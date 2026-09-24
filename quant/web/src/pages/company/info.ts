/**
 * Plain-English definitions (+ formula and source) for every number on the Company page.
 * Formulas mirror quant/src/ohcamel_quant/fundamentals/{ratios,scores,dcf}.py exactly.
 */
import type { Info } from "../../lib/glossary";

const PENMAN = "Penman (2013), Financial Statement Analysis and Security Valuation, 5th ed., ch. 9–12";
const KOLLER = "Koller, Goedhart & Wessels (2020), Valuation, 7th ed.";
const DAMODARAN = "Damodaran (2012), Investment Valuation, 3rd ed., ch. 15";

export const INFO: Record<string, Info> = {
  // ---------------------------------------------------------------- profile / multiples
  price: { title: "Price", text: "The latest traded price the server could fetch, used for market cap and every multiple." },
  market_cap: { title: "Market capitalization", text: "What the stock market says the equity is worth today: price times shares outstanding from the latest SEC cover page.", formula: "\\text{Cap} = P \\times S_{\\text{out}}" },
  ev: { title: "Enterprise value", text: "The price of the whole business — equity plus the debt a buyer would assume, minus the cash it would get. Uses book debt; excludes preferred stock, minority interest and leases.", formula: "EV = \\text{Cap} + \\text{Debt} - \\text{Cash}" },
  pe: { title: "Price / earnings", text: "How many dollars investors pay for one dollar of annual profit. Not shown when earnings are negative.", formula: "P/E = \\frac{\\text{Cap}}{\\text{Net income}}" },
  ev_ebitda: { title: "EV / EBITDA", text: "Enterprise value per dollar of operating cash earnings before depreciation — comparable across companies with different debt levels.", formula: "\\frac{EV}{\\text{EBIT} + D\\&A}" },
  ev_sales: { title: "EV / Sales", text: "Enterprise value per dollar of revenue; useful when profits are thin or negative.", formula: "\\frac{EV}{\\text{Revenue}}" },
  pb: { title: "Price / book", text: "Market value relative to the accountants' value of equity. Well above 1 means the market sees value the balance sheet does not record (brands, R&D, network effects).", formula: "\\frac{\\text{Cap}}{\\text{Shareholders' equity}}" },
  fcf_yield: { title: "Free-cash-flow yield", text: "Cash the business generated after investment, as a percent of what the equity costs today — an earnings yield that is hard to flatter with accounting.", formula: "\\frac{\\text{CFO} - \\text{Capex}}{\\text{Cap}}" },
  shareholder_yield: { title: "Shareholder yield", text: "Dividends plus buybacks as a percent of market cap: the cash actually returned to owners.", formula: "\\frac{\\text{Dividends} + \\text{Buybacks}}{\\text{Cap}}", reference: "Faber (2013), Shareholder Yield" },
  beta: { title: "CAPM beta", text: "How much the stock has moved with the S&P 500 (SPY): OLS slope of 60 monthly excess returns. Blume's adjustment pulls it a third of the way to 1, because betas mean-revert.", formula: "r_i - r_f = \\alpha + \\beta (r_m - r_f) + \\varepsilon,\\quad \\beta_{\\text{Blume}} = 0.67\\,\\beta + 0.33", reference: "Sharpe (1964); Blume (1971), J. Finance 26(1)" },
  range_52w: { title: "52-week range", text: "Where today's price sits between the lowest low and the highest high of the last year." },

  // ---------------------------------------------------------------- statements
  statements: { title: "Standardized statements", text: "Line items read from the company's XBRL filings with the SEC (10-K and 10-Q), mapped to a common chart of accounts. Quarters that companies only report year-to-date are derived as differences (Q4 = FY − 9M). Missing items stay blank — nothing is imputed.", reference: "SEC EDGAR companyfacts API" },
  yoy: { title: "Year-over-year growth", text: "Change against the period one year earlier (same fiscal quarter for quarterly data). Blank when the earlier value is missing or not positive.", formula: "g_t = \\frac{x_t}{x_{t-1y}} - 1" },
  ttm: { title: "Trailing twelve months", text: "The last four consecutive fiscal quarters added together, so every column is a full year that ends at a different quarter. Balance-sheet items are the latest quarter-end values." },

  // ---------------------------------------------------------------- ratios
  gross_margin: { title: "Gross margin", text: "What is left of each sales dollar after the direct cost of producing it — a gauge of pricing power.", formula: "\\frac{\\text{Gross profit}}{\\text{Revenue}}" },
  operating_margin: { title: "Operating margin", text: "Profit from the core business per sales dollar, before interest and taxes.", formula: "\\frac{\\text{EBIT}}{\\text{Revenue}}" },
  ebitda_margin: { title: "EBITDA margin", text: "Operating profit before depreciation and amortization, per sales dollar.", formula: "\\frac{\\text{EBITDA}}{\\text{Revenue}}" },
  net_margin: { title: "Net margin", text: "Bottom-line profit per sales dollar, after interest and taxes.", formula: "\\frac{\\text{Net income}}{\\text{Revenue}}" },
  fcf_margin: { title: "FCF margin", text: "Free cash flow per sales dollar — how much revenue turns into spendable cash.", formula: "\\frac{\\text{CFO} - \\text{Capex}}{\\text{Revenue}}" },
  roe: { title: "Return on equity", text: "Profit earned on the shareholders' capital, using the average of opening and closing equity.", formula: "ROE = \\frac{NI}{\\tfrac12(E_{t-1} + E_t)}", reference: PENMAN },
  roa: { title: "Return on assets", text: "Profit per dollar of assets, however they are financed.", formula: "ROA = \\frac{NI}{\\tfrac12(TA_{t-1} + TA_t)}", reference: PENMAN },
  roic: { title: "Return on invested capital", text: "After-tax operating profit per dollar of capital put into the business by lenders and owners. Above the cost of capital, growth creates value.", formula: "ROIC = \\frac{EBIT(1-t)}{\\overline{\\text{Debt} + \\text{Equity} - \\text{Cash}}}", reference: KOLLER + ", ch. 11" },
  effective_tax_rate: { title: "Effective tax rate", text: "Income tax expense as a share of pre-tax income, clipped to 0–50%.", formula: "t = \\frac{\\text{Tax}}{\\text{Pre-tax income}}" },
  debt_to_equity: { title: "Debt / equity", text: "Book debt per dollar of book equity. Blank when equity is negative (e.g. after large buybacks).", formula: "\\frac{\\text{Total debt}}{\\text{Equity}}" },
  net_debt_to_ebitda: { title: "Net debt / EBITDA", text: "Years of operating cash earnings needed to repay debt net of cash. Below zero means more cash than debt.", formula: "\\frac{\\text{Debt} - \\text{Cash}}{\\text{EBITDA}}" },
  interest_coverage: { title: "Interest coverage", text: "How many times operating profit covers the interest bill.", formula: "\\frac{\\text{EBIT}}{\\text{Interest expense}}" },
  liabilities_to_assets: { title: "Liabilities / assets", text: "Share of assets financed by obligations rather than owners.", formula: "\\frac{\\text{Total liabilities}}{\\text{Total assets}}" },
  current_ratio: { title: "Current ratio", text: "Short-term assets per dollar of bills due within a year.", formula: "\\frac{\\text{Current assets}}{\\text{Current liabilities}}" },
  quick_ratio: { title: "Quick ratio", text: "The acid test: short-term assets excluding inventory, per dollar of current liabilities.", formula: "\\frac{CA - \\text{Inventory}}{CL}" },
  dupont: { title: "DuPont decomposition", text: "Splits return on equity into profitability, efficiency and leverage — three very different ways to earn a high ROE.", formula: "ROE = \\underbrace{\\tfrac{NI}{\\text{Sales}}}_{\\text{margin}} \\times \\underbrace{\\tfrac{\\text{Sales}}{\\overline{TA}}}_{\\text{turnover}} \\times \\underbrace{\\tfrac{\\overline{TA}}{\\overline{E}}}_{\\text{leverage}}", reference: "Soldofsky (1968); " + PENMAN },
  dso: { title: "Days sales outstanding", text: "How many days of revenue are waiting to be collected from customers.", formula: "\\frac{\\overline{\\text{Receivables}}}{\\text{Revenue}} \\times 365" },
  dio: { title: "Days inventory outstanding", text: "How many days of production cost sit in inventory.", formula: "\\frac{\\overline{\\text{Inventory}}}{\\text{COGS}} \\times 365" },
  growth: { title: "Growth vs prior year", text: "Change in revenue, EPS, free cash flow and net income against the same period a year earlier. Blank when the base is not positive.", formula: "g = \\frac{x_t}{x_{t-1}} - 1" },
  cagr: { title: "Compound annual growth", text: "The constant yearly rate that links the value n fiscal years ago to the latest fiscal year.", formula: "\\text{CAGR}_n = \\left(\\frac{x_t}{x_{t-n}}\\right)^{1/n} - 1" },

  // ---------------------------------------------------------------- scores
  piotroski: { title: "Piotroski F-score", text: "Nine pass/fail accounting tests of profitability, balance-sheet strength and efficiency. Piotroski found that among cheap stocks, high scorers (8–9) went on to beat low scorers (0–2) by a wide margin.", formula: "F = \\sum_{j=1}^{9} F_j,\\quad F_j \\in \\{0, 1\\}", reference: "Piotroski (2000), J. Accounting Research 38 Suppl., 1–41" },
  altman_z: { title: "Altman Z-score", text: "A 1968 discriminant model that separated manufacturers that went bankrupt from those that did not. Below 1.81 is the distress zone, above 2.99 safe.", formula: "Z = 1.2X_1 + 1.4X_2 + 3.3X_3 + 0.6X_4 + 1.0X_5", reference: "Altman (1968), J. Finance 23(4), 589–609" },
  altman_z2: { title: "Altman Z″ (non-manufacturers)", text: "Altman's later variant without the sales-turnover term and using book equity, suited to service and non-US firms. Below 1.10 distress, above 2.60 safe.", formula: "Z'' = 6.56X_1 + 3.26X_2 + 6.72X_3 + 1.05X_4", reference: "Altman, Hartzell & Peck (1995); Altman & Hotchkiss (2006) ch. 12" },
  beneish: { title: "Beneish M-score", text: "A probit model estimated on companies caught manipulating earnings. Eight indices compare this year with last; an M above −1.78 resembles the manipulators (−2.22 is a stricter screen). A statistical flag, not an accusation.", formula: "M = -4.84 + 0.920\\,DSRI + 0.528\\,GMI + 0.404\\,AQI + 0.892\\,SGI + 0.115\\,DEPI - 0.172\\,SGAI + 4.679\\,TATA - 0.327\\,LVGI", reference: "Beneish (1999), Financial Analysts Journal 55(5), 24–36" },
  sloan: { title: "Sloan accruals ratio", text: "The part of earnings not backed by operating cash, scaled by average assets. High accruals tend to reverse, so earnings quality is lower; Sloan's extreme deciles sit beyond ±10% of assets.", formula: "\\frac{NI_t - CFO_t}{\\tfrac12(TA_t + TA_{t-1})}", reference: "Sloan (1996), Accounting Review 71(3); Hribar & Collins (2002)" },
  ohlson: { title: "Ohlson O-score", text: "A logit bankruptcy model on nine accounting variables; the output converts to a one-year probability of failure.", formula: "P = \\frac{1}{1 + e^{-O}}", reference: "Ohlson (1980), J. Accounting Research 18(1), 109–131" },

  // ---------------------------------------------------------------- DCF
  dcf: { title: "Two-stage FCFF DCF", text: "Projects free cash flow to the firm for N years, fading growth linearly to a terminal rate, then values everything after year N as a growing perpetuity. Discounted at the WACC, minus net debt, divided by shares.", formula: "EV = \\sum_{i=1}^{N} \\frac{F_i}{(1+W)^i} + \\frac{F_N(1+g_T)}{(W - g_T)(1+W)^N}", reference: KOLLER + ", ch. 10–14; " + DAMODARAN },
  base_fcff: { title: "Base free cash flow to the firm", text: "The cash the business produced for all capital providers in the base year: operating cash flow, plus after-tax interest (so it is unlevered), minus capital expenditure.", formula: "FCFF_0 = CFO + \\text{Interest}(1-t) - \\text{Capex}", reference: DAMODARAN },
  years: { title: "Explicit forecast years", text: "How many years are projected one by one before the perpetuity takes over. Growth fades linearly from the year-1 rate to the terminal rate over this span." },
  initial_growth: { title: "Year-1 growth", text: "Growth of free cash flow in the first forecast year. The default is the 5-year revenue CAGR, clipped between the terminal rate and 25%.", formula: "g_i = g_0 + (g_T - g_0)\\frac{i-1}{N}" },
  terminal_growth: { title: "Terminal growth", text: "Perpetual growth after the forecast. The default is 10-year breakeven inflation (FRED T10YIE): zero real growth, a deliberately modest anchor. It must stay below the WACC.", reference: "Gordon (1962)" },
  risk_free: { title: "Risk-free rate", text: "The yield on the 10-year US Treasury (FRED DGS10): what an investor earns without taking equity risk, matched to the long life of equity cash flows." },
  erp: { title: "Equity risk premium", text: "The extra annual return investors have historically earned for holding the stock market over T-bills: the full-sample mean of Ken French's market excess return, annualized.", formula: "ERP = 12 \\times \\overline{(R_m - R_f)}_{\\text{monthly}}", reference: "Kenneth R. French Data Library" },
  cost_of_equity: { title: "Cost of equity (CAPM)", text: "The return shareholders require for bearing this stock's market risk.", formula: "k_e = r_f + \\beta \\times ERP", reference: "Sharpe (1964); Lintner (1965)" },
  cost_of_debt: { title: "Pre-tax cost of debt", text: "Interest expense over average total debt, never below the risk-free rate (a company cannot borrow cheaper than the Treasury).", formula: "k_d = \\max\\left(\\frac{\\text{Interest}}{\\overline{\\text{Debt}}},\\ r_f\\right)" },
  tax_rate: { title: "Tax rate", text: "The average effective tax rate of the last three fiscal years, clipped to 0–50%. It shields interest in the WACC and in FCFF." },
  wacc: { title: "Weighted average cost of capital", text: "The blended return required by shareholders and lenders, weighted by market equity and book debt. Interest is tax-deductible, hence (1 − t).", formula: "W = \\tfrac{E}{D+E}\\,k_e + \\tfrac{D}{D+E}\\,k_d(1-t)", reference: "Modigliani & Miller (1963)" },
  terminal_share: { title: "Terminal-value share", text: "How much of the enterprise value comes from years after the explicit forecast. Above ~75%, the answer mostly reflects the perpetuity assumptions — check the sensitivity grid." },
  sensitivity: { title: "WACC × terminal-growth sensitivity", text: "Value per share recomputed on a grid around the base case (growth path re-faded to each terminal rate). Green cells are above today's price, vermilion below." },
  reverse_dcf: { title: "Reverse DCF", text: "Instead of asking what the stock is worth, ask what growth today's price already assumes: the constant FCFF growth over the forecast years that makes the DCF equity value equal the market cap.", formula: "\\text{solve } g:\\ \\text{Equity}(g, \\dots, g) = \\text{Cap}", reference: "Mauboussin & Rappaport (2001), Expectations Investing" },
  exit_multiple: { title: "Implied exit multiple", text: "The terminal value divided by the final forecast year's FCFF — a sanity check against multiples seen in the market.", formula: "\\frac{TV_N}{F_N} = \\frac{1+g_T}{W - g_T}" },
};
