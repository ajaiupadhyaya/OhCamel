"""Engine accounting and no-look-ahead guarantees, on real fixture ETF prices."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest import metrics as M
from ohcamel_quant.backtest.engine import (
    EngineConfig,
    LookAheadError,
    StrategyContext,
    rebalance_mask,
    run_backtest,
    run_weights,
)
from ohcamel_quant.backtest.strategies import STRATEGIES, validate_params

NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def px(market) -> pd.DataFrame:
    return market.prices(NINE).data


@pytest.fixture(scope="module")
def rf_real(market, px) -> pd.Series:
    """A real daily cash return: the 2-year Treasury yield (FRED DGS2 fixture)
    converted with (1 + y)^(1/252) - 1 and carried over FRED holidays."""
    y = market.fred(["DGS2"]).data["DGS2"]
    y = y.reindex(px.index.union(y.index)).sort_index().ffill(limit=5).reindex(px.index)
    return ((1.0 + y / 100.0) ** (1.0 / 252.0) - 1.0).fillna(0.0)


# ------------------------------------------------------------- look-ahead
def peeking(ctx: StrategyContext, horizon: int = 5) -> pd.DataFrame:
    """Cheats: holds assets whose NEXT-week return is positive."""
    fwd = ctx.prices.shift(-horizon) / ctx.prices - 1.0
    return (fwd > 0).astype(float) / ctx.prices.shape[1]


def test_peeking_strategy_is_detected(px):
    ctx = StrategyContext(px)
    with pytest.raises(LookAheadError):
        run_backtest(ctx, peeking, {"horizon": 5}, EngineConfig(rebalance="daily"))


@pytest.mark.parametrize("key", sorted(STRATEGIES))
def test_future_shuffle_leaves_past_weights_unchanged(px, key):
    """Permuting every price AFTER a cut date must not change any weight on or
    before the cut: weights at t are functions of data <= t only."""
    spec = STRATEGIES[key]
    cols = ["XLK", "QQQ"] if key in ("pairs_coint", "pairs_distance") else NINE
    extra = {"safe_asset": "IEF"} if key == "dual_momentum" else {}
    params = validate_params(spec, extra, cols)
    p = px[cols]
    cut = int(len(p) * 0.7)
    rng = np.random.default_rng(0)
    shuffled = p.copy()
    tail = shuffled.iloc[cut + 1:].to_numpy()
    shuffled.iloc[cut + 1:] = tail[rng.permutation(len(tail))]
    mkt = px["SPY"]
    mkt_s = shuffled["SPY"] if "SPY" in cols else mkt
    w1 = spec.fn(StrategyContext(p, None, mkt), **params).iloc[: cut + 1]
    w2 = spec.fn(StrategyContext(shuffled, None, mkt_s), **params).iloc[: cut + 1]
    pd.testing.assert_frame_equal(w1, w2, check_exact=False, rtol=1e-10, atol=1e-12)


def test_execution_is_lagged(px):
    targets = pd.DataFrame(0.0, index=px.index, columns=["SPY"])
    d = px.index[100]
    targets.loc[d:, "SPY"] = 1.0
    res = run_weights(px[["SPY"]], targets, EngineConfig(rebalance="signal", cost_bps=0, execution_lag=2))
    first = res.weights.index[res.weights["SPY"] > 0][0]
    assert first == px.index[102]
    assert res.returns_net.loc[: px.index[102]].abs().max() == 0.0


def test_execution_lag_zero_rejected():
    with pytest.raises(ValueError):
        EngineConfig(execution_lag=0)


# ------------------------------------------------------------- accounting
def test_zero_cost_buy_and_hold_equals_price_ratio(px):
    ctx = StrategyContext(px[["SPY"]])
    res = run_backtest(ctx, STRATEGIES["buy_and_hold"].fn, {},
                       EngineConfig(rebalance="never", cost_bps=0.0, max_gross_leverage=1.0))
    live = res.live(res.returns_net)
    wealth = float(np.prod(1 + live))
    ratio = float(px["SPY"].iloc[-1] / px["SPY"].loc[res.live_start])
    assert wealth == pytest.approx(ratio, rel=1e-12)
    assert res.live_start == px.index[1]


def test_multi_asset_buy_and_hold_drifts(px):
    cols = ["SPY", "TLT", "GLD"]
    res = run_backtest(StrategyContext(px[cols]), STRATEGIES["buy_and_hold"].fn, {},
                       EngineConfig(rebalance="never", cost_bps=0.0, max_gross_leverage=1.0))
    s0 = res.live_start
    growth = px[cols].iloc[-1] / px[cols].loc[s0]
    assert float(np.prod(1 + res.live(res.returns_net))) == pytest.approx(float(growth.mean()), rel=1e-12)
    # drifted weights are value shares
    np.testing.assert_allclose(res.weights.iloc[-1].to_numpy(), (growth / growth.sum()).to_numpy(), rtol=1e-10)
    assert len(res.executions) == 1


def test_costs_reduce_return_by_bps_times_turnover(px):
    spec = STRATEGIES["xsmom"]
    params = validate_params(spec, {}, NINE)
    ctx = StrategyContext(px)
    free = run_backtest(ctx, spec.fn, params, EngineConfig(cost_bps=0.0))
    paid = run_backtest(ctx, spec.fn, params, EngineConfig(cost_bps=25.0))
    # turnover and gross returns do not depend on the cost level
    np.testing.assert_allclose(free.turnover, paid.turnover, atol=1e-14)
    np.testing.assert_allclose(free.returns_gross, paid.returns_gross, atol=1e-14)
    # each session: 1 + net = (1 + gross)(1 - c * turnover), exactly
    expected = (1 + paid.returns_gross) * (1 - 25e-4 * paid.turnover) - 1
    np.testing.assert_allclose(paid.returns_net, expected, atol=1e-15)
    np.testing.assert_allclose(paid.costs, 25e-4 * paid.turnover, atol=1e-16)
    ratio = np.prod(1 + paid.returns_net) / np.prod(1 + free.returns_net)
    assert ratio == pytest.approx(np.prod(1 - 25e-4 * paid.turnover), rel=1e-12)
    assert paid.turnover.sum() > 1.0


def test_cash_earns_real_risk_free(px, rf_real):
    targets = pd.DataFrame(0.0, index=px.index, columns=["SPY"])
    res = run_weights(px[["SPY"]], targets, EngineConfig(cost_bps=0.0, rebalance="daily"), rf=rf_real)
    np.testing.assert_allclose(res.returns_net.iloc[1:], rf_real.iloc[1:], atol=1e-15)


def test_levered_position_pays_financing_and_borrow(px, rf_real):
    targets = pd.DataFrame({"SPY": 1.5, "TLT": -0.5}, index=px.index)
    cfg = EngineConfig(cost_bps=0.0, borrow_bps=100.0, rebalance="daily", max_gross_leverage=3.0)
    res = run_weights(px[["SPY", "TLT"]], targets, cfg, rf=rf_real)
    r = px[["SPY", "TLT"]].pct_change()
    s = px.index[10]
    w = res.weights.shift(1).loc[s]
    expected = float(w @ r.loc[s]) + (1 - w.sum()) * rf_real.loc[s] - 0.5 * 0.01 / 252
    assert res.returns_net.loc[s] == pytest.approx(expected, abs=1e-15)


def test_gross_leverage_cap(px):
    targets = pd.DataFrame(1.0, index=px.index, columns=NINE)
    res = run_weights(px, targets, EngineConfig(max_gross_leverage=1.5, rebalance="monthly"))
    post = res.weights.loc[res.executions]
    assert post.abs().sum(axis=1).max() == pytest.approx(1.5)
    assert any("capped" in n for n in res.notes)


def test_vol_target_overlay_hits_target(px):
    spec = STRATEGIES["static_mix"]
    ctx = StrategyContext(px[["SPY", "TLT", "GLD"]])
    cfg = EngineConfig(cost_bps=0.0, rebalance="weekly", vol_target=0.08, max_gross_leverage=4.0)
    res = run_backtest(ctx, spec.fn, {"weights": {}}, cfg)
    realized = M.ann_vol(res.live(res.returns_net))
    assert 0.05 < realized < 0.12  # ex-ante target, realized within a reasonable band
    assert res.vol_scale.dropna().gt(0).all()
    unmanaged = run_backtest(ctx, spec.fn, {"weights": {}}, EngineConfig(cost_bps=0.0, rebalance="weekly"))
    # the overlay reduces the vol dispersion across years relative to the unmanaged mix
    by_year = res.live(res.returns_net).groupby(lambda d: d.year).std()
    by_year_u = unmanaged.live(unmanaged.returns_net).groupby(lambda d: d.year).std()
    assert by_year.std() < by_year_u.std()


def test_rebalance_masks(px):
    idx = px.index
    m = rebalance_mask(idx, "monthly")
    ends = idx[m]
    assert all(ends.month != idx[np.flatnonzero(m) + 1].month)
    assert m.sum() == len(set(zip(idx.year, idx.month, strict=True))) - 1
    assert rebalance_mask(idx, "weekly").sum() > m.sum() > rebalance_mask(idx, "quarterly").sum()


def test_signal_rebalance_trades_only_on_change(px):
    targets = pd.DataFrame(0.5, index=px.index, columns=["SPY", "TLT"])
    targets.iloc[1000:, 0] = 0.8
    res = run_weights(px[["SPY", "TLT"]], targets, EngineConfig(rebalance="signal"))
    assert len(res.executions) == 2


def test_trade_summary(px):
    spec = STRATEGIES["xsmom"]
    res = run_backtest(StrategyContext(px), spec.fn, validate_params(spec, {}, NINE), EngineConfig())
    ts = res.trade_summary()
    assert ts["executions"] == len(res.executions)
    assert ts["annual_turnover"] > 0
    assert ts["avg_holdings"] == pytest.approx(3, abs=0.2)
    contrib = res.contributions.sum().sum()
    assert contrib == pytest.approx(res.returns_gross.sum(), rel=1e-10)


def test_entry_trade_cost_is_in_live_returns(px):
    """The first execution (cash -> target) pays c * turnover; that cost must be
    part of the live return series and of the trade summary, not dropped."""
    res = run_backtest(StrategyContext(px[["SPY"]]), STRATEGIES["buy_and_hold"].fn, {},
                       EngineConfig(rebalance="never", cost_bps=50.0, max_gross_leverage=1.0))
    assert res.trade_summary()["total_cost_paid"] == pytest.approx(50e-4, rel=1e-12)
    wealth = float(np.prod(1 + res.live(res.returns_net)))
    ratio = float(px["SPY"].iloc[-1] / px["SPY"].loc[res.live_start])
    assert wealth == pytest.approx(ratio * (1 - 50e-4), rel=1e-12)


def test_per_asset_traded_notional_sums_to_turnover(px):
    """Per-asset traded notional is |w* - w~| (target minus DRIFTED weight), so
    it adds up to the engine's turnover; a weight diff across the session would
    also count the drift as trading."""
    spec = STRATEGIES["static_mix"]
    res = run_backtest(StrategyContext(px[["SPY", "TLT"]]), spec.fn, {"weights": {"SPY": 0.6, "TLT": 0.4}},
                       EngineConfig(rebalance="monthly"))
    per = res.trade_summary()["per_asset"]
    assert float(per["traded_notional"].sum()) == pytest.approx(float(res.live(res.turnover).sum()), rel=1e-12)


# ------------------------------------------------- single names over dated membership (completion task 3.8, R5)
def _members_eq(ctx: StrategyContext) -> pd.DataFrame:
    """Equal weight over the index's members on each date that have a price that day (causal)."""
    m = ctx.members.reindex(index=ctx.prices.index, columns=ctx.prices.columns).fillna(False).astype(bool)
    m &= ctx.prices.notna()
    n = m.sum(axis=1).replace(0, np.nan)
    return m.astype(float).div(n, axis=0)


