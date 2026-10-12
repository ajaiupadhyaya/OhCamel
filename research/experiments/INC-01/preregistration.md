# INC-01 — Paper incubation of the repository's strategies, scored live

**Status: APPROVED 2026-10-11 for the design §5/§6 as written; the points
marked *not fixed by §5* below await the owner's confirmation, and nothing is
sized until it is recorded here.** Drafted by the agent 2026-10-11 for the
owner. Under `docs/CHARTER.md` the owner owns hypotheses and kill decisions;
no incubation record is published until the owner's approval is recorded at
the bottom of this file. It implements
§5 of `docs/superpowers/specs/2026-10-11-autonomous-paper-trading-design.md`.

**This is not a hypothesis test with a pass mark.** No strategy below passes
the charter's gates. Incubation records each one's first out-of-sample run on
simulated money, under rules written here before the first order, so that
the record can be scored with the charter's statistics and read for what it
is. A strategy that loses money here is a recorded negative, and that is a
complete result.

## What incubation is and is not

The charter, *The gates*, as amended 2026-10-11:

> On the paper venue, and only there, the owner may register a strategy as
> `incubation`: it is sized under the book's incubation caps whatever its
> status, its validation block is carried unchanged, every order, page and
> report it touches says INCUBATION, its realised record is scored with this
> charter's statistics and stop rules the owner wrote before it traded, and
> promotion to `live` never follows from that record by itself. Incubation
> exists so that a strategy's first out-of-sample test on simulated money is
> recorded; it is not evidence of edge and is never reported as such.

Three consequences, stated now:

- **The verdicts are unchanged.** EXP-A01's two manifests say **fail**; the
  farm's cells carry whatever their newest nightly verdict says (none is a
  charter pass as of 2026-10-11). The desk copies each document's
  `validation` block byte for byte into its journal, and every page prints
  the manifest's verdict beside every incubation figure.
- **Paper only, twice over.** The sizing site refuses incubation unless the
  trading host is the compiled-in paper constant, and that constant is the
  only host there is.
- **Nothing here can promote a strategy.** Promotion to `live` sizing is the
  owner's decision alone, made in `book.sexp`, after a passing battery.

## Roster

`research/experiments/INC-01/roster.yaml` is the one list every producer
reads, and it agrees row for row with the `signals` block of
`book.example.sexp` (a research test holds the two together). Rules are the
functions of `quant/src/ohcamel_quant/backtest/strategies.py` for the farm
cells, called by `signals.daily` exactly as the farm's backtest calls them;
universes are `quant/src/ohcamel_quant/universes.json`'s groups.

