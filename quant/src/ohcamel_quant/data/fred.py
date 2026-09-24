"""FRED (Federal Reserve Bank of St. Louis) economic time series.

Two documented endpoints:

* public CSV, no key: ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=A,B``
  (first column ``observation_date`` -- older files: ``DATE`` -- then one
  column per id; ``.`` or blank marks a missing observation);
* API, with ``FRED_API_KEY``: ``https://api.stlouisfed.org/fred/series/observations``
  (``file_type=json``; ``value == "."`` is missing) and
  ``/fred/series`` for metadata (title, units, frequency, seasonal adjustment).

Each series' full history is cached (family ``fred``) and windowed locally,
so one download serves every window. Values are returned exactly as
published (yields in percent), outer-joined, never forward-filled.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store

if TYPE_CHECKING:
    from .market import Dataset, MarketData

log = logging.getLogger("ohcamel_quant.data.fred")

FAMILY = "fred"
META_FAMILY = "fred_meta"
FREDGRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API = "https://api.stlouisfed.org/fred"
TRADING_DAYS = 252
RF_FFILL_LIMIT = 5  # business days: covers FRED/bond-market holidays, not outages


def parse_fredgraph_csv(text: str) -> pd.DataFrame:
    """Parse fredgraph.csv into a float frame indexed by ``date``.

    Accepts both header styles (``observation_date`` and legacy ``DATE``) and
    both missing-value markers (``.`` and empty cells).
    """
    text = text.lstrip("\ufeff \t\r\n")  # a UTF-8 BOM would hide the header name
    head = text[:64].lower()
    if not (head.startswith("observation_date") or head.startswith("date")):
        raise DataUnavailable(f"fred: unexpected response ({' '.join(text.split())[:80]})")
    df = pd.read_csv(io.StringIO(text), na_values=[".", "", " "], keep_default_na=True)
    date_col = df.columns[0]
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop(date_col))).normalize()
    df.index.name = "date"
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.apply(pd.to_numeric, errors="coerce").astype(float).sort_index()


def parse_api_observations(payload: dict[str, Any], series_id: str) -> pd.Series:
    """FRED API ``series/observations`` JSON -> float Series (``.`` -> NaN)."""
    if "error_message" in payload:
        raise DataUnavailable(f"fred api: {payload['error_message']}")
    obs = payload.get("observations") or []
    if not obs:
        raise DataUnavailable(f"fred api: no observations for {series_id}")
    idx = pd.DatetimeIndex(pd.to_datetime([o["date"] for o in obs])).normalize()
    vals = pd.to_numeric(pd.Series([o.get("value") for o in obs]), errors="coerce").to_numpy(dtype=float)
    s = pd.Series(vals, index=idx, name=series_id)
    s.index.name = "date"
    return s.sort_index()


def _fetch_csv(ids: list[str], settings: Settings) -> tuple[dict[str, pd.Series], Provenance]:
    text = http.get_text(FREDGRAPH, settings, params={"id": ",".join(ids)}, source="fred")
    df = parse_fredgraph_csv(text)
    out: dict[str, pd.Series] = {}
    for sid in ids:
        if sid in df.columns and df[sid].notna().any():
            s = df[sid]
            first, last = s.first_valid_index(), s.last_valid_index()
            out[sid] = s.loc[first:last].rename(sid)
    return out, Provenance.now("fred", endpoint=FREDGRAPH, series=ids)


def _fetch_api(sid: str, settings: Settings) -> tuple[pd.Series, Provenance]:
    payload = http.get_json(
        f"{API}/series/observations", settings,
        params={"series_id": sid, "api_key": settings.fred_api_key, "file_type": "json"},
        source="fred-api",
    )
    return parse_api_observations(payload, sid), Provenance.now(
        "fred", endpoint=f"{API}/series/observations", series=[sid]
    )


def _series(ids: list[str], settings: Settings) -> tuple[dict[str, pd.Series], list[Provenance], list[str]]:
    """Full-history series by id: fresh cache, else download (API per id when a
    key exists, else one fredgraph request for all), else stale cache."""
    store = get_store(settings)
    got: dict[str, pd.Series] = {}
    provs: list[Provenance] = []
    need: list[str] = []
    for sid in ids:
        hit = store.get(FAMILY, sid, settings.ttl_macro_s)
        if hit is not None:
            got[sid] = hit[0].rename(sid)
            provs.append(hit[1])
        else:
            need.append(sid)
    errors: dict[str, str] = {}
    if need and not settings.offline:
        if settings.fred_api_key:
            for sid in need:
                try:
                    s, p = _fetch_api(sid, settings)
                    got[sid] = s
                    provs.append(p)
                    store.put(FAMILY, sid, s, p)
                except DataUnavailable as e:
                    errors[sid] = str(e)
        remaining = [s for s in need if s not in got]
        # One request for all ids; if FRED rejects the batch as a bad request
        # (one unknown id fails the whole fredgraph call), retry id by id so
        # the error names the bad series and the good ones are still served.
        groups = [remaining] if remaining else []
        while groups:
            group = groups.pop(0)
            try:
                fetched, p = _fetch_csv(group, settings)
            except DataUnavailable as e:
                status = getattr(e, "status", None)
                if len(group) > 1 and status in (400, 404):
                    groups.extend([sid] for sid in group)
                    continue
                for sid in group:
                    errors[sid] = str(e) if len(group) > 1 or status not in (400, 404) else \
                        f"fred: series {sid} not found ({e})"
                continue
            for sid, s in fetched.items():
                got[sid] = s
                one = Provenance(source=p.source, fetched_at=p.fetched_at,
                                 detail={"endpoint": FREDGRAPH, "series": [sid]})
                store.put(FAMILY, sid, s, one)
            if fetched:
                provs.append(Provenance(source=p.source, fetched_at=p.fetched_at,
                                        detail={"endpoint": FREDGRAPH, "series": sorted(fetched)}))
            for sid in group:
                if sid not in fetched:
                    errors[sid] = f"fred: series {sid} not found or empty"
    elif need:
        for sid in need:
            errors[sid] = "offline mode"
    missing = []
    for sid in ids:
        if sid in got:
            continue
        stale = store.get_stale(FAMILY, sid, errors.get(sid, "refresh failed"))
        if stale is not None:
            got[sid] = stale[0].rename(sid)
            provs.append(stale[1])
        else:
            missing.append(f"{sid} ({errors.get(sid, 'unavailable')})")
    return got, provs, missing


def fetch_series(ids: list[str], start: date | None, end: date | None, settings: Settings) -> Dataset:
    """Outer-joined FRED series (as published, NOT forward-filled)."""
    from .market import Dataset

    ids = list(dict.fromkeys(s.strip().upper() for s in ids if s.strip()))
    if not ids:
        raise DataUnavailable("fred: no series requested")
    got, provs, missing = _series(ids, settings)
    if missing:
        raise DataUnavailable("fred: could not fetch " + "; ".join(missing))
    df = pd.concat([got[s] for s in ids], axis=1, sort=True)
    df.columns = ids
    df.index.name = "date"
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    df = df.dropna(how="all")
    if df.empty:
        raise DataUnavailable(f"fred: no observations of {', '.join(ids)} in the requested window")
    return Dataset(df.astype(float), provs)


def fetch_metadata(ids: list[str], settings: Settings) -> Dataset:
    """Series metadata ``{id: {title, units, frequency, seasonal_adjustment,
    last_updated, observation_start, observation_end, notes}}``.

    Needs ``FRED_API_KEY``; without it each entry is ``{"id": id}`` only and
    the provenance says so (nothing is guessed).
    """
    from .market import Dataset

    ids = list(dict.fromkeys(s.strip().upper() for s in ids if s.strip()))
    if not settings.fred_api_key:
        return Dataset({sid: {"id": sid} for sid in ids},
                       [Provenance.now("fred", note="metadata requires FRED_API_KEY; ids only")])
    store = get_store(settings)
    out: dict[str, dict[str, Any]] = {}
    provs: list[Provenance] = []
    for sid in ids:
        def fetch(sid: str = sid) -> tuple[dict[str, Any], Provenance]:
            payload = http.get_json(f"{API}/series", settings, params={
                "series_id": sid, "api_key": settings.fred_api_key, "file_type": "json"}, source="fred-api")
            rows = payload.get("seriess") or []
            if not rows:
                raise DataUnavailable(f"fred api: no metadata for {sid}")
            r = rows[0]
            keep = ("id", "title", "units", "units_short", "frequency", "frequency_short",
                    "seasonal_adjustment", "last_updated", "observation_start", "observation_end", "notes")
            return {k: r.get(k) for k in keep}, Provenance.now("fred", endpoint=f"{API}/series", series=[sid])

        try:
            meta, p = store.fetch_or_stale(META_FAMILY, sid, 7 * 24 * 3600, fetch, offline=settings.offline)
        except DataUnavailable as e:
            meta, p = {"id": sid, "error": str(e)}, Provenance.now("fred", note=str(e))
        out[sid] = meta
        provs.append(p)
    return Dataset(out, provs)


def yield_to_daily_return(y_pct: pd.Series, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Convert an annualized yield in percent to a per-period decimal return,
    ``r_d = (1 + y/100)**(1/252) - 1`` (geometric de-annualization)."""
    return (1.0 + y_pct / 100.0) ** (1.0 / periods_per_year) - 1.0


