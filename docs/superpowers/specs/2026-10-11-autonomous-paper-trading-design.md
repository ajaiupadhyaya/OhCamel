# Autonomous paper trading — design

*Drafted 2026-10-11 for the owner's approval. Status: **DRAFT — nothing in it
is built.** It extends the desk design
(`2026-09-12-the-desk-design.md`), the Ship design (`2026-10-05-the-ship-design.md`)
and `docs/CHARTER.md`; where it changes any of them it says so in §3 and §7.
The implementation plan is `docs/superpowers/plans/2026-10-11-autonomous-paper-trading.md`.*

## 0. What the owner asked for

2026-10-11: *"enable autonomous trading. Not through github actions based off a
time in the day but actual algorithmic, autonomous trading ... it should
utilize all the built out strategies, analysis, etc. and use it towards
intelligently autonomous algorithmic quantitative trading. paper trading."*

Read as success criteria:

1. **The desk trades on its own, all session, every session,** on the Alpaca
   paper account, with no person and no cron-at-a-time-of-day in the loop. A
   running process decides, sizes, sends, watches fills, and reacts to risk
   during regular hours.
2. **Every strategy the repository has built is on the desk** — EXP-A01's two
   rules, the ten strategies of the Quant library as the farm runs them, the
   EXP-Q01 ranker — and every analytic the repository computes that can
   lawfully inform sizing does so (volatility forecasts, covariance estimators,
   the engine's VaR, drawdown and limits). What cannot lawfully inform sizing
   (a DESCRIPTIVE ONLY regime model) is shown beside the decision, not inside it.
3. **Genuinely intraday strategies exist,** pre-registered by the owner,
   validated by the battery on minute bars, and traded by the same desk.
4. **It stays honest.** Nothing passes the charter's gates today; the design
   does not pretend otherwise. The account is simulated money; the record it
   produces is scored with the charter's own statistics and labelled for what
   it is.

## 1. The situation, verified 2026-10-11

- **Execution exists and is complete.** `desk/` is a journaled, gated,
  paper-only order manager: rules → gate on a fork of the live graph → journal
  → one submit site → Alpaca paper → fills as facts → reconcile on restart. It
  trades only as a *reaction*: a manual ticket (a `Day` order, regular hours),
  or an accepted signal, sent as market-on-open between 19:00 ET and two
  minutes before the open (`desk/intake.ml`, `desk/rebalance.ml`,
  `desk/oms.ml`). Nothing in the engine decides to trade on a clock during the
  session. The book's universe is capped at 30 names by the free data stream
  (`lib/config.ml:180`), a rebalance gates every subset of at most 6 legs
  (`desk/rebalance.ml:114`), and each order's `source` column already
  attributes it to its strategy.
- **Strategies exist and do not reach the desk.** The research service emits
  EXP-A01's two daily signals (both verdicts **fail** → advisory under R6).
  The Quant library has ten strategies (`quant/src/ohcamel_quant/backtest/strategies.py`:
  tsmom, xsmom, dual_momentum, sma_trend, vol_managed, risk_parity, low_beta,
  st_reversal, pairs_distance, pairs_coint) that the nightly farm (P4) runs
  over 21 strategy × universe cells with walk-forward selection and farm-wide
  DSR; their verdicts live only in droplet artifacts, and no signal path
  exists. EXP-Q01 (monthly ETF ranker) is **FAIL**; EXP-Q02 (regime HMM) is
  **DESCRIPTIVE ONLY** and is not a strategy.
- **Analytics run live.** P1 Monte Carlo VaR every 15 minutes in session, P2's
  volatility league and P3's covariance league nightly/weekly, the engine's
  VaR/ES/attribution/limits on every tick.
- **Intraday data exists.** `bars_minute`: Alpaca IEX 1-minute bars for 50
  names, two years back plus a top-up every 30 minutes in session. The OCaml
  engine holds no intraday bars; it holds live trades for the book's names.
- **The charter says only `status: pass` may be sized.** Nothing passes.
  Enabling trading in `book.sexp` as it stands would trade nothing. The agent
  may not invent alpha; the owner owns hypotheses and kill decisions.

So the work is three things: a **governance decision** (what may trade on
simulated money and under what label), the **loop** (producers → the desk →
intraday execution and risk), and **intraday research** (pre-registered
hypotheses through the battery on minute bars).

## 2. Decisions

Each is the agent's recommended default under the owner's standing
"decide defaults" instruction. The owner overturns any by saying so; D1, D2
and D9 are the charter-level ones the owner must actively affirm.

| | Decision | Why |
|---|---|---|
| **D1** | **Paper incubation**: a third sizing tier, `(sizing incubation)`, beside `advisory` and `live`. An incubated strategy is sized on the paper venue whatever its validation status, under caps, labelled INCUBATION on every page, route, journal row and report. Its validation block is never rewritten. | The owner wants the strategies to trade; none passes; the charter must change explicitly, not be worked around. §3 has the amendment. |
| **D2** | **The live paper record is the test.** Each incubated strategy's realised record (fills, marks, costs) is scored with the charter's statistics after 60 sessions, with pre-stated stop rules that the desk executes and promotion that only the owner makes. | Incubation without scoring is just trading; scoring makes it an out-of-sample experiment the charter can recognise. |
| **D3** | **Producers in Python, judgement and execution in OCaml.** Signals and the risk overlay are written by jobs in `ohcamel-worker` into the signals volume; the desk judges, sizes, sends. The core never trusts a producer's timing. | The strategies, the warehouse and the analytics are Python; the invariants put every order through the engine. |
| **D4** | **The roster** (§5): EXP-A01 ×2, the farm's 21 cells with parameters frozen monthly from the nightly sweep's own selection, EXP-Q01's monthly long-short quintile portfolio. Equal capital split under `incubation_cap`. (0.025 each; the cap scales the roster proportionally.) | "All the built-out strategies." A monthly freeze keeps the farm from re-fitting the live book every night. |
| **D5** | **The risk overlay** is one number, `exposure_scale ∈ [0, 1]`, from volatility targeting (Moreira & Muir 2017) on the combined targets, using P2's current best volatility model and P3's current best covariance estimator, multiplied in the desk by a drawdown governor read from the graph. Regime probabilities (Q02) and P1's Monte Carlo VaR are shown beside it and never inside it. | Vol targeting is a literature rule with a mechanism; Q02 earned DESCRIPTIVE ONLY and may not size; P1 computes reference books, not this one. |
| **D6** | **Intraday execution**: a second rebalance mode sends `Day` limit orders during regular hours (marketable, inside the collar), in waves (shrinking legs first), each wave gated against the real book; the overlay's moves re-size held strategies intraday; the kill switch keeps halting and never liquidating. | Trading through the day without a second order path: the rules, gate, journal and wire are unchanged. |
| **D7** | **The universe cap rises to 64**, the stream keeps 30 names, the rest are marked by Alpaca's multi-symbol latest-trade REST call every 10 s. The book declares the 42 tradable site ETFs. | The roster needs 42 names; one free stream carries 30. |
| **D8** | **Contract v2** for intraday signals: `as_of` is the instant of a bar's close, ages count in minutes against the venue's clock, and the desk corroborates the bar with its own ticks. v1 (daily) is unchanged. | A rule change bumps the version (interface README); daily signals must not change meaning. |
| **D9** | **Three intraday hypotheses are drafted for the owner** (§6) on five liquid ETFs, backfilled to 2016 on minute bars, through the existing battery extended to session-level series. The charter's gates apply unchanged, regimes included. | The agent drafts from the literature; the owner pre-registers; no gate is loosened. |
| **D10** | **Canary first.** The first three live sessions run with `incubation_cap 0.10`; the owner raises it after reading the record. | A new order path meets real fills slowly. |

## 3. The charter amendment

`docs/CHARTER.md`, *The gates*, last paragraph, currently:

> A signal reaches the core with a `validation` block written by the code that
> ran the battery. Only `status: pass` may trade; anything else is advisory,
> logged, and never sized.

becomes:

> A signal reaches the core with a `validation` block written by the code that
> ran the battery. Only `status: pass` may be sized as `live`; anything else is
> advisory, logged, and never sized as `live`. **On the paper venue, and only
> there, the owner may register a strategy as `incubation`: it is sized under
> the book's incubation caps whatever its status, its validation block is
> carried unchanged, every order, page and report it touches says
> INCUBATION, its realised record is scored with this charter's statistics
> and stop rules the owner wrote before it traded, and promotion to `live`
> never follows from that record by itself.** Incubation exists so that a
> strategy's first out-of-sample test on simulated money is recorded; it is
> not evidence of edge and is never reported as such.

And under *Reporting*: "Distinguish *advisory*, *incubation* and *live* every
time." The amendment is approved by the owner approving this document; the
plan's first task writes it.

## 4. Architecture

### 4.1 The loop

```
 nightly / weekly                     every session                         every 15 min in session
 ─────────────────                    ─────────────                         ───────────────────────
 farm.sweep (P4)  ─┐                  signals.daily ──► signals/*.json ─┐   bars_minute top-up
 vol.forecast_league (P2) ─┤                                            │   signals.intraday ──► signals/*.json ─┐
 cov.league (P3) ─┤   artifacts       research service (EXP-A01) ──────┤   signals.overlay  ──► signals/overlay-*.json
 models.xs_lgbm (P5) ─┘   (read)      signals.q01 (monthly) ───────────┘                                          │
                                                                        │                                          │
                                   ┌────────────────────────────────────▼──────────────────────────────────────────▼───┐
                                   │  ohcamel-live (OCaml)                                                              │
                                   │  intake (1 min): judge R1–R7 (v1) / R1–R7+R9 (v2) / O1–O4 (overlay)               │
                                   │  size: target = trunc(w · fraction · scale · E / p); diff vs strategy position     │
                                   │  rebalance: Opg at the open (daily cadence) · Day limit waves in session (intraday)│
                                   │  rules → gate (fork) → journal → wire → Alpaca paper → fills → graph → limits      │
                                   │  kill switch · drawdown governor · stop rules · end-of-day flatten · reconcile     │
                                   └─────────────────────────────────────────────────────────────────────────────────────┘
                                                                        │
                                   journal (SQLite) ─► /api/desk, /api/research, /research, /execution ─► record scoring
```

### 4.2 Producers (Python, `quant/src/ohcamel_quant/signals/`)

New job kinds, registered in `jobs/kinds.py`, scheduled in `schedules.yaml`
(New York time), run by `ohcamel-worker`, which gains the `signals` volume
read-write (`deploy/docker-compose.yml`). Every document is written to a temp
file and renamed, as the research service does; the desk's intake refuses
anything else.

- **`signals.daily`** — weekdays 18:45 (after the 18:30 daily-bars ingest).
  For each roster cell `farm_<strategy>_<universe>`: load the cell's frozen
  parameters (§4.2.1), compute the strategy's target weights at yesterday's
  close with the *same* function the backtest engine calls
  (`backtest/strategies.py`, point-in-time, `assert_point_in_time` on the
  frame), write one contract-v1 signal with `as_of` = that session's date,
  `validation` copied from the cell's newest farm verdict (`PASS`→`pass`,
  `FAIL`→`fail`, anything else→`unvalidated`), `sequence` = the session
  ordinal. A cell whose universe has a name outside the book is **refused by
  the producer** with a reason in the artifact, never silently projected.
