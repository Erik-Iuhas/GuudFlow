// Ghost targeting rules and the scatter / chase / frightened mode scheduler.
//
//   import { targetTile, ghostTarget, scatterCorner,
//            modeAtStep, createModeScheduler } from './ghosts.js';
//
// This module decides WHERE a ghost wants to go, WHICH mode it is in, and
// moves it (createGhost / stepGhost, documented in the "movement" section at the
// bottom). Everything is integer: no clock, no Math.random, no DOM.
//
// ── Ghosts ────────────────────────────────────────────────────────────────
// A ghost is {kind, tile:{x,y}, scatter?:{x,y}, mode?}. Kinds, in the order of
// the template's 'G' starts (maze.ghostStarts[i] is GHOST_KINDS[i]):
//   0 chaser    (Blinky)  targets Pac-Man's tile.
//   1 ambusher  (Pinky)   targets the tile AMBUSH_AHEAD (4) ahead of Pac-Man in
//                         his facing direction. Pure offset (dx, dy) * 4, so
//                         facing up is (0, -4): the classic "up also goes left"
//                         overflow bug does not exist. Clamped into the maze.
//                         Pac-Man with no direction yet: his own tile.
//   2 flanker   (Inky)    pivot = tile FLANK_AHEAD (2) ahead of Pac-Man (clamped
//                         into the maze); target = chaser + 2 * (pivot - chaser),
//                         clamped into the maze. If the chaser stands on the
//                         pivot, the vector is zero and the target is the pivot.
//                         If the chaser stands on Pac-Man's tile the target is
//                         the tile 4 ahead of Pac-Man (vector 2 * dir, doubled).
//   3 shy       (Clyde)   targets Pac-Man while MORE than SHY_RADIUS (8) tiles
//                         away (euclidean tile distance, so exactly 8 is
//                         "within"); inside that radius (inclusive) it heads
//                         for its own scatter corner: the wander.
// Clamping is to [0, width-1] x [0, height-1]; targets are not wrapped through
// tunnels and need not be walkable tiles.
//
// targetTile(ghost, ctx) -> {x, y}: the chase target by the rules above.
//   ctx = {
//     pac:    {tile:{x,y}, dir}   (dir 'up'|'left'|'down'|'right'|null; a bare
//                                 {x, y, dir} is accepted too)
//     chaser: {x, y}              the chaser's tile (or {tile:{x,y}})
//     maze:   parsed maze         (width/height for clamping and corners)
//   }
// The chaser and ambusher ignore ghost.tile; the shy ghost needs it. The
// flanker needs ctx.chaser; the others do not.
//
// scatterCorner(maze, kindOrIndex) -> {x, y}: the ghost's corner, from maze
// size only: chaser top-right, ambusher top-left, flanker bottom-right, shy
// bottom-left, i.e. (width-1 | 0, 0 | height-1). A ghost's own `scatter` field
// overrides it (ability hooks / modifiers may move corners).
//
// ghostTarget(ghost, ctx, mode) -> {x, y} | null: the mode-aware target.
//   'scatter'    the ghost's scatter corner
//   'chase'      targetTile(ghost, ctx)
//   'frightened' null (no target; movement picks randomly from the seeded rng)
//   'eaten'      the ghost-house door (or the ghost's own start without a
//                door). A ghost with ghost.mode === 'eaten' is eaten whatever
//                the global mode says; frightened never applies to it.
//
// ── Mode schedule ─────────────────────────────────────────────────────────
// SCHEDULE is in 60 Hz steps (seconds * 60):
//   scatter 7s, chase 20s, scatter 7s, chase 20s, scatter 5s, chase 20s,
//   scatter 5s, then chase forever.
// modeAtStep(step) is that table: step is the number of non-frightened steps
// the schedule has run (0-based). Phase changes land on steps
//   420 (->chase) 1620 (->scatter) 2040 3240 3540 4740 5040 (->chase, final).
// modeAtStep(419) === 'scatter', modeAtStep(420) === 'chase', and so on.
//
// createModeScheduler() is the stateful wrapper for the game loop:
//   const sched = createModeScheduler();
//   const { mode, reverse } = sched.update(game.state.frightenedTimer);
// Call once per game step, after game.step(), with the current
// frightenedTimer. While frightenedTimer > 0 the mode is 'frightened' and the
// schedule does NOT advance (paused); it resumes where it left off when the
// timer reaches 0. sched.state = {step} counts non-frightened updates; the
// mode returned by an update is modeAtStep(step before that update's
// increment), so the first update returns modeAtStep(0).
// `reverse` is true on the update where the mode changed in a way that makes
// ghosts reverse: scatter<->chase swaps and entering frightened. Leaving
// frightened back to the same underlying mode does not reverse. (The first
// update never reverses.)

