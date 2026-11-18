# Gyrex web: public GPU price index page + JSON API

Next.js 16 app that reads the `gpu_index` view from Supabase with the **anon** key (read-only,
see the migration's grants). Without Supabase env vars it serves `demo/gpu_index.json`, a real
one-off gpuhunt pull, behind a "Sample data" banner.

```bash
npm install
npm run dev                 # http://localhost:3000 (demo data)
cp .env.example .env.local  # add SUPABASE_URL + SUPABASE_ANON_KEY for the live index
npm run build && npm start
```

- `/`: sortable, filterable table. Per GPU-hour: on-demand median (plus lowest), spot lowest,
  decentralized lowest (column appears once any decentralized rows exist).
- `/api/v1/gpu-index`: the same rows as JSON, CORS-open, cached 5 minutes at the edge.
- Pages revalidate every 5 minutes; the collector writes hourly.
- If Supabase is unreachable the page shows "temporarily unavailable" rather than failing the
  build. A failed revalidation replaces the cached page for up to 5 minutes.

Deploy: import the repo on Vercel with root directory `web` and set the two env vars. The free
Hobby plan is non-commercial; move to Pro before charging for the API.

Refresh the demo data: `python scripts/make_demo_index.py` from the repo root.
