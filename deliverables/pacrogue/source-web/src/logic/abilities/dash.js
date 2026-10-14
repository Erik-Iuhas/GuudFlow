// Dash (ability): makes the run's default dash stronger. Data only.
//
// Every run has one Space dash (dash_core.js, decision D7): charges, a dash of
// double speed, a safe window after it, a recharge. Holding Dash changes the
// numbers of that dash, nothing else:
//
//   level   charges   dash_steps   recharge_steps
//   L1      3         30           420   (7 s per charge)
//   L2      4         36           300   (5 s per charge)
//
// Not held: DASH_DEFAULTS in dash_core.js apply (the 'dash' action works with
// no ability at all). Only the keys listed here override the defaults; the safe
// window and the kill points stay at their defaults.
//
// Ability data object (decision D2): {id, levels, init, hooks}. It has no hooks:
// game.js reads `levels[level]` when it builds state.dash, and keeps the timing
// in state.dash, not in this ability's own state ({id, level} only).
//
// Pure module: no DOM, no randomness, no imports.

/** Dash parameters per level (dash_core.js createDashState names). */
export const DASH_LEVELS = Object.freeze({
  1: Object.freeze({ charges: 3, dash_steps: 30, recharge_steps: 420 }),
  2: Object.freeze({ charges: 4, dash_steps: 36, recharge_steps: 300 }),
});

export const dash = Object.freeze({
  id: 'dash',
  levels: DASH_LEVELS,
  init: () => ({}),
  hooks: Object.freeze({}),
});

export default dash;
