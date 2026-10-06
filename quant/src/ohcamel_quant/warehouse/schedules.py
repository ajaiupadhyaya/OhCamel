"""Lane C's schedule entries and max ages, in jobs/schedules.yaml's shape
(compute plan B4). ``SCHEDULES`` entries carry exactly B4's seven keys (name,
cron in New York time, kind, params, priority, mem_class, heavy). ``MAX_AGE``
is the top-level ``max_age:`` map: kind -> plain duration, which Lane B
compares as ``now - finished_at > max_age(kind)``. The integration step copies
both into schedules.yaml; this lane does not edit Lane B's files.

Session kinds get 78h, so Friday's artifact (about 72 h old by Monday's run)
is not stale every weekend; a missed weekday run shows amber after about three
days. Session-precise staleness (stale the morning after one missed run) is
``GET /api/warehouse/freshness``. Calendar kinds use the endpoint's own ages:
FRED 30h (daily), weekly kinds 204h."""

from __future__ import annotations

from .ingest.options import OPTION_UNDERLYINGS

KIND_DATASET = {
    "ingest.universes": "universe_members", "ingest.bars_daily": "bars_daily", "ingest.bars_minute": "bars_minute",
    "ingest.fred_warehouse": "fred", "ingest.factors": "factors", "ingest.sec_facts": "sec_facts",
    "ingest.holdings_13f": "holdings_13f", "ingest.option_snapshots": "option_snapshots",
}

SCHEDULES: list[dict] = [
    {"name": "warehouse-universes", "cron": "0 5 * * 0", "kind": "ingest.universes", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-bars-daily", "cron": "30 18 * * 1-5", "kind": "ingest.bars_daily", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-bars-minute", "cron": "0 19 * * 1-5", "kind": "ingest.bars_minute", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-fred", "cron": "0 7 * * *", "kind": "ingest.fred_warehouse", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-factors", "cron": "0 6 * * 6", "kind": "ingest.factors", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-sec-facts", "cron": "0 2 * * 0", "kind": "ingest.sec_facts", "params": {},
     "priority": 2, "mem_class": "M", "heavy": True},
    {"name": "warehouse-13f", "cron": "0 3 * * 0", "kind": "ingest.holdings_13f", "params": {},
     "priority": 2, "mem_class": "S", "heavy": False},
    {"name": "warehouse-option-snapshots", "cron": "20 16 * * 1-5", "kind": "ingest.option_snapshots",
     "params": {"underlyings": list(OPTION_UNDERLYINGS)},
     "priority": 2, "mem_class": "S", "heavy": False},
]

MAX_AGE: dict[str, str] = {
    "ingest.universes": "204h",
    "ingest.bars_daily": "78h",
    "ingest.bars_minute": "78h",
    "ingest.fred_warehouse": "30h",
    "ingest.factors": "204h",
    "ingest.sec_facts": "204h",
    "ingest.holdings_13f": "204h",
    "ingest.option_snapshots": "78h",
}
