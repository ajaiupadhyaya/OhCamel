"""Walk-forward engine (no look-ahead, cost accounting) and Sharpe-difference tests."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.portfolio import covariance as cv
from ohcamel_quant.portfolio import expected as er
from ohcamel_quant.portfolio import inference as inf
from ohcamel_quant.portfolio import walkforward as wf
from ohcamel_quant.portfolio.methods import run_method

RF_DAILY = 0.00008  # test input for the rf argument


@pytest.fixture(scope="module")
def rets(etf_returns) -> pd.DataFrame:
    return etf_returns.dropna().loc["2019-01-01":"2023-12-31"]


@pytest.fixture(scope="module")
def result(rets) -> wf.WalkForwardResult:
    return wf.walk_forward(rets, ["min_variance", "max_sharpe", "hrp", "min_cvar"], window=252, freq="Q",
                           cost_bps=25.0, rf_daily=RF_DAILY)


def test_rebalance_positions(rets):
    pos = wf.rebalance_positions(rets.index, 252, "M")
    assert all(p >= 252 for p in pos)
    months = rets.index[pos].to_period("M")
    assert len(set(months)) == len(months)
    for p in pos:  # first session of its month
        assert rets.index[p - 1].to_period("M") != rets.index[p].to_period("M")
    with pytest.raises(ValueError):
        wf.rebalance_positions(rets.index, 10_000, "M")


def test_no_look_ahead(rets, result):
    """Weights at rebalance t equal those computed from the window strictly before t,
    and are unchanged when every return from t onwards is altered."""
    tr = result.tracks["max_sharpe"]
    for t in tr.weights.index[:3]:
        p = rets.index.get_loc(t)
        est = rets.iloc[p - 252:p]
        assert est.index[-1] < t
        cov = cv.estimate_covariance(est, "lw_constant_corr").cov
        mu = er.historical_mean(est).mu
        w = run_method("max_sharpe", est, cov, mu, RF_DAILY * 252).weights.to_numpy()
        assert np.allclose(tr.weights.loc[t].to_numpy(), w, atol=1e-8)
    # perturb the future: returns on and after the 3rd rebalance date
    t3 = tr.weights.index[2]
    fut = rets.copy()
    fut.loc[t3:] = fut.loc[t3:].to_numpy()[::-1] * 3.0
    res2 = wf.walk_forward(fut, ["max_sharpe"], window=252, freq="Q", cost_bps=25.0, rf_daily=RF_DAILY)
    w_a = tr.weights.loc[:t3]
    w_b = res2.tracks["max_sharpe"].weights.loc[:t3]
    assert np.allclose(w_a.to_numpy(), w_b.to_numpy(), atol=1e-8)
    r_a = result.tracks["max_sharpe"].returns.loc[:t3].iloc[:-1]
    r_b = res2.tracks["max_sharpe"].returns.loc[:t3].iloc[:-1]
    assert np.allclose(r_a.to_numpy(), r_b.to_numpy())


def test_cost_and_drift_accounting(rets, result):
    tr = result.tracks["equal_weight"]  # the benchmark is always included
    first = tr.weights.index[0]
    assert tr.turnover.iloc[0] == pytest.approx(1.0)  # buy from cash
    r0 = rets.loc[first].to_numpy().mean()
    assert tr.returns.loc[first] == pytest.approx((1 - 0.0025) * (1 + r0) - 1)
    # between rebalances, buy-and-hold: day-2 return = drifted-weight average
    day2 = tr.returns.index[1]
    g = 1 + rets.loc[first].to_numpy()
    wd = g / g.sum()
    assert tr.returns.loc[day2] == pytest.approx(wd @ rets.loc[day2].to_numpy())
    # second rebalance: turnover = sum|1/N - drifted|
    second = tr.weights.index[1]
    seg = rets.loc[first:second].iloc[:-1]
    gg = np.prod(1 + seg.to_numpy(), axis=0)
    drift = gg / gg.sum()
    assert tr.turnover.iloc[1] == pytest.approx(np.abs(1 / 9 - drift).sum())
    assert tr.costs.iloc[1] == pytest.approx(0.0025 * tr.turnover.iloc[1])


def test_stats_and_tests(result):
    st = result.stats
    assert set(st) == {"min_variance", "max_sharpe", "hrp", "min_cvar", "equal_weight"}
    for m, s in st.items():
        r = result.tracks[m].returns
        assert s["annual_vol"] == pytest.approx(r.std(ddof=1) * math.sqrt(252))
        w = np.cumprod(1 + r.to_numpy())
        assert s["max_drawdown"] == pytest.approx((w / np.maximum.accumulate(w) - 1).min())
        assert s["max_drawdown"] <= 0
        assert s["failed_rebalances"] == 0
    assert st["min_variance"]["annual_vol"] < st["equal_weight"]["annual_vol"]
    for m, t in result.tests.items():
        assert t["vs"] == "equal_weight"
        assert 0 <= t["ledoit_wolf"]["p_value"] <= 1 and 0 <= t["memmel"]["p_value"] <= 1
        ex = result.stats[m]["sharpe"] - result.stats["equal_weight"]["sharpe"]
        assert t["ledoit_wolf"]["sharpe_diff_annual"] == pytest.approx(ex, rel=1e-3, abs=1e-6)


def test_validation(rets):
    with pytest.raises(ValueError):
        wf.walk_forward(rets, ["nope"], window=252)
    with pytest.raises(ValueError):
        wf.walk_forward(rets, ["mean_variance"], window=252)
    with pytest.raises(ValueError):
        wf.walk_forward(rets, ["hrp"], window=252, cost_bps=-1)


def test_turnover_limit_respected(rets):
    from ohcamel_quant.portfolio.optimize import Constraints

    res = wf.walk_forward(rets, ["min_variance"], window=252, freq="Q", rf_daily=RF_DAILY,
                          cons=Constraints(0.0, 1.0, max_turnover=0.1))
    to = res.tracks["min_variance"].turnover.iloc[1:]
    relaxed = res.stats["min_variance"]["turnover_limit_relaxed"]
    assert (to <= 0.1 + 1e-5).sum() >= len(to) - relaxed


def test_sharpe_tests_on_real_returns(etf_returns):
    r = etf_returns.dropna()
    a = r["SPY"].to_numpy() - RF_DAILY
    b = r["TLT"].to_numpy() - RF_DAILY
    lw = inf.ledoit_wolf_test(a, b)
    lw_rev = inf.ledoit_wolf_test(b, a)
    assert lw["z"] == pytest.approx(-lw_rev["z"])
    mm = inf.memmel_test(a, b)
    sa, sb = a.mean() / a.std(ddof=1), b.mean() / b.std(ddof=1)
    rho = np.corrcoef(a, b)[0, 1]
    theta = (2 * (1 - rho) + 0.5 * (sa ** 2 + sb ** 2 - 2 * sa * sb * rho ** 2)) / len(a)
    assert mm["z"] == pytest.approx((sa - sb) / math.sqrt(theta))
    # lags = 0: the HAC reduces to the iid delta-method variance
    t = len(a)
    y = np.column_stack([a - a.mean(), b - b.mean(), a ** 2 - np.mean(a ** 2), b ** 2 - np.mean(b ** 2)])
    psi = y.T @ y / t
    va, vb = np.var(a), np.var(b)
    grad = np.array([np.mean(a ** 2) / va ** 1.5, -np.mean(b ** 2) / vb ** 1.5,
                     -0.5 * a.mean() / va ** 1.5, 0.5 * b.mean() / vb ** 1.5])
    d = a.mean() / np.sqrt(va) - b.mean() / np.sqrt(vb)
    z0 = d / np.sqrt(grad @ psi @ grad / t)
    assert inf.ledoit_wolf_test(a, b, lags=0)["z"] == pytest.approx(z0)
    # the two tests broadly agree on real data
    assert abs(lw["z"] - mm["z"]) < 1.0
    with pytest.raises(ValueError):
        inf.memmel_test(a[:10], b[:10])


def test_heuristic_constraint_violations_disclosed(rets):
    """HRP ignores a 20% cap; the walk-forward used to report its track with no caveat."""
    from ohcamel_quant.portfolio.optimize import Constraints

    res = wf.walk_forward(rets, ["hrp", "min_variance"], window=504, freq="Q", rf_daily=RF_DAILY,
                          cons=Constraints(0.0, 0.2))
    assert res.tracks["hrp"].weights.to_numpy().max() > 0.2
    assert res.stats["hrp"]["constraint_violations"] > 0
    assert res.stats["min_variance"]["constraint_violations"] == 0
    assert res.tracks["min_variance"].weights.to_numpy().max() <= 0.2 + 1e-6
    assert any("hrp" in n and "constraint" in n for n in res.notes)
