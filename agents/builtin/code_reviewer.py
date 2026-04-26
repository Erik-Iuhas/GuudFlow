"""CodeReviewer — git-diff-aware reviewer.

DESIGN §18, §19. Reads the upstream agent's branch (via git:diff /
git:read), evaluates work against the goal, posts a structured
VERIFY_RESULT, and ends with APPROVED or REJECTED.

For Stage 5 the reviewer drives a generic LLM tool-use loop with a
review-specific system prompt; the loop's tool calls (git:diff,
fs:read, etc.) are mediated by the manifest. The runner sets
`is_reviewer=True` on the AgentConfig so the runner knows to merge
on APPROVED.
"""

from __future__ import annotations

from agents.base import BaseAgent


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


class CodeReviewer(BaseAgent):
    """A reviewer agent. Subclasses `BaseAgent`; relies on the runner's
    generic tool-use loop. The system prompt is the only thing this
    needs over the prompt-defined default."""

    role: str = "code_reviewer"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "code_reviewer"),
            description="Reviews upstream agent work via git diffs and decides APPROVED / REJECTED.",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            **kwargs,
        )
