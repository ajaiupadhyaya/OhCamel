#!/usr/bin/env python3
"""Make an artifact's numbers again from a warehouse export, and exit non-zero unless every table matches.

    tools/regenerate.py <artifact dir> --warehouse <export dir> [--artifacts ROOT] [--allow-drift] [--scratch DIR]

Run with the project's own environment, from the repository root:

    quant/.venv/bin/python tools/regenerate.py /data/artifacts/risk.mc_atlas/<id> \
        --warehouse /var/backups/ohcamel/warehouse/2026-11-17

or inside the worker image (``python /app/tools/...`` is not shipped; use
``python -m ohcamel_quant.jobs.regenerate`` there, which this file calls). What it checks, in order, and every
exit code, is ohcamel_quant/jobs/regenerate.py's docstring. It never writes into the artifact store, the export
or a live warehouse: the export is imported into a new file in a scratch directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "quant" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ohcamel_quant.jobs.regenerate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
