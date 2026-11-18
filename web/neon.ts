import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  // Live queries for the index page (see lib/realtime.ts). Browser origins allowed to connect:
  // add the production domain once Vercel assigns it.
  preview: {
    realtime: {
      allowedOrigins: ["http://localhost:3000", "http://localhost:3100"],
    },
  },
  auth: false,
  functions: {
    snapshot: { name: "Daily index snapshot", source: "./functions/snapshot.ts" },
  },
  triggers: {
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
