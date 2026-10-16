"""Files outside the project's stack (worker context plan B6, B7)."""

from __future__ import annotations

from core.harness.spec_brief import SpecBrief
from core.harness.stack_check import outside_stack, stack_languages
from core.spec import Spec, Stack

PYGAME = Stack(language="Python 3.12", framework="pygame")


def test_python_stack_refuses_typescript_and_npm() -> None:
    files = ["game/main.py", "src/game.ts", "web/app.tsx", "package.json", "README.md",
             "assets/ship.png", "requirements.txt", "tests/test_score.py", "config.yaml"]
    assert outside_stack(PYGAME, files) == ["src/game.ts", "web/app.tsx", "package.json"]


def test_typescript_stack_allows_js_and_refuses_python() -> None:
    bun = Stack(language="TypeScript", test_command="bun test")
    assert stack_languages(bun) == {"typescript"}
    assert outside_stack(bun, ["src/a.ts", "src/b.js", "tool.py", "requirements.txt"]) == [
        "tool.py", "requirements.txt"]


def test_unknown_or_empty_stack_checks_nothing() -> None:
    assert outside_stack(Stack(), ["a.ts", "b.py"]) == []
    assert outside_stack(Stack(language="Brainfuck"), ["a.ts"]) == []
    assert outside_stack(None, ["a.ts"]) == []


def test_reviewers_answer_a_stack_line() -> None:
    brief = SpecBrief(Spec({"stack": PYGAME.to_dict()}))
    assert brief.reviewer_lines("builder")[0].startswith(
        "stack: the change is written in Python 3.12, pygame")
    assert SpecBrief(Spec({})).reviewer_lines("builder") == []
