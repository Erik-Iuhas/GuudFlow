// Canvas 2D drawing of the maze, pellets, Pac-Man and the score strip.
//
//   drawFrame(ctx, game.state, view)
//
// view = { maze, phase?, invert?, pops?, seed?, hud?, score?, summary?, offers? }
//   maze   game.maze (read-only); the state holds no walls.
//   The rest is render/flow.js's view: phase ('picking'|'playing'|'frozen'|
//   'cleared'|'summary'|'won') picks the screen or panel, invert > 0 draws the
//   frame palette-inverted, pops are the ghost-eat scores, hud the level n/5 /
//   family / held-ability chips, score the run total. All optional except maze
//   (not needed while picking: that screen has no maze and no state).
//   The layout is recomputed from ctx.canvas.width/height every call, so
//   resizing the canvas needs no extra bookkeeping.
//
// Reads state, never writes it. Colours come only from palette.js. Everything
// is axis-aligned fillRect on integer coordinates under an integer scale with
// imageSmoothingEnabled = false: walls have sharp corners and no blur. The one
// curve is Pac-Man himself (a pie, as he must be).
//
// Canvas 2D only (R8.4): it draws on the 2D context it is handed, nothing else.
// The only logic import is the SUB constant.

import { SUB } from '../logic/player.js';
import { ageOf, crumbLook, ringLook, shardLooks, slideLook, trailLook } from './fx.js';
import { INK, PINK, YELLOW, CYAN, WHITE } from './palette.js';
import { computeLayout, LOGICAL_W, LOGICAL_H, HUD_H, FOOT_H } from './layout.js';
import { chipsWithCooldowns, dashInfo, popLook } from './flow.js';
import {
  FONT_LABEL, FONT_VALUE, drawDashPips, drawHudRun, drawLives, drawMotionIndicator, drawPanel, drawPickScreen, panelMessage,
} from './screens.js';

// Tile codes and pellet codes mirror logic/maze.js (TILE) and logic/game.js
// (PELLET_*); tests/draw.test.js pins them so they cannot drift.
export const TILE_WALL = 1;
export const TILE_DOOR = 2;
export const TILE_CRACKED = 3;
export const PELLET_DOT = 1;
export const PELLET_POWER = 2;

// Sizes in logical px (= sub-units). "Reference scale" is scale 1.
export const WALL_EDGE_PX = 3; // outline thickness, must stay >= 3
export const DOT_PX = 4;
export const POWER_PX = 10;
export const PAC_RADIUS = 7;
export const DOOR_BAR_PX = 4;
export const POWER_BLINK_TICKS = 14; // visible for 14 ticks, hidden for 14
export const CHOMP_PERIOD_TICKS = 12;
export const CHOMP_MAX_HALF = 0.8; // radians, widest half-angle of the mouth
export const CHOMP_MIN_HALF = 0.04;
export const CHOMP_IDLE_HALF = 0.4; // mouth held while standing still

// Extra-life pickup (R15). The render cannot import logic/levels/families.js
// (draw.js may only import SUB), so the timings are mirrored here and pinned by
// tests/feel.test.js against LIFE_PICKUP_STAY.
export const PICKUP_STAY_STEPS = 600; // mirrors families.js LIFE_PICKUP_STAY
export const PICKUP_BLINK_STEPS = 120; // blinks in the last 120 steps before it expires
export const PICKUP_BLINK_TICKS = 8; // on for 8 steps, off for 8 steps while blinking
export const PICKUP_PX = 12; // a CYAN block, bigger than a dot (4) and a power pellet (10)

const DIR_ANGLE = {
  right: 0,
  down: Math.PI / 2,
  left: Math.PI,
  up: -Math.PI / 2,
};

/** Is the power pellet visible on this tick? Visible on tick 0. */
export function powerPelletVisible(tick) {
  return Math.floor(tick / POWER_BLINK_TICKS) % 2 === 0;
}

/** Half-angle of Pac-Man's mouth (radians) for a tick; frozen while idle. */
export function mouthHalfAngle(tick, moving) {
  if (!moving) return CHOMP_IDLE_HALF;
  const phase = (tick % CHOMP_PERIOD_TICKS) / CHOMP_PERIOD_TICKS;
  const tri = phase < 0.5 ? phase * 2 : 2 - phase * 2; // 0 -> 1 -> 0
  return CHOMP_MIN_HALF + (CHOMP_MAX_HALF - CHOMP_MIN_HALF) * tri;
}

