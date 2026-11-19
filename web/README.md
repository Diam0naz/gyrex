# Gyrex web: GPU price index page, JSON API and Neon services

Next.js 16 app on Neon. Without `DATABASE_URL` it serves `demo/gpu_index.json` (a real one-off
gpuhunt pull) behind a "Sample data" banner.

| Neon service | Where | What it does |
|---|---|---|
| Postgres | `lib/db.ts`, `lib/load.ts` | Reads the index with the HTTP driver (`@neondatabase/serverless`): one-shot SELECTs from an ISR render (5 min) and the API route. |
| Realtime | `lib/realtime.ts`, `app/LiveIndex.tsx`, `app/api/realtime/gpu-index` | The server seals the index query (`lib/queries.ts`); the browser subscribes with `useLiveQuery` and the table updates as each collector run commits. `POST /api/realtime/gpu-index` renews the sealed query (it expires after ~60 s). |
| Functions | `functions/snapshot.ts`, `neon.ts` | `snapshot`: a daily cron trigger (00:41 UTC) stores the index into `gpu_index_daily`; `GET /?days=30` serves that history as public JSON. Uses `pg` with a module-scope pool, as Neon recommends for long-running Functions. `dispatch`: an hourly cron trigger (:23) that starts the collector workflow on GitHub, skipping when a run is active or started in the last 20 minutes. |

```bash
npm install
neon deploy                 # applies neon.ts, writes .env.local
npm run dev                 # http://localhost:3000
npm run build && npm start
```

Routes:

- `/`: sortable, filterable table with a "Live" indicator.
- `/api/v1/gpu-index`: the same rows as JSON, CORS-open, cached 5 minutes at the edge.
- History: the `snapshot` function's URL, linked in the page footer.

## Deploy on Vercel

1. **Root Directory: `web`** (Project Settings → Build and Deployment). The repo root holds the
   Python collector, so building from the root fails.
2. Environment variables (Production and Preview), values from `web/.env.local`:
   - `DATABASE_URL`
   - `NEON_REALTIME_SECRET` (server only, never `NEXT_PUBLIC_`)
   - `NEXT_PUBLIC_NEON_REALTIME_URL` (same value as `NEON_REALTIME_URL`)
   - `NEON_BRANCH_ID` and `NEON_FUNCTION_HOST_SUFFIX` (for the history link)
3. Add the production origin (e.g. `https://gyrex.vercel.app`) to `preview.realtime.allowedOrigins`
   in `neon.ts` and run `neon deploy`, or browsers there can't open the live connection: the page still shows the
   server-rendered prices but stays at "Connecting to live updates…" (verified locally with an
   origin left out of the list).

The free Hobby plan is non-commercial; move to Pro before charging for the API.

Refresh the demo data: `python scripts/make_demo_index.py` from the repo root.
