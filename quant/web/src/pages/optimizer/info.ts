/**
 * Plain-English definitions, formulas and references for every number on the Optimizer
 * page. Method / estimator references come from the API catalog when available; the
 * formulas below are the textbook definitions the backend implements.
 */
import type { Info } from "../../lib/glossary";
import type { CovName, LinkageName, MethodName, ReturnsModel } from "./types";

export const INFO = {
  exAnteReturn: {
    title: "Ex-ante expected return",
    text: "What the model expects the portfolio to earn per year: the weighted average of each asset's expected return. It is only as good as those inputs — sample means are very noisy.",
    formula: "\\mathbb{E}[r_p] = \\mu^\\top w",
    reference:
      "Merton (1980), 'On estimating the expected return on the market', JFE 8(4)",
  },
  exAnteVol: {
    title: "Ex-ante volatility",
    text: "The annual volatility the covariance estimate implies for these weights — correlations included, so it is below the weighted average of the assets' own vols.",
    formula: "\\sigma_p = \\sqrt{w^\\top \\Sigma\\, w}",
  },
  exAnteSharpe: {
    title: "Ex-ante Sharpe ratio",
    text: "Expected excess return per unit of expected volatility. Ex-ante Sharpe ratios of optimised portfolios are biased upward: the optimiser loads on the estimation errors that look best.",
    formula: "SR = \\frac{\\mu^\\top w - r_f}{\\sqrt{w^\\top \\Sigma w}}",
    reference:
      "Sharpe (1966), J. Business 39(1); Kan & Smith (2008), 'The distribution of the sample minimum-variance frontier', Mgmt Sci 54(7)",
  },
  riskContribution: {
    title: "Risk contribution",
    text: "The share of portfolio volatility each asset is responsible for, counting its correlation with everything else. The shares add up to 100 %. A 10 % weight can easily be 30 % of the risk.",
    formula:
      "RC_i = \\frac{w_i\\,(\\Sigma w)_i}{\\sqrt{w^\\top \\Sigma w}}, \\quad \\sum_i RC_i = \\sigma_p",
    reference:
      "Euler allocation — Tasche (1999); Maillard, Roncalli & Teiletche (2010), JPM 36(4)",
  },
  enb: {
    title: "Effective number of bets",
    text: "How many independent (uncorrelated) sources of risk the portfolio really holds. Nine assets that all fall together are closer to one bet than nine.",
    formula:
      "ENB = \\exp\\Big(-\\sum_k p_k \\ln p_k\\Big),\\quad p_k = \\frac{(e_k^\\top w)^2 \\lambda_k}{w^\\top\\Sigma w}",
    reference: "Meucci (2009), 'Managing diversification', Risk 22(5)",
  },
  diversificationRatio: {
    title: "Diversification ratio",
    text: "Weighted average asset volatility divided by portfolio volatility. 1 means no diversification benefit; 2 means correlations halve the risk you would otherwise carry.",
    formula: "DR = \\frac{w^\\top \\sigma}{\\sqrt{w^\\top \\Sigma w}}",
    reference:
      "Choueifaty & Coignard (2008), 'Toward maximum diversification', JPM 35(1)",
  },
  effectiveN: {
    title: "Effective N (weights)",
    text: "How many equally-sized positions the weights are equivalent to — a concentration measure that ignores correlation.",
    formula:
      "N_{eff} = \\frac{1}{\\sum_i \\tilde w_i^2},\\quad \\tilde w_i = \\frac{|w_i|}{\\sum_j |w_j|}",
  },
  turnover: {
    title: "Turnover",
    text: "How much of the portfolio has to be traded to move from the current weights to the new ones (one-way, sum of absolute changes).",
    formula: "TO = \\sum_i |w_i - w_{i,0}|",
  },
  cvar: {
    title: "Daily CVaR (expected shortfall)",
    text: "Average daily loss on the worst (1 − α) of historical days, for these weights. Minimum-CVaR minimises exactly this.",
    formula:
      "\\text{CVaR}_\\alpha = \\min_{\\zeta}\\; \\zeta + \\frac{1}{(1-\\alpha)T}\\sum_t \\big(-r_t^\\top w - \\zeta\\big)^+",
    reference: "Rockafellar & Uryasev (2000), J. Risk 2(3)",
  },
  shrinkage: {
    title: "Shrinkage intensity",
    text: "How far the estimator pulls the noisy sample covariance toward a simple, stable target. 0 = raw sample, 1 = all target. It is chosen from the data to minimise expected estimation error.",
    formula: "\\hat\\Sigma = \\delta\\, F + (1-\\delta)\\, S",
    reference: "Ledoit & Wolf (2004), JPM 30(4)",
  },
  condition: {
    title: "Condition number",
    text: "Largest over smallest eigenvalue of the covariance matrix. Big numbers mean the matrix is close to singular, so an optimiser that inverts it will amplify noise into extreme weights.",
    formula: "\\kappa(\\Sigma) = \\lambda_{max} / \\lambda_{min}",
  },
  avgCorr: {
    title: "Average pairwise correlation",
    text: "Mean of the off-diagonal correlations — a one-number summary of how much the universe moves together.",
    formula: "\\bar\\rho = \\frac{1}{N(N-1)}\\sum_{i\\neq j} \\rho_{ij}",
  },
  mp: {
    title: "Marchenko–Pastur bounds",
    text: "If returns were pure noise, the eigenvalues of their sample correlation matrix would fall between λ− and λ+. Eigenvalues above λ+ are structure (signal) the data can actually support.",
    formula:
      "\\lambda_\\pm = \\sigma^2\\left(1 \\pm \\sqrt{N/T}\\right)^2,\\quad q = T/N",
    reference:
      "Marčenko & Pastur (1967); Laloux, Cizeau, Bouchaud & Potters (1999), PRL 83; López de Prado (2020), ML for Asset Managers, ch. 2",
  },
  corrDistance: {
    title: "Correlation distance",
    text: "HRP turns correlations into distances so that assets which move together sit close in the tree. The tree then decides how money is split: first between clusters, then within them.",
    formula: "d_{ij} = \\sqrt{\\tfrac12 (1-\\rho_{ij})}",
    reference:
      "López de Prado (2016), 'Building diversified portfolios that outperform out of sample', JPM 42(4)",
  },
  bisection: {
    title: "Recursive bisection",
    text: "HRP walks down the tree. At each split, the two halves get money in inverse proportion to their variance (α to the left half, 1 − α to the right).",
    formula: "\\alpha = 1 - \\frac{V_L}{V_L + V_R}",
    reference: "López de Prado (2016), JPM 42(4)",
  },
  frontier: {
    title: "Efficient frontier",
    text: "For every target return, the portfolio with the lowest possible volatility under your constraints. Anything below the curve is dominated.",
    formula:
      "\\min_w\\; w^\\top\\Sigma w \\;\\; \\text{s.t.}\\;\\; \\mu^\\top w \\ge R,\\; \\mathbf 1^\\top w = 1,\\; w \\in \\mathcal C",
    reference: "Markowitz (1952), 'Portfolio selection', JF 7(1)",
  },
  cml: {
    title: "Capital market line",
    text: "Mixing the risk-free asset with the tangency (maximum-Sharpe) portfolio gives the best achievable return for each level of risk when you can lend or borrow at the risk-free rate.",
    formula: "\\mathbb{E}[r] = r_f + SR_T\\, \\sigma",
    reference: "Tobin (1958); Sharpe (1964), JF 19(3)",
  },
  blPosterior: {
    title: "Black–Litterman posterior",
    text: "Starts from the returns that would make the prior portfolio optimal (equilibrium), then tilts them toward your views in proportion to your confidence. No views → you get the prior back.",
    formula:
      "\\mu_{BL} = \\big[(\\tau\\Sigma)^{-1} + P^\\top\\Omega^{-1}P\\big]^{-1}\\big[(\\tau\\Sigma)^{-1}\\Pi + P^\\top\\Omega^{-1}Q\\big],\\;\\; \\Pi = \\delta\\Sigma w_{mkt}",
    reference:
      "Black & Litterman (1992), FAJ 48(5); He & Litterman (1999); Idzorek (2005)",
  },
  blConfidence: {
    title: "View confidence",
    text: "How sure you are about the view. 100 % forces the posterior to match the view exactly; 0 % ignores it. Mapped to the view's uncertainty Ω with Idzorek's method.",
    reference:
      "Idzorek (2005), 'A step-by-step guide to the Black-Litterman model'",
  },
  blTau: {
    title: "τ (tau)",
    text: "How uncertain the equilibrium prior itself is, relative to the covariance of returns. Small τ = trust the prior more.",
    formula: "\\Pi \\sim \\mathcal N(\\mu, \\tau\\Sigma)",
    reference: "He & Litterman (1999)",
  },
  blDelta: {
    title: "Risk aversion δ",
    text: "Scales the covariance into equilibrium returns. Implied from the benchmark's own excess return divided by its variance over the window.",
    formula: "\\delta = \\frac{\\mathbb{E}[r_m] - r_f}{\\sigma_m^2}",
    reference: "Black & Litterman (1992)",
  },
  priorImplied: {
    title: "Prior-implied value",
    text: "What the equilibrium prior already expects for this view before you express it. The further your view is from this, the more it moves the posterior.",
  },
  riskFree: {
    title: "Risk-free rate",
    text: "Used for Sharpe ratios, the tangency portfolio, the CML and Black–Litterman's equilibrium. By default it is the 3-month T-bill (FRED DGS3MO) over the window; you can type your own instead.",
  },
  sharpeTest: {
    title: "Sharpe-difference test",
    text: "Is the out-of-sample Sharpe ratio of a method different from 1/N by more than luck would explain? A p-value below 0.05 is conventional evidence; above it, the two are statistically indistinguishable over this sample.",
    formula:
      "z = \\frac{\\widehat{SR}_i - \\widehat{SR}_{1/N}}{\\widehat{se}_{HAC}}",
    reference:
      "Ledoit & Wolf (2008), J. Empirical Finance 15(5); Jobson & Korkie (1981); Memmel (2003)",
  },
  walkForward: {
    title: "Walk-forward (out-of-sample)",
    text: "At each rebalance the method sees only the trailing window of data, picks weights, and then holds them — drifting with prices and paying trading costs — until the next rebalance. No look-ahead.",
    reference:
      "DeMiguel, Garlappi & Uppal (2009), 'Optimal versus naive diversification', RFS 22(5)",
  },
  costDrag: {
    title: "Cost drag",
    text: "Annual return lost to trading costs: turnover × the cost per unit traded.",
    formula: "\\text{drag} \\approx \\text{turnover}_{ann} \\times c",
  },
  calmar: {
    title: "Calmar ratio",
    text: "Annual growth rate divided by the worst drawdown — return per unit of pain.",
    formula: "\\text{Calmar} = \\text{CAGR} / |\\text{MDD}|",
    reference: "Young (1991), Futures magazine",
  },
  annualTurnover: {
    title: "Annual turnover",
    text: "Average one-way trading per year as a fraction of the portfolio. 1.0 means the whole book is replaced once a year.",
    formula:
      "\\frac{252}{T}\\sum_{t \\in \\text{rebal}} \\sum_i |w_{i,t} - w_{i,t^-}|",
  },
  standardError: {
    title: "Standard error of the mean",
    text: "How imprecise a historical average return is. With daily data it shrinks only with the length of the sample in years, not the number of days — ten years still leaves a ±2 SE band of many percentage points.",
    formula: "se(\\hat\\mu) \\approx \\frac{\\sigma}{\\sqrt{T_{years}}}",
    reference: "Merton (1980), JFE 8(4)",
  },
} satisfies Record<string, Info>;