| Slug | Producer | Rule | Universe | Fraction |
|---|---|---|---|---|
| `exp_a01_spy` | research service | EXP-A01: price above its N-day SMA (Faber 2007), long or flat | SPY | 0.025 |
| `exp_a01_tlt` | research service | EXP-A01: as above | TLT | 0.025 |
| `farm_tsmom_us_equity_indices` | `signals.daily` | `tsmom` (Moskowitz, Ooi & Pedersen 2012) | us_equity_indices: SPY QQQ DIA IWM MDY | 0.025 |
| `farm_tsmom_rates` | `signals.daily` | `tsmom` | rates: BIL SHY IEI IEF TLT TIP | 0.025 |
| `farm_tsmom_commodities` | `signals.daily` | `tsmom` | commodities: GLD SLV USO UNG DBA DBC | 0.025 |
| `farm_tsmom_international` | `signals.daily` | `tsmom` | international: EFA EEM VGK EWJ FXI INDA | 0.025 |
| `farm_tsmom_credit` | `signals.daily` | `tsmom` | credit: LQD HYG EMB | 0.025 |
| `farm_xsmom_sectors` | `signals.daily` | `xsmom` (Jegadeesh & Titman 1993) | sectors: XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC | 0.025 |
| `farm_xsmom_international` | `signals.daily` | `xsmom` | international | 0.025 |
| `farm_dual_momentum_us_equity_indices` | `signals.daily` | `dual_momentum` (Antonacci 2014) | us_equity_indices | 0.025 |
| `farm_dual_momentum_international` | `signals.daily` | `dual_momentum` | international | 0.025 |
| `farm_sma_trend_us_equity_indices` | `signals.daily` | `sma_trend` (Faber 2007) | us_equity_indices | 0.025 |
| `farm_sma_trend_commodities` | `signals.daily` | `sma_trend` | commodities | 0.025 |
| `farm_sma_trend_international` | `signals.daily` | `sma_trend` | international | 0.025 |
| `farm_vol_managed_us_equity_indices` | `signals.daily` | `vol_managed` (Moreira & Muir 2017) | us_equity_indices | 0.025 |
| `farm_risk_parity_rates` | `signals.daily` | `risk_parity` (inverse volatility to a volatility target) | rates | 0.025 |
| `farm_risk_parity_credit` | `signals.daily` | `risk_parity` | credit | 0.025 |
| `farm_risk_parity_commodities` | `signals.daily` | `risk_parity` | commodities | 0.025 |
| `farm_risk_parity_us_equity_indices` | `signals.daily` | `risk_parity` | us_equity_indices | 0.025 |
| `farm_low_beta_sectors` | `signals.daily` | `low_beta` (Frazzini & Pedersen 2014) | sectors | 0.025 |
| `farm_st_reversal_sectors` | `signals.daily` | `st_reversal` (Lehmann 1990) | sectors | 0.025 |
| `farm_pairs_distance_sectors` | `signals.daily` | `pairs_distance` (Gatev, Goetzmann & Rouwenhorst 2006) | sectors | 0.025 |
| `farm_pairs_coint_sectors` | `signals.daily` | `pairs_coint` (Engle & Granger 1987) | sectors | 0.025 |
| `q01_ls_quintile` | `signals.q01` | EXP-Q01's ranker: long the top quintile, short the bottom, equal weight, monthly | EXP-Q01's 34 ETFs | 0.025, held; joins in Stage 4 |

The first 23 rows sum to 23 × 0.025 = 0.575. The last row joins the roster,
and `roster.yaml` and the book, only in Stage 4, at the 0.025 held for it,
making 0.600. Each intraday strategy of EXP-I01–I03 joins only after the
owner approves its pre-registration and its battery has run, and at the
owner's word. Every change to the roster keeps the fractions equal, and is
recorded here, dated, before the first session it applies to.

The research service keeps emitting EXP-A01's two signals unchanged: the same
rule, the same parameters, the same `validation` block. Incubation changes how
the desk sizes them, not what they say.

## Capital

- **Equal fractions of 0.025.** Every roster strategy has
  `capital_fraction` 0.025: 23 × 0.025 = 0.575, and the remaining 0.025 is
  held for EXP-Q01's portfolio, which joins at 0.025 in Stage 4 (0.600).
- `incubation_cap` **0.60** of equity, **a proportional ceiling** (not fixed
  by §5): each incubation strategy's effective fraction is capital_fraction
  × min(1, incubation_cap / Σ incubation capital_fraction). At 0.60 over a
  sum of 0.575 it scales nothing, and each strategy sizes 0.025.
- **The canary.** The first three sessions run at `canary_cap` **0.10**,
  booked as `(incubation_cap 0.10)` over the same roster: each strategy's
  effective fraction is 0.025 × 0.10 / 0.575 = 0.0043478, and the 23
  together size 0.10. Raising it is one edit; the owner raises it to 0.60
  after reading the three sessions' record.
- `vol_target` **0.10** annualised, the overlay's target (below).
- **Gross scaling.** A strategy whose Σ|w| exceeds 1 is scaled to Σ|w| = 1,
  and the document records `gross_scaled: true` among its parameters (and
  so in its `params_hash`). A strategy's fraction is therefore the most
  gross exposure it can ever take, before the overlay. The farm's backtests
  run each cell at the strategy's `default_max_leverage` (2–3× gross for
  `tsmom`, `risk_parity` and `low_beta`) while the desk trades these
  de-levered 1× targets, so a cell's verdict was earned at the farm's
  leverage, not at the leverage traded; `signals.daily`'s artifact says so on
  every run. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*

## The parameter-freeze rule

