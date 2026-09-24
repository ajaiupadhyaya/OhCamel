"""/api/options -- listed-option analytics on real Cboe delayed chains and real OHLCV.

Everything is derived from the quotes: the forward and discount factor of every
expiry come from put-call parity, implied vols are our own (Black-76 on mids),
smiles are raw SVI fits with static-arbitrage diagnostics, and densities are
Breeden-Litzenberger. The only external rate is the optional FRED 3-month bill
used to prefill ``r`` in the calculator when the caller omits it.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from ...data.base import DataUnavailable
from ...data.market import MarketData, get_market
from ...options import bsm, density, realized, strategy
from ...options.chain import CONTRACT_MULTIPLIER, ChainAnalytics, analyze_chain
from ...options.parity import InsufficientQuotes
from ...options.surface import VolSurface, build_surface, svi_weights
from ...options.svi import fit_svi
from ..serialize import clean, frame, records, series

router = APIRouter(prefix="/options", tags=["options"])
Market = Annotated[MarketData, Depends(get_market)]

# Yahoo symbols for the price history of cash-settled index underlyings.
INDEX_PRICE_SYMBOL = {"SPX": "^GSPC", "XSP": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "MRUT": "^RUT",
                      "VIX": "^VIX", "DJX": "^DJI", "OEX": "^OEX", "XEO": "^OEX"}
# Cboe implied-volatility indices published on FRED, by underlying.
IMPLIED_INDEX = {"SPY": "VIXCLS", "SPX": "VIXCLS", "XSP": "VIXCLS", "^GSPC": "VIXCLS", "VOO": "VIXCLS",
                 "IVV": "VIXCLS", "QQQ": "VXNCLS", "NDX": "VXNCLS", "IWM": "RVXCLS", "RUT": "RVXCLS",
                 "GLD": "GVZCLS", "USO": "OVXCLS", "EEM": "VXEEMCLS", "XLE": "VXXLECLS", "DIA": "VXDCLS",
                 "DJX": "VXDCLS", "AAPL": "VXAPLCLS", "AMZN": "VXAZNCLS", "GOOG": "VXGOGCLS",
                 "GOOGL": "VXGOGCLS", "GS": "VXGSCLS", "IBM": "VXIBMCLS"}

REFS = {
    "bsm": "Black & Scholes (1973); Merton (1973); Black (1976); greeks per Haug (2007)",
    "iv": "Corrado & Miller (1996) initial guess; safeguarded Newton (Numerical Recipes rtsafe) + Brent (1973)",
    "parity": "Put-call parity (Stoll 1969) regression with Huber (1964) IRLS; Cboe VIX white paper forward",
    "svi": "Gatheral (2004) raw SVI; Zeliade (2009) quasi-explicit calibration; Gatheral & Jacquier (2014)",
    "mfiv": "Demeterfi, Derman, Kamal & Zou (1999); Britten-Jones & Neuberger (2000); Cboe VIX white paper",
    "bl": "Breeden & Litzenberger (1978)",
    "rv": "Parkinson (1980); Garman & Klass (1980); Rogers & Satchell (1991); Yang & Zhang (2000)",
    "cone": "Burghardt & Lane (1990)",
    "vrp": "Carr & Wu (2009); Bollerslev, Tauchen & Zhou (2009)",
}


# ------------------------------------------------------------------ cache
@dataclass
class _Bundle:
    ca: ChainAnalytics
    provenance: list[dict[str, Any]]
    created: float
    surface: VolSurface | None = None
    surface_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_CACHE: dict[tuple[str, float], _Bundle] = {}
_CACHE_LOCK = threading.Lock()
_MAX_ENTRIES = 24


def _norm(ticker: str) -> str:
    t = ticker.strip().upper().lstrip("^_")
    if not t or len(t) > 12 or not all(c.isalnum() or c in ".-" for c in t):
        raise ValueError(f"invalid ticker {ticker!r}")
    return t


def _bundle(ticker: str, market: MarketData, max_rel_spread: float = 0.5) -> _Bundle:
    t = _norm(ticker)
    key = (t, round(max_rel_spread, 4))
    ttl = float(market.settings.ttl_options_s)
    now = time.monotonic()
    with _CACHE_LOCK:
        b = _CACHE.get(key)
        if b is not None and now - b.created < ttl:
            return b
    ds = market.option_chain(t)
    try:
        ca = analyze_chain(ds.data, max_rel_spread=max_rel_spread)
    except InsufficientQuotes as e:
        raise DataUnavailable(f"{t}: {e}") from e
    b = _Bundle(ca=ca, provenance=ds.provenance_dicts(), created=now)
    with _CACHE_LOCK:
        _CACHE[key] = b
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k].created))
    return b


def _surface(b: _Bundle) -> VolSurface:
    with b.lock:
        if b.surface is None and b.surface_error is None:
            try:
                b.surface = build_surface(b.ca)
            except ValueError as e:
                b.surface_error = str(e)
    if b.surface is None:
        raise DataUnavailable(f"{b.ca.underlying}: no volatility surface ({b.surface_error})")
    return b.surface


def _header(ca: ChainAnalytics) -> dict[str, Any]:
    return {"underlying": ca.underlying, "spot": ca.spot, "as_of": ca.as_of.isoformat(),
            "exercise_style": "European" if ca.european else "American"}


def _slice_summary(ca: ChainAnalytics) -> list[dict[str, Any]]:
    cols = ["expiry", "settlement", "roots", "T", "dte", "forward", "discount", "rate", "div_yield",
            "rate_source", "rate_se", "forward_se", "parity_rmse", "parity_strikes", "n_contracts", "n_valid",
            "n_smile", "n_arb_bound", "vendor_iv_mad"]
    s = ca.slices[cols].copy()
    s["expiry_ts"] = ca.slices["expiry_ts"].map(lambda x: pd.Timestamp(x).isoformat())
    return records(s, index_name="slice")


# ------------------------------------------------------------------ chain endpoints
@router.get("/expiries/{ticker}")
def expiries(ticker: str, market: Market) -> dict[str, Any]:
    """Listed expiries with implied forward, rate and dividend yield per expiry."""
    b = _bundle(ticker, market)
    ca = b.ca
    return clean({
        **_header(ca), "expiries": _slice_summary(ca),
        "method": {"forward": "put-call parity regression per expiry", "day_count": "ACT/365 to settlement",
                   "reference": REFS["parity"]},
        "notes": ca.notes, "errors": ca.errors, "provenance": b.provenance,
    })


QUOTE_COLS = ["contract", "root", "strike", "type", "bid", "ask", "mid", "last", "volume", "open_interest",
              "k", "otm", "valid", "wide", "zero_bid", "crossed", "no_quote", "stale", "arb_bound", "use_smile",
              "iv", "iv_bid", "iv_ask", "vendor_iv", "iv_minus_vendor", "model_price", "delta", "gamma", "vega",
              "theta_day", "rho", "vanna", "volga", "charm_day"]


@router.get("/chain/{ticker}")
def chain(
    ticker: str, market: Market, expiry: str | None = None,
    max_rel_spread: Annotated[float, Query(gt=0.01, le=5.0)] = 0.5,
) -> dict[str, Any]:
    """Cleaned quotes for one expiry: our IV (mid/bid/ask), vendor IV, greeks, flags,
    implied F/r/q, and that expiry's SVI smile."""
    b = _bundle(ticker, market, max_rel_spread)
    ca = b.ca
    sid = ca.resolve(expiry)
    s = ca.slices.loc[sid]
    q = ca.slice_quotes(sid)
    smile = None
    atm_iv = None
    sm = q[q["use_smile"]]
    notes = list(ca.notes)
    try:
        T = float(s["T"])
        fit = fit_svi(sm["k"].to_numpy(), sm["iv"].to_numpy(), T,
                      weights=svi_weights(T, sm["iv_bid"].to_numpy(), sm["iv_ask"].to_numpy()))
        atm_iv = float(np.sqrt(fit.params.w(0.0) / T))
        kk = np.linspace(fit.k_min, fit.k_max, 121)
        smile = {"fit": fit.to_dict(), "k": kk, "strike": float(s["forward"]) * np.exp(kk),
                 "iv": fit.params.implied_vol(kk, T)}
    except ValueError as e:
        notes.append(f"SVI smile not fitted for {sid}: {e}")
    greeks_units = {"delta": "per $1 of spot", "gamma": "per $1 of spot", "vega": "per 1.00 of vol",
                    "theta_day": "per calendar day", "rho": "per 1.00 of rate", "vanna": "d delta / d vol",
                    "volga": "d vega / d vol", "charm_day": "d delta per calendar day",
                    "per": "one option on one unit of the underlying (x multiplier for a contract)"}
    return clean({
        **_header(ca), "expiry": sid,
        "slice": {k: s[k] for k in ("expiry", "settlement", "T", "dte", "forward", "discount", "rate",
                                    "div_yield", "rate_source", "rate_se", "parity_rmse", "n_contracts",
                                    "n_valid", "n_smile", "vendor_iv_mad")}
                  | {"atm_iv": atm_iv, "atm_iv_source": "SVI fit at k = ln(K/F) = 0" if atm_iv is not None else None},
        "expiries": list(ca.slices.index),
        "quotes": records(q[QUOTE_COLS].reset_index(drop=True)),
        "smile": smile,
        "multiplier": CONTRACT_MULTIPLIER, "greeks_units": greeks_units,
        "method": {"iv": "Black-76 on the mid with parity-implied F and D", "iv_solver": REFS["iv"],
                   "greeks": REFS["bsm"], "otm_rule": "puts for K < F, calls for K >= F",
                   "filters": ca.params, "reference": REFS["parity"]},
        "notes": notes, "provenance": b.provenance,
    })


