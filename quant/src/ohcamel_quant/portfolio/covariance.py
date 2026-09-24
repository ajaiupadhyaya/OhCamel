"""Covariance estimation for portfolio construction.

All estimators take a ``T x N`` frame of simple DAILY decimal returns (rows =
sessions, no NaN) and return a :class:`CovEstimate` whose ``cov`` is an
ANNUALIZED ``N x N`` :class:`pandas.DataFrame` (daily estimate x 252).

Estimators
----------
* ``sample``      -- unbiased sample covariance ``S = X_c' X_c / (T - 1)``.
* ``ewma``        -- RiskMetrics exponentially weighted covariance (J.P. Morgan /
  Reuters 1996, *RiskMetrics Technical Document*, 4th ed.), zero mean,
  ``Sigma = sum_k w_k r_{T-k} r_{T-k}'`` with ``w_k = (1-lambda) lambda^k``
  renormalised to sum to one over the sample.
* ``lw_constant_corr`` -- Ledoit & Wolf (2004), "Honey, I shrunk the sample
  covariance matrix", *Journal of Portfolio Management* 30(4): shrink towards the
  constant-correlation target ``F`` with the optimal intensity
  ``delta* = max(0, min(1, kappa / T))``, ``kappa = (pi - rho) / gamma``.
* ``lw_identity`` -- Ledoit & Wolf (2004), "A well-conditioned estimator for
  large-dimensional covariance matrices", *JMVA* 88(2): shrink towards
  ``mu I`` (``sklearn.covariance.LedoitWolf``).
* ``oas``         -- Chen, Wiesel, Eldar & Hero (2010), "Shrinkage algorithms for
  MMSE covariance estimation", *IEEE Trans. Signal Processing* 58(10), eq. (23).
* ``mp_denoise``  -- Marcenko-Pastur eigenvalue clipping, constant-residual
  method (Lopez de Prado 2020, *Machine Learning for Asset Managers*, ch. 2;
  Laloux, Cizeau, Bouchaud & Potters 1999).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

TRADING_DAYS = 252

Estimator = Literal["sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise"]
ESTIMATORS: tuple[str, ...] = ("sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise")

REFERENCES: dict[str, str] = {
    "sample": "Unbiased sample covariance (Markowitz 1952 inputs)",
    "ewma": "J.P. Morgan/Reuters (1996), RiskMetrics Technical Document, 4th ed.",
    "lw_constant_corr": "Ledoit & Wolf (2004), 'Honey, I shrunk the sample covariance matrix', JPM 30(4)",
    "lw_identity": "Ledoit & Wolf (2004), 'A well-conditioned estimator for large-dimensional "
                   "covariance matrices', JMVA 88(2)",
    "oas": "Chen, Wiesel, Eldar & Hero (2010), 'Shrinkage algorithms for MMSE covariance "
           "estimation', IEEE TSP 58(10)",
    "mp_denoise": "Lopez de Prado (2020), Machine Learning for Asset Managers, ch. 2 "
                  "(constant-residual eigenvalue method); Marcenko & Pastur (1967)",
}


@dataclass
class CovEstimate:
    """An annualized covariance estimate plus diagnostics."""

    method: str
    cov: pd.DataFrame
    shrinkage: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def reference(self) -> str:
        return REFERENCES.get(self.method, "")

    def correlation(self) -> pd.DataFrame:
        return cov_to_corr(self.cov)

    def condition_number(self) -> float:
        ev = np.linalg.eigvalsh(self.cov.to_numpy())
        return float(ev[-1] / ev[0]) if ev[0] > 0 else float("inf")


# ------------------------------------------------------------------ helpers
def _as_array(returns: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns must be a DataFrame (T x N)")
    x = returns.to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("returns contain NaN/inf; drop or align sessions first")
    t, n = x.shape
    if n < 2:
        raise ValueError("need at least two assets")
    if t < n + 2:
        raise ValueError(f"need more observations ({t}) than assets + 1 ({n + 1})")
    names = [str(c) for c in returns.columns]
    flat = [nm for nm, sd in zip(names, x.std(axis=0), strict=True) if not sd > 0]
    if flat:
        # correlations (LW constant-correlation target, MP spectrum, HRP distance) are undefined
        raise ValueError(f"zero return variance over the window for {', '.join(flat)} "
                         "(constant price / no trading); drop it from the universe")
    return x, names


def _frame(m: np.ndarray, names: list[str]) -> pd.DataFrame:
    m = 0.5 * (m + m.T)
    return pd.DataFrame(m, index=names, columns=names)


def cov_to_corr(cov: pd.DataFrame | np.ndarray) -> Any:
    """``C = D^{-1} Sigma D^{-1}``, ``D = diag(sqrt(diag Sigma))``."""
    arr = cov.to_numpy() if isinstance(cov, pd.DataFrame) else np.asarray(cov, float)
    sd = np.sqrt(np.diag(arr))
    c = arr / np.outer(sd, sd)
    c = np.clip(0.5 * (c + c.T), -1.0, 1.0)
    np.fill_diagonal(c, 1.0)
    if isinstance(cov, pd.DataFrame):
        return pd.DataFrame(c, index=cov.index, columns=cov.columns)
    return c


def nearest_psd(m: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    """Clip negative eigenvalues (Higham-style spectral projection)."""
    m = 0.5 * (m + m.T)
    w, v = np.linalg.eigh(m)
    if w.min() >= floor:
        return m
    w = np.maximum(w, floor * max(1.0, float(w.max())))
    return (v * w) @ v.T


# ------------------------------------------------------------------ estimators
def sample_cov(returns: pd.DataFrame) -> CovEstimate:
    """Unbiased sample covariance ``S = (1/(T-1)) sum_t (r_t - rbar)(r_t - rbar)'``, x252."""
    x, names = _as_array(returns)
    s = np.cov(x, rowvar=False, ddof=1)
    return CovEstimate("sample", _frame(s * TRADING_DAYS, names), params={"observations": x.shape[0]})


