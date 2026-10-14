// The feel layer: hit-stop, inverted impact frames, screen shake, speed lines and
// slanted text pops. It reads ONLY the logic event list (rule-3); the logic never
// knows it exists. DOM-free, clock-free and random-free: every number below is a
// pure function of the events seen and the frames elapsed, so bun tests it.
//
//   const feel = createFeel({ reducedMotion });
//   feel.onEvents(events, at)   per game step: big hits start hit-stop / flash /
//                               shake, ability events add pops and speed lines.
//                               `at` = {x, y} Pac-Man's centre in sub-units, the
//                               anchor for events that carry no position (DASH!)
//   feel.hitStop                steps the simulation must still skip (main.js
//                               does not call game.step while it is > 0)
//   feel.tickHitStop()          consume one skipped step
//   feel.update()               once per rendered frame, after drawing
//   feel.view()                 what draw.js paints this frame:
//     { reducedMotion, invert, shake: {x, y}, hitStop, pops, speedLines }
//   feel.toggleReducedMotion()  the M key
//   feel.reset()                a new level or run: drop everything in flight
//
// Reduced motion (R7.5) switches OFF shake, inverted frames and speed lines. The
// hit-stop stays: it is a pause, not motion. Pops stay too.
//
// Time units: hit-stop counts logic STEPS (60/s); everything else counts RENDER
// frames (60/s).

import { PINK, YELLOW, CYAN, WHITE } from './palette.js';

export const HIT_STOP_STEPS = 4; // ~66 ms
export const INVERTED_MAX_FRAMES = 2; // rule-10: an inverted impact flash is never longer than this
export const INVERT_LOCK_FRAMES = 10; // no new flash for this long (no strobing)
export const SHAKE_FRAMES = 15; // 250 ms: the shake is gone by then
export const SHAKE_MAX_PX = 14;
export const POP_LIFE_FRAMES = 45;
export const POP_MAX = 12;

// Fixed direction table: the shake is a pure function of age, not random.
const SHAKE_DIRS = Object.freeze([
  [1, 0], [-1, 1], [0, -1], [1, 1], [-1, 0], [1, -1], [0, 1], [-1, -1],
]);

/**
 * What a logic event means to the feel (pure). null = nothing to feel.
 *   hit    freeze the simulation for HIT_STOP_STEPS
 *   invert inverted impact frames (0, 1 or 2)
 *   shake  shake amplitude in logical px (scales with how big the event is)
 *   force  the flash ignores the strobe lock (death, combo, boss kill)
 */
export function eventImpact(e) {
  switch (e && e.type) {
    case 'ghost_eaten':
      // A dash kill is the player's own move landing: a harder shake than a pellet eat.
      return { hit: true, invert: 1, shake: (e.cause === 'dash' ? 6 : 4) + (e.chain >= 3 ? 2 : 0) };
    case 'blast': {
      const killed = Math.max(0, e.killed | 0);
      return { hit: true, invert: killed >= 2 ? 2 : 1, shake: 6 + 2 * Math.min(killed, 4) };
    }
    case 'wall_broken':
      return { hit: true, invert: 1, shake: 4 };
    case 'combo':
      // OVERDRIVE! fires once per dash: no heavier than a dash kill, and not forced,
      // so it never costs a 2-frame inverted flash on every dash (rule-10 lock applies).
      if (e.name === 'OVERDRIVE!') return { hit: true, invert: 1, shake: 6 };
      return { hit: true, invert: 2, shake: 10, force: true };
    case 'chain_kill':
      return { hit: true, invert: 1, shake: 5 };
    case 'boss_hit':
      return { hit: true, invert: 1, shake: 8 };
    case 'boss_killed':
      return { hit: true, invert: 2, shake: 14, force: true };
    case 'life_lost':
    case 'player_died':
      return { hit: true, invert: 2, shake: 14, force: true };
    default:
      return null;
  }
}

