"""Offline quality gate for a finished run directory (plan S14).

`run_gate(run_dir, project)` inspects what a finished opencode run leaves on disk
and says whether the result is good enough. No network, no model:

  {run_dir}/board.jsonl            the signal board (append-only)
  {run_dir}/tasks.jsonl            the card board (latest line per task_id wins)
  {run_dir}/llm_costs.jsonl        the gateway's cost log
  {run_dir}/workspace/working/     the merged git repo (`main`)

Two groups of checks run:

  * generic run-health checks, always (this file);
  * functional checks on the merged code, registered per example project in
    `tests/quality/functional.py` (see `.claude/future_buff/functional_verification.md`).

The merged repo is never modified: functional checks run on a throwaway export of
`HEAD` (`RunContext.tree`), in a subprocess with a timeout, proxies and API keys
removed, `NO_NETWORK=1`, and a `sitecustomize` shim that refuses non-loopback
connections, so "the tests pass offline" is enforced rather than assumed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.harness.git_roles import is_protected
from core.oneshot import summarise_costs
from core.secret_scan import scan_directory

TEST_TIMEOUT_S = 120
"""Default wall-clock limit for a subprocess run in the merged tree."""

_NO_NETWORK_SHIM = '''\
"""Quality gate: refuse every non-loopback connection (loaded as sitecustomize)."""
import socket as _socket

_MSG = "network disabled by the quality gate (NO_NETWORK=1)"


def _local(host):
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    return host in ("", "localhost", "::1", "0.0.0.0") or host.startswith("127.")


_gai = _socket.getaddrinfo


def _getaddrinfo(host, *args, **kwargs):
    if not _local(host):
        raise OSError(_MSG)
    return _gai(host, *args, **kwargs)


_socket.getaddrinfo = _getaddrinfo
_connect = _socket.socket.connect
_connect_ex = _socket.socket.connect_ex


def _guard(sock, address):
    if sock.family in (_socket.AF_INET, _socket.AF_INET6) and not _local(address[0]):
        raise OSError(_MSG)


def connect(self, address):
    _guard(self, address)
    return _connect(self, address)


def connect_ex(self, address):
    _guard(self, address)
    return _connect_ex(self, address)


_socket.socket.connect = connect
_socket.socket.connect_ex = connect_ex
'''

_STRIPPED_ENV = re.compile(r"(^|_)(PROXY|API_KEY|TOKEN|SECRET|PASSWORD)$|^(ALL|HTTPS?|FTP|NO)_PROXY$", re.I)


# ── Results ────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """One check's outcome. `skipped` checks neither pass nor fail the gate."""

    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False

    @property
    def status(self) -> str:
        """`PASS`, `FAIL` or `SKIP`."""
        return "SKIP" if self.skipped else ("PASS" if self.passed else "FAIL")


def ok(name: str, detail: str = "") -> CheckResult:
    """A passing result."""
    return CheckResult(name, True, detail)


def fail(name: str, detail: str) -> CheckResult:
    """A failing result."""
    return CheckResult(name, False, detail)


def skip(name: str, reason: str) -> CheckResult:
    """A skipped result; `reason` says why the check could not run."""
    return CheckResult(name, True, reason, skipped=True)


@dataclass
class GateReport:
    """All check results for one run directory."""

    project: str | None
    run_dir: Path
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no check failed (skipped checks do not count)."""
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        """The failing checks."""
        return [c for c in self.checks if not c.passed]

    def render(self, width: int = 110) -> str:
        """A fixed-width table of every check plus a verdict line."""
        name_w = max([len("check"), *(len(c.name) for c in self.checks)])
        room = max(20, width - name_w - 8)
        lines = [f"Quality gate: {self.run_dir}  project={self.project or '-'}",
                 f"{'':4}  {'check'.ljust(name_w)}  detail",
                 f"{'-' * 4}  {'-' * name_w}  {'-' * 20}"]
        for c in self.checks:
            detail = " ".join(c.detail.split())
            if len(detail) > room:
                detail = detail[: room - 1] + "…"
            lines.append(f"{c.status:4}  {c.name.ljust(name_w)}  {detail}")
        counts = {s: sum(1 for c in self.checks if c.status == s) for s in ("PASS", "FAIL", "SKIP")}
        verdict = "PASSED" if self.passed else "FAILED"
        lines.append(f"{verdict}: {counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (`--json`)."""
        return {"project": self.project, "run_dir": str(self.run_dir), "passed": self.passed,
                "checks": [{**asdict(c), "status": c.status} for c in self.checks]}


# ── Run context ────────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parsed object lines of a jsonl file; blank or malformed lines are skipped."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _git(repo: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run git in `repo` with hooks and fsmonitor off (the repo is untrusted input)."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                           "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


