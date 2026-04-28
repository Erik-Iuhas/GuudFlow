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

from dataclasses import dataclass, field
from typing import Any, ClassVar

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
  4. End with the [ACTION] block per the protocol layer:
     `target:` the role to act, `verb:` FIX|REVISIT|CLARIFY,
     `subject:` a concrete one-line direction (name the file, the
     function, the change).

You are the senior here. The team is stuck. Make the call.

TASK ASSIGNMENT: When you produce work that another agent should
implement, decompose it into one or more tasks via the `assign-task`
tool. Always populate acceptance criteria. Reference your own
artifacts (files, commits, parent stories) so the assignee has the
source of truth. Do not assign work outside the team's roster.
"""

TECH_LEAD_RESPOND_PROMPT = """\
You are a senior tech lead. A teammate has asked you a question and needs
an answer before they can continue their work. Read the question carefully.

Process:
  1. Read any context files listed in context_paths if provided.
  2. Look at the relevant git branches (git-diff, git-log) if the question
     is about existing code.
  3. Give a direct, specific answer — one or two sentences max.
  4. End with [ANSWER] block:
       ask_id: <the ask_id provided in the question context>
       to: <the role that asked>
       answer: <your answer — ≤ 12 words, specific and actionable>

You are the authority here. Give a decision, not a list of options.
"""

TECH_LEAD_CONFIRM_SKIP_PROMPT = """\
You are a senior tech lead. A teammate wants to sit out this iteration.
Your job is to confirm or redirect them.

Context you receive: the agent's role, their reason, and the files they inspected.

Process:
  1. Read the evidence_paths to verify they actually contain no work for that role.
  2. If the diff/files confirm the agent has nothing to do: emit [SKIP_GRANTED].
  3. If there IS work for that agent: emit [SKIP_DENIED] with a specific redirected_task.
  4. Keep it brief — this is a fast decision.

Block for granting: [SKIP_GRANTED]
  skip_id: <skip_id>
  note: confirmed — no relevant files

Block for denying: [SKIP_DENIED]
  skip_id: <skip_id>
  redirected_task: <one sentence: what the agent should actually do>
"""


class TechLead(EscalationHandler):
    role: str = "tech_lead"

    # Wakes when an agent crosses the project's REJECTED threshold.
    # The runner reads `escalation_thresholds["tech_lead"]` per project
    # to decide the actual count; this attribute declares the trigger
    # signal type for catalog/meta-agent reasoning.
    wake_on_signal_type: ClassVar[SignalType | None] = SignalType.REJECTED

    # M3.5 — tech_lead is the senior planner. It can rebalance work
    # via the assign-task tool when escalations or user comments
    # arrive (DESIGN §38.12 D2).
    default_tools: ClassVar[tuple[str, ...]] = (
        *EscalationHandler.default_tools, "assign-task",
    )
    default_rbac_scopes: ClassVar[tuple[str, ...]] = (
        *EscalationHandler.default_rbac_scopes, "assign-task",
    )

    archetype_description: ClassVar[str] = (
        "Investigates stuck cycles (multiple rejections on the same "
        "agent) and posts a specific ACTION recommendation that "
        "breaks the loop. HEAVY tier by default — activates rarely. "
        "Also handles peer ASK/ANSWER routing and REQUEST_SKIP confirmation."
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

    async def run_respond(
        self,
        workspace: Any,
        ask_id: str,
        asker_role: str,
        question: str,
        context_paths: list[str] | None = None,
    ) -> str:
        """Handle a `respond` task — answer a peer's ASK.

        Called by the runner when tech_lead is spawned with task_type="respond".
        Returns the ANSWER body text.
        """
        from core.llm import LLMMessage

        # Temporarily override system prompt for this task.
        original_prompt = self.system_prompt
        self.system_prompt = TECH_LEAD_RESPOND_PROMPT

        ctx_note = ""
        if context_paths:
            ctx_note = f"\nContext files to check: {', '.join(context_paths)}"

        messages = [
            LLMMessage(
                role="user",
                content=(
                    f"PEER QUESTION from {asker_role}:\n"
                    f"ask_id: {ask_id}\n"
                    f"question: {question}{ctx_note}\n\n"
                    f"Provide a direct answer. End with the [ANSWER] block."
                ),
            )
        ]

        try:
            response = await self.llm(messages)
            return response.text or "(no answer)"
        finally:
            self.system_prompt = original_prompt

    async def run_confirm_skip(
        self,
        workspace: Any,
        skip_id: str,
        requesting_role: str,
        reason: str,
        evidence_paths: list[str] | None = None,
    ) -> str:
        """Handle a `confirm_skip` task — grant or deny a REQUEST_SKIP.

        Called by the runner when tech_lead is spawned with task_type="confirm_skip".
        Returns the full compact block text ([SKIP_GRANTED] or [SKIP_DENIED]).
        """
        from core.llm import LLMMessage

        original_prompt = self.system_prompt
        self.system_prompt = TECH_LEAD_CONFIRM_SKIP_PROMPT

        evidence_note = ""
        if evidence_paths:
            evidence_note = f"\nEvidence files: {', '.join(evidence_paths)}"

        messages = [
            LLMMessage(
                role="user",
                content=(
                    f"REQUEST_SKIP from {requesting_role}:\n"
                    f"skip_id: {skip_id}\n"
                    f"reason: {reason}{evidence_note}\n\n"
                    f"Inspect the evidence and emit [SKIP_GRANTED] or [SKIP_DENIED]."
                ),
            )
        ]

        try:
            response = await self.llm(messages)
            return response.text or "(no decision)"
        finally:
            self.system_prompt = original_prompt
