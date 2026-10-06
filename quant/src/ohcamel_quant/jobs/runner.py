"""Run one job in a spawned child and measure it.

* cpu_seconds: ``RUSAGE_CHILDREN`` (ru_utime + ru_stime) after minus before.
* peak_rss_bytes: the child's own ``RUSAGE_SELF`` maximum, sent back with the
  result. ``RUSAGE_CHILDREN``'s ru_maxrss is the largest child reaped so far
  (it never falls), so it is used only when the child died without a result,
  as an upper bound.
* Cancel (``beat()`` returns 'cancelled') or stop (``stop`` set): the child's
  cancel event is set; after ``grace_s`` it is terminated, then killed.
"""

from __future__ import annotations

import multiprocessing as mp
import resource
import shutil
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


def run_in_child(job: Job, *, threads: int, db_path: Path, staging_root: Path, stop: threading.Event,
                 beat: Callable[[], str], heartbeat_s: float = 15.0, grace_s: float = 15.0) -> Outcome:
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
    next_beat = time.monotonic() + heartbeat_s
    while True:
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
        if reason is None and stop.is_set():
            reason, deadline = "interrupted", t + grace_s
            cancel.set()
        if t >= next_beat:
            next_beat = t + heartbeat_s
            if beat() == "cancelled" and reason is None:
                reason, deadline = "cancelled", t + grace_s
                cancel.set()
        if deadline is not None and t >= deadline:
            proc.terminate()
            proc.join(3)
            if proc.is_alive():
                proc.kill()
            break
    proc.join(5)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
    parent_conn.close()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    peak = msg.get("peak_rss_bytes") if msg else None
    if peak is None:
        peak = maxrss_bytes(after.ru_maxrss)
    if msg and msg.get("ok") and reason != "cancelled":
        return Outcome("done", msg, None, cpu, peak, staging)
    if reason == "interrupted":
        return Outcome("interrupted", msg, "interrupted by worker shutdown", cpu, peak, staging)
    if reason == "cancelled" or (msg and msg.get("cancelled")):
        return Outcome("cancelled", msg, "cancelled", cpu, peak, staging)
    error = (msg or {}).get("error") or (
        f"the job's process exited with code {proc.exitcode} without a result (killed, or out of memory?)")
    return Outcome("failed", msg, error, cpu, peak, staging)
