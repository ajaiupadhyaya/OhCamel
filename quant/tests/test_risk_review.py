"""Regression tests for defects found in the adversarial review of the risk domain."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from ohcamel_quant.risk import backtest as bt
from ohcamel_quant.risk import decomposition as dec

PF = {"holdings": [{"ticker": "SPY", "weight": 0.6}, {"ticker": "TLT", "weight": 0.4}]}


# ------------------------------------------------------------ DQ test dof
def test_dq_dof_is_rank_of_regressors_when_no_exceptions():
    """With zero exceptions every lagged Hit column equals the constant -p, so the
    regressor matrix has rank 2 (constant, VaR); the chi2 reference must use the
    rank, not the nominal K + 2 columns (Engle & Manganelli 2004 assume full rank)."""
    rng = np.random.default_rng(0)
    T, p = 300, 0.01
    var = 0.02 + 0.005 * rng.standard_normal(T)
    out = bt.dq_test(np.zeros(T, dtype=int), var, p)
    assert out["df"] == 2
    assert out["p_value"] == pytest.approx(stats.chi2.sf(out["dq"], 2))


def test_dq_dof_full_rank_unchanged():
    rng = np.random.default_rng(1)
    T, p = 500, 0.05
    hits = (rng.random(T) < p).astype(int)
    out = bt.dq_test(hits, 0.02 + 0.005 * rng.standard_normal(T), p)
    assert out["df"] == 6


# ------------------------------------------- decomposition horizon consistency
def test_historical_decomposition_respects_horizon(etf_returns):
    """Historical VaR/ES (and their Euler components) must be h-day when horizon = h,
    like the parametric figures they are tabulated next to."""
    W = {"SPY": 0.5, "TLT": 0.3, "GLD": 0.2}
    R = etf_returns[list(W)].dropna()
    d1 = dec.decompose(R, W, None, 0.99, 1)
    d10 = dec.decompose(R, W, None, 0.99, 10)
    t1, t10 = d1["totals"], d10["totals"]
    assert t10["historical_es"] > 2.0 * t1["historical_es"]
    # expected: overlapping 10-day summed position P&L
    Rh = R.rolling(10).sum().dropna().to_numpy()
    rp = Rh @ np.array(list(W.values()))
    k = int(np.ceil(rp.size * 0.01 - 1e-9))
    assert t10["historical_es"] == pytest.approx(-np.sort(rp)[:k].mean(), rel=1e-12)
    assert d10["table"]["hist_component_es"].sum() == pytest.approx(t10["historical_es"], rel=1e-12)
    # tail days are row positions into R (window END dates)
    tail = d10["historical_tail_days"]
    assert tail.max() < len(R) and tail.min() >= 9


# ---------------------------------------------------------------- router
def test_router_rejects_non_finite_weights(client):
    raw = '{"holdings":[{"ticker":"SPY","weight":NaN}]}'
    r = client.post("/api/risk/summary", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert "finite" in r.text.lower() and "weight" in r.text.lower()
    raw = '{"holdings":[{"ticker":"SPY","weight":Infinity}]}'
    r = client.post("/api/risk/stress/historical", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422


@pytest.mark.parametrize("ep", ["summary", "garch", "backtest"])
def test_router_rejects_zero_variance_portfolio(client, ep):
    r = client.post(f"/api/risk/{ep}", json={"holdings": [{"ticker": "SPY", "weight": 0.0}]})
    assert r.status_code == 422, r.text[:200]
    assert "zero variance" in r.text


def test_conditional_stress_rejects_duplicate_shock_keys(client):
    r = client.post("/api/risk/stress/conditional",
                    json={**PF, "shocks": {"spy": -0.1, " SPY": -0.2}})
    assert r.status_code == 422
    assert "duplicate" in r.text.lower()


def test_backtest_caps_refits_for_the_droplet(client):
    """refit_every=1 on 10 years would run ~2,000 GARCH/GJR/t/EVT refits (tens of
    seconds on 2 vCPU); the router caps the refit count and says so."""
    r = client.post("/api/risk/backtest", json={**PF, "refit_every": 1})
    assert r.status_code == 200
    j = r.json()
    assert j["refit_every"] == 1
    assert j["refit_every_effective"] > 1
    assert any("refit" in n and "capped" in n for n in j["notes"])
    assert j["compute_seconds"] < 8


def test_summary_table_keys_distinguish_high_confidence_levels(client):
    j = client.post("/api/risk/summary", json={**PF, "alphas": [0.99999, 0.99]}).json()
    assert set(j["table"]["gaussian"]) == {"0.99999", "0.99"}


def test_router_rejects_end_before_start(client):
    r = client.post("/api/risk/summary", json={**PF, "start": "2024-01-01", "end": "2023-01-01"})
    assert r.status_code == 422 and "must be after start" in r.text


def test_backtest_evt_with_too_few_exceedances_says_why(client):
    r = client.post("/api/risk/backtest", json={**PF, "window": 100, "evt_threshold": 0.99,
                                                "models": ["evt_pot"]})
    assert r.status_code == 422
    assert "EVT" in r.text and "exceedances" in r.text


# ------------------------------------------------------------- FHS horizon
@pytest.fixture(scope="module")
def spy_gjr(market):
    from ohcamel_quant.risk import garch as gm

    r = market.returns(["SPY"]).data["SPY"].dropna()
    return gm.fit_garch(r, "gjr")


def test_fhs_long_horizon_keeps_wiped_out_paths(spy_gjr):
    """Bootstrapped GJR paths over 250 days occasionally draw a daily return <= -100%;
    log1p then gives NaN and those WORST paths used to be silently dropped, biasing
    ES down. They must count as a total loss (-100%)."""
    import warnings

    from ohcamel_quant.risk import garch as gm

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        est = gm.fhs_var_es(spy_gjr, 0.99, 250, simulations=10_000)
    assert est.params["scenarios"] == 10_000
    assert est.params["wiped_out_paths"] > 0
    assert any("-100%" in n for n in est.notes)
    assert est.es <= 1.0 + 1e-12


def test_fhs_bootstrap_is_simulated_once_per_fit_and_budgeted(spy_gjr):
    import time

    from ohcamel_quant.risk import garch as gm

    a = gm.fhs_var_es(spy_gjr, 0.99, 20, simulations=20_000)
    t = time.perf_counter()
    b = gm.fhs_var_es(spy_gjr, 0.95, 20, simulations=20_000)
    assert time.perf_counter() - t < 0.05          # reuses the simulated paths
    assert b.var < a.var
    big = gm.fhs_var_es(spy_gjr, 0.99, 250, simulations=50_000)
    assert big.params["scenarios"] * 250 <= gm.FHS_MAX_CELLS
    assert any("capped" in n for n in big.notes)
