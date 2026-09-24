/**
 * Definitions for every metric and model on the Volatility page: one plain-English
 * sentence, the formula (KaTeX) and the paper it comes from.
 */
import type { Info } from "../../lib/glossary";

export const ESTIMATOR_LABEL: Record<string, string> = {
  close_to_close: "Close-to-close",
  parkinson: "Parkinson",
  garman_klass: "Garman–Klass",
  rogers_satchell: "Rogers–Satchell",
  yang_zhang: "Yang–Zhang",
};

export const ESTIMATOR_SHORT: Record<string, string> = {
  close_to_close: "CC",
  parkinson: "PK",
  garman_klass: "GK",
  rogers_satchell: "RS",
  yang_zhang: "YZ",
};

export const INFO = {
  close_to_close: {
    title: "Close-to-close volatility",
    text: "The textbook estimate: the standard deviation of daily log returns, annualized. Uses one price per day, so it is noisy over short windows.",
    formula: "\\sigma^2_{CC} = \\frac{252}{n-1}\\sum_{i}(r_i - \\bar r)^2,\\quad r_i = \\ln\\frac{C_i}{C_{i-1}}",
  },
  parkinson: {
    title: "Parkinson (high–low)",
    text: "Uses the day's high–low range, which carries about five times more information than the close alone. Ignores overnight gaps and assumes no drift, so it reads low when prices gap.",
    formula: "\\sigma^2_{P} = \\frac{252}{4n\\ln 2}\\sum_i \\left(\\ln\\frac{H_i}{L_i}\\right)^2",
    reference: "Parkinson (1980), “The Extreme Value Method for Estimating the Variance of the Rate of Return”, J. Business 53(1)",
  },
  garman_klass: {
    title: "Garman–Klass",
    text: "Combines the high–low range with the open-to-close move for a more efficient estimate. Like Parkinson it ignores overnight gaps and assumes zero drift.",
    formula: "\\sigma^2_{GK} = \\frac{252}{n}\\sum_i \\left[\\tfrac12\\left(\\ln\\tfrac{H_i}{L_i}\\right)^2 - (2\\ln2-1)\\left(\\ln\\tfrac{C_i}{O_i}\\right)^2\\right]",
    reference: "Garman & Klass (1980), “On the Estimation of Security Price Volatilities from Historical Data”, J. Business 53(1)",
  },
  rogers_satchell: {
    title: "Rogers–Satchell",
    text: "A range estimator that stays unbiased when the price trends during the day (drift-robust). Still excludes overnight gaps.",
    formula: "\\sigma^2_{RS} = \\frac{252}{n}\\sum_i \\left[\\ln\\tfrac{H_i}{C_i}\\ln\\tfrac{H_i}{O_i} + \\ln\\tfrac{L_i}{C_i}\\ln\\tfrac{L_i}{O_i}\\right]",
    reference: "Rogers & Satchell (1991), “Estimating Variance from High, Low and Closing Prices”, Ann. Appl. Probab. 1(4)",
  },
  yang_zhang: {
    title: "Yang–Zhang",
    text: "The most complete estimator here: overnight gap variance plus a weighted blend of open-to-close and Rogers–Satchell variance. Drift-independent and handles gaps, with the lowest estimation error of the five.",
    formula: "\\sigma^2_{YZ} = \\sigma^2_{O} + k\\,\\sigma^2_{C} + (1-k)\\,\\sigma^2_{RS},\\quad k = \\frac{0.34}{1.34 + \\frac{n+1}{n-1}}",
    reference: "Yang & Zhang (2000), “Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices”, J. Business 73(3)",
  },
  cone: {
    title: "Volatility cone",
    text: "For each look-back horizon, the historical range of realized volatility (min, 10th–90th percentile bands, max). Today's value is the dot: near the top means volatility is unusually high for that horizon, near the bottom unusually calm. Short horizons fan out wider because they are noisier.",
    formula: "\\text{band}_h = \\text{percentiles of }\\{\\hat\\sigma_{t,h}\\}_t \\text{ over overlapping } h\\text{-day windows}",
    reference: "Burghardt & Lane (1990), “How to Tell if Options Are Cheap”, J. Portfolio Management 16(2)",
  },
  cone_pctile: {
    title: "Percentile in history",
    text: "Share of all past windows of the same length whose realized volatility was at or below today's.",
    formula: "\\text{pct} = \\frac{1}{N}\\#\\{t : \\hat\\sigma_{t,h} \\le \\hat\\sigma_{\\text{now},h}\\}",
  },
  vrp: {
    title: "Volatility risk premium",
    text: "Implied volatility minus the volatility that is actually realized. It is usually positive: option buyers pay a premium for insurance, and option sellers earn it — except in crashes, when realized vol overshoots.",
    formula: "\\text{VRP}_t = \\sigma^{\\text{impl}}_{t} - \\sigma^{\\text{real}}_{t},\\qquad \\text{VRP}^{\\text{var}}_t = (\\sigma^{\\text{impl}}_{t})^2 - (\\sigma^{\\text{real}}_{t})^2",
    reference: "Carr & Wu (2009), “Variance Risk Premiums”, RFS 22(3); Bollerslev, Tauchen & Zhou (2009), RFS 22(11)",
  },
  vrp_forward: {
    title: "Ex-post (forward) premium",
    text: "Implied vol on day t minus the realized vol over the NEXT 21 sessions — the volatility the option price was actually forecasting. Known only after the fact.",
    formula: "\\sigma^{\\text{impl}}_t - \\hat\\sigma^{CC}_{t \\to t+21}",
    reference: "Carr & Wu (2009), RFS 22(3)",
  },
  vrp_ratio: { title: "Implied / realized", text: "How many times larger implied vol is than trailing realized vol. Above 1 means options price more movement than has recently occurred." },
  atm_iv: {
    title: "ATM-forward implied vol",
    text: "Implied volatility at the strike equal to the forward price, read from the fitted SVI smile. The market's single-number estimate of volatility to that expiry.",
    formula: "\\sigma_{ATM}(T) = \\sqrt{w(0, T)/T}",
    reference: "Gatheral (2004), “A parsimonious arbitrage-free implied volatility parameterization”",
  },
  atm_30d: {
    title: "30-day ATM vol",
    text: "ATM-forward implied vol at a constant 30-day maturity, interpolated linearly in total variance between the two expiries around 30 days.",
    formula: "w(0, T_{30}) = w_1 + \\frac{T_{30}-T_1}{T_2-T_1}(w_2 - w_1),\\quad \\sigma = \\sqrt{w/T_{30}}",
  },
  model_free: {
    title: "Model-free (VIX-style) vol",
    text: "Volatility implied by the whole strip of out-of-the-money options, the way Cboe computes the VIX. It doesn't depend on any pricing model and includes the tails, so it usually sits above ATM vol when puts are expensive.",
    formula: "\\sigma^2 = \\frac{2}{T}\\sum_i \\frac{\\Delta K_i}{K_i^2} e^{rT} Q(K_i) - \\frac{1}{T}\\left(\\frac{F}{K_0} - 1\\right)^2",
    reference: "Demeterfi, Derman, Kamal & Zou (1999); Britten-Jones & Neuberger (2000), J. Finance 55(2); Cboe VIX white paper",
  },
  rr: {
    title: "Risk reversal",
    text: "Implied vol of an out-of-the-money call minus a put of the same delta. Negative means downside protection costs more than upside exposure — the market fears a fall more than it hopes for a rally.",
    formula: "RR_{25} = \\sigma(\\Delta_C = 0.25) - \\sigma(\\Delta_P = -0.25)",
    reference: "Market convention; see Castagna (2010), FX Options and Smile Risk, ch. 3",
  },
  bf: {
    title: "Butterfly",
    text: "Average of the 25-delta call and put vols minus ATM vol — how much fatter than normal the market thinks both tails are (smile curvature).",
    formula: "BF_{25} = \\tfrac12\\left[\\sigma(\\Delta_C = 0.25) + \\sigma(\\Delta_P = -0.25)\\right] - \\sigma_{ATM}",
    reference: "Market convention; see Castagna (2010), ch. 3",
  },
  svi: {
    title: "SVI smile",
    text: "A five-parameter curve (a, b, ρ, m, σ) fitted to each expiry's implied total variance. It has the right shape in the wings and makes it easy to check for static arbitrage.",
    formula: "w(k) = a + b\\left[\\rho (k - m) + \\sqrt{(k-m)^2 + \\sigma^2}\\right],\\quad w = \\sigma_{BS}^2 T,\\ k = \\ln(K/F)",
    reference: "Gatheral (2004); Zeliade Systems (2009) quasi-explicit calibration; Gatheral & Jacquier (2014), Quant. Finance 14(1)",
  },
  butterfly_g: {
    title: "Butterfly arbitrage: g(k)",
    text: "Durrleman's condition: the implied density is non-negative everywhere only if g(k) ≥ 0. A negative minimum means some butterfly spread would have a negative price — a free lunch, or a bad fit.",
    formula: "g(k) = \\left(1 - \\frac{k w'}{2w}\\right)^2 - \\frac{w'^2}{4}\\left(\\frac1w + \\frac14\\right) + \\frac{w''}{2} \\ \\ge 0",
    reference: "Gatheral & Jacquier (2014), “Arbitrage-free SVI volatility surfaces”, Quant. Finance 14(1)",
  },
  calendar: {
    title: "Calendar arbitrage",
    text: "Total implied variance must not decrease with maturity at any fixed log-moneyness; otherwise a calendar spread would have negative cost.",
    formula: "\\partial_T\\, w(k, T) \\ge 0 \\;\\;\\forall k",
    reference: "Gatheral & Jacquier (2014), Quant. Finance 14(1)",
  },
  parity: {
    title: "Implied forward, rate & dividend",
    text: "For European options, call minus put at the same strike is a straight line in the strike. Regressing C − P on K across near-the-money strikes gives the discount factor D (slope) and the forward F (intercept / slope) — the market's own rate and dividend yield for that expiry, with no outside data.",
    formula: "C(K) - P(K) = D\\,(F - K)\\;\\Rightarrow\\; r = -\\tfrac{\\ln D}{T},\\quad q = r - \\tfrac{1}{T}\\ln\\tfrac{F}{S}",
    reference: "Stoll (1969), J. Finance 24(5); robust fit by Huber (1964) IRLS; Cboe VIX white paper",
  },
  implied_move: {
    title: "Implied move",
    text: "The at-the-money straddle price as a fraction of spot — roughly the average absolute move the market prices by that expiry.",
    formula: "\\text{move} \\approx \\frac{C(K_{ATM}) + P(K_{ATM})}{S} \\approx \\sqrt{\\tfrac{2}{\\pi}}\\,\\sigma\\sqrt{T}",
  },
  rnd: {
    title: "Risk-neutral density",
    text: "The probability distribution of the price at expiry that makes every option in the chain correctly priced. It comes from the curvature of call prices in the strike.",
    formula: "q(K) = \\frac{1}{D}\\frac{\\partial^2 C(K)}{\\partial K^2},\\qquad P(S_T \\le K) = 1 + \\frac1D\\frac{\\partial C}{\\partial K}",
    reference: "Breeden & Litzenberger (1978), “Prices of State-Contingent Claims Implicit in Option Prices”, J. Business 51(4)",
  },
  lognormal: {
    title: "Lognormal benchmark",
    text: "The Black–Scholes density with a single flat vol equal to the ATM vol. The gaps between it and the market density are the smile: a fatter left tail and a thinner right tail are the usual equity picture.",
    formula: "\\ln S_T \\sim \\mathcal N\\left(\\ln F - \\tfrac12\\sigma_{ATM}^2T,\\ \\sigma_{ATM}^2 T\\right)",
  },
  q_prob: {
    title: "Risk-neutral probability",
    text: "Probability under the pricing measure Q. It embeds risk premia (crash insurance is expensive), so downside probabilities are typically higher than real-world odds — read them as prices, not forecasts.",
    reference: "Bakshi, Kapadia & Madan (2003), RFS 16(1)",
  },
  skew: {
    title: "Skewness",
    text: "Asymmetry of the return distribution. Negative means a longer left tail — large falls are priced as more likely than equally large rises.",
    formula: "\\gamma_1 = \\mathbb E_Q\\left[\\left(\\frac{x - \\mu}{s}\\right)^3\\right],\\quad x = S_T/F - 1",
  },
  kurt: {
    title: "Excess kurtosis",
    text: "Tail heaviness relative to a normal distribution (0). Positive means big moves are priced as more likely than a bell curve suggests.",
    formula: "\\gamma_2 = \\mathbb E_Q\\left[\\left(\\frac{x - \\mu}{s}\\right)^4\\right] - 3",
  },
  bsm: {
    title: "Black–Scholes–Merton",
    text: "Price of a European option when the underlying follows geometric Brownian motion with constant volatility, a continuous rate r and a continuous dividend yield q.",
    formula: "C = S e^{-qT} N(d_1) - K e^{-rT} N(d_2),\\quad d_{1,2} = \\frac{\\ln(S/K) + (r - q \\pm \\tfrac12\\sigma^2)T}{\\sigma\\sqrt T}",
    reference: "Black & Scholes (1973), JPE 81(3); Merton (1973), Bell J. Econ. 4(1); Black (1976)",
  },
  delta: { title: "Delta", text: "Change in option value for a $1 move in the underlying — also the hedge ratio in shares.", formula: "\\Delta_C = e^{-qT}N(d_1),\\quad \\Delta_P = -e^{-qT}N(-d_1)", reference: "Haug (2007), The Complete Guide to Option Pricing Formulas" },
  gamma: { title: "Gamma", text: "How fast delta changes as the underlying moves; largest at the money near expiry.", formula: "\\Gamma = \\frac{e^{-qT} n(d_1)}{S\\sigma\\sqrt T}", reference: "Haug (2007)" },
  vega: { title: "Vega", text: "Change in option value for one volatility point (1%) change in implied vol.", formula: "\\mathcal V = S e^{-qT} n(d_1)\\sqrt T \\;/\\; 100", reference: "Haug (2007)" },
  theta: { title: "Theta (per day)", text: "Value lost per calendar day as time passes with nothing else changing.", formula: "\\Theta = \\partial V/\\partial t \\;/\\; 365", reference: "Haug (2007)" },
  rho: { title: "Rho", text: "Change in option value for a one-percentage-point rise in the interest rate.", formula: "\\rho_C = K T e^{-rT} N(d_2) \\;/\\; 100", reference: "Haug (2007)" },
  vanna: { title: "Vanna", text: "How delta changes when implied vol moves (equivalently, how vega changes with spot).", formula: "\\partial^2 V / \\partial S\\,\\partial\\sigma = -e^{-qT} n(d_1)\\, d_2/\\sigma", reference: "Haug (2007)" },
  volga: { title: "Volga (vomma)", text: "How vega changes when implied vol moves — the convexity of the option in volatility.", formula: "\\partial^2 V / \\partial\\sigma^2 = \\mathcal V\\, d_1 d_2 / \\sigma", reference: "Haug (2007)" },
  charm: { title: "Charm (per day)", text: "How delta drifts each day from the passage of time alone — why hedges need rebalancing even when the price doesn't move.", formula: "\\partial \\Delta / \\partial t", reference: "Haug (2007)" },
  pop: {
    title: "Probability of profit",
    text: "The risk-neutral probability that the position's P&L at the first expiry is positive, integrating the payoff against the Breeden–Litzenberger density from that expiry's SVI smile.",
    formula: "\\text{POP} = \\int \\mathbb 1\\{\\text{P\\&L}(K) > 0\\}\\, q(K)\\, dK",
    reference: "Breeden & Litzenberger (1978); risk-neutral, see Bakshi, Kapadia & Madan (2003)",
  },
  exp_pnl: {
    title: "Expected P&L under Q",
    text: "Average P&L at expiry weighted by the risk-neutral density. Close to zero (minus costs) by construction: options are fairly priced under Q.",
    formula: "\\mathbb E_Q[\\text{P\\&L}] = \\int \\text{P\\&L}(K)\\, q(K)\\, dK",
  },
  friction: { title: "Round-trip friction", text: "Cost at the natural prices (buy at the ask, sell at the bid) minus cost at mid — what crossing the spread costs you." },
  iv: {
    title: "Implied volatility (ours)",
    text: "The volatility that makes Black-76 reproduce the option's mid price, using the parity-implied forward and discount factor of its expiry.",
    formula: "C_{mid} = D\\,[F N(d_1) - K N(d_2)],\\quad d_{1,2} = \\frac{\\ln(F/K) \\pm \\tfrac12\\sigma^2T}{\\sigma\\sqrt T}",
    reference: "Black (1976); solver: Corrado & Miller (1996) start, safeguarded Newton + Brent (1973)",
  },
  vendor_iv: { title: "Vendor IV", text: "The implied vol published by the data vendor (Cboe). Shown faintly for comparison; differences usually come from a different rate, dividend or price convention." },
} satisfies Record<string, Info>;
