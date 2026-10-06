"""Artifact registry: indexing, serving copies, metadata stripping, path safety (plan §5.10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from core.harness import artifacts
from core.harness.artifacts import ArtifactRegistry, safe_resolve
from core.signals import SignalType


class Posts:
    """Collects what the registry posts to the board."""

    def __init__(self) -> None:
        self.calls: list[tuple[SignalType, str, str, dict[str, Any]]] = []

    def __call__(self, kind: SignalType, role: str, summary: str,
                 detail: dict[str, Any] | str | None = None, priority: str = "normal") -> None:
        assert isinstance(detail, dict)
        self.calls.append((kind, role, summary, detail))


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    path = tmp_path / "clone"
    path.mkdir()
    return path


@pytest.fixture
def run_root(tmp_path: Path) -> Path:
    path = tmp_path / "run"
    path.mkdir()
    return path


@pytest.fixture
def posts() -> Posts:
    return Posts()


@pytest.fixture
def registry(run_root: Path, posts: Posts) -> ArtifactRegistry:
    return ArtifactRegistry(run_root, posts)


def make_png(path: Path, color: tuple[int, int, int] = (200, 30, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path)


def test_new_png_registered_copied_and_signalled(registry: ArtifactRegistry, clone: Path,
                                                 run_root: Path, posts: Posts) -> None:
    make_png(clone / "artifacts" / "logo.png")
    records = registry.scan("builder", clone, ["artifacts/logo.png", "src/app.py"], task_id="T1")

    assert len(records) == 1
    rec = records[0]
    assert (rec.path, rec.kind, rec.role, rec.task_id) == ("artifacts/logo.png", "image",
                                                           "builder", "T1")
    assert rec.size == (clone / "artifacts" / "logo.png").stat().st_size
    assert len(rec.sha256) == 64 and rec.created_at.endswith("+00:00")
    served = run_root / "artifacts" / "builder" / "artifacts" / "logo.png"
    assert served.is_file()
    with Image.open(served) as img:
        assert img.size == (16, 16)

    assert len(posts.calls) == 1
    kind, role, summary, detail = posts.calls[0]
    assert (kind, role, summary) == (SignalType.ARTIFACT, "builder",
                                     "builder: image artifacts/logo.png")
    assert detail["served_path"] == "artifacts/builder/artifacts/logo.png"
    assert detail["sha256"] == rec.sha256 and detail["task_id"] == "T1"
    assert registry.records("builder") == records
    assert registry.records("other") == []


def test_kinds(registry: ArtifactRegistry, clone: Path) -> None:
    (clone / "d.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (clone / "r.pdf").write_bytes(b"%PDF-1.4 fake")
    (clone / "p.html").write_text("<html></html>")
    (clone / "n.txt").write_text("nope")
    records = registry.scan("designer", clone, ["d.svg", "r.pdf", "p.html", "n.txt"])
    assert {r.path: r.kind for r in records} == {"d.svg": "svg", "r.pdf": "pdf", "p.html": "html"}
    # Copied as-is.
    assert (registry._dir / "designer" / "d.svg").read_text() == (clone / "d.svg").read_text()


def test_unchanged_file_skipped_changed_file_reregistered(registry: ArtifactRegistry, clone: Path,
                                                          posts: Posts) -> None:
    make_png(clone / "a.png", (10, 10, 10))
    assert len(registry.scan("builder", clone, ["a.png"])) == 1
    assert registry.scan("builder", clone, ["a.png"]) == []
    assert len(posts.calls) == 1

    make_png(clone / "a.png", (250, 250, 250))
    again = registry.scan("builder", clone, ["a.png"])
    assert len(again) == 1
    assert len(posts.calls) == 2
    assert len(registry.records()) == 2

    # Same path under another role is its own artifact.
    assert len(registry.scan("reviewer", clone, ["a.png"])) == 1


def test_already_indexed_survives_new_registry(run_root: Path, clone: Path, posts: Posts) -> None:
    make_png(clone / "a.png")
    ArtifactRegistry(run_root, posts).scan("builder", clone, ["a.png"])
    second = ArtifactRegistry(run_root, posts)
    assert len(second.records()) == 1
    assert second.scan("builder", clone, ["a.png"]) == []


def test_exif_removed_from_served_copy(registry: ArtifactRegistry, clone: Path,
                                       run_root: Path) -> None:
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x9286] = "private comment"
    source = clone / "photo.jpg"
    Image.new("RGB", (32, 32), (120, 60, 30)).save(source, exif=exif)
    with Image.open(source) as img:
        assert img.getexif().get(0x010F) == "SecretCameraMaker"   # the source has EXIF

    assert len(registry.scan("builder", clone, ["photo.jpg"])) == 1
    served = run_root / "artifacts" / "builder" / "photo.jpg"
    with Image.open(served) as img:
        assert len(img.getexif()) == 0
        assert img.size == (32, 32)
    assert b"SecretCameraMaker" not in served.read_bytes()


def test_png_text_chunks_removed(registry: ArtifactRegistry, clone: Path, run_root: Path) -> None:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "hidden-injection-text")
    Image.new("RGB", (8, 8)).save(clone / "t.png", pnginfo=info)
    assert b"hidden-injection-text" in (clone / "t.png").read_bytes()
    registry.scan("builder", clone, ["t.png"])
    assert b"hidden-injection-text" not in (run_root / "artifacts/builder/t.png").read_bytes()


def test_undecodable_raster_not_served(registry: ArtifactRegistry, clone: Path,
                                       run_root: Path, posts: Posts) -> None:
    (clone / "bad.png").write_bytes(b"not an image at all")
    assert registry.scan("builder", clone, ["bad.png"]) == []
    assert not (run_root / "artifacts" / "builder" / "bad.png").exists()
    assert posts.calls == []


def test_screens_dir_picked_up_without_changed_files(registry: ArtifactRegistry,
                                                     clone: Path) -> None:
    make_png(clone / ".guudflow" / "screens" / "home.png")
    make_png(clone / ".guudflow" / "screens" / "sub" / "x.png")
    records = registry.scan("builder", clone)
    assert {r.path for r in records} == {".guudflow/screens/home.png",
                                         ".guudflow/screens/sub/x.png"}
    assert (registry._dir / "builder" / ".guudflow/screens/home.png").is_file()


def test_path_escapes_refused(registry: ArtifactRegistry, clone: Path, tmp_path: Path,
                              run_root: Path, posts: Posts) -> None:
    outside = tmp_path / "outside.png"
    make_png(outside)
    (clone / "link.png").symlink_to(outside)
    (clone / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    screens = clone / ".guudflow" / "screens"
    screens.mkdir(parents=True)
    (screens / "sneaky.png").symlink_to(outside)

    records = registry.scan("builder", clone, [
        "link.png", "linkdir/outside.png", "../outside.png", "a/../../outside.png",
        str(outside)])
    assert records == []
    assert posts.calls == []
    assert not (run_root / "artifacts").exists() or not any(
        p.is_file() and p.name != "index.jsonl" for p in (run_root / "artifacts").rglob("*"))


def test_safe_resolve(clone: Path, tmp_path: Path) -> None:
    make_png(clone / "ok.png")
    assert safe_resolve(clone, "ok.png") == clone.resolve() / "ok.png"
    assert safe_resolve(clone, clone / "ok.png") == clone.resolve() / "ok.png"
    assert safe_resolve(clone, "../x.png") is None
    assert safe_resolve(clone, "/etc/passwd") is None
    assert safe_resolve(clone, "") is None


def test_oversized_file_refused(registry: ArtifactRegistry, clone: Path, posts: Posts,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    make_png(clone / "big.png")
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 10)
    assert registry.scan("builder", clone, ["big.png"]) == []
    assert posts.calls == []


def test_default_limit_is_20_mb() -> None:
    assert artifacts.MAX_ARTIFACT_BYTES == 20 * 1024 * 1024


def test_index_jsonl_appended(registry: ArtifactRegistry, clone: Path, run_root: Path) -> None:
    make_png(clone / "a.png", (1, 2, 3))
    registry.scan("builder", clone, ["a.png"], task_id="T9")
    make_png(clone / "b.png", (4, 5, 6))
    registry.scan("builder", clone, ["b.png"])
    lines = (run_root / "artifacts" / "index.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["path"] for r in rows] == ["a.png", "b.png"]
    assert rows[0]["task_id"] == "T9" and rows[1]["task_id"] is None
    assert rows[0]["served_path"] == "artifacts/builder/a.png"


def test_deleted_changed_file_ignored(registry: ArtifactRegistry, clone: Path) -> None:
    assert registry.scan("builder", clone, ["gone.png"]) == []


def test_invalid_role_rejected(registry: ArtifactRegistry, clone: Path) -> None:
    with pytest.raises(ValueError):
        registry.scan("../evil", clone)
