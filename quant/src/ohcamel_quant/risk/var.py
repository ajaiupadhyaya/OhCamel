"""Unconditional and EWMA Value-at-Risk / Expected-Shortfall models.

Every function takes a 1-D array of DECIMAL daily portfolio returns and returns
a :class:`~ohcamel_quant.risk.core.RiskEstimate` whose ``var``/``es`` are
positive loss fractions (see :mod:`ohcamel_quant.risk.core` for the sign
convention). ``alpha`` is the confidence level, ``p = 1 - alpha`` the tail
probability, ``h`` the horizon in trading days.

GARCH-family and filtered-historical-simulation models live in
:mod:`ohcamel_quant.risk.garch`.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from .core import (
    RiskEstimate,
    empirical_var_es,
    ewma_variance_path,
    fit_t_dof,
    std_t_es,
    std_t_quantile,
    validate_alpha,
)


def _clean(r: np.ndarray, min_obs: int = 30) -> np.ndarray:
    x = np.asarray(r, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < min_obs:
        raise ValueError(f"need at least {min_obs} return observations, got {x.size}")
    return x


def _sample_notes(n: int) -> list[str]:
    return [f"only {n} observations (< 250); estimates are imprecise"] if n < 250 else []


# -------------------------------------------------------------- (1) historical
def overlapping_returns(r: np.ndarray, h: int) -> np.ndarray:
    """Overlapping compounded h-day returns ``prod_{j<h}(1 + r_{t+j}) - 1``."""
    r = np.asarray(r, dtype=float)
    if h <= 1:
        return r.copy()
    logg = np.log1p(r)
    c = np.concatenate([[0.0], np.cumsum(logg)])
    return np.expm1(c[h:] - c[:-h])


def historical(r: np.ndarray, alpha: float = 0.99, horizon: int = 1) -> RiskEstimate:
    """Historical simulation (non-parametric).

    Losses ``L_t = -r_t`` (or overlapping compounded ``h``-day losses when
    ``h > 1``) are ranked; with ``k = ceil(n (1 - alpha))``,

    ``VaR = L_(k)`` (the k-th largest loss),  ``ES = (1/k) sum_{j<=k} L_(j)``.

    Reference: Jorion (2007), *Value at Risk*, 3rd ed., ch. 10; Acerbi & Tasche
    (2002) for the tail-mean ES estimator.
    """
    alpha = validate_alpha(alpha)
    x = _clean(r)
    notes = _sample_notes(x.size)
    xs = overlapping_returns(x, horizon)
    if horizon > 1:
        notes.append(
            f"{horizon}-day historical VaR uses {xs.size} OVERLAPPING {horizon}-day windows: "
            "observations are serially dependent, so the effective sample is ~n/h and the "
            "estimate is noisier than its count suggests."
        )
    var, es = empirical_var_es(-xs, alpha)
    k = max(1, math.ceil(xs.size * (1 - alpha) - 1e-9))
    return RiskEstimate(
        "historical", alpha, horizon, var, es,
        params={"n_scenarios": int(xs.size), "tail_obs": k,
                "quantile_convention": "VaR = k-th largest loss, k = ceil(n(1-alpha)); ES = mean of the k largest losses"},
        notes=notes,
        reference="Jorion (2007) Value at Risk, 3rd ed.; Acerbi & Tasche (2002) J. Banking & Finance 26(7)",
    )


# ---------------------------------------------------------------- (2) Gaussian
def gaussian(r: np.ndarray, alpha: float = 0.99, horizon: int = 1) -> RiskEstimate:
    """Parametric (variance-covariance) normal VaR/ES.

    With sample mean ``mu`` and standard deviation ``sigma`` and iid scaling,

    ``VaR = -h mu + sqrt(h) sigma z_alpha``,
    ``ES  = -h mu + sqrt(h) sigma phi(z_alpha) / (1 - alpha)``

    where ``z_alpha = Phi^{-1}(alpha)``. Reference: J.P. Morgan/Reuters (1996),
    *RiskMetrics Technical Document*; Jorion (2007) ch. 5.
    """
    alpha = validate_alpha(alpha)
    x = _clean(r)
    mu, sigma = float(x.mean()), float(x.std(ddof=1))
    z = float(stats.norm.ppf(alpha))
    sh = math.sqrt(horizon)
    var = -horizon * mu + sh * sigma * z
    es = -horizon * mu + sh * sigma * float(stats.norm.pdf(z)) / (1 - alpha)
    notes = _sample_notes(x.size)
    if horizon > 1:
        notes.append("h-day figures use iid square-root-of-time scaling (mean x h, sigma x sqrt(h)).")
    return RiskEstimate(
        "gaussian", alpha, horizon, var, es,
        params={"mu": mu, "sigma": sigma, "z": z, "n": int(x.size)}, notes=notes,
        reference="J.P. Morgan/Reuters (1996) RiskMetrics Technical Document, 4th ed.",
    )


# --------------------------------------------------------------- (3) Student-t
def student_t(r: np.ndarray, alpha: float = 0.99, horizon: int = 1,
              nu: float | None = None) -> RiskEstimate:
    """Student-t VaR/ES with MLE degrees of freedom and variance matching.

    ``nu`` maximises the t likelihood with location = sample mean and scale
    ``c = sigma sqrt((nu-2)/nu)`` so the fitted variance equals the sample
    variance (:func:`~ohcamel_quant.risk.core.fit_t_dof`). Then

    ``VaR = -h mu + sqrt(h) sigma q*_nu(alpha)``,  ``ES = -h mu + sqrt(h) sigma ES*_nu(alpha)``

    with the unit-variance t quantile ``q*`` and the closed-form t shortfall
    ``ES* = g_nu(q)/(1-alpha) (nu + q^2)/(nu - 1) sqrt((nu-2)/nu)``, ``q = t_nu^{-1}(alpha)``
    (McNeil, Frey & Embrechts 2015, QRM, Ex. 2.15). Reference for the model:
    Lucas & Klaassen (1998); Jorion (2007) ch. 5.
    """
    alpha = validate_alpha(alpha)
    x = _clean(r)
    mu, sigma = float(x.mean()), float(x.std(ddof=1))
    nu = fit_t_dof(x) if nu is None else float(nu)
    sh = math.sqrt(horizon)
    q, e = std_t_quantile(alpha, nu), std_t_es(alpha, nu)
    notes = _sample_notes(x.size)
    if nu >= 199:
        notes.append("fitted dof hit the upper bound: returns are close to Gaussian")
    if horizon > 1:
        notes.append("h-day figures scale the 1-day t quantile by sqrt(h); a sum of t variables "
                     "is not t-distributed, so this is an approximation (conservative in the far tail).")
    return RiskEstimate(
        "student_t", alpha, horizon, -horizon * mu + sh * sigma * q, -horizon * mu + sh * sigma * e,
        params={"mu": mu, "sigma": sigma, "nu": nu, "n": int(x.size),
                "scale": sigma * math.sqrt((nu - 2) / nu)},
        notes=notes,
        reference="McNeil, Frey & Embrechts (2015) Quantitative Risk Management, 2nd ed., Ex. 2.15",
    )


# ---------------------------------------------------------- (4) Cornish-Fisher
def cornish_fisher_z(z: np.ndarray | float, skew: np.ndarray | float, exkurt: np.ndarray | float):
    """Cornish-Fisher quantile expansion (Cornish & Fisher 1937):

    ``g = z + (z^2 - 1) S/6 + (z^3 - 3z) K/24 - (2 z^3 - 5z) S^2/36``

    ``S`` = skewness, ``K`` = EXCESS kurtosis, ``z`` a standard normal quantile.
    """
    return (z + (z * z - 1) * skew / 6 + (z ** 3 - 3 * z) * exkurt / 24
            - (2 * z ** 3 - 5 * z) * skew * skew / 36)


def _normal_partial_moments(g, qmax: int) -> list:
    """``J_q(g) = int_{-inf}^g u^q phi(u) du`` for q = 0..qmax.

    Integration by parts: ``J_0 = Phi(g)``, ``J_1 = -phi(g)``,
    ``J_q = -g^{q-1} phi(g) + (q-1) J_{q-2}``.
    """
    phi, Phi = stats.norm.pdf(g), stats.norm.cdf(g)
    J = [Phi, -phi]
    for q in range(2, qmax + 1):
        J.append(-(g ** (q - 1)) * phi + (q - 1) * J[q - 2])
    return J


def cornish_fisher_tail_mean(p, skew, exkurt):
    """Standardised left-tail mean ``M = E_G[X | X <= g_p]`` of the Edgeworth law.

    Boudt, Peterson & Croux (2008), "Estimation and decomposition of downside
    risk for portfolios with non-normal returns", J. of Risk 11(2), eq. (9):
    with the Cornish-Fisher quantile ``g = g_p`` and the second-order Edgeworth
    density ``e(x) = phi(x)[1 + S/6 He_3(x) + K/24 He_4(x) + S^2/72 He_6(x)]``,

    ``M = (1/p) int_{-inf}^g x e(x) dx
        = (1/p)[ J_1 + S/6 (J_4 - 3 J_2) + K/24 (J_5 - 6 J_3 + 3 J_1)
                 + S^2/72 (J_7 - 15 J_5 + 45 J_3 - 15 J_1) ]``.
    """
    z = stats.norm.ppf(p)
    g = cornish_fisher_z(z, skew, exkurt)
    J = _normal_partial_moments(g, 7)
    m = (J[1] + skew / 6 * (J[4] - 3 * J[2]) + exkurt / 24 * (J[5] - 6 * J[3] + 3 * J[1])
         + skew * skew / 72 * (J[7] - 15 * J[5] + 45 * J[3] - 15 * J[1]))
    return m / p, g


def cornish_fisher_valid(skew: float, exkurt: float) -> bool:
    """Whether the Cornish-Fisher transform is monotone (a valid quantile function).

    ``g'(z) = a z^2 + b z + c`` with ``a = K/8 - S^2/6``, ``b = S/3``,
    ``c = 1 - K/8 + 5 S^2/36``; the expansion is increasing for every ``z`` iff
    ``a > 0`` and ``b^2 - 4ac <= 0`` (Maillard 2012, "A User's Guide to the
    Cornish Fisher Expansion", SSRN 1997178; the exact case ``S = K = 0`` is
    the identity and valid).
    """
    a = exkurt / 8 - skew * skew / 6
    b = skew / 3
    c = 1 - exkurt / 8 + 5 * skew * skew / 36
    if abs(a) < 1e-15 and abs(b) < 1e-15:
        return c > 0
    return bool(a > 0 and b * b - 4 * a * c <= 0)


def cornish_fisher(r: np.ndarray, alpha: float = 0.99, horizon: int = 1) -> RiskEstimate:
    """Modified (Cornish-Fisher) VaR and modified ES.

    ``mVaR = -(mu_h + sigma_h g_p)`` with ``g_p`` the Cornish-Fisher expansion
    of ``z_p = Phi^{-1}(1 - alpha)`` (Zangari 1996, RiskMetrics Monitor Q4;
    Favre & Galeano 2002, J. Alternative Investments 5(2)).
    ``mES = -(mu_h + sigma_h min(M, g_p))`` with the Edgeworth tail mean ``M``
    of Boudt, Peterson & Croux (2008) (the ``min`` guarantees ES >= VaR).

    Multi-day moments aggregate as for iid sums: ``mu_h = h mu``,
    ``sigma_h = sqrt(h) sigma``, ``S_h = S/sqrt(h)``, ``K_h = K/h``.
    Sample skewness/excess kurtosis are the bias-corrected estimators.
    """
    alpha = validate_alpha(alpha)
    x = _clean(r)
    mu, sigma = float(x.mean()), float(x.std(ddof=1))
    S = float(stats.skew(x, bias=False))
    K = float(stats.kurtosis(x, fisher=True, bias=False))
    h = horizon
    Sh, Kh = S / math.sqrt(h), K / h
    p = 1 - alpha
    M, g = cornish_fisher_tail_mean(p, Sh, Kh)
    M, g = float(M), float(g)
    var = -(h * mu + math.sqrt(h) * sigma * g)
    es = -(h * mu + math.sqrt(h) * sigma * min(M, g))
    valid = cornish_fisher_valid(Sh, Kh)
    notes = _sample_notes(x.size)
    if not valid:
        # Outside the domain the Edgeworth tail mean is meaningless and the min()
        # clamp collapses ES onto VaR; report ES as unavailable rather than ES == VaR.
        es = math.nan
        notes.append(
            "skewness/kurtosis lie OUTSIDE the Cornish-Fisher domain of validity (Maillard 2012): "
            "the expansion is not monotone, so modified VaR is unreliable (flagged valid=false) "
            "and modified ES is not reported."
        )
    return RiskEstimate(
        "cornish_fisher", alpha, horizon, var, es,
        params={"mu": mu, "sigma": sigma, "skew": S, "excess_kurtosis": K,
                "z_normal": float(stats.norm.ppf(p)), "z_cf": g, "tail_mean_std": M,
                "valid_domain": valid, "n": int(x.size)},
        notes=notes,
        valid=valid,
        reference=("Zangari (1996); Favre & Galeano (2002) J. Alt. Inv.; Boudt, Peterson & Croux "
                   "(2008) J. Risk; Maillard (2012) SSRN 1997178"),
    )


# ------------------------------------------------------------------- (5) EWMA
def ewma(r: np.ndarray, alpha: float = 0.99, horizon: int = 1, lam: float = 0.94) -> RiskEstimate:
    """RiskMetrics EWMA volatility with zero mean and normal quantile.

    ``sigma^2_{t+1} = lam sigma^2_t + (1 - lam) r_t^2`` (J.P. Morgan/Reuters
    1996; lam = 0.94 is the RiskMetrics daily decay).

    ``VaR = sqrt(h) sigma_{t+1} z_alpha``, ``ES = sqrt(h) sigma_{t+1} phi(z_alpha)/(1 - alpha)``.
    """
    alpha = validate_alpha(alpha)
    x = _clean(r)
    path = ewma_variance_path(x, lam)
    sigma = math.sqrt(path[-1])
    z = float(stats.norm.ppf(alpha))
    sh = math.sqrt(horizon)
    notes = _sample_notes(x.size)
    if horizon > 1:
        notes.append("h-day EWMA VaR uses sqrt(h) scaling (RiskMetrics convention: EWMA variance is a martingale).")
    return RiskEstimate(
        "ewma", alpha, horizon, sh * sigma * z, sh * sigma * float(stats.norm.pdf(z)) / (1 - alpha),
        params={"lambda": lam, "sigma_next": sigma, "sigma_next_annualized": sigma * math.sqrt(252),
                "effective_obs": 1 / (1 - lam), "z": z},
        notes=notes,
        reference="J.P. Morgan/Reuters (1996) RiskMetrics Technical Document, 4th ed.",
    )


# -------------------------------------------------------------------- (8) EVT
def _gpd_profile_nll(theta: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Profile negative log-likelihood of the GPD in ``theta = xi/beta``.

    With ``k(theta) = mean log(1 + theta y)`` the likelihood is maximised over
    ``xi`` at ``xi = k(theta)`` (``beta = xi/theta``), leaving
    ``-l*(theta)/N = log(k/theta) + k + 1`` (Grimshaw 1993, Technometrics 35).
    """
    t = np.atleast_1d(theta)[:, None]
    k = np.log1p(t * y[None, :]).mean(axis=1)
    return np.log(k / t[:, 0]) + k + 1.0


