"""EXP-K01's forecast run: one row per name-day of Kronos's sampled one-day variance.

Reads the frozen ``config.yaml`` and the committed fixtures, and writes ``forecasts.csv`` beside the config
with a ``forecasts.meta.json`` sidecar. The scoring is ``quant``'s job
(``ohcamel_quant.models.kronos_k01``) and reads only those two files.
"""

from __future__ import annotations

import hashlib
import json
import platform
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yaml

from .sampler import CLOSE, sample_next

COLUMNS = ["symbol", "date", "var_kronos", "var_kronos_actual_anchor", "q05_kronos", "distinct_frac",
           "prev_close_actual", "prev_close_rec", "seed"]


def day_seed(base: int, symbol: str, day: pd.Timestamp) -> int:
    return base + zlib.crc32(f"{symbol}|{day.date().isoformat()}".encode())


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text())
    if cfg.get("experiment") != "EXP-K01":
        raise ValueError(f"{path} is not EXP-K01's config")
    return cfg


def load_bars(fixtures: Path, symbol: str) -> pd.DataFrame:
    """Committed bars, refused without a provenance sidecar or with ``synthetic: true`` (the charter's rule)."""
    pq = fixtures / f"{symbol}.parquet"
    meta = fixtures / f"{symbol}.parquet.meta.json"
    if not meta.exists():
        raise ValueError(f"{pq} has no provenance sidecar")
    if json.loads(meta.read_text()).get("synthetic", True):
        raise ValueError(f"{pq} is synthetic or does not say it is not")
    df = pd.read_parquet(pq).sort_index()
    df.index = pd.DatetimeIndex(df.index)
    return df


def eval_days(bars: pd.DataFrame, start: str, lookback: int) -> pd.DatetimeIndex:
    days = bars.index[bars.index >= pd.Timestamp(start)]
    first = bars.index.get_loc(days[0])
    if first < lookback:
        raise ValueError(f"only {first} sessions before {days[0].date()}; need {lookback}")
    return days


@dataclass
class Models:
    tokenizer: object
    model: object


def load_models(cfg: dict) -> Models:
    from kronos_model import Kronos, KronosTokenizer

    k = cfg["kronos"]
    # token=False: the weights are public, and a stale login on the machine must not be sent (or break the load)
    tok = KronosTokenizer.from_pretrained(k["tokenizer"], revision=k["tokenizer_revision"], token=False)
    mod = Kronos.from_pretrained(k["model"], revision=k["model_revision"], token=False)
    dev = cfg["sampling"]["device"]
    return Models(tok.eval().to(dev), mod.eval().to(dev))


def forecast_row(models: Models, bars: pd.DataFrame, symbol: str, day: pd.Timestamp, s: dict, max_context: int,
                 seed_base: int) -> dict:
    """One name-day. ``bars`` may extend past ``day``; only the ``lookback`` sessions before it are read."""
    i = bars.index.get_loc(day)
    ctx = bars.iloc[i - s["lookback"]:i]
    seed = day_seed(seed_base, symbol, day)
    torch.manual_seed(seed)
    d = sample_next(models.tokenizer, models.model, ctx[["open", "high", "low", "close", "volume"]], day,
                    n_samples=s["n_samples"], T=s["temperature"], top_k=s["top_k"], top_p=s["top_p"],
                    clip=s["clip"], max_context=max_context, device=s["device"])
    prev_actual = float(ctx["close"].iloc[-1])
    r = np.exp(d.log_returns()) - 1.0                      # simple returns, as the league's target
    ra = np.exp(d.log_returns(anchor=prev_actual)) - 1.0
    return {"symbol": symbol, "date": day.date().isoformat(), "var_kronos": float(np.mean(r * r)),
            "var_kronos_actual_anchor": float(np.mean(ra * ra)), "q05_kronos": float(np.quantile(r, 0.05)),
            "distinct_frac": float(np.unique(d.next[:, CLOSE]).size / d.next.shape[0]),
            "prev_close_actual": prev_actual, "prev_close_rec": float(d.prev[0, CLOSE]), "seed": seed}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(config_path: Path, repo_root: Path, out: Path | None = None, symbols: list[str] | None = None,
        limit_days: int | None = None, progress=print) -> Path:
    cfg = load_config(config_path)
    s = cfg["sampling"]
    torch.set_num_threads(int(s["torch_threads"]))
    fixtures = repo_root / cfg["fixtures"]
    out = out or config_path.parent / "forecasts.csv"
    models = load_models(cfg)
    rows, inputs = [], {}
    for sym in symbols or cfg["names"]:
        bars = load_bars(fixtures, sym)
        inputs[sym] = sha256(fixtures / f"{sym}.parquet")
        days = eval_days(bars, cfg["eval_start"], s["lookback"])
        for j, day in enumerate(days[:limit_days] if limit_days else days):
            rows.append(forecast_row(models, bars, sym, day, s, cfg["kronos"]["max_context"], s["seed"]))
            if j % 25 == 0:
                progress(f"{sym} {day.date()} ({j + 1}/{len(days)})")
    pd.DataFrame(rows, columns=COLUMNS).to_csv(out, index=False, float_format="%.10g")
    meta = {"experiment": "EXP-K01", "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "config_sha256": sha256(config_path), "fixtures_sha256": inputs, "rows": len(rows),
            "complete": symbols is None and limit_days is None,
            "torch": torch.__version__, "python": platform.python_version(), "machine": platform.machine(),
            "system": platform.system(), "kronos": cfg["kronos"], "sampling": s, "synthetic": False}
    Path(f"{out}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return out
