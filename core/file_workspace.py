"""FileWorkspace — the only sanctioned filesystem interface for agents.

All agent file I/O goes through this class. It is scoped to a single run's
workspace directory and rejects every form of path escape: absolute paths,
`..` traversal, and symlinks pointing outside the root.

Layout (DESIGN §17):
    {root}/
    ├── inputs/        # read-only at run start: source files, uploads
    ├── working/       # rw: agents work here, includes .git/
    └── deliverables/  # rw: promoted final outputs

By default, all relative paths resolve under `working/`. The `inputs` and
`deliverables` subtrees are addressed via dedicated methods (`read_input`,
`promote`).

DESIGN rules enforced here:
- §17: scope to run dir, signal references files don't contain them
- §29: path escape is the #2 threat — `_safe_path` is mandatory on every op
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class PathSafetyError(ValueError):
    """Raised when a path attempts to escape the workspace root."""


class FileWorkspace:
    """Run-scoped filesystem helper.

    Construct with the run's workspace root (e.g.
    ``data/runs/{run_id}/workspace``). The constructor creates the three
    standard subdirectories if they don't already exist.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.working = self.root / "working"
        self.inputs = self.root / "inputs"
        self.deliverables = self.root / "deliverables"
        for d in (self.working, self.inputs, self.deliverables):
            d.mkdir(exist_ok=True)

    # ── Path safety ─────────────────────────────────────────────────────────

    def _safe_path(self, path: str, base: Path) -> Path:
        """Resolve ``path`` under ``base`` and confirm it stays inside.

        Rejects:
          - absolute paths
          - `..` traversal that escapes ``base``
          - symlinks whose target lies outside ``base``
        """
        if path is None:
            raise PathSafetyError("path must not be None")
        p = Path(path)
        if p.is_absolute():
            raise PathSafetyError(f"absolute paths not allowed: {path!r}")
        if p.drive or p.root:
            raise PathSafetyError(f"rooted/drive paths not allowed: {path!r}")

        candidate = (base / p)
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise PathSafetyError(f"could not resolve path {path!r}: {e}") from e

        base_resolved = base.resolve(strict=False)
        if not _is_relative_to(resolved, base_resolved):
            raise PathSafetyError(
                f"path {path!r} resolves outside workspace root"
            )
        return resolved

    def absolute(self, path: str = "") -> Path:
        """Return the absolute path for a working-dir-relative path.

        Use only when a tool genuinely needs an absolute path on disk
        (e.g. Playwright pointing at an HTML file). Other operations should
        use the read/write helpers.
        """
        return self._safe_path(path, self.working) if path else self.working

    # ── Async read/write API (matches DESIGN §17) ───────────────────────────

    async def read(self, path: str) -> str:
        full = self._safe_path(path, self.working)
        return await asyncio.to_thread(full.read_text, encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        full = self._safe_path(path, self.working)
        await asyncio.to_thread(full.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(full.write_text, content, encoding="utf-8")

    async def append(self, path: str, content: str) -> None:
        full = self._safe_path(path, self.working)
        await asyncio.to_thread(full.parent.mkdir, parents=True, exist_ok=True)

        def _append() -> None:
            with full.open("a", encoding="utf-8") as f:
                f.write(content)

        await asyncio.to_thread(_append)

    async def read_binary(self, path: str) -> bytes:
        full = self._safe_path(path, self.working)
        return await asyncio.to_thread(full.read_bytes)

    async def write_binary(self, path: str, content: bytes) -> None:
        full = self._safe_path(path, self.working)
        await asyncio.to_thread(full.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(full.write_bytes, content)

    async def list(self, path: str = "", recursive: bool = False) -> list[str]:
        """List files under `path` relative to working/. Returns paths
        relative to working/. Directories are NOT included; only files."""
        base = self._safe_path(path, self.working) if path else self.working
        if not await asyncio.to_thread(base.exists):
            return []

        def _walk() -> list[str]:
            if recursive:
                items = [p for p in base.rglob("*") if p.is_file()]
            else:
                items = [p for p in base.iterdir() if p.is_file()]
            return sorted(str(p.relative_to(self.working)).replace("\\", "/") for p in items)

        return await asyncio.to_thread(_walk)

    async def exists(self, path: str) -> bool:
        try:
            full = self._safe_path(path, self.working)
        except PathSafetyError:
            return False
        return await asyncio.to_thread(full.exists)

    async def delete(self, path: str) -> None:
        full = self._safe_path(path, self.working)

        def _delete() -> None:
            if full.is_dir():
                shutil.rmtree(full)
            elif full.exists():
                full.unlink()

        await asyncio.to_thread(_delete)

    # ── Inputs (read-only at runtime) ───────────────────────────────────────

    async def read_input(self, path: str) -> str:
        full = self._safe_path(path, self.inputs)
        return await asyncio.to_thread(full.read_text, encoding="utf-8")

    async def list_inputs(self, recursive: bool = True) -> list[str]:
        if not await asyncio.to_thread(self.inputs.exists):
            return []

        def _walk() -> list[str]:
            if recursive:
                items = [p for p in self.inputs.rglob("*") if p.is_file()]
            else:
                items = [p for p in self.inputs.iterdir() if p.is_file()]
            return sorted(str(p.relative_to(self.inputs)).replace("\\", "/") for p in items)

        return await asyncio.to_thread(_walk)

    # ── Deliverables (promoted final outputs) ───────────────────────────────

    async def promote(
        self, working_path: str, deliverable_path: str | None = None
    ) -> None:
        """Copy a file from working/ to deliverables/.

        If `deliverable_path` is None, the file is placed at the same
        relative path inside deliverables/. The reviewer agent calls this
        when work is approved (DESIGN §17).
        """
        src = self._safe_path(working_path, self.working)
        if not await asyncio.to_thread(src.is_file):
            raise FileNotFoundError(f"working path is not a file: {working_path!r}")
        dst = self._safe_path(
            deliverable_path if deliverable_path is not None else working_path,
            self.deliverables,
        )

        def _copy() -> None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        await asyncio.to_thread(_copy)


def _is_relative_to(p: Path, base: Path) -> bool:
    """Backport-style helper: True if p is base or under base.
    Path.is_relative_to exists in 3.9+ but is also fine to inline for clarity."""
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False