def ewma_cov(returns: pd.DataFrame, lam: float = 0.94) -> CovEstimate:
    """RiskMetrics EWMA covariance with decay ``lambda`` (zero-mean).

    ``Sigma_T = sum_{k=0}^{T-1} w_k r_{T-k} r_{T-k}'``, ``w_k = (1-lambda) lambda^k / (1 - lambda^T)``
    -- the recursion ``Sigma_t = lambda Sigma_{t-1} + (1-lambda) r_t r_t'`` unrolled
    over the sample with weights renormalised to one. RiskMetrics (1996) uses
    lambda = 0.94 for daily data; effective memory ``1/(1-lambda)`` sessions.
    """
    if not 0.0 < lam < 1.0:
        raise ValueError("EWMA lambda must be in (0, 1)")
    x, names = _as_array(returns)
    t = x.shape[0]
    k = np.arange(t)[::-1]  # most recent row has k = 0
    w = (1.0 - lam) * lam ** k
    w = w / w.sum()
    s = (x * w[:, None]).T @ x
    est = CovEstimate("ewma", _frame(s * TRADING_DAYS, names), params={"lambda": lam})
    est.params["effective_observations"] = float(1.0 / np.sum(w ** 2))
    est.notes.append("EWMA assumes zero daily mean (RiskMetrics convention).")
    return est


def lw_constant_correlation(returns: pd.DataFrame) -> CovEstimate:
    """Ledoit & Wolf (2004, JPM) shrinkage to the constant-correlation target.

    With de-meaned returns ``y_it = x_it - m_i`` and the MLE sample covariance
    ``s_ij = (1/T) sum_t y_it y_jt``:

    * target ``F``: ``f_ii = s_ii``, ``f_ij = rbar sqrt(s_ii s_jj)`` where
      ``rbar = 2/(N(N-1)) sum_{i<j} r_ij`` is the average sample correlation;
    * ``pi_ij = (1/T) sum_t (y_it y_jt - s_ij)^2``, ``pi = sum_ij pi_ij``;
    * ``theta_ii,ij = (1/T) sum_t (y_it^2 - s_ii)(y_it y_jt - s_ij)``,
      ``rho = sum_i pi_ii + sum_{i != j} (rbar/2)(sqrt(s_jj/s_ii) theta_ii,ij
      + sqrt(s_ii/s_jj) theta_jj,ij)``;
    * ``gamma = ||F - S||_F^2``, ``kappa = (pi - rho)/gamma``;
    * ``delta* = max(0, min(1, kappa / T))`` and ``Sigma = delta* F + (1-delta*) S``.
    """
    x, names = _as_array(returns)
    t, n = x.shape
    y = x - x.mean(axis=0)
    s = y.T @ y / t
    var = np.diag(s)
    sd = np.sqrt(var)
    r = s / np.outer(sd, sd)
    rbar = (r.sum() - n) / (n * (n - 1))
    f = rbar * np.outer(sd, sd)
    np.fill_diagonal(f, var)
    y2 = y ** 2
    pi_mat = (y2.T @ y2) / t - s ** 2
    pi_hat = float(pi_mat.sum())
    # theta_ii,ij = (1/T) sum_t y_it^3 y_jt - s_ii s_ij   (expanded product)
    theta = ((y ** 3).T @ y) / t - var[:, None] * s
    ratio = np.outer(1.0 / sd, sd)  # sqrt(s_jj / s_ii)
    term = ratio * theta + ratio.T * theta.T
    np.fill_diagonal(term, 0.0)
    rho_hat = float(np.trace(pi_mat) + 0.5 * rbar * term.sum())
    gamma_hat = float(np.sum((f - s) ** 2))
    kappa = (pi_hat - rho_hat) / gamma_hat if gamma_hat > 0 else np.inf
    delta = float(max(0.0, min(1.0, kappa / t)))
    shrunk = delta * f + (1.0 - delta) * s
    est = CovEstimate("lw_constant_corr", _frame(shrunk * TRADING_DAYS, names), shrinkage=delta,
                      params={"average_correlation": float(rbar), "pi": pi_hat, "rho": rho_hat,
                              "gamma": gamma_hat, "kappa": float(kappa)})
    est.notes.append("Uses the MLE (1/T) sample covariance as in Ledoit & Wolf (2004).")
    return est


