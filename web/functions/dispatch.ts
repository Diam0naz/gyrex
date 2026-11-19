/**
 * `dispatch` Neon Function: the collector's scheduler.
 *
 *   POST /  (Function Trigger, hourly cron in neon.ts) -> starts .github/workflows/collect.yml
 *
 * GitHub's own cron skips runs under load, and every missed run is a permanent gap in the price
 * history. Neon's trigger is the primary schedule; GitHub's cron stays as a slower fallback.
 * Only genuine trigger deliveries are accepted (parseTriggerDelivery), and only the branch named
 * in SCHEDULE_ON_BRANCH acts, so preview branches never start production runs.
 */
import { parseTriggerDelivery } from "@neon/functions/triggers";
import { dispatchCollector } from "./github";

const config = {
  token: process.env.GITHUB_DISPATCH_TOKEN ?? "",
  repo: process.env.GITHUB_REPO ?? "",
  workflow: process.env.GITHUB_WORKFLOW_FILE ?? "collect.yml",
  ref: process.env.GITHUB_REF_NAME ?? "main",
};

export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405, headers: { Allow: "POST" } });
    }
    const parsed = await parseTriggerDelivery(request);
    if (!parsed.ok) {
      return new Response(parsed.error, { status: parsed.error === "invalid_body" ? 400 : 401 });
    }
    const invocation = parsed.invocation;
    if (invocation.type !== "schedule") return new Response("unsupported trigger", { status: 400 });

    if (process.env.NEON_BRANCH !== process.env.SCHEDULE_ON_BRANCH) {
      return Response.json({ action: "skipped", reason: `branch ${process.env.NEON_BRANCH} does not schedule` });
    }
    if (!config.token || !config.repo) {
      console.error("dispatch: GITHUB_DISPATCH_TOKEN or GITHUB_REPO is not set");
      return Response.json({ error: "not configured" }, { status: 500 });
    }
    try {
      const outcome = await dispatchCollector(config);
      const detail = outcome.action === "skipped" ? ` (${outcome.reason})` : "";
      console.log(`dispatch ${invocation.invocationId}: ${outcome.action}${detail}`);
      return Response.json(outcome);
    } catch (e) {
      console.error(`dispatch ${invocation.invocationId} failed:`, e instanceof Error ? e.message : e);
      return Response.json({ error: "dispatch failed" }, { status: 502 });
    }
  },
};
