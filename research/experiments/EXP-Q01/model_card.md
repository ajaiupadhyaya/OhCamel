# EXP-Q01 model card: gradient-boosted ETF ranker vs a linear composite (P5)

**Status:** approved pre-registration (2026-10-06, option A). Job kind `models.xs_lgbm`, monthly.
**Verdict:** shown first on every artifact. The model is advisory under every verdict. Nothing reaches the desk, and nothing is sized.

## Hypothesis (quoted from the pre-registration)

> On a cross-section of ~45 liquid ETFs (sectors, countries, rates, credit, commodities), a LightGBM ranker on
> standard point-in-time features predicts next-month relative returns better than an equal-weight linear
> composite of the same features, and the difference survives costs.

The baseline is the point. If the model only matches the linear composite, that is a negative result and is reported as one. The pre-registration's prior is that the model adds little: 45 assets × ~240 months is a small panel.

## Frozen design

The frozen design is [`config.yaml`](config.yaml). Every value in it is copied from [`preregistration.md`](preregistration.md) or from an interpretation point. In summary:

- **Universe.** The ETFs in `quant/src/ohcamel_quant/universes.json` that pass continuity rule I-Q01-9. The list is frozen once, after the warehouse backfill (see "The universe freeze" below).
- **Features.** Five, all at the month-end close: 12-1 momentum, 1-month reversal, 60-day realized volatility, 252-day beta to SPY, and 20-day average dollar volume. Each frame passes `assert_point_in_time`.
- **Label.** The next month's open-to-open total-return rank, entered at the open after the decision.
- **Cross-validation.** Purged walk-forward: 5-year train, 1-year test, 6-month step. The purge covers the label span, and the embargo is 5 sessions.
- **Model.** LightGBM with L2 regression on the percentile rank. The grid has 8 configurations (`num_leaves` ∈ {7, 15}, `min_data_in_leaf` ∈ {50, 200}, `learning_rate` ∈ {0.03, 0.1}). Each trains for up to 300 rounds, with early stopping inside the training fold. Seed 20261006, deterministic.
- **Portfolio.** Long the top quintile and short the bottom, equal weight, rebalanced monthly. Base cost is 5 bps, charged on turnover at entry. Borrow is 0.
- **Windows.** Selection runs 2008-01 → 2021-12. The holdout runs 2022-01 → latest and is evaluated **once**.

## Interpretation points (fixed before any result)

The pre-registration leaves some details open. I-Q01-1 to I-Q01-9 fix them, and each is written verbatim in `config.yaml` under `interpretations:`. The full text is in the Lane M plan (`docs/superpowers/plans/2026-10-06-lane-m.md`, "Interpretation points"). In brief:

1. **Label span.** The decision is at the month-end close. Entry is at the next session's open. Exit is at the open after the next month-end. Prices are adjusted opens. Each sample belongs to the window in which its label is *realised*, so the 2021-12-31 decision is the first holdout sample.
2. **Ranker.** L2 regression on the cross-sectional percentile rank. It is not lambdarank.
3. **Early stopping.** The last 12 months of each purged training window are the validation set. The model at its best iteration is used without refitting.
4. **Trials.** Each configuration × fold is one DSR trial. The stitched out-of-sample series takes each month from the newest fold that covers it.
5. **Selection.** The highest per-period net Sharpe on the stitched selection series. Ties go to the lower configuration index.
6. **Where each gate is measured.** The regime gate uses the full walk-forward out-of-sample record (selection + holdout). Every other gate uses the holdout alone.
7. **Composite signs.** Momentum +1, reversal −1, volatility −1, beta −1, dollar volume −1. **The owner has not yet confirmed the dollar-volume sign. It must be confirmed before the first holdout run.**
8. **Accounting.** Daily open-to-open returns with drifting weights. Cost is charged at entry: `(1 + net) = (1 + gross)(1 − c × turnover)`.
9. **Universe.** An ETF needs its first bar on or before 2008-01-02, no gap of more than 5 sessions against SPY, and a dividend-adjusted close, judged by **source**. Bars must come from alpaca or yahoo. Any stooq, fixture or unknown bar excludes the ETF.

## Gates (the charter's, applied literally)

- holdout positive
- DSR ≥ 0.30, deflated by every configuration × fold trial
- PSR ≥ 0.70
- stationary-block bootstrap lower 5th percentile > 0
- positive in ≥ 3 of the regimes 2008, 2015–16, 2020, 2022 and 2024
- PBO reported, and named if high
- cost sweep at 0 / 5 / 15 / 30 bps, reported at every level
- **in addition:** the model's holdout net Sharpe beats the linear composite's

If any gate fails, the verdict is FAIL. The following are reported but not gated: IC and rank-IC with Newey–West t-statistics, decile mean returns, annual turnover, and capacity (1% of 20-day dollar volume per position, the median month's binding name).

## Reading the artifact

| Table | What it holds |
|---|---|
| `gates` | One row per gate: value, rule, passed (`None` = reported, not gated). |
| `selection` | The selected configuration, its parameters, PBO, `config_hash` and `methodology_version`. |
| `trials` | Each configuration × fold: per-period net Sharpe and day count. These are the DSR trials. |
| `stitched` | Daily net returns of each configuration on the stitched selection series. Every date is before the holdout. |
| `holdout` | The once-only evaluation: the verdict, every gate metric, IC, turnover, capacity, and when it was evaluated. |
| `holdout_returns` | Daily net returns of the model and of the linear composite over the holdout. |
| `holdout_costs` | Model and composite Sharpe at 0 / 5 / 15 / 30 bps. |
| `holdout_regimes` | Compounded return in each charter regime (full out-of-sample record). |
| `holdout_ic`, `holdout_deciles` | Monthly IC and rank-IC; mean next-month return by prediction decile. |
| `scores`, `model` | This month's advisory ranks from the retrain on the latest 60 months, and the booster. They are never sized. |

## The holdout is evaluated once

The first run that can select a configuration does so, then evaluates the holdout and stores the result in `holdout`. Every later run copies the stored holdout tables forward unchanged. A change to `methodology_version` or to the config hash marks the stored evaluation stale, and the verdict becomes INSUFFICIENT DATA. Only the owner can approve a re-evaluation, by setting `holdout_reevaluation_approved` to the new methodology version.

## The universe freeze

The freeze runs once, after Lane C's warehouse backfill, inside the worker container:

```
python -m ohcamel_quant.products.q01 freeze --warehouse /data/warehouse.duckdb
```

Paste its `universe:` and `universe_frozen_on:` lines into `config.yaml`, keep the `# excluded` lines as a comment block, and commit. Until then every run reports **INSUFFICIENT DATA — universe not frozen**.

## Results

Not yet run. The owner copies the first holdout row here after the first droplet run. There are no numbers until a real run produces them.
