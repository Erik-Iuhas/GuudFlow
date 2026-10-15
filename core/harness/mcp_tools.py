"""Board-facing MCP tools: read_board, report_progress, ask_human,
request_visual_feedback, request_package (plan §5.4, §5.6) and sync_dependencies (§16).

`tools(ctx)` returns the tools for one run. The calling role comes only from
`identity.role` (the bearer token), never from arguments. Which role may call
which tool is decided by the role's capabilities, not here.

Interaction modes stay distinct (CLAUDE.md): `ask_human` is the hard pause
(sets `workspace.pending_human`), `request_visual_feedback` blocks only the
calling agent. Every wait ends in a non-error "no answer" result on timeout so
the agent can carry on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.tool_context import RunToolContext
from core.signals import Signal, SignalType
from core.workspace import ApprovalRequest, HumanRequest

log = logging.getLogger(__name__)

Installer = Callable[[list[str]], Awaitable[tuple[int, str]]]

DEFAULT_TIMEOUT_S = 900
MIN_TIMEOUT_S: float = 10
MAX_TIMEOUT_S: float = 3600
MAX_BOARD_LIMIT = 100
MAX_PROGRESS_CHARS = 300
INSTALL_TIMEOUT_S = 300
OUTPUT_LIMIT = 2000
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
PACKAGE_APPROVAL_KIND = "package_install"

_PIP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NPM_NAME = re.compile(r"^(@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")


_KNOWN_ANSWERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(allowed|permitted|blocked|denied|refused|permission)\b.*\b(command|bash|shell|"
                r"bun|npm|test|sed|grep|echo|pipe|redirect)|\b(command|bash|shell)\b.*\b"
                r"(allowed|permitted|denied|refused)", re.I | re.S),
     "Do not ask a person about shell permissions. A refused command line usually has one part "
     "that is not allowed: run each command on its own (plain `bun test`, then `grep`/`head`/`tail` "
     "only), with no redirection, `echo` or `sed`. The brief lists the allowed commands."),
    (re.compile(r"\bwhich (card|task)\b.*\b(mine|my|own|assigned)|\b(is|are) (t|card )\d+.*\b"
                r"(mine|my card|assigned to me)", re.I | re.S),
     "Do not ask which card is yours. Your current card is the one in your brief; cards listed there "
     "as parked or queued are not yours to work on now."),
    (re.compile(r"\b(playtest|play-test|screenshot|in (a|the) browser|browser check|manual(ly)? "
                r"(check|verif|test))", re.I),
     "Do not ask a person for a playtest, screenshot or browser check. Those are the owner's checks "
     "at acceptance: finish the code and tests, submit `done`, and list them in `owner_checks`."),
)


def answerable_without_human(question: str) -> str | None:
    """A reply when the question is one the brief already answers (shell permissions, which card
    is yours, owner checks), so a small model does not pause the whole run for it."""
    for pattern, reply in _KNOWN_ANSWERS:
        if pattern.search(question):
            return reply
    return None


def tools(ctx: RunToolContext, *, installer: Installer | None = None) -> list[McpTool]:
    """Build the board-facing tools for one run.

    `installer` runs an argv and returns (exit code, combined output); tests
    inject a fake so no real pip/npm runs. It defaults to a host subprocess.
    """
    install = installer or _run_installer

    async def read_board(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        limit = args.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return ToolResult("limit must be an integer between 1 and 100", is_error=True)
        limit = min(limit, MAX_BOARD_LIMIT)
        wanted: set[SignalType] | None = None
        raw_types = args.get("types")
        if raw_types is not None:
            if not isinstance(raw_types, list) or not all(isinstance(t, str) for t in raw_types):
                return ToolResult("types must be a list of signal type names", is_error=True)
            try:
                wanted = {SignalType(t.upper()) for t in raw_types}
            except ValueError as exc:
                return ToolResult(f"unknown signal type: {exc}", is_error=True)
        signals = [s for s in ctx.workspace.board if wanted is None or s.type in wanted]
        lines = [f"[{s.type.value}] {s.from_agent or 'user'}: {s.summary}"
                 for s in signals[-limit:]]  # summaries only: detail is never exposed
        return ToolResult("\n".join(lines) if lines else "The board has no matching signals.")

    async def report_progress(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            return ToolResult("message must be a non-empty string", is_error=True)
        ctx.post(SignalType.THINKING, identity.role, message.strip()[:MAX_PROGRESS_CHARS])
        return ToolResult("noted")

    async def ask_human(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return ToolResult("question must be a non-empty string", is_error=True)
        known = answerable_without_human(question)
        if known:
            return ToolResult(known, is_error=True)
        timeout = _timeout(args)
        if isinstance(timeout, ToolResult):
            return timeout
        role, workspace = identity.role, ctx.workspace
        if workspace.pending_human is not None:
            return ToolResult("Another question to the human is already pending; wait for it to "
                              "be answered, or continue with your best judgement.", is_error=True)
        question = question.strip()
        question_id = uuid4().hex
        request = HumanRequest(from_agent=role, question=question,
                               iteration=workspace.iteration)
        workspace.pending_human = request
        try:
            reply = await _post_and_wait(
                ctx, lambda: ctx.post(SignalType.HUMAN_NEEDED, role, question[:300],
                                      {"question_id": question_id, "question": question}),
                lambda sig: sig.type is SignalType.HUMAN_RESPONDED
                and _detail(sig).get("question_id") == question_id,
                timeout)
        finally:
            if workspace.pending_human is request:
                workspace.pending_human = None
        if reply is None:
            return ToolResult(f"No answer from the human within {timeout:g} seconds; continue with "
                              "your best judgement and say so in your summary.")
        return ToolResult(str(_detail(reply).get("answer", reply.summary)))

    async def request_visual_feedback(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        role = identity.role
        path, question = args.get("path"), args.get("question")
        if not isinstance(path, str) or not path.strip():
            return ToolResult("path must be a non-empty string", is_error=True)
        if not isinstance(question, str) or not question.strip():
            return ToolResult("question must be a non-empty string", is_error=True)
        timeout = _timeout(args)
        if isinstance(timeout, ToolResult):
            return timeout
        resolved = _resolve_in_clone(ctx.clone_for(role), path.strip())
        if isinstance(resolved, ToolResult):
            return resolved
        request_id = uuid4().hex
        question = question.strip()
        reply = await _post_and_wait(
            ctx, lambda: ctx.post(SignalType.SCREENSHOT_READY, role, question[:300],
                                  {"request_id": request_id, "path": resolved,
                                   "question": question}),
            lambda sig: sig.type is SignalType.USER_VISUAL_FEEDBACK
            and _detail(sig).get("request_id") == request_id,
            timeout)
        if reply is None:
            return ToolResult(f"No feedback from the human within {timeout:g} seconds; continue "
                              "with your best judgement and say so in your summary.")
        return ToolResult(str(_detail(reply).get("feedback", reply.summary)))

    async def request_package(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        role = identity.role
        manager, name, version = args.get("manager"), args.get("name"), args.get("version")
        problem = _package_problem(manager, name, version)
        if problem:
            return ToolResult(problem, is_error=True)
        assert isinstance(manager, str) and isinstance(name, str) and isinstance(version, str)
        spec = f"{name}=={version}" if manager == "pip" else f"{name}@{version}"
        detail = {"role": role, "manager": manager, "name": name, "version": version}
        ctx.post(SignalType.PACKAGE_REQUEST, role, f"package requested: {spec}", detail)
        if not _allow_listed(ctx.settings.get("package_allow", {}), manager, name, version):
            granted = await _ask_approval(ctx, role, f"{role} wants to install {spec}", detail)
            if not granted:
                ctx.post(SignalType.PACKAGE_REJECTED, role, f"rejected: {spec}",
                         "not approved by the human")
                return ToolResult(f"Installing {spec} was not approved; continue without it.",
                                  is_error=True)
        ctx.post(SignalType.PACKAGE_APPROVED, role, f"approved: {spec}", detail)
        tools_dir = ctx.tool_dir(role)
        try:
            if manager == "pip":
                code, output = await install(_install_argv(manager, name, version, tools_dir))
            else:
                code, output = await _npm_isolated(install, name, version, tools_dir)
        except Exception as exc:  # installer crash must surface as a tool error, not an exception
            log.exception("package install crashed")
            return ToolResult(f"Installing {spec} failed: {exc}", is_error=True)
        output = output[:OUTPUT_LIMIT]
        if code != 0:
            return ToolResult(f"Installing {spec} failed (exit {code}):\n{output}", is_error=True)
        if manager == "pip":
            how = "set PYTHONPATH=.guudflow/site-packages (relative to /work) to import it"
        else:
            how = "installed into .guudflow/node_modules; set NODE_PATH=.guudflow/node_modules"
        return ToolResult(f"Installed {spec}; {how}.")

    sync_lock = asyncio.Lock()   # one install at a time per run; others wait, then reuse

    async def sync_dependencies(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        from core.deps import (MANAGERS, MANIFEST, DepsError, link_node_modules,
                               merge_provenance, read_pins)
        role, manager = identity.role, args.get("manager")
        if manager not in MANAGERS:
            return ToolResult(f"manager must be one of {', '.join(MANAGERS)}", is_error=True)
        assert isinstance(manager, str)
        deps = ctx.deps
        if deps is None:
            return ToolResult("This session has no shared dependency environment; use "
                              "request_package instead.", is_error=True)
        try:
            pins = read_pins(ctx.clone_for(role), manager)
        except DepsError as exc:
            return ToolResult(str(exc), is_error=True)
        sep = "==" if manager == "pip" else "@"
        async with sync_lock:
            old = deps.current(manager)
            before = old.pins() if old else {}
            added = {n: v for n, v in pins.items() if before.get(n) != v}
            removed = sorted(set(before) - set(pins))
            if old is not None and not added and not removed:
                if manager == "npm":
                    link_node_modules(ctx.clone_for(role), ctx.deps_mount)
                return ToolResult(f"Already in sync: {len(pins)} {manager} packages from "
                                  f"{MANIFEST[manager]} are installed.")
            specs = [f"{n}{sep}{v}" for n, v in sorted(added.items())]
            detail = {"role": role, "manager": manager, "added": specs, "removed": removed,
                      "shared": True}
            ctx.post(SignalType.PACKAGE_REQUEST, role,
                     f"sync {manager}: " + (", ".join(specs[:8]) or "remove " + ", ".join(removed[:8]))
                     + (" …" if len(specs) > 8 else ""), detail)
            allow = ctx.settings.get("package_allow", {})
            unlisted = [f"{n}{sep}{v}" for n, v in sorted(added.items())
                        if not _allow_listed(allow, manager, n.split("[", 1)[0], v)]
            approved_by = "allow-list"
            if unlisted:
                granted = await _ask_approval(
                    ctx, role, f"{role} wants to add to the shared {manager} packages: "
                    + ", ".join(unlisted), {**detail, "needs_approval": unlisted})
                if not granted:
                    ctx.post(SignalType.PACKAGE_REJECTED, role, "rejected: " + ", ".join(unlisted),
                             json.dumps({**detail, "refused": unlisted}))
                    return ToolResult("Not approved: " + ", ".join(unlisted) + ". Take them out "
                                      f"of {MANIFEST[manager]} and continue without them.",
                                      is_error=True)
                approved_by = "human"
            try:
                code, output = await deps.build(manager, pins, install)
            except Exception as exc:  # an installer crash is a tool error, not an exception
                log.exception("dependency sync crashed")
                return ToolResult(f"Installing the {manager} packages failed: {exc}", is_error=True)
            if code != 0:
                return ToolResult(f"Installing the {manager} packages failed (exit {code}):\n"
                                  f"{output[:OUTPUT_LIMIT]}", is_error=True)
            deps.activate(manager, merge_provenance(manager, pins, old, role=role,
                                                    approved_by=approved_by, run_id=ctx.run_id))
            ctx.post(SignalType.PACKAGE_APPROVED, role,
                     f"synced {manager}: {len(pins)} packages", {**detail, "approved_by": approved_by})
        note = ""
        if manager == "npm":
            problem = link_node_modules(ctx.clone_for(role), ctx.deps_mount)
            note = f" ({problem})" if problem else " node_modules points at it."
            how = "require/import them as usual; their commands (e.g. vitest) are on PATH."
        else:
            how = "import them as usual (PYTHONPATH includes them); their commands are on PATH."
        return ToolResult(f"Synced {len(pins)} {manager} packages from {MANIFEST[manager]} into "
                          f"the project's shared environment; {how}{note} Teammates get them "
                          "from their next turn.")

    def text(description: str) -> dict[str, Any]:
        return {"type": "string", "description": description}

    timeout_prop = {"type": "integer", "minimum": MIN_TIMEOUT_S, "maximum": MAX_TIMEOUT_S,
                    "description": f"Seconds to wait (default {DEFAULT_TIMEOUT_S})."}
    return [
        McpTool(
            name="read_board",
            description="Read recent signals from the run's shared board, newest last.",
            input_schema={"type": "object", "additionalProperties": False, "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_BOARD_LIMIT},
                "types": {"type": "array", "items": {"type": "string"},
                          "description": "Only these signal types, e.g. COMPLETE."}}},
            handler=read_board),
        McpTool(
            name="report_progress",
            description="Tell the human what you are doing right now, in one short sentence.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"message": text("Short progress message.")},
                          "required": ["message"]},
            handler=report_progress),
        McpTool(
            name="ask_human",
            description="Ask the human a blocking question. Use sparingly: it pauses the run's "
                        "scheduling until answered. Returns the answer.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"question": text("The question."),
                                         "timeout_s": timeout_prop},
                          "required": ["question"]},
            handler=ask_human),
        McpTool(
            name="request_visual_feedback",
            description="Show the human a screenshot and ask about it. `path` is an image "
                        "(.png/.jpg/.jpeg/.webp) inside your working directory.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"path": text("Relative path to the image."),
                                         "question": text("What you want feedback on."),
                                         "timeout_s": timeout_prop},
                          "required": ["path", "question"]},
            handler=request_visual_feedback),
        McpTool(
            name="request_package",
            description="Request an exact-pinned pip or npm package. It is installed on the host "
                        "(no install scripts) into your working directory; packages outside the "
                        "project allow-list need human approval.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"manager": {"type": "string", "enum": ["pip", "npm"]},
                                         "name": text("Package name."),
                                         "version": text("Exact version, e.g. 1.2.3.")},
                          "required": ["manager", "name", "version"]},
            handler=request_package),
        McpTool(
            name="sync_dependencies",
            description="Install the project's dependencies after you change package.json or "
                        "requirements.txt (exact versions only, e.g. \"1.2.3\" or name==1.2.3). "
                        "The host installs them, without install scripts, into an environment "
                        "every teammate shares; packages outside the project allow-list need "
                        "human approval.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"manager": {"type": "string", "enum": ["npm", "pip"]}},
                          "required": ["manager"]},
            handler=sync_dependencies),
    ]


# ── waiting on the human ────────────────────────────────────────────────────
async def _post_and_wait(ctx: RunToolContext, post: Callable[[], None],
                         accept: Callable[[Signal], bool], timeout: float) -> Signal | None:
    """Subscribe, run `post`, and wait for the first signal `accept` approves.

    Returns None on timeout. Subscribing before posting means a reply that
    arrives immediately is never missed.
    """
    arrived: asyncio.Future[Signal] = asyncio.get_running_loop().create_future()

    def on_signal(sig: Signal) -> None:
        if not arrived.done() and accept(sig):
            arrived.set_result(sig)

    unsubscribe = ctx.workspace.subscribe(on_signal)
    try:
        post()
        return await asyncio.wait_for(arrived, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        unsubscribe()


async def _ask_approval(ctx: RunToolContext, role: str, summary: str,
                        context: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_S) -> bool:
    """Post APPROVAL_NEEDED (kind package_install) and wait for the decision.

    Same wire format as `core.harness.approvals`: the decision signal's detail
    carries `approval_id=<id>`. Timeout counts as denied.
    """
    request = ApprovalRequest(id=f"appr_{uuid4().hex[:10]}", kind=PACKAGE_APPROVAL_KIND,
                              summary=summary, context=context, triggered_by=role,
                              iteration=ctx.workspace.iteration)
    ctx.workspace.pending_approvals.append(request)
    decision = await _post_and_wait(
        ctx,
        lambda: ctx.post(SignalType.APPROVAL_NEEDED, role, summary,
                         f"approval_id={request.id}; kind={PACKAGE_APPROVAL_KIND}; "
                         f"context={json.dumps(context)}"),
        lambda sig: sig.type in (SignalType.APPROVAL_GRANTED, SignalType.APPROVAL_DENIED)
        and f"approval_id={request.id}" in (sig.detail or ""),
        timeout)
    if decision is None:
        ctx.workspace.record_approval(request.id, "denied", note="timed out")
        return False
    return decision.type is SignalType.APPROVAL_GRANTED


# ── argument helpers ────────────────────────────────────────────────────────
def _timeout(args: dict[str, Any]) -> float | ToolResult:
    raw = args.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return ToolResult("timeout_s must be a number of seconds", is_error=True)
    return float(min(max(raw, MIN_TIMEOUT_S), MAX_TIMEOUT_S))


def _detail(sig: Signal) -> dict[str, Any]:
    """A signal's detail parsed as a JSON object (empty when it is not one)."""
    try:
        parsed = json.loads(sig.detail or "")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_in_clone(clone: Path, path: str) -> str | ToolResult:
    """Validate an image path inside `clone`; return it relative to the clone.

    Absolute paths, `..` segments and symlinks that lead outside the clone are
    refused with an error result, as is anything that is not an existing image.
    """
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return ToolResult("path must be relative to your working directory and may not "
                          "contain '..'", is_error=True)
    root = clone.resolve()
    resolved = (root / candidate).resolve()  # follows symlinks
    if not resolved.is_relative_to(root):
        return ToolResult("path resolves outside your working directory", is_error=True)
    if resolved.suffix.lower() not in IMAGE_SUFFIXES:
        return ToolResult("path must be a .png, .jpg, .jpeg or .webp image", is_error=True)
    if not resolved.is_file():
        return ToolResult(f"no such image: {path}", is_error=True)
    return resolved.relative_to(root).as_posix()


