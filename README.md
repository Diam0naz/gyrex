# Gyrex: GPU price index (weekend build)

Collects GPU prices from centralized clouds (via [gpuhunt](https://pypi.org/project/gpuhunt/))
and from Nosana, normalizes them into one schema, and writes them to **Neon Postgres**. A Next.js
page shows the index and updates live through **Neon Realtime**; a **Neon Function** keeps a
daily history. This is layer 1 of the Gyrex plan, minus probe jobs.

```
gpuhunt (9 clouds) ─┐                                    ┌> offers            (current state, active flag)
                    ├─> normalize ─> ingest_offers() ────┼> price_changes     (only when a price moves)
Nosana markets API ─┘    (hourly, GitHub Actions)        └> collector_runs    (monitoring)
                                                                │
        web/ (Vercel) <── initial rows: Neon HTTP driver ───────┤ gpu_index (per-GPU stats)
          browser   <──── live rows: Neon Realtime (sealed live query)
        snapshot (Neon Function, cron 00:41 UTC) ──> gpu_index_daily ──> GET / = public history JSON
```

## Setup

1. **Neon.** The project is linked in `web/.neon` (not committed) and declared in `web/neon.ts`.
   ```bash
   cd web && neon deploy           # functions, trigger, Realtime; writes web/.env.local
   cd .. && set -a; . web/.env.local; set +a
   python -m gyrex.migrate         # applies db/migrations/*.sql once each (uses DATABASE_URL_UNPOOLED)
   ```
2. **Local run.**
   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e '.[dev]'
   cargo build --release --manifest-path ingest/Cargo.toml   # the database write path
   cargo test --manifest-path ingest/Cargo.toml
   pytest -q
   gyrex-collect --dry-run --dump out     # no database; inspect out/*.json
   gyrex-collect                          # writes to Neon (needs DATABASE_URL)
   ```
3. **Schedule.** Add the repo secret `DATABASE_URL` (Neon, pooled). `.github/workflows/collect.yml`
   runs hourly and keeps Nosana's raw API payload as a 7-day artifact.
4. **Website.** See [`web/README.md`](web/README.md) for the Vercel settings and env vars.

## Data model

| Object | What it holds |
|---|---|
| `offers` | One row per offer, keyed `source\|provider\|instance\|region\|spot\|gpu_count`. Prices are per instance; `price_per_gpu_hr` is generated. `active` is false once the offer's provider ran without it. |
| `price_changes` | A row when an offer first appears or its price changes. This is the history that can't be backfilled. |
| `collector_runs` | Status, counts, delisted offers, errors and **unmapped GPU names** per source per run. |
| `gpu_index` (view) | Per `gpu_model × vram_gb × offer_type`: offer count, providers, min/median/max $/GPU-hr over active offers. Same SQL as the live query in `web/lib/queries.ts`. |
| `gpu_index_daily` | One `gpu_index` snapshot per UTC day, written by the `snapshot` function. |

The collector writes through Neon's SQL-over-HTTP endpoint, so it needs only outbound HTTPS.
Python fetches and normalizes; offers then go to `gyrex-ingest` (Rust, [`ingest/`](ingest/)), which
owns the write path:

- Each row is checked against the table's rules first, so a bad row is dropped on its own instead
  of failing its 1,000-row batch. More than 5% rejected aborts the source before anything is written.
- Batches go through `ingest_offers()`, retrying on 429, 5xx and lost connections.
- `close_run()` then marks offers a provider stopped listing as inactive. A provider that errors,
  returns nothing or had a row rejected keeps its last known offers, and a failed write delists nothing.

Rejected rows appear in `collector_runs.errors` and mark the run `partial`.

No row-level security: Neon Realtime rejects live queries on RLS tables, and the database is
only reachable with a server-side connection string. The browser never sees one: it gets a
sealed (encrypted, 60-second) live query.

Storage: a full gpuhunt run is about 12k offers, roughly 14 MB including indexes. History grows only
with actual price moves, so Neon's free storage lasts a long time. Full hourly snapshots
would have filled it in weeks.

## GPU name mapping

`gyrex/normalize.py` maps provider names onto gpuhunt's canonical names ("H100", "RTX4090",
"A6000", "RTX6000Ada", …). It strips brand words, form factors and memory sizes, adds the missing
"RTX" in names like Nosana's "NVIDIA 4090", and uses a small `ALIASES` table. Unmapped names
are still stored with `gpu_model = null` and listed in `collector_runs.unmapped_gpus`; add them
to `ALIASES` when they show up. VRAM is only filled from the known-GPU table when the model has
a single size: A100 (40/80) and RTX 3060 (8/12) are left empty rather than guessed.

A Nosana market can hold several variants, so its own name decides the model: "NVIDIA 4070" is
indexed as the RTX 4070 even though it also contains Ti and SUPER cards. "(6x)" sets `gpu_count`,
so the index shows the price per GPU. `(Reserved)` names no GPU and stays unmapped on purpose.

## Verify before trusting Nosana numbers

The Nosana adapter was written against the official SDK (`@nosana/api` 2.24.5) but **has not
been run against the live API**. The build sandbox couldn't reach Nosana's hosts.

- `GET https://host-manager.k8s.prd.nosana.com/markets/` is untyped in the SDK. Field names
  (`address`, `slug`, `name`, `type`, `usd_reward_per_hour`, `nos_reward_per_second`) come from
  the SDK's market update schema. After the first run, open `out/nosana_raw.json` and confirm them.
- `usd_reward_per_hour` is what the **host** earns. The index adds each market's
  `network_fee_percentage` to show the buyer price, and keeps the host rate in `extra.host_usd_hr`.
- `/reservations/gpus` (VRAM, node and availability counts) is optional enrichment. If it needs
  auth or changes shape, prices still come through and the run is marked `partial`.

## Notes

- `gpuhunt.query()` across all providers raises if any online provider fails. The adapter
  queries one provider at a time, so one bad provider is logged and skipped.
- Skipped on purpose: GPU slices (Azure A10 at 1/6–1/2, GCP RTX PRO 6000 at 1/8–1/2; about 5% of
  offers), detected as VRAM below the model's smallest real size, and non-GPU accelerators
  (TPUs, Gaudi, Tenstorrent). Both would distort a per-GPU index.
- Default providers are gpuhunt's offline catalog (aws, azure, gcp, lambdalabs, nebius, oci,
  runpod, verda, cloudrift), which needs no keys. Vast and other online providers need keys and
  churn per machine; add them with `GPUHUNT_PROVIDERS` when you're ready for the extra history.
- GitHub turns off scheduled workflows after 60 days with no repo activity.
- Vercel's free Hobby plan doesn't allow commercial use: fine for the public page, not for a paid API.

## Next steps (from the plan)

1. ~~Public index page~~: done, see [`web/`](web/README.md).
2. Probe jobs on Nosana (frequent, cheap) and H100/4090 (rare): startup time, completion, throughput.
3. io.net, Akash and Vast adapters (check each network's terms on automated access first).
