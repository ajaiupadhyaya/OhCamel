/**
 * Pure arithmetic for the yield-curve chart (charts/CurveChart.tsx): curves over tenor in
 * years on a log x axis. No DOM.
 */
export interface CurveLine {
  key: string;
  label: string;
  tenors: number[];
  yields: (number | null)[];
}

/** The tenors that get an axis tick: the standard points inside [lo, hi] (1M is left off to keep the short end legible). */
export const TICK_TENORS = [0.25, 1, 2, 5, 10, 30];

export function tenorSplits(lo: number, hi: number): number[] {
  return TICK_TENORS.filter((t) => t >= lo * 0.99 && t <= hi * 1.01);
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Curves aligned on the ascending union of their tenors (uPlot's AlignedData shape); a missing point is null, never interpolated. */
export function curveTable(lines: CurveLine[]): { x: number[]; ys: (number | null)[][] } {
  const xs = new Set<number>();
  for (const l of lines) l.tenors.forEach((t) => finite(t) && t > 0 && xs.add(t));
  const x = [...xs].sort((a, b) => a - b);
  const ys = lines.map((l) => {
    const at = new Map<number, number>();
    l.tenors.forEach((t, i) => {
      const v = l.yields[i];
      if (finite(t) && t > 0 && finite(v)) at.set(t, v);
    });
    return x.map((t) => at.get(t) ?? null);
  });
  return { x, ys };
}

/** 0.25 → "3M", 10 → "10Y", 1.5 → "1.5Y". */
export function fmtTenor(t: number): string {
  if (!finite(t)) return "—";
  if (t < 1 - 1e-9) return `${Math.round(t * 12)}M`;
  return `${Math.round(t * 100) / 100}Y`;
}
