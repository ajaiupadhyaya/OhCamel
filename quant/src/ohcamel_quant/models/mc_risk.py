"""P1 Monte Carlo risk (compute plan M2): FHS and Student-t copula VaR/ES for a book.

FHS (Barone-Adesi, Giannopoulos & Vosper 1999): a GJR-GARCH(1,1)-t per asset
(kernel ``garch_fit``); standardized residual ROWS are drawn jointly, so the
cross-section keeps its empirical dependence. 1-day FHS is exact over the
residual rows (no simulation, as ``risk.garch.fhs_var_es``) and carries the
Euler split (Tasche 2000): asset i's ES contribution is its mean weighted loss
on the tail rows. h-day FHS and both copula horizons simulate ``n_paths`` on
the Lane A kernels. The copula's degrees of freedom are the median of the
per-asset t MLEs of standardized returns, clipped to [3, 30].
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import kernels
from ..risk.core import empirical_var_es, fit_t_dof, tail_count
from ..risk.garch import FastGarch

ALPHAS = (0.95, 0.975, 0.99)
HORIZONS = (1, 10)
NU_BOUNDS = (3.0, 30.0)


def fit_gjr(r: np.ndarray) -> FastGarch:
    """GJR-GARCH(1,1)-t on DECIMAL returns via the kernel (fit on 100 r), as risk/backtest._refit converts it."""
    g = kernels.garch_fit(100.0 * np.asarray(r, dtype=float), "gjr", None)
    p = g.params  # [mu, omega, alpha, gamma, beta, nu]
    return FastGarch("gjr", float(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]), -g.nll,
                     g.sigma2, g.next_variance, g.std_resid, p.copy(), g.converged, g.iterations)


def residual_matrix(fits: Sequence[FastGarch]) -> np.ndarray:
    z = np.column_stack([f.std_resid for f in fits])
    return np.ascontiguousarray(z[np.all(np.isfinite(z), axis=1)])


def fhs_1d(fits: Sequence[FastGarch], weights: np.ndarray, alpha: float) -> tuple[float, float, np.ndarray]:
    """``(VaR, ES, asset_loss)``: L_{j,i} = -w_i (mu_i + sigma_{T+1,i} z_{j,i}) / 100 over residual rows j."""
    z = residual_matrix(fits)
    mu = np.array([f.mu for f in fits])
    sig = np.sqrt(np.array([f.next_variance_pct for f in fits]))
    asset_loss = -(mu + sig * z) / 100.0 * np.asarray(weights, dtype=float)
    var, es = empirical_var_es(asset_loss.sum(axis=1), alpha)
    return var, es, asset_loss


def euler_es(asset_loss: np.ndarray, alpha: float) -> np.ndarray:
    loss = asset_loss.sum(axis=1)
    k = tail_count(loss.size, alpha)
    tail = np.argsort(-loss, kind="stable")[:k]
    return asset_loss[tail].mean(axis=0)


def copula_nu(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    z = (x - x.mean(axis=0)) / x.std(axis=0, ddof=1)
    return float(np.clip(np.median([fit_t_dof(z[:, j]) for j in range(z.shape[1])]), *NU_BOUNDS))


@dataclass
class BookRisk:
    rows: list[dict]
    euler: dict[float, np.ndarray] = field(default_factory=dict)
    nu: float = float("nan")
    engine: str = "python"
    converged: bool = True


def _row(method: str, h: int, a: float, var: float, es: float, paths: int | None) -> dict:
    return {"method": method, "horizon": h, "alpha": a, "var": float(var), "es": float(es), "n_paths": paths}


def book_risk(returns: pd.DataFrame, weights: np.ndarray, *, n_paths: int, seed: int, threads: int,
              alphas: Sequence[float] = ALPHAS, horizons: Sequence[int] = HORIZONS) -> BookRisk:
    x = np.ascontiguousarray(returns.to_numpy(dtype=float))
    w = np.asarray(weights, dtype=float)
    fits = [fit_gjr(x[:, j]) for j in range(x.shape[1])]
    out = BookRisk(rows=[], nu=copula_nu(x), engine=kernels.engine_of("fhs_paths"),
                   converged=all(f.converged for f in fits))
    for h in horizons:
        if h == 1:
            for a in alphas:
                var, es, al = fhs_1d(fits, w, a)
                out.rows.append(_row("fhs", 1, a, var, es, None))
                out.euler[a] = euler_es(al, a)
        else:
            pnl = kernels.fhs_paths(residual_matrix(fits), kernels.GarchParams.from_fits(fits), w, h, n_paths,
                                    seed, threads)
            for a in alphas:
                out.rows.append(_row("fhs", h, a, *kernels.var_es_from_pnl(pnl, a), n_paths))
        pnl = kernels.copula_t_paths(x, w, out.nu, h, n_paths, seed + 1, threads)
        for a in alphas:
            out.rows.append(_row("copula_t", h, a, *kernels.var_es_from_pnl(pnl, a), n_paths))
    return out
