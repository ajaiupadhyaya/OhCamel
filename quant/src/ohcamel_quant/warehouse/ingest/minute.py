"""``ingest.bars_minute`` (compute plan C4): Alpaca IEX 1-minute bars for 50
liquid names, 2 years back then incremental, feeding P2's HAR-RV.

Only the regular session [09:30, 16:00) New York is stored (~390 bars a day:
50 x 390 x 252 x 2 ~ 9.8M rows). ``ts`` is naive UTC. Each calendar month is
fetched and written on its own, so memory holds one month of one ticker.
``adjustment=split``: a split re-adjusts history, detected on the overlap of
the incremental refetch, and then the ticker is refetched in full.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote as urlquote

import numpy as np
import pandas as pd

from ...data import http, prices
from ...data.base import DataUnavailable
from ..freshness import NY
from .base import KeyState, Written, insert_frame, run_ingest

MINUTE_TICKERS = (
    "SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "XLF", "XLE", "XLK", "XLV",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V",
    "MA", "UNH", "XOM", "LLY", "JNJ", "PG", "HD", "COST", "WMT", "BAC",
    "KO", "PEP", "ABBV", "MRK", "CVX", "ADBE", "CRM", "NFLX", "AMD", "ORCL",
    "CSCO", "INTC", "QCOM", "TXN", "DIS", "MCD", "CAT", "BA", "GS", "PLTR",
)
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 0)
OVERLAP = timedelta(days=1)
RTOL = 5e-5
INSERT = ("INSERT OR REPLACE INTO bars_minute SELECT ticker, ts, open, high, low, close, volume, source "
          "FROM incoming")


def _now() -> datetime:
    return datetime.now(UTC)


def parse_minute_bars(bars: list[dict[str, Any]]) -> pd.DataFrame:
    cols = ["ts", "open", "high", "low", "close", "volume"]
    if not bars:
        return pd.DataFrame(columns=cols)
    raw = pd.DataFrame(bars)
    ts = pd.to_datetime(raw["t"], utc=True)
    local = ts.dt.tz_convert(NY).dt.time
    keep = (local >= SESSION_OPEN) & (local < SESSION_CLOSE)
    df = pd.DataFrame({"ts": ts.dt.tz_localize(None).astype("datetime64[ns]"),
                       "open": raw["o"].astype(float), "high": raw["h"].astype(float),
                       "low": raw["l"].astype(float), "close": raw["c"].astype(float),
                       "volume": raw["v"].astype(float)})[keep.to_numpy()]
    return df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


def month_ranges(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    out, lo = [], start
    while lo < end:
        nxt = datetime(lo.year + (lo.month == 12), lo.month % 12 + 1, 1, tzinfo=lo.tzinfo)
        hi = min(nxt, end)
        out.append((lo, hi))
        lo = hi
    return out


def _pages(sym: str, lo: datetime, hi: datetime, settings: Any) -> list[dict[str, Any]]:
    url = f"{prices.ALPACA_BASE}/stocks/{urlquote(sym, safe='.')}/bars"
    params: dict[str, Any] = {"timeframe": "1Min", "adjustment": "split", "feed": "iex", "limit": 10000,
                              "start": lo.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": hi.strftime("%Y-%m-%dT%H:%M:%SZ")}
    out: list[dict[str, Any]] = []
    for _ in range(1000):
        payload = http.get_json(url, settings, params=params, headers=prices._alpaca_headers(settings),
                                source="alpaca")
        out.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            return out
        params["page_token"] = token
    raise DataUnavailable(f"alpaca: minute pagination did not terminate for {sym}")


def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
    out = {}
    for ticker, last in con.execute("SELECT ticker, max(ts) FROM bars_minute WHERE list_contains(?, ticker) "
                                    "GROUP BY ticker", [keys]).fetchall():
        tail = con.execute("SELECT ts, close FROM bars_minute WHERE ticker = ? AND ts >= ? ORDER BY ts",
                           [ticker, last - OVERLAP]).df()
        tail["ts"] = pd.to_datetime(tail["ts"]).astype("datetime64[ns]")
        out[ticker] = KeyState(last.date(), {"last_ts": last, "tail": tail})
    return out


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def run_bars_minute(params: dict, ctx: Any) -> dict:
    years = int(params.get("years", 2))
    tickers = [t.upper().strip() for t in params.get("tickers") or MINUTE_TICKERS]
    now = _now()
    end = now - prices.SIP_DELAY
    backfill = (datetime.combine(date.fromisoformat(params["start"]), time(0), UTC) if params.get("start")
                else now - timedelta(days=365 * years + years // 4))

    def fetch(key: str, state: KeyState, s: Any):
        if not (s.alpaca_key_id and s.alpaca_secret_key):
            raise DataUnavailable("alpaca: API keys not configured")
        sym = prices.alpaca_symbol(key)
        start = backfill
        state.extra["mode"] = "full"
        if state.extra.get("last_ts") is not None:
            lo = _utc(state.extra["last_ts"] - OVERLAP)
            new = parse_minute_bars(_pages(sym, lo, end, s))
            if new.empty:  # nothing new (holiday, or vendor lag): keep what is stored
                state.extra["mode"] = "incremental"
                return
            tail = state.extra["tail"]
            both = tail.merge(new, on="ts", suffixes=("_old", "_new"))
            if len(both) and np.allclose(both["close_old"], both["close_new"], rtol=RTOL, atol=0.0):
                state.extra["mode"] = "incremental"
                yield new
                return
        for lo, hi in month_ranges(start, end):
            yield parse_minute_bars(_pages(sym, lo, hi, s))

    def write(con: Any, key: str, state: KeyState, frame: pd.DataFrame) -> Written:
        if state.extra.get("mode") == "full" and not state.extra.get("cleared") and len(frame):
            con.execute("DELETE FROM bars_minute WHERE ticker = ?", [key])
            state.extra["cleared"] = True
        f = frame.copy()
        f.insert(0, "ticker", key)
        f["source"] = "alpaca:iex"
        if len(f):
            insert_frame(con, INSERT, f)
        last = con.execute("SELECT max(ts) FROM bars_minute WHERE ticker = ?", [key]).fetchone()[0]
        return Written(len(f), last.date() if last else None, {"source": "alpaca:iex", "mode": state.extra["mode"]})

    notes = ["Alpaca IEX feed (single venue): volumes are IEX-only, prices are IEX trades",
             "regular session only, 09:30-16:00 New York; ts is UTC"]
    return run_ingest(dataset="bars_minute", keys=list(dict.fromkeys(tickers)), ctx=ctx, read_state=read_state,
                      fetch=fetch, write=write, notes=notes)
