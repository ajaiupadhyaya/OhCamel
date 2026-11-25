"""Heston (1993) stochastic volatility, priced by the COS method and calibrated by bounded least squares.

Model, under the pricing measure, for the forward ``F_t`` of one expiry (``v`` the instantaneous variance)::

    dF / F = sqrt(v) dW_1,     dv = kappa (theta - v) dt + sigma sqrt(v) dW_2,     d<W_1, W_2> = rho dt

**Characteristic function** of ``X = ln(S_T / F)``: Albrecher, Mayer, Schoutens & Tistaert (2007), "The little
Heston trap" -- the form with ``g = (beta - d)/(beta + d)`` and ``e^{-dT}``, whose complex logarithm never leaves
its principal branch (``kernels.reference.heston_cf``).

**Pricing**: the Fourier-cosine expansion of Fang & Oosterlee (2008), "A novel pricing method for European
options based on Fourier-cosine series expansions", SIAM J. Sci. Comput. 31(2). The density of ``X`` is expanded
in ``n_terms`` cosines (default 160) on the truncation range ``[c1 - L sqrt(c2), c1 + L sqrt(c2)]``, ``L = 12``,
with ``c1``, ``c2`` the mean and variance of ``X`` (their eq. 49, the ``c4`` term left out: the c2-only form).
``c1`` and ``c2`` are derived in closed form from the CIR moments (:func:`cumulants`; tested against the CF's own
derivatives). The out-of-the-money option is the series (a put at ``K <= F``, a call above) and the other is
put-call parity, so parity holds to rounding and no wing price is a difference of two large numbers. A strike
within ``L/2`` standard deviations of the range's edge, or past it, is priced on its own range extended to ``L/2``
sd beyond it, with the terms scaled to the range (``kernels.reference.heston_contract_range``), so no quoted strike
prices at exactly zero. The nightly league prices at :data:`LEAGUE_N` = 1024 terms on ``L`` = 24: at a fitted vol
of vol near 3, 160 terms miss by up to 2e-2 in implied vol. Accuracy:
the published reference (S = K = 100, T = 1, r = 0, kappa 1.5768, theta 0.0398, sigma 0.5751, rho -0.5711,
v0 0.0175; call 5.785155450) is matched to 4.0e-5 -- the c2-only range truncates that heavy left tail; at
L = 20 and 512 terms the series is within 3e-8 of the reference. The NumPy reference
(``kernels.reference.heston_price``) and the Rust kernel (``native/kernels/src/heston.rs``) agree to 1e-9.

**Calibration** (:func:`calibrate`): ``scipy.optimize.least_squares`` (trust-region reflective) on PRICES over
``(kappa, theta, sigma, rho, v0)`` in the box :data:`LO`..:data:`HI`, from :data:`STARTS` (two deterministic
starts, ``theta`` and ``v0`` set from the quotes' long and short ATM variance), at most :data:`MAX_NFEV` evaluations
each; the lower cost wins. ``weights="vega"``: each price error is divided by the quote's Black-76 vega at its
own implied vol, so the residual is, to first order, the implied-vol error -- the unit the league scores in.
The Feller condition ``2 kappa theta >= sigma^2`` (the variance never touches zero) is REPORTED
(``HestonFit.feller``, ``feller_ratio``), not enforced: equity surfaces routinely violate it and the model prices
either way.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from .. import kernels
from ..kernels import reference as _ref
from .bsm import implied_vol_black

L = _ref.HESTON_L          # truncation half-width, standard deviations of ln(S_T / F)
N_TERMS = 160              # cosine terms (Fang & Oosterlee 2008's N)
LO = np.array([1e-3, 1e-4, 1e-3, -0.999, 1e-4])      # kappa, theta, sigma, rho, v0
HI = np.array([20.0, 1.0, 5.0, 0.999, 1.0])
STARTS = ((2.0, 0.5, -0.6), (0.8, 1.0, -0.8))      # (kappa, sigma, rho); theta, v0 from the quotes
MAX_NFEV = 120             # per start (scipy counts residual calls, not the Jacobian's 5 extra per step)
MIN_QUOTES = 6
#: An out-of-the-money model price below this fraction of ``df F`` has no implied vol here (NaN): below it the
#: series' rounding (~1e-16 of the strike per term) is a visible share of the price.
PRICE_FLOOR = 1e-12
#: What the nightly league prices Heston with (calibration, scoring and violations): a fitted surface's vol of vol
#: runs to ~3 (2026-11-13 SPY), where the log-return density is sharp and heavy-tailed: 160 terms leave up to 2e-2
#: in implied vol and the L = 12 range up to 6e-2 at 8 sd out; 1024 terms on L = 24 converge (series within 7e-6,
#: range within 2e-5 at T <= 0.5 against L = 48; measured, task 4.6 fix report).
LEAGUE_N, LEAGUE_L = 1024, 24.0


@dataclass(frozen=True)
class HestonParams:
    kappa: float
    theta: float
    sigma: float
    rho: float
    v0: float

    def __post_init__(self) -> None:
        vals = self.astuple()
        if not all(math.isfinite(v) for v in vals):
            raise ValueError(f"Heston parameters must be finite: {vals}")
        if not (self.kappa > 0 and self.theta > 0 and self.sigma > 0 and self.v0 > 0):
            raise ValueError(f"Heston needs kappa, theta, sigma, v0 > 0: {vals}")
        if not -1.0 < self.rho < 1.0:
            raise ValueError(f"Heston needs |rho| < 1: rho = {self.rho}")

    def astuple(self) -> tuple[float, float, float, float, float]:
        return (float(self.kappa), float(self.theta), float(self.sigma), float(self.rho), float(self.v0))

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @property
    def feller_ratio(self) -> float:
        """``2 kappa theta / sigma^2``: at least 1 when the Feller condition holds."""
        return 2.0 * self.kappa * self.theta / (self.sigma * self.sigma)


def cumulants(T: float, p: HestonParams) -> tuple[float, float]:
    """``(c1, c2)``: mean and variance of ``ln(S_T / F)`` (``kernels.reference.heston_cumulants``)."""
    return _ref.heston_cumulants(float(T), *p.astuple())


def truncation_range(T: float, p: HestonParams, L: float = L) -> tuple[float, float]:
    """The COS range of ``ln(S_T / F)``: ``c1 -/+ L sqrt(c2)``."""
    return _ref.heston_range(float(T), *p.astuple(), L)


def price(F: Any, K: Any, T: Any, df: Any, p: HestonParams, *, is_call: Any, n_terms: int = N_TERMS,
          L: float = L) -> np.ndarray:
    """European prices (discounted) by COS, broadcast over ``F, K, T, df, is_call``; dispatched to the Rust kernel
    when the wheel is loaded (``kernels.heston_price``). A strike within ``L/2`` standard deviations of the range's
    edge, or beyond it, is priced on its own extended range (``kernels.reference.heston_contract_range``)."""
    return kernels.heston_price(F, K, T, df, is_call, *p.astuple(), n_terms=n_terms, L=L)


def implied_vol(p: HestonParams, k: Any, T: float, n_terms: int = N_TERMS, L: float = L,
                floor: float = PRICE_FLOOR) -> np.ndarray:
    """Black-76 implied vol of the Heston price at log-forward-moneyness ``k`` and maturity ``T`` (the smile in
    forward moneyness depends on ``k`` and ``T`` only), inverted from the out-of-the-money option; NaN where that
    price is below ``floor`` (default :data:`PRICE_FLOOR`)."""
    k = np.asarray(k, dtype=float)
    K = np.exp(k)
    call = k >= 0
    px = price(1.0, K, float(T), 1.0, p, is_call=call, n_terms=n_terms, L=L)
    px = np.where(px >= floor, px, np.nan)
    return implied_vol_black(px, 1.0, K, float(T), 1.0, np.where(call, "C", "P"))


def density(p: HestonParams, x: Any, T: float, n_terms: int = N_TERMS, L: float = L) -> np.ndarray:
    """The COS density of ``x = ln(S_T / F)``: ``2/(b-a) sum' A_k cos(u_k (x - a))`` on ``[a, b]``, zero outside.
    The put's second strike derivative is ``df f(ln(K/F)) / K``, so where this goes negative the COS-priced smile
    has butterfly arbitrage."""
    x = np.asarray(x, dtype=float)
    a, b, u, A = _ref.heston_series(float(T), *p.astuple(), n_terms, L)
    f = (2.0 / (b - a)) * (np.cos(np.multiply.outer(x - a, u)) @ A)
    return np.where((x >= a) & (x <= b), f, 0.0)


@dataclass
class HestonFit:
    params: HestonParams
    n: int
    rmse_vol_pts: float          # unweighted, implied-vol points, over the quotes whose model IV inverts
    max_abs_err_vol_pts: float
    n_iv_failed: int             # quotes whose model price has no implied vol (below PRICE_FLOOR)
    cost: float
    nfev: int
    status: int
    start: int                   # index into STARTS of the winning start
    feller: bool                 # 2 kappa theta >= sigma^2 -- reported, not enforced
    feller_ratio: float
    weights: str
    n_terms: int = N_TERMS
    L: float = L

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["params"] = self.params.to_dict()
        return d


QUOTE_COLS = ("T", "F", "df", "K", "price", "is_call")


def _vega(F: np.ndarray, K: np.ndarray, T: np.ndarray, df: np.ndarray, iv: np.ndarray) -> np.ndarray:
    sd = iv * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = np.log(F / K) / sd + 0.5 * sd
    return df * F * np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) * np.sqrt(T)


def _atm_var(q: pd.DataFrame, T: float) -> float:
    s = q[q["T"] == T]
    k = np.log(s["K"].to_numpy() / s["F"].to_numpy())
    o = np.argsort(k)
    return float(np.interp(0.0, k[o], s["iv"].to_numpy()[o] ** 2))


def calibrate(quotes: pd.DataFrame, *, weights: str | None = "vega", starts: tuple = STARTS,
              max_nfev: int = MAX_NFEV, n_terms: int = N_TERMS, L: float = L) -> HestonFit:
    """Heston on ``quotes`` -- one row per option, columns ``T, F, df, K, price, is_call`` (and optionally ``iv``,
    the quote's Black-76 implied vol; inverted from the price when absent) -- by least squares on prices
    (module docstring). ``weights``: ``"vega"`` (price error / Black vega, ~ the implied-vol error) or ``None``
    (price error / (df F)). Raises ValueError with its reason on unusable input."""
    missing = [c for c in QUOTE_COLS if c not in quotes.columns]
    if missing:
        raise ValueError(f"Heston quotes need columns {QUOTE_COLS}; missing {missing}")
    q = quotes.loc[:, [*QUOTE_COLS, *(["iv"] if "iv" in quotes.columns else [])]].copy()
    q["is_call"] = q["is_call"].astype(bool)
    if "iv" not in q.columns:
        q["iv"] = implied_vol_black(q["price"].to_numpy(float), q["F"].to_numpy(float), q["K"].to_numpy(float),
                                    q["T"].to_numpy(float), q["df"].to_numpy(float),
                                    np.where(q["is_call"], "C", "P"))
    num = q[["T", "F", "df", "K", "price", "iv"]].to_numpy(float)
    q = q[np.isfinite(num).all(axis=1) & (num[:, :5] > 0).all(axis=1) & (num[:, 5] > 0)]
    if len(q) < MIN_QUOTES:
        raise ValueError(f"Heston calibration needs at least {MIN_QUOTES} quotes with an implied vol; got {len(q)}")
    if weights not in ("vega", None):
        raise ValueError(f"weights must be 'vega' or None; got {weights!r}")
    T, F, df, K = (q[c].to_numpy(float) for c in ("T", "F", "df", "K"))
    mkt, iv, call = q["price"].to_numpy(float), q["iv"].to_numpy(float), q["is_call"].to_numpy(bool)
    scale = _vega(F, K, T, df, iv) if weights == "vega" else df * F
    scale = np.maximum(scale, 1e-8 * df * F * np.sqrt(T))

    def resid(x: np.ndarray) -> np.ndarray:
        m = kernels.heston_price(F, K, T, df, call, *x, n_terms=n_terms, L=L)
        return (m - mkt) / scale

    Ts = np.unique(T)
    theta0 = float(np.clip(_atm_var(q, float(Ts[-1])), LO[1] * 2, HI[1] / 2))
    v00 = float(np.clip(_atm_var(q, float(Ts[0])), LO[4] * 2, HI[4] / 2))
    best, best_i = None, -1
    for i, (k0, s0, r0) in enumerate(starts):
        x0 = np.clip(np.array([k0, theta0, s0, r0, v00]), LO, HI)
        r = least_squares(resid, x0, bounds=(LO, HI), method="trf", x_scale="jac", max_nfev=max_nfev,
                          ftol=1e-12, xtol=1e-12, gtol=1e-12)
        if np.isfinite(r.cost) and (best is None or r.cost < best.cost):
            best, best_i = r, i
    if best is None:
        raise ValueError("Heston calibration: no start gave a finite objective")
    p = HestonParams(*(float(v) for v in best.x))
    model = kernels.heston_price(F, K, T, df, call, *p.astuple(), n_terms=n_terms, L=L)
    otm = model - np.where(call == (K > F), 0.0, np.where(call, 1.0, -1.0) * df * (F - K))
    model = np.where(otm >= PRICE_FLOOR * df * F, model, np.nan)
    miv = implied_vol_black(model, F, K, T, df, np.where(call, "C", "P"))
    ok = np.isfinite(miv)
    err = (miv[ok] - iv[ok]) * 100.0
    return HestonFit(params=p, n=int(len(q)), rmse_vol_pts=float(np.sqrt(np.mean(err * err))) if err.size else
                     float("nan"), max_abs_err_vol_pts=float(np.max(np.abs(err))) if err.size else float("nan"),
                     n_iv_failed=int((~ok).sum()), cost=float(best.cost), nfev=int(best.nfev),
                     status=int(best.status), start=best_i, feller=bool(p.feller_ratio >= 1.0),
                     feller_ratio=float(p.feller_ratio), weights=str(weights), n_terms=int(n_terms), L=float(L))
