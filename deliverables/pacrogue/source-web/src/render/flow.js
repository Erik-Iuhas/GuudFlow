// Render-side flow: what the screen does with the events the logic emits, and
// how a run moves between screens. DOM-free and clock-free so bun can test it;
// main.js only wires it up (it owns the clock, createRun and the level builder).
//
// Phases (view.phase):
//   picking   the pick screen: 3 offer cards, keys 1/2/3 (before level 1 and
//             after levels 1-4)
//   playing   the simulation steps
//   frozen    Pac-Man died: ~0.5 s hit-stop with the inverted flash
//   cleared   level cleared: a short banner, then picking (or won after level 5)
//   summary   the run is over (death): seed, level reached, score, abilities
//   won       level 5 cleared: win screen with score and abilities
// The simulation steps only in 'playing'. 'summary' and 'won' take R (after the
// restart lockout) and nothing else; 'picking' takes 1/2/3 and nothing else.
//
// Two layers:
//   view functions   (createView, consumeEvents, advanceFrame, ...) are pure and
//                    return a NEW view; they know nothing about runs.
//   session          (createSession, pickSlot, stepSession, endFrame) binds a
//                    run (logic/run.js) and a level builder to a view. It holds
//                    mutable {run, game, view}; main.js passes the clock-seeded
//                    run in and the builder (generateLevel + createGame).
//
//   const session = createSession({ run: createRun({ seed }), buildLevel });
//   pickSlot(session, 2);                          // key 2 on the pick screen
//   stepSession(session, consumeDirection, due);   // per frame
//   drawFrame(ctx, session.game?.state, session.view);
//   endFrame(session);                             // per frame, after drawing
//
// view = {
//   maze, seed
//   phase        see above
//   freeze       render frames of death freeze left (phase 'frozen')
//   invert       render frames of inverted impact flash left (<= INVERTED_MAX_FRAMES)
//   pops         [{text, x, y, age, outline?}] score / combo-name pops, x/y in sub-units
//   fx           render/fx.js effect list (rings, bursts, crumbles, dash trail)
//   endFrames    render frames since the banner/summary/win appeared
//   hud          {level, levels, family, held:[{id, name, level}]}
//   score        run total (earlier levels + this level)
//   offers       the 3 offers while picking (run.currentOffers())
//   summary      {seed, levelReached, score, held, won} once the run ended
//   ending       true when the freeze is for the last life (player_died): it ends
//                in 'summary'; a lost life (life_lost) ends in 'playing' instead
// }
//
// Time here is RENDER frames (60/s): the freeze is a hit-stop, so it must run
// even though the simulation does not.

import { CYAN, PINK, YELLOW } from './palette.js';
import { advanceFx, createFx, spawnFx, spawnTrail } from './fx.js';
import { INVERTED_MAX_FRAMES } from './feel.js';
import { LEVEL_COUNT } from '../logic/run.js';
import { getAbility } from '../logic/abilities/registry.js';
import { BLAST_COOLDOWN } from '../logic/abilities/ghost_blast.js';

export const FREEZE_FRAMES = 30; // ~0.5 s of frozen play after Pac-Man dies
export const INVERT_FRAMES = INVERTED_MAX_FRAMES; // the death flash: never longer than rule-10 allows
export const POP_FRAMES = 60; // a score pop lives ~1 s
export const POP_RISE_PX = 14; // total rise over a pop's life
export const POP_BIG_FRAMES = 5; // hard-cut oversize frames at the start
export const POP_BLINK_FRAMES = 14; // last frames flicker out
export const POP_MAX = 16; // oldest pops drop past this
export const RESTART_LOCKOUT_FRAMES = 30; // ~0.5 s before R may restart
export const CLEAR_FRAMES = 60; // level-cleared banner, then the next screen

export const POP_SKEW_DEG = 14; // every pop is slanted at least 10 degrees (R7.8)
export const POP_OUTLINE_PX = 3; // and outlined at least 3 px thick
export const POP_FONT_PX = 18;
export const POP_BIG_FONT_PX = 26;
const POP_CHAR_W = 0.62; // monospace advance as a fraction of the font size

export function createView({ maze = null, seed = null, ...rest } = {}) {
  return {
    maze,
    seed,
    phase: 'playing',
    freeze: 0,
    invert: 0,
    pops: [],
    fx: createFx(),
    endFrames: 0,
    ending: false,
    ...rest,
  };
}

/** The simulation advances only while play is live. */
export function canStep(view) {
  return view.phase === 'playing';
}

/** R may start a new run once the summary / win screen has been up ~0.5 s. */
export function canRestart(view) {
  return (view.phase === 'summary' || view.phase === 'won') &&
    view.endFrames >= RESTART_LOCKOUT_FRAMES;
}

