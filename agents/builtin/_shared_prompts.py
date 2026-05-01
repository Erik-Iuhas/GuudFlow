"""Shared prompt fragments injected into multiple builtin agent prompts."""

NO_OP_REASON_ADDENDUM = """\
NO-OP COMPLETION RULE: If your review finds nothing to change and you make \
no commits, you MUST include a `no_op_reason:` line immediately before your \
[VERIFY] block. The reason must be specific (≥ 10 characters):

  ✅  no_op_reason: all edge cases already tested; no schema drift between branches
  ❌  no_op_reason: looks fine
  ❌  no_op_reason: nothing to do

Without this, your completion is rejected as a protocol violation.\
"""

DELEGATION_TILT = """\
PREFER OWN WORK OVER DELEGATION. Default: do the work yourself with \
fs-write. Only assign-task when ALL three are true:
  (a) the work is clearly outside your role's responsibility,
  (b) the assignee genuinely exists on the team and matches the skill,
  (c) the work is large enough that delegating saves more tokens than
      it costs in coordination.
If in doubt, write it yourself. The reviewer catches mistakes; the \
reviewer can't catch tasks that never get done.\
"""
