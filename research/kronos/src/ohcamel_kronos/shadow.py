"""Kronos in shadow: tomorrow's variance forecast, made tonight, before tomorrow exists.

The quant worker's ``kronos.shadow`` job runs this in a subprocess (this project's own venv in the image, so
torch never enters quant's), hands it each name's bars in a parquet file, and reads one row per name back.
The design is EXP-K01's, read from that experiment's frozen config, so the shadow record measures the same
model the experiment measured, only prospectively.

Point in time is structural. The forecast for the session after the last bar is made from bars up to and
including that bar, and the realised return does not exist yet when the row is written. The worker stamps
the row with its own clock.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .forecast import day_seed, load_config, load_models
from .sampler import CLOSE, sample_next

CHUNK = 8    # draws per forward pass: bounds peak memory for the droplet (see sampler.sample_next)
COLUMNS = ["symbol", "asof", "target_hint", "var_kronos", "q05_kronos", "distinct_frac", "seed"]


def next_weekday(d: pd.Timestamp) -> pd.Timestamp:
    """The time features' guess at the next session. A holiday only shifts the calendar stamp the model
    sees; the worker matches the forecast to the first bar that actually follows ``asof``."""
    n = d + pd.Timedelta(days=1)
    while n.weekday() >= 5:
        n += pd.Timedelta(days=1)
    return n


def forecast_next(bars: pd.DataFrame, config_path: Path, models=None, threads: int | None = None) -> pd.DataFrame:
    """``bars``: long frame with symbol, date, open, high, low, close, volume (split-adjusted). One row a symbol
    with at least ``lookback`` bars; a shorter one is skipped (never padded)."""
    cfg = load_config(config_path)
    s = cfg["sampling"]
    torch.set_num_threads(int(threads or s["torch_threads"]))
    models = models or load_models(cfg)
    rows = []
    for sym, g in bars.sort_values("date").groupby("symbol", sort=True):
        g = g.set_index(pd.DatetimeIndex(g["date"]))
        if len(g) < s["lookback"]:
            continue
        ctx = g.iloc[-s["lookback"]:][["open", "high", "low", "close", "volume"]].astype(float)
        asof = ctx.index[-1]
        target = next_weekday(asof)
        seed = day_seed(s["seed"], str(sym), target)
        torch.manual_seed(seed)
        d = sample_next(models.tokenizer, models.model, ctx, target, n_samples=s["n_samples"], T=s["temperature"],
                        top_k=s["top_k"], top_p=s["top_p"], clip=s["clip"], max_context=cfg["kronos"]["max_context"],
                        device=s["device"], chunk=CHUNK)
        r = np.exp(d.log_returns()) - 1.0
        rows.append({"symbol": sym, "asof": asof.date().isoformat(), "target_hint": target.date().isoformat(),
                     "var_kronos": float(np.mean(r * r)), "q05_kronos": float(np.quantile(r, 0.05)),
                     "distinct_frac": float(np.unique(d.next[:, CLOSE]).size / d.next.shape[0]), "seed": seed})
    return pd.DataFrame(rows, columns=COLUMNS)
