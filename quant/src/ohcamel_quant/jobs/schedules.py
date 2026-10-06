"""The scheduler: schedules.yaml entries enqueued when due, idempotent per (name, scheduled_for).

Each tick, for each entry: the newest fire time after the entry's last
recorded run (or after now - 26 h, for an entry that has never run) and not
after now. A worker that was down for seven hourly runs enqueues one job, for
the latest. Duplicates are impossible twice over: ``schedule_runs`` has
(name, scheduled_for) as its key, and ``enqueue`` dedupes a live job.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .artifacts import latest_artifact
from .cron import Cron, latest_due, parse_cron
from .db import connect, iso, parse_iso
from .kinds import REGISTRY
from .queue import MEM_CLASSES, enqueue

log = logging.getLogger("ohcamel_quant.jobs.schedules")
DEFAULT_PATH = Path(__file__).with_name("schedules.yaml")
CATCHUP = timedelta(hours=26)
_DUR = re.compile(r"^(\d+)([smhd])$")


@dataclass(frozen=True)
class ScheduleEntry:
    name: str
    cron: str
    parsed: Cron
    kind: str
    params: dict[str, Any]
    priority: int
    mem_class: str
    heavy: bool


@dataclass(frozen=True)
class Schedules:
    entries: tuple[ScheduleEntry, ...]
    max_age_s: dict[str, float] = field(default_factory=dict)


def parse_duration(text: str) -> float:
    m = _DUR.match(str(text).strip())
    if not m:
        raise ValueError(f"duration must look like 45s, 90m, 30h or 7d: {text!r}")
    return float(int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)])


def _entry(raw: Any) -> ScheduleEntry:
    if not isinstance(raw, dict):
        raise ValueError(f"schedule entry must be a mapping: {raw!r}")
    want = {"name", "cron", "kind", "params", "priority", "mem_class", "heavy"}
    if set(raw) != want:
        raise ValueError(f"schedule entry keys must be exactly {sorted(want)}: got {sorted(raw)}")
    name = str(raw["name"])
    if raw["kind"] not in REGISTRY:
        raise ValueError(f"{name}: unknown kind {raw['kind']!r}")
    if raw["priority"] not in (1, 2):
        raise ValueError(f"{name}: priority must be 1 (intraday) or 2 (nightly)")
    if raw["mem_class"] not in MEM_CLASSES:
        raise ValueError(f"{name}: mem_class must be one of {MEM_CLASSES}")
    if not isinstance(raw["heavy"], bool):
        raise ValueError(f"{name}: heavy must be true or false")
    if not isinstance(raw["params"], dict):
        raise ValueError(f"{name}: params must be a mapping")
    return ScheduleEntry(name, str(raw["cron"]), parse_cron(str(raw["cron"])), raw["kind"], raw["params"],
                         raw["priority"], raw["mem_class"], raw["heavy"])


def load_schedules(path: Path = DEFAULT_PATH) -> Schedules:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(doc, dict) or set(doc) - {"max_age", "schedules"}:
        raise ValueError(f"{path}: top level must be max_age and schedules")
    entries = tuple(_entry(e) for e in (doc.get("schedules") or []))
    names = [e.name for e in entries]
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: duplicate schedule names")
    max_age: dict[str, float] = {}
    for kind, dur in (doc.get("max_age") or {}).items():
        if kind not in REGISTRY:
            raise ValueError(f"{path}: max_age names unknown kind {kind!r}")
        max_age[kind] = parse_duration(dur)
    return Schedules(entries, max_age)


def tick(conn: sqlite3.Connection, schedules: Schedules, now: datetime) -> list[tuple[str, str, bool]]:
    out = []
    for e in schedules.entries:
        last = conn.execute("SELECT MAX(scheduled_for) FROM schedule_runs WHERE name = ?", (e.name,)).fetchone()[0]
        after = now - CATCHUP
        if last is not None:
            after = max(after, parse_iso(last))
        due = latest_due(e.parsed, after, now)
        if due is None:
            continue
        key = iso(due)
        if conn.execute("SELECT 1 FROM schedule_runs WHERE name = ? AND scheduled_for = ?", (e.name, key)).fetchone():
            continue
        job, created = enqueue(conn, kind=e.kind, params=e.params, priority=e.priority, mem_class=e.mem_class,
                               heavy=e.heavy, submitted_by=f"schedule:{e.name}", now=now)
        conn.execute("INSERT OR IGNORE INTO schedule_runs (name, scheduled_for, job_id, enqueued_at) VALUES (?,?,?,?)",
                     (e.name, key, job.id, iso(now)))
        log.info("schedule %s (%s) -> job %s%s", e.name, key, job.id, "" if created else " (already live)")
        out.append((e.name, job.id, created))
    return out


def start_scheduler(db_path: Path, schedules: Schedules, stop: threading.Event, now: Callable[[], datetime],
                    interval_s: float = 30.0) -> threading.Thread:
    def loop() -> None:
        conn = connect(db_path)
        try:
            while not stop.is_set():
                try:
                    tick(conn, schedules, now())
                except Exception:  # noqa: BLE001 - a bad tick is logged; the next one retries
                    log.exception("scheduler tick failed")
                stop.wait(interval_s)
        finally:
            conn.close()

    t = threading.Thread(target=loop, name="ohcamel-scheduler", daemon=True)
    t.start()
    return t


def read_latest(conn: sqlite3.Connection, kind: str, params_hash: str | None = None, *, now: datetime,
                schedules: Schedules | None = None) -> dict[str, Any] | None:
    """The latest artifact of a kind as ``{"manifest", "stale"}``, stale per schedules.yaml's max_age."""
    s = schedules or load_schedules()
    return latest_artifact(conn, kind, params_hash, now=now, max_age_s=s.max_age_s.get(kind))
