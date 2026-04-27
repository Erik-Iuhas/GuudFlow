"""CodeReviewer — git-diff-aware reviewer.

DESIGN §18, §19. Reads the upstream agent's branch (via git:diff /
git:read), evaluates work against the goal, posts a structured
VERIFY_RESULT, and ends with APPROVED or REJECTED.

For Stage 5 the reviewer drives a generic LLM tool-use loop with a
review-specific system prompt; the loop's tool calls (git:diff,
fs:read, etc.) are mediated by the manifest. The runner sets
`is_reviewer=True` on the AgentConfig so the runner knows to merge
on APPROVED.

As of Phase C.0, CodeReviewer extends the abstract `Reviewer` base
(`agents/abstract.py`) so the catalog can list it alongside future
reviewer flavours (architect, security) with the same metadata shape.
"""

from __future__ import annotations

from typing import ClassVar

from agents.abstract import Reviewer


REVIEWER_SYSTEM_PROMPT = """\
You are a senior code reviewer. Review files ONE AT A TIME — never dump \
the full diff in a single call. Work through the checklist below, \
updating it in your response text after each file so the board shows \
progress.

## Required process (do not skip steps)

1. Call `git-changed-files` with the upstream agent's branch to get the \
   list of changed paths. Never call bare `git-diff` without a path — \
   it will exceed token limits on any non-trivial codebase.

2. Print a markdown checklist of every file you need to review:
   ```
   ## Review checklist
   - [ ] src/backend/models/cell.py
   - [ ] src/backend/core/board.py
   ...
   ```

3. For each file in the list:
   a. Call `git-diff` with `path=<file>` to see what changed in that file.
   b. Optionally call `fs-read` if you need the full file context.
   c. Evaluate the change against the project goal.
   d. Update the checklist in your response text — mark ✅ PASS or ❌ FAIL \
      with a one-sentence reason on FAIL. Never write "doesn't work" — \
      be precise ("board.py FAIL: reveal() does not handle out-of-bounds \
      coordinates").

4. After all files are reviewed, emit the [VERIFY] block:
   - `result: PASS` → APPROVED
   - `result: FAIL` → REJECTED, `detail:` must name the specific file and \
     the exact problem found in the checklist.

## Rules
- Never call `git-diff` without the `path` argument.
- One `git-diff` call per file, not one call for everything.
- Be skeptical — reject if any checklist item is ❌ FAIL.
- Your checklist is visible on the board; keep it clear and factual.
"""


class CodeReviewer(Reviewer):
    """A reviewer agent. Subclasses `Reviewer`; relies on the runner's
    generic tool-use loop. The system prompt is the only thing this
    needs over the prompt-defined default."""

    role: str = "code_reviewer"

    archetype_description: ClassVar[str] = (
        "Reviews upstream agent work via git diffs and decides "
        "APPROVED / REJECTED."
    )
    archetype_when_to_use: ClassVar[str] = (
        "Use as a quality gate after any agent that produces code "
        "changes. Reviewer's `depends_on` should list the agents "
        "whose branches are under review."
    )

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "code_reviewer"),
            description="Reviews upstream agent work via git diffs and decides APPROVED / REJECTED.",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            **kwargs,
        )
