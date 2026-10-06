#!/usr/bin/env python3
"""G-RT: the live engine is never starved (compute plan Task 0.5).

docs/superpowers/plans/2026-09-24-quant-compute-program.md, Global Constraint
4: the engine's stabilize latency p99 may not regress more than 10 % against
the Phase 0 baseline while the batch tier runs at full load. This script is
that check, run on the droplet by the owner (step O-3) -- never in CI:

  1. reads the engine's /api/ops `stabilize_ms` every --every-s seconds for
     --rest-s seconds: the engine at rest. The engine records the duration of
     every stabilize of its graph that recomputed something -- the tick
     handlers' above all -- so nothing needs to be subscribed;
  2. starts tools/perf/load.py in the batch slice, at the batch tier's weight
     and CPU cap (`docker run --cgroup-parent ohcamel-batch.slice
     --cpu-shares 128 --cpus 1.75`), and stops with exit 2 if that container
     does not stay up -- a "loaded" phase with no load would pass anything;
  3. samples again for --load-s seconds while it runs;
  4. compares the loaded p99 against the baseline's rest p99 (the newest row
     of --baseline), or against this run's own rest p99 when the baseline
     file has no row yet, and exits 1 on a regression of more than
     --threshold (10 %);
  5. prints one markdown row for docs/perf/baseline.md.

A phase's p99 is the median of the p99s sampled during it. /api/ops reports
the p99 of the engine's last 4096 working stabilizes, so each sample covers a
trailing window of frames rather than the phase alone; the median over the
phase damps the samples taken right after the switch, whose window still
reaches back into the previous phase. Run it during a session, when ticks
arrive; outside one the engine may stabilize too rarely to measure, and a
phase with no measurement fails rather than passing.

Credentials for the live host, when --engine is its public URL behind basic
auth, come from the environment as OHCAMEL_GATE_AUTH=user:password -- never
from the command line or the URL, so they reach no shell history and no
process list.

Exit codes: 0 pass, 1 regression (or a phase with no measurement), 2 usage
or setup error.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# The same pinned Python image deploy/research.Dockerfile builds from: a tag and
# a digest, so the load is reproducible and nothing floats.
LOAD_IMAGE = (
    "python:3.12.14-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
)
HEADER = "| date | sha | rest p99 ms | load p99 ms | change | verdict |"
_ROW = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]*?)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+|n/a)\s*\|"
)


def phase_p99(samples: list[dict[str, Any]]) -> float | None:
    """The median of the non-null p99s sampled during a phase, or None."""
    xs = [float(s["p99"]) for s in samples if isinstance(s.get("p99"), (int, float))]
    return statistics.median(xs) if xs else None


def parse_baseline(text: str) -> float | None:
    """The rest p99 of the newest dated row of the baseline table, or None."""
    rest = None
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m:
            rest = float(m.group(3))
    return rest


def verdict(reference: float | None, loaded: float | None, threshold: float) -> tuple[float | None, bool]:
    """(relative change, passed). A side that was not measured fails."""
    if reference is None or loaded is None or reference <= 0:
        return None, False
    change = round((loaded - reference) / reference, 12)
    return change, change <= threshold


def markdown_row(
    date: str, sha: str, rest: float | None, loaded: float | None, change: float | None, passed: bool
) -> str:
    def ms(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    pct = "n/a" if change is None else f"{change * 100:+.1f}%"
    return f"| {date} | {sha} | {ms(rest)} | {ms(loaded)} | {pct} | {'pass' if passed else 'FAIL'} |"


def load_command(load_py: Path, *, procs: int, seconds: int) -> list[str]:
    """The batch-tier load: tools/perf/load.py in the batch slice, at its weight and cap."""
    return [
        "docker", "run", "--rm",
        "--cgroup-parent", "ohcamel-batch.slice",
        "--cpu-shares", "128",
        "--cpus", "1.75",
        "--read-only",
        "-v", f"{load_py}:/load.py:ro",
        LOAD_IMAGE,
        "python", "/load.py", "--procs", str(procs), "--seconds", str(seconds),
    ]  # fmt: skip


def _request(url: str) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    auth = os.environ.get("OHCAMEL_GATE_AUTH")
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    return req


def _read_stabilize(engine: str) -> dict[str, Any]:
    with urllib.request.urlopen(_request(f"{engine}/api/ops"), timeout=10) as r:
        return json.load(r).get("stabilize_ms") or {}


def _sample(engine: str, seconds: int, every: int, label: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            s = _read_stabilize(engine)
            out.append(s)
            print(f"  {label}: n={s.get('n')} p50={s.get('p50')} p99={s.get('p99')} max={s.get('max')}", flush=True)
        except OSError as e:
            print(f"  {label}: /api/ops unreadable ({e})", flush=True)
        time.sleep(every)
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True, help="engine base URL, e.g. http://127.0.0.1:8081")
    ap.add_argument("--baseline", type=Path, default=Path("docs/perf/baseline.md"))
    ap.add_argument("--rest-s", type=int, default=300)
    ap.add_argument("--load-s", type=int, default=300)
    ap.add_argument("--every-s", type=int, default=10)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--threshold", type=float, default=0.10)
    a = ap.parse_args(argv)
    engine = a.engine.rstrip("/")
    load_py = Path(__file__).resolve().parent / "load.py"

    try:
        first = _read_stabilize(engine)
    except OSError as e:
        print(f"gate_rt: cannot read {engine}/api/ops: {e}", file=sys.stderr)
        return 2
    if "window" not in first:
        print("gate_rt: this engine's /api/ops has no stabilize_ms -- it predates Task 0.5", file=sys.stderr)
        return 2

    print(f"G-RT: {a.rest_s}s at rest", flush=True)
    rest = phase_p99(_sample(engine, a.rest_s, a.every_s, "rest"))
    print(f"G-RT: {a.load_s}s under batch load ({a.procs} processes in ohcamel-batch.slice)", flush=True)
    try:
        load = subprocess.Popen(load_command(load_py, procs=a.procs, seconds=a.load_s + 30))
    except OSError as e:
        print(f"gate_rt: the batch load did not start: {e}", file=sys.stderr)
        return 2
    try:
        time.sleep(5)
        if load.poll() is not None:
            print(f"gate_rt: the batch load exited at once (status {load.returncode}); nothing was measured under load", file=sys.stderr)
            return 2
        loaded = phase_p99(_sample(engine, a.load_s, a.every_s, "load"))
    finally:
        if load.poll() is None:
            load.terminate()
            load.wait(timeout=60)

    reference = parse_baseline(a.baseline.read_text()) if a.baseline.exists() else None
    against = "the baseline's rest p99" if reference is not None else "this run's rest p99 (no baseline row yet)"
    if reference is None:
        reference = rest
    change, passed = verdict(reference, loaded, a.threshold)
    print(f"G-RT: loaded p99 against {against}: {reference} -> {loaded} ms", flush=True)
    print(HEADER)
    print("| --- | --- | --- | --- | --- | --- |")
    print(markdown_row(dt.datetime.now(dt.UTC).date().isoformat(), _git_sha(), rest, loaded, change, passed))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
