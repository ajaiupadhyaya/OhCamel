/**
 * Wire types for /api/fundamentals/* (quant/src/ohcamel_quant/api/routers/fundamentals.py).
 * Money is USD as plain floats; ratios and growth rates are DECIMALS; every missing
 * figure is null (the backend never imputes).
 */
import type { Envelope, FramePayload } from "../../lib/types";

export interface Identity {
  ticker: string;
  name: string | null;
  cik: string | null;
  latest_fiscal_year_end: string;
  latest_quarter_end: string | null;
  shares_outstanding: number | null;
  shares_outstanding_as_of: string | null;
}

export interface Multiples {
  price: number;
  shares_outstanding: number;
  market_cap: number;
  enterprise_value: number | null;
  pe: number | null;
  pe_eps: number | null;
  ev_ebitda: number | null;
  ev_ebit: number | null;
  ev_sales: number | null;
  price_to_sales: number | null;
  price_to_book: number | null;
  price_to_fcf: number | null;
  fcf_yield: number | null;
  earnings_yield: number | null;
  dividend_yield: number | null;
  buyback_yield: number | null;
  shareholder_yield: number | null;
}

export interface BetaInfo {
  beta: number;
  alpha_monthly: number;
  se: number;
  t_stat: number | null;
  r2: number | null;
  n_months: number;
  start: string;
  end: string;
  blume_adjusted: number;
  excess_returns: boolean;
  benchmark: string;
}

export interface Profile extends Identity, Envelope {
  price: number;
  price_as_of: string | null;
  market_cap: number;
  enterprise_value: number | null;
  multiples: Multiples;
  multiples_basis: "ttm" | "fy";
  multiples_period_end: string;
  beta: BetaInfo | null;
  range_52w: { high: number; low: number; high_date: string; low_date: string; position: number | null } | null;
  method: Record<string, string>;
}

export type Period = "annual" | "quarterly" | "ttm";

export interface StatementRow {
  item: string;
  label: string;
  values: (number | null)[];
  yoy: (number | null)[] | null;
  available: boolean;
}
export interface StatementTable {
  periods: string[];
  rows: StatementRow[];
}
export interface Statements extends Identity, Envelope {
  period: Period;
  income_statement: StatementTable;
  balance_sheet: StatementTable;
  cash_flow: StatementTable;
  units: Record<string, string>;
}

export interface Ratios extends Identity, Envelope {
  annual: FramePayload;
  ttm: FramePayload;
  latest: { basis: "ttm" | "fy"; period_end: string; values: Record<string, number | null> };
  growth_cagr: Record<string, Record<string, number | null>>;
  groups: Record<string, { unit: string; keys: string[] }>;
  method: Record<string, string>;
}

export interface PiotroskiSignal {
  name: string;
  description: string;
  value: 0 | 1 | null;
  inputs: Record<string, number | null>;
}
export interface Piotroski {
  score: number | null;
  partial_score?: number;
  n_available?: number;
  signals: PiotroskiSignal[] | Record<string, never>;
  interpretation?: string | null;
  fiscal_years?: string[];
  missing: string[];
  reference: string;
}
export interface Component {
  name: string;
  value: number | null;
  coefficient: number;
  contribution?: number | null;
  description?: string;
}
export interface Altman {
  z: number | null;
  zone?: "distress" | "grey" | "safe" | null;
  components?: Component[];
  zones?: { distress_below: number; safe_above: number };
  fiscal_year?: string;
  missing: string[];
  reference: string;
}
export interface Beneish {
  m: number | null;
  probability?: number | null;
  flag?: boolean | null;
  flag_sensitive?: boolean | null;
  threshold?: number;
  intercept?: number;
  indices?: Component[];
  fiscal_years?: string[];
  missing: string[];
  reference: string;
}
export interface Sloan {
  ratio: number | null;
  interpretation?: string | null;
  inputs?: Record<string, number | null>;
  fiscal_years?: string[];
  missing: string[];
  reference: string;
}
export interface Ohlson {
  o: number | null;
  probability?: number | null;
  components?: Component[];
  intercept?: number;
  fiscal_years?: string[];
  missing: string[];
  reference: string;
}
export interface Scores extends Identity, Envelope {
  piotroski: Piotroski;
  altman_z: Altman;
  altman_z2: Altman;
  beneish: Beneish;
  sloan: Sloan;
  ohlson: Ohlson;
  history: { fiscal_year: string; piotroski: number | null; beneish_m: number | null; altman_z2: number | null; sloan_accruals: number | null }[];
  method: Record<string, string>;
}

/** POST /fundamentals/{t}/dcf body — every field optional; omitted = derived from data. */
export interface DcfBody {
  years?: number;
  fcff_basis?: "auto" | "ttm" | "fy";
  base_fcff?: number;
  initial_growth?: number;
  terminal_growth?: number;
  risk_free?: number;
  beta?: number;
  beta_type?: "raw" | "blume";
  erp?: number;
  cost_of_debt?: number;
  tax_rate?: number;
  wacc?: number;
}

export interface DcfInput {
  value: number | null;
  source: "derived" | "override" | "unavailable";
  method?: string;
  as_of?: string;
  [k: string]: unknown;
}

export interface DcfOut extends Identity, Envelope {
  price: number;
  price_as_of: string | null;
  market_cap: number;
  inputs: Record<string, DcfInput>;
  valuation: {
    table: { year: number; growth: number; fcff: number; discount_factor: number; pv: number }[];
    pv_explicit: number;
    terminal_value: number;
    pv_terminal: number;
    terminal_share: number | null;
    enterprise_value: number;
    net_debt: number;
    equity_value: number;
    value_per_share: number | null;
    price: number | null;
    upside: number | null;
    implied_exit_ev_fcff: number;
    shares_outstanding: number;
    total_debt: number | null;
    cash: number | null;
  };
  sensitivity: { wacc: number[]; terminal_growth: number[]; value_per_share: (number | null)[][] };
  reverse_dcf: {
    implied_growth: number | null;
    years?: number;
    terminal_growth?: number;
    wacc?: number;
    market_cap?: number;
    reason: string | null;
    historical_revenue_cagr?: number | null;
  };
  method: { model: string; years: number; references: string[] };
}
