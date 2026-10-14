// Game core: one level of play, advanced in exact fixed 60 Hz steps.
//
//   import { createGame, FRIGHTENED_STEPS } from './game.js';
//   const game = createGame({ template, seed });
//   const events = game.step('left');      // or step(null) / step()
//   game.step({ dir: 'left', actions: new Set(['dash']) });
//
// createGame({template, seed})
//   template  ASCII rows (array or newline string, see maze.js) or an already
//             parsed maze. The maze is never mutated; it is shared read-only
//             as game.maze (width, height, tiles, ghostStarts, ...).
//   seed      number or string; feeds the seeded mulberry32 in rng.js, exposed
//             as game.rng for later systems. No clock, no Math.random.
//
//   config    optional level config (levels/families.js has the shape and the
//             family values); missing keys take BASE_CONFIG, so no config gives
//             exactly the classic four-ghost level. Validated by
//             normalizeConfig and exposed frozen as game.config.
//     ghostCount        ghosts on the level (default 4). Ghost i is
//                       GHOST_KINDS[i % 4]. Ghosts 0-3 start on their own G tile;
//                       ghosts 4+ (rule-5) start inside the house on the same
//                       four spots in rotation and are released one after
//                       another: ghost 4 at tick 480, then every
//                       EXTRA_RELEASE_GAP (120) steps (the first four keep
//                       RELEASE_STEPS).
//     ghostSpeedFactor  scales speed, frightenedSpeed and eatenSpeed (Speed
//                       family 1.25 -> 135 / 75 / 300). Whole percent, integer
//                       maths, rounded to the nearest sub-unit per 60 steps.
//     boss, bossHp      Boss level: see "Boss" below.
//     frightenedSteps   how long a power pellet frightens the ghosts (360 at level 5; Rush = 1.5 x the ramp)
//     pelletPoints      score of a normal pellet (10; Rush 20). A power pellet
//                       stays POWER_POINTS (50).
//     crackedRatio      not used by the logic (the generator measures it).
//
// Boss. config.boss adds one more ghost AFTER the normal ones (so a boss level
// has ghostCount normal ghosts plus the boss, and the boss is state.ghosts's
// last entry, also reachable as state.boss, null on other levels). It is a
// ghost with kind 'boss' (ghosts.js BOSS_KIND: chases like the chaser), hp and
// maxHp (config.bossHp, 5), size 2 (draw it two tiles across; it still touches
// Pac-Man on one tile). It waits at the house centre and leaves at tick
// BOSS_RELEASE_STEP (120). It is hurt only by game.hitBoss(cause) (the ability
// cards), killGhost(boss, cause) (same thing) and Pac-Man eating it while
// frightened; touching it any other time is player_died as for any ghost. Each
// hit is -1 HP, +BOSS_HIT_POINTS and the event
//   boss_hit {hp, maxHp, cause, points, x, y}     x, y: its centre in sub-units
// and, unless hp is now 0, the boss retreats to the house centre and respawns
// like an eaten ghost (not hittable while it is retreating or waiting). At hp 0:
// boss_killed {points, x, y} (+BOSS_KILL_POINTS) and level_cleared, whatever
// pellets are left. Eating the last pellet on a Boss level does not clear it:
// the pellets are put back ({type:'pellets_reset'}).
//
//   abilities optional held abilities [{id, level}] in PICK ORDER (level 1 or 2;
//             a run's held Map is accepted too). Wall Burst, Ghost Blast and
//             Chain Reaction and Magnet have behaviour (abilities/); Dash is level
//             data for the default dash (below).
//             Unknown ids, levels other than 1/2 and duplicates throw.
//             state.abilities has one JSON entry per held ability, same order:
//             {id, level, ...} (Ghost Blast adds `cooldown`).
//   behaviours  optional id -> behaviour object, replacing abilities/index.js
//             BEHAVIOURS (tests and new abilities).
//
// Abilities (decision D2, rule-3). Each held ability is a data object with
// hooks that game.js calls in pick order as hook(api, state, ...args):
//   onLevelGen  once at creation, on the game's own maze
//   onInput     start of every step, args (input) = {dir, actions: Set}; a dash
//               or blast starts this very step, before anything moves
//   onWallHit   Pac-Man ran into a wall: {dir, tx, ty}. While a dash is active a
//               dash held against a wall counts every sub-step
//   onGhostKilled  after a ghost died by killGhost / eating: (ghost, cause)
//   onStep      end of every step (cooldowns and dash timers tick here)
// api = {state, maze, rng, config, emit, killGhost, hitBoss, hasAbility, abilityLevel, wallBroken,
// isDashing}. Hooks only change state and emit events; nothing here draws.
// Events added by the abilities: wall_broken {tx, ty}, blast {x, y, radius
// (tiles), radiusSub, killed}, ability_ready {id}; kills are the usual
// ghost_eaten with cause 'blast'. 'blast' fires Ghost Blast (nothing when it is
// not held); an unknown action name throws.
//
// The default dash (decision D7, dash_core.js owns the numbers). EVERY game has
// one, whatever is held: state.dash = {charges, maxCharges, active, safe,
// recharge, rechargeSteps, dashSteps, safeStepsAfter, killPoints, bossHit}
// (dash_core.js documents each; the HUD reads charges / maxCharges / recharge).
// A new game has DASH_DEFAULTS' charges; holding Dash overrides charges, dash
// steps and recharge by level (abilities/dash.js); Wall Burst only adds wall
// breaking to the dash. Rules:
//   - the 'dash' action spends one charge and starts a dash at the START of the
//     step (before anything moves): Pac-Man moves at double speed for dashSteps
//     steps and emits dash_start {source:'dash', dir, steps, charges,
//     maxCharges}. No charge, already dashing, or no heading yet: nothing
//     happens (a press without a heading is not spent).
//   - while dashing and for safeStepsAfter steps after (state.dash.safe), a ghost
//     touch NEVER emits player_died. A touchable ghost that is not edible
//     (not frightened) and is touched DURING the dash is killed by it:
//     ghost_eaten with cause 'dash' and killPoints (flat, the chain untouched;
//     a frightened ghost is eaten as usual, cause 'eat'). The boss loses 1 HP
//     (boss_hit, cause 'dash') on the first touch of a dash, never more.
//   - one charge returns every rechargeSteps steps (dash_charge {charges,
//     maxCharges, cause:'recharge'}); eating a power pellet refills all of them
//     (dash_charge cause 'power', only when something was missing). dash_end
//     {source:'dash'} is sent when the last dash step ends.
//
// Extra-life pickup (R15, families.js). Only when config.lifePickup is true
// (set by generateLevel's seeded roll, 35% of levels). Once LIFE_PICKUP_AFTER
// (0.5) of the pellets are eaten, one pickup spawns on a seeded floor tile
// reachable from Pac-Man's start: event life_pickup_spawned {tx, ty},
// state.pickup = {tx, ty, age}. Pac-Man standing on it eats it: life_gained
// {lives, gained} (+1 life while below START_LIVES (3); gained false at the cap).
// It expires after LIFE_PICKUP_STAY (600) steps: life_pickup_expired. At most one
// pickup per level, and it is not respawned after it is eaten or expires.
//
// game.maze is the game's OWN copy of the maze (private tiles; Wall Burst turns
// walls into floor there, emitting wall_broken). The parsed maze or template you
// passed in is never mutated; a renderer must read game.maze, not its own copy.
//
// A dash moves Pac-Man in two sub-steps per step (4 sub-units instead of 2), so
// it stays integer, tile-aligned for turns and wall hits, and pellets, ghosts
// and collisions are checked once per step as always.
//
// game.step(input) advances exactly one step and returns the events of that
// step as an array of plain objects (empty when nothing happened). input is a
// direction ('up'|'down'|'left'|'right'), null/undefined for "no new press",
// {dir} (the same) or {dir, actions} (D5), where actions is a Set (or array) of
// ACTIONS names ('dash'|'blast'|'phase'). 'phase' is accepted and ignored. An invalid direction or an unknown action name
// throws before anything changes (no step is taken, no rng is drawn).
// After cleared or dead, step() is a no-op: it returns [] and changes nothing
// (tick does not advance).
//
// game.state (plain JSON-friendly data; same seed + same inputs => identical
// JSON, so it can be snapshotted and compared):
//   tick             number of steps taken so far (0 before the first step)
//   score            running score
//   pac              {x, y, dir, phased, moving, buffer}
//                      x, y     top-left of Pac-Man's tile cell in sub-units
//                               (SUB = 16 per tile, player.js); tile centre
//                               is x + 8, y + 8. Wraps horizontally in tunnels.
//                      dir      'up'|'down'|'left'|'right' or null until the
//                               first input; kept while blocked by a wall
//                      phased   false (true only while an ability lets Pac-Man
//                               pass walls; never set by this card)
//                      moving   true if the last step moved Pac-Man
//                      buffer   pending turn {dir, age} or null
//   pellets          Array of width * height numbers, index y * width + x:
//                      0 nothing left, 1 pellet, 2 power pellet
//                    (a plain array so JSON.stringify shows it exactly)
//   pelletsLeft      pellets + power pellets still on the board
//   pelletsTotal     pelletsLeft at the start of the level
//   frightenedTimer  steps of ghost fright left, 0 when none; 360 right after
//                    a power pellet, then -1 per step down to 0
//   mode             global ghost mode this step: 'scatter'|'chase'|'frightened'
//                    (createModeScheduler().update(frightenedTimer) is called
//                    once per step, after pellet eating; 'scatter' before the
//                    first step; the schedule pauses while frightened)
//   ghosts           one per maze.ghostStarts entry, in order: ghost i is
//                    GHOST_KINDS[i] (chaser, ambusher, flanker, shy), made by
//                    createGhost (ghosts.js documents every field: kind, x, y,
//                    tile, dir, mode, house, speed, scatter, ...). The chaser
//                    starts 'out' (it is outside the house in classic1, and
//                    'leaving' if a template puts it inside); the others start
//                    house 'waiting' (dir 'up', not moving) and are let out on
//                    the RELEASE_STEPS schedule. scatter is the maze corner.
//   chain            value the NEXT ghost eaten in a chain scores: CHAIN_START
//                    (200) right after a power pellet (and at the start), then
//                    doubled per chain kill, capped at CHAIN_MAX (1600), so a
//                    fifth and sixth ghost (more than four exist) score 1600
//   chainCount       ghosts eaten in the chain so far (0 after a power pellet)
//   cleared          true once the last pellet was eaten (level_cleared sent)
//   dead             true once Pac-Man died (a lethal ghost touch, see below)
// Each ghost also carries, beyond the createGhost fields:
//   respawn          steps left of the wait inside the house after being eaten
//                    (RESPAWN_STEPS when it starts, 0 when not respawning)
//   immune           true while it is revived inside the still-open power
//                    window: it follows scatter/chase (mode is NOT
//                    'frightened'), so it is not edible and not drawn scared
//
// Events (plain objects, {type, ...}):
//   pellet_eaten   {tile:{x,y}, points:10}
//   power_eaten    {tile:{x,y}, points:50}      also (re)starts frightenedTimer,
//                                               resets chain to 200 and frightens
//                                               every ghost that is not eaten
//   level_cleared  {}                           after the last pellet's event
//   ghost_left_house {kind}                     a ghost stepped out of the house
//   ghost_reverse    {kind, dir, reason}        reason 'mode' (scatter/chase swap
//                                               or frightened entered: once per
//                                               ghost per change) or 'dead_end'
//   ghost_eaten    {kind, points, x, y, chain, cause}
//                  a ghost died (killGhost below; Pac-Man eating a frightened
//                  ghost has cause 'eat'). points were added to state.score.
//                  x, y: the ghost's CENTRE in sub-units where it died (the
//                  popup position; Pac-Man's tile centre is x + 8 likewise).
//                  chain: 1-based position in the power window's chain (1 for
//                  the 200 eat, 2 for 400, ...), 0 for a flat non-chain kill.
//   player_died    {kind, tile:{x,y}}           Pac-Man touched a lethal ghost
//                                               (touchable, not frightened) while
//                                               not phased: kind of the first such
//                                               ghost, tile Pac-Man's centre tile.
//                                               state.dead becomes true, it fires
//                                               exactly once and later step()s are
//                                               no-ops. pac.phased suppresses it.
//   ghost_home     {kind}                       the eaten ghost reached the door;
//                                               it is now in the house waiting
//                                               RESPAWN_STEPS (see below)
// plus the movement events passed through from player.js, in the order they
// happened: player_reverse {dir}, player_turn {dir}, wall_hit {dir, tx, ty}.
// Order inside a step: player events, pellet events, phase-1 touch events
// (ghost_eaten / player_died), then the ghosts' events in ghost order (chaser
// first), then the phase-2 touch events.
//
// Step order: dash start, ability onInput hooks, player moves (dash: twice), tick++, frightenedTimer--, pellets, mode scheduler,
// house releases (ghost i is released once tick >= RELEASE_STEPS[i]; a ghost
// that is respawning counts its own respawn wait down instead), then each
// ghost moves with stepGhost(ghost, {maze, mode, pac, chaser}, rng): pac is
// {tile, dir} (Pac-Man's centre tile and heading) and chaser is ghost 0's tile,
// both read once before any ghost moves, so every ghost sees the same board.
// The seeded game.rng is only drawn from by frightened ghosts. Last, the ability
// onStep hooks run (the dash timers tick first, then cooldowns); a step that ends
// the game (death, boss kill) returns before them.
//
// Collisions are tile-wise and run twice per step: after Pac-Man moved (before
// the ghosts) and again after the ghosts moved. A touch is the same centre tile
// (x modulo the maze width, so tunnels wrap) or a head-on swap of the tiles the
// two held at the start of the step (tilesTouch). A death in phase 1 ends the
// step: the ghosts do not move. A power pellet eaten this step already
// frightens the ghosts for phase 1, so eating it on a ghost's tile eats the
// ghost instead of dying. If the last pellet was eaten this step the level is
// cleared and no touch kills.
//
// Eating ghosts. A power pellet sets frightenedTimer to config.frightenedSteps,
// resets chain to 200 and frightens every ghost that is not already eaten (a
// ghost that is waiting in the house is frightened too, but cannot be touched).
// In both collision passes (below) Pac-Man eats every ghost that is touchable (not
// waiting, not eaten) and frightened and on his centre tile, or that swapped
// tiles with him this step (no tunnelling through a ghost). Eating is
// killGhost(ghost, 'eat'): state.score += chain, event ghost_eaten, then
// eatGhost() (mode 'eaten'). Two ghosts on his tile are eaten in ghost order,
// 200 then 400. A phased Pac-Man still eats. Ghosts that are waiting in the
// house or already eaten neither harm nor are harmed by Pac-Man.
//
// The way home and respawn. An eaten ghost steers to the door at eatenSpeed
// (ghosts.js; it may pass doors, never reverses). On reaching it ghosts.js
// emits ghost_home and this module puts the ghost on its house tile (a start
// tile inside the house, or the free interior tile nearest the door for a
// start outside it: the chaser in classic1), house 'waiting', dir 'up', and
// starts the respawn wait: RESPAWN_STEPS (120) steps. On the step with
// tick === (ghost_home tick) + RESPAWN_STEPS it is released ('leaving'),
// walks out and emits ghost_left_house, then is a normal ghost. Revived while
// frightenedTimer > 0 it is immune to that window (ghost.immune): it follows
// the scatter/chase mode the schedule resumes with, so it cannot be eaten
// again in the window. The immunity ends when the window ends or the next
// power pellet frightens it; a ghost eaten in two separate windows scores
// both times.
//
// killGhost(ghost, cause = 'eat') -> events[]   (on the game object)
// The one route a ghost death takes, so ability cards (blast, dash, ...) reuse
// the scoring, the event and the respawn. It scores, emits ghost_eaten (with
// the given cause) and sends the ghost home exactly as eating does. Scoring:
//   ghost frightened (mode 'frightened')  the chain value 200/400/800/1600,
//                                         advancing the chain, any cause
//   any other ghost                        a flat KILL_POINTS (200); the chain
//                                         is untouched
// A ghost that is waiting in the house or already eaten is refused: the call
// returns [] and changes nothing. A ghost that is not in state.ghosts throws.
// It returns the events it made; a caller outside step() forwards them to the
// event list it returns to the render layer.
//
// A pellet is eaten on the step Pac-Man's centre first lies in its tile
// (playerTile in player.js) and is removed then, so it can never count twice.
// The level is only cleared by eating: a template with no pellets at all never
// emits level_cleared (the Boss level is not cleared by pellets).
//
// Pure module: no DOM, no clock, no Math.random. Imports maze.js, player.js,
// ghosts.js and rng.js only.

