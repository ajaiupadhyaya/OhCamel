"""The book marked to live quotes: drifted weights, the day's P&L, drawdown
including today, and the Euler split of VaR at the live weights.

Pure: no I/O. Weights are signed DECIMAL fractions of equity at the previous
close; ``1 - sum(w)`` is cash at 0 %.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..risk.decomposition import euler_parametric, parametric_risk


def live_weights(weights: dict[str, float], moves: dict[str, float | None]) -> tuple[dict[str, float], float, list[str]]:
    """Drift close weights by each holding's move since the previous close.

    ``w'_i = w_i (1 + r_i) / (1 + sum_j w_j r_j)``. A holding with no move
    (``None`` or non-finite) is held at ``r_i = 0`` and returned in the third
    element, so the caller can say which marks are missing.
    Returns ``(live_weights, day_return, missing)``.
    """
    missing: list[str] = []
    r: dict[str, float] = {}
    for t in weights:
        m = moves.get(t)
        if m is None or not math.isfinite(m):
            missing.append(t)
            r[t] = 0.0
        else:
            r[t] = float(m)
    day = sum(weights[t] * r[t] for t in weights)
    if not 1.0 + day > 0:
        raise ValueError("the book's equity is not positive after today's moves")
    return {t: weights[t] * (1.0 + r[t]) / (1.0 + day) for t in weights}, day, missing


def drawdown(close_returns: pd.Series, today: float | None) -> float:
    """Current drawdown (>= 0) of the wealth path ``prod(1 + r)``, with
    ``today`` appended as the last step when it is not already in the series."""
    r = close_returns.to_numpy(dtype=float)
    if today is not None:
        r = np.append(r, today)
    if r.size == 0:
        raise ValueError("no returns")
    wealth = np.cumprod(1.0 + r)
    peak = max(1.0, float(np.max(wealth)))
    return max(0.0, 1.0 - float(wealth[-1]) / peak)


def risk_at(weights: dict[str, float], columns: list[str], cov: np.ndarray, alpha: float) -> dict[str, Any]:
    """1-day parametric VaR/ES at zero mean (the RiskMetrics convention the
    EWMA covariance is built for) and the Euler split of VaR by holding."""
    w = np.array([weights.get(c, 0.0) for c in columns], dtype=float)
    mu = np.zeros(len(columns))
    var = parametric_risk(w, mu, cov, alpha, 1, "var")
    es = parametric_risk(w, mu, cov, alpha, 1, "es")
    split = euler_parametric(w, mu, cov, alpha, 1, "var")
    return {
        "var": var, "es": es,
        "component": dict(zip(columns, map(float, split["component"]), strict=True)),
        "pct": dict(zip(columns, map(float, split["pct"]), strict=True)),
    }
