"""Per-project functional checks for the quality gate (plan S14).

Each check is a small function `(RunContext) -> CheckResult`, registered under an
example-project id with `@functional(...)`. They implement the assertions of
`.claude/future_buff/functional_verification.md` (imports clean, entry point runs,
tests pass, spec adherence) for the six projects in `data/example_projects/`.

Rules the checks follow:

  * they look for capabilities, never exact code (LLM output varies);
  * everything runs on a throwaway export of the merged tree, offline, with a
    timeout (see `tests.quality.gate.RunContext`);
  * a failure caused only by a dependency that the project *declares* but the gate's
    interpreter lacks is a `skipped` result, not a failure; an undeclared missing
    import is a real failure;
  * anything needing a browser or a long-lived server is registered as `skipped`
    with the reason.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import unquote

from tests.quality.gate import CheckResult, RunContext, _git, fail, ok, skip

Check = Callable[[RunContext], CheckResult]

FUNCTIONAL_CHECKS: dict[str, list[Check]] = {}
BENCH_TIMEOUT_S = 60

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", "dist", "build"}


def normalise_project(project: str) -> str:
    """`example-api-client-library` and `API-Client-Library` both become `api-client-library`."""
    key = project.strip().lower()
    return key.removeprefix("example-")


def functional(*projects: str, name: str) -> Callable[[Check], Check]:
    """Register a check under one or more project ids; `name` is its report label."""

    def register(fn: Check) -> Check:
        fn.check_name = name  # type: ignore[attr-defined]  # read by gate._guarded on a crash
        for project in projects:
            FUNCTIONAL_CHECKS.setdefault(project, []).append(fn)
        return fn

    return register


# ── Shared helpers ─────────────────────────────────────────────────────────


def walk(root: Path, suffixes: tuple[str, ...] = ()) -> Iterator[Path]:
    """Files under `root` (skipping vcs/build dirs), optionally filtered by suffix."""
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and (not suffixes or path.suffix in suffixes):
            yield path


def is_test_path(rel: str) -> bool:
    """True for test files (pytest, unittest, jest/vitest naming)."""
    p = Path(rel)
    return (p.name.startswith("test_") or p.stem.endswith("_test") or ".test." in p.name
            or ".spec." in p.name or "tests" in p.parts or "test" in p.parts or "__tests__" in p.parts)


def python_tests(tree: Path) -> list[Path]:
    """Python test files in the tree."""
    return [p for p in walk(tree, (".py",))
            if (p.name.startswith("test_") or p.stem.endswith("_test"))]


def parse_py(path: Path) -> ast.Module | None:
    """AST of a Python file, or None when it does not parse."""
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def python_packages(tree: Path) -> list[str]:
    """Importable top-level names the project ships: `src/<pkg>/` packages and `src/<mod>.py`."""
    src = tree / "src"
    names: list[str] = []
    if src.is_dir():
        for entry in sorted(src.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").is_file() and not entry.name.endswith(".egg-info"):
                names.append(entry.name)
            elif entry.suffix == ".py" and entry.stem != "__init__":
                names.append(entry.stem)
        return names
    skip_names = {"tests", "test", "docs", "bench", "scripts", "examples", "backend", "frontend"}
    for entry in sorted(tree.iterdir()):
        if entry.is_dir() and entry.name not in skip_names and (entry / "__init__.py").is_file():
            names.append(entry.name)
    return names


def _norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_text(tree: Path) -> str:
    """Lower-cased text of every dependency declaration file, names normalised."""
    chunks: list[str] = []
    for path in sorted(tree.iterdir()):
        if path.name in ("pyproject.toml", "setup.py", "setup.cfg") or (
                path.name.startswith("requirements") and path.suffix == ".txt"):
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return re.sub(r"[-_.]+", "-", "\n".join(chunks).lower())


_MISSING_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")


def classify_failure(ctx: RunContext, name: str, rc: int, out: str, tree: Path) -> CheckResult:
    """Turn a non-zero subprocess result into a fail, or a skip for an unavailable declared dependency."""
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    # pytest's short summary ("FAILED path::test - reason") beats the noisy tail of warnings.
    summary = [ln for ln in lines if ln.startswith(("FAILED ", "ERROR "))]
    tail = " | ".join(summary[:3] if summary else lines[-3:])
    if rc == 124:
        return fail(name, f"timed out after {ctx.timeout_s}s")
    match = _MISSING_RE.search(out)
    if match:
        top = match.group(1).split(".")[0]
        local = (tree / top).exists() or (tree / f"{top}.py").exists() or (tree / "src" / top).exists() \
            or (tree / "src" / f"{top}.py").exists()
        if not local and importlib.util.find_spec(top) is None and _norm_dist(top) in declared_text(tree):
            return skip(name, f"declared dependency {top!r} is not installed in the gate's environment")
    return fail(name, tail or f"exit code {rc}")


def pytest_check(ctx: RunContext, name: str, tree: Path | None = None,
                 extra: list[str] | None = None) -> CheckResult:
    """Run the merged tree's own pytest suite offline and report the outcome."""
    tree = tree or ctx.tree
    if not python_tests(tree):
        return fail(name, "no Python test files (test_*.py) in the merged tree")
    rc, out = ctx.pytest(tree=tree, extra=extra)
    last = next((ln.strip() for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    if rc == 0:
        return ok(name, last)
    if rc == 5:
        return fail(name, "pytest collected no tests")
    return classify_failure(ctx, name, rc, out, tree)


def import_check(ctx: RunContext, name: str) -> CheckResult:
    """Every package/module the project ships under `src/` imports without error."""
    tree = ctx.tree
    packages = python_packages(tree)
    if not packages:
        return fail(name, "no importable package found (expected src/<package>/__init__.py)")
    rc, out = ctx.run(["-c", "; ".join(f"import {p}" for p in packages)], tree=tree, timeout=60)
    if rc == 0:
        return ok(name, ", ".join(packages))
    return classify_failure(ctx, name, rc, out, tree)


def read_readme(tree: Path) -> str | None:
    """Text of the top-level README, or None."""
    for path in sorted(tree.iterdir()):
        if path.is_file() and path.name.lower().startswith("readme"):
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def readme_check(ctx: RunContext, name: str) -> CheckResult:
    """README exists with an install snippet, a usage section and a fenced code block."""
    text = read_readme(ctx.tree)
    if text is None:
        return fail(name, "no README at the repo root")
    low = text.lower()
    missing = [label for label, ok_ in (
        ("install", "install" in low),
        ("usage", any(w in low for w in ("usage", "quickstart", "quick start", "example"))),
        ("code block", "```" in text)) if not ok_]
    if missing:
        return fail(name, "README lacks: " + ", ".join(missing))
    return ok(name, f"{len(text.splitlines())} lines")


def pyproject(tree: Path) -> dict | None:
    """Parsed `pyproject.toml`, or None when missing/invalid."""
    path = tree / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None


def console_scripts(tree: Path) -> dict[str, str]:
    """Console scripts as `{command: "module:function"}` from pyproject, setup.py or setup.cfg."""
    scripts: dict[str, str] = {}
    data = pyproject(tree)
    if data:
        for cmd, target in (data.get("project", {}).get("scripts") or {}).items():
            scripts[str(cmd)] = str(target)
    for fname in ("setup.py", "setup.cfg"):
        path = tree / fname
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"""['"]?([\w.-]+)\s*=\s*([\w.]+):(\w+)['"]?""", text):
            scripts.setdefault(m.group(1), f"{m.group(2)}:{m.group(3)}")
    return scripts


# ── api-client-library ─────────────────────────────────────────────────────


@functional("api-client-library", "cli-tool-from-spec", name="pyproject.toml")
def check_pyproject(ctx: RunContext) -> CheckResult:
    """`pyproject.toml` (or setup.py) declares an installable project."""
    name = "pyproject.toml"
    tree = ctx.tree
    data = pyproject(tree)
    if data is None:
        if (tree / "setup.py").is_file() or (tree / "setup.cfg").is_file():
            return ok(name, "setup.py/setup.cfg packaging")
        return fail(name, "no valid pyproject.toml (or setup.py) at the repo root")
    project = data.get("project") or {}
    if not project.get("name") and "tool" not in data:
        return fail(name, "pyproject.toml has no [project] name")
    return ok(name, f"project {project.get('name', '?')!r}")


@functional("api-client-library", name="package imports")
def check_api_import(ctx: RunContext) -> CheckResult:
    """The client package imports cleanly."""
    return import_check(ctx, "package imports")


@functional("api-client-library", name="typed client class")
def check_typed_client(ctx: RunContext) -> CheckResult:
    """A `*Client` class exists and every public method has a return annotation."""
    name = "typed client class"
    clients: list[str] = []
    untyped: list[str] = []
    for path in walk(ctx.tree / "src", (".py",)) if (ctx.tree / "src").is_dir() else []:
        module = parse_py(path)
        if module is None:
            return fail(name, f"{path.relative_to(ctx.tree)} does not parse")
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef) and "Client" in node.name:
                clients.append(node.name)
                for fn in node.body:
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and not fn.name.startswith("_") \
                            and fn.returns is None:
                        untyped.append(f"{node.name}.{fn.name}")
    if not clients:
        return fail(name, "no class with 'Client' in its name under src/")
    if untyped:
        return fail(name, "public methods without a return annotation: " + ", ".join(untyped[:8]))
    return ok(name, ", ".join(sorted(set(clients))))


