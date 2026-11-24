"""Completion task 2.3: one cost file (research/config/costs.yaml) -- tiered half-spreads, borrow, financing, the
sweep on the walk-forward out-of-sample series, and capacity.

The hand-computable cases price two names at constant, made-up levels so every fee is visible to the last digit: they
test the accounting, and no number from them is reported anywhere. The sweep case runs on the committed real ETF
fixtures."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest import costs
from ohcamel_quant.backtest import metrics as M
from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_weights
from ohcamel_quant.backtest.strategies import get_strategy
from ohcamel_quant.backtest.validation import executed_trades, oos_at_cost, sweep, walk_forward
from ohcamel_quant.config import REPO_ROOT

DAY = 1.0 / 252.0


# ------------------------------------------------------------------ the file
def test_tiers_are_read_from_the_one_file():
    m = costs.load()
    assert costs.COSTS_PATH == REPO_ROOT / "research" / "config" / "costs.yaml"
    assert (m.half_spread_bps("SPY"), m.tier("SPY")) == (1.0, 1)
    assert (m.half_spread_bps("LQD"), m.tier("LQD")) == (3.0, 2)
    assert (m.half_spread_bps("UNG"), m.tier("UNG")) == (8.0, 3)
    assert m.half_spread_bps("spy") == 1.0
    assert m.borrow_bps == 50.0 and m.financing_spread_bps == 50.0
    assert m.sweep == (0.0, 1.0, 3.0, 6.0)
    assert m.participation == 0.01 and m.adv_window == 20


def test_a_ticker_with_no_tier_raises():
    with pytest.raises(costs.NoCostTier, match="AAPL"):
        costs.load().half_spread_bps("AAPL")
    assert issubclass(costs.NoCostTier, ValueError)  # the farm skips such a cell with the reason, never guesses


def test_every_site_etf_has_a_tier_and_a_stated_source():
    uni = json.loads((REPO_ROOT / "quant" / "src" / "ohcamel_quant" / "universes.json").read_text())["universes"]
    etfs = sorted({m["ticker"] for u in uni.values() for m in u["members"]} - {"BTC-USD", "ETH-USD", "^VIX"})
    m = costs.load()
    assert len(etfs) == 42
    assert sorted(m.tickers) == etfs
    assert {m.source(t) for t in etfs} <= {"measured", "provisional"}


def test_a_measured_half_spread_rounds_up_to_its_tier():
    assert [costs.tier_for(x) for x in (0.0, 0.4, 1.0, 1.01, 3.0, 3.2, 8.0)] == [1, 1, 1, 2, 2, 3, 3]
    with pytest.raises(costs.NoCostTier, match="wider than every tier"):
        costs.tier_for(8.5)
    with pytest.raises(ValueError):
        costs.tier_for(float("nan"))


def test_scaled_multiplies_the_half_spread_and_nothing_else():
    m = costs.load()
    s = m.scaled(3.0)
    assert s.half_spread_bps("UNG") == 24.0 and s.half_spread_bps("SPY") == 3.0
    assert s.scaled(2.0).half_spread_bps("SPY") == 6.0
    assert m.scaled(0.0).half_spread_bps("UNG") == 0.0
    assert (s.borrow_bps, s.financing_spread_bps) == (m.borrow_bps, m.financing_spread_bps)
    np.testing.assert_allclose(s.half_spreads(["SPY", "LQD"]), [3.0, 9.0])
    with pytest.raises(ValueError):
        m.scaled(-1.0)


# ------------------------------------------------------------- the accounting
def _flat(n: int = 300) -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-04", periods=n, name="date")
    return pd.DataFrame({"SPY": 100.0, "UNG": 10.0}, index=idx)


def _targets(px: pd.DataFrame, rows: dict[int, dict[str, float]]) -> pd.DataFrame:
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for i, w in rows.items():
        t.iloc[i] = [w.get(c, 0.0) for c in px.columns]
    return t


def test_a_ung_round_trip_pays_8bp_a_leg_on_the_traded_notional():
    """8 bp is UNG's tier-3 half-spread, paid on every fill (spec, "Costs."): a round trip of the whole book crosses it
    twice, about 16 bp of NAV; a round trip of half the book costs 8 bp of NAV."""
    px = _flat()
    cfg = EngineConfig(rebalance="signal")
    whole = run_weights(px, _targets(px, {0: {"UNG": 1.0}, 10: {"UNG": 0.0}}), cfg)
    assert whole.costs.iloc[1] == pytest.approx(8e-4, rel=1e-12)    # in: 8 bp of 1.0 traded
    assert whole.costs.iloc[11] == pytest.approx(8e-4, rel=1e-12)   # out: 8 bp of 1.0 traded
    assert float(np.prod(1 + whole.returns_net)) == pytest.approx((1 - 8e-4) ** 2, rel=1e-12)
    half = run_weights(px, _targets(px, {0: {"UNG": 0.5}, 10: {"UNG": 0.0}}), cfg)
    assert float(half.costs.sum()) == pytest.approx(8e-4, rel=1e-12)  # a round trip of half the book: 8 bp of NAV
    assert float(half.borrow.sum()) == 0.0 and float(half.financing.sum()) == 0.0


def test_a_held_short_pays_50bp_a_year_on_its_notional():
    px = _flat(260)
    res = run_weights(px, _targets(px, {0: {"SPY": -0.5}}), EngineConfig(rebalance="signal"))
    assert res.costs.iloc[1] == pytest.approx(0.5e-4, rel=1e-12)    # SPY is tier 1: 1 bp of 0.5 traded
    assert res.borrow.iloc[2] == pytest.approx(50e-4 * DAY * 0.5, rel=1e-12)
    year = res.borrow.iloc[2:254]                                    # 252 sessions held
    # 0.5 x 50 bp over the year, plus drift: the short's weight grows ~0.25% as the fee shrinks NAV
    assert 50e-4 * 0.5 < float(year.sum()) < 50e-4 * 0.5 * 1.0026
    assert float(res.financing.sum()) == 0.0                         # gross 0.5: nothing above 1 to finance


def test_gross_two_pays_financing_on_the_second_unit_over_the_risk_free_rate():
    px = _flat(30)
    rf = pd.Series(1e-4, index=px.index)
    lev = run_weights(px, _targets(px, {0: {"SPY": 1.0, "UNG": 1.0}}), EngineConfig(rebalance="signal"), rf=rf)
    fin = 50e-4 * DAY
    assert lev.financing.iloc[2] == pytest.approx(fin * 1.0, rel=1e-12)
    # cash = 1 - 2 = -1: the borrowed unit pays the risk-free rate (the cash term) plus the 50 bp spread
    assert lev.returns_net.iloc[2] == pytest.approx(-1e-4 - fin, rel=1e-12)
    one = run_weights(px, _targets(px, {0: {"SPY": 1.0}}), EngineConfig(rebalance="signal"), rf=rf)
    assert float(one.financing.sum()) == 0.0
    assert one.returns_net.iloc[2] == pytest.approx(0.0, abs=1e-18)


def test_the_engine_reads_the_file_and_an_explicit_cost_overrides_it():
    px = _flat(30)
    t = _targets(px, {0: {"SPY": 0.5, "UNG": 0.5}})
    tiered = run_weights(px, t, EngineConfig(rebalance="signal"))
    assert tiered.costs.iloc[1] == pytest.approx(0.5 * 1e-4 + 0.5 * 8e-4, rel=1e-12)
    flat = run_weights(px, t, EngineConfig(rebalance="signal", cost_bps=5.0))
    assert flat.costs.iloc[1] == pytest.approx(5e-4, rel=1e-12)
    three = run_weights(px, t, EngineConfig(rebalance="signal", costs=costs.load().scaled(3.0)))
    assert three.costs.iloc[1] == pytest.approx(3 * tiered.costs.iloc[1], rel=1e-12)
    odd = px.rename(columns={"UNG": "AAPL"})
    with pytest.raises(costs.NoCostTier, match="AAPL"):
        run_weights(odd, t.rename(columns={"UNG": "AAPL"}), EngineConfig(rebalance="signal"))


# ------------------------------------------------------------------ the sweep
@pytest.fixture(scope="module")
def wf_case(market):
    px = market.prices(["SPY", "QQQ", "IWM"]).data
    spec = get_strategy("sma_trend")
    cfg = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=spec.default_max_leverage)
    sw = sweep(StrategyContext(px), spec, {}, {"months": [3, 6, 10, 14]}, cfg)
    spreads = costs.load().half_spreads(px.columns)
    wf = walk_forward(sw, 504, 126, cost_bps=spreads)
    return px, sw, wf, spreads


def test_the_sweep_reprices_the_walk_forward_out_of_sample_series(wf_case):
    px, sw, wf, spreads = wf_case
    oos = wf["returns"]
    at = {m: oos_at_cost(sw, wf, spreads, m) for m in costs.load().sweep}
    pd.testing.assert_series_equal(at[1.0], oos, check_names=False, rtol=1e-12, atol=1e-15)
    assert all(s.index.equals(oos.index) for s in at.values())
    sr = {m: M.sharpe(s) for m, s in at.items()}
    assert sr[0.0] > sr[1.0] > sr[3.0] > sr[6.0]
    # the series the gates judge is not the selected combination's full sample
    full = sw.results[wf["folds"][-1]["combo"]]
    assert M.sharpe(full.live(full.returns_net)) != pytest.approx(sr[1.0], rel=1e-6)
    assert len(full.live(full.returns_net)) > len(oos)


def test_a_flat_bps_level_reprices_the_same_series(wf_case):
    px, sw, wf, spreads = wf_case
    zero = oos_at_cost(sw, wf, spreads, 0.0)
    pd.testing.assert_series_equal(oos_at_cost(sw, wf, spreads, flat_bps=0.0), zero, rtol=1e-12, atol=1e-15)
    assert M.sharpe(oos_at_cost(sw, wf, spreads, flat_bps=30.0)) < M.sharpe(zero)


def test_trades_rebuilt_from_a_slimmed_result_are_the_engines_own(market):
    """Capacity reads the trades along the out-of-sample path, but the sweep drops each result's trades to save
    memory: rebuilt from the weights and the engine's own drift, they must be the trades the engine made."""
    from ohcamel_quant.backtest.engine import run_backtest

    px = market.prices(["SPY", "TLT", "GLD"]).data
    spec = get_strategy("xsmom")
    params = {"lookback": 126, "skip": 21, "top_k": 1, "long_short": True}   # shorts: the drift carries borrow
    res = run_backtest(StrategyContext(px), spec.fn, params, EngineConfig(rebalance="monthly"))
    assert res.trades is not None and float(res.borrow.sum()) > 0
    rebuilt = executed_trades(res, px)
    np.testing.assert_allclose(rebuilt.to_numpy(), res.trades.to_numpy(), atol=1e-12)


