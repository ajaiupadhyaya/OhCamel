"""The worker process: ``python -m ohcamel_quant worker`` (compose service ohcamel-worker).

One job at a time. Each poll: housekeeping (stale re-queue every minute),
stamp the heartbeat file (the compose healthcheck), ask admission, claim,
run in a child, settle. SIGTERM/SIGINT request a stop: the running job is
given ``grace_s`` (15 s) to finish or notice, then re-queued -- compose
gives the container 25 s (stop_grace_period).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .admission import Admission, make_admit
from .artifacts import build_manifest, publish
from .db import connect, iso, utcnow
from .kinds import retries_for
from .queue import (
    Job,
    claim_next,
    fail,
    finish,
    heartbeat,
    requeue,
    requeue_stale,
    settle_cancelled,
)
from .runner import Outcome, run_in_child

log = logging.getLogger("ohcamel_quant.jobs.worker")


@dataclass
class WorkerConfig:
    db_path: Path
    artifacts_root: Path
    poll_s: float = 5.0
    heartbeat_s: float = 15.0
    grace_s: float = 15.0
    heartbeat_file: Path | None = Path("/tmp/ohcamel-worker.heartbeat")
    code_sha: str = "unknown"
    scheduler: bool = True
    schedules_path: Path | None = None


class Worker:
    def __init__(self, cfg: WorkerConfig, *, admit: Callable[[datetime], Admission] | None = None,
                 now: Callable[[], datetime] = utcnow) -> None:
        self.cfg = cfg
        self.admit = admit or make_admit()
        self.now = now
        self.stop = threading.Event()
        self.conn = connect(cfg.db_path)
        self._last_reason: str | None = None

    def request_stop(self) -> None:
        self.stop.set()

    def _stamp(self) -> None:
        if self.cfg.heartbeat_file is not None:
            try:
                self.cfg.heartbeat_file.touch()
            except OSError as e:
                log.warning("cannot stamp %s: %s", self.cfg.heartbeat_file, e)

    def _housekeeping(self) -> None:
        for job_id, state in requeue_stale(self.conn, now=self.now()):
            log.warning("stale job %s -> %s", job_id, state)

    def run_once(self) -> Outcome | None:
        now = self.now()
        adm = self.admit(now)
        if adm.reason != self._last_reason:
            log.info("admission: %s", adm.reason)
            self._last_reason = adm.reason
        job = claim_next(self.conn, allowed_classes=adm.allowed_classes, allow_heavy=adm.allow_heavy, now=now)
        if job is None:
            return None
        log.info("running %s %s (threads=%d)", job.kind, job.id, adm.threads)

        def beat() -> str:
            self._stamp()
            return heartbeat(self.conn, job.id, now=self.now())

        out = run_in_child(job, threads=adm.threads, db_path=self.cfg.db_path,
                           staging_root=self.cfg.artifacts_root / ".staging", stop=self.stop, beat=beat,
                           heartbeat_s=self.cfg.heartbeat_s, grace_s=self.cfg.grace_s)
        self._settle(job, out)
        log.info("%s %s -> %s (%.2f cpu s, peak %s B)", job.kind, job.id, out.status, out.cpu_seconds,
                 out.peak_rss_bytes)
        return out

    def _settle(self, job: Job, out: Outcome) -> None:
        now = self.now()
        if out.status == "done":
            assert out.result is not None
            manifest = build_manifest(job, out.result, code_sha=self.cfg.code_sha, finished_at=iso(now),
                                      cpu_seconds=out.cpu_seconds, peak_rss_bytes=out.peak_rss_bytes)
            final = publish(out.staging, self.cfg.artifacts_root, job, manifest)
            art = {"id": job.id, "kind": job.kind, "params_hash": job.params_hash, "finished_at": iso(now),
                   "path": str(final), "data_asof": manifest["data_asof"]}
            if not finish(self.conn, job.id, artifact=art, cpu_seconds=out.cpu_seconds,
                          peak_rss_bytes=out.peak_rss_bytes, now=now):
                shutil.rmtree(final, ignore_errors=True)  # cancelled while it finished: not published
            return
        shutil.rmtree(out.staging, ignore_errors=True)
        if out.status == "failed":
            fail(self.conn, job.id, error=out.error or "unknown error", retries=retries_for(job.kind),
                 cpu_seconds=out.cpu_seconds, peak_rss_bytes=out.peak_rss_bytes, now=now)
        elif out.status == "cancelled":
            settle_cancelled(self.conn, job.id, cpu_seconds=out.cpu_seconds, peak_rss_bytes=out.peak_rss_bytes,
                             now=now)
        else:
            requeue(self.conn, job.id, reason="interrupted by worker shutdown; re-queued", now=now)

    def run(self) -> None:
        self._housekeeping()
        sched = None
        if self.cfg.scheduler:
            from .schedules import DEFAULT_PATH, load_schedules, start_scheduler

            schedules = load_schedules(self.cfg.schedules_path or DEFAULT_PATH)  # a bad file fails the start
            sched = start_scheduler(self.cfg.db_path, schedules, self.stop, self.now)
        last_house = time.monotonic()
        while not self.stop.is_set():
            self._stamp()
            if time.monotonic() - last_house >= 60.0:
                self._housekeeping()
                last_house = time.monotonic()
            if self.run_once() is None:
                self.stop.wait(self.cfg.poll_s)
        if sched is not None:
            sched.join(5)
        self.conn.close()


def install_signal_handlers(worker: Worker) -> dict[int, object]:
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: worker.request_stop())
    return previous


def main(argv: list[str] | None = None) -> int:
    from ..config import get_settings
    from .paths import artifacts_root, jobs_db_path

    p = argparse.ArgumentParser(prog="ohcamel-quant worker",
                                description="Run queued jobs one at a time (compute plan Lane B).")
    p.add_argument("--db", type=Path, help="jobs.sqlite (default {data_dir}/jobs.sqlite)")
    p.add_argument("--artifacts", type=Path, help="artifact root (default {data_dir}/artifacts)")
    p.add_argument("--poll", type=float, default=5.0, help="seconds between polls when idle")
    p.add_argument("--no-scheduler", action="store_true", help="do not enqueue schedules.yaml entries")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    cfg = WorkerConfig(db_path=a.db or jobs_db_path(s), artifacts_root=a.artifacts or artifacts_root(s),
                       poll_s=a.poll, code_sha=os.environ.get("OHCAMEL_GIT_SHA") or "unknown",
                       scheduler=not a.no_scheduler)
    w = Worker(cfg)
    install_signal_handlers(w)
    w.run()
    return 0
