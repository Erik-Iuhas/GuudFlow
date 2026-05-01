"""desk-push tool — hand a document to a teammate's project desk (M2 §4a).

Writes a file to the target persona's project-overlay desk directory and
posts a DESK_HANDOFF protocol signal. The runner handles refreshing the
target's DeskIndex on its next iteration.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agents.tools.base import BaseTool

if TYPE_CHECKING:
    from core.workspace import Workspace

_logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({
    "design-schema", "convention", "api-contract", "security-req",
    "playbook", "runbook", "reference", "example",
})
_VALID_PRIORITIES = frozenset({"high", "normal", "low"})
_DOC_NAME_RE = __import__("re").compile(r"^[a-z0-9_-]+$")


class DeskPushTool(BaseTool):
    name = "desk-push"
    priority = 122
    when_to_use = "hand a finished document to a teammate's project desk"
    description = "Hand a document to a teammate's project desk. Use when you have a doc they should follow."
    input_schema = {
        "type": "object",
        "properties": {
            "target_role": {"type": "string", "description": "Role that should receive the doc"},
            "doc_name": {"type": "string", "description": "Filename slug (lowercase, hyphens ok), no .md extension"},
            "body": {"type": "string", "description": "Markdown body of the document"},
            "kind": {"type": "string", "description": "design-schema|convention|api-contract|security-req|playbook|runbook|reference|example", "default": "reference"},
            "priority": {"type": "string", "description": "high|normal|low", "default": "high"},
            "triggers": {"type": "array", "items": {"type": "string"}, "description": "Keywords that auto-inject this doc"},
            "note": {"type": "string", "description": "One-line note for the recipient (≤12 words)"},
        },
        "required": ["target_role", "doc_name", "body"],
    }

    def __init__(
        self,
        sender_role: str,
        project_id: str,
        project_desks_root: Path | str,
        workspace: "Workspace | None" = None,
        # Maps role → persona_id — resolved at runtime by runner.
        role_to_persona: dict[str, str] | None = None,
    ):
        self._sender = sender_role
        self._project_id = project_id
        self._desks_root = Path(project_desks_root)
        self._workspace = workspace
        self._role_to_persona = role_to_persona or {}

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Push a doc to a teammate's project desk.",
            "args": (
                "target_role: str, doc_name: str, body: str, "
                "kind: str = 'reference', priority: str = 'high', "
                "triggers: list[str] = [], note: str = ''"
            ),
            "emits": "DESK_HANDOFF protocol message + writes file to target's project overlay",
            "errors": "TARGET_NOT_FOUND, DOC_NAME_INVALID",
            "example": 'ACTION desk-push target_role="backend" doc_name="auth-schema" kind="design-schema" priority="high" note="Follow this for /auth endpoints"',
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target_role = str(arguments.get("target_role", "")).strip()
        doc_name = str(arguments.get("doc_name", "")).strip().lower().replace(" ", "-")
        body = str(arguments.get("body", "")).strip()
        kind = str(arguments.get("kind", "reference")).strip()
        priority = str(arguments.get("priority", "high")).strip()
        triggers_raw = arguments.get("triggers") or []
        triggers = [str(t).strip().lower() for t in triggers_raw if str(t).strip()]
        note = str(arguments.get("note", "")).strip()

        # Validate
        if not target_role:
            return {"error": "TARGET_NOT_FOUND", "detail": "target_role is required"}
        if not doc_name or not _DOC_NAME_RE.match(doc_name):
            return {"error": "DOC_NAME_INVALID", "detail": "doc_name must be lowercase alphanumeric/hyphens only"}
        if not body:
            return {"error": "body is required"}
        if kind not in _VALID_KINDS:
            kind = "reference"
        if priority not in _VALID_PRIORITIES:
            priority = "high"

        # Resolve target persona_id
        target_persona_id = self._role_to_persona.get(target_role, target_role)

        # Warn on upward push (sender is lower tier than target by naming convention)
        # Simple heuristic: non-reviewer pushing to architect or tech_lead.
        if target_role in ("architect", "tech_lead") and self._sender not in ("architect", "tech_lead", "meta"):
            _logger.warning(
                "desk-push: upward push from %s → %s (doc: %s); succeeding but logging",
                self._sender, target_role, doc_name,
            )

        # Build frontmatter
        now_ts = time.time()
        if not triggers:
            # Auto-extract keywords from H1/H2 headings in body
            import re
            headings = re.findall(r"^#{1,2}\s+(.+)", body, re.MULTILINE)
            triggers = list({
                w.lower() for h in headings
                for w in re.findall(r"[a-z][a-z0-9_]*", h.lower())
                if len(w) >= 3
            })[:6]

        trigger_str = "[" + ", ".join(f'"{t}"' for t in triggers) + "]"
        tags = f'["handoff", "from:{self._sender}"]'
        frontmatter = (
            f"---\n"
            f"name: {doc_name}\n"
            f"triggers: {trigger_str}\n"
            f"kind: {kind}\n"
            f"priority: {priority}\n"
            f"tags: {tags}\n"
            f"handoff_from: {self._sender}\n"
            f"handoff_at: {now_ts:.3f}\n"
            f"handoff_note: {note}\n"
            f"---\n"
        )
        file_content = frontmatter + body

        # Write to target's project overlay handoffs dir
        target_dir = self._desks_root / target_persona_id / "handoffs"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{doc_name}.md"
        try:
            dest.write_text(file_content, encoding="utf-8")
        except OSError as e:
            return {"error": f"write failed: {e}"}

        storage_path = str(dest)

        # Post DESK_HANDOFF signal to board
        if self._workspace is not None:
            from core.signals import Signal, SignalType
            import json as _json
            payload = _json.dumps({
                "type": "DESK_HANDOFF",
                "from_role": self._sender,
                "target_role": target_role,
                "doc_name": doc_name,
                "kind": kind,
                "priority": priority,
                "note": note[:60],
                "storage_path": storage_path,
            })
            self._workspace.post_signal(Signal(
                type=SignalType.PROTOCOL,
                summary=f"DESK_HANDOFF {self._sender}→{target_role}: {doc_name}",
                from_agent=self._sender,
                detail=payload,
            ))

        return {
            "ok": True,
            "storage_path": storage_path,
            "target_role": target_role,
            "doc_name": doc_name,
            "triggers_set": triggers,
        }
