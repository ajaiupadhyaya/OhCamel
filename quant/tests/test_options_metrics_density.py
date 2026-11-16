"""Cboe model-free variance, constant-maturity interpolation, RR/BF, implied moves,
and the Breeden-Litzenberger density against closed-form lognormal results."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import norm

from ohcamel_quant.options import bsm, metrics
from ohcamel_quant.options.density import DensityError, breeden_litzenberger, density_checks
from ohcamel_quant.options.svi import K_MAX, SVIParams, butterfly_check, density_k

F, D, T, SIG = 100.0, np.exp(-0.04 * 30 / 365), 30 / 365, 0.20


def _flat(w_total):
    return lambda k: np.full_like(np.asarray(k, float), w_total)


def _flat_strip(sig=SIG, T_=T, F_=F, D_=D, step=0.5, lo=40.0, hi=220.0):
    K = np.arange(lo, hi + step / 2, step)
    c = bsm.black76_price(F_, K, T_, sig, D_, "C")
    p = bsm.black76_price(F_, K, T_, sig, D_, "P")
    return K, c * 0.99, c * 1.01, p * 0.99, p * 1.01


def test_vix_methodology_reproduces_flat_bsm_variance():
    K, cb, ca, pb, pa = _flat_strip()
    mf = metrics.model_free_variance(K, cb, ca, pb, pa, F, D, T)
    assert mf["sigma2"] == pytest.approx(SIG**2, rel=2e-3)
    assert mf["K0"] == 100.0 and mf["n_strikes"] > 100
    # forward between strikes: K0 is the strike immediately below F
    F2 = 100.3
    K, cb, ca, pb, pa = _flat_strip(F_=F2)
    mf2 = metrics.model_free_variance(K, cb, ca, pb, pa, F2, D, T)
    assert mf2["K0"] == 100.0 and mf2["sigma2"] == pytest.approx(SIG**2, rel=2e-3)


def test_vix_strip_stops_after_two_zero_bids():
    K, cb, ca, pb, pa = _flat_strip()
    pb = pb.copy()
    pb[(K == 80.0) | (K == 79.5)] = 0.0    # two consecutive zero bids truncate the put wing
    mf = metrics.model_free_variance(K, cb, ca, pb, pa, F, D, T)
    assert mf["k_min"] == 80.5
    pb[K == 79.5] = 0.01                     # a single zero bid is skipped, not a stop
    mf = metrics.model_free_variance(K, cb, ca, pb, pa, F, D, T)
    assert mf["k_min"] < 79.5 and 80.0 not in mf["strikes"]


def test_constant_maturity_interpolation():
    out = metrics.constant_maturity_variance([(23 / 365, 0.04), (37 / 365, 0.04)])
    assert out["index"] == pytest.approx(20.0) and not out["extrapolated"]
    # total variance linear in T: w(30) = w1 + (w2 - w1) * 7/14
    v1, v2 = 0.03, 0.05
    w30 = 23 / 365 * v1 + (37 / 365 * v2 - 23 / 365 * v1) * 0.5
    out = metrics.constant_maturity_variance([(23 / 365, v1), (37 / 365, v2)])
    assert out["sigma2"] == pytest.approx(w30 / (30 / 365))
    assert metrics.constant_maturity_variance([(35 / 365, 0.04), (60 / 365, 0.04)])["extrapolated"]
    with pytest.raises(ValueError):
        metrics.constant_maturity_variance([(35 / 365, 0.04)])


def test_rr_bf_flat_smile_zero_and_delta_strikes_exact():
    flat = SVIParams(a=SIG**2 * T, b=0.0, rho=0.0, m=0.0, sigma=0.1)
    rb = metrics.risk_reversal_butterfly(flat, T, F, 0.01)
    assert rb["risk_reversal"] == pytest.approx(0.0, abs=1e-12) and rb["butterfly"] == pytest.approx(0.0, abs=1e-12)
    # the solved strike really has spot delta 0.25 under BSM
    S = F * np.exp(-(0.04 - 0.01) * T)
    g = bsm.bsm_greeks(S, rb["strike_call"], T, SIG, 0.04, 0.01, "C")
    assert float(g["delta"]) == pytest.approx(0.25, abs=1e-9)


def test_rr_negative_for_equity_skew_and_skew_slope_closed_form():
    p = SVIParams(a=0.002, b=0.05, rho=-0.7, m=0.02, sigma=0.08)
    rb = metrics.risk_reversal_butterfly(p, 0.25, F, 0.0)
    assert rb["risk_reversal"] < 0 and rb["butterfly"] > 0
    assert rb["strike_put"] < F < rb["strike_call"]
    h = 1e-6
    fd = (p.implied_vol(h, 0.25) - p.implied_vol(-h, 0.25)) / (2 * h)
    assert metrics.skew_slope(p, 0.25) == pytest.approx(float(fd), rel=1e-6)


def test_implied_move_and_market_atm():
    K = np.array([95.0, 100.0, 105.0])
    c = bsm.black76_price(F, K, T, SIG, D, "C")
    p = bsm.black76_price(F, K, T, SIG, D, "P")
    m = metrics.implied_move(K, c, p, F, 99.0)
    assert m["strike"] == 100.0 and m["move"] == pytest.approx((c[1] + p[1]) / 99.0)
    # ATM straddle ~ 0.8 sigma sqrt(T) (normal approximation)
    assert m["straddle"] / F == pytest.approx(SIG * np.sqrt(T) * np.sqrt(2 / np.pi) * D, rel=0.01)
    assert metrics.atm_vol_market(np.array([-0.1, 0.1]), np.array([0.3, 0.2])) == pytest.approx(0.25)
    assert np.isnan(metrics.atm_vol_market(np.array([0.1, 0.2]), np.array([0.3, 0.2])))


@pytest.mark.parametrize("T_,sig", [(7 / 365, 0.15), (0.5, 0.25), (2.0, 0.6)])
def test_bl_density_lognormal_closed_form(T_, sig):
    D_ = np.exp(-0.03 * T_)
    d = breeden_litzenberger(F, D_, T_, _flat(sig * sig * T_))
    s = sig * np.sqrt(T_)
    # the grid stops at |k| = K_MAX; what lies beyond is reported, and is the lognormal's own tails
    assert -K_MAX <= d.k_range[0] and d.k_range[1] <= K_MAX
    k_lo, k_hi = np.log(d.strikes[[0, -1]] / F)

    def moment(n):  # E[(S_T/F)^n; K_lo < S_T < K_hi] for the lognormal with E[S_T] = F
        return np.exp(n * (n - 1) * s * s / 2) * (norm.cdf((-k_lo + (n - 0.5) * s * s) / s)
                                                  - norm.cdf((-k_hi + (n - 0.5) * s * s) / s))

    assert d.tail_mass_left == pytest.approx(norm.cdf((k_lo + 0.5 * s * s) / s), abs=1e-6)
    assert d.tail_mass_right == pytest.approx(norm.sf((k_hi + 0.5 * s * s) / s), abs=1e-6)
    assert d.integral == pytest.approx(moment(0), abs=1e-5)
    assert d.integral + d.tail_mass_left + d.tail_mass_right == pytest.approx(1.0, abs=1e-5)
    assert d.mean == pytest.approx(F * moment(1), rel=1e-5)
    assert d.negative_mass < 1e-8
    # lognormal pdf and cdf
    x = d.strikes
    pdf = norm.pdf((np.log(x / F) + 0.5 * s * s) / s) / (x * s)
    i = np.abs(np.log(x / F)) < 2 * s
    np.testing.assert_allclose(d.density[i], pdf[i], rtol=1e-4)
    for K in (0.8 * F, F, 1.2 * F):
        assert float(d.prob_below(K)) == pytest.approx(norm.cdf((np.log(K / F) + 0.5 * s * s) / s), abs=1e-5)
    qs = d.quantiles([0.05, 0.5, 0.95])
    exact = F * np.exp(-0.5 * s * s + s * norm.ppf([0.05, 0.5, 0.95]))
    np.testing.assert_allclose(qs, exact, rtol=1e-4)
    mo = d.moments()
    # moments() renormalizes the density on the grid: the truncated lognormal's
    assert mo["std"] == pytest.approx(np.sqrt(moment(2) / moment(0) - (moment(1) / moment(0)) ** 2), rel=1e-3)
    assert mo["skew"] > 0


def test_bl_density_skewed_smile_and_move_probability():
    p = SVIParams(a=0.004, b=0.08, rho=-0.6, m=0.03, sigma=0.1)
    d = breeden_litzenberger(F, D, 0.25, p.w)
    assert d.integral == pytest.approx(1.0, abs=1e-5) and d.mean == pytest.approx(F, rel=1e-5)
    assert d.moments()["skew"] < 0                        # negative skew smile -> left-skewed density
    pm = d.prob_move(F, np.array([0.05, 0.10, 0.20]))
    assert pm[0] > pm[1] > pm[2] > 0
    assert float(d.prob_move(F, 0.10)) == pytest.approx(
        float(d.prob_below(0.9 * F) + 1 - d.prob_below(1.1 * F)))


def test_bl_rejects_bad_inputs():
    with pytest.raises(ValueError):
        breeden_litzenberger(F, D, 0.0, _flat(0.01))
    with pytest.raises(ValueError):
        breeden_litzenberger(F, D, 0.5, _flat(0.0))


def test_bl_k_range_reuses_the_grid_of_a_skewed_density():
    """A flat-vol density evaluated with ``k_range`` of a skewed one lives on exactly the same
    strikes (the skewed smile widens its left tail; without ``k_range`` the grids differ)."""
    p = SVIParams(a=0.004, b=0.08, rho=-0.6, m=0.03, sigma=0.1)
    T_ = 0.25
    d = breeden_litzenberger(F, D, T_, p.w)
    atm_w = float(p.w(0.0))
    free = breeden_litzenberger(F, D, T_, _flat(atm_w))
    assert not np.allclose(free.strikes[[0, -1]], d.strikes[[0, -1]])    # grids differ by default
    ln = breeden_litzenberger(F, D, T_, _flat(atm_w), k_range=d.k_range)
    np.testing.assert_allclose(ln.strikes, d.strikes, rtol=0, atol=1e-12)
    s = np.sqrt(atm_w)
    x = ln.strikes
    pdf = norm.pdf((np.log(x / F) + 0.5 * s * s) / s) / (x * s)
    i = np.abs(np.log(x / F)) < 2 * s
    np.testing.assert_allclose(ln.density[i], pdf[i], rtol=1e-4)
    with pytest.raises(ValueError):
        breeden_litzenberger(F, D, T_, _flat(atm_w), k_range=(0.1, -0.1))


def _failed(F_, D_, T_, p):
    """The density checks (a)-(e) that p's density fails: (a) breeden_litzenberger raises
    DensityError; (b) negative mass; (c) negative tail mass; (d) the grid integral against the
    closed-form density of the smile; (e) the mean against the forward."""
    try:
        d = breeden_litzenberger(F_, D_, T_, p.w)
    except DensityError:
        return {"a"}
    chk = density_checks(d, density_k(p, np.log(d.strikes / F_)) / d.strikes)
    names = {"negative_mass": "b", "tails": "c", "analytic": "d", "mean": "e"}
    return {names[n] for n, ok in chk["passed"].items() if not ok}


def test_bl_left_slope_045_stops_at_k_max_and_reports_its_left_tail():
    """The review's runaway case: a short-dated smile whose left wing rises at dw/dk = 0.45 put
    so much mass below the strikes that widening by 1.5x a dozen times reached k ~ -200. Now the
    grid stops at K_MAX and the mass it leaves out is reported."""
    b, rho = 0.45 / 1.9, -0.9
    runaway = SVIParams(a=0.00005, b=b, rho=rho, m=0.02, sigma=0.03)     # the review's case, T = 8 days
    assert runaway.wing_slopes()[0] == pytest.approx(0.45)
    with pytest.raises(DensityError):         # it is also arbitrageable near the money: g < 0 there
        breeden_litzenberger(670.0, 1.0, 8 / 365, runaway.w)
    # the same wing on an arbitrage-free slice: 3 months, ATM vol 50%, g >= 0 on [-K_MAX, K_MAX]
    T_ = 0.25
    p = SVIParams(a=0.0, b=b, rho=rho, m=0.0, sigma=0.1)
    p = SVIParams(a=0.5**2 * T_ - float(p.w(0.0)), b=b, rho=rho, m=0.0, sigma=0.1)
    assert p.wing_slopes()[0] == pytest.approx(0.45) and butterfly_check(p)["arbitrage_free"]
    d = breeden_litzenberger(670.0, 1.0, T_, p.w)
    assert -K_MAX <= d.k_range[0] < d.k_range[1] <= K_MAX
    assert d.k_range[0] == -K_MAX                      # it did need the whole range
    assert d.tail_mass_left > 1e-3                     # and still left mass below it, reported
    assert _failed(670.0, 1.0, T_, p) == set()


def test_bl_raises_on_a_non_monotone_cdf_instead_of_clipping_it():
    # Axel Vogt's arbitrageable slice (Gatheral & Jacquier 2014, section 2.2): g < 0 on k in
    # about (0.4, 1.3), so the density is negative there and the CDF falls
    vogt = SVIParams(a=-0.0410, b=0.1331, rho=0.3060, m=0.3586, sigma=0.4153)
    with pytest.raises(DensityError, match="not monotone"):
        breeden_litzenberger(F, D, 1.0, vogt.w)


def test_integral_plus_tails_is_one_for_any_smile_so_the_checks_are_others():
    """integral + tail masses telescopes to one for an arbitrageable smile too (q is the derivative
    of the CDF whose ends are the tails): computed with the CDF left unchecked, Vogt's slice and the
    review's bad slice both give 1.0000004. The checks that can fail do fail on both."""
    b, rho, sigma = 3.9 / 1.9, 0.9, 0.2
    bad = SVIParams(a=-b * sigma * np.sqrt(1 - rho * rho) + 0.0005, b=b, rho=rho, m=0.58, sigma=sigma)
    vogt = SVIParams(a=-0.0410, b=0.1331, rho=0.3060, m=0.3586, sigma=0.4153)
    for p, T_ in ((bad, 0.28), (vogt, 1.0)):
        k = np.linspace(-3.0, 3.0, 4001)
        K = 100.0 * np.exp(k)
        C = bsm.black76_price(100.0, K, T_, np.sqrt(np.maximum(p.w(k), 1e-14) / T_), 1.0, "C")
        h = k[1] - k[0]
        Ck, Ckk, Ki = (C[2:] - C[:-2]) / (2 * h), (C[2:] - 2 * C[1:-1] + C[:-2]) / h**2, K[1:-1]
        q, cdf = (Ckk - Ck) / Ki**2, 1.0 + Ck / Ki
        assert np.trapezoid(q, Ki) + cdf[0] + (1.0 - cdf[-1]) == pytest.approx(1.0, abs=1e-5)
        assert np.trapezoid(np.clip(-q, 0, None), Ki) > 1e-5          # yet the density goes negative
        assert _failed(100.0, 1.0, T_, p) & {"a", "b", "c", "d"}


def test_spy_2026_11_15_every_density_passes_the_checks_that_can_fail(spy_2026_11_15):
    """On the real chain of the review, every slice's density passes (a)-(e): no DensityError, no
    negative mass, no negative tail mass, the grid integral within 1e-3 of the smile's closed-form
    density on the same grid, and the mean within 0.5% of the forward. Before the wings were capped at
    Lee's bound, mean/forward ran to 1.40."""
    ca, vs, _ = spy_2026_11_15
    assert len(vs.fits) == 31
    bad = {}
    for sid, f in vs.fits.items():
        s = ca.slices.loc[sid]
        failed = _failed(float(s["forward"]), float(s["discount"]), float(s["T"]), f.params)
        if failed:
            bad[sid] = sorted(failed)
    assert not bad, bad
