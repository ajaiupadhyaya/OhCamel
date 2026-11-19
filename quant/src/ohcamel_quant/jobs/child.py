"""The child process: one job, then exit (its memory goes back to the OS).

Thread caps are set in the environment BEFORE numpy can be imported (this
module imports nothing numeric at top level). Progress goes straight to
jobs.sqlite (rate-limited to once a second); the result goes back over the
pipe; tables are written into the staging directory. The result carries the record of every read the
handler made and every seed it drew (jobs/provenance.py); a draw without a seed fails the job here.
"""

from __future__ import annotations

import os
import resource
import time
from pathlib import Path
from typing import Any

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
               "RAYON_NUM_THREADS")


def child_main(job: dict[str, Any], threads: int, staging: str, db_path: str, cancel_event: Any, conn: Any) -> None:
    # its own process group (completion task 3.6): at the wall limit the worker kills the group, so whatever this
    # job starts goes with it, and the worker's own group is never signalled
    try:
        os.setpgid(0, 0)
    except OSError:
        pass
    for var in THREAD_VARS:
        os.environ[var] = str(threads)
    from .context import JobCancelled, JobContext
    from .db import connect, utcnow
    from .local import execute
    from .queue import set_progress
    from .rusage import maxrss_bytes

    db = connect(Path(db_path))
    last = [0.0]

    def progress(fraction: float, message: str | None) -> None:
        t = time.monotonic()
        if t - last[0] < 1.0 and fraction < 1.0:
            return
        last[0] = t
        set_progress(db, job["id"], fraction, message, now=utcnow())

    ctx = JobContext(job_id=job["id"], params=job["params"], threads=threads, progress_fn=progress,
                     cancelled_fn=cancel_event.is_set)
    msg: dict[str, Any]
    try:
        # the handler under the recorder; the tables; what it read, the seeds it drew (jobs/local.py, Task 3.3)
        msg = {"ok": True, **execute(job["kind"], job["params"], ctx, Path(staging))}
    except JobCancelled:
        msg = {"ok": False, "cancelled": True, "error": "cancelled"}
    except Exception as e:  # noqa: BLE001 - every failure is reported, never swallowed
        msg = {"ok": False, "cancelled": False, "error": f"{type(e).__name__}: {e}"[:2000]}
    finally:
        ctx.close()
        db.close()
    msg["peak_rss_bytes"] = maxrss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    conn.send(msg)
    conn.close()
