import type { RunReportData } from "../api";

/** A finished run's report payload, shared by the unit tests and the Playwright flow. */
export const REPORT_FIXTURE: RunReportData = {
  run_id: "run_rep",
  meta: { status: "complete", project_id: "p1", created_at: "2026-03-01T10:00:00+00:00" },
  signal_count: 40,
  first_signal_at: "2026-03-01T10:00:00+00:00",
  last_signal_at: "2026-03-01T10:12:03+00:00",
  retrospective: {
    status: "complete",
    stall_minutes: 3.5,
    recurring_issues: [["T2", "lint errors in api.py"]],
    review_rounds_used: { "dev->reviewer": 2 },
    cards: [
      { task_id: "T1", title: "Plan", owner: "planner", column: "done", review_round: 0 },
      { task_id: "T2", title: "Build API", owner: "dev", column: "done", review_round: 2 },
      { task_id: "T3", title: "Docs", owner: "dev", column: "blocked", review_round: 0 },
    ],
  },
  carry_forward: "Previous run: `run_rep` (complete), main at commit `abc1234`.\n\nStill open: T3 Docs",
  costs: {
    rows: [
      { role: "dev", calls: 12, input_tokens: 120000, output_tokens: 30000, cache_read_tokens: 40000, images: 2, cost_usd: 1.2345 },
      { role: "reviewer", calls: 4, input_tokens: 20000, output_tokens: 3000, cache_read_tokens: 0, images: 0, cost_usd: 0.15 },
    ],
    total: { calls: 16, input_tokens: 140000, output_tokens: 33000, cache_read_tokens: 40000, images: 2, cost_usd: 1.3845 },
  },
  capabilities: [
    { role: "dev", role_type: "builder", model: "anthropic/sonnet", effective: { shell: "sandboxed", web: "search" } },
    { role: "reviewer", role_type: "reviewer", model: "anthropic/sonnet", effective: { shell: "off", web: "off" } },
  ],
  web: [
    { role: "dev", tool: "web_search", query: "open-meteo hourly format", status: 200, bytes: 2048, cache_hit: false },
    { role: "dev", tool: "web_fetch", url: "http://169.254.169.254/", status: null, bytes: 0, refused: "private address" },
  ],
  interventions: [
    { signal_id: "i1", type: "rebrief", target: "dev", rationale: "dev kept guessing the response shape", needs_human: false, detail: {}, worked: true },
    { signal_id: "i2", type: "propose_team_change", target: "", rationale: "docs need their own role", needs_human: true, detail: {}, worked: false },
  ],
  branches: [
    {
      role: "dev", branch: "agent/dev",
      commits: [{ sha: "a".repeat(40), short_sha: "aaaaaaa", subject: "feat: add forecast endpoint", author: "dev", committed_at: 1772359200 }],
    },
  ],
};

export const SAMPLE_DIFF = [
  "diff --git a/api.py b/api.py",
  "index 1111111..2222222 100644",
  "--- a/api.py",
  "+++ b/api.py",
  "@@ -1,3 +1,4 @@",
  " import json",
  "-print('old')",
  "+print('new')",
  "+print('extra')",
  " end",
  "",
].join("\n");
