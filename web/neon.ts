import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  // Live queries for the index page (see lib/realtime.ts). Browser origins allowed to connect:
  // add the production domain once Vercel assigns it.
  preview: {
    realtime: {
      allowedOrigins: [
        "http://localhost:3000",
        "http://localhost:3100",
        "https://gyrex-joshua-utenohs-projects.vercel.app",
      ],
    },
  },
  auth: false,
  functions: {
    snapshot: { name: "Daily index snapshot", source: "./functions/snapshot.ts" },
    dispatch: {
      name: "Collector scheduler",
      source: "./functions/dispatch.ts",
      env: {
        // Fine-grained GitHub token: this repository only, Actions read and write.
        GITHUB_DISPATCH_TOKEN: { value: process.env.GITHUB_DISPATCH_TOKEN, required: "default-branch" },
        GITHUB_REPO: "Diam0naz/gyrex",
        GITHUB_WORKFLOW_FILE: "collect.yml",
        GITHUB_REF_NAME: "main",
        SCHEDULE_ON_BRANCH: "production",
      },
    },
  },
  triggers: {
    // Hourly collector run. GitHub's cron in collect.yml is only a fallback: it skips runs under load.
    "hourly-collect": { type: "schedule", function: "dispatch", cron: "23 * * * *" },
    // 00:41 UTC daily: after the 00:23 collector run, so each day snapshots that day's first prices.
    "daily-snapshot": { type: "schedule", function: "snapshot", cron: "41 0 * * *" },
  },
  // Branch policy: per-branch tuning
  branch: (branch) => {
    if (branch.isDefault) return {};
    // New non-default branches auto-expire. `neon checkout <name>` creates one with these settings.
    if (!branch.exists) return { ttl: "7d" };
    return {};
  },
});
