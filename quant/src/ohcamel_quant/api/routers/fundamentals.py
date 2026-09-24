"""/api/fundamentals -- company fundamentals and valuation from SEC XBRL filings.

* ``GET  /fundamentals/{ticker}/profile``    -- identity, live price, market cap, EV,
  multiples, CAPM beta, 52-week range.
* ``GET  /fundamentals/{ticker}/statements`` -- income statement, balance sheet, cash
  flow (``period=annual|quarterly|ttm``) with YoY growth.
* ``GET  /fundamentals/{ticker}/ratios``     -- ratio history (annual and rolling TTM),
  latest snapshot, CAGRs.
* ``GET  /fundamentals/{ticker}/scores``     -- Piotroski F, Altman Z / Z'', Beneish M,
  Sloan accruals, Ohlson O (itemized).
* ``POST /fundamentals/{ticker}/dcf``        -- two-stage FCFF DCF with data-derived,
  overridable inputs, WACC x g sensitivity and reverse DCF.

Data: ``MarketData.company_facts`` (SEC EDGAR), ``quotes``/``ohlcv`` (prices),
``fred`` (DGS10, T10YIE, GDPDEF), Ken French (market risk premium).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from ...data.base import DataUnavailable
from ...data.market import MarketData, get_market
from ...fundamentals import dcf as dcfm
from ...fundamentals import ratios as rt
from ...fundamentals import scores as sc
from ...fundamentals import statements as st
from ..serialize import clean, frame

router = APIRouter(prefix="/fundamentals", tags=["fundamentals"])
Market = Annotated[MarketData, Depends(get_market)]
Ticker = Annotated[str, Path(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$")]

BETA_BENCHMARK = "SPY"
BETA_WINDOW_MONTHS = 60
FACTS_TTL_S = 3600.0
QUOTE_TTL_S = 60.0
MODEL_TTL_S = 900.0

# ------------------------------------------------------------------ cache
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_LOCK = threading.Lock()
_MAX_ENTRIES = 256


def _cached(key: str, ttl_s: float, fn: Callable[[], Any]) -> Any:
    """In-process TTL/LRU cache (errors are not cached)."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl_s:
            _CACHE.move_to_end(key)
            return hit[1]
    val = fn()
    with _LOCK:
        _CACHE[key] = (now, val)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return val


def _norm(ticker: str) -> str:
    return ticker.strip().upper()


# ------------------------------------------------------------------ loaders
def _facts(market: MarketData, ticker: str) -> tuple[dict[str, Any], list[dict]]:
    def load() -> tuple[dict[str, Any], list[dict]]:
        ds = market.company_facts(ticker)
        d = dict(ds.data)
        d["annual"] = st.ensure_columns(d["annual"])
        d["quarterly"] = st.ensure_columns(d["quarterly"])
        if d["annual"].empty:
            raise DataUnavailable(f"sec: no annual statements for {ticker}")
        return d, ds.provenance_dicts()

    return _cached(f"facts:{ticker}", FACTS_TTL_S, load)


def _quote(market: MarketData, ticker: str) -> tuple[float, Any, list[dict]]:
    def load() -> tuple[float, Any, list[dict]]:
        ds = market.quotes([ticker])
        q = ds.data
        if ticker not in q.index or not np.isfinite(float(q.loc[ticker, "price"])):
            raise DataUnavailable(f"no live price for {ticker}")
        return float(q.loc[ticker, "price"]), q.loc[ticker, "as_of"], ds.provenance_dicts()

    return _cached(f"quote:{ticker}", QUOTE_TTL_S, load)


#: A quote older than this many sessions (weekdays) is flagged in the notes.
STALE_PRICE_SESSIONS = 5


