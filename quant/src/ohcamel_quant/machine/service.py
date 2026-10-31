"""The Machine's service: the graph built once and kept, for ``GET /api/machine``.

``build_machine`` is the whole response but for the day's runs: the graph and its extras, one provenance
entry, and ``notes``, which say in words what is missing. It is the expensive part (a year of closes for
five hundred names, their correlations, five hundred nodes), and this is the public front page, so no
visitor waits for it but the very first. ``MachineCache`` serves the last good build. When five minutes
have passed, or a newer product has finished, one background thread rebuilds it and the old build is
served until the new one lands. A build made without the warehouse (it is locked through the nightly
write) lasts one minute, not five.

Nothing here runs a job or writes a store. The notes are made only from the loader's own ``why`` words
and the book's ``summary_why``, which carry no path (``load`` strips the warehouse's), so a public body
holds nothing the loader did not already vouch for.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..data.base import Provenance
from ..jobs.db import connect, iso
from .graph import PROD, build_graph
from .lenses import extras
from .load import load_inputs

log = logging.getLogger("ohcamel_quant.machine.service")

TTL_S = 300.0           # a build is refreshed at this age, or when a newer product has finished
DEGRADED_TTL_S = 60.0   # and at this age when it was made without the warehouse (``degraded``)
BACKOFF_S = 60.0        # a refresh that failed is not tried again for this long
SKEW_S = 60.0           # a caller's clock may read this much earlier than the build it is handed: see MachineCache


def sentence(text: str) -> str:
    """``text`` as a sentence: trimmed, its first word capitalised when it is a plain word (a name such as
    ``desk.report`` or ``book_summary`` keeps its case), and ended with a full stop."""
    text = text.strip()
    if text.split(" ", 1)[0].isalpha():
        text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _notes(why: dict, nodes: list[dict]) -> list[str]:
    """What is missing, in words: each source the loader could not read (its ``why``), then why the live
    book's limits and kill switch are not shown, when they are not. Each once, in that order."""
    book = next((n["m"] for n in nodes if n["id"] == "book"), {})
    words = [*why.values(), book.get("summary_why")]
    return list(dict.fromkeys(sentence(w) for w in words if isinstance(w, str) and w.strip()))


def build_machine(db: Path, settings: Settings, now: datetime) -> dict:
    """The response but for ``runs``: ``{asof, nodes, edges, x, provenance, notes}``. ``now`` is a
    timezone-aware time. A store that is missing or fails leaves its nodes missing, and says so in ``notes``."""
    inp = load_inputs(db, settings, now)
    graph = build_graph(inp)
    return {"asof": iso(now), **graph, "x": extras(inp),
            "provenance": [Provenance.now("ohcamel-machine", store="jobs.sqlite+warehouse").to_dict()],
            "notes": _notes(dict(inp.why), graph["nodes"])}


def degraded(machine: dict) -> bool:
    """Whether a build was made without the warehouse: every feed is missing, and no ticker has a readout."""
    feeds = [n for n in machine.get("nodes", ()) if n.get("k") == "feed"]
    return bool(feeds) and all(n.get("status") == "missing" for n in feeds)


def watermark(db: Path) -> str | None:
    """The finish time of the newest artifact of a ``PROD`` kind, which moves when a job the graph draws publishes
    and not when a visitor's public job does. None with no jobs database (this never creates one), with no such
    artifact yet, or when the store cannot be read."""
    if not db.exists():
        return None
    try:
        with closing(connect(db)) as conn:
            return conn.execute(f"SELECT MAX(finished_at) FROM artifacts WHERE kind IN ({','.join('?' * len(PROD))})",
                                tuple(PROD)).fetchone()[0]
    except sqlite3.Error:
        log.debug("jobs database unreadable; no watermark", exc_info=True)
        return None


def _in_thread(refresh: Callable[[], None]) -> None:
    threading.Thread(target=refresh, name="machine-build", daemon=True).start()