def fit_gpd(y: np.ndarray) -> tuple[float, float]:
    """MLE of the generalised Pareto distribution to exceedances ``y > 0``.

    ``G_{xi,beta}(y) = 1 - (1 + xi y / beta)^{-1/xi}``. The two-parameter
    likelihood is profiled to one dimension (Grimshaw 1993): a grid over the
    admissible ``theta = xi/beta in (-1/max y, inf)`` locates the global
    optimum, refined by bounded Brent search. Returns ``(xi, beta)``; the
    exponential limit (``xi = 0``) is taken when it is not beaten.
    """
    from scipy.optimize import minimize_scalar

    y = np.asarray(y, dtype=float)
    y = y[y > 0]
    if y.size < 3:
        raise ValueError("need at least 3 positive exceedances to fit a GPD")
    ybar, ymax = y.mean(), y.max()
    lo = -1.0 / ymax * (1 - 1e-6)
    hi = 100.0 / ybar
    grid = np.concatenate([np.linspace(lo, -1e-6 / ybar, 40), np.geomspace(1e-6 / ybar, hi, 60)])
    vals = _gpd_profile_nll(grid, y)
    exp_nll = np.log(ybar) + 1.0                      # xi = 0: exponential with beta = ybar
    i = int(np.nanargmin(vals))
    a, b = grid[max(i - 1, 0)], grid[min(i + 1, grid.size - 1)]
    if a < 0 < b:
        a, b = (a, -1e-9 / ybar) if grid[i] < 0 else (1e-9 / ybar, b)
    res = minimize_scalar(lambda t: float(_gpd_profile_nll(np.array([t]), y)[0]), bounds=(a, b),
                          method="bounded", options={"xatol": 1e-10 / ybar})
    th = float(res.x)
    best = float(res.fun)
    if not np.isfinite(best) or exp_nll <= best:
        return 0.0, float(ybar)
    xi = float(np.mean(np.log1p(th * y)))
    return xi, xi / th


