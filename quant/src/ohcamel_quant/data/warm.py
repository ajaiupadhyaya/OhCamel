"""Cache warmer: prefetch everything the landing (Markets) pages need.

Run once (``ohcamel-quant warm``) or periodically via
:func:`ohcamel_quant.data.market.start_background_refresh`. Every step is
independent; a failure is logged and reported in the summary, never raised,
so one blocked vendor cannot stop the rest from refreshing.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from .base import DataUnavailable
from .market import MarketData, get_market, load_universes

log = logging.getLogger("ohcamel_quant.data.warm")

#: Core FRED series for the macro dashboard (ids as published by FRED).
DASHBOARD_FRED_SERIES: dict[str, str] = {
    "DFF": "Effective federal funds rate",
    "SOFR": "Secured overnight financing rate",
    "T10Y2Y": "10y minus 2y Treasury spread",
    "T10Y3M": "10y minus 3m Treasury spread",
    "T5YIE": "5-year breakeven inflation",
    "T10YIE": "10-year breakeven inflation",
    "DFII10": "10-year TIPS real yield",
    "BAMLC0A0CM": "ICE BofA US corporate IG OAS",
    "BAMLH0A0HYM2": "ICE BofA US high-yield OAS",
    "VIXCLS": "Cboe VIX close",
    "DTWEXBGS": "Nominal broad US dollar index",
    "DCOILWTICO": "WTI crude oil spot",
    "CPIAUCSL": "CPI, all urban consumers",
    "CPILFESL": "Core CPI",
    "PCEPILFE": "Core PCE price index",
    "UNRATE": "Unemployment rate",
    "PAYEMS": "Nonfarm payrolls",
    "INDPRO": "Industrial production",
    "GDPC1": "Real GDP",
    "UMCSENT": "U. Michigan consumer sentiment",
    "ICSA": "Initial jobless claims",
    "NFCI": "Chicago Fed national financial conditions",
    "USREC": "NBER recession indicator",
}


def _step(name: str, fn: Any, summary: dict[str, Any]) -> None:
    t0 = time.monotonic()
    try:
        fn()
        summary["ok"].append(name)
    except (DataUnavailable, ValueError) as e:
        summary["failed"][name] = str(e)
        log.warning("warm: %s failed: %s", name, e)
    except Exception as e:  # noqa: BLE001 - a warmer must never crash the process
        summary["failed"][name] = f"{type(e).__name__}: {e}"
        log.exception("warm: %s crashed", name)
    finally:
        summary["seconds"][name] = round(time.monotonic() - t0, 2)


def warm(market: MarketData | None = None, include_13f: bool = True) -> dict[str, Any]:
    """Prefetch universes (5y+ OHLCV), Treasury curve, dashboard FRED series,
    Fama-French factors, risk-free rate and notable 13F filers. Returns a
    summary ``{"ok": [...], "failed": {name: reason}, "seconds": {...}}``."""
    m = market or get_market()
    summary: dict[str, Any] = {"ok": [], "failed": {}, "seconds": {}}
    u = load_universes()
    start = date.today() - timedelta(days=int(365.25 * 5) + 30)
    tickers = list(dict.fromkeys(mem["ticker"] for spec in u["universes"].values() for mem in spec["members"]))
    for t in tickers:
        _step(f"ohlcv:{t}", lambda t=t: m.ohlcv(t, start), summary)
    _step("quotes", lambda: m.quotes(tickers), summary)
    _step("treasury_curve", lambda: m.treasury_curve(), summary)
    for sid in DASHBOARD_FRED_SERIES:
        _step(f"fred:{sid}", lambda sid=sid: m.fred([sid]), summary)
    _step("risk_free_daily", lambda: m.risk_free_daily(), summary)
    _step("ff5+mom", lambda: m.ff_factors("ff5", True), summary)
    _step("ff3", lambda: m.ff_factors("ff3", False), summary)
    _step("sec:company_tickers", lambda: m.search("AAPL"), summary)
    if include_13f:
        for f in u["notable_13f_filers"]:
            _step(f"13f:{f['cik']}", lambda c=f["cik"]: m.holdings_13f(c), summary)
    log.info("warm: %d ok, %d failed", len(summary["ok"]), len(summary["failed"]))
    return summary
