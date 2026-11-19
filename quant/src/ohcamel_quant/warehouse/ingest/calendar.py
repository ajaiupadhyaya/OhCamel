"""The exchange calendar's ingest (completion task 3.6), run inside ``ingest.bars_daily`` (weekdays 18:30 New York),
the way ``fred_vintages`` is appended inside ``ingest.fred_warehouse``: no new job kind, no new schedule.

One key, ``alpaca``: GET ``/v2/calendar`` on the paper trading API for a window, every session in it written
(``warehouse/calendar.py``). The first fetch -- an empty table -- asks from ``FULL_START`` (1990, the bars' own
default start), so ``missing_session`` can judge a whole series; after that the window is the last
``LOOKBACK_DAYS`` and the next ``AHEAD_DAYS``, so a closure Alpaca adds later replaces its day. ``data_asof`` is
the last session the cache holds (a date ahead of today: the calendar is published in advance).

No keys, offline, or a vendor error: the key fails with the vendor's words, and the bars ingest that carried it
records a note and is not failed by it (the calendar is a reference table, not a market series).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ...data import http
from ...data.base import DataUnavailable
from .. import calendar as C
from .. import freshness
from .base import KeyState, Written, run_ingest, utcnow

FULL_START = date(1990, 1, 1)
LOOKBACK_DAYS = 30
AHEAD_DAYS = 400


def fetch_calendar(settings: Any, start: date, end: date) -> Any:
    """The raw ``/v2/calendar`` JSON for ``[start, end]``. Raises :class:`DataUnavailable` without keys."""
    if not (settings.alpaca_key_id and settings.alpaca_secret_key):
        raise DataUnavailable("alpaca calendar: no Alpaca keys (APCA_API_KEY_ID / APCA_API_SECRET_KEY)")
    from ...data.prices import _alpaca_headers

    return http.get_json(C.ALPACA_CALENDAR, settings, params={"start": start.isoformat(), "end": end.isoformat()},
                         headers=_alpaca_headers(settings), source="alpaca")


def window(con: Any, today: date) -> tuple[date, date]:
    """The range to ask for: from 1990 while the cache holds nothing that old, else the last month."""
    first = con.execute('SELECT min("date") FROM exchange_calendar').fetchone()[0]
    start = FULL_START if first is None or first > FULL_START + timedelta(days=7) else today - timedelta(
        days=LOOKBACK_DAYS)
    return start, today + timedelta(days=AHEAD_DAYS)


def run_calendar(ctx: Any) -> dict:
    today_ny = freshness._now().astimezone(freshness.NY).date()
    span: dict[str, date] = {}

    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        span["start"], span["end"] = window(con, today_ny)
        return {}

    def fetch(key: str, state: KeyState, settings: Any) -> Any:
        payload = fetch_calendar(settings, span["start"], span["end"])
        yield C.parse_alpaca_calendar(payload)

    def write(con: Any, key: str, state: KeyState, frame: Any) -> Written:
        n = C.write_calendar(con, frame, span["start"], span["end"], source="alpaca", fetched_at=utcnow())
        last = con.execute('SELECT max("date") FROM exchange_calendar').fetchone()[0]
        return Written(n, last, {"source": "alpaca", "window_start": span["start"].isoformat(),
                                 "window_end": span["end"].isoformat(), "endpoint": C.ALPACA_CALENDAR})

    return run_ingest(dataset=C.DATASET, keys=["alpaca"], ctx=ctx, read_state=read_state, fetch=fetch, write=write,
                      notes=["exchange_calendar: Alpaca's /v2/calendar (holidays left out, early closes stated); "
                             "data_asof is the last session cached, ahead of today"])


def with_calendar(run_bars: Any) -> Any:
    """``ingest.bars_daily``'s handler, then the calendar refresh in the same job (not on a run for named tickers:
    a hand-run repair has no business refreshing a reference table). A failed refresh is a note on the bars' run."""
    from .base import IngestFailed

    def run(params: dict, ctx: Any) -> dict:
        spec = run_bars(params, ctx)
        if params.get("tickers"):
            return spec
        try:
            cal = run_calendar(ctx)
        except IngestFailed as e:
            spec.setdefault("notes", []).append(f"exchange_calendar not refreshed (freshness keeps the cached one, "
                                                f"or counts weekdays and says so): {e}")
            return spec
        spec.setdefault("notes", []).extend(cal["notes"])
        spec.setdefault("provenance", []).extend(cal["provenance"])
        tables = spec.setdefault("tables", {})
        if "ingest_summary" in tables:
            import pandas as pd

            tables["ingest_summary"] = pd.concat([tables["ingest_summary"], cal["tables"]["ingest_summary"]],
                                                 ignore_index=True)
        else:
            tables["ingest_summary"] = cal["tables"]["ingest_summary"]
        return spec

    run.__name__ = getattr(run_bars, "__name__", "run_bars_daily")
    run.__doc__ = run_bars.__doc__
    run.__wrapped__ = run_bars   # type: ignore[attr-defined]
    return run
