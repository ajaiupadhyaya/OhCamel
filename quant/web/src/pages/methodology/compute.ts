/**
 * Methodology entries for the engine and the compute tier's kernels (Ship plan P2-7). The
 * products P1-P7 live in ./models.ts (Lane M); these are the engine narrative the Engine page
 * points to (Note → #engine-incremental) and one entry per kernel of contract II.4, each
 * authored from native/kernels/src/*.rs and quant/src/ohcamel_quant/kernels/reference.py.
 * The Compute page's kernel table links to #kernel-<name>.
 */
import type { Model } from "./models";

const T = String.raw;

export type Entry = Omit<Model, "section">;

export interface ExtraSection {
  id: string;
  title: string;
  entries: Entry[];
}

/** The ten functions of contract II.4, in its order (kernels/__init__.py API). */
export const KERNEL_API = [
  "fhs_paths",
  "copula_t_paths",
  "var_es_from_pnl",
  "stationary_bootstrap_means",
  "cscv_pbo",
  "garch_nll",
  "garch_fit",
  "backtest_weights",
  "svi_fit",
  "realized_vol_minute",
] as const;

const COMPUTE = { label: "Compute · Kernels", to: "/compute" };
const RISK = { label: "Risk", to: "/risk" };
const ATLAS = { label: "Risk · Atlas", to: "/risk" };
const LAB = { label: "Strategy Lab", to: "/research" };
const SWEEP = { label: "Strategy Lab · Sweep", to: "/research?tab=sweep" };
const FARM = { label: "Research · Farm", to: "/research?view=farm" };
const OPT_BT = { label: "Optimizer · Backtest", to: "/optimize?tab=backtest" };
const VOL_FC = { label: "Volatility · Forecasts", to: "/options?tab=forecasts" };
const VOL_SURF = { label: "Volatility · Surface", to: "/options?tab=surface" };
const VOL_HIST = { label: "Volatility · History", to: "/options?tab=history" };

const THREADED = "Parallel over rows in fixed 16k chunks: chunk c goes to worker c mod threads, which draws from ChaCha20 seeded by seed on stream = worker index; the same (seed, threads) is bit-identical on any machine.";
const RANDOM_PARITY = "Rust and the NumPy reference draw from different generators (ChaCha20, PCG64), so they agree in distribution; the parity tests check moments to a stated tolerance.";
const EXACT_PARITY = "Deterministic: Rust matches the NumPy reference to 1e-10 relative (parity test).";

export const ENGINE: Entry[] = [
  {
    id: "engine-incremental",
    name: "The live engine: risk as an incremental dependency graph",
    summary:
      "The OCaml engine takes a book (positions, cash, limits) and a price feed and keeps exposure, VaR and ES, beta to a macro factor, drawdown and limit breaches current. It builds the computation once as a graph with Jane Street's Incremental: a tick sets one input node, and stabilize re-runs exactly the nodes downstream of it, in dependency order, then hands every observer one consistent snapshot. A tick reaches about 25 nodes whether the book holds 10 names or 400.",
    formulas: [
      { tex: T`\text{VaR}_\alpha = z_\alpha \sqrt{w^\top \Sigma w},\qquad \text{CVaR}_i = w_i \frac{(\Sigma w)_i}{\sqrt{w^\top \Sigma w}}\, z_\alpha,\qquad \sum_i \text{CVaR}_i = \text{VaR}_\alpha`, caption: "the quantities at the end of the graph: parametric VaR and its Euler split" },
    ],
    assumptions: [
      "Cost follows the change, not the book: a tick touches one name's chain and the book-level aggregates; other names' windows, marks and exposures are reused.",
      "Observers read only after stabilize finishes, so VaR, exposures and limits describe the same instant.",
      "Cut-offs: when a recomputed node's value is unchanged, propagation stops there.",
      "The graph on the Engine page is an illustration for three symbols; the live engine has one chain per holding.",
      "The benchmark (make bench: about 0.5 ms per tick at 400 names against 53 ms to recompute everything) was measured on an M2 Pro laptop, not the droplet.",
      "The site reads the engine through a GET-only bridge (health, ops, snapshot, history; 2 s cache). No order or desk route is reachable through it.",
    ],
    appears: [{ label: "Live Engine", to: "/engine" }, { label: "Flight Deck", to: "/deck" }],
    refs: ["minsky_2015", "acar_2005", "tasche_2000"],
    keywords: "ocaml incremental stabilize dependency graph engine real time bridge",
  },
];

