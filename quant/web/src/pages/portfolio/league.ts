/**
 * Reader for the cov.league artifact (compute plan Lane M, M4), table `league`.
 *
 * Expected columns (anything else renders as an unknown shape):
 *   estimator, oos_vol | realized_vol [, lw_p]
 *   -- oos_vol: annualized realized volatility of the estimator's minimum-variance portfolio
 *      over the following month (lower is better); lw_p: Ledoit–Wolf (2008) Sharpe/variance
 *      test p-value against the sample estimator.
 */
export interface LeagueRow {
  rank: number;
  estimator: string;
  oosVol: number;
  /** oos_vol minus the sample estimator's (null when no sample row). */
  vsSample: number | null;
  lwP: number | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function leagueRows(recs: Record<string, unknown>[] | null | undefined): LeagueRow[] | null {
  if (!recs) return null;
  const rows = recs
    .map((r) => ({ estimator: r.estimator, oosVol: num(r.oos_vol) ?? num(r.realized_vol), lwP: num(r.lw_p) }))
    .filter((r): r is { estimator: string; oosVol: number; lwP: number | null } => typeof r.estimator === "string" && r.oosVol !== null);
  if (recs.length && !rows.length) return null;
  const sample = rows.find((r) => r.estimator.toLowerCase() === "sample")?.oosVol ?? null;
  return [...rows].sort((a, b) => a.oosVol - b.oosVol).map((r, i) => ({ rank: i + 1, ...r, vsSample: sample === null ? null : r.oosVol - sample }));
}
