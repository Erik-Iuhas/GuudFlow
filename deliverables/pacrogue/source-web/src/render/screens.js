// Screens that are not the maze: the pick screen, ability chips, the HUD's run
// block and the summary / win / banner panel.
//
// Flat rects only, hard edges, palette colours only. Text is drawn in
// "Courier New" bold, a monospace face (advance = 0.6 em), so layout is computed
// from character counts and needs no measureText. All layout lives in pure
// functions (pickLayout, wrapText, chipLayout, panelLines) that bun tests; the
// draw functions only paint what they return.
//
//   drawPickScreen(ctx, view)          view.offers [{slot,name,level,
//                                      description,reason,leveledUp}], view.hud
//   drawChips(ctx, held, x, y, maxW)   held [{id, name, level}]
//   drawPanel(ctx, state, view, msg)   summary / won / level-clear panel
//
// Reads only; no state is written. No logic imports.

import { INK, PINK, YELLOW, CYAN, WHITE } from './palette.js';
import { LOGICAL_W, LOGICAL_H } from './layout.js';

export const FONT_LABEL = 'bold 14px "Courier New", monospace';
export const FONT_VALUE = 'bold 28px "Courier New", monospace';
export const FONT_TITLE = 'bold 40px "Courier New", monospace';
const font = (px) => `bold ${px}px "Courier New", monospace`;

export const CHAR_W = 0.6; // monospace advance as a fraction of the font size

// ── cards ───────────────────────────────────────────────────────────────────
export const CARD_X = 40;
export const CARD_W = LOGICAL_W - 2 * CARD_X; // 400
export const CARD_H = 124;
export const CARD_GAP = 14;
export const CARD_Y0 = 118;
export const CARD_OUTLINE_PX = 4; // must stay >= 3
export const CARD_NUM_W = 84; // the big slot-number block
export const CARD_TEXT_X = CARD_X + CARD_NUM_W + 14;
export const CARD_TEXT_CHARS = 33; // description wrap width (14 px text)

/** Short chip labels (uppercase, <= 6 chars). Unknown ids fall back to the name. */
export const CHIP_LABELS = Object.freeze({
  wall_burst: 'BURST',
  ghost_blast: 'BLAST',
  dash: 'DASH',
  chain_reaction: 'CHAIN',
  magnet: 'MAGNET',
});

export function chipLabel(item) {
  return (CHIP_LABELS[item.id] ?? String(item.name ?? item.id).toUpperCase()).slice(0, 8);
}

/** Which key fires an ability (shown on cards and in the HUD). */
export const KEY_HINTS = Object.freeze({
  dash: 'SPACE',
  wall_burst: 'SPACE',
  ghost_blast: 'E',
});

/**
 * HUD reminder of the keys. Every run has the Space dash (R12), so 'SPACE DASH'
 * is always first, held or not; E BLAST joins it once Ghost Blast is held.
 */
export function keyHints(held) {
  const ids = new Set((held || []).map((h) => h.id));
  const parts = ['SPACE DASH'];
  if (ids.has('ghost_blast')) parts.push('E BLAST');
  return parts.join('  ');
}

/** Card accent by why the offer is there: level-up yellow, combo pink, else cyan. */
export function cardColour(offer) {
  if (offer.reason === 'upgrade') return YELLOW;
  if (offer.reason === 'combo') return PINK;
  return CYAN;
}

/** The small tag on a card's meta row, '' when there is none. */
export function cardTag(offer, family = '') {
  if (offer.reason === 'upgrade') return 'LEVEL UP';
  if (offer.reason === 'combo') return 'COMBO PARTNER';
  if (offer.reason === 'suited') return `FITS ${String(family).toUpperCase()}`;
  return '';
}

/** "LV 1" for a new ability, "LV 1 > 2" for a level-up (level = level after pick). */
export function cardLevelText(offer) {
  return offer.leveledUp ? `LV ${offer.level - 1} > ${offer.level}` : `LV ${offer.level}`;
}

/** Greedy word wrap on character counts (monospace). Never returns empty lines. */
export function wrapText(text, maxChars) {
  const lines = [];
  let line = '';
  for (const word of String(text).split(/\s+/).filter(Boolean)) {
    if (line === '') line = word;
    else if (line.length + 1 + word.length <= maxChars) line += ` ${word}`;
    else {
      lines.push(line);
      line = word;
    }
  }
  if (line !== '') lines.push(line);
  return lines;
}

