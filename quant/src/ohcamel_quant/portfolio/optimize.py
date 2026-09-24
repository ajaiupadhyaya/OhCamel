"""Portfolio optimizers and ex-ante diagnostics.

Inputs are ANNUALIZED: expected returns ``mu`` (N), covariance ``Sigma`` (N x N)
and a risk-free rate ``rf`` (decimal per year, supplied by the caller). Weights
are fractions of equity summing to one (the budget constraint); per-asset bounds
``lo_i <= w_i <= hi_i`` (long-only ``[0, 1]`` by default), optional gross
leverage ``sum |w_i| <= L`` and optional turnover ``sum |w_i - w0_i| <= T``
versus current weights ``w0``.

Convex programs (minimum variance, mean-variance, target volatility, max-Sharpe
after the Cornuejols-Tutuncu homogenisation) are solved with SLSQP on exact
gradients -- for a convex QP with linear constraints any KKT point is the
global optimum. Absolute values in turnover/leverage constraints are linearised
with auxiliary variables ``t_i >= +-(w_i - w0_i)``. Minimum CVaR is the
Rockafellar-Uryasev linear program solved with HiGHS.

References
----------
* Markowitz (1952), "Portfolio selection", JF 7(1).
* Cornuejols & Tutuncu (2007), *Optimization Methods in Finance*, sec. 8.2
  (max-Sharpe as a convex QP).
* Maillard, Roncalli & Teiletche (2010), "The properties of equally weighted
  risk contribution portfolios", JPM 36(4); Spinu (2013), "An algorithm for
  computing risk parity weights", SSRN 2297383.
* Choueifaty & Coignard (2008), "Toward maximum diversification", JPM 35(1).
* Rockafellar & Uryasev (2000), "Optimization of conditional value-at-risk",
  J. of Risk 2(3).
* DeMiguel, Garlappi & Uppal (2009), "Optimal versus naive diversification",
  RFS 22(5).
* Meucci (2009), "Managing diversification", Risk 22(5) (effective number of bets).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize
from scipy.sparse import csr_matrix, hstack, identity, vstack

TRADING_DAYS = 252
_TOL = 1e-7


# ------------------------------------------------------------------ constraints
@dataclass
class Constraints:
    """Feasible set ``{w : 1'w = 1, lo <= w <= hi, [sum|w| <= L], [sum|w - w0| <= T]}``."""

    lower: np.ndarray | float = 0.0
    upper: np.ndarray | float = 1.0
    max_gross: float | None = None
    max_turnover: float | None = None
    current: np.ndarray | None = None

    def bounds(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        lo = np.broadcast_to(np.asarray(self.lower, float), (n,)).copy()
        hi = np.broadcast_to(np.asarray(self.upper, float), (n,)).copy()
        if np.any(lo > hi + 1e-12):
            raise ValueError("a lower bound exceeds its upper bound")
        if lo.sum() > 1 + 1e-9 or hi.sum() < 1 - 1e-9:
            raise ValueError(f"bounds are infeasible with fully-invested weights: sum(lo)={lo.sum():.3f}, "
                             f"sum(hi)={hi.sum():.3f} must bracket 1")
        return lo, hi

    @property
    def long_only(self) -> bool:
        return bool(np.all(np.asarray(self.lower) >= 0))

    def validate(self, n: int) -> None:
        lo, hi = self.bounds(n)
        if self.max_turnover is not None:
            if self.current is None or len(self.current) != n:
                raise ValueError("max_turnover needs current weights for every asset")
            if self.max_turnover < 0:
                raise ValueError("max_turnover must be >= 0")
        if self.max_gross is not None and self.max_gross < 1:
            raise ValueError("max_gross (sum |w|) must be >= 1 for a fully-invested portfolio")

    def satisfied(self, w: np.ndarray, tol: float = 1e-5) -> bool:
        lo, hi = self.bounds(len(w))
        ok = abs(w.sum() - 1) < tol and np.all(w >= lo - tol) and np.all(w <= hi + tol)
        if self.max_gross is not None:
            ok = ok and np.abs(w).sum() <= self.max_gross + tol
        if self.max_turnover is not None and self.current is not None:
            ok = ok and np.abs(w - self.current).sum() <= self.max_turnover + tol
        return bool(ok)

    def is_default(self, n: int) -> bool:
        lo, hi = self.bounds(n)
        return bool(np.all(lo == 0) and np.all(hi >= 1) and self.max_turnover is None
                    and self.max_gross is None)


# ------------------------------------------------------------------ result
@dataclass
class PortfolioResult:
    method: str
    weights: pd.Series
    expected_return: float | None
    volatility: float
    sharpe: float | None
    risk_contributions: pd.Series  # sum to volatility (Euler)
    risk_contributions_pct: pd.Series
    diversification_ratio: float
    effective_bets: float  # Meucci (2009) PCA-entropy ENB
    effective_n: float  # 1 / sum(w_i^2) of normalised |w|
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method, "weights": self.weights.to_dict(),
            "expected_return": self.expected_return, "volatility": self.volatility,
            "sharpe": self.sharpe, "risk_contributions": self.risk_contributions.to_dict(),
            "risk_contributions_pct": self.risk_contributions_pct.to_dict(),
            "diversification_ratio": self.diversification_ratio,
            "effective_bets": self.effective_bets, "effective_n": self.effective_n,
            "params": self.params, "notes": self.notes, "extra": self.extra,
            "reference": self.reference,
        }


def risk_contributions(w: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Euler contributions ``RC_i = w_i (Sigma w)_i / sqrt(w' Sigma w)``; ``sum RC_i = sigma_p``."""
    vol = float(np.sqrt(max(w @ sigma @ w, 0.0)))
    if vol == 0:
        return np.zeros_like(w)
    return w * (sigma @ w) / vol


def effective_bets(w: np.ndarray, sigma: np.ndarray) -> float:
    """Meucci (2009) effective number of minimum-torsion-free (principal-component) bets.

    ``Sigma = E Lambda E'``, factor exposures ``w~ = E' w``, variance shares
    ``p_k = w~_k^2 lambda_k / (w' Sigma w)``, ``ENB = exp(-sum_k p_k ln p_k)`` in ``[1, N]``.
    """
    lam, e = np.linalg.eigh(sigma)
    lam = np.maximum(lam, 0.0)
    wt = e.T @ w
    v = wt ** 2 * lam
    tot = v.sum()
    if tot <= 0:
        return float("nan")
    p = v / tot
    p = p[p > 1e-16]
    return float(np.exp(-np.sum(p * np.log(p))))


def evaluate(method: str, w: np.ndarray, sigma: pd.DataFrame, mu: pd.Series | None = None,
             rf: float | None = None, reference: str = "", **kw: Any) -> PortfolioResult:
    """Ex-ante statistics of weights ``w`` under ``(mu, Sigma)``."""
    names = list(sigma.columns)
    s = sigma.to_numpy(float)
    w = np.asarray(w, float)
    vol = float(np.sqrt(max(w @ s @ w, 0.0)))
    rc = risk_contributions(w, s)
    er = float(mu.reindex(names).to_numpy(float) @ w) if mu is not None else None
    sharpe = (er - rf) / vol if (er is not None and rf is not None and vol > 0) else None
    sd = np.sqrt(np.diag(s))
    dr = float(np.abs(w) @ sd / vol) if vol > 0 else float("nan")
    a = np.abs(w) / np.abs(w).sum()
    return PortfolioResult(
        method=method, weights=pd.Series(w, index=names), expected_return=er, volatility=vol,
        sharpe=sharpe, risk_contributions=pd.Series(rc, index=names),
        risk_contributions_pct=pd.Series(rc / vol if vol > 0 else rc, index=names),
        diversification_ratio=dr, effective_bets=effective_bets(w, s),
        effective_n=float(1.0 / np.sum(a ** 2)), reference=reference, **kw)


# ------------------------------------------------------------------ generic convex solver
def _solve(n: int, obj: Callable[[np.ndarray], float], grad: Callable[[np.ndarray], np.ndarray],
           cons: Constraints, x0: np.ndarray | None = None,
           extra: list[dict[str, Any]] | None = None) -> np.ndarray:
    """Minimise a smooth convex ``obj(w)`` over the :class:`Constraints` set.

    Variables ``x = [w, t (turnover aux), g (gross aux)]``; ``extra`` are SLSQP
    constraint dicts written in terms of ``w``.
    """
    cons.validate(n)
    lo, hi = cons.bounds(n)
    use_t = cons.max_turnover is not None
    use_g = cons.max_gross is not None
    m = n * (1 + int(use_t) + int(use_g))
    w0 = cons.current if (cons.current is not None and cons.satisfied(np.asarray(cons.current))) else None
    if x0 is None:
        x0 = w0 if w0 is not None else np.clip(np.full(n, 1.0 / n), lo, hi)
    start = np.zeros(m)
    start[:n] = x0
    off = n
    bnds: list[tuple[float | None, float | None]] = [(float(a), float(b)) for a, b in zip(lo, hi, strict=True)]
    c_list: list[dict[str, Any]] = [{"type": "eq", "fun": lambda x: np.sum(x[:n]) - 1.0,
                                     "jac": lambda x: np.r_[np.ones(n), np.zeros(m - n)]}]
    if use_t:
        cur = np.asarray(cons.current, float)
        ti = off
        start[ti:ti + n] = np.abs(x0 - cur) + 1e-9
        bnds += [(0.0, None)] * n
        a1 = np.zeros((n, m))
        a1[:, :n] = -np.eye(n)
        a1[:, ti:ti + n] = np.eye(n)  # t - (w - w0) >= 0
        a2 = np.zeros((n, m))
        a2[:, :n] = np.eye(n)
        a2[:, ti:ti + n] = np.eye(n)  # t + (w - w0) >= 0
        a3 = np.zeros(m)
        a3[ti:ti + n] = -1.0
        tmax = float(cons.max_turnover)  # type: ignore[arg-type]
        c_list += [
            {"type": "ineq", "fun": lambda x, a=a1: a @ x + cur, "jac": lambda x, a=a1: a},
            {"type": "ineq", "fun": lambda x, a=a2: a @ x - cur, "jac": lambda x, a=a2: a},
            {"type": "ineq", "fun": lambda x, a=a3: a @ x + tmax, "jac": lambda x, a=a3: a[None, :]},
        ]
        off += n
    if use_g:
        gi = off
        start[gi:gi + n] = np.abs(x0) + 1e-9
        bnds += [(0.0, None)] * n
        b1 = np.zeros((n, m))
        b1[:, :n] = -np.eye(n)
        b1[:, gi:gi + n] = np.eye(n)
        b2 = np.zeros((n, m))
        b2[:, :n] = np.eye(n)
        b2[:, gi:gi + n] = np.eye(n)
        b3 = np.zeros(m)
        b3[gi:gi + n] = -1.0
        gmax = float(cons.max_gross)  # type: ignore[arg-type]
        c_list += [
            {"type": "ineq", "fun": lambda x, a=b1: a @ x, "jac": lambda x, a=b1: a},
            {"type": "ineq", "fun": lambda x, a=b2: a @ x, "jac": lambda x, a=b2: a},
            {"type": "ineq", "fun": lambda x, a=b3: a @ x + gmax, "jac": lambda x, a=b3: a[None, :]},
        ]
    for c in extra or []:
        f, j = c["fun"], c["jac"]
        c_list.append({"type": c["type"], "fun": lambda x, f=f: f(x[:n]),
                       "jac": lambda x, j=j: np.r_[np.atleast_2d(j(x[:n])).ravel(), np.zeros(m - n)][None, :]})

    def fx(x: np.ndarray) -> float:
        return obj(x[:n])

    def gx(x: np.ndarray) -> np.ndarray:
        out = np.zeros(m)
        out[:n] = grad(x[:n])
        return out

    res = minimize(fx, start, jac=gx, method="SLSQP", bounds=bnds, constraints=c_list,
                   options={"maxiter": 2000, "ftol": 1e-14})
    w = res.x[:n]
    if not res.success and not cons.satisfied(w, 1e-4):
        raise ValueError(f"optimizer failed: {res.message}")
    w = np.clip(w, lo, hi)
    return w


def active_set_qp(p: np.ndarray, q: np.ndarray, a: np.ndarray, b: float, lo: np.ndarray,
                  hi: np.ndarray, max_iter: int = 500) -> np.ndarray | None:
    """Primal active-set method for ``min 1/2 x'Px + q'x`` s.t. ``a'x = b``, ``lo <= x <= hi``
    with ``P`` positive definite (Nocedal & Wright 2006, *Numerical Optimization*, Alg. 16.3).

    Each iteration solves the equality-constrained KKT system on the free set
    ``[P_FF a_F; a_F' 0] [x_F; -nu] = [-(q_F + P_FB x_B); b - a_B'x_B]``; blocking bounds
    are added, and bounds whose multipliers have the wrong sign are released.
    Finite termination for strictly convex problems. Returns None if it does not
    converge (callers then fall back to SLSQP).
    """
    n = len(q)
    start = linprog(np.zeros(n), A_eq=a[None, :], b_eq=[b], bounds=list(zip(lo, hi, strict=True)),
                    method="highs")
    if start.status != 0:
        raise ValueError("constraints are infeasible")
    x = np.clip(start.x, lo, hi)
    tol = 1e-12 * max(1.0, float(np.abs(p).max()))
    at_lo = np.isclose(x, lo, atol=1e-12)
    at_hi = np.isclose(x, hi, atol=1e-12) & ~at_lo
    # keep only a basis-compatible working set: free at least one variable with a_i != 0
    work = at_lo | at_hi
    if work.all():
        j = int(np.argmax(np.abs(a)))
        work[j] = False
    for _ in range(max_iter):
        f = ~work
        fi = np.flatnonzero(f)
        bi = np.flatnonzero(work)
        rhs_q = q[fi] + p[np.ix_(fi, bi)] @ x[bi]
        af = a[fi]
        k = len(fi)
        kkt = np.zeros((k + 1, k + 1))
        kkt[:k, :k] = p[np.ix_(fi, fi)]
        kkt[:k, k] = af
        kkt[k, :k] = af
        rhs = np.r_[-rhs_q, b - a[bi] @ x[bi]]
        try:
            sol = np.linalg.solve(kkt, rhs)
        except np.linalg.LinAlgError:
            return None
        target = sol[:k]
        step = target - x[fi]
        if np.max(np.abs(step), initial=0.0) <= 1e-13:
            g = p @ x + q
            nu = -sol[k]
            lam = g - nu * a  # multipliers of the active bounds
            viol_lo = work & np.isclose(x, lo) & (lam < -tol)
            viol_hi = work & np.isclose(x, hi) & (lam > tol)
            viol = np.where(viol_lo, -lam, 0.0) + np.where(viol_hi, lam, 0.0)
            if not viol.any():
                return x
            work[int(np.argmax(viol))] = False
            continue
        alpha, block = 1.0, -1
        for j, i in enumerate(fi):
            if step[j] < 0 and np.isfinite(lo[i]):
                t = (lo[i] - x[i]) / step[j]
            elif step[j] > 0 and np.isfinite(hi[i]):
                t = (hi[i] - x[i]) / step[j]
            else:
                continue
            if t < alpha:
                alpha, block = max(t, 0.0), int(i)
        x[fi] = x[fi] + alpha * step
        if block >= 0:
            x[block] = lo[block] if step[list(fi).index(block)] < 0 else hi[block]
            work[block] = True
    return None


def _scaled(sigma: np.ndarray) -> tuple[np.ndarray, float]:
    k = float(np.mean(np.diag(sigma)))
    return sigma / k, k


# ------------------------------------------------------------------ optimizers
def min_variance(sigma: pd.DataFrame, cons: Constraints | None = None) -> np.ndarray:
    """Global minimum variance: ``min w' Sigma w`` s.t. constraints.
    Unconstrained closed form ``w = Sigma^{-1} 1 / 1' Sigma^{-1} 1``."""
    cons = cons or Constraints()
    s, _ = _scaled(sigma.to_numpy(float))
    n = len(s)
    if cons.max_turnover is None and cons.max_gross is None:
        lo, hi = cons.bounds(n)
        w = active_set_qp(2 * s, np.zeros(n), np.ones(n), 1.0, lo, hi)
        if w is not None:
            return w
    return _solve(n, lambda w: float(w @ s @ w), lambda w: 2 * s @ w, cons)


def max_return(mu: pd.Series, n_cons: Constraints, names: list[str]) -> float:
    """Largest feasible ``mu' w`` (LP) -- upper end of the efficient frontier."""
    m = mu.reindex(names).to_numpy(float)
    if n_cons.max_turnover is None and n_cons.max_gross is None:
        lo, hi = n_cons.bounds(len(m))
        res = linprog(-m, A_eq=np.ones((1, len(m))), b_eq=[1.0], bounds=list(zip(lo, hi, strict=True)),
                      method="highs")
        if res.status == 0:
            return float(m @ res.x)
    w = _solve(len(m), lambda w: -float(m @ w), lambda w: -m, n_cons)
    return float(m @ w)


def mean_variance(mu: pd.Series, sigma: pd.DataFrame, cons: Constraints | None = None, *,
                  target_return: float | None = None, target_vol: float | None = None,
                  risk_aversion: float | None = None) -> np.ndarray:
    """Markowitz (1952) in three equivalent parameterisations (exactly one given):

    * target return: ``min w' Sigma w`` s.t. ``mu' w >= R``;
    * target volatility: ``max mu' w`` s.t. ``w' Sigma w <= sigma*^2``;
    * risk aversion ``gamma``: ``max mu' w - (gamma/2) w' Sigma w``.
    """
    cons = cons or Constraints()
    names = list(sigma.columns)
    m = mu.reindex(names).to_numpy(float)
    s = sigma.to_numpy(float)
    n = len(m)
    given = [x is not None for x in (target_return, target_vol, risk_aversion)]
    if sum(given) != 1:
        raise ValueError("give exactly one of target_return, target_vol, risk_aversion")
    if target_return is not None:
        hi = max_return(mu, cons, names)
        if target_return > hi + 1e-9:
            raise ValueError(f"target return {target_return:.2%} exceeds the maximum feasible {hi:.2%}")
        ss, _ = _scaled(s)
        return _solve(n, lambda w: float(w @ ss @ w), lambda w: 2 * ss @ w, cons,
                      extra=[{"type": "ineq", "fun": lambda w: float(m @ w - target_return),
                              "jac": lambda w: m}])
    if target_vol is not None:
        w_gmv = min_variance(sigma, cons)
        v_min = float(np.sqrt(w_gmv @ s @ w_gmv))
        if target_vol < v_min - 1e-9:
            raise ValueError(f"target volatility {target_vol:.2%} is below the minimum attainable {v_min:.2%}")
        tv2 = target_vol ** 2
        return _solve(n, lambda w: -float(m @ w), lambda w: -m, cons, x0=w_gmv,
                      extra=[{"type": "ineq", "fun": lambda w: (tv2 - float(w @ s @ w)) / tv2,
                              "jac": lambda w: -2 * s @ w / tv2}])
    g = float(risk_aversion)  # type: ignore[arg-type]
    if g <= 0:
        raise ValueError("risk aversion must be positive")
    if cons.max_turnover is None and cons.max_gross is None:
        lo, hi = cons.bounds(n)
        w = active_set_qp(g * s, -m, np.ones(n), 1.0, lo, hi)
        if w is not None:
            return w
    return _solve(n, lambda w: -float(m @ w) + 0.5 * g * float(w @ s @ w),
                  lambda w: -m + g * s @ w, cons)


def _max_ratio(a: np.ndarray, sigma: np.ndarray, cons: Constraints) -> np.ndarray:
    """``max a'w / sqrt(w' Sigma w)`` over the (homogeneous) constraint set.

    Cornuejols & Tutuncu (2007, sec. 8.2): with ``y = kappa w``, ``kappa > 0``, solve the convex QP
    ``min y' Sigma y`` s.t. ``a'y = 1``, ``1'y = kappa``, ``lo kappa <= y <= hi kappa``,
    ``[sum|y| <= L kappa]``; then ``w = y / kappa``.
    """
    n = len(a)
    lo, hi = cons.bounds(n)
    s, _ = _scaled(sigma)
    if cons.max_gross is None and np.all(lo == 0) and np.all(hi >= 1):
        # cone {y >= 0}: min y'Sigma y s.t. a'y = 1, y >= 0 is a bound-constrained QP
        y = active_set_qp(2 * s, np.zeros(n), a, 1.0, np.zeros(n), np.full(n, np.inf))
        if y is not None and y.sum() > 1e-12:
            return y / y.sum()
    use_g = cons.max_gross is not None
    m = n + 1 + (n if use_g else 0)
    k = n  # kappa index
    cl: list[dict[str, Any]] = []

    def lin(row: np.ndarray, kind: str, b: float = 0.0) -> dict[str, Any]:
        return {"type": kind, "fun": lambda x, r=row: r @ x - b, "jac": lambda x, r=row: r[None, :]}

    r = np.zeros(m)
    r[:n] = a
    cl.append(lin(r, "eq", 1.0))
    r = np.zeros(m)
    r[:n] = 1.0
    r[k] = -1.0
    cl.append(lin(r, "eq"))
    rows: list[np.ndarray] = []
    for i in range(n):
        if np.isfinite(lo[i]):  # y_i - lo_i kappa >= 0
            r = np.zeros(m)
            r[i], r[k] = 1.0, -lo[i]
            rows.append(r)
        if np.isfinite(hi[i]):  # hi_i kappa - y_i >= 0
            r = np.zeros(m)
            r[i], r[k] = -1.0, hi[i]
            rows.append(r)
    bnds: list[tuple[float | None, float | None]] = [(None, None)] * n + [(0.0, None)]
    if use_g:
        gi = n + 1
        for i in range(n):
            r = np.zeros(m)
            r[gi + i], r[i] = 1.0, -1.0
            rows.append(r)
            r = np.zeros(m)
            r[gi + i], r[i] = 1.0, 1.0
            rows.append(r)
        r = np.zeros(m)
        r[gi:gi + n] = -1.0
        r[k] = float(cons.max_gross)  # type: ignore[arg-type]
        rows.append(r)
        bnds += [(0.0, None)] * n
    if rows:
        a_in = np.vstack(rows)
        cl.append({"type": "ineq", "fun": lambda x: a_in @ x, "jac": lambda x: a_in})
    # start: best single feasible direction scaled so a'y = 1
    w_start = np.clip(np.full(n, 1.0 / n), lo, hi)
    if a @ w_start <= 0:
        w_start = np.clip(np.where(a > 0, 1.0, 0.0), lo, hi)
        w_start = w_start / max(w_start.sum(), 1e-12)
    kap = 1.0 / max(float(a @ w_start), 1e-6)
    x0 = np.zeros(m)
    x0[:n] = w_start * kap
    x0[k] = kap
    if use_g:
        x0[n + 1:] = np.abs(x0[:n]) + 1e-9

    res = minimize(lambda x: float(x[:n] @ s @ x[:n]),
                   x0, jac=lambda x: np.r_[2 * s @ x[:n], np.zeros(m - n)],
                   method="SLSQP", bounds=bnds, constraints=cl, options={"maxiter": 3000, "ftol": 1e-15})
    y, kap = res.x[:n], res.x[k]
    if kap <= 1e-12:
        raise ValueError(f"max-ratio problem degenerate: {res.message}")
    w = y / kap
    if not cons.satisfied(w, 1e-4):
        raise ValueError(f"max-ratio optimizer failed: {res.message}")
    return np.clip(w, lo, hi)


def max_sharpe(mu: pd.Series, sigma: pd.DataFrame, rf: float, cons: Constraints | None = None) -> np.ndarray:
    """Tangency portfolio ``max (mu'w - rf)/sqrt(w'Sigma w)``.

    Solved via :func:`_max_ratio` with ``a = mu - rf`` (Cornuejols-Tutuncu). With a
    turnover limit the feasible set is not a cone, so the maximum is found by
    golden-section search over target returns on the constrained frontier (the
    Sharpe ratio is unimodal along the concave efficient frontier). Unconstrained
    closed form: ``w = Sigma^{-1}(mu - rf 1) / 1' Sigma^{-1} (mu - rf 1)``.
    """
    cons = cons or Constraints()
    names = list(sigma.columns)
    m = mu.reindex(names).to_numpy(float)
    s = sigma.to_numpy(float)
    ex = m - rf
    if cons.max_turnover is None:
        hi_ret = max_return(mu, Constraints(cons.lower, cons.upper, cons.max_gross), names)
        if hi_ret <= rf + 1e-12:
            raise ValueError("no feasible portfolio has an expected return above the risk-free rate; "
                             "the tangency portfolio does not exist")
        return _max_ratio(ex, s, cons)
    w_gmv = min_variance(sigma, cons)
    lo_r = float(m @ w_gmv)
    hi_r = max_return(mu, cons, names)
    if hi_r <= rf + 1e-12:
        raise ValueError("no feasible portfolio has an expected return above the risk-free rate")

    def neg_sharpe(r: float) -> tuple[float, np.ndarray]:
        w = mean_variance(mu, sigma, cons, target_return=r)
        return -(m @ w - rf) / np.sqrt(w @ s @ w), w

    a, b = max(lo_r, rf), hi_r
    gr = (np.sqrt(5) - 1) / 2
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, wc = neg_sharpe(c)
    fd, wd = neg_sharpe(d)
    for _ in range(40):
        if abs(b - a) < 1e-6:
            break
        if fc < fd:
            b, d, fd, wd = d, c, fc, wc
            c = b - gr * (b - a)
            fc, wc = neg_sharpe(c)
        else:
            a, c, fc, wc = c, d, fd, wd
            d = a + gr * (b - a)
            fd, wd = neg_sharpe(d)
    return wc if fc < fd else wd


def max_diversification(sigma: pd.DataFrame, cons: Constraints | None = None) -> np.ndarray:
    """Most-diversified portfolio (Choueifaty & Coignard 2008):
    ``max DR(w) = w' sigma / sqrt(w' Sigma w)``, the max-ratio problem with ``a = sigma``."""
    cons = cons or Constraints()
    if cons.max_turnover is not None:
        raise ValueError("max diversification does not support a turnover limit")
    s = sigma.to_numpy(float)
    return _max_ratio(np.sqrt(np.diag(s)), s, cons)


def risk_parity(sigma: pd.DataFrame, budgets: np.ndarray | None = None, tol: float = 1e-12,
                max_iter: int = 200) -> tuple[np.ndarray, int]:
    """Equal (or budgeted) risk contribution, long-only (Maillard, Roncalli & Teiletche 2010).

    Spinu (2013): the unique minimiser of the strictly convex
    ``F(y) = 1/2 y' Sigma y - sum_i b_i ln y_i`` over ``y > 0`` satisfies
    ``y_i (Sigma y)_i = b_i``; hence ``w = y / 1'y`` has ``RC_i / sigma_p = b_i``.
    Solved by damped Newton (gradient ``Sigma y - b/y``, Hessian ``Sigma + diag(b/y^2)``)
    with a positivity-preserving backtracking line search. Returns weights and iterations.
    """
    s = sigma.to_numpy(float)
    s = s / np.mean(np.diag(s))
    n = len(s)
    b = np.full(n, 1.0 / n) if budgets is None else np.asarray(budgets, float)
    if b.shape != (n,) or np.any(b <= 0):
        raise ValueError("risk budgets must be positive, one per asset")
    b = b / b.sum()
    y = b / np.sqrt(np.diag(s))
    y = y / np.sqrt(y @ s @ y)

    def f(v: np.ndarray) -> float:
        return 0.5 * float(v @ s @ v) - float(b @ np.log(v))

    it = 0
    for it in range(1, max_iter + 1):  # noqa: B007
        g = s @ y - b / y
        h = s + np.diag(b / y ** 2)
        step = np.linalg.solve(h, g)
        dec = float(g @ step)
        if dec / 2 < tol:
            break
        t = 1.0
        while np.any(y - t * step <= 0):
            t *= 0.5
        fy = f(y)
        while f(y - t * step) > fy - 0.25 * t * dec and t > 1e-12:
            t *= 0.5
        y = y - t * step
    w = y / y.sum()
    return w, it


def min_cvar(scenarios: pd.DataFrame, alpha: float = 0.95, cons: Constraints | None = None,
             mu: pd.Series | None = None, target_return: float | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Minimum CVaR on historical scenarios (Rockafellar & Uryasev 2000) as an LP.

    ``min_{w, zeta, u} zeta + (1 / ((1-alpha) S)) sum_s u_s``
    s.t. ``u_s >= -r_s' w - zeta``, ``u_s >= 0``, ``1'w = 1``, bounds,
    ``[mu' w >= R]``, ``[sum t <= T, t >= +-(w - w0)]``, ``[sum g <= L, g >= +-w]``.
    At the optimum ``zeta`` is a VaR_alpha and the objective is CVaR_alpha of the
    daily scenario loss ``L_s = -r_s' w``. Solved with HiGHS via ``scipy.optimize.linprog``.
    """
    cons = cons or Constraints()
    x = scenarios.to_numpy(float)
    ns, n = x.shape
    cons.validate(n)
    lo, hi = cons.bounds(n)
    if not 0.5 < alpha < 1:
        raise ValueError("alpha must be in (0.5, 1)")
    use_t = cons.max_turnover is not None
    use_g = cons.max_gross is not None
    nt = n if use_t else 0
    ng = n if use_g else 0
    # variable order: w (n), zeta (1), u (ns), t (nt), g (ng)
    nv = n + 1 + ns + nt + ng
    c = np.zeros(nv)
    c[n] = 1.0
    c[n + 1:n + 1 + ns] = 1.0 / ((1 - alpha) * ns)
    # -r_s'w - zeta - u_s <= 0
    rows = [hstack([csr_matrix(-x), csr_matrix(-np.ones((ns, 1))), -identity(ns, format="csr"),
                    csr_matrix((ns, nt + ng))])]
    rhs = [np.zeros(ns)]
    if use_t:
        cur = np.asarray(cons.current, float)
        e = identity(n, format="csr")
        z = csr_matrix((n, 1 + ns))
        zg = csr_matrix((n, ng))
        rows += [hstack([e, z, -e, zg]), hstack([-e, z, -e, zg]),
                 hstack([csr_matrix((1, n + 1 + ns)), csr_matrix(np.ones((1, n))), csr_matrix((1, ng))])]
        rhs += [cur, -cur, np.array([cons.max_turnover])]
    if use_g:
        e = identity(n, format="csr")
        z = csr_matrix((n, 1 + ns + nt))
        rows += [hstack([e, z, -e]), hstack([-e, z, -e]),
                 hstack([csr_matrix((1, n + 1 + ns + nt)), csr_matrix(np.ones((1, n)))])]
        rhs += [np.zeros(n), np.zeros(n), np.array([cons.max_gross])]
    if target_return is not None:
        if mu is None:
            raise ValueError("target_return needs expected returns")
        mrow = np.zeros((1, nv))
        mrow[0, :n] = -mu.reindex(scenarios.columns).to_numpy(float)
        rows.append(csr_matrix(mrow))
        rhs.append(np.array([-target_return]))
    a_ub = vstack(rows).tocsr()
    b_ub = np.concatenate(rhs)
    a_eq = np.zeros((1, nv))
    a_eq[0, :n] = 1.0
    bounds = ([(float(a), float(b)) for a, b in zip(lo, hi, strict=True)] + [(None, None)]
              + [(0.0, None)] * (ns + nt + ng))
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=[1.0], bounds=bounds, method="highs")
    if res.status != 0:
        raise ValueError(f"min-CVaR LP failed: {res.message}")
    w = res.x[:n]
    return w, {"cvar_daily": float(res.fun), "var_daily": float(res.x[n]), "alpha": alpha,
               "scenarios": ns}


def empirical_cvar(losses: np.ndarray, alpha: float) -> float:
    """Rockafellar-Uryasev CVaR of an empirical loss sample:
    ``CVaR = min_z z + E[(L - z)^+] / (1 - alpha)`` (minimised at the alpha-quantile)."""
    loss = np.sort(np.asarray(losses, float))
    s = loss.size
    k = int(np.ceil(alpha * s)) - 1  # index of the upper alpha-quantile
    z = loss[max(k, 0)]
    return float(z + np.mean(np.maximum(loss - z, 0.0)) / (1 - alpha))


def inverse_volatility(sigma: pd.DataFrame) -> np.ndarray:
    """``w_i = sigma_i^{-1} / sum_j sigma_j^{-1}`` (naive risk parity; ERC when correlations are equal)."""
    iv = 1.0 / np.sqrt(np.diag(sigma.to_numpy(float)))
    return iv / iv.sum()


def equal_weight(n: int) -> np.ndarray:
    """``w_i = 1/N`` (DeMiguel, Garlappi & Uppal 2009)."""
    return np.full(n, 1.0 / n)


# ------------------------------------------------------------------ frontier
def efficient_frontier(mu: pd.Series, sigma: pd.DataFrame, cons: Constraints | None = None,
                       points: int = 40, rf: float | None = None) -> dict[str, Any]:
    """Constrained efficient frontier from the GMV return to the maximum feasible return.

    Each point solves ``min w'Sigma w`` s.t. ``mu'w >= R_k`` on an even grid of ``R_k``.
    With ``rf``, adds the tangency portfolio and the capital market line
    ``E[r] = rf + SR_T sigma`` (Sharpe 1964; Tobin 1958 separation).
    """
    cons = cons or Constraints()
    names = list(sigma.columns)
    m = mu.reindex(names).to_numpy(float)
    s = sigma.to_numpy(float)
    w_gmv = min_variance(sigma, cons)
    r_lo = float(m @ w_gmv)
    r_hi = max_return(mu, cons, names)
    grid = np.linspace(r_lo, r_hi, max(points, 2))
    ss, _ = _scaled(s)
    rows: list[dict[str, Any]] = []
    prev = w_gmv
    for i, r in enumerate(grid):
        w = w_gmv if i == 0 else _solve(len(m), lambda w: float(w @ ss @ w), lambda w: 2 * ss @ w, cons,
                                        x0=prev, extra=[{"type": "ineq",
                                                         "fun": lambda w, r=r: float(m @ w - r),
                                                         "jac": lambda w: m}])
        prev = w
        vol = float(np.sqrt(w @ s @ w))
        ret = float(m @ w)
        rows.append({"ret": ret, "vol": vol,
                     "sharpe": (ret - rf) / vol if rf is not None and vol > 0 else None,
                     "weights": dict(zip(names, w.tolist(), strict=True))})
    out: dict[str, Any] = {"points": rows, "gmv": {"ret": r_lo, "vol": float(np.sqrt(w_gmv @ s @ w_gmv)),
                                                   "weights": dict(zip(names, w_gmv.tolist(), strict=True))}}
    if rf is not None and r_hi > rf:
        wt = max_sharpe(mu, sigma, rf, cons)
        tv, tr = float(np.sqrt(wt @ s @ wt)), float(m @ wt)
        sr = (tr - rf) / tv
        vmax = max(p["vol"] for p in rows) * 1.1
        out["tangency"] = {"ret": tr, "vol": tv, "sharpe": sr,
                           "weights": dict(zip(names, wt.tolist(), strict=True))}
        out["cml"] = [{"vol": v, "ret": rf + sr * v} for v in np.linspace(0, vmax, 25)]
    return out
