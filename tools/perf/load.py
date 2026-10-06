#!/usr/bin/env python3
"""A batch-tier CPU load, for the G-RT gate ONLY (tools/perf/gate_rt.py).

This script exists solely to emulate the batch tier while gate_rt.py measures
the live engine's stabilize latency (compute plan Task 0.5). Its output is
nothing: it is the one sanctioned exception to the compute plan's
"no busy-work" constraint (Global Constraint 3: no load generators outside
tools/perf/), and nothing else may run it.

It spawns --procs processes, each multiplying matrices for --seconds: NumPy's
matmul when NumPy imports (the shape of the real batch tier's work), and a
pure-Python float matrix product otherwise, so it runs in a bare
python:3.12-slim container with the standard library alone.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import time


def _burn(seconds: float, seed: int) -> int:
    deadline = time.monotonic() + seconds
    rounds = 0
    try:
        import numpy as np

        rng = np.random.default_rng(seed)
        a = rng.standard_normal((256, 256))
        b = rng.standard_normal((256, 256))
        while time.monotonic() < deadline:
            a = (a @ b) / 256.0
            rounds += 1
    except ImportError:
        rnd = random.Random(seed)
        n = 48
        a = [[rnd.random() for _ in range(n)] for _ in range(n)]
        b = [[rnd.random() for _ in range(n)] for _ in range(n)]
        while time.monotonic() < deadline:
            bt = list(zip(*b))
            a = [[sum(x * y for x, y in zip(row, col)) / n for col in bt] for row in a]
            rounds += 1
    return rounds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=300.0)
    a = ap.parse_args()
    with mp.Pool(a.procs) as pool:
        rounds = pool.starmap(_burn, [(a.seconds, i) for i in range(a.procs)])
    print(f"load.py: {a.procs} processes, {a.seconds:.0f}s, {sum(rounds)} rounds")


if __name__ == "__main__":
    main()