export const KERNELS: Entry[] = [
  {
    id: "kernel-dispatch",
    name: "Kernel dispatch: Rust with a NumPy reference",
    summary:
      "Every heavy loop has two implementations with one signature: Rust in the ohcamel_kernels extension (PyO3, rayon; native/kernels) and a NumPy reference (kernels/reference.py). The dispatcher uses Rust when the wheel imports, the reference when OHCAMEL_QUANT_KERNELS=python, and refuses to start when it is set to rust and the wheel is missing or stale. Each result records the engine that actually ran in its provenance.",
    formulas: [],
    assumptions: [
      "Inputs are float64 C-contiguous arrays; the GIL is released around the Rust core; a Rust error becomes a ValueError.",
      "Simulation output is capped at 10M values (80 MB) and horizons at 2,520 sessions.",
      "Benchmarks are medians in milliseconds; only rows measured on the droplet's CPU class speak for production. Where Rust is slower than the reference, the table says so.",
    ],
    appears: [COMPUTE],
    refs: [],
    keywords: "rust pyo3 numpy reference dispatcher engine provenance wheel",
  },
  {
    id: "kernel-fhs_paths",
    name: "fhs_paths — filtered historical simulation",
    summary:
      "Simulates h-day portfolio P&L by drawing whole rows of standardized residuals, so the cross-section keeps its empirical dependence, and scaling each asset's draw by its own GJR-GARCH(1,1) volatility, which is updated with the drawn shock day by day.",
    formulas: [
      { tex: T`e_i = \sigma_i z_{j,i},\quad r_i = (\mu_i + e_i)/100,\quad \sigma_i^2 \leftarrow \omega_i + (\alpha_i + \gamma_i \mathbf{1}[e_i<0])\,e_i^2 + \beta_i \sigma_i^2`, caption: "one day of one path; parameters in percent units" },
      { tex: T`\text{P\&L}_h = \prod_{k=1}^{h}\Big(1 + \sum_i w_i r_{k,i}\Big) - 1` },
    ],
    assumptions: ["Rebalanced daily to w; a day at or below −100% is a total loss and the path's P&L is −1.", THREADED, RANDOM_PARITY],
    appears: [ATLAS, RISK, COMPUTE],
    refs: ["barone_adesi_1999", "glosten_1993"],
    keywords: "fhs filtered historical simulation garch monte carlo paths",
  },
  {
    id: "kernel-copula_t_paths",
    name: "copula_t_paths — Student-t copula with empirical marginals",
    summary:
      "Simulates h-day portfolio P&L from a Student-t copula: correlation from Kendall's tau, a multivariate t draw mapped to uniforms, and each asset's return read off its own empirical quantile, so marginals are historical and the dependence has tail co-movement.",
    formulas: [
      { tex: T`R_{ij} = \sin(\pi \tau_{ij}/2),\quad T = Z/\sqrt{W},\ Z \sim N(0,R),\ W \sim \chi^2_\nu/\nu,\quad U_i = t_\nu(T_i)`, caption: "Kendall's tau holds for every elliptical copula" },
      { tex: T`r_i = x_{i,(\lceil U_i n \rceil)}`, caption: "empirical quantile, clamped to 1..n" },
    ],
    assumptions: ["Rebalanced daily to w; the h-day P&L compounds as in fhs_paths.", "The t CDF uses the regularized incomplete beta by Lentz's continued fraction.", THREADED, RANDOM_PARITY],
    appears: [ATLAS, COMPUTE],
    refs: ["lindskog_2003", "mcneil_2015"],
    keywords: "t copula kendall tau empirical marginals monte carlo",
  },
  {
    id: "kernel-var_es_from_pnl",
    name: "var_es_from_pnl — historical VaR and ES of a P&L sample",
    summary: "VaR and ES as positive loss fractions of a simulated or historical P&L sample, with the same tail-count rule as every other VaR in the platform.",
    formulas: [{ tex: T`k = \max\!\big(1, \lceil n(1-\alpha) - 10^{-9}\rceil\big),\quad \text{VaR} = L_{(k)},\quad \text{ES} = \tfrac{1}{k}\sum_{j\le k} L_{(j)}`, caption: "the 1e-9 keeps 500 × 0.05 from rounding up" }],
    assumptions: ["Losses are −P&L; non-finite values are dropped.", "Single-threaded.", EXACT_PARITY],
    appears: [ATLAS, RISK, COMPUTE],
    refs: ["acerbi_tasche_2002", "jorion_2007"],
    keywords: "var es tail count historical",
  },
  {
    id: "kernel-stationary_bootstrap_means",
    name: "stationary_bootstrap_means — the stationary bootstrap",
    summary:
      "Column means of stationary-bootstrap resamples: each replication walks n indices, starting a new block at a uniform index with probability 1/mean_block and otherwise continuing circularly. One index stream serves every column, so passing (x, x²) gives a replication's mean and variance from one draw — how the bootstrap Sharpe uses it.",
    formulas: [{ tex: T`P(\text{new block}) = p = 1/b,\quad \mathbb{E}[\text{block length}] = b` }],
    assumptions: ["The index stream depends only on (seed, threads, n, b, reps), never on the values.", THREADED, RANDOM_PARITY],
    appears: [LAB, FARM, COMPUTE],
    refs: ["politis_romano_1994", "politis_white_2004"],
    keywords: "stationary bootstrap block resample sharpe confidence",
  },
  {
    id: "kernel-cscv_pbo",
    name: "cscv_pbo — probability of backtest overfitting by CSCV",
    summary:
      "Splits a T × N matrix of trial returns into S contiguous blocks, takes every split into S/2 in-sample and S/2 out-of-sample blocks, picks the in-sample best trial and records its out-of-sample rank. PBO is the share of splits where the in-sample winner lands at or below the out-of-sample median.",
    formulas: [
      { tex: T`n^\ast = \arg\max_n \text{SR}^{IS}_n,\quad w = \frac{\operatorname{rank}^{OOS}(n^\ast)}{N+1},\quad \lambda = \ln\frac{w}{1-w}`, caption: "ties averaged; non-finite Sharpe ranks lowest" },
      { tex: T`\text{PBO} = \frac{\#\{\lambda \le 0\}}{\binom{S}{S/2}}` },
    ],
    assumptions: ["Leading rows are dropped so S divides T; combinations in lexicographic order.", "A trial with (relatively) zero variance in a block has an undefined Sharpe there.", "Also returns each split's winner and its in- and out-of-sample Sharpe, for the degradation regression.", "Parallel over combinations.", EXACT_PARITY],
    appears: [SWEEP, FARM, COMPUTE],
    refs: ["bailey_2017"],
    keywords: "pbo cscv overfitting combinatorially symmetric cross validation",
  },
  {
    id: "kernel-garch_nll",
    name: "garch_nll — Student-t (GJR-/E)GARCH(1,1) likelihood",
    summary: "The negative log-likelihood of GARCH, GJR-GARCH or EGARCH(1,1) with unit-variance Student-t innovations, initialised by arch's exponentially weighted backcast.",
    formulas: [
      { tex: T`s_t = \omega + (\alpha + \gamma\mathbf{1}[e_{t-1}<0])\,e_{t-1}^2 + \beta s_{t-1}`, caption: "GJR; γ = 0 is GARCH" },
      { tex: T`\text{NLL} = -\sum_t \Big[\ln\Gamma\!\big(\tfrac{\nu+1}{2}\big) - \ln\Gamma\!\big(\tfrac{\nu}{2}\big) - \tfrac12\ln(\pi(\nu-2)s_t) - \tfrac{\nu+1}{2}\ln\!\Big(1 + \frac{e_t^2}{(\nu-2)s_t}\Big)\Big]` },
    ],
    assumptions: ["Parameters in the units of r (callers pass percent returns, as arch does).", "Backcast: Σ wᵢeᵢ² / Σ wᵢ over the first min(75, n) residuals, wᵢ = 0.94ⁱ.", "Single-threaded.", EXACT_PARITY],
    appears: [RISK, VOL_FC, COMPUTE],
    refs: ["bollerslev_1986", "glosten_1993"],
    keywords: "garch gjr egarch likelihood student t",
  },
  {
    id: "kernel-garch_fit",
    name: "garch_fit — fitting (GJR-/E)GARCH(1,1)",
    summary: "Minimises the mean Student-t NLL inside the stationarity box by Nelder–Mead (a step-for-step port of scipy's), restarting from the best point until a restart improves by no more than 1e-12; an infeasible point scores 1e10.",
    formulas: [{ tex: T`\alpha + \gamma/2 + \beta < 1,\quad \nu > 2`, caption: "GJR stationarity and a finite t variance" }],
    assumptions: ["The NumPy reference fits GARCH and GJR by SLSQP with an analytic gradient; the Rust fit must match its parameters within 1e-4 at a likelihood at least as good, with the same next-day variance. For EGARCH (multimodal) both engines run the same Nelder–Mead and agree to 1e-6 in NLL.", "On the recorded benchmark the reference's SLSQP is faster than Rust's Nelder–Mead; the Compute table shows it.", "Single-threaded."],
    appears: [RISK, VOL_FC, COMPUTE],
    refs: ["bollerslev_1986", "glosten_1993", "nelder_mead_1965"],
    keywords: "garch fit nelder mead optimisation",
  },
  {
    id: "kernel-backtest_weights",
    name: "backtest_weights — the backtester's accounting loop",
    summary: "The inner loop of every weights backtest: daily drift of the held weights, borrow on short notional, and proportional costs on turnover at each execution session.",
    formulas: [
      { tex: T`g_s = w^\top R_s + (1 - \textstyle\sum w)\,rf_s - \tfrac{b}{10^4 \cdot 252}\cdot\tfrac{\sum|w| - \sum w}{2},\qquad w \leftarrow \frac{w + w \odot R_s}{1 + g_s}`, caption: "gross return net of borrow, then drift" },
      { tex: T`1 + \text{net}_s = (1 + g_s)\big(1 - \tfrac{c}{10^4}\lVert w^\ast - w\rVert_1\big),\quad w \leftarrow w^\ast`, caption: "at an execution session" },
    ],
    assumptions: ["Before the first execution the book is cash and earns rf.", "A session with 1 + g ≤ 0 wipes the book out: net = −1 and the loop stops.", "The execution lag is applied by the caller.", "Single-threaded.", EXACT_PARITY],
    appears: [LAB, OPT_BT, FARM, COMPUTE],
    refs: [],
    keywords: "backtest accounting turnover costs borrow drift",
  },
  {
    id: "kernel-svi_fit",
    name: "svi_fit — raw SVI calibration",
    summary: "Fits raw SVI to one smile by Zeliade's quasi-explicit method: for fixed (m, σ) the total variance is linear in (a, u, v) and the box-constrained weighted least squares is solved exactly; the outer (m, ln σ) search is a 9 × 7 grid, then Nelder–Mead from the three best points.",
    formulas: [
      { tex: T`w(k) = a + b\big(\rho(k-m) + \sqrt{(k-m)^2 + \sigma^2}\big)` },
      { tex: T`y = \tfrac{k-m}{\sigma},\quad w = a + \tfrac{u}{2}\big(y + \sqrt{y^2+1}\big) + \tfrac{v}{2}\big(\sqrt{y^2+1} - y\big)`, caption: "the linear inner problem, 0 ≤ u, v ≤ 4σ" },
    ],
    assumptions: ["b = c/σ and ρ = d/c, clipped to ±0.999999.", "Single-threaded.", EXACT_PARITY],
    appears: [VOL_SURF, VOL_HIST, COMPUTE],
    refs: ["zeliade_2009", "gatheral_2004"],
    keywords: "svi smile calibration quasi explicit zeliade",
  },
  {
    id: "kernel-realized_vol_minute",
    name: "realized_vol_minute — daily realized variance from minute bars",
    summary: "For each session [open, close), the sum of squared log returns between consecutive one-minute prices inside it. The overnight move is never counted; a session with fewer than two prices is missing.",
    formulas: [{ tex: T`RV_d = \sum_{t \in d} \big(\ln P_t - \ln P_{t-1}\big)^2` }],
    assumptions: ["Timestamps are nanosecond epochs; sessions are half-open.", "Take the square root for realized volatility.", "Single-threaded.", EXACT_PARITY],
    appears: [VOL_FC, COMPUTE],
    refs: ["andersen_bollerslev_1998"],
    keywords: "realized variance minute bars intraday har",
  },
];

export const EXTRA_SECTIONS: ExtraSection[] = [
  { id: "engine", title: "Engine", entries: ENGINE },
  { id: "kernels", title: "Kernels", entries: KERNELS },
];
