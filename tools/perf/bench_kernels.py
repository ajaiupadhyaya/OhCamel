#!/usr/bin/env python3
"""Kernel benchmarks: the NumPy reference vs Rust at one and two threads (compute plan gate GA).

    quant/.venv/bin/python tools/perf/bench_kernels.py run --measured-on "<machine>" [--repeat 5] [--sha <12 hex>]
    quant/.venv/bin/python tools/perf/bench_kernels.py render

`run` needs the ohcamel_kernels wheel (make kernels-dev) and reads only the committed fixtures
(offline). It replaces the rows with the same (name, host, measured_on) in docs/perf/kernels.json and
re-renders docs/perf/kernels.md. `measured_on` is required and is written as given: say what the
machine is, and whether it is the droplet's CPU class. `host` is NOT given: it is the CPU model the run
reads from the machine it runs on (`bench.cpu_model()`), so a row cannot claim a CPU it was not measured on.
`garch_fit` dispatches on the row whose `host` matches the running CPU; with no such row it stays on the
wheel's engine (there is no "latest row" fallback), so a run on the droplet gives the droplet its own verdict. `--sha` is for a run where git is absent (the
image): pass the checkout's commit. The droplet command is in docs/runbooks/compute.md.
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
    xs = etf["SPY"].to_numpy()
    xx = np.column_stack([xs, xs * xs])
    out["stationary_bootstrap_means"] = (lambda t: kernels.stationary_bootstrap_means(xx, 10.0, 10_000, 3, t), True)
    perf18 = np.column_stack([etf.to_numpy(), -0.5 * etf.to_numpy()])
    out["cscv_pbo"] = (lambda t: kernels.cscv_pbo(perf18, 16, t), True)
    ypct = 100.0 * etf["SPY"].to_numpy()
    p6 = kernels.garch_fit(ypct[-1000:], "gjr", None).params
    out["garch_nll"] = (lambda t: kernels.garch_nll(p6, ypct, "gjr"), False)
    out["garch_fit"] = (lambda t: kernels.garch_fit(ypct[-1000:], "gjr", None), False)
    from ohcamel_quant.backtest.engine import rebalance_mask

    px9 = market.prices(nine).data.dropna()
    dec = np.flatnonzero(rebalance_mask(px9.index, "monthly"))
    ex9 = dec[dec + 1 < len(px9)] + 1
    tw9 = np.full((len(ex9), 9), 1 / 9)
    out["backtest_weights"] = (lambda t: kernels.backtest_weights(px9.to_numpy(), tw9, ex9, 5.0, 0.0, np.zeros(len(px9))), False)

    from ohcamel_quant.options.svi import SVIParams

    k41 = np.linspace(-0.4, 0.3, 41)
    w41 = SVIParams(a=0.004, b=0.04, rho=-0.6, m=0.02, sigma=0.12).w(k41)
    out["svi_fit"] = (lambda t: kernels.svi_fit(k41, w41, np.ones(41)), False)
    day, minute = 86_400 * 10**9, 60 * 10**9
    tsm = (np.arange(252)[:, None] * day + np.arange(390)[None, :] * minute).ravel().astype(np.int64)
    pxm = 100.0 * np.exp(np.cumsum(np.tile([1e-4, -1e-4], 252 * 195)))
    sbm = np.column_stack([np.arange(252) * day, np.arange(252) * day + 390 * minute])
    out["realized_vol_minute"] = (lambda t: kernels.realized_vol_minute(tsm, pxm, sbm), False)
    # American: 200 contracts (strikes 0.8..1.2 x spot, calls and puts, 0.1 to 2 years) at SPY-like inputs
    ka = np.tile(np.linspace(600.0, 900.0, 50), 4)
    ta = np.repeat([0.1, 0.5, 1.0, 2.0], 50)
    ca_ = np.arange(200) % 2 == 0
    pa = kernels.american_price(750.0, ka, ta, 0.045, 0.013, 0.18, ca_)
    out["american_price"] = (lambda t: kernels.american_price(750.0, ka, ta, 0.045, 0.013, 0.18, ca_, threads=t), True)
    out["american_iv"] = (lambda t: kernels.american_iv(pa, 750.0, ka, ta, 0.045, 0.013, ca_, threads=t), True)

    th = np.repeat(np.linspace(0.02, 2.0, 25), 200)
    kh = np.tile(np.linspace(450.0, 1050.0, 200), 25)
    hp = (1.5768, 0.0398, 0.5751, -0.5711, 0.0175)
    out["heston_price"] = (lambda t: kernels.heston_price(750.0, kh, th, 1.0, kh >= 750.0, *hp), False)

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
    sha = args.sha or subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True,
                                     text=True, check=True).stdout.strip()
    host = bench.cpu_model()
    print(f"host: {host}")
    doc = json.loads(args.json.read_text(encoding="utf-8"))
    rows = [r for r in bench.validate_table(doc) if (r["host"], r["measured_on"]) != (host, args.measured_on)]
    for name, (call, threaded) in cases().items():
        with kernels.forced("python"):
            py = _time(lambda c=call: c(1), args.repeat)
        with kernels.forced("rust"):
            r1 = _time(lambda c=call: c(1), args.repeat)
            r2 = _time(lambda c=call: c(2), args.repeat) if threaded else None
        rows.append({"name": name, "python_ms": py, "rust_1t_ms": r1, "rust_2t_ms": r2,
                     "host": host, "measured_on": args.measured_on, "sha": sha})
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
    r.add_argument("--sha", default=None, help="the commit measured (default: git HEAD of this checkout)")
    for p in (r, sub.add_parser("render")):
        p.add_argument("--json", type=Path, default=JSON)
        p.add_argument("--md", type=Path, default=MD)
    args = ap.parse_args()
    (run if args.cmd == "run" else render)(args)


if __name__ == "__main__":
    main()
