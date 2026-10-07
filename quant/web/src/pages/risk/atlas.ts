/**
 * Readers for the risk.mc_atlas artifact (compute plan Lane M, M2): tables `summary` and `euler`.
 *
 * Expected columns (Lane M produces them; anything else renders as an unknown shape, never a guess):
 *   summary: book, method ("fhs" | "t_copula"), alpha (0.99 or 99), horizon | horizon_days, var, es
 *            [, n_paths] -- var and es are positive loss fractions of book value.
 *   euler:   book, ticker, weight, pct_es | es_share | share [, method, alpha, horizon]
 *            -- each position's share of the book's ES (shares sum to 1).
 */
export interface AtlasRow {
  book: string;
  method: string;
  alpha: number;
  horizon: number;
  var: number;
  es: number;
  nPaths: number | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function atlasRows(recs: Record<string, unknown>[] | null | undefined): AtlasRow[] | null {
  if (!recs) return null;
  const out: AtlasRow[] = [];
  for (const r of recs) {
    const a = num(r.alpha);
    const h = num(r.horizon) ?? num(r.horizon_days);
    const v = num(r.var);
    const es = num(r.es);
    if (typeof r.book !== "string" || typeof r.method !== "string" || a === null || h === null || v === null || es === null) continue;
    out.push({ book: r.book, method: r.method.toLowerCase(), alpha: a > 1 ? a / 100 : a, horizon: h, var: v, es, nPaths: num(r.n_paths) });
  }
  return recs.length && !out.length ? null : out;
}

export interface AtlasBook {
  book: string;
  fhs: AtlasRow | null;
  tcop: AtlasRow | null;
  /** t-copula VaR minus FHS VaR (null unless both ran). */
  gap: number | null;
}

const same = (a: number, b: number) => Math.abs(a - b) < 1e-9;

export function atlasBooks(rows: AtlasRow[], alpha: number, horizon: number): AtlasBook[] {
  const at = rows.filter((r) => same(r.alpha, alpha) && same(r.horizon, horizon));
  const books = [...new Set(at.map((r) => r.book))];
  return books.map((book) => {
    const fhs = at.find((r) => r.book === book && r.method === "fhs") ?? null;
    const tcop = at.find((r) => r.book === book && (r.method === "t_copula" || r.method === "tcopula" || r.method === "copula_t")) ?? null;
    return { book, fhs, tcop, gap: fhs && tcop ? tcop.var - fhs.var : null };
  });
}

export interface EulerRow {
  ticker: string;
  weight: number;
  share: number;
}

export function eulerRows(recs: Record<string, unknown>[] | null | undefined, book: string, filter?: { method?: string; alpha?: number; horizon?: number }): EulerRow[] | null {
  if (!recs) return null;
  const out: EulerRow[] = [];
  let shaped = 0;
  for (const r of recs) {
    const w = num(r.weight);
    const s = num(r.pct_es) ?? num(r.es_share) ?? num(r.share);
    if (typeof r.book !== "string" || typeof r.ticker !== "string" || w === null || s === null) continue;
    shaped += 1;
    if (r.book !== book) continue;
    if (filter?.method && typeof r.method === "string" && r.method.toLowerCase() !== filter.method) continue;
    const a = num(r.alpha);
    if (filter?.alpha !== undefined && a !== null && !same(a > 1 ? a / 100 : a, filter.alpha)) continue;
    const h = num(r.horizon) ?? num(r.horizon_days);
    if (filter?.horizon !== undefined && h !== null && !same(h, filter.horizon)) continue;
    out.push({ ticker: r.ticker, weight: w, share: s });
  }
  return recs.length && !shaped ? null : out;
}