def _package_problem(manager: object, name: object, version: object) -> str | None:
    """Why the package arguments are invalid, or None when they are fine."""
    if manager not in ("pip", "npm"):
        return "manager must be 'pip' or 'npm'"
    pattern = _PIP_NAME if manager == "pip" else _NPM_NAME
    if not isinstance(name, str) or not pattern.fullmatch(name):
        return f"invalid {manager} package name: {name!r}"
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        return f"version must be an exact pin like 1.2.3 (no ranges or tags): {version!r}"
    return None


def _normalise(manager: str, name: str) -> str:
    """Canonical package name: PEP 503 for pip, lower case for npm."""
    return re.sub(r"[-_.]+", "-", name).lower() if manager == "pip" else name.lower()


def _allow_listed(allow: object, manager: str, name: str, version: str) -> bool:
    """True when `name` at the exact `version` is on the project allow-list.

    Entries are `name==version` (pip) or `name@version` (npm); a `*` version
    allows any exact pin of that package.
    """
    entries = allow.get(manager, []) if isinstance(allow, dict) else []
    if not isinstance(entries, list):
        return False
    sep = "==" if manager == "pip" else "@"
    for entry in entries:
        if not isinstance(entry, str):
            continue
        allowed_name, found, allowed_version = entry.rpartition(sep)
        if not found or _normalise(manager, allowed_name) != _normalise(manager, name):
            continue
        if allowed_version in ("*", version):
            return True
    return False


