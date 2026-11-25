"""factors.models / factors.hac / factors.library on real fixture returns."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from ohcamel_quant.factors import hac
from ohcamel_quant.factors import library as lib
from ohcamel_quant.factors import models as fm
from ohcamel_quant.factors import performance as perf


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


# ------------------------------------------------- cross-sectional factor tests (task 4.8)
FRENCH_SLICE = Path(__file__).parent / "data" / "french_25_size_bm_ff3_monthly_1996-2025.csv"


def _fm_panel(seed: int = 11, n_t: int = 240, n_a: int = 150, premium: float = 0.005):
    """Returns r_it = beta_i f_t + e_it with E[f] = premium: the true price of one unit of beta."""
    rng = np.random.default_rng(seed)
    beta = rng.uniform(0.4, 1.6, n_a)
    f = premium + 0.04 * rng.standard_normal(n_t)
    r = np.outer(f, beta) + 0.03 * rng.standard_normal((n_t, n_a))
    idx = pd.date_range("2000-01-31", periods=n_t, freq="ME")
    cols = [f"A{i:03d}" for i in range(n_a)]
    ret = pd.DataFrame(r, index=idx, columns=cols)
    exp = pd.DataFrame(np.tile(beta, (n_t, 1)), index=idx, columns=cols)
    return ret, {"beta": exp}, pd.DataFrame({"f": f}, index=idx)


def test_fama_macbeth_recovers_a_half_percent_premium_and_shanken_widens_the_error():
    ret, exposures, factors = _fm_panel()
    for kw in ({}, {"factors": factors}):
        res = fm.fama_macbeth(ret, exposures, **kw)
        lam, se = res.premia["beta"], res.se_nw["beta"]
        assert abs(lam - 0.005) < 2 * se, (lam, se)
        assert res.shanken_c > 1.0
        assert res.t_shanken["beta"] < res.t_nw["beta"]          # the plain t is the Newey-West t
        assert res.se_shanken["beta"] == pytest.approx(se * math.sqrt(res.shanken_c), rel=1e-12)
        assert res.n_periods == 240 and res.nw_lags == hac.nw_lag_rule(240)


def test_fama_macbeth_is_the_time_series_mean_of_per_period_ols():
    ret, exposures, _ = _fm_panel(n_t=60, n_a=40)
    res = fm.fama_macbeth(ret, exposures, nw_lags=3)
    b = exposures["beta"].iloc[0].to_numpy()
    lam_t = [sm.OLS(ret.iloc[t].to_numpy(), sm.add_constant(b)).fit().params for t in range(60)]
    assert np.allclose(res.lambdas.to_numpy(), np.array(lam_t), atol=1e-12)
    assert res.premia["beta"] == pytest.approx(np.mean([p[1] for p in lam_t]), abs=1e-14)
    dev = res.lambdas["beta"].to_numpy() - res.premia["beta"]
    assert res.se_nw["beta"] == pytest.approx(math.sqrt(hac.newey_west_lrv(dev, 3)[0, 0] / 60), rel=1e-12)
    assert res.nw_lags == 3


def test_fama_macbeth_uses_the_exposures_it_is_given_without_peeking():
    # a date whose exposures are missing is skipped; shifting returns against exposures changes the answer
    ret, exposures, _ = _fm_panel(n_t=80, n_a=30)
    exposures["beta"].iloc[5] = np.nan
    assert fm.fama_macbeth(ret, exposures).n_periods == 79
    with pytest.raises(ValueError, match="too few"):
        fm.fama_macbeth(ret.iloc[:, :2], {"beta": exposures["beta"].iloc[:, :2]})


def _sim_grs_panel(rng, alpha, n=10, t=240, k=3):
    f = 0.005 + 0.04 * rng.standard_normal((t, k))
    b = rng.uniform(0.3, 1.2, (k, n))
    mix = rng.standard_normal((n, n)) * 0.01 + np.eye(n) * 0.03
    e = rng.standard_normal((t, n)) @ mix
    return alpha + f @ b + e, f


def test_grs_p_values_are_uniform_under_the_null_and_reject_a_half_percent_alpha():
    from scipy import stats
    rng = np.random.default_rng(7)
    p = np.array([fm.grs_test(*_sim_grs_panel(rng, 0.0)).p_value for _ in range(500)])
    assert stats.kstest(p, "uniform").pvalue > 0.01
    rej = fm.grs_test(*_sim_grs_panel(np.random.default_rng(7), 0.005))
    assert rej.p_value < 0.01


def test_grs_is_a_function_of_regression_outputs_and_the_mle_unbiased_forms_agree():
    rng = np.random.default_rng(3)
    y, f = _sim_grs_panel(rng, 0.001, n=6, t=200, k=2)
    r = fm.grs_test(y, f)
    t, n, nl = 200, 6, 2
    x = np.column_stack([np.ones(t), f])
    resid = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
    s_mle = resid.T @ resid / t
    om_mle = np.cov(f.T, ddof=0)
    a = r.alphas.to_numpy()
    s1 = fm.grs(a, s_mle, f.mean(0), om_mle, t)
    s2 = fm.grs(a, resid.T @ resid / (t - nl - 1), f.mean(0), np.cov(f.T, ddof=1), t, mle=False)
    assert s1[0] == pytest.approx(s2[0], rel=1e-12) and s1[1] == pytest.approx(s2[1], rel=1e-10)
    # the paper's form, residual covariance unbiased (T-L-1) and factor covariance MLE: (T/N)(T-N-L)/(T-L-1) a'S^-1 a / (1 + m'O^-1 m)
    su = resid.T @ resid / (t - nl - 1)
    ou = np.cov(f.T, ddof=0)            # GRS (1989): the factor covariance is the MLE one
    m = f.mean(0)
    book = (t / n) * (t - n - nl) / (t - nl - 1) * a @ np.linalg.solve(su, a) / (1 + m @ np.linalg.solve(ou, m))
    assert s1[0] == pytest.approx(book, rel=1e-10)
    assert (r.statistic, r.p_value) == pytest.approx(s1, rel=1e-12)


def test_grs_on_the_french_25_size_value_portfolios_matches_an_independent_computation():
    from scipy import stats
    d = pd.read_csv(FRENCH_SLICE, index_col="month") / 100.0
    ports, fac, rf = d.iloc[:, :25], d[["Mkt-RF", "SMB", "HML"]], d["RF"]
    assert ports.shape == (360, 25)
    y = ports.sub(rf, axis=0)
    r = fm.grs_test(y, fac)
    # independent path: statsmodels OLS per portfolio, then the textbook formula on its outputs
    t, n, nl = len(y), 25, 3
    fits = [sm.OLS(y.iloc[:, i], sm.add_constant(fac)).fit() for i in range(n)]
    a = np.array([f.params["const"] for f in fits])
    resid = np.column_stack([f.resid for f in fits])
    s_u = resid.T @ resid / (t - nl - 1)
    mu, om_u = fac.mean().to_numpy(), fac.cov(ddof=0).to_numpy()
    stat = (t / n) * (t - n - nl) / (t - nl - 1) * a @ np.linalg.solve(s_u, a) / (1 + mu @ np.linalg.solve(om_u, mu))
    assert r.statistic == pytest.approx(stat, abs=1e-8)
    assert r.p_value == pytest.approx(stats.f.sf(stat, n, t - n - nl), abs=1e-8)
    assert r.n_assets == 25 and r.n_factors == 3 and r.n_obs == 360


def test_neutralise_leaves_zero_group_means_and_zero_covariance_with_beta():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2024-01-01", periods=40, freq="B")
    names = [f"S{i:02d}" for i in range(30)]
    beta = pd.DataFrame(rng.uniform(0.3, 1.7, (40, 30)), index=idx, columns=names)
    signal = pd.DataFrame(rng.standard_normal((40, 30)) + 2.0 * beta.to_numpy(), index=idx, columns=names)
    groups = pd.Series(np.arange(30) % 4, index=names)
    out = perf.neutralise(signal, groups=groups, betas=beta)
    for _, cols in groups.groupby(groups).groups.items():
        assert np.abs(out[list(cols)].mean(axis=1)).max() < 1e-10
    cov = ((out - out.mean(axis=1).to_numpy()[:, None]) * (beta - beta.mean(axis=1).to_numpy()[:, None])).mean(axis=1)
    assert np.abs(cov).max() < 1e-10
    g_only = perf.neutralise(signal, groups=groups, betas=None)
    assert np.abs(g_only[names[0::4]].mean(axis=1)).max() < 1e-10
    b_only = perf.neutralise(signal, groups=None, betas=beta)
    assert np.abs(b_only.mean(axis=1)).max() < 1e-10
    assert perf.neutralise(signal, groups=None, betas=None).equals(signal)
    sparse = signal.copy()
    sparse.iloc[3, :5] = np.nan
    assert perf.neutralise(sparse, groups=groups, betas=beta).iloc[3, :5].isna().all()


def test_fama_macbeth_without_factors_warns_and_names_its_proxy_and_a_characteristic_run_skips_shanken():
    ret, exposures, factors = _fm_panel(n_t=80, n_a=30)
    with pytest.warns(UserWarning, match="proxy"):
        res = fm.fama_macbeth(ret, exposures)
    assert "proxy" in res.sigma_f_source
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ok = fm.fama_macbeth(ret, exposures, factors=factors)
        char = fm.fama_macbeth(ret, exposures, shanken=False)
    assert ok.sigma_f_source == "factor covariance"
    assert math.isnan(char.shanken_c) and char.t_shanken.isna().all() and "not applied" in char.sigma_f_source
    assert char.t_nw["beta"] == pytest.approx(ok.t_nw["beta"], rel=1e-12)


def test_neutralise_returns_nan_for_a_date_with_no_more_names_than_regressors():
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    names = list("ABCDEF")
    sig = pd.DataFrame(np.arange(18.0).reshape(3, 6) ** 1.3, index=idx, columns=names)
    beta = pd.DataFrame(np.linspace(0.5, 1.5, 18).reshape(3, 6), index=idx, columns=names)
    sig.iloc[1, 3:] = np.nan                       # date 1: 3 names, 2 groups + beta = 3 regressors
    out = perf.neutralise(sig, groups=pd.Series(["x", "x", "y", "y", "z", "z"], index=names), betas=beta)
    assert out.iloc[1].isna().all() and out.iloc[0].notna().all() and out.iloc[2].notna().all()
    assert out.attrs["rank_deficient_dates"] == 1 and "1 date" in out.attrs["note"]
