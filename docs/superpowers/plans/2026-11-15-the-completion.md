# The Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every defect the 2026-11-15 review found so the desk can be left alone, the record is true, every verdict is about what trades, every number regenerates, and every formula fits its instrument.

**Architecture:** Six stages, numbered by subsystem and executed in the order of the table at the end (the project is a showcase of methods, so visibly wrong numbers are fixed first). Stage 0 ships `honest-book`. Stage 1 changes the OCaml desk (`desk/`, `lib/`), safety core first. Stage 2 changes the research gates and replaces INC-01 with INC-02. Stage 3 changes the warehouse and the job manifests. Stage 4 fixes and then deepens the Python analytics and their Rust kernels. Stage 5 makes the site generated from the system and runs the definition of done.

**Tech Stack:** OCaml 5 (Core, Async, Alcotest; `make test`), Python 3.12 (pandas, NumPy, SciPy, DuckDB, FastAPI, LightGBM; `make quant-test`), the research package (`make research-test`), Rust via PyO3 (`native/kernels`, `cargo test`), TypeScript (Vitest, `quant/web`), bash (`deploy/`, `deploy/test/*.sh`).

**Spec:** `docs/superpowers/specs/2026-11-15-the-completion.md`

## Global Constraints

- The charter (`docs/CHARTER.md`) governs. No gate is loosened. No signal is invented by the agent; INC-02's hypothesis is the owner's.
- The agent never POSTs `/api/desk/kill/reset`, never edits `book.sexp` on the droplet, never deploys. The owner runs `deploy/deploy.sh`.
- No restart of `ohcamel-live` inside 09:25–17:10 America/New_York.
- A task that depends on a ruling (R1–R6 in the spec) carries `Approved-by: owner, <date>` in its commit before that code reaches the live host. Until then it merges behind its default-off config word where one is named.
- Public pages show no position, size, limit threshold or observed limit value.
- No synthetic market data where a number is reported. New fixtures are committed slices of real data with a provenance sidecar.
- Every new statistic on a page carries its source and as-of, as existing panels do.
- Paper only. Nothing here points the desk at real money.
- Each task ends with its suite green: `make test` (OCaml), `make quant-test`, `make research-test`, `cargo test` in `native/kernels`, `scripts/check-counts.sh --verify`.
- Commit style follows the log: `desk: …`, `research: …`, `fix: …`, `docs: …`, one sentence saying what is now true.

## Review Focus

- A strategy long and another short the same ETF, with the account netted between them: both must still be sizable and stoppable. Test in Task 1.6.
- A restart while tripped, inside the hysteresis band: the desk must come back tripped. Test in Task 1.1.
- A reduce-only order that would flip a position through zero must be refused whole, not clipped. Test in Task 1.2.
- An ex-dividend date for a monthly payer (BIL, TLT, LQD, HYG): the strategy's session P&L must not show the dividend as a loss. Test in Task 1.8.
- An SVI slice whose quotes end at k = 0.2 with a steep right wing: the density must integrate to 1 within 0.01 or report the tail mass, never clip silently. Test in Task 4.1.

---

## Stage 0 — Ship what is built

### Task 0.1: Merge honest-book

**Files:** none new. Branch `honest-book` (seven fix commits over `main`).

- [ ] Run `make test && make quant-test && make research-test` on `honest-book`. Expected: all pass, `scripts/check-counts.sh --verify` clean.
- [ ] Run every script in `deploy/test/`. Expected: all pass.
- [ ] Tick the eight tasks in `docs/superpowers/plans/2026-11-05-honest-book.md` that the commits complete; leave the host steps unticked.
- [ ] Merge to `main` with `--no-ff`; push; confirm CI and the image build are green.

### Task 0.2: Owner deploys; the switch stays tripped

**Files:** Modify `docs/status.md` (deployed rows), `.superpowers/sdd/2026-11-01-consolidated/progress.md` (append).

- [ ] Hand the owner: `deploy/deploy.sh --sha <merge sha> --live`, outside 09:25–17:10 ET, and the regenerated-book step from the honest-book plan.
- [ ] After `deploys.log` shows ok, verify read-only over `ssh ohcamel`: both hosts on the SHA, `/api/desk` `switch.limit` still `dd-cap` (a limit trip does not survive a restart today, so if it reads `clear` say so at once and ask the owner to halt by hand), 31 positions graph==venue, public ledger dated 2026-11-05.
- [ ] Record the result in the ledger. **Do not reset the switch.** It is reset in Task 2.2, after Stage 1's safety core is live.

---

## Stage 1 — The desk can be left alone

Tasks 1.1–1.5 are the safety core and deploy together. 1.6–1.12 follow in order.

### Task 1.1: A limit's trip survives a restart

**Files:**
- Modify: `desk/halt.ml`, `desk/halt.mli`, `desk/journal.ml`, `desk/journal.mli`, `desk/oms.ml` (beside the hand-halt persistence near 2096–2120)
- Test: `test/test_halt.ml`, `test/test_journal.ml`

**Interfaces — Produces:**
- `Journal.record_trip : t -> limit:string -> at:Time_ns.t -> unit Or_error.t`
- `Journal.standing_trip : t -> (string * Time_ns.t) option` (the last trip with no later reset)
- `Journal.record_reset : t -> at:Time_ns.t -> unit Or_error.t`
- `Halt.restore_trip : t -> limit:string -> at:Time_ns.t -> unit` (sets `Tripped` even when the source reads clear; only `reset` lifts it)

- [ ] Write failing tests: `restored_trip_outranks_clear_source` (create with `Source.none`, `restore_trip`, assert `State.name = "tripped"` and `reason` is `Some`); `reset_lifts_restored_trip`; `journal_trip_then_reset_is_none`; `journal_trip_without_reset_is_some` with the limit name and time round-tripped.
- [ ] Run `dune test test/test_halt.exe test/test_journal.exe`. Expected: FAIL, unbound values.
- [ ] Implement the four functions; in `oms.ml` journal a trip on the `Clear → Tripped` edge and call `restore_trip` at startup from `standing_trip`; the kill-reset route calls `record_reset`.
- [ ] Run `make test`. Expected: PASS.
- [ ] Commit: `desk: a limit's trip survives a restart -- journaled when it happens, restored at startup, lifted only by reset`.

### Task 1.2: Risk-reducing orders pass a limit's trip (R2)

**Files:**
- Modify: `desk/rules.ml` (the `kill_switch` rule near 316), `desk/oms.ml` (the refusal near 967), `lib/config.ml` (desk spec word)
- Test: `test/test_rules.ml`, `test/test_oms.ml`, `test/test_example_book.ml`

**Interfaces — Produces:**
- Desk spec word `(reduce_through_trip true|false)`, default `false`.
- `Rules.reduces : own:float -> account:float -> side:Order.Side.t -> qty:int -> bool` — true when `own` and `account` share a sign opposite to the order's direction and `qty ≤ min |own| |account|`.
- The rule's context gains `halt_state : Halt.State.t` in place of `halted : string option`.

