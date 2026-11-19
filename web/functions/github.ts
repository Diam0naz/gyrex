/**
 * Starts the collector workflow on GitHub, unless a run is already going or just started.
 * No imports, so `node --test` can load it directly (see github.test.mjs).
 */

export interface DispatchConfig {
  token: string;
  repo: string; // owner/name
  workflow: string; // file name, e.g. collect.yml
  ref: string;
}

export interface WorkflowRun {
  status: string;
  created_at: string;
}

export type Outcome = { action: "dispatched" } | { action: "skipped"; reason: string };

/** Shorter than the gap between this trigger (:23) and GitHub's fallback cron (:53). */
export const RECENT_MINUTES = 20;
const TIMEOUT_MS = 20_000;

export function recentRun(runs: WorkflowRun[], now: Date, minutes = RECENT_MINUTES): string | null {
  for (const run of runs) {
    if (run.status !== "completed") return `a run is ${run.status}`;
    const age = (now.getTime() - Date.parse(run.created_at)) / 60_000;
    if (age >= 0 && age < minutes) return `a run started ${Math.floor(age)} min ago`;
  }
  return null;
}

async function failure(what: string, response: Response): Promise<Error> {
  const body = (await response.text()).slice(0, 300);
  return new Error(`${what}: GitHub returned ${response.status} ${body}`);
}

export async function dispatchCollector(
  config: DispatchConfig,
  now = new Date(),
  fetchImpl: typeof fetch = fetch,
): Promise<Outcome> {
  const headers = {
    Authorization: `Bearer ${config.token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "gyrex-scheduler",
  };
  const base = `https://api.github.com/repos/${config.repo}/actions/workflows/${encodeURIComponent(config.workflow)}`;

  const list = await fetchImpl(`${base}/runs?per_page=5`, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!list.ok) throw await failure("listing runs", list);
  const { workflow_runs = [] } = (await list.json()) as { workflow_runs?: WorkflowRun[] };
  const reason = recentRun(workflow_runs, now);
  if (reason) return { action: "skipped", reason };

  const started = await fetchImpl(`${base}/dispatches`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ ref: config.ref }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!started.ok) throw await failure("starting the workflow", started);
  return { action: "dispatched" };
}
