// Run module: one roguelike run of 5 levels. Owns the level index, the seed,
// the held abilities and offer generation. DOM-free and deterministic.
//
//   import { createRun } from './run.js';
//   const run = createRun({ seed: 1234 });
//   run.status            // 'picking' (before level 1)
//   run.currentOffers()   // 3 distinct offers (2 on the 5th pick screen when only 2 are left)
//   run.pick(2)           // slot 1-3 (the 1/2/3 keys) or an ability id -> events
//   run.status            // 'playing'; run.levelConfig() tells the generator what to build
//   run.levelCleared(score, lives)  // -> 'picking' (levels 1-4) or 'won' (level 5)
//   run.playerDied(score)    // -> 'over'
//
// Fields (read-only for callers):
//   seed          the seed as an unsigned 32-bit integer
//   levelIndex    0-4; the level being played, or about to be played while picking
//   families      5 family names: levels 1-4 are a seeded permutation of
//                 speed/cracked/rush/swarm, level 5 is always boss
//   held          Map id -> level (1 | 2). Insertion order is pick order; levelling
//                 an ability up keeps its original position
//   score         last score reported through levelCleared / playerDied
//   lives         lives left for the next level (START_LIVES at the start;
//                 levelCleared(score, lives) stores what the level ended with)
//   status        'picking' | 'playing' | 'won' | 'over'
//
// Flow: picking(level 1) -> playing -> levelCleared -> picking(level 2) -> ... ->
// picking(level 5) -> playing -> levelCleared -> won. That is 5 pick screens
// (before level 1 and after levels 1-4) and none after level 5.
//
// Every mutating call returns an array of events (decision D2); an ignored call
// (wrong status, bad pick) returns []. Event shapes:
//   {type:'ability_picked', id, level, leveledUp, slot}
//   {type:'level_started', levelIndex, level, family}      level = levelIndex + 1
//   {type:'level_cleared', level, score}                   levels 1-4: pick screen next
//   {type:'run_won',  score, held}
//   {type:'run_over', seed, levelReached, score, held}     levelReached 1-5
// `held` in events is an array [{id, level}] in pick order (JSON-friendly).
//
// Randomness: everything comes from createRng(seed). The run rng first shuffles
// the families, then forks one independent generator per level for level
// generation and one per pick screen for offers, so offers never depend on how
// many times currentOffers() was called or on anything else the game consumed.

import { createRng } from './rng.js';
import { START_LIVES } from './game.js';
import {
  FAMILIES, OFFER_IDS, comboPartners, getAbility, suitedFor,
} from './abilities/registry.js';

export const LEVEL_COUNT = 5;
export const OFFER_COUNT = 3;
export const MAX_ABILITY_LEVEL = 2;

/** Why an offer is in its slot. Slots that cannot be filled fall back to 'random'. */
export const OFFER_REASONS = Object.freeze({
  SUITED: 'suited',   // slot 1: fits the next level's family
  UPGRADE: 'upgrade', // slot 2: level-up of a held ability
  COMBO: 'combo',     // slot 2: unheld partner of a held ability
  RANDOM: 'random',   // slot 3, or a fallback for slot 1 / 2
});

function heldList(held) {
  return [...held].map(([id, level]) => ({ id, level }));
}

/**
 * Build the 3 offers for one pick screen.
 *
 * Slot 1: an ability suited to `family` (random among the suited ones that are
 *         still available, i.e. not already level 2).
 * Slot 2: a level-up of a held level-1 ability, or an unheld combo partner of a
 *         held ability (random among both kinds), excluding slot 1's pick.
 * Slot 3: random among everything still available.
 * FALLBACK: when slot 1 or 2 has no candidate (nothing suited is available;
 * nothing held yet, so no level-up or partner), that slot is filled from the
 * random pool exactly like slot 3, so there are 3 distinct offers whenever 3 are available.
 * "Available" = built (OFFER_IDS) and not held at level 2; abilities already
 * placed in an earlier slot are excluded. With 4 built abilities, picks 1-4 always
 * have at least 3 available; on pick 5 (after two level-2 abilities) only 2 may be
 * left, and then the list has 2 offers, never a duplicate and never a level-2 one.
 */
