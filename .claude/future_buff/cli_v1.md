# CLI v1 — Headless Terminal Spin

**Type:** New product surface (terminal-based runner).
**Token budget when picked up:** ~200–400k for a viable v1; ~600k for polished.
**Depends on:** `production_sandbox.md` becomes a hard dep before *distributing* the CLI to anyone other than yourself. For a private CLI you run on your own laptop, dev-mode `code-exec` is acceptable with the existing warning.
**Priority within future_buff:** **lowest.** Captured for completeness; no near-term use case driving it.

## Why this is realistic

The hard work is already done. AgentFlow's engine — `core/runner.py`, `core/scheduler.py`, `agents/`, `core/llm/anthropic.py`, `core/llm/openai.py` — is pure Python with no web dependency. Today the FastAPI layer in `api/` and the React Studio in `studio/` are the only callers, but they're consumers of the engine, not part of it.

A CLI just needs:
1. **Entry point** — `aflow run "make me a todo app"` or `aflow run --goal-file goal.md`. ~150 lines using `click` or stdlib `argparse`.
2. **Live terminal UI** — replaces what BoardFeed/QuipFeed do in the browser. `rich` (already in many users' env) gives colored signal log, per-agent status panels, progress indicators. ~200 lines.
3. **Smart team inference** — the meta-agent already does this in the Studio when you give a freeform goal. The CLI just calls the same code path.
4. **End-of-run summary** — read `data/runs/{run_id}/iteration_metrics.jsonl`, print cost / time / file-changes summary. ~50 lines.
5. **Console-scripts entry in `pyproject.toml`** — `aflow = cli.main:main`. Trivial.

## What gets skipped

- React Studio — not needed.
- WebSocket layer — terminal IS the live view.
- API auth — single-user binary.
- API routes — engine called in-process.
- Persistence to `data/runs/` — could keep for replayability, or store under `~/.aflow/runs/` for cleanliness.

## File sketch

```
cli/
  __init__.py
  main.py              # argparse / click entry, instantiates AgentRunner directly
  terminal_ui.py       # rich-based live signal display
  config_loader.py     # reads goal text, infers team via meta-agent path
  summary.py           # end-of-run cost/time/file-changes summary
pyproject.toml         # entry: console-scripts aflow = cli.main:main
docs/cli.md            # install + first-run walkthrough
tests/cli/
  test_main.py         # argparse parsing, --help text, dry-run mode
  test_terminal_ui.py  # rendering of canned signal sequences (snapshot-style)
```

## Effort breakdown

| Slice | Effort |
|---|---|
| Entry point + argparse + glue | 0.5 day |
| Terminal UI (`rich`-based) | 1 day |
| Team inference reuse + config loader | 0.5 day |
| Summary printer | 0.25 day |
| Tests + docs | 0.5 day |
| **Subtotal — private/personal use** | **~3 days** |
| `production_sandbox.md` (if distributing) | +1–2 days |
| Polish: install experience, persona discovery, multi-LLM-provider preset | +1–2 days |
| **Total — distributable** | **~5–7 days** |

## Why this is the lowest priority in `future_buff`

- The Studio already gives the same workflow with richer UX.
- No user demand for a CLI yet (no distribution channel, no installs to support).
- The sandbox dep means even shipping it requires another track first.
- The dollar value of "make AgentFlow CLI-installable" is low until there's an audience asking for one.

If/when one of these becomes true:
- AgentFlow gets shared with developers who want to try it without spinning up a UI,
- A scripted/automated workflow needs AgentFlow as a CI step,
- Someone wants `aflow review pr/1234` style integrations,

…then this becomes worth the week of work. Until then, it sits at the bottom of the queue.

## What good looks like

```bash
$ pip install agentflow
$ aflow run "build a TODO CLI in Python"
✓ Meta-agent: proposed team (architect, backend_dev, reviewer)
✓ architect: shipped 3 design docs (4.2s, $0.18)
✓ backend_dev: 12 files committed (1m 8s, $0.61)
✓ reviewer: PASS (32s, $0.09)

Total: 1m 50s, $0.88, 14 files in /tmp/aflow_run_xxx
README: /tmp/aflow_run_xxx/README.md
```

That's the bar. The `core/` engine already produces every number in that summary; the CLI is just the surface that frames it.
