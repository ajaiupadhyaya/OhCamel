"""GARCH-family conditional VaR/ES and filtered historical simulation.

Models (fitted with the ``arch`` package on PERCENT returns for numerical
stability; results are converted back to decimals):

* GARCH(1,1) (Bollerslev 1986, "Generalized autoregressive conditional
  heteroskedasticity", J. Econometrics 31):
  ``r_t = mu + eps_t``, ``eps_t = sigma_t z_t``,
  ``sigma^2_t = omega + alpha eps^2_{t-1} + beta sigma^2_{t-1}``.
* GJR-GARCH(1,1,1) (Glosten, Jagannathan & Runkle 1993, J. Finance 48):
  ``sigma^2_t = omega + (alpha + gamma 1[eps_{t-1} < 0]) eps^2_{t-1} + beta sigma^2_{t-1}``.

Innovations ``z_t`` are unit-variance Student-t with ``nu`` estimated jointly
(Bollerslev 1987). Persistence is ``alpha + gamma/2 + beta`` (``gamma/2``
because the standardized t is symmetric).

Filtered historical simulation (Barone-Adesi, Giannopoulos & Vosper 1999,
"VaR without correlations for portfolios of derivative securities",
J. Futures Markets 19; Hull & White 1998, "Incorporating volatility updating
into the historical simulation method for VaR", J. Risk 1): standardized
residuals ``z_t = eps_t / sigma_t`` are rescaled by the forecast volatility,
``r*_{t+1} = mu + sigma_{t+1} z_j``, and VaR/ES are read off that empirical
distribution. Multi-day FHS bootstraps whole GARCH paths from the residuals
(a numerical method on the fitted, real-data model).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .core import RiskEstimate, empirical_var_es, std_t_es, std_t_quantile, validate_alpha

KINDS = {"garch": 0, "gjr": 1}
REFERENCES = {
    "garch": "Bollerslev (1986) J. Econometrics 31; Student-t innovations: Bollerslev (1987) REStat 69",
    "gjr": "Glosten, Jagannathan & Runkle (1993) J. Finance 48; Student-t innovations: Bollerslev (1987)",
}

#: Persistence at or above which the fit is treated as near-unit-root: the
#: long-run variance omega / (1 - P) divides by a tiny, poorly estimated number
#: (Engle & Bollerslev 1986, "Modelling the persistence of conditional variances").
NEAR_UNIT_ROOT = 0.995


def near_unit_root_note(fit_kind: str, persistence: float) -> str:
    """Caveat for a fit with ``alpha + gamma/2 + beta >= NEAR_UNIT_ROOT``."""
    alt = "EWMA (RiskMetrics)" if fit_kind == "gjr" else "GJR-GARCH or EWMA (RiskMetrics)"
    return (f"{fit_kind.upper()} persistence {persistence:.4f} >= {NEAR_UNIT_ROOT}: near unit root "
            "(IGARCH-like). The long-run variance omega/(1-P) and the half-life are poorly identified, "
            f"so long-horizon forecasts are unreliable; prefer {alt} here.")


@dataclass
class GarchFit:
    """A fitted (GJR-)GARCH(1,1)-t model. Parameters are in PERCENT units."""

    kind: str
    mu: float
    omega: float
    alpha: float
    gamma: float
    beta: float
    nu: float
    std_err: dict[str, float]
    loglik: float
    aic: float
    bic: float
    converged: bool
    index: pd.DatetimeIndex | None
    cond_vol: np.ndarray            # decimal daily sigma_t, aligned with the input returns
    std_resid: np.ndarray           # z_t
    next_variance_pct: float        # sigma^2_{T+1}, percent^2
    last_eps_pct: float
    last_var_pct: float
    result: Any = field(default=None, repr=False)
    # memo of bootstrapped h-day FHS returns keyed on (horizon, simulations, seed),
    # so several confidence levels reuse one simulation
    fhs_cache: dict[tuple[int, int, int], tuple[np.ndarray, int]] = field(default_factory=dict, repr=False)

    @property
    def persistence(self) -> float:
        return self.alpha + 0.5 * self.gamma + self.beta

    @property
    def unconditional_variance_pct(self) -> float:
        p = self.persistence
        return self.omega / (1 - p) if p < 1 else math.inf

    @property
    def half_life(self) -> float:
        p = self.persistence
        return math.log(0.5) / math.log(p) if 0 < p < 1 else math.inf

    def params_dict(self) -> dict[str, Any]:
        uv = self.unconditional_variance_pct
        return {
            "model": self.kind, "mu_pct": self.mu, "omega": self.omega, "alpha": self.alpha,
            "gamma": self.gamma, "beta": self.beta, "nu": self.nu,
            "persistence": self.persistence, "half_life_days": self.half_life,
            "unconditional_vol_annualized": math.sqrt(uv * 252) / 100 if math.isfinite(uv) else None,
            "next_day_vol": math.sqrt(self.next_variance_pct) / 100,
            "std_err": self.std_err, "loglik": self.loglik, "aic": self.aic, "bic": self.bic,
            "converged": self.converged,
            "near_unit_root": self.persistence >= NEAR_UNIT_ROOT,
        }


def fit_garch(returns: np.ndarray | pd.Series, kind: str = "gjr",
              starting_values: np.ndarray | None = None) -> GarchFit:
    """Fit a GARCH(1,1)-t (``kind='garch'``) or GJR-GARCH(1,1,1)-t (``'gjr'``) by QMLE/MLE."""
    from arch import arch_model

    if kind not in KINDS:
        raise ValueError(f"unknown GARCH kind {kind!r}; use 'garch' or 'gjr'")
    idx = returns.index if isinstance(returns, pd.Series) else None
    r = np.asarray(returns, dtype=float)
    if r.size < 100 or not np.all(np.isfinite(r)):
        raise ValueError("GARCH needs at least 100 finite return observations")
    model = arch_model(100.0 * r, mean="Constant", vol="GARCH", p=1, o=KINDS[kind], q=1,
                       dist="t", rescale=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = model.fit(disp="off", starting_values=starting_values, options={"maxiter": 300})
    pr = res.params
    gamma = float(pr.get("gamma[1]", 0.0))
    mu, omega, a, b, nu = (float(pr["mu"]), float(pr["omega"]), float(pr["alpha[1]"]),
                           float(pr["beta[1]"]), float(pr["nu"]))
    cv = np.asarray(res.conditional_volatility, dtype=float)
    eps_last = 100.0 * r[-1] - mu
    var_last = cv[-1] ** 2
    nxt = omega + (a + gamma * (eps_last < 0)) * eps_last ** 2 + b * var_last
    return GarchFit(
        kind=kind, mu=mu, omega=omega, alpha=a, gamma=gamma, beta=b, nu=nu,
        std_err={k: float(v) for k, v in res.std_err.items()},
        loglik=float(res.loglikelihood), aic=float(res.aic), bic=float(res.bic),
        converged=int(res.convergence_flag) == 0, index=idx,
        cond_vol=cv / 100.0, std_resid=np.asarray(res.std_resid, dtype=float),
        next_variance_pct=float(nxt), last_eps_pct=float(eps_last), last_var_pct=float(var_last),
        result=res,
    )


def variance_forecast(fit: GarchFit, horizon: int) -> np.ndarray:
    """k-step-ahead conditional variance forecasts ``E_T[sigma^2_{T+k}]``, k=1..H (percent^2).

    ``E_T sigma^2_{T+k} = sigma_bar^2 + P^{k-1} (sigma^2_{T+1} - sigma_bar^2)``,
    ``P = alpha + gamma/2 + beta``, ``sigma_bar^2 = omega/(1-P)`` (Engle & Bollerslev 1986).
    """
    k = np.arange(horizon)
    P = fit.persistence
    s1 = fit.next_variance_pct
    if P >= 1:
        return s1 + fit.omega * k  # IGARCH-type linear growth
    ub = fit.unconditional_variance_pct
    return ub + P ** k * (s1 - ub)


def term_structure(fit: GarchFit, horizon: int) -> pd.DataFrame:
    """Forecast volatility term structure: daily vol at step k and annualized average vol to k."""
    v = variance_forecast(fit, horizon)
    k = np.arange(1, horizon + 1)
    return pd.DataFrame({
        "step": k,
        "daily_vol": np.sqrt(v) / 100.0,
        "cum_vol": np.sqrt(np.cumsum(v)) / 100.0,
        "annualized_vol_to_step": np.sqrt(np.cumsum(v) / k * 252) / 100.0,
    })


def garch_var_es(fit: GarchFit, alpha: float = 0.99, horizon: int = 1) -> RiskEstimate:
    """Conditional VaR/ES from the fitted model.

    1-day: ``VaR = -(mu + sigma_{T+1} q*_nu(1-alpha))`` with the unit-variance
    t quantile, ``ES = -mu + sigma_{T+1} ES*_nu(alpha)``.
    h-day: mean ``h mu`` and the AGGREGATED variance forecast
    ``sum_{k=1..h} E_T sigma^2_{T+k}`` with the same t quantile (the h-day sum
    is not exactly t: stated in notes; FHS bootstraps the exact path law).
    """
    alpha = validate_alpha(alpha)
    v = variance_forecast(fit, horizon)
    s = math.sqrt(float(v.sum())) / 100.0
    m = horizon * fit.mu / 100.0
    notes: list[str] = []
    if horizon > 1:
        notes.append("h-day GARCH VaR uses the aggregated variance forecast with the 1-day t "
                     "shape; the true h-day law is not t (see FHS for a path bootstrap).")
    if fit.persistence >= 1:
        notes.append("persistence >= 1: variance is non-stationary (IGARCH-like)")
    elif fit.persistence >= NEAR_UNIT_ROOT:
        notes.append(near_unit_root_note(fit.kind, fit.persistence))
    if not fit.converged:
        notes.append("optimizer did not report convergence")
    name = "garch" if fit.kind == "garch" else "gjr_garch"
    return RiskEstimate(
        name, alpha, horizon, -m + s * std_t_quantile(alpha, fit.nu), -m + s * std_t_es(alpha, fit.nu),
        params=fit.params_dict(), notes=notes, reference=REFERENCES[fit.kind],
    )


# Budget for the h-day FHS bootstrap: simulations x horizon cells. arch keeps
# several (sims x h) float arrays; 2.5M cells is ~0.6 s and < 200 MB, whereas
# 50,000 x 250 takes ~10 s and ~1 GB -- too much for a 2 vCPU / 4 GB server.
FHS_MAX_CELLS = 2_500_000


def _fhs_paths(fit: GarchFit, horizon: int, simulations: int, seed: int) -> tuple[np.ndarray, int]:
    """Bootstrapped compounded h-day returns (decimal) and the number of wiped-out paths."""
    key = (horizon, simulations, seed)
    if key not in fit.fhs_cache:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f = fit.result.forecast(horizon=horizon, method="bootstrap", simulations=simulations,
                                    reindex=False, random_state=np.random.RandomState(seed))
        paths = f.simulations.values[-1] / 100.0  # (sims, h)
        # a simulated daily return <= -100% is a total loss of the (long) position:
        # compound it as -100% instead of letting log1p produce NaN and dropping the path
        wiped = paths <= -1.0
        with np.errstate(divide="ignore"):
            logg = np.log1p(np.where(wiped, -1.0, paths))
        cum = np.expm1(logg.sum(axis=1))
        fit.fhs_cache.clear()
        fit.fhs_cache[key] = (cum, int(wiped.any(axis=1).sum()))
    return fit.fhs_cache[key]


def fhs_var_es(fit: GarchFit, alpha: float = 0.99, horizon: int = 1,
               simulations: int = 10_000, seed: int = 20_240_805) -> RiskEstimate:
    """Filtered historical simulation VaR/ES.

    1-day (exact, no simulation): ``L_j = -(mu + sigma_{T+1} z_j)/100`` over the
    standardized residuals ``z_j``; VaR/ES by the historical tail convention.
    h-day: ``simulations`` bootstrap GARCH paths (arch's residual bootstrap);
    the h-day return is ``prod_k (1 + r_k) - 1``, with any simulated daily
    return <= -100% compounded as a total loss. ``simulations x horizon`` is
    capped at :data:`FHS_MAX_CELLS` (stated in notes). The seed is fixed so
    results are reproducible, and the paths are reused across ``alpha``.
    """
    alpha = validate_alpha(alpha)
    z = fit.std_resid[np.isfinite(fit.std_resid)]
    notes = [f"{z.size} standardized residuals from the {fit.kind.upper()} filter"]
    extra: dict[str, Any] = {}
    if horizon == 1:
        losses = -(fit.mu + math.sqrt(fit.next_variance_pct) * z) / 100.0
        var, es = empirical_var_es(losses, alpha)
        extra["scenarios"] = int(z.size)
    else:
        sims = int(simulations)
        cap = max(1_000, FHS_MAX_CELLS // horizon)
        if sims > cap:
            notes.append(f"bootstrap simulations capped at {cap} (requested {sims}) to keep "
                         f"simulations x horizon <= {FHS_MAX_CELLS:,}")
            sims = cap
        cum, wiped = _fhs_paths(fit, horizon, sims, seed)
        var, es = empirical_var_es(-cum, alpha)
        notes.append(f"{sims} bootstrap GARCH paths (numerical method on the fitted model; seed {seed})")
        if wiped:
            notes.append(f"{wiped} simulated paths drew a daily return <= -100% and are counted as a -100% "
                         "total loss (the linear-return GARCH model is unreliable that far out).")
        extra.update({"scenarios": int(cum.size), "simulations": sims, "wiped_out_paths": wiped})
    return RiskEstimate(
        "fhs", alpha, horizon, var, es,
        params={"filter": fit.kind, "residuals": int(z.size), "next_day_vol": math.sqrt(fit.next_variance_pct) / 100,
                **extra},
        notes=notes,
        reference="Barone-Adesi, Giannopoulos & Vosper (1999) J. Futures Markets 19; Hull & White (1998) J. Risk 1",
    )


def news_impact_curve(fit: GarchFit, n_points: int = 81, span_sd: float = 5.0) -> pd.DataFrame:
    """Engle & Ng (1993) news impact curve: ``sigma^2_{t+1}`` as a function of the
    shock ``eps_t`` with ``sigma^2_t`` held at its unconditional level.
    Output in DECIMAL daily volatility units."""
    ub = fit.unconditional_variance_pct if fit.persistence < 1 else fit.last_var_pct
    sd = math.sqrt(ub)
    eps = np.linspace(-span_sd * sd, span_sd * sd, n_points)
    s2 = fit.omega + (fit.alpha + fit.gamma * (eps < 0)) * eps ** 2 + fit.beta * ub
    return pd.DataFrame({"shock": eps / 100.0, "next_vol": np.sqrt(s2) / 100.0})


def filter_variance(fit_like: dict[str, float], eps_pct: np.ndarray, s2_first: float) -> np.ndarray:
    """Run the (GJR-)GARCH variance recursion with FIXED parameters.

    Given the forecast ``s2_first`` for the first day of a block and the
    block's demeaned percent returns ``eps_pct`` (all but the last are used),
    returns ``sigma^2`` for every day of the block:
    ``s2[k+1] = omega + (alpha + gamma 1[eps_k<0]) eps_k^2 + beta s2[k]``
    (vectorised as a first-order IIR filter).
    """
    from scipy.signal import lfilter

    m = eps_pct.size
    out = np.empty(m)
    out[0] = s2_first
    if m > 1:
        e = eps_pct[:-1]
        x = fit_like["omega"] + (fit_like["alpha"] + fit_like["gamma"] * (e < 0)) * e * e
        y, _ = lfilter([1.0], [1.0, -fit_like["beta"]], x, zi=[fit_like["beta"] * s2_first])
        out[1:] = y
    return out


# ====================================================================
# Fast in-house (GJR-)GARCH(1,1)-t MLE with analytic gradients.
#
# Used for the many rolling refits of the out-of-sample backtest, where the
# generic ``arch`` optimiser (numerical derivatives of the constraints) is the
# bottleneck. Same model, same backcast initialisation and same likelihood as
# ``arch`` (tested to agree); all derivative recursions are first-order IIR
# filters, so each likelihood+gradient evaluation is O(n) vectorised.
# ====================================================================
def _backcast(e: np.ndarray) -> float:
    """arch's backcast: exponentially weighted (0.94) mean of the first 75 squared residuals."""
    tau = min(75, e.size)
    w = 0.94 ** np.arange(tau)
    return float(np.sum(e[:tau] ** 2 * w / w.sum()))


def _garch_nll_grad(theta: np.ndarray, y: np.ndarray, bc: float, gjr: bool) -> tuple[float, np.ndarray]:
    """Mean negative log-likelihood of GJR-GARCH(1,1) with unit-variance t innovations
    and its analytic gradient.

    ``l_t = C(nu) - log(s_t)/2 - (nu+1)/2 log(1 + e_t^2/((nu-2) s_t))``,
    ``C(nu) = lnG((nu+1)/2) - lnG(nu/2) - log(pi (nu-2))/2``;
    ``s_0 = omega + (alpha + gamma/2 + beta) bc``,
    ``s_t = omega + (alpha + gamma 1[e_{t-1}<0]) e_{t-1}^2 + beta s_{t-1}``;
    ``ds_t/dtheta = dx_t/dtheta + beta ds_{t-1}/dtheta (+ s_{t-1} for beta)``.
    """
    from scipy.signal import lfilter
    from scipy.special import digamma, gammaln

    if gjr:
        mu, om, a, g, b, nu = theta
    else:
        mu, om, a, b, nu = theta
        g = 0.0
    e = y - mu
    n = e.size
    ep, e2p = e[:-1], e[:-1] ** 2
    neg = (ep < 0).astype(float)
    ag = a + g * neg
    s0 = om + (a + 0.5 * g + b) * bc
    filt = [1.0], [1.0, -b]

    def rec(u: np.ndarray, d0: float) -> np.ndarray:
        out, _ = lfilter(*filt, u, zi=[b * d0])
        return np.concatenate([[d0], out])

    s = rec(om + ag * e2p, s0)
    if np.any(s <= 0) or not np.all(np.isfinite(s)):
        return 1e10, np.zeros_like(theta)
    nm2 = nu - 2.0
    q = e * e / (nm2 * s)
    A = 1.0 + q
    C = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(np.pi * nm2)
    ll = n * C - 0.5 * np.sum(np.log(s)) - 0.5 * (nu + 1) * np.sum(np.log(A))
    dl_ds = -0.5 / s + 0.5 * (nu + 1) * q / (A * s)
    dl_de = -(nu + 1) * e / (nm2 * s * A)
    ds_dom = rec(np.ones(n - 1), 1.0)
    ds_da = rec(e2p, bc)
    ds_db = rec(s[:-1], bc)
    ds_dmu = rec(-2.0 * ag * ep, 0.0)
    g_mu = float(dl_ds @ ds_dmu - dl_de.sum())
    g_om, g_a, g_b = float(dl_ds @ ds_dom), float(dl_ds @ ds_da), float(dl_ds @ ds_db)
    dC = 0.5 * digamma((nu + 1) / 2) - 0.5 * digamma(nu / 2) - 0.5 / nm2
    g_nu = float(n * dC - 0.5 * np.sum(np.log(A)) + 0.5 * (nu + 1) * np.sum(q / A) / nm2)
    if gjr:
        g_g = float(dl_ds @ rec(neg * e2p, 0.5 * bc))
        grad = np.array([g_mu, g_om, g_a, g_g, g_b, g_nu])
    else:
        grad = np.array([g_mu, g_om, g_a, g_b, g_nu])
    return -ll / n, -grad / n


@dataclass
class FastGarch:
    kind: str
    mu: float
    omega: float
    alpha: float
    gamma: float
    beta: float
    nu: float
    loglik: float
    sigma2: np.ndarray        # percent^2, in-sample
    next_variance_pct: float
    std_resid: np.ndarray
    x: np.ndarray             # optimiser vector, for warm starts
    converged: bool

    @property
    def persistence(self) -> float:
        return self.alpha + 0.5 * self.gamma + self.beta


def fit_garch_fast(returns: np.ndarray, kind: str = "gjr", x0: np.ndarray | None = None) -> FastGarch:
    """MLE of (GJR-)GARCH(1,1)-t on DECIMAL returns (internally percent), SLSQP with
    analytic gradient and linear constraints ``alpha + gamma/2 + beta <= 0.9999``,
    ``alpha + gamma >= 0``."""
    from scipy.optimize import minimize

    if kind not in KINDS:
        raise ValueError(f"unknown GARCH kind {kind!r}")
    gjr = kind == "gjr"
    y = 100.0 * np.asarray(returns, dtype=float)
    if y.size < 100:
        raise ValueError("GARCH needs at least 100 observations")
    bc = _backcast(y - y.mean())
    v = y.var()
    if x0 is None:
        x0 = (np.array([y.mean(), v * 0.05, 0.03, 0.09, 0.88, 8.0]) if gjr
              else np.array([y.mean(), v * 0.05, 0.08, 0.87, 8.0]))
    bounds = ([(-10 * abs(y).max(), 10 * abs(y).max()), (1e-8, 10 * v), (0.0, 1.0)]
              + ([(-1.0, 2.0)] if gjr else []) + [(0.0, 1.0), (2.05, 500.0)])
    if gjr:
        cons = [{"type": "ineq", "fun": lambda t: 0.9999 - t[2] - 0.5 * t[3] - t[4],
                 "jac": lambda t: np.array([0, 0, -1.0, -0.5, -1.0, 0])},
                {"type": "ineq", "fun": lambda t: t[2] + t[3], "jac": lambda t: np.array([0, 0, 1.0, 1.0, 0, 0])}]
    else:
        cons = [{"type": "ineq", "fun": lambda t: 0.9999 - t[2] - t[3],
                 "jac": lambda t: np.array([0, 0, -1.0, -1.0, 0])}]
    x0 = np.clip(np.asarray(x0, dtype=float), [b[0] for b in bounds], [b[1] for b in bounds])
    res = minimize(_garch_nll_grad, x0, args=(y, bc, gjr), jac=True, method="SLSQP", bounds=bounds,
                   constraints=cons, options={"maxiter": 300, "ftol": 1e-10})
    x = res.x
    if gjr:
        mu, om, a, g, b, nu = x
    else:
        mu, om, a, b, nu = x
        g = 0.0
    e = y - mu
    s = np.empty(y.size)
    s[0] = om + (a + 0.5 * g + b) * bc
    from scipy.signal import lfilter

    u = om + (a + g * (e[:-1] < 0)) * e[:-1] ** 2
    s[1:], _ = lfilter([1.0], [1.0, -b], u, zi=[b * s[0]])
    nxt = om + (a + g * (e[-1] < 0)) * e[-1] ** 2 + b * s[-1]
    return FastGarch(kind, float(mu), float(om), float(a), float(g), float(b), float(nu),
                     float(-res.fun * y.size), s, float(nxt), e / np.sqrt(s), x, bool(res.success))