- [ ] Write failing tests in `test_rules.ml`: with `Tripped` and the word on, sell 50 against own +100 / account +80 passes `kill_switch`; sell 90 (more than the account's 80) is refused; sell 120 against own +100 (through zero) is refused; any buy is refused; with `Halted` the sell 50 is refused; with the word off the sell 50 is refused. In `test_oms.ml`: a stop flatten and a governor cut each reach the sim venue while tripped.
- [ ] Run. Expected: FAIL.
- [ ] Implement. Set the word `true` in `book.example.sexp` and state in its comment that it departs from INC-01's "halts and never liquidates" under INC-02.
- [ ] Run `make test`. Expected: PASS.
- [ ] Commit: `desk: an order that only shrinks a position passes a limit's trip -- the governor's cuts and a stop's flatten were refused by the switch they answer to`.

### Task 1.3: A trip-limit raises on a held breach, not on one print

**Files:**
- Modify: `lib/alerts.ml` (`Tracker.step`, 164–190), `lib/config.ml` (`Alerts.t`), `lib/feed/alpaca_ws.ml` (trade application near 334–338)
- Test: `test/test_alerts.ml`, `test/test_feed.ml`

**Interfaces — Produces:**
- `Config.Alerts.raise_after_n : int` (default 3) and `raise_after_s : float` (default 30.0).
- `Config.Feed.max_print_deviation : float` (default 0.05).
- A feed counter `dropped_prints` exposed on the existing vitals record.

- [ ] Write failing tests: one breached evaluation raises nothing; three breached evaluations within 10 s raise nothing; three spanning 31 s raise once; a clear evaluation between them restarts the count; a print 6% from the last quote midpoint leaves the mark unchanged and increments `dropped_prints`; a print 4% away is applied; with no quote yet the print is applied.
- [ ] Run. Expected: FAIL.
- [ ] Implement: per-limit `(first_breach_at, consecutive)` in the tracker; the deviation filter before the mark update.
- [ ] Run `make test`. Expected: PASS.
- [ ] Commit: `engine: a limit raises after three breached evaluations over thirty seconds, and a print five percent off its quote is not a mark`.

### Task 1.4: Drawdown is from the peak of the last 250 closes

**Files:**
- Modify: `lib/risk_metrics.ml` (499–503), `lib/graph.ml` (the drawdown node), `lib/config.ml`
- Test: `test/test_risk_metrics.ml`, `test/test_overlay.ml`

**Interfaces — Produces:** `Config.Risk.drawdown_window : int` (default 250); `Risk_metrics.drawdown : window:int -> float array -> float`.

- [ ] Write failing tests: a path that peaks, falls 8% and holds flat for 251 closes reads 0.08 at close 250 and 0.0 at close 252; `Overlay.governor` with `dd_cap 0.10` returns to 1.0 for that path; a window longer than the history uses the whole history.
- [ ] Run; implement; run `make test`. Expected: PASS.
- [ ] Commit: `engine: drawdown is measured from the peak of the last 250 closes -- from the all-time peak a cut book could never earn its size back`.

### Task 1.5: Unknown is not permission

**Files:**
- Modify: `lib/gate.ml` (22–25, 128), `desk/intake.ml` (overlay staleness near 1632–1638 and 1859–1869; governor read near 639 and 704–710), `desk/overlay.ml`
- Test: `test/test_gate.ml`, `test/test_intake.ml`, `test/test_overlay.ml`

**Interfaces — Produces:**
- `Gate.Outcome` gains `Refused_unevaluable of string list` when a limit named in `kill_switch_trips_on` cannot be evaluated and the proposal raises gross notional.
- `Overlay.effective` takes `~age:Time_ns.Span.t option`; older than `overlay_max_age` (book word, default 4 h, counted in session hours) it returns `fallback × governor`.
- An unreadable drawdown yields governor `overlay_fallback`.

- [ ] Write failing tests: unevaluable `var-cap` with a gross-raising proposal is `Refused_unevaluable ["var-cap"]`; the same with a gross-lowering proposal passes; a 5-hour-old overlay of 0.9 with fallback 0.5 sizes at 0.5; a 3-hour-old one sizes at 0.9; after a restart with no drawdown reading the governor is the fallback.
- [ ] Run; implement; run `make test`. Expected: PASS.
- [ ] Commit: `desk: a limit that cannot be evaluated refuses more risk, and a stale overlay or an unread drawdown sizes at the fallback`.

### Task 1.6: One sub-ledger check for the desk

**Files:**
- Modify: `desk/rebalance.ml` (remove `account_refusal`, 280–292), `desk/oms.ml` (1425–1431), `desk/reconcile.ml`
- Create: `desk/subledger.ml`, `desk/subledger.mli`
- Test: `test/test_rebalance.ml`, create `test/test_subledger.ml`

**Interfaces — Produces:**
- `Subledger.break : { symbol : Symbol.t; strategies : float; account : float }`
- `Subledger.check : own:(string * Symbol.t * float) list -> account:(Symbol.t * float) list -> tolerance:float -> break list`
- `Subledger.refuses : break list -> symbol:Symbol.t -> increases:bool -> string option`

- [ ] Write failing tests: A +100 SPY, B −50 SPY, account +50 gives no break and both strategies size; account +40 gives one break `{SPY; 50; 40}`; with that break an order increasing SPY exposure is refused naming both figures, an order in QQQ is not, and a reducing SPY order is not.
- [ ] Run; implement; wire `check` into the sync pass and the nightly close, raising one alert per break; run `make test`. Expected: PASS.
- [ ] Commit: `desk: the strategies' positions are checked against the account as a sum, per name -- two strategies on opposite sides refused each other forever`.

### Task 1.7: Orders are priced off the quote and sent after the open

**Files:**
- Modify: `desk/oms.ml` (1175–1187 sizing, 1453–1472 OPG send, 1610–1613 quote fetch), `desk/intake.ml` (completions 825–837, 1785–1824), `desk/rebalance.ml` (106–108), `desk/journal.ml` (1599–1603), `desk/rules.ml` (345–355)
- Test: `test/test_oms.ml`, `test/test_intake.ml`, `test/test_sim_venue.ml`, `test/test_journal.ml`

**Interfaces — Produces:**
- `Journal.completions_today : t -> date:Date.t -> key:string -> int`
- Rule `quote`: an order with no quote for its name within 5 s of sending is refused.
- Netting across strategies is cut (spec, *Cut*); each strategy still sends its own orders.

- [ ] Write failing tests: no `Opg` order is ever built; an order's limit is ask + 10 bps for a buy and bid − 10 bps for a sell from the quote fetched at send; with no quote within 5 s the order is refused `quote`; share count uses the current mark, not yesterday's close; nothing is sent before 09:35 New York; a restart after two completions allows one more, not three; a `failed` order is eligible for completion.
- [ ] Run; implement; run `make test`. Expected: PASS.
- [ ] Commit: `desk: every order is a day limit off the quote, sent from 09:35 -- 55 of 59 auction orders expired and the rest were priced off a print up to thirty minutes old`.

### Task 1.8: The record carries dividends and costs and marks to the official close

**Files:**
- Modify: `desk/record.ml` (38–96; `cost = 0.0` at 91), `desk/record.mli`, `desk/session_close.ml` (140–148), `desk/alpaca_trade.ml` (account activities), `desk/journal.ml`
- Test: `test/test_record.ml`, `test/test_session_close.ml`, `test/test_alpaca_trading.ml`

**Interfaces — Produces:**
- `Record.fill` gains `arrival_mid : float option` and `fee : float`.
- `Record.dividend : { ex_date : Date.t; symbol : Symbol.t; per_share : float }`
- `Record.sessions_of` gains `~dividends:dividend list`; a strategy holding `q` shares at the prior close is credited `q × per_share` on `ex_date`.
- `Alpaca_trade.activities : t -> since:Date.t -> kinds:string list -> activity list Deferred.Or_error.t`
- `Session_close.official_close : venue -> date:Date.t -> Symbol.t -> (float * [ `Bar | `Last_print ]) option`

- [ ] Write failing tests: a hand-computable case — hold 100 TLT, close 90.00 then 89.70 on an ex-date paying 0.30: session P&L is 0.00, not −30.00; a fill of 100 at 50.05 against arrival mid 50.00 with fee 0.10 has cost 5.10 and `summarise` gives `cost_bps = 10.2`; a fill with no arrival mid costs its fee only and is counted in a `cost_unknown` field; a short through an ex-date is debited; `official_close` prefers the bar and tags `Last_print` when it falls back.
- [ ] Run; implement; the nightly close pulls `DIV` and `FEE` since the last recorded session; run `make test`. Expected: PASS.
- [ ] Recompute every strategy's sessions from 2026-10-21 with the new rule on a journal copy; write the before/after per strategy into the ledger. Expected: differences only on ex-dates and by cost.
- [ ] Commit: `desk: a strategy's P&L has its dividends, its fills' cost against the arrival quote, and the venue's close -- every ex-date read as a loss and cost was always zero`.

### Task 1.9: Limits that fit an ETF book

**Files:**
- Modify: `lib/types.ml` (limit kinds), `lib/limits.ml`, `lib/config.ml`, `lib/graph.ml` (1277–1283, 1509), `desk/rules.ml`, `book.example.sexp`
- Test: `test/test_graph.ml`, `test/test_rules.ml`, `test/test_example_book.ml`, `test/test_properties.ml`

**Interfaces — Produces:**
- Limit kinds `(Class_gross FRACTION)` scope `(Sector NAME)`, `(Name_weight FRACTION)` scope `Portfolio` (applies to every instrument), `(Expected_shortfall DOLLARS)` scope `Portfolio`.
- `Config.Risk.var_window : int` 250, `es_confidence : float` 0.99.
- No net-beta limit and no borrow or buying-power rule (spec, *Cut*).

- [ ] Write failing tests: a book 20% in one ETF breaches `Name_weight 0.15`; ES is the mean of the worst 1% of 250 P&Ls (with 250 observations: the mean of the worst 2.5 by interpolation, pinned to a hand-computed fixture); the example book loads with `es-cap`, `name-cap` and one `Class_gross` per sector and no AAPL/NVDA/TECH/ENERGY caps.
- [ ] Run; implement; set example values: `es-cap` $45,000, `name-cap` 0.15, `Class_gross` 0.35 per sector; `kill_switch_trips_on (book-cap es-cap dd-cap)`; `var-cap` stays as a non-tripping alert. Run `make test`. Expected: PASS.
- [ ] Commit: `desk: the book's limits are the book's -- name weight, class gross and a 99% shortfall over 250 sessions`.

### Task 1.10: The risk model is scored every night

**Files:**
- Modify: `desk/session_close.ml` (111–138), `lib/var_backtest.ml` (expose a pure entry over forecasts), `quant/src/ohcamel_quant/signals/report.py`, `quant/src/ohcamel_quant/api/routers/artifacts.py` (`PUBLIC_TABLES`)
- Test: `test/test_var_backtest.ml`, `test/test_session_close.ml`, `quant/tests/test_signals_report.py`, `quant/tests/test_artifacts_api.py`

**Interfaces — Produces:**
- `Var_backtest.score : forecasts:(Date.t * float) list -> realised:(Date.t * float) list -> level:float -> { n; exceptions; kupiec_p; christoffersen_p; light : [ `Green | `Yellow | `Red ] }`
- `/api/desk` field `var_score`; `desk.report` table `var_score` (public: counts, p-values, light; no dollar figure).

- [ ] Write failing tests: 250 forecasts with 3 exceptions at 99% is green with `kupiec_p > 0.05`; 12 exceptions is red; fewer than 60 scored sessions returns `None` and the report says "n of 60"; the public table contains no column named `var`, `pnl` or `dollars`.
- [ ] Run; implement; run `make test && make quant-test`. Expected: PASS.
- [ ] Commit: `desk: the journaled risk forecasts are tested against what happened, every close`.

### Task 1.11: The watch pages a person

**Files:** Modify `deploy/watch.sh`, `book.example.sexp` (alerts block, 144–149), `docs/runbooks/` (create `desk.md`). Test: `deploy/test/watch_test.sh`.

- [ ] Add failing cases to `watch_test.sh`: tripped for more than one session alerts once per day; a `Subledger` break alerts; `completions exhausted` in the last hour alerts; overlay older than 4 session hours alerts; `var_score.light = red` alerts; each is silent when its condition is absent.
- [ ] Implement; enable the alerts block in the example book with the webhook read from `deploy/.env` (`OHCAMEL_ALERT_URL`, owner sets it; unset means log only, and the watch says so once a day).
- [ ] Write `docs/runbooks/desk.md`: for each alert, what it means, the read-only check, and the owner's action.
- [ ] Run `deploy/test/watch_test.sh` and `shellcheck deploy/watch.sh`. Expected: pass, clean.
- [ ] Commit: `deploy: the watch says when the desk is tripped, broken, starved or unscored, and the runbook says what to do`.

### Task 1.12: Deploy the desk

- [ ] Run the sim-venue soak: `dune exec bin/main.exe -- demo` for one simulated week with a forced −5% gap day. Expected: trip after debounce, governor cuts and stop flattens fill while tripped, restart comes back tripped, no order increases exposure.
- [ ] Owner deploys with `--live` outside session hours. Verify read-only: SHA, switch still tripped by `dd-cap`, sub-ledger no breaks, positions graph==venue.
- [ ] Update `docs/status.md` and the ledger.

---

## Stage 2 — The verdict and the experiment

### Task 2.1: INC-01 closes; INC-02 is drafted for the owner (R1, R4)

**Files:**
- Modify: `research/experiments/INC-01/preregistration.md` (append `## Amendment A1, 2026-11-15` and `## Closed`)
- Create: `research/experiments/INC-02/preregistration.md`, `research/experiments/INC-02/roster.yaml`
- Modify: `research/tests/test_experiments_present.py`, `docs/CHARTER.md` (the incubation sentence names INC-02's stop rule)

- [ ] Write the failing test: INC-02 exists, its roster has 22 rows of 0.025 with no `farm_pairs_coint_sectors` and no `q01_ls_quintile`, `incubation_cap 0.55`, and agrees row for row with `book.example.sexp`; INC-01 contains the string `Closed: 2026-11-05`.
- [ ] Write A1: the dd-cap moved from 0.02 to 0.10 on 2026-11-05 after the cap tripped, by an agent-drafted spec; asked on 2026-11-15, the owner did not know whether they had approved it; the change therefore breaks the charter's rule on gates and is recorded as such; INC-01's record ends at that close and is not continued.
- [ ] In INC-02, argue the drawdown cap on its merits before any INC-02 session trades: the vol target's one-year 2σ loss, the governor's ramp, what 0.02, 0.05 and 0.10 would each have done over the fixture history. State 0.10 as the proposal and leave the line for the owner to approve or change.
- [ ] Write INC-02 exactly from the spec's Research section: the pooled test (500 sessions, one-sided 5% by PSR, power 41% at Sharpe 1.0 and 17% at 0.5, stated in the first paragraph), vol-normalised stops (`2.0 × σ`, floor 0.05, cap 0.25, 500 sessions), reduce-through-trip, every INC-01 clause marked "awaiting the owner's confirmation" restated as its own numbered line. Leave `Approved-by:` empty.
- [ ] Run `make research-test`. Expected: PASS.
- [ ] Commit: `research: INC-01 closes at the day its limit was changed, and INC-02 states one test that can be wrong`. **Stop and hand INC-02 to the owner for approval, line by line.**

### Task 2.2: Stops by volatility; the switch is reset by the owner

**Files:** Modify `desk/stop.ml`, `desk/stop.mli`, `lib/config.ml` (strategy stop words), `quant/src/ohcamel_quant/signals/roster.py`, `quant/src/ohcamel_quant/signals/freeze.py` (writes each strategy's ex-ante vol), `book.example.sexp`. Test: `test/test_stop.ml`, `quant/tests/test_signals_roster.py`.

**Interfaces — Produces:** strategy words `(stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500) (ex_ante_vol FLOAT)`; `Stop.threshold : ex_ante_vol:float -> multiple:float -> floor:float -> cap:float -> float`.

- [ ] Write failing tests: vol 0.04 gives threshold 0.08; vol 0.02 gives the floor 0.05; vol 0.15 gives the cap 0.25; a strategy with no `ex_ante_vol` fails to load, naming it; `freeze` writes the 252-session realised vol of the cell's backtest at traded parameters and gross 1.
- [ ] Run; implement; run `make test && make quant-test`. Expected: PASS.
- [ ] Commit: `desk: a strategy stops at twice its own volatility -- fifteen percent stopped the volatile ones by chance and the quiet ones never`.
- [ ] After the owner approves INC-02 and Stage 1 is live: the owner regenerates `book.sexp` from the example, deploys, and resets the switch. The agent verifies read-only and records INC-02's first session in the ledger.

### Task 2.3: One cost file, tiered, with borrow, financing and capacity

**Files:**
- Create: `research/config/costs.yaml`, `quant/src/ohcamel_quant/backtest/costs.py`
- Modify: `quant/src/ohcamel_quant/backtest/engine.py` (93–94), `quant/src/ohcamel_quant/products/farm.py` (66, 77–78), `quant/src/ohcamel_quant/models/farm.py` (83–87), `quant/src/ohcamel_quant/signals/daily.py`, `research/src/ohcamel_research/battery/gates.py` (reads the same file)
- Test: create `quant/tests/test_backtest_costs.py`; `quant/tests/test_backtest_engine.py`, `quant/tests/test_products_farm.py`

**Interfaces — Produces:**
- `costs.load() -> CostModel` with `half_spread_bps(ticker) -> float`, `borrow_bps: float = 50`, `financing_spread_bps: float = 50`, `scaled(mult: float) -> CostModel`.
- `costs.capacity(weights: pd.DataFrame, dollar_volume: pd.DataFrame, participation: float = 0.01) -> float`
- Leaderboard columns `turnover`, `capacity_usd`, `sr_at_0x`, `sr_at_1x`, `sr_at_3x`, `sr_at_6x`.

- [ ] Write failing tests: SPY is tier 1 (1 bp), LQD tier 2 (3 bp), UNG tier 3 (8 bp); a ticker with no tier raises; a hand-computable two-asset backtest pays 8 bp on a UNG round trip and 50 bp/y on a held short; gross 2 pays financing on the second unit; the sweep is computed on the walk-forward out-of-sample returns (assert it differs from the full-sample series on a fixture where the two differ); capacity of a strategy trading 10% of capital in a name with $10M median dollar volume is $1,000,000.
- [ ] Assign every one of the 42 site ETFs a tier in `costs.yaml` from its median IEX quoted half-spread over the stored minute bars' sessions, rounded up to the tier; commit the measurement script output beside it.
- [ ] Run; implement; run `make quant-test && make research-test`. Expected: PASS.
- [ ] Commit: `research: one cost file for the backtest, the farm and the desk's signals -- spread by tier, borrow, financing, a sweep on the series the gates judge, and a capacity figure`.

### Task 2.4: The farm scores what trades, and counts every trial

**Files:**
- Modify: `quant/src/ohcamel_quant/products/farm.py` (37, 59, 77–89, 116–120), `quant/src/ohcamel_quant/models/farm.py` (46–59), `quant/src/ohcamel_quant/backtest/metrics.py` (206–207), `quant/src/ohcamel_quant/backtest/engine.py` (fill price option)
- Create: `quant/src/ohcamel_quant/models/trial_ledger.py`
- Test: `quant/tests/test_models_farm.py`, `quant/tests/test_products_farm.py`, `quant/tests/test_backtest_metrics.py`, create `quant/tests/test_trial_ledger.py`

**Interfaces — Produces:**
- `BacktestConfig.fill: Literal["close", "open"]`; the farm uses `"open"`, lag 1, `max_gross=1.0`.
- `trial_ledger.append(path, cell_id, grid_version, params_hash, sharpe, n_obs) -> None` (append-only JSONL under the artifact root, never rewritten); `trial_ledger.distinct(path) -> pd.DataFrame`.
- `farm_cells()` validates each cell: `spec.min_assets ≤ len(universe) ≤ spec.max_assets`; `pairs_coint × sectors` is not a cell.
- `expected_max_sharpe` raises `ValueError` when `n_trials < 2` or the variance is not finite.

- [ ] Write failing tests: `("pairs_coint", "sectors")` is absent from `farm_cells()` and every remaining cell passes the size check; running the farm twice on a fixture doubles nothing (same `params_hash` is one trial) while a changed grid adds trials; `farm_dsr` uses the ledger's count; `expected_max_sharpe(n_trials=1, …)` raises; a cell's out-of-sample series on a fixture equals a hand backtest filled at the next open with gross 1.
- [ ] Run; implement; run `make quant-test`. Expected: PASS.
- [ ] Commit: `research: the farm is scored at the leverage and the fill it trades at, and its deflation counts every trial it has ever run`.

### Task 2.5: Gates — benchmark-relative, one regime table, tighter (R3)

**Files:**
- Create: `research/config/regimes.yaml`
- Modify: `quant/src/ohcamel_quant/models/verdict.py` (23–30, 97–117), `research/src/ohcamel_research/battery/gates.py` (72–78), `docs/CHARTER.md` (the gates table), `quant/src/ohcamel_quant/products/farm.py` (87)
- Test: `quant/tests/test_models_verdict.py`, `research/tests/` (the gates test), `quant/tests/test_products_methodology.py`

**Interfaces — Produces:**
- `PSR_MIN = 0.95`, `DSR_MIN = 0.95`, `SPA_MAX_P = 0.10`.
- `charter_verdict(..., spa_p: Any, regimes_covered: int)`; a `Gate("excess", spa_p, "SPA p <= 0.10 vs buy-and-hold", …)`.
- `regimes.load() -> tuple[tuple[str, str, str], ...]` read by both packages; fewer than `REGIMES_MIN` covered returns `insufficient(...)`.

- [ ] Write failing tests: PSR 0.90 fails; DSR 0.50 fails; SPA p 0.20 fails with every other gate passing; a series covering two regimes returns `INSUFFICIENT DATA`, not `FAIL`; both packages return the same regime tuple; the charter's table text matches the constants (the methodology test reads the file).
- [ ] Decide the regime list once, in the YAML: 2008, 2011, 2015–16, 2018Q4, 2020, 2022, 2024 — the union of the two existing lists plus 2011; `REGIMES_MIN` stays 3.
- [ ] Run; implement; rerun the farm locally on the fixture warehouse and record the verdict table in the ledger. Expected: nothing passes that did not pass before; the five sector cells read `INSUFFICIENT DATA`.
- [ ] Commit, carrying R3: `research: a strategy must beat its own universe held, at 0.95 -- Sharpe against zero passed long-or-flat SPY in a bull market`.

---

## Stage 3 — Data and reproducibility

### Task 3.1: One read path repairs splits

**Files:** Modify `quant/src/ohcamel_quant/warehouse/readers.py` (116–147), `quant/src/ohcamel_quant/products/q01.py` (37), `quant/src/ohcamel_quant/machine/load.py` (173), `quant/src/ohcamel_quant/products/vol_league.py` (41), `quant/src/ohcamel_quant/signals/daily.py` (226, 282). Test: `quant/tests/test_warehouse_readers.py`, `quant/tests/test_products_q01.py`, `quant/tests/test_signals_daily.py`, `quant/tests/test_splits.py`.

**Interfaces — Produces:** `readers.bars_panel(con, tickers: list[str], start: date | None, end: date | None, *, field: str = "adj_close") -> tuple[pd.DataFrame, list[str]]` (wide frame, repair notes).

- [ ] Write failing tests on the fixture warehouse's unadjusted MTUM-shaped split: `bars_panel` returns no return beyond ±30% on the ex-date and one note; Q01's feature panel has no such return; the Machine's closes match `bars_panel`; `signals.daily`'s data hash equals the hash of the frame the strategy read; `ops.data_integrity` still reports `split_suspect` on the same warehouse.
- [ ] Run; implement; run `make quant-test`. Expected: PASS.
- [ ] Commit: `fix: every reader of daily bars gets the repaired series -- the cross-sectional model and the vol league read the vendor's bytes while the pages read the repair`.

### Task 3.2: Raw bars and a corporate-action table

**Files:**
- Create: `quant/src/ohcamel_quant/warehouse/migrations/002_raw_bars.sql`, `quant/src/ohcamel_quant/warehouse/ingest/corp_actions.py`, `quant/src/ohcamel_quant/warehouse/adjust.py`
- Modify: `quant/src/ohcamel_quant/warehouse/ingest/bars.py`, `quant/src/ohcamel_quant/jobs/schedules.yaml`, `quant/src/ohcamel_quant/jobs/kinds.py`, `quant/src/ohcamel_quant/products/data_integrity.py`
- Test: create `quant/tests/test_warehouse_adjust.py`; `quant/tests/test_warehouse_bars.py`, `quant/tests/test_jobs_schedules.py`

**Interfaces — Produces:**
- Tables `bars_raw(ticker, date, open, high, low, close, volume, source)` and `corp_actions(ticker, ex_date, kind, ratio, cash, source, fetched_at)`.
- `adjust.series(raw: pd.DataFrame, actions: pd.DataFrame, *, dividends: bool) -> pd.DataFrame`
- Job kind `ingest.corp_actions`, weekdays 18:20, class S.
- Integrity check `adjust_drift`: derived `adj_close` against `bars_daily.adj_close`, flagged beyond 1e-6 relative.

- [ ] Write failing tests: a 2-for-1 split halves prior raw closes and doubles prior volume; a $0.50 dividend on a $100 close scales prior closes by 0.995 when `dividends=True` and leaves them when `False`; applying the same action twice is impossible (keyed on `ticker, ex_date, kind`); the derived series equals the committed vendor-adjusted fixture within 1e-6.
- [ ] Run; implement; backfill on the fixture warehouse. Run `make quant-test`. Expected: PASS.
- [ ] Commit: `warehouse: raw bars and the actions that adjust them are kept, and the adjusted series is derived -- every dividend used to rewrite a ticker's history`.
- [ ] On the droplet (owner deploys): 30 days of `adjust_drift` clean, then a follow-up commit drops the writes to `bars_daily` and points `readers` at `adjust.series`.

### Task 3.3: A number can be regenerated

**Files:**
- Modify: `quant/src/ohcamel_quant/jobs/artifacts.py` (60–85), `quant/src/ohcamel_quant/jobs/context.py`, each product under `quant/src/ohcamel_quant/products/` that draws random numbers (`atlas.py:26` and the others found by `grep -n "default_rng\|seed"`), `deploy/backup.sh`, `deploy/backup.list`, `deploy/restore.sh`
- Create: `tools/regenerate.py`, `docs/runbooks/restore-drill-2026-11.md`
- Test: `quant/tests/test_jobs_artifacts.py`, `deploy/test/` (backup and restore scripts), create `quant/tests/test_regenerate.py`

**Interfaces — Produces:**
- `ctx.read(name: str, frame: pd.DataFrame) -> pd.DataFrame` records the frame's SHA-256 (canonical Parquet bytes); the manifest gains `data_hash` (hash of the sorted per-read hashes), `seed`, `lock_hash`.
- Backup method `export`: `EXPORT DATABASE '<dir>' (FORMAT PARQUET)` from a read-only connection, to `/var/backups/ohcamel/warehouse/YYYY-MM-DD/`, newest 14 and month-ends kept.
- `tools/regenerate.py <artifact dir> --warehouse <export dir>` reruns the job and exits non-zero unless every table matches (floats within 1e-9).

- [ ] Write failing tests: two runs of a seeded product on the same fixture produce equal `data_hash` and tables; changing one input row changes `data_hash`; a product that draws random numbers with no seed in its params fails the manifest build; `regenerate.py` passes on a fixture artifact and fails when a row of the export is altered; the backup test finds a dated export directory and 15 directories prune to 14 plus month-ends.
- [ ] Run; implement; run `make quant-test` and the deploy tests. Expected: PASS.
- [ ] Run the restore drill on a scratch droplet or a local container from last night's backup; commit the transcript as `docs/runbooks/restore-drill-2026-11.md`.
- [ ] Commit: `jobs: a manifest names the data it read, its seed and its lock file, and the warehouse is exported every night -- so a published number can be made again`.

### Task 3.4: Option snapshots a surface can be built from

**Files:** Create `quant/src/ohcamel_quant/warehouse/migrations/003_option_quotes.sql`. Modify `quant/src/ohcamel_quant/warehouse/ingest/options.py` (27), `quant/src/ohcamel_quant/products/surface_history.py` (41, 76), `quant/src/ohcamel_quant/jobs/schedules.yaml`, `quant/src/ohcamel_quant/api/routers/options.py` (81–100). Test: `quant/tests/test_warehouse_options.py`, `quant/tests/test_products_surface_history.py`, `quant/tests/test_options_router.py`.

**Interfaces — Produces:** columns `spot DOUBLE, quote_ts TIMESTAMP, vendor_iv DOUBLE, pull TEXT` on `option_snapshots`; primary key gains `pull` (`'1545'`, `'1620'`); `surface_history` prefers `'1545'`; discounting uses `macro.curve` bootstrapped zeros as of the snapshot date; time to expiry is from `quote_ts` to 16:00 New York on expiry.

- [ ] Write failing tests: two pulls on one day are two sets of rows; a chain stamped Sunday prices its Tuesday expiry with T counted from the stamp (the 1.7-day, 48%-implied-q case from the review reads T ≥ the true business-time and implied q under 5%); a back-processed 2026-10-20 snapshot uses 2026-10-20's curve; the router discounts with a zero rate that differs from the par yield on a steep-curve fixture.
- [ ] Run; implement; old rows keep `pull = '1620'` and null new columns. Run `make quant-test`. Expected: PASS.
- [ ] Commit: `warehouse: an option snapshot keeps its spot, its quote time and the vendor's vol, and there is one before the close`.

### Task 3.5: Membership with dates; revised series as first published

**Files:**
- Modify: `quant/src/ohcamel_quant/warehouse/ingest/universes.py` (36), `quant/src/ohcamel_quant/warehouse/constituents/__init__.py`, `quant/src/ohcamel_quant/warehouse/ingest/macro.py` (60, 81), `quant/src/ohcamel_quant/data/fred.py`, `quant/src/ohcamel_quant/macro/recession.py` (146), `quant/src/ohcamel_quant/warehouse/readers.py`
- Create: `quant/src/ohcamel_quant/warehouse/migrations/004_membership_vintages.sql`
- Test: `quant/tests/test_warehouse_universes.py`, `quant/tests/test_warehouse_macro_sec.py`, `quant/tests/test_macro_recession.py`

**Interfaces — Produces:**
- Table `universe_membership(universe, ticker, start, end)`; `readers.members_asof(con, universe: str, asof: date) -> list[str]`.
- Table `fred_vintages(series, date, realtime_start, value)`; `readers.fred_asof(con, series: str, asof: date) -> pd.Series`.
- `recession.fit_expanding(...) -> RecessionOOS` with `oos_prob: pd.Series`, `oos_brier: float`.

- [ ] Write failing tests: a ticker removed from the S&P 500 in 2024 is a member as of 2023-06-30 and not as of 2025-01-02 (fixture: a committed slice of the changes table with its sidecar); `fred_asof("UNRATE", 2020-05-15)` returns the first-published April 2020 value, not today's revision (fixture: committed ALFRED slice); the probit's 2008 probabilities come from fits that end before each date; the page's headline number is `oos_brier`.
- [ ] Run; implement; FRED vintages are appended, never deleted. Run `make quant-test`. Expected: PASS.
- [ ] Commit: `warehouse: index membership has dates and revised macro series are read as first published -- the recession model was fitted on numbers nobody had at the time`.
- [ ] Leave single-name backtests refused here; Task 3.8 is what lifts it.

### Task 3.6: Jobs time out, backups catch up, integrity looks wider

**Files:** Modify `quant/src/ohcamel_quant/jobs/runner.py`, `quant/src/ohcamel_quant/jobs/kinds.py`, `quant/src/ohcamel_quant/products/data_integrity.py` (15–24, 44), `quant/src/ohcamel_quant/warehouse/freshness.py`, `deploy/systemd/` or the crontab installer. Test: `quant/tests/test_jobs_worker.py`, `quant/tests/test_products_research_ops.py`, `quant/tests/test_warehouse_freshness.py`, `deploy/test/`.

**Interfaces — Produces:** `Kind.wall_limit_s` (S 600, M 2700, L budget + 600); integrity findings `missing_session`, `flat_bar`, `vendor_change`, `stale_minute`, `stale_options`, `stale_fred`; `freshness` uses the exchange calendar from Alpaca's `/v2/calendar`, cached in the warehouse.

- [ ] Write failing tests: a child that sleeps past its limit is killed and the job is `failed` with `wall limit`; a series missing one session inside its range is flagged; a zero-volume bar on a tier-1 name is flagged; a ticker whose `source` changes mid-series is flagged; the day after Thanksgiving's half session is not stale; a backup missed by a reboot runs within 15 minutes of boot.
- [ ] Run; implement; run `make quant-test` and the deploy tests. Expected: PASS.
- [ ] Commit: `jobs: a job that hangs is killed, a backup a reboot missed is run, and the integrity check sees holes, flat bars and a change of vendor`.

### Task 3.7: Burst research (R6)

**Files:** Create `tools/burst.py`, `docs/runbooks/burst.md`. Modify `Makefile` (`research-burst`), `quant/src/ohcamel_quant/jobs/queue.py` (accept a signed artifact upload for an existing queued job). Test: create `quant/tests/test_burst.py`; `quant/tests/test_jobs_api.py`.

**Interfaces — Produces:** `make research-burst KIND=<kind> PARAMS='<json>'`: pulls the newest export over rsync, runs the handler locally with `OHCAMEL_THREADS` set to the machine's cores, and uploads the staged artifact through `publish` with the manifest field `host: burst`. Upload is authenticated by the live host's basic auth and refused for any kind not marked `burstable`.

- [ ] Write failing tests: an artifact produced by `burst.py` on the fixture export equals the one the worker produces (same `data_hash`, tables within 1e-9); an upload for a non-burstable kind is 403; an upload whose `data_hash` matches no export the droplet holds is 409.
- [ ] Run; implement; mark `farm.sweep`, `models.xs_lgbm`, `cov.league`, `risk.mc_atlas` burstable. Run `make quant-test`. Expected: PASS.
- [ ] Commit: `tools: a heavy job can run on another machine against last night's export and publish through the same door`.

### Task 3.8: Delisted names behind a switch (R5)

**Files:**
- Create: `quant/src/ohcamel_quant/data/eodhd.py`, `quant/src/ohcamel_quant/warehouse/ingest/delisted.py`, `quant/tests/data/delisted_acceptance.csv` (26 rows: ticker, name, last trading date, reason; with a provenance sidecar), `tools/accept_delisted.py`
- Modify: `quant/src/ohcamel_quant/config.py` (`delisted_source: Literal["", "eodhd"]`, key from `EODHD_API_KEY`), `quant/src/ohcamel_quant/jobs/kinds.py`, `quant/src/ohcamel_quant/jobs/schedules.yaml`, `quant/src/ohcamel_quant/backtest/engine.py` (the single-name refusal), `docs/runbooks/compute.md`, `docs/CHARTER.md` (the Data section's last sentence)
- Test: create `quant/tests/test_data_eodhd.py`, `quant/tests/test_warehouse_delisted.py`; `quant/tests/test_backtest_engine.py`

**Interfaces — Produces:**
- `eodhd.bars(ticker: str, start: date, end: date, *, delisted: bool) -> pd.DataFrame` (raw OHLCV) and `eodhd.actions(ticker) -> pd.DataFrame`, both writing into `bars_raw` / `corp_actions` with `source = "eodhd"`.
- Job `ingest.delisted`, Sunday 04:00, class M: every ticker in `universe_membership` with an `end` date and no bars to that date.
- `readers.survivorship_free(con, universe: str) -> tuple[bool, float]` (accepted flag, share of ever-members with bars to their end date).
- The engine's single-name refusal lifts only when `survivorship_free` is `(True, ≥ 0.95)`; every such result's notes carry the coverage.
- `tools/accept_delisted.py` exits 0 only if at least 24 of the 26 acceptance names return bars within 5 sessions of their last trading date, and writes `accepted_at` into the warehouse.

- [ ] Write failing tests against recorded HTTP fixtures (a committed EODHD response for one listed and one delisted ticker, sidecars included): the adapter parses both; with the switch off the job is not scheduled and the refusal stands; with the switch on and coverage 0.80 the refusal stands and says "80% of members have prices to their exit"; with acceptance recorded and coverage 0.97 a single-name backtest over `members_asof` runs and its notes say 97%; a ticker reused by a later company is not spliced (two rows in `universe_membership`, two series).
- [ ] Run; implement; run `make quant-test`. Expected: PASS.
- [ ] Write the runbook section: what to buy (EODHD All-World, about $20/month; check the live price), where the key goes (`deploy/.env`), the one command (`tools/accept_delisted.py`), and what to do if acceptance fails (cancel; the switch stays off; nothing else changes). Add the optional Massive Stocks Starter note for whole-market minute bars, about $29/month, as a second switch `OHCAMEL_MINUTE_SOURCE=massive` that this plan documents and does not build.
- [ ] Commit: `warehouse: delisted names from a paid source, behind a switch and an acceptance test on known failures -- single-name research stays refused until the test passes on the owner's key`.

---

## Stage 4 — The math

### Task 4.1: SVI wings and the density

**Files:** Modify `quant/src/ohcamel_quant/options/svi.py` (54, 102–117, 213, 237, 310), `quant/src/ohcamel_quant/options/density.py` (76–131), `native/kernels/src/svi.rs` (15, 200), `quant/src/ohcamel_quant/kernels/reference.py`. Test: `quant/tests/test_options_metrics_density.py`, `quant/tests/test_options_svi_parity.py`, `quant/tests/test_kernels_options.py`, `quant/tests/test_kernels_parity.py`, `quant/tests/test_data_live.py` (the SPY density test). Fixture: commit the 2026-11-15 SPY chain used in the review under `quant/tests/data/` with its sidecar.

**Interfaces — Produces:**
- `SLOPE_CAP = 2.0`; `calibrate` bounds `m` to `[k.min(), k.max()]`; a candidate with `butterfly_g < -tol` anywhere on `[-K_MAX, K_MAX]`, `K_MAX = 3.0`, is rejected in the outer search.
- `butterfly_check(p, k_grid=None, ...)` defaults to the grid on `[-K_MAX, K_MAX]`.
- `RNDensity` gains `tail_mass_left: float`, `tail_mass_right: float`, `integral: float`; `breeden_litzenberger` never widens past `K_MAX` and never clips the CDF: a non-monotone CDF raises `DensityError`.

- [ ] Write failing tests on the committed chain: every one of the 31 slices has `b(1+|ρ|) ≤ 2 + 1e-9` and `m` inside its quotes; every slice's `integral + tail masses` is in [0.99, 1.01] and its mean is within 0.5% of the forward; `butterfly_check` reports a violation for the review's bad parameters (`ρ = 0.9, m = 0.58`, right slope 3.9); the synthetic left-slope-0.45 case terminates with `|k| ≤ 3` and integral in [0.99, 1.01]; Rust and reference agree to 1e-10 on parameters for the fixture; in-sample fit RMSE is under 1.0 vol point on every slice.
- [ ] Run. Expected: FAIL on 13 slices.
- [ ] Implement in the reference, then the kernel; rebuild with `make kernels-dev`.
- [ ] Run `make quant-test`, `cargo test`, and `make quant-live-test`. Expected: PASS, including the live SPY density test that CI has been carrying as allowed-to-fail; remove its `continue-on-error`.
- [ ] Commit: `fix: an SVI slice obeys Lee's bound and keeps its vertex inside its quotes, and a density that does not integrate to one says how much it left out`.

### Task 4.2: American options are priced as American

**Files:** Create `quant/src/ohcamel_quant/options/american.py`, `native/kernels/src/american.rs`. Modify `quant/src/ohcamel_quant/options/chain.py` (156–165), `quant/src/ohcamel_quant/options/parity.py`, `quant/src/ohcamel_quant/kernels/reference.py`, `quant/src/ohcamel_quant/kernels/__init__.py`. Test: create `quant/tests/test_options_american.py`; `quant/tests/test_options_chain_strategy.py`, `quant/tests/test_kernels_parity.py`.

**Interfaces — Produces:**
- `american.price(S, K, T, r, sigma, *, is_call: bool, dividends: Sequence[tuple[float, float]] = (), q: float = 0.0, steps: int = 400) -> float` (CRR, escrowed discrete dividends).
- `american.implied_vol(price, S, K, T, r, *, is_call, dividends=(), q=0.0) -> float` (Brent on `price`).
- `chain` gains `exercise: Literal["american", "european"]` per underlying (index options European, ETF and single-name American) and uses `american.implied_vol` for American contracts; the parity forward uses only the strike pair nearest the money and bounds, not equality, elsewhere.

- [ ] Write failing tests: a deep ITM American put is worth at least intrinsic and more than the European put; with no dividends an American call equals the Black–Scholes call within 1e-3 at 400 steps; with q = 0.12 > r = 0.08 (S=100, K=100, T=0.5, σ=0.2) the American call exceeds the European call by more than 0.05; the price at 400 steps is within 0.01 of the price at 1,600 steps; a call with one discrete dividend of 2.00 paid at T/2 is worth at least the larger of the European value and the value of exercising just before the dividend; on the SPY fixture, put and call IV at the same strike differ by under 0.5 vol points for |k| < 0.15; kernel and reference agree to 1e-8.
- [ ] Run; implement; run `make quant-test && cargo test`. Expected: PASS.
- [ ] Commit: `options: an American option is inverted with an American price -- in-the-money puts showed four to seven points of volatility that was early exercise`.

### Task 4.3: The vol league scores one quantity

**Files:** Modify `quant/src/ohcamel_quant/products/vol_league.py` (39–49, 69), `quant/src/ohcamel_quant/models/vol_league.py`, `quant/src/ohcamel_quant/models/surface_metrics.py` (68–71), `quant/src/ohcamel_quant/options/realized.py` (labels). Test: `quant/tests/test_products_vol_league.py`, `quant/tests/test_models_vol_league.py`, `quant/tests/test_models_surface.py`, `quant/tests/test_options_realized.py`.

**Interfaces — Produces:** `_daily_rv(ctx, ticker, days) -> np.ndarray` = open-to-close minute RV + (open_t / close_{t−1} − 1)², IEX-sparse names (fewer than 100 prints in the session) excluded with a note; every estimator in `realized.py` carries `covers: Literal["open_to_close", "close_to_close"]` in its output.

- [ ] Write failing tests: on a fixture day with a 1% overnight gap the target exceeds the minute RV by 1e-4; EWMA's QLIKE against the new target on the fixture is lower than against the old one; VRP on the fixture falls by the overnight share; a name with 40 prints is excluded and named in the notes.
- [ ] Run; implement; run `make quant-test`. Expected: PASS.
- [ ] Commit: `fix: the vol league's target includes the overnight move -- close-to-close forecasts were scored against open-to-close variance, so the model trained on the target won by construction`.

### Task 4.4: Small corrections

**Files:** Modify `quant/src/ohcamel_quant/models/mc_risk.py` (61–64), `quant/src/ohcamel_quant/risk/var.py` (352–374), `native/kernels/src/` (GARCH fit), `quant/src/ohcamel_quant/kernels/__init__.py` (dispatch), `quant/src/ohcamel_quant/kernels/bench.py`, `docs/perf/kernels.json`. Test: `quant/tests/test_models_mc_risk.py`, `quant/tests/test_risk_models.py`, `quant/tests/test_kernels_garch.py`, `quant/tests/test_kernels_bench.py`.

- [ ] Failing tests: copula ν on a simulated t-copula with ν = 5 and Gaussian marginals is estimated in [4, 6.5] (the median-of-marginals rule returns over 30); EVT VaR on a GARCH-simulated series uses standardised residuals and matches the simulated 99% conditional quantile within 10%; the 10-day FHS path's variance on a high-vol start exceeds the iid draw's; `garch_fit` dispatches to whichever engine the droplet benchmark says is faster; the benchmark file's `host` field is the droplet's CPU model.
- [ ] Implement: profile likelihood over ν for the copula; POT on `ε_t = r_t / σ_t` rescaled by the forecast σ; FHS for the 10-day atlas; rerun `bench` on the droplet through a job and commit the file.
- [ ] Run `make quant-test && cargo test`. Expected: PASS.
- [ ] Commit: `fix: the copula's tail is fitted to the copula, the extreme-value VaR is conditional, and the kernel table was measured where it runs`.

### Task 4.5: An arbitrage-free surface (SSVI), scored

**Files:** Create `quant/src/ohcamel_quant/options/ssvi.py`, `quant/src/ohcamel_quant/products/surface_league.py`. Modify `quant/src/ohcamel_quant/options/surface.py`, `quant/src/ohcamel_quant/jobs/kinds.py`, `quant/src/ohcamel_quant/jobs/schedules.yaml`, `quant/src/ohcamel_quant/api/routers/options.py`, `quant/web/src/` (the Volatility page's surface panel). Test: create `quant/tests/test_options_ssvi.py`, `quant/tests/test_products_surface_league.py`; `quant/tests/test_jobs_schedules.py`.

**Interfaces — Produces:**
- `ssvi.SSVIParams(rho: float, eta: float, gamma: float, theta: np.ndarray, T: np.ndarray)` with power-law `φ(θ) = η θ^{-γ} (1+θ)^{γ-1}`.
- `ssvi.fit(slices: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]]) -> SSVIFit` under `η(1+|ρ|) ≤ 2` and `θ` non-decreasing.
- `ssvi.total_variance(p, k, T) -> np.ndarray`.
- Job `vol.surface_league`, weekdays 21:10, class M heavy: per underlying, in-sample RMSE and count of butterfly and calendar violations for per-slice SVI and SSVI, and next-day pricing RMSE (today's parameters on tomorrow's quotes). Verdict `DESCRIPTIVE ONLY`.

- [ ] Write failing tests: a fitted SSVI has zero calendar violations and `g ≥ 0` on `[-3, 3]` for every T on the SPY fixture; the conditions are rejected when violated (η(1+|ρ|) = 2.1 raises); recovering known parameters from a noiseless synthetic surface within 1e-4; the league table has one row per model per underlying with the three scores.
- [ ] Run; implement; the page shows both surfaces and the league's three numbers. Run `make quant-test` and `npm test` in `quant/web`. Expected: PASS.
- [ ] Commit: `options: a surface with no calendar or butterfly arbitrage by construction, scored each night against the slices it replaces`.

### Task 4.6: Heston by the COS method, scored out of sample

**Files:** Create `quant/src/ohcamel_quant/options/heston.py`, `native/kernels/src/heston.rs`. Modify `quant/src/ohcamel_quant/products/surface_league.py`, `quant/src/ohcamel_quant/kernels/`. Test: create `quant/tests/test_options_heston.py`; `quant/tests/test_kernels_parity.py`, `quant/tests/test_products_surface_league.py`.

**Interfaces — Produces:**
- `heston.HestonParams(kappa, theta, sigma, rho, v0)`; `heston.price(F, K, T, df, p, *, is_call, n_terms: int = 160) -> np.ndarray` (COS, Fang–Oosterlee, the Albrecher "little trap" characteristic function).
- `heston.calibrate(quotes, *, weights="vega") -> HestonFit` (least squares on prices, bounds, Feller reported not enforced).
- A third league entrant `heston`.

- [ ] Write failing tests: the published reference price (S=100, K=100, T=1, r=0, κ=1.5768, θ=0.0398, σ=0.5751, ρ=−0.5711, v0=0.0175) call = 5.7851 within 1e-3; put–call parity to 1e-10; σ → 0 recovers Black-76 at √θ within 1e-4; calibration recovers synthetic parameters within 5%; kernel and reference agree to 1e-9.
- [ ] Run; implement; run `make quant-test && cargo test`. Expected: PASS.
- [ ] Commit: `options: Heston priced by cosine expansion and calibrated nightly, an entrant in the surface league and not a picture`.

### Task 4.7: Covariance and beta entrants

**Files:** Modify `quant/src/ohcamel_quant/portfolio/covariance.py`, `quant/src/ohcamel_quant/models/cov_league.py`, `quant/src/ohcamel_quant/products/cov_league.py`. Create `quant/src/ohcamel_quant/risk/dcc.py`, `quant/src/ohcamel_quant/factors/kalman.py`, `quant/src/ohcamel_quant/products/beta_league.py`. Test: `quant/tests/test_portfolio_covariance.py`, `quant/tests/test_models_cov_league.py`, create `quant/tests/test_risk_dcc.py`, `quant/tests/test_factors_kalman.py`.

**Interfaces — Produces:**
- `covariance.nonlinear_shrinkage(returns: pd.DataFrame) -> pd.DataFrame` (Ledoit–Wolf 2020 analytical).
- `dcc.fit(returns: pd.DataFrame) -> DCCFit` with `forecast(h: int = 1) -> np.ndarray` (GJR-GARCH-t marginals from `risk.garch`, DCC(1,1) by two-step quasi-likelihood, `a + b < 1`).
- `kalman.dynamic_beta(y: pd.Series, x: pd.Series, *, q: float | None = None) -> KalmanBeta` (random-walk coefficients, `q` by maximum likelihood when `None`); `filtered` and `smoothed` series, only `filtered` used for scoring.
- Job `beta.league`, Saturday 08:30, class M: rolling OLS (60, 120, 250) against Kalman on next-21-day realised beta error, HAC test of the loss difference.

- [ ] Write failing tests: nonlinear shrinkage on a simulated N=50, T=100 panel has lower Frobenius loss to the truth than linear Ledoit–Wolf; DCC recovers a = 0.05, b = 0.90 within 0.03 on a simulated panel and returns a positive-definite forecast; the Kalman beta tracks a step change from 0.5 to 1.5 within 40 observations and its filtered value at t uses no data after t (truncation test); the two new estimators appear in the covariance league's ranking.
- [ ] Run; implement; run `make quant-test`. Expected: PASS.
- [ ] Commit: `models: nonlinear shrinkage and a dynamic-correlation model join the covariance league, and a state-space beta is scored against the rolling ones`.

### Task 4.8: Cross-sectional factor tests

**Files:** Modify `quant/src/ohcamel_quant/factors/models.py`, `quant/src/ohcamel_quant/factors/performance.py`, `quant/src/ohcamel_quant/factors/workbench.py`, `quant/src/ohcamel_quant/api/routers/` (the factors router), `quant/web/src/` (the workbench panel). Test: `quant/tests/test_factors_models.py`, `quant/tests/test_factors_workbench.py`, `quant/tests/test_factors_router.py`.

**Interfaces — Produces:**
- `models.fama_macbeth(returns: pd.DataFrame, exposures: dict[str, pd.DataFrame], *, nw_lags: int | None = None) -> FamaMacBeth` (per-period cross-sectional OLS, Newey–West on the premia, Shanken correction reported).
- `models.grs(alphas, resid_cov, factor_means, factor_cov, T) -> tuple[float, float]`.
- `performance.neutralise(signal: pd.DataFrame, *, groups: pd.Series | None, betas: pd.DataFrame | None) -> pd.DataFrame`.

- [ ] Write failing tests: on simulated data with a true premium of 0.5% a month the estimate is within two standard errors and the Shanken t is smaller than the plain t; GRS p-values over 500 simulated zero-alpha panels (N=10, T=240, seed 7) are uniform (Kolmogorov–Smirnov p > 0.01) and a panel with every alpha at 0.5% a month rejects at 1%; on the committed French slice (25 size-value portfolios, three factors) the statistic equals an independent computation from the regression outputs to 1e-8; a neutralised signal has zero mean within each group and zero cross-sectional covariance with beta to 1e-10.
- [ ] Run; implement; the workbench shows the premia table and the GRS line. Run `make quant-test` and `npm test`. Expected: PASS.
- [ ] Commit: `factors: Fama-MacBeth premia with Shanken's correction, the GRS test, and neutralisation by group and beta`.

### Task 4.9: Execution cost, stated honestly

**Files:** Create `quant/src/ohcamel_quant/risk/execution.py`. Modify `desk/tca.ml` (13–15, 46–67), `quant/src/ohcamel_quant/signals/report.py`, `quant/src/ohcamel_quant/api/routers/` (the risk router's liquidity section), `quant/web/src/` (the Risk page's liquidity panel). Test: create `quant/tests/test_risk_execution.py`; `test/test_tca.ml`, `quant/tests/test_signals_report.py`.

**Interfaces — Produces:**
- `execution.almgren_chriss(X: float, T: float, n: int, *, sigma: float, eta: float, gamma: float, lam: float) -> ACSchedule` with `trades: np.ndarray`, `expected_cost: float`, `variance: float`; `execution.frontier(...) -> pd.DataFrame`.
- `Tca.benchmarks` gains `vs_open`, `vs_close`, `vs_vwap` (session VWAP from the venue's bars) per order and a per-strategy roll-up.
- The report's TCA table carries the sentence: "Paper fills have no market impact and no queue; realised shortfall here is a floor on what a real venue would charge."

- [ ] Write failing tests: λ = 0 gives equal slices; large λ front-loads monotonically; the closed-form expected cost for the linear-impact case matches a hand value; a buy filled at 50.10 with open 50.00, close 50.20 and VWAP 50.05 reports +20 bp, −20 bp and +10 bp; the sentence is present in the artifact.
- [ ] Run; implement; the Risk page shows the frontier beside the square-root impact number with η and γ stated as assumptions. Run `make test && make quant-test`. Expected: PASS.
- [ ] Commit: `risk: an execution schedule and its cost frontier, and the desk's shortfall measured against the open, the close and VWAP with the paper venue's limits said beside it`.

---

## Stage 5 — The site says what is true, and done is checked

### Task 5.1: The ledger is generated

**Files:** Modify the ledger's data source (`quant/src/ohcamel_quant/api/routers/` — the route that serves `/ledger`; find it with `grep -rn "BACKUPS PAUSED" quant/ web/`), `quant/web/src/` (the Ledger page). Create `quant/src/ohcamel_quant/machine/ledger.py`. Test: create `quant/tests/test_ledger.py`; the page's Vitest file.

**Interfaces — Produces:** `ledger.build(settings, *, now) -> Ledger` whose `as_of` is `now`'s date and whose rows in `LIMITS`, `ADVISORY` and `OWNER` each come from a function of system state: backup log age, deployed SHA of each host, the desk's `book_summary` (strategy count, tier counts, switch state), the experiment directories' approval lines. Static prose rows stay in one YAML with a `checked` date; a row older than 45 days renders hatched as stale.

- [ ] Write failing tests: with a fresh backup log there is no backup-paused row; with a 3-day-old log there is; with 22 incubation strategies the advisory row says so and never says "No strategy on this site trades"; `as_of` equals today; a static row checked 60 days ago is marked stale; no row contains a 7-character hex SHA.
- [ ] Run; implement; run `make quant-test` and `npm test`. Expected: PASS.
- [ ] Commit: `site: the ledger is built from the system it describes -- it said nothing traded for three weeks after 23 strategies did`.

### Task 5.2: The desk's public record is the pooled test

**Files:** Modify `quant/src/ohcamel_quant/signals/report.py`, `quant/src/ohcamel_quant/api/routers/artifacts.py` (`PUBLIC_TABLES`), `quant/src/ohcamel_quant/machine/readouts.py`, `quant/web/src/` (the Machine's book step). Test: `quant/tests/test_signals_report.py`, `quant/tests/test_artifacts_api.py`, `quant/tests/test_machine_api.py`.

**Interfaces — Produces:** `desk.report` table `pooled_test`: `experiment` ("INC-02"), `sessions_done`, `sessions_required` (500), `psr`, `sharpe_se`, `power_at_1`, `power_at_half`, `verdict` (`"IN PROGRESS"` until 500, then `"REJECTED NULL"` or `"NOT REJECTED"`). No return, equity, position or size.

- [ ] Write failing tests: at 120 sessions the verdict is `IN PROGRESS` and `psr` is present with its SE; at 500 with PSR 0.96 it is `REJECTED NULL`; `power_at_1` is 0.41 and `power_at_half` is 0.17 to two places; the existing leak strings in `test_book_summary_is_config_and_counts` still pass and the new table has no column outside the list above.
- [ ] Run; implement; the book step reads "session n of 500 · this test detects a Sharpe of 1.0 four times in ten". Run `make quant-test` and `npm test`. Expected: PASS.
- [ ] Commit: `site: the book's record is one test with its horizon and its power, said before its result`.

### Task 5.3: Methodology, status and the definition of done

**Files:** Modify `docs/status.md`, `docs/quant_notes.md`, `docs/overview.md`, the Methodology page's content, `README.md`. Create `scripts/check-done.sh`. Test: `quant/tests/test_products_methodology.py`, `deploy/test/` (a test for the script).

- [ ] Write `scripts/check-done.sh`, one check per line of the spec's "What finished means", each a command with a pass condition: (1) sim soak log shows trip → reduce → restart-tripped; (2) `Subledger.check` on last night's journal copy returns no break and the recomputed strategy P&L sums to the account within $1; (3) the farm artifact's manifest has `fill: open`, `max_gross: 1`, a `capacity_usd` column and a trial count equal to the ledger's; (4) INC-02 has a non-empty `Approved-by:` and `pooled_test` exists; (5) `tools/regenerate.py` passes on last night's atlas, farm and surface artifacts; (6) the committed SPY chain's density integrals are in [0.99, 1.01], same-strike put/call IV differs under 0.5 points, and the vol league's target note says `close_to_close`; (7) the ledger's `as_of` is today.
- [ ] Update the methodology text for every changed model and gate; the methodology test asserts each product's card names its fill, cost model and gates as the code has them.
- [ ] Rewrite `docs/status.md`'s head for the finished state and move superseded sections under a dated heading.
- [ ] Run `scripts/check-done.sh` against the droplet read-only. Expected: seven lines `ok`.
- [ ] Commit: `docs: what finished means, as a script that checks it`.

---

## Order of execution

The project is a showcase of methods, so the order is what a visitor sees first.

| Wave | Tasks | Why now |
|---|---|---|
| A | 0.1, 0.2 | Ships what is built; the ledger stops being a month stale |
| B | 4.1, 4.2, 4.3, 5.1 | The numbers that are wrong on the page today |
| C | 1.1–1.5, then 1.6, 1.7, 1.8 | The desk is safe to leave and its record is true |
| D | 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8 | Every number regenerates; the data widens (3.3 before 3.7; 3.2 and 3.5 before 3.8) |
| E | 2.3 → 2.4 → 2.5, 2.1, then 1.9, 1.10, 1.11, 1.12, 2.2 | Verdicts about what trades; INC-02 drafted; the desk redeployed and, on the owner's approval, reset |
| F | 4.4, 4.5 → 4.6, 4.7, 4.8, 4.9 | Depth, each as a scored product |
| G | 5.2, 5.3 | The pooled test on the page; the done check |

Within a wave, tasks that share no files may run side by side: 4.1/4.2/4.3 share none; 3.1 and 3.2 both touch `readers.py` and run in order; Stage 1 tasks all touch `desk/oms.ml` or `desk/intake.ml` and run in order.

Deploys, each the owner's: after wave A; after wave B; after 1.5; after wave C; after wave D; after 1.12 with 2.2; after wave F; after wave G.

## Day-by-day schedule

One phase a day, in execution order. A day's phase is done when every task in it is implemented, reviewed clean and recorded in the ledger; an owner step (a deploy, an approval) closes the day but never blocks the next one. Tasks run one at a time, and the first two took 1.5 and 3 hours each with their reviews, so the days marked *long* need the session left running; a phase that does not finish carries its remaining tasks to the next morning, ahead of that day's phase.

| Day | Phase | Tasks | Owner step at the end |
|---|---|---|---|
| 0 (2026-11-15, done) | Ship what is built | 0.1, 0.2, 4.1 | Deployed `fff8933`; desk halted by hand |
| 1 | Wrong numbers on the page | finish 4.2 (round 3, then review), 4.3, 5.1 | Deploy `--public-only`, any time of day |
| 2 | The desk's safety core | 1.1, 1.2, 1.3, 1.4, 1.5 | Deploy `--live`, outside 09:25–17:10 ET. The halt stays |
| 3 | The desk's record is true | 1.6, 1.7, 1.8 | Deploy `--live` |
| 4 (*long*) | Every number regenerates; the data widens | 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8 | Deploy `--public-only`; decide on the EODHD subscription and, if bought, run `tools/accept_delisted.py` |
| 5 (*long*) | Verdicts about what trades; the desk redeployed | 2.3, 2.4, 2.5, 2.1, then 1.9, 1.10, 1.11, 1.12 | Read and approve INC-02 line by line; deploy `--live` |
| 6 | The reset | 2.2 | Regenerate `book.sexp`, deploy `--live`, lift the halt. Only after INC-02 is approved and Day 2's deploy is live |
| 7 (*long*) | Depth, each as a scored product | 4.4, 4.5, 4.6, 4.7, 4.8, 4.9 | Deploy `--public-only` |
| 8 | The site says what is true; done is checked | 5.2, 5.3, then the whole-branch review | Deploy; run `scripts/check-done.sh` |

Not on this calendar because nothing here can hurry them: the 30 clean days before the raw-bars cut-over (Task 3.2's last step) and the 500 sessions of the pooled test.
