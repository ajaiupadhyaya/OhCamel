/**
 * Optimizer inputs: one config object edited by the setup rail, and the request bodies
 * derived from it for /portfolio/optimize, /frontier, /covariance and /compare.
 */
import { cleanTicker } from "../../lib/portfolio";
import type { CovName, LinkageName, MethodName, ReturnsModel } from "./types";

export interface ViewDraft {
  id: string;
  kind: "absolute" | "relative";
  long: string;
  short: string;
  /** annual decimal: absolute = expected return, relative = outperformance */
  value: number;
  /** 0..1 */
  confidence: number;
}

export type MvTarget = "risk_aversion" | "target_return" | "target_vol";

export interface OptConfig {
  tickers: string[];
  start: string | null;
  end: string | null;
  benchmark: string;
  cov: CovName;
  ewmaLambda: number;
  returns: ReturnsModel;
  rfMode: "market" | "manual";
  rf: number;
  method: MethodName;
  // constraints
  longOnly: boolean;
  minWeight: number;
  maxWeight: number;
  bounds: Record<string, [number, number]>;
  maxGross: number | null;
  turnoverOn: boolean;
  maxTurnover: number;
  /** current holdings (decimal weights) — from the active portfolio or a deep-link */
  current: Record<string, number>;
  currentSource: string;
  // method parameters
  mvTarget: MvTarget;
  mvValue: Record<MvTarget, number>;
  /** null = the method's own default (single for HRP, ward for HERC) */
  linkage: LinkageName | null;
  nClusters: number | null;
  cvarAlpha: number;
  // Black–Litterman
  views: ViewDraft[];
  tau: number;
}

export const DEFAULTS: Omit<
  OptConfig,
  "tickers" | "current" | "currentSource" | "start" | "end" | "benchmark"
> = {
  cov: "lw_constant_corr",
  ewmaLambda: 0.94,
  returns: "historical",
  rfMode: "market",
  rf: 0.02,
  method: "hrp",
  longOnly: true,
  minWeight: 0,
  maxWeight: 1,
  bounds: {},
  maxGross: null,
  turnoverOn: false,
  maxTurnover: 0.5,
  mvTarget: "target_vol",
  mvValue: { risk_aversion: 4, target_return: 0.08, target_vol: 0.1 },
  linkage: null,
  nClusters: null,
  cvarAlpha: 0.95,
  views: [],
  tau: 0.05,
};

let viewSeq = 0;
export const newViewId = () =>
  `v${Date.now().toString(36)}${(viewSeq++).toString(36)}`;

/** Current weights restricted to the universe and rescaled to sum to 1 (null when nothing overlaps). */
export function currentInUniverse(
  cfg: Pick<OptConfig, "current" | "tickers">,
): Record<string, number> | null {
  const inU = cfg.tickers.filter((t) => Math.abs(cfg.current[t] ?? 0) > 1e-9);
  const s = inU.reduce((a, t) => a + cfg.current[t], 0);
  if (!inU.length || Math.abs(s) < 1e-9) return null;
  const out: Record<string, number> = {};
  for (const t of cfg.tickers) out[t] = (cfg.current[t] ?? 0) / s;
  return out;
}

export function constraintsBody(cfg: OptConfig) {
  const bounds: Record<string, [number, number]> = {};
  for (const t of cfg.tickers) if (cfg.bounds[t]) bounds[t] = cfg.bounds[t];
  const cur = cfg.turnoverOn ? currentInUniverse(cfg) : null;
  return {
    min_weight: cfg.longOnly ? Math.max(0, cfg.minWeight) : cfg.minWeight,
    max_weight: cfg.maxWeight,
    bounds: Object.keys(bounds).length ? bounds : undefined,
    max_gross: !cfg.longOnly && cfg.maxGross ? cfg.maxGross : undefined,
    max_turnover: cur ? cfg.maxTurnover : undefined,
    current_weights: cur ?? undefined,
  };
}

export function estimationBody(cfg: OptConfig) {
  const bl = cfg.returns === "black_litterman";
  return {
    tickers: cfg.tickers,
    start: cfg.start ?? undefined,
    end: cfg.end ?? undefined,
    cov_method: cfg.cov,
    ewma_lambda: cfg.cov === "ewma" ? cfg.ewmaLambda : undefined,
    returns_model: cfg.returns,
    benchmark: cfg.benchmark,
    risk_free_rate: cfg.rfMode === "manual" ? cfg.rf : undefined,
    views: bl
      ? validViews(cfg).map((v) => ({
          long: v.long,
          short: v.kind === "relative" ? v.short : undefined,
          value: v.value,
          confidence: v.confidence,
        }))
      : undefined,
    tau: bl ? cfg.tau : undefined,
    constraints: constraintsBody(cfg),
  };
}

export function validViews(
  cfg: Pick<OptConfig, "views" | "tickers">,
): ViewDraft[] {
  return cfg.views.filter(
    (v) =>
      cfg.tickers.includes(v.long) &&
      (v.kind === "absolute" ||
        (cfg.tickers.includes(v.short) && v.short !== v.long)),
  );
}

