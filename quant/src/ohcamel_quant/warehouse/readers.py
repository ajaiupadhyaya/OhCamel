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

import numpy as np
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


def _membership(con: Any) -> str:
    """``universe_membership`` with each row's price series (completion task 3.8): ``universe, ticker, start, end,
    series`` -- the series from ``membership_series`` (migration 006), else the ticker (a warehouse before 006, or a
    row the previous code wrote)."""
    has = con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'main' AND "
                      "table_name = 'membership_series'").fetchone()[0]
    if not has:
        return 'SELECT universe, ticker, "start", "end", ticker AS series FROM universe_membership'
    return ('SELECT m.universe, m.ticker, m."start", m."end", coalesce(s.series, m.ticker) AS series '
            "FROM universe_membership m LEFT JOIN membership_series s ON s.universe = m.universe AND "
            's.ticker = m.ticker AND s."start" IS NOT DISTINCT FROM m."start" AND '
            's."end" IS NOT DISTINCT FROM m."end"')


def _membership_universe(con: Any, universe: str) -> str:
    u = universe.strip().lower()
    n = con.execute("SELECT count(*) FROM universe_membership WHERE universe = ?", [u]).fetchone()[0]
    if not n:
        raise DataUnavailable(f"{u}: no dated membership in the warehouse")
    return u


def members_asof(con: Any, universe: str, asof: date) -> list[str]:
    """The index's members on ``asof`` (completion task 3.5): every company with a ``universe_membership`` stint
    whose ``start`` is on or before ``asof`` (or unknown: before coverage) and whose ``end`` is after it (or NULL),
    named by its price series (completion task 3.8: the key its bars are stored under -- its last ticker across
    renames, ``TICKER@DATE`` for the earlier holder of a reused ticker; the ticker itself for most), sorted.
    Refuses -- ``DataUnavailable`` -- a universe with no stints, and a date before the changes table's coverage
    (``constituents.COVERAGE_START``), where the stints cannot be trusted."""
    from .constituents import COVERAGE_START

    u = universe.strip().lower()
    cov = COVERAGE_START.get(u)
    if cov is not None and asof < cov:
        raise DataUnavailable(f"{u}: membership before {cov.isoformat()} is not known (the changes table is "
                              "complete only from then)")
    u = _membership_universe(con, u)
    rows = con.execute(f'SELECT DISTINCT series FROM ({_membership(con)}) WHERE universe = ? AND '
                       '("start" IS NULL OR "start" <= ?) AND ("end" IS NULL OR "end" > ?) ORDER BY 1',
                       [u, asof, asof]).fetchall()
    return [t for (t,) in rows]


