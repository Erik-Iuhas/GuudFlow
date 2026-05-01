"""Inter-agent protocol — the structured language agents use on the board.

Agents reason in natural language internally. They communicate in protocol
externally. This module defines the structured message types and the
parse/render functions that move between text and dataclasses.

See DESIGN.md section 18 for the full specification.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Literal


FileStatus = Literal["new", "mod", "del"]
VerifyStatus = Literal["PASS", "FAIL"]
ActionVerb = Literal["FIX", "REVISIT", "CLARIFY", "APPROVE", "REJECT", "SKIP"]
SkipOutcome = Literal["shipped", "abandoned", "deferred"]

PROTOCOL_TYPES: frozenset[str] = frozenset({
    "COMMIT", "VERIFY_RESULT", "DELTA", "ACTION", "ASK", "ANSWER", "STATUS",
    "REQUEST_SKIP", "SKIP_GRANTED", "SKIP_DENIED",
    "STORY_OPENED", "STORY_CLOSED",
    # M3.5 — task lifecycle (DESIGN §38.12)
    "ASSIGN_TASK", "TASK_STARTED", "TASK_COMPLETED",
    "TASK_BLOCKED", "TASK_CANCELLED",
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
        if self.status == "FAIL":
            stripped = (self.detail or "").strip()
            if len(stripped) < 20:
                raise ValueError(
                    f"VerifyResult FAIL on {self.checkpoint!r} requires a detail of "
                    f"at least 20 characters (got {len(stripped)})"
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

    @property
    def story_id(self) -> str | None:
        return self.parsed.get("story_id")


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
    *,
    closes: Iterable[str] | None = None,
) -> str:
    """Render a COMMIT protocol message. `closes` (M3.5) is an optional
    list of `task:<id>` references; the runner reads it to bookkeep
    desk completion. Existing callers without `closes` produce
    byte-identical output."""
    lines = [f"COMMIT {agent} {commit_hash}"]
    lines.extend(_file_change_line(fc) for fc in files)
    lines.append("VERIFY: " + "|".join(checkpoints))
    if closes is not None:
        ids = [tid.strip() for tid in closes if tid and tid.strip()]
        if ids:
            lines.append("closes: " + " | ".join(ids))
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


def render_request_skip(
    agent: str,
    skip_id: str,
    reason: str,
    evidence_paths: Iterable[str] = (),
) -> str:
    lines = [f"REQUEST_SKIP {agent} skip:{skip_id}", f"  reason: {reason}"]
    paths = list(evidence_paths)
    if paths:
        lines.append("  evidence: " + ", ".join(paths))
    return "\n".join(lines)


def render_skip_granted(
    skip_id: str,
    granted_by: str,
    note: str = "",
) -> str:
    line = f"SKIP_GRANTED skip:{skip_id} by:{granted_by}"
    return line + (f"\n  {note}" if note else "")


def render_skip_denied(
    skip_id: str,
    denied_by: str,
    redirected_task: str,
) -> str:
    return f"SKIP_DENIED skip:{skip_id} by:{denied_by}\n  redirect: {redirected_task}"


def render_story_opened(
    story_id: str,
    name: str,
    goal: str,
    owner_role: str | None = None,
    parent_story_id: str | None = None,
) -> str:
    lines = [f"STORY_OPENED story:{story_id}", f"  name: {name}", f"  goal: {goal}"]
    if owner_role:
        lines.append(f"  owner: {owner_role}")
    if parent_story_id:
        lines.append(f"  parent: {parent_story_id}")
    return "\n".join(lines)


def render_story_closed(
    story_id: str,
    outcome: str = "shipped",
    summary: str = "",
) -> str:
    lines = [f"STORY_CLOSED story:{story_id} outcome:{outcome}"]
    if summary:
        lines.append(f"  {summary}")
    return "\n".join(lines)


# ── M3.5 — task lifecycle (DESIGN §38.12) ───────────────────────────────────


@dataclass
class TaskRefField:
    """Parser/renderer-internal mirror of `core.workspace.TaskRef`. Lives
    here so `core/protocol.py` doesn't import workspace."""

    kind: str    # "file" | "commit" | "task" | "story"
    value: str
    note: str = ""


