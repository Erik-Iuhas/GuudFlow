// Entry: fixed 60 Hz accumulator loop, keyboard input, Canvas 2D drawing.
// The only module that reads the clock (performance.now / Date.now).
// Ghost mode (scatter/chase/frightened) is game.state.mode, owned by game.js;
// what the screen does with events (death freeze, pops, banner) is flow.js; the
// feel (hit-stop, flash, shake, speed lines, slanted pops) is feel.js.

import { createGame } from './logic/game.js';
import { createRun } from './logic/run.js';
import { generateLevel } from './logic/levels/generate.js';
import {
  attachKeyboard, consumeInput, consumeMotionToggle, consumePick, consumeRestart, clearDirections,
  clearPresses,
} from './render/input.js';
import { drawFrame } from './render/draw.js';
import { createFeel } from './render/feel.js';
import { createSession, pickSlot, stepSession, endFrame, canRestart } from './render/flow.js';
import { integerScale, canvasSizeForScale } from './render/layout.js';
import { INK } from './render/palette.js';

const STEP_MS = 1000 / 60;
const MAX_STEPS_PER_FRAME = 5; // never spiral: at most 5 logic steps per frame
const MAX_FRAME_MS = MAX_STEPS_PER_FRAME * STEP_MS; // clamp stalls (hidden tab)

// Page background comes from the palette; index.html carries no colour literal.
document.documentElement.style.background = INK;
document.body.style.background = INK;

const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d'); // Canvas 2D only (R8.4)

// One level, built from the run's config: the generator picks the template,
// mirrors it and applies the family modifier; createGame gets the maze + config.
function buildLevel(cfg) {
  const level = generateLevel({ seed: cfg.seed, family: cfg.family, level: cfg.level });
  // cfg.held = the run's abilities [{id, level}] in pick order; cfg.lives = the run's lives.
  return createGame({
    template: level.maze,
    config: { ...level.config, lives: cfg.lives },
    seed: cfg.seed,
    abilities: cfg.held,
  });
}

// The run seed comes from the clock, here and only here; the logic gets it
// explicitly. A session opens on the pick screen before level 1.
let session;

// Reduced motion starts on under prefers-reduced-motion; the M key toggles it.
const feel = createFeel({
  reducedMotion: typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches,
});

function startRun() {
  feel.reset();
  session = createSession({ run: createRun({ seed: Date.now() >>> 0 }), buildLevel });
  clearDirections();
  clearPresses();
}
startRun();

attachKeyboard(window);

// Backing store = logical grid x an integer scale, so pixels stay crisp.
// CSS object-fit letterboxes it when the window is smaller than scale 1.
function resize() {
  const scale = integerScale(window.innerWidth, window.innerHeight);
  const { width, height } = canvasSizeForScale(scale);
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.imageSmoothingEnabled = false;
}
window.addEventListener('resize', resize);
resize();

let last = performance.now();
let acc = 0;

document.addEventListener('visibilitychange', () => {
  last = performance.now();
  acc = 0;
});

function frame(now) {
  const dt = Math.min(Math.max(now - last, 0), MAX_FRAME_MS);
  last = now;
  acc += dt;

  // Keys: 1/2/3 and R are taken every frame and handed to the flow, which
  // ignores them outside the pick screen / summary / win screen (R after a
  // ~0.5 s lockout). Directions only reach the game while it is being played.
  const pick = consumePick();
  const restart = consumeRestart();
  if (consumeMotionToggle()) feel.toggleReducedMotion(); // M: never reaches game.step
  if (restart && canRestart(session.view)) {
    startRun();
  } else if (pickSlot(session, pick)) {
    clearDirections(); // arrow presses made on the pick screen never leak into play
    feel.reset();
    acc = 0;
  }
  if (session.view.phase !== 'playing') clearDirections();

  // Logic steps only while play is live; a death or clear mid-burst stops it,
  // and the freeze / banner frames then run on render time alone. A big hit
  // starts a hit-stop (feel.hitStop steps): those steps are spent without
  // calling game.step, so the world holds still for ~66 ms.
  const due = Math.min(Math.floor(acc / STEP_MS), MAX_STEPS_PER_FRAME);
  let used = 0;
  while (used < due && session.view.phase === 'playing') {
    if (feel.hitStop > 0) feel.tickHitStop();
    else stepSession(session, consumeInput, 1, feel); // one {dir, actions} per step (D5)
    used++;
  }
  acc = session.view.phase !== 'playing' ? 0 : acc - used * STEP_MS;
  if (acc > STEP_MS) acc = 0; // hit the cap: drop the backlog

  drawFrame(ctx, session.game ? session.game.state : null, { ...session.view, feel: feel.view() });
  endFrame(session);
  feel.update();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
