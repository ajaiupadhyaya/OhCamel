"""Published strategies on the nine real fixture ETFs (2016-06..2026-06)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest import metrics as M
from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_backtest
from ohcamel_quant.backtest.strategies import (
    STRATEGIES,
    catalog,
    engle_granger_pvalue,
    get_strategy,
    validate_params,
    warmup_sessions,
)

NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def px(market) -> pd.DataFrame:
    return market.prices(NINE).data


def _run(px, key, params=None, cols=NINE, **cfg):
    spec = get_strategy(key)
    p = validate_params(spec, params or {}, cols)
    config = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=spec.default_max_leverage, **cfg)
    return run_backtest(StrategyContext(px[cols], None, px["SPY"]), spec.fn, p, config)


def test_tsmom_nine_etfs_sane(px):
    res = _run(px, "tsmom")
    net = res.live(res.returns_net)
    s = M.summary(net)
    assert len(net) > 2000                      # 12-month warm-up, then ~9 years live
    assert res.live_start.year == 2017
    assert 0.05 < s["ann_vol"] < 0.40
    assert -1.0 < s["sharpe"] < 2.0
    assert -0.7 < s["max_drawdown"] < 0
    assert res.audit["passed"] and res.audit["points"] >= 3
    # MOP weights: sign x 40%/sigma / N -- bonds (low vol) carry the largest |w|
    w = res.weights.loc[res.executions[-1]]
    # the cap binds at executions; weights then drift with prices until the next rebalance
    assert res.weights.loc[res.executions].abs().sum(axis=1).max() <= 3.0 + 1e-9
    assert abs(w["IEF"]) >= abs(w["XLE"]) or w["IEF"] == 0 or w["XLE"] == 0


def test_tsmom_sign_follows_trailing_return(px):
    spec = get_strategy("tsmom")
    ctx = StrategyContext(px)
    w = spec.fn(ctx, **validate_params(spec, {}, NINE))
    past = px / px.shift(252) - 1
    d = px.index[-1]
    np.testing.assert_array_equal(np.sign(w.loc[d]), np.sign(past.loc[d]))


def test_xsmom_holds_top_k(px):
    spec = get_strategy("xsmom")
    w = spec.fn(StrategyContext(px), lookback=252, skip=21, top_k=3, long_short=True)
    d = px.index[-1]
    score = px.shift(21).loc[d] / px.shift(252).loc[d] - 1
    top = set(score.nlargest(3).index)
    bottom = set(score.nsmallest(3).index)
    assert set(w.loc[d][w.loc[d] > 0].index) == top
    assert set(w.loc[d][w.loc[d] < 0].index) == bottom
    assert w.loc[d].sum() == pytest.approx(0.0)


def test_dual_momentum_absolute_filter(px):
    cols = ["SPY", "QQQ", "IWM", "IEF"]
    spec = get_strategy("dual_momentum")
    w = spec.fn(StrategyContext(px[cols]), lookback=252, top_n=1, safe_asset="IEF")
    valid = w.dropna()
    assert np.allclose(valid.sum(axis=1), 1.0)       # always fully in one asset
    mom = (px[cols] / px[cols].shift(252) - 1).loc[valid.index, ["SPY", "QQQ", "IWM"]]
    in_safe = valid["IEF"] == 1.0
    assert (mom.max(axis=1)[in_safe] <= 0).all()
    assert (mom.max(axis=1)[~in_safe] > 0).all()


def test_sma_trend_matches_monthly_definition(px):
    spec = get_strategy("sma_trend")
    w = spec.fn(StrategyContext(px[["SPY"]]), months=10)
    me = px["SPY"].groupby(px.index.to_period("M")).last()
    sma = me.rolling(10).mean()
    # at month ends the signal is price > 10-month SMA of month-end closes
    month_end_days = px.groupby(px.index.to_period("M")).tail(1).index[12:-1]
    for d in month_end_days[::7]:
        p = d.to_period("M")
        assert (w.loc[d, "SPY"] > 0) == bool(me.loc[p] > sma.loc[p])


def test_vol_managed_real_time_constant(px):
    res = _run(px, "vol_managed", cols=["SPY"])
    net = res.live(res.returns_net)
    assert res.weights.loc[res.executions, "SPY"].max() <= 2.0 + 1e-9
    # exposure falls when trailing variance is high: COVID March 2020 vs calm 2017
    assert res.weights.loc["2020-04"].mean().iloc[0] < res.weights.loc["2017-11"].mean().iloc[0]
    assert M.ann_vol(net) < 0.30


def test_risk_parity_inverse_vol_and_target(px):
    res = _run(px, "risk_parity", {"target_vol": 0.10})
    net = res.live(res.returns_net)
    assert 0.05 < M.ann_vol(net) < 0.16
    w = res.weights.loc[res.executions[-1]]
    assert w["IEF"] > w["XLE"] > 0                # lower vol, bigger weight
    unlev = get_strategy("risk_parity").fn(StrategyContext(px), vol_window=756, target_vol=0.0,
                                            cov_window=252, max_leverage=3.0)
    assert np.allclose(unlev.dropna().sum(axis=1), 1.0)


def test_low_beta_is_ex_ante_beta_neutral(px):
    cols = ["QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]
    spec = get_strategy("low_beta")
    ctx = StrategyContext(px[cols], None, px["SPY"])
    w = spec.fn(ctx, **validate_params(spec, {}, cols)).dropna()
    assert len(w) > 500
    lr = np.log(px).diff()
    d = w.index[-1]
    # recompute the shrunk betas at d and check sum w_i beta_i = 0
    win = lr.loc[:d]
    vol = win.iloc[-252:].std()
    r3 = win.rolling(3).sum().iloc[-1260:]
    rho = r3[cols].corrwith(r3["SPY"])
    beta = 0.6 * rho * vol[cols] / vol["SPY"] + 0.4
    assert float((w.loc[d] * beta).sum()) == pytest.approx(0.0, abs=1e-8)
    assert w.loc[d][beta.idxmin()] > 0 and w.loc[d][beta.idxmax()] < 0
    lo = _run(px, "low_beta", {"mode": "long_only"}, cols=cols)
    assert np.allclose(lo.weights.loc[lo.executions].sum(axis=1), 1.0)


def test_st_reversal_weights(px):
    spec = get_strategy("st_reversal")
    w = spec.fn(StrategyContext(px), lookback=5, long_only=False).dropna()
    assert np.allclose(w.clip(lower=0).sum(axis=1), 1.0)
    assert np.allclose(w.clip(upper=0).sum(axis=1), -1.0)
    d = w.index[-1]
    past = px.loc[d] / px.shift(5).loc[d] - 1
    assert w.loc[d, past.idxmin()] > 0 and w.loc[d, past.idxmax()] < 0


def test_pairs_distance_xlk_qqq_runs(px):
    res = _run(px, "pairs_distance", cols=["XLK", "QQQ"])
    net = res.live(res.returns_net)
    assert len(net) > 1500
    assert len(res.executions) >= 2                 # opened and closed at least once
    w = res.weights.loc[res.executions]
    assert np.allclose(w.sum(axis=1), 0.0)         # dollar-neutral legs
    assert M.ann_vol(net) < 0.25


def test_pairs_distance_selects_closest_pair(px):
    spec = get_strategy("pairs_distance")
    cols = ["SPY", "QQQ", "XLK", "GLD"]
    w = spec.fn(StrategyContext(px[cols]), formation=252, trading=126, n_pairs=1, entry_sd=0.5)
    traded = w.iloc[252:378].abs().sum()
    norm = px[cols].iloc[:252] / px[cols].iloc[0]
    ssd = {(a, b): float(((norm[a] - norm[b]) ** 2).sum()) for i, a in enumerate(cols) for b in cols[i + 1:]}
    best = min(ssd, key=ssd.get)
    assert set(traded[traded > 0].index) <= set(best)


def test_pairs_coint_xlk_qqq_runs(px):
    res = _run(px, "pairs_coint", cols=["XLK", "QQQ"])
    assert res.live_start is not None
    w = res.weights.loc[res.executions]
    assert (w.abs().sum(axis=1) <= 1.0 + 1e-9).all()     # gross 1 at entry
    assert len(res.executions) >= 2
    assert res.audit["passed"]


def test_engle_granger_matches_statsmodels(px):
    from statsmodels.tsa.stattools import coint

    y = np.log(px["XLK"].to_numpy()[-500:])
    x = np.log(px["QQQ"].to_numpy()[-500:])
    t, p = engle_granger_pvalue(y, x, lags=1)
    t_sm, p_sm, _ = coint(y, x, trend="c", maxlag=1, autolag=None)
    assert t == pytest.approx(t_sm, rel=1e-8)
    assert p == pytest.approx(p_sm, rel=1e-8)


def test_every_strategy_runs_and_passes_audit(px):
    for key, spec in STRATEGIES.items():
        cols = ["XLK", "QQQ"] if key.startswith("pairs") else NINE
        params = {"safe_asset": "IEF"} if key == "dual_momentum" else {}
        res = _run(px, key, params, cols)
        assert res.live_start is not None, key
        assert np.isfinite(res.returns_net).all(), key
        assert res.audit["passed"], key
        assert warmup_sessions(spec, validate_params(spec, params, cols)) >= 5


def test_catalog_and_validation():
    cat = catalog()
    assert {c["key"] for c in cat} == set(STRATEGIES)
    for c in cat:
        assert c["citation"] and len(c["explanation"]) > 80
        for p in c["params"]:
            assert {"name", "type", "default", "min", "max", "description"} <= set(p)
    spec = get_strategy("tsmom")
    with pytest.raises(ValueError, match="maximum"):
        validate_params(spec, {"lookback": 10_000}, NINE)
    with pytest.raises(ValueError, match="unknown parameter"):
        validate_params(spec, {"nope": 1}, NINE)
    with pytest.raises(ValueError, match="integer"):
        validate_params(spec, {"lookback": 12.5}, NINE)
    with pytest.raises(ValueError, match="at most 2"):
        validate_params(get_strategy("pairs_coint"), {}, NINE)
    with pytest.raises(ValueError, match="must be one of"):
        validate_params(get_strategy("dual_momentum"), {"safe_asset": "AGG"}, NINE)
    with pytest.raises(ValueError):
        get_strategy("nope")


def test_vol_managed_constant_uses_one_common_sample(px):
    """Moreira & Muir choose c so the managed and unmanaged series have equal
    volatility over the SAME sample; the real-time c_t must therefore compare
    std(f) and std(f / sigma^2_{s-1}) over the same sessions s <= t."""
    spec = get_strategy("vol_managed")
    ctx = StrategyContext(px[["SPY"]])
    w = spec.fn(ctx, window=21, min_history=252, max_leverage=100.0)["SPY"]
    f = px["SPY"].pct_change()
    var = f.rolling(21, min_periods=21).var(ddof=1) * 252
    scaled = f / var.shift(1)
    d = px.index[600]
    both = pd.concat([f, scaled], axis=1).loc[:d].dropna()
    c = both.iloc[:, 0].std() / both.iloc[:, 1].std()
    assert w.loc[d] == pytest.approx(c / var.loc[d], rel=1e-10)


@pytest.mark.parametrize("key", sorted(STRATEGIES))
def test_warmup_covers_first_signal(px, key):
    """The router loads ``warmup_sessions`` of history before ``start``; with
    that much data every strategy must already have a signal on the last row."""
    spec = STRATEGIES[key]
    cols = ["XLK", "QQQ"] if key in ("pairs_coint", "pairs_distance") else NINE
    params = validate_params(spec, {}, cols)
    n = warmup_sessions(spec, params)
    p = px[cols].iloc[:n]
    w = spec.fn(StrategyContext(p, None, px["SPY"].iloc[:n]), **params)
    assert np.isfinite(w.iloc[-1].to_numpy()).any(), (key, n)


def test_explanations_use_typographic_dashes():
    from ohcamel_quant.backtest.strategies import STRATEGIES

    for spec in STRATEGIES.values():
        assert "--" not in spec.explanation, spec.key
    assert "—" in STRATEGIES["static_mix"].explanation
