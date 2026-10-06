"""Daily bars ingest (compute plan C3). Vendors are replaced by functions that
serve the committed real fixture bars (fixtures/history), so no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fixtures, prices
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse.constituents import SURVIVORSHIP
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest import HANDLERS, bars
from ohcamel_quant.warehouse.ingest.base import IngestFailed, LocalContext

SPY, _ = fixtures.history("SPY")  # Alpaca daily bars 2016-06-01..2026-06-01
SPY.index = SPY.index.astype("datetime64[ns]")


def vendor(name, frame, calls=None):
    def fetch(ticker, start, end, settings):
        if calls is not None:
            calls.append((name, ticker, start))
        out = frame[frame.index >= pd.Timestamp(start)]
        if out.empty:
            raise DataUnavailable(f"{name}: no bars for {ticker}")
        return out.copy(), Provenance.now(name, symbol=ticker, notes=[f"{name} test vendor"])
    return fetch


def failing(name, msg):
    def fetch(ticker, start, end, settings):
        raise DataUnavailable(f"{name}: {msg}")
    return fetch


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))  # Tue 10:00 EDT
    s = Settings(offline=False, data_dir=tmp_path, warehouse_path=tmp_path / "w.duckdb",
                 price_providers=["alpaca", "yahoo"], APCA_API_KEY_ID=None, APCA_API_SECRET_KEY=None)
    return LocalContext(market=MarketData(s)), tmp_path / "w.duckdb"


def stored(path, ticker="SPY"):
    with open_ro(path) as con:
        return con.execute("SELECT count(*), max(date), min(source), max(source) FROM bars_daily WHERE ticker = ?",
                           [ticker]).fetchone()


def last_log(path, key="SPY"):
    with open_ro(path) as con:
        return con.execute("SELECT status, data_asof, detail FROM ingest_log WHERE dataset = 'bars_daily' "
                           "AND key = ? ORDER BY ran_at DESC LIMIT 1", [key]).fetchone()


def test_backfill_uses_yahoo_first_for_full_history(setup, monkeypatch):
    # provider_order: a start before 2016 moves alpaca after yahoo (data/prices.py).
    ctx, path = setup
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY, calls))
    spec = bars.run_bars_daily({"tickers": ["spy"]}, ctx)
    assert calls[0] == ("yahoo", "SPY", prices.DEFAULT_START)  # the provider path's own default, 1990-01-01
    assert stored(path) == (len(SPY), date(2026, 6, 1), "yahoo", "yahoo")
    assert spec["data_asof"] == "2026-06-01" and spec["provenance"][0]["source"] == "yahoo"
    assert json.loads(last_log(path)[2])["requested_start"] == "1990-01-01"


def test_years_or_start_narrow_the_window(setup, monkeypatch):
    ctx, path = setup
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    bars.run_bars_daily({"tickers": ["SPY"], "years": 20, "full": True}, ctx)
    bars.run_bars_daily({"tickers": ["SPY"], "start": "2010-01-04", "full": True}, ctx)
    assert [c[2] for c in calls] == [date(2006, 6, 2), date(2010, 1, 4)]  # 20y before Tue 2026-06-02
    assert json.loads(last_log(path)[2])["requested_start"] == "2010-01-04"


def test_fallback_records_the_vendor_that_served(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", failing("yahoo", "HTTP 429"))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path)[2] == "alpaca"
    status, asof, detail = last_log(path)
    assert status == "ok" and "yahoo: HTTP 429" in json.loads(detail)["fallback_from"]
    # Alpaca is asked from 2016 at the earliest, so the log must not claim 1990 coverage.
    assert json.loads(detail)["requested_start"] == prices.ALPACA_HISTORY_START.isoformat()


def test_incremental_appends_after_the_overlap(setup, monkeypatch):
    ctx, path = setup
    early = SPY[SPY.index <= "2026-05-15"]
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", early))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path)[1] == date(2026, 5, 15)
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert calls == [("yahoo", "SPY", date(2026, 5, 5))]  # 10 calendar days before 2026-05-15
    assert stored(path)[:2] == (len(SPY), date(2026, 6, 1))
    detail = json.loads(last_log(path)[2])
    assert detail["mode"] == "incremental" and "requested_start" not in detail  # only full fetches prove coverage


def test_readjusted_history_triggers_full_replace(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    readj = SPY.copy()
    readj["adj_close"] *= 0.99  # a new dividend rescales every adjusted close
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", readj))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert json.loads(last_log(path)[2])["mode"] == "full"
    with open_ro(path) as con:
        first = con.execute("SELECT adj_close FROM bars_daily WHERE ticker = 'SPY' ORDER BY date LIMIT 1").fetchone()[0]
    assert first == pytest.approx(SPY["adj_close"].iloc[0] * 0.99, rel=1e-12)  # whole history replaced
    assert stored(path)[0] == len(SPY)


def test_vendor_failure_keeps_last_good_data_asof(tmp_path, monkeypatch):
    # Compute plan Review Focus 4: a vendor fails at 02:00 -> data_asof stays at the last good date.
    path = build_fixture_warehouse(tmp_path / "w.duckdb")  # SPY through 2026-06-01
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 3, 6, 0, tzinfo=UTC))
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path,
                                                  price_providers=["alpaca"])))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", failing("alpaca", "HTTP 500"))
    before = stored(path)
    with pytest.raises(IngestFailed, match="alpaca: HTTP 500"):
        bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    status, asof, detail = last_log(path)
    assert (status, asof) == ("failed", date(2026, 6, 1)) and "alpaca: HTTP 500" in detail
    assert stored(path) == before  # bars untouched
    assert last_log(path, "*")[:2] == ("failed", date(2026, 6, 1))


def test_rerun_is_idempotent(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    first = stored(path)
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path) == first


def test_default_tickers_come_from_the_universe(tmp_path, monkeypatch):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        n = con.execute("SELECT count(DISTINCT ticker) FROM universe_members").fetchone()[0]
    tickers = bars.universe_tickers(path)
    assert len(tickers) == n and tickers == sorted(tickers)
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path,
                                                  warehouse_path=tmp_path / "empty.duckdb")))
    with pytest.raises(IngestFailed, match="no universe"):
        bars.run_bars_daily({}, ctx)


def test_universe_runs_carry_the_survivorship_note(tmp_path, monkeypatch):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        con.execute("INSERT INTO universe_members VALUES ('sp500', 'SPY', 'x', NULL, 't', ?)", [SURVIVORSHIP])
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path,
                                                  price_providers=["yahoo"])))
    assert bars.run_bars_daily({}, ctx)["survivorship"] == SURVIVORSHIP
    assert HANDLERS["ingest.bars_daily"] is bars.run_bars_daily
