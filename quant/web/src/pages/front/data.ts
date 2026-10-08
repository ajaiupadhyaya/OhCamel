/**
 * Front Page reads and the pure arithmetic behind its figures.
 * Reads: GET /api/market/overview (SPY, QQQ, ^VIX), GET /api/macro/series (DGS2, DGS10; VIXCLS
 * when the quote source lacks ^VIX),
 * GET /api/macro/curve (via the Rates page's useCurve), GET /api/warehouse/freshness.
 */
import { isStale, type LampState } from "../../design";
import { NIGHTLY_MAX_AGE_SEC } from "../../lib/artifacts";
import { useApiQuery } from "../../lib/query";
import type { Envelope, FramePayload } from "../../lib/types";
import type { FredExplorer } from "../macro/types";
import type { OverviewRow } from "../markets/data";

export const FIGURE_TICKERS = ["SPY", "QQQ", "^VIX"] as const;

export interface FrontOverview extends Envelope {
  as_of: string | null;
  rows: OverviewRow[];
}

export function useFrontOverview() {
  return useApiQuery<FrontOverview>("/market/overview", { tickers: FIGURE_TICKERS.join(",") });
}

/** Thirteen months of the 2y and 10y: enough for the 1D change and the 1Y comparison. */
export function useTreasuries() {
  const d = new Date();
  d.setUTCMonth(d.getUTCMonth() - 13, 1);
  return useApiQuery<FredExplorer>("/macro/series", { ids: "DGS2,DGS10", start: d.toISOString().slice(0, 10) });
}

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The latest 2s10s where both legs print on the same date, in basis points. */
export function slope2s10s(f: FramePayload): { bp: number; date: string } | null {
  const a = f.data.DGS2 ?? [];
  const b = f.data.DGS10 ?? [];
  for (let i = f.index.length - 1; i >= 0; i--) {
    const x = a[i];
    const y = b[i];
    if (num(x) && num(y)) return { bp: Math.round((y - x) * 1000) / 10, date: String(f.index[i]) };
  }
  return null;
}

/** The last two printed values of one column (for a 1D change). */
export function lastPair(f: FramePayload, col: string): { last: number; prev: number | null; date: string } | null {
  const v = f.data[col];
  if (!v) return null;
  let last: { v: number; i: number } | null = null;
  for (let i = f.index.length - 1; i >= 0; i--) {
    const x = v[i];
    if (!num(x)) continue;
    if (!last) last = { v: x, i };
    else return { last: last.v, prev: x, date: String(f.index[last.i]) };
  }
  return last ? { last: last.v, prev: null, date: String(f.index[last.i]) } : null;
}

export interface VixFigure {
  value: number | null;
  ret1d: number | null;
  asOf: string | null;
  source: "quote" | "fred" | "none";
}

/**
 * The VIX figure: the ^VIX quote when the overview has it, else FRED VIXCLS (the CBOE close FRED
 * republishes, a day behind) with its own 1D change; neither reads as missing.
 */
export function vixFigure(row: OverviewRow | undefined, fred: FramePayload | null | undefined): VixFigure {
  if (row && !row.error && num(row.last)) return { value: row.last, ret1d: num(row.ret_1d) ? row.ret_1d : null, asOf: row.as_of ?? null, source: "quote" };
  const p = fred ? lastPair(fred, "VIXCLS") : null;
  if (p) return { value: p.last, ret1d: p.prev ? p.last / p.prev - 1 : null, asOf: p.date, source: "fred" };
  return { value: null, ret1d: null, asOf: null, source: "none" };
}

/** FRED VIXCLS, read only when the quote source has no ^VIX (the treasuries' window: a stale store still reads). */
export function useVixFred(enabled: boolean) {
  const d = new Date();
  d.setUTCMonth(d.getUTCMonth() - 13, 1);
  return useApiQuery<FredExplorer>("/macro/series", { ids: "VIXCLS", start: d.toISOString().slice(0, 10) }, { enabled });
}

export interface FreshnessRow {
  dataset: string;
  data_asof: string | null;
  status?: string;
  max_age_s?: number;
  /** The API's own verdict (it knows a session dataset from a calendar one). */
  stale?: boolean;
  keys?: number;
  keys_behind?: number;
  keys_failed?: number;
}

const ENVELOPE_KEYS = new Set(["now", "as_of", "generated_at", "provenance", "notes", "datasets"]);
const str = (v: unknown) => (typeof v === "string" ? v : undefined);
const int = (v: unknown) => (num(v) ? v : undefined);

/**
 * GET /api/warehouse/freshness as rows. The served shape (warehouse/freshness.py) is
 * `{now, datasets: {name: {data_asof, last_status, keys, keys_behind, keys_failed, stale, rule}}}`;
 * a list of `{dataset, ...}` and a bare name → data_asof record are read too.
 */
export function freshnessRows(body: unknown): FreshnessRow[] {
  if (!body || typeof body !== "object") return [];
  const b = body as Record<string, unknown>;
  if (Array.isArray(b.datasets)) {
    return b.datasets
      .filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && typeof (r as { dataset?: unknown }).dataset === "string")
      .map((r) => ({
        dataset: r.dataset as string,
        data_asof: typeof r.data_asof === "string" ? r.data_asof : null,
        status: typeof r.status === "string" ? r.status : undefined,
        max_age_s: num(r.max_age_s) ? r.max_age_s : undefined,
      }));
  }
  if (b.datasets && typeof b.datasets === "object") {
    return Object.entries(b.datasets as Record<string, unknown>)
      .filter((e): e is [string, Record<string, unknown>] => !!e[1] && typeof e[1] === "object")
      .map(([dataset, r]) => {
        const row: FreshnessRow = { dataset, data_asof: str(r.data_asof) ?? null, status: str(r.last_status) };
        if (typeof r.stale === "boolean") row.stale = r.stale;
        if (int(r.keys) !== undefined) row.keys = r.keys as number;
        if (int(r.keys_behind) !== undefined) row.keys_behind = r.keys_behind as number;
        if (int(r.keys_failed) !== undefined) row.keys_failed = r.keys_failed as number;
        return row;
      })
      .sort((x, y) => x.dataset.localeCompare(y.dataset));
  }
  return Object.entries(b)
    .filter(([k, v]) => !ENVELOPE_KEYS.has(k) && (typeof v === "string" || v === null))
    .map(([k, v]) => ({ dataset: k, data_asof: v as string | null }))
    .sort((x, y) => x.dataset.localeCompare(y.dataset));
}

/**
 * One lamp per dataset: a failed run or failed keys is a fault; a dataset never ingested (no
 * keys, or no data date and no run) is idle (NOT YET RUN); then the API's stale flag; then age.
 */
export function lampOf(r: FreshnessRow, now: Date): LampState {
  if ((r.status && /fail|error/i.test(r.status)) || (r.keys_failed ?? 0) > 0) return "fault";
  if (!r.data_asof && (r.keys === 0 || !r.status)) return "idle";
  if (r.stale !== undefined) return r.stale ? "stale" : "ok";
  return isStale(r.data_asof, r.max_age_s ?? NIGHTLY_MAX_AGE_SEC, now) ? "stale" : "ok";
}

/** The STATE column's word for a lamp. */
export function stateLabel(l: LampState): string {
  return l === "idle" ? "NOT YET RUN" : l.toUpperCase();
}
