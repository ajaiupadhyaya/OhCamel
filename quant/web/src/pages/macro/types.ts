/**
 * Rates & Macro — wire types for /api/macro/* (routers/macro.py). Page-local.
 * Units: FRED yields, spreads in "pp" and every rate here are PERCENT; `*_bp` are basis
 * points; regime means/vols are DECIMALS per year; probabilities are 0..1.
 */
import type { Envelope, FramePayload, SeriesPayload } from "../../lib/types";

// ------------------------------------------------------------------ dashboard
export type Horizon = "1M" | "3M" | "1Y";

export interface SeriesSummary {
  latest: number;
  date: string;
  change: Record<Horizon, number | null>;
  percentile_10y: number;
  percentile_window: { start: string; end: string; n: number };
  history_min: number;
  history_max: number;
  sparkline?: SeriesPayload<number>;
}

export interface DashboardRow extends Partial<SeriesSummary> {
  id: string;
  name: string;
  category: string;
  transform: string;
  units: string;
  frequency: "D" | "W" | "M" | "Q" | string;
  error?: string;
}

export interface Dashboard extends Envelope {
  series: DashboardRow[];
  categories: string[];
  transforms: Record<string, string>;
  highlights: { sahm_rule?: { value: number; date: string; threshold: number; triggered: boolean } };
  missing: Record<string, string>;
}

// ------------------------------------------------------------------ explorer
export interface FredExplorer extends Envelope {
  ids: string[];
  transforms: Record<string, string>;
  data: FramePayload;
  summary: Record<string, Omit<SeriesSummary, "sparkline">>;
  metadata: Record<string, { id: string; title?: string; units?: string; frequency?: string; [k: string]: unknown }>;
  method: { transforms: Record<string, string> };
}

// ------------------------------------------------------------------ curve
export interface CurveTable {
  t: number[];
  discount: number[];
  zero: number[];
  inst_forward: number[];
  forward_1y: number[];
}

export interface CurveFit {
  params: Record<string, number>;
  rmse_bp: number;
  starts: number;
  fitted: { t: number[]; zero: number[] };
  residuals_bp: { t: number[]; bp: number[] };
  binding_constraints: string[];
  error?: string;
}

export interface CurveSnapshot {
  date: string;
  par: { tenors: number[]; yields: number[] };
  curve: CurveTable;
  nodes: { t: number[]; zero: number[]; discount: number[]; par: number[] | null };
  notes?: string[] | string;
}

export interface CompareCurve extends Partial<CurveSnapshot> {
  label: string;
  change_bp?: Record<string, number>;
  error?: string;
}

export interface CurveOut extends CurveSnapshot, Envelope {
  fits: { nelson_siegel?: CurveFit; svensson?: CurveFit };
  compare: CompareCurve[];
  method: Record<string, unknown>;
}

export interface CurveHistory extends Envelope {
  heatmap: { dates: string[]; tenors: number[]; yields: (number | null)[][] };
  spreads: FramePayload;
  spreads_latest: Record<string, { value_bp: number; date: string; percentile: number }>;
  pca: {
    tenors: number[];
    loadings: FramePayload;
    explained_variance: number[];
    explained_variance_all: number[];
    eigenvalues_bp2: number[];
    n_obs: number;
    factor_levels_weekly: FramePayload;
    window: { start: string; end: string };
  };
  method: { years: number; pca_years: number; pca: string };
}

// ------------------------------------------------------------------ recession
export interface ProbitModel {
  h: number;
  params: Record<string, number>;
  std_errors: Record<string, number>;
  pvalues: Record<string, number>;
  loglik: number;
  loglik_null: number;
  nobs: number;
  pseudo_r2_mcfadden: number;
  pseudo_r2_estrella: number;
  regressors: string[];
}

export interface Episode {
  start: string;
  end: string;
}

export interface RecessionOut extends Envelope {
  h: number;
  models: { spread: ProbitModel; spread_nfci?: ProbitModel };
  probability: FramePayload;
  current: { origin: string; target: string; spread: number; probability: number; probability_with_nfci?: number; nfci_origin?: string };
  spread: SeriesPayload;
  recessions: Episode[];
  sahm: null | { series: FramePayload; threshold: number; latest: { date: string; value: number; triggered: boolean }; trigger_dates: string[] };
  sample: { start: string; end: string; nobs: number };
}

// ------------------------------------------------------------------ regimes
export interface RiskComponent {
  name: string;
  label: string;
  value: number;
  as_of: string;
  percentile: number | null;
  score: number;
  rule: string;
  history_start?: string;
}

export interface RegimesOut extends Envelope {
  ticker: string;
  k: number;
  freq: "D" | "W" | "M";
  model: {
    k: number;
    freq: string;
    means_ann: number[];
    vols_ann: number[];
    transition: number[][];
    expected_duration_periods: (number | null)[];
    current_regime: number;
    current_prob: number;
    loglik: number;
    aic: number;
    bic: number;
    nobs: number;
  };
  regimes: { regime: number; mean_ann: number; vol_ann: number; expected_duration_periods: number | null; share_of_time: number }[];
  smoothed: FramePayload;
  filtered: FramePayload;
  returns: SeriesPayload;
  price: SeriesPayload;
  risk_panel: { components: RiskComponent[]; composite: number; state: "risk-on" | "risk-off" | "neutral" };
}

// ------------------------------------------------------------------ taylor
export interface TaylorOut extends Envelope {
  series: FramePayload;
  latest: { date: string; inflation: number; output_gap: number; taylor_1993: number; balanced_approach: number; fed_funds?: number; gap_taylor_minus_ff?: number; gap_balanced_minus_ff?: number };
  params: { r_star: number; pi_star: number; inflation_coef: number; gap_coef_taylor: number; gap_coef_balanced: number; defaults_are: string };
}

// ------------------------------------------------------------------ bonds
export interface BondIn {
  coupon: number;
  years?: number;
  maturity?: string;
  freq: 1 | 2 | 4 | 12;
  settlement?: string;
  price?: number;
  yield_pct?: number;
  use_curve: boolean;
  curve_date?: string;
  notional: number;
}

export interface BondOut extends Envelope {
  bond: { coupon_pct: number; maturity: string; freq: number; settlement: string; price_source: "input" | "curve" };
  clean_price: number;
  dirty_price: number;
  accrued: number;
  ytm_pct: number;
  risk: { macaulay_duration: number; modified_duration: number; convexity: number; dv01: number; dv01_notional: number; notional: number };
  cashflows: { date: string; t_years: number; cashflow: number; pv: number }[];
  price_yield: { yield_pct: number[]; price: number[]; duration_approx: number[]; duration_convexity_approx: number[] };
  curve?: {
    date: string;
    fair_clean: number;
    fair_dirty: number;
    rich_cheap: number;
    z_spread_bp: number;
    effective_duration: number;
    effective_convexity: number;
    key_rate_durations: { tenor: number; krd: number; krd01: number; krd01_notional: number }[];
    krd_sum: number;
  };
}
