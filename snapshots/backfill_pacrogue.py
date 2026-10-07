"""Backfill pacrogue with reasons and card links, as if earlier sessions had recorded them.

- Drafts 1-3: each change gets a reason (group) quoting the words that asked for it.
- Requirements: origin (the draft that created them) and `why` on history rows from drafts.
- Backlog items: a history entry for each draft that created or edited them, in time order;
  B8 serves R7.
- Cards of every session: `serves` from their check lines and items (a few set by hand).

Idempotent: running it twice gives the same result.
Usage: python backfill_pacrogue.py <data_root>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.harness.tasks import card_serves  # noqa: E402
from core.work_db import WorkDB  # noqa: E402

DATA = Path(sys.argv[1])
PROJECT = DATA / "projects" / "pacrogue"


def g(name: str, summary: str, because: str, source: str, quote: str | None) -> dict:
    return {"name": name, "summary": summary, "because": because, "source": source, "quote": quote}


REASONS: dict[str, list[tuple[dict, list[str]]]] = {
    "draft-1": [
        (g("The game", "The first spec of the game: maze and snappy movement, four ghosts, a 5-level run "
           "with upgrade picks, six abilities with level-ups, combos and five level families.",
           "Your description, with your answers to the first questions.", "person",
           'the whole point is that abilities "break Pac-Man", and 5 levels must feel different each '
           'run because of them'), ["c1", "c2", "c3", "c4", "c5", "c6"]),
        (g("Feel and limits", "The Studio-Trigger feel (hit-stop, inverted frames, slanted pops, five flat "
           "colours), no build step or packages, and what this version leaves out.",
           "You asked for a snappy, loud look served from a plain web/ folder.", "person",
           "Feel: snappy first."), ["c7", "c8", "c9"]),
        (g("Architecture", "Logic and drawing stay apart, abilities are data with hooks, and mazes come "
           "from seeded templates with per-level modifiers.",
           "You asked for maze generation to be decided now instead of researched.", "person",
           "Decide it now: seeded generator with hand-tuned templates as the base, plus per-level "
           "modifiers."), ["c10", "c11", "c12", "c13"]),
        (g("Three-session plan", "Seven work items over three sessions of about $7 each: playable core, "
           "run and abilities, then families, combos and feel.",
           "You set a $25 budget and asked for three sessions.", "person",
           "Plan for three sessions"), ["c14", "c15", "c16", "c17", "c18", "c19", "c20"]),
    ],
    "draft-2": [
        (g("Ghosts into the game", "The ghost logic works but the ghosts were never put in the game; B2 "
           "wires them in (eating, death, replays) and B3 draws them.",
           "Session 1 ran out of time before the ghosts reached the game, so R2.3 to R2.5 could not "
           "be checked.", "person",
           "Not done before the deadline: ghosts in the game (T7), collisions (T8), ghost drawing "
           "(T12), constraint tests (T13)."), ["c2", "c8", "c9"]),
        (g("Palette fix", "Walls, pellets and ghosts may only use the five palette colours, colours may "
           "only come from palette.js, and a new item fixes the palette.",
           "Session 1 drew blue walls and mixed extra shades into the palette.", "person",
           "Palette drifted from R7 (blue walls): must be fixed next session."),
         ["c1", "c3", "c10"]),
        (g("Keep logic testable", "The no-browser-code rule now lets maze files be plain data, and a new "
           "item adds the test that enforces it.",
           "Nobody wrote the test that keeps browser code out of the game logic (R8.3).", "session",
           None), ["c4", "c5", "c11"]),
        (g("Decisions from session 1", "Extra ghosts start from the four ghost-house tiles in turn, and "
           "the game takes one direction per step plus on/off ability flags.",
           "Later families and abilities need these settled before they are built.", "pm", None),
         ["c6", "c7"]),
        (g("Leftovers first", "The run structure and the abilities move down so the leftover ghost work "
           "goes first in session 2.",
           "Session 2 carries about $3-4 of work left over from session 1.", "pm", None),
         ["c12", "c13"]),
    ],
    "draft-3": [
        (g("Core finish", "B3 finishes the core: death flash, ghost-eat score pops and moving on when a "
           "level is cleared.", "You set the order for the last session; the core comes first.",
           "person", "Finish the core: Pac-Man dies on ghost contact"), ["c9"]),
        (g("The run", "A run is 5 levels with a pick screen between them, a death summary, a win screen "
           "and R to restart; offers lean toward the next level.",
           "The run is second in your order.", "person", "The run: 5 levels in a row"),
         ["c1", "c10"]),
        (g("Four abilities", "The core pool is Wall Burst, Ghost Blast, Dash and Chain Reaction with "
           "level-ups and the Burst+Blast combo; Magnet and Phase become a stretch item.",
           "You trimmed the pool to fit about $10.", "person",
           "Magnet and Phase only if there is money left."), ["c2", "c3", "c4", "c11", "c14"]),
        (g("Level families", "Five families (Speed, Cracked, Dark, Swarm, Boss) on shared templates; how "
           "abilities reshape the maze becomes optional.",
           "You want the 5 levels to feel different within the budget.", "person",
           "Level families so the 5 levels feel different"), ["c5", "c6", "c8", "c12"]),
        (g("Small feel pass", "The feel pass is trimmed to hit-stop, short inverted frames, shake and "
           "slanted pops.", "You asked for the feel to be kept small.", "person",
           "Feel, kept small"), ["c7", "c13"]),
        (g("Park constraint tests", "B9 moves to the bottom until it is deleted.",
           "bun test already guards the logic.", "person",
           "drop the constraint tests item (B9) for now"), ["c15"]),
    ],
    # draft-4 was rewritten in the structured format by redo_draft4.py, with its own reasons.
}

# Cards whose check lines and item do not say what they serve.
CARD_SERVES = {
    ("run_046488c5ab", "T4"): ["R7"],          # palette fix (B8)
    ("run_046488c5ab", "T20"): ["R7"],         # feel layer, extra card
    ("run_aef4b1e1be", "T9"): ["R7", "R1"],    # palette module and keyboard input
}
ITEM_SERVES = {"B8": ["R7"]}


def backfill_drafts(db: WorkDB) -> dict[str, dict]:
    """Set reasons on every draft; return draft id → {change id → reason} for the spec rows."""
    by_draft: dict[str, dict] = {}
    with db.drafts_doc() as doc:
        for row in doc["drafts"]:
            plan = REASONS.get(row["id"])
            if not plan:
                continue
            groups = {}
            cid_reason = {}
            for reason, cids in plan:
                groups[reason["name"]] = reason
                for cid in cids:
                    cid_reason[cid] = reason
            for c in row["changes"]:
                reason = cid_reason.get(c["cid"])
                assert reason is not None, f"{row['id']} {c['cid']} has no reason"
                c["change"]["group"] = reason["name"]
            row["groups"] = groups
            by_draft[row["id"]] = {"row": row, "reasons": cid_reason}
    return by_draft


def stamp(row: dict, reason: dict) -> dict:
    return {"why": {k: reason[k] for k in ("name", "summary", "because", "source", "quote")}}


def backfill_spec(db: WorkDB, drafts: dict[str, dict]) -> None:
    with db.transaction() as txn:
        reqs = {r["id"]: r for r in txn.spec.get("requirements", [])}
        for did, info in drafts.items():
            row = info["row"]
            if row["status"] != "approved":
                continue
            created = row.get("created") or {}
            for c in row["changes"]:
                ch, reason = c["change"], info["reasons"][c["cid"]]
                if ch["op"] == "add_requirement" and ch.get("ref") in created:
                    req = reqs.get(created[ch["ref"]])
                    if req is not None:
                        req["origin"] = {"draft": did, "at": row.get("closed_at"),
                                         "by": row.get("approved_by"), **stamp(req, reason)}
                elif ch["op"] == "edit_requirement" and ch.get("id") in reqs:
                    for h in reqs[ch["id"]].get("history", []):
                        if h.get("draft") == did:
                            h.update(stamp(h, reason))
        items = {i["id"]: i for i in txn.backlog.get("items", [])}
        for iid, serves in ITEM_SERVES.items():
            if iid in items:
                items[iid]["serves"] = serves
        for did, info in drafts.items():
            row = info["row"]
            if row["status"] != "approved":
                continue
            created = row.get("created") or {}
            for c in row["changes"]:
                ch, reason = c["change"], info["reasons"][c["cid"]]
                if ch["op"] == "add_item":
                    iid, what = created.get(ch.get("ref", "")), "created"
                elif ch["op"] == "edit_item":
                    fields = sorted(k for k, v in ch.items()
                                    if k not in ("op", "id", "group") and v is not None)
                    iid, what = ch.get("id"), "edited: " + ", ".join(fields)
                else:
                    continue
                item = items.get(iid or "")
                if item is None:
                    continue
                hist = item.setdefault("history", [])
                hist[:] = [h for h in hist if h.get("draft") != did]     # idempotent
                hist.append({"draft": did, "at": row.get("closed_at"), "by": row.get("approved_by"),
                             "change": what, **stamp(item, reason)})
                hist.sort(key=lambda h: float(h.get("at") or 0))


def backfill_cards(db: WorkDB) -> int:
    item_serves = {i["id"]: list(i.get("serves") or []) for i in db.read_backlog_doc()["items"]}
    item_serves.update(ITEM_SERVES)
    changed = 0
    for run in sorted((DATA / "runs").iterdir()):
        path = run / "tasks.jsonl"
        if not path.is_file():
            continue
        last: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line)
                last[t["task_id"]] = t
        out = []
        for tid, t in last.items():
            if t.get("story_id") not in ("plan", "board"):
                continue
            serves = card_serves(t.get("acceptance_criteria") or [],
                                 CARD_SERVES.get((run.name, tid), []),
                                 item_serves.get(t.get("backlog_id") or "", []))
            if serves != t.get("serves") or "supports" not in t:
                out.append({**t, "serves": serves, "supports": list(t.get("supports") or [])})
        if out:
            with path.open("a", encoding="utf-8") as f:
                for t in out:
                    f.write(json.dumps(t) + "\n")
            changed += len(out)
    return changed


def main() -> None:
    db = WorkDB(PROJECT)
    drafts = backfill_drafts(db)
    backfill_spec(db, drafts)
    print("cards updated:", backfill_cards(db))


if __name__ == "__main__":
    main()
