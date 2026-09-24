"""The market-data facade: the ONE object API routers use to obtain data.

Contract (every method):

* returns a :class:`Dataset` -- ``.data`` plus ``.provenance`` (one record per
  source actually used) -- never a bare frame;
* dates are a tz-naive, normalized, ascending ``DatetimeIndex`` named ``date``;
* numbers are floats in natural units stated per method (prices in USD,
  yields in PERCENT as FRED publishes them, factor returns as DECIMALS);
* raises :class:`DataUnavailable` with a human-readable reason when no source
  can serve the request. It never fabricates, interpolates across missing
  sessions, or falls back to a constant.

Online, each call goes store (fresh?) -> providers in order -> store.put.
Offline (``Settings.offline``), only the committed fixtures and whatever is
already in the store are consulted.

The online providers live in sibling modules (``prices``, ``fred``,
``french``, ``cboe``, ``sec``); this module only orchestrates.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, get_settings
from . import fixtures
from .base import DataUnavailable, Provenance

log = logging.getLogger("ohcamel_quant.data.market")
UNIVERSES_PATH = Path(__file__).resolve().parents[1] / "universes.json"

# FRED constant-maturity Treasury series, tenor in years.
TREASURY_SERIES: dict[str, float] = {
    "DGS1MO": 1 / 12,
    "DGS3MO": 0.25,
    "DGS6MO": 0.5,
    "DGS1": 1.0,
    "DGS2": 2.0,
    "DGS3": 3.0,
    "DGS5": 5.0,
    "DGS7": 7.0,
    "DGS10": 10.0,
    "DGS20": 20.0,
    "DGS30": 30.0,
}


@dataclass
class Dataset:
    data: Any  # usually a DataFrame / Series; sometimes a dict or an OptionChain
    provenance: list[Provenance] = field(default_factory=list)

    def provenance_dicts(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.provenance]


@dataclass
class OptionChain:
    """One underlying's listed options at one instant.

    ``quotes`` columns: expiry (Timestamp), strike, type ('C'|'P'), bid, ask,
    last, volume, open_interest, vendor_iv (decimal, may be NaN),
    contract (OCC symbol). Rows with no two-sided market are kept; analytics
    decide what to filter and say so.
    """

    underlying: str
    spot: float
    as_of: pd.Timestamp
    quotes: pd.DataFrame


def _window(df: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


class MarketData:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- prices
    def ohlcv(self, ticker: str, start: date | None = None, end: date | None = None) -> Dataset:
        """Daily bars: columns open, high, low, close, adj_close, volume.

        ``adj_close`` is split- and dividend-adjusted where the source provides
        it (Yahoo does; Alpaca's is split-adjusted only -- stated in provenance).
        """
        ticker = ticker.upper().strip()
        if self.settings.offline:
            hit = fixtures.history(ticker)
            if hit is None:
                raise DataUnavailable(
                    f"{ticker}: offline mode and no committed fixture "
                    f"(available: {', '.join(fixtures.fixture_symbols())})"
                )
            df, prov = hit
            return Dataset(_window(df, start, end), [prov])
        from . import prices  # online providers

        return prices.fetch_ohlcv(ticker, start, end, self.settings)

    def prices(
        self, tickers: list[str], start: date | None = None, end: date | None = None,
        field: str = "adj_close",
    ) -> Dataset:
        """Wide frame of one field for many tickers, inner-joined on common sessions.

        Raises DataUnavailable naming every ticker that could not be served.
        """
        frames: dict[str, pd.Series] = {}
        provs: list[Provenance] = []
        errors: list[str] = []
        for t in dict.fromkeys(x.upper().strip() for x in tickers if x.strip()):
            try:
                ds = self.ohlcv(t, start, end)
            except DataUnavailable as e:
                errors.append(str(e))
                continue
            frames[t] = ds.data[field]
            provs.extend(ds.provenance)
        if errors:
            raise DataUnavailable("; ".join(errors))
        if not frames:
            raise DataUnavailable("no tickers requested")
        full = pd.DataFrame(frames).sort_index()
        # Sessions every ticker has a row for, but where some ticker's value is NaN:
        # these are dropped by the inner join and the caller should know (calendar
        # differences -- a row missing altogether -- are ordinary and not reported).
        common = None
        for sr in frames.values():
            common = sr.index if common is None else common.intersection(sr.index)
        blank = full.loc[common].isna() if common is not None else full.iloc[:0].isna()
        bad = blank.any(axis=1)
        wide = full.dropna(how="any")
        wide.index.name = "date"
        if bool(bad.any()):
            per = {t: int(n) for t, n in blank.sum().items() if n}
            dates = [d.date().isoformat() for d in blank.index[bad]]
            provs.append(Provenance.now(
                "derived", field=field, dropped_sessions=dates[:50], nan_tickers=per,
                note=(f"{len(dates)} session(s) dropped from the common-session join because {field} is NaN "
                      "for " + ", ".join(f"{t} ({n})" for t, n in per.items())
                      + f": {', '.join(dates[:5])}{' ...' if len(dates) > 5 else ''}")))
        return Dataset(wide, provs)

    def returns(
        self, tickers: list[str], start: date | None = None, end: date | None = None,
        log: bool = False,
    ) -> Dataset:
        """Daily simple (or log) returns from adj_close on common sessions."""
        import numpy as np

        px = self.prices(tickers, start, end)
        r = np.log(px.data).diff() if log else px.data.pct_change()
        return Dataset(r.iloc[1:], px.provenance)

    def quotes(self, tickers: list[str]) -> Dataset:
        """Latest snapshot per ticker: DataFrame indexed by ticker with
        price, prev_close, change, change_pct, as_of (Timestamp), source."""
        if self.settings.offline:
            rows, provs = [], []
            for t in tickers:
                ds = self.ohlcv(t)
                d = ds.data
                last, prev = d.iloc[-1], d.iloc[-2]
                rows.append({
                    "ticker": t.upper(), "price": last["close"], "prev_close": prev["close"],
                    "change": last["close"] - prev["close"],
                    "change_pct": last["close"] / prev["close"] - 1.0,
                    "as_of": d.index[-1], "source": ds.provenance[0].source,
                })
                provs.extend(ds.provenance)
            return Dataset(pd.DataFrame(rows).set_index("ticker"), provs)
        from . import prices

        return prices.fetch_quotes([t.upper() for t in tickers], self.settings)

    # ----------------------------------------------------------------- macro
    def fred(self, series_ids: list[str], start: date | None = None, end: date | None = None) -> Dataset:
        """FRED series as published (levels; yields in percent), outer-joined,
        NOT forward-filled (FRED holidays stay NaN)."""
        ids = [s.upper() for s in series_ids]
        if self.settings.offline:
            hit = fixtures.macro(ids)
            missing = [s for s in ids if s not in fixtures.FRED_FIXTURE_COLUMNS]
            if hit is None or missing:
                raise DataUnavailable(f"offline mode: FRED series not in fixtures: {missing or ids}")
            df, prov = hit
            return Dataset(_window(df, start, end), [prov])
        from . import fred

        return fred.fetch_series(ids, start, end, self.settings)

    def treasury_curve(self, start: date | None = None, end: date | None = None) -> Dataset:
        """Constant-maturity par yields in percent; columns are tenors in YEARS
        (floats, ascending). Rows where every tenor is NaN are dropped."""
        ds = self.fred(list(TREASURY_SERIES), start, end)
        df = ds.data.rename(columns=TREASURY_SERIES)
        df = df[sorted(df.columns)].dropna(how="all")
        return Dataset(df, ds.provenance)

    def risk_free_daily(self, start: date | None = None, end: date | None = None) -> Dataset:
        """Daily risk-free DECIMAL return from the 3-month T-bill (DGS3MO,
        falling back to Ken French's RF), forward-filled only across FRED
        holidays inside the window."""
        from . import fred

        return fred.risk_free_daily(self, start, end)

    def fred_metadata(self, series_ids: list[str]) -> Dataset:
        """FRED series metadata ``{id: {title, units, frequency, ...}}``
        (needs FRED_API_KEY online; ids only otherwise -- never guessed)."""
        ids = [s.upper() for s in series_ids]
        if self.settings.offline:
            return Dataset({s: {"id": s} for s in ids}, [Provenance.now("fixture", note="offline: ids only")])
        from . import fred

        return fred.fetch_metadata(ids, self.settings)

    # --------------------------------------------------------------- factors
    def french_dataset(self, name: str, table: int = 0) -> Dataset:
        """Any Kenneth French library file by stem (e.g.
        ``12_Industry_Portfolios_daily``), first (or ``table``-th) block, DECIMALS."""
        if self.settings.offline:
            raise DataUnavailable("offline mode: Kenneth French data is fetched online only")
        from . import french

        return french.fetch_dataset(name, self.settings, table)

    def ff_factors(self, model: str = "ff5", momentum: bool = True) -> Dataset:
        """Kenneth French daily factors as DECIMALS: Mkt-RF, SMB, HML
        [, RMW, CMA] [, Mom], RF."""
        if self.settings.offline:
            raise DataUnavailable("offline mode: Fama-French factors are fetched online only")
        from . import french

        return french.fetch_factors(model, momentum, self.settings)

    # --------------------------------------------------------------- options
    def option_chain(self, ticker: str) -> Dataset:
        """Delayed full chain from Cboe; ``.data`` is an OptionChain."""
        if self.settings.offline:
            raise DataUnavailable("offline mode: option chains are fetched online only")
        from . import cboe

        return cboe.fetch_chain(ticker.upper(), self.settings)

    # ------------------------------------------------------------ filings
    def company_facts(self, ticker: str) -> Dataset:
        """SEC XBRL company facts, standardized: ``.data`` is a dict with keys
        'cik', 'name', 'ticker', 'annual' (DataFrame, fiscal-year rows x
        standardized line items, USD) and 'quarterly' (same, fiscal quarters),
        'shares_outstanding' (latest dei value, float or None)."""
        if self.settings.offline:
            raise DataUnavailable("offline mode: SEC filings are fetched online only")
        from . import sec

        return sec.fetch_company_facts(ticker.upper(), self.settings)

    def holdings_13f(self, cik: str) -> Dataset:
        """Latest 13F-HR information table for a filer: ``.data`` is a dict
        'filer', 'period', 'filed', 'holdings' (DataFrame: issuer, cusip,
        title, value_usd, shares, put_call, ticker [mapped where possible],
        weight)."""
        if self.settings.offline:
            raise DataUnavailable("offline mode: SEC filings are fetched online only")
        from . import sec

        return sec.fetch_13f(cik, self.settings)

    def search(self, query: str, limit: int = 12) -> Dataset:
        """Ticker/company search over SEC's company_tickers list (plus fixture
        symbols offline): list of dicts {ticker, name, cik}."""
        if self.settings.offline:
            q = query.upper()
            hits = [{"ticker": s, "name": s, "cik": None} for s in fixtures.fixture_symbols() if q in s]
            return Dataset(hits[:limit], [Provenance.now("fixture")])
        from . import sec

        return sec.search(query, limit, self.settings)


@lru_cache(maxsize=1)
def get_market() -> MarketData:
    """FastAPI dependency (``Depends(get_market)``); tests override it."""
    return MarketData()


@lru_cache(maxsize=1)
def _universes_cached() -> dict[str, Any]:
    return json.loads(UNIVERSES_PATH.read_text())


def load_universes() -> dict[str, Any]:
    """Named universes (definitions only) from ``universes.json``:
    ``{"universes": {key: {label, description, members: [{ticker, name}]}},
    "notable_13f_filers": [{cik, name, manager}]}``."""
    import copy

    return copy.deepcopy(_universes_cached())


_refresh_thread: threading.Thread | None = None
_refresh_stop = threading.Event()


def start_background_refresh(interval_s: float = 6 * 3600, initial_delay_s: float = 5.0,
                             market: MarketData | None = None) -> threading.Thread | None:
    """Start (once per process) a daemon thread that runs
    :func:`ohcamel_quant.data.warm.warm` after ``initial_delay_s`` and then
    every ``interval_s`` seconds. No-op in offline mode (returns None).

    Call it from the app's startup (e.g. a FastAPI lifespan/startup hook);
    :func:`stop_background_refresh` stops it at shutdown.
    """
    global _refresh_thread
    m = market or get_market()
    if m.settings.offline:
        return None
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return _refresh_thread
    _refresh_stop.clear()

    def loop() -> None:
        from .warm import warm

        if _refresh_stop.wait(initial_delay_s):
            return
        while not _refresh_stop.is_set():
            try:
                warm(m)
            except Exception:  # noqa: BLE001 - keep the refresher alive
                log.exception("background refresh failed")
            if _refresh_stop.wait(interval_s):
                return

    _refresh_thread = threading.Thread(target=loop, name="ohcamel-data-refresh", daemon=True)
    _refresh_thread.start()
    return _refresh_thread


def stop_background_refresh(timeout_s: float = 5.0) -> None:
    """Signal the background refresher to stop and wait briefly for it."""
    _refresh_stop.set()
    if _refresh_thread is not None:
        _refresh_thread.join(timeout_s)
