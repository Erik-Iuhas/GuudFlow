"""Render an agent's full effective_system_prompt() offline.

Usage:
    python scripts/dump_agent_prompt.py RougePack game_architect

Loads the project's persisted config + persona + manifest exactly the
way the runner does at run-start, then dumps the composed prompt with
per-layer character counts. Lets us see "the meat of the original input"
without needing a live run.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base import BaseAgent
from agents.persona import Persona
from agents import registry as agent_registry
from core.tool_manifest import ToolManifestBuilder, RoleRBAC
from core.workspace import Workspace
from worker.job_runner import serialise_project


async def main(project_id: str, role: str) -> None:
    data_root = Path("data")
    proj_data = json.loads((data_root / "projects.json").read_text(encoding="utf-8"))
    if project_id not in proj_data:
        print(f"project {project_id!r} not found"); return
    project = serialise_project(proj_data[project_id])
    cfg = next((c for c in project.agents if c.role == role), None)
    if cfg is None:
        print(f"role {role!r} not found in {project_id}"); return

    archetype_label = cfg.agent_class_role_type
    archetype = agent_registry.get(archetype_label) if archetype_label and agent_registry.has(archetype_label) else BaseAgent
    agent = archetype(role=cfg.role, description=cfg.description or "", system_prompt=cfg.system_prompt or "")

    if cfg.persona_id:
        try:
            persona = Persona.load(cfg.persona_id, data_root / "personas")
            agent.persona = persona
        except Exception as e:
            print(f"persona load failed: {e}")

    manifesto_path = data_root / "projects" / project_id / "manifesto.md"
    if manifesto_path.exists():
        agent._manifesto = manifesto_path.read_text(encoding="utf-8")

    rules_path = data_root / "projects" / project_id / "persona_rules" / f"{cfg.persona_id or cfg.role}.md"
    shared_path = data_root / "projects" / project_id / "persona_rules" / "_shared.md"
    pieces = []
    if shared_path.exists(): pieces.append(shared_path.read_text(encoding="utf-8"))
    if rules_path.exists(): pieces.append(rules_path.read_text(encoding="utf-8"))
    if pieces:
        agent._project_rules = "\n\n".join(pieces)

    ctx_path = data_root / "projects" / project_id / "context" / "roadmap.md"
    if ctx_path.exists():
        agent._project_context = ctx_path.read_text(encoding="utf-8")

    # Build a permissive RBAC: grant every declared scope, since this
    # script just wants to render the prompt the runner would build.
    rbac = {cfg.role: RoleRBAC(role=cfg.role, allowed_scopes=list(cfg.declared_local_tools or []) + ["fs", "git", "code", "doc", "data", "web", "vis", "mcp"])}
    from core.tool_manifest import LocalToolEntry
    local_registry: dict[str, LocalToolEntry] = {}
    from core.runner import LOCAL_TOOL_CLASSES
    for cls in LOCAL_TOOL_CLASSES:
        name = getattr(cls, "name", cls.__name__)
        local_registry[name] = LocalToolEntry(
            name=name,
            description=getattr(cls, "description", "") or "",
            input_schema=getattr(cls, "input_schema", {}) or {},
            priority=getattr(cls, "priority", 100),
            when_to_use=getattr(cls, "when_to_use", "") or "",
        )
    builder = ToolManifestBuilder(
        local_registry=local_registry,
        mcp_catalogs={},
        rbac=rbac,
    )
    agent._manifest = builder.build_for_agent(
        role=cfg.role,
        declared_local_tools=cfg.declared_local_tools or [],
        declared_mcp_servers=cfg.declared_mcp_servers or [],
        excluded_baseline=cfg.excluded_baseline_tools or [],
    )
    agent._workspace = Workspace(run_id="dump", goal=project.goal)
    agent._current_task = project.goal

    prompt = await agent.effective_system_prompt()
    out = Path("scripts") / f"prompt_dump_{project_id}_{role}.txt"
    out.write_text(prompt, encoding="utf-8")

    print(f"\n=== {role} effective_system_prompt — {len(prompt):,} chars / ~{len(prompt)//4:,} tokens ===")
    print(f"Full prompt written to {out}")
    # Print bytes-per-section using known anchors so we can see layer sizes.
    anchors = [
        ("identity", 0),
        ("OFFICE RULES", -1),
        ("PROJECT RULES", -1),
        ("PROJECT CONTEXT", -1),
        ("FROM YOUR EXPERIENCE", -1),
        ("Relevant lessons", -1),
        ("CODEBASE STRUCTURE", -1),
        ("AVAILABLE TOOLS", -1),
        ("PROTOCOL VOICE", -1),
        ("AUTONOMY RULES", -1),
        ("PROTOCOL OUTPUT", -1),
        ("Make this something", -1),
    ]
    positions = []
    for name, _ in anchors:
        i = prompt.find(name) if name != "identity" else 0
        positions.append((name, i))
    positions = [(n, p) for n, p in positions if p >= 0]
    positions.sort(key=lambda x: x[1])
    print(f"\n--- Layer sizes (by anchor) ---")
    for i, (name, pos) in enumerate(positions):
        end = positions[i+1][1] if i+1 < len(positions) else len(prompt)
        size = end - pos
        print(f"  {pos:>6,}  +{size:>6,} chars  {name}")


if __name__ == "__main__":
    project_id = sys.argv[1] if len(sys.argv) > 1 else "RougePack"
    role = sys.argv[2] if len(sys.argv) > 2 else "game_architect"
    asyncio.run(main(project_id, role))
