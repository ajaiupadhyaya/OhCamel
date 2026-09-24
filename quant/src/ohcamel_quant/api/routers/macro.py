"""/api/macro -- rates, fixed income, macro indicators and regimes from real data.

Every number comes from FRED (Treasury CMT curve, macro series, NBER
recession dates, credit spreads, VIX) or real ETF prices via
:class:`MarketData`. Model parameters that are genuine choices (Taylor-rule
``r*``/``pi*``, forecast horizon, number of regimes, PCA window) are query
inputs with literature defaults, named in each payload's ``method``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ...data.base import DataUnavailable, Provenance
from ...data.market import MarketData, get_market
from ...macro import bonds as bd
from ...macro import curve as cv
from ...macro import dashboard as dash
from ...macro import recession as rec
from ...macro import regimes as rg
from ..serialize import clean, frame, records, series

router = APIRouter(prefix="/macro", tags=["macro"])
Market = Annotated[MarketData, Depends(get_market)]

_TTL_S = 900.0
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: tuple[Any, ...], build: Callable[[], Any], ttl: float = _TTL_S) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] <= ttl:
            return hit[1]
    val = build()
    with _lock:
        _cache[key] = (now, val)
        if len(_cache) > 128:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:32]:
                _cache.pop(k, None)
    return val


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _prov(provs: list[Provenance]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for p in provs:
        d = p.to_dict()
        key = (d["source"], d["fetched_at"], str(d["detail"]))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _fetch(market: MarketData, ids: list[str], start: date | None = None,
           end: date | None = None) -> tuple[dict[str, pd.Series], list[Provenance], dict[str, str]]:
    """FRED series by id; one batch request, falling back to per-id requests
    so a single failing series does not sink the others. Returns
    ``(series, provenance, {missing_id: reason})``."""
    got: dict[str, pd.Series] = {}
    provs: list[Provenance] = []
    missing: dict[str, str] = {}
    try:
        ds = market.fred(ids, start, end)
        for sid in ids:
            got[sid] = ds.data[sid].dropna()
        provs.extend(ds.provenance)
        return got, provs, missing
    except DataUnavailable:
        pass
    for sid in ids:
        try:
            ds = market.fred([sid], start, end)
            got[sid] = ds.data[sid].dropna()
            provs.extend(ds.provenance)
        except DataUnavailable as e:
            missing[sid] = str(e)
    return got, provs, missing


def _require(market: MarketData, ids: list[str], what: str) -> tuple[dict[str, pd.Series], list[Provenance]]:
    got, provs, missing = _fetch(market, ids)
    if missing:
        raise DataUnavailable(f"{what} needs FRED {', '.join(missing)}: " + "; ".join(missing.values()))
    return got, provs


# =========================================================================
# Dashboard
# =========================================================================
@router.get("/dashboard")
def dashboard(market: Market) -> dict[str, Any]:
    """Every configured macro series: latest, 1M/3M/1Y change, 10y percentile, sparkline."""
    def build() -> dict[str, Any]:
        cfg = dash.load_series_config()
        ids = [s["id"] for s in cfg["series"]]
        got, provs, missing = _fetch(market, ids)
        if not got:
            raise DataUnavailable("macro dashboard: no FRED series could be fetched: "
                                  + "; ".join(f"{k}: {v}" for k, v in missing.items()))
        rows: list[dict[str, Any]] = []
        for spec in cfg["series"]:
            sid = spec["id"]
            row = dict(spec)
            if sid not in got:
                row["error"] = missing.get(sid, "unavailable")
                rows.append(row)
                continue
            try:
                x = dash.apply_transform(got[sid], spec["transform"])
                row.update(dash.summarize(x))
            except ValueError as e:
                row["error"] = str(e)
            rows.append(row)
        categories = list(dict.fromkeys(s["category"] for s in cfg["series"]))
        highlights: dict[str, Any] = {}
        if "UNRATE" in got:
            sahm = rec.sahm_rule(got["UNRATE"])
            if not sahm.empty:
                highlights["sahm_rule"] = {"value": float(sahm["sahm"].iloc[-1]), "date": sahm.index[-1],
                                           "threshold": rec.SAHM_THRESHOLD,
                                           "triggered": bool(sahm["triggered"].iloc[-1])}
        notes = ["change vs 1M/3M/1Y = latest minus the value that was latest at that earlier date; "
                 "horizons shorter than a series' release frequency (e.g. 1M for quarterly GDP) are null",
                 "percentile = share of the last 10 years' observations at or below the latest value",
                 "values are as published by FRED (latest vintage, revisions included)"]
        if missing:
            notes.append("unavailable: " + ", ".join(sorted(missing)))
        return clean({
            "series": rows, "categories": categories, "transforms": cfg["transforms"],
            "highlights": highlights, "missing": missing, "notes": notes,
            "method": {"name": "macro dashboard", "transforms": dash.TRANSFORMS,
                       "percentile_window_years": 10, "sahm": "Sahm (2019)"},
            "provenance": _prov(provs),
        })

    return _cached(("dashboard",), build)


# =========================================================================
# Generic FRED explorer
# =========================================================================
@router.get("/series")
def fred_series(
    market: Market,
    ids: Annotated[str, Query(description="comma-separated FRED ids, e.g. DGS10,DGS2")],
    transform: Annotated[str, Query(description="one transform, or one per id (comma-separated)")] = "level",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Any FRED series with a transform (level | yoy_pct | diff | mom_ann)."""
    id_list = list(dict.fromkeys(s.strip().upper() for s in ids.split(",") if s.strip()))
    if not id_list:
        raise HTTPException(422, "no series ids")
    if len(id_list) > 12:
        raise HTTPException(422, "at most 12 series per request")
    tr = [t.strip() for t in transform.split(",") if t.strip()]
    if len(tr) == 1:
        tr = tr * len(id_list)
    if len(tr) != len(id_list):
        raise HTTPException(422, "give one transform or one per id")
    for t in tr:
        if t not in dash.TRANSFORMS:
            raise HTTPException(422, f"unknown transform {t!r}; choose from {', '.join(dash.TRANSFORMS)}")
    # transforms need history before `start` (yoy looks back a year)
    fetch_start = None if start is None else (pd.Timestamp(start) - pd.DateOffset(years=1, days=10)).date()
    ds = market.fred(id_list, fetch_start, end)
    out: dict[str, pd.Series] = {}
    summaries: dict[str, Any] = {}
    for sid, t in zip(id_list, tr, strict=True):
        x = dash.apply_transform(ds.data[sid], t)  # type: ignore[arg-type]
        if start is not None:
            x = x[x.index >= pd.Timestamp(start)]
        out[sid] = x
        if not x.empty:
            summaries[sid] = dash.summarize(x)
            summaries[sid].pop("sparkline", None)
    meta = market.fred_metadata(id_list)
    wide = pd.DataFrame(out)
    wide.index.name = "date"
    return clean({
        "ids": id_list, "transforms": dict(zip(id_list, tr, strict=True)), "data": frame(wide),
        "summary": summaries, "metadata": meta.data,
        "notes": ["series are outer-joined on their own observation dates (no forward fill)",
                  "summary.percentile_10y ranks the latest value within summary.percentile_window (the last 10 "
                  "years of the returned data, i.e. clipped by 'start'); see its description and 'complete' flag"],
        "method": {"transforms": {t: dash.load_series_config()["transforms"][t] for t in set(tr)}},
        "provenance": _prov(ds.provenance + meta.provenance),
    })


