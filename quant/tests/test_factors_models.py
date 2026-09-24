"""factors.models / factors.hac / factors.library on real fixture returns."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from ohcamel_quant.factors import hac
from ohcamel_quant.factors import library as lib
from ohcamel_quant.factors import models as fm


@pytest.fixture(scope="module")
def xy(etf_returns):
    """y = 0.0002 + 0.5 SPY + 0.3 TLT + tiny real residual (demeaned GLD x 1e-3)."""
    x = etf_returns[["SPY", "TLT"]]
    g = etf_returns["GLD"]
    y = 0.0002 + 0.5 * x["SPY"] + 0.3 * x["TLT"] + 1e-3 * (g - g.mean())
    return y.rename("y"), x


def test_nw_lag_rule():
    assert hac.nw_lag_rule(100) == 4
    assert hac.nw_lag_rule(2513) == math.floor(4 * (25.13) ** (2 / 9)) == 8
    assert hac.nw_lag_rule(0) == 0


def test_regression_recovers_exact_coefficients(etf_returns):
    x = etf_returns[["SPY", "TLT"]]
    y = 0.0002 + 0.5 * x["SPY"] + 0.3 * x["TLT"]
    reg = fm.factor_regression(y, x)
    assert reg.params["alpha"] == pytest.approx(0.0002, abs=1e-12)
    assert reg.params["SPY"] == pytest.approx(0.5, abs=1e-10)
    assert reg.params["TLT"] == pytest.approx(0.3, abs=1e-10)
    assert reg.r2 == pytest.approx(1.0, abs=1e-12)


def test_regression_recovers_with_real_residual(xy):
    y, x = xy
    reg = fm.factor_regression(y, x)
    assert reg.params["SPY"] == pytest.approx(0.5, abs=5e-4)
    assert reg.params["TLT"] == pytest.approx(0.3, abs=5e-4)
    assert reg.alpha_annualized == pytest.approx(0.0002 * 252, abs=1e-4)
    assert reg.r2 > 0.999
    assert reg.nw_lags == hac.nw_lag_rule(len(y))
    t = reg.table()
    assert list(t.index) == ["alpha", "SPY", "TLT"]
    assert (t["ci_lower"] < t["estimate"]).all() and (t["estimate"] < t["ci_upper"]).all()


def test_newey_west_matches_statsmodels(etf_returns):
    y = etf_returns["XLE"]
    x = etf_returns[["SPY", "TLT", "GLD"]]
    for lags in (0, 3, 8, 20):
        reg = fm.factor_regression(y, x, lags=lags)
        X = sm.add_constant(x)
        ref = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
        np.testing.assert_allclose(reg.std_errors.to_numpy(), ref.bse.to_numpy(), rtol=1e-10)
        np.testing.assert_allclose(reg.t_stats.to_numpy(), ref.tvalues.to_numpy(), rtol=1e-10)
        # Our numpy sandwich reproduces it too.
        v = hac.ols_hac_cov(X.to_numpy(), ref.resid.to_numpy(), lags)
        np.testing.assert_allclose(np.sqrt(np.diag(v)), ref.bse.to_numpy(), rtol=1e-10)
    # lags = 0 is White (1980) HC0.
    ref0 = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HC0")
    np.testing.assert_allclose(fm.factor_regression(y, x, lags=0).std_errors.to_numpy(),
                               ref0.bse.to_numpy(), rtol=1e-10)


def test_risk_decomposition_sums(etf_returns):
    y = etf_returns["XLF"]
    x = etf_returns[["SPY", "IWM", "TLT"]]
    reg = fm.factor_regression(y, x)
    rd = fm.risk_decomposition(reg)
    t = rd["table"]
    assert t["variance_contribution"].sum() == pytest.approx(rd["systematic_variance"], rel=1e-12)
    # In-sample OLS: Var(y) = b' Sigma b + Var(e) exactly.
    assert rd["total_variance"] == pytest.approx(rd["sample_variance"], rel=1e-10)
    assert rd["systematic_share"] == pytest.approx(reg.r2, rel=1e-10)
    b = reg.betas.to_numpy()
    sig = x.cov().to_numpy()
    np.testing.assert_allclose(t["variance_contribution"], b * (sig @ b), rtol=1e-12)
    total_vol = rd["total_vol_ann"]
    idio_vol_contrib = rd["idiosyncratic_variance"] / math.sqrt(rd["total_variance"]) * math.sqrt(252)
    assert t["vol_contribution_ann"].sum() + idio_vol_contrib == pytest.approx(total_vol, rel=1e-10)


def test_attribution_sums_exactly(etf_returns):
    y = etf_returns["XLK"] - 0.0  # raw returns as "excess" (rf = 0)
    x = etf_returns[["SPY", "QQQ", "IEF"]]
    reg = fm.factor_regression(y, x)
    a = fm.return_attribution(reg)
    daily = a["daily"]
    np.testing.assert_allclose(daily.drop(columns="total").sum(axis=1), y.loc[daily.index], atol=1e-15)
    arith = a["cumulative_arithmetic"]
    np.testing.assert_allclose(arith.drop(columns="total").sum(axis=1), y.cumsum(), rtol=1e-10, atol=1e-13)
    linked = a["cumulative_linked"]
    compounded = np.cumprod(1 + y.to_numpy()) - 1
    np.testing.assert_allclose(linked.drop(columns="total").sum(axis=1), compounded, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(linked["total"], compounded, rtol=1e-12)


def test_rolling_regression_matches_last_window(etf_returns):
    y = etf_returns["XLE"]
    x = etf_returns[["SPY", "TLT"]]
    roll = fm.rolling_regression(y, x, window=252)
    assert len(roll) == len(y) - 251
    last = fm.factor_regression(y.iloc[-252:], x.iloc[-252:])
    row = roll.iloc[-1]
    assert row["SPY"] == pytest.approx(last.params["SPY"], rel=1e-8)
    assert row["TLT"] == pytest.approx(last.params["TLT"], rel=1e-8)
    assert row["alpha_ann"] == pytest.approx(last.alpha_annualized, rel=1e-8)
    assert row["r2"] == pytest.approx(last.r2, rel=1e-8)
    with pytest.raises(ValueError):
        fm.rolling_regression(y.iloc[:100], x.iloc[:100], window=252)


def test_long_short_factors(etf_returns):
    rf = pd.Series(1e-4, index=etf_returns.index)  # only to check arithmetic
    f = fm.long_short_factors(etf_returns, [
        {"name": "MKT", "long": "SPY"},
        {"name": "SIZE", "long": "IWM", "short": "SPY"},
        fm.LongShortFactor("TERM", "TLT", "IEF"),
    ], rf)
    np.testing.assert_allclose(f["MKT"], etf_returns["SPY"] - 1e-4)
    np.testing.assert_allclose(f["SIZE"], etf_returns["IWM"] - etf_returns["SPY"])
    np.testing.assert_allclose(f["TERM"], etf_returns["TLT"] - etf_returns["IEF"])
    raw = fm.long_short_factors(etf_returns, [{"name": "MKT", "long": "SPY"}])
    np.testing.assert_allclose(raw["MKT"], etf_returns["SPY"])
    with pytest.raises(ValueError):
        fm.long_short_factors(etf_returns, [{"name": "X", "long": "NOPE"}])
    with pytest.raises(ValueError):
        fm.long_short_factors(etf_returns, [{"name": "X", "long": "SPY"}, {"name": "X", "long": "QQQ"}])


def test_model_specs():
    assert fm.model_spec("FF5+Mom").factors == ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")
    assert fm.model_spec("carhart").key == "carhart4"
    assert fm.model_spec("capm").factors == ("Mkt-RF",)
    with pytest.raises(ValueError):
        fm.model_spec("ff7")


# ------------------------------------------------------------------ library
@pytest.fixture(scope="module")
def etf_factor_table(etf_returns):
    """A French-format table built from REAL ETF spreads (French data is online-only)."""
    return pd.DataFrame({
        "Mkt-RF": etf_returns["SPY"], "SMB": etf_returns["IWM"] - etf_returns["SPY"],
        "HML": etf_returns["XLF"] - etf_returns["XLK"], "RF": 0.0,
    })


def test_window_slice(etf_factor_table):
    f = etf_factor_table
    last = f.index[-1]
    one_y = lib.window_slice(f, "1Y")
    assert one_y.index[0] > last - pd.DateOffset(years=1)
    assert len(f.loc[: last - pd.DateOffset(years=1)]) + len(one_y) == len(f)
    ytd = lib.window_slice(f, "ytd")
    assert (ytd.index.year == last.year).all()
    with pytest.raises(ValueError):
        lib.window_slice(f, "7Y")


def test_factor_library(etf_factor_table):
    f = etf_factor_table
    out = lib.factor_library(f, "3Y", corr_window=252, max_points=400)
    assert out["factors"] == ["Mkt-RF", "SMB", "HML"]
    w = lib.window_slice(f.drop(columns="RF"), "3Y")
    np.testing.assert_allclose(out["cumulative"].iloc[-1], (1 + w).prod() - 1, rtol=1e-12)
    assert len(out["cumulative"]) <= 401 and out["cumulative"].index[-1] == f.index[-1]
    rc = out["rolling_corr"]
    tail = f.iloc[-252:]
    assert rc["Mkt-RF / SMB"].iloc[-1] == pytest.approx(tail["Mkt-RF"].corr(tail["SMB"]), rel=1e-9)
    assert rc.index[0] >= w.index[0]
    tr = out["trailing"].set_index(["factor", "horizon"])
    y1 = lib.window_slice(f, "1Y")["SMB"]
    assert tr.loc[("SMB", "1Y"), "cumulative"] == pytest.approx((1 + y1).prod() - 1, rel=1e-12)
    assert tr.loc[("SMB", "1Y"), "sharpe"] == pytest.approx(y1.mean() / y1.std() * math.sqrt(252), rel=1e-12)
    assert (out["drawdowns"] <= 0).all().all()
    assert out["full_sample"]["Mkt-RF"]["n_obs"] == len(f)


def test_library_does_not_annualise_sub_annual_horizons(etf_factor_table):
    # Compounding a 1-month return to a 12-month rate is misleading; the payload
    # promises annualised returns for horizons >= 1Y only.
    tr = lib.factor_library(etf_factor_table, "3Y")["trailing"].set_index(["factor", "horizon"])
    for h in ("1M", "3M", "YTD"):
        assert np.isnan(tr.loc[("SMB", h), "annualized"])
    y3 = lib.window_slice(etf_factor_table, "3Y")["SMB"]
    assert tr.loc[("SMB", "3Y"), "annualized"] == pytest.approx(
        (1 + y3).prod() ** (252 / len(y3)) - 1, rel=1e-12)


def test_regression_rejects_collinear_factors(etf_returns):
    x = pd.DataFrame({"A": etf_returns["SPY"], "B": etf_returns["SPY"]})
    with pytest.raises(ValueError, match="collinear"):
        fm.factor_regression(etf_returns["XLK"], x)
    const = pd.DataFrame({"A": etf_returns["SPY"], "C": 0.001}, index=etf_returns.index)
    with pytest.raises(ValueError, match="collinear"):
        fm.factor_regression(etf_returns["XLK"], const)