def _fred_latest(market: MarketData, sid: str, lookback_days: int = 45
                 ) -> tuple[float, str, list[dict], list[str]]:
    """Latest FRED print (percent -> decimal), its date, provenance and notes.

    Looks back ``lookback_days`` first; if the series has not printed in that
    window (a publication lag, a FRED outage, a stale cache) it falls back to
    the last available observation of the past 5 years and says so with its
    date -- a real, dated print, never a substitute value."""
    def load() -> tuple[float, str, list[dict], list[str]]:
        ds = market.fred([sid], start=date.today() - timedelta(days=lookback_days))
        s = ds.data[sid].dropna()
        notes: list[str] = []
        if s.empty:
            ds = market.fred([sid], start=date.today() - timedelta(days=5 * 366))
            s = ds.data[sid].dropna()
            if s.empty:
                raise DataUnavailable(f"fred: no {sid} observation in the last 5 years")
            d = s.index[-1].date()
            notes.append(f"FRED {sid} has no print in the last {lookback_days} days; using the last available "
                         f"observation, {float(s.iloc[-1]):.2f}% dated {d.isoformat()} "
                         f"({(date.today() - d).days} days old)")
        return float(s.iloc[-1]) / 100.0, s.index[-1].date().isoformat(), ds.provenance_dicts(), notes

    return _cached(f"fred:{sid}", 3600.0, load)


def _stale_price_notes(as_of: Any, today: date | None = None) -> list[str]:
    """A note when the quote is more than :data:`STALE_PRICE_SESSIONS` weekday
    sessions old (exchange holidays not removed)."""
    try:
        ts = pd.Timestamp(as_of)
    except (TypeError, ValueError):
        return []
    if pd.isna(ts):
        return []
    d = (ts.tz_convert(None) if ts.tzinfo else ts).date()
    today = today or date.today()
    n = int(np.busday_count(d, today)) if d < today else 0
    if n > STALE_PRICE_SESSIONS:
        return [f"the price used ({d.isoformat()}) is {n} sessions old (> {STALE_PRICE_SESSIONS}): market cap, "
                "multiples and the valuation gap are as of that date"]
    return []


def _monthly_rf(market: MarketData, start: date) -> tuple[pd.Series | None, list[dict], list[str]]:
    try:
        ds = market.risk_free_daily(start, None)
    except DataUnavailable as e:
        return None, [], [f"risk-free rate unavailable ({e}); beta estimated on raw rather than excess returns"]
    return dcfm.monthly_from_daily(ds.data), ds.provenance_dicts(), []


def _beta(market: MarketData, ticker: str) -> tuple[dict[str, Any], list[dict], list[str]]:
    def load() -> tuple[dict[str, Any], list[dict], list[str]]:
        start = date.today() - timedelta(days=int(365.25 * (BETA_WINDOW_MONTHS / 12 + 1)))
        a = market.ohlcv(ticker, start)
        m = market.ohlcv(BETA_BENCHMARK, start)
        ra, rm = dcfm.monthly_returns(a.data["adj_close"]), dcfm.monthly_returns(m.data["adj_close"])
        rf, rf_prov, notes = _monthly_rf(market, start)
        if rf is not None:
            rf = rf.reindex(ra.index.union(rm.index))
        b = dcfm.estimate_beta(ra, rm, rf, BETA_WINDOW_MONTHS)
        b["benchmark"] = BETA_BENCHMARK
        if b["n_months"] < BETA_WINDOW_MONTHS:
            notes.append(f"beta uses {b['n_months']} months (< {BETA_WINDOW_MONTHS}): limited price history")
        return b, a.provenance_dicts() + m.provenance_dicts() + rf_prov, notes

    return _cached(f"beta:{ticker}", 6 * 3600.0, load)


