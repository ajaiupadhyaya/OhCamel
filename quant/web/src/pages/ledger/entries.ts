/**
 * The Ledger: what exists, what was cut, what is advisory, known limits, and what the owner
 * holds. Plain data; re-dated at launch (Lane H). Cut items say what they would have been.
 */
export type LedgerSection = "BUILT" | "CUT" | "ADVISORY" | "LIMITS" | "OWNER";

export interface LedgerEntry {
  section: LedgerSection;
  item: string;
  detail: string;
}

export const LEDGER_AS_OF = "2026-10-05";

export const SECTION_ORDER: LedgerSection[] = ["BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER"];

export const LEDGER: LedgerEntry[] = [
  // ---------------------------------------------------------------- built
  { section: "BUILT", item: "FRONT", detail: "Front page: index figures, UST curve and 2s10s, product headlines, last night's compute, data freshness." },
  { section: "BUILT", item: "MKTS", detail: "Markets: indices, sectors, rates, credit, commodities, volatility, cross-asset correlation." },
  { section: "BUILT", item: "RISK", detail: "Portfolio VaR/ES across nine models, Euler decomposition, VaR backtests, GARCH; Monte Carlo atlas." },
  { section: "BUILT", item: "PORT", detail: "Portfolio Lab: holdings, performance, factors, stress, covariance league." },
  { section: "BUILT", item: "OPT", detail: "Optimizer: mean-variance, risk parity, HRP, Black–Litterman, constraints, walk-forward." },
  { section: "BUILT", item: "RSCH", detail: "Strategy Lab: literature backtests with PBO and deflated Sharpe; strategy farm; models." },
  { section: "BUILT", item: "VOL", detail: "Volatility: chains, smiles, SVI surface, risk-neutral densities, payoffs; forecast league; surface history." },
  { section: "BUILT", item: "RATES", detail: "Rates & Macro: par curve and fits, history and PCA, recession probit, policy rules, regimes." },
  { section: "BUILT", item: "CO", detail: "Company: SEC statements, ratios, quality scores, DCF." },
  { section: "BUILT", item: "GP", detail: "Ticker: price history, volume, return statistics, realized volatility." },
  { section: "BUILT", item: "DECK", detail: "Flight Deck: the paper book's limits, risk and feeds as live instruments." },
  { section: "BUILT", item: "SYS", detail: "System index: Compute, Engine, Methodology, Ledger." },
  { section: "BUILT", item: "P1 RISK ATLAS", detail: "Nightly Monte Carlo VaR/ES, FHS and t-copula, 1D and 10D, Euler split." },
  { section: "BUILT", item: "P2 VOL FORECAST LEAGUE", detail: "GARCH, GJR, EGARCH, HAR-RV, EWMA scored by QLIKE and MSE; Diebold–Mariano; model confidence set." },
  { section: "BUILT", item: "P3 COVARIANCE LEAGUE", detail: "Estimators ranked by next-month minimum-variance realized volatility; Ledoit–Wolf test." },
  { section: "BUILT", item: "P4 STRATEGY FARM", detail: "Strategies × universes × grids; CSCV PBO, SPA, DSR, cost sensitivity; verdict first." },
  { section: "BUILT", item: "P5 CROSS-SECTIONAL MODEL", detail: "EXP-Q01 LightGBM on purged walk-forward folds; IC, decile spread net of costs, DSR, PBO." },
  { section: "BUILT", item: "P6 REGIME HMM", detail: "EXP-Q02 Gaussian HMM, 2–4 states by BIC; filtered and smoothed probabilities." },
  { section: "BUILT", item: "P7 SURFACE HISTORY", detail: "Nightly SVI: ATM IV, 25-delta risk reversal and butterfly, term slope, model-free variance, VRP." },
  { section: "BUILT", item: "KERNELS", detail: "Rust hot loops via PyO3 with a NumPy reference and parity tests; Rust vs Python benchmarks on /compute." },
  { section: "BUILT", item: "JOBS", detail: "Scheduled job queue on the droplet: immutable artifacts with provenance and data_asof." },
  { section: "BUILT", item: "WAREHOUSE", detail: "DuckDB: daily and minute bars, FRED, factors, option snapshots, SEC facts; ingest log." },
  { section: "BUILT", item: "ENGINE", detail: "OCaml incremental real-time risk engine, as deployed." },
  { section: "BUILT", item: "DESK", detail: "The paper desk: limits and marks on the engine's book. Paper only." },
  // ---------------------------------------------------------------- cut
  { section: "CUT", item: "ENGINE DEPTH", detail: "Finish-plan Stages 2A–6: further OCaml risk depth, desk features and the research battery." },
  { section: "CUT", item: "P8 SCENARIO GRID", detail: "Engine-side scenario grid over the live book." },
  { section: "CUT", item: "C STUBS", detail: "C bindings for the engine's numeric core." },
  { section: "CUT", item: "KIOSK MODE", detail: "A wall-display mode of the Flight Deck." },
  { section: "CUT", item: "SAVED LAYOUTS", detail: "Per-route saved panel layouts." },
  { section: "CUT", item: "DENSITY MODES", detail: "Compact and comfortable table densities." },
  // ---------------------------------------------------------------- advisory
  { section: "ADVISORY", item: "MODEL OUTPUT", detail: "Every model and research result is advisory; none reaches the desk." },
  { section: "ADVISORY", item: "NO LIVE STRATEGY", detail: "No strategy on this site trades. Backtests are not track records." },
  // ---------------------------------------------------------------- limits
  { section: "LIMITS", item: "SURVIVORSHIP", detail: "Universes are current constituents; delisted names are absent." },
  { section: "LIMITS", item: "IEX FEED", detail: "Alpaca IEX is a single venue, not the consolidated tape." },
  { section: "LIMITS", item: "OPTIONS DELAYED", detail: "Option quotes are Cboe delayed data." },
  { section: "LIMITS", item: "SHARED VCPU", detail: "One 2-vCPU droplet; CPU steal from neighbours is measured, not prevented." },
  // ---------------------------------------------------------------- owner
  { section: "OWNER", item: "LIVE TRADING SWITCH", detail: "book.sexp on the droplet; never edited by the agent." },
  { section: "OWNER", item: "PRE-REGISTRATIONS", detail: "EXP-Q01 and EXP-Q02 results publish only after owner approval." },
];