def render_assign_task(
    assignee: str,
    task_id: str,
    *,
    goal: str,
    acceptance: Iterable[str] = (),
    out_of_scope: Iterable[str] = (),
    parent_task: str | None = None,
    story: str | None = None,
    references: Iterable[TaskRefField] = (),
) -> str:
    """Render an `ASSIGN_TASK` protocol message. All fields except the
    header line are optional. `acceptance` / `out_of_scope` use `|` as
    separator (matches the existing VERIFY: convention)."""
    lines = [f"ASSIGN_TASK {assignee} task:{task_id}"]
    if goal:
        lines.append(f"  goal: {goal}")
    acc = [a.strip() for a in acceptance if a and a.strip()]
    if acc:
        lines.append("  acceptance: " + " | ".join(acc))
    oos = [o.strip() for o in out_of_scope if o and o.strip()]
    if oos:
        lines.append("  out_of_scope: " + " | ".join(oos))
    if parent_task:
        lines.append(f"  parent_task: {parent_task}")
    if story:
        lines.append(f"  story: {story}")
    refs = list(references)
    if refs:
        lines.append("  refs:")
        for ref in refs:
            note = f' "{ref.note}"' if ref.note else ""
            lines.append(f"    {ref.kind}:{ref.value}{note}")
    return "\n".join(lines)


def render_task_started(task_id: str, by_role: str) -> str:
    return f"TASK_STARTED task:{task_id} by:{by_role}"


def render_task_completed(task_id: str, commit_hash: str) -> str:
    return f"TASK_COMPLETED task:{task_id} commit:{commit_hash}"


def render_task_blocked(
    task_id: str,
    by_role: str,
    *,
    reason: str = "",
    blocked_on: Iterable[str] = (),
) -> str:
    lines = [f"TASK_BLOCKED task:{task_id} by:{by_role}"]
    if reason:
        lines.append(f"  reason: {reason}")
    blockers = [b.strip() for b in blocked_on if b and b.strip()]
    if blockers:
        lines.append("  blocked_on: " + " | ".join(blockers))
    return "\n".join(lines)


def render_task_cancelled(task_id: str, by_role: str, reason: str = "") -> str:
    lines = [f"TASK_CANCELLED task:{task_id} by:{by_role}"]
    if reason:
        lines.append(f"  reason: {reason}")
    return "\n".join(lines)


def render_task_completion_summary(record: dict) -> str:
    """M5.5 C3: render a TaskCompletionRecord dict as a compact desk line.

    Format (D3):
        [task {id} done · commit {sha[:7]}] {summary}
        deliverables:
          - {item}
        refs: {paths}
    """
    task_id = record.get("task_id", "?")
    sha = record.get("closed_by_commit", "")[:7] or "?"
    summary = record.get("summary", "(summary unavailable)")
    deliverables: list[str] = record.get("deliverables", [])
    references: list[str] = record.get("references", [])

    lines = [f"[task {task_id} done · commit {sha}] {summary}"]
    if deliverables:
        lines.append("deliverables:")
        for item in deliverables:
            lines.append(f"  - {item}")
    if references:
        lines.append("refs: " + ", ".join(references[:6]))
    return "\n".join(lines)


# ── Emit helpers (Python-level; auto-generate IDs) ──────────────────────────


def emit_ask(
    from_role: str,
    target_role: str,
    body: str,
    context_paths: list[str] | None = None,
    story_id: str | None = None,
) -> "CompactBlock":
    """Return a CompactBlock representing an ASK.  ask_id is auto-generated."""
    ask_id = uuid.uuid4().hex
    fields: dict[str, str] = {
        "ask_id": ask_id,
        "to": target_role,
        "question": body,
    }
    if context_paths:
        fields["context_paths"] = ",".join(context_paths)
    if story_id:
        fields["story_id"] = story_id
    return CompactBlock(kind="ASK", fields=fields)


def emit_answer(
    to_role: str,
    ask_id: str,
    body: str,
    story_id: str | None = None,
) -> "CompactBlock":
    fields: dict[str, str] = {"to": to_role, "ask_id": ask_id, "answer": body}
    if story_id:
        fields["story_id"] = story_id
    return CompactBlock(kind="ANSWER", fields=fields)


def emit_request_skip(
    reason: str,
    evidence_paths: list[str] | None = None,
    story_id: str | None = None,
) -> "CompactBlock":
    """Return a CompactBlock for REQUEST_SKIP. skip_id auto-generated by runner."""
    fields: dict[str, str] = {"reason": reason}
    if evidence_paths:
        fields["evidence_paths"] = ",".join(evidence_paths)
    if story_id:
        fields["story_id"] = story_id
    return CompactBlock(kind="REQUEST_SKIP", fields=fields)


