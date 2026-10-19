"""One-step-ahead Kronos samples that keep their spread.

Upstream's ``KronosPredictor.predict`` draws ``sample_count`` paths and returns
their MEAN. A variance forecast needs the spread of the paths, and the mean
throws it away. This module therefore repeats the predictor's preprocessing and
its one-step inference (``kronos.auto_regressive_inference`` with
``pred_len = 1``) line for line, and stops before the averaging.

``test_sampler.py`` pins the two together. With greedy decoding (``top_k = 1``) on
the same context, ``sample_next`` must return the same next bar as upstream's
predictor.

Point in time: ``sample_next`` is given the context bars and the timestamp of
the bar to forecast. It is never given the bar itself, so a forecast for day t
cannot see day t.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from kronos_model import sample_from_logits

PRICE_COLS = ["open", "high", "low", "close"]
FEATURES = PRICE_COLS + ["volume", "amount"]
CLOSE = FEATURES.index("close")


def time_features(ts: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """Upstream ``calc_time_stamps``: minute, hour, weekday, day, month."""
    ts = pd.DatetimeIndex(ts)
    return np.stack([ts.minute, ts.hour, ts.weekday, ts.day, ts.month], axis=1).astype(np.float32)


def model_inputs(bars: pd.DataFrame) -> np.ndarray:
    """The six input columns, as ``KronosPredictor.predict`` builds them.

    ``amount`` is volume times the mean of O/H/L/C when absent, as upstream fills it.
    """
    df = bars.copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0
        df["amount"] = 0.0
    if "amount" not in df.columns:
        df["amount"] = df["volume"] * df[PRICE_COLS].mean(axis=1)
    x = df[FEATURES].to_numpy(dtype=np.float32)
    if np.isnan(x).any():
        raise ValueError("context bars contain NaN")
    return x


@dataclass(frozen=True)
class Draws:
    """Decoded bars, in price units, one row per sample.

    ``prev`` is the tokenizer's reconstruction of the last context bar. The encoder
    and decoder are causal, so ``prev`` is the same for every sample. ``next`` is the
    sampled next bar.
    """

    prev: np.ndarray  # (n_samples, 6)
    next: np.ndarray  # (n_samples, 6)

    def log_returns(self, anchor: float | None = None) -> np.ndarray:
        """ln(next close / anchor), one per sample. ``None`` anchors on ``prev``'s
        reconstructed close, so the tokenizer's level error cancels."""
        a = self.prev[:, CLOSE] if anchor is None else np.full(self.next.shape[0], anchor)
        return np.log(self.next[:, CLOSE] / a)


@torch.no_grad()
def sample_next(tokenizer, model, context: pd.DataFrame, next_ts: pd.Timestamp, *, n_samples: int,
                T: float = 1.0, top_k: int = 0, top_p: float = 1.0, clip: float = 5.0,
                max_context: int = 512, device: str = "cpu") -> Draws:
    """``n_samples`` draws of the bar after ``context`` (DatetimeIndex, oldest first)."""
    if len(context) > max_context:
        raise ValueError(f"context has {len(context)} bars; the model's max is {max_context}")
    if not next_ts > context.index[-1]:
        raise ValueError("next_ts must be after the last context bar")
    x = model_inputs(context)
    mean, std = x.mean(axis=0), x.std(axis=0)
    xn = np.clip((x - mean) / (std + 1e-5), -clip, clip)
    stamps = np.concatenate([time_features(context.index), time_features(pd.DatetimeIndex([next_ts]))])

    xt = torch.from_numpy(xn).to(device).unsqueeze(0).repeat(n_samples, 1, 1)
    st = torch.from_numpy(stamps).to(device).unsqueeze(0).repeat(n_samples, 1, 1)
    xt = torch.clip(xt, -clip, clip)                       # upstream clips twice; harmless, kept for parity
    s1, s2 = tokenizer.encode(xt, half=True)
    L = xt.size(1)

    s1_logits, ctx = model.decode_s1(s1, s2, st[:, :L, :].contiguous())
    pre = sample_from_logits(s1_logits[:, -1, :], temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)
    s2_logits = model.decode_s2(ctx, pre)
    post = sample_from_logits(s2_logits[:, -1, :], temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)

    full = [torch.cat([s1, pre], dim=1), torch.cat([s2, post], dim=1)]
    start = max(0, L + 1 - max_context)
    z = tokenizer.decode([full[0][:, start:].contiguous(), full[1][:, start:].contiguous()], half=True)
    z = z[:, -2:, :].cpu().numpy() * (std + 1e-5) + mean
    return Draws(prev=z[:, 0, :], next=z[:, 1, :])
