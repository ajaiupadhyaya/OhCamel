"""Almgren-Chriss (2000): the TWAP limit, front-loading, a closed-form hand value, the frontier.

Hand values (X = 100,000 sh, T = 1, n = 10, tau = 0.1, eta = 2.5e-6, gamma = 2.5e-7, sigma = 0.3):
  eta~ = eta - gamma tau / 2 = 2.4875e-6
  lambda = 0:  E = gamma X^2 / 2 + eta~ X^2 / T = 1,250 + 24,875 = 26,125
               V = sigma^2 tau X^2 (n-1)(2n-1)/(6n) = 0.09 x 0.1 x 1e10 x 9 x 19 / 60 = 2.565e8
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.risk import execution as ex

KW = {"sigma": 0.3, "eta": 2.5e-6, "gamma": 2.5e-7}
X, T, N = 100_000.0, 1.0, 10


def test_zero_risk_aversion_is_equal_slices():
    s = ex.almgren_chriss(X, T, N, lam=0.0, **KW)
    assert np.allclose(s.trades, X / N)
    assert s.trades.sum() == pytest.approx(X)


def test_linear_impact_expected_cost_and_variance_match_the_hand_values():
    s = ex.almgren_chriss(X, T, N, lam=0.0, **KW)
    assert s.expected_cost == pytest.approx(26_125.0, rel=1e-12)
    assert s.variance == pytest.approx(2.565e8, rel=1e-12)


def test_large_risk_aversion_front_loads_monotonically():
    s = ex.almgren_chriss(X, T, N, lam=1e-3, **KW)
    assert s.trades.sum() == pytest.approx(X)
    assert np.all(np.diff(s.trades) < 0)
    assert s.trades[0] > X / N


def test_more_risk_aversion_costs_more_and_risks_less():
    lo, hi = (ex.almgren_chriss(X, T, N, lam=lam, **KW) for lam in (1e-6, 1e-4))
    assert hi.expected_cost > lo.expected_cost
    assert hi.variance < lo.variance


def test_a_sell_schedule_is_the_same_by_magnitude():
    assert ex.almgren_chriss(-X, T, N, lam=1e-5, **KW).trades.sum() == pytest.approx(X)


def test_the_frontier_is_a_dataframe_ordered_by_lambda():
    f = ex.frontier(X, T, N, lams=[0.0, 1e-6, 1e-5, 1e-4], **KW)
    assert isinstance(f, pd.DataFrame)
    assert list(f.columns) == ["lam", "expected_cost", "variance", "std"]
    assert f["expected_cost"].is_monotonic_increasing
    assert f["variance"].is_monotonic_decreasing
    assert f.loc[0, "expected_cost"] == pytest.approx(26_125.0)


def test_inputs_that_break_the_model_are_refused():
    with pytest.raises(ValueError):
        ex.almgren_chriss(X, T, N, lam=0.0, sigma=0.3, eta=1e-9, gamma=1e-3)   # eta~ <= 0
    with pytest.raises(ValueError):
        ex.almgren_chriss(X, T, 0, lam=0.0, **KW)
    with pytest.raises(ValueError):
        ex.almgren_chriss(X, T, N, lam=-1.0, **KW)


# ------------------------------------------------------------------------------- the route
def test_route_states_its_assumptions_and_its_sigma_and_sits_beside_the_sqrt_number():
    from fastapi.testclient import TestClient

    from ohcamel_quant.api.app import create_app

    body = {"holdings": [{"ticker": "SPY", "weight": 0.6}, {"ticker": "XLE", "weight": 0.4}],
            "notional": 10_000_000.0}
    r = TestClient(create_app()).post("/api/risk/execution", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ticker"] == "SPY"
    assert d["assumptions"]["kind"] == "assumed, not estimated" and d["assumptions"]["epsilon"] == 0.0
    assert d["sigma"]["source"] and d["sigma"]["as_of"] and d["sigma"]["value"] > 0
    f = pd.DataFrame(d["frontier"])
    assert f["expected_cost"].is_monotonic_increasing and f["variance"].is_monotonic_decreasing
    assert d["sqrt_impact_usd"] > 0
    assert sum(d["schedule"]["trades"]) == pytest.approx(d["shares"])
    assert any("assumptions" in n for n in d["notes"])
