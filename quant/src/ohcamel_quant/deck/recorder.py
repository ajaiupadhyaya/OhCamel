"""The flight recorder: the reference book's readings, one a minute through
the session, kept in SQLite so the deck's tape survives reloads, restarts and
viewers.

``Store`` is the database (stdlib ``sqlite3``, WAL, one connection per call so
the loop thread and request threads never share one). ``tick`` is one pass of
the workflow and is what the tests drive; ``start``/``stop`` run it on a
daemon thread beside the data refresher. The loop computes nothing itself: it
is handed ``compute`` (the router's reading function, so the tape and the page
are the same arithmetic) and ``clock``.

Schema::

    readings(book TEXT, session TEXT, ts TEXT, payload TEXT, PRIMARY KEY (book, ts))
    INDEX readings_session ON readings(book, session)

``payload`` is the compact reading from :func:`compact`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .clock import Clock

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    book TEXT NOT NULL,
    session TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (book, ts)
);
CREATE INDEX IF NOT EXISTS readings_session ON readings(book, session);
"""


def compact(reading: dict[str, Any]) -> dict[str, Any]:
    """The part of a full reading worth a row: the numbers the tape draws."""
    return {
        "day_pnl_usd": reading["book"].get("day_pnl_usd"),
        "equity_usd": reading["book"].get("equity_usd"),
        "var_usd": reading["risk"].get("var_usd"),
        "es_usd": reading["risk"].get("es_usd"),
        "utilisation": {lim["name"]: lim["utilisation"] for lim in reading["limits"]},
        "breached": [lim["name"] for lim in reading["limits"] if lim["breached"]],
        "change_pct": {m["ticker"]: m.get("change_pct") for m in reading["marks"]},
        "price": {m["ticker"]: m.get("price") for m in reading["marks"]},
    }


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record(self, book: str, session: str, ts: datetime, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as c, c:
            c.execute("INSERT OR REPLACE INTO readings (book, session, ts, payload) VALUES (?, ?, ?, ?)",
                      (book, session, ts.astimezone(UTC).isoformat(), json.dumps(payload, allow_nan=False)))

    def sessions(self, book: str) -> list[str]:
        with closing(self._connect()) as c:
            rows = c.execute("SELECT DISTINCT session FROM readings WHERE book = ? ORDER BY session DESC",
                             (book,)).fetchall()
        return [r[0] for r in rows]

    def tape(self, book: str, session: str) -> list[tuple[str, dict[str, Any]]]:
        with closing(self._connect()) as c:
            rows = c.execute("SELECT ts, payload FROM readings WHERE book = ? AND session = ? ORDER BY ts",
                             (book, session)).fetchall()
        return [(ts, json.loads(p)) for ts, p in rows]

    def prune(self, book: str, keep_sessions: int) -> int:
        """Delete all but the newest ``keep_sessions`` sessions; returns rows deleted."""
        keep = self.sessions(book)[:keep_sessions]
        with closing(self._connect()) as c, c:
            if not keep:
                return 0
            marks = ",".join("?" * len(keep))
            cur = c.execute(f"DELETE FROM readings WHERE book = ? AND session NOT IN ({marks})", (book, *keep))
            return cur.rowcount

    def stats(self) -> dict[str, Any]:
        with closing(self._connect()) as c:
            rows, last = c.execute("SELECT COUNT(*), MAX(ts) FROM readings").fetchone()
            sessions = c.execute("SELECT COUNT(DISTINCT session) FROM readings").fetchone()[0]
        return {"rows": rows, "sessions": sessions, "last_write": last}


def tape_columns(rows: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Rows -> the column-major tape the page draws. A limit or ticker absent
    from a row (the book or its limits changed mid-session) is ``None`` there."""
    names = list(dict.fromkeys(k for _, p in rows for k in p.get("utilisation", {})))
    tickers = list(dict.fromkeys(k for _, p in rows for k in p.get("change_pct", {})))
    return {
        "n": len(rows),
        "ts": [ts for ts, _ in rows],
        "day_pnl_usd": [p.get("day_pnl_usd") for _, p in rows],
        "equity_usd": [p.get("equity_usd") for _, p in rows],
        "var_usd": [p.get("var_usd") for _, p in rows],
        "es_usd": [p.get("es_usd") for _, p in rows],
        "utilisation": {n: [p.get("utilisation", {}).get(n) for _, p in rows] for n in names},
        "change_pct": {t: [p.get("change_pct", {}).get(t) for _, p in rows] for t in tickers},
    }


class Recorder:
    """One pass of the workflow (:meth:`tick`), and the thread that repeats it."""

    def __init__(self, store: Store, compute: Callable[[], dict[str, dict[str, Any]]],
                 clock: Callable[[], Clock], interval_s: float = 60.0, keep_sessions: int = 30) -> None:
        self.store = store
        self.compute = compute
        self.clock = clock
        self.interval_s = interval_s
        self.keep_sessions = keep_sessions
        self.last_error: str | None = None
        self.last_tick: str | None = None
        self._pruned_for: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None) -> int:
        """Record every book once if the session is open; after the close,
        prune once per session. Returns the number of rows written."""
        now = now or datetime.now(UTC)
        self.last_tick = now.astimezone(UTC).isoformat()
        clk = self.clock()
        if not clk.is_open:
            if self._pruned_for != clk.session:
                for book in self.compute_books():
                    self.store.prune(book, self.keep_sessions)
                self._pruned_for = clk.session
            return 0
        written = 0
        for book, reading in self.compute().items():
            self.store.record(book, clk.session, now, compact(reading))
            written += 1
        self.last_error = None
        return written

    def compute_books(self) -> list[str]:
        with closing(self.store._connect()) as c:
            return [r[0] for r in c.execute("SELECT DISTINCT book FROM readings").fetchall()]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - a failed tick must not end the recorder
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("flight recorder tick failed")
            if self._stop.wait(self.interval_s):
                return

    def start(self, initial_delay_s: float = 10.0) -> None:
        if self.running:
            return
        self._stop.clear()

        def run() -> None:
            if not self._stop.wait(initial_delay_s):
                self._loop()

        self._thread = threading.Thread(target=run, name="ohcamel-flight-recorder", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
