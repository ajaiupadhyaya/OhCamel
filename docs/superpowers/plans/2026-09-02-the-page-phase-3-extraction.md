# The page — Phase 3: extraction with the byte-identical gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the six CLI reports out of `bin/main.ml` into `lib/` as pure records, leaving each `run_*` a printer over its record, under a gate that says the six credential-free modes' stdout must be byte-identical afterwards.

**Architecture:** Every `ohcamel <mode>` today computes and prints in one pass, so the numbers the README quotes exist only as terminal output and nothing else can read them. This phase splits each mode in two: a library module that computes a record (`Recompute_log`, `Synthetic_book`, `Scaling_probe`, `Validation_report`, `Options_walk`, `Garch_study`, `Verified`) and a printer in `bin/main.ml` that formats it. Phases 4 and 5 then encode the same records onto `/api/reports` and `/api/graph`, so the web page and the terminal cannot drift — which is the one-implementation rule `stress.ml` already enforces, applied to reports.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route.
- No persistence.
- No external asset (no CDN, no web font, no charting library, no framework).
- The live host's gate untouched (no Caddy CORS, no basic-auth matcher, no `/api/up`).
- No invented vol surface on either deployed book.
- No number the process did not produce styled as if it had.
- No second implementation of engine arithmetic.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged.
- The eight invariants in `docs/status.md`.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in repo).
- All tests hermetic.
- The byte-identical stdout gate for the six credential-free modes wherever `bin/main.ml` is touched.

## What already exists, and what Phase 1 left you

Read these before starting. Everything below is in the repository as it stands.

- `bin/main.ml` (1,870 lines) holds every mode. `Counter` at :136–166, `probe` at :394–455, `scaling_report` at :457–469, `rate_beta` at :576–581, `seeded_demo_graph` at :583–605, `backtest_rng`/`backtest_series` at :742–767, `run_backtest` at :781–862, the GARCH constants and loop at :881–970, `synthetic_implied_vol` at :997–999 and the options constants and walks at :1001–1237, `worst_burst` at :1280–1291, `run_backtest_crisis` at :1310–1422.
- `lib/` has no `.mli` files at all, so every top-level binding in a library module is public. `lib/dune` compiles every `.ml` in `lib/` and `lib/feed/` into one library `ohcamel` with `(include_subdirs unqualified)`; a new module needs **no** dune edit.
- `Var_backtest` (lib/var_backtest.ml) exports `Observation.{create,var,realised,exceeded}`, `Estimator.{Historical,Parametric,Parametric_ewma of float,to_string,estimate}`, `rolling ~returns ~window ~confidence ~estimator : Observation.t list`, `run ~observations ~estimator ~confidence : report`, `of_returns`, `exceedances : Observation.t list -> bool array`, `shape_bounds : float * float` (= `(0.05, 20.0)`, var_backtest.ml:393), `Zone.{Green,Yellow,Red,to_string}`, `rejected ?alpha`, `verdict ?alpha`, `to_string`, and `report` with `[@@deriving fields ~getters]` giving `observations`, `exceptions`, `expected_exceptions`, `observed_rate`, `expected_rate`, `kupiec_statistic`, `kupiec_p`, `independence_statistic`, `independence_p`, `conditional_coverage_statistic`, `conditional_coverage_p`, `duration_shape : float option`, `duration_statistic : float option`, `duration_p : float option`, `zone`, `zone_cumulative_probability`, `worst_loss`, `var_at_worst_loss`.
- `Crisis_data` exports `Window.{name,description,dates,closes,sessions,symbols}`, `of_string ~name`, `load ?dir ~name ()`, `load_all ?dir ()`, `returns`, `portfolio_returns_of_book ~instruments ~positions ~marks window : float array option`, `window_names`.
- `Vol_estimators.Garch11` exports the record `{omega; alpha; beta}` with getters, `persistence`, `long_run_variance`, `shock_half_life : t -> float option`, `conditional_variances`, `log_likelihood`, `of_params`, `fit ?(coarse_steps = 40) ~returns () : t`, `simulate ?(burn_in = 500) ~innovations t : float array`. `Vol_estimators.Ewma.default_lambda` is 0.94.
- `Graph` exports `create ?on_compute ?starting_cash … ~instruments ~limits ~confidence ~return_window ()`, `fork ?on_compute ?limits`, `destroy`, `stabilize`, the setters, `snapshot`, `portfolio_returns`, `portfolio_vega`, `total_stabilizes`, `total_nodes_recomputed`, and `Snapshot` with `exposure_by_instrument`, `portfolio_gamma`, `portfolio_vega`, `vega_by_bucket : float Options.Tenor_bucket.Map.t`, `breaches`, `weights`, and the rest.
- `Options` exports `Tenor_bucket.{ordered,to_string,of_days,Map}`, `Position.create ?multiplier ~underlying ~id ~strike ~right ~expiry_in_days ()`, `default_multiplier`, `Strike.of_float`, `Right.Call`, `Implied_vol.of_float`, `Contracts.of_float`.

**Phase 1 (a separate plan, already executed) left two names this plan consumes:**

- `Crisis_data.load_all_embedded : unit -> Crisis_data.Window.t list` — `of_string` over three embedded CSV strings that keep their `#` comment lines, so `Window.description` still reads the first. It returns a plain list, not `Or_error.t`: the strings are compile-time constants.
- `Quoted.json : string` — the contents of `web/quoted.json`, **in the shape Phase 1's Task 5 shipped** (its plan `:804–874`), which Tasks 5, 6 and 8 parse. Do not reshape the file; the pin tests read these keys:

```json
{
  "note": "...", "source": "README.md", "quoted_on": "2026-09-02", "machine": "Apple M2 Pro, macOS, arm64",
  "scaling": {
    "note": "...", "columns": ["instruments", "nodes_in_graph", "nodes_per_tick", "if_polled"],
    "rows": [
      { "instruments": 10,  "nodes_in_graph": 58,   "nodes_per_tick": 25.6, "if_polled": 58 },
      { "instruments": 100, "nodes_in_graph": 337,  "nodes_per_tick": 25.2, "if_polled": 337 },
      { "instruments": 400, "nodes_in_graph": 1267, "nodes_per_tick": 26.0, "if_polled": 1267 }
    ]
  },
  "battery": {
    "note": "...", "confidence": 0.95, "window": 60, "seed": "2026_08_24",
    "rows": [
      { "series": "iid-normal", "estimator": "historical", "n": 940, "exceptions": 45, "expected": 47.0,
        "kupiec_p": 0.7631, "independence_p": 0.3595, "joint_p": 0.6280, "duration_p": 0.6291,
        "duration_shape": 0.95, "zone": "green", "verdict": "ok" }
    ]
  },
  "crisis": {
    "note": "...", "confidence": 0.95, "window": 60, "burst_span": 21,
    "rows": [
      { "window": "gfc", "estimator": "historical", "n": 570, "exceptions": 25, "expected": 28.5,
        "kupiec_p": 0.4925, "independence_p": 0.9207, "joint_p": 0.7862, "duration_p": 0.7483,
        "duration_shape": 0.95, "burst": 5, "zone": "green", "verdict": "ok" }
    ]
  },
  "garch": {
    "note": "...", "seed": "2026_08_25", "replications": 30, "burn_in": 500, "engine_window": 60,
    "truth": { "omega": 4e-6, "alpha": 0.10, "beta": 0.88, "persistence": 0.98, "half_life": 34 },
    "rows": [
      { "n": 60, "alpha_mean": 0.112, "alpha_sd": 0.104, "beta_mean": 0.444,
        "beta_sd": 0.375, "persistence_mean": 0.556, "persistence_sd": 0.364 }
    ]
  }
}
```

The row keys are `n` (not `observations`), `expected` (not `expected_exceptions`), `joint_p` (not `conditional_coverage_p`) and `verdict` as the string `"ok"` or `"REJECTED"` (not a boolean `rejected`); the estimator strings are `historical`, `parametric`, `ewma(0.94)`. `battery.rows` holds all nine rows in the README's order (iid-normal / vol-regime / jumps × the three estimators); `crisis.rows` holds all nine (gfc / covid / rates-2022 × the same three); `garch.rows` holds all six sample sizes. `duration_p` and `duration_shape` are `null` on the `jumps`/`historical` row, which has fewer than two exceptions. The values are the tables at `README.md:471–481`, `README.md:682–692` and `README.md:619–626`; the `scaling` block is transcribed from `README.md:53–57` as it stands — 58 / 337 / 1267, stale on purpose, marked so in its `note` — and Task 10 corrects the README, `docs/status.md`, this block and Phase 1's `1267` pin together.

## How to run anything

Every command below is run from the repository root, `/Users/ajaiupadhyaya/Documents/OhCamel`, with the switch activated:

```bash
eval $(opam env --switch=$PWD --set-switch)
```

The Makefile does this itself (`OPAM_ENV` at Makefile:9), so `make test`, `make run`, `make fmt` need no prefix. Raw dune commands do.

---

### Task 1: The gate — capture the six credential-free modes

