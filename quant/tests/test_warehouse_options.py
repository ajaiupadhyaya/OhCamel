"""Nightly option snapshots from Cboe delayed chains (compute plan C6)."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import cboe, http
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse.db import open_ro
from ohcamel_quant.warehouse.ingest import HANDLERS, options
from ohcamel_quant.warehouse.ingest.base import IngestFailed, LocalContext

# Cboe's delayed-quotes shape, as in quant/tests/test_data_fred_french_cboe.py, plus an
# adjusted root (SPY1) that collides with SPY240621P00530000 on (expiry, strike, cp).
CBOE_SPY = {"timestamp": "2024-06-07 16:15:03", "data": {
    "symbol": "SPY", "current_price": 534.01, "options": [
        {"option": "SPY240607C00400000", "bid": 133.5, "ask": 134.9, "iv": 0.0, "open_interest": 305,
         "volume": 12, "last_trade_price": 134.2},
        {"option": "SPY240621P00530000", "bid": 3.1, "ask": 3.15, "iv": 0.1123, "open_interest": 25000,
         "volume": 8123, "last_trade_price": 3.12},
        {"option": "SPY1240621P00530000", "bid": 3.0, "ask": 3.4, "open_interest": 1, "volume": 0},
        {"option": "garbage", "bid": 1}]}}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


def ctx(tmp_path):
    return LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, http_retries=0,
                                                   warehouse_path=tmp_path / "w.duckdb")))


def snap(tmp_path):
    with open_ro(tmp_path / "w.duckdb") as con:
        return con.execute('SELECT underlying, "asof", expiry, strike, cp, bid, ask, last, volume, open_interest, '
                           "source FROM option_snapshots ORDER BY expiry, strike").fetchall()


def test_underlyings_are_the_thirty():
    u = options.OPTION_UNDERLYINGS
    assert len(u) == 30 == len(set(u))
    assert {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
            "SPY", "QQQ", "IWM", "TLT", "GLD"} <= set(u)


@respx.mock
def test_snapshot_drops_nonstandard_roots(tmp_path):
    respx.get(cboe.URL.format(sym="SPY")).mock(return_value=httpx.Response(200, json=CBOE_SPY))
    spec = options.run_option_snapshots({"underlyings": ["SPY"]}, ctx(tmp_path))
    assert snap(tmp_path) == [
        ("SPY", date(2024, 6, 7), date(2024, 6, 7), 400.0, "C", 133.5, 134.9, 134.2, 12.0, 305.0, "cboe"),
        ("SPY", date(2024, 6, 7), date(2024, 6, 21), 530.0, "P", 3.1, 3.15, 3.12, 8123.0, 25000.0, "cboe")]
    assert spec["data_asof"] == "2024-06-07"  # the chain's own timestamp, not the run date
    with open_ro(tmp_path / "w.duckdb") as con:
        detail = json.loads(con.execute("SELECT detail FROM ingest_log WHERE key = 'SPY'").fetchone()[0])
    assert detail["dropped_nonstandard_roots"] == 1 and detail["spot"] == 534.01


@respx.mock
def test_snapshot_rerun_is_idempotent(tmp_path):
    respx.get(cboe.URL.format(sym="SPY")).mock(return_value=httpx.Response(200, json=CBOE_SPY))
    c = ctx(tmp_path)
    options.run_option_snapshots({"underlyings": ["SPY"]}, c)
    first = snap(tmp_path)
    options.run_option_snapshots({"underlyings": ["SPY"]}, c)
    assert snap(tmp_path) == first


@respx.mock
def test_cboe_failure_is_logged(tmp_path):
    respx.get(cboe.URL.format(sym="SPY")).mock(return_value=httpx.Response(404))
    with pytest.raises(IngestFailed, match="SPY"):
        options.run_option_snapshots({"underlyings": ["SPY"]}, ctx(tmp_path))
    assert HANDLERS["ingest.option_snapshots"] is options.run_option_snapshots
