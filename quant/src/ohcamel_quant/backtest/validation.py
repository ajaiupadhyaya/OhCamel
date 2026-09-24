"""Overfitting and robustness diagnostics for backtests.

* Parameter sweeps over a grid (every combination run through the engine).
* Probability of Backtest Overfitting via Combinatorially Symmetric
  Cross-Validation -- Bailey, D. H., Borwein, J., Lopez de Prado, M. & Zhu, Q. J.
  (2017). The Probability of Backtest Overfitting. *Journal of Computational
  Finance* 20(4), 39-69.
* Deflated Sharpe Ratio with ``n_trials`` = grid size -- Bailey & Lopez de
  Prado (2014), *Journal of Portfolio Management* 40(5).
* Stationary-bootstrap confidence interval for the Sharpe ratio -- Politis, D. N.
  & Romano, J. P. (1994). The Stationary Bootstrap. *JASA* 89(428), with the
  automatic expected block length of Politis, D. N. & White, H. (2004).
  Automatic Block-Length Selection for the Dependent Bootstrap. *Econometric
  Reviews* 23(1) (corrected by Patton, Politis & White 2009).
* Superior Predictive Ability test of the grid against a benchmark --
  Hansen, P. R. (2005). A Test for Superior Predictive Ability. *Journal of
  Business & Economic Statistics* 23(4), via ``arch.bootstrap.SPA``.
* Walk-forward optimization (Pardo, R. 2008, *The Evaluation and Optimization
  of Trading Strategies*, 2nd ed., Wiley, ch. 11).
* Transaction-cost sensitivity and break-even cost.

Bootstraps are NUMERICAL methods applied to the realized (real-data) return
series -- they resample history, they do not simulate markets.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize, stats

from . import metrics as M
from .engine import BacktestResult, EngineConfig, StrategyContext, run_backtest, run_weights
from .strategies import StrategySpec, validate_params

MAX_GRID = 64
MAX_REPS = 1000


# ======================================================================= grid
def param_grid(space: dict[str, list[Any]], max_combos: int = MAX_GRID) -> list[dict[str, Any]]:
    """Cartesian product of ``space`` (ordered as given); refuses grids larger
    than ``max_combos`` so heavy endpoints stay bounded."""
    if not space:
        raise ValueError("grid is empty")
    for k, v in space.items():
        if not isinstance(v, (list, tuple)) or len(v) == 0:
            raise ValueError(f"grid values for {k!r} must be a non-empty list")
    size = math.prod(len(v) for v in space.values())
    if size > max_combos:
        raise ValueError(f"grid has {size} combinations; the cap is {max_combos}")
    keys = list(space)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(space[k] for k in keys))]


@dataclass
class SweepResult:
    combos: list[dict[str, Any]]
    results: list[BacktestResult]
    returns: pd.DataFrame            # net returns, common live window, one column per combo
    weights: list[pd.DataFrame]      # post-trade weights per combo (common window)
    table: pd.DataFrame              # per-combo metrics
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _annual_turnover(res: BacktestResult) -> float:
    """``trade_summary()['annual_turnover']`` without building the full summary."""
    to = res.live(res.turnover)
    return float(to.sum() / (len(to) / M.PERIODS)) if len(to) else 0.0


def sweep(ctx: StrategyContext, spec: StrategySpec, base_params: dict[str, Any],
          space: dict[str, list[Any]], config: EngineConfig, rf: pd.Series | None = None) -> SweepResult:
    """Run every grid combination (``base_params`` overridden by the combo) and
    align the net returns on the window where ALL combinations are live, so
    that metrics are compared on identical sessions."""
    for k in space:
        p = spec.param(k)
        if not p.sweepable:
            raise ValueError(f"parameter {k!r} is not numeric and cannot be swept")
    combos = param_grid(space)
    tickers = list(ctx.prices.columns)
    cfg = EngineConfig(**{**config.__dict__, "audit_points": min(config.audit_points, 1)})
    ok_combos, results, skipped = [], [], []
    for combo in combos:
        try:
            params = validate_params(spec, {**base_params, **combo}, tickers)
            res = run_backtest(ctx, spec.fn, params, cfg)
        except ValueError as e:
            skipped.append({**combo, "reason": str(e)})
            continue
        if res.live_start is None:
            skipped.append({**combo, "reason": "never produced a signal"})
            continue
        ok_combos.append(combo)
        results.append(res)
    if len(results) < 2:
        raise ValueError("fewer than two grid combinations produced a live backtest")
    start = max(r.live_start for r in results)
    rets = pd.DataFrame({i: r.returns_net.loc[r.returns_net.index >= start] for i, r in enumerate(results)})
    if len(rets) < 126:
        raise ValueError(f"only {len(rets)} sessions where every combination is live; widen the window")
    weights = [r.weights.loc[rets.index] for r in results]
    rows = []
    for i, combo in enumerate(ok_combos):
        r = rets[i]
        ex = M.excess(r, rf)
        rows.append({**combo, "sharpe": M.sharpe(ex), "cagr": M.cagr(r), "ann_vol": M.ann_vol(r),
                     "max_drawdown": M.max_drawdown(r), "sortino": M.sortino(ex),
                     "annual_turnover": _annual_turnover(results[i])})
    return SweepResult(ok_combos, results, rets, weights, pd.DataFrame(rows), skipped)


# ======================================================================= CSCV
def _block_moments(m: np.ndarray, s: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = (len(m) // s) * s
    m = m[len(m) - t:]
    blocks = m.reshape(s, t // s, m.shape[1])
    return blocks.sum(axis=1), (blocks ** 2).sum(axis=1), np.full(s, t // s, dtype=float)


def _sharpe_from_sums(s1: np.ndarray, s2: np.ndarray, n: np.ndarray) -> np.ndarray:
    mean = s1 / n[:, None]
    var = (s2 - n[:, None] * mean ** 2) / (n[:, None] - 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(var > 0, mean / np.sqrt(np.maximum(var, 0.0)), np.nan)


def cscv_pbo(returns: pd.DataFrame | np.ndarray, n_partitions: int = 16, bins: int = 20) -> dict[str, Any]:
    """Probability of Backtest Overfitting by CSCV (Bailey et al. 2017, sec. 2).

    1. Split the ``T x N`` matrix of trial returns into ``S`` contiguous,
       equal-size row blocks (leading rows dropped so ``S | T``).
    2. For every one of the ``C(S, S/2)`` combinations ``c`` of ``S/2`` blocks:
       ``J`` = those blocks (in-sample), ``Jbar`` = the rest (out-of-sample).
    3. ``n* = argmax_n SR_J(n)``; ``w_c = rank_Jbar(n*) / (N + 1)`` (rank 1 =
       worst); ``lambda_c = ln(w_c / (1 - w_c))``.
    4. ``PBO = #{lambda_c <= 0} / C(S, S/2)`` -- the probability that the
       in-sample winner is at or below the out-of-sample median.

    Also returned: the logit histogram, the performance-degradation regression
    ``SR_Jbar(n*) = a + b SR_J(n*) + e`` and ``P[SR_Jbar(n*) < 0]``. Sharpe
    ratios here are per-period (non-annualized); moments come from block sums,
    so all combinations are evaluated in one vectorized pass.
    """
    m = np.asarray(returns, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValueError("CSCV needs a T x N matrix with N >= 2 trials")
    if n_partitions < 2 or n_partitions % 2:
        raise ValueError("n_partitions must be an even integer >= 2")
    if np.isnan(m).any():
        raise ValueError("returns matrix contains NaN")
    if len(m) < 4 * n_partitions:
        raise ValueError(f"need at least {4 * n_partitions} rows for {n_partitions} partitions")
    s = n_partitions
    b1, b2, bn = _block_moments(m, s)
    combos = np.array(list(itertools.combinations(range(s), s // 2)))
    mask = np.zeros((len(combos), s))
    mask[np.arange(len(combos))[:, None], combos] = 1.0
    is1, is2, isn = mask @ b1, mask @ b2, mask @ bn
    oos1, oos2, oosn = b1.sum(0) - is1, b2.sum(0) - is2, bn.sum() - isn
    sr_is = _sharpe_from_sums(is1, is2, isn)
    sr_oos = _sharpe_from_sums(oos1, oos2, oosn)
    sr_is_f = np.where(np.isfinite(sr_is), sr_is, -np.inf)
    best = np.argmax(sr_is_f, axis=1)
    rows = np.arange(len(combos))
    oos_f = np.where(np.isfinite(sr_oos), sr_oos, -np.inf)
    ranks = stats.rankdata(oos_f, axis=1, method="average")
    n = m.shape[1]
    w = ranks[rows, best] / (n + 1.0)
    lam = np.log(w / (1.0 - w))
    pbo = float(np.mean(lam <= 0))
    x, y = sr_is[rows, best], sr_oos[rows, best]
    ok = np.isfinite(x) & np.isfinite(y)
    reg: dict[str, float] = {}
    if ok.sum() > 2 and np.ptp(x[ok]) > 0:
        lr = stats.linregress(x[ok], y[ok])
        reg = {"slope": float(lr.slope), "intercept": float(lr.intercept), "r2": float(lr.rvalue ** 2),
               "slope_pvalue": float(lr.pvalue)}
    hist, edges = np.histogram(lam, bins=bins)
    scatter_idx = np.linspace(0, len(rows) - 1, min(400, len(rows))).astype(int)
    ann = math.sqrt(M.PERIODS)
    return {
        "pbo": pbo,
        "n_partitions": s,
        "n_combinations": int(len(combos)),
        "n_trials": int(n),
        "rows_used": int((len(m) // s) * s),
        "logits": {"counts": hist.tolist(), "edges": edges.tolist(), "mean": float(np.mean(lam)),
                   "median": float(np.median(lam))},
        "degradation": {**reg, "prob_oos_loss": float(np.mean(y[ok] < 0)) if ok.any() else float("nan"),
                        "is_sharpe_ann": (x[scatter_idx] * ann).tolist(),
                        "oos_sharpe_ann": (y[scatter_idx] * ann).tolist()},
        "selected_counts": np.bincount(best, minlength=n).tolist(),
    }


# ======================================================================== DSR
def deflated_sharpe_for_grid(returns: pd.DataFrame, selected: int | None = None,
                             rf: pd.Series | None = None) -> dict[str, Any]:
    """DSR of the grid's best (or ``selected``) trial with ``n_trials`` = number
    of columns and ``V[SR]`` = cross-trial variance of per-period Sharpe ratios
    (Bailey & Lopez de Prado 2014, eqs. 1-2)."""
    ex = returns.apply(lambda c: M.excess(c, rf))
    srs = np.array([M.sharpe_per_period(ex[c]) for c in ex.columns])
    if selected is None:
        selected = int(np.nanargmax(srs))
    col = ex.columns[selected]
    sk, ku = M.moments(ex[col])
    var_sr = float(np.nanvar(srs, ddof=1))
    out = M.deflated_sharpe(float(srs[selected]), len(ex), sk, ku, len(srs), var_sr)
    out.update({"selected": selected, "sharpe_ann": float(srs[selected]) * math.sqrt(M.PERIODS),
                "psr_vs_0": M.probabilistic_sharpe(float(srs[selected]), len(ex), sk, ku, 0.0)})
    return out


# ================================================================== bootstrap
def optimal_block(x: np.ndarray) -> float:
    """Politis-White (2004) expected block length for the stationary bootstrap
    (``arch.bootstrap.optimal_block_length``), floored at 1."""
    from arch.bootstrap import optimal_block_length

    b = float(optimal_block_length(np.asarray(x, dtype=float))["stationary"].iloc[0])
    return max(1.0, b) if math.isfinite(b) else 1.0


def bootstrap_sharpe(r: pd.Series, rf: pd.Series | None = None, reps: int = 1000, alpha: float = 0.05,
                     seed: int = 7, bins: int = 30) -> dict[str, Any]:
    """Stationary-bootstrap (Politis & Romano 1994) distribution of the
    annualized Sharpe ratio, expected block length by Politis & White (2004).
    Percentile interval at level ``1 - alpha``; also the bootstrap standard
    error and ``P*[SR <= 0]``. ``reps`` is capped at 1000."""
    from arch.bootstrap import StationaryBootstrap

    reps = int(min(max(reps, 100), MAX_REPS))
    x = M.excess(r.dropna(), rf).to_numpy(dtype=float)
    if len(x) < 60:
        raise ValueError("need at least 60 observations to bootstrap the Sharpe ratio")
    block = optimal_block(x)
    bs = StationaryBootstrap(block, x, seed=seed)

    def f(a: np.ndarray) -> float:
        sd = a.std(ddof=1)
        return a.mean() / sd * math.sqrt(M.PERIODS) if sd > 0 else np.nan

    draws = bs.apply(f, reps=reps)[:, 0]
    draws = draws[np.isfinite(draws)]
    lo, hi = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    hist, edges = np.histogram(draws, bins=bins)
    return {
        "sharpe": f(x), "ci_low": float(lo), "ci_high": float(hi), "level": 1 - alpha,
        "std_error": float(draws.std(ddof=1)), "prob_sharpe_le_0": float(np.mean(draws <= 0)),
        "expected_block_length": block, "reps": reps, "reps_cap": MAX_REPS,
        "histogram": {"counts": hist.tolist(), "edges": edges.tolist()},
        "method": "stationary bootstrap (Politis & Romano 1994), block length Politis & White (2004), percentile CI",
    }


def spa_test(benchmark: pd.Series, models: pd.DataFrame, reps: int = 1000, seed: int = 7) -> dict[str, Any]:
    """Hansen (2005) SPA test. H0: no strategy in ``models`` has a higher
    expected daily return than ``benchmark``. Losses are negative returns;
    stationary bootstrap with block length = median Politis-White length of the
    loss differentials. Returns the lower / consistent / upper p-values
    (consistent = Hansen's recommended statistic)."""
    from arch.bootstrap import SPA

    df = pd.concat([benchmark.rename("__bench__"), models], axis=1).dropna()
    b = -df["__bench__"].to_numpy()
    mdl = -df.drop(columns="__bench__").to_numpy()
    diffs = b[:, None] - mdl
    blocks = [optimal_block(diffs[:, j]) for j in range(diffs.shape[1])]
    block = int(max(1, round(float(np.median(blocks)))))
    reps = int(min(max(reps, 100), MAX_REPS))
    spa = SPA(b, mdl, block_size=block, reps=reps, bootstrap="stationary", studentize=True, seed=seed)
    spa.compute()
    pv = spa.pvalues
    return {"pvalue_consistent": float(pv["consistent"]), "pvalue_lower": float(pv["lower"]),
            "pvalue_upper": float(pv["upper"]), "block_size": block, "reps": reps, "reps_cap": MAX_REPS,
            "n_models": int(mdl.shape[1]), "observations": int(len(df))}


# ================================================================ walk-forward
def _objective(r: pd.Series, rf: pd.Series | None, objective: str) -> float:
    if objective == "sharpe":
        return M.sharpe(M.excess(r, rf))
    if objective == "sortino":
        return M.sortino(M.excess(r, rf))
    if objective == "cagr":
        return M.cagr(r)
    if objective == "calmar":
        return M.calmar(r)
    raise ValueError("objective must be sharpe, sortino, cagr or calmar")


def walk_forward(sw: SweepResult, is_len: int, oos_len: int, anchored: bool = False,
                 objective: str = "sharpe", cost_bps: float = 0.0, rf: pd.Series | None = None,
                 reference: int | None = None, lag: int = 1) -> dict[str, Any]:
    """Walk-forward optimization on the sweep's aligned returns.

    Fold ``k`` switches to combination ``n_k`` at the close of session
    ``start - 1`` and records its returns over the next ``oos_len`` sessions.
    ``n_k = argmax_n objective(IS returns of n)`` where the in-sample block
    (rolling ``is_len`` sessions, or everything so far when ``anchored``) ENDS
    ``lag`` sessions before the switch close -- the decision is taken at the
    close of ``start - 1 - lag`` and executed ``lag`` sessions later, exactly
    like the engine (same-close selection-and-switch would be look-ahead).
    Because every strategy is causal, each combination's return at ``t``
    depends only on data ``<= t``, so the stitched series is genuinely
    out-of-sample. Switching combinations costs
    ``cost_bps * sum|w_new - w_old|`` at the fold boundary (entry from cash on
    the first fold). Walk-forward efficiency = OOS annualized Sharpe / mean
    IS annualized Sharpe of the chosen combinations (Pardo 2008)."""
    rets = sw.returns
    t_len = len(rets)
    if is_len < 63 or oos_len < 21:
        raise ValueError("is_len >= 63 and oos_len >= 21 sessions required")
    if lag < 1:
        raise ValueError("lag must be >= 1 session")
    if is_len + oos_len > t_len:
        raise ValueError(f"is_len + oos_len = {is_len + oos_len} exceeds the {t_len} common sessions")
    c = cost_bps / 1e4
    pieces, folds = [], []
    prev: int | None = None
    start = is_len
    while start < t_len:
        stop = min(start + oos_len, t_len)
        lo = 0 if anchored else start - is_len
        is_block = rets.iloc[lo:start - lag]
        scores = np.array([_objective(is_block[j], rf, objective) for j in rets.columns])
        pick = int(np.nanargmax(np.where(np.isfinite(scores), scores, -np.inf)))
        oos = rets.iloc[start:stop][rets.columns[pick]].copy()
        w_new = sw.weights[pick].iloc[start - 1].to_numpy()
        w_old = np.zeros_like(w_new) if prev is None else sw.weights[prev].iloc[start - 1].to_numpy()
        switch = c * float(np.abs(w_new - w_old).sum()) if prev != pick else 0.0
        oos.iloc[0] = (1.0 + oos.iloc[0]) * (1.0 - switch) - 1.0
        pieces.append(oos)
        folds.append({
            "is_start": rets.index[lo], "is_end": rets.index[start - lag - 1],
            "oos_start": rets.index[start], "oos_end": rets.index[stop - 1],
            "params": sw.combos[pick], "combo": pick,
            "is_score": float(scores[pick]),
            "is_sharpe": M.sharpe(M.excess(is_block[rets.columns[pick]], rf)),
            "oos_sharpe": M.sharpe(M.excess(oos, rf)), "oos_return": M.total_return(oos),
            "switch_cost": switch,
        })
        prev = pick
        start = stop
    stitched = pd.concat(pieces).rename("walk_forward")
    is_mean = float(np.nanmean([f["is_sharpe"] for f in folds]))
    oos_sr = M.sharpe(M.excess(stitched, rf))
    out: dict[str, Any] = {
        "folds": folds, "returns": stitched,
        "oos_summary": M.summary(stitched, rf),
        "walk_forward_efficiency": oos_sr / is_mean if is_mean and math.isfinite(is_mean) and is_mean > 0 else float("nan"),
        "mean_is_sharpe": is_mean, "oos_sharpe": oos_sr,
        "distinct_choices": len({f["combo"] for f in folds}),
    }
    if reference is not None:
        ref = rets[rets.columns[reference]].loc[stitched.index]
        out["reference"] = {"params": sw.combos[reference], "returns": ref, "summary": M.summary(ref, rf)}
    return out


# ======================================================================= costs
def cost_sensitivity(res: BacktestResult, bps_grid: list[float], rf: pd.Series | None = None,
                     benchmark: pd.Series | None = None) -> dict[str, Any]:
    """Re-price a backtest at other proportional cost levels.

    Costs do not change target or drifted weights (both are fractions of NAV),
    so turnover is invariant and the net return at cost ``c`` is exactly::

        1 + net_s(c) = (1 + gross_s - borrow_s) (1 - c * turnover_s)

    Returns metric curves over ``bps_grid`` and the break-even costs at which
    the (excess) Sharpe ratio reaches 0 and at which CAGR falls to the
    benchmark's (root-finding with Brent's method)."""
    g = res.live(res.returns_gross - res.borrow)
    to = res.live(res.turnover)

    def net(bps: float) -> pd.Series:
        return (1.0 + g) * (1.0 - bps / 1e4 * to) - 1.0

    rows = []
    for bps in bps_grid:
        r = net(bps)
        rows.append({"cost_bps": float(bps), "sharpe": M.sharpe(M.excess(r, rf)), "cagr": M.cagr(r),
                     "ann_vol": M.ann_vol(r), "max_drawdown": M.max_drawdown(r),
                     "annual_cost_drag": float((bps / 1e4 * to).sum() / max(len(to), 1) * M.PERIODS)})

    def root(fun: Any) -> tuple[float | None, str | None]:
        """Break-even in [0, 10,000] bps and which case applies: ``'crosses'``
        (a root exists), ``'always_above'`` (the target is still beaten at
        10,000 bps), ``'always_below'`` (not beaten even at zero cost), or
        ``None`` when the metric is undefined at an endpoint."""
        lo, hi = 0.0, 10_000.0
        f_lo, f_hi = fun(lo), fun(hi)
        if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
            return None, None
        if f_lo <= 0:
            return None, "always_below"
        if f_hi > 0:
            return None, "always_above"
        return float(optimize.brentq(fun, lo, hi, xtol=1e-3)), "crosses"

    be_sharpe, case_sharpe = root(lambda b: M.sharpe(M.excess(net(b), rf)))
    be_bench, case_bench = None, None
    if benchmark is not None:
        bc = M.cagr(benchmark.reindex(g.index).dropna())
        be_bench, case_bench = root(lambda b: M.cagr(net(b)) - bc)
    years = max(len(to), 1) / M.PERIODS
    return {"curve": rows, "breakeven_bps_sharpe_zero": be_sharpe, "breakeven_bps_vs_benchmark": be_bench,
            "break_even_case": case_sharpe, "break_even_case_vs_benchmark": case_bench,
            "annual_turnover": float(to.sum() / years)}


def run_config_from(config: EngineConfig, **kw: Any) -> EngineConfig:
    """Copy of ``config`` with fields replaced."""
    return EngineConfig(**{**config.__dict__, **kw})


__all__ = [
    "MAX_GRID", "MAX_REPS", "SweepResult", "bootstrap_sharpe", "cost_sensitivity", "cscv_pbo",
    "deflated_sharpe_for_grid", "optimal_block", "param_grid", "run_weights", "spa_test", "sweep",
    "walk_forward", "run_config_from",
]
