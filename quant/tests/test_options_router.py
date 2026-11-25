"""/api/options endpoints: offline against real fixtures (realized, calculator), chain
endpoints against a dependency-overridden market serving the numerical test chain,
503s when chains are unavailable, and live checks on real Cboe chains."""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

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
    # the payload says which part of the day each estimator measures (the range estimators leave out the overnight move)
    assert j["covers"] == {"close_to_close": "close_to_close", "yang_zhang": "close_to_close", "parkinson": "open_to_close",
                           "garman_klass": "open_to_close", "rogers_satchell": "open_to_close"}
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
    assert j["expiry"] == "2026-11-20" and j["exercise_style"] == "European"   # the test chain says so
    # the level the IVs use is carried apart from the vendor spot; a European chain never implies one
    assert j["implied_spot"] is None and j["iv_spot"] == j["spot"] and j["iv_spot_source"] == "vendor"
    q = [x for x in j["quotes"] if x["use_smile"]]
    assert len(q) > 40 and all(abs(x["iv"] - x["vendor_iv"]) < 2e-4 for x in q)
    # every quote says why it has, or has not, an IV
    assert {x["iv_status"] for x in j["quotes"] if x["iv"] is None} <= {"zero_bid", "arb_bound", "no_vol", "wide"}
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
    assert d["checks"]["passed"] == {"negative_mass": True, "analytic": True, "mean": True}
    assert d["checks"]["analytic_integral"] == pytest.approx(d["checks"]["integral"], abs=1e-3)
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


def test_surface_carries_the_ssvi_surface_on_the_same_grid(chain_client):
    """Completion task 4.5: the SSVI surface beside the per-slice one -- same (T, k) grid, its parameters inside the
    no-arbitrage conditions, its in-sample RMSE and its violations counted as the league counts them."""
    j = _ok(chain_client.get("/api/options/surface/TESTX"))
    s = j["ssvi"]
    assert s["error"] is None
    iv = np.array(s["grid_iv"], dtype=float)
    assert iv.shape == np.array(j["grid"]["iv"], dtype=float).shape and np.isfinite(iv).all()
    p = s["fit"]["params"]
    assert p["eta"] * (1 + abs(p["rho"])) <= 2.0 and 0 < p["gamma"] <= 0.5 and len(p["T"]) == len(j["smiles"])
    assert s["violations"]["butterfly_violations"] == 0 == s["violations"]["calendar_violations"]
    assert s["fit"]["rmse_vol_pts"] >= 0 and "Gatheral" in s["fit"]["conditions"]["reference"]
    assert set(j["svi_violations"]) >= {"butterfly_violations", "calendar_violations", "calendar_pairs",
                                        "calendar_violations_quoted", "calendar_pairs_quoted"}
    assert s["violations"]["calendar_violations_quoted"] == 0
    # the calendar table is the quoted-overlap check: one definition with svi_violations' quoted count
    assert j["svi_violations"]["calendar_violations_quoted"] == sum(c["violation"] for c in j["calendar"])
    assert all("k_lo" in c and "overlap" in c for c in j["calendar"])


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



# ------------------------------------------------------------------ Task 3.4: the quote time and the zero curve
SPY_CHAIN_2026_11_15 = Path(__file__).parent / "data" / "cboe_spy_2026-11-15.json.gz"
_META = json.loads(SPY_CHAIN_2026_11_15.with_name(SPY_CHAIN_2026_11_15.name + ".meta.json").read_text())
#: The curve FRED served for that chain (dated 2026-11-12, in the fixture's sidecar).
FRED_2026_11_12 = (_META["treasury_curve"]["date"], _META["treasury_curve"]["tenor_years"],
                   _META["treasury_curve"]["yield_pct"])
#: CONSTRUCTED: a steep par curve (0.25% at one month to 8% at 30 years), not market data -- it separates the
#: bootstrapped zero from the par yield, which a flat curve would not.
STEEP_CONSTRUCTED = ("2026-11-12", [1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0],
                     [0.25, 0.5, 1.0, 2.0, 3.5, 4.5, 5.75, 6.5, 7.0, 7.75, 8.0])