On the first session of each month the newest `farm.sweep` artifact's
`selected_params` for the cell are read and held for the month. A cell the
farm reports SKIPPED or INSUFFICIENT DATA keeps last month's parameters. A
cell with none is not emitted, and the reason is recorded in the producer's
artifact. The farm's nightly re-selection never reaches the live book inside
a month. A cell the newest farm artifact does not carry at all, a month with
no farm artifact at all, and an ok row that selected no parameters are each
treated as the farm not reporting the cell: last month's parameters are kept
when there are any, and otherwise the cell is not emitted, with the
reason. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)* The cell's `validation` is copied from its newest farm verdict
(`PASS` → `pass`, `FAIL` → `fail`, anything else → `unvalidated`) only when
that row's `selected_params` equal the frozen parameters, else `unvalidated`
naming the mid-month re-selection. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)* A cell
whose universe names a symbol outside the book is refused by the producer,
with the reason, and never silently projected onto the book's names.

**When a document is written.** A cell's document is written only on its
strategy's own rebalance dates (decided against the next exchange session,
with NYSE holidays) and on the cell's first run; `signal`-cadence strategies
write when the targets differ from the last document's beyond the engine's
tolerance; other sessions write nothing and the artifact says `held`; a
period end missed by the producer is caught up late on the next session, and
said so. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)* An all-NaN last row is `held`, an all-zero row is a flat
document. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)* When the live engine answers, a held cell whose last
document the desk did not size -- judged other than accepted or
accepted_incubation, or accepted and refused, not sized, or still pending --
is written again with today's targets, said as `retry`; when the engine does
not answer, the producer keys on the last document it wrote, and says
so. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*

## The overlay

One number, `exposure_scale` in [0, 1], written by `signals.overlay` at
09:40, every 15 minutes to 15:45, and at 18:50; and at 09:20 and 18:40, so
that every moment the desk sizes at (18:45, 19:15, the 09:30 open) has an
overlay younger than `overlay_max_age_minutes` before it. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*

    exposure_scale = min(1, σ_target / σ̂),   σ̂ = √(wᵀ Σ w), annualised

- `w` is the combined targets of every roster strategy, each scaled by its
  fraction; `σ_target` is the book's `vol_target`, 0.10.
- `Σ` is the covariance league's (P3) current winner, by realised GMV
  volatility; Ledoit–Wolf when no league artifact exists. Where the
  volatility league (P2) has a current best model for a name (by QLIKE), its
  forecast variance replaces that name's diagonal entry; EWMA(0.94) when
  absent. How P2 and P3 combine is not fixed by §5. A non-finite forecast
  writes nothing; the desk then holds its last overlay and goes stale. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
  So do bars more than one session behind the previous NYSE session, and a
  materially negative wᵀΣw. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- The document records every input: the models' names, the artifact ids and
  `as_of`, the newest bar read.

Volatility targeting is a rule with a mechanism (Moreira & Muir 2017): risk
is forecastable, return mostly is not, so scaling down when forecast risk is
high improves risk-adjusted return when that holds. It is not assumed to hold
here; it is the sizing rule, not a hypothesis under test.

**The drawdown governor**, computed in the desk from the graph's `drawdown`
node and the book's `dd-cap`: 1 up to half `dd-cap`, falling linearly to 0 at
the cap. The effective scale is `exposure_scale × governor`, and it
multiplies every incubation and live target. With the example book's
`dd-cap` of 0.02, the governor is 1 to a 1 % book drawdown and 0 at 2 %.

**Staleness.** An accepted overlay holds for 60 minutes
(`overlay_max_age_minutes`), and after it the desk uses `overlay_fallback`
0.5; the page says OVERLAY STALE. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)* A desk whose journal holds no accepted
overlay at all reads 1.0 (the desk as it was before this plan) and the page
says OVERLAY ABSENT; before the venue's clock has answered, an overlay cannot
be aged and reads stale, and the desk sizes nothing until it has. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*

**Re-sizing.** When the effective scale moves more than `overlay_band` 0.10
from the scale a strategy was last sized at, every strategy with
`(adjust intraday)` is re-sized from its last accepted document through the
intraday mode. The comparison is of effective scales, before the incubation
ratio; while the overlay is stale the desk holds every last sizing and
re-sizes only on a governor move beyond the band, at the overlay factor the
strategy was last sized with. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*

**Reported beside sizing and never used in it.** EXP-Q02's regime
probabilities, because Q02's verdict is DESCRIPTIVE ONLY and the charter does
not let a descriptive model size anything. P1's Monte Carlo VaR, because P1
computes reference books, not this one. Both appear in the overlay document
for the reader; neither is multiplied in.