import { DIR_NAMES, DIRS, isOpen, neighbours, stepTile } from './maze.js';
import { OPPOSITE, SUB } from './player.js';

export const GHOST_KINDS = Object.freeze(['chaser', 'ambusher', 'flanker', 'shy']);

/**
 * The Boss level's big ghost. Not one of GHOST_KINDS (those rotate over the
 * template's G tiles): createGhost accepts it, and targeting, scatter corner
 * and movement treat it exactly like the chaser.
 */
export const BOSS_KIND = 'boss';
export const AMBUSH_AHEAD = 4;
export const FLANK_AHEAD = 2;
export const SHY_RADIUS = 8;

export const MODE = Object.freeze({
  SCATTER: 'scatter',
  CHASE: 'chase',
  FRIGHTENED: 'frightened',
  EATEN: 'eaten',
});

const STEPS_PER_SECOND = 60;

/** [mode, steps] phases in order; after the last, the mode is chase forever. */
export const SCHEDULE = Object.freeze(
  [
    ['scatter', 7],
    ['chase', 20],
    ['scatter', 7],
    ['chase', 20],
    ['scatter', 5],
    ['chase', 20],
    ['scatter', 5],
  ].map(([mode, seconds]) => Object.freeze([mode, seconds * STEPS_PER_SECOND])),
);

/** Step at which each phase starts, then the start of the final chase. */
export const PHASE_STARTS = Object.freeze(
  (() => {
    const starts = [];
    let at = 0;
    for (const [, steps] of SCHEDULE) {
      starts.push(at);
      at += steps;
    }
    starts.push(at);
    return starts;
  })(),
);

/** Scatter/chase mode at schedule step `step` (non-frightened steps run). */
export function modeAtStep(step) {
  if (!Number.isInteger(step) || step < 0) {
    throw new RangeError(`modeAtStep: step must be an integer >= 0, got ${step}`);
  }
  for (let i = 0; i < SCHEDULE.length; i++) {
    if (step < PHASE_STARTS[i + 1]) return SCHEDULE[i][0];
  }
  return MODE.CHASE;
}

/** Effective global mode: frightened while the timer runs, else the schedule. */
export function globalMode(step, frightenedTimer = 0) {
  return frightenedTimer > 0 ? MODE.FRIGHTENED : modeAtStep(step);
}

export function createModeScheduler() {
  const state = { step: 0 };
  let previous = null; // last effective mode

  function update(frightenedTimer = 0) {
    let mode;
    if (frightenedTimer > 0) {
      mode = MODE.FRIGHTENED; // schedule paused
    } else {
      mode = modeAtStep(state.step);
      state.step += 1;
    }
    // The schedule is paused while frightened, so leaving frightened always
    // resumes the mode it interrupted: that is not a change, no reversal.
    const reverse =
      previous !== null &&
      mode !== previous &&
      (mode === MODE.FRIGHTENED || previous !== MODE.FRIGHTENED);
    previous = mode;
    return { mode, reverse };
  }

  return { state, update };
}

// ── targeting ───────────────────────────────────────────────────────────────

function ghostIndex(kind) {
  if (kind === BOSS_KIND) return 0; // the boss hunts like the chaser
  const i = typeof kind === 'number' ? kind : GHOST_KINDS.indexOf(kind);
  if (!Number.isInteger(i) || i < 0 || i >= GHOST_KINDS.length) {
    throw new Error(`ghosts: unknown ghost kind ${JSON.stringify(kind)}`);
  }
  return i;
}

