"""Repo map — compact ranked view of the codebase.

Parses Python and JS/TS files via tree-sitter, extracts top-level symbols and
imports, builds an import graph, runs PageRank to rank files by importance,
and renders a token-bounded compact summary that ALWAYS fits in agent context.

DESIGN §28: the repo map is in every `get_context()` call. It is cheap and
always useful. It is rebuilt incrementally on GIT_COMMIT — full rebuild only
on first run.

The map is data — `core/workspace.py:RepoMap` is the dataclass. This module
populates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tree_sitter_javascript
import tree_sitter_python
from tree_sitter import Language, Parser

from core.workspace import RepoMap, RepoMapEntry


_PY_LANGUAGE = Language(tree_sitter_python.language(), "python")
_JS_LANGUAGE = Language(tree_sitter_javascript.language(), "javascript")


PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}


@dataclass
class _ParsedFile:
    """Internal parse result. Becomes a RepoMapEntry after import resolution."""

    path: str  # repo-relative, posix-style
    type: str  # "module"
    classes: list[str]
    functions: list[str]
    imports: list[str]  # raw module strings, unresolved


# ── Parsing ─────────────────────────────────────────────────────────────────


def _parser_for(suffix: str) -> Parser | None:
    p = Parser()
    if suffix in PY_EXTS:
        p.set_language(_PY_LANGUAGE)
        return p
    if suffix in JS_EXTS:
        p.set_language(_JS_LANGUAGE)
        return p
    return None


def _walk_top_level(root):
    """Yield direct children of the root node."""
    for child in root.children:
        yield child


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _parse_python(rel_path: str, source: bytes) -> _ParsedFile:
    parser = Parser()
    parser.set_language(_PY_LANGUAGE)
    tree = parser.parse(source)

    classes: list[str] = []
    functions: list[str] = []
    imports: list[str] = []

    for node in _walk_top_level(tree.root_node):
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                classes.append(_node_text(name_node, source))
        elif node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                functions.append(_node_text(name_node, source))
        elif node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                name_node = inner.child_by_field_name("name")
                if name_node is not None:
                    if inner.type == "class_definition":
                        classes.append(_node_text(name_node, source))
                    elif inner.type == "function_definition":
                        functions.append(_node_text(name_node, source))
        elif node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append(_node_text(child, source))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        imports.append(_node_text(name, source))
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            if module_node is not None:
                imports.append(_node_text(module_node, source))

    return _ParsedFile(
        path=rel_path,
        type="module",
        classes=classes,
        functions=functions,
        imports=imports,
    )


def _parse_javascript(rel_path: str, source: bytes) -> _ParsedFile:
    parser = Parser()
    parser.set_language(_JS_LANGUAGE)
    tree = parser.parse(source)

    classes: list[str] = []
    functions: list[str] = []
    imports: list[str] = []

    def _scan(node):
        for child in node.children:
            if child.type == "class_declaration":
                name = child.child_by_field_name("name")
                if name is not None:
                    classes.append(_node_text(name, source))
            elif child.type == "function_declaration":
                name = child.child_by_field_name("name")
                if name is not None:
                    functions.append(_node_text(name, source))
            elif child.type == "lexical_declaration" or child.type == "variable_declaration":
                # const Foo = (...) => ... — capture the name
                for declarator in child.children:
                    if declarator.type == "variable_declarator":
                        name = declarator.child_by_field_name("name")
                        value = declarator.child_by_field_name("value")
                        if name is not None and value is not None and value.type in (
                            "arrow_function", "function_expression", "function"
                        ):
                            functions.append(_node_text(name, source))
            elif child.type == "import_statement":
                source_node = child.child_by_field_name("source")
                if source_node is not None:
                    raw = _node_text(source_node, source)
                    imports.append(raw.strip("'\""))
            elif child.type == "export_statement":
                # Recurse — exported declarations live inside.
                _scan(child)

    _scan(tree.root_node)

    return _ParsedFile(
        path=rel_path,
        type="module",
        classes=classes,
        functions=functions,
        imports=imports,
    )


def _parse_file(rel_path: str, source: bytes) -> _ParsedFile | None:
    suffix = Path(rel_path).suffix
    if suffix in PY_EXTS:
        return _parse_python(rel_path, source)
    if suffix in JS_EXTS:
        return _parse_javascript(rel_path, source)
    return None


# ── Import resolution (best-effort) ────────────────────────────────────────


def _resolve_imports(
    file: _ParsedFile,
    all_paths: set[str],
) -> list[str]:
    """Return the subset of `file.imports` that resolve to a known path
    in the repo. External (third-party) imports are dropped silently."""
    resolved: list[str] = []
    self_dir = str(Path(file.path).parent).replace("\\", "/")
    if self_dir == ".":
        self_dir = ""

    for raw in file.imports:
        candidates = _import_candidates(raw, self_dir)
        for cand in candidates:
            if cand in all_paths:
                resolved.append(cand)
                break
    return resolved


def _import_candidates(raw: str, self_dir: str) -> list[str]:
    """Return possible repo-relative paths an import string might map to.

    Handles Python dotted names (`pkg.mod`), Python relative-marker imports
    (rare at top-level, treat as bare), and JS relative paths (`./foo`).
    """
    candidates: list[str] = []

    # JS relative imports
    if raw.startswith("./") or raw.startswith("../"):
        base = (Path(self_dir) / raw).as_posix()
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            candidates.append(f"{base}{ext}")
            candidates.append(f"{base}/index{ext}")
        return candidates

    # Skip JS bare imports — external packages.
    if "/" not in raw and not raw.replace(".", "").replace("_", "").isalnum():
        return []

    # Python dotted names
    if "." in raw:
        as_path = raw.replace(".", "/")
        candidates.append(f"{as_path}.py")
        candidates.append(f"{as_path}/__init__.py")
    else:
        candidates.append(f"{raw}.py")
        candidates.append(f"{raw}/__init__.py")

    return candidates


# ── PageRank ────────────────────────────────────────────────────────────────


def _pagerank(
    nodes: list[str],
    edges: dict[str, list[str]],
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """Vanilla PageRank. Edges go u → v meaning "u imports v" so heavily
    imported files end up with higher rank, which is what we want."""
    if not nodes:
        return {}

    n = len(nodes)
    rank = {node: 1.0 / n for node in nodes}
    out_degree = {node: max(len(edges.get(node, [])), 1) for node in nodes}

    # Build inverse graph: for each v, who imports v?
    inbound: dict[str, list[str]] = {node: [] for node in nodes}
    for u, vs in edges.items():
        for v in vs:
            if v in inbound and u != v:
                inbound[v].append(u)

    for _ in range(iterations):
        new_rank = {}
        for v in nodes:
            new_rank[v] = (1 - damping) / n + damping * sum(
                rank[u] / out_degree[u] for u in inbound[v]
            )
        rank = new_rank

    return rank


# ── Public API ──────────────────────────────────────────────────────────────


SUPPORTED_EXTS = PY_EXTS | JS_EXTS

# Track 4.4 — directories we never descend into when building the repo map.
# These are usually massive (node_modules: ~50k+ files) and contribute zero
# signal — they're vendored deps, build artifacts, or virtualenvs. Tree-
# sitter parsing every file here turns a 1-second init into a multi-minute
# stall when AgentFlow is pointed at a real repo (in-place mode).
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules",
    "venv", "env", ".venv", ".env",
    "dist", "build", "out", "target",
    "__pycache__",
    ".flow",          # AgentFlow's own metadata
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor",
    "coverage", "htmlcov", ".nyc_output",
    ".next", ".nuxt", ".svelte-kit", ".turbo",
    "bower_components",
    ".tox",
    "site-packages",
})

# Soft cap: above this many parsed files, log and stop. The map has been
# useful long before this point; PageRank on a 5k-node graph is fine, on a
# 50k-node graph is sluggish and burns memory for no win.
_MAX_PARSED_FILES = 5000


def _iter_supported_files(root: Path) -> Iterable[Path]:
    """Walk `root` yielding parseable source files. Prunes large
    vendored/build/cache directories — see _SKIP_DIRS — so init stays
    fast on real repos."""
    import os as _os
    for dirpath, dirnames, filenames in _os.walk(root):
        # Prune in place — os.walk respects mutation of dirnames.
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for fname in filenames:
            suffix = Path(fname).suffix
            if suffix not in SUPPORTED_EXTS:
                continue
            yield Path(dirpath) / fname


def build(root: Path | str, generated_at: str = "") -> RepoMap:
    """Full scan and rebuild of the repo map for `root`. Capped at
    `_MAX_PARSED_FILES` so a misconfigured / unfiltered tree doesn't
    pin the runner at startup."""
    import logging
    log = logging.getLogger(__name__)
    root = Path(root).resolve()
    parsed: dict[str, _ParsedFile] = {}
    capped = False
    for path in _iter_supported_files(root):
        if len(parsed) >= _MAX_PARSED_FILES:
            capped = True
            break
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_bytes()
        except OSError:
            continue
        pf = _parse_file(rel, source)
        if pf is not None:
            parsed[rel] = pf

    if capped:
        log.warning(
            "repo_map: hit %d-file cap at %s; PageRank is partial. "
            "Add directories to _SKIP_DIRS if a large vendored tree is leaking through.",
            _MAX_PARSED_FILES, root,
        )

    return _assemble(parsed, generated_at)


def update_incremental(
    existing: RepoMap,
    root: Path | str,
    changed_paths: list[str],
    generated_at: str = "",
) -> RepoMap:
    """Re-parse only the paths in `changed_paths`, leave others as-is, then
    re-run PageRank because edge changes can ripple. PageRank is cheap on a
    small graph so this is fine.

    `changed_paths` are repo-relative posix paths. Paths that no longer
    exist are removed from the map.
    """
    root = Path(root).resolve()
    parsed: dict[str, _ParsedFile] = {}

    # Carry over entries that weren't touched.
    changed_set = set(changed_paths)
    for entry in existing.entries:
        if entry.path not in changed_set:
            parsed[entry.path] = _ParsedFile(
                path=entry.path,
                type=entry.type,
                classes=[s for s in entry.symbols if s and s[0].isupper()],
                functions=[s for s in entry.symbols if s and s[0].islower()],
                imports=list(entry.imports),
            )

    for rel in changed_paths:
        path = root / rel
        if not path.is_file():
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        pf = _parse_file(rel, source)
        if pf is not None:
            parsed[rel] = pf

    return _assemble(parsed, generated_at)


def _assemble(parsed: dict[str, _ParsedFile], generated_at: str) -> RepoMap:
    all_paths = set(parsed.keys())
    edges: dict[str, list[str]] = {}
    for pf in parsed.values():
        edges[pf.path] = _resolve_imports(pf, all_paths)
    ranks = _pagerank(list(parsed.keys()), edges)

    entries = []
    for pf in parsed.values():
        symbols = pf.classes + pf.functions
        entries.append(RepoMapEntry(
            path=pf.path,
            type=pf.type,
            symbols=symbols,
            imports=edges[pf.path],
            rank=ranks.get(pf.path, 0.0),
        ))
    entries.sort(key=lambda e: e.rank, reverse=True)
    return RepoMap(generated_at=generated_at, entries=entries)


# ── Rendering ───────────────────────────────────────────────────────────────


def _approx_tokens(text: str) -> int:
    """Cheap proxy for tokeniser length. Real tokenisation is model-specific
    and we do not want a tiktoken dependency in core/. ~4 chars/token is a
    reasonable upper-bound for English + code."""
    return max(1, len(text) // 4)


# ── Task-aware scoring (Track 1d) ───────────────────────────────────────────


import re as _re

_WORD_RE = _re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _split_camel_snake(word: str) -> set[str]:
    """Split CamelCase / snake_case identifiers into lower-cased component tokens."""
    parts = _re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", word)
    return {p.lower() for p in parts if p}


def _query_tokens(text: str) -> set[str]:
    """Identifier-ish tokens (whole + camel/snake split) for keyword matching."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        out.add(word.lower())
        out.update(_split_camel_snake(word))
    return out


