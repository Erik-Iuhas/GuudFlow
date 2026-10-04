"""Artifact registry: index and serve the visual files agents produce (plan §5.10).

After each turn the runner calls `ArtifactRegistry.scan(role, clone, changed_files)`.
New or changed images, SVG, PDF and HTML files are copied to
`<run_root>/artifacts/<role>/<path>` (the folder Studio serves), an `ARTIFACT`
signal is posted per file, and every record is appended to
`<run_root>/artifacts/index.jsonl`.

Safety rules (plan §5.10):
  - Paths are resolved inside the role's clone only; `..`, absolute paths outside the
    clone and symlinks are refused.
  - Files larger than `MAX_ARTIFACT_BYTES` are refused.
  - Raster images (PNG/JPEG/WebP) are re-encoded without EXIF or text chunks before
    being served; an undecodable raster is not served at all.
  - SVG, PDF and HTML are copied byte for byte. They are never executed or parsed here;
    Studio sanitizes SVG and sandboxes HTML when it renders them.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from PIL import Image, ImageOps

from core.signals import SignalType

log = logging.getLogger(__name__)

VISUAL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf", ".html", ".htm"}
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
SCREENS_DIR = ".guudflow/screens"

ArtifactKind = Literal["image", "svg", "pdf", "html"]
_KINDS: dict[str, ArtifactKind] = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
    ".svg": "svg", ".pdf": "pdf", ".html": "html", ".htm": "html",
}
# Raster formats re-encoded to drop metadata. GIF is copied as-is (re-encoding would
# lose animation).
_REENCODE_FORMATS = {"PNG", "JPEG", "WEBP"}
_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ArtifactRecord:
    """One registered visual file. `size` and `sha256` describe the source file."""

    path: str            # relative to the clone (posix)
    kind: ArtifactKind
    role: str
    task_id: str | None
    size: int
    sha256: str
    created_at: str      # ISO UTC


def safe_resolve(root: Path, path: str | Path) -> Path | None:
    """Resolve `path` (relative to `root`, or absolute inside it) to a path under `root`.

    Returns None when the path escapes `root` (`..`, absolute elsewhere) or when it,
    or any directory on the way, is a symlink. The file need not exist.
    """
    root_real = root.resolve()
    candidate = Path(path)
    if candidate.is_absolute():
        for base in (root, root_real):
            try:
                candidate = candidate.relative_to(base)
                break
            except ValueError:
                continue
        else:
            return None
    if ".." in candidate.parts or not candidate.parts:
        return None
    current = root_real
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            return None
    try:
        current.relative_to(root_real)
    except ValueError:
        return None
    return current


def _sanitized_bytes(source: Path) -> bytes | None:
    """Re-encode a raster image without metadata; None if it cannot be decoded."""
    try:
        with Image.open(source) as img:
            fmt = img.format
            if fmt not in _REENCODE_FORMATS:
                return None
            img.load()
            clean = ImageOps.exif_transpose(img)  # honour orientation before dropping EXIF
            if fmt == "JPEG" and clean.mode not in ("RGB", "L", "CMYK"):
                clean = clean.convert("RGB")
            buf = io.BytesIO()
            clean.save(buf, format=fmt)  # no exif= / pnginfo= / icc_profile= → none written
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for bad files
        log.warning("artifact image could not be sanitized (%s): %s", source.name, exc)
        return None


class ArtifactRegistry:
    """Indexes and serves the visual files produced during a run."""

    def __init__(self, run_root: Path, post: Callable[..., None]) -> None:
        self._run_root = run_root
        self._post = post
        self._dir = run_root / "artifacts"
        self._index = self._dir / "index.jsonl"
        self._records: list[ArtifactRecord] = []
        self._seen: dict[tuple[str, str], str] = {}   # (role, path) → latest sha256
        self._load()

    def _load(self) -> None:
        if not self._index.is_file():
            return
        for line in self._index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                data.pop("served_path", None)
                rec = ArtifactRecord(**data)
            except (ValueError, TypeError):
                log.warning("skipping malformed artifact index line")
                continue
            self._records.append(rec)
            self._seen[(rec.role, rec.path)] = rec.sha256

    def records(self, role: str | None = None) -> list[ArtifactRecord]:
        """All registered records (oldest first), optionally for one role."""
        return [r for r in self._records if role is None or r.role == role]

    def scan(self, role: str, clone: Path, changed_files: Iterable[str] = (),
             task_id: str | None = None, tool_dir: Path | None = None) -> list[ArtifactRecord]:
        """Register new or changed visual files of `role`; return the new records.

        `tool_dir` is the role's host-only tool directory (seen by the agent as
        /work/.guudflow); screenshots there are registered as `.guudflow/screens/...`.
        """
        if not _ROLE_RE.match(role):
            raise ValueError(f"invalid role name: {role!r}")
        candidates: dict[str, None] = {}
        for name in changed_files:
            if PurePosixPath(name).suffix.lower() in VISUAL_EXTS:
                candidates[PurePosixPath(name).as_posix()] = None
        tools_root = tool_dir if tool_dir is not None else clone / ".guudflow"
        screens = tools_root / "screens"
        if screens.is_dir() and not screens.is_symlink():
            for found in sorted(screens.rglob("*")):
                if found.suffix.lower() in VISUAL_EXTS and not found.is_symlink():
                    candidates[".guudflow/" + found.relative_to(tools_root).as_posix()] = None

        new: list[ArtifactRecord] = []
        for rel in candidates:
            if rel.startswith(".guudflow/"):
                record = self._register(role, tools_root, rel[len(".guudflow/"):], task_id,
                                        display=rel)
            else:
                record = self._register(role, clone, rel, task_id)
            if record is not None:
                new.append(record)
        return new

    def _register(self, role: str, clone: Path, rel: str, task_id: str | None, display: str | None = None
                  ) -> ArtifactRecord | None:
        source = safe_resolve(clone, rel)
        rel = display or rel
        if source is None:
            log.warning("artifact path refused (escapes clone or symlink): %s", rel)
            return None
        if not source.is_file():
            return None   # deleted since the commit
        size = source.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            log.warning("artifact refused (%d bytes over the limit): %s", size, rel)
            return None
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if self._seen.get((role, rel)) == sha:
            return None
        ext = source.suffix.lower()
        served = data
        if ext in _RASTER_EXTS:
            clean = _sanitized_bytes(source)
            if clean is None:
                return None
            served = clean
        elif ext == ".gif" and not data.startswith((b"GIF87a", b"GIF89a")):
            return None
        dest = self._dir / role / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(served)

        record = ArtifactRecord(
            path=rel, kind=_KINDS[ext], role=role, task_id=task_id, size=size, sha256=sha,
            created_at=datetime.now(timezone.utc).isoformat())
        detail: dict[str, Any] = asdict(record)
        detail["served_path"] = dest.relative_to(self._run_root).as_posix()
        with self._index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(detail) + "\n")
        self._records.append(record)
        self._seen[(role, rel)] = sha
        self._post(SignalType.ARTIFACT, role, f"{role}: {record.kind} {rel}", detail)
        return record