@functional("api-client-library", name="mocked tests pass offline")
def check_api_tests(ctx: RunContext) -> CheckResult:
    """The test suite passes with no network (non-loopback connections are refused)."""
    return pytest_check(ctx, "mocked tests pass offline")


@functional("api-client-library", name="README install + usage")
def check_api_readme(ctx: RunContext) -> CheckResult:
    """README has install and usage with a code block."""
    return readme_check(ctx, "README install + usage")


# ── cli-tool-from-spec ─────────────────────────────────────────────────────


@functional("cli-tool-from-spec", name="console script declared")
def check_cli_script(ctx: RunContext) -> CheckResult:
    """The package registers a console script."""
    name = "console script declared"
    scripts = console_scripts(ctx.tree)
    if not scripts:
        return fail(name, "no [project.scripts] / console_scripts entry")
    return ok(name, ", ".join(f"{c} -> {t}" for c, t in scripts.items()))


@functional("cli-tool-from-spec", name="package imports")
def check_cli_import(ctx: RunContext) -> CheckResult:
    """The CLI package imports cleanly."""
    return import_check(ctx, "package imports")


@functional("cli-tool-from-spec", name="--help works")
def check_cli_help(ctx: RunContext) -> CheckResult:
    """The entry point prints usage and exits 0 for `--help`."""
    name = "--help works"
    tree = ctx.tree
    scripts = console_scripts(tree)
    if scripts:
        cmd, target = next(iter(scripts.items()))
        module, _, func = target.partition(":")
        code = (f"import sys; sys.argv = [{cmd!r}, '--help']; "
                f"from {module} import {func} as _main; sys.exit(_main())")
        how = f"{cmd} --help"
    else:
        mains = [p for p in python_packages(tree) if (tree / "src" / p / "__main__.py").is_file()
                 or (tree / p / "__main__.py").is_file()]
        if not mains:
            return fail(name, "no console script and no __main__.py to run")
        code = f"import runpy, sys; sys.argv = [{mains[0]!r}, '--help']; runpy.run_module({mains[0]!r}, run_name='__main__')"
        how = f"python -m {mains[0]} --help"
    rc, out = ctx.run(["-c", code], tree=tree, timeout=60)
    if rc != 0:
        return classify_failure(ctx, name, rc, out, tree)
    if "usage" not in out.lower():
        return fail(name, f"{how} exited 0 but printed no usage text")
    return ok(name, how)


