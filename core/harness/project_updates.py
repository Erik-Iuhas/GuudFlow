"""What changed in the project during a session, in lines a worker can read.

When the spec or the design changes while a session runs (the owner edits it, Jack's draft is
approved), every worker is told in its next turn and Studio shows who has been told. These
helpers turn two versions into short lines; the run posts them as a `PROJECT_UPDATED` signal
and each worker's delivery as `DELIVERED`.
"""

from __future__ import annotations

from core.design import Design
from core.spec import STACK_FIELDS, STACK_LABELS, Spec

MAX_LINES = 20


def spec_changes(old: Spec, new: Spec) -> list[str]:
    """Lines for what differs between two spec versions (empty when nothing a worker reads)."""
    lines: list[str] = []
    for k in STACK_FIELDS:
        a, b = getattr(old.stack, k), getattr(new.stack, k)
        if a != b:
            lines.append(f"Stack — {STACK_LABELS[k]}: {b or '(cleared)'}"
                         + (f" (was {a})" if a else ""))
    before = {d.id: d for d in old.decisions if d.status == "accepted"}
    after = {d.id: d for d in new.decisions if d.status == "accepted"}
    for did, d in after.items():
        if did not in before or (before[did].choice, before[did].title) != (d.choice, d.title):
            lines.append(f"Decision {did} {'(binding) ' if d.binding else ''}{d.title}: {d.choice}")
    lines += [f"Decision {did} no longer holds" for did in before if did not in after]
    rules_old = {r.id: r for r in old.rules if r.status == "active"}
    rules_new = {r.id: r for r in new.rules if r.status == "active"}
    for rid, r in rules_new.items():
        if rid not in rules_old or rules_old[rid].text != r.text:
            lines.append(f"Rule {rid} ({r.strength}): {r.text}")
    lines += [f"Rule {rid} was dropped" for rid in rules_old if rid not in rules_new]
    reqs_old = {r.id: r for r in old.requirements}
    for r in new.requirements:
        prev = reqs_old.get(r.id)
        if r.status != "active":
            if prev is not None and prev.status == "active":
                lines.append(f"Requirement {r.id} is now {r.status}")
            continue
        if prev is None:
            lines.append(f"New requirement {r.id}: {r.text}")
        elif prev.text != r.text or [c.text for c in prev.checks] != [c.text for c in r.checks]:
            lines.append(f"Requirement {r.id} changed: {r.text}")
    return lines


def design_changes(old: Design, new: Design) -> list[str]:
    """Lines for components, interfaces and technical decisions added or changed."""
    lines: list[str] = []
    old_c = {c.id: c for c in old.components()}
    for c in new.components():
        if c.id not in old_c:
            lines.append(f"New component {c.id} {c.name} ({', '.join(c.paths)})")
        elif (old_c[c.id].name, old_c[c.id].paths) != (c.name, c.paths):
            lines.append(f"Component {c.id} {c.name} changed ({', '.join(c.paths)})")
    old_i = {i.id: i for i in old.interfaces()}
    for i in new.interfaces():
        if i.id not in old_i or old_i[i.id].signature != i.signature:
            lines.append(f"Interface {i.id} {i.name}: {i.signature}")
    old_d = {d.get("id") for d in old.decisions()}
    for d in new.decisions():
        if d.get("id") not in old_d:
            lines.append(f"Technical decision {d.get('id')} {d.get('title')}: {d.get('choice')}")
    return lines


def brief_text(updates: list[tuple[str, list[str]]]) -> str:
    """The brief section for the updates a worker has not been told about yet."""
    lines = [line for _, changed in updates for line in changed]
    shown = lines[:MAX_LINES]
    more = len(lines) - len(shown)
    return ("The owner or the project manager changed the project while this session runs. "
            "These now hold; follow them over anything earlier in this brief:\n\n"
            + "\n".join(f"- {line}" for line in shown)
            + (f"\n- … and {more} more (look them up with `guudflow_spec`)" if more else ""))


__all__ = ["brief_text", "design_changes", "spec_changes"]
