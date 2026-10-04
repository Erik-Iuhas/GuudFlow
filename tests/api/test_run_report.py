"""Tests for GET /api/runs/{id}/report and GET /api/projects/{id}/{runs-metrics,playbook} (S26)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.playbook import Playbook


def _sig(i: int, kind: str, role: str | None, detail: dict | None = None, summary: str = "") -> dict:
    return {"id": f"s{i}", "type": kind, "summary": summary or kind.lower(), "from_agent": role,
            "detail": json.dumps(detail) if detail is not None else None, "priority": "normal",
            "iteration": 1, "timestamp": f"2026-03-01T10:00:{i:02d}+00:00"}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                   check=True, capture_output=True)


@pytest.fixture
def run_root(data_root: Path) -> Path:
    root = data_root / "runs" / "run_a"
    root.mkdir(parents=True)
    (root / "meta.json").write_text(json.dumps(
        {"run_id": "run_a", "project_id": "p1", "status": "complete",
         "created_at": "2026-03-01T10:00:00+00:00"}))
    _write(root / "board.jsonl", [
        _sig(1, "CAPABILITIES", "dev", {"role_type": "builder", "effective": {"web": "off"}}),
        _sig(2, "CAPABILITIES", "dev", {"role_type": "builder", "effective": {"web": "search"}}),
        _sig(3, "WEB_REQUEST", "dev", {"tool": "web_search", "query": "q", "bytes": 12}),
        _sig(4, "OVERSEER_INTERVENTION", "overseer",
             {"type": "rebrief", "role": "dev", "rationale": "dev kept guessing", "needs_human": False}),
        _sig(5, "OVERSEER_INTERVENTION", "overseer",
             {"type": "pause", "rationale": "budget", "needs_human": True}),
    ])
    _write(root / "llm_costs.jsonl", [
        {"role": "dev", "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 5,
         "images": 1, "cost_usd": 0.5},
        {"role": "dev", "input_tokens": 50, "output_tokens": 10, "cache_read_tokens": 0,
         "images": 0, "cost_usd": 0.25},
        {"role": "reviewer", "input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 0,
         "images": 0, "cost_usd": 0.1},
    ])
    (root / "retrospective.json").write_text(json.dumps(
        {"run_id": "run_a", "status": "complete", "stall_minutes": 3.0,
         "interventions": [{"type": "rebrief", "target": "dev", "detail": {}, "worked": True}],
         "metrics": {}}))
    (root / "carry_forward.json").write_text(json.dumps({"text": "Previous run: run_a"}))
    return root


async def test_report_sections(client, run_root) -> None:
    r = await client.get("/api/runs/run_a/report")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["project_id"] == "p1"
    assert body["signal_count"] == 5
    rows = {c["role"]: c for c in body["costs"]["rows"]}
    assert rows["dev"]["calls"] == 2 and rows["dev"]["input_tokens"] == 150
    assert rows["dev"]["cost_usd"] == 0.75 and rows["dev"]["images"] == 1
    assert body["costs"]["total"]["cost_usd"] == 0.85
    # latest CAPABILITIES per role wins
    assert [c["role"] for c in body["capabilities"]] == ["dev"]
    assert body["capabilities"][0]["effective"] == {"web": "search"}
    assert body["web"][0]["tool"] == "web_search"
    ivs = body["interventions"]
    assert [i["type"] for i in ivs] == ["rebrief", "pause"]
    assert ivs[0]["worked"] is True and ivs[0]["rationale"] == "dev kept guessing"
    assert ivs[1]["worked"] is None and ivs[1]["needs_human"] is True
    assert body["retrospective"]["stall_minutes"] == 3.0
    assert body["carry_forward"] == "Previous run: run_a"
    assert body["branches"] == []


async def test_report_without_retrospective_or_costs(client, data_root) -> None:
    root = data_root / "runs" / "run_live"
    root.mkdir(parents=True)
    r = await client.get("/api/runs/run_live/report")
    assert r.status_code == 200
    body = r.json()
    assert body["retrospective"] is None and body["carry_forward"] is None
    assert body["costs"]["rows"] == [] and body["costs"]["total"]["cost_usd"] == 0
    assert body["interventions"] == [] and body["branches"] == []


async def test_report_branch_commits(client, run_root) -> None:
    repo = run_root / "workspace" / "working"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: seed")
    _git(repo, "checkout", "-b", "agent/dev")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: add b")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "agent/dev", "-m", "merge dev")
    body = (await client.get("/api/runs/run_a/report")).json()
    assert [b["role"] for b in body["branches"]] == ["dev"]
    subjects = [c["subject"] for c in body["branches"][0]["commits"]]
    assert subjects == ["feat: add b", "chore: seed"]
    assert len(body["branches"][0]["commits"][0]["sha"]) == 40


@pytest.mark.parametrize("run_id", ["nope", "..%2Fdata", "%2E%2E", "run_a%2Fworkspace"])
async def test_report_unknown_or_unsafe_run_is_404(client, run_root, run_id) -> None:
    assert (await client.get(f"/api/runs/{run_id}/report")).status_code == 404


async def test_report_refuses_symlinked_run(client, data_root, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "llm_costs.jsonl").write_text(json.dumps({"role": "x", "cost_usd": 9}) + "\n")
    (data_root / "runs" / "evil").symlink_to(outside, target_is_directory=True)
    assert (await client.get("/api/runs/evil/report")).status_code == 404


async def test_runs_metrics_and_playbook(client, data_root) -> None:
    pb = Playbook.load(data_root, "p1")
    pb.record_run_metrics("run_1", {"stall_minutes": 4.0, "interventions": 2.0})
    pb.record_run_metrics("run_2", {"stall_minutes": 1.0, "interventions": 1.0})
    pb.add("known_pitfalls", "dev", "Watch out", "run_1")
    pb.save()
    r = await client.get("/api/projects/p1/runs-metrics")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert [x["run_id"] for x in runs] == ["run_1", "run_2"]
    assert runs[0]["metrics"]["stall_minutes"] == 4.0
    entries = (await client.get("/api/projects/p1/playbook")).json()["entries"]
    assert entries[0]["section"] == "known_pitfalls" and entries[0]["evidence_runs"] == ["run_1"]


async def test_metrics_for_project_without_playbook_is_empty(client) -> None:
    assert (await client.get("/api/projects/none/runs-metrics")).json()["runs"] == []
    assert (await client.get("/api/projects/none/playbook")).json()["entries"] == []


async def test_metrics_path_unsafe_project_is_404(client) -> None:
    assert (await client.get("/api/projects/..%5Cx/runs-metrics")).status_code == 404