def _erp(market: MarketData) -> tuple[dict[str, Any], list[dict], list[str]]:
    """Market risk premium: Ken French monthly Mkt-RF (full sample, arithmetic,
    annualized); fallback daily French factors compounded to months; last
    resort the S&P 500 ETF's realized monthly excess return over its history."""
    def load() -> tuple[dict[str, Any], list[dict], list[str]]:
        errors: list[str] = []
        try:
            ds = market.french_dataset("F-F_Research_Data_Factors", 0)
            x = ds.data["Mkt-RF"]
            if (x.index.to_series().diff().dt.days.median() or 0) < 25:
                x = dcfm.monthly_from_daily(x)
            r = dcfm.equity_risk_premium(x)
            r["source"] = "Ken French Mkt-RF (monthly, value-weighted CRSP market minus 1-month T-bill)"
            return r, ds.provenance_dicts(), []
        except (DataUnavailable, KeyError, ValueError) as e:
            errors.append(f"french monthly: {e}")
        try:
            ds = market.ff_factors("ff3", momentum=False)
            mkt = ds.data["Mkt-RF"] + ds.data["RF"]
            ex = dcfm.monthly_from_daily(mkt) - dcfm.monthly_from_daily(ds.data["RF"])
            r = dcfm.equity_risk_premium(ex)
            r["source"] = "Ken French daily Mkt-RF compounded to months"
            return r, ds.provenance_dicts(), []
        except (DataUnavailable, KeyError, ValueError) as e:
            errors.append(f"french daily: {e}")
        spy = market.ohlcv(BETA_BENCHMARK)
        rm = dcfm.monthly_returns(spy.data["adj_close"])
        rf, rf_prov, rf_notes = _monthly_rf(market, spy.data.index[0].date())
        if rf is None:
            raise DataUnavailable("equity risk premium unavailable: " + "; ".join(errors + rf_notes))
        ex = (rm - rf.reindex(rm.index)).dropna()
        r = dcfm.equity_risk_premium(ex)
        r["source"] = f"{BETA_BENCHMARK} realized monthly return minus T-bill over its history"
        return r, spy.provenance_dicts() + rf_prov, [
            "Ken French data unavailable (" + "; ".join(errors) + f"); ERP is the realized {BETA_BENCHMARK} "
            "excess return over its (much shorter) history -- a noisier estimate"]

    return _cached("erp", 24 * 3600.0, load)


def _gdp_deflator_1968(market: MarketData, at: pd.Timestamp) -> tuple[float | None, list[dict], list[str]]:
    """Ohlson's (1980) price-level index for the balance sheet dated ``at``: the
    GDP implicit price deflator's mean over the calendar year before ``at``'s
    year, rebased to 1968 = 100 (:func:`scores.price_level_index_1968`)."""
    try:
        ds = _cached("fred:GDPDEF", 24 * 3600.0, lambda: market.fred(["GDPDEF"], start=date(1968, 1, 1)))
    except DataUnavailable as e:
        return None, [], [f"Ohlson O-score: GDP deflator unavailable ({e})"]
    v = sc.price_level_index_1968(ds.data["GDPDEF"], at)
    if v is None:
        return None, ds.provenance_dicts(), [f"Ohlson O-score: GDP deflator lacks 1968 or {at.year - 1}"]
    return v, ds.provenance_dicts(), []


def _fresh_ttm(facts: dict[str, Any]) -> tuple[pd.Timestamp, pd.Series] | None:
    """The latest complete TTM window, or None when it ends before the latest
    fiscal year (a gap in the quarterly data would otherwise make a stale TTM
    window win over a newer 10-K)."""
    ttm = st.latest_ttm(facts["quarterly"])
    if ttm is None or ttm[0] < facts["annual"].index[-1]:
        return None
    return ttm


def _fundamentals_row(facts: dict[str, Any]) -> tuple[pd.Series, str, str | None]:
    """The row multiples are computed on: TTM when revenue and net income are
    available for four consecutive quarters ending no earlier than the latest
    fiscal year, else the latest fiscal year."""
    ttm = _fresh_ttm(facts)
    if ttm is not None:
        end, row = ttm
        if np.isfinite(row["revenue"]) and np.isfinite(row["net_income"]):
            return row, "ttm", end.date().isoformat()
    a = facts["annual"]
    return a.iloc[-1], "fy", a.index[-1].date().isoformat()


def _shares(facts: dict[str, Any]) -> float:
    so = facts.get("shares_outstanding")
    if so is None or not np.isfinite(float(so)) or float(so) <= 0:
        raise DataUnavailable(f"sec: shares outstanding (dei:EntityCommonStockSharesOutstanding) not reported "
                              f"for {facts.get('ticker')}")
    return float(so)


