# Phase A3: signals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring OhCamel Alpha's signal contract and research layer into this repository, run one pre-registered strategy — Faber (2007) on SPY and TLT — through the charter's full battery on real data, and let the desk read signals, judge them R1–R7, show them, and size only what passes the gates *and* the owner has promoted.

**Architecture:** Research is Python (uv), over `fdq`'s validation library, and writes signal files. Judgement and execution are OCaml, in the desk library. The two meet at one directory of JSON files and one schema. Python never trades; OCaml never trusts Python's clock. A signal that passes R1–R7 is still only *advisory* until the owner sets its strategy to `live` in `book.sexp`; a model does not promote itself.

**Tech Stack:** OCaml 5.2.1, Core/Async, alcotest, SQLite; Python ≥ 3.12 with uv, pandas, pyarrow, jsonschema, pytest, ruff; `fdq` pinned at `git+https://github.com/ajaiupadhyaya/capitallimits.git@652474c02b4919ed9842ec2d9510c6327b92f874`.

**Spec:** `docs/superpowers/specs/2026-09-12-the-desk-design.md` §3.12 (signals), decisions Q1 and Q6, and §5's A3 row: *accepted when the hypothesis is committed before the run, a verdict is led by the gates, and an advisory signal is shown with its rule*. The governing methodology is Alpha's `docs/CHARTER.md`, ported in Task 5 and authoritative for every research task.

**Source repository:** `~/Documents/ohcamel-alpha` at `d972f7c` (clean). "Ported" below means copied from there, with the package renamed as Task 6 states, and then made to pass here.

## Global Constraints

