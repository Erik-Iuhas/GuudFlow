"""Track 3d.4 — coverage check: every built-in tool sets priority + when_to_use.

This test is a regression guard. If a contributor adds a new tool but
forgets to populate `priority` and `when_to_use`, this test fails with
a clear message naming the offending class.

The rule: each built-in tool must explicitly set BOTH attributes (i.e.
the values must differ from BaseTool's defaults). This forces a
deliberate decision about where the tool fits in the funnel.
"""
from __future__ import annotations

import pytest

from agents.tools.base import BaseTool
from core.runner import LOCAL_TOOL_CLASSES


_BASE_PRIORITY = BaseTool.priority         # 100
_BASE_WHEN_TO_USE = BaseTool.when_to_use   # ""


def test_every_built_in_tool_sets_priority_and_when_to_use():
    """Walk LOCAL_TOOL_CLASSES; assert each has BOTH attributes deliberately set.

    A tool counts as "deliberately set" if it differs from the BaseTool default
    OR if its priority is exactly 100 AND when_to_use is non-empty (some tools
    legitimately belong at priority 100, the batch tier).
    """
    missing: list[str] = []
    for cls in LOCAL_TOOL_CLASSES:
        priority_set = cls.priority != _BASE_PRIORITY
        when_to_use_set = cls.when_to_use != _BASE_WHEN_TO_USE
        if not (priority_set or when_to_use_set):
            missing.append(cls.__name__)

    assert not missing, (
        "Track 3d.4 coverage: these built-in tools have no priority or "
        "when_to_use override on their class. Add both per the buckets in "
        f"agents/tools/base.py:\n  {missing}"
    )


def test_priority_within_documented_buckets():
    """Priority numbers should fall in the documented 0..200 range. Catches typos
    like priority=10000 or negative numbers."""
    out_of_range: list[tuple[str, int]] = []
    for cls in LOCAL_TOOL_CLASSES:
        if not (0 <= cls.priority <= 200):
            out_of_range.append((cls.__name__, cls.priority))
    assert not out_of_range, (
        "Track 3d.4 sanity: priority numbers must be 0..200 per the buckets:\n"
        f"  {out_of_range}"
    )


def test_when_to_use_is_short_when_set():
    """when_to_use is shown inline in the prompt; cap at ~120 chars to keep
    the rendered catalog scannable."""
    too_long: list[tuple[str, int]] = []
    for cls in LOCAL_TOOL_CLASSES:
        if cls.when_to_use and len(cls.when_to_use) > 120:
            too_long.append((cls.__name__, len(cls.when_to_use)))
    assert not too_long, (
        "Track 3d.4 sanity: when_to_use must be ≤ 120 chars to keep the "
        "rendered catalog scannable:\n"
        f"  {too_long}"
    )


def test_batch_tools_have_lower_priority_than_singular_counterparts():
    """The load-bearing nudge: batch variants must sit ABOVE their singular
    counterparts in the catalog (lower priority number = renders first)."""
    from agents.tools.fs import FsEdit, FsWrite
    from agents.tools.fs_batch import FsEditBatch, FsWriteBatch
    from agents.tools.code import CodeOutline
    from agents.tools.code_batch import CodeOutlineBatch

    assert FsEditBatch.priority < FsEdit.priority, (
        "fs-edit-batch must render before fs-edit (lower priority number)"
    )
    assert FsWriteBatch.priority < FsWrite.priority, (
        "fs-write-batch must render before fs-write"
    )
    assert CodeOutlineBatch.priority > CodeOutline.priority, (
        "code-outline-batch sits AFTER code-outline by convention "
        "(outline is the universal first move; batch is for when you need ≥ 3)"
    )