/** Direction string -> facing angle in radians (null faces right). */
export function facingAngle(dir) {
  return Object.hasOwn(DIR_ANGLE, dir) ? DIR_ANGLE[dir] : 0;
}

// Off-map counts as open, so the outer rim of the maze gets an outline too.
function isWallTile(maze, tx, ty) {
  if (tx < 0 || ty < 0 || tx >= maze.width || ty >= maze.height) return false;
  // Cracked walls are walls too; until the cracked-wall drawing card gives them
  // their own look they are drawn like plain walls so no solid tile looks empty.
  const t = maze.tiles[ty * maze.width + tx];
  return t === TILE_WALL || t === TILE_CRACKED;
}

// A cracked wall (breakable by Wall Burst): no solid outline, instead a PINK
// dashed frame and a zigzag crack through the middle, so it reads at a glance as
// "different from the cyan walls". Rects are tile-local; pure so bun can check.
export const CRACK_DASH_PX = 4;
export function crackedRects(px, py) {
  const S = SUB;
  const E = WALL_EDGE_PX;
  const D = CRACK_DASH_PX;
  const out = [];
  for (let o = 0; o < S; o += 2 * D) {
    out.push([px + o, py, D, E], [px + o, py + S - E, D, E], [px, py + o, E, D], [px + S - E, py + o, E, D]);
  }
  out.push([px + 8, py + 4, 2, 4], [px + 6, py + 7, 2, 3], [px + 8, py + 9, 2, 3]); // the crack
  return out;
}

function drawWalls(ctx, maze) {
  const S = SUB;
  const E = WALL_EDGE_PX;
  const { width, height } = maze;

  // Every wall tile first gets its ink body, so each one is drawn and nothing
  // that ran under the maze (speed lines) shows through a wall. Cyan outline strips on every exposed side, plus a square in
  // each inner corner (both neighbours wall, diagonal open) so joins stay
  // mitred instead of notched.
  ctx.fillStyle = INK;
  for (let ty = 0; ty < height; ty++) {
    for (let tx = 0; tx < width; tx++) {
      if (isWallTile(maze, tx, ty)) ctx.fillRect(tx * S, ty * S, S, S);
    }
  }
  ctx.fillStyle = CYAN;
  for (let ty = 0; ty < height; ty++) {
    for (let tx = 0; tx < width; tx++) {
      if (!isWallTile(maze, tx, ty)) continue;
      const px = tx * S;
      const py = ty * S;
      if (maze.tiles[ty * width + tx] === TILE_CRACKED) {
        ctx.fillStyle = PINK;
        for (const r of crackedRects(px, py)) ctx.fillRect(r[0], r[1], r[2], r[3]);
        ctx.fillStyle = CYAN;
        continue;
      }
      const up = isWallTile(maze, tx, ty - 1);
      const down = isWallTile(maze, tx, ty + 1);
      const left = isWallTile(maze, tx - 1, ty);
      const right = isWallTile(maze, tx + 1, ty);
      if (!up) ctx.fillRect(px, py, S, E);
      if (!down) ctx.fillRect(px, py + S - E, S, E);
      if (!left) ctx.fillRect(px, py, E, S);
      if (!right) ctx.fillRect(px + S - E, py, E, S);
      if (up && left && !isWallTile(maze, tx - 1, ty - 1)) ctx.fillRect(px, py, E, E);
      if (up && right && !isWallTile(maze, tx + 1, ty - 1)) ctx.fillRect(px + S - E, py, E, E);
      if (down && left && !isWallTile(maze, tx - 1, ty + 1)) ctx.fillRect(px, py + S - E, E, E);
      if (down && right && !isWallTile(maze, tx + 1, ty + 1)) {
        ctx.fillRect(px + S - E, py + S - E, E, E);
      }
    }
  }

  // Ghost-house doors: a flat pink bar across the tile.
  ctx.fillStyle = PINK;
  for (let ty = 0; ty < height; ty++) {
    for (let tx = 0; tx < width; tx++) {
      if (maze.tiles[ty * width + tx] === TILE_DOOR) {
        ctx.fillRect(tx * S, ty * S + ((S - DOOR_BAR_PX) >> 1), S, DOOR_BAR_PX);
      }
    }
  }
}