@functional("cli-tool-from-spec", name="tests pass")
def check_cli_tests(ctx: RunContext) -> CheckResult:
    """The project's pytest suite passes offline."""
    return pytest_check(ctx, "tests pass")


@functional("cli-tool-from-spec", name="README install + usage")
def check_cli_readme(ctx: RunContext) -> CheckResult:
    """README has install and usage with a code block."""
    return readme_check(ctx, "README install + usage")


@functional("cli-tool-from-spec", name="README flags exist in code")
def check_cli_readme_flags(ctx: RunContext) -> CheckResult:
    """Every `--flag` the README documents exists in the source ("never invent flags")."""
    name = "README flags exist in code"
    tree = ctx.tree
    text = read_readme(tree)
    if text is None:
        return fail(name, "no README at the repo root")
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in walk(tree, (".py",))
                     if not is_test_path(p.relative_to(tree).as_posix()))
    documented: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if re.search(r"\b(pip|pipx|pytest|python -m pytest|git)\b", line):
            continue
        documented.update(re.findall(r"(?<![\w-])(--[a-z][\w-]*)", line))
    invented = sorted(f for f in documented if f not in ("--help", "--version") and f not in code)
    if invented:
        return fail(name, "documented but not in the code: " + ", ".join(invented))
    return ok(name, f"{len(documented)} flag(s) documented")


