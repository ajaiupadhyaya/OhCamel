"""Recession probit (alignment, estimator) and the Sahm rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from ohcamel_quant.macro import recession as rec


def _months(n: int, start: str = "1970-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="MS")


def test_align_forecast_shift():
    idx = _months(6)
    y = pd.Series([0, 0, 1, 1, 0, 0], index=idx, dtype=float)
    x = pd.DataFrame({"s": [10, 11, 12, 13, 14, 15]}, index=idx, dtype=float)
    yl, xa = rec.align_forecast(y, x, 2)
    # x at Jan pairs with y at Mar
    assert list(xa["s"]) == [10, 11, 12, 13]
    assert list(yl) == [1, 1, 0, 0]
    assert yl.index[0] == idx[0]
    # mid-month stamps are normalized to month starts
    x2 = x.copy()
    x2.index = x2.index + pd.Timedelta(days=14)
    yl2, _ = rec.align_forecast(y, x2, 2)
    assert list(yl2) == list(yl)
    with pytest.raises(ValueError):
        rec.align_forecast(y, x, -1)


def test_probit_matches_statsmodels_and_recovers_params():
    rng = np.random.default_rng(7)
    n, h = 900, 3
    idx = _months(n)
    x = rng.normal(1.0, 1.2, n)
    latent = -0.6 - 0.9 * x + rng.normal(size=n)
    y_target = (latent > 0).astype(float)  # event at t+h driven by x_t
    y = pd.Series(np.r_[np.zeros(h), y_target[:-h]], index=idx)
    res = rec.fit_probit(y, pd.DataFrame({"spread": x}, index=idx), h, hac=False)
    direct = sm.Probit(y_target[: n - h], sm.add_constant(x[: n - h])).fit(disp=0)
    assert np.allclose(res.params.to_numpy(), direct.params, atol=1e-8)
    assert res.llf == pytest.approx(direct.llf, rel=1e-10)
    assert res.mcfadden_r2 == pytest.approx(direct.prsquared, rel=1e-10)
    assert res.params["const"] == pytest.approx(-0.6, abs=0.15)
    assert res.params["spread"] == pytest.approx(-0.9, abs=0.15)
    # fitted probabilities exist for every origin month, including the last h
    assert len(res.fitted) == n and res.fitted.index[-1] == idx[-1]
    hac = rec.fit_probit(y, pd.DataFrame({"spread": x}, index=idx), h, hac=True)
    assert np.allclose(hac.params, res.params)
    assert any("Newey-West" in s for s in hac.notes)


def test_estrella_r2():
    llf, ll0, n = -100.0, -200.0, 500
    assert rec.estrella_r2(llf, ll0, n) == pytest.approx(1 - (0.5) ** (-(2 / n) * ll0))
    assert rec.estrella_r2(ll0, ll0, n) == pytest.approx(0.0)


def test_sahm_rule_hand_sequence():
    u = pd.Series([4.0] * 12 + [4.0, 4.3, 4.6, 4.9], index=_months(16), dtype=float)
    s = rec.sahm_rule(u)
    # first defined value needs 3 (ma) + 12 (prior window) observations
    assert s.index[0] == _months(16)[14]
    # t=14: ma3 = (4.0+4.3+4.6)/3 = 4.3; prior 12 ma3 min = 4.0 -> 0.30
    assert s["sahm"].iloc[0] == pytest.approx(0.30)
    # t=15: ma3 = 4.6 -> 0.60, triggers
    assert s["sahm"].iloc[1] == pytest.approx(0.60)
    assert list(s["triggered"]) == [False, True]
    assert rec.SAHM_THRESHOLD == 0.5


def test_term_spread_splice_and_monthly_mean():
    d = pd.bdate_range("1982-01-01", "1982-03-31")
    daily = pd.Series(np.where(d.month == 1, 1.0, 2.0), index=d)
    idx = _months(4, "1981-10-01")
    gs10 = pd.Series([15.0, 14.0, 13.5, 14.0], index=idx)
    tb3 = pd.Series([14.0, 11.0, 10.5, 12.0], index=idx)
    s, notes = rec.term_spread_monthly(daily, gs10, tb3)
    assert list(s.index) == list(_months(6, "1981-10-01"))
    assert list(s.round(6)) == [1.0, 3.0, 3.0, 1.0, 2.0, 2.0]
    assert notes and "1982-01-01" in notes[0]


def test_recession_episodes():
    u = pd.Series([0, 1, 1, 0, 0, 1], index=_months(6), dtype=float)
    ep = rec.recession_episodes(u)
    assert ep == [{"start": "1970-02-01", "end": "1970-03-01"}, {"start": "1970-06-01", "end": "1970-06-01"}]


def test_sahm_uses_calendar_months_across_a_missing_release():
    """Regression: with a month missing (Oct 2025: no CPS), observation-count
    windows averaged 4 calendar months and looked back 13."""
    idx = _months(18)
    u = pd.Series([4.0] * 12 + [4.0, 4.2, 4.4, 4.6, 4.8, 5.0], index=idx, dtype=float)
    u_gap = u.drop(idx[15])
    s = rec.sahm_rule(u_gap)
    assert idx[15] not in s.index
    # t=16: calendar window {14, 15(missing), 16} -> mean(4.4, 4.8) = 4.6
    assert s.loc[idx[16], "ma3"] == pytest.approx(4.6)
    assert s.loc[idx[16], "sahm"] == pytest.approx(0.6)
    # t=17: window {15(missing), 16, 17} -> mean(4.8, 5.0)
    assert s.loc[idx[17], "ma3"] == pytest.approx(4.9)
    # gapless data unchanged
    full = rec.sahm_rule(u)
    assert full.loc[idx[16], "ma3"] == pytest.approx((4.4 + 4.6 + 4.8) / 3)
