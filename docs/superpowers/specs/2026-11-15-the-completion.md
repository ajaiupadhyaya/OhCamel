# The completion — 15 Nov 2026

A four-lane review on 2026-11-15 (research, desk, math, data) plus a read of the live engine found a sound core with
specific defects. This spec is the decisions that close them. The plan is
`docs/superpowers/plans/2026-11-15-the-completion.md`.

## Where things stand

- The desk has been halted since 2026-11-05 19:53 UTC, tripped by `dd-cap` at 2%, with 31 positions on. `honest-book`
  (seven fix commits) is built, not merged, not deployed.
- The paper record is six sessions of full-roster trading. Equity $989,914 on $1,000,000.
- The farm is 0 pass. EXP-A01 and EXP-K01 failed. EXP-Q01 has no result. No strategy has shown edge, and the site says so.
- The droplet is 2 shared vCPUs and 3.9 GB, idle most of the day.

## What this project is

The owner's answer on 2026-11-15: **a showcase of methods**, not a book to be funded. That sets the order and the cuts.

- What a visitor can see being wrong comes first: a density that integrates to 1.2, an in-the-money put with seven
  points of false volatility, a league won by construction, a ledger three weeks stale.
- The desk is the demonstration that the methods run unattended on a live feed. It must be safe to leave alone and its
  record must be true. It does not need what only real money needs: order netting across strategies, locate and
  short-sale rules, buying-power checks, a second venue. Those are cut, and listed under *Cut* so nobody mistakes
  their absence for an oversight.
- Depth is added as scored products on real data, because a formula on a page is decoration.

## What "finished" means

The project is finished when all of these hold. Each is checked by a test or a command in the plan's last stage.

1. **The desk can be left alone.** A limit trip de-risks instead of freezing, survives a restart, cannot fire on one
   print, and lifts by a stated rule. A dead overlay or an unevaluable limit refuses risk, never grants it.
2. **The record is true.** Per-strategy P&L includes dividends and costs and is marked to official closes; strategy
   sub-ledgers sum to the account every night or the desk says which name breaks.
3. **Every verdict is about the thing that trades.** The farm is scored at the traded leverage and fill, against its
   universe's buy-and-hold, with tiered costs, borrow and a capacity figure, and every trial ever run is counted.
4. **The live experiment can reject something.** One pooled test with a stated horizon and power replaces 23 unpowered ones.
5. **Every published number can be regenerated** from a code SHA, a data hash and a seed, against a snapshot that exists.
6. **Every formula on the site is right for the instrument it is applied to.** No density that integrates to 1.2, no
   American option inverted as European, no league that scores forecasts of a different quantity.
7. **The site says what is true today.** The ledger is generated from the running system, not typed.

**Cut** (real-money concerns a showcase does not need): netting orders across strategies and pro-rata fill allocation,
locate and short-sale-restriction handling, buying-power checks, a net-beta limit, a second venue, any path to real money.
Also out of scope: intraday strategies (EXP-I01–I03 stay unrun) and a redesign of any page.

## Rulings the owner must give

Each has a default. The plan proceeds on the defaults; a task that depends on a ruling says so, and its commit carries the
ruling in an `Approved-by:` line before the code that depends on it reaches the live host.

| | Question | Default |
|---|---|---|
| R1 | Is `dd-cap` 0.10 (honest-book) the owner's decision? | **Answered 2026-11-15: the owner does not know.** So it is recorded as what it was: a limit changed after it tripped, by an agent-drafted spec, without the owner's knowing approval. INC-01's record closes at 2026-11-05 and says so. The 0.10 value is argued on its merits in INC-02 and takes effect only with the owner's explicit approval there. |
| R2 | May risk-reducing orders pass a limit trip? INC-01 says "the kill switch halts and never liquidates". | Yes for a limit's trip, never for a hand's halt or the engine's stop. |
| R3 | Tighten the charter's gates? | PSR ≥ 0.95, DSR ≥ 0.95, and a new gate: excess over the universe's buy-and-hold, SPA p ≤ 0.10. Nothing passes today, so no strategy is admitted or removed by this. |
| R4 | Replace INC-01 with a new pre-registration? | Yes: INC-02, written by the owner from the agent's draft, approved before the kill switch is reset. |
| R5 | Monthly data budget | **Answered 2026-11-15: up to $50.** Spend it on delisted names first: EODHD "EOD Historical Data — All World", about $20/month, reported to carry delisted tickers with 30+ years of history. That is unconfirmed from the vendor's own pages, so the adapter ships with an acceptance test on known failures and nothing depends on it until the test passes on the owner's key. Optional second, about $29/month: Massive (formerly Polygon) Stocks Starter, for minute bars from the whole market in place of IEX's 2–3%. Full SIP at $99 does not fit. Prices are from third-party listings in November 2026; the owner checks the live pricing pages before paying. |
| R6 | Resize the droplet? | No. Heavy research runs on a burst machine against the nightly export. |

