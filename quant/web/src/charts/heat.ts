/**
 * The heatmap's colour scale, pure. Three forms:
 *  - sequential: the ink density ramp, low to high;
 *  - diverging "pnl" (returns, Sharpe): signal for losses to ink for gains around `zmid`;
 *  - diverging "neutral" (correlations, weights: signed, neither good nor bad): ink only. The
 *    magnitude is ink density and a negative is the same density with a back-hatch, so signal
 *    red stays reserved for losses and breaches.
 */
import { rampColor } from "./theme";

export interface HeatCell {
  fill: string;
  /** Draw the negative back-hatch over the fill (neutral palette only). */
  hatch: boolean;
}

export interface HeatScale {
  div: boolean;
  lo: number;
  hi: number;
  cell: (v: number) => HeatCell;
  /** The readout marks this value in signal (a loss on the pnl palette). */
  signal: (v: number) => boolean;
}

export interface HeatScaleOpts {
  diverging?: boolean;
  zmid?: number;
  zmin?: number;
  zmax?: number;
  palette?: "pnl" | "neutral";
}

export function heatScale(flat: number[], { diverging, zmid = 0, zmin, zmax, palette = "pnl" }: HeatScaleOpts, t: { seq: string[]; div: string[]; divNeutral: string[] }): HeatScale {
  const div = diverging ?? (flat.some((v) => v < 0) && flat.some((v) => v > 0));
  if (div) {
    const m = Math.max(...flat.map((v) => Math.abs(v - zmid)), 1e-12);
    const lo = zmin ?? zmid - m;
    const hi = zmax ?? zmid + m;
    if (palette === "neutral") {
      // mid, then the positive half of the ramp: one ink density for both signs
      const ink = t.divNeutral.slice(Math.floor(t.divNeutral.length / 2));
      const span = Math.max(Math.abs(hi - zmid), Math.abs(zmid - lo)) || 1;
      return { div, lo, hi, cell: (v) => ({ fill: rampColor(ink, Math.abs(v - zmid) / span), hatch: v < zmid }), signal: () => false };
    }
    const p = (v: number) => (v < zmid ? 0.5 * ((v - lo) / (zmid - lo || 1)) : 0.5 + 0.5 * ((v - zmid) / (hi - zmid || 1)));
    return { div, lo, hi, cell: (v) => ({ fill: rampColor(t.div, p(v)), hatch: false }), signal: (v) => v < zmid };
  }
  const lo = zmin ?? (flat.length ? Math.min(...flat) : 0);
  const hi = zmax ?? (flat.length ? Math.max(...flat) : 1);
  return { div, lo, hi, cell: (v) => ({ fill: rampColor(t.seq, (v - lo) / (hi - lo || 1)), hatch: false }), signal: () => false };
}
