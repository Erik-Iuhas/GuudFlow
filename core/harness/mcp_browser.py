"""Host-side `screenshot` MCP tool (plan §5.9, §5.10).

Playwright runs on the HOST, never inside a sandbox. The role's dev server listens
inside its sandbox; the host reaches it at `ctx.sandbox_host_for(role)`. The page
is captured under a navigation lock: every request whose origin is not exactly the
target origin is aborted, so the tool cannot be used to reach any other host.

Files: the PNG is saved in the role's clone at `.guudflow/screens/` (git-excluded, so
the role may `read` it) and copied into the run folder under `screenshots/<role>/`,
which Studio serves statically. The image also goes back inline in the tool result
(MCP image content reaches the model). It is never sent over the websocket.
"""

from __future__ import annotations

import asyncio
import base64
import glob
import logging
import os
import re
import shutil
import struct
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from core.harness.artifacts import safe_resolve
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult, serve_app
from core.harness.tool_context import RunToolContext
from core.signals import SignalType

log = logging.getLogger(__name__)

# (url, width, height, full_page, allowed_origin) -> PNG bytes. For static-file captures the
# tool also passes the keywords `wait_ms` (settle time after load) and `keys` (key names to
# press before the capture); a hook that ignores them should accept **kwargs.
CaptureFn = Callable[[str, int, int, bool, str], Awaitable[bytes]]

SCREEN_DIR = ".guudflow/screens"
TOTAL_TIMEOUT_S = 20.0
SETTLE_MS = 500
DEFAULT_WAIT_MS = 1500
MAX_WAIT_MS = 5000
MAX_KEYS = 20
KEY_DELAY_MS = 120
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_BAD_PATH_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\]")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "port": {"type": "integer", "minimum": 1024, "maximum": 65535,
                 "description": "Port of the dev server running in your sandbox. Give either "
                                "`port` or `file`, not both."},
        "path": {"type": "string", "default": "/",
                 "description": "With `port`: URL path (and query) to open; must start with '/'."},
        "file": {"type": "string",
                 "description": "Static page in your working folder, e.g. 'web/index.html'. The "
                                "host serves it read-only and captures it; no server needed."},
        "wait_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS,
                    "default": DEFAULT_WAIT_MS,
                    "description": "With `file`: wait after load so canvas content renders a frame."},
        "keys": {"type": "array", "items": {"type": "string", "maxLength": 32},
                 "maxItems": MAX_KEYS,
                 "description": "With `file`: key names to press before capture, e.g. "
                                "['ArrowLeft', 'Space']."},
        "width": {"type": "integer", "minimum": 320, "maximum": 2560, "default": 1280},
        "height": {"type": "integer", "minimum": 240, "maximum": 2000, "default": 800},
        "full_page": {"type": "boolean", "default": False,
                      "description": "Capture the whole scrollable page, not just the viewport."},
    },
    "additionalProperties": False,
}

DESCRIPTION = (
    "Take a screenshot of the web app your dev server is serving. The browser runs on the "
    "host and can only reach your own sandbox's dev server on the given port; any other "
    "origin the page references is blocked. For a static page (plain HTML/JS, no build step) "
    "you need no server: pass `file` (e.g. 'web/index.html') instead of `port`, and the host "
    "serves your working folder read-only for the capture. Optional `wait_ms` (default 1500) "
    "lets canvas games draw, and `keys` (e.g. ['ArrowLeft']) are pressed before capturing. "
    "The PNG is saved under .guudflow/screens/ in your clone and returned inline."
)


class CaptureError(Exception):
    """A capture failed in a way worth reporting to the agent verbatim."""


def find_chromium_executable() -> str | None:
    """Locate a Chromium binary: $PLAYWRIGHT_CHROMIUM_EXECUTABLE, else the newest
    `/opt/pw-browsers/chromium-*/chrome-linux/chrome`, else None (Playwright default)."""
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if env and Path(env).is_file():
        return env
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers"
    candidates = glob.glob(f"{root}/chromium-*/chrome-linux/chrome")

    def build(path: str) -> int:
        match = re.search(r"chromium-(\d+)", path)
        return int(match.group(1)) if match else -1

    return max(candidates, key=build) if candidates else None


