"""Seed local `data/` from checked-in defaults (Phase G.2 onboarding).

Idempotent: existing personas / projects are NEVER overwritten. Only
copies seeds that don't already exist locally. Safe to re-run.

Run via:
    python scripts/seed_data.py [--data-root path]

Called automatically by `scripts/setup.sh` and `scripts/setup.ps1`
on first install (when `.env` doesn't exist).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
        help="Where to seed (default: ./data relative to repo root)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    seed_root = repo_root / "data" / "_seed"
    if not seed_root.is_dir():
        print(f"no seed dir at {seed_root} — nothing to do")
        return 0

    seeded_personas = _seed_personas(seed_root, args.data_root)
    print(f"Done: seeded {seeded_personas} persona(s)")

    return 0


def _seed_personas(seed_root: Path, data_root: Path) -> int:
    src = seed_root / "personas"
    if not src.is_dir():
        return 0
    dest = data_root / "personas"
    dest.mkdir(parents=True, exist_ok=True)

    seeded = 0
    for persona_dir in sorted(src.iterdir()):
        if not persona_dir.is_dir():
            continue
        target = dest / persona_dir.name
        if target.exists():
            print(f"  skip {persona_dir.name} (already exists)")
            continue
        shutil.copytree(persona_dir, target)
        print(f"  + {persona_dir.name}")
        seeded += 1
    return seeded


if __name__ == "__main__":
    sys.exit(main())
