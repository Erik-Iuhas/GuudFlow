"""TechLead — escalation handler.

DESIGN §23. Wakes when the rejection counter on a given role hits the
project's escalation threshold (default: 2 rejections). The runner
dispatches it on a side task (it doesn't go through the scheduler) so
its analysis lands on the board concurrently with the rest of the run.

Stage 5 shipped the activation contract; Phase C.3 wires the runner
to actually count REJECTED signals and call this handler. The agent's
`run()` is a focused investigation: read the diff, read the rejection
reasons, optionally research via `web:search` / `web:fetch`, post an
ACTION recommending a specific fix.

As of Phase C.0, TechLead extends the abstract `EscalationHandler`
base (`agents/abstract.py`) so the catalog lists it alongside future
escalation handlers with the same metadata shape.
"""

from __future__ import annotations

from typing import ClassVar

from agents.abstract import EscalationHandler
from core.signals import SignalType


TECH_LEAD_SYSTEM_PROMPT = """\
You are a senior tech lead. An agent has been rejected multiple times
on the same task. Your job is to investigate the cause and post a
specific ACTION recommendation that breaks the loop.

Process:
  1. Read the rejection signals on the board (read_protocol with
     types={'VERIFY_RESULT'}).
  2. Use git:diff to see the agent's most recent attempt.
  3. If the failure is novel, use web:search to research.
  4. Post one specific ACTION that the agent should take. Be concrete
     — name the file, the function, the change.
  5. End with a one-line summary of your recommendation.

You are the senior here. The team is stuck. Make the call.
"""


class TechLead(EscalationHandler):
    role: str = "tech_lead"

    # Wakes when an agent crosses the project's REJECTED threshold.
    # The runner reads `escalation_thresholds["tech_lead"]` per project
    # to decide the actual count; this attribute declares the trigger
    # signal type for catalog/meta-agent reasoning.
    wake_on_signal_type: ClassVar[SignalType | None] = SignalType.REJECTED

    archetype_description: ClassVar[str] = (
        "Investigates stuck cycles (multiple rejections on the same "
        "agent) and posts a specific ACTION recommendation that "
        "breaks the loop. HEAVY tier by default — activates rarely."
    )
    archetype_when_to_use: ClassVar[str] = (
        "Include in any project where the developer/reviewer cycle "
        "could repeat unsuccessfully. The runner only dispatches this "
        "agent if `escalation_thresholds.tech_lead` is positive (default 2)."
    )

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "tech_lead"),
            description="Investigates stuck cycles and posts a specific ACTION.",
            system_prompt=TECH_LEAD_SYSTEM_PROMPT,
            **kwargs,
        )