def _identity(facts: dict[str, Any]) -> dict[str, Any]:
    q = facts["quarterly"]
    return {
        "ticker": facts.get("ticker"), "name": facts.get("name"), "cik": facts.get("cik"),
        "latest_fiscal_year_end": facts["annual"].index[-1],
        "latest_quarter_end": q.index[-1] if len(q) else None,
        "shares_outstanding": facts.get("shares_outstanding"),
        "shares_outstanding_as_of": facts.get("shares_outstanding_as_of"),
    }


def _facts_notes(facts: dict[str, Any]) -> list[str]:
    return list(facts.get("notes") or [])


# ------------------------------------------------------------------ endpoints
@router.get("/{ticker}/profile")
def profile(ticker: Ticker, market: Market) -> dict[str, Any]:
    """Company identity, live price, market cap, enterprise value, valuation
    multiples, CAPM beta (60 monthly excess returns vs SPY) and 52-week range."""
    t = _norm(ticker)
    return _cached(f"profile:{t}", QUOTE_TTL_S, lambda: _profile(t, market))


def _profile(t: str, market: MarketData) -> dict[str, Any]:
    facts, prov = _facts(market, t)
    price, as_of, qprov = _quote(market, t)
    shares = _shares(facts)
    row, basis, basis_end = _fundamentals_row(facts)
    mult = rt.market_multiples(price, shares, row)
    notes = _facts_notes(facts) + _stale_price_notes(as_of)
    notes.append(f"multiples use {'trailing-twelve-month' if basis == 'ttm' else 'latest fiscal-year'} "
                 f"fundamentals (period ending {basis_end}); balances at that period end; market cap = live "
                 "price x SEC cover-page shares outstanding")
    notes.append("EV = market cap + book total debt - cash & equivalents; it excludes preferred stock, "
                 "minority interest and operating leases, and does not net short-term investments")
    beta, bprov = None, []
    try:
        beta, bprov, bnotes = _beta(market, t)
        notes += bnotes
    except (DataUnavailable, ValueError) as e:
        notes.append(f"beta unavailable: {e}")
    rng = None
    try:
        px = market.ohlcv(t, date.today() - timedelta(days=365))
        d = px.data
        rng = {"high": float(d["high"].max()), "low": float(d["low"].min()),
               "high_date": d["high"].idxmax(), "low_date": d["low"].idxmin(),
               "position": (price - float(d["low"].min())) / (float(d["high"].max()) - float(d["low"].min()))
               if float(d["high"].max()) > float(d["low"].min()) else None}
        bprov = bprov + px.provenance_dicts()
    except DataUnavailable as e:
        notes.append(f"52-week range unavailable: {e}")
    return clean({
        **_identity(facts),
        "price": price, "price_as_of": as_of,
        "market_cap": mult["market_cap"], "enterprise_value": mult["enterprise_value"],
        "multiples": mult, "multiples_basis": basis, "multiples_period_end": basis_end,
        "beta": beta, "range_52w": rng,
        "method": {"multiples": "EV = market cap + total debt - cash (book debt)",
                   "beta": f"OLS of {BETA_WINDOW_MONTHS} monthly excess returns on {BETA_BENCHMARK}; "
                           "Blume (1971) adjusted = 0.67 b + 0.33"},
        "notes": notes,
        "provenance": prov + qprov + bprov,
    })


@router.get("/{ticker}/statements")
def statements(ticker: Ticker, market: Market,
               period: Annotated[Literal["annual", "quarterly", "ttm"], Query()] = "annual",
               limit: Annotated[int, Query(ge=1, le=40)] = 10) -> dict[str, Any]:
    """Income statement, balance sheet and cash-flow tables with YoY growth."""
    t = _norm(ticker)

    def build() -> dict[str, Any]:
        facts, prov = _facts(market, t)
        out = st.build_statements(facts["annual"], facts["quarterly"], period, limit)
        notes = _facts_notes(facts)
        if period == "ttm":
            notes.append("TTM flows = sum of the last four consecutive fiscal quarters; balances at the latest "
                         "quarter end; EPS = sum of quarterly diluted EPS; shares = 4-quarter average")
        notes.append("missing line items are shown as null -- never imputed; YoY growth is null when the "
                     "prior-year value is missing or non-positive")
        return clean({**_identity(facts), **out, "units": {"default": "USD", "eps_diluted": "USD/share",
                                                           "shares_diluted": "shares"},
                      "notes": notes, "provenance": prov})

    return _cached(f"statements:{t}:{period}:{limit}", FACTS_TTL_S, build)


