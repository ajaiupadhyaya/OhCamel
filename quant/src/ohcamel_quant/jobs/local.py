"""One job, run in this process exactly as the worker's child runs it (Task 3.3).

``execute`` is the child's body: the kind's handler under the job context's recorder, the tables written, the
record of what was read and which seeds were drawn put into the result -- and a result that drew random numbers
without a seed refused before anything is published. ``child.child_main`` calls it; so do ``run_local`` (the
restore drill's and the tests' way to make an artifact without a worker) and ``tools/regenerate.py``.

``pinned_clock`` holds the warehouse's freshness clock (``warehouse.freshness._now``) at a job's start: whether
the facade serves bars from the warehouse or the providers depends on it, so a re-run days later must ask the
question at the time the job asked it.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .artifacts import ArtifactSpec, build_manifest, publish, write_tables
from .context import JobContext
from .db import iso, parse_iso
from .kinds import get_kind, load_handler
from .provenance import check_seed, checkout_sha, kernels_info, lock_hash, runtime_info
from .queue import params_hash
from .ulid import new_ulid


@contextmanager
def pinned_clock(now: datetime | str | None) -> Iterator[None]:
    """Pin the warehouse freshness clock at ``now`` (a datetime or the manifest's ISO ``started_at``)."""
    if now is None:
        yield
        return
    from ..warehouse import freshness

    at = parse_iso(now) if isinstance(now, str) else now
    real = freshness._now
    freshness._now = lambda: at
    try:
        yield
    finally:
        freshness._now = real


def execute(kind: str, params: dict[str, Any], ctx: JobContext, staging: Path) -> dict[str, Any]:
    """Run ``kind``'s handler; write its tables into ``staging``; return the child's result message (without
    ``ok``). Raises whatever the handler raises, and ``UnseededDraw`` for a draw without a seed."""
    spec_k = get_kind(kind)
    handler = load_handler(spec_k)
    with ctx.recorder.active():
        spec = handler(params, ctx)
    if not isinstance(spec, ArtifactSpec):
        raise TypeError(f"handler for {kind} returned {type(spec).__name__}, not ArtifactSpec")
    record = ctx.recorder.summary()
    check_seed(kind, record, draws_random=spec_k.draws_random)
    names = write_tables(Path(staging), spec.tables)
    return {"data_asof": spec.data_asof, "provenance": spec.provenance, "notes": spec.notes,
            "survivorship": spec.survivorship, "engine": spec.engine, "tables": names, "verdict": spec.verdict,
            "verdict_detail": spec.verdict_detail, **record, "threads": ctx.threads, "lock_hash": lock_hash(),
            "kernels": kernels_info(), "runtime": runtime_info()}


def make_context(params: dict[str, Any], *, settings: Any = None, threads: int = 1,
                 job_id: str = "local") -> JobContext:
    from ..data.market import MarketData

    return JobContext(job_id=job_id, params=params, threads=threads, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=MarketData(settings) if settings is not None else None)


def run_local(kind: str, params: dict[str, Any], *, artifacts_root: Path, settings: Any = None, threads: int = 1,
              now: datetime | None = None, code_sha: str | None = None) -> Path:
    """Run one job here and publish its artifact under ``artifacts_root/<kind>/<id>`` (no jobs.sqlite row).
    ``now`` pins the job's clock (its ``started_at`` and the warehouse freshness clock); default: the real one."""
    at = now or datetime.now(UTC)
    job = SimpleNamespace(id=new_ulid(int(at.timestamp() * 1000)), kind=kind, params=params,
                          params_hash=params_hash(params), started_at=iso(at))
    staging = Path(artifacts_root) / ".staging" / job.id
    ctx = make_context(params, settings=settings, threads=threads, job_id=job.id)
    try:
        with pinned_clock(at):
            result = execute(kind, params, ctx, staging)
        manifest = build_manifest(job, result, code_sha=code_sha or checkout_sha(),  # type: ignore[arg-type]
                                  finished_at=iso(at), cpu_seconds=0.0, peak_rss_bytes=None)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        ctx.close()
    return publish(staging, Path(artifacts_root), job, manifest)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    """``python -m ohcamel_quant.jobs.local KIND --artifacts DIR [--warehouse FILE] [--params JSON] [--now ISO]``:
    one job here, its artifact published under DIR (the restore drill's fixture artifact)."""
    import argparse
    import json

    from ..config import Settings

    p = argparse.ArgumentParser(prog="python -m ohcamel_quant.jobs.local", description=main.__doc__)
    p.add_argument("kind")
    p.add_argument("--artifacts", type=Path, required=True)
    p.add_argument("--warehouse", type=Path, default=None)
    p.add_argument("--params", default="{}")
    p.add_argument("--now", default=None, help="pin the job's clock (ISO, with a zone)")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--offline", action="store_true")
    a = p.parse_args(argv)
    over: dict[str, Any] = {"offline": True} if a.offline else {}
    if a.warehouse is not None:
        over["warehouse_path"] = a.warehouse
    settings = Settings(**over) if over else None
    out = run_local(a.kind, json.loads(a.params), artifacts_root=a.artifacts, settings=settings, threads=a.threads,
                    now=parse_iso(a.now) if a.now else None)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