- **`signals.q01`** — the 2nd of the month 07:00, from the newest
  `models.xs_lgbm` artifact's `scores` table: long the top quintile, short the
  bottom quintile, equal weight, Σ|w| = 1, `validation` from the artifact's
  verdict (FAIL today). Stage 4.
- **`signals.intraday`** — every 15 minutes 10:00–15:45: top up the minute
  bars for the strategy's names (the existing ingest, same writer), run each
  pre-registered intraday strategy's rule at the last complete bar, write a
  contract-v2 signal with `as_of` = that bar's close instant, `bar` = its
  length, `horizon: close` and `max_age_minutes`. At 15:45 it writes zero
  targets for every `horizon: close` strategy. Stage 2, after the owner
  approves §6. An intraday rule is evaluated only at the producer's marks
  (:00, :15, :30, :45) on the bar closes available then, never tick by tick,
  and each pre-registration says so.
- **`signals.overlay`** — 09:40, then every 15 minutes to 15:45, and 18:50.
  Reads the roster's newest targets, the warehouse's daily returns, P2's
  newest artifact (the best model per name by QLIKE; EWMA(0.94) when absent)
  and P3's newest (the best estimator by realised GMV vol; Ledoit–Wolf when
  absent); forecasts the combined targets' annualised volatility
  σ̂ = √(wᵀΣw); writes `exposure_scale = min(1, σ_target / σ̂)` with every
  input, the models' names and artifact ids, and `as_of` = the newest bar it
  read. `σ_target` comes from the book. Reported beside it in the same
  document, never multiplied in: Q02's newest regime probabilities and P1's
  newest reference-book VaR.