@router.get("/{ticker}/ratios")
def ratios(ticker: Ticker, market: Market) -> dict[str, Any]:
    """Ratio history (annual and rolling TTM) for charts, a latest snapshot and CAGRs."""
    t = _norm(ticker)

    def build() -> dict[str, Any]:
        facts, prov = _facts(market, t)
        annual_hist = rt.ratio_history(facts["annual"])
        ttm_frame = st.rolling_ttm(facts["quarterly"])
        ttm_hist = rt.ratio_history(ttm_frame) if len(ttm_frame) else pd.DataFrame(columns=list(rt.RATIO_KEYS))
        latest_basis = "ttm" if len(ttm_hist) and ttm_hist.index[-1] >= annual_hist.index[-1] \
            and ttm_hist.iloc[-1].notna().sum() >= annual_hist.iloc[-1].notna().sum() else "fy"
        latest = (ttm_hist if latest_basis == "ttm" else annual_hist)
        latest_row = latest.iloc[-1]
        return clean({
            **_identity(facts),
            "annual": frame(annual_hist),
            "ttm": frame(ttm_hist),
            "latest": {"basis": latest_basis, "period_end": latest.index[-1], "values": latest_row.to_dict()},
            "growth_cagr": rt.growth_table(facts["annual"]),
            "groups": rt.RATIO_GROUPS,
            "method": {"model": "accounting ratios", "averages": "return and turnover ratios divide by the "
                       "average of opening and closing balances (Penman 2013)",
                       "roic": "NOPAT / avg(total debt + equity - cash), NOPAT = EBIT (1 - effective tax)",
                       "dupont": "ROE = net margin x asset turnover x equity multiplier"},
            "notes": _facts_notes(facts) + [
                "ratios needing an opening balance are null for the first period",
                "ratios with a non-positive denominator (e.g. negative equity) are null"],
            "provenance": prov,
        })

    return _cached(f"ratios:{t}", FACTS_TTL_S, build)


@router.get("/{ticker}/scores")
def scores(ticker: Ticker, market: Market) -> dict[str, Any]:
    """Piotroski F, Altman Z and Z'', Beneish M, Sloan accruals and Ohlson O, itemized."""
    t = _norm(ticker)
    return _cached(f"scores:{t}", QUOTE_TTL_S * 5, lambda: _scores(t, market))


def _scores(t: str, market: MarketData) -> dict[str, Any]:
    facts, prov = _facts(market, t)
    annual = facts["annual"]
    notes = _facts_notes(facts)
    mve, qprov = None, []
    try:
        price, _, qprov = _quote(market, t)
        mve = price * _shares(facts)
    except DataUnavailable as e:
        notes.append(f"Altman Z needs market value of equity: {e}")
    deflator, dprov, dnotes = _gdp_deflator_1968(market, annual.index[-1])
    notes += dnotes
    history = []
    for k in range(2, len(annual) + 1):
        a = annual.iloc[:k]
        p, b, s2, sl = sc.piotroski(a), sc.beneish(a), sc.altman_z2(a), sc.sloan_accruals(a)
        history.append({"fiscal_year": a.index[-1], "piotroski": p.get("score"), "beneish_m": b.get("m"),
                        "altman_z2": s2.get("z"), "sloan_accruals": sl.get("ratio")})
    notes += [
        "scores use the latest fiscal year (10-K) figures; EBIT = operating income",
        "Piotroski EQ_OFFER uses the diluted weighted share count as a proxy for equity issuance",
        "Beneish DEPI uses depreciation & amortization; the published coefficients are the model's "
        "estimated parameters (Beneish 1999, Table 3), not market data",
        "Ohlson FUTL uses cash from operations as the proxy for funds from operations; SIZE uses total "
        "assets in dollars over the prior-year GDP deflator (1968 = 100), as in Ohlson (1980)",
        "Altman Z uses today's market value of equity against the latest fiscal-year balance sheet",
    ]
    return clean({
        **_identity(facts),
        "piotroski": sc.piotroski(annual),
        "altman_z": sc.altman_z(annual, mve),
        "altman_z2": sc.altman_z2(annual),
        "beneish": sc.beneish(annual),
        "sloan": sc.sloan_accruals(annual),
        "ohlson": sc.ohlson_o(annual, deflator),
        "history": history,
        "method": {"piotroski": "Piotroski (2000)", "altman_z": "Altman (1968)",
                   "altman_z2": "Altman, Hartzell & Peck (1995)", "beneish": "Beneish (1999) 8-variable",
                   "sloan": "Sloan (1996) / Hribar & Collins (2002)", "ohlson": "Ohlson (1980) model 1"},
        "notes": notes,
        "provenance": prov + qprov + dprov,
    })


