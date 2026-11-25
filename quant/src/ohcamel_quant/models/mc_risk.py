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
METHODS = ("fhs", "copula_t")
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


def _copula_inputs(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rank pseudo-observations ``u = rank/(n+1)`` and the Kendall-tau correlation ``sin(pi tau/2)`` (made
    positive definite by clipping eigenvalues), the same R the ``copula_t_paths`` kernel draws with."""
    from scipy.stats import kendalltau, rankdata

    n, k = x.shape
    u = np.column_stack([rankdata(x[:, j]) for j in range(k)]) / (n + 1.0)
    r = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            r[i, j] = r[j, i] = np.sin(np.pi / 2.0 * float(kendalltau(x[:, i], x[:, j]).statistic))
    w, v = np.linalg.eigh(r)
    if w.min() < 1e-8:
        r = (v * np.clip(w, 1e-8, None)) @ v.T
        d = np.sqrt(np.diag(r))
        r = r / np.outer(d, d)
    return u, r


def _copula_loglik(u: np.ndarray, r: np.ndarray, nu: float) -> float:
    """``sum_t log c_nu(u_t; R)`` = joint multivariate-t log-density at ``t_nu^{-1}(u)`` minus the univariate ones."""
    from scipy import stats

    t = stats.t.ppf(u, nu)
    joint = stats.multivariate_t(loc=np.zeros(u.shape[1]), shape=r, df=nu, allow_singular=False).logpdf(t)
    return float(np.sum(joint) - np.sum(stats.t.logpdf(t, nu)))


def _profile(u: np.ndarray, r: np.ndarray, n_grid: int = 25) -> tuple[np.ndarray, np.ndarray]:
    grid = np.geomspace(NU_BOUNDS[0], NU_BOUNDS[1], n_grid)
    return grid, np.array([_copula_loglik(u, r, float(nu)) for nu in grid])


def copula_nu_profile(x: np.ndarray, n_grid: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """``(nu grid, copula log-likelihood)`` on a geometric grid over :data:`NU_BOUNDS`, R held at its Kendall estimate."""
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2:
        raise ValueError("the copula likelihood needs at least two assets")
    return _profile(*_copula_inputs(x), n_grid)


def copula_nu(x: np.ndarray) -> float:
    """Degrees of freedom of the t-copula: the maximiser of its profile likelihood over nu in :data:`NU_BOUNDS`
    (geometric grid to locate the peak, bounded Brent search between its neighbours to refine it, the result
    rounded to the search's own tolerance, 1e-3, so a last-digit platform difference cannot move the pinned tables).

    One asset has no copula, so the single marginal's t MLE (clipped to the same bounds) stands in for it.
    """
    from scipy.optimize import minimize_scalar

    x = np.asarray(x, dtype=float)
    if x.ndim == 2 and x.shape[1] == 1:
        z = (x[:, 0] - x[:, 0].mean()) / x[:, 0].std(ddof=1)
        return float(np.clip(fit_t_dof(z), *NU_BOUNDS))
    if x.ndim != 2:
        raise ValueError("the copula likelihood needs an n x k matrix")
    u, r = _copula_inputs(x)
    grid, ll = _profile(u, r)
    i = int(np.argmax(ll))
    if i in (0, grid.size - 1):                      # the likelihood is still rising at the bound: the bound is the answer
        return float(grid[i])
    res = minimize_scalar(lambda nu: -_copula_loglik(u, r, nu), bounds=(grid[i - 1], grid[i + 1]),
                          method="bounded", options={"xatol": 1e-3})
    return float(np.clip(round(float(res.x), 3), *NU_BOUNDS))


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
              alphas: Sequence[float] = ALPHAS, horizons: Sequence[int] = HORIZONS,
              methods: Sequence[str] = METHODS) -> BookRisk:
    """``methods`` names what is computed; a method left out is not simulated at all (no busy-work)."""
    if not methods or set(methods) - set(METHODS):
        raise ValueError(f"methods must be a non-empty subset of {METHODS}, got {tuple(methods)}")
    copula = "copula_t" in methods
    x = np.ascontiguousarray(returns.to_numpy(dtype=float))
    w = np.asarray(weights, dtype=float)
    fits = [fit_gjr(x[:, j]) for j in range(x.shape[1])] if "fhs" in methods else []
    out = BookRisk(rows=[], nu=copula_nu(x) if copula else float("nan"), engine=kernels.engine_of("fhs_paths"),
                   converged=all(f.converged for f in fits))
    for h in horizons:
        if "fhs" in methods and h == 1:
            for a in alphas:
                var, es, al = fhs_1d(fits, w, a)
                out.rows.append(_row("fhs", 1, a, var, es, None))
                out.euler[a] = euler_es(al, a)
        elif "fhs" in methods:
            pnl = kernels.fhs_paths(residual_matrix(fits), kernels.GarchParams.from_fits(fits), w, h, n_paths,
                                    seed, threads)
            for a in alphas:
                out.rows.append(_row("fhs", h, a, *kernels.var_es_from_pnl(pnl, a), n_paths))
        if not copula:
            continue
        pnl = kernels.copula_t_paths(x, w, out.nu, h, n_paths, seed + 1, threads)
        for a in alphas:
            out.rows.append(_row("copula_t", h, a, *kernels.var_es_from_pnl(pnl, a), n_paths))
    return out
