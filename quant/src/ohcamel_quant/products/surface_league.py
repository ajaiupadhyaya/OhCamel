"""Completion task 4.5: ``vol.surface_league`` (weekdays 21:10 New York, heavy, class M) -- per underlying, the
surfaces the Volatility page can show, scored against each other on the warehouse's option snapshots.

Rows: one per model per underlying (``MODELS``). Models:

* ``SVI (per slice)`` -- raw SVI fitted expiry by expiry (``options.svi``; what the page's surface has shown);
* ``SSVI (power law)`` -- one surface free of calendar and butterfly arbitrage by construction (``options.ssvi``),
  fitted to the same expiries, quotes and weights (``options.surface.smile_inputs``);
* ``Heston (COS)`` (Task 4.6) -- five parameters for the whole surface, priced by the COS method and calibrated
  nightly (``options.heston``) to the SAME identified-IV quotes, each IV turned into a European Black-76 price on its
  expiry's forward and discount factor (``surface.heston_quotes``; the chains are American, and this keeps the three
  models on one footing), vega-weighted. Scored in IV points (its prices inverted to Black vols); its violations
  are counted on the same grids and definitions from its implied total variance (``surface.heston_violations``).

Three scores (verdict ``DESCRIPTIVE ONLY``: a measurement, no model is chosen by it):

1. **In-sample RMSE**, implied-vol points, unweighted, every fitted quote of the latest snapshot day pooled.
2. **Violations**, counted by the same code for every model with SVI-form slices: ``butterfly_violations`` = slices
   failing ``svi.butterfly_check`` (Durrleman's ``g`` on ``[-K_MAX, K_MAX]`` by the refined scan ``svi._g_min``,
   tolerance ``svi.G_TOL``); and two calendar counts of adjacent fitted expiries whose total variance decreases
   (tolerance 1e-8), kept apart because they answer different questions (``surface.count_violations``):
   ``calendar_violations_quoted`` of ``calendar_pairs_quoted`` -- on each pair's COMMON QUOTED range
   ``[max(kmin1, kmin2), min(kmax1, kmax2)]`` (201 points; ``svi.calendar_check_quoted``), where both smiles are
   pinned by quotes; and ``calendar_violations`` of ``calendar_pairs`` -- on ``K_GRID`` (``surface.ARB_K_GRID``),
   1201 points over ``[-K_MAX, K_MAX]``, the range the page's densities integrate over, INCLUDING the wings each
   smile extrapolates beyond its quotes (a short expiry quoted on ``[-0.11, 0.04]`` is extrapolated over most of it).
3. **Next-day IV RMSE** (vol points): the previous session's fitted parameters scored on the latest day's
   identified quotes (``use_smile``, expiries with ``dte >= MIN_DTE``). Each quote is predicted by its own expiry's
   smile as fitted the day before, at the quote's new log-forward-moneyness (sticky moneyness, same expiry); a quote
   on an expiry the earlier fit did not have is counted in ``next_day_unscored``. Measured in implied vol, not price:
   the chains are American, and an IV is how a quote is identified (an American mid and a Black price differ by the
   early-exercise premium). Blank, with the reason in ``next_day_note``, when there is no earlier snapshot day or
   it is not the previous NYSE session (``signals.calendar.previous_session``: weekdays less the rule holidays; a
   snapshot missed for a day makes the next one unscored, as it is not a next-day score).

Snapshots: each day's pre-close pull, quote time, spot and zero curve are read exactly as ``vol.surface_history``
reads them (``surface_history._chain`` / ``_curve``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..jobs.context import JobCancelled
from ..options import heston, ssvi, svi
from ..options.chain import ChainAnalytics, analyze_chain
from ..options.surface import ARB_K_GRID as K_GRID  # noqa: F401  (the grid the calendar count uses)
from ..options.surface import (
    VolSurface,
    build_surface,
    count_violations,
    fit_heston_surface,
    fit_ssvi_surface,
    heston_violations,
)

KIND = "vol.surface_league"
MIN_DTE = 1.0              # build_surface's default: expiries under a day are not fitted, nor scored
MAX_GAP_SESSIONS = 1       # the earlier snapshot must be the previous NYSE session: "the next day"
COLS = ["underlying", "model", "asof", "n_slices", "n_quotes", "rmse_vol_pts", "butterfly_violations",
        "calendar_violations_quoted", "calendar_pairs_quoted", "calendar_violations", "calendar_pairs",
        "next_day_rmse_vol_pts", "next_day_n", "next_day_unscored",
        "next_day_fit_asof", "next_day_note", "params", "fit_note"]
ERR_COLS = ["underlying", "asof", "model", "error"]


@dataclass(frozen=True)
class Model:
    """One league row's model: ``fit(ca, vs)`` on a snapshot day's chain and its per-slice surface; ``score``
    gives n_quotes, rmse_vol_pts and the violation counts; ``predict(fitted, sid, k)`` the implied vol the fit
    gives expiry ``sid`` at log-moneyness ``k`` (``None`` when it has no such expiry); ``params`` a JSON-able
    summary for the table."""

    name: str
    fit: Callable[[ChainAnalytics, VolSurface], Any]
    score: Callable[[Any], dict[str, Any]]
    predict: Callable[[Any, str, np.ndarray], np.ndarray | None]
    params: Callable[[Any], dict[str, Any] | None]


# --- per-slice SVI --------------------------------------------------------------------------------------------------
def _svi_fit(ca: ChainAnalytics, vs: VolSurface) -> dict[str, svi.SVIFit]:
    return dict(vs.fits)


def _svi_score(fits: dict[str, svi.SVIFit]) -> dict[str, Any]:
    n = np.array([f.n for f in fits.values()], dtype=float)
    r = np.array([f.rmse_vol_pts for f in fits.values()], dtype=float)
    return {"n_quotes": int(n.sum()), "rmse_vol_pts": float(np.sqrt(np.sum(n * r * r) / n.sum())),
            **count_violations([(f.T, f.params, f.k_min, f.k_max) for f in fits.values()])}


def _svi_predict(fits: dict[str, svi.SVIFit], sid: str, k: np.ndarray) -> np.ndarray | None:
    f = fits.get(sid)
    return None if f is None else f.params.implied_vol(k, f.T)


# --- SSVI -----------------------------------------------------------------------------------------------------------
@dataclass
class _SSVIFitted:
    fit: ssvi.SSVIFit
    T_of: dict[str, float]                        # slice id -> its fitted maturity
    k_range: dict[float, tuple[float, float]]     # fitted maturity -> the quoted k range it was fitted to


def _ssvi_fit(ca: ChainAnalytics, vs: VolSurface) -> _SSVIFitted:
    return _SSVIFitted(fit=fit_ssvi_surface(ca, vs), T_of={sid: f.T for sid, f in vs.fits.items()},
                       k_range={f.T: (f.k_min, f.k_max) for f in vs.fits.values()})


def _ssvi_score(m: _SSVIFitted) -> dict[str, Any]:
    p = m.fit.params
    return {"n_quotes": m.fit.n, "rmse_vol_pts": m.fit.rmse_vol_pts,
            **count_violations([(float(t), ssvi.slice_svi(p, float(t)), *m.k_range[float(t)]) for t in p.T])}


def _ssvi_predict(m: _SSVIFitted, sid: str, k: np.ndarray) -> np.ndarray | None:
    T = m.T_of.get(sid)
    return None if T is None else ssvi.implied_vol(m.fit.params, k, T)


def _ssvi_params(m: _SSVIFitted) -> dict[str, Any]:
    p = m.fit.params
    return {"rho": p.rho, "eta": p.eta, "gamma": p.gamma, "eta_one_plus_abs_rho": p.eta * (1 + abs(p.rho))}


# --- Heston by COS (Task 4.6) ---------------------------------------------------------------------------------------
@dataclass
class _HestonFitted:
    fit: heston.HestonFit
    T_of: dict[str, float]
    k_range: dict[float, tuple[float, float]]
    excluded: int | None = None      # violation-test points below the price floor (set by _heston_score)


def _heston_fit(ca: ChainAnalytics, vs: VolSurface) -> _HestonFitted:
    return _HestonFitted(fit=fit_heston_surface(ca, vs), T_of={sid: f.T for sid, f in vs.fits.items()},
                         k_range={f.T: (f.k_min, f.k_max) for f in vs.fits.values()})


def _heston_score(m: _HestonFitted) -> dict[str, Any]:
    v = heston_violations(m.fit.params, [(T, *r) for T, r in m.k_range.items()])
    m.excluded = v.pop("violation_points_excluded")
    scored = m.fit.n - m.fit.n_iv_failed
    f = m.fit
    note = (f"{scored} of {f.n} quotes scored" + (f" ({f.n_iv_failed} model prices below the inversion floor)"
                                                  if f.n_iv_failed else "")
            + f" · COS {f.n_terms} terms, L {f.L:g} · fit status {f.status}, {f.nfev} evaluations, start {f.start}")
    return {"n_quotes": m.fit.n, "rmse_vol_pts": m.fit.rmse_vol_pts, "fit_note": note, **v}


def _heston_predict(m: _HestonFitted, sid: str, k: np.ndarray) -> np.ndarray | None:
    T = m.T_of.get(sid)
    return None if T is None else heston.implied_vol(m.fit.params, k, T, heston.LEAGUE_N, heston.LEAGUE_L)


def _heston_params(m: _HestonFitted) -> dict[str, Any]:
    f = m.fit
    out = {**f.params.to_dict(), "feller_ratio": f.feller_ratio, "n_iv_failed": f.n_iv_failed,
           "max_abs_err_vol_pts": f.max_abs_err_vol_pts, "status": f.status, "nfev": f.nfev, "cost": f.cost,
           "start": f.start, "n_terms": f.n_terms, "L": f.L}
    if m.excluded is not None:
        out["violation_points_excluded"] = m.excluded
    return out


MODELS: tuple[Model, ...] = (
    Model("SVI (per slice)", _svi_fit, _svi_score, _svi_predict, lambda f: None),
    Model("SSVI (power law)", _ssvi_fit, _ssvi_score, _ssvi_predict, _ssvi_params),
    Model("Heston (COS)", _heston_fit, _heston_score, _heston_predict, _heston_params),
)


# --- next day -------------------------------------------------------------------------------------------------------
def next_day(fitted: dict[str, Any], ca_next: ChainAnalytics, min_dte: float = MIN_DTE) -> dict[str, dict[str, Any]]:
    """Each model's RMSE (vol points) on ``ca_next``'s identified quotes (``use_smile``, ``dte >= min_dte``),
    predicted by ``fitted[model]`` (fitted on an earlier day). Only quotes every model can price are scored, so all
    rows are on one set; the rest are counted as ``unscored``. ``{model: {rmse_vol_pts, n, unscored}}``."""
    models = [m for m in MODELS if m.name in fitted]
    err: dict[str, list[np.ndarray]] = {m.name: [] for m in models}
    unscored = 0
    for sid, row in ca_next.slices.iterrows():
        if float(row["dte"]) < min_dte:
            continue
        q = ca_next.slice_quotes(str(sid))
        q = q[q["use_smile"]]
        k, iv = q["k"].to_numpy(float), q["iv"].to_numpy(float)
        ok = np.isfinite(k) & np.isfinite(iv) & (iv > 0)
        k, iv = k[ok], iv[ok]
        if not k.size:
            continue
        preds = {m.name: m.predict(fitted[m.name], str(sid), k) for m in models}
        if any(p is None for p in preds.values()):
            unscored += int(k.size)
            continue
        good = np.logical_and.reduce([np.isfinite(p) for p in preds.values()])
        unscored += int((~good).sum())
        for name, p in preds.items():
            err[name].append((p[good] - iv[good]) * 100.0)
    out = {}
    for name, es in err.items():
        e = np.concatenate(es) if es else np.array([])
        out[name] = {"rmse_vol_pts": float(np.sqrt(np.mean(e * e))) if e.size else float("nan"),
                     "n": int(e.size), "unscored": unscored}
    return out


def sessions_between(prev: str, asof: str) -> int:
    """NYSE sessions in ``(prev, asof]`` (``signals.calendar``: weekdays less the rule holidays): 1 when ``prev``
    is the previous session of ``asof`` -- a Friday before a Monday, or before a Tuesday after a Monday holiday."""
    from ..signals.calendar import next_session

    d, end, n = pd.Timestamp(prev).date(), pd.Timestamp(asof).date(), 0
    while d < end:
        d = next_session(d)
        n += 1
    return n


# --- the job --------------------------------------------------------------------------------------------------------
def _day(ctx: Any, und: str, asof: str) -> tuple[ChainAnalytics, VolSurface]:
    from .surface_history import _chain, _curve

    chain, _ = _chain(ctx.warehouse, und, asof)
    ca = analyze_chain(chain, rate_curve=_curve(ctx.market, asof).rate)
    return ca, build_surface(ca, min_dte=MIN_DTE, use_kernels=True)


def _days(con: Any) -> dict[str, list[str]]:
    from .surface_history import _pending

    out: dict[str, list[str]] = {}
    for und, asof in _pending(con, set()):
        out.setdefault(und, []).append(asof)
    return {u: sorted(d) for u, d in sorted(out.items())}


def _fit_all(ca: ChainAnalytics, vs: VolSurface, und: str, asof: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for m in MODELS:
        try:
            out[m.name] = m.fit(ca, vs)
        except JobCancelled:
            raise
        except Exception as e:      # one model's failure is that model's row, never the name's or the night's
            errors.append({"underlying": und, "asof": asof, "model": m.name, "error": f"{type(e).__name__}: {e}"})
    return out


def _underlying(ctx: Any, und: str, dates: list[str], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    asof = dates[-1]
    ca, vs = _day(ctx, und, asof)
    today = _fit_all(ca, vs, und, asof, errors)
    nd: dict[str, dict[str, Any]] = {}
    prev = dates[-2] if len(dates) > 1 else None
    note = None
    if prev is None:
        note = "no earlier snapshot day for this underlying"
    else:
        gap = sessions_between(prev, asof)
        if gap > MAX_GAP_SESSIONS:
            note = (f"the earlier snapshot day ({prev}) is {gap} sessions back, not the previous session; "
                    "not a next-day score")
        else:
            try:
                ca0, vs0 = _day(ctx, und, prev)
                fitted0 = _fit_all(ca0, vs0, und, prev, errors)
                nd = next_day(fitted0, ca)
                if not fitted0:
                    note = f"no model fitted the {prev} snapshot"
            except JobCancelled:
                raise
            except Exception as e:
                note = f"the {prev} snapshot could not be fitted: {type(e).__name__}: {e}"
    rows = []
    for m in MODELS:
        row: dict[str, Any] = {"underlying": und, "model": m.name, "asof": asof}
        if m.name in today:
            row.update(m.score(today[m.name]))
            pr = m.params(today[m.name])
            row["params"] = None if pr is None else json.dumps({k: round(float(v), 8) for k, v in pr.items()})
        else:
            row["fit_note"] = next((e["error"] for e in errors if e["underlying"] == und and e["asof"] == asof
                                    and e["model"] == m.name), "not fitted")
        s = nd.get(m.name)
        if s is not None and s["n"] > 0:
            row.update({"next_day_rmse_vol_pts": s["rmse_vol_pts"], "next_day_n": s["n"],
                        "next_day_unscored": s["unscored"], "next_day_fit_asof": prev})
        else:
            row["next_day_note"] = note or (f"no quote of {asof} on an expiry the {prev} fit had"
                                            if s is not None else f"the model did not fit the {prev} snapshot")
            if s is not None:
                row["next_day_unscored"] = s["unscored"]
        rows.append(row)
    return rows


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    notes: list[str] = []
    try:
        days = _days(ctx.warehouse)
    except DataUnavailable as e:
        days, notes = {}, [f"warehouse unavailable: {e}"]
    if params.get("underlyings"):
        want = {str(u).upper() for u in params["underlyings"]}
        days = {u: d for u, d in days.items() if u in want}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, (und, dates) in enumerate(days.items()):
        ctx.check_cancelled()
        try:
            rows += _underlying(ctx, und, dates, errors)
        except JobCancelled:
            raise
        except Exception as e:       # one name never sinks the night: its failure and reason are that name's record
            errors.append({"underlying": und, "asof": dates[-1], "model": None, "error": f"{type(e).__name__}: {e}"})
        ctx.progress((i + 1) / max(len(days), 1), und)
    league = pd.DataFrame(rows, columns=COLS)
    err = pd.DataFrame(errors, columns=ERR_COLS)
    if league.empty:
        return ArtifactSpec(tables={"league": league, "errors": err}, data_asof=None, provenance=[], notes=notes,
                            verdict="INSUFFICIENT DATA", verdict_detail="NO OPTION SNAPSHOTS")
    n_und = league["underlying"].nunique()
    return ArtifactSpec(
        tables={"league": league, "errors": err}, data_asof=str(league["asof"].max()),
        provenance=[{"source": "warehouse:option_snapshots (Cboe delayed)"}],
        notes=[*notes,
               "in-sample RMSE: implied-vol points, every fitted quote of the latest snapshot day pooled, unweighted",
               f"violations: butterfly = slices with Durrleman g < 0 on k in [-{svi.K_MAX:g}, {svi.K_MAX:g}] "
               "(refined scan); calendar, two counts of adjacent fitted expiries whose total variance decreases: "
               "within the pair's common quoted k range (201 points), and on a 1201-point grid of the same [-3, 3] "
               "range including the wings each smile extrapolates beyond its quotes; one code for both models",
               "next-day IV RMSE (vol pts): the previous session's fit (same expiry, sticky log-forward-moneyness) on "
               "the latest day's identified quotes; blank with its reason when there is no earlier snapshot day or "
               "it is not the previous NYSE session",
               "SSVI: power-law phi under eta (1 + |rho|) <= 2, gamma in (0, 1/2], theta non-decreasing "
               "(Gatheral & Jacquier 2014, Thm 4.1, Thm 4.2, Cor. 4.1)",
               "Heston: COS (Fang & Oosterlee 2008) at 1024 terms on c1 -/+ 24 sqrt(c2) (a strike near or past the "
               "edge gets its own extended range), little-trap CF (Albrecher et al. 2007), calibrated by least squares on European Black-76 prices of the same identified-IV "
               "quotes (the chains are American), vega-weighted; Feller 2 kappa theta >= sigma^2 reported in params, "
               "not enforced; violations from its implied total variance on the same grids, at points whose price is at "
               "least 1e-8 of df F (the rest excluded, counted in params)"],
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"{n_und} UNDERLYING{'S' if n_und != 1 else ''} · {len(MODELS)} MODELS · MEASUREMENT")
