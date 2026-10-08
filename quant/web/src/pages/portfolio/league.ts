/**
 * Reader for the cov.league artifact (compute plan Lane M, M4), table `league`.
 *
 * Expected columns (anything else renders as an unknown shape):
 *   [universe,] estimator, oos_vol | realized_vol [, lw_p]
 *   -- universe: the job writes one block of rows per universe (sectors, site_etfs, ndx100);
 *      each block is its own league, ranked and compared with its own sample estimator.
 *      oos_vol: annualized realized volatility of the estimator's minimum-variance portfolio
 *      over the following month (lower is better); lw_p: Ledoit–Wolf (2008) Sharpe/variance
 *      test p-value against the sample estimator.
 */
export interface LeagueRow {
  /** Unique across universes: `${universe}/${estimator}`. */
  key: string;
  universe: string | null;
  rank: number;
  estimator: string;
  oosVol: number;
  /** oos_vol minus the same universe's sample estimator's (null when it has no sample row). */
  vsSample: number | null;
  lwP: number | null;
}

export interface LeagueGroup {
  universe: string | null;
  rows: LeagueRow[];
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

type Raw = { universe: string | null; estimator: string; oosVol: number; lwP: number | null };

function rank(universe: string | null, rows: Raw[]): LeagueRow[] {
  const sample = rows.find((r) => r.estimator.toLowerCase() === "sample")?.oosVol ?? null;
  return [...rows]
    .sort((a, b) => a.oosVol - b.oosVol)
    .map((r, i) => ({ key: `${universe ?? ""}/${r.estimator}`, rank: i + 1, ...r, universe, vsSample: sample === null ? null : r.oosVol - sample }));
}

/** One league per universe, in the order the artifact lists them. */
export function leagueGroups(recs: Record<string, unknown>[] | null | undefined): LeagueGroup[] | null {
  if (!recs) return null;
  const raw = recs
    .map((r) => ({
      universe: typeof r.universe === "string" ? r.universe : null,
      estimator: r.estimator,
      oosVol: num(r.oos_vol) ?? num(r.realized_vol),
      lwP: num(r.lw_p),
    }))
    .filter((r): r is Raw => typeof r.estimator === "string" && r.oosVol !== null);
  if (recs.length && !raw.length) return null;
  const order: (string | null)[] = [];
  const by = new Map<string | null, Raw[]>();
  for (const r of raw) {
    if (!by.has(r.universe)) {
      by.set(r.universe, []);
      order.push(r.universe);
    }
    by.get(r.universe)!.push(r);
  }
  return order.map((u) => ({ universe: u, rows: rank(u, by.get(u)!) }));
}

/** Flat rows, ranked within each universe (universes in artifact order). */
export function leagueRows(recs: Record<string, unknown>[] | null | undefined): LeagueRow[] | null {
  const g = leagueGroups(recs);
  return g ? g.flatMap((x) => x.rows) : null;
}