export const COV_LABEL: Record<CovName, string> = {
  sample: "Sample",
  ewma: "EWMA (RiskMetrics)",
  lw_constant_corr: "Ledoit–Wolf · constant correlation",
  lw_identity: "Ledoit–Wolf · identity",
  oas: "Oracle approximating shrinkage",
  mp_denoise: "Marchenko–Pastur denoised",
};

export const COV_SHORT: Record<CovName, string> = {
  sample: "Sample",
  ewma: "EWMA",
  lw_constant_corr: "LW const-corr",
  lw_identity: "LW identity",
  oas: "OAS",
  mp_denoise: "MP denoised",
};

const COV_TEXT: Record<CovName, { text: string; formula?: string }> = {
  sample: {
    text: "The plain historical covariance. Unbiased, but with N assets it estimates N(N+1)/2 numbers from limited data, so its extremes are mostly noise.",
    formula: "S = \\frac{1}{T-1}\\sum_t (r_t-\\bar r)(r_t-\\bar r)^\\top",
  },
  ewma: {
    text: "Recent days weigh more (decay λ per day), so the estimate reacts quickly to changing volatility. Good for risk today, noisy for long-run allocation.",
    formula: "\\Sigma_t = \\lambda\\Sigma_{t-1} + (1-\\lambda) r_t r_t^\\top",
  },
  lw_constant_corr: {
    text: "Shrinks the sample covariance toward a matrix where every pair has the same (average) correlation. The usual default for equity-like universes.",
    formula: "\\hat\\Sigma = \\delta F_{\\bar\\rho} + (1-\\delta) S",
  },
  lw_identity: {
    text: "Shrinks toward a scaled identity matrix — pulls every correlation toward zero and every variance toward the average. Well-conditioned even with more assets than observations.",
    formula: "\\hat\\Sigma = \\delta\\, \\bar\\sigma^2 I + (1-\\delta) S",
  },
  oas: {
    text: "Like Ledoit–Wolf toward identity, with a shrinkage intensity derived to be closer to optimal under Gaussian returns in small samples.",
    formula:
      "\\hat\\Sigma = \\rho\\,\\tfrac{\\operatorname{tr} S}{N} I + (1-\\rho) S",
  },
  mp_denoise: {
    text: "Keeps the eigenvalues that stand above the Marchenko–Pastur noise ceiling and flattens the rest to their average — removing noise without touching the signal directions.",
    formula:
      "\\tilde\\lambda_k = \\tfrac{1}{N-K}\\sum_{j>K}\\lambda_j \\;\\; (k > K)",
  },
};

