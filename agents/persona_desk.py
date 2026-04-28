"""Persona desk — trigger-based knowledge retrieval (M2).

Each persona has two desk layers:
  1. Persona-default:  data/personas/{id}/desk/         — travels across projects
  2. Project-overlay:  data/projects/{id}/persona_desks/{persona_id}/  — project-specific

Desk files are markdown with YAML frontmatter (lifted from OpenHands microagents).
Trigger-based auto-injection fires before every LLM call; explicit lookup via
the desk-search tool.

DESIGN §28 (context management): embeddings off by default; keyword/tag ranking used.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Auto-inject budget across both layers per task.
AUTO_INJECT_TOKEN_BUDGET = 2000
DEFAULT_MAX_INJECT_TOKENS = 800

_YAML_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.DOTALL)


# ── Frontmatter parsing ────────────────────────────────────────────────────


def _parse_yaml_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Never raises — logs warning and returns ({}, text)."""
    m = _YAML_FRONT_RE.match(text)
    if not m:
        return {}, text
    raw_yaml, body = m.group(1), m.group(2)
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(raw_yaml) or {}
    except Exception:
        # yaml not available — fall back to a minimal line-by-line parser.
        data = _simple_yaml(raw_yaml)
    return data if isinstance(data, dict) else {}, body


def _simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML parser for frontmatter (no deps required)."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):
            items = [s.strip().strip("\"'") for s in rest[1:-1].split(",") if s.strip()]
            out[key] = items
        else:
            out[key] = rest.strip("\"'")
    return out


# ── DeskFile ───────────────────────────────────────────────────────────────


