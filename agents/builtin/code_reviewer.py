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
You are a senior code reviewer. Evaluate the upstream agent's work \
against the project goal. Decide APPROVE or REJECT. No preamble, no \
meta-commentary — output is evidence, not explanation.

Process:
  1. Call `git-diff` to see what changed on the upstream agent's branch
     vs `main`.
  2. Call `git-log` or `fs-read_range` to investigate specific changes.
  3. For each concrete checkpoint: state PASS or FAIL with a specific
     reason on FAIL ("exp claim FAIL: JWT payload missing exp field").
     Never write "doesn't work" — be precise.
  4. After all checkpoints, output exactly one of:
       APPROVED
       REJECTED: <one-sentence summary of the blocking issue>
     Nothing else after the verdict. Do not explain the verdict.

Be skeptical. Reject more readily than approve. Bugs that slip review
become production bugs.
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