function drawPellets(ctx, maze, state) {
  const { pellets, tick } = state;
  if (!pellets) return;
  const w = maze.width;
  const half = SUB >> 1;

  ctx.fillStyle = WHITE;
  const dotOff = half - (DOT_PX >> 1);
  for (let i = 0; i < pellets.length; i++) {
    if (pellets[i] !== PELLET_DOT) continue;
    ctx.fillRect((i % w) * SUB + dotOff, Math.floor(i / w) * SUB + dotOff, DOT_PX, DOT_PX);
  }

  if (!powerPelletVisible(tick)) return;
  ctx.fillStyle = YELLOW;
  const powOff = half - (POWER_PX >> 1);
  for (let i = 0; i < pellets.length; i++) {
    if (pellets[i] !== PELLET_POWER) continue;
    ctx.fillRect((i % w) * SUB + powOff, Math.floor(i / w) * SUB + powOff, POWER_PX, POWER_PX);
  }
}

/**
 * Is the extra-life pickup drawn at `age` steps on the board (pure)? Solid for
 * most of its stay; in the last PICKUP_BLINK_STEPS it blinks on and off.
 */
export function pickupVisible(age) {
  const left = PICKUP_STAY_STEPS - age;
  if (left > PICKUP_BLINK_STEPS) return true;
  return Math.floor(age / PICKUP_BLINK_TICKS) % 2 === 0;
}

/**
 * Rects of the pickup on tile (tx, ty), tile-local (pure): a CYAN block centred
 * in the tile with an INK plus cut into it, so it reads as "+1" and never as a
 * pellet. Returned as [x, y, w, h, colour] with colour a palette constant.
 */
export function pickupRects(tx, ty) {
  const x = tx * SUB + ((SUB - PICKUP_PX) >> 1);
  const y = ty * SUB + ((SUB - PICKUP_PX) >> 1);
  const bar = 4; // thickness of the plus
  const off = (PICKUP_PX - bar) >> 1; // where the plus' bars start across the block
  return [
    [x, y, PICKUP_PX, PICKUP_PX, CYAN],
    [x + 2, y + off, PICKUP_PX - 4, bar, INK],
    [x + off, y + 2, bar, PICKUP_PX - 4, INK],
  ];
}

function drawPickup(ctx, state) {
  const p = state.pickup;
  if (!p || !pickupVisible(p.age)) return;
  for (const [x, y, w, h, colour] of pickupRects(p.tx, p.ty)) {
    ctx.fillStyle = colour;
    ctx.fillRect(x, y, w, h);
  }
}

function pacPie(ctx, cx, cy, angle, half) {
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, PAC_RADIUS, angle + half, angle + Math.PI * 2 - half);
  ctx.closePath();
  ctx.fill();
}

export const INVULN_BLINK_STEPS = 4; // Pac-Man flips visible / hidden every 4 steps while invulnerable

/** True on the frames Pac-Man is hidden while invulnerable (pure). Ghosts are never hidden. */
export function pacBlinkHidden(invuln) {
  return invuln > 0 && Math.floor(invuln / INVULN_BLINK_STEPS) % 2 === 0;
}

function drawPac(ctx, maze, state) {
  const pac = state.pac;
  if (!pac) return;
  if (pacBlinkHidden(state.invuln)) return;
  const cx = pac.x + (SUB >> 1);
  const cy = pac.y + (SUB >> 1);
  const angle = facingAngle(pac.dir);
  const half = mouthHalfAngle(state.tick, pac.moving);
  const mazeW = maze.width * SUB;

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, mazeW, maze.height * SUB);
  ctx.clip(); // tunnel wrap: nothing may leak past the maze edge
  ctx.fillStyle = YELLOW;
  pacPie(ctx, cx, cy, angle, half);
  // pac.x wraps into [0, mazeW) and is a tile's top-left, so the centre is
  // never left of 8: only the right edge can straddle. Draw the wrapped twin.
  if (cx + PAC_RADIUS > mazeW) pacPie(ctx, cx - mazeW, cy, angle, half);
  ctx.restore();
}

