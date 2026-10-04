"""Offline wheel installer used inside the viz image build (the base image has no pip).

Usage: python3 viz_install.py <wheels-dir> <target-dir>

Unpacks every `*.whl` into <target-dir> (a wheel is a zip laid out for site-packages).
Wheels with a `.data` payload (scripts, headers) are not used by the pinned stack and
are skipped. Standard library only.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def install(wheels_dir: Path, target: Path) -> int:
    """Unpack all wheels from `wheels_dir` into `target`; return how many were installed."""
    wheels = sorted(wheels_dir.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheels in {wheels_dir}")
    target.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as zf:
            for info in zf.infolist():
                if ".data/" in info.filename:
                    continue
                zf.extract(info, target)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    # zipfile drops the unix mode; kaleido's bundled chromium needs +x.
                    (target / info.filename).chmod(mode | 0o444)
    return len(wheels)


if __name__ == "__main__":
    count = install(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"installed {count} wheels into {sys.argv[2]}")
