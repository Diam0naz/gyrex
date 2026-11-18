-- Gyrex GPU price index: current offers + change-only price history.
--
-- offers         one row per offer (source|provider|instance|region|spot|count), updated in place
-- price_changes  a row only when an offer first appears or its price moves (history can't be backfilled)
-- collector_runs one row per source per run, for monitoring
-- gpu_index      public view: per-GPU price stats over offers seen in the last 6 hours
--
-- Writes go through ingest_offers() with the service-role key. anon can read offers,
-- price_changes and the view; nothing else.

create table if not exists public.offers (
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
    extra            jsonb not null default '{}'::jsonb,
    first_seen       timestamptz not null default now(),
    last_seen        timestamptz not null default now(),
    last_changed     timestamptz not null default now()
);

create index if not exists offers_model_idx on public.offers (gpu_model, vram_gb, last_seen desc);
create index if not exists offers_source_idx on public.offers (source, last_seen desc);

create table if not exists public.price_changes (
    id           bigint generated always as identity primary key,
    offer_key    text not null references public.offers (offer_key) on delete cascade,
    price_usd_hr numeric(12, 6) not null,
    observed_at  timestamptz not null default now()
);

create index if not exists price_changes_offer_idx on public.price_changes (offer_key, observed_at desc);

create table if not exists public.collector_runs (
    id            bigint generated always as identity primary key,
    source        text not null,
    status        text not null check (status in ('ok', 'partial', 'failed')),
    started_at    timestamptz not null,
    finished_at   timestamptz not null default now(),
    offers        integer not null default 0,
    price_changes integer not null default 0,
    errors        jsonb,
    unmapped_gpus jsonb
);

-- Upsert a batch of offers and log price changes, in one statement.
-- Data-modifying CTEs share one snapshot, so `changed` compares against prices from
-- BEFORE this batch's upsert.
create or replace function public.ingest_offers(p_source text, p_offers jsonb)
returns jsonb
language sql
set search_path = public
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

-- Public index: one row per (gpu_model, vram_gb, offer_type), per-GPU prices.
create or replace view public.gpu_index
with (security_invoker = true) as
select
    gpu_model,
    vram_gb,
    offer_type,
    count(*)                                                        as offers,
    count(distinct provider)                                        as providers,
    min(price_per_gpu_hr)                                           as min_usd_hr,
    percentile_cont(0.5) within group (order by price_per_gpu_hr)   as median_usd_hr,
    max(price_per_gpu_hr)                                           as max_usd_hr,
    max(last_seen)                                                  as last_seen
from public.offers
where gpu_model is not null
  and last_seen > now() - interval '6 hours'
group by gpu_model, vram_gb, offer_type;

-- Access control.
alter table public.offers         enable row level security;
alter table public.price_changes  enable row level security;
alter table public.collector_runs enable row level security;

drop policy if exists "public read" on public.offers;
create policy "public read" on public.offers for select to anon, authenticated using (true);
drop policy if exists "public read" on public.price_changes;
create policy "public read" on public.price_changes for select to anon, authenticated using (true);
-- collector_runs: no policy => only service_role (which bypasses RLS) can read or write.

revoke all on public.offers, public.price_changes, public.collector_runs from anon, authenticated;
grant select on public.offers, public.price_changes, public.gpu_index to anon, authenticated;
-- Supabase normally grants these by default; stated explicitly so the migration doesn't depend on it.
grant select, insert, update, delete on public.offers, public.price_changes, public.collector_runs to service_role;
grant select on public.gpu_index to service_role;

revoke execute on function public.ingest_offers(text, jsonb) from public, anon, authenticated;
grant execute on function public.ingest_offers(text, jsonb) to service_role;
