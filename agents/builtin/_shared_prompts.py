"""Shared prompt fragments injected into multiple builtin agent prompts."""

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
