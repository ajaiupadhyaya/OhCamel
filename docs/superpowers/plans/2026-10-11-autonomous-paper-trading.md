# Autonomous Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desk trades the paper account on its own every session — every built strategy sized under a labelled incubation tier, a volatility-targeting overlay re-sizing it through the day, intraday pre-registered strategies on minute bars, the live record scored with the charter's statistics, and stop rules the owner wrote before the first order.

**Architecture:** Python jobs in `ohcamel-worker` produce signal and overlay documents into the shared `signals` volume; the OCaml desk judges them against its own clocks, sizes them under the book's caps, and sends every order through the unchanged rules → gate → journal → wire path, now in two modes (market-on-open, and intraday limit waves). The research battery is extended to session-level series so minute-bar hypotheses are held to the same gates.

**Tech Stack:** OCaml (Core, Async, Incremental, Alcotest, SQLite via the journal), Python 3.13 (pandas, DuckDB, the Quant job system), Alpaca paper REST + IEX websocket, Docker Compose on the droplet.

**Spec:** `docs/superpowers/specs/2026-10-11-autonomous-paper-trading-design.md` — read it first; every task below cites its section.

**Ledger:** `.superpowers/sdd/2026-10-11-autonomous-paper-trading/progress.md` (gitignored). Branches: one per stage, `auto/s0-governance` … `auto/s5-unattended`, each from `main` after the previous stage merges. Conventions: `.superpowers/sdd/conventions.md` and `implementer-howto.md` bind every task (opam switch, `dune build @fmt`, hermetic tests, `lib/verified.ml` counts bumped in the same commit, never `git add -A`, the owner's three untracked root files never touched). Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Global Constraints

- **No gate is loosened and no validation block is rewritten.** `research/.../battery/gates.py` thresholds, `quant/.../models/verdict.py` constants and every committed manifest are byte-identical at the end of every stage; the desk copies a document's `validation` into the journal unchanged (spec invariant 15).
- **Nothing promotes to `live`.** No task writes `(sizing live)` anywhere, and no code path sets it (spec §2 D2, §11 step 6).
- **Paper only, twice.** The trading host stays the compiled-in constant (invariant 9); incubation sizing additionally refuses a trade half whose `name` is neither `alpaca-paper` nor `sim` (invariant 14).
- **Every INCUBATION, STOPPED and OVERLAY STALE label is a literal string** pinned by `test/test_embedded_assets.ml` in the built page, and `/api/research` carries the same word in `tier`.
- **`Config.universe_cap = 64`, `Config.stream_cap = 30`** (spec D7). The stream subscribes to at most `stream_cap` names; every other name is polled.
- **Journal changes are additive** (`CREATE TABLE/INDEX IF NOT EXISTS`, `schema_version` stays 1). A task that believes it needs a bump stops and reports.
- **The kernel may not name the desk and writes no file.** `grep -rE 'paper-api\.alpaca\.markets|/v2/orders|Sqlite3|ohcamel_desk' lib/` prints nothing after every task. The new poller talks only to `data.alpaca.markets`.
- **`lib/` has no interface files and this plan adds none.** New desk modules ship their own `.mli` (`overlay`, `waves`, `record`, `stop`, `shadow`, `validation`, `replay`).
- **Every fetch is bounded**: `Clock_ns.with_timeout` (30 s) with Cohttp's `~interrupt`; a timeout is an `Error`; no startup path awaits a new fetch.
- **Unknown is `None`**, rendered as a reason, never zero.
- **Text only, never HTML**, for server-supplied strings on a page.
- **Hermetic tests with hand-derived values**; the derivation beside the assertion. Python tests run offline against the fixture warehouse (`OHCAMEL_QUANT_OFFLINE=1`).
- **Deploys only outside 09:25–16:10 New York**; `--live` after `--public-only` smoke is clean; the droplet's `book.sexp` is never edited by a task.
- **Scratch files** use `$TMPDIR/auto-taskN-*`; nothing scratch is committed.

## Review Focus

1. **An overlay document with `exposure_scale` 0** flattens every incubated strategy within a minute. It is honoured (a producer that says zero means zero) but the pass journals an alert kind `overlay_zero` and the page shows the reason — Task 11's test `overlay zero flattens and alerts`.
2. **A NaN, infinite or missing `exposure_scale`, weight, `as_of` or `sequence`** is refused, never coerced — Task 8 (`O4 refuses NaN`), Task 22 (`R7 on a v2 NaN weight`), written as `not (x <= bound)` as `contract.ml` does.
3. **A half-day session** (venue `next_close` 13:00) reaches `flatten_at` 15:50 after the close: the `session` rule refuses the flatten, the position carries overnight, and the journal says `end-of-day flatten refused: the regular session is closed` — Task 24's test `half day`.
4. **A restart in the middle of a wave**: reconcile reloads the open orders, the wave does not resume, and the next pass's `strategy_position` refuses to size while one of them may still fill — Task 10's test `restart mid-wave does not double-send`.
5. **Two strategies proposing the same symbol, side and quantity inside `duplicate_window_s`** are not a double-click once the rule is scoped by `source` — Task 9's test `duplicate is per source`.

---

## Stage 0 — Governance: the charter, the book, the contracts, the pre-registrations

Branch `auto/s0-governance` from `main`. No trading behaviour changes in this stage; every task is text, schema or validation.

### Task 1: The charter amendment, the plan index, and the ledger

**Files:**
- Modify: `docs/CHARTER.md` (the *The gates* last paragraph; *Reporting*)
- Modify: `docs/superpowers/plans/README.md` (active plan line)
- Create: `.superpowers/sdd/2026-10-11-auto/progress.md` (gitignored; not committed)
- Test: `test/test_embedded_assets.ml` (a doc pin: `docs/CHARTER.md` contains the sentence `the owner may register a strategy as \`incubation\``)

**Interfaces:** Produces the words every later page quotes: `INCUBATION`, `not evidence of edge`.

- [ ] **Step 1: Write the failing pin.** In `test/test_embedded_assets.ml`, beside the existing README pins, add a case `charter names incubation` that reads `../../docs/CHARTER.md` through the same embedded-docs path the README pin uses and checks `String.is_substring ~substring:"the owner may register a strategy as `incubation`"`.
- [ ] **Step 2: Run** `make test` → the new case FAILS (substring absent). Record RED.
- [ ] **Step 3: Edit `docs/CHARTER.md`:** replace the paragraph beginning "A signal reaches the core with a `validation` block" with the spec §3 text verbatim; under *Reporting* change "Distinguish *advisory* from *live* every time." to "Distinguish *advisory*, *incubation* and *live* every time."
- [ ] **Step 4: Edit `docs/superpowers/plans/README.md`:** add at the top, above the compute-program line: `**Active plan (2026-10-11):** \`docs/superpowers/plans/2026-10-11-autonomous-paper-trading.md\` — autonomous paper trading. Design doc: \`docs/superpowers/specs/2026-10-11-autonomous-paper-trading-design.md\`. Ledger: \`.superpowers/sdd/2026-10-11-auto/progress.md\`.`
- [ ] **Step 5: Create the ledger** with the conventions header the Ship ledger uses and one line: `2026-10-11 Stage 0 opened on auto/s0-governance from main <sha>`.
- [ ] **Step 6: Run** `make test`; bump `lib/verified.ml` `tests` by 1. GREEN. `dune build @fmt` clean.
- [ ] **Step 7: Commit** `docs: the charter admits paper incubation, labelled and scored, never live` — files: `docs/CHARTER.md`, `docs/superpowers/plans/README.md`, `test/test_embedded_assets.ml`, `lib/verified.ml`.

### Task 2: The book's schema — the incubation tier, the overlay fields, the caps

**Files:**
- Modify: `lib/config.ml` (`universe_cap`; `Desk_spec`; `Signals_spec`)
- Modify: `book.example.sexp` (the `desk` and `signals` blocks per spec §4.4)
- Modify: `test/test_example_book.ml`, `test/test_config.ml` (or the file that tests `Book.errors` today — anchor by the existing `signals:` error cases)
- Test values below.

**Interfaces:**
- Produces in `Ohcamel.Config`:
  ```ocaml
  let universe_cap = 64
  let stream_cap = 30
  module Book.Desk_spec : type t = { … existing …;
    incubation_cap : float; [@sexp.default 0.0]
    vol_target : float; [@sexp.default 0.10]
    overlay_max_age_minutes : int; [@sexp.default 60]
    overlay_fallback : float; [@sexp.default 0.5]
    overlay_band : float; [@sexp.default 0.10]
    intraday_slip_bps : float; [@sexp.default 10.0]
    min_leg_notional : float; [@sexp.default 200.0]
    wave_timeout_s : float; [@sexp.default 180.0]
    flatten_at : string; [@sexp.default "15:50"]
    stream : string list; [@sexp.default []] }
    (* and max_open_orders' default becomes 200: a roster of two dozen strategies rests more than twenty
       market-on-open orders at once; the example book says so beside the field *)
  module Book.Signals_spec :
    type sizing = Advisory | Live | Incubation
    type cadence = Daily | Intraday
    type adjust = Intraday_adjust | Open_adjust   (* sexp names: intraday | open *)
    type horizon = Carry | Close
    module Stop : type t = { drawdown : float; sessions : int }
    module Strategy : type t = { name; symbols; max_age; sizing; capital_fraction;
      cadence : cadence; [@sexp.default Daily]
      adjust : adjust; [@sexp.default Intraday_adjust]
      max_age_minutes : int; [@sexp.default 20]
      horizon : horizon; [@sexp.default Carry]
      stop : Stop.t option; [@sexp.default None]
      reinstated : Date.t option [@sexp.default None] }
    val sizing_to_string : sizing -> string   (* "advisory" | "live" | "incubation" *)
    val flatten_ofday : string -> (Time_ns.Ofday.t, string) Result.t
  ```
- `Signals_spec.validate` refuses: incubation fractions summing above `incubation_cap` (+1e-9); live + incubation above 1; `stop.drawdown` not in (0, 1]; `stop.sessions` < 1; `max_age_minutes` < 1; a `reinstated` date with no `stop`. `Desk_spec.validate` refuses: `incubation_cap` not in [0, 1]; `vol_target` not in (0, 1]; `overlay_fallback`/`overlay_band` not in [0, 1]; `intraday_slip_bps` ≥ `price_collar × 1e4`; `min_leg_notional` < 0; `wave_timeout_s` ≤ 0; `flatten_at` not `HH:MM` inside [09:30, 16:00); `stream` naming a symbol outside the universe or more than `stream_cap`.

- [ ] **Step 1: Failing tests.** In the config test file add, each as its own Alcotest case with the exact message checked:
  - `incubation sum over cap`: two strategies `(sizing incubation) (capital_fraction 0.4)` with `(incubation_cap 0.6)` → `Error` containing `incubation strategies' capital_fraction values sum to 0.8, more than incubation_cap 0.6`.
  - `live plus incubation over one`: live 0.5 + incubation 0.6 with cap 0.6 → `sum to 1.1, more than 1`.
  - `stop drawdown range`: `(stop ((drawdown 1.5) (sessions 250)))` → `stop.drawdown must be in (0, 1], got 1.5`.
  - `flatten_at parses`: `"15:50"` → `Ok` of 15:50; `"16:00"` → `Error "flatten_at must be inside the regular session [09:30, 16:00), got 16:00"`; `"3pm"` → `Error` containing `HH:MM`.
  - `stream cap`: 31 names in `(stream …)` → `Error` containing `stream names 31 symbols; at most 30 stream`.
  - `sixty-four names load, sixty-five refuse`: a generated book of 64 positions parses with `Book.errors` = []; 65 → the universe error names `64`.
  - `defaults`: `Desk_spec.default.incubation_cap = 0.0`, `vol_target = 0.10`, `flatten_at = "15:50"`, `max_open_orders = 200`.
- [ ] **Step 2: Run** `make test` → every new case fails to compile or fails. Record RED.
- [ ] **Step 3: Implement** the types and validations above in `lib/config.ml`. `flatten_ofday` parses with `Time_ns.Ofday.of_string` after checking the `^[0-9]{2}:[0-9]{2}$` shape and compares against `Time_ns.Ofday.create ~hr:9 ~min:30 ()` and `~hr:16 ()`. Keep every existing message unchanged.
- [ ] **Step 4: `book.example.sexp`:** extend the `desk` block with every new field at the spec's defaults, comments in the file's voice (one or two sentences each, saying what the engine does with it); rewrite the `signals` block to the full roster: `exp_a01_spy`, `exp_a01_tlt` (`(sizing incubation) (capital_fraction 0.025) (cadence daily) (adjust intraday) (stop ((drawdown 0.15) (sessions 250)))`), then the 21 farm cells (slug `farm_<strategy>_<universe>` over `backtest/strategies.py`'s `suggested_universes`, symbols from `quant/src/ohcamel_quant/universes.json`), each with the same incubation fields; `(incubation_cap 0.60)`; positions gain every one of the 42 site ETFs at `(qty 0.0)` with a sector per universe group (`INDEX`, `SECTOR`, `STYLE`, `RATES`, `CREDIT`, `INTL`, `COMMODITY`); `(trading disabled)` stays. Update the block comments: incubation is explained in four sentences and points at the charter.
- [ ] **Step 5: `test/test_example_book.ml`:** the existing parse-and-check case passes; add `roster fractions sum to the cap` (23 × 0.025 + … = 0.60 within 1e-9 — write the exact arithmetic beside it: 23 strategies at 0.025 is 0.575; make the two EXP-A01 entries 0.0375 each so the sum is exactly 0.600) and `every roster symbol is a position`.
- [ ] **Step 6: `check-book`:** run `dune exec -- ohcamel check-book book.example.sexp` → exits 0; run it on a copy with `(incubation_cap 0.5)` → exits 1 naming the sum.
- [ ] **Step 7: Run** `make test`, bump `lib/verified.ml`; `dune build @fmt`. GREEN.
- [ ] **Step 8: Commit** `book: the incubation tier, the overlay fields and a 64-name universe, every one validated by check-book` — `lib/config.ml`, `book.example.sexp`, the two test files, `lib/verified.ml`.

### Task 3: Contract v2 and the overlay document, in `interface/`

**Files:**
- Modify: `interface/README.md` (a *v2 — intraday* section: R1 `schema_version = 2`, `as_of` an RFC 3339 instant, `bar`, `horizon`, R3/R4 against the venue clock in minutes, R9; an *Overlay* section: O1–O4)
- Keep: `interface/signal.schema.json` byte-identical (the research package and the desk's tests read it as v1).
- Create: `interface/signal.v2.schema.json`
- Create: `interface/overlay.schema.json`
- Create: `interface/examples/v2-pass.json`, `v2-r9-uncorroborated.json`, `v2-r4-stale.json`, `overlay-pass.json`, `overlay-o4-range.json`; extend `interface/examples/expected.json` with their outcomes under a stated clock `{"now": "2026-10-13T15:31:00Z", "last_tick": {"SPY": "2026-10-13T15:30:58Z"}}`.
- Test: `research/tests/test_contract_examples.py` (whatever today reads `expected.json` — anchor by the file that iterates `interface/examples/`) gains the v2 and overlay rows for the schema half only; the OCaml half arrives in Task 22.

**Interfaces:** Produces the v2 document shape every producer and the desk share:
```json
{"schema_version": 2, "strategy": "exp_i01_spy", "params_hash": "sha256:…", "as_of": "2026-10-13T15:30:00Z",
 "bar": "30Min", "horizon": "close", "computed_at": "…", "data_hash": "sha256:…", "sequence": 41,
 "validation": {…as v1…}, "targets": [{"symbol": "SPY", "weight": 0.9}]}
```
and the overlay:
```json
{"schema_version": 1, "kind": "overlay", "as_of": "2026-10-13T15:30:00Z", "computed_at": "…", "sequence": 7,
 "exposure_scale": 0.7071, "inputs": {"vol_target": 0.10, "vol_forecast": 0.1414, "vol_model": "har", "cov_estimator": "lw",
 "vol_artifact": "01M…", "cov_artifact": "01M…"}, "reported": {"regime_p_high": 0.12, "mc_var_99_1d_core": 0.0131}}
```
`bar` ∈ {`1Min`,`5Min`,`15Min`,`30Min`}; `horizon` ∈ {`carry`,`close`}.

- [ ] **Step 1: Failing test.** Add the five example files and their `expected.json` rows (`v2-pass` → `accepted`; `v2-r9-uncorroborated` → `rejected R9` with `last_tick` for SPY at 14:59:00Z and `bar` 30Min; `v2-r4-stale` → `rejected R4` with `as_of` 14:40:00Z and `max_age_minutes` 20; `overlay-pass` → `accepted`; `overlay-o4-range` → `rejected O4` with `exposure_scale` 1.2). Run the research example test → FAIL (schemas absent).
- [ ] **Step 2: Write** `signal.v2.schema.json` (copy v1; `schema_version` `const 2`; `as_of` `format date-time`; add `bar` enum and `horizon` enum, both required) and `overlay.schema.json` (fields above; `exposure_scale` `minimum 0, maximum 1`; `additionalProperties false`). Write the README sections.
- [ ] **Step 3: Run** the research tests → GREEN for the schema half; the rows whose OCaml half is pending are marked `"core": "pending-task-22"` and the OCaml `contract check` skips rows so marked (one line in `research/src/ohcamel_research/cli.py`'s contract-check loop; test it with a fixture row).
- [ ] **Step 4: Commit** `research: contract v2 for intraday signals and the overlay document, with examples both sides read` — the `interface/` files, `research/src/ohcamel_research/cli.py`, its test.

### Task 4: INC-01 and the three intraday pre-registrations

**Files:**
- Create: `research/experiments/INC-01/preregistration.md`, `research/experiments/INC-01/roster.yaml`
- Create: `research/experiments/EXP-I01/preregistration.md`, `config.yaml`; same for `EXP-I02`, `EXP-I03`
- Modify: `docs/superpowers/specs/2026-10-11-autonomous-paper-trading-design.md` (§5 and §6 link the files)

**Interfaces:** `roster.yaml` is the one list every producer reads (Task 13):
```yaml
version: 1
incubation_cap: 0.60
canary_cap: 0.10
vol_target: 0.10
strategies:
  - slug: exp_a01_spy
    producer: research-service        # exists; unchanged
    capital_fraction: 0.0375
  - slug: farm_tsmom_rates
    producer: signals.daily
    strategy: tsmom
    universe: rates
    symbols: [BIL, SHY, IEI, IEF, TLT, TIP]
    capital_fraction: 0.025
  …
```

- [ ] **Step 1: Write INC-01** with every section spec §5 lists, in EXP-A01's register: *What incubation is and is not*; *Roster* (a table of 24 rows: slug, producer, rule, universe, fraction); *Capital*; *The parameter-freeze rule* ("on the first session of each month the newest `farm.sweep` artifact's `selected_params` for the cell are read and held for the month; SKIPPED or INSUFFICIENT DATA keeps last month's; none → not emitted, reason recorded"); *Gross scaling* ("a strategy whose Σ|w| exceeds 1 is scaled to Σ|w| = 1 and the document records `gross_scaled: true` in `params_hash`'s parameters"); *The overlay* (the formula, the governor); *Execution* (slip, leg floor, waves); *Stop rules*; *Reporting*; *Kill criteria for the program*; *Approval* block empty for the owner.
- [ ] **Step 2: Write EXP-I01, I02, I03** from spec §6 in EXP-A01's form (paper, hypothesis, rationale, data stated exactly — "Alpaca IEX minute bars 2016-01-04 → 2026-09-30 for SPY, QQQ, IWM, TLT, GLD, regular session, warehouse `bars_minute`", assumptions, grid fixed in advance, how it fails, kill criteria "any charter gate failing is a fail"); each `config.yaml` names `id`, `universe`, `bars: minute`, `session_series: true`, `selection_window {2016-06-01, 2023-12-31}`, `holdout_window {2024-01-01, 2026-09-30}`, `regimes: research` (the battery's five), `cost_sweep_bps_round_trip [0,5,15,30]`, `bootstrap {1000, stationary_block}`, `seed 42`, and the grid.
- [ ] **Step 3: A research test** (`research/tests/test_experiments_present.py`) asserts each new directory has a `preregistration.md` ending with an `## Approval` heading and that INC-01's `roster.yaml` loads and its fractions sum to `incubation_cap` within 1e-9.
- [ ] **Step 4: Commit** `research: INC-01, the incubation pre-registration, and EXP-I01..I03 drafted for the owner` — the new files and the test.

### Task 5: Stage 0 close

- [ ] Run `make test`, `make research-test`, `cd quant && uv run pytest -q`, `scripts/check-counts.sh --verify`, `dune build @fmt`. All green.
- [ ] Ledger: one line per task with its SHA; the owner-gate line: `OWNER: approve the design doc (D1, D2), INC-01, EXP-I01..I03`.
- [ ] Merge `auto/s0-governance` into `main` (`--no-ff`), push. No deploy (docs and schema only; the live engine's book is unchanged).

---

## Stage 1 — The desk trades: sixty-four names, incubation, the overlay, intraday waves, the daily producers

Branch `auto/s1-desk` from `main`. Gate before it: the owner approved the design and INC-01 (ledger line).

### Task 6: The universe at sixty-four: the stream's thirty and the poller's rest

**Files:**
- Modify: `lib/config.ml` (`Book.stream_names`)
- Modify: `lib/feed/alpaca_rest.ml` (`latest_trades_uri`, `parse_latest_trades`)
- Create: `lib/feed/alpaca_poll.ml`
- Modify: `bin/main.ml:1083-1103` (the cap message names 64 and says the rest are polled), `:1516-1532` (start the poller beside the websocket)
- Modify: `lib/feed/alpaca_ws.ml` (`run` takes `~symbols` instead of the whole book — anchor by `subscribe_frame ~symbols`)
- Test: `test/test_feed.ml`, `test/test_config.ml`

**Interfaces:**
```ocaml
(* lib/config.ml *)
val Book.stream_names : Book.t -> Types.Symbol.t list
  (* the desk's (stream …) names in their order, then the remaining positions in
     book order, truncated to stream_cap; never a duplicate *)
val Book.polled_names : Book.t -> Types.Symbol.t list   (* instruments minus stream_names *)
(* lib/feed/alpaca_rest.ml *)
val latest_trades_uri : symbols:Types.Symbol.t list -> feed:string -> Uri.t
  (* https://data.alpaca.markets/v2/stocks/trades/latest?symbols=A,B&feed=iex *)
val parse_latest_trades : Yojson.Safe.t -> (Types.Symbol.t * Types.Tick.t) list
  (* {"trades":{"SPY":{"t":"2026-10-13T15:30:58.12Z","p":567.12,"s":100,…}}};
     an entry with a non-finite p, a missing t or a symbol the parser cannot read is skipped *)
(* lib/feed/alpaca_poll.ml *)
val run : graph:Graph.t -> credentials:Config.Credentials.t -> runtime:Config.Runtime.t
       -> symbols:Types.Symbol.t list -> every:Time_ns.Span.t -> stats:Alpaca_ws.Stats.t
       -> on_event:(string -> unit) -> unit Deferred.t
  (* batches of at most 50 symbols; each request under Clock_ns.with_timeout 30 s with ~interrupt;
     applies ticks with Graph.apply_tick and one Graph.stabilize per batch; never raises out *)
```

- [ ] **Step 1: Failing tests.** `test_feed.ml`: `parse_latest_trades two names` on the hand-written payload above plus TLT at 91.50 → two ticks with the prices and the instants `2026-10-13 15:30:58.120000000Z`; `skips a NaN price` (`"p": NaN` is not JSON — use `"p": "x"`) → one tick; `latest_trades_uri` → the exact string with `symbols=SPY%2CTLT` (Uri's encoding) and `feed=iex`. `test_config.ml`: `stream_names prefers the stream list` (a 35-name book with `(stream (GLD TLT))` → GLD, TLT, then the first 28 positions in order, none repeated; `polled_names` is the other 5); `a book without stream streams the first thirty`.
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.** `parse_latest_trades` reuses `Alpaca_ws`'s trade-message tick construction (anchor: the function that builds `Types.Tick` from `t`, `p`, `s`) — do not write a second parser of the instant. `alpaca_poll.ml` loops `Clock_ns.every' every` (stop on the graph's close), `Deferred.List.iter ~how:`Sequential` over `List.chunks_of symbols ~length:50`, logs one line on the first success and on each failure class change (not every failure). `bin/main.ml`: pass `~symbols:(Config.Book.stream_names book)` to `Alpaca_ws.run`; start `Alpaca_poll.run ~symbols:(Config.Book.polled_names book) ~every:(Time_ns.Span.of_sec 10.)` when that list is non-empty; the cap refusal message becomes `the book declares %d names and this engine carries at most %d (thirty on the stream, the rest polled every ten seconds)`.
- [ ] **Step 4: Topology pins.** `test_graph.ml`'s recompute sets are unchanged (the poller adds no node). `test_scaling_probe.ml` unchanged. If either moves, stop and report.
- [ ] **Step 5: GREEN**, counts bumped, fmt clean.
- [ ] **Step 6: Commit** `graph: sixty-four names, the stream's thirty and the rest marked by the latest-trades poll every ten seconds`.

### Task 7: Journal additions for the loop

**Files:**
- Modify: `desk/journal.ml` (schema list, additive), `desk/journal.mli`
- Test: `test/test_journal.ml`

**Interfaces:**
```ocaml
(* tables *)
"CREATE TABLE IF NOT EXISTS overlays (sequence INTEGER PRIMARY KEY, as_of TEXT NOT NULL, received_at TEXT NOT NULL, exposure_scale REAL NOT NULL, verdict TEXT NOT NULL, rule TEXT, detail TEXT, document TEXT NOT NULL)"
"CREATE TABLE IF NOT EXISTS sizings (strategy TEXT NOT NULL, sequence INTEGER NOT NULL, at TEXT NOT NULL, tier TEXT NOT NULL, scale REAL NOT NULL, allocated REAL NOT NULL, mode TEXT NOT NULL, PRIMARY KEY (strategy, sequence, at))"
"CREATE TABLE IF NOT EXISTS strategy_state (strategy TEXT PRIMARY KEY, state TEXT NOT NULL, rule TEXT, at TEXT NOT NULL, detail TEXT NOT NULL)"
"CREATE INDEX IF NOT EXISTS sizings_by_strategy ON sizings (strategy, at)"
(* functions *)
val record_overlay : t -> sequence:int -> as_of:string -> received_at:Time_ns.t -> exposure_scale:float
                     -> verdict:[ `Accepted | `Rejected ] -> rule:string option -> detail:string -> document:string -> unit
val latest_overlay : t -> (int * string * float * string) option   (* sequence, as_of, scale, received_at; accepted only *)
val highest_overlay_sequence : t -> int
val record_sizing : t -> strategy:string -> sequence:int -> at:Time_ns.t -> tier:string -> scale:float
                    -> allocated:float -> mode:string -> unit
val latest_sizing : t -> strategy:string -> (int * float * float * string) option   (* sequence, scale, allocated, at *)
val set_strategy_state : t -> strategy:string -> state:[ `Active | `Stopped ] -> rule:string option
                         -> at:Time_ns.t -> detail:string -> unit
val strategy_state : t -> strategy:string -> ([ `Active | `Stopped ] * string option * string * string) option
val latest_accepted_document : t -> strategy:string -> (int * string) option   (* sequence, document JSON of the newest accepted or accepted_incubation judgement *)
```
`Journal.Signal.Verdict` gains `Accepted_incubation` (string `accepted_incubation`); `of_string` accepts it; the existing three unchanged.

- [ ] **Step 1: Failing tests** in `test_journal.ml`: round trips for each function on an in-memory journal; `latest_overlay ignores rejected`; `latest_sizing is the newest at`; `strategy_state absent is None`; `Verdict.to_string Accepted_incubation = "accepted_incubation"` and `of_string` inverse; `schema_version stays 1` after opening a journal created by the previous schema (the existing migration test's fixture).
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN**, counts, fmt.
- [ ] **Step 5: Commit** `desk: the journal records overlays, sizings and a strategy's state, additively`.

### Task 8: `desk/overlay.ml` — the document, its rules, the governor

**Files:**
- Create: `desk/overlay.ml`, `desk/overlay.mli`
- Test: `test/test_overlay.ml` (register in `test/test_ohcamel.ml` beside `Test_contract.suite`)

**Interfaces:**
```ocaml
type t = { schema_version : int; kind : string; as_of : Time_ns.t; computed_at : string; sequence : int;
           exposure_scale : float; inputs : Yojson.Safe.t; reported : Yojson.Safe.t; raw : string }
val parse_string : string -> (t, string) Result.t
type clock = { now : Time_ns.t }
type verdict = Accepted of t | Rejected of [ `O1 | `O2 | `O3 | `O4 ] * string
val validate : clock:clock -> max_age_minutes:int -> last_sequence:int -> t -> verdict
  (* O1 schema_version = 1 and kind = "overlay"; O2 as_of <= now and now - as_of <= max_age minutes;
     O3 sequence > last_sequence; O4 exposure_scale finite and in [0, 1] — written not (x >= 0. && x <= 1.) *)
val governor : drawdown:float option -> dd_cap:float option -> float
  (* None cap or None drawdown -> 1.0; 1.0 while dd <= cap/2; (cap - dd) / (cap/2) between; 0.0 at and beyond cap *)
val effective : overlay:float option -> fallback:float -> governor:float -> float
  (* (overlay or fallback) * governor, clamped to [0, 1] *)
type state = Fresh | Held of Time_ns.Span.t | Stale | Absent
val state : clock:clock -> received_at:Time_ns.t option -> max_age_minutes:int -> state
```

- [ ] **Step 1: Failing tests, hand-derived:** `governor at half cap is one` (dd 0.010, cap 0.02 → 1.0); `governor at three quarters` (dd 0.015 → (0.02−0.015)/0.01 = 0.5); `at seven eighths` (0.0175 → 0.25); `at cap` (0.02 → 0.0); `beyond cap` (0.03 → 0.0); `no cap` → 1.0; `effective` (overlay 0.7071, governor 0.5 → 0.35355; overlay None, fallback 0.5, governor 1 → 0.5); `O2 future` (as_of = now + 1 s → Rejected O2); `O2 stale` (now − as_of = 61 min, max_age 60 → O2 naming `61 minutes`); `O3 replay`; `O4 refuses NaN` (`"exposure_scale": "nan"` is not JSON: feed `1e999` → parses to inf → O4) and `O4 1.2`; `state Held` (received 30 min ago, max 60 → `Held 30m`); `state Stale` (61 min); `parse refuses kind signal`.
- [ ] **Step 2: RED. Step 3: Implement** (pure; Yojson; no IO). **Step 4: GREEN**, counts, fmt.
- [ ] **Step 5: Commit** `desk: the overlay document, O1-O4, the drawdown governor and the effective scale, all pure`.

### Task 9: The incubation tier in the contract, the intake and the OMS

**Files:**
- Modify: `desk/contract.ml`, `desk/contract.mli` (`validate` continues past R6 when asked)
- Modify: `desk/intake.ml:404-486` (`decide`), `:805-880` (`rebalance`, `size`), the judgement record (tier)
- Modify: `desk/rebalance.ml:236-355` (`plan ~scale`), `desk/oms.ml:1141-1171` (`plan_rebalance ~scale ~tier`), `desk/rules.ml` (`duplicate` scoped by source), `desk/oms.ml` `context`/`Recent` (carry `source`)
- Modify: `desk/desk.ml` (`venue_name` exposed to the OMS as `trade_half_name`)
- Test: `test/test_contract.ml`, `test/test_intake.ml`, `test/test_rebalance.ml`, `test/test_rules.ml`, `test/test_oms.ml`

**Interfaces:**
```ocaml
(* contract.ml *)
type verdict = Accepted of t | Accepted_unvalidated of t * string | Rejected of Rule.t * string
val validate : ?r6:[ `Enforce | `Record ] -> clock:Clock.t -> registry:registry -> universe:universe -> max_age:int -> t -> verdict
  (* default `Enforce (today's behaviour). `Record: an R6 failure is carried as Accepted_unvalidated (doc, why)
     and R7 still runs; R1-R5 and R7 failures are Rejected as before *)
(* intake.ml *)
module Judgement : type t = { verdict : Journal.Signal.Verdict.t; rule : string option; detail : string; tier : string }
  (* tier is "advisory" | "incubation" | "live" — the strategy's sizing word, journaled on the signals row's detail
     prefix "[tier=incubation] " and served by /api/research *)
val decide : … -> doc -> verdict -> [ `Defer of string | `Judged of Judgement.t ]
  (* Incubation strategy: Accepted doc, or Accepted_unvalidated (doc, why) -> verdict Accepted_incubation,
     rule (None | Some "R6"), detail names the validation status; Live: Accepted_unvalidated -> Advisory "R6" as today *)
(* rebalance.ml *)
val plan : … -> scale:float -> … (* target = trunc (w * f * scale * E / p); scale in [0, 1] else Error; scale 0 -> every target 0 *)
(* oms.ml *)
val plan_rebalance : t -> strategy:… -> targets:… -> scale:float -> (Rebalance.Leg.t list, string) Result.t
val incubation_permitted : t -> (unit, string) Result.t
  (* Ok when the trade half's name is "alpaca-paper" or "sim"; else Error "incubation sizing is refused on venue %s" *)
(* rules.ml *)
module Recent : type t = { symbol; side; qty; at; source : string }
  (* duplicate: same symbol, side, qty AND source within the window *)
```

- [ ] **Step 1: Failing tests.**
  - `test_contract.ml`: `record mode carries R6` (status `fail`, targets valid → `Accepted_unvalidated (_, "validation.status is \"fail\" -- advisory, not traded")`); `record mode still rejects R7` (status fail, weight 1.5 → `Rejected (R7, …)`); `enforce is unchanged` (status fail → `Rejected (R6, _)`).
  - `test_intake.ml`: add a third strategy `spy_incubation = strategy ~sizing:Signals_spec.Incubation "inc_spy" ["SPY"]`; cases: `incubation sizes a failing status` (status `fail` → journal verdict `accepted_incubation`, rule `Some "R6"`, detail contains `incubation`, and a rebalance sentence not beginning `not sized`); `incubation sizes a pass too` (rule None); `incubation refused on a foreign venue` (an `Oms` built on a trade half named `other` → the rebalance sentence is `refused, and nothing journaled or sent: incubation sizing is refused on venue other`); `live still advisory on fail` (unchanged case stays green); `tier served` (`/api/research` row has `"tier": "incubation"`).
  - `test_rebalance.ml`: `scale halves the target` (w 1.0, f 0.5, scale 0.5, E 100 000, p 100 → target 250; current 0 → Buy 250); `scale zero flattens` (current 300 → Sell 300); `scale 1.5 refused`.
  - `test_intake.ml`: `the validation block is journaled byte for byte` (the `signals` row's `document` equals the file's bytes, and its parsed `validation` equals the file's — invariant 15).
  - `test_rules.ml`: `duplicate is per source` (two proposals, same SPY Buy 10 within 10 s, sources `signal:a:1` and `signal:b:1` → the second passes; same source → `duplicate`).
- [ ] **Step 2: RED. Step 3: Implement** in the order contract → rebalance → rules → oms → intake. In `intake.size`, record the sizing row (`Journal.record_sizing ~tier ~scale ~allocated:(f *. scale *. E) ~mode:"open"`) beside the rebalance sentence. The effective scale for this task is `1.0` (Task 11 wires the overlay) — `size` takes `~scale:(fun () -> 1.0)` so Task 11 changes one argument.
- [ ] **Step 4: GREEN**, counts, fmt. `grep -rn 'Accepted_unvalidated' lib/` is empty (desk only).
- [ ] **Step 5: Commit** `desk: incubation sizes a strategy whatever its status, on the paper venue only, with its tier on every row`.

### Task 10: Intraday mode and waves

**Files:**
- Modify: `desk/rebalance.ml` (`Mode`, `limit_price`, `drop_small`), `desk/oms.ml:1273-1544` (`check_rebalance ~mode`, `propose_rebalance ~mode`), `desk/oms.mli` (if present; else the `.ml` doc comment)
- Create: `desk/waves.ml`, `desk/waves.mli`
- Modify: `desk/sim_venue.ml` (a `Day` limit order fills at the first mark inside its limit, at mark ± half-spread, on `pump`)
- Test: `test/test_rebalance.ml`, `test/test_waves.ml` (new; register), `test/test_oms.ml`, `test/desk_async/test_desk_async.ml` (the timeout on a virtual clock; bump `scheduler_tests`)

**Interfaces:**
```ocaml
(* rebalance.ml *)
module Mode : type t = Open | Intraday  [@@deriving sexp_of, compare, equal]
val limit_price : side:Order.Side.t -> mark:float -> slip_bps:float -> float
  (* buy: mark * (1 + slip/1e4); sell: mark * (1 - slip/1e4); rounded to the cent half-even *)
val drop_small : legs:Leg.t list -> min_leg_notional:float -> Leg.t list * Leg.t list   (* kept, dropped: |qty * price| < floor *)
(* waves.ml *)
val split : legs:Rebalance.Leg.t list -> position:(Symbol.t -> float) -> max_legs:int -> Rebalance.Leg.t list list
  (* wave 1 = every leg Rebalance.grows says shrinks (may be empty -> omitted); then growing legs by
     |qty * price| descending in chunks of max_legs *)
(* oms.ml *)
module Rebalance_outcome : type t = Refused of string | Sent of Order.t list | Stopped of {…}
  | Waved of { waves : t list; timed_out : bool }
val propose_rebalance : t -> source:… -> legs:… -> mode:Rebalance.Mode.t -> Rebalance_outcome.t Deferred.t
  (* Open: as today (Market/Opg, max_legs refusal). Intraday: Limit/Day at limit_price; n <= max_legs -> one wave;
     else Waves.split, each wave check_rebalance'd and sent in turn, then await_settled ~timeout:(wave_timeout_s);
     on timeout every open order of this source is cancelled (cancel, journaled) and the outcome's timed_out is true;
     the next wave is not sent after a timeout *)
val await_settled : t -> source:Rebalance.Source.t -> timeout:Time_ns.Span.t -> [ `Settled | `Timed_out ] Deferred.t
  (* polls Journal.open_orders_by_source every 2 s on the OMS's time source *)
```

- [ ] **Step 1: Failing tests, hand-derived.** `limit_price buy` (100.00, 10 bps → 100.10), `sell` (→ 99.90), `rounds half even` (123.455 × 1.001 = 123.578455 → 123.58). `drop_small` (a 1-share leg at 150.00 with floor 200 → dropped; 2 shares → kept). `Waves.split seven` (3 shrinking + 4 growing → `[[3]; [4]]`), `fourteen` (2 shrinking + 12 growing with notionals 12…1 → `[[2]; [6 largest]; [6 smallest]]`), `no shrinking` (→ one wave only). `test_oms.ml` on the sim venue: `intraday single wave sends Day limits` (3 legs → 3 orders `kind = Limit`, `tif = Day`); `open mode unchanged` (the existing MOO cases stay green byte for byte); `restart mid-wave does not double-send` (send wave 1 of 2, drop the OMS, build a new one on the same journal, reconcile, run the next intake pass → `strategy_position` refuses with the existing "may still fill" reason and no new order exists). `desk_async`: `wave timeout cancels` (virtual clock; the sim venue withholds fills; after 180 s every order of the source is `pending_cancel` then `cancelled`, outcome `Waved { timed_out = true }`, no second wave).
- [ ] **Step 2: RED. Step 3: Implement.** `check_rebalance ~mode` chooses `kind`/`tif` and the rules `context ~tif`; the `n <= max_legs` refusal applies per wave. `propose_rebalance ~mode:Intraday` with `n > max_legs` runs the waves sequentially inside one `enqueue` so no ticket interleaves (the sequencer's existing guarantee). Sim venue: a `Day` `Limit` order fills on the next `pump` whose mark is at or inside the limit, at mark ± half-spread, else rests.
- [ ] **Step 4: GREEN**, counts (both), fmt.
- [ ] **Step 5: Commit** `desk: an intraday rebalance mode -- Day limit orders inside the collar, in gated waves with a bounded wait`.

### Task 11: The overlay in the desk, and the adjust pass

**Files:**
- Modify: `desk/intake.ml` (the pass reads `overlay-*.json` before signals; `size` takes the effective scale; a new `adjust` step), `desk/desk.ml` (holds the current overlay, `exposure_scale`, `governor`; summary fields), `desk/oms.ml` (`drawdown` read for the governor — `Graph.drawdown t.graph`; anchor by the existing drawdown node read on the frame)
- Modify: `lib/alerts.ml` only if an alert kind enum exists there — otherwise the alert row is written by the desk via `Journal.record_alert ~kind:"overlay_zero"` (anchor: the alerts table writer)
- Modify: `test/test_server.ml` (`test_round_trips` required keys: `exposure_scale`, `governor`, `overlay`)
- Test: `test/test_intake.ml`, `test/test_desk.ml`

**Interfaces:**
```ocaml
(* intake.ml *)
val effective_scale : t -> float   (* Overlay.effective ~overlay:(latest accepted, if Fresh|Held) ~fallback ~governor *)
val adjust : t -> oms:Oms.t option -> unit Deferred.t
  (* for each registered strategy with adjust = Intraday_adjust, state Active, a latest_accepted_document and a
     latest_sizing: if |effective - last scale| > overlay_band then rebalance ~mode:Intraday from that document at the
     effective scale; journal the sizing with mode "adjust" *)
(* /api/desk additions: appended after "clock", because readers index the summary by position *)
("exposure_scale", `Float); ("governor", `Float);
("overlay", `Assoc [ ("value", `Float | `Null); ("as_of", `String | `Null); ("age_s", `Int | `Null); ("state", `String "fresh"|"held"|"stale"|"absent") ])
```

- [ ] **Step 1: Failing tests.** `test_intake.ml`: `overlay accepted and journaled` (an `overlay-7.json` → `overlays` row verdict accepted, scale 0.7071); `overlay stale falls back` (received 61 min before the pass → `effective_scale = overlay_fallback × governor`, `/api/desk` state `stale`); `overlay zero flattens and alerts` (scale 0.0 → an incubated strategy with a position gets a Sell of its whole position in mode `adjust`, and an `alerts` row kind `overlay_zero`); `adjust within band does nothing` (last 0.70, now 0.65, band 0.10 → no order); `adjust beyond band re-sizes` (0.70 → 0.55 → Sell of the difference: target trunc(1.0 × 0.5 × 0.55 × 100000 / 100) = 275 from 350 → Sell 75); `governor read from the graph` (a synthetic book driven to 1.5 % drawdown with cap 2 % → governor 0.5). `test_server.ml`: the three keys round-trip.
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN**, counts, fmt.
- [ ] **Step 5: Commit** `desk: the overlay scales every sizing, the governor reads the graph's drawdown, and the adjust pass re-sizes through the day`.

### Task 12: The Research and Desk pages say INCUBATION

**Files:**
- Modify: `web/research.html`, `web/research.js`, `web/index.html` (the Desk's account strip), `web/execution.html`/`.js` (waves and flattens in the blotter), `desk/desk.ml` (`/api/research` rows: `tier`, `state`, `frozen_params` placeholder `null` until Task 14's documents carry them, `overlay` block)
- Test: `test/test_embedded_assets.ml`, `test/test_intake.ml` (`/api/research` shape)

- [ ] **Step 1: Failing pins** in `test_embedded_assets.ml`: the built `research.js` contains the literals `"INCUBATION"`, `"not evidence of edge"`, `"OVERLAY STALE"`, `"STOPPED"`; `index.html` contains `exposure_scale`. `test_intake.ml`: `/api/research` row keys pinned exactly: `name, symbols, sizing, tier, state, capital_fraction, cadence, adjust, latest (…), judgement (…), validation, record (null)`; top-level `overlay` object keys `value, as_of, age_s, state, inputs`.
- [ ] **Step 2: RED. Step 3: Implement** with `F.el`/`textContent` only. Each strategy row leads with its `Verdict`-style stamp: the manifest verdict, then the tier word; an incubated row's footnote is the charter sentence. The overlay cell shows value, inputs, and the two reported-only figures labelled `reported, not used`.
- [ ] **Step 4: GREEN**, counts, fmt. Playwright on a local `demo` (port free, detached, killed by PID): `/research` renders, no console error.
- [ ] **Step 5: Commit** `web: the Research page leads with the tier -- ADVISORY, INCUBATION, LIVE, STOPPED -- and shows the overlay beside its inputs`.

### Task 13: The Python contract writer and the roster

**Files:**
- Create: `quant/src/ohcamel_quant/signals/__init__.py`, `signals/contract.py`, `signals/roster.py`
- Test: `quant/tests/test_signals_contract.py`, `quant/tests/test_signals_roster.py`

**Interfaces:**
```python
# signals/contract.py
def params_hash(params: Mapping[str, Any]) -> str            # "sha256:" + sha256(canonical JSON: sort_keys, separators=(",",":"))
def data_hash(bars: pd.DataFrame) -> str                     # the v1 recipe: lines "symbol,date,open,high,low,close,volume" sorted (date, symbol), floats repr()
def signal_v1(*, strategy, params, as_of: date, sequence: int, validation: dict, targets: dict[str, float], bars) -> dict
def signal_v2(*, strategy, params, as_of: datetime, bar: str, horizon: str, sequence, validation, targets, bars) -> dict
def overlay_doc(*, as_of: datetime, sequence: int, exposure_scale: float, inputs: dict, reported: dict) -> dict
def write_atomic(doc: dict, path: Path) -> Path             # tmp in the same dir, fsync, os.replace
def validate(doc: dict, schema: Path) -> None                # jsonschema against interface/*.schema.json; raises
# signals/roster.py
@dataclass(frozen=True) class Cell: slug: str; producer: str; strategy: str | None; universe: str | None; symbols: tuple[str, ...]; capital_fraction: float
def load_roster(path: Path = REPO_ROOT / "research/experiments/INC-01/roster.yaml") -> Roster   # .cells, .incubation_cap, .vol_target
def targets_gross_scaled(w: dict[str, float]) -> tuple[dict[str, float], bool]   # w / max(1, sum|w|), flag
```

- [ ] **Step 1: Failing tests.** `params_hash` equals the research package's for `{"fast": 1, "slow": 150}` (pin the hex from `research/tests`' existing vector — copy the literal); `data_hash` on `interface/examples`' bar fixture equals the `expected.json` value; `signal_v1` validates against `signal.v1.schema.json` and `signal_v2` against v2; `overlay_doc` against `overlay.schema.json`; `write_atomic leaves no tmp on success`; `targets_gross_scaled` ((0.8, 0.8) → (0.5, 0.5), True; (0.3, 0.3) → unchanged, False); `load_roster` sums fractions to `incubation_cap`; `roster slugs are registered in book.example.sexp` (a regex scan of `(name <slug>)` over the file).
- [ ] **Step 2: RED. Step 3: Implement. Step 4:** `uv run pytest -q`, `ruff check`, `scripts/check-counts.sh --verify` → bump the quant count.
- [ ] **Step 5: Commit** `quant: the signal and overlay writers, hashed as the research package hashes, and the roster`.

### Task 14: `signals.daily` — the farm's strategies as daily documents, parameters frozen monthly

**Files:**
- Create: `quant/src/ohcamel_quant/signals/freeze.py`, `signals/daily.py`, `quant/src/ohcamel_quant/jobs/handlers/signals.py`
- Modify: `jobs/kinds.py` (register `signals.daily`, mem class S, not public; `signals.freeze` is an artifact kind written by `signals.daily` itself, registered for its retention and never scheduled), `jobs/schedules.yaml` (one entry, `signals-daily` `45 18 * * 1-5`, `max_age 78h`; the freeze runs inside `signals.daily` on the first run of a month that has no `signals.freeze` artifact yet)
- Modify: `jobs/context.py` (`JobContext.signals_dir` from `OHCAMEL_QUANT_SIGNALS_DIR`)
- Test: `quant/tests/test_signals_daily.py`

**Interfaces:**
```python
# freeze.py
def frozen_params(ctx, *, month: date, cells: list[Cell]) -> tuple[dict[str, dict], list[dict]]
    # reads the newest farm.sweep artifact's "cells" table (products/io.previous_artifact + read_table);
    # returns {slug: selected_params} and a list of {slug, reason} for cells without usable params;
    # publishes/reads artifact kind "signals.freeze" table "params" for the month so the month is read once
# daily.py
def targets_for(cell: Cell, params: dict, prices: pd.DataFrame, rf: pd.Series | None) -> tuple[dict[str, float], date]
    # StrategyContext as products/farm.py builds it; fn(ctx, **params); last row; NaN and 0 dropped; gross-scaled
def validation_for(cell: Cell, farm_cells: pd.DataFrame) -> dict   # PASS->pass, FAIL->fail, else unvalidated; dsr/psr/pbo from the row or None; manifest = the artifact id
def run(params: dict, ctx) -> ArtifactSpec
    # for each roster cell with producer signals.daily: a v1 document named f"{slug}-{as_of}.json" in ctx.signals_dir,
    # sequence = number of sessions in bars_daily for SPY up to as_of; tables: emitted (slug, as_of, sequence, gross_scaled, n_targets),
    # refused (slug, reason); refuses a cell naming a symbol outside book.example.sexp's positions (reason "symbol X is not in the book")
```

- [ ] **Step 1: Failing tests** on the fixture warehouse (`quant/tests/lane_m_harness.py` builds it): `two cells emit two documents` that validate against v1; `as_of is the last session`; `sequence counts SPY sessions`; `gross scaled recorded`; `freeze reads selected_params once per month` (two runs in one month read one artifact; a run in the next month reads a new one); `cell without params is refused with reason`; `symbol outside the book refused`; `validation fail from a FAIL row`.
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN**, ruff, counts.
- [ ] **Step 5: Commit** `quant: signals.daily emits every farm cell's targets as a contract document, parameters frozen for the month`.

### Task 15: `signals.overlay` — volatility targeting from the leagues

**Files:**
- Create: `quant/src/ohcamel_quant/signals/overlay.py`
- Modify: `jobs/handlers/signals.py` (adapter `overlay`), `jobs/kinds.py`, `jobs/schedules.yaml` (`signals-overlay-open` `40 9 * * 1-5`; `signals-overlay` `*/15 10-15 * * 1-5`; `signals-overlay-evening` `50 18 * * 1-5`; `max_age 35m`)
- Test: `quant/tests/test_signals_overlay.py`

**Interfaces:**
```python
def combined_targets(signals_dir: Path, roster: Roster) -> dict[str, float]     # sum over cells of fraction * w from each slug's newest document (by sequence)
def covariance(returns: pd.DataFrame, estimator: str) -> pd.DataFrame            # dispatch into portfolio/ by the cov league's estimator names; "lw" when unknown
def forecast_vol(targets: dict[str, float], cov: pd.DataFrame, vol_forecasts: dict[str, float] | None) -> float
    # annualised sqrt(w' Σ w); when vol_forecasts has a name, its variance replaces Σ's diagonal for that name
def exposure_scale(vol_target: float, vol_forecast: float) -> float               # min(1, vol_target / vol_forecast); 1 when forecast is 0 or NaN -> but NaN -> raise
def run(params, ctx) -> ArtifactSpec   # reads P2/P3 newest artifacts (products/io.latest_with_path), Q02 probs and P1 core VaR for `reported`,
                                        # writes overlay-<sequence>.json; sequence = previous + 1 from the artifact ledger; tables: overlay (one row)
```

- [ ] **Step 1: Failing tests, hand-derived:** two assets, w = (0.5, 0.5), daily σ = 0.2/√252 each, ρ = 0 → annualised σ̂ = √(0.25·0.04 + 0.25·0.04) = 0.141421; `exposure_scale(0.10, 0.141421) = 0.707107`; `forecast replaces the diagonal` (vol_forecasts {"A": 0.4} → σ̂ = √(0.25·0.16 + 0.25·0.04) = 0.223607); `scale capped at one` (σ̂ 0.05 → 1.0); `NaN forecast raises`; `combined_targets uses the newest sequence`; `document validates`; `sequence increments across runs`.
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN**, ruff, counts; `schedules.yaml` loads (the existing schedule test).
- [ ] **Step 5: Commit** `quant: signals.overlay writes one exposure scale from volatility targeting on the leagues' winners`.

### Task 16: `desk.report`, the worker's signals mount, deploy tests and smoke

**Files:**
- Create: `quant/src/ohcamel_quant/signals/report.py` (kind `desk.report`, `30 16 * * 1-5`, class S: reads the live engine's `/api/desk`, `/api/desk/tca`, `/api/research` in-container at `http://ohcamel-live:8081` with a 10 s timeout; tables `fills`, `pnl_by_strategy`, `refusals`, `overlay_path`; `verdict` None; when the engine is unreachable the artifact says so and the job succeeds)
- Modify: `deploy/docker-compose.yml` (`ohcamel-worker`: `- signals:/signals` and `OHCAMEL_QUANT_SIGNALS_DIR: /signals`; a comment: the worker and the research service are both writers, to distinct file names)
- Modify: `deploy/test/*.sh` (the compose-shape tests: the worker mounts `signals` rw; the live engine still `ro`), `deploy/smoke.sh` (the live host's `/api/desk` carries a numeric `exposure_scale`; `--public-only` unchanged)
- Test: `quant/tests/test_signals_report.py` (a stub HTTP server), the deploy tests

- [ ] **Step 1: Failing tests. Step 2: RED. Step 3: Implement. Step 4: GREEN** (`deploy/test` 17 → 19; `docker compose config` validates).
- [ ] **Step 5: Commit** `deploy: the worker writes signals, desk.report records each session, and the smoke reads the exposure scale`.

### Task 17: Stage 1 close — review, merge, deploy, the canary book

- [ ] Full suites green: `make test`, `make research-test`, quant pytest on both kernels, vitest/typecheck unchanged, `deploy/test`, `check-counts --verify`, `dune build @fmt`, `actionlint`.
- [ ] Whole-stage review by a fresh reviewer; one fix wave; re-review clean.
- [ ] `docs/status.md`: the *Deployed* rows, a new *Autonomous trading* section (tier, overlay, canary), limits.
- [ ] Merge `auto/s1-desk` → `main` (`--no-ff`), push; CI and the image workflow green.
- [ ] Deploy outside the session: `deploy/deploy.sh --sha <sha> --public-only`, smoke clean; then `--live`, smoke clean; `check-book` in the new image refuses nothing on the droplet's unchanged book (no `signals` block yet → intake off, as today).
- [ ] Owner step (ledger, verbatim): copy `book.example.sexp` over the droplet's `book.sexp` with `(trading enabled)`, `(incubation_cap 0.10)`, alerts enabled, kill switch armed on `book-cap var-cap dd-cap`; restart `ohcamel-live` outside the session. Three canary sessions; the agent reads `desk.report` after each and writes the ledger line.

---

## Stage 2 — Intraday: minute history, the session-level battery, three hypotheses, contract v2, the producer, the flatten

Branch `auto/s2-intraday`. Gate: the owner approved EXP-I01..I03 (each file's `## Approval` block filled).

### Task 18: Minute bars for five names since 2016

**Files:**
- Modify: `quant/src/ohcamel_quant/warehouse/ingest/minute.py:125-160` (`tickers` param; `start` already exists; a `purge` that respects a per-ticker window: rows before `start` are purged only for tickers in the params)
- Modify: `docs/runbooks/compute.md` (*Backfilling minute history for an experiment*: `python -m ohcamel_quant.jobs run-schedule`-style enqueue with `{"start": "2016-01-01", "tickers": ["SPY","QQQ","IWM","TLT","GLD"]}`; expected rows ≈ 5 × 10 × 252 × 390 ≈ 4.9 M; class S holds because months are transactions)
- Test: `quant/tests/test_warehouse_minute.py` (`tickers narrows the fetch`; `purge is per ticker`)

- [ ] RED → GREEN → commit `quant: ingest.bars_minute takes a ticker list and a start, so an experiment's names reach back to 2016`.
- [ ] Owner/agent step after merge: enqueue the backfill on the droplet outside the session; record rows and `data_asof` in the ledger.

### Task 19: The session-level battery

**Files:**
- Create: `quant/src/ohcamel_quant/models/intraday.py`
- Modify: `quant/src/ohcamel_quant/models/verdict.py` (`charter_verdict(..., regimes=RESEARCH_REGIMES)`; the constants untouched)
- Create: `quant/src/ohcamel_quant/products/exp_intraday.py` (kind `exp.intraday`, class M heavy, params `{"experiment": "EXP-I01"}`; artifact tables `sessions`, `folds`, `gates`, `regimes`, `cost_curve`, `trials`; verdict first)
- Test: `quant/tests/test_models_intraday.py`, `quant/tests/test_products_exp_intraday.py`

**Interfaces:**
```python
RESEARCH_REGIMES = {"2018_q4_selloff": ("2018-10-01","2018-12-31"), "2020_covid": ("2020-02-19","2020-06-30"),
                    "2022_rate_shock": ("2022-01-01","2022-12-31"), "2023_recovery": ("2023-01-01","2023-12-31"), "2024": ("2024-01-01","2024-12-31")}
@dataclass class SessionBars: date: date; bars: pd.DataFrame   # ts index (NY), open/high/low/close/volume per symbol
IntradayRule = Callable[[SessionBars, pd.DataFrame /*history of prior sessions*/, dict], pd.DataFrame]
    # returns target weights per bar of the session (index = bar close ts); flat after 16:00 implicitly
def session_pnl(rule_targets: pd.DataFrame, bars: SessionBars, friction: Friction) -> float
    # fills at the next bar's open after a target change, half-spread per fill from friction (bps of price) + fees; return on 1.0 notional
def session_series(rule: IntradayRule, params: dict, sessions: list[SessionBars], friction) -> pd.Series   # one return per session
def walk_forward(rule, grid: list[dict], sessions, *, train_sessions=252, test_sessions=63, friction) -> WalkForward  # oos series + per-fold choice + trials
def battery(rule, grid, sessions, cfg) -> dict   # holdout once; DSR over len(grid)*n_folds trials; PSR; bootstrap; regimes (RESEARCH_REGIMES on session dates); cost sweep; verdict
```

- [ ] **Step 1: Failing tests, hand-derived:** `session_pnl one round trip` (target 1.0 at bar 2, 0 at bar 5; opens 100.00 → 101.00; spread 2 bps round trip halved per fill → 1 bp each; fees 0 → return = 101/100·(1−1e-4)² − 1 ≈ +0.00980); `flat session is zero`; `walk_forward chooses per fold` (two configs, a synthetic session set where config A wins the first fold and B the second → choices [A, B], oos stitched); `regimes on session dates` (a series with +1 % in each regime span → 5 of 5); `verdict FAIL when holdout negative`.
- [ ] **Step 2: RED. Step 3: Implement**, kernels via the dispatcher where one exists (`stationary_bootstrap_means`, `cscv_pbo`). **Step 4: GREEN** on rust and python kernels; counts.
- [ ] **Step 5: Commit** `quant: the battery on session-level series -- the charter's gates, unchanged, for minute-bar strategies`.

### Task 20: The three rules

**Files:**
- Create: `quant/src/ohcamel_quant/signals/intraday_rules.py` (`exp_i01`, `exp_i02`, `exp_i03`, each an `IntradayRule`; `RULES = {...}`)
- Test: `quant/tests/test_intraday_rules.py`

- [ ] **Step 1: Failing tests on hand-built sessions:** I01 — first half-hour +0.5 % → target +1.0 at the 15:30 bar, 0 after; conditioned variant with 15:00–15:30 −0.2 % → 0 all session; I02 — range 5 min high 101 low 100, a bar closing 101.5 → +1.0 scaled by `vol_target / ATR`; stop when a later bar's low crosses 100; I03 — a pair whose normalised spread reaches +2.1σ → short the rich leg, long the cheap leg, 0.5 each; closes at the session's last bar.
- [ ] RED → GREEN → commit `quant: EXP-I01, I02 and I03 as pure intraday rules`.

### Task 21: Run the batteries; write the verdicts

- [ ] After Task 18's backfill is on the droplet and the owner approved each file: enqueue `exp.intraday` for I01, I02, I03 outside the session (class M heavy → held until 16:05).
- [ ] For each: the artifact's `verdict`, gates table and the leaderboard are copied, quoted not paraphrased, into `research/experiments/EXP-I0x/model_card.md` (*Results* section, leading with the verdict); `docs/status.md` *Research* rows.
- [ ] Commit `research: EXP-I01..I03 verdicts, quoted from the battery artifacts`.

### Task 22: Contract v2 in the desk, and R9

**Files:**
- Modify: `desk/contract.ml`, `desk/contract.mli` (`t` gains `as_of_instant : Time_ns.t option; bar : Time_ns.Span.t option; horizon : [ `Carry | `Close ]`; `Rule.t` gains `R9`; `Clock2`; `validate_v2`), `desk/intake.ml` (routes by `schema_version`; the v2 clock from the desk's cached venue clock and `Graph`'s per-symbol last tick time — anchor by the staleness cells), `desk/journal.ml` (none; the `signals` row's `as_of` stores the instant's RFC 3339 string)
- Modify: `research/src/ohcamel_research/cli.py` (the `contract check` rows marked `pending-task-22` now run)
- Test: `test/test_contract.ml`, `test/test_intake.ml`

**Interfaces:**
```ocaml
module Clock2 : type t = { now : Time_ns.t; last_tick : Symbol.t -> Time_ns.t option }
val validate_v2 : clock:Clock2.t -> registry:registry -> universe:universe -> max_age_minutes:int -> ?r6:_ -> t -> verdict
  (* R1 = 2; R2; R3 as_of_instant <= now; R4 now - as_of <= max_age minutes; R5; R6 (per ?r6); R7;
     R9 for every target: last_tick symbol >= as_of - bar, else Rejected (R9, "%s: no tick at or after %s corroborates the %s bar") *)
```

- [ ] **Step 1: Failing tests** mirroring `interface/examples/expected.json`'s v2 rows (the same clock, the same outcomes), plus `R7 on a v2 NaN weight` (`1e999` → inf → R7) and `R9 passes at the boundary` (tick at exactly `as_of − bar`).
- [ ] RED → GREEN (both the OCaml cases and `uv run ohcamel-research contract check --core _build/default/bin/main.exe`) → counts → commit `desk: contract v2 -- intraday instants against the venue clock, and R9 corroborates the bar with the desk's own ticks`.

### Task 23: `signals.intraday`

**Files:**
- Create: `quant/src/ohcamel_quant/signals/intraday.py` (kind `signals.intraday`, `*/15 10-15 * * 1-5` plus `45 15 * * 1-5`, class S, `max_age 20m`: top up `bars_minute` for the roster's intraday names through the existing ingest function, build today's `SessionBars` and history, run each approved rule with its artifact's `selected_params`, write a v2 document per slug with `as_of` = the last complete bar's close, `bar` per rule, `horizon close`; at or after 15:45 every `horizon close` slug gets `targets: []`)
- Modify: `jobs/kinds.py`, `schedules.yaml`, `research/experiments/INC-01/roster.yaml` (the intraday rows, producer `signals.intraday`, with the owner's sizing word per verdict)
- Test: `quant/tests/test_signals_intraday.py`

- [ ] **Step 1: Failing tests:** `document is v2 and validates`; `as_of is the last complete bar`; `15:45 writes empty targets`; `a rule without a verdict artifact is refused with reason`.
- [ ] RED → GREEN → commit `quant: signals.intraday emits each approved rule's targets every fifteen minutes and goes flat at 15:45`.

### Task 24: The end-of-day flatten

**Files:**
- Modify: `desk/intake.ml` (`flatten_pass`, run by the minute loop: when the venue clock's local time is at or after `flatten_at` and before the close, every `horizon = Close` strategy with a non-zero `strategy_position` is proposed a zero-target `Intraday` rebalance once per session, journaled `end-of-day flatten`), `desk/journal.ml` (none: the sizing row's mode `flatten` is the once-per-session marker)
- Test: `test/test_intake.ml`, `test/desk_async/test_desk_async.ml`

- [ ] **Step 1: Failing tests:** `flatten at 15:50 sells the position` (virtual clock 15:50 ET, session open, position 10 → Sell 10 Day limit, sizing mode `flatten`); `once per session`; `half day` (venue `next_close` 13:00 on the clock; at 15:50 the session is closed → the rebalance sentence is `refused, and nothing journaled or sent: … the regular session is closed` and the position stands; the sizing row records the refusal); `carry strategies untouched`.
- [ ] RED → GREEN → counts → commit `desk: a horizon-close strategy is flat at 15:50 by its own rebalance, or the refusal says why`.

### Task 25: Stage 2 close

- [ ] Suites, review, fix wave, `docs/status.md`, merge, push, deploy outside the session. Owner step: add the intraday rows to the droplet's `book.sexp` per the roster, restart. Ledger.

---

## Stage 3 — The record: scoring, the shadow, VaR coverage, stop rules, replay

Branch `auto/s3-record`.

### Task 26: `desk/record.ml` — the pure scorer

**Files:**
- Create: `desk/record.ml`, `desk/record.mli`
- Test: `test/test_record.ml` (register)

**Interfaces:**
```ocaml
type session = { date : Date.t; allocated : float; pnl : float; cost : float; turnover : float }
type summary = { sessions : int; compounded : float; sharpe : float option; psr : float option; max_drawdown : float;
                 hit_rate : float option; turnover_mean : float option; cost_bps : float option }
val returns : session list -> float list                 (* pnl / allocated; a session with allocated <= 0 is skipped and counted *)
val psr : returns:float list -> benchmark:float -> float option
  (* Bailey & López de Prado 2012: Φ((SR − SR*) √(n−1) / √(1 − γ3 SR + (γ4 − 1)/4 SR²)), per-period SR; None below 2 *)
val summarise : session list -> summary                   (* sharpe annualised by √252; psr against 0; verdict words never here *)
val drawdown : returns:float list -> float                (* max peak-to-trough of the compounded path, as a positive fraction *)
```

- [ ] **Step 1: Failing tests, hand-derived:** returns +1 %, −1 %, +2 % → compounded 1.01 × 0.99 × 1.02 − 1 = +0.019898; drawdown 0.01; hit rate 2/3; `psr` on a hand series (five returns 0.01 each → SR undefined (σ = 0) → None); `psr of 0.5, 0.0, 0.5, 0.0 …` compute by hand in the test comment; `allocated zero skipped`.
- [ ] RED → GREEN → commit `desk: the record's statistics, pure -- Sharpe, PSR, drawdown, hit rate, turnover, cost`.

### Task 27: The record from the journal, on the wire and the page

**Files:**
- Modify: `desk/journal.ml`/`.mli` (`sessions_for ~strategy ~since` joining `sizings`, `fills`, `marks`, `sessions` by date, bounded), `desk/desk.ml` (`/api/research` `record` block: `summary` or `{"sessions": n, "needed": 60}`), `web/research.js`
- Test: `test/test_journal.ml`, `test/test_intake.ml`, `test/test_embedded_assets.ml` (`"session n of 60"` literal)

- [ ] RED → GREEN → commit `desk: each strategy's live record on /api/research, with no verdict before sixty sessions`.

### Task 28: Stop rules

**Files:**
- Create: `desk/stop.ml`, `desk/stop.mli` (`check : spec:Signals_spec.Stop.t -> record:Record.summary -> first_fill:Date.t option -> today:Date.t -> [ `Continue | `Stop of string ]`)
- Modify: `desk/session_close.ml` (after marks: evaluate every strategy with a `stop`; on `Stop why` → `Journal.set_strategy_state … `Stopped`, an alert row kind `incubation_stopped`, and a zero-target `Open` rebalance proposed for the next open), `desk/intake.ml` (a `Stopped` strategy is never sized; at startup a `reinstated` date later than the stop's `at` sets `Active` with detail `reinstated by the book`), `web/research.js` (`STOPPED` with the rule)
- Test: `test/test_stop.ml` (register), `test/test_session_close.ml`, `test/test_intake.ml`

- [ ] **Step 1: Failing tests:** `drawdown fires at the line` (0.15 with 0.15 → Stop "drawdown 15.0% reached 15.0%"); `sessions fire` (250 since first fill); `stopped is never sized`; `never un-stops` (a restart without `reinstated` → still Stopped); `reinstated later re-activates`; `reinstated earlier does not`.
- [ ] RED → GREEN → commit `desk: stop rules the owner wrote fire at the close, flatten at the next open, and never reset themselves`.

### Task 29: The shadow record

As finish-plan Task 61, scoped: `desk/shadow.ml`/`.mli`, `/api/research` `against_live`, the page, `test/test_shadow.ml` with the hand-derived values (SPY at weight 1 with marks 100 → 101 → 99.99: +1.00 %, −1.00 %, compounded −0.01 %, hit 50 %, drawdown 1.0 %). Commit `desk: the shadow record -- advisory targets on the next session's marks, beside the live record`.

### Task 30: VaR coverage on the live record

As finish-plan Tasks 55, 57, 59 scoped to what exists: `desk/validation.ml`/`.mli` keyed on the estimator name, `Var_backtest.conditional_coverage` lifted (ruling 21; `run`'s fields unchanged to the bit), loss_t = E_{t−1} − E_t + F_t with F_t = 0 on paper and a `cash_flow_source: "none"` field, `/api/desk/validation` and the Execution page's "Session n of 250", no verdict before 60. Tests: a hand-built 70-session record with 4 exceedances at 95 % → Kupiec statistic computed by hand in the comment; the Basel zone. Commit `desk: the live record scores its own VaR forecasts with the engine's coverage tests`.

### Task 31: `ohcamel replay DATE`

`bin/main.ml` mode; prints the date's overlays, signals (judgement, tier), sizings, orders, fills, flattens and stops from the journal in time order; `test/test_desk_routes.ml`-style test on an in-memory journal pins the exact lines. Commit `desk: ohcamel replay prints a session's decisions in order`.

### Task 32: Stage 3 close

Suites, review, status, merge, push, deploy outside the session. Ledger.

---

## Stage 4 — EXP-Q01's portfolio and short legs

Branch `auto/s4-q01`.

### Task 33: Short legs

**Files:** `desk/rebalance.ml` (a negative target is a short: `order = target − current` with `side` by sign; `grows` already handles crossing zero), `desk/oms.ml` (nothing: `Order.Side.Sell` beyond flat is what Alpaca receives), `desk/sim_venue.ml` (negative positions), `desk/alpaca_paper.ml` (the position parser's `side: short` → negative qty, if not already), `test/test_rebalance.ml`, `test/test_sim_venue.ml`.
- [ ] Tests: `target −10 from 0 → Sell 10`; `from +5 → Sell 15 and grows`; `from −10 to 0 → Buy 10 and shrinks`; `a short that breaches book-cap is refused by the gate naming it` (there is no long-only flag; the book's limits are the control).
- [ ] Commit `desk: short targets are sells beyond flat, gated like every other leg`.

### Task 34: `signals.q01`

`quant/src/ohcamel_quant/signals/q01.py`: kind `signals.q01`, `0 7 2 * *`, from the newest `models.xs_lgbm` artifact's `scores` table: top quintile +1/(2k), bottom quintile −1/(2k), `validation` from the artifact's verdict (`FAIL` → `fail`), a v1 document `q01_ls_quintile-<as_of>.json`; roster row; book row `(name q01_ls_quintile) (symbols (…34…)) (cadence daily) (adjust open)`; tests on a fixture scores table (7 long at +0.0714, 7 short at −0.0714, Σ|w| = 1.0 within 1e-9). Commit `quant: signals.q01 emits the ranker's monthly long-short quintiles`.

### Task 35: Stage 4 close

Suites, review, status, merge, push, deploy outside the session; owner adds the Q01 row to the droplet's book.

---

## Stage 5 — Unattended: the watchdog, liveness, alerts, the runbook, the soak

Branch `auto/s5-unattended`.

### Task 36: The trade-updates watchdog

As finish-plan Task 42, verbatim: `desk/trade_updates.ml`, a ping every 60 s while the venue clock says open, reconnect and reconcile on a missing pong within 30 s, three virtual-clock cases in `test/desk_async`. Commit `desk: a liveness watchdog on the trade-updates stream`.

### Task 37: Liveness of the producers, and the alert kinds

**Files:** `deploy/watch.sh` (in session: `overlay.age_s` from `/api/desk` above 1 800 → a warning line; `strategy_state` STOPPED count printed), `web/research.js` (a strategy whose newest document is older than its `max_age` and the overlay when its `state` is `stale` or `absent` are hatched — both facts the desk itself holds; the page never reads the worker), `lib/alerts.ml`/the desk's alert writer: kinds `overlay_zero`, `overlay_stale`, `incubation_stopped`, `wave_timeout` reach the configured sinks (Log, Slack when set) with hysteresis not applicable (edge events, once each). Tests: the sink receives one line per event; `watch.sh` dry-run cases in `deploy/test`.
- [ ] Commit `deploy: the watch reads the overlay's age and the stopped count; the desk's new alert kinds reach the sinks`.

### Task 38: The runbook, the status, the README

**Files:** `docs/runbooks/autonomous.md` (what runs when, how to read a session with `replay` and `desk.report`, raising the cap after the canary, stopping everything: `(trading disabled)` + restart, or `POST /api/desk/kill`; reinstating a stopped strategy; adding an intraday strategy after a verdict), `docs/status.md` (the *Autonomous trading* section complete: roster, caps, the record's state, limits), `README.md` (*Signals and research*: the three tiers, the charter's amended sentence quoted), `docs/superpowers/specs/2026-10-11-autonomous-paper-trading-design.md` §8 departures if any.
- [ ] Commit `docs: the autonomous desk's runbook, status and README`.

### Task 39: Stage 5 close — review, merge, publish, soak

- [ ] Whole-program review (a fresh reviewer, the most capable model) over `main..auto/s5-unattended`; fix wave; re-review.
- [ ] Merge, push, deploy both hosts outside the session.
- [ ] Ten-session soak: each session's `desk.report` read; the ledger records fills, refusals, overlay path, any stop, peak RSS of `ohcamel-live` (≤ 512 MB) and the worker; any wave timeout investigated the same day.
- [ ] Owner step: raise `incubation_cap` to 0.60 after the canary sessions (or earlier, at their word), restart outside the session.
- [ ] The plan's status line in `docs/superpowers/plans/README.md` becomes "DONE <date>".

## Acceptance

1. On the live host, with the owner's book enabling trading at the canary cap, a session passes in which: at least one incubated strategy's document is judged `accepted_incubation`, sized, and its orders fill; the overlay's value changes during the session and an `adjust` sizing row follows it; `/api/research` shows every roster row with `tier: incubation` and the charter sentence; `replay DATE` reproduces the day.
2. Every EXP-I0x the owner approved has a quoted verdict in its model card and, at the owner's word, a roster row; a `horizon close` strategy is flat by 15:50 on a full session and its refusal is journaled on a half day.
3. After 60 sessions of any strategy, its record block prints Sharpe, PSR, drawdown, hit rate, turnover and cost, under the words INCUBATION — not evidence of edge.
4. A stop rule has fired at least once in tests on a virtual clock, and no code path un-stops.
5. `grep -rn 'sizing live' book.example.sexp research/ quant/ desk/ lib/` prints only documentation that says the owner sets it.
