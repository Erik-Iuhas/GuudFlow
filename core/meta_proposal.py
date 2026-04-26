"""Meta-agent proposal helpers (Phase D.1).

Reads project inputs + roster + archetype catalog and produces the
prompt + structured-output schema the MetaAgent uses to propose a
team. Parses the LLM's tool-use response back into a `TeamProposal`.
Falls back to a deterministic skeleton when anything goes sideways
so `/api/meta/propose` never breaks.

The meta-agent itself (`agents/builtin/meta.py`) is the consumer of
these helpers. Splitting the logic out keeps `meta.py` short (just
the BaseAgent class + run method) and makes the input/output shapes
easy to test in isolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.catalog import (
    ArchetypeMeta,
    dedupe_role_names,
    list_all_archetypes,
    suggest_persona_id,
)
from core.archetype_doc import get_doc_archetype
from core.runner import AgentConfig
from core.tool_manifest import RoleRBAC


# ── Input model ────────────────────────────────────────────────────────────


@dataclass
class UploadInfo:
    filename: str
    size: int
    excerpt: str = ""


@dataclass
class PriorRunContext:
    """For continuation runs: what the meta-agent learns about the
    previous run before producing a delta proposal."""

    run_id: str
    status: str = ""
    completed_agents: list[str] = field(default_factory=list)
    feedback: str = ""  # from from_run_{id}/feedback.md if present
    prior_team_roles: list[str] = field(default_factory=list)


@dataclass
class MetaInputs:
    """Everything the meta-agent reads at proposal time."""

    project_id: str
    goal: str
    uploads: list[UploadInfo] = field(default_factory=list)
    source_repo: dict[str, Any] | None = None  # parsed source_repo.meta.json
    prior_runs: list[dict[str, Any]] = field(default_factory=list)  # attached from_run_*
    continuation: PriorRunContext | None = None


# ── Persona hire recommendation ────────────────────────────────────────────


@dataclass
class PersonaHire:
    """Per-role recommendation for who fills the seat. Two kinds:
    `hire_existing` (re-use a registered persona) and `create_new`
    (mint a new persona id + display name)."""

    role: str
    action: str  # "hire_existing" | "create_new"
    persona_id: str
    display_name: str
    rationale: str = ""


# ── Reading project inputs ─────────────────────────────────────────────────


_EXCERPT_CHARS = 800
_TEXTY_SUFFIXES = {".md", ".txt", ".markdown", ".rst", ".json", ".yaml", ".yml"}


def read_meta_inputs(project_dir: Path, project_id: str, goal: str) -> MetaInputs:
    """Walk `project_dir/inputs/` and produce a structured summary the
    meta-agent's prompt builder consumes."""
    inputs_dir = Path(project_dir) / "inputs"
    inputs = MetaInputs(project_id=project_id, goal=goal)

    if not inputs_dir.is_dir():
        return inputs

    # Uploads: enumerate with size + a short text excerpt for files we
    # can read cheaply. PDF/Word files don't get an excerpt at this
    # layer (the meta-agent reads filename + size and infers).
    uploads_dir = inputs_dir / "uploads"
    if uploads_dir.is_dir():
        for f in sorted(uploads_dir.iterdir()):
            if not f.is_file():
                continue
            size = f.stat().st_size
            excerpt = ""
            if f.suffix.lower() in _TEXTY_SUFFIXES and size < 100_000:
                try:
                    excerpt = f.read_text(encoding="utf-8")[:_EXCERPT_CHARS]
                except OSError:
                    excerpt = ""
            inputs.uploads.append(UploadInfo(
                filename=f.name, size=size, excerpt=excerpt,
            ))

    # Source repo metadata (sidecar from D-prime Chunk 2).
    src_meta = inputs_dir / "source_repo.meta.json"
    if src_meta.is_file():
        try:
            inputs.source_repo = json.loads(
                src_meta.read_text(encoding="utf-8"),
            )
        except (json.JSONDecodeError, OSError):
            pass

    # Prior runs attached to the project (D-prime Chunk 3).
    for entry in sorted(inputs_dir.iterdir()):
        if entry.is_file() and entry.name.startswith("from_run_") and entry.name.endswith(".meta.json"):
            try:
                inputs.prior_runs.append(
                    json.loads(entry.read_text(encoding="utf-8")),
                )
            except (json.JSONDecodeError, OSError):
                continue

    return inputs


