"""When is warehouse data current? Session datasets are judged against the
New York session clock, on the exchange calendar cached in the warehouse
(completion task 3.6: Alpaca's ``/v2/calendar``, ``warehouse/calendar.py``) --
holidays are not sessions, and the day after Thanksgiving is a session. Where
the calendar is missing or does not cover a date, sessions are counted as
weekdays and the answer says so (``calendar.note``). Either way a session counts
as covered when the last successful ingest ran after it was due and found
nothing newer (one session of slack, never more): a vendor that publishes late."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .calendar import Calendar, load_calendar
from .db import move_incomplete

__all__ = ["Calendar", "load_calendar", "expected_session", "is_fresh", "dataset_freshness", "calendar_status"]

NY = ZoneInfo("America/New_York")
#: A session's daily bars are expected from vendors by 16:20 New York
#: (16:00 close + Alpaca's 16-minute SIP delay + slack).
VENDOR_DUE = time(16, 20)


def _now() -> datetime:
    """Current UTC time (indirection so tests can pin the clock)."""
    return datetime.now(UTC)


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _prev_session(d: date, cal: Calendar | None) -> date:
    """The session before ``d``: the calendar's where it covers ``d`` and has one before it, else the weekday's."""
    if cal is not None and cal.covers(d):
        p = cal.previous(d)
        if p is not None:
            return p
    return _prev_weekday(d)


def expected_session(now: datetime, due: time, cal: Calendar | None = None) -> date:
    """The latest session whose ``due`` New York time has passed at ``now``: on the calendar where it covers
    today, else the latest such weekday."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ny = now.astimezone(NY)
    d = ny.date()
    if cal is not None and cal.covers(d):
        if cal.is_session(d) and ny.time() >= due:
            return d
        p = cal.previous(d)
        if p is not None:
            return p
    if d.weekday() >= 5 or ny.time() < due:
        d = _prev_weekday(d)  # Sat/Sun -> Fri; a weekday before `due` -> the previous weekday
    return d


def is_fresh(data_asof: date | None, last_ok_ran_at: datetime | None, now: datetime, due: time,
             cal: Calendar | None = None) -> bool:
    """``data_asof`` reaches the expected session, or it is exactly one session
    behind and the last successful ingest ran after that session was due."""
    if data_asof is None:
        return False
    exp = expected_session(now, due, cal)
    if data_asof >= exp:
        return True
    if last_ok_ran_at is None or data_asof < _prev_session(exp, cal):
        return False
    ran = last_ok_ran_at if last_ok_ran_at.tzinfo else last_ok_ran_at.replace(tzinfo=UTC)
    return ran >= datetime.combine(exp, due, NY)


DATASETS = ("universe_members", "bars_daily", "bars_minute", "fred", "factors", "sec_facts", "holdings_13f",
            "option_snapshots", "corp_actions", "universe_membership", "fred_vintages", "exchange_calendar")
