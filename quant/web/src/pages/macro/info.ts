/** Rates & Macro — plain-English definitions, formulas and references for every metric/model. */
import type { Info } from "../../lib/glossary";

export const INFO = {
  percentile: {
    title: "10-year percentile",
    text: "Where today's reading sits within the last ten years of observations: 0% is the lowest seen, 100% the highest. It shows whether a number is unusual for its own history.",
    formula: "\\text{pct} = \\tfrac{1}{N}\\,\\#\\{\\,i : x_i \\le x_{\\text{latest}}\\}",
  },
  change: {
    title: "Change",
    text: "Latest value minus the value that was the latest one 1 month / 3 months / 1 year earlier. Blank when the horizon is shorter than how often the series is published (e.g. 1M for quarterly GDP).",
  },
  yoy: { title: "Year-over-year", text: "Percent change against the same period one year earlier — the standard way to read inflation and growth without seasonal noise.", formula: "100\\left(\\frac{x_t}{x_{t-1y}} - 1\\right)" },
  par: {
    title: "Par yield (CMT)",
    text: "The coupon a new Treasury would need to trade at exactly 100 for each maturity — the curve as the Treasury publishes it (constant-maturity, bond-equivalent, semiannual).",
    reference: "U.S. Treasury Daily Par Yield Curve Rates, via FRED DGS1MO…DGS30",
  },
  zero: {
    title: "Zero (spot) rate",
    text: "The yield on a single payment at maturity T, stripped out of coupon bonds by bootstrapping. It is the rate you discount one cash flow at.",
    formula: "P(T) = e^{-z(T)\\,T},\\qquad 1 = \\tfrac{c}{2}\\sum_{k=1}^{2T} P(k/2) + P(T)",
    reference: "Hull, Options, Futures & Other Derivatives, ch. 4 (bootstrap)",
  },
  forward: {
    title: "Forward rates",
    text: "The rate the curve locks in today for borrowing in the future. The instantaneous forward is the rate for an instant at T; the 1-year forward is the rate for a year starting at T.",
    formula: "f(T) = z(T) + T\\,z'(T),\\qquad f_{T,T+1} = z(T{+}1)(T{+}1) - z(T)\\,T",
  },
  ns: {
    title: "Nelson–Siegel",
    text: "A four-parameter smooth curve: β₀ is the long-run level, β₁ the short-minus-long slope, β₂ a hump, τ where the hump peaks. Central banks use it to publish smooth zero curves.",
    formula: "z(t) = \\beta_0 + \\beta_1\\frac{1-e^{-t/\\tau}}{t/\\tau} + \\beta_2\\left(\\frac{1-e^{-t/\\tau}}{t/\\tau} - e^{-t/\\tau}\\right)",
    reference: "Nelson & Siegel (1987), J. Business 60(4)",
  },
  nss: {
    title: "Svensson (NSS)",
    text: "Nelson–Siegel plus a second hump (β₃, τ₂), which lets the fit bend twice — the model the Fed's Gürkaynak–Sack–Wright curve uses.",
    formula: "z(t) = \\text{NS}(t;\\beta_0,\\beta_1,\\beta_2,\\tau_1) + \\beta_3\\left(\\frac{1-e^{-t/\\tau_2}}{t/\\tau_2} - e^{-t/\\tau_2}\\right)",
    reference: "Svensson (1994), NBER WP 4871; Gürkaynak, Sack & Wright (2007), JME 54(8)",
  },
  rmse: { title: "Fit RMSE", text: "Root-mean-square gap between the fitted curve and the bootstrapped zero rates at each quoted maturity, in basis points. Under ~5 bp is a tight fit.", formula: "\\sqrt{\\tfrac1N \\textstyle\\sum_i (z_i - \\hat z_i)^2}" },
  s2s10: { title: "2s10s", text: "10-year minus 2-year Treasury yield. Negative (inverted) means markets expect rates to fall — historically a recession warning.", formula: "100\\,(y_{10} - y_{2})\\ \\text{bp}" },
  s3m10y: { title: "3m10y", text: "10-year minus 3-month Treasury yield — the spread with the best recession-forecasting record.", formula: "100\\,(y_{10} - y_{0.25})\\ \\text{bp}", reference: "Estrella & Mishkin (1998), REStat 80(1)" },
  s5s30: { title: "5s30s", text: "30-year minus 5-year yield: the steepness of the long end, driven by term premia and long-run inflation expectations.", formula: "100\\,(y_{30} - y_{5})\\ \\text{bp}" },
  fly: { title: "2s5s10s butterfly", text: "How the 5-year sits relative to the 2- and 10-year. Positive = the belly yields more than the wings (cheap).", formula: "100\\,(2y_5 - y_2 - y_{10})\\ \\text{bp}" },
  pca: {
    title: "PCA of yield changes",
    text: "Daily moves across all maturities are decomposed into a few independent patterns. Almost all variation is a parallel shift (level), a twist (slope) and a bend (curvature).",
    formula: "\\Sigma_{\\Delta y} = V\\,\\Lambda\\,V^{\\top},\\quad \\text{share}_k = \\lambda_k / \\textstyle\\sum_j \\lambda_j",
    reference: "Litterman & Scheinkman (1991), J. Fixed Income 1(1)",
  },
  probit: {
    title: "Yield-curve recession probit",
    text: "The probability that the economy is in an NBER recession h months from now, estimated from the 10y–3m term spread today. Inverted curves have preceded every U.S. recession since the 1960s.",
    formula: "P(\\text{REC}_{t+h}=1) = \\Phi(\\alpha + \\beta\\,\\text{spread}_t)",
    reference: "Estrella & Mishkin (1998), REStat 80(1); Estrella & Hardouvelis (1991), J. Finance 46(2)",
  },
  pseudoR2: { title: "Pseudo-R²", text: "How much better the model fits than a constant probability, on a 0–1 scale. Estrella's version is built to read like an OLS R².", formula: "R^2_{E} = 1 - \\left(\\frac{\\ell_u}{\\ell_c}\\right)^{-\\frac{2}{n}\\ell_c}", reference: "Estrella (1998), JBES 16(2)" },
  nfci: { title: "NFCI", text: "Chicago Fed National Financial Conditions Index: 0 is average, positive means tighter-than-average credit and funding conditions.", reference: "Brave & Butters (2011), Chicago Fed Economic Perspectives" },
  sahm: {
    title: "Sahm rule",
    text: "Fires when the 3-month average unemployment rate rises 0.5 pp above its low of the prior 12 months — a real-time signal that a recession has started.",
    formula: "\\overline{u}^{(3)}_t - \\min_{s\\in[t-12,t-1]} \\overline{u}^{(3)}_s \\ge 0.50",
    reference: "Sahm (2019), Brookings Hamilton Project",
  },
  markov: {
    title: "Markov-switching regimes",
    text: "Returns are drawn from one of k hidden states, each with its own mean and volatility; the market jumps between states with fixed probabilities. The model infers which state each week was most likely in.",
    formula: "r_t = \\mu_{S_t} + \\sigma_{S_t}\\varepsilon_t,\\quad P(S_t=j\\mid S_{t-1}=i) = p_{ij}",
    reference: "Hamilton (1989), Econometrica 57(2); Kim (1994), J. Econometrics 60",
  },
  smoothed: { title: "Smoothed probability", text: "The probability of each regime at each date using the whole sample (including later data). Good for reading history; the filtered probability is the real-time version.", reference: "Kim (1994) smoother" },
  filtered: { title: "Filtered probability", text: "The regime probability using only data available up to that date — what a real-time observer would have believed.", reference: "Hamilton (1989) filter" },
  duration: { title: "Expected duration", text: "How long a regime typically lasts once entered, from its probability of staying put.", formula: "E[D_i] = \\frac{1}{1 - p_{ii}}" },
  transition: { title: "Transition matrix", text: "Row i, column j: the probability of being in regime j next period given regime i now. Rows sum to 100%.", formula: "p_{ij} = P(S_t = j \\mid S_{t-1} = i)" },
  riskPanel: {
    title: "Risk-on / risk-off panel",
    text: "A transparent composite of simple signals, each scored 0 (risk-off) to 1 (risk-on) against its own history: VIX and credit-spread percentiles (lower = calmer), curve slope sign, and price vs its 200-day average.",
    formula: "\\text{composite} = \\tfrac{1}{n}\\sum_i \\text{score}_i",
    reference: "Faber (2007), J. Wealth Management (200-day trend)",
  },
  taylor: {
    title: "Taylor (1993) rule",
    text: "A benchmark for where the policy rate 'should' be given inflation and the output gap: neutral real rate plus inflation, plus half the inflation overshoot and half the output gap.",
    formula: "i = r^* + \\pi + 0.5(\\pi - \\pi^*) + 0.5\\,\\text{gap}",
    reference: "Taylor (1993), Carnegie-Rochester Conf. Series 39",
  },
  balanced: {
    title: "Balanced-approach rule",
    text: "The Taylor rule with double the weight on the output gap — the variant the Fed's Monetary Policy Report shows alongside Taylor (1993).",
    formula: "i = r^* + \\pi + 0.5(\\pi - \\pi^*) + 1.0\\,\\text{gap}",
    reference: "Yellen (2012), speech; Fed Monetary Policy Report, policy-rules box",
  },
  rstar: { title: "r* (rule parameter)", text: "The neutral real interest rate you assume — neither stimulating nor restraining the economy. It is not observed; Taylor used 2%. Estimates since 2010 have mostly been 0.5–1.5%.", reference: "Holston, Laubach & Williams (2017), JIE 108" },
  pistar: { title: "π* (rule parameter)", text: "The inflation target the rule aims at. The Fed's is 2% on PCE inflation." },
  gap: { title: "Output gap", text: "How far real GDP is above (+) or below (−) the CBO's estimate of potential output, in percent.", formula: "100\\,\\frac{Y - Y^*}{Y^*}" },
  ytm: { title: "Yield to maturity", text: "The single discount rate, compounded at the coupon frequency, that makes the bond's cash flows worth its full (dirty) price.", formula: "P_{\\text{dirty}} = \\sum_k \\frac{CF_k}{(1 + y/m)^{m t_k}}", reference: "SIFMA Standard Formulas; Fabozzi, Bond Markets" },
  dirty: { title: "Dirty vs clean price", text: "Clean price is what's quoted; dirty (full) price is what you pay — clean plus the coupon interest accrued since the last payment.", formula: "P_{\\text{dirty}} = P_{\\text{clean}} + AI" },
  macaulay: { title: "Macaulay duration", text: "The present-value-weighted average time (in years) until you receive the bond's cash flows.", formula: "D_{\\text{mac}} = \\frac{1}{P}\\sum_k t_k\\,\\frac{CF_k}{(1+y/m)^{m t_k}}" },
  modified: { title: "Modified duration", text: "The percent price change for a 1-percentage-point move in yield (first-order).", formula: "D_{\\text{mod}} = \\frac{D_{\\text{mac}}}{1 + y/m} = -\\frac{1}{P}\\frac{\\partial P}{\\partial y}" },
  convexity: { title: "Convexity", text: "The curvature of the price–yield relation: how much duration itself changes as yields move. Positive convexity means gains exceed losses for equal-sized yield moves.", formula: "C = \\frac{1}{P}\\frac{\\partial^2 P}{\\partial y^2}" },
  dv01: { title: "DV01", text: "Dollar value of a basis point: how much the price (per 100 face, or for your notional) falls when yield rises 1 bp.", formula: "\\text{DV01} = D_{\\text{mod}}\\,P_{\\text{dirty}} \\times 10^{-4}" },
  krd: { title: "Key-rate durations", text: "Duration broken down by maturity: the price sensitivity to a 1 bp bump of the zero curve at one key tenor only (a tent-shaped bump). They add up to effective duration.", formula: "\\text{KRD}_i = \\frac{P(z - \\delta w_i) - P(z + \\delta w_i)}{2 P \\delta}", reference: "Ho (1992), J. Fixed Income 2(2)" },
  zspread: { title: "Z-spread", text: "The constant spread over the Treasury zero curve that discounts the cash flows to the market price. Positive = the bond yields more than Treasuries of matching timing.", formula: "P = \\sum_k CF_k\\, e^{-(z(t_k) + s)\\,t_k}" },
  fair: { title: "Curve fair value", text: "The price if every cash flow is discounted on today's Treasury zero curve. Rich/cheap is your price minus this fair value (positive = rich)." },
  effdur: { title: "Effective duration", text: "Duration measured by shifting the whole zero curve up and down 1 bp and repricing — the curve-based counterpart of modified duration.", formula: "D_{\\text{eff}} = \\frac{P_- - P_+}{2 P_0\\,\\Delta z}" },
  transforms: {
    title: "Transforms",
    text: "level = as published; y/y % = change vs a year earlier; diff = change vs the previous observation; m/m ann. = period-over-period change compounded to an annual rate.",
    formula: "\\text{m/m ann.} = 100\\left((x_t/x_{t-1})^{m} - 1\\right)",
  },
} satisfies Record<string, Info>;

