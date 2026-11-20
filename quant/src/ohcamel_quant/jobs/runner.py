"""Run one job in a spawned child and measure it.

* cpu_seconds: ``RUSAGE_CHILDREN`` (ru_utime + ru_stime) after minus before.
* peak_rss_bytes: the child's own ``RUSAGE_SELF`` maximum, sent back with the
  result. ``RUSAGE_CHILDREN``'s ru_maxrss is the largest child reaped so far
  (it never falls), so it is used only when the child died without a result,
  as an upper bound.
* Cancel (``beat()`` returns 'cancelled') or stop (``stop`` set): the child's
  cancel event is set; after ``grace_s`` its process group is terminated, then killed. Whether the child
  stopped in time or not, its group is SIGKILLed afterwards, so nothing it started outlives a cancel or a
  stop (fix round 1, I2).
* Wall limit (completion task 3.6): a run older than ``wall_limit_s`` is not asked -- a hung job does not
  read its cancel event -- it is killed at once with its whole process group (the child makes itself a
  group leader first thing, jobs/child.py, so whatever it started, a Kronos subprocess say, goes with it):
  SIGTERM to the group, then SIGKILL after ``KILL_AFTER_S``. A process's locks die with it, the warehouse
  writer's among them (DuckDB holds an OS file lock). The outcome is ``failed`` with a reason naming the
  wall limit; the worker settles it like any failure and takes the next job.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import resource
import shutil
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .child import child_main
from .queue import Job
from .rusage import maxrss_bytes


@dataclass
class Outcome:
    status: Literal["done", "failed", "cancelled", "interrupted"]
    result: dict[str, Any] | None
    error: str | None
    cpu_seconds: float
    peak_rss_bytes: int | None
    staging: Path


#: Seconds between the SIGTERM to a job's process group and the SIGKILL, at its wall limit.
KILL_AFTER_S = 3.0


def group_of(proc: Any) -> int | None:
    """The child's own process group id, once it leads one (it calls ``setpgid(0, 0)`` first thing): ``None``
    before then, after it is gone, or if its group is this process's -- the worker's own group is never a target."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return None
    return pgid if pgid == proc.pid and pgid != os.getpgrp() else None


def _signal(proc: Any, pgid: int | None, sig: int) -> None:
    """``sig`` to the recorded group when there is one -- whether or not its leader is still alive: a member that
    ignored the SIGTERM outlives the leader's exit -- else to the child alone."""
    if pgid is not None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        return
    try:
        os.kill(proc.pid, sig)
    except ProcessLookupError:
        pass


def kill_group(proc: Any, kill_after_s: float = KILL_AFTER_S, pgid: int | None = None) -> None:
    """SIGTERM the child's process group, wait ``kill_after_s``, then SIGKILL the same group. The group is looked
    up ONCE, before the first signal (fix round 1, I1): after the SIGTERM the wait reaps the leader, and a second
    lookup would find nothing and miss the members still running. ``pgid`` passes a group recorded earlier."""
    if pgid is None:
        pgid = group_of(proc)
    _signal(proc, pgid, signal.SIGTERM)
    proc.join(kill_after_s)
    _signal(proc, pgid, signal.SIGKILL)
    proc.join(5)


def run_in_child(job: Job, *, threads: int, db_path: Path, staging_root: Path, stop: threading.Event,
                 beat: Callable[[], str], heartbeat_s: float = 15.0, grace_s: float = 15.0,
                 wall_limit_s: float | None = None) -> Outcome:
    ctx = mp.get_context("spawn")
    staging = staging_root / job.id
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    cancel = ctx.Event()
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = ctx.Process(target=child_main, name=f"job-{job.id}",
                       args=(job.to_dict(), threads, str(staging), str(db_path), cancel, child_conn))
    proc.start()
    child_conn.close()
    msg: dict[str, Any] | None = None
    reason: str | None = None
    deadline: float | None = None
    started = time.monotonic()
    next_beat = started + heartbeat_s
    pgid: int | None = None          # the child's group, recorded once it leads one (while it is surely alive)
    while True:
        if pgid is None:
            pgid = group_of(proc)
        if parent_conn.poll(0.2):
            try:
                msg = parent_conn.recv()
            except EOFError:
                msg = None
            break
        if not proc.is_alive():
            if parent_conn.poll(0.2):
                try:
                    msg = parent_conn.recv()
                except EOFError:
                    msg = None
            break
        t = time.monotonic()
        if wall_limit_s is not None and reason in (None, "cancelled", "interrupted") and t - started >= wall_limit_s:
            # a stop or cancel already in its grace still ends here: the limit is the outer bound
            reason = reason or "wall_limit"
            kill_group(proc, pgid=pgid)
            break
        if reason is None and stop.is_set():
            reason, deadline = "interrupted", t + grace_s
            cancel.set()
        if t >= next_beat:
            next_beat = t + heartbeat_s
            if beat() == "cancelled" and reason is None:
                reason, deadline = "cancelled", t + grace_s
                cancel.set()
        if deadline is not None and t >= deadline:
            kill_group(proc, 3.0, pgid=pgid)      # fix round 1, I2: the whole group, not the child alone
            break
    proc.join(5)
    if proc.is_alive():
        kill_group(proc, 0.0, pgid=pgid)
    if reason is not None and pgid is not None:
        # a cancelled, stopped or killed run leaves nothing it started running, even when the child itself
        # exited in time (fix round 1, I2): a cancelled kronos.shadow's subprocess, say
        _signal(proc, pgid, signal.SIGKILL)
    parent_conn.close()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    peak = msg.get("peak_rss_bytes") if msg else None
    if peak is None:
        peak = maxrss_bytes(after.ru_maxrss)
    if reason == "wall_limit":
        return Outcome("failed", None, (f"wall limit: the job ran past its wall-clock limit of {wall_limit_s:g} s "
                                        f"and was killed with its process group"), cpu, peak, staging)
    if msg and msg.get("ok") and reason != "cancelled":
        return Outcome("done", msg, None, cpu, peak, staging)
    if reason == "interrupted":
        return Outcome("interrupted", msg, "interrupted by worker shutdown", cpu, peak, staging)
    if reason == "cancelled" or (msg and msg.get("cancelled")):
        return Outcome("cancelled", msg, "cancelled", cpu, peak, staging)
    error = (msg or {}).get("error") or (
        f"the job's process exited with code {proc.exitcode} without a result (killed, or out of memory?)")
    return Outcome("failed", msg, error, cpu, peak, staging)