# ---------------------------------------------------------------------- DCF
class DCFIn(BaseModel):
    """Every field optional: omitted inputs are derived from data."""

    years: int = Field(default=5, ge=1, le=15, description="explicit forecast years N")
    fcff_basis: Literal["auto", "ttm", "fy"] = "auto"
    base_fcff: float | None = Field(default=None, description="USD; overrides the derived base FCFF")
    initial_growth: float | None = Field(default=None, ge=-0.5, le=1.0, description="year-1 growth (decimal)")
    terminal_growth: float | None = Field(default=None, ge=-0.05, le=0.10)
    risk_free: float | None = Field(default=None, ge=-0.02, le=0.25)
    beta: float | None = Field(default=None, ge=-3.0, le=5.0)
    beta_type: Literal["raw", "blume"] = "raw"
    erp: float | None = Field(default=None, ge=-0.05, le=0.20)
    cost_of_debt: float | None = Field(default=None, ge=0.0, le=0.5)
    tax_rate: float | None = Field(default=None, ge=0.0, le=0.6)
    wacc: float | None = Field(default=None, gt=0.0, le=0.5)
    wacc_step: float = Field(default=0.005, gt=0.0, le=0.05)
    g_step: float = Field(default=0.0025, gt=0.0, le=0.05)


@router.post("/{ticker}/dcf")
def dcf(ticker: Ticker, market: Market, body: DCFIn | None = None) -> dict[str, Any]:
    """Two-stage FCFF DCF (explicit N years + Gordon terminal) with sensitivity and reverse DCF."""
    t = _norm(ticker)
    body = body or DCFIn()
    key = f"dcf:{t}:" + hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    return _cached(key, MODEL_TTL_S, lambda: _dcf(t, body, market))


def _cost_of_equity(body: DCFIn, market: MarketData, t: str, record: Callable[..., None], prov: list[dict],
                    notes: list[str]) -> tuple[float, float]:
    """(r_f, k_e = r_f + beta x ERP), each input overridable, recorded in ``inputs``;
    extends ``prov`` and ``notes`` in place."""
    if body.risk_free is not None:
        rf = body.risk_free
        record("risk_free", rf, "override")
    else:
        rf, d_, p_, fn = _fred_latest(market, "DGS10")
        prov += p_
        notes += fn
        record("risk_free", rf, "derived", method="10-year Treasury constant-maturity yield (FRED DGS10)", as_of=d_)
    if body.beta is not None:
        beta = body.beta
        record("beta", beta, "override")
    else:
        try:
            beta_info, p_, bn = _beta(market, t)
        except ValueError as e:     # too little price history: a data gap, not a bad request
            raise DataUnavailable(f"beta unavailable for {t}: {e}; pass beta") from e
        prov += p_
        notes += bn
        beta = beta_info["blume_adjusted"] if body.beta_type == "blume" else beta_info["beta"]
        record("beta", beta, "derived", type=body.beta_type, raw=beta_info["beta"],
               blume_adjusted=beta_info["blume_adjusted"], se=beta_info["se"], r2=beta_info["r2"],
               n_months=beta_info["n_months"], benchmark=BETA_BENCHMARK)
    if body.erp is not None:
        erp = body.erp
        record("erp", erp, "override")
    else:
        e, p_, en = _erp(market)
        prov += p_
        notes += en
        erp = e["erp"]
        record("erp", erp, "derived", method="full-sample arithmetic mean of monthly market excess returns x 12",
               se=e["se"], n_months=e["n_months"], start=e["start"], end=e["end"], data=e["source"])
    ke = rf + beta * erp
    record("cost_of_equity", ke, "derived", method="CAPM: rf + beta x ERP")
    return rf, ke


