#!/usr/bin/env python3
"""EXP-A01's two strategies' ex-ante volatility for INC-02's stop (completion task 2.2).

The stop is twice a strategy's ex-ante annualised volatility, and that volatility is the 252-session realised
volatility of the strategy's backtest at its traded parameters (``quant/.../signals/freeze.py``). EXP-A01's two
rows (``exp_a01_spy``, ``exp_a01_tlt``) are research-battery strategies, not farm cells, so the farm freeze never
writes theirs. The battery commits no return series -- only manifests -- so this script RE-RUNS the battery's own
holdout backtest (``battery.run.holdout_returns``: fdq's engine, the friction file, the committed fixtures) at each
manifest's ``selected_params``, and takes the last 252 daily returns of the holdout window (2022-06-01 ..
2026-06-01, the series the manifest's gates read) -- the same rule, from EXP-A01's own backtest. The params are
read from the committed manifests; nothing is fetched and nothing is generated.

    cd research && uv run --offline python ../tools/inc02_exp_a01_vol.py [--out ../research/experiments/INC-02/exp_a01_vol.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "src"))

SESSIONS = 252


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    from ohcamel_research.battery.config import load_experiment_config
    from ohcamel_research.battery.data import load_bars, load_macro, slice_bars
    from ohcamel_research.battery.run import _wide_for, holdout_returns, load_friction

    exp = ROOT / "research" / "experiments" / "EXP-A01"
    cfg = load_experiment_config(exp / "config.yaml", ROOT)
    friction = load_friction(ROOT / cfg.friction_file, cfg.friction_version)
    macro = load_macro(ROOT / cfg.macro)
    out: dict[str, dict] = {}
    for slug in ("exp_a01_spy", "exp_a01_tlt"):
        manifest = json.loads((exp / f"manifest.{slug}.json").read_text())
        sym, params = manifest["symbol"], manifest["selected_params"]
        long = load_bars(ROOT / cfg.bars, [sym])
        bars = _wide_for(slice_bars(long, cfg.selection.start, cfg.holdout.end), sym)
        r = holdout_returns(manifest["strategy"], params, bars, cfg.holdout, cfg.tier, friction, macro, cfg.seed)
        last = r.dropna().iloc[-SESSIONS:]
        if len(last) < SESSIONS:
            raise SystemExit(f"{slug}: {len(last)} sessions, fewer than {SESSIONS}: no number")
        vol = float(last.std(ddof=1)) * math.sqrt(252)
        out[slug] = {"ex_ante_vol": vol, "strategy": manifest["strategy"], "params": params,
                     "sessions": SESSIONS, "first": str(last.index[0].date()), "last": str(last.index[-1].date()),
                     "source": "battery.run.holdout_returns on fixtures/history (bars to 2026-06-01, so the window ends 2026-06-01), EXP-A01 friction, tier "
                               f"{cfg.tier:g}"}
        print(f"{slug}  {manifest['strategy']} {params}  ex_ante_vol {vol:.6f}  ({last.index[0].date()} .. "
              f"{last.index[-1].date()})")
    if args.out:
        args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