function clampTile(maze, x, y) {
  return {
    x: Math.min(Math.max(x, 0), maze.width - 1),
    y: Math.min(Math.max(y, 0), maze.height - 1),
  };
}

function tileOf(value, what) {
  const t = value && value.tile ? value.tile : value;
  if (!t || !Number.isInteger(t.x) || !Number.isInteger(t.y)) {
    throw new TypeError(`ghosts: ${what} must be a tile {x, y} of integers`);
  }
  return t;
}

function aheadOf(maze, pacTile, dir, n) {
  if (dir === null || dir === undefined) return clampTile(maze, pacTile.x, pacTile.y);
  const d = DIRS[dir];
  if (!d) throw new Error(`ghosts: unknown Pac-Man direction ${JSON.stringify(dir)}`);
  return clampTile(maze, pacTile.x + d.dx * n, pacTile.y + d.dy * n);
}

export function scatterCorner(maze, kind) {
  const i = ghostIndex(kind);
  return {
    x: i === 0 || i === 2 ? maze.width - 1 : 0,
    y: i < 2 ? 0 : maze.height - 1,
  };
}

function cornerOf(ghost, maze) {
  if (ghost.scatter) return { x: ghost.scatter.x, y: ghost.scatter.y };
  return scatterCorner(maze, ghost.kind);
}

export function targetTile(ghost, ctx) {
  if (!ghost) throw new TypeError('targetTile: ghost is required');
  if (!ctx || !ctx.maze || !ctx.pac) {
    throw new TypeError('targetTile: ctx needs {pac, chaser, maze}');
  }
  const { maze } = ctx;
  const pacTile = tileOf(ctx.pac, 'ctx.pac tile');
  const dir = ctx.pac.dir ?? null;

  switch (GHOST_KINDS[ghostIndex(ghost.kind)]) {
    case 'chaser':
      return { x: pacTile.x, y: pacTile.y };

    case 'ambusher':
      return aheadOf(maze, pacTile, dir, AMBUSH_AHEAD);

    case 'flanker': {
      const chaser = tileOf(ctx.chaser, 'ctx.chaser tile');
      const pivot = aheadOf(maze, pacTile, dir, FLANK_AHEAD);
      return clampTile(
        maze,
        chaser.x + 2 * (pivot.x - chaser.x),
        chaser.y + 2 * (pivot.y - chaser.y),
      );
    }

    case 'shy': {
      const me = tileOf(ghost.tile, 'ghost.tile');
      const dx = pacTile.x - me.x;
      const dy = pacTile.y - me.y;
      // Integer compare of euclidean distance: dist > 8  <=>  dist^2 > 64.
      if (dx * dx + dy * dy > SHY_RADIUS * SHY_RADIUS) {
        return { x: pacTile.x, y: pacTile.y };
      }
      return cornerOf(ghost, maze);
    }
  }
  /* c8 ignore next */
  throw new Error('unreachable');
}

export function ghostTarget(ghost, ctx, mode) {
  const effective = ghost.mode === MODE.EATEN ? MODE.EATEN : mode;
  switch (effective) {
    case MODE.SCATTER:
      return cornerOf(ghost, ctx.maze);
    case MODE.CHASE:
      return targetTile(ghost, ctx);
    case MODE.FRIGHTENED:
      return null;
    case MODE.EATEN: {
      const { maze } = ctx;
      const door = maze.ghostHouse && maze.ghostHouse.door;
      const home = door || maze.ghostStarts[ghostIndex(ghost.kind)];
      return { x: home.x, y: home.y };
    }
    default:
      throw new Error(`ghostTarget: unknown mode ${JSON.stringify(mode)}`);
  }
}

