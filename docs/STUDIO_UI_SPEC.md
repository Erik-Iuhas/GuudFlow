# Studio UI spec (O21)

The contract for the UI track (plan §12; work breakdown S23–S27, S29, S33, O22). It
fixes three things the Sonnet tasks build against: the **design tokens**, the **typed
run-event store**, and the **Run cockpit layout**. Anything not fixed here is the
implementer's call, consistent with §12.2.

## 1. Stack (pinned at U0)

| Concern | Package | Pin |
|---|---|---|
| Styling | `tailwindcss` 3.4 + `postcss` + `autoprefixer` | `^3.4` (v4's config model is a later upgrade) |
| Components | shadcn/ui pattern: Radix primitives copied into `src/components/ui/` | `@radix-ui/react-*` latest 1.x |
| Utilities | `clsx`, `tailwind-merge`, `class-variance-authority` | latest |
| Server state | `@tanstack/react-query` | `^5` |
| Live state | `zustand` | `^5` |
| Graph | `@xyflow/react` | `^12` |
| Long lists | `@tanstack/react-virtual` | `^3` |
| Diffs | `react-diff-view` + `gitdiff-parser` | latest |
| SVG safety | `dompurify` | `^3` |
| E2E | `@playwright/test` (browser at `/opt/pw-browsers`, never `playwright install`) | match installed |

React stays on 18 and Vite on 5 for U0. Upgrading either is its own change.

## 2. Design tokens

CSS variables in `src/styles/tokens.css`, consumed by Tailwind (`theme.extend.colors`
maps to `hsl(var(--…))`). Light values live on `:root`, dark on `.dark` (class
strategy, toggled from Settings and stored in `localStorage` under `guudflow.theme`;
the default follows `prefers-color-scheme`).

| Token | Use |
|---|---|
| `--background`, `--foreground` | page |
| `--card`, `--card-foreground`, `--popover`, `--popover-foreground` | surfaces |
| `--muted`, `--muted-foreground` | secondary text, empty states |
| `--border`, `--input`, `--ring` | lines, inputs, focus ring (always visible on focus) |
| `--primary`, `--primary-foreground` | main actions |
| `--secondary`, `--accent`, `--destructive` (+ `-foreground`) | |
| `--status-running`, `--status-complete`, `--status-blocked`, `--status-failed`, `--status-waiting` | agent and card states |
| `--col-todo`, `--col-in_progress`, `--col-review`, `--col-done`, `--col-blocked`, `--col-needs_attention` | board columns (names match `TaskColumn`) |
| `--radius` | 0.5rem |
| `--font-sans`, `--font-mono` | system stacks; no web-font download |

Spacing and type use the Tailwind scale. No raw hex values outside `tokens.css`. No new
`style={{…}}` objects (U4 removes the remaining ones).

## 3. The typed run-event store

### 3.1 Source of truth

`studio/src/lib/runEvents.gen.ts` is **generated** by `scripts/gen_studio_events.py`
from `core/signals.py` and `core/harness/event_contract.py`. A backend test fails when
it is stale. It exports `SignalType`, `RawSignal`, one `…Detail` interface per
JSON-detail kind, `DetailByType`, `TypedSignal`, `TaskColumn`, `ThreadKind` and
`InterventionType`. Never hand-edit it. A new detail field is added to
`event_contract.py`, then the generator is re-run.

### 3.2 Store (`src/stores/runEvents.ts`, Zustand)

One store instance per open run (`createRunStore(runId)`), fed by the run websocket
(`/ws/runs/{id}`: first `{"type":"snapshot","signals":[…]}`, then
`{"type":"signal","signal":{…}}`) and seeded with REST where noted.

```ts
interface RunState {
  runId: string;
  live: boolean;                         // snapshot.live
  signals: TypedSignal[];                // append-only, in arrival order, deduped by id
  byId: Map<string, TypedSignal>;
  agents: Record<string, AgentView>;     // derived (below)
  cards: Record<string, CardView>;       // GET /api/runs/{id}/cards, then TASK_* signals
  approvals: ApprovalView[];             // APPROVAL_NEEDED minus GRANTED/DENIED
  interventions: InterventionView[];     // OVERSEER_INTERVENTION (+ the AGENT_RESULT of the overseer)
  costs: Record<string, number>;         // role → USD, from AGENT_RESULT.cost (gateway-backed)
  artifacts: ArtifactView[];             // ARTIFACT + SCREENSHOT_READY
  web: WebRequestView[];                 // WEB_REQUEST
  ingest(raw: RawSignal | RawSignal[]): void;   // parse detail once → TypedSignal.data
  reset(): void;
}
interface AgentView { role: string; state: "idle"|"running"|"complete"|"failed"|"blocked"|"waiting";
  card?: string; lastResult?: string; capabilities?: CapabilitiesDetail; spawns: number;
  toolCalls: number; cost: number }
interface CardView { id: string; title: string; owner: string; column: TaskColumn; kind: string;
  size: string; priority: number; round: number; acceptance: string[]; thread: TaskThreadDetail[];
  dependsOn: string[]; since?: string }
```

Rules:
- `ingest` is the only writer. It parses `detail` with `JSON.parse` in a try/catch,
  only for `JSON_DETAIL_TYPES`, and never throws on a malformed signal.
- Derived slices are updated incrementally per signal. They are never recomputed from
  the whole list. This is what keeps 2,000 events smooth.
- Components subscribe with selectors (`useRunStore(s => s.cards[id])`), never to the
  whole state.
- REST goes through TanStack Query hooks in `src/api/queries.ts`, which wrap the
  existing `api.ts` functions. The store holds live state only.

### 3.3 Mapping table (signal → slice)

| Signal | Slice update |
|---|---|
| STARTED / COMPLETE / FAILED / BLOCKED | `agents[role].state`, `spawns` |
| CAPABILITIES | `agents[role].capabilities` |
| TOOL_CALL | `agents[role].toolCalls++`; the role's tool feed (node details sheet) |
| AGENT_RESULT | `agents[role].lastResult`, `costs[role] += cost` |
| TASK_ASSIGNED | new card stub (refetch the card via the query cache) |
| TASK_MOVED | `cards[id].column`, `round`, `since` |
| TASK_THREAD | `cards[id].thread.push` |
| APPROVAL_NEEDED / GRANTED / DENIED | `approvals` |
| OVERSEER_INTERVENTION | `interventions` |
| ARTIFACT / SCREENSHOT_READY | `artifacts` |
| WEB_REQUEST | `web` |
| HUMAN_NEEDED | a banner and the comment box focus |

## 4. Run cockpit layout (U1, S24)

```
┌───────────────────────────────────────────────────────────────────────────┐
│ header: project · run id · status pill · elapsed · stop                   │
├───────────────────────────────────────────────────────────────────────────┤
│ Pipeline graph (React Flow, full width, left → right, height fits nodes)  │
│  each role node: name · state · current card · two-line last result ·     │
│  calls · cost. Review edges carry the round; blocked arrows dashed;       │
│  the overseer is a detached node. Clicking a node (or Enter on it) opens  │
│  a side sheet: capability badge, full last result, and tabs Tool calls    │
│  (virtual) │ Changes (the role's live working diff, §4.1).                │
├───────────────────────────────────────────────────────────────────────────┤
│ Tabs: Board │ Quips │ Overseer │ Cards │ Artifacts  (virtualized feeds)    │
├───────────────────────────────────────────────────────────────────────────┤
│ bottom bar: pending approvals (once/always/reject) · budget meter          │
│ (spent / cap, per-agent breakdown on hover) · always-visible comment box  │
└───────────────────────────────────────────────────────────────────────────┘
```

- The graph takes the full width and sizes its height to its nodes at 1x; the panel
  below has a fixed height and the page scrolls. Below 1024px the panel becomes a drawer.
- The comment box never hides while agents run (CLAUDE.md user-interaction rule).
- Empty states: "Waiting for the first agent…" before any STARTED; the error state
  shows the websocket error and a retry.
- Keyboard: `g` focuses the graph, `c` the comment box, `a` the first approval,
  `⌘K` opens the command palette (U4).
- Accessibility: every colour status also has a text label or icon. Focus is visible.
  Each graph node holds a button labelled with the role and its state.

### 4.1 Seeing code while it is written

- **Changes tab** (agent detail sheet, `features/cockpit/RoleChanges.tsx`): what the role
  has written since it last synced with `main` (each spawn starts with a sync), from
  `GET /api/runs/{run_id}/roles/{role}/changes` → `{role, base, head, files: [{path,
  status: M|A|D|untracked, added, removed}], diff, truncated}`. The route is read-only: it
  never commits or fetches and runs git with `--no-optional-locks`, so polling cannot race
  the runner. Protected paths are left out; untracked text files appear as new-file diffs
  (symlinks, binaries and files over 200 KB are not read); the diff is capped at 200 KB.
- It refreshes after each finished file-changing `TOOL_CALL` of that role (`edit`, `write`,
  `patch`, `multiedit`, `apply_patch`, `bash`), at most once every 2 s, and polls every 10 s
  while the role runs. When the role is not running the view is dimmed: between turns it
  can still show the turn that was just merged.
- **Card drawer**: a card in progress whose owner is running shows the same view as
  "Live changes", above its merged "Diffs".
- Every diff in Studio (card drawer merges, the Work tab's review diff, the run report)
  renders through `DiffText` (`features/report/DiffViewer.tsx`, `react-diff-view`); text
  that is not a unified diff falls back to plain text.

## 5. Folder layout

```
src/styles/tokens.css   src/styles/globals.css (tailwind layers)
src/components/ui/      shadcn-style primitives (button, card, badge, dialog, tabs,
                        tooltip, switch, scroll-area, sheet, dropdown-menu, toast)
src/lib/runEvents.gen.ts (generated)  src/lib/utils.ts (cn)
src/stores/runEvents.ts src/api/queries.ts
src/features/cockpit/   src/features/board/   src/features/artifacts/
src/features/overseer/  src/features/report/  src/features/oneshot/
```

Existing views keep their routes. Each step replaces a view's insides behind its route.
