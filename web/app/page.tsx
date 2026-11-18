import { getIndex, toLines, type IndexData } from "@/lib/data";
import IndexTable from "./IndexTable";

export const revalidate = 300;

function fmtTime(iso: string | null) {
  if (!iso) return "unknown";
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }) + " UTC";
}

export default async function Page() {
  let data: IndexData;
  try {
    data = await getIndex();
  } catch (e) {
    console.error(e);
    return (
      <main className="wrap">
        <header className="head">
          <p className="eyebrow">Gyrex</p>
          <h1>GPU price index</h1>
        </header>
        <p className="notice" role="alert">
          The index is temporarily unavailable. Try again in a few minutes.
        </p>
      </main>
    );
  }
  const { rows, updatedAt, source } = data;
  const lines = toLines(rows);
  const offers = rows.reduce((n, r) => n + r.offers, 0);
  const hasDecentralized = rows.some((r) => r.offer_type === "decentralized");

  return (
    <main className="wrap">
      <header className="head">
        <p className="eyebrow">Gyrex</p>
        <h1>GPU price index</h1>
        <p className="lede">
          What it costs to rent one GPU for one hour, across centralized clouds
          {hasDecentralized ? " and decentralized networks" : ""}. Multi-GPU instances are divided
          by GPU count so every price is comparable.
        </p>
      </header>

      {source === "demo" && (
        <p className="notice" role="status">
          Sample data: a one-off pull from centralized clouds, not live. Set{" "}
          <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code> to serve the live index.
        </p>
      )}

      <dl className="stats">
        <div>
          <dt>GPU models</dt>
          <dd>{lines.length}</dd>
        </div>
        <div>
          <dt>Offers</dt>
          <dd>{offers.toLocaleString("en-US")}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd className="small">{fmtTime(updatedAt)}</dd>
        </div>
      </dl>

      <IndexTable lines={lines} showDecentralized={hasDecentralized} />

      <footer className="foot">
        <p>
          Median and lowest price per GPU-hour, from offers seen in the last 6 hours. Centralized
          prices come from{" "}
          <a href="https://github.com/dstackai/gpuhunt">gpuhunt</a>; decentralized prices from each
          network&apos;s market API. Listed prices only: availability isn&apos;t guaranteed.
        </p>
        <p>
          JSON: <a href="/api/v1/gpu-index">/api/v1/gpu-index</a>
        </p>
      </footer>
    </main>
  );
}
