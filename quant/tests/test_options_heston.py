"""Completion task 4.6: Heston (1993) priced by the COS method (Fang & Oosterlee 2008) with the Albrecher et al.
(2007) "little trap" characteristic function, and calibrated by bounded least squares on prices.

Every market number here is either a published reference value or a CONSTRUCTED Heston surface (the recovery
test): no market data is synthesised for anything reported."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant import kernels
from ohcamel_quant.options import heston
from ohcamel_quant.options.bsm import black76_price, implied_vol_black

#: Fang & Oosterlee (2008), Table 4 / Albrecher et al. (2007): S = 100, K = 100, T = 1, r = q = 0.
REF = heston.HestonParams(kappa=1.5768, theta=0.0398, sigma=0.5751, rho=-0.5711, v0=0.0175)
REF_CALL = 5.785155450


def test_the_published_reference_call():
    c = heston.price(100.0, 100.0, 1.0, 1.0, REF, is_call=True)
    assert float(c) == pytest.approx(5.7851, abs=1e-3)
    # the c2-only range at L = 12 truncates this heavy left tail (sigma = 0.58, rho = -0.57): 4.0e-5 below the
    # reference's nine digits at n_terms = 160, measured; at L = 20 and 512 terms the series is within 3e-8 of them
    assert float(c) == pytest.approx(REF_CALL, abs=5e-5)
    wide = kernels.heston_price(100.0, 100.0, 1.0, 1.0, True, *REF.astuple(), n_terms=512, L=20.0)
    assert float(wide) == pytest.approx(REF_CALL, abs=1e-7)


def test_the_cumulants_are_the_derivatives_of_the_characteristic_function():
    """c1 = -i (ln phi)'(0), c2 = -(ln phi)''(0), by central differences of the little-trap CF."""
    from ohcamel_quant.kernels import reference as R

    for T, p in ((1.0, REF), (0.02, REF), (2.0, heston.HestonParams(3.0, 0.09, 1.2, 0.2, 0.2))):
        h = 1e-3
        lp = np.log(R.heston_cf(np.array([-h, 0.0, h]), T, *p.astuple()))
        c1, c2 = heston.cumulants(T, p)
        assert c1 == pytest.approx((lp[2] - lp[0]).imag / (2 * h), rel=1e-6)
        assert c2 == pytest.approx(-(lp[2] - 2 * lp[1] + lp[0]).real / h ** 2, rel=1e-5)


def test_put_call_parity_to_1e10():
    K = np.linspace(60.0, 160.0, 41)
    for T in (0.01, 0.1, 0.5, 1.0, 3.0):
        df = np.exp(-0.04 * T)
        F = 100.0 * np.exp(0.03 * T)
        c = heston.price(F, K, T, df, REF, is_call=True)
        p = heston.price(F, K, T, df, REF, is_call=False)
        np.testing.assert_allclose(c - p, df * (F - K), rtol=0, atol=1e-10)


def test_vanishing_vol_of_vol_with_v0_at_theta_is_black76_at_sqrt_theta():
    """sigma -> 0: the variance is deterministic, v_t = theta + (v0 - theta) e^{-kappa t}. With v0 = theta it is
    theta for all t, so the price is Black-76 at sqrt(theta). sigma = 1e-6: the price moves linearly in sigma (3.5e-4 at
    sigma = 1e-4, T = 2), and the CF is evaluated without the cancellation in beta - d, so 1e-6 is 3.5e-6 off."""
    p = heston.HestonParams(kappa=1.5, theta=0.04, sigma=1e-6, rho=-0.5, v0=0.04)
    K = np.linspace(70.0, 140.0, 29)
    for T in (0.05, 0.5, 2.0):
        for call in (True, False):
            got = heston.price(100.0, K, T, 0.98, p, is_call=call)
            want = black76_price(100.0, K, T, np.sqrt(0.04), 0.98, "C" if call else "P")
            np.testing.assert_allclose(got, want, rtol=0, atol=1e-4)


def test_vanishing_vol_of_vol_with_v0_off_theta_is_black76_at_the_integrated_variance():
    """The same limit with v0 != theta: Black-76 at the time-averaged variance
    theta + (v0 - theta) (1 - e^{-kappa T}) / (kappa T)."""
    p = heston.HestonParams(kappa=2.0, theta=0.04, sigma=1e-6, rho=0.3, v0=0.09)
    K = np.linspace(70.0, 140.0, 29)
    for T in (0.1, 1.0):
        avg = p.theta + (p.v0 - p.theta) * (1.0 - np.exp(-p.kappa * T)) / (p.kappa * T)
        got = heston.price(100.0, K, T, 1.0, p, is_call=True)
        np.testing.assert_allclose(got, black76_price(100.0, K, T, np.sqrt(avg), 1.0, "C"), rtol=0, atol=1e-4)


