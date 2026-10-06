"""Minute bars (compute plan C4): Alpaca IEX 1-minute bars, regular session only,
fetched and written one month at a time. Alpaca is mocked with respx."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http, prices
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse.db import open_ro
from ohcamel_quant.warehouse.ingest import HANDLERS, minute
from ohcamel_quant.warehouse.ingest.base import IngestFailed, LocalContext

URL = f"{prices.ALPACA_BASE}/stocks/SPY/bars"


def bar(t, c, v=100.0):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": v}


# 2026-06-01 is EDT (UTC-4): 13:30Z = 09:30 NY (in), 13:29Z = 09:29 (out),
# 19:59Z = 15:59 (in), 20:00Z = 16:00 (out: the session is [09:30, 16:00)).
DAY = [bar("2026-06-01T13:29:00Z", 529.0), bar("2026-06-01T13:30:00Z", 530.0),
       bar("2026-06-01T13:31:00Z", 530.5), bar("2026-06-01T19:59:00Z", 531.0),
       bar("2026-06-01T20:00:00Z", 531.2)]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    monkeypatch.setattr(minute, "_now", lambda: datetime(2026, 6, 2, 0, 0, tzinfo=UTC))


def ctx(tmp_path, keys=True):
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=tmp_path / "w.duckdb",
                 APCA_API_KEY_ID="k" if keys else None, APCA_API_SECRET_KEY="s" if keys else None)
    return LocalContext(market=MarketData(s))


def serve(bars_list, scale=1.0, page_size=None):
    """respx side effect: bars inside the request's [start, end), optionally paginated."""
    def handler(request):
        p = request.url.params
        lo, hi = pd.Timestamp(p["start"]), pd.Timestamp(p["end"])
        sel = [dict(b, c=b["c"] * scale) for b in bars_list if lo <= pd.Timestamp(b["t"]) < hi]
        start = int(p.get("page_token", 0))
        n = page_size or len(sel) or 1
        page = sel[start:start + n]
        token = str(start + n) if start + n < len(sel) else None
        return httpx.Response(200, json={"bars": page, "next_page_token": token})
    return handler


def rows(tmp_path):
    with open_ro(tmp_path / "w.duckdb") as con:
        return con.execute("SELECT ts, close FROM bars_minute WHERE ticker = 'SPY' ORDER BY ts").fetchall()


def test_parse_keeps_the_regular_session_only():
    df = minute.parse_minute_bars(DAY)
    assert [str(t) for t in df["ts"]] == ["2026-06-01 13:30:00", "2026-06-01 13:31:00", "2026-06-01 19:59:00"]


def test_month_ranges():
    r = minute.month_ranges(datetime(2026, 1, 15), datetime(2026, 3, 2))
    assert r == [(datetime(2026, 1, 15), datetime(2026, 2, 1)), (datetime(2026, 2, 1), datetime(2026, 3, 1)),
                 (datetime(2026, 3, 1), datetime(2026, 3, 2))]


def test_minute_tickers_are_fifty_unique():
    assert len(minute.MINUTE_TICKERS) == 50 == len(set(minute.MINUTE_TICKERS))


@respx.mock
def test_backfill_month_chunks_and_pagination(tmp_path):
    route = respx.get(URL).mock(side_effect=serve(DAY, page_size=2))
    spec = minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, ctx(tmp_path))
    # chunks [05-30, 06-01) and [06-01, 06-01T23:44Z = now-16min): the first is empty (1 call);
    # the second holds 5 raw bars at 2 per page (3 calls); 2 of the 5 fall outside the session
    assert route.call_count == 4
    assert [c for _, c in rows(tmp_path)] == [530.0, 530.5, 531.0]
    assert spec["data_asof"] == "2026-06-01"
    p = route.calls[-1].request.url.params
    assert p["feed"] == "iex" and p["timeframe"] == "1Min" and p["adjustment"] == "split"


@respx.mock
def test_minute_rerun_is_idempotent(tmp_path):
    respx.get(URL).mock(side_effect=serve(DAY))
    c = ctx(tmp_path)
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    first = rows(tmp_path)
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    assert rows(tmp_path) == first


@respx.mock
def test_minute_split_mismatch_refetches(tmp_path):
    route = respx.get(URL).mock(side_effect=serve(DAY))
    c = ctx(tmp_path)
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    route.side_effect = serve(DAY, scale=0.5)  # a 2:1 split re-adjusts history
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    assert [cl for _, cl in rows(tmp_path)] == [265.0, 265.25, 265.5]  # every stored bar replaced


@respx.mock
def test_empty_vendor_answer_keeps_stored_bars(tmp_path):
    route = respx.get(URL).mock(side_effect=serve(DAY))
    c = ctx(tmp_path)
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    route.side_effect = serve([])  # vendor returns nothing (outage or holiday): never a delete
    minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, c)
    assert [cl for _, cl in rows(tmp_path)] == [530.0, 530.5, 531.0]


@respx.mock
def test_no_keys_fails_cleanly(tmp_path):
    with pytest.raises(IngestFailed, match="keys not configured"):
        minute.run_bars_minute({"tickers": ["SPY"], "start": "2026-05-30"}, ctx(tmp_path, keys=False))
    assert HANDLERS["ingest.bars_minute"] is minute.run_bars_minute
