import assert from "node:assert/strict";
import test from "node:test";

import { dispatchCollector, recentRun } from "./github.ts";

const NOW = new Date("2026-11-19T05:23:00Z");
const CONFIG = { token: "t0ken", repo: "owner/repo", workflow: "collect.yml", ref: "main" };

function fakeGitHub(runs, dispatchStatus = 204) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, method: init.method ?? "GET", headers: init.headers, body: init.body });
    if (url.endsWith("/dispatches")) return new Response(null, { status: dispatchStatus });
    return Response.json({ workflow_runs: runs });
  };
  return { calls, fetchImpl };
}

const finished = (minutesAgo) => ({
  status: "completed",
  created_at: new Date(NOW.getTime() - minutesAgo * 60_000).toISOString(),
});

test("dispatches when the last run is old", async () => {
  const gh = fakeGitHub([finished(60)]);
  assert.deepEqual(await dispatchCollector(CONFIG, NOW, gh.fetchImpl), { action: "dispatched" });
  const post = gh.calls[1];
  assert.equal(post.method, "POST");
  assert.equal(post.url, "https://api.github.com/repos/owner/repo/actions/workflows/collect.yml/dispatches");
  assert.deepEqual(JSON.parse(post.body), { ref: "main" });
  assert.equal(post.headers.Authorization, "Bearer t0ken");
});

test("skips while a run is queued or in progress", async () => {
  for (const status of ["queued", "in_progress", "waiting"]) {
    const gh = fakeGitHub([{ status, created_at: finished(90).created_at }]);
    const outcome = await dispatchCollector(CONFIG, NOW, gh.fetchImpl);
    assert.equal(outcome.action, "skipped");
    assert.equal(gh.calls.length, 1, "no dispatch");
  }
});

test("skips right after another run, such as GitHub's fallback", async () => {
  const gh = fakeGitHub([finished(5)]);
  assert.deepEqual(await dispatchCollector(CONFIG, NOW, gh.fetchImpl), {
    action: "skipped",
    reason: "a run started 5 min ago",
  });
});

test("the fallback at :53 does not block the next :23 run", () => {
  assert.equal(recentRun([finished(30)], NOW), null);
});

test("dispatches when the workflow has never run", async () => {
  const gh = fakeGitHub([]);
  assert.equal((await dispatchCollector(CONFIG, NOW, gh.fetchImpl)).action, "dispatched");
});

test("a GitHub error is raised without the token", async () => {
  const gh = fakeGitHub([finished(60)], 403);
  await assert.rejects(dispatchCollector(CONFIG, NOW, gh.fetchImpl), (e) => {
    assert.match(e.message, /starting the workflow: GitHub returned 403/);
    assert.doesNotMatch(e.message, /t0ken/);
    return true;
  });
});
