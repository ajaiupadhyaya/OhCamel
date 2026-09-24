/**
 * Wire types for the Portfolio Lab endpoints (see quant/src/ohcamel_quant/api/routers/
 * performance.py, risk.py and factors.py). Only the fields the page reads are typed.
 * Returns, weights, vols, VaR and ES are DECIMALS; `*_usd` fields are dollars of notional.
 */
import type { Envelope, FramePayload, SeriesPayload } from "../../lib/types";

export type Rebalance = "daily" | "weekly" | "monthly" | "quarterly" | "annual" | "none";

export interface Holding {
  ticker: string;
  weight: number;
}

// ------------------------------------------------------------------ performance
export interface PerfSummary {
  start: string;
  end: string;
  n_obs: number;
  years: number;
  total_return: number | null;
  cagr: number | null;
  mean_ann: number | null;
  vol_ann: number | null;
  rf_ann: number | null;
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  omega: number | null;
  max_drawdown: number | null;
  max_drawdown_peak: string | null;
  max_drawdown_trough: string | null;
  max_drawdown_recovery: string | null;
  current_drawdown: number | null;
  ulcer_index: number | null;
  skew: number | null;
  excess_kurtosis: number | null;
  jarque_bera: number | null;
  jarque_bera_p: number | null;
  hit_rate: number | null;
  payoff_ratio: number | null;
  best_day: number | null;
  best_day_date: string | null;
  worst_day: number | null;
  worst_day_date: string | null;
  best_month: number | null;
  worst_month: number | null;
  positive_months: number | null;
  var_level: number;
  var: number | null;
  cvar: number | null;
  tail_ratio: number | null;
}

export interface SharpeInference {
  sharpe: number | null;
  se_iid: number | null;
  se_nonnormal: number | null;
  se_hac: number | null;
  ci95_lower: number | null;
  ci95_upper: number | null;
  t_stat_hac: number | null;
  sharpe_autocorr_adjusted: number | null;
  rho_1: number | null;
  n_obs: number;
  psr: number | null;
  psr_benchmark_sharpe: number;
  min_track_record_periods: number | null;
  min_track_record_years: number | null;
  min_track_record_confidence: number;
  track_record_sufficient: boolean | null;
  skew_population: number | null;
  kurtosis_population: number | null;
  deflated: number | null | Record<string, unknown>;
}

export interface Relative {
  alpha_ann: number | null;
  alpha_t: number | null;
  alpha_p: number | null;
  beta: number | null;
  beta_t: number | null;
  beta_se: number | null;
  r2: number | null;
  correlation: number | null;
  tracking_error: number | null;
  active_return_ann: number | null;
  information_ratio: number | null;
  treynor: number | null;
  m2: number | null;
  m2_excess: number | null;
  up_capture: number | null;
  down_capture: number | null;
  benchmark_vol: number | null;
  benchmark_cagr: number | null;
  benchmark_sharpe: number | null;
  nw_lags: number;
}

export interface DrawdownEpisode {
  peak: string;
  trough: string;
  recovery: string | null;
  depth: number;
  length: number;
  decline: number;
  recovery_periods: number | null;
  recovered: boolean;
}

export interface PerformanceOut extends Envelope {
  portfolio: {
    holdings: Holding[];
    benchmark: string | null;
    observations: number;
    start: string;
    end: string;
    rebalance: Rebalance;
    current_weights: Record<string, number>;
    turnover: { rebalances: number; mean_per_rebalance: number; annual: number | null } | null;
    cash_weight: number;
  };
  summary: PerfSummary;
  benchmark_summary: PerfSummary | null;
  sharpe_inference: SharpeInference;
  relative: Relative | null;
  drawdowns: DrawdownEpisode[];
  monthly_table: { columns: string[]; rows: Record<string, number | null>[] };
  equity: FramePayload;
  drawdown: FramePayload;
  rolling: FramePayload | null;
  rolling_window: number;
  returns_histogram: { counts: number[]; edges: number[] };
  method: Record<string, string>;
}

// ------------------------------------------------------------------ risk
export interface RiskMeta {
  holdings: Holding[];
  notional: number;
  benchmark: string;
  observations: number;
  start: string;
  end: string;
}

export interface Estimate {
  model: string;
  alpha: number;
  horizon: number;
  var: number | null;
  es: number | null;
  var_usd?: number | null;
  es_usd?: number | null;
  params?: Record<string, unknown>;
  notes?: string[];
  reference?: string;
  error?: string;
}

