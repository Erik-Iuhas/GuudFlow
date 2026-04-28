"""JSON-backed storage for projects and runs.

Lightweight on-disk persistence. `data/projects.json` holds all project
configs as a dict keyed by project_id. Runs are already structured as
`data/runs/{run_id}/` from earlier stages.

Stage 6 ships file-based storage with an asyncio.Lock for write
serialisation. Stage later can swap for a real DB behind the same
contract.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


class ProjectStore:
    """File-backed project config storage.

    `data/projects.json` holds project configs (dict keyed by project_id).
    Per-project directories live alongside at `data/projects/{id}/` and hold
    inputs (uploads, source repo, prior-run continuations) — see DESIGN §33.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.path = self.root / "projects.json"
        self.projects_dir = self.root / "projects"
        self._lock = asyncio.Lock()

    def project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def inputs_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "inputs"

    def uploads_dir(self, project_id: str) -> Path:
        return self.inputs_dir(project_id) / "uploads"

    def source_repo_dir(self, project_id: str) -> Path:
        return self.inputs_dir(project_id) / "source_repo"

    def source_repo_meta_path(self, project_id: str) -> Path:
        return self.inputs_dir(project_id) / "source_repo.meta.json"

    def from_run_dir(self, project_id: str, run_id: str) -> Path:
        return self.inputs_dir(project_id) / f"from_run_{run_id}"

    def from_run_meta_path(self, project_id: str, run_id: str) -> Path:
        return self.inputs_dir(project_id) / f"from_run_{run_id}.meta.json"

    def roadmap_path(self, project_id: str) -> Path:
        """Phase K.0 — project context document. Free-form markdown
        the user owns; team reads it at run start. Optional first-
        class — projects without a roadmap behave exactly as today."""
        return self.project_dir(project_id) / "context" / "roadmap.md"

    def iter_from_runs(self, project_id: str) -> list[dict[str, Any]]:
        """Return metadata for every attached prior run, sorted newest-first."""
        inputs = self.inputs_dir(project_id)
        if not inputs.exists():
            return []
        out: list[dict[str, Any]] = []
        for entry in inputs.iterdir():
            if not entry.is_file() or not entry.name.startswith("from_run_"):
                continue
            if not entry.name.endswith(".meta.json"):
                continue
            try:
                out.append(json.loads(entry.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        out.sort(key=lambda m: m.get("attached_at", ""), reverse=True)
        return out

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            data = await self._read_locked()
            return list(data.values())

    async def get(self, project_id: str) -> dict[str, Any] | None:
        async with self._lock:
            data = await self._read_locked()
            return data.get(project_id)

    async def create(self, project: dict[str, Any]) -> dict[str, Any]:
        if "project_id" not in project:
            raise ValueError("project must include project_id")
        async with self._lock:
            data = await self._read_locked()
            if project["project_id"] in data:
                raise ValueError(f"project {project['project_id']!r} already exists")
            data[project["project_id"]] = project
            await self._write_locked(data)
            self.uploads_dir(project["project_id"]).mkdir(parents=True, exist_ok=True)
            return project

    async def update(self, project_id: str, project: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            data = await self._read_locked()
            if project_id not in data:
                return None
            project["project_id"] = project_id
            data[project_id] = project
            await self._write_locked(data)
            return project

    async def delete(self, project_id: str) -> bool:
        async with self._lock:
            data = await self._read_locked()
            if project_id not in data:
                return False
            del data[project_id]
            await self._write_locked(data)
            project_dir = self.project_dir(project_id)
            if project_dir.exists():
                shutil.rmtree(project_dir)
            return True

    async def _read_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    async def _write_locked(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, indent=2, default=str)
            tmp_name = tmp.name
        shutil.move(tmp_name, self.path)


class RunStore:
    """Lightweight run metadata accessor. Reads from
    `data/runs/{run_id}/meta.json` — written by the runner."""

    def __init__(self, runs_root: Path | str):
        self.runs_root = Path(runs_root)

    async def list(self, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not self.runs_root.exists():
            return []
        runs: list[dict[str, Any]] = []
        for run_dir in self.runs_root.iterdir():
            meta_path = run_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if project_id is not None and meta.get("project_id") != project_id:
                continue
            runs.append(meta)
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs[:limit]

    async def get(self, run_id: str) -> dict[str, Any] | None:
        meta_path = self.runs_root / run_id / "meta.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    async def get_board(self, run_id: str) -> list[dict[str, Any]] | None:
        board_path = self.runs_root / run_id / "board.json"
        if not board_path.exists():
            return None
        try:
            return json.loads(board_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def workspace_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id / "workspace"

    def deliverables_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id / "workspace" / "deliverables"


class PersonaStore:
    """Read-only persona metadata accessor."""

    def __init__(self, personas_root: Path | str):
        self.root = Path(personas_root)

    async def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        rosters: list[dict[str, Any]] = []
        for persona_dir in self.root.iterdir():
            if not persona_dir.is_dir() or persona_dir.name.startswith("_"):
                continue
            profile_path = persona_dir / "profile.json"
            data: dict[str, Any] = {}
            if profile_path.exists():
                try:
                    data = json.loads(profile_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            rosters.append(_summarise_persona(persona_dir, data))
        return rosters

    async def get(self, persona_id: str) -> dict[str, Any] | None:
        persona_dir = self.root / persona_id
        if not persona_dir.is_dir():
            return None
        profile_path = persona_dir / "profile.json"
        data: dict[str, Any] = {}
        if profile_path.exists():
            try:
                data = json.loads(profile_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return _summarise_persona(persona_dir, data)


_PERSONA_REQUIRED_FILES = ("profile.json", "episodes.jsonl", "patterns.json", "reputation.json", "style.json")


def _summarise_persona(persona_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    rep_path = persona_dir / "reputation.json"
    reputation: dict[str, Any] = {}
    if rep_path.exists():
        try:
            reputation = json.loads(rep_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reputation = {}

    episodes_path = persona_dir / "episodes.jsonl"
    n_episodes = 0
    if episodes_path.exists():
        n_episodes = sum(
            1 for _ in episodes_path.read_text(encoding="utf-8").splitlines()
            if _.strip()
        )

    missing = [f for f in _PERSONA_REQUIRED_FILES if not (persona_dir / f).exists()]

    return {
        "persona_id": profile.get("persona_id") or profile.get("id") or persona_dir.name,
        "name": profile.get("name", ""),
        "specialty": profile.get("specialty", ""),
        "character": profile.get("character", ""),
        "n_episodes": n_episodes,
        "reputation": reputation,
        "degraded": len(missing) > 0,
        "missing_files": missing,
    }
