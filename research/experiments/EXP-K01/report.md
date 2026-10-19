# EXP-K01 report: Kronos in the volatility league

**Verdict: DOES NOT BEAT THE INCUMBENTS** — POOLED DM vs EWMA p = 0.969 (need < 0.05) · IN 10% MCS ON 3 OF 9 (need 5) · DATA DEFECT: XLE (unadjusted split? 2025-12-05), XLK (unadjusted split? 2025-12-05)

Research only. Kronos sizes nothing, is not a graph node and reaches no desk. The owner approved the pre-registration before any result existed.

Evaluation window 2025-07-01 → 2026-06-01 (sessions after the weights' 2025-06-30 commit), 9 names, target the squared simple return of the adjusted close.

## Gates

| gate | value | stat | mean QLIKE diff | days |
|---|---|---|---|---|
| pooled DM vs EWMA (one-sided, Kronos lower) | 0.9687 | 1.862 | 1.13 | 231 |
| names with Kronos in the 10% MCS | 3 | nan | nan | nan |

## League (mean QLIKE per name; lower is better)

| symbol | egarch | ewma | garch | gjr | kronos |
|---|---|---|---|---|---|
| GLD | -7.302 | -7.272 | -7.259 | -7.324 | -5.555 |
| IEF | nan | -10.55 | -10.52 | -10.53 | -10.49 |
| IWM | -7.462 | -7.744 | -7.768 | -7.81 | -7.671 |
| QQQ | -8.169 | -8.116 | -8.16 | -8.176 | -7.207 |
| SPY | -8.843 | -8.691 | -8.759 | -8.807 | -7.91 |
| TLT | nan | -9.161 | -9.138 | -9.153 | -9.098 |
| XLE | -0.5169 | 1.77 | 0.4127 | 0.3212 | 8.938 |
| XLF | -8.388 | -8.369 | -8.375 | -8.379 | -8.207 |
| XLK | nan | -1.141 | -1.481 | -2.144 | -1.903 |

Rank of Kronos by QLIKE (1 = best of five) and MCS membership:

| symbol | rank_qlike | in_mcs | qlike | mse |
|---|---|---|---|---|
| SPY | 5 | False | -7.91 | 3.013e-07 |
| QQQ | 5 | False | -7.207 | 1.274e-06 |
| IWM | 4 | False | -7.671 | 8.652e-08 |
| TLT | 4 | True | -9.098 | 4.17e-09 |
| IEF | 4 | True | -10.49 | 2.287e-10 |
| GLD | 5 | False | -5.555 | 0.0002172 |
| XLE | 5 | False | 8.938 | 0.0004236 |
| XLF | 5 | False | -8.207 | 3.087e-08 |
| XLK | 2 | True | -1.903 | 0.0002852 |

Mean rank by model across names:

| model | rank_qlike |
|---|---|
| gjr | 1.556 |
| egarch | 2 |
| garch | 3 |
| ewma | 3.111 |
| kronos | 4.333 |

## Reported, not gated

### Diebold–Mariano, Kronos vs each incumbent (negative stat: Kronos lower loss)

| symbol | egarch | ewma | garch | gjr |
|---|---|---|---|---|
| GLD | 6.708 | 6.938 | 6.291 | 7.11 |
| IEF | nan | 1.144 | 0.5483 | 0.7682 |
| IWM | -1.442 | 1.314 | 2.421 | 3.495 |
| QQQ | 6.255 | 5.044 | 6.222 | 6.384 |
| SPY | 6.494 | 4.202 | 5.52 | 5.921 |
| TLT | nan | 1.789 | 1.217 | 1.507 |
| XLE | 1.064 | 1.043 | 1.054 | 1.068 |
| XLF | 3.837 | 2.408 | 3.687 | 3.778 |
| XLK | nan | -0.4995 | -0.318 | 0.4128 |

### Calibration

`mean_forecast_over_mean_r2` above 1 means Kronos forecasts more variance than was realised. Mincer–Zarnowitz `r² = a + b F`: an unbiased forecast has b = 1 (`mz_t_b_eq_1` is the HAC t of b − 1). `distinct_frac_median` is the median share of distinct closes among the 128 draws (quantization). `rec_close_err_median_bp` is the tokenizer's median error reconstructing the last close.

| symbol | mean_forecast_over_mean_r2 | ewma_mean_over_mean_r2 | mz_a | mz_b | mz_t_b_eq_1 | mz_r2 | distinct_frac_median | rec_close_err_median_bp |
|---|---|---|---|---|---|---|---|---|
| SPY | 6.745 | 1.068 | 5.724e-05 | 0.00121 | -28.86 | 2.305e-05 | 0.7109 | 33.06 |
| QQQ | 7.958 | 1.015 | 0.000106 | 0.0002795 | -47.21 | 1.847e-06 | 0.7188 | 43.52 |
| IWM | 1.723 | 1.002 | 0.0001436 | 0.03076 | -13.27 | 0.0004448 | 0.7266 | 28.75 |
| TLT | 1.407 | 1.061 | 3.472e-05 | 0.0474 | -12.19 | 0.0007907 | 0.7109 | 15.94 |
| IEF | 1.482 | 1.023 | 5.367e-06 | 0.2882 | -4.103 | 0.01636 | 0.7578 | 6.052 |
| GLD | 31.61 | 0.9911 | 0.0001865 | 0.01191 | -141.9 | 0.02678 | 0.8047 | 56.03 |
| XLE | 3.055 | 0.9965 | 0.001373 | -0.02915 | -33.53 | 0.0004206 | 0.6719 | 48.41 |
| XLF | 1.872 | 1.041 | 8.214e-05 | 0.002902 | -4.886 | 2.473e-06 | 0.7266 | 28.65 |
| XLK | 1.906 | 0.9931 | 0.001567 | -0.1332 | -13.06 | 0.001214 | 0.6406 | 58.4 |

### 95% VaR from the 5th percentile of the draws

| symbol | breaches | expected | kupiec_p | christoffersen_ind_p | christoffersen_cc_p |
|---|---|---|---|---|---|
| SPY | 4 | 11.55 | 0.008747 | 0.7067 | 0.031 |
| QQQ | 3 | 11.55 | 0.002242 | 0.7783 | 0.009357 |
| IWM | 18 | 11.55 | 0.07081 | 0.6075 | 0.1663 |
| TLT | 15 | 11.55 | 0.3184 | 0.06871 | 0.1141 |
| IEF | 20 | 11.55 | 0.02024 | 0.5993 | 0.09946 |
| GLD | 0 | 11.55 | 1.127e-06 | 1 | 7.524e-06 |
| XLE | 36 | 11.55 | 2.205e-09 | 4.627e-07 | 4.569e-14 |
| XLF | 9 | 11.55 | 0.424 | 0.3446 | 0.4701 |
| XLK | 14 | 11.55 | 0.4735 | 0.04491 | 0.1024 |

### Diagnostic, not eligible: anchored on the actual last close

Pooled DM vs EWMA: stat 3.13, one-sided p 0.999, mean QLIKE diff 0.893. The gap between this and the gated row measures how much of Kronos's variance is the tokenizer's level error rather than a forecast.

## Data defect

On XLE (2025-12-05), XLK (2025-12-05) the committed fixture's adjusted close moves by 40% or more in one session. That is a split the vendor did not adjust (close_adj equals close throughout): every model on those names is scored against a fictitious crash, and Kronos's input bars carry the unadjusted price. The fixture is a published input and is NOT repaired here. The verdict above follows the rule as written; the same rule on the 7 names without a defect (MCS need scaled to the same share, 4):

**DOES NOT BEAT THE INCUMBENTS** — pooled DM vs EWMA one-sided p = 1, in the 10% MCS on 2 of 7 (SPY, QQQ, IWM, TLT, IEF, GLD, XLF).

## Deviations from the pre-registration

An incumbent whose refit could not be fitted is not scored on that name. This is how `league_for` treats a degenerate fit: a number is never guessed for it. It REMOVES A COMPETITOR from that name's MCS, which can only make Kronos's membership there easier, so read those names' MCS rows with that in mind:

| symbol | model | reason |
|---|---|---|
| TLT | egarch | a refit failed (the GARCH fit ended at a non-finite likelihood); not scored |
| IEF | egarch | non-finite variance forecast from a degenerate fit; not scored |
| XLK | egarch | non-finite variance forecast from a degenerate fit; not scored |

