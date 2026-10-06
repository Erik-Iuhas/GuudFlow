"""The project record is committed to main on accept when the project opts in."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.project_repo import ProjectRepo, ProjectRepoError
from core.project_runs import decide_run, keeps_record_in_repo
from tests.spec_fixtures import seed_spec


def _submit(data_root: Path, run_id: str = "run_a", project_id: str = "p1") -> ProjectRepo:
    repo = ProjectRepo(data_root, project_id)
    base = repo.ensure(None)
    work = data_root / "tmp" / run_id
    repo.clone_into(work)
    (work / "a.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: a"], cwd=work, check=True)
    repo.submit(run_id, work, base)
    return repo


def _opt_in(data_root: Path, keep: bool, project_id: str = "p1") -> None:
    (data_root / "projects.json").write_text(json.dumps(
        {project_id: {"project_id": project_id, "goal": "g", "record": {"keep_in_repo": keep}}}))


def _main_files(repo: ProjectRepo) -> list[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=repo.path,
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def test_accept_writes_spec_and_decisions_when_opted_in(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    repo = _submit(tmp_path)
    _opt_in(tmp_path, True)

    decide_run(tmp_path, "p1", "run_a", accept=True)

    files = _main_files(repo)
    assert "SPEC.md" in files and "a.py" in files
    assert any(f.startswith("docs/decisions/0001-") for f in files)
    log = subprocess.run(["git", "log", "-1", "--format=%s", "main"], cwd=repo.path,
                         capture_output=True, text=True, check=True).stdout
    assert "project record" in log


def test_accept_leaves_the_repo_alone_without_opt_in(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    repo = _submit(tmp_path)
    _opt_in(tmp_path, False)
    assert not keeps_record_in_repo(tmp_path, "p1")

    decide_run(tmp_path, "p1", "run_a", accept=True)

    assert "SPEC.md" not in _main_files(repo)


def test_commit_files_is_a_no_op_when_nothing_changed_and_refuses_escapes(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path, "p1")
    repo.ensure(None)
    first = repo.commit_files({"SPEC.md": "# Spec\n"}, "docs: record")
    assert first is not None
    assert repo.commit_files({"SPEC.md": "# Spec\n"}, "docs: record") is None
    for bad in ("../x.md", "/etc/x", ".git/config"):
        with pytest.raises(ProjectRepoError):
            repo.commit_files({bad: "x"}, "docs: bad")
