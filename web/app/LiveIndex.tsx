"use client";

import { useCallback, useState } from "react";
import { createRealtimeClient, type SealedLiveQuery } from "@neon/realtime/client";
import { RealtimeProvider, useLiveQuery } from "@neon/realtime-react";
import { latestSeen, toLines, type IndexRow, type IndexSource } from "@/lib/data";
import IndexTable from "./IndexTable";

type Props = {
  initialRows: IndexRow[];
  source: IndexSource;
  sealedQuery: SealedLiveQuery<IndexRow> | null;
  realtimeUrl: string | null;
};

type LiveStatus = "static" | "connecting" | "live" | "paused";

/** Renders the server's rows at once; with Realtime configured, swaps to live rows in place. */
export default function LiveIndex({ initialRows, source, sealedQuery, realtimeUrl }: Props) {
  if (!sealedQuery || !realtimeUrl) {
    return <IndexView rows={initialRows} source={source} live="static" />;
  }
  return (
    <RealtimeRoot url={realtimeUrl}>
      <Subscribed initialRows={initialRows} source={source} sealedQuery={sealedQuery} />
    </RealtimeRoot>
  );
}

function RealtimeRoot({ url, children }: { url: string; children: React.ReactNode }) {
  // One client per mounted app, created in the browser tree; the server render never connects.
  const [client] = useState(() => createRealtimeClient({ url }));
  return <RealtimeProvider client={client}>{children}</RealtimeProvider>;
}

async function renewSealedQuery(): Promise<SealedLiveQuery<IndexRow>> {
  const res = await fetch("/api/realtime/gpu-index", { method: "POST", cache: "no-store" });
  if (!res.ok) throw new Error(`renew failed: ${res.status}`);
  return (await res.json()) as SealedLiveQuery<IndexRow>;
}

function Subscribed({
  initialRows,
  source,
  sealedQuery,
}: {
  initialRows: IndexRow[];
  source: IndexSource;
  sealedQuery: SealedLiveQuery<IndexRow>;
}) {
  const [query] = useState(sealedQuery); // renewals go through refreshQuery
  const refreshQuery = useCallback(renewSealedQuery, []);
  const { data, status } = useLiveQuery(query, { refreshQuery, initialData: initialRows });
  const live: LiveStatus = status === "live" ? "live" : status === "error" || status === "stale" ? "paused" : "connecting";
  return <IndexView rows={data ?? initialRows} source={source} live={live} />;
}

function fmtTime(iso: string | null) {
  if (!iso) return "unknown";
  return (
    new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC"
  );
}

const LIVE_LABEL: Record<LiveStatus, string | null> = {
  static: null,
  connecting: "Connecting to live updates…",
  live: "Live: updates as new prices arrive",
  paused: "Live updates paused; showing the last prices received",
};

function IndexView({ rows, source, live }: { rows: readonly IndexRow[]; source: IndexSource; live: LiveStatus }) {
  const lines = toLines(rows);
  const offers = rows.reduce((n, r) => n + r.offers, 0);
  const hasDecentralized = rows.some((r) => r.offer_type === "decentralized");
  const label = LIVE_LABEL[live];

  return (
    <>
      {source === "demo" && (
        <p className="notice" role="status">
          Sample data: a one-off pull from centralized clouds, not live. Set <code>DATABASE_URL</code> to serve
          the live index from Neon.
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
          <dd className="small">{fmtTime(latestSeen(rows))}</dd>
        </div>
      </dl>
      {label && (
        <p className={`live live-${live}`} role="status" data-live={live}>
          <span className="dot" aria-hidden="true" />
          {label}
        </p>
      )}
      <IndexTable lines={lines} showDecentralized={hasDecentralized} />
    </>
  );
}
