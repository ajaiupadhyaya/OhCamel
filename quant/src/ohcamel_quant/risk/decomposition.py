"""Risk decomposition: Euler allocation, incremental/standalone risk, active risk.

Euler allocation (Tasche 2000, "Risk contributions and performance
measurement", TU Munich working paper; Tasche 2008, "Capital allocation to
business units and sub-portfolios: the Euler principle"): for a risk measure
``rho`` positively homogeneous of degree 1 in the weights,

``rho(w) = sum_i w_i d rho / d w_i``  (component = weight x marginal).

* Parametric (normal) VaR/ES with mean ``mu`` and covariance ``Sigma`` over
  ``h`` days: ``rho(w) = -h w'mu + sqrt(h) c sigma_p``, ``sigma_p = sqrt(w'Sigma w)``,
  ``c = z_alpha`` (VaR) or ``phi(z_alpha)/(1-alpha)`` (ES);
  marginal ``= -h mu_i + sqrt(h) c (Sigma w)_i / sigma_p``.
* Historical ES: with ``T`` the set of the k worst portfolio days,
  ``ES = -(1/k) sum_{t in T} r_p,t`` and component
  ``C_i = -(1/k) sum_{t in T} w_i r_i,t``; ``sum_i C_i = ES`` exactly
  (Tasche 2000; Hallerbach 2003, "Decomposing portfolio value-at-risk: a
  general analysis", J. Risk 5(2)).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .core import TRADING_DAYS, covariance, tail_count, validate_alpha


def _coef(alpha: float, measure: str) -> float:
    z = stats.norm.ppf(alpha)
    if measure == "var":
        return float(z)
    if measure == "es":
        return float(stats.norm.pdf(z) / (1 - alpha))
    raise ValueError("measure must be 'var' or 'es'")


def parametric_risk(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, alpha: float,
                    horizon: int = 1, measure: str = "var") -> float:
    """``-h w'mu + sqrt(h) c sqrt(w'Sigma w)``."""
    s = math.sqrt(max(float(w @ cov @ w), 0.0))
    return -horizon * float(w @ mu) + math.sqrt(horizon) * _coef(alpha, measure) * s


def euler_parametric(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, alpha: float,
                     horizon: int = 1, measure: str = "var") -> dict[str, np.ndarray | float]:
    """Euler decomposition of parametric VaR/ES. Returns total, marginal, component, pct."""
    alpha = validate_alpha(alpha)
    c = _coef(alpha, measure)
    sp = math.sqrt(max(float(w @ cov @ w), 0.0))
    if sp == 0:
        raise ValueError("portfolio variance is zero")
    marginal = -horizon * mu + math.sqrt(horizon) * c * (cov @ w) / sp
    comp = w * marginal
    total = float(comp.sum())
    return {"total": total, "marginal": marginal, "component": comp,
            "pct": comp / total if total != 0 else np.full_like(comp, np.nan)}


def historical_es_euler(R: np.ndarray, w: np.ndarray, alpha: float) -> dict[str, Any]:
    """Euler (tail-scenario) decomposition of historical ES; components sum exactly to ES."""
    alpha = validate_alpha(alpha)
    rp = R @ w
    k = tail_count(rp.size, alpha)
    idx = np.argsort(rp, kind="stable")[:k]               # k worst portfolio days
    comp = -(R[idx] * w).mean(axis=0)
    es = float(-rp[idx].mean())
    var = float(-rp[idx].max())
    return {"total": es, "var": var, "component": comp, "pct": comp / es if es != 0 else comp * np.nan,
            "tail_days": np.sort(idx), "k": k}


def historical_var_es(rp: np.ndarray, alpha: float) -> tuple[float, float]:
    k = tail_count(rp.size, alpha)
    worst = np.sort(rp)[:k]
    return float(-worst[-1]), float(-worst.mean())


def decompose(
    returns: pd.DataFrame, weights: dict[str, float], benchmark: str | None, alpha: float = 0.99,
    horizon: int = 1, cov_method: str = "sample", lam: float = 0.94,
) -> dict[str, Any]:
    """Full position-level decomposition.

    ``returns`` holds the holdings' (and optionally the benchmark's) daily
    returns on common sessions. Output tables are per holding.

    For ``horizon = h > 1`` the historical VaR/ES and their Euler components use
    OVERLAPPING h-day sums of daily position P&L (constant exposure), consistent
    with the parametric ``h mu`` / ``h Sigma`` scaling; ``historical_tail_days``
    are then the row positions of each tail window's END date.
    """
    alpha = validate_alpha(alpha)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    tickers = list(weights)
    R = returns[tickers].to_numpy(dtype=float)
    if horizon > 1:
        # h-day position P&L = overlapping sums of daily P&L at constant exposure,
        # the same aggregation the parametric figures use (mean x h, Sigma x h).
        if R.shape[0] < horizon + 1:
            raise ValueError(f"need more than {horizon} observations for a {horizon}-day decomposition")
        c = np.vstack([np.zeros((1, R.shape[1])), np.cumsum(R, axis=0)])
        R = c[horizon:] - c[:-horizon]
    w = np.array([weights[t] for t in tickers], dtype=float)
    mu, cov = covariance(returns[tickers], cov_method, lam)
    n = len(tickers)

    ev = euler_parametric(w, mu, cov, alpha, horizon, "var")
    ee = euler_parametric(w, mu, cov, alpha, horizon, "es")
    he = historical_es_euler(R, w, alpha)
    hvar, hes = historical_var_es(R @ w, alpha)
    # map tail windows to the row (END date) of each window in ``returns``
    he["tail_days"] = he["tail_days"] + (horizon - 1)

    inc_var = np.empty(n)
    inc_hes = np.empty(n)
    standalone_var = np.empty(n)
    standalone_es = np.empty(n)
    vols = np.sqrt(np.diag(cov))
    for i in range(n):
        w0 = w.copy()
        w0[i] = 0.0
        inc_var[i] = ev["total"] - (parametric_risk(w0, mu, cov, alpha, horizon, "var") if np.any(w0) else 0.0)
        inc_hes[i] = hes - (historical_var_es(R @ w0, alpha)[1] if np.any(w0) else 0.0)
        wi = np.zeros(n)
        wi[i] = w[i]
        standalone_var[i] = parametric_risk(wi, mu, cov, alpha, horizon, "var")
        standalone_es[i] = parametric_risk(wi, mu, cov, alpha, horizon, "es")

    sp = math.sqrt(float(w @ cov @ w))
    div_ratio = float(np.abs(w) @ vols / sp)
    corr = cov / np.outer(vols, vols)

    table = pd.DataFrame({
        "weight": w, "vol_annualized": vols * math.sqrt(TRADING_DAYS),
        "marginal_var": ev["marginal"], "component_var": ev["component"], "pct_var": ev["pct"],
        "marginal_es": ee["marginal"], "component_es": ee["component"], "pct_es": ee["pct"],
        "hist_component_es": he["component"], "hist_pct_es": he["pct"],
        "incremental_var": inc_var, "incremental_hist_es": inc_hes,
        "standalone_var": standalone_var, "standalone_es": standalone_es,
    }, index=pd.Index(tickers, name="ticker"))

    out: dict[str, Any] = {
        "table": table,
        "totals": {
            "parametric_var": ev["total"], "parametric_es": ee["total"],
            "historical_var": hvar, "historical_es": hes,
            "sum_standalone_var": float(standalone_var.sum()),
            "diversification_benefit_var": float(standalone_var.sum() - ev["total"]),
            "diversification_ratio": div_ratio,
            "portfolio_vol_annualized": sp * math.sqrt(TRADING_DAYS),
        },
        "correlation": pd.DataFrame(corr, index=tickers, columns=tickers),
        "historical_tail_days": he["tail_days"],
    }

    if benchmark:
        out["benchmark"] = benchmark_risk(returns, weights, benchmark, cov_method, lam)
    return out


def benchmark_risk(returns: pd.DataFrame, weights: dict[str, float], benchmark: str,
                   cov_method: str = "sample", lam: float = 0.94) -> dict[str, Any]:
    """Beta and ex-ante tracking error versus a benchmark.

    Active weights ``a = [w; -1]`` over (holdings, benchmark) (the benchmark
    weight is netted when the benchmark is itself held). Ex-ante TE
    ``= sqrt(a' Sigma a)`` (daily; annualised by sqrt(252)); beta
    ``= w' Sigma_{.,b} / Sigma_{bb}`` (Grinold & Kahn 2000, *Active Portfolio
    Management*, ch. 3). TE contributions are the Euler components
    ``a_i (Sigma a)_i / TE``.
    """
    if benchmark not in returns.columns:
        raise ValueError(f"benchmark {benchmark} returns not supplied")
    cols = list(dict.fromkeys([*weights, benchmark]))
    _, cov = covariance(returns[cols], cov_method, lam)
    w = np.array([weights.get(c, 0.0) for c in cols])
    b = np.array([1.0 if c == benchmark else 0.0 for c in cols])
    a = w - b
    ib = cols.index(benchmark)
    var_b = cov[ib, ib]
    betas = cov[:, ib] / var_b
    te = math.sqrt(max(float(a @ cov @ a), 0.0))
    contrib = a * (cov @ a) / te if te > 0 else np.zeros_like(a)
    return {
        "benchmark": benchmark,
        "portfolio_beta": float(w @ betas),
        "position_beta": {c: float(betas[i]) for i, c in enumerate(cols) if c in weights},
        "tracking_error_daily": te,
        "tracking_error_annualized": te * math.sqrt(TRADING_DAYS),
        "active_weights": {c: float(a[i]) for i, c in enumerate(cols)},
        "te_contribution": {c: float(contrib[i]) for i, c in enumerate(cols)},
        "correlation_with_benchmark": float((w @ cov[:, ib]) / math.sqrt(max(w @ cov @ w, 1e-300) * var_b)),
    }