export interface RiskSummaryOut extends Envelope {
  portfolio: RiskMeta;
  alphas: number[];
  horizon: number;
  estimates: Estimate[];
  table: Record<string, Record<string, { var: number | null; es: number | null; var_usd: number | null; es_usd: number | null }>>;
  stats: {
    mean_daily: number;
    vol_daily: number;
    vol_annualized: number;
    skew: number;
    excess_kurtosis: number;
    min: number;
    max: number;
    jarque_bera: { stat: number; p_value: number };
    max_drawdown: number;
  };
  distribution: { centers: number[]; counts: number[]; normal_expected: number[]; t_expected: number[] | null };
  method: Record<string, unknown>;
}

export interface DecompPosition {
  ticker: string;
  weight: number;
  vol_annualized: number;
  marginal_var: number;
  component_var: number;
  pct_var: number;
  marginal_es: number;
  component_es: number;
  pct_es: number;
  hist_component_es: number;
  hist_pct_es: number;
  incremental_var: number;
  incremental_hist_es: number;
  standalone_var: number;
  standalone_es: number;
  component_var_usd: number;
  component_es_usd: number;
  hist_component_es_usd: number;
  incremental_var_usd: number;
  standalone_var_usd: number;
}

export interface DecompositionOut extends Envelope {
  portfolio: RiskMeta;
  alpha: number;
  horizon: number;
  positions: DecompPosition[];
  totals: {
    parametric_var: number;
    parametric_es: number;
    historical_var: number;
    historical_es: number;
    sum_standalone_var: number;
    diversification_benefit_var: number;
    diversification_ratio: number;
    portfolio_vol_annualized: number;
    parametric_var_usd: number;
    parametric_es_usd: number;
    historical_var_usd: number;
    historical_es_usd: number;
    sum_standalone_var_usd: number;
    diversification_benefit_var_usd: number;
  };
  correlation: FramePayload;
  benchmark: {
    benchmark: string;
    portfolio_beta: number;
    position_beta: Record<string, number>;
    tracking_error_annualized: number;
    te_contribution: Record<string, number>;
    correlation_with_benchmark: number;
  } | null;
  historical_tail_dates: string[];
  method: Record<string, string>;
}

export interface LrTest {
  lr?: number;
  p_value?: number;
}

export interface Scorecard {
  T: number;
  exceptions: number;
  expected: number;
  exception_rate: number;
  kupiec: { lr: number | null; p_value: number | null };
  christoffersen: { lr_ind: number | null; p_value_ind: number | null; lr_cc: number | null; p_value_cc: number | null };
  traffic_light: { zone: "green" | "yellow" | "red"; cumulative_probability: number; exceptions: number; T: number; green_max: number; yellow_max: number };
  dq: { dq: number | null; p_value: number | null; df: number } | null;
  acerbi_szekely_z2: { z2: number | null; indicative_verdict: string; critical_5pct: number; critical_0_01pct: number } | null;
  quantile_score: number | null;
  fz0_loss: number | null;
  mean_var: number | null;
  mean_es: number | null;
  basel_last_250?: { zone: "green" | "yellow" | "red"; exceptions: number; T: number; plus_factor: number; multiplier: number } | null;
}

export interface BacktestOut extends Envelope {
  portfolio: RiskMeta;
  alpha: number;
  window: number;
  refit_every: number;
  scorecards: Record<string, Scorecard>;
  ranking_by_fz0: string[];
  series: {
    dates: string[];
    returns: number[];
    pnl_usd: number[];
    var: Record<string, (number | null)[]>;
    es: Record<string, (number | null)[]>;
    exceptions: Record<string, string[]>;
  };
  compute_seconds: number;
  method: { tests: Record<string, string> } & Record<string, unknown>;
}

export interface GarchParams {
  model: string;
  mu_pct: number;
  omega: number;
  alpha: number;
  gamma: number;
  beta: number;
  nu: number;
  persistence: number;
  half_life_days: number | null;
  unconditional_vol_annualized: number | null;
  next_day_vol: number;
  std_err: Record<string, number>;
  loglik: number;
  aic: number;
  bic: number;
  converged: boolean;
}

export interface GarchModelOut {
  params: GarchParams;
  var_es_1d: Estimate;
  var_es_horizon: Estimate;
  term_structure: FramePayload;
  conditional_vol_annualized: SeriesPayload;
  news_impact: FramePayload;
  reference: string;
}

