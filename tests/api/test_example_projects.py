"""Tests for /api/example-projects and the loader.

Covers:
  - loader: discovery, malformed-skip in list, strict raise on load_one
  - GET /api/example-projects: lists shipped catalog
  - POST .../instantiate: creates project, auto-suffixes ids, copies
    seed_inputs, honors explicit project_id + goal overrides, 404 on
    unknown template, 409 on explicit-id collision, auth required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.example_projects import (
    ExampleProjectError,
    find_example_project,
    list_example_projects,
    load_example_project,
    materialize_project_config,
)


# ── Loader fixtures ────────────────────────────────────────────────────────


def _write_template(root: Path, slug: str, payload: dict) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "template.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def _valid_payload(template_id: str = "example-x") -> dict:
    return {
        "id": template_id,
        "title": "X",
        "category": "Coding",
        "icon": "🌐",
        "summary": "x",
        "details": "x details",
        "default_project_id": "x",
        "default_goal": "do x",
        "requires": {"source_repo": False, "hints": ["a", "b"]},
        "project_template": {
            "max_iterations": 3,
            "agents": [
                {"role": "dev", "system_prompt": "do work"},
            ],
            "rbac": {"dev": {"allowed_scopes": ["fs-write"]}},
        },
    }


# ── Loader unit tests ──────────────────────────────────────────────────────


def test_list_returns_empty_when_root_missing(tmp_path: Path):
    assert list_example_projects(tmp_path / "missing") == []


def test_list_skips_underscore_dirs(tmp_path: Path):
    _write_template(tmp_path, "good", _valid_payload("example-good"))
    _write_template(tmp_path, "_skipme", _valid_payload("example-skip"))
    found = list_example_projects(tmp_path)
    assert [t.id for t in found] == ["example-good"]


def test_list_skips_malformed(tmp_path: Path):
    _write_template(tmp_path, "good", _valid_payload("example-good"))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "template.json").write_text("{this is not json", encoding="utf-8")
    found = list_example_projects(tmp_path)
    assert [t.id for t in found] == ["example-good"]


def test_list_sorted_by_id(tmp_path: Path):
    _write_template(tmp_path, "z", _valid_payload("example-zeta"))
    _write_template(tmp_path, "a", _valid_payload("example-alpha"))
    _write_template(tmp_path, "m", _valid_payload("example-mid"))
    found = list_example_projects(tmp_path)
    # sorted by directory name (matches scenarios pattern)
    assert [t.id for t in found] == [
        "example-alpha",
        "example-mid",
        "example-zeta",
    ]


def test_load_strict_raises_on_invalid_category(tmp_path: Path):
    p = _valid_payload()
    p["category"] = "NotAValidCategory"
    d = _write_template(tmp_path, "x", p)
    with pytest.raises(ExampleProjectError, match="category"):
        load_example_project(d)


def test_load_strict_raises_on_missing_required(tmp_path: Path):
    p = _valid_payload()
    del p["title"]
    d = _write_template(tmp_path, "x", p)
    with pytest.raises(ExampleProjectError, match="title"):
        load_example_project(d)


def test_load_strict_raises_on_empty_agents(tmp_path: Path):
    p = _valid_payload()
    p["project_template"]["agents"] = []
    d = _write_template(tmp_path, "x", p)
    with pytest.raises(ExampleProjectError, match="non-empty"):
        load_example_project(d)


def test_load_strict_raises_on_agent_missing_role(tmp_path: Path):
    p = _valid_payload()
    p["project_template"]["agents"] = [{"system_prompt": "..."}]  # no role
    d = _write_template(tmp_path, "x", p)
    with pytest.raises(ExampleProjectError, match="role"):
        load_example_project(d)


def test_load_picks_up_seed_inputs_dir(tmp_path: Path):
    d = _write_template(tmp_path, "x", _valid_payload())
    seed = d / "seed_inputs"
    seed.mkdir()
    (seed / "sample.txt").write_text("hello", encoding="utf-8")
    t = load_example_project(d)
    assert t.seed_inputs_dir == seed
    assert t.to_summary()["has_seed_inputs"] is True


def test_load_no_seed_inputs_dir(tmp_path: Path):
    d = _write_template(tmp_path, "x", _valid_payload())
    t = load_example_project(d)
    assert t.seed_inputs_dir is None
    assert t.to_summary()["has_seed_inputs"] is False


def test_find_example_project_returns_none_for_unknown(tmp_path: Path):
    _write_template(tmp_path, "x", _valid_payload("example-x"))
    assert find_example_project(tmp_path, "example-missing") is None


def test_find_example_project_returns_match(tmp_path: Path):
    _write_template(tmp_path, "x", _valid_payload("example-x"))
    found = find_example_project(tmp_path, "example-x")
    assert found is not None
    assert found.id == "example-x"


def test_materialize_overrides_id_and_goal(tmp_path: Path):
    d = _write_template(tmp_path, "x", _valid_payload())
    t = load_example_project(d)
    cfg = materialize_project_config(t, "my-real-project", "my real goal")
    assert cfg["project_id"] == "my-real-project"
    assert cfg["goal"] == "my real goal"
    # template's project_template fields preserved
    assert cfg["max_iterations"] == 3
    assert cfg["agents"][0]["role"] == "dev"


def test_materialize_does_not_mutate_template(tmp_path: Path):
    d = _write_template(tmp_path, "x", _valid_payload())
    t = load_example_project(d)
    materialize_project_config(t, "a", "g1")
    materialize_project_config(t, "b", "g2")
    # Template's stored config still missing project_id/goal — proves
    # materialize returned a fresh dict each time.
    assert "project_id" not in t.project_template
    assert "goal" not in t.project_template


# ── HTTP fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def example_root(tmp_path: Path) -> Path:
    """Test-only templates root with two valid templates and a copy of
    one of the shipped templates so we exercise both."""
    root = tmp_path / "templates"
    root.mkdir()
    _write_template(root, "alpha", _valid_payload("example-alpha"))
    p = _valid_payload("example-with-seeds")
    d = _write_template(root, "with-seeds", p)
    seed = d / "seed_inputs"
    seed.mkdir()
    (seed / "sample.txt").write_text("hello world", encoding="utf-8")
    sub = seed / "nested"
    sub.mkdir()
    (sub / "deep.md").write_text("# nested", encoding="utf-8")
    return root


@pytest.fixture
def app_with_examples(app, example_root):
    app.state.example_projects_root = example_root
    return app


@pytest.fixture
async def example_client(app_with_examples):
    import httpx
    transport = httpx.ASGITransport(app=app_with_examples)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── List route ─────────────────────────────────────────────────────────────


async def test_list_returns_summaries(example_client):
    r = await example_client.get("/api/example-projects")
    assert r.status_code == 200
    body = r.json()
    assert {t["id"] for t in body} == {"example-alpha", "example-with-seeds"}
    alpha = next(t for t in body if t["id"] == "example-alpha")
    assert alpha["title"] == "X"
    assert alpha["category"] == "Coding"
    assert alpha["has_seed_inputs"] is False
    seeded = next(t for t in body if t["id"] == "example-with-seeds")
    assert seeded["has_seed_inputs"] is True


async def test_list_no_auth_required(example_client):
    # Mirrors /api/training/scenarios — listing is public.
    r = await example_client.get("/api/example-projects")
    assert r.status_code == 200


async def test_list_when_root_missing(app, monkeypatch):
    """When the templates directory doesn't exist, the list is empty
    rather than 500."""
    app.state.example_projects_root = Path("/nonexistent/path/that/does/not/exist")
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/example-projects")
    assert r.status_code == 200
    assert r.json() == []


# ── Instantiate route ──────────────────────────────────────────────────────


async def test_instantiate_creates_project(example_client, auth_header, data_root):
    r = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["from_template"] == "example-alpha"
    project = body["project"]
    assert project["project_id"] == "x"  # default_project_id
    assert project["goal"] == "do x"  # default_goal
    assert project["agents"][0]["role"] == "dev"

    # Project actually persisted on disk.
    projects_json = data_root / "projects.json"
    assert projects_json.is_file()
    data = json.loads(projects_json.read_text())
    assert "x" in data


async def test_instantiate_auto_suffixes_on_collision(
    example_client, auth_header, data_root,
):
    # First create takes the default id.
    r1 = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={},
        headers=auth_header,
    )
    assert r1.status_code == 201
    assert r1.json()["project"]["project_id"] == "x"

    # Second create gets `-2`.
    r2 = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={},
        headers=auth_header,
    )
    assert r2.status_code == 201
    assert r2.json()["project"]["project_id"] == "x-2"

    # Third gets `-3`.
    r3 = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={},
        headers=auth_header,
    )
    assert r3.status_code == 201
    assert r3.json()["project"]["project_id"] == "x-3"


async def test_instantiate_explicit_id_collides_409(
    example_client, auth_header,
):
    r1 = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={"project_id": "my-app"},
        headers=auth_header,
    )
    assert r1.status_code == 201
    r2 = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={"project_id": "my-app"},
        headers=auth_header,
    )
    assert r2.status_code == 409


async def test_instantiate_explicit_goal_override(
    example_client, auth_header,
):
    r = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={"project_id": "p1", "goal": "build an actual thing"},
        headers=auth_header,
    )
    assert r.status_code == 201
    project = r.json()["project"]
    assert project["goal"] == "build an actual thing"


async def test_instantiate_copies_seed_inputs(
    example_client, auth_header, data_root,
):
    r = await example_client.post(
        "/api/example-projects/example-with-seeds/instantiate",
        json={"project_id": "seedy"},
        headers=auth_header,
    )
    assert r.status_code == 201
    uploads = data_root / "projects" / "seedy" / "inputs" / "uploads"
    assert (uploads / "sample.txt").read_text() == "hello world"
    assert (uploads / "nested" / "deep.md").read_text() == "# nested"


async def test_instantiate_unknown_template_404(example_client, auth_header):
    r = await example_client.post(
        "/api/example-projects/example-missing/instantiate",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_instantiate_requires_auth(example_client):
    # Mirrors POST /api/projects (also auth-gated).
    r = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={},
    )
    assert r.status_code in (401, 403)


async def test_instantiate_rejects_blank_explicit_id(
    example_client, auth_header,
):
    r = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={"project_id": "   "},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_instantiate_rejects_non_string_goal(
    example_client, auth_header,
):
    r = await example_client.post(
        "/api/example-projects/example-alpha/instantiate",
        json={"goal": 42},
        headers=auth_header,
    )
    assert r.status_code == 400


# ── Shipped catalog smoke test ─────────────────────────────────────────────


def test_shipped_catalog_loads_cleanly():
    """Every template under data/example_projects/ must load through the
    strict loader. Catches typos or schema drift in the seed templates
    before they hit production."""
    from pathlib import Path as _P
    repo_root = _P(__file__).resolve().parent.parent.parent
    root = repo_root / "data" / "example_projects"
    templates = list_example_projects(root)
    # All six headline templates must be discoverable.
    expected = {
        "example-fullstack-web-app",
        "example-bug-hunt-and-fix",
        "example-cli-tool-from-spec",
        "example-algorithm-with-benchmarks",
        "example-api-client-library",
        "example-codebase-wiki",
    }
    assert expected.issubset({t.id for t in templates}), (
        f"missing templates: {expected - {t.id for t in templates}}"
    )
    for t in templates:
        # Strict load must succeed — listing tolerates malformed but
        # we want zero malformed in the shipped set.
        load_example_project(t.template_dir)
