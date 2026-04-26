"""Tests for core.secret_scan — DESIGN §33 secret detection."""

from __future__ import annotations

from pathlib import Path

from core.secret_scan import SecretFinding, scan_directory, strip_secrets


def test_clean_dir_returns_empty(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("nothing sensitive\n", encoding="utf-8")
    assert scan_directory(tmp_path) == []


def test_finds_dotenv(tmp_path: Path):
    (tmp_path / ".env").write_text("DB_PASS=secret\n", encoding="utf-8")
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == [".env"]
    assert "env" in findings[0].reason.lower()


def test_finds_dotenv_variants(tmp_path: Path):
    (tmp_path / ".env.local").write_text("X=1\n", encoding="utf-8")
    (tmp_path / ".env.production").write_text("Y=2\n", encoding="utf-8")
    paths = sorted(f.path for f in scan_directory(tmp_path))
    assert paths == [".env.local", ".env.production"]


def test_skips_dotenv_examples(tmp_path: Path):
    (tmp_path / ".env.example").write_text("X=replace_me\n", encoding="utf-8")
    (tmp_path / ".env.sample").write_text("X=replace_me\n", encoding="utf-8")
    (tmp_path / ".env.template").write_text("X=replace_me\n", encoding="utf-8")
    assert scan_directory(tmp_path) == []


def test_finds_pem_extension(tmp_path: Path):
    (tmp_path / "server.pem").write_text("-----BEGIN CERT-----\n", encoding="utf-8")
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == ["server.pem"]


def test_finds_key_extensions(tmp_path: Path):
    for name in ("a.pem", "b.key", "c.p12", "d.pfx", "e.keystore", "f.jks"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    paths = sorted(f.path for f in scan_directory(tmp_path))
    assert paths == ["a.pem", "b.key", "c.p12", "d.pfx", "e.keystore", "f.jks"]


def test_finds_ssh_key_names(tmp_path: Path):
    for name in ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"):
        (tmp_path / name).write_text("not real key", encoding="utf-8")
    paths = sorted(f.path for f in scan_directory(tmp_path))
    assert paths == ["id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"]


def test_finds_aws_key_in_content(tmp_path: Path):
    (tmp_path / "config.txt").write_text(
        "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n",
        encoding="utf-8",
    )
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == ["config.txt"]
    assert "aws" in findings[0].reason.lower()


def test_finds_private_key_block_in_content(tmp_path: Path):
    (tmp_path / "creds.txt").write_text(
        "-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == ["creds.txt"]


def test_finds_rsa_private_key_block(tmp_path: Path):
    (tmp_path / "id.txt").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nfoo\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == ["id.txt"]


def test_skips_dot_git_dir(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    (git_dir / "objects").mkdir()
    (git_dir / "objects" / "ab.pem").write_text("x", encoding="utf-8")
    assert scan_directory(tmp_path) == []


def test_walks_subdirectories(tmp_path: Path):
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    (nested / ".env").write_text("X=1\n", encoding="utf-8")
    findings = scan_directory(tmp_path)
    assert [f.path for f in findings] == ["src/deep/.env"]


def test_ignores_large_text_files(tmp_path: Path):
    """Files >1 MiB are not content-scanned (cost guardrail).

    They'd still be flagged if they had a suspicious filename — this only
    covers content patterns inside otherwise-innocent files."""
    big = tmp_path / "big.log"
    big.write_text("ok\n" * 600_000, encoding="utf-8")  # > 1 MiB
    assert big.stat().st_size > 1_000_000
    assert scan_directory(tmp_path) == []


def test_strip_secrets_removes_files(tmp_path: Path):
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    (tmp_path / "key.pem").write_text("k", encoding="utf-8")
    (tmp_path / "safe.py").write_text("print(1)", encoding="utf-8")

    findings = scan_directory(tmp_path)
    strip_secrets(tmp_path, findings)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["safe.py"]


def test_strip_secrets_idempotent(tmp_path: Path):
    findings = [SecretFinding(path="missing.env", reason=".env file")]
    strip_secrets(tmp_path, findings)  # no error, no-op
