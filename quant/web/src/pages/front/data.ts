/**
 * Front Page reads and the pure arithmetic behind its figures.
 * Reads: GET /api/market/overview (SPY, QQQ, ^VIX), GET /api/macro/series (DGS2, DGS10),
 * GET /api/macro/curve (via the Rates page's useCurve), GET /api/warehouse/freshness.
 */
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

export interface FreshnessRow {
  dataset: string;
  data_asof: string | null;
  status?: string;
  max_age_s?: number;
}

const ENVELOPE_KEYS = new Set(["as_of", "generated_at", "provenance", "notes", "datasets"]);

/** GET /api/warehouse/freshness as rows: `{datasets: [...]}` or a dataset → data_asof record. */
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
  return Object.entries(b)
    .filter(([k, v]) => !ENVELOPE_KEYS.has(k) && (typeof v === "string" || v === null))
    .map(([k, v]) => ({ dataset: k, data_asof: v as string | null }))
    .sort((x, y) => x.dataset.localeCompare(y.dataset));
}