#: A dataset written inside another's ingest run, so judged by that run's summary row (completion task 3.5:
#: ingest.universes writes the dated membership; ingest.fred_warehouse appends the vintages under its own run).
RUN_OF = {"universe_membership": "universe_members"}
#: Session datasets are due 3 h after their schedule (warehouse/schedules.py); calendar
#: datasets are stale when the last successful run is older than one missed run allows.
FRESHNESS: dict[str, dict[str, Any]] = {
    "universe_members": {"rule": "age", "max_age_h": 204},
    "bars_daily": {"rule": "session", "due": time(21, 30)},
    "bars_minute": {"rule": "session", "due": time(22, 0)},
    "fred": {"rule": "age", "max_age_h": 30},
    "factors": {"rule": "age", "max_age_h": 204},
    "sec_facts": {"rule": "age", "max_age_h": 204},
    "holdings_13f": {"rule": "age", "max_age_h": 204},
    "option_snapshots": {"rule": "session", "due": time(19, 20)},
    "corp_actions": {"rule": "session", "due": time(21, 20)},
    "universe_membership": {"rule": "age", "max_age_h": 204},
    "fred_vintages": {"rule": "age", "max_age_h": 30},
    # completion task 3.6: refreshed inside the weekday bars run (ingest/calendar.py), so aged like a session kind
    "exchange_calendar": {"rule": "age", "max_age_h": 78},
    # completion task 3.8: ingest.delisted's own rule (weekly), kept out of DATASETS: the job runs only while
    # OHCAMEL_DELISTED_SOURCE is on, and its rows are bars_daily's, judged there; a lamp that is never lit while the
    # switch is off would be a lamp always red.
    "delisted": {"rule": "age", "max_age_h": 204},
}
SUCCESS = ("ok", "partial")


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def calendar_status(cal: Calendar | None, now: datetime) -> dict[str, Any]:
    """What the session rule stood on: ``{"source": "alpaca", "first", "last"}``, plus a ``note`` whenever any part
    of the answer fell back to counting weekdays."""
    today = now.astimezone(NY).date()
    if cal is None:
        return {"source": "weekdays", "first": None, "last": None,
                "note": "no exchange calendar cached in the warehouse (exchange_calendar is empty or predates "
                        "migration 005; the nightly bars run fetches it from Alpaca's /v2/calendar): sessions are "
                        "counted as weekdays, so holidays and early closes are not known"}
    out: dict[str, Any] = {"source": cal.source, "first": cal.first.isoformat(), "last": cal.last.isoformat()}
    if not cal.covers(today):
        out["note"] = (f"the cached exchange calendar covers {cal.first} to {cal.last}, not today ({today}): "
                       "sessions outside it are counted as weekdays")
    return out


def dataset_freshness(con: Any, now: datetime) -> dict[str, Any]:
    cal = load_calendar(con)
    keys = con.execute(
        "SELECT dataset, key, status, data_asof FROM (SELECT *, row_number() OVER "
        "(PARTITION BY dataset, key ORDER BY ran_at DESC) AS rn FROM ingest_log WHERE key <> '*') WHERE rn = 1"
    ).fetchall()
    runs = {d: (ran, st, ok) for d, ran, st, ok in con.execute(
        "SELECT dataset, max(ran_at), arg_max(status, ran_at), max(ran_at) FILTER (WHERE status IN ('ok', 'partial')) "
        "FROM ingest_log WHERE key = '*' GROUP BY dataset").fetchall()}
    out: dict[str, Any] = {}
    for ds in DATASETS:
        mine = [(k, st, d) for dd, k, st, d in keys if dd == ds]
        dates = [d for _, _, d in mine if d is not None]
        asof = max(dates) if dates else None
        last_run, last_status, last_ok = runs.get(RUN_OF.get(ds, ds), (None, None, None))
        rule = FRESHNESS[ds]
        if rule["rule"] == "session":
            stale = not is_fresh(asof, last_ok, now, rule["due"], cal)
        else:
            ok_at = last_ok.replace(tzinfo=UTC) if last_ok is not None else None
            stale = ok_at is None or (now - ok_at).total_seconds() > rule["max_age_h"] * 3600
        out[ds] = {"data_asof": _iso(asof), "last_run_at": _iso(last_run), "last_status": last_status,
                   "last_ok_at": _iso(last_ok), "keys": len(mine),
                   "keys_behind": sum(1 for _, _, d in mine if asof is not None and (d is None or d < asof)),
                   "keys_failed": sum(1 for _, st, _ in mine if st == "failed"), "stale": stale, "rule": rule["rule"]}
    # fix round 1, M5: a calendar never fetched (a host without Alpaca keys logs no attempt) is an announced
    # weekday fallback -- not stale, the reason on the row -- not a lamp that reads stale every night. A host with
    # keys whose fetch fails logs a failed run, which shows as the failure it is.
    status = calendar_status(cal, now)
    ec = out["exchange_calendar"]
    if ec["last_run_at"] is None and ec["keys"] == 0:
        ec["stale"] = False
    if status.get("note"):
        ec["note"] = status["note"]
    # task 3.4: while migration 003's option-table move is unfinished the table holds part of the history; the
    # dataset reads "migrating" (and stale), not a figure for a part
    if move_incomplete(con):
        out["option_snapshots"] |= {"migrating": True, "stale": True}
    return {"now": now.isoformat(), "datasets": out, "calendar": status}