# =========================================================================
# Yield curve
# =========================================================================
def _curves(market: MarketData) -> tuple[pd.DataFrame, list[Provenance]]:
    ds = market.treasury_curve()
    return ds.data, ds.provenance


def _parse_compare(token: str, ref: pd.Timestamp) -> pd.Timestamp:
    t = token.strip().upper()
    if not t:
        raise ValueError("empty compare token")
    if t[-1] in "DWMY" and t[:-1].isdigit():
        n = int(t[:-1])
        off = {"D": pd.DateOffset(days=n), "W": pd.DateOffset(weeks=n), "M": pd.DateOffset(months=n),
               "Y": pd.DateOffset(years=n)}[t[-1]]
        return ref - off
    return pd.Timestamp(token.strip())


def _curve_payload(d: pd.Timestamp, par: pd.Series, grid: np.ndarray) -> dict[str, Any]:
    zc = cv.bootstrap_par_curve(par.index.to_numpy(dtype=float), par.to_numpy(dtype=float))
    g = grid[grid <= zc.t[-1] + 1e-9]
    tab = zc.table(g)
    return {
        "date": d, "par": {"tenors": par.index.tolist(), "yields": par.to_numpy().tolist()},
        "curve": tab.to_dict(orient="list"),
        "nodes": {"t": zc.t.tolist(), "zero": zc.zero(zc.t).tolist(), "discount": zc.df.tolist(),
                  "par": (zc.par.tolist() if zc.par is not None else None)},
        "notes": zc.notes,
        "_zc": zc,
    }


