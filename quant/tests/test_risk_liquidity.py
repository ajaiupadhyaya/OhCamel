"""Liquidity: the engine kernel's worked example, its identities, and the route.

Every expected value in the first group is hand-derived in test/test_liquidity.ml;
holding them here keeps lib/liquidity.ml and risk/liquidity.py one definition.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.risk import liquidity as lq

ONE = lq.Position("AAPL", qty=10_000.0, price=100.0, adv20=1_000_000.0, daily_stddev=0.02, half_spread_bps=5.0)


def test_the_engine_kernels_worked_example():
    t = lq.compute([ONE], participation=0.10, impact_coefficient=1.0)
    x = t.lines[0]
    assert x.days_to_liquidate == pytest.approx(0.1)
    assert x.impact_fraction == pytest.approx(0.002)
    assert x.impact_cost == pytest.approx(2_000.0)
    assert x.spread_cost == pytest.approx(500.0)
    assert t.spread_cost == pytest.approx(500.0)
    assert t.impact_cost == pytest.approx(2_000.0)
    assert t.max_days_to_liquidate == pytest.approx(0.1)
    assert lq.lvar(50_000.0, t) == pytest.approx(50_500.0)


def test_a_short_costs_what_the_long_cost():
    short = lq.Position("AAPL", -10_000.0, 100.0, 1_000_000.0, 0.02, 5.0)
    a, b = lq.compute([short]).lines[0], lq.compute([ONE]).lines[0]
    assert (a.days_to_liquidate, a.impact_cost, a.spread_cost) == (b.days_to_liquidate, b.impact_cost, b.spread_cost)


def test_zero_spread_collapses_lvar_to_var_and_it_is_never_below():
    free = lq.Position("AAPL", 10_000.0, 100.0, 1_000_000.0, 0.02, 0.0)
    assert lq.lvar(50_000.0, lq.compute([free])) == 50_000.0
    assert lq.lvar(50_000.0, lq.compute([ONE])) > 50_000.0


def test_totals_are_the_sums_and_the_slowest_line():
    other = lq.Position("MSFT", 500.0, 300.0, 2_000.0, 0.03, 2.0)
    t = lq.compute([ONE, other], participation=0.05, impact_coefficient=1.2)
    assert t.spread_cost == pytest.approx(sum(x.spread_cost for x in t.lines))
    assert t.impact_cost == pytest.approx(sum(x.impact_cost for x in t.lines))
    assert t.max_days_to_liquidate == max(x.days_to_liquidate for x in t.lines)


@pytest.mark.parametrize("adv,sigma", [(None, 0.02), (1e6, None), (0.0, 0.02), (1e6, math.nan), (-1.0, 0.02)])
def test_an_unknown_is_not_a_zero_and_poisons_its_total(adv, sigma):
    bad = lq.Position("BAD", 100.0, 10.0, adv, sigma, 5.0)
    t = lq.compute([ONE, bad])
    x = t.lines[1]
    assert x.days_to_liquidate is None and x.impact_fraction is None and x.impact_cost is None
    assert x.spread_cost == pytest.approx(0.5)  # price, quantity and spread are all known
    assert t.impact_cost is None and t.max_days_to_liquidate is None
    assert t.spread_cost is not None


def test_a_bad_half_spread_unknowns_the_line_and_lvar():
    t = lq.compute([ONE, lq.Position("BAD", 100.0, 10.0, 1e6, 0.02, math.nan)])
    assert t.lines[1].spread_cost is None and t.spread_cost is None
    assert lq.lvar(1.0, t) is None


def test_zero_shares_is_a_known_zero_even_with_no_adv():
    x = lq.compute([lq.Position("NONE", 0.0, 10.0, None, None, 5.0)]).lines[0]
    assert (x.days_to_liquidate, x.impact_fraction, x.impact_cost, x.spread_cost) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("p,y", [(0.0, 1.0), (-0.1, 1.0), (1.1, 1.0), (0.1, -1.0), (math.nan, 1.0)])
def test_nonsense_parameters_are_refused(p, y):
    with pytest.raises(ValueError):
        lq.compute([ONE], participation=p, impact_coefficient=y)


def test_which_half_spread_applies_is_stated():
    assert lq.half_spread("SPY", 5.0, {}) == (1.0, "book")
    assert lq.half_spread("XLE", 5.0, {}) == (5.0, "default")
    assert lq.half_spread("SPY", 5.0, {"SPY": 3.0}) == (3.0, "request")


# ------------------------------------------------------------------------- the route
BODY = {
    "holdings": [{"ticker": "SPY", "weight": 0.6}, {"ticker": "XLE", "weight": 0.4}],
    "notional": 10_000_000.0,
    "alpha": 0.99,
}


@pytest.fixture
def client():
    return TestClient(create_app())


def test_route_reports_lvar_on_real_fixtures(client):
    r = client.post("/api/risk/liquidity", json=BODY)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["lvar_usd"] == pytest.approx(d["var_usd"] + d["spread_cost_usd"])
    assert d["lvar"] == pytest.approx(d["lvar_usd"] / 10_000_000.0)
    spy, xle = d["lines"]
    assert spy["ticker"] == "SPY" and spy["half_spread_source"] == "book" and spy["half_spread_bps"] == 1.0
    assert xle["half_spread_source"] == "default" and xle["half_spread_bps"] == 5.0
    assert spy["exposure_usd"] == pytest.approx(6_000_000.0)
    assert spy["spread_cost_usd"] == pytest.approx(600.0)
    assert xle["spread_cost_usd"] == pytest.approx(2_000.0)
    assert spy["adv20"] > 0 and 0 < spy["daily_stddev"] < 0.1
    assert spy["days_to_liquidate"] == pytest.approx(spy["shares"] / (0.10 * spy["adv20"]))
    assert d["max_days_to_liquidate"] == max(spy["days_to_liquidate"], xle["days_to_liquidate"])
    assert d["provenance"]
    assert any("half-spread" in n for n in d["notes"])


def test_route_var_is_the_historical_estimate_of_the_same_returns(client):
    d = client.post("/api/risk/liquidity", json=BODY).json()
    s = client.post("/api/risk/summary", json={**BODY, "alphas": [0.99]}).json()
    assert d["var"] == pytest.approx(s["table"]["historical"]["0.99"]["var"])


def test_route_scores_a_short_on_its_magnitude(client):
    long_ = client.post("/api/risk/liquidity", json=BODY).json()
    short = client.post("/api/risk/liquidity", json={
        **BODY, "holdings": [{"ticker": "SPY", "weight": -0.6}, {"ticker": "XLE", "weight": 0.4}]}).json()
    assert short["spread_cost_usd"] == pytest.approx(long_["spread_cost_usd"])
    assert short["impact_cost_usd"] == pytest.approx(long_["impact_cost_usd"])


def test_route_honours_the_request_and_says_so(client):
    d = client.post("/api/risk/liquidity", json={
        **BODY, "spread_bps": {"spy": 2.0}, "spread_bps_default": 10.0, "participation": 0.05}).json()
    spy, xle = d["lines"]
    assert spy["half_spread_bps"] == 2.0 and spy["half_spread_source"] == "request"
    assert xle["half_spread_bps"] == 10.0
    assert spy["days_to_liquidate"] == pytest.approx(spy["shares"] / (0.05 * spy["adv20"]))
    assert d["method"]["participation"] == 0.05


def test_route_prices_and_volumes_are_those_of_the_analysis_window(client):
    whole = client.post("/api/risk/liquidity", json=BODY).json()
    early = client.post("/api/risk/liquidity", json={**BODY, "end": "2021-12-31"}).json()
    assert early["portfolio"]["end"].startswith("2021-12-")
    assert early["lines"][0]["price"] != whole["lines"][0]["price"]
    assert early["lines"][0]["adv20"] != whole["lines"][0]["adv20"]


@pytest.mark.parametrize("patch", [{"participation": 0.0}, {"participation": 1.5}, {"spread_bps_default": -1.0},
                                   {"spread_bps": {"SPY": -1.0}}, {"impact_coefficient": -0.5}])
def test_route_refuses_nonsense(client, patch):
    assert client.post("/api/risk/liquidity", json={**BODY, **patch}).status_code == 422
