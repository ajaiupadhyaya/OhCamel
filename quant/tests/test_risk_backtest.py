"""VaR backtesting: hand-computed test statistics, Basel zones, and the OOS engine on real data."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest
from scipy import stats

from ohcamel_quant.risk import backtest as bt
from ohcamel_quant.risk.core import portfolio_returns


# ------------------------------------------------------------------ Kupiec
def test_kupiec_hand_computed():
    # T = 250, x = 10, p = 1%:
    # LR = -2[240 ln .99 + 10 ln .01] + 2[240 ln .96 + 10 ln .04]
    #    = -2(-48.463781) + 2(-41.986032) = 12.955498
    res = bt.kupiec_pof(10, 250, 0.01)
    assert res["lr"] == pytest.approx(12.955498, abs=1e-5)
    assert res["p_value"] == pytest.approx(stats.chi2.sf(12.955498, 1), rel=1e-5)
    assert res["p_value"] < 0.001


def test_kupiec_edge_cases():
    # expected count exactly -> LR 0
    assert bt.kupiec_pof(5, 500, 0.01)["lr"] == pytest.approx(0.0, abs=1e-12)
    # zero exceptions: LR = -2 T ln(1-p)
    assert bt.kupiec_pof(0, 250, 0.01)["lr"] == pytest.approx(-2 * 250 * math.log(0.99))


# ------------------------------------------------------------------ Christoffersen
def test_christoffersen_independent_sequence_has_zero_lr():
    hits = np.array([0, 0, 1, 1, 0, 0, 0, 1, 0, 0])
    res = bt.christoffersen(hits, 0.3)
    assert (res["n00"], res["n01"], res["n10"], res["n11"]) == (4, 2, 2, 1)
    assert res["pi01"] == pytest.approx(1 / 3) and res["pi11"] == pytest.approx(1 / 3)
    assert res["lr_ind"] == pytest.approx(0.0, abs=1e-12)


def test_christoffersen_clustered_sequence_hand_computed():
    hits = np.array([0, 0, 0, 0, 1, 1, 1, 0, 0, 0])
    res = bt.christoffersen(hits, 0.1)
    assert (res["n00"], res["n01"], res["n10"], res["n11"]) == (5, 1, 1, 2)
    ll0 = 6 * math.log(2 / 3) + 3 * math.log(1 / 3)
    ll1 = 5 * math.log(5 / 6) + math.log(1 / 6) + math.log(1 / 3) + 2 * math.log(2 / 3)
    assert res["lr_ind"] == pytest.approx(-2 * (ll0 - ll1), rel=1e-12)
    pof = bt.kupiec_pof(3, 9, 0.1)["lr"]
    assert res["lr_cc"] == pytest.approx(pof + res["lr_ind"], rel=1e-12)
    assert res["p_value_cc"] == pytest.approx(stats.chi2.sf(res["lr_cc"], 2))


# ------------------------------------------------------------------ Basel
def test_traffic_light_matches_basel_table_for_250_obs_at_99():
    zones = [bt.traffic_light(x, 250, 0.01)["zone"] for x in range(0, 15)]
    assert zones[:5] == ["green"] * 5
    assert zones[5:10] == ["yellow"] * 5
    assert zones[10:] == ["red"] * 5
    tl = bt.traffic_light(4, 250, 0.01)
    assert tl["green_max"] == 4 and tl["yellow_max"] == 9
    # BCBS (1996) Table 2 cumulative probabilities
    assert bt.traffic_light(0, 250, 0.01)["cumulative_probability"] == pytest.approx(0.0811, abs=1e-4)
    assert bt.traffic_light(4, 250, 0.01)["cumulative_probability"] == pytest.approx(0.8922, abs=1e-4)
    assert bt.traffic_light(5, 250, 0.01)["cumulative_probability"] == pytest.approx(0.9588, abs=1e-4)
    assert bt.traffic_light(9, 250, 0.01)["cumulative_probability"] == pytest.approx(0.9997, abs=1e-4)
    assert bt.traffic_light(10, 250, 0.01)["cumulative_probability"] == pytest.approx(0.9999, abs=1e-4)


def test_traffic_light_scales_with_sample():
    tl = bt.traffic_light(0, 1000, 0.01)
    assert tl["green_max"] > 4 and tl["yellow_max"] > 9
    assert stats.binom.cdf(tl["green_max"], 1000, 0.01) < 0.95 <= stats.binom.cdf(tl["green_max"] + 1, 1000, 0.01)


def test_basel_regulatory_multiplier():
    h = np.zeros(250, dtype=int)
    h[:7] = 1
    res = bt.basel_regulatory(h)
    assert res["zone"] == "yellow" and res["plus_factor"] == 0.65 and res["multiplier"] == 3.65
    h[:12] = 1
    assert bt.basel_regulatory(h)["multiplier"] == 4.0


# ------------------------------------------------------------------ DQ / Z2 / scores
def test_dq_test_detects_clustering():
    p = 0.05
    T = 1000
    rng = np.random.default_rng(7)
    var = 0.02 + 0.002 * rng.standard_normal(T)   # time-varying VaR -> full-rank regressors
    iid = (rng.random(T) < p).astype(int)          # genuine Bernoulli(p) hits
    clustered = np.zeros(T, dtype=int)
    clustered[400:450] = 1             # same order of count, all in one block
    assert bt.dq_test(iid, var, p)["p_value"] > 0.05
    assert bt.dq_test(clustered, var, p)["p_value"] < 1e-6
    assert bt.dq_test(iid, var, p)["df"] == 6
    # a constant VaR is collinear with the intercept: dof = rank(X) = 5
    assert bt.dq_test(iid, np.full(T, 0.02), p)["df"] == 5


def test_acerbi_szekely_z2_sign():
    p = 0.025
    T = 400
    var = np.full(T, 0.02)
    es = np.full(T, 0.03)
    r = np.zeros(T)
    r[:10] = -0.03                     # T p = 10 exceptions with loss == ES -> Z2 = 0
    assert bt.acerbi_szekely_z2(r, var, es, p)["z2"] == pytest.approx(0.0, abs=1e-12)
    r[:10] = -0.06                     # losses twice ES -> Z2 = -1 (underestimated)
    assert bt.acerbi_szekely_z2(r, var, es, p)["z2"] == pytest.approx(-1.0)


def test_scores_prefer_the_true_quantile(etf_returns):
    r = etf_returns["SPY"].to_numpy()
    p = 0.01
    q = -np.quantile(r, p)
    good = bt.quantile_score(r, np.full(r.size, q), p)
    assert good < bt.quantile_score(r, np.full(r.size, 0.5 * q), p)
    assert good < bt.quantile_score(r, np.full(r.size, 2 * q), p)


# ------------------------------------------------------------------ engine
@pytest.fixture(scope="module")
def port(etf_returns):
    return portfolio_returns(etf_returns, {"SPY": 0.5, "TLT": 0.3, "GLD": 0.2}).to_numpy()


def test_rolling_forecasts_are_out_of_sample(port):
    """Forecasts for days < t cannot change when data from t onward changes."""
    kw = dict(alpha=0.99, window=250, refit_every=20)
    models = ("historical", "gaussian", "cornish_fisher", "ewma", "garch", "evt_pot")
    a = bt.rolling_forecasts(port[:800], models=models, **kw)
    mod = port[:800].copy()
    mod[700:] *= 5.0
    b = bt.rolling_forecasts(mod, models=models, **kw)
    cut = 700 - 250  # forecast index of day 700
    for m in models:
        np.testing.assert_allclose(a.var[m][:cut + 1], b.var[m][:cut + 1], rtol=1e-9, err_msg=m)


def test_rolling_historical_and_gaussian_match_direct(port):
    from ohcamel_quant.risk import var as vm

    f = bt.rolling_forecasts(port, alpha=0.99, window=500, models=("historical", "gaussian"))
    for j in (0, 777, len(port) - 501):
        w = port[j:j + 500]
        assert f.var["historical"][j] == pytest.approx(vm.historical(w, 0.99).var)
        assert f.es["historical"][j] == pytest.approx(vm.historical(w, 0.99).es)
        assert f.var["gaussian"][j] == pytest.approx(vm.gaussian(w, 0.99).var)


def test_full_backtest_is_fast_and_sane(port):
    t0 = time.perf_counter()
    f = bt.rolling_forecasts(port, alpha=0.99, window=500, refit_every=20)
    elapsed = time.perf_counter() - t0
    assert elapsed < 15  # typically ~2-3 s for 9 models x ~2000 days
    assert set(f.var) == set(bt.ALL_MODELS)
    oos = port[500:]
    for m in f.var:
        assert f.var[m].shape == oos.shape
        assert np.all(np.isfinite(f.var[m])) and np.all(f.es[m] >= f.var[m] - 1e-12), m
        card = bt.scorecard(oos, f.var[m], f.es[m], 0.99)
        # every model's 99% exception rate on real data lies in a plausible band
        assert 0.002 < card["exception_rate"] < 0.04, m
        assert card["traffic_light"]["zone"] in {"green", "yellow", "red"}
    # conditional-volatility models react to regimes: FHS is well-calibrated on this book
    fhs = bt.scorecard(oos, f.var["fhs"], f.es["fhs"], 0.99)
    assert fhs["kupiec"]["p_value"] > 0.01


def test_backtest_input_validation(port):
    with pytest.raises(ValueError):
        bt.rolling_forecasts(port[:300], window=500)
    with pytest.raises(ValueError):
        bt.rolling_forecasts(port, models=("nope",))