/** React to one step's events: death freeze, score pops, level-cleared banner. */
export function consumeEvents(events, view) {
  if (!events || events.length === 0) return view;
  let next = view;
  const own = () => (next === view ? (next = { ...view, pops: view.pops.slice() }) : next);
  const fx = spawnFx(view.fx, events);
  if (fx !== view.fx) own().fx = fx;
  for (const e of events) {
    if (e.type === 'ghost_eaten') {
      const v = own();
      // A dash kill's pop is outlined CYAN (the dash's colour); the rest follow popLook.
      const pop = { text: String(e.points), x: e.x, y: e.y, age: 0 };
      if (e.cause === 'dash') pop.outline = CYAN;
      v.pops.push(pop);
      if (v.pops.length > POP_MAX) v.pops.shift();
    } else if (e.type === 'boss_hit' || e.type === 'boss_killed') {
      const v = own();
      v.pops.push({ text: String(e.points), x: e.x, y: e.y, age: 0, outline: PINK });
      if (v.pops.length > POP_MAX) v.pops.shift();
    } else if (e.type === 'life_lost' || e.type === 'player_died') {
      // Both freeze with the inverted flash; only the last life ends the run.
      const v = own();
      if (v.phase === 'playing') {
        v.phase = 'frozen';
        v.freeze = FREEZE_FRAMES;
        v.invert = INVERT_FRAMES;
        v.endFrames = 0;
        v.ending = e.type === 'player_died';
      }
    } else if (e.type === 'level_cleared') {
      const v = own();
      if (v.phase === 'playing') {
        v.phase = 'cleared';
        v.endFrames = 0;
      }
    }
  }
  return next;
}

/**
 * Run up to maxSteps logic steps while play is live; stops the moment the view
 * leaves 'playing' (a death or a clear ends the burst: nothing steps on).
 * readInput() is called exactly once per step. Returns {view, steps}.
 */
export function stepWhilePlaying(game, view, readInput, maxSteps, feel = null) {
  let v = view;
  let steps = 0;
  // A big hit (feel.onEvents) starts a hit-stop: the burst ends there, and the
  // caller (main.js) then skips game.step until feel.hitStop has run out.
  while (steps < maxSteps && canStep(v) && !(feel && feel.hitStop > 0)) {
    const events = game.step(readInput());
    if (feel) feel.onEvents(events, { x: game.state.pac.x + 8, y: game.state.pac.y + 8 });
    v = consumeEvents(events, v);
    steps++;
  }
  return { view: v, steps };
}

/** Pure per-frame step, after drawing: age pops, count the freeze down. */
export function advanceFrame(view) {
  let pops = view.pops;
  if (pops.length > 0) {
    pops = [];
    for (const p of view.pops) if (p.age + 1 < POP_FRAMES) pops.push({ ...p, age: p.age + 1 });
  }
  let { phase, freeze, invert, endFrames } = view;
  if (phase === 'frozen') {
    freeze -= 1;
    if (freeze <= 0) {
      freeze = 0;
      phase = view.ending ? 'summary' : 'playing'; // a lost life resumes the same level
      endFrames = 0;
    }
  } else if (phase === 'summary' || phase === 'won' || phase === 'cleared') {
    endFrames += 1;
  }
  if (invert > 0) invert -= 1;
  return { ...view, pops, fx: advanceFx(view.fx), phase, freeze, invert, endFrames };
}

/**
 * Where and how a score pop is drawn this frame (pure). x/y are maze-space
 * logical px of the text's centre; hard-cut size pop in, steady rise, flicker
 * out. White fill; the outline alternates PINK (200, 800) / YELLOW (400, 1600).
 */
export function popLook(pop, mazeW = Infinity) {
  const big = pop.age < POP_BIG_FRAMES;
  const size = big ? POP_BIG_FONT_PX : POP_FONT_PX;
  const life = pop.life ?? POP_FRAMES;
  const rise = Math.round((Math.min(pop.age, life) / life) * POP_RISE_PX);
  // The slant leans the glyphs right by up to size * tan(skew) at the top, so
  // keep that much extra room inside the maze.
  const halfW = Math.ceil((pop.text.length * size * POP_CHAR_W) / 2) + POP_OUTLINE_PX + 4;
  const x = Math.max(halfW, Math.min(mazeW - halfW, Math.round(pop.x)));
  const left = life - pop.age;
  const visible = left > POP_BLINK_FRAMES || Math.floor(left / 2) % 2 === 1;
  const pink = pop.text === '200' || pop.text === '800';
  const outline = pop.outline ?? (pink ? PINK : YELLOW);
  return {
    text: pop.text, x, y: Math.round(pop.y) - rise, size, outline, visible,
    skewDeg: POP_SKEW_DEG, outlinePx: POP_OUTLINE_PX,
  };
}

// ── run session ─────────────────────────────────────────────────────────────

const heldChips = (held) => [...held].map(([id, level]) => ({ id, name: getAbility(id)?.name ?? id, level }));
const chipsOf = (list) => list.map(({ id, level }) => ({ id, name: getAbility(id)?.name ?? id, level }));

function hudOf(run) {
  return {
    level: run.levelIndex + 1,
    levels: LEVEL_COUNT,
    family: run.families[run.levelIndex],
    held: heldChips(run.held),
  };
}

