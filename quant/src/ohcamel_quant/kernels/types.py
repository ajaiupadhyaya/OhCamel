"""Result and parameter types of the kernel API (contract II.4). Both engines
return these, so callers never see which one ran except through ``engine_of``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class GarchParams:
    """Per-asset (GJR-)GARCH(1,1) parameters for ``fhs_paths``, in PERCENT units
    (as fitted on ``100 r``): ``mu``, ``omega``, ``alpha``, ``gamma`` (0 for plain
    GARCH), ``beta``, and ``sigma2_next`` = sigma^2_{T+1} (percent^2), the first
    simulated day's variance. Each field has one entry per asset."""

    mu: np.ndarray
    omega: np.ndarray
    alpha: np.ndarray
    gamma: np.ndarray
    beta: np.ndarray
    sigma2_next: np.ndarray

    @classmethod
    def from_fits(cls, fits: Sequence[Any]) -> GarchParams:
        """From risk.garch.GarchFit or FastGarch objects, one per asset."""
        names = ("mu", "omega", "alpha", "gamma", "beta", "next_variance_pct")
        return cls(*(np.array([float(getattr(f, n)) for f in fits]) for n in names))

    def arrays(self) -> tuple[np.ndarray, ...]:
        """The six fields as C-contiguous float64 vectors of one length, validated."""
        out = tuple(np.ascontiguousarray(np.atleast_1d(np.asarray(v, dtype=np.float64)))
                    for v in (self.mu, self.omega, self.alpha, self.gamma, self.beta, self.sigma2_next))
        k = out[0].size
        if any(a.ndim != 1 or a.size != k for a in out):
            raise ValueError("GarchParams fields must be 1-D and of one length (one entry per asset)")
        if not all(np.all(np.isfinite(a)) for a in out):
            raise ValueError("GarchParams must be finite")
        if np.any(out[5] <= 0):
            raise ValueError("GarchParams.sigma2_next must be positive")
        return out


@dataclass(frozen=True)
class GarchFitResult:
    """``garch_fit``'s answer, in the units of the ``r`` it was given. ``params``
    is laid out as garch [mu, omega, alpha, beta, nu], gjr and egarch
    [mu, omega, alpha, gamma, beta, nu]; ``nll`` is the TOTAL negative
    log-likelihood; ``sigma2`` the in-sample conditional variances;
    ``next_variance`` sigma^2_{T+1}; ``std_resid`` (r - mu) / sigma."""

    kind: str
    params: np.ndarray
    nll: float
    converged: bool
    iterations: int
    sigma2: np.ndarray
    next_variance: float
    std_resid: np.ndarray


@dataclass(frozen=True)
class BacktestPath:
    """``backtest_weights``'s answer: per-session gross and net returns, post-trade
    weights (n_t x n_a), turnover, signed trades (n_t x n_a), cost and borrow
    fractions of NAV, and whether the book was wiped out."""

    gross: np.ndarray
    net: np.ndarray
    weights: np.ndarray
    turnover: np.ndarray
    trades: np.ndarray
    costs: np.ndarray
    borrow: np.ndarray
    ruined: bool


@dataclass(frozen=True)
class SviFitResult:
    """``svi_fit``'s answer: raw SVI (a, b, rho, m, sigma), the weighted SSE in
    total variance and whether the inner problem imposed a >= 0."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float
    sse: float
    constrained_a: bool
