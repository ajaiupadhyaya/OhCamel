/**
 * Wire types for /api/options/* (quant/src/ohcamel_quant/api/routers/options.py).
 * Vols, rates, yields and returns are DECIMALS; `dte` is calendar days; `T` is years ACT/365.
 * NaN / ±inf arrive as null (e.g. an unbounded max loss).
 */
import type { Envelope, FramePayload, SeriesPayload } from "../../lib/types";

export const ESTIMATORS = ["close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang"] as const;
export type Estimator = (typeof ESTIMATORS)[number];

// ------------------------------------------------------------------ realized
export interface ConeRow {
  horizon: number;
  min: number;
  max: number;
  mean: number;
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  current: number;
  current_pctile: number;
  n_windows: number;
}

export interface VrpNow {
  implied: number;
  realized: number;
  vol_premium: number;
  variance_premium: number;
  ratio: number | null;
  estimator: string;
  realized_window: number;
  as_of?: string;
  implied_source: string;
  close_to_close?: { implied: number; realized: number; vol_premium: number; variance_premium: number; ratio: number | null };
  model_free?: { implied: number; realized: number; vol_premium: number; variance_premium: number; ratio: number | null } | null;
}

export interface Realized extends Envelope {
  ticker: string;
  price_symbol: string;
  as_of: string;
  window: number;
  estimator: Estimator;
  rolling: FramePayload;
  current: Record<string, Record<Estimator, number | null>>;
  cone: ConeRow[];
  implied_term: { dte: (number | null)[]; atm_iv: (number | null)[] } | null;
  vrp: VrpNow | null;
  vrp_history: {
    index: string;
    frame: FramePayload;
    mean_premium_forward: number | null;
    share_positive_forward: number | null;
    mean_premium_trailing: number | null;
  } | null;
  close: SeriesPayload;
  method: Record<string, unknown>;
}

// ------------------------------------------------------------------ expiries / slices
export interface SliceSummary {
  slice: string;
  expiry: string;
  settlement: string;
  roots: string;
  T: number;
  dte: number;
  forward: number;
  discount: number;
  rate: number | null;
  div_yield: number | null;
  rate_source: string;
  rate_se: number | null;
  forward_se: number | null;
  parity_rmse: number | null;
  parity_strikes: number | null;
  n_contracts: number;
  n_valid: number;
  n_smile: number;
  n_arb_bound: number;
  vendor_iv_mad: number | null;
  expiry_ts: string;
}

interface ChainHeader extends Envelope {
  underlying: string;
  spot: number;
  as_of: string;
  exercise_style: "European" | "American";
}

export interface Expiries extends ChainHeader {
  expiries: SliceSummary[];
  errors: Record<string, string>;
  method: Record<string, string>;
}

// ------------------------------------------------------------------ chain
export interface Quote {
  contract: string;
  root: string;
  strike: number;
  type: "C" | "P";
  bid: number | null;
  ask: number | null;
  mid: number | null;
  last: number | null;
  volume: number | null;
  open_interest: number | null;
  k: number | null;
  otm: boolean;
  valid: boolean;
  wide: boolean;
  zero_bid: boolean;
  crossed: boolean;
  no_quote: boolean;
  stale: boolean;
  arb_bound: boolean;
  use_smile: boolean;
  iv: number | null;
  iv_bid: number | null;
  iv_ask: number | null;
  vendor_iv: number | null;
  iv_minus_vendor: number | null;
  model_price: number | null;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta_day: number | null;
  rho: number | null;
  vanna: number | null;
  volga: number | null;
  charm_day: number | null;
}

export interface SviFit {
  params: { a: number; b: number; rho: number; m: number; sigma: number };
  T: number;
  n: number;
  rmse_w: number;
  rmse_vol_pts: number;
  max_abs_err_vol_pts: number;
  k_min: number;
  k_max: number;
  constrained_a: boolean;
  butterfly: { arbitrage_free: boolean; g_min: number | null; k_at_g_min: number | null; violation_k: number[]; k_range: [number, number] };
  wing_slopes: [number, number];
  min_variance: number;
}

export interface Chain extends ChainHeader {
  expiry: string;
  slice: {
    expiry: string;
    settlement: string;
    T: number;
    dte: number;
    forward: number;
    discount: number;
    rate: number | null;
    div_yield: number | null;
    rate_source: string;
    rate_se: number | null;
    parity_rmse: number | null;
    n_contracts: number;
    n_valid: number;
    n_smile: number;
    vendor_iv_mad: number | null;
  };
  expiries: string[];
  quotes: Quote[];
  smile: { fit: SviFit; k: number[]; strike: number[]; iv: number[] } | null;
  multiplier: number;
  greeks_units: Record<string, string>;
  method: Record<string, unknown>;
}

