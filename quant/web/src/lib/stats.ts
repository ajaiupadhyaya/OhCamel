/**
 * Small descriptive statistics computed in the browser from series the API already
 * returned (e.g. a price history). Heavy analytics belong in the backend; use these only
 * for display transforms of data on screen, and say so in the panel (`notes`).
 * Inputs are arrays aligned with an ISO-date index; returns are DECIMALS.
 */
import { parseDate } from "./format";

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN;
}
export function stdev(xs: number[], ddof = 1): number {
  if (xs.length <= ddof) return NaN;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - ddof));
}
export function quantile(xs: number[], q: number): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const pos = (s.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}

/** Daily simple returns from levels (first element null). */
export function simpleReturns(levels: (number | null)[]): (number | null)[] {
  return levels.map((v, i) => (i && finite(v) && finite(levels[i - 1]) && levels[i - 1] !== 0 ? v / (levels[i - 1] as number) - 1 : null));
}

/** Rolling annualized volatility of daily log returns over `window` sessions. */
export function rollingVol(levels: (number | null)[], window = 21, periodsPerYear = 252): (number | null)[] {
  const lr = levels.map((v, i) => (i && finite(v) && finite(levels[i - 1]) ? Math.log(v / (levels[i - 1] as number)) : null));
  const out: (number | null)[] = new Array(levels.length).fill(null);
  for (let i = window; i < lr.length; i++) {
    const w = lr.slice(i - window + 1, i + 1).filter(finite);
    if (w.length >= Math.floor(window * 0.8)) out[i] = stdev(w) * Math.sqrt(periodsPerYear);
  }
  return out;
}

/** Summary of a level path over its whole length. */
export function pathStats(dates: string[], levels: (number | null)[], periodsPerYear = 252) {
  const idx = levels.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => finite(p[1]));
  if (idx.length < 2) return null;
  const first = idx[0];
  const last = idx[idx.length - 1];
  const r = simpleReturns(levels).filter(finite);
  const d0 = parseDate(dates[first[0]])!;
  const d1 = parseDate(dates[last[0]])!;
  const years = (d1.getTime() - d0.getTime()) / (365.25 * 86400e3);
  const total = last[1] / first[1] - 1;
  const cagr = years > 0 ? (1 + total) ** (1 / years) - 1 : NaN;
  const vol = stdev(r) * Math.sqrt(periodsPerYear);
  let peak = -Infinity;
  let mdd = 0;
  for (const [, v] of idx) {
    peak = Math.max(peak, v);
    mdd = Math.min(mdd, v / peak - 1);
  }
  return {
    total,
    cagr,
    vol,
    /** mean/sd of daily returns × √N — no risk-free rate subtracted */
    returnToVol: (mean(r) / stdev(r)) * Math.sqrt(periodsPerYear),
    maxDrawdown: mdd,
    best: Math.max(...r),
    worst: Math.min(...r),
    q05: quantile(r, 0.05),
    sessions: r.length,
    years,
  };
}

/** Calendar-month returns from month-end levels: {years, months (1..12), z[year][month]}. */
export function monthlyReturns(dates: string[], levels: (number | null)[]) {
  const monthEnd = new Map<string, number>(); // "YYYY-MM" -> last level
  dates.forEach((d, i) => {
    const v = levels[i];
    if (finite(v)) monthEnd.set(d.slice(0, 7), v);
  });
  const keys = [...monthEnd.keys()].sort();
  const byYear = new Map<number, (number | null)[]>();
  for (let k = 1; k < keys.length; k++) {
    const [y, m] = keys[k].split("-").map(Number);
    const r = monthEnd.get(keys[k])! / monthEnd.get(keys[k - 1])! - 1;
    if (!byYear.has(y)) byYear.set(y, new Array(12).fill(null));
    byYear.get(y)![m - 1] = r;
  }
  const years = [...byYear.keys()].sort((a, b) => b - a);
  return { years, z: years.map((y) => byYear.get(y)!) };
}