def emit_skip_granted(
    skip_id: str,
    note: str = "",
    story_id: str | None = None,
) -> "CompactBlock":
    fields: dict[str, str] = {"skip_id": skip_id}
    if note:
        fields["note"] = note
    if story_id:
        fields["story_id"] = story_id
    return CompactBlock(kind="SKIP_GRANTED", fields=fields)


def emit_skip_denied(
    skip_id: str,
    redirected_task: str,
    story_id: str | None = None,
) -> "CompactBlock":
    fields: dict[str, str] = {"skip_id": skip_id, "redirected_task": redirected_task}
    if story_id:
        fields["story_id"] = story_id
    return CompactBlock(kind="SKIP_DENIED", fields=fields)


def emit_story_opened(
    name: str,
    goal: str,
    owner_role: str | None = None,
    parent_story_id: str | None = None,
) -> "CompactBlock":
    story_id = uuid.uuid4().hex
    fields: dict[str, str] = {"story_id": story_id, "name": name, "goal": goal}
    if owner_role:
        fields["owner_role"] = owner_role
    if parent_story_id:
        fields["parent_story_id"] = parent_story_id
    return CompactBlock(kind="STORY_OPENED", fields=fields)


def emit_story_closed(
    story_id: str,
    outcome: str = "shipped",
    summary: str = "",
) -> "CompactBlock":
    fields: dict[str, str] = {"story_id": story_id, "outcome": outcome}
    if summary:
        fields["summary"] = summary
    return CompactBlock(kind="STORY_CLOSED", fields=fields)


# ── Teammate brief & incoming-work renderers ─────────────────────────────────


@dataclass
class TeammateEntry:
    role: str
    persona_name: str
    descriptor: str          # one-liner (goal or first sentence of character)
    is_self: bool = False
    status: str = "PENDING"  # PENDING | DONE | RUNNING | SAT_OUT
    last_commit_subject: str = ""
    escalation_target: str = ""


def render_teammate_brief(self_role: str, teammates: list[TeammateEntry]) -> str:
    """Render the TEAM block injected at Layer 3.5.  Hard-capped at 300 tokens
    worth of content (~240 words); callers must pass pre-filtered entries.
    """
    lines: list[str] = []
    for t in teammates:
        if t.is_self:
            role_label = f"(you are: {t.role} / {t.persona_name} — {t.descriptor})"
            lines.append(f"TEAM {role_label}")
            break

    for t in teammates:
        self_marker = " (you)" if t.is_self else ""
        desc_part = f" — {t.descriptor}" if t.descriptor else ""
        line = f"- {t.role} / {t.persona_name}{self_marker}{desc_part}"
        if t.status == "DONE" and t.last_commit_subject:
            line += f"\n    DONE @ {t.last_commit_subject[:80]}"
        elif t.status == "DONE":
            line += "\n    DONE"
        elif t.status == "SAT_OUT":
            line += "\n    SAT OUT"
        elif t.status == "RUNNING":
            line += "\n    RUNNING"
        if t.is_self and t.escalation_target:
            line += f"\n    escalate to: {t.escalation_target}"
        lines.append(line)

    return "\n".join(lines)


@dataclass
class UpstreamEntry:
    role: str
    persona_name: str
    commit_subject: str
    commit_hash: str
    summary: str
    files: list[str]
    sat_out: bool = False
    sat_out_reason: str = ""
    sat_out_granted_by: str = ""


def render_incoming_work(entries: list[UpstreamEntry]) -> str:
    """Render the INCOMING WORK block injected as a fresh user-turn message
    before each agent iteration.  Returns empty string when no entries.
    """
    if not entries:
        return ""

    blocks: list[str] = []
    for e in sorted(entries, key=lambda x: x.role):
        if e.sat_out:
            block = (
                f"FROM {e.role} ({e.persona_name}) — SAT OUT\n"
                f"  reason: {e.sat_out_reason}\n"
                f"  granted_by: {e.sat_out_granted_by}\n"
                f"  no commits on agent/{e.role}"
            )
        else:
            file_list = ", ".join(e.files[:20]) if e.files else "none"
            block = (
                f"FROM {e.role} ({e.persona_name}) @ commit {e.commit_hash[:8]}:\n"
                f"  subject: {e.commit_subject}\n"
                f"  summary: {e.summary or '(empty)'}\n"
                f"  files: {file_list}"
            )
        blocks.append(block)

    return "=== INCOMING WORK ===\n" + "\n\n".join(blocks) + "\n=== END INCOMING ==="


