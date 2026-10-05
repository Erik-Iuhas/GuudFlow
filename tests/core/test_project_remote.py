"""Tests for core.project_remote (plan §14.1): sync from and publish to the real remote."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from core.backlog import Backlog, CardOutcome
from core.project_remote import (
    RemoteConfig, RemoteError, _git_env, open_pull_request, publish, sync,
)
from core.project_repo import ProjectRepo
from core.project_runs import decide_run, prepare_run


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def _remote(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'GitHub' repo and a human's clone of it with one commit."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    human = tmp_path / "human"
    _git(tmp_path, "clone", "-q", str(bare), str(human))
    _commit(human, "README.md", "# app\n")
    _git(human, "push", "-q", "origin", "main")
    return bare, human


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", f"feat: {name}")


@pytest.mark.parametrize("raw", [
    {"url": "ext::sh -c touch% /tmp/pwned"}, {"url": "relative/path"},
    {"url": "/abs", "token_env": "HOME"}, {"url": "/abs", "publish": "pr"},
    {"url": "/abs", "branch": "../x"}, "nope"])
def test_bad_settings_are_refused(raw: object) -> None:
    with pytest.raises(RemoteError):
        RemoteConfig.from_settings(raw)


def test_seed_then_fast_forward_merge_and_conflict(tmp_path: Path) -> None:
    bare, human = _remote(tmp_path)
    cfg = RemoteConfig.from_settings({"url": str(bare)})
    assert cfg is not None
    repo = ProjectRepo(tmp_path / "data", "p")
    seeded = sync(repo, cfg)
    assert seeded.status == "seeded" and seeded.main == _git(human, "rev-parse", "HEAD")
    assert sync(repo, cfg).status == "up_to_date"
    _commit(human, "a.py", "a\n")
    _git(human, "push", "-q", "origin", "main")
    assert sync(repo, cfg).status == "fast_forward"
    # A run accepted locally, and the human pushes a different file: merged.
    work = tmp_path / "run1"
    base = repo.clone_into(work)
    _commit(work, "b.py", "b\n")
    repo.submit("run_1", work, base)
    repo.accept("run_1")
    _commit(human, "c.py", "c\n")
    _git(human, "push", "-q", "origin", "main")
    merged = sync(repo, cfg)
    assert merged.status == "merged"
    check = tmp_path / "check"
    repo.clone_into(check)
    assert all((check / f).exists() for f in ("a.py", "b.py", "c.py"))
    # Both change README: the sync is skipped, main untouched.
    work2 = tmp_path / "run2"
    base2 = repo.clone_into(work2)
    _commit(work2, "README.md", "# ours\n")
    repo.submit("run_2", work2, base2)
    repo.accept("run_2")
    _git(human, "pull", "-q", "--no-rebase", "origin", "main")
    _commit(human, "README.md", "# theirs\n")
    _git(human, "push", "-q", "origin", "main")
    before = repo.sha("main")
    conflict = sync(repo, cfg)
    assert conflict.status == "conflict" and "README.md" in conflict.detail
    assert repo.sha("main") == before


def test_unrelated_history_and_bad_url_are_errors(tmp_path: Path) -> None:
    bare, _ = _remote(tmp_path)
    repo = ProjectRepo(tmp_path / "data", "p")
    repo.ensure(None)                                   # started from a copy, not the remote
    result = sync(repo, RemoteConfig(url=str(bare)))
    assert result.status == "error" and "share no history" in result.detail
    other = ProjectRepo(tmp_path / "data", "q")
    assert sync(other, RemoteConfig(url=str(tmp_path / "missing.git"))).status == "error"
    assert not other.exists()                           # a failed seed leaves nothing


def test_accept_syncs_merges_and_publishes_a_branch(tmp_path: Path) -> None:
    bare, human = _remote(tmp_path)
    data = tmp_path / "data"
    cfg = RemoteConfig.from_settings({"url": str(bare), "publish": "branch"})
    run_root = data / "runs" / "run_1"
    working = run_root / "workspace" / "working"
    item = Backlog(data, "p").add("Feature", ["works"])
    prepare_run(data, "p", "run_1", run_root, working, {"backlog_items": [item.id]},
                in_place=False, remote=cfg)
    assert (working / "README.md").exists()                     # started from the remote
    _commit(working, "feature.py", "x\n")
    info = json.loads((run_root / "project_run.json").read_text())
    assert info["sync"]["status"] == "seeded"
    repo = ProjectRepo(data, "p")
    repo.submit("run_1", working, info["base"])
    Backlog(data, "p").record_outcomes("run_1", [CardOutcome("Feature", "done", item.id)])
    _commit(human, "human.py", "h\n")                           # the human kept working
    _git(human, "push", "-q", "origin", "main")
    decision = decide_run(data, "p", "run_1", accept=True, remote=cfg)
    assert decision.run.sync and decision.run.sync["status"] == "fast_forward"
    assert decision.run.published == {"status": "pushed", "ref": "guudflow/run_1", "url": "",
                                      "detail": ""}
    files = _git(bare, "ls-tree", "--name-only", "guudflow/run_1")
    assert {"README.md", "feature.py", "human.py"} <= set(files.split())
    assert _git(bare, "rev-parse", "main") == _git(human, "rev-parse", "HEAD")  # main untouched


def test_publish_main_is_never_forced(tmp_path: Path) -> None:
    bare, human = _remote(tmp_path)
    cfg = RemoteConfig(url=str(bare), publish="main")
    repo = ProjectRepo(tmp_path / "data", "p")
    sync(repo, cfg)
    work = tmp_path / "w"
    base = repo.clone_into(work)
    _commit(work, "x.py", "x\n")
    run = repo.submit("run_1", work, base)
    run = repo.accept("run_1")
    _commit(human, "y.py", "y\n")
    _git(human, "push", "-q", "origin", "main")                 # remote moved meanwhile
    result = publish(repo, cfg, run)
    assert result.status == "error" and "sync" in result.detail
    sync(repo, cfg)
    assert publish(repo, cfg, run).status == "pushed"
    assert "x.py" in _git(bare, "ls-tree", "--name-only", "main")


def test_pull_request_uses_the_host_token(monkeypatch) -> None:
    monkeypatch.setenv("GUUDFLOW_GIT_TOKEN", "ghp_secret")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"html_url": "https://github.com/acme/app/pull/7"})

    cfg = RemoteConfig.from_settings({"url": "https://github.com/acme/app.git", "publish": "pr",
                                      "token_env": "GUUDFLOW_GIT_TOKEN"})
    assert cfg is not None
    url = open_pull_request(cfg, head="guudflow/run_1", title="t", body="b",
                            http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert url.endswith("/pull/7") and seen["url"] == "https://api.github.com/repos/acme/app/pulls"
    assert seen["auth"] == "Bearer ghp_secret"
    assert seen["body"] == {"title": "t", "head": "guudflow/run_1", "base": "main", "body": "b"}


def test_token_reaches_git_only_through_askpass(monkeypatch) -> None:
    monkeypatch.setenv("GUUDFLOW_GIT_TOKEN", "ghp_secret")
    cfg = RemoteConfig(url="https://github.com/acme/app.git", token_env="GUUDFLOW_GIT_TOKEN")
    with _git_env(cfg) as env:
        assert "GUUDFLOW_GIT_TOKEN" not in env and env["GIT_TERMINAL_PROMPT"] == "0"
        askpass = env["GIT_ASKPASS"]
        assert "ghp_secret" not in Path(askpass).read_text()
        out = subprocess.run([askpass, "Password for https://github.com"], env=env,
                             capture_output=True, text=True).stdout.strip()
        assert out == "ghp_secret"
        assert subprocess.run([askpass, "Username for x"], env=env, capture_output=True,
                              text=True).stdout.strip() == "x-access-token"
    assert not Path(askpass).exists()