def lw_identity(returns: pd.DataFrame) -> CovEstimate:
    """Ledoit & Wolf (2004, JMVA) linear shrinkage towards ``mu I``, ``mu = tr(S)/N``.

    ``Sigma = (1 - delta) S + delta mu I`` with ``delta = min(1, b^2 / d^2)``,
    ``d^2 = ||S - mu I||^2``, ``b^2 = min(d^2, (1/T^2) sum_t ||y_t y_t' - S||^2)``
    (Frobenius norms normalised by N). Computed with scikit-learn.
    """
    from sklearn.covariance import LedoitWolf

    x, names = _as_array(returns)
    lw = LedoitWolf(assume_centered=False).fit(x)
    return CovEstimate("lw_identity", _frame(lw.covariance_ * TRADING_DAYS, names),
                       shrinkage=float(lw.shrinkage_), params={"target": "scaled identity"})


def oas(returns: pd.DataFrame) -> CovEstimate:
    """Oracle Approximating Shrinkage (Chen et al. 2010, eq. 23).

    With the MLE sample covariance ``S`` (T observations, p assets) and target
    ``F = (tr S / p) I``:

    ``rho = min(1, [(1 - 2/p) tr(S^2) + tr(S)^2] / [(T + 1 - 2/p)(tr(S^2) - tr(S)^2 / p)])``,
    ``Sigma = (1 - rho) S + rho F``.
    """
    x, names = _as_array(returns)
    t, p = x.shape
    y = x - x.mean(axis=0)
    s = y.T @ y / t
    tr = float(np.trace(s))
    tr2 = float(np.sum(s * s))  # tr(S^2) for symmetric S
    num = (1.0 - 2.0 / p) * tr2 + tr ** 2
    den = (t + 1.0 - 2.0 / p) * (tr2 - tr ** 2 / p)
    rho = 1.0 if den <= 0 else float(min(1.0, num / den))
    shrunk = (1.0 - rho) * s + rho * (tr / p) * np.eye(p)
    return CovEstimate("oas", _frame(shrunk * TRADING_DAYS, names), shrinkage=rho,
                       params={"target": "scaled identity"})


# ------------------------------------------------------------------ Marcenko-Pastur
def mp_bounds(sigma2: float, q: float) -> tuple[float, float]:
    """``lambda_{+-} = sigma^2 (1 +- sqrt(1/q))^2`` with ``q = T/N``."""
    return sigma2 * (1 - np.sqrt(1.0 / q)) ** 2, sigma2 * (1 + np.sqrt(1.0 / q)) ** 2


def mp_pdf(x: np.ndarray, sigma2: float, q: float) -> np.ndarray:
    """Marcenko-Pastur density ``q/(2 pi sigma^2 x) sqrt((l+ - x)(x - l-))`` on [l-, l+], 0 outside."""
    lo, hi = mp_bounds(sigma2, q)
    x = np.asarray(x, float)
    out = np.zeros_like(x)
    inside = (x > lo) & (x < hi)
    xi = x[inside]
    out[inside] = q / (2 * np.pi * sigma2 * xi) * np.sqrt((hi - xi) * (xi - lo))
    return out


def _kde(obs: np.ndarray, x: np.ndarray, bandwidth: float) -> np.ndarray:
    z = (x[:, None] - obs[None, :]) / bandwidth
    return np.exp(-0.5 * z ** 2).sum(axis=1) / (obs.size * bandwidth * np.sqrt(2 * np.pi))


