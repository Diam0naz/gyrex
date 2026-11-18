/** Shared, client-safe types and helpers. Server-only loading lives in lib/load.ts. */

/** One row of the gpu_index query (per-GPU USD/hour). */
export type IndexRow = {
  key: string;
  gpu_model: string;
  vram_gb: number | null;
  offer_type: "on-demand" | "spot" | "decentralized";
  offers: number;
  providers: number;
  min_usd_hr: number;
  median_usd_hr: number;
  max_usd_hr: number;
  /** timestamptz: a Date from the database drivers and Realtime, an ISO string in demo data */
  last_seen: Date | string;
};

export type IndexSource = "neon" | "demo";

/** A GPU (model + VRAM) with its on-demand / spot / decentralized stats side by side. */
export type GpuLine = {
  key: string;
  gpu_model: string;
  vram_gb: number | null;
  onDemand?: IndexRow;
  spot?: IndexRow;
  decentralized?: IndexRow;
};

export const REVALIDATE_SECONDS = 300;

export function toLines(rows: readonly IndexRow[]): GpuLine[] {
  const lines = new Map<string, GpuLine>();
  for (const r of rows) {
    const key = `${r.gpu_model}|${r.vram_gb ?? ""}`;
    const line = lines.get(key) ?? { key, gpu_model: r.gpu_model, vram_gb: r.vram_gb };
    if (r.offer_type === "on-demand") line.onDemand = r;
    else if (r.offer_type === "spot") line.spot = r;
    else line.decentralized = r;
    lines.set(key, line);
  }
  return [...lines.values()];
}

/** Newest last_seen across rows, as an ISO string. */
export function latestSeen(rows: readonly IndexRow[]): string | null {
  let latest: number | null = null;
  for (const r of rows) {
    const t = new Date(r.last_seen).getTime();
    if (!Number.isNaN(t) && (latest === null || t > latest)) latest = t;
  }
  return latest === null ? null : new Date(latest).toISOString();
}
