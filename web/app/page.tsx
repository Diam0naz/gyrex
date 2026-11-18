import { functionBaseUrl } from "@neon/env";
import { getIndex, type IndexData } from "@/lib/load";
import { realtimeEnabled, sealGpuIndex } from "@/lib/realtime";
import LiveIndex from "./LiveIndex";

export const revalidate = 300;

/** Public URL of the `snapshot` Neon Function (daily index history), when the branch env is present. */
function historyUrl(): string | null {
  try {
    return functionBaseUrl("snapshot");
  } catch {
    return null; // NEON_BRANCH_ID / NEON_FUNCTION_HOST_SUFFIX not set (demo mode)
  }
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
  const { rows, source } = data;
  const live = source === "neon" && realtimeEnabled();
  const sealedQuery = live ? await sealGpuIndex() : null;
  const history = source === "neon" ? historyUrl() : null;
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

      <LiveIndex
        initialRows={rows}
        source={source}
        sealedQuery={sealedQuery}
        realtimeUrl={live ? (process.env.NEXT_PUBLIC_NEON_REALTIME_URL ?? null) : null}
      />

      <footer className="foot">
        <p>
          Median and lowest price per GPU-hour, from each provider&apos;s latest listing. Centralized
          prices come from <a href="https://github.com/dstackai/gpuhunt">gpuhunt</a>; decentralized
          prices from each network&apos;s market API. Listed prices only: availability isn&apos;t
          guaranteed.
        </p>
        <p>
          JSON: <a href="/api/v1/gpu-index">/api/v1/gpu-index</a>
          {history && (
            <>
              {" · "}Daily history: <a href={history}>{history.replace(/^https:\/\//, "")}</a>
            </>
          )}
        </p>
      </footer>
    </main>
  );
}
