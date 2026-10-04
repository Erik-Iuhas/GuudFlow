"""visual_diff: pixel comparison, baselines, inputs/ references, path safety (plan §5.10)."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from core.harness import artifacts
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.mcp_visual import tools
from core.harness.tool_context import RunToolContext

IDENT = McpIdentity("run-1", "builder", frozenset({"visual_diff"}))


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.run_root = tmp_path / "run"
        self.clone = tmp_path / "clone"
        self.run_root.mkdir()
        self.clone.mkdir()
        self.tmp = tmp_path
        ctx = RunToolContext(
            run_id="run-1", workspace=None,  # type: ignore[arg-type]  # not used by this tool
            run_root=self.run_root, clone_for=lambda role: self.clone,
            sandbox_host_for=lambda role: "127.0.0.1")
        (self.tool,) = tools(ctx)

    async def call(self, **args: Any) -> ToolResult:
        return await self.tool.handler(IDENT, args)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def page(size: tuple[int, int] = (100, 100), box: tuple[int, int, int, int] | None = None
         ) -> Image.Image:
    img = Image.new("RGB", size, (255, 255, 255))
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(0, 0, 0))
    return img


def fraction_of(result: ToolResult) -> float:
    line = next(ln for ln in result.text.splitlines() if ln.startswith("changed_fraction:"))
    return float(line.split()[1])


def test_tool_shape(env: Env) -> None:
    assert isinstance(env.tool, McpTool)
    assert env.tool.name == "visual_diff"
    schema = env.tool.schema_for(IDENT)
    assert schema["required"] == ["current"]


async def test_identical_is_match_and_no_inline_image(env: Env) -> None:
    page().save(env.clone / "a.png")
    page().save(env.clone / "ref.png")
    res = await env.call(current="a.png", reference="ref.png")
    assert not res.is_error
    assert "verdict: match" in res.text
    assert fraction_of(res) == 0.0
    assert res.images == []
    assert (env.clone / ".guudflow/screens/diff-a.png").is_file()
    assert (env.run_root / "screenshots/builder/diff-a.png").is_file()


async def test_shifted_box_differs_with_expected_fraction_and_inline_diff(env: Env) -> None:
    page(box=(10, 10, 49, 49)).save(env.clone / "ref.png")      # 40x40 = 1600 px
    page(box=(20, 10, 59, 49)).save(env.clone / "a.png")        # shifted right by 10
    res = await env.call(current="a.png", reference="ref.png")
    assert "verdict: differs" in res.text
    # 10 columns x 40 rows disappear and 10 x 40 appear = 800 px of 10_000.
    assert fraction_of(res) == pytest.approx(0.08, abs=0.001)
    assert len(res.images) == 1
    data, mime = res.images[0]
    assert mime == "image/png"
    with Image.open(io.BytesIO(base64.b64decode(data))) as img:
        assert img.size == (100, 100)
        assert img.convert("RGB").getpixel((55, 12)) == (255, 0, 0)    # newly black: red
        assert img.convert("RGB").getpixel((90, 90)) != (255, 0, 0)    # unchanged: dimmed
    assert not res.is_error


async def test_small_differences_below_tolerance_ignored(env: Env) -> None:
    Image.new("RGB", (20, 20), (100, 100, 100)).save(env.clone / "ref.png")
    Image.new("RGB", (20, 20), (110, 110, 110)).save(env.clone / "a.png")
    res = await env.call(current="a.png", reference="ref.png")
    assert fraction_of(res) == 0.0


async def test_threshold_controls_verdict(env: Env) -> None:
    page().save(env.clone / "ref.png")
    page(box=(0, 0, 9, 9)).save(env.clone / "a.png")             # 100 px = 1%
    assert "verdict: match" in (await env.call(current="a.png", reference="ref.png")).text
    assert "verdict: differs" in (await env.call(current="a.png", reference="ref.png",
                                                 threshold=0.005)).text


async def test_size_mismatch_reference_resized(env: Env) -> None:
    page((200, 200), box=(40, 40, 119, 119)).save(env.clone / "ref.png")
    page((100, 100), box=(20, 20, 59, 59)).save(env.clone / "a.png")
    res = await env.call(current="a.png", reference="ref.png")
    assert not res.is_error
    assert "reference resized from 200x200 to 100x100" in res.text
    assert "current: a.png 100x100" in res.text and "reference: ref.png 200x200" in res.text
    assert fraction_of(res) < 0.02


async def test_baseline_flow(env: Env) -> None:
    page().save(env.clone / "home.png")
    first = await env.call(current="home.png")
    assert "baseline recorded" in first.text and not first.is_error
    baseline = env.run_root / "baselines" / "builder" / "home.png"
    assert baseline.is_file()
    assert not (env.clone / ".guudflow/screens/diff-home.png").exists()

    page(box=(0, 0, 49, 49)).save(env.clone / "home.png")        # 25% changed
    second = await env.call(current="home.png")
    assert "verdict: differs" in second.text
    assert fraction_of(second) == pytest.approx(0.25, abs=0.001)
    with Image.open(baseline) as img:                              # baseline moved on
        assert img.convert("RGB").getpixel((10, 10)) == (0, 0, 0)

    third = await env.call(current="home.png")                    # same image again
    assert "verdict: match" in third.text and fraction_of(third) == 0.0


async def test_inputs_reference(env: Env) -> None:
    (env.run_root / "inputs" / "mockups").mkdir(parents=True)
    page(box=(10, 10, 49, 49)).save(env.run_root / "inputs/mockups/home.png")
    page(box=(10, 10, 49, 49)).save(env.clone / "shot.png")
    res = await env.call(current="shot.png", reference="inputs/mockups/home.png")
    assert "verdict: match" in res.text
    assert "reference: inputs/mockups/home.png" in res.text


async def test_missing_or_bad_inputs_are_errors(env: Env) -> None:
    page().save(env.clone / "a.png")
    (env.clone / "text.png").write_text("not an image")
    for args in (
        {},
        {"current": ""},
        {"current": 5},
        {"current": "nope.png"},
        {"current": "text.png"},
        {"current": "a.png", "reference": "nope.png"},
        {"current": "a.png", "reference": "inputs/missing.png"},
        {"current": "a.png", "reference": "inputs"},
        {"current": "a.png", "reference": 7},
        {"current": "a.png", "reference": "a.png", "threshold": 5},
        {"current": "a.png", "reference": "a.png", "threshold": "x"},
    ):
        res = await env.call(**args)
        assert res.is_error, args
        assert "visual_diff failed" in res.text


async def test_non_raster_and_svg_refused(env: Env) -> None:
    (env.clone / "a.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    page().save(env.clone / "ok.png")
    res = await env.call(current="a.svg", reference="ok.png")
    assert res.is_error


async def test_path_escape_refused(env: Env) -> None:
    page().save(env.clone / "a.png")
    page().save(env.tmp / "outside.png")
    (env.clone / "link.png").symlink_to(env.tmp / "outside.png")
    (env.run_root / "inputs").mkdir()
    (env.run_root / "inputs" / "link.png").symlink_to(env.tmp / "outside.png")
    for args in (
        {"current": "../outside.png"},
        {"current": str(env.tmp / "outside.png")},
        {"current": "link.png"},
        {"current": "a.png", "reference": "../outside.png"},
        {"current": "a.png", "reference": "link.png"},
        {"current": "a.png", "reference": "inputs/../../outside.png"},
        {"current": "a.png", "reference": "inputs/link.png"},
    ):
        res = await env.call(**args)
        assert res.is_error, args
    assert not (env.run_root / "baselines").exists()


async def test_oversized_image_refused(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    page().save(env.clone / "a.png")
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 10)
    res = await env.call(current="a.png")
    assert res.is_error


async def test_rgba_and_jpeg_inputs(env: Env) -> None:
    Image.new("RGBA", (30, 30), (0, 0, 0, 0)).save(env.clone / "t.png")     # transparent → white
    Image.new("RGB", (30, 30), (255, 255, 255)).save(env.clone / "w.jpg")
    res = await env.call(current="t.png", reference="w.jpg")
    assert not res.is_error and "verdict: match" in res.text