def _origin(url: str) -> str:
    """scheme://host:port of `url` with the default port made explicit."""
    parts = urlsplit(url)
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme, 0)
    return f"{parts.scheme}://{parts.hostname}:{port}"


def _extra_seconds(wait_ms: int | None, keys: Sequence[str]) -> float:
    """Extra time budget for a capture that waits and presses keys."""
    return ((wait_ms or 0) + (len(keys) + 1) * KEY_DELAY_MS) / 1000


class _StaticSite:
    """Read-only static file server for one role's clone (GET/HEAD only)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def _serve(self, request: Request) -> Response:
        if request.method not in ("GET", "HEAD"):
            return PlainTextResponse("method not allowed", status_code=405,
                                     headers={"Allow": "GET, HEAD"})
        parts = [p for p in request.path_params.get("rel", "").split("/") if p]
        if not parts or any(p.startswith(".") or "\\" in p or "\x00" in p for p in parts):
            return PlainTextResponse("not found", status_code=404)
        target = safe_resolve(self.root, "/".join(parts))  # no `..`, no symlinks on the way
        if target is None or not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(target)

    def app(self) -> Starlette:
        methods = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
        return Starlette(routes=[Route("/{rel:path}", self._serve, methods=methods)])


async def playwright_capture(url: str, width: int, height: int, full_page: bool,
                             allowed_origin: str, *, wait_ms: int | None = None,
                             keys: Sequence[str] = ()) -> bytes:
    """Default `CaptureFn`: headless Chromium via Playwright on the host.

    Aborts every request whose origin differs from `allowed_origin` (this covers
    redirects, subresources and fetch/XHR); `file:` and non-image `data:` URLs are
    blocked too. Waits for `load` plus `wait_ms` (default a 500 ms settle), presses `keys`
    in order, within 20 s plus the extra waiting in total.
    """
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    allowed = _origin(allowed_origin)
    host = urlsplit(url).hostname or ""
    blocked_documents: list[str] = []
    fetch_errors: list[str] = []

    def permitted(target: str) -> bool:
        return target.split(":", 1)[0] in ("http", "https") and _origin(target) == allowed

    async def lock(route: Any) -> None:
        request = route.request
        target = request.url
        if target.startswith("data:image/") and request.resource_type == "image":
            await route.continue_()
            return
        if not permitted(target):
            if request.resource_type == "document":
                blocked_documents.append(target)
            await route.abort("blockedbyclient")
            return
        # Chromium follows redirects without asking Playwright again, so fetch the
        # response here without following it and vet any redirect target ourselves.
        try:
            response = await route.fetch(max_redirects=0)
        except PlaywrightError as exc:
            fetch_errors.append(str(exc))
            await route.abort("failed")
            return
        location = response.headers.get("location")
        if 300 <= response.status < 400 and location:
            destination = urljoin(target, location)
            if not permitted(destination):
                if request.resource_type == "document":
                    blocked_documents.append(destination)
                await route.abort("blockedbyclient")
                return
        await route.fulfill(response=response)

    def blocked_error() -> CaptureError:
        return CaptureError(f"navigation blocked: the page tried to go to {blocked_documents[0]}; "
                            f"only {allowed} is reachable")

    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S + _extra_seconds(wait_ms, keys)):
            async with async_playwright() as pw:
                launch: dict[str, Any] = {
                    # Playwright 1.47 passes --headless=old for headless=True, which recent
                    # Chromium builds removed; ask for new headless ourselves instead.
                    "headless": False,
                    # No proxy (HTTP(S)_PROXY must not apply); no DNS for any other host.
                    "args": ["--headless=new", "--hide-scrollbars", "--mute-audio", "--no-sandbox",
                             "--no-proxy-server",
                             f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {host}"],
                }
                executable = find_chromium_executable()
                if executable:
                    launch["executable_path"] = executable
                browser = await pw.chromium.launch(**launch)
                try:
                    context = await browser.new_context(
                        viewport={"width": width, "height": height}, service_workers="block")
                    # Playwright 1.47 cannot route WebSockets; remove them from the page.
                    await context.add_init_script(
                        "Object.defineProperty(window,'WebSocket',{value:undefined});")
                    await context.route("**/*", lock)
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="load")
                    except PlaywrightError as exc:
                        if blocked_documents:
                            raise blocked_error() from exc
                        if fetch_errors:
                            raise CaptureError(fetch_errors[0]) from exc
                        raise
                    if blocked_documents:
                        raise blocked_error()
                    await page.wait_for_timeout(SETTLE_MS if wait_ms is None else wait_ms)
                    for key in keys:
                        await page.keyboard.press(key)
                        await page.wait_for_timeout(KEY_DELAY_MS)
                    if keys:
                        await page.wait_for_timeout(KEY_DELAY_MS)
                    return await page.screenshot(type="png", full_page=full_page)
                finally:
                    await browser.close()
    except TimeoutError as exc:
        raise CaptureError(f"timed out after {TOTAL_TIMEOUT_S:.0f}s loading {url}") from exc


def _png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk, or None if `data` is not a PNG."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _safe_role(role: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", role) or "role"


def _run_relative(role: str, name: str) -> str:
    """Path of a screenshot relative to the run folder."""
    return f"screenshots/{_safe_role(role)}/{name}"


def _store(tool_dir: Path, run_root: Path, role: str, name: str, png: bytes) -> None:
    """Write the PNG into the role's host-only tool dir and copy it into the run folder.

    The tool dir is mounted read-only at /work/.guudflow, so the agent reads the file at
    `.guudflow/screens/<name>` but can never plant a symlink where the host writes.
    """
    clone_file = tool_dir / "screens" / name
    clone_file.parent.mkdir(parents=True, exist_ok=True)
    clone_file.write_bytes(png)
    run_file = run_root / _run_relative(role, name)
    run_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clone_file, run_file)


def _int_arg(args: dict[str, Any], key: str, default: int | None, lo: int, hi: int) -> int:
    value = args.get(key, default)
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return value


def _validate_file(args: dict[str, Any]) -> tuple[str, int, list[str]]:
    """Check the static-file arguments; returns (file, wait_ms, keys)."""
    if "port" in args or "path" in args:
        raise ValueError("give either `file` or `port` (with `path`), not both")
    raw = args["file"]
    if (not isinstance(raw, str) or not raw.strip() or "://" in raw or "?" in raw or "#" in raw
            or _BAD_PATH_CHARS.search(raw.strip())):
        raise ValueError("file must be a plain path inside your working folder, "
                         "e.g. 'web/index.html'")
    file = raw.strip().removeprefix("/work/").lstrip("/")
    parts = file.split("/")
    if not file or ".." in parts or any(p.startswith(".") for p in parts if p):
        raise ValueError("file must stay inside your working folder and may not name dotfiles")
    wait_ms = _int_arg(args, "wait_ms", DEFAULT_WAIT_MS, 0, MAX_WAIT_MS)
    keys = args.get("keys", [])
    if (not isinstance(keys, list) or len(keys) > MAX_KEYS
            or not all(isinstance(k, str) and 0 < len(k) <= 32 and k.isprintable() for k in keys)):
        raise ValueError(f"keys must be a list of at most {MAX_KEYS} key names such as 'ArrowLeft'")
    return file, wait_ms, keys


def _validate(args: dict[str, Any]) -> tuple[int, str, int, int, bool]:
    """Check tool arguments for the dev-server mode; returns (port, path, width, height, full_page)."""
    unknown = set(args) - set(SCHEMA["properties"])
    if unknown:
        raise ValueError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    if "wait_ms" in args or "keys" in args:
        raise ValueError("wait_ms and keys only work with `file`")
    port = _int_arg(args, "port", None, 1024, 65535)
    width = _int_arg(args, "width", 1280, 320, 2560)
    height = _int_arg(args, "height", 800, 240, 2000)
    path = args.get("path", "/")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if (not path.startswith("/") or path.startswith("//") or "://" in path
            or _BAD_PATH_CHARS.search(path)):
        raise ValueError("path must start with a single '/', contain no '://', "
                         "whitespace or backslashes, and name a path on your own dev server")
    full_page = args.get("full_page", False)
    if not isinstance(full_page, bool):
        raise ValueError("full_page must be a boolean")
    return port, path, width, height, full_page


def _explain(exc: Exception, url: str) -> str:
    """Turn a capture failure into a message an agent can act on."""
    text = str(exc)
    if isinstance(exc, CaptureError) and "ECONNREFUSED" not in text:
        return text
    if "ERR_CONNECTION_REFUSED" in text or "ECONNREFUSED" in text:
        return (f"connection refused at {url}: nothing is listening on that port. Start your dev "
                "server first and bind it to 0.0.0.0, not just localhost.")
    if "ERR_NAME_NOT_RESOLVED" in text or "ERR_ADDRESS_UNREACHABLE" in text:
        return f"could not reach {url}: host unreachable"
    if "Timeout" in text or isinstance(exc, TimeoutError):
        return f"timed out loading {url}"
    return f"screenshot failed for {url}: {text.splitlines()[0] if text else type(exc).__name__}"


def tools(ctx: RunToolContext, *, capture: CaptureFn | None = None) -> list[McpTool]:
    """The `screenshot` tool for a run. `capture` is injectable for tests."""
    take = capture or playwright_capture

    async def capture_file(role: str, args: dict[str, Any]) -> tuple[bytes, str, str]:
        """Serve the role's clone on a temporary host-only server and capture one file."""
        file, wait_ms, keys = _validate_file(args)
        width = _int_arg(args, "width", 1280, 320, 2560)
        height = _int_arg(args, "height", 800, 240, 2000)
        full_page = args.get("full_page", False)
        if not isinstance(full_page, bool):
            raise ValueError("full_page must be a boolean")
        root = ctx.clone_for(role)
        target = safe_resolve(root, file)
        if target is None or not target.is_file():
            raise ValueError(f"no such file in your working folder: {file}")
        server = await serve_app(_StaticSite(root).app(), host="127.0.0.1", port=0)
        try:
            origin = server.url
            url = f"{origin}/{quote(file)}"
            png = await asyncio.wait_for(
                take(url, width, height, full_page, origin, wait_ms=wait_ms, keys=keys),
                timeout=TOTAL_TIMEOUT_S + 5 + _extra_seconds(wait_ms, keys))
        finally:
            await server.stop()
        return png, f"{origin}/{file}", "file"

    async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        role = identity.role
        label = "dev"
        if "file" in args:
            try:
                png, url, label = await capture_file(role, args)
            except ValueError as exc:
                return ToolResult(f"invalid arguments: {exc}", is_error=True)
            except Exception as exc:  # report, never raise into the agent's turn
                if not isinstance(exc, (CaptureError, TimeoutError)):
                    log.warning("static screenshot of %s failed: %s", args.get("file"), exc)
                return ToolResult(_explain(exc, str(args.get("file"))), is_error=True)
            width, height, port = 1280, 800, 0
        else:
            try:
                port, path, width, height, full_page = _validate(args)
            except ValueError as exc:
                return ToolResult(f"invalid arguments: {exc}", is_error=True)
            try:
                host = ctx.sandbox_host_for(role)
            except Exception as exc:
                return ToolResult(f"cannot locate the sandbox for role {role!r}: {exc}",
                                  is_error=True)
            origin = f"http://{host}:{port}"
            url = f"{origin}{path}"
            try:
                png = await asyncio.wait_for(take(url, width, height, full_page, origin),
                                             timeout=TOTAL_TIMEOUT_S + 5)
            except Exception as exc:  # report, never raise into the agent's turn
                if not isinstance(exc, (CaptureError, TimeoutError)):
                    log.warning("screenshot of %s failed: %s", url, exc)
                return ToolResult(_explain(exc, url), is_error=True)
        if not png:
            return ToolResult(f"screenshot of {url} came back empty", is_error=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
        name = f"{stamp}-{port or label}.png"
        try:
            await asyncio.to_thread(_store, ctx.tool_dir(role), ctx.run_root, role, name, png)
        except OSError as exc:
            return ToolResult(f"could not save the screenshot: {exc}", is_error=True)

        size = _png_size(png) or (width, height)
        clone_path = f"{SCREEN_DIR}/{name}"
        ctx.post(SignalType.SCREENSHOT_READY, role, f"Screenshot of {url}",
                 {"role": role, "path": _run_relative(role, name), "clone_path": clone_path,
                  "url": url, "width": size[0], "height": size[1]})
        return ToolResult(
            f"Saved {clone_path} ({size[0]}x{size[1]}). "
            "Read it with the read tool if you need it again.",
            images=[(base64.b64encode(png).decode("ascii"), "image/png")])

    return [McpTool(name="screenshot", description=DESCRIPTION, input_schema=SCHEMA,
                    handler=handler)]
