/**
 * `snapshot` Neon Function: daily GPU index history, next to the database.
 *
 *   POST /  (Function Trigger, cron in neon.ts) -> stores today's gpu_index into gpu_index_daily
 *   GET  /?days=30                              -> public JSON history, newest day first
 *
 * The POST route only accepts genuine trigger deliveries (parseTriggerDelivery checks the
 * x-neon-trigger-invocation-id header, which the Functions proxy strips from public requests).
 * The snapshot is idempotent per UTC day, so a retried delivery just refreshes that day.
 */
import { attachDatabasePool } from "@neon/functions";
import { parseTriggerDelivery } from "@neon/functions/triggers";
import { Pool } from "pg";

// Module scope: one small pool per isolate, reused across requests (DATABASE_URL is injected).
// Neon URLs say sslmode=require; asking pg for verify-full keeps today's checks and skips its warning.
const pool = new Pool({
  connectionString: process.env.DATABASE_URL?.replace("sslmode=require", "sslmode=verify-full"),
  max: 5,
});
attachDatabasePool(pool);

const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" };

function utcDay(iso: string | undefined): string {
  const d = iso ? new Date(iso) : new Date();
  return (Number.isNaN(d.getTime()) ? new Date() : d).toISOString().slice(0, 10);
}

async function history(url: URL): Promise<Response> {
  const days = Math.min(Math.max(Number.parseInt(url.searchParams.get("days") ?? "30", 10) || 30, 1), 365);
  const { rows } = await pool.query(
    `SELECT to_char(day, 'YYYY-MM-DD') AS day, gpu_model, NULLIF(vram_gb, 0) AS vram_gb, offer_type,
            offers, providers, min_usd_hr, median_usd_hr, max_usd_hr
       FROM gpu_index_daily
      WHERE day > (SELECT coalesce(max(day), current_date) FROM gpu_index_daily) - $1::int
      ORDER BY day DESC, gpu_model, vram_gb, offer_type`,
    [days],
  );
  return Response.json(
    { unit: "USD per GPU-hour", days, rows },
    { headers: { ...CORS, "Cache-Control": "public, max-age=300" } },
  );
}

async function snapshot(request: Request): Promise<Response> {
  const parsed = await parseTriggerDelivery(request);
  if (!parsed.ok) {
    return new Response(parsed.error, { status: parsed.error === "invalid_body" ? 400 : 401 });
  }
  const inv = parsed.invocation;
  if (inv.type !== "schedule") return new Response("unsupported trigger", { status: 400 });
  const day = utcDay(inv.data.scheduledAt);
  const { rows } = await pool.query("SELECT snapshot_gpu_index($1::date) AS n", [day]);
  console.log(`snapshot ${day}: ${rows[0].n} index rows (invocation ${inv.invocationId})`);
  return Response.json({ day, rows: rows[0].n });
}

export default {
  async fetch(request: Request): Promise<Response> {
    try {
      if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
      if (request.method === "GET") return await history(new URL(request.url));
      if (request.method === "POST") return await snapshot(request);
      return new Response("method not allowed", { status: 405, headers: { Allow: "GET, POST, OPTIONS" } });
    } catch (e) {
      console.error(e);
      return Response.json({ error: "internal error" }, { status: 500, headers: CORS });
    }
  },
};
