"""Track 4.3 — runner wires the agent working tree to the user's directory
when the project's source repo is `local_path_in_place`.

Default (no source repo, or git_url, or local_path "isolated"): agents work
inside `<run_root>/workspace/working/`.

In-place: the runner reads `<project_dir>/inputs/source_repo.meta.json`,
sees `kind: "local_path_in_place"`, and points the working tree at the
recorded `path`. Inputs/deliverables still live under run_root for run
isolation.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


def _make_runner(
    tmp_path: Path,
    *,
    run_root: Path | None = None,
    project_dir: Path | None = None,
) -> AgentRunner:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    rbac = {"dev": RoleRBAC(role="dev", allowed_scopes=[])}
    proj = ProjectConfig(project_id="p_inplace", goal="t", agents=agents, rbac=rbac)
    return AgentRunner(
        project=proj, run_id="r_inplace_abc",
        run_root=(run_root or tmp_path / "run"), llm=MockLLMClient([]),
        project_dir=project_dir,
    )


def _write_source_meta(project_dir: Path, meta: dict) -> None:
    inputs_dir = project_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "source_repo.meta.json").write_text(
        json.dumps(meta), encoding="utf-8",
    )


def test_runner_isolated_default_uses_run_root_workspace(tmp_path: Path) -> None:
    """No source meta: working dir is <run_root>/workspace/working/."""
    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working
    assert runner.git.working_dir == expected_working


def test_runner_local_path_isolated_still_uses_run_root_workspace(
    tmp_path: Path,
) -> None:
    """Source kind `local_path` (the copy variant) does NOT shift the working
    tree — the worker copies into `<run>/workspace/working/` as before."""
    project_dir = tmp_path / "project"
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    _write_source_meta(project_dir, {"kind": "local_path", "path": str(repo_dir)})

    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root, project_dir=project_dir)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working


def test_runner_in_place_points_working_tree_at_user_dir(tmp_path: Path) -> None:
    """`local_path_in_place`: working dir = the user's chosen path."""
    project_dir = tmp_path / "project"
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    _write_source_meta(
        project_dir, {"kind": "local_path_in_place", "path": str(repo_dir)},
    )

    run_root = tmp_path / "flow_runs" / "r1"
    runner = _make_runner(tmp_path, run_root=run_root, project_dir=project_dir)

    assert runner.files.working == repo_dir.resolve()
    assert runner.git.working_dir == repo_dir.resolve()
    # Inputs / deliverables stay under run_root.
    assert runner.files.inputs == (run_root / "workspace" / "inputs").resolve()
    assert runner.files.deliverables == (run_root / "workspace" / "deliverables").resolve()
    # GitWorkspace.init() runs against the user's dir.
    assert (repo_dir / ".git").exists()


def test_runner_in_place_reuses_existing_repo(tmp_path: Path) -> None:
    """If the directory is already a git repo, the runner re-uses it
    (existing commits/branches preserved)."""
    from git import Repo
    project_dir = tmp_path / "project"
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    pre_repo = Repo.init(repo_dir, initial_branch="main")
    (repo_dir / "README.md").write_text("hi")
    pre_repo.index.add(["README.md"])
    with pre_repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "t@example.com")
    pre_repo.index.commit("seed")
    pre_sha = pre_repo.heads["main"].commit.hexsha

    _write_source_meta(
        project_dir, {"kind": "local_path_in_place", "path": str(repo_dir)},
    )
    run_root = tmp_path / "flow_runs" / "r1"
    runner = _make_runner(tmp_path, run_root=run_root, project_dir=project_dir)

    assert runner.git.repo.heads["main"].commit.hexsha == pre_sha


def test_runner_in_place_falls_back_when_path_missing(tmp_path: Path) -> None:
    """If the recorded path no longer exists, fall back to isolated mode
    rather than crashing — the meta file is advisory."""
    project_dir = tmp_path / "project"
    _write_source_meta(
        project_dir,
        {"kind": "local_path_in_place", "path": str(tmp_path / "gone")},
    )
    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root, project_dir=project_dir)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working


def test_runner_in_place_ignores_other_kinds(tmp_path: Path) -> None:
    """`git_url` source: working dir stays under run_root."""
    project_dir = tmp_path / "project"
    _write_source_meta(
        project_dir,
        {"kind": "git_url", "git_url": "https://x/y", "branch": "main"},
    )
    run_root = tmp_path / "run"
    runner = _make_runner(tmp_path, run_root=run_root, project_dir=project_dir)

    expected_working = (run_root / "workspace" / "working").resolve()
    assert runner.files.working == expected_working
