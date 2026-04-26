"""TechLead — escalation handler.

DESIGN §23. Event-driven, HEAVY tier. Wakes when the rejection counter
on a given role hits the escalation threshold (default: 2 rejections by
the reviewer triggers tech_lead investigation).

Stage 5 ships the activation contract; the runner watches for repeated
rejections and dispatches to tech_lead. The agent's run() is a
focused investigation: read the diff, read the rejection reasons,
research via web:search if available, post an ACTION recommending a
specific fix.
"""

from __future__ import annotations

from agents.base import BaseAgent


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


class TechLead(BaseAgent):
    role: str = "tech_lead"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "tech_lead"),
            description="Investigates stuck cycles and posts a specific ACTION.",
            system_prompt=TECH_LEAD_SYSTEM_PROMPT,
            **kwargs,
        )
