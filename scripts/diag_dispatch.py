"""One-shot diagnostic: does the dispatch rule fire for Diana?"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base import BaseAgent
from core.tool_manifest import ToolManifestBuilder, RoleRBAC, LocalToolEntry
from core.workspace import Workspace
from core.runner import LOCAL_TOOL_CLASSES
from worker.job_runner import serialise_project


async def main():
    project = serialise_project(json.load(open("data/projects.json", encoding="utf-8"))["RougePack"])
    cfg = next(c for c in project.agents if c.role == "game_architect")
    agent = BaseAgent(role=cfg.role, description=cfg.description or "", system_prompt=cfg.system_prompt or "")
    rbac = {cfg.role: RoleRBAC(role=cfg.role, allowed_scopes=list(cfg.declared_local_tools or []) + ["fs","git","code","doc","data","web","vis","mcp"])}
    local_registry = {}
    for cls in LOCAL_TOOL_CLASSES:
        name = getattr(cls, "name", cls.__name__)
        local_registry[name] = LocalToolEntry(
            name=name,
            description=getattr(cls, "description", "") or "",
            input_schema=getattr(cls, "input_schema", {}) or {},
            priority=getattr(cls, "priority", 100),
            when_to_use=getattr(cls, "when_to_use", "") or "",
        )
    builder = ToolManifestBuilder(local_registry=local_registry, mcp_catalogs={}, rbac=rbac)
    agent._manifest = builder.build_for_agent(
        role=cfg.role,
        declared_local_tools=cfg.declared_local_tools or [],
        declared_mcp_servers=cfg.declared_mcp_servers or [],
        excluded_baseline=cfg.excluded_baseline_tools or [],
    )
    agent._manages = list(cfg.manages or [])
    agent._workspace = Workspace(run_id="diag", goal=project.goal)
    print("_manages:", agent._manages)
    print("_has_assign_task:", agent._has_assign_task())
    print("descriptors with assign-task:", [d.name for d in agent._manifest.descriptors if "assign" in d.name])
    sp = await agent.effective_system_prompt()
    has_block = "DISPATCH RULE — split work by domain" in sp
    print("dispatch block injected:", has_block)
    if has_block:
        i = sp.find("DISPATCH RULE — split work by domain")
        print("---block---")
        print(sp[i:i+800])

asyncio.run(main())
