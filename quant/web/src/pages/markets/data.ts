/**
 * Markets page — wire types and query hooks for GET /api/market/overview.
 * (Page-local: other pages should not import from here.)
 */
import { DataUnavailableError } from "../../lib/api";
import { useApiQuery } from "../../lib/query";
import type { Envelope, SeriesPayload } from "../../lib/types";

/** One instrument row (routers/market.py ticker_metrics). Returns are DECIMALS. */
export interface OverviewRow {
  ticker: string;
  name: string;
  error: string | null;
  last?: number | null;
  as_of?: string | null;
  ret_1d?: number | null;
  ret_1w?: number | null;
  ret_1m?: number | null;
  ret_3m?: number | null;
  ret_ytd?: number | null;
  ret_1y?: number | null;
  ret_3y_ann?: number | null;
  vol_1m_ann?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  dist_52w_high?: number | null;
  dist_52w_low?: number | null;
  sma20_pos?: number | null;
  sma50_pos?: number | null;
  sma200_pos?: number | null;
  sparkline?: SeriesPayload | null;
}

export interface Overview extends Envelope {
  universe: string;
  label: string;
  as_of: string | null;
  rows: OverviewRow[];
  correlation_1y?: { tickers: string[]; matrix: (number | null)[][]; n_obs: number };
  method?: Record<string, string>;
}

export const PERIODS = [
  { key: "ret_1d", label: "1D", scale: 0.02, long: "1-day" },
  { key: "ret_1w", label: "1W", scale: 0.04, long: "1-week" },
  { key: "ret_1m", label: "1M", scale: 0.08, long: "1-month" },
  { key: "ret_3m", label: "3M", scale: 0.12, long: "3-month" },
  { key: "ret_ytd", label: "YTD", scale: 0.2, long: "year-to-date" },
  { key: "ret_1y", label: "1Y", scale: 0.3, long: "1-year" },
] as const;
export type PeriodLabel = (typeof PERIODS)[number]["label"];
export type PeriodKey = (typeof PERIODS)[number]["key"];
export const periodOf = (label: string) => PERIODS.find((p) => p.label === label) ?? PERIODS[0];

export function useOverview(universe: string | null, tickers?: string[]) {
  return useApiQuery<Overview>("/market/overview", tickers ? { tickers: tickers.join(",") } : { universe: universe ?? undefined }, {
    enabled: !!universe || !!tickers?.length,
  });
}

/**
 * When every instrument in a universe failed, surface it as one data-unavailable error
 * (so the panel shows the reason once instead of a table of dashes).
 */
export function allFailed(o: Overview | undefined): DataUnavailableError | null {
  if (!o || !o.rows.length || o.rows.some((r) => !r.error)) return null;
  const reasons = [...new Set(o.rows.map((r) => r.error!.replace(/^[A-Z0-9^.=-]+:\s*/, "")))];
  return new DataUnavailableError(`${o.rows.map((r) => r.ticker).join(", ")} — ${reasons.join("; ")}`, "/api/market/overview");
}
