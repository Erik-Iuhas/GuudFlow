"""Tests for the MetaAgent (Phase D.1).

Covers:
  - The proposal flow end-to-end with a MockLLMClient (greenfield)
  - Falls back to the deterministic skeleton on LLM error
  - Falls back when the LLM returns text instead of a tool call
  - Falls back when the LLM picks an unexpected tool
  - Reads project inputs (uploads, source_repo) into the user prompt
  - Honors persona hire recommendations
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.builtin import register_builtins
from agents.builtin.meta import MetaAgent, TeamProposal
from core.llm import MockLLMClient, MockTurn, ToolCall


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


def _project_with_inputs(tmp_path: Path, project_id: str = "p1") -> Path:
    """Create a per-project storage layout with uploads + source_repo
    metadata, mimicking what D-prime sets up."""
    project_dir = tmp_path / "data" / "projects" / project_id
    inputs = project_dir / "inputs"
    (inputs / "uploads").mkdir(parents=True)
    (inputs / "uploads" / "spec.md").write_text(
        "# Auth spec\n\nAdd refresh tokens to the existing OAuth flow.\n",
        encoding="utf-8",
    )
    (inputs / "source_repo").mkdir()
    (inputs / "source_repo" / "main.py").write_text("print('seed')\n", encoding="utf-8")
    (inputs / "source_repo.meta.json").write_text(
        json.dumps({
            "kind": "git_url",
            "git_url": "https://example.com/repo.git",
            "branch": "main",
        }),
        encoding="utf-8",
    )
    return project_dir


def _propose_call(args: dict[str, Any]) -> MockTurn:
    """Helper: build a MockTurn that emits a `propose_team` tool call
    with the given arguments."""
    return MockTurn(tool_calls=[ToolCall(
        id="meta_t1",
        name="propose_team",
        arguments=args,
    )])


# ── Greenfield happy path ─────────────────────────────────────────────────


async def test_greenfield_proposal_with_two_agents(tmp_path: Path):
    project_dir = _project_with_inputs(tmp_path)

    llm = MockLLMClient([_propose_call({
        "rationale": "Spec asks for refresh tokens; need a backend dev to ship the change and a code_reviewer to gate it.",
        "agents": [
            {
                "role": "auth_dev",
                "archetype": "backend_dev",
                "description": "Implements the refresh token flow.",
                "depends_on": [],
                "model_tier": "solid",
                "is_reviewer": False,
                "is_final_reviewer": False,
                "persona": {
                    "action": "create_new",
                    "name_hint": "Mike",
                    "rationale": "New persona; no prior fit in roster.",
                },
            },
            {
                "role": "auth_reviewer",
                "archetype": "code_reviewer",
                "description": "Reviews the auth changes.",
                "depends_on": ["auth_dev"],
                "model_tier": "solid",
                "is_reviewer": True,
                "is_final_reviewer": False,
                "persona": {"action": "create_new"},
            },
        ],
    })])

    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir,
        project_id="p1",
        goal="Add refresh tokens",
        roster=[],
    )

    assert isinstance(proposal, TeamProposal)
    assert "refresh tokens" in proposal.rationale.lower()
    assert [a.role for a in proposal.roles] == ["auth_dev", "auth_reviewer"]

    # auth_dev is a backend_dev (described-only archetype) → no
    # registered class, system_prompt comes from the YAML, tools
    # default from the catalog entry.
    auth_dev = proposal.roles[0]
    assert auth_dev.agent_class_role_type is None
    assert "fs:write" in auth_dev.declared_local_tools
    assert "code:exec" in auth_dev.declared_local_tools
    assert auth_dev.system_prompt  # populated from YAML
    assert "backend developer" in auth_dev.system_prompt.lower()

    # auth_reviewer is a code_reviewer (shipped) → uses the registered
    # class; no system_prompt needed (the class provides it).
    reviewer = proposal.roles[1]
    assert reviewer.agent_class_role_type == "code_reviewer"
    assert reviewer.is_reviewer is True

    # RBAC scopes propagate from the archetype defaults.
    assert "fs:write" in proposal.rbac["auth_dev"].allowed_scopes
    assert proposal.rbac["auth_reviewer"].allowed_scopes == []  # reviewers don't write

    # Persona hires: both create_new with curated names.
    by_role = {h.role: h for h in proposal.persona_hires}
    assert by_role["auth_dev"].action == "create_new"
    assert by_role["auth_dev"].display_name == "Mike"
    assert by_role["auth_dev"].persona_id == "mike-auth_dev-v1"
    # auth_reviewer didn't pass a name_hint → curated list, first
    # available name (mike already used at auth_dev → sarah).
    assert by_role["auth_reviewer"].display_name == "Sarah"


async def test_proposal_dedupes_role_collisions(tmp_path: Path):
    """If the LLM emits two agents with the same role, the parser
    auto-appends `_2`."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({
        "rationale": "Two devs.",
        "agents": [
            {
                "role": "developer",
                "archetype": "backend_dev",
                "depends_on": [],
            },
            {
                "role": "developer",  # collision
                "archetype": "frontend_dev",
                "depends_on": [],
            },
        ],
    })])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "developer_2"]
    assert any("collision" in w.lower() for w in proposal.warnings)


