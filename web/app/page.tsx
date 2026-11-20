import { functionBaseUrl } from "@neon/env";
import { latestSeen, toLines } from "@/lib/data";
import { featured, spread, usd } from "@/lib/featured";
import { getIndex, type IndexData } from "@/lib/load";
import { realtimeEnabled, sealGpuIndex } from "@/lib/realtime";
import Depth from "./Depth";
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

/** The logo's path: a G that spirals into its centre. Reused large, as the background gyre. */
const GYRE = "M45.4 18.6A19 19 0 1 0 51 32H40a8 8 0 0 0-16 0 4 4 0 0 0 8 0";

function Nav() {
  return (
    <nav className="nav" aria-label="Main">
      <a className="brand" href="#top">
        <img src="/gyrex-mark.svg" alt="" width={26} height={26} />
        <span>Gyrex</span>
      </a>
      <div className="nav-links">
        <a href="#index">Index</a>
        <a href="#method">Method</a>
        <a href="#api">API</a>
      </div>
      <a className="btn btn-small" href="#index">
        Live prices
      </a>
    </nav>
  );
}

function Unavailable() {
  return (
    <main className="site" id="top">
      <Nav />
      <section className="hero hero-short">
        <div className="plane plane-back" aria-hidden="true">
          <div className="floor" />
          <div className="glow" />
        </div>
        <div className="plane plane-front hero-copy">
          <p className="kicker">GPU price index</p>
          <h1 className="display">
            Back <em>shortly.</em>
          </h1>
          <p className="lede" role="alert">
            The index is temporarily unavailable. Try again in a few minutes.
          </p>
        </div>
      </section>
    </main>
  );
}

