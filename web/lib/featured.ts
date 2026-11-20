/** Headline numbers for the hero and focal sections, picked from the live index rows. */
import { toLines, type GpuLine, type IndexRow } from "./data";

export type Featured = {
  model: string;
  vram: number | null;
  onDemandMedian?: number;
  lowest: number;
  lowestType: string;
  offers: number;
};

export type Spread = { model: string; vram: number | null; min: number; max: number; offers: number };

const PREFERRED = ["H100", "RTX4090", "A100", "H200", "L40S", "A6000"];

/** RTX4090 -> RTX 4090, RTX6000Ada -> RTX 6000 Ada. */
export function displayName(model: string): string {
  return model.replace(/^RTX(\d)/, "RTX $1").replace(/(\d)Ada$/, "$1 Ada");
}

function cheapest(l: GpuLine): { row: IndexRow; type: string } | null {
  const opts = [
    l.onDemand && { row: l.onDemand, type: "on-demand" },
    l.spot && { row: l.spot, type: "spot" },
    l.decentralized && { row: l.decentralized, type: "decentralized" },
  ].filter(Boolean) as { row: IndexRow; type: string }[];
  if (!opts.length) return null;
  return opts.reduce((a, b) => (b.row.min_usd_hr < a.row.min_usd_hr ? b : a));
}

/** The best-covered VRAM variant of each preferred model, then whatever else is listed. */
export function featured(rows: readonly IndexRow[], count = 3): Featured[] {
  const lines = toLines(rows);
  const coverage = (l: GpuLine) => (l.onDemand?.offers ?? 0) + (l.spot?.offers ?? 0) + (l.decentralized?.offers ?? 0);
  const best = new Map<string, GpuLine>();
  for (const l of lines) {
    const cur = best.get(l.gpu_model);
    if (!cur || coverage(l) > coverage(cur)) best.set(l.gpu_model, l);
  }
  const order = [
    ...PREFERRED.filter((m) => best.has(m)),
    ...[...best.keys()].filter((m) => !PREFERRED.includes(m)).sort((a, b) => coverage(best.get(b)!) - coverage(best.get(a)!)),
  ];
  const out: Featured[] = [];
  for (const m of order) {
    const l = best.get(m)!;
    const c = cheapest(l);
    if (!c) continue;
    out.push({
      model: displayName(m),
      vram: l.vram_gb,
      onDemandMedian: l.onDemand?.median_usd_hr,
      lowest: c.row.min_usd_hr,
      lowestType: c.type,
      offers: coverage(l),
    });
    if (out.length === count) break;
  }
  return out;
}

/** The on-demand price range of the first preferred GPU that has one: the "same GPU, different price" story. */
export function spread(rows: readonly IndexRow[]): Spread | null {
  const ond = rows.filter((r) => r.offer_type === "on-demand" && r.offers >= 3 && r.max_usd_hr > r.min_usd_hr);
  for (const m of PREFERRED) {
    const r = ond.filter((x) => x.gpu_model === m).sort((a, b) => b.offers - a.offers)[0];
    if (r) return { model: displayName(m), vram: r.vram_gb, min: r.min_usd_hr, max: r.max_usd_hr, offers: r.offers };
  }
  const r = [...ond].sort((a, b) => b.offers - a.offers)[0];
  return r ? { model: displayName(r.gpu_model), vram: r.vram_gb, min: r.min_usd_hr, max: r.max_usd_hr, offers: r.offers } : null;
}

export const usd = (v: number) => (v < 1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`);