- **`desk.report`** — 16:30: the session's fills, P&L per strategy, rules and
  gates refused, overlay path, as an artifact the Research page links.

**4.2.1 The parameter freeze.** On the first session of each month,
`signals.daily` reads the newest `farm.sweep` artifact's `cells` table and
records each cell's `selected_params` in its own artifact; those parameters
hold for the month. A cell the farm reports SKIPPED or INSUFFICIENT DATA keeps
last month's parameters; a cell with none is not emitted and the reason is
in the artifact. The rule is written in INC-01 (§5) before the first run.

### 4.3 The desk (OCaml)

**Sizing tier.** `lib/config.ml` `Signals_spec`: `sizing advisory | live |
incubation`; a new desk-level `(incubation_cap F)` that scales the roster: each
incubation strategy's effective fraction is `capital_fraction × min(1,
incubation_cap / Σ incubation capital_fraction)` (`Signals_spec.effective_fraction`),
so the canary and every later cap change is one book edit; the validator
requires Σ incubation fractions ≤ 1 and live + incubation ≤ 1. `desk/intake.ml` `decide`: a document that passes R1–R5, R7 (and
R9 for v2) whose strategy is `incubation` becomes `Accepted_incubation`
whatever R6 says; R6's own result is still recorded on the judgement row.
`Accepted_incubation` sizes exactly as `Accepted` does; the order's `source`
stays `signal:<slug>:<sequence>` (so a strategy's position is one range of
the journal, as today) and the tier is recorded on the judgement and on a
`sizings` row. The venue kind is checked at the
same site: a trading half whose host is not the compiled-in paper constant
refuses incubation sizing (belt and braces over invariant 9).

**The overlay.** A new document kind, `interface/overlay.schema.json`, rules
O1–O4 in `desk/contract.ml`: version 1; `as_of` not after the venue clock's
`now` and within `max_age_minutes` of it; `sequence` increasing;
`exposure_scale` in [0, 1]. Accepted overlays are journaled (`overlays`
table). The effective scale is `overlay × governor`, where the **drawdown
governor** is computed in the desk from the graph's `drawdown` node and the
book's `dd-cap`: 1 up to half the cap, falling linearly to 0 at the cap (the
kill switch, if armed on `dd-cap`, halts there anyway). A stale overlay holds
its last value for `max_age_minutes`, then `overlay_fallback` from the book
applies and the page says OVERLAY STALE. Sizing multiplies every incubation
and live target by the effective scale. When the effective scale moves more
than `overlay_band` from the scale a strategy was last sized at, every
strategy with `(adjust intraday)` is re-sized from its last accepted document
and the difference is proposed through the intraday mode.

**Intraday rebalance mode.** `desk/rebalance.ml` gains `mode = Open | Intraday`.
`Intraday` legs are `Kind.Limit` at the mark moved `intraday_slip_bps` toward
the far side (default 10 bps, far inside the 5 % collar), `Tif.Day`; the
`session` rule already requires the regular session for `Day` orders. Legs
below `min_leg_notional` (book; default $200) are dropped and the drop is
journaled. A rebalance with more than 6 legs runs in **waves**: wave 1 is
every shrinking leg, gated as a set; the desk waits until each is filled or
cancelled (bounded by `wave_timeout_s`, default 180, then cancel); wave 2 and
on are growing legs, at most 6 per wave by notional descending, each wave
gated against the real book as it is then. An unfilled intraday leg at its
timeout is cancelled and the strategy is re-sized on the next pass; a newer
document for the same strategy supersedes by R5.

**Contract v2** (`interface/README.md` §v2, `desk/contract.ml`): R1 is
`schema_version = 2`; `as_of` is an RFC 3339 instant and `bar` a length
(`1Min`, `5Min`, `15Min`, `30Min`); R3 is `as_of ≤ now` by the desk's cached
venue clock; R4 is `now − as_of ≤ max_age_minutes`; R5–R7 unchanged; **R9**:
for every target symbol the desk's own last tick time is at or after
`as_of − bar` (the bar the producer claims to have read is corroborated by
data the desk received itself). The daily-session clock plays no part. A v2
document with `horizon: close` names a strategy whose exit is a
market-on-close order (`Tif.Cls`, Alpaca `cls`; the sim venue fills it at the
session's last mark), sent when the producer's 15:45 zero-target document
arrives; the desk **flattens at `flatten_at`** (book; default 15:50) by a
zero-target intraday rebalance only as the backstop, when no `cls` order of
that strategy rests, journaled `end-of-day flatten`.

**Stop rules.** `(stop ((drawdown F) (sessions N)))` per strategy. The desk
keeps a per-strategy realised record (§4.5); when its drawdown from peak on
allocated capital reaches `F`, or `N` sessions have elapsed since its first
fill, the strategy is **stopped**: flattened by a zero-target rebalance,
written to a `strategy_state` table with the rule that fired, shown STOPPED,
and sized no more until the owner edits the book (a `(reinstated DATE)` field
later than the stop) and the engine restarts. The desk never un-stops.

**Universe.** `Config.universe_cap` becomes 64. The stream subscribes to the
book's `(stream (…))` list first, then the remaining positions in book order,
up to `stream_cap` = 30; `lib/feed/alpaca_rest.ml`
gains `latest_trades` (`GET /v2/stocks/trades/latest?symbols=…`, batches of
≤ 50) polled every 10 s for every name not streamed, applied to the same
price and tick cells. Staleness (90 s) is unchanged. Both feeds are in `lib/`
and talk only to `data.alpaca.markets`.

**Rules.** `duplicate` is scoped by `source` so two strategies buying the
same quantity of the same name are not a double-click; `max_open_orders`'s
default rises to 200 and the example book says why. Per-strategy orders are
not netted (§10).

### 4.4 The book

`book.example.sexp` gains, with the engine's defaults written out and
`check-book` validating all of it:

```
(desk ((trading enabled) (max_order_notional 25000.0) … (max_open_orders 200)
       (incubation_cap 0.60) (vol_target 0.10) (overlay_max_age_minutes 60)
       (overlay_fallback 0.5) (overlay_band 0.10) (intraday_slip_bps 10.0)
       (min_leg_notional 200.0) (wave_timeout_s 180) (flatten_at "15:50")
       (stream (SPY QQQ IWM TLT GLD))))
