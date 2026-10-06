"""``ingest.universes`` (compute plan C2): ``universe_members`` from the committed
constituent lists (sp500, ndx100) and the site's universes.json ('site', ~45),
names from SEC company_tickers where it lists the ticker. Each universe is
replaced whole in one transaction."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...data import sec
from ...data.base import DataUnavailable
from ...data.market import load_universes
from ..constituents import SURVIVORSHIP, load_constituents
from .base import KeyState, Written, insert_frame, run_ingest

UNIVERSES = ("sp500", "ndx100", "site")
COLUMNS = ["universe", "ticker", "name", "added", "source", "survivorship"]
LIST_SOURCE = {"sp500": "wikipedia:List_of_S&P_500_companies", "ndx100": "wikipedia:List_of_NASDAQ-100_companies"}


def _site_members() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for u in load_universes()["universes"].values():
        for m in u["members"]:
            seen.setdefault(m["ticker"].upper(), m["name"])
    return list(seen.items())


def universe_rows(universe: str, sec_names: dict[str, str] | None) -> pd.DataFrame:
    if universe == "site":
        members, added, base = _site_members(), None, "ohcamel:universes.json"
    else:
        c = load_constituents(universe)
        members, added, base = c.members, c.as_of, f"{LIST_SOURCE[universe]}@{c.as_of.isoformat()}"
    rows = []
    for ticker, name in members:
        sec_name = (sec_names or {}).get(ticker) or (sec_names or {}).get(ticker.replace(".", "-"))
        rows.append({"universe": universe, "ticker": ticker, "name": sec_name or name, "added": added,
                     "source": base + ("+sec:company_tickers" if sec_name else ""), "survivorship": SURVIVORSHIP})
    return pd.DataFrame(rows, columns=COLUMNS)


def _sec_names(settings: Any) -> tuple[dict[str, str] | None, str | None]:
    try:
        table, _ = sec.ticker_table(settings)
    except DataUnavailable as e:
        return None, f"SEC company_tickers unavailable ({e}): names from the constituent lists"
    return dict(zip(table["ticker"], table["name"], strict=True)), None


def run_universes(params: dict, ctx: Any) -> dict:
    names, warn = _sec_names(ctx.market.settings)

    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        return {}

    def fetch(key: str, state: KeyState, settings: Any):
        yield universe_rows(key, names)

    def write(con: Any, key: str, state: KeyState, frame: pd.DataFrame) -> Written:
        con.execute("DELETE FROM universe_members WHERE universe = ?", [key])
        insert_frame(con, "INSERT INTO universe_members SELECT universe, ticker, name, CAST(added AS DATE), source, "
                          "survivorship FROM incoming", frame)
        added = frame["added"].iloc[0] if len(frame) else None
        base = str(frame["source"].iloc[0]).split("+")[0] if len(frame) else key
        named = int(frame["source"].str.contains("sec:company_tickers").sum())
        return Written(len(frame), added, {"source": base, "sec_named": named})

    notes = [f"universe members are {SURVIVORSHIP}"] + ([warn] if warn else [])
    return run_ingest(dataset="universe_members", keys=list(UNIVERSES), ctx=ctx, read_state=read_state,
                      fetch=fetch, write=write, notes=notes, survivorship=SURVIVORSHIP)