export function methodParams(cfg: OptConfig, method: MethodName = cfg.method) {
  const p: Record<string, unknown> = {};
  if (method === "mean_variance") p[cfg.mvTarget] = cfg.mvValue[cfg.mvTarget];
  if ((method === "hrp" || method === "herc") && cfg.linkage)
    p.linkage = cfg.linkage;
  if (method === "herc" && cfg.nClusters) p.n_clusters = cfg.nClusters;
  if (method === "min_cvar") p.cvar_alpha = cfg.cvarAlpha;
  return p;
}

export function optimizeBody(cfg: OptConfig) {
  return { ...estimationBody(cfg), method: cfg.method, ...methodParams(cfg) };
}

export function frontierBody(cfg: OptConfig) {
  return { ...estimationBody(cfg), points: 40 };
}

/** Evaluate the CURRENT weights under the same estimates: every weight pinned by its bounds. */
export function currentEvalBody(cfg: OptConfig) {
  const cur = currentInUniverse(cfg);
  if (!cur) return null;
  const bounds: Record<string, [number, number]> = {};
  for (const t of cfg.tickers) bounds[t] = [cur[t], cur[t]];
  const est = estimationBody(cfg);
  return {
    ...est,
    method: "min_variance" as const,
    constraints: { min_weight: -2, max_weight: 3, bounds },
  };
}

export function covarianceBody(cfg: OptConfig, linkage: LinkageName) {
  return {
    tickers: cfg.tickers,
    start: cfg.start ?? undefined,
    end: cfg.end ?? undefined,
    estimator: cfg.cov,
    ewma_lambda: cfg.cov === "ewma" ? cfg.ewmaLambda : undefined,
    linkage,
  };
}

export interface CompareDraft {
  methods: MethodName[];
  window: number;
  rebalance: "W" | "M" | "Q";
  costBps: number;
  muMethod: "historical" | "james_stein";
}

export const COMPARE_DEFAULT: CompareDraft = {
  methods: [
    "equal_weight",
    "inverse_volatility",
    "min_variance",
    "max_sharpe",
    "risk_parity",
    "hrp",
    "max_diversification",
    "min_cvar",
  ],
  window: 504,
  rebalance: "M",
  costBps: 10,
  muMethod: "historical",
};

export function compareBody(cfg: OptConfig, d: CompareDraft) {
  const c = constraintsBody(cfg);
  const methods = d.methods.includes("equal_weight")
    ? d.methods
    : ["equal_weight" as MethodName, ...d.methods];
  const mp = methods.includes("mean_variance")
    ? { [cfg.mvTarget]: cfg.mvValue[cfg.mvTarget] }
    : {};
  return {
    tickers: cfg.tickers,
    start: cfg.start ?? undefined,
    end: cfg.end ?? undefined,
    methods,
    benchmark_method: "equal_weight",
    window: d.window,
    rebalance: d.rebalance,
    cost_bps: d.costBps,
    cov_method: cfg.cov,
    ewma_lambda: cfg.cov === "ewma" ? cfg.ewmaLambda : undefined,
    mu_method: d.muMethod,
    risk_free_rate: cfg.rfMode === "manual" ? cfg.rf : undefined,
    min_weight: c.min_weight,
    max_weight: c.max_weight,
    max_gross: c.max_gross,
    linkage: cfg.linkage ?? undefined,
    cvar_alpha: methods.includes("min_cvar") ? cfg.cvarAlpha : undefined,
    ...mp,
  };
}

/**
 * Deep-link from Portfolio Lab: ?tickers=SPY,QQQ&weights=0.6,0.4 (decimals or percents),
 * or ?weights=SPY:0.6,QQQ:0.4. Returns null when absent/unusable.
 */
export function parseDeepLink(
  sp: URLSearchParams,
): { tickers: string[]; weights: Record<string, number> } | null {
  const tRaw = sp.get("tickers");
  const wRaw = sp.get("weights");
  if (!tRaw && !wRaw) return null;
  const weights: Record<string, number> = {};
  let tickers = (tRaw ?? "")
    .split(/[,\s]+/)
    .map(cleanTicker)
    .filter(Boolean);
  if (wRaw) {
    const parts = wRaw.split(/[,\s]+/).filter(Boolean);
    if (parts.every((p) => p.includes(":"))) {
      for (const p of parts) {
        const [t, v] = p.split(":");
        const n = parseFloat(v);
        if (cleanTicker(t) && Number.isFinite(n)) weights[cleanTicker(t)] = n;
      }
      if (!tickers.length) tickers = Object.keys(weights);
    } else if (parts.length === tickers.length) {
      parts.forEach((p, i) => {
        const n = parseFloat(p.replace("%", ""));
        if (Number.isFinite(n)) weights[tickers[i]] = n;
      });
    }
    const sum = Object.values(weights).reduce((a, b) => a + b, 0);
    if (Math.abs(sum) > 1.5 || wRaw.includes("%"))
      for (const k of Object.keys(weights)) weights[k] /= 100;
  }
  tickers = [...new Set(tickers)];
  if (tickers.length < 2) return null;
  return { tickers, weights };
}

export function shallowEqualJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