/** The text pop an event asks for (pure), or null. x/y in maze sub-units. */
export function popFor(e, at) {
  const here = at || { x: 0, y: 0 };
  switch (e && e.type) {
    case 'wall_broken':
      return { text: 'BURST!', x: e.tx * 16 + 8, y: e.ty * 16 + 8, outline: YELLOW };
    case 'blast':
      return { text: 'BLAST!', x: e.x, y: e.y - 6, outline: PINK };
    case 'dash_start':
      return { text: 'DASH!', x: here.x, y: here.y - 14, outline: CYAN };
    case 'chain_kill':
      return { text: 'CHAIN!', x: e.x, y: e.y, outline: YELLOW };
    case 'combo':
      return { text: e.name, x: e.x, y: e.y - 34, outline: PINK }; // the name comes from the event
    case 'life_gained':
      // Only a life actually added (gained true) pops; at full lives there is nothing to say.
      // The event has no position: the pickup is eaten on Pac-Man's tile, so use his centre.
      return e.gained ? { text: '+1 LIFE!', x: here.x, y: here.y - 14, outline: CYAN } : null;
    default:
      return null;
  }
}

/** Shake offset in logical px at `age` frames after amplitude `amp` (pure). */
export function shakeOffset(amp, age) {
  if (amp <= 0 || age >= SHAKE_FRAMES) return { x: 0, y: 0 };
  const a = amp * (1 - age / SHAKE_FRAMES);
  const [dx, dy] = SHAKE_DIRS[age % SHAKE_DIRS.length];
  return { x: Math.round(dx * a), y: Math.round(dy * a) };
}

// Speed-line lanes: fixed fractions across the screen, lengths and phases.
const LANES = Object.freeze([
  [0.04, 70, 0], [0.13, 110, 160], [0.22, 50, 310], [0.31, 90, 60], [0.40, 130, 220],
  [0.49, 60, 400], [0.58, 100, 120], [0.67, 80, 350], [0.76, 120, 20], [0.85, 55, 260],
  [0.94, 95, 190],
]);
export const SPEED_LINE_PX_PER_FRAME = 40;

/**
 * Speed lines for a dash heading `dir`, `age` frames in (pure): thin flat
 * rects parallel to the heading that stream past, opposite to the motion.
 * area = {x, y, w, h} in logical px.
 */
export function speedLineRects(dir, age, area) {
  const horizontal = dir === 'left' || dir === 'right';
  const toward = dir === 'right' || dir === 'down' ? -1 : 1; // lines run against the heading
  const span = horizontal ? area.w : area.h;
  const cross = horizontal ? area.h : area.w;
  return LANES.map(([lane, len, phase], i) => {
    const m = span + len;
    const along = (((phase + toward * age * SPEED_LINE_PX_PER_FRAME) % m) + m) % m - len;
    const thick = i % 2 === 0 ? 3 : 2;
    const across = Math.round(lane * cross);
    return horizontal
      ? { x: area.x + along, y: area.y + across, w: len, h: thick, colour: i % 3 === 0 ? CYAN : WHITE }
      : { x: area.x + across, y: area.y + along, w: thick, h: len, colour: i % 3 === 0 ? CYAN : WHITE };
  });
}

