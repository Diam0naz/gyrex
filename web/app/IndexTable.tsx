"use client";

import { useMemo, useState } from "react";
import type { GpuLine, IndexRow } from "@/lib/data";

type SortKey = "gpu" | "vram" | "onDemand" | "spot" | "decentralized";

const usd = (v: number | undefined) =>
  v === undefined ? null : v < 1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;

function sortValue(l: GpuLine, key: SortKey): number | string | undefined {
  switch (key) {
    case "gpu":
      return l.gpu_model.toLowerCase();
    case "vram":
      return l.vram_gb ?? undefined;
    case "onDemand":
      return l.onDemand?.median_usd_hr;
    case "spot":
      return l.spot?.min_usd_hr;
    case "decentralized":
      return l.decentralized?.min_usd_hr;
  }
}

function Price({ row, field, label }: { row?: IndexRow; field: "median_usd_hr" | "min_usd_hr"; label: string }) {
  if (!row) return <span className="none" aria-label="no offers">–</span>;
  return (
    <span className="price">
      <span className="num">{usd(row[field])}</span>
      <span className="sub">
        {label === "median" ? `from ${usd(row.min_usd_hr)} · ` : ""}
        {row.providers} {row.providers === 1 ? "provider" : "providers"}
      </span>
    </span>
  );
}

export default function IndexTable({ lines, showDecentralized }: { lines: GpuLine[]; showDecentralized: boolean }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "onDemand", dir: -1 });

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/\s+/g, "");
    const filtered = q ? lines.filter((l) => l.gpu_model.toLowerCase().includes(q)) : lines;
    return [...filtered].sort((a, b) => {
      const va = sortValue(a, sort.key);
      const vb = sortValue(b, sort.key);
      if (va === undefined && vb === undefined) return a.gpu_model.localeCompare(b.gpu_model);
      if (va === undefined) return 1; // missing values always last
      if (vb === undefined) return -1;
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
  }, [lines, query, sort]);

  const header = (key: SortKey, label: string, className = "") => {
    const active = sort.key === key;
    return (
      <th className={className} aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
        <button
          type="button"
          onClick={() =>
            setSort(active ? { key, dir: sort.dir === 1 ? -1 : 1 } : { key, dir: key === "gpu" ? 1 : -1 })
          }
        >
          {label}
          <span className="arrow" aria-hidden="true">{active ? (sort.dir === 1 ? "↑" : "↓") : ""}</span>
        </button>
      </th>
    );
  };

  return (
    <section className="table-block">
      <label className="search">
        <span className="visually-hidden">Filter GPUs</span>
        <input
          type="search"
          placeholder="Filter: H100, 4090, A100…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <div className="scroller">
        <table>
          <thead>
            <tr>
              {header("gpu", "GPU")}
              {header("vram", "VRAM", "r col-vram")}
              {header("onDemand", "On\u2011demand median", "r")}
              {header("spot", "Spot lowest", "r")}
              {showDecentralized && header("decentralized", "Decentralized lowest", "r")}
            </tr>
          </thead>
          <tbody>
            {shown.map((l) => (
              <tr key={l.key}>
                <th scope="row">
                  {l.gpu_model}
                  {l.vram_gb ? <span className="vram-inline">{l.vram_gb} GB</span> : null}
                </th>
                <td className="r num col-vram">{l.vram_gb ? `${l.vram_gb} GB` : "–"}</td>
                <td className="r"><Price row={l.onDemand} field="median_usd_hr" label="median" /></td>
                <td className="r"><Price row={l.spot} field="min_usd_hr" label="min" /></td>
                {showDecentralized && (
                  <td className="r"><Price row={l.decentralized} field="min_usd_hr" label="min" /></td>
                )}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={showDecentralized ? 5 : 4} className="empty">No GPU matches “{query}”.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
