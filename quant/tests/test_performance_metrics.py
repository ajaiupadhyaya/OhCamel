"""factors.performance against hand computations on real fixture data and
published identities / worked examples."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from ohcamel_quant.factors import performance as perf


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"]


@pytest.fixture(scope="module")
def covid(spy):
    return spy.loc["2020-01-01":"2020-12-31"]


@pytest.fixture(scope="module")
def real_rf(market, etf_returns):
    """A REAL daily rate series (2y Treasury yield from the FRED fixture,
    converted like the production T-bill rf) standing in for the T-bill rate."""
    from ohcamel_quant.data.fred import yield_to_daily_return

    y = market.fred(["DGS2"]).data["DGS2"]
    rf = yield_to_daily_return(y.reindex(etf_returns.index).ffill().bfill())
    return rf.astype(float)


# ------------------------------------------------------------ hand computations
def test_sharpe_sortino_vol_cagr_by_hand(covid):
    x = covid.to_numpy()
    n = len(x)
    mean = sum(x) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1))
    assert perf.sharpe_ratio(covid) == pytest.approx(mean / sd * math.sqrt(252), rel=1e-12)
    assert perf.annualized_vol(covid) == pytest.approx(sd * math.sqrt(252), rel=1e-12)
    dd = math.sqrt(sum(min(v, 0.0) ** 2 for v in x) / n)
    assert perf.sortino_ratio(covid) == pytest.approx(mean * 252 / (dd * math.sqrt(252)), rel=1e-12)
    g = 1.0
    for v in x:
        g *= 1 + v
    assert perf.total_return(covid) == pytest.approx(g - 1, rel=1e-12)
    assert perf.cagr(covid) == pytest.approx(g ** (252 / n) - 1, rel=1e-12)


def test_sharpe_with_rf(covid, real_rf):
    rf = real_rf.loc[covid.index]
    ex = covid - rf
    assert perf.sharpe_ratio(covid, rf) == pytest.approx(ex.mean() / ex.std() * math.sqrt(252), rel=1e-12)


def test_max_drawdown_by_hand_covid(covid):
    wealth, peak, mdd = 1.0, 1.0, 0.0
    for v in covid.to_numpy():
        wealth *= 1 + v
        peak = max(peak, wealth)
        mdd = min(mdd, wealth / peak - 1)
    assert perf.max_drawdown(covid) == pytest.approx(mdd, rel=1e-12)
    ep = perf.drawdown_episodes(covid, top=3)
    worst = ep.iloc[0]
    assert worst["depth"] == pytest.approx(mdd, rel=1e-12)
    assert worst["peak"] == pd.Timestamp("2020-02-19")
    assert worst["trough"] == pd.Timestamp("2020-03-23")
    assert worst["recovered"] and worst["recovery"] > worst["trough"]
    assert worst["length"] == worst["decline"] + worst["recovery_periods"]
    assert list(ep["depth"]) == sorted(ep["depth"])
    assert perf.calmar_ratio(covid) == pytest.approx(perf.cagr(covid) / abs(mdd), rel=1e-12)
    dd = perf.drawdown_series(covid).to_numpy()
    assert perf.ulcer_index(covid) == pytest.approx(math.sqrt(np.mean(dd**2)), rel=1e-12)


def test_omega_var_cvar_tail(covid):
    x = covid.to_numpy()
    assert perf.omega_ratio(covid) == pytest.approx(x[x > 0].sum() / -x[x < 0].sum(), rel=1e-12)
    lvl = 1.1 ** (1 / 252) - 1
    assert perf.omega_ratio(covid, 0.10) == pytest.approx(
        np.maximum(x - lvl, 0).sum() / np.maximum(lvl - x, 0).sum(), rel=1e-12)
    var, cvar = perf.historical_var_cvar(covid, 0.95)
    q = np.quantile(x, 0.05)
    assert var == pytest.approx(-q) and cvar == pytest.approx(-x[x <= q].mean())
    assert cvar >= var > 0
    assert perf.tail_ratio(covid) == pytest.approx(abs(np.quantile(x, 0.95)) / abs(q))


def test_distribution_and_win_loss(spy):
    d = perf.distribution_stats(spy)
    x = spy.to_numpy()
    n = len(x)
    s, k = stats.skew(x), stats.kurtosis(x)
    assert d["jarque_bera"] == pytest.approx(n / 6 * (s**2 + k**2 / 4), rel=1e-9)
    assert d["jarque_bera_p"] < 1e-10  # daily equity returns are not normal
    assert d["excess_kurtosis"] > 3
    wl = perf.win_loss_stats(spy)
    assert wl["hit_rate"] == pytest.approx((x > 0).sum() / (x != 0).sum())
    assert wl["avg_win"] > 0 > wl["avg_loss"]


def test_monthly_table_compounds(spy):
    tab = perf.monthly_table(spy.loc["2017-01-01":"2019-12-31"])
    assert list(tab.index) == [2017, 2018, 2019]
    months = tab.drop(columns="Annual")
    comp = (1 + months).prod(axis=1) - 1
    np.testing.assert_allclose(comp.to_numpy(), tab["Annual"].to_numpy(), rtol=1e-12)
    jan17 = spy.loc["2017-01"]
    assert tab.loc[2017, "Jan"] == pytest.approx((1 + jan17).prod() - 1)


# ------------------------------------------------------------- Sharpe inference
def test_lo_standard_errors(spy):
    si = perf.sharpe_inference(spy, lags=0)
    n = len(spy)
    sr = spy.mean() / spy.std()
    assert si["se_iid"] == pytest.approx(math.sqrt((1 + sr**2 / 2) / n) * math.sqrt(252), rel=1e-12)
    # With 0 lags the GMM variance equals the non-normal IID formula
    # 1 - g3 SR + (g4 - 1)/4 SR^2 (up to the ddof of sigma).
    assert si["se_hac"] == pytest.approx(si["se_nonnormal"], rel=2e-3)
    # No autocorrelation lags -> eta = sqrt(q) -> adjusted == IID-scaled Sharpe.
    assert si["sharpe_autocorr_adjusted"] == pytest.approx(si["sharpe"], rel=1e-12)
    full = perf.sharpe_inference(spy)
    assert full["autocorr_lags"] == 8  # floor(4 (2513/100)^(2/9))
    assert full["ci95_lower"] < full["sharpe"] < full["ci95_upper"]


# --------------------------------------------------- PSR / MinTRL / DSR identities
def test_bailey_lopez_de_prado_2014_worked_example():
    """DSR paper, section 'A numerical example': SR = 2.5 (annual), T = 1250
    daily obs (250 days/yr), N = 100 trials, V[SR_n] = 1/2 (annual), skew -3,
    kurtosis 10 -> SR_0 ~= 0.1132 (per period) and DSR ~= 0.9004."""
    d = perf.deflated_sharpe(sr=2.5, n_obs=1250, n_trials=100, sr_variance=0.5, skew=-3.0,
                             kurtosis=10.0, periods_per_year=250, detail=True)
    assert d.sr0 == pytest.approx(0.1132, abs=5e-4)
    assert d.dsr == pytest.approx(0.9004, abs=1e-3)
    assert perf.deflated_sharpe(sr=2.5, n_obs=1250, n_trials=100, sr_variance=0.5, skew=-3.0,
                                kurtosis=10.0, periods_per_year=250) == pytest.approx(d.dsr)


def test_psr_identities(spy):
    x = spy.to_numpy()
    sr = x.mean() / x.std(ddof=1)
    g3, g4 = stats.skew(x), stats.kurtosis(x, fisher=False)
    assert perf.probabilistic_sharpe(sr, len(x), sr, g3, g4) == pytest.approx(0.5)
    psr0 = perf.probabilistic_sharpe(sr, len(x), 0.0, g3, g4)
    assert 0.5 < psr0 < 1
    # Normal returns: PSR = Phi(SR sqrt(T-1) / sqrt(1 + SR^2/2)).
    assert perf.probabilistic_sharpe(sr, len(x)) == pytest.approx(
        stats.norm.cdf(sr * math.sqrt(len(x) - 1) / math.sqrt(1 + sr**2 / 2)))
    # MinTRL: PSR evaluated at T = MinTRL equals the confidence level.
    m = perf.min_track_record_length(sr, 0.0, g3, g4, 0.95)
    assert perf.probabilistic_sharpe(sr, m, 0.0, g3, g4) == pytest.approx(0.95, abs=1e-9)
    z = (sr - 0.0) * math.sqrt(m - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr**2)
    assert stats.norm.cdf(z) == pytest.approx(0.95, rel=1e-12)
    assert perf.min_track_record_length(-0.01) == math.inf
    # DSR with one trial is PSR(0); more trials can only lower it.
    dsr1 = perf.deflated_sharpe(x, n_trials=1, sr_variance=0.0)
    assert dsr1 == pytest.approx(psr0)
    prev = dsr1
    for n in (2, 10, 100, 1000):
        d = perf.deflated_sharpe(x, n_trials=n, sr_variance=1e-4)
        assert d <= prev + 1e-15
        prev = d


def test_expected_max_sharpe_monotone():
    vals = [perf.expected_max_sharpe(n, 0.01) for n in (2, 10, 100, 1000)]
    assert all(b > a for a, b in zip(vals, vals[1:], strict=False))
    with pytest.raises(ValueError):
        perf.expected_max_sharpe(0, 0.01)


# ----------------------------------------------------------- portfolio returns
def test_buy_and_hold_equals_share_holdings(market, real_rf):
    px = market.prices(["SPY", "TLT", "GLD"], "2019-01-01", "2021-12-31").data
    rets = px.pct_change().iloc[1:]
    w = {"SPY": 0.5, "TLT": 0.3, "GLD": 0.1}          # 10% cash
    rf = real_rf.reindex(rets.index)
    rp = perf.portfolio_returns(rets, w, "none", rf)
    v0 = 1_000_000.0
    shares = {t: w[t] * v0 / px[t].iloc[0] for t in w}
    cash = (1 - sum(w.values())) * v0 * np.cumprod(1 + rf.to_numpy())
    value = sum(shares[t] * px[t].iloc[1:].to_numpy() for t in w) + cash
    np.testing.assert_allclose(v0 * np.cumprod(1 + rp.to_numpy()), value, rtol=1e-10)


def test_rebalance_modes(etf_returns, real_rf):
    r = etf_returns[["SPY", "TLT"]].loc["2022-01-01":"2022-12-31"]
    rf = real_rf.reindex(r.index)
    w = {"SPY": 0.6, "TLT": 0.5}  # levered: cash = -0.1 borrows at rf
    daily = perf.portfolio_returns(r, w, "daily", rf)
    np.testing.assert_allclose(daily, 0.6 * r["SPY"] + 0.5 * r["TLT"] - 0.1 * rf, rtol=1e-12)
    monthly, wp = perf.portfolio_returns(r, w, "monthly", rf, return_weights=True)
    # Each month is a buy-and-hold from target weights.
    for _, grp in r.groupby(r.index.to_period("M")):
        bh = perf.portfolio_returns(grp, w, "none", rf.loc[grp.index])
        np.testing.assert_allclose(monthly.loc[grp.index], bh, rtol=1e-12)
    firsts = r.groupby(r.index.to_period("M")).head(1).index
    np.testing.assert_allclose(wp.loc[firsts, ["SPY", "TLT", "CASH"]].to_numpy(),
                               np.tile([0.6, 0.5, -0.1], (len(firsts), 1)), atol=1e-12)
    np.testing.assert_allclose(wp.sum(axis=1), 1.0, rtol=1e-12)
    # Within one month, monthly rebalancing == buy-and-hold.
    jan = r.loc["2022-01"]
    np.testing.assert_allclose(perf.portfolio_returns(jan, w, "monthly"), perf.portfolio_returns(jan, w, "none"))
    to = perf.turnover(wp, w, r, rf)
    assert len(to) == 11 and (to > 0).all()
    # Manual turnover at the February reset.
    jan_end = wp.loc[jan.index[-1]] * (1 + pd.Series({"SPY": jan["SPY"].iloc[-1], "TLT": jan["TLT"].iloc[-1],
                                                      "CASH": rf.loc[jan.index[-1]]}))
    jan_end /= jan_end.sum()
    tgt = pd.Series({"SPY": 0.6, "TLT": 0.5, "CASH": -0.1})
    assert to.iloc[0] == pytest.approx(0.5 * (jan_end - tgt).abs().sum(), rel=1e-10)
    with pytest.raises(ValueError):
        perf.portfolio_returns(r, {"XXX": 1.0})


# ------------------------------------------------------------ relative & rolling
def test_relative_stats_identities(etf_returns):
    r = etf_returns["QQQ"]
    b = etf_returns["SPY"]
    rel = perf.relative_stats(r, b)
    assert rel["beta"] == pytest.approx(np.cov(r, b)[0, 1] / b.var(), rel=1e-10)
    te = (r - b).std() * math.sqrt(252)
    assert rel["tracking_error"] == pytest.approx(te, rel=1e-12)
    assert rel["information_ratio"] == pytest.approx((r - b).mean() * 252 / te, rel=1e-12)
    assert rel["correlation"] == pytest.approx(np.corrcoef(r, b)[0, 1])
    assert rel["treynor"] == pytest.approx(r.mean() * 252 / rel["beta"], rel=1e-10)
    self_rel = perf.relative_stats(b, b)
    assert self_rel["up_capture"] == pytest.approx(1.0) and self_rel["down_capture"] == pytest.approx(1.0)
    assert self_rel["beta"] == pytest.approx(1.0) and self_rel["m2_excess"] == pytest.approx(0.0, abs=1e-12)
    assert self_rel["information_ratio"] != self_rel["information_ratio"]  # TE = 0 -> NaN


def test_rolling_stats_last_window(etf_returns):
    r, b = etf_returns["XLK"], etf_returns["SPY"]
    roll = perf.rolling_stats(r, 126, b)
    last_r, last_b = r.iloc[-126:], b.iloc[-126:]
    row = roll.iloc[-1]
    assert row["vol"] == pytest.approx(last_r.std() * math.sqrt(252), rel=1e-9)
    assert row["sharpe"] == pytest.approx(last_r.mean() / last_r.std() * math.sqrt(252), rel=1e-9)
    assert row["beta"] == pytest.approx(np.cov(last_r, last_b)[0, 1] / last_b.var(), rel=1e-9)
    assert row["return"] == pytest.approx((1 + last_r).prod() ** (252 / 126) - 1, rel=1e-9)
    assert len(roll) == len(r) - 125


def test_performance_report_shape(etf_returns):
    rep = perf.performance_report(etf_returns["XLE"], etf_returns["SPY"], n_trials=20, sr_variance=0.1)
    s = rep["summary"]
    assert s["max_drawdown"] < -0.5  # 2020 energy crash
    assert rep["sharpe_inference"]["deflated"]["dsr"] <= rep["sharpe_inference"]["psr"]
    assert rep["relative"]["beta"] > 0
    assert list(rep["monthly_table"].columns)[-1] == "Annual"
    assert rep["rolling"] is not None and len(rep["equity"]) == len(etf_returns)


def test_sharpe_inference_zero_variance_is_nan_not_crash(spy):
    flat = pd.Series(0.0001, index=spy.index[:300])
    si = perf.sharpe_inference(flat)
    assert math.isnan(si["sharpe"]) and math.isnan(si["se_hac"]) and si["n_obs"] == 300


def test_turnover_uses_rebalance_calendar(etf_returns):
    r = etf_returns[["SPY", "TLT"]].loc["2019-01-01":"2019-12-31"]
    w = {"SPY": 1.0}
    _, wp = perf.portfolio_returns(r, w, "quarterly", return_weights=True)
    to = perf.turnover(wp, w, r, rebalance="quarterly")
    assert list(to.index.month) == [4, 7, 10] and (to == 0).all()
    # Drifting two-asset book: calendar resets agree with the inferred ones.
    w2 = {"SPY": 0.6, "TLT": 0.4}
    _, wp2 = perf.portfolio_returns(r, w2, "monthly", return_weights=True)
    a = perf.turnover(wp2, w2, r, rebalance="monthly")
    b = perf.turnover(wp2, w2, r)
    pd.testing.assert_series_equal(a, b)
    assert len(a) == 11 and (a > 0).all()


def test_rf_source_label_is_honest():
    from ohcamel_quant.api.routers.performance import rf_source

    rf = pd.Series([0.0001])
    fred = [{"source": "fred", "detail": {"series": "DGS3MO"}}]
    french = [{"source": "french", "detail": {}},
              {"source": "derived", "detail": {"rf_series": "FF_RF",
                                               "note": "FRED DGS3MO unavailable (x); using Ken French RF "
                                                       "(1-month T-bill) instead"}}]
    assert rf_source(rf, fred).startswith("FRED DGS3MO")
    assert rf_source(rf, french).startswith("Kenneth French")
    assert rf_source(None, fred) == "none (0)"