import { createRng } from './rng.js';
import { DIRS, TILE, floodFillPlayer, parseTemplate, stepTile } from './maze.js';
import { BASE_CONFIG, LIFE_PICKUP_AFTER, LIFE_PICKUP_STAY } from './levels/families.js';
import { createAbility, normalizeAbilities } from './abilities/index.js';
import { activeCombos } from './abilities/combos.js';
import { DASH_DEFAULTS, createDashState, isSafe, refillDash, startDash, tickDash } from './dash_core.js';
import { SUB, createPlayer, isAligned, playerTile, stepPlayer } from './player.js';
import {
  BOSS_KIND,
  EATEN_SPEED,
  FRIGHTENED_SPEED,
  GHOST_KINDS,
  GHOST_SPEED,
  HOUSE,
  MODE,
  createGhost,
  createModeScheduler,
  eatGhost,
  modeAtStep,
  releaseGhost,
  scatterCorner,
  stepGhost,
} from './ghosts.js';

export const FRIGHTENED_STEPS = BASE_CONFIG.frightenedSteps; // 360
export const PELLET_POINTS = BASE_CONFIG.pelletPoints; // 10
export const POWER_POINTS = 50;

/** Value of the first ghost eaten after a power pellet; doubles per ghost. */
export const CHAIN_START = 200;
/** The chain stops doubling here (fifth and later ghosts still score this). */
export const CHAIN_MAX = 1600;
/** Flat score of a kill that is not a frightened-chain eat (blast, dash, ...). */
export const KILL_POINTS = 200;

