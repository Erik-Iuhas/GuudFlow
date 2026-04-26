"""Inter-agent protocol — the structured language agents use on the board.

Agents reason in natural language internally. They communicate in protocol
externally. This module defines the structured message types and the
parse/render functions that move between text and dataclasses.

See DESIGN.md section 18 for the full specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal


FileStatus = Literal["new", "mod", "del"]
VerifyStatus = Literal["PASS", "FAIL"]
ActionVerb = Literal["FIX", "REVISIT", "CLARIFY", "APPROVE", "REJECT", "SKIP"]

PROTOCOL_TYPES: frozenset[str] = frozenset({
    "COMMIT", "VERIFY_RESULT", "DELTA", "ACTION", "ASK", "ANSWER", "STATUS",
})


@dataclass
class FileChange:
    path: str
    status: FileStatus
    description: str = ""
    lines_changed: int | None = None

    def __post_init__(self) -> None:
        if self.status not in ("new", "mod", "del"):
            raise ValueError(f"status must be new|mod|del, got {self.status!r}")
        if self.status == "mod" and self.lines_changed is None:
            raise ValueError("mod FileChange requires lines_changed")


@dataclass
class VerifyResult:
    checkpoint: str
    status: VerifyStatus
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("PASS", "FAIL"):
            raise ValueError(f"status must be PASS|FAIL, got {self.status!r}")
        if self.status == "FAIL" and not (self.detail and self.detail.strip()):
            raise ValueError(
                f"VerifyResult FAIL on {self.checkpoint!r} requires a specific detail"
            )


@dataclass
class ProtocolMessage:
    """Parsed protocol message. `raw` is the canonical text form, `parsed`
    holds the typed fields keyed by name. The exact keys depend on `type`.
    """

    type: str
    from_agent: str | None
    commit_hash: str | None
    raw: str
    parsed: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in PROTOCOL_TYPES:
            raise ValueError(f"unknown protocol type {self.type!r}")


# ── Render ──────────────────────────────────────────────────────────────────


def _file_change_line(fc: FileChange) -> str:
    if fc.status == "new":
        tag = "[new]"
    elif fc.status == "del":
        tag = "[del]"
    else:
        tag = f"[mod:{fc.lines_changed}]"
    sym = {"new": "+", "mod": "~", "del": "-"}[fc.status]
    desc = f"  {fc.description}" if fc.description else ""
    return f"  {sym} {fc.path}  {tag}{desc}"


def render_commit(
    agent: str,
    commit_hash: str,
    files: Iterable[FileChange],
    checkpoints: Iterable[str],
) -> str:
    lines = [f"COMMIT {agent} {commit_hash}"]
    lines.extend(_file_change_line(fc) for fc in files)
    lines.append("VERIFY: " + "|".join(checkpoints))
    return "\n".join(lines)


def render_verify(
    commit_hash: str,
    results: Iterable[VerifyResult],
    action: tuple[str, ActionVerb, str] | None = None,
) -> str:
    lines = [f"VERIFY_RESULT {commit_hash}"]
    for r in results:
        if r.status == "PASS":
            lines.append(f"  {r.checkpoint}  PASS")
        else:
            lines.append(f"  {r.checkpoint}  FAIL  {r.detail}")
    if action is not None:
        target, verb, subject = action
        lines.append(f"ACTION: {target} {verb} {subject}")
    return "\n".join(lines)


def render_delta(
    agent: str,
    iteration: int,
    from_hash: str,
    to_hash: str,
    changes: Iterable[str],
    addressed: Iterable[str] = (),
) -> str:
    lines = [f"DELTA {agent} iter:{iteration} from:{from_hash} to:{to_hash}"]
    lines.extend(f"  ~ {c}" for c in changes)
    lines.extend(f"  ! {a}" for a in addressed)
    return "\n".join(lines)


def render_action(target_role: str, verb: ActionVerb, subject: str) -> str:
    if verb not in ("FIX", "REVISIT", "CLARIFY", "APPROVE", "REJECT", "SKIP"):
        raise ValueError(f"invalid action verb {verb!r}")
    return f"ACTION {target_role} {verb} {subject}"


def render_ask(
    question_id: str,
    from_role: str,
    to_role: str,
    question: str,
    context: str | None = None,
) -> str:
    lines = [f"ASK {question_id} {from_role} → {to_role}", f"  {question}"]
    if context:
        lines.append(f"  CTX: {context}")
    return "\n".join(lines)


def render_answer(
    question_id: str,
    to_role: str,
    from_role: str,
    answer: str,
) -> str:
    return f"ANSWER {question_id} {to_role} → {from_role}\n  {answer}"


def render_status(agent: str, state: str, iteration: int, description: str) -> str:
    return f"STATUS {agent} {state} iter:{iteration}\n  {description}"


# ── Parse ───────────────────────────────────────────────────────────────────


_FILE_LINE = re.compile(
    r"^\s*([+~\-])\s+(\S+)\s+\[(new|del|mod:(\d+))\](?:\s{1,}(.*))?\s*$"
)
_DELTA_HEADER = re.compile(
    r"^DELTA\s+(\S+)\s+iter:(\d+)\s+from:(\S+)\s+to:(\S+)\s*$"
)
_ASK_HEADER = re.compile(r"^ASK\s+(\S+)\s+(\S+)\s+(?:→|->)\s+(\S+)\s*$")
_ANSWER_HEADER = re.compile(r"^ANSWER\s+(\S+)\s+(\S+)\s+(?:→|->)\s+(\S+)\s*$")
_STATUS_HEADER = re.compile(r"^STATUS\s+(\S+)\s+(\S+)\s+iter:(\d+)\s*$")
_ACTION_TRAILER = re.compile(r"^ACTION:\s+(\S+)\s+(\S+)\s+(.+?)\s*$")
_ACTION_LINE = re.compile(r"^ACTION\s+(\S+)\s+(\S+)\s+(.+?)\s*$")


def _strip_blank(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.strip()]


def _parse_commit(raw: str, lines: list[str]) -> ProtocolMessage:
    head = lines[0].split()
    if len(head) != 3:
        raise ValueError(f"malformed COMMIT header: {lines[0]!r}")
    _, agent, commit_hash = head

    files: list[FileChange] = []
    checkpoints: list[str] = []
    for line in lines[1:]:
        if line.startswith("VERIFY:"):
            cks = line[len("VERIFY:"):].strip()
            checkpoints = [c.strip() for c in cks.split("|") if c.strip()]
            continue
        m = _FILE_LINE.match(line)
        if not m:
            raise ValueError(f"malformed COMMIT file line: {line!r}")
        sym, path, tag, mod_n, desc = m.groups()
        if tag == "new":
            status: FileStatus = "new"
            lines_changed = None
        elif tag == "del":
            status = "del"
            lines_changed = None
        else:
            status = "mod"
            lines_changed = int(mod_n)
        files.append(FileChange(
            path=path,
            status=status,
            description=(desc or "").strip(),
            lines_changed=lines_changed,
        ))

    if not checkpoints:
        raise ValueError("COMMIT message missing VERIFY: line")

    return ProtocolMessage(
        type="COMMIT",
        from_agent=agent,
        commit_hash=commit_hash,
        raw=raw,
        parsed={"files": files, "checkpoints": checkpoints},
    )


def _parse_verify(raw: str, lines: list[str]) -> ProtocolMessage:
    head = lines[0].split()
    if len(head) != 2:
        raise ValueError(f"malformed VERIFY_RESULT header: {lines[0]!r}")
    commit_hash = head[1]

    results: list[VerifyResult] = []
    action: tuple[str, str, str] | None = None
    for line in lines[1:]:
        m = _ACTION_TRAILER.match(line)
        if m:
            action = (m.group(1), m.group(2), m.group(3))
            continue
        body = line.strip()
        # "checkpoint  PASS" or "checkpoint  FAIL  detail"
        if " FAIL " in body or body.endswith(" FAIL"):
            ck, _, rest = body.partition(" FAIL")
            detail = rest.strip() or None
            results.append(VerifyResult(
                checkpoint=ck.strip(),
                status="FAIL",
                detail=detail,
            ))
        elif body.endswith(" PASS"):
            ck = body[: -len(" PASS")].strip()
            results.append(VerifyResult(checkpoint=ck, status="PASS"))
        else:
            raise ValueError(f"malformed VERIFY_RESULT line: {line!r}")

    parsed: dict = {"results": results}
    if action is not None:
        parsed["action"] = {
            "target_role": action[0],
            "verb": action[1],
            "subject": action[2],
        }
    return ProtocolMessage(
        type="VERIFY_RESULT",
        from_agent=None,
        commit_hash=commit_hash,
        raw=raw,
        parsed=parsed,
    )


def _parse_delta(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _DELTA_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed DELTA header: {lines[0]!r}")
    agent, iteration, from_hash, to_hash = m.groups()

    changes: list[str] = []
    addressed: list[str] = []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("~ "):
            changes.append(s[2:].strip())
        elif s.startswith("! "):
            addressed.append(s[2:].strip())
        else:
            raise ValueError(f"malformed DELTA body line: {line!r}")

    return ProtocolMessage(
        type="DELTA",
        from_agent=agent,
        commit_hash=to_hash,
        raw=raw,
        parsed={
            "iteration": int(iteration),
            "from_hash": from_hash,
            "to_hash": to_hash,
            "changes": changes,
            "addressed": addressed,
        },
    )


def _parse_action(raw: str, lines: list[str]) -> ProtocolMessage:
    if len(lines) != 1:
        raise ValueError("ACTION message must be a single line")
    m = _ACTION_LINE.match(lines[0])
    if not m:
        raise ValueError(f"malformed ACTION: {lines[0]!r}")
    target, verb, subject = m.groups()
    return ProtocolMessage(
        type="ACTION",
        from_agent=None,
        commit_hash=None,
        raw=raw,
        parsed={"target_role": target, "verb": verb, "subject": subject.strip()},
    )


def _parse_ask(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _ASK_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed ASK header: {lines[0]!r}")
    qid, from_role, to_role = m.groups()
    if len(lines) < 2:
        raise ValueError("ASK message missing question body")
    question = lines[1].strip()
    context: str | None = None
    if len(lines) >= 3:
        ctx_line = lines[2].strip()
        if not ctx_line.startswith("CTX:"):
            raise ValueError(f"expected CTX: line, got {ctx_line!r}")
        context = ctx_line[len("CTX:"):].strip()
    return ProtocolMessage(
        type="ASK",
        from_agent=from_role,
        commit_hash=None,
        raw=raw,
        parsed={
            "question_id": qid,
            "from_role": from_role,
            "to_role": to_role,
            "question": question,
            "context": context,
        },
    )


def _parse_answer(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _ANSWER_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed ANSWER header: {lines[0]!r}")
    qid, to_role, from_role = m.groups()
    if len(lines) < 2:
        raise ValueError("ANSWER message missing body")
    answer = lines[1].strip()
    return ProtocolMessage(
        type="ANSWER",
        from_agent=to_role,
        commit_hash=None,
        raw=raw,
        parsed={
            "question_id": qid,
            "to_role": to_role,
            "from_role": from_role,
            "answer": answer,
        },
    )


def _parse_status(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _STATUS_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed STATUS header: {lines[0]!r}")
    agent, state, iteration = m.groups()
    description = lines[1].strip() if len(lines) >= 2 else ""
    return ProtocolMessage(
        type="STATUS",
        from_agent=agent,
        commit_hash=None,
        raw=raw,
        parsed={
            "state": state,
            "iteration": int(iteration),
            "description": description,
        },
    )


_DISPATCH = {
    "COMMIT": _parse_commit,
    "VERIFY_RESULT": _parse_verify,
    "DELTA": _parse_delta,
    "ACTION": _parse_action,
    "ASK": _parse_ask,
    "ANSWER": _parse_answer,
    "STATUS": _parse_status,
}


def parse(raw: str) -> ProtocolMessage:
    """Parse a raw protocol message string into a ProtocolMessage.

    Raises ValueError on malformed input. The parser is strict — it will not
    silently accept unknown types or skip malformed lines.
    """
    if not raw or not raw.strip():
        raise ValueError("cannot parse empty protocol message")

    lines = _strip_blank(raw.splitlines())
    if not lines:
        raise ValueError("cannot parse protocol message with no content")

    head = lines[0].split(maxsplit=1)
    msg_type = head[0]
    if msg_type not in _DISPATCH:
        raise ValueError(f"unknown protocol message type {msg_type!r}")

    return _DISPATCH[msg_type](raw, lines)