export function covInfo(name: CovName, reference?: string): Info {
  return { title: COV_LABEL[name], ...COV_TEXT[name], reference };
}

export const RETURNS_LABEL: Record<ReturnsModel, string> = {
  historical: "Historical mean",
  james_stein: "James–Stein shrinkage",
  capm: "CAPM equilibrium",
  black_litterman: "Black–Litterman",
};

const RETURNS_TEXT: Record<ReturnsModel, { text: string; formula?: string }> = {
  historical: {
    text: "The average past return of each asset, annualised. Simple, and the single largest source of error in mean-variance optimisation.",
    formula: "\\hat\\mu_i = 252\\,\\bar r_i",
  },
  james_stein: {
    text: "Pulls every asset's historical mean toward the grand mean. Extreme past winners and losers are treated as partly luck.",
    formula:
      "\\hat\\mu^{JS} = (1-\\hat w)\\,\\hat\\mu + \\hat w\\, \\mu_0 \\mathbf 1",
  },
  capm: {
    text: "Expected return = risk-free + beta × the benchmark's excess return. Uses no asset-specific mean at all.",
    formula: "\\mu_i = r_f + \\beta_i (\\mathbb E[r_m] - r_f)",
  },
  black_litterman: {
    text: "Equilibrium returns implied by a prior portfolio, blended with your own views in proportion to your confidence. Edit views in the Black–Litterman tab.",
    formula:
      "\\mu_{BL} = [(\\tau\\Sigma)^{-1}+P^\\top\\Omega^{-1}P]^{-1}[(\\tau\\Sigma)^{-1}\\Pi+P^\\top\\Omega^{-1}Q]",
  },
};

