"""The verification ledger (role contracts plan §4.4): per spec check, its state and the evidence.

GuudFlow writes the ledger; no model does. For each check a session served:

- **verified**: its linked test ran on the host and passed (in `test_first`, it also failed
  before the change);
- **failing**: its linked test ran and failed;
- **missing_test**: it names a test that the run's report does not contain;
- **claimed**: the reviewer said met and no test proves it (manual checks, no test linked, or a
  test that already passed before the change in `test_first`);
- **unmet**: the reviewer said unmet;
- **unchecked**: nobody said anything;
- **person**: the owner confirmed it at acceptance (manual and visual checks).

Per-test results come from a JUnit XML report the project's check command writes. Known test
runners (`pytest`, `bun test`, `vitest`, `jest`) get the reporter flags added automatically
(`with_report`); any other command can write its own report to `approval_gates.test_report`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

REPORT_PATH = ".guudflow/junit.xml"

LedgerState = Literal["verified", "failing", "missing_test", "claimed", "unmet", "unchecked",
                      "person"]
TestStatus = Literal["passed", "failed", "skipped"]

# Runner → the flags that make it write JUnit XML to {path}.
_RUNNERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:python3? -m )?pytest\b"), "--junitxml={path}"),
    (re.compile(r"\bbun test\b"), "--reporter=junit --reporter-outfile={path}"),
    (re.compile(r"\bvitest\b(?: run)?"), "--reporter=default --reporter=junit --outputFile={path}"),
    (re.compile(r"\bjest\b"), "--reporters=default --reporters=jest-junit"),
)


def with_report(command: str, path: str = REPORT_PATH) -> tuple[str, str | None]:
    """The command with JUnit output added for a known runner, and the report path (None when
    the runner is not recognised or the command already asks for a report)."""
    if "junit" in command:
        return command, path if path in command else None
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    prefix = f"mkdir -p {folder} && " if folder else ""      # bun does not create the folder
    for pattern, flags in _RUNNERS:
        found = pattern.search(command)
        if found:
            end = found.end()
            if "jest-junit" in flags:
                return (prefix + f"JEST_JUNIT_OUTPUT_FILE={path} " + command[:end] + " "
                        + flags + command[end:]), path
            return prefix + command[:end] + " " + flags.format(path=path) + command[end:], path
    return command, None


@dataclass(frozen=True)
class TestOutcome:
    """One test case from a JUnit report."""

    file: str        # "tests/dash.test.js" (best effort: file attribute, suite name or classname)
    name: str        # the test's own name
    full: str        # describe path and name joined with " > "
    status: TestStatus

    @property
    def key(self) -> str:
        """`file::full`, the id the ledger records."""
        return f"{self.file}::{self.full}"


_FILE_LIKE = re.compile(r"[\w./-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx)$")


def _pytest_class(classname: str) -> tuple[str, list[str]] | None:
    """pytest's dotted classname ("tests.test_dash.TestDash") → (file, class parts)."""
    if not classname or "." not in classname or "/" in classname or " " in classname:
        return None
    parts = classname.split(".")
    mods = [p for p in parts if not p[:1].isupper()]
    classes = [p for p in parts if p[:1].isupper()]
    return "/".join(mods) + ".py", classes


def parse_junit(text: str) -> list[TestOutcome]:
    """Every test case of a JUnit XML report; [] when it does not parse.

    The file comes from the case's `file` attribute, else the nearest suite named like a file
    (bun, vitest), else pytest's dotted classname. Suites nested under a file suite are describe
    blocks and form the path ("Dash > refills on a power pellet")."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: list[TestOutcome] = []

    def case(node: ET.Element, file: str, path: list[str]) -> None:
        failed = any(c.tag in ("failure", "error") for c in node)
        skipped = any(c.tag == "skipped" for c in node)
        name = node.get("name") or ""
        status: TestStatus = "failed" if failed else "skipped" if skipped else "passed"
        where, extra = node.get("file") or file, list(path)
        if not where:
            dotted = _pytest_class(node.get("classname") or "")
            if dotted:
                where, extra = dotted[0], dotted[1]
            else:
                where = node.get("classname") or ""
        out.append(TestOutcome(file=where, name=name, full=" > ".join(extra + [name]),
                               status=status))

    def walk(node: ET.Element, file: str, path: list[str]) -> None:
        for child in node:
            if child.tag in ("testsuite", "testsuites"):
                name = child.get("name") or ""
                own = child.get("file") or (name if _FILE_LIKE.search(name) else "")
                if own and own == file and name and name != own:
                    walk(child, file, path + [name])        # bun: describe blocks name the file
                elif own:
                    walk(child, own, [])
                elif file and name:
                    walk(child, file, path + [name])        # a describe block inside a file
                else:
                    walk(child, file, path)                 # a top-level group ("pytest")
            elif child.tag == "testcase":
                case(child, file, path)

    holder = ET.Element("root")
    holder.append(root)
    walk(holder, "", [])
    return out


def outcomes_from(tests: Mapping[str, str]) -> list[TestOutcome]:
    """Test outcomes back from a stored `{key: status}` map (`CheckResult.tests`)."""
    out = []
    for key, status in tests.items():
        file, _, full = key.partition("::")
        out.append(TestOutcome(file=file, name=full.rsplit(" > ", 1)[-1], full=full,
                               status=status if status in ("passed", "failed", "skipped")
                               else "failed"))
    return out


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def match_test(link: str, results: Sequence[TestOutcome]) -> list[TestOutcome]:
    """The test cases a check's `test` link ("file::name", or just "file") names."""
    file, _, name = link.partition("::")
    file, name = _norm(file.strip()), name.strip()
    in_file = [r for r in results if r.file and (_norm(r.file).endswith(file)
                                                 or file.endswith(_norm(r.file)))]
    if not name:
        return in_file
    wanted = name.casefold()
    return [r for r in in_file
            if r.name.casefold() == wanted or r.full.casefold() == wanted
            or r.full.casefold().endswith(" > " + wanted)
            or r.name.casefold().startswith(wanted + "[")              # pytest parametrize
            or r.full.casefold().replace(" > ", "::") == wanted]


