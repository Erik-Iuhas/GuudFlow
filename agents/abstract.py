"""Abstract base classes for agent archetypes.

DESIGN §3.4 plus Phase C.0 (post-shipped C.1, C.2). The codebase has
three recurring patterns of agent — reviewers, escalation handlers,
and specialists — and three concrete builtins (CodeReviewer, TechLead,
RequirementsWriter as the proof-of-pattern) that fit those patterns.

Why this layer exists
---------------------
Without it, every new builtin (the next escalation handler, the next
reviewer flavour, the next specialist) is a near-duplicate of the
previous one: copy the system prompt, copy the tool list, copy the
RBAC defaults, hope nothing drifts. With it:

- A new escalation handler is one prompt + class attribute overrides.
- A new specialist is one prompt + permission tweaks.
- The meta-agent (Phase D) reads the catalog (`agents/catalog.py`) and
  picks archetypes by `archetype_kind` + `archetype_when_to_use` —
  there's a real taxonomy to reason over instead of free-form prose.

What's NOT in here
------------------
- `MemoryWriter`, `VisualChecker`, `TestRunner`, `ITAgent` — these
  are synthesis / side-task agents whose `run()` overrides the
  generic loop. They could extend an archetype later if a fit emerges,
  but forcing them in now would be premature.
- The runtime dispatch logic (when does an EscalationHandler fire? at
  what threshold?). That lives in `core/runner.py` per project config
  — the class only declares the "shape", not the trigger condition.
"""

from __future__ import annotations

from typing import ClassVar

from agents.base import BaseAgent
from core.signals import SignalType


class AgentArchetype(BaseAgent):
    """Marker base for builtin archetypes. Holds the class-level
    metadata that the catalog (and meta-agent) reads. Subclasses
    override the class attributes; concrete agent classes extend the
    intermediate Reviewer / EscalationHandler / Specialist layer below
    rather than this base directly."""

    # Discriminator used by the catalog. Set by the intermediate
    # bases; concrete subclasses don't override.
    archetype_kind: ClassVar[str] = ""

    # Human-readable description + selection criterion. Concrete
    # subclasses set these so the catalog has something useful for the
    # meta-agent to reason over.
    archetype_description: ClassVar[str] = ""
    archetype_when_to_use: ClassVar[str] = ""

    # Defaults the meta-agent uses when populating an AgentConfig for
    # this archetype. Concrete subclasses may override.
    default_model_tier: ClassVar[str] = "solid"
    default_tools: ClassVar[tuple[str, ...]] = ()
    default_rbac_scopes: ClassVar[tuple[str, ...]] = ()


class Reviewer(AgentArchetype):
    """Diff-aware reviewer. Reads upstream agent branches via git tools,
    evaluates against the project goal, posts APPROVED or REJECTED.

    Concrete subclasses provide `system_prompt` and may override
    `default_tools` / `default_rbac_scopes` for narrower focus (e.g.
    a security-only reviewer with a curated read scope).

    Today this is a marker — the runner's `_generic_tool_use_loop`
    drives all reviewers. The base exists so adding a new reviewer
    flavour (architect, security, accessibility) is one prompt + class
    attributes, not a copy of the dispatch wiring.
    """

    archetype_kind: ClassVar[str] = "reviewer"
    default_model_tier: ClassVar[str] = "solid"
    default_tools: ClassVar[tuple[str, ...]] = (
        "fs-read", "fs-list", "fs-read_range", "fs-grep",
        "git-diff", "git-changed-files", "git-log", "git-read", "git-status",
    )
    # Reviewers don't write — they decide. Concrete subclasses can
    # widen this if they need to (e.g. a reviewer that auto-files
    # tickets via an MCP).
    default_rbac_scopes: ClassVar[tuple[str, ...]] = ()


class EscalationHandler(AgentArchetype):
    """Wakes on a workspace event (runner-dispatched, not scheduler-
    driven). The runner uses `wake_on_signal_type` (and per-project
    threshold config in `escalation_thresholds`) to decide when to
    fire the handler.

    Concrete subclasses provide `system_prompt` and override
    `wake_on_signal_type` to declare what they listen for. For the
    current implementation the runner only acts on REJECTED-count
    crossings, but this hook is here so future handlers (incident
    responders, deadline watchers) can declare different triggers
    without reinventing the dispatch path.

    HEAVY tier by default — escalations activate rarely and need
    genuine reasoning (CLAUDE.md, DESIGN §22 §23).
    """

    archetype_kind: ClassVar[str] = "escalation_handler"
    wake_on_signal_type: ClassVar[SignalType | None] = None

    default_model_tier: ClassVar[str] = "heavy"
    default_tools: ClassVar[tuple[str, ...]] = (
        "fs-read", "fs-list", "fs-read_range", "fs-grep",
        "git-diff", "git-log", "git-read",
        "web-search", "web-fetch",
    )
    default_rbac_scopes: ClassVar[tuple[str, ...]] = (
        "web-search", "web-fetch",
    )


class Specialist(AgentArchetype):
    """Does the actual work: produces files, fills artifacts, commits
    on its own branch. Frontend, backend, requirements writer, test
    writer — all extend this with different prompts and permission
    profiles.

    Concrete subclasses provide `system_prompt` and override
    `default_tools` + `default_rbac_scopes` to express the role's
    permission posture (e.g. a requirements writer reads docs and
    writes Markdown but never executes code).
    """

    archetype_kind: ClassVar[str] = "specialist"
    default_model_tier: ClassVar[str] = "solid"
    default_tools: ClassVar[tuple[str, ...]] = (
        "fs-read", "fs-list", "fs-read_range", "fs-grep",
    )
    default_rbac_scopes: ClassVar[tuple[str, ...]] = ()