/** Lives a new game starts with (config.lives overrides it per level; R14.1). */
export const START_LIVES = 3;
/**
 * Steps after a lost life during which ghost contact costs nothing (R14.2).
 * Contact on steps T+1 .. T+INVULN_STEPS is safe, step T+INVULN_STEPS+1 is not.
 */
export const INVULN_STEPS = 120;

/**
 * Steps an eaten ghost waits inside the house after reaching the door, before
 * it is released again: 120 = 2 s at 60 Hz. The wait starts the step the
 * ghost_home event is sent (the ghost is put on its house tile that step) and
 * it is released on the step tick === ghost_home tick + RESPAWN_STEPS.
 */
export const RESPAWN_STEPS = 120;

/**
 * Step (state.tick) at which ghost i is let out of the house. Index = ghost
 * index. 0 means "starts outside" (the chaser). Strictly increasing; the gaps
 * (60, 120, 180) leave each ghost time to clear the house before the next one,
 * and all four are out well inside 600 steps.
 */
export const RELEASE_STEPS = Object.freeze([0, 60, 180, 360]);

/**
 * Release step of extra ghosts (index 4 and up, ghostCount above 4): they leave
 * one after another, EXTRA_RELEASE_GAP steps apart, after the first four.
 */
export const EXTRA_RELEASE_GAP = 120;
/** Step at which the Boss level's boss is let out of the house. */
export const BOSS_RELEASE_STEP = 120;
/** Score for each boss hit, and for the killing hit on top of it. */
export const BOSS_HIT_POINTS = 500;
export const BOSS_KILL_POINTS = 2000;

/** Largest ghostCount createGame accepts. */
export const MAX_GHOSTS = 16;

/** Names the input's actions Set may hold (D5). */
export const ACTIONS = Object.freeze(['dash', 'blast', 'phase']);

