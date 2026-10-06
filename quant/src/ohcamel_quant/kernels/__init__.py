"""Kernel dispatcher: ``ohcamel_quant.kernels`` (compute plan contract II.4).

Every heavy loop of the platform has two implementations with one signature:
a Rust one in the ``ohcamel_kernels`` extension (``native/kernels``, PyO3 +
maturin, rayon) and a NumPy reference in :mod:`.reference`. This module picks
one per process:

* ``OHCAMEL_QUANT_KERNELS=python``: the reference, always.
* ``OHCAMEL_QUANT_KERNELS=rust``: Rust, or ImportError at import. The
  production image sets this, so a missing or stale wheel fails the image
  build and the service start instead of silently running slower code.
* unset (development; CI's plain ``quant`` job): Rust when the wheel imports,
  else the reference, with a WARNING in the log.

Callers record the engine that actually ran (``engine_of(name)``) as
``engine`` in their result's provenance. Analytics stay pure: no I/O here.

Determinism (II.4): the same ``(seed, threads)`` gives bit-identical output on
one engine. Rust draws from ChaCha20 (stream = worker index), the reference
from PCG64(seed), so random kernels agree across engines in distribution and
deterministic ones to 1e-10 relative.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
from collections.abc import Iterator
from types import ModuleType
from typing import Any, Literal

import numpy as np

from . import reference
from .types import BacktestPath, GarchFitResult, GarchParams, SviFitResult

log = logging.getLogger(__name__)

ENV = "OHCAMEL_QUANT_KERNELS"
#: The wheel API this dispatcher speaks (native/kernels/src/lib.rs API_VERSION).
API_VERSION = 1
#: Contract II.4's functions, in its order.
API: tuple[str, ...] = (
    "fhs_paths", "copula_t_paths", "var_es_from_pnl", "stationary_bootstrap_means", "cscv_pbo",
    "garch_nll", "garch_fit", "backtest_weights", "svi_fit", "realized_vol_minute",
)
MAX_THREADS = 64
#: Largest simulation output accepted (80 MB of float64); Rust refuses more too.
MAX_PATHS = 10_000_000
#: Longest simulation horizon in sessions (ten years).
MAX_HORIZON = 2_520

Engine = Literal["rust", "python"]


def _load() -> tuple[ModuleType | None, Engine]:
    mode = (os.environ.get(ENV) or "auto").strip().lower()
    if mode not in ("auto", "rust", "python"):
        raise ValueError(f"{ENV} must be 'rust', 'python' or unset; got {mode!r}")
    if mode == "python":
        log.info("%s=python: kernels run on the NumPy reference", ENV)
        return None, "python"
    try:
        import ohcamel_kernels as rust
    except ImportError as e:
        if mode == "rust":
            raise ImportError(f"{ENV}=rust but the ohcamel_kernels wheel is not importable: {e}") from e
        log.warning("ohcamel_kernels is not importable (%s); kernels run on the NumPy reference", e)
        return None, "python"
    got = getattr(rust, "API_VERSION", None)
    if got != API_VERSION:
        msg = f"ohcamel_kernels API_VERSION {got!r} != {API_VERSION}: rebuild the wheel (make kernels-dev)"
        if mode == "rust":
            raise ImportError(msg)
        log.warning("%s; kernels run on the NumPy reference", msg)
        return None, "python"
    log.info("kernels run on Rust (ohcamel_kernels %s)", getattr(rust, "__version__", "?"))
    return rust, "rust"


_RUST, ENGINE = _load()
_forced: Engine | None = None
_warned: set[str] = set()


def engine_of(name: str) -> Engine:
    """The engine kernel ``name`` runs on in this process: what callers record as ``engine``."""
    eng = _forced or ENGINE
    if eng == "rust" and not hasattr(_RUST, name):
        if name not in _warned:
            log.warning("ohcamel_kernels has no %s; that kernel runs on the NumPy reference", name)
            _warned.add(name)
        return "python"
    return eng


def _impl(name: str) -> Any:
    return getattr(_RUST, name) if engine_of(name) == "rust" else getattr(reference, name)


@contextlib.contextmanager
def forced(engine: Engine) -> Iterator[None]:
    """Tests and benchmarks only: run every kernel on ``engine`` inside the block."""
    global _forced
    if engine not in ("rust", "python"):
        raise ValueError(f"engine must be 'rust' or 'python'; got {engine!r}")
    if engine == "rust" and _RUST is None:
        raise RuntimeError("ohcamel_kernels is not importable: cannot force the Rust engine")
    prev, _forced = _forced, engine
    try:
        yield
    finally:
        _forced = prev


# ------------------------------------------------------------------ coercion
def _arr(x: Any, name: str, ndim: int, dtype: Any = np.float64) -> np.ndarray:
    a = np.ascontiguousarray(np.asarray(x, dtype=dtype))
    if a.ndim != ndim:
        raise ValueError(f"{name} must be {ndim}-D; got shape {a.shape}")
    return a


def _matrix(x: Any, name: str) -> np.ndarray:
    """1-D -> one column; 2-D as is; C-contiguous float64."""
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    if a.ndim != 2:
        raise ValueError(f"{name} must be 1-D or 2-D; got shape {a.shape}")
    return np.ascontiguousarray(a)


def _threads(threads: Any) -> int:
    t = int(threads)
    if not 1 <= t <= MAX_THREADS:
        raise ValueError(f"threads must be in 1..{MAX_THREADS}; got {threads!r}")
    return t


def _seed(seed: Any) -> int:
    s = int(seed)
    if not 0 <= s < 2**64:
        raise ValueError(f"seed must be in [0, 2**64); got {seed!r}")
    return s


def _alpha(alpha: Any) -> float:
    a = float(alpha)
    if not 0.5 < a < 1.0:
        raise ValueError(f"confidence level alpha must be in (0.5, 1); got {alpha!r}")
    return a


def _sim(horizon: Any, n_paths: Any, seed: Any, threads: Any) -> tuple[int, int, int, int]:
    h, n = int(horizon), int(n_paths)
    if not 1 <= h <= MAX_HORIZON:
        raise ValueError(f"horizon must be in 1..{MAX_HORIZON}; got {horizon!r}")
    if not 1 <= n <= MAX_PATHS:
        raise ValueError(f"n_paths must be in 1..{MAX_PATHS}; got {n_paths!r}")
    return h, n, _seed(seed), _threads(threads)


# ------------------------------------------------------------------ kernels
def tail_count(n: int, alpha: float) -> int:
    """Tail observations at ``alpha``: risk.core.tail_count's rule, shared by every tail kernel."""
    n = int(n)
    if n < 1:
        raise ValueError(f"n must be >= 1; got {n}")
    return int(_impl("tail_count")(n, _alpha(alpha)))


