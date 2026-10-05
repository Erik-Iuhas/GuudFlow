"""The project repo's link to the real remote (plan §14.1).

Settings (`approval_gates.project_remote`):

    {"url": "https://github.com/acme/app.git" | "/abs/path/to/repo",
     "branch": "main",
     "publish": "none" | "branch" | "pr" | "main",
     "token_env": "GUUDFLOW_GIT_TOKEN"}

- **Sync** (before every run, and on demand): fetch the remote branch and bring it
  into the project repo's `main`: nothing to do, fast-forward, or a merge. A
  conflicting merge is reported and skipped, never forced.
- **Publish** (after a human accepts a run): `branch` pushes `main` to
  `guudflow/{run_id}`; `pr` does that and opens a GitHub pull request into
  `branch`; `main` pushes `main` to `branch` (never forced: a remote that moved
  means "sync first").

Safety: only https/http/file URLs or absolute paths are accepted (git's `ext::`
transport could run commands). The token is read on the host from an environment
variable whose name starts with `GUUDFLOW_GIT_`, and reaches git only through a
short-lived askpass helper, never a URL, an argument or a log line. Sandboxes never
see any of this: it all runs on the host, outside any run.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from core.project_repo import MAIN, ProjectRepo, RunBranch

Publish = Literal["none", "branch", "pr", "main"]
_TOKEN_ENV = re.compile(r"^GUUDFLOW_GIT_[A-Z0-9_]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
_GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "protocol.ext.allow=never", "-c", "credential.helper=")
_AUTHOR = ("-c", "user.name=GuudFlow", "-c", "user.email=runner@guudflow.local")


class RemoteError(ValueError):
    """Bad remote settings, or a git/GitHub operation on the remote failed."""


@dataclass(frozen=True)
class RemoteConfig:
    """A validated `project_remote` setting."""

    url: str
    branch: str = MAIN
    publish: Publish = "none"
    token_env: str | None = None

    @classmethod
    def from_settings(cls, raw: Any) -> "RemoteConfig | None":  # Any: settings JSON
        """None when no remote is configured; RemoteError when it is invalid."""
        if not raw:
            return None
        if not isinstance(raw, dict):
            raise RemoteError("project_remote must be an object")
        url = str(raw.get("url") or "").strip()
        if not (url.startswith(("https://", "http://", "file://")) or Path(url).is_absolute()):
            raise RemoteError("remote url must be https://, http://, file:// or an absolute path")
        if url.startswith("-") or "::" in url:
            raise RemoteError("remote url is not allowed")
        branch = str(raw.get("branch") or MAIN)
        if not _BRANCH.match(branch) or ".." in branch:
            raise RemoteError(f"invalid branch {branch!r}")
        publish = raw.get("publish") or "none"
        if publish not in ("none", "branch", "pr", "main"):
            raise RemoteError("publish must be none, branch, pr or main")
        token_env = raw.get("token_env") or None
        if token_env is not None and not _TOKEN_ENV.match(str(token_env)):
            raise RemoteError("token_env must name a variable starting with GUUDFLOW_GIT_")
        if publish == "pr" and not _GITHUB.match(url):
            raise RemoteError("publish 'pr' needs a https://github.com/owner/repo url")
        return cls(url=url, branch=branch, publish=publish, token_env=token_env)

    def token(self) -> str | None:
        """The token from the host environment (never stored)."""
        return os.environ.get(self.token_env) if self.token_env else None


@dataclass
class SyncResult:
    """What a sync did to the project's main."""

    status: Literal["seeded", "up_to_date", "fast_forward", "merged", "conflict", "error"]
    main: str = ""
    detail: str = ""
    at: float = 0.0


@dataclass
class PublishResult:
    """Where an accepted run went."""

    status: Literal["skipped", "pushed", "pr_opened", "error"]
    ref: str = ""
    url: str = ""
    detail: str = ""


@contextmanager
def _git_env(cfg: RemoteConfig) -> Iterator[dict[str, str]]:
    """Environment for git talking to the remote: no prompts, the token via askpass."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GUUDFLOW_GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
    token = cfg.token()
    if not token:
        yield env
        return
    with tempfile.TemporaryDirectory(prefix="guudflow-askpass-") as tmp:
        script = Path(tmp) / "askpass.sh"
        script.write_text('#!/bin/sh\ncase "$1" in\n  Username*) echo x-access-token ;;\n'
                          '  *) printf "%s\\n" "$GUUDFLOW_ASKPASS_TOKEN" ;;\nesac\n')
        script.chmod(stat.S_IRWXU)
        env.update(GIT_ASKPASS=str(script), GUUDFLOW_ASKPASS_TOKEN=token)
        yield env


def _git(env: dict[str, str] | None, cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *_SAFE, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)
    if proc.returncode != 0:
        raise RemoteError(f"git {args[0]} failed: {_redact(proc.stderr.strip())}")
    return proc.stdout.strip()


def _redact(text: str) -> str:
    """Strip anything that looks like credentials in a URL from git's messages."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)[:2000]