// ── movement ────────────────────────────────────────────────────────────────
//
// createGhost(spec) -> ghost, stepGhost(ghost, ctx, rng) -> events[]
//
// A ghost lives on the same integer grid as the player: x / y are the top-left
// sub-unit coordinate of its tile cell, SUB (16) per tile, x wraps through
// tunnels. It is tile-aligned when x % SUB === 0 && y % SUB === 0. ghost.tile is
// the tile its centre is in (what targeting and collision use).
//
//   spec = {kind, tile:{x,y}, dir?='left', mode?=null, house?='out',
//           speed?, frightenedSpeed?, eatenSpeed?, scatter?:{x,y}}
//
// Speed is a parameter, in sub-units per 60 steps (120 = Pac-Man's speed, which
// is PLAYER_SPEED 2 per step). speed / frightenedSpeed / eatenSpeed are plain
// integer fields so a level family can scale them. Each step
//     acc += speed;  move floor(acc / 60) sub-units;  acc %= 60
// so 60 moves exactly 1 sub-unit per step, 90 moves 3 sub-units per 2 steps, and
// nothing is ever fractional. Moving is done one sub-unit at a time, so a fast
// ghost still stops to choose a direction on every tile it crosses.
//
// Steering (stepGhost), at each tile-aligned sub-unit before leaving the tile:
//   1. candidates = open neighbours in up, left, down, right order, minus the
//      reverse of the current heading;
//   2. scatter / chase / eaten: the candidate whose tile has the least squared
//      distance to ghostTarget() wins; ties go to the earlier of up, left,
//      down, right. frightened: rng.pick over the candidates (one rng draw per
//      choice; stepGhost throws if frightened and no rng is passed);
//   3. a ghost never enters a ghost-house door unless it is eaten or leaving
//      the house.
// Exactly one exception to "never reverse": a forced dead end (no candidate but
// the reverse is open) turns round, emitting {type:'ghost_reverse',
// reason:'dead_end'}. classic1 has no dead ends.
//
// Mode changes. ctx.mode is the global mode ('scatter'|'chase'|'frightened',
// from createModeScheduler().update()). The ghost remembers the mode it last
// applied in ghost.mode. When ctx.mode differs, scatter<->chase swaps and
// entering frightened reverse the ghost ONCE, on that very step (mid-tile too,
// like the player), emitting {type:'ghost_reverse', reason:'mode'}; leaving
// frightened does not. Ghosts that are waiting / leaving the house or eaten
// never reverse on a mode change. A ghost standing exactly on the tile just
// outside the door whose way back is the door skips the reversal (it cannot
// legally take it); the mode change is still recorded. The first step a ghost
// ever takes (ghost.mode null) adopts ctx.mode without reversing.
//
// Eaten ghosts: eatGhost(ghost) sets mode 'eaten' (steers to the door at
// eatenSpeed, may pass doors). Reaching the door tile revives it: ghost.mode
// becomes ctx.mode (frightened immunity is the caller's concern), it starts
// leaving the house at once with a free heading, and {type:'ghost_home'} is
// emitted. Delays inside the house belong to the respawn logic, not here.
//
// House: house 'waiting' ghosts do not move; releaseGhost() makes them 'leaving'.
// A leaving ghost may cross doors and steers to the tile outside the door
// (whatever the mode), then becomes 'out' emitting {type:'ghost_left_house'}.

export const HOUSE = Object.freeze({ WAITING: 'waiting', LEAVING: 'leaving', OUT: 'out' });

/** Sub-units per 60 steps. 120 equals Pac-Man. */
export const GHOST_SPEED = 108;
export const FRIGHTENED_SPEED = 60;
export const EATEN_SPEED = 240;
export const SPEED_DENOMINATOR = 60;

const STEERING_MODES = new Set([MODE.SCATTER, MODE.CHASE, MODE.FRIGHTENED]);

function checkSpeed(name, value) {
  if (!Number.isInteger(value) || value < 0) {
    throw new RangeError(`ghosts: ${name} must be an integer >= 0 (sub-units per 60 steps), got ${value}`);
  }
  return value;
}