/** Rect of card i (0-2) in logical px. */
export function cardRect(i) {
  return { x: CARD_X, y: CARD_Y0 + i * (CARD_H + CARD_GAP), w: CARD_W, h: CARD_H };
}

/**
 * Everything the pick screen paints, as data: {title, subtitle, hint, cards[]}.
 * Each card: {rect, colour, slot, name, level, tag, lines}.
 */
export function pickLayout(view) {
  const hud = view.hud || {};
  const offers = view.offers || [];
  return {
    title: 'PICK AN UPGRADE',
    subtitle: hud.level
      ? `LEVEL ${hud.level}/${hud.levels ?? 5}  ${String(hud.family ?? '').toUpperCase()}`
      : '',
    // the last pick can have only 2 offers (R3.3): never ask for a key that does nothing
    hint: `PRESS ${offers.slice(0, 3).map((o, i) => o.slot ?? i + 1).join('  ')}`,
    cards: offers.slice(0, 3).map((o, i) => ({
      rect: cardRect(i),
      colour: cardColour(o),
      slot: o.slot,
      name: String(o.name).toUpperCase(),
      level: cardLevelText(o),
      key: KEY_HINTS[o.id] ?? '',
      tag: cardTag(o, hud.family),
      lines: wrapText(o.description, CARD_TEXT_CHARS),
    })),
  };
}

// ── chips ───────────────────────────────────────────────────────────────────
export const CHIP_H = 20;
export const CHIP_PX = 11; // text size
export const CHIP_PAD = 6;
export const CHIP_GAP = 6;
export const CHIP_OUTLINE_PX = 2;
export const CHIP_BAR_PX = 3; // cooldown bar thickness

/**
 * Chips for held abilities wrapped into rows no wider than maxW from (x, y).
 * Each: {x, y, w, h, text, level, filled, cooling, bar}; level 2 is filled
 * (maxed); `bar` is the pink cooldown bar width in px (0 when ready).
 */
export function chipLayout(held, x, y, maxW = Infinity) {
  const out = [];
  let cx = x;
  let cy = y;
  for (const item of held || []) {
    const ready = `${chipLabel(item)} LV${item.level}`;
    const w = Math.ceil(ready.length * CHIP_PX * CHAR_W) + 2 * CHIP_PAD;
    // item.cool = {steps, max, active} (see flow.js cooldownInfo): while it
    // recharges the chip shows the seconds left and a pink bar under the text.
    const cool = item.cool && item.cool.steps > 0 ? item.cool : null;
    const text = cool ? `${chipLabel(item)} ${Math.ceil(cool.steps / 60)}S` : ready;
    const bar = cool ? Math.max(1, Math.round(((w - 2 * CHIP_OUTLINE_PX) * Math.min(cool.steps, cool.max)) / cool.max)) : 0;
    if (cx > x && cx + w > x + maxW) {
      cx = x;
      cy += CHIP_H + CHIP_GAP;
    }
    out.push({ x: cx, y: cy, w, h: CHIP_H, text, level: item.level, filled: item.level >= 2, cooling: Boolean(cool), bar });
    cx += w + CHIP_GAP;
  }
  return out;
}

export function drawChips(ctx, held, x, y, maxW) {
  const chips = chipLayout(held, x, y, maxW);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.font = font(CHIP_PX);
  for (const c of chips) {
    ctx.fillStyle = CYAN;
    ctx.fillRect(c.x, c.y, c.w, c.h);
    if (!c.filled) {
      ctx.fillStyle = INK;
      ctx.fillRect(c.x + CHIP_OUTLINE_PX, c.y + CHIP_OUTLINE_PX, c.w - 2 * CHIP_OUTLINE_PX, c.h - 2 * CHIP_OUTLINE_PX);
    }
    if (c.bar > 0) {
      ctx.fillStyle = PINK;
      ctx.fillRect(c.x + CHIP_OUTLINE_PX, c.y + c.h - CHIP_OUTLINE_PX - CHIP_BAR_PX, c.bar, CHIP_BAR_PX);
    }
    ctx.fillStyle = c.filled ? INK : CYAN;
    ctx.fillText(c.text, c.x + CHIP_PAD, c.y + (c.h >> 1) - 1);
  }
  return chips;
}