def members_panel(con: Any, universe: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """:func:`members_asof` on every date of ``index`` at once: a boolean frame (dates x series), True where the
    series is a member that day. The same rule, row for row (tests/test_backtest_engine.py checks the two agree);
    one read instead of one per date. Columns: every series with a stint overlapping the index's span, sorted."""
    from .constituents import COVERAGE_START

    u = universe.strip().lower()
    idx = pd.DatetimeIndex(index)
    cov = COVERAGE_START.get(u)
    if len(idx) and cov is not None and idx[0].date() < cov:
        raise DataUnavailable(f"{u}: membership before {cov.isoformat()} is not known (the changes table is "
                              "complete only from then)")
    u = _membership_universe(con, u)
    if not len(idx):
        return pd.DataFrame(index=idx, dtype=bool)
    st = con.execute(f'SELECT series, "start", "end" FROM ({_membership(con)}) WHERE universe = ? '
                     'AND ("start" IS NULL OR "start" <= ?) AND ("end" IS NULL OR "end" > ?)',
                     [u, idx[-1].date(), idx[0].date()]).fetchall()
    names = sorted({k for k, _, _ in st})
    out = pd.DataFrame(False, index=idx, columns=pd.Index(names, name="ticker"))
    for k, a, b in st:
        on = np.ones(len(idx), dtype=bool)
        if a is not None:
            on &= idx >= pd.Timestamp(a)
        if b is not None:
            on &= idx < pd.Timestamp(b)
        out[k] = out[k].to_numpy() | on
    return out


#: A member's exit counts as covered (completion task 3.8, fix round 1, M3) when its series has bars ACROSS the stint
#: it ends: from the later of the stint's start and the changes table's coverage start (2011-01-01 for sp500; the
#: stint's start for a universe without one) up to the exit. Three conditions, all on WEEKDAYS, which count the
#: market's holidays too and so are stricter than sessions, never looser: a bar on one of the ``EXIT_SESSIONS`` (5)
#: weekdays before the exit; the series' first bar in the stint within ``EXIT_SESSIONS`` weekdays of its start; and
#: bars on at least ``STINT_DENSITY`` (90%) of the stint's weekdays (a year has about 9 holidays, 3.6%). Only exits
#: on or after the coverage start count (:func:`exit_coverage`); earlier ones are not known completely.
EXIT_SESSIONS = 5
STINT_DENSITY = 0.90


def membership_exits(con: Any, universe: str) -> pd.DataFrame:
    """Every exit of a company from ``universe``: ``series, start, end`` with ``end`` not NULL, the rows of one
    series that meet (a rename splits a stint into rows that touch) merged first, so a rename is not an exit."""
    u = _membership_universe(con, universe)
    rows = con.execute(f'SELECT series, "start", "end" FROM ({_membership(con)}) WHERE universe = ? '
                       'ORDER BY 1, "start" NULLS FIRST', [u]).fetchall()
    merged: list[list[Any]] = []
    for k, a, b in rows:
        if merged and merged[-1][0] == k and merged[-1][2] is not None and a is not None and merged[-1][2] >= a:
            merged[-1][2] = None if b is None else max(merged[-1][2], b)
            continue
        merged.append([k, a, b])
    return pd.DataFrame([m for m in merged if m[2] is not None], columns=["series", "start", "end"])


def fetched_to(con: Any, series: list[str]) -> dict[str, date]:
    """For each series ``ingest.delisted`` wrote, the last date it asked the vendor for (its latest successful run's
    ``fetched_to``): a series whose last bar is near it was cut there, not delisted (completion task 3.8)."""
    if not series:
        return {}
    rows = con.execute("SELECT key, arg_max(json_extract_string(detail, '$.fetched_to'), ran_at) FROM ingest_log "
                       "WHERE dataset = 'delisted' AND status = 'ok' AND list_contains(?, key) "
                       "AND json_extract_string(detail, '$.fetched_to') IS NOT NULL GROUP BY key",
                       [list(series)]).fetchall()
    return {k: date.fromisoformat(v) for k, v in rows if v}


def exit_coverage(con: Any, universe: str) -> pd.DataFrame:
    """The universe's exits on or after its changes table's coverage start (:func:`membership_exits`), each with
    ``covered``: its series has bars across the stint it ends (the three weekday conditions above)."""
    from .constituents import COVERAGE_START

    u = universe.strip().lower()
    exits = membership_exits(con, u)
    cov = COVERAGE_START.get(u)
    if cov is not None:
        exits = exits[exits["end"] >= cov]
    if exits.empty:
        return exits.assign(covered=pd.Series(dtype=bool))
    ends = pd.to_datetime(exits["end"]).dt.date.to_numpy()
    starts = [None if pd.isna(a) else pd.Timestamp(a).date() for a in exits["start"]]
    froms = [max([d for d in (a, cov) if d is not None]) if (a is not None or cov is not None) else None
             for a in starts]
    frame = pd.DataFrame({
        "i": range(len(exits)), "series": exits["series"].to_numpy(), "end": ends,
        "lo": [np.busday_offset(e, -EXIT_SESSIONS, roll="forward").astype(object) for e in ends],
        "frm": [f if f is not None else e for f, e in zip(froms, ends, strict=True)]})
    con.register("exits_incoming", frame)
    try:
        got = {i: (n, first, near) for i, n, first, near in con.execute(
            "SELECT x.i, count(b.date), min(b.date), count(b.date) FILTER (WHERE b.date >= x.lo) "
            "FROM exits_incoming x LEFT JOIN bars_daily b ON b.ticker = x.series AND b.date >= least(x.frm, x.lo) "
            "AND b.date < x.\"end\" GROUP BY x.i").fetchall()}
    finally:
        con.unregister("exits_incoming")
    covered = []
    for i in range(len(frame)):
        n, first, near = got.get(i, (0, None, 0))
        ok = bool(near)
        f = froms[i]
        if ok and f is not None and f < ends[i]:
            span = int(np.busday_count(f, ends[i]))
            ok = (first is not None and int(np.busday_count(f, first)) <= EXIT_SESSIONS
                  and n >= STINT_DENSITY * span)
        covered.append(ok)
    out = exits.reset_index(drop=True).copy()
    out["covered"] = covered
    return out


def survivorship_free(con: Any, universe: str) -> tuple[bool, float]:
    """``(accepted, coverage)`` (completion task 3.8, spec R5). ``coverage``: the share of the universe's exits --
    every company that left it, on or after its changes table's coverage start -- whose price series has bars in
    ``bars_daily`` across the stint it ends, to the exit (:func:`exit_coverage`: weekday tolerances, stricter than
    sessions; exits before the coverage start are not counted) -- the bars a backtest needs to hold the company while
    a member and realise what it was worth when it left. 1.0 when no member has left.
    ``accepted``: a ``delisted_acceptance`` row (written only by tools/accept_delisted.py, on a pass) for the
    committed acceptance list as it is now. Refuses a universe with no dated membership."""
    import duckdb

    from .accept import acceptance_list_sha256

    exits = exit_coverage(con, universe)
    try:
        accepted = bool(con.execute("SELECT count(*) FROM delisted_acceptance WHERE list_sha256 = ?",
                                    [acceptance_list_sha256()]).fetchone()[0])
    except duckdb.CatalogException:   # a warehouse before migration 006 has no acceptance table: not accepted
        accepted = False
    if exits.empty:
        return accepted, 1.0
    return accepted, float(exits["covered"].sum()) / float(len(exits))


def fred_vintage_rows(con: Any, series: str, asof: date) -> pd.DataFrame:
    """Every ``fred_vintages`` row of ``series`` published on or before ``asof`` (``date, realtime_start, value``,
    sorted): what :func:`fred_asof` reads, and what a product hashes into its provenance."""
    df = con.execute("SELECT date, realtime_start, value FROM fred_vintages WHERE series = ? AND realtime_start <= ? "
                     "ORDER BY date, realtime_start", [series.upper().strip(), asof]).df()
    for c in ("date", "realtime_start"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    return df


def fred_asof(con: Any, series: str, asof: date, *, first_release: bool = False) -> pd.Series:
    """A revised series as it stood on ``asof`` (completion task 3.5): for each observation published by then, the
    value of the latest vintage with ``realtime_start <= asof`` -- what a reader of FRED saw that day. Its newest
    observations are their first publication; an older one carries the revisions published by ``asof`` and no
    later. ``first_release=True`` gives instead every observation's FIRST published value (earliest
    ``realtime_start``), revisions ignored even when known by ``asof``. Indexed by ``date``; empty when nothing
    was published by ``asof``. Raises ``DataUnavailable`` when the series has no vintages at all."""
    sid = series.upper().strip()
    if not con.execute("SELECT count(*) FROM fred_vintages WHERE series = ?", [sid]).fetchone()[0]:
        raise DataUnavailable(f"{sid}: no vintages in the warehouse (fred_vintages)")
    rows = fred_vintage_rows(con, sid, asof)
    pick = rows.groupby("date", sort=True)["value"]
    out = (pick.first() if first_release else pick.last()).astype(float)
    out.index = _date_index(out.index)
    out.name = sid
    return out


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
