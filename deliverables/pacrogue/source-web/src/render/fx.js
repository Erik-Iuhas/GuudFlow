// Ability effects as pure data: spawn from events, age by frame, look up by age.
// No DOM, no clock, no randomness (shard directions are fixed tables), so bun
// tests every effect without a canvas. draw.js paints what the look functions
// return; flow.js owns the effect list inside the view, so a new level or a
// restart (a new view) starts with none.
//
//   fx = createFx()                         { now: 0, list: [] }
//   fx = spawnFx(fx, events)                blast / chain_kill / combo / wall_broken
//   fx = spawnTrail(fx, state)              dash afterimage while a dash is active
//   fx = advanceFx(fx)                      once per rendered frame; drops expired
//   ringLook(e, age), shardLooks(e, age), crumbLook(e, age), trailLook(e, age)
//
// Effect e = {kind, born, x, y, ...}: x/y in maze sub-units (centres), `born` the
// fx.now it appeared on. age = fx.now - born, in render frames (60/s).
//
// Everything is flat rects: rings are square frames, shards are squares.

import { PINK, YELLOW, CYAN, WHITE } from './palette.js';

export const RING_FRAMES = 16;
export const BURST_FRAMES = 18;
export const CRUMBLE_FRAMES = 12;
export const TRAIL_FRAMES = 9;
export const SLIDE_FRAMES = 4; // a magnet-pulled pellet's slide, one tile per step
export const FX_MAX = 64; // oldest effects drop past this
const TILE = 16; // sub-units per tile (logic/player.js SUB; render must not import logic)
const SLIDE_PX = 4; // a slide is a small flat square

export const RING_EDGE_PX = 4; // frame thickness
export const SHARD_DIRS = Object.freeze([
  [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1],
]);

export function createFx() {
  return { now: 0, list: [] };
}

const push = (fx, e) => {
  const list = [...fx.list, { born: fx.now, ...e }];
  while (list.length > FX_MAX) list.shift();
  return { ...fx, list };
};

/** Effects an event list asks for (never mutates `fx`). */
export function spawnFx(fx, events) {
  let out = fx;
  for (const e of events || []) {
    switch (e.type) {
      case 'blast':
        out = push(out, { kind: 'ring', colour: PINK, x: e.x, y: e.y, radiusSub: e.radiusSub });
        break;
      case 'combo': {
        // radiusSub is the event's own; fall back to radius (tiles). Neither finite: no ring, never NaN.
        const rSub = Number.isFinite(e.radiusSub) ? e.radiusSub : Number.isFinite(e.radius) ? e.radius * TILE : null;
        if (rSub === null || !Number.isFinite(e.x) || !Number.isFinite(e.y)) break;
        out = push(out, { kind: 'ring', colour: YELLOW, x: e.x, y: e.y, radiusSub: rSub });
        break;
      }
      case 'magnet_pull':
        // A pellet slides from the tile it left to the tile it now sits on.
        out = push(out, {
          kind: 'slide',
          fromX: e.fromTx * TILE + TILE / 2,
          fromY: e.fromTy * TILE + TILE / 2,
          toX: e.tx * TILE + TILE / 2,
          toY: e.ty * TILE + TILE / 2,
        });
        break;
      case 'chain_kill':
        out = push(out, { kind: 'ring', colour: CYAN, x: e.x, y: e.y, radiusSub: e.radiusSub });
        out = push(out, { kind: 'burst', x: e.x, y: e.y, radiusSub: e.radiusSub });
        break;
      case 'wall_broken':
        out = push(out, { kind: 'crumble', tx: e.tx, ty: e.ty });
        break;
      default:
    }
  }
  return out;
}

/** A dash afterimage at Pac-Man's tile while the dash (state.dash) is active. */
export function spawnTrail(fx, state) {
  const dashing = Boolean(state.dash) && state.dash.active > 0;
  if (!dashing || !state.pac) return fx;
  return push(fx, { kind: 'trail', x: state.pac.x + 8, y: state.pac.y + 8, dir: state.pac.dir });
}

/** Age every effect by one frame and drop the finished ones. */
export function advanceFx(fx) {
  const now = fx.now + 1;
  return { now, list: fx.list.filter((e) => now - e.born < lifeOf(e.kind)) };
}