// ── ghosts ──────────────────────────────────────────────────────────────────
// A ghost is {kind, x, y} in sub-units (top-left of its tile cell, like pac) or
// {kind, tile:{x,y}}; mode 'eaten' draws eyes only. Flat rectangles only: a
// square-shouldered body with a notched, sawtooth skirt.
export const GHOST_PX = 14; // body width/height, centred in the 16 px tile
export const FRIGHT_BLINK_STEPS = 120; // last 2 s of fright blink
export const FRIGHT_BLINK_TICKS = 8;

// Every ghost is a body + outline pair, so no ghost relies on hue alone.
// Frightened is ink with a white outline and a pupil-less face; it blinks to a
// white body for the last FRIGHT_BLINK_STEPS steps.
const GHOST_STYLE = Object.freeze({
  chaser: Object.freeze({ body: PINK, outline: WHITE }),
  ambusher: Object.freeze({ body: WHITE, outline: PINK }),
  flanker: Object.freeze({ body: CYAN, outline: WHITE }),
  shy: Object.freeze({ body: INK, outline: PINK }),
});
const FRIGHT_STYLE = Object.freeze({ body: INK, outline: WHITE });
const FRIGHT_BLINK = Object.freeze({ body: WHITE, outline: INK });

/** Body + outline colours for a ghost (pure, tested). */
export function ghostStyle(ghost, frightenedTimer = 0, tick = 0) {
  if (frightenedTimer > 0) {
    const blink =
      frightenedTimer <= FRIGHT_BLINK_STEPS &&
      Math.floor(tick / FRIGHT_BLINK_TICKS) % 2 === 1;
    return blink ? FRIGHT_BLINK : FRIGHT_STYLE;
  }
  return GHOST_STYLE[ghost.kind] || GHOST_STYLE.chaser;
}

/** Body colour for a ghost on a given tick/frightened timer (pure, tested). */
export function ghostColor(ghost, frightenedTimer = 0, tick = 0) {
  return ghostStyle(ghost, frightenedTimer, tick).body;
}

function ghostOrigin(ghost) {
  if (ghost.tile) return { x: ghost.tile.x * SUB, y: ghost.tile.y * SUB };
  return { x: ghost.x, y: ghost.y };
}

// Body silhouette grown by `g` px on every side: square shoulders, clipped top
// corners and a three-tooth sawtooth skirt. g = 1 gives the outline ring.
// `r(dx, dy, w, h)` paints a rect in body space (origin = body top-left), so the
// same shape serves the 1x ghosts and the 2x boss.
function ghostShape(r, g) {
  const W = GHOST_PX;
  r(-g, 2 - g, W + 2 * g, W - 6 + 2 * g); // body
  r(2 - g, -g, W - 4 + 2 * g, 2 + g); // flat top
  for (const tx of [0, 5, 10]) r(tx - g, W - 4, 4 + 2 * g, 4 + g);
}

/** Body scale of a ghost: 2 for the boss (size 2), else 1. */
export function ghostScale(ghost) {
  return ghost.size >= 2 ? ghost.size : 1;
}

/** Top-left of a ghost's body in maze px (the boss body is centred on its tile). */
export function ghostBodyOrigin(ghost) {
  const o = ghostOrigin(ghost);
  const k = ghostScale(ghost);
  return {
    x: o.x + (SUB >> 1) - ((GHOST_PX * k) >> 1),
    y: o.y + (SUB >> 1) - ((GHOST_PX * k) >> 1),
  };
}

export const PIP_PX = 6;
export const PIP_GAP = 4;

/**
 * HP pips above a boss (pure): one square per max HP, centred over the body,
 * `filled` while that HP is left. Empty for ghosts without hp.
 */
export function bossPips(ghost) {
  if (!Number.isInteger(ghost.maxHp) || ghost.maxHp < 1) return [];
  const o = ghostBodyOrigin(ghost);
  const k = ghostScale(ghost);
  const total = ghost.maxHp * PIP_PX + (ghost.maxHp - 1) * PIP_GAP;
  const x0 = o.x + ((GHOST_PX * k - total) >> 1);
  const y = o.y - PIP_PX - 4;
  return Array.from({ length: ghost.maxHp }, (_, i) => ({
    x: x0 + i * (PIP_PX + PIP_GAP),
    y,
    w: PIP_PX,
    h: PIP_PX,
    filled: i < Math.max(0, ghost.hp ?? 0),
  }));
}

