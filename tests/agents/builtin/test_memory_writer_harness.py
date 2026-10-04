"""memory_writer on opencode harness runs (S13): episodes come from board + cards."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agents.builtin.memory_writer import MemoryWriter
from agents.persona import Persona
from core.llm import LLMResponse
from core.workspace import Workspace
from tests.agents.builtin.harness_run_fixture import reworked_run


def _persona(tmp_path: Path, persona_id: str) -> tuple[Persona, Path]:
    d = tmp_path / "personas" / persona_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(
        json.dumps({"persona_id": persona_id, "name": persona_id, "specialty": "x"}),
        encoding="utf-8",
    )
    p = Persona.load(persona_id, tmp_path / "personas")
    p._dir = d
    return p, d


def _episodes(d: Path) -> list[dict]:
    return [json.loads(line) for line in (d / "episodes.jsonl").read_text().splitlines()]


async def _run_writer(tmp_path: Path, llm=None, harness: str = "opencode"):
    run_root = reworked_run(tmp_path / "run")
    personas = {r: _persona(tmp_path, f"p_{r}") for r in ("backend", "reviewer", "planner")}
    ws = Workspace(run_id="r1", goal="Build a status page")
    project = SimpleNamespace(harness=harness, mode="production", project_id="proj")
    await MemoryWriter(llm=llm).on_run_complete(
        workspace=ws, project=project, run_id="r1", run_root=run_root,
        personas={r: p for r, (p, _) in personas.items()},
    )
    return {r: d for r, (_, d) in personas.items()}


async def test_one_episode_per_role_with_outcome(tmp_path: Path):
    dirs = await _run_writer(tmp_path)
    backend = _episodes(dirs["backend"])
    assert len(backend) == 1
    ep = backend[0]
    assert "passed_after_rework" in ep["situation"]
    assert "outcome-passed-after-rework" in ep["tags"]
    assert ep["model_used"] == "fake-model"
    assert ep["iterations_to_resolve"] == 3
    assert ep["run_id"] == "r1"
    reviewer = _episodes(dirs["reviewer"])
    assert len(reviewer) == 1 and "completed" in reviewer[0]["situation"]
    assert len(_episodes(dirs["planner"])) == 1
    hist = json.loads((dirs["backend"] / "history.json").read_text())["entries"]
    assert hist[0]["outcome"] == "approved"
    assert hist[0]["role"] == "backend"


async def test_recurring_issue_becomes_evidence_backed_lesson(tmp_path: Path):
    dirs = await _run_writer(tmp_path)
    ep = _episodes(dirs["backend"])[0]
    assert "recurred on card T1 in rounds 1, 2" in ep["what_failed"]
    assert "status() returns None" in ep["what_failed"]
    assert "status() returns None" in ep["feedback_verbatim"]
    assert "review-recurrence" in ep["tags"]
    assert "Reinforced: Card T2" in ep["what_worked"]
    # No evidence, no lesson: the reviewer was never sent anything back.
    rev = _episodes(dirs["reviewer"])[0]
    assert rev["what_failed"] == "" and rev["feedback_verbatim"] == ""


class _CapturingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, system, messages, tools=None, max_tokens=1024, **kw):
        self.prompts.append(messages[0].content)
        return LLMResponse(text="", tool_calls=[], stop_reason="end_turn")


async def test_distill_prompt_wraps_transcript(tmp_path: Path):
    p, d = _persona(tmp_path, "p_backend")
    # Seed two similar older episodes so a cluster forms with the new one.
    from agents.persona import Episode
    for i in range(2):
        p.episodes.append(Episode(
            episode_id=f"old{i}", task_context="status page", what_i_tried="x",
            tags=["status", "page", "backend", "build"], timestamp="2026-01-01T00:00:00+00:00"))
    run_root = reworked_run(tmp_path / "run")
    llm = _CapturingLLM()
    ws = Workspace(run_id="r1", goal="Build status page backend")
    project = SimpleNamespace(harness="opencode", mode="production", project_id="proj")
    await MemoryWriter(llm=llm).on_run_complete(
        workspace=ws, project=project, run_id="r1", run_root=run_root,
        personas={"backend": p},
    )
    assert llm.prompts, "distillation should have been attempted"
    assert "<untrusted-" in llm.prompts[0]
    assert "harness-transcript:backend" in llm.prompts[0]


async def test_legacy_project_ignores_board_files(tmp_path: Path):
    dirs = await _run_writer(tmp_path, harness="legacy")
    ep = _episodes(dirs["backend"])[0]
    assert ep["situation"] == "Acted as backend"
    assert "outcome-" not in " ".join(ep["tags"])


async def test_no_project_harness_flag_detects_by_agent_result(tmp_path: Path):
    run_root = reworked_run(tmp_path / "run")
    p, d = _persona(tmp_path, "p_backend")
    await MemoryWriter().on_run_complete(
        workspace=Workspace(run_id="r1", goal="Build"), project=None, run_id="r1",
        run_root=run_root, personas={"backend": p})
    assert "passed_after_rework" in _episodes(d)[0]["situation"]
