// Ability framework glue: the behaviour objects, the held-ability factory and
// the hook list. game.js is the only caller.
//
//   import { createAbility, normalizeAbilities, HOOKS } from './abilities/index.js';
//
// An ability is a data object (decision D2). behaviour lives in dash.js,
// wall_burst.js and ghost_blast.js: {id, levels, init, hooks}; registry.js stays
// the pure offer data (names, families, partners). A held ability, made here, is
//   {id, level, hooks, state}
// where `state` is the JSON-friendly object kept in game.state.abilities
// ({id, level, ...whatever init() returns, e.g. cooldown}): all mutable
// per-ability data lives there, so same seed + same inputs give the same JSON.
// An ability with no behaviour object is accepted as held data with no hooks. A
// behaviour with extraGhost: true (Ghost Blast, Chain Reaction) adds
// one ghost to the level (game.js; both together still add one).
// Combos live in combos.js; hooks may also include onWallBroken (combos only).
//
// Hooks, all optional, called by game.js for each held ability IN PICK ORDER as
// hook(api, state, ...args):
//   onLevelGen(api, state)           once, when the game is created (maze edits)
//   onInput(api, state, input)       start of each step; input = {dir, actions:Set}
//   onWallHit(api, state, hit)       Pac-Man ran into a wall; hit = {dir, tx, ty}
//   onGhostKilled(api, state, ghost, cause)  after a ghost died (not a boss hit)
//   onStep(api, state)               end of each step (cooldowns tick here)
// api = {state, maze, rng, config, emit(event), killGhost(ghost, cause),
//        hitBoss(cause), hasAbility(id), isDashing()}; see game.js.
//
// Pure module: only imports are the ability files and the registry.

import { dash } from './dash.js';
import { wall_burst } from './wall_burst.js';
import { ghost_blast } from './ghost_blast.js';
import { chain_reaction } from './chain_reaction.js';
import { magnet } from './magnet.js';
import { HOOK_NAMES, getAbility } from './registry.js';

export const HOOKS = HOOK_NAMES;
export const MAX_LEVEL = 2;

/** Behaviour objects by id. */
export const BEHAVIOURS = Object.freeze({ dash, wall_burst, ghost_blast, chain_reaction, magnet });

/**
 * Validate the abilities option of createGame and return a clean
 * [{id, level}] in the given (pick) order. Accepts an array of {id, level} or a
 * run's held Map (id -> level). Throws on anything else: unknown id, level not
 * 1 or 2, duplicate id.
 */
export function normalizeAbilities(list) {
  if (list === undefined || list === null) return [];
  const items = list instanceof Map ? [...list].map(([id, level]) => ({ id, level })) : list;
  if (!Array.isArray(items)) throw new TypeError('createGame: abilities must be an array of {id, level}');
  const seen = new Set();
  return items.map((item) => {
    if (!item || typeof item !== 'object') throw new TypeError('createGame: each ability must be {id, level}');
    const { id, level = 1 } = item;
    if (!getAbility(id)) throw new RangeError(`createGame: unknown ability ${JSON.stringify(id)}`);
    if (level !== 1 && level !== MAX_LEVEL) {
      throw new RangeError(`createGame: ability ${id} level must be 1 or 2, got ${JSON.stringify(level)}`);
    }
    if (seen.has(id)) throw new RangeError(`createGame: ability ${id} is held twice`);
    seen.add(id);
    return { id, level };
  });
}

/**
 * Make a held ability {id, level, hooks, state} from a validated {id, level}.
 * `behaviours` (id -> behaviour object) defaults to BEHAVIOURS; tests and later
 * cards can pass their own.
 */
export function createAbility({ id, level }, behaviours = BEHAVIOURS) {
  const behaviour = behaviours[id];
  return {
    id,
    level,
    hooks: behaviour ? behaviour.hooks : Object.freeze({}),
    behaviour: behaviour || null,
    state: { id, level, ...(behaviour ? behaviour.init() : {}) },
  };
}