@router.get("/curve")
def curve(
    market: Market,
    on: Annotated[date | None, Query(alias="date", description="curve date (last close on/before)")] = None,
    compare: Annotated[str, Query(description="offsets (1M,1Y,...) or ISO dates")] = "1M,1Y",
) -> dict[str, Any]:
    """Par (CMT), bootstrapped zero, instantaneous and 1y-forward curves;
    Nelson-Siegel and Svensson fits; comparison curves."""
    def build() -> dict[str, Any]:
        curves, provs = _curves(market)
        target = pd.Timestamp(on) if on is not None else curves.index[-1]
        d, par = cv.curve_on(curves, target)
        grid = np.round(np.arange(0.25, 30.0 + 1e-9, 0.25), 4)
        main = _curve_payload(d, par, grid)
        zc: cv.ZeroCurve = main.pop("_zc")
        t_fit, z_fit = zc.t, zc.zero(zc.t)
        fits: dict[str, Any] = {}
        fit_notes: list[str] = []
        for name, fn in (("nelson_siegel", cv.fit_nelson_siegel), ("svensson", cv.fit_svensson)):
            try:
                f = fn(t_fit, z_fit)
                g = grid[grid <= zc.t[-1] + 1e-9]
                binding = [c for c, v in (("b0 >= 0", f.params["b0"]),
                                          ("b0 + b1 >= 0", f.params["b0"] + f.params["b1"])) if v <= 1e-8]
                fits[name] = {"params": f.params, "rmse_bp": f.rmse_bp, "starts": f.starts,
                              "fitted": {"t": g.tolist(), "zero": f(g).tolist()},
                              "residuals_bp": {"t": t_fit.tolist(), "bp": f.residuals_bp.tolist()},
                              "binding_constraints": binding}
                if binding:
                    fit_notes.append(f"{name}: sign restriction {', '.join(binding)} is binding -- the "
                                     "long-run level is not identified by maturities <= 30y; read the "
                                     "fitted curve, not b0, as the long end")
            except ValueError as e:
                fits[name] = {"error": str(e)}
        comps = []
        for tok in [c for c in compare.split(",") if c.strip()][:6]:
            try:
                cd, cpar = cv.curve_on(curves, _parse_compare(tok, d))
            except ValueError as e:
                comps.append({"label": tok.strip(), "error": str(e)})
                continue
            p = _curve_payload(cd, cpar, grid)
            p.pop("_zc")
            p["label"] = tok.strip()
            p["change_bp"] = {str(k): 100.0 * (float(par[k]) - float(cpar[k]))
                              for k in par.index if k in cpar.index}
            comps.append(p)
        notes = [
            "CMT par yields (bond-equivalent, semiannual) interpolated by PCHIP onto the semiannual grid, "
            "bills (<= 6M) as simple BEY discount instruments, coupon nodes bootstrapped as par bonds",
            "zero and forward rates are continuously compounded (percent)",
            "NS/NSS are fitted to the bootstrapped zero rates at the curve nodes, with the sign "
            "restrictions b0 >= 0 and b0 + b1 >= 0 (Bundesbank / ECB practice)",
        ] + main["notes"] + fit_notes
        if d != target.normalize():
            notes.append(f"requested {target.date()}; latest curve on/before is {d.date()}")
        return clean({
            **main, "fits": fits, "compare": comps, "notes": notes,
            "method": {"bootstrap": "par-bond bootstrap (Hull, Options, Futures & Other Derivatives, ch. 4)",
                       "interpolation": "PCHIP (Fritsch & Carlson 1980) on par yields and on -ln P(t)",
                       "parametric": ["Nelson & Siegel (1987)", "Svensson (1994)",
                                      "variable projection NLS, multi-start (Gurkaynak, Sack & Wright 2007)"]},
            "provenance": _prov(provs),
        })

    return _cached(("curve", str(on), compare), build)


