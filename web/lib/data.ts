import demo from "@/demo/gpu_index.json";

/** One row of the public.gpu_index view (per-GPU USD/hour). */
export type IndexRow = {
  gpu_model: string;
  vram_gb: number | null;
  offer_type: "on-demand" | "spot" | "decentralized";
  offers: number;
  providers: number;
  min_usd_hr: number;
  median_usd_hr: number;
  max_usd_hr: number;
  last_seen: string;
};

export type IndexData = {
  rows: IndexRow[];
  updatedAt: string | null;
  source: "supabase" | "demo";
};

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

export async function getIndex(): Promise<IndexData> {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    return { rows: demo.rows as IndexRow[], updatedAt: demo.generated_at, source: "demo" };
  }
  const res = await fetch(`${url.replace(/\/$/, "")}/rest/v1/gpu_index?select=*`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(`gpu_index fetch failed: ${res.status} ${await res.text()}`);
  }
  const rows = (await res.json()) as IndexRow[];
  const updatedAt = rows.reduce<string | null>(
    (latest, r) => (!latest || r.last_seen > latest ? r.last_seen : latest),
    null,
  );
  return { rows, updatedAt, source: "supabase" };
}

export function toLines(rows: IndexRow[]): GpuLine[] {
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
