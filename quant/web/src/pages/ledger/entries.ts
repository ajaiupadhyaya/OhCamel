/**
 * The Ledger: what exists, what was cut, what is advisory, known limits, and what the owner
 * holds. Plain data, dated; re-checked against the code by Lane H (H2). Cut items say what they
 * would have been. A product row names its job kind, and the page reads that kind's latest
 * artifact for the row's state, so the Ledger never claims a run it cannot see.
 */
export type LedgerSection = "BUILT" | "CUT" | "ADVISORY" | "LIMITS" | "OWNER";

export interface LedgerEntry {
  section: LedgerSection;
  item: string;
  detail: string;
  /** Products only: the job kind whose latest artifact gives the row its live state. */
  kind?: string;
  /** Products only: the Methodology entry id. */
  methodology?: string;
}

export const LEDGER_AS_OF = "2026-10-08";

export const SECTION_ORDER: LedgerSection[] = ["BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER"];

export const LEDGER: LedgerEntry[] = [
  // ---------------------------------------------------------------- built
  { section: "BUILT", item: "FRONT", detail: "Front page: index figures, UST curve and 2s10s, regime, atlas, farm and model headlines, last night's compute, data freshness." },
  { section: "BUILT", item: "MKTS", detail: "Markets: indices, sectors, rates, credit, commodities, volatility, cross-asset correlation." },
  { section: "BUILT", item: "RISK", detail: "Portfolio VaR/ES under nine models, Euler decomposition, out-of-sample VaR backtests, GARCH; the Monte Carlo atlas." },
  { section: "BUILT", item: "PORT", detail: "Portfolio Lab: holdings and 13F import, performance, factors, 17 historical stress scenarios, covariance league." },
  { section: "BUILT", item: "OPT", detail: "Optimizer: ten methods incl. mean-variance, risk parity, HRP, HERC, min-CVaR; Black–Litterman; walk-forward against 1/N." },
  { section: "BUILT", item: "RSCH", detail: "Strategy Lab: twelve strategies with PBO, deflated Sharpe, SPA, bootstrap, cost curves; strategy farm; models." },
  { section: "BUILT", item: "VOL", detail: "Volatility: chains, parity forwards, SVI surface, risk-neutral densities, payoffs, realized vol; forecast league; surface history." },
  { section: "BUILT", item: "RATES", detail: "Rates & Macro: par, zero and forward curves, NS/NSS fits, PCA, recession probit, Sahm rule, policy rules, regimes." },
  { section: "BUILT", item: "CO", detail: "Company: SEC statements, ratios, quality scores, FCFF DCF and reverse DCF." },
  { section: "BUILT", item: "GP", detail: "Ticker: price history, volume, return statistics, realized volatility." },
  { section: "BUILT", item: "DECK", detail: "Flight Deck: the reference books' limits, risk and feeds as live instruments; the compute bank." },
  { section: "BUILT", item: "SYS", detail: "System index: Compute, Engine, Methodology, Ledger, each with a live status line." },
  { section: "BUILT", item: "COMPUTE", detail: "Host CPU by slice, steal and memory from hostd; the queue with live progress over SSE; the last 24 h with CPU seconds and peak RSS; the schedule with next runs; the kernel benchmark table." },
  { section: "BUILT", item: "DOCS", detail: "Methodology: every model, P1–P7, the engine, each kernel, data sources, limitations, one bibliography." },
  { section: "BUILT", item: "P1 RISK ATLAS", kind: "risk.mc_atlas", methodology: "p1-mc-atlas", detail: "Nightly 1M-path VaR/ES per reference book, FHS and t-copula, 1D and 10D, Euler split of ES; universe members FHS only; books re-measured every 15 min in session." },
  { section: "BUILT", item: "P2 VOL FORECAST LEAGUE", kind: "vol.forecast_league", methodology: "p2-vol-league", detail: "GARCH, GJR, EGARCH, HAR-RV, EWMA scored by QLIKE and MSE; Diebold–Mariano; model confidence set. Nightly." },
  { section: "BUILT", item: "P3 COVARIANCE LEAGUE", kind: "cov.league", methodology: "p3-cov-league", detail: "Seven estimators ranked weekly by the realized volatility of their minimum-variance portfolio; HAC test against the sample covariance. Descriptive only." },
  { section: "BUILT", item: "P4 STRATEGY FARM", kind: "farm.sweep", methodology: "p4-strategy-farm", detail: "Strategy Lab strategies × universes × grids up to 256, walk-forward; farm-wide DSR, PSR, CSCV PBO, SPA, costs 0/5/15/30 bps; verdict first. Nightly, 90-min budget." },
  { section: "BUILT", item: "P5 CROSS-SECTIONAL MODEL", kind: "models.xs_lgbm", methodology: "p5-exp-q01", detail: "EXP-Q01: LightGBM ETF ranker vs a linear composite, purged walk-forward, 2022 holdout evaluated once; the charter's gates decide. Monthly. Insufficient data until the universe is frozen." },
  { section: "BUILT", item: "P6 REGIME HMM", kind: "regime.hmm", methodology: "p6-exp-q02", detail: "EXP-Q02: Gaussian HMM on four weekly features, 2–4 states by BIC; high-volatility probability vs EWMA for next-month SPY variance. Weekly." },
  { section: "BUILT", item: "P7 SURFACE HISTORY", kind: "vol.surface_history", methodology: "p7-surface-history", detail: "Nightly SVI per snapshot: ATM IV, 25-delta risk reversal and butterfly, term slope, model-free variance, VRP. Grows from the first snapshot." },
  { section: "BUILT", item: "KERNELS", detail: "Ten Rust kernels via PyO3, each with a NumPy reference and parity tests; the image refuses to start without them; Rust vs Python benchmarks on /compute." },
  { section: "BUILT", item: "JOBS", detail: "Queue, worker and scheduler: immutable artifacts with provenance and data_asof; admission by hostd memory; heavy jobs one at a time outside 09:25–16:05 NY; progress over SSE." },
  { section: "BUILT", item: "WAREHOUSE", detail: "DuckDB written only by the worker: daily and minute bars, FRED, factors, option snapshots, SEC facts, 13F holdings; ingest log and freshness." },
  { section: "BUILT", item: "HOSTD", detail: "Rust host telemetry: CPU, steal, iowait, memory and per-slice use every 5 s, one hour kept." },
  { section: "BUILT", item: "HARDENING", detail: "Artifact reads contained to the artifact root; public job kinds pinned; per-client queue and rate limits; SSE caps; CSP measured per route." },
  { section: "BUILT", item: "ENGINE", detail: "OCaml incremental real-time risk engine, as deployed." },
  { section: "BUILT", item: "DESK", detail: "The paper desk: limits and marks on the engine's book. Paper only." },
  // ---------------------------------------------------------------- cut
  { section: "CUT", item: "ENGINE DEPTH", detail: "Finish-plan Stages 2A–6: the long window, GARCH and Cornish–Fisher in the engine, factor and liquidity limits, option marks, self-validation, R8." },
  { section: "CUT", item: "P8 SCENARIO GRID", detail: "Engine-side scenario grid over the live book." },
  { section: "CUT", item: "C STUBS", detail: "C bindings for the engine's numeric core." },
  { section: "CUT", item: "KIOSK MODE", detail: "A wall-display mode of the Flight Deck." },
  { section: "CUT", item: "SAVED LAYOUTS", detail: "Per-route saved panel layouts." },
  { section: "CUT", item: "DENSITY MODES", detail: "Compact and comfortable table densities." },
  // ---------------------------------------------------------------- advisory
  { section: "ADVISORY", item: "MODEL OUTPUT", detail: "Every model and research result is advisory; none reaches the desk." },
  { section: "ADVISORY", item: "NO LIVE STRATEGY", detail: "No strategy on this site trades. Backtests are not track records." },
  { section: "ADVISORY", item: "PRE-REGISTERED ONLY", detail: "P5 and P6 run only as their approved pre-registrations; each holdout is evaluated once." },
  // ---------------------------------------------------------------- limits
  { section: "LIMITS", item: "SURVIVORSHIP", detail: "Universes are current constituents; delisted names are absent." },
  { section: "LIMITS", item: "IEX FEED", detail: "Alpaca IEX is a single venue, not the consolidated tape." },
  { section: "LIMITS", item: "OPTIONS DELAYED", detail: "Option quotes are Cboe delayed data; snapshot history starts on the first night and cannot be re-fetched." },
  { section: "LIMITS", item: "SHARED VCPU", detail: "One 2-vCPU, 4 GB droplet; CPU steal from neighbours is measured, not prevented." },
  { section: "LIMITS", item: "NO WAREHOUSE BACKUP", detail: "The warehouse and the artifacts are not backed up; the warehouse is rebuilt from vendors, option snapshots excepted." },
  { section: "LIMITS", item: "BACKUPS PAUSED", detail: "Nightly backups refuse to run until /var/backups/ohcamel exists." },
  { section: "LIMITS", item: "LIVE HOST BEHIND", detail: "The live host serves 88ebc96 until its deploy; the public host serves 557296a." },
  { section: "LIMITS", item: "KERNEL BENCHMARKS", detail: "The recorded table was measured on a developer laptop, not the droplet's CPU class; two kernels are slower in Rust there." },
  // ---------------------------------------------------------------- owner
  { section: "OWNER", item: "LIVE TRADING SWITCH", detail: "book.sexp on the droplet; never edited by the agent." },
  { section: "OWNER", item: "BACKUP DIRECTORY", detail: "Root: install -d -m 0770 -o 10001 -g ohcamel /var/backups/ohcamel. Unblocks nightly backups and the live deploy." },
  { section: "OWNER", item: "PRE-REGISTRATIONS", detail: "EXP-Q01 and EXP-Q02 approved 2026-10-06; any deviation is a new experiment and goes back to the owner." },
  { section: "OWNER", item: "EXP-Q01 UNIVERSE", detail: "Freeze runs once after the bars backfill. I-Q01-7 dollar-volume sign recorded as −1, the default, 2026-10-07; the owner's to confirm." },
  { section: "OWNER", item: "DROPLET SIZE", detail: "Resize if steal stays above 10 % or the nightly batch misses 09:00 NY." },
  { section: "OWNER", item: "ENGINE BRIDGE", detail: "Whether the public site shows the paper book's positions; off." },
];
