"""Files outside the project's stack (worker context plan B6).

A check, not a prompt: when the stack names its language (Python, TypeScript, ...), a change
that adds code in another language, or another ecosystem's manifest (a `package.json` in a
pygame project), is sent back. Docs, config, data and assets always pass, and so does
everything when the stack names no language GuudFlow knows.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from core.spec import Stack

_JS = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_TS = frozenset({".ts", ".tsx", ".mts", ".cts"})
LANGUAGES: dict[str, tuple[re.Pattern[str], frozenset[str]]] = {
    "python": (re.compile(r"\bpython|pygame|django|flask|fastapi|pytest\b", re.I),
               frozenset({".py", ".pyi"})),
    "typescript": (re.compile(r"\btypescript|\bts\b|\bbun\b|deno|angular", re.I), _TS | _JS),
    "javascript": (re.compile(r"\bjavascript|\bjs\b|node|react|vue|svelte|vite|vitest|jest",
                              re.I), _JS),
    "go": (re.compile(r"\bgo(lang)?\b", re.I), frozenset({".go"})),
    "rust": (re.compile(r"\brust\b|cargo", re.I), frozenset({".rs"})),
    "java": (re.compile(r"\bjava\b|spring|gradle|maven", re.I), frozenset({".java"})),
    "kotlin": (re.compile(r"kotlin", re.I), frozenset({".kt", ".kts"})),
    "csharp": (re.compile(r"c#|csharp|\.net|dotnet|unity", re.I), frozenset({".cs"})),
    "cpp": (re.compile(r"c\+\+|\bcpp\b", re.I), frozenset({".cpp", ".cc", ".hpp", ".h"})),
    "ruby": (re.compile(r"\bruby|rails", re.I), frozenset({".rb"})),
    "php": (re.compile(r"\bphp|laravel", re.I), frozenset({".php"})),
    "swift": (re.compile(r"\bswift", re.I), frozenset({".swift"})),
}
CODE_EXTENSIONS = frozenset().union(*(exts for _, exts in LANGUAGES.values()))
# Manifests that mean "this is a project in that ecosystem".
_MANIFESTS = {"package.json": ("typescript", "javascript"), "tsconfig.json": ("typescript",),
              "bun.lockb": ("typescript",), "requirements.txt": ("python",),
              "pyproject.toml": ("python",), "setup.py": ("python",), "Cargo.toml": ("rust",),
              "go.mod": ("go",)}


def stack_languages(stack: Stack | None) -> set[str]:
    """The languages the stack names (empty: no check)."""
    if stack is None:
        return set()
    text = f"{stack.language} {stack.framework} {stack.test_command}"
    return {name for name, (pattern, _) in LANGUAGES.items() if pattern.search(text)}


def outside_stack(stack: Stack | None, files: Sequence[str]) -> list[str]:
    """The files that do not belong to the stack (empty when the stack is unknown)."""
    langs = stack_languages(stack)
    if not langs:
        return []
    allowed = frozenset().union(*(LANGUAGES[lang][1] for lang in langs))
    out: list[str] = []
    for f in files:
        path = PurePosixPath(f)
        owners = _MANIFESTS.get(path.name)
        if owners is not None:
            if not langs.intersection(owners):
                out.append(f)
            continue
        ext = path.suffix.lower()
        if ext in CODE_EXTENSIONS and ext not in allowed:
            out.append(f)
    return out


def problem_text(stack: Stack, files: Sequence[str]) -> str:
    """The reason given back to the worker and on the card."""
    what = stack.language or stack.framework
    return (f"files outside the stack ({what}): {', '.join(files)}. The project is built with "
            f"{what}; rewrite this work in it or remove these files.")


__all__ = ["CODE_EXTENSIONS", "LANGUAGES", "outside_stack", "problem_text", "stack_languages"]
