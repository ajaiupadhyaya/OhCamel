# INC-02 — The pooled incubation book: one test that can be wrong

**Status: DRAFT, awaiting the owner's approval line by line. Nothing in this file is approved.** No INC-02
session trades, and the kill switch is not reset, until the owner's approval is recorded under *Approval* at
the bottom. Drafted by the agent on 2026-11-24 from `docs/superpowers/specs/2026-11-15-the-completion.md`
(*Research*; rulings R1 and R4). Under `docs/CHARTER.md` the owner owns hypotheses and kill decisions, and
R4 says INC-02 is written by the owner from this draft: every numbered line below is the owner's to approve,
change or strike, and each is marked *(awaiting approval)* until then. A line marked *agent's proposal* fills
a gap the spec leaves open; the spec does not fix it.

INC-02 tests one hypothesis: that the pooled incubation book's Sharpe ratio is above zero. The statistic is
the book's daily return, net of costs, over the 500 sessions from its first fill. The null is a Sharpe of zero
or less, rejected at one-sided 5% when the Probabilistic Sharpe Ratio (PSR) against zero reaches 0.95; T1 proposes
that the return be an excess return over the risk-free rate on the capital deployed. Over
500 sessions that test has about 41% power against a true annualised Sharpe of 1.0 and about 17% against a
true Sharpe of 0.5 (computed, not quoted: `research/tests/test_inc02_power.py`). A non-rejection is the
expected outcome and is not evidence of anything: not that the book has no edge, and not that any strategy
in it has none. A rejection is evidence about the book as a whole, never about one strategy, and it never
promotes anything to live sizing.

## What INC-02 replaces

INC-01 closed at 2026-11-05 (its *Amendment A1* and *Closed*): its drawdown cap was changed from 0.02 to
0.10 after it tripped, by an agent-drafted spec, and on 2026-11-15 the owner did not know whether they had
approved it. INC-01 scored 23 per-strategy records and tested none, and none could have been powered: 60
sessions give a daily Sharpe a standard error near 2 annualised (INC-01, *How it fails*). INC-02 keeps the per-strategy
records as description and puts the one test on the pooled book, with a stated horizon and power (the
completion spec's *What "finished" means*, item 4). It is an operational shakedown of the desk plus that one
hypothesis.

G1. Every rule of INC-01 that the owner approved on 2026-10-11 (design §5/§6) and that this file neither
restates nor changes carries over as INC-01 states it: the incubation tier and its labels, the overlay's
formula, the reporting rules, *How it fails*, and the program's kill criteria other than C17 below.
*(awaiting approval)*

## The test

T1. **The statistic: excess return on deployed capital.** The book's daily return is
`Σᵢ wᵢ (rᵢ − r_f)`, summed over the roster, where:

- `rᵢ` is strategy i's return that day on its allocated capital: its P&L on its own positions, marked to the
  official close, net of spreads, fees, borrow and financing, with dividends credited (the completion spec's
  *Desk*: "the record carries dividends and costs and marks to the official close");
- `wᵢ` is the share of the account's prior-close equity allocated to it that day (its `capital_fraction` ×
  the incubation ratio × the overlay × the governor);
- `r_f` is that day's risk-free rate, the rate the farm uses (`Market.risk_free_daily`).

