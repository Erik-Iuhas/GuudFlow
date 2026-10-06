// Generates run2000.json: a recorded-looking opencode run of exactly 2,000 signals across
// five roles (planner, backend, frontend, reviewer, overseer), plus the run meta, project
// config and REST card list a cockpit needs to render it.
//
//   node src/features/cockpit/__fixtures__/gen-run2000.mjs
//
// Deterministic (seeded PRNG): re-running produces the same file.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TOTAL = 2000;
const RUN_ID = "run-fixture-2000";
const PROJECT_ID = "fixture-webapp";
const T0 = Date.parse("2026-03-01T10:00:00Z");

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(2000);
const pick = (xs) => xs[Math.floor(rand() * xs.length)];
const between = (lo, hi) => lo + Math.floor(rand() * (hi - lo + 1));

const signals = [];
let clock = T0;
let iteration = 1;

function post(type, from, summary, detail = null, extra = {}) {
  clock += between(300, 2600);
  const n = signals.length + 1;
  signals.push({
    id: `sig-${String(n).padStart(4, "0")}`,
    type,
    summary,
    detail: detail === null ? null : typeof detail === "string" ? detail : JSON.stringify(detail),
    from_agent: from,
    from_user: false,
    priority: "normal",
    iteration,
    seen_by: [],
    timestamp: new Date(clock).toISOString().replace("Z", "+00:00"),
    ...extra,
  });
}

const FILES = {
  backend: ["api/routes/items.py", "api/models.py", "api/db.py", "api/auth.py", "tests/test_items.py", "api/schemas.py"],
  frontend: ["web/src/App.tsx", "web/src/ItemList.tsx", "web/src/api.ts", "web/src/ItemForm.tsx", "web/src/styles.css"],
  planner: ["PLAN.md", "docs/architecture.md"],
  reviewer: ["api/routes/items.py", "web/src/ItemList.tsx"],
  overseer: ["PLAN.md"],
};
const TOOLS = [
  ["read", (f) => ({ filePath: f }), () => "(file contents)"],
  ["grep", () => ({ pattern: pick(["def create_", "useState", "TODO", "export function"]) }), () => "3 matches"],
  ["edit", (f) => ({ filePath: f, oldString: "x", newString: "y" }), () => "Edit applied"],
  ["write", (f) => ({ filePath: f, content: "…" }), () => "Wrote file"],
  ["bash", () => ({ command: pick(["pytest -x -q", "npm run typecheck", "ruff check .", "git status --short"]) }), () => "ok"],
  ["glob", () => ({ pattern: "**/*.py" }), () => "12 files"],
];

const costs = { planner: 0, backend: 0, frontend: 0, reviewer: 0, overseer: 0 };
const spawned = new Set();

function capabilities(role) {
  const reviewer = role === "reviewer";
  const planner = role === "planner" || role === "overseer";
  post("CAPABILITIES", role, `${role} capabilities`, {
    role_type: reviewer ? "reviewer" : planner ? role : "developer",
    requested: {},
    effective: {
      sandbox: "strict",
      web: role === "frontend" ? "search" : "off",
      shell: reviewer || planner ? "off" : "on",
      packages: role === "backend" ? "ask" : "off",
      subagents: "off",
      browser: role === "frontend" ? "on" : "off",
      vision: role === "frontend" ? "on" : "off",
    },
    digest: `d-${role}`,
    mcp_tools: [],
    model: reviewer || role === "planner" ? "claude-opus" : "claude-sonnet",
    warnings: [],
  });
}

function toolCalls(role, n) {
  const files = FILES[role];
  for (let i = 0; i < n; i++) {
    const [tool, input, output] = pick(TOOLS);
    const f = pick(files);
    const failed = rand() < 0.07;
    const inp = input(f);
    post("TOOL_CALL", role, `${role}: ${tool} ${inp.filePath ?? inp.command ?? inp.pattern} → ${failed ? "error" : "ok"}`, {
      tool,
      status: failed ? "error" : "completed",
      input: inp,
      ...(failed ? { error: "exit code 1: 2 failed" } : { output: output() }),
      duration_ms: between(20, 4200),
    });
  }
}

/** One agent turn: STARTED, tool calls, AGENT_RESULT, COMPLETE. */
function turn(role, nTools, { verdict, issues, summary, blockedOn } = {}) {
  post("STARTED", role, `${role} started`);
  if (!spawned.has(role)) {
    spawned.add(role);
    capabilities(role);
  }
  toolCalls(role, nTools);
  const cost = Number((0.004 + rand() * 0.05).toFixed(4));
  costs[role] += cost;
  const result =
    role === "reviewer"
      ? { verdict, summary: summary ?? "Reviewed against the acceptance list.", issues: issues ?? [] }
      : { status: blockedOn ? "blocked" : "done", summary: summary ?? `${role} finished the card.` };
  post("AGENT_RESULT", role, `${role} result: ${result.status ?? result.verdict}`, {
    result,
    cost,
    tokens: { input: between(2000, 18000), output: between(300, 2600) },
    tool_calls: [],
    commits: role === "reviewer" ? [] : ["c" + between(1000, 9999)],
    files: role === "reviewer" ? [] : [pick(FILES[role]), pick(FILES[role])],
  });
  if (blockedOn) {
    post("BLOCKED", role, `${role} is blocked on ${blockedOn.role}`, {
      task_id: blockedOn.card, on: blockedOn.role, need: blockedOn.need, unblock_card: blockedOn.card + "b",
    });
  } else {
    post("COMPLETE", role, `${role} complete`);
  }
}

function moved(card, from, to, owner, round, reason) {
  post("TASK_MOVED", "runner", `${card} ${from} → ${to}`, { task_id: card, from, to, reason, owner, round });
}

const cards = [];
function planCards(n) {
  const titles = [
    "Items table and migration", "POST /items endpoint", "GET /items with pagination", "Auth middleware",
    "Item list component", "Item form with validation", "API client module", "Error toasts",
    "Rate limiting", "Search box", "Delete confirmation", "CSV export",
  ];
  for (let i = 1; i <= n; i++) {
    const owner = i % 2 === 0 ? "frontend" : "backend";
    cards.push({
      id: `T${i}`, title: titles[(i - 1) % titles.length], owner, column: "todo",
      kind: owner === "frontend" ? "visual" : "code", size: pick(["S", "M"]), priority: i,
      review_round: 0, acceptance: ["Tests pass", "Matches the spec", "No lint errors"], depends_on: i > 2 ? [`T${i - 2}`] : [],
      column_since: null, thread: [],
    });
  }
}

// ── The run ──────────────────────────────────────────────────────────────────

// Reserve room so the final wrap-up lands the total on exactly TOTAL.
const WRAPUP_FIXED = 8; // STARTED, AGENT_RESULT, 6 COMPLETE (the rest of the wrap-up is tool calls)

planCards(12);
turn("planner", 14, { summary: "Planned 12 cards across backend and frontend." });
for (const c of cards) {
  post("TASK_ASSIGNED", "planner", `task:${c.id} → ${c.owner}: ${c.title}`, c.id);
}
post("USER_COMMENT", null, "Please keep the API backwards compatible.", null, { from_user: true });

let cardIx = 0;
let approvals = 0;
let interventions = 0;
while (signals.length < TOTAL - WRAPUP_FIXED - 130) {
  const card = cards[cardIx % cards.length];
  cardIx += 1;
  const owner = card.owner;
  iteration = 1 + Math.floor(cardIx / cards.length);
  const cardId = card.id;
  const round0 = 0;

  moved(cardId, "todo", "in_progress", owner, round0, "scheduler started the turn");
  if (cardIx % 7 === 3 && approvals < 6) {
    approvals += 1;
    const id = `appr-${approvals}`;
    const ctx = JSON.stringify({ role: owner, permission: "bash", patterns: ["pip install httpx"] });
    post("APPROVAL_NEEDED", owner, `${owner} asks to run: pip install httpx`, `approval_id=${id}; kind=tool_permission; context=${ctx}`);
    post("APPROVAL_GRANTED", "runner", `approval ${id} granted`, `approval_id=${id}; kind=tool_permission`);
  }
  turn(owner, between(6, 14), { summary: `Implemented ${card.title}.` });
  post("TASK_THREAD", owner, `${cardId}: note`, {
    task_id: cardId, kind: "note", author: owner, text: `Done with ${card.title}; tests added.`, iteration, data: {},
  });
  moved(cardId, "in_progress", "review", owner, round0, "result done");

  const rejections = rand() < 0.45 ? between(1, 2) : 0;
  let round = 0;
  for (let r = 0; r < rejections; r++) {
    round += 1;
    const issues = [{ file: pick(FILES[owner]), detail: pick(["Missing input validation", "Off-by-one in pagination", "No test for the empty list"]) }];
    turn("reviewer", between(5, 11), { verdict: "fail", issues, summary: `Round ${round}: ${issues[0].detail}.` });
    post("REJECTED", "reviewer", `reviewer sent ${cardId} back to ${owner} (round ${round})`, {
      task_id: cardId, authors: [owner], round, issues,
    });
    post("TASK_THREAD", "reviewer", `${cardId}: review issue`, {
      task_id: cardId, kind: "review_issue", author: "reviewer", text: issues[0].detail, iteration, data: { issues },
    });
    moved(cardId, "review", "in_progress", owner, round, "review failed");
    turn(owner, between(4, 10), { summary: `Reworked ${card.title} (round ${round}).` });
    moved(cardId, "in_progress", "review", owner, round, "result done");
  }

  if (rejections === 2 && interventions < 4) {
    interventions += 1;
    const type = pick(["rebrief", "decide", "extend_review"]);
    turn("overseer", between(3, 6), { summary: "Review loop is not converging; sharpening the brief." });
    post("OVERSEER_INTERVENTION", "overseer", `overseer: ${type} on ${cardId}`, {
      type, rationale: `${cardId} was rejected twice for the same class of issue.`, needs_human: false,
      role: owner, task_id: cardId, addendum: "Follow the acceptance list item by item before submitting.",
    });
  }

  if (cardIx % 9 === 5) {
    const other = owner === "backend" ? "frontend" : "backend";
    post("STARTED", owner, `${owner} started`);
    post("BLOCKED", owner, `${owner} is blocked on ${other} (${cardId})`, {
      task_id: cardId, on: other, need: "Needs the response shape for /items", unblock_card: `${cardId}b`,
    });
    post("STARTED", owner, `${owner} started`);
    post("COMPLETE", owner, `${owner} complete`);
  }

  turn("reviewer", between(5, 9), { verdict: "pass", summary: `All acceptance items met for ${cardId}.` });
  moved(cardId, "review", "done", owner, round, "review passed");
  card.column = "done";
  card.review_round = round;
}

// Wrap-up. The reviewer's last turn absorbs any shortfall so the total is exactly TOTAL.
iteration += 1;
const fixed = signals.length + WRAPUP_FIXED;
const pad = TOTAL - fixed;
if (pad < 0) throw new Error(`overshot by ${-pad}; lower the loop bound`);
post("STARTED", "reviewer", "reviewer started");
toolCalls("reviewer", pad);
post("AGENT_RESULT", "reviewer", "reviewer result: pass", {
  result: { verdict: "pass", summary: "Final pass over the whole branch.", issues: [] }, cost: 0.02,
  tokens: { input: 9000, output: 800 }, tool_calls: [], commits: [], files: [],
});
costs.reviewer += 0.02;
post("COMPLETE", "reviewer", "reviewer complete");
for (const role of ["planner", "backend", "frontend", "overseer"]) post("COMPLETE", role, `${role} complete`);
post("COMPLETE", "runner", "run complete");

if (signals.length !== TOTAL) throw new Error(`expected ${TOTAL} signals, got ${signals.length}`);

const project = {
  project_id: PROJECT_ID,
  goal: "Build an items CRUD web app",
  harness: "opencode",
  approval_gates: { token_budget_usd: 5 },
  agents: [
    { role: "planner", model_tier: "heavy", depends_on: [] },
    { role: "backend", model_tier: "solid", depends_on: ["planner"] },
    { role: "frontend", model_tier: "solid", depends_on: ["planner"] },
    { role: "reviewer", model_tier: "heavy", depends_on: ["backend", "frontend"], is_reviewer: true, review_of: ["backend", "frontend"], max_review_rounds: 3 },
    { role: "overseer", model_tier: "heavy", depends_on: [] },
  ],
};
const meta = {
  run_id: RUN_ID, project_id: PROJECT_ID, goal: project.goal, status: "complete", iteration,
  created_at: signals[0].timestamp, token_budget_usd: 5, harness: "opencode",
};
const quips = signals
  .filter((s) => s.type === "APPROVED" || s.type === "REJECTED")
  .slice(0, 25)
  .map((s) => ({ ts: s.timestamp, persona: "Reviewer", role: "reviewer", text: "Not on my watch.", event: "rejected" }));

const here = dirname(fileURLToPath(import.meta.url));
writeFileSync(join(here, "run2000.json"), JSON.stringify({ meta, project, cards, quips, signals }, null, 1) + "\n");
console.log(`wrote ${signals.length} signals, ${cards.length} cards; spend $${Object.values(costs).reduce((a, b) => a + b, 0).toFixed(2)}`);
