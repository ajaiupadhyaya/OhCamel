/**
 * Strategy Lab state: the editable draft, the request bodies built from it, and the
 * default parameter grids for the sweep / walk-forward tabs.
 */
import type { ParamSpec } from "../../lib/types";
import type { StrategySpec } from "./types";

export interface LabConfig {
  strategy: string;
  params: Record<string, unknown>;
  tickers: string[];
  start: string | null;
  end: string | null;
  cost_bps: number;
  borrow_bps: number;
  execution_lag: number;
  /** null = the strategy's default rebalance */
  rebalance: string | null;
  /** null = off */
  vol_target: number | null;
  benchmark: string;
}

export const DEFAULT_STRATEGY = "tsmom";

export function paramDefaultsFor(spec: StrategySpec): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of spec.params) out[p.name] = p.type === "weights" ? {} : p.default ?? null;
  return out;
}

export function initialConfig(spec: StrategySpec, prev?: Partial<LabConfig>): LabConfig {
  return {
    strategy: spec.key,
    params: paramDefaultsFor(spec),
    tickers: [...spec.default_tickers],
    start: prev?.start ?? null,
    end: prev?.end ?? null,
    cost_bps: prev?.cost_bps ?? 5,
    borrow_bps: prev?.borrow_bps ?? 25,
    execution_lag: prev?.execution_lag ?? 1,
    rebalance: null,
    vol_target: prev?.vol_target ?? null,
    benchmark: prev?.benchmark ?? "SPY",
  };
}

/** Params the server accepts: drops ticker params that are not in the universe and weights for dropped tickers. */
export function cleanParams(spec: StrategySpec, params: Record<string, unknown>, tickers: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of spec.params) {
    const v = params[p.name];
    if (v === undefined) continue;
    if (p.type === "ticker") out[p.name] = typeof v === "string" && tickers.includes(v) ? v : "";
    else if (p.type === "weights") {
      const w: Record<string, number> = {};
      for (const [k, x] of Object.entries((v ?? {}) as Record<string, number>)) if (tickers.includes(k) && Number.isFinite(x) && x !== 0) w[k] = x;
      out[p.name] = w;
    } else out[p.name] = v;
  }
  return out;
}

/** The body shared by run / sweep / walkforward / costs. */
export function baseBody(cfg: LabConfig, spec: StrategySpec, tickers: string[]) {
  return {
    strategy: cfg.strategy,
    params: cleanParams(spec, cfg.params, tickers),
    tickers,
    start: cfg.start,
    end: cfg.end,
    cost_bps: cfg.cost_bps,
    borrow_bps: cfg.borrow_bps,
    execution_lag: cfg.execution_lag,
    rebalance: cfg.rebalance,
    vol_target: cfg.vol_target,
    benchmark: cfg.benchmark,
  };
}
export type BaseBody = ReturnType<typeof baseBody>;

// ------------------------------------------------------------------ grids

export const isNumericParam = (p: ParamSpec) => p.type === "int" || p.type === "float";

/** Numeric params ranked for sweeping: sizing/leverage knobs last (they mostly rescale risk). */
export function sweepableParams(spec: StrategySpec): ParamSpec[] {
  const nums = spec.params.filter(isNumericParam);
  const scaling = (p: ParamSpec) => /leverage|vol_target|target_vol|min_history/.test(p.name);
  return [...nums.filter((p) => !scaling(p)), ...nums.filter(scaling)];
}

function roundNice(v: number, isInt: boolean): number {
  if (isInt) return Math.round(v);
  const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(v) || 1)) - 1);
  return Math.round(v / mag) * mag;
}

/** Five values around the default (×0.5 … ×2), clamped to [min, max], unique, sorted. */
export function defaultValues(p: ParamSpec, n = 5): number[] {
  const isInt = p.type === "int";
  const d = typeof p.default === "number" ? p.default : Number(p.default ?? p.min ?? 1);
  const lo = p.min ?? -Infinity;
  const hi = p.max ?? Infinity;
  const mults = n >= 5 ? [0.5, 0.75, 1, 1.5, 2] : [0.5, 1, 2];
  let vals = mults.map((m) => roundNice(Math.min(hi, Math.max(lo, d * m)), isInt));
  if (d === 0) {
    const span = (Number.isFinite(hi) ? hi : 1) - (Number.isFinite(lo) ? lo : 0);
    vals = Array.from({ length: n }, (_, i) => roundNice((Number.isFinite(lo) ? lo : 0) + (span * i) / (n - 1), isInt));
  }
  vals = [...new Set(vals.map((v) => +v.toFixed(6)))].sort((a, b) => a - b);
  if (vals.length < 3 && Number.isFinite(lo) && Number.isFinite(hi)) {
    vals = [...new Set(Array.from({ length: n }, (_, i) => roundNice(lo + ((hi - lo) * i) / (n - 1), isInt)))];
  }
  return vals;
}

export function parseValues(text: string, p: ParamSpec | undefined): { values: number[]; error: string | null } {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  const values: number[] = [];
  for (const s of parts) {
    const v = Number(s);
    if (!Number.isFinite(v)) return { values: [], error: `“${s}” is not a number` };
    if (p?.type === "int" && !Number.isInteger(v)) return { values: [], error: `${p.name} takes whole numbers` };
    if (p?.min != null && v < p.min) return { values: [], error: `${v} is below the minimum ${p.min}` };
    if (p?.max != null && v > p.max) return { values: [], error: `${v} is above the maximum ${p.max}` };
    values.push(v);
  }
  const uniq = [...new Set(values)].sort((a, b) => a - b);
  if (uniq.length < 2) return { values: uniq, error: "give at least two values" };
  return { values: uniq, error: null };
}

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function fmtParams(params: Record<string, unknown> | null | undefined): string {
  if (!params) return "";
  return Object.entries(params)
    .map(([k, v]) => {
      if (typeof v === "number") return `${k} ${+v.toFixed(4)}`;
      if (v && typeof v === "object") {
        const e = Object.entries(v as Record<string, number>);
        return e.length ? e.map(([t, w]) => `${t} ${+(w * 100).toFixed(1)}%`).join(" ") : `${k} equal`;
      }
      if (v === "") return `${k} cash`;
      return `${k} ${String(v)}`;
    })
    .join(" · ");
}
