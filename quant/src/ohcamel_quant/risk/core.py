"""Shared building blocks for the risk domain.

Conventions used throughout :mod:`ohcamel_quant.risk`
-----------------------------------------------------
* Returns ``r`` are simple DECIMAL daily returns; the portfolio return on a
  session is ``r_p = sum_i w_i r_i`` with the signed weights held constant
  (i.e. the book is rebalanced to the target weights every session; any
  residual ``1 - sum w`` is uninvested cash earning zero).
* Losses are ``L = -r``. Value-at-Risk and Expected Shortfall are reported as
  POSITIVE loss fractions of equity at confidence ``alpha`` (e.g. 0.99):

  ``VaR_alpha = inf{ l : P(L <= l) >= alpha }``,
  ``ES_alpha  = E[L | L >= VaR_alpha]`` (Acerbi & Tasche 2002, "On the
  coherence of expected shortfall").

  USD figures are the fractions times the portfolio notional.
* Tail probability ``p = 1 - alpha``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


@dataclass
class RiskEstimate:
    """One model's VaR/ES at one confidence level and horizon."""

    model: str
    alpha: float
    horizon: int
    var: float
    es: float
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    reference: str = ""
    #: False when the model is used outside its domain of validity (e.g. a
    #: non-monotone Cornish-Fisher expansion); the numbers are then flagged.
    valid: bool = True

    def to_dict(self, notional: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model": self.model,
            "alpha": self.alpha,
            "horizon": self.horizon,
            "var": self.var,
            "es": self.es,
            "params": self.params,
            "notes": self.notes,
            "reference": self.reference,
            "valid": self.valid,
        }
        if notional is not None:
            out["var_usd"] = self.var * notional if math.isfinite(self.var) else None
            out["es_usd"] = self.es * notional if math.isfinite(self.es) else None
        return out


def validate_alpha(alpha: float) -> float:
    if not 0.5 < alpha < 1.0:
        raise ValueError(f"confidence level alpha must be in (0.5, 1); got {alpha}")
    return float(alpha)