export interface GarchOut extends Envelope {
  portfolio: RiskMeta;
  alpha: number;
  horizon: number;
  forecast_days: number;
  models: { garch: GarchModelOut; gjr: GarchModelOut };
  comparison: { aic: Record<string, number>; bic: Record<string, number>; lr_gjr_vs_garch: number; lr_p_value: number; preferred_by_bic: string };
  fhs: Estimate;
  ewma_vol_annualized: SeriesPayload;
  realized_abs_return_annualized: SeriesPayload;
  method: Record<string, string>;
}

// ------------------------------------------------------------------ stress
export interface ScenarioDef {
  id: string;
  name: string;
  start: string;
  end: string;
  anchor: string;
  category: string;
  description: string;
}

export interface ScenariosOut extends Envelope {
  scenarios: ScenarioDef[];
}

export interface ScenarioPosition {
  ticker: string;
  weight: number;
  source: "actual" | "proxy" | "missing";
  return?: number | null;
  pnl_usd?: number | null;
  contribution?: number | null;
  beta?: number;
  beta_obs?: number;
  beta_r2?: number;
}

export interface ScenarioResult {
  id: string;
  name: string;
  category: string;
  description: string;
  start: string;
  end: string;
  base_date?: string;
  end_date?: string;
  sessions?: number;
  complete: boolean;
  portfolio_return: number | null;
  pnl_usd: number | null;
  benchmark: string;
  benchmark_return: number | null;
  max_drawdown: number | null;
  worst_day?: { date: string; return: number } | null;
  path: { dates: string[]; portfolio: number[]; benchmark: (number | null)[] | null } | null;
  proxied: string[];
  missing: string[];
  positions: ScenarioPosition[];
  notes: string[];
}

export interface HistoricalStressOut extends Envelope {
  portfolio: { holdings: Holding[]; notional: number; benchmark: string };
  scenarios: ScenarioResult[];
  method: Record<string, string>;
}

export interface ConditionalPosition {
  ticker: string;
  weight: number;
  shocked: boolean;
  move: number;
  move_in_sd: number | null;
  pnl: number;
  pnl_usd: number;
  sensitivity: Record<string, number> | null;
}

export interface ConditionalStressOut extends Envelope {
  portfolio: RiskMeta;
  shocks: Record<string, number>;
  portfolio_return: number;
  pnl_usd: number;
  positions: ConditionalPosition[];
  conditional_residual_vol_daily: number | null;
  shock_zscores: Record<string, number>;
  condition_number: number | null;
  method: Record<string, unknown>;
}

// ------------------------------------------------------------------ factors
export interface FactorModelsOut extends Envelope {
  models: { key: string; label: string; factors: string[]; reference: string }[];
  factor_descriptions: Record<string, string>;
  etf_preset: { name: string; long: string; short: string | null; label: string }[];
}

export interface Loading {
  term: string;
  estimate: number;
  std_error: number;
  t_stat: number;
  p_value: number;
  ci_lower: number;
  ci_upper: number;
  estimate_ann: number;
}

export interface FactorOut extends Envelope {
  target: { holdings: Holding[]; rebalance: Rebalance; ticker: string | null };
  model: { key: string; label: string; factors: (string | { name: string; long: string; short: string | null })[] };
  loadings: Loading[];
  stats: {
    alpha_daily: number;
    alpha_ann: number;
    alpha_t: number;
    alpha_p: number;
    r2: number;
    adj_r2: number;
    n_obs: number;
    nw_lags: number;
    residual_vol_ann: number;
    appraisal_ratio: number | null;
    start: string;
    end: string;
    excess_return_ann: number;
  };
  risk_decomposition: {
    total_vol_ann: number;
    systematic_vol_ann: number;
    idiosyncratic_vol_ann: number;
    systematic_share: number;
    table: { factor: string; beta: number; factor_vol_ann: number; variance_contribution: number; share_of_total: number; vol_contribution_ann: number }[];
    factor_correlation: { factors: string[]; matrix: number[][] };
  };
  attribution: {
    cumulative_linked: FramePayload;
    totals_linked: Record<string, number>;
    totals_arithmetic: Record<string, number>;
  };
  rolling: FramePayload | null;
  rolling_window: number;
  fitted_vs_actual: FramePayload;
  method: Record<string, unknown>;
}

export interface FactorDef {
  name: string;
  long: string;
  short: string | null;
}
