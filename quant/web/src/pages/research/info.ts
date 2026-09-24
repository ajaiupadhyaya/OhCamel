/**
 * InfoTip content for Strategy Lab: one plain-English sentence, the formula as the backend
 * computes it (quant/src/ohcamel_quant/backtest/{metrics,validation}.py) and the paper.
 */
import type { Info } from "../../lib/glossary";

export const INFO = {
  cagr: { title: "CAGR", text: "The constant yearly growth rate that turns the starting value into the ending value, after trading costs.", formula: "\\text{CAGR} = \\Big(\\prod_t (1+r_t)\\Big)^{252/N} - 1" },
  total_return: { title: "Total return", text: "Cumulative growth over the whole live window.", formula: "\\prod_t (1+r_t) - 1" },
  vol: { title: "Annualized volatility", text: "The typical yearly swing: standard deviation of daily returns scaled by √252.", formula: "\\sigma = \\sqrt{252}\\,\\operatorname{sd}(r_t)" },
  sharpe: { title: "Sharpe ratio", text: "Average excess return over T-bills per unit of volatility, annualized. Above 1 is rare for a real strategy after costs.", formula: "SR = \\sqrt{252}\\,\\frac{\\overline{r - r_f}}{\\operatorname{sd}(r - r_f)}", reference: "Sharpe (1994), “The Sharpe Ratio”, Journal of Portfolio Management 21(1)" },
  sortino: { title: "Sortino ratio", text: "Like Sharpe, but only downside volatility counts as risk.", formula: "\\text{Sortino} = \\sqrt{252}\\,\\frac{\\overline{r - r_f}}{\\sqrt{\\overline{\\min(r - r_f, 0)^2}}}", reference: "Sortino & Price (1994), Journal of Investing 3(3)" },
  max_drawdown: { title: "Maximum drawdown", text: "The worst peak-to-trough fall of the equity curve — the loss someone who bought at the top would have sat through.", formula: "\\text{MDD} = \\min_t \\Big( \\tfrac{W_t}{\\max_{s \\le t} W_s} - 1 \\Big)" },
  calmar: { title: "Calmar ratio", text: "Growth per unit of worst drawdown: how much you were paid for the deepest hole.", formula: "\\text{Calmar} = \\frac{\\text{CAGR}}{|\\text{MDD}|}", reference: "Young (1991), “Calmar Ratio: A Smoother Tool”, Futures 20(1)" },
  hit_rate: { title: "Hit rate", text: "Share of sessions with a positive return." },
  var95: { title: "Historical VaR 95%", text: "The daily loss exceeded on only 5% of sessions in the backtest.", formula: "\\text{VaR}_{95} = -q_{0.05}(r_t)" },
  cvar95: { title: "Historical CVaR 95%", text: "The average loss on the worst 5% of sessions.", formula: "\\text{CVaR}_{95} = -\\mathbb{E}[r_t \\mid r_t \\le q_{0.05}]", reference: "Acerbi & Tasche (2002), Journal of Banking & Finance 26(7)" },
  skew: { title: "Skewness", text: "Asymmetry of daily returns: negative means occasional large losses (crash risk); trend-followers often show positive skew." },
  kurtosis: { title: "Kurtosis", text: "Fatness of the tails (3 for a normal distribution). High kurtosis makes a Sharpe ratio less trustworthy.", formula: "\\gamma_4 = \\mathbb{E}[(r-\\mu)^4]/\\sigma^4" },
  best_worst: { title: "Best / worst day", text: "The single largest daily gain and loss in the live window." },
  psr: {
    title: "Probabilistic Sharpe Ratio",
    text: "The probability that the true Sharpe exceeds 0, given how long the track record is and how skewed and fat-tailed the returns are. 95% or more is the usual bar.",
    formula: "\\widehat{PSR}(0) = \\Phi\\!\\left(\\frac{\\widehat{SR}\\sqrt{n-1}}{\\sqrt{1 - \\gamma_3 \\widehat{SR} + \\frac{\\gamma_4 - 1}{4}\\widehat{SR}^2}}\\right)",
    reference: "Bailey & López de Prado (2012), “The Sharpe Ratio Efficient Frontier”, Journal of Risk 15(2)",
  },
  min_trl: {
    title: "Minimum track record length",
    text: "How many years of returns like these you would need before the PSR clears 95%. If it exceeds the backtest length, the Sharpe is not yet distinguishable from zero.",
    formula: "\\text{MinTRL} = 1 + \\big(1 - \\gamma_3 SR + \\tfrac{\\gamma_4 - 1}{4} SR^2\\big)\\Big(\\frac{z_{0.95}}{SR}\\Big)^2",
    reference: "Bailey & López de Prado (2012), Journal of Risk 15(2), eq. 13",
  },
  bootstrap: {
    title: "Bootstrap Sharpe interval",
    text: "Resample the daily returns in random-length blocks (keeping short-run dependence), recompute the Sharpe each time, and read off the middle 95%. A wide interval that includes 0 means the backtest cannot tell skill from luck.",
    formula: "\\text{CI}_{95} = \\big[q_{0.025}(SR^*_b),\\; q_{0.975}(SR^*_b)\\big]",
    reference: "Politis & Romano (1994), JASA 89(428); block length: Politis & White (2004), Econometric Reviews 23(1)",
  },
  alpha: { title: "Alpha (annualized)", text: "Return left over after accounting for the benchmark exposure (beta), per year.", formula: "r - r_f = \\alpha + \\beta (r_b - r_f) + \\varepsilon", reference: "Jensen (1968), Journal of Finance 23(2)" },
  beta: { title: "Beta", text: "How much the strategy tends to move when the benchmark moves 1%.", formula: "\\beta = \\frac{\\operatorname{Cov}(r, r_b)}{\\operatorname{Var}(r_b)}" },
  correlation: { title: "Correlation", text: "How closely daily returns move with the benchmark (−1 to +1). Low correlation is what makes a strategy a diversifier." },
  ir: { title: "Information ratio", text: "Average return over the benchmark per unit of tracking error, annualized.", formula: "IR = \\sqrt{252}\\,\\frac{\\overline{r - r_b}}{\\operatorname{sd}(r - r_b)}", reference: "Grinold & Kahn (2000), Active Portfolio Management" },
  te: { title: "Tracking error", text: "Annualized volatility of the difference between strategy and benchmark returns.", formula: "TE = \\sqrt{252}\\,\\operatorname{sd}(r - r_b)" },
  capture: { title: "Up / down capture", text: "Share of the benchmark's gains (on up days) and losses (on down days) the strategy captured. Up above down is the goal." },
  turnover: { title: "Annual turnover", text: "Total traded notional per year as a multiple of capital: 10× means the book was traded ten times over in a year.", formula: "\\text{TO} = \\tfrac{252}{N}\\sum_t \\sum_i |w_{i,t} - w_{i,t^-}|" },
  cost_drag: { title: "Annual cost drag", text: "Return lost to proportional trading costs per year, at the cost level you set.", formula: "\\approx c \\times \\text{TO}" },
  exposure: { title: "Exposure", text: "Gross = sum of absolute weights (leverage); net = longs minus shorts (market direction); cash = 1 − Σw, which earns the T-bill rate." },
  weights: { title: "Target weights over time", text: "The weight the strategy held in each asset at each month end: ochre is short, cornflower is long, pale is flat." },
  rolling_sharpe: { title: "Rolling Sharpe", text: "Sharpe ratio over the trailing window, sampled weekly. Shows whether the edge is steady or came from one lucky stretch.", formula: "SR_t = \\sqrt{252}\\,\\frac{\\overline{r - r_f}_{[t-w,t]}}{\\operatorname{sd}(r - r_f)_{[t-w,t]}}" },
  drawdown: { title: "Drawdown", text: "How far the equity curve sits below its previous peak on each day.", formula: "D_t = \\frac{W_t}{\\max_{s \\le t} W_s} - 1" },
  look_ahead: { title: "Look-ahead audit", text: "The engine re-runs the strategy on data truncated at several dates and checks that the weights decided on those dates do not change. Any difference would mean the rule peeked at the future." },
  pbo: {
    title: "Probability of Backtest Overfitting",
    text: "Split the history into S blocks and, for every way of choosing half of them as the ‘in-sample’, pick the best parameter set there and see where it ranks on the other half. PBO is the share of splits where the in-sample winner lands in the bottom half out-of-sample. Near 0 is good; 0.5 means choosing parameters is a coin flip.",
    formula: "\\lambda_c = \\ln\\frac{\\omega_c}{1-\\omega_c},\\quad \\text{PBO} = \\frac{\\#\\{c : \\lambda_c \\le 0\\}}{\\binom{S}{S/2}}",
    reference: "Bailey, Borwein, López de Prado & Zhu (2017), “The Probability of Backtest Overfitting”, Journal of Computational Finance 20(4)",
  },
  logit: {
    title: "Logit of the out-of-sample rank",
    text: "ω is the relative rank (0–1) of the in-sample winner among all trials out-of-sample. Its logit λ is positive when the winner stays in the top half and negative when it falls to the bottom half. Mass left of zero is the PBO.",
    formula: "\\omega_c = \\frac{\\operatorname{rank}_{\\bar J}(n^*)}{N+1}",
    reference: "Bailey et al. (2017), J. Computational Finance 20(4), sec. 2",
  },
  degradation: {
    title: "Performance degradation",
    text: "Each dot is one CSCV split: the in-sample Sharpe of the chosen parameters (x) against what they earned out-of-sample (y). A negative slope means the better it looked in-sample, the worse it did afterwards — the signature of overfitting.",
    formula: "SR_{\\bar J}(n^*) = a + b\\,SR_J(n^*) + \\varepsilon",
    reference: "Bailey et al. (2017), J. Computational Finance 20(4), sec. 3",
  },
  dsr: {
    title: "Deflated Sharpe Ratio",
    text: "The PSR of the best parameter set, but measured against the Sharpe you would expect from the best of N worthless trials rather than against zero. It asks: is the winner better than the luckiest of N random strategies?",
    formula: "DSR = \\widehat{PSR}(SR_0),\\quad SR_0 = \\sqrt{V[\\widehat{SR}]}\\Big((1-\\gamma)\\Phi^{-1}\\!\\big(1-\\tfrac1N\\big) + \\gamma\\,\\Phi^{-1}\\!\\big(1-\\tfrac{1}{Ne}\\big)\\Big)",
    reference: "Bailey & López de Prado (2014), “The Deflated Sharpe Ratio”, Journal of Portfolio Management 40(5)",
  },
  sr0: {
    title: "Expected maximum Sharpe under the null",
    text: "If none of the N trials had any skill, the best of them would still show roughly this Sharpe by chance. It grows with the number of trials and with how different their Sharpe ratios are.",
    formula: "\\mathbb{E}[\\max_n \\widehat{SR}_n] \\approx \\sqrt{V}\\Big((1-\\gamma)\\Phi^{-1}(1-\\tfrac1N) + \\gamma\\Phi^{-1}(1-\\tfrac{1}{Ne})\\Big)",
    reference: "Bailey & López de Prado (2014), JPM 40(5), eq. 2 (the False Strategy Theorem)",
  },
  spa: {
    title: "Hansen's SPA test",
    text: "Tests whether any of the parameter sets truly beats buy-and-hold of the benchmark after accounting for having tried them all. A small p-value (below 0.05) is evidence that at least one does; a large one means the best result is consistent with luck.",
    formula: "T^{SPA} = \\max_k \\frac{\\sqrt{n}\\,\\bar d_k}{\\hat\\omega_k},\\quad d_{k,t} = r_{k,t} - r_{b,t}",
    reference: "Hansen (2005), “A Test for Superior Predictive Ability”, JBES 23(4); White (2000), Econometrica 68(5)",
  },
  wfe: {
    title: "Walk-forward efficiency",
    text: "Out-of-sample Sharpe divided by the average in-sample Sharpe of the parameters that were chosen. Near 1 means the optimisation carried over; well below 1 means much of the in-sample edge was fitted noise.",
    formula: "WFE = \\frac{SR_{OOS}}{\\overline{SR}_{IS}}",
    reference: "Pardo (2008), The Evaluation and Optimization of Trading Strategies, ch. 11",
  },
  walk_forward: {
    title: "Walk-forward optimisation",
    text: "At each step, choose the parameters that did best over the previous in-sample window, trade them over the next out-of-sample block, then roll forward. The stitched out-of-sample curve is what an investor re-optimising on schedule would actually have earned.",
    reference: "Pardo (2008), ch. 11",
  },
  breakeven: {
    title: "Break-even cost",
    text: "The one-way proportional cost (bps per unit of traded notional) at which the strategy's Sharpe falls to zero — or its CAGR to the benchmark's. Compare it with what you actually pay: ~1–5 bps for liquid ETFs at a discount broker.",
    formula: "1 + r^{net}_t = (1 + r^{gross}_t - b_t)(1 - c\\,\\text{TO}_t)",
  },
  cost_bps: { title: "Trading cost", text: "One-way proportional cost per unit of traded notional, in basis points (1 bp = 0.01%). Covers commission, half the bid-ask spread and a little impact." },
  borrow_bps: { title: "Borrow fee", text: "Annual fee charged on short positions, in basis points of short notional." },
  lag: { title: "Execution lag", text: "Signals are decided at the close of day t and traded at the close of day t + lag. A lag of 1 is the honest minimum: you cannot trade at the close that produced the signal." },
  vol_target: { title: "Portfolio volatility target", text: "Optionally scale the whole book so its forecast volatility (EWMA, 21-day half-life) matches a target, capped by the strategy's leverage limit.", reference: "Moreira & Muir (2017), Journal of Finance 72(4)" },
  rebalance: { title: "Rebalance frequency", text: "How often target weights are traded. ‘Signal’ trades only when the target changes; ‘never’ buys once and lets weights drift." },
  benchmark: { title: "Benchmark", text: "Buy-and-hold comparison (no costs). Used for relative statistics, the SPA test and the break-even vs benchmark." },
} satisfies Record<string, Info>;

/** Short "Author (year)" from a full citation string. */
export function shortCite(c: string | null | undefined): string {
  if (!c) return "";
  const m = /^(.*?)\((\d{4})\)/.exec(c);
  if (!m) return c.split(".")[0];
  const authors = m[1].replace(/,?\s*[A-Z]\.(\s?[A-Z]\.)*/g, "").replace(/\s*&\s*/g, " & ").replace(/,\s*,/g, ",").replace(/[,\s]+$/g, "").trim();
  const names = authors.split(/,|&/).map((s) => s.trim()).filter(Boolean);
  const who = names.length > 2 ? `${names[0]} et al.` : names.join(" & ");
  return `${who} (${m[2]})`;
}

export const CATEGORY_LABEL: Record<string, string> = {
  trend: "Trend",
  momentum: "Momentum",
  risk: "Risk-based",
  factor: "Factor",
  reversal: "Reversal",
  "stat-arb": "Statistical arbitrage",
  benchmark: "Benchmarks",
};
export const CATEGORY_ORDER = ["trend", "momentum", "risk", "factor", "reversal", "stat-arb", "benchmark"];
