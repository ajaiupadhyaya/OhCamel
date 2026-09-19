# EXP-A01 — report

Pre-registration: `hypothesis.md` and `config.yaml`, committed alone in `7235b7b`,
before any line of `battery/` existed. Evidence: `manifest.exp_a01_spy.json` and
`manifest.exp_a01_tlt.json`, written by `ohcamel-research battery run` on the battery
at `639546e`, seed 42, `ran_at` 2026-09-19T04:12:20Z, Python 3.13.12. Every number
below is read from those two manifests. None is computed by hand, except the
distribution bound in the Disclosures, which is arithmetic on the pre-registration's
own figure and is labelled as such.

## Verdict

| Strategy | Verdict | Sizing | verdict_line |
|---|---|---|---|
| `exp_a01_spy` (SPY) | **fail** | advisory | fail: 1 of 5 gates failed (regimes_positive); PBO 0.786, above 0.5 |
| `exp_a01_tlt` (TLT) | **fail** | advisory | fail: 5 of 5 gates failed (holdout_positive, dsr, psr, bootstrap_sharpe_lower5, regimes_positive); PBO 0.981, above 0.5 |

Both strategies fail. The pre-registration's kill criterion is "any charter gate
failing is a fail", and each strategy fails at least one gate.

- **SPY fails one gate, the regimes.** It was positive in 2 of the 5 regimes. Those
  two are 2023 and 2024, the calm bull years the owner named before the run. It was
  negative in all three stress regimes: the 2018 Q4 selloff, the 2020 COVID crash and
  the 2022 rate shock. SPY passes the holdout, DSR, PSR and bootstrap gates. Had its
  regime gate passed, its verdict would have been "pass, fragile", because its PBO is
  0.786, and under the kill criteria it would still not have been promoted without a
  second, independent window.
- **TLT fails every decided gate.** Its holdout is negative. Its DSR, PSR and
  bootstrap lower bound are far below their lines. It was positive in 2 of 5 regimes,
  the 2018 Q4 selloff and the 2020 COVID crash, and negative in 2022, 2023 and 2024.
- **PBO is high for both.** It is 0.786 for SPY and 0.981 for TLT, and both are above
  0.5. With three configurations, a set with no skill scores about 2/3 (see
  Disclosures). Both figures are above that.

The hypothesis says "Three regimes negative would refute it." Three regimes are
negative for SPY and three for TLT.

**The verdict promotes nothing.** Both strategies ship `advisory`. Promotion to
sizing is the owner's decision alone, made in `book.sexp` after reading this report.
It never follows from a verdict. Under the contract's R6, a signal whose
`validation.status` is not `pass` is advisory, logged and never sized. With these
manifests, neither strategy's signals can be sized, whatever `book.sexp` says.

## The gates

The charter's gates, applied literally. The holdout gate reads the holdout series
alone. DSR, PSR, the bootstrap and the regimes read the walk-forward's out-of-sample
series joined to the holdout series (`walk_forward_oos+holdout`, 2,211 bars,
2017-08-11 → 2026-06-01). PBO reads the selection window's three configurations.

### `exp_a01_spy` — SPY, fast 1, slow 150 (advisory)

| Gate | Value | Threshold | Result |
|---|---|---|---|
| Holdout positive (2022-06-01 → 2026-06-01, 1,003 bars) | +0.526 compounded | > 0 | pass |
| Deflated Sharpe Ratio (56 fold-trials) | 0.399 | ≥ 0.30 | pass |
| Probabilistic Sharpe Ratio | 0.977 | ≥ 0.70 | pass |
| Stationary-block bootstrap, lower 5th percentile of annualised Sharpe (1,000 resamples, mean block 21, seed 42) | 0.120 | > 0 | pass |
| Regimes positive | 2 of 5 | ≥ 3 | **fail** |
| PBO, max(port, fdq) | **0.786, high** (port 0.786, fdq 0.786) | reported; above 0.5 named | reported: high |
| Cost sweep | see below | reported at 0 / 5 / 15 / 30 bps | reported |

Bootstrap percentiles: 5th 0.120, 50th 0.713, 95th 1.291.

Regimes (compounded return of the joined series inside each span):

| Regime | Span | Bars | Return | Positive |
|---|---|---|---|---|
| 2018_q4_selloff | 2018-10-01 → 2018-12-31 | 63 | −0.112 | no |
| 2020_covid | 2020-02-19 → 2020-06-30 | 93 | −0.139 | no |
| 2022_rate_shock | 2022-01-01 → 2022-12-31 | 250 | −0.195 | no |
| 2023_recovery (calm bull year) | 2023-01-01 → 2023-12-31 | 250 | +0.111 | yes |
| 2024 (calm bull year) | 2024-01-01 → 2024-12-31 | 252 | +0.212 | yes |

