"""Jack, the project manager persona (project-flow plan §10.1).

One persona per install, `jack-project-manager-v1`, working across every project. It is
seeded from `data/_seed/personas/` like the other personas and lives in
`{data_root}/personas/`, so Studio's roster and the memory rules (15, 16, 22) apply as usual.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agents.persona import Persona

PM_PERSONA_ID = "jack-project-manager-v1"
DEFAULT_PM_NAME = "Jack"
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "_seed" / "personas" / PM_PERSONA_ID


def personas_dir(data_root: Path | str) -> Path:
    """Where this install's personas live."""
    return Path(data_root) / "personas"


def pm_dir(data_root: Path | str) -> Path:
    """Jack's own folder."""
    return personas_dir(data_root) / PM_PERSONA_ID


def ensure_pm(data_root: Path | str) -> Path:
    """Create Jack from the seed if his folder is missing (never overwrites)."""
    target = pm_dir(data_root)
    if not (target / "profile.json").is_file():
        if not SEED_DIR.is_dir():
            raise FileNotFoundError(f"seed persona missing: {SEED_DIR}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SEED_DIR, target, dirs_exist_ok=True)
    return target


def load_pm(data_root: Path | str) -> Persona:
    """Load Jack, creating him from the seed first when needed."""
    ensure_pm(data_root)
    persona = Persona.load(PM_PERSONA_ID, personas_dir(data_root))
    persona._dir = pm_dir(data_root)  # type: ignore[attr-defined]  # where MemoryWriter._save writes
    return persona


def pm_name(data_root: Path | str) -> str:
    """The display name: Jack's profile name (default "Jack")."""
    try:
        return load_pm(data_root).profile.name or DEFAULT_PM_NAME
    except Exception:  # noqa: BLE001 — a name must never break a caller
        return DEFAULT_PM_NAME
