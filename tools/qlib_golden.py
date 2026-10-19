"""Regenerate quant/tests/data/alpha158_qlib_golden.parquet: qlib's OWN Alpha158 on the committed bars.

qlib is never a dependency of this repository. Run this from a throwaway environment that has it:

    uv venv /tmp/qlibenv --python 3.12 && uv pip install --python /tmp/qlibenv/bin/python pyqlib==0.9.7
    git clone --depth 1 https://github.com/microsoft/qlib /tmp/qlib-src        # for scripts/dump_bin.py
    /tmp/qlibenv/bin/python -I tools/qlib_golden.py /tmp/qlib-src /tmp/qlib-work

It writes adjusted OHLC (factor = close_adj / close) and raw volume for SPY and XLE as CSV, dumps them with
qlib's own dump_bin.py, evaluates Alpha158DL's default config with qlib.data.D, and keeps the first 70
sessions (the warm-up) plus every session from 2025-01-01. A changed file is a changed reference: review it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ["SPY", "XLE"]


def main(qlib_src: str, work: str) -> None:
    import qlib
    from qlib.contrib.data.loader import Alpha158DL
    from qlib.data import D

    w = Path(work)
    (w / "csv").mkdir(parents=True, exist_ok=True)
    for s in SYMBOLS:
        d = pd.read_parquet(ROOT / "fixtures/history" / f"{s}.parquet").sort_index()
        k = d.close_adj / d.close
        pd.DataFrame({"date": pd.DatetimeIndex(d.index).strftime("%Y-%m-%d"), "open": d.open * k,
                      "high": d.high * k, "low": d.low * k, "close": d.close_adj, "volume": d.volume,
                      "factor": k}).to_csv(w / "csv" / f"{s.lower()}.csv", index=False)
    subprocess.run([sys.executable, "-I", f"{qlib_src}/scripts/dump_bin.py", "dump_all", "--data_path",
                    str(w / "csv"), "--qlib_dir", str(w / "bin"), "--include_fields",
                    "open,high,low,close,volume,factor", "--date_field_name", "date"], check=True)
    qlib.init(provider_uri=str(w / "bin"), region="us", kernels=1)
    fields, names = Alpha158DL.get_feature_config()
    df = D.features(SYMBOLS, fields, start_time="2016-06-01", end_time="2026-06-01")
    df.columns = names
    keep = []
    for s in SYMBOLS:
        g = df.xs(s, level="instrument")
        keep.append(pd.concat([g.iloc[:70], g.loc["2025-01-01":]]).assign(symbol=s))
    out = ROOT / "quant/tests/data/alpha158_qlib_golden.parquet"
    pd.concat(keep).reset_index().rename(columns={"datetime": "date"}).to_parquet(out, index=False)
    Path(f"{out}.meta.json").write_text(json.dumps({
        "source": "qlib", "qlib_version": qlib.__version__, "synthetic": False,
        "inputs": "fixtures/history SPY, XLE (Alpaca adjusted daily bars)", "generator": "tools/qlib_golden.py",
        "note": "qlib stores and computes float32; VWAP0 is NaN (no VWAP field)"}, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
