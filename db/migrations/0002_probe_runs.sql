-- Probe jobs: measured startup, completion and throughput, one row per attempt.
--
-- Every attempt is stored, including ones that never ran: a job that sat in the queue until we
-- gave up is a reliability data point, not noise. Timestamps come from three clocks:
--   submitted_at              ours, when the deployment was created
--   listed_at, job_started_at,
--   job_ended_at              Nosana's (the job's on-chain times)
--   container_started_at,
--   first_kernel_at           the container's (Docker state and the probe script)
-- so stage durations mix clocks only where noted in gyrex/probe.py.

create table if not exists probe_runs (
    id                    bigint generated always as identity primary key,
    source                text not null,             -- 'nosana'
    market                text not null,             -- market address
    market_slug           text,
    market_type           text,                      -- PREMIUM | COMMUNITY
    gpu_model             text,                      -- canonical name, as in offers
    probe_version         integer not null,
    image                 text not null,
    deployment_id         text,
    job_id                text,
    node                  text,                      -- host that ran it, for per-host history
    status                text not null check (status in ('ok', 'failed', 'queue_timeout', 'run_timeout', 'submit_error')),
    failure               text,                      -- short reason when status <> 'ok'
    price_usd_hr          numeric(12, 6),            -- buyer price at submission
    cost_cap_usd          numeric(12, 6) not null,   -- most this attempt could cost; counts against the cap
    cost_usd              numeric(12, 6),            -- price x billed time, once known
    submitted_at          timestamptz not null,
    listed_at             timestamptz,
    job_started_at        timestamptz,
    container_started_at  timestamptz,
    first_kernel_at       timestamptz,
    job_ended_at          timestamptz,
    finished_at           timestamptz not null default now(),
    queue_s               numeric,                   -- submitted -> picked up by a node
    startup_s             numeric,                   -- picked up -> first CUDA kernel (image pull, container start, imports)
    total_s               numeric,                   -- submitted -> job end
    tflops_median         numeric,
    tflops_p10            numeric,
    tflops_first_fifth    numeric,
    tflops_last_fifth     numeric,
    copy_gbps             numeric,
    gpu_name              text,                      -- as the GPU reports itself
    gpu_uuid              text,
    driver_version        text,
    vram_mb               integer,
    result                jsonb,                     -- the probe's full JSON output
    raw                   jsonb                      -- Nosana job record, trimmed, for debugging
);

create index if not exists probe_runs_market_idx on probe_runs (source, market, submitted_at desc);
create index if not exists probe_runs_submitted_idx on probe_runs (submitted_at desc);