export const PELLET_NONE = 0;
export const PELLET_DOT = 1;
export const PELLET_POWER = 2;

// The game's own maze: a shallow copy of the parsed maze with a private copy of
// the tiles, because abilities (Wall Burst) change tiles during play. The parsed
// maze (and so a level, a template or another game) is never mutated; the
// read-only parts (starts, house, tunnels, pellet lists) are shared.
function toMaze(template) {
  let parsed;
  if (template && !Array.isArray(template) && typeof template === 'object') {
    if (!template.tiles || !template.width || !template.height) {
      throw new TypeError('createGame: template object is not a parsed maze');
    }
    parsed = template;
  } else {
    parsed = parseTemplate(template);
  }
  return { ...parsed, tiles: new Uint8Array(parsed.tiles) };
}

// Input is a bare dir, null/undefined, {dir} or {dir, actions}. Returns
// {dir, actions} with actions as an array; throws on an unknown action name or
// a malformed actions value. The direction itself is validated by stepPlayer,
// which also runs before any state is touched.
function parseInput(input) {
  if (input === null || input === undefined || typeof input === 'string') {
    return { dir: input ?? null, actions: [] };
  }
  if (typeof input !== 'object') {
    throw new TypeError(`step: invalid input ${JSON.stringify(input)}`);
  }
  const { dir = null, actions } = input;
  let list = [];
  if (actions !== undefined && actions !== null) {
    if (typeof actions === 'string' || typeof actions[Symbol.iterator] !== 'function') {
      throw new TypeError('step: input.actions must be a Set (or array) of action names');
    }
    list = [...actions];
    for (const a of list) {
      if (!ACTIONS.includes(a)) {
        throw new TypeError(`step: unknown action ${JSON.stringify(a)}`);
      }
    }
  }
  return { dir, actions: list };
}

/**
 * True while a ghost can touch Pac-Man in either direction: it is not waiting
 * in the house (including while it waits to respawn) and not eaten. Such a
 * ghost never harms Pac-Man and Pac-Man never harms it.
 */
export function isGhostTouchable(ghost) {
  return ghost.house !== HOUSE.WAITING && ghost.mode !== MODE.EATEN;
}

/** True when Pac-Man touching this ghost eats it: touchable and frightened. */
export function isGhostEdible(ghost) {
  return isGhostTouchable(ghost) && ghost.mode === MODE.FRIGHTENED;
}

/**
 * Do Pac-Man and a ghost touch this step? Tiles are {x, y} centre tiles, x
 * compared modulo `width` (tunnel wrap). True when they share a tile after the
 * move, or swapped tiles head-on (each ended where the other started), so two
 * fast movers cannot pass through each other between steps.
 */
export function tilesTouch(pacPrev, pacNow, ghostPrev, ghostNow, width) {
  const same = (a, b) => (((a.x - b.x) % width) + width) % width === 0 && a.y === b.y;
  if (same(pacNow, ghostNow)) return true;
  return same(pacNow, ghostPrev) && same(pacPrev, ghostNow);
}

// Where an eaten ghost waits in the house, per start index. A start tile that
// is inside the house is used as is. A start outside it (the chaser in
// classic1) gets the free interior tile closest to the door (squared distance,
// then reading order), so respawning ghosts never share a tile. Ghost i uses
// homes[i % homes.length], the same rotation as D4's extra-ghost spawns.
function houseHomes(maze) {
  const { interior, door } = maze.ghostHouse;
  const same = (a, b) => a.x === b.x && a.y === b.y;
  if (interior.length === 0) return maze.ghostStarts.map((t) => ({ x: t.x, y: t.y }));
  const taken = maze.ghostStarts.filter((t) => interior.some((n) => same(n, t)));
  const dist = (t) => (door ? (t.x - door.x) ** 2 + (t.y - door.y) ** 2 : 0);
  const free = interior.filter((t) => !taken.some((n) => same(n, t)));
  free.sort((a, b) => dist(a) - dist(b)); // stable: ties keep reading order
  return maze.ghostStarts.map((t) => {
    if (interior.some((n) => same(n, t))) return { x: t.x, y: t.y };
    const f = free.shift() || interior[0];
    return { x: f.x, y: f.y };
  });
}

// The interior tile nearest the centroid of the house interior (integer maths,
// ties in reading order): where the boss waits and respawns. Without a house it
// is the second ghost start.
function houseCentre(maze) {
  const { interior } = maze.ghostHouse;
  if (interior.length === 0) return { ...maze.ghostStarts[1 % maze.ghostStarts.length] };
  const n = interior.length;
  let sx = 0;
  let sy = 0;
  for (const t of interior) {
    sx += t.x;
    sy += t.y;
  }
  let best = interior[0];
  let bestD = Infinity;
  for (const t of interior) {
    const d = (n * t.x - sx) ** 2 + (n * t.y - sy) ** 2;
    if (d < bestD) {
      bestD = d;
      best = t;
    }
  }
  return { x: best.x, y: best.y };
}

/**
 * Validate a level config and fill the defaults (levels/families.js
 * BASE_CONFIG). Unknown keys are ignored; a bad value throws before anything
 * is built. Returns a frozen plain object, exposed as game.config.
 */
export function normalizeConfig(config) {
  if (config !== undefined && config !== null && typeof config !== 'object') {
    throw new TypeError('createGame: config must be an object');
  }
  const c = { ...BASE_CONFIG, ...(config || {}) };
  if (!Number.isInteger(c.ghostCount) || c.ghostCount < 1 || c.ghostCount > MAX_GHOSTS) {
    throw new RangeError(`createGame: config.ghostCount must be an integer 1-${MAX_GHOSTS}, got ${c.ghostCount}`);
  }
  if (typeof c.ghostSpeedFactor !== 'number' || !(c.ghostSpeedFactor > 0) || c.ghostSpeedFactor > 4) {
    throw new RangeError(`createGame: config.ghostSpeedFactor must be a number in (0, 4], got ${c.ghostSpeedFactor}`);
  }
  if (!Number.isInteger(c.frightenedSteps) || c.frightenedSteps < 1) {
    throw new RangeError(`createGame: config.frightenedSteps must be an integer >= 1, got ${c.frightenedSteps}`);
  }
  if (!Number.isInteger(c.pelletPoints) || c.pelletPoints < 0) {
    throw new RangeError(`createGame: config.pelletPoints must be an integer >= 0, got ${c.pelletPoints}`);
  }
  if (!Number.isInteger(c.bossHp) || c.bossHp < 1) {
    throw new RangeError(`createGame: config.bossHp must be an integer >= 1, got ${c.bossHp}`);
  }
  c.boss = Boolean(c.boss);
  return Object.freeze({
    ghostCount: c.ghostCount,
    ghostSpeedFactor: c.ghostSpeedFactor,
    frightenedSteps: c.frightenedSteps,
    pelletPoints: c.pelletPoints,
    boss: c.boss,
    bossHp: c.bossHp,
    crackedRatio: Number(c.crackedRatio) || 0,
    lifePickup: c.lifePickup === true,
  });
}

