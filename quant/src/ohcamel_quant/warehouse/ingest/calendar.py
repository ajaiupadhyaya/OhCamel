"""The exchange calendar's ingest (completion task 3.6), run inside ``ingest.bars_daily`` (weekdays 18:30 New York),
the way ``fred_vintages`` is appended inside ``ingest.fred_warehouse``: no new job kind, no new schedule.

One key, ``alpaca``: GET ``/v2/calendar`` on the paper trading API for a window, every session in it written
(``warehouse/calendar.py``). The first fetch -- an empty table -- asks from ``FULL_START`` (1990, the bars' own
default start), so ``missing_session`` can judge a whole series; after that the window is the last
``LOOKBACK_DAYS`` and the next ``AHEAD_DAYS``, so a closure Alpaca adds later replaces its day.

It runs BEFORE the bars pass (fix round 1, I3), so a bars pass killed at its wall limit cannot starve it.
``data_asof`` is today at most; the last session cached is in the detail (fix round 1, M5). A host without
keys logs nothing (an announced weekday fallback, not a fault); offline or a vendor error fails the key with the
vendor's words, and the bars run records a note and is not failed by it (a reference table, not a series).
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
    """The refresh. Without Alpaca keys it raises before touching ``ingest_log`` (fix round 1, M5): a keyless host
    has no calendar by configuration, which freshness announces as a weekday fallback (a note), not a failed run
    (a fault lamp on the front page every night)."""
    s = ctx.market.settings
    if not (s.alpaca_key_id and s.alpaca_secret_key):
        raise DataUnavailable("no Alpaca keys on this host (APCA_API_KEY_ID / APCA_API_SECRET_KEY)")
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
        # data_asof is today at most (fix round 1, M5): the calendar runs ~400 days ahead, and a data date in the
        # next year on the front page would read as a defect; the last session cached is in the detail
        return Written(n, min(last, today_ny) if last is not None else None,
                       {"source": "alpaca", "window_start": span["start"].isoformat(),
                        "window_end": span["end"].isoformat(), "last_session": str(last),
                        "endpoint": C.ALPACA_CALENDAR})

    return run_ingest(dataset=C.DATASET, keys=["alpaca"], ctx=ctx, read_state=read_state, fetch=fetch, write=write,
                      notes=["exchange_calendar: Alpaca's /v2/calendar (holidays left out, early closes stated); "
                             "data_asof is today, the last session cached (ahead of it) is in the log's detail"])


def with_calendar(run_bars: Any) -> Any:
    """``ingest.bars_daily``'s handler with the calendar refresh in the same job, FIRST (fix round 1, I3): the bars
    pass may be killed at its wall limit, and the calendar must not depend on it finishing. Not on a run for named
    tickers (a hand-run repair has no business refreshing a reference table). A failed refresh, or a host without
    Alpaca keys, is a note on the bars' run, never its failure."""
    def run(params: dict, ctx: Any) -> dict:
        cal: dict | None = None
        note: str | None = None
        if not params.get("tickers"):
            try:
                cal = run_calendar(ctx)
            except DataUnavailable as e:        # IngestFailed included
                note = (f"exchange_calendar not refreshed (freshness keeps the cached one, or counts weekdays and "
                        f"says so): {e}")
        spec = run_bars(params, ctx)
        if note:
            spec.setdefault("notes", []).append(note)
        if cal is not None:
            spec.setdefault("notes", []).extend(cal.get("notes", []))
            spec.setdefault("provenance", []).extend(cal.get("provenance", []))
            summary = (cal.get("tables") or {}).get("ingest_summary")
            if summary is not None:
                tables = spec.setdefault("tables", {})
                if "ingest_summary" in tables:
                    import pandas as pd

                    tables["ingest_summary"] = pd.concat([tables["ingest_summary"], summary], ignore_index=True)
                else:
                    tables["ingest_summary"] = summary
        return spec

    run.__name__ = getattr(run_bars, "__name__", "run_bars_daily")
    run.__doc__ = run_bars.__doc__
    run.__wrapped__ = run_bars   # type: ignore[attr-defined]
    return run