async def test_proposal_hires_existing_persona(tmp_path: Path):
    """When the LLM picks `hire_existing` with a known persona_id,
    the AgentConfig's persona_id is set to that id."""
    project_dir = _project_with_inputs(tmp_path)
    roster = [
        {
            "persona_id": "aria-frontend-v1",
            "name": "Aria",
            "specialty": "react",
            "reputation": {"production": {"total_runs": 12}},
        },
    ]
    llm = MockLLMClient([_propose_call({
        "rationale": "Re-hire Aria for the UI work.",
        "agents": [
            {
                "role": "ui_dev",
                "archetype": "frontend_dev",
                "depends_on": [],
                "persona": {
                    "action": "hire_existing",
                    "persona_id": "aria-frontend-v1",
                    "rationale": "Aria's React track record fits.",
                },
            },
        ],
    })])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1",
        goal="UI redesign", roster=roster,
    )
    assert proposal.roles[0].persona_id == "aria-frontend-v1"
    hire = proposal.persona_hires[0]
    assert hire.action == "hire_existing"
    assert hire.persona_id == "aria-frontend-v1"
    assert hire.display_name == "Aria"


async def test_proposal_hire_existing_unknown_falls_back_to_create(tmp_path: Path):
    """An LLM that requests hire_existing for a persona NOT in the
    roster gets sanitised to create_new with a warning."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({
        "rationale": "x",
        "agents": [{
            "role": "dev",
            "archetype": "backend_dev",
            "depends_on": [],
            "persona": {
                "action": "hire_existing",
                "persona_id": "ghost-v99",
            },
        }],
    })])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    hire = proposal.persona_hires[0]
    assert hire.action == "create_new"
    assert hire.persona_id != "ghost-v99"
    assert any("unknown persona" in w.lower() for w in proposal.warnings)


# ── Fallback paths ────────────────────────────────────────────────────────


async def test_fallback_when_llm_raises(tmp_path: Path):
    """LLM exception → deterministic skeleton."""
    project_dir = _project_with_inputs(tmp_path)

    class ExplodingLLM:
        async def complete(self, **kwargs):
            raise RuntimeError("anthropic 503")

    meta = MetaAgent()
    meta._llm = ExplodingLLM()
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "reviewer"]
    assert "anthropic 503" in proposal.rationale or any(
        "anthropic 503" in w for w in proposal.warnings
    )


async def test_fallback_when_no_tool_call(tmp_path: Path):
    """LLM returns plain text → skeleton."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([MockTurn(text="Sure, here's a team...")])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "reviewer"]


async def test_fallback_when_unexpected_tool(tmp_path: Path):
    """LLM calls a different tool by mistake → skeleton."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="x", name="search_web", arguments={"q": "?"},
    )])])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "reviewer"]


async def test_fallback_when_proposal_has_no_agents(tmp_path: Path):
    """Empty agents array → skeleton."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({"rationale": "x", "agents": []})])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "reviewer"]


async def test_fallback_when_no_llm_set(tmp_path: Path):
    """No _llm injected → skeleton without raising."""
    project_dir = _project_with_inputs(tmp_path)
    meta = MetaAgent()
    # _llm intentionally not set
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert [a.role for a in proposal.roles] == ["developer", "reviewer"]


# ── Input rendering ──────────────────────────────────────────────────────