// Ghost speeds scaled by the family's factor with integers only: the factor is
// taken as whole percent (1.25 -> 125) and the result rounded to the nearest
// sub-unit per 60 steps, so 108/60/240 become 135/75/300 for Speed and there is
// no float position drift (positions only ever move by whole sub-units).
function scaleSpeed(base, factor) {
  const percent = Math.round(factor * 100);
  return Math.floor((base * percent + 50) / 100);
}

function makeGhosts(maze, cfg, homes, bossHome) {
  const interior = maze.ghostHouse.interior;
  const speeds = {
    speed: scaleSpeed(GHOST_SPEED, cfg.ghostSpeedFactor),
    frightenedSpeed: scaleSpeed(FRIGHTENED_SPEED, cfg.ghostSpeedFactor),
    eatenSpeed: scaleSpeed(EATEN_SPEED, cfg.ghostSpeedFactor),
  };
  const ghosts = [];
  for (let i = 0; i < cfg.ghostCount; i++) {
    // Ghosts 0-3 start on their own G tile. Extras (rule-5) start on the same
    // four spots in rotation, but inside the house (homes), so a start that is
    // outside it (the chaser's in classic1) is never used for an extra ghost.
    const tile = i < maze.ghostStarts.length ? maze.ghostStarts[i] : homes[i % homes.length];
    const insideHouse = interior.some((t) => t.x === tile.x && t.y === tile.y);
    let house = HOUSE.WAITING;
    let dir = 'up'; // a waiting ghost faces the door; release clears the heading
    if (i === 0) {
      house = insideHouse ? HOUSE.LEAVING : HOUSE.OUT;
      dir = insideHouse ? null : 'left'; // free heading when it must find the door
    }
    const kind = i % GHOST_KINDS.length; // kinds rotate through GHOST_KINDS
    const ghost = createGhost({
      kind,
      tile,
      dir,
      house,
      scatter: scatterCorner(maze, kind),
      ...speeds,
    });
    ghost.respawn = 0; // steps left of the post-eaten wait in the house
    ghost.immune = false; // revived during a power pellet's fright: not frightened
    ghosts.push(ghost);
  }
  if (cfg.boss) {
    const boss = createGhost({
      kind: BOSS_KIND,
      tile: bossHome,
      dir: 'up',
      house: HOUSE.WAITING,
      scatter: scatterCorner(maze, 0),
      ...speeds,
    });
    boss.respawn = 0;
    boss.immune = false;
    boss.hp = cfg.bossHp;
    boss.maxHp = cfg.bossHp;
    boss.size = 2; // drawn two tiles across; logic still touches on one tile
    ghosts.push(boss);
  }
  return ghosts;
}

// Step at which the waiting ghost at `index` is let out of the house.
function releaseStep(ghost, index) {
  if (ghost.kind === BOSS_KIND) return BOSS_RELEASE_STEP;
  if (index < RELEASE_STEPS.length) return RELEASE_STEPS[index];
  return RELEASE_STEPS[RELEASE_STEPS.length - 1] + (index - RELEASE_STEPS.length + 1) * EXTRA_RELEASE_GAP;
}

