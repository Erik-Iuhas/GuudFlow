---
name: python-pygame
description: Conventions for a Python game built with pygame. Use for any code or test in a pygame project.
---

# Python with pygame

- The project is Python. Write `.py` files only; never JavaScript or TypeScript.
- Keep game logic (state, rules, scoring, collisions) in plain modules with no pygame imports,
  so it can be unit tested. Keep pygame to the edges: the window, input, drawing, sound.
- Tests use pytest and live in `tests/`. Pure logic needs no display.
- Code that must touch pygame in a test runs headless: set `SDL_VIDEODRIVER=dummy` and
  `SDL_AUDIODRIVER=dummy` before `pygame.init()` (in the test or a `conftest.py` fixture).
- One entry point (for example `main.py` or `python -m <package>`) creates the window and runs
  the loop at a fixed frame rate with `pygame.time.Clock`.
- Load assets with paths relative to the package, not the current directory.
- Dependencies go in `requirements.txt` with exact pins (`pygame==2.6.1`), then call
  `sync_dependencies` with manager `pip`.
