"""Term-structure analytics: bootstrap, Nelson-Siegel/Svensson, PCA, spreads."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.macro import curve as cv

# A CMT-format par curve (tenors in years, percent) used only to exercise the
# algebra; every assertion below is an identity that must hold for ANY curve.
TENORS = [1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
PAR = [4.31, 4.28, 4.14, 3.95, 3.62, 3.55, 3.61, 3.77, 4.02, 4.51, 4.60]


def test_bootstrap_reprices_par_bonds():
    zc = cv.bootstrap_par_curve(TENORS, PAR)
    grid = np.arange(0.5, 30.0 + 1e-9, 0.5)
    for T in grid:
        c = np.interp(T, zc.t, zc.par) / 100  # par coupon at node T
        n = int(round(2 * T))
        tk = np.arange(1, n + 1) / 2
        price = c / 2 * zc.discount(tk).sum() + zc.discount(T)
        assert abs(price - 1.0) < 1e-12
        assert abs(zc.par_yield(T) - 100 * c) < 1e-8
    # quoted tenors are reproduced exactly on the grid
    for t, y in zip(TENORS, PAR, strict=True):
        if t >= 0.5:
            assert zc.par_yield(t) == pytest.approx(y, abs=1e-8)


def test_bills_are_simple_bey_and_forward_consistency():
    zc = cv.bootstrap_par_curve(TENORS, PAR)
    assert zc.discount(0.25) == pytest.approx(1 / (1 + 0.0428 * 0.25), rel=1e-14)
    assert zc.discount(0.5) == pytest.approx(1 / (1 + 0.0414 / 2), rel=1e-14)
    # integral of the instantaneous forward equals -ln P(T)
    for T in (0.7, 4.0, 12.5, 29.0):
        s = np.linspace(0.0, T, 200_001)
        integral = np.trapezoid(zc.inst_forward(s) / 100, s)
        assert integral == pytest.approx(-np.log(zc.discount(T)), rel=1e-6)
    # 1y forward from zeros
    t = 3.0
    f = zc.forward(t, 1.0)
    assert f == pytest.approx((zc.zero(4.0) * 4 - zc.zero(3.0) * 3), rel=1e-12)
    # table columns
    tab = zc.table([0.5, 1, 2, 10])
    assert list(tab.columns) == ["t", "discount", "zero", "inst_forward", "forward_1y"]
    # flat par curve -> zero curve equals the continuous equivalent of the semiannual rate
    flat = cv.bootstrap_par_curve([0.5, 1, 2, 5, 10], [5, 5, 5, 5, 5])
    assert flat.zero(np.array([1.0, 5.0, 10.0])) == pytest.approx(200 * np.log(1.025), abs=1e-10)


def test_hull_textbook_bootstrap():
    """Hull, Options, Futures & Other Derivatives, Table 4.3/4.4: zero rates
    10.127, 10.469, 10.536, 10.681, 10.808 (% continuous)."""
    bonds = [cv.BondQuote(0.25, 97.5), cv.BondQuote(0.5, 94.9), cv.BondQuote(1.0, 90.0),
             cv.BondQuote(1.5, 96.0, 0.08), cv.BondQuote(2.0, 101.6, 0.12)]
    z = cv.bootstrap_bonds(bonds)["zero_cc_pct"].round(3).tolist()
    assert z == [10.127, 10.469, 10.536, 10.681, 10.808]


def test_nelson_siegel_recovers_known_params():
    t = np.array(TENORS, dtype=float)
    true = {"b0": 4.2, "b1": -1.3, "b2": 2.1, "tau": 1.7}
    fit = cv.fit_nelson_siegel(t, cv.nelson_siegel(t, **true))
    for k, v in true.items():
        assert fit.params[k] == pytest.approx(v, abs=1e-5)
    assert fit.rmse_bp < 1e-4
    assert fit(np.array([0.0]))[0] == pytest.approx(true["b0"] + true["b1"], abs=1e-5)  # short rate


def test_svensson_recovers_known_params_and_beats_ns():
    t = np.linspace(0.1, 30, 40)
    true = {"b0": 4.0, "b1": -1.5, "b2": 2.0, "b3": -1.0, "tau1": 1.2, "tau2": 8.0}
    y = cv.svensson(t, **true)
    fit = cv.fit_svensson(t, y)
    for k, v in true.items():
        assert fit.params[k] == pytest.approx(v, rel=1e-3, abs=1e-3)
    assert fit.rmse_bp < 1e-3
    assert cv.fit_nelson_siegel(t, y).rmse_bp > fit.rmse_bp


def test_parametric_fit_on_bootstrapped_curve():
    zc = cv.bootstrap_par_curve(TENORS, PAR)
    ns = cv.fit_nelson_siegel(zc.t, zc.zero(zc.t))
    nss = cv.fit_svensson(zc.t, zc.zero(zc.t))
    assert nss.rmse_bp <= ns.rmse_bp + 1e-9
    assert nss.params["tau2"] > nss.params["tau1"]
    assert 0.05 <= ns.params["tau"] <= 30
    assert len(ns.fitted) == len(zc.t)


def test_curve_on_and_spreads_and_monthly_sample():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-02-01", "2024-02-29"])
    df = pd.DataFrame({0.25: [5.4, 5.41, np.nan, 5.39], 2.0: [4.3, 4.35, 4.2, 4.6],
                       5.0: [3.9, 3.95, 3.8, 4.2], 10.0: [3.9, 3.98, 3.9, 4.25], 30.0: [4.1, 4.2, 4.1, 4.4]},
                      index=idx)
    d, par = cv.curve_on(df, "2024-02-15", min_tenors=4)
    assert d == pd.Timestamp("2024-02-01") and 0.25 not in par.index
    with pytest.raises(ValueError):
        cv.curve_on(df, "2023-01-01")
    sp = cv.curve_spreads(df)
    assert sp.loc["2024-01-02", "2s10s"] == pytest.approx(-40.0)
    assert sp.loc["2024-01-02", "3m10y"] == pytest.approx(-150.0)
    assert sp.loc["2024-01-02", "5s30s"] == pytest.approx(20.0)
    assert sp.loc["2024-01-02", "2s5s10s"] == pytest.approx(100 * (2 * 3.9 - 4.3 - 3.9))
    m = cv.monthly_sample(df)
    assert list(m.index) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-02-29")]


def test_pca_recovers_level_slope_structure():
    """Constructed factor model dY = L f + e: PC1 flat, PC2 monotone, variance shares ordered."""
    rng = np.random.default_rng(0)
    tn = [0.25, 1, 2, 5, 10, 30]
    level = np.ones(len(tn))
    slope = np.linspace(-1, 1, len(tn))
    f = rng.normal(size=(3000, 2)) * [8.0, 3.0]
    dy = f @ np.vstack([level, slope]) + rng.normal(scale=0.3, size=(3000, len(tn)))
    idx = pd.bdate_range("2010-01-01", periods=3001)
    lv = pd.DataFrame(np.vstack([np.zeros(len(tn)), np.cumsum(dy, axis=0)]) / 100 + 3.0, index=idx, columns=tn)
    res = cv.yield_pca(lv, tn)
    ld = res.loadings
    assert np.allclose(ld["level"], ld["level"].mean(), atol=0.02)
    assert ld["slope"].is_monotonic_increasing
    assert res.explained[0] > res.explained[1] > res.explained[2]
    assert res.explained.sum() == pytest.approx(1.0)
    assert res.n_obs == 3000
    # scores reproduce the centred data projection; cumulative is their running sum
    assert np.allclose(res.cumulative.iloc[-1], res.scores.sum())


def test_pca_on_real_fixture_two_tenors_raises(market):
    df = market.fred(["DGS2", "DGS10"]).data
    with pytest.raises(ValueError):
        cv.yield_pca(df.rename(columns={"DGS2": 2.0, "DGS10": 10.0}), [2.0, 10.0])


def test_pca_does_not_difference_across_a_missing_tenor_gap():
    """Regression: rows with one tenor missing used to be dropped BEFORE
    differencing, so a 200-day hole became one giant "daily" change."""
    idx = pd.bdate_range("2000-01-03", periods=400)
    rng = np.random.default_rng(0)
    lv = np.cumsum(rng.normal(0, 0.05, 400))
    cur = pd.DataFrame({t: 3 + lv + 0.1 * t for t in [1.0, 2.0, 5.0, 10.0, 30.0]}, index=idx)
    cur.iloc[100:300, 4] = np.nan      # 30y not quoted for 200 sessions
    cur.iloc[300:, :] += 2.0           # a 200bp move inside the hole
    p = cv.yield_pca(cur, [1.0, 2.0, 5.0, 10.0, 30.0])
    # only consecutive-day changes with all tenors quoted: 399 - 201 = 198
    assert p.n_obs == 198
    # every retained change is a genuine 1-day move (sd 5bp per tenor), never the 200bp jump
    assert p.scores["level"].abs().max() < 60


def test_svensson_sign_restrictions_and_identification():
    """Unconstrained NSS on a normal CMT curve put b0 (the long-run level)
    at about -9% offset by a huge b3; the Bundesbank/ECB sign restrictions
    b0 >= 0, b0 + b1 >= 0 and tau2 > 1.05 tau1 must hold for any fit."""
    par = [1, 1.1, 1.3, 1.6, 2.1, 2.4, 2.8, 3.0, 3.2, 3.6, 3.5]
    zc = cv.bootstrap_par_curve(TENORS, par)
    z = zc.zero(zc.t)
    raw = cv.fit_svensson(zc.t, z, constrained=False)
    assert raw.params["b0"] < 0  # the pathology the restriction removes
    for fit in (cv.fit_svensson(zc.t, z), cv.fit_nelson_siegel(zc.t, z)):
        p = fit.params
        assert p["b0"] >= -1e-10 and p["b0"] + p["b1"] >= -1e-10
        assert fit.rmse_bp < 10
    nss = cv.fit_svensson(zc.t, z)
    assert nss.params["tau2"] > 1.05 * nss.params["tau1"]
    # constrained fit reproduces the curve (within the 30y fitting range) almost as well
    assert nss.rmse_bp < raw.rmse_bp + 3