@dataclass
class DeskFile:
    """One parsed desk file."""

    name: str
    triggers: list[str]
    body: str
    agents: list[str]           # empty = all roles
    priority: str               # "high" | "normal" | "low"
    kind: str                   # design-schema | convention | api-contract | …
    tags: list[str]
    max_inject_tokens: int
    layer: str                  # "persona" | "project"
    source_path: Path
    mtime: float = 0.0
    # desk-push metadata (§4a)
    handoff_from: str = ""
    handoff_at: float = 0.0
    handoff_note: str = ""

    @classmethod
    def from_path(cls, path: Path, layer: str) -> "DeskFile | None":
        """Load and validate a desk file. Returns None on invalid frontmatter."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            _logger.warning("desk: cannot read %s: %s", path, e)
            return None

        fm, body = _parse_yaml_frontmatter(text)

        name = fm.get("name") or path.stem
        triggers_raw = fm.get("triggers", [])
        if not triggers_raw:
            _logger.warning("desk: %s missing triggers — skipped", path)
            return None
        triggers = [str(t).strip() for t in (triggers_raw if isinstance(triggers_raw, list) else [triggers_raw])]

        agents_raw = fm.get("agents", [])
        agents = [str(a).strip() for a in (agents_raw if isinstance(agents_raw, list) else [agents_raw])] if agents_raw else []

        tags_raw = fm.get("tags", [])
        tags = [str(t).strip() for t in (tags_raw if isinstance(tags_raw, list) else [tags_raw])] if tags_raw else []

        priority = fm.get("priority", "normal")
        if priority not in ("high", "normal", "low"):
            priority = "normal"

        kind = fm.get("kind", "reference")
        max_inject_tokens = int(fm.get("max_inject_tokens", DEFAULT_MAX_INJECT_TOKENS))

        handoff_from = str(fm.get("handoff_from", ""))
        handoff_at_raw = fm.get("handoff_at", 0.0)
        try:
            handoff_at = float(handoff_at_raw) if handoff_at_raw else 0.0
        except (TypeError, ValueError):
            handoff_at = 0.0
        handoff_note = str(fm.get("handoff_note", ""))

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        return cls(
            name=name,
            triggers=triggers,
            body=body.strip(),
            agents=agents,
            priority=priority,
            kind=kind,
            tags=tags,
            max_inject_tokens=max_inject_tokens,
            layer=layer,
            source_path=path,
            mtime=mtime,
            handoff_from=handoff_from,
            handoff_at=handoff_at,
            handoff_note=handoff_note,
        )

    def matches_role(self, role: str) -> bool:
        return not self.agents or role in self.agents

    def trigger_count(self, task_lower: str) -> int:
        """Count how many triggers match (word-boundary substring) in task_lower."""
        count = 0
        for trigger in self.triggers:
            if re.search(r"\b" + re.escape(trigger.lower()) + r"\b", task_lower):
                count += 1
        return count

    @property
    def priority_rank(self) -> int:
        return {"high": 2, "normal": 1, "low": 0}.get(self.priority, 1)

    @property
    def layer_rank(self) -> int:
        return 1 if self.layer == "project" else 0


# ── Token estimation ───────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


# ── DeskIndex ─────────────────────────────────────────────────────────────


class DeskIndex:
    """Merged two-layer desk index for one persona on one project.

    Load with `DeskIndex.build(persona_dir, project_desk_dir)`.
    Both dirs are optional — absent layer simply contributes no files.
    """

    def __init__(self, files: list[DeskFile]):
        self._files = files

    @classmethod
    def build(
        cls,
        persona_desk_dir: Path | str | None,
        project_desk_dir: Path | str | None = None,
    ) -> "DeskIndex":
        files: list[DeskFile] = []

        def _load_layer(d: Path | str | None, layer: str) -> None:
            if d is None:
                return
            d = Path(d)
            if not d.is_dir():
                return
            for p in sorted(d.rglob("*.md")):
                df = DeskFile.from_path(p, layer)
                if df is not None:
                    files.append(df)

        _load_layer(persona_desk_dir, "persona")
        _load_layer(project_desk_dir, "project")

        return cls(files)

    def is_empty(self) -> bool:
        return len(self._files) == 0

    def search(self, query: str, max_tokens: int = DEFAULT_MAX_INJECT_TOKENS, role: str = "") -> list[tuple[DeskFile, str]]:
        """Keyword search. Returns (file, snippet) pairs within max_tokens budget."""
        query_lower = query.lower()
        query_tokens = set(re.findall(r"[a-z][a-z0-9_]*", query_lower))

        scored: list[tuple[float, DeskFile]] = []
        for df in self._files:
            if role and not df.matches_role(role):
                continue
            body_lower = df.body.lower()
            # Score by TF-like overlap: count query tokens in body
            score = sum(body_lower.count(tok) for tok in query_tokens if len(tok) >= 2)
            if score > 0:
                scored.append((score, df))

        scored.sort(key=lambda x: x[0], reverse=True)

        result: list[tuple[DeskFile, str]] = []
        budget = max_tokens
        for _, df in scored:
            snippet = _truncate_to_tokens(df.body, min(df.max_inject_tokens, budget))
            if not snippet:
                continue
            result.append((df, snippet))
            budget -= _estimate_tokens(snippet)
            if budget <= 0:
                break
        return result

    def auto_inject(self, task: str, role: str = "") -> list[tuple[DeskFile, str]]:
        """Return triggered files ranked by priority > layer > trigger-count > mtime.
        Total tokens capped at AUTO_INJECT_TOKEN_BUDGET.
        """
        task_lower = task.lower()

        candidates: list[tuple[DeskFile, int]] = []
        for df in self._files:
            if role and not df.matches_role(role):
                continue
            count = df.trigger_count(task_lower)
            if count > 0:
                candidates.append((df, count))

        # Sort: priority desc, layer desc (project > persona), trigger-count desc, mtime desc
        candidates.sort(
            key=lambda x: (x[0].priority_rank, x[0].layer_rank, x[1], x[0].mtime),
            reverse=True,
        )

        result: list[tuple[DeskFile, str]] = []
        budget = AUTO_INJECT_TOKEN_BUDGET
        for df, _ in candidates:
            snippet = _truncate_to_tokens(df.body, min(df.max_inject_tokens, budget))
            if not snippet:
                continue
            result.append((df, snippet))
            budget -= _estimate_tokens(snippet)
            if budget <= 0:
                break
        return result


# ── Block renderer ─────────────────────────────────────────────────────────


def render_desk_inject_block(items: list[tuple[DeskFile, str]]) -> str:
    """Format the auto-injected block prepended to the agent's user turn."""
    if not items:
        return ""
    lines = ["=== YOUR DESK NOTES (auto-loaded) ==="]
    for df, snippet in items:
        header_parts = [f"file: {df.name}.md"]
        matched = [t for t in df.triggers]
        if matched:
            header_parts.append(f"triggers matched: {', '.join(matched[:3])}")
        if df.handoff_from:
            header_parts.append(f"HANDED OVER by {df.handoff_from}")
        lines.append(f"[{', '.join(header_parts)}]")
        if df.handoff_note:
            lines.append(f"note: {df.handoff_note}")
        lines.append(snippet)
        lines.append("")
    lines.append("=== END DESK NOTES ===")
    return "\n".join(lines)


# ── Token truncation ───────────────────────────────────────────────────────


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens, stopping at a sentence boundary."""
    if _estimate_tokens(text) <= max_tokens:
        return text
    limit_chars = max_tokens * 4
    truncated = text[:limit_chars]
    # Walk back to last sentence boundary
    for sep in (". ", ".\n", "\n\n", "\n"):
        idx = truncated.rfind(sep)
        if idx > limit_chars // 2:
            return truncated[: idx + len(sep)].rstrip()
    return truncated.rstrip()
