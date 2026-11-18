import "server-only";
import { createRealtime, rawSql, type SealedLiveQuery } from "@neon/realtime/server";
import { databaseName } from "./db";
import { GPU_INDEX_SQL } from "./queries";
import type { IndexRow } from "./data";

/**
 * Seals the index live query so the browser can subscribe without seeing the SQL or the secret.
 * Sealing is local crypto (no network call). Sealed queries live ~60 s; the client renews them
 * through POST /api/realtime/gpu-index, which seals this exact query again.
 */
const realtime = process.env.NEON_REALTIME_SECRET
  ? createRealtime({ secret: process.env.NEON_REALTIME_SECRET, db: databaseName() })
  : null;

export function realtimeEnabled(): boolean {
  return realtime !== null && Boolean(process.env.NEXT_PUBLIC_NEON_REALTIME_URL);
}

export async function sealGpuIndex(): Promise<SealedLiveQuery<IndexRow> | null> {
  if (!realtime) return null;
  return realtime.seal({ query: rawSql<IndexRow>(GPU_INDEX_SQL) });
}
