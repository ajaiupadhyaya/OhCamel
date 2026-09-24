/**
 * Wire types for /api/portfolio/* (quant/src/ohcamel_quant/api/routers/portfolio.py).
 * Units: weights, returns and vols are annual DECIMALS; eigenvalues are unitless.
 */
import type { Envelope, FramePayload, SeriesPayload } from "../../lib/types";

export type MethodName =
  | "equal_weight"
  | "inverse_volatility"
  | "min_variance"
  | "max_sharpe"
  | "mean_variance"
  | "risk_parity"
  | "hrp"
  | "herc"
  | "max_diversification"
  | "min_cvar";
export type CovName =
  | "sample"
  | "ewma"
  | "lw_constant_corr"
  | "lw_identity"
  | "oas"
  | "mp_denoise";
export type ReturnsModel =
  | "historical"
  | "james_stein"
  | "capm"
  | "black_litterman";
export type LinkageName = "single" | "ward" | "average" | "complete";

export interface MethodSpec {
  name: MethodName;
  label: string;
  description: string;
  reference: string;
  needs_expected_returns: boolean;
  needs_risk_free: boolean;
  honours_constraints: boolean;
  parameters: {
    name: string;
    type: string;
    default: unknown;
    description: string;
  }[];
}

export interface MethodsCatalog extends Envelope {
  methods: MethodSpec[];
  covariance_estimators: { name: CovName; reference: string }[];
  returns_models: { name: ReturnsModel; reference: string }[];
  walk_forward: { reference: string; tests: string[] };
}

export interface UniverseMeta {
  tickers: string[];
  start: string;
  end: string;
  observations: number;
}

export interface Dendrogram {
  linkage: number[][];
  order: string[];
  icoord: number[][];
  dcoord: number[][];
  ivl: string[];
}

export interface BisectionStep {
  left: string[];
  right: string[];
  var_left: number;
  var_right: number;
  alpha: number;
}

export interface PortfolioResult {
  method: MethodName;
  weights: Record<string, number>;
  expected_return: number | null;
  volatility: number;
  sharpe: number | null;
  risk_contributions: Record<string, number>;
  risk_contributions_pct: Record<string, number>;
  diversification_ratio: number | null;
  effective_bets: number | null;
  effective_n: number | null;
  params: Record<string, unknown>;
  notes: string[];
  extra: {
    dendrogram?: Dendrogram;
    bisection?: BisectionStep[];
    cvar_daily?: number;
    var_daily?: number;
    scenarios?: number;
    [k: string]: unknown;
  };
  reference: string;
}

export interface BLView {
  label: string;
  kind: "absolute" | "relative";
  long: string;
  short: string | null;
  value: number;
  confidence: number;
  omega: number;
  prior_implied: number;
}

export interface ExpectedReturnsPayload {
  model: ReturnsModel;
  mu: Record<string, number>;
  params: Record<string, unknown> & {
    delta?: number;
    tau?: number;
    risk_free?: number;
    delta_source?: string;
  };
  reference: string;
  // historical
  geometric?: Record<string, number>;
  standard_error?: Record<string, number>;
  // black-litterman
  prior_excess?: Record<string, number>;
  posterior_excess?: Record<string, number>;
  market_weights?: Record<string, number>;
  unconstrained_bl_weights?: Record<string, number>;
  views?: BLView[];
  prior_source?: string;
  [k: string]: unknown;
}

export interface RiskFreeInfo {
  source: "user" | "market" | null;
  annual?: number;
  series?: string;
  error?: string;
}

export interface OptimizeOut extends Envelope {
  universe: UniverseMeta;
  result: PortfolioResult;
  allocation: {
    ticker: string;
    weight: number;
    risk_contribution: number;
    risk_contribution_pct: number;
    expected_return: number | null;
  }[];
  expected_returns: ExpectedReturnsPayload;
  covariance: {
    estimator: CovName;
    shrinkage: number | null;
    condition_number: number | null;
    reference: string;
  };
  assets: {
    ticker: string;
    expected_return: number | null;
    volatility: number;
    historical_mean: number;
    sharpe: number | null;
  }[];
  risk_free: RiskFreeInfo;
  in_sample_growth: SeriesPayload;
  method: {
    name: MethodName;
    label: string;
    reference: string;
    cov_method: CovName;
    returns_model: ReturnsModel;
    params: Record<string, unknown>;
    constraints: Record<string, unknown>;
  };
}

export interface FrontierPoint {
  ret: number;
  vol: number;
  sharpe?: number | null;
  weights: Record<string, number>;
}

export interface FrontierOut extends Envelope {
  universe: UniverseMeta;
  frontier: FrontierPoint[];
  gmv: FrontierPoint;
  tangency: FrontierPoint | null;
  cml: { vol: number; ret: number }[] | null;
  assets: { ticker: string; ret: number; vol: number }[];
  overlay: {
    method: MethodName;
    label: string;
    ret?: number;
    vol?: number;
    sharpe?: number | null;
    weights?: Record<string, number>;
    error?: string;
  }[];
  risk_free: RiskFreeInfo;
  expected_returns: ExpectedReturnsPayload;
  method: Record<string, unknown>;
}

export interface Spectrum {
  eigenvalues: number[];
  q: number;
  sigma2: number;
  lambda_minus: number;
  lambda_plus: number;
  n_signal: number;
  signal_variance_share: number;
  mp_grid: number[];
  mp_density: number[];
  bandwidth: number;
  observations: number;
  assets: number;
  estimator_eigenvalues: number[];
  sample_eigenvalues: number[];
}

export interface CovarianceOut extends Envelope {
  universe: UniverseMeta;
  estimator: CovName;
  shrinkage: number | null;
  params: Record<string, unknown>;
  covariance: FramePayload<number>;
  correlation: FramePayload<number>;
  volatility: Record<string, number>;
  hrp_order: string[];
  correlation_ordered: FramePayload<number>;
  dendrogram: Dendrogram;
  spectrum: Spectrum;
  comparison: {
    estimator: CovName;
    shrinkage: number | null;
    condition_number: number | null;
    average_correlation: number | null;
    reference: string;
  }[];
  method: Record<string, unknown>;
}

export interface SharpeTest {
  test: string;
  z: number | null;
  p_value: number | null;
  sharpe_diff_annual: number | null;
  se_annual?: number | null;
  lags?: number;
  kernel?: string;
  correlation?: number | null;
  reference: string;
}

export interface CompareStats {
  method: MethodName;
  label: string;
  cagr: number | null;
  annual_return: number | null;
  annual_vol: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  calmar: number | null;
  total_return: number | null;
  observations: number;
  annual_turnover: number | null;
  cost_drag_annual: number | null;
  rebalances: number;
  failed_rebalances?: number;
  turnover_limit_relaxed?: number;
}

export interface CompareOut extends Envelope {
  universe: UniverseMeta;
  stats: CompareStats[];
  tests: Record<
    string,
    { vs: MethodName; ledoit_wolf?: SharpeTest; memmel?: SharpeTest }
  >;
  equity: FramePayload<number>;
  drawdown: FramePayload<number>;
  weights: Record<string, FramePayload<number>>;
  turnover: Record<string, SeriesPayload>;
  failures: Record<string, unknown[]>;
  rebalance_dates: string[];
  risk_free: RiskFreeInfo;
  method: {
    design: string;
    window: number;
    freq: string;
    cost_bps: number;
    cov_method: string;
    mu_method: string;
    benchmark: MethodName;
    labels: Record<string, string>;
    reference: string;
    [k: string]: unknown;
  };
}
