"""Basic secret scanner — refuses or strips obviously-sensitive files
before a cloned/copied source repo lands in `data/projects/{id}/inputs/`.

DESIGN §33 ("Privacy: existing repos may contain secrets"). This is
defence-in-depth, not a guarantee — agents in production also have
`network_mode: none` (§29) so even a leaked secret cannot exfiltrate.

Patterns covered:
  - filenames: `.env`, `.env.*` (except `.env.example`/`.env.sample`)
  - extensions: `.pem`, `.key`, `.p12`, `.pfx`, `.keystore`, `.jks`
  - SSH private keys: `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`
  - content (small text files): `BEGIN ... PRIVATE KEY` blocks; AWS access
    key regex `AKIA[0-9A-Z]{16}`

`.git/` is skipped entirely — git's object database can occasionally
match content regexes spuriously, and we don't strip git history.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


_KEY_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
_SSH_KEY_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_DOTENV_EXEMPT_SUFFIXES = (".example", ".sample", ".template")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_MAX_CONTENT_SCAN_BYTES = 1_000_000


@dataclass
class SecretFinding:
    path: str    # POSIX-style path relative to scan root
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def scan_directory(root: Path) -> list[SecretFinding]:
    """Walk `root` and return findings for any file matching a secret pattern."""
    findings: list[SecretFinding] = []
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        try:
            rel = entry.relative_to(root)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        if rel_posix.startswith(".git/") or rel_posix == ".git":
            continue
        reason = _classify(entry, rel_posix)
        if reason is not None:
            findings.append(SecretFinding(path=rel_posix, reason=reason))
    return findings


def strip_secrets(root: Path, findings: list[SecretFinding]) -> None:
    """Remove the matching files from disk in-place. Idempotent."""
    for f in findings:
        target = root / f.path
        if target.is_file():
            target.unlink()


def _classify(entry: Path, rel_posix: str) -> str | None:
    name = entry.name
    suffix = entry.suffix.lower()

    if name == ".env":
        return ".env file"
    if name.startswith(".env.") and not name.endswith(_DOTENV_EXEMPT_SUFFIXES):
        return ".env variant"

    if suffix in _KEY_EXTENSIONS:
        return f"{suffix} file (likely private key/cert)"

    if name in _SSH_KEY_NAMES:
        return "SSH private key"

    try:
        size = entry.stat().st_size
    except OSError:
        return None
    if size == 0 or size > _MAX_CONTENT_SCAN_BYTES:
        return None

    try:
        content = entry.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    if _PRIVATE_KEY_RE.search(content):
        return "embedded private key block"
    if _AWS_KEY_RE.search(content):
        return "embedded AWS access key"
    return None
