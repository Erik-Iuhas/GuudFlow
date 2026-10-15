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


def _dev_reload_args() -> list[str]:
    import re
    import shlex
    text = (ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8")
    cmd = re.search(r"python -m uvicorn api\.main:app --reload \\\n(.*?)> ", text, re.S)
    assert cmd, "dev.sh has no reload uvicorn command"
    return shlex.split(cmd.group(1).replace("\\\n", " "))


def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def test_dev_reload_watches_only_source_dirs(monkeypatch) -> None:
    """F11: the reload task must not restart (and strand a live run's gateway) when tests/,
    data/ or docs change. uvicorn < 0.34.3 dropped --reload-dir under the cwd and watched the
    whole project; this checks the scope the installed uvicorn really watches."""
    from uvicorn.config import Config
    from uvicorn.supervisors.watchfilesreload import WatchFilesReload

    args = _dev_reload_args()
    monkeypatch.chdir(ROOT)
    config = Config("api.main:app", reload=True, reload_dirs=_flag_values(args, "--reload-dir"),
                    reload_excludes=_flag_values(args, "--reload-exclude"))
    watched = WatchFilesReload(config, target=lambda sockets: None, sockets=[]).reload_dirs
    assert ROOT not in watched
    assert {p.relative_to(ROOT).as_posix() for p in watched} == {
        "api", "core", "agents", "worker", "gateway"}
    for outside in ("tests/gateway/test_gateway.py", "data/runs/r/workspace/working/x.py"):
        assert not any(d in (ROOT / outside).parents for d in watched), outside


def test_dev_reload_cannot_hang_on_open_websockets() -> None:
    args = _dev_reload_args()
    assert _flag_values(args, "--timeout-graceful-shutdown"), \
        "without a graceful-shutdown timeout an open Studio websocket hangs the reload"