@router.get("/curve/history")
def curve_history(
    market: Market,
    years: Annotated[int, Query(ge=1, le=70, description="window for slopes/heatmap")] = 30,
    pca_years: Annotated[int, Query(ge=2, le=40, description="window for PCA of daily changes")] = 10,
) -> dict[str, Any]:
    """Curve history: monthly heatmap, slopes/butterfly, PCA (level/slope/curvature)."""
    def build() -> dict[str, Any]:
        curves, provs = _curves(market)
        last = curves.index[-1]
        win = curves[curves.index > last - pd.DateOffset(years=years)]
        heat = cv.monthly_sample(win)
        spreads = cv.curve_spreads(win)
        pwin = curves[curves.index > last - pd.DateOffset(years=pca_years)]
        tenors = [c for c in pwin.columns if pwin[c].notna().mean() > 0.9]
        pca = cv.yield_pca(pwin, tenors)
        weekly_scores = pca.cumulative.resample("W-FRI").last().dropna(how="all")
        return clean({
            "heatmap": {"dates": list(heat.index), "tenors": [float(c) for c in heat.columns],
                        "yields": heat.to_numpy().tolist()},
            "spreads": frame(spreads),
            "spreads_latest": {c: {"value_bp": spreads[c].dropna().iloc[-1],
                                   "date": spreads[c].dropna().index[-1],
                                   "percentile": rg.percentile_rank(spreads[c])}
                               for c in spreads.columns if spreads[c].notna().any()},
            "pca": {"tenors": pca.tenors, "loadings": frame(pca.loadings),
                    "explained_variance": pca.explained[: pca.loadings.shape[1]].tolist(),
                    "explained_variance_all": pca.explained.tolist(),
                    "eigenvalues_bp2": pca.eigenvalues.tolist(), "n_obs": pca.n_obs,
                    "factor_levels_weekly": frame(weekly_scores),
                    "window": {"start": pca.scores.index[0], "end": pca.scores.index[-1]}},
            "notes": ["spreads in basis points: 2s10s = 10y-2y, 3m10y = 10y-3m, 5s30s = 30y-5y, "
                      "2s5s10s butterfly = 2*5y - 2y - 10y",
                      "heatmap is the last observation of each month",
                      "PCA uses days on which every chosen tenor is quoted; factor levels are cumulative "
                      "scores (bp) and are identified up to scale/sign (signs normalized)"],
            "method": {"pca": "eigen-decomposition of the covariance of daily yield changes "
                              "(Litterman & Scheinkman 1991)", "years": years, "pca_years": pca_years},
            "provenance": _prov(provs),
        })

    return _cached(("curve_history", years, pca_years), build)


