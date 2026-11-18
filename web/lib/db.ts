import "server-only";
import { neon, type NeonQueryFunction } from "@neondatabase/serverless";

/**
 * Neon's HTTP driver: every read here is a one-shot SELECT from an ISR render or an API route,
 * which is what the HTTP transport is for (no pool to manage, one HTTPS round trip).
 * Uses the pooled DATABASE_URL that `neon deploy` / `neon env pull` writes.
 */
let client: NeonQueryFunction<false, false> | null = null;

export function db(): NeonQueryFunction<false, false> | null {
  const url = process.env.DATABASE_URL;
  if (!url) return null;
  client ??= neon(url);
  return client;
}

/** Database name from the connection string, which Realtime binds each sealed query to. */
export function databaseName(): string {
  const url = process.env.DATABASE_URL;
  return url ? decodeURIComponent(new URL(url).pathname.slice(1)) || "neondb" : "neondb";
}
