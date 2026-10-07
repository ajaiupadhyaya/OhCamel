/**
 * Pure helpers for the Volatility page: the P2 forecast league and P7 surface history read
 * from their artifacts (Lane M's table shapes, plan M3 / M8), the cone as quiet lines, day
 * ticks for a log expiry axis, the strategy chart's focus window and signed series parts.
 */
import type { XYSeries } from "../../charts/XYChart";
import type { ConeRow } from "./types";

const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** A daily variance (decimal²) as an annualized vol: sqrt(252 v). */
export function annualVol(v: number | null | undefined): number | null {
  return fin(v) && v >= 0 ? Math.sqrt(252 * v) : null;
}

// ------------------------------------------------------------------ P2 vol.forecast_league

/** `league`: one row per name × model, scored out of sample on QLIKE (plan M3). */
export interface LeagueRow {
  ticker: string;
  model: string;
  qlike: number | null;
  mse: number | null;
  n: number | null;
  /** In the 10% Model Confidence Set; null when the MCS could not be computed. */
  in_mcs: boolean | null;
  rank_qlike: number | null;
  target: string | null;
}

/** `summary`: one row per model across names. */
export interface LeagueSummaryRow {
  model: string;
  names: number | null;
  mean_rank: number | null;
  mcs_rate: number | null;
  median_qlike: number | null;
}

/** `dm`: Diebold–Mariano per pair; negative stat: a has the smaller loss. */
export interface DmRow {
  ticker: string;
  a: string;
  b: string;
  stat: number | null;
  pvalue: number | null;
}

/** `forecasts`: per name, var_1d_<model> (daily variance) and har_22d (daily units). */
export type ForecastRow = { ticker: string; asof?: string | null; har_22d?: number | null } & Record<string, unknown>;

export interface SkipRow {
  ticker: string;
  model?: string | null;
  reason: string | null;
}

export const MODEL_LABEL: Record<string, string> = { garch: "GARCH", gjr: "GJR", egarch: "EGARCH", har: "HAR-RV", ewma: "EWMA" };
export const modelLabel = (m: string) => MODEL_LABEL[m] ?? m.toUpperCase();

/** Models by how often they sit in the MCS, then by mean QLIKE rank; an unknown rate last. */
export function leagueSummary(rows: LeagueSummaryRow[]): LeagueSummaryRow[] {
  return [...rows].sort((a, b) => {
    const ra = fin(a.mcs_rate) ? a.mcs_rate : -1;
    const rb = fin(b.mcs_rate) ? b.mcs_rate : -1;
    if (ra !== rb) return rb - ra;
    return (fin(a.mean_rank) ? a.mean_rank : Infinity) - (fin(b.mean_rank) ? b.mean_rank : Infinity);
  });
}

export function leagueFor(rows: LeagueRow[], ticker: string): LeagueRow[] {
  const t = ticker.toUpperCase();
  return rows.filter((r) => r.ticker?.toUpperCase() === t).sort((a, b) => (a.rank_qlike ?? Infinity) - (b.rank_qlike ?? Infinity));
}

export function dmFor(rows: DmRow[], ticker: string): DmRow[] {
  const t = ticker.toUpperCase();
  return rows.filter((r) => r.ticker?.toUpperCase() === t);
}

export interface NameForecast {
  asof: string | null;
  models: { model: string; vol: number }[];
  har22: number | null;
}

export function nameForecasts(rows: ForecastRow[], ticker: string): NameForecast | null {
  const t = ticker.toUpperCase();
  const r = rows.find((x) => typeof x.ticker === "string" && x.ticker.toUpperCase() === t);
  if (!r) return null;
  const models = Object.entries(r)
    .filter(([k]) => k.startsWith("var_1d_"))
    .map(([k, v]) => ({ model: k.slice("var_1d_".length), vol: annualVol(v as number) }))
    .filter((m): m is { model: string; vol: number } => m.vol !== null);
  return { asof: typeof r.asof === "string" ? r.asof : null, models, har22: annualVol(r.har_22d) };
}

// ------------------------------------------------------------------ P7 vol.surface_history