function drawGhost(ctx, ghost, frightened, tick, frightenedTimer) {
  const eaten = ghost.mode === 'eaten';
  const k = ghostScale(ghost);
  const o = ghostBodyOrigin(ghost);
  const r = (dx, dy, w, h) => ctx.fillRect(o.x + dx * k, o.y + dy * k, w * k, h * k);
  const W = GHOST_PX;
  // The ghost's own mode decides (a ghost revived inside the power window is
  // immune and follows scatter/chase); the global timer only drives the blink.
  const fright = !eaten && (ghost.mode === undefined ? frightened : ghost.mode === 'frightened');
  const style = ghostStyle(ghost, fright ? frightenedTimer : 0, tick);

  if (!eaten) {
    ctx.fillStyle = style.outline;
    ghostShape(r, 1);
    ctx.fillStyle = style.body;
    ghostShape(r, 0);
    // Gaps between the skirt teeth show the ink background.
    ctx.fillStyle = INK;
    r(4, W - 4, 1, 4);
    r(9, W - 4, 1, 4);
  }

  if (fright) {
    // Scared face: two squares and a flat mouth bar, never pupils.
    ctx.fillStyle = style.body === INK ? WHITE : INK;
    r(3, 4, 2, 2);
    r(9, 4, 2, 2);
    r(3, 9, 8, 1);
  } else {
    const look = ghostLook(ghost);
    // White-bodied ghosts get ink eye sockets with white pupils so eyes read.
    const white = !eaten && style.body === WHITE;
    ctx.fillStyle = white ? INK : WHITE; // eye whites
    r(2, 3, 4, 5);
    r(8, 3, 4, 5);
    ctx.fillStyle = white ? WHITE : INK; // pupils shift toward travel direction
    r(3 + look.dx, 5 + look.dy, 2, 2);
    r(9 + look.dx, 5 + look.dy, 2, 2);
  }

  // Boss HP pips: a filled white square per HP left, a pink frame per HP lost.
  for (const pip of bossPips(ghost)) {
    ctx.fillStyle = pip.filled ? WHITE : PINK;
    ctx.fillRect(pip.x, pip.y, pip.w, pip.h);
    if (!pip.filled) {
      ctx.fillStyle = INK;
      ctx.fillRect(pip.x + 2, pip.y + 2, pip.w - 4, pip.h - 4);
    }
  }
}

function ghostLook(ghost) {
  switch (ghost.dir) {
    case 'left': return { dx: -1, dy: 0 };
    case 'right': return { dx: 1, dy: 0 };
    case 'up': return { dx: 0, dy: -2 };
    case 'down': return { dx: 0, dy: 1 };
    default: return { dx: 0, dy: 0 };
  }
}

function drawGhosts(ctx, state) {
  const ghosts = state.ghosts;
  if (!ghosts || ghosts.length === 0) return;
  const frightened = state.frightenedTimer > 0;
  for (const g of ghosts) drawGhost(ctx, g, frightened, state.tick, state.frightenedTimer);
}

// ── ability effects (render/fx.js) ──────────────────────────────────────────
function drawFx(ctx, fx) {
  if (!fx || fx.list.length === 0) return;
  for (const e of fx.list) {
    const age = ageOf(fx, e);
    if (e.kind === 'ring') {
      const r = ringLook(e, age);
      const x = r.x - r.half;
      const y = r.y - r.half;
      const w = r.half * 2;
      ctx.fillStyle = r.colour;
      ctx.fillRect(x, y, w, r.edge);
      ctx.fillRect(x, y + w - r.edge, w, r.edge);
      ctx.fillRect(x, y, r.edge, w);
      ctx.fillRect(x + w - r.edge, y, r.edge, w);
    } else if (e.kind === 'burst') {
      for (const q of shardLooks(e, age)) {
        ctx.fillStyle = q.colour;
        ctx.fillRect(q.x, q.y, q.w, q.h);
      }
    } else if (e.kind === 'crumble') {
      const c = crumbLook(e, age, SUB);
      for (const q of [c.flash, ...c.chips]) {
        if (q.w <= 0) continue;
        ctx.fillStyle = q.colour;
        ctx.fillRect(q.x, q.y, q.w, q.h);
      }
    } else if (e.kind === 'slide') {
      const q = slideLook(e, age);
      ctx.fillStyle = q.colour;
      ctx.fillRect(q.x, q.y, q.w, q.h);
    } else if (e.kind === 'trail') {
      const t = trailLook(e, age);
      for (const q of [t.streak, t.body]) {
        if (!q) continue;
        ctx.fillStyle = q.colour;
        ctx.fillRect(q.x, q.y, q.w, q.h);
      }
    }
  }
}

