"""Card-to-card handoffs: carry just enough from one card to the next (plan §11.1).

Each card starts a fresh opencode session. What it needs from earlier cards comes from
handoffs: the worker's own note (`AgentResult.handoff`, written when it submits) plus
facts GuudFlow reads from git (files changed with line counts, exported names). A
handoff is stored on its card's thread as a `handoff` entry, so it survives restarts and
shows in Studio's card drawer.

Which handoffs a brief gets (`select_handoffs`):
- builders: the cards this card depends on, the role's own most recent handoff, and up
  to two done cards that changed files this card expects to touch;
- reviewers: the handoff of the card under review.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.harness.brief import wrap_untrusted
from core.harness.results import AgentResult, Handoff

if TYPE_CHECKING:
    from core.harness.task_board import TaskBoard

log = logging.getLogger(__name__)

MAX_FILES = 12          # files listed in the facts
MAX_EXPORTS = 15        # exported names listed in the facts
MAX_OVERLAP = 2         # extra handoffs picked because they touched the same files
_CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")

_JS_EXPORT = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(function\*?|class|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)\s*(\([^)]*\))?", re.MULTILINE)
_JS_ARROW = re.compile(r"=\s*(?:async\s*)?(\([^)]*\))\s*=>")
_PY_DEF = re.compile(r"^(?:async\s+)?(def|class)\s+([A-Za-z]\w*)\s*(\([^)]*\))?", re.MULTILINE)


@dataclass(frozen=True)
class HostFacts:
    """What GuudFlow itself knows about a card's change (the model cannot get these wrong)."""

    files: tuple[tuple[str, int, int], ...] = ()   # (path, lines added, lines removed)
    exports: tuple[str, ...] = ()                  # "path: name(params)"

    def to_dict(self) -> dict[str, Any]:  # Any: JSON payload
        """JSON form stored on the thread entry."""
        return {"files": [list(f) for f in self.files], "exports": list(self.exports)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostFacts":  # Any: JSON payload
        """Rebuild from :meth:`to_dict` output."""
        files = tuple((str(f[0]), int(f[1]), int(f[2])) for f in data.get("files", [])
                      if isinstance(f, list) and len(f) == 3)
        return cls(files=files, exports=tuple(str(e) for e in data.get("exports", [])))


def exported_names(path: str, text: str) -> list[str]:
    """Top-level exported names of a JS/TS or Python file, with parameters when cheap."""
    names: list[str] = []
    if path.endswith(".py"):
        for _kind, name, params in _PY_DEF.findall(text):
            if not name.startswith("_"):
                names.append(f"{name}{params or ''}")
    else:
        for match in _JS_EXPORT.finditer(text):
            _kind, name, params = match.groups()
            if not params:
                arrow = _JS_ARROW.match(text, match.end())
                params = arrow.group(1) if arrow else ""
            names.append(f"{name}{params or ''}")
    # Functions (with parameters) first: they are the contract; constants come last.
    return sorted(names, key=lambda n: "(" not in n)


def host_facts(numstat: Sequence[tuple[str, int, int]],
               read: Callable[[str], str]) -> HostFacts:
    """Facts for one merged change: files with line counts, and exported names of code files."""
    files = tuple(numstat[:MAX_FILES])
    exports: list[str] = []
    for path, _added, _removed in numstat:
        if len(exports) >= MAX_EXPORTS or not path.endswith(_CODE_SUFFIXES):
            continue
        if "/test" in f"/{path}" or path.startswith("tests/"):
            continue
        for name in exported_names(path, read(path))[:5]:
            exports.append(f"{path}: {name}")
    return HostFacts(files=files, exports=tuple(exports[:MAX_EXPORTS]))


def changed_exports(numstat: Sequence[tuple[str, int, int]],
                    read: Callable[[str], str],
                    read_before: Callable[[str], str] | None = None) -> dict[str, list[str]]:
    """The exported names a merge added to the code files it changed (no caps; tests skipped),
    for the design drift check (role contracts plan §4.2). With `read_before` (the file at the
    merge's base), names the file already exported are left out: drift is what this change
    introduced, not what an adopted codebase always had."""
    out: dict[str, list[str]] = {}
    for path, _added, _removed in numstat:
        if not path.endswith(_CODE_SUFFIXES) or "/test" in f"/{path}" or path.startswith("tests/"):
            continue
        try:
            text = read(path)
        except Exception as e:  # noqa: BLE001 — a deleted file has no exports
            log.debug("no exports for %s: %s", path, e)
            continue
        names = exported_names(path, text)
        if read_before is not None and names:
            try:
                before = set(exported_names(path, read_before(path)))
            except Exception:  # noqa: BLE001 — a new file exported nothing before
                before = set()
            names = [n for n in names if n not in before]
        if names:
            out[path] = names
    return out


def fallback_handoff(result: AgentResult) -> Handoff:
    """A worker that skipped the handoff still leaves its summary for the next card."""
    return Handoff(changed=result.summary if len(result.summary) >= 10
                   else f"{result.summary} (no handoff written)")


@dataclass(frozen=True)
class CardHandoff:
    """One stored handoff, as read back from a card thread."""

    task_id: str
    title: str
    author: str
    handoff: Handoff
    facts: HostFacts = field(default_factory=HostFacts)
    source: str = "worker"            # "worker" or "fallback"

    def files(self) -> set[str]:
        """Paths this card changed."""
        return {f[0] for f in self.facts.files}


def thread_data(result: AgentResult, facts: HostFacts) -> tuple[str, dict[str, Any]]:
    """The thread entry text and data for a done result (Any: JSON payload)."""
    source = "worker" if result.handoff is not None else "fallback"
    handoff = result.handoff or fallback_handoff(result)
    text = (f"handoff: {len(facts.files)} file(s), {len(handoff.decisions)} decision(s)"
            + (" (no handoff written; summary used)" if source == "fallback" else ""))
    return text, {"handoff": handoff.model_dump(), "facts": facts.to_dict(), "source": source}


def stored_handoffs(board: "TaskBoard") -> list[CardHandoff]:
    """Every handoff on the board, oldest first (a card's rework adds a newer one)."""
    out: list[tuple[int, int, CardHandoff]] = []
    for order, task in enumerate(board.workspace.tasks.values()):
        for pos, entry in enumerate(task.thread):
            data = entry.get("data") or {}
            if entry.get("kind") != "handoff" or "handoff" not in data:
                continue
            try:
                handoff = Handoff.model_validate(data["handoff"])
            except ValueError:
                continue
            out.append((int(entry.get("iteration") or 0), order * 1000 + pos, CardHandoff(
                task_id=task.task_id, title=task.title, author=str(entry.get("author", "")),
                handoff=handoff, facts=HostFacts.from_dict(data.get("facts") or {}),
                source=str(data.get("source", "worker")))))
    return [c for _i, _o, c in sorted(out, key=lambda t: (t[0], t[1]))]


def latest_per_card(handoffs: Sequence[CardHandoff]) -> dict[str, CardHandoff]:
    """The newest handoff of each card."""
    return {h.task_id: h for h in handoffs}


def select_handoffs(board: "TaskBoard", role: str, task_id: str, *,
                    reviewer: bool = False) -> list[tuple[str, CardHandoff]]:
    """(why, handoff) pairs for one card turn, most relevant first, each card at most once."""
    all_handoffs = stored_handoffs(board)
    latest = latest_per_card(all_handoffs)
    card = board.card(task_id)
    if reviewer:
        own = latest.get(task_id)
        return [("the work you are reviewing", own)] if own else []
    picked: list[tuple[str, CardHandoff]] = []
    seen: set[str] = set()

    def add(why: str, h: CardHandoff | None) -> None:
        if h is not None and h.task_id not in seen:
            picked.append((why, h))
            seen.add(h.task_id)

    for dep in card.depends_on:
        add("this card depends on it", latest.get(dep))
    mine = [h for h in all_handoffs if h.author == role]
    if mine:
        add("your last card" if mine[-1].task_id != task_id else "your earlier work on this card",
            latest.get(mine[-1].task_id))
    hint = set(card.files_hint)
    if hint:
        overlap = [h for h in reversed(list(latest.values()))
                   if h.task_id not in seen and h.task_id != task_id and h.files() & hint]
        for h in overlap[:MAX_OVERLAP]:
            add("it changed files this card touches", h)
    return picked


def render_handoffs(picked: Sequence[tuple[str, CardHandoff]],
                    names: dict[str, str] | None = None) -> str:
    """The brief section body. Worker text is untrusted; git facts are GuudFlow's own."""
    names = names or {}
    blocks = []
    for why, h in picked:
        who = names.get(h.author, h.author)
        lines = [f"### {h.task_id}: {h.title} (by {who}; {why})"]
        if h.facts.files:
            lines.append("Files changed: " + ", ".join(f"`{p}` (+{a}/-{r})"
                                                       for p, a, r in h.facts.files))
        body = [f"Changed: {h.handoff.changed}"]
        if h.handoff.decisions:
            body.append("Decisions and gotchas:\n" + "\n".join(f"- {d}" for d in h.handoff.decisions))
        if h.handoff.tests:
            body.append(f"Tests: {h.handoff.tests}")
        if h.handoff.next:
            body.append(f"For the next card: {h.handoff.next}")
        if h.facts.exports:
            body.append("Exported names in the changed code:\n"
                        + "\n".join(f"- {e}" for e in h.facts.exports))
        lines.append(wrap_untrusted(f"handoff from {who} on {h.task_id}", "\n".join(body)))
        blocks.append("\n".join(lines))
    return ("These are short handoffs from earlier cards, instead of their full history. "
            "The code is the truth: read the files they name before relying on them.\n\n"
            + "\n\n".join(blocks))