export function returnsInfo(name: ReturnsModel, reference?: string): Info {
  return { title: RETURNS_LABEL[name], ...RETURNS_TEXT[name], reference };
}

/** Objective of each method in one line of TeX (shown in the method picker's info). */
export const METHOD_FORMULA: Record<MethodName, string> = {
  equal_weight: "w_i = 1/N",
  inverse_volatility: "w_i \\propto 1/\\sigma_i",
  min_variance: "\\min_w\\; w^\\top \\Sigma w",
  max_sharpe: "\\max_w\\; \\frac{\\mu^\\top w - r_f}{\\sqrt{w^\\top\\Sigma w}}",
  mean_variance:
    "\\max_w\\; \\mu^\\top w - \\tfrac{\\gamma}{2} w^\\top\\Sigma w",
  risk_parity: "RC_i = RC_j \\;\\; \\forall i,j",
  hrp: "\\text{tree}(d_{ij}) \\to \\text{bisection with } \\alpha = 1 - \\tfrac{V_L}{V_L+V_R}",
  herc: "\\text{equal risk across clusters, } w \\propto 1/\\sigma^2 \\text{ within}",
  max_diversification:
    "\\max_w\\; \\frac{w^\\top\\sigma}{\\sqrt{w^\\top\\Sigma w}}",
  min_cvar: "\\min_w\\; \\text{CVaR}_\\alpha(-r^\\top w)",
};

/** One-word family used to group the method cards. */
export const METHOD_FAMILY: Record<
  MethodName,
  "Naive" | "Mean–variance" | "Risk-based" | "Hierarchical" | "Tail risk"
> = {
  equal_weight: "Naive",
  inverse_volatility: "Naive",
  min_variance: "Risk-based",
  max_sharpe: "Mean–variance",
  mean_variance: "Mean–variance",
  risk_parity: "Risk-based",
  hrp: "Hierarchical",
  herc: "Hierarchical",
  max_diversification: "Risk-based",
  min_cvar: "Tail risk",
};

export const METHOD_SHORT: Record<MethodName, string> = {
  equal_weight: "1/N",
  inverse_volatility: "Inverse vol",
  min_variance: "Min variance",
  max_sharpe: "Max Sharpe",
  mean_variance: "Mean–variance",
  risk_parity: "Risk parity (ERC)",
  hrp: "HRP",
  herc: "HERC",
  max_diversification: "Max diversification",
  min_cvar: "Min CVaR",
};

export const LINKAGE_INFO: Record<LinkageName, string> = {
  single:
    "Single: clusters merge at their closest pair (López de Prado's default; tends to chain).",
  ward: "Ward: merges that least increase within-cluster variance (compact, balanced clusters).",
  average: "Average: mean distance between all pairs across clusters.",
  complete: "Complete: clusters merge at their farthest pair (tight clusters).",
};
