/**
 * Consolidated bibliography for every model in OhCamel Quant. Keys are cited by the model
 * entries in ./models.ts; the Methodology page renders each once, alphabetically.
 * Citations follow the docstrings in quant/src/ohcamel_quant/**.
 */
export interface Ref {
  /** "Author, A. & Author, B." */
  authors: string;
  year: string;
  title: string;
  venue: string;
  href?: string;
}

export const REFS: Record<string, Ref> = {
  acerbi_szekely_2014: { authors: "Acerbi, C. & Szekely, B.", year: "2014", title: "Backtesting expected shortfall", venue: "Risk, December" },
  acerbi_tasche_2002: { authors: "Acerbi, C. & Tasche, D.", year: "2002", title: "On the coherence of expected shortfall", venue: "Journal of Banking & Finance 26(7), 1487–1503" },
  acar_2005: { authors: "Acar, U. A.", year: "2005", title: "Self-Adjusting Computation", venue: "PhD thesis, Carnegie Mellon University" },
  ait_sahalia_lo_1998: { authors: "Aït-Sahalia, Y. & Lo, A. W.", year: "1998", title: "Nonparametric estimation of state-price densities implicit in financial asset prices", venue: "Journal of Finance 53(2), 499–547" },
  altman_1968: { authors: "Altman, E. I.", year: "1968", title: "Financial ratios, discriminant analysis and the prediction of corporate bankruptcy", venue: "Journal of Finance 23(4), 589–609" },
  altman_1995: { authors: "Altman, E. I., Hartzell, J. & Peck, M.", year: "1995", title: "Emerging markets corporate bonds: a scoring system", venue: "Salomon Brothers; see Altman & Hotchkiss (2006), Corporate Financial Distress and Bankruptcy, 3rd ed., ch. 12" },
  antonacci_2014: { authors: "Antonacci, G.", year: "2014", title: "Dual Momentum Investing", venue: "McGraw-Hill" },
  asness_2012: { authors: "Asness, C., Frazzini, A. & Pedersen, L. H.", year: "2012", title: "Leverage aversion and risk parity", venue: "Financial Analysts Journal 68(1), 47–59" },
  bailey_2012: { authors: "Bailey, D. H. & López de Prado, M.", year: "2012", title: "The Sharpe ratio efficient frontier", venue: "Journal of Risk 15(2), 3–44" },
  bailey_2014: { authors: "Bailey, D. H. & López de Prado, M.", year: "2014", title: "The deflated Sharpe ratio: correcting for selection bias, backtest overfitting and non-normality", venue: "Journal of Portfolio Management 40(5), 94–107" },
  bailey_2017: { authors: "Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J.", year: "2017", title: "The probability of backtest overfitting", venue: "Journal of Computational Finance 20(4), 39–69" },
  barone_adesi_1999: { authors: "Barone-Adesi, G., Giannopoulos, K. & Vosper, L.", year: "1999", title: "VaR without correlations for portfolios of derivative securities", venue: "Journal of Futures Markets 19(5), 583–602" },
  bcbs_1996: { authors: "Basel Committee on Banking Supervision", year: "1996", title: "Supervisory framework for the use of “backtesting” in conjunction with the internal models approach to market risk capital requirements", venue: "Bank for International Settlements" },
  beneish_1999: { authors: "Beneish, M. D.", year: "1999", title: "The detection of earnings manipulation", venue: "Financial Analysts Journal 55(5), 24–36" },
  black_1976: { authors: "Black, F.", year: "1976", title: "The pricing of commodity contracts", venue: "Journal of Financial Economics 3(1–2), 167–179" },
  black_litterman_1992: { authors: "Black, F. & Litterman, R.", year: "1992", title: "Global portfolio optimization", venue: "Financial Analysts Journal 48(5), 28–43" },
  blume_1971: { authors: "Blume, M. E.", year: "1971", title: "On the assessment of risk", venue: "Journal of Finance 26(1), 1–10" },
  bollerslev_1986: { authors: "Bollerslev, T.", year: "1986", title: "Generalized autoregressive conditional heteroskedasticity", venue: "Journal of Econometrics 31(3), 307–327" },
  boudt_2008: { authors: "Boudt, K., Peterson, B. & Croux, C.", year: "2008", title: "Estimation and decomposition of downside risk for portfolios with non-normal returns", venue: "Journal of Risk 11(2), 79–103" },
  breeden_litzenberger_1978: { authors: "Breeden, D. T. & Litzenberger, R. H.", year: "1978", title: "Prices of state-contingent claims implicit in option prices", venue: "Journal of Business 51(4), 621–651" },
  burghardt_lane_1990: { authors: "Burghardt, G. & Lane, M.", year: "1990", title: "How to tell if options are cheap", venue: "Journal of Portfolio Management 16(2), 72–78" },
  carhart_1997: { authors: "Carhart, M. M.", year: "1997", title: "On persistence in mutual fund performance", venue: "Journal of Finance 52(1), 57–82" },
  carino_1999: { authors: "Cariño, D. R.", year: "1999", title: "Combining attribution effects over time", venue: "Journal of Performance Measurement 3(4), 5–14" },
  carr_wu_2009: { authors: "Carr, P. & Wu, L.", year: "2009", title: "Variance risk premiums", venue: "Review of Financial Studies 22(3), 1311–1341" },
  cboe_vix: { authors: "Cboe Global Markets", year: "2019", title: "Cboe Volatility Index (VIX) — Mathematics Methodology (white paper)", venue: "Cboe", href: "https://cdn.cboe.com/api/global/us_indices/governance/VIX_Methodology.pdf" },
  chen_2010: { authors: "Chen, Y., Wiesel, A., Eldar, Y. C. & Hero, A. O.", year: "2010", title: "Shrinkage algorithms for MMSE covariance estimation", venue: "IEEE Transactions on Signal Processing 58(10), 5016–5029" },
  choueifaty_2008: { authors: "Choueifaty, Y. & Coignard, Y.", year: "2008", title: "Toward maximum diversification", venue: "Journal of Portfolio Management 35(1), 40–51" },
  christoffersen_1998: { authors: "Christoffersen, P. F.", year: "1998", title: "Evaluating interval forecasts", venue: "International Economic Review 39(4), 841–862" },
  cornish_fisher_1937: { authors: "Cornish, E. A. & Fisher, R. A.", year: "1937", title: "Moments and cumulants in the specification of distributions", venue: "Revue de l'Institut International de Statistique 5(4), 307–320" },
  corrado_miller_1996: { authors: "Corrado, C. J. & Miller, T. W.", year: "1996", title: "A note on a simple, accurate formula to compute implied standard deviations", venue: "Journal of Banking & Finance 20(3), 595–603" },
  damodaran_2012: { authors: "Damodaran, A.", year: "2012", title: "Investment Valuation, 3rd ed.", venue: "Wiley" },
  demeterfi_1999: { authors: "Demeterfi, K., Derman, E., Kamal, M. & Zou, J.", year: "1999", title: "More than you ever wanted to know about volatility swaps", venue: "Goldman Sachs Quantitative Strategies Research Notes" },
  demiguel_2009: { authors: "DeMiguel, V., Garlappi, L. & Uppal, R.", year: "2009", title: "Optimal versus naive diversification: how inefficient is the 1/N portfolio strategy?", venue: "Review of Financial Studies 22(5), 1915–1953" },
  engle_manganelli_2004: { authors: "Engle, R. F. & Manganelli, S.", year: "2004", title: "CAViaR: conditional autoregressive value at risk by regression quantiles", venue: "Journal of Business & Economic Statistics 22(4), 367–381" },
  engle_granger_1987: { authors: "Engle, R. F. & Granger, C. W. J.", year: "1987", title: "Co-integration and error correction: representation, estimation, and testing", venue: "Econometrica 55(2), 251–276" },
  estrella_mishkin_1998: { authors: "Estrella, A. & Mishkin, F. S.", year: "1998", title: "Predicting U.S. recessions: financial variables as leading indicators", venue: "Review of Economics and Statistics 80(1), 45–61" },
  faber_2007: { authors: "Faber, M. T.", year: "2007", title: "A quantitative approach to tactical asset allocation", venue: "Journal of Wealth Management 9(4), 69–79" },
  fama_french_1993: { authors: "Fama, E. F. & French, K. R.", year: "1993", title: "Common risk factors in the returns on stocks and bonds", venue: "Journal of Financial Economics 33(1), 3–56" },
  fama_french_2015: { authors: "Fama, E. F. & French, K. R.", year: "2015", title: "A five-factor asset pricing model", venue: "Journal of Financial Economics 116(1), 1–22" },
  fissler_ziegel_2016: { authors: "Fissler, T. & Ziegel, J. F.", year: "2016", title: "Higher order elicitability and Osband's principle", venue: "Annals of Statistics 44(4), 1680–1707" },
  frazzini_pedersen_2014: { authors: "Frazzini, A. & Pedersen, L. H.", year: "2014", title: "Betting against beta", venue: "Journal of Financial Economics 111(1), 1–25" },
  gatev_2006: { authors: "Gatev, E., Goetzmann, W. N. & Rouwenhorst, K. G.", year: "2006", title: "Pairs trading: performance of a relative-value arbitrage rule", venue: "Review of Financial Studies 19(3), 797–827" },
  gatheral_2004: { authors: "Gatheral, J.", year: "2004", title: "A parsimonious arbitrage-free implied volatility parameterization with application to the valuation of volatility derivatives", venue: "Global Derivatives & Risk Management, Madrid" },
  gatheral_jacquier_2014: { authors: "Gatheral, J. & Jacquier, A.", year: "2014", title: "Arbitrage-free SVI volatility surfaces", venue: "Quantitative Finance 14(1), 59–71" },
  glosten_1993: { authors: "Glosten, L. R., Jagannathan, R. & Runkle, D. E.", year: "1993", title: "On the relation between the expected value and the volatility of the nominal excess return on stocks", venue: "Journal of Finance 48(5), 1779–1801" },
  gordon_1962: { authors: "Gordon, M. J.", year: "1962", title: "The Investment, Financing and Valuation of the Corporation", venue: "Irwin" },
  grinold_kahn_2000: { authors: "Grinold, R. C. & Kahn, R. N.", year: "2000", title: "Active Portfolio Management, 2nd ed.", venue: "McGraw-Hill" },
  gurkaynak_2007: { authors: "Gürkaynak, R. S., Sack, B. & Wright, J. H.", year: "2007", title: "The U.S. Treasury yield curve: 1961 to the present", venue: "Journal of Monetary Economics 54(8), 2291–2304" },
  hamilton_1989: { authors: "Hamilton, J. D.", year: "1989", title: "A new approach to the economic analysis of nonstationary time series and the business cycle", venue: "Econometrica 57(2), 357–384" },
  hansen_2005: { authors: "Hansen, P. R.", year: "2005", title: "A test for superior predictive ability", venue: "Journal of Business & Economic Statistics 23(4), 365–380" },
  harvey_2018: { authors: "Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M. & Van Hemert, O.", year: "2018", title: "The impact of volatility targeting", venue: "Journal of Portfolio Management 45(1), 14–33" },
  haug_2007: { authors: "Haug, E. G.", year: "2007", title: "The Complete Guide to Option Pricing Formulas, 2nd ed.", venue: "McGraw-Hill" },
  he_litterman_1999: { authors: "He, G. & Litterman, R.", year: "1999", title: "The intuition behind Black–Litterman model portfolios", venue: "Goldman Sachs Investment Management Research" },
  ho_1992: { authors: "Ho, T. S. Y.", year: "1992", title: "Key rate durations: measures of interest rate risks", venue: "Journal of Fixed Income 2(2), 29–44" },
  hribar_collins_2002: { authors: "Hribar, P. & Collins, D. W.", year: "2002", title: "Errors in estimating accruals: implications for empirical research", venue: "Journal of Accounting Research 40(1), 105–134" },
  hull_ofod: { authors: "Hull, J. C.", year: "2022", title: "Options, Futures, and Other Derivatives, 11th ed.", venue: "Pearson (ch. 4, the bootstrap method)" },
  idzorek_2005: { authors: "Idzorek, T. M.", year: "2005", title: "A step-by-step guide to the Black–Litterman model", venue: "Zephyr Associates working paper" },
  jegadeesh_titman_1993: { authors: "Jegadeesh, N. & Titman, S.", year: "1993", title: "Returns to buying winners and selling losers: implications for stock market efficiency", venue: "Journal of Finance 48(1), 65–91" },
  jensen_1968: { authors: "Jensen, M. C.", year: "1968", title: "The performance of mutual funds in the period 1945–1964", venue: "Journal of Finance 23(2), 389–416" },
  jorion_1986: { authors: "Jorion, P.", year: "1986", title: "Bayes–Stein estimation for portfolio analysis", venue: "Journal of Financial and Quantitative Analysis 21(3), 279–292" },
  jorion_2007: { authors: "Jorion, P.", year: "2007", title: "Value at Risk: The New Benchmark for Managing Financial Risk, 3rd ed.", venue: "McGraw-Hill" },
  koller_2020: { authors: "Koller, T., Goedhart, M. & Wessels, D.", year: "2020", title: "Valuation: Measuring and Managing the Value of Companies, 7th ed.", venue: "McKinsey & Company / Wiley" },
  kupiec_1995: { authors: "Kupiec, P. H.", year: "1995", title: "Techniques for verifying the accuracy of risk measurement models", venue: "Journal of Derivatives 3(2), 73–84" },
  kupiec_1998: { authors: "Kupiec, P. H.", year: "1998", title: "Stress testing in a value at risk framework", venue: "Journal of Derivatives 6(1), 7–24" },
  ledoit_wolf_2004a: { authors: "Ledoit, O. & Wolf, M.", year: "2004", title: "Honey, I shrunk the sample covariance matrix", venue: "Journal of Portfolio Management 30(4), 110–119" },
  ledoit_wolf_2004b: { authors: "Ledoit, O. & Wolf, M.", year: "2004", title: "A well-conditioned estimator for large-dimensional covariance matrices", venue: "Journal of Multivariate Analysis 88(2), 365–411" },
  ledoit_wolf_2008: { authors: "Ledoit, O. & Wolf, M.", year: "2008", title: "Robust performance hypothesis testing with the Sharpe ratio", venue: "Journal of Empirical Finance 15(5), 850–859" },
  lehmann_1990: { authors: "Lehmann, B. N.", year: "1990", title: "Fads, martingales, and market efficiency", venue: "Quarterly Journal of Economics 105(1), 1–28" },
  lintner_1965: { authors: "Lintner, J.", year: "1965", title: "The valuation of risk assets and the selection of risky investments in stock portfolios and capital budgets", venue: "Review of Economics and Statistics 47(1), 13–37" },
  litterman_scheinkman_1991: { authors: "Litterman, R. & Scheinkman, J.", year: "1991", title: "Common factors affecting bond returns", venue: "Journal of Fixed Income 1(1), 54–61" },
  lo_2002: { authors: "Lo, A. W.", year: "2002", title: "The statistics of Sharpe ratios", venue: "Financial Analysts Journal 58(4), 36–52" },
  lopez_de_prado_2016: { authors: "López de Prado, M.", year: "2016", title: "Building diversified portfolios that outperform out of sample", venue: "Journal of Portfolio Management 42(4), 59–69" },
  lopez_de_prado_2020: { authors: "López de Prado, M.", year: "2020", title: "Machine Learning for Asset Managers", venue: "Cambridge University Press (ch. 2, denoising)" },
  maillard_2010: { authors: "Maillard, S., Roncalli, T. & Teïletche, J.", year: "2010", title: "The properties of equally weighted risk contribution portfolios", venue: "Journal of Portfolio Management 36(4), 60–70" },
  markowitz_1952: { authors: "Markowitz, H.", year: "1952", title: "Portfolio selection", venue: "Journal of Finance 7(1), 77–91" },
  mauboussin_2001: { authors: "Mauboussin, M. J. & Rappaport, A.", year: "2001", title: "Expectations Investing: Reading Stock Prices for Better Returns", venue: "Harvard Business School Press" },
  mcneil_frey_2000: { authors: "McNeil, A. J. & Frey, R.", year: "2000", title: "Estimation of tail-related risk measures for heteroscedastic financial time series: an extreme value approach", venue: "Journal of Empirical Finance 7(3–4), 271–300" },
  mcneil_2015: { authors: "McNeil, A. J., Frey, R. & Embrechts, P.", year: "2015", title: "Quantitative Risk Management, revised ed.", venue: "Princeton University Press" },
  memmel_2003: { authors: "Memmel, C.", year: "2003", title: "Performance hypothesis testing with the Sharpe ratio", venue: "Finance Letters 1, 21–23" },
  merton_1973: { authors: "Merton, R. C.", year: "1973", title: "Theory of rational option pricing", venue: "Bell Journal of Economics and Management Science 4(1), 141–183" },
  meucci_2009: { authors: "Meucci, A.", year: "2009", title: "Managing diversification", venue: "Risk 22(5), 74–79" },
  minsky_2015: { authors: "Minsky, Y. et al.", year: "2015", title: "Incremental: a library for incremental computations", venue: "Jane Street", href: "https://github.com/janestreet/incremental" },
  moreira_muir_2017: { authors: "Moreira, A. & Muir, T.", year: "2017", title: "Volatility-managed portfolios", venue: "Journal of Finance 72(4), 1611–1644" },
  moskowitz_2012: { authors: "Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H.", year: "2012", title: "Time series momentum", venue: "Journal of Financial Economics 104(2), 228–250" },
  nelson_siegel_1987: { authors: "Nelson, C. R. & Siegel, A. F.", year: "1987", title: "Parsimonious modeling of yield curves", venue: "Journal of Business 60(4), 473–489" },
  newey_west_1987: { authors: "Newey, W. K. & West, K. D.", year: "1987", title: "A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix", venue: "Econometrica 55(3), 703–708" },
  newey_west_1994: { authors: "Newey, W. K. & West, K. D.", year: "1994", title: "Automatic lag selection in covariance matrix estimation", venue: "Review of Economic Studies 61(4), 631–653" },
  ohlson_1980: { authors: "Ohlson, J. A.", year: "1980", title: "Financial ratios and the probabilistic prediction of bankruptcy", venue: "Journal of Accounting Research 18(1), 109–131" },
  parkinson_1980: { authors: "Parkinson, M.", year: "1980", title: "The extreme value method for estimating the variance of the rate of return", venue: "Journal of Business 53(1), 61–65" },
  garman_klass_1980: { authors: "Garman, M. B. & Klass, M. J.", year: "1980", title: "On the estimation of security price volatilities from historical data", venue: "Journal of Business 53(1), 67–78" },
  rogers_satchell_1991: { authors: "Rogers, L. C. G. & Satchell, S. E.", year: "1991", title: "Estimating variance from high, low and closing prices", venue: "Annals of Applied Probability 1(4), 504–512" },
  yang_zhang_2000: { authors: "Yang, D. & Zhang, Q.", year: "2000", title: "Drift-independent volatility estimation based on high, low, open, and close prices", venue: "Journal of Business 73(3), 477–491" },
  patton_2019: { authors: "Patton, A. J., Ziegel, J. F. & Chen, R.", year: "2019", title: "Dynamic semiparametric models for expected shortfall (and value-at-risk)", venue: "Journal of Econometrics 211(2), 388–413" },
  penman_2013: { authors: "Penman, S. H.", year: "2013", title: "Financial Statement Analysis and Security Valuation, 5th ed.", venue: "McGraw-Hill" },
  piotroski_2000: { authors: "Piotroski, J. D.", year: "2000", title: "Value investing: the use of historical financial statement information to separate winners from losers", venue: "Journal of Accounting Research 38 (Supplement), 1–41" },
  politis_romano_1994: { authors: "Politis, D. N. & Romano, J. P.", year: "1994", title: "The stationary bootstrap", venue: "Journal of the American Statistical Association 89(428), 1303–1313" },
  politis_white_2004: { authors: "Politis, D. N. & White, H.", year: "2004", title: "Automatic block-length selection for the dependent bootstrap", venue: "Econometric Reviews 23(1), 53–70 (corrected by Patton, Politis & White 2009)" },
  raffinot_2018: { authors: "Raffinot, T.", year: "2018", title: "The hierarchical equal risk contribution portfolio", venue: "SSRN 3237540" },
  riskmetrics_1996: { authors: "J.P. Morgan / Reuters", year: "1996", title: "RiskMetrics — Technical Document, 4th ed.", venue: "J.P. Morgan" },
  rockafellar_uryasev_2000: { authors: "Rockafellar, R. T. & Uryasev, S.", year: "2000", title: "Optimization of conditional value-at-risk", venue: "Journal of Risk 2(3), 21–41" },
  sahm_2019: { authors: "Sahm, C.", year: "2019", title: "Direct stimulus payments to individuals", venue: "In Recession Ready, The Hamilton Project / Brookings" },
  sharpe_1964: { authors: "Sharpe, W. F.", year: "1964", title: "Capital asset prices: a theory of market equilibrium under conditions of risk", venue: "Journal of Finance 19(3), 425–442" },
  sharpe_1994: { authors: "Sharpe, W. F.", year: "1994", title: "The Sharpe ratio", venue: "Journal of Portfolio Management 21(1), 49–58" },
  sloan_1996: { authors: "Sloan, R. G.", year: "1996", title: "Do stock prices fully reflect information in accruals and cash flows about future earnings?", venue: "The Accounting Review 71(3), 289–315" },
  sortino_price_1994: { authors: "Sortino, F. A. & Price, L. N.", year: "1994", title: "Performance measurement in a downside risk framework", venue: "Journal of Investing 3(3), 59–64" },
  spinu_2013: { authors: "Spinu, F.", year: "2013", title: "An algorithm for computing risk parity weights", venue: "SSRN 2297383" },
  svensson_1994: { authors: "Svensson, L. E. O.", year: "1994", title: "Estimating and interpreting forward interest rates: Sweden 1992–1994", venue: "NBER Working Paper 4871" },
  tasche_2000: { authors: "Tasche, D.", year: "2000", title: "Risk contributions and performance measurement", venue: "Working paper, TU München" },
  taylor_1993: { authors: "Taylor, J. B.", year: "1993", title: "Discretion versus policy rules in practice", venue: "Carnegie-Rochester Conference Series on Public Policy 39, 195–214" },
  keating_2002: { authors: "Keating, C. & Shadwick, W. F.", year: "2002", title: "A universal performance measure", venue: "Journal of Performance Measurement 6(3), 59–84" },
  modigliani_1997: { authors: "Modigliani, F. & Modigliani, L.", year: "1997", title: "Risk-adjusted performance", venue: "Journal of Portfolio Management 23(2), 45–54" },
  modigliani_miller_1963: { authors: "Modigliani, F. & Miller, M. H.", year: "1963", title: "Corporate income taxes and the cost of capital: a correction", venue: "American Economic Review 53(3), 433–443" },
  kim_1994: { authors: "Kim, C.-J.", year: "1994", title: "Dynamic linear models with Markov-switching", venue: "Journal of Econometrics 60(1–2), 1–22" },
  fabozzi: { authors: "Fabozzi, F. J.", year: "2015", title: "Bond Markets, Analysis, and Strategies, 9th ed.", venue: "Pearson" },
  yellen_2012: { authors: "Yellen, J. L.", year: "2012", title: "Perspectives on monetary policy", venue: "Speech at the Boston Economic Club, June 6" },
  stoll_1969: { authors: "Stoll, H. R.", year: "1969", title: "The relationship between put and call option prices", venue: "Journal of Finance 24(5), 801–824" },
  zeliade_2009: { authors: "Zeliade Systems (De Marco, S. & Martini, C.)", year: "2009", title: "Quasi-explicit calibration of Gatheral's SVI model", venue: "Zeliade white paper ZWP-0005" },
  pardo_2008: { authors: "Pardo, R.", year: "2008", title: "The Evaluation and Optimization of Trading Strategies, 2nd ed.", venue: "Wiley (ch. 11)" },
  laloux_1999: { authors: "Laloux, L., Cizeau, P., Bouchaud, J.-P. & Potters, M.", year: "1999", title: "Noise dressing of financial correlation matrices", venue: "Physical Review Letters 83(7), 1467–1470" },
  huij_verbeek_2009: { authors: "Huij, J. & Verbeek, M.", year: "2009", title: "On the use of multifactor models to evaluate mutual fund performance", venue: "Journal of Financial and Quantitative Analysis 44(2), 297–314" },
  sec_edgar: { authors: "U.S. Securities and Exchange Commission", year: "2024", title: "EDGAR application programming interfaces (XBRL companyfacts, submissions)", venue: "sec.gov", href: "https://www.sec.gov/edgar/sec-api-documentation" },
};

export function citation(key: string): string {
  const r = REFS[key];
  if (!r) return key;
  return `${r.authors} (${r.year}). ${r.title.replace(/\.$/, "")}. ${r.venue.replace(/\.$/, "")}.`;
}

/** "Author (year)" short form for inline chips. */
export function shortCite(key: string): string {
  const r = REFS[key];
  if (!r) return key;
  const first = r.authors.split(",")[0].replace(/ et al\.?/, "");
  const multi = /&| et al/.test(r.authors) ? (r.authors.split("&").length > 2 || r.authors.split(",").length > 3 || / et al/.test(r.authors) ? " et al." : ` & ${r.authors.split("&")[1].trim().split(",")[0]}`) : "";
  return `${first}${multi} (${r.year})`;
}