def fit_mp_sigma2(eigenvalues: np.ndarray, q: float, bandwidth: float = 0.01, points: int = 1000) -> float:
    """Fit the MP noise variance ``sigma^2`` by least squares between the
    Gaussian KDE of the empirical eigenvalues and the MP density (Lopez de
    Prado 2020, snippet 2.4 ``findMaxEval``): ``min_{sigma^2} sum_x (pdf_MP(x) - kde(x))^2``."""
    ev = np.sort(np.asarray(eigenvalues, float))

    def sse(s2: float) -> float:
        lo, hi = mp_bounds(s2, q)
        x = np.linspace(lo, hi, points)
        return float(np.sum((mp_pdf(x, s2, q) - _kde(ev, x, bandwidth)) ** 2))

    res = minimize_scalar(sse, bounds=(1e-5, 1 - 1e-5), method="bounded")
    return float(res.x)


def spectrum(returns: pd.DataFrame, bandwidth: float = 0.01) -> dict[str, Any]:
    """Eigen-spectrum of the sample CORRELATION matrix with the fitted MP law.

    Returns eigenvalues (descending), ``q = T/N``, fitted ``sigma^2``, MP bounds
    ``lambda_-``/``lambda_+``, the number of 'signal' eigenvalues above
    ``lambda_+``, the share of variance they explain, and the MP density on a grid
    for charting.
    """
    x, _ = _as_array(returns)
    t, n = x.shape
    c = np.corrcoef(x, rowvar=False)
    ev = np.sort(np.linalg.eigvalsh(c))[::-1]
    q = t / n
    s2 = fit_mp_sigma2(ev, q, bandwidth)
    lo, hi = mp_bounds(s2, q)
    n_facts = int(np.sum(ev > hi))
    grid = np.linspace(0.0, max(float(ev[0]), hi) * 1.05, 200)
    return {
        "eigenvalues": ev, "q": q, "sigma2": s2, "lambda_minus": lo, "lambda_plus": hi,
        "n_signal": n_facts, "signal_variance_share": float(ev[:n_facts].sum() / ev.sum()),
        "mp_grid": grid, "mp_density": mp_pdf(grid, s2, q), "bandwidth": bandwidth,
        "observations": t, "assets": n,
    }


def mp_denoise(returns: pd.DataFrame, bandwidth: float = 0.01) -> CovEstimate:
    """Constant-residual eigenvalue denoising (Lopez de Prado 2020, snippet 2.5).

    1. ``C = V diag(lambda) V'`` (sample correlation), fit ``sigma^2`` of the MP law.
    2. ``k`` = number of eigenvalues above ``lambda_+``; the remaining ``N-k``
       'noise' eigenvalues are replaced by their average (trace preserved).
    3. ``C~ = V diag(lambda~) V'`` rescaled to unit diagonal,
       ``Sigma~ = D C~ D`` with sample standard deviations ``D``.
    """
    x, names = _as_array(returns)
    t, n = x.shape
    spec = spectrum(returns, bandwidth)
    c = np.corrcoef(x, rowvar=False)
    w, v = np.linalg.eigh(c)
    order = np.argsort(w)[::-1]
    w, v = w[order], v[:, order]
    k = max(1, spec["n_signal"])
    w2 = w.copy()
    if k < n:
        w2[k:] = w[k:].sum() / (n - k)
    c2 = (v * w2) @ v.T
    c2 = cov_to_corr(c2)
    sd = x.std(axis=0, ddof=1)
    cov = c2 * np.outer(sd, sd)
    est = CovEstimate("mp_denoise", _frame(cov * TRADING_DAYS, names),
                      params={"sigma2": spec["sigma2"], "lambda_plus": spec["lambda_plus"],
                              "n_signal": int(k), "q": spec["q"], "bandwidth": bandwidth},
                      extra={"spectrum": spec, "denoised_eigenvalues": np.sort(w2)[::-1]})
    if n < 20:
        est.notes.append(f"Only {n} assets: the Marcenko-Pastur law is asymptotic (N, T -> inf); "
                         "with few assets the noise/signal split is coarse.")
    if spec["n_signal"] == 0:
        est.notes.append("No eigenvalue exceeded lambda_+; kept the largest as the single signal factor.")
    return est


def estimate_covariance(returns: pd.DataFrame, method: str = "lw_constant_corr",
                        ewma_lambda: float = 0.94, mp_bandwidth: float = 0.01) -> CovEstimate:
    """Dispatch to one of :data:`ESTIMATORS`."""
    if method == "sample":
        return sample_cov(returns)
    if method == "ewma":
        return ewma_cov(returns, ewma_lambda)
    if method == "lw_constant_corr":
        return lw_constant_correlation(returns)
    if method == "lw_identity":
        return lw_identity(returns)
    if method == "oas":
        return oas(returns)
    if method == "mp_denoise":
        return mp_denoise(returns, mp_bandwidth)
    raise ValueError(f"unknown covariance estimator {method!r}; choose from {', '.join(ESTIMATORS)}")
