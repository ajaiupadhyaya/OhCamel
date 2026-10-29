"""The world: every name the warehouse holds, read from repo config.

ETFs and their universes from ``universes.json``; equities from the committed
S&P 500 and Nasdaq-100 lists (ETFs excluded); FRED series from ``macro/series.json``
plus the Treasury tenors; the two Ken French factors; the 13F filers; and the
minute and option tracks' tickers. Names only: no prices, no network, no warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.market import TREASURY_SERIES, load_universes
from ..macro.dashboard import load_series_config
from ..warehouse.constituents import load_constituents
from ..warehouse.ingest.minute import MINUTE_TICKERS
from ..warehouse.ingest.options import OPTION_UNDERLYINGS

FACTORS = (("FF5", "Fama–French five factors, daily"), ("MOM", "Momentum factor, daily"))


@dataclass(frozen=True)
class World:
    etfs: tuple[tuple[str, str, tuple[str, ...]], ...]  # ticker, name, universe labels
    equities: dict[str, str]  # ticker -> name
    fred: tuple[tuple[str, str], ...]  # series id, name
    filers: tuple[dict[str, str], ...]  # cik, name, manager
    minute: tuple[str, ...]
    options: tuple[str, ...]
    factors: tuple[tuple[str, str], ...] = FACTORS

    def daily(self) -> list[str]:
        """The tickers with daily bars: ETFs, then equities."""
        return [t for t, _, _ in self.etfs] + list(self.equities)


def _treasury_name(years: float) -> str:
    return f"Treasury yield, {years:g}-year" if years >= 1 else f"Treasury yield, {round(years * 12)}-month"


def load_world() -> World:
    uni = load_universes()
    names: dict[str, str] = {}
    labels: dict[str, list[str]] = {}
    for u in uni["universes"].values():
        for m in u["members"]:
            names.setdefault(m["ticker"], m["name"])
            labels.setdefault(m["ticker"], []).append(u["label"])
    etfs = tuple((t, n, tuple(labels[t])) for t, n in names.items())

    equities: dict[str, str] = {}
    for index in ("sp500", "ndx100"):
        for ticker, name in load_constituents(index).members:
            if ticker not in names:
                equities.setdefault(ticker, name)

    fred_names = {s["id"]: s["name"] for s in load_series_config()["series"]}
    for series_id, years in TREASURY_SERIES.items():
        fred_names.setdefault(series_id, _treasury_name(years))

    filers = tuple({k: f[k] for k in ("cik", "name", "manager")} for f in uni["notable_13f_filers"])
    return World(etfs, equities, tuple(fred_names.items()), filers, tuple(MINUTE_TICKERS), tuple(OPTION_UNDERLYINGS))
