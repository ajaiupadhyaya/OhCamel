"""Volatility surface for one underlying: SVI per expiry + term-structure metrics.

Combines :mod:`.chain` (cleaned OTM quotes with our IVs, implied ``F, D``),
:mod:`.svi` (quasi-explicit raw-SVI fit per slice, butterfly / calendar checks,
total-variance interpolation in ``T``) and :mod:`.metrics` (ATM, RR/BF, skew,
Cboe model-free variance, implied moves, 30-day constant maturity).

SVI weights: each quote's squared total-variance error is weighted by the
inverse of its bid-ask spread measured in total variance,
``1 / (T (iv_ask^2 - iv_bid^2))`` (floored at 10% of the slice median so that a
few ultra-tight quotes cannot dominate) -- i.e. quotes are trusted in proportion
to how precisely the market pins them down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import metrics
from .chain import ChainAnalytics
from .svi import (
    SVIFit,
    SVIParams,
    calendar_check,
    fit_svi,
    implied_vol_surface,
    total_variance_surface,
)


@dataclass
class VolSurface:
    fits: dict[str, SVIFit]
    term: pd.DataFrame                      # per slice metrics (index slice id)
    calendar: list[dict[str, Any]]
    grid_k: np.ndarray
    grid_T: np.ndarray
    grid_iv: np.ndarray                     # (len(grid_T), len(grid_k))
    vix_style: dict[str, Any] | None
    atm_30d: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def slices(self) -> list[tuple[float, SVIParams]]:
        return [(f.T, f.params) for f in self.fits.values()]


def _strike_table(q: pd.DataFrame, fields: tuple[str, ...]) -> tuple[np.ndarray, dict[tuple[str, str], np.ndarray]]:
    """Strike-aligned ``{(field, 'C'|'P'): values}`` (first finite value per strike and type;
    strikes where every entry is NaN are dropped) -- a fast ``pivot_table(aggfunc='first')``."""
    K = q["strike"].to_numpy(float)
    typ = q["type"].to_numpy()
    strikes = np.unique(K[np.isfinite(K)])
    out: dict[tuple[str, str], np.ndarray] = {}
    for f in fields:
        v = q[f].to_numpy(float)
        for t in ("C", "P"):
            arr = np.full(strikes.size, np.nan)
            sel = (typ == t) & np.isfinite(v) & np.isfinite(K)
            idx = np.searchsorted(strikes, K[sel])
            arr[idx[::-1]] = v[sel][::-1]            # reversed so the first row per strike wins
            out[(f, t)] = arr
    keep = np.zeros(strikes.size, dtype=bool)
    for arr in out.values():
        keep |= np.isfinite(arr)
    return strikes[keep], {key: arr[keep] for key, arr in out.items()}


def svi_weights(T: float, iv_bid: np.ndarray, iv_ask: np.ndarray) -> np.ndarray:
    dw = T * (np.asarray(iv_ask, float) ** 2 - np.asarray(iv_bid, float) ** 2)
    ok = np.isfinite(dw) & (dw > 0)
    if not ok.any():
        return np.ones_like(dw)
    floor = 0.1 * float(np.median(dw[ok]))
    return np.where(ok, 1.0 / np.maximum(dw, floor), 1.0 / float(np.median(dw[ok])))


def build_surface(
    ca: ChainAnalytics, min_dte: float = 1.0, max_slices: int = 40, n_k: int = 41, n_T: int = 30,
    rr_deltas: tuple[float, ...] = (0.25, 0.10),
) -> VolSurface:
    """Fit every expiry with ``dte >= min_dte`` (up to ``max_slices``, nearest first)."""
    notes: list[str] = []
    errors: dict[str, str] = {}
    fits: dict[str, SVIFit] = {}
    rows: list[dict[str, Any]] = []
    sl = ca.slices[ca.slices["dte"] >= min_dte]
    if len(sl) > max_slices:
        notes.append(f"surface limited to the nearest {max_slices} of {len(sl)} expiries")
        sl = sl.iloc[:max_slices]
    skipped_short = int((ca.slices["dte"] < min_dte).sum())
    if skipped_short:
        notes.append(f"{skipped_short} expiries under {min_dte:g} day(s) excluded from the surface")

    for sid, s in sl.iterrows():
        q = ca.slice_quotes(str(sid))
        T, F, D = float(s["T"]), float(s["forward"]), float(s["discount"])
        row: dict[str, Any] = {"slice": sid, "expiry": s["expiry"], "T": T, "dte": s["dte"], "forward": F,
                               "discount": D, "rate": s["rate"], "div_yield": s["div_yield"],
                               "settlement": s["settlement"], "n_smile": int(s["n_smile"])}
        sm = q[q["use_smile"]]
        row["atm_iv_market"] = metrics.atm_vol_market(sm["k"].to_numpy(), sm["iv"].to_numpy())
        try:
            fit = fit_svi(sm["k"].to_numpy(), sm["iv"].to_numpy(), T,
                          weights=svi_weights(T, sm["iv_bid"].to_numpy(), sm["iv_ask"].to_numpy()))
            fits[str(sid)] = fit
            p = fit.params
            row.update({"atm_iv": metrics.atm_vol(p, T), "skew_slope": metrics.skew_slope(p, T),
                        "svi_rmse_vol_pts": fit.rmse_vol_pts, "butterfly_ok": fit.butterfly["arbitrage_free"],
                        "g_min": fit.butterfly["g_min"]})
            for dl in rr_deltas:
                rb = metrics.risk_reversal_butterfly(p, T, F, float(s["div_yield"]), dl)
                tag = f"{round(dl * 100):d}d"
                row[f"rr_{tag}"] = rb["risk_reversal"]
                row[f"bf_{tag}"] = rb["butterfly"]
                row[f"iv_call_{tag}"] = rb["iv_call"]
                row[f"iv_put_{tag}"] = rb["iv_put"]
        except (ValueError, np.linalg.LinAlgError) as e:
            errors[str(sid)] = f"SVI: {e}"
        # model-free variance on the raw (uncleaned) strip, per Cboe rules
        try:
            strikes, tab = _strike_table(q, ("bid", "ask"))
            cols = [tab[(f, t)] for f, t in (("bid", "C"), ("ask", "C"), ("bid", "P"), ("ask", "P"))]
            mf = metrics.model_free_variance(strikes, *cols, F, D, T)
            row.update({"mf_var": mf["sigma2"], "mf_vol": mf["vol"], "mf_strikes": mf["n_strikes"], "mf_K0": mf["K0"]})
        except (ValueError, KeyError) as e:
            errors.setdefault(str(sid), f"model-free variance: {e}")
        mk, mids = _strike_table(q[q["valid"]], ("mid",))
        if np.isfinite(mids[("mid", "C")]).any() and np.isfinite(mids[("mid", "P")]).any():
            im = metrics.implied_move(mk, mids[("mid", "C")], mids[("mid", "P")], F, ca.spot)
            row.update({"implied_move": im["move"], "straddle": im["straddle"], "straddle_strike": im["strike"]})
        rows.append(row)

    term = pd.DataFrame(rows).set_index("slice") if rows else pd.DataFrame()
    if not fits:
        raise ValueError("no expiry had enough clean OTM quotes to fit a smile")
    ss = [(f.T, f.params) for f in fits.values()]
    kmins = [f.k_min for f in fits.values()]
    kmaxs = [f.k_max for f in fits.values()]
    k_lo, k_hi = float(np.median(kmins)), float(np.median(kmaxs))
    if k_hi - k_lo < 1e-3:
        k_lo, k_hi = min(kmins), max(kmaxs)
    grid_k = np.linspace(k_lo, k_hi, n_k)
    Ts = sorted(t for t, _ in ss)
    grid_T = np.unique(np.concatenate([np.linspace(Ts[0], Ts[-1], n_T), Ts])) if len(Ts) > 1 else np.array(Ts)
    grid_iv = implied_vol_surface(ss, grid_k, grid_T)
    cal_grid = np.linspace(min(kmins), max(kmaxs), 201)
    cal = calendar_check(ss, cal_grid)
    n_cal = sum(c["violation"] for c in cal)
    n_bf = sum(not f.butterfly["arbitrage_free"] for f in fits.values())
    if n_bf:
        notes.append(f"{n_bf} fitted smile(s) fail the Durrleman butterfly condition g(k) >= 0 somewhere "
                     "on the checked range (negative implied density)")
    if n_cal:
        notes.append(f"{n_cal} adjacent expiry pair(s) show decreasing total variance (calendar arbitrage "
                     "in the fitted smiles, or quote noise)")

    vix = None
    terms = [(float(r["T"]), float(r["mf_var"])) for _, r in term.iterrows()
             if "mf_var" in r and np.isfinite(r.get("mf_var", np.nan))]
    try:
        vix = metrics.constant_maturity_variance(terms)
    except ValueError as e:
        notes.append(f"30-day model-free index unavailable: {e}")
    atm30 = None
    target = 30.0 / 365.0
    if Ts[0] <= target <= Ts[-1]:
        w = total_variance_surface(ss, np.array([0.0]), np.array([target]))[0, 0]
        atm30 = {"iv": float(np.sqrt(w / target)), "extrapolated": False, "method": "SVI total variance linear in T"}
    else:
        try:
            cm = metrics.constant_maturity_variance([(t, float(p.w(0.0)) / t) for t, p in ss], target, 0.0)
            atm30 = {"iv": float(np.sqrt(cm["sigma2"])), "extrapolated": True,
                     "method": "SVI ATM total variance, extrapolated linearly in T"}
        except ValueError:
            pass
    return VolSurface(fits=fits, term=term, calendar=cal, grid_k=grid_k, grid_T=grid_T, grid_iv=grid_iv,
                      vix_style=vix, atm_30d=atm30, notes=notes, errors=errors)