# =========================================================================
# Recession
# =========================================================================
@router.get("/recession")
def recession(
    market: Market,
    h: Annotated[int, Query(ge=1, le=24, description="forecast horizon, months")] = 12,
    extra: Annotated[Literal["none", "nfci"], Query(description="optional extra regressor")] = "nfci",
) -> dict[str, Any]:
    """Estrella-Mishkin yield-curve probit and the Sahm rule."""
    def build() -> dict[str, Any]:
        core, provs = _require(market, ["USREC", "T10Y3M"], "recession probit")
        early, p2, miss_early = _fetch(market, ["GS10", "TB3MS"])
        provs += p2
        notes: list[str] = []
        spread, n1 = rec.term_spread_monthly(core["T10Y3M"], early.get("GS10"), early.get("TB3MS"))
        notes += n1
        last_daily = core["T10Y3M"].index[-1]
        if last_daily < last_daily + pd.offsets.BMonthEnd(0):
            notes.append(f"latest month's spread is a month-to-date average (through {last_daily.date()})")
        if miss_early:
            notes.append("pre-1982 spread unavailable (" + ", ".join(miss_early) + "); sample starts 1982")
        usrec = core["USREC"]
        base = rec.fit_probit(usrec, spread.to_frame("spread"), h)
        notes += base.notes
        off = pd.DateOffset(months=h)
        prob = base.fitted.copy()
        prob.index = prob.index + off
        last_origin = base.fitted.index[-1]
        models: dict[str, Any] = {"spread": base.to_dict()}
        prob_frame = pd.DataFrame({"spread_model": prob})
        current: dict[str, Any] = {"origin": last_origin, "target": last_origin + off,
                                   "spread": float(spread.loc[last_origin]),
                                   "probability": float(base.fitted.iloc[-1])}
        if extra == "nfci":
            got, p3, miss = _fetch(market, ["NFCI"])
            provs += p3
            if "NFCI" in got:
                nf = rec.monthly_mean(got["NFCI"])
                X = pd.concat([spread.rename("spread"), nf.rename("nfci")], axis=1).dropna()
                try:
                    m2 = rec.fit_probit(usrec, X, h)
                    models["spread_nfci"] = m2.to_dict()
                    p = m2.fitted.copy()
                    p.index = p.index + off
                    prob_frame["spread_nfci_model"] = p
                    current["probability_with_nfci"] = float(m2.fitted.iloc[-1])
                    current["nfci_origin"] = m2.fitted.index[-1]
                except ValueError as e:
                    notes.append(f"NFCI model not estimated: {e}")
            else:
                notes.append("NFCI unavailable: " + "; ".join(miss.values()))
        notes.append("the excess bond premium (Gilchrist & Zakrajsek 2012) is not on FRED and is not used")
        sahm_payload: dict[str, Any] | None = None
        got_u, p4, miss_u = _fetch(market, ["UNRATE"])
        provs += p4
        if "UNRATE" in got_u:
            s = rec.sahm_rule(got_u["UNRATE"])
            sahm_payload = {"series": frame(s[["unrate", "ma3", "sahm"]]), "threshold": rec.SAHM_THRESHOLD,
                            "latest": {"date": s.index[-1], "value": float(s["sahm"].iloc[-1]),
                                       "triggered": bool(s["triggered"].iloc[-1])},
                            "trigger_dates": [d for d, v in s["triggered"].items()
                                              if v and not s["triggered"].shift(1).fillna(False).loc[d]]}
        else:
            notes.append("Sahm rule unavailable: " + "; ".join(miss_u.values()))
        notes.append("NBER dates (USREC) are assigned with a lag of many months; recent zeros are provisional")
        notes.append(f"probabilities are in-sample fits indexed by the TARGET month (origin + {h} months)")
        return clean({
            "h": h, "models": models, "probability": frame(prob_frame), "current": current,
            "spread": series(spread), "recessions": rec.recession_episodes(usrec), "sahm": sahm_payload,
            "sample": {"start": base.fitted.index[0], "end": last_origin, "nobs": base.nobs},
            "notes": notes,
            "method": {"model": "probit P(USREC_{t+h}=1) = Phi(a + b spread_t)",
                       "reference": "Estrella & Mishkin (1998); pseudo-R2 of Estrella (1998)",
                       "errors": "Newey-West HAC", "sahm": "Sahm (2019), threshold 0.50pp"},
            "provenance": _prov(provs),
        })

    return _cached(("recession", h, extra), build)