/** Create the feel layer. `reducedMotion` is the starting M-key state. */
export function createFeel({ reducedMotion = false } = {}) {
  const f = {
    reducedMotion: Boolean(reducedMotion),
    hitStop: 0,
    invert: 0,
    invertLock: 0,
    invertStreak: 0, // consecutive inverted frames already drawn
    invertPending: 0, // a forced flash held back until one plain frame has passed
    shakeAmp: 0,
    shakeAge: 0,
    dashFrames: 0,
    dashAge: 0,
    dashDir: null,
    pops: [],
    last: null, // last position any event carried: the fallback anchor
  };

  f.onEvents = (events, at) => {
    if (!events || events.length === 0) return;
    let hits = 0;
    let amp = 0;
    let invert = 0;
    let force = false;
    for (const e of events) {
      if (typeof e.x === 'number' && typeof e.y === 'number') f.last = { x: e.x, y: e.y };
      const impact = eventImpact(e);
      if (impact) {
        hits += 1;
        amp = Math.max(amp, impact.shake);
        invert = Math.max(invert, impact.invert);
        force = force || Boolean(impact.force);
      }
      const pop = popFor(e, at || f.last);
      if (pop) {
        f.pops.push({ ...pop, age: 0, life: POP_LIFE_FRAMES });
        if (f.pops.length > POP_MAX) f.pops.shift();
      }
      if (e.type === 'dash_start') {
        f.dashFrames = e.steps || 24;
        f.dashAge = 0;
        f.dashDir = e.dir;
      } else if (e.type === 'dash_end') {
        f.dashFrames = 0;
      }
    }
    if (hits === 0) return;
    // The hit-stop is a pause, not motion: it ignores reduced motion. Several
    // hits in one step do not add up; they share one freeze.
    f.hitStop = Math.max(f.hitStop, HIT_STOP_STEPS);
    // Shake grows a little with how many things were hit at once.
    const total = Math.min(SHAKE_MAX_PX, amp + Math.min(4, hits - 1));
    const stillAmp = f.shakeAmp > 0 ? f.shakeAmp * (1 - f.shakeAge / SHAKE_FRAMES) : 0;
    f.shakeAmp = Math.max(total, stillAmp); // a new hit restarts the decay
    f.shakeAge = 0;
    if (invert > 0 && (force || f.invertLock === 0)) {
      // Hard cap (rule-10): never more than INVERTED_MAX_FRAMES inverted frames in a row,
      // however many forced flashes arrive. What does not fit waits for a plain frame.
      const room = INVERTED_MAX_FRAMES - f.invertStreak;
      const want = Math.min(INVERTED_MAX_FRAMES, invert);
      if (room > 0) f.invert = Math.min(room, Math.max(f.invert, want));
      else if (force) f.invertPending = Math.max(f.invertPending, want);
      f.invertLock = INVERT_LOCK_FRAMES;
    }
  };

  f.tickHitStop = () => {
    if (f.hitStop > 0) f.hitStop -= 1;
    return f.hitStop;
  };

  f.update = () => {
    if (f.invert > 0) {
      f.invert -= 1;
      f.invertStreak += 1;
    } else {
      f.invertStreak = 0;
      if (f.invertPending > 0) {
        f.invert = f.invertPending; // the frame just drawn was plain: the held flash may start
        f.invertPending = 0;
      }
    }
    if (f.invertLock > 0) f.invertLock -= 1;
    if (f.shakeAmp > 0) {
      f.shakeAge += 1;
      if (f.shakeAge >= SHAKE_FRAMES) {
        f.shakeAmp = 0;
        f.shakeAge = 0;
      }
    }
    if (f.dashFrames > 0) {
      f.dashFrames -= 1;
      f.dashAge += 1;
    }
    if (f.pops.length > 0) {
      f.pops = f.pops.map((p) => ({ ...p, age: p.age + 1 })).filter((p) => p.age < p.life);
    }
  };

  f.view = (area = { x: 0, y: 56, w: 480, h: 496 }) => {
    const calm = f.reducedMotion;
    return {
      reducedMotion: calm,
      invert: !calm && f.invert > 0,
      shake: calm ? { x: 0, y: 0 } : shakeOffset(f.shakeAmp, f.shakeAge),
      hitStop: f.hitStop,
      pops: f.pops,
      speedLines: !calm && f.dashFrames > 0 ? speedLineRects(f.dashDir, f.dashAge, area) : [],
    };
  };

  f.setReducedMotion = (on) => {
    f.reducedMotion = Boolean(on);
  };
  f.toggleReducedMotion = () => {
    f.reducedMotion = !f.reducedMotion;
    return f.reducedMotion;
  };

  /** A new level or run: nothing in flight carries over (reduced motion stays). */
  f.reset = () => {
    f.hitStop = 0;
    f.invert = 0;
    f.invertLock = 0;
    f.invertStreak = 0;
    f.invertPending = 0;
    f.shakeAmp = 0;
    f.shakeAge = 0;
    f.dashFrames = 0;
    f.dashAge = 0;
    f.dashDir = null;
    f.pops = [];
    f.last = null;
  };

  return f;
}

export default createFeel;