def _dcf(t: str, body: DCFIn, market: MarketData) -> dict[str, Any]:
    facts, prov = _facts(market, t)
    annual = facts["annual"]
    price, as_of, qprov = _quote(market, t)
    shares = _shares(facts)
    cap = price * shares
    prov = prov + qprov
    notes = _facts_notes(facts) + _stale_price_notes(as_of)
    inputs: dict[str, dict[str, Any]] = {}

    def record(name: str, value: Any, source: str, **extra: Any) -> None:
        inputs[name] = {"value": value, "source": source, **extra}

    # tax
    if body.tax_rate is not None:
        tax = body.tax_rate
        record("tax_rate", tax, "override")
    else:
        tax = dcfm.effective_tax_3y(annual)
        if tax is None:
            raise DataUnavailable("effective tax rate unavailable (no recent year with positive pre-tax income "
                                  "and reported tax); pass tax_rate")
        record("tax_rate", tax, "derived", method="mean of last 3 fiscal-year effective tax rates "
                                                  "(income_tax / pretax_income), clipped to [0, 0.5]")
    # base FCFF
    ttm = _fresh_ttm(facts)
    ttm_for_fcff = ttm
    if body.fcff_basis == "ttm" and ttm is None:
        ttm_for_fcff = st.latest_ttm(facts["quarterly"])
        if ttm_for_fcff is not None:
            notes.append(f"the latest complete TTM window ends {ttm_for_fcff[0].date()}, before the latest "
                         f"fiscal year ({annual.index[-1].date()}): quarterly data has a gap")
    if body.base_fcff is not None:
        base = body.base_fcff
        record("base_fcff", base, "override")
    else:
        b = dcfm.base_fcff_from_statements(annual, ttm_for_fcff[1] if ttm_for_fcff else None, tax,
                                           body.fcff_basis)
        base = b["fcff"]
        notes += b.pop("notes")
        record("base_fcff", base, "derived", method="CFO + interest x (1 - t) - capex", **b)
    # terminal growth
    if body.terminal_growth is not None:
        g_t = body.terminal_growth
        record("terminal_growth", g_t, "override")
    else:
        g_t, d_, p_, fn = _fred_latest(market, "T10YIE")
        prov += p_
        notes += fn
        record("terminal_growth", g_t, "derived", method="10-year breakeven inflation (FRED T10YIE): a nominal "
                                                          "floor equal to zero real growth", as_of=d_)
    # initial growth
    if body.initial_growth is not None:
        g0 = body.initial_growth
        record("initial_growth", g0, "override")
    else:
        hist, n = dcfm.revenue_cagr(annual, 5)
        if hist is None:
            raise DataUnavailable("revenue CAGR unavailable (need positive revenue at both ends of a >= 1-year "
                                  "window); pass initial_growth")
        g0 = float(np.clip(hist, g_t, dcfm.MAX_INITIAL_GROWTH))
        record("initial_growth", g0, "derived", historical_revenue_cagr=hist, cagr_years=n,
               method=f"{n}-year revenue CAGR ({hist:.2%}) clipped to [terminal g, "
                      f"{dcfm.MAX_INITIAL_GROWTH:.0%}], fading linearly to terminal g")
        if n < 5:
            notes.append(f"only {n} years of revenue history for the growth CAGR")
    # risk-free, beta, ERP -> cost of equity. With a WACC override these only
    # feed the displayed inputs, so missing data must not block the valuation.
    wacc_override = body.wacc is not None
    try:
        rf, ke = _cost_of_equity(body, market, t, record, prov, notes)
    except (DataUnavailable, ValueError) as e:
        if not wacc_override:
            raise
        rf, ke = None, None
        for k in ("risk_free", "beta", "erp", "cost_of_equity"):
            inputs.setdefault(k, {"value": None, "source": "unavailable"})
        notes.append(f"cost of equity not computed ({e}); irrelevant because WACC is overridden")
    # debt
    d_open, d_close = dcfm.debt_open_close(annual)
    bal = ttm[1] if ttm is not None and np.isfinite(ttm[1]["total_debt"]) else annual.iloc[-1]
    debt = float(bal["total_debt"]) if np.isfinite(bal["total_debt"]) else None
    cash = float(bal["cash"]) if np.isfinite(bal["cash"]) else None
    if debt is None:
        notes.append("total debt not reported: treated as debt-free in WACC weights and net debt")
    if cash is None:
        notes.append("cash not reported: net debt excludes cash")
    net_debt = (debt or 0.0) - (cash or 0.0)
    interest = annual.iloc[-1]["interest_expense"]
    if body.cost_of_debt is not None:
        kd = body.cost_of_debt
        record("cost_of_debt", kd, "override")
    elif rf is not None:
        kd, basis = dcfm.cost_of_debt(interest, d_close, d_open, rf)
        record("cost_of_debt", kd, "derived", method=basis)
    else:
        kd = None
        record("cost_of_debt", None, "unavailable", method="needs the risk-free rate; WACC is overridden")
    if body.wacc is not None:
        w = body.wacc
        record("wacc", w, "override")
    else:
        if ke is None or kd is None:  # unreachable: _cost_of_equity raised above
            raise DataUnavailable("cost of capital unavailable; pass wacc")
        w = dcfm.wacc(cap, debt or 0.0, ke, kd, tax)
        record("wacc", w, "derived", method="E/(D+E) ke + D/(D+E) kd (1 - t); E market value, D book value",
               equity_weight=cap / (cap + (debt or 0.0)), debt_weight=(debt or 0.0) / (cap + (debt or 0.0)))

    path = dcfm.fade_path(g0, g_t, body.years)
    val = dcfm.dcf_value(base, path, w, g_t, net_debt, shares, price)
    sens = dcfm.sensitivity(base, g0, body.years, w, g_t, net_debt, shares, body.wacc_step, body.g_step)
    rev = dcfm.reverse_dcf(base, body.years, w, g_t, net_debt, cap)
    rev["historical_revenue_cagr"] = inputs["initial_growth"].get("historical_revenue_cagr")
    if val["terminal_share"] is not None and val["terminal_share"] > 0.75:
        notes.append(f"terminal value is {val['terminal_share']:.0%} of enterprise value: the result is "
                     "dominated by the perpetuity assumptions (see the sensitivity grid)")
    notes.append("end-of-year discounting; FCFF = unlevered free cash flow; net debt at book value")
    return clean({
        **_identity(facts),
        "price": price, "price_as_of": as_of, "market_cap": cap,
        "inputs": inputs,
        "valuation": {**val, "shares_outstanding": shares, "total_debt": debt, "cash": cash},
        "sensitivity": sens,
        "reverse_dcf": rev,
        "method": {"model": "two-stage FCFF DCF with Gordon terminal value",
                   "years": body.years,
                   "references": ["Koller, Goedhart & Wessels (2020), Valuation, 7th ed.",
                                  "Damodaran (2012), Investment Valuation, 3rd ed.",
                                  "Gordon (1962), The Investment, Financing and Valuation of the Corporation",
                                  "Sharpe (1964); Lintner (1965) -- CAPM", "Blume (1971) -- adjusted beta",
                                  "Mauboussin & Rappaport (2001), Expectations Investing -- reverse DCF"]},
        "notes": notes,
        "provenance": prov,
    })
