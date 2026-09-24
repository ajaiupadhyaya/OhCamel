"""Multi-leg option positions priced from real quotes.

Each leg is a listed contract (resolved by OCC symbol or ``(expiry, strike,
type)``) or a stock leg. The position is marked:

* **cost at mid** -- ``sum qty x mid x multiplier`` (+ ``qty x spot`` for stock);
* **cost at natural** -- buys at the ask, sells at the bid (the price a
  marketable order would pay); the difference is the round-trip friction;
* **value at a future date ``t``** -- each option leg repriced with BSM at its
  remaining time ``T_leg - t``, its own implied vol held fixed (*sticky-strike*
  assumption, Derman 1999 "Regimes of volatility") and its slice's implied
  ``r, q``; legs past expiry pay intrinsic value;
* **P&L at the first expiry** -- payoff of expiring legs plus BSM value of later
  legs, minus cost at mid; breakevens are its sign changes, max profit / loss are
  its extrema, and an unbounded side is detected from the asymptotic slope
  ``dP&L/dS`` as ``S -> inf`` (calls and stock contribute; puts do not);
* **probability of profit** -- ``Q(P&L(S_T) > 0)`` under the Breeden-Litzenberger
  risk-neutral density of the first expiry (from its SVI smile). This is a
  *risk-neutral* probability, not a real-world forecast: it embeds the volatility
  risk premium (see e.g. Bakshi, Kapadia & Madan 2003);
* **net greeks** -- sums of ``qty x multiplier x`` BSM greeks (stock delta = qty).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import bsm
from .chain import CONTRACT_MULTIPLIER, ChainAnalytics
from .density import RNDensity, breeden_litzenberger
from .svi import SVIParams

GREEK_KEYS = ("delta", "gamma", "vega", "theta_day", "rho", "vanna", "volga", "charm_day")


@dataclass
class Leg:
    kind: str                 # 'option' | 'stock'
    qty: float
    contract: str | None = None
    slice: str | None = None
    strike: float = float("nan")
    type: str | None = None
    T: float = float("nan")
    r: float = 0.0
    q: float = 0.0
    F: float = float("nan")
    bid: float = float("nan")
    ask: float = float("nan")
    mid: float = float("nan")
    iv: float = float("nan")
    iv_source: str = ""
    expiry: pd.Timestamp | None = None


def resolve_legs(ca: ChainAnalytics, legs: list[dict[str, Any]], fits: dict[str, SVIParams] | None = None) -> list[Leg]:
    """Match leg specs to chain rows. Option spec keys: ``qty`` and either ``contract``
    or ``expiry`` + ``strike`` + ``type``; stock spec: ``kind='stock'``, ``qty``."""
    fits = fits or {}
    out: list[Leg] = []
    qt = ca.quotes
    for i, spec in enumerate(legs):
        qty = float(spec.get("qty", 0.0))
        if qty == 0 or not np.isfinite(qty):
            raise ValueError(f"leg {i + 1}: qty must be a non-zero number")
        if spec.get("kind", "option") == "stock":
            out.append(Leg(kind="stock", qty=qty, mid=ca.spot, bid=ca.spot, ask=ca.spot))
            continue
        if spec.get("contract"):
            row = qt[qt["contract"] == str(spec["contract"]).strip().upper()]
            if row.empty:
                raise ValueError(f"leg {i + 1}: contract {spec['contract']} not in the cleaned chain")
        else:
            if spec.get("expiry") is None or spec.get("strike") is None or spec.get("type") is None:
                raise ValueError(f"leg {i + 1}: give a contract symbol or expiry + strike + type")
            sid = ca.resolve(str(spec["expiry"]))
            typ = str(spec["type"]).upper()[:1]
            row = qt[(qt["slice"] == sid) & (qt["type"] == typ) & np.isclose(qt["strike"], float(spec["strike"]))]
            if row.empty:
                raise ValueError(f"leg {i + 1}: no {typ} {spec['strike']} listed for {sid}")
        r = row.iloc[0]
        leg = Leg(kind="option", qty=qty, contract=str(r.get("contract", "")), slice=str(r["slice"]),
                  strike=float(r["strike"]), type=str(r["type"]), T=float(r["T"]), r=float(r["r"]),
                  q=float(r["q"]), F=float(r["F"]), bid=float(r["bid"]), ask=float(r["ask"]),
                  mid=float(r["mid"]), expiry=pd.Timestamp(r["expiry"]))
        if np.isfinite(r["iv"]):
            leg.iv, leg.iv_source = float(r["iv"]), "own IV from mid"
        elif leg.slice in fits:
            leg.iv = float(fits[leg.slice].implied_vol(np.log(leg.strike / leg.F), leg.T))
            leg.iv_source = "SVI smile (quote unusable)"
        elif np.isfinite(r.get("vendor_iv", np.nan)):
            leg.iv, leg.iv_source = float(r["vendor_iv"]), "vendor IV (quote unusable)"
        else:
            raise ValueError(f"leg {i + 1}: no usable implied volatility for {leg.contract}")
        if not np.isfinite(leg.mid) or leg.mid <= 0:
            leg.mid = float(bsm.bsm_price(ca.spot, leg.strike, leg.T, leg.iv, leg.r, leg.q, leg.type))
            leg.iv_source += "; mid replaced by model value (no two-sided quote)"
        out.append(leg)
    return out


def position_value(legs: list[Leg], S: np.ndarray, t: float, multiplier: float = CONTRACT_MULTIPLIER) -> np.ndarray:
    """Mark-to-model value of the position at spot(s) ``S`` and time ``t`` (years from now)."""
    S = np.asarray(S, float)
    v = np.zeros_like(S)
    for lg in legs:
        if lg.kind == "stock":
            v += lg.qty * S
            continue
        tau = lg.T - t
        if tau <= 1e-10:
            val = np.maximum(bsm.option_sign(lg.type) * (S - lg.strike), 0.0)
        else:
            val = bsm.bsm_price(S, lg.strike, tau, lg.iv, lg.r, lg.q, lg.type)
        v += lg.qty * multiplier * val
    return v


def _cost(legs: list[Leg], multiplier: float) -> tuple[float, float]:
    mid = nat = 0.0
    for lg in legs:
        m = 1.0 if lg.kind == "stock" else multiplier
        mid += lg.qty * m * lg.mid
        px = lg.ask if lg.qty > 0 else lg.bid
        if not np.isfinite(px) or px <= 0:
            px = lg.mid
        nat += lg.qty * m * px
    return mid, nat


def _asymptotic_slope(legs: list[Leg], T_h: float, multiplier: float) -> float:
    s = 0.0
    for lg in legs:
        if lg.kind == "stock":
            s += lg.qty
        elif lg.type == "C":
            tau = lg.T - T_h
            s += lg.qty * multiplier * (np.exp(-lg.q * tau) if tau > 1e-10 else 1.0)
    return s


def _roots(x: np.ndarray, y: np.ndarray) -> list[float]:
    out = []
    for i in np.flatnonzero(np.sign(y[:-1]) * np.sign(y[1:]) < 0):
        out.append(float(x[i] - y[i] * (x[i + 1] - x[i]) / (y[i + 1] - y[i])))
    out.extend(float(x[i]) for i in np.flatnonzero(y == 0))
    return sorted(set(round(v, 6) for v in out))


def analyze_position(
    ca: ChainAnalytics, legs: list[Leg], fits: dict[str, SVIParams] | None = None,
    multiplier: float = CONTRACT_MULTIPLIER, n_grid: int = 301, n_dates: int = 4,
) -> dict[str, Any]:
    """Full analytics for a resolved position (see module docstring)."""
    fits = fits or {}
    notes: list[str] = []
    opts = [lg for lg in legs if lg.kind == "option"]
    spot = ca.spot
    cost_mid, cost_nat = _cost(legs, multiplier)

    # greeks today
    greeks = dict.fromkeys(GREEK_KEYS, 0.0)
    leg_rows = []
    for lg in legs:
        if lg.kind == "stock":
            greeks["delta"] += lg.qty
            leg_rows.append({"kind": "stock", "qty": lg.qty, "price": spot, "delta": lg.qty})
            continue
        g = bsm.bsm_greeks(spot, lg.strike, lg.T, lg.iv, lg.r, lg.q, lg.type)
        row = {"kind": "option", "qty": lg.qty, "contract": lg.contract, "expiry": lg.expiry, "slice": lg.slice,
               "strike": lg.strike, "type": lg.type, "bid": lg.bid, "ask": lg.ask, "mid": lg.mid, "iv": lg.iv,
               "iv_source": lg.iv_source, "T": lg.T, "dte": lg.T * 365.0}
        for key in GREEK_KEYS:
            val = float(g[key]) * lg.qty * multiplier
            greeks[key] += val
            row[key] = val
        leg_rows.append(row)
    greeks["dollar_delta"] = greeks["delta"] * spot
    greeks["dollar_gamma_1pct"] = 0.5 * greeks["gamma"] * (0.01 * spot) ** 2
    greeks["vega_per_vol_pt"] = greeks["vega"] / 100.0
    greeks["rho_per_pct"] = greeks["rho"] / 100.0

    if not opts:
        raise ValueError("a strategy needs at least one option leg")
    T_h = min(lg.T for lg in opts)
    first = min(opts, key=lambda lg: lg.T)
    if len({lg.slice for lg in opts}) > 1:
        notes.append("multi-expiry position: 'at expiry' means the first expiry; later legs are valued "
                     "with BSM at their remaining time and today's IV (sticky strike)")

    # density of the first expiry
    dens: RNDensity | None = None
    if first.slice in fits:
        p = fits[first.slice]
        dens = breeden_litzenberger(first.F, np.exp(-first.r * first.T), first.T, p.w)
        dens_src = f"Breeden-Litzenberger density from the SVI smile of {first.slice}"
    else:
        dens = breeden_litzenberger(first.F, np.exp(-first.r * first.T), first.T,
                                    lambda k: np.full_like(np.asarray(k, float), first.iv**2 * first.T))
        dens_src = f"lognormal density at the leg IV ({first.iv:.2%}): no SVI fit for {first.slice}"
        notes.append("probability of profit uses a flat-vol lognormal density (no fitted smile)")

    lo = min(float(dens.quantiles([0.001])[0]), *(lg.strike for lg in opts)) * 0.9
    hi = max(float(dens.quantiles([0.999])[0]), *(lg.strike for lg in opts)) * 1.1
    S = np.linspace(max(lo, 1e-6 * spot), hi, n_grid)
    S = np.unique(np.concatenate([S, [lg.strike for lg in opts], [spot]]))
    fracs = np.linspace(0.0, 1.0, n_dates + 1)
    curves = {}
    for i, f in enumerate(fracs):
        t = float(T_h * f)
        label = "today" if i == 0 else ("expiry" if i == len(fracs) - 1 else f"+{t * 365:.1f}d")
        curves[label] = {"t_years": t, "days": t * 365.0, "pnl": position_value(legs, S, t, multiplier) - cost_mid}
    pnl_exp = curves["expiry"]["pnl"]
    payoff = pnl_exp + cost_mid

    slope = _asymptotic_slope(legs, T_h, multiplier)
    pnl_zero = float(position_value(legs, np.array([1e-9 * spot]), T_h, multiplier)[0] - cost_mid)
    cand = np.append(pnl_exp, pnl_zero)
    tol = 1e-9 * max(1.0, abs(cost_mid))
    max_profit = float("inf") if slope > tol else float(cand.max())
    max_loss = float("-inf") if slope < -tol else float(cand.min())
    gross_calls = sum(abs(lg.qty) * multiplier for lg in opts if lg.type == "C") + sum(
        abs(lg.qty) for lg in legs if lg.kind == "stock")
    if abs(slope) > tol and abs(slope) < 0.05 * gross_calls:
        notes.append("the unbounded side comes only from the dividend-yield carry of later-expiry calls "
                     f"(asymptotic slope {slope:+.3g} per $1): it matters only for very large moves")
    breakevens = _roots(S, pnl_exp)
    if pnl_zero * pnl_exp[0] < 0:
        notes.append("an additional breakeven lies below the plotted spot range")

    Kd = dens.strikes
    pnl_d = position_value(legs, Kd, T_h, multiplier) - cost_mid
    qd = np.clip(dens.density, 0.0, None)
    z = float(np.trapezoid(qd, Kd))
    pop = float(np.trapezoid(np.where(pnl_d > 0, qd, 0.0), Kd) / z) if z > 0 else float("nan")
    exp_pnl = float(np.trapezoid(pnl_d * qd, Kd) / z) if z > 0 else float("nan")

    return {
        "legs": leg_rows,
        "cost": {"mid": cost_mid, "natural": cost_nat, "friction": cost_nat - cost_mid,
                 "direction": "debit" if cost_mid > 0 else "credit"},
        "greeks": greeks,
        "horizon": {"T_years": T_h, "days": T_h * 365.0, "expiry": first.expiry, "slice": first.slice},
        "grid": {"spot": S, "payoff_at_expiry": payoff, "curves": curves},
        "breakevens": breakevens,
        "max_profit": max_profit, "max_loss": max_loss,
        # explicit flags: a null extremum on the wire could mean unlimited OR not computable
        "max_profit_unbounded": bool(math.isinf(max_profit) and max_profit > 0),
        "max_loss_unbounded": bool(math.isinf(max_loss) and max_loss < 0),
        "asymptotic_slope": slope,
        "prob_profit": pop, "expected_pnl_q": exp_pnl,
        "density_source": dens_src,
        "notes": notes,
    }
