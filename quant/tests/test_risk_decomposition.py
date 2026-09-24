"""Euler allocation identities and benchmark-relative risk on real ETF returns."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ohcamel_quant.risk import decomposition as dec
from ohcamel_quant.risk.core import covariance

W = {"SPY": 0.4, "QQQ": 0.2, "TLT": 0.25, "GLD": 0.15, "XLE": -0.1}


@pytest.fixture(scope="module")
def rets(etf_returns):
    return etf_returns


@pytest.mark.parametrize("measure", ["var", "es"])
@pytest.mark.parametrize("method", ["sample", "ewma"])
def test_parametric_euler_components_sum_to_total(rets, measure, method):
    cols = list(W)
    mu, cov = covariance(rets[cols], method)
    w = np.array([W[c] for c in cols])
    e = dec.euler_parametric(w, mu, cov, 0.99, 10, measure)
    assert e["component"].sum() == pytest.approx(e["total"], rel=1e-12)
    assert e["total"] == pytest.approx(dec.parametric_risk(w, mu, cov, 0.99, 10, measure), rel=1e-12)
    assert e["pct"].sum() == pytest.approx(1.0, rel=1e-12)
    # marginal = numerical gradient
    h = 1e-7
    for i in range(len(w)):
        wp = w.copy()
        wp[i] += h
        num = (dec.parametric_risk(wp, mu, cov, 0.99, 10, measure)
               - dec.parametric_risk(w, mu, cov, 0.99, 10, measure)) / h
        assert e["marginal"][i] == pytest.approx(num, rel=1e-4, abs=1e-8)


def test_historical_es_euler_sums_exactly(rets):
    cols = list(W)
    R = rets[cols].to_numpy()
    w = np.array([W[c] for c in cols])
    e = dec.historical_es_euler(R, w, 0.975)
    assert e["component"].sum() == pytest.approx(e["total"], rel=1e-12)
    assert e["total"] == pytest.approx(dec.historical_var_es(R @ w, 0.975)[1], rel=1e-12)


def test_decompose_full(rets):
    d = dec.decompose(rets, W, "SPY", 0.99)
    t = d["table"]
    tot = d["totals"]
    assert t["component_var"].sum() == pytest.approx(tot["parametric_var"], rel=1e-12)
    assert t["component_es"].sum() == pytest.approx(tot["parametric_es"], rel=1e-12)
    assert t["hist_component_es"].sum() == pytest.approx(tot["historical_es"], rel=1e-12)
    # sub-additivity of normal VaR for a long/short book with positive total
    assert tot["sum_standalone_var"] >= tot["parametric_var"]
    assert tot["diversification_ratio"] > 1
    # incremental VaR for SPY equals VaR(w) - VaR(w without SPY)
    cols = list(W)
    mu, cov = covariance(rets[cols], "sample")
    w = np.array([W[c] for c in cols])
    w0 = w.copy()
    w0[0] = 0
    inc = dec.parametric_risk(w, mu, cov, 0.99) - dec.parametric_risk(w0, mu, cov, 0.99)
    assert t.loc["SPY", "incremental_var"] == pytest.approx(inc)
    # correlation matrix is a proper correlation matrix
    c = d["correlation"].to_numpy()
    np.testing.assert_allclose(np.diag(c), 1.0)
    assert np.all(np.linalg.eigvalsh(c) > -1e-12)


def test_benchmark_beta_and_tracking_error(rets):
    b = dec.benchmark_risk(rets, {"SPY": 1.0}, "SPY")
    assert b["portfolio_beta"] == pytest.approx(1.0)
    assert b["tracking_error_daily"] == pytest.approx(0.0, abs=1e-12)
    b2 = dec.benchmark_risk(rets, {"QQQ": 1.0}, "SPY")
    x = rets[["QQQ", "SPY"]].to_numpy()
    beta = np.cov(x[:, 0], x[:, 1])[0, 1] / np.var(x[:, 1], ddof=1)
    assert b2["portfolio_beta"] == pytest.approx(beta)
    te = np.std(x[:, 0] - x[:, 1], ddof=1)
    assert b2["tracking_error_daily"] == pytest.approx(te, rel=1e-10)
    assert b2["tracking_error_annualized"] == pytest.approx(te * math.sqrt(252), rel=1e-10)
    assert sum(b2["te_contribution"].values()) == pytest.approx(te, rel=1e-10)
