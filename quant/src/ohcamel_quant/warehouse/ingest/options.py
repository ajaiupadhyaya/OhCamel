"""``ingest.option_snapshots`` (compute plan C6; completion task 3.4): Cboe's delayed full chain for 30
underlyings into ``option_snapshots``, twice a session -- the ``'1545'`` pull before the close (scheduled 15:42 New York) and
the ``'1620'`` pull after it. The history P7 builds on; it starts empty and grows, and cannot be re-fetched.

Each row keeps what a surface needs: ``spot`` (the underlying price the vendor stated in the same payload),
``quote_ts`` (when the quotes were taken, naive UTC; ``quote_ts_source`` names the vendor field that gave it --
data/cboe.py: the header's ``last_trade_time``, else the payload's ``timestamp``; never this job's clock) and
``vendor_iv`` (Cboe's own implied vol, stored for comparison only, never used as the surface's IV; NULL where Cboe
has no model value). ``asof`` is the New York date of ``quote_ts``.

A ``'1545'`` pull must be stamped by the vendor's quote time (``last_trade_time``; the payload clock's reading
is not trusted for it) and hold quotes of a market still open: when the payload's quotes are more than
``LIVE_LAG_MAX`` older than the payload itself (a holiday, a 13:00 half day) or taken at or after 16:00, the key
stores nothing and says so -- closing quotes are never filed as pre-close ones. Only the standard root is kept:
adjusted roots (``SPY1``) share (expiry, strike, cp) with it.
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
#: The pulls: before the close ('1545', scheduled 15:42 New York) and after it (16:20). ``vol.surface_history`` prefers the first.
PULLS = ("1545", "1620")
DEFAULT_PULL = "1620"
#: A pre-close payload's quotes run the 15-minute delay behind it; more than this and the market was not open.
LIVE_LAG_MAX = pd.Timedelta(minutes=30)
COLUMNS = ('underlying, "asof", expiry, strike, cp, bid, ask, last, volume, open_interest, source, spot, quote_ts, '
           "vendor_iv, pull, quote_ts_source")
# "asof" is quoted everywhere: it is DuckDB's reserved ASOF join keyword (II.5 as amended).
INSERT = (f"INSERT OR REPLACE INTO option_snapshots ({COLUMNS}) SELECT underlying, CAST(\"asof\" AS DATE), "
          "CAST(expiry AS DATE), strike, cp, bid, ask, last, volume, open_interest, source, spot, "
          "CAST(quote_ts AS TIMESTAMP), vendor_iv, pull, quote_ts_source FROM incoming")


def _utc(ny: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(ny).tz_localize("America/New_York").tz_convert("UTC").tz_localize(None)


def pre_close_refusal(chain: Any) -> str | None:
    """Why ``chain`` cannot be a '1545' (pre-close) snapshot, or None when it can."""
    as_of = pd.Timestamp(chain.as_of)
    if getattr(chain, "quote_ts_source", None) != "cboe:last_trade_time":
        # stamped from the payload's own clock: were Cboe's `timestamp` ever not UTC, this stamp would be hours off
        # with nothing to show it, so a pre-close snapshot is filed only on the vendor's quote time
        return (f"no last_trade_time in the payload (quote time from {getattr(chain, 'quote_ts_source', None)}): a "
                "pre-close snapshot is stored only on the vendor's own quote time")
    gen = getattr(chain, "payload_ts", None)
    if gen is not None and pd.Timestamp(gen) - _utc(as_of) > LIVE_LAG_MAX:
        return (f"closing quotes: taken at {as_of} New York, {pd.Timestamp(gen) - _utc(as_of)} before the payload "
                "(the market was not open at the pre-close pull)")
    if as_of.time() >= pd.Timestamp("16:00").time():
        return f"closing quotes: taken at {as_of} New York, at or after the 16:00 close"
    return None


def run_option_snapshots(params: dict, ctx: Any) -> dict:
    unds = [u.upper().strip() for u in params.get("underlyings") or OPTION_UNDERLYINGS]
    pull = str(params.get("pull") or DEFAULT_PULL)
    if pull not in PULLS:
        raise ValueError(f"pull must be one of {PULLS}; got {pull!r}")

    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        return {u: KeyState(d) for u, d in con.execute(
            'SELECT underlying, max("asof") FROM option_snapshots WHERE list_contains(?, underlying) AND pull = ? '
            "GROUP BY underlying", [keys, pull]).fetchall()}

    def fetch(key: str, state: KeyState, s: Any):
        payload = http.get_json(cboe.URL.format(sym=cboe.cboe_symbol(key)), s, source="cboe")
        yield cboe.parse_chain(payload, key)

    def write(con: Any, key: str, state: KeyState, chain: Any) -> Written:
        as_of = pd.Timestamp(chain.as_of)
        quote_ts = _utc(as_of)
        base = {"source": "cboe", "pull": pull, "spot": chain.spot, "as_of": as_of.isoformat(),
                "quote_ts_utc": quote_ts.isoformat(), "quote_ts_source": chain.quote_ts_source,
                "payload_ts_utc": None if chain.payload_ts is None else pd.Timestamp(chain.payload_ts).isoformat(),
                "delay": "15 minutes (Cboe delayed quotes)"}
        if pull == "1545" and (why := pre_close_refusal(chain)):
            return Written(0, state.data_asof, {**base, "skipped": why})
        q = chain.quotes
        root = cboe.cboe_symbol(key).lstrip("_")
        std = q[q["root"] == root]
        frame = pd.DataFrame({"underlying": key, "asof": as_of.normalize(), "expiry": std["expiry"],
                              "strike": std["strike"].astype(float), "cp": std["type"], "bid": std["bid"],
                              "ask": std["ask"], "last": std["last"], "volume": std["volume"],
                              "open_interest": std["open_interest"], "source": "cboe", "spot": float(chain.spot),
                              "quote_ts": quote_ts, "vendor_iv": std["vendor_iv"], "pull": pull,
                              "quote_ts_source": chain.quote_ts_source})
        frame = frame.drop_duplicates(["expiry", "strike", "cp"], keep="first")
        insert_frame(con, INSERT, frame)
        return Written(len(frame), as_of.date(), {**base, "dropped_nonstandard_roots": int(len(q) - len(std))})

    return run_ingest(dataset="option_snapshots", keys=list(dict.fromkeys(unds)), ctx=ctx, read_state=read_state,
                      fetch=fetch, write=write,
                      notes=[f"pull {pull} ({'before' if pull == '1545' else 'after'} the close); Cboe delayed "
                             "quotes (15 min); asof is the New York date of the quotes' own time (quote_ts)"])
