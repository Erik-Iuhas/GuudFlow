"""Memory evidence from opencode harness runs (S13).

The legacy memory_writer builds episodes from the in-memory workspace. An
opencode run has no per-agent turn log of that kind (plan gotcha #10: opencode's
message list is unusable once `format` is used), so a role's transcript is
rebuilt from what the run wrote to disk:

  * `{run_root}/board.jsonl`  — AGENT_RESULT, TOOL_CALL, REJECTED, BLOCKED, FAILED
    signals (docs/OPENCODE_HARNESS_PLAN.md §2.1a);
  * `{run_root}/tasks.jsonl`  — card snapshots with their threads (§4.6; the last
    snapshot of each card wins);
  * `{run_root}/llm_costs.jsonl` — per-call token and cost rows.

Everything here tolerates missing files and malformed lines: bad input is skipped,
never raised. Free text written by agents (summaries, notes, review issues) is
untrusted; `render_transcript_for_prompt` delimits it with `wrap_untrusted`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.harness.brief import wrap_untrusted

# Signal authors that are infrastructure, not roles.
_NON_ROLES = frozenset({"git", "runner", "scheduler", "system", "memory_writer"})
_DONE_COLUMN = "done"
_ATTENTION_COLUMNS = frozenset({"needs_attention"})
_BLOCKED_COLUMNS = frozenset({"blocked"})


@dataclass(frozen=True)
class IssueRecord:
    """One review issue a role received, with where and when (for recurrence)."""

    card_id: str
    round: int
    text: str
    key: str  # what makes two issues "the same" (acceptance item, else normalised text)


@dataclass
class HarnessTranscript:
    """Everything one role did in an opencode run, rebuilt from the run's files."""

    role: str
    results: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    review_issues: list[str] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    blocked_on: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)
    # Extras that back lessons with evidence.
    issue_records: list[IssueRecord] = field(default_factory=list)
    rejections: int = 0
    commits: int = 0
    files: list[str] = field(default_factory=list)
    model: str = ""
    result_summaries: list[str] = field(default_factory=list)


@dataclass
class RoleEvidence:
    """What the memory writer records for one role of an opencode run."""

    outcome: str  # clean | passed_after_rework | blocked | failed | incomplete | completed | no_activity
    what_i_tried: str = ""
    what_worked: str = ""
    what_failed: str = ""
    feedback_verbatim: str = ""
    iterations_to_resolve: int = 0
    confidence: str = "medium"
    extra_tags: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)


