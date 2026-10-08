/**
 * Axis and label arithmetic for the uPlot charts. Pure functions, no DOM.
 *
 * niceTicks -- 1-2-5 stepped ticks that cover [min, max] in about `count` steps.
 * endLabels -- end-of-line labels pushed apart so no two sit closer than `gap` px.
 */
import { roundTo } from "../lib/format";

export function niceTicks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) { const d = Math.abs(min) || 1; min -= d * 0.5; max += d * 0.5; }
  const raw = (max - min) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw)!;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number((Math.round(v / step) * step).toPrecision(12)) || 0);
  return out;
}

export function endLabels(items: { y: number; text: string }[], gap: number): { y: number; text: string }[] {
  const s = [...items].sort((a, b) => a.y - b.y).map((x) => ({ ...x }));
  for (let i = 1; i < s.length; i++) if (s[i].y - s[i - 1].y < gap) s[i].y = s[i - 1].y + gap;
  return s;
}

// ------------------------------------------------------------------ time alignment

/** A time-series x value as unix seconds: numbers are epoch milliseconds (as Plotly read them), strings are parsed (date-only = UTC midnight). */
export function toEpochSec(x: string | number): number | null {
  const ms = typeof x === "number" ? x : Date.parse(x);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/**
 * Join series that may each carry their own dates onto one ascending time axis (what uPlot
 * needs). A date that only one series has is null in the others; an unparseable date is
 * dropped; a non-finite value is null. Nothing is interpolated.
 */
export function alignSeries(series: { x: (string | number)[]; y: (number | null | undefined)[] }[]): { t: number[]; ys: (number | null)[][] } {
  const clean = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const first = series[0]?.x;
  const shared = !!first && series.every((s) => s.x === first || (s.x.length === first.length && s.x.every((v, i) => v === first[i])));
  if (shared) {
    const keep: number[] = [];
    const t: number[] = [];
    first.forEach((x, i) => {
      const e = toEpochSec(x);
      if (e !== null) {
        keep.push(i);
        t.push(e);
      }
    });
    const sorted = t.every((v, i) => i === 0 || v > t[i - 1]);
    if (sorted) return { t, ys: series.map((s) => keep.map((i) => clean(s.y[i]))) };
  }
  const maps = series.map((s) => {
    const m = new Map<number, number | null>();
    s.x.forEach((x, i) => {
      const e = toEpochSec(x);
      if (e !== null) m.set(e, clean(s.y[i]));
    });
    return m;
  });
  const all = new Set<number>();
  for (const m of maps) for (const k of m.keys()) all.add(k);
  const t = [...all].sort((a, b) => a - b);
  return { t, ys: maps.map((m) => t.map((k) => m.get(k) ?? null)) };
}

// ------------------------------------------------------------------ number formats

/** "pct" (decimals shown as %), "pctPoints" (already in %), "num", "usd", "bps", "int", "x" (multiple). */
export type ValueFormat = "pct" | "pctPoints" | "num" | "usd" | "bps" | "int" | "x";

const MINUS = "−";

function grouped(v: number, digits: number): string {
  return Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signOf(v: number, digits: number, scale: number, signed: boolean): string {
  const shown = roundTo(v * scale, digits);
  if (shown < 0) return MINUS;
  return signed && shown > 0 ? "+" : "";
}

/** A value for readouts and labels: Plex Mono figures, a true minus, "+" only when `signed`, "—" when missing. */
export function formatValue(v: number | null | undefined, f: ValueFormat | undefined, digits = 2, signed = false): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  switch (f) {
    case "pct":
      return `${signOf(v, digits, 100, signed)}${grouped(v * 100, digits)}%`;
    case "pctPoints":
      return `${signOf(v, digits, 1, signed)}${grouped(v, digits)}%`;
    case "usd":
      return `${signOf(v, digits, 1, signed)}$${grouped(v, digits)}`;
    case "bps":
      return `${signOf(v, 0, 1, signed)}${grouped(v, 0)} bp`;
    case "int":
      return `${signOf(v, 0, 1, signed)}${grouped(v, 0)}`;
    case "x":
      return `${signOf(v, digits, 1, signed)}${grouped(v, digits)}×`;
    default:
      return `${signOf(v, digits, 1, signed)}${grouped(v, digits)}`;
  }
}

/** Decimal places that resolve a tick step (0.02 → 2, 0.5 → 1, 2 → 0). */
export function stepDecimals(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 2;
  return Math.max(0, Math.min(6, Math.ceil(-Math.log10(step) - 1e-9)));
}

/** An axis tick: decimals follow the tick step; currency abbreviates by thousands only when the step does. */
export function formatTick(v: number, f: ValueFormat | undefined, step: number): string {
  switch (f) {
    case "pct":
      return formatValue(v, "pct", stepDecimals(step * 100));
    case "pctPoints":
      return formatValue(v, "pctPoints", stepDecimals(step));
    case "usd": {
      const a = Math.abs(step);
      const [unit, suffix] = a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : a >= 1e4 ? [1e3, "k"] : [1, ""];
      const d = stepDecimals(step / unit);
      return `${signOf(v / unit, d, 1, false)}$${grouped(v / unit, d)}${suffix}`;
    }
    case "bps":
      return formatValue(v, "bps", 0);
    case "int":
      return formatValue(v, "int", 0);
    case "x":
      return formatValue(v, "x", stepDecimals(step));
    default:
      return formatValue(v, "num", stepDecimals(step));
  }
}
