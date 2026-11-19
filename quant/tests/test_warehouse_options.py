"""Nightly option snapshots from Cboe delayed chains (compute plan C6)."""

from __future__ import annotations

import json
from datetime import date, datetime

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


# --- Task 3.4: what a surface needs, and a pull before the close --------------------------------------------------
# CONSTRUCTED payloads in Cboe's shape (timestamp in UTC, last_trade_time in New York time, as observed live on
# 2026-11-19): a Wednesday's 15:45 pull (quotes 15:30, the 15-minute delay) and its 16:20 pull (the 16:00 close).
def _payload(ts_utc, ltt_ny, spot, mid):
    return {"timestamp": ts_utc, "data": {"symbol": "SPY", "current_price": spot, "last_trade_time": ltt_ny,
                                          "options": [
        {"option": "SPY261120C00735000", "bid": round(mid - 0.05, 2), "ask": round(mid + 0.05, 2), "iv": 0.1432, "open_interest": 900,
         "volume": 40, "last_trade_price": mid},
        {"option": "SPY261120P00735000", "bid": round(mid - 0.25, 2), "ask": round(mid - 0.15, 2), "iv": 0.0, "open_interest": 700,
         "volume": 30, "last_trade_price": round(mid - 0.2, 2)}]}}


PRE = _payload("2026-11-18 20:45:09", "2026-11-18T15:30:08", 735.50, 4.10)      # CONSTRUCTED
POST = _payload("2026-11-18 21:20:04", "2026-11-18T16:00:00", 736.20, 4.40)     # CONSTRUCTED


def rows(tmp_path):
    with open_ro(tmp_path / "w.duckdb") as con:
        return con.execute('SELECT pull, "asof", cp, bid, spot, quote_ts, quote_ts_source, vendor_iv '
                           'FROM option_snapshots ORDER BY pull, cp').fetchall()


@respx.mock
def test_two_pulls_on_one_day_are_two_sets_of_rows(tmp_path):
    route = respx.get(cboe.URL.format(sym="SPY"))
    c = ctx(tmp_path)
    route.mock(return_value=httpx.Response(200, json=PRE))
    options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1545"}, c)
    route.mock(return_value=httpx.Response(200, json=POST))
    spec = options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1620"}, c)
    d = date(2026, 11, 18)
    pre_ts, post_ts = datetime(2026, 11, 18, 20, 30, 8), datetime(2026, 11, 18, 21, 0, 0)   # naive UTC
    assert rows(tmp_path) == [
        ("1545", d, "C", 4.05, 735.50, pre_ts, "cboe:last_trade_time", 0.1432),
        ("1545", d, "P", 3.85, 735.50, pre_ts, "cboe:last_trade_time", None),    # iv 0 = no vendor value
        ("1620", d, "C", 4.35, 736.20, post_ts, "cboe:last_trade_time", 0.1432),
        ("1620", d, "P", 4.15, 736.20, post_ts, "cboe:last_trade_time", None)]
    assert spec["data_asof"] == "2026-11-18"
    with open_ro(tmp_path / "w.duckdb") as con:
        detail = json.loads(con.execute("SELECT detail FROM ingest_log WHERE key = 'SPY' ORDER BY ran_at DESC "
                                        "LIMIT 1").fetchone()[0])
    assert detail["pull"] == "1620" and detail["quote_ts_utc"] == "2026-11-18T21:00:00"


@respx.mock
def test_a_rerun_of_one_pull_replaces_its_rows_and_leaves_the_other(tmp_path):
    route = respx.get(cboe.URL.format(sym="SPY"))
    c = ctx(tmp_path)
    route.mock(return_value=httpx.Response(200, json=PRE))
    options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1545"}, c)
    route.mock(return_value=httpx.Response(200, json=POST))
    options.run_option_snapshots({"underlyings": ["SPY"]}, c)                    # no pull given: the 16:20 one
    options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1620"}, c)
    assert [r[0] for r in rows(tmp_path)] == ["1545", "1545", "1620", "1620"]


@respx.mock
def test_a_pre_close_pull_that_finds_closing_quotes_stores_nothing(tmp_path):
    """Thanksgiving 2026-11-26: the 15:45 pull reads Wednesday's close. Stored as '1545' it would pass closing quotes
    off as pre-close ones (and on a 13:00 half day the same), so the key stores nothing and says why."""
    holiday = _payload("2026-11-26 20:45:03", "2026-11-25T16:00:00", 736.2, 4.4)     # CONSTRUCTED
    half_day = _payload("2026-11-27 20:45:03", "2026-11-27T13:00:00", 737.0, 4.2)    # CONSTRUCTED
    route = respx.get(cboe.URL.format(sym="SPY"))
    for payload in (holiday, half_day):
        route.mock(return_value=httpx.Response(200, json=payload))
        spec = options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1545"}, ctx(tmp_path))
        s = spec["tables"]["ingest_summary"].iloc[0]
        assert s["status"] == "ok" and s["rows"] == 0 and "closing quotes" in json.loads(s["detail"])["skipped"]
    assert rows(tmp_path) == []


def test_an_unknown_pull_is_refused(tmp_path):
    with pytest.raises(ValueError, match="pull"):
        options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1600"}, ctx(tmp_path))


@respx.mock
def test_a_pre_close_pull_stamped_by_the_payload_clock_stores_nothing(tmp_path):
    """Fix round 1: a 15:45 payload without last_trade_time is stamped from Cboe's `timestamp` (UTC). If that reading
    were ever wrong the stamp would be five hours off with nothing to show it, and that fallback is exactly the
    signature: a pre-close pull is stored only when the quote time came from last_trade_time."""
    no_ltt = _payload("2026-11-18 20:45:09", None, 735.5, 4.1)                       # CONSTRUCTED
    del no_ltt["data"]["last_trade_time"]
    respx.get(cboe.URL.format(sym="SPY")).mock(return_value=httpx.Response(200, json=no_ltt))
    spec = options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1545"}, ctx(tmp_path))
    s = spec["tables"]["ingest_summary"].iloc[0]
    assert s["rows"] == 0 and "last_trade_time" in json.loads(s["detail"])["skipped"]
    assert rows(tmp_path) == []
