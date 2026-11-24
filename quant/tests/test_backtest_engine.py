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
    # gross 2: the unit above 1 pays costs.yaml's 50 bp financing spread over the risk-free rate (task 2.3)
    fin = 50e-4 / 252 * (float(w.abs().sum()) - 1.0)
    expected = float(w @ r.loc[s]) + (1 - w.sum()) * rf_real.loc[s] - 0.5 * 0.01 / 252 - fin
    assert res.returns_net.loc[s] == pytest.approx(expected, abs=1e-15)
    assert res.financing.loc[s] == pytest.approx(fin, abs=1e-18)


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
    acc, tgt, carried, untraded = members_weights(p, targets, members, execution_lag=1)
    res = run_weights(acc, tgt, EngineConfig(rebalance="monthly", cost_bps=0.0), untraded=untraded)
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


# ------------------------------------------------- fix round 1 (task 3.8): bounds, halts, boundaries
def _cut_xlk(px, last="2020-03-16"):
    p = px[["SPY", "XLK"]].loc["2020-01-02":"2020-06-30"].copy()
    if last is not None:
        p.loc[p.index > pd.Timestamp(last), "XLK"] = np.nan
    members = pd.DataFrame(True, index=p.index, columns=p.columns)
    targets = pd.DataFrame(0.5, index=p.index, columns=p.columns)
    return p, members, targets


def test_the_lower_bound_marks_an_ended_holding_to_zero_at_its_end(px):
    """I2: the headline values a vanished name at its last print; the lower bound marks it to zero on the first
    session it no longer trades. The two differ, that session, by exactly the weight held into it."""
    from ohcamel_quant.backtest.engine import lower_bound_run, members_weights

    p, members, targets = _cut_xlk(px)
    acc, tgt, ended, untraded = members_weights(p, targets, members, execution_lag=1)
    cfg = EngineConfig(rebalance="monthly", cost_bps=0.0)
    res = run_weights(acc, tgt, cfg, untraded=untraded)
    lb = lower_bound_run(acc, tgt, ended, cfg, untraded=untraded)
    i = p.index.get_loc(pd.Timestamp("2020-03-16"))
    head, low = res.returns_gross.to_numpy(), lb.returns_gross.to_numpy()
    assert np.array_equal(head[: i + 1], low[: i + 1])                 # identical up to the last print
    held = res.weights["XLK"].iloc[i]
    assert held > 0.3 and head[i + 1] - low[i + 1] == pytest.approx(held, abs=1e-9)
    assert float(np.prod(1 + low)) < float(np.prod(1 + head))


def test_without_an_ended_holding_the_lower_bound_is_the_headline(px):
    from ohcamel_quant.backtest.engine import lower_bound_run, members_weights

    p, members, targets = _cut_xlk(px, last=None)
    acc, tgt, ended, untraded = members_weights(p, targets, members, execution_lag=1)
    cfg = EngineConfig(rebalance="monthly", cost_bps=0.0)
    assert ended == {}
    res = run_weights(acc, tgt, cfg, untraded=untraded)
    lb = lower_bound_run(acc, tgt, ended, cfg, untraded=untraded)
    assert np.array_equal(res.returns_net.to_numpy(), lb.returns_net.to_numpy())


def test_a_name_with_one_missing_bar_on_an_execution_is_left_untraded(px):
    """I3: a still-listed name with no bar on an execution session (a halt, a vendor gap) is neither sold at a
    price that never printed nor bought: its position is left as it drifted, and traded at the next execution."""
    from ohcamel_quant.backtest.engine import members_weights

    p, members, targets = _cut_xlk(px, last=None)
    targets.loc[:, "XLK"] = np.where(p.index < pd.Timestamp("2020-03-31"), 0.5, 0.2)   # March decision trims XLK
    gap = pd.Timestamp("2020-04-01")                                     # the March decision's execution session
    p.loc[gap, "XLK"] = np.nan
    acc, tgt, ended, untraded = members_weights(p, targets, members, execution_lag=1)
    assert ended == {} and bool(untraded.loc[gap, "XLK"]) and int(untraded.to_numpy().sum()) == 1
    res = run_weights(acc, tgt, EngineConfig(rebalance="monthly", cost_bps=0.0), untraded=untraded)
    assert gap in res.executions
    assert res.trades.loc[gap, "XLK"] == pytest.approx(0.0, abs=1e-15)     # untraded at the gap
    assert res.trades.loc[gap, "SPY"] != 0 and res.weights.loc[gap, "XLK"] > 0.4   # not trimmed, not sold
    nxt = res.executions[res.executions > gap][0]
    assert res.weights.loc[nxt, "XLK"] == pytest.approx(0.2, abs=1e-12)    # traded at the next execution


