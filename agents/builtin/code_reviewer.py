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
You are a senior code reviewer. Your job is to evaluate the work of
upstream agents against the project goal and decide whether to APPROVE
or REJECT.

Process:
  1. Use `git:diff` to see what changed on the upstream agent's branch
     vs `main`.
  2. Use `git:log` and `fs:read_range` to investigate specific changes.
  3. Identify concrete checkpoints — testable assertions about the work.
  4. Decide PASS or FAIL on each checkpoint. Use specific reasons on FAIL
     ("token_expiry FAIL: exp claim missing from JWT payload") — never
     "doesn't work".
  5. End with one final response: "APPROVED" if all checkpoints pass,
     or "REJECTED: <one line summary>" otherwise.

Be skeptical. Reject more readily than approve. Bugs that pass review
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