export function lifeOf(kind) {
  switch (kind) {
    case 'ring': return RING_FRAMES;
    case 'burst': return BURST_FRAMES;
    case 'crumble': return CRUMBLE_FRAMES;
    case 'slide': return SLIDE_FRAMES;
    default: return TRAIL_FRAMES;
  }
}

export const ageOf = (fx, e) => fx.now - e.born;

/**
 * Square ring (pure): grows from 30 % to 100 % of the radius, white for its
 * first 3 frames then its own colour, thinning at the end. {x, y, half, edge,
 * colour} where half is the half-width of the outer square.
 */
export function ringLook(e, age) {
  const t = Math.min(1, age / (RING_FRAMES - 1));
  const half = Math.round(e.radiusSub * (0.3 + 0.7 * t));
  const edge = age > RING_FRAMES - 5 ? RING_EDGE_PX / 2 : RING_EDGE_PX;
  return { x: e.x, y: e.y, half, edge, colour: age < 3 ? WHITE : e.colour };
}

/** Eight squares flying out of a chain explosion, shrinking (pure). */
export function shardLooks(e, age) {
  const t = Math.min(1, age / (BURST_FRAMES - 1));
  const dist = 6 + Math.round(e.radiusSub * 0.7 * t);
  const size = Math.max(2, Math.round(8 * (1 - t)));
  return SHARD_DIRS.map(([dx, dy], i) => ({
    x: Math.round(e.x + dx * dist - size / 2),
    y: Math.round(e.y + dy * dist - size / 2),
    w: size,
    h: size,
    colour: i % 2 === 0 ? YELLOW : WHITE,
  }));
}

/** A broken wall: a white flash square that shrinks, plus four corner chips. */
export function crumbLook(e, age, sub = 16) {
  const t = Math.min(1, age / (CRUMBLE_FRAMES - 1));
  const size = Math.max(0, Math.round(sub * (1 - t)));
  const cx = e.tx * sub + sub / 2;
  const cy = e.ty * sub + sub / 2;
  const chip = Math.max(2, Math.round(5 * (1 - t)));
  const reach = Math.round(sub / 2 + 10 * t);
  return {
    flash: { x: Math.round(cx - size / 2), y: Math.round(cy - size / 2), w: size, h: size, colour: WHITE },
    chips: [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([dx, dy]) => ({
      x: Math.round(cx + dx * reach - chip / 2),
      y: Math.round(cy + dy * reach - chip / 2),
      w: chip,
      h: chip,
      colour: CYAN,
    })),
  };
}

/**
 * A magnet-pulled pellet: a small WHITE square that moves from its old tile
 * centre to its new one over SLIDE_FRAMES (pure). {x, y, w, h, colour}.
 */
export function slideLook(e, age) {
  const t = Math.min(1, (age + 1) / SLIDE_FRAMES);
  const x = e.fromX + (e.toX - e.fromX) * t;
  const y = e.fromY + (e.toY - e.fromY) * t;
  return { x: Math.round(x - SLIDE_PX / 2), y: Math.round(y - SLIDE_PX / 2), w: SLIDE_PX, h: SLIDE_PX, colour: WHITE };
}

/** Dash afterimage: a yellow square that shrinks, and a white streak behind it. */
export function trailLook(e, age) {
  const t = Math.min(1, age / (TRAIL_FRAMES - 1));
  const size = Math.max(2, Math.round(12 * (1 - t)));
  const horizontal = e.dir === 'left' || e.dir === 'right';
  const back = e.dir === 'left' || e.dir === 'up' ? 1 : -1;
  const len = Math.round(10 * (1 - t));
  return {
    body: { x: Math.round(e.x - size / 2), y: Math.round(e.y - size / 2), w: size, h: size, colour: YELLOW },
    streak: len < 1 ? null : horizontal
      ? { x: e.x + (back > 0 ? 0 : -len), y: e.y - 1, w: len, h: 2, colour: WHITE }
      : { x: e.x - 1, y: e.y + (back > 0 ? 0 : -len), w: 2, h: len, colour: WHITE },
  };
}