def portfolio_returns(returns: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """``r_p,t = sum_i w_i r_i,t`` on the sessions common to every holding.

    Weights are held constant (daily rebalanced). Raises ``ValueError`` if a
    weighted ticker is missing from ``returns``.
    """
    missing = [t for t in weights if t not in returns.columns]
    if missing:
        raise ValueError(f"no return series for: {', '.join(missing)}")
    cols = list(weights)
    w = np.array([weights[c] for c in cols], dtype=float)
    sub = returns[cols].dropna(how="any")
    rp = pd.Series(sub.to_numpy() @ w, index=sub.index, name="portfolio")
    return rp


def weight_vector(weights: Mapping[str, float], columns: list[str]) -> np.ndarray:
    return np.array([float(weights.get(c, 0.0)) for c in columns])


# --------------------------------------------------------------- tail helpers
def tail_count(n: int, alpha: float) -> int:
    """Number of tail observations ``k = ceil(n (1 - alpha))`` (at least 1).

    Empirical convention used by every historical estimator here: VaR is the
    k-th largest loss and ES is the mean of the k largest losses, so that the
    empirical tail frequency ``k / n`` is the smallest one ``>= 1 - alpha``.
    """
    return max(1, math.ceil(n * (1.0 - alpha) - 1e-9))


def empirical_var_es(losses: np.ndarray, alpha: float) -> tuple[float, float]:
    """Historical VaR (k-th largest loss) and ES (mean of the k largest)."""
    x = np.asarray(losses, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return math.nan, math.nan
    k = tail_count(x.size, alpha)
    top = -np.partition(-x, k - 1)[:k]
    top.sort()
    return float(top[0]), float(top.mean())


def std_t_quantile(alpha: float, nu: float) -> float:
    """alpha-quantile of the UNIT-VARIANCE Student-t: ``t_nu^{-1}(alpha) sqrt((nu-2)/nu)``."""
    return float(stats.t.ppf(alpha, nu) * math.sqrt((nu - 2.0) / nu))


def std_t_es(alpha: float, nu: float) -> float:
    """Expected shortfall (upper tail) of the UNIT-VARIANCE Student-t.

    For the standard t with ``nu > 1`` and ``q = t_nu^{-1}(alpha)``
    (McNeil, Frey & Embrechts 2015, *Quantitative Risk Management*, Ex. 2.15):

    ``ES_alpha = g_nu(q) / (1 - alpha) * (nu + q^2) / (nu - 1)``

    where ``g_nu`` is the t density; multiplied by ``sqrt((nu-2)/nu)`` to give
    unit variance.
    """
    q = stats.t.ppf(alpha, nu)
    es = stats.t.pdf(q, nu) / (1.0 - alpha) * (nu + q * q) / (nu - 1.0)
    return float(es * math.sqrt((nu - 2.0) / nu))


def fit_t_dof(x: np.ndarray, lo: float = 2.05, hi: float = 200.0) -> float:
    """MLE of the Student-t degrees of freedom with variance matched to the sample.

    The location is the sample mean ``m`` and the scale ``c = s sqrt((nu-2)/nu)``
    (``s`` the sample standard deviation), so the fitted law has exactly the
    sample variance; ``nu`` maximises

    ``l(nu) = sum_i [ log g_nu((x_i - m)/c) - log c ]``

    (profile likelihood, bounded Brent search on ``[lo, hi]``).
    """
    from scipy.optimize import minimize_scalar

    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    m, s = x.mean(), x.std(ddof=1)
    if not s > 0:
        raise ValueError("zero-variance return series")
    z = (x - m) / s

    def nll(log_nu_m2: float) -> float:
        nu = 2.0 + math.exp(log_nu_m2)
        c = math.sqrt((nu - 2.0) / nu)
        return -float(np.sum(stats.t.logpdf(z / c, nu)) - z.size * math.log(c))

    res = minimize_scalar(nll, bounds=(math.log(lo - 2.0), math.log(hi - 2.0)), method="bounded",
                          options={"xatol": 1e-4})
    return float(2.0 + math.exp(res.x))


# ----------------------------------------------------------------- covariance
def ewma_weights(n: int, lam: float) -> np.ndarray:
    """Normalised exponential weights, oldest first: ``w_k ∝ (1-lam) lam^(n-1-k)``."""
    if not 0.0 < lam < 1.0:
        raise ValueError("EWMA lambda must be in (0, 1)")
    k = np.arange(n)[::-1]
    w = (1.0 - lam) * lam ** k
    return w / w.sum()


def covariance(returns: pd.DataFrame, method: str = "sample", lam: float = 0.94) -> tuple[np.ndarray, np.ndarray]:
    """Mean vector and covariance matrix of daily returns.

    * ``sample``: sample mean and unbiased sample covariance.
    * ``ewma``: RiskMetrics (J.P. Morgan/Reuters 1996, *RiskMetrics Technical
      Document*, 4th ed.) zero-mean exponentially weighted covariance
      ``Sigma = sum_k w_k r_{t-k} r_{t-k}'`` with normalised weights
      ``w_k ∝ (1-lam) lam^k``; the mean is set to zero as in RiskMetrics.
    """
    x = returns.to_numpy(dtype=float)
    if x.shape[0] < 2:
        raise ValueError("need at least two return observations")
    if method == "sample":
        return x.mean(axis=0), np.atleast_2d(np.cov(x, rowvar=False, ddof=1))
    if method == "ewma":
        w = ewma_weights(x.shape[0], lam)
        return np.zeros(x.shape[1]), (x * w[:, None]).T @ x
    raise ValueError(f"unknown covariance method {method!r} (use 'sample' or 'ewma')")


def ewma_variance_path(r: np.ndarray, lam: float, seed_obs: int = 30) -> np.ndarray:
    """RiskMetrics recursion ``s2_{t+1} = lam s2_t + (1 - lam) r_t^2``.

    Returns an array of length ``n + 1`` where element ``t`` is the variance
    forecast for day ``t`` made with information up to ``t - 1`` (element
    ``n`` is the forecast for the next, unobserved day). Seeded with the mean
    squared return of the first ``min(seed_obs, n)`` observations.
    """
    from scipy.signal import lfilter

    r = np.asarray(r, dtype=float)
    if not 0.0 < lam < 1.0:
        raise ValueError("EWMA lambda must be in (0, 1)")
    seed = float(np.mean(r[: max(1, min(seed_obs, r.size))] ** 2))
    y, _ = lfilter([1.0 - lam], [1.0, -lam], r * r, zi=[lam * seed])
    return np.concatenate([[seed], y])


def data_notes(provenance: list[dict[str, Any]]) -> list[str]:
    """Caveats implied by the data sources (e.g. Alpaca's split-only adjustment)."""
    notes: list[str] = []
    if any("alpaca" in str(p.get("source", "")).lower() for p in provenance):
        notes.append("Alpaca adj_close is split-adjusted only; dividends are excluded from returns.")
    return notes
