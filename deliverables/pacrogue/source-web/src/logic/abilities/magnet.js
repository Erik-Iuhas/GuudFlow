// Magnet: pellets drift toward Pac-Man from across the maze.
//
//   L1  pulls plain pellets within 2 tiles
//   L2  pulls within 4 tiles, and power pellets too
//   OVERDRIVE (Dash + Magnet, combos.js): while Pac-Man is dashing the radius is
//   doubled (L1 4 tiles, L2 8 tiles). Outside a dash it is the level radius.
//
// Each step, in onStep (after the ghosts have moved and the dash has ticked),
// every pellet inside the radius takes one step to the open floor tile that is
// nearest Pac-Man, if that tile is strictly nearer than its current one. A pellet
// never moves onto another pellet, a wall or a ghost-house door, so it cannot
// oscillate, and it is eaten the step after it reaches Pac-Man's tile. Pellet
// counts do not change: a pulled pellet is still a pellet on the board.
//
// Radius test: the distance between Pac-Man's centre and the pellet's tile centre,
// in sub-units, with the same integer maths as Ghost Blast (withinSub).
// Pellets are visited in index order, so the result is the same for the same state.
//
// Events: magnet_pull {tx, ty, fromTx, fromTy} per pellet moved, where (tx, ty)
// is the tile it moved INTO and (fromTx, fromTy) the tile it left (one step away).
// State (game.state.abilities entry): {id, level} only.
//
// Pure module: no DOM, no randomness.

import { DIRS, TILE, stepTile } from '../maze.js';
import { SUB, playerTile } from '../player.js';
import { withinSub } from './ghost_blast.js';

// Pellet kinds in game.state.pellets. Must match game.js (PELLET_*); they are not
// imported because game.js imports this file (a cycle).
const PELLET_NONE = 0;
const PELLET_POWER = 2;

export const MAGNET_RADIUS = Object.freeze({ 1: 2, 2: 4 }); // tiles
export const OVERDRIVE_MULTIPLIER = 2; // Dash + Magnet, while dashing

/** Wrap-aware distance along one axis of a maze that is `w` tiles wide. */
function wrapDelta(d, w) {
  const a = Math.abs(d);
  return Math.min(a, w - a);
}

/** Squared tile distance from (x, y) to Pac-Man's tile, wrapping through tunnels. */
function tileDist2(x, y, pacTile, w) {
  const dx = wrapDelta(x - pacTile.x, w);
  const dy = y - pacTile.y;
  return dx * dx + dy * dy;
}

/**
 * The open floor tile next to (tx, ty) that is strictly nearer Pac-Man than
 * (tx, ty) is, or null. Ties between neighbours go to the first in DIRS order.
 */
function nextTowards(maze, pellets, tx, ty, pacTile) {
  const w = maze.width;
  let best = null;
  let bestDist = tileDist2(tx, ty, pacTile, w);
  for (const dir of Object.keys(DIRS)) {
    const n = stepTile(maze, tx, ty, dir);
    if (!n) continue;
    const i = n.y * w + n.x;
    if (maze.tiles[i] !== TILE.FLOOR || pellets[i] !== PELLET_NONE) continue;
    const d = tileDist2(n.x, n.y, pacTile, w);
    if (d < bestDist) {
      best = n;
      bestDist = d;
    }
  }
  return best;
}

/** The pull radius in tiles for a held level, doubled by OVERDRIVE while dashing. */
export function magnetRadius(api, level) {
  const base = MAGNET_RADIUS[level];
  const overdrive = api.isDashing() && api.state.combos.some((c) => c.id === 'overdrive');
  return overdrive ? base * OVERDRIVE_MULTIPLIER : base;
}

/** Move every pellet inside the magnet radius one step toward Pac-Man. */
function pullPellets(api, st) {
  const { state, maze } = api;
  const w = maze.width;
  const pac = state.pac;
  const pacCentre = { x: pac.x + (SUB >> 1), y: pac.y + (SUB >> 1) };
  const pacTile = playerTile(pac, maze);
  const rSub = magnetRadius(api, st.level) * SUB;
  const powerToo = st.level >= 2;
  const pellets = state.pellets;

  // Pellets in range, in index order, decided before any of them moves.
  const inRange = [];
  for (let i = 0; i < pellets.length; i++) {
    const kind = pellets[i];
    if (kind === PELLET_NONE) continue;
    if (kind === PELLET_POWER && !powerToo) continue;
    const tx = i % w;
    const ty = (i - tx) / w;
    const centre = { x: tx * SUB + (SUB >> 1), y: ty * SUB + (SUB >> 1) };
    if (withinSub(pacCentre, centre, rSub, w)) inRange.push(i);
  }

  for (const i of inRange) {
    const kind = pellets[i];
    const tx = i % w;
    const ty = (i - tx) / w;
    const next = nextTowards(maze, pellets, tx, ty, pacTile);
    if (!next) continue;
    pellets[i] = PELLET_NONE;
    pellets[next.y * w + next.x] = kind;
    api.emit({ type: 'magnet_pull', tx: next.x, ty: next.y, fromTx: tx, fromTy: ty });
  }
}

export const magnet = Object.freeze({
  id: 'magnet',
  levels: Object.freeze({
    1: Object.freeze({ radius: MAGNET_RADIUS[1], powerPellets: false }),
    2: Object.freeze({ radius: MAGNET_RADIUS[2], powerPellets: true }),
  }),
  init: () => ({}),
  hooks: Object.freeze({
    onStep(api, st) {
      pullPellets(api, st);
    },
  }),
});

export default magnet;
