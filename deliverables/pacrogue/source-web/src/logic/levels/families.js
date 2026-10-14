// Level families as modifiers (decision D6, rule-9): data plus one function.
//
//   import { FAMILY_CONFIG, levelConfigFor, crackWalls,
//            wallSegments, SPEED_FACTOR, CRACKED_TARGET } from './families.js';
//
// A family changes a shared template in two ways: a `config` the game reads
// (createGame({config})) and, for Cracked only, the walls themselves.
//
// config = {ghostCount, ghostSpeedFactor, boss, bossHp, frightenedSteps,
//           pelletPoints, crackedRatio}
//   ghostCount        normal ghosts (the boss, if any, comes on top)
//   ghostSpeedFactor  multiplies every ghost speed (game.js does it in integers)
//   boss              true on the Boss level: the game spawns the big ghost
//   bossHp            the boss's hit points (5)
//   frightenedSteps   how long a power pellet frightens the ghosts (360)
//   pelletPoints      score of a normal pellet (10); a power pellet is 50
//   crackedRatio      share of interior wall segments that are cracked (0..1);
//                     the generator measures it on the finished maze
// No config field hides any part of the maze or the ghosts (rule-10).
//
//   speed    ghosts +25%                     ghostSpeedFactor 1.25
//   cracked  >= 30% of interior segments     crackedRatio >= 0.3 (target 0.35)
//   rush     8 power pellets (2 per quadrant), frightened = 1.5 x the level's ramp
//            (900 at level 1, 630 at level 4), pellets 20
//   swarm    6 ghosts                        ghostCount 6
//   boss     a big ghost with 5 HP + 4 ghosts
//
// Rush also changes the maze: addPowerPellets(rows, rng) makes exactly
// POWER_PER_QUADRANT power pellets in each quadrant (a pellet '.' becomes 'o');
// the pellet count and every walkable tile stay as they were, so reachability is
// unchanged.
//
// Wall segments. A segment is a 4-connected group of INTERIOR wall tiles
// ('#', never the outer border row/column), so each hand-drawn block is one
// segment. Groups that touch the ghost house (its door or inside tiles) are not
// counted and never cracked, so the house always looks and works the same.
// Cracking turns every tile of the chosen segments from '#' to 'c' (a solid wall
// the abilities can break later), so cracked tiles never open a path by
// themselves: reachability is unchanged.
//
// Pure data and functions: no DOM, no clock; randomness only from the rng
// passed in.

import { parseTemplate } from '../maze.js';

export const SPEED_FACTOR = 1.25;
export const SWARM_GHOSTS = 6;
export const NORMAL_GHOSTS = 4;
export const BOSS_HP = 5;
/** Rush: frightened steps = round(RUSH_FRIGHTENED_FACTOR x the level's ramp frightened steps), pellet value, power per quadrant. */
export const RUSH_FRIGHTENED_FACTOR = 1.5;
export const RUSH_PELLET_POINTS = 20;
export const POWER_PER_QUADRANT = 2;
/**
 * Per-level ramp (R16): levels 1..RAMP_LEVELS move in equal steps from the
 * start values to the end values. Level 5 is the normal game (1.0 and 360).
 */
export const RAMP_LEVELS = 5;
export const GHOST_SPEED_START = 0.8;
export const GHOST_SPEED_END = 1.0;
export const FRIGHTENED_START = 600;
export const FRIGHTENED_END = 360;
/** Required minimum share of interior segments that are cracked (R6). */
export const CRACKED_MIN_RATIO = 0.3;
/** What the modifier aims for: comfortably above the minimum. */
export const CRACKED_TARGET = 0.35;
/** Extra-life pickup (R15): share of levels that get one, seeded per level. */
export const LIFE_PICKUP_CHANCE = 0.35;
/** The pickup spawns once this share of the level's pellets is eaten. */
export const LIFE_PICKUP_AFTER = 0.5;
/** Steps the pickup stays on the board before it expires if not eaten. */
export const LIFE_PICKUP_STAY = 600;

/** The config of an unmodified level; createGame defaults to exactly this. */
export const BASE_CONFIG = Object.freeze({
  ghostCount: NORMAL_GHOSTS,
  ghostSpeedFactor: 1,
  boss: false,
  bossHp: BOSS_HP,
  frightenedSteps: 360,
  pelletPoints: 10,
  crackedRatio: 0,
  lifePickup: false,
});

/**
 * What each family changes (crackedRatio is filled in by the generator).
 * speedMultiplier and frightenedMultiplier act ON TOP of the level ramp; the
 * other fields replace the base values.
 */
export const FAMILY_CONFIG = Object.freeze({
  speed: Object.freeze({ speedMultiplier: SPEED_FACTOR }),
  cracked: Object.freeze({}),
  rush: Object.freeze({ frightenedMultiplier: RUSH_FRIGHTENED_FACTOR, pelletPoints: RUSH_PELLET_POINTS }),
  swarm: Object.freeze({ ghostCount: SWARM_GHOSTS }),
  boss: Object.freeze({ boss: true, bossHp: BOSS_HP }),
});

const round4 = (x) => Math.round(x * 10000) / 10000;

/**
 * Ramp values for a level 1..RAMP_LEVELS: {ghostSpeedFactor, frightenedSteps}.
 * Equal steps: level 1 = start, level 5 = end (L1 0.8/600 ... L5 1.0/360).
 */
export function rampFor(level) {
  if (!Number.isInteger(level) || level < 1 || level > RAMP_LEVELS) {
    throw new RangeError(`rampFor: level must be an integer 1..${RAMP_LEVELS}, got ${level}`);
  }
  const t = (level - 1) / (RAMP_LEVELS - 1);
  return Object.freeze({
    ghostSpeedFactor: round4(GHOST_SPEED_START + (GHOST_SPEED_END - GHOST_SPEED_START) * t),
    frightenedSteps: Math.round(FRIGHTENED_START + (FRIGHTENED_END - FRIGHTENED_START) * t),
  });
}

