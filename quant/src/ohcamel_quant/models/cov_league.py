"""P3: covariance forecast league (compute plan M4).

Every ``hold`` sessions, each estimator is fitted on the trailing ``est_window``
sessions; its global minimum-variance portfolio ``w = S^-1 1 / 1'S^-1 1`` is held
at constant weights (rebalanced daily to them) for the next ``hold`` sessions.
The estimator whose GMV portfolio realizes the lowest volatility forecast
covariance best for this use (Ledoit & Wolf 2004; Engle & Colacito 2006's
criterion). The PCA factor model keeps the ``k`` principal components of the
correlation matrix above the Marchenko-Pastur edge (at least one) and puts
specific variances on the diagonal.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

from ..factors.hac import newey_west_lrv, nw_lag_rule
from ..portfolio.covariance import estimate_covariance

ESTIMATORS = ("sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise", "pca_factor")
EST_WINDOW = 252
HOLD = 21
EVAL_SESSIONS = 2520


def pca_factor_cov(x: np.ndarray, k: int | None = None) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    t, n = x.shape
    s = np.cov(x, rowvar=False)
    if k is None:
        sd = np.sqrt(np.diag(s))
        corr_eig = np.linalg.eigvalsh(s / np.outer(sd, sd))
        k = max(1, int(np.sum(corr_eig > (1.0 + math.sqrt(n / t)) ** 2)))
    vals, vecs = np.linalg.eigh(s)
    top = np.argsort(vals)[::-1][:k]
    b = vecs[:, top] * np.sqrt(vals[top])
    common = b @ b.T
    spec = np.clip(np.diag(s) - np.diag(common), 1e-12, None)
    return common + np.diag(spec)


def gmv_weights(cov: np.ndarray) -> np.ndarray:
    w = np.linalg.solve(np.asarray(cov, dtype=float), np.ones(len(cov)))
    return w / w.sum()


def estimator_cov(name: str, window: pd.DataFrame) -> np.ndarray:
    if name == "pca_factor":
        return pca_factor_cov(window.to_numpy(dtype=float))
    return estimate_covariance(window, name).cov.to_numpy(dtype=float)


def league_returns(returns: pd.DataFrame, *, estimators: tuple[str, ...] = ESTIMATORS, est_window: int = EST_WINDOW,
                   hold: int = HOLD, eval_sessions: int = EVAL_SESSIONS) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = returns.dropna()
    first = max(est_window, len(r) - eval_sessions)
    if len(r) - first < hold:
        raise ValueError(f"need at least {est_window + hold} common sessions; have {len(r)}")
    out = {e: [] for e in estimators}
    gross = []
    for s in range(first, len(r), hold):
        win, nxt = r.iloc[s - est_window:s], r.iloc[s:s + hold].to_numpy(dtype=float)
        row = {"date": r.index[s]}
        for e in estimators:
            w = gmv_weights(estimator_cov(e, win))
            out[e].append(nxt @ w)
            row[e] = float(np.abs(w).sum())
        gross.append(row)
    idx = r.index[first:]
    daily = pd.DataFrame({e: np.concatenate(v) for e, v in out.items()}, index=idx)
    return daily, pd.DataFrame(gross).set_index("date")


def variance_test(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """H0: Var(a) = Var(b). Delta method on ln Var(a) - ln Var(b) with a Newey-West long-run covariance of the
    moment series ((a - mean a)^2 - Var a, (b - mean b)^2 - Var b), as Ledoit & Wolf's HAC inference (2008; the
    variance version, 2011) without their bootstrap. Two-sided normal p-value."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    va, vb = a.var(), b.var()
    u = np.column_stack([(a - a.mean()) ** 2 - va, (b - b.mean()) ** 2 - vb])
    s = newey_west_lrv(u, nw_lag_rule(len(a)))
    g = np.array([1.0 / va, -1.0 / vb])
    se = math.sqrt(max(float(g @ s @ g) / len(a), 0.0))
    diff = math.log(va) - math.log(vb)
    stat = diff / se if se > 0 else 0.0
    return {"diff_log_var": diff, "stat": stat, "pvalue": float(2.0 * stats.norm.sf(abs(stat)))}
