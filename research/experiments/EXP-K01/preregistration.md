# EXP-K01 pre-registration: Kronos in the volatility league

**Status: APPROVED by the owner on 2026-10-19, as written.** The engineering session drafted it the same day
and committed it (`486f0b5`) before any forecast existed. The owner approved it unchanged while the full forecast
run was in progress. At that moment no forecast had been scored, no result had been computed, and the partial
forecast file had not been read. Every sentence below is now the owner's.

Written 2026-10-19, before any scored forecast existed. One thing was seen before writing:
an engineering check of the sampler drew 64 samples for SPY on 2026-06-01, the last
evaluation day. That is one name on one day out of about 2,070 name-days. It showed the sampled spread
was roughly twice the trailing 20-day realised volatility. That observation is disclosed here and
did not change any value below.

## What Kronos is

[Kronos](https://github.com/shiyu-coder/Kronos) (Shi et al., AAAI 2026, arXiv 2508.02739) is a
decoder-only transformer pre-trained on daily and intraday candlesticks from 45+ exchanges. A tokenizer
quantizes each OHLCV bar into two hierarchical discrete tokens, and the transformer predicts the next
bar's tokens autoregressively. Because it *samples* the next bar, it implies a forecast distribution,
not just a point forecast.

## Hypothesis

> The one-day-ahead variance implied by Kronos-small's sampled next bar is a better forecast of the
> next day's squared return than the incumbents of OhCamel's volatility league (GARCH, GJR, EGARCH and
> EWMA, as `vol.forecast_league` runs them), on nine liquid US ETFs.

**Economic rationale.** Volatility clusters, and a model that has read millions of candlestick sequences
might recognise the patterns that come before a volatility change: ranges widening, gaps, volume.
Those patterns sit in O/H/L/V. A close-to-close GARCH ignores them; Kronos sees all of it.

**Expected failure modes, stated in advance:**

1. **Quantization.** Each window is normalized by its own mean and standard deviation of price
   *levels* over 400 days, and a daily return is small against that, so the tokenizer may represent
   one-day moves coarsely.
2. **The wrong market.** The pre-training corpus is dominated by Chinese A-shares and crypto at
   intraday frequencies, so its idea of a typical move may be miscalibrated for US ETFs at daily
   frequency.
3. **A level model, not a variance model.** Kronos was trained to predict bars, not to calibrate a
   spread. EWMA may simply be better calibrated.

## Leakage guard

Kronos-small's weights were committed to the Hugging Face Hub on **2025-06-30** at revision `ac5c409a31`;
later commits change only the README. The tokenizer's weights date from the same day. A model whose
weights were frozen on 2025-06-30 cannot have trained on a later bar. So the evaluation window starts
on the **first session on or after 2025-07-01** and ends at the last session in the committed fixtures
(2026-06-01). The window is fixed by the weights' date, not chosen.

## Frozen design

The design is [`config.yaml`](config.yaml).

- **Model.** `NeoQuasar/Kronos-small` at revision `901c26c1…`, with `NeoQuasar/Kronos-Tokenizer-base` at
  `0e011738…`, the two the upstream regression test pins. The code is vendored from upstream commit
  `67b630e` (see `research/kronos/vendor/kronos_model/PROVENANCE.md`).
- **Context.** The 400 sessions ending at t−1 (O, H, L, C, volume, with amount filled as upstream fills
  it). 400 is the lookback in the upstream README's own example and was not tuned.
- **Sampling.** 128 independent one-step draws per name-day, with temperature 1.0, top_k 0 and
  top_p 1.0. Nucleus truncation (upstream's default top_p of 0.9) removes the tails of the distribution
  by construction and would bias a variance forecast down. Sampling the full distribution is the
  only choice that does not decide the answer in advance. Runs on CPU; the seed is
  `20261019 + crc32("SYMBOL|YYYY-MM-DD")`.
- **Forecast.** `F_t = mean_i (Ĉ_t,i / Ĉ_{t−1} − 1)²`. Here Ĉ_{t−1} is the tokenizer's reconstruction
  of the last context close. The encoder and decoder are causal, so it is the same for every draw, and
  anchoring on it cancels the tokenizer's level error. Returns are simple returns, as the league's are.
  The second moment is taken about zero, matching the target.
- **Target.** `r_t²`, from the simple return of the adjusted close, which is the league's target for
  names without minute bars. Kronos reads raw (unadjusted) OHLC. On the 2–4 ex-dividend days a year per
  ETF, its anchor differs from the target's by the dividend yield, which is noted and not corrected.
- **Incumbents.** `models.vol_league.garch_oos` (garch, gjr, egarch) with `est_window` 1000 and refits
  every 21 sessions, plus `ewma_oos` with λ = 0.94, all on the same days. HAR is excluded because the
  league scores it only on minute RV, which these fixtures lack.
- **Names.** SPY, QQQ, IWM, TLT, IEF, GLD, XLE, XLF and XLK, which are every series in
  `fixtures/history/`, adjusted daily closes from Alpaca with provenance sidecars, 2016-06-01 to
  2026-06-01.

## Scoring

QLIKE, in the league's form `ln F + RV/F`. The Diebold–Mariano test uses the league's `dm_test` with
Newey–West variance. The 10% Model Confidence Set uses the league's `mcs_members`.

**PASS requires both:**

1. **Pooled.** On each day, take Kronos's QLIKE minus EWMA's QLIKE and average across the nine
   names. A one-sided Diebold–Mariano test on that daily series must have p < 0.05, with Kronos lower.
2. **Breadth.** Kronos must be in the 10% MCS on at least 5 of the 9 names.

Otherwise the verdict is **DOES NOT BEAT THE INCUMBENTS**. Insufficient data (fewer than 150 evaluation
sessions on any name, or a name failing its fixture provenance) is **INSUFFICIENT DATA**, naming the
name.

**Reported, never gated:**

- MSE.
- Each name's DM test against each incumbent.
- Calibration: the mean of F against the mean of r², and a Mincer–Zarnowitz regression of r² on F.
- 95% VaR from the 5th percentile of the sampled returns, with Kupiec and Christoffersen.
- The fraction of distinct values among the 128 draws, a quantization diagnostic.
- A diagnostic variant anchored on the *actual* last close. It measures the tokenizer's level error
  and is **not eligible to be the result**.

## What a PASS would and would not mean

Even a PASS is descriptive. Kronos sizes nothing, enters no graph node and reaches no desk. Wiring it
into `vol.forecast_league` on the droplet would also need torch in the worker image (about 300 MB of
wheels and over 1 GB resident during inference on a 4 GB host). That is an owner decision, argued
separately.
