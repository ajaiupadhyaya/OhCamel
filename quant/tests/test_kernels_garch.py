"""garch_nll against arch's own log-likelihood, garch_fit against fit_garch_fast, and
rolling_forecasts' kernel path (compute plan A5)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from arch import arch_model

from ohcamel_quant import kernels
from ohcamel_quant.risk import backtest as bt
from ohcamel_quant.risk import garch as gm


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"].dropna().to_numpy()


def _arch(y, kind):
    vol = "EGARCH" if kind == "egarch" else "GARCH"
    o = 0 if kind == "garch" else 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = arch_model(y, mean="Constant", vol=vol, p=1, o=o, q=1, dist="t", rescale=False).fit(disp="off")
    pr = res.params
    if kind == "garch":
        params = [pr["mu"], pr["omega"], pr["alpha[1]"], pr["beta[1]"], pr["nu"]]
    else:
        params = [pr["mu"], pr["omega"], pr["alpha[1]"], pr["gamma[1]"], pr["beta[1]"], pr["nu"]]
    return np.array(params, dtype=float), float(res.loglikelihood)


@pytest.mark.parametrize("kind", ["garch", "gjr", "egarch"])
def test_nll_matches_arch_at_archs_fit(spy, kind):
    # compute plan A5: the NLL at arch's fitted parameters equals arch's -loglikelihood to 1e-8
    y = 100.0 * spy[-1500:]
    params, ll = _arch(y, kind)
    assert kernels.garch_nll(params, y, kind) == pytest.approx(-ll, rel=1e-8)


def test_nll_infeasible_is_inf(spy):
    y = 100.0 * spy[-500:]
    assert kernels.garch_nll([0.0, 0.02, 0.05, 0.9, 2.0], y, "garch") == float("inf")   # nu <= 2


def test_nll_rejects_bad_input(spy):
    y = 100.0 * spy[-500:]
    with pytest.raises(ValueError, match="kind"):
        kernels.garch_nll([0.0] * 5, y, "figarch")
    with pytest.raises(ValueError, match="parameters"):
        kernels.garch_nll([0.0] * 4, y, "garch")


@pytest.mark.parametrize("kind", ["garch", "gjr"])
def test_fit_matches_fit_garch_fast(spy, kind):
    # compute plan A5: within 1e-4 of fit_garch_fast on SPY (relative for nu, which is large),
    # at a likelihood at least as good, with the same next-day variance
    r = spy[-1000:]
    fast = gm.fit_garch_fast(r, kind)
    got = kernels.garch_fit(100.0 * r, kind, None)
    np.testing.assert_allclose(got.params, fast.x, rtol=1e-4, atol=1e-4)
    assert got.nll <= -fast.loglik + 1e-6 * abs(fast.loglik)
    assert got.next_variance == pytest.approx(fast.next_variance_pct, rel=1e-3)


def test_fit_warm_start_is_cheaper(spy):
    y = 100.0 * spy[-1000:]
    cold = kernels.garch_fit(y, "gjr", None)
    warm = kernels.garch_fit(y, "gjr", cold.params)
    assert warm.nll <= cold.nll + 1e-9 * abs(cold.nll)
    if kernels.engine_of("garch_fit") == "rust":
        assert warm.iterations < cold.iterations


def test_fit_needs_100_observations(spy):
    with pytest.raises(ValueError, match="100 observations"):
        kernels.garch_fit(100.0 * spy[-99:], "gjr", None)


def test_rolling_forecasts_default_is_unchanged(spy):
    fc = bt.rolling_forecasts(spy[-700:], 0.99, 500, 20, models=("gjr_garch",))
    assert "engine" not in fc.info["gjr_garch"]


def test_rolling_forecasts_on_the_kernel_has_no_refit_cap(spy):
    # 200 forecast days, refit every 2 -> 100 refits: no _MAX_REFITS here (that cap is the router's)
    r = spy[-700:]
    legacy = bt.rolling_forecasts(r, 0.99, 500, 2, models=("gjr_garch",))
    job = bt.rolling_forecasts(r, 0.99, 500, 2, models=("gjr_garch",), use_kernels=True)
    assert job.info["gjr_garch"]["refits"] == 100
    assert job.info["gjr_garch"]["engine"] == kernels.engine_of("garch_fit")
    # parameters within 1e-4 move the VaR by far less than a basis point
    np.testing.assert_allclose(job.var["gjr_garch"], legacy.var["gjr_garch"], rtol=1e-3)
