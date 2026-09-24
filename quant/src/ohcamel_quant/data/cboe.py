"""Cboe delayed (15-minute) option chains.

Endpoint: ``https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json``
-- the JSON behind cboe.com's delayed quote pages. Cash-settled indices take a
leading underscore (``SPX -> _SPX``, ``VIX -> _VIX``, ``NDX``, ``RUT``, ``XSP``,
``DJX``, ``OEX``, ``XEO``).

Payload (abridged)::

    {"timestamp": "2024-06-07 16:15:03",
     "data": {"symbol": "SPY", "current_price": 534.01, "close": ...,
              "prev_day_close": ..., "iv30": ...,
              "options": [{"option": "SPY240607C00400000", "bid": 133.5,
                           "ask": 134.9, "last_trade_price": 134.2,
                           "volume": 12, "open_interest": 305, "iv": 0.0,
                           "delta": 1.0, "gamma": 0.0, "theta": ..., "vega": ...,
                           "rho": ..., "theo": ...}, ...]}}

Contract symbols follow the OCC Options Symbology Initiative (2010):
``root + YYMMDD + C|P + strike*1000 as 8 digits``; the root length varies
(``SPXW``, ``AAPL1`` after adjustments), so the fixed-width 15-character tail is
parsed from the right. Cboe reports ``iv = 0`` when it has no model value;
that becomes NaN. Timestamps are US/Eastern wall-clock; ``as_of`` is kept
tz-naive in America/New_York (stated in provenance).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store

if TYPE_CHECKING:
    from .market import Dataset

FAMILY = "options"
URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
INDEX_SYMBOLS = frozenset({"SPX", "SPXW", "VIX", "NDX", "RUT", "XSP", "DJX", "OEX", "XEO", "MRUT", "NANOS"})
QUOTE_COLUMNS = [
    "contract", "root", "expiry", "strike", "type", "bid", "ask", "last", "volume", "open_interest",
    "vendor_iv", "vendor_delta", "vendor_gamma", "vendor_theta", "vendor_vega", "vendor_rho",
]


def cboe_symbol(ticker: str) -> str:
    """Cboe CDN symbol: strip Yahoo's ``^``; indices get a leading underscore."""
    t = ticker.upper().strip().lstrip("^").lstrip("_")
    return f"_{t}" if t in INDEX_SYMBOLS else t


def parse_occ(symbol: str) -> tuple[str, pd.Timestamp, str, float]:
    """OCC symbol -> ``(root, expiry, 'C'|'P', strike)``.

    >>> parse_occ("SPXW240607P05000000")[1:]
    (Timestamp('2024-06-07 00:00:00'), 'P', 5000.0)
    """
    s = symbol.strip().replace(" ", "")
    if len(s) < 16:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    root, ymd, cp, strike = s[:-15], s[-15:-9], s[-9], s[-8:]
    if cp not in ("C", "P") or not ymd.isdigit() or not strike.isdigit():
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    expiry = pd.Timestamp(year=2000 + int(ymd[:2]), month=int(ymd[2:4]), day=int(ymd[4:6]))
    return root, expiry, cp, int(strike) / 1000.0


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if np.isfinite(f) else float("nan")


def parse_chain(payload: dict[str, Any], ticker: str):
    """Cboe delayed-quotes JSON -> :class:`~.market.OptionChain`."""
    from .market import OptionChain

    data = (payload or {}).get("data") or {}
    opts = data.get("options") or []
    if not opts:
        raise DataUnavailable(f"cboe: no options listed for {ticker}")
    spot = _num(data.get("current_price"))
    if not np.isfinite(spot) or spot <= 0:
        raise DataUnavailable(f"cboe: no current_price for {ticker}")
    ts = data.get("timestamp") or payload.get("timestamp") or data.get("last_trade_time")
    if not ts:
        raise DataUnavailable(f"cboe: no timestamp for {ticker}")
    as_of = pd.Timestamp(ts)
    if as_of.tzinfo is not None:
        as_of = as_of.tz_convert("America/New_York").tz_localize(None)
    rows = []
    for o in opts:
        try:
            root, expiry, cp, strike = parse_occ(str(o.get("option", "")))
        except ValueError:
            continue
        iv = _num(o.get("iv"))
        rows.append({
            "contract": str(o["option"]), "root": root, "expiry": expiry, "strike": strike, "type": cp,
            "bid": _num(o.get("bid")), "ask": _num(o.get("ask")), "last": _num(o.get("last_trade_price")),
            "volume": _num(o.get("volume")), "open_interest": _num(o.get("open_interest")),
            "vendor_iv": iv if iv > 0 else float("nan"),
            "vendor_delta": _num(o.get("delta")), "vendor_gamma": _num(o.get("gamma")),
            "vendor_theta": _num(o.get("theta")), "vendor_vega": _num(o.get("vega")),
            "vendor_rho": _num(o.get("rho")),
        })
    if not rows:
        raise DataUnavailable(f"cboe: no parseable option symbols for {ticker}")
    q = pd.DataFrame(rows, columns=QUOTE_COLUMNS).sort_values(["expiry", "type", "strike"])
    q = q.reset_index(drop=True)
    return OptionChain(underlying=ticker.upper().lstrip("^"), spot=spot, as_of=as_of, quotes=q)


def fetch_chain(ticker: str, settings: Settings) -> Dataset:
    """Full delayed chain for ``ticker`` (cached ``Settings.ttl_options_s``)."""
    from .market import Dataset, OptionChain

    sym = cboe_symbol(ticker)
    url = URL.format(sym=sym)

    def fetch() -> tuple[pd.DataFrame, Provenance]:
        payload = http.get_json(url, settings, source="cboe")
        ch = parse_chain(payload, ticker)
        return ch.quotes, Provenance.now(
            "cboe", url=url, symbol=sym, spot=ch.spot, as_of=ch.as_of.isoformat(),
            timezone="America/New_York", delay="15 minutes (Cboe delayed quotes)", n_contracts=len(ch.quotes),
        )

    quotes, prov = get_store(settings).fetch_or_stale(
        FAMILY, sym, settings.ttl_options_s, fetch, offline=settings.offline
    )
    chain = OptionChain(
        underlying=ticker.upper().lstrip("^"), spot=float(prov.detail["spot"]),
        as_of=pd.Timestamp(prov.detail["as_of"]), quotes=quotes,
    )
    return Dataset(chain, [prov])