class MachineCache:
    """The last good build, refreshed in the background.

    ``get`` hands back the build it holds. When that build has passed its age (``ttl_s``, or ``degraded_ttl_s`` when
    ``degraded`` says it was made without its sources) or ``mark()`` has changed since it was built, ``get`` starts
    one refresh through ``spawn`` and still answers at once. ``spawn`` runs what it is given on another thread; a test
    hands in one that runs it there and then. Only the first request, with nothing to serve, waits for a build, and
    a burst of first requests builds once. A build that raises there goes to the caller, and the next ``get`` tries
    again. A refresh that raises keeps the old build, is logged, and is not tried again for ``backoff_s``. One build
    runs at a time, however it was started.

    ``mark`` is read on every ``get``, so it must be cheap (``watermark`` is one ``MAX`` over the artifacts table,
    which retention keeps small). It is read before a build and stored with it, so an artifact that lands while the
    graph is being built still shows at the next ``get``.

    ``now`` is the caller's clock and is timezone-aware. Each request takes its own ``now`` before it waits, so the
    build a waiter is handed can carry a later ``now`` than its own, by the milliseconds the wait took: that build is
    fresh, and an age down to ``-skew_s`` counts as zero. A ``now`` further back than that is a clock that was stepped
    backwards, and counts as expired, so it cannot pin an old graph.
    """

    def __init__(self, build: Callable[[datetime], dict], mark: Callable[[], str | None], ttl_s: float = TTL_S,
                 skew_s: float = SKEW_S, *, degraded: Callable[[dict], bool] = lambda _: False,
                 degraded_ttl_s: float = DEGRADED_TTL_S, backoff_s: float = BACKOFF_S,
                 spawn: Callable[[Callable[[], None]], None] = _in_thread) -> None:
        self._build, self._mark, self._degraded, self._spawn = build, mark, degraded, spawn
        self._ttl, self._short = timedelta(seconds=ttl_s), timedelta(seconds=degraded_ttl_s)
        self._skew, self._backoff = timedelta(seconds=skew_s), timedelta(seconds=backoff_s)
        self._building = threading.Lock()   # held for the length of a build, whichever thread runs it
        self._held: tuple[dict, datetime, str | None, timedelta] | None = None   # value, built for, its mark, its age limit
        self._retry_at: datetime | None = None

    def _make(self, now: datetime) -> dict:
        mark = self._mark()
        value = self._build(now)
        self._held = (value, now, mark, self._short if self._degraded(value) else self._ttl)
        self._retry_at = None
        return value

    def _expired(self, now: datetime) -> bool:
        _, built, marked, limit = self._held
        return marked != self._mark() or not -self._skew <= now - built < limit

    def _backing_off(self, now: datetime) -> bool:
        retry_at = self._retry_at           # read once: a refresh on another thread may clear it
        return retry_at is not None and timedelta(0) < retry_at - now <= self._backoff

    def _refresh(self, now: datetime) -> None:
        try:
            self._make(now)
        except Exception:   # noqa: BLE001 -- whatever broke, the last good build stands; the log has the rest
            log.exception("the machine could not be rebuilt; the last good build stays")
            self._retry_at = now + self._backoff
        finally:
            self._building.release()

    def refresh(self, now: datetime) -> None:
        """Start one background build, unless one is running or a failed one is backing off. It never waits or raises."""
        if self._backing_off(now) or not self._building.acquire(blocking=False):
            return
        try:
            self._spawn(lambda: self._refresh(now))
        except RuntimeError:                # no thread to be had; a later request tries again
            self._building.release()
            log.exception("the machine's refresh could not be started")

    def get(self, now: datetime) -> dict:
        if self._held is None:
            with self._building:            # whoever waited here finds the build that was made while they did
                if self._held is None:
                    return self._make(now)
        if self._expired(now):
            self.refresh(now)
        return self._held[0]
