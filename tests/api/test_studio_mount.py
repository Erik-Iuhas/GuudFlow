"""Studio served by the API: files, and an SPA fallback for deep links (plan §11.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Studio</title>")
    (dist / "assets" / "app.js").write_text("console.log('studio')")
    monkeypatch.setenv("GUUDFLOW_STUDIO_DIST", str(dist))
    monkeypatch.setenv("GUUDFLOW_DATA_DIR", str(tmp_path / "data"))
    from api.main import create_app
    return TestClient(create_app())


def test_files_and_root(client: TestClient) -> None:
    assert "Studio" in client.get("/").text
    assert "studio" in client.get("/assets/app.js").text


def test_deep_link_gets_index(client: TestClient) -> None:
    r = client.get("/projects/pacrogue?tab=plan")
    assert r.status_code == 200 and "Studio" in r.text


def test_missing_files_and_api_paths_still_404(client: TestClient) -> None:
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404


def test_index_is_revalidated_so_a_new_build_shows_up(client: TestClient) -> None:
    """Seen live: after a rebuild the browser kept the old index.html (no Cache-Control, so
    heuristic caching) and with it the old bundle. Hashed assets may still be cached."""
    for path in ("/", "/runs/run_x"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/assets/app.js").headers
