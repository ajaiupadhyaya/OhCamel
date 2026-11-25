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

from . import heston, metrics, ssvi
from .bsm import black76_price
from .chain import ChainAnalytics
from .svi import (
    G_TOL,
    K_MAX,
    QUOTED_N,
    SVIFit,
    SVIParams,
    butterfly_check,
    calendar_check,
    calendar_check_quoted,
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


def smile_inputs(ca: ChainAnalytics, sid: str) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """``(T, k, iv, weights)`` of one expiry: the quotes a smile is fitted to (``use_smile``: OTM, with an
    identified IV -- an American quote at its intrinsic value or below the model forward has none) and their
    inverse-spread weights (:func:`svi_weights`). The per-slice SVI fit and the SSVI fit read the same set."""
    q = ca.slice_quotes(str(sid))
    sm = q[q["use_smile"]]
    T = float(ca.slices.loc[sid, "T"])
    return (T, sm["k"].to_numpy(float), sm["iv"].to_numpy(float),
            svi_weights(T, sm["iv_bid"].to_numpy(), sm["iv_ask"].to_numpy()))


def fit_ssvi_surface(ca: ChainAnalytics, vs: VolSurface) -> ssvi.SSVIFit:
    """SSVI (power law, :mod:`.ssvi`) on exactly the expiries the per-slice SVI fitted in ``vs``, to the same
    quotes and weights (:func:`smile_inputs`)."""
    return ssvi.fit([smile_inputs(ca, sid) for sid in vs.fits])


def ssvi_grid(p: ssvi.SSVIParams, grid_k: np.ndarray, grid_T: np.ndarray) -> np.ndarray:
    """SSVI implied vols on the (T, k) grid of a :class:`VolSurface` (rows = T)."""
    return np.vstack([ssvi.implied_vol(p, grid_k, float(t)) for t in np.atleast_1d(grid_T)])


#: The k grid the wide calendar count of :func:`count_violations` runs on: 1201 points over [-K_MAX, K_MAX], the range
#: every fitted smile is held butterfly-free on and the densities integrate over (wings beyond the quotes included).
ARB_K_GRID = np.linspace(-K_MAX, K_MAX, 1201)


def count_violations(slices: list[tuple[float, SVIParams, float, float]]) -> dict[str, int]:
    """Butterfly and calendar violations of SVI-form slices ``[(T, params, k_min, k_max), ...]`` (``k_min``,
    ``k_max``: the slice's quoted range), by the code the per-slice fits are checked with. One code for every model
    whose slices are SVI (per-slice SVI, SSVI), so their counts compare like with like.

    * ``butterfly_violations`` of ``n_slices``: slices failing :func:`.svi.butterfly_check` (the refined g scan on
      [-K_MAX, K_MAX], tolerance G_TOL).
    * ``calendar_violations_quoted`` of ``calendar_pairs_quoted``: adjacent pairs whose total variance decreases
      somewhere on their COMMON QUOTED range (:func:`.svi.calendar_check_quoted`) -- the page's calendar table.
    * ``calendar_violations`` of ``calendar_pairs``: adjacent pairs whose total variance decreases somewhere on
      :data:`ARB_K_GRID`, [-K_MAX, K_MAX], INCLUDING the extrapolated wings beyond either smile's quotes."""
    ss = sorted(slices, key=lambda s: s[0])
    cal = calendar_check([(t, p) for t, p, _, _ in ss], ARB_K_GRID) if len(ss) > 1 else []
    calq = calendar_check_quoted(ss)
    return {"butterfly_violations": int(sum(not butterfly_check(p)["arbitrage_free"] for _, p, _, _ in ss)),
            "n_slices": len(ss),
            "calendar_violations_quoted": int(sum(c["violation"] for c in calq)),
            "calendar_pairs_quoted": int(sum(c["overlap"] for c in calq)),
            "calendar_violations": int(sum(c["violation"] for c in cal)), "calendar_pairs": len(cal)}


def svi_violations(vs: VolSurface) -> dict[str, int]:
    """:func:`count_violations` of the per-slice SVI fits, each over its own quoted range."""
    return count_violations([(f.T, f.params, f.k_min, f.k_max) for f in vs.fits.values()])


def ssvi_violations(p: ssvi.SSVIParams, vs: VolSurface) -> dict[str, int]:
    """:func:`count_violations` of the SSVI slices at the fitted maturities, each over the quoted range of the
    per-slice fit it was fitted to (the same quotes, :func:`fit_ssvi_surface`)."""
    rng = {f.T: (f.k_min, f.k_max) for f in vs.fits.values()}
    return count_violations([(float(t), ssvi.slice_svi(p, float(t)), *rng[float(t)]) for t in p.T])


def heston_quotes(ca: ChainAnalytics, vs: VolSurface) -> pd.DataFrame:
    """The quotes Heston is calibrated to: exactly the identified-IV quotes per-slice SVI and SSVI fit
    (:func:`smile_inputs`, the expiries of ``vs``), each IV turned into a EUROPEAN Black-76 price of the
    out-of-the-money option on its expiry's forward and discount factor. The chains are American; pricing the
    identified IV as European keeps all three league models on one footing (SVI and SSVI fit those same IVs)."""
    rows = []
    for sid in vs.fits:
        T, k, iv, _ = smile_inputs(ca, sid)
        F, D = float(ca.slices.loc[sid, "forward"]), float(ca.slices.loc[sid, "discount"])
        K, call = F * np.exp(k), k >= 0
        rows.append(pd.DataFrame({"T": T, "F": F, "df": D, "K": K, "iv": iv, "is_call": call,
                                  "price": black76_price(F, K, T, iv, D, np.where(call, "C", "P"))}))
    return pd.concat(rows, ignore_index=True)


def fit_heston_surface(ca: ChainAnalytics, vs: VolSurface) -> heston.HestonFit:
    """Heston (:mod:`.heston`, COS at the league's ``LEAGUE_N`` terms on ``LEAGUE_L``) calibrated to
    :func:`heston_quotes`, vega-weighted."""
    return heston.calibrate(heston_quotes(ca, vs), weights="vega", n_terms=heston.LEAGUE_N, L=heston.LEAGUE_L)


#: The w comparisons of :func:`heston_violations` use only points whose out-of-the-money price (in units of
#: ``df F``) is at least this: below it an implied vol inverted from a COS price carries the series' rounding
#: (the calendar count wandered 13 / 5 / 6 / 3 with the series length on prices of 1e-12 .. 1e-6).
HESTON_W_FLOOR = 1e-8


def heston_violations(p: heston.HestonParams, slices: list[tuple[float, float, float]],
                      n_terms: int = heston.LEAGUE_N, L: float = heston.LEAGUE_L,
                      floor: float = HESTON_W_FLOOR) -> dict[str, int]:
    """Butterfly and calendar violations of a Heston surface at the maturities ``[(T, k_min, k_max), ...]``, by the
    definitions :func:`count_violations` applies to SVI-form slices, measured on the COS-priced surface (``n_terms``
    terms on the ``L`` range: converged, :data:`.heston.LEAGUE_N`) through its implied total variance
    ``w(k, T)``. Heston is free of static arbitrage in exact arithmetic; what these can catch is the expansion's.

    * butterfly: Durrleman's ``g < -G_TOL`` on the 1201-point scan of [-K_MAX, K_MAX] (no zoom refinement), with
      ``g`` from the COS density ``f`` and ``w`` (Gatheral & Jacquier 2014, eq. 2.2 inverted:
      ``g = f sqrt(2 pi w) / exp(-d2^2 / 2)``, ``d2 = -k / sqrt(w) - sqrt(w) / 2``), whose sign is the sign of the
      COS-priced put's second strike derivative;
    * calendar: ``w(k, T2) < w(k, T1) - 1e-8`` on each adjacent pair's common quoted range (201 points) and on
      :data:`ARB_K_GRID`.

    Only points where both prices involved are at least ``floor`` (``df F`` units) and inside the density's range
    count; the rest are excluded and counted in ``violation_points_excluded`` (most of [-3, 3] at a 1-day expiry)."""
    from .svi import _K_GRID

    ss = sorted(slices, key=lambda s: s[0])
    bf, excluded = 0, 0
    W: dict[float, np.ndarray] = {}
    for T, _, _ in ss:
        iv = heston.implied_vol(p, _K_GRID, T, n_terms, L, floor)
        w = iv * iv * T
        W[T] = heston.implied_vol(p, ARB_K_GRID, T, n_terms, L, floor) ** 2 * T
        a, b = heston.truncation_range(T, p, L)
        ok = np.isfinite(w) & (w > 0) & (_K_GRID > a) & (_K_GRID < b)
        excluded += int((~ok).sum())
        if ok.any():
            k, ww = _K_GRID[ok], w[ok]
            sw = np.sqrt(ww)
            d2 = -k / sw - 0.5 * sw
            g = heston.density(p, k, T, n_terms, L) * np.sqrt(2.0 * np.pi * ww) * np.exp(0.5 * d2 * d2)
            bf += bool(np.min(g) < -G_TOL)
    cal = calq = pairs_q = 0
    for (T1, lo1, hi1), (T2, lo2, hi2) in zip(ss, ss[1:], strict=False):
        dw = W[T2] - W[T1]
        excluded += int((~np.isfinite(dw)).sum())
        cal += bool(np.nanmin(dw) < -1e-8) if np.isfinite(dw).any() else 0
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if hi > lo:
            pairs_q += 1
            k = np.linspace(lo, hi, QUOTED_N)
            d = (heston.implied_vol(p, k, T2, n_terms, L, floor) ** 2 * T2
                 - heston.implied_vol(p, k, T1, n_terms, L, floor) ** 2 * T1)
            excluded += int((~np.isfinite(d)).sum())
            calq += bool(np.nanmin(d) < -1e-8) if np.isfinite(d).any() else 0
    return {"butterfly_violations": int(bf), "n_slices": len(ss), "calendar_violations_quoted": int(calq),
            "calendar_pairs_quoted": int(pairs_q), "calendar_violations": int(cal),
            "calendar_pairs": max(len(ss) - 1, 0), "violation_points_excluded": int(excluded)}


def build_surface(
    ca: ChainAnalytics, min_dte: float = 1.0, max_slices: int = 40, n_k: int = 41, n_T: int = 30,
    rr_deltas: tuple[float, ...] = (0.25, 0.10), use_kernels: bool = False,
) -> VolSurface:
    """Fit every expiry with ``dte >= min_dte`` (up to ``max_slices``, nearest first).

    ``use_kernels`` routes each slice's SVI fit through the compute kernels (``fit_svi``)."""
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
        _, sk, siv, swt = smile_inputs(ca, str(sid))
        row["atm_iv_market"] = metrics.atm_vol_market(sk, siv)
        try:
            fit = fit_svi(sk, siv, T, weights=swt, use_kernels=use_kernels)
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
            im = metrics.implied_move(mk, mids[("mid", "C")], mids[("mid", "P")], F, ca.iv_spot)
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
    # each adjacent pair on its common quoted range (no extrapolated wing): the definition count_violations reports
    # as calendar_violations_quoted, so the page's table and the league's quoted count are one check
    cal = calendar_check_quoted([(f.T, f.params, f.k_min, f.k_max) for f in fits.values()])
    n_cal = sum(c["violation"] for c in cal)
    n_bf = sum(not f.butterfly["arbitrage_free"] for f in fits.values())
    if n_bf:
        notes.append(f"{n_bf} fitted smile(s) fail the Durrleman butterfly condition g(k) >= 0 somewhere "
                     "on the checked range (negative implied density)")
    if n_cal:
        notes.append(f"{n_cal} adjacent expiry pair(s) show decreasing total variance within their common quoted "
                     "range (calendar arbitrage in the fitted smiles, or quote noise)")

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
