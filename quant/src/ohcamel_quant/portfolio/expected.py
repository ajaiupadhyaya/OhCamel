"""Expected-return models (all outputs ANNUALIZED decimal returns, x252).

* ``historical``  -- sample arithmetic mean x 252 (the geometric/CAGR figure is
  reported alongside: ``g ~ mu - sigma^2/2``; optimizers use arithmetic means
  because portfolio arithmetic means are linear in weights).
* ``james_stein`` -- Bayes-Stein shrinkage towards the grand mean (Jorion 1986,
  "Bayes-Stein estimation for portfolio analysis", *JFQA* 21(3)).
* ``capm``        -- CAPM equilibrium ``mu_i = rf + beta_i (E[r_b] - rf)`` with
  betas and the benchmark premium estimated over the window (Sharpe 1964).
* ``black_litterman`` -- He & Litterman (1999), "The intuition behind
  Black-Litterman model portfolios", with Idzorek (2005), "A step-by-step
  guide to the Black-Litterman model", confidence-based ``Omega``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

TRADING_DAYS = 252

REFERENCES: dict[str, str] = {
    "historical": "Sample mean (Markowitz 1952); see Merton (1980) on the imprecision of mean estimates",
    "james_stein": "Jorion (1986), 'Bayes-Stein estimation for portfolio analysis', JFQA 21(3)",
    "capm": "Sharpe (1964), 'Capital asset prices', JF 19(3); Lintner (1965)",
    "black_litterman": "Black & Litterman (1992), FAJ 48(5); He & Litterman (1999), 'The intuition "
                       "behind Black-Litterman model portfolios'; Idzorek (2005), 'A step-by-step guide "
                       "to the Black-Litterman model'",
}


@dataclass
class ExpectedReturns:
    method: str
    mu: pd.Series  # annualized
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    cov: pd.DataFrame | None = None  # posterior covariance (Black-Litterman) if different

    @property
    def reference(self) -> str:
        return REFERENCES.get(self.method, "")


def _check(returns: pd.DataFrame) -> np.ndarray:
    x = returns.to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("returns contain NaN/inf")
    if x.shape[0] < 2:
        raise ValueError("need at least two observations")
    return x


def historical_mean(returns: pd.DataFrame) -> ExpectedReturns:
    """``mu_i = 252 * mean_t r_it``; also reports the geometric annual growth
    ``(prod(1 + r_it))^(252/T) - 1`` and the standard error ``252 sigma_i / sqrt(T)``."""
    x = _check(returns)
    t = x.shape[0]
    mu = x.mean(axis=0) * TRADING_DAYS
    geo = np.prod(1 + x, axis=0) ** (TRADING_DAYS / t) - 1
    se = x.std(axis=0, ddof=1) * TRADING_DAYS / np.sqrt(t)
    er = ExpectedReturns("historical", pd.Series(mu, index=returns.columns))
    er.extra = {"geometric": pd.Series(geo, index=returns.columns),
                "standard_error": pd.Series(se, index=returns.columns)}
    er.notes.append("Arithmetic mean x 252; the geometric (compound) rate is lower by about sigma^2/2. "
                    f"Mean estimates over {t} sessions carry standard errors of "
                    f"{np.median(se):.1%} (median) -- treat them with caution.")
    return er


def james_stein(returns: pd.DataFrame, target: Literal["gmv", "average"] = "gmv") -> ExpectedReturns:
    """Bayes-Stein shrinkage of the sample means (Jorion 1986, eqs. 17-19).

    ``mu_BS = (1 - w) mu_hat + w mu_0 1`` with
    ``w = (N + 2) / ((N + 2) + T (mu_hat - mu_0 1)' Sigma^{-1} (mu_hat - mu_0 1))``,
    ``Sigma = S (T - 1)/(T - N - 2)``, and grand mean ``mu_0`` the mean of the
    global-minimum-variance portfolio ``1'Sigma^{-1} mu_hat / 1'Sigma^{-1} 1``
    (``target='gmv'``, Jorion) or the cross-sectional average (``'average'``).
    Computed in daily units, then annualized.
    """
    x = _check(returns)
    t, n = x.shape
    if t <= n + 2:
        raise ValueError("James-Stein needs T > N + 2")
    m = x.mean(axis=0)
    s = np.cov(x, rowvar=False, ddof=1) * (t - 1) / (t - n - 2)
    sinv = np.linalg.inv(s)
    one = np.ones(n)
    mu0 = float(one @ sinv @ m / (one @ sinv @ one)) if target == "gmv" else float(m.mean())
    d = m - mu0
    wshrink = (n + 2) / ((n + 2) + t * float(d @ sinv @ d))
    mu = ((1 - wshrink) * m + wshrink * mu0) * TRADING_DAYS
    er = ExpectedReturns("james_stein", pd.Series(mu, index=returns.columns),
                         params={"shrinkage": wshrink, "grand_mean": mu0 * TRADING_DAYS,
                                 "target": target})
    return er


def benchmark_premium(bench: pd.Series, rf_daily: pd.Series | float) -> tuple[float, float]:
    """Annualized mean excess return and variance of the benchmark over the window."""
    b = bench.to_numpy(float)
    rf = _align_rf(bench.index, rf_daily)
    ex = b - rf
    return float(ex.mean() * TRADING_DAYS), float(b.var(ddof=1) * TRADING_DAYS)


def _align_rf(index: pd.Index, rf_daily: pd.Series | float) -> np.ndarray:
    if isinstance(rf_daily, pd.Series):
        rf = rf_daily.reindex(index).ffill().bfill()
        if rf.isna().any():
            raise ValueError("risk-free series does not cover the window")
        return rf.to_numpy(float)
    return np.full(len(index), float(rf_daily))


def implied_risk_aversion(bench: pd.Series, rf_daily: pd.Series | float) -> float:
    """``delta = (E[r_b] - rf) / sigma_b^2`` estimated over the window (Black & Litterman 1992)."""
    prem, var = benchmark_premium(bench, rf_daily)
    return prem / var


def capm_equilibrium(returns: pd.DataFrame, bench: pd.Series, rf_daily: pd.Series | float) -> ExpectedReturns:
    """``mu_i = rf + beta_i (E[r_b] - rf)``, ``beta_i = cov(r_i, r_b)/var(r_b)``
    on excess returns; ``rf`` is the window-average annualized risk-free rate."""
    x = _check(returns)
    b = bench.reindex(returns.index).to_numpy(float)
    if not np.isfinite(b).all():
        raise ValueError("benchmark returns do not cover the window")
    rf = _align_rf(returns.index, rf_daily)
    ex = x - rf[:, None]
    eb = b - rf
    cov = ((ex - ex.mean(0)) * (eb - eb.mean())[:, None]).sum(0) / (len(eb) - 1)
    beta = cov / eb.var(ddof=1)
    prem = eb.mean() * TRADING_DAYS
    rf_ann = rf.mean() * TRADING_DAYS
    mu = rf_ann + beta * prem
    er = ExpectedReturns("capm", pd.Series(mu, index=returns.columns),
                         params={"benchmark_premium": float(prem), "risk_free": float(rf_ann)},
                         extra={"beta": pd.Series(beta, index=returns.columns)})
    er.notes.append("Benchmark premium is its historical mean excess return over the window: a noisy "
                    "estimate of the forward equity premium.")
    return er


# ------------------------------------------------------------------ Black-Litterman
@dataclass
class View:
    """``P_k w`` view: absolute (``long`` returns ``value``) or relative
    (``long`` outperforms ``short`` by ``value``), annualized decimal, with
    Idzorek confidence in [0, 1].

    An absolute view is a TOTAL expected return (the same units as the model's
    output ``mu``); :func:`black_litterman` converts it to an excess return
    ``value - rf`` because the prior ``pi`` and the posterior are excess returns.
    A relative view is a spread, so the risk-free rate cancels."""

    long: str
    value: float
    confidence: float
    short: str | None = None

    @property
    def kind(self) -> str:
        return "relative" if self.short else "absolute"

    def label(self) -> str:
        if self.short:
            return f"{self.long} outperforms {self.short} by {self.value:.2%}"
        return f"{self.long} returns {self.value:.2%}"


def _pq(views: list[View], names: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = {n: i for i, n in enumerate(names)}
    p = np.zeros((len(views), len(names)))
    q = np.zeros(len(views))
    c = np.zeros(len(views))
    for k, v in enumerate(views):
        if v.long not in idx:
            raise ValueError(f"view asset {v.long} not in universe")
        p[k, idx[v.long]] = 1.0
        if v.short:
            if v.short not in idx:
                raise ValueError(f"view asset {v.short} not in universe")
            if v.short == v.long:
                raise ValueError("relative view needs two different assets")
            p[k, idx[v.short]] = -1.0
        if not 0.0 <= v.confidence <= 1.0:
            raise ValueError("view confidence must be in [0, 1]")
        q[k], c[k] = v.value, v.confidence
    return p, q, c


def bl_posterior(pi: np.ndarray, sigma: np.ndarray, p: np.ndarray, q: np.ndarray,
                 omega: np.ndarray, tau: float) -> tuple[np.ndarray, np.ndarray]:
    """He & Litterman (1999) posterior, in the form valid for ``Omega -> 0``:

    ``mu_BL = pi + tau Sigma P' (P tau Sigma P' + Omega)^{-1} (Q - P pi)``,
    ``M = tau Sigma - tau Sigma P' (P tau Sigma P' + Omega)^{-1} P tau Sigma``,
    ``Sigma_BL = Sigma + M``.
    """
    if p.shape[0] == 0:
        return pi.copy(), sigma + tau * sigma
    ts = tau * sigma
    a = p @ ts @ p.T + omega
    k = ts @ p.T @ np.linalg.solve(a, np.eye(len(q)))
    mu = pi + k @ (q - p @ pi)
    m = ts - k @ p @ ts
    return mu, sigma + m


def idzorek_omega(pi: np.ndarray, sigma: np.ndarray, p: np.ndarray, q: np.ndarray,
                  conf: np.ndarray, tau: float, delta: float, w_mkt: np.ndarray) -> np.ndarray:
    """Idzorek (2005) confidence-based view variances (one view at a time).

    For view ``k``: the 100%-confidence weights ``w_100 = (delta Sigma)^{-1} mu_100``
    with ``mu_100`` the posterior at ``omega_k = 0``; target tilt
    ``w_target = w_mkt + c_k (w_100 - w_mkt)``; solve
    ``omega_k = argmin sum_i (w_i(omega_k) - w_target,i)^2`` over ``omega_k > 0``
    with ``w(omega) = (delta Sigma)^{-1} mu_BL(omega)``. Starts from the Walters
    closed form ``omega_k = tau ((1-c)/c) p_k Sigma p_k'``. Confidence 1 gives
    ``omega_k = 0``; confidence 0 gives an infinite variance (view ignored).
    """
    k = len(q)
    om = np.zeros(k)
    ds_inv = np.linalg.inv(delta * sigma)
    for j in range(k):
        pj, qj, cj = p[j:j + 1], q[j:j + 1], float(conf[j])
        if cj >= 1.0:
            om[j] = 0.0
            continue
        if cj <= 0.0:
            om[j] = np.inf
            continue
        mu100, _ = bl_posterior(pi, sigma, pj, qj, np.zeros((1, 1)), tau)
        w100 = ds_inv @ mu100
        target = w_mkt + cj * (w100 - w_mkt)
        base = float((tau * pj @ sigma @ pj.T)[0, 0])

        def loss(log_om: float, pj=pj, qj=qj, target=target) -> float:
            mu, _ = bl_posterior(pi, sigma, pj, qj, np.array([[np.exp(log_om)]]), tau)
            return float(np.sum((ds_inv @ mu - target) ** 2))

        guess = np.log(base * (1 - cj) / cj)
        res = minimize_scalar(loss, bounds=(guess - 12, guess + 12), method="bounded",
                              options={"xatol": 1e-10})
        om[j] = float(np.exp(res.x))
    return om


def black_litterman(sigma: pd.DataFrame, delta: float, w_mkt: pd.Series, views: list[View],
                    tau: float = 0.05, rf: float = 0.0, prior_source: str = "") -> ExpectedReturns:
    """Black-Litterman posterior expected returns (annualized).

    Prior (reverse optimisation): ``pi = delta Sigma w_mkt`` (excess returns);
    views ``P mu = Q + eps``, ``eps ~ N(0, Omega)``, ``Omega = diag(omega_k)`` from
    :func:`idzorek_omega`. Absolute views are stated as total returns and enter
    ``Q`` as excess returns ``value - rf`` (relative views are spreads and need no
    adjustment), so a 100%-confidence absolute view is reproduced exactly in the
    returned total ``mu``. The returned ``mu`` is TOTAL return ``rf + mu_BL`` and
    ``cov`` is the posterior ``Sigma + M``. With no views the posterior mean is
    the prior ``pi`` (He & Litterman 1999).
    """
    names = [str(c) for c in sigma.columns]
    s = sigma.to_numpy(float)
    w = w_mkt.reindex(names).to_numpy(float)
    if not np.isfinite(w).all():
        raise ValueError("market weights missing for some assets")
    w = w / w.sum()
    if tau <= 0:
        raise ValueError("tau must be positive")
    pi = delta * s @ w
    used = [v for v in views if v.confidence > 0]
    p, q, c = _pq(used, names)
    # absolute views are total returns; the model works in excess returns
    q = q - rf * np.array([0.0 if v.short else 1.0 for v in used])
    if used:
        om = idzorek_omega(pi, s, p, q, c, tau, delta, w)
        finite = np.isfinite(om)
        p, q, om = p[finite], q[finite], om[finite]
        mu, post = bl_posterior(pi, s, p, q, np.diag(om), tau)
    else:
        om = np.zeros(0)
        mu, post = bl_posterior(pi, s, p, q, np.zeros((0, 0)), tau)
    er = ExpectedReturns("black_litterman", pd.Series(mu + rf, index=names),
                         params={"delta": delta, "tau": tau, "risk_free": rf, "n_views": len(used)},
                         cov=pd.DataFrame(post, index=names, columns=names))
    w_bl = np.linalg.solve(delta * post, mu)
    er.extra = {
        "prior_excess": pd.Series(pi, index=names),
        "posterior_excess": pd.Series(mu, index=names),
        "market_weights": pd.Series(w, index=names),
        "unconstrained_bl_weights": pd.Series(w_bl, index=names),
        # prior_implied is in the same units as value (total return for an absolute
        # view, spread for a relative one); value_excess is the Q entry actually used
        "views": [{"label": v.label(), "kind": v.kind, "long": v.long, "short": v.short,
                   "value": v.value, "confidence": v.confidence,
                   "omega": float(o), "prior_implied": float(pr) + (0.0 if v.short else rf),
                   "value_excess": float(qk)}
                  for v, o, pr, qk in zip(used, om, (p @ pi) if len(used) else [], q, strict=False)],
        "prior_source": prior_source,
    }
    if not views:
        er.notes.append("No views: posterior mean equals the equilibrium prior pi = delta Sigma w_mkt.")
    return er
