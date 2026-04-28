"""Tests for agents/tools/desk_push.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest
import pytest_asyncio

from agents.tools.desk_push import DeskPushTool


def _make_tool(tmp_path: Path, sender: str = "backend", project_id: str = "proj-1") -> DeskPushTool:
    desks_root = tmp_path / "persona_desks"
    desks_root.mkdir(parents=True, exist_ok=True)
    return DeskPushTool(
        sender_role=sender,
        project_id=project_id,
        project_desks_root=desks_root,
        workspace=None,
        role_to_persona={"backend": "aria", "frontend": "kai", "reviewer": "rex"},
    )


@pytest.mark.asyncio
async def test_push_creates_file(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "auth-schema",
        "body": "# Auth Schema\nUse JWT tokens.",
        "kind": "design-schema",
        "priority": "high",
        "triggers": ["auth", "jwt"],
        "note": "Follow for all /auth endpoints",
    })
    assert result.get("ok") is True
    dest = Path(result["storage_path"])
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "auth-schema" in content
    assert "Use JWT tokens" in content


@pytest.mark.asyncio
async def test_push_file_has_valid_frontmatter(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.run({
        "target_role": "frontend",
        "doc_name": "api-contract",
        "body": "# API Contract\nDetails here.",
        "triggers": ["api"],
    })
    dest = tmp_path / "persona_desks" / "kai" / "handoffs" / "api-contract.md"
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "---" in content
    assert "handoff_from: backend" in content
    assert "kind:" in content


@pytest.mark.asyncio
async def test_push_resolves_persona_id(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "my-doc",
        "body": "# Doc\nContent.",
        "triggers": ["doc"],
    })
    # Should resolve "frontend" → "kai"
    assert "kai" in result["storage_path"]


@pytest.mark.asyncio
async def test_push_falls_back_to_role_when_no_persona_map(tmp_path):
    desks_root = tmp_path / "persona_desks"
    desks_root.mkdir(parents=True, exist_ok=True)
    tool = DeskPushTool(
        sender_role="backend",
        project_id="proj",
        project_desks_root=desks_root,
        workspace=None,
        role_to_persona={},  # empty map
    )
    result = await tool.run({
        "target_role": "unknown-role",
        "doc_name": "some-doc",
        "body": "# Doc\nContent.",
        "triggers": ["doc"],
    })
    assert result.get("ok") is True
    assert "unknown-role" in result["storage_path"]


@pytest.mark.asyncio
async def test_push_invalid_doc_name_returns_error(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "My Doc Has Spaces!",
        "body": "# Doc",
        "triggers": ["doc"],
    })
    assert result.get("error") == "DOC_NAME_INVALID"


@pytest.mark.asyncio
async def test_push_empty_target_role_returns_error(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "",
        "doc_name": "valid-name",
        "body": "# Doc",
    })
    assert result.get("error") == "TARGET_NOT_FOUND"


@pytest.mark.asyncio
async def test_push_auto_extracts_triggers_from_headings(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "extracted",
        "body": "# Authentication Patterns\n## JWT Handling\nDetails.",
        # No triggers provided — should be auto-extracted from headings
    })
    assert result.get("ok") is True
    triggers = result.get("triggers_set", [])
    assert len(triggers) > 0
    # Should extract something from the headings
    trigger_str = " ".join(triggers)
    assert any(word in trigger_str for word in ["authentication", "jwt", "handling", "patterns"])


@pytest.mark.asyncio
async def test_push_posts_signal_to_workspace(tmp_path):
    from unittest.mock import MagicMock
    workspace = MagicMock()
    workspace.post_signal = MagicMock()

    desks_root = tmp_path / "persona_desks"
    desks_root.mkdir(parents=True, exist_ok=True)
    tool = DeskPushTool(
        sender_role="backend",
        project_id="proj",
        project_desks_root=desks_root,
        workspace=workspace,
        role_to_persona={"frontend": "kai"},
    )
    await tool.run({
        "target_role": "frontend",
        "doc_name": "signal-test",
        "body": "# Doc\nContent.",
        "triggers": ["doc"],
    })
    workspace.post_signal.assert_called_once()
    call_args = workspace.post_signal.call_args[0][0]
    assert call_args.from_agent == "backend"
    assert "DESK_HANDOFF" in call_args.summary


@pytest.mark.asyncio
async def test_push_invalid_kind_defaults_to_reference(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "kind-test",
        "body": "# Doc\nContent.",
        "kind": "totally-invalid-kind",
        "triggers": ["doc"],
    })
    assert result.get("ok") is True
    dest = Path(result["storage_path"])
    content = dest.read_text(encoding="utf-8")
    assert "kind: reference" in content


@pytest.mark.asyncio
async def test_push_overwrites_same_name(tmp_path):
    """Spec §9: two pushes with the same doc_name; second wins on disk."""
    tool = _make_tool(tmp_path)
    await tool.run({
        "target_role": "frontend",
        "doc_name": "auth-spec",
        "body": "First version body.",
        "triggers": ["auth"],
    })
    await tool.run({
        "target_role": "frontend",
        "doc_name": "auth-spec",
        "body": "Second version body — REPLACED.",
        "triggers": ["auth"],
    })
    dest = tmp_path / "persona_desks" / "kai" / "handoffs" / "auth-spec.md"
    content = dest.read_text(encoding="utf-8")
    assert "REPLACED" in content
    assert "First version" not in content


@pytest.mark.asyncio
async def test_high_priority_default_when_unspecified(tmp_path):
    """Spec §4a: pushed file with no explicit priority gets priority=high in frontmatter."""
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "default-prio",
        "body": "# Doc\nContent.",
        "triggers": ["doc"],
        # priority intentionally omitted
    })
    dest = Path(result["storage_path"])
    content = dest.read_text(encoding="utf-8")
    assert "priority: high" in content


@pytest.mark.asyncio
async def test_upward_push_logs_warning(tmp_path, caplog):
    """Spec §4a: backend pushing to architect logs a warning but succeeds."""
    import logging
    desks_root = tmp_path / "persona_desks"
    tool = DeskPushTool(
        sender_role="backend",
        project_id="proj",
        project_desks_root=desks_root,
        workspace=None,
        role_to_persona={"architect": "alex"},
    )
    with caplog.at_level(logging.WARNING):
        result = await tool.run({
            "target_role": "architect",
            "doc_name": "feedback",
            "body": "# Feedback",
            "triggers": ["feedback"],
        })
    assert result.get("ok") is True  # Push succeeds
    # Warning is emitted
    assert any("upward push" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_push_returns_triggers_set(tmp_path):
    tool = _make_tool(tmp_path)
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "trigger-check",
        "body": "# Doc",
        "triggers": ["alpha", "beta", "gamma"],
    })
    assert set(result["triggers_set"]) == {"alpha", "beta", "gamma"}