def attach_continuation_context(
    inputs: MetaInputs,
    project_dir: Path,
    prior_run_id: str,
    prior_team_roles: list[str] | None = None,
) -> MetaInputs:
    """Populate `inputs.continuation` with the prior run's context.

    Reads `inputs/from_run_{id}/feedback.md` for any user feedback,
    plus the prior_runs metadata for status / completed_agents.
    `prior_team_roles` comes from the caller (the route layer reads
    the project's stored config to know what the prior team looked like).
    """
    prior_meta = next(
        (m for m in inputs.prior_runs if m.get("run_id") == prior_run_id),
        None,
    )
    if prior_meta is None:
        return inputs

    feedback_path = (
        Path(project_dir) / "inputs" / f"from_run_{prior_run_id}" / "feedback.md"
    )
    feedback = ""
    if feedback_path.is_file():
        try:
            feedback = feedback_path.read_text(encoding="utf-8").strip()
        except OSError:
            feedback = ""

    inputs.continuation = PriorRunContext(
        run_id=prior_run_id,
        status=str(prior_meta.get("status", "")),
        completed_agents=list(prior_meta.get("completed_agents") or []),
        feedback=feedback,
        prior_team_roles=list(prior_team_roles or []),
    )
    return inputs


# ── Prompt rendering ───────────────────────────────────────────────────────


def render_archetype_catalog(archetypes: list[ArchetypeMeta]) -> str:
    """Compact archetype list for the meta-agent's prompt. The full
    `responsibility` prose comes from the YAML for prompt-defined
    agents at construction time — at proposal time the LLM sees one
    line per archetype to keep the prompt cheap."""
    lines = ["Available archetypes (pick by `archetype` field):"]
    for m in archetypes:
        line = (
            f"- {m.name} ({m.abstract_base}, {m.default_model_tier} tier): "
            f"{m.description}"
        )
        if m.when_to_use:
            line += f" — When to use: {m.when_to_use}"
        lines.append(line)
    return "\n".join(lines)


def render_persona_roster(roster: list[dict[str, Any]]) -> str:
    """Compact persona list. Each entry: id, name, specialty, run count.
    The LLM uses this to pick `hire_existing` vs `create_new` per role."""
    if not roster:
        return "Persona roster is empty — every persona will be created new."
    lines = ["Available personas (use the `persona_id` for hire_existing):"]
    for p in roster:
        rep = p.get("reputation") or {}
        prod = rep.get("production") or {}
        runs = prod.get("total_runs", 0)
        lines.append(
            f"- {p.get('persona_id', '?')} ({p.get('name', '?')}, "
            f"{p.get('specialty', 'general')}): {runs} prior runs"
        )
    return "\n".join(lines)


def render_inputs(inputs: MetaInputs) -> str:
    """Render the project's inputs into a prompt block."""
    parts: list[str] = [f"Project goal: {inputs.goal}"]

    if inputs.source_repo is not None:
        kind = inputs.source_repo.get("kind")
        if kind == "git_url":
            parts.append(
                f"Source repo: cloned from "
                f"{inputs.source_repo.get('git_url', '?')} @ "
                f"{inputs.source_repo.get('branch', 'main')}"
            )
        elif kind == "local_path":
            parts.append(
                f"Source repo: copied from local path "
                f"{inputs.source_repo.get('path', '?')}"
            )

    if inputs.uploads:
        parts.append("Uploaded inputs:")
        for u in inputs.uploads:
            line = f"  - uploads/{u.filename} ({u.size} bytes)"
            if u.excerpt:
                # Indent + cap the excerpt so the prompt stays compact.
                excerpt_short = u.excerpt[:200].replace("\n", " ")
                line += f" — excerpt: {excerpt_short}"
            parts.append(line)

    if inputs.continuation is not None:
        c = inputs.continuation
        parts.append(
            f"\nThis is a CONTINUATION of run {c.run_id} "
            f"(status={c.status}, completed: {', '.join(c.completed_agents) or '(none)'})."
        )
        if c.prior_team_roles:
            parts.append(f"Prior team roles: {', '.join(c.prior_team_roles)}")
        if c.feedback:
            parts.append(f"User feedback for this run:\n{c.feedback}")

    return "\n".join(parts)


def render_user_message(
    inputs: MetaInputs,
    archetypes: list[ArchetypeMeta],
    roster: list[dict[str, Any]],
) -> str:
    """Build the full user message the LLM sees."""
    return "\n\n".join([
        render_inputs(inputs),
        render_archetype_catalog(archetypes),
        render_persona_roster(roster),
        (
            "Propose a team using the `propose_team` tool. Ensure every "
            "role name is unique. Prefer descriptive role names "
            "(`auth_dev`, `api_dev`) over numeric suffixes. For each "
            "role, recommend either `hire_existing` (referencing a "
            "persona_id from the roster above) or `create_new` (with a "
            "name_hint like 'Mike' or 'Sarah' that fits the persona's "
            "character)."
        ),
    ])


