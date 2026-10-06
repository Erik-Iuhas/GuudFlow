# Plain-language intake: the project manager agent

**Status:** proposal, written 2026-10-06. Nothing here is built yet.
**Goal:** a person who is not a developer can run a project by writing what they want in plain
words. A heavy-tier **project manager** agent turns that into proper backlog items: titles,
acceptance checks, size, order, dependencies and links. It asks a question only when it
genuinely cannot proceed. People who do want to write precise tickets still can. Their detail is
kept, and the manager fills in only what is missing.

---

## 1. Where the friction is today

| Where | What we ask the user for | Who should really decide it |
|---|---|---|
| **New project** (Propose page) | Meta-agent model, goal, agent harness (`legacy` is still offered although it is refused at run time), graph/list view, capability switches per role, a project id (slug) | The goal is the user's. The rest is either a default, or the meta-agent's to decide and show, with capabilities only shown when they change risk |
| **Team tab** | Max iterations, PR mode, "Sub-LLM models" (a legacy-harness feature), the agent graph | Defaults. Only the team itself is worth showing |
| **Backlog form** | Title, "Done when" (one check per line), kind, size, "After (items)" by id | The user's words. Checks, kind, size and order are the manager's to propose |
| **Roadmap tab** | A free-form markdown document, which grooming needs | The manager drafts and maintains it. The human edits it if they want |
| **Settings → Project work** | A check command (`python3 -m unittest …`), a remote URL and token variable, autopilot hours and dollar limits | The check command can be detected from the repo. Autopilot can be a choice of three presets. The remote stays a human setting |
| **Run review** | A diff | A plain-language "what changed and how to try it" first, with the diff one click away |

Grooming (`core/groom.py`) already proposes items from the roadmap, but it only invents *new*
work. It never tidies what the user typed, and its proposals have to be added one by one.

## 2. The idea in one picture

```
User writes:   "the ghosts are too fast on level 1 and I want a pause button"
      │
      ▼  Request R7 (verbatim, human text), saved instantly, never lost
Project manager (host-side, heavy tier, one structured call)
      │   reads: goal, roadmap, open backlog, last run summaries, repo outline
      ▼
IntakeResult:  split into 2 items
  B12 "Slow ghosts on early levels"   size S  checks: [...]  related: B5
  B13 "Pause with P / Esc"            size S  checks: [...]
  roadmap note: "difficulty curve" under Milestone 2
      │
      ▼  applied to the backlog, shown as "refined from your request"; edit or undo
```

## 3. Architecture

### 3.1 Requests: a new inbox

A **request** is anything a human writes to the project: a sentence, a paragraph, a pasted bug
report, or a screenshot with a comment. It is stored verbatim and is never overwritten. Fields:

- `id` (`R1`, `R2`…), `text`, `attachments`, `author`, `created_at`;
- `status`: `new` → `refining` → `done` or `needs_answer`;
- `items`: the backlog items it produced or touched;
- `questions` and `answers`;
- `source`: `human`, `run:<id>` (follow-ups a run reported), or `github:<issue>` (§3.6).

It is stored in the project's SQLite work database next to the backlog (`core/work_db.py`), in
a new `core/intake.py`.

### 3.2 The project manager agent

It runs host-side like the meta-agent and grooming, with `LLMClient` and one tool call. It uses
the heavy tier by default, and its model can be picked in Settings like any other. It is a named
worker (`core/crew.py`), so the user meets "Mara, project manager" and the rest of the team can
refer to it by name. It is **not** the overseer: the overseer works inside one run with a fixed
intervention menu, and the manager works across the whole project, between runs.

**What it reads:**
- the project goal and roadmap;
- the open and recently done backlog (ids, titles, checks);
- the requests in this batch, plus any earlier answers;
- the last few run summaries;
- a compact outline of the project repo (the existing host-side `core/repo_map.py`), so it can
  name real files.

Anything that did not come from a human of this project is wrapped as untrusted: run summaries,
GitHub issue bodies and file contents.

**What it returns:** a validated `IntakeResult` (Pydantic, in the style of `core/harness/results.py`):

```python
class ItemDraft(BaseModel):
    title: str                      # short, imperative
    acceptance: list[str]           # 1-6 checks, each testable by a reviewer
    kind: Kind; size: Literal["S", "M", "L"]
    depends_on: list[str] = []      # existing item ids or "new:<n>" for drafts in this result
    related: list[str] = []         # ids it touches but does not wait for
    files_hint: list[str] = []
    notes: str = ""                 # plain-language context the planner should see

class RequestOutcome(BaseModel):
    request_id: str
    action: Literal["create", "extend", "duplicate", "question", "not_work"]
    drafts: list[ItemDraft] = []    # create: one or more (a split)
    extend: dict[str, list[str]] = {}  # extend: item id -> extra acceptance checks
    duplicate_of: str | None = None
    question: str | None = None     # only when it cannot proceed; plain language
    summary: str                    # one line shown to the user: what it did and why

class IntakeResult(BaseModel):
    outcomes: list[RequestOutcome]
    roadmap_note: str | None = None # a suggested roadmap addition (never applied silently)
```

**Validation:**
- unknown ids are refused;
- dependency cycles are refused, using the same check as `_check_deps` in `core/backlog.py`;
- each request may produce at most N drafts;
- a `question` must say what it blocks;
- if the request already has a title and checks, the user's text is kept word for word and the
  manager only fills the missing fields (§3.4).

An invalid result gets one retry with the error, the same rule as agent turns.

**When it runs:**
- when a request is submitted (batched over a few seconds, so a burst of requests is one call);
- when a question is answered;
- before a run starts, as a cheap "anything stale?" pass over the items being taken;
- after a run, to turn the run's reported leftovers into requests marked `run:<id>`.

