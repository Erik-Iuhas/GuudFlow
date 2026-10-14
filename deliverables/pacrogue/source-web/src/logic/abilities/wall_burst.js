// Wall Burst: the Space dash goes through walls and breaks them.
//
//   L1  breaks CRACKED walls in the dash's path
//   L2  also breaks solid interior walls
//   Never the outer border (row 0 / last row, column 0 / last column) and never
//   the ghost house's own walls (the ring around its door and inside), so the
//   house works as in every other level. Doors are never broken.
//
// Wall Burst has no dash of its own: it only modifies the run's one dash
// (dash_core.js; Dash changes its numbers). Without any ability the dash still
// works, it just stops at walls; with Wall Burst it goes through the walls this
// level can break.
//
// Wall breaking: game.js tells every ability about each wall Pac-Man runs into
// (onWallHit {dir, tx, ty}), including while a dash holds him against a wall.
// Only while a dash is active (state.dash.active > 0) does Wall Burst act: the
// tile becomes floor in the game's own maze copy (the shared parsed maze is never
// touched) and wall_broken {tx, ty} is emitted. Ghosts read the same maze, so they path through the gap
// from the next step on. The dash then simply carries on through the opening.
//
// State (game.state.abilities entry): {id, level} only.
//
// Pure module: only imports are the tile constants.

import { TILE } from '../maze.js';

// Tiles that belong to the ghost house: its doors, its inside and every wall
// next to them (8 neighbours). Cached per maze layout (ghostHouse is shared by
// a game's mutable copy).
const houseTiles = new WeakMap();
function protectedTiles(maze) {
  let set = houseTiles.get(maze.ghostHouse);
  if (!set) {
    set = new Set();
    const { doors, interior } = maze.ghostHouse;
    for (const t of [...doors, ...interior]) {
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) set.add((t.y + dy) * maze.width + (t.x + dx));
      }
    }
    houseTiles.set(maze.ghostHouse, set);
  }
  return set;
}

/** Can a Wall Burst of this level break the wall tile (tx, ty)? */
export function canBreak(maze, level, tx, ty) {
  if (tx <= 0 || ty <= 0 || tx >= maze.width - 1 || ty >= maze.height - 1) return false;
  const i = ty * maze.width + tx;
  const tile = maze.tiles[i];
  if (tile === TILE.CRACKED) return true;
  return level >= 2 && tile === TILE.WALL && !protectedTiles(maze).has(i);
}

export const wall_burst = Object.freeze({
  id: 'wall_burst',
  levels: Object.freeze({
    1: Object.freeze({ breaks: 'cracked' }),
    2: Object.freeze({ breaks: 'solid' }),
  }),
  init: () => ({}),
  hooks: Object.freeze({
    onWallHit(api, st, hit) {
      if (!api.isDashing()) return;
      const { maze } = api;
      if (!canBreak(maze, st.level, hit.tx, hit.ty)) return;
      maze.tiles[hit.ty * maze.width + hit.tx] = TILE.FLOOR;
      api.wallBroken(hit.tx, hit.ty); // emits wall_broken {tx, ty} and tells the combos
    },
  }),
});

export default wall_burst;
