"""Rewrite pacrogue's draft-4 in the structured format, as Jack would propose it now.

Same decisions and scope as the original draft-4 (default dash with charges, Rush replaces Dark,
readability, built-only offers and the HUD fix, Magnet/OVERDRIVE as the stretch card), but:
- one change per field (set_statement, edit_check, add_check, remove_check), never whole-text edits;
- the new requirements carry their tunable numbers as params, assumed until a person confirms;
- items point at check ids instead of copying text;
- every change belongs to a reason that quotes the owner's message.

The turn passes `validate_turn` (the same checks a real PM turn gets) and replaces the untouched
PM changes of the open draft. Usage: python redo_draft4.py <data_root>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.plan_drafts import PlanDrafts, PlanTurn, validate_turn  # noqa: E402
from core.spec import Spec  # noqa: E402

DATA = Path(sys.argv[1])

DASH, BOARD, PICK, EXTRA = "Default dash", "Readable board", "Pick screen and HUD fixes", "Optional extra"

GROUPS = [
    {"name": DASH, "source": "person",
     "summary": "Every run starts with a Space dash that has charges; dashing through a ghost kills "
                "it and power pellets refill the charges. The Dash upgrade makes this dash stronger "
                "and Wall Burst builds on it.",
     "because": "You want the player to have a real choice instead of waiting for power pellets.",
     "quote": "Default dash for everyone: every run starts with a dash (Space) that lets Pac-Man go "
              "through ghosts or eat them during the dash."},
    {"name": BOARD, "source": "person",
     "summary": "Rush (more power pellets) replaces Dark, the boss gets 5 HP, and no family or "
                "effect may hide the maze or the ghosts.",
     "because": "You need to see the ghosts to plan.",
     "quote": "Drop the Dark family."},
    {"name": PICK, "source": "person",
     "summary": "The pick screen only offers abilities that are built, and the HUD never shows DEAD.",
     "because": "Session 3 offered Magnet, which is not built, and showed DOTS: DEAD.",
     "quote": "Fix the pick screen offering Magnet, which is not built"},
    {"name": EXTRA, "source": "person",
     "summary": "The stretch card shrinks to Magnet and the OVERDRIVE combo; Phase and DETONATE are "
                "dropped because every dash already passes through ghosts.",
     "because": "Phase would duplicate the default dash.",
     "quote": "Rebalance the abilities around the default dash"},
]

CHANGES = [
    # ── Default dash ────────────────────────────────────────────────────────
    {"op": "add_requirement", "ref": "new:1", "group": DASH, "kind": "feature", "priority": "must",
     "title": "Default dash with charges",
     "text": "Every run starts with a Space dash, no pick needed. A dash spends one charge and "
             "doubles Pac-Man's speed for a short time. While dashing, and for a moment after, ghost "
             "contact never kills Pac-Man: a ghost he touches is killed (a frightened one is eaten "
             "as usual) and the boss loses at most one HP per dash. Charges come back over time, "
             "and eating a power pellet refills them all.",
     "rationale": "The player always has a way to deal with a threat, and power pellets become a "
                  "resource to route for.",
     "assumed": True,
     "params": [
         {"key": "charges", "value": 2, "assumed": True},
         {"key": "dash_steps", "value": 24, "unit": "steps", "assumed": True, "note": "0.4 s"},
         {"key": "safe_steps_after", "value": 6, "unit": "steps", "assumed": True},
         {"key": "recharge_steps", "value": 600, "unit": "steps", "assumed": True, "note": "10 s"},
         {"key": "dash_kill_points", "value": 200, "assumed": True},
     ],
     "checks": [
         {"method": "unit", "text": "a new run with no abilities held has `charges` dash charges; "
          "the 'dash' action spends one charge and doubles speed for `dash_steps` steps; with no "
          "charge left it does nothing"},
         {"method": "unit", "text": "ghost contact during a dash and for `safe_steps_after` steps "
          "after never emits player_died; a non-frightened ghost touched during a dash emits "
          "ghost_killed with cause 'dash' and adds `dash_kill_points`; the boss loses at most 1 HP "
          "per dash"},
         {"method": "unit", "text": "one charge comes back every `recharge_steps` steps up to the "
          "maximum, and eating a power pellet restores all charges at once"},
         {"method": "manual", "text": "the HUD shows dash charges as pips with recharge progress; "
          "Space dashes with speed lines from level 1; dashing through a ghost eats it with a "
          "score pop and hit-stop"},
     ]},
    {"op": "set_statement", "id": "R4", "group": DASH,
     "text": "Core upgrade pool: four upgrades, each with two levels, all built on the default dash. "
             "Dash makes the default dash stronger (more charges, a longer dash, faster recharge). "
             "Wall Burst lets the dash go through cracked walls and break them; at its second level "
             "it also breaks solid interior walls, never the outer border. Ghost Blast (E) fires a "
             "shockwave that kills nearby ghosts, then cools down. Chain Reaction (passive) makes a "
             "killed ghost, including a dash kill, kill the ghosts next to it; at its second level "
             "chained kills explode too."},
    {"op": "edit_check", "id": "R4.9", "group": DASH, "method": "unit",
     "text": "Bun test: Dash L1 gives 3 charges, a 30-step dash and a 420-step recharge; L2 gives 4 "
             "charges, 36 steps and 300 steps; without Dash the default dash values apply, and the "
             "'dash' action works with no ability held."},
    {"op": "add_decision", "ref": "new:5", "group": DASH, "binding": True,
     "serves": ["new:1", "R4"],
     "title": "Default dash: charges, ghost eating, power-pellet refill",
     "choice": "Every run has one Space dash with charges (values in the default dash requirement). "
               "It is safe during the dash and briefly after, kills non-frightened ghosts on "
               "contact, hurts the boss at most once per dash, recharges over time and refills on a "
               "power pellet. Dash and Wall Burst are upgrades of this one dash; the old 'Wall Burst "
               "grants the dash' path is removed.",
     "reason": "Gives the player a choice of how to handle every threat, not just waiting for power "
               "pellets. Rejected: pass-through only (no way to fight back), a plain cooldown (no "
               "link to power pellets, so no routing choice), unlimited dash (removes all danger) "
               "and a separate Phase ability (duplicates the dash)."},
    {"op": "add_item", "ref": "new:3", "group": DASH, "kind": "code", "size": "M",
     "title": "Default dash with charges; Dash and Wall Burst built on it",
     "serves": ["new:1", "R4"],
     "acceptance": [
         "The 'Wall Burst grants the dash' path is removed; Dash and Wall Burst only modify the "
         "shared dash.",
         "Dash state (charges, maximum, recharge progress) is exposed to the HUD.",
     ]},
    # ── Readable board ──────────────────────────────────────────────────────
    {"op": "set_statement", "id": "R6", "group": BOARD,
     "text": "Five level families: Speed (faster ghosts), Cracked (many cracked walls), Rush (extra "
             "power pellets, longer frightened time, pellets worth more), Swarm (extra ghosts) and "
             "Boss (a big ghost with several HP, hurt by ability attacks, a dash or being eaten while "
             "frightened, plus the usual ghosts; cleared when the boss dies). The first four levels "
             "are Speed, Cracked, Rush and Swarm in a seeded shuffled order; the last level is always "
             "Boss. Families are modifiers on shared templates (D6), and no family hides the maze or "
             "the ghosts."},
    {"op": "edit_check", "id": "R6.1", "group": BOARD, "method": "unit",
     "text": "Bun test: for any seed, levels 1-4 are a permutation of Speed, Cracked, Rush, Swarm "
             "and level 5 is Boss."},
    {"op": "edit_check", "id": "R6.6", "group": BOARD, "method": "unit",
     "text": "Bun test: each family's modifier holds in the generated level config (speed factor "
             "1.25; cracked ratio >= 0.3; Rush has 8 power pellets, 2 per quadrant, frightened 180 "
             "steps, pellet value 20; ghost count 6; boss HP 5), and no config has a light radius or "
             "visibility field."},
    {"op": "edit_check", "id": "R6.9", "group": BOARD, "method": "manual",
     "text": "Manual: the HUD shows the family name; cracked walls look distinct; Rush's 8 power "
             "pellets are visible; the boss is drawn larger with 5 HP pips."},
    {"op": "add_requirement", "ref": "new:2", "group": BOARD, "kind": "quality",
     "priority": "must", "title": "Threats always readable",
     "text": "In every family the whole maze and every ghost are drawn every frame during play. "
             "Effects (inverted frames, shake, pops, banners) never hide ghosts or Pac-Man.",
     "params": [{"key": "inverted_max_frames", "value": 2, "unit": "frames", "assumed": True}],
     "checks": [
         {"method": "unit", "text": "with a stub canvas context, drawing a game state in each of "
          "the 5 families draws every wall tile and every live ghost"},
         {"method": "unit", "text": "an inverted impact frame lasts at most `inverted_max_frames` "
          "rendered frames and still draws every ghost"},
         {"method": "manual", "text": "in all 5 families every ghost and the whole maze stay "
          "visible at all times during play, including during shake, text pops and dashes"},
     ]},
    {"op": "add_decision", "ref": "new:6", "group": BOARD, "binding": False,
     "serves": ["R6", "new:2"], "title": "Rush replaces the Dark family",
     "choice": "The fourth family is Rush: 8 power pellets (2 per quadrant), frightened 180 steps, "
               "pellets worth 20. It is a modifier on the shared templates, like the others.",
     "reason": "Dark hid the ghosts, so players could not plan. Rush keeps the board readable and "
               "plays into the dash-recharge loop for the cost of a modifier. Rejected: Maze Shift "
               "(moving walls need new generation, connectivity checks and telegraphs, too costly "
               "for one session) and staying at 3 families (one family would repeat within a run)."},
    {"op": "add_rule", "ref": "new:7", "group": BOARD, "applies_to": ["*"], "strength": "binding",
     "text": "No level family, effect or screen may hide maze walls or ghosts during play. Inverted "
             "impact frames last at most 2 frames and still draw every ghost."},
    {"op": "add_item", "ref": "new:4", "group": BOARD, "kind": "code", "size": "M",
     "title": "Rush replaces Dark, boss 5 HP, built-only offers, no DEAD in the HUD",
     "serves": ["R6", "new:2", "R3"], "depends_on": ["new:3"],
     "acceptance": [
         "The Dark modifier, light radius and dark render code are removed; the Rush modifier is "
         "in families.js.",
         "A full 5-level run plays in the browser.",
     ]},
    # ── Pick screen and HUD fixes ───────────────────────────────────────────
    {"op": "set_statement", "id": "R3", "group": PICK,
     "text": "Run structure: a run is 5 levels. Before level 1 and after each of levels 1-4 the "
             "player picks 1 of 3 upgrade offers with keys 1/2/3. Offers come only from abilities "
             "that are built and registered. Picking a held ability raises it to level 2 (max). "
             "Offers lean toward the next level: slot 1 is an ability suited to the next level's "
             "family when one is available (Speed: Dash; Cracked: Wall Burst; Swarm: Ghost Blast or "
             "Chain Reaction; Rush: Dash or Chain Reaction; Boss: Ghost Blast), slot 2 is a level-up "
             "of a held ability or a combo partner when available, and slot 3 is seeded random. No "
             "lives: one death ends the run with a summary. Clearing level 5 shows a win screen. R "
             "restarts with a new seed."},
    {"op": "add_check", "id": "R3", "group": PICK,
     "check": {"method": "unit", "text": "Bun test over 200 seeds: every offered id is a built "
               "ability in the registry; Magnet (or any listed but unbuilt id) is never offered."}},
    {"op": "edit_check", "id": "R3.9", "group": PICK, "method": "manual",
     "text": "Manual: on ghost contact the screen flashes inverted, play freezes for about 0.5 s, "
             "then a summary shows seed, level reached, score and abilities held; the HUD never "
             "shows the text DEAD; R starts a new run with a new seed."},
    # ── Optional extra ──────────────────────────────────────────────────────
    {"op": "set_statement", "id": "R10", "group": EXTRA,
     "text": "Stretch, only if budget remains: Magnet (passive: pulls pellets within 2 tiles; L2 4 "
             "tiles, power pellets too) and OVERDRIVE (with Dash and Magnet held, the magnet radius "
             "doubles during a dash). Phase and DETONATE are dropped: every dash already passes "
             "through ghosts."},
    {"op": "remove_check", "id": "R10.2", "group": EXTRA},
    {"op": "remove_check", "id": "R10.4", "group": EXTRA},
    {"op": "edit_item", "id": "B7", "group": EXTRA,
     "title": "Stretch (only if budget remains): Magnet and OVERDRIVE",
     "depends_on": ["new:3", "new:4"]},
]

REPLY = ("Draft 4 is the design-fix session in four reasons: the default dash, a readable board, "
         "the pick screen and HUD fixes, and the trimmed stretch card. The new numbers are params "
         "marked assumed, so you can confirm or change them on the Spec tab.")


def main() -> None:
    pd = PlanDrafts(DATA, "pacrogue")
    turn = PlanTurn.model_validate({"groups": GROUPS, "changes": CHANGES, "reply": REPLY})
    said = [m["text"] for m in pd.messages(500) if m["author"] == "human"]
    projects = json.loads((DATA / "projects.json").read_text())
    doc = projects.get("pacrogue") if isinstance(projects, dict) else None
    team = list((doc or {}).get("agents") or [])
    problems = validate_turn(turn, Spec(pd.db.read_spec_doc()), pd.db.read_backlog_doc(), team, said)
    if problems:
        raise SystemExit("refused:\n" + "\n".join(problems))
    draft = pd.current()
    assert draft is not None and draft.id == "draft-4", draft
    lead = bool(((doc or {}).get("seats") or {}).get("lead"))
    d = pd.merge_pm_turn(draft, turn, lead_held=lead)
    print(d.id, len(d.changes), "changes,", len(d.groups), "reasons")


if __name__ == "__main__":
    main()