# ── Tool-use schema ────────────────────────────────────────────────────────


PROPOSE_TEAM_TOOL = {
    "name": "propose_team",
    "description": (
        "Propose a team to accomplish the project goal. Justify each "
        "role's existence in the rationale. Use `archetype` from the "
        "catalog. Set `is_final_reviewer=true` for at most one architect-"
        "style role; the runner auto-fills its depends_on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "Why this team for this goal — 2-4 sentences.",
            },
            "agents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "Unique role name within this team. Snake_case.",
                        },
                        "archetype": {
                            "type": "string",
                            "description": "Archetype name from the catalog above.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-line description of this role's responsibility in this project.",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Roles this agent waits for. Empty for the first agent in the chain.",
                        },
                        "model_tier": {
                            "type": "string",
                            "enum": ["heavy", "solid", "fast"],
                        },
                        "is_reviewer": {"type": "boolean"},
                        "is_final_reviewer": {"type": "boolean"},
                        "persona": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["hire_existing", "create_new"],
                                },
                                "persona_id": {
                                    "type": "string",
                                    "description": (
                                        "For hire_existing: the persona_id "
                                        "from the roster. For create_new: "
                                        "leave empty; the runner generates one."
                                    ),
                                },
                                "name_hint": {
                                    "type": "string",
                                    "description": (
                                        "For create_new: a first name that "
                                        "fits the persona's character "
                                        "(e.g. 'Mike', 'Sarah'). Optional."
                                    ),
                                },
                                "rationale": {"type": "string"},
                            },
                            "required": ["action"],
                        },
                    },
                    "required": ["role", "archetype", "depends_on"],
                },
            },
        },
        "required": ["rationale", "agents"],
    },
}


# ── Parsing ────────────────────────────────────────────────────────────────


@dataclass
class ParseResult:
    """Output of `parse_proposal_args`: the structured proposal plus
    any warnings from sanitisation (e.g. role-name collisions
    auto-fixed)."""

    rationale: str
    agents: list[AgentConfig]
    rbac: dict[str, RoleRBAC]
    persona_hires: list[PersonaHire]
    warnings: list[str] = field(default_factory=list)


