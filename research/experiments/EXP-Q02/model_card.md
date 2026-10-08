# EXP-Q02 model card: HMM regime probabilities vs EWMA (P6)

**Status:** approved pre-registration (2026-10-06). Job kind `regime.hmm`, weekly (Saturday 09:00 New York).
**Verdict:** shown first on every artifact. This is a descriptive risk model, not a strategy. It sizes nothing and reaches no desk.

## Hypothesis (quoted from the pre-registration)

> The filtered (real-time) probability of the high-volatility state from a Gaussian HMM on market and macro
> features predicts next-month realized SPY volatility better than an EWMA(λ = 0.94) volatility forecast alone.

The expected failure mode, stated in advance, is an HMM that only re-describes trailing volatility. If that happens, EWMA matches it.

## Frozen design

The frozen design is [`config.yaml`](config.yaml). Every value in it is copied from [`preregistration.md`](preregistration.md) or from an interpretation point. In summary:

- **Features.** Four, weekly and point in time:
  - SPY 4-week return
  - SPY 4-week realized volatility
  - the 10y–2y slope (FRED DGS10 − DGS2)
  - the 1-week change in the Baa − 10-year credit spread (FRED BAA10Y; v2, replacing BAMLH0A0HYM2)

  Each is standardized on an expanding window of at least 52 weeks.
- **Model.** A Gaussian HMM with full covariance, fitted by our own EM (`quant/src/ohcamel_quant/models/hmm.py`, scaled Baum–Welch). The number of states K ∈ {2, 3, 4} is chosen by BIC inside each training window. Each fit uses 20 seeded restarts (seed 20261006), and the best likelihood is kept. States are ordered by their mean standardized 4-week volatility, which fixes label switching.
- **Refits.** Expanding window, every 26 weeks from the first 2005 week-end, with at least 156 training weeks. Only **filtered** probabilities are scored. Smoothed probabilities use later data, so they are shown only as labelled history (`p_high_smoothed_history`).
- **Target.** Next-month SPY realized variance: the sum of squared daily log returns over the 21 sessions after the week-end. The EWMA forecast is `21 × σ²_{t+1}`, with RiskMetrics λ = 0.94.
- **Windows.** The selection window runs through 2021-12-31, judged by the end of each week's target. The holdout runs 2022-01-01 → latest and is scored **once**.

## Interpretation points (fixed before any result)

I-Q02-1 to I-Q02-7 are written verbatim in `config.yaml` under `interpretations:`. The full text is in the Lane M plan (`docs/superpowers/plans/2026-10-06-lane-m.md`, "Interpretation points"). In brief:

1. **Weekly sampling.** Each week is sampled at its last session that has a later session.
2. **Credit change.** The 1-week change in BAA10Y (v2; v1 used BAMLH0A0HYM2). Every FRED value is used from the business day after its date.
3. **Standardization.** Expanding, through t inclusive, with at least 52 weeks.
4. **Target and EWMA forecast.** As in the design above.
5. **Refits.** Every 26 weeks. The high-volatility state is the state with the highest mean standardized `rv_4w`.
6. **Scoring.**
   - Selection weeks are those whose whole target window ends by 2021-12-31. Weeks whose target straddles the boundary are purged.
   - Two OLS regressions: target ~ 1 + EWMA, and target ~ 1 + EWMA + P(high). Newey–West errors use `max(4, nw_lag_rule(n))` lags.
   - The coefficients are frozen and applied to the holdout. Forecasts are floored at the selection minimum.
   - QLIKE is `RV/F − ln(RV/F) − 1`. The Diebold–Mariano test is one-sided, with the same lag rule.
7. **Sufficiency.** At least 260 selection weeks and 26 holdout weeks are needed, and the first refit's 156 weeks must be covered by every feature series. Otherwise the verdict is INSUFFICIENT DATA, naming the short series.

## Pass criteria (from the pre-registration)

**PASS** requires both:
- holdout QLIKE lower than EWMA's, with a one-sided DM p < 0.05;
- a HAC t-statistic > 2 on the probability term in the selection window.

Otherwise the verdict is **DESCRIPTIVE ONLY — adds nothing over EWMA**, and the page still shows the regimes, labelled with that verdict. Incremental R² is reported in selection and in the holdout but is not gated. A PASS is still descriptive. It never sizes anything.

## Reading the artifact

| Table | What it holds |
|---|---|
| `gates` | Selection HAC t on P, the holdout DM p-value, and incremental R² in both windows (reported). |
| `probs` | Weekly filtered state probabilities (`p0`…`p3`, `p_high`) from the latest refit on or before each week, plus `p_high_smoothed_history`. The smoothed column uses later data and is display-only. |
| `fits` | One row per refit: the chosen K, log-likelihood, BIC, training weeks, and convergence. |
| `regression` | The frozen selection-window coefficients of both regressions. |
| `holdout` | The once-only score: DM statistic and p-value, QLIKE of both forecasts, HAC t, R², window bounds, verdict, and when it was evaluated. |
| `holdout_forecasts` | Weekly holdout target, EWMA, and both floored forecasts. |

## The holdout is scored once

The first run with sufficient data scores the holdout and stores it. Later runs refresh the probabilities but copy `holdout`, `holdout_forecasts`, `regression` and `gates` forward unchanged. A change to `methodology_version` or to the config hash marks the stored score stale, and the verdict becomes INSUFFICIENT DATA. Only the owner can approve a re-score, via `holdout_reevaluation_approved`. A run that was INSUFFICIENT DATA stored no score, so the next sufficient run scores the holdout for the first time.

## Data caveat

FRED serves its ICE BofA series (BAMLH0A0HYM2) only from 2023-10, which is why v2 (2026-10-08, owner-approved) uses BAA10Y. If any input's history does not cover the first refit's 156 weeks (from 2005), the artifact says INSUFFICIENT DATA and names the series. The code never substitutes a series on its own; any change is a pre-registered amendment.

## Results

Not yet run. The owner copies the first holdout row here after the first droplet run. There are no numbers until a real run produces them.