def sync(repo: ProjectRepo, cfg: RemoteConfig) -> SyncResult:
    """Bring the remote branch into the project's main. Never raises."""
    upstream = f"refs/remotes/upstream/{cfg.branch}"
    try:
        with repo.db.runs_doc():           # the same lock accept() holds: one writer of main
            seeding = not repo.exists()
            if seeding:                    # a remote-backed project starts from the remote
                repo.path.mkdir(parents=True, exist_ok=True)
                _git(None, repo.path, "init", "--quiet", "--bare", "-b", MAIN, str(repo.path))
            with _git_env(cfg) as env:
                _git(env, repo.path, "fetch", "--quiet", "--no-tags", cfg.url,
                     f"+{cfg.branch}:{upstream}")
            if seeding:
                _git(None, repo.path, "update-ref", f"refs/heads/{MAIN}", repo.sha(upstream))
                return SyncResult("seeded", repo.sha(MAIN), at=time.time())
            main, up = repo.sha(MAIN), repo.sha(upstream)
            if subprocess.run(["git", *_SAFE, "merge-base", main, up], cwd=repo.path,
                              capture_output=True).returncode != 0:
                return SyncResult("error", main, detail=(
                    "the project repo and the remote share no history (the project was "
                    "started from a copy). Main was left as it was."), at=time.time())
            if _is_ancestor(repo, up, main):
                return SyncResult("up_to_date", main, at=time.time())
            if _is_ancestor(repo, main, up):
                _git(None, repo.path, "update-ref", f"refs/heads/{MAIN}", up, main)
                return SyncResult("fast_forward", up, at=time.time())
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "sync"
                _git(None, Path(tmp), "clone", "--quiet", "--no-local", "--branch", MAIN,
                     str(repo.path), str(work))
                _git(None, work, "fetch", "--quiet", "origin", f"{upstream}:{upstream}")
                try:
                    _git(None, work, *_AUTHOR, "merge", "--no-ff", "--quiet", "-m",
                         f"merge: {cfg.branch} from the remote", upstream)
                except RemoteError:
                    files = _git(None, work, "diff", "--name-only", "--diff-filter=U")
                    _git(None, work, "merge", "--abort")
                    return SyncResult("conflict", main, detail=(
                        "the remote and the project's main both changed: "
                        + ", ".join(files.split()) + ". Main was left as it was."),
                        at=time.time())
                _git(None, work, "push", "--quiet", "origin", MAIN)
            return SyncResult("merged", repo.sha(MAIN), at=time.time())
    except (RemoteError, subprocess.SubprocessError, OSError) as exc:
        if not (repo.path / "refs" / "heads" / MAIN).exists() and repo.path.is_dir():
            shutil.rmtree(repo.path, ignore_errors=True)   # a failed seed leaves nothing
        return SyncResult("error", detail=_redact(str(exc)), at=time.time())


def _is_ancestor(repo: ProjectRepo, a: str, b: str) -> bool:
    return subprocess.run(["git", *_SAFE, "merge-base", "--is-ancestor", a, b],
                          cwd=repo.path, capture_output=True).returncode == 0


def publish(repo: ProjectRepo, cfg: RemoteConfig, run: RunBranch,
            http: httpx.Client | None = None) -> PublishResult:
    """Send an accepted run to the remote, as configured. Never raises."""
    if cfg.publish == "none":
        return PublishResult("skipped")
    target = cfg.branch if cfg.publish == "main" else f"guudflow/{run.run_id}"
    try:
        _push(repo, cfg, target)
    except RemoteError as exc:
        hint = " (the remote moved: sync, then accept again)" if cfg.publish == "main" else ""
        return PublishResult("error", ref=target, detail=f"{exc}{hint}")
    if cfg.publish != "pr":
        return PublishResult("pushed", ref=target)
    try:
        url = open_pull_request(cfg, head=target, title=f"GuudFlow run {run.run_id}",
                                body=_pr_body(run), http=http)
    except RemoteError as exc:
        return PublishResult("error", ref=target, detail=str(exc))
    return PublishResult("pr_opened", ref=target, url=url)


def _push(repo: ProjectRepo, cfg: RemoteConfig, target: str) -> None:
    with _git_env(cfg) as env:
        _git(env, repo.path, "push", "--quiet", cfg.url, f"refs/heads/{MAIN}:refs/heads/{target}")


def _pr_body(run: RunBranch) -> str:
    files = "\n".join(f"- `{f}`" for f in run.files[:50])
    note = f"\n\nReviewer's note: {run.note}" if run.note else ""
    return (f"Accepted in GuudFlow: run `{run.run_id}`, {run.commits} commit(s).{note}\n\n"
            f"Files:\n{files}")


def open_pull_request(cfg: RemoteConfig, *, head: str, title: str, body: str,
                      http: httpx.Client | None = None) -> str:
    """Open a GitHub pull request `head` → `cfg.branch`; returns its URL."""
    match = _GITHUB.match(cfg.url)
    token = cfg.token()
    if match is None or not token:
        raise RemoteError("opening a pull request needs a GitHub url and a token")
    owner, name = match.group(1), match.group(2)
    client = http or httpx.Client(timeout=30)
    try:
        resp = client.post(f"https://api.github.com/repos/{owner}/{name}/pulls",
                           headers={"Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json"},
                           json={"title": title, "head": head, "base": cfg.branch, "body": body})
    finally:
        if http is None:
            client.close()
    if resp.status_code >= 300:
        raise RemoteError(f"GitHub refused the pull request ({resp.status_code}): "
                          f"{resp.text[:300]}")
    return str(resp.json().get("html_url", ""))
