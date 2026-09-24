"""Put-call-parity forward/discount recovery, expiry timing, SVI calibration and
static-arbitrage diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.options import bsm, parity
from ohcamel_quant.options.svi import (
    SVIParams,
    butterfly_check,
    butterfly_g,
    calendar_check,
    fit_svi,
    implied_vol_surface,
    total_variance_surface,
)


def _two_sided(spot, T, r, q, sig_fn, strikes, half_spread=0.02):
    rows = []
    F = spot * np.exp((r - q) * T)
    for K in strikes:
        iv = sig_fn(np.log(K / F))
        for cp in ("C", "P"):
            m = float(bsm.bsm_price(spot, K, T, iv, r, q, cp))
            rows.append({"strike": K, "type": cp, "bid": m - half_spread, "ask": m + half_spread})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ parity
@pytest.mark.parametrize("T,r,q", [(0.25, 0.045, 0.012), (1.0, 0.03, 0.02), (0.08, 0.05, -0.01)])
def test_parity_regression_recovers_forward_and_discount(T, r, q):
    spot = 4500.0
    strikes = np.arange(3500.0, 5505.0, 25.0)
    qt = _two_sided(spot, T, r, q, lambda k: 0.18 - 0.2 * k, strikes)
    fit = parity.fit_parity(qt, spot, T, n_strikes=15)
    assert fit.forward == pytest.approx(spot * np.exp((r - q) * T), rel=1e-9)
    assert fit.discount == pytest.approx(np.exp(-r * T), rel=1e-9)
    assert fit.rate == pytest.approx(r, abs=1e-7)
    assert fit.div_yield == pytest.approx(q, abs=1e-7)
    assert fit.n_strikes == 15 and fit.rate_source == "regression"


def test_parity_robust_to_one_bad_quote():
    spot, T, r, q = 100.0, 0.5, 0.04, 0.01
    qt = _two_sided(spot, T, r, q, lambda k: 0.25 + 0 * k, np.arange(80.0, 121.0, 2.5))
    bad = (qt["strike"] == 102.5) & (qt["type"] == "C")
    qt.loc[bad, ["bid", "ask"]] += 0.8           # a stale call quote
    fit = parity.fit_parity(qt, spot, T, n_strikes=12)
    assert fit.forward == pytest.approx(spot * np.exp((r - q) * T), rel=2e-4)


def test_parity_needs_two_sided_quotes():
    qt = pd.DataFrame({"strike": [100.0, 100.0], "type": ["C", "P"], "bid": [0.0, 1.0], "ask": [1.0, 1.2]})
    with pytest.raises(parity.InsufficientQuotes):
        parity.fit_parity(qt, 100.0, 0.1)


def test_fit_all_borrows_rate_for_unidentified_short_expiry():
    spot, r, q = 500.0, 0.045, 0.013
    long_q = _two_sided(spot, 0.5, r, q, lambda k: 0.2 + 0 * k, np.arange(400.0, 601.0, 5.0), 0.01)
    # 2-day expiry with only three wide two-sided strikes: slope not identified
    short_q = _two_sided(spot, 2 / 365, r, q, lambda k: 0.2 + 0 * k, np.array([495.0, 500.0, 505.0]), 0.5)
    fits, errors = parity.fit_all({"L": (long_q, 0.5), "S": (short_q, 2 / 365)}, spot, max_rate_se=0.005)
    assert not errors
    assert fits["L"].rate_source == "regression" and fits["S"].rate_source == "borrowed"
    assert fits["S"].rate == pytest.approx(fits["L"].rate)
    assert fits["S"].forward == pytest.approx(spot * np.exp((r - q) * 2 / 365), rel=1e-6)


def test_expiry_timing_am_pm_and_act365():
    assert parity.expiry_timestamp("2026-10-16", "SPX") == pd.Timestamp("2026-10-16 09:30")
    assert parity.expiry_timestamp("2026-10-16", "SPXW") == pd.Timestamp("2026-10-16 16:00")
    assert parity.expiry_timestamp("2026-10-16", "AAPL") == pd.Timestamp("2026-10-16 16:00")
    v = parity.expiry_timestamps(pd.Series(pd.to_datetime(["2026-10-16"] * 3)), pd.Series(["SPX", "SPXW", "AAPL"]))
    assert v.tolist() == [parity.expiry_timestamp("2026-10-16", r) for r in ("SPX", "SPXW", "AAPL")]
    T = parity.year_fraction(pd.Timestamp("2026-10-15 16:00"), [pd.Timestamp("2026-10-16 16:00")])
    assert T[0] == pytest.approx(1 / 365)


# ------------------------------------------------------------------ SVI
TRUE = SVIParams(a=0.012, b=0.11, rho=-0.55, m=0.04, sigma=0.12)


def test_svi_fit_recovers_known_parameters():
    T = 0.4
    k = np.linspace(-0.7, 0.45, 45)
    fit = fit_svi(k, TRUE.implied_vol(k, T), T)
    for name in ("a", "b", "rho", "m", "sigma"):
        assert getattr(fit.params, name) == pytest.approx(getattr(TRUE, name), abs=1e-6), name
    assert fit.rmse_vol_pts < 1e-5
    assert fit.butterfly["arbitrage_free"]


def test_svi_fit_with_noise_and_weights_is_close():
    T = 0.4
    k = np.linspace(-0.7, 0.45, 60)
    rng = np.random.default_rng(7)
    noisy = TRUE.implied_vol(k, T) + rng.normal(0, 0.002, k.size)
    fit = fit_svi(k, noisy, T, weights=np.linspace(0.5, 1.5, k.size))
    assert fit.rmse_vol_pts < 0.35
    np.testing.assert_allclose(fit.params.implied_vol(k, T), TRUE.implied_vol(k, T), atol=0.004)
    assert fit.params.b >= 0 and abs(fit.params.rho) < 1 and fit.params.sigma > 0
    assert fit.params.min_variance() >= -1e-12


def test_svi_needs_five_points():
    with pytest.raises(ValueError):
        fit_svi([0.0, 0.1, 0.2, 0.3], [0.2] * 4, 0.5)


def test_butterfly_flags_vogt_example():
    # Axel Vogt's arbitrageable raw SVI slice, Gatheral & Jacquier (2014), section 2.2 (T = 1)
    vogt = SVIParams(a=-0.0410, b=0.1331, rho=0.3060, m=0.3586, sigma=0.4153)
    chk = butterfly_check(vogt, np.linspace(-1.5, 1.5, 601))
    assert not chk["arbitrage_free"] and chk["g_min"] < 0
    assert 0.4 < chk["k_at_g_min"] < 1.3
    assert butterfly_check(TRUE, np.linspace(-1.5, 1.5, 601))["arbitrage_free"]


def test_g_equals_one_for_flat_smile():
    flat = SVIParams(a=0.04, b=0.0, rho=0.0, m=0.0, sigma=0.1)
    np.testing.assert_allclose(butterfly_g(flat, np.linspace(-1, 1, 11)), (1.0 + 0 * np.linspace(-1, 1, 11)))


def test_calendar_check_and_surface_interpolation():
    p1 = SVIParams(a=0.01, b=0.1, rho=-0.5, m=0.0, sigma=0.1)
    p2 = SVIParams(a=0.03, b=0.12, rho=-0.5, m=0.0, sigma=0.15)
    k = np.linspace(-0.5, 0.5, 51)
    ok = calendar_check([(0.25, p1), (0.5, p2)], k)
    assert not ok[0]["violation"] and ok[0]["min_dw"] > 0
    bad = calendar_check([(0.25, p2), (0.5, p1)], k)
    assert bad[0]["violation"]
    W = total_variance_surface([(0.25, p1), (0.5, p2)], k, [0.25, 0.375, 0.5, 0.75])
    np.testing.assert_allclose(W[0], p1.w(k))
    np.testing.assert_allclose(W[1], 0.5 * (p1.w(k) + p2.w(k)))
    assert np.isnan(W[3]).all()          # no extrapolation beyond the last slice
    iv = implied_vol_surface([(0.25, p1), (0.5, p2)], k, [0.5])
    np.testing.assert_allclose(iv[0], p2.implied_vol(k, 0.5))


def test_inner_box_qp_matches_generic_bounded_solver():
    """The face-enumeration QP (faces grouped per free set) equals a generic bounded solver."""
    from scipy.optimize import minimize

    from ohcamel_quant.options import svi as svi_mod

    rng = np.random.default_rng(7)
    for _ in range(30):
        A = rng.normal(size=(12, 3))
        G = A.T @ A + 1e-3 * np.eye(3)
        h = rng.normal(size=3) * 3
        lo, hi = np.array([0.0, 0.0, 0.0]), np.array([0.5, 0.4, 0.3])
        x = svi_mod._box_qp(G, h, lo, hi, svi_mod._FACES_A_BOXED)
        ref = minimize(lambda z, G=G, h=h: z @ G @ z - 2 * h @ z, np.full(3, 0.1),
                       jac=lambda z, G=G, h=h: 2 * G @ z - 2 * h,
                       bounds=list(zip(lo, hi, strict=True)), method="L-BFGS-B", options={"ftol": 1e-15, "gtol": 1e-12})
        assert x @ G @ x - 2 * h @ x <= ref.fun + 1e-9
