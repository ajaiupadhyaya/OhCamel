/**
 * Rates & Macro — pure transforms of payloads already on screen (tested in derive.test.ts):
 * tenor ticks for a log maturity axis, joining curves sampled at different maturities onto one
 * x, inversion runs, NBER and regime bands for XYChart, the explorer's series styles, and the
 * P6 (regime.hmm) probability and holdout tables.
 */
import type { Tone, XYBand } from "../../charts/XYChart";
import { parseDate, toIsoDate } from "../../lib/format";
import type { Episode } from "./types";

// ------------------------------------------------------------------ tenors
export const STD_TENORS = [1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30];

/** 0.0833 -> "1M", 0.5 -> "6M", 2 -> "2Y", 0.75 -> "9M", 2.5 -> "2.5Y". */
export function tenorLabel(t: number | string): string {
  const x = typeof t === "string" ? parseFloat(t) : t;
  if (!Number.isFinite(x)) return String(t);
  if (x < 1 - 1e-9) return `${Math.round(x * 12)}M`;
  return `${Math.round(x * 100) / 100}Y`;
}

/** Standard Treasury tenors inside [lo, hi] as XYChart x ticks. */
export function tenorTicks(lo: number, hi: number): { at: number; label: string }[] {
  return STD_TENORS.filter((t) => t >= lo * 0.99 && t <= hi * 1.01).map((t) => ({ at: t, label: tenorLabel(t) }));
}

/**
 * Join numeric-x series sampled at different points (a curve's quoted nodes and a fitted curve)
 * onto one ascending x; a point only one series has is null in the others. Non-finite x dropped.
 */
export function alignNumeric(sets: { x: number[]; y: (number | null | undefined)[] }[]): { x: number[]; ys: (number | null)[][] } {
  const key = (v: number) => Math.round(v * 1e9) / 1e9;
  const maps = sets.map((s) => {
    const m = new Map<number, number | null>();
    s.x.forEach((x, i) => {
      if (!Number.isFinite(x)) return;
      const v = s.y[i];
      m.set(key(x), typeof v === "number" && Number.isFinite(v) ? v : null);
    });
    return m;
  });
  const x = [...new Set(maps.flatMap((m) => [...m.keys()]))].sort((a, b) => a - b);
  return { x, ys: maps.map((m) => x.map((k) => m.get(k) ?? null)) };
}

// ------------------------------------------------------------------ runs and bands
/** Runs of a flag along an index -> [first, last] index values of each run (inclusive). */
export function runs(index: (string | number)[], flag: (i: number) => boolean): [string, string][] {
  const out: [string, string][] = [];
  let s: number | null = null;
  for (let i = 0; i < index.length; i++) {
    const f = flag(i);
    if (f && s === null) s = i;
    if (!f && s !== null) {
      out.push([String(index[s]), String(index[i - 1])]);
      s = null;
    }
  }
  if (s !== null) out.push([String(index[s]), String(index[index.length - 1])]);
  return out;
}

export interface Inversion {
  start: string;
  end: string;
  n: number;
  /** Deepest reading inside the run (the series' units). */
  min: number;
  /** The run reaches the last observation. */
  open: boolean;
}

/** Runs of a series below zero, newest first, with their length and depth. */
export function inversions(index: (string | number)[], values: (number | null | undefined)[]): Inversion[] {
  const out: Inversion[] = [];
  let s = -1;
  const close = (e: number) => {
    let min = Infinity;
    for (let k = s; k <= e; k++) min = Math.min(min, values[k] as number);
    out.push({ start: String(index[s]), end: String(index[e]), n: e - s + 1, min, open: e === index.length - 1 });
    s = -1;
  };
  for (let i = 0; i < index.length; i++) {
    const v = values[i];
    const neg = typeof v === "number" && Number.isFinite(v) && v < 0;
    if (neg && s < 0) s = i;
    if (!neg && s >= 0) close(i - 1);
  }
  if (s >= 0) close(index.length - 1);
  return out.reverse();
}

/** NBER recession spans (peak month start to the end of the trough month) from `from` on. */
export function nberBands(episodes: Episode[] | undefined, from?: string): XYBand[] {
  return (episodes ?? [])
    .filter((e) => !from || e.end >= from)
    .map((e) => {
      const end = parseDate(e.end);
      return { from: e.start, to: end ? toIsoDate(new Date(end.getFullYear(), end.getMonth() + 1, 0)) : e.end, tone: "faint" as const };
    });
}

