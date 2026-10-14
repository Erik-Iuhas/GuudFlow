// Level generator: shared templates + family modifiers (decision D6, rule-9).
//
//   import { generateLevel } from './levels/generate.js';
//   const level = generateLevel({ seed: cfg.seed, family: cfg.family });
//   createGame({ template: level.maze, config: level.config, seed });
//
// generateLevel({seed, family, level = 5}) -> {
//   seed         the seed asked for (as given)
//   level        the ramp level 1..5 (default 5 = base values)
//   attemptSeed  the derived seed of the attempt that passed (== seed's u32 on
//                the first try)
//   attempts     how many attempts were needed (1 almost always)
//   family       'speed' | 'cracked' | 'rush' | 'swarm' | 'boss'
//   templateId   'classic1' | 'classic2' ...
//   mirrored     true when the template was flipped left-to-right
//   template     the final ASCII rows ('c' = cracked wall tile)
//   maze         parseTemplate(template), ready for createGame
//   config       {ghostCount, ghostSpeedFactor, boss, bossHp, frightenedSteps,
//                 pelletPoints, crackedRatio, lifePickup}, see families.js
//                 (lifePickup: the seeded R15 roll, true on LIFE_PICKUP_CHANCE)
// }
//
// The R15 roll is drawn last, from rng.fork() of the attempt's rng, so it never
// shifts the template, mirror, cracks or power pellets of a seed.
//
// One attempt, all randomness from createRng(attemptSeed):
//   1. pick a template from TEMPLATES (every family can use any of them);
//   2. mirror it left-to-right or not (a coin flip);
//   3. apply the family modifier (Cracked cracks interior wall segments; Rush
//      puts 2 power pellets in each quadrant);
//   4. check: the maze parses, every open tile is connected, and every pellet
//      and power pellet is reachable from Pac-Man's start WITHOUT abilities
//      (walls, cracked walls and ghost-house doors are solid), and for Cracked
//      the realised cracked ratio is at least 0.3.
// If the check fails, the next attempt uses the next derived seed
// (attemptSeed = seed + attempt, mod 2^32), up to MAX_ATTEMPTS, then it throws.
// Same seed + family always gives the same level; there is no clock and no
// state.
//
// Mirroring flips rows only. Ghost kinds follow the G tiles in reading order, so
// a mirrored template keeps four ghosts but may swap which two share a row.

import { createRng, toSeed } from '../rng.js';
import { allPelletsReachable, isConnected, parseTemplate } from '../maze.js';
import { classic1 } from './classic1.js';
import { classic2 } from './classic2.js';
import {
  CRACKED_MIN_RATIO, FAMILY_CONFIG, LIFE_PICKUP_CHANCE, POWER_PER_QUADRANT, RAMP_LEVELS, addPowerPellets, crackWalls,
  levelConfigFor,
} from './families.js';

/** Every template, id -> rows. Ids are what generateLevel reports as templateId. */
export const TEMPLATES = Object.freeze({ classic1, classic2 });

export const MAX_ATTEMPTS = 32;

const mirrorRows = (rows) => rows.map((r) => [...r].reverse().join(''));

/** One attempt; returns the level or null when it fails the checks. */
function attempt(attemptSeed, family, level, templates) {
  const rng = createRng(attemptSeed);
  const templateId = rng.pick(Object.keys(templates));
  const mirrored = rng.next() < 0.5;
  let rows = [...templates[templateId]];
  if (mirrored) rows = mirrorRows(rows);

  let crackedRatio = 0;
  if (family === 'cracked') {
    const cracked = crackWalls(rows, rng);
    rows = cracked.rows;
    crackedRatio = cracked.ratio;
    if (crackedRatio < CRACKED_MIN_RATIO) return null;
  }

  if (family === 'rush') {
    rows = addPowerPellets(rows, rng).rows;
  }

  let maze;
  try {
    maze = parseTemplate(rows);
  } catch {
    return null;
  }
  if (!isConnected(maze) || !allPelletsReachable(maze)) return null;
  if (family === 'rush' && maze.powerPellets.length !== 4 * POWER_PER_QUADRANT) return null;

  // R15 roll: a fork of the level rng, drawn after every maze draw, so the
  // maze for a seed is the same as before the pickup existed.
  const lifePickup = rng.fork().next() < LIFE_PICKUP_CHANCE;

  return {
    templateId,
    mirrored,
    template: rows,
    maze,
    config: levelConfigFor(family, crackedRatio, level, lifePickup),
  };
}

/**
 * `level` (1..5, default 5) picks the ramp step: ghost speed and frightened
 * time. Level 5 is the base game, so old callers keep BASE values.
 * `templates` (id -> rows) defaults to TEMPLATES; tests pass their own to
 * exercise the retry path.
 */
export function generateLevel({ seed = 0, family, level = RAMP_LEVELS, templates = TEMPLATES } = {}) {
  if (!Object.hasOwn(FAMILY_CONFIG, family)) {
    throw new RangeError(`generateLevel: unknown family ${JSON.stringify(family)}`);
  }
  if (!Number.isInteger(level) || level < 1 || level > RAMP_LEVELS) {
    throw new RangeError(`generateLevel: level must be an integer 1..${RAMP_LEVELS}, got ${level}`);
  }
  const base = toSeed(seed);
  for (let n = 0; n < MAX_ATTEMPTS; n++) {
    const attemptSeed = (base + n) >>> 0;
    const found = attempt(attemptSeed, family, level, templates);
    if (found) return { seed, attemptSeed, attempts: n + 1, family, level, ...found };
  }
  throw new Error(`generateLevel: no valid ${family} level in ${MAX_ATTEMPTS} attempts from seed ${seed}`);
}

export default generateLevel;
