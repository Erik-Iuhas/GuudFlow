// Combos: separate data objects that name the abilities they need (decision D2).
//
//   {id, name, requires:[ability ids], init(), hooks}
//
// A combo is active only when EVERY required ability is held (game.js builds the
// active list with activeCombos and keeps each one's JSON state in
// game.state.combos). Combo hooks run after the abilities' hooks of the same
// name, with the same (api, state, ...args) signature. Besides the ability hooks
// a combo may use onWallBroken(api, state, {tx, ty}), called when Wall Burst
// turns a wall into floor.
//
// BURST+BLAST (wall_burst + ghost_blast): a Wall Burst dash that breaks at least
// one wall fires a free Ghost Blast where the dash ENDS, i.e. around Pac-Man's
// position on the step the dash runs out (the walls broken on the way arm it,
// once per dash). It uses the same blast routine as Ghost Blast (fireBlast) with
// the held Ghost Blast's level radius, and never touches Ghost Blast's
// cooldown.
// Events: combo {name:'BURST+BLAST!', x, y, radius (tiles), radiusSub}, then the
// usual blast {x, y, radius, radiusSub, killed} and ghost_eaten (cause 'blast').
// State: {id, armed}.
//
// Pure module: no DOM, no randomness.

import { SUB } from '../player.js';
import { BLAST_RADIUS, fireBlast } from './ghost_blast.js';
import { magnetRadius } from './magnet.js';

export const burst_blast = Object.freeze({
  id: 'burst_blast',
  name: 'BURST+BLAST!',
  requires: Object.freeze(['wall_burst', 'ghost_blast']),
  init: () => ({ armed: false }),
  hooks: Object.freeze({
    onWallBroken(api, st) {
      st.armed = true;
    },
    onStep(api, st) {
      if (!st.armed || api.isDashing()) return; // wait for the dash to end
      st.armed = false;
      const tiles = BLAST_RADIUS[api.abilityLevel('ghost_blast')];
      const { pac } = api.state;
      api.emit({
        type: 'combo',
        name: burst_blast.name,
        x: pac.x + (SUB >> 1),
        y: pac.y + (SUB >> 1),
        radius: tiles,
        radiusSub: tiles * SUB,
      });
      fireBlast(api, tiles);
    },
  }),
});

// OVERDRIVE (dash + magnet): while Pac-Man dashes, the magnet's radius is doubled
// (magnet.js magnetRadius reads this combo's presence and api.isDashing(); it is
// never doubled by a dash that is not running). The combo itself only announces
// each dash that starts while it is held.
// Events: combo {name:'OVERDRIVE!', x, y, radius, radiusSub} once per dash, on the
// step the dash starts. x,y = Pac-Man's centre in sub-units; radius = the doubled
// magnet radius in tiles (the dash is running); radiusSub = radius * SUB.
// State: {id, dashing} (dashing: a dash was running at the end of the last step).
export const overdrive = Object.freeze({
  id: 'overdrive',
  name: 'OVERDRIVE!',
  requires: Object.freeze(['dash', 'magnet']),
  init: () => ({ dashing: false }),
  hooks: Object.freeze({
    onInput(api, st) {
      const dashing = api.isDashing();
      if (dashing && !st.dashing) {
        const { pac } = api.state;
        const radius = magnetRadius(api, api.abilityLevel('magnet'));
        api.emit({
          type: 'combo',
          name: overdrive.name,
          x: pac.x + (SUB >> 1),
          y: pac.y + (SUB >> 1),
          radius,
          radiusSub: radius * SUB,
        });
      }
      st.dashing = dashing;
    },
    onStep(api, st) {
      st.dashing = api.isDashing(); // the dash may have ended in this step
    },
  }),
});

/** Every combo, in a fixed order. */
export const COMBOS = Object.freeze([burst_blast, overdrive]);

/** The combos whose required abilities are all in `heldIds`. */
export function activeCombos(heldIds) {
  return COMBOS.filter((c) => c.requires.every((id) => heldIds.includes(id)));
}
