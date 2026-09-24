/**
 * Wire types for /api/backtest/* (quant/src/ohcamel_quant/api/routers/backtest.py).
 * Returns, weights, vols and drawdowns are decimals; Sharpe ratios are annualized.
 */
import type { Envelope, FramePayload, ParamSpec, SeriesPayload } from "../../lib/types";

export interface StrategySpec {
  key: string;
  name: string;
  category: string;
  params: (ParamSpec & { type: string })[];
  citation: string;
  explanation: string;
  default_rebalance: string;
  default_tickers: string[];
  suggested_universes: string[];
  min_assets: number;
  max_assets: number | null;
  needs_market: boolean;
  default_max_leverage: number;
  notes: string[];
}

export interface Catalog extends Envelope {
  strategies: StrategySpec[];
  universes: { key: string; label: string | null; tickers: string[] }[];
  rebalance_choices: string[];
  engine_defaults: { cost_bps: number; borrow_bps: number; execution_lag: number; benchmark: string; vol_estimator: string; vol_halflife: number; vol_window: number };
  caps: { grid_combinations: number; bootstrap_reps: number; spa_reps: number; tickers: number; cost_points: number };
  method: { model: string; reference: string };
}

export interface Summary {
  ann_vol: number | null;
  best_day: number | null;
  cagr: number | null;
  calmar: number | null;
  cvar_95_hist: number | null;
  hit_rate: number | null;
  kurtosis: number | null;
  max_drawdown: number | null;
  min_track_record_years_95: number | null;
  observations: number;
  psr_vs_0: number | null;
  sharpe: number | null;
  skew: number | null;
  sortino: number | null;
  total_return: number | null;
  var_95_hist: number | null;
  worst_day: number | null;
  years: number | null;
}

export interface Method {
  model: string;
  strategy: string;
  reference: string;
  params: Record<string, unknown>;
  engine: {
    execution: string;
    rebalance: string;
    cost_bps: number;
    borrow_bps_per_year: number;
    max_gross_leverage: number | null;
    vol_target: number | null;
    vol_estimator: string | null;
    cash: string;
  };
  validation?: Record<string, unknown>;
  costs?: string;
}

export interface Bootstrap {
  ci_high?: number;
  ci_low?: number;
  expected_block_length?: number;
  histogram?: { counts: number[]; edges: number[] };
  level?: number;
  method?: string;
  prob_sharpe_le_0?: number;
  reps?: number;
  sharpe?: number;
  std_error?: number;
  error?: string;
}

export interface RunOut extends Envelope {
  strategy: { key: string; name: string; category: string; citation: string; explanation: string };
  params: Record<string, unknown>;
  tickers: string[];
  benchmark: string;
  window: { data_start: string; live_start: string; end: string; live_sessions: number; years: number };
  equity: FramePayload;
  drawdown: FramePayload;
  metrics: { net: Summary; gross: Summary; benchmark: Summary };
  relative: { alpha_ann: number | null; beta: number | null; correlation: number | null; down_capture: number | null; up_capture: number | null; information_ratio: number | null; tracking_error: number | null };
  drawdowns: { depth: number; peak: string; trough: string; recovery: string | null; sessions_to_recovery: number | null; sessions_to_trough: number }[];
  rolling: FramePayload;
  weights_monthly: FramePayload;
  turnover_monthly: SeriesPayload;
  exposure: FramePayload;
  vol_scale: SeriesPayload | null;
  trades: {
    annual_cost_drag: number | null;
    annual_turnover: number | null;
    asset_trades: number;
    avg_gross_exposure: number | null;
    avg_holdings: number | null;
    avg_net_exposure: number | null;
    avg_turnover_per_execution: number | null;
    executions: number;
    total_borrow_paid: number | null;
    total_cost_paid: number | null;
  };
  per_asset: { ticker: string; avg_weight: number; pct_time_long: number; pct_time_short: number; pnl_contribution: number; traded_notional: number; trades: number }[];
  calendar_returns: { year: number; strategy: number | null; benchmark: number | null }[];
  monthly_returns: ({ year: number } & Record<string, number | null>)[];
  bootstrap_sharpe: Bootstrap;
  psr: { psr_vs_0: number | null; reference: string };
  look_ahead_audit: { checked: string[]; max_abs_diff: number; passed: boolean; points: number };
  method: Method;
}

export interface SweepOut extends Envelope {
  strategy: { key: string; name: string; citation: string };
  base_params: Record<string, unknown>;
  grid: Record<string, number[]>;
  combos: Record<string, number>[];
  table: ({ combo: number; sharpe: number | null; sortino: number | null; cagr: number | null; ann_vol: number | null; max_drawdown: number | null; annual_turnover: number | null } & Record<string, number | null>)[];
  heatmap: { metric: string; x_param: string; x_values: number[]; y_param?: string; y_values?: number[]; z: (number | null)[][] | (number | null)[] };
  best: { combo: number; params: Record<string, number>; sharpe: number };
  pbo: {
    error?: string;
    pbo?: number;
    n_partitions?: number;
    n_combinations?: number;
    n_trials?: number;
    rows_used?: number;
    selected_counts?: number[];
    logits?: { counts: number[]; edges: number[]; mean: number | null; median: number | null };
    degradation?: { intercept: number | null; slope: number | null; slope_pvalue: number | null; r2: number | null; prob_oos_loss: number | null; is_sharpe_ann: number[]; oos_sharpe_ann: number[] };
  };
  deflated_sharpe: { dsr: number | null; n_trials: number; psr_vs_0: number | null; selected: number; sharpe_ann: number | null; sr0_annualized: number | null; sr0_per_period: number | null; var_sr: number | null; error?: string };
  spa: { block_size?: number; n_models?: number; observations?: number; pvalue_consistent?: number | null; pvalue_lower?: number | null; pvalue_upper?: number | null; reps?: number; error?: string };
  benchmark: { ticker: string; summary: Summary };
  equity_weekly: FramePayload;
  window: { start: string; end: string; sessions: number };
  method: Method;
}

export interface Fold {
  combo: number;
  params: Record<string, number>;
  is_start: string;
  is_end: string;
  oos_start: string;
  oos_end: string;
  is_score: number | null;
  is_sharpe: number | null;
  oos_sharpe: number | null;
  oos_return: number | null;
  switch_cost: number | null;
}

export interface WalkForwardOut extends Envelope {
  strategy: { key: string; name: string; citation: string };
  grid: Record<string, number[]>;
  combos: Record<string, number>[];
  folds: Fold[];
  equity: FramePayload;
  oos_summary: Summary;
  benchmark_summary: Summary;
  reference: { params: Record<string, number>; summary: Summary } | null;
  walk_forward_efficiency: number | null;
  mean_is_sharpe: number | null;
  oos_sharpe: number | null;
  distinct_choices: number;
  method: Method;
}

export interface CostPoint {
  cost_bps: number;
  sharpe: number | null;
  cagr: number | null;
  ann_vol: number | null;
  max_drawdown: number | null;
  annual_cost_drag: number | null;
}

export interface CostsOut extends Envelope {
  strategy: { key: string; name: string; citation: string };
  params: Record<string, unknown>;
  curve: CostPoint[];
  breakeven_bps_sharpe_zero: number | null;
  breakeven_bps_vs_benchmark: number | null;
  annual_turnover: number | null;
  benchmark: { ticker: string; summary: Summary };
  window: { live_start: string; end: string; sessions: number };
  method: Method;
}
