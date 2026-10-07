"""P7: one row per underlying per day from a fitted surface's term table (compute plan M8).

ATM IV interpolates in total variance w = sigma^2 T (calendar-arbitrage-free between
slices); RR/BF interpolate linearly in T; neither extrapolates (None outside the
quoted terms). The 30-day model-free variance is Cboe's constant-maturity
interpolation (options.metrics.constant_maturity_variance), refused when it would
extrapolate. VRP = 30-day implied variance - 252 x the HAR 22-day daily-variance forecast.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pandas as pd

from ..options.metrics import constant_maturity_variance

TARGETS = {"30d": 30.0 / 365.0, "90d": 90.0 / 365.0}


def _bracket(term: pd.DataFrame, col: str, T: float) -> tuple[pd.Series, pd.Series] | None:
    t = term[["T", col]].dropna().sort_values("T")
    lo, hi = t[t["T"] <= T], t[t["T"] >= T]
    if lo.empty or hi.empty:
        return None
    return lo.iloc[-1], hi.iloc[0]


def interp_total_variance(term: pd.DataFrame, col: str, T: float) -> float | None:
    b = _bracket(term, col, T)
    if b is None:
        return None
    (t1, v1), (t2, v2) = (b[0]["T"], b[0][col]), (b[1]["T"], b[1][col])
    if t2 == t1:
        return float(v1)
    w = v1 * v1 * t1 + (v2 * v2 * t2 - v1 * v1 * t1) * (T - t1) / (t2 - t1)
    return float(math.sqrt(w / T)) if w > 0 else None


def interp_linear(term: pd.DataFrame, col: str, T: float) -> float | None:
    b = _bracket(term, col, T)
    if b is None:
        return None
    (t1, v1), (t2, v2) = (b[0]["T"], b[0][col]), (b[1]["T"], b[1][col])
    return float(v1) if t2 == t1 else float(v1 + (v2 - v1) * (T - t1) / (t2 - t1))


def day_metrics(term: pd.DataFrame) -> dict[str, float | None]:
    a30 = interp_total_variance(term, "atm_iv", TARGETS["30d"])
    a90 = interp_total_variance(term, "atm_iv", TARGETS["90d"])
    mf = None
    if "mf_var" in term:
        try:
            cm = constant_maturity_variance(list(zip(term["T"], term["mf_var"], strict=True)), TARGETS["30d"])
            mf = None if cm.get("extrapolated") else float(cm["sigma2"])
        except ValueError:
            mf = None
    return {"atm_iv_30d": a30, "atm_iv_90d": a90, "term_slope": None if a30 is None or a90 is None else a90 - a30,
            "rr25_30d": interp_linear(term, "rr_25d", TARGETS["30d"]) if "rr_25d" in term else None,
            "bf25_30d": interp_linear(term, "bf_25d", TARGETS["30d"]) if "bf_25d" in term else None,
            "mf_var_30d": mf, "n_slices": int(len(term))}


def vrp(mf_var_30d: float | None, har_22d_daily: float | None) -> float:
    if mf_var_30d is None or har_22d_daily is None:
        return float("nan")
    return float(mf_var_30d) - 252.0 * float(har_22d_daily)


def rate_curve(row: pd.Series) -> Callable[[float], float]:
    """FRED CMT yields (percent, bond-equivalent) -> continuous ``r = 2 ln(1 + y/200)``, linear in tenor
    (the options router's convention), from one dated row of ``MarketData.treasury_curve``."""
    last = row.dropna()
    if len(last) < 2:
        raise ValueError("fewer than two tenors on the Treasury curve")
    tenors = np.asarray(last.index, dtype=float)
    rates = 2.0 * np.log1p(last.to_numpy(dtype=float) / 200.0)
    o = np.argsort(tenors)
    return lambda T: float(np.interp(T, tenors[o], rates[o]))