Idle cash contributes zero. *(agent's proposal: the spec fixes neither the denominator nor the risk-free rate)*

The alternative is the whole account's return less `r_f` on all of its equity. The desk's record has no
interest-on-cash term: the nightly close pulls only the venue's `DIV` and `FEE` activities
(`desk/session_close.ml`, journaled by `desk/journal.ml`), and nothing in `desk/` or `lib/` credits interest on
cash. What Alpaca's paper account itself credits on idle cash is not established here. At most 0.55 of equity is
deployed. So, with no interest on idle cash, that alternative charges a full point of return a year for every
point of `r_f`, where T1 charges at most 0.55 of a point. Against the
fixture proxy's volatility of 0.026 to 0.039 a year (*The drawdown cap, on its merits*, below), each point of
`r_f` is a Sharpe hurdle of:

| Statistic | Hurdle per point of `r_f` | At a 3.5% rate (illustration only) |
|---|---|---|
| The alternative (whole account) | 0.26 to 0.38 | 0.9 to 1.35 |
| T1 (deployed capital) | at most 0.14 to 0.21 | at most 0.49 to 0.74 |

Raw returns with no `r_f` at all would count the rates cells' T-bill carry as edge. The power in T5 is
against the true Sharpe of whichever statistic the owner approves. *(awaiting approval)*

T2. **The horizon.** 500 NYSE sessions from INC-02's first fill, the canary sessions (R4) included. A session
in which the desk is halted or tripped, or holds nothing, still counts, with the return it had (zero when
flat). *(agent's proposal)* *(awaiting approval)*

T3. **The null and the test.** H0: Sharpe ≤ 0. Rejected at one-sided 5% when PSR against zero is at least 0.95,
computed by the research battery's own `psr_gate` (Bailey & López de Prado 2012) on the 500 daily returns of
T1, per session, with their sample skewness and kurtosis. *(awaiting approval)*

T4. **One evaluation.** The test is read once, at the close of session 500. The desk's public page shows
sessions done of 500, the running PSR with its standard error and the stated power (the completion spec,
*Site*); that running figure is a display and never an interim test: nothing is stopped, resized or reported
as a result because of it. *(agent's proposal: a horizon fixed in advance is read once)* *(awaiting approval)*

T5. **The power, and what it assumes.** Under independent, normally distributed daily returns and 252 sessions
a year, the test rejects:

| True annualised Sharpe | 0 | 0.5 | 1.0 |
|---|---|---|---|
| Probability of rejecting H0 in 500 sessions | 5% (the size) | 17% | 41% |

Both figures are checked two ways in `research/tests/test_inc02_power.py`: in closed form (the per-session
Sharpe estimate is asymptotically normal with variance `(1 + SR²/2) / (n − 1)`), and by 10,000 seeded
500-session paths judged by `psr_gate` itself, which reject 40.8% and 17.8% of the time (pinned by the test to
that precision). Fat tails,
autocorrelation and the governor's cuts lower the power; the book's size does not enter, because a Sharpe
ratio does not depend on it. *(awaiting approval)*

T6. **What a result means.** A rejection says the pooled book's Sharpe over these 500 sessions was above zero
at 95% confidence, net of costs; it does not say which strategy earned it, and under the charter nothing is
promoted to live sizing from it. A non-rejection says nothing. Either is reported with the same prominence.
*(awaiting approval)*

T7. **The per-strategy records.** Reported as INC-01's *Reporting* says ("session n of 60", then annualised
Sharpe, PSR, maximum drawdown, hit rate, turnover and cost in bps), always as description: never tested,
never deflated, never selected on. Selecting a strategy on its record is a new experiment, with its own DSR
over all 22. *(awaiting approval)*

## Roster

`research/experiments/INC-02/roster.yaml` is the one list every producer reads; it agrees row for row, in
order, with the `signals` block of `book.example.sexp` (`research/tests/test_experiments_present.py` holds the
two together), and its farm rows are the farm's cells (`quant/tests/test_signals_roster.py`). Rules are the
functions of `quant/src/ohcamel_quant/backtest/strategies.py`, called by `signals.daily` as the farm calls
them; universes are `quant/src/ohcamel_quant/universes.json`'s groups.

R1. **The roster** is these 22 rows. *(awaiting approval)*

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

R2. **Two rows of INC-01 are gone.** `farm_pairs_coint_sectors`: `pairs_coint` takes two tickers and was given
eleven, and the farm dropped the cell in completion task 2.4. EXP-Q01's held slot (`q01_ls_quintile`): Q01's
own pre-registration says "Nothing reaches the desk under any verdict". *(awaiting approval)*

R3. **Capital.** Every row at `capital_fraction` 0.025: 22 × 0.025 = 0.55, and `incubation_cap` **0.55**, a
proportional ceiling (C18), so nothing is held back and the cap scales nothing. `vol_target` 0.10, as in
INC-01. *(awaiting approval)*

R4. **The canary**, as in INC-01: the first three sessions run at `incubation_cap` 0.10 over the same roster
(each strategy at 0.025 × 0.10 / 0.55 ≈ 0.0045), and the owner raises it to 0.55 after reading their record.
*(awaiting approval)*

R5. **The roster is fixed for the 500 sessions.** A stopped strategy's slot stays in the pool and holds cash;
adding a strategy (each of EXP-I01–I03 after its own approval and battery) or removing one starts a new
experiment. *(agent's proposal: a pooled test of a changing book tests nothing in particular)*
*(awaiting approval)*

## Stops

S1. **Drawdown, scaled to the strategy's own volatility.** A strategy stops when its drawdown, from its own
peak, on its allocated capital, exceeds `2.0 ×` its ex-ante annualised volatility at traded parameters,
floored at 0.05 and capped at 0.25: an ex-ante volatility of 0.04 stops it at 0.08, one of 0.02 at the floor
0.05, one of 0.15 at the cap 0.25. INC-01's fixed 0.15 stopped the volatile strategies by chance and the quiet
ones never. The ex-ante volatility is the 252-session realised volatility of the cell's backtest at traded
parameters and gross 1, written by the monthly freeze (completion plan task 2.2; book words
`(stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)`). *(awaiting approval)*

S2. **500 sessions** after its first fill, where INC-01 said 250: the stop and the test's horizon are the
same length. *(awaiting approval)*

S3. **What a stop does**, as in INC-01: the strategy is flattened by a zero-target rebalance, recorded with the
rule that fired, shown STOPPED and sized no more; only the owner reinstates it, by a `(reinstated DATE)` in the
book and a restart. The desk never un-stops. *(awaiting approval)*

S4. **No INC-02 session trades on INC-01's stops.** The desk reads S1 and S2 from the book since completion
task 2.2 (`(stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500) (ex_ante_vol F)`, and a
strategy with no `ex_ante_vol` does not load), and `book.example.sexp` carries them. Its `ex_ante_vol` is measured
for EXP-A01's two rows (`tools/inc02_exp_a01_vol.py`) and is a stated placeholder, 0.075 (twice it is INC-01's
0.15), for the 20 farm rows until the freeze on the droplet's warehouse writes theirs (`tools/book_ex_ante_vol.py`).
*(awaiting approval)*

## The book's limits

L1. **The drawdown cap, `dd-cap`, is 0.10**, argued below. This is the agent's proposal, and the line is the
owner's to approve or to change to another number. *(awaiting approval)*

L2. **The drawdown is measured from the peak of the last 250 session closes**, not from the all-time peak
(`(risk ((drawdown_window 250)))`), so a book the governor cut can earn its size back. *(awaiting approval)*

L3. **The governor's ramp is unchanged**: 1 up to half the cap, falling linearly to 0 at the cap (at 0.10:
cutting from a 5% drawdown, flat at 10%). *(awaiting approval)*

L4. **Reduce through a trip** (`reduce_through_trip true`). While a limit's trip holds the kill switch, an
order that only shrinks a position still passes it -- a stopped strategy's flatten, the governor's cut -- and
everything else is refused; a hand's halt and the engine's stop still refuse every order. This departs from
INC-01's "the kill switch halts and never liquidates", and it is ruling R2, the completion spec's default,
which awaits the owner. *(awaiting approval)*

L5. **A trip is debounced, persisted and lifted only by a reset**: it fires after three consecutive breached
evaluations spanning at least 30 seconds (a print more than 5% from the name's last quote midpoint is dropped
from marks and counted), it is journaled and restored at startup, and only the owner resets it. The agent
never resets the switch. *(awaiting approval)*

### The drawdown cap, on its merits

This argument is made before any INC-02 session trades and before anyone has seen how INC-02 trades under it,
as the charter asks of a changed gate. The 0.10 that the 2026-11-05 spec set has no standing of its own
(INC-01, *Amendment A1*); it is argued here from scratch against 0.02 and 0.05.

*The vol target.* The overlay scales the book toward `vol_target` 0.10 a year and only ever down
(`min(1, σ_target / σ̂)`), and the book's gross is at most 0.55, so a book at its target is the most risk it
is built to carry. At 0.10 a year a book's one-session standard deviation is about 0.63%, its one-year 1σ
loss is 0.10 and its one-year 2σ loss is 0.20. A cap of 0.02 is about 3.2 times the one-session σ at the
target; 0.05 is half a year's 1σ; 0.10 is one year's 1σ and half the one-year 2σ loss.

*The governor's ramp.* The governor starts cutting at half the cap: at 0.02 from a 1% drawdown (1.6 times the
one-session σ at the target, inside one week's 1σ of 1.4%), at 0.05 from 2.5%, at 0.10 from 5%.
Each cut also cuts the test: a book the governor holds near zero records near-zero returns.

*What each cap would have done over the fixture history.* `tools/inc02_ddcap.py` (run:
`cd quant && uv run --frozen python ../tools/inc02_ddcap.py`; its output is committed beside this file as
`ddcap_fixture.txt`) rebuilds the roster on the committed fixtures, offline: the nine Alpaca ETFs of
`fixtures/history/` (2016-06-01 to 2026-06-01, unadjusted, dividends excluded). Each row runs its strategy
at default parameters (no selection) at the traded configuration (gross 1, filled at the next open, the
cost file's half-spreads and borrow), on its universe cut to the fixture ETFs it holds; EXP-A01's rows run
`sma_trend` (10 months) on SPY and TLT; 15 rows run and 7 hold cash (every international and credit row, and
`risk_parity` on commodities, which needs two names and has one). The book is the sum of 0.025 × each row's
return; the window starts when every running row has traded, 2019-06-03, and ends 2026-06-01 (1759
sessions, with the 2020 crash, 2022 and 2024 in it). The governor and the trip are simulated on closes, a
day behind, with the book flat from a trip on.

| Book | Realised vol (a year) | Worst 250-close drawdown | Cap | Episodes reaching cap/2, cap (ungoverned) | Governed: sessions cut | Governed: trip |
|---|---|---|---|---|---|---|
| Rows that run, rescaled to 0.55 | 0.039 | 0.072 | 0.02 | 44, 24 | 3 of 188 | 2020-02-28 |
| | | | 0.05 | 16, 4 | 449 of 1759 | never |
| | | | 0.10 | 4, 0 | 188 of 1759 | never |
| Cash rows held (0.025 each, gross ≤ 0.375) | 0.026 | 0.049 | 0.02 | 35, 13 | 837 of 1759 | never |
| | | | 0.05 | 12, 0 | 284 of 1759 | never |
| | | | 0.10 | 0, 0 | 0 of 1759 | never |
| Bound: SPY held at 0.55 | 0.108 | 0.200 | 0.02 | 64, 46 | 4 of 45 | 2019-08-05 |
| | | | 0.05 | 32, 18 | 64 of 194 | 2020-03-09 |
| | | | 0.10 | 18, 11 | 13 of 199 | 2020-03-16 |

*What that can and cannot show.* It can show the shape of the trade-off on real prices: on the proxy book,
0.02 trips or keeps the book cut for a large share of sessions, 0.05 keeps it cut for a quarter of them
(449 of 1759), and 0.10 cuts it on about a tenth (188) and never trips, while a book that behaved like SPY at
the book's gross trips even 0.10, in the 2020 crash. It cannot show what the roster itself would have done:
the fixtures hold 9 of the roster's 42 ETFs, seven rows are cash and the rest run on cut universes at default
parameters, the overlay is not simulated, and the proxy's volatility (0.026 to 0.039) is far below the live
book's. That last point is the strongest evidence there is, and it is not from the fixtures: INC-01's live
book tripped the 0.02 cap in its sixth session of full-roster trading (the completion spec, *Where things
stand*: "The paper record is six sessions of full-roster trading").

*The proposal.* 0.10: one year's 1σ at the vol target, so the cap binds on losses the book was not sized to
take, not on ordinary weeks; on the proxy it never trips and seldom cuts, and it still trips a book that
behaves like equity in a crash. The case against it is the size of the loss it allows, $100,000 on
$1,000,000 of paper equity, against which 0.05 is the nearest alternative at the cost of a book cut for about
a quarter of its sessions. The owner decides L1.

## The parameter freeze

F1. **The freeze rule, restated.** On the first session of each month the newest `farm.sweep` artifact's
`selected_params` for the cell are read and held for the month. A cell the farm reports SKIPPED or
INSUFFICIENT DATA keeps last month's parameters. A cell with none is not emitted, and the reason is recorded
in the producer's artifact. The farm's nightly re-selection never reaches the book inside a month.
(`quant/src/ohcamel_quant/signals/freeze.py`, `KEEPS_LAST_MONTH`.) *(awaiting approval)*

F2. **What F1 does under the gates of ruling R3, stated plainly.** R3 (which itself awaits the owner) makes a
cell whose out-of-sample record has data in fewer than 3 of the 7 regimes of `research/config/regimes.yaml`
read INSUFFICIENT DATA, and F1 then keeps last month's parameters, while `signals.daily` marks the cell
`unvalidated`. Incubation sizes whatever the validation says, so sizing does not change. But the table's last
regime is 2024, and a cell's out-of-sample record starts 1,260 sessions (about five years) after its
universe's first common session. A cell whose universe begins after about mid-2015 can therefore never reach
3 regimes. That is likely the four sector cells (`xsmom`, `low_beta`, `st_reversal` and `pairs_distance` on
sectors): XLC began trading in June 2018, so their out-of-sample records begin about mid-2023 and can reach
only 2024. This estimate is from listing dates and has not been run on the live warehouse. For those cells
F1 means:

- their parameters are pinned forever to the last month before they first read INSUFFICIENT DATA, and the
  farm's later selections never reach the book;
- a cell that has never had parameters is never emitted, so it never trades;
- the page shows them `unvalidated` for the whole of INC-02.

The owner approves F1 and F2 as they stand, changes them (for instance: an INSUFFICIENT DATA cell takes the
farm's newest selection like any other verdict, or the sector cells leave the roster and R1 and R3 are
re-equalised), or strikes them. *(awaiting approval)*

## INC-01's clauses that awaited the owner's confirmation

INC-01 marked 17 clauses "awaiting the owner's confirmation" and three points "not fixed by §5"; the owner
never confirmed them. Each is restated here as its own line, with what has changed since where something has.

C1. **Gross scaling.** A strategy whose Σ|w| exceeds 1 is scaled to Σ|w| = 1, and its document records
`gross_scaled: true` among its parameters (and so in its `params_hash`); a strategy's fraction is the most
gross exposure it can take, before the overlay. Since completion task 2.4 the farm scores every cell at gross
1, filled at the open of the next session, so a cell's verdict and its traded leverage now agree;
`signals.daily`'s artifact says per row which configuration a verdict was earned at. *(awaiting approval)*

C2. **The farm not reporting a cell.** A cell the newest farm artifact does not carry at all, a month with no
farm artifact at all, and an ok row that selected no parameters are each treated as the farm not reporting the
cell: last month's parameters are kept when there are any, and otherwise the cell is not emitted, with the
reason. *(awaiting approval)*

C3. **Validation follows the frozen parameters.** A cell's `validation` is copied from its newest farm
verdict (`PASS` → `pass`, `FAIL` → `fail`, anything else → `unvalidated`) only when that row's
`selected_params` equal the frozen parameters; otherwise `unvalidated`, naming the mid-month re-selection.
*(awaiting approval)*

C4. **When a document is written.** Only on the strategy's own rebalance dates (decided against the next
exchange session, with NYSE holidays) and on the cell's first run; `signal`-cadence strategies write when the
targets differ from the last document's beyond the engine's tolerance; other sessions write nothing and the
artifact says `held`; a period end the producer missed is caught up late on the next session, and said so.
*(awaiting approval)*

C5. **Empty rows.** An all-NaN last row is `held`; an all-zero row is a flat document. *(awaiting approval)*

C6. **Retry.** When the live engine answers, a held cell whose last document the desk did not size -- judged
other than accepted or accepted_incubation, or accepted and refused, not sized, or still pending -- is written
again with today's targets, said as `retry`; when the engine does not answer, the producer keys on the last
document it wrote, and says so. *(awaiting approval)*

C7. **The overlay's schedule.** `signals.overlay` writes at 09:20, at 09:40 and every 15 minutes to 15:45, and
at 18:40 and 18:50, so every moment the desk sizes at has an overlay younger than `overlay_max_age_minutes`.
*(awaiting approval)*

C8. **The overlay's inputs.** `Σ` is the covariance league's current winner by realised GMV volatility,
Ledoit–Wolf when no league artifact exists; where the volatility league has a current best model for a name
(by QLIKE), its forecast variance replaces that name's diagonal entry, EWMA(0.94) when absent. A non-finite
forecast writes nothing, and the desk holds its last overlay and goes stale. *(awaiting approval)*

C9. **Inputs too old or impossible.** Bars more than one session behind the previous NYSE session, and a
materially negative `wᵀΣw`, also write nothing. *(awaiting approval)*

C10. **Staleness.** An accepted overlay holds for 60 minutes (`overlay_max_age_minutes`); after it the desk
uses `overlay_fallback` 0.5 and the page says OVERLAY STALE. Since the completion spec (*Unknown is not
permission*), an overlay older than four session hours (`overlay_max_age` 4.0) decays to the fallback, and a
governor that cannot be read is the fallback, not 1.0. *(awaiting approval)*

C11. **No overlay at all.** A desk whose journal holds no accepted overlay reads 1.0 (the desk as it was before
INC-01) and the page says OVERLAY ABSENT; before the venue's clock has answered, an overlay cannot be aged and
reads stale, and the desk sizes nothing until it has. *(awaiting approval)*

C12. **Re-sizing.** When the effective scale moves more than `overlay_band` 0.10 from the scale a strategy was
last sized at, every strategy with `(adjust intraday)` is re-sized from its last accepted document. The
comparison is of effective scales, before the incubation ratio; while the overlay is stale the desk holds
every last sizing and re-sizes only on a governor move beyond the band, at the overlay factor the strategy was
last sized with. *(awaiting approval)*

C13. **At the open.** Daily documents become day limit orders priced from the quote at send time (a buy 10 bps
above the ask, a sell 10 bps below the bid), sent from 09:35 New York; no `OPG` orders (the completion spec, *Orders are
priced off the quote*, which replaced INC-01's market-on-open). The farm scores fills at the open of the next
session (task 2.4); the desk's fill is a limit near it, after 09:35; `signals.daily`'s artifact says so.
*(awaiting approval)*

C14. **The leg floor.** A leg below `min_leg_notional` $200 is dropped, and the drop is journaled. The floor
applies in the intraday mode only, and never to a leg that closes a name. *(awaiting approval)*

C15. **Timing.** A document the session window refuses (the session closed, before 09:35, no venue clock yet)
stays pending and is sized at the first pass the window opens; nothing is sized before the venue's clock has
answered. *(awaiting approval)*

C16. **Waves.** A rebalance of more than 6 legs runs in waves: the shrinking legs first, by notional
descending, in chunks of at most 6, then the growing legs the same way; each wave is gated against the real
book as it then is, and every wave, a single one included, is awaited until its orders fill or
`wave_timeout_s` (180 seconds) passes, when its open orders are cancelled and no later wave is sent. An
unfilled strategy is re-sized on the next pass. *(awaiting approval)*

C17. **The program's kill criterion on stops.** Three strategies stopped within one calendar month halts the
desk, and the owner decides what happens next. *(awaiting approval)*

C18. **`incubation_cap` is a proportional ceiling** (INC-01: "not fixed by §5"): each incubation strategy's
effective fraction is `capital_fraction × min(1, incubation_cap / Σ capital_fraction)`. At 0.55 over a sum of
0.55 it scales nothing. *(awaiting approval)*

C19. **How the two leagues combine** (INC-01: "not fixed by §5"): the covariance league's winner supplies `Σ`,
and the volatility league's best model replaces only the diagonal entry of each name it covers (C8).
*(awaiting approval)*

C20. **Exits at the close** (INC-01: "not fixed by §5"): a strategy with `horizon close` exits by a
market-on-close order (Alpaca `cls`) sent when the producer's 15:45 zero-target document arrives; the 15:50
`Day` limit flatten is only a backstop when no `cls` order rests. No roster row has `horizon close` today.
*(awaiting approval)*

## Approval

Approved-by:
