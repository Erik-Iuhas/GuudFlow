"""Generate studio/src/lib/runEvents.gen.ts from the backend's signal contract.

Usage: python scripts/gen_studio_events.py [--check]
--check exits 1 when the generated file is out of date (CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.harness.event_contract import render_typescript  # noqa: E402
from core.signals import SignalType  # noqa: E402

TARGET = ROOT / "studio" / "src" / "lib" / "runEvents.gen.ts"


def main(argv: list[str]) -> int:
    """Write (or with --check, verify) the generated TypeScript module."""
    text = render_typescript([t.value for t in SignalType])
    if "--check" in argv:
        current = TARGET.read_text() if TARGET.is_file() else ""
        if current != text:
            print(f"{TARGET} is out of date: run python scripts/gen_studio_events.py")
            return 1
        return 0
    TARGET.write_text(text)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
