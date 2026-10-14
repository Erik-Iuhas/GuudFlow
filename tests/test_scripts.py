"""Script hygiene that breaks setups when it slips."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_powershell_scripts_are_ascii() -> None:
    """Windows PowerShell 5.1 reads BOM-less UTF-8 as Windows-1252: a "—" in a string becomes
    `â€"`, whose last byte is a quote that ends the string (a parse error on native Windows)."""
    for path in (ROOT / "scripts").glob("*.ps1"):
        text = path.read_bytes()
        bad = sorted({b for b in text if b > 127})
        assert not bad, f"{path.name} has non-ASCII bytes {bad}; use plain ASCII"


def test_shell_scripts_have_no_crlf() -> None:
    for path in (ROOT / "scripts").glob("*.sh"):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"
