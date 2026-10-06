# EXP-Q01 — Does a gradient-boosted cross-sectional ranker beat a linear composite on liquid ETFs?

**Status: APPROVED 2026-10-06.** Drafted by the agent 2026-10-06 for the
owner. Under `docs/CHARTER.md` the owner owns hypotheses and kill decisions;
nothing below runs against the holdout, and no result is published, until the
owner records approval at the bottom of this file.

## The owner's decision this draft needs first

The compute plan (Lane M, M6) specified a LightGBM model on ~600 current S&P
500 / Nasdaq-100 constituents. **The charter forbids that claim**: "Delisted
names are absent from this data source … single-name cross-sectional claims
are out of scope until a survivorship-free source is in." A model trained on
today's survivors learns, in part, that the names which survived did well.
Three options:

- **(A, recommended, drafted below)** Run it on the fixed ETF universe that
  existed throughout the window (survivorship-free by construction).
- **(B)** Buy or source a point-in-time constituent history (compute plan O-4),
  amend the charter's Data section, then run single names.
- **(C)** Drop EXP-Q01; the Models page shows the farm and the regime model only.

## Hypothesis (proposed — the owner's to accept, edit or reject)

On a cross-section of ~45 liquid ETFs (sectors, countries, rates, credit,
commodities), a LightGBM ranker on standard point-in-time features predicts
next-month relative returns better than an equal-weight linear composite of the
same features, and the difference survives costs.

**The baseline is the point.** Per the charter's ML rule, the model is
research-only unless it beats the simple baseline under the same gates. A model
that only matches the linear composite is a negative result, reported as one.

## Economic rationale

The features are documented cross-asset anomalies with stated counterparties:
12-1 month momentum (under-reaction; Asness, Moskowitz & Pedersen 2013,
"Value and Momentum Everywhere"), 1-month reversal (liquidity provision), low
volatility (leverage-constrained investors overpay for high-beta assets;
Frazzini & Pedersen 2014) and carry where defined. The ML question is narrow:
does a non-linear combination add anything over a linear one? The prior is that
it adds little, because 45 assets × ~240 months is a small panel.

## Design, fixed in advance

- **Universe:** the ETFs in `quant` `universes.json` that have daily bars
  continuously from the window start; the list and its date are frozen in
  `config.yaml` before the first run.
- **Features (all at month-end close t, from the warehouse):** momentum 12-1,
  reversal 1m, realized vol 60d, beta to SPY 252d, 20d average dollar volume.
  `assert_point_in_time` (M1) runs on every feature frame.
- **Label:** next-month total return rank, from the open of t+1 (signal at the
  close of t is actionable at the open of t+1 — charter principle 1).
- **CV:** purged walk-forward, 5y train / 1y test / 6-month step, purge = label
  horizon (21 trading days), embargo = 5 trading days (López de Prado 2018 ch. 7).
- **Model:** LightGBM, a grid fixed in advance of at most 8 configurations
  (`num_leaves ∈ {7, 15}`, `min_data_in_leaf ∈ {50, 200}`, `learning_rate ∈
  {0.03, 0.1}`; 300 rounds; early stopping inside the train fold only). Every
  configuration × fold counts as a trial for DSR.
- **Portfolio for the gates:** monthly long top quintile / short bottom
  quintile, equal weight, costs from the shared cost model.
- **Windows:** selection 2008-01 → 2021-12; holdout 2022-01 → the latest month,
  evaluated once with parameters selected before it.

## Gates (the charter's, applied literally)

Holdout positive · DSR ≥ 0.30 · PSR ≥ 0.70 · PBO reported and named if high ·
stationary-block bootstrap lower 5th percentile > 0 · positive in ≥ 3 of the
regimes 2008, 2015–16, 2020, 2022, 2024 · cost sweep 0 / 5 / 15 / 30 bps
reported at every level. **Additionally, the model must beat the linear
composite on holdout net Sharpe.** Reported but not gated: IC and rank-IC with
HAC t-stats, decile spreads, turnover, capacity.

(The Ship spec §6 listed DSR > 0.95 and PBO < 0.2. The charter's gates govern;
changing them is the owner's call, argued before any result is seen.)

## How it could fail

Small-panel overfitting (45 names is thin for trees); regime dependence of
momentum (2009 and 2020 crashes); costs on monthly quintile turnover in thin
ETFs; the linear baseline doing as well, which is the most likely outcome.

## Kill rule

Any gate failing → verdict FAIL, shown first on the Models page; the model
stays advisory forever and is never sized. Nothing reaches the desk under any
verdict (Ship spec §6).

## Approval

`Approved-by:` owner (ajaiupadhyaya), 2026-10-06, option A (ETF universe), approved as written, recorded by the agent from the owner's answer in session.
