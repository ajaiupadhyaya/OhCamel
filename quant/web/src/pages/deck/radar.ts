/**
 * The radar's geometry: where each holding's blip sits on the polar scope. Pure; Radar.tsx
 * draws it and radar.test.ts checks it.
 *
 * - Bearing: fixed by book order — holding i of n sits at i·360°/n, clockwise from north,
 *   so a blip never moves around the dial between readings, only in and out.
 * - Range: |pct_var| (its Euler share of VaR) on a linear scale in which the scope's rim is
 *   RIM_SHARE. Rings are drawn at 10 / 25 / 50 %. A share beyond the rim is clamped to it
 *   and flagged `clamped`, so it is drawn at the edge with a mark rather than off-screen.
 *   A negative share (a hedge) is placed by its magnitude and drawn hollow.
 * - Size: blip area ∝ |live_weight| (radius ∝ √|w|), with |w| = 1 at BLIP_MAX, so sizes
 *   compare across readings and books. A floor keeps tiny positions visible.
 * - Tone: gain / loss by today's move; flat or unknown is neutral.
 * - A holding with no VaR share (null) is not placed — it is listed as `unplaced`, never
 *   drawn at the centre, because the centre means "no risk".
 *
 * Coordinates are in unit-radius space (the rim is 1), centre (0, 0), y down (SVG).
 */

export interface BlipIn {
  ticker: string;
  live_weight: number | null;
  change_pct: number | null;
  component_var: number | null;
  pct_var: number | null;
}

export interface Blip {
  ticker: string;
  bearing: number; // degrees clockwise from north
  range: number; // 0..1 of the rim
  x: number;
  y: number;
  r: number; // blip radius, unit-radius space
  clamped: boolean;
  hedge: boolean;
  tone: "gain" | "loss" | "flat";
  pct_var: number;
  live_weight: number | null;
  change_pct: number | null;
}

export interface RadarModel {
  blips: Blip[];
  unplaced: string[];
  rings: { share: number; range: number }[];
  summary: string;
}

export const RINGS = [0.1, 0.25, 0.5];
export const RIM_SHARE = 0.6;
export const BLIP_MIN = 0.018;
export const BLIP_MAX = 0.11;

const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

export function bearingOf(i: number, n: number): number {
  return n > 0 ? (i * 360) / n : 0;
}

export function rangeOf(pctVar: number): { range: number; clamped: boolean } {
  const a = Math.abs(pctVar);
  return a > RIM_SHARE ? { range: 1, clamped: true } : { range: a / RIM_SHARE, clamped: false };
}

export function sizeOf(liveWeight: number | null): number {
  const w = finite(liveWeight) ? Math.min(1, Math.abs(liveWeight)) : 0;
  return Math.max(BLIP_MIN, BLIP_MAX * Math.sqrt(w));
}

/** Polar → SVG (y down): bearing 0 is straight up, 90 is right. */
export function polar(bearingDeg: number, range: number): { x: number; y: number } {
  const t = (bearingDeg * Math.PI) / 180;
  const round = (v: number) => Math.round(v * 1e9) / 1e9 + 0; // sin(π) is not 1e-16, and −0 is 0
  return { x: round(range * Math.sin(t)), y: round(-range * Math.cos(t)) };
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

export function radar(items: BlipIn[] | null | undefined): RadarModel {
  const list = items ?? [];
  const n = list.length;
  const blips: Blip[] = [];
  const unplaced: string[] = [];
  list.forEach((b, i) => {
    if (!finite(b.pct_var)) {
      unplaced.push(b.ticker);
      return;
    }
    const bearing = bearingOf(i, n);
    const { range, clamped } = rangeOf(b.pct_var);
    const { x, y } = polar(bearing, range);
    const hedge = finite(b.component_var) ? b.component_var < 0 : b.pct_var < 0;
    const tone = finite(b.change_pct) && b.change_pct !== 0 ? (b.change_pct > 0 ? "gain" : "loss") : "flat";
    blips.push({ ticker: b.ticker, bearing, range, x, y, r: sizeOf(b.live_weight), clamped, hedge, tone, pct_var: b.pct_var, live_weight: b.live_weight, change_pct: b.change_pct });
  });

  const parts: string[] = [];
  if (!n) parts.push("No holdings on the radar.");
  else {
    const top = blips.slice().sort((a, b) => Math.abs(b.pct_var) - Math.abs(a.pct_var))[0];
    parts.push(`${blips.length} of ${n} holdings placed by share of value at risk.`);
    if (top) parts.push(`Largest: ${top.ticker} at ${pct(top.pct_var)} of VaR.`);
    const hedges = blips.filter((b) => b.hedge).map((b) => b.ticker);
    if (hedges.length) parts.push(`Hedging (negative component VaR): ${hedges.join(", ")}.`);
    const up = blips.filter((b) => b.tone === "gain").length;
    const down = blips.filter((b) => b.tone === "loss").length;
    parts.push(`${up} up and ${down} down today.`);
    if (unplaced.length) parts.push(`No VaR share for: ${unplaced.join(", ")}.`);
  }

  return { blips, unplaced, rings: RINGS.map((s) => ({ share: s, range: s / RIM_SHARE })), summary: parts.join(" ") };
}
