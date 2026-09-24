"""BSM / Black-76 identities, greeks vs finite differences, implied-vol round trips."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant.options import bsm

S, K, T, SIG, R, Q = 100.0, 95.0, 0.75, 0.27, 0.041, 0.017


def test_put_call_parity_identity():
    Ks = np.linspace(40, 200, 33)
    for T_ in (1 / 365, 0.1, 1.0, 5.0):
        c = bsm.bsm_price(S, Ks, T_, SIG, R, Q, "C")
        p = bsm.bsm_price(S, Ks, T_, SIG, R, Q, "P")
        np.testing.assert_allclose(c - p, S * np.exp(-Q * T_) - Ks * np.exp(-R * T_), atol=1e-10)


def test_textbook_value():
    # Hull (2018), Example 15.6: S=42, K=40, r=10%, sigma=20%, T=0.5 -> c=4.76, p=0.81
    assert float(bsm.bsm_price(42, 40, 0.5, 0.2, 0.10, 0.0, "C")) == pytest.approx(4.7594, abs=1e-4)
    assert float(bsm.bsm_price(42, 40, 0.5, 0.2, 0.10, 0.0, "P")) == pytest.approx(0.8086, abs=1e-4)


def test_black76_equals_bsm_on_forward():
    F, D = bsm.forward_and_discount(S, T, R, Q)
    for cp in ("C", "P"):
        a = bsm.bsm_price(S, np.array([60.0, 95.0, 140.0]), T, SIG, R, Q, cp)
        b = bsm.black76_price(F, np.array([60.0, 95.0, 140.0]), T, SIG, D, cp)
        np.testing.assert_allclose(a, b, rtol=1e-13)


def test_limits_and_signs():
    assert float(bsm.bsm_price(S, K, 0.0, SIG, R, Q, "C")) == pytest.approx(S - K)
    assert float(bsm.bsm_price(S, K, T, 0.0, R, Q, "P")) == pytest.approx(0.0)
    np.testing.assert_array_equal(bsm.option_sign(["c", "P", "call", "put"]), [1, -1, 1, -1])
    np.testing.assert_array_equal(bsm.option_sign(np.array([True, False])), [1, -1])
    with pytest.raises(ValueError):
        bsm.option_sign("X")


@pytest.mark.parametrize("cp", ["C", "P"])
@pytest.mark.parametrize("K_", [60.0, 95.0, 100.0, 150.0])
def test_greeks_match_finite_differences(cp, K_):
    g = bsm.bsm_greeks(S, K_, T, SIG, R, Q, cp)

    def price(**kw):
        a = {"S": S, "K": K_, "T": T, "sigma": SIG, "r": R, "q": Q} | kw
        return float(bsm.bsm_price(a["S"], a["K"], a["T"], a["sigma"], a["r"], a["q"], cp))

    def delta(**kw):
        a = {"S": S, "T": T, "sigma": SIG} | kw
        return float(bsm.bsm_greeks(a["S"], K_, a["T"], a["sigma"], R, Q, cp)["delta"])

    def vega(sig):
        return float(bsm.bsm_greeks(S, K_, T, sig, R, Q, cp)["vega"])

    h = 1e-4
    assert float(g["price"]) == pytest.approx(price(), rel=1e-12)
    assert float(g["delta"]) == pytest.approx((price(S=S + h) - price(S=S - h)) / (2 * h), abs=1e-7)
    assert float(g["gamma"]) == pytest.approx((price(S=S + 1e-2) - 2 * price() + price(S=S - 1e-2)) / 1e-4, abs=1e-6)
    assert float(g["vega"]) == pytest.approx((price(sigma=SIG + h) - price(sigma=SIG - h)) / (2 * h), abs=1e-6)
    assert float(g["rho"]) == pytest.approx((price(r=R + h) - price(r=R - h)) / (2 * h), abs=1e-6)
    theta_fd = -(price(T=T + h) - price(T=T - h)) / (2 * h)
    assert float(g["theta"]) == pytest.approx(theta_fd, abs=1e-6)
    assert float(g["theta_day"]) == pytest.approx(theta_fd / 365.0, abs=1e-8)
    assert float(g["vanna"]) == pytest.approx((delta(sigma=SIG + h) - delta(sigma=SIG - h)) / (2 * h), abs=1e-6)
    assert float(g["volga"]) == pytest.approx((vega(SIG + h) - vega(SIG - h)) / (2 * h), rel=1e-6, abs=1e-6)
    charm_fd = -(delta(T=T + h) - delta(T=T - h)) / (2 * h)
    assert float(g["charm"]) == pytest.approx(charm_fd, abs=1e-6)


def test_greeks_vectorized_shapes():
    Ks = np.linspace(80, 120, 5)
    g = bsm.bsm_greeks(S, Ks, T, SIG, R, Q, np.array(["C", "P", "C", "P", "C"]))
    assert all(v.shape == (5,) for v in g.values())
    assert np.isnan(bsm.bsm_greeks(S, K, 0.0, SIG, R, Q, "C")["delta"])


def test_implied_vol_round_trip_wide_grid():
    F, D = 100.0, 0.97
    Ts = np.array([1 / 365, 7 / 365, 0.25, 1.0, 5.0])
    ks = np.exp(np.linspace(-2.0, 2.0, 81))
    sigs = np.array([0.05, 0.2, 0.5, 1.0, 2.0])
    Kg, Tg, Sg = np.meshgrid(F * ks, Ts, sigs, indexing="ij")
    otm = bsm.black76_price(F, Kg, Tg, Sg, 1.0, np.where(Kg >= F, "C", "P"))
    ok = otm > 1e-10 * F   # below this the price carries no information about sigma in float64
    assert ok.sum() > 900
    for cp in ("C", "P"):
        p = bsm.black76_price(F, Kg, Tg, Sg, D, cp)
        iv = bsm.implied_vol_black(p, F, Kg, Tg, D, cp)
        assert not np.isnan(iv[ok]).any()
        # ITM quotes lose digits to cancellation (time value = price - intrinsic)
        np.testing.assert_allclose(iv[ok], Sg[ok], atol=1e-6)


def test_implied_vol_bsm_wrapper_and_bounds():
    p = bsm.bsm_price(S, K, T, SIG, R, Q, "P")
    assert float(bsm.implied_vol(p, S, K, T, R, Q, "P")) == pytest.approx(SIG, abs=1e-10)
    F, D = (float(x) for x in bsm.forward_and_discount(S, T, R, Q))
    intrinsic_c = D * (F - K)
    out = bsm.implied_vol_black([intrinsic_c * 0.999, D * F * 1.001, -1.0, np.nan], F, K, T, D, "C")
    assert np.isnan(out).all()   # below intrinsic, above the upper bound, negative, missing


def test_corrado_miller_guess_is_close_near_the_money():
    F = 100.0
    for sd in (0.05, 0.1, 0.3):
        c = float(bsm.black76_price(F, 102.0, 1.0, sd, 1.0, "C"))
        g = float(bsm.corrado_miller_guess(np.array([c]), np.array([F]), np.array([102.0]))[0])
        assert g == pytest.approx(sd, rel=0.05)
