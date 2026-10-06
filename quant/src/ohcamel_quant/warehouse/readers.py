"""Read side of the warehouse, in MarketData's own shapes (compute plan C1).

``warehouse_returns`` is what ``MarketData.returns`` calls first when a
warehouse is configured: it serves only when every ticker is present, fresh
(or the window ends on or before the stored history), and its stored history
covers the requested start, and otherwise returns the reason so the provider
path can record it in provenance.

Window coverage mirrors ``data/prices.py``'s cache rule. ``start=None`` means
``prices.DEFAULT_START`` (1990, "provider max history" in api/models.py). A
ticker covers ``want`` when ``want`` is on or after its first stored session, or
when its latest successful full fetch asked the vendor for ``want`` or earlier
(``ingest_log.detail.requested_start``) -- the vendor then has nothing earlier
than what is stored. Otherwise the provider path serves, with a ``window``
reason, so a 20-year store never silently truncates a default or early window.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from ..config import Settings
from ..data import prices
from ..data.base import DataUnavailable, Provenance
from ..data.market import Dataset, MarketData, _window
from . import freshness as fr
from .db import WarehouseUnavailable, open_ro, warehouse_path

BAR_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
RO_TIMEOUT_S = 2.0


def _date_index(values: Any) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(values)).astype("datetime64[ns]")
    idx.name = "date"
    return idx


def ohlcv(con: Any, ticker: str, start: date | None = None, end: date | None = None, *,
          path: str = "") -> Dataset:
    """Daily bars, MarketData.ohlcv's contract: open..volume floats on a ``date`` index."""
    t = ticker.upper().strip()
    sql = ("SELECT date, open, high, low, close, adj_close, volume, source, fetched_at "
           "FROM bars_daily WHERE ticker = ?")
    args: list[Any] = [t]
    if start is not None:
        sql += " AND date >= ?"
        args.append(start)
    if end is not None:
        sql += " AND date <= ?"
        args.append(end)
    raw = con.execute(sql + " ORDER BY date", args).df()
    if raw.empty:
        raise DataUnavailable(f"{t}: not in the warehouse")
    sources = sorted({str(s) for s in raw["source"].dropna()}) or ["unknown"]
    fetched = raw["fetched_at"].max()
    df = raw[BAR_COLUMNS].astype(float)
    df.index = _date_index(raw["date"])
    prov = Provenance(
        source="warehouse:" + "+".join(sources),
        fetched_at=fetched.isoformat() if pd.notna(fetched) else "",
        detail={"dataset": "bars_daily", "path": path, "symbol": t,
                "first": str(df.index[0].date()), "last": str(df.index[-1].date())},
    )
    return Dataset(df, [prov])


def bars_status(con: Any, tickers: list[str]) -> dict[str, tuple[date | None, datetime | None]]:
    """Per ticker: (last stored session, last successful ingest's ran_at)."""
    last = dict(con.execute(
        "SELECT ticker, max(date) FROM bars_daily WHERE list_contains(?, ticker) GROUP BY ticker",
        [tickers]).fetchall())
    ok = dict(con.execute(
        "SELECT key, max(ran_at) FROM ingest_log WHERE dataset = 'bars_daily' AND status = 'ok' "
        "AND list_contains(?, key) GROUP BY key", [tickers]).fetchall())
    return {t: (last.get(t), ok.get(t)) for t in tickers}


def bars_coverage(con: Any, tickers: list[str]) -> dict[str, tuple[date | None, date | None]]:
    """Per ticker: (first stored session, ``requested_start`` of the latest
    successful full fetch or ``None`` when no log row records one)."""
    first = dict(con.execute(
        "SELECT ticker, min(date) FROM bars_daily WHERE list_contains(?, ticker) GROUP BY ticker",
        [tickers]).fetchall())
    asked = dict(con.execute(
        "SELECT key, arg_max(json_extract_string(detail, '$.requested_start'), ran_at) FROM ingest_log "
        "WHERE dataset = 'bars_daily' AND status = 'ok' AND list_contains(?, key) "
        "AND json_extract_string(detail, '$.requested_start') IS NOT NULL GROUP BY key", [tickers]).fetchall())
    return {t: (first.get(t), date.fromisoformat(asked[t]) if asked.get(t) else None) for t in tickers}


def _covers(want: date, first: date | None, requested: date | None) -> bool:
    return first is not None and (want >= first or (requested is not None and requested <= want))


class _WarehouseBars(MarketData):
    """MarketData over frames already read from the warehouse, so prices() and
    returns() apply exactly the same join and NaN rules as the provider path."""

    _reads_warehouse = False

    def __init__(self, settings: Settings, frames: dict[str, Dataset]) -> None:
        super().__init__(settings)
        self._frames = frames

    def ohlcv(self, ticker: str, start: date | None = None, end: date | None = None) -> Dataset:
        ds = self._frames[ticker.upper().strip()]
        return Dataset(_window(ds.data, start, end), list(ds.provenance))


def warehouse_returns(settings: Settings, tickers: list[str], start: date | None, end: date | None,
                      log: bool) -> tuple[Dataset | None, str | None]:
    """``(dataset, None)`` when the warehouse can serve every ticker, else ``(None, reason)``."""
    want = list(dict.fromkeys(x.upper().strip() for x in tickers if x.strip()))
    if not want:
        return None, None
    path = warehouse_path(settings)
    try:
        with open_ro(path, timeout_s=RO_TIMEOUT_S) as con:
            status = bars_status(con, want)
            now = fr._now()
            missing = [t for t in want if status[t][0] is None]
            stale = [t for t in want if t not in missing
                     and not (end is not None and status[t][0] >= end)
                     and not fr.is_fresh(status[t][0], status[t][1], now, fr.VENDOR_DUE)]
            from_ = start or prices.DEFAULT_START
            present = [t for t in want if t not in missing]
            cover = bars_coverage(con, present) if present else {}  # an empty list param has no type
            short = [t for t in cover if not _covers(from_, *cover[t])]
            if missing or stale or short:
                parts = []
                if missing:
                    parts.append("not in warehouse: " + ", ".join(missing))
                if stale:
                    parts.append("stale: " + ", ".join(f"{t} (data_asof {status[t][0]})" for t in stale))
                if short:
                    parts.append(f"window: start {from_} before stored history: "
                                 + ", ".join(f"{t} (from {cover[t][0]})" for t in short))
                return None, "warehouse not used (" + "; ".join(parts) + "); served from providers"
            frames = {t: ohlcv(con, t, path=str(path)) for t in want}
    except WarehouseUnavailable as e:
        return None, f"warehouse not used ({e}); served from providers"
    return _WarehouseBars(settings, frames).returns(want, start, end, log), None