def test_short_dated_out_of_the_money_quotes_hold_against_a_long_expansion():
    """Where COS is weakest: a 2-day expiry, strikes out to 6 standard deviations in both wings (k = -0.06 is a
    put price of 1.5e-7). The shipped series (L = 12, 160 terms) against a wider range and a long one (L = 20, 4096
    terms): the implied vols agree to 1e-6 (1e-4 vol points). Measured: within 2e-8. Beyond the range (k = -0.12
    here, 12 sd) the strike gets its own extended range; its price (1.7e-14) is below PRICE_FLOOR, so no implied vol."""
    T = 2.0 / 365.0
    k = np.array([-0.06, -0.04, -0.025, -0.012, 0.012, 0.025, 0.035, 0.05])
    K = 100.0 * np.exp(k)
    call = K > 100.0
    lo = heston.price(100.0, K, T, 1.0, REF, is_call=call)
    hi = kernels.heston_price(100.0, K, T, 1.0, call, *REF.astuple(), n_terms=4096, L=20.0)
    iv_lo = implied_vol_black(lo, 100.0, K, T, 1.0, np.where(call, "C", "P"))
    iv_hi = implied_vol_black(hi, 100.0, K, T, 1.0, np.where(call, "C", "P"))
    assert np.isfinite(iv_hi).all() and np.isfinite(iv_lo).all()
    np.testing.assert_allclose(iv_lo, iv_hi, rtol=0, atol=1e-6)
    assert 0.0 <= float(heston.price(100.0, 100.0 * np.exp(-0.12), T, 1.0, REF, is_call=False)) < 1e-12 * 100.0
    assert np.isnan(heston.implied_vol(REF, np.array([-0.12]), T)).all()


def test_the_truncation_range_is_the_cumulant_range():
    a, b = heston.truncation_range(1.0, REF)
    c1, c2 = heston.cumulants(1.0, REF)
    assert a == pytest.approx(c1 - heston.L * np.sqrt(c2)) and b == pytest.approx(c1 + heston.L * np.sqrt(c2))
    assert c1 < 0 < c2 and heston.L == 12.0


def _surface(p, Ts=(0.08, 0.25, 0.5, 1.0, 2.0)):
    """A CONSTRUCTED Heston surface: OTM prices of the true model on a strike grid per maturity."""
    rows = []
    for T in Ts:
        F, df = 100.0 * np.exp(0.02 * T), np.exp(-0.04 * T)
        sd = 0.2 * np.sqrt(T)
        K = F * np.exp(np.linspace(-2.0 * sd, 1.5 * sd, 15))
        call = K >= F
        px = heston.price(F, K, T, df, p, is_call=call)
        rows.append(pd.DataFrame({"T": T, "F": F, "df": df, "K": K, "price": px, "is_call": call}))
    return pd.concat(rows, ignore_index=True)


@pytest.mark.parametrize("truth", [
    heston.HestonParams(kappa=2.0, theta=0.04, sigma=0.5, rho=-0.7, v0=0.03),
    heston.HestonParams(kappa=1.2, theta=0.06, sigma=0.9, rho=-0.4, v0=0.05),      # Feller violated
], ids=["feller-ok", "feller-violated"])
def test_calibration_recovers_synthetic_parameters_within_5pct(truth):
    q = _surface(truth)
    t0 = time.perf_counter()
    fit = heston.calibrate(q, weights="vega")
    took = time.perf_counter() - t0
    for f in ("kappa", "theta", "sigma", "rho", "v0"):
        assert getattr(fit.params, f) == pytest.approx(getattr(truth, f), rel=0.05), f
    assert fit.rmse_vol_pts < 1e-2 and fit.n == len(q) and fit.n_iv_failed == 0
    assert fit.feller == (2 * truth.kappa * truth.theta >= truth.sigma ** 2)     # reported, not enforced
    assert fit.feller_ratio == pytest.approx(2 * fit.params.kappa * fit.params.theta / fit.params.sigma ** 2)
    assert took < 30.0


def test_calibration_refuses_too_few_quotes_with_a_reason():
    q = _surface(REF).iloc[:4]
    with pytest.raises(ValueError, match="at least"):
        heston.calibrate(q)


