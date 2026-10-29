"""The Machine's service: the graph built once and kept, for ``GET /api/machine``.

``build_machine`` is the whole response but for the day's runs: the graph and its extras, one provenance
entry, and ``notes``, which say in words what is missing. It is the expensive part (a year of closes for
five hundred names, their correlations, five hundred nodes), so ``MachineCache`` keeps it. It is rebuilt
when five minutes have passed or when a newer artifact has finished, whichever is first, under a lock, so
a burst of requests on a cold or expired cache builds once and the rest wait for that build.

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
from .graph import build_graph
from .lenses import extras
from .load import load_inputs

log = logging.getLogger("ohcamel_quant.machine.service")

TTL_S = 300.0   # the graph is at most this old, unless a newer artifact has finished since


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


def watermark(db: Path) -> str | None:
    """The finish time of the newest artifact (``MAX(finished_at)``), which moves when a job publishes.
    None with no jobs database (this never creates one), with no artifact yet, or when the store cannot be read."""
    if not db.exists():
        return None
    try:
        with closing(connect(db)) as conn:
            return conn.execute("SELECT MAX(finished_at) FROM artifacts").fetchone()[0]
    except sqlite3.Error:
        log.debug("jobs database unreadable; no watermark", exc_info=True)
        return None


class MachineCache:
    """One built graph, kept until ``ttl_s`` seconds have passed or ``mark()`` has changed since it was built.

    ``mark`` is read on every ``get``, so it must be cheap (``watermark`` is one ``MAX`` over the artifacts
    table, which retention keeps small). It is read before a build and stored with it, so an artifact that lands while the graph is
    being built still shows at the next ``get``. A build that raises is not kept: the error goes to the caller
    and the next ``get`` tries again. ``now`` is the caller's clock and is timezone-aware; a ``now`` earlier
    than the build it holds counts as expired, so a clock that went backwards cannot pin an old graph.
    """

    def __init__(self, build: Callable[[datetime], dict], mark: Callable[[], str | None], ttl_s: float = TTL_S) -> None:
        self._build, self._mark, self._ttl = build, mark, timedelta(seconds=ttl_s)
        self._lock = threading.Lock()
        self._held: tuple[dict, datetime, str | None] | None = None   # (value, built for, mark when built)

    def _fresh(self, now: datetime, mark: str | None) -> dict | None:
        held = self._held
        if held is None:
            return None
        value, built, marked = held
        return value if marked == mark and timedelta(0) <= now - built < self._ttl else None

    def get(self, now: datetime) -> dict:
        value = self._fresh(now, self._mark())
        if value is not None:
            return value
        with self._lock:   # whoever waited here finds the build that was made while they did
            mark = self._mark()
            value = self._fresh(now, mark)
            if value is None:
                value = self._build(now)
                self._held = (value, now, mark)
            return value