async def test_proposal_user_message_includes_inputs(tmp_path: Path):
    """The LLM call's user message references the source repo URL +
    upload filenames so the LLM can incorporate them."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({
        "rationale": "x",
        "agents": [{
            "role": "dev", "archetype": "backend_dev", "depends_on": [],
        }],
    })])
    meta = MetaAgent()
    meta._llm = llm
    await meta.propose(
        project_dir=project_dir, project_id="p1",
        goal="Add refresh tokens", roster=[],
    )
    user_msg = llm.calls[0]["messages"][0].content
    assert "Add refresh tokens" in user_msg
    assert "spec.md" in user_msg
    assert "https://example.com/repo.git" in user_msg
    # Catalog passed in.
    assert "code_reviewer" in user_msg
    assert "tech_lead" in user_msg
    # Persona roster section present (empty in this test).
    assert "Persona roster" in user_msg or "persona" in user_msg.lower()


async def test_proposal_includes_roster_when_personas_exist(tmp_path: Path):
    """The persona roster gets rendered into the user message when
    one is provided."""
    project_dir = _project_with_inputs(tmp_path)
    roster = [
        {
            "persona_id": "mike-frontend-v1",
            "name": "Mike",
            "specialty": "react",
            "reputation": {"production": {"total_runs": 5}},
        },
    ]
    llm = MockLLMClient([_propose_call({
        "rationale": "x",
        "agents": [{"role": "dev", "archetype": "backend_dev", "depends_on": []}],
    })])
    meta = MetaAgent()
    meta._llm = llm
    await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=roster,
    )
    user_msg = llm.calls[0]["messages"][0].content
    assert "mike-frontend-v1" in user_msg
    assert "Mike" in user_msg
    assert "5 prior runs" in user_msg


# ── Serialisation ────────────────────────────────────────────────────────


# ── Continuation deltas (Phase D.3) ──────────────────────────────────────


async def test_continuation_proposal_computes_deltas_keep_add_drop(tmp_path: Path):
    """Prior team had old_dev + old_reviewer; new proposal keeps
    old_dev, drops old_reviewer, adds frontend_dev. Expect three
    deltas: keep / drop / add."""
    project_dir = _project_with_inputs(tmp_path, "p_cont")
    # Stage a from_run_*.meta.json so the meta-agent treats this as
    # a continuation context.
    inputs = project_dir / "inputs"
    (inputs / "from_run_prev.meta.json").write_text(json.dumps({
        "kind": "prior_run", "run_id": "prev",
        "status": "complete", "completed_agents": ["old_dev", "old_reviewer"],
        "has_feedback": False,
    }), encoding="utf-8")

    prior_team = [
        {"role": "old_dev", "model_tier": "solid"},
        {"role": "old_reviewer", "model_tier": "solid", "is_reviewer": True},
    ]

    llm = MockLLMClient([_propose_call({
        "rationale": "Keep old_dev, swap reviewer for ui work.",
        "agents": [
            {"role": "old_dev", "archetype": "backend_dev",
             "depends_on": [], "model_tier": "solid"},
            {"role": "frontend_ui", "archetype": "frontend_dev",
             "depends_on": ["old_dev"], "model_tier": "solid"},
        ],
    })])
    meta = MetaAgent()
    meta._llm = llm

    proposal = await meta.propose(
        project_dir=project_dir, project_id="p_cont",
        goal="Add UI", roster=[],
        prior_run_id="prev",
        prior_team_roles=["old_dev", "old_reviewer"],
        prior_team=prior_team,
    )

    actions = {(d["role"], d["action"]) for d in proposal.deltas}
    assert ("old_dev", "keep") in actions
    assert ("old_reviewer", "drop") in actions
    assert ("frontend_ui", "add") in actions


async def test_continuation_swap_tier_delta(tmp_path: Path):
    """Same role + same persona, different model_tier → swap_tier."""
    project_dir = _project_with_inputs(tmp_path, "p_swap")
    inputs = project_dir / "inputs"
    (inputs / "from_run_prev.meta.json").write_text(json.dumps({
        "kind": "prior_run", "run_id": "prev",
        "status": "complete", "completed_agents": ["dev"],
    }), encoding="utf-8")

    prior_team = [{"role": "dev", "model_tier": "fast"}]
    llm = MockLLMClient([_propose_call({
        "rationale": "Bumping dev to solid for the new harder ask.",
        "agents": [
            {"role": "dev", "archetype": "backend_dev",
             "depends_on": [], "model_tier": "solid"},
        ],
    })])
    meta = MetaAgent()
    meta._llm = llm

    proposal = await meta.propose(
        project_dir=project_dir, project_id="p_swap",
        goal="harder ask", roster=[],
        prior_run_id="prev",
        prior_team_roles=["dev"],
        prior_team=prior_team,
    )
    swap = next(d for d in proposal.deltas if d["role"] == "dev")
    assert swap["action"] == "swap_tier"
    assert swap["from_state"]["model_tier"] == "fast"
    assert swap["to_state"]["model_tier"] == "solid"


async def test_continuation_replace_persona_delta(tmp_path: Path):
    """Same role + same tier, different persona_id → replace_persona."""
    project_dir = _project_with_inputs(tmp_path, "p_rep")
    inputs = project_dir / "inputs"
    (inputs / "from_run_prev.meta.json").write_text(json.dumps({
        "kind": "prior_run", "run_id": "prev",
        "status": "complete", "completed_agents": ["dev"],
    }), encoding="utf-8")

    prior_team = [{"role": "dev", "model_tier": "solid",
                   "persona_id": "old-dev-v1"}]
    roster = [
        {"persona_id": "old-dev-v1", "name": "OldDev",
         "specialty": "y", "reputation": {}},
        {"persona_id": "new-dev-v1", "name": "NewDev",
         "specialty": "z", "reputation": {}},
    ]
    llm = MockLLMClient([_propose_call({
        "rationale": "Old dev not a fit; bringing in NewDev.",
        "agents": [
            {"role": "dev", "archetype": "backend_dev",
             "depends_on": [], "model_tier": "solid",
             "persona": {"action": "hire_existing",
                         "persona_id": "new-dev-v1"}},
        ],
    })])
    meta = MetaAgent()
    meta._llm = llm

    proposal = await meta.propose(
        project_dir=project_dir, project_id="p_rep",
        goal="extend", roster=roster,
        prior_run_id="prev",
        prior_team_roles=["dev"],
        prior_team=prior_team,
    )
    rep = next(d for d in proposal.deltas if d["role"] == "dev")
    assert rep["action"] == "replace_persona"
    assert rep["from_state"]["persona_id"] == "old-dev-v1"
    assert rep["to_state"]["persona_id"] == "new-dev-v1"


async def test_greenfield_proposal_has_no_deltas(tmp_path: Path):
    """Without continuation context, deltas stay empty (just a fresh
    team proposal, nothing to diff against)."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({
        "rationale": "fresh team",
        "agents": [{"role": "dev", "archetype": "backend_dev", "depends_on": []}],
    })])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    assert proposal.deltas == []


