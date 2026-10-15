"""The Code tab's read-only view (plan §15): project, run and role sources."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.code_browser import CodeBrowserError, Source, read_file, tree
from core.git_workspace import GitWorkspace
from core.harness.git_roles import RoleClones
from core.project_repo import ProjectRepo


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(work: Path, files: dict[str, str], msg: str = "feat: x") -> None:
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", msg)


@pytest.fixture
def project(tmp_path: Path) -> tuple[ProjectRepo, str, Path]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@x")
    _commit(seed, {"README.md": "# app\n", "web/src/gen.js": "a\nb\n", ".github/ci.yml": "x\n"})
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(seed)
    run = tmp_path / "run"
    repo.clone_into(run)
    _git(run, "config", "user.name", "t")
    _git(run, "config", "user.email", "t@x")
    _commit(run, {"web/src/gen.js": "a\nB\nc\n", "web/src/new.js": "n\n"})
    (run / "README.md").unlink()
    _git(run, "commit", "-qam", "chore: drop readme")
    return repo, base, run


def test_project_source_lists_main_without_protected_paths(project) -> None:
    repo, _, _ = project
    t = tree(Source.project(repo.path))
    assert [e.path for e in t.entries] == ["README.md", "web/src/gen.js"]
    assert all(e.status == "" for e in t.entries)
    f = read_file(Source.project(repo.path), "web/src/gen.js")
    assert f.text == "a\nb\n" and f.size == 4
    with pytest.raises(CodeBrowserError):
        read_file(Source.project(repo.path), ".github/ci.yml")
    with pytest.raises(CodeBrowserError):
        read_file(Source.project(repo.path), "../../etc/passwd")


def test_run_source_marks_changes_against_the_base(project) -> None:
    _, base, run = project
    src = Source.run(run, base)
    marks = {e.path: (e.status, e.added, e.removed) for e in tree(src).entries}
    assert marks == {"README.md": ("D", 0, 1), "web/src/gen.js": ("M", 2, 1),
                     "web/src/new.js": ("A", 1, 0)}
    f = read_file(src, "web/src/gen.js", with_diff=True)
    assert f.text == "a\nB\nc\n" and "-b" in (f.diff or "") and "+B" in (f.diff or "")
    assert read_file(src, "README.md").deleted


def test_role_source_reads_the_worktree_including_untracked_files(tmp_path: Path) -> None:
    main = GitWorkspace(tmp_path / "working")
    main.init()
    clones = RoleClones(main, tmp_path / "roles")
    work = clones.ensure("dev", "Bram")
    (work / "app.js").write_text("one\ntwo\n")
    (work / "bin.dat").write_bytes(b"\0\1\2")
    src = Source.role(clones.git_dir("dev"), work)
    marks = {e.path: e.status for e in tree(src).entries}
    assert marks["app.js"] == "A" and marks["bin.dat"] == "A"
    f = read_file(src, "app.js", with_diff=True)
    assert f.text == "one\ntwo\n" and "+two" in (f.diff or "")
    assert read_file(src, "bin.dat").binary
    (work / "link.js").symlink_to("/etc/hostname")
    with pytest.raises(CodeBrowserError):
        read_file(src, "link.js")