export function createGhost(spec) {
  if (!spec) throw new TypeError('createGhost: spec is required');
  const kind = spec.kind === BOSS_KIND ? BOSS_KIND : GHOST_KINDS[ghostIndex(spec.kind)];
  const t = tileOf(spec.tile, 'spec.tile');
  const dir = spec.dir === undefined ? 'left' : spec.dir;
  if (dir !== null && !Object.hasOwn(DIRS, dir)) {
    throw new Error(`createGhost: unknown direction ${JSON.stringify(dir)}`);
  }
  const house = spec.house ?? HOUSE.OUT;
  if (!Object.values(HOUSE).includes(house)) {
    throw new Error(`createGhost: unknown house state ${JSON.stringify(house)}`);
  }
  const mode = spec.mode ?? null;
  if (mode !== null && !Object.values(MODE).includes(mode)) {
    throw new Error(`createGhost: unknown mode ${JSON.stringify(mode)}`);
  }
  const ghost = {
    kind,
    x: t.x * SUB,
    y: t.y * SUB,
    tile: { x: t.x, y: t.y },
    dir,
    mode,
    house,
    speed: checkSpeed('speed', spec.speed ?? GHOST_SPEED),
    frightenedSpeed: checkSpeed('frightenedSpeed', spec.frightenedSpeed ?? FRIGHTENED_SPEED),
    eatenSpeed: checkSpeed('eatenSpeed', spec.eatenSpeed ?? EATEN_SPEED),
    acc: 0,
    decided: false, // a heading is already chosen for the tile it stands on
  };
  if (spec.scatter) ghost.scatter = { x: spec.scatter.x, y: spec.scatter.y };
  return ghost;
}

/** Put a ghost in 'eaten' mode: it heads for the door at eatenSpeed. */
export function eatGhost(ghost) {
  ghost.mode = MODE.EATEN;
  ghost.house = HOUSE.OUT;
}

/** Let a waiting ghost start leaving the house. */
export function releaseGhost(ghost) {
  if (ghost.house === HOUSE.WAITING) ghost.house = HOUSE.LEAVING;
}

/** True when the ghost sits exactly on a tile. */
export function isGhostAligned(ghost) {
  return ghost.x % SUB === 0 && ghost.y % SUB === 0;
}

/** Sub-units per 60 steps the ghost moves at in its current mode. */
export function ghostSpeed(ghost) {
  if (ghost.mode === MODE.EATEN) return checkSpeed('eatenSpeed', ghost.eatenSpeed);
  if (ghost.mode === MODE.FRIGHTENED) return checkSpeed('frightenedSpeed', ghost.frightenedSpeed);
  return checkSpeed('speed', ghost.speed);
}

function centreTile(ghost, maze) {
  const half = SUB >> 1;
  return {
    x: Math.floor((ghost.x + half) / SUB) % maze.width,
    y: Math.floor((ghost.y + half) / SUB),
  };
}

function inHouse(maze, tx, ty) {
  const { doors, interior } = maze.ghostHouse;
  for (const d of doors) if (d.x === tx && d.y === ty) return true;
  for (const t of interior) if (t.x === tx && t.y === ty) return true;
  return false;
}

// Tile just outside the primary door: the first open non-house neighbour in
// up, left, down, right order. Null for a maze without a door.
function houseExit(maze) {
  const door = maze.ghostHouse.door;
  if (!door) return null;
  for (const dir of DIR_NAMES) {
    const t = stepTile(maze, door.x, door.y, dir);
    if (t && isOpen(maze, t.x, t.y) && !inHouse(maze, t.x, t.y)) return t;
  }
  return { x: door.x, y: door.y };
}

function reversesOnModeChange(from, to) {
  if (from === null || from === MODE.EATEN || to === MODE.EATEN) return false;
  return to === MODE.FRIGHTENED || from !== MODE.FRIGHTENED;
}

function reverseForMode(ghost, maze, events) {
  if (ghost.dir === null || ghost.house !== HOUSE.OUT) return;
  const back = OPPOSITE[ghost.dir];
  const aligned = isGhostAligned(ghost);
  if (aligned) {
    // Mid-tile the way back is the tile it came from; on a tile it must be open.
    const t = stepTile(maze, ghost.x / SUB, ghost.y / SUB, back);
    if (!t || !isOpen(maze, t.x, t.y, false)) return;
  }
  ghost.dir = back;
  ghost.decided = aligned; // the reversal IS this tile's choice
  events.push({ type: 'ghost_reverse', kind: ghost.kind, dir: back, reason: 'mode' });
}