def risk_free_daily(market: MarketData, start: date | None, end: date | None) -> Dataset:
    """Daily risk-free DECIMAL return (``Series`` named ``RF``, business-day index).

    Source: FRED ``DGS3MO`` (3-month Treasury constant-maturity yield, percent,
    bond-equivalent basis), converted with :func:`yield_to_daily_return`.
    FRED holidays (NaN) are forward-filled for at most five business days
    and only between the first and last published observation in the window
    -- never extrapolated past the last print. If FRED cannot serve it, falls
    back to Kenneth French's daily ``RF`` (1-month T-bill, Ibbotson), already
    a daily decimal return.
    """
    from .market import Dataset

    fred_err = ""
    try:
        ds = market.fred(["DGS3MO"], start, end)
        y = ds.data["DGS3MO"]
        first, last = y.first_valid_index(), y.last_valid_index()
        if first is None:
            raise DataUnavailable("fred: DGS3MO has no observations in window")
        bdays = pd.bdate_range(first, last, name="date")
        y = y.reindex(bdays.union(y.loc[first:last].dropna().index)).ffill(limit=RF_FFILL_LIMIT)
        y = y[y.index.dayofweek < 5].dropna()
        rf = yield_to_daily_return(y).rename("RF").astype(float)
        rf.index.name = "date"
        provs = list(ds.provenance) + [Provenance.now(
            "derived", series="DGS3MO", rf_series="DGS3MO", formula="(1 + y/100)**(1/252) - 1",
            ffill=f"FRED holidays only, <= {RF_FFILL_LIMIT} business days, inside window")]
        return Dataset(rf, provs)
    except DataUnavailable as e:
        fred_err = str(e)
        log.info("risk_free_daily: FRED failed (%s); trying Ken French RF", e)
    try:
        ff = market.ff_factors("ff3", momentum=False)
    except DataUnavailable as e:
        raise DataUnavailable(f"risk-free rate unavailable: {fred_err}; french: {e}") from e
    rf = ff.data["RF"].astype(float).rename("RF")
    if start is not None:
        rf = rf[rf.index >= pd.Timestamp(start)]
    if end is not None:
        rf = rf[rf.index <= pd.Timestamp(end)]
    if rf.empty:
        raise DataUnavailable(f"risk-free rate unavailable in window: {fred_err}; french RF has no rows")
    rf.index.name = "date"
    note = Provenance.now("derived", rf_series="FF_RF",
                          note=f"FRED DGS3MO unavailable ({fred_err}); using Ken French RF "
                               "(1-month T-bill) instead")
    return Dataset(rf, list(ff.provenance) + [note])


def rf_error_reason(e: BaseException) -> str:
    """The provider's reason without its own leading 'risk-free rate unavailable'
    phrase, so a note reads 'Risk-free rate unavailable (<reason>)' once."""
    msg = str(e).strip()
    for prefix in ("risk-free rate unavailable in window", "risk-free rate unavailable"):
        if msg.lower().startswith(prefix):
            msg = msg[len(prefix):].lstrip(" :-")
            break
    return msg or "no source could serve it"


def rf_series_used(provenance: list[dict[str, Any]]) -> str | None:
    """Which series a :func:`risk_free_daily` result came from, read from the
    structured ``detail.rf_series`` field of its provenance: ``"DGS3MO"``,
    ``"FF_RF"`` (Ken French fallback) or ``None`` if absent."""
    for p in reversed(provenance):
        v = (p.get("detail") or {}).get("rf_series")
        if v:
            return str(v)
    return None


__all__ = [
    "rf_error_reason", "rf_series_used", "fetch_series", "fetch_metadata", "risk_free_daily", "parse_fredgraph_csv",
    "parse_api_observations", "yield_to_daily_return",
]
