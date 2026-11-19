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
that becomes NaN. Roots are kept as given (``SPX``, ``SPXW``, and since
November 2026 ``SPXO``, whose settlement is not established here: analytics
decide what a root means, :func:`ohcamel_quant.options.parity.root_settlement`).

Times (completion task 3.4). ``timestamp`` is when Cboe generated the payload,
in UTC; the header's ``last_trade_time`` is the underlying's last trade in
New York time. Observed on a live fetch at 09:58:59 EST on 2026-11-19:
``timestamp`` 14:58:11 (UTC) and ``last_trade_time`` 09:43:10 -- the
15-minute delay. On a closed market the payload keeps being regenerated while
its quotes stay those of the last session (the committed SPY payload was
generated Sunday 2026-11-15 22:22:02 UTC; ``last_trade_time`` is Friday 16:00).
So the chain's quote time, ``as_of``, is ``last_trade_time`` when the payload
gives one no later than its own ``timestamp`` (``quote_ts_source =
'cboe:last_trade_time'``), else the ``timestamp`` (``'cboe:timestamp'``: it
can postdate the quotes by the delay, and by more on a closed market). After
the close ``last_trade_time`` is the 16:00 closing print while index-ETF option
quotes run to 16:15, so ``as_of`` can precede the last option quotes by up to
15 minutes (T overstated by at most that). A payload with neither time is
refused: the ingest's own clock is never used. ``as_of`` is tz-naive
America/New_York (the analytics convention); ``payload_ts`` is tz-naive UTC.
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
    as_of, source, payload_ts = quote_time(payload)
    if as_of is None:
        raise DataUnavailable(f"cboe: no timestamp for {ticker}")
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
    return OptionChain(underlying=ticker.upper().lstrip("^"), spot=spot, as_of=as_of, quotes=q,
                       quote_ts_source=source, payload_ts=payload_ts)


def _stamp(v: Any, tz: str) -> pd.Timestamp | None:
    """A vendor time as a tz-aware Timestamp; naive strings are read in ``tz``."""
    if not v:
        return None
    try:
        t = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    if t is pd.NaT:
        return None
    return t.tz_localize(tz) if t.tzinfo is None else t


def quote_time(payload: dict[str, Any]) -> tuple[pd.Timestamp | None, str | None, pd.Timestamp | None]:
    """``(as_of, source, payload_ts)`` of a Cboe payload (module docstring): the quotes' time, tz-naive New York;
    which field gave it; the payload's generation time, tz-naive UTC."""
    data = (payload or {}).get("data") or {}
    gen = _stamp(data.get("timestamp") or (payload or {}).get("timestamp"), "UTC")
    ltt = _stamp(data.get("last_trade_time"), "America/New_York")
    payload_ts = None if gen is None else gen.tz_convert("UTC").tz_localize(None)
    if ltt is not None and (gen is None or ltt <= gen):
        t, source = ltt, "cboe:last_trade_time"
    elif gen is not None:
        t, source = gen, "cboe:timestamp"
    else:
        return None, None, None
    return t.tz_convert("America/New_York").tz_localize(None), source, payload_ts


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
            quote_ts_source=ch.quote_ts_source,
            payload_ts_utc=None if ch.payload_ts is None else ch.payload_ts.isoformat(),
        )

    quotes, prov = get_store(settings).fetch_or_stale(
        FAMILY, sym, settings.ttl_options_s, fetch, offline=settings.offline
    )
    chain = OptionChain(
        underlying=ticker.upper().lstrip("^"), spot=float(prov.detail["spot"]),
        as_of=pd.Timestamp(prov.detail["as_of"]), quotes=quotes,
        quote_ts_source=prov.detail.get("quote_ts_source"),
        payload_ts=pd.Timestamp(prov.detail["payload_ts_utc"]) if prov.detail.get("payload_ts_utc") else None,
    )
    return Dataset(chain, [prov])
