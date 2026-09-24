/**
 * Converters from the serializer's wire shapes into chart traces / table rows,
 * plus a few pure transforms used across pages. See types.ts for the shapes.
 */
import type { Data as PlotlyData } from "plotly.js";
import type { FramePayload, SeriesPayload } from "./types";

export type XY = { x: (string | number)[]; y: (number | null)[] };

/** {index, values} -> {x, y}. */
export function seriesXY(s: SeriesPayload | null | undefined): XY {
  if (!s) return { x: [], y: [] };
  return { x: s.index ?? [], y: (s.values ?? []) as (number | null)[] };
}

/** One column of a frame as {x, y}. */
export function frameColumn(f: FramePayload | null | undefined, col: string): XY {
  if (!f) return { x: [], y: [] };
  return { x: f.index ?? [], y: (f.data?.[col] ?? []) as (number | null)[] };
}

/**
 * Frame -> Plotly line traces (one per column, or the given subset, in order).
 * Colours are left to the Chart template's colorway (fixed categorical order).
 */
export function frameToTraces(
  f: FramePayload | null | undefined,
  opts: { columns?: string[]; type?: "scatter" | "bar"; mode?: string; names?: Record<string, string>; hovertemplate?: string } = {},
): PlotlyData[] {
  if (!f) return [];
  const cols = opts.columns ?? f.columns ?? Object.keys(f.data ?? {});
  return cols.map((c) => ({
    type: opts.type ?? "scatter",
    mode: opts.type === "bar" ? undefined : (opts.mode ?? "lines"),
    name: opts.names?.[c] ?? c,
    x: f.index,
    y: f.data?.[c] ?? [],
    hovertemplate: opts.hovertemplate,
  })) as PlotlyData[];
}

/** Series -> one Plotly trace. */
export function seriesToTrace(s: SeriesPayload | null | undefined, name = "", extra: Record<string, unknown> = {}): PlotlyData {
  const { x, y } = seriesXY(s);
  return { type: "scatter", mode: "lines", name, x, y, ...extra } as PlotlyData;
}

/** Frame -> row-major records: [{[indexKey]: idx, col1: v, ...}]. */
export function frameToRows<T extends Record<string, unknown> = Record<string, unknown>>(f: FramePayload | null | undefined, indexKey = "index"): T[] {
  if (!f) return [];
  const cols = f.columns ?? Object.keys(f.data ?? {});
  return (f.index ?? []).map((idx, i) => {
    const row: Record<string, unknown> = { [indexKey]: idx };
    for (const c of cols) row[c] = f.data?.[c]?.[i] ?? null;
    return row as T;
  });
}

/** Frame -> heatmap z matrix (rows = index, cols = columns). */
export function frameToMatrix(f: FramePayload | null | undefined): { x: string[]; y: (string | number)[]; z: (number | null)[][] } {
  if (!f) return { x: [], y: [], z: [] };
  const cols = f.columns ?? [];
  return {
    x: cols,
    y: f.index,
    z: f.index.map((_, i) => cols.map((c) => (f.data?.[c]?.[i] ?? null) as number | null)),
  };
}

/** Is the value a SeriesPayload / FramePayload (defensive parsing of unknown payloads)? */
export const isSeries = (v: unknown): v is SeriesPayload =>
  !!v && typeof v === "object" && Array.isArray((v as any).index) && Array.isArray((v as any).values);
export const isFrame = (v: unknown): v is FramePayload =>
  !!v && typeof v === "object" && Array.isArray((v as any).index) && Array.isArray((v as any).columns) && typeof (v as any).data === "object";

/** Rebase a price/level path to `base` at its first finite value (growth of $1 = base 1). */
export function rebase(y: (number | null)[], base = 1): (number | null)[] {
  const first = y.find((v) => v !== null && Number.isFinite(v)) as number | undefined;
  if (!first) return y.map(() => null);
  return y.map((v) => (v === null ? null : (v / first) * base));
}

/** Cumulative wealth from simple returns (decimal), starting at `base`. */
export function cumulative(returns: (number | null)[], base = 1): number[] {
  let w = base;
  return returns.map((r) => (w *= 1 + (r ?? 0)));
}

/** Drawdown path (decimal, ≤ 0) from a wealth/price path. */
export function drawdown(levels: (number | null)[]): (number | null)[] {
  let peak = -Infinity;
  return levels.map((v) => {
    if (v === null || !Number.isFinite(v)) return null;
    peak = Math.max(peak, v);
    return v / peak - 1;
  });
}

/** Simple returns from a level path. */
export function pctChange(levels: (number | null)[]): (number | null)[] {
  return levels.map((v, i) => {
    const p = levels[i - 1];
    return i === 0 || v === null || p === null || p === undefined || p === 0 ? null : v / p - 1;
  });
}

/** Keep the last `n` points (for sparklines). */
export function tail<T>(arr: T[], n: number): T[] {
  return arr.length > n ? arr.slice(arr.length - n) : arr;
}