/** `history`: one row per underlying per snapshot day (plan M8). */
export interface HistoryRow {
  underlying: string;
  asof: string;
  atm_iv_30d: number | null;
  atm_iv_90d: number | null;
  term_slope: number | null;
  rr25_30d: number | null;
  bf25_30d: number | null;
  mf_var_30d: number | null;
  /** mf_var_30d − 252 × HAR 22-day daily variance (variance units). */
  vrp: number | null;
  vrp_note: string | null;
  n_slices: number | null;
  spot: number | null;
}

export interface HistorySummaryRow {
  underlying: string;
  days: number | null;
  first: string | null;
  last: string | null;
}

export interface HistoryErrorRow {
  underlying: string;
  asof: string;
  error: string | null;
}

export interface HistoryPoint extends HistoryRow {
  mf_vol_30d: number | null;
  /** The HAR 22-day vol the VRP was taken against: sqrt(mf_var − vrp). */
  har_vol_22d: number | null;
  /** The premium in vol points: model-free vol − HAR vol. */
  vrp_pts: number | null;
}

export function historyFor(rows: HistoryRow[], underlying: string): HistoryPoint[] {
  const u = underlying.toUpperCase();
  return rows
    .filter((r) => r.underlying?.toUpperCase() === u)
    .sort((a, b) => (a.asof < b.asof ? -1 : a.asof > b.asof ? 1 : 0))
    .map((r) => {
      const mf = fin(r.mf_var_30d) && r.mf_var_30d >= 0 ? Math.sqrt(r.mf_var_30d) : null;
      const rv = fin(r.mf_var_30d) && fin(r.vrp) && r.mf_var_30d - r.vrp >= 0 ? Math.sqrt(r.mf_var_30d - r.vrp) : null;
      return { ...r, mf_vol_30d: mf, har_vol_22d: rv, vrp_pts: mf !== null && rv !== null ? mf - rv : null };
    });
}

// ------------------------------------------------------------------ charts

/** The cone as lines: the percentile bands quiet (ink-3), the median ink-2, today ink with points. */
export function coneLines(cone: ConeRow[]): XYSeries[] {
  const col = (k: keyof ConeRow) => cone.map((c) => (fin(c[k]) ? (c[k] as number) : null));
  return [
    { name: "P90", y: col("p90"), tone: "ink3", dash: "dot" },
    { name: "P75", y: col("p75"), tone: "ink3" },
    { name: "MED", y: col("p50"), tone: "ink2", dash: "dash" },
    { name: "P25", y: col("p25"), tone: "ink3" },
    { name: "P10", y: col("p10"), tone: "ink3", dash: "dot" },
    { name: "TODAY", y: col("current"), tone: "ink", width: 2 },
  ];
}

const DAY_TICKS = [3, 7, 14, 30, 60, 90, 180, 365, 730];

/** Calendar-day ticks for a log expiry axis, within the span of `days`. */
export function dayTicks(days: number[]): { at: number; label: string }[] {
  const ds = days.filter(fin);
  if (!ds.length) return [];
  const lo = Math.min(...ds);
  const hi = Math.max(...ds);
  return DAY_TICKS.filter((d) => d >= lo * 0.8 && d <= hi * 1.25).map((d) => ({ at: d, label: d >= 365 ? `${d / 365}Y` : `${d}D` }));
}

/** The spot range a payoff chart opens on: spot, strikes and breakevens, padded, inside the grid. */
export function focusWindow({ spot, breakevens, strikes, grid }: { spot: number; breakevens: number[]; strikes: number[]; grid: [number, number] }): [number, number] {
  const pts = [spot, ...breakevens, ...strikes].filter(fin);
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const pad = Math.max((hi - lo) * 0.6, spot * 0.05);
  return [Math.max(grid[0], lo - pad), Math.min(grid[1], hi + pad)];
}

/** A series split into its non-negative part and its negative part (null elsewhere). */
export function signedParts(y: (number | null)[]): { pos: (number | null)[]; neg: (number | null)[] } {
  return { pos: y.map((v) => (fin(v) && v >= 0 ? v : null)), neg: y.map((v) => (fin(v) && v < 0 ? v : null)) };
}

/** Round to `digits` decimals for an input value (not for display; display goes through lib/format). */
export function roundTo(x: number, digits: number): number {
  const f = 10 ** digits;
  return Math.round(x * f) / f;
}
