"""Track 4.3 — runner wires the agent working tree to storage_base when
working_tree_mode is "in-place".

In isolated mode (default), agents work inside `<run_root>/workspace/working/`.
In in-place mode + in-directory storage, they work directly in the user-chosen
directory. Inputs/deliverables stay under run_root either way for run isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import storage
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


@pytest.fixture(autouse=True)
def _reset_storage_cache():
    storage.reset_storage_config_for_tests()
    yield
    storage.reset_storage_config_for_tests()


def _make_runner(tmp_path: Path, run_root: Path | None = None) -> AgentRunner:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    rbac = {"dev": RoleRBAC(role="dev", allowed_scopes=[])}
    proj = ProjectConfig(project_id="p_inplace", goal="t", agents=agents, rbac=rbac)
    return AgentRunner(
        project=proj, run_id="r_inplace_abc",
        run_root=(run_root or tmp_path / "run"), llm=MockLLMClient([]),
    )


def test_runner_isolated_mode_uses_run_root_workspace(monkeypatch, tmp_path: Path) -> None:
    """Default isolated mode: working dir is <run_root>/workspace/working/."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.delenv("GUUDFLOW_WORKING_TREE_MODE", raising=False)
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working
    assert runner.git.working_dir == expected_working


def test_runner_in_place_uses_storage_base_for_working_tree(
    monkeypatch, tmp_path: Path,
) -> None:
    """In in-place + in-directory mode, working dir is the user's chosen base."""
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()

    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(repo_dir))
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "in-place")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    run_root = tmp_path / "flow_runs" / "r1"
    runner = _make_runner(tmp_path, run_root=run_root)

    assert runner.files.working == repo_dir.resolve()
    assert runner.git.working_dir == repo_dir.resolve()
    # Inputs/deliverables stay under run_root for run isolation.
    assert runner.files.inputs == (run_root / "workspace" / "inputs").resolve()
    assert runner.files.deliverables == (run_root / "workspace" / "deliverables").resolve()
    # Repo gets initialised inside the user's directory.
    assert (repo_dir / ".git").exists()


def test_runner_in_place_reuses_existing_repo(monkeypatch, tmp_path: Path) -> None:
    """If the chosen directory is already a git repo, the runner reuses it."""
    from git import Repo
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    pre_repo = Repo.init(repo_dir, initial_branch="main")
    (repo_dir / "README.md").write_text("hi")
    pre_repo.index.add(["README.md"])
    pre_repo.index.commit("seed")
    pre_sha = pre_repo.heads["main"].commit.hexsha

    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(repo_dir))
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "in-place")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    run_root = tmp_path / "flow_runs" / "r1"
    runner = _make_runner(tmp_path, run_root=run_root)

    # Existing main commit preserved — no fresh init clobbered the repo.
    assert runner.git.repo.heads["main"].commit.hexsha == pre_sha


def test_runner_global_mode_ignores_in_place_setting(monkeypatch, tmp_path: Path) -> None:
    """In global mode, in-place is forced off — working dir stays under run_root."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "in-place")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working