def var_es_from_pnl(pnl: Any, alpha: float) -> tuple[float, float]:
    """VaR and ES (positive loss fractions) of a P&L sample at ``alpha``: losses
    ``L = -pnl`` with non-finite values dropped, ``k = tail_count(n, alpha)``, VaR
    the k-th largest loss, ES the mean of the k largest (risk.core.empirical_var_es);
    ``(nan, nan)`` for an empty sample."""
    v, e = _impl("var_es_from_pnl")(_arr(pnl, "pnl", 1), _alpha(alpha))
    return float(v), float(e)


def fhs_paths(std_resid: Any, sigma_path_params: GarchParams, weights: Any, horizon: int, n_paths: int,
              seed: int, threads: int) -> np.ndarray:
    """``(n_paths,)`` h-day portfolio P&L (fraction) by filtered historical simulation.

    ``std_resid`` is (n,) or (n, k) standardized residuals, rows drawn jointly;
    ``sigma_path_params`` the per-asset GJR parameters (percent units) and
    sigma^2_{T+1}; ``weights`` the daily-rebalanced book. See native/kernels/src/fhs.rs.
    """
    z = _matrix(std_resid, "std_resid")
    if not np.all(np.isfinite(z)):
        raise ValueError("std_resid must be finite (drop the NaN rows first)")
    p = sigma_path_params.arrays()
    w = _arr(weights, "weights", 1)
    k = z.shape[1]
    if w.size != k or any(a.size != k for a in p):
        raise ValueError(f"std_resid has {k} columns; weights and every GarchParams field need {k} entries")
    h, n, s, t = _sim(horizon, n_paths, seed, threads)
    if engine_of("fhs_paths") == "rust":
        return np.asarray(_RUST.fhs_paths(z, *p, w, h, n, s, t))
    return reference.fhs_paths(z, sigma_path_params, w, h, n, s, t)


