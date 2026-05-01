"""Code tools — `code-exec`, `code:outline`.

DESIGN §29 / §31. In production, `code-exec` MUST run inside the sandbox
container — the worker refuses to start in production mode without
Docker available. In dev mode, `code-exec` may run on the host with an
explicit `⚠ DEV MODE` warning logged on every call AND the dev-mode
hardening described below.

`code:outline` is a read-only structural extractor — tree-sitter parse of
a source file returning imports / classes (with their methods) / top-level
functions. It is the cheap intermediate between repo_map (whole codebase,
top-level only) and `fs:read` (full content).

## Dev-mode `code-exec` hardening (active before Stage 7's container)

These guards apply when `code-exec` runs on the host:

  1. **Mode enforcement.** If `GUUDFLOW_MODE=production`, the host
     runner refuses to run and returns an error directing the runner to
     use the container-routed runner. The default mode is `dev`.
  2. **Audit log.** Every call records timestamp, language, code preview,
     exit code, duration, mode. Persisted to disk if a path is configured.
  3. **Per-run quota.** A single `CodeExec` instance has a max-call cap
     (default 50). Exceeding it returns an error rather than running.
  4. **Environment scrubbing.** The subprocess env is a curated minimal
     set (PATH, HOME, TMPDIR, LANG, plus an opt-in allowlist). API keys
     and other secrets in the runner's env do not leak into the call.
  5. **Working directory pinning.** Subprocess cwd is configurable;
     the runner sets it to `workspace/working/`. Default is the runner
     process's cwd.
  6. **POSIX rlimits.** On Linux/macOS, `preexec_fn` sets RLIMIT_CPU,
     RLIMIT_AS, RLIMIT_NPROC. Best-effort; not a substitute for the
     container, but real on platforms that support it.

Stage 7 will inject the container-routed runner and these dev-mode
guards become inert (the production runner has its own much stronger
controls via Docker).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time as _time_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents.tools.base import BaseTool
from core.file_workspace import FileWorkspace, PathSafetyError


_logger = logging.getLogger(__name__)


CodeRunner = Callable[[str, list[str], dict[str, Any]], Awaitable[dict[str, Any]]]
"""(language, code_or_args, options) → result dict."""


# Default env keys passed through to subprocess. Anything NOT in this
# list is scrubbed. Callers can extend via the `env_allowlist` ctor arg.
_DEFAULT_ENV_ALLOW: frozenset[str] = frozenset({
    "PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONPATH",  # needed if subprocess runs python that imports project code
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR",  # required on Windows
})


# Env keys that look secret-like and should NEVER pass through, even if
# explicitly allowlisted by mistake. Belt-and-suspenders.
_SECRET_HINTS: tuple[str, ...] = (
    "API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
    "PRIVATE_KEY", "CREDENTIALS", "AUTH",
)


def _scrub_env(allowlist: set[str]) -> dict[str, str]:
    """Return a curated env dict. Allowlisted keys whose names look
    secret-like are still dropped (defensive)."""
    out: dict[str, str] = {}
    for key in allowlist:
        if any(hint in key.upper() for hint in _SECRET_HINTS):
            continue
        if key in os.environ:
            out[key] = os.environ[key]
    return out


def _posix_rlimit_preexec(cpu_seconds: int, address_bytes: int, max_processes: int):
    """Return a preexec_fn that sets POSIX rlimits, or None on non-POSIX."""
    if sys.platform == "win32":
        return None
    try:
        import resource  # POSIX only
    except ImportError:
        return None

    def _apply():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except (OSError, ValueError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_AS, (address_bytes, address_bytes))
        except (OSError, ValueError):
            pass
        try:
            # NPROC is per-user on macOS/Linux. Best-effort.
            resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        except (OSError, ValueError, AttributeError):
            pass

    return _apply


def make_dev_host_runner(
    cwd: Path | str | None = None,
    env_allowlist: set[str] | None = None,
    cpu_seconds: int = 30,
    address_bytes: int = 512 * 1024 * 1024,
    max_processes: int = 50,
    audit_path: Path | None = None,
) -> CodeRunner:
    """Build a dev-mode runner with the given hardening configuration.

    Returns a callable matching the CodeRunner signature, ready to inject
    into a `CodeExec` tool.
    """
    allow = set(_DEFAULT_ENV_ALLOW) | (env_allowlist or set())

    async def _runner(
        language: str,
        args: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        _logger.warning(
            "⚠ DEV MODE: code-exec running on host. Do not use with untrusted input."
        )

        code = options.get("code", "")
        timeout = float(options.get("timeout_s", 30.0))

        if language not in ("python", "shell"):
            return {"error": f"language not supported in dev runner: {language}"}

        if language == "python":
            cmd = [sys.executable, "-c", code]
        else:
            cmd = ["bash", "-lc", code] if sys.platform != "win32" else ["cmd", "/c", code]

        env = _scrub_env(allow)
        preexec = _posix_rlimit_preexec(cpu_seconds, address_bytes, max_processes)

        started = _time_mod.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                preexec_fn=preexec,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, UnboundLocalError):
                pass
            duration = _time_mod.monotonic() - started
            _audit(audit_path, language, code, exit_code=None, duration=duration,
                   mode="dev", error="timeout")
            return {"error": f"timeout after {timeout}s"}
        except FileNotFoundError as e:
            duration = _time_mod.monotonic() - started
            _audit(audit_path, language, code, exit_code=None, duration=duration,
                   mode="dev", error=f"interpreter not found: {e}")
            return {"error": f"interpreter not found: {e}"}
        except OSError as e:
            duration = _time_mod.monotonic() - started
            _audit(audit_path, language, code, exit_code=None, duration=duration,
                   mode="dev", error=f"exec failed: {e}")
            return {"error": f"exec failed: {e}"}

        duration = _time_mod.monotonic() - started
        _audit(audit_path, language, code, exit_code=proc.returncode,
               duration=duration, mode="dev")
        return {
            "ok": True,
            "language": language,
            "exit_code": proc.returncode,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
            "dev_mode": True,
            "duration_s": duration,
        }

    return _runner


def make_container_runner(
    docker_manager: Any,
    run_id: str,
) -> CodeRunner:
    """Build a production-mode runner that routes `code-exec` through the
    run's sandbox container.

    The runner shape matches `make_dev_host_runner` so the `CodeExec` tool
    is mode-agnostic at the call site. `docker_manager.exec_in()`
    enforces the §29 resource limits + network_mode=none container that
    the agent-generated code actually executes inside.

    The `language` field is forwarded to `DockerManager.exec_in` which
    knows how to wrap python/shell. Anything else returns an error.
    """

    async def _runner(
        language: str,
        args: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        code = options.get("code", "")
        timeout = float(options.get("timeout_s", 30.0))

        if language not in ("python", "shell"):
            return {"error": f"language not supported in container runner: {language}"}

        started = _time_mod.monotonic()
        try:
            result = await docker_manager.exec_in(
                run_id, language, code, timeout_s=timeout,
            )
        except Exception as e:
            return {
                "error": f"container exec failed: {e}",
                "duration_s": _time_mod.monotonic() - started,
            }

        duration = _time_mod.monotonic() - started
        return {
            "ok": True,
            "language": language,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "container_mode": True,
            "is_stub": result.is_stub,
            "duration_s": duration,
        }

    return _runner


def _audit(
    audit_path: Path | None,
    language: str,
    code: str,
    exit_code: int | None,
    duration: float,
    mode: str,
    error: str | None = None,
) -> None:
    if audit_path is None:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "language": language,
        "code_preview": code[:200],
        "code_len": len(code),
        "exit_code": exit_code,
        "duration_s": round(duration, 3),
        "mode": mode,
        "error": error,
    }
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        # Audit write failure must not break execution; log and continue.
        _logger.warning("code-exec audit log write failed", exc_info=True)


# Backward-compatible default runner — used by tests that don't care
# about hardening config.
async def _dev_host_runner(
    language: str,
    args: list[str],
    options: dict[str, Any],
) -> dict[str, Any]:
    runner = make_dev_host_runner()
    return await runner(language, args, options)


DEFAULT_QUOTA = 50


class CodeExec(BaseTool):
    """Run code via a runner callable.

    The runner is injected — dev mode uses `make_dev_host_runner()`;
    Stage 7's worker overrides with a container-routed runner. This keeps
    the BaseTool surface stable across modes.

    Per-instance quota caps the number of calls (default 50) to prevent
    runaway loops. Exceeding the quota returns an error and does not
    invoke the runner.

    Mode enforcement: if `GUUDFLOW_MODE=production` is set in the
    environment AND the runner is the dev host runner, calls fail with
    a clear error. Custom runners (e.g. the container runner from
    Stage 7) are unaffected — they should not be used in dev mode either,
    so the runner identity is the signal.
    """

    name = "code-exec"
    description = "Execute code (Python or shell). In dev mode runs on host with hardening (env scrub, cwd pin, rlimits, audit); in production routes to sandbox container."
    input_schema = {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": ["python", "shell"]},
            "code": {"type": "string"},
            "timeout_s": {"type": "number", "description": "default 30"},
        },
        "required": ["language", "code"],
    }

    def __init__(
        self,
        runner: CodeRunner | None = None,
        quota: int = DEFAULT_QUOTA,
        is_dev_host_runner: bool | None = None,
    ):
        # If runner is None, fall back to the bare dev runner.
        # `is_dev_host_runner` flags whether the runner is the unsandboxed
        # host runner — used for production mode enforcement. When
        # callers pass `make_dev_host_runner()` they should set this True.
        self.runner = runner or _dev_host_runner
        self.quota = quota
        self._calls = 0
        if is_dev_host_runner is None:
            self._is_dev_host_runner = runner is None
        else:
            self._is_dev_host_runner = is_dev_host_runner

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        language = arguments.get("language")
        code = arguments.get("code")
        timeout = arguments.get("timeout_s", 30.0)
        if language not in ("python", "shell"):
            return {"error": "language must be 'python' or 'shell'"}
        if not isinstance(code, str) or not code.strip():
            return {"error": "missing required argument: code"}

        # Mode enforcement (DESIGN §29 rule 61).
        if self._is_dev_host_runner and os.environ.get("GUUDFLOW_MODE") == "production":
            return {
                "error": (
                    "code-exec refuses to run on the host in production mode. "
                    "The runner must be the container-routed runner. "
                    "(Stage 7 worker/docker_manager wires this up.)"
                ),
            }

        # Per-run quota.
        if self._calls >= self.quota:
            return {
                "error": f"code-exec quota exhausted ({self.quota} calls); refusing further executions",
                "quota": self.quota,
            }
        self._calls += 1

        return await self.runner(language, [], {"code": code, "timeout_s": timeout})


# ── code:outline ────────────────────────────────────────────────────────────


class CodeOutline(BaseTool):
    """Structural outline of a single source file.

    Returns imports, top-level classes (with their methods), top-level
    functions, and top-level constants — without the body of any
    function. For a 2000-line file the outline is typically ~50 lines of
    JSON, so this is a cheap reconnaissance tool that the agent can use
    before deciding which `fs:read_range` to perform.

    Reuses the tree-sitter grammars already loaded by `core/repo_map.py`.
    """

    name = "code-outline"
    description = "Extract structure of a source file: imports, classes (with methods), functions. Cheap reconnaissance before fs-read_range."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return {"error": "missing required argument: path"}

        try:
            text = await self.files.read(path)
        except PathSafetyError as e:
            return {"error": f"path safety: {e}"}
        except FileNotFoundError:
            return {"error": f"not found: {path}"}
        except UnicodeDecodeError:
            return {"error": "file is not UTF-8 text"}
        except OSError as e:
            return {"error": f"read failed: {e}"}

        suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if suffix == ".py":
            outline = _outline_python(text)
        elif suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            outline = _outline_javascript(text)
        else:
            return {"error": f"unsupported language for outline: {suffix or '<no extension>'}"}

        return {"ok": True, "path": path, **outline}


def _outline_python(source: str) -> dict[str, Any]:
    import tree_sitter_python
    from tree_sitter import Language, Parser
    lang = Language(tree_sitter_python.language(), "python")
    parser = Parser()
    parser.set_language(lang)
    src_bytes = source.encode("utf-8")
    tree = parser.parse(src_bytes)
    root = tree.root_node

    def text_of(node) -> str:
        return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def signature_of(fn_node) -> str:
        """`def foo(a, b=2):` → `foo(a, b=2)`."""
        name_node = fn_node.child_by_field_name("name")
        params = fn_node.child_by_field_name("parameters")
        name = text_of(name_node) if name_node else "<anon>"
        sig_params = text_of(params) if params else "()"
        return f"{name}{sig_params}"

    imports: list[str] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    constants: list[str] = []

    def handle_class(node) -> None:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        cls_name = text_of(name_node) if name_node else "<anon>"
        methods: list[dict[str, Any]] = []
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    methods.append({
                        "name": signature_of(child),
                        "line": child.start_point[0] + 1,
                    })
                elif child.type == "decorated_definition":
                    inner = child.child_by_field_name("definition")
                    if inner is not None and inner.type == "function_definition":
                        methods.append({
                            "name": signature_of(inner),
                            "line": child.start_point[0] + 1,
                        })
        classes.append({
            "name": cls_name,
            "line": node.start_point[0] + 1,
            "methods": methods,
        })

    for node in root.children:
        if node.type == "class_definition":
            handle_class(node)
        elif node.type == "function_definition":
            functions.append({
                "name": signature_of(node),
                "line": node.start_point[0] + 1,
            })
        elif node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is None:
                continue
            if inner.type == "class_definition":
                handle_class(inner)
            elif inner.type == "function_definition":
                functions.append({
                    "name": signature_of(inner),
                    "line": node.start_point[0] + 1,
                })
        elif node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append(text_of(child))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        imports.append(text_of(name))
        elif node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            if module is not None:
                imports.append(f"from {text_of(module)}")
        elif node.type == "expression_statement":
            # Top-level constants like FOO = 1.
            if node.children and node.children[0].type == "assignment":
                target = node.children[0].child_by_field_name("left")
                if target is not None:
                    constants.append(text_of(target))

    return {
        "language": "python",
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "constants": constants,
    }


def _outline_javascript(source: str) -> dict[str, Any]:
    import tree_sitter_javascript
    from tree_sitter import Language, Parser
    lang = Language(tree_sitter_javascript.language(), "javascript")
    parser = Parser()
    parser.set_language(lang)
    src_bytes = source.encode("utf-8")
    tree = parser.parse(src_bytes)
    root = tree.root_node

    def text_of(node) -> str:
        return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    imports: list[str] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    constants: list[str] = []

    def handle_class(node) -> None:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        cls_name = text_of(name_node) if name_node else "<anon>"
        methods: list[dict[str, Any]] = []
        if body is not None:
            for child in body.children:
                if child.type == "method_definition":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        methods.append({
                            "name": text_of(name) + "()",
                            "line": child.start_point[0] + 1,
                        })
        classes.append({
            "name": cls_name,
            "line": node.start_point[0] + 1,
            "methods": methods,
        })

    def scan(parent) -> None:
        for child in parent.children:
            if child.type == "class_declaration":
                handle_class(child)
            elif child.type == "function_declaration":
                name = child.child_by_field_name("name")
                if name is not None:
                    functions.append({
                        "name": text_of(name) + "()",
                        "line": child.start_point[0] + 1,
                    })
            elif child.type in ("lexical_declaration", "variable_declaration"):
                for declarator in child.children:
                    if declarator.type != "variable_declarator":
                        continue
                    name = declarator.child_by_field_name("name")
                    value = declarator.child_by_field_name("value")
                    if name is None:
                        continue
                    if value is not None and value.type in (
                        "arrow_function", "function_expression", "function",
                    ):
                        functions.append({
                            "name": text_of(name) + "()",
                            "line": declarator.start_point[0] + 1,
                        })
                    else:
                        constants.append(text_of(name))
            elif child.type == "import_statement":
                source_node = child.child_by_field_name("source")
                if source_node is not None:
                    imports.append(text_of(source_node).strip("'\""))
            elif child.type == "export_statement":
                # Recurse — exported declarations live inside.
                scan(child)

    scan(root)

    return {
        "language": "javascript",
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "constants": constants,
    }