// Choose a heading at an aligned tile. False when the ghost has nowhere to go.
function chooseHeading(ghost, ctx, rng, events) {
  const { maze } = ctx;
  const tx = ghost.x / SUB;
  const ty = ghost.y / SUB;
  ghost.tile = { x: tx, y: ty };

  if (ghost.mode === MODE.EATEN) {
    const home = ghostTarget(ghost, ctx, MODE.EATEN);
    if (home.x === tx && home.y === ty) {
      ghost.mode = ctx.mode;
      ghost.house = HOUSE.LEAVING;
      ghost.dir = null;
      events.push({ type: 'ghost_home', kind: ghost.kind });
    }
  }
  if (ghost.house === HOUSE.LEAVING && !inHouse(maze, tx, ty)) {
    ghost.house = HOUSE.OUT;
    events.push({ type: 'ghost_left_house', kind: ghost.kind });
  }

  const allowDoor = ghost.mode === MODE.EATEN || ghost.house === HOUSE.LEAVING;
  const back = ghost.dir === null ? null : OPPOSITE[ghost.dir];
  const open = neighbours(maze, tx, ty, allowDoor);
  const options = open.filter((n) => n.dir !== back);

  if (options.length === 0) {
    if (open.length === 0) return false;
    // Forced dead end: the single documented exception to "never reverse".
    ghost.dir = open[0].dir;
    ghost.decided = true;
    events.push({ type: 'ghost_reverse', kind: ghost.kind, dir: ghost.dir, reason: 'dead_end' });
    return true;
  }

  let pick;
  if (ghost.house === HOUSE.LEAVING || ghost.mode !== MODE.FRIGHTENED) {
    const target =
      ghost.house === HOUSE.LEAVING
        ? houseExit(maze)
        : ghostTarget(ghost, ctx, ghost.mode);
    pick = options[0];
    if (options.length > 1 && target) {
      let best = Infinity;
      for (const n of options) {
        const dx = n.x - target.x;
        const dy = n.y - target.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < best) {
          best = d2; // strict <: up, left, down, right wins ties
          pick = n;
        }
      }
    }
  } else {
    if (!rng || typeof rng.int !== 'function') {
      throw new TypeError('stepGhost: a frightened ghost needs the seeded rng');
    }
    pick = options[rng.int(options.length)];
  }
  ghost.dir = pick.dir;
  ghost.decided = true;
  return true;
}

/**
 * Advance one ghost by exactly one fixed step. Mutates `ghost`, returns this
 * step's events (see the section comment). ctx = {maze, mode, pac, chaser}: the
 * targeting context of targetTile() plus the global mode. rng is only drawn
 * from while the ghost is frightened.
 */
export function stepGhost(ghost, ctx, rng) {
  if (!ghost) throw new TypeError('stepGhost: ghost is required');
  if (!ctx || !ctx.maze) throw new TypeError('stepGhost: ctx needs {maze, mode, pac, chaser}');
  if (!STEERING_MODES.has(ctx.mode)) {
    throw new Error(`stepGhost: ctx.mode must be scatter, chase or frightened, got ${JSON.stringify(ctx.mode)}`);
  }
  const { maze } = ctx;
  const events = [];
  const mode = ghost.mode === MODE.EATEN ? MODE.EATEN : ctx.mode;

  if (mode !== ghost.mode) {
    const from = ghost.mode;
    ghost.mode = mode;
    if (reversesOnModeChange(from, mode)) reverseForMode(ghost, maze, events);
  }
  if (ghost.house === HOUSE.WAITING) return events;

  ghost.acc += ghostSpeed(ghost);
  const moves = Math.floor(ghost.acc / SPEED_DENOMINATOR);
  ghost.acc %= SPEED_DENOMINATOR;

  const width = maze.width * SUB;
  for (let i = 0; i < moves; i++) {
    if (isGhostAligned(ghost) && !ghost.decided && !chooseHeading(ghost, ctx, rng, events)) break;
    const d = DIRS[ghost.dir];
    ghost.x = (((ghost.x + d.dx) % width) + width) % width;
    ghost.y += d.dy;
    ghost.decided = false;
  }

  ghost.tile = centreTile(ghost, maze);
  return events;
}
