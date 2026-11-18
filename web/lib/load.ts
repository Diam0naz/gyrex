import "server-only";
import demo from "@/demo/gpu_index.json";
import { db } from "./db";
import { GPU_INDEX_SQL } from "./queries";
import type { IndexRow, IndexSource } from "./data";

export type IndexData = { rows: IndexRow[]; source: IndexSource };

/** Live index from Neon, or the bundled sample when DATABASE_URL isn't set. */
export async function getIndex(): Promise<IndexData> {
  const sql = db();
  if (!sql) {
    return { rows: demo.rows.map((r) => ({ ...r, key: `${r.gpu_model}|${r.vram_gb ?? ""}|${r.offer_type}` })) as IndexRow[], source: "demo" };
  }
  const rows = (await sql.query(GPU_INDEX_SQL)) as IndexRow[];
  return { rows, source: "neon" };
}