## Execution

- **At the open:** daily documents become market-on-open orders, as today.
  The backtest engine fills at the close of d+1 (`execution_lag=1`); the desk
  fills at the open of d+1 by market-on-open, so the farm's fill is not the
  desk's; `signals.daily`'s artifact says so on every run. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- **In the session:** `Day` limit orders at the mark moved
  `intraday_slip_bps` **10 bps** toward the far side, inside the desk's 5 %
  collar.
- **Leg floor:** a leg below `min_leg_notional` **$200** is dropped, and the
  drop is journaled. The floor applies in the intraday mode only, and never
  to a leg that closes a name. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- **Timing:** a document the opening auction's window refuses (before
  19:00 ET) stays pending and is sized at the first pass the window opens;
  nothing is sized before the venue's clock has answered. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- **Waves:** a rebalance of more than 6 legs runs in waves: the shrinking
  legs first, by notional descending, in chunks of at most 6, then the
  growing legs the same way; each wave is gated against the real book as it
  then is, and every wave — a single one included — is awaited until its
  orders fill or `wave_timeout_s` (**180** seconds) passes, when its open
  orders are cancelled and no later wave is sent. An unfilled strategy is
  re-sized on the next pass. *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- **Exits at the close** (not fixed by §5): a strategy with `horizon close`
  exits by a market-on-close order (Alpaca `cls`) sent when the producer's
  15:45 zero-target document arrives; the 15:50 `Day` limit flatten is only
  a backstop when no `cls` order rests.
- Every order passes the desk's rules, then the book's limits on a fork of
  the live graph, then the journal, then the wire, as every order does. The
  kill switch halts and never liquidates.

## Stop rules

Per strategy, written into the book before it trades:

- **Drawdown 0.15** of allocated capital, from the strategy's peak.
- **250 sessions** after its first fill.

Either one stops the strategy: it is flattened by a zero-target rebalance,
recorded with the rule that fired, shown STOPPED, and sized no more. Only the
owner reinstates it, by a `(reinstated DATE)` in the book and a restart. The
desk never un-stops.

## Reporting

Per strategy, from the journal: allocated capital each session (fraction ×
scale × equity), the strategy's position net of its own fills, marks at each
close, so a daily return on allocated capital, and costs per fill.

- **Before 60 sessions:** "session n of 60", and no statistic.
- **From 60 sessions:** annualised Sharpe, PSR (the research battery's own
  formula), maximum drawdown, hit rate, turnover, and cost paid in bps.
- **Always,** on every report and page that shows the record:
  *incubation records are not evidence of edge; promotion to live sizing is
  the owner's decision after a passing battery, never from this record
  alone.*
- The manifest's verdict is printed beside every incubation figure, and
  every row is labelled INCUBATION. Advisory, incubation and live are
  distinguished every time.

## How it fails

- **Sixty sessions is a short record.** A daily Sharpe estimated on 60
  observations has a standard error near 2 annualised; PSR will say so. The
  record is expected to be inconclusive for most strategies for most of a
  year, and it will be reported as inconclusive.
- **Twenty-three strategies, most sharing names.** Some will look good by
  chance. The record is not deflated across the roster because nothing is
  selected on it; if anyone ever selects on it, that selection is a new
  experiment with its own DSR over all 23.
- **Paper fills are Alpaca's simulator's,** marks are IEX, and per-strategy
  orders are not netted: opposite legs pay both spreads. Costs here will be
  higher than a netted book's.
- **The overlay and the governor change every strategy's size together,** so
  the per-strategy records are not independent of each other or of the
  book's drawdown.

## Kill criteria for the program

Any one of these halts the desk, and the owner decides what happens next:

- three strategies stopped within one calendar month; *(amended 2026-10-11 by the agent under a ruling; awaiting the owner's confirmation)*
- a wave timeout on three consecutive sessions;
- any order the journal cannot reconcile with the venue.

## Approval

Approved-by: owner (ajaiupadhyaya), 2026-10-11, approved the design §5/§6 in session ("Looks good, Everything approved"); recorded by the agent. A draft that departs from §5/§6 is not covered by this line.
