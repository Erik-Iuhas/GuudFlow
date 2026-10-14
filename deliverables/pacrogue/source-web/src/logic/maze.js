// ASCII maze templates: parsing, tile queries, neighbour helpers, connectivity.
//
// Template legend (one string per row, all rows the same length):
//   '#'  wall
//   'c'  cracked wall      (a wall for every movement and pathing rule; a distinct
//                           tile type so drawing and abilities can tell it apart)
//   '.'  pellet            (floor + 10-point pellet)
//   'o'  power pellet      (floor + 50-point pellet)
//   ' '  empty floor
//   '-'  ghost-house door  (open to ghosts only; solid for the player)
//   'P'  player start      (exactly one)
//   'G'  ghost start       (exactly four; reading order = ghost index 0..3)
//
// Horizontal tunnels: a row whose first and last tiles are both non-wall is a
// wrap tunnel; stepping off one edge re-enters at the other. Rows open at only
// one edge are rejected. Vertical wrap does not exist; off-map rows are wall.
//
// Pure module: no DOM, no randomness, no imports.

export const TILE = Object.freeze({ FLOOR: 0, WALL: 1, DOOR: 2, CRACKED: 3 });

/** True for tiles nothing can walk through: plain and cracked walls. */
const isSolidTile = (t) => t === TILE.WALL || t === TILE.CRACKED;

/** Directions in the classic ghost tie-break order: up, left, down, right. */
export const DIRS = Object.freeze({
  up: Object.freeze({ dx: 0, dy: -1 }),
  left: Object.freeze({ dx: -1, dy: 0 }),
  down: Object.freeze({ dx: 0, dy: 1 }),
  right: Object.freeze({ dx: 1, dy: 0 }),
});
export const DIR_NAMES = Object.freeze(['up', 'left', 'down', 'right']);

const GHOST_COUNT = 4;

/**
 * Parse an ASCII template (array of row strings, or one newline-separated
 * string) into a maze. Throws Error with row/column on malformed input.
 */
export function parseTemplate(rows) {
  if (typeof rows === 'string') rows = rows.split('\n');
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('parseTemplate: template must be a non-empty array of row strings');
  }
  const height = rows.length;
  for (let y = 0; y < height; y++) {
    if (typeof rows[y] !== 'string') {
      throw new Error(`parseTemplate: row ${y} is not a string`);
    }
  }
  const width = rows[0].length;
  if (width === 0) throw new Error('parseTemplate: row 0 is empty');

  const tiles = new Uint8Array(width * height);
  const pellets = [];
  const powerPellets = [];
  const ghostStarts = [];
  const doors = [];
  let playerStart = null;

  for (let y = 0; y < height; y++) {
    const row = rows[y];
    if (row.length !== width) {
      throw new Error(
        `parseTemplate: ragged row ${y}: length ${row.length}, expected ${width}`,
      );
    }
    for (let x = 0; x < width; x++) {
      const ch = row[x];
      const i = y * width + x;
      switch (ch) {
        case '#':
          tiles[i] = TILE.WALL;
          break;
        case 'c':
          tiles[i] = TILE.CRACKED;
          break;
        case '.':
          pellets.push({ x, y });
          break;
        case 'o':
          powerPellets.push({ x, y });
          break;
        case ' ':
          break;
        case '-':
          tiles[i] = TILE.DOOR;
          doors.push({ x, y });
          break;
        case 'P':
          if (playerStart) {
            throw new Error(
              `parseTemplate: second player start 'P' at row ${y}, col ${x} (first at row ${playerStart.y}, col ${playerStart.x})`,
            );
          }
          playerStart = { x, y };
          break;
        case 'G':
          ghostStarts.push({ x, y });
          break;
        default:
          throw new Error(
            `parseTemplate: unknown character ${JSON.stringify(ch)} at row ${y}, col ${x}`,
          );
      }
    }
  }

  if (!playerStart) throw new Error("parseTemplate: missing player start 'P'");
  if (ghostStarts.length !== GHOST_COUNT) {
    throw new Error(
      `parseTemplate: expected ${GHOST_COUNT} ghost starts 'G', found ${ghostStarts.length}`,
    );
  }

  const tunnels = [];
  for (let y = 0; y < height; y++) {
    const leftOpen = !isSolidTile(tiles[y * width]);
    const rightOpen = !isSolidTile(tiles[y * width + width - 1]);
    if (leftOpen && rightOpen) tunnels.push({ y, left: 0, right: width - 1 });
    else if (leftOpen || rightOpen) {
      throw new Error(
        `parseTemplate: row ${y} is open at only one edge (tunnels need both ends open)`,
      );
    }
  }

  const maze = {
    width,
    height,
    tiles,
    pellets,
    powerPellets,
    playerStart,
    ghostStarts,
    ghostHouse: { door: doors[0] || null, doors, interior: [] },
    tunnels,
  };
  maze.ghostHouse.interior = findHouseInterior(maze);
  return maze;
}

