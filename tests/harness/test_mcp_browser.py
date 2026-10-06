"""Tests for the host-side `screenshot` MCP tool (S19)."""

from __future__ import annotations

import base64
import json
import struct
import threading
import zlib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from core.harness.mcp_browser import (
    CaptureError,
    CaptureFn,
    _png_size,
    find_chromium_executable,
    playwright_capture,
    tools,
)
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.tool_context import RunToolContext
from core.signals import SignalType
from core.workspace import Workspace
from tests.harness.conftest import init_repo

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_png(width: int = 4, height: int = 3) -> bytes:
    """A minimal valid RGB PNG."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def identity(role: str = "builder") -> McpIdentity:
    return McpIdentity(run_id="r", role=role, tools=frozenset({"screenshot"}))


@pytest.fixture
def ctx(tmp_path: Path) -> RunToolContext:
    clone = init_repo(tmp_path / "clones" / "builder")
    run_root = tmp_path / "run"
    run_root.mkdir()
    return RunToolContext(run_id="r", workspace=Workspace(run_id="r", goal="g"),
                          run_root=run_root, clone_for=lambda role: clone,
                          sandbox_host_for=lambda role: "127.0.0.1")


class FakeCapture:
    """Records calls and returns canned PNG bytes (or raises)."""

    def __init__(self, png: bytes | None = None, error: Exception | None = None) -> None:
        self.png = png if png is not None else make_png(1280, 800)
        self.error = error
        self.calls: list[tuple[str, int, int, bool, str]] = []
        self.extras: list[dict[str, Any]] = []

    async def __call__(self, url: str, width: int, height: int, full_page: bool,
                       allowed_origin: str, **extra: Any) -> bytes:
        self.calls.append((url, width, height, full_page, allowed_origin))
        self.extras.append(extra)
        if self.error:
            raise self.error
        return self.png


def screenshot_tool(ctx: RunToolContext, capture: CaptureFn) -> McpTool:
    (tool,) = tools(ctx, capture=capture)
    assert tool.name == "screenshot"
    return tool


async def call(tool: McpTool, **args: Any) -> ToolResult:
    return await tool.handler(identity(), args)


# ── argument validation ─────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    {},                                      # port missing
    {"port": 80},                            # below 1024
    {"port": 70000},
    {"port": "3000"},
    {"port": True},
    {"port": 3000, "path": "x"},             # no leading slash
    {"port": 3000, "path": "//evil.com/x"},
    {"port": 3000, "path": "/redirect?u=http://evil.com"},
    {"port": 3000, "path": "/a b"},
    {"port": 3000, "path": "/\\evil.com"},
    {"port": 3000, "path": 5},
    {"port": 3000, "width": 319},
    {"port": 3000, "width": 2561},
    {"port": 3000, "height": 239},
    {"port": 3000, "height": 2001},
    {"port": 3000, "full_page": "yes"},
    {"port": 3000, "url": "http://evil.com"},
])
async def test_invalid_arguments_are_errors_and_never_capture(ctx: RunToolContext,
                                                              args: dict[str, Any]) -> None:
    fake = FakeCapture()
    result = await screenshot_tool(ctx, fake).handler(identity(), args)
    assert result.is_error
    assert result.text.startswith("invalid arguments")
    assert fake.calls == []
    assert ctx.workspace.board == []


@pytest.mark.parametrize("port,width,height", [(1024, 320, 240), (65535, 2560, 2000)])
async def test_boundary_values_are_accepted(ctx: RunToolContext, port: int, width: int,
                                            height: int) -> None:
    fake = FakeCapture()
    result = await call(screenshot_tool(ctx, fake), port=port, width=width, height=height)
    assert not result.is_error
    assert fake.calls[0][:4] == (f"http://127.0.0.1:{port}/", width, height, False)


# ── happy path with an injected capture ─────────────────────────────────────
async def test_success_saves_files_posts_signal_and_returns_image(ctx: RunToolContext) -> None:
    png = make_png(1280, 800)
    fake = FakeCapture(png)
    result = await call(screenshot_tool(ctx, fake), port=5173, path="/app?x=1", full_page=True)

    assert not result.is_error
    assert fake.calls == [("http://127.0.0.1:5173/app?x=1", 1280, 800, True,
                           "http://127.0.0.1:5173")]

    clone = ctx.clone_for("builder")
    (saved,) = list((clone / ".guudflow" / "screens").iterdir())
    assert saved.name.endswith("-5173.png")
    assert saved.read_bytes() == png
    copies = list((ctx.run_root / "screenshots" / "builder").iterdir())
    assert [p.name for p in copies] == [saved.name]
    assert copies[0].read_bytes() == png

    assert result.text == (f"Saved .guudflow/screens/{saved.name} (1280x800). "
                           "Read it with the read tool if you need it again.")
    assert result.images == [(base64.b64encode(png).decode(), "image/png")]
    assert result.to_mcp()["content"][1]["type"] == "image"

    (signal,) = ctx.workspace.board
    assert signal.type == SignalType.SCREENSHOT_READY
    assert signal.from_agent == "builder"
    assert json.loads(signal.detail or "") == {
        "role": "builder",
        "path": f"screenshots/builder/{saved.name}",
        "clone_path": f".guudflow/screens/{saved.name}",
        "url": "http://127.0.0.1:5173/app?x=1",
        "width": 1280, "height": 800,
    }


async def test_reported_size_comes_from_the_png(ctx: RunToolContext) -> None:
    fake = FakeCapture(make_png(1280, 3000))  # full-page capture taller than the viewport
    result = await call(screenshot_tool(ctx, fake), port=3000, full_page=True)
    assert "(1280x3000)" in result.text


async def test_screens_go_to_host_only_tool_dir(ctx: RunToolContext, tmp_path: Path) -> None:
    """O8 review: the host writes screenshots into its own tool dir, never through a path
    in the agent's worktree (where a planted symlink could redirect the write)."""
    import dataclasses
    tools = tmp_path / "tooldir"
    ctx2 = dataclasses.replace(ctx, tool_dir_for=lambda role: tools)
    work = ctx2.clone_for("builder")
    (work / ".guudflow").mkdir(exist_ok=True)
    (work / ".guudflow" / "screens").symlink_to(tmp_path / "elsewhere")
    await call(screenshot_tool(ctx2, FakeCapture()), port=3000)
    assert list((tools / "screens").glob("*.png"))
    assert not (tmp_path / "elsewhere").exists()