def test_a_name_whose_series_has_ended_is_still_sold_at_its_last_price(px):
    from ohcamel_quant.backtest.engine import members_weights

    p, members, targets = _cut_xlk(px)
    acc, tgt, ended, untraded = members_weights(p, targets, members, execution_lag=1)
    assert not untraded["XLK"].loc[p.index > pd.Timestamp("2020-03-16")].any()   # ended, not halted
    res = run_weights(acc, tgt, EngineConfig(rebalance="monthly", cost_bps=0.0), untraded=untraded)
    nxt = res.executions[res.executions > pd.Timestamp("2020-03-16")][0]
    assert res.weights.loc[nxt, "XLK"] == 0 and res.trades.loc[nxt, "XLK"] < 0


def test_coverage_boundaries_94_refused_95_runs(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import SingleNameRefused
    from ohcamel_quant.warehouse import accept

    low = H.warehouse(tmp_path, covered=18, uncovered=1, name="low.duckdb")      # 18/19 = 0.947
    H.record_acceptance(low, accept.acceptance_list_sha256())
    with pytest.raises(SingleNameRefused, match="94% of members have prices to their exit, and 95% are needed"):
        _members_run(low, on=True)
    edge = H.warehouse(tmp_path, covered=19, uncovered=1, name="edge.duckdb")    # 19/20 = 0.95
    H.record_acceptance(edge, accept.acceptance_list_sha256())
    res = _members_run(edge, on=True)
    assert any("95% of fixture-xs members have prices to their exit" in n for n in res.notes)


def test_a_members_result_reports_its_lower_bound_beside_the_headline(tmp_path):
    import survivorship_harness as H

    from ohcamel_quant.warehouse import accept

    path = H.warehouse(tmp_path, covered=33, uncovered=1)
    H.record_acceptance(path, accept.acceptance_list_sha256())
    res = _members_run(path, on=True)
    lb = res.lower_bound
    assert lb is not None and set(lb) >= {"total_net", "total_net_lower_bound", "ended_held", "returns_net"}
    assert lb["total_net"] == pytest.approx(lb["total_net_lower_bound"])   # nothing ended inside this window
    note = next(n for n in res.notes if "lower bound" in n)
    assert "not an estimate" in note and f"{lb['total_net']:+.1%}" in note



# ------------------------------------------------- fix round 2 (task 3.8): the bound covers long holdings only
def test_a_short_in_a_name_whose_series_ends_is_not_marked_to_zero_in_the_bound(px):
    """N1: marking a SHORT to zero would credit it the whole position and lift the 'lower bound' above the headline.
    Only long holdings carried past the last bar are marked to zero; a short keeps its last print."""
    from ohcamel_quant.backtest.engine import lower_bound_run, members_weights

    p, members, targets = _cut_xlk(px)
    targets.loc[:, "XLK"] = -0.3
    acc, tgt, ended, untraded = members_weights(p, targets, members, execution_lag=1)
    cfg = EngineConfig(rebalance="monthly", cost_bps=0.0)
    res = run_weights(acc, tgt, cfg, untraded=untraded)
    assert res.weights["XLK"].iloc[p.index.get_loc(pd.Timestamp("2020-03-16"))] < -0.2
    lb = lower_bound_run(acc, tgt, ended, cfg, untraded=untraded, weights=res.weights)
    assert float(np.prod(1 + lb.returns_net)) <= float(np.prod(1 + res.returns_net)) + 1e-15
    assert lb.lower_bound["shorts"] == ["XLK"] and lb.lower_bound["longs"] == []


def test_a_members_result_says_the_bound_does_not_cover_a_short(tmp_path):
    """N1 through run_members_backtest: a test universe of SPY and the committed AAPL slice (written as an ended
    series, 2020-07-31..2020-09-10), the strategy short AAPL. The bound is not above the headline and the note
    names AAPL as a short whose loss has no upper limit."""
    import survivorship_harness as H

    from ohcamel_quant.backtest.engine import run_members_backtest
    from ohcamel_quant.data import eodhd
    from ohcamel_quant.data.base import Provenance
    from ohcamel_quant.warehouse import accept
    from ohcamel_quant.warehouse.db import open_ro, open_rw
    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
    from ohcamel_quant.warehouse.ingest import delisted
    from ohcamel_quant.warehouse.ingest.base import KeyState, insert_frame

    data = __import__("pathlib").Path(__file__).parent / "data"
    load = lambda e: __import__("json").loads((data / f"eodhd_{e}_AAPL.US_2020-07-31_2020-09-10.json").read_bytes())  # noqa: E731
    splits, divs = eodhd.parse_splits(load("splits")), eodhd.parse_dividends(load("div"))
    pay = delisted.DelistedPayload(eodhd.raw_bars(eodhd.parse_eod(load("eod")), splits), eodhd.as_actions(splits, divs),
                                   Provenance("eodhd", "2026-11-20T00:00:00Z"), pd.Timestamp("1990-01-01").date(),
                                   pd.Timestamp("2020-09-10").date())
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        delisted.write_delisted(con, "AAPL", KeyState(), pay)
        con.execute("COMMIT")
        insert_frame(con, 'INSERT INTO universe_membership (universe, ticker, "start", "end", source) SELECT universe, '
                          'ticker, CAST("start" AS DATE), CAST("end" AS DATE), source FROM incoming', pd.DataFrame([
                              {"universe": "t-short", "ticker": "AAPL", "start": "2020-08-03", "end": "2020-09-11",
                               "source": "t"},
                              {"universe": "t-short", "ticker": "SPY", "start": "2016-06-01", "end": None,
                               "source": "t"}]))
    H.record_acceptance(path, accept.acceptance_list_sha256())

    def short_aapl(ctx: StrategyContext) -> pd.DataFrame:
        m = ctx.members.reindex(index=ctx.prices.index, columns=ctx.prices.columns).fillna(False).astype(bool)
        w = pd.DataFrame(0.0, index=ctx.prices.index, columns=ctx.prices.columns)
        w["SPY"] = 0.5
        if "AAPL" in w:
            w["AAPL"] = np.where(m["AAPL"] & ctx.prices["AAPL"].notna(), -0.3, 0.0)
        return w

    with open_ro(path) as con:
        res = run_members_backtest(con, "t-short", short_aapl, {}, pd.Timestamp("2020-08-03").date(),
                                   pd.Timestamp("2020-12-31").date(),
                                   # task 2.3: the cost file holds no single-name tier, so AAPL's cost is set here
                                   EngineConfig(rebalance="monthly", cost_bps=5.0),
                                   settings=_gate_settings(True))
    lb = res.lower_bound
    assert lb["shorts"] == ["AAPL"] and lb["total_net_lower_bound"] <= lb["total_net"] + 1e-12
    note = next(n for n in res.notes if "lower bound" in n)
    assert "long holdings" in note and "AAPL" in note and "no upper limit" in note


# ------------------------------------------------------- fill at the next open (completion task 2.4)
def _equal_weight(ctx: StrategyContext) -> pd.DataFrame:
    return pd.DataFrame(1.0 / ctx.prices.shape[1], index=ctx.prices.index, columns=ctx.prices.columns)


def test_an_open_fill_needs_the_open_prices(px):
    with pytest.raises(ValueError, match="open prices"):
        run_backtest(StrategyContext(px[["SPY", "TLT"]]), _equal_weight, {}, EngineConfig(fill="open"))
    with pytest.raises(ValueError, match="fill"):
        EngineConfig(fill="vwap")


def test_an_open_fill_is_the_close_accounting_on_the_open_panel(market):
    """Decisions on closes; the book filled at the next open and marked open to open, on the real fixture opens put
    on the closes' adjustment (open x adj_close / close)."""
    tk = ["SPY", "TLT"]
    adj, op, cl = (market.prices(tk, field=f).data for f in ("adj_close", "open", "close"))
    idx = adj.index.intersection(op.index).intersection(cl.index)
    closes, opens = adj.loc[idx], (op * adj / cl).loc[idx]
    cfg = EngineConfig(fill="open", rebalance="monthly", cost_bps=5.0)
    got = run_backtest(StrategyContext(closes, opens=opens), _equal_weight, {}, cfg)
    want = run_weights(opens, _equal_weight(StrategyContext(closes)), EngineConfig(rebalance="monthly", cost_bps=5.0))
    pd.testing.assert_series_equal(got.returns_net, want.returns_net)
    assert got.executions.equals(want.executions)
    close_filled = run_backtest(StrategyContext(closes, opens=opens), _equal_weight, {},
                                EngineConfig(rebalance="monthly", cost_bps=5.0))
    assert not np.allclose(close_filled.returns_net.to_numpy(), got.returns_net.to_numpy())
    # an open missing on a session the closes have is refused, never filled from elsewhere
    with pytest.raises(ValueError, match="no open price"):
        run_backtest(StrategyContext(closes, opens=opens.drop(opens.index[100])), _equal_weight, {}, cfg)
