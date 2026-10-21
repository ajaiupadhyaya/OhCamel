/**
 * Data sources (what each supplies, how it is fetched, how often it refreshes) and the
 * platform's honest limitations. Cadences are the server's cache TTLs in
 * quant/src/ohcamel_quant/config.py (prices 6 h, quotes 60 s, macro 12 h, factors 7 d,
 * options 15 min, filings 24 h) plus each vendor's own publication schedule.
 */
export interface Source {
  name: string;
  supplies: string;
  cadence: string;
  notes: string;
  href: string;
  offline: boolean;
}

export const SOURCES: Source[] = [
  { name: "Alpaca Market Data", supplies: "Daily OHLCV bars (SIP, falling back to IEX) and latest quotes for US equities and ETFs; the committed offline dataset (SPY QQQ IWM TLT IEF GLD XLE XLF XLK, 2016-06 → 2026-06).", cadence: "Daily bars cached 6 h; quotes 60 s. Free plans cannot query SIP data newer than 15 minutes, so the end is clamped to now − 16 min.", notes: "OHLC is split-adjusted only; a separate all-adjusted close (splits, dividends, spin-offs) feeds returns. History starts in 2016.", href: "https://docs.alpaca.markets/docs/about-market-data-api", offline: true },
  { name: "Yahoo Finance (chart API)", supplies: "Long daily histories, indices (^GSPC, ^VIX), crypto, FX; split- and dividend-adjusted closes.", cadence: "Cached 6 h, refreshed incrementally (last five sessions re-fetched and checked against the cache).", notes: "Timestamps converted to exchange-local session dates. Tried first for windows older than Alpaca's history.", href: "https://finance.yahoo.com", offline: false },
  { name: "Stooq", supplies: "Daily OHLCV CSV as a last-resort price source.", cadence: "Cached 6 h.", notes: "No adjusted close: adj_close = close, and the provenance says so.", href: "https://stooq.com", offline: false },
  { name: "FRED (St. Louis Fed)", supplies: "Treasury yields (DGS10, DGS2, the full CMT curve), 3-month T-bill (risk-free), breakeven inflation (T10YIE), VIX (VIXCLS), credit spreads, CPI/PCE, GDP & potential GDP, unemployment, recession dates, GDP deflator.", cadence: "Cached 12 h; each series publishes on its own schedule (daily to quarterly). Values kept exactly as published — yields in percent, never forward-filled.", notes: "Offline only DGS10, DGS2 and VIXCLS are committed.", href: "https://fred.stlouisfed.org", offline: true },
  { name: "Kenneth R. French Data Library", supplies: "Daily and monthly Fama–French factors (Mkt−RF, SMB, HML, RMW, CMA), momentum and the one-month T-bill.", cadence: "Cached 7 days; the library itself updates monthly with a lag of about a month.", notes: "Percent converted to decimals; missing markers (−99.99) dropped.", href: "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html", offline: false },
  { name: "Cboe delayed quotes", supplies: "Full option chains (bid, ask, volume, open interest, vendor IV and greeks) for equities, ETFs and cash-settled indices.", cadence: "15-minute delayed; cached 15 min.", notes: "No per-quote timestamps, so a one-sided quote is the only observable staleness; vendor IV of 0 becomes missing.", href: "https://www.cboe.com/delayed_quotes/", offline: false },
  { name: "SEC EDGAR — XBRL company facts", supplies: "Every financial fact a company has filed in 10-K / 10-Q (standardized into statements), shares outstanding from the cover page, the ticker ↔ CIK map.", cadence: "Cached 24 h; companies file quarterly (10-Q) and annually (10-K).", notes: "Fair-access rules: a descriptive User-Agent and at most 10 requests per second.", href: "https://www.sec.gov/edgar/sec-api-documentation", offline: false },
  { name: "SEC EDGAR — Form 13F", supplies: "Quarterly long equity holdings of institutional managers (used to import a real fund's portfolio).", cadence: "Filed within 45 days of quarter end; cached 24 h.", notes: "Values reported in thousands of USD for older filings; only long US-listed equity positions are disclosed.", href: "https://www.sec.gov/divisions/investment/13ffaq", offline: false },
  { name: "OpenFIGI (Bloomberg)", supplies: "CUSIP → exchange ticker mapping for 13F holdings.", cadence: "Mappings cached permanently (a definitive “not found” too); rate-limited to 25 requests a minute without a key.", notes: "CUSIPs not reached within a call's time budget stay unmapped and are filled on a later call.", href: "https://www.openfigi.com/api", offline: false },
];

export const LIMITATIONS: { title: string; text: string }[] = [
  { title: "Delayed and end-of-day data", text: "Prices are daily closes (or quotes delayed up to 15 minutes); options are 15-minute delayed; fundamentals update only when companies file. Nothing here is suitable for intraday execution. The Live Engine uses the IEX feed, a single exchange with a small share of US volume." },
  { title: "Adjustment differences between sources", text: "Alpaca's OHLC is split-adjusted only (a separate all-adjusted close is used for returns); Stooq has no dividend adjustment at all. Where a fallback source is used, total returns on dividend payers can be understated — the provenance footer names the source actually used." },
  { title: "Survivorship in user-chosen universes", text: "Universes, presets and backtest baskets are chosen today from names that exist today. Companies that were delisted or went bankrupt are missing, which flatters historical performance and the apparent success of strategies." },
  { title: "Offline mode", text: "The offline build only has nine ETFs (2016-06 → 2026-06) and three FRED series. Options, SEC filings, Ken French factors and most FRED series return a clear “data source unavailable” state instead of a stand-in." },
  { title: "Models are estimates", text: "VaR, expected returns, betas, factor loadings and DCF values are statistical estimates with sampling error, fitted to one historical path. Backtests ignore market impact beyond proportional costs; out-of-sample tests reduce, but do not remove, overfitting." },
  { title: "Accounting data quirks", text: "XBRL tags vary across companies and years; the standardization uses prioritized tag lists, derives Q4 as FY − 9M and never imputes, so some line items and ratios are blank. Score coefficients come from decades-old samples and may not transfer to every industry (e.g. banks)." },
  { title: "Risk-neutral is not real-world", text: "Probabilities read from option prices embed risk premia; they are what the market charges for, not a forecast of what will happen." },
  { title: "Not investment advice", text: "OhCamel Quant is an educational and research tool built on public data. Every figure should be checked against primary sources before any decision." },
];
