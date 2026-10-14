// Palette: exactly five flat colours. Nothing else is ever drawn: no mixes, no
// shades, no gradients, no alpha. Hierarchy comes from shape, size and which of
// the five is used, never from a new hue.
//
//   INK     background, outlines, ghost-house gaps
//   PINK    danger: chaser ghost, damage, impact frames, death
//   YELLOW  the player, power pellets, score values
//   CYAN    maze walls, abilities, pickups, UI highlights
//   WHITE   pellets, eyes, flashes, hint text
//
// This is the only file under web/src allowed to contain colour literals
// (tests/palette.test.js scans every file).

export const INK = '#0B0B12';
export const PINK = '#FF2E88';
export const YELLOW = '#FFE600';
export const CYAN = '#00E5FF';
export const WHITE = '#FFFFFF';

export const PALETTE = Object.freeze({
  ink: INK,
  pink: PINK,
  yellow: YELLOW,
  cyan: CYAN,
  white: WHITE,
});
