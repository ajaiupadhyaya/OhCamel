/**
 * Portfolio Lab metric & model explanations (one plain-English sentence, formula, paper).
 * Kept page-local; the shared glossary (lib/glossary.ts) covers the common ones
 * (sharpe, sortino, vol, cagr, var, es, beta, max_drawdown).
 */
import type { Info } from "../../lib/glossary";

export const INFO = {
  sharpe_se: {
    title: "Sharpe ratio ± standard error",
    text: "The Sharpe ratio is itself an estimate from a finite sample. Lo's standard error — corrected for fat tails and autocorrelation (HAC) — says how far the true value could plausibly be from the one measured.",
    formula: "\\widehat{SE}(\\widehat{SR}) \\approx \\sqrt{\\tfrac{1}{T}\\left(1 + \\tfrac{1}{2}\\widehat{SR}^2 - \\gamma_3 \\widehat{SR} + \\tfrac{\\gamma_4 - 3}{4}\\widehat{SR}^2\\right)}",
    reference: "Lo (2002), “The Statistics of Sharpe Ratios”, Financial Analysts Journal 58(4); Mertens (2002)",
  },
  alpha: {
    title: "Jensen's alpha",
    text: "The part of the return not explained by exposure to the benchmark — the intercept of a regression of portfolio on benchmark returns, annualized. Its t-stat uses Newey–West errors.",
    formula: "r_{p,t} - r_{f,t} = \\alpha + \\beta\\,(r_{b,t} - r_{f,t}) + \\varepsilon_t",
    reference: "Jensen (1968), Journal of Finance 23(2); Newey & West (1987), Econometrica 55(3)",
  },
  information_ratio: {
    title: "Information ratio",
    text: "Active return over the benchmark divided by tracking error: how consistently the portfolio beats (or lags) its benchmark per unit of deviation.",
    formula: "IR = \\frac{\\overline{r_p - r_b}}{\\sigma(r_p - r_b)}\\sqrt{252}",
    reference: "Grinold & Kahn (2000), Active Portfolio Management, 2nd ed.",
  },
  tracking_error: {
    title: "Tracking error",
    text: "The annualized volatility of the difference between portfolio and benchmark returns — how far the portfolio wanders from the benchmark.",
    formula: "TE = \\sigma(r_p - r_b)\\sqrt{252}",
  },
  calmar: {
    title: "Calmar ratio",
    text: "Compound annual growth divided by the worst drawdown: return earned per unit of worst-case pain.",
    formula: "\\text{Calmar} = \\frac{\\text{CAGR}}{|\\text{MDD}|}",
    reference: "Young (1991), Futures magazine",
  },
  omega: {
    title: "Omega ratio",
    text: "Probability-weighted gains above a threshold divided by probability-weighted losses below it; uses the whole distribution, not just mean and variance.",
    formula: "\\Omega(\\tau) = \\frac{\\mathbb{E}[(r - \\tau)^+]}{\\mathbb{E}[(\\tau - r)^+]}",
    reference: "Keating & Shadwick (2002), Journal of Performance Measurement 6(3)",
  },
  m2: {
    title: "M² (Modigliani risk-adjusted return)",
    text: "The return the portfolio would have earned if levered or de-levered to the benchmark's volatility — Sharpe ratio expressed in percent.",
    formula: "M^2 = r_f + SR_p\\,\\sigma_b",
    reference: "Modigliani & Modigliani (1997), Journal of Portfolio Management 23(2)",
  },
  treynor: {
    title: "Treynor ratio",
    text: "Excess return per unit of beta — reward for market (systematic) risk rather than total risk.",
    formula: "T = \\frac{\\bar r_p - r_f}{\\beta_p}",
    reference: "Treynor (1965), Harvard Business Review 43",
  },
  capture: {
    title: "Up / down capture",
    text: "Share of the benchmark's return captured in months when it rose (up) and fell (down). Up above down is the asymmetric profile investors want.",
    formula: "\\text{Up} = \\frac{\\prod_{m: r_b>0}(1+r_{p,m})^{1/n}-1}{\\prod_{m: r_b>0}(1+r_{b,m})^{1/n}-1}",
    reference: "Morningstar capture-ratio methodology",
  },
  ulcer: {
    title: "Ulcer index",
    text: "Root-mean-square drawdown: penalizes both how deep and how long the portfolio stays under water.",
    formula: "UI = \\sqrt{\\tfrac{1}{T}\\sum_t DD_t^2}",
    reference: "Martin & McCann (1989), The Investor's Guide to Fidelity Funds",
  },
  psr: {
    title: "Probabilistic Sharpe ratio",
    text: "The probability that the true Sharpe ratio is above a hurdle (default 0), given the track-record length, skewness and fat tails of the returns.",
    formula: "PSR(SR^*) = \\Phi\\!\\left(\\frac{(\\widehat{SR} - SR^*)\\sqrt{T-1}}{\\sqrt{1 - \\gamma_3 \\widehat{SR} + \\frac{\\gamma_4 - 1}{4}\\widehat{SR}^2}}\\right)",
    reference: "Bailey & López de Prado (2012), “The Sharpe Ratio Efficient Frontier”, Journal of Risk 15(2)",
  },
  mintrl: {
    title: "Minimum track record length",
    text: "How many years of returns like these you need before you can say, at the chosen confidence, that the true Sharpe beats the hurdle.",
    formula: "MinTRL = 1 + \\left(1 - \\gamma_3 \\widehat{SR} + \\tfrac{\\gamma_4 - 1}{4}\\widehat{SR}^2\\right)\\left(\\frac{z_{\\alpha}}{\\widehat{SR} - SR^*}\\right)^2",
    reference: "Bailey & López de Prado (2012), Journal of Risk 15(2)",
  },
  turnover: {
    title: "Turnover",
    text: "The one-way fraction of the portfolio traded to reset drifted weights back to target, summed over a year.",
    formula: "\\tau = \\tfrac12\\sum_i |w_i^{target} - w_i^{drift}|",
  },
  skew: { title: "Skewness", text: "Asymmetry of daily returns. Negative means large losses are more common than equally large gains." },
  kurtosis: { title: "Excess kurtosis", text: "Fatness of the tails relative to a normal distribution (0). Higher values mean extreme days happen far more often than a bell curve predicts." },
  jb: {
    title: "Jarque–Bera test",
    text: "Tests whether skewness and kurtosis are consistent with a normal distribution; a tiny p-value rejects normality.",
    formula: "JB = \\tfrac{T}{6}\\left(S^2 + \\tfrac{(K-3)^2}{4}\\right)",
    reference: "Jarque & Bera (1980), Economics Letters 6(3)",
  },
  // ---------------------------------------------------------------- VaR models
  m_historical: {
    title: "Historical simulation",
    text: "Replays every past day's portfolio return and reads the loss quantile directly — no distributional assumption, but only as good as the history it sees.",
    formula: "\\text{VaR}_\\alpha = L_{(k)},\\; k = \\lceil n(1-\\alpha) \\rceil",
    reference: "Jorion (2007), Value at Risk, 3rd ed.; Acerbi & Tasche (2002)",
  },
  m_gaussian: {
    title: "Gaussian (variance–covariance)",
    text: "Assumes returns are normal with the sample mean and volatility. Simple, but understates tail losses when returns are fat-tailed.",
    formula: "\\text{VaR}_\\alpha = -\\mu + \\sigma z_\\alpha,\\quad \\text{ES}_\\alpha = -\\mu + \\sigma\\frac{\\phi(z_\\alpha)}{1-\\alpha}",
    reference: "J.P. Morgan/Reuters (1996), RiskMetrics Technical Document",
  },
  m_student_t: {
    title: "Student-t",
    text: "Fits a fat-tailed t distribution (degrees of freedom by maximum likelihood) scaled to the sample volatility.",
    formula: "\\text{VaR}_\\alpha = -\\mu + \\sigma\\sqrt{\\tfrac{\\nu-2}{\\nu}}\\,t_{\\nu}^{-1}(\\alpha)",
    reference: "McNeil, Frey & Embrechts (2015), Quantitative Risk Management, 2nd ed.",
  },
  m_cornish_fisher: {
    title: "Cornish–Fisher (modified VaR)",
    text: "Adjusts the normal quantile for skewness and kurtosis. Breaks down when kurtosis is very high — flagged when outside its domain of validity.",
    formula: "z_{cf} = z + \\tfrac{(z^2-1)S}{6} + \\tfrac{(z^3-3z)K}{24} - \\tfrac{(2z^3-5z)S^2}{36}",
    reference: "Zangari (1996); Favre & Galeano (2002); Maillard (2012)",
  },
  m_ewma: {
    title: "EWMA (RiskMetrics)",
    text: "Normal VaR with volatility that weights recent days more heavily (λ = 0.94), so it reacts quickly to calm or stormy markets.",
    formula: "\\sigma_{t+1}^2 = \\lambda \\sigma_t^2 + (1-\\lambda) r_t^2",
    reference: "J.P. Morgan/Reuters (1996), RiskMetrics Technical Document",
  },
  m_garch: {
    title: "GARCH(1,1)-t",
    text: "Volatility that clusters and mean-reverts to a long-run level, with Student-t shocks; tomorrow's VaR uses tomorrow's forecast volatility.",
    formula: "\\sigma_t^2 = \\omega + \\alpha\\, \\varepsilon_{t-1}^2 + \\beta\\, \\sigma_{t-1}^2",
    reference: "Bollerslev (1986), Journal of Econometrics 31; Bollerslev (1987), REStat 69",
  },
  m_gjr_garch: {
    title: "GJR-GARCH(1,1,1)-t",
    text: "GARCH with a leverage term: bad days raise future volatility more than good days of the same size.",
    formula: "\\sigma_t^2 = \\omega + (\\alpha + \\gamma\\,\\mathbb{1}_{\\varepsilon_{t-1}<0})\\,\\varepsilon_{t-1}^2 + \\beta\\,\\sigma_{t-1}^2",
    reference: "Glosten, Jagannathan & Runkle (1993), Journal of Finance 48",
  },
  m_fhs: {
    title: "Filtered historical simulation",
    text: "Bootstraps the portfolio's own standardized GJR-GARCH residuals and rescales them by today's volatility — history's shapes, today's scale.",
    formula: "r_{T+1}^{(b)} = \\hat\\sigma_{T+1}\\, \\hat z^{(b)},\\; \\hat z_t = \\hat\\varepsilon_t/\\hat\\sigma_t",
    reference: "Barone-Adesi, Giannopoulos & Vosper (1999), J. Futures Markets 19; Hull & White (1998)",
  },
  m_evt_pot: {
    title: "Extreme value theory (peaks over threshold)",
    text: "Fits a generalized Pareto distribution to only the losses beyond the 90th percentile, modelling the tail itself rather than the whole distribution.",
    formula: "\\text{VaR}_\\alpha = u + \\tfrac{\\beta}{\\xi}\\left[\\left(\\tfrac{n}{N_u}(1-\\alpha)\\right)^{-\\xi} - 1\\right]",
    reference: "McNeil & Frey (2000), J. Empirical Finance 7; Smith (1987), Annals of Statistics 15",
  },
  // ---------------------------------------------------------------- backtests
  kupiec: {
    title: "Kupiec POF test",
    text: "Checks whether the number of VaR breaches matches what the confidence level promises (e.g. 1% of days at 99%). A p-value below 5% rejects the model.",
    formula: "LR_{POF} = -2\\ln\\frac{(1-p)^{T-x}p^x}{(1-\\hat\\pi)^{T-x}\\hat\\pi^x} \\sim \\chi^2_1",
    reference: "Kupiec (1995), Journal of Derivatives 3(2)",
  },
  christoffersen: {
    title: "Christoffersen independence test",
    text: "Checks whether breaches cluster — a breach today should not make one tomorrow more likely. Clustering means the model reacts too slowly.",
    formula: "LR_{ind} = -2\\ln\\frac{(1-\\hat\\pi)^{n_{00}+n_{10}}\\hat\\pi^{n_{01}+n_{11}}}{(1-\\hat\\pi_{01})^{n_{00}}\\hat\\pi_{01}^{n_{01}}(1-\\hat\\pi_{11})^{n_{10}}\\hat\\pi_{11}^{n_{11}}}",
    reference: "Christoffersen (1998), International Economic Review 39(4)",
  },
  cc: {
    title: "Conditional coverage",
    text: "Joint test of the right number of breaches and no clustering (Kupiec + independence).",
    formula: "LR_{cc} = LR_{POF} + LR_{ind} \\sim \\chi^2_2",
    reference: "Christoffersen (1998)",
  },
  dq: {
    title: "Dynamic quantile test",
    text: "Regresses breach indicators on their own lags and the VaR itself; any predictability means the VaR is mis-specified.",
    formula: "Hit_t = \\mathbb{1}(r_t < -\\text{VaR}_t) - (1-\\alpha),\\; DQ \\sim \\chi^2_6",
    reference: "Engle & Manganelli (2004), J. Business & Economic Statistics 22(4)",
  },
  traffic_light: {
    title: "Basel traffic light",
    text: "The regulator's zone for a 99% VaR model, from the binomial probability of seeing this many breaches: green is fine, yellow raises the capital multiplier, red means the model is rejected.",
    formula: "\\text{zone} = \\begin{cases}\\text{green} & F(x) < 95\\% \\\\ \\text{yellow} & F(x) < 99.99\\% \\\\ \\text{red} & \\text{otherwise}\\end{cases}",
    reference: "Basel Committee (1996), Supervisory framework for the use of backtesting",
  },
  z2: {
    title: "Acerbi–Szekely Z₂",
    text: "Backtests Expected Shortfall: compares realized losses on breach days with the ES forecast. Values well below 0 mean ES is too small.",
    formula: "Z_2 = \\sum_t \\frac{r_t\\,\\mathbb{1}_t}{T(1-\\alpha)\\,\\text{ES}_t} + 1",
    reference: "Acerbi & Szekely (2014), Risk, December",
  },
  fz0: {
    title: "FZ0 loss",
    text: "A scoring rule that jointly rewards accurate VaR and ES; lower is better. Used here to rank models.",
    reference: "Patton, Ziegel & Chen (2019), Journal of Econometrics 211(2)",
  },
  // ---------------------------------------------------------------- decomposition
  component_var: {
    title: "Component (Euler) VaR",
    text: "Each position's share of total VaR. Components add up exactly to portfolio VaR, so they show where the risk really sits — not where the money sits.",
    formula: "CVaR_i = w_i \\frac{\\partial \\text{VaR}}{\\partial w_i},\\quad \\sum_i CVaR_i = \\text{VaR}",
    reference: "Tasche (2000); Jorion (2007) ch. 7",
  },
  marginal_var: {
    title: "Marginal VaR",
    text: "How much portfolio VaR changes per unit of extra weight in the position.",
    formula: "MVaR_i = \\frac{\\partial \\text{VaR}}{\\partial w_i} = z_\\alpha \\frac{(\\Sigma w)_i}{\\sigma_p}",
  },
  incremental_var: {
    title: "Incremental VaR",
    text: "How much portfolio VaR would fall if you removed the position entirely (a full revaluation, not a derivative).",
    formula: "IVaR_i = \\text{VaR}(w) - \\text{VaR}(w_{-i})",
  },
  diversification_ratio: {
    title: "Diversification ratio",
    text: "Weighted-average stand-alone volatility divided by portfolio volatility. 1 means no diversification; higher means correlations are doing work.",
    formula: "DR = \\frac{\\sum_i |w_i|\\sigma_i}{\\sigma_p}",
    reference: "Choueifaty & Coignard (2008), Journal of Portfolio Management 35(1)",
  },
  correlation: {
    title: "Correlation",
    text: "Pearson correlation of daily returns: +1 moves in lockstep, 0 unrelated, −1 opposite.",
    formula: "\\rho_{ij} = \\frac{\\operatorname{Cov}(r_i, r_j)}{\\sigma_i\\sigma_j}",
  },
  // ---------------------------------------------------------------- GARCH
  persistence: {
    title: "Persistence",
    text: "How long a volatility shock lasts. Close to 1 means shocks decay slowly; the half-life is the days until half of a shock has faded.",
    formula: "P = \\alpha + \\tfrac{\\gamma}{2} + \\beta,\\quad h_{1/2} = \\frac{\\ln 0.5}{\\ln P}",
  },
  term_structure: {
    title: "Volatility forecast term structure",
    text: "The model's expected volatility for each future day, converging from today's level to the long-run level at the speed set by persistence.",
    formula: "\\mathbb{E}_T[\\sigma^2_{T+k}] = \\bar\\sigma^2 + P^{k-1}(\\sigma^2_{T+1} - \\bar\\sigma^2)",
    reference: "Engle & Patton (2001), Quantitative Finance 1",
  },
  // ---------------------------------------------------------------- factors
  nw_t: {
    title: "Newey–West t-statistic",
    text: "The loading divided by a standard error that is robust to heteroskedasticity and autocorrelation. |t| above about 2 means the exposure is statistically distinguishable from zero.",
    formula: "t_k = \\hat b_k / \\widehat{SE}_{NW}(\\hat b_k)",
    reference: "Newey & West (1987), Econometrica 55(3)",
  },
  r2: { title: "R²", text: "Share of the portfolio's daily return variance explained by the factors. The rest is idiosyncratic (stock-specific or unmodelled) risk.", formula: "R^2 = 1 - \\frac{\\operatorname{Var}(\\hat\\varepsilon)}{\\operatorname{Var}(y)}" },
  factor_alpha: {
    title: "Factor alpha",
    text: "Average excess return left over after accounting for every factor exposure, annualized. Genuine skill (or an unmodelled factor) shows up here.",
    formula: "y_t = \\alpha + \\sum_k b_k f_{k,t} + \\varepsilon_t,\\quad \\alpha_{ann} = 252\\,\\alpha",
  },
  appraisal: { title: "Appraisal ratio", text: "Annualized alpha divided by idiosyncratic volatility: alpha earned per unit of risk you could have diversified away.", formula: "AR = \\alpha_{ann} / \\sigma_\\varepsilon", reference: "Treynor & Black (1973), Journal of Business 46(1)" },
  factor_risk: {
    title: "Factor risk decomposition",
    text: "Splits total variance into each factor's contribution (Euler) and the idiosyncratic remainder. Contributions can be negative when a factor hedges another.",
    formula: "\\operatorname{Var}(y) = b^\\top \\Sigma_f b + \\sigma^2_\\varepsilon,\\quad c_k = b_k(\\Sigma_f b)_k",
  },
  carino: {
    title: "Return attribution (Cariño linking)",
    text: "Splits the compounded excess return into what each factor exposure, the alpha and the residual contributed; log-linking makes the pieces add up exactly.",
    formula: "R = \\sum_k \\sum_t \\frac{k_t}{K} b_k f_{k,t} + \\ldots,\\quad k_t = \\frac{\\ln(1+r_t)}{r_t}",
    reference: "Cariño (1999), Journal of Performance Measurement 4(1)",
  },
  // ---------------------------------------------------------------- stress
  hist_stress: {
    title: "Historical scenario replay",
    text: "Applies the actual cumulative returns each holding earned over a dated crisis window to today's weights (buy-and-hold from the base close). Names with no prices then are proxied by beta × benchmark and flagged.",
    formula: "R_s = \\sum_i w_i \\left(\\frac{P_{i,end}}{P_{i,base}} - 1\\right)",
    reference: "BCBS (2009), Principles for sound stress testing; Jorion (2007) ch. 14",
  },
  cond_stress: {
    title: "Conditional stress test",
    text: "You fix moves for some assets; every other asset moves by its expected return given those moves, estimated from the covariance matrix.",
    formula: "\\mathbb{E}[r_o \\mid r_s = s] = \\Sigma_{os}\\Sigma_{ss}^{-1}s",
    reference: "Kupiec (1998), “Stress testing in a value at risk framework”, Journal of Derivatives 6(1)",
  },
} satisfies Record<string, Info>;

export const MODEL_LABEL: Record<string, string> = {
  historical: "Historical",
  gaussian: "Gaussian",
  student_t: "Student-t",
  cornish_fisher: "Cornish–Fisher",
  ewma: "EWMA",
  garch: "GARCH-t",
  gjr_garch: "GJR-GARCH-t",
  fhs: "Filtered HS",
  evt_pot: "EVT (POT)",
};

/** Info for a VaR model key (falls back to plain text). */
export function modelInfo(model: string): Info {
  return (INFO as Record<string, Info>)[`m_${model}`] ?? { text: model };
}

export const modelLabel = (m: string) => MODEL_LABEL[m] ?? m;

/** Models whose volatility is conditional on the recent past. */
export const CONDITIONAL_MODELS = ["ewma", "garch", "gjr_garch", "fhs"];
/** Unconditional, fat-tail aware models. */
export const FAT_TAIL_MODELS = ["historical", "student_t", "evt_pot"];