@functional("cli-tool-from-spec", name="docs/cli_spec.md")
def check_cli_spec(ctx: RunContext) -> CheckResult:
    """The designer's spec exists and is not empty."""
    path = ctx.tree / "docs" / "cli_spec.md"
    if not path.is_file() or len(path.read_text(encoding="utf-8", errors="replace").strip()) < 50:
        return fail("docs/cli_spec.md", "missing or nearly empty")
    return ok("docs/cli_spec.md")


# ── algorithm-with-benchmarks ──────────────────────────────────────────────


def _public_defs(tree: Path) -> list[tuple[str, ast.AST]]:
    """Public top-level functions/classes in `src/`, as `(relpath:name, node)`."""
    out: list[tuple[str, ast.AST]] = []
    src = tree / "src"
    for path in walk(src, (".py",)) if src.is_dir() else []:
        module = parse_py(path)
        if module is None:
            continue
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and not node.name.startswith("_"):
                out.append((f"{path.relative_to(tree).as_posix()}:{node.name}", node))
    return out


@functional("algorithm-with-benchmarks", name="implementation under src/")
def check_algo_impl(ctx: RunContext) -> CheckResult:
    """`src/` holds a public function or class."""
    name = "implementation under src/"
    defs = _public_defs(ctx.tree)
    if not defs:
        return fail(name, "no public function or class under src/")
    return ok(name, ", ".join(n for n, _ in defs[:4]))