def copula_t_paths(returns: Any, weights: Any, nu: float, horizon: int, n_paths: int, seed: int,
                   threads: int) -> np.ndarray:
    """``(n_paths,)`` h-day portfolio P&L (fraction) from a Student-t copula (Kendall
    sin-transform correlation, ``nu`` degrees of freedom) over the empirical
    marginals of ``returns`` (n, k). See native/kernels/src/copula.rs."""
    x = _matrix(returns, "returns")
    if not np.all(np.isfinite(x)):
        raise ValueError("returns must be finite")
    w = _arr(weights, "weights", 1)
    if w.size != x.shape[1]:
        raise ValueError(f"returns has {x.shape[1]} columns; weights has {w.size}")
    nu = float(nu)
    if not (math.isfinite(nu) and nu > 0):
        raise ValueError(f"nu must be positive and finite; got {nu}")
    h, n, s, t = _sim(horizon, n_paths, seed, threads)
    return np.asarray(_impl("copula_t_paths")(x, w, nu, h, n, s, t))


def _kendall_corr(returns: Any, threads: int = 1) -> np.ndarray:
    """The copula's k x k correlation (exposed for the parity tests)."""
    x = _matrix(returns, "returns")
    k = x.shape[1]
    return np.asarray(_impl("_kendall_corr")(x, _threads(threads))).reshape(k, k)


def stationary_bootstrap_means(x: Any, mean_block: float, reps: int, seed: int, threads: int) -> np.ndarray:
    """Means of ``reps`` stationary-bootstrap resamples (Politis & Romano 1994,
    expected block length ``mean_block``). ``x`` (n,) gives (reps,); ``x`` (n, k)
    gives (reps, k), every column resampled with the SAME indices (the stream
    never depends on the values)."""
    one_d = np.ndim(x) == 1
    m = _matrix(x, "x")
    n, k = m.shape
    if n < 2:
        raise ValueError(f"x needs n >= 2 observations; got {n}")
    if not np.all(np.isfinite(m)):
        raise ValueError("x must be finite")
    b = float(mean_block)
    if not (math.isfinite(b) and b >= 1.0):
        raise ValueError(f"mean_block must be >= 1; got {mean_block!r}")
    reps = int(reps)
    if not 1 <= reps * k <= MAX_PATHS:
        raise ValueError(f"reps x columns must be in 1..{MAX_PATHS}; got reps={reps}")
    out = np.asarray(_impl("stationary_bootstrap_means")(m, b, reps, _seed(seed), _threads(threads)))
    out = out.reshape(reps, k)
    return out[:, 0].copy() if one_d else out


MAX_PARTITIONS = 20


def cscv_pbo(perf: Any, n_partitions: int, threads: int) -> dict:
    """CSCV probability of backtest overfitting over a T x N trial-returns matrix
    (Bailey et al. 2017). Returns ``pbo``, ``logits`` (one per C(S, S/2) combination,
    lexicographic), ``n_combinations``, and per combination the in-sample winner
    ``selected`` with its ``is_sharpe`` / ``oos_sharpe`` (per period). S <= 20."""
    m = _arr(perf, "perf", 2)
    s, t = int(n_partitions), _threads(threads)
    if engine_of("cscv_pbo") == "rust":
        pbo, logits, nc, sel, is_sr, oos_sr = _RUST.cscv_pbo(m, s, t)
        return {"pbo": float(pbo), "logits": np.asarray(logits), "n_combinations": int(nc),
                "selected": np.asarray(sel, dtype=np.int64), "is_sharpe": np.asarray(is_sr),
                "oos_sharpe": np.asarray(oos_sr)}
    return reference.cscv_pbo(m, s, t)