## Decisions

### Desk

**A limit's trip is a state with an exit.** Three changes, one behaviour:

- *Reduce-only.* While `Halt.State.Tripped`, an order passes the `kill_switch` rule if it shrinks both the strategy's own
  position and the account's position in that name, and is no larger than either. Everything else is refused as now.
  `Halted` and `Stopped` refuse everything, as now.
- *Persisted.* A trip is journaled when it happens and restored at startup, as a hand's halt is. Only `reset` lifts it.
- *Debounced.* A trip-limit raises after three consecutive breached evaluations spanning at least 30 seconds. A trade
  print more than 5% from the name's last quote midpoint is dropped from marks and counted.

**Drawdown is measured from the peak of the last 250 session closes**, not from the all-time peak. The governor's ramp
(`cap/2` to `cap`) is unchanged.

**Unknown is not permission.** A trip-limit the gate cannot evaluate refuses an order that increases gross. An overlay
older than four hours in session decays to `overlay_fallback`; a governor that cannot be read is `overlay_fallback`, not 1.0.

**One sub-ledger check for the desk.** Per name, the sum of strategies' own positions is compared with the account. A
break names the symbol and both figures, refuses orders that increase exposure in that name only, and raises one alert.
The per-strategy `account_refusal` goes.

**Orders are priced off the quote.** Orders are day limits priced from the quote at send time (bid/ask ± 10 bps, the
existing band), sent from 09:35 New York. No `OPG` orders. Share counts use the current mark. The completion count is
journaled. Strategies still send their own orders; opposite sides in one name still pay two spreads, and the record's
cost column now shows it.

**The record carries dividends and costs and marks to the official close.** Account activities `DIV` and `FEE` are
journaled nightly; a dividend is credited to strategies pro rata to their own position at the prior close.
`Record.session.cost` is the sum over fills of `|qty| × |fill price − arrival midpoint|` plus fees. The close is the
venue's daily bar close, falling back to the last print with a note.

**Limits fit the book.** New limit kinds: `Class_gross` (fraction of equity per asset class) and `Name_weight` (fraction
of equity in any one instrument, default 0.15). The tripping risk limit is 1-day 99% expected shortfall over 250 sessions.

**The model is scored.** Each night the journaled VaR forecasts are tested (Kupiec, Christoffersen, traffic light) and
the result is a table on `desk.report`.

### Research

**INC-01 closes at 2026-11-05; INC-02 replaces it.** INC-02 is an operational shakedown plus one hypothesis:

- *The test.* The pooled incubation book's daily return, net of costs, over 500 sessions from the first fill. Null:
  Sharpe ≤ 0. Reject at one-sided 5% using PSR. The pre-registration prints the power: about 41% against a true Sharpe
  of 1.0, about 17% against 0.5. It says in the first paragraph that a non-rejection is the expected outcome and is not
  evidence of anything.
- *Stops are vol-normalised.* A strategy stops when its drawdown exceeds `2.0 ×` its ex-ante annualised volatility at
  traded parameters, floored at 0.05 and capped at 0.25. The 250-session stop becomes 500.
