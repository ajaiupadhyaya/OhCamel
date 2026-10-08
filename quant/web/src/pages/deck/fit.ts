/**
 * Console scale (Lane F2): every instrument draws in CSS pixels of the box it is given, so a
 * hairline is one pixel and a readout is 11 px at 380, 1280 and 1920 alike -- nothing is a
 * fixed drawing stretched to fit. These are the geometries; the components only draw them.
 * Pure: no DOM, no React.
 */
import { segWidth } from "./SevenSeg";

/** A seven-segment cell is 0.58 as wide as it is tall, with 0.4 of a cell between cells. */
const ASPECT = 0.58;
const GAP = 0.4;
const MIN_H = 12;

export interface DigitFit {
  w: number;
  h: number;
  gap: number;
  /** drawn width of the text at this size */
  width: number;
}

/** The largest seven-segment size, at most `maxH` tall, at which `text` fits `maxW`. */
export function fitDigits(text: string, maxW: number, maxH: number): DigitFit {
  const at = (h: number): DigitFit => {
    const w = h * ASPECT;
    const gap = w * GAP;
    return { w, h, gap, width: segWidth(text, w, gap) };
  };
  const full = at(Math.max(MIN_H, maxH));
  if (!text.length || full.width <= maxW) return full;
  return at(Math.max(MIN_H, (full.h * Math.max(0, maxW)) / full.width));
}

export interface CounterBank {
  cols: number;
  gap: number;
  /** horizontal padding inside a tile, each side */
  pad: number;
  maxH: number;
  /** one digit size for every counter in the bank */
  seg: DigitFit;
}

/** The counter bank: four tiles in a row (two on a phone), all at the longest counter's size. */
export function counterBank(width: number, lengths: number[]): CounterBank {
  const gap = 4;
  const pad = 10;
  const maxH = 40;
  const cols = width >= 560 ? 4 : 2;
  const tile = (Math.max(0, width) - (cols - 1) * gap) / cols - 2 * pad;
  const longest = Math.max(1, ...lengths);
  return { cols, gap, pad, maxH, seg: fitDigits("8".repeat(longest), tile, maxH) };
}

export interface ScopeGeometry {
  w: number;
  h: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  cx: number;
  cy: number;
  hw: number;
  hh: number;
  /** where the eight perspective lines start; each runs to (cx, cy) */
  persp: [number, number][];
  readout: { x: number; y: number; w: number; h: number; seg: DigitFit };
  caption: { x: number; y: number };
}

/** The targeting computer in a w × h pixel box: the tunnel frame, the readout under it, the caption. */
export function scopeGeometry(width: number, height: number, digits: string): ScopeGeometry {
  const w = Math.max(240, width);
  const h = Math.max(180, height);
  const inset = 10;
  const captionH = 18;
  const readH = Math.round(Math.min(72, Math.max(40, h * 0.17)));
  const x0 = inset;
  const x1 = w - inset;
  const y0 = inset;
  const y1 = h - captionH - readH - 2 * inset;
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const qx = (x1 - x0) / 4;
  const readW = Math.min(w - 2 * inset, Math.max(200, w * 0.42));
  const seg = fitDigits(digits, readW - 24, readH - 16);
  return {
    w,
    h,
    x0,
    y0,
    x1,
    y1,
    cx,
    cy,
    hw: (x1 - x0) / 2,
    hh: (y1 - y0) / 2,
    persp: [
      [x0, y0],
      [x1, y0],
      [x0, y1],
      [x1, y1],
      [x0 + qx, y0],
      [x1 - qx, y0],
      [x0 + qx, y1],
      [x1 - qx, y1],
    ],
    readout: { x: cx - readW / 2, y: y1 + inset, w: readW, h: readH, seg },
    caption: { x: cx, y: h - 5 },
  };
}

export interface RadarGeometry {
  size: number;
  c: number;
  /** rim radius */
  r: number;
}

/** The radar: a square scope set by the smaller side of its box (the width alone when height is 0). */
export function radarGeometry(width: number, height: number): RadarGeometry {
  const size = Math.max(200, Math.floor(height > 0 ? Math.min(width, height) : width));
  const c = size / 2;
  return { size, c, r: c - 14 };
}
