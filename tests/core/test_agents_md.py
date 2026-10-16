"""The project's generated AGENTS.md (worker context plan R4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from core import agents_md
from core.project_repo import ProjectRepo
from core.work_db import WorkDB


def _seed(tmp_path: Path, agents: str | None = None) -> ProjectRepo:
    seed = tmp_path / "seed"
    seed.mkdir()
    run = lambda *a: subprocess.run(a, cwd=seed, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.name", "t")
    run("git", "config", "user.email", "t@x")
    (seed / "README.md").write_text("x\n")
    if agents is not None:
        (seed / "AGENTS.md").write_text(agents)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    repo = ProjectRepo(tmp_path / "data", "p")
    repo.ensure(seed)
    return repo


def _stack(tmp_path: Path) -> None:
    db = WorkDB(tmp_path / "data" / "projects" / "p")
    with db.spec_doc() as doc:
        doc["stack"] = {"language": "Python 3.12", "framework": "pygame"}
        doc["rules"] = [{"id": "rule-1", "text": "No globals", "strength": "binding"}]


def test_generated_agents_md_is_committed_and_rewritten(tmp_path: Path) -> None:
    repo = _seed(tmp_path)
    _stack(tmp_path)
    assert agents_md.write(tmp_path / "data", "p", "after run r1")
    text = agents_md.main_text(repo.path) or ""
    assert text.startswith(agents_md.MARKER) and "Python 3.12" in text and "No globals" in text
    assert not agents_md.person_written(text)


def test_a_person_written_agents_md_is_never_overwritten(tmp_path: Path) -> None:
    repo = _seed(tmp_path, agents="# Ours\nRun make test.\n")
    _stack(tmp_path)
    assert agents_md.write(tmp_path / "data", "p", "after run r1") is None
    assert agents_md.main_text(repo.path) == "# Ours\nRun make test.\n"
    assert agents_md.person_written("# Ours\n")


def test_nothing_to_say_writes_nothing(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert agents_md.write(tmp_path / "data", "p", "x") is None