// ------------------------------------------------------------------ surface
export interface TermRow {
  slice: string;
  expiry: string;
  T: number;
  dte: number;
  forward: number;
  discount: number;
  rate: number | null;
  div_yield: number | null;
  settlement: string;
  n_smile: number;
  atm_iv_market?: number | null;
  atm_iv?: number | null;
  skew_slope?: number | null;
  svi_rmse_vol_pts?: number | null;
  butterfly_ok?: boolean | null;
  g_min?: number | null;
  rr_25d?: number | null;
  bf_25d?: number | null;
  iv_call_25d?: number | null;
  iv_put_25d?: number | null;
  rr_10d?: number | null;
  bf_10d?: number | null;
  mf_var?: number | null;
  mf_vol?: number | null;
  mf_strikes?: number | null;
  mf_K0?: number | null;
  implied_move?: number | null;
  straddle?: number | null;
  straddle_strike?: number | null;
}

export interface SmileSlice {
  slice: string;
  T: number;
  dte: number;
  fit: SviFit;
  market: { k: number[]; iv: number[]; iv_bid: (number | null)[]; iv_ask: (number | null)[]; strike: number[]; type: ("C" | "P")[] };
  curve: { k: number[]; iv: number[] };
}

export interface CalendarCheck {
  T1: number;
  T2: number;
  min_dw: number | null;
  k_at_min: number | null;
  violation: boolean;
  violation_k: number[];
}

export interface Surface extends ChainHeader {
  term_structure: TermRow[];
  smiles: SmileSlice[];
  grid: { k: number[]; moneyness: number[]; T: number[]; days: number[]; iv: (number | null)[][] };
  calendar: CalendarCheck[];
  vix_style_30d: { sigma2: number; index: number; T1: number; T2: number; extrapolated: boolean; target_days: number } | null;
  atm_30d: { iv: number; extrapolated: boolean; method: string } | null;
  expiries: SliceSummary[];
  errors: Record<string, string>;
  method: Record<string, string>;
}

// ------------------------------------------------------------------ density
export interface Moments {
  mean: number | null;
  std: number | null;
  skew: number | null;
  excess_kurtosis: number | null;
}

export interface Density extends ChainHeader {
  expiry: string;
  T: number;
  dte: number;
  forward: number;
  discount: number;
  strikes: number[];
  density: (number | null)[];
  cdf: (number | null)[];
  lognormal_density: (number | null)[];
  atm_iv: number;
  checks: { integral: number; mean: number; forward: number; mean_minus_forward: number; negative_mass: number; butterfly: SviFit["butterfly"] };
  moments: Moments;
  lognormal_moments: Moments;
  quantiles: { p: number; level: number; return: number }[];
  prob_below: { level: number; moneyness: number; p: number }[];
  prob_move: { move: number; p: number; p_lognormal: number }[];
  method: Record<string, string>;
}

// ------------------------------------------------------------------ strategy
export interface LegSpec {
  kind: "option" | "stock";
  qty: number;
  expiry?: string;
  strike?: number;
  type?: "C" | "P";
}

export interface LegOut {
  kind: "option" | "stock";
  qty: number;
  contract?: string;
  expiry?: string;
  slice?: string;
  strike?: number;
  type?: "C" | "P";
  bid?: number | null;
  ask?: number | null;
  mid?: number | null;
  price?: number;
  iv?: number | null;
  iv_source?: string;
  dte?: number;
  delta?: number | null;
  gamma?: number | null;
  vega?: number | null;
  theta_day?: number | null;
}

export interface StrategyOut extends ChainHeader {
  legs: LegOut[];
  cost: { mid: number; natural: number; friction: number; direction: "debit" | "credit" };
  greeks: Record<string, number | null>;
  horizon: { T_years: number; days: number; expiry: string; slice: string };
  grid: { spot: number[]; payoff_at_expiry: number[]; curves: Record<string, { t_years: number; days: number; pnl: (number | null)[] }> };
  breakevens: number[];
  max_profit: number | null;
  max_loss: number | null;
  asymptotic_slope: number;
  prob_profit: number | null;
  expected_pnl_q: number | null;
  density_source: string;
  method: Record<string, string>;
}

// ------------------------------------------------------------------ calculator
export interface Greeks {
  price: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  theta_day: number;
  rho: number;
  vanna: number;
  volga: number;
  charm: number;
  charm_day: number;
  d1: number;
  d2: number;
}

export interface PriceOut extends Envelope {
  inputs: { S: number; K: number; T: number; sigma: number; r: number; q: number; type: "C" | "P"; price: number | null };
  forward: number;
  discount: number;
  implied_vol: number | null;
  result: Greeks;
  call: Greeks;
  put: Greeks;
  parity_check: { C_minus_P: number; "S_e^-qT_minus_K_e^-rT": number; gap: number };
  curves: { spot: number[]; value_today: number[]; payoff_at_expiry: number[]; delta: number[]; gamma: number[]; vega: number[]; theta_day: number[] };
  method: Record<string, string>;
}

export interface FredSeries extends Envelope {
  ids: string[];
  summary: Record<string, { latest: number | null; date: string | null }>;
}