@functional("algorithm-with-benchmarks", name="entry point typed + documented")
def check_algo_entry(ctx: RunContext) -> CheckResult:
    """A public function has a docstring and a return annotation (the spec's reference API)."""
    name = "entry point typed + documented"
    for label, node in _public_defs(ctx.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None \
                and ast.get_docstring(node):
            return ok(name, label)
    return fail(name, "no public function with both a docstring and a return annotation")


@functional("algorithm-with-benchmarks", name="tests pass")
def check_algo_tests(ctx: RunContext) -> CheckResult:
    """The pytest suite passes and covers more than a single case."""
    result = pytest_check(ctx, "tests pass")
    if not result.passed or result.skipped:
        return result
    count = 0
    for path in python_tests(ctx.tree):
        module = parse_py(path)
        if module is not None:
            count += sum(1 for n in ast.walk(module)
                         if isinstance(n, ast.FunctionDef) and n.name.startswith("test"))
    if count < 3:
        return fail("tests pass", f"suite passes but has only {count} test function(s); the spec asks for edge cases")
    return ok("tests pass", f"{result.detail}; {count} test functions")


@functional("algorithm-with-benchmarks", name="benchmark runs")
def check_algo_bench(ctx: RunContext) -> CheckResult:
    """`bench/benchmark.py` exits 0 within the time limit and prints a table of numbers."""
    name = "benchmark runs"
    tree = ctx.tree
    script = tree / "bench" / "benchmark.py"
    if not script.is_file():
        return fail(name, "bench/benchmark.py is missing")
    limit = min(BENCH_TIMEOUT_S, ctx.timeout_s)
    rc, out = ctx.run(["bench/benchmark.py"], tree=tree, timeout=limit)
    if rc == 124:
        return fail(name, f"benchmark did not finish within {limit}s")
    if rc != 0:
        return classify_failure(ctx, name, rc, out, tree)
    rows = [ln for ln in out.splitlines() if re.search(r"\d", ln) and len(ln.split()) >= 2]
    if len(rows) < 2:
        return fail(name, "benchmark ran but printed no results table")
    return ok(name, f"{len(rows)} result line(s)")


@functional("algorithm-with-benchmarks", name="RESULTS.md table")
def check_algo_results(ctx: RunContext) -> CheckResult:
    """`RESULTS.md` has a markdown table with numeric cells."""
    name = "RESULTS.md table"
    path = ctx.tree / "RESULTS.md"
    if not path.is_file():
        return fail(name, "RESULTS.md is missing")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    has_rule = any(re.match(r"^\s*\|?\s*:?-{2,}", ln) and "|" in ln for ln in lines)
    numeric = [ln for ln in lines if ln.strip().startswith("|") and re.search(r"\d", ln)]
    if not has_rule or not numeric:
        return fail(name, "no markdown results table with numbers")
    return ok(name, f"{len(numeric)} table row(s)")


# ── bug-hunt-and-fix ───────────────────────────────────────────────────────


def _changes(ctx: RunContext) -> list[tuple[str, str]]:
    """`(status, path)` from the run's base commit to HEAD (no rename detection)."""
    out = _git(ctx.working, "diff", "--name-status", "--no-renames", ctx.base_ref(), "HEAD").stdout
    changes: list[tuple[str, str]] = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if path:
            changes.append((status[0], path))
    return changes


def _is_doc(rel: str) -> bool:
    return rel.startswith("docs/") or rel.lower().endswith((".md", ".rst", ".txt"))


@functional("bug-hunt-and-fix", name="fix + regression test present")
def check_bug_changes(ctx: RunContext) -> CheckResult:
    """The run changed a source file (the fix) and added or changed a test."""
    name = "fix + regression test present"
    changes = _changes(ctx)
    fixes = [p for s, p in changes if not is_test_path(p) and not _is_doc(p)]
    tests = [p for s, p in changes if s in "AM" and is_test_path(p)]
    if not fixes:
        return fail(name, "no non-test source file changed since the run started")
    if not tests:
        return fail(name, "no test file added or changed")
    return ok(name, f"{len(fixes)} source file(s), {len(tests)} test file(s)")


@functional("bug-hunt-and-fix", name="fix is focused")
def check_bug_minimal(ctx: RunContext) -> CheckResult:
    """The fix touches a handful of source files, not the whole repo."""
    name = "fix is focused"
    fixes = [p for s, p in _changes(ctx) if not is_test_path(p) and not _is_doc(p)]
    if len(fixes) > 5:
        return fail(name, f"{len(fixes)} source files changed: " + ", ".join(fixes[:6]))
    return ok(name, f"{len(fixes)} source file(s) changed")


@functional("bug-hunt-and-fix", name="ADR written")
def check_bug_adr(ctx: RunContext) -> CheckResult:
    """`docs/adr/` has a non-trivial markdown record."""
    name = "ADR written"
    adrs = sorted((ctx.tree / "docs" / "adr").glob("*.md")) if (ctx.tree / "docs" / "adr").is_dir() else []
    good = [p.name for p in adrs if len(p.read_text(encoding="utf-8", errors="replace").strip()) >= 100]
    if not good:
        return fail(name, "no docs/adr/*.md with real content")
    return ok(name, ", ".join(good))


@functional("bug-hunt-and-fix", name="tests pass after fix")
def check_bug_tests(ctx: RunContext) -> CheckResult:
    """The merged tree's tests pass."""
    return pytest_check(ctx, "tests pass after fix")


@functional("bug-hunt-and-fix", name="regression test fails without the fix")
def check_bug_regression(ctx: RunContext) -> CheckResult:
    """With the fix reverted, the new test fails (it really exercises the bug)."""
    name = "regression test fails without the fix"
    changes = _changes(ctx)
    tests = [p for s, p in changes if s in "AM" and is_test_path(p) and p.endswith(".py")]
    fixes = [(s, p) for s, p in changes if not is_test_path(p) and not _is_doc(p)]
    if not tests:
        return skip(name, "no Python regression test among the changes (other runners are not run offline)")
    if not fixes:
        return skip(name, "no source change to revert (see 'fix + regression test present')")
    pre = ctx.export("HEAD", "prefix")
    base = ctx.base_ref()
    for status, path in fixes:
        target = pre / path
        if status == "A":
            target.unlink(missing_ok=True)
            continue
        shown = _git(ctx.working, "show", f"{base}:{path}")
        if shown.returncode == 0:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(shown.stdout, encoding="utf-8")
    rc, out = ctx.pytest(tree=pre, extra=tests)
    if rc == 0:
        return fail(name, "the new test(s) also pass with the fix reverted")
    if rc == 124:
        return fail(name, f"timed out after {ctx.timeout_s}s")
    return ok(name, f"{len(tests)} test file(s) fail on the pre-fix code (pytest exit {rc})")


# ── codebase-wiki ──────────────────────────────────────────────────────────

WIKI = "docs/wiki"
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE_RE = re.compile(r"```.*?```", re.S)


def _wiki_pages(tree: Path) -> list[Path]:
    root = tree / WIKI
    return sorted(root.rglob("*.md")) if root.is_dir() else []


def _links(page: Path, tree: Path) -> list[str]:
    """Internal link targets of `page` as tree-relative posix paths (resolved, unchecked)."""
    text = _FENCE_RE.sub("", page.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for raw in _LINK_RE.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) or raw.startswith("#"):
            continue
        target = unquote(raw.split("#")[0].split("?")[0])
        if not target:
            continue
        base = tree if target.startswith("/") else page.parent
        resolved = (base / target.lstrip("/")).resolve()
        try:
            out.append(resolved.relative_to(tree.resolve()).as_posix())
        except ValueError:
            out.append(f"../{target}")
    return out


@functional("codebase-wiki", name="wiki structure")
def check_wiki_structure(ctx: RunContext) -> CheckResult:
    """index, architecture, glossary, and at least one module and one flow page exist."""
    name = "wiki structure"
    root = ctx.tree / WIKI
    missing = [f for f in ("index.md", "architecture.md", "glossary.md") if not (root / f).is_file()]
    for sub in ("modules", "flows"):
        if not list((root / sub).glob("*.md")):
            missing.append(f"{sub}/*.md")
    if missing:
        return fail(name, "missing under docs/wiki: " + ", ".join(missing))
    return ok(name, f"{len(_wiki_pages(ctx.tree))} page(s)")


@functional("codebase-wiki", name="no empty pages")
def check_wiki_nonempty(ctx: RunContext) -> CheckResult:
    """Every wiki page has real content."""
    name = "no empty pages"
    pages = _wiki_pages(ctx.tree)
    if not pages:
        return fail(name, "no pages under docs/wiki")
    thin = [p.relative_to(ctx.tree).as_posix() for p in pages
            if len(p.read_text(encoding="utf-8", errors="replace").strip()) < 80]
    if thin:
        return fail(name, "nearly empty: " + ", ".join(thin))
    return ok(name)


@functional("codebase-wiki", name="architecture mermaid diagram")
def check_wiki_mermaid(ctx: RunContext) -> CheckResult:
    """architecture.md has a fenced mermaid diagram with edges."""
    name = "architecture mermaid diagram"
    path = ctx.tree / WIKI / "architecture.md"
    if not path.is_file():
        return fail(name, "docs/wiki/architecture.md is missing")
    m = re.search(r"```mermaid\s*\n(.*?)```", path.read_text(encoding="utf-8", errors="replace"), re.S)
    if not m:
        return fail(name, "no ```mermaid block")
    if "-->" not in m.group(1) and "---" not in m.group(1) and "->>" not in m.group(1):
        return fail(name, "mermaid block draws no edges")
    return ok(name)


@functional("codebase-wiki", name="internal links resolve")
def check_wiki_links(ctx: RunContext) -> CheckResult:
    """Every relative markdown link on every page points at an existing file."""
    name = "internal links resolve"
    tree = ctx.tree
    broken: list[str] = []
    total = 0
    for page in _wiki_pages(tree):
        for target in _links(page, tree):
            total += 1
            if target.startswith("../") or not (tree / target).exists():
                broken.append(f"{page.relative_to(tree).as_posix()} -> {target}")
    if broken:
        return fail(name, f"{len(broken)} broken: " + "; ".join(broken[:5]))
    return ok(name, f"{total} link(s)")


@functional("codebase-wiki", name="index reaches every page")
def check_wiki_reachable(ctx: RunContext) -> CheckResult:
    """Following links from index.md reaches every page (the wiki is navigable)."""
    name = "index reaches every page"
    tree = ctx.tree
    index = tree / WIKI / "index.md"
    if not index.is_file():
        return fail(name, "docs/wiki/index.md is missing")
    pages = {p.relative_to(tree).as_posix(): p for p in _wiki_pages(tree)
             if not p.name.startswith("_")}
    seen: set[str] = set()
    queue = [f"{WIKI}/index.md"]
    while queue:
        rel = queue.pop()
        if rel in seen or rel not in pages:
            continue
        seen.add(rel)
        queue.extend(_links(pages[rel], tree))
    orphans = sorted(set(pages) - seen)
    if orphans:
        return fail(name, "not reachable from index.md: " + ", ".join(orphans[:6]))
    return ok(name, f"{len(pages)} page(s)")


@functional("codebase-wiki", name="flow pages cite file:line")
def check_wiki_citations(ctx: RunContext) -> CheckResult:
    """Each flow page carries `file:line` citations."""
    name = "flow pages cite file:line"
    flows = sorted((ctx.tree / WIKI / "flows").glob("*.md")) if (ctx.tree / WIKI / "flows").is_dir() else []
    if not flows:
        return fail(name, "no flow pages")
    pattern = re.compile(r"[\w./-]+\.\w+:\d+")
    bare = [p.name for p in flows if not pattern.search(p.read_text(encoding="utf-8", errors="replace"))]
    if bare:
        return fail(name, "no file:line citation in: " + ", ".join(bare))
    return ok(name, f"{len(flows)} flow page(s)")


@functional("codebase-wiki", name="glossary alphabetised")
def check_wiki_glossary(ctx: RunContext) -> CheckResult:
    """The glossary has at least three bold terms, in alphabetical order."""
    name = "glossary alphabetised"
    path = ctx.tree / WIKI / "glossary.md"
    if not path.is_file():
        return fail(name, "docs/wiki/glossary.md is missing")
    terms = re.findall(r"^\s*(?:[-*]\s+)?\*\*([^*]+)\*\*", path.read_text(encoding="utf-8", errors="replace"), re.M)
    if len(terms) < 3:
        return fail(name, f"only {len(terms)} bold term(s)")
    keys = [t.strip().lower() for t in terms]
    if keys != sorted(keys):
        return fail(name, "terms are not in alphabetical order")
    return ok(name, f"{len(terms)} term(s)")


# ── fullstack-web-app ──────────────────────────────────────────────────────

_ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+`?(/[\w./{}:<>-]*)", re.I)


def _static_prefix(path: str) -> str:
    """The leading static part of a route: `/api/items/{id}` -> `/api/items`."""
    cut = re.split(r"[{:<]|\$\{", path, maxsplit=1)[0]
    return cut.rstrip("/") or "/"


def _backend_text(tree: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in walk(tree / "backend", (".py",))) if (tree / "backend").is_dir() else ""


def _served(prefix: str, backend: str) -> bool:
    """True when the backend source mentions `prefix` (or, for a single router prefix, its tail)."""
    if prefix == "/" or prefix in backend:
        return True
    tail = "/" + prefix.strip("/").split("/", 1)[-1] if prefix.strip("/").count("/") else ""
    return bool(tail) and tail in backend


@functional("fullstack-web-app", name="backend + frontend layout")
def check_fullstack_layout(ctx: RunContext) -> CheckResult:
    """`backend/` has Python that parses, `frontend/` has an HTML page, the contract exists."""
    name = "backend + frontend layout"
    tree = ctx.tree
    py = list(walk(tree / "backend", (".py",))) if (tree / "backend").is_dir() else []
    html = list(walk(tree / "frontend", (".html",))) if (tree / "frontend").is_dir() else []
    problems: list[str] = []
    if not py:
        problems.append("no Python under backend/")
    if not html:
        problems.append("no .html under frontend/")
    if not (tree / "docs" / "api_contract.md").is_file():
        problems.append("docs/api_contract.md missing")
    problems.extend(f"{p.relative_to(tree).as_posix()} does not parse" for p in py if parse_py(p) is None)
    if problems:
        return fail(name, "; ".join(problems))
    return ok(name, f"{len(py)} backend file(s), {len(html)} page(s)")


@functional("fullstack-web-app", name="contract matches backend")
def check_fullstack_contract(ctx: RunContext) -> CheckResult:
    """Every `METHOD /path` in `docs/api_contract.md` appears in the backend source."""
    name = "contract matches backend"
    path = ctx.tree / "docs" / "api_contract.md"
    if not path.is_file():
        return fail(name, "docs/api_contract.md is missing")
    routes = sorted({m.group(1) for m in _ROUTE_RE.finditer(path.read_text(encoding="utf-8", errors="replace"))})
    if not routes:
        return fail(name, "the contract lists no 'METHOD /path' routes")
    backend = _backend_text(ctx.tree)
    unserved = [r for r in routes if not _served(_static_prefix(r), backend)]
    if unserved:
        return fail(name, "in the contract but not in the backend: " + ", ".join(unserved))
    return ok(name, f"{len(routes)} route(s)")


@functional("fullstack-web-app", name="frontend calls real endpoints")
def check_fullstack_frontend(ctx: RunContext) -> CheckResult:
    """The frontend's `fetch()` paths exist in the backend (no invented endpoints)."""
    name = "frontend calls real endpoints"
    tree = ctx.tree
    front = tree / "frontend"
    if not front.is_dir():
        return fail(name, "frontend/ is missing")
    paths: set[str] = set()
    for page in walk(front, (".html", ".js", ".mjs", ".jsx", ".ts")):
        text = page.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"fetch\(\s*([`'\"])(.*?)\1", text):
            raw = m.group(2)
            raw = re.sub(r"^https?://[^/]+", "", raw)
            if raw.startswith("/"):
                paths.add(_static_prefix(raw.split("?")[0]))
    if not paths:
        return fail(name, "the frontend never calls the backend with fetch('/...')")
    backend = _backend_text(tree)
    invented = sorted(p for p in paths if not _served(p, backend))
    if invented:
        return fail(name, "not served by the backend: " + ", ".join(invented))
    return ok(name, ", ".join(sorted(paths)))


@functional("fullstack-web-app", name="integration tests pass")
def check_fullstack_tests(ctx: RunContext) -> CheckResult:
    """The project's integration tests (they start the server on loopback themselves) pass."""
    return pytest_check(ctx, "integration tests pass")


@functional("fullstack-web-app", name="app boots and serves the frontend")
def check_fullstack_boot(ctx: RunContext) -> CheckResult:
    """Booting the app needs a long-lived server whose entry point the spec does not fix."""
    return skip("app boots and serves the frontend",
                "needs a running server and the entry point is not fixed by the spec; "
                "the project's own integration tests start the app and are run above")
