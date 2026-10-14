# Changelog

## Unreleased

- Run console: each run keeps GuudFlow's own log lines (`console.jsonl`), explains at the end who
  ran and who never started and why, and returns each role's server log; Studio's run view has a
  Console tab.
- Overseer bar under the run graph with a Wake overseer button, or the reason a run has none.
- Jack: picks his model and effort per project, sees a "Minimum team" line and proposes missing
  roles, parks left-out changes with computed fixes (*Accept the suggestion*), and repair rounds
  no longer repeat his summary. Fixed: host-side calls ignored the configured models and always
  used Anthropic.
- Studio: confirm before a goal-only run skips open backlog items; Ask Jack to change the team.
- Setup: uv for the Python packages, `install_uv.sh`, `doctor.sh`, `stop.sh`, `build.sh`,
  `check.sh`, VS Code tasks (run through bash; on Windows that is WSL).

## 0.1.0

First tagged release. GuudFlow runs a named team of agents on an ongoing project: you plan with
the project manager, approve the team, and accept or reject each finished session.

**Agents and runs**
- opencode harness: one sandboxed server per role, a scheduler, a task board with review loops,
  and an overseer that wakes on stuck detectors. Keys stay on the host behind an LLM gateway.
- Ongoing projects: a long-lived repo and a backlog; a run starts from `main` and reaches it only
  when a person accepts. Continuing a run carries only unfinished items.
- Flow hardening (`docs/FLOW_HARDENING_PLAN.md`): manual checks are the owner's and never block a
  card, a card blocked on a finished role wakes the overseer, a stopped run writes `stopped.json`
  and says why, and workers see the shell commands they may run.

**Planning**
- Jack, the project manager, edits a versioned spec through drafts a person ticks. A turn that
  has a bad change keeps its good ones, repairs only the bad ones, records every call's cost, and
  parks what it cannot fix with a computed fix to accept (`docs/PM_SELF_REPAIR_PLAN.md`).
- Role contracts, a design artifact, a verification ledger, a knowledge base and project kinds.

**Models**
- Providers, models, prices and effort levels are edited in Studio. Host-side calls send effort.

**Studio**
- Team approval, live run graph, review queue with owner checks and the stop reason, plan
  conversation with spend, backlog, settings.

**Known limits**
- Replaying the real session-5 planning turns as fixtures (J7) is not done; unit tests cover each
  failure shape.
- Briefs are the same length for small and large models.
- opencode writes its own refusal text, so a refused shell command is not named by segment.
- The legacy harness is unsupported.
