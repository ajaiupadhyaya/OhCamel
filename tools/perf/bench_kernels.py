#!/usr/bin/env python3
"""Kernel benchmarks: the NumPy reference vs Rust at one and two threads (compute plan gate GA).

    quant/.venv/bin/python tools/perf/bench_kernels.py run --measured-on "<machine>" [--repeat 5]
    quant/.venv/bin/python tools/perf/bench_kernels.py render

`run` needs the ohcamel_kernels wheel (make kernels-dev) and reads only the committed fixtures
(offline). It replaces the rows with the same (name, measured_on) in docs/perf/kernels.json and
re-renders docs/perf/kernels.md. `measured_on` is required and is written as given: say what the
machine is, and whether it is the droplet's CPU class.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("OHCAMEL_QUANT_OFFLINE", "1")

import numpy as np  # noqa: E402

from ohcamel_quant import kernels  # noqa: E402
from ohcamel_quant.kernels import bench  # noqa: E402
from ohcamel_quant.kernels.types import GarchParams  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
JSON = ROOT / "docs" / "perf" / "kernels.json"
MD = ROOT / "docs" / "perf" / "kernels.md"

Case = tuple[Callable[[int], object], bool]  # (call with a thread count, whether threads matter)


def cases() -> dict[str, Case]:
    """One timed call per kernel at the size bench.SIZES states, on the committed fixtures."""
    from ohcamel_quant.data.market import get_market

    market = get_market()
    out: dict[str, Case] = {}
    from ohcamel_quant.risk.garch import fit_garch

    nine = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]
    etf = market.returns(nine).data.dropna()
    fits = [fit_garch(etf[c].to_numpy(), "gjr") for c in nine]
    z9 = np.column_stack([f.std_resid for f in fits])
    gp9 = GarchParams.from_fits(fits)
    w9 = np.full(9, 1 / 9)
    out["fhs_paths"] = (lambda t: kernels.fhs_paths(z9, gp9, w9, 10, 100_000, 1, t), True)
    out["copula_t_paths"] = (lambda t: kernels.copula_t_paths(etf.to_numpy(), w9, 6.0, 10, 100_000, 1, t), True)
    pnl1m = kernels.fhs_paths(z9, gp9, w9, 1, 1_000_000, 2, 2)
    out["var_es_from_pnl"] = (lambda t: kernels.var_es_from_pnl(pnl1m, 0.99), False)
    return out


def _time(fn: Callable[[], object], repeat: int) -> float:
    fn()  # warm-up
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e3)
    return round(statistics.median(ts), 3)


def run(args: argparse.Namespace) -> None:
    if kernels._RUST is None:
        sys.exit("ohcamel_kernels is not importable: run `make kernels-dev` first")
    sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.strip()
    doc = json.loads(args.json.read_text(encoding="utf-8"))
    rows = [r for r in bench.validate_table(doc) if r["measured_on"] != args.measured_on]
    for name, (call, threaded) in cases().items():
        with kernels.forced("python"):
            py = _time(lambda c=call: c(1), args.repeat)
        with kernels.forced("rust"):
            r1 = _time(lambda c=call: c(1), args.repeat)
            r2 = _time(lambda c=call: c(2), args.repeat) if threaded else None
        rows.append({"name": name, "python_ms": py, "rust_1t_ms": r1, "rust_2t_ms": r2,
                     "measured_on": args.measured_on, "sha": sha})
        print(f"{name:28s} python {py:12.3f} ms   rust 1T {r1:10.3f} ms   rust 2T {r2 if r2 is not None else 'n/a'}")
    out = {"kernels": rows}
    bench.validate_table(out)
    args.json.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    args.md.write_text(bench.render_markdown(out), encoding="utf-8")


def render(args: argparse.Namespace) -> None:
    args.md.write_text(bench.render_markdown(json.loads(args.json.read_text(encoding="utf-8"))), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--measured-on", required=True)
    r.add_argument("--repeat", type=int, default=5)
    for p in (r, sub.add_parser("render")):
        p.add_argument("--json", type=Path, default=JSON)
        p.add_argument("--md", type=Path, default=MD)
    args = ap.parse_args()
    (run if args.cmd == "run" else render)(args)


if __name__ == "__main__":
    main()