# ── installing ──────────────────────────────────────────────────────────────
def _install_argv(manager: str, name: str, version: str, target: Path) -> list[str]:
    """The exact installer command: no shell, no install scripts, exact pin.

    `target` is the role's host-only tool directory (never the agent's worktree, so an
    agent-written package.json / .npmrc / symlink cannot steer the host-side install).
    """
    if manager == "pip":
        return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                "--no-input", "--isolated", "--only-binary=:all:", "--target",
                str(target / "site-packages"), f"{name}=={version}"]
    return ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund",
            "--registry=https://registry.npmjs.org/", "--prefix", str(target),
            f"{name}@{version}"]


async def _npm_isolated(install: Installer, name: str, version: str,
                        tools_dir: Path) -> tuple[int, str]:
    """npm install into a fresh temp prefix, then move node_modules into the tool dir."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory(prefix="gf-npm-") as tmp:
        prefix = Path(tmp)
        (prefix / "package.json").write_text('{"name": "guudflow-packages", "private": true}\n')
        argv = _install_argv("npm", name, version, prefix) + ["--userconfig", str(prefix / ".npmrc")]
        (prefix / ".npmrc").write_text("")
        code, output = await install(argv)
        if code == 0 and (prefix / "node_modules").is_dir():
            dest = tools_dir / "node_modules"
            dest.mkdir(parents=True, exist_ok=True)
            for item in (prefix / "node_modules").iterdir():
                target = dest / item.name
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                shutil.move(str(item), str(target))
        return code, output


async def _run_installer(argv: list[str]) -> tuple[int, str]:
    """Run `argv` on the host without a shell; return (exit code, combined output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:  # e.g. npm is not installed
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), INSTALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {INSTALL_TIMEOUT_S} seconds"
    code = proc.returncode if proc.returncode is not None else 1
    return code, out.decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
