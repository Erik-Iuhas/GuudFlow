"""Tests for core.run_initializer — DESIGN §33 run pre-flight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from git import Repo

from core import run_initializer


# ── seed_run ───────────────────────────────────────────────────────────────


def _make_project_inputs(project_dir: Path) -> None:
    """Materialise a project's inputs/ tree for tests."""
    inputs = project_dir / "inputs"
    (inputs / "uploads").mkdir(parents=True)
    (inputs / "uploads" / "spec.txt").write_text("spec body\n", encoding="utf-8")

    (inputs / "source_repo").mkdir()
    (inputs / "source_repo" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (inputs / "source_repo.meta.json").write_text(
        json.dumps({"kind": "git_url", "git_url": "https://example.com/r.git", "branch": "main"}),
        encoding="utf-8",
    )

    (inputs / "from_run_xyz").mkdir()
    (inputs / "from_run_xyz" / "deliverables").mkdir()
    (inputs / "from_run_xyz" / "deliverables" / "out.txt").write_text("done\n", encoding="utf-8")
    (inputs / "from_run_xyz" / "feedback.md").write_text("be nicer\n", encoding="utf-8")
    (inputs / "from_run_xyz.meta.json").write_text(
        json.dumps({"kind": "prior_run", "run_id": "xyz", "has_feedback": True}),
        encoding="utf-8",
    )


def test_seed_run_copies_uploads(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_project_inputs(project_dir)
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)

    inputs = run_root / "workspace" / "inputs"
    assert (inputs / "uploads" / "spec.txt").read_text(encoding="utf-8") == "spec body\n"


def test_seed_run_copies_source_repo_with_meta(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_project_inputs(project_dir)
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)

    inputs = run_root / "workspace" / "inputs"
    assert (inputs / "source_repo" / "main.py").read_text(encoding="utf-8") == "print(1)\n"
    meta = json.loads((inputs / "source_repo.meta.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "git_url"


def test_seed_run_copies_prior_run(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_project_inputs(project_dir)
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)

    inputs = run_root / "workspace" / "inputs"
    assert (inputs / "from_run_xyz" / "deliverables" / "out.txt").exists()
    assert (inputs / "from_run_xyz" / "feedback.md").read_text(encoding="utf-8") == "be nicer\n"
    assert (inputs / "from_run_xyz.meta.json").exists()


def test_seed_run_idempotent(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_project_inputs(project_dir)
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)
    # Modify a copied file; second seed_run should overwrite it back.
    target = run_root / "workspace" / "inputs" / "uploads" / "spec.txt"
    target.write_text("tampered", encoding="utf-8")

    run_initializer.seed_run(project_dir, run_root)
    assert target.read_text(encoding="utf-8") == "spec body\n"


def test_seed_run_empty_project(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)

    inputs = run_root / "workspace" / "inputs"
    assert inputs.is_dir()
    assert list(inputs.iterdir()) == []


def test_seed_run_with_no_inputs_dir(tmp_path: Path):
    """Project dir exists but has no inputs/ — still creates an empty run inputs/."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    run_root = tmp_path / "run"

    run_initializer.seed_run(project_dir, run_root)
    inputs = run_root / "workspace" / "inputs"
    assert inputs.is_dir()


# ── generate_inputs_readme ─────────────────────────────────────────────────


def test_generate_readme_lists_uploads(tmp_path: Path):
    inputs = tmp_path / "inputs"
    (inputs / "uploads").mkdir(parents=True)
    (inputs / "uploads" / "spec.pdf").write_bytes(b"x")
    (inputs / "uploads" / "mockup.png").write_bytes(b"x")

    run_initializer.generate_inputs_readme(inputs)
    body = (inputs / "README.md").read_text(encoding="utf-8")
    assert "uploads/spec.pdf" in body
    assert "uploads/mockup.png" in body


def test_generate_readme_lists_git_source(tmp_path: Path):
    inputs = tmp_path / "inputs"
    (inputs / "source_repo").mkdir(parents=True)
    (inputs / "source_repo" / "x.txt").write_text("x", encoding="utf-8")
    (inputs / "source_repo.meta.json").write_text(
        json.dumps({"kind": "git_url", "git_url": "https://example.com/r.git", "branch": "develop"}),
        encoding="utf-8",
    )

    run_initializer.generate_inputs_readme(inputs)
    body = (inputs / "README.md").read_text(encoding="utf-8")
    assert "https://example.com/r.git" in body
    assert "develop" in body


def test_generate_readme_lists_local_source(tmp_path: Path):
    inputs = tmp_path / "inputs"
    (inputs / "source_repo").mkdir(parents=True)
    (inputs / "source_repo" / "x").write_text("x", encoding="utf-8")
    (inputs / "source_repo.meta.json").write_text(
        json.dumps({"kind": "local_path", "path": "/home/dev/project"}),
        encoding="utf-8",
    )

    run_initializer.generate_inputs_readme(inputs)
    body = (inputs / "README.md").read_text(encoding="utf-8")
    assert "/home/dev/project" in body


def test_generate_readme_lists_prior_runs(tmp_path: Path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "from_run_abc.meta.json").write_text(
        json.dumps({"kind": "prior_run", "run_id": "abc", "has_feedback": True}),
        encoding="utf-8",
    )
    (inputs / "from_run_abc").mkdir()

    run_initializer.generate_inputs_readme(inputs)
    body = (inputs / "README.md").read_text(encoding="utf-8")
    assert "from_run_abc/deliverables/" in body
    assert "feedback.md" in body


def test_generate_readme_greenfield(tmp_path: Path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    run_initializer.generate_inputs_readme(inputs)
    body = (inputs / "README.md").read_text(encoding="utf-8")
    assert "greenfield" in body.lower()


def test_generate_readme_skips_when_dir_missing(tmp_path: Path):
    """No-op rather than crashing when inputs/ doesn't exist."""
    inputs = tmp_path / "missing"
    run_initializer.generate_inputs_readme(inputs)
    assert not inputs.exists()


# ── seed_working_from_source ───────────────────────────────────────────────


def test_seed_working_copies_and_inits_git(tmp_path: Path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (src / "README.md").write_text("# proj\n", encoding="utf-8")

    working = tmp_path / "working"
    run_initializer.seed_working_from_source(working, src)

    assert (working / "sub" / "main.py").exists()
    assert (working / "README.md").exists()
    assert (working / ".git").is_dir()

    repo = Repo(working)
    assert not repo.head.is_detached
    assert repo.head.ref.name == "main"
    log = list(repo.iter_commits("main"))
    assert len(log) == 1
    assert "seed working/" in log[0].message


def test_seed_working_strips_upstream_git(tmp_path: Path):
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "HEAD").write_text("ref: refs/heads/upstream\n", encoding="utf-8")
    (src / "main.py").write_text("print(1)\n", encoding="utf-8")

    working = tmp_path / "working"
    run_initializer.seed_working_from_source(working, src)

    # Working has its own .git (we just init'd), but it should NOT contain the
    # upstream HEAD content.
    assert (working / ".git").is_dir()
    head = (working / ".git" / "HEAD").read_text(encoding="utf-8")
    assert "upstream" not in head


def test_seed_working_no_source(tmp_path: Path):
    """Nothing happens when source_repo doesn't exist (greenfield)."""
    working = tmp_path / "working"
    run_initializer.seed_working_from_source(working, tmp_path / "nope")
    assert not working.exists()


def test_seed_working_replaces_existing(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fresh.txt").write_text("new", encoding="utf-8")

    working = tmp_path / "working"
    working.mkdir()
    (working / "stale.txt").write_text("old", encoding="utf-8")

    run_initializer.seed_working_from_source(working, src)
    assert (working / "fresh.txt").exists()
    assert not (working / "stale.txt").exists()


# ── bundle_prior_history ───────────────────────────────────────────────────


def _seed_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(repo_dir, initial_branch="main")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "test")
        cfg.set_value("user", "email", "test@example.com")
        cfg.set_value("commit", "gpgsign", "false")
    (repo_dir / "x.txt").write_text("x", encoding="utf-8")
    repo.index.add(["x.txt"])
    repo.index.commit("init")


def test_bundle_prior_history_creates_bundle(tmp_path: Path):
    prior = tmp_path / "prior_working"
    _seed_repo(prior)

    bundle_path = tmp_path / "out" / "history.bundle"
    run_initializer.bundle_prior_history(prior, bundle_path)

    assert bundle_path.is_file()
    assert bundle_path.stat().st_size > 0


def test_bundle_prior_history_no_git(tmp_path: Path):
    """No-op when prior_run has no .git/."""
    prior = tmp_path / "no_git"
    prior.mkdir()
    bundle_path = tmp_path / "out" / "history.bundle"
    run_initializer.bundle_prior_history(prior, bundle_path)
    assert not bundle_path.exists()
