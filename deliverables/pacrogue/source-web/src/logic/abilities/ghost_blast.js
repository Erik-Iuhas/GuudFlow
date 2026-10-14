// Ghost Blast: the E key fires a shockwave around Pac-Man that kills every
// ghost within a radius.
//
//   L1  radius 2 tiles, cooldown 480 steps (8 s)
//   L2  radius 3 tiles, cooldown 300 steps (5 s)
//
// Distance is euclidean between Pac-Man's centre and the ghost's centre in
// sub-units (so it works mid-tile), compared in integers:
//   dx^2 + dy^2 <= (radius * SUB)^2      (exactly on the radius is inside)
// dx wraps through the horizontal tunnels. Every ghost in range dies through
// game.killGhost-style scoring (api.killGhost, cause 'blast'): a frightened
// ghost scores the chain value, any other a flat 200. Ghosts that cannot be
// harmed (waiting in the house, already eaten) are left alone, the boss loses
// 1 HP.
//
// The cooldown starts when the blast fires and counts down in onStep, so it can
// fire again exactly `cooldown` steps later; ability_ready tells the HUD when.
//
// Events: blast {x, y, radius, radiusSub, killed} (x, y: Pac-Man's centre in
// sub-units; radius in tiles; radiusSub the same in sub-units, for drawing; killed
// = ghosts hit), then one ghost_eaten per kill; ability_ready {id}.
// State: {id, level, cooldown}.
//
// Pure module: no DOM, no randomness. Only import is SUB.

import { SUB } from '../player.js';

export const BLAST_RADIUS = Object.freeze({ 1: 2, 2: 3 });
export const BLAST_COOLDOWN = Object.freeze({ 1: 480, 2: 300 });

/** Are a and b (anything with x, y in sub-units) within rSub sub-units? Integer maths. */
export function withinSub(a, b, rSub, mazeWidth) {
  const wrap = mazeWidth * SUB;
  let dx = Math.abs(b.x - a.x);
  dx = Math.min(dx, wrap - dx);
  const dy = b.y - a.y;
  return dx * dx + dy * dy <= rSub * rSub;
}

/** Is the ghost within `tiles` tiles of Pac-Man (centres, integer maths)? */
export function inBlastRange(pac, ghost, tiles, mazeWidth) {
  return withinSub(pac, ghost, tiles * SUB, mazeWidth);
}

/**
 * The blast routine, shared with the BURST+BLAST combo: a shockwave of `tiles`
 * radius around Pac-Man's current position. Emits blast first, then one
 * ghost_eaten (cause 'blast') per kill; returns how many ghosts died. Touches no
 * cooldown.
 */
export function fireBlast(api, tiles) {
  const { pac } = api.state;
  const event = {
    type: 'blast',
    x: pac.x + (SUB >> 1),
    y: pac.y + (SUB >> 1),
    radius: tiles,
    radiusSub: tiles * SUB,
    killed: 0,
  };
  api.emit(event); // first, so the ring precedes the kills it causes
  let killed = 0;
  for (const ghost of api.state.ghosts) {
    if (inBlastRange(pac, ghost, tiles, api.maze.width) && api.killGhost(ghost, 'blast')) killed += 1;
  }
  event.killed = killed;
  return killed;
}

export const ghost_blast = Object.freeze({
  id: 'ghost_blast',
  extraGhost: true, // holding it adds one ghost (game.js, rule-5)
  levels: Object.freeze({
    1: Object.freeze({ radius: BLAST_RADIUS[1], cooldown: BLAST_COOLDOWN[1] }),
    2: Object.freeze({ radius: BLAST_RADIUS[2], cooldown: BLAST_COOLDOWN[2] }),
  }),
  init: () => ({ cooldown: 0 }),
  hooks: Object.freeze({
    onInput(api, st, input) {
      if (!input.actions.has('blast') || st.cooldown > 0) return;
      st.cooldown = BLAST_COOLDOWN[st.level];
      fireBlast(api, BLAST_RADIUS[st.level]);
    },
    onStep(api, st) {
      if (st.cooldown > 0) {
        st.cooldown -= 1;
        if (st.cooldown === 0) api.emit({ type: 'ability_ready', id: st.id });
      }
    },
  }),
});

export default ghost_blast;
