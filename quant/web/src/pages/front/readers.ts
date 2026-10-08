/**
 * The Front Page's reads of Lane M's artifact tables, as pure functions (the cells in
 * ./Products.tsx render them):
 *   risk.mc_atlas · summary    → the core book at 99% · 1D, FHS beside the Student-t copula
 *   farm.sweep    · leaderboard → the top rows by farm-wide DSR (verdict first in the cell)
 *   regime.hmm    · probs       → the latest filtered P(high vol) and the last two years
 * Anything that does not match returns null and the cell says so; nothing is guessed.
 */
import { atlasRows, atlasBooks } from "../risk/atlas";
import { latestProb, probRows, type ProbRow } from "../macro/derive";
import type { FarmBoardRow } from "../research/types";
import { last24h, type JobRow } from "../compute/model";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export interface AtlasLeg {
  var: number;
  es: number;
  varUsd: number | null;
  esUsd: number | null;
}

export interface AtlasFront {
  book: string;
  fhs: AtlasLeg | null;
  tcop: AtlasLeg | null;
  notional: number | null;
  /** Books in the artifact (the cell links to the rest on /risk). */
  books: number;
}

const ALPHA = 0.99;
const HORIZON = 1;
const same = (a: number, b: number) => Math.abs(a - b) < 1e-9;

export function atlasFront(recs: Record<string, unknown>[] | null | undefined): AtlasFront | null {
  const rows = atlasRows(recs);
  if (!rows) return null;
  const books = atlasBooks(rows, ALPHA, HORIZON);
  if (!books.length) return null;
  const pick = books.find((b) => b.book === "core") ?? books[0];
  const raw = (method: (m: string) => boolean) =>
    (recs ?? []).find((r) => r.book === pick.book && typeof r.method === "string" && method(r.method.toLowerCase()) && num(r.alpha) !== null && same(num(r.alpha)! > 1 ? num(r.alpha)! / 100 : num(r.alpha)!, ALPHA) && same(num(r.horizon) ?? num(r.horizon_days) ?? NaN, HORIZON));
  const leg = (row: { var: number; es: number } | null, r: Record<string, unknown> | undefined): AtlasLeg | null => (row ? { var: row.var, es: row.es, varUsd: num(r?.var_usd), esUsd: num(r?.es_usd) } : null);
  const fhsRaw = raw((m) => m === "fhs");
  const tRaw = raw((m) => m === "t_copula" || m === "tcopula" || m === "copula_t");
  return {
    book: pick.book,
    fhs: leg(pick.fhs, fhsRaw),
    tcop: leg(pick.tcop, tRaw),
    notional: num(fhsRaw?.notional) ?? num(tRaw?.notional),
    books: new Set(rows.map((r) => r.book)).size,
  };
}

/** The farm's best rows by farm-wide DSR (most fail: the cell shows their verdicts first). */
export function farmTop(board: FarmBoardRow[], n = 3): FarmBoardRow[] {
  return [...board].sort((a, b) => (b.dsr_farm ?? -Infinity) - (a.dsr_farm ?? -Infinity)).slice(0, n);
}

/** Two years of weeks on the Front Page chart; the full history is on Rates · Regimes. */
export const REGIME_WEEKS = 104;

export interface RegimeFront {
  last: ProbRow & { p_high: number };
  state: "HIGH VOL" | "NOT HIGH VOL";
  window: ProbRow[];
}

export function regimeFront(recs: Record<string, unknown>[]): RegimeFront | null {
  const rows = probRows(recs);
  const last = latestProb(rows);
  if (!last || last.p_high === null) return null;
  return { last: last as RegimeFront["last"], state: last.p_high >= 0.5 ? "HIGH VOL" : "NOT HIGH VOL", window: rows.slice(-REGIME_WEEKS) };
}

/** Artifacts published in the last 24h: done jobs with an artifact id (GET /api/jobs). */
export function artifacts24h(rows: JobRow[] | undefined, nowMs: number): number | null {
  if (!rows) return null;
  return last24h(rows, nowMs).filter((r) => r.state === "done" && !!r.artifact_id).length;
}
