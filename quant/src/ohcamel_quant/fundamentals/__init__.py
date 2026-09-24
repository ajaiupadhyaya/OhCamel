"""Company fundamentals and valuation from SEC XBRL filings.

* :mod:`.statements` -- income statement, balance sheet, cash flow (annual,
  quarterly, TTM) with YoY growth;
* :mod:`.ratios` -- margins, returns, DuPont, leverage, liquidity, efficiency,
  growth, per-share and market multiples;
* :mod:`.scores` -- Piotroski F, Altman Z / Z'', Beneish M, Sloan accruals,
  Ohlson O;
* :mod:`.dcf` -- two-stage FCFF DCF, CAPM/WACC inputs, sensitivity, reverse DCF.

Pure analytics: no I/O; routers supply the data.
"""
