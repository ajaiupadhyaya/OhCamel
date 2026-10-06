"""backtest_weights through the dispatcher, every registered strategy on both engines,
and the causality audit against the Rust path (compute plan A6)."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant import kernels
from ohcamel_quant.backtest.engine import (
    EngineConfig,
    LookAheadError,
    StrategyContext,
    run_backtest,
)
from ohcamel_quant.backtest.strategies import STRATEGIES, get_strategy, validate_params

NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def px(market):
    return market.prices(NINE).data


def test_long_one_asset_hand():
    # prices 100, 110, 99; buy w = 1 at session 1 with 10 bps: net = 0, -0.001, -0.1 (derivation in backtest.rs)
    p = kernels.backtest_weights([[100.0], [110.0], [99.0]], [[1.0]], [1], 10.0, 0.0, np.zeros(3))
    np.testing.assert_allclose(p.net, [0.0, -0.001, -0.1], atol=1e-15)
    assert p.turnover.tolist() == [0.0, 1.0, 0.0] and p.weights.shape == (3, 1) and not p.ruined


def test_no_executions_is_all_cash():
    p = kernels.backtest_weights(np.full((4, 2), 100.0), np.zeros((0, 2)), [], 5.0, 0.0, [0.0, 0.01, 0.0, 0.02])
    assert p.net.tolist() == [0.0, 0.01, 0.0, 0.02] and not p.weights.any()


@pytest.mark.parametrize("idx,msg", [([0], "prior close"), ([5], "prior close"), ([2, 1], "increasing")])
def test_rejects_bad_execution_sessions(idx, msg):
    tw = np.ones((len(idx), 1))
    with pytest.raises(ValueError, match=msg):
        kernels.backtest_weights(np.full((5, 1), 100.0), tw, idx, 0.0, 0.0, np.zeros(5))


def _run(px, key, cfg):
    cols = ["XLK", "QQQ"] if key in ("pairs_coint", "pairs_distance") else NINE
    spec = get_strategy(key)
    p = validate_params(spec, {}, cols)
    config = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=spec.default_max_leverage, **cfg)
    return run_backtest(StrategyContext(px[cols], None, px["SPY"]), spec.fn, p, config)


@pytest.mark.parametrize("cfg", [{}, {"cost_bps": 10.0, "borrow_bps": 75.0, "vol_target": 0.10}], ids=["default", "costly"])
@pytest.mark.parametrize("key", sorted(STRATEGIES))
def test_every_strategy_on_the_engine_it_runs(px, key, cfg):
    res = _run(px, key, cfg)
    assert res.kernel_engine == kernels.engine_of("backtest_weights")
    assert res.audit["passed"]


def test_peeking_strategy_still_raises(px):
    def peek(ctx):
        nxt = ctx.prices.pct_change().shift(-1)            # tomorrow's return: look-ahead
        return (nxt > 0).astype(float) / ctx.prices.shape[1]

    with pytest.raises(LookAheadError):
        run_backtest(StrategyContext(px[NINE]), peek, {}, EngineConfig(rebalance="daily"))