def parse_proposal_args(
    args: dict[str, Any],
    archetypes: list[ArchetypeMeta],
    roster: list[dict[str, Any]],
) -> ParseResult:
    """Turn the LLM's `propose_team` tool-call arguments into a
    structured proposal. Defensive: missing or malformed fields are
    sanitised with warnings rather than failing outright. The caller
    (MetaAgent) decides whether to fall back to the skeleton based on
    how degraded the result is."""
    warnings: list[str] = []

    rationale = str(args.get("rationale") or "").strip()
    if not rationale:
        warnings.append("rationale was empty")

    raw_agents = args.get("agents") or []
    if not isinstance(raw_agents, list):
        return ParseResult(
            rationale=rationale,
            agents=[],
            rbac={},
            persona_hires=[],
            warnings=warnings + ["`agents` field was not a list"],
        )

    archetypes_by_name = {a.name: a for a in archetypes}
    existing_persona_ids: set[str] = {
        str(p.get("persona_id")) for p in roster if p.get("persona_id")
    }
    # Track display names used WITHIN this proposal so a 3-agent team
    # reads like 3 humans (Mike, Sarah, Alex) rather than 3 Mikes.
    # Populated as we walk roles: existing-hire's name + create_new's
    # generated name. NOT pre-populated with the full roster (that
    # would ban every persona name globally, defeating the point).
    used_display_names: set[str] = set()
    roster_by_id = {
        str(p.get("persona_id")): p for p in roster if p.get("persona_id")
    }

    role_names: list[str] = []
    raw_role_data: list[dict[str, Any]] = []
    for raw in raw_agents:
        if not isinstance(raw, dict):
            warnings.append("skipped a non-dict agent entry")
            continue
        role = str(raw.get("role") or "").strip()
        if not role:
            warnings.append("skipped agent with empty role")
            continue
        role_names.append(role)
        raw_role_data.append(raw)

    deduped = dedupe_role_names(role_names)
    if deduped != role_names:
        warnings.append("role name collisions were auto-renumbered")

    agents: list[AgentConfig] = []
    rbac: dict[str, RoleRBAC] = {}
    hires: list[PersonaHire] = []

    for role, raw in zip(deduped, raw_role_data):
        archetype_name = str(raw.get("archetype") or "").strip()
        archetype = archetypes_by_name.get(archetype_name)
        if archetype is None:
            warnings.append(
                f"role {role!r} referenced unknown archetype "
                f"{archetype_name!r}; defaulting to a generic specialist"
            )

        # Pull defaults from the archetype if found, else conservative.
        default_tools = list(archetype.default_tools) if archetype else ["fs:read"]
        default_rbac = list(archetype.default_rbac_scopes) if archetype else []
        default_tier = archetype.default_model_tier if archetype else "solid"

        # If the archetype is shipped (concrete class), use its registry
        # name so the runner's agent_factory builds the class. Else
        # leave agent_class_role_type=None for prompt-defined.
        agent_class_role_type: str | None = None
        if archetype is not None:
            from agents.registry import registry as _registry
            if _registry.has(archetype.name):
                agent_class_role_type = archetype.name

        # System prompt: for shipped, the class provides it. For
        # described-only, pull from the YAML.
        system_prompt = ""
        if agent_class_role_type is None and archetype is not None:
            doc_entry = get_doc_archetype(archetype.name)
            if doc_entry is not None:
                system_prompt = doc_entry.responsibility.strip()

        # Persona handling.
        persona_id: str | None = None
        persona_block = raw.get("persona") if isinstance(raw.get("persona"), dict) else None
        if persona_block is not None:
            action = str(persona_block.get("action") or "").strip()
            if action == "hire_existing":
                pid = str(persona_block.get("persona_id") or "").strip()
                if pid in existing_persona_ids:
                    persona_id = pid
                    existing_persona = roster_by_id.get(pid, {})
                    display = str(existing_persona.get("name") or pid)
                    used_display_names.add(display)
                    hires.append(PersonaHire(
                        role=role, action="hire_existing",
                        persona_id=pid, display_name=display,
                        rationale=str(persona_block.get("rationale") or ""),
                    ))
                else:
                    warnings.append(
                        f"role {role!r} requested hire_existing for "
                        f"unknown persona {pid!r}; falling back to create_new"
                    )
                    action = "create_new"
            if action == "create_new":
                hint = str(persona_block.get("name_hint") or "").strip() or None
                pid, display = suggest_persona_id(
                    role=role,
                    existing_ids=existing_persona_ids,
                    name_hint=hint,
                    used_display_names=used_display_names,
                )
                persona_id = pid
                existing_persona_ids.add(pid)
                used_display_names.add(display)
                hires.append(PersonaHire(
                    role=role, action="create_new",
                    persona_id=pid, display_name=display,
                    rationale=str(persona_block.get("rationale") or ""),
                ))

        agents.append(AgentConfig(
            role=role,
            description=str(raw.get("description") or "").strip(),
            depends_on=[str(d) for d in (raw.get("depends_on") or [])],
            system_prompt=system_prompt,
            declared_local_tools=default_tools,
            declared_mcp_servers=[],
            persona_id=persona_id,
            model_tier=str(raw.get("model_tier") or default_tier),
            agent_class_role_type=agent_class_role_type,
            is_reviewer=bool(raw.get("is_reviewer", False)),
            is_final_reviewer=bool(raw.get("is_final_reviewer", False)),
        ))
        rbac[role] = RoleRBAC(role=role, allowed_scopes=default_rbac)

    return ParseResult(
        rationale=rationale or "(meta-agent did not provide a rationale)",
        agents=agents,
        rbac=rbac,
        persona_hires=hires,
        warnings=warnings,
    )


# ── Fallback ───────────────────────────────────────────────────────────────


def fallback_skeleton(goal: str, reason: str = "") -> ParseResult:
    """Hardcoded developer + reviewer team. Returned when the LLM
    proposal fails so `/api/meta/propose` always responds with a
    usable team."""
    rationale = (
        f"Proposing a developer + reviewer baseline for '{goal}'. "
        "Adjust roles, tools, and RBAC scopes before approving."
    )
    if reason:
        rationale += f" (Fallback reason: {reason})"

    return ParseResult(
        rationale=rationale,
        agents=[
            AgentConfig(
                role="developer",
                description="Writes the code.",
                depends_on=[],
                declared_local_tools=[
                    "fs:read", "fs:list", "fs:write", "fs:edit",
                    "code:exec", "git:diff", "git:log", "git:read",
                ],
                declared_mcp_servers=[],
                model_tier="solid",
            ),
            AgentConfig(
                role="reviewer",
                description="Reviews the developer's work.",
                depends_on=["developer"],
                declared_local_tools=[
                    "fs:read", "fs:list", "git:diff", "git:log",
                    "git:read", "git:status",
                ],
                declared_mcp_servers=[],
                model_tier="solid",
                agent_class_role_type="code_reviewer",
                is_reviewer=True,
            ),
        ],
        rbac={
            "developer": RoleRBAC(
                role="developer",
                allowed_scopes=["fs:write", "fs:edit", "code:exec"],
            ),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
        persona_hires=[],
        warnings=[reason] if reason else [],
    )
