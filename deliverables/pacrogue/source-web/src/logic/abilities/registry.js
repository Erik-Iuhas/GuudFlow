// Ability registry: the data every other module reads about an ability.
//
//   import { ABILITIES, POOL_IDS, OFFER_IDS, getAbility, comboPartners,
//            suitedFor, FAMILIES } from './abilities/registry.js';
//
// Decision D2: an ability is a plain data object with hooks. The registry stays
// pure offer data (every `hooks` object here is empty); the behaviour objects
// {id, levels, init, hooks} live next to it: dash.js, wall_burst.js and
// ghost_blast.js, wired up by index.js.
// Registry entries are frozen; a *held* ability is {id, level} kept by run.js.
//
// Entry fields:
//   id          stable snake_case id (what picks, offers and held use)
//   name        short UI name
//   description one line shown on the pick screen
//   suited      level families the ability is good in (offer slot 1 looks here)
//   partners    ids it forms a combo with (always symmetric; slot 2 looks here)
//   offerOnly   true for a listed but unbuilt placeholder: it can be held as data
//               but is never offered, so it is in neither POOL_IDS nor OFFER_IDS
//               (none at present: all five abilities are built)
//   hooks       {onStep, onInput, onGhostKilled, onWallHit, onLevelGen}: empty here
//
// Pure module: no DOM, no randomness, no imports.

/** Level families. Levels 1-4 are a seeded permutation of the first four. */
export const FAMILIES = Object.freeze(['speed', 'cracked', 'rush', 'swarm', 'boss']);

/** The hook names an ability may implement (documented contract for later cards). */
export const HOOK_NAMES = Object.freeze(['onStep', 'onInput', 'onGhostKilled', 'onWallHit', 'onLevelGen']);

function def(entry) {
  return Object.freeze({
    offerOnly: false,
    ...entry,
    suited: Object.freeze([...entry.suited]),
    partners: Object.freeze([...entry.partners]),
    hooks: Object.freeze({}),
  });
}

export const ABILITIES = Object.freeze([
  def({
    id: 'wall_burst',
    name: 'Wall Burst',
    description: 'Your dash smashes through walls and leaves a gap behind.',
    suited: ['cracked'],
    partners: ['dash', 'chain_reaction'],
  }),
  def({
    id: 'ghost_blast',
    name: 'Ghost Blast',
    description: 'Fire a shockwave that blasts every nearby ghost.',
    suited: ['swarm', 'boss'],
    partners: ['chain_reaction', 'dash', 'magnet'],
  }),
  def({
    id: 'dash',
    name: 'Dash',
    description: 'More dash charges, a longer burst and a faster recharge.',
    suited: ['speed', 'rush'],
    partners: ['wall_burst', 'ghost_blast', 'magnet'],
  }),
  def({
    id: 'chain_reaction',
    name: 'Chain Reaction',
    description: 'Every ghost you kill explodes and takes its neighbours with it.',
    suited: ['swarm', 'rush'],
    partners: ['ghost_blast', 'wall_burst', 'magnet'],
  }),
  def({
    id: 'magnet',
    name: 'Magnet',
    description: 'Pellets fly to you from across the maze.',
    suited: ['speed', 'rush'],
    partners: ['ghost_blast', 'chain_reaction', 'dash'],
  }),
]);

/** Ids of the built abilities (everything except offer-only placeholders). */
export const POOL_IDS = Object.freeze(ABILITIES.filter((a) => !a.offerOnly).map((a) => a.id));

/** Ids that can appear in an offer: only abilities that are built (R3.10). */
export const OFFER_IDS = POOL_IDS;

const BY_ID = new Map(ABILITIES.map((a) => [a.id, a]));

/** Registry entry for an id, or undefined. */
export function getAbility(id) {
  return BY_ID.get(id);
}

/** Ids that combine with `id` (empty array for an unknown id). */
export function comboPartners(id) {
  const a = BY_ID.get(id);
  return a ? a.partners : [];
}

/** Abilities (registry order) suited to a family; empty for an unknown family. */
export function suitedFor(family) {
  return ABILITIES.filter((a) => a.suited.includes(family));
}
