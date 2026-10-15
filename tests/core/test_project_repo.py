"""Tests for core.project_repo (plan §13): runs build on the project, humans accept them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.json_store import file_lock, locked_json, read_json, JsonStoreBusy
from core.project_repo import ProjectRepo, ProjectRepoConflict, ProjectRepoError


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", f"feat: {name}")


def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@x")
    _commit(seed, "README.md", "# app\n")
    return seed


def test_seeded_once_and_runs_start_from_main_with_history(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    main = repo.ensure(_seed(tmp_path))
    assert repo.exists() and repo.ensure(None) == main          # idempotent
    work = tmp_path / "run1"
    assert repo.clone_into(work) == main
    assert (work / "README.md").read_text() == "# app\n"
    assert "feat: README.md" in _git(work, "log", "--format=%s")  # history kept
    assert _git(work, "remote") == ""                              # nothing to push to


def test_empty_project_gets_a_first_commit(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    repo.ensure(None)
    work = tmp_path / "run1"
    repo.clone_into(work)
    assert "start the project repo" in _git(work, "log", "--format=%s")


def test_submit_accept_reaches_main_and_the_next_run_builds_on_it(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(_seed(tmp_path))
    work = tmp_path / "run1"
    repo.clone_into(work)
    _commit(work, "app.py", "print('v1')\n")
    run = repo.submit("run_1", work, base)
    assert run.state == "pending" and run.commits == 1 and run.files == ["app.py"]
    assert repo.sha("main") == base                                 # not merged yet
    assert "+print('v1')" in repo.changes("run_1")
    accepted = repo.accept("run_1", note="looks good")
    assert accepted.state == "accepted" and accepted.merged == repo.sha("main")
    nxt = tmp_path / "run2"
    repo.clone_into(nxt)
    assert (nxt / "app.py").read_text() == "print('v1')\n"
    with pytest.raises(ProjectRepoError):
        repo.accept("run_1")                                        # decided already


def test_reject_leaves_main_alone(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(None)
    work = tmp_path / "run1"
    repo.clone_into(work)
    _commit(work, "bad.py", "x\n")
    repo.submit("run_1", work, base)
    assert repo.reject("run_1", "wrong approach").state == "rejected"
    assert repo.sha("main") == base
    assert repo.get("run_1").note == "wrong approach"  # type: ignore[union-attr]


def test_run_without_commits_is_not_pending(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(None)
    work = tmp_path / "run1"
    repo.clone_into(work)
    assert repo.submit("run_1", work, base).state == "no_changes"
    with pytest.raises(ProjectRepoError):
        repo.accept("run_1")


def test_conflicting_accept_is_refused_and_main_untouched(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(_seed(tmp_path))
    a, b = tmp_path / "a", tmp_path / "b"
    repo.clone_into(a)
    repo.clone_into(b)
    _commit(a, "README.md", "# from a\n")
    _commit(b, "README.md", "# from b\n")
    repo.submit("run_a", a, base)
    repo.submit("run_b", b, base)
    repo.accept("run_a")
    main = repo.sha("main")
    with pytest.raises(ProjectRepoConflict) as err:
        repo.accept("run_b")
    assert err.value.files == ["README.md"]
    assert repo.sha("main") == main and repo.get("run_b").state == "pending"  # type: ignore[union-attr]


def test_continuing_a_pending_run_starts_from_its_branch(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(None)
    first = tmp_path / "run1"
    repo.clone_into(first)
    _commit(first, "a.py", "a\n")
    repo.submit("run_1", first, base)
    second = tmp_path / "run2"
    head = repo.clone_into(second, run_id="run_1")
    assert head == repo.sha("runs/run_1") and (second / "a.py").exists()
    _commit(second, "b.py", "b\n")
    run2 = repo.submit("run_2", second, head, parent_run="run_1")
    assert run2.files == ["b.py"] and run2.parent_run == "run_1"
    repo.accept("run_2")                       # brings run_1's commit in too
    final = tmp_path / "check"
    repo.clone_into(final)
    assert (final / "a.py").exists() and (final / "b.py").exists()


def test_bad_run_ids_are_refused(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(None)
    work = tmp_path / "w"
    repo.clone_into(work)
    with pytest.raises(ProjectRepoError):
        repo.submit("../main", work, base)


def test_locked_json_round_trip_and_busy(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    with locked_json(path, {}) as doc:
        doc["a"] = 1
    assert read_json(path, {}) == {"a": 1}
    with file_lock(path):
        with pytest.raises(JsonStoreBusy):
            with file_lock(path, wait=0.1):
                pass
    try:
        with locked_json(path, {}) as doc:
            doc["a"] = 2
            raise ValueError("boom")
    except ValueError:
        pass
    assert read_json(path, {}) == {"a": 1}      # not saved on error


def test_the_code_folder_follows_main_and_never_overwrites_local_work(tmp_path: Path) -> None:
    repo = ProjectRepo(tmp_path / "data", "p")
    base = repo.ensure(_seed(tmp_path))
    assert repo.refresh_code() == "created" and (repo.code_path() / "README.md").is_file()
    work = tmp_path / "run1"
    repo.clone_into(work)
    _commit(work, "app.py", "print('v1')\n")
    repo.submit("run_1", work, base)
    repo.accept("run_1")
    assert repo.refresh_code() == "updated"
    assert (repo.code_path() / "app.py").read_text() == "print('v1')\n"
    (repo.code_path() / "app.py").write_text("my edit\n")          # someone works in the copy
    assert repo.refresh_code() == "not updated: the copy has local changes"
    assert (repo.code_path() / "app.py").read_text() == "my edit\n"
