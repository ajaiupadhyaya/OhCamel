"""Bond analytics against textbook values, closed forms and finite differences."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.macro import bonds as bd
from ohcamel_quant.macro import curve as cv

SETTLE = pd.Timestamp("2026-01-15")


def test_fabozzi_20y_9pct_at_6pct():
    """Fabozzi: 9% 20-year bond yielding 6% -> price 134.6722, modified
    duration 10.66, convexity 164.106 (annualized)."""
    b = bd.Bond(0.09, pd.Timestamp("2046-01-15"))
    p = bd.price_from_yield(b, SETTLE, 0.06)
    assert p["clean"] == pytest.approx(134.6722, abs=1e-4)
    assert p["accrued"] == 0.0
    rm = bd.risk_measures(b, SETTLE, 0.06)
    assert rm["modified_duration"] == pytest.approx(10.66, abs=5e-3)
    assert rm["convexity"] == pytest.approx(164.106, abs=1e-3)
    assert rm["dv01"] == pytest.approx(rm["modified_duration"] * rm["dirty"] * 1e-4)


def test_par_bond_and_closed_form_duration():
    y = 0.05
    b = bd.Bond(y, pd.Timestamp("2036-01-15"))
    assert bd.price_from_yield(b, SETTLE, y)["clean"] == pytest.approx(100.0, abs=1e-10)
    # Macaulay duration of a par bond: (1+y/f)/y * [1 - (1+y/f)^-n]
    n, f = 20, 2
    mac = (1 + y / f) / y * (1 - (1 + y / f) ** -n)
    assert bd.risk_measures(b, SETTLE, y)["macaulay_duration"] == pytest.approx(mac, rel=1e-12)


def test_yield_price_round_trip_and_finite_differences():
    b = bd.Bond(0.0375, pd.Timestamp("2033-08-15"))
    settle = pd.Timestamp("2026-03-02")  # mid-period
    y = 0.0421
    clean = bd.price_from_yield(b, settle, y)["clean"]
    assert bd.yield_from_price(b, settle, clean) == pytest.approx(y, abs=1e-12)
    rm = bd.risk_measures(b, settle, y)
    h = 1e-5
    pu = bd.price_from_yield(b, settle, y + h)["dirty"]
    pdn = bd.price_from_yield(b, settle, y - h)["dirty"]
    p0 = rm["dirty"]
    assert rm["modified_duration"] == pytest.approx(-(pu - pdn) / (2 * h * p0), rel=1e-6)
    assert rm["convexity"] == pytest.approx((pu + pdn - 2 * p0) / (h * h * p0), rel=1e-4)


def test_act_act_accrued_and_schedule():
    b = bd.Bond(0.0425, pd.Timestamp("2036-05-15"))
    # 92 of 184 days into the May-Nov period -> half a coupon
    assert bd.accrued_interest(b, pd.Timestamp("2026-08-15")) == pytest.approx(2.125 * 92 / 184)
    prev, nxt = bd.coupon_schedule(b, pd.Timestamp("2026-08-15"))
    assert prev == pd.Timestamp("2026-05-15") and nxt[0] == pd.Timestamp("2026-11-15")
    assert len(nxt) == 20 and nxt[-1] == b.maturity
    # end-of-month rule: Feb 28/29 maturities roll to Aug 31
    e = bd.Bond(0.04, pd.Timestamp("2030-02-28"))
    _, dates = bd.coupon_schedule(e, pd.Timestamp("2026-01-10"))
    assert pd.Timestamp("2026-08-31") in dates and pd.Timestamp("2027-02-28") in dates
    # clean + accrued = dirty; dirty continuous across a coupon date
    y = 0.045
    before = bd.price_from_yield(b, pd.Timestamp("2026-11-14"), y)
    after = bd.price_from_yield(b, pd.Timestamp("2026-11-16"), y)
    assert abs(before["clean"] - after["clean"]) < 0.05
    with pytest.raises(ValueError):
        bd.coupon_schedule(b, pd.Timestamp("2040-01-01"))


def _curve():
    return cv.bootstrap_par_curve([0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30],
                                  [4.28, 4.14, 3.95, 3.62, 3.55, 3.61, 3.77, 4.02, 4.51, 4.60])


def test_curve_price_z_spread_and_krd():
    zc = _curve()
    b = bd.Bond(0.04, pd.Timestamp("2035-11-15"))
    settle = pd.Timestamp("2026-02-17")
    fair = bd.curve_price(b, settle, zc)
    assert bd.z_spread(b, settle, zc, fair["clean"]) == pytest.approx(0.0, abs=1e-12)
    zs = bd.z_spread(b, settle, zc, fair["clean"] - 1.0)
    assert zs > 0
    eff = bd.effective_duration(b, settle, zc, zs)
    krd = bd.key_rate_durations(b, settle, zc, spread=zs)
    assert krd["krd"].sum() == pytest.approx(eff["effective_duration"], rel=1e-6)
    # a ~10y bond's risk sits at the 7y/10y keys; nothing beyond 20y
    assert krd.set_index("tenor").loc[[7.0, 10.0], "krd"].sum() > 0.8 * eff["effective_duration"]
    assert krd.set_index("tenor").loc[30.0, "krd"] == pytest.approx(0.0, abs=1e-12)


def test_flat_curve_price_matches_continuous_yield():
    zc = cv.bootstrap_par_curve([0.5, 1, 2, 5, 10, 30], [5] * 6)
    b = bd.Bond(0.06, pd.Timestamp("2031-01-15"))
    cf, t = bd._curve_times(b, SETTLE)
    r = 2 * np.log(1.025)
    assert bd.curve_price(b, SETTLE, zc)["dirty"] == pytest.approx(float(np.sum(cf * np.exp(-r * t))), rel=1e-10)


def test_tents_partition_unity():
    t = np.linspace(0, 40, 401)
    keys = bd.DEFAULT_KEY_TENORS
    total = sum(bd._tent(t, keys, i) for i in range(len(keys)))
    assert np.allclose(total, 1.0)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        bd.Bond(0.05, pd.Timestamp("2030-01-01"), freq=3)
    with pytest.raises(ValueError):
        bd.Bond(-0.01, pd.Timestamp("2030-01-01"))


def test_z_spread_deep_discount_and_clear_error():
    """Regression: the fixed [-50%, +100%] bracket raised scipy's opaque
    "f(a) and f(b) must have different signs" for distressed prices."""
    zc = _curve()
    b = bd.Bond(0.04, pd.Timestamp("2056-05-15"))
    settle = pd.Timestamp("2026-08-17")
    zs = bd.z_spread(b, settle, zc, 1.0)
    assert zs > 1.0
    cf, t = bd._curve_times(b, settle)
    dirty = float(np.sum(cf * np.exp(-(zc.zero(t) / 100 + zs) * t)))
    assert dirty == pytest.approx(1.0 + bd.accrued_interest(b, settle), rel=1e-9)
    with pytest.raises(ValueError, match="Z-spread"):
        bd.z_spread(b, settle, zc, 1e200)