def _gate_settings(on: bool):
    from ohcamel_quant.config import Settings

    return Settings(OHCAMEL_DELISTED_SOURCE="eodhd" if on else "")


def _members_run(path, on: bool):
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import run_members_backtest
    from ohcamel_quant.warehouse.db import open_ro

    with open_ro(path) as con:
        return run_members_backtest(con, H.UNIVERSE, _members_eq, {}, pd.Timestamp("2018-01-02").date(),
                                    pd.Timestamp("2025-12-31").date(), EngineConfig(rebalance="monthly"),
                                    settings=_gate_settings(on))


def test_single_names_are_refused_with_the_switch_off(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import SingleNameRefused
    from ohcamel_quant.warehouse import accept

    path = H.warehouse(tmp_path, covered=33, uncovered=1)
    H.record_acceptance(path, accept.acceptance_list_sha256())
    with pytest.raises(SingleNameRefused, match="OHCAMEL_DELISTED_SOURCE"):
        _members_run(path, on=False)


def test_single_names_are_refused_at_80_percent_coverage(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import SingleNameRefused
    from ohcamel_quant.warehouse import accept

    path = H.warehouse(tmp_path, covered=4, uncovered=1)
    H.record_acceptance(path, accept.acceptance_list_sha256())   # accepted: the coverage alone refuses
    with pytest.raises(SingleNameRefused, match="80% of members have prices to their exit"):
        _members_run(path, on=True)


def test_single_names_are_refused_until_acceptance_is_recorded(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import SingleNameRefused

    path = H.warehouse(tmp_path, covered=33, uncovered=1)
    with pytest.raises(SingleNameRefused, match="tools/accept_delisted.py"):
        _members_run(path, on=True)


def test_single_names_run_at_97_percent_with_acceptance_and_say_so(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.warehouse import accept, readers
    from ohcamel_quant.warehouse.db import open_ro

    path = H.warehouse(tmp_path, covered=33, uncovered=1)
    H.record_acceptance(path, accept.acceptance_list_sha256())
    res = _members_run(path, on=True)
    assert any("97% of fixture-xs members have prices to their exit" in n for n in res.notes)
    assert res.audit["passed"] and len(res.executions) > 50
    # every book held is a subset of members_asof on the decision date that led to it
    lag = res.config.execution_lag
    idx = res.weights.index
    with open_ro(path) as con:
        for d in res.decisions[::7]:
            held = res.weights.iloc[idx.get_loc(d) + lag]
            held = set(held[held.abs() > 1e-12].index)
            assert held <= set(readers.members_asof(con, H.UNIVERSE, d.date()))


def test_a_position_in_a_name_whose_series_ends_is_closed_at_its_last_price(px):
    """A delisted name's last bar is its last trade: the holding carries the move up to that bar, is valued at that
    price afterwards (no return, no interest), and the next execution sells it there. Never dropped. Real SPY and
    XLK closes; XLK's bars are cut after 2020-03-16 as a delisting would cut them."""
    from ohcamel_quant.backtest.engine import members_weights

    p = px[["SPY", "XLK"]].loc["2020-01-02":"2020-06-30"].copy()
    last = pd.Timestamp("2020-03-16")
    p.loc[p.index > last, "XLK"] = np.nan
    members = pd.DataFrame(True, index=p.index, columns=p.columns)
    targets = pd.DataFrame(0.5, index=p.index, columns=p.columns)
    acc, tgt, carried = members_weights(p, targets, members, execution_lag=1)
    res = run_weights(acc, tgt, EngineConfig(rebalance="monthly", cost_bps=0.0))
    i = p.index.get_loc(last)
    w = res.weights["XLK"]
    # the March decision (last session of February) put XLK in the book; its loss to 03-16 is in the returns
    assert w.iloc[i - 1] > 0.3
    r_xlk = p["XLK"].iloc[i] / p["XLK"].iloc[i - 1] - 1.0
    assert res.contributions["XLK"].iloc[i] == pytest.approx(w.iloc[i - 1] * r_xlk, rel=1e-12)
    # after the last bar: no return from XLK; the weight only drifts with SPY until the next execution sells it
    assert (res.contributions["XLK"].iloc[i + 1:] == 0).all()
    nxt = res.executions[res.executions > last][0]
    assert w.loc[:nxt].iloc[-2] > 0 and w.loc[nxt] == 0 and res.trades.loc[nxt, "XLK"] < 0
    assert carried == {"XLK": str(last.date())}
    # nothing is bought after the series ended
    assert (w.loc[nxt:] == 0).all()