**Files:**
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/capture.sh`
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh`
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/*.txt` (six captures)
- Modify: nothing in the repository
- Test: the gate script itself

**Interfaces:**
- Consumes: `bin/main.ml`'s six credential-free modes, from its own usage text at `bin/main.ml:1821–1840` and the Makefile targets at `Makefile:82,89,96,111,121,133` — `synthetic`, `stress`, `backtest`, `backtest-crisis`, `options`, `garch`.
- Produces: `gate.sh`, run in every later task's Step 4. Nothing in the repository.

- [ ] **Step 1: Write the failing test**

The test here is the gate. Write it first, before any capture exists, so its first run fails for the right reason — there is nothing to compare against.

```bash
mkdir -p /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh <<'SH'
#!/usr/bin/env bash
# The byte-identical gate for Phase 3.
#
# The extraction moves the arithmetic behind six printed reports out of
# bin/main.ml. Without this gate that move is churn with a chance of silently
# changing the tables the README quotes; with it, it is the one-implementation
# rule stress.ml already enforces, applied to reports.
#
#   gate.sh                 diff every mode against its baseline
#   gate.sh synthetic garch diff only those modes
set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
REPO="/Users/ajaiupadhyaya/Documents/OhCamel"
MODES=("$@")
if [ ${#MODES[@]} -eq 0 ]; then
  MODES=(synthetic stress backtest backtest-crisis options garch)
fi
cd "$REPO" || exit 2
eval "$(opam env --switch="$REPO" --set-switch)"
dune build bin/main.exe 2>&1 || { echo "GATE: build failed"; exit 2; }
status=0
for mode in "${MODES[@]}"; do
  if [ ! -f "$BASE/$mode.txt" ]; then
    echo "GATE: no baseline for $mode -- run capture.sh first"
    status=2
    continue
  fi
  ./_build/default/bin/main.exe "$mode" > "$BASE/$mode.now.txt" 2>&1
  if diff -u "$BASE/$mode.txt" "$BASE/$mode.now.txt" > "$BASE/$mode.diff"; then
    echo "GATE ok        $mode"
  else
    echo "GATE DIFFERS   $mode  -- see $BASE/$mode.diff"
    status=1
  fi
done
exit $status
SH
chmod +x /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

- [ ] **Step 2: Run it and see it fail**

```bash
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: six lines reading `GATE: no baseline for synthetic -- run capture.sh first` (and the same for the other five), exit status 2.

- [ ] **Step 3: Minimal implementation**

```bash
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/capture.sh <<'SH'
#!/usr/bin/env bash
# Capture the stdout of the six credential-free modes, ONCE, before the
# extraction begins. Run exactly once. Re-running after a move would re-baseline
# the very thing the gate exists to catch.
set -eu
BASE="$(cd "$(dirname "$0")" && pwd)"
REPO="/Users/ajaiupadhyaya/Documents/OhCamel"
cd "$REPO"
eval "$(opam env --switch="$REPO" --set-switch)"
dune build bin/main.exe
git rev-parse HEAD > "$BASE/BASELINE_SHA"
for mode in synthetic stress backtest backtest-crisis options garch; do
  ./_build/default/bin/main.exe "$mode" > "$BASE/$mode.txt" 2>&1
  echo "captured $mode ($(wc -l < "$BASE/$mode.txt") lines)"
done
shasum -a 256 "$BASE"/*.txt > "$BASE/BASELINE_SHA256"
SH
chmod +x /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/capture.sh
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/capture.sh
```

`backtest-crisis` reads `docs/crisis/*.csv` relative to the working directory, which is why `capture.sh` `cd`s to the repository root — Task 6 is the change that makes that unnecessary.

- [ ] **Step 4: Run the tests and see them pass**

```bash
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: six lines, `GATE ok        synthetic` … `GATE ok        garch`, exit status 0. Then read three numbers out of the captures and keep them in front of you, because four later tasks pin them:

```bash
grep -A 5 "instruments   nodes in graph" /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/synthetic.txt
grep -E "^ +(60|250|1000) " /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/garch.txt
grep -E "hedge is|BREACH|\+20 days|1w-1m|3-6m|portfolio gamma" /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/options.txt
```

Expected, and asserted by tests later in this plan: the scaling rows are `10 / 63 / 25.6 / 63`, `100 / 342 / 25.2 / 342`, `400 / 1272 / 26.0 / 1272`; the GARCH rows at n = 60, 250, 1000 are `0.444 +/- 0.375` / `0.841 +/- 0.108` / `0.880 +/- 0.023` for beta and `0.556 +/- 0.364` / `0.939 +/- 0.102` / `0.973 +/- 0.012` for persistence; the hedge is 1338 shares, `nvda-vega` breaches at 141.5%, `+20 days` reads `$657,690 / -25.2 / $-1,502`, and the calendar buckets are `1w-1m $-12,559` and `3-6m $12,559` with portfolio gamma `-72.5`.

- [ ] **Step 5: Commit (nothing to commit)**

This task writes only to the scratch directory. Confirm the repository is untouched:

```bash
git -C /Users/ajaiupadhyaya/Documents/OhCamel status --porcelain
```

Expected: one line, `?? claudecodehandoff.md`, which belongs to a different project and is ignored throughout this plan. Nothing else. Do not commit.

---

### Task 2: `Recompute_log` — the counter that forks cannot reach

**Files:**
- Create: `lib/recompute_log.ml`
- Create: `test/test_recompute_log.ml`
- Modify: `bin/main.ml:130–166` (the `Counter` module becomes a thin wrapper)
- Modify: `test/test_ohcamel.ml:33–48` (register the new suite)
- Test: `test/test_recompute_log.ml`

**Interfaces:**
- Consumes: `Graph.create ?on_compute:(string -> unit) … ~instruments ~limits ~confidence ~return_window ()`; `Graph.fork ?on_compute ?limits : Graph.t -> Graph.t`; `Hashtbl.incr : ?by:int -> ?remove_if_zero:bool -> ('a, int) t -> 'a key -> unit` (`_opam/lib/base/hashtbl_intf.ml:338`); `Stress.suite_for ~graph`, `Stress.run_all ~graph ~scenarios`.
- Produces: `Recompute_log.t`; `Recompute_log.create : unit -> t`; `Recompute_log.note : t -> string -> unit`; `Recompute_log.drain : t -> (string * int) list`; `Recompute_log.distinct : t -> int`; `Recompute_log.total : t -> int`; `Recompute_log.hottest : t -> n:int -> (string * int) list`. Phase 4 passes `~on_compute:(Recompute_log.note log)` to the served `Graph.create` and drains in the broadcaster; Phase 2's `/api/ops` reads `distinct`, `total`, `hottest`.

- [ ] **Step 1: Write the failing test**

Create `test/test_recompute_log.ml`:

```ocaml
(* Unit tests for recompute_log.ml.

   The log is what the page's "26 of 53 named nodes ran this frame" is made of,
   so the two properties that matter are not about counting. They are about
   WHOSE recomputations land in it: exactly the graph it was attached to, and
   nothing a fork or a probe does in the same process. Incremental's state is
   shared across every Graph.t here, and its own counters cannot tell those
   apart -- which is the reason this module exists next to them. *)

open Core
module Recompute_log = Ohcamel.Recompute_log
module Graph = Ohcamel.Graph
module Stress = Ohcamel.Stress
module Synthetic_book = Ohcamel.Synthetic_book
open Ohcamel.Types

let aapl = Symbol.of_string "AAPL"

(* note / drain / distinct / total, with the counts hand-written above them:
   a ran three times, b twice, c once. Six notes, three distinct names. *)
let test_note_and_drain () =
  let log = Recompute_log.create () in
  List.iter [ "a"; "b"; "a"; "c"; "a"; "b" ] ~f:(Recompute_log.note log);
  Alcotest.(check (list (pair string int)))
    "drain returns each name once with its count, ordered by name"
    [ ("a", 3); ("b", 2); ("c", 1) ]
    (Recompute_log.drain log);
  Alcotest.(check int) "three distinct names" 3 (Recompute_log.distinct log);
  Alcotest.(check int) "six notes in the lifetime table" 6 (Recompute_log.total log);
  (* Drain empties the FRAME table and leaves the lifetime table alone. A frame
     that reported the same node twice because the previous frame forgot to
     clear would make the page's per-frame count monotonically wrong. *)
  Alcotest.(check (list (pair string int))) "the frame table is empty after a drain"
    [] (Recompute_log.drain log);
  Alcotest.(check int) "the lifetime table survives a drain" 6 (Recompute_log.total log)

(* Ordered by count descending, then by name ascending so a tie is stable
   between runs and between machines. Counts here: d 5, a 3, c 3, b 1. *)
let test_hottest_orders_by_count_then_name () =
  let log = Recompute_log.create () in
  List.iter
    [ "a"; "a"; "a"; "b"; "c"; "c"; "c"; "d"; "d"; "d"; "d"; "d" ]
    ~f:(Recompute_log.note log);
  Alcotest.(check (list (pair string int)))
    "hottest three: d before the a/c tie, a before c"
    [ ("d", 5); ("a", 3); ("c", 3) ]
    (Recompute_log.hottest log ~n:3)

(* Seed the synthetic book the way run_demo does, then tick one name.

   The expected set is test_graph.ml's own pinned [downstream_of_aapl_tick]
   (test/test_graph.ml:311–375) evaluated on the six-name book instead of the
   three-name one, which adds exactly the two limits that book has and the test
   book does not: nvda-risk reads component_var_map and tech-risk reads
   component_var_sector_map, and both maps are already in the pinned set. The
   two names that are NOT here are the point: covariance and covariance_ewma
   are not downstream of any price cell. *)
let downstream_of_aapl_tick_on_the_demo_book =
  [
    "exposure:AAPL";
    "exposure_map";
    "sector:TECH";
    "sector_map";
    "gross_exposure";
    "net_exposure";
    "weights";
    "portfolio_returns";
    "historical_var";
    "expected_shortfall";
    "parametric_var";
    "parametric_var_ewma";
    "attribution";
    "component_var_map";
    "component_var_sector_map";
    "diversification_ratio";
    "portfolio_beta";
    "var_notional";
    "es_notional";
    "equity";
    "current_drawdown";
    "limit:aapl-cap";
    "limit:tech-cap";
    "limit:book-cap";
    "limit:var-cap";
    "limit:dd-cap";
    "limit:nvda-risk";
    "limit:tech-risk";
    "breaches";
    "feed:AAPL";
    "feed_health";
  ]

let seeded_graph log =
  let graph =
    Graph.create
      ~on_compute:(Recompute_log.note log)
      ~starting_cash:Synthetic_book.starting_cash ~instruments:Synthetic_book.instruments
      ~limits:Synthetic_book.limits ~confidence:Synthetic_book.confidence
      ~return_window:Synthetic_book.return_window ()
  in
  List.iter Synthetic_book.book ~f:(fun (symbol, _, price, qty) ->
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty));
  Synthetic_book.seed_returns ~rng:(Random.State.make [| 7 |]) ~graph;
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  ignore (Recompute_log.drain log : (string * int) list);
  graph

(* AAPL is marked DOWN, from 150 to 140, deliberately. Equity falls below the
   peak that mark_equity just recorded, so current_drawdown moves off zero and
   its Float.equal cutoff lets limit:dd-cap through. Mark it up instead and the
   drawdown stays 0.0, the cutoff fires, and dd-cap is correctly absent -- which
   is a real behaviour and would make this list depend on the tick's sign. *)
let test_a_tick_drains_exactly_its_downstream_set () =
  let log = Recompute_log.create () in
  let graph = seeded_graph log in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 140.0; time = Time.epoch };
      Graph.stabilize graph;
      let drained = Recompute_log.drain log in
      Alcotest.(check (list string))
        "an AAPL tick drains exactly AAPL's dependents"
        (List.sort downstream_of_aapl_tick_on_the_demo_book ~compare:String.compare)
        (List.map drained ~f:fst);
      (* Each exactly once. More than once inside one stabilize would mean the
         graph is re-entering node bodies, which would quietly multiply the cost
         of every tick and inflate the page's per-frame count. *)
      List.iter drained ~f:(fun (name, n) ->
          Alcotest.(check int) (Printf.sprintf "%s ran once" name) 1 n))
    ()

(* A write that changes nothing propagates nothing. The setters do not
   stabilize, so this is a real stabilize over a clean graph. *)
let test_an_unchanged_write_drains_empty () =
  let log = Recompute_log.create () in
  let graph = seeded_graph log in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.stabilize graph;
      Alcotest.(check (list (pair string int)))
        "re-writing the price it already had recomputes nothing" []
        (Recompute_log.drain log))
    ()

(* THE ONE THAT MATTERS.

   Graph.fork does not inherit the hook (graph.ml:1415 takes its own optional
   ?on_compute and defaults it away), so twelve scenario forks -- which do
   thousands of node recomputations against the same shared Incremental state --
   must leave the served graph's log completely empty. Incremental's own
   process-wide counter cannot make this distinction, which is exactly why the
   page labels that counter as including forks and prints this one beside it. *)
let test_a_fork_never_reaches_the_parents_log () =
  let log = Recompute_log.create () in
  let graph = seeded_graph log in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let before = Graph.total_nodes_recomputed () in
      let outcomes = Stress.run_all ~graph ~scenarios:(Stress.suite_for ~graph) in
      Alcotest.(check bool)
        "the suite actually ran something" true
        (List.length outcomes > 0
        && Graph.total_nodes_recomputed () - before > 0);
      Alcotest.(check (list (pair string int)))
        "not one forked recomputation reached the parent's log" []
        (Recompute_log.drain log);
      Alcotest.(check int) "and the lifetime table is still empty too" 0
        (Recompute_log.total log))
    ()

let suite =
  ( "recompute_log",
    [
      Alcotest.test_case "note, drain, distinct, total" `Quick test_note_and_drain;
      Alcotest.test_case "hottest orders by count then name" `Quick
        test_hottest_orders_by_count_then_name;
      Alcotest.test_case "a tick drains exactly its downstream set, each once" `Quick
        test_a_tick_drains_exactly_its_downstream_set;
      Alcotest.test_case "an unchanged write drains empty" `Quick
        test_an_unchanged_write_drains_empty;
      Alcotest.test_case "A FORK NEVER REACHES THE PARENT'S LOG" `Quick
        test_a_fork_never_reaches_the_parents_log;
    ] )
```

Register it in `test/test_ohcamel.ml` by inserting one line after `Test_crisis_data.suite;`:

```ocaml
      Test_crisis_data.suite;
      Test_recompute_log.suite;
      Test_stress.suite;
```

This test names `Synthetic_book`, which Task 3 creates. Write it now anyway — Step 2's failure is the point, and Task 3 is the next task.

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel && eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -20
```

Expected: `Error: Unbound module Ohcamel.Recompute_log` (and, once that is fixed, `Unbound module Ohcamel.Synthetic_book`).

- [ ] **Step 3: Minimal implementation**

Create `lib/recompute_log.ml`:

```ocaml
(* Which named node bodies ran, and how often.

   bin/main.ml has counted this since Phase 1 in order to print one column. The
   page needs the same fact for a different reason -- it lights the nodes that
   ran on a drawing of the graph -- and needs it per FRAME rather than per run,
   so the counting moves here and gains a second table.

   WHY TWO TABLES. [frame] is drained by whoever emits a frame and is empty
   immediately afterwards; [lifetime] is never cleared and answers "how many
   distinct nodes has this graph ever run, and which are the hot ones". A single
   table cannot do both: draining it to report a frame would destroy the
   lifetime tally, and not draining it would make every frame report every node
   that has ever run.

   WHY IT CANNOT RAISE. [note] runs INSIDE node bodies, alongside the
   computation, under the same rule graph.ml's on_change listeners are under:
   record the fact and return. An exception thrown from inside a stabilize
   leaves the graph half-computed with no honest way to report what state it is
   in. Hashtbl.incr is total -- it inserts 1 where there was no key -- so there
   is no lookup here that could fail and no allocation that could be refused
   more than any other.

   WHY IT IS NOT INCREMENTAL'S COUNTER. Inc.State.num_nodes_recomputed is
   process-wide: it counts input cells, the unnamed plumbing graph.ml never
   named, every scenario fork, and the startup scaling probe, all against one
   shared state. That number is worth printing and is labelled as what it is.
   This one is the named node bodies of ONE graph, because Graph.fork does not
   inherit the on_compute hook -- which is what makes "26 of 53 ran this frame"
   a statement about the book rather than about the process. *)

open Core

type t = {
  (* Cleared by [drain]. What ran since the last frame was emitted. *)
  frame : int String.Table.t;
  (* Never cleared. What has ever run, for [distinct], [total] and [hottest]. *)
  lifetime : int String.Table.t;
}

let create () = { frame = String.Table.create (); lifetime = String.Table.create () }

(* Called from inside a node body, once per recomputation. Two hashtable
   increments and nothing else: no formatting, no allocation of a list, no
   comparison against a previous value. Anything more expensive here is paid on
   every node of every tick. *)
let note (t : t) (name : string) : unit =
  Hashtbl.incr t.frame name;
  Hashtbl.incr t.lifetime name

(* Sorted by name rather than returned in hashtable order, because this list
   goes onto the wire and out to a browser: an unsorted order would make two
   frames with the same content compare unequal for no reason, and would make a
   test of the drained set depend on a hash seed. *)
let drain (t : t) : (string * int) list =
  let entries =
    Hashtbl.to_alist t.frame
    |> List.sort ~compare:(fun (a, _) (b, _) -> String.compare a b)
  in
  Hashtbl.clear t.frame;
  entries

let distinct (t : t) : int = Hashtbl.length t.lifetime

let total (t : t) : int =
  Hashtbl.fold t.lifetime ~init:0 ~f:(fun ~key:_ ~data acc -> acc + data)

(* Count descending, then name ascending. The second key is not decoration: two
   nodes on the same edge run the same number of times, so ties are the common
   case here and an unstable order would make the hottest list flicker between
   runs of a program whose whole claim is that it reproduces. *)
let hottest (t : t) ~(n : int) : (string * int) list =
  Hashtbl.to_alist t.lifetime
  |> List.sort ~compare:(fun (name_a, a) (name_b, b) ->
      match Int.descending a b with 0 -> String.compare name_a name_b | c -> c)
  |> fun sorted -> List.take sorted n
```

Then replace `bin/main.ml:130–166` — the whole `(* Recompute accounting *)` banner block through the `end` of `module Counter` — with the wrapper. The exact text being replaced begins `(* ------------------------------------------------------------------------ *)` / `(* Recompute accounting *)` and ends with `    |> fun sorted -> List.take sorted n\nend`. The replacement:

```ocaml
(* ------------------------------------------------------------------------ *)
(* Recompute accounting                                                      *)
(* ------------------------------------------------------------------------ *)

(* The tally moved to lib/recompute_log.ml, because the page needs the same
   fact and a second implementation of it would be free to disagree with the
   one the terminal prints. What stays here is the one field that is a
   PROPERTY OF THE DISPLAY rather than of the graph: live mode reports work per
   trade, so it has to remember how many trades had arrived at the previous
   snapshot. The graph has no opinion about trades. *)
module Counter = struct
  type t = {
    log : Recompute_log.t;
    mutable trades_at_last_snapshot : int;
  }

  let create () = { log = Recompute_log.create (); trades_at_last_snapshot = 0 }
  let on_compute t name = Recompute_log.note t.log name

  (* Node bodies since the previous reading. The log reports which nodes ran;
     this column only ever wanted how many, so it sums the drained counts. *)
  let take_step t =
    List.fold (Recompute_log.drain t.log) ~init:0 ~f:(fun acc (_, n) -> acc + n)

  let distinct_nodes t = Recompute_log.distinct t.log
  let grand_total t = Recompute_log.total t.log
  let hottest t ~n = Recompute_log.hottest t.log ~n
end
```

`bin/main.ml` already has `open Ohcamel` at :23, so `Recompute_log` resolves unqualified. Nothing else in `bin/main.ml` changes: `Counter.create`, `Counter.on_compute`, `Counter.take_step`, `Counter.distinct_nodes`, `Counter.grand_total`, `Counter.hottest` and the `counter.Counter.trades_at_last_snapshot` field access at :1492–1493 all keep their shapes.

- [ ] **Step 4: Run the tests and see them pass**

Task 3 creates `Synthetic_book`, so run the parts that can run now and finish this step after Task 3:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel && eval $(opam env --switch=$PWD --set-switch) && dune build bin/main.exe && dune fmt
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: `dune build bin/main.exe` succeeds (the library and the executable compile; the test binary does not yet, and that is Task 3's Step 4), and the gate prints six `GATE ok` lines. `synthetic` is the mode that would move if the tally changed — it prints `distinct nodes in the graph`, `node recomputations, incremental` and the six hottest nodes.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/recompute_log.ml test/test_recompute_log.ml test/test_ohcamel.ml bin/main.ml
git commit -m "graph: a recompute log the page can read, because a fork must not inflate it"
```

---

### Task 3: `Synthetic_book` — the book, the generator, and `seed_returns`

**Files:**
- Create: `lib/synthetic_book.ml`
- Create: `test/test_synthetic_book.ml`
- Modify: `bin/main.ml:45–128` (the `The synthetic book` and `Synthetic market` sections), `bin/main.ml:559–605` (`rate_beta` and `seeded_demo_graph`), `bin/main.ml:1724–1732` (`run_demo`'s seeding loop)
- Modify: `test/test_ohcamel.ml` (register the suite)
- Test: `test/test_synthetic_book.ml`, and Task 2's `test/test_recompute_log.ml`, which compiles for the first time here

**Interfaces:**
- Consumes: `Graph.create`, `Graph.set_price`, `Graph.set_qty`, `Graph.set_returns : t -> Symbol.t -> float array -> unit` (`lib/graph.ml:1612`), `Graph.set_factor_returns : t -> float array -> unit` (`:1630`), `Graph.returns : t -> Symbol.t -> float array` (`:1621`), `Graph.factor_returns : t -> float array` (`:1633`), `Graph.portfolio_beta : t -> float option` (`:1725`); `Random.State.make : int array -> t`, `Random.State.float : t -> float -> float` (`_opam/lib/base/random.mli:112,128`); `Types.{Symbol, Sector, Notional, Instrument, Limit, Greek}`.
- Produces: `Synthetic_book.book : (Symbol.t * Sector.t * float * float) list` (symbol, sector, mark, qty); `Synthetic_book.instruments : Instrument.t list`; `Synthetic_book.limit : string -> Limit.scope -> Limit.kind -> Limit.t`; `Synthetic_book.limits : Limit.t list`; `Synthetic_book.starting_cash : Notional.t`; `Synthetic_book.confidence : float` (= 0.95); `Synthetic_book.return_window : int` (= 60); `Synthetic_book.gaussian : rng:Random.State.t -> sigma:float -> float`; `Synthetic_book.daily_return : rng:Random.State.t -> float`; `Synthetic_book.rate_beta : Sector.t -> float`; `Synthetic_book.seed_returns : rng:Random.State.t -> graph:Graph.t -> unit`. Phase 5 reads `book`, `confidence`, `return_window`; Tasks 4–8 of this plan read the rest.

- [ ] **Step 1: Write the failing test**

Create `test/test_synthetic_book.ml`:

```ocaml
(* Unit tests for synthetic_book.ml.

   The book is six literals and nine limits, and the point of testing literals
   is that three printers, a stress suite, a crisis backtest and now a served
   process all read the same ones. The one number derived by hand here is the
   gross the crisis mode prints as a literal string ("$316,000 gross"), which
   is the only place the CLI states a fact about this book that the book itself
   does not compute. *)

open Core
module Synthetic_book = Ohcamel.Synthetic_book
module Graph = Ohcamel.Graph
open Ohcamel.Types

(*   AAPL 150 x  400 =  60,000
     MSFT 300 x  200 =  60,000
     NVDA 900 x   60 =  54,000
     JPM  200 x  250 =  50,000
     XOM  100 x -500 = -50,000  -> |.| 50,000
     CVX  140 x -300 = -42,000  -> |.| 42,000
                        -------
     gross             316,000 *)
let test_the_book_is_the_one_the_cli_prints () =
  Alcotest.(check int) "six names" 6 (List.length Synthetic_book.book);
  Alcotest.(check int) "six instruments" 6 (List.length Synthetic_book.instruments);
  Alcotest.(check int) "nine limits" 9 (List.length Synthetic_book.limits);
  Alcotest.(check (float 1e-9))
    "gross at the marks is the $316,000 the crisis mode states" 316_000.0
    (List.fold Synthetic_book.book ~init:0.0 ~f:(fun acc (_, _, mark, qty) ->
         acc +. Float.abs (mark *. qty)));
  Alcotest.(check (list string))
    "three sectors, and the short leg is ENERGY"
    [ "ENERGY"; "FINANCIALS"; "TECH" ]
    (List.map Synthetic_book.book ~f:(fun (_, s, _, _) -> Sector.to_string s)
    |> List.dedup_and_sort ~compare:String.compare);
  Alcotest.(check (float 1e-9)) "confidence" 0.95 Synthetic_book.confidence;
  Alcotest.(check int) "return window" 60 Synthetic_book.return_window

let with_seeded_graph ~seed ~f =
  let graph =
    Graph.create ~starting_cash:Synthetic_book.starting_cash
      ~instruments:Synthetic_book.instruments ~limits:Synthetic_book.limits
      ~confidence:Synthetic_book.confidence ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter Synthetic_book.book ~f:(fun (symbol, _, price, qty) ->
          Graph.set_price graph symbol (Price.of_float price);
          Graph.set_qty graph symbol (Qty.of_float qty));
      Synthetic_book.seed_returns ~rng:(Random.State.make [| seed |]) ~graph;
      Graph.stabilize graph;
      f graph)

(* Spec §07, amended: the demo host used to seed its windows and its factor as
   independent draws, so every beta was fitted to noise. What is asserted here
   is the structural fact, not a beta: the factor cell is set, every window is
   the full return_window long, and two graphs seeded from the same seed hold
   exactly the same arrays -- which is what makes the served host's stress
   table a reproducible figure rather than a fresh coin toss per restart. *)
let test_seed_returns_is_deterministic_and_sets_the_factor () =
  let read graph =
    ( Array.to_list (Graph.factor_returns graph),
      List.map Synthetic_book.book ~f:(fun (symbol, _, _, _) ->
          Array.to_list (Graph.returns graph symbol)) )
  in
  let factor_a, windows_a = with_seeded_graph ~seed:7 ~f:read in
  let factor_b, windows_b = with_seeded_graph ~seed:7 ~f:read in
  Alcotest.(check int) "the factor is one window long" 60 (List.length factor_a);
  List.iter windows_a ~f:(fun w ->
      Alcotest.(check int) "each name's window is full" 60 (List.length w));
  Alcotest.(check (list (float 0.0))) "same seed, same factor" factor_a factor_b;
  Alcotest.(check (list (list (float 0.0)))) "same seed, same windows" windows_a windows_b;
  Alcotest.(check bool)
    "with a factor set, the graph can estimate a beta at all" true
    (with_seeded_graph ~seed:7 ~f:(fun g -> Option.is_some (Graph.portfolio_beta g)))

let suite =
  ( "synthetic_book",
    [
      Alcotest.test_case "the book is the one the CLI prints" `Quick
        test_the_book_is_the_one_the_cli_prints;
      Alcotest.test_case "seed_returns is deterministic and sets the factor" `Quick
        test_seed_returns_is_deterministic_and_sets_the_factor;
    ] )
```

Register it in `test/test_ohcamel.ml`, one line after the `Test_recompute_log.suite;` line Task 2 added:

```ocaml
      Test_recompute_log.suite;
      Test_synthetic_book.suite;
      Test_stress.suite;
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound module Ohcamel.Synthetic_book` (from `test/test_recompute_log.ml` or `test/test_synthetic_book.ml`, whichever the compiler reaches first).

- [ ] **Step 3: Minimal implementation**

Create `lib/synthetic_book.ml`. The book, the limits and the three constants are `bin/main.ml:56–108` verbatim; `gaussian` and `daily_return` are `bin/main.ml:123–128` with the state made a parameter; `rate_beta` is `bin/main.ml:576–581` with its comment.

```ocaml
(* The six-name synthetic book, and the generator that seeds it.

   WHY THIS IS A LIBRARY MODULE. Until this phase the book was six literals at
   the top of bin/main.ml, and that was correct for as long as the only reader
   was a printer. It now has four: the CLI's printers, the stress suite, the
   crisis backtest (which values the book at these marks to fix its weights),
   and the served demo process, whose page has to say "this is the same book
   `make run` prints". Two copies of a book are two books, and the day one of
   them gains a name the other has not is the day the page's figures stop
   being about the terminal's.

   WHAT DOES NOT LIVE HERE. Nothing that formats, and nothing that owns a
   random state. [gaussian] and [daily_return] take the state as a parameter
   because the CLI draws every mode from ONE state in program order and its
   printed tables depend on that order; a state created here would either be a
   second stream (and the tables would move) or a global (and the demo host
   would share draws with the scaling probe). The caller owns the stream. *)

open Core
open Types

let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float

(* Long technology and financials, short energy: a book with real sector
   structure and a genuine short leg, so gross and net differ and the sector
   limits have something to say. *)
let book =
  [
    (sym "AAPL", sec "TECH", 150.00, 400.0);
    (sym "MSFT", sec "TECH", 300.00, 200.0);
    (sym "NVDA", sec "TECH", 900.00, 60.0);
    (sym "JPM", sec "FINANCIALS", 200.00, 250.0);
    (sym "XOM", sec "ENERGY", 100.00, -500.0);
    (sym "CVX", sec "ENERGY", 140.00, -300.0);
  ]

let instruments =
  List.map book ~f:(fun (symbol, sector, _, _) -> { Instrument.symbol; sector })

let limit name scope kind = { Limit.name; scope; kind }

let limits =
  [
    limit "nvda-cap"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Gross_notional (dollars 55_000.0));
    limit "aapl-cap"
      (Limit.Instrument (sym "AAPL"))
      (Limit.Gross_notional (dollars 80_000.0));
    limit "tech-cap"
      (Limit.Sector (sec "TECH"))
      (Limit.Gross_notional (dollars 200_000.0));
    limit "energy-cap"
      (Limit.Sector (sec "ENERGY"))
      (Limit.Gross_notional (dollars 100_000.0));
    limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 400_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 12_000.0));
    limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.02);
    (* Two limits on risk SHARE rather than on notional, so this book exercises
       all four limit kinds and the difference between the two is visible rather
       than described.

       NVDA already has a notional cap above. This one caps its Euler share of
       portfolio VaR, and the two measure genuinely different things: trimming
       an unrelated position raises NVDA's risk share without touching its
       notional at all, and a volatility shock moves this one while leaving the
       notional cap exactly where it was. `make stress` shows precisely that --
       the vol-regime row has zero P&L, unchanged gross, and breaks the sector
       one and nothing else on the page. *)
    limit "nvda-risk"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Component_var (dollars 900.0));
    limit "tech-risk" (Limit.Sector (sec "TECH")) (Limit.Component_var (dollars 2_000.0));
  ]

let starting_cash = dollars 1_000_000.0
let confidence = 0.95
let return_window = 60

(* ------------------------------------------------------------------------ *)
(* The synthetic market                                                      *)
(* ------------------------------------------------------------------------ *)

(* Box-Muller, cosine branch, with the first uniform floored at 1e-12 so the
   log cannot see a zero. This is the ONE generator every synthetic series in
   the project is drawn from -- the demo's ticks, the scaling probe, the
   validation battery's three series, the GARCH study's innovations -- and it
   is written once so that "seed N reproduces" is a statement about a seed
   rather than about which of four copies of this function ran. Two uniforms
   per draw, always, whether or not the sine branch would have been free. *)
let gaussian ~(rng : Random.State.t) ~(sigma : float) : float =
  let u1 = Float.max 1e-12 (Random.State.float rng 1.0) in
  let u2 = Random.State.float rng 1.0 in
  sigma *. Float.sqrt (-2.0 *. Float.log u1) *. Float.cos (2.0 *. Float.pi *. u2)

(* Returns are drawn as normal noise with a small negative drift, so the book
   tends to bleed and the drawdown breaker has something to do. *)
let daily_return ~(rng : Random.State.t) : float = gaussian ~rng ~sigma:0.012 -. 0.0008

(* Rate sensitivity per sector, as a return per percentage point of yield
   change: a 100bp rise costs a technology name 3% and pays an energy name 1%.

   These are assumptions, not measurements, and they are written down here
   rather than buried in a generator because the rate-shock scenario is only as
   meaningful as they are. The signs are the conventional ones -- long-duration
   growth equity discounts badly when rates rise, financials earn a wider spread
   -- and the magnitudes are the order of a real regression rather than the
   result of one.

   In live mode nothing like this is assumed. The betas come out of
   Risk_metrics.beta against the actual FRED series and the actual price
   history, which is the whole point of expressing a macro move as a factor
   shock. This exists so the synthetic book has a factor structure to shock at
   all; a factor uncorrelated with everything would make every beta zero and
   the scenario would truthfully report that nothing moved, which is a real
   state and a poor demonstration. *)
let rate_beta sector =
  match Sector.to_string sector with
  | "TECH" -> -0.030
  | "FINANCIALS" -> 0.009
  | "ENERGY" -> 0.010
  | _ -> 0.0

(* The factor series and the six return windows, written into [graph].

   Daily changes in a ten-year yield, in percentage points -- the units
   fred_client.ml delivers. A standard deviation of 5bp a day is about right
   for DGS10. Each name's window is then a common factor component plus
   idiosyncratic noise, so the betas the rate-shock scenario recovers are the
   ones written above rather than zero.

   DRAW ORDER IS LOAD-BEARING. The factor is drawn first, then one window per
   name in [book] order, which is the order bin/main.ml's inline version drew
   them; the stress table is a printed figure, and it reproduces only if the
   shared state is consumed in the same sequence. Prices and quantities are the
   caller's to set -- they draw nothing, so they may be set before or after. *)
let seed_returns ~(rng : Random.State.t) ~(graph : Graph.t) : unit =
  let factor = Array.init return_window ~f:(fun _ -> gaussian ~rng ~sigma:0.05) in
  Graph.set_factor_returns graph factor;
  List.iter book ~f:(fun (symbol, sector, _, _) ->
      let beta = rate_beta sector in
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun i ->
             (beta *. factor.(i)) +. gaussian ~rng ~sigma:0.009)))
```

Then edit `bin/main.ml`. Three regions.

**(a) `bin/main.ml:45–128`.** Delete from the banner `(* The synthetic book *)` (the three-line banner at :45–47) through `let daily_return () = gaussian ~sigma:0.012 -. 0.0008` at :128 — that is `sym`/`sec`/`dollars`, the `book` literal with its comment, `instruments`, `limit`, the `limits` literal with its comment, `starting_cash`, `default_port`, `confidence`, `return_window`, `steps`, `bar_every`, the `Synthetic market` banner, the `rng` comment and value, `gaussian` and `daily_return`. Put this in its place:

```ocaml
(* ------------------------------------------------------------------------ *)
(* The synthetic book                                                        *)
(* ------------------------------------------------------------------------ *)

let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float

(* The book lives in lib/synthetic_book.ml now, because the served process
   needs the same six names, the same nine limits and the same seeding rule the
   CLI prints, and two copies of a book are two books. These are aliases, not
   values: every printer below reads through them exactly as it did. *)
let book = Synthetic_book.book
let instruments = Synthetic_book.instruments
let limits = Synthetic_book.limits
let starting_cash = Synthetic_book.starting_cash
let confidence = Synthetic_book.confidence
let return_window = Synthetic_book.return_window
let default_port = 8080
let steps = 60
let bar_every = 10

(* ------------------------------------------------------------------------ *)
(* Synthetic market                                                          *)
(* ------------------------------------------------------------------------ *)

(* Seeded explicitly so two runs print the same numbers. A demo whose output
   changes every time cannot be compared against itself, and the first question
   anyone asks of a risk figure is "what did it say last time".

   This is the ONE state every credential-free mode draws from, in program
   order. The generator itself moved to Synthetic_book with the book; what
   stays here is its binding to this process's stream, because the printed
   tables depend on which draw each mode gets and that is a property of the
   program, not of the book. *)
let rng = Random.State.make [| 2026_07_30 |]
let gaussian ~sigma = Synthetic_book.gaussian ~rng ~sigma
let daily_return () = Synthetic_book.daily_return ~rng
```

**(b) `bin/main.ml:559–605`.** Delete `rate_beta` and its comment (`:559–581`, from `(* Rate sensitivity per sector` through `| _ -> 0.0`), and replace `seeded_demo_graph` (`:583–605`) with:

```ocaml
let seeded_demo_graph ?on_compute () =
  let graph =
    Graph.create ?on_compute ~starting_cash ~instruments ~limits ~confidence
      ~return_window ()
  in
  List.iter book ~f:(fun (symbol, _, price, qty) ->
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty));
  (* The factor and the six windows, drawn from the shared state in exactly the
     order the inline version drew them -- factor first, then one window per
     name in book order. Prices and quantities draw nothing, so setting them
     first changes no draw. The stress table is a printed figure and it must
     not move. *)
  Synthetic_book.seed_returns ~rng ~graph;
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  graph
```

**(c) `bin/main.ml:1724–1733`** (inside `run_demo`, from `let last_price = Symbol.Table.create () in` through `Graph.stabilize graph;` before the `mark_equity`). Replace:

```ocaml
  let last_price = Symbol.Table.create () in
  List.iter book ~f:(fun (symbol, _, price, qty) ->
      Hashtbl.set last_price ~key:symbol ~data:price;
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty);
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun _ -> daily_return ())));
  Graph.set_factor_returns graph
    (Array.init return_window ~f:(fun _ -> gaussian ~sigma:0.04));
  Graph.stabilize graph;
```

with:

```ocaml
  let last_price = Symbol.Table.create () in
  List.iter book ~f:(fun (symbol, _, price, qty) ->
      Hashtbl.set last_price ~key:symbol ~data:price;
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty));
  (* Spec §07, amended. The windows and the factor used to be independent
     draws, so every beta the public host's rate-shock scenario recovered was
     fitted to noise and its P&L was a real computation on a meaningless input.
     Each window is now rate_beta x factor + noise -- the structure the CLI's
     stress table has had all along. Bar closes below keep drawing plain
     returns; the structure is in the seed, and the seed is what a beta is
     fitted to. *)
  Synthetic_book.seed_returns ~rng ~graph;
  Graph.stabilize graph;
```

Nothing else in `bin/main.ml` changes in this task: `probe`, `run_synthetic`, `run_backtest_crisis` and `run_demo`'s `demo_limits` all read `book`, `limits`, `starting_cash`, `confidence` and `return_window` through the aliases.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel && make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: the suite is green — `recompute_log` reports 5 passing cases (Task 2's, finishing that task's Step 4) and `synthetic_book` 2 — and the gate prints six `GATE ok` lines. `stress` is the mode that would move if `seed_returns` drew in a different order from the inline version, and `synthetic` is the one that would move if `gaussian`'s arithmetic changed by a term.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/synthetic_book.ml test/test_synthetic_book.ml test/test_ohcamel.ml bin/main.ml
git commit -m "book: the synthetic book and its seeding rule in lib, because the demo host's betas were fitted to noise"
```

---

### Task 4: `Scaling_probe` — the probe as a row, with its own state

**Files:**
- Create: `lib/scaling_probe.ml`
- Create: `test/test_scaling_probe.ml`
- Modify: `bin/main.ml` (the `How the saving scales` section: `probe` and `scaling_report`, at `:383–469` before Task 3's edits shifted the numbering — find them by name)
- Modify: `test/test_ohcamel.ml` (register the suite)
- Test: `test/test_scaling_probe.ml`

**Interfaces:**
- Consumes: `Recompute_log.{create, note, drain, distinct}` (Task 2); `Synthetic_book.{limit, starting_cash, confidence, return_window, daily_return, gaussian}` (Task 3); `Graph.create ?on_compute … ()`, `Graph.set_price`, `Graph.set_qty`, `Graph.set_returns`, `Graph.stabilize`, `Graph.mark_equity`, `Graph.destroy`; `Types.Time.now`, `Types.Time.diff` (`lib/types.ml:178–179`), `Time_ns.Span.to_int_ns : t -> int` (`_opam/lib/core/span_ns_intf.ml:74`, reached as `Time.Span.to_int_ns`); `Exn.protect : f:(unit -> 'a) -> finally:(unit -> unit) -> 'a` (`_opam/lib/base/exn.mli:61`).
- Produces: `Scaling_probe.row = { instrument_count : int; named_nodes : int; nodes_per_tick : float; ns_per_tick : float }`; `Scaling_probe.probe : rng:Random.State.t -> instrument_count:int -> ticks:int -> row`; `Scaling_probe.rows : seed:int -> sizes:int list -> ticks:int -> row list`; `Scaling_probe.default_sizes = [ 10; 100; 400 ]`; `Scaling_probe.default_ticks = 50`. Phase 5's `Reports.compute` calls `rows ~seed:2026_07_30 ~sizes ~ticks` and encodes `instrument_count`, `named_nodes`, `nodes_per_tick`.

- [ ] **Step 1: Write the failing test**

Create `test/test_scaling_probe.ml`:

```ocaml
(* Unit tests for scaling_probe.ml.

   Two of these are hand derivations and two are properties. The 63 is counted
   below, node by node. The per-tick band is a property of the graph's shape:
   a set_price tick reaches 25 named nodes, plus limit:dd-cap when equity has
   moved off its peak, so the average over any run sits between 25 and 26 and
   the band [24, 28] is a statement about STRUCTURE with a margin, not a pin of
   a run. Nothing here reads a README value; the README's 25.6 / 25.2 / 26.0
   are the CLI's shared-state draws and are reproduced by the gate, not here. *)

open Core
module Scaling_probe = Ohcamel.Scaling_probe
module Recompute_log = Ohcamel.Recompute_log
module Graph = Ohcamel.Graph
module Synthetic_book = Ohcamel.Synthetic_book
open Ohcamel.Types

let fresh () = Random.State.make [| 2026_09_03 |]

(* Ten names, SYM0000..SYM0009, all in SEC000 (index / 10 = 0):

     per name    exposure:S, limit:cap-S, feed:S            3 x 10 = 30
     per sector  sector:SEC000                                        1
     singletons  exposure_map, sector_map, gross_exposure,
                 net_exposure, weights, aligned_returns,
                 covariance, covariance_ewma, portfolio_returns,
                 historical_var, expected_shortfall,
                 parametric_var, parametric_var_ewma, attribution,
                 component_var_map, component_var_sector_map,
                 diversification_ratio, var_notional, es_notional,
                 equity, current_drawdown, breaches,
                 portfolio_beta, feed_health                          24
     options     gamma_map, vega_map, portfolio_gamma,
                 portfolio_vega, vega_by_bucket                        5
     portfolio   limit:book-cap, limit:var-cap, limit:dd-cap           3
                                                                    ----
                                                                      63

   The five option singletons exist on a book with no options -- graph.ml builds
   them unconditionally and they evaluate to empty maps -- which is why this is
   63 and the README's older table said 58. *)
let test_ten_names_make_sixty_three_named_nodes () =
  let row = Scaling_probe.probe ~rng:(fresh ()) ~instrument_count:10 ~ticks:5 in
  Alcotest.(check int) "echoes the request" 10 row.Scaling_probe.instrument_count;
  Alcotest.(check int) "63 named nodes" 63 row.Scaling_probe.named_nodes

let test_nodes_per_tick_is_flat_in_the_book () =
  List.iter [ 10; 100 ] ~f:(fun instrument_count ->
      let row = Scaling_probe.probe ~rng:(fresh ()) ~instrument_count ~ticks:20 in
      let per_tick = row.Scaling_probe.nodes_per_tick in
      Alcotest.(check bool)
        (Printf.sprintf "%d names: %.1f nodes per tick, within [24, 28]" instrument_count
           per_tick)
        true
        (Float.( >= ) per_tick 24.0 && Float.( <= ) per_tick 28.0))

(* [rows] owns its state. Two calls with one seed must agree on every
   deterministic field; ns_per_tick is wall-clock and is excluded on purpose. *)
let test_two_runs_with_one_seed_agree () =
  let shape rows =
    List.map rows ~f:(fun (r : Scaling_probe.row) ->
        ( r.Scaling_probe.instrument_count,
          (r.Scaling_probe.named_nodes, r.Scaling_probe.nodes_per_tick) ))
  in
  let a = Scaling_probe.rows ~seed:11 ~sizes:[ 10; 100 ] ~ticks:10 in
  let b = Scaling_probe.rows ~seed:11 ~sizes:[ 10; 100 ] ~ticks:10 in
  Alcotest.(check (list (pair int (pair int (float 0.0)))))
    "same seed, same rows" (shape a) (shape b)

(* THE ONE THAT MATTERS for the page. The served graph's recompute log is what
   "26 of 53 ran this frame" is made of, and the probe runs in the same process
   against the same Incremental state. Its thousands of recomputations must not
   land in the served graph's log -- and they cannot, because the probe's graph
   has its own on_compute and the served graph's inputs never change. Asserted,
   because "cannot" is the kind of claim that is true until a refactor shares a
   log for convenience. *)
let test_a_probe_never_reaches_a_served_graphs_log () =
  let log = Recompute_log.create () in
  let graph =
    Graph.create
      ~on_compute:(Recompute_log.note log)
      ~starting_cash:Synthetic_book.starting_cash ~instruments:Synthetic_book.instruments
      ~limits:Synthetic_book.limits ~confidence:Synthetic_book.confidence
      ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter Synthetic_book.book ~f:(fun (symbol, _, price, qty) ->
          Graph.set_price graph symbol (Price.of_float price);
          Graph.set_qty graph symbol (Qty.of_float qty));
      Synthetic_book.seed_returns ~rng:(Random.State.make [| 7 |]) ~graph;
      Graph.stabilize graph;
      ignore (Recompute_log.drain log : (string * int) list);
      let before = Recompute_log.total log in
      let (_ : Scaling_probe.row) =
        Scaling_probe.probe ~rng:(fresh ()) ~instrument_count:100 ~ticks:20
      in
      Alcotest.(check (list (pair string int)))
        "the served graph's frame table is still empty" [] (Recompute_log.drain log);
      Alcotest.(check int) "and its lifetime table did not move" before
        (Recompute_log.total log))

let suite =
  ( "scaling_probe",
    [
      Alcotest.test_case "ten names make 63 named nodes" `Quick
        test_ten_names_make_sixty_three_named_nodes;
      Alcotest.test_case "nodes per tick is flat in the book" `Quick
        test_nodes_per_tick_is_flat_in_the_book;
      Alcotest.test_case "two runs with one seed agree" `Quick
        test_two_runs_with_one_seed_agree;
      Alcotest.test_case "A PROBE NEVER REACHES A SERVED GRAPH'S LOG" `Quick
        test_a_probe_never_reaches_a_served_graphs_log;
    ] )
```

Register it in `test/test_ohcamel.ml`, after `Test_synthetic_book.suite;`:

```ocaml
      Test_synthetic_book.suite;
      Test_scaling_probe.suite;
      Test_stress.suite;
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound module Ohcamel.Scaling_probe`.

- [ ] **Step 3: Minimal implementation**

Create `lib/scaling_probe.ml`. The body is `bin/main.ml`'s `probe` with the counter replaced by a `Recompute_log`, the destroy moved into `Exn.protect`, and the state made a parameter:

```ocaml
(* How the cost of one tick moves as the book grows.

   The percentage `make run` prints is a floor, and a small book is the worst
   case for it: with six names the portfolio-level nodes -- gross, net,
   weights, the three risk numbers -- genuinely depend on everything, so they
   dominate the tally and there is not much left to skip.

   The interesting quantity is how the cost of ONE tick moves as the book
   grows. In a poll-and-recompute engine it grows with the book, because
   everything is redone. Here it does not move at all: a tick still touches one
   instrument, one sector, the aggregates, and the limits that read them. This
   probe measures exactly that, at whatever book sizes it is asked for.

   WHY THE STATE IS A PARAMETER. The CLI draws every mode from one shared
   Random.State in program order, and its printed table -- 25.6 / 25.2 / 26.0
   -- is the average over a path drawn from wherever that stream had reached
   after sixty synthetic events. The served process has no such stream and
   runs this at startup, from a seed of its own; [rows] builds that state and
   never touches anyone else's. The two therefore agree on [named_nodes]
   exactly and on [nodes_per_tick] to within the drawdown node's coin flips,
   which is what the page's computed-vs-quoted line is for.

   WHY THE GRAPH IS DESTROYED IN Exn.protect. Incremental's state is shared by
   every Graph.t in the process. A probe graph left alive after a raise would
   recompute on every stabilize of the served graph forever, and the symptom
   -- a process-wide counter climbing for no reason -- is exactly the number
   the smoke suite reads to decide the deploy is alive. *)

open Core
open Types

type row = {
  instrument_count : int;
  (* Every named node ran at least once during seeding, so this is the size of
     the graph -- and therefore what a polling engine would redo per event. *)
  named_nodes : int;
  nodes_per_tick : float;
  (* Wall clock, this process, this machine. Real, and labelled as what it is
     wherever it is shown; never compared against the README's bench table,
     which ran under core_bench on named hardware. *)
  ns_per_tick : float;
}

let default_sizes = [ 10; 100; 400 ]
let default_ticks = 50

let probe ~(rng : Random.State.t) ~(instrument_count : int) ~(ticks : int) : row =
  if ticks < 1 then invalid_argf "scaling_probe: need at least one tick, got %d" ticks ();
  let log = Recompute_log.create () in
  let symbols =
    List.init instrument_count ~f:(fun i -> Symbol.of_string (Printf.sprintf "SYM%04d" i))
  in
  let instruments =
    List.map symbols ~f:(fun symbol ->
        (* Ten names per sector, so the sector nodes are neither degenerate (one
           member each) nor a single bucket holding the whole book. *)
        {
          Instrument.symbol;
          sector =
            Sector.of_string
              (Printf.sprintf "SEC%03d"
                 (Int.of_string (String.drop_prefix (Symbol.to_string symbol) 3) / 10));
        })
  in
  (* One cap per name, as a real book has, plus the three portfolio limits. The
     per-name limits are the part that a polling engine re-evaluates in full on
     every tick and that this one leaves untouched. *)
  let limits =
    List.map symbols ~f:(fun symbol ->
        Synthetic_book.limit
          ("cap-" ^ Symbol.to_string symbol)
          (Limit.Instrument symbol)
          (Limit.Gross_notional (Notional.of_float 100_000.0)))
    @ [
        Synthetic_book.limit "book-cap" Limit.Portfolio
          (Limit.Gross_notional (Notional.of_float 1e9));
        Synthetic_book.limit "var-cap" Limit.Portfolio
          (Limit.Value_at_risk (Notional.of_float 1e9));
        Synthetic_book.limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.5);
      ]
  in
  let graph =
    Graph.create
      ~on_compute:(Recompute_log.note log)
      ~starting_cash:Synthetic_book.starting_cash ~instruments ~limits
      ~confidence:Synthetic_book.confidence ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let prices = Symbol.Table.create () in
      List.iter symbols ~f:(fun symbol ->
          Hashtbl.set prices ~key:symbol ~data:100.0;
          Graph.set_price graph symbol (Price.of_float 100.0);
          Graph.set_qty graph symbol (Qty.of_float 100.0);
          Graph.set_returns graph symbol
            (Array.init Synthetic_book.return_window ~f:(fun _ ->
                 Synthetic_book.daily_return ~rng)));
      Graph.stabilize graph;
      Graph.mark_equity graph;
      Graph.stabilize graph;
      let named_nodes = Recompute_log.distinct log in
      (* Construction and seeding are not ticks; do not charge them. *)
      ignore (Recompute_log.drain log : (string * int) list);
      let total = ref 0 in
      let ring = Array.of_list symbols in
      let started = Time.now () in
      for i = 1 to ticks do
        let symbol = ring.(i % Array.length ring) in
        let price =
          Hashtbl.find_exn prices symbol
          *. (1.0 +. Synthetic_book.gaussian ~rng ~sigma:0.004)
        in
        Hashtbl.set prices ~key:symbol ~data:price;
        Graph.set_price graph symbol (Price.of_float price);
        Graph.stabilize graph;
        total :=
          !total
          + List.fold (Recompute_log.drain log) ~init:0 ~f:(fun acc (_, n) -> acc + n)
      done;
      let elapsed_ns = Time.Span.to_int_ns (Time.diff (Time.now ()) started) in
      {
        instrument_count;
        named_nodes;
        nodes_per_tick = float_of_int !total /. float_of_int ticks;
        ns_per_tick = float_of_int elapsed_ns /. float_of_int ticks;
      })

(* One state for the whole table, consumed size by size, as the CLI's shared
   state is. Never a global: the served process calls this once at startup and
   the demo feed must not find its draws shifted by it. *)
let rows ~(seed : int) ~(sizes : int list) ~(ticks : int) : row list =
  let rng = Random.State.make [| seed |] in
  List.map sizes ~f:(fun instrument_count -> probe ~rng ~instrument_count ~ticks)
```

Then in `bin/main.ml`, delete `probe` (from `let probe ~(instrument_count : int) ~(ticks : int) =` through `(graph_size, float_of_int !total /. float_of_int ticks)`) and the four-paragraph comment above it that begins `(* The percentage above is a floor`, and replace `scaling_report` with:

```ocaml
(* The probe itself is lib/scaling_probe.ml, because the served process runs
   the same measurement at startup. What stays here is the table, and the one
   thing that is a property of THIS program rather than of the probe: it draws
   from the shared state, after the sixty events above, so the README's
   per-tick column is this stream's and the gate holds it. *)
let scaling_report () =
  printf "%s\n  HOW THAT SCALES\n%s\n\n" (rule 106) (rule 106);
  printf "  %12s %16s %18s %16s\n" "instruments" "nodes in graph" "nodes per tick"
    "if polled";
  printf "  %s\n" (rule 66);
  List.iter Scaling_probe.default_sizes ~f:(fun instrument_count ->
      let row =
        Scaling_probe.probe ~rng ~instrument_count ~ticks:Scaling_probe.default_ticks
      in
      printf "  %12d %16d %18.1f %16d\n" row.Scaling_probe.instrument_count
        row.Scaling_probe.named_nodes row.Scaling_probe.nodes_per_tick
        row.Scaling_probe.named_nodes);
  printf
    "\n\
    \  The middle column is flat and the right one is not. That is the whole\n\
    \  argument: the cost of an event is set by what the event touches, not by\n\
    \  how large the book is.\n\n"
```

`Counter` is no longer referenced by the probe; it is still used by `run_synthetic`, `final_report` and live mode and stays.

- [ ] **Step 4: Run the tests and see them pass**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh synthetic
```

Expected: `scaling_probe` reports 4 passing cases; `GATE ok        synthetic`. The scaling table is the last thing `synthetic` prints, and it is the only mode that runs the probe. If the per-tick column moved, the draw order changed: compare the order of `daily_return` and `gaussian ~sigma:0.004` calls against the deleted `probe` before touching anything else.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/scaling_probe.ml test/test_scaling_probe.ml test/test_ohcamel.ml bin/main.ml
git commit -m "graph: the scaling probe as a row with its own state, because the served host runs it at startup and must not shift the demo's draws"
```

---

### Task 5: `Validation_report`, part 1 — the synthetic battery as a record

**Files:**
- Create: `lib/validation_report.ml`
- Create: `test/test_validation_report.ml`
- Modify: `bin/main.ml` — `backtest_rng`, `backtest_gaussian`, `backtest_length`, `backtest_series`, `backtest_row` and `run_backtest` (`:742–862` before Tasks 3–4 shifted the numbering; find them by name). `duration_cell` at `:737` stays: it is formatting.
- Modify: `test/test_ohcamel.ml` (register the suite), `test/dune` (add `yojson`)
- Test: `test/test_validation_report.ml`

**Interfaces:**
- Consumes: `Var_backtest.rolling ~returns ~window ~confidence ~estimator : Observation.t list`, `Var_backtest.run ~observations ~estimator ~confidence : report`, `Var_backtest.exceedances : Observation.t list -> bool array`, `Var_backtest.Observation.{var, realised}` (`lib/var_backtest.ml:70`, `[@@deriving fields ~getters]`), `Var_backtest.Estimator.t` (`:81–92`), the `report` getters (`:464–500`, `[@@deriving fields ~getters]` — so `Var_backtest.estimator : report -> Estimator.t` exists), `Var_backtest.rejected ?alpha`, `Var_backtest.conditional_coverage_p`; `Synthetic_book.{gaussian, confidence, return_window}` (Task 3); `Vol_estimators.Ewma.default_lambda` (= 0.94); `Random.State.make : int array -> t` (`_opam/lib/base/random.mli:112`); `Array.foldi` (used at `lib/vol_estimators.ml:302`); `Quoted.json : string` (Phase 1 — the shape at Phase 1's plan `:805–875`, with row keys `series`/`window`, `estimator`, `n`, `exceptions`, `expected`, `kupiec_p`, `independence_p`, `joint_p`, `duration_p`, `duration_shape`, `zone`, `verdict`, and `burst` on crisis rows — **not** the hypothetical shape in this plan's preamble, which Phase 1 superseded); `Yojson.Safe.from_string`, `Yojson.Safe.Util.{member, to_list, to_string, to_int, to_number}` (`_opam/lib/yojson`).
- Produces (exactly what Phase 5's `Reports` consumes): `Validation_report.Series.t = { name : string; description : string; length : int; forecasts : int }`; `Validation_report.Window_facts.t = { name : string; description : string; first : string; last : string; sessions : int; forecasts : int; symbols : string list; worst_day : float; best_day : float; dates : string array; realised : float array }`; `Validation_report.Row.t = { label : string; report : Var_backtest.report; hits : int list; burst : (int * int) option; var_series : float array; realised_series : float array }`; `Validation_report.t = { rows : Row.t list; rejected : int; most_severe : Row.t option; series : Series.t list; windows : Window_facts.t list; confidence : float; window : int; alpha : float; ewma_lambda : float }`; `Validation_report.seed = 2026_08_24`; `Validation_report.length = 1000`; `Validation_report.estimators : Var_backtest.Estimator.t list`; `Validation_report.synthetic_series : rng:Random.State.t -> (string * string * float array) list`; `Validation_report.row : label:string -> returns:float array -> estimator:Var_backtest.Estimator.t -> Row.t` (Task 6 gives it `?burst_span`); `Validation_report.most_severe : Row.t list -> Row.t option`; `Validation_report.synthetic : unit -> t`. Task 6 adds `crisis`, `worst_burst`, `burst_span`.

- [ ] **Step 1: Write the failing test**

Create `test/test_validation_report.ml`:

```ocaml
(* Reproducibility pins for validation_report.ml.

   THESE ARE PINS, NOT DERIVATIONS. Every expected value in the two table
   tests below is read out of web/quoted.json (Phase 1's transcription of
   README.md, compiled in as Quoted.json), and what is asserted is that the
   library reproduces the README's nine-by-nine tables exactly: exceptions,
   zones and verdicts to the integer and the word, p-values to the four
   decimals the README prints, Weibull shapes to two. Nothing here says WHY
   the vol-regime parametric row is rejected; var_backtest.ml's own tests do
   that against hand-derived statistics. What this file says is that moving the
   battery out of bin/main.ml did not move a single cell of the published
   result -- which is the byte-identical gate, restated as a unit test that
   runs without the CLI.

   The remaining tests are hand derivations about STRUCTURE: the order the
   generator consumes its stream in, the hit indices, the series lengths. *)

open Core
module Validation_report = Ohcamel.Validation_report
module Var_backtest = Ohcamel.Var_backtest
module Synthetic_book = Ohcamel.Synthetic_book
module U = Yojson.Safe.Util

let quoted = lazy (Yojson.Safe.from_string Ohcamel.Quoted.json)
let quoted_rows table = U.to_list (U.member "rows" (U.member table (Lazy.force quoted)))

(* One README row, found by its label column and its estimator string -- the
   same two strings the CLI prints in its first two columns. *)
let quoted_row table ~key ~value ~estimator =
  List.find_exn (quoted_rows table) ~f:(fun r ->
      String.equal (U.to_string (U.member key r)) value
      && String.equal (U.to_string (U.member "estimator" r)) estimator)

(* Compared as the strings printf would print, because the README's cells ARE
   printf's output ("%.4f" for p-values, "%.2f" for shapes, "%.1f" for the
   expected count). Rounding the computed float and comparing with a tolerance
   would disagree with the README at an exact tie and agree everywhere else,
   which is a worse test than the one that asks the same question the README
   asked. *)
let check_row_against_quoted ~table ~key (row : Validation_report.Row.t) =
  let r = row.Validation_report.Row.report in
  let estimator = Var_backtest.Estimator.to_string (Var_backtest.estimator r) in
  let q = quoted_row table ~key ~value:row.Validation_report.Row.label ~estimator in
  let where what = Printf.sprintf "%s/%s: %s" row.Validation_report.Row.label estimator what in
  let same fmt name value =
    Alcotest.(check string) (where name)
      (Printf.sprintf fmt (U.to_number (U.member name q)))
      (Printf.sprintf fmt value)
  in
  Alcotest.(check int) (where "n") (U.to_int (U.member "n" q)) (Var_backtest.observations r);
  Alcotest.(check int) (where "exceptions")
    (U.to_int (U.member "exceptions" q))
    (Var_backtest.exceptions r);
  same "%.1f" "expected" (Var_backtest.expected_exceptions r);
  same "%.4f" "kupiec_p" (Var_backtest.kupiec_p r);
  same "%.4f" "independence_p" (Var_backtest.independence_p r);
  same "%.4f" "joint_p" (Var_backtest.conditional_coverage_p r);
  (match (U.member "duration_p" q, Var_backtest.duration_p r, Var_backtest.duration_shape r) with
  | `Null, None, None -> ()
  | `Null, _, _ -> Alcotest.fail (where "duration: the README prints --, the library computed one")
  | _, Some p, Some shape ->
      same "%.4f" "duration_p" p;
      same "%.2f" "duration_shape" shape
  | _, _, _ -> Alcotest.fail (where "duration: the README has a value, the library has none"));
  Alcotest.(check string) (where "zone")
    (U.to_string (U.member "zone" q))
    (Var_backtest.Zone.to_string (Var_backtest.zone r));
  Alcotest.(check string) (where "verdict")
    (U.to_string (U.member "verdict" q))
    (if Var_backtest.rejected r then "REJECTED" else "ok")

let labels (rows : Validation_report.Row.t list) =
  List.map rows ~f:(fun row ->
      ( row.Validation_report.Row.label,
        Var_backtest.Estimator.to_string
          (Var_backtest.estimator row.Validation_report.Row.report) ))

let test_the_nine_synthetic_rows_are_the_readmes () =
  let t = Validation_report.synthetic () in
  Alcotest.(check (list (pair string string)))
    "nine rows, series-major, in the README's order"
    [
      ("iid-normal", "historical"); ("iid-normal", "parametric"); ("iid-normal", "ewma(0.94)");
      ("vol-regime", "historical"); ("vol-regime", "parametric"); ("vol-regime", "ewma(0.94)");
      ("jumps", "historical"); ("jumps", "parametric"); ("jumps", "ewma(0.94)");
    ]
    (labels t.Validation_report.rows);
  List.iter t.Validation_report.rows ~f:(check_row_against_quoted ~table:"battery" ~key:"series");
  Alcotest.(check (float 1e-12)) "confidence" 0.95 t.Validation_report.confidence;
  Alcotest.(check int) "window" 60 t.Validation_report.window;
  Alcotest.(check (float 1e-12)) "alpha" 0.05 t.Validation_report.alpha;
  Alcotest.(check (float 1e-12)) "ewma lambda" 0.94 t.Validation_report.ewma_lambda

(* Read off the quoted table: three rows say REJECTED -- iid-normal/ewma at
   joint p 0.0219, vol-regime/parametric at 0.0373, jumps/historical at 0.0000
   -- and the smallest of the three is the jumps/historical row, the estimator
   that saw fifty identical -8% days and forecast none of them. *)
let test_most_severe_is_the_smallest_joint_p_among_the_rejected () =
  let t = Validation_report.synthetic () in
  Alcotest.(check int) "three rejected" 3 t.Validation_report.rejected;
  match t.Validation_report.most_severe with
  | None -> Alcotest.fail "three rejections and no most-severe"
  | Some row ->
      Alcotest.(check string) "the jumps series" "jumps" row.Validation_report.Row.label;
      Alcotest.(check string) "the historical estimator" "historical"
        (Var_backtest.Estimator.to_string
           (Var_backtest.estimator row.Validation_report.Row.report))

(* [hits] are indices into forecast space, so they must be strictly increasing,
   inside [0, forecasts), and exactly as many as the report counts. [var_series]
   and [realised_series] are the same observations laid out for a chart, so a
   hit at index i is exactly the i where realised < -var. *)
let test_hit_indices_index_the_forecast_series () =
  let t = Validation_report.synthetic () in
  List.iter t.Validation_report.rows ~f:(fun row ->
      let open Validation_report.Row in
      let forecasts = Array.length row.var_series in
      Alcotest.(check int) (row.label ^ ": 940 forecasts") 940 forecasts;
      Alcotest.(check int) (row.label ^ ": realised alongside") forecasts
        (Array.length row.realised_series);
      Alcotest.(check int) (row.label ^ ": one index per exception")
        (Var_backtest.exceptions row.report) (List.length row.hits);
      Alcotest.(check bool) (row.label ^ ": strictly increasing") true
        (List.is_sorted_strictly row.hits ~compare:Int.compare);
      List.iter row.hits ~f:(fun i ->
          Alcotest.(check bool) (Printf.sprintf "%s: index %d is a hit" row.label i) true
            (i >= 0 && i < forecasts
            && Float.( < ) row.realised_series.(i) (-.row.var_series.(i)))))

let test_series_facts () =
  let t = Validation_report.synthetic () in
  Alcotest.(check (list (pair string (pair int int))))
    "three series of 1000, 940 forecasts each"
    [ ("iid-normal", (1000, 940)); ("vol-regime", (1000, 940)); ("jumps", (1000, 940)) ]
    (List.map t.Validation_report.series ~f:(fun s ->
         Validation_report.Series.(s.name, (s.length, s.forecasts))));
  Alcotest.(check int) "no windows on the synthetic report" 0
    (List.length t.Validation_report.windows)

(* THE DRAW ORDER, derived by hand. bin/main.ml built the three series as one
   list literal, and OCaml evaluates a list literal's elements RIGHT TO LEFT
   (verified on this switch: [f 1; f 2; f 3] prints 321). So the stream was
   consumed jumps first, then vol-regime, then iid-normal, and the README's
   table is the table of THAT order. Two uniforms per gaussian, always. jumps
   draws on 950 of its 1000 days (every twentieth is a literal -0.08), and
   vol-regime on all 1000: the first iid-normal value is therefore gaussian draw
   number 1951 of a fresh state, whatever sigma the 1950 before it used. *)
let test_the_generator_reads_one_stream_jumps_first () =
  let rng = Random.State.make [| Validation_report.seed |] in
  let generated = Validation_report.synthetic_series ~rng in
  let iid_normal = List.Assoc.find_exn (List.map generated ~f:(fun (n, _, r) -> (n, r))) ~equal:String.equal "iid-normal" in
  let replay = Random.State.make [| Validation_report.seed |] in
  for _ = 1 to 950 + 1000 do
    ignore (Synthetic_book.gaussian ~rng:replay ~sigma:1.0 : float)
  done;
  Alcotest.(check (float 0.0)) "iid-normal.(0) is draw 1951"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011) iid_normal.(0);
  Alcotest.(check (float 0.0)) "iid-normal.(1) is draw 1952"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011) iid_normal.(1)

let suite =
  ( "validation_report",
    [
      Alcotest.test_case "THE NINE SYNTHETIC ROWS ARE THE README'S" `Quick
        test_the_nine_synthetic_rows_are_the_readmes;
      Alcotest.test_case "most severe is the smallest joint p among the rejected" `Quick
        test_most_severe_is_the_smallest_joint_p_among_the_rejected;
      Alcotest.test_case "hit indices index the forecast series" `Quick
        test_hit_indices_index_the_forecast_series;
      Alcotest.test_case "series facts" `Quick test_series_facts;
      Alcotest.test_case "the generator reads one stream, jumps first" `Quick
        test_the_generator_reads_one_stream_jumps_first;
    ] )
```

Register it in `test/test_ohcamel.ml`, after `Test_scaling_probe.suite;`:

```ocaml
      Test_scaling_probe.suite;
      Test_validation_report.suite;
      Test_stress.suite;
```

And make the test binary's use of Yojson explicit. `dune-project` does not set `implicit_transitive_deps false`, so `Yojson` already resolves through `ohcamel`; naming it is what keeps that true if someone tightens the project. `test/dune` becomes:

```lisp
(test
 (name test_ohcamel)
 ; qcheck-core supplies the generators and qcheck-alcotest turns a property into
 ; an ordinary alcotest case, which is what keeps the example-based and
 ; property-based suites in ONE runner. Two runners would mean two things to
 ; remember to run, and the one that gets forgotten is the one that fails.
 ; yojson: the reproducibility pins parse web/quoted.json (Quoted.json).
 (libraries ohcamel alcotest qcheck-core qcheck-alcotest yojson))
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound module Ohcamel.Validation_report`.

- [ ] **Step 3: Minimal implementation**

Create `lib/validation_report.ml`:

```ocaml
(* The VaR validation battery as a value.

   WHY A RECORD. `make backtest` and `make backtest-crisis` were two printers
   with the battery inlined into each; the served page needs the same nine
   rows twice over, plus the hit indices and the forecast series a chart is
   made of, none of which a printf can hand back. So the battery is computed
   here, once, into a record, and both the CLI and the page are readers of it.
   Nothing in this file is a statistic: every p-value, zone and verdict is
   Var_backtest's, reached through the same [rolling] then [run] the CLI has
   always used. This module decides WHICH series and WHICH estimators, and
   lays the answers out.

   WHAT IS NOT HERE. Formatting -- money, percentages, the rules and the prose
   stay in bin/main.ml. And no random state: [synthetic] makes its own from
   [seed] every call, because the README's table is the table of a fresh
   Random.State.make [| 2026_08_24 |] and nothing else, and the CLI's shared
   demo stream must never be touched by it. *)

open Core
open Types

module Series = struct
  type t = { name : string; description : string; length : int; forecasts : int }
end

(* One real window, as the page and the CLI describe it. [dates] and
   [realised] are in FORECAST space -- one entry per observation, aligned with
   every row's [var_series] -- not in session space; the alignment is derived
   at [crisis] in Task 6. *)
module Window_facts = struct
  type t = {
    name : string;
    description : string;
    first : string;
    last : string;
    sessions : int;
    forecasts : int;
    symbols : string list;
    worst_day : float;
    best_day : float;
    dates : string array;
    realised : float array;
  }
end

module Row = struct
  type t = {
    (* The series name or the window name -- the CLI's first column. *)
    label : string;
    (* Var_backtest's own report, whole. The estimator travels inside it
       (Var_backtest.estimator), so a row cannot be separated from which
       lambda produced it. *)
    report : Var_backtest.report;
    (* Indices into forecast space where realised < -var, strictly increasing. *)
    hits : int list;
    (* (count, start index) of the worst [burst_span] run; None on synthetic
       rows, which never printed one. Filled by Task 6. *)
    burst : (int * int) option;
    var_series : float array;
    realised_series : float array;
  }
end

type t = {
  rows : Row.t list;
  rejected : int;
  most_severe : Row.t option;
  series : Series.t list;
  windows : Window_facts.t list;
  confidence : float;
  window : int;
  alpha : float;
  ewma_lambda : float;
}

let seed = 2026_08_24
let length = 1000
let alpha = 0.05

(* The three estimators, in the CLI's column order. The third is the argument
   of Phase A, put in front of the same battery as the other two rather than
   described: if the equal-weighted parametric estimator is rejected on the
   vol-regime series and the EWMA one is not, that is the claim demonstrated.
   If EWMA is rejected too, the table says so, which is what a validation
   suite is for. *)
let estimators =
  [
    Var_backtest.Estimator.Historical;
    Var_backtest.Estimator.Parametric;
    Var_backtest.Estimator.Parametric_ewma Vol_estimators.Ewma.default_lambda;
  ]

(* The three synthetic series, drawn from [rng] in the order that reproduces the
   README.

   DRAW ORDER IS LOAD-BEARING, AND IT IS NOT THE LIST'S ORDER. bin/main.ml
   built these as one list literal, and OCaml evaluates a list literal's
   elements right to left -- so for as long as that table has existed, the
   stream was consumed jumps first, then vol-regime, then iid-normal. Written
   here as three lets in exactly that order, so the fact is on the page rather
   than in the compiler, and the list is assembled afterwards. The jumps series
   does not draw on a jump day: every twentieth value is the literal -0.08, so
   it consumes 950 draws, not 1000, and iid-normal's first value is draw 1951
   of a fresh state. test_validation_report.ml pins that number. *)
let synthetic_series ~(rng : Random.State.t) : (string * string * float array) list =
  let gaussian ~sigma = Synthetic_book.gaussian ~rng ~sigma in
  let jumps =
    Array.init length ~f:(fun i -> if i % 20 = 19 then -0.08 else gaussian ~sigma:0.004)
  in
  let vol_regime =
    Array.init length ~f:(fun i -> gaussian ~sigma:(if i < 600 then 0.006 else 0.024))
  in
  let iid_normal = Array.init length ~f:(fun _ -> gaussian ~sigma:0.011) in
  [
    ( "iid-normal",
      "Independent normal returns -- exactly what the parametric estimator assumes.",
      iid_normal );
    ( "vol-regime",
      "Calm for 600 days, then four times as volatile. The window takes 60 days to \
       notice.",
      vol_regime );
    ( "jumps",
      "Quiet days with an identical -8% loss every twentieth. Exactly 5% of days are the \
       tail.",
      jumps );
  ]

(* [rolling] then [run], rather than [of_returns], so the observations are in
   hand for the hit indices and the two series. var_backtest.ml exports both
   steps and composes them the same way, so this is the same path and not a
   second one. *)
let row ~(label : string) ~(returns : float array) ~(estimator : Var_backtest.Estimator.t)
    : Row.t =
  let observations =
    Var_backtest.rolling ~returns ~window:Synthetic_book.return_window
      ~confidence:Synthetic_book.confidence ~estimator
  in
  let report =
    Var_backtest.run ~observations ~estimator ~confidence:Synthetic_book.confidence
  in
  let hits =
    Array.foldi (Var_backtest.exceedances observations) ~init:[] ~f:(fun i acc hit ->
        if hit then i :: acc else acc)
    |> List.rev
  in
  {
    Row.label;
    report;
    hits;
    burst = None;
    var_series = Array.of_list_map observations ~f:Var_backtest.Observation.var;
    realised_series = Array.of_list_map observations ~f:Var_backtest.Observation.realised;
  }

(* The WORST failure rather than the first: the rejected row with the smallest
   conditional-coverage p. A table of p-values is a summary; the failure is the
   finding, and the most severe one is the finding worth printing in full.
   List.min_elt keeps the first of equal minima, as the CLI's did. *)
let most_severe (rows : Row.t list) : Row.t option =
  List.filter rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report)
  |> List.min_elt ~compare:(fun a b ->
      Float.compare
        (Var_backtest.conditional_coverage_p a.Row.report)
        (Var_backtest.conditional_coverage_p b.Row.report))

let synthetic () : t =
  let rng = Random.State.make [| seed |] in
  let generated = synthetic_series ~rng in
  let rows =
    List.concat_map generated ~f:(fun (label, _, returns) ->
        List.map estimators ~f:(fun estimator -> row ~label ~returns ~estimator))
  in
  {
    rows;
    rejected = List.count rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report);
    most_severe = most_severe rows;
    series =
      List.map generated ~f:(fun (name, description, returns) ->
          {
            Series.name;
            description;
            length = Array.length returns;
            forecasts = Array.length returns - Synthetic_book.return_window;
          });
    windows = [];
    confidence = Synthetic_book.confidence;
    window = Synthetic_book.return_window;
    alpha;
    ewma_lambda = Vol_estimators.Ewma.default_lambda;
  }
```

Then `bin/main.ml`. Delete `backtest_rng`, `backtest_gaussian`, `backtest_length` and `backtest_series` (from `let backtest_rng = Random.State.make [| 2026_08_24 |]` through the closing `]` of `backtest_series`). Keep `let backtest_window = return_window` — the crisis mode still reads it until Task 6. Replace `backtest_row` and `run_backtest` with:

```ocaml
(* The battery is lib/validation_report.ml now; this is its printer. Every
   number below is read out of the record, and the prose is the prose. *)
let backtest_row (row : Validation_report.Row.t) =
  let r = row.Validation_report.Row.report in
  printf "  %-12s %-12s %6d %8d %9.1f %9.4f %9.4f %9.4f  %14s  %-7s %s\n"
    row.Validation_report.Row.label
    (Var_backtest.Estimator.to_string (Var_backtest.estimator r))
    (Var_backtest.observations r) (Var_backtest.exceptions r)
    (Var_backtest.expected_exceptions r)
    (Var_backtest.kupiec_p r)
    (Var_backtest.independence_p r)
    (Var_backtest.conditional_coverage_p r)
    (duration_cell r)
    (Var_backtest.Zone.to_string (Var_backtest.zone r))
    (if Var_backtest.rejected r then "REJECTED" else "ok")

let run_backtest () =
  let report = Validation_report.synthetic () in
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  BACKTEST (VaR model validation, no credentials, no network)\n\n";
  printf "  confidence      %.0f%%\n" (report.Validation_report.confidence *. 100.0);
  printf "  window          %d observations, rolling\n" report.Validation_report.window;
  printf "  series length   %d, so %d forecasts each\n" Validation_report.length
    (Validation_report.length - report.Validation_report.window);
  printf
    "  estimators      historical, parametric (equal-weighted), parametric (EWMA at\n\
    \                  lambda = %.2f). The last two differ ONLY in how the window is\n\
    \                  weighted, so a difference in verdict is a statement about\n\
    \                  weighting and about nothing else.\n"
    report.Validation_report.ewma_lambda;
  printf
    "  discipline      each forecast is built from the %d days BEFORE the day it is\n\
    \                  scored against, and cannot see that day.\n\n"
    report.Validation_report.window;
  printf "%s\n  COVERAGE AND INDEPENDENCE\n%s\n\n" (rule 106) (rule 106);
  printf "  %-12s %-12s %6s %8s %9s %9s %9s %9s  %14s  %-7s %s\n" "series" "estimator" "n"
    "excepts" "expected" "Kupiec p" "indep p" "joint p" "duration p" "Basel" "verdict";
  printf "  %s\n" (rule 116);
  List.iter report.Validation_report.rows ~f:backtest_row;
  printf "  %s\n" (rule 116);
  printf
    "\n\
    \  indep p     Christoffersen's FIRST-ORDER Markov test: does a breach yesterday\n\
    \              predict one today. Blind to a cluster that is not on adjacent days.\n\
    \  duration p  Christoffersen-Pelletier: fits a Weibull to the waiting times\n\
    \              BETWEEN breaches and tests the memoryless case b = 1. b < 1 is\n\
    \              clustering; b > 1 is more regular than chance. Sees a burst\n\
    \              whatever its spacing. Reported beside the joint verdict rather\n\
    \              than folded into it -- conditional coverage is Kupiec plus the\n\
    \              Markov test and its degrees of freedom follow from exactly those\n\
    \              two, so adding a third would give it a distribution nobody has\n\
    \              derived.\n\n";
  List.iter report.Validation_report.series ~f:(fun s ->
      printf "  %-12s %s\n" s.Validation_report.Series.name
        s.Validation_report.Series.description);
  (* One report in full, and it is the WORST failure rather than the first. *)
  (match report.Validation_report.most_severe with
  | None ->
      printf
        "\n\
        \  Nothing was rejected, which on this set of series would itself be a\n\
        \  finding: two of the three are built to break an equal-weighted window.\n\n"
  | Some row ->
      printf "\n%s\n  IN FULL: the most severe rejection (%s)\n%s\n\n" (rule 106)
        row.Validation_report.Row.label (rule 106);
      printf "%s\n" (Var_backtest.to_string row.Validation_report.Row.report));
  printf
    "\n\
    \  A rejection here is the suite working. The point of a coverage test is\n\
    \  not that the model passes it -- it is that a model which does not can be\n\
    \  told apart from one which does, before the difference is discovered by\n\
    \  losing money.\n\n"
```

Two things to notice while editing. First, the `printf "  confidence ..."` line used to read the CLI's `confidence` alias and now reads the record's; both are `Synthetic_book.confidence`, so the byte is the same. Second, `backtest_series` was a top-level value evaluated at program start in EVERY mode; it drew from its own state and never from `rng`, so removing it moves no other mode's draws — `stress` and `synthetic` are the modes that would say otherwise.

- [ ] **Step 4: Run the tests and see them pass**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: `validation_report` reports 5 passing cases and the gate prints six `GATE ok` lines. `backtest` is the mode this task can move. If the pin test fails on the iid-normal rows only, the three lets in `synthetic_series` are in the wrong order — the gate's diff would show the same rows — and the draw-order test above says which draw the first value should be.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/validation_report.ml test/test_validation_report.ml test/test_ohcamel.ml test/dune bin/main.ml
git commit -m "backtest: the battery as a record, because the page needs the hit indices a printf cannot hand back"
```

---

### Task 6: `Validation_report`, part 2 — the crisis battery, the burst, and a mode that works from any directory

**Files:**
- Modify: `lib/validation_report.ml` (append `burst_span`, `worst_burst`; replace `row`; append `crisis`)
- Modify: `test/test_validation_report.ml` (five cases and their suite entries)
- Modify: `test/test_crisis_data.ml` — the comment above `repo_root` (`:30–36`), `test_a_missing_cache_is_fatal_and_says_how_to_fix_it` (`:176–186`), `test_the_committed_cache_loads` (`:192–222`) and their two suite entries. `repo_root` and `crisis_dir` STAY: Phase 1's `test_the_embedded_windows_are_the_files_on_disk` compares the binary against the disk and needs the walk.
- Modify: `bin/main.ml` — delete `backtest_window`, `crisis_estimators`, the comment and `worst_burst`, `burst_span`, `crisis_row`; rewrite `run_backtest_crisis` (`:1257–1422` before the earlier tasks shifted the numbering; find them by name)
- Test: `test/test_validation_report.ml`, `test/test_crisis_data.ml`

**Interfaces:**
- Consumes: `Crisis_data.Window.{name, description, dates, sessions, symbols}` (`lib/crisis_data.ml:48–72`); `Crisis_data.portfolio_returns_of_book ~instruments ~positions ~marks window : float array option` (`:261`); `Crisis_data.load_all_embedded : unit -> Window.t list` (Phase 1, Task 8 — raises on a malformed embedded CSV, never returns `Error`); `Crisis_data.load ?dir ~name ()` whose missing-file message names the `.csv` path and `tools/fetch_crisis_data.py` (`:178–208`); `Synthetic_book.{book, instruments, confidence, return_window}` (Task 3); `Types.{Qty, Price, Symbol}`; Task 5's `Row`, `Window_facts`, `t`, `estimators`, `most_severe`, `alpha`.
- Produces: `Validation_report.burst_span = 21`; `Validation_report.worst_burst : span:int -> bool array -> int * int` — `(count, start)`, the most hits any `span` consecutive observations held and the forecast-space index where that run begins, `(0, 0)` on an empty array; `Validation_report.row : ?burst_span:int -> label:string -> returns:float array -> estimator:Var_backtest.Estimator.t -> Row.t`; `Validation_report.crisis : Crisis_data.Window.t list -> t` with `windows` filled, `series = []`, and every row's `burst = Some _`. Phase 5's `Reports.compute` calls `crisis (Crisis_data.load_all_embedded ())` and reads `Row.burst` as `(count, start)`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_validation_report.ml`, above `let suite =`:

```ocaml
module Crisis_data = Ohcamel.Crisis_data

let crisis = lazy (Validation_report.crisis (Crisis_data.load_all_embedded ()))

let test_the_nine_crisis_rows_are_the_readmes () =
  let t = Lazy.force crisis in
  Alcotest.(check (list (pair string string)))
    "nine rows, window-major, in the README's order"
    [
      ("gfc", "historical"); ("gfc", "parametric"); ("gfc", "ewma(0.94)");
      ("covid", "historical"); ("covid", "parametric"); ("covid", "ewma(0.94)");
      ("rates-2022", "historical"); ("rates-2022", "parametric"); ("rates-2022", "ewma(0.94)");
    ]
    (labels t.Validation_report.rows);
  List.iter t.Validation_report.rows ~f:(fun row ->
      check_row_against_quoted ~table:"crisis" ~key:"window" row;
      let r = row.Validation_report.Row.report in
      let estimator = Var_backtest.Estimator.to_string (Var_backtest.estimator r) in
      let q = quoted_row "crisis" ~key:"window" ~value:row.Validation_report.Row.label ~estimator in
      Alcotest.(check int)
        (Printf.sprintf "%s/%s: burst" row.Validation_report.Row.label estimator)
        (U.to_int (U.member "burst" q))
        (Option.value_map row.Validation_report.Row.burst ~default:(-1) ~f:fst));
  Alcotest.(check int) "no synthetic series on the crisis report" 0
    (List.length t.Validation_report.series)

(* Read off the quoted table: every crisis verdict is "ok". *)
let test_crisis_rejects_nothing_so_there_is_no_most_severe () =
  let t = Lazy.force crisis in
  Alcotest.(check int) "none rejected" 0 t.Validation_report.rejected;
  Alcotest.(check bool) "so no most-severe row" true
    (Option.is_none t.Validation_report.most_severe)

(* Hand-traced. Span 3 over F T T F F T T T F:

     i   hit   +hit   -hits.(i-3)   running   worst (end)
     0   F                            0        0
     1   T     1                      1        1 (1)
     2   T     1                      2        2 (2)
     3   F           hits.(0)=F       2
     4   F           hits.(1)=T  -1   1
     5   T     1     hits.(2)=T  -1   1
     6   T     1     hits.(3)=F       2
     7   T     1     hits.(4)=F       3        3 (7)
     8   F           hits.(5)=T  -1   2

   worst 3, first attained at i = 7, so the run starts at 7 - 3 + 1 = 5.
   The count is exactly what bin/main.ml's worst_burst returned (the README's
   burst column); the start is new. *)
let test_worst_burst_on_hand_built_indicators () =
  let t = true and f = false in
  Alcotest.(check (pair int int)) "F T T F F T T T F, span 3" (3, 5)
    (Validation_report.worst_burst ~span:3 [| f; t; t; f; f; t; t; t; f |]);
  (* Two hits further apart than the span never share a window: the count is 1
     and the run is the first hit's own window, floored at index 0. *)
  Alcotest.(check (pair int int)) "T F F F T, span 3" (1, 0)
    (Validation_report.worst_burst ~span:3 [| t; f; f; f; t |]);
  (* A span wider than the series holds everything. *)
  Alcotest.(check (pair int int)) "five hits, span 21" (5, 0)
    (Validation_report.worst_burst ~span:21 [| t; t; t; t; t |]);
  Alcotest.(check (pair int int)) "no hits" (0, 0)
    (Validation_report.worst_burst ~span:21 [| f; f; f |]);
  Alcotest.(check (pair int int)) "empty" (0, 0) (Validation_report.worst_burst ~span:21 [||])

(* The window facts come from the CSV headers: "631 sessions common to all six
   names" (gfc), 400 (covid), 401 (rates-2022). Returns are one per session
   gap, so 630 / 399 / 400 of them, and a 60-day rolling window leaves
   570 / 339 / 340 forecasts -- the n column of the README's crisis table. *)
let test_window_facts () =
  let t = Lazy.force crisis in
  Alcotest.(check (list (pair string (pair int int))))
    "sessions and forecasts per window"
    [ ("gfc", (631, 570)); ("covid", (400, 339)); ("rates-2022", (401, 340)) ]
    (List.map t.Validation_report.windows ~f:(fun w ->
         Validation_report.Window_facts.(w.name, (w.sessions, w.forecasts))));
  List.iter t.Validation_report.windows ~f:(fun w ->
      let open Validation_report.Window_facts in
      Alcotest.(check (list string)) (w.name ^ ": the six names")
        [ "AAPL"; "CVX"; "JPM"; "MSFT"; "NVDA"; "XOM" ] w.symbols;
      Alcotest.(check bool) (w.name ^ ": worst day is a loss") true (Float.( < ) w.worst_day 0.0);
      Alcotest.(check bool) (w.name ^ ": best day is a gain") true (Float.( > ) w.best_day 0.0);
      Alcotest.(check int) (w.name ^ ": one date per forecast") w.forecasts (Array.length w.dates);
      Alcotest.(check int) (w.name ^ ": one realised return per forecast") w.forecasts
        (Array.length w.realised))

(* DATE ALIGNMENT, derived by hand. returns.(k) is close.(k+1) / close.(k) - 1:
   the return REALISED on dates.(k+1). Var_backtest.rolling's observation j
   forecasts from returns.(j .. j+59) and scores returns.(60 + j), which is the
   return realised on dates.(61 + j). So forecast j belongs to session 61 + j,
   the first forecast to dates.(61), and the last to dates.(sessions - 1). *)
let test_forecast_j_is_session_61_plus_j () =
  let t = Lazy.force crisis in
  let gfc = List.find_exn (Crisis_data.load_all_embedded ()) ~f:(fun w ->
      String.equal (Crisis_data.Window.name w) "gfc") in
  let facts = List.find_exn t.Validation_report.windows ~f:(fun w ->
      String.equal w.Validation_report.Window_facts.name "gfc") in
  let sessions = Crisis_data.Window.dates gfc in
  let open Validation_report.Window_facts in
  Alcotest.(check string) "first session" sessions.(0) facts.first;
  Alcotest.(check string) "last session" sessions.(630) facts.last;
  Alcotest.(check string) "forecast 0 is session 61" sessions.(61) facts.dates.(0);
  Alcotest.(check string) "forecast 569 is session 630" sessions.(630) facts.dates.(569);
  (* And every gfc row's realised series IS the window's, so a chart can draw
     the three estimators' VaR over one realised line. *)
  List.iter t.Validation_report.rows ~f:(fun row ->
      if String.equal row.Validation_report.Row.label "gfc" then
        Alcotest.(check (array (float 0.0))) "row realised = window realised"
          facts.realised row.Validation_report.Row.realised_series)
```

And add to the suite list, after the `"the generator reads one stream, jumps first"` entry:

```ocaml
      Alcotest.test_case "THE NINE CRISIS ROWS ARE THE README'S" `Quick
        test_the_nine_crisis_rows_are_the_readmes;
      Alcotest.test_case "crisis rejects nothing, so there is no most-severe" `Quick
        test_crisis_rejects_nothing_so_there_is_no_most_severe;
      Alcotest.test_case "worst_burst on hand-built indicators" `Quick
        test_worst_burst_on_hand_built_indicators;
      Alcotest.test_case "window facts" `Quick test_window_facts;
      Alcotest.test_case "forecast j is session 61 + j" `Quick
        test_forecast_j_is_session_61_plus_j;
```

Then `test/test_crisis_data.ml`. Replace the comment above `repo_root` (`:30–36`), which reads:

```
(* dune runs tests from inside _build, so the repository-relative cache path the
   binary uses does not resolve here. Walk up to the directory holding
   dune-project and point the loader at its docs/crisis.

   Worth doing rather than skipping these two tests: the committed CSVs are part
   of a published result, and "the cache is present, has six columns and enough
   sessions to be worth scoring" is exactly the assertion that fails when
   somebody deletes a window or truncates a file. *)
```

with:

```
(* dune runs tests from inside _build, so the repository-relative cache path
   [load] uses does not resolve here. Walk up to the directory holding
   dune-project and point the loader at its docs/crisis.

   One test still needs this: the one that compares the windows compiled into
   the binary against the files on disk, which cannot be done without the disk.
   The shape checks used to walk too; they now read the embedded windows, which
   is what the CLI and the served process read, and need no directory at all. *)
```

Replace `test_a_missing_cache_is_fatal_and_says_how_to_fix_it`, which reads:

```ocaml
let test_a_missing_cache_is_fatal_and_says_how_to_fix_it () =
  match Crisis_data.load ~dir:(crisis_dir ()) ~name:"no-such-window-exists" () with
```

with a version that needs no repository -- `load` tests the file's existence before anything else, so a directory that does not exist produces exactly the missing-file message:

```ocaml
let test_a_missing_cache_is_fatal_and_says_how_to_fix_it () =
  match
    Crisis_data.load ~dir:"/nonexistent/ohcamel-crisis-cache" ~name:"no-such-window-exists" ()
  with
```

(the `| Ok _ ->` and `| Error e ->` arms are unchanged). Replace `test_the_committed_cache_loads` -- the whole function from its comment `(* The committed cache is part of the repository's published result` to the final `(Float.( < ) (Float.abs r) 0.60))))` -- with:

```ocaml
(* The three windows are part of the repository's published result, so their
   presence and shape are asserted rather than assumed. This is the test that
   fails if somebody deletes a window, renames a column, or commits a file with
   two rows in it -- read through the binary, because that is where the CLI and
   the served process read them from; the embedded-equals-disk test below is
   what makes reading here equivalent to reading the files. *)
let test_the_embedded_windows_are_the_right_shape () =
  let windows = Crisis_data.load_all_embedded () in
  Alcotest.(check (list string))
    "the three windows, in window_names order" Crisis_data.window_names
    (List.map windows ~f:Crisis_data.Window.name);
  List.iter windows ~f:(fun w ->
      let name = Crisis_data.Window.name w in
      Alcotest.(check (list string))
        (name ^ ": the six names of the synthetic book")
        [ "AAPL"; "CVX"; "JPM"; "MSFT"; "NVDA"; "XOM" ]
        (List.map (Crisis_data.Window.symbols w) ~f:Symbol.to_string);
      (* Enough sessions for the 60-day window to leave a testable number of
         forecasts behind. A coverage test on a handful of observations has no
         power to reject anything, so a short window is a silent no-op. *)
      Alcotest.(check bool)
        (Printf.sprintf "%s: at least 300 sessions (has %d)" name
           (Crisis_data.Window.sessions w))
        true
        (Crisis_data.Window.sessions w >= 300);
      let returns = Crisis_data.returns w in
      Map.iteri returns ~f:(fun ~key:symbol ~data ->
          Alcotest.(check int)
            (Printf.sprintf "%s/%s: one return per session gap" name
               (Symbol.to_string symbol))
            (Crisis_data.Window.sessions w - 1)
            (Array.length data);
          (* No adjusted daily equity return should be beyond +/-60%. This is
             the split check: an unadjusted 2-for-1 shows up as exactly -50% and
             would become the entire tail of a 60-day window at 95%. *)
          Array.iter data ~f:(fun r ->
              Alcotest.(check bool)
                (Printf.sprintf "%s/%s: %.4f is a plausible adjusted daily return" name
                   (Symbol.to_string symbol) r)
                true
                (Float.( < ) (Float.abs r) 0.60))))
```

and in `suite`, replace the entry

```ocaml
      Alcotest.test_case "the committed cache loads and is the right shape" `Quick
        test_the_committed_cache_loads;
```

with

```ocaml
      Alcotest.test_case "the embedded windows are the right shape" `Quick
        test_the_embedded_windows_are_the_right_shape;
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound value Validation_report.crisis` (the first of `crisis`, `worst_burst` the compiler reaches in `test/test_validation_report.ml`).

- [ ] **Step 3: Minimal implementation**

In `lib/validation_report.ml`, first replace `row` (from its `(* [rolling] then [run]` comment through the closing `}`) with the version that can carry a burst:

```ocaml
(* One month of sessions. Short enough that a burst inside it is a burst rather
   than a season, long enough that a single bad week does not fill it. *)
let burst_span = 21

(* The most exceptions any [span] consecutive observations contained, and where
   that run starts.

   This is a descriptive statistic over Var_backtest's own exported exceedance
   array, not a new test and not a second implementation of anything -- but it
   is here because the real data exposed a gap the synthetic series never did.

   Christoffersen's independence statistic is a FIRST-ORDER MARKOV test: it
   compares P(exception | exception yesterday) against P(exception | none
   yesterday). That catches exceptions arriving back to back and is blind to
   exceptions arriving in a burst that is not literally consecutive. On the GFC
   window this book takes five exceptions between 15 September and 7 October
   2008 -- seventeen sessions, against 0.85 expected at 95% -- and because only
   one pair anywhere in that series falls on adjacent days, the independence
   test returns p = 0.92. It is not wrong. It is answering a narrower question
   than the one a reader assumes it answered.

   The duration-based test (Christoffersen-Pelletier) IS implemented --
   Var_backtest.duration_independence, the duration p column -- and it sees a
   cluster at any spacing, but it is a GLOBAL fit: one local burst inside a
   long calm series barely moves it. So the burst count sits in the table next
   to both p-values as the only one of the three that sees a LOCAL cluster.
   Not a hypothesis test, and labelled as such wherever it is shown.

   The start index is for the page, which shades the run; the CLI prints the
   count alone, exactly as it did. The count is attained at the first index
   where the running total reaches its maximum, and the run is the [span]
   observations ending there, floored at 0. *)
let worst_burst ~(span : int) (hits : bool array) : int * int =
  let n = Array.length hits in
  if n = 0 then (0, 0)
  else begin
    let worst = ref 0 in
    let worst_end = ref 0 in
    let running = ref 0 in
    Array.iteri hits ~f:(fun i hit ->
        if hit then incr running;
        if i >= span && hits.(i - span) then decr running;
        if !running > !worst then begin
          worst := !running;
          worst_end := i
        end);
    (!worst, Int.max 0 (!worst_end - span + 1))
  end

(* [rolling] then [run], rather than [of_returns], so the observations are in
   hand for the hit indices, the two series and the burst. var_backtest.ml
   exports both steps and composes them the same way, so this is the same path
   and not a second one. *)
let row ?burst_span ~(label : string) ~(returns : float array)
    ~(estimator : Var_backtest.Estimator.t) : Row.t =
  let observations =
    Var_backtest.rolling ~returns ~window:Synthetic_book.return_window
      ~confidence:Synthetic_book.confidence ~estimator
  in
  let report =
    Var_backtest.run ~observations ~estimator ~confidence:Synthetic_book.confidence
  in
  let exceedances = Var_backtest.exceedances observations in
  let hits =
    Array.foldi exceedances ~init:[] ~f:(fun i acc hit -> if hit then i :: acc else acc)
    |> List.rev
  in
  {
    Row.label;
    report;
    hits;
    burst = Option.map burst_span ~f:(fun span -> worst_burst ~span exceedances);
    var_series = Array.of_list_map observations ~f:Var_backtest.Observation.var;
    realised_series = Array.of_list_map observations ~f:Var_backtest.Observation.realised;
  }
```

Then append at the end of the file:

```ocaml
(* The same battery, real data.

   Everything in [synthetic] is validated against series whose regime the
   author chose. That is the right way to BUILD a coverage battery -- it is the
   only setting where you know in advance which tests ought to reject -- and it
   is not evidence that the model survives a real tail. This changes exactly one
   thing: the data. Same window, same confidence, same three estimators, same
   Var_backtest.rolling. The method has to be visibly identical or the
   comparison says nothing.

   The book's return series comes out of the ENGINE, through
   Crisis_data.portfolio_returns_of_book, at today's book held at constant
   weights -- graph.ml's own approximation, and the question a limit asks.

   A window too short to form a series is a raise, not a skipped row. The
   windows are compiled into the binary and are hundreds of sessions long; the
   only way to get here is a broken build, and a report that quietly scored two
   windows under a heading promising three is the outcome this module exists
   to make impossible.

   DATE ALIGNMENT. returns.(k) is the return realised on dates.(k + 1), and
   Var_backtest.rolling's observation j scores returns.(window + j), so
   forecast j belongs to session window + 1 + j -- dates.(61 + j) at this
   engine's window. [Window_facts.dates] and [Window_facts.realised] are laid
   out in that forecast space, one entry per observation, so a chart draws
   every row's [var_series] over them without re-deriving the offset. *)
let crisis (windows : Crisis_data.Window.t list) : t =
  let window = Synthetic_book.return_window in
  let scored =
    List.map windows ~f:(fun w ->
        match
          Crisis_data.portfolio_returns_of_book ~instruments:Synthetic_book.instruments
            ~positions:(List.map Synthetic_book.book ~f:(fun (s, _, _, q) -> (s, Qty.of_float q)))
            ~marks:(List.map Synthetic_book.book ~f:(fun (s, _, p, _) -> (s, Price.of_float p)))
            w
        with
        | Some returns when Array.length returns > window -> (w, returns)
        | Some returns ->
            invalid_argf "validation_report: window %s has %d returns, fewer than the %d a \
                          rolling forecast needs"
              (Crisis_data.Window.name w) (Array.length returns) window ()
        | None ->
            invalid_argf "validation_report: window %s is too short to form a return series"
              (Crisis_data.Window.name w) ())
  in
  let rows =
    List.concat_map scored ~f:(fun (w, returns) ->
        List.map estimators ~f:(fun estimator ->
            row ~burst_span ~label:(Crisis_data.Window.name w) ~returns ~estimator))
  in
  let facts =
    List.map scored ~f:(fun (w, returns) ->
        let dates = Crisis_data.Window.dates w in
        let sessions = Crisis_data.Window.sessions w in
        let forecasts = Array.length returns - window in
        {
          Window_facts.name = Crisis_data.Window.name w;
          description = Crisis_data.Window.description w;
          first = dates.(0);
          last = dates.(sessions - 1);
          sessions;
          forecasts;
          symbols = List.map (Crisis_data.Window.symbols w) ~f:Symbol.to_string;
          (* Folded from 0.0, as the CLI folded: the worst day is at most flat
             and the best at least flat, which is a fact about the fold and is
             kept because the printed line is a published figure. *)
          worst_day = Array.fold returns ~init:0.0 ~f:Float.min;
          best_day = Array.fold returns ~init:0.0 ~f:Float.max;
          dates = Array.init forecasts ~f:(fun j -> dates.(window + 1 + j));
          realised = Array.sub returns ~pos:window ~len:forecasts;
        })
  in
  {
    rows;
    rejected = List.count rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report);
    most_severe = most_severe rows;
    series = [];
    windows = facts;
    confidence = Synthetic_book.confidence;
    window;
    alpha;
    ewma_lambda = Vol_estimators.Ewma.default_lambda;
  }
```

Then `bin/main.ml`. Delete `let backtest_window = return_window`. Delete `crisis_estimators` and its comment, `worst_burst` and its whole comment, `burst_span` and its comment, and `crisis_row` -- from `(* Everything above this line is validated against series` through the end of `crisis_row`. The comment being deleted ends with these stale lines (`bin/main.ml:1275–1279`):

```
   So the burst count sits in the table next to the p-value it qualifies. A
   proper fix is a duration-based test (Christoffersen-Pelletier), which models
   the time BETWEEN exceptions rather than the day after each one; that is not
   implemented here and the README says so rather than leaving the reader to
   assume the column is a hypothesis test. *)
```

That was true when written and has not been since `Var_backtest.duration_independence` landed -- the `duration p` column two places to the left of `burst` IS Christoffersen-Pelletier. The corrected paragraph is in `worst_burst`'s comment in the library above; nothing of the old one remains in `bin/main.ml`. Put this in the deleted region's place:

```ocaml
(* The crisis battery is lib/validation_report.ml's [crisis]; this is its
   printer. The windows come from the binary (Crisis_data.load_all_embedded),
   so the mode runs from any working directory and inside the image, where
   there is no docs/. The files on disk are still the source -- lib/dune
   embeds them at build time and test_crisis_data.ml asserts the two agree. *)
let crisis_row (row : Validation_report.Row.t) =
  let r = row.Validation_report.Row.report in
  printf "  %-12s %-12s %6d %8d %9.1f %9.4f %9.4f %9.4f  %14s %6d  %-7s %s\n"
    row.Validation_report.Row.label
    (Var_backtest.Estimator.to_string (Var_backtest.estimator r))
    (Var_backtest.observations r) (Var_backtest.exceptions r)
    (Var_backtest.expected_exceptions r)
    (Var_backtest.kupiec_p r)
    (Var_backtest.independence_p r)
    (Var_backtest.conditional_coverage_p r)
    (duration_cell r)
    (Option.value_map row.Validation_report.Row.burst ~default:0 ~f:fst)
    (Var_backtest.Zone.to_string (Var_backtest.zone r))
    (if Var_backtest.rejected r then "REJECTED" else "ok")
```

Replace `run_backtest_crisis` entirely -- the `match Crisis_data.load_all () with | Error e -> ... | Ok windows -> ...` shape goes, because `load_all_embedded` cannot fail; the `Error` arm's "loud, named, NOT a fallback" argument now lives in `Crisis_data.load_all_embedded`'s comment (it raises on a broken build):

```ocaml
let run_backtest_crisis () =
  let report = Validation_report.crisis (Crisis_data.load_all_embedded ()) in
  let burst_span = Validation_report.burst_span in
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  CRISIS BACKTEST (real market data, cached, no credentials, no network)\n\n";
  printf "  book            the same six names as `make run`: long TECH and\n";
  printf "                  FINANCIALS, short ENERGY, %s gross.\n"
    (money (Notional.of_float 316_000.0));
  printf
    "  weights         held constant across each window, which is graph.ml's own\n\
    \                  approximation. The question is what TODAY's book would have\n\
    \                  done through that history, not what the book of the day did.\n";
  printf "  confidence      %.0f%%\n" (report.Validation_report.confidence *. 100.0);
  printf "  window          %d observations, rolling\n" report.Validation_report.window;
  printf
    "  data            docs/crisis/*.csv -- adjusted daily closes, committed, so\n\
    \                  this table reproduces with no API key and no network.\n\n";
  printf "%s\n  THE WINDOWS\n%s\n\n" (rule 106) (rule 106);
  List.iter report.Validation_report.windows ~f:(fun w ->
      let open Validation_report.Window_facts in
      printf "  %-12s %d sessions, %d forecasts. Book's worst day %s, best %s.\n" w.name
        w.sessions w.forecasts (pct w.worst_day) (pct w.best_day);
      printf "  %-12s %s\n\n" "" w.description);
  printf "%s\n  COVERAGE AND INDEPENDENCE\n%s\n\n" (rule 106) (rule 106);
  printf "  %-12s %-12s %6s %8s %9s %9s %9s %9s  %14s %6s  %-7s %s\n" "window" "estimator"
    "n" "excepts" "expected" "Kupiec p" "indep p" "joint p" "duration p" "burst" "Basel"
    "verdict";
  printf "  %s\n" (rule 126);
  List.iter report.Validation_report.rows ~f:crisis_row;
  printf "  %s\n" (rule 126);
  printf
    "\n\
    \  Three instruments, three blind spots, which is why all three are here.\n\
    \  indep p     first-order Markov: only sees breaches on ADJACENT days.\n\
    \  duration p  Weibull on the waiting times: sees a cluster at any spacing,\n\
    \              but it is a GLOBAL fit -- one local burst inside a long calm\n\
    \              series barely moves it.\n\
    \  burst       the most exceptions any %d consecutive sessions held, against\n\
    \              about %.1f expected under independence. Not a hypothesis test\n\
    \              and labelled as such -- it is the only one of the three that\n\
    \              sees a LOCAL cluster.\n"
    burst_span
    (float_of_int burst_span *. (1.0 -. report.Validation_report.confidence));
  printf "\n  %d of %d configurations rejected at 5%%.\n" report.Validation_report.rejected
    (List.length report.Validation_report.rows);
  (match report.Validation_report.most_severe with
  | None -> ()
  | Some row ->
      printf "\n%s\n  IN FULL: the most severe rejection (%s)\n%s\n\n" (rule 106)
        row.Validation_report.Row.label (rule 106);
      printf "%s\n" (Var_backtest.to_string row.Validation_report.Row.report));
  printf
    "\n\
    \  Read the two sharp windows against the slow one. An equal-weighted\n\
    \  volatility window fails in a specific way when volatility JUMPS -- it\n\
    \  under-forecasts for as long as the window takes to absorb the change --\n\
    \  and 2022 is the control: a large drawdown with no single day over 10%%.\n\
    \  A model that fails there is failing for a different reason than one that\n\
    \  fails in 2008, and the point of running all three is to be able to tell.\n\n"
```

The "too short to form a return series" line the old printer had for a `None` window is gone with the `option`: the library raises instead, and no embedded window is short, so the gate cannot see the difference.

- [ ] **Step 4: Run the tests and see them pass**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: `validation_report` reports 10 passing cases, `crisis_data` still reports 7 (Phase 1's count: the two replaced cases replaced one-for-one), and the gate prints six `GATE ok` lines. Then the claim this task makes, checked from a directory that has no `docs/`:

```bash
cd /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad && /Users/ajaiupadhyaya/Documents/OhCamel/_build/default/bin/main.exe backtest-crisis | head -3
```

Expected: the banner `OhCamel -- reactive risk and limits engine` / `CRISIS BACKTEST (...)`, not `ohcamel: no cached crisis data at docs/crisis/gfc.csv.`

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/validation_report.ml test/test_validation_report.ml test/test_crisis_data.ml bin/main.ml
git commit -m "crisis: the battery reads the windows from the binary, because the image has no docs/ and a page cannot chdir"
```

---

### Task 7: `Options_walk` — the delta-hedge walk and the calendar spread as a record

**Files:**
- Create: `lib/options_walk.ml`
- Create: `test/test_options_walk.ml`
- Modify: `bin/main.ml` — the `Options: Greeks-aware exposure` section: `synthetic_implied_vol` and its comments, `options_spot` … `options_book`, `calendar_near_days` … `calendar_book`, `run_calendar_spread`, `run_options` (`:975–1237` before the earlier tasks shifted the numbering; find them by name)
- Modify: `test/test_ohcamel.ml` (register the suite)
- Test: `test/test_options_walk.ml`

**Interfaces:**
- Consumes: `Graph.create ?on_compute ?starting_cash ?options ?rate ~instruments ~limits ~confidence ~return_window ()` (as `bin/main.ml`'s `run_options` calls it), `Graph.{set_price, set_qty, set_contracts, set_implied_vol, advance_valuation_days, stabilize, snapshot, portfolio_vega, destroy}` (`lib/graph.ml:1538, 1544, 1563, 1734, 1746`), `Graph.Snapshot.{exposure_by_instrument, portfolio_gamma, portfolio_vega, vega_by_bucket, breaches}`; `Options.Position.create ?multiplier ~underlying ~id ~strike ~right ~expiry_in_days ()` (`lib/options.ml:378`), `Options.default_multiplier` (= 100.0, `:349`), `Options.{Strike, Implied_vol, Contracts}.of_float`, `Options.Right.{Call, to_string}` (`:182–185`, `Call -> "C"`), `Options.Tenor_bucket.{ordered, to_string}` (`:144–180`; the six names are `<=1w`, `1w-1m`, `1-3m`, `3-6m`, `6-12m`, `>1y`); `Limits.utilisation : Breach.t -> float`, `Limits.to_string : Breach.t -> string` (`lib/limits.ml:217, 188`); `Types.Breach.{t, breached, limit}` (`lib/types.ml:295–320`); `Synthetic_book.limit` (Task 3); `Exn.protect` (`_opam/lib/base/exn.mli:61`). The graph's `vega_by_bucket` map holds only OCCUPIED buckets (`lib/graph.ml:807–822` folds the legs into an empty map; `test/test_options_graph.ml:486` asserts two).
- Produces (exactly what Phase 5's `Reports.json_of_options` consumes): `Options_walk.Surface.t = { formula : string; floor : float; at_setup : float }`; `Options_walk.Setup.t = { underlying : string; id : string; strike : float; right : string; expiry_days : float; contracts : float; multiplier : float; spot : float; rate : float; implied_vol : float }`; `Options_walk.State.t = { label : string; delta_equivalent : float; gamma : float; vega : float }` with `vega` in the ENGINE's unit (dollars per 1.00 of vol; the `/100` is the printer's); `Options_walk.Leg.t = { id : string; days : float; contracts : float }`; `Options_walk.Calendar.t = { far : Leg.t; near : Leg.t; portfolio_vega : float; portfolio_gamma : float; buckets : (string * float) list }` — all six tenor buckets in `Tenor_bucket.ordered` order, `0.0` where the graph has no entry; `Options_walk.t = { surface : Surface.t; setup : Setup.t; states : State.t list; hedge_shares : float; breaches : Types.Breach.t list; clock_advance_days : float; calendar : Calendar.t }` with `states` the four in CLI order — `options only`, `+ the hedge`, `today`, `+20 days` — and `breaches` sorted by utilisation descending, as the CLI prints them; `Options_walk.synthetic_implied_vol : spot:float -> strike:float -> float`; `Options_walk.run : unit -> t`.

- [ ] **Step 1: Write the failing test**

Create `test/test_options_walk.ml`:

```ocaml
(* Reproducibility pins for options_walk.ml, plus one hand derivation.

   THESE ARE PINS. The hedge count, the three "+20 days" cells, the 141.5%
   vega utilisation, the calendar's gamma and its two bucket vegas are the
   numbers README.md prints for `make options`, and what is asserted is that
   the walk, moved out of bin/main.ml, still produces them. Why a short 950
   call at 26.9% vol has THAT delta is options.ml's business, tested there
   against Hull. The hand derivation is the implied vol itself, which is a
   three-term formula and can be checked on paper. *)

open Core
module Options_walk = Ohcamel.Options_walk
module Options = Ohcamel.Options
module Limits = Ohcamel.Limits
open Ohcamel.Types

let walk = lazy (Options_walk.run ())
let f0 x = Printf.sprintf "%.0f" x
let f1 x = Printf.sprintf "%.1f" x

(* m = 950 / 900 - 1 = 0.0555...; m^2 = 0.0030864
   0.28 + 0.9 * 0.0030864 - 0.25 * 0.0555... = 0.28 + 0.0027778 - 0.0138889
                                              = 0.2688889, i.e. 26.9% *)
let test_the_synthetic_surface_at_the_setup () =
  let iv = Options_walk.synthetic_implied_vol ~spot:900.0 ~strike:950.0 in
  Alcotest.(check (float 1e-6)) "26.89% by hand" 0.2688889 iv;
  let w = Lazy.force walk in
  Alcotest.(check (float 0.0)) "the setup carries the same number" iv
    w.Options_walk.setup.Options_walk.Setup.implied_vol;
  Alcotest.(check (float 0.0)) "and so does the surface" iv
    w.Options_walk.surface.Options_walk.Surface.at_setup;
  Alcotest.(check (float 0.0)) "the floor is 5%" 0.05 w.Options_walk.surface.Options_walk.Surface.floor;
  (* The floor never binds. 0.9 m^2 - 0.25 m + 0.28 is a parabola with its
     minimum at m = 0.25 / 1.8 = 0.13889, where it is
     0.28 - 0.25^2 / (4 * 0.9) = 0.28 - 0.017361 = 0.262639 -- above 0.05
     everywhere. The floor is a guard against a future edit to the
     coefficients, not a feature of this surface, and the page says so. *)
  Alcotest.(check (float 1e-6)) "the surface's minimum, by hand" 0.262639
    (Options_walk.synthetic_implied_vol ~spot:900.0 ~strike:(900.0 *. (1.0 +. (0.25 /. 1.8))))

let test_the_setup_is_the_readmes_book () =
  let s = (Lazy.force walk).Options_walk.setup in
  let open Options_walk.Setup in
  Alcotest.(check string) "NVDA" "NVDA" s.underlying;
  Alcotest.(check string) "the contract id" "NVDA-950C-30d" s.id;
  Alcotest.(check (float 0.0)) "strike" 950.0 s.strike;
  Alcotest.(check string) "a call" "C" s.right;
  Alcotest.(check (float 0.0)) "30 days" 30.0 s.expiry_days;
  Alcotest.(check (float 0.0)) "short fifty" (-50.0) s.contracts;
  Alcotest.(check (float 0.0)) "the listed multiplier" 100.0 s.multiplier;
  Alcotest.(check (float 0.0)) "spot" 900.0 s.spot;
  Alcotest.(check (float 0.0)) "rate" 0.04 s.rate

let test_the_hedge_is_1338_shares_and_flattens_delta () =
  let w = Lazy.force walk in
  Alcotest.(check string) "1,338 shares" "1338" (f0 w.Options_walk.hedge_shares);
  Alcotest.(check (list string)) "four states, in the CLI's order"
    [ "options only"; "+ the hedge"; "today"; "+20 days" ]
    (List.map w.Options_walk.states ~f:(fun s -> s.Options_walk.State.label));
  match w.Options_walk.states with
  | [ before; after; today; _ ] ->
      let open Options_walk.State in
      Alcotest.(check bool) "short calls are short delta" true
        (Float.( < ) before.delta_equivalent 0.0);
      (* shares = -delta / spot, then exposure = shares * spot + delta. In IEEE
         arithmetic (a / b) * b is not always a, so "exactly zero" is asserted
         to a micro-dollar on a six-figure exposure rather than bit-for-bit. *)
      Alcotest.(check (float 1e-6)) "delta-equivalent is zero after the hedge" 0.0
        after.delta_equivalent;
      Alcotest.(check (float 0.0)) "gamma did not move" before.gamma after.gamma;
      Alcotest.(check (float 0.0)) "vega did not move" before.vega after.vega;
      Alcotest.(check (float 0.0)) "today IS the hedged state" after.delta_equivalent
        today.delta_equivalent
  | _ -> Alcotest.fail "four states"

let test_nvda_vega_is_breached_at_141_5_percent () =
  let w = Lazy.force walk in
  let named name =
    List.find_exn w.Options_walk.breaches ~f:(fun b ->
        String.equal (Breach.limit b).Limit.name name)
  in
  Alcotest.(check bool) "nvda-vega breached" true (Breach.breached (named "nvda-vega"));
  Alcotest.(check string) "141.5%" "141.5" (f1 (Limits.utilisation (named "nvda-vega") *. 100.0));
  Alcotest.(check bool) "the notional cap is comfortably clear" false
    (Breach.breached (named "nvda-notional"));
  Alcotest.(check bool) "sorted by utilisation, descending" true
    (List.is_sorted w.Options_walk.breaches ~compare:(fun a b ->
         Float.descending (Limits.utilisation a) (Limits.utilisation b)))

let test_twenty_days_later () =
  let w = Lazy.force walk in
  Alcotest.(check (float 0.0)) "the clock advanced twenty days" 20.0
    w.Options_walk.clock_advance_days;
  let later = List.last_exn w.Options_walk.states in
  let open Options_walk.State in
  Alcotest.(check string) "$657,690 over-hedged" "657690" (f0 later.delta_equivalent);
  Alcotest.(check string) "gamma -25.2" "-25.2" (f1 later.gamma);
  Alcotest.(check string) "vega $-1,502 per point" "-1502" (f0 (later.vega /. 100.0))

let test_the_calendar_spread () =
  let c = (Lazy.force walk).Options_walk.calendar in
  let open Options_walk.Calendar in
  Alcotest.(check (pair string (pair (float 0.0) (float 0.0))))
    "long 50 far calls, 180 days out" ("NVDA-950C-far", (180.0, 50.0))
    Options_walk.Leg.(c.far.id, (c.far.days, c.far.contracts));
  Alcotest.(check string) "near leg, 25 days out" "NVDA-950C-near" c.near.Options_walk.Leg.id;
  Alcotest.(check (float 0.0)) "25 days" 25.0 c.near.Options_walk.Leg.days;
  Alcotest.(check bool) "the near leg is short" true
    (Float.( < ) c.near.Options_walk.Leg.contracts 0.0);
  Alcotest.(check string) "gamma -72.5" "-72.5" (f1 c.portfolio_gamma);
  Alcotest.(check bool) "the parallel-shift vega is zero" true
    (Float.( < ) (Float.abs c.portfolio_vega) 1e-3);
  Alcotest.(check (list string)) "all six buckets, in tenor order"
    (List.map Options.Tenor_bucket.ordered ~f:Options.Tenor_bucket.to_string)
    (List.map c.buckets ~f:fst);
  let bucket name = List.Assoc.find_exn c.buckets ~equal:String.equal name in
  Alcotest.(check string) "1w-1m: $-12,559 per point" "-12559" (f0 (bucket "1w-1m" /. 100.0));
  Alcotest.(check string) "3-6m: $12,559 per point" "12559" (f0 (bucket "3-6m" /. 100.0));
  Alcotest.(check bool) "the buckets sum to the (zero) total" true
    (Float.( < ) (Float.abs (List.sum (module Float) c.buckets ~f:snd -. c.portfolio_vega)) 1e-3);
  Alcotest.(check int) "the other four are empty" 4
    (List.count c.buckets ~f:(fun (_, vega) -> Float.( = ) vega 0.0))

let suite =
  ( "options_walk",
    [
      Alcotest.test_case "the synthetic surface at the setup" `Quick
        test_the_synthetic_surface_at_the_setup;
      Alcotest.test_case "the setup is the README's book" `Quick
        test_the_setup_is_the_readmes_book;
      Alcotest.test_case "THE HEDGE IS 1,338 SHARES AND FLATTENS DELTA" `Quick
        test_the_hedge_is_1338_shares_and_flattens_delta;
      Alcotest.test_case "nvda-vega is breached at 141.5%" `Quick
        test_nvda_vega_is_breached_at_141_5_percent;
      Alcotest.test_case "twenty days later" `Quick test_twenty_days_later;
      Alcotest.test_case "the calendar spread" `Quick test_the_calendar_spread;
    ] )
```

Register it in `test/test_ohcamel.ml`, after `Test_validation_report.suite;`:

```ocaml
      Test_validation_report.suite;
      Test_options_walk.suite;
      Test_stress.suite;
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound module Ohcamel.Options_walk`.

- [ ] **Step 3: Minimal implementation**

Create `lib/options_walk.ml`. The constants, the surface and both walks are `bin/main.ml`'s, in the same order of `set_*` calls; what changes is that every value is returned rather than printed.

```ocaml
(* The options demonstration as a value: a delta hedge, what it removes and
   what it leaves, then the calendar spread that one vega number hides.

   WHY A RECORD. `make options` printed four snapshots, a limits table and a
   bucket table straight from the graph; the served page needs the same
   numbers, and a page cannot read a printf. So the two walks run here, into a
   record, and bin/main.ml prints it. Every number is the engine's -- the
   hedge ratio is the graph's own delta divided by spot, the vega utilisation
   is Limits.utilisation on the graph's own breach -- and nothing in this file
   prices anything.

   THE VOL SURFACE HERE IS INVENTED, and it is invented in a shape that at
   least resembles a real one rather than being flat. A smile plus a skew: vol
   rises as a contract moves away from the money (the quadratic term) and
   equity puts trade richer than equity calls (the linear one), which is the
   shape every equity index surface has had since 1987 and is the reason a
   flat surface would misprice the hedge this mode demonstrates. It is
   labelled synthetic everywhere it appears -- [Surface.formula] is the label
   the page prints -- and live mode does not use it: a plausible surface
   presented as a real one is exactly the "looks live and is showing made-up
   numbers" failure the credentials path is arranged against.

   UNITS. [State.vega] and every vega below are in the ENGINE's unit, dollars
   per 1.00 of annualised vol -- a hundred times the desk's "per vol point".
   The /100 is a display decision and it belongs to the printer, because this
   conversion is exactly the one that silently makes a limit a hundred times
   too loose; the record carries what the limit thresholds are written in.

   GRAPHS ARE DESTROYED IN Exn.protect. Incremental's state is shared by every
   Graph.t in the process, and the served host runs this at startup: a graph
   left alive after a raise would recompute on every stabilize of the served
   graph forever, and the symptom is the process-wide counter the smoke suite
   reads to decide the deploy is alive. *)

open Core
open Types

module Surface = struct
  type t = { formula : string; floor : float; at_setup : float }
end

module Setup = struct
  type t = {
    underlying : string;
    id : string;
    strike : float;
    right : string;
    expiry_days : float;
    contracts : float;
    multiplier : float;
    spot : float;
    rate : float;
    implied_vol : float;
  }
end

module State = struct
  type t = { label : string; delta_equivalent : float; gamma : float; vega : float }
end

module Leg = struct
  type t = { id : string; days : float; contracts : float }
end

module Calendar = struct
  type t = {
    far : Leg.t;
    near : Leg.t;
    portfolio_vega : float;
    portfolio_gamma : float;
    (* All six tenor slots, in Tenor_bucket.ordered order, 0.0 where the graph
       holds no leg. The graph's map holds only occupied buckets; the page
       draws the empty ones too, because the point of the figure is that a
       total of zero hides two opposite bars, and slots that vanish when
       unoccupied would hide the hiding. *)
    buckets : (string * float) list;
  }
end

type t = {
  surface : Surface.t;
  setup : Setup.t;
  (* Four, in the CLI's order: options only, + the hedge, today, +20 days. The
     third is the second read again after the limits table, as the CLI did. *)
  states : State.t list;
  hedge_shares : float;
  (* After the hedge, sorted by utilisation descending, as the CLI prints. *)
  breaches : Breach.t list;
  clock_advance_days : float;
  calendar : Calendar.t;
}

let floor = 0.05
let formula = "max(0.05, 0.28 + 0.9 m^2 - 0.25 m), m = strike / spot - 1"

let synthetic_implied_vol ~(spot : float) ~(strike : float) : float =
  let moneyness = (strike /. spot) -. 1.0 in
  Float.max floor (0.28 +. (0.9 *. moneyness *. moneyness) -. (0.25 *. moneyness))

let underlying = Symbol.of_string "NVDA"
let sector = Sector.of_string "TECH"
let spot = 900.0
let strike = 950.0
let expiry_days = 30.0
let contracts = -50.0
let rate = 0.04
let contract_id = "NVDA-950C-30d"
let clock_advance_days = 20.0

(* A vega cap that the unhedged book is already through, so the interesting
   line -- a limit that a delta hedge does NOT clear -- is visible rather than
   described. Stated in the engine's internal unit -- dollars per 1.00 of
   annualised vol -- which is a hundred times the desk's "per vol point".
   $300,000 here is a $3,000-a-point cap. *)
let vega_cap = 300_000.0
let gamma_cap = 400.0
let notional_cap = 1_000_000.0

let limits =
  [
    Synthetic_book.limit "nvda-notional" (Limit.Instrument underlying)
      (Limit.Gross_notional (Notional.of_float notional_cap));
    Synthetic_book.limit "nvda-vega" (Limit.Instrument underlying)
      (Limit.Greek_limit (Greek.Vega, Notional.of_float vega_cap));
    Synthetic_book.limit "nvda-gamma" (Limit.Instrument underlying)
      (Limit.Greek_limit (Greek.Gamma, Notional.of_float gamma_cap));
  ]

(* The case a single portfolio vega gets wrong. A calendar spread is long one
   expiry and short another on the same name. Its parallel-shift vega -- the
   sum across expiries -- nets to nearly nothing, because the two legs'
   sensitivities cancel. But they are sensitivities to DIFFERENT volatilities:
   the 25-day implied and the 180-day implied move together and not
   identically, so the position is a real bet on the term structure and the
   total says it is flat. *)
let calendar_near_days = 25.0
let calendar_far_days = 180.0
let calendar_far_contracts = 50.0
let far_id = "NVDA-950C-far"
let near_id = "NVDA-950C-near"

let position ~id ~expiry_in_days =
  Options.Position.create ~underlying ~id ~strike:(Options.Strike.of_float strike)
    ~right:Options.Right.Call ~expiry_in_days ()

let state ~label (graph : Graph.t) : State.t =
  let s = Graph.snapshot graph in
  {
    State.label;
    delta_equivalent =
      Notional.to_float (Map.find_exn (Graph.Snapshot.exposure_by_instrument s) underlying);
    gamma = Graph.Snapshot.portfolio_gamma s;
    vega = Graph.Snapshot.portfolio_vega s;
  }

(* The hedge walk: options only, hedged, the limits, then the clock. *)
let walk ~implied_vol : State.t list * float * Breach.t list =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = underlying; sector } ]
      ~limits
      ~options:[ position ~id:contract_id ~expiry_in_days:expiry_days ]
      ~rate ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph underlying (Price.of_float spot);
      Graph.set_contracts graph contract_id (Options.Contracts.of_float contracts);
      Graph.set_implied_vol graph contract_id (Options.Implied_vol.of_float implied_vol);
      Graph.stabilize graph;
      let options_only = state ~label:"options only" graph in
      (* Buy the shares the short calls are short. The count is the engine's
         own delta-equivalent exposure divided by the spot -- a number the risk
         engine produces, not a guess. *)
      let hedge_shares = -.options_only.State.delta_equivalent /. spot in
      Graph.set_qty graph underlying (Qty.of_float hedge_shares);
      Graph.stabilize graph;
      let hedged = state ~label:"+ the hedge" graph in
      let breaches =
        Graph.Snapshot.breaches (Graph.snapshot graph)
        |> List.sort ~compare:(fun a b ->
            Float.descending (Limits.utilisation a) (Limits.utilisation b))
      in
      let today = state ~label:"today" graph in
      Graph.advance_valuation_days graph clock_advance_days;
      Graph.stabilize graph;
      let later = state ~label:"+20 days" graph in
      ([ options_only; hedged; today; later ], hedge_shares, breaches))

let calendar () : Calendar.t =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = underlying; sector } ]
      ~limits:[]
      ~options:
        [
          position ~id:far_id ~expiry_in_days:calendar_far_days;
          position ~id:near_id ~expiry_in_days:calendar_near_days;
        ]
      ~rate ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph underlying (Price.of_float spot);
      List.iter [ far_id; near_id ] ~f:(fun id ->
          Graph.set_implied_vol graph id
            (Options.Implied_vol.of_float (synthetic_implied_vol ~spot ~strike)));
      (* One contract of each, to read the per-contract vegas out of the engine
         and size the spread from them rather than from a guess. Vega scales
         roughly with the square root of time, so the far leg carries more of
         it per contract and the near leg has to be the larger position. *)
      Graph.set_contracts graph far_id (Options.Contracts.of_float 1.0);
      Graph.set_contracts graph near_id (Options.Contracts.of_float 0.0);
      Graph.stabilize graph;
      let far_vega = Graph.portfolio_vega graph in
      Graph.set_contracts graph far_id (Options.Contracts.of_float 0.0);
      Graph.set_contracts graph near_id (Options.Contracts.of_float 1.0);
      Graph.stabilize graph;
      let near_vega = Graph.portfolio_vega graph in
      let near_contracts = -.calendar_far_contracts *. far_vega /. near_vega in
      Graph.set_contracts graph far_id (Options.Contracts.of_float calendar_far_contracts);
      Graph.set_contracts graph near_id (Options.Contracts.of_float near_contracts);
      Graph.stabilize graph;
      let s = Graph.snapshot graph in
      let by_bucket = Graph.Snapshot.vega_by_bucket s in
      {
        Calendar.far =
          { Leg.id = far_id; days = calendar_far_days; contracts = calendar_far_contracts };
        near = { Leg.id = near_id; days = calendar_near_days; contracts = near_contracts };
        portfolio_vega = Graph.Snapshot.portfolio_vega s;
        portfolio_gamma = Graph.Snapshot.portfolio_gamma s;
        buckets =
          List.map Options.Tenor_bucket.ordered ~f:(fun bucket ->
              ( Options.Tenor_bucket.to_string bucket,
                Option.value (Map.find by_bucket bucket) ~default:0.0 ));
      })

(* The hedge walk first and the calendar second, as the CLI ran them: each
   graph is destroyed before the next is created, so at no point do two of
   this module's graphs share Incremental's state. *)
let run () : t =
  let implied_vol = synthetic_implied_vol ~spot ~strike in
  let states, hedge_shares, breaches = walk ~implied_vol in
  let calendar = calendar () in
  {
    surface = { Surface.formula; floor; at_setup = implied_vol };
    setup =
      {
        Setup.underlying = Symbol.to_string underlying;
        id = contract_id;
        strike;
        right = Options.Right.to_string Options.Right.Call;
        expiry_days;
        contracts;
        multiplier = Options.default_multiplier;
        spot;
        rate;
        implied_vol;
      };
    states;
    hedge_shares;
    breaches;
    clock_advance_days;
    calendar;
  }
```

Then `bin/main.ml`. Delete everything from the `(* THE VOL SURFACE HERE IS INVENTED` comment through the end of `run_options` (`synthetic_implied_vol`, `options_spot` … `options_book`, the calendar comment and constants, `calendar_book`, `run_calendar_spread`, `run_options`). Keep the section banner and the `(* A separate mode rather than an overlay on the synthetic book` paragraph above it. Put this in the deleted region's place:

```ocaml
(* The walks are lib/options_walk.ml's; this is their printer. The /100 on
   every vega is here and only here: the engine carries vega per 1.00 of vol
   because that is its mathematical unit and the one the arithmetic is done
   in, and the desk's per-point figure is a display decision, which is where
   options.ml argues it belongs. *)
let print_calendar ~(strike : float) (c : Options_walk.Calendar.t) =
  let open Options_walk.Calendar in
  printf "%s\n  THE CALENDAR SPREAD: what one vega number hides\n%s\n\n" (rule 106) (rule 106);
  printf "  long  %5.0f NVDA %g calls, %.0f days out\n" c.far.Options_walk.Leg.contracts strike
    c.far.Options_walk.Leg.days;
  printf "  short %5.0f NVDA %g calls, %.0f days out\n\n"
    (Float.abs c.near.Options_walk.Leg.contracts)
    strike c.near.Options_walk.Leg.days;
  printf "  portfolio vega          %14s   <- the parallel-shift number\n"
    (money (Notional.of_float (c.portfolio_vega /. 100.0)));
  printf "  portfolio gamma         %14s\n\n" (Printf.sprintf "%.1f" c.portfolio_gamma);
  printf "  by tenor bucket:\n";
  (* The record carries all six slots; the engine's map, and the old printer,
     carried only the occupied ones. A slot is occupied here exactly when its
     vega is non-zero -- two legs of opposite sign in different buckets -- so
     printing the non-zero slots prints the lines the map would have. *)
  List.iter c.buckets ~f:(fun (bucket, vega) ->
      if Float.( <> ) vega 0.0 then
        printf "    %-8s              %14s\n" bucket (money (Notional.of_float (vega /. 100.0))));
  printf
    "\n\
    \  The total is zero and the book is not flat. It is short near-dated\n\
    \  volatility and long far-dated volatility in equal parallel-shift size --\n\
    \  a bet that the TERM STRUCTURE steepens, which is a real position with real\n\
    \  P&L and which a single vega number reports as nothing at all.\n\n\
    \  Summing vega across expiries adds sensitivities to different\n\
    \  volatilities: the 25-day implied and the 180-day implied move together\n\
    \  and not identically. Bucketing does not make that sum exact -- vega\n\
    \  within a bucket is still added across the expiries inside it -- but it\n\
    \  makes the thing being approximated visible, which is the difference\n\
    \  between an approximation and a blind spot.\n\n\
    \  The buckets are cut on days REMAINING, so a position slides between them\n\
    \  as the valuation clock advances and nothing is traded. graph.ml therefore\n\
    \  hangs the bucketing off that clock rather than assigning a bucket once at\n\
    \  construction, which would be wrong within a month and look right\n\
    \  forever.\n\n"

let run_options () =
  let w = Options_walk.run () in
  let s = w.Options_walk.setup in
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  OPTIONS (Greeks-aware exposure, synthetic vol surface, no credentials)\n\n";
  printf "  the book        short %.0f NVDA %g calls, %g days out\n"
    (Float.abs s.Options_walk.Setup.contracts)
    s.Options_walk.Setup.strike s.Options_walk.Setup.expiry_days;
  printf "  spot            %s\n" (money (Notional.of_float s.Options_walk.Setup.spot));
  printf "  implied vol     %.1f%% -- SYNTHETIC, from a smiled surface generated here\n"
    (s.Options_walk.Setup.implied_vol *. 100.0);
  printf
    "                  and labelled as such. There is no options-chain data source\n\
    \                  configured; live mode declines to invent one rather than\n\
    \                  showing made-up Greeks that look real.\n\n";
  let show (st : Options_walk.State.t) =
    printf "  %-14s %16s %14s %16s\n" st.Options_walk.State.label
      (money (Notional.of_float st.Options_walk.State.delta_equivalent))
      (Printf.sprintf "%.1f" st.Options_walk.State.gamma)
      (money (Notional.of_float (st.Options_walk.State.vega /. 100.0)))
  in
  let columns () =
    printf "  %-14s %16s %14s %16s\n" "" "delta-equiv" "gamma" "vega / vol pt";
    printf "  %s\n" (rule 64)
  in
  let hedge, clock = List.split_n w.Options_walk.states 2 in
  printf "%s\n  WHAT A DELTA HEDGE REMOVES, AND WHAT IT LEAVES\n%s\n\n" (rule 106) (rule 106);
  columns ();
  List.iter hedge ~f:show;
  printf "  %s\n\n" (rule 64);
  printf
    "  The hedge is %.0f shares, and the engine computed the ratio: it is the\n\
    \  option leg's delta-equivalent exposure divided by the spot. Delta goes to\n\
    \  zero. Gamma and vega do not move at all -- a share is linear in its own\n\
    \  price, so it contributes exactly none of either. That is the whole content\n\
    \  of the phrase FIRST-ORDER hedge, and it is why gamma and vega are their own\n\
    \  nodes instead of a column in the exposure table: there is no honest way to\n\
    \  express convexity as a quantity of underlying.\n\n"
    w.Options_walk.hedge_shares;
  printf "%s\n  LIMITS\n%s\n\n" (rule 106) (rule 106);
  List.iter w.Options_walk.breaches ~f:(fun breach ->
      printf "  %-6s %6.1f%%  %s\n"
        (if Breach.breached breach then "BREACH" else "ok")
        (Limits.utilisation breach *. 100.0)
        (Limits.to_string breach));
  printf
    "\n\
    \  The notional cap is comfortably clear, because the book IS delta flat.\n\
    \  The vega cap is not, because it never was: hedging the delta did not\n\
    \  remove a dollar of it. A risk engine that only measured exposure would\n\
    \  report this book as carrying no risk at all.\n\n";
  printf "%s\n  THETA, AND THE SECOND CLOCK\n%s\n\n" (rule 106) (rule 106);
  columns ();
  List.iter clock ~f:show;
  printf "  %s\n\n" (rule 64);
  printf
    "  Read the delta column first, because it is the one nobody expects. The\n\
    \  share count has not changed and NOTHING HAS BEEN TRADED, and yet the book\n\
    \  is no longer delta flat -- the contract decayed further out of the money,\n\
    \  its delta fell, and the hedge that exactly offset it twenty days ago now\n\
    \  over-hedges by more than half a million dollars. A delta hedge is correct\n\
    \  at an instant and stale immediately afterwards, which is precisely why\n\
    \  this number hangs off an edge instead of being stored.\n\n\
    \  Vega falls as the contract runs out of time to be uncertain in, and\n\
    \  at-the-money gamma RISES as the distribution tightens around the strike.\n\
    \  All three moved because the VALUATION clock advanced -- a separate cell\n\
    \  from the staleness clock that drives feed health, and separate on purpose.\n\
    \  Theta is real and needs a clock; wiring it to the five-second staleness\n\
    \  timer would have put the entire book on a schedule to capture a decay that\n\
    \  is invisible below a day, which is the design this project exists to\n\
    \  replace. test_options_graph.ml asserts that neither clock can do the\n\
    \  other's job.\n\n";
  print_calendar ~strike:s.Options_walk.Setup.strike w.Options_walk.calendar
```

One byte to watch: the old printer computed the whole walk, printing as it went; this one computes everything first and prints afterwards. stdout is the only stream either writes, so the bytes are the same, and the gate is the proof.

- [ ] **Step 4: Run the tests and see them pass**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: `options_walk` reports 6 passing cases and the gate prints six `GATE ok` lines. `options` is the mode this task can move; if its diff is confined to the bucket table, a slot the engine carries at exactly zero is being hidden by the `<> 0.0` filter and the record should carry `float option` instead — but `test_options_graph.ml:486` says the map holds two entries for this spread, both non-zero.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/options_walk.ml test/test_options_walk.ml test/test_ohcamel.ml bin/main.ml
git commit -m "options: the hedge walk and the calendar as a record, because the page needs the vega the limit is written in and not the one the desk reads"
```

---

### Task 8: `Garch_study` — the sample-size experiment with a progress callback

**Files:**
- Create: `lib/garch_study.ml`
- Create: `test/test_garch_study.ml`
- Modify: `bin/main.ml` — the `GARCH` section: `garch_seed`, `garch_replications`, `garch_sample_sizes`, `garch_truth`, `garch_innovations`, `mean_sd`, `run_garch` (`:881–970` before the earlier tasks shifted the numbering; find them by name)
- Modify: `lib/vol_estimators.ml:320–322` (the stale table in `fit`'s comment)
- Modify: `test/test_ohcamel.ml` (register the suite)
- Test: `test/test_garch_study.ml`

**Interfaces:**
- Consumes: `Vol_estimators.Garch11.t = { omega; alpha; beta }` with getters (`lib/vol_estimators.ml:236–244`), `Garch11.persistence` (`:249`), `Garch11.shock_half_life : t -> float option` (`:259`), `Garch11.simulate ?(burn_in = 500) ~innovations t : float array` (`:434` — needs MORE than `burn_in` innovations), `Garch11.fit ?coarse_steps ~returns () : t` (`:334`); `Synthetic_book.gaussian ~rng ~sigma` (Task 3 — at `sigma:1.0` the product is bit-identical to the CLI's unscaled Box–Muller, because `1.0 *. x` is exact); `Random.State.make` (`_opam/lib/base/random.mli:112`); `Base.List.init` calls `f` for index n−1 first (verified on this switch: `List.init 3 ~f:print_int` prints `210`) and `Base.List.map` left to right (prints `123`); `Domain.spawn : (unit -> 'a) -> 'a Domain.t`, `Domain.join : 'a Domain.t -> 'a` (`_opam/lib/ocaml/domain.mli:33, 40`; neither `Core` nor `Base` shadows `Domain`).
- Produces (exactly what Phase 5's `Reports.Garch` consumes): `Garch_study.Row.t = { n : int; alpha_mean : float; alpha_sd : float; beta_mean : float; beta_sd : float; persistence_mean : float; persistence_sd : float }`; `Garch_study.result = { seed : int; replications : int; sample_sizes : int list; burn_in : int; truth : Vol_estimators.Garch11.t; rows : Row.t list }`; `Garch_study.run : ?progress:(int -> unit) -> seed:int -> replications:int -> sample_sizes:int list -> truth:Vol_estimators.Garch11.t -> burn_in:int -> unit -> result`, where `progress` is called after EVERY fit with the running count 1 … replications × |sample_sizes|; `Garch_study.default_seed = 2026_08_25`, `default_replications = 30`, `default_sample_sizes = [ 60; 125; 250; 500; 1000; 2000 ]`, `default_burn_in = 500`, `default_truth : Vol_estimators.Garch11.t` (= `{ omega = 4e-6; alpha = 0.10; beta = 0.88 }`); `Garch_study.mean_sd : float list -> float * float` (population sd). Phase 5's `Reports.Garch.default_truth` should alias `Garch_study.default_truth`.

- [ ] **Step 1: Write the failing test**

Create `test/test_garch_study.ml`:

```ocaml
(* Unit tests for garch_study.ml.

   The README's six-row table is NOT pinned here: it is 180 maximum-likelihood
   fits and `make garch` is the reproduction, held by the byte-identical gate.
   What is tested is the contract the served host depends on -- the progress
   callback's count and order, determinism from the seed alone, that the
   study is the same on a second domain -- and the two facts about the truth
   that the verdict paragraph prints. mean_sd's two values are hand
   derivations. *)

open Core
module Garch_study = Ohcamel.Garch_study
module Garch11 = Ohcamel.Vol_estimators.Garch11

(* Two sizes, two replications, a short burn-in: eight fits at n <= 125, which
   is quick enough to run twice in a test. *)
let tiny ?progress () =
  Garch_study.run ?progress ~seed:2026_09_02 ~replications:2 ~sample_sizes:[ 60; 125 ]
    ~truth:Garch_study.default_truth ~burn_in:100 ()

let shape (r : Garch_study.result) =
  List.map r.Garch_study.rows ~f:(fun (row : Garch_study.Row.t) ->
      ( row.Garch_study.Row.n,
        Printf.sprintf "%.12f %.12f %.12f" row.Garch_study.Row.alpha_mean
          row.Garch_study.Row.beta_mean row.Garch_study.Row.persistence_mean ))

(* 2 replications x 2 sizes = 4 fits, so the callback sees 1, 2, 3, 4 -- once
   after each fit, in order, and never a count it has already passed. The
   served host's "computing k of 180" line is this list's last element. *)
let test_progress_is_called_after_every_fit_in_order () =
  let seen = ref [] in
  let r = tiny ~progress:(fun k -> seen := k :: !seen) () in
  Alcotest.(check (list int)) "1, 2, 3, 4" [ 1; 2; 3; 4 ] (List.rev !seen);
  Alcotest.(check int) "the last count is replications x sizes"
    (r.Garch_study.replications * List.length r.Garch_study.sample_sizes)
    (List.hd_exn !seen)

(* The state is built from the seed inside [run], so two calls are two
   identical streams; nothing about the result depends on what else the
   process has drawn. This is what makes the served host's table a figure
   rather than a coin toss per restart. *)
let test_two_runs_with_one_seed_agree () =
  Alcotest.(check (list (pair int string))) "same seed, same rows" (shape (tiny ())) (shape (tiny ()))

let test_the_rows_echo_the_request () =
  let r = tiny () in
  Alcotest.(check (list int)) "one row per size, in order" [ 60; 125 ]
    (List.map r.Garch_study.rows ~f:(fun row -> row.Garch_study.Row.n));
  Alcotest.(check int) "seed" 2026_09_02 r.Garch_study.seed;
  Alcotest.(check int) "burn-in" 100 r.Garch_study.burn_in;
  List.iter r.Garch_study.rows ~f:(fun (row : Garch_study.Row.t) ->
      let open Garch_study.Row in
      (* The fit is bounded inside the stationary region, so every mean is too,
         and a standard deviation is never negative. *)
      Alcotest.(check bool) "alpha in (0, 1)" true
        (Float.( > ) row.alpha_mean 0.0 && Float.( < ) row.alpha_mean 1.0);
      Alcotest.(check bool) "persistence below 0.999" true
        (Float.( < ) row.persistence_mean 0.999);
      Alcotest.(check bool) "sd >= 0" true (Float.( >= ) row.persistence_sd 0.0))

(* Textbook daily-equity parameters. 0.10 + 0.88 = 0.98, and the half-life is
   ln 0.5 / ln 0.98 = -0.693147 / -0.020203 = 34.31 periods -- the "34" the
   verdict paragraph prints with %.0f. *)
let test_the_truth_is_persistence_0_98_half_life_34 () =
  let t = Garch_study.default_truth in
  Alcotest.(check (float 1e-9)) "persistence" 0.98 (Garch11.persistence t);
  match Garch11.shock_half_life t with
  | None -> Alcotest.fail "a stationary process has a finite half-life"
  | Some h ->
      Alcotest.(check (float 0.01)) "34.31 periods" 34.31 h;
      Alcotest.(check string) "prints as 34" "34" (Printf.sprintf "%.0f" h)

let test_the_defaults_are_the_readmes_experiment () =
  Alcotest.(check int) "seed" 2026_08_25 Garch_study.default_seed;
  Alcotest.(check int) "thirty replications" 30 Garch_study.default_replications;
  Alcotest.(check (list int)) "six sizes" [ 60; 125; 250; 500; 1000; 2000 ]
    Garch_study.default_sample_sizes;
  Alcotest.(check int) "burn-in 500" 500 Garch_study.default_burn_in;
  (* The verdict paragraph reads the row at the engine's own window; it must
     be one of the sizes or the paragraph silently disappears. *)
  Alcotest.(check bool) "the engine's window is a row" true
    (List.mem Garch_study.default_sample_sizes Ohcamel.Synthetic_book.return_window
       ~equal:Int.equal)

(* [1; 2; 3; 4]: mean 2.5, population variance (2.25 + 0.25 + 0.25 + 2.25) / 4
   = 1.25, sd = 1.118034. Population, not sample: the CLI divided by n, and the
   README's +/- columns are that number. *)
let test_mean_sd_is_the_population_sd () =
  let mean, sd = Garch_study.mean_sd [ 1.0; 2.0; 3.0; 4.0 ] in
  Alcotest.(check (float 1e-9)) "mean" 2.5 mean;
  Alcotest.(check (float 1e-6)) "population sd" 1.118034 sd

(* THE ONE THE SERVED HOST DEPENDS ON. Phase 5 runs the full study on a
   spawned domain so the page can come up before the 180 fits finish; if the
   study reached any state shared with the main domain, the rows would depend
   on the scheduler. Same seed, other domain, same rows. *)
let test_the_same_rows_on_a_spawned_domain () =
  let direct = tiny () in
  let spawned = Domain.join (Domain.spawn (fun () -> tiny ())) in
  Alcotest.(check (list (pair int string))) "same rows on either domain" (shape direct)
    (shape spawned)

let suite =
  ( "garch_study",
    [
      Alcotest.test_case "progress is called after every fit, in order" `Quick
        test_progress_is_called_after_every_fit_in_order;
      Alcotest.test_case "two runs with one seed agree" `Quick
        test_two_runs_with_one_seed_agree;
      Alcotest.test_case "the rows echo the request" `Quick test_the_rows_echo_the_request;
      Alcotest.test_case "the truth is persistence 0.98, half-life 34" `Quick
        test_the_truth_is_persistence_0_98_half_life_34;
      Alcotest.test_case "the defaults are the README's experiment" `Quick
        test_the_defaults_are_the_readmes_experiment;
      Alcotest.test_case "mean_sd is the population sd" `Quick
        test_mean_sd_is_the_population_sd;
      Alcotest.test_case "THE SAME ROWS ON A SPAWNED DOMAIN" `Quick
        test_the_same_rows_on_a_spawned_domain;
    ] )
```

Register it in `test/test_ohcamel.ml`, after `Test_options_walk.suite;`:

```ocaml
      Test_options_walk.suite;
      Test_garch_study.suite;
      Test_stress.suite;
```

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -10
```

Expected: `Error: Unbound module Ohcamel.Garch_study`.

- [ ] **Step 3: Minimal implementation**

Create `lib/garch_study.ml`:

```ocaml
(* The GARCH(1,1) sample-size experiment: simulate a KNOWN process, fit it
   back, and see how close the fit lands, at several sample sizes.

   This exists to justify an absence, which is an unusual thing for a program
   to do and is the point. GARCH is the obvious next step after EWMA -- it
   adds mean reversion -- and it is implemented and tested in
   vol_estimators.ml and NOT wired into the graph, because this measurement
   says it should not be: at sixty observations, the engine's own window, the
   persistence comes back biased and with a standard deviation the size of the
   quantity. `make garch` prints the table; the served page shows the same
   one, computed in the same process.

   WHY A CALLBACK. The full study is 180 maximum-likelihood fits and takes
   seconds; the served host runs it on a second domain so the page can come up
   first, and reports "k of 180" while it runs. [progress] is called after
   every fit with the running count. The CLI passes nothing and prints at the
   end, as it always did.

   WHY THE STATE IS BUILT INSIDE. [run] makes its own Random.State from
   [seed] and consumes it sequentially -- every innovation of every
   replication at n = 60, then n = 125, and so on -- exactly as the CLI's
   loop did. Nothing about the result depends on which domain runs it or on
   what the process drew before; test_garch_study.ml asserts both. *)

open Core

module Row = struct
  type t = {
    n : int;
    alpha_mean : float;
    alpha_sd : float;
    beta_mean : float;
    beta_sd : float;
    persistence_mean : float;
    persistence_sd : float;
  }
end

type result = {
  seed : int;
  replications : int;
  sample_sizes : int list;
  burn_in : int;
  truth : Vol_estimators.Garch11.t;
  rows : Row.t list;
}

let default_seed = 2026_08_25
let default_replications = 30
let default_sample_sizes = [ 60; 125; 250; 500; 1000; 2000 ]
let default_burn_in = 500

(* Textbook daily-equity parameters. Persistence 0.98 is a shock half-life of
   about 34 days, which is what an equity index actually looks like. *)
let default_truth = Vol_estimators.Garch11.{ omega = 4e-6; alpha = 0.10; beta = 0.88 }

(* Standard normal shocks. Synthetic_book.gaussian at sigma = 1.0 is the CLI's
   unscaled Box-Muller bit for bit -- multiplying by 1.0 is exact -- so the
   README's table is reproduced from the one generator rather than a copy. *)
let innovations ~(rng : Random.State.t) ~(n : int) : float array =
  Array.init n ~f:(fun _ -> Synthetic_book.gaussian ~rng ~sigma:1.0)

(* Population standard deviation -- divided by n, not n - 1. The README's +/-
   columns are this number and the choice is kept rather than corrected: with
   thirty replications the two differ in the third decimal, which is the
   decimal the table prints. *)
let mean_sd (xs : float list) : float * float =
  let n = float_of_int (List.length xs) in
  let mean = List.fold xs ~init:0.0 ~f:( +. ) /. n in
  let var = List.fold xs ~init:0.0 ~f:(fun acc x -> acc +. ((x -. mean) ** 2.0)) /. n in
  (mean, Float.sqrt var)

let run ?(progress : int -> unit = fun _ -> ()) ~(seed : int) ~(replications : int)
    ~(sample_sizes : int list) ~(truth : Vol_estimators.Garch11.t) ~(burn_in : int) () :
    result =
  let module G = Vol_estimators.Garch11 in
  if replications < 1 then
    invalid_argf "garch_study: need at least one replication, got %d" replications ();
  if List.is_empty sample_sizes then invalid_arg "garch_study: no sample sizes";
  List.iter sample_sizes ~f:(fun n ->
      if n < 3 then
        invalid_argf "garch_study: a GARCH(1,1) fit needs at least 3 observations, got %d" n
          ());
  let rng = Random.State.make [| seed |] in
  let fitted = ref 0 in
  let rows =
    List.map sample_sizes ~f:(fun n ->
        (* List.init, as the CLI had it, and not a for-loop into an array: Base
           calls f for the LAST index first, so the fits land in the list in
           reverse draw order, and mean_sd's fold then sums them in that order.
           Floating-point addition is not associative; the README's third
           decimal is the sum in this order. *)
        let fits =
          List.init replications ~f:(fun _ ->
              let innovations = innovations ~rng ~n:(n + burn_in) in
              let path = G.simulate ~burn_in ~innovations truth in
              let fit = G.fit ~returns:path () in
              incr fitted;
              progress !fitted;
              fit)
        in
        let alpha_mean, alpha_sd = mean_sd (List.map fits ~f:G.alpha) in
        let beta_mean, beta_sd = mean_sd (List.map fits ~f:G.beta) in
        let persistence_mean, persistence_sd = mean_sd (List.map fits ~f:G.persistence) in
        { Row.n; alpha_mean; alpha_sd; beta_mean; beta_sd; persistence_mean; persistence_sd })
  in
  { seed; replications; sample_sizes; burn_in; truth; rows }
```

Then `bin/main.ml`. In the `GARCH` section, delete `garch_seed`, `garch_replications`, `garch_sample_sizes`, `garch_truth` and its comment, `garch_innovations`, `mean_sd` (from `let garch_seed = 2026_08_25` through `(mean, Float.sqrt var)`). Keep the section's opening comment (`(* This mode exists to justify an absence`). Replace `run_garch` with:

```ocaml
(* The experiment is lib/garch_study.ml; this is its printer. No progress
   callback: the CLI prints the table when it is done, as it always has. *)
let run_garch () =
  let module G = Vol_estimators.Garch11 in
  let study =
    Garch_study.run ~seed:Garch_study.default_seed
      ~replications:Garch_study.default_replications
      ~sample_sizes:Garch_study.default_sample_sizes ~truth:Garch_study.default_truth
      ~burn_in:Garch_study.default_burn_in ()
  in
  let truth = study.Garch_study.truth in
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  GARCH(1,1) -- why it is implemented and NOT wired into the engine\n\n";
  printf
    "  GARCH adds the one thing EWMA does not have: MEAN REVERSION. EWMA tracks a\n\
    \  volatility regime change and has no opinion about what happens next, because\n\
    \  with a single hand-set decay factor there is no long-run level to revert to.\n\
    \  GARCH has one, and alpha + beta -- the PERSISTENCE -- says how much of a shock\n\
    \  survives each period, which sets its half-life.\n\n\
    \  That number is the entire reason to prefer GARCH. So the question is whether\n\
    \  it can be estimated on the window this engine actually has.\n\n";
  printf "%s\n  THE EXPERIMENT\n%s\n\n" (rule 106) (rule 106);
  printf "  Simulate a KNOWN process, fit it back, repeat %d times at each sample size.\n"
    study.Garch_study.replications;
  printf
    "  Truth: alpha = %.2f, beta = %.2f, persistence = %.2f (shock half-life %s).\n\n"
    (G.alpha truth) (G.beta truth) (G.persistence truth)
    (match G.shock_half_life truth with
    | Some h -> Printf.sprintf "%.0f periods" h
    | None -> "infinite");
  printf "  %8s   %-22s %-22s %-22s\n" "n" "alpha (mean +/- sd)" "beta (mean +/- sd)"
    "persistence (mean +/- sd)";
  printf "  %s\n" (rule 80);
  List.iter study.Garch_study.rows ~f:(fun (row : Garch_study.Row.t) ->
      let open Garch_study.Row in
      printf "  %8d   %7.3f +/- %-11.3f %7.3f +/- %-11.3f %7.3f +/- %-11.3f\n" row.n
        row.alpha_mean row.alpha_sd row.beta_mean row.beta_sd row.persistence_mean
        row.persistence_sd);
  printf "  %s\n\n" (rule 80);
  (match
     List.find study.Garch_study.rows ~f:(fun row -> row.Garch_study.Row.n = return_window)
   with
  | Some row ->
      printf
        "%s\n\
        \  THE VERDICT\n\
         %s\n\n\
        \  This engine's return window is %d observations, and at %d the persistence\n\
        \  comes back at %.3f +/- %.3f against a true %.2f.\n\n\
        \  Read both numbers. The standard deviation is roughly the size of the\n\
        \  quantity being estimated, so a single fit carries almost no information --\n\
        \  and the mean is BIASED, not merely noisy: the fit systematically\n\
        \  understates how persistent volatility is, because sixty observations do not\n\
        \  contain enough evidence of a long memory to distinguish it from a short\n\
        \  one. A model that cannot tell a 34-day half-life from a 2-day one is not\n\
        \  reporting a half-life.\n\n\
        \  So GARCH(1,1) is implemented in lib/vol_estimators.ml, tested against a\n\
        \  process it recovers correctly when given enough data, and NOT wired into\n\
        \  graph.ml. The engine ships equal-weighted and EWMA, both of which are\n\
        \  parameterised rather than fitted and therefore have no sampling\n\
        \  distribution to be wrong about at this window length.\n\n\
        \  The row that says when it WOULD be defensible is n = 250 and up. That is a\n\
        \  year of daily data, and it is what a GARCH deserves.\n\n"
        (rule 106) (rule 106) return_window return_window
        row.Garch_study.Row.persistence_mean row.Garch_study.Row.persistence_sd
        (G.persistence truth)
  | None -> ());
  printf
    "  Nothing above touches the network or a credential, and the seed is fixed, so\n\
    \  this table is the same on any machine. It is the evidence for a design\n\
    \  decision rather than a demonstration of a feature.\n\n"
```

Then the stale table in `lib/vol_estimators.ml`'s comment above `fit`. Lines `:320–322` read:

```
       n = 60     beta = 0.558 +/- 0.310     persistence = 0.651 +/- 0.327
       n = 250    beta = 0.839 +/- 0.071     persistence = 0.939 +/- 0.039
       n = 1000   beta = 0.875 +/- 0.030     persistence = 0.971 +/- 0.017
```

Those are from an earlier seed and burn-in; `make garch` has printed the README's numbers (`README.md:622–627`) since. Replace the three lines with the current ones and name the source, so the next drift is visible:

```
       n = 60     beta = 0.444 +/- 0.375     persistence = 0.556 +/- 0.364
       n = 250    beta = 0.841 +/- 0.108     persistence = 0.939 +/- 0.102
       n = 1000   beta = 0.880 +/- 0.023     persistence = 0.973 +/- 0.012

     (`make garch`, seed 2026_08_25, burn-in 500 -- the table README.md prints,
     regenerated by lib/garch_study.ml.)
```

The paragraph below it -- "At sixty observations the persistence ... is estimated a third of the way from its true value with a standard deviation of the same size" -- is still right for 0.556 against 0.98 (it is now nearer half way; leave the sentence, the table beside it is what a reader checks).

- [ ] **Step 4: Run the tests and see them pass**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: `garch_study` reports 7 passing cases and the gate prints six `GATE ok` lines. `garch` is the mode this task can move; if only the `+/-` columns differ in the third decimal, the fits are being summed in a different order than `List.init` produced — check that `mean_sd` receives the list `List.init` built, unreversed and unsorted.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/garch_study.ml test/test_garch_study.ml test/test_ohcamel.ml bin/main.ml lib/vol_estimators.ml
git commit -m "garch: the sample-size study as a function with a progress callback, because the served host runs it on a second domain and has to say how far along it is"
```

---

### Task 9: `Verified` — the dated constants, and a registry that counts itself

**Files:**
- Create: `lib/verified.ml`
- Modify: `test/test_ohcamel.ml` (the whole file: the suite list lifted into a value, one new case)
- Test: `test/test_ohcamel.ml`

**Interfaces:**
- Consumes: every `Test_*.suite : string * unit Alcotest.test_case list` the registry already lists, plus Tasks 2–8's; `Alcotest.run : string -> (string * unit test_case list) list -> unit`; `List.sum (module Int)` (`_opam/lib/base/container_intf.ml:115`).
- Produces (exactly what Phase 5's `test_quoted_verified_matches_lib` and `json_of_verified` consume): `Verified.tests : int` — the number of cases `test_ohcamel` registers, asserted equal to the registry by the registry; `Verified.coverage_pct : float` — the published percentage, one decimal, as README.md's badge and docs/status.md print it; `Verified.coverage_covered : int`, `Verified.coverage_lines : int` — the fraction it is rounded from; `Verified.dated : string` — the ISO date both were last measured; and `test/test_ohcamel.ml`'s `suites : (string * unit Alcotest.test_case list) list`, which Phase 5's Task adds `Test_reports.suite` to, after `Test_server.suite`.

- [ ] **Step 1: Write the failing test**

Replace the `let () = Alcotest.run "ohcamel" [ ... ]` block at the end of `test/test_ohcamel.ml` -- from `let () =` through the closing `]` -- with the list as a value, and one case that counts it:

```ocaml
let suites =
  [
    ( "link",
      [
        Alcotest.test_case "owl reaches LAPACK (inv)" `Quick test_owl_lapack;
        Alcotest.test_case "async deferred round trip" `Quick test_async;
      ] );
    Test_risk_metrics.suite;
    Test_vol_estimators.suite;
    Test_options.suite;
    Test_options_graph.suite;
    Test_attribution.suite;
    Test_var_backtest.suite;
    Test_crisis_data.suite;
    Test_embedded_assets.suite;
    Test_recompute_log.suite;
    Test_synthetic_book.suite;
    Test_scaling_probe.suite;
    Test_validation_report.suite;
    Test_options_walk.suite;
    Test_garch_study.suite;
    Test_stress.suite;
    Test_graph.suite;
    Test_feed.suite;
    Test_history_buffer.suite;
    Test_server.suite;
    Test_alerts.suite;
    Test_properties.suite;
  ]

(* The registry counts itself against lib/verified.ml.

   The page says "N hermetic tests" in a block labelled with a date, and the
   README and docs/status.md say it too. A count that lives in three prose
   files and nowhere the compiler can see is a count that drifts the day a
   suite gains a case; this is the assertion that makes the drift a red test
   instead of a stale page. The +1 is this case, which Alcotest.run below
   registers alongside the list. *)
let test_the_count_is_the_dated_one () =
  let registered =
    1 + List.sum (module Int) suites ~f:(fun (_, cases) -> List.length cases)
  in
  Alcotest.(check int)
    (Printf.sprintf
       "lib/verified.ml says %d tests; the registry holds %d. If the registry is right, \
        set Verified.tests to %d and re-date it."
       Ohcamel.Verified.tests registered registered)
    Ohcamel.Verified.tests registered

let () =
  Alcotest.run "ohcamel"
    (suites
    @ [
        ( "verified",
          [ Alcotest.test_case "the test count is the dated one" `Quick test_the_count_is_the_dated_one ] );
      ])
```

The list above is the registry as it stands after Phase 1 (`Test_embedded_assets.suite`, placed after `Test_crisis_data.suite` by Phase 1's Task 4) and Phase 2 (which adds NO suite of its own — every Phase 2 test lands in an existing file: `test_server.ml`, `test_graph.ml`, `test_alerts.ml`, `test_feed.ml`, `test_embedded_assets.ml`); the ORDER of the existing lines is unchanged and the six Phase 3 suites sit where Tasks 2–8 put them. Phase 5's Task 1 later inserts `Test_reports.suite;` immediately after `Test_server.suite;`. `open Core` is needed for `List.sum`; add `open Core` after the file's header comment if it is not already there.

- [ ] **Step 2: Run it and see it fail**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build @all 2>&1 | head -5
```

Expected: `Error: Unbound module Ohcamel.Verified`.

- [ ] **Step 3: Minimal implementation**

Create `lib/verified.ml` with the spec's dated values. `tests` is the spec's 210 on purpose: Step 4 measures the real count and the test's own message says what to set.

```ocaml
(* What has been verified, and when. Dated constants, nothing computed.

   The page prints these in a block labelled MEASURED with [dated] beside it,
   because a test count and a coverage figure are facts about a moment and a
   machine, not about the running process -- the one number on the page that
   the process cannot vouch for by producing it. Keeping them here rather than
   in the HTML means the compiler sees them: test_ohcamel.ml asserts [tests]
   against the registry it runs, so the count cannot drift silently, and Phase
   5 pins web/quoted.json's copy to [coverage_pct] the same way.

   [coverage_pct] is the published rounding, not the quotient: 2,099 / 2,982
   is 70.389%, README.md's badge says 70%, docs/status.md says 70.4%, and a
   page that printed 70.389 would be claiming a precision `make coverage` did
   not measure (bisect_ppx counts visited points, not lines, and the number
   moves with the instrumentation). The fraction is kept beside it so the
   rounding is checkable. Re-measure both with `make coverage` and re-date
   whenever [tests] changes; a stale coverage under a fresh test count is the
   failure a single [dated] exists to prevent. *)

let tests = 210
let coverage_covered = 2_099
let coverage_lines = 2_982
let coverage_pct = 70.4
let dated = "2026-08-25"
```

- [ ] **Step 4: Run the tests, read the count, set it, run again**

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | grep -A3 'the test count is the dated one' | head -8
```

Expected: ONE failure, in the `verified` suite, whose message reads `lib/verified.ml says 210 tests; the registry holds N. If the registry is right, set Verified.tests to N and re-date it.` — with N printed. Sanity-check N against the arithmetic before believing it: the spec's 210 was 195 example cases plus 15 properties (`grep -c 'Alcotest.test_case' test/*.ml` summed, plus `test_properties.ml`'s list); Phase 1 added 9 (eight `embedded_assets`, one `crisis_data`); Phase 2 added its own (count its `test_case` lines); this phase adds 5 + 2 + 4 + 10 + 6 + 7 + 1 = 35. If N is that sum, set `let tests = N` in `lib/verified.ml`. If it is not, a suite in this plan was registered twice or not at all — fix the registry, not the constant.

Then re-measure coverage so the two numbers carry one date:

```bash
make coverage 2>&1 | tail -4
```

Expected: the summary's last line is the project total — covered points, total points, and the percentage. Set `coverage_covered`, `coverage_lines` and `coverage_pct` (to ONE decimal, as the docs print it) from that line, and `dated` to today's date, `2026-09-08`. If the floor in CI (60%) would be crossed, stop: that is a finding, not a constant.

```bash
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
```

Expected: the whole run green, `verified` reporting 1 passing case, and six `GATE ok` lines — `bin/main.ml` is untouched by this task, so the gate is a formality here and is run anyway, because the rule is "wherever `bin/main.ml` was touched in the phase", not the task.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/verified.ml test/test_ohcamel.ml
git commit -m "verified: the test count and coverage as dated constants the registry checks, because a number the page prints from prose is a number that drifts"
```

---

### Task 10: The final gate, and the documents that were wrong before this phase started

**Files:**
- Modify: `docs/status.md:86–89`, `:168–175` (the route table), `:179`, `:187`, `:209–213`, `:254`
- Modify: `README.md:53–57` (the scaling table), `:1071`
- Modify: `web/quoted.json` (the `scaling` block Phase 1 marked `STALE ON PURPOSE`)
- Modify: `test/test_embedded_assets.ml` (Phase 1's `1267` pin on the last scaling row becomes `1272`)
- Test: the gate, run on the whole phase's `bin/main.ml`; `test/test_embedded_assets.ml`'s quoted.json cases (Phase 1) stay green after the edit

**Interfaces:**
- Consumes: `Verified.{tests, coverage_pct, dated}` (Task 9) — the numbers the prose below must carry; Task 4's derivation of 63 named nodes; Phase 1's `test_embedded_assets.ml` quoted.json shape test, which counts `scaling.rows` (three) and reads `nodes_in_graph` of the last row.
- Produces: nothing a later phase calls. Phase 5's computed-vs-quoted line for the scaling table will read `agree` on `nodes_in_graph` once this lands; Phase 6 re-measures and re-dates `Verified`.

- [ ] **Step 1: The gate, one more time, on the finished `bin/main.ml`**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
git diff --stat 7e4a265..HEAD -- bin/main.ml
grep -c 'Random.State' bin/main.ml
```

Expected: six `GATE ok` lines; the diff-stat shows `bin/main.ml` net SMALLER by several hundred lines; and `Random.State` appears in `bin/main.ml` exactly twice — the demo stream's `Random.State.make [| 2026_07_30 |]` and `run_demo`'s `Random.State.int rng` — because every other state this file used to own now lives in the library module that draws from it. If a third remains, a mode still owns a stream the page cannot reproduce.

- [ ] **Step 2: Run it and see it fail**

The "test" for prose is the reader. Before editing, confirm the three statements this task strikes are the ones the code contradicts:

```bash
grep -n '2008 window is not there\|COVID and 2022 only\|| 10 | 58 |\|210 hermetic\|Coverage 70.4' docs/status.md
grep -n '^          10               58\|runs 210 tests' README.md
grep -n '"nodes_in_graph": 58' web/quoted.json
grep -n 'AS THE README STILL SAYS" 1267' test/test_embedded_assets.ml
```

Expected: one hit each (eight lines in all). After Step 3, the same greps print nothing.

- [ ] **Step 3: The edits**

**`docs/status.md:86–89`.** Strike:

```
crisis windows from a committed cache (`make backtest-crisis`): the COVID crash
(2020-02 → 2020-04) and the 2022 rate shock. **The 2008 window is not there**
— Alpaca's history begins in 2016 — and the README says so rather than
substituting anything.
```

Write:

```
crisis windows from a committed cache (`make backtest-crisis`): the 2008
financial crisis (2007-07 → 2009-12), the COVID crash (2019-06 → 2020-12) and
the 2022 rate shock (2021-06 → 2022-12) — adjusted daily closes from Yahoo
Finance via `tools/fetch_crisis_data.py`, committed under `docs/crisis/` and
compiled into the binary, so the mode runs from any directory and inside the
image. Alpaca's own history begins in 2016, which is why the cache exists.
```

**`docs/status.md:254`.** Strike `- Validation windows: COVID and 2022 only. No 2008.` Write:

```
- Validation windows: three US equity episodes, scored at TODAY's six names held at constant weights — what this book would have done, not what the book of the day did.
```

**`docs/status.md:209–213`.** Strike the table's three data rows (`| 10 | 58 | 25.6 | 58 |`, `| 100 | 337 | 25.2 | 337 |`, `| 400 | 1267 | 26.0 | 1267 |`) and write:

```
| 10 | 63 | 25.6 | 63 |
| 100 | 342 | 25.2 | 342 |
| 400 | 1272 | 26.0 | 1272 |

(The node counts were 58 / 337 / 1267 until the five option singletons —
`gamma_map`, `vega_map`, `portfolio_gamma`, `portfolio_vega`,
`vega_by_bucket` — were added to every graph, whether or not it holds
options. The per-tick column did not move, which is the point of the table.)
```

**`docs/status.md:168–175`, the route table.** After the `/api/stress` row add the two routes Phase 2 serves — nothing in this phase adds a route; Phases 4 and 5 add theirs to this table when they land:

```
| `/ops` | The operator's view: what the process is, how long it has been up, what it has recomputed |
| `/api/ops` | The same as JSON, including the recompute log's distinct and total counts and its hottest nodes |
```

and, immediately beneath the table (the spec's *Embedding* asks for this pointer, in this phase's docs change), one paragraph:

```
The page is assembled at build time from `web/` by a rule in `lib/dune`; `lib/dashboard_html.ml` no longer exists. The 47-line design essay that headed it is archived verbatim, as an HTML comment, at the head of `web/index.html`, with the successor paragraphs beneath it.
```

**`docs/status.md:179` and `README.md:1071`.** Replace `210` with the value of `Verified.tests` set in Task 9, in both. **`docs/status.md:187`.** Replace `70.4%` with `Verified.coverage_pct` if Task 9's re-measurement moved it; otherwise leave it.

**`README.md:53–57`.** Replace the three data rows of the fenced table:

```
          10               63               25.6               63
         100              342               25.2              342
         400             1272               26.0             1272
```

The `nodes per tick` column is the gate's and stays; the prose at `README.md:96` quotes `(25.6, 25.2, 26.0)` and is still right.

**`web/quoted.json`, the `scaling` block.** Replace the rows and the note:

```json
  "scaling": {
    "note": "README.md, the closing table of `make run`. The node set is 63 / 342 / 1272: the five option singletons are built into every graph, whether or not it holds options, and Phase 3 corrected README.md, docs/status.md and this block together. The per-tick column is the CLI's shared-state draws; the served host's probe draws from its own seed and lands within the drawdown node's coin flips of it, which is what the computed-vs-quoted line beside this table shows.",
    "columns": ["instruments", "nodes_in_graph", "nodes_per_tick", "if_polled"],
    "rows": [
      { "instruments": 10,  "nodes_in_graph": 63,   "nodes_per_tick": 25.6, "if_polled": 63 },
      { "instruments": 100, "nodes_in_graph": 342,  "nodes_per_tick": 25.2, "if_polled": 342 },
      { "instruments": 400, "nodes_in_graph": 1272, "nodes_per_tick": 26.0, "if_polled": 1272 }
    ]
  },
```

**`test/test_embedded_assets.ml`.** Phase 1's `test_the_pinned_cells_match_the_readme` pins `nodes_in_graph` of the LAST scaling row to `1267` (its plan `:777–779`, deliberately stale, with a comment saying Phase 3 moves it). In the same edit, replace

```ocaml
  (* Stale on purpose; see the note in the scaling block. Phase 3 corrects
     README.md, docs/status.md and this file together, and moves this pin. *)
  Alcotest.(check int)
    "scaling at 400 names: nodes in graph, AS THE README STILL SAYS" 1267
    (U.to_int (U.member "nodes_in_graph" (List.last_exn (rows "scaling"))))
```

with

```ocaml
  (* Moved here by Phase 3, which corrected README.md, docs/status.md and
     web/quoted.json together: 1272 is the current node set at 400 names, the
     five option singletons included. *)
  Alcotest.(check int)
    "scaling at 400 names: nodes in graph" 1272
    (U.to_int (U.member "nodes_in_graph" (List.last_exn (rows "scaling"))))
```

— the test was written to the stale table on purpose, and this is the change it was waiting for; say so in the commit message.

- [ ] **Step 4: Run everything and see it pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
grep -n '2008 window is not there\|COVID and 2022 only\|| 10 | 58 |' docs/status.md; grep -n '^          10               58' README.md; grep -n '"nodes_in_graph": 58' web/quoted.json; grep -n 'AS THE README STILL SAYS' test/test_embedded_assets.ml
grep -c 'archived verbatim' docs/status.md
git status --short
```

Expected: the whole suite green (the `embedded_assets` cases included — `web/quoted.json` is rebuilt into `Quoted.json` by Phase 1's dune rule, so the pin tests in Task 5 and 6 read the edited file, and the moved `1272` pin reads the corrected row); six `GATE ok` lines; the four greps print nothing; `grep -c` prints `1`; and `git status` shows only the four files this task edits (`docs/status.md`, `README.md`, `web/quoted.json`, `test/test_embedded_assets.ml`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add docs/status.md README.md web/quoted.json test/test_embedded_assets.ml
git commit -m "docs: three windows, sixty-three nodes, and a count the registry checks, because status said 2008 was missing a month after it landed"
```

---

### Task 11: The ubuntu leg's verdict on the pins, and the sentence it decides

The README's tables were measured on an M2 Pro (`README.md:84`), and every pin this phase landed — the nine synthetic and nine crisis rows in Tasks 5 and 6, the hedge walk in Task 7, the GARCH truth in Task 8 — asserts that `lib/` reproduces them. On the macOS leg that is the README's own platform family. On the ubuntu leg it is a different compiler, libm and OpenBLAS, and the spec calls that leg the cross-machine reproducibility claim; it is also the droplet's platform (amd64/linux), so it is the leg that says in advance whether Phase 5's computed-vs-quoted line will read `agrees` or `differs` when Phase 6 reads it on the deployed page. Nothing in this phase has pushed yet, so neither leg has run a single pin. This task pushes, reads the ubuntu leg's `Test` step, and records the verdict in one sentence in `docs/status.md`. It loosens nothing: a pin that fails on ubuntu is data for the owner, not a tolerance to widen.

**Files:**
- Create (scratchpad, nothing in the repository): `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh`, and the `jobs.txt` and `ubuntu-test.log` it writes
- Modify: `docs/status.md` (the `**CI**` bullet under `## What is verified`, lines 191–195 — one sentence appended after `are noise.`; Task 10 edited `:179` and `:187` in this section and left this bullet alone, and Phase 6 Task 11 rewrites the section's numbers with the bullet still in place)
- Modify: nothing under `lib/`, `test/` or `deploy/`
- Test: the ubuntu leg's `Test` step log, read by `ubuntu-pins.sh` — the seven pin cases each `[OK]`, none `[FAIL]`, none missing

**Interfaces:**
- Consumes: `.github/workflows/ci.yml`'s `build-and-test` job — `strategy.matrix.os: [ubuntu-latest, macos-latest]`, `fail-fast: false`, the `Test` step `opam exec -- dune runtest --force` — so the two job names are `build-and-test (ubuntu-latest)` and `build-and-test (macos-latest)`; `gh run list --workflow ci.yml --commit <sha>`, `gh run watch <id> --exit-status`, `gh run view <id> --json jobs`, `gh run view --job <id> --log` (gh 2.93 on this machine; the log lines are `<job>\t<step>\t<timestamp> <line>`); the suite names Task 9's registry holds (`validation_report`, `options_walk`, `garch_study`) and the case titles Tasks 5–8 wrote — `THE NINE SYNTHETIC ROWS ARE THE README'S`, `THE NINE CRISIS ROWS ARE THE README'S`, `THE HEDGE IS 1,338 SHARES AND FLATTENS DELTA`, `nvda-vega is breached at 141.5%`, `twenty days later`, `the calendar spread`, `the truth is persistence 0.98, half-life 34`. Alcotest prints one `[OK]`/`[FAIL]` line per case as suite name, index, title, and may cut a long title at the terminal width, so the script matches on prefixes.
- Produces: the sentence in `docs/status.md`'s CI bullet saying both legs run the pins, naming the run; and the fact Phase 5 Task 13's two wordings (`agrees with the README's table to four decimals` / `computed on this host (…): differs … in N cells`) and Phase 6 Task 7's droplet reading rest on — amd64/linux reproduces the README's cells, or it does not and the owner has decided what the page says before it is public.

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh <<'SH'
#!/bin/bash
# The ubuntu leg's verdict on the README pins. Usage: ubuntu-pins.sh [sha]
# Exit 0 only when a ci run exists for the sha, it has concluded, and every
# pin case on the ubuntu leg is [OK]. Prints the pin lines either way.
set -u
cd /Users/ajaiupadhyaya/Documents/OhCamel
SHA="${1:-$(git rev-parse HEAD)}"
OUT=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci
RUN=$(gh run list --workflow ci.yml --commit "$SHA" --json databaseId --jq '.[0].databaseId // empty')
if [ -z "$RUN" ]; then echo "no ci run for $SHA -- not pushed, or not started"; exit 1; fi
gh run watch "$RUN" --exit-status > /dev/null 2>&1 || echo "run $RUN did not conclude green (a leg failed; read on)"
gh run view "$RUN" --json jobs --jq '.jobs[] | "\(.name)\t\(.conclusion)"' | tee "$OUT/jobs.txt"
JOB=$(gh run view "$RUN" --json jobs --jq '.jobs[] | select(.name == "build-and-test (ubuntu-latest)") | .databaseId')
gh run view --job "$JOB" --log > "$OUT/ubuntu-test.log"
echo "--- pin cases on ubuntu (run $RUN, job $JOB) ---"
fail=0
for pin in "validation_report.*THE NINE SYNTHETIC" "validation_report.*THE NINE CRISIS" \
           "options_walk.*THE HEDGE IS" "options_walk.*nvda-vega is breached" \
           "options_walk.*twenty days later" "options_walk.*the calendar spread" \
           "garch_study.*the truth is persistence"; do
  line=$(grep -E "\[(OK|FAIL)\] +$pin" "$OUT/ubuntu-test.log" | head -1 | sed $'s/^[^\t]*\t[^\t]*\t[^ ]* //')
  case "$line" in
    *"[OK]"*)   echo "PASS  $line" ;;
    *"[FAIL]"*) echo "FAIL  $line"; fail=$((fail + 1)) ;;
    *)          echo "MISSING  $pin -- the case did not run, or its title changed"; fail=$((fail + 1)) ;;
  esac
done
# The differing cells, when there are any: Alcotest prints the expectation and the value under the FAIL line.
grep -A 12 '\[FAIL\]' "$OUT/ubuntu-test.log" | grep -E 'Expected|Received|ASSERT|FAIL' | head -40
echo "ubuntu leg: $fail pin cases not OK"
exit "$fail"
SH
chmod +x /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel && git status -sb | head -1
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh
```

Expected: `## main...origin/main [ahead N]` — the phase's commits are local — then `no ci run for <sha> -- not pushed, or not started` and exit 1.

- [ ] **Step 3: Push, and let both legs run**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git status --short                      # empty: Task 10 committed everything, the gate scripts are scratch
git log --oneline origin/main..HEAD     # this phase's commits, Task 2 through Task 10
git push origin main
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh
```

Expected: `gh run watch` waits for the run (Owl is built from source on both runners; twenty to forty minutes with a cold dune cache), then `build-and-test (ubuntu-latest)\tsuccess`, `build-and-test (macos-latest)\tsuccess`, `coverage\tsuccess`, seven `PASS` lines and `ubuntu leg: 0 pin cases not OK`. Then one of two things is true:

**(a) Seven `PASS`.** amd64/linux reproduces the README's cells to the decimals the pins hold. Append this sentence inside the `**CI**` bullet of `docs/status.md`'s `## What is verified`, immediately after `are noise.` (`:195`; the only `are noise.` in the file), keeping the bullet's two-space wrap:

```
  Since the extraction phase both legs also run the README-value pins — the
  nine synthetic and nine crisis validation rows, the delta-hedge walk and the
  GARCH truth — so the tables are reproduced on amd64/linux, the droplet's
  platform, as well as on the arm64 macOS the README quotes, to the four
  decimals it prints (run <RUN>, <YYYY-MM-DD>).
```

with `<RUN>` and the date from the script's output. That is the whole footnote: Phase 5 Task 13's computed-vs-quoted line will read `agrees` on the droplet, and the README's tables need no platform caveat.

**(b) Any `FAIL`.** STOP here; do not edit a pin, a tolerance or the README. The differing cells are in the script's `Expected`/`Received` lines and in `phase3-ci/ubuntu-test.log`. The macOS leg is green — the pins hold on the README's platform — so what has been learned is that a README table is a one-platform number, and the owner decides the footnote wording before anything is public. The spec leaves that decision open between two forms: a README note under the affected table naming the platform and the decimal at which amd64 departs, with that cell's pin narrowed to the decimals both platforms share and its header saying so; or the page printing Phase 5's `differs … in N cells` line on the droplet as the honest reading. Neither is this task's to choose. Write the cells and the run id into the working log, and the phase does not end until both legs are green.

- [ ] **Step 4: Run the test and see it pass**

```bash
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-ci/ubuntu-pins.sh
cd /Users/ajaiupadhyaya/Documents/OhCamel
grep -c 'README-value pins' docs/status.md
grep -n 'to the four' docs/status.md
git diff --stat
```

Expected: the same run re-read — seven `PASS`, `ubuntu leg: 0 pin cases not OK`, exit 0; `1`; one line, inside the CI bullet, ending in the run id and date; and a diff touching `docs/status.md` alone.

- [ ] **Step 5: Commit and push**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add docs/status.md
git commit -m "docs: the README's tables hold on amd64 too, because a number measured on one laptop is a claim until a second platform reproduces it"
git push origin main
```

---

## Done when

- `gate.sh` prints six `GATE ok` lines against the baseline captured in Task 1, on the final `bin/main.ml` — `synthetic`, `stress`, `backtest`, `backtest-crisis`, `options`, `garch` byte-identical — and `bin/main.ml` holds exactly two `Random.State` references (the demo stream and `run_demo`'s pick).
- `lib/` has `recompute_log.ml`, `synthetic_book.ml`, `scaling_probe.ml`, `validation_report.ml`, `options_walk.ml`, `garch_study.ml`, `verified.ml`, each with the signatures its task's **Produces** line names, and no `.mli`; Phase 5's `Reports` compiles against them without a deviation path.
- `dune build @fmt` passes; `dune runtest --force` is green with the six new suites (`recompute_log` 5, `synthetic_book` 2, `scaling_probe` 4, `validation_report` 10, `options_walk` 6, `garch_study` 7) plus `verified` 1, and `Verified.tests` equals the registry's count.
- `ohcamel backtest-crisis` prints its table from a directory with no `docs/`; `test/test_crisis_data.ml` walks the cwd in exactly one test (Phase 1's embedded-equals-disk comparison).
- The nine synthetic and nine crisis rows, the hedge (1,338 shares, 141.5%, $657,690 / −25.2 / $−1,502, ±$12,559, −72.5) and the GARCH truth (0.98, half-life 34) are pinned by tests whose headers say they are pins.
- `docs/status.md`, `README.md` and `web/quoted.json` say 63 / 342 / 1272, three crisis windows, and the measured test count; `lib/vol_estimators.ml`'s comment table matches `make garch`; `bin/main.ml` no longer says Christoffersen-Pelletier is unimplemented.
- The phase is pushed and CI is green on both legs for its last commit; `docs/status.md`'s CI bullet says the README-value pins pass on `ubuntu-latest` and `macos-latest` and names the run (Task 11) — or, if amd64 did not reproduce a cell, the owner has decided the footnote wording before anything is public, and no pin was loosened to get there.
- Every task committed on its own, tree clean, the eight invariants in `docs/status.md` intact — in particular invariant 2: no arithmetic in this phase is a second implementation of anything `Var_backtest`, `Graph` or `Vol_estimators` already does.
