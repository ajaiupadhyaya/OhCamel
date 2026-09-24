"""Overfitting diagnostics: CSCV/PBO, DSR, stationary bootstrap, SPA, walk-forward, costs."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from ohcamel_quant.backtest import metrics as M
from ohcamel_quant.backtest import validation as V
from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_backtest
from ohcamel_quant.backtest.strategies import get_strategy, validate_params

NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def px(market) -> pd.DataFrame:
    return market.prices(NINE).data


@pytest.fixture(scope="module")
def tsmom_sweep(px) -> V.SweepResult:
    spec = get_strategy("tsmom")
    return V.sweep(StrategyContext(px), spec, {}, {"lookback": [63, 126, 252, 378], "com": [20, 60, 120]},
                   EngineConfig(max_gross_leverage=3.0))


# ------------------------------------------------------------------ grid
def test_param_grid_and_cap():
    g = V.param_grid({"a": [1, 2], "b": [3, 4, 5]})
    assert len(g) == 6 and g[0] == {"a": 1, "b": 3}
    with pytest.raises(ValueError, match="cap"):
        V.param_grid({"a": list(range(9)), "b": list(range(8))})


def test_sweep_aligns_windows(tsmom_sweep):
    sw = tsmom_sweep
    assert sw.returns.shape[1] == 12 == len(sw.combos)
    assert not sw.returns.isna().any().any()
    assert sw.returns.index[0] > max(r.live_start for r in sw.results) - pd.Timedelta(days=1)
    assert set(sw.table.columns) >= {"lookback", "com", "sharpe", "cagr", "max_drawdown"}


def test_sweep_rejects_non_numeric(px):
    with pytest.raises(ValueError, match="not numeric"):
        V.sweep(StrategyContext(px), get_strategy("tsmom"), {}, {"long_only": [True, False]}, EngineConfig())


# ------------------------------------------------------------------ CSCV
def _pbo_bruteforce(m: np.ndarray, s: int) -> float:
    t = (len(m) // s) * s
    m = m[len(m) - t:]
    blocks = np.array_split(np.arange(t), s)
    lam = []
    for combo in itertools.combinations(range(s), s // 2):
        is_rows = np.concatenate([blocks[i] for i in combo])
        oos_rows = np.concatenate([blocks[i] for i in range(s) if i not in combo])
        sr_is = m[is_rows].mean(0) / m[is_rows].std(0, ddof=1)
        sr_oos = m[oos_rows].mean(0) / m[oos_rows].std(0, ddof=1)
        n_star = int(np.argmax(sr_is))
        rank = stats.rankdata(sr_oos)[n_star]
        w = rank / (m.shape[1] + 1)
        lam.append(math.log(w / (1 - w)))
    return float(np.mean(np.array(lam) <= 0))


def test_cscv_matches_bruteforce(etf_returns):
    m = etf_returns.to_numpy()
    for s in (4, 6, 8):
        assert V.cscv_pbo(m, s)["pbo"] == pytest.approx(_pbo_bruteforce(m, s), abs=1e-12)


def test_pbo_of_parameter_grid_in_unit_interval(tsmom_sweep):
    out = V.cscv_pbo(tsmom_sweep.returns, 16)
    assert 0.0 <= out["pbo"] <= 1.0
    assert out["n_combinations"] == math.comb(16, 8)
    assert sum(out["logits"]["counts"]) == out["n_combinations"]
    assert sum(out["selected_counts"]) == out["n_combinations"]
    assert "slope" in out["degradation"] and 0 <= out["degradation"]["prob_oos_loss"] <= 1


def test_pbo_zero_when_one_trial_dominates(etf_returns):
    """XLK plus 20 bp/day beats every other ETF in every half-sample."""
    m = etf_returns.copy()
    m["DOMINANT"] = m["XLK"] + 0.002
    out = V.cscv_pbo(m, 16)
    assert out["pbo"] == pytest.approx(0.0, abs=1e-12)
    assert out["selected_counts"][-1] == out["n_combinations"]


def test_pbo_high_when_trials_have_no_skill(etf_returns):
    """Demeaned real returns: every trial's true mean is 0, so in-sample winners
    are out-of-sample losers (the CSCV halves are complementary)."""
    demeaned = etf_returns - etf_returns.mean()
    assert V.cscv_pbo(demeaned, 16)["pbo"] > 0.5


def test_cscv_input_checks(etf_returns):
    with pytest.raises(ValueError):
        V.cscv_pbo(etf_returns, 15)
    with pytest.raises(ValueError):
        V.cscv_pbo(etf_returns[["SPY"]], 16)


# ------------------------------------------------------------------ DSR
def test_deflated_sharpe_uses_grid(tsmom_sweep):
    d = V.deflated_sharpe_for_grid(tsmom_sweep.returns)
    assert d["n_trials"] == 12
    srs = [M.sharpe_per_period(tsmom_sweep.returns[c]) for c in tsmom_sweep.returns]
    assert d["var_sr"] == pytest.approx(np.var(srs, ddof=1))
    assert d["selected"] == int(np.argmax(srs))
    assert 0 <= d["dsr"] <= d["psr_vs_0"] <= 1        # deflating can only lower confidence


# ------------------------------------------------------------------ bootstrap
def test_bootstrap_sharpe_ci(etf_returns):
    r = etf_returns["SPY"]
    b1 = V.bootstrap_sharpe(r, reps=500, seed=11)
    b2 = V.bootstrap_sharpe(r, reps=500, seed=11)
    assert b1["ci_low"] == b2["ci_low"]                # reproducible
    assert b1["ci_low"] < b1["sharpe"] < b1["ci_high"]
    assert b1["sharpe"] == pytest.approx(M.sharpe(r))
    assert b1["expected_block_length"] >= 1.0
    # SE close to the i.i.d. asymptotic sqrt((1 + SR^2/2)/n) * sqrt(252) (Lo 2002)
    sr = M.sharpe_per_period(r)
    se_iid = math.sqrt((1 + sr * sr / 2) / len(r)) * math.sqrt(252)
    assert 0.5 * se_iid < b1["std_error"] < 2.0 * se_iid
    assert V.bootstrap_sharpe(r, reps=5000)["reps"] == V.MAX_REPS


# ------------------------------------------------------------------ SPA
def test_spa_detects_superior_model(etf_returns):
    spy = etf_returns["SPY"]
    better = pd.DataFrame({"a": spy + 0.001, "b": spy - 0.001})
    worse = pd.DataFrame({"a": spy - 0.0005, "b": spy - 0.001})
    assert V.spa_test(spy, better, reps=500)["pvalue_consistent"] < 0.05
    assert V.spa_test(spy, worse, reps=500)["pvalue_consistent"] > 0.5


def test_spa_on_grid(tsmom_sweep, px):
    bench = px["SPY"].pct_change().loc[tsmom_sweep.returns.index]
    out = V.spa_test(bench, tsmom_sweep.returns, reps=300)
    assert 0 <= out["pvalue_lower"] <= out["pvalue_consistent"] <= out["pvalue_upper"] <= 1
    assert out["n_models"] == 12


# ------------------------------------------------------------------ walk-forward
def test_walk_forward_is_out_of_sample(tsmom_sweep):
    wf = V.walk_forward(tsmom_sweep, 504, 126, anchored=False, cost_bps=5.0, reference=0)
    folds = wf["folds"]
    idx = tsmom_sweep.returns.index
    assert len(wf["returns"]) == len(idx) - 504
    for f in folds:
        assert f["is_end"] < f["oos_start"]
    for a, b in itertools.pairwise(folds):
        assert a["oos_end"] < b["oos_start"]
    # without switching, stitched returns equal the chosen combo's own returns
    f = folds[1]
    seg = wf["returns"].loc[f["oos_start"]:f["oos_end"]].iloc[1:]
    own = tsmom_sweep.returns[f["combo"]].loc[f["oos_start"]:f["oos_end"]].iloc[1:]
    np.testing.assert_allclose(seg, own)
    assert "reference" in wf and math.isfinite(wf["oos_sharpe"])


def test_walk_forward_anchored_is_windows(tsmom_sweep):
    wf = V.walk_forward(tsmom_sweep, 504, 252, anchored=True)
    first_is = {f["is_start"] for f in wf["folds"]}
    assert first_is == {tsmom_sweep.returns.index[0]}
    with pytest.raises(ValueError):
        V.walk_forward(tsmom_sweep, 5000, 252)


# ------------------------------------------------------------------ costs
def test_cost_sensitivity_reproduces_engine(px):
    spec = get_strategy("xsmom")
    params = validate_params(spec, {}, NINE)
    res = run_backtest(StrategyContext(px), spec.fn, params, EngineConfig(cost_bps=10.0))
    cs = V.cost_sensitivity(res, [0.0, 10.0, 50.0])
    row10 = next(r for r in cs["curve"] if r["cost_bps"] == 10.0)
    assert row10["cagr"] == pytest.approx(M.cagr(res.live(res.returns_net)), rel=1e-12)
    sharpes = [r["sharpe"] for r in cs["curve"]]
    assert sharpes == sorted(sharpes, reverse=True)
    be = cs["breakeven_bps_sharpe_zero"]
    if be is not None:
        at_be = V.cost_sensitivity(res, [be])["curve"][0]["sharpe"]
        assert abs(at_be) < 1e-3
    assert cs["break_even_case"] == ("crosses" if be is not None else
                                     ("always_below" if sharpes[0] <= 0 else "always_above"))
    assert cs["break_even_case_vs_benchmark"] is None       # no benchmark passed


def test_break_even_cases_all_three(px):
    """Scaling gross returns moves the Sharpe-zero break-even: a large negative
    drift is below 0 at zero cost, a huge positive one survives 10,000 bps."""
    import dataclasses

    spec = get_strategy("xsmom")
    params = validate_params(spec, {}, NINE)
    res = run_backtest(StrategyContext(px), spec.fn, params, EngineConfig(cost_bps=0.0))
    live = res.returns_gross.index >= res.live_start
    down = dataclasses.replace(res, returns_gross=res.returns_gross.where(~live, 0.1 * res.returns_gross - 0.005))
    assert V.cost_sensitivity(down, [0.0])["break_even_case"] == "always_below"
    to = res.turnover.where(~live, 1e-9)
    up = dataclasses.replace(res, returns_gross=res.returns_gross.where(
        ~live, 0.01 + 0.001 * np.sign(res.returns_gross)), turnover=to)
    assert V.cost_sensitivity(up, [0.0])["break_even_case"] == "always_above"
    cs = V.cost_sensitivity(res, [0.0])
    if cs["breakeven_bps_sharpe_zero"] is not None:
        assert cs["break_even_case"] == "crosses"


def test_walk_forward_selection_does_not_use_the_switch_session(tsmom_sweep):
    """The combination held from the close of session ``start - 1`` must be
    chosen with information available one execution lag earlier: a return
    printed AT the switch close cannot influence the choice (same-close
    execution would be look-ahead, as in the engine)."""
    import dataclasses

    is_len = 504
    base = V.walk_forward(tsmom_sweep, is_len, 126, objective="cagr")
    pick0 = base["folds"][0]["combo"]
    other = (pick0 + 1) % tsmom_sweep.returns.shape[1]
    rets = tsmom_sweep.returns.copy()
    rets.iloc[is_len - 1, other] = 0.9                 # huge gain on the switch session itself
    tampered = dataclasses.replace(tsmom_sweep, returns=rets)
    wf = V.walk_forward(tampered, is_len, 126, objective="cagr")
    assert wf["folds"][0]["combo"] == pick0
    assert wf["folds"][0]["is_end"] < rets.index[is_len - 1]
