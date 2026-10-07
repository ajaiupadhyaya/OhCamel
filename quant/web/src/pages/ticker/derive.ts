/**
 * Ticker page — display transforms of the price history the API returned (no analytics that
 * belong in the backend): the range window, the drawdown path and the calendar-month table.
 */
import { parseDate, toIsoDate } from "../../lib/format";
import { monthlyReturns } from "../../lib/stats";
import type { FramePayload } from "../../lib/types";

export const RANGES = ["1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"] as const;
export type Range = (typeof RANGES)[number];

/** First excluded date of a range ending at `last` (sessions strictly after it are kept); null = all. */
export function rangeStart(r: Range, last: string): string | null {
  const d = parseDate(last)!;
  const back = (m: number) => toIsoDate(new Date(d.getFullYear(), d.getMonth() - m, d.getDate()));
  switch (r) {
    case "1M":
      return back(1);
    case "3M":
      return back(3);
    case "6M":
      return back(6);
    case "YTD":
      return `${d.getFullYear() - 1}-12-31`;
    case "1Y":
      return back(12);
    case "5Y":
      return back(60);
    default:
      return null;
  }
}

export interface View {
  dates: string[];
  adj: (number | null)[];
  close: (number | null)[];
  volume: (number | null)[];
}

export function sliceWindow(f: FramePayload, range: Range): View | null {
  if (!f.index.length) return null;
  const dates = f.index as string[];
  const start = rangeStart(range, dates[dates.length - 1]);
  const found = start ? dates.findIndex((d) => d > start) : 0;
  const i0 = found < 0 ? dates.length - 1 : found;
  const col = (c: string) => ((f.data[c] ?? []) as (number | null)[]).slice(i0);
  return { dates: dates.slice(i0), adj: col("adj_close"), close: col("close"), volume: col("volume") };
}

/** Fall from the running peak of a level path: 0 at a new high, null where the level is missing. */
export function drawdown(levels: (number | null)[]): (number | null)[] {
  let peak = -Infinity;
  return levels.map((v) => {
    if (v == null || !Number.isFinite(v)) return null;
    peak = Math.max(peak, v);
    return v / peak - 1;
  });
}

export interface MonthRow {
  year: number;
  /** Calendar-month returns Jan..Dec (null where the month has no return). */
  months: (number | null)[];
  /** The available months compounded. */
  year_ret: number | null;
  /** Months with a return (12 = a full year). */
  n: number;
}

/** Calendar-month returns of month-end levels, newest year first, with the months compounded. */
export function monthlyTable(dates: string[], levels: (number | null)[]): MonthRow[] {
  const m = monthlyReturns(dates, levels);
  return m.years.map((year, i) => {
    const months = m.z[i];
    const have = months.filter((x): x is number => x != null && Number.isFinite(x));
    return { year, months, n: have.length, year_ret: have.length ? have.reduce((acc, r) => acc * (1 + r), 1) - 1 : null };
  });
}