@router.get("/surface/{ticker}")
def surface(ticker: str, market: Market) -> dict[str, Any]:
    """SVI smile per expiry with diagnostics, 3-D grid, term structure, RR/BF, model-free vol, implied moves."""
    b = _bundle(ticker, market)
    ca = b.ca
    vs = _surface(b)
    smiles = []
    for sid, f in vs.fits.items():
        q = ca.slice_quotes(sid)
        sm = q[q["use_smile"]]
        kk = np.linspace(f.k_min, f.k_max, 81)
        smiles.append({"slice": sid, "T": f.T, "dte": f.T * 365.0, "fit": f.to_dict(),
                       "market": {"k": sm["k"].to_numpy(), "iv": sm["iv"].to_numpy(),
                                  "iv_bid": sm["iv_bid"].to_numpy(), "iv_ask": sm["iv_ask"].to_numpy(),
                                  "strike": sm["strike"].to_numpy(), "type": sm["type"].tolist(),
                                  "vendor_iv": sm["vendor_iv"].to_numpy()},
                       "curve": {"k": kk, "iv": f.params.implied_vol(kk, f.T)}})
    return clean({
        **_header(ca),
        "term_structure": records(vs.term, index_name="slice"),
        "smiles": smiles,
        "grid": {"k": vs.grid_k, "moneyness": np.exp(vs.grid_k), "T": vs.grid_T, "days": vs.grid_T * 365.0,
                 "iv": vs.grid_iv},
        "calendar": vs.calendar,
        "vix_style_30d": vs.vix_style,
        "atm_30d": vs.atm_30d,
        "expiries": _slice_summary(ca),
        "method": {"smile": "raw SVI per expiry, weighted by inverse bid-ask total-variance spread",
                   "calibration": "quasi-explicit: exact inner box-QP over (a, c, d), outer grid + Nelder-Mead",
                   "arbitrage": "Durrleman g(k) >= 0 (butterfly); w(k,T) non-decreasing in T (calendar)",
                   "interpolation": "total variance linear in T at fixed k = ln(K/F); no extrapolation",
                   "rr_bf": "spot-delta 25/10 RR = iv_call - iv_put; BF = mean - ATMF vol",
                   "model_free": REFS["mfiv"], "reference": REFS["svi"]},
        "notes": [*ca.notes, *vs.notes,
                  "model-free variance discounts with the parity-implied rate (Cboe uses Treasury CMT rates)"],
        "errors": {**ca.errors, **vs.errors},
        "provenance": b.provenance,
    })


