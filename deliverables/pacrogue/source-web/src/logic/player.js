// Player (Pac-Man) movement: constant speed, buffered turns, instant reversal.
//
// Positions are integers in sub-units, SUB (16) per tile (decision D1).
// player.x / player.y are the top-left sub-unit coordinate of the entity's tile
// cell, so the player is tile-aligned (standing exactly on an intersection) when
// x % SUB === 0 && y % SUB === 0. PLAYER_SPEED divides SUB, so a player that
// starts aligned always lands exactly on every tile boundary: 8 steps per tile.
//
// Per-step order inside stepPlayer:
//   1. input   - opposite of heading reverses at once, same as heading cancels
//                any pending turn, anything else is (re)buffered with age 0.
//   2. turn    - only when tile-aligned AND the target tile is open.
//   3. move    - PLAYER_SPEED sub-units, or stop flush against a wall.
//   4. ageing  - the buffer ages by one step; it is dropped once its age
//                exceeds TURN_BUFFER_STEPS.
// A press made s steps of travel before an intersection therefore reaches the
// junction with age s: taken for s = 0..6, dropped for s >= 7. Ages count
// logic steps only, never wall-clock time.
//
// Pure module: no DOM, no randomness. Only import is the maze helpers.

import { DIRS, isOpen, stepTile } from './maze.js';

export const SUB = 16;
export const PLAYER_SPEED = 2;
export const TURN_BUFFER_STEPS = 6;

export const OPPOSITE = Object.freeze({
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
});

/**
 * Fresh player on the maze's start tile, tile-aligned and standing still
 * (dir null until the first input).
 *   x, y      top-left sub-unit coordinate of the tile cell
 *   dir       heading ('up'|'down'|'left'|'right') or null; kept while blocked
 *             so the renderer can keep facing the wall
 *   moving    true when the last step moved the player
 *   buffer    pending turn {dir, age} or null
 */
export function createPlayer(maze) {
  const s = maze.playerStart;
  return {
    x: s.x * SUB,
    y: s.y * SUB,
    dir: null,
    moving: false,
    buffer: null,
  };
}

/** True when the player sits exactly on a tile (an intersection candidate). */
export function isAligned(player) {
  return player.x % SUB === 0 && player.y % SUB === 0;
}

/**
 * Tile the player's centre is in, as {x, y}. Use this for pellet pickup and
 * collision; it flips halfway between two tiles and honours tunnel wrap.
 */
export function playerTile(player, maze) {
  const half = SUB >> 1;
  const tx = Math.floor((player.x + half) / SUB) % maze.width;
  return { x: tx, y: Math.floor((player.y + half) / SUB) };
}

function canMove(maze, tx, ty, dir) {
  const t = stepTile(maze, tx, ty, dir);
  return t !== null && isOpen(maze, t.x, t.y);
}

/**
 * Advance the player by exactly one fixed step. Mutates `player` and returns
 * the events of this step (possibly empty):
 *   {type:'player_reverse', dir}
 *   {type:'player_turn', dir}            turn taken while already heading somewhere
 *   {type:'wall_hit', dir, tx, ty}       just stopped; (tx, ty) is the wall tile
 * inputDir is 'up'|'down'|'left'|'right', or null/undefined for "no new press".
 */
export function stepPlayer(player, maze, inputDir = null) {
  if (inputDir !== null && !Object.hasOwn(DIRS, inputDir)) {
    throw new TypeError(`stepPlayer: invalid direction ${JSON.stringify(inputDir)}`);
  }
  const events = [];

  // 1. input
  if (inputDir !== null) {
    if (player.dir !== null && inputDir === OPPOSITE[player.dir]) {
      player.dir = inputDir;
      player.buffer = null;
      events.push({ type: 'player_reverse', dir: inputDir });
    } else if (inputDir === player.dir) {
      player.buffer = null;
    } else {
      player.buffer = { dir: inputDir, age: 0 };
    }
  }

  // 2. turn + 3. move
  let moved = false;
  if (isAligned(player)) {
    const tx = player.x / SUB;
    const ty = player.y / SUB;
    if (player.buffer && canMove(maze, tx, ty, player.buffer.dir)) {
      const first = player.dir === null; // leaving a standstill is not a turn
      player.dir = player.buffer.dir;
      player.buffer = null;
      if (!first) events.push({ type: 'player_turn', dir: player.dir });
    }
    if (player.dir !== null) {
      if (canMove(maze, tx, ty, player.dir)) {
        moved = true;
      } else if (player.moving) {
        const wall = stepTile(maze, tx, ty, player.dir);
        events.push({
          type: 'wall_hit',
          dir: player.dir,
          tx: wall ? wall.x : tx + DIRS[player.dir].dx,
          ty: wall ? wall.y : ty + DIRS[player.dir].dy,
        });
      }
    }
  } else {
    // Mid-tile: the tile ahead is the one we are already travelling into.
    moved = player.dir !== null;
  }

  if (moved) {
    const d = DIRS[player.dir];
    const w = maze.width * SUB;
    player.x = (((player.x + d.dx * PLAYER_SPEED) % w) + w) % w;
    player.y += d.dy * PLAYER_SPEED;
  }
  player.moving = moved;

  // 4. age the buffer
  if (player.buffer) {
    player.buffer.age += 1;
    if (player.buffer.age > TURN_BUFFER_STEPS) player.buffer = null;
  }

  return events;
}
