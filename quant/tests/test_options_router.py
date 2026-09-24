"""/api/options endpoints: offline against real fixtures (realized, calculator), chain
endpoints against a dependency-overridden market serving the numerical test chain,
503s when chains are unavailable, and live checks on real Cboe chains."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from test_options_chain_strategy import EXPIRIES, SIG0, make_chain

from ohcamel_quant.api.routers import options as opt_router
from ohcamel_quant.config import Settings
from ohcamel_quant.data.base import Provenance
from ohcamel_quant.data.market import Dataset, MarketData, get_market


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "notes" in j and "method" in j
    return j


class ChainMarket(MarketData):
    """Offline market whose option_chain serves the numerical test chain."""

    def option_chain(self, ticker: str) -> Dataset:
        return Dataset(make_chain(root=ticker.upper()), [Provenance.now("test-fixture", note="BSM/SSVI numerical chain")])


@pytest.fixture
def chain_client(client):
    opt_router._CACHE.clear()
    client.app.dependency_overrides[get_market] = lambda: ChainMarket()
    yield client
    client.app.dependency_overrides.pop(get_market, None)
    opt_router._CACHE.clear()


# ------------------------------------------------------------------ offline, real fixtures
def test_realized_spy_offline(client):
    j = _ok(client.get("/api/options/realized/SPY"))
    assert j["ticker"] == "SPY" and j["estimator"] == "yang_zhang"
    assert set(j["rolling"]["columns"]) == {"close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang"}
    assert [c["horizon"] for c in j["cone"]] == [10, 21, 42, 63, 126, 252]
    assert all(0 < v < 2 for v in j["current"]["21"].values())
    # chain is online-only -> VRP falls back to VIX (FRED fixture) and says so
    assert any("option chain unavailable" in n for n in j["notes"])
    assert j["vrp"]["implied_source"].startswith("VIXCLS")
    assert j["vrp_history"]["index"] == "VIXCLS" and j["vrp_history"]["mean_premium_forward"] > 0
    assert all(not p["synthetic"] for p in j["provenance"])
    r = client.get("/api/options/realized/SPY", params={"estimator": "parkinson", "window": 63, "years": 3})
    assert _ok(r)["window"] == 63
    assert client.get("/api/options/realized/NOTATICKER").status_code == 503


@pytest.mark.parametrize("path", ["/api/options/expiries/SPY", "/api/options/chain/SPY",
                                  "/api/options/surface/SPY", "/api/options/density/SPY"])
def test_chain_endpoints_503_offline(client, path):
    opt_router._CACHE.clear()
    r = client.get(path)
    assert r.status_code == 503
    assert "option chains are fetched online only" in r.json()["detail"]


def test_strategy_503_offline(client):
    opt_router._CACHE.clear()
    body = {"ticker": "SPY", "legs": [{"qty": 1, "expiry": "2026-12-18", "strike": 500, "type": "C"}]}
    assert client.post("/api/options/strategy", json=body).status_code == 503


def test_price_calculator(client):
    j = _ok(client.post("/api/options/price", json={"S": 42, "K": 40, "T": 0.5, "sigma": 0.2, "r": 0.1}))
    assert j["result"]["price"] == pytest.approx(4.7594, abs=1e-4)          # Hull Example 15.6
    assert j["put"]["price"] == pytest.approx(0.8086, abs=1e-4)
    assert abs(j["parity_check"]["gap"]) < 1e-12
    assert len(j["curves"]["spot"]) == 161
    j = _ok(client.post("/api/options/price", json={"S": 42, "K": 40, "T": 0.5, "price": 4.7594, "r": 0.1}))
    assert j["implied_vol"] == pytest.approx(0.2, abs=1e-4)
    assert client.post("/api/options/price", json={"S": 42, "K": 40, "T": 0.5, "r": 0.1}).status_code == 422
    r = client.post("/api/options/price", json={"S": 42, "K": 40, "T": 0.5, "price": 0.5, "r": 0.1})
    assert r.status_code == 422 and "no-arbitrage" in r.json()["detail"]
    # r omitted -> FRED DGS3MO, which is online-only here
    r = client.post("/api/options/price", json={"S": 42, "K": 40, "T": 0.5, "sigma": 0.2})
    assert r.status_code == 503


# ------------------------------------------------------------------ chain endpoints (overridden market)
def test_expiries_and_chain(chain_client):
    j = _ok(chain_client.get("/api/options/expiries/TESTX"))
    assert [e["slice"] for e in j["expiries"]] == EXPIRIES
    assert all(abs(e["rate"] - 0.045) < 1e-4 and abs(e["div_yield"] - 0.013) < 1e-3 for e in j["expiries"])
    j = _ok(chain_client.get("/api/options/chain/TESTX", params={"expiry": "2026-11-20"}))
    assert j["expiry"] == "2026-11-20" and j["exercise_style"] == "American"
    q = [x for x in j["quotes"] if x["use_smile"]]
    assert len(q) > 40 and all(abs(x["iv"] - x["vendor_iv"]) < 2e-4 for x in q)
    assert j["smile"]["fit"]["rmse_vol_pts"] < 0.01
    calls = [x for x in j["quotes"] if x["type"] == "C" and x["iv"] is not None]
    assert all(0 <= x["delta"] <= 1 for x in calls)
    r = chain_client.get("/api/options/chain/TESTX", params={"expiry": "2031-01-01"})
    assert r.status_code == 422


def test_surface_and_density(chain_client):
    j = _ok(chain_client.get("/api/options/surface/TESTX"))
    assert len(j["smiles"]) == len(EXPIRIES)
    assert all(abs(t["atm_iv"] - SIG0) < 1e-3 for t in j["term_structure"])
    assert j["atm_30d"]["iv"] == pytest.approx(SIG0, abs=1e-3)
    assert 20 < j["vix_style_30d"]["index"] < 30
    iv = np.array(j["grid"]["iv"], dtype=float)
    assert iv.shape == (len(j["grid"]["T"]), len(j["grid"]["k"])) and np.isfinite(iv).all()
    assert not any(c["violation"] for c in j["calendar"])
    d = _ok(chain_client.get("/api/options/density/TESTX", params={"expiry": "2026-12-18"}))
    assert d["checks"]["integral"] == pytest.approx(1.0, abs=1e-4)
    assert d["checks"]["mean"] == pytest.approx(d["forward"], rel=1e-4)
    p = [x["p"] for x in d["prob_below"]]
    assert all(b >= a for a, b in zip(p, p[1:], strict=False))
    assert d["moments"]["skew"] < d["lognormal_moments"]["skew"]
    # the lognormal comparison density is evaluated on the SAME strikes as `density`
    from scipy.stats import norm
    x = np.array(d["strikes"], dtype=float)
    s = d["atm_iv"] * math.sqrt(d["T"])
    pdf = norm.pdf((np.log(x / d["forward"]) + 0.5 * s * s) / s) / (x * s)
    i = np.abs(np.log(x / d["forward"])) < 2 * s
    np.testing.assert_allclose(np.array(d["lognormal_density"], dtype=float)[i], pdf[i], rtol=1e-3)


def test_strategy_endpoint(chain_client):
    body = {"ticker": "TESTX", "legs": [
        {"qty": 1, "expiry": "2026-10-30", "strike": 480, "type": "P"},
        {"qty": -1, "expiry": "2026-10-30", "strike": 460, "type": "P"}]}
    j = _ok(chain_client.post("/api/options/strategy", json=body))
    cost = j["cost"]["mid"]
    assert j["max_profit"] == pytest.approx(2000 - cost, abs=1e-6) and j["max_loss"] == pytest.approx(-cost, abs=1e-6)
    assert len(j["breakevens"]) == 1 and 0 < j["prob_profit"] < 1
    assert set(j["grid"]["curves"]) >= {"today", "expiry"}
    body["legs"].append({"qty": -1, "expiry": "2026-10-30", "strike": 520, "type": "C"})
    j = _ok(chain_client.post("/api/options/strategy", json=body))
    assert j["max_loss"] is None           # -inf serializes to null: unbounded
    assert j["max_loss_unbounded"] is True and j["max_profit_unbounded"] is False
    assert chain_client.post("/api/options/strategy", json={"ticker": "TESTX", "legs": [{"qty": 1}]}).status_code == 422


# ------------------------------------------------------------------ live
@pytest.fixture
def live_client(client, tmp_path):
    opt_router._CACHE.clear()
    client.app.dependency_overrides[get_market] = lambda: MarketData(Settings(offline=False, data_dir=tmp_path))
    yield client
    client.app.dependency_overrides.pop(get_market, None)
    opt_router._CACHE.clear()


@pytest.mark.live
@pytest.mark.parametrize("ticker", ["SPY", "_SPX"])
def test_live_surface(live_client, ticker):
    j = _ok(live_client.get(f"/api/options/surface/{ticker}"))
    assert j["spot"] > 0 and len(j["smiles"]) >= 5
    atm = [t["atm_iv"] for t in j["term_structure"] if t.get("atm_iv") is not None]
    assert all(0.03 < v < 1.5 for v in atm)
    good = [s for s in j["smiles"] if s["fit"]["rmse_vol_pts"] < 2.0]
    assert len(good) >= 0.7 * len(j["smiles"])
    rates = [e["rate"] for e in j["expiries"]]
    assert all(-0.02 < r < 0.15 for r in rates)
    assert j["vix_style_30d"] is None or 5 < j["vix_style_30d"]["index"] < 150
    assert any(p["source"] == "cboe" for p in j["provenance"])
    if ticker == "_SPX":
        assert any(e["settlement"] == "AM" for e in j["expiries"])
        assert j["exercise_style"] == "European"


@pytest.mark.live
def test_live_spy_density_and_realized(live_client):
    d = _ok(live_client.get("/api/options/density/SPY"))
    assert abs(d["checks"]["integral"] - 1) < 0.02
    assert abs(d["checks"]["mean"] / d["forward"] - 1) < 0.01
    r = _ok(live_client.get("/api/options/realized/SPY"))
    assert r["vrp"] is not None and math.isfinite(r["vrp"]["implied"])


def test_bill_yield_to_continuous_reprices_the_bill():
    """DGS3MO (bond-equivalent, simple ACT/365 for a 3-month bill) -> continuous r must
    reproduce the bill price 1 / (1 + y tau); the semi-annual 2 ln(1 + y/2) does not."""
    y, tau = 0.05, 0.25
    r = opt_router.bill_yield_to_continuous(y, tau)
    assert math.exp(-r * tau) == pytest.approx(1.0 / (1.0 + y * tau), rel=1e-14)
    assert r == pytest.approx(0.0496901, abs=1e-7)
    assert abs(r - 2.0 * math.log1p(y / 2.0)) > 2e-4          # ~3 bp at 5%
    with pytest.raises(ValueError):
        opt_router.bill_yield_to_continuous(y, 0.0)


class BadBarsMarket(MarketData):
    """Offline market whose daily bars are all inconsistent (high < low)."""

    def ohlcv(self, ticker, start=None, end=None):
        idx = pd.bdate_range("2024-01-01", periods=40)
        df = pd.DataFrame({"open": 10.0, "high": 9.0, "low": 11.0, "close": 10.0, "adj_close": 10.0,
                           "volume": 1.0}, index=idx)
        return Dataset(df, [Provenance.now("test-fixture", note="inconsistent bars")])


def test_realized_with_no_usable_bars_is_503(client):
    client.app.dependency_overrides[get_market] = lambda: BadBarsMarket()
    try:
        r = client.get("/api/options/realized/SPY")
    finally:
        client.app.dependency_overrides.pop(get_market, None)
    assert r.status_code == 503, r.text
    assert "bars" in r.json()["detail"]


def test_trading_days_between_counts_weekdays():
    # Fri 2026-10-02 -> Fri 2026-10-16: two full weeks = 10 weekdays
    assert opt_router.trading_days_between(pd.Timestamp("2026-10-02 16:00", tz="America/New_York"),
                                           pd.Timestamp("2026-10-16 16:15", tz="America/New_York")) == 10
    assert opt_router.trading_days_between(pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-02")) == 0


def test_density_grids_line_up_and_chain_surface_extras(chain_client):
    d = _ok(chain_client.get("/api/options/density/TESTX", params={"expiry": "2026-12-18"}))
    moves = [m["move"] for m in d["prob_move"]]
    assert moves == list(opt_router.DENSITY_MOVES) == d["move_grid"]
    assert {0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3} <= set(moves)
    mny = {round(p["moneyness"], 12) for p in d["prob_below"]}
    for m in moves:
        assert round(1 - m, 12) in mny and round(1 + m, 12) in mny
    # P(|move| > x) = P(below 1-x) + 1 - P(below 1+x) on the same density
    pb = {round(p["moneyness"], 12): p["p"] for p in d["prob_below"]}
    for row in d["prob_move"]:
        m = row["move"]
        assert row["p"] == pytest.approx(pb[round(1 - m, 12)] + 1 - pb[round(1 + m, 12)], abs=2e-3)

    c = _ok(chain_client.get("/api/options/chain/TESTX", params={"expiry": "2026-11-20"}))
    assert c["slice"]["atm_iv"] == pytest.approx(SIG0, abs=2e-3)

    s = _ok(chain_client.get("/api/options/surface/TESTX"))
    for sm in s["smiles"]:
        mk = sm["market"]
        assert len(mk["vendor_iv"]) == len(mk["iv"])
        assert all(abs(v - i) < 2e-4 for v, i in zip(mk["vendor_iv"], mk["iv"], strict=True)
                   if v is not None and i is not None)


def test_realized_implied_term_has_trading_days(chain_client):
    j = _ok(chain_client.get("/api/options/realized/SPY"))
    it = j["implied_term"]
    assert it is not None and len(it["trading_days"]) == len(it["dte"]) == len(it["atm_iv"])
    for td, dte in zip(it["trading_days"], it["dte"], strict=True):
        # weekdays are ~5/7 of calendar days (within a week's rounding)
        assert abs(td - dte * 5 / 7) <= 3
    assert any("trading_days" in n for n in j["notes"])