def gpd_var_es(u: float, xi: float, beta: float, n: int, nu: int, alpha: float) -> tuple[float, float]:
    """Tail estimators of McNeil & Frey (2000) / Smith (1987):

    ``VaR = u + (beta/xi)[((n/N_u)(1 - alpha))^{-xi} - 1]``  (``xi -> 0``: ``u - beta log(n(1-alpha)/N_u)``)
    ``ES  = VaR/(1 - xi) + (beta - xi u)/(1 - xi)``          (finite for ``xi < 1``).
    """
    ratio = n * (1 - alpha) / nu
    if abs(xi) < 1e-8:
        var = u - beta * math.log(ratio)
    else:
        var = u + beta / xi * (ratio ** (-xi) - 1)
    es = (var + beta - xi * u) / (1 - xi) if xi < 1 else math.inf
    return var, es


def evt_pot(r: np.ndarray, alpha: float = 0.99, horizon: int = 1,
            threshold_quantile: float = 0.90) -> RiskEstimate:
    """Peaks-over-threshold EVT: GPD fitted by maximum likelihood to the loss tail.

    Threshold ``u`` = the ``threshold_quantile`` empirical quantile of losses
    (default 90th percentile, the choice of McNeil & Frey 2000, "Estimation of
    tail-related risk measures for heteroscedastic financial time series: an
    extreme value approach", J. Empirical Finance 7). Exceedances ``y = L - u``
    for ``L > u`` are fitted with a GPD(xi, beta); VaR/ES from
    :func:`gpd_var_es`. Asymptotic standard errors (Smith 1985, valid for
    ``xi > -1/2``): ``Var(xi) = (1+xi)^2/N_u``, ``Var(beta) = 2 beta^2 (1+xi)/N_u``.

    Multi-day: square-root-of-time scaling, stated as a caveat (for heavy tails
    the alpha-root rule ``h^xi`` of Danielsson & de Vries 1997 may be more apt).
    """
    alpha = validate_alpha(alpha)
    if not 0.5 <= threshold_quantile < 1:
        raise ValueError("EVT threshold quantile must be in [0.5, 1)")
    x = _clean(r)
    L = -x
    n = L.size
    u = float(np.quantile(L, threshold_quantile))
    y = L[L > u] - u
    nu_exc = int(y.size)
    notes = _sample_notes(n)
    if nu_exc < 10:
        raise ValueError(f"EVT: only {nu_exc} exceedances over the threshold; cannot fit a GPD")
    if nu_exc < 50:
        notes.append(f"only {nu_exc} threshold exceedances: GPD parameters are imprecise "
                     "(McNeil & Frey recommend on the order of 100).")
    if 1 - alpha > (nu_exc / n):
        notes.append("confidence level is not beyond the threshold: the GPD tail formula is extrapolating inward")
    xi, beta = fit_gpd(y)
    var, es = gpd_var_es(u, xi, beta, n, nu_exc, alpha)
    se_xi = (1 + xi) / math.sqrt(nu_exc) if xi > -0.5 else math.nan
    se_beta = beta * math.sqrt(2 * (1 + xi) / nu_exc) if xi > -0.5 else math.nan
    if xi >= 1:
        notes.append("xi >= 1: the loss tail has infinite mean, ES is not finite")
    sh = math.sqrt(horizon)
    if horizon > 1:
        notes.append("h-day EVT figures use sqrt(h) scaling; with xi > 0 the alpha-root rule h^xi "
                     "(Danielsson & de Vries 1997) is an alternative.")
    return RiskEstimate(
        "evt_pot", alpha, horizon, var * sh, es * sh,
        params={"threshold": u, "threshold_quantile": threshold_quantile, "exceedances": nu_exc,
                "xi": xi, "beta": beta, "xi_se": se_xi, "beta_se": se_beta, "n": n,
                "tail_index": (1 / xi) if xi > 0 else None},
        notes=notes,
        reference="McNeil & Frey (2000) J. Empirical Finance 7; Smith (1987) Annals of Statistics 15",
    )
