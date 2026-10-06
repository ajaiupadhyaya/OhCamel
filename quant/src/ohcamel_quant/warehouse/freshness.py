"""When is warehouse data current? Session datasets are judged against the
New York session clock; exchange holidays are not known, so a session counts as
covered when the last successful ingest ran after it was due and found nothing
newer (one session of slack, never more)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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


def expected_session(now: datetime, due: time) -> date:
    """The latest weekday whose ``due`` New York time has passed at ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ny = now.astimezone(NY)
    d = ny.date()
    if d.weekday() >= 5 or ny.time() < due:
        d = _prev_weekday(d)  # Sat/Sun -> Fri; a weekday before `due` -> the previous weekday
    return d


def is_fresh(data_asof: date | None, last_ok_ran_at: datetime | None, now: datetime, due: time) -> bool:
    """``data_asof`` reaches the expected session, or it is exactly one session
    behind and the last successful ingest ran after that session was due."""
    if data_asof is None:
        return False
    exp = expected_session(now, due)
    if data_asof >= exp:
        return True
    if last_ok_ran_at is None or data_asof < _prev_weekday(exp):
        return False
    ran = last_ok_ran_at if last_ok_ran_at.tzinfo else last_ok_ran_at.replace(tzinfo=UTC)
    return ran >= datetime.combine(exp, due, NY)


DATASETS = ("universe_members", "bars_daily", "bars_minute", "fred", "factors", "sec_facts", "holdings_13f",
            "option_snapshots")
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
}
SUCCESS = ("ok", "partial")


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def dataset_freshness(con: Any, now: datetime) -> dict[str, Any]:
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
        last_run, last_status, last_ok = runs.get(ds, (None, None, None))
        rule = FRESHNESS[ds]
        if rule["rule"] == "session":
            stale = not is_fresh(asof, last_ok, now, rule["due"])
        else:
            ok_at = last_ok.replace(tzinfo=UTC) if last_ok is not None else None
            stale = ok_at is None or (now - ok_at).total_seconds() > rule["max_age_h"] * 3600
        out[ds] = {"data_asof": _iso(asof), "last_run_at": _iso(last_run), "last_status": last_status,
                   "last_ok_at": _iso(last_ok), "keys": len(mine),
                   "keys_behind": sum(1 for _, _, d in mine if asof is not None and (d is None or d < asof)),
                   "keys_failed": sum(1 for _, st, _ in mine if st == "failed"), "stale": stale, "rule": rule["rule"]}
    return {"now": now.isoformat(), "datasets": out}