function pickView(s) {
  return createView({
    seed: s.run.seed,
    phase: 'picking',
    offers: s.run.currentOffers(),
    hud: hudOf(s.run),
    score: s.base,
  });
}

function startLevel(s) {
  s.game = s.buildLevel(s.run.levelConfig());
  s.view = createView({
    maze: s.game.maze,
    seed: s.run.seed,
    phase: 'playing',
    hud: hudOf(s.run),
    score: s.base,
  });
}

/**
 * Bind a run to a view. buildLevel(levelConfig) -> game (main.js: generateLevel +
 * createGame). A fresh session opens on the pick screen before level 1.
 */
export function createSession({ run, buildLevel }) {
  const s = { run, buildLevel, game: null, base: 0, view: null };
  s.view = pickView(s);
  return s;
}

/**
 * The 1/2/3 keys. Only the pick screen takes them: anywhere else this does
 * nothing and returns false. A taken pick builds the level (all pellets and
 * ghosts reset: it is a fresh game) and starts play.
 */
export function pickSlot(s, slot) {
  if (s.view.phase !== 'picking') return false;
  if (s.run.pick(slot).length === 0) return false;
  startLevel(s);
  return true;
}

/**
 * Step the simulation up to maxSteps while play is live (readInput once per
 * step), then tell the run what happened: a death ends it (summary after the
 * freeze), a clear opens the banner and moves the run on. Returns steps taken.
 */
export function stepSession(s, readInput, maxSteps, feel = null) {
  if (!canStep(s.view)) return 0;
  const { view, steps } = stepWhilePlaying(s.game, s.view, readInput, maxSteps, feel);
  const total = s.base + s.game.state.score;
  let v = { ...view, score: total };
  if (steps > 0) v.fx = spawnTrail(v.fx, s.game.state);
  if (v.phase === 'frozen' && v.ending) {
    const over = s.run.playerDied(total).find((e) => e.type === 'run_over');
    if (over) v.summary = { seed: over.seed, levelReached: over.levelReached, score: total, held: chipsOf(over.held), won: false };
  } else if (v.phase === 'cleared') {
    const events = s.run.levelCleared(total, s.game.state.lives); // lives carry over
    s.base = total;
    const won = events.find((e) => e.type === 'run_won');
    if (won) v.summary = { seed: s.run.seed, levelReached: LEVEL_COUNT, score: total, held: chipsOf(won.held), won: true };
  }
  s.view = v;
  return steps;
}

/** Once per rendered frame, after drawing: ages the view; banner -> next screen. */
export function endFrame(s) {
  const v = advanceFrame(s.view);
  if (v.phase === 'cleared' && v.endFrames >= CLEAR_FRAMES) {
    if (s.run.status === 'won') s.view = { ...v, phase: 'won', endFrames: 0 };
    else {
      s.game = null; // the pick screen has no maze; the next level is built on pick
      s.view = pickView(s);
    }
    return s.view;
  }
  s.view = v;
  return v;
}

// ── cooldowns and dash charges for the HUD ──────────────────────────────────
/**
 * The default dash for the HUD (pure), from state.dash (dash_core.js):
 *   {charges, max, steps, rechargeSteps, ratio, active, safe}
 * charges / max are the pips; steps is the steps left until the next charge
 * (0 when full); ratio (0..1) is the recharge progress; active is true mid-dash;
 * safe is true while Pac-Man cannot be killed (dash or the moment after).
 * Null without a dash state.
 */
export function dashInfo(dash) {
  if (!dash || typeof dash.charges !== 'number') return null;
  const full = dash.charges >= dash.maxCharges;
  return {
    charges: dash.charges,
    max: dash.maxCharges,
    steps: full ? 0 : Math.max(0, dash.rechargeSteps - dash.recharge),
    rechargeSteps: dash.rechargeSteps,
    ratio: full ? 1 : dash.recharge / dash.rechargeSteps,
    active: dash.active > 0,
    safe: dash.active > 0 || dash.safe > 0,
  };
}

/**
 * Per-ability cooldown (pure) for the chips: id -> {steps, max, active}.
 * Only Ghost Blast has one. The dash is not a chip cooldown: its charges and
 * recharge are the HUD pips (dashInfo + screens.js drawDashPips), so the Dash
 * and Wall Burst chips just show their level, like Chain Reaction.
 */
export function cooldownInfo(abilities) {
  const blast = (abilities || []).find((a) => a.id === 'ghost_blast');
  const out = {};
  if (blast) out.ghost_blast = { steps: blast.cooldown, max: BLAST_COOLDOWN[blast.level], active: false };
  return out;
}

/** hud.held chips with their cooldown attached ({steps, max, active}). */
export function chipsWithCooldowns(held, abilities) {
  const info = cooldownInfo(abilities);
  return (held || []).map((h) => (info[h.id] ? { ...h, cool: info[h.id] } : h));
}
