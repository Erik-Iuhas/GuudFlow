// Dash core: the one Space dash every run has (decision D7, rule-11).
//
//   import { createDashState, startDash, tickDash, refillDash, isSafe, DASH_DEFAULTS } from './dash_core.js';
//
// Pure functions over one plain JSON object, game.state.dash (game.js owns it
// and does the moving, the kills and the events' delivery; this file owns the
// numbers):
//
//   charges          dashes available now (0 .. maxCharges)
//   maxCharges       the cap
//   active           dash steps left (0 = not dashing); Pac-Man moves at double
//                    speed while it is above 0
//   safe             steps left of the safe window AFTER a dash (0 = none)
//   recharge         steps counted towards the next charge (0 while full): the
//                    HUD's recharge progress is recharge / rechargeSteps
//   rechargeSteps    steps per charge
//   dashSteps        length of a dash in steps
//   safeStepsAfter   length of the safe window after a dash
//   killPoints       score for a non-frightened ghost killed by the dash
//   bossHit          true once this dash has hurt the boss (once per dash)
//
// Params use the requirement's names (snake_case) and fill from DASH_DEFAULTS:
//   charges, dash_steps, safe_steps_after, recharge_steps, dash_kill_points.
// Dash (abilities/dash.js DASH_LEVELS) overrides charges, dash_steps and
// recharge_steps per level; everything else stays at the defaults.
//
// Timing (all counted at the END of a step, by tickDash, so a dash that starts
// on step T lasts exactly dashSteps steps, T .. T+dashSteps-1):
//   - startDash spends one charge, sets active = dashSteps, clears bossHit. It is
//     refused (false, nothing changes) with no charge, while already dashing, or
//     with no heading.
//   - isSafe is true while active > 0 or safe > 0: a ghost touch never kills
//     Pac-Man. When the last active step ends, safe = safeStepsAfter, so the
//     next safeStepsAfter steps are safe too.
//   - the recharge counter runs whenever charges < maxCharges, also mid-dash:
//     one charge comes back every rechargeSteps steps (the step that spent the
//     charge is the first one counted).
//   - refillDash (a power pellet) restores every charge at once and resets the
//     counter.
//
// Events (returned, game.js puts them in the step's list):
//   dash_end {source:'dash'}                      the last dash step ended
//   dash_charge {charges, maxCharges, cause}      charges went UP; cause is
//                                                 'recharge' or 'power'
// (dash_start is made by game.js, which knows the direction and the source.)
//
// Pure module: no DOM, no randomness, no imports.

/**
 * What a run's dash is with no ability held (assumed values, tuned below Dash
 * L1: 3 charges, 30 steps, 420 recharge): 2 charges, a 24-step (0.4 s) dash, a
 * 600-step (10 s) recharge, 18 safe steps (0.3 s) after, 300 points per kill.
 */
export const DASH_DEFAULTS = Object.freeze({
  charges: 2,
  dash_steps: 24,
  safe_steps_after: 18,
  recharge_steps: 600,
  dash_kill_points: 300,
});

function positive(params, key, min) {
  const v = params[key];
  if (!Number.isInteger(v) || v < min) {
    throw new RangeError(`createDashState: ${key} must be an integer >= ${min}, got ${JSON.stringify(v)}`);
  }
  return v;
}

/** A fresh dash state with every charge. Unknown params are ignored. */
export function createDashState(params = {}) {
  const p = { ...DASH_DEFAULTS, ...(params || {}) };
  const charges = positive(p, 'charges', 1);
  return {
    charges,
    maxCharges: charges,
    active: 0,
    safe: 0,
    recharge: 0,
    rechargeSteps: positive(p, 'recharge_steps', 1),
    dashSteps: positive(p, 'dash_steps', 1),
    safeStepsAfter: positive(p, 'safe_steps_after', 0),
    killPoints: positive(p, 'dash_kill_points', 0),
    bossHit: false,
  };
}

/** Pac-Man cannot be killed by a ghost: dashing, or inside the safe window. */
export function isSafe(dash) {
  return dash.active > 0 || dash.safe > 0;
}

/**
 * Spend a charge and start a dash heading `dir`. Returns true when it started;
 * false (nothing changes) with no charge, mid-dash or without a heading.
 */
export function startDash(dash, dir) {
  if (dash.charges <= 0 || dash.active > 0) return false;
  if (dir === null || dir === undefined) return false;
  dash.charges -= 1;
  dash.active = dash.dashSteps;
  dash.safe = 0; // the running dash is the safety now
  dash.bossHit = false;
  return true;
}

/** End-of-step countdown: dash, safe window and recharge. Returns the events. */
export function tickDash(dash) {
  const events = [];
  if (dash.active > 0) {
    dash.active -= 1;
    if (dash.active === 0) {
      dash.safe = dash.safeStepsAfter;
      events.push({ type: 'dash_end', source: 'dash' });
    }
  } else if (dash.safe > 0) {
    dash.safe -= 1;
  }
  if (dash.charges < dash.maxCharges) {
    dash.recharge += 1;
    if (dash.recharge >= dash.rechargeSteps) {
      dash.charges += 1;
      dash.recharge = 0;
      events.push({ type: 'dash_charge', charges: dash.charges, maxCharges: dash.maxCharges, cause: 'recharge' });
    }
  }
  return events;
}

/** A power pellet: every charge back at once. Events: dash_charge when it changed anything. */
export function refillDash(dash) {
  const events = [];
  const gained = dash.charges < dash.maxCharges;
  dash.charges = dash.maxCharges;
  dash.recharge = 0;
  if (gained) {
    events.push({ type: 'dash_charge', charges: dash.charges, maxCharges: dash.maxCharges, cause: 'power' });
  }
  return events;
}