def test_params_refuse_values_outside_the_bounds():
    with pytest.raises(ValueError):
        heston.HestonParams(kappa=-1.0, theta=0.04, sigma=0.5, rho=-0.5, v0=0.04)
    with pytest.raises(ValueError):
        heston.HestonParams(kappa=1.0, theta=0.04, sigma=0.5, rho=-1.0, v0=0.04)


def test_implied_vol_is_independent_of_the_forward_and_discount():
    """In forward moneyness the Heston smile depends on k and T only: what predict() relies on."""
    k = np.linspace(-0.3, 0.2, 11)
    a = heston.implied_vol(REF, k, 0.5)
    F, df = 640.0, 0.97
    K = F * np.exp(k)
    call = k >= 0
    px = heston.price(F, K, 0.5, df, REF, is_call=call)
    b = implied_vol_black(px, F, K, 0.5, df, np.where(call, "C", "P"))
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-9)


def test_density_integrates_to_one_and_prices_are_its_integral():
    """The COS density of x = ln(S_T / F): mass one on the truncation range, and its second-strike-derivative
    relation to the put price (P'' = df f(ln(K/F)) / K)."""
    T = 0.5
    a, b = heston.truncation_range(T, REF)
    x = np.linspace(a, b, 20001)
    f = heston.density(REF, x, T)
    assert np.trapezoid(f, x) == pytest.approx(1.0, abs=1e-8)
    K0, h = 95.0, 1e-2
    P = heston.price(100.0, np.array([K0 - h, K0, K0 + h]), T, 1.0, REF, is_call=False)
    d2 = (P[0] - 2 * P[1] + P[2]) / h ** 2
    assert d2 == pytest.approx(float(heston.density(REF, np.log(K0 / 100.0), T)) / K0, rel=1e-5)


#: A fit of the 2026-11-13 SPY surface (vol of vol ~ 3, Feller ratio 0.09): quoted strikes outside the L = 12 range.
WILD = heston.HestonParams(kappa=6.815, theta=0.05599, sigma=2.984, rho=-0.737, v0=0.03467)


def test_a_quoted_strike_beyond_the_cumulant_range_is_priced_not_zeroed():
    """Deep out-of-the-money puts the review found priced at exactly 0 (k = -1.0 and -1.2 at 32.7 and 60.7 days, past
    c1 - 12 sqrt(c2)): each gets its own range (at least L/2 sd beyond the strike, terms scaled with the range) and
    agrees with a 2^17-term series on [-12, 4] to 5e-4 in implied vol (measured 2.1e-4 at an IV of 0.82) at the league's LEAGUE_N, LEAGUE_L. Strikes inside the
    range are unchanged by the rule."""
    from ohcamel_quant.kernels import reference as R

    for T, k in ((32.7 / 365, -1.0), (60.7 / 365, -1.2)):
        a, b = heston.truncation_range(T, WILD)
        assert k < a
        K = np.array([np.exp(k)])
        got = heston.price(1.0, K, T, 1.0, WILD, is_call=False, n_terms=heston.LEAGUE_N, L=heston.LEAGUE_L)
        u, A = R.heston_series_on(T, *WILD.astuple(), -12.0, 4.0, 1 << 17)
        ref = R._cos_otm(np.ones(1), K, np.ones(1), -12.0, 4.0, u, A)
        assert got[0] > 0
        iv = implied_vol_black(np.r_[got, ref], 1.0, np.r_[K, K], T, 1.0, "P")
        assert abs(iv[0] - iv[1]) < 5e-4, iv
    k_in = np.linspace(-0.05, 0.05, 5)
    a0, b0 = heston.truncation_range(0.5, WILD)
    aj, bj, nj = R.heston_contract_range(k_in, a0, b0, heston.cumulants(0.5, WILD)[1], 160)
    assert (aj == a0).all() and (bj == b0).all() and (nj == 160).all()


def test_160_terms_do_not_converge_on_a_vol_of_vol_of_3_and_the_league_n_does():
    """Why the league prices at LEAGUE_N: at sigma ~ 3 the density is sharp; 160 terms miss by > 1e-3 in implied vol
    where 1024 agree with 4096 to 1e-6."""
    T = 0.5
    s = np.sqrt(heston.cumulants(T, WILD)[1])
    k = np.linspace(-4 * s, 3 * s, 15)
    iv = {n: heston.implied_vol(WILD, k, T, n, heston.LEAGUE_L) for n in (160, 1024, 4096)}
    assert np.nanmax(np.abs(iv[160] - iv[4096])) > 1e-3
    np.testing.assert_allclose(iv[1024], iv[4096], rtol=0, atol=1e-6)