(signals ((strategies
  (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing incubation)
    (capital_fraction 0.025) (cadence daily) (adjust intraday)
    (stop ((drawdown 0.15) (sessions 250))))
   ((name farm_tsmom_rates) (symbols (BIL SHY IEI IEF TLT TIP)) …)
   ((name exp_i01_spy) (symbols (SPY)) (max_age_minutes 20) (sizing incubation)
    (cadence intraday) (horizon close) …)
   …))))
```

The droplet's `book.sexp` is the owner's; the plan ships the full example and
the runbook, and the owner copies it (or tells the agent to, in that session).

### 4.5 Self-validation of the live record

Revived from the finish plan's Stage 4 validation lane (Tasks 55–61), scoped:

- **Per strategy**, from the journal: allocated capital at each session
  (fraction × scale × equity), the strategy's position (net of its fills),
  marks at each close, so a daily return on allocated capital; costs from
  `desk/tca.ml` per fill. After 60 sessions: annualised Sharpe, PSR (the
  research battery's own formula, ported with a hand-derived test), max
  drawdown, hit rate, turnover, cost paid in bps; before 60: "session n of
  60". Always: "INCUBATION — not evidence of edge" and the manifest's verdict
  beside it.
- **The shadow record** (Task 61) for advisory strategies: targets applied to
  the next session's marks, so advisory and incubated strategies can be
  compared on the same page.
- **The book**: the VaR forecasts the session close already journals, scored
  against the realised session loss (equity fall net of cash flows, the
  finish spec's derivation) with the existing Kupiec, Christoffersen,
  conditional-coverage, duration and traffic-light functions called directly
  (finish-plan ruling 21). "Session n of 250"; no verdict before 60.
- **`ohcamel replay DATE`** prints a session's signals, overlays, orders and
  fills from the journal.

### 4.6 Pages and routes

Research (`/research`, `/api/research`): each strategy's tier (ADVISORY ·
INCUBATION · LIVE · STOPPED), its manifest verdict, its latest document and
judgement, its frozen parameters, its live record (§4.5), the overlay's
current value with its inputs and the two reported-only analytics. Execution:
the waves, the flattens, the governor. Desk: the effective scale beside
equity. `/api/desk` carries `exposure_scale`, `governor`, `overlay` (value,
`as_of`, age, state) and per-strategy `tier`. Every INCUBATION label is a
literal string a test pins in the built page.

### 4.7 Operations

- The trade-updates watchdog (finish-plan Task 42): a ping every 60 s in
  session, reconnect and reconcile on a missing pong.
- Producer liveness: each kind's `max_age` in `schedules.yaml`; the Research
  page hatches a producer whose newest artifact is past it; `deploy/watch.sh`
  checks the overlay's age in session.
- Alerts: the existing block; the owner may add the Slack sink. New alert
  kinds: `incubation stopped`, `overlay stale`, `wave timeout`.
- Deploys outside 09:25–16:10 New York only (the existing rule); the first
  three sessions at the canary cap (D10).

## 5. INC-01 — the incubation pre-registration

*Written: [`research/experiments/INC-01/preregistration.md`](../../../research/experiments/INC-01/preregistration.md), with its roster [`roster.yaml`](../../../research/experiments/INC-01/roster.yaml).*

`research/experiments/INC-01/preregistration.md`, approved by the owner
before the first incubated order, states in full:

- **What incubation is and is not** (§3's words).
- **The roster**: `exp_a01_spy`, `exp_a01_tlt`; the 21 farm cells by slug,
  each with its universe and its library function; `q01_ls_quintile`; the
  intraday strategies as each is approved.
- **Capital**: `incubation_cap` 0.60 of equity (0.10 for the canary sessions);
  equal fractions across the roster, recomputed when the roster changes;
  `vol_target` 0.10 annualised.
- **The parameter-freeze rule** (§4.2.1), the overlay rule (§4.2), the
  governor (§4.3), the slip and leg floors, the wave rule.
- **Stop rules**: drawdown 0.15 of allocated capital; 250 sessions.
- **What is reported and when** (§4.5), and the sentence printed on every
  report: *incubation records are not evidence of edge; promotion to live
  sizing is the owner's decision after a passing battery, never from this
  record alone.*
- **Kill criteria for the program**: three stops in one month, or a wave
  timeout on three consecutive sessions, or any order the journal cannot
  reconcile — the desk is halted and the owner decides.

## 6. Intraday hypotheses — drafts for the owner

*Written: [EXP-I01](../../../research/experiments/EXP-I01/preregistration.md), [EXP-I02](../../../research/experiments/EXP-I02/preregistration.md), [EXP-I03](../../../research/experiments/EXP-I03/preregistration.md), each with its `config.yaml`.*

Three pre-registrations in `research/experiments/EXP-I0x/`, drafted from the
literature in EXP-A01's form (paper, hypothesis, rationale, data, assumptions,
grid fixed in advance, how it fails, kill criteria), each **approved, edited
or rejected by the owner** before its battery runs. Universe: SPY, QQQ, IWM,
TLT, GLD, whose minute bars are backfilled from 2016 (Alpaca IEX, one-time
`ingest.bars_minute` with `start`, five names only, ≈ 4 M rows). Costs: the
shared friction file, per fill. The battery's unit is the **session**: one
observation per session, so the charter's gates — holdout, DSR over every
configuration × fold, PSR, the bootstrap, the five named regimes, the cost
sweep — apply unchanged.

- **EXP-I01 — Market intraday momentum** (Gao, Han, Li & Zhou 2018, *JFE*):
  the first half-hour return predicts the last half-hour return. Position in
  the sign of the 09:30–10:00 return, entered at 15:30, flat at the close.
  Grid: unconditional; conditioned on the 15:00–15:30 return agreeing (two
  configurations). Rationale: late-informed trading and end-of-day hedging
  and rebalancing flows. Fails: post-publication decay; two fills a day
  against a small mean effect.
- **EXP-I02 — Opening-range breakout** (Zarattini, Aziz & Barbon 2023 on
  QQQ): long above the opening range's high, short below its low, stop at the
  range's far side, flat at the close, volatility-sized. Grid: range length
  {5, 15, 30} minutes. Rationale: the open resolves overnight information
  with a defined risk. Fails: range days, turnover.
- **EXP-I03 — Intraday pairs** (the library's `pairs_distance` on 30-minute
  bars; Gatev, Goetzmann & Rouwenhorst 2006 at the daily horizon): sector
  pairs formed over 20 sessions, entered at 2σ of the normalised spread,
  closed at 0 or at the session's end. Grid: entry {2.0, 2.5}σ. Rationale:
  liquidity provision to transient imbalances. Fails: non-stationary pairs,
  costs on many round trips.

A verdict of pass makes the strategy eligible for `live` at the owner's word;
any other verdict makes it eligible for `incubation` at the owner's word.

## 7. Invariants

The desk design's thirteen stay in force. Three are added:

14. **Incubation is paper-only twice over.** The sizing site refuses
    incubation unless the trading host is the paper constant, and invariant
    9 already makes it the only host there is.
15. **A validation block is never rewritten.** The desk copies a document's
    `validation` into the journal byte for byte, and the page prints the
    manifest's verdict beside every incubation figure.
16. **The desk never un-stops.** A stop rule's effect is reversed only by a
    book edit and a restart.

## 8. Stages

| Stage | Delivers | Owner gate before it |
|---|---|---|
| 0 Governance | The charter amendment, INC-01, the three EXP-I0x drafts, the book schema and `check-book`, contract v2 and the overlay schema in `interface/` | approves this document |
| 1 The desk trades | Universe 64 + REST marks; incubation tier; overlay + governor; intraday mode + waves; `signals.daily`, `signals.overlay`, `desk.report`; pages; deploy; three canary sessions | approves INC-01 |
| 2 Intraday | Minute backfill to 2016 (five names); the battery's session-level mode; EXP-I01–I03 run; contract v2 + R9 in the desk; short legs (Task 33, moved here from Stage 4); `signals.intraday`; end-of-day flatten; the roster grows per verdict | approves each EXP-I0x |
| 3 The record | Per-strategy live record, PSR after 60, shadow record, book VaR coverage, stop rules, `replay`, the Research page's record | — |
| 4 Q01 and shorts | `signals.q01`; the page | — |
| 5 Unattended | Watchdog, producer liveness in `watch.sh`, alert kinds, runbook, a ten-session soak, `docs/status.md` | raises the cap after the canary |

## 9. Testing

Hermetic, hand-derived, as the repository does it: the sim venue and a
virtual clock for every desk path (incubation accepted under R6 fail; refused
on a non-paper host; overlay O1–O4; governor at 0, half and full cap; a
7-leg rebalance in two waves with a timeout; v2 R3/R4/R9; the 15:50 flatten;
a stop firing once and never resetting); the universe at 31 names with the
31st marked by REST; `check-book` on every new field. Python: producers on
the fixture warehouse offline, the freeze rule on a two-month fixture farm
artifact, the overlay against a hand-computed σ̂, the intraday battery on a
fixture of minute bars with a hand-computed session series. Deploy:
`deploy/test` cases for the worker's signals mount and the new kinds; the
smoke suite reads `exposure_scale` on the live host. Counts pinned in
`lib/verified.ml` and `scripts/check-counts.sh` as today.

## 10. Limits, stated

- Simulated money, one venue, IEX marks, no consolidated tape, no locate on
  shorts, no options. Fills are Alpaca's paper simulator's.
- Per-strategy orders are not netted across strategies; opposite legs pay
  both spreads. Netting with pro-rata attribution is a later improvement.
- The regime model and the Monte Carlo atlas are reported beside sizing, not
  used in it, by their verdicts.
- The farm's verdicts are the droplet's artifacts, not committed evidence;
  the freeze records what was read.
- An incubation record is not evidence of edge, and the pages say so.

## 11. Owner steps

1. Approve this document (D1 and D2 are the charter-level affirmations).
2. Approve INC-01 as written or edited.
3. Approve, edit or reject EXP-I01, I02, I03 and the five-name backfill.
4. Copy the shipped `book.example.sexp` over the droplet's `book.sexp` with
   `(trading enabled)`, alerts enabled, the kill switch armed on `book-cap
   var-cap dd-cap`, `incubation_cap 0.10` — or tell the agent to, in that
   session — and restart the live container outside the session.
5. After three sessions, raise `incubation_cap` to 0.60.
6. Promotion of anything to `(sizing live)`: the owner, ever.

## 12. Out of scope, with reasons

Live-money trading (invariant 9). Single-name intraday strategies (the
charter's survivorship stance; the five ETFs suffice). An LLM in the loop
(not a pre-registrable hypothesis under the charter; the owner may propose
one as an experiment). Options trading. Cross-strategy netting (§10). The
public site showing the paper book (owner step O-7, unchanged).
