// Keyboard input -> direction queue. Render-layer only (touches events, never
// window/document directly: the caller passes the event target).
//
// Directions are the strings 'up' | 'down' | 'left' | 'right', exactly what
// game.step(input) accepts. Presses queue FIFO with a cap of 4; when the queue
// is full further presses are dropped (the oldest intent is kept). One press is
// handed out per consumeDirection() call, so a fast "down, left" tap sequence
// becomes two successive turns instead of one swallowed key.
//
// Two more key kinds never reach game.step (the logic sees only directions and,
// later, ability actions): PICK keys (Digit1-3 / Numpad1-3 -> slot 1-3) and the
// RESTART key (KeyR). Each is a single pending value, taken once by
// consumePick() / consumeRestart(); the caller (main.js) takes them every frame
// and the flow decides whether the current phase may use them, so a stray digit
// during play or a stray arrow on the pick screen is simply dropped.

// ABILITY keys (D5, rule-6): Space -> 'dash', E -> 'blast'. A press is latched
// until the next step takes it, so consumeInput() hands game.step exactly
// {dir, actions: Set} per step: one queued direction and every ability pressed
// since the last step. The logic never sees a key code.

export const QUEUE_CAP = 4;

const KEY_TO_DIR = Object.freeze({
  ArrowUp: 'up',
  ArrowDown: 'down',
  ArrowLeft: 'left',
  ArrowRight: 'right',
  KeyW: 'up',
  KeyS: 'down',
  KeyA: 'left',
  KeyD: 'right',
});

/** Pure: KeyboardEvent.code -> 'up'|'down'|'left'|'right', else null. */
export function keyToDir(code) {
  return typeof code === 'string' && Object.hasOwn(KEY_TO_DIR, code)
    ? KEY_TO_DIR[code]
    : null;
}

const KEY_TO_ACTION = Object.freeze({
  Space: 'dash',
  KeyE: 'blast',
});

/** Pure: KeyboardEvent.code -> 'dash' | 'blast', else null. */
export function keyToAction(code) {
  return typeof code === 'string' && Object.hasOwn(KEY_TO_ACTION, code)
    ? KEY_TO_ACTION[code]
    : null;
}

const KEY_TO_PICK = Object.freeze({
  Digit1: 1,
  Digit2: 2,
  Digit3: 3,
  Numpad1: 1,
  Numpad2: 2,
  Numpad3: 3,
});

/** Pure: KeyboardEvent.code -> pick slot 1-3, else null. */
export function keyToPick(code) {
  return typeof code === 'string' && Object.hasOwn(KEY_TO_PICK, code)
    ? KEY_TO_PICK[code]
    : null;
}

/** Pure: is this the restart key (R)? */
export function isRestartKey(code) {
  return code === 'KeyR';
}

/** Pure: is this the reduced-motion toggle (M)? Never forwarded to game.step. */
export function isMotionKey(code) {
  return code === 'KeyM';
}

const queue = [];
const latched = new Set();
let pendingPick = null;
let pendingRestart = false;
let pendingMotion = false;

/** Takes (and clears) the latest pick slot 1-3, or null. */
export function consumePick() {
  const p = pendingPick;
  pendingPick = null;
  return p;
}

/** Takes (and clears) a pending M press (toggle reduced motion). */
export function consumeMotionToggle() {
  const m = pendingMotion;
  pendingMotion = false;
  return m;
}

/** Takes (and clears) a pending restart press. */
export function consumeRestart() {
  const r = pendingRestart;
  pendingRestart = false;
  return r;
}

/** Pure-ish: push a direction honouring the cap. Returns true if queued. */
export function pushDirection(dir) {
  if (dir === null || queue.length >= QUEUE_CAP) return false;
  queue.push(dir);
  return true;
}

/** Returns the oldest queued direction, or null when nothing is pending. */
export function consumeDirection() {
  return queue.length > 0 ? queue.shift() : null;
}

export function clearDirections() {
  queue.length = 0;
  latched.clear(); // an ability pressed on another screen must not fire later
}

/**
 * The input for ONE game.step: {dir, actions}. dir is the oldest queued turn
 * (or null), actions a fresh Set of the abilities pressed since the last call.
 */
export function consumeInput() {
  const actions = new Set(latched);
  latched.clear();
  return { dir: consumeDirection(), actions };
}

/** Drop pending pick and restart presses too (a new screen starts clean). */
export function clearPresses() {
  pendingPick = null;
  pendingRestart = false;
  pendingMotion = false;
}

/**
 * Listen for keydown on `target` (window/document in the browser, any object
 * with addEventListener/removeEventListener in tests). Clears the queue on
 * attach. Returns a detach function.
 */
export function attachKeyboard(target) {
  clearDirections();
  clearPresses();
  function onKeyDown(e) {
    // Leave browser shortcuts (Ctrl/Cmd+W, Alt+Left...) alone.
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const pick = keyToPick(e.code);
    if (pick !== null || isRestartKey(e.code)) {
      e.preventDefault();
      if (e.repeat) return;
      if (pick !== null) pendingPick = pick;
      else pendingRestart = true;
      return;
    }
    if (isMotionKey(e.code)) {
      e.preventDefault();
      if (!e.repeat) pendingMotion = true; // held M must not flicker the mode
      return;
    }
    const action = keyToAction(e.code);
    if (action !== null) {
      e.preventDefault(); // Space must not scroll the page or press a focused button
      if (!e.repeat) latched.add(action);
      return;
    }
    const dir = keyToDir(e.code);
    if (dir === null) return;
    e.preventDefault(); // stop arrows scrolling the page, even on repeats
    if (e.repeat) return; // holding a key must not flood the queue
    pushDirection(dir);
  }
  target.addEventListener('keydown', onKeyDown);
  return function detach() {
    target.removeEventListener('keydown', onKeyDown);
    clearDirections();
    clearPresses();
  };
}