PBO detail: 12,870 CSCV splits over 2016-06-01 → 2022-05-31. The port and fdq agree
exactly. No split is called overfit by one and not the other.

### `exp_a01_tlt` — TLT, fast 1, slow 200 (advisory)

| Gate | Value | Threshold | Result |
|---|---|---|---|
| Holdout positive (2022-06-01 → 2026-06-01, 1,003 bars) | −0.272 compounded | > 0 | **fail** |
| Deflated Sharpe Ratio (12 fold-trials) | 0.003 | ≥ 0.30 | **fail** |
| Probabilistic Sharpe Ratio | 0.234 | ≥ 0.70 | **fail** |
| Stationary-block bootstrap, lower 5th percentile of annualised Sharpe (1,000 resamples, mean block 21, seed 42) | −0.833 | > 0 | **fail** |
| Regimes positive | 2 of 5 | ≥ 3 | **fail** |
| PBO, max(port, fdq) | **0.981, high** (port 0.981, fdq 0.981) | reported; above 0.5 named | reported: high |
| Cost sweep | see below | reported at 0 / 5 / 15 / 30 bps | reported |

Bootstrap percentiles: 5th −0.833, 50th −0.258, 95th 0.248.

| Regime | Span | Bars | Return | Positive |
|---|---|---|---|---|
| 2018_q4_selloff | 2018-10-01 → 2018-12-31 | 63 | +0.027 | yes |
| 2020_covid | 2020-02-19 → 2020-06-30 | 93 | +0.114 | yes |
| 2022_rate_shock | 2022-01-01 → 2022-12-31 | 250 | −0.028 | no |
| 2023_recovery (calm bull year) | 2023-01-01 → 2023-12-31 | 250 | −0.064 | no |
| 2024 (calm bull year) | 2024-01-01 → 2024-12-31 | 252 | −0.158 | no |

PBO detail: 12,870 CSCV splits over 2016-06-01 → 2022-05-31. The port and fdq agree
exactly. No split is called overfit by one and not the other.

## Cost sweep: 0 / 5 / 15 / 30 bps round trip

At each level, every symbol's spread is that many basis points round trip. Fees and
the VIX widening (×1.5 above 25) stay. The parameters are the ones chosen at the base
friction: each fold's own `fold_params` over its test span, then `selected_params`
over the holdout. Nothing is re-selected. "Joined" is the series the gates read.
"Holdout" is the holdout alone.

Annualised Sharpe:

| Level (bps round trip) | SPY joined | SPY holdout | TLT joined | TLT holdout |
|---|---|---|---|---|
| 0 | 0.697 | 1.100 | −0.229 | −1.370 |
| 5 | 0.676 | 1.082 | −0.257 | −1.427 |
| 15 | 0.633 | 1.046 | −0.313 | −1.537 |
| 30 | 0.570 | 0.991 | −0.396 | −1.694 |
| Base friction (SPY 2 bps, TLT 3 bps, from the friction file) | 0.688 | 1.093 | −0.246 | −1.404 |

SPY's Sharpe stays positive at every level, on both series. TLT's is negative at
every level, including zero spread, so friction does not explain TLT's failure.

## The holdout, on its own

2022-06-01 → 2026-06-01, 1,003 bars. The holdout runs once with the parameters
selected on the selection window. Its moving average warms up on bars from
2016-06-01, and its returns count only from 2022-06-01.

