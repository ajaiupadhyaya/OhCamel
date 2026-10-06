"""Admission control (compute plan B3; Review Focus 1 and 2).

A job may start only if hostd's ``mem_available`` minus a 512 MiB reserve
covers its memory class (S 256, M 640, L 1152 MiB -- contract II.2's MB read
as MiB, the conservative reading). A heavy job may not start inside
09:25-16:05 New York on a session day. Threads: 1 inside that window, 2
outside. When hostd cannot be read -- unset, unreachable, or no first
interval yet -- nothing starts: a job the worker cannot budget is a job that
could OOM the box.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Any

from ..deck.clock import NY, Clock

MiB = 2**20
RESERVE_BYTES = 512 * MiB
CLASS_BUDGET = {"S": 256 * MiB, "M": 640 * MiB, "L": 1152 * MiB}
WINDOW = (dtime(9, 25), dtime(16, 5))


@dataclass(frozen=True)
class Admission:
    allowed_classes: frozenset[str]
    allow_heavy: bool
    threads: int
    reason: str


def admit_all(now: datetime) -> Admission:
    return Admission(frozenset(CLASS_BUDGET), True, 2, "admit_all (tests only)")


def class_budget(mem_class: str) -> int:
    return CLASS_BUDGET[mem_class]


def in_heavy_window(now: datetime, clock: Clock) -> bool:
    """09:25 <= New York time < 16:05 on a session day (the clock's session, or its next open, is today)."""
    ny = now.astimezone(NY)
    today = ny.date()
    session_day = (clock.session == today.isoformat()
                   or datetime.fromisoformat(clock.next_open).astimezone(NY).date() == today)
    return session_day and WINDOW[0] <= ny.time() < WINDOW[1]


def decide(now: datetime, host: dict[str, Any] | None, clock: Clock, host_error: str | None = None) -> Admission:
    window = in_heavy_window(now, clock)
    threads = 1 if window else 2
    heavy_note = "heavy jobs wait for 16:05 New York" if window else "heavy jobs allowed"
    if host is None:
        return Admission(frozenset(), not window, threads,
                         f"host telemetry unavailable ({host_error}); nothing may start")
    latest = host.get("latest") or {}
    avail = latest.get("mem_available") if isinstance(latest, dict) else None
    if not isinstance(avail, (int, float)):
        return Admission(frozenset(), not window, threads, "hostd has no mem_available reading yet; nothing may start")
    headroom = int(avail) - RESERVE_BYTES
    allowed = frozenset(c for c, b in CLASS_BUDGET.items() if headroom >= b)
    return Admission(allowed, not window, threads,
                     f"mem_available {int(avail) // MiB} MiB - 512 reserve -> classes {''.join(sorted(allowed)) or 'none'}; "
                     f"{heavy_note}; threads {threads}")


def make_admit(read_host: Callable[[], dict[str, Any]] | None = None,
               clock: Callable[[datetime], Clock] | None = None) -> Callable[[datetime], Admission]:
    from ..api.routers.ops import HostdUnavailable

    if read_host is None:
        from ..api.routers.ops import read_host as _read_host

        read_host = _read_host
    if clock is None:
        from ..config import get_settings
        from ..deck.clock import session_clock

        def clock(t: datetime) -> Clock:
            return session_clock(get_settings(), t)

    def admit(now: datetime) -> Admission:
        clk = clock(now)
        try:
            host, err = read_host(), None
        except HostdUnavailable as e:
            host, err = None, e.detail
        return decide(now, host, clk, err)

    return admit
