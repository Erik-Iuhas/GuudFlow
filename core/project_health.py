"""What could stop this project's next session, in one place (docs/JACK_UNSTUCK_PLAN.md U6).

Team problems, the stack, test runners agents do not have, model tiers with no usable model,
items stuck "in run" with no live run, and sessions waiting for review. Jack reads it with
`read_health`; Studio can show it as banners. It never reports a key or token, only whether one is
set.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TIERS = ("heavy", "solid", "fast")


@dataclass
class Health:
    """The project's blockers (`problems`) and things worth knowing (`notes`)."""

    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        """Lines for Jack."""
        if not self.problems and not self.notes:
            return "Nothing blocks the next session."
        return "\n".join([f"Problem: {p}" for p in self.problems] + [f"Note: {n}" for n in self.notes])

    def to_dict(self) -> dict[str, list[str]]:
        """JSON form."""
        return {"problems": list(self.problems), "notes": list(self.notes)}


def project_health(data_root: Path, project_id: str, doc: Mapping[str, Any],  # Any: project JSON
                   live_runs: Collection[str] = (), check_runtimes: bool = True) -> Health:
    """Check what the next session needs. Every check is best effort: one that cannot run is a
    note, never an error."""
    from core.backlog import Backlog
    from core.team_changes import team_problems
    h = Health()
    agents = [a for a in doc.get("agents") or [] if isinstance(a, dict)]
    if not agents:
        h.problems.append("the team is empty: approve a team before starting a session")
    h.problems += [f"team: {p}" for p in team_problems(agents)]
    try:
        from core.spec import Spec
        from core.work_db import WorkDB
        spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
        if spec.stack.empty:
            h.notes.append("the stack is not set (Spec tab): workers choose their own language")
    except Exception as e:  # noqa: BLE001 — health is best effort
        h.notes.append(f"spec unreadable: {e}")
    try:
        from core.llm.registry import ModelRegistry
        registry = ModelRegistry.for_data_root(Path(data_root))
        used = {str(a.get("model_tier") or "solid") for a in agents} | {"heavy", "fast"}
        for tier in TIERS:
            if tier not in used:
                continue
            try:
                registry.resolve(tier)
            except Exception:  # noqa: BLE001 — no usable model for this tier
                h.problems.append(f"no model with a provider key for the {tier} tier "
                                  "(Settings → Models & providers)")
    except Exception as e:  # noqa: BLE001
        h.notes.append(f"model settings unreadable: {e}")
    if check_runtimes:
        try:
            from core.harness import runtime_check
            gates = doc.get("approval_gates") or {}
            for m in runtime_check.missing_runtimes(Path(data_root), project_id,
                                                    str(gates.get("check_command") or "")):
                d = m.to_dict()
                (h.problems if d.get("checked") else h.notes).append(
                    f"test runner {d.get('program')} "
                    + ("is not installed" if d.get("checked") else "could not be checked")
                    + f" where agents run ({d.get('where')})")
        except Exception as e:  # noqa: BLE001
            h.notes.append(f"runtime check skipped: {e}")
    try:
        stuck = [i.id for i in Backlog(Path(data_root), project_id).items()
                 if i.status == "in_run" and i.run_id not in live_runs and not i.pending]
        if stuck:
            h.notes.append("items held by a run that is not running: " + ", ".join(stuck)
                           + " (release them, or send that run to review)")
    except Exception:  # noqa: BLE001
        pass
    return h


__all__ = ["Health", "project_health"]