GARCH_KINDS = ("garch", "gjr", "egarch")
_N_PARAMS = {"garch": 5, "gjr": 6, "egarch": 6}


def _kind(kind: str) -> str:
    if kind not in GARCH_KINDS:
        raise ValueError(f"unknown GARCH kind {kind!r}; use one of {GARCH_KINDS}")
    return kind


def garch_nll(params: Any, r: Any, kind: str) -> float:
    """Total Student-t negative log-likelihood of (GJR-/E)GARCH(1,1) at ``params``
    (garch [mu, omega, alpha, beta, nu]; gjr/egarch [mu, omega, alpha, gamma, beta, nu])
    over ``r`` in the parameters' units (percent returns, as arch fits); ``inf`` where
    the parameters are infeasible. Initialisation is arch's backcast."""
    kind = _kind(kind)
    p, y = _arr(params, "params", 1), _arr(r, "r", 1)
    if p.size != _N_PARAMS[kind]:
        raise ValueError(f"{kind} takes {_N_PARAMS[kind]} parameters; got {p.size}")
    if y.size < 2 or not np.all(np.isfinite(y)):
        raise ValueError("r must be at least 2 finite returns")
    return float(_impl("garch_nll")(p, y, kind))


def garch_fit(r: Any, kind: str, x0: Any | None) -> GarchFitResult:
    """Maximum-likelihood (GJR-/E)GARCH(1,1)-t on ``r`` (percent returns; >= 100),
    warm-started from ``x0`` when given (compute plan A5)."""
    kind = _kind(kind)
    y = _arr(r, "r", 1)
    if y.size < 100:
        raise ValueError(f"GARCH needs at least 100 observations; got {y.size}")
    if not np.all(np.isfinite(y)):
        raise ValueError("r must be finite")
    x = None if x0 is None else _arr(x0, "x0", 1)
    if x is not None and x.size != _N_PARAMS[kind]:
        raise ValueError(f"x0 needs {_N_PARAMS[kind]} parameters; got {x.size}")
    if engine_of("garch_fit") == "rust":
        params, nll, conv, nit, s2, nxt, z = _RUST.garch_fit(y, kind, x)
        return GarchFitResult(kind, np.asarray(params), float(nll), bool(conv), int(nit), np.asarray(s2),
                              float(nxt), np.asarray(z))
    return reference.garch_fit(y, kind, x)


def backtest_weights(prices: Any, target_w: Any, decision_idx: Any, cost_bps: float, borrow_bps: float,
                     rf: Any) -> BacktestPath:
    """The engine's accounting loop (backtest/engine.py docstring): ``prices`` n_t x n_a
    adjusted closes, ``target_w`` one row per execution, ``decision_idx[j]`` the
    session at whose close row j is executed (decision + execution lag), costs in
    bps, ``rf`` daily decimal cash returns."""
    px = _arr(prices, "prices", 2)
    n_t, n_a = px.shape
    tw = np.ascontiguousarray(np.asarray(target_w, dtype=np.float64).reshape(-1, n_a))
    idx = np.ascontiguousarray(np.asarray(decision_idx, dtype=np.int64).reshape(-1))
    rfa = _arr(rf, "rf", 1)
    if tw.shape[0] != idx.size:
        raise ValueError(f"target_w has {tw.shape[0]} rows for {idx.size} executions")
    if rfa.size != n_t:
        raise ValueError(f"rf has {rfa.size} values for {n_t} sessions")
    if idx.size and (idx[0] < 1 or idx[-1] >= n_t or np.any(np.diff(idx) <= 0)):
        raise ValueError("decision_idx must be strictly increasing sessions in 1..n_t-1 "
                         "(an execution needs a prior close)")
    if not np.all(np.isfinite(px)) or np.any(px <= 0):
        raise ValueError("prices must be finite and positive")
    cost, borrow = float(cost_bps), float(borrow_bps)
    if not (cost >= 0 and borrow >= 0 and math.isfinite(cost) and math.isfinite(borrow)):
        raise ValueError("costs must be finite and non-negative")
    if engine_of("backtest_weights") == "rust":
        g, net, w, to, tr, c, b, ruined = _RUST.backtest_weights(px, tw, idx, cost, borrow, rfa)
        return BacktestPath(np.asarray(g), np.asarray(net), np.asarray(w).reshape(n_t, n_a), np.asarray(to),
                            np.asarray(tr).reshape(n_t, n_a), np.asarray(c), np.asarray(b), bool(ruined))
    return reference.backtest_weights(px, tw, idx, cost, borrow, rfa)


