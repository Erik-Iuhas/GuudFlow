"""Tests for core.groom (plan §14.6): proposals from the roadmap, never auto-added."""

from __future__ import annotations

from pathlib import Path

from core.backlog import Backlog
from core.groom import parse_proposals, propose_items
from core.llm.client import MockLLMClient, MockTurn, ToolCall


async def test_proposals_use_the_roadmap_and_skip_existing_items(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path, "p")
    backlog.add("Add login", ["works"])
    roadmap = tmp_path / "projects" / "p" / "context" / "roadmap.md"
    roadmap.parent.mkdir(parents=True, exist_ok=True)
    roadmap.write_text("## Next\n- password reset\n- audit log\n")
    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall("1", "propose_items", {"items": [
        {"title": "Password reset by email", "acceptance": ["POST /reset sends a mail", ""],
         "kind": "code", "size": "M", "why": "roadmap: next"},
        {"title": "add login", "acceptance": ["dup"]},                 # already on the backlog
        {"title": "Audit log", "acceptance": ["every login is logged"], "kind": "weird"},
        {"title": "x", "acceptance": []},                              # too short
    ]})])])
    proposals = await propose_items(llm, tmp_path, "p", "A billing API")
    assert [p.title for p in proposals] == ["Password reset by email", "Audit log"]
    assert proposals[0].acceptance == ["POST /reset sends a mail"] and proposals[1].kind == "code"
    sent = llm.calls[0]["messages"][0].content
    assert "password reset" in sent and "Add login" in sent and "A billing API" in sent
    assert [i.title for i in backlog.items()] == ["Add login"]         # nothing added


def test_malformed_proposals_are_dropped(tmp_path: Path) -> None:
    assert parse_proposals({"items": "nope"}, Backlog(tmp_path, "p")) == []
    assert parse_proposals(None, Backlog(tmp_path, "p")) == []
