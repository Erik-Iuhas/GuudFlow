// Layout: fixed logical resolution, integer scale, letterboxing.
//
// The game is drawn on a fixed LOGICAL_W x LOGICAL_H grid where one logical
// pixel is exactly one logic sub-unit (SUB = 16 per tile). The canvas backing
// store is that grid multiplied by an INTEGER scale (never fractional, so
// edges stay razor sharp), centred in whatever canvas size we are given; the
// leftover border is letterbox bars drawn in ink.
//
//   const L = computeLayout({ canvasWidth, canvasHeight, mazeWidth, mazeHeight });
//   L.scale            integer device pixels per logical pixel (>= 1)
//   L.offsetX/offsetY  device-pixel position of the logical origin (letterbox)
//   L.mazeX/mazeY      logical-pixel position of the maze's top-left corner
//   subToLogical(L, sx, sy)  sub-unit (maze space) -> logical pixels
//   subToPixel(L, sx, sy)    sub-unit (maze space) -> device pixels
//   pixelToLogical(L, px, py) device pixels -> logical pixels
//
// The logical grid fits mazes up to 30 x 32 tiles at 16 logical px per tile
// (classic 28 x 31 with room to spare); `fits` reports false for anything
// bigger. Pure module apart from one constant import; no DOM access.

import { SUB } from '../logic/player.js';

export const LOGICAL_W = 480;
export const LOGICAL_H = 576;
export const HUD_H = 56; // score strip above the maze
export const FOOT_H = 24; // breathing room below the maze

/** Largest integer scale at which the logical grid fits the given pixel box. */
export function integerScale(canvasWidth, canvasHeight) {
  const s = Math.floor(Math.min(canvasWidth / LOGICAL_W, canvasHeight / LOGICAL_H));
  return Math.max(1, s);
}

/** Canvas backing-store size for a given integer scale. */
export function canvasSizeForScale(scale) {
  return { width: LOGICAL_W * scale, height: LOGICAL_H * scale };
}

export function computeLayout({
  canvasWidth = LOGICAL_W,
  canvasHeight = LOGICAL_H,
  mazeWidth = 28,
  mazeHeight = 31,
} = {}) {
  const scale = integerScale(canvasWidth, canvasHeight);
  const offsetX = Math.floor((canvasWidth - LOGICAL_W * scale) / 2);
  const offsetY = Math.floor((canvasHeight - LOGICAL_H * scale) / 2);

  const mazeW = mazeWidth * SUB;
  const mazeH = mazeHeight * SUB;
  const areaH = LOGICAL_H - HUD_H - FOOT_H;
  const mazeX = Math.floor((LOGICAL_W - mazeW) / 2);
  const mazeY = HUD_H + Math.floor((areaH - mazeH) / 2);

  return {
    scale,
    offsetX,
    offsetY,
    canvasWidth,
    canvasHeight,
    logicalW: LOGICAL_W,
    logicalH: LOGICAL_H,
    mazeX,
    mazeY,
    mazeW,
    mazeH,
    fits: mazeW <= LOGICAL_W && mazeH <= areaH,
  };
}

/** Sub-unit position (maze space) -> logical pixels. */
export function subToLogical(layout, sx, sy) {
  return { x: layout.mazeX + sx, y: layout.mazeY + sy };
}

/** Sub-unit position (maze space) -> device pixels on the canvas. */
export function subToPixel(layout, sx, sy) {
  return {
    x: layout.offsetX + (layout.mazeX + sx) * layout.scale,
    y: layout.offsetY + (layout.mazeY + sy) * layout.scale,
  };
}

/** Device pixels -> logical pixels (fractional; floor for a logical cell). */
export function pixelToLogical(layout, px, py) {
  return {
    x: (px - layout.offsetX) / layout.scale,
    y: (py - layout.offsetY) / layout.scale,
  };
}
