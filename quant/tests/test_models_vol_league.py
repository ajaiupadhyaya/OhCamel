"""Lane M, M3: volatility forecasts and their scoring (compute plan M3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from ohcamel_quant import kernels
from ohcamel_quant.factors.hac import nw_lag_rule
from ohcamel_quant.models.vol_league import (
    dm_test,
    ewma_oos,
    garch_oos,
    har_22d,
    har_oos,
    mcs_members,
    qlike,
    variance_path,
)


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"].dropna().to_numpy()


@pytest.mark.parametrize("kind", ["garch", "gjr", "egarch"])
def test_variance_path_reproduces_the_kernels_next_variance(spy, kind):
    """Reference: the kernel's own sigma^2_{T+1} for the window it was fitted on (1e-10 relative)."""
    y = 100.0 * spy[-1000:]
    fit = kernels.garch_fit(y, kind, None)
    path = variance_path(fit.params, y, kind)
    assert path.size == y.size + 1
    assert path[-1] == pytest.approx(fit.next_variance, rel=1e-10)
    np.testing.assert_allclose(path[:-1], fit.sigma2, rtol=1e-10)


def test_garch_oos_forecasts_use_only_the_past(spy):
    f, nxt = garch_oos(spy[:1100], "gjr", est_window=1000, eval_days=42, refit_every=21)
    g, _ = garch_oos(np.r_[spy[:1100], 9.9], "gjr", est_window=1000, eval_days=43, refit_every=21)
    assert f.shape == (42,) and np.all(f > 0) and nxt > 0
    np.testing.assert_allclose(g[:21], f[:21], rtol=1e-12)  # appending a future day cannot move earlier forecasts


def test_ewma_by_hand():
    # seed = mean(r[:30]^2) = (1e-4 + 1e-4 + 4e-4)/3 = 2e-4 (risk.core.ewma_variance_path);
    # day 1: 0.94 x 2e-4 + 0.06 x 1e-4 = 1.94e-4; day 2: 0.94 x 1.94e-4 + 0.06 x 1e-4 = 1.8836e-4;
    # next day: 0.94 x 1.8836e-4 + 0.06 x 4e-4 = 2.010584e-4.
    r = np.array([0.01, 0.01, 0.02])
    f, nxt = ewma_oos(r, eval_days=2, lam=0.94)
    np.testing.assert_allclose(f, [1.94e-4, 1.8836e-4])
    assert nxt == pytest.approx(2.010584e-4)


def test_har_matches_statsmodels_ols_and_ignores_the_future(spy):
    """Reference: statsmodels OLS on the same (x_t, rv_{t+1}) pairs known before the refit day."""
    rv = spy[-700:] ** 2
    f, nxt = har_oos(rv, window=250, eval_days=42, refit_every=21)
    n, start = rv.size, rv.size - 42

    def x(t):
        return [1.0, rv[t], rv[t - 4:t + 1].mean(), rv[t - 21:t + 1].mean()]

    for d in (start, start + 20, start + 21, n - 1):
        s0 = start + 21 * ((d - start) // 21)              # the refit day that serves day d
        ts = np.arange(s0 - 251, s0 - 1)
        beta = sm.OLS(rv[ts + 1], np.array([x(t) for t in ts])).fit().params
        floor = rv[s0 - 250:s0][rv[s0 - 250:s0] > 0].min()
        assert f[d - start] == pytest.approx(max(float(np.dot(x(d - 1), beta)), floor), rel=1e-9)
    g, _ = har_oos(np.r_[rv, 1.0], window=250, eval_days=43, refit_every=21)
    np.testing.assert_allclose(g[:21], f[:21], rtol=1e-12)
    assert nxt > 0 and har_22d(rv, window=250) > 0


def test_qlike_is_minimised_at_the_truth_and_finite_on_a_zero_day():
    rv = np.array([1.0, 2.0, 0.5])
    # d/dF (ln F + RV/F) = 1/F - RV/F^2 = 0 at F = RV; at F = RV: ln RV + 1 (1.0 for RV = 1 by hand).
    assert qlike(np.array([1.0]), np.array([1.0]))[0] == pytest.approx(1.0)
    assert np.all(qlike(rv, rv) < qlike(rv, rv * 1.2)) and np.all(qlike(rv, rv) < qlike(rv, rv * 0.8))
    assert np.isfinite(qlike(np.array([0.0]), np.array([1e-4]))).all()


def test_dm_matches_statsmodels_hac(spy):
    """Reference: OLS of the loss differential on a constant with HAC (Newey-West) errors, same lags."""
    rng = np.random.default_rng(5)
    la, lb = spy[-300:] ** 2, spy[-300:] ** 2 + rng.normal(0, 1e-5, 300)
    out = dm_test(la, lb)
    d = la - lb
    t = sm.OLS(d, np.ones_like(d)).fit(cov_type="HAC", cov_kwds={"maxlags": nw_lag_rule(d.size)}).tvalues[0]
    assert out["stat"] == pytest.approx(t, rel=1e-9) and 0 <= out["pvalue"] <= 1


def test_mcs_drops_a_clearly_worse_model(spy):
    rv = spy[-500:] ** 2
    f, _ = ewma_oos(spy[-501:], eval_days=500, lam=0.94)
    base = qlike(rv, f)
    noise = np.random.default_rng(9).normal(0.0, 0.01 * base.std(), base.size)
    losses = pd.DataFrame({"ewma": base, "twin": base + noise, "bad": base + 1.0})  # twin: indistinguishable
    keep = mcs_members(losses, size=0.10, seed=7)
    assert "bad" not in keep and keep and keep <= {"ewma", "twin"}
