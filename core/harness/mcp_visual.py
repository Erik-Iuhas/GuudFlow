"""Host-side `visual_diff` MCP tool: a deterministic pre-check before any LLM review (plan §5.10).

Compares a screenshot with a reference image (a mockup, another file in the clone, or
the role's baseline from the previous round) and returns a changed-pixel fraction, a
verdict and a diff image. No LLM cost, no network.

Baselines live at `<run_root>/baselines/<role>/<image name>`. After every comparison the
current image becomes the new baseline, so the next round compares to this one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageEnhance

from core.harness import artifacts
from core.harness.artifacts import SCREENS_DIR, safe_resolve
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.tool_context import RunToolContext

log = logging.getLogger(__name__)

TOOL_NAME = "visual_diff"
CHANNEL_TOLERANCE = 16        # a pixel "changed" if any channel differs by more than this
DIM_FACTOR = 0.3              # the diff image shows the current image at 30% brightness
INLINE_MAX_SIDE = 1280        # inline diff images are capped to this long side
_FORMATS = {"PNG", "JPEG", "WEBP"}

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "current": {"type": "string",
                    "description": "Path of the PNG/JPEG to check, inside your working folder."},
        "reference": {"type": "string",
                      "description": ("Reference image: a path in your working folder, or "
                                      "`inputs/...` for a provided mockup. Omit to compare "
                                      "with the previous round's baseline.")},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.02,
                      "description": "Largest changed-pixel fraction that still counts as a match."},
    },
    "required": ["current"],
}


class _VisualError(Exception):
    """Bad input; the message goes back to the agent."""


@dataclass
class _Outcome:
    text: str
    diff_png: bytes | None = None


def _open_image(path: Path) -> Image.Image:
    if path.stat().st_size > artifacts.MAX_ARTIFACT_BYTES:
        raise _VisualError(f"{path.name} is larger than the {artifacts.MAX_ARTIFACT_BYTES} byte limit")
    try:
        img = Image.open(path)
        if img.format not in _FORMATS:
            raise _VisualError(f"{path.name} is not a PNG, JPEG or WebP image")
        img.load()
    except _VisualError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for bad files
        raise _VisualError(f"{path.name} could not be read as an image: {exc}") from exc
    return img


def _rgb(img: Image.Image) -> Image.Image:
    """Flatten transparency onto white and return RGB."""
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def compare(current: Image.Image, reference: Image.Image) -> tuple[float, Image.Image, bool]:
    """Return (changed fraction, diff image, whether the reference was resized)."""
    cur = _rgb(current)
    ref = _rgb(reference)
    resized = ref.size != cur.size
    if resized:
        ref = ref.resize(cur.size, Image.Resampling.LANCZOS)
    r, g, b = ImageChops.difference(cur, ref).split()
    biggest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = biggest.point(lambda v: 255 if v > CHANNEL_TOLERANCE else 0)
    fraction = mask.histogram()[255] / (cur.width * cur.height)
    diff = ImageEnhance.Brightness(cur).enhance(DIM_FACTOR)
    diff.paste((255, 0, 0), mask=mask)
    return fraction, diff, resized


def _inline_png(diff: Image.Image) -> bytes:
    img = diff.copy()
    img.thumbnail((INLINE_MAX_SIDE, INLINE_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _resolve_reference(ctx: RunToolContext, role: str, ref: str) -> Path:
    ref = ref.strip()
    if ref == "inputs" or ref == "inputs/":
        raise _VisualError("`reference` must name a file under inputs/")
    if ref.startswith("inputs/"):
        resolved = safe_resolve(ctx.run_root / "inputs", ref[len("inputs/"):])
        if resolved is None:
            raise _VisualError("`reference` must stay inside inputs/ (no .. or symlinks)")
    else:
        resolved = ctx.resolve_agent_path(role, ref)
        if resolved is None:
            raise _VisualError("`reference` must be inside your working folder or inputs/")
    if not resolved.is_file():
        raise _VisualError(f"`reference` not found: {ref}")
    return resolved


def _diff_once(ctx: RunToolContext, role: str, args: dict[str, Any]) -> _Outcome:
    current_arg = args.get("current")
    if not isinstance(current_arg, str) or not current_arg.strip():
        raise _VisualError("`current` must be the path of an image in your working folder")
    threshold = args.get("threshold", 0.02)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) \
            or not 0 <= threshold <= 1:
        raise _VisualError("`threshold` must be a number between 0 and 1")
    reference_arg = args.get("reference")
    if reference_arg is not None and (not isinstance(reference_arg, str)
                                      or not reference_arg.strip()):
        raise _VisualError("`reference` must be a path string when given")

    clone = ctx.clone_for(role)
    current_path = ctx.resolve_agent_path(role, current_arg)
    if current_path is None:
        raise _VisualError("`current` must be a file inside your working folder (no symlinks)")
    if not current_path.is_file():
        raise _VisualError(f"`current` not found: {current_arg}")
    current = _open_image(current_path)

    name = current_path.name
    baseline = ctx.run_root / "baselines" / role / name

    def update_baseline() -> None:
        baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current_path, baseline)

    if reference_arg is None:
        if not baseline.is_file():
            update_baseline()
            return _Outcome(f"baseline recorded for {name}; the next call will compare with it")
        reference_path, ref_label = baseline, f"baseline of {name}"
    else:
        reference_path, ref_label = _resolve_reference(ctx, role, reference_arg), reference_arg
    reference = _open_image(reference_path)

    fraction, diff, resized = compare(current, reference)
    verdict = "match" if fraction <= threshold else "differs"

    screens = ctx.tool_dir(role) / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    diff_name = f"diff-{current_path.stem}.png"
    diff.save(screens / diff_name, format="PNG")
    served = ctx.run_root / "screenshots" / role
    served.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(screens / diff_name, served / diff_name)
    update_baseline()

    lines = [
        f"verdict: {verdict}",
        f"changed_fraction: {fraction:.4f} (threshold {threshold})",
        f"current: {name} {current.width}x{current.height}",
        f"reference: {ref_label} {reference.width}x{reference.height}",
    ]
    if resized:
        lines.append(f"note: reference resized from {reference.width}x{reference.height} to "
                     f"{current.width}x{current.height} before comparing")
    lines.append(f"diff image: {SCREENS_DIR}/{diff_name}")
    lines.append(f"baseline for {name} updated to the current image")
    return _Outcome("\n".join(lines), _inline_png(diff) if verdict == "differs" else None)


def tools(ctx: RunToolContext) -> list[McpTool]:
    """The `visual_diff` tool bound to this run."""

    async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        try:
            outcome = await asyncio.to_thread(_diff_once, ctx, identity.role, args)
        except _VisualError as exc:
            return ToolResult(f"visual_diff failed: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - a tool never raises into the MCP server
            log.exception("visual_diff crashed")
            return ToolResult(f"visual_diff failed: {type(exc).__name__}: {exc}", is_error=True)
        images = ([(base64.b64encode(outcome.diff_png).decode("ascii"), "image/png")]
                  if outcome.diff_png else [])
        return ToolResult(outcome.text, images=images)

    return [McpTool(
        name=TOOL_NAME,
        description=("Compare a screenshot with a reference image or with the previous round's "
                     "baseline using a deterministic pixel diff. Returns the changed fraction, a "
                     "verdict and a diff image. Free: no model call."),
        input_schema=_SCHEMA,
        handler=handler,
    )]
