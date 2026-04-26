"""VisualChecker — uses request_visual_feedback to surface UI screenshots.

DESIGN §5. The visual checker writes a UI artifact, asks the runner to
screenshot it (Playwright runs on host, never inside the container),
and waits for USER_VISUAL_FEEDBACK. Stage 5 ships the agent shell;
Stage 6 wires real Playwright via the runner's `screenshot_callback`.
"""

from __future__ import annotations

from agents.base import BaseAgent


VISUAL_SYSTEM_PROMPT = """\
You are a visual checker. After the developer produces UI code, your
job is to render it, capture screenshots at the relevant viewports, and
ask the user for spatial feedback if anything looks wrong.

Process:
  1. Read the developer's HTML/component output.
  2. Call request_visual_feedback() with the rendered file path.
  3. When feedback arrives, summarise it as actionable items.
"""


class VisualChecker(BaseAgent):
    role: str = "visual_checker"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "visual_checker"),
            description="Takes screenshots of UI artifacts and routes them to the user for spatial feedback.",
            system_prompt=VISUAL_SYSTEM_PROMPT,
            **kwargs,
        )
