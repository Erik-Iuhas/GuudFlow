// Chain Reaction: a passive. A ghost that dies explodes and kills the ghosts
// next to it.
//
//   L1  a killed ghost kills ghosts within 1.5 tiles of it (24 sub-units);
//       the ghosts it kills do NOT explode
//   L2  2.5 tiles (40 sub-units), and chained kills explode too (they cascade)
//
// Runs from the onGhostKilled hook, so it triggers on every kill: eating a
// frightened ghost, a Ghost Blast, another explosion. Chained kills are made
// with cause 'chain' (game.killGhost route: normal scoring, ghost_eaten events),
// which is how L1 tells the original kill from a chained one. Distance is
// integer squared distance between ghost positions in sub-units (exactly on the
// radius is inside, tunnels wrap). It cannot loop: a ghost that is already dead
// (eaten) or waiting in the house cannot be killed again, so each ghost dies at
// most once per cascade; the boss only loses 1 HP to an explosion.
//
// Events: chain_kill {x, y, radius (tiles), radiusSub, depth} once per
// explosion, BEFORE the kills it causes; x, y is the exploding ghost's centre in
// sub-units and depth is 1 for an explosion of the original kill, 2 for one
// caused by that, and so on (L2 only).
// State (game.state.abilities entry): {id, level, depth}; depth is the live
// cascade depth, 0 between kills.
//
// Pure module: no DOM, no randomness. Imports SUB and the shared distance test.

import { SUB } from '../player.js';
import { withinSub } from './ghost_blast.js';

/** Kill cause of a ghost that died in a chain explosion. */
export const CHAIN_CAUSE = 'chain';

export const CHAIN_RADIUS = Object.freeze({ 1: 1.5, 2: 2.5 });
/** The same radii in sub-units (integers: 1.5 * 16, 2.5 * 16). */
export const CHAIN_RADIUS_SUB = Object.freeze({ 1: 24, 2: 40 });

export const chain_reaction = Object.freeze({
  id: 'chain_reaction',
  extraGhost: true, // holding it adds one ghost (game.js, rule-5)
  levels: Object.freeze({
    1: Object.freeze({ radius: CHAIN_RADIUS[1], radiusSub: CHAIN_RADIUS_SUB[1], cascades: false }),
    2: Object.freeze({ radius: CHAIN_RADIUS[2], radiusSub: CHAIN_RADIUS_SUB[2], cascades: true }),
  }),
  init: () => ({ depth: 0 }),
  hooks: Object.freeze({
    onGhostKilled(api, st, ghost, cause) {
      if (cause === CHAIN_CAUSE && st.level < 2) return; // L1: chained kills stay quiet
      const rSub = CHAIN_RADIUS_SUB[st.level];
      st.depth += 1;
      try {
        api.emit({
          type: 'chain_kill',
          x: ghost.x + (SUB >> 1),
          y: ghost.y + (SUB >> 1),
          radius: CHAIN_RADIUS[st.level],
          radiusSub: rSub,
          depth: st.depth,
        });
        for (const other of api.state.ghosts) {
          if (other !== ghost && withinSub(ghost, other, rSub, api.maze.width)) {
            api.killGhost(other, CHAIN_CAUSE); // re-enters this hook for L2 cascades
          }
        }
      } finally {
        st.depth -= 1;
      }
    },
  }),
});

export default chain_reaction;
