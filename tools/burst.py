#!/usr/bin/env python3
"""Run one heavy research job on this machine against the droplet's newest warehouse export and publish its
artifact through the live host's burst door (completion task 3.7, spec R6).

    tools/burst.py --kind KIND [--params JSON] [--dry-run] [--export DIR | --no-pull] [--threads N] [--keep]

From the repository root, in the project's environment (``make research-burst KIND=... PARAMS=... [DRY_RUN=1]``
does this):

    cd quant && uv run --frozen python ../tools/burst.py --kind cov.league --params '{}' --dry-run

What it does, in order, and every refusal, is ohcamel_quant/jobs/burst.py's docstring; the owner's runbook is
docs/runbooks/burst.md. This file only sets the thread count before numpy can load -- ``OHCAMEL_THREADS`` and the
BLAS/OpenMP/Rayon variables, to ``--threads`` or this machine's cores, as the worker's child does -- and calls it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "quant" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _threads(argv: list[str]) -> int:
    for i, a in enumerate(argv):
        if a == "--threads" and i + 1 < len(argv) and argv[i + 1].isdigit():
            return max(1, int(argv[i + 1]))
        if a.startswith("--threads=") and a.split("=", 1)[1].isdigit():
            return max(1, int(a.split("=", 1)[1]))
    return os.cpu_count() or 1


if __name__ == "__main__":
    n = str(_threads(sys.argv[1:]))
    for var in ("OHCAMEL_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS"):
        os.environ[var] = n
    from ohcamel_quant.jobs.burst import main  # noqa: E402 - after the thread variables

    raise SystemExit(main(sys.argv[1:] + ([] if "--threads" in " ".join(sys.argv[1:]) else ["--threads", n])))