// ── pick screen ─────────────────────────────────────────────────────────────
export function drawPickScreen(ctx, view) {
  const L = pickLayout(view);
  const cx = LOGICAL_W >> 1;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = FONT_TITLE;
  ctx.fillStyle = YELLOW;
  ctx.fillText(L.title, cx, 44);
  ctx.font = font(18);
  ctx.fillStyle = CYAN;
  ctx.fillText(L.subtitle, cx, 88);

  for (const c of L.cards) {
    const { x, y, w, h } = c.rect;
    const O = CARD_OUTLINE_PX;
    ctx.fillStyle = c.colour;
    ctx.fillRect(x, y, w, h); // outline (the interior is painted over it)
    ctx.fillStyle = INK;
    ctx.fillRect(x + O, y + O, w - 2 * O, h - 2 * O);
    ctx.fillStyle = c.colour;
    ctx.fillRect(x, y, CARD_NUM_W, h); // slot-number block, flush with the outline

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = font(72);
    ctx.fillStyle = INK;
    ctx.fillText(String(c.slot), x + (CARD_NUM_W >> 1), y + (h >> 1) + 4);

    ctx.textAlign = 'left';
    ctx.font = font(24);
    ctx.fillStyle = WHITE;
    ctx.fillText(c.name, CARD_TEXT_X, y + 28);
    if (c.key) {
      ctx.textAlign = 'right';
      ctx.font = font(14);
      ctx.fillStyle = c.colour;
      ctx.fillText(`[${c.key}]`, x + w - O - 10, y + 28);
      ctx.textAlign = 'left';
    }

    ctx.font = font(14);
    ctx.fillStyle = c.colour;
    ctx.fillText(c.level, CARD_TEXT_X, y + 54);
    if (c.tag) {
      ctx.textAlign = 'right';
      ctx.fillText(c.tag, x + w - O - 10, y + 54);
      ctx.textAlign = 'left';
    }

    ctx.fillStyle = WHITE;
    c.lines.slice(0, 2).forEach((line, i) => ctx.fillText(line, CARD_TEXT_X, y + 80 + i * 18));
  }

  const n = Math.max(1, L.cards.length); // 2 offers on the last pick: the footer follows the cards
  const footY = CARD_Y0 + n * CARD_H + (n - 1) * CARD_GAP + 16;
  const held = (view.hud && view.hud.held) || [];
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = FONT_LABEL;
  ctx.fillStyle = WHITE;
  ctx.fillText(L.hint, cx, footY);
  if (held.length > 0) drawChips(ctx, held, CARD_X, footY + 14, CARD_W);
}

// ── motion indicator (M key) ────────────────────────────────────────────────
/** The label for the reduced-motion state. */
export function motionLabel(reduced) {
  return reduced ? 'M: REDUCED' : 'M: MOTION';
}

/** Tiny centred label in the top margin: PINK when reduced, CYAN when not. */
export function drawMotionIndicator(ctx, reduced) {
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.font = font(10);
  ctx.fillStyle = reduced ? PINK : CYAN;
  ctx.fillText(motionLabel(reduced), LOGICAL_W >> 1, 0);
}

// ── dash pips (R12.4) ───────────────────────────────────────────────────────
// One pip per MAXIMUM charge in the score strip, between the level block and the
// DOTS counter: a solid YELLOW block per charge held, an empty CYAN frame per
// charge spent. The next pip to come back (the first empty one) fills from the
// bottom with a flat PINK bar as the recharge progresses. The label turns WHITE
// while Pac-Man cannot be killed (mid-dash and the moment after).
export const PIPS_X = 316;
export const PIPS_Y = 26;
export const PIP_W = 16;
export const PIP_H = 24;
export const PIP_GAP = 4;
export const PIP_OUTLINE_PX = 2;
export const PIPS_LABEL_Y = 10;

/**
 * Pips and label as data (pure). `info` is flow.js dashInfo(state.dash)
 * ({charges, max, ratio, safe, ...}); null (no dash state) gives null.
 * Each pip: {x, y, w, h, filled, recharging, bar} with bar = the CYAN
 * recharge bar's height in px inside the frame (0 unless it is the next pip;
 * the bar is drawn PINK, matching a chip's cooldown bar).
 */