- *Roster.* The farm's cells that can run, at 0.025 each. `farm_pairs_coint_sectors` is removed (it needs two tickers
  and is given eleven); the reserved EXP-Q01 slot is removed (Q01's own pre-registration forbids it).
- Every clause in INC-01 marked "awaiting the owner's confirmation" is restated in INC-02 and approved or struck.

**The verdict is about the traded configuration.** The farm scores each cell at gross 1×, filled at the open of d+1,
which is what `signals.daily` trades.

**Gates (R3).** PSR ≥ 0.95. DSR ≥ 0.95. Excess: SPA p-value against the universe's equal-weight buy-and-hold ≤ 0.10.
Regimes: one table, in one file, read by both batteries; fewer than three regimes with out-of-sample data is
`INSUFFICIENT DATA`, not `FAIL`. `expected_max_sharpe` with fewer than two trials raises.

**Costs.** One file, `research/config/costs.yaml`, read by the backtest, the farm and `signals.daily`: half-spread by
tier (tier 1: 1 bp, tier 2: 3 bp, tier 3: 8 bp, assigned per ticker), borrow 50 bp/y on shorts, financing at the
risk-free rate plus 50 bp on gross above 1. The sweep multiplies the half-spread by 0, 1, 3, 6 and runs on the gated
out-of-sample series. Capacity is the capital at which the largest rebalance trade is 1% of 20-day median dollar volume;
turnover and capacity are leaderboard columns.

**Every trial is counted.** An append-only trial ledger records each farm trial's Sharpe with its grid version. DSR's N
is the ledger's distinct trials, not tonight's grid.

### Data

**One read path repairs splits.** `readers.bars_panel` applies `splits.present`; EXP-Q01, the Machine and the vol league
read through it; `signals.daily` hashes the frame it used.

**Raw bars and a corporate-action table.** `bars_raw` keeps unadjusted OHLCV; `corp_actions` keeps splits and dividends
with ex-dates from Alpaca. Adjusted series are derived at read time. The existing `bars_daily` table stays until the
derived series matches it within 1e-6 on every ticker for 30 days, then it is dropped.

**A number can be regenerated.** The manifest gains `data_hash` (SHA-256 over the rows the job read), `seed`, and
`lock_hash` (of the installed lock file). A nightly `EXPORT DATABASE` writes Parquet to `/var/backups/ohcamel/warehouse/`,
newest 14 plus month-ends kept. The restore drill is run once and its transcript committed.

**Options snapshots carry what a surface needs:** `spot`, `quote_ts`, `vendor_iv`, and a second pull at 15:45. A
back-processed day uses that day's curve.

**Membership has dates.** `universe_membership(universe, ticker, start, end)` from Wikipedia's changes table.

**Delisted names, behind a switch (R5).** An EODHD adapter fetches end-of-day bars and corporate actions for any ticker
that was ever a member, delisted ones included, into the same raw tables. `OHCAMEL_DELISTED_SOURCE=eodhd` turns it on.
It is accepted only when a committed list of known failures and acquisitions (Lehman, Bear Stearns, Washington Mutual,
Silicon Valley Bank, First Republic, Bed Bath & Beyond, and twenty more with their last trading dates) comes back with
prices up to the delisting. Until then single-name backtests stay refused, as the charter says. With it accepted, the
refusal lifts for universes built from `members_asof`, and every such result carries the coverage it actually got.

**Revised series are read as first published** (ALFRED) wherever a model consumes them; the recession probit is refit
expanding-window and its out-of-sample hit rate is the number shown.

**Operations.** Every job has a wall-clock limit (S 10 min, M 45 min, L its budget plus 10). The backup catches up after
a reboot. `ops.data_integrity` also flags missing sessions, flat or zero-volume bars, a vendor change inside a series,
and stale minute, option and FRED data.

**Burst.** `make research-burst` runs any heavy job kind on another machine against the latest export and uploads the
artifact through the existing publish path.

### Math

Correctness first, each with a fixture that fails today:

- **SVI.** Wing slopes `b(1+|ρ|) ≤ 2`. The vertex `m` inside the quoted log-moneyness range. `g(k) ≥ 0` enforced over
  the density's integration range. The density integrates only where `g ≥ 0` holds and reports the tail mass it left out.
  The widening loop stops at `|k| = 3`. The Rust kernel matches. Pass: on the committed chains, every slice's density
  integral is in [0.99, 1.01] and its mean is within 0.5% of the forward.
- **American options.** A CRR tree with discrete dividends prices American calls and puts; chain IVs for American-style
  contracts invert it. Pass: put and call IV at one strike differ by under 0.5 vol points on the SPY fixture.
- **Vol league.** The target is open-to-close realised variance plus the squared overnight return. VRP uses the same.
- **Small ones.** Copula degrees of freedom by copula likelihood. EVT on GARCH-standardised residuals. Bootstrapped
  zeros, not par yields, for discounting. Time to expiry from the quote timestamp. Rust GARCH fit only where it beats
  the reference; benchmarks measured on the droplet.

Then depth. Each new model ships as a scored product, never as a formula on a page:

- **SSVI surface**, calendar- and butterfly-free by construction, scored against per-slice SVI on fit error and on
  arbitrage violations per night.
- **Heston by the COS method**, calibrated nightly to the same quotes, scored against SSVI out of sample (today's
  parameters pricing tomorrow's chain).
- **DCC-GARCH** and **nonlinear shrinkage** as two more entrants in the covariance league.
- **Kalman-filter betas** as an entrant against rolling OLS, scored on next-month beta forecast error.
- **Fama–MacBeth and GRS** in the factor workbench, with sector and beta neutralisation.
- **Almgren–Chriss** schedule and cost, shown beside the desk's realised shortfall with the paper venue's limits stated.

### Site

The ledger is built from the system: its date is the build's, its `LIMITS` and `ADVISORY` rows are computed from the
backup log, the desk's state and the deployed SHA. The desk's public page shows the pooled test: sessions done of 500,
the running PSR with its standard error, and the stated power. No position, size or threshold is added.

## Order

A showcase is judged on what it shows, so the order is: ship what is built (Stage 0); fix the numbers that are wrong on
the page (Stage 4's three corrections, and the generated ledger from Stage 5); make the desk safe and its record true
(Stage 1); make every number regenerable and widen the data (Stage 3); make the verdicts about what trades and draft
INC-02 (Stage 2); add depth (the rest of Stage 4); finish the site and run the done check (Stage 5). The kill switch is
reset only by the owner, after the safety core is live and INC-02 is approved.