# --------------------------------------------------------------- capacity
def test_capacity_of_ten_percent_of_capital_in_a_ten_million_dollar_name_is_one_million():
    idx = pd.bdate_range("2024-01-02", periods=40, name="date")
    w = pd.DataFrame({"SPY": 0.0}, index=idx)
    w.iloc[30:] = 0.10
    dv = pd.DataFrame({"SPY": 10_000_000.0}, index=idx)
    assert costs.capacity(w, dv) == pytest.approx(1_000_000.0, rel=1e-12)
    assert costs.capacity(w, dv, participation=0.02) == pytest.approx(2_000_000.0, rel=1e-12)


def test_capacity_binds_on_the_largest_trade_against_its_own_liquidity():
    idx = pd.bdate_range("2024-01-02", periods=60, name="date")
    w = pd.DataFrame({"SPY": 0.0, "UNG": 0.0}, index=idx)
    w.iloc[30:] = [0.50, 0.05]     # SPY: 0.50 of capital in $1B a day; UNG: 0.05 in $2M a day
    w.iloc[45:] = [0.20, 0.05]     # a later SPY trade of 0.30
    dv = pd.DataFrame({"SPY": 1e9, "UNG": 2e6}, index=idx)
    assert costs.capacity(w, dv) == pytest.approx(0.01 * 2e6 / 0.05, rel=1e-12)


def test_capacity_is_unknown_when_a_trade_has_no_volume_history():
    idx = pd.bdate_range("2024-01-02", periods=40, name="date")
    w = pd.DataFrame({"SPY": 0.0}, index=idx)
    w.iloc[5:] = 0.10              # before 20 sessions of dollar volume exist
    dv = pd.DataFrame({"SPY": 10_000_000.0}, index=idx)
    assert np.isnan(costs.capacity(w, dv))
    assert np.isnan(costs.capacity(pd.DataFrame({"SPY": 0.0}, index=idx), dv))   # nothing traded