# ── Loading ─────────────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file; missing file, unreadable file and bad lines yield nothing."""
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _detail_json(sig: dict[str, Any]) -> dict[str, Any] | None:
    """The signal's detail as a dict when it is a JSON object, else None."""
    detail = sig.get("detail")
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_harness_transcripts(run_root: Path) -> dict[str, HarnessTranscript]:
    """Build one `HarnessTranscript` per role from a run directory.

    Reads board.jsonl, tasks.jsonl and llm_costs.jsonl under `run_root`. Missing
    files give an empty result; malformed lines are skipped.
    """
    run_root = Path(run_root)
    transcripts: dict[str, HarnessTranscript] = {}

    def get(role: str) -> HarnessTranscript:
        if role not in transcripts:
            transcripts[role] = HarnessTranscript(role=role)
        return transcripts[role]

    board = _read_jsonl(run_root / "board.jsonl")
    cards = _latest_cards(_read_jsonl(run_root / "tasks.jsonl"))
    card_owner = {cid: str(c.get("assigned_to") or "") for cid, c in cards.items()}
    result_costs: dict[str, tuple[float, dict[str, int]]] = {}
    rejected: list[dict[str, Any]] = []
    board_calls: dict[str, list[tuple[str, str]]] = {}

    for sig in board:
        stype = str(sig.get("type") or "")
        role = str(sig.get("from_agent") or "")
        if stype == "REJECTED":
            rejected.append(sig)
            continue
        if not role or role in _NON_ROLES:
            continue
        detail = _detail_json(sig)
        if stype == "AGENT_RESULT":
            t = get(role)
            if detail is None:
                continue
            result = detail.get("result")
            if not isinstance(result, dict):
                continue
            t.results.append(result)
            summary = result.get("summary")
            if isinstance(summary, str) and summary:
                t.result_summaries.append(summary)
            for call in detail.get("tool_calls") or []:
                if isinstance(call, (list, tuple)) and len(call) >= 2:
                    t.tool_calls.append((str(call[0]), str(call[1])))
            commits = detail.get("commits")
            if isinstance(commits, list):
                t.commits += len(commits)
            for f in detail.get("files") or []:
                if isinstance(f, str) and f not in t.files:
                    t.files.append(f)
            cost, toks = result_costs.get(role, (0.0, {}))
            cost += _float(detail.get("cost"))
            raw_tokens = detail.get("tokens")
            if isinstance(raw_tokens, dict):
                for k, v in raw_tokens.items():
                    toks[str(k)] = toks.get(str(k), 0) + _int(v)
            result_costs[role] = (cost, toks)
        elif stype == "TOOL_CALL" and detail is not None:
            # AGENT_RESULT carries the same list per turn; TOOL_CALL rows are
            # only used when no AGENT_RESULT lists exist for the role (below).
            tool = detail.get("tool")
            if isinstance(tool, str):
                get(role)
                board_calls.setdefault(role, []).append((tool, str(detail.get("status") or "")))
        elif stype == "BLOCKED":
            t = get(role)
            if detail is not None:
                on, need = str(detail.get("on") or ""), str(detail.get("need") or "")
                card = str(detail.get("task_id") or "")
                t.blocked_on.append(f"{card + ': ' if card else ''}{on}: {need}".strip(": "))
            else:
                t.blocked_on.append(str(sig.get("detail") or sig.get("summary") or ""))
        elif stype == "FAILED":
            t = get(role)
            text = str(sig.get("summary") or "")
            extra = sig.get("detail")
            if isinstance(extra, str) and extra:
                text = f"{text} — {extra}"
            t.failures.append(text)

    for role, calls in board_calls.items():
        if not transcripts[role].tool_calls:  # AGENT_RESULT lists win when present
            transcripts[role].tool_calls = calls

    # Cards and the issues their owners received.
    for cid, card in cards.items():
        owner = card_owner.get(cid, "")
        if not owner or owner in _NON_ROLES:
            continue
        t = get(owner)
        t.cards.append({
            "id": cid,
            "title": str(card.get("title") or card.get("goal") or ""),
            "column": str(card.get("column") or ""),
            "review_round": _int(card.get("review_round")),
            "acceptance": [str(a) for a in card.get("acceptance_criteria") or []],
        })
        seen_keys: dict[str, int] = {}
        for entry in card.get("thread") or []:
            if not isinstance(entry, dict) or entry.get("kind") != "review_issue":
                continue
            text = str(entry.get("text") or "")
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            key = _norm(str(data.get("item") or text))
            seen_keys[key] = seen_keys.get(key, 0) + 1
            t.issue_records.append(IssueRecord(cid, seen_keys[key], text, key))

    # REJECTED signals: authors learn which cards bounced and (legacy) why.
    cards_with_thread_issues = {r.card_id for t in transcripts.values() for r in t.issue_records}
    for sig in rejected:
        detail = _detail_json(sig) or {}
        authors = detail.get("authors")
        if not isinstance(authors, list):
            authors = [str(a) for a in [sig.get("from_agent")] if a]
        card_id = str(detail.get("task_id") or "")
        rnd = _int(detail.get("round"))
        issues = detail.get("issues")
        texts: list[str] = []
        if isinstance(issues, list):
            for i in issues:
                if isinstance(i, dict):
                    f, d = str(i.get("file") or ""), str(i.get("detail") or "")
                    texts.append(f"{f}: {d}" if f and d else (d or f))
                elif isinstance(i, str):
                    texts.append(i)
        elif not detail and isinstance(sig.get("detail"), str) and sig.get("detail"):
            texts.append(str(sig["detail"]))
        for author in authors:
            author = str(author)
            if author in _NON_ROLES:
                continue
            t = get(author)
            t.rejections += 1
            if card_id and card_id in cards_with_thread_issues:
                continue  # the card thread already carries these, with the item
            for text in texts:
                t.issue_records.append(IssueRecord(card_id, rnd, text, _norm(text)))

    for t in transcripts.values():
        for rec in t.issue_records:
            if rec.text and rec.text not in t.review_issues:
                t.review_issues.append(rec.text)

    _apply_costs(transcripts, _read_jsonl(run_root / "llm_costs.jsonl"), result_costs)
    return transcripts