- **The charter governs.** No lookahead: a signal computed at the close of day *t* is actionable at the open of *t+1*. Realistic execution: every fill pays spread and commission, and the backtest and the simulator read the same cost configuration. Walk-forward and out-of-sample; Sharpe, drawdown, turnover and capacity reported, not returns alone. Multiple testing corrected. Seeded and reproducible. **No synthetic market data wherever a number is reported.**
- **The gates, applied literally:** holdout positive; DSR ≥ 0.30; PSR ≥ 0.70; PBO reported and high values named; stationary-block bootstrap (~1000 resamples) lower 5th percentile > 0; positive in ≥ 3 regimes; cost sensitivity reported at 0 / 5 / 15 / 30 bps. **No gate is loosened, ever, by any task.**
- **The owner owns hypotheses and kill decisions.** No task invents a signal, tunes a parameter after seeing a result, or promotes a strategy. The code builds infrastructure and red-teams.
- **Paper only, by construction.** Nothing here changes the venue: the trading host is still the constant `paper-api.alpaca.markets` and keys must begin `PK`.
- **The kernel may not name the desk.** `grep -rE 'paper-api\.alpaca\.markets|/v2/orders|Sqlite3|ohcamel_desk' lib/` prints nothing. Contract judgement, intake and sizing live in `desk/`.
- **Journal before wire; one submit site; every order through the rules and the gate.** A rebalance is proposed through the same path as a ticket.
- **The delimiters.** `grep -rF '|ohcamel_html}' web/` and `grep -rF '|ohcamel_json}' web/` print nothing; any file embedded at build time is checked the same way.
- **Text only, never HTML** for server-supplied strings on the page. Every element lookup returns early when absent.
- **Counts.** OCaml main suite 454 and scheduler suite 7 at the start. Each task updates `lib/verified.ml` and the three count sentences (`README.md`, `docs/overview.md`, `docs/status.md`) when an OCaml count moves. The Python suite is reported separately and is not in `verified.ml`. Coverage is re-measured once by the controller after the whole-branch review.
- **Never read or print a credential.** No `.env`, `deploy/.env`, `/etc/ohcamel`, `~/.claude.json`. No task needs Alpaca keys: the ten-year data is already on disk with provenance.
- **Commit hygiene.** Stage by name; never `git add -A` or `git add .`. Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp`, `.playwright-mcp/`, `_coverage/`, any `.env`, or anything under `research/experiments/*/results/` except the per-strategy `manifest.*.json` files and `report.md`. Every commit message ends with a blank line and exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Rulings that bind this phase

1. **The owner's EXP-A01 decision (2026-09-18).** Before pre-registering, the controller found that `capitallimits` EXP-002 had already run trend-following on SPY over 2016-06-01 → 2022-05-31 (`ma_crossover`: 36 trials, Sharpe 0.67, DSR 0.73, PBO 0.725; `donchian`: 8 trials, DSR 0.917, PBO 0.253). The drafted line "registered before any run on this data window" was therefore false for SPY. The owner chose **disclose, deflate, clean holdout**:
   - the pre-registration names EXP-002 and its result;
   - SPY's DSR deflates by every trial in the family. Ruling 1b settles the unit;
   - parameter selection and every walk-forward fold use only 2016-06-01 → 2022-05-31;
   - the holdout 2022-06-01 → 2026-06-01, which no experiment has touched, is evaluated once with the selected parameters, and the "holdout positive" gate reads it alone;
   - everything else in the draft stands: fast 1, slow ∈ {150, 200, 250}, any gate failing is a fail, and PBO > 0.5 is "pass, fragile" and not promoted without a second window.
1b. **The owner's second decision (2026-09-18): DSR counts fold-trials.** The plan review found that fdq's `deflated_sharpe(returns, trial_sharpes)` takes the actual trial Sharpes, not a count, and that "47" mixed units: EXP-002's 44 counted configurations × folds, while EXP-A01's 3 counted configurations. The owner chose consistent fold units: **SPY 56** (EXP-002's 44, recovered by re-running its two SPY grids over the selection window with the same code, data, friction and seed, plus EXP-A01's 3 × 4 = 12) and **TLT 12**. This is stricter than 47.
1c. **The data, stated exactly.** The bars are consolidated (SIP), not IEX alone, and unadjusted (`close_adj == close`), so returns exclude distributions: about 3% a year on TLT. That biases the test against the rule. The pre-registration says so before the run.
2. **Promotion is the owner's, and it defaults off.** The spec says a passing signal's weights become targets. The charter adds that the owner owns kill decisions, and quant-rigor that a model does not promote itself. So each registered strategy carries `(sizing advisory)` or `(sizing live)` in `book.sexp`, default `advisory`. A signal is sized only when it passes R1–R7 **and** its strategy is `live`. A passing signal on an `advisory` strategy is shown with its weights and never sized. No task sets any strategy to `live`.
3. **R8 is not enforced**, as §3.12 says, until its hash recipe stops depending on how two languages print a float. The page says so.
4. **Research gets a page of its own.** W2 deferred it here. §4 lists strategies, manifests, gates, advisory signals and backtest against live. "Against live" needs sessions that do not exist yet, so the page states that honestly and A5 fills it.
5. **Alpha is archived with a pointer** (Q1). Task 17 pushes a pointer commit to Alpha's README. The GitHub "archive" setting is the owner's to switch.
6. **The cross-language check is by shared examples, not by subprocess.** Alpha's `test_cross.py` ran the OCaml binary from Python. Here both languages read the same `interface/examples/expected.json`. The OCaml test checks each example's `core` verdict: the first failing rule, or pass. The Python test checks each example's `schema_valid`. Between them, every column of the oracle is checked, and neither language invokes the other.
7. **The plan review's engineering findings are settled in the tasks themselves.** Each is recorded in the ledger and in the task it changes: the market-on-open window and its mark (Task 15), the universe (Task 14), the slugs (Tasks 12, 14, 16), content-hash staleness and the frozen battery (Task 10), stale as `unvalidated` (Task 12), per-strategy manifests (Tasks 10, 13, 17), the vendored friction file and the cost-sweep semantics (Tasks 8, 11), one cost configuration (Task 15), turnover and capacity (Tasks 9, 11), R5's baseline and R3 deferral (Task 14), malformed files (Task 14), advisory semantics (Task 14), the strategy's own symbols and positions (Tasks 14, 15), the factored submit with per-submit re-checks (Task 15), and the images' ignore files (Tasks 16, 17).

## File Structure

```
docs/CHARTER.md                         NEW   ported verbatim; the governing methodology
docs/superpowers/specs/2026-09-02-ohcamel-alpha-design.md   NEW  ported, provenance
docs/superpowers/plans/2026-09-02-phase-1-validated-signal.md NEW ported, provenance; the code Tasks 8-12 adapt
interface/                              NEW   signal.schema.json, README.md (rules R1-R8), examples/*.json, examples/generate.py
desk/contract.ml, desk/contract.mli     NEW   R1-R7, pure, with the clock as a parameter
test/test_contract.ml                   NEW   ported; plus expected.json as the shared oracle
research/                               NEW   uv project; package `ohcamel_research` (renamed from `alpha`)
  src/ohcamel_research/{contract,replay,signal,cli}.py        ported
  src/ohcamel_research/battery/{gates,manifest,run}.py         NEW (Tasks 8-10)
  src/ohcamel_research/service.py                              NEW (Task 15)
  tests/                                                       ported + new
  experiments/EXP-A01/{hypothesis.md,config.yaml}              NEW (Task 7), committed before any run
  experiments/EXP-A01/{manifest.exp_a01_spy.json,manifest.exp_a01_tlt.json,report.md}  GENERATED, COMMITTED (Task 13)
  config/friction_v1.yaml                                      VENDORED from capitallimits@652474c (Task 8)
fixtures/bars/                          NEW   Alpha's 2018-2020 slice, with sidecars (tests)
fixtures/history/                       NEW   nine ETFs 2016-06-01 -> 2026-06-01 from the capitallimits cache, with sidecars
fixtures/macro/                         NEW   FRED's VIX series for fdq's spread widening, with its sidecar
desk/intake.ml                          NEW   read signals/, judge, journal, serve (Task 13)
desk/rebalance.ml                       NEW   targets, the rebalance, market-on-open (Task 14)
deploy/research.Dockerfile              NEW   (Task 16), with its own .dockerignore
web/research.html, web/research.js      NEW   (Task 16)
```

Changed: `desk/oms.ml`, `desk/rules.ml`, `desk/halt.ml`, `desk/order.ml`, `desk/journal.ml`, `desk/desk.ml`, `desk/desk_routes.ml`, `lib/config.ml` (the book's `signals` block), `lib/graph.ml` (the signals band), `bin/main.ml`, `deploy/docker-compose.yml`, `deploy/smoke.sh`, `.github/workflows/ci.yml`, `Makefile`, `.gitignore`, `web/nav.html`, `web/graph.js`, `web/page.css`, `lib/dune`, the documents.

---

## Pre-flight: what A2 left for this phase

A2's whole-branch review and its fix wave parked nine items for A3's start, with reasons recorded in `.superpowers/sdd/2026-09-12-desk-a2-orders/progress.md`. Three of them — the halt the engine imposes, the gate counting resting orders, and a failed order that turns out to be resting — matter more now, because Task 14 lets the desk place orders without a person pressing a button.

### Task 1: The order manager's three carried fixes

**Files:** `desk/oms.ml`, `desk/rules.ml`, `test/desk_async/test_desk_async.ml`, `test/test_rules.ml` or `test/test_oms.ml`, `lib/verified.ml`, the count sentences.

**What must become true:**
1. **The post-quote book-currency re-check bites.** `propose` re-checks the book's currency after the arrival quote (`oms.ml`, the re-check after the quote; around :606 at A2's end). No test fails if it is deleted, because the scheduler fixture's manager hard-codes `book_is_current`. Add a scheduler case that builds its own manager with `book_is_current` reading a `ref`. The ref is `true` when the proposal starts; the case flips it to `false` while the quote is in flight, then asserts the order is refused with the trading rule named and that nothing reached the venue. **Scheduler count 7 → 8**: `lib/verified.ml`'s `scheduler_tests` and every sentence saying "seven scheduler cases".
2. **A failed order the venue reports as open is cancelled.** When the desk has declared an order `Failed` (its lookups ran out) and the venue later reports it open under its kept venue id — through `on_update` or a reconcile — the desk cancels it by that id, in any switch state, and writes an event line saying so.
   - A cancel is never a resend, so invariant 10 is untouched.
   - The desk has given up on the order, and leaving it resting would be an order nothing manages.
   - Test it with the simulated venue: an order declared failed, then reported open, gets exactly one DELETE.
3. **The session rule reads the clock, not a minute-old flag.** `refresh` stores the venue clock's `is_open` flag once a minute, so an order proposed up to a minute after the close still passes. Store the clock's `next_open` and `next_close` instead, and compute "open" at proposal time as `next_close` in the future and `next_open` not yet reached — whichever form the venue's clock payload supports. Test on both sides of the close with a fixed clock.

**Verify:** `make build`, `make test` (454 main, 8 scheduler, plus whatever this task adds to main), `dune build @fmt`, the `lib/` grep. **Commit** as `desk:` with the reason.

### Task 2: The halt the engine imposes, and the switch's words

**Files:** `desk/halt.ml`, `desk/desk_routes.ml`, `desk/oms.ml` (only if the halt's callers need it), `bin/main.ml`, `desk/desk.ml` (the frame's `kill_switch` name), `web/desk.js`, tests.

**What must become true:**
1. **The engine has a halt of its own.** `Halt.State` gains a state for a halt the engine imposed — `Stopped of { why : string; at : Time_ns.t }`, or equivalent. It differs from the hand halt in two ways:
   - its reason reads "stopped by the engine: …", never "halted by hand";
   - `Halt.reset` does **not** clear it, because the thing that stopped still has not recovered.

   Only a restart clears it. The halt `bin/main.ml` raises when the venue's order updates stop moves to this state.
   - The reset route answers **409**, with a fixed sentence, while the switch is stopped.
   - The frame's `kill_switch` name reads `"stopped"`, and the page's switch line explains that a restart clears it.
2. **A raise after the halt reaches the person who pressed halt.** The kill route answers 200 as soon as the halt is set, and cancels open orders under `don't_wait_for`. A synchronous raise after the halt shows up today only in the log. Carry it into the answer instead: `{"switch": …, "error": "<fixed sentence>"}`. The page shows that `error` as text beside the switch. Never put the exception's own text in the response.
3. **The switch line tells the truth while cancels are in flight.** The page says "open orders cancelled" the moment the switch is set. Until the frame's `open_orders` reaches zero, say "cancelling N open orders"; after that, "open orders cancelled".

**Tests:** `Halt` unit cases (reset leaves `Stopped` stopped; `Stopped` refuses new orders; a hand halt still resets); a route case (reset is 409 while stopped). **Verify** as Task 1; check the switch line in a browser on the demo. **Commit** as `desk:`.

### Task 3: The gate counts what is resting

**Files:** `desk/oms.ml`, `test/test_oms.ml` (and/or `test/desk_async/`), `README.md` (its "limits, stated" sentence about the gate not counting resting orders becomes false and must be rewritten), `lib/verified.ml`.

**What must become true.** `Gate.check graph ~fills` already takes a list. Today `preview` and `propose` pass only the proposal's own fills, so several resting orders can each pass and together breach a limit that nothing sees until they fill. Build the fill list as:
- every open order the desk owns, at its **remaining** quantity and its side, priced at its limit price if it has one and at the live mark otherwise;
- then the proposal's own fills.

This is "as if everything resting fills", the conservative reading of invariant 12. Do it for preview and propose alike, so a preview never says yes to what propose refuses.

**Tests:** two limit orders that each pass alone and together breach `tech-cap` — the first rests, the second is refused naming the limit; and a resting order that is cancelled frees the room again. Each quantity carries its derivation. **Commit** as `desk:`.

### Task 4: The journal's last index, and a fill-driven read that cannot stick

**Files:** `desk/journal.ml`, `desk/desk.ml`, tests.

**What must become true:**
1. **`/api/desk` reads fills by an index.** `Journal.recent_fills` sorts the whole fills table on every call, the last `/api/desk` query that grows with the journal. Add a `CREATE INDEX IF NOT EXISTS` on the column it orders by, additive and idempotent, leaving `schema_version` at 1. Extend the existing "the indexes exist on an existing file" case to include it.
2. **A raising restore cannot freeze fill-driven reads.** If a restore raises inside `after_fill`'s read, `fill_read` stays `Running` for the life of the process and fill-driven reads stop. Clear it on every exit path, and test that a read after a raising restore still runs.

**Commit** as `desk:`.

---

## The port

### Task 5: The contract, the charter, and the provenance

**Files:**
- Create `interface/`, copied from Alpha's `interface/`: `signal.schema.json`, `README.md`, and `examples/` including `generate.py` and `expected.json`.
- Create `desk/contract.ml` and `desk/contract.mli`, from Alpha's `core/lib/contract.ml`.
- Create `test/test_contract.ml`, from Alpha's `core/test/test_contract.ml`.
- Create `docs/CHARTER.md`, verbatim.
- Create `docs/superpowers/specs/2026-09-02-ohcamel-alpha-design.md` and `docs/superpowers/plans/2026-09-02-phase-1-validated-signal.md`, verbatim, for provenance.
- Modify `desk/dune` and `test/dune` if needed, `lib/verified.ml`, and the count sentences.

**The one change to the contract module.** Alpha's version builds its clock from `bars.ml`, the bars it has itself received. Here the clock must still be one the core built from its own data — "OCaml never trusts Python's timing". It becomes a **parameter**, so the module stays pure:

```ocaml
module Clock : sig
  type t
  val create : latest_bar:Date.t -> bars_after:(Date.t -> int) -> t
  (* latest_bar: the newest trading date the desk holds a close for.
     bars_after d: how many of the desk's trading dates fall strictly after d,
     which R4 counts instead of calendar days. *)
end
```

Every rule reads time only through `Clock.t`. Task 13 builds it from the journal's `sessions` table, whose rows are the closes the desk has recorded.

**R1–R7 exactly as Alpha states them. R8 is not implemented**, and `contract.mli`'s comment says why (§3.12). Rules apply in order and the first failure is reported; the tests pin that order, including "R6 before R7". Task 14 relies on it: a first failure of R6 means R1–R5 passed and R7 was not reached. Alpha's ported tests cannot be copied unchanged, because their paths and their clock differ; adapt them, and record every adaptation in the report.

**The shared oracle.** `test/test_contract.ml` loads `interface/examples/*.json` and `interface/examples/expected.json` — the dune stanza needs a `deps` on them — and asserts that every example's verdict matches `expected.json`. Task 6 makes the Python side assert against the same file. That is the cross-language check (ruling 6).

**Where it lives.** `desk/`, not `lib/`: judging a signal is the desk's business, and invariant 6 keeps the kernel from naming the desk. The `lib/` grep stays silent.

**Verify:** `make build`; `make test` with main up by the ported alcotest cases plus the oracle case; `dune build @fmt`; the `lib/` grep. **Commit** as `desk:` — "the signal contract comes home".

### Task 6: The research layer

**Files:**
- Create `research/` from Alpha's `research/`, renaming the package `alpha` to `ohcamel_research`: `src/`, `tests/`, `pyproject.toml`, `uv.lock` regenerated, `README.md`.
- Create `fixtures/bars/` from Alpha's `fixtures/bars/`, with its sidecars.
- Modify `Makefile` (a `research-test` target: `cd research && uv sync --locked --extra dev && uv run pytest && uv run ruff check`, so CI never rewrites `uv.lock`), `.github/workflows/ci.yml` (a Python job on ubuntu that runs that target), and `.gitignore` (`research/.venv/`, `research/experiments/*/results/`, keeping the per-strategy `manifest.*.json` files and `report.md`).

**The rename.** Package directory, every import, `pyproject.toml`'s `name` and `[project.scripts]` (the CLI becomes `ohcamel-research`), and `tool.hatch.build.targets.wheel.packages`. `grep -rnE "(^|[^a-z_])(import|from) alpha([^a-z_]|$)|alpha\." research/src research/tests` must find nothing (a plain `alpha` grep also matches "alphabetical"). `fdq` stays pinned at the same commit.

**Ruling 6 in the tests.** Replace Alpha's `test_cross.py`, which invoked the OCaml binary, with a Python test asserting every `interface/examples/*.json` against `interface/examples/expected.json` — the file Task 5's OCaml test reads. Point every path at the repository root's `interface/` and `fixtures/`.

**Verify:** `make research-test` green, reporting its count; `make build` and `make test` unchanged; `.github/workflows/ci.yml` still parses (`python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`). **Commit** as `research:` — "the research layer comes home".

---

## EXP-A01: one pre-registered rule, through every gate

The six tasks below adapt Alpha's unstarted Phase 1 plan (ported in Task 5 as `docs/superpowers/plans/2026-09-02-phase-1-validated-signal.md`), whose Tasks 2–7 carry complete code. "Alpha Task N" below means that document's Task N. Map its paths as Task 6 renamed them: `research/src/alpha/` becomes `research/src/ohcamel_research/`, and the CLI `alpha` becomes `ohcamel-research`. Everything in that plan binds unless this plan says otherwise. The owner's decision (ruling 1) is the one place it says otherwise.

### Task 7: Pre-register EXP-A01, before anything that could run it exists

**Files:** create `research/experiments/EXP-A01/hypothesis.md` and `research/experiments/EXP-A01/config.yaml`. Nothing else.

This task writes two files and commits them, and it runs nothing. **No runner code exists yet.** That is the point: the commit's timestamp precedes every line of the battery. The text below carries **both** of the owner's decisions (rulings 1 and 1b) and the plan review's data facts. The owner owns it, and an implementer changes no word of its substance.

`research/experiments/EXP-A01/hypothesis.md`:

```markdown
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
```

`research/experiments/EXP-A01/config.yaml`:

```yaml
id: EXP-A01
title: "Does the 10-month moving-average rule survive friction on liquid ETFs?"
friction: {version: "1.0.0", file: research/config/friction_v1.yaml}
macro: fixtures/macro/macro.parquet
mode: walkforward
n_folds: 4
strategies:
  - {slug: exp_a01_spy, ma_crossover: {symbol: SPY, grid: {fast: [1], slow: [150, 200, 250]}}, prior: EXP-002-SPY}
  - {slug: exp_a01_tlt, ma_crossover: {symbol: TLT, grid: {fast: [1], slow: [150, 200, 250]}}}
prior:
  EXP-002-SPY:
    rerun:
      - ma_crossover: {symbol: SPY, grid: {fast: [10, 20, 50], slow: [50, 100, 200]}}
      - donchian: {symbol: SPY, grid: {window: [20, 50]}}
    window: selection
    expected_fold_trials: 44
dsr: {unit: fold_trials}
capital_tiers: [50000]
selection_window: {start: "2016-06-01", end: "2022-05-31"}
holdout_window: {start: "2022-06-01", end: "2026-06-01"}
stress_multipliers: [1, 2, 5]
cost_sweep_bps_round_trip: [0, 5, 15, 30]
bootstrap: {resamples: 1000, method: stationary_block}
seed: 42
```

**Commit, alone:**

```bash
git add research/experiments/EXP-A01/hypothesis.md research/experiments/EXP-A01/config.yaml
git commit -m "$(cat <<'EOF'
research: pre-register EXP-A01 -- Faber's 10-month rule on SPY and TLT, disclosing what EXP-002 already showed about SPY, deflating by every trial in the family in one unit, stating the data exactly, and holding out 2022-06 onward untouched -- because a hypothesis written after a result is a description of the result

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

No test count changes.

### Task 8: The ten-year history, the VIX series and the friction file, with provenance

Alpha Task 2 fetched ten years from Alpaca. **That fetch is not needed**, and it would need keys this plan never reads. Everything is already on disk, with provenance.

**Files:**
- **`fixtures/history/`:** the nine ETFs (GLD, IEF, IWM, QQQ, SPY, TLT, XLE, XLF, XLK), each with its `.meta.json` sidecar, copied from `~/Documents/capitallimits/data/raw/`.
- **`fixtures/macro/`:** `macro.parquet` and its sidecar, also from `~/Documents/capitallimits/data/raw/`. It is FRED data, `synthetic: false`, and gets its own directory because `load_bars` would otherwise read it as a symbol.
- **`research/config/friction_v1.yaml`:** vendored from `~/Documents/capitallimits/config/friction_v1.yaml` **at the pinned commit `652474c`**, via `git -C ~/Documents/capitallimits show 652474c:config/friction_v1.yaml`, because fdq's wheel does not ship it.
- **`fixtures/history/README.md`:** where each file came from, the window, the fetch date, and every file's SHA-256 and row count, for the fixtures, the macro series and the friction file. It also says what these bars are: consolidated volumes, unadjusted, distributions excluded. A refresh is a new data version and makes every manifest built on the old one stale.

**Steps:**
1. Copy the files.
2. Assert every sidecar says `synthetic: false` and covers the window, and that each file's first and last dates match its sidecar.
3. Point Alpha Task 2's `fixtures doctor` at `fixtures/history/` and `fixtures/macro/`, and make it part of `make research-test`.

**Commit** as `research:`.

### Task 9: The four gates `fdq` does not compute, plus turnover and capacity

Alpha Task 3, as written: `psr_gate`, `bootstrap_gate` (stationary block, seeded), `regime_gate` and `cost_sweep`, each a pure function of return series. They go in `research/src/ohcamel_research/battery/gates.py`, with hand-derived tests in `research/tests/test_gates.py`.

**Two additions, which Task 13's report needs and `fdq` does not provide:**
- `turnover(weights) -> float` — annualised one-way turnover: the sum over days of |Δweight| ÷ 2, scaled by 252 ÷ days.
- `capacity(trades, adv_dollars) -> float` — the capital at which the median trade reaches 1% of that symbol's 20-day dollar ADV on its day. That is the desk's own participation rule. Per trade, capital = 0.01 × ADV ÷ |Δweight|; the function returns the median.

Each gets a hand-derived test.

**One table of thresholds.** No threshold appears in gate code except the charter's, and those are kept in one table that the report also reads, so a gate and its report can never disagree about the line.

**Commit** as `research:`.

### Task 10: The manifest, and when it is stale

Alpha Task 4 builds a `Manifest` dataclass with load and dump. **Its staleness rule is replaced**, because the image has no git and a date is too coarse:

- **One manifest per registered strategy:** `research/experiments/EXP-A01/manifest.exp_a01_spy.json` and `manifest.exp_a01_tlt.json`.
- **Fields, beyond Alpha's:**
  - `slug`;
  - `selected_params` — the parameters chosen on the selection window, which Task 16 computes the live signal from;
  - `selection_window` and `holdout_window`;
  - `dsr`: its unit, its trial count, its trial-Sharpe sources, and the return series it reads;
  - `verdict` and `verdict_line` — one sentence, which Task 17 embeds, so no image needs `report.md`;
  - `hashes`: the SHA-256 of every file under `research/src/ohcamel_research/battery/`, of `config.yaml`, of the friction file, of `fixtures/macro/macro.parquet`, and of every fixture it read.
- **Stale** means any recorded hash differs from the same file now. The check needs no git, so it runs in the image.
- **The runner refuses to start** if `battery/` has uncommitted changes (`git status --porcelain -- research/src/ohcamel_research/battery/` is non-empty). This is checked at run time, never in the image.
- **`battery/` is frozen after Task 13 for the rest of this phase.** Any later change to it makes the manifests stale, and re-running would evaluate the holdout a second time. That is a new experiment with a new id, disclosed as a second look, never a silent re-run.

**Tests:** round trip; stale on each kind of hash change; fresh when nothing changed.

**Commit** as `research:`.

### Task 11: The runner, with both of the owner's decisions

Alpha Task 5 builds `run(config) -> manifests` from `fdq`'s walk-forward plus Task 9's gates. Follow it, **with these changes, which are rulings 1 and 1b and are not optional**:

1. **Selection sees only the selection window.** Slice the bars to `selection_window` and run `fdq`'s `walk_forward` with `n_folds` on that slice alone. The selected parameters are the ones `walk_forward` chooses over the whole slice, recorded as `selected_params`.
2. **The holdout is run once.** Run a backtest with `selected_params` over `holdout_window`. Its moving average may read bars before the holdout's start to warm up; returns count only from the start. **A test asserts no return dated before `holdout_window.start` enters the holdout series.**
3. **"Holdout positive" reads the holdout series alone**, by Alpha's definition of the gate.
4. **DSR, in fold-trials:**
   - Call `fdq`'s `deflated_sharpe(returns, trial_sharpes)` **unchanged**.
   - `returns` is the walk-forward's out-of-sample series concatenated with the holdout series. Name it in the manifest.
   - `trial_sharpes`, for SPY, is EXP-002's re-run fold-trial Sharpes (the two grids in `config.yaml`'s `prior`, `walk_forward` over the selection window, same friction, macro and seed) concatenated with EXP-A01's own `wf.trial_sharpes`.
   - Assert the counts: **56 for SPY** (44 + 12) and **12 for TLT**. A mismatch against `expected_fold_trials` stops the run.
5. **The other gates** — PSR, bootstrap and regime — run on the same concatenated series. **PBO** is `fdq`'s CSCV over the selection window's configurations.
6. **The cost sweep, re-evaluated, never re-selected.** At each level in `cost_sweep_bps_round_trip`:
   - set the friction's `spread_bps_default` to that level and clear the per-symbol table;
   - keep the fees and the VIX widening;
   - rerun the backtests with `selected_params` over the walk-forward folds' test spans and the holdout;
   - report the net result at every level.
7. **Friction and macro.** Load the vendored `friction_v1.yaml` explicitly, never a relative default. Pass the VIX series from `fixtures/macro/` as `macro`; `None` is refused.
8. **Turnover and capacity.** Rerun each fold's backtest with its `fold_params` to get the positions and trades, and apply Task 9's functions. Report them per strategy.
9. **The verdict is mechanical.**
   - `pass` if every gate passes.
   - `pass, fragile` if every gate passes and PBO > 0.5.
   - `fail` if any gate fails, naming each gate that failed.

**Tests:** Alpha's smoke test on the 2018–2020 `fixtures/bars/` slice (shape, not values); the holdout-isolation test; a test that the trial-Sharpe count reaches `deflated_sharpe`; a test that a per-level cost run does not re-select; a test that `macro = None` is refused. **No test runs EXP-A01's real configuration** — that is Task 13.

**Commit** as `research:`.

### Task 12: A signal's validation block comes only from a manifest

Alpha Task 6 adds `validation_from_manifest(path) -> dict` and `ohcamel-research signal emit --validation-from PATH`. Two changes the plan review found necessary:

- **`emit` takes the document's `strategy` slug and `fdq`'s strategy key as separate arguments.** Today one argument does both jobs, so the two symbols would both emit as `ma_crossover`, with the same file name and sequence. Slugs must match the schema's `^[a-z][a-z0-9_]{1,63}$`, hence `exp_a01_spy` and `exp_a01_tlt`.
- **A stale manifest maps to the schema's existing `unvalidated` status.** The reason goes in the validation block's own fields, and the schema is not changed, so `schema_version` stays 1. A signal can carry `status: pass` only by copying a fresh manifest whose verdict is `pass` or `pass, fragile`. Hand-writing it is refused.

**Tests:** each path — fresh pass, fresh fail, stale, and hand-written refused — plus two strategies emitting two distinct documents.

**Commit** as `research:`.

### Task 13: Run EXP-A01 for real, and commit what it says

**Preconditions, checked first. If either fails, stop and report:**
- the commit that added `research/experiments/EXP-A01/hypothesis.md` is an ancestor of every commit touching `research/src/ohcamel_research/battery/`;
- `battery/` has no uncommitted changes.

**Steps:**
1. Run `ohcamel-research battery run research/experiments/EXP-A01` on `fixtures/history/`, seed 42.
2. Commit `manifest.exp_a01_spy.json`, `manifest.exp_a01_tlt.json` and `report.md`. Nothing under `results/` is committed.
3. The report **leads with the verdict and the gates**:
   - one table per strategy, one row per gate, with value, threshold and pass or fail;
   - PBO named if high;
   - the cost sweep at every level;
   - the holdout's own numbers apart from the walk-forward's;
   - Sharpe, max drawdown, turnover and capacity for each strategy;
   - and only then returns.
4. In its own section, the report states the EXP-002 disclosure, the DSR unit and counts (56 and 12), and the data facts: consolidated, unadjusted, distributions excluded.
5. It says plainly that the verdict does not promote anything, and that promotion is the owner's decision in `book.sexp`.
6. **Run it twice.** The second run's manifests must equal the first's, byte for byte, apart from timestamps. A difference is the finding, and it is reported.

**What this task may not do:** change the grid, the windows, a gate, a threshold, the friction, the macro series or the seed after seeing any number. A fail is a complete result, reported with the same confidence as a pass. After this commit, `battery/` is frozen for the phase (Task 10).

**Commit** as `research:` — "EXP-A01's verdict, led by its gates".

---

## The desk reads, judges, and — only when promoted — sizes

### Task 14: Intake — read, judge R1–R7, record, show

**Files:**
- `lib/config.ml`: the book's `signals` block.
- `book.example.sexp`: SPY and TLT added to the universe, plus the block.
- `test/test_example_book.ml`: now eight instruments, not six.
- `desk/intake.ml`.
- `desk/journal.ml` and `.mli`: the `signals` table's reader and writer, and a new `signal_files` table.
- `desk/desk.ml`: an `/api/research` extension.
- `bin/main.ml`: the directory and the minute loop.
- `deploy/smoke.sh`: `/api/research` in `EXPECTED_ROUTES`.
- Tests.

**The universe.** Every signal's symbols must be in the book's universe (R7), so `book.example.sexp` gains SPY and TLT at quantity 0, each with a sector, keeping the limits as they are.
- `test_example_book`'s count pin moves from 6 to 8, and any prose describing the example's six names changes.
- On the live host, quantities come from the paper account. Task 18 tells the owner their `book.sexp` needs both names.
- **The demo is exempt.** Its synthetic book (`lib/synthetic_book.ml`) holds the same six names, and adding two would move Figure 1 and the topology pins. So the demo registers no strategies, and its Research page shows the evidence and says no strategy is registered on the demo.

**The book's `signals` block**, optional, absent meaning no intake:

```lisp
 (signals
  ((strategies
    (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing advisory) (capital_fraction 0.5))
     ((name exp_a01_tlt) (symbols (TLT)) (max_age 3) (sizing advisory) (capital_fraction 0.5))))))
```

- **Two strategies, one per symbol**, because EXP-A01 validated each symbol separately as long or flat.
- **Validation:**
  - names match the schema's pattern;
  - every `symbols` entry is in the universe;
  - `capital_fraction` is in (0, 1];
  - the fractions of `live` strategies sum to at most 1;
  - `sizing` is `advisory` or `live`, default `advisory` (ruling 2).
- **`book.example.sexp` ships both strategies `advisory`.** Its comment says that promoting one is the owner's decision, made after reading its manifest. It also says that promotion needs `max_order_notional` to be at least the strategy's largest target order: at $100k of equity and a fraction of 0.5 that is about $50k, against a default of $25k. Otherwise the `notional` rule refuses every rebalance, visibly.

**The directory** comes from `OHCAMEL_SIGNALS_DIR`. With it unset there is no intake. Refuse to start if it is set but unreadable, naming the variable.

**Once a minute:**
1. **Candidates.** For each `*.json` file, skip it if its name is in `signal_files` (a malformed file already recorded) or if its `(strategy, sequence)` is already in `signals`.
2. **Parsing.** Parse it with `Contract`. **Any** parse failure — unreadable JSON or a missing field — is recorded once in `signal_files (name TEXT PRIMARY KEY, received_at TEXT NOT NULL, error TEXT NOT NULL)`, created `IF NOT EXISTS`, and never retried.
3. **The clock.** `Contract.Clock.t` comes from the journal's `sessions` table: `latest_bar` is the newest session date, and `bars_after d` counts session dates after `d`.
4. **R3 is deferred, never lost to a race.** If a file's `as_of` is after `latest_bar`, **do not record it yet**: re-examine it each minute. Once a session on or after its `as_of` exists, judge it normally. If `max_age` more sessions pass without one, record it `rejected` with R3. The session close can land after the service writes, retry, or be missed across a restart, and a single early look must not throw the day away.
5. **R5's baseline** is the highest sequence among this strategy's judgements with verdict `accepted` or `advisory`. Both passed R1–R5.
6. **The strategy's own symbols.** A document naming a target outside its strategy's registered `symbols` is `rejected`, rule `strategy`, checked after the contract passes. An `exp_a01_spy` signal must not size TLT.
7. **The verdicts:**
   - `accepted` — passes R1–R7 and its strategy is `live`;
   - `advisory` — the contract's first failure is R6, meaning R1–R5 passed and R7 was not reached, which is the contract's first-failure semantics, **or** it passes everything and its strategy is `advisory`. The detail says which.
   - `rejected` — any other first failure, with the rule named.
8. Every judgement is recorded in `signals` with its detail and the full document.

This task sizes nothing. Task 15 attaches to `accepted`.

**`/api/research`** (a desk extension; `lib/` stays silent) serves:
- the registered strategies with their `sizing` and `capital_fraction`;
- the latest judgement per strategy — verdict, rule, detail, `as_of`, weights and validation block;
- how many files are deferred;
- R8's fixed sentence: "R8 (data hash) is not enforced; see the design §3.12".

It is bounded and reads through indexes.

**Tests:** a file per verdict; R5 rejecting a **lower** sequence (a file with the same sequence is skipped as already judged, never re-judged); a malformed file and a parse failure each recorded once in `signal_files`; R3 deferral, first deferred and then judged when a session appears; R3 deferral ending as `rejected` after `max_age` sessions; a target outside the strategy's symbols rejected; `advisory` sizing; no directory means no intake; `/api/research`'s shape; the example book with eight names.

**Commit** as `desk:`.

### Task 15: Sizing, the rebalance, and market-on-open

**Files:** `desk/order.ml`, `desk/rules.ml`, `desk/alpaca_paper.ml` (the order body), `desk/sim_venue.ml`, `desk/rebalance.ml`, `desk/oms.ml`, `desk/intake.ml`, `book.example.sexp` (`spread_bps` for SPY and TLT), and tests in both suites.

**Time in force.** `Order.Request` gains `tif : Tif.t` with `Day | Opg`, default `Day`.
- It is journaled with the order. The orders table gets a `tif` column, added with a guard, because `ALTER TABLE ADD COLUMN` is not idempotent. Check the column exists first, then add it with `DEFAULT 'day'`. `schema_version` stays 1.
- Every existing path stays `Day`.
- Alpaca's order body carries `"time_in_force": "day"` or `"opg"`.

**When an `Opg` order is legal.** Alpaca rejects opening-auction orders sent between 09:28 and 19:00 ET and queues them after 19:00. So:
- `Opg` is legal only when the session is closed, **and** the time is at or after 19:00 America/New_York following the last close, **and** it is before `next_open − 2 minutes`.
- Otherwise the session rule refuses it with a sentence naming which boundary failed.
- The research service runs at 19:15 ET (Task 16).
- An `accepted` signal whose rebalance falls outside the window is refused, with that sentence in the judgement's detail. It is not held: the next day's signal carries the same target.

**The mark for an `Opg` order.** IEX prints stop about 17:00 ET, and the feed marks a name stale 90 s after its last print. So for `Opg`, the `mark` rule reads the **last recorded session close** for that symbol, from the journal's `marks` table for the newest session, not the feed's freshness. The notional cap, the ADV share and the collar are computed on that close. No close recorded means the order is refused.

**The simulated venue gains a closed phase** that a test controls, `Sim_venue.set_session`, with a clock whose `is_open`, `next_open` and `next_close` follow it. It holds an `Opg` order until the test opens the session, then fills it at the mark.

**Targets** (`desk/rebalance.ml`, pure). Targets are computed over **the strategy's registered `symbols`**, with an implicit weight of 0 for a symbol the signal does not name. Emit drops zero weights, so without this a signal that goes flat would sell nothing. For each symbol, with `capital_fraction f`, equity `E`, weight `w` and the `Opg` mark `p`:

    target  = trunc(w × f × E / p)                            (toward zero; whole shares)
    current = the strategy's OWN position: the net quantity of the desk's
              fills from orders whose source is signal:<slug>:*
    order   = target − current

- **`current` is never the account's whole position.** Going flat sells exactly what the strategy bought, and never a share the owner holds by hand.
- A zero `order` is no order.
- An unknown mark, an unknown equity or an unknown fill history produces **no targets and a reason**, never a guess.

**Tests:** hand-computed targets on both sides of a share boundary; a flat signal selling exactly the strategy's own shares while a hand-bought lot is left alone; a negative weight rounding toward zero.

**`Oms.propose_rebalance oms ~source ~orders`** goes through the same machinery as a ticket:
1. **Factor first.** Move `propose`'s journal → submit → record block into **one function** that both `propose` and `propose_rebalance` call, so there is still exactly **one submit site**. Split the gate call out of `preview`, so it can take many fills.
2. **The rules, per order.**
3. **One gate call** with all the rebalance's fills plus the resting orders (Task 3). A rebalance is gated as a unit.
4. **The journal.** Every order is written first, in the same sequencer job.
5. **Before each submit**, re-check the switch and the book's currency. `/api/desk/kill` sets the halt outside the sequencer, so a halt can land between two submits. If either check fails, every unsent order moves from `Pending_submit` to `Rejected_pre_trade`, which is a legal transition, with the reason.

If any order fails a rule, or the gate refuses, **nothing** is journaled or sent, and the refusal names the order and the rule or limit. `source` is `signal:<slug>:<sequence>`.

**On `accepted`** (Task 14), intake computes the targets and calls `propose_rebalance` once, and the outcome goes into the judgement's detail.

**One cost configuration.** `book.example.sexp`'s `spread_bps` for SPY and TLT is set to fdq friction v1's half-spreads, because the book's `spread_bps` is a half-spread while fdq's `spread_bps` is a full spread it halves per fill. A test reads `research/config/friction_v1.yaml` and asserts the two agree. The demo's simulated venue keeps its flat 5 bps, recorded in `docs/status.md` as a departure: the demo is never evidence.

**Tests:**
- **Main suite:**
  - the `Opg` window on both sides of 19:00 and of `next_open − 2 min`;
  - the `Opg` mark read from the recorded close, and refused with no close;
  - a rebalance refused as a unit when its second order breaches a limit, with nothing journaled;
  - an `advisory` strategy producing no order;
  - the Alpaca body carrying `opg`;
  - the `spread_bps` agreement;
  - the migration adding `tif` to an existing file.
- **Scheduler suite:**
  - a rebalance journals before the wire, and fills when the simulated session opens;
  - a halt set between two submits leaves the second order `Rejected_pre_trade` and unsent.

Name every new case in the count.

**Commit** as `desk:`. This is the first code in the project that can place an order without a person pressing a button. Its review is on opus and reads it that way.

### Task 16: The research service

**Files:** `research/src/ohcamel_research/service.py`, `research/tests/test_service.py`, `deploy/research.Dockerfile`, `deploy/research.Dockerfile.dockerignore`, `deploy/docker-compose.yml`, `deploy/smoke.sh`.

**The service** runs once per trading day at **19:15 America/New_York**. That is after the desk records the close (Task 14 defers anything that arrives first) and inside Alpaca's opening-auction window. For each strategy in its config (slug, fdq key, symbol, manifest path):
1. **Refresh** that symbol's bars through `fdq` from Alpaca. The service reads the data keys from its environment; no human or agent does. Fetch enough history for the longest moving average plus a margin.
2. **Compute** the rule on the latest bar with the **manifest's `selected_params`**, never a new choice: weight 1 if the price is above its SMA, else 0.
3. **Emit a signal**:
   - `strategy` is the slug;
   - `as_of` is the latest bar's date;
   - `sequence` is that date as `YYYYMMDD`, so it strictly increases per strategy;
   - the validation block comes from `validation_from_manifest`, using the manifests baked into the image.
4. **Write it atomically** to `/signals/<slug>-<as_of>.json`: write `<name>.tmp`, fsync, then rename. A second run on the same day finds the file and writes nothing.

**Tests** are hermetic. The fetch sits behind an interface with a fake backed by `fixtures/bars/`, and the clock is injected:
- a day's run writes two distinct valid documents, one per strategy;
- each weight matches the rule on the fixture, computed by hand;
- the write is atomic: no `.tmp` is left, and a crash between write and rename leaves no `.json`;
- re-running on the same day is a no-op;
- a stale manifest emits `unvalidated`.

**Deploy:**
- **`deploy/research.Dockerfile`:** `python:3.12-slim` plus `uv`. Copy `research/` (including its `README.md`) and `interface/`, then `uv sync --locked`. `fdq` comes from GitHub at its pin, which is public and needs no credentials.
- **`deploy/research.Dockerfile.dockerignore`:** its own ignore file, re-including `research/README.md` and the manifests. The engine's `.dockerignore` excludes `*.md`.
- **Compose:**
  - `ohcamel-research` goes in `profiles: [live]`, with the live engine's `env_file` and a named volume `signals` mounted read-write at `/signals`. It reads only the data keys; the trading keys also in that file are unused, which is recorded as a known Minor.
  - The live engine mounts the same volume **read-only** at `/data/signals` and gets `OHCAMEL_SIGNALS_DIR=/data/signals`.
  - No `ports:`. The Caddyfile is untouched.
- **The smoke suite:** `/api/research` answers on the demo and returns 401 without credentials on the live host.

**Verify:**
- `make research-test` is green;
- `docker compose -f deploy/docker-compose.yml config` parses, if Docker is available; if it is not, say so;
- `make build` and `make test` are unchanged.

**Commit** as `deploy:`.

### Task 17: The Research page, the sixth link, and the signals band

**Files:**
- `web/research.html` and `web/research.js`;
- `web/nav.html`: the sixth link, between Execution and Argument;
- `lib/dune`: the `research_html.ml` rule;
- `desk/dune`: a rule embedding the two manifests **by name**, because dune's `glob_files` cannot match across directories;
- `lib/server.ml`: the `/research` route;
- `lib/graph.ml` and `web/graph.js`: the `signals` band;
- `web/page.css`, `deploy/smoke.sh`, and the tests that pin routes, pages and topology.

**The page** reads `/api/research` and the embedded manifests, and computes nothing. In order:
1. **Strategies:** each registered strategy's symbols, its `sizing` in words, and its `capital_fraction`.
2. **Latest signal:** per strategy, `as_of`, weights and verdict: `accepted`, `advisory` with the rule or the sizing named, or `rejected` with the rule. Also how many files are deferred.
3. **Evidence:** each manifest's gate table (value, threshold, pass or fail), its `verdict_line`, the cost sweep at every level, turnover and capacity, and the DSR's unit and counts. It is read from the manifest's JSON, so nothing needs `report.md`.
4. **Against live:** one sentence saying the desk's own record will be compared with the backtest once enough sessions exist, which is A5.
5. **R8:** the fixed sentence.

On the demo the page says no strategy is registered there and shows the evidence. Everything goes in as text. Every lookup is guarded. The page works in both schemes and at 380 px.

**The signals band.** `Topology.Outside` gains a `signals` entry when a desk with intake is attached. It is drawn as a short stub into the `orders` entry, in that stub's style. The figure with no desk stays byte-identical.

**Routes.** `/research` goes after `/execution` in the route table, the test pin and `EXPECTED_ROUTES`, with a 200 probe on the demo and a 401 on the live host, and a dispatch assertion that its body carries its own id.

**Commit** as `web:`.

### Task 18: The documents, the smoke suite, the count — and Alpha's pointer

**Files:** `README.md`, `docs/overview.md`, `docs/status.md`, `interface/README.md` (it names OhCamel as its consumer now), `lib/verified.ml`, and in `~/Documents/ohcamel-alpha`: `README.md`.

1. **What is true now.** Every sentence about signals, research, EXP-A01, sizing, the Research page and the six-page site. Say "advisory" and "live" exactly as the code does. **The EXP-A01 verdict is quoted from its report, not paraphrased**, with its gates. Never describe a strategy as trading unless the live host's `book.sexp` says `live`, which no task sets.
2. **`docs/status.md`:** the inventory gains the research service and the Research page. "Next" drops A3 and keeps A4, A5 and A6. The acceptance row "an advisory signal shown with its rule" is recorded as met, on the demo or the live host as appropriate.
3. **Alpha's pointer.** In `~/Documents/ohcamel-alpha`, add a short section to the top of `README.md`: the repository is archived, its contract, research layer and Phase 1 plan now live in OhCamel (link), and nothing here is maintained. Commit it there, with the same trailer, and push it to its `origin`. **The GitHub "archive" setting is not changed**; the final report tells the owner it is theirs to switch.
4. **The smoke suite** against a local demo: every probe passes, including `/research` and `/api/research`.
5. **What the owner must do on the live host, stated in `docs/status.md` and in the final report**, because no task may change the owner's `book.sexp`:
   - add SPY and TLT to the live book; copying the new `book.example.sexp` does this;
   - leave both strategies `advisory` until they have read the manifests;
   - before promoting one, raise `max_order_notional` to at least its largest target order, or the `notional` rule refuses every rebalance, visibly;
   - redeploy with the `live` profile, so that `ohcamel-research` runs.

**Verify:** `make build`, `make test`, `make research-test`, `dune build @fmt`, and the three greps. **Commit** as `docs:`.

---

## Acceptance

1. `make build` clean; `make test` green at the counts `lib/verified.ml` states; `make research-test` green; `dune build @fmt` clean; the three greps silent.
2. `research/experiments/EXP-A01/hypothesis.md`'s commit is an ancestor of every commit touching the battery code (Task 13's precondition), and of both manifests'.
3. Both manifests reproduce byte for byte apart from timestamps, and neither is stale: every recorded hash matches.
4. The report leads with the verdict and the gates. It discloses EXP-002, states the DSR unit and counts (56 and 12) and the data facts, and states that nothing is promoted.
5. The spec's acceptance, "an advisory signal shown with its rule", holds in three places:
   - **Hermetically:** a test renders an `advisory` judgement with its rule or its sizing named, through `/api/research` and the page.
   - **On the demo:** `/research` shows both strategies as `advisory` and the EXP-A01 evidence.
   - **On the live host, after the owner deploys and the service's first post-close run:** the real signal appears as `advisory` with its reason. That last one is the owner's to see, and the final report says so.
6. No strategy is `live` anywhere in the repository.
7. `deploy/smoke.sh http://localhost:8099` passes every probe.
8. Alpha's pointer is pushed; the archive switch is left to the owner.
