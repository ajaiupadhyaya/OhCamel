"""The sampler against tiny randomly initialised models (hermetic, seconds), and, behind
OHCAMEL_KRONOS_NETWORK=1, against upstream's own regression data with the real pinned weights."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
from kronos_model import Kronos, KronosTokenizer
from kronos_model.kronos import KronosPredictor

from ohcamel_kronos.sampler import CLOSE, Draws, model_inputs, sample_next, time_features

DATA = Path(__file__).parent / "data"
MODEL_REVISION = "901c26c1332695a2a8f243eb2f37243a37bea320"
TOKENIZER_REVISION = "0e0117387f39004a9016484a186a908917e22426"


@pytest.fixture(scope="module")
def tiny():
    torch.manual_seed(0)
    tok = KronosTokenizer(d_in=6, d_model=16, n_heads=2, ff_dim=32, n_enc_layers=1, n_dec_layers=1,
                          ffn_dropout_p=0.0, attn_dropout_p=0.0, resid_dropout_p=0.0, s1_bits=4, s2_bits=4,
                          beta=0.05, gamma0=1.0, gamma=1.1, zeta=0.05, group_size=4).eval()
    mod = Kronos(s1_bits=4, s2_bits=4, n_layers=1, d_model=16, n_heads=2, ff_dim=32, ffn_dropout_p=0.0,
                 attn_dropout_p=0.0, resid_dropout_p=0.0, token_dropout_p=0.0, learn_te=True).eval()
    return tok, mod


def bars(n: int = 60, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = c * (1 + rng.normal(0, 0.002, n))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996,
                         "close": c, "volume": rng.uniform(1e6, 2e6, n)}, index=idx)


def test_shapes_and_a_shared_reconstruction(tiny):
    b = bars()
    d = sample_next(*tiny, b.iloc[:-1], b.index[-1], n_samples=16)
    assert d.next.shape == d.prev.shape == (16, 6)
    # causal tokenizer: the last context bar decodes identically whatever was drawn after it
    np.testing.assert_allclose(d.prev, np.broadcast_to(d.prev[0], d.prev.shape), rtol=1e-5)
    assert np.all(np.isfinite(d.log_returns()))


def test_the_forecast_day_is_never_read(tiny):
    """Point in time: rewriting day t, or everything after it, cannot change the draws for t."""
    b = bars()
    ctx, day = b.iloc[:-1], b.index[-1]
    torch.manual_seed(5)
    a = sample_next(*tiny, ctx, day, n_samples=8)
    b2 = b.copy()
    b2.iloc[-1] *= 3.0
    torch.manual_seed(5)
    c = sample_next(*tiny, b2.iloc[:-1], day, n_samples=8)
    np.testing.assert_array_equal(a.next, c.next)


def test_seeded_draws_repeat_and_unseeded_ones_differ(tiny):
    b = bars()
    torch.manual_seed(11)
    a = sample_next(*tiny, b.iloc[:-1], b.index[-1], n_samples=32)
    torch.manual_seed(11)
    c = sample_next(*tiny, b.iloc[:-1], b.index[-1], n_samples=32)
    np.testing.assert_array_equal(a.next, c.next)
    torch.manual_seed(12)
    e = sample_next(*tiny, b.iloc[:-1], b.index[-1], n_samples=32)
    assert not np.array_equal(a.next, e.next)


def test_greedy_matches_upstream_predictor_on_tiny_models(tiny):
    """The reimplementation and upstream's predictor agree when sampling is taken out (top_k = 1)."""
    b = bars()
    ctx, day = b.iloc[:-1], b.index[-1]
    up = KronosPredictor(tiny[1], tiny[0], device="cpu", max_context=512).predict(
        ctx.reset_index(drop=True), pd.Series(ctx.index), pd.Series([day]), 1, T=1.0, top_k=1, top_p=1.0,
        sample_count=1, verbose=False)
    ours = sample_next(*tiny, ctx, day, n_samples=3, top_k=1)
    for row in ours.next:
        np.testing.assert_allclose(row, up.iloc[0].to_numpy(), rtol=1e-5)