// ── pops ────────────────────────────────────────────────────────────────────
// Ghost-eat scores (200/400/800/1600) and the feel layer's BURST!/BLAST!/DASH!/
// CHAIN!/combo names, at their event position, in maze space. White text, an
// outline of look.outlinePx (>= 3) in PINK / YELLOW / CYAN made from eight
// hard-offset copies (no strokeText, so no rounded joins), and the whole pop
// slanted look.skewDeg (>= 10) degrees by a shear transform.
const POP_OFFSETS = [
  [-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1],
];

function drawPops(ctx, pops, mazeW) {
  if (!pops || pops.length === 0) return;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const pop of pops) {
    const look = popLook(pop, mazeW);
    if (!look.visible) continue;
    ctx.save();
    ctx.translate(look.x, look.y);
    // x' = x - tan(skew) * y: the top of the letters leans right.
    if (typeof ctx.transform === 'function') ctx.transform(1, 0, -Math.tan((look.skewDeg * Math.PI) / 180), 1, 0, 0);
    ctx.font = `bold ${look.size}px "Courier New", monospace`;
    ctx.fillStyle = look.outline;
    for (const [dx, dy] of POP_OFFSETS) {
      ctx.fillText(look.text, dx * look.outlinePx, dy * look.outlinePx);
    }
    ctx.fillStyle = WHITE;
    ctx.fillText(look.text, 0, 0);
    ctx.restore();
  }
}

// ── speed lines (Dash) ──────────────────────────────────────────────────────
function drawSpeedLines(ctx, lines) {
  if (!lines || lines.length === 0) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, HUD_H, LOGICAL_W, LOGICAL_H - HUD_H - FOOT_H);
  ctx.clip();
  for (const l of lines) {
    ctx.fillStyle = l.colour;
    ctx.fillRect(l.x, l.y, l.w, l.h);
  }
  ctx.restore();
}

// ── end-of-level panel ──────────────────────────────────────────────────────
// Plain flat panel; nothing animated (screens.js paints it). view.phase decides
// when there is one: the death freeze ('frozen') shows none, only the inverted
// flash and a held frame. Without a view phase (older callers) state.cleared /
// state.dead decide.
export function endMessage(state, view) {
  return panelMessage(state, view);
}

function drawEndMessage(ctx, state, view) {
  const msg = endMessage(state, view);
  if (msg) drawPanel(ctx, state, view, msg);
}

// ── inverted impact frame ───────────────────────────────────────────────────
// Palette-pure inversion: the roles swap, INK <-> WHITE and PINK <-> CYAN;
// YELLOW (Pac-Man, pellets) becomes an ink silhouette so it stays readable on
// the white flash. Nothing outside the five palette colours is ever produced.
export const INVERT_MAP = Object.freeze({
  [INK]: WHITE,
  [WHITE]: INK,
  [PINK]: CYAN,
  [CYAN]: PINK,
  [YELLOW]: INK,
});

function invertedCtx(ctx) {
  return new Proxy(ctx, {
    get(target, key) {
      const v = target[key];
      return typeof v === 'function' ? v.bind(target) : v;
    },
    set(target, key, value) {
      const mapped = (key === 'fillStyle' || key === 'strokeStyle') && Object.hasOwn(INVERT_MAP, value);
      target[key] = mapped ? INVERT_MAP[value] : value;
      return true;
    },
  });
}

function drawHud(ctx, state, view) {
  const pad = 16;
  ctx.textBaseline = 'top';

  ctx.textAlign = 'left';
  ctx.font = FONT_LABEL;
  ctx.fillStyle = CYAN;
  ctx.fillText('SCORE', pad, 10);
  ctx.font = FONT_VALUE;
  ctx.fillStyle = YELLOW;
  const score = view && view.score !== undefined ? view.score : state.score;
  ctx.fillText(String(score | 0).padStart(6, '0'), pad, 24);

  // Dash charge pips come from state.dash, so every run has them, ability or not.
  drawDashPips(ctx, dashInfo(state.dash));
  // Remaining lives (R14.4), from state.lives: one icon per life, outside the view.hud guard.
  drawLives(ctx, state.lives);
  if (view && view.hud) {
    // level n/5, family, held-ability chips with their cooldowns, the key hint
    drawHudRun(ctx, { ...view.hud, held: chipsWithCooldowns(view.hud.held, state.abilities) });
  }
  ctx.textBaseline = 'top';

  ctx.textAlign = 'right';
  ctx.font = FONT_LABEL;
  ctx.fillStyle = CYAN;
  ctx.fillText('DOTS', LOGICAL_W - pad, 10);
  ctx.font = FONT_VALUE;
  // The strip never announces a death: the freeze, the flash and the summary do.
  if (state.cleared) {
    ctx.fillStyle = CYAN;
    ctx.fillText('CLEAR', LOGICAL_W - pad, 24);
  } else {
    ctx.fillStyle = YELLOW;
    ctx.fillText(String(state.pelletsLeft | 0).padStart(3, '0'), LOGICAL_W - pad, 24);
  }

  // Flat divider under the strip.
  ctx.fillStyle = CYAN;
  ctx.fillRect(0, HUD_H - 4, LOGICAL_W, 3);
}

export function drawFrame(rawCtx, state, view) {
  const picking = Boolean(view) && view.phase === 'picking';
  if (!view || (!view.maze && !picking)) throw new TypeError('drawFrame: view.maze is required');
  const feel = view && view.feel ? view.feel : null;
  // With a feel layer the flash is its call (reduced motion turns it off);
  // without one the view's own invert counter (the death flash) decides.
  const flash = feel ? feel.invert : view.invert > 0;
  const ctx = flash ? invertedCtx(rawCtx) : rawCtx;
  const maze = view.maze || { width: 28, height: 31 }; // the pick screen draws none
  const canvas = ctx.canvas || {};
  const layout = computeLayout({
    canvasWidth: canvas.width || LOGICAL_W,
    canvasHeight: canvas.height || LOGICAL_H,
    mazeWidth: maze.width,
    mazeHeight: maze.height,
  });

  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.lineJoin = 'miter';
  ctx.miterLimit = 10;

  // Letterbox bars and background.
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, layout.canvasWidth, layout.canvasHeight);

  // Everything else lives on the fixed logical grid at an integer scale.
  ctx.setTransform(layout.scale, 0, 0, layout.scale, layout.offsetX, layout.offsetY);
  ctx.beginPath();
  ctx.rect(0, 0, LOGICAL_W, LOGICAL_H);
  ctx.clip();

  if (picking) {
    drawPickScreen(ctx, view);
    if (feel) drawMotionIndicator(ctx, feel.reducedMotion);
    ctx.restore();
    return;
  }

  // Screen shake: the whole logical screen moves; the clip and the ink behind
  // it stay put, so the edge never shows anything but ink.
  if (feel && (feel.shake.x !== 0 || feel.shake.y !== 0)) ctx.translate(feel.shake.x, feel.shake.y);

  drawHud(ctx, state, view);
  if (feel) drawMotionIndicator(ctx, feel.reducedMotion);

  // R13 / rule-10: nothing may hide a wall, a ghost or Pac-Man. Speed lines go
  // first (the wall bodies and everything after paint over them); the pops go
  // under Pac-Man and the ghosts, which are always the last things drawn.
  if (feel) drawSpeedLines(ctx, feel.speedLines);

  ctx.save();
  ctx.translate(layout.mazeX, layout.mazeY);
  drawWalls(ctx, maze);
  drawPellets(ctx, maze, state);
  drawPickup(ctx, state);
  drawFx(ctx, view.fx);
  // Pops keep their own colours through a flash (white face + coloured outline
  // would collapse into one ink blob once YELLOW inverts to INK).
  drawPops(rawCtx, view.pops, maze.width * SUB);
  if (feel) drawPops(rawCtx, feel.pops, maze.width * SUB);
  drawPac(ctx, maze, state);
  drawGhosts(ctx, state);
  ctx.restore();

  drawEndMessage(ctx, state, view);

  ctx.restore();
}

export default drawFrame;