class RunContext:
    """Everything the checks read: the run's files, the repo, and scratch trees.

    Create it, run checks, then call `close()` (or use it as a context manager)
    to delete the scratch exports.
    """

    def __init__(self, run_dir: Path, timeout_s: int | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.working = self.run_dir / "workspace" / "working"
        self.timeout_s = timeout_s or int(os.environ.get("GUUDFLOW_GATE_TIMEOUT", TEST_TIMEOUT_S))
        self.board = _read_jsonl(self.run_dir / "board.jsonl")
        self._scratch: Path | None = None
        self._tree: Path | None = None
        self._shim: Path | None = None
        self._tracked: list[tuple[str, str]] | None = None

    def __enter__(self) -> RunContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Delete every scratch directory this context created."""
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)
        self._scratch = self._tree = self._shim = None

    # -- the merged repo --

    @property
    def has_repo(self) -> bool:
        """True when `workspace/working` is a git repo with at least one commit."""
        if not (self.working / ".git").exists():
            return False
        try:
            return _git(self.working, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @property
    def tracked(self) -> list[tuple[str, str]]:
        """`(mode, path)` of every file in `HEAD` (mode `120000` is a symlink)."""
        if self._tracked is None:
            self._tracked = []
            if self.has_repo:
                out = _git(self.working, "ls-tree", "-r", "-z", "HEAD").stdout
                for rec in filter(None, out.split("\0")):
                    meta, _, path = rec.partition("\t")
                    self._tracked.append((meta.split()[0], path))
        return self._tracked

    @property
    def tracked_paths(self) -> list[str]:
        """Paths of every file in `HEAD`."""
        return [p for _, p in self.tracked]

    def scratch(self) -> Path:
        """The context's private temp directory (created on first use)."""
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="guudflow-gate-"))
        return self._scratch

    def export(self, ref: str, name: str) -> Path:
        """Export the tree at `ref` into a fresh scratch directory and return it."""
        dest = self.scratch() / name
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        archive = self.scratch() / f"{name}.tar"
        proc = _git(self.working, "archive", "--format=tar", "-o", str(archive), ref)
        if proc.returncode != 0:
            raise RuntimeError(f"git archive {ref} failed: {proc.stderr.strip()}")
        subprocess.run(["tar", "-xf", str(archive), "-C", str(dest)], check=True, capture_output=True)
        archive.unlink(missing_ok=True)
        return dest

    @property
    def tree(self) -> Path:
        """A throwaway copy of the merged tree (`HEAD`); never the recorded workspace."""
        if self._tree is None:
            self._tree = self.export("HEAD", "tree")
        return self._tree

    def base_ref(self) -> str:
        """The run's starting commit: "chore: initialise run workspace", else the root."""
        out = _git(self.working, "log", "--format=%H%x09%s", "--reverse", "HEAD").stdout.splitlines()
        for line in out:
            sha, _, subject = line.partition("\t")
            if subject.startswith("chore: initialise run workspace"):
                return sha
        return out[0].split("\t")[0] if out else "HEAD"

    # -- running things in a tree --

    def _shim_dir(self) -> Path:
        if self._shim is None:
            self._shim = self.scratch() / "shim"
            self._shim.mkdir()
            (self._shim / "sitecustomize.py").write_text(_NO_NETWORK_SHIM, encoding="utf-8")
        return self._shim

    def env(self, tree: Path) -> dict[str, str]:
        """Subprocess env: no proxies/keys, `NO_NETWORK=1`, tree and `tree/src` importable."""
        env = {k: v for k, v in os.environ.items() if not _STRIPPED_ENV.search(k)}
        env["NO_NETWORK"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = os.pathsep.join([str(self._shim_dir()), str(tree / "src"), str(tree)])
        return env

    def run(self, args: list[str], tree: Path | None = None, timeout: int | None = None
            ) -> tuple[int, str]:
        """Run `python *args` in `tree` (default: the merged tree).

        Returns `(returncode, combined output)`; `124` means the timeout hit.
        """
        tree = tree or self.tree
        limit = timeout or self.timeout_s
        try:
            proc = subprocess.run([sys.executable, *args], cwd=tree, env=self.env(tree),
                                  capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
            return 124, f"timed out after {limit}s\n{partial}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def pytest(self, tree: Path | None = None, extra: list[str] | None = None,
               timeout: int | None = None) -> tuple[int, str]:
        """`python -m pytest -q` in `tree`, offline, without writing a cache."""
        return self.run(["-m", "pytest", "-q", "-p", "no:cacheprovider", *(extra or [])],
                        tree=tree, timeout=timeout)


# ── Board helpers ──────────────────────────────────────────────────────────

_LIFECYCLE = ("STARTED", "COMPLETE", "FAILED", "BLOCKED")


def _roles_started(board: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for sig in board:
        role = str(sig.get("from_agent") or "")
        if sig.get("type") == "STARTED" and role and role not in seen:
            seen.append(role)
    return seen


def _last_lifecycle(board: list[dict[str, Any]], role: str) -> str | None:
    last = None
    for sig in board:
        if sig.get("from_agent") == role and sig.get("type") in _LIFECYCLE:
            last = str(sig["type"])
    return last


# ── Generic run-health checks ──────────────────────────────────────────────


def check_run_complete(ctx: RunContext) -> CheckResult:
    """The run reached COMPLETE: every started role ended in COMPLETE, nothing is waiting on a human."""
    name = "run reached COMPLETE"
    if not ctx.board:
        return fail(name, "board.jsonl is missing or empty")
    if not any(s.get("type") == "COMPLETE" for s in ctx.board):
        return fail(name, "no COMPLETE signal on the board")
    problems: list[str] = []
    for role in _roles_started(ctx.board):
        last = _last_lifecycle(ctx.board, role)
        if last != "COMPLETE":
            problems.append(f"{role} ended in {last}")
    pending = 0
    for sig in ctx.board:
        if sig.get("type") == "HUMAN_NEEDED":
            pending += 1
        elif sig.get("type") == "HUMAN_RESPONDED":
            pending = max(0, pending - 1)
    if pending:
        problems.append(f"{pending} HUMAN_NEEDED never answered")
    if problems:
        return fail(name, "; ".join(problems))
    return ok(name, f"{len(_roles_started(ctx.board))} role(s) completed")


def check_cards_done(ctx: RunContext) -> CheckResult:
    """Every card is in `done` (the latest `tasks.jsonl` line per task_id wins)."""
    name = "every card done"
    path = ctx.run_dir / "tasks.jsonl"
    if not path.is_file():
        return ok(name, "no tasks.jsonl (no task board in this run)")
    latest: dict[str, dict[str, Any]] = {}
    for rec in _read_jsonl(path):
        tid = str(rec.get("task_id") or "")
        if tid and not tid.startswith("root:"):
            latest[tid] = rec
    stuck = []
    for tid, rec in sorted(latest.items()):
        column = str(rec.get("column") or ("done" if rec.get("status") == "COMPLETE" else "todo"))
        if column != "done":
            stuck.append(f"{tid} in {column}")
    if stuck:
        return fail(name, f"{len(stuck)} of {len(latest)} card(s) not done: " + ", ".join(stuck))
    return ok(name, f"{len(latest)} card(s) done")


def check_no_unresolved_failures(ctx: RunContext) -> CheckResult:
    """No FAILED signal is left unresolved: the role must have completed after it."""
    name = "no unresolved FAILED"
    open_failures: dict[str, str] = {}
    for sig in ctx.board:
        role = str(sig.get("from_agent") or "")
        if sig.get("type") == "FAILED":
            open_failures[role] = str(sig.get("summary") or "")
        elif sig.get("type") == "COMPLETE":
            open_failures.pop(role, None)
    if open_failures:
        return fail(name, "; ".join(f"{r}: {s}" for r, s in open_failures.items()))
    return ok(name)


def check_capabilities_per_role(ctx: RunContext) -> CheckResult:
    """Every spawned role (one that posted STARTED) has a CAPABILITIES signal."""
    name = "CAPABILITIES per role"
    spawned = _roles_started(ctx.board)
    granted = {str(s.get("from_agent")) for s in ctx.board if s.get("type") == "CAPABILITIES"}
    missing = [r for r in spawned if r not in granted]
    if not spawned:
        return fail(name, "no role was ever spawned (no STARTED signal)")
    if missing:
        return fail(name, "no CAPABILITIES signal for: " + ", ".join(missing))
    return ok(name, f"{len(spawned)} role(s)")


def check_agent_results_valid(ctx: RunContext) -> CheckResult:
    """Every AGENT_RESULT carries parseable JSON with a `result` object."""
    name = "AGENT_RESULT validated"
    results = [s for s in ctx.board if s.get("type") == "AGENT_RESULT"]
    bad: list[str] = []
    for sig in results:
        who = str(sig.get("from_agent") or "?")
        try:
            body = json.loads(str(sig.get("detail") or ""))
        except json.JSONDecodeError:
            bad.append(f"{who}: detail is not JSON")
            continue
        if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
            bad.append(f"{who}: no result object")
    if bad:
        return fail(name, "; ".join(bad))
    return ok(name, f"{len(results)} result(s)")


def check_costs(ctx: RunContext) -> CheckResult:
    """The cost log exists and shows spend (cost or tokens) whenever the model was called."""
    name = "cost log"
    path = ctx.run_dir / "llm_costs.jsonl"
    rows = summarise_costs(ctx.run_dir)
    called = any(s.get("type") == "AGENT_RESULT" for s in ctx.board)
    if not path.is_file() or not rows:
        if called:
            return fail(name, "agents returned results but llm_costs.jsonl is missing or empty")
        return ok(name, "no model calls recorded and none expected")
    total = rows[-1]
    tokens = total["input_tokens"] + total["output_tokens"]
    if total["calls"] <= 0 or (total["cost_usd"] <= 0 and tokens <= 0):
        return fail(name, f"{total['calls']} call(s) but zero cost and zero tokens")
    return ok(name, f"{total['calls']} call(s), {tokens:,} tokens, ${total['cost_usd']:.4f}")


def _need_repo(ctx: RunContext, name: str) -> CheckResult | None:
    if not ctx.has_repo:
        return fail(name, f"no merged git repo with a commit at {ctx.working}")
    return None


def check_repo_present(ctx: RunContext) -> CheckResult:
    """The merged repo exists, has a commit and tracks at least one file."""
    name = "merged repo present"
    missing = _need_repo(ctx, name)
    if missing:
        return missing
    if not ctx.tracked:
        return fail(name, "HEAD tracks no files")
    return ok(name, f"{len(ctx.tracked)} tracked file(s)")


def check_no_secrets(ctx: RunContext) -> CheckResult:
    """No secret patterns (`core.secret_scan`) in the tracked merged tree."""
    name = "no secrets in merged tree"
    missing = _need_repo(ctx, name)
    if missing:
        return missing
    findings = scan_directory(ctx.tree)
    if findings:
        return fail(name, "; ".join(f"{f.path} ({f.reason})" for f in findings[:5]))
    return ok(name)


def check_no_protected_paths(ctx: RunContext) -> CheckResult:
    """No protected path (`core.harness.git_roles.is_protected`) is tracked in the merged tree."""
    name = "no protected paths"
    missing = _need_repo(ctx, name)
    if missing:
        return missing
    hits = [p for p in ctx.tracked_paths if is_protected(p)]
    if hits:
        return fail(name, ", ".join(hits[:8]))
    return ok(name)


def check_no_escaping_symlinks(ctx: RunContext) -> CheckResult:
    """No tracked symlink is absolute or resolves outside the repo."""
    name = "no escaping symlinks"
    missing = _need_repo(ctx, name)
    if missing:
        return missing
    bad: list[str] = []
    for mode, path in ctx.tracked:
        if mode != "120000":
            continue
        proc = _git(ctx.working, "cat-file", "blob", f"HEAD:{path}")
        target = proc.stdout.strip()
        if target.startswith("/"):
            bad.append(f"{path} -> {target}")
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if resolved == ".." or resolved.startswith("../"):
            bad.append(f"{path} -> {target}")
    if bad:
        return fail(name, "; ".join(bad))
    return ok(name)


GENERIC_CHECKS: tuple[Callable[[RunContext], CheckResult], ...] = (
    check_run_complete,
    check_cards_done,
    check_no_unresolved_failures,
    check_capabilities_per_role,
    check_agent_results_valid,
    check_costs,
    check_repo_present,
    check_no_secrets,
    check_no_protected_paths,
    check_no_escaping_symlinks,
)


# ── Entry point ────────────────────────────────────────────────────────────


def _guarded(check: Callable[[RunContext], CheckResult], ctx: RunContext) -> CheckResult:
    """Run `check`; an exception inside a check is a failure, never a crash."""
    try:
        return check(ctx)
    except Exception as exc:  # noqa: BLE001 — the gate must report, not die, on a broken run
        name = getattr(check, "check_name", check.__name__.removeprefix("check_").replace("_", " "))
        return fail(name, f"check crashed: {type(exc).__name__}: {exc}")


def run_gate(run_dir: Path, project: str | None = None) -> GateReport:
    """Inspect the finished run in `run_dir` and return the report.

    `project` is an example-project id (`api-client-library` or
    `example-api-client-library`); with it, that project's functional checks run
    after the generic ones. An unknown id is itself a failed check.
    """
    from tests.quality import functional

    run_dir = Path(run_dir)
    key = functional.normalise_project(project) if project else None
    report = GateReport(project=key, run_dir=run_dir)
    if not run_dir.is_dir():
        report.checks.append(fail("run directory", f"{run_dir} is not a directory"))
        return report
    with RunContext(run_dir) as ctx:
        report.checks.extend(_guarded(c, ctx) for c in GENERIC_CHECKS)
        if key is not None:
            checks = functional.FUNCTIONAL_CHECKS.get(key)
            if checks is None:
                report.checks.append(fail("functional checks", (
                    f"no functional checks registered for {project!r}; "
                    f"known: {', '.join(sorted(functional.FUNCTIONAL_CHECKS))}")))
            elif not ctx.has_repo:
                report.checks.append(fail("functional checks", "no merged repo to inspect"))
            else:
                report.checks.extend(_guarded(c, ctx) for c in checks)
    return report