# =========================================================================
# Regimes
# =========================================================================
@router.get("/regimes")
def regimes(
    market: Market,
    ticker: Annotated[str, Query(min_length=1, max_length=12)] = "SPY",
    k: Annotated[int, Query(ge=2, le=3)] = 2,
    freq: Annotated[Literal["D", "W", "M"], Query()] = "W",
    start: date | None = None,
    search_reps: Annotated[int, Query(ge=0, le=100)] = 20,
) -> dict[str, Any]:
    """Hamilton (1989) Markov-switching regimes of returns + risk-on/off panel."""
    tkr = ticker.strip().upper()

    def build() -> dict[str, Any]:
        ds = market.ohlcv(tkr, start)
        px = ds.data["adj_close"].dropna()
        provs = list(ds.provenance)
        r = rg.resample_returns(px, freq)
        ms = rg.fit_markov_switching(r, k=k, freq=freq, search_reps=search_reps)
        notes = list(ms.notes)
        ppy = rg.PERIODS_PER_YEAR[freq]
        notes.append(f"regime mean_ann / vol_ann are annualized per-period moments: mean_ann = per-period "
                     f"arithmetic mean x {ppy}, vol_ann = per-period sigma x sqrt({ppy}) ({freq} returns); "
                     "they are not compounded (geometric) returns")
        notes.append(f"price is sampled on the same {freq} grid as the regime probabilities (period-end close)")
        if any("alpaca" in p.source for p in ds.provenance):
            notes.append("Alpaca adj_close is split-adjusted only; dividends excluded")
        got, p2, missing = _fetch(market, ["VIXCLS", "DGS10", "DGS2", "BAMLH0A0HYM2"])
        provs += p2
        slope = None
        if "DGS10" in got and "DGS2" in got:
            slope = (got["DGS10"] - got["DGS2"]).dropna()
        panel = rg.risk_panel(vix=got.get("VIXCLS"), hy_oas=got.get("BAMLH0A0HYM2"), slope=slope,
                              index_prices=px)
        if missing:
            notes.append("risk panel omits: " + ", ".join(sorted(missing)))
        regimes_out = [{"regime": i, "mean_ann": float(ms.means_ann[i]), "vol_ann": float(ms.vols_ann[i]),
                        "expected_duration_periods": float(ms.expected_duration[i]),
                        "share_of_time": float((ms.smoothed.idxmax(axis=1) == f"regime_{i}").mean())}
                       for i in range(k)]
        return clean({
            "ticker": tkr, "k": k, "freq": freq, "model": ms.to_dict(), "regimes": regimes_out,
            "smoothed": frame(ms.smoothed), "filtered": frame(ms.filtered),
            # price on the SAME grid (and dates) as the regime probabilities / returns
            "returns": series(r), "price": series(rg.resample_prices(px, freq).reindex(ms.smoothed.index)),
            "risk_panel": panel, "notes": notes,
            "method": {"model": "Markov-switching mean & variance, Hamilton (1989) filter, Kim (1994) smoother",
                       "estimation": "MLE after EM + random-start search (statsmodels MarkovRegression)",
                       "search_reps": search_reps, "seed": 12345,
                       "risk_panel": "percentile ranks vs own history; slope sign; 200d trend (Faber 2007)"},
            "provenance": _prov(provs),
        })

    return _cached(("regimes", tkr, k, freq, str(start), search_reps), build)


