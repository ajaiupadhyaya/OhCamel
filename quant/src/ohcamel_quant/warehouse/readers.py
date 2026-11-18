"""Read side of the warehouse, in MarketData's own shapes (compute plan C1).

``warehouse_returns`` is what ``MarketData.returns`` calls first when a
warehouse is configured (``warehouse_prices`` is the same for
``MarketData.prices(..., warehouse_first=True)``, which batch jobs such as
``farm.sweep`` use so they do not hammer the vendors): it serves only when every ticker is present, fresh
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


def _with_notes(ds: Dataset, notes: list[str]) -> Dataset:
    """``ds`` with ``notes`` appended to its first provenance entry's ``detail.notes``."""
    provs = list(ds.provenance)
    if notes and provs:
        head = provs[0]
        detail = dict(head.detail)
        detail["notes"] = [*(detail.get("notes") or []), *notes]
        provs[0] = Provenance(head.source, head.fetched_at, head.synthetic, detail)
    return Dataset(ds.data, provs)


def repaired_ohlcv(con: Any, ticker: str, start: date | None = None, end: date | None = None, *,
                   path: str = "") -> Dataset:
    """:func:`ohlcv` with every unadjusted split repaired (``data.splits.present``), each repair a sentence in
    the first provenance entry's ``detail.notes``. The read path for one name; :func:`bars_panel` for many.

    Only the stored bars in ``[start, end]`` are read, so nothing after ``end`` can rescale what came before it.
    ``ohlcv`` itself stays the vendor's bytes: ``ops.data_integrity`` must see the defect it reports."""
    from ..data.splits import present

    ds = ohlcv(con, ticker, start, end, path=path)
    fixed, notes = present(ds.data)
    return _with_notes(Dataset(fixed, list(ds.provenance)), notes)


def bars_panel(con: Any, tickers: list[str], start: date | None, end: date | None, *,
               field: str = "adj_close") -> tuple[pd.DataFrame, list[str]]:
    """One field of the daily bars of many names, split-repaired: ``(wide frame, repair notes)``.

    The one read path for a reader that wants daily bars from the warehouse. One SQL read of the stored bars in
    ``[start, end]`` (either may be None); each name repaired on its own (``data.splits.present``: a session
    whose close-to-close and adjusted moves are both beyond 30% and whose overnight ratio is within 3% of a
    standard split ratio has every bar before it divided by that ratio, volume multiplied). ``field`` is one of
    ``BAR_COLUMNS``; every field comes off the same repaired bars, so a price divided by a ratio has its volume
    multiplied by it. The frame is indexed by ``date`` (datetime64, ascending), its columns (axis ``ticker``)
    one per requested name that has a stored bar (upper-cased, in the order asked; a name with none has no column), NaN where a name
    has no bar on a session another has. Notes: one sentence per repaired ex-date, ``"TICKER: ..."``, in the
    order the names were asked, oldest ex-date first. Deterministic: the same warehouse gives the same frame.

    Point in time: a repair uses the ex-date's bar and the bar before it, and changes only bars BEFORE the
    ex-date. A split after ``end`` is never read, so a reader as of ``end`` sees nothing it could not have
    known then; within the window the result equals repairing the whole stored history and cutting it."""
    if field not in BAR_COLUMNS:
        raise ValueError(f"field {field!r} is not one of {', '.join(BAR_COLUMNS)}")
    want = list(dict.fromkeys(t.upper().strip() for t in tickers if t.strip()))
    empty = pd.DataFrame(index=_date_index([]), columns=pd.Index([], name="ticker"), dtype=float)
    if not want:
        return empty, []
    from ..data.splits import present

    sql = f"SELECT ticker, date, {', '.join(BAR_COLUMNS)} FROM bars_daily WHERE list_contains(?, ticker)"
    args: list[Any] = [want]
    if start is not None:
        sql += " AND date >= ?"
        args.append(start)
    if end is not None:
        sql += " AND date <= ?"
        args.append(end)
    raw = con.execute(sql + " ORDER BY ticker, date", args).df()
    cols: dict[str, pd.Series] = {}
    notes: list[str] = []
    by = dict(tuple(raw.groupby("ticker", sort=False))) if len(raw) else {}
    for t in want:
        if t not in by:
            continue
        g = by[t]
        bars = g[BAR_COLUMNS].astype(float)
        bars.index = _date_index(g["date"])
        fixed, said = present(bars)
        cols[t] = fixed[field]
        notes += [f"{t}: {n}" for n in said]
    if not cols:
        return empty, notes
    wide = pd.DataFrame(cols).sort_index()
    wide.index.name, wide.columns.name = "date", "ticker"
    return wide, notes


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


def _warehouse_bars(settings: Settings, want: list[str], start: date | None,
                    end: date | None) -> tuple[_WarehouseBars | None, str | None]:
    """The stored bars for every ticker when the warehouse can serve them all, else ``(None, reason)``."""
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
            # The whole stored history, as the pages have always read it (see repaired_ohlcv): the frames are
            # cut to the window afterwards, and a split's repair changes no return before its ex-date.
            frames = {t: repaired_ohlcv(con, t, path=str(path)) for t in want}
    except WarehouseUnavailable as e:
        return None, f"warehouse not used ({e}); served from providers"
    return _WarehouseBars(settings, frames), None


def _wanted(tickers: list[str]) -> list[str]:
    return list(dict.fromkeys(x.upper().strip() for x in tickers if x.strip()))


def warehouse_returns(settings: Settings, tickers: list[str], start: date | None, end: date | None,
                      log: bool) -> tuple[Dataset | None, str | None]:
    """``(dataset, None)`` when the warehouse can serve every ticker, else ``(None, reason)``."""
    want = _wanted(tickers)
    if not want:
        return None, None
    bars, note = _warehouse_bars(settings, want, start, end)
    return (bars.returns(want, start, end, log), None) if bars is not None else (None, note)


def warehouse_prices(settings: Settings, tickers: list[str], start: date | None, end: date | None,
                     field: str) -> tuple[Dataset | None, str | None]:
    """``MarketData.prices(..., warehouse_first=True)``: the same serve-or-reason rule as returns."""
    want = _wanted(tickers)
    if not want:
        return None, None
    bars, note = _warehouse_bars(settings, want, start, end)
    return (bars.prices(want, start, end, field), None) if bars is not None else (None, note)


def sec_facts_asof(con: Any, ticker: str, asof: date, tags: list[str] | None = None) -> pd.DataFrame:
    """Facts available on ``asof`` (II.5 point-in-time rule: ``filed <= asof``): for each
    (tag, unit, period_start, period_end) the latest value filed on or before ``asof``."""
    sql = ("SELECT tag, unit, period_start, period_end, filed, form, value FROM ("
           "SELECT *, row_number() OVER (PARTITION BY tag, unit, period_start, period_end ORDER BY filed DESC) AS rn "
           "FROM sec_facts WHERE ticker = ? AND filed <= ?")
    args: list[Any] = [ticker.upper().strip(), asof]
    if tags:
        sql += " AND list_contains(?, tag)"
        args.append(list(tags))
    df = con.execute(sql + ") WHERE rn = 1 ORDER BY tag, period_end, period_start", args).df()
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
    return df
