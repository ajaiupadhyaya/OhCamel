from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
from kronos_model import Kronos, KronosTokenizer

from ohcamel_kronos import forecast as F
from ohcamel_kronos import shadow as S

CONFIG = Path(__file__).resolve().parents[3] / "research/experiments/EXP-K01/config.yaml"


def tiny() -> F.Models:
    torch.manual_seed(0)
    tok = KronosTokenizer(d_in=6, d_model=16, n_heads=2, ff_dim=32, n_enc_layers=1, n_dec_layers=1,
                          ffn_dropout_p=0.0, attn_dropout_p=0.0, resid_dropout_p=0.0, s1_bits=4, s2_bits=4,
                          beta=0.05, gamma0=1.0, gamma=1.1, zeta=0.05, group_size=4).eval()
    mod = Kronos(s1_bits=4, s2_bits=4, n_layers=1, d_model=16, n_heads=2, ff_dim=32, ffn_dropout_p=0.0,
                 attn_dropout_p=0.0, resid_dropout_p=0.0, token_dropout_p=0.0, learn_te=True).eval()
    return F.Models(tok, mod)


def long_bars(symbol: str, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"symbol": symbol, "date": idx, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e6})


def test_one_row_per_name_with_enough_history_and_none_padded():
    bars = pd.concat([long_bars("AAA", 420, 1), long_bars("BBB", 120, 2)])
    out = S.forecast_next(bars, CONFIG, models=tiny())
    assert list(out["symbol"]) == ["AAA"]                     # BBB has 120 < 400 bars: skipped, not padded
    row = out.iloc[0]
    assert row["asof"] == str(pd.bdate_range("2024-01-01", periods=420)[-1].date())
    assert row["var_kronos"] > 0 and -1 < row["q05_kronos"] < 0 and 0 < row["distinct_frac"] <= 1


def test_the_forecast_is_reproducible_and_never_sees_a_later_bar():
    bars = long_bars("AAA", 420, 1)
    a = S.forecast_next(bars, CONFIG, models=tiny())
    b = S.forecast_next(bars, CONFIG, models=tiny())
    pd.testing.assert_frame_equal(a, b)
    c = S.forecast_next(bars.iloc[:-1], CONFIG, models=tiny())   # one bar earlier: a different, earlier forecast
    assert c.iloc[0]["asof"] < a.iloc[0]["asof"]


def test_next_weekday_skips_the_weekend():
    assert S.next_weekday(pd.Timestamp("2026-10-16")) == pd.Timestamp("2026-10-19")   # Fri -> Mon
    assert S.next_weekday(pd.Timestamp("2026-10-19")) == pd.Timestamp("2026-10-20")


def test_weights_load_from_a_baked_directory_without_the_hub(tmp_path, monkeypatch):
    """OHCAMEL_KRONOS_WEIGHTS: the image's read-only path. Saved tiny models stand in for the pinned ones."""
    m = tiny()
    m.tokenizer.save_pretrained(tmp_path / "Kronos-Tokenizer-base")
    m.model.save_pretrained(tmp_path / "Kronos-small")
    monkeypatch.setenv("OHCAMEL_KRONOS_WEIGHTS", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    loaded = F.load_models(F.load_config(CONFIG))
    bars = long_bars("AAA", 420, 1)
    pd.testing.assert_frame_equal(S.forecast_next(bars, CONFIG, models=loaded), S.forecast_next(bars, CONFIG, models=m))


def test_chunked_draws_are_reproducible_and_complete():
    m = tiny()
    b = long_bars("AAA", 60, 3).set_index("date")[["open", "high", "low", "close", "volume"]]
    from ohcamel_kronos.sampler import sample_next
    torch.manual_seed(4)
    a = sample_next(m.tokenizer, m.model, b.iloc[:-1], b.index[-1], n_samples=20, chunk=8)
    torch.manual_seed(4)
    c = sample_next(m.tokenizer, m.model, b.iloc[:-1], b.index[-1], n_samples=20, chunk=8)
    assert a.next.shape == (20, 6) and np.array_equal(a.next, c.next)