# =========================================================================
# Bonds
# =========================================================================
class BondIn(BaseModel):
    coupon: float = Field(ge=0, le=50, description="annual coupon rate, PERCENT")
    maturity: date | None = None
    years: float | None = Field(default=None, gt=0, le=100, description="alternative to maturity")
    freq: Literal[1, 2, 4, 12] = 2
    settlement: date | None = Field(default=None, description="default: next business day (T+1)")
    price: float | None = Field(default=None, gt=0, description="CLEAN price per 100 face")
    yield_pct: float | None = Field(default=None, gt=-50, lt=100, description="YTM, percent, compounded freq")
    use_curve: bool = True
    curve_date: date | None = None
    notional: float = Field(default=1_000_000.0, gt=0)
    key_tenors: list[float] | None = Field(default=None, min_length=2, max_length=15)

    @model_validator(mode="after")
    def _check(self) -> BondIn:
        if (self.maturity is None) == (self.years is None):
            raise ValueError("give exactly one of maturity or years")
        if self.price is not None and self.yield_pct is not None:
            raise ValueError("give price or yield_pct, not both")
        if self.key_tenors is not None:
            if any(not (0 < k <= 100) for k in self.key_tenors):
                raise ValueError("key_tenors must be in (0, 100] years")
            self.key_tenors = sorted(set(float(k) for k in self.key_tenors))
        return self


@router.post("/bond")
def bond(body: BondIn, market: Market) -> dict[str, Any]:
    """Bond price/yield, duration, convexity, DV01; curve fair value, Z-spread, KRDs."""
    settle = (pd.Timestamp(body.settlement) if body.settlement is not None
              else (pd.Timestamp.today().normalize() + pd.offsets.BDay(1)))
    mat = (pd.Timestamp(body.maturity) if body.maturity is not None
           else settle + pd.DateOffset(months=int(round(12 * float(body.years or 0)))))
    b = bd.Bond(coupon=body.coupon / 100.0, maturity=mat, freq=body.freq)
    notes = ["accrued interest ACT/ACT (ICMA); yield compounded at the coupon frequency (street convention)",
             "curve pricing: continuously-compounded zeros on an ACT/365.25 time axis"]
    provs: list[Provenance] = []
    zc: cv.ZeroCurve | None = None
    curve_meta: dict[str, Any] | None = None
    if body.use_curve:
        try:
            curves, provs = _curves(market)
            target = pd.Timestamp(body.curve_date) if body.curve_date else curves.index[-1]
            cd, par = cv.curve_on(curves, target)
            zc = cv.bootstrap_par_curve(par.index.to_numpy(dtype=float), par.to_numpy(dtype=float))
            curve_meta = {"date": cd}
        except DataUnavailable as e:
            notes.append(f"curve-based analytics unavailable: {e}")
    source = "input"
    if body.price is not None:
        clean_px = float(body.price)
        ytm = bd.yield_from_price(b, settle, clean_px)
    elif body.yield_pct is not None:
        ytm = body.yield_pct / 100.0
        clean_px = bd.price_from_yield(b, settle, ytm)["clean"]
    elif zc is not None:
        clean_px = bd.curve_price(b, settle, zc)["clean"]
        ytm = bd.yield_from_price(b, settle, clean_px)
        source = "curve"
        notes.append("no price or yield given: priced at the curve-implied fair value")
    else:
        raise DataUnavailable("no price/yield given and the Treasury curve is unavailable")
    rm = bd.risk_measures(b, settle, ytm)
    cf, per, _, dates = bd.cashflows(b, settle)
    pv = cf / (1 + ytm / b.freq) ** per
    sched = pd.DataFrame({"date": dates, "t_years": per / b.freq, "cashflow": cf, "pv": pv})
    shifts = np.arange(-300, 301, 25)
    grid_y = ytm + shifts / 1e4
    prices = [bd.price_from_yield(b, settle, y)["dirty"] for y in grid_y]
    d0 = rm["dirty"]
    approx_d = d0 * (1 - rm["modified_duration"] * shifts / 1e4)
    approx_dc = approx_d + d0 * 0.5 * rm["convexity"] * (shifts / 1e4) ** 2
    scale = body.notional / b.face
    out: dict[str, Any] = {
        "bond": {"coupon_pct": body.coupon, "maturity": mat, "freq": b.freq, "settlement": settle,
                 "price_source": source},
        "clean_price": clean_px, "dirty_price": rm["dirty"], "accrued": rm["accrued"],
        "ytm_pct": 100 * ytm, "risk": {**{k: rm[k] for k in ("macaulay_duration", "modified_duration",
                                                             "convexity", "dv01")},
                                      "dv01_notional": rm["dv01"] * scale, "notional": body.notional},
        "cashflows": records(sched),
        "price_yield": {"yield_pct": (100 * grid_y).tolist(), "price": prices,
                        "duration_approx": approx_d.tolist(), "duration_convexity_approx": approx_dc.tolist()},
    }
    if zc is not None and curve_meta is not None:
        fair = bd.curve_price(b, settle, zc)
        zs = bd.z_spread(b, settle, zc, clean_px)
        eff = bd.effective_duration(b, settle, zc, zs)
        keys = body.key_tenors or list(bd.DEFAULT_KEY_TENORS)
        krd = bd.key_rate_durations(b, settle, zc, keys, zs)
        krd["krd01_notional"] = krd["krd01"] * scale
        out["curve"] = {
            **curve_meta, "fair_clean": fair["clean"], "fair_dirty": fair["dirty"],
            "rich_cheap": clean_px - fair["clean"], "z_spread_bp": 1e4 * zs, **eff,
            "key_rate_durations": records(krd), "krd_sum": float(krd["krd"].sum()),
        }
        notes.append("KRDs: 1bp tent bumps of the zero curve at key tenors (Ho 1992); they sum to the "
                     "effective duration")
    out.update({
        "notes": notes,
        "method": {"price_yield": "street convention (SIFMA Standard Formulas; Fabozzi)",
                   "ytm_solver": "Brent (1973)", "key_rate_durations": "Ho (1992)",
                   "curve": "par-bootstrapped Treasury zero curve (Hull ch. 4)"},
        "provenance": _prov(provs),
    })
    return clean(out)


