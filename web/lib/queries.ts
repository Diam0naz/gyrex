/**
 * The index query, shared by the server render (Neon HTTP driver) and the Realtime live query,
 * so the initial rows and the live rows have the same shape.
 *
 * Same SQL as the gpu_index view in db/migrations/0001_price_index.sql: keep them in step.
 * Realtime needs a deterministic, read-only, single SELECT: no now(), no parameters needed,
 * because offers.active already marks each provider's latest run. Numbers are cast to
 * float8/int4 so every client decodes them as JS numbers (numeric and int8 arrive as strings).
 */
export const GPU_INDEX_SQL = `SELECT
  gpu_model || '|' || coalesce(vram_gb::text, '') || '|' || offer_type AS key,
  gpu_model,
  vram_gb::float8 AS vram_gb,
  offer_type,
  count(*)::int4 AS offers,
  count(DISTINCT provider)::int4 AS providers,
  min(price_per_gpu_hr)::float8 AS min_usd_hr,
  (percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_gpu_hr))::float8 AS median_usd_hr,
  max(price_per_gpu_hr)::float8 AS max_usd_hr,
  max(last_seen) AS last_seen
FROM offers
WHERE active AND gpu_model IS NOT NULL
GROUP BY gpu_model, vram_gb, offer_type`;
