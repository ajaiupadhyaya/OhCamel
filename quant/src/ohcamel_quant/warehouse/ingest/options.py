"""``ingest.option_snapshots`` (compute plan C6): each night after 16:15 New York,
Cboe's delayed full chain for 30 underlyings into ``option_snapshots``. The
history P7 builds on; it starts empty and grows, and cannot be re-fetched.

``asof`` is the chain's own (New York) timestamp date. Only the standard root
is kept: adjusted roots (``SPY1``) share (expiry, strike, cp) with it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...data import cboe, http
from .base import KeyState, Written, insert_frame, run_ingest

SECTORS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
BROAD = ("SPY", "QQQ", "IWM", "TLT", "GLD")
#: The 14 largest single names by listed-option volume, a dated choice (2026-10) the owner may revise.
TOP_NAMES = ("TSLA", "NVDA", "AAPL", "AMZN", "PLTR", "AMD", "META", "MSFT", "GOOGL", "AVGO", "MSTR", "NFLX",
             "COIN", "HOOD")
OPTION_UNDERLYINGS = SECTORS + BROAD + TOP_NAMES
# "asof" is quoted everywhere: it is DuckDB's reserved ASOF join keyword (II.5 as amended).
INSERT = ('INSERT OR REPLACE INTO option_snapshots SELECT underlying, CAST("asof" AS DATE), CAST(expiry AS DATE), '
          "strike, cp, bid, ask, last, volume, open_interest, source FROM incoming")


def run_option_snapshots(params: dict, ctx: Any) -> dict:
    unds = [u.upper().strip() for u in params.get("underlyings") or OPTION_UNDERLYINGS]

    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        return {u: KeyState(d) for u, d in con.execute(
            'SELECT underlying, max("asof") FROM option_snapshots WHERE list_contains(?, underlying) '
            "GROUP BY underlying", [keys]).fetchall()}

    def fetch(key: str, state: KeyState, s: Any):
        payload = http.get_json(cboe.URL.format(sym=cboe.cboe_symbol(key)), s, source="cboe")
        yield cboe.parse_chain(payload, key)

    def write(con: Any, key: str, state: KeyState, chain: Any) -> Written:
        q = chain.quotes
        root = cboe.cboe_symbol(key).lstrip("_")
        std = q[q["root"] == root]
        frame = pd.DataFrame({"underlying": key, "asof": chain.as_of.normalize(), "expiry": std["expiry"],
                              "strike": std["strike"].astype(float), "cp": std["type"], "bid": std["bid"],
                              "ask": std["ask"], "last": std["last"], "volume": std["volume"],
                              "open_interest": std["open_interest"], "source": "cboe"})
        frame = frame.drop_duplicates(["expiry", "strike", "cp"], keep="first")
        insert_frame(con, INSERT, frame)
        return Written(len(frame), chain.as_of.date(), {
            "source": "cboe", "spot": chain.spot, "as_of": chain.as_of.isoformat(),
            "dropped_nonstandard_roots": int(len(q) - len(std)), "delay": "15 minutes (Cboe delayed quotes)"})

    return run_ingest(dataset="option_snapshots", keys=list(dict.fromkeys(unds)), ctx=ctx, read_state=read_state,
                      fetch=fetch, write=write,
                      notes=["Cboe delayed quotes (15 min); asof is the chain's own New York timestamp date"])
