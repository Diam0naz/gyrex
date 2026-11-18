-- Gyrex GPU price index on Neon (plain Postgres).
--
-- offers           one row per offer (source|provider|instance|region|spot|count), updated in place
-- price_changes    a row only when an offer first appears or its price moves (history can't be backfilled)
-- collector_runs   one row per source per run, for monitoring
-- gpu_index        per-GPU price stats over the offers in each provider's latest run
-- gpu_index_daily  one gpu_index snapshot per UTC day, written by the `snapshot` Neon Function
--
-- No row-level security: Neon Realtime rejects live queries on RLS tables, and nothing here is
-- reachable without a server-side connection string. Live queries must be deterministic, so
-- "current" is tracked with offers.active (set by close_run) rather than now().

create table if not exists offers (
    offer_key        text primary key,
    source           text not null,
    provider         text not null,
    offer_type       text not null check (offer_type in ('on-demand', 'spot', 'decentralized')),
    instance_name    text not null,
    region           text not null,
    gpu_raw          text not null,
    gpu_model        text,                       -- null = unmapped, see collector_runs.unmapped_gpus
    gpu_count        integer not null check (gpu_count > 0),
    vram_gb          numeric,
    price_usd_hr     numeric(12, 6) not null check (price_usd_hr >= 0),
    price_per_gpu_hr numeric(12, 6) generated always as (price_usd_hr / gpu_count) stored,
    available        integer,
    active           boolean not null default true,  -- false once the offer's provider ran without it
    extra            jsonb not null default '{}'::jsonb,
    first_seen       timestamptz not null default now(),
    last_seen        timestamptz not null default now(),
    last_changed     timestamptz not null default now()
);

create index if not exists offers_active_model_idx on offers (gpu_model, vram_gb, offer_type) where active;
create index if not exists offers_source_idx on offers (source, provider, last_seen);

create table if not exists price_changes (
    id           bigint generated always as identity primary key,
    offer_key    text not null references offers (offer_key) on delete cascade,
    price_usd_hr numeric(12, 6) not null,
    observed_at  timestamptz not null default now()
);

create index if not exists price_changes_offer_idx on price_changes (offer_key, observed_at desc);

create table if not exists collector_runs (
    id            bigint generated always as identity primary key,
    source        text not null,
    status        text not null check (status in ('ok', 'partial', 'failed')),
    started_at    timestamptz not null,
    finished_at   timestamptz not null default now(),
    offers        integer not null default 0,
    price_changes integer not null default 0,
    deactivated   integer not null default 0,
    errors        jsonb,
    unmapped_gpus jsonb
);

-- Upsert a batch of offers and log price changes, in one statement.
-- Data-modifying CTEs share one snapshot, so `changed` compares against prices from
-- BEFORE this batch's upsert.
create or replace function ingest_offers(p_source text, p_offers jsonb)
returns jsonb
language sql
as $$
    with incoming as (
        select *
        from jsonb_to_recordset(p_offers) as x(
            offer_key text, provider text, offer_type text, instance_name text, region text,
            gpu_raw text, gpu_model text, gpu_count integer, vram_gb numeric,
            price_usd_hr numeric, available integer, extra jsonb
        )
    ),
    upserted as (
        insert into offers as o (
            offer_key, source, provider, offer_type, instance_name, region, gpu_raw, gpu_model,
            gpu_count, vram_gb, price_usd_hr, available, extra
        )
        select offer_key, p_source, provider, offer_type, instance_name, region, gpu_raw, gpu_model,
               gpu_count, vram_gb, round(price_usd_hr, 6), available, coalesce(extra, '{}'::jsonb)
        from incoming
        on conflict (offer_key) do update set
            gpu_raw      = excluded.gpu_raw,
            gpu_model    = excluded.gpu_model,
            vram_gb      = excluded.vram_gb,
            price_usd_hr = excluded.price_usd_hr,
            available    = excluded.available,
            extra        = excluded.extra,
            active       = true,
            last_seen    = now(),
            last_changed = case when o.price_usd_hr is distinct from excluded.price_usd_hr
                                then now() else o.last_changed end
        returning offer_key, price_usd_hr
    ),
    changed as (
        insert into price_changes (offer_key, price_usd_hr)
        select u.offer_key, u.price_usd_hr
        from upserted u
        left join offers prev on prev.offer_key = u.offer_key  -- pre-upsert snapshot
        where prev.offer_key is null or prev.price_usd_hr is distinct from u.price_usd_hr
        returning 1
    )
    select jsonb_build_object(
        'upserted', (select count(*) from upserted),
        'price_changes', (select count(*) from changed)
    );
$$;

-- After a successful run: offers of the providers that ran, but not seen since the run
-- started, are no longer listed. Providers that failed this run keep their offers as they were.
create or replace function close_run(p_source text, p_providers text[], p_started_at timestamptz)
returns integer
language sql
as $$
    with gone as (
        update offers set active = false
        where source = p_source
          and provider = any (p_providers)
          and active
          and last_seen < p_started_at
        returning 1
    )
    select count(*)::integer from gone;
$$;

-- Same SQL as the live query in web/lib/queries.ts: keep them in step.
-- Numbers are cast to float8/int4 so pg, the HTTP driver and Realtime all decode them as numbers.
create or replace view gpu_index as
select
    gpu_model || '|' || coalesce(vram_gb::text, '') || '|' || offer_type        as key,
    gpu_model,
    vram_gb::float8                                                              as vram_gb,
    offer_type,
    count(*)::int4                                                               as offers,
    count(distinct provider)::int4                                               as providers,
    min(price_per_gpu_hr)::float8                                                as min_usd_hr,
    (percentile_cont(0.5) within group (order by price_per_gpu_hr))::float8      as median_usd_hr,
    max(price_per_gpu_hr)::float8                                                as max_usd_hr,
    max(last_seen)                                                               as last_seen
from offers
where active and gpu_model is not null
group by gpu_model, vram_gb, offer_type;

create table if not exists gpu_index_daily (
    day           date not null,
    gpu_model     text not null,
    vram_gb       float8 not null default 0,   -- 0 = unknown VRAM
    offer_type    text not null,
    offers        int4 not null,
    providers     int4 not null,
    min_usd_hr    float8 not null,
    median_usd_hr float8 not null,
    max_usd_hr    float8 not null,
    taken_at      timestamptz not null default now(),
    primary key (day, gpu_model, vram_gb, offer_type)
);

-- Idempotent: running it twice on one day just refreshes that day's rows.
create or replace function snapshot_gpu_index(p_day date)
returns integer
language sql
as $$
    with snap as (
        insert into gpu_index_daily as d (day, gpu_model, vram_gb, offer_type, offers, providers,
                                          min_usd_hr, median_usd_hr, max_usd_hr)
        select p_day, gpu_model, coalesce(vram_gb, 0), offer_type, offers, providers,
               min_usd_hr, median_usd_hr, max_usd_hr
        from gpu_index
        on conflict (day, gpu_model, vram_gb, offer_type) do update set
            offers = excluded.offers, providers = excluded.providers,
            min_usd_hr = excluded.min_usd_hr, median_usd_hr = excluded.median_usd_hr,
            max_usd_hr = excluded.max_usd_hr, taken_at = now()
        returning 1
    )
    select count(*)::integer from snap;
$$;