/** The most likely state at each row of a probability frame (column order = state order). */
export function argmaxStates(cols: string[], data: Record<string, (number | null)[]>, n: number): number[] {
  return Array.from({ length: n }, (_, i) => {
    let best = 0;
    let bv = -Infinity;
    cols.forEach((c, j) => {
      const v = data[c]?.[i];
      if (typeof v === "number" && v > bv) {
        bv = v;
        best = j;
      }
    });
    return best;
  });
}

/**
 * Spans where the most likely regime is not the calmest: with three states the middle one is
 * faint and the most volatile hatched; with two, the turbulent one hatched.
 */
export function regimeBands(index: (string | number)[], states: number[], k: number): XYBand[] {
  const out: XYBand[] = [];
  for (let j = 1; j < k; j++) for (const [a, b] of runs(index, (i) => states[i] === j)) out.push({ from: a, to: b, tone: j === k - 1 ? "hatch" : "faint" });
  return out;
}

// ------------------------------------------------------------------ explorer
/** Ink tones then dashes, so up to nine overlaid series stay distinct without colour. Signal is never used: a series is not a loss. */
export function seriesStyle(i: number): { tone: Tone; dash: "solid" | "dash" | "dot" } {
  const tones: Tone[] = ["ink", "ink2", "ink3"];
  const dashes = ["solid", "dash", "dot"] as const;
  return { tone: tones[i % 3], dash: dashes[Math.floor(i / 3) % 3] };
}

// ------------------------------------------------------------------ P6 regime.hmm
export interface ProbRow {
  date: string;
  p_high: number | null;
  p_high_smoothed_history: number | null;
}

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const day = (x: unknown): string | null => {
  if (typeof x === "string") return x.slice(0, 10);
  if (typeof x === "number" && Number.isFinite(x)) return new Date(x).toISOString().slice(0, 10);
  return null;
};

/** The probs table as dated filtered / smoothed P(high-vol) rows, oldest first; undated rows dropped. */
export function probRows(rows: Record<string, unknown>[]): ProbRow[] {
  return rows
    .map((r) => ({ date: day(r.date), p_high: num(r.p_high), p_high_smoothed_history: num(r.p_high_smoothed_history) }))
    .filter((r): r is ProbRow => r.date !== null)
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

/** The newest week with a filtered probability, and the refit count. */
export function latestProb(rows: ProbRow[]): ProbRow | null {
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].p_high !== null) return rows[i];
  return null;
}

export interface Q02Holdout {
  selection_weeks: number | null;
  boundary_weeks_purged: number | null;
  holdout_weeks: number | null;
  hac_t_p: number | null;
  hac_lags: number | null;
  r2_ewma: number | null;
  r2_ewma_p: number | null;
  dm_stat: number | null;
  dm_pvalue: number | null;
  qlike_ewma: number | null;
  qlike_ewma_p: number | null;
  oos_r2_ewma: number | null;
  oos_r2_ewma_p: number | null;
  holdout_start: string | null;
  holdout_end: string | null;
  selection_last_target_end: string | null;
  methodology_version: number | null;
}

const Q02_NUMS = ["selection_weeks", "boundary_weeks_purged", "holdout_weeks", "hac_t_p", "hac_lags", "r2_ewma", "r2_ewma_p", "dm_stat", "dm_pvalue", "qlike_ewma", "qlike_ewma_p", "oos_r2_ewma", "oos_r2_ewma_p", "methodology_version"] as const;

/** The holdout row, coerced; null when the table has no row (not yet evaluated). */
export function q02Holdout(rows: Record<string, unknown>[] | null): Q02Holdout | null {
  const r = rows?.[0];
  if (!r) return null;
  const out: Record<string, unknown> = {};
  for (const k of Q02_NUMS) out[k] = num(r[k]);
  for (const k of ["holdout_start", "holdout_end", "selection_last_target_end"]) out[k] = day(r[k]);
  return out as unknown as Q02Holdout;
}

/** EXP-Q02's gate names as terse caps codes. */
export const Q02_GATE: Record<string, string> = {
  selection_hac_t_on_p: "HAC t ON P · SELECTION",
  holdout_dm_one_sided_p: "DM p · HOLDOUT · ONE-SIDED",
  incremental_r2_selection: "ΔR² · SELECTION",
  incremental_r2_holdout: "ΔR² OOS · HOLDOUT",
};