export function pipLayout(info) {
  if (!info || !(info.max > 0)) return null;
  const inner = PIP_H - 2 * PIP_OUTLINE_PX;
  const pips = [];
  for (let i = 0; i < info.max; i++) {
    const filled = i < info.charges;
    const recharging = !filled && i === info.charges;
    const ratio = Math.max(0, Math.min(1, info.ratio ?? 0));
    pips.push({
      x: PIPS_X + i * (PIP_W + PIP_GAP),
      y: PIPS_Y,
      w: PIP_W,
      h: PIP_H,
      filled,
      recharging,
      // never a full bar before the charge is back, never a hidden one once it has begun
      bar: recharging && ratio > 0 ? Math.min(inner - 1, Math.max(1, Math.round(inner * ratio))) : 0,
    });
  }
  return {
    label: { text: 'DASH', x: PIPS_X, y: PIPS_LABEL_Y, colour: info.safe ? WHITE : CYAN },
    pips,
  };
}

/** Paint the dash pips; returns the layout (null and nothing drawn without a dash). */
export function drawDashPips(ctx, info) {
  const L = pipLayout(info);
  if (!L) return null;
  const O = PIP_OUTLINE_PX;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.font = FONT_LABEL;
  ctx.fillStyle = L.label.colour;
  ctx.fillText(L.label.text, L.label.x, L.label.y);
  for (const p of L.pips) {
    if (p.filled) {
      ctx.fillStyle = YELLOW;
      ctx.fillRect(p.x, p.y, p.w, p.h);
      continue;
    }
    ctx.fillStyle = CYAN;
    ctx.fillRect(p.x, p.y, p.w, p.h); // frame (the interior is inked over it)
    ctx.fillStyle = INK;
    ctx.fillRect(p.x + O, p.y + O, p.w - 2 * O, p.h - 2 * O);
    if (p.bar > 0) {
      ctx.fillStyle = PINK; // the same pink as a chip's cooldown bar
      ctx.fillRect(p.x + O, p.y + p.h - O - p.bar, p.w - 2 * O, p.bar);
    }
  }
  return L;
}

// ── lives ───────────────────────────────────────────────────────────────────
// One flat palette square per remaining life, in the score strip beside the
// dash pips. Pure layout first, so the count is testable without a canvas.
export const LIFE_X = 124; // right of the score digits (x 16..117)
export const LIFE_Y = 50;
export const LIFE_PX = 8;
export const LIFE_STEP = 14;

/** Icon rects for `lives` remaining lives (pure); none for 0 or a bad value. */
export function lifeLayout(lives) {
  const n = Number.isInteger(lives) && lives > 0 ? lives : 0;
  const out = [];
  for (let i = 0; i < n; i++) out.push({ x: LIFE_X + i * LIFE_STEP, y: LIFE_Y, w: LIFE_PX, h: LIFE_PX });
  return out;
}

/** Paint the lives icons (YELLOW, one fillRect each). Returns the rects drawn. */
export function drawLives(ctx, lives) {
  const rects = lifeLayout(lives);
  ctx.fillStyle = YELLOW;
  for (const r of rects) ctx.fillRect(r.x, r.y, r.w, r.h);
  return rects;
}

// ── HUD run block ───────────────────────────────────────────────────────────
/** Centre of the score strip: level n/5 over the family name. */
export function hudRunText(hud) {
  if (!hud) return null;
  return {
    label: `LEVEL ${hud.level}/${hud.levels ?? 5}`,
    value: String(hud.family ?? '').toUpperCase(),
  };
}

export const HUD_CHIPS_Y = LOGICAL_H - 22; // footer strip under the maze

export function drawHudRun(ctx, hud) {
  const t = hudRunText(hud);
  if (!t) return;
  const cx = LOGICAL_W >> 1;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.font = FONT_LABEL;
  ctx.fillStyle = CYAN;
  ctx.fillText(t.label, cx, 10);
  ctx.font = font(24);
  ctx.fillStyle = WHITE;
  ctx.fillText(t.value, cx, 26);
  // The key hint is always there (SPACE DASH needs no ability): it takes the
  // right of the footer and the chips get what is left of the row.
  const hint = keyHints(hud.held);
  const left = LOGICAL_W - 16 - Math.ceil(hint.length * CHIP_PX * CHAR_W);
  if (hud.held && hud.held.length > 0) drawChips(ctx, hud.held, 16, HUD_CHIPS_Y, left - 12 - 16);
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.font = font(CHIP_PX);
  ctx.fillStyle = WHITE;
  ctx.fillText(hint, LOGICAL_W - 16, HUD_CHIPS_Y + (CHIP_H >> 1) - 1);
}

