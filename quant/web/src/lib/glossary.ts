/**
 * Shared metric definitions for info tooltips. Use by key:
 *   <StatTile label="Sharpe" value={...} info="sharpe" />
 *   <InfoTip info="var" />
 * or pass an inline Info object. Add new entries here rather than inline when a metric
 * appears on more than one page, so wording stays consistent.
 */
export interface Info {
  /** Title shown in bold (defaults to the label). */
  title?: string;
  /** One plain-English sentence. */
  text: string;
  /** Optional KaTeX formula (display mode). */
  formula?: string;
  /** Optional reference, e.g. "Sharpe (1966), J. Business 39(1)". */
  reference?: string;
  /** Optional link for the reference. */
  href?: string;
}

const GLOSSARY_ES: Info = {
  title: "Expected Shortfall",
  text: "The average loss on the days that are worse than VaR — what a bad day costs once it happens.",
  formula: "\\text{ES}_\\alpha = -\\mathbb{E}[r \\mid r \\le -\\text{VaR}_\\alpha]",
  reference: "Acerbi & Tasche (2002), Journal of Banking & Finance 26(7)",
};

export const GLOSSARY = {
  return_1d: { title: "1-day return", text: "Percent change in the adjusted close over the last trading session.", formula: "r_t = \\frac{P_t}{P_{t-1}} - 1" },
  cagr: {
    title: "CAGR",
    text: "The constant yearly growth rate that would turn the starting value into the ending value.",
    formula: "\\text{CAGR} = \\left(\\frac{V_T}{V_0}\\right)^{252/N} - 1",
  },
  vol: {
    title: "Annualized volatility",
    text: "How much returns typically swing in a year: the standard deviation of daily returns scaled by √252.",
    formula: "\\sigma_{ann} = \\sqrt{252}\\; \\operatorname{sd}(r_t)",
  },
  sharpe: {
    title: "Sharpe ratio",
    text: "Excess return earned per unit of total volatility — higher means more reward for the risk taken.",
    formula: "SR = \\frac{\\mathbb{E}[r - r_f]}{\\sigma(r - r_f)}\\sqrt{252}",
    reference: "Sharpe (1966), “Mutual Fund Performance”, Journal of Business 39(1)",
  },
  sortino: {
    title: "Sortino ratio",
    text: "Like Sharpe, but only penalizes downside volatility — upside surprises don't count as risk.",
    formula: "\\text{Sortino} = \\frac{\\mathbb{E}[r - r_f]}{\\sqrt{\\mathbb{E}[\\min(r - r_f, 0)^2]}}\\sqrt{252}",
    reference: "Sortino & Price (1994), Journal of Investing 3(3)",
  },
  max_drawdown: {
    title: "Maximum drawdown",
    text: "The largest peak-to-trough fall in value — the worst loss an investor who bought at the top would have sat through.",
    formula: "\\text{MDD} = \\min_t \\left( \\frac{V_t}{\\max_{s \\le t} V_s} - 1 \\right)",
    reference: "Magdon-Ismail & Atiya (2004), “Maximum drawdown”, Risk 17(10)",
  },
  beta: {
    title: "Beta",
    text: "How much the asset tends to move when the benchmark moves 1%.",
    formula: "\\beta = \\frac{\\operatorname{Cov}(r, r_m)}{\\operatorname{Var}(r_m)}",
    reference: "Sharpe (1964), “Capital Asset Prices”, Journal of Finance 19(3)",
  },
  var: {
    title: "Value at Risk",
    text: "A loss threshold that should be exceeded only (1 − α) of the time over the horizon, e.g. the 99% one-day VaR is beaten on about 1 day in 100.",
    formula: "\\text{VaR}_\\alpha = -\\inf\\{x : P(r \\le x) > 1 - \\alpha\\}",
    reference: "Jorion (2007), Value at Risk, 3rd ed.",
  },
  es: GLOSSARY_ES,
  realized_vol: {
    title: "Realized volatility",
    text: "Volatility measured from recent daily returns over a rolling window, annualized.",
    formula: "\\sigma_{t} = \\sqrt{\\tfrac{252}{n-1}\\sum_{i=0}^{n-1} (r_{t-i} - \\bar r)^2}",
  },
  vix: { title: "VIX", text: "The market's 30-day implied volatility for the S&P 500, backed out of index option prices. Roughly the expected annualized swing." },
  term_spread: {
    title: "10Y − 2Y spread",
    text: "The slope of the Treasury curve. Negative (inverted) spreads have preceded most US recessions.",
    reference: "Estrella & Mishkin (1998), Review of Economics and Statistics 80(1)",
  },
  ytd: { title: "Year to date", text: "Return from the last close of the previous calendar year to the latest close." },

  // ---------------------------------------------------------------- tail risk (alias)
  expected_shortfall: GLOSSARY_ES,

  // ---------------------------------------------------------------- portfolio construction
  risk_contribution: {
    title: "Risk contribution",
    text: "The share of total portfolio volatility that each holding is responsible for, after accounting for how it co-moves with everything else. The contributions add up to 100%.",
    formula: "RC_i = \\frac{w_i\\,(\\Sigma w)_i}{w^\\top \\Sigma w}, \\qquad \\sum_i RC_i = 1",
    reference: "Maillard, Roncalli & Teïletche (2010), “The Properties of Equally Weighted Risk Contribution Portfolios”, Journal of Portfolio Management 36(4)",
  },
  effective_bets: {
    title: "Effective number of bets",
    text: "How many independent bets the portfolio really makes. Ten holdings that all move together may be only one or two effective bets.",
    formula: "N_{\\text{eff}} = \\exp\\!\\Big(-\\sum_k p_k \\ln p_k\\Big), \\quad p_k = \\text{share of variance from uncorrelated factor } k",
    reference: "Meucci (2009), “Managing Diversification”, Risk 22(5)",
  },
  diversification_ratio: {
    title: "Diversification ratio",
    text: "Weighted-average volatility of the holdings divided by the portfolio's volatility. 1 means no diversification benefit; higher means correlations are cancelling risk.",
    formula: "DR = \\frac{\\sum_i w_i \\sigma_i}{\\sqrt{w^\\top \\Sigma w}}",
    reference: "Choueifaty & Coignard (2008), “Toward Maximum Diversification”, Journal of Portfolio Management 35(1)",
  },
  shrinkage: {
    title: "Covariance shrinkage",
    text: "Blends the noisy sample covariance with a simple, stable target; the blend weight is chosen from the data to minimise estimation error.",
    formula: "\\hat\\Sigma = \\delta\\, F + (1 - \\delta)\\, S, \\quad 0 \\le \\delta \\le 1",
    reference: "Ledoit & Wolf (2004), “Honey, I Shrunk the Sample Covariance Matrix”, Journal of Portfolio Management 30(4)",
  },
  condition_number: {
    title: "Condition number",
    text: "Largest over smallest eigenvalue of the covariance matrix. Large values mean the matrix is close to singular, so optimizers amplify tiny estimation errors into extreme weights.",
    formula: "\\kappa(\\Sigma) = \\frac{\\lambda_{\\max}}{\\lambda_{\\min}}",
    reference: "Michaud (1989), “The Markowitz Optimization Enigma”, Financial Analysts Journal 45(1)",
  },
  marchenko_pastur: {
    title: "Marchenko–Pastur bound",
    text: "The range of eigenvalues pure noise would produce for this many assets and observations. Eigenvalues inside it are indistinguishable from noise; only those above carry signal.",
    formula: "\\lambda_{\\pm} = \\sigma^2 \\left(1 \\pm \\sqrt{N/T}\\right)^2",
    reference: "Marchenko & Pastur (1967), Mathematics of the USSR-Sbornik 1(4); Laloux et al. (1999), Physical Review Letters 83(7)",
  },

  // ---------------------------------------------------------------- backtest overfitting
  psr: {
    title: "Probabilistic Sharpe ratio",
    text: "The probability that the true Sharpe ratio is above a benchmark (usually 0), given the track-record length and the skew and fat tails of the returns.",
    formula: "\\text{PSR} = \\Phi\\!\\left( \\frac{(\\widehat{SR} - SR^*)\\sqrt{T-1}}{\\sqrt{1 - \\gamma_3 \\widehat{SR} + \\tfrac{\\gamma_4 - 1}{4}\\widehat{SR}^2}} \\right)",
    reference: "Bailey & López de Prado (2012), “The Sharpe Ratio Efficient Frontier”, Journal of Risk 15(2)",
  },
  dsr: {
    title: "Deflated Sharpe ratio",
    text: "The probabilistic Sharpe ratio with the benchmark raised to the best Sharpe you would expect by luck after trying this many strategies — a correction for multiple testing.",
    formula: "\\text{DSR} = \\text{PSR}(SR^*), \\quad SR^* = \\sqrt{V[\\widehat{SR}_n]}\\left( (1-\\gamma)\\Phi^{-1}\\!\\left[1 - \\tfrac{1}{N}\\right] + \\gamma\\,\\Phi^{-1}\\!\\left[1 - \\tfrac{1}{N e}\\right] \\right)",
    reference: "Bailey & López de Prado (2014), “The Deflated Sharpe Ratio”, Journal of Portfolio Management 40(5)",
  },
  pbo: {
    title: "Probability of backtest overfitting",
    text: "How often the configuration that looked best in-sample ranks below the median out-of-sample, across many in/out splits of the history. Above ~50% means the selection is mostly luck.",
    formula: "\\text{PBO} = P\\big(\\bar\\omega_c \\le 0\\big), \\quad \\bar\\omega_c = \\ln\\frac{\\bar r_c}{1 - \\bar r_c}",
    reference: "Bailey, Borwein, López de Prado & Zhu (2017), “The Probability of Backtest Overfitting”, Journal of Computational Finance 20(4)",
  },
} satisfies Record<string, Info>;

export type GlossaryKey = keyof typeof GLOSSARY;

export function resolveInfo(info: Info | GlossaryKey | string | undefined): Info | undefined {
  if (!info) return undefined;
  if (typeof info === "string") return (GLOSSARY as Record<string, Info>)[info] ?? { text: info };
  return info;
}