export function createGame({ template, seed = 0, config, abilities, behaviours } = {}) {
  if (template === undefined || template === null) {
    throw new TypeError('createGame: template is required');
  }
  const maze = toMaze(template);
  const baseConfig = normalizeConfig(config);
  // Lives are not part of the level config (BASE_CONFIG shape is fixed); the run passes them in.
  const lives = config && config.lives !== undefined ? config.lives : START_LIVES;
  if (!Number.isInteger(lives) || lives < 1) {
    throw new RangeError(`createGame: config.lives must be an integer >= 1, got ${lives}`);
  }
  const heldSpecs = normalizeAbilities(abilities);
  const rng = createRng(seed);

  // Held abilities (D2): data objects with hooks, called in pick order. Their
  // mutable state is the JSON in state.abilities (one entry each, same order).
  const held = heldSpecs.map((spec) => createAbility(spec, behaviours));
  // Combos (abilities/combos.js): active only when every ability they require is
  // held. Their state is the JSON in state.combos.
  const combos = activeCombos(heldSpecs.map((a) => a.id)).map((def) => ({ def, state: { id: def.id, ...def.init() } }));

  // Holding Ghost Blast or Chain Reaction (extraGhost) adds ONE ghost on top of
  // the level's ghostCount (rule-5): both together still add only one, so the
  // ghost count never snowballs with the pick. It goes through the normal
  // extra-ghost path (G tiles in rotation, released after the others) and is
  // capped at MAX_GHOSTS (a Swarm level, 6, becomes 7). game.config shows the
  // effective ghostCount.
  const extraGhost = held.some((a) => a.behaviour && a.behaviour.extraGhost) ? 1 : 0;
  const cfg = extraGhost
    ? Object.freeze({ ...baseConfig, ghostCount: Math.min(MAX_GHOSTS, baseConfig.ghostCount + extraGhost) })
    : baseConfig;

  const pellets = new Array(maze.width * maze.height).fill(PELLET_NONE);
  for (const p of maze.pellets) pellets[p.y * maze.width + p.x] = PELLET_DOT;
  for (const p of maze.powerPellets) pellets[p.y * maze.width + p.x] = PELLET_POWER;
  const pelletsTotal = maze.pellets.length + maze.powerPellets.length;

  const pac = createPlayer(maze);
  pac.phased = false;
  let sched = createModeScheduler();
  // Tick at which the current life began: release steps count from here, so a
  // restarted level releases its ghosts on the same schedule as the first try.
  let scheduleBase = 0;
  const homes = houseHomes(maze);
  const bossHome = houseCentre(maze);

  const state = {
    tick: 0,
    score: 0,
    pac,
    pellets,
    pelletsLeft: pelletsTotal,
    pelletsTotal,
    frightenedTimer: 0,
    mode: MODE.SCATTER,
    lives, // lost one by one on lethal touches (R14); the last one ends the run
    invuln: 0, // steps of invulnerability left after a lost life (INVULN_STEPS)
    ghosts: makeGhosts(maze, cfg, homes, bossHome),
    boss: null, // the boss ghost on a Boss level (also in ghosts), else null
    pickup: null, // extra-life pickup {tx, ty, age} on the board, else null (R15)
    dash: null, // the default dash: charges and timers (dash_core.js)
    abilities: [], // held abilities in pick order: {id, level, ...} (abilities/)
    combos: [], // active combos: {id, ...} (abilities/combos.js)
    chain: CHAIN_START,
    chainCount: 0,
    cleared: false,
    dead: false,
  };
  state.boss = state.ghosts.find((g) => g.kind === BOSS_KIND) || null;

  state.abilities = held.map((a) => a.state);
  state.combos = combos.map((c) => c.state);

  // The default dash every game has (dash_core.js). Holding Dash overrides its
  // charges, dash steps and recharge by level (the behaviour's level table).
  const dashHeld = held.find((a) => a.id === 'dash');
  const dashLevel = dashHeld && dashHeld.behaviour ? dashHeld.behaviour.levels[dashHeld.level] : null;
  state.dash = createDashState({ ...DASH_DEFAULTS, ...(dashLevel || {}) });

  // Events emitted by hooks go to `sink`: the event list of the step in
  // progress (or of the killGhost call that is running).
  let sink = [];

  // Extra-life pickup (R15). At most one per level: spawned once, then it is
  // eaten, expires or stays; pickupSpent stops a second spawn either way.
  let pickupSpent = false;

  function callHooks(name, ...args) {
    for (const a of held) {
      const hook = a.hooks[name];
      if (hook) hook(api, a.state, ...args);
    }
    // Combos run after the abilities, for the same hook.
    for (const c of combos) {
      const hook = c.def.hooks[name];
      if (hook) hook(api, c.state, ...args);
    }
  }

  // What hooks get (abilities/index.js lists it). Logic only emits events.
  const api = {
    state,
    maze,
    rng,
    config: cfg,
    emit: (event) => sink.push(event),
    killGhost: (ghost, cause) => killGhostInto(ghost, cause, sink),
    hitBoss: (cause) => (state.boss ? hitBossInto(state.boss, cause, sink) : false),
    hasAbility: (id) => held.some((a) => a.id === id),
    abilityLevel: (id) => (held.find((a) => a.id === id) || { level: 0 }).level,
    // A wall was turned into floor: tell the renderer, then the combos.
    wallBroken: (tx, ty) => {
      sink.push({ type: 'wall_broken', tx, ty });
      for (const c of combos) {
        const hook = c.def.hooks.onWallBroken;
        if (hook) hook(api, c.state, { tx, ty });
      }
    },
    // The one dash is running (state.dash.active > 0).
    isDashing: () => state.dash.active > 0,
  };

  // The mode a ghost that is immune to the open power window follows instead
  // of 'frightened': the scatter/chase mode the paused schedule resumes with.
  function underlyingMode() {
    return state.mode === MODE.FRIGHTENED ? modeAtStep(sched.state.step) : state.mode;
  }

  function eatPellets(events) {
    const tile = playerTile(pac, maze);
    const i = tile.y * maze.width + tile.x;
    const kind = pellets[i];
    if (kind === PELLET_NONE) return;
    pellets[i] = PELLET_NONE;
    state.pelletsLeft -= 1;
    if (kind === PELLET_POWER) {
      state.score += POWER_POINTS;
      state.frightenedTimer = cfg.frightenedSteps;
      // A new power pellet starts a new chain and frightens every ghost that
      // is not eaten, including ones that were immune to the previous window.
      // (Eaten ghosts stay eaten; they are immune again when they revive.)
      state.chain = CHAIN_START;
      state.chainCount = 0;
      for (const g of state.ghosts) if (g.mode !== MODE.EATEN) g.immune = false;
      events.push({ type: 'power_eaten', tile, points: POWER_POINTS });
      events.push(...refillDash(state.dash)); // a power pellet restores every dash charge
    } else {
      state.score += cfg.pelletPoints;
      events.push({ type: 'pellet_eaten', tile, points: cfg.pelletPoints });
    }
    if (state.pelletsLeft === 0) {
      if (state.boss) {
        // A Boss level is cleared by killing the boss, never by pellets. Put
        // every pellet back (except the one under Pac-Man) so power pellets
        // keep coming and the level cannot dead-end.
        for (const p of maze.pellets) pellets[p.y * maze.width + p.x] = PELLET_DOT;
        for (const p of maze.powerPellets) pellets[p.y * maze.width + p.x] = PELLET_POWER;
        pellets[i] = PELLET_NONE;
        state.pelletsLeft = pelletsTotal - 1;
        events.push({ type: 'pellets_reset' });
      } else {
        state.cleared = true;
        events.push({ type: 'level_cleared' });
      }
    }
  }

  // The extra-life pickup (R15), run once per step after pellets. A step
  // first checks whether Pac-Man stands on it (eaten: +1 life up to
  // START_LIVES, life_gained {lives, gained}); otherwise it ages and expires
  // after LIFE_PICKUP_STAY steps (life_pickup_expired). With no pickup on the
  // board, one spawns (life_pickup_spawned {tx, ty}) on a seeded reachable floor
  // tile once LIFE_PICKUP_AFTER of the pellets are eaten, on levels whose
  // config.lifePickup is set and only once per level.
  function updatePickup(events) {
    if (state.cleared) return;
    const here = playerTile(pac, maze);
    if (state.pickup) {
      const p = state.pickup;
      if (here.x === p.tx && here.y === p.ty) {
        state.pickup = null;
        const gained = state.lives < START_LIVES;
        if (gained) state.lives += 1;
        events.push({ type: 'life_gained', lives: state.lives, gained });
        return;
      }
      p.age += 1;
      if (p.age >= LIFE_PICKUP_STAY) {
        state.pickup = null;
        events.push({ type: 'life_pickup_expired' });
      }
      return;
    }
    if (!cfg.lifePickup || pickupSpent) return;
    const eaten = pelletsTotal - state.pelletsLeft;
    if (pelletsTotal === 0 || eaten < LIFE_PICKUP_AFTER * pelletsTotal) return;
    pickupSpent = true;
    // Reachable from Pac-Man's start, floor only (no walls, doors or house),
    // and not under Pac-Man now so it cannot be eaten on the spot.
    const reach = [...floodFillPlayer(maze, maze.playerStart)].sort((a, b) => a - b);
    const spots = reach
      .filter((i) => maze.tiles[i] === TILE.FLOOR && i !== here.y * maze.width + here.x)
      .map((i) => ({ x: i % maze.width, y: Math.floor(i / maze.width) }));
    if (spots.length === 0) return;
    const spot = rng.pick(spots);
    state.pickup = { tx: spot.x, ty: spot.y, age: 0 };
    events.push({ type: 'life_pickup_spawned', tx: spot.x, ty: spot.y });
  }

  function moveGhosts(events) {
    const { ghosts } = state;
    if (ghosts.length === 0) return;
    // One snapshot per step: every ghost is fed the same Pac-Man and chaser tile.
    const pacCtx = { tile: playerTile(pac, maze), dir: pac.dir };
    const chaser = { x: ghosts[0].tile.x, y: ghosts[0].tile.y };
    const underlying = underlyingMode();
    for (let i = 0; i < ghosts.length; i++) {
      const ghost = ghosts[i];
      if (ghost.house === HOUSE.WAITING) {
        // An eaten ghost counts its own respawn wait down; the others wait for
        // their slot in the release schedule.
        let release = false;
        if (ghost.respawn > 0) {
          ghost.respawn -= 1;
          release = ghost.respawn === 0;
        } else {
          release = state.tick - scheduleBase >= releaseStep(ghost, i);
        }
        if (release) {
          releaseGhost(ghost);
          ghost.dir = null; // free heading: the house exit decides
        }
      }
      const mode = ghost.immune && state.mode === MODE.FRIGHTENED ? underlying : state.mode;
      const ctx = { maze, mode, pac: pacCtx, chaser };
      const own = stepGhost(ghost, ctx, rng);
      if (own.some((e) => e.type === 'ghost_home')) {
        sendHome(ghost, i, underlying);
        // It is back in the house: drop any exit it had already made this step.
        events.push(...own.filter((e) => e.type !== 'ghost_left_house'));
      } else {
        events.push(...own);
      }
    }
  }

  // ghosts.js revived the ghost on the door tile. Respawn it for real: put it
  // on its house tile, waiting RESPAWN_STEPS before it is released (the
  // release in moveGhosts). Revived inside an open power window it is immune
  // to that window: it follows the schedule's scatter/chase mode, not
  // 'frightened', so it is neither edible nor drawn as scared, until the
  // window ends or a new power pellet frightens it.
  function sendHome(ghost, index, underlying) {
    const home = ghost.kind === BOSS_KIND ? bossHome : homes[index % homes.length];
    ghost.x = home.x * SUB;
    ghost.y = home.y * SUB;
    ghost.tile = { x: home.x, y: home.y };
    ghost.dir = 'up';
    ghost.acc = 0;
    ghost.decided = false;
    ghost.house = HOUSE.WAITING;
    ghost.respawn = RESPAWN_STEPS;
    if (state.mode === MODE.FRIGHTENED) {
      ghost.immune = true;
      ghost.mode = underlying;
    } else {
      ghost.mode = state.mode;
    }
  }

  // A lost life (R14.1): Pac-Man and every ghost go back to their start tiles,
  // the mode schedule and fright/chain restart, the dash's active and safe
  // windows end (charges stay). Eaten pellets, score, boss HP and the level
  // itself are kept. Ghost objects are refilled in place (same identities).
  function loseLife() {
    Object.assign(pac, createPlayer(maze));
    const bossHp = state.boss ? state.boss.hp : 0;
    const fresh = makeGhosts(maze, cfg, homes, bossHome);
    fresh.forEach((g, i) => Object.assign(state.ghosts[i], g));
    if (state.boss) state.boss.hp = bossHp;
    scheduleBase = state.tick;
    sched = createModeScheduler();
    state.mode = MODE.SCATTER;
    state.frightenedTimer = 0;
    state.chain = CHAIN_START;
    state.chainCount = 0;
    state.dash.active = 0;
    state.dash.safe = 0;
    state.dash.bossHit = false;
    state.invuln = INVULN_STEPS;
  }

  // One hit on the boss (an ability hit, or Pac-Man eating it while frightened):
  // -1 HP, BOSS_HIT_POINTS, event boss_hit. The boss is not eaten like a
  // ghost: unless that was the last HP it retreats to the house centre (mode
  // 'eaten', then the usual respawn wait) so it cannot be hit twice in a row.
  // At 0 HP: boss_killed, then level_cleared (pellets do not matter), and the
  // game is over for step(). Refused (false, nothing changes) while the boss is
  // waiting in the house or on its way home, or once the level is over.
  function hitBossInto(boss, cause, events) {
    if (state.cleared || state.dead || boss.hp <= 0 || !isGhostTouchable(boss)) return false;
    boss.hp -= 1;
    const killed = boss.hp === 0;
    const points = BOSS_HIT_POINTS;
    state.score += points;
    eatGhost(boss);
    boss.immune = false;
    boss.respawn = 0;
    const x = boss.x + (SUB >> 1);
    const y = boss.y + (SUB >> 1);
    events.push({ type: 'boss_hit', hp: boss.hp, maxHp: boss.maxHp, cause, points, x, y });
    if (killed) {
      state.cleared = true;
      state.score += BOSS_KILL_POINTS;
      events.push({ type: 'boss_killed', points: BOSS_KILL_POINTS, x, y });
      events.push({ type: 'level_cleared' });
    }
    return true;
  }

  // game.hitBoss(cause) for the ability cards: one hit on the boss. Returns the
  // events it made ([] when there is no boss or it cannot be hit now).
  function hitBoss(cause = 'ability') {
    if (typeof cause !== 'string' || cause === '') {
      throw new TypeError('hitBoss: cause must be a non-empty string');
    }
    const events = [];
    if (state.boss) hitBossInto(state.boss, cause, events);
    return events;
  }

  // The single route every ghost death takes (eating now; the ability cards'
  // blast/dash/... later). Scores, emits ghost_eaten, starts the trip home.
  // Returns false and does nothing for a ghost that is waiting in the house or
  // already eaten (they cannot be harmed, and it stops a double score).
  //   frightened ghost  -> the chain value (200, 400, 800, 1600, capped), and
  //                        the chain advances, whatever the cause;
  //   any other ghost   -> a flat KILL_POINTS (200; a dash kill passes
  //                        state.dash.killPoints), the chain is untouched.
  function killGhostInto(ghost, cause, events, flatPoints = KILL_POINTS) {
    if (!isGhostTouchable(ghost)) return false;
    if (ghost.kind === BOSS_KIND) return hitBossInto(ghost, cause, events);
    let points = flatPoints;
    let chain = 0;
    if (ghost.mode === MODE.FRIGHTENED) {
      points = state.chain;
      state.chainCount += 1;
      chain = state.chainCount;
      state.chain = Math.min(CHAIN_MAX, state.chain * 2);
    }
    state.score += points;
    eatGhost(ghost);
    ghost.immune = false;
    ghost.respawn = 0;
    events.push({
      type: 'ghost_eaten',
      kind: ghost.kind,
      points,
      x: ghost.x + (SUB >> 1),
      y: ghost.y + (SUB >> 1),
      chain,
      cause,
    });
    // onGhostKilled hooks (Chain Reaction later) emit into the same event list.
    if (held.length > 0) {
      const outer = sink;
      sink = events;
      try {
        callHooks('onGhostKilled', ghost, cause);
      } finally {
        sink = outer;
      }
    }
    return true;
  }

  function killGhost(ghost, cause = 'eat') {
    if (!state.ghosts.includes(ghost)) {
      throw new TypeError('killGhost: ghost is not one of state.ghosts');
    }
    if (typeof cause !== 'string' || cause === '') {
      throw new TypeError('killGhost: cause must be a non-empty string');
    }
    const events = [];
    killGhostInto(ghost, cause, events);
    return events;
  }

  // Is this ghost edible on this touch check? Before the ghosts move (phase 1)
  // the ghost's own mode is one step stale (a power pellet eaten this very step
  // has not frightened it yet, and a window that just ended has not released
  // it), so the global mode decides. After they moved it is the ghost's own.
  function edibleNow(ghost, afterMove) {
    if (afterMove) return ghost.mode === MODE.FRIGHTENED;
    return state.mode === MODE.FRIGHTENED && !ghost.immune;
  }

  // One collision pass (D: tile-wise, every step). prevPac / prevTiles are the
  // centre tiles at the start of the step. Every touchable ghost that shares
  // Pac-Man's centre tile or swapped tiles with him (tilesTouch) is either
  // eaten (frightened; a phased Pac-Man still eats) or lethal. Eating comes
  // first, so a ghost eaten on the same tile as a lethal one still scores.
  // Lethal touches kill Pac-Man unless pac.phased or the level was just cleared
  // (eating the last pellet wins the tie). Returns true when Pac-Man died.
  function touchGhosts(prevPac, prevTiles, events, afterMove) {
    const now = playerTile(pac, maze);
    const lethal = [];
    state.ghosts.forEach((ghost, i) => {
      if (!isGhostTouchable(ghost)) return;
      if (!tilesTouch(prevPac, now, prevTiles[i], ghost.tile, maze.width)) return;
      if (edibleNow(ghost, afterMove)) {
        ghost.mode = MODE.FRIGHTENED; // adopt it now; killGhostInto scores the chain
        killGhostInto(ghost, 'eat', events);
      } else if (state.dash.active > 0) {
        // The dash kills what it touches; the boss loses 1 HP, once per dash.
        if (ghost.kind !== BOSS_KIND) {
          killGhostInto(ghost, 'dash', events, state.dash.killPoints);
        } else if (!state.dash.bossHit && hitBossInto(ghost, 'dash', events)) {
          state.dash.bossHit = true;
        }
      } else if (!isSafe(state.dash) && state.invuln === 0) {
        lethal.push(ghost);
      } // else: safe steps after a dash, or invulnerable after a lost life: nothing
    });
    if (lethal.length === 0 || pac.phased || state.cleared) return false;
    state.lives -= 1;
    if (state.lives > 0) {
      loseLife();
      events.push({ type: 'life_lost', lives: state.lives, kind: lethal[0].kind, tile: { x: now.x, y: now.y } });
      return true; // the step ends here, as for a death
    }
    state.dead = true;
    events.push({
      type: 'player_died',
      kind: lethal[0].kind,
      tile: { x: now.x, y: now.y },
    });
    return true;
  }

  // Move Pac-Man for one step: one stepPlayer call, or two sub-steps while a
  // dash is active (4 sub-units per step, still integers, still turning only on
  // tile centres). Every wall he runs into is offered to the abilities
  // (onWallHit): a dash held against a wall counts as a hit each sub-step even
  // when stepPlayer was silent because he had already stopped.
  function movePlayer(dir, events) {
    const dashing = api.isDashing();
    const subSteps = dashing ? 2 : 1;
    for (let s = 0; s < subSteps; s++) {
      const own = stepPlayer(pac, maze, s === 0 ? dir : null);
      events.push(...own);
      if (held.length === 0) continue;
      let hit = own.find((e) => e.type === 'wall_hit');
      // (A Pac-Man who has never moved has no heading yet: his pending first
      // press is the direction he dashes in.)
      const heading = pac.dir ?? (pac.buffer ? pac.buffer.dir : null);
      if (!hit && dashing && !pac.moving && heading !== null && isAligned(pac)) {
        const tile = stepTile(maze, pac.x / SUB, pac.y / SUB, heading);
        if (tile) hit = { dir: heading, tx: tile.x, ty: tile.y };
      }
      if (hit) callHooks('onWallHit', { dir: hit.dir, tx: hit.tx, ty: hit.ty });
    }
  }

  function step(input = null) {
    if (state.cleared || state.dead) return [];
    // Validate everything first: a bad action or direction changes nothing.
    const { dir, actions } = parseInput(input);
    if (dir !== null && !Object.hasOwn(DIRS, dir)) {
      throw new TypeError(`step: invalid direction ${JSON.stringify(dir)}`);
    }
    const prevPac = playerTile(pac, maze);
    const invulnAtStart = state.invuln > 0; // counts down at the end of this step
    const events = [];
    sink = events;
    // The default dash starts first (this very step, before anything moves),
    // then the abilities read the input (a blast fires this step too).
    if (actions.includes('dash')) {
      const heading = dir ?? pac.dir;
      if (startDash(state.dash, heading)) {
        events.push({
          type: 'dash_start',
          source: 'dash',
          dir: heading,
          steps: state.dash.dashSteps,
          charges: state.dash.charges,
          maxCharges: state.dash.maxCharges,
        });
      }
    }
    callHooks('onInput', { dir, actions: new Set(actions) });
    movePlayer(dir, events);
    state.tick += 1;
    if (state.frightenedTimer > 0) {
      state.frightenedTimer -= 1;
      // The window is over: nobody is immune to it any more.
      if (state.frightenedTimer === 0) for (const g of state.ghosts) g.immune = false;
    }
    eatPellets(events);
    updatePickup(events);
    // Ghost reversal on a mode change is done by stepGhost itself (once per
    // ghost per change), so the scheduler's `reverse` flag is not needed here.
    state.mode = sched.update(state.frightenedTimer).mode;
    const prevTiles = state.ghosts.map((g) => ({ x: g.tile.x, y: g.tile.y }));
    // Phase 1: Pac-Man has moved, the ghosts have not. Phase 2: after they move.
    // A death ends the step at once: the ghosts do not move on the death step.
    if (touchGhosts(prevPac, prevTiles, events, false)) return events;
    if (state.boss && state.boss.hp === 0) return events; // boss died: level over
    moveGhosts(events);
    touchGhosts(prevPac, prevTiles, events, true);
    events.push(...tickDash(state.dash)); // dash timers and recharge, before the abilities see the end of the step
    if (invulnAtStart) state.invuln -= 1;
    callHooks('onStep'); // cooldowns count down at the end of the step
    return events;
  }

  // Level-generation hooks run once, on the game's own maze copy.
  callHooks('onLevelGen');

  return { maze, rng, state, step, killGhost, hitBoss, config: cfg };
}

export default createGame;
