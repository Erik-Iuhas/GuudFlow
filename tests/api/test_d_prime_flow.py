"""End-to-end smoke test for Phase D-prime — full ingestion flow.

This test drives the **public HTTP API** to assemble a project's inputs
(uploads + git source repo + prior-run attachment), then runs the
worker's pre-flight initializer against the same data root and verifies
the run's workspace reflects everything correctly.

Why this exists:
  Unit tests prove each layer works in isolation. They do **not** prove
  the layers fit together — that the route writes a metadata sidecar in
  exactly the shape the initializer reads, that path conventions agree,
  that the README generator finds what the routes wrote, etc. Any drift
  in those joints is a real bug; this test is the seam check that
  surfaces them.

Flow:
  1. Create project via POST /api/projects.
  2. Upload a spec via POST /inputs/upload.
  3. Import a git source repo via POST /inputs/git
     (clone is monkey-patched to populate the dest with controlled files).
  4. Stage a prior run on disk (with a real git history in working/) and
     attach it via POST /inputs/from_run/{id} with feedback.
  5. Worker._initialize_run runs against the same data root for a new
     run id.
  6. Verify the new run's workspace/inputs/ contains uploads, source_repo
     + sidecar, from_run/deliverables + feedback.md + sidecar, and a
     README.md indexing everything (including the bundled history).
  7. Verify workspace/working/ is seeded from source_repo with a fresh
     git history.
  8. Verify the prior run's history is bundled at
     inputs/from_run_{prior}/.history.bundle.

Reuses fixtures from tests/api/conftest.py: client, auth_header, data_root.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from git import Repo

from core import source_repo as source_repo_lib
from worker.job_runner import JobRunner
from worker.queue import InMemoryJobQueue, Job


def _seed_prior_run_on_disk(runs_root: Path, run_id: str, project_id: str) -> Path:
    """Materialise a fully-formed prior run: meta.json + deliverables/ +
    a working/ with real git history (so bundle_prior_history has
    something to bundle)."""
    run_dir = runs_root / run_id
    deliverables = run_dir / "workspace" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "shipped.py").write_text("print('first ship')\n", encoding="utf-8")

    (run_dir / "meta.json").write_text(
        json.dumps({
            "run_id": run_id,
            "project_id": project_id,
            "status": "complete",
            "created_at": "2026-04-01T00:00:00Z",
        }),
        encoding="utf-8",
    )

    working = run_dir / "workspace" / "working"
    working.mkdir()
    repo = Repo.init(working, initial_branch="main")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "test")
        cfg.set_value("user", "email", "test@example.com")
        cfg.set_value("commit", "gpgsign", "false")
    (working / "v1.py").write_text("# v1\n", encoding="utf-8")
    repo.index.add(["v1.py"])
    repo.index.commit("v1 ships")
    return run_dir


async def test_full_dprime_flow_user_assembles_inputs_and_run_sees_them(
    data_root: Path, client, auth_header, monkeypatch
):
    """Drive the full D-prime flow via HTTP, then run the worker
    initializer and assert everything connects."""
    PROJECT_ID = "p_full_flow"
    PRIOR_RUN_ID = "run_v1"
    NEW_RUN_ID = "run_v2"

    # ── 1. Create project ──────────────────────────────────────────────
    r = await client.post(
        "/api/projects",
        json={"project_id": PROJECT_ID, "goal": "ship v2 with refresh tokens", "agents": []},
        headers=auth_header,
    )
    assert r.status_code == 201, r.text

    # ── 2. Upload a spec ───────────────────────────────────────────────
    spec_body = b"# Spec v2\nAdd refresh tokens.\n"
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/upload",
        files=[("files", ("spec.md", spec_body, "text/markdown"))],
        headers=auth_header,
    )
    assert r.status_code == 200, r.text

    # ── 3. Import a git source repo (clone mocked) ─────────────────────
    async def fake_clone(url, branch, shallow, dest):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("# upstream\n", encoding="utf-8")
        (dest / "src").mkdir()
        (dest / "src" / "auth.py").write_text("# old auth\n", encoding="utf-8")
        # Note: a real clone leaves a .git dir here. seed_working_from_source
        # ignores .git when it copies into working/, so we don't need to
        # fake one — but if we did, the test would still pass.

    monkeypatch.setattr(source_repo_lib, "clone_git_repo", fake_clone)

    git_url = "https://example.com/example/web-app.git"
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/git",
        json={"git_url": git_url, "branch": "main"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text

    # ── 4. Stage a prior run on disk + attach with feedback ────────────
    _seed_prior_run_on_disk(data_root / "runs", PRIOR_RUN_ID, PROJECT_ID)
    feedback_text = "Token expiry was missing. Add an exp claim and a refresh endpoint."
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/from_run/{PRIOR_RUN_ID}",
        json={"feedback": feedback_text},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text

    # Sanity: GET /inputs reports everything we just assembled.
    r = await client.get(f"/api/projects/{PROJECT_ID}/inputs")
    assert r.status_code == 200
    body = r.json()
    assert [u["filename"] for u in body["uploads"]] == ["spec.md"]
    assert body["source_repo"]["kind"] == "git_url"
    assert body["source_repo"]["git_url"] == git_url
    assert [p["run_id"] for p in body["prior_runs"]] == [PRIOR_RUN_ID]
    assert body["prior_runs"][0]["has_feedback"] is True

    # ── 5. Worker pre-flight for a new run ─────────────────────────────
    new_run_root = data_root / "runs" / NEW_RUN_ID
    new_run_root.mkdir(parents=True)

    worker = JobRunner(
        queue=InMemoryJobQueue(),
        project_loader=lambda _: None,  # unused for _initialize_run
        data_root=data_root,
    )
    job = Job(job_id="j_full", project_id=PROJECT_ID, run_id=NEW_RUN_ID)
    worker._initialize_run(job, new_run_root)

    # ── 6. Verify workspace/inputs ─────────────────────────────────────
    inputs = new_run_root / "workspace" / "inputs"

    # 6a. Uploaded spec is intact byte-for-byte.
    assert (inputs / "uploads" / "spec.md").read_bytes() == spec_body

    # 6b. Source repo content + the sidecar that the route wrote.
    assert (inputs / "source_repo" / "README.md").read_text(encoding="utf-8") == "# upstream\n"
    assert (inputs / "source_repo" / "src" / "auth.py").exists()
    src_meta = json.loads(
        (inputs / "source_repo.meta.json").read_text(encoding="utf-8")
    )
    assert src_meta["kind"] == "git_url"
    assert src_meta["git_url"] == git_url
    assert src_meta["branch"] == "main"

    # 6c. Prior run deliverables, feedback, and sidecar.
    fr = inputs / f"from_run_{PRIOR_RUN_ID}"
    assert (fr / "deliverables" / "shipped.py").read_text(encoding="utf-8") == \
        "print('first ship')\n"
    assert feedback_text in (fr / "feedback.md").read_text(encoding="utf-8")
    fr_meta = json.loads(
        (inputs / f"from_run_{PRIOR_RUN_ID}.meta.json").read_text(encoding="utf-8")
    )
    assert fr_meta["run_id"] == PRIOR_RUN_ID
    assert fr_meta["has_feedback"] is True

    # 6d. README indexes every concrete artifact (uploads, source origin,
    #     prior-run deliverables + feedback + bundle).
    readme = (inputs / "README.md").read_text(encoding="utf-8")
    assert "uploads/spec.md" in readme
    assert git_url in readme
    assert f"from_run_{PRIOR_RUN_ID}/deliverables/" in readme
    assert "feedback.md" in readme
    assert ".history.bundle" in readme

    # ── 7. working/ is seeded from source_repo with a fresh git ────────
    working = new_run_root / "workspace" / "working"
    assert (working / "README.md").read_text(encoding="utf-8") == "# upstream\n"
    assert (working / "src" / "auth.py").exists()
    assert (working / ".git").is_dir()

    seeded_repo = Repo(working)
    assert not seeded_repo.head.is_detached
    assert seeded_repo.head.ref.name == "main"
    log = list(seeded_repo.iter_commits("main"))
    assert len(log) == 1, "expected exactly the seed commit on main"
    assert "seed working/" in log[0].message

    # ── 8. Prior run history bundled ───────────────────────────────────
    bundle = inputs / f"from_run_{PRIOR_RUN_ID}" / ".history.bundle"
    assert bundle.is_file()
    assert bundle.stat().st_size > 0


async def test_full_dprime_flow_greenfield_no_inputs(
    data_root: Path, client, auth_header
):
    """Companion smoke: a project with NO assembled inputs still
    initialises cleanly. The README announces 'greenfield', no working/
    is created, and nothing else under inputs/ is left behind."""
    PROJECT_ID = "p_blank"
    NEW_RUN_ID = "run_blank"

    r = await client.post(
        "/api/projects",
        json={"project_id": PROJECT_ID, "goal": "build x from scratch", "agents": []},
        headers=auth_header,
    )
    assert r.status_code == 201

    # No uploads, no git import, no prior run attached.
    r = await client.get(f"/api/projects/{PROJECT_ID}/inputs")
    assert r.status_code == 200
    assert r.json() == {"uploads": [], "source_repo": None, "prior_runs": []}

    new_run_root = data_root / "runs" / NEW_RUN_ID
    new_run_root.mkdir(parents=True)
    worker = JobRunner(
        queue=InMemoryJobQueue(),
        project_loader=lambda _: None,
        data_root=data_root,
    )
    worker._initialize_run(
        Job(job_id="j_blank", project_id=PROJECT_ID, run_id=NEW_RUN_ID),
        new_run_root,
    )

    inputs = new_run_root / "workspace" / "inputs"
    assert inputs.is_dir()
    # README + the empty uploads/ dir that ProjectStore materialised at
    # create time (Chunk 1 — seed_run mirrors project inputs/ verbatim,
    # so an empty uploads/ propagates). No source_repo, no from_run_*.
    assert sorted(p.name for p in inputs.iterdir()) == ["README.md", "uploads"]
    assert (inputs / "uploads").is_dir() and not list((inputs / "uploads").iterdir())
    readme = (inputs / "README.md").read_text(encoding="utf-8")
    assert "greenfield" in readme.lower()

    # No source_repo means no working/ either — the runner's GitWorkspace
    # will init that itself when the actual run starts.
    assert not (new_run_root / "workspace" / "working").exists()


async def test_full_dprime_flow_secret_in_source_blocks_then_force_strips(
    data_root: Path, client, auth_header, monkeypatch
):
    """The secret-scan refusal/strip path is the trickiest cross-layer
    interaction. Verify end-to-end: clone returns a tree with a `.env`
    file → 409 + findings → user retries with force=true → the import
    lands without the .env, the run sees the source_repo without the
    secret, and the README still indexes it."""
    PROJECT_ID = "p_secret"
    NEW_RUN_ID = "run_secret_post_strip"

    r = await client.post(
        "/api/projects",
        json={"project_id": PROJECT_ID, "goal": "x", "agents": []},
        headers=auth_header,
    )
    assert r.status_code == 201

    async def fake_clone_with_secret(url, branch, shallow, dest):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("# repo\n", encoding="utf-8")
        (dest / ".env").write_text("DB_PASS=hunter2\n", encoding="utf-8")
    monkeypatch.setattr(source_repo_lib, "clone_git_repo", fake_clone_with_secret)

    # First attempt: refused with findings.
    git_url = "https://example.com/leaky/repo.git"
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/git",
        json={"git_url": git_url},
        headers=auth_header,
    )
    assert r.status_code == 409
    findings = r.json()["findings"]
    assert any(f["path"] == ".env" for f in findings)
    # Storage rolled back — no source_repo dir, no sidecar.
    assert not (data_root / "projects" / PROJECT_ID / "inputs" / "source_repo").exists()
    assert not (
        data_root / "projects" / PROJECT_ID / "inputs" / "source_repo.meta.json"
    ).exists()

    # Retry with force=true.
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/git",
        json={"git_url": git_url, "force": True},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert any(w["path"] == ".env" for w in r.json()["warnings"])

    # Initialise a run and verify .env did not land in workspace/working/
    # or workspace/inputs/source_repo/.
    new_run_root = data_root / "runs" / NEW_RUN_ID
    new_run_root.mkdir(parents=True)
    worker = JobRunner(
        queue=InMemoryJobQueue(),
        project_loader=lambda _: None,
        data_root=data_root,
    )
    worker._initialize_run(
        Job(job_id="j_secret", project_id=PROJECT_ID, run_id=NEW_RUN_ID),
        new_run_root,
    )

    inputs = new_run_root / "workspace" / "inputs"
    assert (inputs / "source_repo" / "README.md").exists()
    assert not (inputs / "source_repo" / ".env").exists()

    working = new_run_root / "workspace" / "working"
    assert (working / "README.md").exists()
    assert not (working / ".env").exists()
