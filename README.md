# Gyrex: GPU price index (weekend build)

Collects GPU prices from centralized clouds (via [gpuhunt](https://pypi.org/project/gpuhunt/))
and from Nosana, normalizes them into one schema, and writes them to Supabase. This is layer 1
of the Gyrex plan, minus probe jobs.

```
gpuhunt (9 clouds) ─┐
                    ├─> normalize GPU names ─> ingest_offers() RPC ─> offers           (current state)
Nosana markets API ─┘                                              ├> price_changes    (only when a price moves)
                                                                   └> gpu_index view   (public, per-GPU stats)
```

## Setup

1. **Supabase.** Create a project, then run `supabase/migrations/20261118000000_price_index.sql`
   in the SQL editor (or `supabase db push` with the CLI).
2. **Local run.**
   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e '.[dev]'
   pytest -q
   gyrex-collect --dry-run --dump out     # no database; inspect out/*.json
   cp .env.example .env                   # fill in, then:
   set -a; . ./.env; set +a; gyrex-collect
   ```
3. **Schedule.** Push to GitHub and add repo secrets `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY`. `.github/workflows/collect.yml` runs hourly and keeps Nosana's
   raw API payload as a 7-day artifact.

## Data model

| Object | What it holds |
|---|---|
| `offers` | One row per offer, keyed `source\|provider\|instance\|region\|spot\|gpu_count`. Prices are per instance; `price_per_gpu_hr` is generated. |
| `price_changes` | A row when an offer first appears or its price changes. This is the history that can't be backfilled. |
| `collector_runs` | Status, counts, errors and **unmapped GPU names** per source per run. Private. |
| `gpu_index` (view) | Per `gpu_model × vram_gb × offer_type`: offer count, providers, min/median/max $/GPU-hr, over offers seen in the last 6 h. |

Access: `anon` can read `offers`, `price_changes` and `gpu_index`. Only the service role can
write (through `ingest_offers`) or read `collector_runs`.

Storage: a full gpuhunt run is about 13k offers, roughly 14 MB including indexes. History grows only
with actual price moves, so the free tier's 500 MB lasts a long time. Full hourly snapshots
would have filled it in weeks.

## GPU name mapping

`gyrex/normalize.py` maps provider names onto gpuhunt's canonical names ("H100", "RTX4090",
"A6000", "RTX6000Ada", …). It strips brand words, form factors and memory sizes, adds the missing
"RTX" in names like Nosana's "NVIDIA 4090", and uses a small `ALIASES` table. Unmapped names
are still stored with `gpu_model = null` and listed in `collector_runs.unmapped_gpus`; add them
to `ALIASES` when they show up. VRAM is only filled from the known-GPU table when the model has
a single size: A100 (40/80) and RTX 3060 (8/12) are left empty rather than guessed.

## Verify before trusting Nosana numbers

The Nosana adapter was written against the official SDK (`@nosana/api` 2.24.5) but **has not
been run against the live API**. The build sandbox couldn't reach Nosana's hosts.

- `GET https://host-manager.k8s.prd.nosana.com/markets/` is untyped in the SDK. Field names
  (`address`, `slug`, `name`, `type`, `usd_reward_per_hour`, `nos_reward_per_second`) come from
  the SDK's market update schema. After the first run, open `out/nosana_raw.json` and confirm them.
- `usd_reward_per_hour` is what the **host** earns. If buyers pay a network fee on top, set
  `NETWORK_FEE` in `gyrex/adapters/nosana.py`.
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
