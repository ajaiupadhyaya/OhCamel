# Phase A4: risk depth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the risk engine a second, 250-observation window. On it, build:
- a five-factor model with exposure limits;
- a liquidity view with a liquidity-adjusted VaR;
- GARCH(1,1) and Cornish–Fisher as sibling VaR estimators;
- option marks from Alpaca's indicative snapshots.

Every number is derived by hand in a test, and every identity holds to 1e-9.

**Architecture:** Everything lives in the kernel (`lib/`). The only change to the desk is the forecast rows that A5 will read.
- **Pure modules come first**, each with hand-derived tests: `lib/long_panel.ml`, `lib/factor_model.ml`, `lib/liquidity.ml`, Cornish–Fisher in `lib/risk_metrics.ml`, and `lib/feed/alpaca_options.ml`'s parser.
- **Graph nodes are wired next, so that prices never reach a fit.** Every regression, factor covariance and GARCH fit depends only on long-window cells written by the refresh. Only cheap final products sit on the tick path: exposures, forecasts and liquidation figures.
- **The 60-observation window stays byte-identical**, along with its estimators and every README table they produce.

**Tech Stack:** OCaml 5.2.1, Core/Async, Incremental, Owl (linear algebra and the normal distribution), alcotest, Cohttp_async, Yojson.

