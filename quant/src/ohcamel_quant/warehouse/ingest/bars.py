"""``ingest.bars_daily`` (compute plan C3): daily bars for the universe, full
history from ``prices.DEFAULT_START`` (the provider path's own default),
from data/prices.py's vendor chain, one vendor per ticker, incremental by
``max(date)`` with an overlap check, full replace on re-adjustment.

``close`` is split-adjusted and ``adj_close`` fully adjusted (splits and
dividends) where the vendor provides it; Stooq has no adjusted close
(``adj_close = close``) and its provenance says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from ...data import prices
from ...data.base import DataUnavailable, Provenance
from ..constituents import SURVIVORSHIP
from ..db import open_rw, warehouse_path
from ..freshness import NY
from .base import IngestFailed, KeyState, Written, insert_frame, run_ingest, utcnow

OVERLAP_DAYS = 10  # calendar days, >= prices.OVERLAP_SESSIONS (5) sessions
INSERT = ("INSERT OR REPLACE INTO bars_daily SELECT ticker, CAST(date AS DATE), open, high, low, close, adj_close, "
          "volume, source, fetched_at FROM incoming")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class BarsPayload:
    frame: pd.DataFrame
    prov: Provenance
    mode: str                 # 'full' | 'incremental'
    fallback_from: list[str]
    requested_start: date | None = None  # full fetches only: what the serving vendor was asked for


def _years_before(d: date, years: int) -> date:
    return date(d.year - years, d.month, 28 if (d.month, d.day) == (2, 29) else d.day)


def universe_tickers(path: Any) -> list[str]:
    with open_rw(path) as con:
        return [r[0] for r in con.execute("SELECT DISTINCT ticker FROM universe_members ORDER BY ticker").fetchall()]


def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
    heads = con.execute("SELECT ticker, max(date), arg_max(source, date) FROM bars_daily "
                        "WHERE list_contains(?, ticker) GROUP BY ticker", [keys]).fetchall()
    tails = con.execute(
        "SELECT ticker, date, close, adj_close FROM (SELECT ticker, date, close, adj_close, "
        "row_number() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn FROM bars_daily "
        "WHERE list_contains(?, ticker)) WHERE rn <= 15 ORDER BY ticker, date", [keys]).df()
    out = {}
    for ticker, last, source in heads:
        t = tails[tails["ticker"] == ticker]
        tail = pd.DataFrame({"close": t["close"].to_numpy(), "adj_close": t["adj_close"].to_numpy()},
                            index=pd.DatetimeIndex(pd.to_datetime(t["date"])).astype("datetime64[ns]"))
        out[ticker] = KeyState(last, {"source": source, "tail": tail})
    return out


def _ns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.DatetimeIndex(df.index).astype("datetime64[ns]")
    return df


def fetch_bars(ticker: str, state: KeyState, settings: Any, *, start: date, full: bool) -> BarsPayload:
    reasons: list[str] = []
    src = state.extra.get("source")
    if state.data_asof is not None and src in prices.PROVIDERS and not full:
        frm = state.data_asof - timedelta(days=OVERLAP_DAYS)
        try:
            new, prov = prices.PROVIDERS[src](ticker, frm, None, settings)
            new = _ns(new)
            prices.merge_incremental(state.extra["tail"], new)  # raises on re-adjusted history
            return BarsPayload(new, prov, "incremental", [])
        except DataUnavailable as e:
            reasons.append(f"incremental {src}: {e}")
    for name in prices.provider_order(ticker, start, settings):
        try:
            df, prov = prices.PROVIDERS[name](ticker, start, None, settings)
            asked = max(start, prices.ALPACA_HISTORY_START) if name == "alpaca" else start
            return BarsPayload(_ns(df), prov, "full", reasons, asked)
        except DataUnavailable as e:
            reasons.append(str(e))
        except Exception as e:  # noqa: BLE001 - a malformed vendor payload must not stop the chain
            reasons.append(f"{name}: unexpected response ({type(e).__name__}: {e})")
    raise DataUnavailable("; ".join(reasons) or f"{ticker}: no price provider configured")


def write_bars(con: Any, ticker: str, state: KeyState, p: BarsPayload) -> Written:
    frame = p.frame[prices.COLUMNS].reset_index()
    frame.columns = ["date", *prices.COLUMNS]
    frame.insert(0, "ticker", ticker)
    frame["source"] = p.prov.source
    frame["fetched_at"] = utcnow()
    if p.mode == "full":
        con.execute("DELETE FROM bars_daily WHERE ticker = ?", [ticker])
    insert_frame(con, INSERT, frame)
    last = con.execute("SELECT max(date) FROM bars_daily WHERE ticker = ?", [ticker]).fetchone()[0]
    detail = {
        "source": p.prov.source, "mode": p.mode, "first": str(frame["date"].min().date()),
        "last": str(last), "fallback_from": "; ".join(p.fallback_from),
        "vendor_notes": p.prov.detail.get("notes"),
    }
    if p.requested_start is not None:
        detail["requested_start"] = p.requested_start.isoformat()  # C1 bars_coverage
    return Written(len(frame), last, detail)


def run_bars_daily(params: dict, ctx: Any) -> dict:
    settings = ctx.market.settings
    full = bool(params.get("full", False))
    given = [t.upper().strip() for t in params.get("tickers") or [] if str(t).strip()]
    from_universe = not given
    tickers = given or universe_tickers(warehouse_path(settings))
    built_universe = False
    if not tickers and from_universe:
        # A fresh warehouse: build the universe here (this job already holds
        # the writer) rather than fail every weekday until Sunday's
        # ingest.universes. Found 2026-10-08 when the backfill raced it.
        from . import universes

        universes.run_universes({}, ctx)
        tickers = universe_tickers(warehouse_path(settings))
        built_universe = True
    if not tickers:
        raise IngestFailed("bars_daily: no universe -- run ingest.universes first")
    if params.get("start"):
        start = date.fromisoformat(str(params["start"]))
    elif params.get("years"):
        start = _years_before(_now().astimezone(NY).date(), int(params["years"]))
    else:
        start = prices.DEFAULT_START
    notes = [f"window from {start.isoformat()}; one vendor per ticker; Alpaca history begins 2016, "
             "so windows before 2016 are served by Yahoo first (data/prices.py provider order)"]
    if built_universe:
        notes.append("universe built on demand: the warehouse had none (ingest.universes ran inside this job)")
    return run_ingest(
        dataset="bars_daily", keys=list(dict.fromkeys(tickers)), ctx=ctx, read_state=read_state,
        fetch=lambda key, state, s: [fetch_bars(key, state, s, start=start, full=full)],
        write=write_bars, notes=notes, survivorship=SURVIVORSHIP if from_universe else None)