export default async function Page() {
  let data: IndexData;
  try {
    data = await getIndex();
  } catch (e) {
    console.error(e);
    return <Unavailable />;
  }
  const { rows, source } = data;
  const live = source === "neon" && realtimeEnabled();
  const sealedQuery = live ? await sealGpuIndex() : null;
  const history = source === "neon" ? historyUrl() : null;
  const hasDecentralized = rows.some((r) => r.offer_type === "decentralized");

  const cards = featured(rows, 3);
  const range = spread(rows);
  const models = toLines(rows).length;
  const offers = rows.reduce((n, r) => n + r.offers, 0);
  const updated = latestSeen(rows);
  const sample = rows.find((r) => r.gpu_model === "H100" && r.offer_type === "on-demand") ?? rows[0];
  const sampleJson = sample
    ? JSON.stringify(
        {
          gpu_model: sample.gpu_model,
          vram_gb: sample.vram_gb,
          offer_type: sample.offer_type,
          offers: sample.offers,
          providers: sample.providers,
          min_usd_hr: sample.min_usd_hr,
          median_usd_hr: Number(sample.median_usd_hr.toFixed(4)),
          max_usd_hr: sample.max_usd_hr,
        },
        null,
        2,
      )
    : "{}";

  return (
    <main className="site" id="top">
      <Depth />
      <Nav />

      {/* ------------------------------------------------------------------ hero: three planes */}
      <section className="hero" aria-labelledby="hero-title">
        <div className="plane plane-back" aria-hidden="true">
          <div className="floor" />
          <svg className="gyre" viewBox="0 0 64 64">
            <path d={GYRE} />
          </svg>
          <div className="glow" />
          <span className="ghost-word">compute</span>
        </div>

        <div className="plane plane-mid cards" aria-label="Today's prices">
          {cards.map((c, i) => (
            <article className={`pcard pcard-${i}`} key={c.model}>
              <header>
                <span className="pcard-model">{c.model}</span>
                {c.vram ? <span className="pcard-vram">{c.vram} GB</span> : null}
              </header>
              <p className="pcard-price">
                {usd(c.lowest)}
                <span>/GPU-hr</span>
              </p>
              <p className="pcard-meta">
                Lowest, {c.lowestType}
                {c.onDemandMedian !== undefined ? ` · on-demand median ${usd(c.onDemandMedian)}` : ""}
              </p>
              <p className="pcard-foot">{c.offers.toLocaleString("en-US")} offers compared</p>
            </article>
          ))}
        </div>

        <div className="plane plane-front hero-copy">
          <p className="kicker">
            <span className={`pulse ${source === "neon" ? "on" : ""}`} aria-hidden="true" />
            {source === "neon" ? "Live GPU price index" : "GPU price index · sample data"}
          </p>
          <h1 className="display" id="hero-title">
            The true price
            <br />
            of <em>compute.</em>
          </h1>
          <p className="lede">
            One hour of one GPU, priced across centralized clouds
            {hasDecentralized ? " and decentralized networks" : ""}. Every listing normalized to the same
            unit, refreshed every hour.
          </p>
          <div className="ctas">
            <a className="btn" href="#index">
              Explore the index
            </a>
            <a className="btn btn-ghost" href="/api/v1/gpu-index">
              JSON API <span aria-hidden="true">→</span>
            </a>
          </div>
          <dl className="hero-stats">
            <div>
              <dt>GPU models</dt>
              <dd>{models}</dd>
            </div>
            <div>
              <dt>Offers tracked</dt>
              <dd>{offers.toLocaleString("en-US")}</dd>
            </div>
          </dl>
        </div>
      </section>

      {/* ------------------------------------------------------------------ focal: one cropped number */}
      {range && (
        <section className="focal" aria-labelledby="focal-title">
          <div className="plane plane-back hatch" aria-hidden="true" />
          <p className="focal-number plane-mid" aria-hidden="true">
            {(range.max / range.min).toFixed(1)}×
          </p>
          <div className="plane plane-front focal-copy">
            <p className="kicker">Same GPU, same hour</p>
            <h2 className="display-sm" id="focal-title">
              An {range.model} rents for <em>{usd(range.min)}</em> to <em>{usd(range.max)}</em> an hour.
            </h2>
            <p className="lede">
              {range.offers.toLocaleString("en-US")} on-demand {range.model}
              {range.vram ? ` ${range.vram} GB` : ""} listings, a {(range.max / range.min).toFixed(1)}× spread. Listed
              prices are the easy part to collect; knowing which ones are worth paying is the point of Gyrex.
            </p>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ index: the instrument */}
      <section className="index-section" id="index" aria-labelledby="index-title">
        <div className="plane plane-back dots" aria-hidden="true" />
        <div className="section-head plane-front">
          <p className="kicker">The index</p>
          <h2 className="display-sm" id="index-title">
            Every GPU, <em>one unit.</em>
          </h2>
          <p className="lede">
            Median and lowest price per GPU-hour from each provider&apos;s latest listing. Multi-GPU instances
            are divided by GPU count so every price compares.
          </p>
        </div>
        <div className="panel plane-mid">
          <LiveIndex
            initialRows={rows}
            source={source}
            sealedQuery={sealedQuery}
            realtimeUrl={live ? (process.env.NEXT_PUBLIC_NEON_REALTIME_URL ?? null) : null}
          />
        </div>
      </section>

      {/* ------------------------------------------------------------------ method: three layers of data */}
      <section className="method" id="method" aria-labelledby="method-title">
        <div className="plane plane-back lines" aria-hidden="true" />
        <div className="section-head plane-front">
          <p className="kicker">Method</p>
          <h2 className="display-sm" id="method-title">
            Price is the surface. <em>Reliability</em> is underneath.
          </h2>
        </div>
        <ol className="layers plane-mid">
          <li className="layer layer-0">
            <span className="layer-no">01</span>
            <h3>Listed</h3>
            <p>
              Prices from nine clouds via gpuhunt and from Nosana&apos;s markets, mapped to one GPU name and one unit.
              Only actual price moves are kept as history.
            </p>
            <span className="chip chip-live">Live, hourly</span>
          </li>
          <li className="layer layer-1">
            <span className="layer-no">02</span>
            <h3>Measured</h3>
            <p>
              Short benchmark jobs on real hosts: how long until the job starts, whether it finishes, and the
              throughput it actually delivers, starting with RTX 4090s on Nosana.
            </p>
            <span className="chip">In progress</span>
          </li>
          <li className="layer layer-2">
            <span className="layer-no">03</span>
            <h3>Scored</h3>
            <p>
              Success rate, startup time and steady throughput combined into a published, auditable score per
              network and GPU class.
            </p>
            <span className="chip chip-next">Next</span>
          </li>
        </ol>
      </section>

      {/* ------------------------------------------------------------------ api */}
      <section className="api" id="api" aria-labelledby="api-title">
        <div className="plane plane-back grid-field" aria-hidden="true" />
        <div className="section-head plane-front">
          <p className="kicker">API</p>
          <h2 className="display-sm" id="api-title">
            The same numbers, <em>as JSON.</em>
          </h2>
          <p className="lede">
            Open CORS, cached five minutes at the edge, free to read.
            {history ? " A daily snapshot keeps the history." : ""}
          </p>
          <div className="ctas">
            <a className="btn" href="/api/v1/gpu-index">
              Open the endpoint
            </a>
            {history && (
              <a className="btn btn-ghost" href={history}>
                Daily history <span aria-hidden="true">→</span>
              </a>
            )}
          </div>
        </div>
        <div className="code-stack plane-mid">
          <pre className="code code-back" aria-hidden="true">
            <code>{sampleJson}</code>
          </pre>
          <pre className="code code-front">
            <code>
              <span className="c-dim">$ </span>curl -s https://gyrex-joshua-utenohs-projects.vercel.app/api/v1/gpu-index \{"\n"}
              {"    "}| jq &apos;.rows[] | select(.gpu_model == &quot;H100&quot;)&apos;
            </code>
          </pre>
        </div>
      </section>

      {/* ------------------------------------------------------------------ footer */}
      <footer className="footer">
        <span className="footer-word" aria-hidden="true">
          gyrex
        </span>
        <div className="footer-inner">
          <p>
            Centralized prices via <a href="https://github.com/dstackai/gpuhunt">gpuhunt</a>; decentralized prices
            from each network&apos;s market API, including its fee. Listed prices only: availability isn&apos;t
            guaranteed.
          </p>
          <p className="footer-meta">
            {updated
              ? `Updated ${new Date(updated).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC`
              : null}
          </p>
        </div>
      </footer>
    </main>
  );
}
