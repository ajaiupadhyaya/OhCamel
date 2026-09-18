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
- **Commit hygiene.** Stage by name; never `git add -A` or `git add .`. Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp`, `.playwright-mcp/`, `_coverage/`, any `.env`, or anything under `research/experiments/*/results/` except `manifest.json` and `report.md`. Every commit message ends with a blank line and exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Rulings that bind this phase

1. **The owner's EXP-A01 decision (2026-09-18).** Before pre-registering, the controller found that `capitallimits` EXP-002 had already run trend-following on SPY over 2016-06-01 → 2022-05-31 (`ma_crossover`: 36 trials, Sharpe 0.67, DSR 0.73, PBO 0.725; `donchian`: 8 trials, DSR 0.917, PBO 0.253). The drafted line "registered before any run on this data window" was therefore false for SPY. The owner chose **disclose, deflate, clean holdout**:
   - the pre-registration names EXP-002 and its result;
   - SPY's DSR deflates by EXP-002's 44 prior trend trials plus EXP-A01's own 3, so 47; TLT's by 3;
   - parameter selection and every walk-forward fold use only 2016-06-01 → 2022-05-31;
   - the holdout 2022-06-01 → 2026-06-01, which no experiment has touched, is evaluated once with the selected parameters, and the "holdout positive" gate reads it alone;
   - everything else in the draft stands: fast 1, slow ∈ {150, 200, 250}, any gate failing is a fail, and PBO > 0.5 is "pass, fragile" and not promoted without a second window.
2. **Promotion is the owner's, and it defaults off.** The spec says a passing signal's weights become targets. The charter adds that the owner owns kill decisions, and quant-rigor that a model does not promote itself. So each registered strategy carries `(sizing advisory)` or `(sizing live)` in `book.sexp`, default `advisory`. A signal is sized only when it passes R1–R7 **and** its strategy is `live`. A passing signal on an `advisory` strategy is shown with its weights and never sized. No task sets any strategy to `live`.
3. **R8 is not enforced**, as §3.12 says, until its hash recipe stops depending on how two languages print a float. The page says so.
4. **Research gets a page of its own.** W2 deferred it here. §4 lists strategies, manifests, gates, advisory signals and backtest against live. "Against live" needs sessions that do not exist yet, so the page states that honestly and A5 fills it.
5. **Alpha is archived with a pointer** (Q1). Task 17 pushes a pointer commit to Alpha's README. The GitHub "archive" setting is the owner's to switch.
6. **The cross-language check is by shared examples, not by subprocess.** Alpha's `test_cross.py` ran the OCaml binary from Python. Here both languages test themselves against the same `interface/examples/expected.json`, so they agree with each other without either invoking the other.

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
  experiments/EXP-A01/{manifest.json,report.md}                GENERATED, COMMITTED (Task 12)
fixtures/bars/                          NEW   Alpha's 2018-2020 slice, with sidecars (tests)
fixtures/history/                       NEW   nine ETFs 2016-06-01 -> 2026-06-01 from the capitallimits cache, with sidecars
desk/intake.ml                          NEW   read signals/, judge, journal, serve (Task 13)
desk/rebalance.ml                       NEW   targets, the rebalance, market-on-open (Task 14)
deploy/research.Dockerfile              NEW   (Task 15)
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

**R1–R7 exactly as Alpha states them. R8 is not implemented**, and `contract.mli`'s comment says why (§3.12). Rules apply in order and the first failure is reported; the tests pin that order.

**The shared oracle.** `test/test_contract.ml` loads `interface/examples/*.json` and `interface/examples/expected.json` — the dune stanza needs a `deps` on them — and asserts that every example's verdict matches `expected.json`. Task 6 makes the Python side assert against the same file. That is the cross-language check (ruling 6).

**Where it lives.** `desk/`, not `lib/`: judging a signal is the desk's business, and invariant 6 keeps the kernel from naming the desk. The `lib/` grep stays silent.

**Verify:** `make build`; `make test` with main up by the ported alcotest cases plus the oracle case; `dune build @fmt`; the `lib/` grep. **Commit** as `desk:` — "the signal contract comes home".

### Task 6: The research layer

**Files:**
- Create `research/` from Alpha's `research/`, renaming the package `alpha` to `ohcamel_research`: `src/`, `tests/`, `pyproject.toml`, `uv.lock` regenerated, `README.md`.
- Create `fixtures/bars/` from Alpha's `fixtures/bars/`, with its sidecars.
- Modify `Makefile` (a `research-test` target: `cd research && uv sync --extra dev && uv run pytest && uv run ruff check`), `.github/workflows/ci.yml` (a Python job on ubuntu that runs that target), and `.gitignore` (`research/.venv/`, `research/experiments/*/results/`, keeping `manifest.json` and `report.md`).

**The rename.** Package directory, every import, `pyproject.toml`'s `name` and `[project.scripts]` (the CLI becomes `ohcamel-research`), and `tool.hatch.build.targets.wheel.packages`. `grep -rn "alpha" research/src research/tests` must then find only the history the README tells, never an import. `fdq` stays pinned at the same commit.

**Ruling 6 in the tests.** Replace Alpha's `test_cross.py`, which invoked the OCaml binary, with a Python test asserting every `interface/examples/*.json` against `interface/examples/expected.json` — the file Task 5's OCaml test reads. Point every path at the repository root's `interface/` and `fixtures/`.

**Verify:** `make research-test` green, reporting its count; `make build` and `make test` unchanged; `.github/workflows/ci.yml` still parses (`python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`). **Commit** as `research:` — "the research layer comes home".

---

## EXP-A01: one pre-registered rule, through every gate

The six tasks below adapt Alpha's unstarted Phase 1 plan (ported in Task 5 as `docs/superpowers/plans/2026-09-02-phase-1-validated-signal.md`), whose Tasks 2–7 carry complete code. "Alpha Task N" below means that document's Task N. Map its paths as Task 6 renamed them: `research/src/alpha/` becomes `research/src/ohcamel_research/`, and the CLI `alpha` becomes `ohcamel-research`. Everything in that plan binds unless this plan says otherwise. The owner's decision (ruling 1) is the one place it says otherwise.

### Task 7: Pre-register EXP-A01, before anything that could run it exists

**Files:** create `research/experiments/EXP-A01/hypothesis.md` and `research/experiments/EXP-A01/config.yaml`. Nothing else.

This task writes two files and commits them. It runs nothing, and **no runner code exists yet**. That is the point: the commit's timestamp precedes every line of the battery.

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
2016-06-01 → 2022-05-31, walk-forward: `ma_crossover` with fast ∈ {10, 20, 50}
and slow ∈ {50, 100, 200} (36 trials; Sharpe 0.67, DSR 0.73, PBO 0.725) and
`donchian` (8 trials; DSR 0.917, PBO 0.253). This rule, with fast = 1, was not
in that grid, but its family, its asset and its years were. So:

- SPY's Deflated Sharpe Ratio deflates by those 44 prior trend trials plus this
  experiment's 3 — 47 — not by 3.
- Parameter selection and every walk-forward fold use only
  2016-06-01 → 2022-05-31, the window EXP-002 already saw.
- The holdout, 2022-06-01 → 2026-06-01, has been touched by no experiment. It is
  evaluated once, with the parameters selected before it, and the "holdout
  positive" gate reads it alone.

TLT was only ever held inside a 60/40 blend (EXP-001), never trend-tested, so it
deflates by its own 3 trials.

**Economic rationale.** Time-series momentum: prices under-react to slow
information and trend-following captures the drift, while the other side is
taken by rebalancers and forced sellers in drawdowns who accept the loss for
liquidity or mandate reasons. The rule's main effect is expected to be drawdown
avoidance, not return enhancement.

**Assumptions.** Daily bars, Alpaca IEX, from `fixtures/history/`, which is
real data with provenance sidecars. Selection window 2016-06-01 → 2022-05-31;
holdout 2022-06-01 → 2026-06-01. The holdout run may read bars before
2022-06-01 only to warm up its moving average; returns count only from
2022-06-01. Friction model `fdq` v1.0.0: per-symbol half-spread plus regulatory
fees, with fdq's stress at ×1, ×2 and ×5, and the charter's cost sweep at
0, 5, 15 and 30 bps. Signals at the close, fills at the next open. Long or flat
only, one symbol per strategy.

**Grid, fixed in advance.** `fast ∈ {1}`, `slow ∈ {150, 200, 250}` — three
trials per symbol. Two symbols: SPY (equities) and TLT (rates). Anything outside
this grid is a new experiment, not a tweak.

**How it fails.** Whipsaw in sideways markets generates losing round trips. A
decade dominated by one long uptrend can make any long-biased rule look good
in-sample. The 200-day rule's edge in the literature is mostly pre-2010. Three
regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail. A pass with PBO above 0.5
is reported as "pass, fragile" and is not promoted without a second, independent
window. Promotion to sizing is the owner's decision alone, made in `book.sexp`,
and never follows from the verdict by itself.
```

`research/experiments/EXP-A01/config.yaml`:

```yaml
id: EXP-A01
title: "Does the 10-month moving-average rule survive friction on liquid ETFs?"
friction_version: "1.0.0"
mode: walkforward
n_folds: 4
strategies:
  - ma_crossover: {symbol: SPY, grid: {fast: [1], slow: [150, 200, 250]}, prior_trials: 44}
  - ma_crossover: {symbol: TLT, grid: {fast: [1], slow: [150, 200, 250]}, prior_trials: 0}
capital_tiers: [50000]
selection_window: {start: "2016-06-01", end: "2022-05-31"}
holdout_window: {start: "2022-06-01", end: "2026-06-01"}
stress_multipliers: [1, 2, 5]
cost_sweep_bps: [0, 5, 15, 30]
bootstrap: {resamples: 1000, method: stationary_block}
seed: 42
```

**Commit, alone:**

```bash
git add research/experiments/EXP-A01/hypothesis.md research/experiments/EXP-A01/config.yaml
git commit -m "$(cat <<'EOF'
research: pre-register EXP-A01 -- Faber's 10-month rule on SPY and TLT, disclosing what EXP-002 already showed about SPY, deflating by it, and holding out 2022-06 onward untouched -- because a hypothesis written after a result is a description of the result

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

No test count changes.

### Task 8: The ten-year history, with provenance

Alpha Task 2 fetched ten years from Alpaca. That fetch **is not needed**, and would need keys this plan never reads. The window is already on disk, with provenance, at `~/Documents/capitallimits/data/raw/`: nine ETFs (GLD, IEF, IWM, QQQ, SPY, TLT, XLE, XLF, XLK), each from 2016-06-01 to 2026-06-01, `source: alpaca`, `synthetic: false`, fetched 2026-06-09.

**Files:** create `fixtures/history/` (nine parquet files, each with its `.meta.json` sidecar) and `fixtures/history/README.md`, which states where the files came from, the window, the fetch date, and that a refresh is a new data version, invalidating older manifests.

**Steps:**
1. Copy each parquet file and its sidecar from `~/Documents/capitallimits/data/raw/`.
2. Assert every sidecar says `synthetic: false` and covers the window, and that each file's first and last dates match its sidecar. Record, in the README, each file's SHA-256 and its row count.
3. Point Alpha Task 2's `fixtures doctor` check at `fixtures/history/`, and make it part of `make research-test`.

**Commit** as `research:`.

### Task 9: The four gates `fdq` does not compute

Alpha Task 3, as written: `psr_gate`, `bootstrap_gate` (stationary block, seeded), `regime_gate` and `cost_sweep`, each a pure function of return series, in `research/src/ohcamel_research/battery/gates.py`, with hand-derived tests in `research/tests/test_gates.py`.

One addition: `cost_sweep` must accept `cost_sweep_bps: [0, 5, 15, 30]` from the config and report every level, as the charter requires. Each test's expected value carries its derivation. **No threshold appears in the gate code except the charter's.** Put them in one table the report also reads, so a gate and its report can never disagree about the line.

**Commit** as `research:`.

### Task 10: The manifest, and when it is stale

Alpha Task 4, as written: a `Manifest` dataclass, load and dump, and the freshness rule — a manifest older than the last methodology change is stale evidence. Add these fields, required by ruling 1 and read by Task 11:
- `selection_window` and `holdout_window`;
- per strategy, `prior_trials` and the DSR trial count actually used;
- `data`: every fixture file's SHA-256 from Task 8's README;
- `git_sha` of the tree that produced it;
- `methodology_date`: the date of the newest commit touching `research/src/ohcamel_research/battery/`.

A manifest whose `methodology_date` is older than that directory's newest commit is refused as stale.

**Commit** as `research:`.

### Task 11: The runner, with the owner's clean holdout

Alpha Task 5 builds `run(config) -> manifest` from `fdq`'s walk-forward plus Task 9's gates. Follow it, **with these changes, which are ruling 1 and are not optional**:

1. **Selection sees only the selection window.** Run `fdq`'s walk-forward with `n_folds` over `selection_window` alone. Grid search happens on each fold's training span; out-of-sample returns come from each fold's test span. The selected parameters are chosen on the whole selection window by the same rule `fdq`'s walk-forward uses, and recorded in the manifest.
2. **The holdout is run once.** Run the strategy with the selected parameters over `holdout_window`. Its moving average may read bars before `holdout_window.start` to warm up. Returns are counted only from `holdout_window.start`. A test asserts that no return dated before `holdout_window.start` enters the holdout series.
3. **"Holdout positive" reads the holdout alone**, using the definition Alpha's plan gives the gate.
4. **DSR deflates by `len(grid) + prior_trials`**: 47 for SPY, 3 for TLT. A test pins that the count reaches the DSR call.
5. **The other gates** — PSR, bootstrap, regime and cost sweep — run on the walk-forward's out-of-sample returns concatenated with the holdout's returns. Those are all the returns no selection touched. **PBO** comes from `fdq`'s CSCV over the selection window's grid.
6. **The verdict is mechanical.**
   - `pass` if every gate passes.
   - `pass, fragile` if every gate passes and PBO > 0.5.
   - `fail` if any gate fails. The manifest names every gate that failed.

**Tests:** Alpha's smoke test on the 2018–2020 `fixtures/bars/` slice (shape, not values), plus the holdout-isolation test and the DSR-count test above. No test runs EXP-A01's real configuration — that is Task 13.

**Commit** as `research:`.

### Task 12: A signal's validation block comes only from a manifest

Alpha Task 6, as written: `validation_from_manifest(path) -> dict`, and `ohcamel-research signal emit --validation-from PATH`. A signal can carry `status: pass` only by copying a non-stale manifest whose verdict is `pass` or `pass, fragile`. Hand-writing it is refused. A stale manifest yields `status: stale`, which R6 treats as not passing. Test each path.

**Commit** as `research:`.

### Task 13: Run EXP-A01 for real, and commit what it says

**Precondition, checked first:** `git log --format=%H -- research/experiments/EXP-A01/hypothesis.md` names a commit that is an ancestor of every commit touching `research/src/ohcamel_research/battery/`. If not, stop and report: the pre-registration would not precede the run.

**Steps:**
1. `ohcamel-research battery run research/experiments/EXP-A01` on `fixtures/history/`, seed 42.
2. Commit `manifest.json` and `report.md`. Nothing under `results/` is committed.
3. The report **leads with the verdict and the gates**:
   - one table per strategy, one row per gate, with value, threshold and pass or fail;
   - PBO named if high;
   - the cost sweep at every level;
   - the holdout's own numbers apart from the walk-forward's;
   - Sharpe, max drawdown, turnover and capacity for each strategy;
   - and only then returns.
4. It states, in its own section, the EXP-002 disclosure and the deflation used.
5. It says plainly that the verdict does not promote anything, and that promotion is the owner's decision in `book.sexp`.
6. Run it twice: the second manifest must equal the first byte for byte, apart from timestamps. A difference is the finding, and it is reported.

**What this task may not do:** change the grid, the windows, a gate, a threshold, the friction model or the seed after seeing any number. If the result is a fail, the task is complete, and the report says so with the same confidence as a pass would.

**Commit** as `research:` — "EXP-A01's verdict, led by its gates".

---

## The desk reads, judges, and — only when promoted — sizes

### Task 14: Intake — read, judge R1–R7, record, show

**Files:** `lib/config.ml` (the book's `signals` block), `book.example.sexp` (the block, documented and inert), `desk/intake.ml`, `desk/journal.ml` and `.mli` (the `signals` table's reader and writer; the table already exists), `desk/desk.ml` (an `/api/research` extension), `bin/main.ml` (the directory and the minute loop), tests.

**The book's `signals` block**, optional, absent meaning no intake:

```lisp
 (signals
  ((strategies
    (((name exp-a01-spy) (symbols (SPY)) (max_age 3) (sizing advisory) (capital_fraction 0.5))
     ((name exp-a01-tlt) (symbols (TLT)) (max_age 3) (sizing advisory) (capital_fraction 0.5))))))
```

- **Two strategies, one per symbol,** because EXP-A01 validated each symbol separately as long-or-flat. A signal's weight is 0 or 1 exactly as tested, and how much of the book each gets is the owner's `capital_fraction`, not the model's.
- **Validation:**
  - every `symbols` entry must be in the book's universe;
  - `capital_fraction` must be in (0, 1];
  - the fractions of the strategies set to `live` must sum to at most 1;
  - `sizing` is `advisory` or `live`, and **defaults to `advisory`** (ruling 2).
- **`book.example.sexp`** ships the block with both strategies `advisory`, and says in a comment that switching one to `live` is the owner's decision, made after reading its manifest.

**The demo** registers the same two strategies, both `advisory`, in its own in-code desk configuration. That way its Research page names what would be judged. With no research service and no closed sessions on the demo, any signal it were given would fail R3, and the page says that rather than pretending.

**The directory** comes from `OHCAMEL_SIGNALS_DIR`, not from the book, because it is where a deployment put a volume, not a fact about the book. With the variable unset there is no intake. Refuse to start if it is set but unreadable, with a line naming the variable.

**Once a minute**, in the desk's own loop:
1. List the directory.
2. For each `*.json` not already judged, parse it and judge it with `Contract` (Task 5). A file is already judged when its `(strategy, sequence)` is in the journal.
3. Use a `Contract.Clock.t` built from the journal's `sessions` table:
   - `latest_bar` is the newest session date;
   - `bars_after d` counts the session dates after `d`.

   With no sessions yet, nothing can pass R3, which is correct: the desk holds no bar.
4. Record every judgement in the journal's `signals` table: the verdict, the first rule that failed, a detail sentence, and the document itself. The verdicts are:
   - `accepted` — passes R1–R7 and its strategy is `live`;
   - `advisory` — passes R1–R5 and R7, but fails R6, **or** passes everything but its strategy is `advisory`. The detail names which.
   - `rejected` — fails R1–R5 or R7, with the rule.
5. A malformed file, meaning unreadable JSON, is recorded once, keyed by its file name, and never retried.

This task sizes nothing. Task 15 attaches to `accepted`.

**`/api/research`** (a desk extension, so `lib/` stays silent) serves:
- the registered strategies with their `sizing` and `capital_fraction`;
- the latest judgement per strategy, with its verdict, rule, detail, `as_of`, weights and validation block;
- R8's status as a fixed sentence: "R8 (data hash) is not enforced; see the design §3.12".

It is bounded: the latest judgement per strategy plus the 20 newest overall, all read through an index.

**Tests** (main suite; each rule's boundary is Task 5's, so these test the wiring):
- a file per verdict lands in the journal with the right rule;
- a replayed sequence is rejected by R5;
- a malformed file is recorded once;
- the clock comes from sessions — a signal dated after the newest session fails R3;
- `advisory` sizing turns a passing signal into `advisory`, detail naming the sizing;
- no directory means no intake;
- `/api/research`'s shape.

**Commit** as `desk:`.

### Task 15: Sizing, the rebalance, and market-on-open

**Files:** `desk/order.ml` (time in force), `desk/rules.ml` (the session rule by time in force), `desk/alpaca_trade.ml` and `desk/sim_venue.ml` (carry it), `desk/rebalance.ml` (targets), `desk/oms.ml` (`propose_rebalance`), `desk/intake.ml` (call it on `accepted`), tests in the main and scheduler suites.

**Time in force.** `Order.Request` gains `tif : Tif.t` with `Day | Opg`, default `Day`, journaled with the order. Every existing path stays `Day`. Alpaca's body carries `"time_in_force": "day"` or `"opg"`. The simulated venue holds an `Opg` order until a test-visible `open_session` call, then fills it at the mark. The session rule by `tif`, using Task 1's clock:
- **`Day`** needs the regular session open, as today.
- **`Opg`** needs the session **closed** and the next open more than two minutes away, which is Alpaca's cutoff for opening-auction orders. The rule's sentence names which side failed.

**Targets** (`desk/rebalance.ml`, pure). For a strategy with `capital_fraction f`, equity `E`, a signal weight `w` for a symbol and that symbol's latest mark `p`:

    target = trunc(w × f × E / p)          (toward zero; whole shares)
    order  = target − current position

A zero `order` is no order. Tests:
- hand-computed targets on both sides of a share boundary;
- a negative weight rounds toward zero, not away;
- an unknown mark or unknown equity produces **no targets and a reason**, never a guess.

**`Oms.propose_rebalance oms ~source ~orders`** — the one new entry point, and it goes through the same machinery as a ticket:
1. The rules, per order (with `tif = Opg`, the session rule reads as above).
2. **One** gate call, with all the rebalance's fills together, plus the resting orders (Task 3). A rebalance is gated as a unit.
3. The journal: every order written first, in the same sequencer job.
4. The wire: each submitted through the existing single submit site.

If any order fails a rule, or the gate refuses, **nothing** is journaled or sent, and the refusal names the order and the rule or limit. The switch refuses a rebalance as it refuses a ticket. `source` is `signal:<strategy>:<sequence>`, so the blotter says where an order came from.

**On `accepted`** (Task 14), intake computes the targets and calls `propose_rebalance` once. R5's sequence already guarantees once per signal. The outcome — submitted, refused and why, or no change — is written into the judgement's detail.

**Tests:**
- **Main suite:**
  - the session rule for `Opg` on both sides of each boundary;
  - a rebalance refused as a unit when its second order breaches a limit, with nothing journaled;
  - an `advisory` strategy's passing signal produces no order;
  - the Alpaca body carries `opg`.
- **Scheduler suite:** a rebalance journals before the wire and fills at the simulated open. Name the new cases in the count.

**Commit** as `desk:`. This is the first code in the project that can place an order without a person pressing a button, so the task's review is on opus and reads it that way.

### Task 16: The research service

**Files:** `research/src/ohcamel_research/service.py`, `research/tests/test_service.py`, `deploy/research.Dockerfile`, `deploy/docker-compose.yml`, `deploy/smoke.sh`.

**The service**, which runs once per trading day after the desk has recorded the close. Schedule it at 18:00 America/New_York; a signal written before the desk has closed the day would fail R3, which is correct, not a bug. For each strategy it is configured with:
1. Refresh that strategy's bars through `fdq` from Alpaca, reading the data keys from the environment the compose file gives it. **The service reads them; no human or agent does.** Fetch enough history for the longest moving average plus a margin.
2. Compute the rule on the latest bar with the **manifest's selected parameters**, never a new choice: weight 1 if the price is above its SMA, else 0.
3. Emit a signal:
   - `as_of` is the latest bar's date, and `sequence` is that date as `YYYYMMDD`, so it is strictly increasing per strategy;
   - its validation block comes through `validation_from_manifest` (Task 12), from the manifest baked into the image at build.
4. Write it into `/signals` **atomically**: write `<name>.tmp`, fsync, then rename to `<strategy>-<as_of>.json`. A second run on the same day overwrites nothing, because the file already exists; it logs that.

The service's strategies are exactly the ones whose manifests it carries. Its config names each strategy, symbol and manifest path.

**Tests,** hermetic. The `fdq` fetch sits behind a small interface with a fixture-backed fake reading `fixtures/bars/`.
- A day's run writes one well-formed signal that passes the contract examples' schema.
- Its weight matches the rule on the fixture by hand.
- The write is atomic: no `.tmp` is left, and a crash between write and rename leaves no `.json`.
- Re-running on the same day is a no-op.
- A stale manifest emits `status: stale`.

**Deploy:**
- **`deploy/research.Dockerfile`:** `python:3.12-slim` plus `uv`, copying `research/` and `interface/`, with `uv sync --frozen`. `fdq` comes from GitHub at its pin: public, no credentials.
- **Compose:**
  - `ohcamel-research`, in `profiles: [live]`, with the live engine's `env_file` and a named volume `signals` mounted read-write at `/signals`;
  - the live engine mounts the same volume **read-only** at `/data/signals` and gets `OHCAMEL_SIGNALS_DIR=/data/signals`;
  - **no `ports:`**, as for every engine service;
  - the Caddyfile untouched.
- **The smoke suite** checks that `/api/research` answers on the demo (with intake off) and returns 401 without credentials on the live host.

**Verify:** `make research-test` green; `docker compose -f deploy/docker-compose.yml config` parses, if Docker is available (if not, say so); `make build` and `make test` unchanged.

**Commit** as `deploy:` or `research:`.

### Task 17: The Research page, the sixth link, and the signals band

**Files:** `web/research.html`, `web/research.js`, `web/nav.html` (the sixth link, between Execution and Argument, where its comment already says it goes), `lib/dune` (the `research_html.ml` rule, plus a rule embedding `research/experiments/*/manifest.json` and each report's verdict line — in `desk/`, since `lib/` may not name research), `lib/server.ml` (the `/research` route), `lib/graph.ml` and `web/graph.js` (the `signals` band), `web/page.css`, `deploy/smoke.sh`, and the tests that pin routes, pages and topology.

**The page** reads `/api/research` and the embedded manifests, and computes nothing. In reading order:
1. **Strategies:** each registered strategy, its symbols, its `sizing` (`advisory` or `live`, in words) and its `capital_fraction`.
2. **Latest signal:** per strategy, its `as_of` and weights, with its verdict — `accepted`, `advisory` with the rule or the sizing named, or `rejected` with the rule.
3. **Evidence:** each manifest's gate table, with value, threshold and pass or fail, and the verdict line. The EXP-002 disclosure appears where the report has it. The cost sweep is shown at every level.
4. **Against live:** one sentence — the desk's own record will be compared with the backtest once enough sessions exist, which is A5.
5. **R8:** the fixed sentence.

Everything goes in as text. Every lookup is guarded. The page must fit both colour schemes and a 380 px width.

**The signals band.** `Topology.Outside` gains a `signals` entry when a desk with intake is attached. It writes nothing in the graph and is drawn as a short stub into the `orders` entry, because signals become orders. No new routing is needed: reuse the `orders` stub's style. The no-desk figure stays byte-identical, and the existing test proves it.

**Routes:** `/research` joins the table, the test pin and `EXPECTED_ROUTES` after `/execution`, with a 200 probe on the demo and a 401 on the live host, and a dispatch assertion that its body carries its own id.

**Commit** as `web:`.

### Task 18: The documents, the smoke suite, the count — and Alpha's pointer

**Files:** `README.md`, `docs/overview.md`, `docs/status.md`, `interface/README.md` (it names OhCamel as its consumer now), `lib/verified.ml`, and in `~/Documents/ohcamel-alpha`: `README.md`.

1. **What is true now.** Every sentence about signals, research, EXP-A01, sizing, the Research page and the six-page site. Say "advisory" and "live" exactly as the code does. **The EXP-A01 verdict is quoted from its report, not paraphrased**, with its gates. Never describe a strategy as trading unless the live host's `book.sexp` says `live`, which no task sets.
2. **`docs/status.md`:** the inventory gains the research service and the Research page. "Next" drops A3 and keeps A4, A5 and A6. The acceptance row "an advisory signal shown with its rule" is recorded as met, on the demo or the live host as appropriate.
3. **Alpha's pointer.** In `~/Documents/ohcamel-alpha`, add a short section to the top of `README.md`: the repository is archived, its contract, research layer and Phase 1 plan now live in OhCamel (link), and nothing here is maintained. Commit it there, with the same trailer, and push it to its `origin`. **The GitHub "archive" setting is not changed**; the final report tells the owner it is theirs to switch.
4. **The smoke suite** against a local demo: every probe passes, including `/research` and `/api/research`.

**Verify:** `make build`, `make test`, `make research-test`, `dune build @fmt`, and the three greps. **Commit** as `docs:`.

---

## Acceptance

1. `make build` clean; `make test` green at the counts `lib/verified.ml` states; `make research-test` green; `dune build @fmt` clean; the three greps silent.
2. `research/experiments/EXP-A01/hypothesis.md`'s commit is an ancestor of every commit touching the battery code (Task 13's precondition), and of the manifest's.
3. The manifest reproduces byte for byte apart from timestamps, and is not stale.
4. The report leads with the verdict and the gates, discloses EXP-002, and states that nothing is promoted.
5. The spec's acceptance, "an advisory signal shown with its rule", holds in three places:
   - **Hermetically:** a test renders an `advisory` judgement with its rule or its sizing named, through `/api/research` and the page.
   - **On the demo:** `/research` shows both strategies as `advisory` and the EXP-A01 evidence.
   - **On the live host, after the owner deploys and the service's first post-close run:** the real signal appears as `advisory` with its reason. That last one is the owner's to see, and the final report says so.
6. No strategy is `live` anywhere in the repository.
7. `deploy/smoke.sh http://localhost:8099` passes every probe.
8. Alpha's pointer is pushed; the archive switch is left to the owner.