def _latest_cards(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """tasks.jsonl is a snapshot log; the last line per task id is the current card."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("task_id")
        if isinstance(cid, str) and cid:
            latest[cid] = row
    return latest


def _apply_costs(
    transcripts: dict[str, HarnessTranscript],
    rows: list[dict[str, Any]],
    result_costs: dict[str, tuple[float, dict[str, int]]],
) -> None:
    """Cost and tokens: llm_costs.jsonl when it has rows for the role, else AGENT_RESULT."""
    per_role: dict[str, dict[str, Any]] = {}
    for row in rows:
        role = str(row.get("role") or "")
        if not role:
            continue
        acc = per_role.setdefault(role, {"cost": 0.0, "tokens": {}, "models": {}})
        acc["cost"] += _float(row.get("cost_usd"))
        for src, dst in (("input_tokens", "input"), ("output_tokens", "output"),
                         ("cache_read_tokens", "cache_read")):
            acc["tokens"][dst] = acc["tokens"].get(dst, 0) + _int(row.get(src))
        model = str(row.get("model") or "")
        if model:
            acc["models"][model] = acc["models"].get(model, 0) + 1
    for role, t in transcripts.items():
        if role in per_role:
            acc = per_role[role]
            t.cost_usd = round(acc["cost"], 6)
            t.tokens = dict(acc["tokens"])
            if acc["models"]:
                t.model = max(acc["models"], key=lambda m: acc["models"][m])
        elif role in result_costs:
            cost, toks = result_costs[role]
            t.cost_usd = round(cost, 6)
            t.tokens = dict(toks)


# ── Evidence ────────────────────────────────────────────────────────────────


def recurring_issues(t: HarnessTranscript) -> list[tuple[str, list[IssueRecord]]]:
    """Review issues the role was sent back for more than once on the same card.

    Returns `(card_id, records)` per recurrence; records are in round order.
    """
    groups: dict[tuple[str, str], list[IssueRecord]] = {}
    for rec in t.issue_records:
        groups.setdefault((rec.card_id, rec.key), []).append(rec)
    return [(cid, recs) for (cid, _), recs in groups.items() if len(recs) >= 2]


def build_role_evidence(t: HarnessTranscript) -> RoleEvidence:
    """Derive an outcome, and evidence-backed lessons, for one role.

    Lessons are only produced where the run's files back them: a review issue
    that recurred on a card (cites card id and rounds), or a card that reached
    done without any review round (reinforcement). Nothing else is inferred.
    """
    done = [c for c in t.cards if c["column"] == _DONE_COLUMN]
    attention = [c for c in t.cards if c["column"] in _ATTENTION_COLUMNS]
    blocked = [c for c in t.cards if c["column"] in _BLOCKED_COLUMNS]
    reworked = [c for c in done if c["review_round"] > 0]
    clean = [c for c in done if c["review_round"] == 0]

    if t.cards:
        if len(done) == len(t.cards):
            outcome = "passed_after_rework" if reworked else "clean"
        elif attention or (t.failures and not blocked):
            outcome = "failed"
        elif blocked or t.blocked_on:
            outcome = "blocked"
        else:
            outcome = "incomplete"
    elif t.failures:
        outcome = "failed"
    elif t.results:
        outcome = "completed"
    else:
        outcome = "no_activity"

    lessons: list[str] = []
    worked: list[str] = []
    failed: list[str] = []
    for c in clean:
        line = f"Card {c['id']} ({c['title']}) reached done with no review round"
        worked.append(f"Reinforced: {line}.")
        lessons.append(line)
    for c in reworked:
        worked.append(f"Card {c['id']} reached done after {c['review_round']} review round(s).")
    for cid, recs in recurring_issues(t):
        rounds = ", ".join(str(r.round) for r in recs)
        label = f"card {cid}" if cid else "a card"
        lesson = (f"Review issue recurred on {label} in rounds {rounds}: "
                  f"{recs[0].text[:200]}")
        failed.append(f"Lesson candidate: {lesson}")
        lessons.append(lesson)
    for c in attention:
        failed.append(f"Card {c['id']} needed attention (review round {c['review_round']}).")
    for c in blocked:
        failed.append(f"Card {c['id']} ended blocked.")
    for b in t.blocked_on:
        failed.append(f"Blocked on {b}")
    for f in t.failures:
        failed.append(f"Failure: {f}")
    for c in t.cards:
        if c["column"] not in (_DONE_COLUMN, *_ATTENTION_COLUMNS, *_BLOCKED_COLUMNS):
            failed.append(f"Card {c['id']} still open in {c['column']}.")

    tried: list[str] = []
    for c in t.cards:
        tried.append(f"{c['id']} {c['title']} [{c['column']}, rounds {c['review_round']}]")
    for s in t.result_summaries[-3:]:
        tried.append(s)
    if not t.cards and t.results:
        verdicts = [str(r.get("verdict")) for r in t.results if r.get("verdict")]
        if verdicts:
            tried.append("verdicts: " + ", ".join(verdicts))

    tags = [f"outcome-{outcome.replace('_', '-')}"]
    if recurring_issues(t):
        tags.append("review-recurrence")
    max_round = max((c["review_round"] for c in t.cards), default=0)
    if outcome == "clean":
        confidence = "high"
    elif outcome in ("failed", "blocked", "incomplete"):
        confidence = "low"
    else:
        confidence = "medium"
    return RoleEvidence(
        outcome=outcome,
        what_i_tried="; ".join(tried)[:1000],
        what_worked=" ".join(worked)[:600],
        what_failed=" ".join(failed)[:800],
        feedback_verbatim="\n".join(t.review_issues)[:600],
        iterations_to_resolve=(max_round + 1) if t.cards else len(t.results),
        confidence=confidence,
        extra_tags=tags,
        lessons=lessons,
    )


# ── Prompt rendering ────────────────────────────────────────────────────────


def render_transcript_for_prompt(t: HarnessTranscript, limit_chars: int = 6000) -> str:
    """Render a role's transcript for an LLM prompt, at most `limit_chars` long.

    Counts and ids are ours; every agent-authored string (result summaries, card
    titles, review issues, blocked/failed text) goes inside `wrap_untrusted`.
    """
    ev = build_role_evidence(t)
    tool_counts: dict[str, int] = {}
    for tool, status in t.tool_calls:
        key = f"{tool}({status})"
        tool_counts[key] = tool_counts.get(key, 0) + 1
    head = [
        f"role: {t.role}",
        f"outcome: {ev.outcome}",
        f"cost_usd: {t.cost_usd}",
        f"tokens: {json.dumps(t.tokens, sort_keys=True)}",
        f"rejections: {t.rejections}",
        "tool calls: " + (", ".join(f"{k} x{n}" for k, n in sorted(tool_counts.items())) or "none"),
    ]
    body: list[str] = []
    for c in t.cards:
        body.append(f"card {c['id']} [{c['column']}, review_round {c['review_round']}]: {c['title']}")
        for item in c["acceptance"]:
            body.append(f"  acceptance: {item}")
    if t.review_issues:
        body.append("review issues received:")
        for rec in t.issue_records:
            where = f"{rec.card_id} round {rec.round}" if rec.card_id else f"round {rec.round}"
            body.append(f"  - ({where}) {rec.text}")
    if t.blocked_on:
        body.append("blocked on:")
        body.extend(f"  - {b}" for b in t.blocked_on)
    if t.failures:
        body.append("failures:")
        body.extend(f"  - {f}" for f in t.failures)
    if t.result_summaries:
        body.append("result summaries:")
        body.extend(f"  - {s}" for s in t.result_summaries)
    wrapper_overhead = len(wrap_untrusted(f"harness-transcript:{t.role}", ""))
    head_text = "\n".join(head)
    budget = max(0, limit_chars - len(head_text) - wrapper_overhead - 2)
    body_text = "\n".join(body)
    if len(body_text) > budget:
        body_text = body_text[: max(0, budget - 15)] + "\n[truncated]"
    return head_text + "\n" + wrap_untrusted(f"harness-transcript:{t.role}", body_text)
