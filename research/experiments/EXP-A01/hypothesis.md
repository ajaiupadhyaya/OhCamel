# EXP-A01 — Does the 10-month moving-average rule survive friction on liquid ETFs?

**Paper.** Faber, M. (2007), "A Quantitative Approach to Tactical Asset
Allocation", *Journal of Wealth Management* 9(4). The rule: hold the asset when
its month-end price is above its 10-month simple moving average, otherwise hold
cash. Daily equivalent used here: price above its N-day SMA (fdq's
`ma_crossover` with `fast = 1`), decided at the close, executed at the next open.

**Owner's hypothesis.** The rule reduces drawdown materially in equity and rates
ETFs at the cost of some return, and its risk-adjusted return survives retail
friction.

**What was already seen, disclosed before this run.** For SPY this is not a
first look. The owner's `capitallimits` EXP-002 ran trend-following on SPY over
2016-06-01 → 2022-05-31, walk-forward:
- `ma_crossover` with fast ∈ {10, 20, 50} and slow ∈ {50, 100, 200}: 9
  configurations × 4 folds = 36 fold-trials; Sharpe 0.67, DSR 0.73, PBO 0.725;
- `donchian`: 2 configurations × 4 folds = 8 fold-trials; DSR 0.917, PBO 0.253.

This rule, with fast = 1, was not in that grid, but its family, its asset and
its years were. Three consequences:

- **DSR deflates by every trial in the family, in one unit: fold-trials.** For
  SPY, 44 from EXP-002 plus this experiment's 3 configurations × 4 folds = 12,
  so 56. For TLT, 12. The trial Sharpes are the actual per-fold-trial Sharpes.
  EXP-002's are recovered by re-running its two SPY grids over the selection
  window with the same code, data, friction and seed, which touches no data it
  had not already seen. They are passed with this experiment's own to fdq's
  `deflated_sharpe`, unchanged.
- **Parameter selection and every walk-forward fold use only
  2016-06-01 → 2022-05-31**, the window EXP-002 already saw.
- **The holdout, 2022-06-01 → 2026-06-01, has been touched by no experiment.**
  It is evaluated once, with the parameters selected before it, and the
  "holdout positive" gate reads it alone.

TLT was only ever held inside a 60/40 blend (EXP-001) and never trend-tested.

**Economic rationale.** Time-series momentum: prices under-react to slow
information and trend-following captures the drift, while the other side is
taken by rebalancers and forced sellers in drawdowns who accept the loss for
liquidity or mandate reasons. The rule's main effect is expected to be drawdown
avoidance, not return enhancement.

**Data, stated exactly.** Daily bars from `fixtures/history/`, fetched from
Alpaca by fdq on 2026-06-09, with provenance sidecars. Two facts about them bear
on the result:
- **The volumes are consolidated (SIP), not IEX alone.**
- **The bars are unadjusted: `close_adj == close`, so returns exclude
  distributions.** That is about 3% a year on TLT and less on SPY. It lowers
  every return earned while invested, so it biases this test *against* the rule
  passing, not for it. The moving average is computed on price.

**Assumptions.**
- Selection window 2016-06-01 → 2022-05-31; holdout 2022-06-01 → 2026-06-01.
  The holdout run may read bars before 2022-06-01 only to warm up its moving
  average; returns count only from 2022-06-01.
- Friction model `fdq` v1.0.0, from a vendored copy whose SHA-256 the manifest
  records: per-symbol spread, halved per fill, plus regulatory fees, and ×1.5
  spread widening when VIX is above 25. The VIX series is FRED's, from
  `fixtures/macro/`, as EXP-002 ran it.
- The charter's cost sweep, at 0, 5, 15 and 30 bps: at each level the spread
  is that many basis points round trip for every symbol (fdq halves it per
  fill), fees and the VIX widening stay, and the parameters selected at the base
  friction are **not** re-selected.
- Signals at the close, fills at the next open. Long or flat only, one symbol
  per strategy.

**Grid, fixed in advance.** `fast ∈ {1}`, `slow ∈ {150, 200, 250}` — three
configurations per symbol. Two strategies: `exp_a01_spy` (SPY, equities) and
`exp_a01_tlt` (TLT, rates). Anything outside this grid is a new experiment, not
a tweak.

**How it fails.** Whipsaw in sideways markets generates losing round trips. A
decade dominated by one long uptrend can make any long-biased rule look good
in-sample. The 200-day rule's edge in the literature is mostly pre-2010. Three
regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail. A pass with PBO above 0.5
is reported as "pass, fragile" and is not promoted without a second, independent
window. With three configurations per symbol, "pass, fragile" is the likely best
case, and that is stated now rather than discovered later. Promotion to sizing
is the owner's decision alone, made in `book.sexp`, and never follows from the
verdict by itself.
