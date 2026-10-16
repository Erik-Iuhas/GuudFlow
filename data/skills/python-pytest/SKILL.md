---
name: python-pytest
description: Conventions for Python code tested with pytest. Use for Python projects.
---

# Python with pytest

- Write `.py` files only. Follow the project's layout (a package folder or `src/`).
- Tests live in `tests/`, named `test_<behaviour>.py`, with plain `assert`.
- Run `python -m pytest -q` (or the check command in your rules); run one file while working.
- Type hints on public functions; no new dependency without a pin in `requirements.txt` and
  `sync_dependencies`.