def svi_fit(k: Any, w: Any, weights: Any) -> SviFitResult:
    """Raw SVI calibration to total variance ``w`` at log-moneyness ``k`` with
    weights (options/svi.calibrate's algorithm). >= 5 points; w and weights > 0."""
    ka, wa, wt = _arr(k, "k", 1), _arr(w, "w", 1), _arr(weights, "weights", 1)
    if not ka.size == wa.size == wt.size or ka.size < 5:
        raise ValueError(f"SVI needs at least 5 quotes of equal-length k, w and weights; got {ka.size}")
    if not (np.all(np.isfinite(ka)) and np.all(np.isfinite(wa)) and np.all(np.isfinite(wt))
            and np.all(wa > 0) and np.all(wt > 0)):
        raise ValueError("k, w and weights must be finite, with w and weights positive")
    if engine_of("svi_fit") == "rust":
        a, b, rho, m, s, sse, con = _RUST.svi_fit(ka, wa, wt)
        return SviFitResult(float(a), float(b), float(rho), float(m), float(s), float(sse), bool(con))
    return reference.svi_fit(ka, wa, wt)


def realized_vol_minute(ts_ns: Any, px: Any, session_bounds: Any) -> np.ndarray:
    """Daily realized VARIANCE (sum of squared intraday log returns; Andersen &
    Bollerslev 1998) per session [open, close) in nanosecond epoch time; NaN for a
    session with fewer than two prices. ``np.sqrt`` it for realized volatility."""
    ts = np.ascontiguousarray(np.asarray(ts_ns, dtype=np.int64).reshape(-1))
    p = _arr(px, "px", 1)
    sb = np.ascontiguousarray(np.asarray(session_bounds, dtype=np.int64).reshape(-1, 2))
    if ts.size != p.size:
        raise ValueError("ts_ns and px must have one length")
    if np.any(np.diff(ts) < 0):
        raise ValueError("ts_ns must be non-decreasing")
    if not np.all(np.isfinite(p)) or np.any(p <= 0):
        raise ValueError("px must be finite and positive")
    if np.any(sb[:, 0] >= sb[:, 1]) or np.any(sb[1:, 0] < sb[:-1, 1]):
        raise ValueError("sessions must be non-empty, ordered and non-overlapping [open, close)")
    return np.asarray(_impl("realized_vol_minute")(ts, p, sb))


__all__ = [
    "API", "API_VERSION", "ENGINE", "ENV", "GARCH_KINDS", "MAX_HORIZON", "MAX_PARTITIONS", "MAX_PATHS", "MAX_THREADS",
    "BacktestPath", "GarchFitResult", "GarchParams", "SviFitResult",
    "backtest_weights", "copula_t_paths", "cscv_pbo", "engine_of", "fhs_paths", "forced", "garch_fit", "garch_nll", "realized_vol_minute", "stationary_bootstrap_means",
    "svi_fit", "tail_count", "var_es_from_pnl",
]
