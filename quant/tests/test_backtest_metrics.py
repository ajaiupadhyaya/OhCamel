"""Backtest statistics against closed forms and published worked examples."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from ohcamel_quant.backtest import metrics as M


@pytest.fixture(scope="module")
def spy(etf_returns) -> pd.Series:
    return etf_returns["SPY"]


def test_sharpe_vol_cagr(spy):
    a = spy.to_numpy()
    assert M.sharpe(spy) == pytest.approx(a.mean() / a.std(ddof=1) * math.sqrt(252))
    assert M.ann_vol(spy) == pytest.approx(a.std(ddof=1) * math.sqrt(252))
    assert M.cagr(spy) == pytest.approx(np.prod(1 + a) ** (252 / len(a)) - 1)


def test_max_drawdown_matches_cummax(spy):
    w = (1 + spy).cumprod()
    dd = w / np.maximum(w.cummax(), 1.0) - 1
    assert M.max_drawdown(spy) == pytest.approx(dd.min())
    # COVID crash: SPY fell by roughly a third peak-to-trough in Feb-Mar 2020
    covid = M.max_drawdown(spy.loc["2020-01-01":"2020-06-30"])
    assert -0.40 < covid < -0.30


def test_drawdown_table_orders_by_depth(spy):
    tab = M.drawdown_table(spy, 5)
    depths = [e["depth"] for e in tab]
    assert depths == sorted(depths)
    assert depths[0] == pytest.approx(M.max_drawdown(spy))
    top = tab[0]
    assert top["peak"] < top["trough"]
    assert top["recovery"] is None or top["recovery"] > top["trough"]


def test_sortino_definition(spy):
    a = spy.to_numpy()
    dd = math.sqrt(np.mean(np.minimum(a, 0) ** 2))
    assert M.sortino(spy) == pytest.approx(a.mean() / dd * math.sqrt(252))


def test_psr_identities():
    # SR equal to the benchmark -> 1/2
    assert M.probabilistic_sharpe(0.1, 500, -0.5, 6.0, 0.1) == pytest.approx(0.5)
    # Gaussian returns (skew 0, kurt 3): Phi(SR sqrt(n-1) / sqrt(1 + SR^2/2))
    sr, n = 0.08, 750
    expected = stats.norm.cdf(sr * math.sqrt(n - 1) / math.sqrt(1 + sr * sr / 2))
    assert M.probabilistic_sharpe(sr, n, 0.0, 3.0) == pytest.approx(expected)
    # negative skew and fat tails lower confidence
    assert M.probabilistic_sharpe(sr, n, -1.0, 8.0) < expected


def test_min_track_record_length_inverts_psr():
    sr, sk, ku = 0.07, -0.4, 5.0
    n = M.min_track_record_length(sr, sk, ku, 0.0, 0.95)
    assert M.probabilistic_sharpe(sr, n, sk, ku, 0.0) == pytest.approx(0.95, abs=1e-9)
    assert M.min_track_record_length(-0.01, sk, ku) == math.inf


def test_expected_max_sharpe_closed_form():
    g = 0.5772156649015329
    expected = (1 - g) * stats.norm.ppf(1 - 1 / 100) + g * stats.norm.ppf(1 - 1 / (100 * math.e))
    assert M.expected_max_sharpe(100, 1.0) == pytest.approx(expected)
    assert M.expected_max_sharpe(100, 1.0) == pytest.approx(2.5306, abs=1e-4)
    assert M.expected_max_sharpe(1000, 1.0) > M.expected_max_sharpe(10, 1.0)


def test_deflated_sharpe_paper_example():
    """Bailey & Lopez de Prado (2014), section 4 numerical example: annualized
    SR 2.5 over 1250 daily observations (250/yr), skew -3, kurtosis 10,
    100 trials with annualized Sharpe variance 1/2 -> DSR ~ 0.9004."""
    per = 250
    out = M.deflated_sharpe(2.5 / math.sqrt(per), 1250, -3.0, 10.0, 100, 0.5 / per)
    assert out["dsr"] == pytest.approx(0.9004, abs=5e-4)


def test_relative_stats_self_benchmark(spy, etf_returns):
    rel = M.relative_stats(spy, spy)
    assert rel["beta"] == pytest.approx(1.0)
    assert rel["alpha_ann"] == pytest.approx(0.0, abs=1e-12)
    assert rel["tracking_error"] == pytest.approx(0.0, abs=1e-12)
    # 2x SPY has beta 2
    assert M.relative_stats(2 * spy, spy)["beta"] == pytest.approx(2.0)
    qqq = M.relative_stats(etf_returns["QQQ"], spy)
    assert 0.9 < qqq["beta"] < 1.3 and qqq["correlation"] > 0.85


def test_rolling_and_calendar(spy):
    rs = M.rolling_sharpe(spy, 252)
    assert rs.iloc[:251].isna().all()
    assert rs.iloc[-1] == pytest.approx(M.sharpe(spy.iloc[-252:]))
    cal = M.calendar_returns(spy)
    assert (1 + cal).prod() == pytest.approx((1 + spy).prod())
    mon = M.monthly_returns(spy)
    assert mon.shape[1] == 12


def test_summary_keys(spy):
    s = M.summary(spy)
    for k in ("cagr", "sharpe", "sortino", "max_drawdown", "psr_vs_0", "skew", "kurtosis", "cvar_95_hist"):
        assert k in s
    assert 0 <= s["psr_vs_0"] <= 1
    assert s["kurtosis"] > 3  # daily equity returns are fat-tailed