# ── compute_deltas as a pure function ────────────────────────────────────


def test_compute_deltas_pure_keep():
    from core.meta_proposal import compute_deltas
    deltas = compute_deltas(
        [{"role": "a", "model_tier": "solid"}],
        [{"role": "a", "model_tier": "solid"}],
    )
    assert deltas == [{"role": "a", "action": "keep"}]


def test_compute_deltas_handles_mixed_inputs():
    """Accepts dicts on the prior side AND AgentConfig instances on
    the new side — same shape regardless."""
    from core.runner import AgentConfig
    from core.meta_proposal import compute_deltas
    deltas = compute_deltas(
        [{"role": "a", "model_tier": "solid"}],
        [AgentConfig(role="a", model_tier="solid")],
    )
    assert deltas == [{"role": "a", "action": "keep"}]


async def test_team_proposal_to_dict_round_trip(tmp_path: Path):
    """to_dict() output matches the API response shape (stable for
    the studio's Propose.tsx)."""
    project_dir = _project_with_inputs(tmp_path)
    llm = MockLLMClient([_propose_call({
        "rationale": "test",
        "agents": [{
            "role": "dev",
            "archetype": "backend_dev",
            "depends_on": [],
            "persona": {"action": "create_new", "name_hint": "Sarah"},
        }],
    })])
    meta = MetaAgent()
    meta._llm = llm
    proposal = await meta.propose(
        project_dir=project_dir, project_id="p1", goal="x", roster=[],
    )
    d = proposal.to_dict()
    assert d["rationale"] == "test"
    assert isinstance(d["agents"], list)
    assert d["agents"][0]["role"] == "dev"
    assert isinstance(d["rbac"], dict)
    assert isinstance(d["persona_hires"], list)
    assert d["persona_hires"][0]["display_name"] == "Sarah"
    assert isinstance(d["warnings"], list)
    assert isinstance(d["deltas"], list)