class FixtureChainMarket(MarketData):
    """Offline market serving the committed Sunday SPY payload and one dated Treasury curve."""

    def __init__(self, curve=FRED_2026_11_12):
        super().__init__()
        self._curve = curve

    def option_chain(self, ticker: str) -> Dataset:
        from ohcamel_quant.data.cboe import parse_chain

        ch = parse_chain(json.loads(gzip.decompress(SPY_CHAIN_2026_11_15.read_bytes())), "SPY")
        return Dataset(ch, [Provenance.now("fixture", note=SPY_CHAIN_2026_11_15.name)])

    def treasury_curve(self, start=None, end=None) -> Dataset:
        d, tenors, ylds = self._curve
        df = pd.DataFrame([ylds], index=pd.DatetimeIndex([d]), columns=[float(t) for t in tenors])
        df = df[(df.index >= pd.Timestamp(start)) if start else slice(None)]
        df = df[(df.index <= pd.Timestamp(end)) if end else slice(None)]
        return Dataset(df, [Provenance.now("fixture", note="treasury curve")])


def test_a_chain_stamped_on_sunday_counts_its_tuesday_expiry_from_the_quotes():
    """The review's case: the payload was generated on Sunday, and T to Tuesday's expiry came out 1.7 days with an
    implied dividend yield of 48% against the vendor spot. The quotes are Friday's close: T must be at least the two
    sessions (Monday, Tuesday) left from them in business time, and the Tuesday slice's implied q is under 5%.

    Which assertion guards what: T is guarded by `T >= business` and, exactly, `T == 4/365` (Sunday's stamp gave
    0.004753). The |q| < 5% assertion is carried by the option-implied spot (Task 4.2) and would pass with the wrong
    T too (-0.76% then); with Black-76 parity against the vendor spot q is 23.4% even at the right T."""
    opt_router._CACHE.clear()
    b = opt_router._bundle("SPY", FixtureChainMarket())
    opt_router._CACHE.clear()
    s = b.ca.slices.loc["2026-11-17"]
    business = opt_router.trading_days_between(pd.Timestamp("2026-11-13 16:00"), "2026-11-17") / 252.0
    assert opt_router.trading_days_between(pd.Timestamp("2026-11-13 16:00"), "2026-11-17") == 2
    assert s["T"] >= business
    assert s["T"] == pytest.approx(4.0 / 365.0, abs=1e-9)        # Friday 16:00 to Tuesday 16:00, ACT/365
    assert abs(s["div_yield"]) < 0.05
    assert b.ca.as_of == pd.Timestamp("2026-11-13 16:00")


def test_the_router_discounts_with_the_bootstrapped_zero_rate_not_the_par_yield():
    """On a steep curve the zero rate at a two-year expiry sits above the par yield there; the slice's rate must be
    the zero of the curve bootstrapped from that date's par yields, continuously compounded, at the slice's T."""
    from ohcamel_quant.macro.curve import bootstrap_par_curve

    opt_router._CACHE.clear()
    b = opt_router._bundle("SPY", FixtureChainMarket(STEEP_CONSTRUCTED))
    opt_router._CACHE.clear()
    s = b.ca.slices
    assert (s["rate_source"] == "treasury").all()
    _, tenors, ylds = STEEP_CONSTRUCTED
    zc = bootstrap_par_curve(tenors, ylds)
    longest = s.loc[s["T"].idxmax()]
    T = float(longest["T"])
    assert T > 1.5
    assert longest["rate"] == pytest.approx(float(zc.zero(T)) / 100.0, abs=1e-10)
    par = 2.0 * math.log1p(float(np.interp(T, tenors, ylds)) / 200.0)   # the old par-yield discounting
    assert longest["rate"] - par > 5e-4                                  # more than 5 bp apart
    assert any("bootstrapped" in n and "2026-11-12" in n for n in b.ca.notes)


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
    assert all(-0.02 < r < 0.15 for r in rates), rates
    if ticker == "SPY":  # American-style: discounting from the Treasury curve, not parity
        assert all(e["rate_source"] == "treasury" for e in j["expiries"])
        assert any(p["source"] == "fred" for p in j["provenance"])
    assert j["vix_style_30d"] is None or 5 < j["vix_style_30d"]["index"] < 150
    assert any(p["source"] == "cboe" for p in j["provenance"])
    if ticker == "_SPX":
        assert any(e["settlement"] == "AM" for e in j["expiries"])
        assert j["exercise_style"] == "European"


@pytest.mark.live
def test_live_spy_density_and_realized(live_client):
    d = _ok(live_client.get("/api/options/density/SPY"))
    c = d["checks"]
    assert all(c["passed"].values()), c["passed"]     # negative mass, cross-check, mean
    assert abs(c["integral"] - c["analytic_integral"]) <= 1e-3
    assert abs(c["mean"] / d["forward"] - 1) < 0.005
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