# =========================================================================
# Taylor rule
# =========================================================================
@router.get("/taylor")
def taylor(
    market: Market,
    r_star: Annotated[float, Query(ge=-5, le=10, description="neutral real rate, percent (rule parameter)")]
        = dash.TAYLOR_1993["r_star"],
    pi_star: Annotated[float, Query(ge=0, le=10, description="inflation target, percent (rule parameter)")]
        = dash.TAYLOR_1993["pi_star"],
    start: date | None = None,
) -> dict[str, Any]:
    """Taylor (1993) and balanced-approach prescriptions vs the effective fed funds rate."""
    def build() -> dict[str, Any]:
        got, provs = _require(market, ["PCEPILFE", "GDPC1", "GDPPOT", "DFF"], "Taylor rule")
        df, notes = dash.taylor_rule(got["PCEPILFE"], got["GDPC1"], got["GDPPOT"], r_star, pi_star,
                                     fed_funds=got["DFF"])
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if df.empty:
            raise DataUnavailable("Taylor rule: no overlapping inflation/output-gap data in window")
        last = df.iloc[-1]
        notes += [
            "r* and pi* are policy-rule PARAMETERS (defaults = Taylor's 1993 calibration: 2 and 2), not data",
            "inflation = core PCE y/y; output gap = 100 (GDPC1 - GDPPOT) / GDPPOT (CBO potential)",
            "a data-driven r* variant is not shown: no r* estimate (e.g. Holston-Laubach-Williams) is "
            "published on FRED",
            "uses latest-vintage (revised) data, not the real-time data available to policymakers",
        ]
        return clean({
            "series": frame(df), "latest": {"date": df.index[-1], **last.to_dict()},
            "params": {"r_star": r_star, "pi_star": pi_star, "inflation_coef": 0.5,
                       "gap_coef_taylor": 0.5, "gap_coef_balanced": 1.0,
                       "defaults_are": "Taylor (1993) original calibration"},
            "notes": notes,
            "method": {"taylor_1993": "i = r* + pi + 0.5(pi - pi*) + 0.5 gap (Taylor 1993)",
                       "balanced_approach": "i = r* + pi + 0.5(pi - pi*) + 1.0 gap (Yellen 2012)"},
            "provenance": _prov(provs),
        })

    return _cached(("taylor", r_star, pi_star, str(start)), build)
