"""MetaAgent — proposes a project team from a goal + inputs.

DESIGN §7. Reads `inputs/README.md` (and selected artifacts) before
proposing a team config. Output is a `ProjectConfig` the human reviews
and approves before the run starts.

Stage 5 ships the contract. The actual proposal logic (LLM-driven team
synthesis, persona hiring, run profile selection) is large enough to
deserve its own iteration; this scaffold demonstrates the input/output
shape and is replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.base import BaseAgent
from core.runner import AgentConfig, ProjectConfig
from core.tool_manifest import RoleRBAC


META_SYSTEM_PROMPT = """\
You are a meta-agent that proposes a small team to accomplish a project
goal. Read the goal and any inputs/ artifacts first. Propose a team of
1-4 specialists (developer, reviewer, etc.) with:
  - role
  - description
  - depends_on
  - declared_local_tools
  - model_tier

Justify each role's existence. Skip roles that wouldn't add value.
"""


@dataclass
class TeamProposal:
    """What the meta-agent emits. The studio renders this for human
    approval; on approval it is converted to a `ProjectConfig`."""

    rationale: str
    roles: list[AgentConfig] = field(default_factory=list)
    rbac: dict[str, RoleRBAC] = field(default_factory=dict)


class MetaAgent(BaseAgent):
    role: str = "meta"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "meta"),
            description="Proposes a project team from goal + inputs.",
            system_prompt=META_SYSTEM_PROMPT,
            **kwargs,
        )