**Spec:** `docs/superpowers/specs/2026-09-12-the-desk-design.md`:
- §3.13 (risk depth) and §3.2's A4 rows;
- §6 (testing);
- §8 item 3 (a separate 250-observation window, so the published tables stay true);
- §8 item 8 (the Risk page's factor-model and liquidity sections arrive with A4).

§5's A4 row sets the bar: *accepted when identities hold to 1e-9 and values are hand-derived.*

## Global Constraints

- **The 60-observation window is not touched.**
  - `return_window` stays 60.
  - These compute exactly what they compute today: `returns[S]`, `factor_returns`, `covariance`, `covariance_ewma`, `historical_var`, `expected_shortfall`, `parametric_var`, `parametric_var_ewma`, `attribution` and `var_notional`.
  - The **tables** printed by `make backtest`, `make backtest-crisis`, `make garch`, `make stress` and `make options` are byte-identical to `main`'s. The one exception is the sentence `make garch` prints about wiring (ruling 7), which changes deliberately.
  - `test_validation_report.ml` passes unchanged.
- **Prices never reach a fit.** No node that fits, regresses, estimates a covariance or estimates GARCH parameters may be downstream of `price[S]`, `qty[S]` or `now`. A test pins this for every new fit node, as `test_prices_never_reach_covariance` does today.
- **The kernel may not name the desk.** `grep -rE 'paper-api\.alpaca\.markets|/v2/orders|Sqlite3|ohcamel_desk' lib/` prints nothing. The new feeds talk only to `data.alpaca.markets` and FRED. `lib/` cannot use the desk's `Desk_time`.
- **Hand-derived values beside every numeric assertion.** The derivation goes in a comment above the assertion, as in `test_vol_estimators.ml`. Identities are checked at 1e-9.
- **Hermetic tests.** Payloads are inline strings written from Alpaca's and FRED's documentation. No test touches the network, reads a credential, or starts the scheduler outside `test/desk_async/`.
- **Unknown is not zero.** A figure the engine cannot compute is `None` and renders as a reason, never as 0. This covers too few observations, a singular regression, missing volume, and a contract not yet marked. A limit on such a figure is reported unevaluable.
- **Every node is labelled, costed, typed and on the wire.**
  - Each new name gets a `Node_name` entry, a unit in `unit_of` (using the existing vocabulary: `qty`, not "shares") and a cost class in `cost_of`.
  - Each new **scalar** node also gets its `by_node` entry in `lib/server.ml` **in the same task**, because `test_server.ml`'s every-scalar-node test would otherwise go red.
  - `Graph.fork` copies every new cell and every new construction setting.
  - `observed_roots` and `destroy` stay in step.
- **Topology and scaling pins change deliberately, in the task that changes them.** A task that adds observed nodes updates, in the same commit, and names the changes in its commit message:
  - `test_graph.ml`'s pinned recompute sets, labels (`expected_labels`, "sixty-one"), `expected_observed` ("observed 28"), the fork's "+28", and the count formulas;
  - `test_scaling_probe.ml`'s node count and its per-tick band [24, 28].
  
  Cells that no node reads never appear in the topology, because `walk` starts from observers. Task 13 re-measures the published scaling and bench figures once, at the end.
- **The demo keeps its draws.** `Synthetic_book.seed_returns`'s RNG draw order is load-bearing. Everything the demo seeds for A4 uses its own `Random.State` with its own seed.
- **Every fetch is bounded.** Each new request runs under `Clock_ns.with_timeout` (30 s). A timeout is an `Error`. No startup path awaits a new fetch.
- **Text only, never HTML** for server-supplied strings on a page. Every element lookup returns early when absent.
- **Counts.** 600 main and 30 scheduler tests at the start. Each task bumps `lib/verified.ml` and the three count sentences:
  - `README.md` ("`make test` runs N tests…")
  - `docs/overview.md` ("**N tests**…")
  - `docs/status.md` ("**N hermetic tests**…")

  Tasks that run in parallel worktrees each count from their own base, and the controller reconciles the numbers when merging. Coverage is re-measured once, by the controller, after the whole-branch review.
- **Credentials and commit hygiene.**
  - Never read or print a credential.
  - Stage files by name; never run `git add -A`.
  - Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp`, `.playwright-mcp/`, `.claude/`, `_coverage/` or any `.env`.
  - Every commit message ends with a blank line and exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Scratch files use a task-unique prefix** (`$TMPDIR/a4-taskN-*`).

## Rulings that bind this phase

1. **The long window.** It holds 250 daily returns per name, beside the 60-observation window and never replacing it.
   - **Source:** consolidated (SIP) daily bars with `adjustment=all`. One paginated request covers the book's instruments and the five factor ETFs.
   - **Only final bars.** A bar dated d is used only once `now` is at or after 00:16 UTC on d + 1. That is the research service's rule: the day's bar is complete, and outside SIP's restricted final 15 minutes. A request made during a session would otherwise return today's partial bar.
   - **Refresh:** at startup, then at 00:20, 06:20, 12:20 and 18:20 UTC. That is one schedule; the 00:20 run brings each session's final bar.
   - **Rebuilt, not pushed.** Each refresh is a full, idempotent rebuild, not a push at the roll. Value cutoffs make an unchanged rebuild cost nothing.
2. **The calendar is the factor ETFs'.**
   - **Panel dates:** the dates on which all five factor ETFs (SPY, IWM, IWD, IWF, MTUM) have a final bar. The last 251 are kept, which gives 250 returns.
   - **Instruments align to that calendar separately.** An instrument's return on panel date i is its close on date i divided by its close on date i − 1, minus 1, and only when it has a bar on both dates. Otherwise the observation is `nan`, and it stays `nan`: nothing is carried over a gap.
   - **Per-name counts.** Each instrument has its own observation count. One thin or new name never shortens anyone else's window.
   - **ADV** is each instrument's mean volume over its own last 20 final bars.
   - **ΔDGS10** on date i is L(dᵢ) − L(dᵢ₋₁), where L(d) is the last level FRED published on or before d. A bond-market holiday therefore gives a change of 0 that day, and the next date's change carries the whole move.
   - **Unpublished DGS10.** FRED posts DGS10 a business day late. Panel dates run to the last final ETF bar. Rates is `nan` on the trailing run of dates after the last published level. That is not a holiday, and no zero is invented. Only the factor model drops those rows. The portfolio series behind GARCH and Cornish–Fisher never reads rates.
3. **The factors, as the spec names them, in this fixed order:**
   - market = r(SPY);
   - size = r(IWM) − r(SPY);
   - value = r(IWD) − r(IWF);
   - momentum = r(MTUM) − r(SPY);
   - rates = ΔDGS10 in percentage points.

   The ETFs are fetched over REST and never subscribed on the stream.
4. **Units.**
   - A factor exposure `b_k` is in dollars per 1.00 of that factor, or per 1 pp for rates. The rates units cancel in every variance and matter only for labels and for that factor's limit.
   - Exposures use the graph's dollar exposures, `x_S = qty × price`.
   - Variances are in dollars squared.
   - The model's VaR is `−z × sqrt(V)`: a sibling figure, never the book's VaR.
5. **The regression.**
   - OLS with intercept, one regression per instrument, on that instrument's non-`nan` observations. Solve with Owl's QR, not the normal equations.
   - Refuse, with a reason, in two cases:
     - fewer than **120** observations;
     - σ_min ÷ σ_max of the standardised design matrix below 1e-8, taken from Owl's `svdvals` (columns centred and scaled to unit variance).
   - A row with `nan` in y or in any factor is dropped.
   - Factor covariance: population divisor T (the `Risk_metrics` convention), over the rows where every factor is present.
   - Residual variance: RSS ÷ (Tᵢ − K − 1).
   - **The model assumes uncorrelated residuals.** The page says so. Beside the model it publishes xᵀΣx, from the long window's population covariance on the common rows (ruling 7). The gap between the two is mostly the residual correlation the model does not see. It also reflects the different row sets and divisors, and the page says that too.
6. **Liquidity.**
   - **Participation** is its own book setting, `(liquidity ((participation 0.10) (impact_coefficient 1.0)))`, defaulting to 0.10 and 1.0. It is not the order rule's `max_adv_participation`.
   - **Impact** follows Tóth et al. (2011): cost fraction = Y × σ_daily × sqrt(|q| ÷ ADV), with σ_daily the instrument's long-window population standard deviation.
   - **LVaR** follows Bangia et al. (1999) with the spread-volatility term at 0, because no spread history exists: LVaR = VaR + Σ |x_S| × half_spread_S.
     - The half-spreads come from the book's desk block: `spread_bps`, or `spread_bps_default`, which is 5 bps when unset. That block always exists (`config.ml:185-190`).
     - VaR here is the book's `var_notional`.
     - The page states that impact cost and liquidation horizons beyond one day are left out of LVaR and shown separately.
7. **GARCH(1,1) and Cornish–Fisher on the long window.**
   - **The common rows.** Both run on the panel dates where every instrument with a nonzero frozen weight has an observation. Those rows are selected listwise, not as a suffix.
     - They need at least `long_window − 10`, i.e. 240, of them.
     - GARCH treats those rows as consecutive, and says so.
     - The page states n and how many rows were dropped.
   - **A name the frozen set does not cover.** If an instrument has a nonzero **current** weight but no observation on those rows (bought after the freeze, or new), then `portfolio_returns_long`, the GARCH forecast, Cornish–Fisher and the sample variance are all `None`, naming the instrument. `nan` never reaches a figure or the journal.
   - **Fits are cells, not nodes.**
     - `set_long_panel` runs every fit **outside stabilization** when a panel is written: each instrument's OLS, the factor covariance, the common rows, the asset covariance, and `Garch11.fit` at **frozen weights** (`qty × last_close_long` normalised by gross).
     - It writes the results into cells, and `fork` copies those cells.
     - Only cheap nodes read them. No node fits anything, so neither a fill nor a gate fork refits; a gate fork can be one of 63 subsets.
     - A `Graph.For_testing.fits_run` counter proves it.
     - `bin/main.ml` calls `set_long_panel` again, with the last panel, after the desk's first applied sync. Otherwise the frozen weights would come from the book file for up to six hours.
   - **The GARCH forecast** is recomputed with **current** weights through `forecast_stddev` and the fitted parameters. Variance targeting pins ω to the frozen-weight series while the forecast seeds from the current series. That is a second-order difference, and it is stated.
   - **Cornish–Fisher** is computed at current weights with zero mean, like `portfolio_parametric_var`:
     - z_cf = z + (z² − 1)g₁/6 + (z³ − 3z)g₂/24 − (2z³ − 5z)g₁²/36;
     - g₁ is the skewness m₃ ÷ m₂^1.5, and g₂ the excess kurtosis m₄ ÷ m₂² − 3, both with population moments (divisor n, demeaned);
     - σ is the population standard deviation, the convention `Risk_metrics.covariance` uses;
     - when the expansion is not strictly increasing on z ∈ [−4, 4] (801 points), the figure is `None`, "outside the expansion's valid region".
   - **The `make garch` sentence changes.** The line it prints saying GARCH is "NOT wired into the engine", and every copy of that claim, become true statements: GARCH is wired, on the 250-observation window, beside this finding. The table rows stay byte-identical.
8. **Options: quantities from the book, marks from the indicative feed.**
   - The desk does not trade options. The venue's option positions stay unmanaged.
   - Contracts are declared in the book's `options` block. An expired contract is excluded with a startup warning; it does not block startup.
   - Implied vol comes from `GET https://data.alpaca.markets/v1beta1/options/snapshots?feed=indicative`, polled every 5 minutes in chunks of at most 100 symbols.
   - The engine computes its own Greeks, and publishes them per contract. Alpaca's Greeks are a cross-check column. Units are converted in the renderer and named in the headers: engine vega per 1.00 of vol ÷ 100 = per vol point; engine theta per year ÷ 365 = per calendar day. Confirm Alpaca's own units against its documentation.
   - Every cell says *indicative*.
   - **A contract with no quote yet is unmarked, and a Greek limit whose scope includes an unmarked contract is unevaluable**, never 0. Declaring options also means Greek limits enter the gate's verdicts on stock orders, as they do in `Options_walk`. The docs say so.
9. **The demo.**
   - It seeds a synthetic long panel with known betas from its own RNG. Its rates loadings equal the synthetic book's `rate_beta`.
   - The page labels it synthetic.
   - It declares no options.
   - Figure 1 on the demo gains A4's nodes. That is a deliberate topology change.
10. **The desk's order rule keeps its ADV.** `desk/oms.ml` computes `adv20` from the venue's IEX bars, which understates consolidated volume by an order of magnitude. A4 does not change that rule. The docs name it as a known limit beside the liquidity view, which uses SIP.
11. **The roll's forecasts for A5.** A refresh that advances `long_as_of` to a date d triggers the forecast rows, but only when d already has a session row and no `garch` row. They are written **from a fork, never the live book**:
   - fork the graph;
   - set every quantity and price from the journal's marks for d;
   - write the panel into the fork (`set_long_panel`), take the snapshot, and destroy the fork;
   - write the `garch` and `cornish_fisher` rows for d from that snapshot.

   A panel that arrives late therefore writes exactly the rows an on-time one would. A5 never scores a forecast that has seen its own outcome.
12. **Exact forms and small rules.**
   - Book syntax follows the existing `(kind (Gross_notional 150000.0))` form: `(kind (Factor_exposure (market 2000000.0)))` and `(kind (Greek_limit (gamma 400.0)))`.
   - Every covariance and standard deviation uses the population divisor, as `Risk_metrics` does. Only the regression's residual variance divides by T − K − 1.
   - `Graph.create` validates the liquidity pair, because node bodies may not raise.
   - Every fetch passes Cohttp's `~interrupt` alongside `Clock_ns.with_timeout`, so a timed-out request is cancelled, not abandoned.
   - `option_greeks` are per-share Black–Scholes values. So are Alpaca's, which makes the comparison like for like.

## File Structure

```
lib/types.ml                     MODIFY  Factor.t; Limit.kind gains Factor_exposure
lib/long_panel.ml                NEW     the aligned panel: types and the pure builder (no Graph, no IO)
lib/feed/long_window.ml          NEW     SIP bars + DGS10 fetch -> Long_panel.t; refresh loop taking a callback
lib/factor_model.ml              NEW     OLS (QR), factor covariance, exposures, Euler split -- pure, K from the arrays
lib/liquidity.ml                 NEW     days to liquidate, square-root impact, Bangia LVaR -- pure
lib/risk_metrics.ml              MODIFY  skewness, excess_kurtosis, cornish_fisher_z, _is_monotone, _var
lib/feed/alpaca_options.ml       NEW     option snapshot parser (pure), the store, the poll loop (callback)
lib/feed/feed_source.ml          MODIFY  /api/ops gains the long window's block
lib/graph.ml                     MODIFY  long-window cells; factor, liquidity, GARCH, Cornish-Fisher nodes; options
lib/limits.ml                    MODIFY  Factor_exposure in every exhaustive match
lib/config.ml                    MODIFY  Factor_exposure and Greek_limit in Limit_spec; options and liquidity blocks
lib/synthetic_book.ml            MODIFY  seed_long: the demo's long and factor panels, own RNG
lib/scaling_probe.ml             MODIFY  seeds a long panel, so per-tick costs include A4's tick-path nodes
lib/server.ml                    MODIFY  snapshot fields and by_node entries (added by the task that adds each node)
lib/reports.ml, lib/vol_estimators.ml   MODIFY  comments and the wiring sentence (ruling 7)
desk/session_close.ml            MODIFY  record_long_forecasts ~journal ~date ~snapshot
bin/main.ml                      MODIFY  refresh, options poll, graph creation with options and liquidity, make garch's sentence
web/risk.html, web/risk.js       MODIFY  factor model, liquidity, estimators, options sections
web/graph.js                     MODIFY  remove the "garch -- implemented, not wired in" ghost node
web/argument.html, web/argument.js      MODIFY  the GARCH-not-wired sentence; the hard-coded per-tick figures
book.example.sexp                MODIFY  commented examples: factor_exposure, greek limits, options, liquidity
test/test_long_panel.ml, test_long_window.ml, test_factor_model.ml, test_liquidity.ml,
test/test_alpaca_options.ml      NEW
test/test_risk_metrics.ml, test_graph.ml, test_server.ml, test_embedded_assets.ml, test_scaling_probe.ml,
test/test_properties.ml, test_feed.ml (config parsing lives here), test_desk.ml, test_session_close.ml,
test/test_options_graph.ml       MODIFY
README.md, docs/overview.md, docs/status.md, docs/quant_notes.md, web/quoted.json, Makefile   MODIFY
```

## Order

- **Parallel.** Task 1 (`types.ml` `Factor`, `long_panel.ml`), Task 3 (`factor_model.ml`), Task 5 (`liquidity.ml`) and Task 7a (Cornish–Fisher in `risk_metrics.ml`) share no source files. They may run in parallel worktrees. Each registers its test module in `test/test_ohcamel.ml`, and the controller reconciles those registrations and the counts at merge.
- **Dependencies.**
  - 2a follows 1.
  - 4a follows 2a and 3; 4b follows 4a.
  - 6 follows 5.
  - 7b follows 7a and 4a.
- **Serial.** The graph tasks run one at a time, in this order: 2a, 2b, 4a, 4b, 6, 7b, 9.
- **After the graph tasks:**
  - Task 7c (the "wired" sweep) may run beside Task 8.
  - Task 8 follows 7b.
  - Task 9 follows 8, because both edit `bin/main.ml`.
  - Task 10 follows 9.
  - Task 11 follows 10: the options wire.
  - Task 12 follows 11.
  - Task 13 follows every graph task.
  - Task 14 is last.

---
### Task 1: The `Factor` type and the long panel (pure)

**Files:** Modify `lib/types.ml`. Create `lib/long_panel.ml` and `test/test_long_panel.ml`, registered in `test/test_ohcamel.ml`.

**Interfaces — produces:**
```ocaml
(* lib/types.ml *)
module Factor : sig
  type t = Market | Size | Value | Momentum | Rates [@@deriving sexp, compare, equal, enumerate]
  val to_string : t -> string          (* "market" | "size" | "value" | "momentum" | "rates" *)
  val of_string : string -> t Or_error.t
  val etfs : Symbol.t list             (* SPY; IWM; IWD; IWF; MTUM *)
end

(* lib/long_panel.ml -- no Graph, no IO *)
module Bar : sig type t = { date : Date.t; close : float; volume : float } [@@deriving sexp, equal] end
type t = {
  dates : Date.t array;                 (* T + 1 panel dates, oldest first *)
  returns : float array Symbol.Map.t;   (* instruments; length T; nan where no observation *)
  factors : float array array;          (* length 5, Factor.all order, each length T; only rates may be nan,
                                           and only on its trailing run of unpublished dates *)
  last_close : float option Symbol.Map.t;
  adv20 : float option Symbol.Map.t;    (* mean of the instrument's own last 20 final bars *)
  observations : int Symbol.Map.t;      (* non-nan count per instrument *)
  as_of : Date.t;                       (* the last panel date *)
} [@@deriving sexp]
val final_bars : now:Time_ns.t -> Bar.t list -> Bar.t list
    (* keeps bars dated d with now >= (d + 1) 00:16 UTC -- ruling 1 *)
val build :
  bars:Bar.t list Symbol.Map.t -> dgs10:(Date.t * float) list ->
  instruments:Symbol.t list -> window:int -> now:Time_ns.t -> (t, string) Result.t
```

**What must become true:** rulings 1–3, exactly.
- `build` applies `final_bars` first.
- An ETF with no final bars, fewer than `window + 1` common ETF dates, or no DGS10 level on or before the first panel date is an `Error` naming the problem. An instrument with no bars is **not** an error: its returns are all `nan`, and its observation count is 0.

**Tests (hand-derived; derivations in comments):**
- **`final_bars`.** At `now` = 2026-09-18 22:00 UTC, a bar dated 2026-09-18 is dropped and one dated 2026-09-17 is kept. At 2026-09-19 00:16 UTC, both are kept.
- **A window of 2.** The ETF calendar has four dates, and IWD lacks the second, so the panel dates are dates 1, 3 and 4. An instrument with bars on dates 1, 2 and 4 gets:
  - `nan` on date 3, because it has no bar there;
  - `nan` on date 4 as well. Its previous panel date, 3, has no bar, and nothing is carried over a gap.
  - an observation count of 0.

  The factor values are hand-computed ratios.
- **A bond holiday.** DGS10 lacks the third panel date. The rates change there is 0.0, and the next date's change is the two-day level difference.
- **Unpublished DGS10.** The last DGS10 observation is dated before the last two ETF dates. The panel still runs to the last ETF date, and rates is `nan` on those two dates and nowhere else.
- **ADV.** An instrument with 25 final bars of volume 1..25 gets (6 + … + 25) ÷ 20 = 15.5. With 19 bars it gets `None`.
- **Each `Error`.**

**Commit** as `kernel:`.

### Task 2a: The long window in the graph, and the demo's panel

**Files:** `lib/graph.ml`, `lib/synthetic_book.ml`, `bin/main.ml` (the demo call only), `test/test_graph.ml`, `test/test_synthetic_book.ml`. Use the latter if it exists; otherwise add `test_graph.ml` cases.

**Interfaces:**
- **Consumes:** `Long_panel.t`.
- **Produces, in `Graph`:**
  ```ocaml
  val create : ... -> ?long_window:int (* default 250; kept in t; fork passes it *) -> ...
  val set_long_panel : t -> Long_panel.t -> unit
      (* writes returns_long[S], last_close_long[S], adv20[S], factors_long, long_as_of,
         long_observations[S], and long_fit_weights (qty x last_close, normalised by
         the gross of those products; instruments without a last close get 0).
         Tasks 4a and 7b extend it to run every fit here, outside stabilization,
         and write the results into cells (ruling 7) *)
  val long_as_of : t -> Date.t option
  ```
- **Cells:**
  - per instrument: `returns_long[S] : float array`, `last_close_long[S] : float option`, `adv20[S] : float option`, `long_observations[S] : int`;
  - singletons: `factors_long : float array array`, `long_as_of : Date.t option`, `long_fit_weights : float Symbol.Map.t`.

  Each is built with `make_var` and a value-equality cutoff that treats `nan` as equal to `nan`. `Array.equal Float.equal` would call two identical nan-bearing arrays different, so use a bitwise or `Float.is_nan`-aware comparison.
- **Produces, in `Synthetic_book`:**
  ```ocaml
  val long_seed : int
  val seed_long : graph:Graph.t -> unit
  ```

**What must become true:**
1. **The cells.** They exist for every instrument, and `fork` copies all of them. `classify` lists the new prefixes and singletons, and `unit_of` covers them. Nothing reads the cells yet, so the topology pins do not change. Assert that in a test.
2. **The demo.** `seed_long` is called after `seed_returns`, using its own `Random.State.make [| long_seed |]`. It builds 251 dates and draws the factors as independent normals, with daily standard deviations of 0.010, 0.005, 0.004, 0.006 and 0.05 (the last in pp).
   - Each instrument's return is Σₖ β_{S,k} fₖ + ε, with ε ~ N(0, 0.012²).
   - The market betas are 1.2 (AAPL), 1.1 (MSFT), 1.6 (NVDA), 1.0 (JPM), 0.8 (XOM) and 0.8 (CVX). The size, value and momentum loadings are small fixed numbers in the source.
   - The rates loadings equal the synthetic book's `rate_beta` per sector.
   - Last closes are the book's marks, and ADV comes from a fixed table (AAPL 60e6, MSFT 25e6, NVDA 45e6, JPM 10e6, XOM 15e6, CVX 8e6).
   - `seed_returns`' 60-window returns are identical with or without `seed_long`. Assert it.

**Tests:**
- `set_long_panel` round-trips through the accessors.
- `fork` copies every cell.
- An absent instrument reads all-`nan`, `None` and count 0.
- Writing the same nan-bearing panel twice recomputes nothing.
- `long_fit_weights` is hand-computed for two instruments.
- `seed_long` is deterministic.

**Commit** as `graph:`.

### Task 2b: The live refresh

**Files:**
- Create `lib/feed/long_window.ml` and `test/test_long_window.ml`, registered in `test/test_ohcamel.ml`.
- Modify `lib/feed/feed_source.ml` and `bin/main.ml`.

**Interfaces — produces:**
```ocaml
val bars_of_body : string -> (Long_panel.Bar.t list Symbol.Map.t * string option) Or_error.t
val fetch : credentials:Config.Credentials.t -> instruments:Symbol.t list -> window:int ->
            now:Time_ns.t -> (Long_panel.t, string) Result.t Deferred.t
val run : credentials:Config.Credentials.t -> instruments:Symbol.t list -> window:int ->
          on_panel:(Long_panel.t -> unit) -> on_error:(string -> unit) -> unit Deferred.t
    (* first fetch at once, then at 00:20, 06:20, 12:20 and 18:20 UTC; never awaited by startup *)
```
Check `Config.Credentials.t`'s real name in `lib/config.ml`.

**What must become true:**
1. **`bars_of_body`** parses Alpaca's multi-symbol bars shape: `{"bars":{"SPY":[{"t":"2026-09-17T04:00:00Z","o":…,"h":…,"l":…,"c":…,"v":…}]},"next_page_token":null}`.
   - The date is `t`'s UTC date prefix. Daily bars are stamped 04:00Z or 05:00Z.
   - It refuses a non-finite or non-positive close, or a negative volume, naming the symbol and date.
   - It passes Alpaca's own `{"message":…}` through.
2. **`fetch` requests the bars.**
   - URL: `https://data.alpaca.markets/v2/stocks/bars`.
   - Parameters: `timeframe=1Day`, `adjustment=all`, `feed=sip`, `limit=10000`.
   - `symbols` = the instruments ∪ `Factor.etfs`, deduplicated and sorted.
   - `start` = now − ((window × 2) + 60) days.
   - `end` = now − 16 minutes, in RFC 3339.
   - It follows `next_page_token` until the token is null.
3. **`fetch` gets DGS10 from FRED** with `sort_order=desc` and `limit=(window × 2) + 60`, reusing `Fred_client`'s parsing. Errors use `redacted_uri`.
4. **Each request** runs under `Clock_ns.with_timeout (Time_ns.Span.of_sec 30.)` and passes Cohttp's `~interrupt`, so a timed-out request is cancelled.
5. **`run`** writes each panel through `on_panel`. A failure calls `on_error` and keeps the previous panel.
6. **`bin/main.ml`** starts `run` on the live host with `on_panel` calling `Graph.set_long_panel`, and does not await it.
7. **`feed_source.ml`'s `/api/ops` stats** gain `long_window: { as_of, observations_by_instrument, last_error }`.

**Tests:**
- `bars_of_body` on a payload in the documented shape: two symbols, two bars, with a page token.
- `bars_of_body`'s refusals.
- `fetch`'s URL construction, as a pure `build_uri` function, including `end` = now − 16 min.
- `run`'s schedule, as a pure `next_wakeup ~now` function returning the next of 00:20, 06:20, 12:20 and 18:20 UTC. Test just before, at, and just after 00:20.

**Commit** as `feed:`.

### Task 3: The factor model (pure)

**Files:** Create `lib/factor_model.ml` and `test/test_factor_model.ml`, registered in `test/test_ohcamel.ml`.

**Interfaces — produces:**
```ocaml
val min_observations : int   (* 120 *)
module Fit : sig
  type t = { alpha : float; betas : float array; residual_variance : float; observations : int }
  [@@deriving sexp]
end
val fit_one : y:float array -> factors:float array array -> (Fit.t, string) Result.t
    (* rows where y or any factor is nan are dropped; K = Array.length factors; QR solve;
       Error when observations < min_observations, lengths differ, or
       sigma_min / sigma_max of the standardised design (Owl svdvals) < 1e-8 *)
module For_testing : sig
  val fit_unchecked : y:float array -> factors:float array array -> Fit.t
end
val factor_covariance : factors:float array array -> Owl.Mat.mat
    (* K x K, population divisor, over rows where every factor is present *)
module Risk : sig
  type t = {
    exposures : float array; systematic_variance : float; idiosyncratic_variance : float;
    total_variance : float; contributions : float array; model_var : float;
    sample_variance : float option;   (* x' Sigma x on the common tail, when given *)
  } [@@deriving sexp]
end
val risk :
  fits:Fit.t option array -> exposures:float array -> covariance:Owl.Mat.mat ->
  ?asset_covariance:Owl.Mat.mat -> confidence:float -> unit -> (Risk.t, string) Result.t
```
The module does not depend on `Types.Factor`. K comes from the arrays.

**What must become true:** rulings 4 and 5.
- The contributions are b_k × (Σ_f b)_k.
- `model_var` = −z × sqrt(total), where z = `Risk_metrics.normal_ppf ~p:(1 − confidence)`.
- A nonzero exposure without a fit is an `Error` naming its index. The caller maps the index to a symbol.

**Tests (hand-derived):**
- **One factor, via `fit_unchecked`.**
  - Data: f = [0.01; 0.02; 0.03; 0.04], y = [0.01; 0.03; 0.02; 0.04].
  - Deviations: f −0.015, −0.005, 0.005, 0.015; y −0.015, 0.005, −0.005, 0.015.
  - Σ dev_f·dev_y = 4e-4 and Σ dev_f² = 5e-4, so β = 0.8 and α = 0.005.
  - Residuals: −0.003, 0.009, −0.009, 0.003. RSS = 1.8e-4.
  - Residual variance = 1.8e-4 ÷ (4 − 1 − 1) = 9e-5.
  - The public `fit_one` on the same data returns the minimum-observations error.
- **Identities at 1e-9**, on a 256 × 5 case whose factor columns are Walsh–Hadamard columns (mutually orthogonal, each orthogonal to the intercept) scaled to realistic daily sizes, and whose y is Σ βₖ fₖ plus a further Walsh–Hadamard column as the residual, so every beta is recovered **exactly**:
  - residuals are orthogonal to the intercept and to every factor;
  - fitted plus residual equals y;
  - the contributions sum to the systematic variance;
  - systematic plus idiosyncratic equals the total.
- **`nan` rows are dropped.** A y with 10 `nan`s, and 3 trailing `nan` rates, gives `observations` = T − 13 and the same betas as the clean subset. `factor_covariance` drops the same 3 rates rows.
- **Refusals:**
  - a duplicated factor column (the rcond check);
  - lengths that differ;
  - a nonzero exposure with no fit.
- **The risk hand case.** Two assets and one factor, with betas 1.0 and 0.5, residual variances 1e-4 and 4e-4, x = (1e6, 2e6) and var(f) = 2e-4.
  - b = 2e6.
  - Systematic = 4e12 × 2e-4 = 8e8.
  - Idiosyncratic = 1e12 × 1e-4 + 4e12 × 4e-4 = 1.7e9.
  - Total = 2.5e9.
  - At 99%, model_var = 2.3263478740 × 50,000 = 116,317.39.
  - With `asset_covariance` = [[3e-4, 1.5e-4], [1.5e-4, 6e-4]]: sample_variance = 1e12 × 3e-4 + 2 × 2e12 × 1.5e-4 + 4e12 × 6e-4 = 3e8 + 6e8 + 2.4e9 = 3.3e9.

**Commit** as `kernel:`.

### Task 4a: The factor model in the graph

**Files:** `lib/graph.ml`, `lib/server.ml` (this task's `by_node` entries and snapshot fields), `test/test_graph.ml`, `test/test_server.ml`.

**Interfaces:**
- **Consumes:** `Factor_model.*` and Task 2a's cells.
- **Produces:**
  - `set_long_panel` gains these computations, run outside stabilization (ruling 7):
    - each instrument's `Factor_model.fit_one` → cell `factor_fit_long[S] : (Fit.t, string) Result.t`;
    - `factor_covariance` → cell `factor_cov_long`;
    - the common rows → cell `common_rows : (int array, string) Result.t`, the panel indices, with an error when there are fewer than `long_window − 10`;
    - the population covariance of the frozen-weight names on those rows → cell `asset_cov_long`.

    Every fit it runs increments `Graph.For_testing.fits_run`.
  - Nodes, all reading cells:
    - `factor_risk` (tick path), from `factor_fit_long[S]`, `factor_cov_long`, `asset_cov_long` and `exposure:S`;
    - `factor_exposure:<factor>`, one `Notional.t option` per factor.

    `factor_risk` and `sample_variance` are `None` when a name with a nonzero current weight has no fit, or has no column in `asset_cov_long`, and they name the name (ruling 7).
  - Snapshot fields: `factor_exposures`, `factor_contributions`, `systematic_variance`, `idiosyncratic_variance`, `factor_model_var`, `factor_sample_variance`, `factor_fit_errors : (Symbol.t * string) list`, `long_as_of`, `common_rows_count`, `common_rows_dropped`.

**What must become true:**
- The new per-instrument prefixes are in `classify`.
- `fork` copies every new cell.
- `fits_run` stays at 0 across a tick, a fill, a `fork`, and a six-leg `Gate.check`. Assert that in a test.
- Every pin this task moves is updated: labels, observed, formulas, recompute sets, the fork increment, and the scaling probe's count and band. The commit message lists them.

**Tests:**
- **A Walsh–Hadamard panel** (T = 256, `long_window = 256`):
  - Leave out column 0.
  - Build each instrument's returns as α·1 + Σ βₖfₖ + a further Walsh–Hadamard column, so each α and β is known exactly.
  - Check:
    - the exposures equal Σ β × x;
    - the contributions sum to systematic variance (1e-9);
    - fewer than 120 observations puts the name in `factor_fit_errors`;
    - a name bought after the freeze makes `factor_risk` `None`, naming it;
    - the JSON values.
- **The `fits_run` test** above.

**Commit** as `graph:`, listing the pins.

### Task 4b: The `Factor_exposure` limit

**Files:** `lib/types.ml`, `lib/limits.ml`, `lib/graph.ml` (`breach_node`, `limit_kind_to_string`), `lib/config.ml`, `book.example.sexp` (a commented example). Tests go in `test/test_properties.ml`, `test/test_gate.ml`, and the config-parsing test file (`test_feed.ml` ~682 or `test_desk.ml`).

**Interfaces — produces:**
- `Types.Limit.kind` gains `Factor_exposure of Factor.t * Notional.t`: |bₖ| may not exceed the threshold, at Portfolio scope only.
- `Config.Book.Limit_spec.kind` gains `Factor_exposure of (string * float)`, written `(kind (Factor_exposure (market 2000000.0)))`.

**What must become true:** every place that matches on a kind handles the new one.
- `Limits`: the threshold, `unit_of` = Money, `render_value`, and `scope_is_valid` (Portfolio only).
- `validate`: the threshold must be positive.
- `breach_node` reads `factor_exposure:<f>`. `None` means unevaluable.
- `limit_kind_to_string` returns `"factor_exposure"`.
- `test_properties.ml`'s generators include the new kind.
- Config validates the factor name with `Factor.of_string`.

**Tests:**
- The limit breaches exactly when |b| exceeds the threshold, and is unevaluable exactly when b is `None`.
- The gate refuses a fill that would take `market` over its line on a fork.
- The config round-trips.
- The properties hold.

**Commit** as `kernel:`.

### Task 5: Liquidity (pure)

**Files:** Create `lib/liquidity.ml` and `test/test_liquidity.ml`, registered.

**Interfaces — produces:**
```ocaml
type position = { symbol : Symbol.t; qty : float; price : float; adv20 : float option;
                  daily_stddev : float option; half_spread_bps : float }
module Line : sig
  type t = { symbol : Symbol.t; days_to_liquidate : float option; impact_fraction : float option;
             impact_cost : float option; spread_cost : float } [@@deriving sexp]
end
type t = { lines : Line.t list; spread_cost : float; impact_cost : float option;
           max_days_to_liquidate : float option } [@@deriving sexp]
val compute : participation:float -> impact_coefficient:float -> position list -> t
val lvar : var:float -> t -> float           (* var + spread_cost *)
```
- A position with quantity 0 contributes zeros.
- A nonzero position with no ADV or no σ has `None` for its own days, impact fraction and impact cost. It also makes the **totals** `None`, because unknown is not zero.
- `participation` must lie in (0, 1], and `impact_coefficient` must be ≥ 0. Anything else raises `Invalid_argument`.

**Tests (hand-derived):**
- **One position:** q = 10,000 at 100; ADV 1,000,000; participation 0.10; σ 0.02; Y 1.0; 5 bps half-spread.
  - days = 10,000 ÷ 100,000 = 0.1
  - impact fraction = 0.02 × sqrt(0.01) = 0.002
  - impact cost = 2,000
  - spread cost = 1,000,000 × 5e-4 = 500
  - with VaR 50,000, LVaR = 50,500
- **A short position** gives the same magnitudes.
- **Identities:**
  - with zero spreads, LVaR equals VaR exactly;
  - LVaR ≥ VaR;
  - the totals equal the sums of the lines (1e-9).
- **Missing ADV:** on a nonzero position the totals are `None`; on a zero position they are not.
- **Refused arguments** raise `Invalid_argument`.

**Commit** as `kernel:`.

### Task 6: Liquidity in the graph

**Files:** `lib/graph.ml`, `lib/config.ml`, `lib/server.ml` (this task's `by_node` entries and snapshot fields), `bin/main.ml`, `book.example.sexp` (commented), and tests in `test/test_graph.ml`, `test/test_server.ml`, and the config-parsing test file.

**Interfaces:**
- **Consumes:** `Liquidity.compute` and `lvar`; the `adv20[S]` and `returns_long[S]` cells; `var_notional`.
- **Produces:**
  - `Graph.create` gains two parameters, both kept in `t` and passed by `fork`:
    - `?liquidity:(float * float)`, the participation and impact coefficient, default `(0.10, 1.0)`;
    - `?half_spread_bps:(Symbol.t -> float)`, default `fun _ -> 0.0`.
  - `bin/main.ml` passes both hosts the book's desk block half-spreads (`spread_bps`, then `spread_bps_default`) and the book's liquidity block.
  - Nodes:
    - `daily_stddev_long:S`, the population σ of the non-`nan` long returns. It is `None` below 20 observations and depends on the long cells only.
    - `liquidity : Liquidity.t`, on the tick path.
    - `lvar_notional : Notional.t option`.
  - `Config.Book` gains an optional `liquidity` block with validation.

**What must become true:**
- `fork` carries the two new settings. Test it: a fork's `lvar_notional` equals its parent's.
- Prices never reach `daily_stddev_long`. Extend the pinned test.
- Pins move deliberately, and the commit message lists them.

**Tests (hand-derived):**
- A graph holding Task 5's single position reproduces Task 5's figures to 1e-9. Its `lvar_notional` equals `var_notional` + 500.
- With zero half-spreads, `lvar_notional` equals `var_notional`.
- A position with no ADV makes the totals `None`.
- The config block parses, validates and falls back to its defaults.

**Commit** as `graph:`, listing the pins.

### Task 7a: Cornish–Fisher (pure)

**Files:** `lib/risk_metrics.ml`, `test/test_risk_metrics.ml`.

**Interfaces — produces:**
```ocaml
val skewness : float array -> float           (* g1 = m3 / m2^1.5, population moments *)
val excess_kurtosis : float array -> float    (* g2 = m4 / m2^2 - 3 *)
val cornish_fisher_z : z:float -> skew:float -> excess_kurtosis:float -> float
val cornish_fisher_is_monotone : skew:float -> excess_kurtosis:float -> bool
val cornish_fisher_var : returns:float array -> confidence:float -> (float, string) Result.t
    (* zero mean; -(z_cf x population sigma); Error below 120 observations, when flat,
       or when not monotone on [-4, 4] *)
```

**Tests (hand-derived):**
- **Moments of [1; 2; 3; 10].**
  - Mean 4; deviations −3, −2, −1, 6.
  - m₂ = 50 ÷ 4 = 12.5; m₃ = 180 ÷ 4 = 45; m₄ = 1394 ÷ 4 = 348.5.
  - g₁ = 45 ÷ 12.5^1.5 = 45 ÷ 44.19417 = 1.018234.
  - g₂ = 348.5 ÷ 156.25 − 3 = −0.7696.
- **The expansion.**
  - With g₁ = g₂ = 0, `cornish_fisher_z` returns z exactly.
  - At z = −2.3263478740, g₁ = −0.5 and g₂ = 3, the terms are:
    - (z² − 1)g₁/6 = −0.367658
    - (z³ − 3z)g₂/24 = −0.701363
    - −(2z³ − 5z)g₁²/36 = +0.094084
  - So z_cf = −3.301284. Derive each term in the comment and assert to 1e-6 against these values, and to 1e-12 against the formula.
- **VaR against the parametric estimator.** On a symmetric, mesokurtic series (constructed so that g₁ = 0 and g₂ = 0 exactly), `cornish_fisher_var` equals `-(z × population σ)` to 1e-12.
- **Refusals.** g₁ = 3, g₂ = 0 is not monotone, and gives `Error`.

**Commit** as `kernel:`.

### Task 7b: GARCH and Cornish–Fisher wired on the long window

**Files:** `lib/graph.ml`, `lib/server.ml` (this task's `by_node` entries and fields), `test/test_graph.ml`, `test/test_server.ml`.

**Interfaces — produces:**
- **`set_long_panel` also fits GARCH, outside stabilization.** It runs `Garch11.fit` on the common rows at frozen weights and writes the result into the cell `garch_params_long : (Garch11.t, string) Result.t`, catching any raise. The cell is an `Error` when there are fewer than `long_window − 10` common rows or the series is flat. The fit increments `fits_run`.
- **Nodes:**
  - `portfolio_returns_long` (tick path): the common rows at current weights. It is `None`, naming the instrument, when a nonzero current weight lacks a column.
  - `garch_var`: `−z × forecast_stddev ~returns:portfolio_returns_long params`, a fraction of gross; and `garch_var_notional`.
  - `cornish_fisher_var` (a fraction), `cornish_fisher_var_notional` and `cornish_fisher_moments`.
- **Snapshot fields:** the nodes above, plus `garch_params : (omega * alpha * beta) option` and `long_estimator_errors : (string * string) list`.

**What must become true:**
- Rulings 5 and 7.
- `fits_run` stays 0 across a tick, a fill, a fork and a six-leg `Gate.check`.
- Every pin that moves is updated, and the commit lists them.

**Tests:**
- `garch_params_long` equals `Garch11.fit` on the hand-built frozen-weight series over the common rows.
- `garch_var` equals `−z ×` `forecast_stddev` on the current-weight series.
- `cornish_fisher_var` equals Task 7a's function on that series.
- Below 240 common rows, both figures are `None` with a reason.
- A name bought after the freeze makes both `None`, naming it.
- A tick changes `garch_var`, and `fits_run` stays 0.

**Commit** as `graph:`, listing the pins.

### Task 7c: Every "GARCH is not wired" becomes true

**Files:** comments and sentences only, in these places. Grep for "not wired" and "NOT wired" and fix every hit.
- `lib/vol_estimators.ml`
- `lib/reports.ml`
- `lib/garch_study.ml` (~7)
- `bin/main.ml`: `run_garch` at ~662–671, ~690 and ~734–741, and the CLI help line at ~1896
- `Makefile`: the `garch` target's comment
- `web/graph.js`: the ghost node "garch — implemented, not wired in", and the legend entry at ~780 ("present, and not wired in")
- `web/argument.html` (~110)
- `README.md` (~615–618, 644–647, 1672–1676)
- `docs/status.md` (~140, 393)
- `docs/quant_notes.md` (~168)
- `web/quoted.json`'s garch block, re-captured from the new `make garch` output
- `test/test_embedded_assets.ml`, if the ghost node or the legend is pinned

**What must become true:**
- Every claim says GARCH is wired: on the 250-observation window, beside this finding. The 60-observation window is still not used for it, and the claim says why.
- `make garch`'s **table rows** are byte-identical. Diff them against `main`'s, built in a scratch worktree with the main switch.

**Commit** as `docs:`.

### Task 8: The forecasts A5 will read

**Files:** `desk/session_close.ml`, `bin/main.ml` (the live `on_panel` callback), `test/test_session_close.ml`.

**Interfaces — produces:**
```ocaml
val record_long_forecasts :
  journal:Journal.t -> graph:Graph.t -> panel:Long_panel.t -> date:Date.t -> unit Or_error.t
    (* ruling 11: fork [graph]; set every qty and price from the journal's marks for [date];
       set_long_panel on the fork; snapshot; destroy the fork; write "garch" and
       "cornish_fisher" Forecast rows for [date] when each figure is Some; a no-op for an
       estimator whose row exists; an Error when [date] has no session row or no marks *)
```

**What must become true:**
- The live `on_panel` callback first writes the panel into the live graph.
- Then, if the panel's `as_of` is a date d that has a session row and no `garch` row, it calls `record_long_forecasts`.
- The existing roll still writes exactly its three rows.
- A comment says that A5 reads these rows.

**Tests:**
- A panel for a recorded session writes two rows, so that date holds five in all.
- A panel arriving late, after the live book has moved on, writes rows identical to the on-time case. Assert this by building both.
- A date without a session row writes nothing.
- A second call is a no-op.
- `None` figures write no row.

**Commit** as `desk:`.

### Task 9: Options declared in the book; Greek limits that parse; unmarked is unevaluable

**Files:**
- `lib/config.ml`
- `lib/graph.ml`: the marked state, and the Greek breach rule
- `lib/server.ml`: per-contract engine Greeks on the wire
- `bin/main.ml`
- `book.example.sexp` (commented)
- Tests: `test/test_options_graph.ml`, `test/test_server.ml`, and the config-parsing test file

**Interfaces — produces:**
```ocaml
module Option_spec : sig
  type t = { id : string; underlying : string; strike : float; right : [ `Call | `Put ];
             expiry : Date.t; contracts : float; multiplier : float (* default 100 *) }
end
(* Config.Book.options : Option_spec.t list, from:
   (options (((id AAPL261218C00200000) (underlying AAPL) (strike 200)
              (right call) (expiry 2026-12-18) (contracts -5))))            *)
(* Config.Book.Limit_spec.kind gains Greek_limit of (string * float): (kind (Greek_limit (gamma 400.0))) *)
```
- `Graph` gains a per-contract cell `marked[o] : bool`, false until the first `set_implied_vol` for that contract.
- `Greek_limit`'s breach node returns `None` (unevaluable) while any contract in its scope is unmarked.
- The snapshot gains per-contract engine Greeks, `option_greeks : (id * delta * gamma * vega * theta * marked) list`, in the engine's own units, and the wire carries them.

**What must become true:**
1. **Validation.**
   - The underlying is in the universe.
   - The OCC id (`ROOT` padded, then `YYMMDD`, then `C` or `P`, then the strike × 1000 as 8 digits) agrees with every field, and any disagreement is named.
   - The strike and multiplier are positive, and `contracts` is nonzero and finite.
   - Ids are unique.
   - **An expired contract is excluded with a startup warning.** It does not block startup.
2. **Graph and startup.**
   - The graph is built with `Options.Position.t`s. `expiry_in_days` is counted from today, and the quantities are written with `set_contracts`.
   - `valuation_days` advances daily from a timer in `bin/main.ml`.
   - The live startup line reads `options: N contracts declared, marks from Alpaca's indicative feed`, or `options: none declared`.
3. **The existing option tests pass unchanged:** `test_options_graph.ml` and `Options_walk`. Their contracts get `set_implied_vol` before any Greek limit is read, so they are marked. Check this, and say so.
4. **The demo declares none.**

**Tests:**
- Parse round-trip.
- Each refusal, including an OCC/strike disagreement.
- An expired contract excluded with a warning.
- A Greek limit is unevaluable while a contract is unmarked and evaluates once it is marked.
- A `greek` limit parses and breaches on a fork.
- The per-contract Greeks appear on the wire, in the engine's units.

**Commit** as `config:`.

### Task 10: Indicative option marks

**Files:** Create `lib/feed/alpaca_options.ml` and `test/test_alpaca_options.ml` (registered). Modify `bin/main.ml`.

**Interfaces — produces:**
```ocaml
module Quote : sig
  type t = { id : string; implied_vol : float option; delta : float option; gamma : float option;
             vega : float option; theta : float option; bid : float option; ask : float option;
             as_of : Time_ns.t option } [@@deriving sexp]
end
val quotes_of_body : string -> (Quote.t list * string option) Or_error.t
module Store : sig
  type t
  val create : unit -> t
  val update : t -> now:Time_ns.t -> Quote.t list -> unit
  val to_json : t -> Yojson.Safe.t   (* per contract: Alpaca's IV and greeks, quote time, "indicative" *)
end
val run : credentials:Config.Credentials.t -> ids:string list -> every:Time_ns.Span.t ->
          on_quotes:(Quote.t list -> unit) -> on_error:(string -> unit) -> unit Deferred.t
```
`run` takes a callback, never a `Graph.t`. The caller in `bin/main.ml` writes the quotes into the graph.

**What must become true:**
1. **The parser.** Before writing it, read Alpaca's documentation for `/v1beta1/options/snapshots` (the Alpaca docs tools, or WebFetch; never an account or trading tool). Confirm the field names and the units of its Greeks and theta, and record them in the module's header comment.
   - The expected shape is `{"snapshots":{"<occ>":{"latestQuote":{…},"latestTrade":{…},"impliedVolatility":…,"greeks":{…}}},"next_page_token":…}`.
   - A missing IV or missing Greeks is `None`, not an error.
   - A non-finite or negative IV is refused for that contract only.
   - Pagination is followed. Symbols go in chunks of at most 100.
   - Every request runs under `Clock_ns.with_timeout`.
2. **The poll.** It runs every 5 minutes with `feed=indicative`, and startup does not await it.
   - The callback calls `Graph.set_implied_vol` for each quote with an IV. That marks the contract.
   - The callback also updates the store.
   - The store's JSON joins the frame through `?frame_extra` under `options_indicative`. `lib/server.ml` gains no knowledge of the feed.
3. **The engine's Greeks stay the graph's own.** Alpaca's are cross-check only.

**Tests:**
- A two-contract payload in the documented shape: one full, one with no IV and no greeks.
- Pagination.
- A negative IV refused for one contract, the other still accepted.
- The store's JSON shape.
- The graph path: writing IV = 0.20 for Hull's case (S = 49, K = 50, r = 0.05, T = 0.3846) marks the contract, and the engine's call delta is 0.522 at 5e-3, as in `test_options.ml`.

**Commit** as `feed:`.

### Task 11: The options wire, finished

**Files:** `lib/server.ml`, `test/test_server.ml`.

Every scalar and snapshot field from Tasks 4, 6, 7b and 9 is already on the wire, because each task added its own. This task adds the rest and proves the whole.

**What must become true:**
1. Frames carry `options_indicative` from Task 10's `frame_extra`, next to `option_greeks` from Task 9.
2. **Warming up.** With no long panel and no quotes, every new estimator field is `null` and its reason is listed. That covers the factor model, liquidity totals, LVaR, GARCH, Cornish–Fisher and the Greek limits.
3. **The required-key list** in `test_round_trips` covers every A4 key.
4. **Values survive the JSON round trip.** A graph with a known panel emits the values Tasks 4, 6 and 7b derived, to 1e-9.

**Commit** as `server:`.

### Task 12: The Risk page

**Files:**
- `web/risk.html`, `web/risk.js`
- `web/page.css`, only if a new class is needed
- Tests: `test/test_embedded_assets.ml`, `test/test_server.ml` (the page markers), and `deploy/smoke.sh` only if a probe checks a marker

**What must become true:** four sections sit after `greeks` and before `stress`, in this order.

1. **"factor model — 250 sessions, beside the 60-session VaR"**
   - For each factor: its exposure (in dollars per 1.00, or per 1 pp for rates), its contribution and its share.
   - Systematic and idiosyncratic variance.
   - The model's VaR, labelled *a model, not the book's VaR — residuals assumed uncorrelated*, with the sample-covariance variance beside it.
   - `as_of`, and the common-tail length.
   - The names without a fit, with their reasons.
   - On the demo, the word *synthetic*.
2. **"liquidity — at N% of 20-day consolidated volume"**
   - Per name: days to liquidate, impact fraction, impact cost and spread cost.
   - The totals.
   - VaR next to LVaR, with this label: *Bangia (1999): spread volatility not measured (term 0); impact and multi-day horizons not included — shown above*.
   - The spreads' source, which is the desk block.
3. **"estimators — parametric siblings"**
   - Parametric (60), EWMA (60), GARCH(1,1) (250), and Cornish–Fisher (250) with its skew and excess kurtosis.
   - Beside GARCH, `make garch`'s n = 250 row, read from `/api/reports/garch`.
   - Each unavailable estimator shows its reason.
4. **"options — indicative"**
   - Per contract: the engine's Greeks next to Alpaca's, with units converted in the renderer and named in each header (vega per vol point, theta per day), the IV, and the quote time.
   - Every numeric column header says *indicative*.
   - An unmarked contract reads "no indicative quote yet — Greek limits unevaluable".
   - With none declared: "no contracts declared in the book".

**Rules for all four:**
- The page computes nothing except unit conversion. That conversion is a named, commented function.
- Everything is inserted as text through `F.el`/`textContent`.
- Every lookup is guarded.
- Both colour schemes work, at 380 px, with no horizontal page scroll.
- The old `risk.html` comment "A4 adds the factor model and liquidity…" is replaced.

**Tests:**
- The marker-order pin gains the four ids.
- A grep proves no `innerHTML` with data.
- A visual check on the demo in the built-in browser, in both schemes and at 380 px. Put the notes in the report.

**Commit** as `web:`.

### Task 13: The published performance figures

**Files:**
- `README.md`: the scaling row (~55–60), the bench table and prose (~64–107, "about twenty-five nodes")
- `docs/status.md` (~336–349)
- `web/argument.js` (~81, the hard-coded "25.6 / 25.2 / 26.0")
- `web/quoted.json`: the `scaling` block
- `lib/scaling_probe.ml` and the bench's setup: seed a long panel, so tick-path A4 nodes are not `None`
- `test/test_scaling_probe.ml`
- `test/test_embedded_assets.ml` (~313, which pins 1272 nodes at 400 names)
- `bench/bench_graph.ml` (the bench's setup)

**What must become true:**
1. **Seeding.** The scaling probe and `make bench` seed a synthetic long panel for every name, deterministically, so they measure A4's tick-path cost.
2. **Re-measure and update.**
   - Re-run `make run` and update the README's scaling row and `quoted.json`.
   - Re-run `make bench` and update the bench table and its prose.
   - Update `status.md` and `argument.js`.
   - Make `test_scaling_probe.ml`'s pins match.
3. **Figure 1** draws A4's nodes. Confirm it in the browser on the demo at 1280 px and 380 px: the new nodes are placed and labelled, with no overlap.
4. **Byte-identical tables.** Re-run `make backtest`, `make backtest-crisis`, `make garch`, `make stress` and `make options`, and diff each against `main`'s, built in a scratch worktree with the main switch. Their tables must match byte for byte. The only permitted difference is `make garch`'s wiring sentence, from Task 7b. Report any other difference and stop.

**Commit** as `docs:` for the prose, and as `test:` for the pins. Two commits is fine.

### Task 14: The documents

**Files:** `README.md`, `docs/overview.md`, `docs/status.md`, `book.example.sexp` (comments only), and `lib/verified.ml` (counts only).

**What must become true:**
1. **README gains a "Risk depth" section** after the attribution material and before stress. It covers:
   - the long window and why it is separate;
   - the calendar rule;
   - the factor model, with a hand example copied from Task 3's tests;
   - liquidity, with Task 5's example;
   - GARCH on 250, beside its own finding;
   - Cornish–Fisher, with Task 7a's example and the validity check;
   - indicative option marks.
2. **README's limits section gains:**
   - SIP volume in the liquidity view versus IEX volume in the order rule (ruling 10);
   - indicative option marks;
   - option quantities come from the book;
   - Greek limits enter the gate once options are declared;
   - the factor ETFs are fetched over REST and not streamed;
   - the long window refreshes on its own schedule, with only final bars;
   - uncorrelated residuals in the factor model;
   - Bangia's spread-volatility term is 0.
3. **`docs/status.md`:**
   - the inventory gains every new module and feed;
   - "Next" drops A4, and keeps A5 and A6;
   - the acceptance row "identities to 1e-9; hand-derived values" is recorded as met, naming the tests;
   - an A4 row goes into "How it got here", with the placeholder `(merged at <sha>, coverage re-measured)`.
4. **`docs/overview.md`** says what the Risk page now shows.
5. **`book.example.sexp`'s comments** list every limit kind, including `factor_exposure` and `greek`, and the `options` and `liquidity` blocks.

**Commit** as `docs:`.

---

## Acceptance

1. `make build` is clean. `make test` is green at the counts `lib/verified.ml` states. `make research-test` is green. `dune build @fmt` is clean. The three greps are silent.
2. **Identities to 1e-9**, each asserted by a named test:
   - OLS residuals are orthogonal to every regressor.
   - Euler factor contributions sum to the systematic variance.
   - Systematic plus idiosyncratic variance equals the total.
   - LVaR equals VaR at zero spread.
   - Cornish–Fisher equals the normal quantile when g₁ = g₂ = 0.
   - Liquidity totals equal the sums of their lines.
3. **Hand-derived values** sit beside every numeric assertion in `test_long_panel.ml`, `test_factor_model.ml`, `test_liquidity.ml`, `test_long_window.ml`, `test_alpaca_options.ml` and the new `test_risk_metrics.ml` cases.
4. **No fit runs on the tick path, on a fill, in a fork or in a gate check.**
   - Fits run only in `set_long_panel`.
   - `Graph.For_testing.fits_run` stays at 0 across a tick, a fill, a fork and a six-leg `Gate.check`.
   - Prices never reach `daily_stddev_long`.
5. **The backtest, crisis, GARCH, stress and options tables** are byte-identical to `main`'s. `test_validation_report.ml` is unchanged.
6. **The Risk page** shows the four new sections on the demo, in both schemes and at 380 px.
   - On the live host, the factor model, liquidity and estimators fill after the first long-panel fetch whose bars are final.
   - Options fill only for declared contracts.
   - The live half is the owner's to see after deploy, and the final report says so.
