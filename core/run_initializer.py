"""Pre-flight initialisation for a run's workspace.

DESIGN §33. The runner calls these functions between "create the run dir"
and "construct AgentRunner". They turn the project's persistent inputs
(uploads, source repo, attached prior runs) into the populated
`workspace/inputs/` and `workspace/working/` directories that agents
read from.

Functions
---------
seed_run                : copy project inputs into the run's workspace/inputs/
generate_inputs_readme  : auto-generate inputs/README.md index
seed_working_from_source: copy source_repo into working/ + fresh git init
bundle_prior_history    : git bundle a prior run's working/ history
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import git
from git import GitCommandError, Repo


_logger = logging.getLogger(__name__)

_AUTHOR = git.Actor("agentflow", "bot@agentflow.local")


def _force_rw(func, path, _exc_info):
    """rmtree onerror — strip read-only and retry. Required on Windows
    because `.git/objects/*` files are marked read-only and would
    otherwise raise PermissionError mid-rmtree."""
    import os
    import stat

    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def seed_run(project_dir: Path, run_root: Path) -> None:
    """Copy `project_dir/inputs/*` into `run_root/workspace/inputs/`.

    Idempotent — wipes the target inputs dir before copying. No-ops cleanly
    when the project has nothing in inputs/.
    """
    project_inputs = project_dir / "inputs"
    run_inputs = run_root / "workspace" / "inputs"

    if run_inputs.exists():
        shutil.rmtree(run_inputs, onerror=_force_rw)
    run_inputs.mkdir(parents=True, exist_ok=True)

    if not project_inputs.is_dir():
        return

    for entry in sorted(project_inputs.iterdir()):
        target = run_inputs / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        elif entry.is_file():
            shutil.copy2(entry, target)


def generate_inputs_readme(inputs_dir: Path) -> None:
    """Write `inputs/README.md` summarising what's there. Per DESIGN §33,
    this is the first thing the meta-agent and downstream agents read."""
    if not inputs_dir.is_dir():
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        "# Run inputs\n",
        "\n",
        f"Generated {now_iso}. Read-only at runtime.\n",
        "\n",
        "## Provided\n",
        "\n",
    ]
    any_provided = False

    # source_repo
    src_meta_path = inputs_dir / "source_repo.meta.json"
    src_dir = inputs_dir / "source_repo"
    if src_meta_path.exists() and src_dir.is_dir():
        try:
            meta = json.loads(src_meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        kind = meta.get("kind")
        if kind == "git_url":
            lines.append(
                f"- `source_repo/` — cloned from {meta.get('git_url', '?')}"
                f" @ {meta.get('branch', 'main')}\n"
            )
        elif kind == "local_path":
            lines.append(
                f"- `source_repo/` — copied from local path "
                f"`{meta.get('path', '?')}`\n"
            )
        else:
            lines.append("- `source_repo/` — provided source tree\n")
        any_provided = True

    # uploads
    uploads = inputs_dir / "uploads"
    if uploads.is_dir():
        for f in sorted(p for p in uploads.iterdir() if p.is_file()):
            lines.append(f"- `uploads/{f.name}`\n")
            any_provided = True

    # from_run_*
    for meta_entry in sorted(inputs_dir.glob("from_run_*.meta.json")):
        try:
            meta = json.loads(meta_entry.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        run_id = meta.get("run_id", "?")
        lines.append(
            f"- `from_run_{run_id}/deliverables/` — deliverables of run `{run_id}`\n"
        )
        if meta.get("has_feedback"):
            lines.append(
                f"- `from_run_{run_id}/feedback.md` — user feedback to address\n"
            )
        history_bundle = inputs_dir / f"from_run_{run_id}" / ".history.bundle"
        if history_bundle.exists():
            lines.append(
                f"- `from_run_{run_id}/.history.bundle` — prior run's git history\n"
            )
        any_provided = True

    if not any_provided:
        lines.append("- (no inputs — greenfield run)\n")

    lines.append("\n## How agents should use this\n\n")
    lines.append(
        "The meta-agent reads this README first. Treat any `feedback.md` as "
        "the primary directive for changes. Source-repo content is read-only "
        "here; the run's mutable copy lives at `working/`.\n"
    )

    (inputs_dir / "README.md").write_text("".join(lines), encoding="utf-8")


def seed_working_from_source(working_dir: Path, source_repo_dir: Path) -> None:
    """If `source_repo_dir` exists, mirror its tree into `working/`, run
    `git init`, and make a single seed commit. Strips any upstream `.git/`
    so the run's history starts fresh (DESIGN §33 rule 85)."""
    if not source_repo_dir.is_dir():
        return

    if working_dir.exists():
        shutil.rmtree(working_dir, onerror=_force_rw)
    shutil.copytree(
        source_repo_dir,
        working_dir,
        ignore=shutil.ignore_patterns(".git"),
    )

    repo = Repo.init(working_dir, initial_branch="main")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", _AUTHOR.name)
        cfg.set_value("user", "email", _AUTHOR.email)
        cfg.set_value("commit", "gpgsign", "false")

    repo.git.add(A=True)
    # If the source was empty, the seed commit is empty too — that's fine.
    if repo.is_dirty(untracked_files=True) or not repo.head.is_valid():
        repo.index.commit(
            "chore: seed working/ from inputs/source_repo/",
            author=_AUTHOR,
            committer=_AUTHOR,
        )


def bundle_prior_history(prior_run_working_dir: Path, target_bundle_path: Path) -> None:
    """`git bundle create --all` against the prior run's `working/` repo.

    Best-effort: silently skips when the prior run has no git history.
    Used by the runner (Chunk 5) so a continuation has the prior repo
    archived under `inputs/from_run_{id}/.history.bundle`.
    """
    if not (prior_run_working_dir / ".git").is_dir():
        return

    target_bundle_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        repo = Repo(prior_run_working_dir)
        repo.git.bundle("create", str(target_bundle_path), "--all")
    except GitCommandError as e:
        _logger.warning(
            "bundle_prior_history: git bundle failed for %s: %s",
            prior_run_working_dir,
            e,
        )