@dataclass
class LedgerEntry:
    """One check's state and the evidence behind it."""

    check: str
    state: LedgerState
    method: str | None = None
    tests: list[str] = field(default_factory=list)     # test keys that were matched
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:  # Any: JSON
        """JSON form."""
        return asdict(self)


@dataclass(frozen=True)
class CheckInfo:
    """What the ledger needs to know about a check."""

    id: str
    method: str | None
    test: str | None


def build_ledger(checks: Sequence[CheckInfo], results: Sequence[TestOutcome] | None,
                 verdicts: Mapping[str, bool], *,
                 before: Sequence[TestOutcome] | None = None) -> dict[str, LedgerEntry]:
    """Ledger entries for `checks`.

    `results` are the run's test cases (None: the check command did not report per test);
    `verdicts` the reviewer's latest verdict per check id; `before` the same tests at the run's
    base (test first: a test that already passed there proves nothing about this change)."""
    out: dict[str, LedgerEntry] = {}
    for c in checks:
        said = verdicts.get(c.id)
        if c.test and results is not None:
            found = match_test(c.test, results)
            ran = [r for r in found if r.status != "skipped"]
            if not ran:
                out[c.id] = LedgerEntry(c.id, "missing_test", c.method, [],
                                        f"no test matching {c.test} ran")
                continue
            keys = [r.key for r in ran]
            if any(r.status == "failed" for r in ran):
                failed = [r.key for r in ran if r.status == "failed"]
                out[c.id] = LedgerEntry(c.id, "failing", c.method, keys,
                                        "failed: " + ", ".join(failed[:3]))
                continue
            if before is not None:
                earlier = match_test(c.test, before)
                if earlier and all(r.status == "passed" for r in earlier):
                    out[c.id] = LedgerEntry(c.id, "claimed" if said else "unchecked", c.method,
                                            keys, "its test already passed before the change")
                    continue
            out[c.id] = LedgerEntry(c.id, "verified", c.method, keys,
                                    f"{len(keys)} test(s) passed")
            continue
        if said is True:
            reason = ("no test linked" if not c.test else "the check command reports no "
                      "per-test results")
            out[c.id] = LedgerEntry(c.id, "claimed", c.method, [], f"reviewer said met; {reason}")
        elif said is False:
            out[c.id] = LedgerEntry(c.id, "unmet", c.method, [], "reviewer said unmet")
        else:
            out[c.id] = LedgerEntry(c.id, "unchecked", c.method)
    return out


def confirm(ledger: dict[str, dict[str, Any]], confirmed: Sequence[str],  # Any: JSON entries
            by: str) -> dict[str, dict[str, Any]]:  # Any: JSON entries
    """The owner confirmed `confirmed` checks at acceptance (manual or visual ones). A failing
    or missing test is not overridden by a confirmation."""
    out = {k: dict(v) for k, v in ledger.items()}
    for cid in confirmed:
        entry = out.get(cid) or {"check": cid, "state": "unchecked", "method": None,
                                 "tests": [], "detail": ""}
        if entry.get("state") in ("failing", "missing_test"):
            continue
        entry.update(state="person", detail=f"confirmed by {by}")
        out[cid] = entry
    return out


def counts(ledger: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:  # Any: JSON entries
    """How many checks are in each state."""
    out: dict[str, int] = {}
    for entry in ledger.values():
        state = str(entry.get("state"))
        out[state] = out.get(state, 0) + 1
    return out


def spec_states(ledger: Mapping[str, Mapping[str, Any]]  # Any: JSON entries
                ) -> dict[str, tuple[bool, str]]:
    """check id → (met, evidence) for writing into the spec at acceptance. Unchecked checks
    are left out (their stored state stays as it was)."""
    out: dict[str, tuple[bool, str]] = {}
    for cid, entry in ledger.items():
        state = entry.get("state")
        if state in ("verified", "person", "claimed"):
            out[cid] = (True, str(state))
        elif state in ("failing", "unmet", "missing_test"):
            out[cid] = (False, str(state))
    return out


__all__ = ["CheckInfo", "LedgerEntry", "REPORT_PATH", "TestOutcome", "build_ledger", "confirm",
           "counts", "match_test", "outcomes_from", "parse_junit", "spec_states", "with_report"]
