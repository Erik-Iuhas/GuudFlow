"""Smoke test for the optional chart-rendering sandbox image (plan §5.10, task S32).

Builds `guudflow/agent-opencode-viz` from the wheels fetched by
`scripts/fetch_viz_wheels.sh`, runs it with the manager's exact hardened kwargs
(read-only root, cap_drop ALL, no-new-privileges, internal network, non-root user) and
renders a matplotlib PNG and a plotly PNG (kaleido) into the bind-mounted /work.

Skipped unless a Docker daemon is available, GUUDFLOW_SKIP_DOCKER_TESTS is unset and the
wheels directory exists. Unit tests for the variant selection run everywhere.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from core.harness.capabilities import ContainerProfile
from worker.sandbox_manager import (
    DEFAULT_IMAGE,
    VIZ_IMAGE,
    VIZ_WHEELS_ENV,
    RunNetwork,
    SandboxError,
    SandboxManager,
    SandboxSpec,
    image_for_variant,
    viz_wheels_dir,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PROFILE = ContainerProfile(
    use_container=True, read_only_root=True, allow_test_runners=False,
    host_may_reach_dev_server=False,
)

RENDER_SCRIPT = """
import matplotlib
assert matplotlib.get_backend().lower() == "agg", matplotlib.get_backend()
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([1, 2, 3], [3, 1, 2])
fig.savefig("/work/mpl.png")
import plotly.graph_objects as go
go.Figure(go.Bar(x=["a", "b"], y=[1, 2])).write_image("/work/plotly.png")
print("rendered")
"""


def _skip_reason() -> str | None:
    if os.environ.get("GUUDFLOW_SKIP_DOCKER_TESTS"):
        return "GUUDFLOW_SKIP_DOCKER_TESTS is set"
    if not SandboxManager.available():
        return "docker daemon not available"
    if not any(viz_wheels_dir().glob("*.whl")):
        return "viz wheels missing; run scripts/fetch_viz_wheels.sh"
    return None


_SKIP = _skip_reason()
needs_viz = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


# --------------------------------------------------------------------- unit


def test_variants_resolve_to_images() -> None:
    assert image_for_variant() == DEFAULT_IMAGE
    assert image_for_variant("viz") == VIZ_IMAGE
    assert VIZ_IMAGE != DEFAULT_IMAGE and VIZ_IMAGE.startswith("guudflow/agent-opencode-viz:")
    with pytest.raises(SandboxError, match="unknown sandbox image variant"):
        image_for_variant("gpu")


def test_wheels_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VIZ_WHEELS_ENV, str(tmp_path))
    assert viz_wheels_dir() == tmp_path


async def test_viz_build_without_wheels_explains_the_fix(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Images:
        def get(self, name: str) -> Any:
            from docker.errors import ImageNotFound
            raise ImageNotFound(name)

    class _Client:
        images = _Images()

    monkeypatch.setenv(VIZ_WHEELS_ENV, str(tmp_path))
    with pytest.raises(SandboxError, match="scripts/fetch_viz_wheels.sh"):
        await SandboxManager(client=_Client()).ensure_image(variant="viz")  # type: ignore[arg-type]


def test_default_manager_image_is_unchanged() -> None:
    assert SandboxManager().image == DEFAULT_IMAGE


# -------------------------------------------------------------- integration


@pytest.fixture
async def live() -> AsyncIterator[tuple[SandboxManager, str]]:
    mgr = SandboxManager()
    run_id = f"v{uuid.uuid4().hex[:8]}"
    yield mgr, run_id
    await mgr.stop_run(run_id)


@needs_viz
async def test_viz_image_renders_charts_hardened(
        live: tuple[SandboxManager, str], tmp_path: Path) -> None:
    mgr, run_id = live
    await mgr.ensure_image(variant="viz")
    net: RunNetwork = await mgr.create_run_network(run_id)
    workdir = tmp_path / "clone"
    workdir.mkdir()
    spec = SandboxSpec(
        run_id=run_id, role="analyst", workdir=workdir, inputs_dir=None, profile=PROFILE,
        config={}, password="pw", network=net, image=image_for_variant("viz"),
    )
    kwargs = mgr.container_kwargs(spec)
    mgr._hand_over_workdir(spec, kwargs["user"])  # what start() does for a root runner
    kwargs["command"] = ["sleep", "120"]
    kwargs["entrypoint"] = []  # replace `opencode serve`; every other kwarg is the real one
    container = mgr.client.containers.run(**kwargs)

    info = mgr.client.api.inspect_container(container.id)
    host = info["HostConfig"]
    assert info["Config"]["Image"] == VIZ_IMAGE
    assert host["ReadonlyRootfs"] is True and "ALL" in host["CapDrop"]
    assert host["NetworkMode"] == net.name and not host["Privileged"]
    assert not info["Config"]["User"].startswith("0")
    assert mgr.client.networks.get(net.name).attrs["Internal"] is True  # no egress

    code, out = container.exec_run(["python3", "-c", RENDER_SCRIPT])
    assert code == 0, out.decode("utf-8", "replace")
    for name in ("mpl.png", "plotly.png"):
        data = (workdir / name).read_bytes()
        assert data.startswith(PNG_MAGIC), name
        assert len(data) > 1000, name

    # Still hardened inside: root fs read-only, and no way out to the internet.
    assert container.exec_run(["sh", "-c", "echo x > /usr/local/f"])[0] != 0
    code, _ = container.exec_run(["python3", "-c", (
        "import socket,sys\n"
        "try: socket.create_connection(('1.1.1.1', 443), timeout=3)\n"
        "except OSError: sys.exit(3)\n"
    )])
    assert code == 3
