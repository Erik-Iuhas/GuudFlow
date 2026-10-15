"""Tests for core.deps: manifests, hashed environments and the role environment (plan §16)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.deps import (DepsError, ProjectDeps, env_key, link_node_modules, merge_provenance,
                       read_pins, role_env)


def test_read_pins_accepts_exact_and_lists_every_problem(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"vite": "5.4.1", "@scope/x": "=1.0.0"}, "devDependencies": {"t": "v2.0.0"}}')
    assert read_pins(tmp_path, "npm") == {"vite": "5.4.1", "@scope/x": "1.0.0", "t": "2.0.0"}
    (tmp_path / "requirements.txt").write_text("# c\nrich==13.7.1\nhttpx[http2]==0.27.0\nflask>=2\n-e .\n")
    with pytest.raises(DepsError) as exc:
        read_pins(tmp_path, "pip")
    assert "line 4" in str(exc.value) and "line 5" in str(exc.value)


def test_manifest_symlink_is_refused(tmp_path: Path) -> None:
    (tmp_path / "package.json").symlink_to("/etc/hostname")
    with pytest.raises(DepsError):
        read_pins(tmp_path, "npm")


def test_env_key_ignores_order_and_name_case() -> None:
    assert env_key("pip", {"Rich": "1", "a_b": "2"}) == env_key("pip", {"a-b": "2", "rich": "1"})
    assert env_key("pip", {"rich": "1"}) != env_key("pip", {"rich": "2"})


async def test_build_reuses_and_activate_swaps_the_link(tmp_path: Path) -> None:
    deps = ProjectDeps(tmp_path / "deps")
    calls: list[list[str]] = []

    async def install(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        target = Path(argv[argv.index("--target") + 1])
        (target / "rich").mkdir(parents=True)
        return 0, "ok"

    pins = {"rich": "13.7.1"}
    assert (await deps.build("pip", pins, install))[0] == 0
    assert (await deps.build("pip", pins, install)) == (0, "reused") and len(calls) == 1
    assert "--only-binary=:all:" in calls[0]
    env = deps.activate("pip", merge_provenance("pip", pins, None, role="dev", approved_by="human",
                                                run_id="r1"))
    assert os.readlink(deps.root / "pip") == env.key
    assert (deps.root / "pip" / "site-packages" / "rich").is_dir()
    again = merge_provenance("pip", {"rich": "13.7.1", "attrs": "23.2.0"}, deps.current("pip"),
                             role="qa", approved_by="allow-list", run_id="r2")
    assert {p.name: p.added_by for p in again} == {"rich": "dev", "attrs": "qa"}


async def test_failed_install_leaves_nothing(tmp_path: Path) -> None:
    deps = ProjectDeps(tmp_path / "deps")

    async def install(argv: list[str]) -> tuple[int, str]:
        return 1, "boom"

    assert (await deps.build("npm", {"a": "1.0.0"}, install)) == (1, "boom")
    assert [p.name for p in deps.root.iterdir()] == []


def test_role_env_and_link(tmp_path: Path) -> None:
    env = role_env("/deps", "/usr/bin")
    assert env["PATH"] == "/deps/npm/node_modules/.bin:/deps/pip/site-packages/bin:/usr/bin"
    assert link_node_modules(tmp_path, "/deps") is None
    assert os.readlink(tmp_path / "node_modules") == "/deps/npm/node_modules"
    real = tmp_path / "other"
    (real / "node_modules").mkdir(parents=True)
    assert link_node_modules(real, "/deps") is not None
