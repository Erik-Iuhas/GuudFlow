"""What every worker was told (worker context plan A).

At every spawn the runner records the turn's brief, the names of the system prompt's layers,
the rules files and the skills the role was offered, in
`<run_root>/briefs/<role>/<nnn>-<card>.md`. Studio reads them back ("What it was told").

The system prompt's text is not saved: it can hold recalled persona memory, which never leaves
the persona store (CLAUDE.md, persona memory rules). Its digest and layer names are enough to
tell two prompts apart. The brief and the rules files are GuudFlow's own text (anything
untrusted in them is already wrapped).
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DIR = "briefs"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_NAME = re.compile(r"^(\d{3,})-(.+)\.md$")


@dataclass(frozen=True)
class BriefEntry:
    """One recorded turn."""

    n: int
    role: str
    card: str
    file: str
    at: float
    size: int


def _slug(text: str) -> str:
    return _SAFE.sub("_", text).strip("_")[:60] or "turn"


class BriefLog:
    """The briefs of one run."""

    def __init__(self, run_root: Path) -> None:
        self.root = Path(run_root) / DIR

    def _dir(self, role: str) -> Path:
        name = _slug(role)
        if name != role:
            raise ValueError(f"not a role name: {role!r}")
        return self.root / name

    def record(self, role: str, card: str | None, brief: str, *,
               session: str = "fresh", prompt_digest: str = "",
               prompt_layers: Sequence[str] = (),
               rules: Mapping[str, str] | None = None,
               skills: Sequence[str] = ()) -> Path:
        """Write one turn's record and return its path."""
        folder = self._dir(role)
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob("*.md"))) + 1
        path = folder / f"{n:03d}-{_slug(card or 'turn')}.md"
        head = [f"# {role} · {card or 'turn'} · turn {n}",
                f"- at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}",
                f"- session: {session}"]
        if prompt_digest:
            head.append(f"- system prompt: {prompt_digest[:12]} "
                        f"(layers: {', '.join(prompt_layers) or 'none'})")
        if skills:
            head.append(f"- skills offered: {', '.join(skills)}")
        parts = ["\n".join(head)]
        for name, text in (rules or {}).items():
            parts.append(f"## Rules file: {name}\n\n{text.strip()}")
        parts.append(f"## Brief\n\n{brief.strip()}")
        path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        return path

    def entries(self, role: str) -> list[BriefEntry]:
        """The role's recorded turns, oldest first."""
        folder = self._dir(role)
        out: list[BriefEntry] = []
        if not folder.is_dir():
            return out
        for path in sorted(folder.glob("*.md")):
            found = _NAME.match(path.name)
            if not found or path.is_symlink():
                continue
            st = path.stat()
            out.append(BriefEntry(int(found.group(1)), role, found.group(2), path.name,
                                  st.st_mtime, st.st_size))
        return out

    def roles(self) -> list[str]:
        """Roles with at least one recorded turn."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink())

    def read(self, role: str, n: int) -> str | None:
        """One recorded turn's text, or None."""
        for entry in self.entries(role):
            if entry.n == n:
                return (self._dir(role) / entry.file).read_text(encoding="utf-8")
        return None


__all__ = ["BriefEntry", "BriefLog"]
