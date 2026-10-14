"""Tests for /api/tls (Settings → Certificate): the Studio CA path as a backup."""

from __future__ import annotations

from pathlib import Path

import certifi
import pytest

from core import tls


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.delenv("GUUDFLOW_CA_BUNDLE", raising=False)
    tls._reset()
    tls.set_data_root(tmp_path / "appdata")


@pytest.mark.asyncio
async def test_requires_bearer(client):
    assert (await client.get("/api/tls")).status_code == 401


@pytest.mark.asyncio
async def test_save_check_and_clear(client, auth_header, tmp_path):
    r = await client.get("/api/tls", headers=auth_header)
    assert r.status_code == 200 and r.json()["effective"] is None

    r = await client.put("/api/tls", json={"ca_bundle": str(tmp_path / "missing.pem")}, headers=auth_header)
    assert r.status_code == 400 and "not a file" in r.json()["detail"]

    first = Path(certifi.where()).read_text().split("-----END CERTIFICATE-----")[0]
    ca = tmp_path / "corp.pem"
    ca.write_text(first[first.index("-----BEGIN"):] + "-----END CERTIFICATE-----\n")
    r = await client.put("/api/tls", json={"ca_bundle": str(ca)}, headers=auth_header)
    body = r.json()
    assert r.status_code == 200 and body["source"] == "app" and body["effective"] == str(ca)
    assert body["certs"] and "issued by" in body["certs"][0]

    r = await client.put("/api/tls", json={"ca_bundle": ""}, headers=auth_header)
    assert r.json()["effective"] is None
