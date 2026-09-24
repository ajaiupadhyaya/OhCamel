"""Out-of-sample VaR/ES backtesting.

Forecasting design
------------------
For every day ``t`` after an initial estimation window of ``W`` sessions, each
model forecasts the one-day VaR/ES of ``r_t`` using ONLY ``r_{t-W}..r_{t-1}``
(rolling window). Parametric moments are recomputed every day (vectorised
over a sliding-window view); models that need numerical optimisation
(Student-t dof, GARCH/GJR, EVT) are re-estimated every ``refit_every``
sessions and, in between, GARCH variances are FILTERED forward with the
frozen parameters so the volatility forecast still reacts daily. Rolling
GARCH refits use :func:`~ohcamel_quant.risk.garch.fit_garch_fast` (same
likelihood and initialisation as ``arch``, analytic gradients, warm starts).

Tests
-----
* Kupiec (1995) proportion-of-failures LR, "Techniques for verifying the
  accuracy of risk measurement models", J. Derivatives 3(2).
* Christoffersen (1998) independence and conditional-coverage LRs,
  "Evaluating interval forecasts", Int. Economic Review 39(4).
* Basel traffic light (BCBS 1996, "Supervisory framework for the use of
  backtesting in conjunction with the internal models approach"): zones from
  the binomial CDF of the exception count, scaled to any sample size.
* Engle & Manganelli (2004) dynamic quantile test, "CAViaR", JBES 22(4).
* Acerbi & Szekely (2014) Z2 ES test, "Backtesting expected shortfall", Risk.
* Scoring: quantile (tick) loss (Gneiting 2011, JASA 106) and the FZ0 joint
  VaR/ES loss (Fissler & Ziegel 2016, Ann. Stat. 44; Patton, Ziegel & Chen
  2019, J. Econometrics 211).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import stats
from scipy.special import xlogy

from .core import (
    ewma_variance_path,
    fit_t_dof,
    std_t_es,
    std_t_quantile,
    tail_count,
    validate_alpha,
)
from .garch import filter_variance, fit_garch_fast
from .var import cornish_fisher_tail_mean, fit_gpd, gpd_var_es

ALL_MODELS = ("historical", "gaussian", "student_t", "cornish_fisher", "ewma",
              "garch", "gjr_garch", "fhs", "evt_pot")


# ============================================================ forecasting
@dataclass
class Forecasts:
    start: int                       # index into r of the first forecast day
    var: dict[str, np.ndarray]
    es: dict[str, np.ndarray]
    info: dict[str, dict[str, Any]]


def rolling_forecasts(
    r: np.ndarray, alpha: float = 0.99, window: int = 500, refit_every: int = 20,
    models: tuple[str, ...] | list[str] = ALL_MODELS, ewma_lambda: float = 0.94,
    evt_threshold: float = 0.90,
) -> Forecasts:
    """Out-of-sample one-day VaR/ES for days ``window .. n-1`` of ``r``."""
    alpha = validate_alpha(alpha)
    r = np.asarray(r, dtype=float)
    n = r.size
    if window < 100:
        raise ValueError("estimation window must be at least 100 sessions")
    if n < window + 30:
        raise ValueError(f"need more than window + 30 = {window + 30} observations; have {n}")
    if refit_every < 1:
        raise ValueError("refit_every must be >= 1")
    unknown = [m for m in models if m not in ALL_MODELS]
    if unknown:
        raise ValueError(f"unknown models {unknown}; choose from {list(ALL_MODELS)}")
    p = 1 - alpha
    m = n - window
    W = sliding_window_view(r, window)[:-1]           # row j -> r[j : j+window], forecasts day j+window
    k = tail_count(window, alpha)
    var: dict[str, np.ndarray] = {}
    es: dict[str, np.ndarray] = {}
    info: dict[str, dict[str, Any]] = {}
    mu = W.mean(axis=1)
    sd = W.std(axis=1, ddof=1)
    refits = list(range(0, m, refit_every))

    if "historical" in models:
        top = -np.sort(W, axis=1)[:, :k]               # k largest losses, descending
        var["historical"], es["historical"] = top[:, -1], top.mean(axis=1)
    if "gaussian" in models:
        z = stats.norm.ppf(alpha)
        var["gaussian"] = -mu + sd * z
        es["gaussian"] = -mu + sd * stats.norm.pdf(z) / p
    if "student_t" in models:
        q = np.empty(m)
        e = np.empty(m)
        nus = []
        for j0 in refits:
            nu = fit_t_dof(W[j0])
            nus.append(nu)
            q[j0:j0 + refit_every] = std_t_quantile(alpha, nu)
            e[j0:j0 + refit_every] = std_t_es(alpha, nu)
        var["student_t"], es["student_t"] = -mu + sd * q, -mu + sd * e
        info["student_t"] = {"nu_median": float(np.median(nus)), "refits": len(nus)}
    if "cornish_fisher" in models:
        S = stats.skew(W, axis=1, bias=False)
        K = stats.kurtosis(W, axis=1, fisher=True, bias=False)
        M, g = cornish_fisher_tail_mean(p, S, K)
        var["cornish_fisher"] = -(mu + sd * g)
        es["cornish_fisher"] = -(mu + sd * np.minimum(M, g))
    if "ewma" in models:
        path = ewma_variance_path(r, ewma_lambda, seed_obs=min(30, window))
        s = np.sqrt(path[window:n])
        z = stats.norm.ppf(alpha)
        var["ewma"], es["ewma"] = s * z, s * stats.norm.pdf(z) / p
        info["ewma"] = {"lambda": ewma_lambda}

    garch_kinds = [kd for kd, name in (("garch", "garch"), ("gjr", "gjr_garch")) if name in models]
    if "fhs" in models and "gjr" not in garch_kinds:
        garch_kinds.append("gjr")
    for kind in garch_kinds:
        v = np.empty(m)
        ev = np.empty(m)
        fv = np.empty(m)
        fe = np.empty(m)
        start_vals = None
        persist = []
        for j0 in refits:
            j1 = min(j0 + refit_every, m)
            t0 = window + j0
            fit = fit_garch_fast(r[t0 - window:t0], kind, x0=start_vals)
            start_vals = fit.x
            persist.append(fit.persistence)
            eps = 100.0 * r[t0:t0 + (j1 - j0)] - fit.mu
            s2 = filter_variance({"omega": fit.omega, "alpha": fit.alpha, "gamma": fit.gamma,
                                  "beta": fit.beta}, eps, fit.next_variance_pct)
            sig = np.sqrt(s2)
            v[j0:j1] = (-fit.mu + sig * std_t_quantile(alpha, fit.nu)) / 100.0
            ev[j0:j1] = (-fit.mu + sig * std_t_es(alpha, fit.nu)) / 100.0
            if kind == "gjr":
                zres = np.sort(fit.std_resid[np.isfinite(fit.std_resid)])
                kz = tail_count(zres.size, alpha)
                zq, zt = zres[kz - 1], zres[:kz].mean()      # left tail of z
                fv[j0:j1] = -(fit.mu + sig * zq) / 100.0
                fe[j0:j1] = -(fit.mu + sig * zt) / 100.0
        name = "garch" if kind == "garch" else "gjr_garch"
        if name in models:
            var[name], es[name] = v, ev
            info[name] = {"refits": len(refits), "persistence_median": float(np.median(persist))}
        if kind == "gjr" and "fhs" in models:
            var["fhs"], es["fhs"] = fv, fe
            info["fhs"] = {"filter": "gjr", "refits": len(refits)}

    if "evt_pot" in models:
        v = np.empty(m)
        ev = np.empty(m)
        xis = []
        for j0 in refits:
            j1 = min(j0 + refit_every, m)
            L = -W[j0]
            u = float(np.quantile(L, evt_threshold))
            y = L[L > u] - u
            if y.size < 10:
                raise ValueError(
                    f"EVT: a {window}-session window with threshold quantile {evt_threshold} leaves only "
                    f"{y.size} exceedances (need >= 10); use a longer window or a lower evt_threshold")
            xi, beta = fit_gpd(y)
            xis.append(xi)
            a, b = gpd_var_es(u, xi, beta, L.size, y.size, alpha)
            v[j0:j1], ev[j0:j1] = a, b
        var["evt_pot"], es["evt_pot"] = v, ev
        info["evt_pot"] = {"refits": len(refits), "xi_median": float(np.median(xis)),
                           "threshold_quantile": evt_threshold}

    order = [mm for mm in ALL_MODELS if mm in var]
    return Forecasts(window, {mm: var[mm] for mm in order}, {mm: es[mm] for mm in order}, info)


# ============================================================ tests
def kupiec_pof(x: int, T: int, p: float) -> dict[str, float]:
    """Kupiec (1995) proportion-of-failures likelihood ratio.

    ``LR_POF = -2 ln[(1-p)^{T-x} p^x] + 2 ln[(1-x/T)^{T-x} (x/T)^x]  ~ chi2(1)``.
    """
    if T <= 0:
        raise ValueError("T must be positive")
    pi = x / T
    ll0 = xlogy(T - x, 1 - p) + xlogy(x, p)
    ll1 = xlogy(T - x, 1 - pi) + xlogy(x, pi)
    lr = max(0.0, float(-2 * (ll0 - ll1)))
    return {"lr": lr, "p_value": float(stats.chi2.sf(lr, 1)), "exceptions": int(x), "T": int(T),
            "expected": T * p, "rate": pi}


def christoffersen(hits: np.ndarray, p: float) -> dict[str, float]:
    """Christoffersen (1998) Markov independence and conditional-coverage tests.

    With transition counts ``n_ij`` (state i on day t-1 -> j on day t),
    ``pi_01 = n01/(n00+n01)``, ``pi_11 = n11/(n10+n11)``, ``pi = (n01+n11)/(sum n)``:

    ``LR_ind = -2 ln[(1-pi)^{n00+n10} pi^{n01+n11}]
               + 2 ln[(1-pi_01)^{n00} pi_01^{n01} (1-pi_11)^{n10} pi_11^{n11}]  ~ chi2(1)``

    ``LR_cc = LR_POF + LR_ind ~ chi2(2)`` (POF computed on the same T-1 transitions).
    """
    h = np.asarray(hits, dtype=int)
    a, b = h[:-1], h[1:]
    n00 = int(np.sum((a == 0) & (b == 0)))
    n01 = int(np.sum((a == 0) & (b == 1)))
    n10 = int(np.sum((a == 1) & (b == 0)))
    n11 = int(np.sum((a == 1) & (b == 1)))
    pi01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    tot = n00 + n01 + n10 + n11
    pi = (n01 + n11) / tot if tot else 0.0
    ll0 = xlogy(n00 + n10, 1 - pi) + xlogy(n01 + n11, pi)
    ll1 = xlogy(n00, 1 - pi01) + xlogy(n01, pi01) + xlogy(n10, 1 - pi11) + xlogy(n11, pi11)
    lr_ind = max(0.0, float(-2 * (ll0 - ll1)))
    pof = kupiec_pof(n01 + n11, tot, p)
    lr_cc = pof["lr"] + lr_ind
    return {"n00": n00, "n01": n01, "n10": n10, "n11": n11, "pi01": pi01, "pi11": pi11,
            "lr_ind": lr_ind, "p_value_ind": float(stats.chi2.sf(lr_ind, 1)),
            "lr_cc": lr_cc, "p_value_cc": float(stats.chi2.sf(lr_cc, 2))}


def traffic_light(x: int, T: int, p: float) -> dict[str, Any]:
    """Basel traffic-light zone from binomial cumulative probabilities.

    ``F(x) = P(X <= x)``, ``X ~ Bin(T, p)``. Green if ``F(x) < 95%``, yellow if
    ``95% <= F(x) < 99.99%``, red otherwise (BCBS 1996, Table 2, generalised
    to any T and p). For T = 250, p = 1% this reproduces green 0-4,
    yellow 5-9, red 10+.
    """
    F = float(stats.binom.cdf(x, T, p))
    zone = "green" if F < 0.95 else ("yellow" if F < 0.9999 else "red")
    xs = np.arange(0, T + 1)
    cdf = stats.binom.cdf(xs, T, p)
    green_max = int(xs[cdf < 0.95].max()) if np.any(cdf < 0.95) else -1
    yellow_max = int(xs[cdf < 0.9999].max()) if np.any(cdf < 0.9999) else -1
    return {"zone": zone, "cumulative_probability": F, "exceptions": int(x), "T": int(T),
            "green_max": green_max, "yellow_max": yellow_max}


# BCBS (1996) Table 2: plus factor added to the capital multiplier (3) for
# yellow-zone exception counts in a 250-day, 99% backtest. Regulatory table.
BASEL_PLUS_FACTOR = {5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85}


def basel_regulatory(hits_last_250: np.ndarray) -> dict[str, Any]:
    """Basel 99%/250-day regulatory view: zone and capital multiplier ``3 + plus factor``."""
    x = int(np.sum(hits_last_250))
    tl = traffic_light(x, 250, 0.01)
    plus = 0.0 if x <= 4 else BASEL_PLUS_FACTOR.get(x, 1.0)
    return {**tl, "plus_factor": plus, "multiplier": 3.0 + plus}


def dq_test(hits: np.ndarray, var: np.ndarray, p: float, lags: int = 4) -> dict[str, float]:
    """Engle & Manganelli (2004) dynamic quantile test.

    ``Hit_t = 1[L_t > VaR_t] - p``; regress on ``X_t = [1, Hit_{t-1..t-K}, VaR_t]``:
    ``DQ = Hit' X (X'X)^{-1} X' Hit / (p (1 - p))  ~ chi2(K + 2)``.

    The asymptotic chi2 dof is the RANK of ``X``: with no (or only isolated)
    exceptions the lagged-Hit columns are constant and collinear with the
    intercept, so the projection has fewer than ``K + 2`` dimensions; using the
    nominal count would inflate the p-value.
    """
    hit = np.asarray(hits, dtype=float) - p
    v = np.asarray(var, dtype=float)
    T = hit.size
    if T <= lags + 10:
        return {"dq": math.nan, "p_value": math.nan, "df": lags + 2}
    y = hit[lags:]
    cols = [np.ones(T - lags)] + [hit[lags - j:T - j] for j in range(1, lags + 1)] + [v[lags:]]
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    dq = float(y @ fitted / (p * (1 - p)))
    df = int(np.linalg.matrix_rank(X))
    return {"dq": dq, "p_value": float(stats.chi2.sf(dq, df)), "df": df}


def acerbi_szekely_z2(returns: np.ndarray, var: np.ndarray, es: np.ndarray, p: float) -> dict[str, Any]:
    """Acerbi & Szekely (2014) test statistic Z2.

    ``Z2 = sum_t r_t I_t / (T p ES_t) + 1`` with ``I_t = 1[r_t < -VaR_t]`` and
    ES positive. ``E[Z2] = 0`` under correct ES; ``Z2 < 0`` means ES is
    underestimated. The paper reports near-stable critical values for
    ES at 97.5% and T = 250: ~-0.70 (5% level) and ~-1.8 (0.01% level);
    they are indicative only for other alpha/T.
    """
    r = np.asarray(returns, dtype=float)
    hit = r < -np.asarray(var)
    T = r.size
    z2 = float(np.sum(r * hit / (T * p * np.asarray(es))) + 1.0)
    verdict = "reject (ES underestimated)" if z2 < -1.8 else ("warning" if z2 < -0.70 else "accept")
    return {"z2": z2, "indicative_verdict": verdict, "critical_5pct": -0.70, "critical_0_01pct": -1.8}


def quantile_score(returns: np.ndarray, var: np.ndarray, p: float) -> float:
    """Mean tick loss of the return quantile ``q_t = -VaR_t``:
    ``QS = mean[(1[r_t < q_t] - p)(q_t - r_t)]`` (lower is better)."""
    q = -np.asarray(var)
    r = np.asarray(returns)
    return float(np.mean(((r < q).astype(float) - p) * (q - r)))


def fz0_loss(returns: np.ndarray, var: np.ndarray, es: np.ndarray, p: float) -> float:
    """Mean FZ0 loss (Patton, Ziegel & Chen 2019) for the pair (VaR, ES), lower is better:
    ``FZ0 = -1/(p e) 1[r <= v](v - r) + v/e + log(-e) - 1`` with ``v = -VaR``, ``e = -ES < 0``."""
    v, e, r = -np.asarray(var), -np.asarray(es), np.asarray(returns)
    ok = e < 0
    if not np.any(ok):
        return math.nan
    v, e, r = v[ok], e[ok], r[ok]
    loss = -1.0 / (p * e) * (r <= v) * (v - r) + v / e + np.log(-e) - 1.0
    return float(np.mean(loss))


def scorecard(returns: np.ndarray, var: np.ndarray, es: np.ndarray, alpha: float) -> dict[str, Any]:
    """All tests for one model's out-of-sample forecasts."""
    p = 1 - alpha
    r = np.asarray(returns, dtype=float)
    hits = (-r > var).astype(int)
    T, x = r.size, int(hits.sum())
    out: dict[str, Any] = {
        "T": T, "exceptions": x, "expected": T * p, "exception_rate": x / T,
        "kupiec": kupiec_pof(x, T, p),
        "christoffersen": christoffersen(hits, p),
        "traffic_light": traffic_light(x, T, p),
        "dq": dq_test(hits, var, p),
        "acerbi_szekely_z2": acerbi_szekely_z2(r, var, es, p),
        "quantile_score": quantile_score(r, var, p),
        "fz0_loss": fz0_loss(r, var, es, p),
        "mean_var": float(np.mean(var)), "mean_es": float(np.mean(es)),
    }
    if T >= 250:
        out["basel_last_250"] = basel_regulatory(
            (-r[-250:] > np.asarray(var)[-250:]).astype(int)) if abs(alpha - 0.99) < 1e-9 else None
    return out
