"""Treasury term structure: bootstrap, parametric fits, comparison and PCA.

Inputs are FRED constant-maturity Treasury (CMT) yields: *par* yields of
on-the-run securities, in PERCENT, quoted on a bond-equivalent (semiannual)
basis, with tenors in years (``MarketData.treasury_curve``).

Conventions used throughout this module
---------------------------------------
* Times ``t`` are in years.
* Discount factors ``P(t)``; continuously-compounded zero rates
  ``z(t) = -ln P(t) / t``; all rates returned in PERCENT.
* Instantaneous forward ``f(t) = d/dt [ t z(t) ] = -d ln P(t)/dt``;
  1y-forward starting at ``t``: ``F(t, t+1) = (t+1) z(t+1) - t z(t)``
  (continuously compounded).

References
----------
* Hull, J. C. *Options, Futures, and Other Derivatives*, ch. 4 ("Interest
  rates": the bootstrap method).
* Fritsch, F. N. & Carlson, R. E. (1980), "Monotone Piecewise Cubic
  Interpolation", SIAM J. Numer. Anal. 17(2) -- the PCHIP interpolant.
* Nelson, C. R. & Siegel, A. F. (1987), "Parsimonious Modeling of Yield
  Curves", Journal of Business 60(4).
* Svensson, L. E. O. (1994), "Estimating and Interpreting Forward Interest
  Rates: Sweden 1992-1994", NBER WP 4871.
* Gurkaynak, R. S., Sack, B. & Wright, J. H. (2007), "The U.S. Treasury
  Yield Curve: 1961 to the Present", J. Monetary Economics 54(8).
* Litterman, R. & Scheinkman, J. (1991), "Common Factors Affecting Bond
  Returns", Journal of Fixed Income 1(1).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.interpolate import PchipInterpolator

__all__ = [
    "ZeroCurve",
    "bootstrap_par_curve",
    "bootstrap_bonds",
    "BondQuote",
    "nelson_siegel",
    "svensson",
    "fit_nelson_siegel",
    "fit_svensson",
    "ParametricFit",
    "curve_on",
    "yield_pca",
    "curve_spreads",
    "monthly_sample",
]


# --------------------------------------------------------------------------
# Zero curve
# --------------------------------------------------------------------------
@dataclass
class ZeroCurve:
    """A discount curve on nodes ``t`` (years) with discount factors ``df``.

    Between nodes, ``-ln P(t)`` is interpolated with a monotone cubic
    (PCHIP, Fritsch & Carlson 1980) anchored at ``(0, 0)``; because
    ``-ln P`` is increasing for positive rates the interpolant preserves
    positive forwards whenever the node data imply them. Beyond the last
    node the last instantaneous forward is held flat.
    """

    t: np.ndarray
    df: np.ndarray
    par: np.ndarray | None = None  # par yields (percent) on the nodes, if bootstrapped from par
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.df = np.asarray(self.df, dtype=float)
        if np.any(self.df <= 0) or not np.all(np.isfinite(self.df)):
            raise ValueError("bootstrapped discount factors must be positive and finite")
        tt = np.concatenate([[0.0], self.t])
        yy = np.concatenate([[0.0], -np.log(self.df)])
        self._interp = PchipInterpolator(tt, yy, extrapolate=True)
        self._dinterp = self._interp.derivative()
        self._tmax = float(self.t[-1])

    # -ln P(t)
    def _neg_log_df(self, t: np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        inside = self._interp(np.minimum(t, self._tmax))
        fwd_end = float(self._dinterp(self._tmax))
        return np.where(t > self._tmax, inside + fwd_end * (t - self._tmax), inside)

    def discount(self, t: Any) -> np.ndarray:
        """Discount factor ``P(t)`` (``P(0) = 1``)."""
        return np.exp(-self._neg_log_df(np.asarray(t, dtype=float)))

    def zero(self, t: Any) -> np.ndarray:
        """Continuously-compounded zero rate ``z(t) = -ln P(t)/t``, percent."""
        t = np.asarray(t, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = self._neg_log_df(t) / t
        # t -> 0 limit: the instantaneous short rate
        z = np.where(t <= 1e-12, float(self._dinterp(0.0)), z)
        return 100.0 * z

    def inst_forward(self, t: Any) -> np.ndarray:
        """Instantaneous forward ``f(t) = -d ln P/dt``, percent."""
        t = np.asarray(t, dtype=float)
        fwd_end = float(self._dinterp(self._tmax))
        return 100.0 * np.where(t > self._tmax, fwd_end, self._dinterp(np.minimum(t, self._tmax)))

    def forward(self, t: Any, tenor: float = 1.0) -> np.ndarray:
        """Continuously-compounded forward rate for ``[t, t+tenor]``, percent:
        ``F = [ln P(t) - ln P(t+tenor)] / tenor``."""
        t = np.asarray(t, dtype=float)
        return 100.0 * (self._neg_log_df(t + tenor) - self._neg_log_df(t)) / tenor

    def par_yield(self, maturity: float, freq: int = 2) -> float:
        """Par coupon (percent, compounded ``freq`` times a year) of a bond
        maturing at ``maturity`` with coupons on the regular grid:
        ``c = freq (1 - P(T)) / sum_k P(t_k)``."""
        n = int(round(maturity * freq))
        if n < 1 or abs(n / freq - maturity) > 1e-9:
            raise ValueError("maturity must be a whole number of coupon periods")
        tk = np.arange(1, n + 1) / freq
        p = self.discount(tk)
        return 100.0 * freq * (1.0 - p[-1]) / p.sum()

    def table(self, grid: Sequence[float] | None = None) -> pd.DataFrame:
        """Zero, instantaneous-forward and 1y-forward curves on ``grid``."""
        g = np.asarray(grid if grid is not None else self.t, dtype=float)
        return pd.DataFrame({
            "t": g,
            "discount": self.discount(g),
            "zero": self.zero(g),
            "inst_forward": self.inst_forward(g),
            "forward_1y": self.forward(g, 1.0),
        })


def bootstrap_par_curve(tenors: Sequence[float], par_yields_pct: Sequence[float], freq: int = 2,
                        bill_cutoff: float = 0.5) -> ZeroCurve:
    """Bootstrap discount factors from CMT par yields.

    1. Par yields are interpolated in tenor with a monotone cubic (PCHIP) onto
       the coupon grid ``t_k = k/freq`` up to the longest quoted tenor.
    2. Tenors ``t <= bill_cutoff`` (bills; 1M/3M/6M) are single-payment
       instruments on a bond-equivalent simple basis:
       ``P(t) = 1 / (1 + y t)``.
    3. Each later grid node ``t_n`` is a par bond with coupon ``c = y(t_n)``
       paying ``c/freq`` at every ``t_k``:

       ``1 = (c/f) sum_{k<n} P(t_k) + (1 + c/f) P(t_n)``  =>
       ``P(t_n) = (1 - (c/f) sum_{k<n} P(t_k)) / (1 + c/f)``.

    (Hull, ch. 4; the same procedure the U.S. Treasury uses to derive its
    par/spot curves from CMT.) Note that at ``t = 1/freq`` both rules agree:
    ``1/(1 + y/f)``. Re-pricing each grid par bond on the result returns par
    to machine precision (tested).

    Tenors shorter than the first grid node that are quoted (1M, 3M) are kept
    as extra nodes. If the shortest quote is longer than ``1/freq`` the par
    curve is held flat down to ``1/freq`` and a note says so.
    """
    t_in = np.asarray(tenors, dtype=float)
    y_in = np.asarray(par_yields_pct, dtype=float) / 100.0
    ok = np.isfinite(t_in) & np.isfinite(y_in)
    t_in, y_in = t_in[ok], y_in[ok]
    order = np.argsort(t_in)
    t_in, y_in = t_in[order], y_in[order]
    if t_in.size < 3:
        raise ValueError("need at least 3 quoted tenors to bootstrap a curve")
    notes: list[str] = []
    step = 1.0 / freq
    if t_in[0] > step + 1e-12:
        notes.append(f"shortest quoted tenor is {t_in[0]:.2f}y; par curve held flat below it")
        t_in = np.concatenate([[step], t_in])
        y_in = np.concatenate([[y_in[0]], y_in])
    par_interp = PchipInterpolator(t_in, y_in, extrapolate=False)

    short = t_in[t_in < step - 1e-12]
    n_max = int(np.floor(t_in[-1] * freq + 1e-9))
    grid = np.arange(1, n_max + 1) / freq
    nodes_t: list[float] = []
    nodes_df: list[float] = []
    nodes_par: list[float] = []
    for t, y in zip(short, y_in[: short.size], strict=True):
        if t <= bill_cutoff + 1e-12:
            nodes_t.append(float(t))
            nodes_df.append(1.0 / (1.0 + y * t))
            nodes_par.append(100.0 * y)
    coupon_df_sum = 0.0
    for tn in grid:
        c = float(par_interp(tn))
        if tn <= bill_cutoff + 1e-12 and tn < step + 1e-12:
            p = 1.0 / (1.0 + c * tn)
        else:
            p = (1.0 - c / freq * coupon_df_sum) / (1.0 + c / freq)
        if not p > 0:
            raise ValueError(f"bootstrap produced a non-positive discount factor at {tn}y")
        coupon_df_sum += p
        nodes_t.append(float(tn))
        nodes_df.append(p)
        nodes_par.append(100.0 * c)
    return ZeroCurve(np.array(nodes_t), np.array(nodes_df), np.array(nodes_par), notes)


@dataclass(frozen=True)
class BondQuote:
    """A bond used in a textbook bootstrap: ``price`` per 100 face,
    ``coupon`` annual rate (decimal), coupons paid ``freq`` times a year."""

    maturity: float
    price: float
    coupon: float = 0.0
    freq: int = 2
    face: float = 100.0


def bootstrap_bonds(bonds: Sequence[BondQuote]) -> pd.DataFrame:
    """Hull's bootstrap from bond *prices* (continuous compounding).

    Bonds are processed by maturity. For each bond, cash flows at times that
    are already nodes use their known zero rate; cash flows between the last
    known node and the maturity use a zero rate linearly interpolated between
    that node and the unknown ``z_T``, which is solved by Brent's method so
    that ``sum_k CF_k exp(-z(t_k) t_k) = price``. Returns node zero rates
    (percent, continuous) -- reproduces Hull's Table 4.3/4.4 example (tested).
    """
    known_t: list[float] = []
    known_z: list[float] = []
    for b in sorted(bonds, key=lambda x: x.maturity):
        n = int(round(b.maturity * b.freq))
        if b.coupon > 0 and abs(n / b.freq - b.maturity) < 1e-9:
            cf_t = np.arange(1, n + 1) / b.freq
            cf = np.full(n, b.face * b.coupon / b.freq)
            cf[-1] += b.face
        else:
            cf_t = np.array([b.maturity])
            cf = np.array([b.face * (1 + (b.coupon / b.freq if b.coupon > 0 else 0.0))])
        t_last = known_t[-1] if known_t else 0.0
        z_last = known_z[-1] if known_z else None

        def z_at(tt: np.ndarray, z_t: float, t_last: float = t_last, z_last: float | None = z_last,
                 b: BondQuote = b) -> np.ndarray:
            out = np.empty_like(tt)
            for i, s in enumerate(tt):
                if known_t and s <= t_last + 1e-12:
                    out[i] = np.interp(s, known_t, known_z)
                elif z_last is None:
                    out[i] = z_t
                else:
                    w = (s - t_last) / (b.maturity - t_last)
                    out[i] = z_last + w * (z_t - z_last)
            return out

        def f(z_t: float, cf_t: np.ndarray = cf_t, cf: np.ndarray = cf, b: BondQuote = b,
              z_at: Callable[..., np.ndarray] = z_at) -> float:
            return float(np.sum(cf * np.exp(-z_at(cf_t, z_t) * cf_t)) - b.price)

        z = optimize.brentq(f, -0.5, 2.0, xtol=1e-14, maxiter=500)
        known_t.append(b.maturity)
        known_z.append(z)
    return pd.DataFrame({"t": known_t, "zero_cc_pct": 100.0 * np.array(known_z)})


# --------------------------------------------------------------------------
# Nelson-Siegel / Svensson
# --------------------------------------------------------------------------
def _ns_loadings(t: np.ndarray, tau: float) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(t, dtype=float) / tau
    with np.errstate(divide="ignore", invalid="ignore"):
        l1 = np.where(x < 1e-10, 1.0 - x / 2.0, -np.expm1(-x) / x)
    return l1, l1 - np.exp(-x)


def nelson_siegel(t: Any, b0: float, b1: float, b2: float, tau: float) -> np.ndarray:
    """Nelson & Siegel (1987) zero curve:

    ``y(t) = b0 + b1 (1 - e^{-t/tau})/(t/tau) + b2 [(1 - e^{-t/tau})/(t/tau) - e^{-t/tau}]``

    ``b0`` is the long-run level, ``b0 + b1`` the instantaneous short rate,
    ``b2`` the hump whose location is governed by ``tau``.
    """
    l1, l2 = _ns_loadings(np.asarray(t, dtype=float), tau)
    return b0 + b1 * l1 + b2 * l2


def svensson(t: Any, b0: float, b1: float, b2: float, b3: float, tau1: float, tau2: float) -> np.ndarray:
    """Svensson (1994) extension: NS plus a second hump
    ``b3 [(1 - e^{-t/tau2})/(t/tau2) - e^{-t/tau2}]`` (the GSW 2007 form)."""
    l1, l2 = _ns_loadings(np.asarray(t, dtype=float), tau1)
    _, l3 = _ns_loadings(np.asarray(t, dtype=float), tau2)
    return b0 + b1 * l1 + b2 * l2 + b3 * l3


@dataclass
class ParametricFit:
    model: str
    params: dict[str, float]
    rmse_bp: float
    fitted: np.ndarray
    residuals_bp: np.ndarray
    starts: int
    func: Callable[[np.ndarray], np.ndarray]

    def __call__(self, t: Any) -> np.ndarray:
        return self.func(np.asarray(t, dtype=float))

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "params": self.params, "rmse_bp": self.rmse_bp,
                "fitted": self.fitted.tolist(), "residuals_bp": self.residuals_bp.tolist(),
                "starts": self.starts}


def _betas_given_taus(t: np.ndarray, y: np.ndarray, taus: Sequence[float],
                      constrained: bool = True) -> tuple[np.ndarray, float]:
    """OLS betas for fixed decay parameters. With ``constrained`` the
    economically required sign restrictions of the Bundesbank / ECB
    estimation practice are imposed: long-run level ``b0 >= 0`` and
    instantaneous short rate ``b0 + b1 >= 0`` (solved as a box-constrained
    least-squares problem in ``(b0, b0 + b1, b2[, b3])``). Without them the
    Svensson level is not identified when ``tau2`` approaches the longest
    maturity and ``b0`` can drift to economically meaningless values (e.g.
    -85%) that offset an equally huge ``b3``."""
    cols = [np.ones_like(t)]
    l1, l2 = _ns_loadings(t, taus[0])
    cols += [l1, l2]
    if len(taus) > 1:
        cols.append(_ns_loadings(t, taus[1])[1])
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    if constrained and (beta[0] < 0 or beta[0] + beta[1] < 0):
        # y = b0 (1 - l1) + s l1 + ...,  s = b0 + b1
        Z = X.copy()
        Z[:, 0] = 1.0 - l1
        Z[:, 1] = l1
        lo = np.full(X.shape[1], -np.inf)
        lo[:2] = 0.0
        g = optimize.lsq_linear(Z, y, bounds=(lo, np.full(X.shape[1], np.inf)), method="bvls").x
        beta = g.copy()
        beta[1] = g[1] - g[0]
    resid = y - X @ beta
    return beta, float(resid @ resid)


def _fit_parametric(t: np.ndarray, y: np.ndarray, n_tau: int, tau_bounds: Sequence[tuple[float, float]],
                    n_grid: int, constrained: bool = True) -> tuple[np.ndarray, np.ndarray, int]:
    """Variable-projection NLS: for fixed decay parameters the betas are a
    (sign-constrained) linear least-squares problem, so the SSR is profiled
    over the taus. The profile is scanned on a log-spaced grid (a
    deterministic multi-start, cf. GSW 2007 who use many starting values) and
    the best few points are polished with bounded L-BFGS-B on ``log tau``.
    For Svensson the identification ``tau2 > 1.05 tau1`` is enforced on the
    grid and re-checked after polishing (a polished point that violates it
    is discarded in favour of its grid start)."""
    grids = [np.geomspace(lo, hi, n_grid) for lo, hi in tau_bounds]
    cands: list[tuple[float, tuple[float, ...]]] = []
    if n_tau == 1:
        for a in grids[0]:
            cands.append((_betas_given_taus(t, y, [a], constrained)[1], (a,)))
    else:
        for a in grids[0]:
            for b in grids[1]:
                if b <= a * 1.05:  # identify: second hump strictly longer
                    continue
                cands.append((_betas_given_taus(t, y, [a, b], constrained)[1], (a, b)))
    cands.sort(key=lambda c: c[0])
    best_ssr, best_tau = np.inf, None
    log_b = [(np.log(lo), np.log(hi)) for lo, hi in tau_bounds]
    starts = min(5, len(cands))
    for ssr0, tau0 in cands[:starts]:
        res = optimize.minimize(
            lambda lt: _betas_given_taus(t, y, np.exp(lt), constrained)[1], np.log(tau0), method="L-BFGS-B",
            bounds=log_b)
        tau_new, ssr_new = np.exp(res.x), float(res.fun)
        if n_tau == 2 and tau_new[1] <= tau_new[0] * 1.05:
            tau_new, ssr_new = np.asarray(tau0), float(ssr0)
        if ssr_new < best_ssr:
            best_ssr, best_tau = ssr_new, tau_new
    assert best_tau is not None
    beta, _ = _betas_given_taus(t, y, best_tau, constrained)
    return beta, np.asarray(best_tau), starts


def fit_nelson_siegel(t: Sequence[float], y: Sequence[float],
                      tau_bounds: tuple[float, float] = (0.05, 30.0), n_grid: int = 60,
                      constrained: bool = True) -> ParametricFit:
    """Fit :func:`nelson_siegel` to zero rates ``y`` (percent) by nonlinear
    least squares over ``(b0, b1, b2, tau)`` with ``tau`` bounded and (by
    default) ``b0 >= 0``, ``b0 + b1 >= 0``; RMSE in bp."""
    tt, yy = np.asarray(t, dtype=float), np.asarray(y, dtype=float)
    if tt.size < 4:
        raise ValueError("Nelson-Siegel needs at least 4 points")
    beta, tau, starts = _fit_parametric(tt, yy, 1, [tau_bounds], n_grid, constrained)
    p = {"b0": beta[0], "b1": beta[1], "b2": beta[2], "tau": float(tau[0])}
    fitted = nelson_siegel(tt, **p)
    resid = 100.0 * (yy - fitted)
    return ParametricFit("nelson_siegel", {k: float(v) for k, v in p.items()},
                         float(np.sqrt(np.mean(resid**2))), fitted, resid, starts,
                         lambda s, p=p: nelson_siegel(s, **p))


def fit_svensson(t: Sequence[float], y: Sequence[float],
                 tau1_bounds: tuple[float, float] = (0.05, 10.0),
                 tau2_bounds: tuple[float, float] = (1.0, 30.0), n_grid: int = 30,
                 constrained: bool = True) -> ParametricFit:
    """Fit :func:`svensson` (6 params) to zero rates (percent); requires
    ``tau2 > 1.05 tau1`` for identification and (by default) the sign
    restrictions ``b0 >= 0``, ``b0 + b1 >= 0`` (Bundesbank / ECB practice).
    RMSE in bp."""
    tt, yy = np.asarray(t, dtype=float), np.asarray(y, dtype=float)
    if tt.size < 6:
        raise ValueError("Svensson needs at least 6 points")
    beta, tau, starts = _fit_parametric(tt, yy, 2, [tau1_bounds, tau2_bounds], n_grid, constrained)
    p = {"b0": beta[0], "b1": beta[1], "b2": beta[2], "b3": beta[3],
         "tau1": float(tau[0]), "tau2": float(tau[1])}
    fitted = svensson(tt, **p)
    resid = 100.0 * (yy - fitted)
    return ParametricFit("svensson", {k: float(v) for k, v in p.items()},
                         float(np.sqrt(np.mean(resid**2))), fitted, resid, starts,
                         lambda s, p=p: svensson(s, **p))


# --------------------------------------------------------------------------
# History helpers
# --------------------------------------------------------------------------
def curve_on(curves: pd.DataFrame, when: pd.Timestamp | str, min_tenors: int = 5) -> tuple[pd.Timestamp, pd.Series]:
    """The last observed curve on or before ``when`` with at least
    ``min_tenors`` quoted tenors: returns ``(actual_date, par yields)``."""
    ts = pd.Timestamp(when).normalize()
    counts = curves.notna().sum(axis=1)
    eligible = curves.loc[(curves.index <= ts) & (counts >= min_tenors)]
    if eligible.empty:
        raise ValueError(f"no Treasury curve with >= {min_tenors} tenors on or before {ts.date()}")
    d = eligible.index[-1]
    return d, eligible.iloc[-1].dropna()


def curve_spreads(curves: pd.DataFrame) -> pd.DataFrame:
    """Standard curve spreads in basis points:
    ``2s10s = 10y - 2y``, ``3m10y = 10y - 3m``, ``5s30s = 30y - 5y``,
    butterfly ``2s5s10s = 2*5y - 2y - 10y`` (positive = belly cheap/high)."""
    def col(x: float) -> pd.Series:
        return curves[x] if x in curves.columns else pd.Series(np.nan, index=curves.index)

    out = pd.DataFrame({
        "2s10s": 100 * (col(10.0) - col(2.0)),
        "3m10y": 100 * (col(10.0) - col(0.25)),
        "5s30s": 100 * (col(30.0) - col(5.0)),
        "2s5s10s": 100 * (2 * col(5.0) - col(2.0) - col(10.0)),
    }, index=curves.index)
    return out.dropna(how="all")


@dataclass
class PCAResult:
    tenors: list[float]
    loadings: pd.DataFrame           # tenors x PCs
    explained: np.ndarray            # variance ratio per PC
    eigenvalues: np.ndarray          # bp^2 / day
    scores: pd.DataFrame             # daily factor changes (bp)
    cumulative: pd.DataFrame         # cumulated factor levels (bp)
    n_obs: int


def yield_pca(curves: pd.DataFrame, tenors: Sequence[float] | None = None, n_components: int = 3) -> PCAResult:
    """PCA of daily yield changes (Litterman & Scheinkman 1991).

    ``dY`` (T x N, bp) on dates where every chosen tenor is quoted on both
    days; eigen-decomposition of the sample covariance
    ``S = dY_c' dY_c /(T-1) = V diag(lambda) V'``. Loadings ``V`` are signed so
    that PC1 (level) has a positive sum, PC2 (slope) rises with maturity
    (long minus short > 0) and PC3 (curvature) has a positive belly relative
    to the wings. Scores ``dY_c V`` are daily factor moves; their cumulative
    sum is the factor's level history.
    """
    tn = list(tenors) if tenors is not None else [c for c in curves.columns if curves[c].notna().mean() > 0.8]
    # difference consecutive curve observations FIRST, then drop incomplete
    # rows: a change is kept only if every tenor is quoted on both days (a
    # multi-day/-year hole in one tenor must not become one "daily" change)
    dy = 100.0 * curves[tn].sort_index().diff().dropna(how="any")
    if len(dy) < 60 or len(tn) < 3:
        raise ValueError("PCA needs at least 60 daily changes on at least 3 tenors")
    x = dy.to_numpy()
    xc = x - x.mean(axis=0)
    cov = xc.T @ xc / (len(xc) - 1)
    w, v = np.linalg.eigh(cov)
    idx = np.argsort(w)[::-1]
    w, v = w[idx], v[:, idx]
    k = min(n_components, len(tn))
    v = v[:, :k].copy()
    if v[:, 0].sum() < 0:
        v[:, 0] *= -1
    if k > 1 and v[-1, 1] - v[0, 1] < 0:
        v[:, 1] *= -1
    if k > 2:
        mid = len(tn) // 2
        wings = 0.5 * (v[0, 2] + v[-1, 2])
        if v[mid, 2] - wings < 0:
            v[:, 2] *= -1
    names = ["level", "slope", "curvature"][:k] + [f"pc{i + 1}" for i in range(3, k)]
    loadings = pd.DataFrame(v, index=tn, columns=names)
    scores = pd.DataFrame(xc @ v, index=dy.index, columns=names)
    return PCAResult(tn, loadings, w / w.sum(), w, scores, scores.cumsum(), len(dy))


def monthly_sample(df: pd.DataFrame) -> pd.DataFrame:
    """Last observation in each calendar month (index = that observation's date)."""
    g = df.dropna(how="all")
    last = g.groupby(g.index.to_period("M")).tail(1)
    return last