| | SPY (slow 150) | TLT (slow 200) |
|---|---|---|
| Sharpe | 1.093 | −1.404 |
| Max drawdown | −0.113 | −0.272 |
| Compounded return (the gate's value) | +0.526 | −0.272 |

## The walk-forward, on its own

Four folds on 2016-06-01 → 2022-05-31 alone. Each fold's out-of-sample test span runs
its own choice. The joined out-of-sample series is 1,208 bars,
2017-08-11 → 2022-05-27.

| | SPY | TLT |
|---|---|---|
| Fold choices (slow), folds 1–4 | 150, 200, 200, 200 | 150, 150, 250, 150 |
| Sharpe | 0.404 | 0.210 |
| Max drawdown | −0.214 | −0.162 |

Fold test spans: 2017-08-11 → 2018-10-22, 2018-10-23 → 2020-01-06,
2020-01-07 → 2021-03-18 and 2021-03-19 → 2022-05-27. Each fold trains on every
selection bar before its test span.

## Sharpe, max drawdown, turnover and capacity

| | SPY | TLT |
|---|---|---|
| Sharpe, joined series (the gates' series) | 0.688 | −0.246 |
| Sharpe, walk-forward out-of-sample | 0.404 | 0.210 |
| Sharpe, holdout | 1.093 | −1.404 |
| Max drawdown, joined series | −0.228 | −0.390 |
| Max drawdown, walk-forward out-of-sample | −0.214 | −0.162 |
| Max drawdown, holdout | −0.113 | −0.272 |
| Turnover, one-way, a year | 4.90 | 3.86 |
| Capacity | $348,230,073 | $12,726,280 |

Both turnover and capacity were measured, so neither reads "none measured". Both come
from each walk-forward fold's own `fold_params` backtest, not from the holdout.
Turnover is the annualised one-way change in weight. A fold's first entry from cash
is counted, and the drop to cash at a fold boundary is not. Capacity is the capital at
which the median trade reaches 1% of the symbol's 20-day dollar ADV on its day, from
consolidated volumes. It measures liquidity headroom, not an edge.

## Returns

Compounded, at base friction:

| | SPY | TLT |
|---|---|---|
| Joined series, 2017-08-11 → 2026-06-01 | +0.857 | −0.209 |
| Walk-forward out-of-sample, 2017-08-11 → 2022-05-27 | +0.217 | +0.086 |
| Holdout, 2022-06-01 → 2026-06-01 | +0.526 | −0.272 |

Across the cost sweep:

| Level (bps round trip) | SPY joined | SPY holdout | TLT joined | TLT holdout |
|---|---|---|---|---|
| 0 | +0.872 | +0.530 | −0.198 | −0.266 |
| 5 | +0.834 | +0.519 | −0.216 | −0.276 |
| 15 | +0.761 | +0.498 | −0.250 | −0.294 |
| 30 | +0.656 | +0.466 | −0.299 | −0.320 |

The battery computes no buy-and-hold benchmark. So this report does not say whether
the rule reduced drawdown relative to holding SPY or TLT, and the hypothesis's
drawdown claim is not tested by any gate here.

## Disclosures

**EXP-002's prior look at SPY.** This was not a first look at SPY. The owner's
`capitallimits` EXP-002 ran trend-following on SPY over 2016-06-01 → 2022-05-31, the
window this experiment selects on. It ran `ma_crossover` with fast ∈ {10, 20, 50} and
slow ∈ {50, 100, 200}, 36 fold-trials, published as Sharpe 0.67, DSR 0.73 and PBO
0.725. It also ran `donchian`, 8 fold-trials, published as DSR 0.917 and PBO 0.253.
EXP-A01's rule, fast 1, was not in that grid, but its family, asset and years were.
EXP-A01 therefore selects only on that window, and it holds out 2022-06-01 →
2026-06-01, which no experiment had touched. That holdout was evaluated with the
parameters selected before it.

**The DSR unit and counts.** DSR deflates by every trial in the family, in one unit:
fold-trials, configurations × folds. The actual per-fold-trial Sharpes are passed to
fdq's `deflated_sharpe`, unchanged.

- **SPY: 56.** That is 36 from EXP-002's `ma_crossover` grid, 8 from its `donchian`
  grid, and 12 from EXP-A01's 3 configurations × 4 folds. EXP-002's 44 were recovered
  by re-running its two SPY grids over the selection window with the same code, data,
  friction, macro series and seed.
- **TLT: 12.** TLT was only held inside EXP-001's 60/40 blend and never trend-tested.

**The data, stated exactly.** The daily bars are from `fixtures/history/`, fetched
from Alpaca by fdq on 2026-06-09, each with a provenance sidecar reading
`synthetic: false`.

- **The volumes are consolidated (SIP), not IEX alone.** The charter's Data section
  says "IEX, daily". These bars are consolidated.
- **The bars are unadjusted: `close_adj == close`, so returns exclude
  distributions.** That is about 3% a year on TLT and less on SPY. It lowers every
  return earned while invested, so it biases this test against the rule passing, not
  for it. The moving average is computed on price.
- **The bias does not change either verdict.** This bound is arithmetic on the
  pre-registration's 3%, not a battery figure. Four years at 3% compound to about
  12.6%, and that assumes TLT was invested every day of the holdout, which it was not.
  Even that ceiling would not turn TLT's −27.2% holdout positive. Each of SPY's three
  negative regimes lost more than 11% within a span of a year or less, which is more
  than a year of distributions at under 3% could recover.

**Ruling 11b: every spread is doubled.** fdq 1.0.0 does not charge a full exit's
half-spread to equity. Its engine credits a sale at the mid less fees and books the
exit's half-spread to the cost ledger only, so each round trip paid half the stated
spread. The runner doubles every spread it hands fdq, under
`FDQ_EXIT_SPREAD_CORRECTION = 2.0`. That covers the friction file's default and
per-symbol spreads, and each sweep level. The entry fill then carries the whole round
trip, so each round trip pays the stated spread.

- It is exact for these strategies, which only enter from flat or exit in full.
- The exit's half is charged at the entry's notional and VIX.
- A position still open at a backtest's end has paid an exit it never made, which is
  conservative.
- The EXP-002 re-run now pays the doubled spread too. As a result, it no longer
  reproduces EXP-002's published numbers. The trial count, 44, is unaffected. The
  trial Sharpes that enter SPY's DSR are the re-run's, not EXP-002's published ones.
- The friction file, `research/config/friction_v1.yaml`, labels its spreads
  "Half-spread in basis points". That comment contradicts both fdq, whose code treats
  the value as the whole spread and halves it per fill, and the hypothesis, which
  states the spread as a round trip halved per fill. The hypothesis governs. The file
  is not edited, because it is hashed.

**Ruling 11c: PBO is max(port, fdq).** fdq's CSCV gives a strategy that is flat
through a sample a NaN Sharpe, and NaN sorts above every number. The battery's port
(`battery/pbo.py`) gives it 0 instead. Each tool's treatment can pull the figure
downward in some case:

- fdq's, when an in-sample winner goes flat out of sample and ranks best;
- the port's, when a flat column fdq had ranked above the best ranks below it, or
  when its 0 is picked in sample over losing columns.

The fragile label therefore reads the higher of the two figures, so neither defect
can flatter it. In this run the two figures are identical for both strategies, and no
split differs in either direction. The rule moved neither figure.

**The owner's regime decision.** The regime gate keeps Alpha's five regimes and the
3-of-5 threshold unchanged, as the owner decided before the run. Two of the five,
2023 and 2024, are calm bull years. A long-or-flat rule on SPY is expected to be
positive in them, so a regime pass on SPY would be weak evidence. In this run SPY was
positive in exactly those two years and in no other, so its regime gate fails.

**PBO with three configurations.** With 3 configurations, a set with no skill at all
scores a PBO of about 2/3, since the in-sample best lands at or below the
out-of-sample median about two times in three by chance. "Pass, fragile" (PBO above
0.5) was therefore the expected label for a passing strategy here, not evidence
against it. The hypothesis said so before the run. Both figures here, 0.786 and
0.981, are above 2/3.

**Walk-forward coverage.** The selection window has 1,511 bars, cut into 5 blocks of
302. The first block, 2016-06-01 → 2017-08-10, only ever trains. The last bar,
2022-05-31, falls in no fold's test span and is in neither the walk-forward series nor
the holdout. That is why the 2022_rate_shock regime has 250 bars, not 251.

Each fold's out-of-sample backtest restarts in cash and re-enters, and so does the
holdout on 2022-06-01. Two regimes are affected:

- Fold 2's restart, on 2018-10-23, falls inside the 2018 Q4 regime.
- The 2022 regime is two backtests joined: fold 4 up to 2022-05-27, then the holdout
  from 2022-06-01. For SPY they use different parameters, slow 200 and then slow 150.

**The warm-up bias.** The history starts at the selection window's start, 2016-06-01.
A slow-N moving average is undefined for its first N bars, so the rule is flat through
them. For slow 250 that is its first year. The bias touches three things:

- the in-sample Sharpes that choose `selected_params`;
- fold 1's training span, 302 bars, of which slow 250 is flat for 250;
- the matrix PBO reads.

It does not touch the fold test spans or the holdout, which warm up on every bar from
2016-06-01. The bias is pre-existing and shared with EXP-002.

**Selection.** `selected_params` is walk_forward's own rule applied to the whole
selection window: the highest per-bar Sharpe, first in grid order on a tie (ruling
11a). It is not the last fold's choice. SPY selected slow 150, while folds 2–4 chose
200. TLT selected slow 200, which no fold chose.

**The cost sweep re-evaluates the holdout. It never re-selects.** The holdout was run
at the base friction, and that run is the one the holdout gate reads. It was then
re-run at each of the four sweep levels with the same `selected_params`. No parameter
choice read any holdout number. `config.yaml`'s `stress_multipliers` [1, 2, 5] are not
read. The sweep is `cost_sweep_bps_round_trip`.

**Reproduction.** The run was made twice. Both runs' manifests are byte-identical
apart from `ran_at`. The manifests committed are the first run's. Each run took about
26 seconds, offline.

**Advisory, not live.** Both strategies are `advisory`. No strategy is `live`.