async def test_role_with_odd_characters_stays_inside_run_folder(ctx: RunToolContext) -> None:
    tool = screenshot_tool(ctx, FakeCapture())
    result = await tool.handler(identity("../../etc"), {"port": 3000})
    assert not result.is_error
    (signal,) = ctx.workspace.board
    rel = json.loads(signal.detail or "")["path"]
    assert (ctx.run_root / rel).is_file()
    assert (ctx.run_root / rel).resolve().is_relative_to(ctx.run_root.resolve())


# ── failures never raise ────────────────────────────────────────────────────
@pytest.mark.parametrize("error,fragment", [
    (CaptureError("navigation blocked: x"), "navigation blocked"),
    (RuntimeError("Page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:3000/"),
     "connection refused"),
    (TimeoutError(), "timed out"),
    (RuntimeError("boom\nstack"), "boom"),
])
async def test_capture_failures_become_error_results(ctx: RunToolContext, error: Exception,
                                                     fragment: str) -> None:
    result = await call(screenshot_tool(ctx, FakeCapture(error=error)), port=3000)
    assert result.is_error and fragment in result.text
    assert result.images == []
    assert ctx.workspace.board == []


async def test_empty_capture_is_an_error(ctx: RunToolContext) -> None:
    result = await call(screenshot_tool(ctx, FakeCapture(png=b"")), port=3000)
    assert result.is_error


async def test_unknown_sandbox_is_an_error(ctx: RunToolContext) -> None:
    def no_sandbox(role: str) -> str:
        raise KeyError(role)

    ctx.sandbox_host_for = no_sandbox
    result = await call(screenshot_tool(ctx, FakeCapture()), port=3000)
    assert result.is_error and "sandbox" in result.text


def test_png_size_helper() -> None:
    assert _png_size(make_png(7, 9)) == (7, 9)
    assert _png_size(b"not a png at all, but long enough to index") is None