def test_refuses_lookahead_and_oversized_context(tiny):
    b = bars()
    with pytest.raises(ValueError, match="after the last context bar"):
        sample_next(*tiny, b, b.index[-1], n_samples=2)
    with pytest.raises(ValueError, match="max"):
        sample_next(*tiny, b, b.index[-1] + pd.Timedelta(days=1), n_samples=2, max_context=10)


def test_inputs_fill_amount_as_upstream_does():
    b = bars(5)
    x = model_inputs(b)
    np.testing.assert_allclose(x[:, 5], (b["volume"] * b[["open", "high", "low", "close"]].mean(axis=1)), rtol=1e-6)
    with pytest.raises(ValueError, match="NaN"):
        model_inputs(b.assign(close=np.nan))
    assert time_features(b.index).shape == (5, 5)


def test_log_returns_anchor_on_the_reconstruction_by_default():
    prev = np.tile([0, 0, 0, 100.0, 0, 0], (2, 1))
    nxt = np.tile([0, 0, 0, 0, 0, 0.0], (2, 1))
    nxt[:, CLOSE] = [101.0, 99.0]
    d = Draws(prev=prev, next=nxt)
    np.testing.assert_allclose(d.log_returns(), np.log([1.01, 0.99]))
    np.testing.assert_allclose(d.log_returns(anchor=50.0), np.log([101 / 50, 99 / 50]))


@pytest.mark.skipif(os.environ.get("OHCAMEL_KRONOS_NETWORK") != "1",
                    reason="downloads the pinned weights; set OHCAMEL_KRONOS_NETWORK=1")
def test_pinned_weights_reproduce_upstreams_regression_and_our_first_step():
    """Upstream's regression_output_512.csv at the pinned revisions: the vendored predictor reproduces it,
    and the sampler's greedy first step equals its first row."""
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base", revision=TOKENIZER_REVISION,
                                          token=False).eval()
    mod = Kronos.from_pretrained("NeoQuasar/Kronos-small", revision=MODEL_REVISION, token=False).eval()
    df = pd.read_csv(DATA / "regression_input.csv", parse_dates=["timestamps"])
    exp = pd.read_csv(DATA / "regression_output_512.csv", parse_dates=["timestamps"])
    feats = ["open", "high", "low", "close", "volume", "amount"]
    ctx = df.iloc[:512]
    fut = df["timestamps"].iloc[512:512 + len(exp)].reset_index(drop=True)
    torch.manual_seed(123)
    up = KronosPredictor(mod, tok, device="cpu", max_context=512).predict(
        ctx[feats].reset_index(drop=True), ctx["timestamps"].reset_index(drop=True), fut, len(exp), T=1.0,
        top_k=1, top_p=1.0, sample_count=1, verbose=False)
    np.testing.assert_allclose(up[feats].to_numpy(np.float32), exp[feats].to_numpy(np.float32), rtol=1e-5)
    # One step against one step: an 8-step run decodes a window that starts 8 tokens later, and the decoder's
    # output depends on where its window starts, so its first row is not a one-step forecast (volume moves ~0.1%).
    one = KronosPredictor(mod, tok, device="cpu", max_context=512).predict(
        ctx[feats].reset_index(drop=True), ctx["timestamps"].reset_index(drop=True), fut.iloc[:1], 1, T=1.0,
        top_k=1, top_p=1.0, sample_count=1, verbose=False)
    np.testing.assert_allclose(one[["open", "high", "low", "close"]].iloc[0].to_numpy(np.float32),
                               exp[["open", "high", "low", "close"]].iloc[0].to_numpy(np.float32), rtol=1e-4)
    c = ctx.set_index("timestamps")[feats]
    ours = sample_next(tok, mod, c, fut.iloc[0], n_samples=1, top_k=1)
    np.testing.assert_allclose(ours.next[0], one[feats].iloc[0].to_numpy(np.float32), rtol=1e-5)