// Interior = open regions touching a door that cannot be reached from the
// player start without crossing a door. Returned in reading order.
function findHouseInterior(maze) {
  const { doors } = maze.ghostHouse;
  if (doors.length === 0) return [];
  const outside = flood(maze, maze.playerStart, false);
  const seen = new Set();
  const interior = [];
  for (const d of doors) {
    for (const n of rawNeighbours(maze, d.x, d.y)) {
      const idx = indexOf(maze, n.x, n.y);
      if (isSolidTile(maze.tiles[idx]) || maze.tiles[idx] === TILE.DOOR) continue;
      if (outside.has(idx) || seen.has(idx)) continue;
      for (const k of flood(maze, n, false)) seen.add(k);
    }
  }
  for (const k of [...seen].sort((a, b) => a - b)) {
    interior.push({ x: k % maze.width, y: (k / maze.width) | 0 });
  }
  return interior;
}

export function indexOf(maze, tx, ty) {
  return ty * maze.width + tx;
}

export function isTunnelRow(maze, ty) {
  for (const t of maze.tunnels) if (t.y === ty) return true;
  return false;
}

/**
 * Normalise tx for row ty: in-range x passes through, tunnel rows wrap
 * horizontally, anything else off-map returns null.
 */
export function wrapX(maze, tx, ty) {
  if (ty < 0 || ty >= maze.height) return null;
  if (tx >= 0 && tx < maze.width) return tx;
  if (!isTunnelRow(maze, ty)) return null;
  return ((tx % maze.width) + maze.width) % maze.width;
}

/** TILE value at (tx, ty) with tunnel wrap; off-map counts as WALL. */
export function tileAt(maze, tx, ty) {
  const x = wrapX(maze, tx, ty);
  return x === null ? TILE.WALL : maze.tiles[ty * maze.width + x];
}

/** Solid for movement and pathing: plain walls AND cracked walls. */
export function isWall(maze, tx, ty) {
  return isSolidTile(tileAt(maze, tx, ty));
}

/** True only for cracked wall tiles (the ones abilities can break). */
export function isCracked(maze, tx, ty) {
  return tileAt(maze, tx, ty) === TILE.CRACKED;
}

export function isDoor(maze, tx, ty) {
  return tileAt(maze, tx, ty) === TILE.DOOR;
}

/**
 * Can an actor stand on (tx, ty)? Doors are open only with allowDoor (ghosts).
 */
export function isOpen(maze, tx, ty, allowDoor = false) {
  const t = tileAt(maze, tx, ty);
  return t === TILE.FLOOR || (allowDoor && t === TILE.DOOR);
}

/**
 * The tile one step from (tx, ty) in direction dir ('up'|'left'|'down'|'right'),
 * with tunnel wrap. Returns {x, y} or null if that is off-map. Does not check walls.
 */
export function stepTile(maze, tx, ty, dir) {
  const d = DIRS[dir];
  if (!d) throw new Error(`stepTile: unknown direction ${JSON.stringify(dir)}`);
  const ny = ty + d.dy;
  const nx = wrapX(maze, tx + d.dx, ny);
  return nx === null ? null : { x: nx, y: ny };
}

/**
 * Open neighbours of (tx, ty) as [{x, y, dir}] in up, left, down, right order.
 */
export function neighbours(maze, tx, ty, allowDoor = false) {
  const out = [];
  for (const dir of DIR_NAMES) {
    const t = stepTile(maze, tx, ty, dir);
    if (t && isOpen(maze, t.x, t.y, allowDoor)) out.push({ x: t.x, y: t.y, dir });
  }
  return out;
}

// All four geometric neighbours (any tile type), wrap-aware.
function rawNeighbours(maze, tx, ty) {
  const out = [];
  for (const dir of DIR_NAMES) {
    const t = stepTile(maze, tx, ty, dir);
    if (t) out.push(t);
  }
  return out;
}

function flood(maze, start, allowDoor) {
  const seen = new Set();
  if (!start || !isOpen(maze, start.x, start.y, allowDoor)) return seen;
  const queue = [start.x, start.y];
  seen.add(indexOf(maze, start.x, start.y));
  for (let q = 0; q < queue.length; q += 2) {
    for (const n of neighbours(maze, queue[q], queue[q + 1], allowDoor)) {
      const k = indexOf(maze, n.x, n.y);
      if (seen.has(k)) continue;
      seen.add(k);
      queue.push(n.x, n.y);
    }
  }
  return seen;
}

/**
 * Set of tile indices (y * width + x) reachable from startTile {x, y}, walking
 * through tunnels and ghost-house doors. Empty if startTile is a wall/off-map.
 */
export function floodFillReachable(maze, startTile) {
  return flood(maze, startTile, true);
}

/** Number of non-wall tiles (floor + door). */
export function countOpenTiles(maze) {
  let n = 0;
  for (let i = 0; i < maze.tiles.length; i++) if (!isSolidTile(maze.tiles[i])) n++;
  return n;
}

/**
 * Set of tile indices (y * width + x) Pac-Man can walk to from startTile with
 * no abilities: tunnels wrap, walls (plain and cracked) and ghost-house doors
 * are solid. Empty if startTile is not floor.
 */
export function floodFillPlayer(maze, startTile) {
  return flood(maze, startTile, false);
}

/**
 * True when every pellet and power pellet can be reached from the player start
 * without abilities (rule-9's connectivity check).
 */
export function allPelletsReachable(maze) {
  const reach = floodFillPlayer(maze, maze.playerStart);
  return [...maze.pellets, ...maze.powerPellets].every((p) => reach.has(indexOf(maze, p.x, p.y)));
}

/** True when every non-wall tile is reachable from the player start. */
export function isConnected(maze) {
  return floodFillReachable(maze, maze.playerStart).size === countOpenTiles(maze);
}