# ── real Playwright ─────────────────────────────────────────────────────────
class _Counting(BaseHTTPRequestHandler):
    """Records every request path it receives in `self.server.hits`."""

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        routes = getattr(self.server, "routes", {})
        if self.path in routes:
            status, headers, body = routes[self.path]
        else:
            status, headers, body = 404, {}, b"not found"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Site:
    """A tiny HTTP server on a background thread that counts its requests."""

    def __init__(self, bind: str, routes: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.server = ThreadingHTTPServer((bind, 0), _Counting)
        self.server.hits = []  # type: ignore[attr-defined]
        self.server.routes = routes  # type: ignore[attr-defined]
        self.port: int = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def hits(self) -> list[str]:
        return self.server.hits  # type: ignore[attr-defined,no-any-return]

    def __enter__(self) -> Site:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def chromium_works() -> Iterator[None]:
    """Skip the real-browser tests when Chromium cannot launch here."""
    import asyncio

    async def probe() -> None:
        with Site("127.0.0.1", {"/": (200, {"Content-Type": "text/html"}, b"<p>hi</p>")}) as s:
            await playwright_capture(f"http://127.0.0.1:{s.port}/", 320, 240, False,
                                     f"http://127.0.0.1:{s.port}")

    try:
        asyncio.run(probe())
    except Exception as exc:
        pytest.skip(f"Chromium cannot launch here: {exc}")
    yield


def html(body: str) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "text/html"}, body.encode()


async def test_real_capture_blocks_every_other_origin(ctx: RunToolContext,
                                                      chromium_works: None) -> None:
    with Site("0.0.0.0", {}) as other:
        page = f"""<!doctype html><html><body style="background:#def">
        <h1>Hello screenshot</h1>
        <img src="http://127.0.0.2:{other.port}/x.png">
        <img src="http://127.0.0.1:{other.port}/same-host-other-port.png">
        <img src="/ok.png">
        <script>
          fetch("http://example.com").catch(() => {{}});
          fetch("http://127.0.0.2:{other.port}/fetch").catch(() => {{}});
          try {{ new WebSocket("ws://127.0.0.2:{other.port}/ws"); }} catch (e) {{}}
          var l = document.createElement("link"); l.rel = "stylesheet";
          l.href = "http://127.0.0.2:{other.port}/style.css"; document.head.appendChild(l);
        </script></body></html>"""
        with Site("127.0.0.1", {"/": html(page)}) as site:
            (tool,) = tools(ctx)  # default Playwright capture
            result = await call(tool, port=site.port)

            assert not result.is_error, result.text
            ((b64, mime),) = result.images
            png = base64.b64decode(b64)
            assert mime == "image/png"
            assert png.startswith(PNG_SIGNATURE) and len(png) > 1000
            assert _png_size(png) == (1280, 800)
            assert "/" in site.hits and "/ok.png" in site.hits  # same origin is allowed

        assert other.hits == []  # the other origin was NEVER requested
    (signal,) = ctx.workspace.board
    saved = ctx.run_root / json.loads(signal.detail or "")["path"]
    assert saved.read_bytes() == png


async def test_real_capture_blocks_redirect_to_other_origin(ctx: RunToolContext,
                                                            chromium_works: None) -> None:
    with Site("0.0.0.0", {}) as other:
        target = f"http://127.0.0.2:{other.port}/landing"
        redirect = (302, {"Location": target}, b"")
        with Site("127.0.0.1", {"/go": redirect}) as site:
            (tool,) = tools(ctx)
            result = await call(tool, port=site.port, path="/go")
        assert other.hits == []
    assert result.is_error
    assert "blocked" in result.text and target in result.text
    assert ctx.workspace.board == []


