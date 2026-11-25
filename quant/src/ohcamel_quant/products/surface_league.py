"""Completion task 4.5: ``vol.surface_league`` (weekdays 21:10 New York, heavy, class M) -- per underlying, the
surfaces the Volatility page can show, scored against each other on the warehouse's option snapshots.

Rows: one per model per underlying (``MODELS``; Task 4.6 adds Heston by COS as one more entry). Models now:

* ``SVI (per slice)`` -- raw SVI fitted expiry by expiry (``options.svi``; what the page's surface has shown);
* ``SSVI (power law)`` -- one surface free of calendar and butterfly arbitrage by construction (``options.ssvi``),
  fitted to the same expiries, quotes and weights (``options.surface.smile_inputs``).

Three scores (verdict ``DESCRIPTIVE ONLY``: a measurement, no model is chosen by it):

1. **In-sample RMSE**, implied-vol points, unweighted, every fitted quote of the latest snapshot day pooled.
2. **Violations**, counted by the same code for every model with SVI-form slices: ``butterfly_violations`` = slices
   failing ``svi.butterfly_check`` (Durrleman's ``g`` on ``[-K_MAX, K_MAX]`` by the refined scan ``svi._g_min``,
   tolerance ``svi.G_TOL``); ``calendar_violations`` = adjacent fitted expiries failing ``svi.calendar_check``
   (``w(k, T)`` non-decreasing, tolerance 1e-8) on ``K_GRID`` (``surface.ARB_K_GRID``), 1201 points over ``[-K_MAX, K_MAX]`` -- the range the
   page's densities integrate over, so a wing crossing beyond the quotes counts.
3. **Next-day RMSE**, implied-vol points: the previous snapshot day's fitted parameters scored on the latest day's
   identified quotes (``use_smile``, expiries with ``dte >= MIN_DTE``). Each quote is predicted by its own expiry's
   smile as fitted the day before, at the quote's new log-forward-moneyness (sticky moneyness, same expiry); a quote
   on an expiry the earlier fit did not have is counted in ``next_day_unscored``. Measured in implied vol, not price:
   the chains are American, and an IV is how a quote is identified (an American mid and a Black price differ by the
   early-exercise premium). Blank, with the reason in ``next_day_note``, when there is no earlier snapshot day or
   the earlier one is more than ``MAX_GAP_DAYS`` calendar days back (not a next-day score).

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
from ..options import ssvi, svi
from ..options.chain import ChainAnalytics, analyze_chain
from ..options.surface import ARB_K_GRID as K_GRID  # noqa: F401  (the grid the calendar count uses)
from ..options.surface import VolSurface, build_surface, count_violations, fit_ssvi_surface

KIND = "vol.surface_league"
MIN_DTE = 1.0              # build_surface's default: expiries under a day are not fitted, nor scored
MAX_GAP_DAYS = 4           # Friday -> Tuesday over a Monday holiday; further back is not "the next day"
COLS = ["underlying", "model", "asof", "n_slices", "n_quotes", "rmse_vol_pts", "butterfly_violations",
        "calendar_violations", "calendar_pairs", "next_day_rmse_vol_pts", "next_day_n", "next_day_unscored",
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
            **count_violations([(f.T, f.params) for f in fits.values()])}


def _svi_predict(fits: dict[str, svi.SVIFit], sid: str, k: np.ndarray) -> np.ndarray | None:
    f = fits.get(sid)
    return None if f is None else f.params.implied_vol(k, f.T)


# --- SSVI -----------------------------------------------------------------------------------------------------------
@dataclass
class _SSVIFitted:
    fit: ssvi.SSVIFit
    T_of: dict[str, float]     # slice id -> its fitted maturity


def _ssvi_fit(ca: ChainAnalytics, vs: VolSurface) -> _SSVIFitted:
    return _SSVIFitted(fit=fit_ssvi_surface(ca, vs), T_of={sid: f.T for sid, f in vs.fits.items()})


def _ssvi_score(m: _SSVIFitted) -> dict[str, Any]:
    p = m.fit.params
    return {"n_quotes": m.fit.n, "rmse_vol_pts": m.fit.rmse_vol_pts,
            **count_violations([(float(t), ssvi.slice_svi(p, float(t))) for t in p.T])}


def _ssvi_predict(m: _SSVIFitted, sid: str, k: np.ndarray) -> np.ndarray | None:
    T = m.T_of.get(sid)
    return None if T is None else ssvi.implied_vol(m.fit.params, k, T)


def _ssvi_params(m: _SSVIFitted) -> dict[str, Any]:
    p = m.fit.params
    return {"rho": p.rho, "eta": p.eta, "gamma": p.gamma, "eta_one_plus_abs_rho": p.eta * (1 + abs(p.rho))}


MODELS: tuple[Model, ...] = (
    Model("SVI (per slice)", _svi_fit, _svi_score, _svi_predict, lambda f: None),
    Model("SSVI (power law)", _ssvi_fit, _ssvi_score, _ssvi_predict, _ssvi_params),
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
        except (ValueError, np.linalg.LinAlgError) as e:
            errors.append({"underlying": und, "asof": asof, "model": m.name, "error": str(e)})
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
        gap = (pd.Timestamp(asof) - pd.Timestamp(prev)).days
        if gap > MAX_GAP_DAYS:
            note = f"the earlier snapshot day ({prev}) is {gap} days back, beyond {MAX_GAP_DAYS}; not a next-day score"
        else:
            try:
                ca0, vs0 = _day(ctx, und, prev)
                fitted0 = _fit_all(ca0, vs0, und, prev, errors)
                nd = next_day(fitted0, ca)
                if not fitted0:
                    note = f"no model fitted the {prev} snapshot"
            except (DataUnavailable, ValueError, KeyError, np.linalg.LinAlgError) as e:
                note = f"the {prev} snapshot could not be fitted: {e}"
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
        except (DataUnavailable, ValueError, KeyError, np.linalg.LinAlgError) as e:   # one name never sinks the night
            errors.append({"underlying": und, "asof": dates[-1], "model": None, "error": str(e)})
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
               "(refined scan); calendar = adjacent fitted expiries whose total variance decreases at some k of a "
               "1201-point grid on the same range; one code for both models",
               "next-day RMSE: the previous snapshot day's fit (same expiry, sticky log-forward-moneyness) on the "
               "latest day's identified quotes, implied-vol points; blank with its reason when there is no earlier "
               f"day or it is more than {MAX_GAP_DAYS} days back",
               "SSVI: power-law phi under eta (1 + |rho|) <= 2, gamma in (0, 1/2], theta non-decreasing "
               "(Gatheral & Jacquier 2014, Thm 4.1, Thm 4.2, Cor. 4.1)"],
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"{n_und} UNDERLYING{'S' if n_und != 1 else ''} · {len(MODELS)} MODELS · MEASUREMENT")
