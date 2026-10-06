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


__all__ = [
    "API", "API_VERSION", "ENGINE", "ENV", "MAX_HORIZON", "MAX_PATHS", "MAX_THREADS",
    "BacktestPath", "GarchFitResult", "GarchParams", "SviFitResult",
    "engine_of", "forced", "tail_count",
]