async def test_real_capture_follows_same_origin_redirect(ctx: RunToolContext,
                                                         chromium_works: None,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    # A proxy in the environment must not be used for the host -> sandbox hop.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    routes = {"/old": (302, {"Location": "/new"}, b""), "/new": html("<h1>moved</h1>")}
    with Site("127.0.0.1", routes) as site:
        (tool,) = tools(ctx)
        result = await call(tool, port=site.port, path="/old")
        assert site.hits[:2] == ["/old", "/new"]
    assert not result.is_error, result.text


async def test_real_capture_connection_refused(ctx: RunToolContext, chromium_works: None) -> None:
    with Site("127.0.0.1", {}) as site:
        port = site.port
    (tool,) = tools(ctx)  # server is now closed, nothing listens on `port`
    result = await call(tool, port=port)
    assert result.is_error and "connection refused" in result.text


async def test_real_capture_full_page_is_taller_than_viewport(ctx: RunToolContext,
                                                              chromium_works: None) -> None:
    page = '<body style="margin:0"><div style="height:3000px;background:#abc"></div></body>'
    with Site("127.0.0.1", {"/": html(page)}) as site:
        (tool,) = tools(ctx)
        result = await call(tool, port=site.port, width=640, height=480, full_page=True)
    assert not result.is_error, result.text
    size = _png_size(base64.b64decode(result.images[0][0]))
    assert size is not None and size[0] == 640 and size[1] >= 3000


def test_find_chromium_executable_prefers_env(monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
    fake = tmp_path / "chrome"
    fake.write_text("")
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", str(fake))
    assert find_chromium_executable() == str(fake)


def test_find_chromium_executable_picks_newest_build(monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path: Path) -> None:
    for build in ("chromium-900", "chromium-1194", "chromium-1000"):
        exe = tmp_path / build / "chrome-linux" / "chrome"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert find_chromium_executable() == str(tmp_path / "chromium-1194" / "chrome-linux" / "chrome")


# ── static files served by the host ─────────────────────────────────────────
async def test_file_mode_serves_clone_over_http_and_refuses_bad_paths(ctx: RunToolContext) -> None:
    import urllib.error
    import urllib.request

    clone = ctx.clone_for("builder")
    (clone / "web").mkdir()
    (clone / "web" / "index.html").write_text("<h1>hi</h1>")
    (clone / ".env").write_text("SECRET")
    (clone / ".guudflow").mkdir(exist_ok=True)
    (clone / ".guudflow" / "x.txt").write_text("tool")
    outside = clone.parent / "outside.txt"
    outside.write_text("nope")
    (clone / "web" / "link.txt").symlink_to(outside)
    (clone / "web" / "dir").mkdir()

    def fetch(base: str, path: str, method: str = "GET") -> tuple[int, bytes]:
        req = urllib.request.Request(base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, b""

    seen: dict[str, Any] = {}

    async def probing(url: str, width: int, height: int, full_page: bool, origin: str,
                      **extra: Any) -> bytes:
        seen["url"], seen["origin"], seen["extra"] = url, origin, extra
        import asyncio
        seen["results"] = await asyncio.to_thread(lambda: {
            "ok": fetch(origin, "/web/index.html"),
            "head": fetch(origin, "/web/index.html", "HEAD")[0],
            "post": fetch(origin, "/web/index.html", "POST")[0],
            "dotfile": fetch(origin, "/.env")[0],
            "guudflow": fetch(origin, "/.guudflow/x.txt")[0],
            "dir": fetch(origin, "/web/dir")[0],
            "root": fetch(origin, "/")[0],
            "symlink": fetch(origin, "/web/link.txt")[0],
            "dotdot": fetch(origin, "/web/%2e%2e/%2e%2e/outside.txt")[0],
        })
        return make_png()

    result = await call(screenshot_tool(ctx, probing), file="web/index.html", wait_ms=0,
                        keys=["ArrowLeft"])
    assert not result.is_error, result.text
    assert seen["url"] == seen["origin"] + "/web/index.html"
    assert seen["origin"].startswith("http://127.0.0.1:")
    assert seen["extra"] == {"wait_ms": 0, "keys": ["ArrowLeft"]}
    res = seen["results"]
    assert res["ok"] == (200, b"<h1>hi</h1>")
    assert res["head"] == 200
    assert res["post"] == 405
    assert all(res[k] == 404 for k in ("dotfile", "guudflow", "dir", "root", "symlink", "dotdot"))
    # the server is gone afterwards
    with pytest.raises(OSError):
        fetch(seen["origin"], "/web/index.html")


@pytest.mark.parametrize("args", [
    {"file": "../x.html"},
    {"file": ".git/config"},
    {"file": "web/.hidden.html"},
    {"file": "http://evil.com/x"},
    {"file": "missing.html"},
    {"file": "a.html", "port": 3000},
    {"file": "a.html", "wait_ms": 5001},
    {"file": "a.html", "keys": ["a"] * 21},
    {"file": "a.html", "keys": [1]},
    {"port": 3000, "keys": ["a"]},
])
async def test_file_mode_invalid_arguments(ctx: RunToolContext, args: dict[str, Any]) -> None:
    (ctx.clone_for("builder") / "a.html").write_text("x")
    fake = FakeCapture()
    result = await call(screenshot_tool(ctx, fake), **args)
    assert result.is_error
    assert fake.calls == []
