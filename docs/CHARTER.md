# Charter

The governing methodology of this repository. Every strategy, backtest,
signal and report is held to it. **When the charter and a quick result
disagree, the charter wins.** A number that violates it is not a result; it is
a bug that has not been found yet.

It is adopted from the owner's `quant-trading` charter and restated here with
the gates made explicit, because a rule that has to be looked up in another
repository is a rule that gets skipped.

## Prime directive

**The deliverable is a defensible number, never a profitable one.** A
trustworthy negative — "this signal has no edge that survives honest
validation" — is a complete success and is reported with the same confidence
as a positive. Reaching for a change that makes a result look better is the
single failure mode everything here exists to prevent; when the impulse
appears, it is said out loud and written down.

## Division of labour

- **The code builds infrastructure and red-teams results.** Data plumbing,
  validation, execution simulation, tests, reports.
- **The owner owns hypotheses and kill decisions.** No signal is invented
  here; a hypothesis is stated in writing, with its economic rationale, its
  assumptions, and how it could fail, *before* it is run. No gate is loosened
  to admit a strategy someone likes; changing a gate is argued on its own
  merits before seeing which strategies it would admit.

## Principles, in priority order

1. **No lookahead.** Point-in-time data only. A signal computed at the close
   of day *t* is actionable at the open of *t+1*, never at *t*. The core
   enforces this at intake; the backtest enforces it in the fold construction.
2. **Realistic execution.** Every fill pays a spread and a commission, and the
   paper simulator and the backtest read the *same* cost configuration. Cost
   sensitivity is reported as a sweep, never as one number.
3. **Robust validation.** Walk-forward and out-of-sample. Report Sharpe, max
   drawdown, turnover and capacity — not returns alone. In-sample
   outperformance is treated as evidence of a bug until shown otherwise.
4. **Overfitting guarded.** Multiple testing corrected whenever signals or
   parameters are scanned; simple models preferred over parameter-heavy ones.
5. **Reproducible.** Seeded randomness; data versioned by provenance sidecar;
   every run logged with its configuration and its results; a manifest older
   than the last methodology change is stale evidence.

## The gates

Nothing is promoted on a single backtest. Every strategy runs the full
battery and the gates are applied literally:

| Test | Pass |
|---|---|
| Walk-forward, out-of-sample | holdout positive |
| Deflated Sharpe Ratio (multiple-testing corrected) | ≥ 0.30 |
| Probabilistic Sharpe Ratio | ≥ 0.70 |
| Probability of Backtest Overfitting | reported; high values named in the verdict |
| Stationary-block bootstrap, ~1000 resamples | lower 5th percentile > 0 |
| Regime stress across distinct regimes | positive in ≥ 3 |
| Cost sensitivity at 0 / 5 / 15 / 30 bps | reported at every level |

A signal reaches the core with a `validation` block written by the code that
ran the battery. Only `status: pass` may trade; anything else is advisory,
logged, and never sized.

## Data

**No synthetic market data anywhere a number is reported.** Bars come from
Alpaca (IEX, daily) via `fdq`, or from committed slices of that cache, each
with a provenance sidecar. A file without a sidecar, or with
`synthetic: true`, is refused by the replay feed. Delisted names are absent
from this data source; the universe is therefore a fixed list of ETFs that
existed throughout the window, and single-name cross-sectional claims are out
of scope until a survivorship-free source is in.

## Failure modes checked by default

Sign errors in P&L accounting (a hand-computable case in the tests). Stale
validation artefacts (a manifest must post-date the last methodology change).
Mutually inconsistent artefacts (a summary that disagrees with its run files
is discarded). Gates on raw return rather than risk-adjusted lower bounds.
Selection on in-sample metrics. Uncorrected signal scans. Unreported
turnover and capacity.

## Reporting

Lead with the verdict and the gates, not the return. State what failed as
plainly as what passed. Distinguish *advisory* from *live* every time. If a
run cannot be reproduced, that is the finding.