/** Dashboard category display order and plain-English blurbs (keys are the backend's categories). */
export const CATEGORY_BLURB: Record<string, string> = {
  Inflation: "How fast prices are rising — what the Fed targets at 2% (PCE).",
  Labor: "Jobs and unemployment: the other half of the Fed's mandate, and the first place recessions show up.",
  Growth: "Growth: output, spending and sentiment.",
  "Policy & rates": "Policy and Treasury yields — the price of money across maturities.",
  // pre-rename backend keys (kept so an older API response still gets its blurb)
  Activity: "Growth: output, spending and sentiment.",
  Rates: "Policy and Treasury yields — the price of money across maturities.",
  "Credit & conditions": "What it costs companies to borrow over Treasuries, and overall funding stress.",
  Markets: "Volatility, the dollar and oil — cross-asset gauges of risk appetite.",
  "Money & Fed": "Money supply and the Fed's balance sheet.",
};

/** Plain names used on the page for the backend's category keys. The backend now sends
 * "Growth" and "Policy & rates" directly (shown as is); the old keys are relabelled. */
export const CATEGORY_LABEL: Record<string, string> = {
  Activity: "Growth",
  Rates: "Policy & rates",
};

export const TRANSFORM_LABEL: Record<string, string> = { level: "Level", yoy_pct: "y/y %", diff: "Change", mom_ann: "m/m ann." };