# ── Parse ───────────────────────────────────────────────────────────────────


_REQUEST_SKIP_HEADER = re.compile(r"^REQUEST_SKIP\s+(\S+)\s+skip:(\S+)\s*$")
_SKIP_GRANTED_HEADER = re.compile(r"^SKIP_GRANTED\s+skip:(\S+)\s+by:(\S+)\s*$")
_SKIP_DENIED_HEADER = re.compile(r"^SKIP_DENIED\s+skip:(\S+)\s+by:(\S+)\s*$")
_STORY_OPENED_HEADER = re.compile(r"^STORY_OPENED\s+story:(\S+)\s*$")
_STORY_CLOSED_HEADER = re.compile(r"^STORY_CLOSED\s+story:(\S+)\s+outcome:(\S+)\s*$")
_ASSIGN_TASK_HEADER = re.compile(r"^ASSIGN_TASK\s+(\S+)\s+task:(\S+)\s*$")
_TASK_STARTED_HEADER = re.compile(r"^TASK_STARTED\s+task:(\S+)\s+by:(\S+)\s*$")
_TASK_COMPLETED_HEADER = re.compile(r"^TASK_COMPLETED\s+task:(\S+)\s+commit:(\S+)\s*$")
_TASK_BLOCKED_HEADER = re.compile(r"^TASK_BLOCKED\s+task:(\S+)\s+by:(\S+)\s*$")
_TASK_CANCELLED_HEADER = re.compile(r"^TASK_CANCELLED\s+task:(\S+)\s+by:(\S+)\s*$")
_TASK_REF_LINE = re.compile(
    r'^(file|commit|task|story):(\S+?)(?:\s+"([^"]*)")?\s*$'
)

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
    closes: list[str] = []
    for line in lines[1:]:
        if line.startswith("VERIFY:"):
            cks = line[len("VERIFY:"):].strip()
            checkpoints = [c.strip() for c in cks.split("|") if c.strip()]
            continue
        # M3.5 — optional `closes: task:<id> | task:<id>` trailer.
        stripped = line.lstrip()
        if stripped.startswith("closes:"):
            payload = stripped[len("closes:"):].strip()
            closes = [tid.strip() for tid in payload.split("|") if tid.strip()]
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

    parsed: dict = {"files": files, "checkpoints": checkpoints}
    if closes:
        parsed["closes"] = closes
    return ProtocolMessage(
        type="COMMIT",
        from_agent=agent,
        commit_hash=commit_hash,
        raw=raw,
        parsed=parsed,
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


def _parse_request_skip(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _REQUEST_SKIP_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed REQUEST_SKIP header: {lines[0]!r}")
    agent, skip_id = m.groups()
    reason = ""
    evidence: list[str] = []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("reason:"):
            reason = s[len("reason:"):].strip()
        elif s.startswith("evidence:"):
            evidence = [p.strip() for p in s[len("evidence:"):].split(",") if p.strip()]
    return ProtocolMessage(
        type="REQUEST_SKIP",
        from_agent=agent,
        commit_hash=None,
        raw=raw,
        parsed={"skip_id": skip_id, "reason": reason, "evidence_paths": evidence},
    )


def _parse_skip_granted(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _SKIP_GRANTED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed SKIP_GRANTED header: {lines[0]!r}")
    skip_id, granted_by = m.groups()
    note = lines[1].strip() if len(lines) >= 2 else ""
    return ProtocolMessage(
        type="SKIP_GRANTED",
        from_agent=granted_by,
        commit_hash=None,
        raw=raw,
        parsed={"skip_id": skip_id, "granted_by": granted_by, "note": note},
    )


def _parse_skip_denied(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _SKIP_DENIED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed SKIP_DENIED header: {lines[0]!r}")
    skip_id, denied_by = m.groups()
    redirected_task = ""
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("redirect:"):
            redirected_task = s[len("redirect:"):].strip()
    return ProtocolMessage(
        type="SKIP_DENIED",
        from_agent=denied_by,
        commit_hash=None,
        raw=raw,
        parsed={"skip_id": skip_id, "denied_by": denied_by, "redirected_task": redirected_task},
    )


def _parse_story_opened(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _STORY_OPENED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed STORY_OPENED header: {lines[0]!r}")
    story_id = m.group(1)
    name = goal = owner_role = parent_story_id = ""
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("name:"):
            name = s[5:].strip()
        elif s.startswith("goal:"):
            goal = s[5:].strip()
        elif s.startswith("owner:"):
            owner_role = s[6:].strip()
        elif s.startswith("parent:"):
            parent_story_id = s[7:].strip()
    return ProtocolMessage(
        type="STORY_OPENED",
        from_agent=None,
        commit_hash=None,
        raw=raw,
        parsed={
            "story_id": story_id,
            "name": name,
            "goal": goal,
            "owner_role": owner_role or None,
            "parent_story_id": parent_story_id or None,
        },
    )


def _parse_story_closed(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _STORY_CLOSED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed STORY_CLOSED header: {lines[0]!r}")
    story_id, outcome = m.groups()
    summary = lines[1].strip() if len(lines) >= 2 else ""
    return ProtocolMessage(
        type="STORY_CLOSED",
        from_agent=None,
        commit_hash=None,
        raw=raw,
        parsed={"story_id": story_id, "outcome": outcome, "summary": summary},
    )


def _parse_assign_task(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _ASSIGN_TASK_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed ASSIGN_TASK header: {lines[0]!r}")
    assignee, task_id = m.groups()
    goal = ""
    acceptance: list[str] = []
    out_of_scope: list[str] = []
    parent_task: str | None = None
    story_id: str | None = None
    references: list[TaskRefField] = []
    in_refs = False
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("goal:"):
            goal = s[5:].strip()
            in_refs = False
        elif s.startswith("acceptance:"):
            acceptance = [a.strip() for a in s[11:].split("|") if a.strip()]
            in_refs = False
        elif s.startswith("out_of_scope:"):
            out_of_scope = [o.strip() for o in s[13:].split("|") if o.strip()]
            in_refs = False
        elif s.startswith("parent_task:"):
            parent_task = s[12:].strip() or None
            in_refs = False
        elif s.startswith("story:"):
            story_id = s[6:].strip() or None
            in_refs = False
        elif s.startswith("refs:"):
            in_refs = True
            tail = s[5:].strip()
            if tail:
                rm = _TASK_REF_LINE.match(tail)
                if rm:
                    kind, value, note = rm.groups()
                    references.append(TaskRefField(kind, value, note or ""))
        elif in_refs:
            rm = _TASK_REF_LINE.match(s)
            if not rm:
                raise ValueError(f"malformed task ref line: {line!r}")
            kind, value, note = rm.groups()
            references.append(TaskRefField(kind, value, note or ""))
        else:
            raise ValueError(f"unrecognised ASSIGN_TASK line: {line!r}")
    return ProtocolMessage(
        type="ASSIGN_TASK",
        from_agent=None,
        commit_hash=None,
        raw=raw,
        parsed={
            "assignee": assignee,
            "task_id": task_id,
            "goal": goal,
            "acceptance": acceptance,
            "out_of_scope": out_of_scope,
            "parent_task": parent_task,
            "story_id": story_id,
            "references": references,
        },
    )


def _parse_task_started(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _TASK_STARTED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed TASK_STARTED header: {lines[0]!r}")
    task_id, by_role = m.groups()
    return ProtocolMessage(
        type="TASK_STARTED",
        from_agent=by_role,
        commit_hash=None,
        raw=raw,
        parsed={"task_id": task_id, "by_role": by_role},
    )


def _parse_task_completed(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _TASK_COMPLETED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed TASK_COMPLETED header: {lines[0]!r}")
    task_id, commit_hash = m.groups()
    return ProtocolMessage(
        type="TASK_COMPLETED",
        from_agent=None,
        commit_hash=commit_hash,
        raw=raw,
        parsed={"task_id": task_id, "commit_hash": commit_hash},
    )


def _parse_task_blocked(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _TASK_BLOCKED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed TASK_BLOCKED header: {lines[0]!r}")
    task_id, by_role = m.groups()
    reason = ""
    blocked_on: list[str] = []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("reason:"):
            reason = s[7:].strip()
        elif s.startswith("blocked_on:"):
            blocked_on = [b.strip() for b in s[11:].split("|") if b.strip()]
    return ProtocolMessage(
        type="TASK_BLOCKED",
        from_agent=by_role,
        commit_hash=None,
        raw=raw,
        parsed={
            "task_id": task_id,
            "by_role": by_role,
            "reason": reason,
            "blocked_on": blocked_on,
        },
    )


def _parse_task_cancelled(raw: str, lines: list[str]) -> ProtocolMessage:
    m = _TASK_CANCELLED_HEADER.match(lines[0])
    if not m:
        raise ValueError(f"malformed TASK_CANCELLED header: {lines[0]!r}")
    task_id, by_role = m.groups()
    reason = ""
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("reason:"):
            reason = s[7:].strip()
    return ProtocolMessage(
        type="TASK_CANCELLED",
        from_agent=by_role,
        commit_hash=None,
        raw=raw,
        parsed={"task_id": task_id, "by_role": by_role, "reason": reason},
    )


_DISPATCH = {
    "COMMIT": _parse_commit,
    "VERIFY_RESULT": _parse_verify,
    "DELTA": _parse_delta,
    "ACTION": _parse_action,
    "ASK": _parse_ask,
    "ANSWER": _parse_answer,
    "STATUS": _parse_status,
    "REQUEST_SKIP": _parse_request_skip,
    "SKIP_GRANTED": _parse_skip_granted,
    "SKIP_DENIED": _parse_skip_denied,
    "STORY_OPENED": _parse_story_opened,
    "STORY_CLOSED": _parse_story_closed,
    "ASSIGN_TASK": _parse_assign_task,
    "TASK_STARTED": _parse_task_started,
    "TASK_COMPLETED": _parse_task_completed,
    "TASK_BLOCKED": _parse_task_blocked,
    "TASK_CANCELLED": _parse_task_cancelled,
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


# ── Compact end-of-turn block (DESIGN §18 — agent-facing form) ─────────────
#
# Distinct from the full ProtocolMessage above: this is the short structured
# block an agent emits at the end of its LLM turn, before the runner has a
# commit hash. The runner extracts it from the LLM response, stores the
# rendered form as the artifact, and (for reviewers) drives the
# APPROVED/REJECTED signal off `result`. Verbose reasoning above the block
# is NOT posted to the board.

CompactKind = Literal[
    "COMMIT", "VERIFY", "ACTION", "ASK", "ANSWER", "STATUS",
    "REQUEST_SKIP", "SKIP_GRANTED", "SKIP_DENIED",
    "STORY_OPENED", "STORY_CLOSED",
]

_COMPACT_KINDS: frozenset[str] = frozenset({
    "COMMIT", "VERIFY", "ACTION", "ASK", "ANSWER", "STATUS",
    "REQUEST_SKIP", "SKIP_GRANTED", "SKIP_DENIED",
    "STORY_OPENED", "STORY_CLOSED",
})


@dataclass
class CompactBlock:
    """End-of-turn structured summary the LLM produces."""

    kind: str
    fields: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _COMPACT_KINDS:
            raise ValueError(f"unknown compact block kind {self.kind!r}")
        if self.kind == "VERIFY":
            result = self.fields.get("result", "").upper()
            if result not in ("PASS", "FAIL"):
                raise ValueError("VERIFY block requires result: PASS|FAIL")
            if result == "FAIL" and not self.fields.get("detail", "").strip():
                raise ValueError("VERIFY FAIL requires a specific detail:")
            self.fields["result"] = result
        if self.kind == "COMMIT" and not self.fields.get("summary", "").strip():
            raise ValueError("COMMIT block requires summary:")
        if self.kind == "ACTION":
            for required in ("target", "verb", "subject"):
                if not self.fields.get(required, "").strip():
                    raise ValueError(f"ACTION block requires {required}:")
        if self.kind == "ASK":
            for required in ("to", "question"):
                if not self.fields.get(required, "").strip():
                    raise ValueError(f"ASK block requires {required}:")
        if self.kind == "ANSWER":
            # ask_id is required so the runner knows which ASK to close.
            for required in ("to", "answer", "ask_id"):
                if not self.fields.get(required, "").strip():
                    raise ValueError(f"ANSWER block requires {required}:")
        if self.kind == "STATUS" and not self.fields.get("description", "").strip():
            raise ValueError("STATUS block requires description:")
        if self.kind == "REQUEST_SKIP":
            if not self.fields.get("reason", "").strip():
                raise ValueError("REQUEST_SKIP block requires reason:")
        if self.kind == "SKIP_GRANTED":
            if not self.fields.get("skip_id", "").strip():
                raise ValueError("SKIP_GRANTED block requires skip_id:")
        if self.kind == "SKIP_DENIED":
            for required in ("skip_id", "redirected_task"):
                if not self.fields.get(required, "").strip():
                    raise ValueError(f"SKIP_DENIED block requires {required}:")
        if self.kind == "STORY_OPENED":
            for required in ("story_id", "name", "goal"):
                if not self.fields.get(required, "").strip():
                    raise ValueError(f"STORY_OPENED block requires {required}:")
        if self.kind == "STORY_CLOSED":
            if not self.fields.get("story_id", "").strip():
                raise ValueError("STORY_CLOSED block requires story_id:")


_COMPACT_HEADER = re.compile(
    r"^\[(COMMIT|VERIFY|ACTION|ASK|ANSWER|STATUS"
    r"|REQUEST_SKIP|SKIP_GRANTED|SKIP_DENIED|STORY_OPENED|STORY_CLOSED)\]\s*(.*)$"
)
_COMPACT_FIELD = re.compile(r"^([a-z][a-z_]*)\s*:\s*(.*)$")


def extract_trailing(text: str) -> CompactBlock | None:
    """Find the last `[KIND]`-headed compact block in `text` and parse it.

    Returns None if no recognisable block is present at the tail. Anything
    before the block is treated as internal reasoning and discarded.
    Raises ValueError if the trailing block is malformed (caller decides
    whether to retry or fail).
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    header_idx: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        if _COMPACT_HEADER.match(lines[i].strip()):
            header_idx = i
            break
    if header_idx is None:
        return None

    block_lines = [ln for ln in lines[header_idx:] if ln.strip()]
    head_match = _COMPACT_HEADER.match(block_lines[0].strip())
    assert head_match is not None  # guaranteed by the loop above
    kind = head_match.group(1)
    inline = head_match.group(2).strip()

    fields: dict[str, str] = {}
    if kind == "VERIFY" and inline:
        fields["result"] = inline.split()[0].upper()

    for line in block_lines[1:]:
        m = _COMPACT_FIELD.match(line.strip())
        if not m:
            raise ValueError(f"malformed compact block line: {line!r}")
        fields[m.group(1)] = m.group(2).strip()

    return CompactBlock(kind=kind, fields=fields)


def render_compact(block: CompactBlock) -> str:
    """Render a CompactBlock to its canonical short text form for storage."""
    head = f"[{block.kind}]"
    if block.kind == "VERIFY":
        head = f"[VERIFY] {block.fields['result']}"
    body_keys = [k for k in block.fields if not (block.kind == "VERIFY" and k == "result")]
    body = "\n".join(f"  {k}: {block.fields[k]}" for k in body_keys)
    return head if not body else f"{head}\n{body}"


# ── M5: Rejected-feedback context block ───────────────────────────────────


@dataclass(slots=True)
class RejectedFeedback:
    """In-memory record of a reviewer's rejection; injected into the next
    iteration context for the rejected author. Not persisted — rebuilt from
    board signals on resume."""

    reviewer_role: str
    reviewer_name: str    # persona display name, or role.capitalize()
    detail: str           # truncated to 1000 chars
    attempt_number: int   # 1-indexed


_REJECTED_FEEDBACK_MAX_DETAIL = 1000


def render_rejected_feedback(
    reviewer_name: str,
    reviewer_role: str,
    detail: str,
    attempt_number: int,
) -> str:
    """Render the RejectedFeedback context block prepended to the next
    iteration's task prompt so the author sees exactly what to fix.

    Format is fixed (D5 in M5 spec) — do not alter the delimiters.
    """
    if len(detail) > _REJECTED_FEEDBACK_MAX_DETAIL:
        detail = detail[:_REJECTED_FEEDBACK_MAX_DETAIL] + "…(truncated)"
    return (
        f"=== REVIEWER REJECTED YOUR WORK (attempt {attempt_number}) ===\n"
        f"Reviewer: {reviewer_name} ({reviewer_role})\n"
        f"\n"
        f"Reason:\n"
        f"{detail}\n"
        f"\n"
        f"--- Address each point above. Re-emit COMMIT when fixed. ---"
    )