#: Move sizes (fractions of spot) shared by ``prob_below`` (at 1 -/+ x) and ``prob_move``.
DENSITY_MOVES = (0.025, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30)


def trading_days_between(start: pd.Timestamp, end: Any) -> int:
    """Weekdays in ``(start, end]`` (``numpy.busday_count``): a trading-day
    horizon for annualizing on a 252-day basis. Exchange holidays are not
    removed (at most ~9 a year), which is stated wherever this is used."""
    def day(x: Any) -> pd.Timestamp:
        t = pd.Timestamp(x)
        return (t.tz_localize(None) if t.tzinfo else t).normalize()

    a, b = day(start), day(end)
    if b <= a:
        return 0
    return int(np.busday_count((a + pd.Timedelta(days=1)).date(), (b + pd.Timedelta(days=1)).date()))


@router.get("/density/{ticker}")
def rn_density(ticker: str, market: Market, expiry: str | None = None) -> dict[str, Any]:
    """Breeden-Litzenberger risk-neutral density of one expiry from its SVI smile."""
    b = _bundle(ticker, market)
    ca = b.ca
    vs = _surface(b)
    sid = ca.resolve(expiry)
    if sid not in vs.fits:
        raise DataUnavailable(f"{ca.underlying} {sid}: no fitted smile "
                              f"({vs.errors.get(sid, 'expiry excluded from the surface')}); "
                              f"fitted: {', '.join(vs.fits)}")
    f = vs.fits[sid]
    s = ca.slices.loc[sid]
    F, D, T = float(s["forward"]), float(s["discount"]), float(s["T"])
    d = density.breeden_litzenberger(F, D, T, f.params.w)
    atm = float(np.sqrt(f.params.w(0.0) / T))
    # same strike grid as the SVI density so `lognormal_density` lines up with `strikes`
    ln = density.breeden_litzenberger(F, D, T, lambda k: np.full_like(np.asarray(k, float), atm * atm * T),
                                      k_range=d.k_range)
    step = max(1, len(d.strikes) // 500)
    sl = slice(None, None, step)
    probs = list(density.DEFAULT_QUANTILES)
    # ONE move grid for both tables, so P(fall > x), P(rise > x) and P(|move| > x) line up row by row
    moves = list(DENSITY_MOVES)
    mny = [*(1 - m for m in reversed(moves)), 1.0, *(1 + m for m in moves)]
    notes = list(ca.notes)
    if abs(d.integral - 1) > 0.01:
        notes.append(f"density integrates to {d.integral:.4f} (not ~1): tail mass outside the grid or arbitrage")
    if d.negative_mass > 1e-4:
        notes.append(f"negative density mass {d.negative_mass:.2e}: the smile violates the butterfly condition")
    notes.append("risk-neutral (Q) probabilities embed risk premia; they are not real-world forecasts")
    return clean({
        **_header(ca), "expiry": sid, "T": T, "dte": T * 365.0, "forward": F, "discount": D,
        "strikes": d.strikes[sl], "density": d.density[sl], "cdf": d.cdf[sl],
        "lognormal_density": ln.density[sl], "atm_iv": atm,
        "checks": {"integral": d.integral, "mean": d.mean, "forward": F, "mean_minus_forward": d.mean - F,
                   "negative_mass": d.negative_mass, "butterfly": f.butterfly},
        "moments": d.moments(), "lognormal_moments": ln.moments(),
        "quantiles": [{"p": p, "level": float(v), "return": float(v / ca.spot - 1)}
                      for p, v in zip(probs, d.quantiles(probs), strict=True)],
        "prob_below": [{"level": float(ca.spot * m), "moneyness": float(m), "p": float(d.prob_below(ca.spot * m))}
                       for m in mny],
        "prob_move": [{"move": m, "p": float(d.prob_move(ca.spot, m)), "p_lognormal": float(ln.prob_move(ca.spot, m))}
                      for m in moves],
        "move_grid": moves,
        "method": {"density": "q(K) = (1/D) d2C/dK2 on a uniform strike grid, C from Black-76 at SVI vols",
                   "cdf": "P(S_T <= K) = 1 + (1/D) dC/dK", "reference": REFS["bl"]},
        "notes": notes, "provenance": b.provenance,
    })


# ------------------------------------------------------------------ realized
@router.get("/realized/{ticker}")
def realized_vol(
    ticker: str, market: Market,
    window: Annotated[int, Query(ge=5, le=252)] = 21,
    estimator: Literal["close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang"] = "yang_zhang",
    years: Annotated[float, Query(gt=0.5, le=30)] = 10.0,
) -> dict[str, Any]:
    """Realized-vol estimators, volatility cone, and the volatility risk premium."""
    t = _norm(ticker)
    sym = INDEX_PRICE_SYMBOL.get(t, t)
    ds = market.ohlcv(sym)
    prov = ds.provenance_dicts()
    ohlc = realized.adjusted_ohlc(ds.data)
    if ohlc.empty:
        raise DataUnavailable(f"{t}: no daily bars with consistent positive OHLC prices")
    cutoff = ohlc.index[-1] - pd.DateOffset(days=int(years * 365.25))
    ohlc = ohlc[ohlc.index >= cutoff - pd.DateOffset(days=400)]  # warm-up for the 252d cone window
    if len(ohlc) < window + 2:
        raise DataUnavailable(f"{t}: only {len(ohlc)} daily bars")
    notes: list[str] = []
    if any("alpaca" in str(p.get("source", "")) for p in prov):
        notes.append("Alpaca adj_close is split-adjusted only; close-to-close returns exclude dividends "
                     "(ex-dividend drops add a little variance)")
    est = realized.all_estimators(ohlc, window)
    est = est[est.index >= cutoff]
    current = {}
    for w in (10, 21, 63, 126, 252):
        current[str(w)] = {e: float(realized.rolling_vol(ohlc, w, e).iloc[-1]) for e in realized.ESTIMATORS}
    cone = realized.vol_cone(ohlc[ohlc.index >= cutoff], estimator=estimator)

    implied_term = None
    vrp_now = None
    iv30 = None
    mf30 = None
    try:
        b = _bundle(t, market)
        vs = _surface(b)
        prov = prov + b.provenance
        term = vs.term
        ets = b.ca.slices["expiry_ts"].reindex(term.index)
        implied_term = {"dte": term["dte"].to_numpy(),
                        "trading_days": [trading_days_between(b.ca.as_of, e) if pd.notna(e) else None for e in ets],
                        "atm_iv": term.get("atm_iv", pd.Series(dtype=float)).to_numpy()}
        notes.append("implied_term: dte is calendar days (ACT/365); trading_days counts weekdays to expiry "
                     "(exchange holidays not removed)")
        if vs.vix_style:
            mf30 = vs.vix_style.get("index", float("nan")) / 100.0
        if vs.atm_30d:
            iv30 = vs.atm_30d["iv"]
            if vs.atm_30d.get("extrapolated"):
                notes.append("30-day ATM implied vol extrapolated in T from the listed expiries")
    except (DataUnavailable, ValueError) as e:
        notes.append(f"implied vol from the option chain unavailable: {e}")
    rv21 = float(realized.rolling_vol(ohlc, 21, estimator).iloc[-1])
    rv21_cc = float(realized.rolling_vol(ohlc, 21, "close_to_close").iloc[-1])
    if iv30 is not None:
        vrp_now = {**realized.vrp(iv30, rv21), "estimator": estimator, "realized_window": 21,
                   "close_to_close": realized.vrp(iv30, rv21_cc),
                   "model_free": realized.vrp(mf30, rv21_cc) if mf30 is not None and math.isfinite(mf30) else None,
                   "implied_source": "30-day ATM-forward vol from the SVI surface"}

    history = None
    idx_id = IMPLIED_INDEX.get(t)
    if idx_id:
        try:
            fd = market.fred([idx_id], cutoff.date())
            iv_idx = fd.data[idx_id].dropna() / 100.0
            h = realized.vrp_history(iv_idx, ohlc, 21)
            h = h[h.index >= cutoff]
            prov = prov + fd.provenance_dicts()
            history = {"index": idx_id, "frame": frame(h),
                       "mean_premium_forward": float(h["premium_forward"].mean()),
                       "share_positive_forward": float((h["premium_forward"].dropna() > 0).mean()),
                       "mean_premium_trailing": float(h["premium_trailing"].mean())}
            if vrp_now is None and len(h):
                last = h.iloc[-1]
                vrp_now = {**realized.vrp(float(last["implied"]), rv21_cc), "estimator": "close_to_close",
                           "realized_window": 21, "as_of": h.index[-1],
                           "implied_source": f"{idx_id} (Cboe 30-day model-free index, FRED)"}
            notes.append(f"{idx_id} is a model-free 30-day variance index; forward premium uses realized vol "
                         "over the NEXT 21 sessions (ex post)")
        except DataUnavailable as e:
            notes.append(f"implied-vol index {idx_id} unavailable: {e}")
    if len(ohlc[ohlc.index >= cutoff]) < 252:
        notes.append("fewer than 252 sessions: the 252-day cone horizon is missing or thin")
    return clean({
        "ticker": t, "price_symbol": sym, "as_of": ohlc.index[-1], "window": window, "estimator": estimator,
        "rolling": frame(est), "current": current,
        "cone": records(cone, index_name="horizon") if not cone.empty else [],
        "implied_term": implied_term, "vrp": vrp_now, "vrp_history": history,
        "close": series(ohlc["close"][ohlc.index >= cutoff]),
        "method": {"estimators": list(realized.ESTIMATORS), "annualization": 252,
                   "cone": "overlapping rolling windows over the selected history", "reference": REFS["rv"],
                   "cone_reference": REFS["cone"], "vrp_reference": REFS["vrp"]},
        "notes": notes, "provenance": prov,
    })


# ------------------------------------------------------------------ strategy
class LegIn(BaseModel):
    kind: Literal["option", "stock"] = "option"
    qty: float
    contract: str | None = Field(default=None, max_length=32)
    expiry: str | None = None
    strike: float | None = Field(default=None, gt=0)
    type: Literal["C", "P", "c", "p", "call", "put"] | None = None

    @field_validator("qty")
    @classmethod
    def _qty(cls, v: float) -> float:
        if v == 0 or not math.isfinite(v) or abs(v) > 1e6:
            raise ValueError("qty must be a non-zero finite number")
        return v

    @model_validator(mode="after")
    def _spec(self) -> LegIn:
        if self.kind == "option" and not self.contract and None in (self.expiry, self.strike, self.type):
            raise ValueError("option legs need a contract symbol or expiry + strike + type")
        return self


class StrategyIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    legs: list[LegIn] = Field(min_length=1, max_length=12)
    n_dates: int = Field(default=4, ge=1, le=10)


@router.post("/strategy")
def strategy_endpoint(body: StrategyIn, market: Market) -> dict[str, Any]:
    """Price and analyze a multi-leg position from live quotes."""
    b = _bundle(body.ticker, market)
    ca = b.ca
    fits: dict[str, Any] = {}
    notes = list(ca.notes)
    try:
        fits = {sid: f.params for sid, f in _surface(b).fits.items()}
    except DataUnavailable as e:
        notes.append(str(e))
    specs = [lg.model_dump() for lg in body.legs]
    legs = strategy.resolve_legs(ca, specs, fits)
    out = strategy.analyze_position(ca, legs, fits, n_dates=body.n_dates)
    notes.extend(out.pop("notes"))
    notes.append("values before expiry assume sticky strike (each leg keeps today's IV) and the expiry's "
                 "parity-implied r and q; stock legs ignore dividends")
    notes.append(f"contract multiplier {CONTRACT_MULTIPLIER:g} (standard equity/index options)")
    return clean({
        **_header(ca), **out,
        "method": {"valuation": "BSM per leg (own IV, own expiry r, q)", "pop": "risk-neutral probability via "
                   "Breeden-Litzenberger density of the first expiry", "reference": f"{REFS['bsm']}; {REFS['bl']}"},
        "notes": notes, "provenance": b.provenance,
    })


# ------------------------------------------------------------------ calculator
BILL_TENOR_YEARS = 0.25  # DGS3MO is the 3-month constant-maturity point


def bill_yield_to_continuous(y: float, tau: float) -> float:
    """Continuously-compounded rate equivalent to a Treasury-bill bond-equivalent yield.

    For bills with at most a half-year to maturity the bond-equivalent (investment)
    yield is *simple* interest on an actual/365 basis, ``P = 1 / (1 + y tau)``
    (Stigum & Crescenzi 2007, *Stigum's Money Market*, ch. 8; US Treasury, "Interest
    rate statistics -- yield curve methodology"), so ``e^{-r tau} = P`` gives
    ``r = ln(1 + y tau) / tau``. (The semi-annual ``2 ln(1 + y/2)`` applies to coupon
    securities, not to a 3-month bill.)
    """
    if tau <= 0:
        raise ValueError("tau must be positive")
    return math.log1p(y * tau) / tau


class PriceIn(BaseModel):
    S: float = Field(gt=0)
    K: float = Field(gt=0)
    T: float = Field(gt=0, le=50, description="years (ACT/365)")
    sigma: float | None = Field(default=None, gt=0, le=20)
    price: float | None = Field(default=None, gt=0, description="option price to invert for implied vol")
    r: float | None = Field(default=None, ge=-0.2, le=1.0, description="cont. compounded decimal; omit for FRED DGS3MO")
    q: float = Field(default=0.0, ge=-0.5, le=1.0, description="cont. dividend/borrow yield, decimal")
    type: Literal["C", "P"] = "C"

    @model_validator(mode="after")
    def _one(self) -> PriceIn:
        if self.sigma is None and self.price is None:
            raise ValueError("give sigma (to price) or price (to imply sigma)")
        return self


@router.post("/price")
def price(body: PriceIn, market: Market) -> dict[str, Any]:
    """BSM / Black-76 calculator: price, all greeks, implied vol, parity check, P&L curves."""
    notes: list[str] = []
    prov: list[dict[str, Any]] = []
    r = body.r
    if r is None:
        ds = market.fred(["DGS3MO"])
        s = ds.data["DGS3MO"].dropna()
        if s.empty:
            raise DataUnavailable("FRED DGS3MO has no observations")
        y = float(s.iloc[-1]) / 100.0
        r = bill_yield_to_continuous(y, BILL_TENOR_YEARS)
        prov = ds.provenance_dicts()
        notes.append(f"r prefilled from FRED DGS3MO {y:.4%} (bond-equivalent, {s.index[-1].date()}) converted "
                     f"to continuous: r = ln(1 + y tau) / tau with tau = {BILL_TENOR_YEARS:g} = {r:.4%} "
                     "(a bill's bond-equivalent yield is simple interest on an actual/365 basis)")
    S, K, T, q = body.S, body.K, body.T, body.q
    F, D = (float(x) for x in bsm.forward_and_discount(S, T, r, q))
    iv_out = None
    sigma = body.sigma
    if body.price is not None:
        iv_out = float(bsm.implied_vol(body.price, S, K, T, r, q, body.type))
        if not math.isfinite(iv_out):
            lo = D * max((F - K) if body.type == "C" else (K - F), 0.0)
            hi = D * (F if body.type == "C" else K)
            raise ValueError(f"price {body.price} is outside the no-arbitrage bounds ({lo:.4f}, {hi:.4f})")
        if sigma is None:
            sigma = iv_out
    assert sigma is not None
    g = {c: {k: float(v) for k, v in bsm.bsm_greeks(S, K, T, sigma, r, q, c).items()} for c in ("C", "P")}
    parity_gap = g["C"]["price"] - g["P"]["price"] - (S * math.exp(-q * T) - K * math.exp(-r * T))
    sd = sigma * math.sqrt(T)
    grid = np.linspace(max(S * math.exp(-3.5 * sd), 1e-6), S * math.exp(3.5 * sd), 161)
    today = bsm.bsm_price(grid, K, T, sigma, r, q, body.type)
    expiry_payoff = np.maximum((grid - K) if body.type == "C" else (K - grid), 0.0)
    gg = bsm.bsm_greeks(grid, K, T, sigma, r, q, body.type)
    if body.q == 0.0:
        notes.append("q = 0 (no dividend/borrow yield) -- set q for dividend payers")
    return clean({
        "inputs": {"S": S, "K": K, "T": T, "sigma": sigma, "r": r, "q": q, "type": body.type, "price": body.price},
        "forward": F, "discount": D, "implied_vol": iv_out,
        "result": g[body.type], "call": g["C"], "put": g["P"],
        "parity_check": {"C_minus_P": g["C"]["price"] - g["P"]["price"],
                         "S_e^-qT_minus_K_e^-rT": S * math.exp(-q * T) - K * math.exp(-r * T), "gap": parity_gap},
        "curves": {"spot": grid, "value_today": today, "payoff_at_expiry": expiry_payoff,
                   "delta": gg["delta"], "gamma": gg["gamma"], "vega": gg["vega"], "theta_day": gg["theta_day"]},
        "method": {"model": "Black-Scholes-Merton with continuous yield q (== Black-76 on F = S e^{(r-q)T})",
                   "units": "vega/rho per 1.00 (divide by 100 for per point); theta per year and per calendar day",
                   "reference": REFS["bsm"], "iv_solver": REFS["iv"]},
        "notes": notes, "provenance": prov,
    })
