"""Regression tests for /api/market defects found in review: correlation on
mismatched calendars, overview cache stampede, cached outage payloads, and
ticker validation."""

from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.api.routers import market as mr


@pytest.fixture(autouse=True)
def _fresh_cache():
    mr.clear_cache()
    yield
    mr.clear_cache()


def test_correlation_aligns_prices_before_differencing(market):
    """SPY against SPY with every third session missing (a vendor gap or a
    different trading calendar) is the same asset: correlation must be 1.
    Differencing each series on its own calendar first (the old code) pairs
    two-session returns with one-session returns and gives ~0.8."""
    spy = market.ohlcv("SPY").data["adj_close"]
    gappy = spy.iloc[[i for i in range(len(spy)) if i % 3 != 1]]
    c = mr.correlation_matrix({"SPY": spy, "SPY_GAPPY": gappy})
    assert c["tickers"] == ["SPY", "SPY_GAPPY"]
    assert np.array(c["matrix"])[0, 1] == pytest.approx(1.0, abs=1e-12)
    assert 150 <= c["n_obs"] <= 175  # ~2/3 of a year of common sessions
    # the defect, reproduced: per-series returns, then aligned
    w = {k: s.loc[s.index[-1] - pd.Timedelta(days=365):].pct_change().iloc[1:]
         for k, s in {"a": spy, "b": gappy}.items()}
    naive = pd.DataFrame(w).dropna().corr().iloc[0, 1]
    assert naive < 0.95


def test_correlation_window_is_365_days_of_common_sessions(market):
    spy = market.ohlcv("SPY").data["adj_close"]
    tlt = market.ohlcv("TLT").data["adj_close"]
    c = mr.correlation_matrix({"SPY": spy, "TLT": tlt})
    px = pd.concat([spy, tlt], axis=1, keys=["SPY", "TLT"]).dropna()
    px = px.loc[px.index[-1] - pd.Timedelta(days=365):]
    r = px.pct_change().iloc[1:]
    assert c["n_obs"] == len(r)
    assert np.array(c["matrix"])[0, 1] == pytest.approx(r.corr().iloc[0, 1], abs=1e-12)
    assert mr.correlation_matrix({"SPY": spy})["n_obs"] == 0


def test_overview_cache_single_flight():
    calls = []

    def build():
        calls.append(1)
        time.sleep(0.2)
        return {"rows": [{"error": None}]}

    out: list = []
    ts = [threading.Thread(target=lambda: out.append(mr._cached(("k",), 60, build))) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1 and len(out) == 5 and all(o is out[0] for o in out)


def test_payload_with_failed_rows_expires_quickly(monkeypatch):
    calls = []

    def build():
        calls.append(1)
        return {"rows": [{"error": "yahoo: HTTP 429"}]}

    mr._cached(("e",), 300, build)
    mr._cached(("e",), 300, build)
    assert len(calls) == 1
    monkeypatch.setattr(mr, "ERROR_TTL_S", 0.0)
    mr._cached(("e",), 300, build)
    assert len(calls) == 2


def test_ticker_validation(client):
    assert client.get("/api/market/history/SPY;DROP").status_code == 422
    assert client.get("/api/market/history/" + "A" * 30).status_code == 422
    r = client.get("/api/market/overview", params={"tickers": "SPY,<script>"})
    assert r.status_code == 422 and "invalid ticker" in r.json()["detail"]
    # legitimate symbol shapes still pass validation (offline -> 503, not 422)
    for t in ("BRK.B", "^VIX", "BTC-USD", "EURUSD=X"):
        assert client.get(f"/api/market/history/{t}").status_code == 503, t