/**
 * Config object for a family at a ramp level (a fresh, frozen object). Level
 * defaults to RAMP_LEVELS (5), which gives the BASE values for an unmodified level.
 */
export function levelConfigFor(family, crackedRatio = 0, level = RAMP_LEVELS, lifePickup = false) {
  const mod = FAMILY_CONFIG[family];
  if (!mod) throw new RangeError(`levelConfigFor: unknown family ${JSON.stringify(family)}`);
  const { speedMultiplier = 1, frightenedMultiplier = 1, ...fields } = mod;
  const ramp = rampFor(level);
  return Object.freeze({
    ...BASE_CONFIG,
    ...fields,
    ghostSpeedFactor: round4(ramp.ghostSpeedFactor * speedMultiplier),
    frightenedSteps: Math.round(ramp.frightenedSteps * frightenedMultiplier),
    crackedRatio,
    lifePickup: lifePickup === true,
  });
}

const NEIGHBOURS = [[0, -1], [-1, 0], [0, 1], [1, 0]];

/**
 * Interior wall segments of a template (array of row strings), in scan order.
 * Returns [{tiles: [{x, y}, ...]}], house-adjacent groups excluded (see header).
 */
export function wallSegments(rows) {
  const maze = parseTemplate(rows);
  const { width: w, height: h } = maze;
  const house = new Set();
  for (const t of [...maze.ghostHouse.doors, ...maze.ghostHouse.interior]) house.add(t.y * w + t.x);

  const isInterior = (x, y) => x >= 1 && x < w - 1 && y >= 1 && y < h - 1;
  const isWallChar = (x, y) => isInterior(x, y) && rows[y][x] === '#';
  const seen = new Set();
  const segments = [];
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      if (!isWallChar(x, y) || seen.has(y * w + x)) continue;
      const tiles = [];
      let touchesHouse = false;
      const stack = [[x, y]];
      seen.add(y * w + x);
      while (stack.length) {
        const [cx, cy] = stack.pop();
        tiles.push({ x: cx, y: cy });
        for (const [dx, dy] of NEIGHBOURS) {
          const nx = cx + dx;
          const ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          if (house.has(ny * w + nx)) touchesHouse = true;
          if (isWallChar(nx, ny) && !seen.has(ny * w + nx)) {
            seen.add(ny * w + nx);
            stack.push([nx, ny]);
          }
        }
      }
      if (!touchesHouse) {
        tiles.sort((a, b) => a.y - b.y || a.x - b.x);
        segments.push({ tiles });
      }
    }
  }
  return segments;
}

/**
 * Crack ceil(CRACKED_TARGET * n) of the n interior wall segments, chosen with
 * `rng`. Returns {rows, segments, cracked, ratio} with ratio = cracked /
 * segments (0 when the template has no segments).
 */
export function crackWalls(rows, rng) {
  const segments = wallSegments(rows);
  const n = segments.length;
  const count = Math.ceil(n * CRACKED_TARGET);
  const chosen = rng.shuffle(segments.map((_, i) => i)).slice(0, count);
  const grid = rows.map((r) => [...r]);
  for (const i of chosen) for (const t of segments[i].tiles) grid[t.y][t.x] = 'c';
  return {
    rows: grid.map((r) => r.join('')),
    segments: n,
    cracked: count,
    ratio: n === 0 ? 0 : count / n,
  };
}

/** Quadrant 0-3 of a tile: bit 0 = right half, bit 1 = bottom half. */
function quadrantOf(x, y, w, h) {
  return (x * 2 >= w ? 1 : 0) + (y * 2 >= h ? 2 : 0);
}

/**
 * Rush: make exactly POWER_PER_QUADRANT power pellets in each quadrant of the
 * maze (the four quarters split at the middle column and row), with `rng`.
 * A quadrant short of them turns plain pellets ('.') into 'o', preferring tiles
 * at least FAR tiles from the quadrant's other power pellets and falling back
 * to any pellet; a quadrant with too many turns the extras back into '.'.
 * Only '.' and 'o' change, so walls, floor and reachability stay as they were
 * and the pellet total (dots + power pellets) is unchanged.
 * Returns {rows, power: [{x, y}] in reading order}.
 */
export function addPowerPellets(rows, rng) {
  const FAR = 6;
  const h = rows.length;
  const w = rows[0].length;
  const grid = rows.map((r) => [...r]);
  for (let q = 0; q < 4; q++) {
    const power = [];
    const dots = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (quadrantOf(x, y, w, h) !== q) continue;
        if (grid[y][x] === 'o') power.push({ x, y });
        else if (grid[y][x] === '.') dots.push({ x, y });
      }
    }
    // too many: demote seeded extras
    while (power.length > POWER_PER_QUADRANT) {
      const [t] = power.splice(rng.int(power.length), 1);
      grid[t.y][t.x] = '.';
    }
    // too few: promote seeded dots, spread out where the quadrant allows it
    while (power.length < POWER_PER_QUADRANT && dots.length > 0) {
      const apart = dots.filter((d) => power.every((p) => Math.abs(p.x - d.x) + Math.abs(p.y - d.y) >= FAR));
      const pool = apart.length > 0 ? apart : dots;
      const t = pool[rng.int(pool.length)];
      dots.splice(dots.indexOf(t), 1);
      grid[t.y][t.x] = 'o';
      power.push(t);
    }
  }
  const out = grid.map((r) => r.join(''));
  const powerTiles = [];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (out[y][x] === 'o') powerTiles.push({ x, y });
  return { rows: out, power: powerTiles };
}
