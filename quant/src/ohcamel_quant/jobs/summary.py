"""The jobs block of /api/ops (compute plan B6)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .db import iso


def jobs_summary(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    q = conn.execute("SELECT COALESCE(SUM(state='queued'),0), COALESCE(SUM(state='running'),0) FROM jobs").fetchone()
    d = conn.execute("SELECT COALESCE(SUM(state='done'),0), COALESCE(SUM(state='failed'),0),"
                     " COALESCE(SUM(cpu_seconds),0) FROM jobs WHERE state IN ('done','failed') AND finished_at >= ?",
                     (iso(now - timedelta(hours=24)),)).fetchone()
    return {"queued": int(q[0]), "running": int(q[1]), "done_24h": int(d[0]), "failed_24h": int(d[1]),
            "cpu_seconds_24h": float(d[2])}