### 3.3 What is applied automatically and what waits for a human

This is the important policy line. It extends how grooming works today rather than reversing it:

| Change | Applied automatically? | Why |
|---|---|---|
| Refining a **human's** request into items (create, split, extend, link, size, order) | **Yes**. Shown as "refined from R7", with the original text one click away, edit in place, and undo | It carries out what the human asked for; it does not decide what to do |
| `duplicate` or `not_work` | Yes, but the request stays visible with the reason, and one click turns it into an item anyway | Nothing is lost |
| Work the manager **invents** (grooming, roadmap ideas) | **No.** It lands as "suggested", with one click to accept all or each one | Same rule as today: new work is never added on the model's say-so |
| Requests from `run:` or `github:` sources | Refined, but held as "suggested" until a human accepts them (or an autopilot setting allows it, §3.6) | The text is not from a trusted human |
| Roadmap changes | Suggested only | The roadmap is the humans' document |

### 3.4 Technical users lose nothing

- A request written as a ticket (a title line followed by bullet points) is detected. The bullets
  become acceptance checks word for word, and the manager only adds what is missing: kind, size,
  links and dependencies.
- The detailed form stays, collapsed under **Details** on each item. Every field can still be
  edited, and an edit made by a human is pinned: the manager never rewrites a field a human set.
- The API keeps `POST /backlog` for exact items. CI scripts are unaffected. `POST /requests` is
  the new plain-language route.

### 3.5 Without a model key, or when the model fails

A request still becomes an item straight away. The first line becomes the title, the rest becomes
the notes, and the item is marked "not refined". The manager refines it on its next successful
call. Intake never blocks the human, and an LLM error is shown but never crashes anything. The
run's planner already copes with thin items, so an unrefined item can still be run.

### 3.6 Linking to issues

- **Inside GuudFlow:** `related` and `depends_on` link items to each other. `duplicate_of` links a
  request to the item that already covers it. The Work tab shows the links as chips.
- **GitHub issues (optional, later phase):** when the project has a GitHub remote (plan §14, H1), the
  manager can import open issues as requests with `source: github:<n>`, using the host-only token
  the remote already uses. Each item keeps a link to its issue, and an accepted run comments on
  the issue or closes it. Issue text is untrusted (anyone can file one), so imported requests wait
  for a human as described in §3.3. A project setting can trust issues from listed GitHub users
  so that autopilot can take them.

### 3.7 Cost and limits

- One call per batch of requests. The cost is logged like grooming's and shown on the request.
- A per-project daily cap on manager spend, with a sensible default, sits next to the autopilot
  limits.
- The manager sees at most around 60 backlog items and a capped repo outline. Large projects use
  the most relevant items, ranked by keyword overlap first.

## 4. Simplifying the rest of setup

These need no new agent and can ship first:

1. **New project = one box.** "What do you want to build?", plus optional files or a repo link.
   - The project id is derived from the goal.
   - The meta-agent model, the harness selector and the graph/list toggle are removed (opencode
     is the only harness that runs), or moved under **More options**.
   - Capability switches move under **Review permissions**. They are expanded only when a role
     asks for something risky (web access, shell, packages).
   - In the same step, the manager drafts a short roadmap and the first 3–5 backlog items from the
     goal and inputs, so the new project opens with work ready to start.
2. **Team tab.** Remove "Sub-LLM models" (legacy harness only). Move max iterations and PR mode
   under **Advanced**. Keep the team graph and the cost estimate.
3. **Backlog.** One textarea: "Ask for something". Today's fields move under **Details** on each
   item (§3.4). Item ids stay visible, but nobody has to type them.
4. **Checks before review.** Detect the check command from the repo (`package.json` test script,
   `pyproject`/`tests/`, `bun test`, `go test`, `cargo test`) and pre-fill it as a suggestion.
5. **Autopilot presets.** Off, Gentle (one item a day, $2 per run) or Busy (two items every six
   hours, $5 per run). Custom numbers go under **Custom**.
6. **Run review in plain words.** "What changed", "How to try it" and "What was not done", built
   from the run's results and the item checks, above the diff. Non-technical users accept or
   reject from that summary.

## 5. Phases

| Phase | What | Notes |
|---|---|---|
| **I1** | §4.1–§4.5 setup quick wins | Frontend plus small API defaults. No new model calls except detection |
| **I2** | `core/intake.py`: requests store, `IntakeResult` schema, validator, apply and undo, fallback; API routes `POST/GET /requests`, answer, undo | Tests with a mocked LLM: split, extend, duplicate, question, cycle refused, fallback without a key, human-pinned fields never rewritten |
| **I3** | Project manager agent: prompt, named worker, triggers (submit, answer, before run, after run), cost cap | Untrusted wrapping for `run:` text. Board signals `INTAKE_*` for the record |
| **I4** | Studio: "Ask for something" box, request cards (refining / done / needs your answer), "refined from R7" chips, undo, suggested items tray | Vitest plus a visual baseline |
| **I5** | New project in one step: goal → manager drafts roadmap and first items + meta-agent team, one approval screen | Reuses I2–I4 |
| **I6** | Plain-language run review (§4.6) | |
| **I7** | GitHub issue import and links (§3.6) | Optional; needs the remote |

I1 can start immediately. I2–I4 are the core of the change.

## 6. Decisions to confirm

1. **Auto-apply refinements of human requests** (§3.3), while invented work stays "suggested".
   This is the recommendation. The alternative is to have every draft approved, which brings back
   the friction this plan removes.
2. **"Issues" means backlog items first, GitHub issues later** (I7). If GitHub issues are the main
   source for your projects, I7 moves up.
3. **The manager is a separate role from the overseer** (project-wide versus one run). They could
   share a persona so the same name shows up in both places.