export function generateOffers(rng, family, held) {
  const available = OFFER_IDS.filter((id) => (held.get(id) ?? 0) < MAX_ABILITY_LEVEL);
  const chosen = [];
  const reasons = [];

  const take = (candidates, reason) => {
    const open = candidates.filter((id) => !chosen.includes(id));
    const rest = available.filter((a) => !chosen.includes(a));
    if (rest.length === 0) return; // nothing left to offer: a shorter list, never a duplicate
    chosen.push(open.length > 0 ? rng.pick(open) : rng.pick(rest));
    reasons.push(open.length > 0 ? reason : OFFER_REASONS.RANDOM);
  };

  // Slot 1.
  take(suitedFor(family).map((a) => a.id).filter((id) => available.includes(id)), OFFER_REASONS.SUITED);

  // Slot 2: level-ups and combo partners. The reason follows what was drawn.
  const upgrades = available.filter((id) => held.has(id));
  const partners = available.filter(
    (id) => !held.has(id) && [...held.keys()].some((h) => comboPartners(h).includes(id)),
  );
  take([...upgrades, ...partners], OFFER_REASONS.UPGRADE);
  if (reasons[1] === OFFER_REASONS.UPGRADE && !held.has(chosen[1])) reasons[1] = OFFER_REASONS.COMBO;

  // Slot 3: seeded random.
  take(available, OFFER_REASONS.RANDOM);

  return chosen.map((id, i) => {
    const a = getAbility(id);
    const owned = held.get(id) ?? 0;
    return Object.freeze({
      slot: i + 1,
      id,
      name: a.name,
      description: a.description,
      reason: reasons[i],
      level: owned + 1,        // level the ability will have after the pick
      leveledUp: owned > 0,
    });
  });
}

/** Create a run. All randomness derives from `seed`; same seed, same run. */
export function createRun({ seed = 0 } = {}) {
  const rng = createRng(seed);

  // Draw order is fixed: families, then 5 level generators, then 5 offer generators.
  const families = [...rng.shuffle(FAMILIES.slice(0, 4)), FAMILIES[4]];
  const levelRngs = families.map(() => rng.fork());
  const offerRngs = families.map(() => rng.fork());

  const run = {
    seed: rng.seed,
    levelIndex: 0,
    families,
    held: new Map(),
    score: 0,
    lives: START_LIVES,
    status: 'picking',
  };

  let offers = null; // cached for the current pick screen

  function openPickScreen() {
    run.status = 'picking';
    offers = generateOffers(offerRngs[run.levelIndex], families[run.levelIndex], run.held);
  }

  openPickScreen();

  run.currentOffers = () => (run.status === 'picking' ? offers.slice() : []);

  /** Pick by slot (1-3, matching keys 1/2/3) or by offered ability id. */
  run.pick = (choice) => {
    if (run.status !== 'picking') return [];
    const offer = typeof choice === 'string'
      ? offers.find((o) => o.id === choice)
      : Number.isInteger(choice) ? offers[choice - 1] : undefined;
    if (!offer) return [];
    run.held.set(offer.id, offer.level); // Map.set on an existing key keeps pick order
    offers = null;
    run.status = 'playing';
    return [
      { type: 'ability_picked', id: offer.id, level: offer.level, leveledUp: offer.leveledUp, slot: offer.slot },
      { type: 'level_started', levelIndex: run.levelIndex, level: run.levelIndex + 1, family: families[run.levelIndex] },
    ];
  };

  run.levelCleared = (score = run.score, lives = run.lives) => {
    if (run.status !== 'playing') return [];
    run.score = score;
    run.lives = lives;
    if (run.levelIndex === LEVEL_COUNT - 1) {
      run.status = 'won';
      return [{ type: 'run_won', score, held: heldList(run.held) }];
    }
    const cleared = run.levelIndex + 1;
    run.levelIndex += 1;
    openPickScreen();
    return [{ type: 'level_cleared', level: cleared, score }];
  };

  run.playerDied = (score = run.score) => {
    if (run.status !== 'playing') return [];
    run.score = score;
    run.status = 'over';
    return [{ type: 'run_over', seed: run.seed, levelReached: run.levelIndex + 1, score, held: heldList(run.held) }];
  };

  /** What the level generator needs for the level at levelIndex. */
  run.levelConfig = () => ({
    levelIndex: run.levelIndex,
    level: run.levelIndex + 1,
    family: families[run.levelIndex],
    seed: levelRngs[run.levelIndex].seed,
    held: heldList(run.held),
    lives: run.lives,
  });

  return run;
}
