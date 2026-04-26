"""RequirementsWriter — turns project inputs into a structured artifact.

Concrete `Specialist` showcasing the abstract base from
`agents/abstract.py`. Reads spec PDFs / docs / markdown from
`inputs/uploads/` (DESIGN §33), distills them into a single
`requirements.md` artifact under `working/`, and signals COMPLETE.

The class declares its tool/permission posture at the class level
(`default_tools`, `default_rbac_scopes`) so the catalog and meta-agent
can reason about what this archetype needs without inspecting prompts.

Why this is the proof-of-pattern Specialist
-------------------------------------------
RequirementsWriter exercises the parts of the Specialist contract that
matter:
  - Reads multiple input formats (`doc:pdf`, `doc:docx`, `doc:markdown`)
    — proves doc tools propagate via class-level defaults
  - Writes to `working/` (`fs:write`) — proves write RBAC propagates
  - Doesn't run code (`code:exec` not in defaults) — proves the
    permission posture is opinionated, not a kitchen-sink default
"""

from __future__ import annotations

from typing import ClassVar

from agents.abstract import Specialist


REQUIREMENTS_WRITER_SYSTEM_PROMPT = """\
You are a requirements specialist. Your job is to read the project's
inputs (specs, mockups, prior-run feedback) and produce a single
structured artifact: `requirements.md` in the working/ directory.

Process:
  1. Read `inputs/README.md` to see what's been provided.
  2. Read each upload via `doc:pdf` / `doc:docx` / `doc:markdown` as
     appropriate.
  3. Synthesise into a structured requirements document with:
     - Goals (what success looks like)
     - Non-goals (what's explicitly out of scope)
     - Functional requirements (numbered, testable)
     - Constraints (technical, business, regulatory)
     - Open questions (anything unresolved — flag for human review)
  4. Write the result with `fs:write` to `requirements.md`.
  5. End with a one-line summary of what you wrote.

Be precise. Downstream agents will treat your `requirements.md` as the
contract; ambiguity here becomes wrong code later.
"""


class RequirementsWriter(Specialist):
    role: str = "requirements_writer"

    # Class-level defaults — the catalog reads these so the meta-agent
    # knows what to set on AgentConfig when proposing a team.
    default_model_tier: ClassVar[str] = "solid"
    default_tools: ClassVar[tuple[str, ...]] = (
        "fs-read", "fs-list", "fs-read_range", "fs-grep",
        "fs-write",
        "doc-pdf", "doc-docx", "doc-markdown",
    )
    default_rbac_scopes: ClassVar[tuple[str, ...]] = (
        "fs-write",
        "doc-pdf", "doc-docx", "doc-markdown",
    )

    archetype_description: ClassVar[str] = (
        "Reads project inputs (PDFs, specs, prior-run feedback) and "
        "produces a structured requirements.md artifact under working/."
    )
    archetype_when_to_use: ClassVar[str] = (
        "Use as the first agent when the user has provided spec "
        "documents that need to be normalised before development "
        "starts. Downstream developers should depend_on this role."
    )

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "requirements_writer"),
            description=(
                "Turns project inputs (PDFs, specs, prior-run feedback) "
                "into a structured requirements.md."
            ),
            system_prompt=REQUIREMENTS_WRITER_SYSTEM_PROMPT,
            **kwargs,
        )