// ── summary / win / banner panel ────────────────────────────────────────────
/** Title and colour for a panel, or null when the view shows none. */
export function panelMessage(state, view) {
  if (view && view.phase) {
    if (view.phase === 'summary') return { title: 'YOU DIED', colour: PINK };
    if (view.phase === 'won') return { title: 'YOU WIN', colour: YELLOW };
    if (view.phase === 'cleared') return { title: 'LEVEL CLEAR', colour: CYAN };
    return null;
  }
  if (state && state.dead) return { title: 'YOU DIED', colour: PINK };
  if (state && state.cleared) return { title: 'LEVEL CLEAR', colour: CYAN };
  return null;
}

/**
 * Text rows of a panel (pure): {score, seed, reached, abilities, hint, held}.
 * Summary and win show seed, level reached, score and the abilities held.
 */
export function panelLines(state, view) {
  const sum = view && view.summary;
  const score = view && view.score !== undefined ? view.score : (state ? state.score : 0);
  const seed = sum ? sum.seed : view && view.seed;
  const full = Boolean(sum);
  const banner = !full && view && view.phase === 'cleared';
  const hud = view && view.hud;
  return {
    full,
    score: String(score | 0).padStart(6, '0'),
    seed: seed !== null && seed !== undefined && !banner ? `SEED ${seed}` : null,
    reached: sum
      ? (sum.won ? 'ALL 5 LEVELS CLEARED' : `REACHED LEVEL ${sum.levelReached}/${hud?.levels ?? 5}`)
      : hud && banner ? `LEVEL ${hud.level}/${hud.levels ?? 5} DONE` : null,
    held: sum ? sum.held : [],
    hint: banner ? null : 'PRESS R FOR A NEW RUN',
  };
}

export const PANEL_W = 400;

export function drawPanel(ctx, state, view, msg) {
  const P = panelLines(state, view);
  const w = PANEL_W;
  const heldRows = P.full ? chipLayout(P.held, 0, 0, w - 32).reduce((m, c) => Math.max(m, c.y + CHIP_H), 0) : 0;
  // Rows: title 44, score 84, then seed / reached / abilities, hint last.
  const h = P.full
    ? 170 + Math.max(heldRows, CHIP_H) + 44
    : 112 + (P.seed ? 24 : 0) + (P.reached ? 24 : 0) + (P.hint ? 24 : 0) + 8;
  const x = (LOGICAL_W - w) >> 1;
  const y = (LOGICAL_H - h) >> 1;
  const cx = LOGICAL_W >> 1;
  ctx.fillStyle = msg.colour;
  ctx.fillRect(x - 6, y - 6, w + 12, h + 12);
  ctx.fillStyle = INK;
  ctx.fillRect(x, y, w, h);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = msg.colour;
  ctx.font = FONT_TITLE;
  ctx.fillText(msg.title, cx, y + 44);
  ctx.fillStyle = YELLOW;
  ctx.font = FONT_VALUE;
  ctx.fillText(P.score, cx, y + 84);
  ctx.font = FONT_LABEL;
  let row = y + 112;
  if (P.seed) {
    ctx.fillStyle = CYAN;
    ctx.fillText(P.seed, cx, row);
    row += 22;
  }
  if (P.reached) {
    ctx.fillStyle = WHITE;
    ctx.fillText(P.reached, cx, row);
    row += 22;
  }
  if (P.full) {
    ctx.fillStyle = CYAN;
    ctx.fillText(P.held.length > 0 ? 'ABILITIES' : 'NO ABILITIES', cx, row);
    row += 14;
    if (P.held.length > 0) drawChips(ctx, P.held, x + 16, row, w - 32);
  }
  if (P.hint) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = FONT_LABEL;
    ctx.fillStyle = WHITE;
    ctx.fillText(P.hint, cx, y + h - 18);
  }
  return { x, y, w, h };
}