def score_entries_for_task(
    repo_map: RepoMap,
    task: str,
) -> list[tuple[float, RepoMapEntry]]:
    """Score every entry by symbol/path overlap with task tokens.

    Track 1d — the task-aware companion to PageRank. Returns descending-sorted
    (score, entry) pairs. Entries with zero overlap are filtered out.

    Score formula (matches core/context_manager.py:_score_entry):
        2.0 * symbol_hits + 1.0 * path_hits + 0.1 * entry.rank
    """
    if not task or not task.strip() or not repo_map.entries:
        return []
    qtokens = _query_tokens(task)
    if not qtokens:
        return []
    scored: list[tuple[float, RepoMapEntry]] = []
    for entry in repo_map.entries:
        path_t = _query_tokens(entry.path)
        sym_t: set[str] = set()
        for s in entry.symbols:
            sym_t.update(_split_camel_snake(s))
        path_hits = len(path_t & qtokens)
        sym_hits = len(sym_t & qtokens)
        if path_hits == 0 and sym_hits == 0:
            continue
        score = 2.0 * sym_hits + 1.0 * path_hits + 0.1 * entry.rank
        scored.append((score, entry))
    scored.sort(key=lambda p: p[0], reverse=True)
    return scored


def render_compact(repo_map: RepoMap, max_tokens: int = 1500) -> str:
    """Render the map as compact lines, capped at `max_tokens`.

    Format per file:
        path  [type]  symbols: A, b, c  imports: x, y
    """
    lines: list[str] = []
    used = 0
    for entry in repo_map.entries:
        sym = ", ".join(entry.symbols[:8])
        imp = ", ".join(entry.imports[:6])
        line = f"{entry.path}  [{entry.type}]"
        if sym:
            line += f"  symbols: {sym}"
        if imp:
            line += f"  imports: {imp}"
        cost = _approx_tokens(line) + 1
        if used + cost > max_tokens and lines:
            lines.append(f"... ({len(repo_map.entries) - len(lines)} more files truncated)")
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)
