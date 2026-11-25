"""Completion task 4.7: the beta league's mechanics (rolling OLS vs a Kalman beta on the next 21 days' beta)."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant.factors import kalman
from ohcamel_quant.models import beta_league as B


@pytest.fixture(scope="module")
def pair(etf_returns):
    r = etf_returns[["XLK", "SPY"]].dropna()
    return r["XLK"], r["SPY"]


def test_rolling_ols_beta_at_t_is_the_ols_slope_on_the_window_ending_at_t(pair):
    y, x = pair
    b = B.rolling_ols_beta(y, x, 60)
    t = 300
    ys, xs = y.iloc[t - 59:t + 1].to_numpy(), x.iloc[t - 59:t + 1].to_numpy()
    assert b.iloc[t] == pytest.approx(np.polyfit(xs, ys, 1)[0], rel=1e-10)
    assert b.iloc[:59].isna().all()


def test_the_target_is_the_next_21_sessions_beta_starting_strictly_after_t(pair):
    y, x = pair
    tgt = B.forward_beta(y, x, 21)
    t = 400
    ys, xs = y.iloc[t + 1:t + 22].to_numpy(), x.iloc[t + 1:t + 22].to_numpy()
    assert tgt.iloc[t] == pytest.approx(np.polyfit(xs, ys, 1)[0], rel=1e-10)
    assert tgt.iloc[-21:].isna().all()


def test_kalman_forecasts_are_point_in_time(pair):
    """The forecast at t uses q estimated on the trailing window up to the block's refit date d <= t and the filter
    on data up to t: extending the data never changes an earlier forecast, and each one is reproducible by hand."""
    y, x = pair
    y, x = y.iloc[:700], x.iloc[:700]
    a = B.kalman_forecasts(y.iloc[:600], x.iloc[:600], start=400, window=300, refit_every=21)
    b = B.kalman_forecasts(y, x, start=400, window=300, refit_every=21)
    common = a.index
    np.testing.assert_array_equal(a["beta"].to_numpy(), b.loc[common, "beta"].to_numpy())
    t = 430                                   # block starting at d = 421 (400 + 21)
    d = 421
    q = kalman.ml_q(y.iloc[d - 299:d + 1].to_numpy(), x.iloc[d - 299:d + 1].to_numpy())
    assert b.loc[y.index[t], "q"] == pytest.approx(q)
    hand = kalman.dynamic_beta(y.iloc[d - 299:t + 1], x.iloc[d - 299:t + 1], q=q).filtered.iloc[-1]
    assert b.loc[y.index[t], "beta"] == pytest.approx(hand, rel=1e-12)


def test_dm_test_with_hac_lag_is_zero_for_identical_losses_and_signed():
    rng = np.random.default_rng(1)
    la = rng.random(500)
    t = B.dm_test(la, la + 0.1 + 0.01 * rng.standard_normal(500), lag=B.HAC_LAG)
    assert t["mean_diff"] == pytest.approx(-0.1, abs=0.01) and t["stat"] < 0 and t["pvalue"] < 1e-6
    assert t["lag"] == B.HAC_LAG >= 20
    z = B.dm_test(la, la, lag=B.HAC_LAG)
    assert z["mean_diff"] == 0 and z["pvalue"] == 1.0


def test_league_scores_every_model_on_the_same_dates(pair):
    y, x = pair
    s = B.score_pair(y, x, eval_sessions=300, kalman_window=500, refit_every=63)
    f = s["forecasts"]
    assert list(f.columns) == ["target", "kalman", "ols_60", "ols_120", "ols_250", "q"]
    assert len(f) == 300 and f[["target", "kalman", "ols_60", "ols_120", "ols_250"]].notna().all().all()
    lg = s["league"]
    assert set(lg["model"]) == {"kalman", "ols_60", "ols_120", "ols_250"} and (lg["n"] == 300).all()
    assert sorted(lg["rank"]) == [1, 2, 3, 4]
    np.testing.assert_allclose(lg.set_index("model").loc["ols_60", "mse"],
                               float(((f["ols_60"] - f["target"]) ** 2).mean()))
    assert set(s["tests"]["vs"]) == {"ols_60", "ols_120", "ols_250"} and (s["tests"]["lag"] == B.HAC_LAG).all()
