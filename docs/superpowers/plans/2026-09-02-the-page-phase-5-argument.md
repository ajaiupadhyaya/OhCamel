# The page — Phase 5: the reports and the argument — Implementation Plan

> **Superseded 2026-09-10.** Not executed as written. Its scope is folded into items 4–9 of `2026-09-10-the-page-finish-line.md`, with the cuts listed there. This file stays as a parts bin for the encoders, routes and GARCH domain.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every report the CLI prints — the scaling probe, both validation batteries, the options walk and the GARCH study — on the wire as records the engine computed at startup, and build the nine-section argument below the graph that reads them, each section saying in one of three fixed ways where its numbers came from.

**Architecture:** `lib/reports.ml` runs the four static reports once, before the socket binds, and holds the encoded JSON as one cached string; the GARCH study runs afterwards on a second OCaml 5 domain, publishing its fit count through an `Atomic.t` that a one-second `Clock_ns` poll on the main domain reads until it can `Domain.join`. Two new read-only routes serve those strings. On the client, `web/charts.js` adds the eight report chart forms to the sparkline idiom already in the page and `web/argument.js` renders the nine README sections, each headed by a filtered fragment of the Figure 1 renderer, each captioned with a provenance tag, and each computed table checked against `web/quoted.json` in one printed line.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route: every route stays GET and read-only, including §07's "run it again on a fork".
- No persistence: every report is computed into process memory at startup and is gone at restart; there is no uptime percentage and no record of the last smoke run, and the page says both.
- No external asset: no CDN, no web font, no charting library, no framework — every chart is inline SVG drawn by hand in the idiom the sparklines already use.
- The live host's gate is untouched: no Caddy CORS on the live host, no `basic_auth` matcher exemption, no `/api/up`.
- No invented vol surface on either deployed book: the options figure is the CLI's one-name NVDA graph with the synthetic smile, labelled SYNTHETIC in every line it appears in.
- No number this process did not produce styled as if it had: quoted figures carry `QUOTED · source · hardware · date` and sit in `--ink-soft`, never `--ink`.
- No second implementation of anything the engine computes: the client derives closures from the served topology and tallies from served frames, and never recomputes risk.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged.
- The eight invariants in `docs/status.md` (§ *The invariants*, lines 236–243) hold; a change that ships faster by breaking one is a regression even if the tests pass.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in the repo).
- All tests stay hermetic: no network, no credentials, nothing waiting on a wall clock.
- The byte-identical stdout gate for the six credential-free modes applies wherever `bin/main.ml` is touched (Task 9).

---

## Working conventions for this phase

- **One repository, one machine.** Everything runs in `/Users/ajaiupadhyaya/Documents/OhCamel`. Nothing in this phase touches the droplet; that is Phase 6.
- **Build and test.** `make build` and `make test` re-enter the project-local opam switch themselves. A single alcotest suite runs as `dune exec test/test_ohcamel.exe -- test <suite>` after `eval $(opam env --switch=$PWD --set-switch)`, or without the env dance as `./_build/default/test/test_ohcamel.exe test <suite>` once `make build` has run. Both forms are used below; they are equivalent.
- **`node` is a dev-machine convenience only.** `node --check` is used to catch a JavaScript syntax error before a five-minute OCaml rebuild. It is **not** a CI gate and must not be added to the Makefile or a workflow — the image has no node and the page's real gate is the OCaml page test added in Task 10.
- **Ignore `claudecodehandoff.md`.** The untracked file at the repository root belongs to another project. Never read it, never stage it.
- **Commit per task**, in this repository's voice: a lowercase area prefix (`server:`, `graph:`, `web:`, `deploy:`, `docs:`) then a sentence that says *why*. The executor adds the trailers; do not write them.

## What earlier phases hand you

These names are consumed verbatim. Phases 3 and 4 are being written in parallel with this plan, so each is stated as an exact assumed signature; where a phase lands a different shape, the deviation is named in the task that consumes it and is a one-line edit, not a redesign.

**Phase 1.** `web/head.html`, `web/page.css`, `web/index.html`, `web/dashboard.js` (and `web/ops.html`, `web/ops.js` for `/ops`), embedded into `Dashboard_html.page : string` by a rule in `lib/dune` whose `deps` are `(glob_files ../web/*)` and whose body `cat`s the files in order; `web/quoted.json` exists as `Quoted.json` but is NOT yet catted into the page (Task 10(e) here adds it). Phase 1 creates no `format.js`, `graph.js`, `charts.js` or `argument.js`: `format.js` and `graph.js` are Phase 4 Task 12's; `charts.js` and `argument.js` are created here. `Quoted.json : string` (the contents of `web/quoted.json`, so the test binary can parse it). `Crisis_data.embedded` and `Crisis_data.load_all_embedded : unit -> Crisis_data.Window.t list`.

**Phase 2.** `Server.t` carries `mode : [ \`Demo | \`Live ]`, `started_at`, `port`, `peer`, `feed_stats`, `quiet`. `Server.routes : (string * string * handler) list` — path, purpose, handler — with `handle` dispatching from it and the 404 body's `routes` list generated from the same table. `Server.json_of_ops` with a `reports` slot holding `{static, garch}`. `json_headers` is mode-aware, so a demo-mode server adds `Access-Control-Allow-Origin: *` to JSON routes and a live-mode one adds nothing. `web/ops.html` + `web/ops.js`.

**Phase 3.** `Synthetic_book.book : (Types.Symbol.t * Types.Sector.t * float * float) list` (symbol, sector, mark, qty — the six names moved from `bin/main.ml:56–64`), `Synthetic_book.confidence : float`, `Synthetic_book.return_window : int`. `Scaling_probe.rows : seed:int -> sizes:int list -> ticks:int -> row list` with `row = { instrument_count : int; named_nodes : int; nodes_per_tick : float; ns_per_tick : float; ... }`. `Validation_report.synthetic : unit -> t` and `Validation_report.crisis : Crisis_data.Window.t list -> t`. `Validation_report.worst_burst : span:int -> bool array -> int * int`. `Options_walk.run : unit -> Options_walk.t`. `Garch_study.run : ?progress:(int -> unit) -> seed:int -> replications:int -> sample_sizes:int list -> truth:Vol_estimators.Garch11.t -> burn_in:int -> unit -> Garch_study.result`. `Verified.tests : int`, `Verified.coverage_pct : float`, `Verified.dated : string`. `test_ohcamel.ml`'s suite list lifted out of the `Alcotest.run` call into a `let suites = [ ... ]` value.

**Phase 4.** `Server.json_of_graph`, `GET /api/graph`, `/api/snapshot` with `recomputed`, `by_node`, `positions[].marginal|standalone|risk_share`, `GET /api/stress` in the new shape with `counter_cost` and `duration_ms`. `web/format.js` with `window.OhCamelFormat = { money, pct, el }` and `web/graph.js` with `window.OhCamelGraph = { render(container, topology, {filter?: {nodes?, families?}, compact?, inspector?}) -> handle, filter(topology, {nodes?, families?}) -> sub-topology, closure(topology, names, 'down'|'up') -> Set }` and `handle.{light, dim, setValues, setNote, destroy}` (Phase 4 Task 12; `families` are the strings `input|per_symbol|per_sector|per_limit|per_option|singleton`). `lib/dune`'s cat line reading `(cat ../web/format.js ../web/graph.js ../web/dashboard.js)`. `Server.create … ?recompute_log …` (Phase 4 Task 6). The header and ledger DOM, and `<footer>` (Phase 4 Tasks 13–14).

---

### Task 1: `lib/reports.ml` — the four static reports, computed once

**Files:**
- Create: `lib/reports.ml`
- Create: `test/test_reports.ml`
- Modify: `test/test_ohcamel.ml` (the suite list)

**Interfaces:**
- Consumes: `Scaling_probe.rows : seed:int -> sizes:int list -> ticks:int -> Scaling_probe.row list` with `row = { instrument_count : int; named_nodes : int; nodes_per_tick : float; ns_per_tick : float; ... }` (Phase 3); `Validation_report.synthetic : unit -> Validation_report.t` and `Validation_report.crisis : Crisis_data.Window.t list -> Validation_report.t` (Phase 3); `Options_walk.run : unit -> Options_walk.t` (Phase 3); `Crisis_data.load_all_embedded : unit -> Crisis_data.Window.t list` (Phase 1); `Graph.total_nodes_recomputed : unit -> int` (exists — `lib/server.ml` calls it in `json_of_snapshot`); `Types.Time.now`, `Types.Time.diff`, `Types.Time.Span` (exist — `lib/types.ml:172–183`, `Time` includes `Time_ns.Alternate_sexp` and re-exports `now`, `diff`, `add`, `epoch`, with `module Span = Time_ns.Span`).
- Produces: `Reports.t = { computed_at : Types.Time.t; computed_in_ms : float; ticks : int; scaling : Scaling_probe.row list; scaling_cost : int; synthetic : Validation_report.t; crisis : Validation_report.t; options : Options_walk.t }` and `Reports.compute : ?sizes:int list -> ?ticks:int -> unit -> Reports.t`, plus the module constants `Reports.scaling_seed`, `Reports.scaling_ticks`, `Reports.scaling_sizes`, `Reports.burst_span`.

- [ ] **Step 1: Write the failing test.** Create `test/test_reports.ml`:

  ```ocaml
  (* Phase 5. The reports, as records and as the wire format the page reads.

     Nothing here re-derives a risk number. Validation_report, Scaling_probe and
     Options_walk are tested against hand-derived and README-pinned values in
     their own files; what is tested here is the ORCHESTRATION -- that all four
     reports are produced by one call, in one process, with the graphs they
     created destroyed -- and the ENCODER, which is a contract between two
     languages that nothing in between will catch a disagreement in. *)

  open Core
  module Reports = Ohcamel.Reports

  (* One compute for the whole file. The batteries are the expensive part
     (eighteen rolling backtests) and they do not vary between cases, so
     running them once and sharing the record is the difference between a
     suite that runs in a fifth of a second and one that runs in two.

     The scaling probe is cut to one size and five ticks: its VALUES are pinned
     in test_scaling_probe.ml against the current node set, and re-running the
     400-name probe here would buy a slower suite and no assertion. *)
  let computed = lazy (Reports.compute ~sizes:[ 10 ] ~ticks:5 ())

  let test_one_call_produces_all_four () =
    let r = Lazy.force computed in
    Alcotest.(check int) "one scaling row per size asked for" 1 (List.length r.Reports.scaling);
    Alcotest.(check bool)
      "the probe cost the process-wide counter something" true
      (r.Reports.scaling_cost > 0);
    (* Three series x three estimators, and three windows x three estimators.
       Eighteen rows is what smoke.sh asserts against the deployed host, so it
       is asserted here first. *)
    Alcotest.(check int)
      "nine synthetic rows" 9
      (List.length r.Reports.synthetic.Ohcamel.Validation_report.rows);
    Alcotest.(check int)
      "nine crisis rows" 9
      (List.length r.Reports.crisis.Ohcamel.Validation_report.rows);
    (* options only, + the hedge, today, +20 days. *)
    Alcotest.(check int)
      "four states in the options walk" 4
      (List.length r.Reports.options.Ohcamel.Options_walk.states)

  let test_compute_is_positive_and_timed () =
    let r = Lazy.force computed in
    Alcotest.(check bool)
      "computed_in_ms is a real elapsed time, not a zero" true
      (Float.( > ) r.Reports.computed_in_ms 0.0 && Float.is_finite r.Reports.computed_in_ms)

  (* The report graphs are throwaways and every one of them is destroyed. An
     undestroyed graph recomputes on every stabilize in this process forever,
     so the observable symptom is that the process-wide counter keeps moving
     after compute returns with nothing asking it to. Two reads either side of
     a quiet moment is the cheapest way to see that. *)
  let test_no_report_graph_survives () =
    let _ = Lazy.force computed in
    let a = Ohcamel.Graph.total_nodes_recomputed () in
    let idle =
      Ohcamel.Graph.create ~instruments:[] ~limits:[] ~confidence:0.95 ~return_window:10 ()
    in
    Exn.protect
      ~f:(fun () ->
        Ohcamel.Graph.stabilize idle;
        Ohcamel.Graph.stabilize idle)
      ~finally:(fun () -> Ohcamel.Graph.destroy idle);
    let b = Ohcamel.Graph.total_nodes_recomputed () in
    (* An empty graph stabilized twice recomputes a handful of singleton nodes
       once. If a report graph were still alive it would add its own hundreds
       on each of those stabilizes. *)
    Alcotest.(check bool)
      (Printf.sprintf "an idle stabilize costs a handful of nodes, not a report's (%d)"
         (b - a))
      true
      (b - a < 200)

  let suite =
    ( "reports",
      [
        Alcotest.test_case "one call produces all four reports" `Quick
          test_one_call_produces_all_four;
        Alcotest.test_case "compute is timed" `Quick test_compute_is_positive_and_timed;
        Alcotest.test_case "no report graph survives compute" `Quick
          test_no_report_graph_survives;
      ] )
  ```

  Then register it. `test/test_ohcamel.ml` — Phase 3 lifted the suite list out of the `Alcotest.run` call into a `let suites = [ ... ]` value so `Verified.tests` can count it. Add one line to that list, immediately after `Test_server.suite;`:

  ```ocaml
      Test_reports.suite;
  ```

  If Phase 3's lift has not landed and the list is still inline in `Alcotest.run`, add the same line in the same position there.

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -20
  ```
  Expected failure: `Error: Unbound module Reports` (from `test/test_reports.ml`), because `lib/reports.ml` does not exist yet.

- [ ] **Step 3: Minimal implementation.** Create `lib/reports.ml`:

  ```ocaml
  (* Phase 5. The reports, computed once, before the socket binds.

     Every table the CLI prints exists only as terminal output and as README
     prose, and the deployed host -- which could reproduce most of them on
     demand -- reproduces none. This module is the fix, and the shape of the fix
     is the argument: the reports are COMPUTED, once, at startup, into process
     memory, and served as a string. Not recomputed per request, because a
     scaling probe in a request handler is a denial of service with a URL; not
     persisted, because nothing in this project persists and a cached table on
     disk would be a number whose provenance nobody could state.

     WHY THE ORDER MATTERS. The scaling probe creates three graphs of up to 400
     names and thousands of nodes. It runs FIRST, before the served graph
     exists, so that those creations land before the first tick -- otherwise
     the smoke suite's "nodes_recomputed advances across two seconds" read is
     partly the probe's tail rather than the engine's pulse, and the one
     assertion that carries the deploy's weight would be measuring the wrong
     thing.

     Every graph any of these reports creates is destroyed by the module that
     created it, in Exn.protect. The stabilize is process-wide: an undestroyed
     report graph recomputes on every tick of the served one, forever. *)

  open Core

  (* The CLI's own constants, so the page and the terminal cannot drift. The
     seed is bin/main.ml's [rng] seed, the tick count is scaling_report's, and
     the sizes are the three the README's table quotes. *)
  let scaling_seed = 2026_07_30
  let scaling_ticks = 50
  let scaling_sizes = [ 10; 100; 400 ]

  (* One month of sessions, from bin/main.ml's crisis mode. Short enough that a
     burst inside it is a burst rather than a season. *)
  let burst_span = 21

  type t = {
    computed_at : Types.Time.t;
    computed_in_ms : float;
    (* What the probe was actually asked for, rather than the default, so the
       wire cannot claim fifty ticks when a test ran five. *)
    ticks : int;
    scaling : Scaling_probe.row list;
    (* The probe's own cost against Incremental's process-wide counter. The
       footer prints that counter and the page has to be able to say why it
       starts in the thousands on a 53-node graph. *)
    scaling_cost : int;
    synthetic : Validation_report.t;
    crisis : Validation_report.t;
    options : Options_walk.t;
  }

  let compute ?(sizes = scaling_sizes) ?(ticks = scaling_ticks) () : t =
    let started = Types.Time.now () in
    let before = Graph.total_nodes_recomputed () in
    let scaling = Scaling_probe.rows ~seed:scaling_seed ~sizes ~ticks in
    let scaling_cost = Graph.total_nodes_recomputed () - before in
    let synthetic = Validation_report.synthetic () in
    let crisis = Validation_report.crisis (Crisis_data.load_all_embedded ()) in
    let options = Options_walk.run () in
    let finished = Types.Time.now () in
    {
      computed_at = finished;
      computed_in_ms = Types.Time.Span.to_ms (Types.Time.diff finished started);
      ticks;
      scaling;
      scaling_cost;
      synthetic;
      crisis;
      options;
    }
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 3 tests run.`

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml test/test_ohcamel.ml && git commit -m "reports: the four startup reports behind one call, because the probe has to run before the served graph exists"
  ```

---

### Task 2: The wire's float rules — `jfloat`, six significant digits, platform and scaling

**Files:**
- Modify: `lib/reports.ml` (append after `compute`)
- Modify: `test/test_reports.ml` (append two cases before `let suite`)

**Interfaces:**
- Consumes: `Server.jfloat : float -> Yojson.Safe.t` (exists — `lib/server.ml:56–61`, `if Float.is_finite x then \`Float x else \`Null`); `Build_info.architecture : string` and `Build_info.system : string` (Phase 1's generated module, from dune's `%{ocaml-config:architecture}` and `%{ocaml-config:system}`); `Core.Sys.ocaml_version : string` (verified: `open Core; Sys.ocaml_version` prints `5.2.1` on this switch); `Float.round_significant : float -> significant_digits:int -> float` (verified: `_opam/lib/base/float.mli:236`).
- Produces: `Reports.jfloat`, `Reports.jopt_float`, `Reports.jstring`, `Reports.jlist`, `Reports.jfloat6`, `Reports.json_of_platform : unit -> Yojson.Safe.t`, `Reports.json_of_scaling : t -> Yojson.Safe.t`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_reports.ml`, immediately before `let suite =`:

  ```ocaml
  (* The two encoders must agree about what a non-finite float is, and they are
     two functions because server.ml depends on this module rather than the
     reverse. A NaN that escaped here would take out the whole page rather than
     one number, which is the reason the rule exists at all -- so the agreement
     is asserted rather than assumed. *)
  let test_jfloat_agrees_with_the_servers () =
    List.iter [ Float.nan; Float.infinity; Float.neg_infinity; 0.0; -1.5; 1e-9 ]
      ~f:(fun x ->
        Alcotest.(check string)
          (Printf.sprintf "jfloat %g" x)
          (Yojson.Safe.to_string (Ohcamel.Server.jfloat x))
          (Yojson.Safe.to_string (Reports.jfloat x)))

  (* Six significant digits on every float that travels in a SERIES. Nine
     hundred and forty forecasts, three estimators, three windows: full
     seventeen-digit floats would roughly triple a payload whose sixth digit
     nothing on the page can draw. Applied BEFORE the finite check, so a NaN
     still becomes null rather than a rounded NaN. *)
  let test_series_floats_are_rounded () =
    Alcotest.(check string)
      "0.0123456789 keeps six figures" "0.0123457"
      (Yojson.Safe.to_string (Reports.jfloat6 0.0123456789));
    Alcotest.(check string)
      "12345.6789 keeps six figures" "12345.7"
      (Yojson.Safe.to_string (Reports.jfloat6 12345.6789));
    Alcotest.(check string)
      "a NaN in a series is still null" "null"
      (Yojson.Safe.to_string (Reports.jfloat6 Float.nan))
  ```

  And add the two cases to the `suite` list, after the existing three:

  ```ocaml
        Alcotest.test_case "jfloat agrees with the server's" `Quick
          test_jfloat_agrees_with_the_servers;
        Alcotest.test_case "series floats round to six significant digits" `Quick
          test_series_floats_are_rounded;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -10
  ```
  Expected failure: `Error: Unbound value Reports.jfloat` (and `Reports.jfloat6`).

- [ ] **Step 3: Minimal implementation.** Append to `lib/reports.ml`:

  ```ocaml
  (* ------------------------------------------------------------------------ *)
  (* JSON                                                                      *)
  (* ------------------------------------------------------------------------ *)

  (* server.ml's [jfloat], written again here rather than called.

     server.ml depends on THIS module -- Server.create takes the computed
     reports -- so calling Server.jfloat would be a dependency cycle. What is
     duplicated is four tokens; what is NOT duplicated is any arithmetic. The
     rule itself (a non-finite float is null, because NaN is not JSON and a body
     the browser cannot parse loses every number rather than one) is the wire
     contract, and test_reports.ml asserts the two functions agree on it, so the
     duplication cannot drift silently. *)
  let jfloat (x : float) : Yojson.Safe.t = if Float.is_finite x then `Float x else `Null
  let jopt_float = function None -> `Null | Some x -> jfloat x
  let jstring s : Yojson.Safe.t = `String s
  let jlist f xs : Yojson.Safe.t = `List (List.map xs ~f)

  (* Every float that travels inside a SERIES goes through this one.

     Three crisis windows x three estimators x up to 570 forecasts of VaR, plus
     three realised-return arrays: at seventeen digits that is most of the
     payload, and the seventh digit is smaller than a pixel on any chart the
     page draws. Rounding happens BEFORE the finite check, so a non-finite value
     still becomes null rather than a rounded NaN.

     Scalars are NOT rounded. A p-value the README quotes to four places and a
     dollar total the ledger shows in full are read as numbers, not drawn as
     shapes, and truncating them would make the computed-vs-quoted line
     disagree with the README for a reason that is this function's fault. *)
  let jfloat6 (x : float) : Yojson.Safe.t =
    jfloat (Float.round_significant x ~significant_digits:6)

  (* Which OCaml, on which machine. The architecture and system come from dune's
     %{ocaml-config:...} at build time and are therefore the IMAGE's, not the
     developer's: the page's whole computed-vs-quoted argument is that a fixed
     seed reproduces on a different libm, and the reader has to be able to see
     which one answered. *)
  let json_of_platform () : Yojson.Safe.t =
    `Assoc
      [
        ("ocaml_version", jstring Sys.ocaml_version);
        ("architecture", jstring Build_info.architecture);
        ("system", jstring Build_info.system);
      ]

  (* [if_polled] is [nodes_in_graph] and is emitted anyway.

     They are the same number because that IS the argument -- a polling engine
     redoes the whole graph per event -- and the CLI's table prints the column
     twice for exactly that reason. Deriving it on the client would put the
     claim in JavaScript; emitting it keeps the claim where the measurement is. *)
  let json_of_scaling (t : t) : Yojson.Safe.t =
    `Assoc
      [
        ("seed", `Int scaling_seed);
        ("ticks", `Int t.ticks);
        ( "rows",
          jlist
            (fun (r : Scaling_probe.row) ->
              `Assoc
                [
                  ("instruments", `Int r.Scaling_probe.instrument_count);
                  ("nodes_in_graph", `Int r.Scaling_probe.named_nodes);
                  ("nodes_per_tick", jfloat r.Scaling_probe.nodes_per_tick);
                  ("if_polled", `Int r.Scaling_probe.named_nodes);
                ])
            t.scaling );
        ("cost_nodes_recomputed", `Int t.scaling_cost);
      ]
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 5 tests run.`

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml && git commit -m "reports: six significant digits in a series, because the seventh is smaller than a pixel and triples the payload"
  ```

---

### Task 3: The validation encoders — config, the nine-column row, both batteries

**Files:**
- Modify: `lib/reports.ml` (append after `json_of_scaling`)
- Modify: `test/test_reports.ml` (append one case)

**Interfaces:**
- Consumes: `Var_backtest.report` with `[@@deriving fields ~getters]` (exists — `lib/var_backtest.ml:464–500`), giving `Var_backtest.estimator`, `.confidence`, `.observations`, `.exceptions`, `.expected_exceptions`, `.observed_rate`, `.expected_rate`, `.kupiec_statistic`, `.kupiec_p`, `.independence_statistic`, `.independence_p`, `.conditional_coverage_statistic`, `.conditional_coverage_p`, `.duration_shape`, `.duration_statistic`, `.duration_p`, `.zone`, `.zone_cumulative_probability`, `.worst_loss`, `.var_at_worst_loss`, all `report -> _`; `Var_backtest.Estimator.t = Historical | Parametric | Parametric_ewma of float` and `Estimator.to_string` (`lib/var_backtest.ml:81–92`); `Var_backtest.Zone.to_string` (`:444`); `Var_backtest.rejected : ?alpha:float -> report -> bool` (`:570`); `Var_backtest.verdict : ?alpha:float -> report -> string` (`:573`); `Var_backtest.to_string : report -> string` (`:581`); `Var_backtest.shape_bounds = (0.05, 20.0)` (`:393`) — the ceiling is quoted as a literal `20.0` below rather than read, because `shape_bounds` is a tuple and reading it here would be a second place the ceiling is named.
- Consumes (Phase 3 Tasks 5–6, exactly as they define them): `Validation_report.Row.t = { label : string; report : Var_backtest.report; hits : int list; burst : (int * int) option; var_series : float array; realised_series : float array }` where `burst` is `(count, start)` from `worst_burst` — `start` the forecast-space index where the `span`-wide window begins (the first index attaining the maximum, minus `span - 1`, floored at 0), so the chart shades `[start, start + span)`; `Validation_report.Series.t = { name : string; description : string; length : int; forecasts : int }`; `Validation_report.Window_facts.t = { name : string; description : string; first : string; last : string; sessions : int; forecasts : int; symbols : string list; worst_day : float; best_day : float; dates : string array; realised : float array }`; `Validation_report.t = { rows : Row.t list; rejected : int; most_severe : Row.t option; series : Series.t list; windows : Window_facts.t list; confidence : float; window : int; alpha : float; ewma_lambda : float }`. The patterns below bind `label`, `report`, `hits`, `burst` and `var_series` by name and close with `_`, so the extra `realised_series` field compiles through untouched. Phase 3's synthetic series are drawn in the order jumps → vol-regime → iid-normal (an OCaml list literal evaluates right to left, and Phase 3 Task 5 reproduces `bin/main.ml`'s order on purpose); `generator_note` below says so.
- Consumes: `Synthetic_book.book : (Types.Symbol.t * Types.Sector.t * float * float) list` (Phase 3); `Types.Symbol.to_string`, `Types.Sector.to_string` (exist — used throughout `lib/server.ml`).
- Produces: `Reports.json_of_estimator`, `Reports.json_of_row : label_key:string -> Validation_report.Row.t -> (string * Yojson.Safe.t) list`, `Reports.json_of_validation : t -> Yojson.Safe.t`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_reports.ml`, before `let suite =`:

  ```ocaml
  let field json key =
    match json with
    | `Assoc fields -> List.Assoc.find fields key ~equal:String.equal
    | _ -> None

  let field_exn json key =
    match field json key with Some v -> v | None -> Alcotest.failf "missing key %S" key

  let list_exn json key =
    match field_exn json key with
    | `List xs -> xs
    | other -> Alcotest.failf "%s is not a list: %s" key (Yojson.Safe.to_string other)

  (* The battery, as the page reads it. Three things are asserted and each is a
     way the figure could be silently wrong rather than visibly missing.

     The row count, because §04 and §06 each draw nine rows and smoke.sh counts
     eighteen against the deployed host. The hit indices, because §04 draws them
     as ticks on a strip [forecasts] wide and an index past the end would fall
     off the axis rather than raise. And the crisis alignment, because
     observation j is scored against dates.(window + j + 1) -- get that wrong by
     one and every timeline on the page is a day out, which nothing on it would
     show. *)
  let test_validation_wire_shape () =
    let r = Lazy.force computed in
    let v = Reports.json_of_validation r in
    let synthetic = field_exn v "synthetic" and crisis = field_exn v "crisis" in
    Alcotest.(check int) "nine synthetic rows" 9 (List.length (list_exn synthetic "rows"));
    Alcotest.(check int) "nine crisis rows" 9 (List.length (list_exn crisis "rows"));
    let config = field_exn v "config" in
    Alcotest.(check int) "three estimators" 3 (List.length (list_exn config "estimators"));
    (* Every hit is inside its own series. *)
    List.iter [ synthetic; crisis ] ~f:(fun side ->
        List.iter (list_exn side "rows") ~f:(fun row ->
            let n =
              match field_exn row "observations" with
              | `Int n -> n
              | other -> Alcotest.failf "observations: %s" (Yojson.Safe.to_string other)
            in
            let previous = ref (-1) in
            List.iter (list_exn row "hits") ~f:(function
              | `Int i ->
                  Alcotest.(check bool)
                    (Printf.sprintf "hit %d is inside 0..%d and after %d" i n !previous)
                    true
                    (i >= 0 && i < n && i > !previous);
                  previous := i
              | other -> Alcotest.failf "hit: %s" (Yojson.Safe.to_string other))));
    (* dates has one entry per session; realised has one per return; a row's var
       series has one per forecast. dates.(window + j + 1) is therefore in range
       for every j, which is the alignment the timelines are drawn on. *)
    List.iter (list_exn crisis "windows") ~f:(fun w ->
        let dates = List.length (list_exn w "dates") in
        let realised = List.length (list_exn w "realised") in
        let sessions =
          match field_exn w "sessions" with `Int n -> n | _ -> Alcotest.fail "sessions"
        in
        let forecasts =
          match field_exn w "forecasts" with `Int n -> n | _ -> Alcotest.fail "forecasts"
        in
        Alcotest.(check int) "one date per session" sessions dates;
        Alcotest.(check int) "one realised return per session but the first"
          (sessions - 1) realised;
        Alcotest.(check bool)
          "dates.(61 + forecasts - 1) is in range" true
          (61 + forecasts - 1 < dates))
  ```

  And in `suite`, after the existing cases:

  ```ocaml
        Alcotest.test_case "the validation wire shape" `Quick test_validation_wire_shape;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -10
  ```
  Expected failure: `Error: Unbound value Reports.json_of_validation`.

- [ ] **Step 3: Minimal implementation.** Append to `lib/reports.ml`:

  ```ocaml
  (* The estimator, in three fields rather than one string.

     [label] is what the CLI prints and what the README's table is keyed by, so
     the computed-vs-quoted line can join on it. [kind] and [lambda] are what a
     chart needs: the page assigns a fixed colour per estimator page-wide, and
     matching on "ewma(0.94)" would break the day the lambda is retuned. *)
  let json_of_estimator (e : Var_backtest.Estimator.t) : Yojson.Safe.t =
    let kind, lambda =
      match e with
      | Var_backtest.Estimator.Historical -> ("historical", `Null)
      | Var_backtest.Estimator.Parametric -> ("parametric", `Null)
      | Var_backtest.Estimator.Parametric_ewma lambda -> ("parametric_ewma", jfloat lambda)
    in
    `Assoc
      [
        ("kind", jstring kind);
        ("lambda", lambda);
        ("label", jstring (Var_backtest.Estimator.to_string e));
      ]

  (* The Weibull shape as a word, decided once here rather than by a threshold
     in JavaScript. b < 1 is clustering, b > 1 is more regular than chance, and
     the band around 1 is where the test has nothing to say -- var_backtest.ml's
     own reading, moved to the wire so the page cannot invent a different one. *)
  let duration_reading (shape : float option) : Yojson.Safe.t =
    match shape with
    | None -> `Null
    | Some b ->
        if Float.( < ) b 0.95 then jstring "clustered"
        else if Float.( > ) b 1.05 then jstring "over-regular"
        else jstring "memoryless"

  (* The upper end of Var_backtest.shape_bounds. A shape sitting on it is the
     SEARCH stopping, not a fit converging, and the README's table says so on
     the jumps rows; a reader who is not told reads b = 20.00 as an
     extraordinarily regular series. *)
  let duration_ceiling = 20.0

  let json_of_row ~(label_key : string) (row : Validation_report.Row.t) :
      (string * Yojson.Safe.t) list =
    let { Validation_report.Row.label; report = rep; hits; _ } = row in
    [
      (label_key, jstring label);
      ("estimator", json_of_estimator (Var_backtest.estimator rep));
      ("observations", `Int (Var_backtest.observations rep));
      ("exceptions", `Int (Var_backtest.exceptions rep));
      ("expected_exceptions", jfloat (Var_backtest.expected_exceptions rep));
      ("observed_rate", jfloat (Var_backtest.observed_rate rep));
      ("expected_rate", jfloat (Var_backtest.expected_rate rep));
      ("kupiec_statistic", jfloat (Var_backtest.kupiec_statistic rep));
      ("kupiec_p", jfloat (Var_backtest.kupiec_p rep));
      ("independence_statistic", jfloat (Var_backtest.independence_statistic rep));
      ("independence_p", jfloat (Var_backtest.independence_p rep));
      ( "conditional_coverage_statistic",
        jfloat (Var_backtest.conditional_coverage_statistic rep) );
      ("conditional_coverage_p", jfloat (Var_backtest.conditional_coverage_p rep));
      ("duration_shape", jopt_float (Var_backtest.duration_shape rep));
      ("duration_statistic", jopt_float (Var_backtest.duration_statistic rep));
      ("duration_p", jopt_float (Var_backtest.duration_p rep));
      ("duration_reading", duration_reading (Var_backtest.duration_shape rep));
      ( "duration_at_ceiling",
        `Bool
          (match Var_backtest.duration_shape rep with
          | None -> false
          | Some b -> Float.( >= ) b (duration_ceiling -. 1e-6)) );
      ("zone", jstring (Var_backtest.Zone.to_string (Var_backtest.zone rep)));
      ( "zone_cumulative_probability",
        jfloat (Var_backtest.zone_cumulative_probability rep) );
      ("worst_loss", jfloat (Var_backtest.worst_loss rep));
      ("var_at_worst_loss", jfloat (Var_backtest.var_at_worst_loss rep));
      ("rejected", `Bool (Var_backtest.rejected rep));
      ("verdict", jstring (Var_backtest.verdict rep));
      (* Indices into the forecast series, not dates. The page owns the date
         axis and already has it from the window; sending 45 integers rather
         than 45 date strings is the same fact at a fifth of the bytes. *)
      ("hits", `List (List.map hits ~f:(fun i -> `Int i)));
    ]

  (* The most severe rejection, IN FULL, as the engine's own text.

     Var_backtest.to_string is what `make backtest` prints, verbatim, so the
     page's block and the terminal's block are the same string produced by the
     same function. A page that reformatted it would be a second renderer of a
     report whose whole point is that it is the engine speaking. *)
  let json_of_most_severe ~(label_key : string)
      (row : Validation_report.Row.t option) : Yojson.Safe.t =
    match row with
    | None -> `Null
    | Some { Validation_report.Row.label; report = rep; _ } ->
        `Assoc
          [
            (label_key, jstring label);
            ("estimator", jstring (Var_backtest.Estimator.to_string (Var_backtest.estimator rep)));
            ("text", jstring (Var_backtest.to_string rep));
          ]

  let generator_note =
    "one stream from Random.State.make [|2026_08_24|] by Box-Muller, consumed \
     jumps, then vol-regime, then iid-normal -- the reverse of the table's order, \
     because bin/main.ml built the three as an OCaml list literal, which evaluates \
     right to left, and Validation_report reproduces that draw order on purpose; \
     the jumps series does not draw on a jump day, so no single series reproduces \
     out of that order"

  let crisis_source =
    "Yahoo Finance, adjusted daily closes, fetched by tools/fetch_crisis_data.py, \
     committed under docs/crisis and embedded in the binary at build"

  let crisis_book_note =
    "weights come from today's synthetic marks held constant -- what today's book \
     would have done -- not from crisis-era prices; NVDA's 2009 adjusted close is \
     about $0.41 and that is not a bug"

  let json_of_crisis_book () : Yojson.Safe.t =
    let positions =
      List.map Synthetic_book.book ~f:(fun (symbol, sector, mark, qty) ->
          `Assoc
            [
              ("symbol", jstring (Types.Symbol.to_string symbol));
              ("sector", jstring (Types.Sector.to_string sector));
              ("mark", jfloat mark);
              ("qty", jfloat qty);
            ])
    in
    let gross =
      List.fold Synthetic_book.book ~init:0.0 ~f:(fun acc (_, _, mark, qty) ->
          acc +. Float.abs (mark *. qty))
    in
    `Assoc
      [
        ("positions", `List positions);
        ("gross_notional", jfloat gross);
        ("note", jstring crisis_book_note);
      ]

  let json_of_validation (t : t) : Yojson.Safe.t =
    let s = t.synthetic and c = t.crisis in
    let config =
      `Assoc
        [
          ("confidence", jfloat s.Validation_report.confidence);
          ("window", `Int s.Validation_report.window);
          ("alpha", jfloat s.Validation_report.alpha);
          ("ewma_lambda", jfloat s.Validation_report.ewma_lambda);
          ( "estimators",
            jlist
              (fun (row : Validation_report.Row.t) ->
                json_of_estimator (Var_backtest.estimator row.Validation_report.Row.report))
              (* The first three rows are the three estimators against the first
                 series, in the order the CLI runs them. Reading the list off the
                 rows rather than restating it is what keeps the legend and the
                 table from ever disagreeing. *)
              (List.take s.Validation_report.rows 3) );
        ]
    in
    let synthetic =
      `Assoc
        [
          ( "generator",
            `Assoc
              [
                ("seed", `Int 2026_08_24);
                ("method", jstring "Box-Muller");
                ("draw_order", jstring generator_note);
              ] );
          ( "series",
            jlist
              (fun (x : Validation_report.Series.t) ->
                `Assoc
                  [
                    ("name", jstring x.Validation_report.Series.name);
                    ("description", jstring x.Validation_report.Series.description);
                    ("length", `Int x.Validation_report.Series.length);
                    ("forecasts", `Int x.Validation_report.Series.forecasts);
                  ])
              s.Validation_report.series );
          ( "rows",
            jlist
              (fun row -> `Assoc (json_of_row ~label_key:"series" row))
              s.Validation_report.rows );
          ("rejected", `Int s.Validation_report.rejected);
          ( "most_severe",
            json_of_most_severe ~label_key:"series" s.Validation_report.most_severe );
        ]
    in
    let crisis =
      `Assoc
        [
          ("source", jstring crisis_source);
          ("book", json_of_crisis_book ());
          ( "windows",
            jlist
              (fun (w : Validation_report.Window_facts.t) ->
                let open Validation_report.Window_facts in
                `Assoc
                  [
                    ("name", jstring w.name);
                    ("description", jstring w.description);
                    ("first", jstring w.first);
                    ("last", jstring w.last);
                    ("sessions", `Int w.sessions);
                    ("forecasts", `Int w.forecasts);
                    ("symbols", jlist jstring w.symbols);
                    ("worst_day", jfloat w.worst_day);
                    ("best_day", jfloat w.best_day);
                    ("dates", `List (Array.to_list (Array.map w.dates ~f:jstring)));
                    ("realised", `List (Array.to_list (Array.map w.realised ~f:jfloat6)));
                  ])
              c.Validation_report.windows );
          ( "rows",
            jlist
              (fun (row : Validation_report.Row.t) ->
                let burst_count, burst_start =
                  match row.Validation_report.Row.burst with
                  | None -> (0, 0)
                  | Some (count, start) -> (count, start)
                in
                `Assoc
                  (json_of_row ~label_key:"window" row
                  @ [
                      ("burst", `Int burst_count);
                      (* The START of the worst window, and not only its count.
                         §06 shades that span. Without the index the client would
                         have to re-find it from [hits], which is a second
                         implementation of worst_burst in JavaScript -- exactly
                         the rule invariant 2 exists to stop. *)
                      ("burst_start", `Int burst_start);
                      ("burst_span", `Int burst_span);
                      ( "burst_expected",
                        jfloat
                          (float_of_int burst_span
                          *. (1.0 -. c.Validation_report.confidence)) );
                      ( "var",
                        `List
                          (Array.to_list
                             (Array.map row.Validation_report.Row.var_series ~f:jfloat6))
                      );
                    ]))
              c.Validation_report.rows );
          ("rejected", `Int c.Validation_report.rejected);
          ( "most_severe",
            json_of_most_severe ~label_key:"window" c.Validation_report.most_severe );
        ]
    in
    `Assoc [ ("config", config); ("synthetic", synthetic); ("crisis", crisis) ]
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 6 tests run.`

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml && git commit -m "reports: the battery on the wire with hit indices and the burst's start, because re-finding either on the client would be a second implementation"
  ```

---

### Task 4: The options encoder, and the whole report as one cached string

**Files:**
- Modify: `lib/reports.ml` (append after `json_of_validation`)
- Modify: `test/test_reports.ml` (append one case)

**Interfaces:**
- Consumes (Phase 3 Task 7, exactly as it defines them): `Options_walk.Surface.t = { formula : string; floor : float; at_setup : float }`; `Options_walk.Setup.t = { underlying : string; id : string; strike : float; right : string; expiry_days : float; contracts : float; multiplier : float; spot : float; rate : float; implied_vol : float }`; `Options_walk.State.t = { label : string; delta_equivalent : float; gamma : float; vega : float }`; `Options_walk.Leg.t = { id : string; days : float; contracts : float }`; `Options_walk.Calendar.t = { far : Leg.t; near : Leg.t; portfolio_vega : float; portfolio_gamma : float; buckets : (string * float) list }`; `Options_walk.t = { surface : Surface.t; setup : Setup.t; states : State.t list; hedge_shares : float; breaches : Types.Breach.t list; clock_advance_days : float; calendar : Calendar.t }`, with `states` the four in CLI order — `options only`, `+ the hedge`, `today`, `+20 days` — which `List.split_n` below cuts into `walk_hedge` (the first two) and `walk_clock` (the last two); `breaches` in utilisation-descending order; `Calendar.buckets` carrying all six `Tenor_bucket.ordered` slots with `0.0` where empty. Phase 3 asserts the post-hedge `delta_equivalent` to `1e-6`, not bit-exact (`(a / b) * b` is not always `a` in IEEE arithmetic), and the page's prose in Task 12 says the same.
- Consumes: `Limits.to_string : Types.Breach.t -> string` (exists — `bin/main.ml:1197` calls it); `Server.json_of_breach : Types.Breach.t -> Yojson.Safe.t` (exists — `lib/server.ml:88–110`), injected as `~breach_json` rather than called, because server.ml depends on this module.
- Produces: `Reports.json_of_options : breach_json:(Types.Breach.t -> Yojson.Safe.t) -> t -> Yojson.Safe.t` and `Reports.to_json_string : breach_json:(Types.Breach.t -> Yojson.Safe.t) -> t -> string`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_reports.ml`, before `let suite =`:

  ```ocaml
  (* The whole payload, once, as the browser gets it.

     The round trip is the point: Yojson will happily EMIT things it cannot read
     back -- a bare NaN token among them -- so serialising and reparsing is the
     only honest check that the page will not lose every number to one bad
     float. The substring check is the same assertion said a second way, because
     a NaN nested inside an array would still parse in some readers and must not
     be on the wire at all. *)
  let test_the_whole_report_parses_back () =
    let r = Lazy.force computed in
    let text = Reports.to_json_string ~breach_json:Ohcamel.Server.json_of_breach r in
    Alcotest.(check bool)
      "no NaN token anywhere in the payload" false
      (String.is_substring text ~substring:"NaN");
    Alcotest.(check bool)
      "no Infinity token either" false
      (String.is_substring text ~substring:"Infinity");
    match Option.try_with (fun () -> Yojson.Safe.from_string text) with
    | None -> Alcotest.fail "the report did not parse back as JSON"
    | Some json ->
        List.iter
          [ "computed_at"; "computed_in_ms"; "platform"; "scaling"; "validation"; "options" ]
          ~f:(fun key -> ignore (field_exn json key : Yojson.Safe.t));
        let options = field_exn json "options" in
        Alcotest.(check string)
          "the label says SYNTHETIC and says it first" "SYNTHETIC"
          (match field_exn options "label" with `String s -> s | _ -> "?");
        Alcotest.(check int) "two rows in the hedge walk" 2
          (List.length (list_exn options "walk_hedge"));
        Alcotest.(check int) "two rows in the clock walk" 2
          (List.length (list_exn options "walk_clock"));
        (* Six fixed tenor slots, so the empty ones are visible as empty. *)
        Alcotest.(check int) "six tenor buckets" 6
          (List.length (list_exn (field_exn options "calendar") "buckets"));
        (* Each breach carries the engine's own sentence beside the numbers, so
           §03's LIMITS block is Limits.to_string and not a re-rendering of it. *)
        List.iter (list_exn options "limits") ~f:(fun l ->
            ignore (field_exn l "text" : Yojson.Safe.t);
            ignore (field_exn l "unit" : Yojson.Safe.t))
  ```

  And in `suite`:

  ```ocaml
        Alcotest.test_case "the whole report parses back as JSON" `Quick
          test_the_whole_report_parses_back;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -10
  ```
  Expected failure: `Error: Unbound value Reports.to_json_string`.

- [ ] **Step 3: Minimal implementation.** Append to `lib/reports.ml`:

  ```ocaml
  (* The options figure, and the word SYNTHETIC in the first field of it.

     Neither deployed book holds an option and there is no chain source, so the
     surface here is invented -- a smile plus a skew, in the shape a real equity
     surface has had since 1987, precisely so that a FLAT one would misprice the
     hedge this figure exists to demonstrate. An invented surface presented as a
     real one produces Greeks indistinguishable from real ones, which is the one
     failure the credentials path is arranged against; so the label travels in
     the payload rather than being added by a renderer that could forget. *)
  let json_of_options ~(breach_json : Types.Breach.t -> Yojson.Safe.t) (t : t) :
      Yojson.Safe.t =
    let o = t.options in
    let state (s : Options_walk.State.t) =
      let open Options_walk.State in
      `Assoc
        [
          ("label", jstring s.label);
          ("delta_equivalent", jfloat s.delta_equivalent);
          ("gamma", jfloat s.gamma);
          (* Vega crosses in the ENGINE's unit, dollars per 1.00 of annualised
             vol, exactly as /api/snapshot carries it. The desk's per-vol-point
             figure is the /100 and it is a DISPLAY decision: a wire format that
             applied it silently would be a hundred-fold discrepancy between the
             API and the limit thresholds written against it. The caption says
             which unit it is showing. *)
          ("vega", jfloat s.vega);
        ]
    in
    let hedge, clock = List.split_n o.Options_walk.states 2 in
    let leg (l : Options_walk.Leg.t) =
      let open Options_walk.Leg in
      `Assoc
        [ ("id", jstring l.id); ("days", jfloat l.days); ("contracts", jfloat l.contracts) ]
    in
    let c = o.Options_walk.calendar in
    `Assoc
      [
        ("label", jstring "SYNTHETIC");
        ( "surface",
          let open Options_walk.Surface in
          `Assoc
            [
              ("formula", jstring o.Options_walk.surface.formula);
              ("floor", jfloat o.Options_walk.surface.floor);
              ("at_setup", jfloat o.Options_walk.surface.at_setup);
            ] );
        ( "setup",
          let open Options_walk.Setup in
          let s = o.Options_walk.setup in
          `Assoc
            [
              ("underlying", jstring s.underlying);
              ("id", jstring s.id);
              ("strike", jfloat s.strike);
              ("right", jstring s.right);
              ("expiry_days", jfloat s.expiry_days);
              ("contracts", jfloat s.contracts);
              ("multiplier", jfloat s.multiplier);
              ("spot", jfloat s.spot);
              ("rate", jfloat s.rate);
              ("implied_vol", jfloat s.implied_vol);
            ] );
        ("walk_hedge", jlist state hedge);
        ("hedge_shares", jfloat o.Options_walk.hedge_shares);
        ( "limits",
          jlist
            (fun breach ->
              (* The breach record the ledger already knows how to read, plus the
                 engine's own sentence. §03 prints that sentence in the ledger's
                 .lim form; re-composing it from the fields would be a second
                 renderer of a line whose point is that it is the engine
                 speaking. *)
              match breach_json breach with
              | `Assoc fields ->
                  `Assoc (fields @ [ ("text", jstring (Limits.to_string breach)) ])
              | other -> other)
            o.Options_walk.breaches );
        ("walk_clock", jlist state clock);
        ("clock_advance_days", jfloat o.Options_walk.clock_advance_days);
        ( "calendar",
          let open Options_walk.Calendar in
          `Assoc
            [
              ("far", leg c.far);
              ("near", leg c.near);
              ("portfolio_vega", jfloat c.portfolio_vega);
              ("portfolio_gamma", jfloat c.portfolio_gamma);
              (* All six slots, including the four that are empty. The point of
                 the figure is that a parallel-shift total of zero hides two
                 opposite bars; slots that vanish when unoccupied would hide the
                 hiding. *)
              ( "buckets",
                jlist
                  (fun (bucket, vega) ->
                    `Assoc [ ("bucket", jstring bucket); ("vega", jfloat vega) ])
                  c.buckets );
            ] );
      ]

  (* One string, encoded once, held for the life of the process.

     [breach_json] is passed in rather than called: server.ml owns the breach
     encoder and server.ml depends on this module, so injecting it is what keeps
     one implementation of a breach on the wire without a dependency cycle. *)
  let to_json_string ~(breach_json : Types.Breach.t -> Yojson.Safe.t) (t : t) : string =
    Yojson.Safe.to_string
      (`Assoc
         [
           ("computed_at", jstring (Time_ns.to_string_utc t.computed_at));
           ("computed_in_ms", jfloat t.computed_in_ms);
           ("platform", json_of_platform ());
           ("scaling", json_of_scaling t);
           ("validation", json_of_validation t);
           ("options", json_of_options ~breach_json t);
         ])
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 7 tests run.`

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml && git commit -m "reports: the options walk on the wire with SYNTHETIC in its first field, because a label a renderer adds is a label a renderer can forget"
  ```

---

### Task 5: The GARCH study on a second domain

**Files:**
- Modify: `lib/reports.ml` (append a `Garch` module after `to_json_string`)
- Modify: `test/test_reports.ml` (append two cases)

**Interfaces:**
- Consumes (Phase 3 Task 8, exactly as it defines them): `Garch_study.run : ?progress:(int -> unit) -> seed:int -> replications:int -> sample_sizes:int list -> truth:Vol_estimators.Garch11.t -> burn_in:int -> unit -> Garch_study.result` where `progress` is called after every fit with the running count (1 … replications × |sample_sizes|); the CLI's constants as values — `Garch_study.default_seed = 2026_08_25`, `default_replications = 30`, `default_sample_sizes = [ 60; 125; 250; 500; 1000; 2000 ]`, `default_burn_in = 500`, `default_truth : Vol_estimators.Garch11.t` (ω 4e-6, α 0.10, β 0.88) — which `default_params` and `default_truth` below alias rather than restate, so `make garch` and the page cannot drift apart; and `Garch_study.result = { seed : int; replications : int; sample_sizes : int list; burn_in : int; truth : Vol_estimators.Garch11.t; rows : Garch_study.Row.t list }` (only `rows` is read below) with `Garch_study.Row.t = { n : int; alpha_mean : float; alpha_sd : float; beta_mean : float; beta_sd : float; persistence_mean : float; persistence_sd : float }`. The prompt names the truth type `Garch11.params`; in this repository it is `Vol_estimators.Garch11.t` (`lib/vol_estimators.ml:236–244`, a record of `omega`, `alpha`, `beta` with `[@@deriving sexp_of, fields ~getters]`), which is what is used below.
- Consumes: `Vol_estimators.Garch11.persistence`, `.shock_half_life`, `.omega`, `.alpha`, `.beta` (`lib/vol_estimators.ml:244–265`); `Synthetic_book.return_window : int` (Phase 3, = 60).
- Consumes (verified against the installed compiler): `Domain.spawn : (unit -> 'a) -> 'a Domain.t` and `Domain.join : 'a Domain.t -> 'a` (`_opam/lib/ocaml/domain.mli:33` and `:40`); `Atomic.make : 'a -> 'a Atomic.t`, `Atomic.get`, `Atomic.set` (`_opam/lib/ocaml/atomic.mli:30, 46, 49`). Neither `Core` nor `Base` defines a module of either name, so `open Core` leaves both resolving to the stdlib's — verified by compiling `open Core; let a = Atomic.make 0 in Domain.join (Domain.spawn (fun () -> Atomic.get a + 1))` against this switch's `core`.
- Consumes: `Clock_ns.every : ?start:unit Deferred.t -> ?stop:unit Deferred.t -> ?continue_on_error:bool -> Time_ns.Span.t -> (unit -> unit) -> unit` (`_opam/lib/async_kernel/clock_intf.ml:243–249`, instantiated at `Time_ns.Span`); `Ivar.create`, `Ivar.read`, `Ivar.fill_if_empty` (used already in `lib/server.ml`).
- Produces: `Reports.Garch.params`, `Reports.Garch.default_params`, `Reports.Garch.default_truth`, `Reports.Garch.t`, `Reports.Garch.create : ?params:params -> ?truth:Vol_estimators.Garch11.t -> unit -> t`, `Reports.Garch.run_blocking : t -> unit`, `Reports.Garch.start : t -> unit`, `Reports.Garch.status : t -> string`, `Reports.Garch.done_count : t -> int`, `Reports.Garch.total : t -> int`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_reports.ml`, before `let suite =`:

  ```ocaml
  (* A study small enough to run twice in a test, and the two things about the
     second domain that could be wrong.

     Determinism first: Garch_study.run takes its own seed and builds its own
     Random.State, so nothing about it depends on which domain it runs on. If
     that were false -- if it reached a shared generator -- the spawned rows
     would differ from the direct ones and the page would publish numbers that
     changed with the scheduler. This is the assertion that makes the whole
     second-domain design safe to ship.

     The full 180-fit study is not run here. `make garch` runs it in CI. *)
  let tiny_sizes = [ 60; 125 ]
  let tiny_replications = 2

  let tiny_run ?progress () =
    Ohcamel.Garch_study.run ?progress ~seed:2026_09_02 ~replications:tiny_replications
      ~sample_sizes:tiny_sizes ~truth:Ohcamel.Reports.Garch.default_truth ~burn_in:100 ()

  let rows_of (r : Ohcamel.Garch_study.result) =
    List.map r.Ohcamel.Garch_study.rows ~f:(fun (row : Ohcamel.Garch_study.Row.t) ->
        ( row.Ohcamel.Garch_study.Row.n,
          Printf.sprintf "%.12f" row.Ohcamel.Garch_study.Row.persistence_mean ))

  let test_the_domain_path_agrees_with_the_direct_one () =
    let direct = tiny_run () in
    let spawned = Domain.join (Domain.spawn (fun () -> tiny_run ())) in
    Alcotest.(check (list (pair int string)))
      "the same rows on either domain" (rows_of direct) (rows_of spawned)

  (* [progress] is what the page's "computing k of 180 fits" line reads, through
     an Atomic the spawned domain writes and the poll on the main domain reads.
     It has to be called once per fit and it has to be monotone, or the line
     goes backwards in front of a reader. *)
  let test_progress_is_called_once_per_fit_and_is_monotone () =
    let seen = ref [] in
    let _ = tiny_run ~progress:(fun k -> seen := k :: !seen) () in
    let seen = List.rev !seen in
    Alcotest.(check int)
      "one call per fit" (tiny_replications * List.length tiny_sizes)
      (List.length seen);
    Alcotest.(check (list int))
      "counting up from one"
      (List.init (tiny_replications * List.length tiny_sizes) ~f:(fun i -> i + 1))
      seen
  ```

  And in `suite`:

  ```ocaml
        Alcotest.test_case "the domain path agrees with the direct one" `Quick
          test_the_domain_path_agrees_with_the_direct_one;
        Alcotest.test_case "progress is once per fit and monotone" `Quick
          test_progress_is_called_once_per_fit_and_is_monotone;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -10
  ```
  Expected failure: `Error: Unbound module Reports.Garch` (reported as `Unbound value Ohcamel.Reports.Garch.default_truth`).

- [ ] **Step 3: Minimal implementation.** Append to `lib/reports.ml`:

  ```ocaml
  (* ------------------------------------------------------------------------ *)
  (* The GARCH study, on a second domain                                       *)
  (* ------------------------------------------------------------------------ *)

  (* Five to fifteen seconds of pure float arithmetic, and it must not be paid
     anywhere it can be noticed.

     Not in a request handler, because a route that costs ten seconds is a
     denial of service with a URL. Not before the socket binds, because the
     healthcheck's start period is twenty seconds and a deploy that fails its
     own healthcheck is a rollback. Not on a systhread, because OCaml's threads
     TIME-SLICE pure compute rather than parallelising it, which would take the
     ten seconds out of the scheduler that is meant to be serving frames.

     So: a second domain. Garch11.fit is pure OCaml over float arrays -- Owl
     appears in vol_estimators.ml only inside the EWMA covariance -- and the
     droplet has two vCPUs, so the study runs beside the engine rather than
     instead of it.

     THE RULE FOR THE SPAWNED DOMAIN: it touches nothing of Async's. The
     scheduler runs on the main domain only and its data structures are not
     shared. The only two channels out of the spawned closure are [progress],
     an Atomic, and the return value, which Domain.join transfers. *)
  module Garch = struct
    type params = {
      seed : int;
      replications : int;
      sample_sizes : int list;
      burn_in : int;
      coarse_steps : int;
    }

    (* Garch_study's own constants -- the ones `make garch` runs -- aliased
       rather than restated, so the page and the CLI cannot drift apart by one
       edited literal. coarse_steps is Garch11.fit's default (40, its
       `?(coarse_steps = 40)`), named here because the route publishes it. *)
    let default_params =
      {
        seed = Garch_study.default_seed;
        replications = Garch_study.default_replications;
        sample_sizes = Garch_study.default_sample_sizes;
        burn_in = Garch_study.default_burn_in;
        coarse_steps = 40;
      }

    (* Textbook daily-equity parameters (omega 4e-6, alpha 0.10, beta 0.88):
       persistence 0.98 is a shock half-life of about 34 days, which is what an
       equity index actually looks like. The record lives in Garch_study. *)
    let default_truth = Garch_study.default_truth

    type t = {
      params : params;
      truth : Vol_estimators.Garch11.t;
      total : int;
      (* Written by the spawned domain, read by the poll on the main one. An
         Atomic and not a ref: a ref written across domains has no defined
         visibility, and the visible symptom would be a progress line that
         appeared frozen on one core and correct on another. *)
      progress : int Atomic.t;
      mutable domain : Garch_study.result Or_error.t Domain.t option;
      mutable started_at : Types.Time.t option;
      mutable finished : (Garch_study.result Or_error.t * float) option;
      (* Encoded once when the study lands. The route is polled every five
         seconds by every open tab; re-encoding six rows on each of those would
         be free and pointless, and the cached string is what makes "encode
         once" a fact rather than an intention. *)
      mutable cache : string option;
    }

    let create ?(params = default_params) ?(truth = default_truth) () =
      {
        params;
        truth;
        total = params.replications * List.length params.sample_sizes;
        progress = Atomic.make 0;
        domain = None;
        started_at = None;
        finished = None;
        cache = None;
      }

    let total (t : t) = t.total
    let done_count (t : t) = Int.min (Atomic.get t.progress) t.total
    let status (t : t) = match t.finished with Some _ -> "done" | None -> "computing"

    (* The study, and the only place it is called.

       [start] runs exactly this on a spawned domain and [run_blocking] runs it
       on the calling one, so a hermetic test reaches the same body the deployed
       host does. It reads only immutable fields of [t] plus the Atomic, which
       is what makes it safe to run while the main domain writes t.domain. *)
    let run_body (t : t) : Garch_study.result Or_error.t =
      let p = t.params in
      let outcome =
        Or_error.try_with (fun () ->
            Garch_study.run
              ~progress:(fun k -> Atomic.set t.progress k)
              ~seed:p.seed ~replications:p.replications ~sample_sizes:p.sample_sizes
              ~truth:t.truth ~burn_in:p.burn_in ())
      in
      (* Set the counter to [total] here rather than trusting the last progress
         callback. It is what makes a study that RAISED still reachable: the poll
         on the main domain joins when the counter reaches total, so without this
         a failed study would leave the route saying "computing" forever -- and
         a cannot-evaluate rendering as still-working is the one reading this
         project treats as worse than a stated failure. *)
      Atomic.set t.progress t.total;
      outcome

    let finish (t : t) ~(started : Types.Time.t) (outcome : Garch_study.result Or_error.t)
        =
      t.finished <-
        Some (outcome, Types.Time.Span.to_ms (Types.Time.diff (Types.Time.now ()) started));
      t.domain <- None;
      t.cache <- None

    let run_blocking (t : t) =
      let started = Types.Time.now () in
      t.started_at <- Some started;
      finish t ~started (run_body t)

    (* One second, and it stops itself.

       This is a genuine timer and it is the only one this module owns. It is not
       asking the graph whether anything changed -- it is asking a counter on
       another core whether a fixed amount of arithmetic has finished, which is
       not a question an Ivar can answer, because the domain that would fill it
       must not touch Async. The poll ends the moment it joins. *)
    let watch (t : t) ~(started : Types.Time.t) =
      let stop = Ivar.create () in
      Clock_ns.every ~stop:(Ivar.read stop) (Time_ns.Span.of_sec 1.0) (fun () ->
          match t.domain with
          | None -> Ivar.fill_if_empty stop ()
          | Some d ->
              if Atomic.get t.progress >= t.total then begin
                (* Returns immediately: the counter only reaches total on the
                   spawned domain's last statement. *)
                let outcome = Domain.join d in
                finish t ~started outcome;
                Ivar.fill_if_empty stop ()
              end)

    let start (t : t) =
      let started = Types.Time.now () in
      t.started_at <- Some started;
      t.domain <- Some (Domain.spawn (fun () -> run_body t));
      watch t ~started
  end
  ```

  `lib/reports.ml` now needs Async for `Clock_ns` and `Ivar`. Add the open immediately under the existing `open Core` at the head of the file, so the first two lines of code read:

  ```ocaml
  open Core
  open Async
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 9 tests run.` The two new cases take roughly a second between them (eight small fits, run twice).

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml && git commit -m "reports: the GARCH study on a second domain, because a systhread would time-slice ten seconds of pure compute out of the scheduler"
  ```

---

### Task 6: `/api/reports/garch`'s encoder, in both states

**Files:**
- Modify: `lib/reports.ml` (append inside the `Garch` module, after `start`)
- Modify: `test/test_reports.ml` (append one case)

**Interfaces:**
- Consumes: `Reports.Garch.t`, `Reports.Garch.run_blocking`, `Reports.Garch.status`, `Reports.Garch.done_count`, `Reports.Garch.total` (Task 5); `Reports.jfloat`, `jopt_float`, `jstring`, `jlist` (Task 2); `Synthetic_book.return_window : int` (Phase 3).
- Produces: `Reports.Garch.json : t -> Yojson.Safe.t` and `Reports.Garch.json_string : t -> string`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_reports.ml`, before `let suite =`:

  ```ocaml
  (* The route answers 200 in every state, and the two states say different
     things without either one looking like the other.

     A study still running must not render as an empty table -- an empty figure
     and a finished one that found nothing are the same picture -- so the
     computing state carries [done] and [of] and the page prints "computing k of
     N fits, on a second domain" instead of a chart. *)
  let test_garch_encodes_computing_then_done () =
    let g =
      Ohcamel.Reports.Garch.create
        ~params:
          {
            Ohcamel.Reports.Garch.seed = 2026_09_02;
            replications = tiny_replications;
            sample_sizes = tiny_sizes;
            burn_in = 100;
            coarse_steps = 8;
          }
        ()
    in
    let computing = Ohcamel.Reports.Garch.json g in
    Alcotest.(check string)
      "computing before it starts" "computing"
      (match field_exn computing "status" with `String s -> s | _ -> "?");
    Alcotest.(check int)
      "nothing done yet" 0
      (match field_exn computing "done" with `Int n -> n | _ -> -1);
    Alcotest.(check int)
      "and it says how many there will be"
      (tiny_replications * List.length tiny_sizes)
      (match field_exn computing "of" with `Int n -> n | _ -> -1);
    Alcotest.(check bool)
      "no rows and no wall time while computing" true
      (List.is_empty (list_exn computing "rows")
      && Poly.equal (field_exn computing "computed_in_ms") `Null);
    (* The truth record travels so the chart's "true" hairline is the engine's
       number rather than a constant typed into JavaScript. *)
    let truth = field_exn computing "truth" in
    Alcotest.(check (float 1e-9))
      "persistence 0.98" 0.98
      (match field_exn truth "persistence" with `Float f -> f | _ -> Float.nan);
    Ohcamel.Reports.Garch.run_blocking g;
    let done_ = Ohcamel.Reports.Garch.json g in
    Alcotest.(check string)
      "done afterwards" "done"
      (match field_exn done_ "status" with `String s -> s | _ -> "?");
    Alcotest.(check int)
      "one row per sample size" (List.length tiny_sizes) (List.length (list_exn done_ "rows"));
    Alcotest.(check bool)
      "and a wall time" true
      (match field_exn done_ "computed_in_ms" with `Float _ -> true | _ -> false);
    (* n = 60 is the engine's own window and is the row the verdict paragraph is
       bound to, so it is named on the wire rather than found by a client that
       would have to know the window. *)
    Alcotest.(check int)
      "the verdict row is the engine's window" 60
      (match field_exn (field_exn done_ "verdict_row") "n" with `Int n -> n | _ -> -1);
    Alcotest.(check int)
      "the window is stated beside it" 60
      (match field_exn done_ "window" with `Int n -> n | _ -> -1)
  ```

  And in `suite`:

  ```ocaml
        Alcotest.test_case "garch encodes computing then done" `Quick
          test_garch_encodes_computing_then_done;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -10
  ```
  Expected failure: `Error: Unbound value Ohcamel.Reports.Garch.json`.

- [ ] **Step 3: Minimal implementation.** Append inside the `Garch` module in `lib/reports.ml`, after `start`:

  ```ocaml
    let json_of_study_row (r : Garch_study.Row.t) : Yojson.Safe.t =
      let open Garch_study.Row in
      `Assoc
        [
          ("n", `Int r.n);
          ("alpha_mean", jfloat r.alpha_mean);
          ("alpha_sd", jfloat r.alpha_sd);
          ("beta_mean", jfloat r.beta_mean);
          ("beta_sd", jfloat r.beta_sd);
          ("persistence_mean", jfloat r.persistence_mean);
          ("persistence_sd", jfloat r.persistence_sd);
        ]

    let json (t : t) : Yojson.Safe.t =
      let g = Vol_estimators.Garch11.persistence t.truth in
      let base =
        [
          ("status", jstring (status t));
          ("done", `Int (done_count t));
          ("of", `Int t.total);
          ( "params",
            `Assoc
              [
                ("seed", `Int t.params.seed);
                ("replications", `Int t.params.replications);
                ( "sample_sizes",
                  `List (List.map t.params.sample_sizes ~f:(fun n -> `Int n)) );
                ("burn_in", `Int t.params.burn_in);
                ("coarse_steps", `Int t.params.coarse_steps);
              ] );
          ( "truth",
            `Assoc
              [
                ("omega", jfloat (Vol_estimators.Garch11.omega t.truth));
                ("alpha", jfloat (Vol_estimators.Garch11.alpha t.truth));
                ("beta", jfloat (Vol_estimators.Garch11.beta t.truth));
                ("persistence", jfloat g);
                ("half_life", jopt_float (Vol_estimators.Garch11.shock_half_life t.truth));
              ] );
          (* The engine's return window, drawn on the chart as a vertical
             hairline. It is the whole verdict: the whisker at this n spans most
             of the axis. *)
          ("window", `Int Synthetic_book.return_window);
        ]
      in
      match t.finished with
      | None ->
          `Assoc
            (base
            @ [
                ("rows", `List []);
                ("verdict_row", `Null);
                ("computed_in_ms", `Null);
                ("error", `Null);
              ])
      | Some (Error e, ms) ->
          (* A study that raised is DONE and is not a table. Saying so is the
             only honest reading available: leaving it at "computing" would draw
             a progress line that never advances, and dropping the state would
             draw an empty chart that looks like a finding. *)
          `Assoc
            (base
            @ [
                ("rows", `List []);
                ("verdict_row", `Null);
                ("computed_in_ms", jfloat ms);
                ("error", jstring (Error.to_string_hum e));
              ])
      | Some (Ok result, ms) ->
          let rows = result.Garch_study.rows in
          let verdict =
            List.find rows ~f:(fun (r : Garch_study.Row.t) ->
                r.Garch_study.Row.n = Synthetic_book.return_window)
          in
          `Assoc
            (base
            @ [
                ("rows", jlist json_of_study_row rows);
                ( "verdict_row",
                  match verdict with None -> `Null | Some r -> json_of_study_row r );
                ("computed_in_ms", jfloat ms);
                ("error", `Null);
              ])

    let json_string (t : t) : string =
      match (t.finished, t.cache) with
      | Some _, Some cached -> cached
      | Some _, None ->
          let s = Yojson.Safe.to_string (json t) in
          t.cache <- Some s;
          s
      (* While computing, [done] changes under the caller, so there is nothing
         to cache. Ten fields is cheaper to build than a cache invalidation rule
         would be to get right. *)
      | None, _ -> Yojson.Safe.to_string (json t)
  ```

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -8
  ```
  Expected: `Test Successful in ...s. 10 tests run.`

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml test/test_reports.ml && git commit -m "reports: garch answers 200 while it is still computing, because an empty table and a finished one that found nothing are the same picture"
  ```

---

### Task 7: The two routes, and the server holding what it serves

**Files:**
- Modify: `lib/server.ml` (the `t` record, `create`, the `routes` table, `json_of_ops`'s `reports` slot)
- Modify: `bin/main.ml` (in `run_demo` and `run_live`: Phase 4's probe block, from its marker comment through its `printf`, replaced by `Reports.compute`; the two `Server.create` call sites gain `~reports ~garch`)
- Modify: `test/test_server.ml` (`with_server` and `with_logged_server` pass the two new arguments; `test_ops_shape`'s `reports` block; append one case)
- Modify: `deploy/smoke.sh` (the `EXPECTED_ROUTES=` line — the routes table's shadow, extended in the same commit as the table)

**Interfaces:**
- Consumes: `Reports.compute : ?sizes:int list -> ?ticks:int -> unit -> Reports.t` (Task 1); `Reports.to_json_string : breach_json:(Types.Breach.t -> Yojson.Safe.t) -> Reports.t -> string` (Task 4); `Reports.Garch.create : ?params:params -> ?truth:Vol_estimators.Garch11.t -> unit -> Reports.Garch.t`, `Reports.Garch.json_string`, `Reports.Garch.status`, `Reports.Garch.done_count`, `Reports.Garch.total` (Tasks 5–6).
- Consumes (Phase 2): `Server.routes : (string * string * handler) list` — path, one-line purpose, `handler = t -> Cohttp_async.Server.response Deferred.t` — from which `handle` dispatches and the 404 body's `routes` list is generated (Task 12); `json_headers : mode:mode -> Cohttp.Header.t`, called as `json_headers ~mode:t.mode` at every JSON route (Task 9; it adds `Access-Control-Allow-Origin: *` in demo mode only); `deploy/smoke.sh`'s `EXPECTED_ROUTES` string (Task 15) and `test_ops_shape`'s `reports` assertions (Task 11).
- Consumes (Phase 4 Task 6): `Server.create … ?quiet ?recompute_log ~mode ~graph ~factor ()`; `bin/main.ml`'s probe block in `run_demo` and `run_live`, marked `(* Phase 5 replaces this with Reports.compute, which runs the same probe. *)`, and the `let log = Recompute_log.create () in` that follows it; `test/test_server.ml`'s `with_server ?mode ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~f ()` and `with_logged_server ~f ()`. (Phase 4 Task 9): `EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/ops"`.
- Produces: `Server.t` fields `reports : string` and `garch : Reports.Garch.t`; `Server.create` gains `~reports:string` and `~garch:Reports.Garch.t`; routes `GET /api/reports` and `GET /api/reports/garch`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_server.ml`, before `let suite =`:

  ```ocaml
  (* The 404 body lists the routes, and it is generated from the same table
     [handle] dispatches on -- so a route that exists and is not listed, or is
     listed and does not exist, is impossible rather than merely unlikely. The
     two report routes are asserted by name because they are the two a reader
     is most likely to be told about and least likely to find by guessing. *)
  let test_the_report_routes_are_in_the_table () =
    let paths = List.map Server.routes ~f:(fun (path, _, _) -> path) in
    List.iter [ "/api/reports"; "/api/reports/garch" ] ~f:(fun path ->
        Alcotest.(check bool)
          (Printf.sprintf "%s is in the routes table" path)
          true
          (List.mem paths path ~equal:String.equal));
    (* Every route carries a one-line purpose. The 404 body prints them, and a
       route whose purpose is the empty string is a route nobody described. *)
    List.iter Server.routes ~f:(fun (path, purpose, _) ->
        Alcotest.(check bool)
          (Printf.sprintf "%s says what it is for" path)
          true
          (not (String.is_empty purpose)))
  ```

  And in `suite`, after the existing cases:

  ```ocaml
      Alcotest.test_case "the report routes are in the routes table" `Quick
        test_the_report_routes_are_in_the_table;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test server 2>&1 | tail -12
  ```
  Expected failure: `[FAIL] server ... /api/reports is in the routes table` with `Expected: true, Received: false`.

- [ ] **Step 3: Minimal implementation.** Three edits in `lib/server.ml` and two in `bin/main.ml`.

  **(a)** In `lib/server.ml`, add two fields to the `t` record. Locate it with `grep -n "^type t = {" lib/server.ml`; add these immediately after the `history : History_buffer.t;` field:

  ```ocaml
    (* The static reports, encoded once before the socket bound. A string and not
       a record, because nothing in this process ever reads them back -- they are
       computed, serialised, and handed out unchanged until the process dies. *)
    reports : string;
    (* The GARCH study's live state. Mutable inside itself, because it is filled
       by a second domain through an Atomic and joined by a poll on this one. *)
    garch : Reports.Garch.t;
  ```

  **(b)** In `lib/server.ml`, `create` gains the two arguments and sets the two fields. Locate with `grep -n "^let create" lib/server.ml`; add `~(reports : string) ~(garch : Reports.Garch.t)` to the parameter list immediately before the final `()`, and add `reports; garch;` to the record literal it builds, after `history = ...`.

  **(c)** In `lib/server.ml`, add two entries to the `routes` table, immediately after the `/api/stress` entry:

  ```ocaml
      ( "/api/reports",
        "the scaling probe, both validation batteries and the options walk, \
         computed once at startup",
        fun t ->
          Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode) t.reports );
      (* Separate from /api/reports because it arrives later and is polled until
         it does. Folding it in would mean either holding the 60 KB payload back
         for fifteen seconds or re-sending it every five. *)
      ( "/api/reports/garch",
        "the GARCH sample-size study, computed on a second domain after listen",
        fun t ->
          Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
            (Reports.Garch.json_string t.garch) );
  ```

  **(d)** In `lib/server.ml`, `json_of_ops`'s `reports` slot reads the real state. Locate it with `grep -n '"reports"' lib/server.ml` — Phase 2 left it as a constant pair. Replace the value expression with:

  ```ocaml
        ( "reports",
          `Assoc
            [
              (* Static reports exist before the socket binds, so by the time
                 anything can ask this question they are ready. Saying so anyway
                 is what lets /ops distinguish "ready and empty" from "not yet". *)
              ("static", `String "ready");
              ("garch", `String (Reports.Garch.status t.garch));
              ("garch_done", `Int (Reports.Garch.done_count t.garch));
              ("garch_of", `Int (Reports.Garch.total t.garch));
            ] );
  ```

  **(e)** In `bin/main.ml`, Phase 4's probe block is REPLACED — not added to — and both `Server.create` call sites gain the two arguments.

  In `run_demo`, Phase 4's Task 6 left this block immediately before `let log = Recompute_log.create () in`, under its why-comment (`(* The probe runs before the served graph exists. … *)`); find it by the marker comment on its first line:

  ```ocaml
    (* Phase 5 replaces this with Reports.compute, which runs the same probe. *)
    let (_ : Scaling_probe.row list) =
      Scaling_probe.rows ~seed:2026_07_30 ~sizes:Scaling_probe.default_sizes
        ~ticks:Scaling_probe.default_ticks
    in
    printf "  probe       10 / 100 / 400 names, 50 ticks each, before the served graph\n";
  ```

  Replace those six lines — the marker comment through the `printf` — with:

  ```ocaml
    (* Reports.compute runs the scaling probe Phase 4 ran here on its own --
       the same seed, sizes and tick count -- and then both validation
       batteries and the options walk, still before the served graph exists.
       It REPLACES the bare probe: running both would run the probe twice and
       put three thousand extra node creations into the startup counters the
       page labels. The why-comment above (the probe before the graph, so the
       smoke window never reads the probe's tail) still holds. *)
    let reports_record = Reports.compute () in
    let reports = Reports.to_json_string ~breach_json:Server.json_of_breach reports_record in
    let garch = Reports.Garch.create () in
    printf "  reports     probe 10 / 100 / 400, both batteries, the options walk -- %.0f ms, before the served graph\n"
      reports_record.Reports.computed_in_ms;
  ```

  Phase 4's why-comment stays above it; delete only its last sentence, `The rows are dropped here; Phase 5 keeps them for /api/reports.`, which is no longer true. Then `run_demo`'s `Server.create`, which Phase 4 left as

  ```ocaml
    let server =
      Server.create ?alerts ~recompute_log:log ~mode:`Demo ~quiet:[ quiet ] ~graph
        ~factor:"SYNTHETIC" ()
    in
  ```

  becomes

  ```ocaml
    let server =
      Server.create ?alerts ~recompute_log:log ~reports ~garch ~mode:`Demo ~quiet:[ quiet ]
        ~graph ~factor:"SYNTHETIC" ()
    in
  ```

  In `run_live`, the same six-line block sits inside `| Ok config ->` immediately before `let log = Recompute_log.create () in` (Phase 4 put both above `let counter = Counter.create () in`); replace it with the same `Reports.compute` block. Then add `~reports ~garch` immediately after `~recompute_log:log` in the `Server.create` inside `| Some port ->`, so its last line reads `~recompute_log:log ~reports ~garch ~graph ~factor:runtime.Config.Runtime.fred_series_id ()`. `make fmt` may re-wrap either call; the argument set is what matters.

  **(f)** `test/test_server.ml`: `Server.create` now has two required arguments, so the two helpers Phase 4's Task 6 left — `with_server ?mode ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~f ()` and `with_logged_server ~f ()` — no longer compile, and neither does anything in this file until they do. Replace both in full with:

  ```ocaml
  let with_server ?(mode = `Demo) ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~f () =
    with_graph
      ~f:(fun graph ->
        let server =
          (* An empty object for the static reports and a study that is never
             started. Nothing in this file reads either -- the encoders have
             their own suite in test_reports.ml -- and computing the batteries
             here would add a second to every case that builds a server. *)
          Server.create ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~mode ~graph
            ~factor:"SYNTHETIC" ~reports:"{}" ~garch:(Ohcamel.Reports.Garch.create ()) ()
        in
        f server graph)
      ()

  (* A server over a graph whose hook writes into the log the server holds --
     the shape run_demo and run_live build. The seeding runs BEFORE the log is
     read by anything, so a test that wants a clean frame drains once first. *)
  let with_logged_server ~f () =
    let log = Ohcamel.Recompute_log.create () in
    with_graph
      ~on_compute:(Ohcamel.Recompute_log.note log)
      ~f:(fun graph ->
        let server =
          Server.create ~recompute_log:log ~mode:`Demo ~graph ~factor:"SYNTHETIC" ~reports:"{}"
            ~garch:(Ohcamel.Reports.Garch.create ()) ()
        in
        f server graph log)
      ()
  ```

  `?recompute_log` stays in `with_server`'s pass-through and `~recompute_log:log` in `with_logged_server`: Phase 4's `test_the_server_holds_the_log` reads both. This is the last edit either helper receives.

  Then `test_ops_shape`, whose Phase 2 assertion says `reports.static` and `reports.garch` are `absent` — which (d) just made false. Replace the eight lines from `(* Phase 5 fills these. \`absent\` rather than \`ready\`, for the same` through `(match field_exn reports key with \`String s -> s | _ -> "?"));` with:

  ```ocaml
        (* Phase 5 filled these. The static reports are computed before the
           socket binds, so "ready" is the only state a caller can ever observe
           and saying it anyway is what lets /ops tell "ready and empty" from
           "not yet". garch is a word from the study's own state machine, with
           the two counts /ops prints as "computing k of N" beside it. *)
        let reports = field_exn j "reports" in
        Alcotest.(check string)
          "reports.static is ready once the process can answer at all" "ready"
          (match field_exn reports "static" with `String s -> s | _ -> "?");
        Alcotest.(check bool)
          "reports.garch is computing or done, never absent" true
          (match field_exn reports "garch" with
          | `String "computing" | `String "done" -> true
          | _ -> false);
        Alcotest.(check int)
          "garch_done is 0 for a study that was never started" 0
          (match field_exn reports "garch_done" with `Int n -> n | _ -> -1);
        Alcotest.(check bool)
          "garch_of is the study's total" true
          (match field_exn reports "garch_of" with `Int n -> n > 0 | _ -> false);
  ```

  **(g)** `deploy/smoke.sh`: the routes table's shadow, in the same commit as the table. The line Phase 4's Task 9 left,

  ```bash
  EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/ops"
  ```

  becomes

  ```bash
  EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/reports /api/reports/garch /api/graph /api/ops"
  ```

  — the two new tokens after `/api/stress`, where (c) put the routes, so the 404 assertion's order check holds; its count is computed from the string, so it now prints `lists the 11 routes in the table's order` with no further edit.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test server 2>&1 | tail -8 && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -4 && bash -n deploy/smoke.sh && echo SYNTAX-OK
  ```
  Expected: both suites report `Test Successful` — `server` now includes Phase 4's `the server holds the recompute log` (both helpers still compile and still hand the log through) and Phase 2's `test_ops_shape` with its rewritten `reports` block — and `SYNTAX-OK`.

  Then see the routes answer, on a real socket, and the smoke suite's 404 shadow agree with the table:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 6 && curl -s http://localhost:8099/api/reports | head -c 300 && echo && curl -s http://localhost:8099/api/reports/garch && echo && curl -s http://localhost:8099/api/nope | head -c 400 && echo && deploy/smoke.sh http://localhost:8099 | grep 'api/nope' && grep -n 'reports     probe\|probe       10' /tmp/ohcamel-demo.log; pkill -f "main.exe demo 8099"
  ```
  Expected: the first is a JSON object beginning `{"computed_at":`; the second is a small object whose `"status"` is `"computing"` (the domain is not spawned until Task 8, so it stays computing); the 404 body lists both new routes between `/api/stress` and `/api/graph`; `PASS  GET /api/nope               404, lists the 11 routes in the table's order`; and exactly one `reports     probe 10 / 100 / 400, …` line in the log with NO `probe       10 / 100 / 400 names` line — Phase 4's bare probe is gone, so the probe ran once.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/server.ml bin/main.ml test/test_server.ml deploy/smoke.sh && git commit -m "server: two report routes, because the host that could reproduce the README's tables reproduced none of them"
  ```

---

### Task 8: Spawn the study after listen, and prove the six modes are byte-identical

**Files:**
- Modify: `bin/main.ml` (`run_demo` after `Server.start`, `run_live` after `Server.start`)

**Interfaces:**
- Consumes: `Reports.Garch.start : Reports.Garch.t -> unit` (Task 5) — spawns the domain and installs the one-second poll; must be called only after `Server.start` has resolved, so the socket is bound before a second core starts competing for the machine.
- Produces: no new names. The observable change is that `/api/reports/garch` transitions to `done` on its own, and that `/ops` can say so.

- [ ] **Step 1: Write the failing test — the gate, and its baseline.** The gate is Phase 3 Task 1's `gate.sh`, which diffs the six credential-free modes against the captures beside it; every plan that touches `bin/main.ml` runs this one script, so there is one baseline and one verdict, and what it guards is that a refactor of `bin/main.ml` did not change a single byte the README quotes. Confirm the script and its six captures are still in scratch:

  ```bash
  G=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline
  ls "$G"/gate.sh "$G"/capture.sh "$G"/synthetic.txt "$G"/stress.txt "$G"/backtest.txt "$G"/backtest-crisis.txt "$G"/options.txt "$G"/garch.txt
  ```
  Expected: eight paths. If the six `.txt` captures are gone (scratch was cleared), re-capture them from the commit this task starts on — Task 7's — before any edit below. The six modes have been byte-identical since Phase 3's gate first passed, so any commit from Phase 3 Task 10 onward is the same baseline, but the tree must be clean when it is taken:

  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && test -z "$(git status --porcelain -- bin lib test)" && "$G/capture.sh"
  ```
  If the tree is not clean — an edit below was already started — capture from a worktree of the last commit instead, built with the repository's own switch (`capture.sh` builds in `$REPO`, which is why it cannot simply be pointed at the worktree):

  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && WT=/tmp/ohcamel-phase5-before && rm -rf "$WT" && git worktree prune && git worktree add --detach "$WT" "$(git rev-parse HEAD)" && eval "$(opam env --switch=$PWD --set-switch)" && (cd "$WT" && dune build bin/main.exe) && git -C "$WT" rev-parse HEAD > "$G/BASELINE_SHA" && for m in synthetic stress backtest backtest-crisis options garch; do (cd "$WT" && ./_build/default/bin/main.exe "$m") > "$G/$m.txt" 2>&1; echo "captured $m ($(wc -l < "$G/$m.txt") lines)"; done && shasum -a 256 "$G"/*.txt > "$G/BASELINE_SHA256" && git worktree remove --force "$WT"
  ```
  Expected: six `captured <mode> (N lines)` lines. `garch` takes ten to twenty seconds; the rest are fast.

- [ ] **Step 2: Run the gate and see it pass on the untouched tree.**
  ```bash
  /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
  ```
  Expected: six lines, `GATE ok        synthetic` … `GATE ok        garch`, exit 0. This establishes that the six modes are deterministic on this machine before the edit, so a `GATE DIFFERS` after it is the edit's fault and not the seed's.

- [ ] **Step 3: Minimal implementation.** Two edits in `bin/main.ml`. (The live banner's `Nothing here invents one for the live book` wording is Phase 2 Task 13's edit and is already in the tree; nothing here touches it.)

  **(a)** In `run_demo`, immediately after the line that starts the server — currently

  ```ocaml
    let%bind (_ : (_, _) Cohttp_async.Server.t) = Server.start ~port server in
  ```

  — insert:

  ```ocaml
    (* After listen, and only after.

       The study is five to fifteen seconds of pure arithmetic on a second core.
       Starting it before the socket bound would put it inside the healthcheck's
       twenty-second start period on a two-vCPU box, and a deploy that fails its
       own healthcheck is a rollback. Starting it after means the page is up,
       says "computing k of 180 fits, on a second domain", and fills in. *)
    Reports.Garch.start garch;
  ```

  **(b)** In `run_live`, the same line immediately after its `Server.start` — inside the `Some port ->` branch, after `live_line (sprintf "dashboard  http://localhost:%d" port);`:

  ```ocaml
              Reports.Garch.start garch;
  ```

- [ ] **Step 4: Run the tests and see them pass — and the gate.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
  ```
  Expected: six `GATE ok` lines. None of the six modes reaches `run_demo` or `run_live`, so a `GATE DIFFERS` here means an edit landed in shared code by mistake — stop and read the `.diff` it names rather than re-baselining.

  Then watch the study land on a running host:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 6 && curl -s http://localhost:8099/api/reports/garch | head -c 160 && echo && /bin/sleep 40 && curl -s http://localhost:8099/api/reports/garch | head -c 160 && echo && curl -s http://localhost:8099/api/ops | python3 -c 'import json,sys; print(json.load(sys.stdin)["reports"])' && pkill -f "main.exe demo 8099"
  ```
  Expected: the first read says `"status":"computing"` with a `"done"` below 180; the second says `"status":"done"` with a numeric `computed_in_ms`; `/api/ops`'s `reports` slot reports the same.

  And confirm the stream never stalled while the second domain was busy — this is the fear the design is arranged against:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 3 && deploy/smoke.sh http://localhost:8099 ; pkill -f "main.exe demo 8099"
  ```
  Expected: the SSE assertion passes — distinct frames spread over the window — while the study is still running.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add bin/main.ml && git commit -m "server: spawn the study after listen, because a ten-second core inside the healthcheck's start period is a rollback"
  ```

---

### Task 9: `/api/ops.reports` proved on the wire, and the GARCH completion line the deploy greps for

**Files:**
- Modify: `lib/reports.ml` (inside `module Garch`: the `t` record, `create`, a new `completion_line`, and `finish`)
- Modify: `bin/main.ml` (the two `Reports.Garch.create ()` lines Task 7 inserted, one in `run_demo`, one in `run_live`)
- Modify: `test/test_server.ml` (append one case)
- Modify: `test/test_reports.ml` (append one case)

**Interfaces:**
- Consumes: Task 7's `json_of_ops` `reports` slot — `{static: "ready", garch: Reports.Garch.status, garch_done: Reports.Garch.done_count, garch_of: Reports.Garch.total}` — and Task 7(f)'s `test_ops_shape` assertions on it; Task 7(f)'s `with_server`, which builds every server in this file with `~reports:"{}"` and a never-started `Ohcamel.Reports.Garch.create ()`, so the study route below answers `computing` with `done = 0`.
- Consumes: `Reports.Garch.{t, create, run_blocking, finish, done_count, total, status}` (Task 5); Phase 2's `respond server path` test helper (`test/test_server.ml`, Phase 2 Task 12 — `Server.lookup` + `Deferred.peek`, returning `(status : int, content_type : string option, body : string)`), which answers a route with no socket.
- Consumes: `live_line : string -> unit` (exists — `bin/main.ml:1444`, `printf "  %-30s %s\n%!" (Time_ns.to_string_utc (Time.now ())) what`), `Core.printf`.
- Produces: `Reports.Garch.create : ?log:(string -> unit) -> ?params:params -> ?truth:Vol_estimators.Garch11.t -> unit -> t` (the new optional argument defaults to `ignore`, so every existing call site compiles unchanged); `Reports.Garch.completion_line : t -> string`. **The log line's exact text**, which Phase 6's Task 4 finds with `grep -in "garch"` in `docker compose logs` and times against the banner:

  ```
  garch       done -- 180 of 180 fits in <N> ms on a second domain; /api/reports/garch is complete
  ```

  `<N>` is `computed_in_ms` printed with `%.0f`. On the demo host the line is preceded by two spaces (the banner's own indent); on the live host by `live_line`'s UTC timestamp column. If the study raised, the line is instead `garch       FAILED after <N> ms -- <error>; /api/reports/garch carries the error`. It is emitted exactly once per process, from `finish`, which both the blocking path and the joined domain reach once.

- [ ] **Step 1: Write the failing tests.** Append one case to `test/test_server.ml`, above `let suite =`:

  ```ocaml
  (* The two report routes answer through the table, with the JSON headers,
     without a socket. The static one is the string the server was given --
     verbatim, because it is never re-encoded -- and the study one is the
     "computing" object with done = 0, which is the state every deploy serves
     for its first ten seconds. *)
  let test_the_report_routes_answer () =
    with_server
      ~f:(fun server _graph ->
        let status, content_type, body = respond server "/api/reports" in
        Alcotest.(check int) "/api/reports is 200" 200 status;
        Alcotest.(check (option string)) "and is JSON" (Some "application/json") content_type;
        Alcotest.(check string) "and is the string it was given, unchanged" "{}" body;
        let status, _, body = respond server "/api/reports/garch" in
        Alcotest.(check int) "/api/reports/garch is 200 while computing" 200 status;
        let j = Yojson.Safe.from_string body in
        Alcotest.(check string)
          "and says computing" "computing"
          (match field_exn j "status" with `String s -> s | _ -> "?");
        Alcotest.(check int)
          "with nothing done" 0
          (match field_exn j "done" with `Int n -> n | _ -> -1))
      ()
  ```

  registered in `suite` after the Task 7 case:

  ```ocaml
      Alcotest.test_case "the report routes answer through the table" `Quick
        test_the_report_routes_answer;
  ```

  Then the log line. Append to `test/test_reports.ml`, before `let suite =`:

  ```ocaml
  (* The completion line, once.

     Phase 6 greps the container log for it and times the study against the
     startup banner. A line printed twice would time two studies; a line printed
     from the one-second poll on every tick would be a hundred lines the log
     driver has to hold. So it is emitted from [finish], which the blocking path
     and the joined domain each reach exactly once, and this counts. *)
  let test_completion_line_is_emitted_once () =
    let seen = ref [] in
    let g =
      Ohcamel.Reports.Garch.create
        ~log:(fun line -> seen := line :: !seen)
        ~params:
          {
            Ohcamel.Reports.Garch.seed = 2026_09_02;
            replications = tiny_replications;
            sample_sizes = tiny_sizes;
            burn_in = 100;
            coarse_steps = 8;
          }
        ()
    in
    Alcotest.(check int) "nothing logged before the study runs" 0 (List.length !seen);
    Ohcamel.Reports.Garch.run_blocking g;
    Alcotest.(check int) "exactly one line" 1 (List.length !seen);
    let line = List.hd_exn !seen in
    Alcotest.(check string)
      "the line is the one completion_line renders"
      (Ohcamel.Reports.Garch.completion_line g) line;
    let total = tiny_replications * List.length tiny_sizes in
    (* Pinned text, because a deploy greps it. The wall time in the middle is
       the only part that varies. *)
    Alcotest.(check bool)
      "opens with the word the grep looks for, and the counts" true
      (String.is_prefix line
         ~prefix:(Printf.sprintf "garch       done -- %d of %d fits in " total total));
    Alcotest.(check bool)
      "and closes by naming the route that now has the table" true
      (String.is_suffix line
         ~suffix:" ms on a second domain; /api/reports/garch is complete")
  ```

  And in `suite`:

  ```ocaml
        Alcotest.test_case "the completion line is emitted once" `Quick
          test_completion_line_is_emitted_once;
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  make build 2>&1 | tail -10
  ```
  Expected failure: `Error: The function applied to this argument has type ?params:... -> ?truth:... -> unit -> t. This argument cannot be applied with label ~log` (from `test_reports.ml`), because `create` has no `?log` yet.

- [ ] **Step 3: Minimal implementation.** Four edits.

  **(a)** `lib/reports.ml`, inside `module Garch`, the `t` record gains one field. Add it immediately after `mutable cache : string option;`:

  ```ocaml
      (* Where the one completion line goes. Injected rather than printed here,
         because the demo host indents it under its banner and the live host
         prefixes it with live_line's timestamp column, and reports.ml should not
         know either. Defaults to [ignore], so a test that never asked for a log
         does not write to the runner's stdout. *)
      log : string -> unit;
  ```

  **(b)** `create` takes it. Replace the `let create ...` line and add the field to its record literal:

  ```ocaml
    let create ?(log = ignore) ?(params = default_params) ?(truth = default_truth) () =
      {
        params;
        truth;
        total = params.replications * List.length params.sample_sizes;
        progress = Atomic.make 0;
        domain = None;
        started_at = None;
        finished = None;
        cache = None;
        log;
      }
  ```

  **(c)** Insert `completion_line` immediately before `let finish`, and make `finish` call it. The whole of `finish` becomes:

  ```ocaml
    (* One line, pinned. Phase 6 greps the container log for it and times the
       domain from the banner to here, so the wording is a contract and the only
       variable part is the wall time. The "computing" arm exists so the
       function is total; nothing prints it. *)
    let completion_line (t : t) : string =
      match t.finished with
      | None -> sprintf "garch       computing -- %d of %d fits" (done_count t) t.total
      | Some (Ok _, ms) ->
          sprintf
            "garch       done -- %d of %d fits in %.0f ms on a second domain; \
             /api/reports/garch is complete"
            (done_count t) t.total ms
      | Some (Error e, ms) ->
          sprintf "garch       FAILED after %.0f ms -- %s; /api/reports/garch carries the error"
            ms (Error.to_string_hum e)

    let finish (t : t) ~(started : Types.Time.t) (outcome : Garch_study.result Or_error.t)
        =
      t.finished <-
        Some (outcome, Types.Time.Span.to_ms (Types.Time.diff (Types.Time.now ()) started));
      t.domain <- None;
      t.cache <- None;
      (* Last, so the route already answers "done" by the time a reader who saw
         the line goes to look. *)
      t.log (completion_line t)
  ```

  **(d)** `bin/main.ml`: the two lines Task 7 inserted as `let garch = Reports.Garch.create () in`. In `run_demo` it becomes

  ```ocaml
    let garch = Reports.Garch.create ~log:(fun line -> printf "  %s\n%!" line) () in
  ```

  and in `run_live`

  ```ocaml
    let garch = Reports.Garch.create ~log:live_line () in
  ```

  `live_line` is defined at `bin/main.ml:1444`, above `run_live`; `printf` is Core's, already open in `bin/main.ml`.

- [ ] **Step 4: Run the tests and see them pass — and the gate, because `bin/main.ml` was touched.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test server 2>&1 | tail -6 && ./_build/default/test/test_ohcamel.exe test reports 2>&1 | tail -4 && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && echo FMT-CLEAN
  ```
  Expected: both suites `Test Successful` (`reports` now `11 tests run`), and `FMT-CLEAN`.

  ```bash
  /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
  ```
  Expected: six `GATE ok` lines against Phase 3's baseline (Task 8 Step 1 re-captured it if scratch had been cleared). The six modes never construct a `Reports.Garch.t`, so the line cannot appear in any of them.

  Then see the line land, once, on a running host:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 45 && grep -c "garch       done" /tmp/ohcamel-demo.log && grep -i "garch" /tmp/ohcamel-demo.log && curl -s http://localhost:8099/api/ops | python3 -c 'import json,sys; print(json.load(sys.stdin)["reports"])' && pkill -f "main.exe demo 8099"
  ```
  Expected: `1`, then the banner's `alerts ... Kill switch armed on nvda-cap` line and one `  garch       done -- 180 of 180 fits in <N> ms on a second domain; /api/reports/garch is complete` line, then `{'static': 'ready', 'garch': 'done', 'garch_done': 180, 'garch_of': 180}`. If `<N>` is above 20,000 on this machine, something else is competing for the second core; the number itself is recorded in Phase 6 from the droplet, not here.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/reports.ml bin/main.ml test/test_server.ml test/test_reports.ml && git commit -m "reports: the study announces itself in the log once, because the deploy times it from the container's stdout and /ops must not read absent"
  ```

---

### Task 10: `web/charts.js`, part 1 — the shared SVG helpers, four report forms, and the sparkline moved verbatim

**Files:**
- Create: `web/charts.js` (no earlier phase creates it)
- Modify: `web/format.js` (Phase 4 Task 12's file, `window.OhCamelFormat = { money, pct, el }`; one function and one export key added, nothing else)
- Create: `web/argument.js` (a two-line stub, so the dune rule below has a file to cat; Tasks 13–14 replace it)
- Modify: `web/dashboard.js` (the `sparkline` function is cut out; its one call site changes)
- Modify: `lib/dune` (the `(cat ../web/format.js ../web/graph.js ../web/dashboard.js)` line Phase 4 Task 12 left in the `dashboard_html.ml` rule — Phase 1's "one-line extension point" — and the quoted-JSON block inserted above the `<script>` echo)
- Modify: `test/test_embedded_assets.ml` (one case)
- Test: `test/test_embedded_assets.ml`, plus a Playwright script under `/tmp/ohcamel-phase5/pw/` (a dev-machine check, never committed — see *Working conventions*)

**Interfaces:**
- Consumes: the tokens `--ink --ink-soft --ink-faint --rule --over --live --mark --unknown` in `web/page.css` (Phase 1, moved verbatim from the old literal); `--second` and `--shade` (Task 12 adds them; until then a stroke on `var(--second)` falls back to `currentColor`, which is the only reason Task 12 may follow this task rather than precede it); `money`, `pct`, `el` — the three functions at the head of `web/dashboard.js`'s IIFE (`function money(x) {`, `function pct(x, dp) {`, `function el(tag, cls, text) {`), copied verbatim into `web/format.js`; the `sparkline` function in `web/dashboard.js`, from the line `  // A sparkline, drawn as inline SVG built by hand.` through the line `  }` that follows `    return { svg: svg, lo: lo, hi: hi };` (fifty-five lines), moved verbatim; `String.substr_index_all : t -> may_overlap:bool -> pattern:t -> int list` (verified: `_opam/lib/base/string_intf.ml:302`).
- Produces: `window.OhCamelFormat = { money, pct, el, vegaPoint }`; `window.OhCamelCharts` with `sparkline(values, {stroke}) -> {svg, lo, hi} | null` (the moved function, unchanged), `dotLine(container, {x, series: [{name, values, stroke, dashed?}], y: {log?, unit}, quoted?, ratios?}) -> SVGElement`, `pairedBars(container, [{name, money, risk, standalone}]) -> SVGElement`, `tenorBuckets(container, [{bucket, vega}], total) -> SVGElement`, and the helpers Task 11 extends it with: `_svg(tag, attrs, cls)`, `_text(x, y, s, cls, anchor)`, `_frame(container, w, h, cls)`, `_linear(lo, hi, a, b)`, `_log(lo, hi, a, b)`. Every SVG a form draws carries `class="chart <form>"` and `data-chart="<form>"`, which is what the Playwright checks count by.

**Why the sparkline moves and the formatters are copied.** The spec's chart 13 is "the existing function, moved verbatim", so `sparkline` leaves `dashboard.js` and the page holds exactly one copy — the OCaml test below counts. `money`, `pct` and `el` are different: `dashboard.js` calls them a hundred times inside its closure, and cutting them out of a file Phase 4 is rewriting at the same time is a merge nobody needs. So `format.js` is created with the same three bodies, `dashboard.js` keeps its private copies until the reconcile pass removes them, and the spec's `format.js (money, pct, el)` is satisfied by the file that exists.

- [ ] **Step 1: Write the failing tests.** Two, because the two things that can go wrong are different. The OCaml case asserts the page is assembled in the right order and holds one sparkline — that is the gate CI runs. The Playwright script asserts the four forms draw the right elements in a real browser against a real `ohcamel demo` — that is the check this machine runs.

  Append to `test/test_embedded_assets.ml`, immediately above `let suite =`:

  ```ocaml
  (* The client is five scripts catted in a fixed order, and the order is the
     whole contract: format.js defines the formatters charts.js calls at load,
     charts.js defines the sparkline dashboard.js calls on every frame, and
     argument.js runs last because it reads everything above it. A rule that
     cats them in any other order produces a page that parses, serves, and
     throws on its first frame -- which the structure test catches and the
     type checker cannot. *)
  let test_the_client_scripts_are_assembled_in_order () =
    markers_in_order Dashboard_html.page ~name:"scripts"
      ~markers:
        [
          "<script id=\"quoted\" type=\"application/json\">";
          "\"scaling\"";
          "<script>";
          "window.OhCamelFormat =";
          "function sparkline(";
          "window.OhCamelCharts =";
          "new EventSource(";
          "</script>";
        ];
    (* Moved, not copied. Two sparklines would be two renderers of the same
       trail, and the second would be the one nobody remembers to change. *)
    Alcotest.(check int)
      "exactly one sparkline in the whole page" 1
      (List.length
         (String.substr_index_all Dashboard_html.page ~may_overlap:false
            ~pattern:"function sparkline("))
  ```

  and register it in `suite`, after the last existing case:

  ```ocaml
        Alcotest.test_case "the client scripts are assembled in order" `Quick
          test_the_client_scripts_are_assembled_in_order;
  ```

  Then the browser check. Playwright is installed once, into scratch, at the version `~/Documents/gridbox` already uses so its Chromium is already in `~/Library/Caches/ms-playwright` and nothing is downloaded (`npx playwright install chromium` is idempotent and is there for the day the cache is cleared):

  ```bash
  mkdir -p /tmp/ohcamel-phase5/pw && cd /tmp/ohcamel-phase5/pw && [ -f package.json ] || npm init -y >/dev/null
  cd /tmp/ohcamel-phase5/pw && npm i --silent playwright@1.61.1 && npx playwright install chromium 2>&1 | tail -1
  ```

  Create `/tmp/ohcamel-phase5/pw/check-charts-1.mjs`. It loads the page from the demo server, then calls each form with fixture data **inside the page** and counts the elements it drew — the forms are pure functions of their arguments, so fixtures are the right input here and the wire is Task 13's problem:

  ```js
  // Chart forms 2-5 and 13, drawn with fixtures inside the served page.
  import { chromium } from "playwright";
  const base = process.env.BASE || "http://localhost:8099";
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/", { waitUntil: "networkidle" });
  const counts = await page.evaluate(() => {
    const C = window.OhCamelCharts;
    const box = document.createElement("div");
    document.body.appendChild(box);
    const n = (svg, sel) => svg.querySelectorAll(sel).length;
    const out = {};
    const s = C.sparkline([1, 2, 3, 2, 4], { stroke: "var(--ink)" });
    out.sparkline = [n(s.svg, "path"), n(s.svg, "circle")];
    const d = C.dotLine(box, {
      x: [10, 100, 400],
      series: [
        { name: "nodes in graph", values: [63, 342, 1272], stroke: "var(--ink-soft)" },
        { name: "nodes per tick", values: [25.6, 25.2, 26.0], stroke: "var(--ink)" },
      ],
      y: { unit: "count" },
    });
    out.dotLine = [n(d, "path.series"), n(d, "circle.pt"), n(d, "text.x"), n(d, "text.end"), d.getAttribute("data-chart")];
    const b = C.dotLine(box, {
      x: [10, 100, 400],
      series: [
        { name: "incremental", values: [19.5, 89.1, 492.7], stroke: "var(--ink-soft)" },
        { name: "polled", values: [51.7, 3387.7, 53426.8], stroke: "var(--ink-faint)", dashed: true },
      ],
      y: { log: true, unit: "µs" }, quoted: true, ratios: ["2.7×", "38×", "108×"],
    });
    out.bench = [n(b, "path.series"), n(b, "circle.pt"), n(b, "text.ratio"), b.classList.contains("quoted") ? 1 : 0];
    const a = C.pairedBars(box, [
      { name: "AAPL", money: 0.185, risk: 0.188, standalone: 0.21 },
      { name: "CVX", money: 0.185, risk: 0.271, standalone: 0.30 },
      { name: "JPM", money: 0.103, risk: -0.039, standalone: 0.05 },
      { name: "XOM", money: null, risk: null, standalone: null },
    ]);
    out.paired = [n(a, "rect.money"), n(a, "rect.risk"), n(a, "rect.risk.hedge"), n(a, "line.standalone"), n(a, "text.name")];
    const t = C.tenorBuckets(box, [
      { bucket: "≤1w", vega: 0 }, { bucket: "1w–1m", vega: -12559 }, { bucket: "1–3m", vega: 0 },
      { bucket: "3–6m", vega: 12559 }, { bucket: "6–12m", vega: 0 }, { bucket: ">1y", vega: 0 },
    ], 0);
    out.tenor = [n(t, "text.slot"), n(t, "rect.bar"), n(t, "line.zero"), n(t, "line.total")];
    return out;
  });
  const expect = {
    sparkline: [1, 1],
    dotLine: [2, 6, 3, 2, "dotline"],
    bench: [2, 6, 3, 1],
    paired: [3, 3, 1, 3, 4],
    tenor: [6, 2, 1, 1],
  };
  const bad = errors.map((e) => "pageerror: " + e);
  for (const k of Object.keys(expect)) {
    if (JSON.stringify(counts[k]) !== JSON.stringify(expect[k])) {
      bad.push(k + ": expected " + JSON.stringify(expect[k]) + ", got " + JSON.stringify(counts[k]));
    }
  }
  await browser.close();
  if (bad.length) { console.error("FAIL\n  " + bad.join("\n  ")); process.exit(1); }
  console.log("CHARTS-1 OK " + JSON.stringify(counts));
  ```

- [ ] **Step 2: Run them and see them fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -6
  ```
  Expected failure: `[FAIL] embedded_assets ... the client scripts are assembled in order` with `scripts: "window.OhCamelFormat =" does not appear after "<script>"`.

  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-charts-1.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected failure: the evaluate throws `TypeError: Cannot read properties of undefined (reading 'sparkline')`, because `window.OhCamelCharts` does not exist.

- [ ] **Step 3: Minimal implementation.** Five edits, in this order, so the page never references a global that a later script defines.

  **(a) `web/format.js`.** Phase 4's Task 12 created it with `money`, `pct` and `el` (the three bodies copied character for character from the head of `web/dashboard.js`'s IIFE) and the export line `window.OhCamelFormat = { money: money, pct: pct, el: el };`. Add one function and one key. Immediately before that export line, insert:

  ```js
  // Vega crosses the wire per 1.00 of annualised vol, the unit the limits are
  // written in. The desk's "per vol point" is the /100, and it is a DISPLAY
  // decision made here and named, so a chart and a table cannot disagree by a
  // factor of a hundred about the same number.
  function vegaPoint(x) {
    return x === null || x === undefined ? null : x / 100;
  }
  ```

  and change the export line to:

  ```js
  window.OhCamelFormat = { money: money, pct: pct, el: el, vegaPoint: vegaPoint };
  ```

  Nothing else in the file moves; Phase 4's `check-graph.mjs` still reads the same three formatters through the same global.

  **(b) `web/argument.js`**, the stub the rule needs until Task 13:

  ```js
  // web/argument.js -- the argument below the graph. Tasks 13-14 of Phase 5 fill this file.
  ```

  **(c) Cut `sparkline` out of `web/dashboard.js` and keep the bytes.** By content, not by line number, because Phase 4 is editing the same file:

  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && mkdir -p /tmp/ohcamel-phase5 && python3 - <<'PY'
  import pathlib
  p = pathlib.Path('web/dashboard.js'); s = p.read_text()
  head = '  // A sparkline, drawn as inline SVG built by hand.\n'
  tail = '    return { svg: svg, lo: lo, hi: hi };\n  }\n'
  start = s.index(head)
  end = s.index(tail, start) + len(tail)
  moved = s[start:end]
  pathlib.Path('/tmp/ohcamel-phase5/sparkline.moved.js').write_text(moved)
  call = 'var drawn = sparkline(h[s.key], { stroke: s.stroke });'
  assert s.count(call) == 1, 'the one call site'
  s = s[:start] + s[end:]
  s = s.replace(call, 'var drawn = window.OhCamelCharts.sparkline(h[s.key], { stroke: s.stroke });')
  p.write_text(s)
  print(len(moved.splitlines()), 'lines moved;', s.count('function sparkline('), 'sparklines left in dashboard.js')
  PY
  ```
  Expected: `55 lines moved; 0 sparklines left in dashboard.js`. The block that leaves is exactly this, and it is what goes into `charts.js` in (d), unchanged down to its comment:

  ```js
    // A sparkline, drawn as inline SVG built by hand.
    //
    // No charting library, and not because one would be hard to add -- because
    // dashboard_html.ml is a single string compiled into the binary, and the
    // whole point of that is that the dashboard has no external dependency to
    // fetch, version, or fail to fetch. A CDN script tag would make this page
    // stop working on a machine with no route to the internet, which is exactly
    // the machine a risk dashboard is most likely to be pinned to.
    //
    // Sixty lines of SVG buys the two things a trail actually needs: the shape,
    // and the endpoints. It does not buy axes, zoom, tooltips or a legend, and
    // it is not trying to.
    function sparkline(values, opts) {
      var w = 260, h = 44, pad = 3;
      var pts = [];
      for (var i = 0; i < values.length; i++) {
        if (values[i] !== null && isFinite(values[i])) pts.push([i, values[i]]);
      }
      if (pts.length < 2) return null;
      var lo = pts[0][1], hi = pts[0][1];
      for (var j = 1; j < pts.length; j++) {
        if (pts[j][1] < lo) lo = pts[j][1];
        if (pts[j][1] > hi) hi = pts[j][1];
      }
      // A dead-flat series has no range to scale against. Drawing it through the
      // middle is the honest rendering: the line is flat because the number did
      // not move, not because the scale collapsed.
      var span = (hi - lo) || 1;
      var n = values.length - 1 || 1;
      var d = "";
      for (var k = 0; k < pts.length; k++) {
        var x = pad + (pts[k][0] / n) * (w - 2 * pad);
        var y = h - pad - ((pts[k][1] - lo) / span) * (h - 2 * pad);
        d += (k === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
      }
      var last = pts[pts.length - 1];
      var lx = pad + (last[0] / n) * (w - 2 * pad);
      var ly = h - pad - ((last[1] - lo) / span) * (h - 2 * pad);
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.setAttribute("width", w);
      svg.setAttribute("height", h);
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", opts.stroke);
      path.setAttribute("stroke-width", "1.4");
      path.setAttribute("stroke-linejoin", "round");
      svg.appendChild(path);
      var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", lx.toFixed(1));
      dot.setAttribute("cy", ly.toFixed(1));
      dot.setAttribute("r", "2");
      dot.setAttribute("fill", opts.stroke);
      svg.appendChild(dot);
      return { svg: svg, lo: lo, hi: hi };
    }
  ```

  **(d) `web/charts.js`.** Create it. Where the comment says `<<< the moved sparkline >>>`, paste the fifty-five lines above (or `cat /tmp/ohcamel-phase5/sparkline.moved.js`) — nothing else changes in them:

  ```js
  // web/charts.js -- the report charts, inline SVG built by hand.
  //
  // Every form here is the sparkline's idiom grown by one axis: shape and
  // endpoints, no gridlines, no legend beyond a word at a line's end, strokes
  // and fills on CSS variables so both colour schemes work from one drawing.
  // No charting library, for the sparkline's reason -- the page is one string
  // in the binary and must work on a machine with no route to the internet --
  // and one more: a library's defaults would decide what a hairline is, and
  // on this page the hairline behind a risk bar IS the diversification.
  //
  // Each form takes the numbers it draws as arguments and computes nothing
  // about them beyond a scale. Shares, ratios, residuals and bursts arrive
  // from the engine on the wire; a chart that derived any of them would be a
  // second implementation, which is the one rule every module here keeps.
  (function () {
    "use strict";

    var NS = "http://www.w3.org/2000/svg";
    var F = window.OhCamelFormat;

    function svgEl(tag, attrs, cls) {
      var e = document.createElementNS(NS, tag);
      for (var k in attrs) {
        if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
      }
      if (cls) e.setAttribute("class", cls);
      return e;
    }
    function txt(x, y, s, cls, anchor) {
      var t = svgEl("text", { x: (+x).toFixed(1), y: (+y).toFixed(1), "text-anchor": anchor || "start" }, cls);
      t.textContent = s;
      return t;
    }
    // A responsive frame: the viewBox is the drawing's own coordinates and the
    // width is the container's, so a wide figure scrolls inside its own box
    // below 900px (page.css) instead of the page scrolling sideways.
    function frame(container, w, h, cls) {
      var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, width: "100%", role: "img", "data-chart": cls }, "chart " + cls);
      svg.style.maxWidth = w + "px";
      container.appendChild(svg);
      return svg;
    }
    function linear(lo, hi, a, b) {
      var span = (hi - lo) || 1;
      return function (v) { return a + ((v - lo) / span) * (b - a); };
    }
    function logScale(lo, hi, a, b) {
      var l = Math.log10(lo), span = (Math.log10(hi) - l) || 1;
      return function (v) { return a + ((Math.log10(v) - l) / span) * (b - a); };
    }
    // The top of a linear axis: 1, 2 or 5 times a power of ten, at or above v.
    function niceMax(v) {
      if (!(v > 0)) return 1;
      var p = Math.pow(10, Math.floor(Math.log10(v)));
      var m = v / p;
      return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
    }
    function bar(svg, x0, y, dx, hgt, fill, cls) {
      if (dx === null || dx === undefined || !isFinite(dx)) return;
      var x = dx < 0 ? x0 + dx : x0;
      svg.appendChild(svgEl("rect", { x: x.toFixed(1), y: y, width: Math.abs(dx).toFixed(1), height: hgt, fill: fill }, cls));
    }

    // 2 and 3. Dot-and-line over a CATEGORICAL x. Three book sizes are not a
    // continuum, so the x positions are equal steps and the numbers are
    // labels; two series that diverge is the claim, and the flat one is the
    // argument. With y.log the axis is decades, because 108x spans orders of
    // magnitude and a log axis is the only honest linear reading of it. With
    // quoted:true the SVG takes the .quoted class and page.css sets it in the
    // soft ink -- a quotation must not look like a measurement.
    function dotLine(container, spec) {
      var w = 460, h = 190, L = 52, R = 112, T = 18, B = 30;
      var svg = frame(container, w, h, "dotline" + (spec.quoted ? " quoted" : ""));
      if (spec.quoted) svg.classList.add("quoted");
      var xs = spec.x;
      var xAt = function (i) { return L + (xs.length < 2 ? 0 : (i / (xs.length - 1)) * (w - L - R)); };
      var all = [];
      spec.series.forEach(function (s) { s.values.forEach(function (v) { if (v !== null && isFinite(v)) all.push(v); }); });
      var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
      var y, yLo, yHi;
      if (spec.y && spec.y.log) {
        yLo = Math.pow(10, Math.floor(Math.log10(lo)));
        yHi = Math.pow(10, Math.ceil(Math.log10(hi)));
        y = logScale(yLo, yHi, h - B, T);
      } else {
        yLo = 0; yHi = niceMax(hi);
        y = linear(yLo, yHi, h - B, T);
      }
      // The axis is two hairline ticks and two numbers: the ends of the range
      // and nothing in between, because the reader compares the two lines to
      // each other and not to a grid.
      [yLo, yHi].forEach(function (v) {
        svg.appendChild(svgEl("line", { x1: L - 4, y1: y(v).toFixed(1), x2: L, y2: y(v).toFixed(1), stroke: "var(--rule)", "stroke-width": "0.75" }, "tick"));
        svg.appendChild(txt(L - 7, y(v) + 3.5, v.toLocaleString("en-US") + (spec.y && spec.y.unit ? " " + spec.y.unit : ""), "y", "end"));
      });
      xs.forEach(function (x, i) { svg.appendChild(txt(xAt(i), h - 8, String(x), "x", "middle")); });
      spec.series.forEach(function (s) {
        var d = "";
        s.values.forEach(function (v, i) { d += (i === 0 ? "M" : "L") + xAt(i).toFixed(1) + " " + y(v).toFixed(1); });
        svg.appendChild(svgEl("path", { d: d, fill: "none", stroke: s.stroke, "stroke-width": "1.4", "stroke-linejoin": "round", "stroke-dasharray": s.dashed ? "3 3" : null }, "series"));
        s.values.forEach(function (v, i) { svg.appendChild(svgEl("circle", { cx: xAt(i).toFixed(1), cy: y(v).toFixed(1), r: "2.4", fill: s.stroke }, "pt")); });
        var last = s.values.length - 1;
        var e = txt(xAt(last) + 8, y(s.values[last]) + 3.5, s.name, "end", "start");
        e.setAttribute("fill", s.stroke);
        svg.appendChild(e);
      });
      if (spec.ratios) spec.ratios.forEach(function (r, i) { svg.appendChild(txt(xAt(i), T - 6, r, "ratio", "middle")); });
      return svg;
    }

    // 4. Paired horizontal bars per name, signed. The claim is a gap between two
    // shares of the same name, so the two are stacked in one row and the gap is
    // a distance. A negative risk share is a hedge and is drawn LEFTWARD in
    // --live: the sign is the "no stray abs" test made visible, and a bar that
    // showed its magnitude would erase the one fact the row exists to show.
    // The standalone extent is a hairline behind the risk bar; the space between
    // the two is what diversification bought. A null (warming up) draws nothing
    // and says so in the value column.
    function pairedBars(container, rows) {
      var w = 460, rowH = 26, L = 58, R = 74, T = 8;
      var h = T + rows.length * rowH + 8;
      var svg = frame(container, w, h, "paired");
      var maxAbs = 1e-9;
      rows.forEach(function (r) {
        [r.money, r.risk, r.standalone].forEach(function (v) { if (v !== null && v !== undefined && isFinite(v) && Math.abs(v) > maxAbs) maxAbs = Math.abs(v); });
      });
      var zero = L + (w - L - R) * 0.3;
      var px = function (v) { return v === null || v === undefined || !isFinite(v) ? null : (v / maxAbs) * (w - L - R) * 0.7; };
      svg.appendChild(svgEl("line", { x1: zero, y1: T, x2: zero, y2: h - 8, stroke: "var(--rule)", "stroke-width": "0.75" }, "zero"));
      rows.forEach(function (r, i) {
        var y0 = T + i * rowH;
        svg.appendChild(txt(L - 8, y0 + 15, r.name, "name", "end"));
        var sa = px(r.standalone);
        if (sa !== null) svg.appendChild(svgEl("line", { x1: zero, y1: y0 + 16, x2: (zero + sa).toFixed(1), y2: y0 + 16, stroke: "var(--rule)", "stroke-width": "1" }, "standalone"));
        bar(svg, zero, y0 + 4, px(r.money), 6, "var(--ink-soft)", "money");
        var hedge = r.risk !== null && r.risk !== undefined && r.risk < 0;
        bar(svg, zero, y0 + 13, px(r.risk), 6, hedge ? "var(--live)" : "var(--ink)", "risk" + (hedge ? " hedge" : ""));
        var v = txt(w - R + 6, y0 + 15, r.risk === null || r.risk === undefined ? "—" : F.pct(r.risk, 1), "val" + (r.risk === null || r.risk === undefined ? " unknown" : ""), "start");
        svg.appendChild(v);
      });
      return svg;
    }

    // 5. Six fixed tenor slots. The point of the figure is that a parallel-shift
    // total of zero hides two opposite bars, so the empty slots stay on the
    // axis as empty -- a chart that dropped them would hide the hiding. Values
    // arrive already in the display unit (per vol point); the caller applies
    // OhCamelFormat.vegaPoint once and the caption says so.
    function tenorBuckets(container, buckets, total) {
      var w = 460, h = 150, L = 16, R = 104, T = 16, B = 26;
      var svg = frame(container, w, h, "tenor");
      var maxAbs = 1;
      buckets.forEach(function (b) { if (isFinite(b.vega) && Math.abs(b.vega) > maxAbs) maxAbs = Math.abs(b.vega); });
      var mid = T + (h - T - B) / 2;
      var slotW = (w - L - R) / buckets.length;
      var y = function (v) { return mid - (v / maxAbs) * ((h - T - B) / 2 - 4); };
      svg.appendChild(svgEl("line", { x1: L, y1: mid.toFixed(1), x2: w - R, y2: mid.toFixed(1), stroke: "var(--rule)", "stroke-width": "0.75" }, "zero"));
      buckets.forEach(function (b, i) {
        var cx = L + slotW * (i + 0.5);
        svg.appendChild(txt(cx, h - 8, b.bucket, "slot", "middle"));
        if (b.vega !== 0 && isFinite(b.vega)) {
          var top = Math.min(mid, y(b.vega));
          svg.appendChild(svgEl("rect", { x: (cx - 10).toFixed(1), y: top.toFixed(1), width: 20, height: Math.abs(mid - y(b.vega)).toFixed(1), fill: b.vega < 0 ? "var(--over)" : "var(--ink)" }, "bar"));
          svg.appendChild(txt(cx, b.vega < 0 ? y(b.vega) + 12 : y(b.vega) - 5, F.money(b.vega), "val", "middle"));
        }
      });
      // The parallel-shift total, as a flat rule beside the slots. At zero it
      // sits exactly on the axis, which is the whole picture.
      svg.appendChild(svgEl("line", { x1: w - R + 16, y1: y(total).toFixed(1), x2: w - 8, y2: y(total).toFixed(1), stroke: "var(--ink)", "stroke-width": "1.4" }, "total"));
      svg.appendChild(txt(w - R + 16, y(total) - 6, "total " + F.money(total), "totallbl", "start"));
      return svg;
    }

    // 13. <<< the moved sparkline >>>

    window.OhCamelCharts = {
      sparkline: sparkline,
      dotLine: dotLine,
      pairedBars: pairedBars,
      tenorBuckets: tenorBuckets,
      _svg: svgEl, _text: txt, _frame: frame, _linear: linear, _log: logScale, _bar: bar,
    };
  })();
  ```

  Prove the paste is the same bytes that left `dashboard.js`:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && python3 -c "import pathlib; m=pathlib.Path('/tmp/ohcamel-phase5/sparkline.moved.js').read_text(); c=pathlib.Path('web/charts.js').read_text(); print('VERBATIM' if m in c else 'NOT VERBATIM -- re-paste from /tmp/ohcamel-phase5/sparkline.moved.js')" && node --check web/charts.js && node --check web/format.js && node --check web/dashboard.js && echo SYNTAX-OK
  ```

  **(e) `lib/dune`.** Two edits to the `dashboard_html.ml` rule. First, the line Phase 4's Task 12 left,

  ```
      (cat ../web/format.js ../web/graph.js ../web/dashboard.js)
  ```

  becomes exactly

  ```
      (cat ../web/format.js ../web/graph.js ../web/charts.js ../web/dashboard.js ../web/argument.js)
  ```

  Second, the quoted tables reach the page. Phase 1 deliberately left `web/quoted.json` out of this rule — its bytes would have broken Phase 1's page-identical gate — and the spec's *Embedding* puts it between `index.html` and the script. Between the rule's line `    (cat ../web/index.html)` and its line `    (echo "<script>\n")`, insert:

  ```
      (echo "<script id=\"quoted\" type=\"application/json\">\n")
      (cat ../web/quoted.json)
      (echo "</script>\n")
  ```

  so `argument.js`'s `JSON.parse(document.getElementById("quoted").textContent)` (Task 13) has something to read; the OCaml case in Step 1 asserts the block's position. Then `make fmt` — `dune fmt` may re-wrap the long `(cat …)` across lines, and its layout is the one committed.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make fmt >/dev/null && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -4 && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && echo FMT-CLEAN
  ```
  Expected: `Test Successful` and `FMT-CLEAN`.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-charts-1.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected: `CHARTS-1 OK {"sparkline":[1,1],"dotLine":[2,6,3,2,"dotline"],"bench":[2,6,3,1],"paired":[3,3,1,3,4],"tenor":[6,2,1,1]}` and no `pageerror` — the last of which is the check that the four history sparklines still draw after the move, since a broken `renderHistory` throws on the first frame.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add web/charts.js web/format.js web/argument.js web/dashboard.js lib/dune test/test_embedded_assets.ml && git commit -m "web: the report chart forms in the sparkline's idiom, and the sparkline moved to live with them, because the page holds one renderer per shape"
  ```

---

### Task 11: `web/charts.js`, part 2 — exceedance strips, crisis timelines, the GARCH whisker, stress bars, the frame-arrival strip

**Files:**
- Modify: `web/charts.js` (insert five forms and one selector before the line `    window.OhCamelCharts = {`, and extend that object)
- Test: a Playwright script under `/tmp/ohcamel-phase5/pw/`, same style as Task 10 (the OCaml structure test from Task 10 already covers this file's place in the page and does not change)

**Interfaces:**
- Consumes: `_svg`, `_text`, `_frame`, `_linear`, `_log`, `_bar` and `F = window.OhCamelFormat` from Task 10 — all inside the same IIFE, so they are reached by their local names `svgEl`, `txt`, `frame`, `linear`, `logScale`, `bar`; `--shade` and `--second` from `page.css` (Task 12).
- Produces, added to `window.OhCamelCharts`: `estimatorStroke = {historical: "var(--ink)", parametric: "var(--second)", ewma: "var(--mark)"}` — the page-wide assignment the spec fixes, stated once here and read everywhere; `strip(container, {length, hits: [int], marks?: [{at}]}) -> SVGElement`; `timeline(container, {dates, realised, window, var: {kind: [fraction]}, hits: {kind: [int]}, burst: {kind: {start, span, count}}, yRange: [lo, hi], selected?, annotations?: [{index, label}]}) -> {svg, select(kind)}`; `selector(container, kinds, labels, onSelect) -> HTMLElement` (three text links, `a.est[data-est]`); `dotWhisker(container, {xs, rows: [{n, mean, sd}], truth, window, yRange}) -> {svg, update(rows)}`; `signedBars(container, [{name, value, worst, note}]) -> SVGElement`; `arrivalStrip(container, {windowS: 60, shadeS: 20}) -> {svg, tick(atMs, distinct), redraw(nowMs), stats(nowMs) -> {frames, distinct, spreadS}}`.
- Alignment contract for `timeline`, from Task 3's encoder: `realised[t]` is the return of session `t + 1`, so it has `sessions - 1` entries; forecast `j` scores return index `t = window + j` and is dated `dates[window + j + 1]`; a hit `j` is drawn at `x(window + j)` on the bar `realised[window + j]`; a burst `{start, span}` shades `[window + start, window + start + span)`. The chart does the index arithmetic and nothing else — the burst's start and count are the engine's, from the wire.

- [ ] **Step 1: Write the failing test.** Create `/tmp/ohcamel-phase5/pw/check-charts-2.mjs`:

  ```js
  // Chart forms 6-10, drawn with fixtures inside the served page.
  import { chromium } from "playwright";
  const base = process.env.BASE || "http://localhost:8099";
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/", { waitUntil: "networkidle" });
  const counts = await page.evaluate(() => {
    const C = window.OhCamelCharts;
    const box = document.createElement("div");
    document.body.appendChild(box);
    const n = (el, sel) => el.querySelectorAll(sel).length;
    const out = {};
    const s = C.strip(box, { length: 940, hits: [10, 50, 600], marks: [{ at: 600 }] });
    out.strip = [n(s, "line.base"), n(s, "rect.hit"), n(s, "line.mark")];
    // 400 sessions from 2019-06-03, one calendar day apart, so exactly one year
    // boundary falls inside the window and the first year is labelled too.
    const dates = [];
    const d0 = new Date("2019-06-03T00:00:00Z");
    for (let i = 0; i < 400; i++) dates.push(new Date(d0.getTime() + i * 86400e3).toISOString().slice(0, 10));
    const realised = Array.from({ length: 399 }, (_, t) => 0.03 * Math.sin(t / 7));
    const flat = (v) => Array.from({ length: 339 }, () => v);
    const tl = C.timeline(box, {
      dates, realised, window: 60,
      var: { historical: flat(0.02), parametric: flat(0.025), ewma: flat(0.03) },
      hits: { historical: [5, 100], parametric: [7, 100, 200], ewma: [9] },
      burst: { historical: { start: 90, span: 21, count: 2 }, parametric: { start: 95, span: 21, count: 3 }, ewma: { start: 0, span: 21, count: 1 } },
      yRange: [-0.08, 0.08], selected: "parametric",
    });
    out.timeline = [n(tl.svg, "rect.ret"), n(tl.svg, "path.var"), n(tl.svg, "circle.hit"), n(tl.svg, "rect.burst"), n(tl.svg, "text.x"), tl.svg.getAttribute("data-selected")];
    tl.select("historical");
    out.timelineAfter = [n(tl.svg, "circle.hit"), tl.svg.querySelector("text.burst").textContent, tl.svg.getAttribute("data-selected")];
    const links = C.selector(box, ["historical", "parametric", "ewma"], ["historical", "parametric", "ewma(0.94)"], (k) => tl.select(k));
    links.querySelectorAll("a.est")[2].click();
    out.selector = [n(links, "a.est"), n(links, "a.est.on"), tl.svg.getAttribute("data-selected"), n(tl.svg, "circle.hit")];
    const g = C.dotWhisker(box, { xs: [60, 125, 250, 500, 1000, 2000], rows: [{ n: 60, mean: 0.556, sd: 0.364 }, { n: 125, mean: 0.704, sd: 0.334 }], truth: 0.98, window: 60, yRange: [0, 1.1] });
    out.whisker = [n(g.svg, "text.x"), n(g.svg, "circle.dot"), n(g.svg, "line.whisker"), n(g.svg, "line.truth"), n(g.svg, "line.window")];
    g.update([60, 125, 250, 500, 1000, 2000].map((k) => ({ n: k, mean: 0.9, sd: 0.05 })));
    out.whiskerAfter = [n(g.svg, "circle.dot"), n(g.svg, "line.whisker")];
    const rows = Array.from({ length: 12 }, (_, i) => ({ name: "s" + i, value: (i % 2 ? -1 : 1) * (i + 1) * 1000, worst: i === 11, note: i < 3 ? "dd-cap" : "" }));
    const sb = C.signedBars(box, rows);
    out.signed = [n(sb, "rect.bar"), n(sb, "rect.bar.worst"), n(sb, "text.name"), n(sb, "text.note")];
    const now = Date.now();
    const ar = C.arrivalStrip(box, { windowS: 60, shadeS: 20 });
    ar.tick(now - 70000, true); ar.tick(now - 15000, true); ar.tick(now - 5000, true); ar.tick(now - 5000, false);
    ar.redraw(now);
    out.arrival = [n(ar.svg, "line.tick"), n(ar.svg, "line.tick.distinct"), n(ar.svg, "rect.shade"), JSON.stringify(ar.stats(now))];
    return out;
  });
  const expect = {
    strip: [1, 3, 1],
    timeline: [399, 3, 3, 1, 2, "parametric"],
    timelineAfter: [2, "2 exceptions in 21 sessions", "historical"],
    selector: [3, 1, "ewma", 1],
    whisker: [6, 2, 2, 1, 1],
    whiskerAfter: [6, 6],
    signed: [12, 1, 12, 3],
    arrival: [3, 2, 1, '{"frames":3,"distinct":2,"spreadS":10}'],
  };
  const bad = errors.map((e) => "pageerror: " + e);
  for (const k of Object.keys(expect)) {
    if (JSON.stringify(counts[k]) !== JSON.stringify(expect[k])) {
      bad.push(k + ": expected " + JSON.stringify(expect[k]) + ", got " + JSON.stringify(counts[k]));
    }
  }
  await browser.close();
  if (bad.length) { console.error("FAIL\n  " + bad.join("\n  ")); process.exit(1); }
  console.log("CHARTS-2 OK");
  ```

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-charts-2.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected failure: `TypeError: C.strip is not a function`.

- [ ] **Step 3: Minimal implementation.** One insertion and one replacement in `web/charts.js`. Insert the block below immediately **above** the line `    window.OhCamelCharts = {` (after the moved sparkline). Every element class named here is one the Step 1 test counts, and the counts are derived beside each form.

  ```js
    // The estimator colours, fixed page-wide and stated once. §04's table,
    // §06's three timelines and the selector under them all read this object;
    // a second assignment anywhere would be a second legend for the reader to
    // learn, and the point of fixing it is that there is one.
    var estimatorStroke = { historical: "var(--ink)", parametric: "var(--second)", ewma: "var(--mark)" };

    function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

    // 6. Exceedance strip. One hairline per forecast day, a 1x6 tick per hit.
    // Clustering, regularity and absence are visible in a strip and invisible
    // in a p-value: the jumps rows read as a comb, which is the Weibull test's
    // whole reason to exist, and the zero-exception row reads as a bare rule.
    // Marks are the caller's (§04 puts the day-600 regime boundary on the
    // vol-regime rows); the strip does no arithmetic on them.
    function strip(container, spec) {
      var w = 460, h = 12, L = 2, R = 2;
      var svg = frame(container, w, h, "strip");
      var x = linear(0, Math.max(1, spec.length - 1), L, w - R);
      svg.appendChild(svgEl("line", { x1: L, y1: h - 2, x2: w - R, y2: h - 2, stroke: "var(--rule)", "stroke-width": "0.75" }, "base"));
      (spec.marks || []).forEach(function (m) {
        svg.appendChild(svgEl("line", { x1: x(m.at).toFixed(1), y1: 0, x2: x(m.at).toFixed(1), y2: h, stroke: "var(--rule)", "stroke-width": "0.75", "stroke-dasharray": "2 2" }, "mark"));
      });
      spec.hits.forEach(function (i) {
        svg.appendChild(svgEl("rect", { x: (x(i) - 0.5).toFixed(1), y: h - 8, width: 1, height: 6, fill: "var(--over)" }, "hit"));
      });
      return svg;
    }

    // 7. A crisis timeline. Returns as 1px --rule bars, the three -VaR lines in
    // the fixed estimator colours, and -- for ONE estimator at a time -- its
    // hits as --over dots and its worst 21-session window shaded in --shade.
    // One estimator's dots at a time because three sets of dots on 570 days is
    // a smear; the selector under the figure switches them.
    //
    // Alignment, from the encoder (Task 3): realised[t] is the return of
    // session t+1, so it has sessions-1 entries and realised index t sits under
    // dates[t+1]; forecast j scores realised[window + j]; a hit j is a dot at
    // x(window + j) on that bar; a burst {start, span} shades
    // [window + start, window + start + span). The chart does this index
    // arithmetic and nothing else -- start, span and count are the engine's.
    function timeline(container, spec) {
      var w = 460, h = 120, L = 40, R = 10, T = 12, B = 18;
      var svg = frame(container, w, h, "timeline");
      var n = spec.realised.length;
      var x = linear(0, Math.max(1, n - 1), L, w - R);
      var y = linear(spec.yRange[0], spec.yRange[1], h - B, T);
      var zero = y(0);
      var win = spec.window;
      [spec.yRange[0], spec.yRange[1]].forEach(function (v) {
        svg.appendChild(svgEl("line", { x1: L - 4, y1: y(v).toFixed(1), x2: L, y2: y(v).toFixed(1), stroke: "var(--rule)", "stroke-width": "0.75" }, "tick"));
        svg.appendChild(txt(L - 7, y(v) + 3.5, F.pct(v, 0), "y", "end"));
      });
      spec.realised.forEach(function (r, t) {
        if (r === null || !isFinite(r)) return;
        var top = Math.min(zero, y(r));
        svg.appendChild(svgEl("rect", { x: (x(t) - 0.5).toFixed(1), y: top.toFixed(1), width: 1, height: Math.max(0.5, Math.abs(y(r) - zero)).toFixed(1), fill: "var(--rule)" }, "ret"));
      });
      // X labels: the first year and every year boundary, nothing else. A
      // date index i sits at the bar of realised index i-1 (the first date has
      // no return under it and is drawn at the axis origin).
      var xd = function (i) { return x(Math.max(0, i - 1)); };
      var lastYear = null;
      spec.dates.forEach(function (d, i) {
        var yr = String(d).slice(0, 4);
        if (yr !== lastYear) {
          svg.appendChild(txt(xd(i), h - 4, yr, "x", i === 0 ? "start" : "middle"));
          lastYear = yr;
        }
      });
      (spec.annotations || []).forEach(function (a) {
        svg.appendChild(svgEl("line", { x1: xd(a.index).toFixed(1), y1: T, x2: xd(a.index).toFixed(1), y2: h - B, stroke: "var(--rule)", "stroke-width": "0.75" }, "ann"));
        svg.appendChild(txt(xd(a.index), h - 4, a.label, "x ann", "middle"));
      });
      Object.keys(spec.var).forEach(function (kind) {
        var d = "";
        spec.var[kind].forEach(function (v, j) {
          if (v === null || !isFinite(v)) return;
          d += (d === "" ? "M" : "L") + x(win + j).toFixed(1) + " " + y(-v).toFixed(1);
        });
        svg.appendChild(svgEl("path", { d: d, fill: "none", stroke: estimatorStroke[kind], "stroke-width": "1", "stroke-linejoin": "round", "data-est": kind }, "var"));
      });
      var sel = svgEl("g", {}, "sel");
      svg.appendChild(sel);
      function select(kind) {
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        var b = spec.burst && spec.burst[kind];
        if (b && b.count > 0) {
          var x1 = x(win + b.start) - 0.5, x2 = x(win + b.start + b.span - 1) + 0.5;
          sel.appendChild(svgEl("rect", { x: x1.toFixed(1), y: T, width: (x2 - x1).toFixed(1), height: h - T - B, fill: "var(--shade)" }, "burst"));
          var onLeft = x1 < L + (w - L - R) / 2;
          sel.appendChild(txt(onLeft ? x2 + 4 : x1 - 4, T + 8, b.count + " exceptions in " + b.span + " sessions", "burst lbl", onLeft ? "start" : "end"));
        }
        (spec.hits[kind] || []).forEach(function (j) {
          var r = spec.realised[win + j];
          if (r === null || r === undefined || !isFinite(r)) return;
          sel.appendChild(svgEl("circle", { cx: x(win + j).toFixed(1), cy: y(r).toFixed(1), r: "2.5", fill: "var(--over)" }, "hit"));
        });
        svg.setAttribute("data-selected", kind);
      }
      select(spec.selected || Object.keys(spec.var)[0]);
      return { svg: svg, select: select };
    }

    // The three text links under the timelines. Plain <a>s in the .lbl
    // treatment, each in its estimator's colour; the click marks one .on and
    // hands the kind to the caller, which usually fans it out to three charts.
    // No .on at construction: the timelines already show their own selection,
    // and the caller clicks the matching link once so the two cannot disagree.
    function selector(container, kinds, labels, onSelect) {
      var box = F.el("p", "est-links lbl");
      kinds.forEach(function (k, i) {
        var a = F.el("a", "est", labels[i]);
        a.href = "#";
        a.setAttribute("data-est", k);
        a.style.color = estimatorStroke[k];
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          box.querySelectorAll("a.est").forEach(function (o) { o.classList.remove("on"); });
          a.classList.add("on");
          onSelect(k);
        });
        box.appendChild(a);
        if (i < kinds.length - 1) box.appendChild(document.createTextNode(" · "));
      });
      container.appendChild(box);
      return box;
    }

    // 8. The GARCH study: persistence mean as a dot with a +/- sd whisker at
    // six log-spaced n, a dotted hairline at the true value, a vertical
    // hairline at the engine's window. Log x because the sample sizes roughly
    // double. update(rows) redraws only the dots and whiskers, which is how §05
    // fills row by row as the second domain reports in -- the only progress
    // indicator on the page, and it is data arriving rather than a spinner.
    function dotWhisker(container, spec) {
      var w = 460, h = 170, L = 44, R = 16, T = 16, B = 28;
      var svg = frame(container, w, h, "whisker");
      var x = logScale(spec.xs[0], spec.xs[spec.xs.length - 1], L, w - R);
      var lo = spec.yRange[0], hi = spec.yRange[1];
      var y = linear(lo, hi, h - B, T);
      [lo, hi].forEach(function (v) {
        svg.appendChild(svgEl("line", { x1: L - 4, y1: y(v).toFixed(1), x2: L, y2: y(v).toFixed(1), stroke: "var(--rule)", "stroke-width": "0.75" }, "tick"));
        svg.appendChild(txt(L - 7, y(v) + 3.5, v.toFixed(1), "y", "end"));
      });
      spec.xs.forEach(function (n) { svg.appendChild(txt(x(n), h - 10, String(n), "x", "middle")); });
      svg.appendChild(svgEl("line", { x1: L, y1: y(spec.truth).toFixed(1), x2: w - R, y2: y(spec.truth).toFixed(1), stroke: "var(--rule)", "stroke-width": "0.75", "stroke-dasharray": "3 3" }, "truth"));
      svg.appendChild(txt(w - R, y(spec.truth) - 4, "true " + spec.truth.toFixed(2), "truthlbl", "end"));
      svg.appendChild(svgEl("line", { x1: x(spec.window).toFixed(1), y1: T, x2: x(spec.window).toFixed(1), y2: h - B, stroke: "var(--rule)", "stroke-width": "0.75" }, "window"));
      svg.appendChild(txt(x(spec.window) + 4, T + 8, "the engine's window", "windowlbl", "start"));
      var g = svgEl("g", {}, "rows");
      svg.appendChild(g);
      function update(rows) {
        while (g.firstChild) g.removeChild(g.firstChild);
        rows.forEach(function (r) {
          if (r.mean === null || r.mean === undefined || !isFinite(r.mean)) return;
          var cx = x(r.n).toFixed(1);
          var sd = isFinite(r.sd) ? r.sd : 0;
          g.appendChild(svgEl("line", { x1: cx, y1: y(clamp(r.mean + sd, lo, hi)).toFixed(1), x2: cx, y2: y(clamp(r.mean - sd, lo, hi)).toFixed(1), stroke: "var(--ink)", "stroke-width": "1" }, "whisker"));
          g.appendChild(svgEl("circle", { cx: cx, cy: y(clamp(r.mean, lo, hi)).toFixed(1), r: "2.6", fill: "var(--ink)" }, "dot"));
        });
      }
      update(spec.rows);
      return { svg: svg, update: update };
    }

    // 9. Stress: signed horizontal P&L bars in suite order, losses leftward in
    // --over, gains in --ink, the worst outlined, breach names beside. Twelve
    // signed numbers want comparing and suite order keeps the six standard
    // scenarios first with each sector's selloff beside its squeeze.
    function signedBars(container, rows) {
      var w = 460, rowH = 18, L = 96, R = 150, T = 6;
      var h = T + rows.length * rowH + 6;
      var svg = frame(container, w, h, "signed");
      var maxAbs = 1e-9;
      rows.forEach(function (r) { if (r.value !== null && isFinite(r.value) && Math.abs(r.value) > maxAbs) maxAbs = Math.abs(r.value); });
      var zero = L + (w - L - R) / 2;
      var px = function (v) { return v === null || v === undefined || !isFinite(v) ? null : (v / maxAbs) * ((w - L - R) / 2); };
      svg.appendChild(svgEl("line", { x1: zero, y1: T, x2: zero, y2: h - 6, stroke: "var(--rule)", "stroke-width": "0.75" }, "zero"));
      rows.forEach(function (r, i) {
        var y0 = T + i * rowH;
        svg.appendChild(txt(L - 8, y0 + 12, r.name, "name", "end"));
        var dx = px(r.value);
        bar(svg, zero, y0 + 4, dx, 9, r.value < 0 ? "var(--over)" : "var(--ink)", "bar" + (r.worst ? " worst" : ""));
        if (dx !== null) {
          var neg = dx < 0;
          svg.appendChild(txt(zero + dx + (neg ? -4 : 4), y0 + 12, F.money(r.value), "val" + (neg ? " neg" : ""), neg ? "end" : "start"));
        }
        if (r.note) svg.appendChild(txt(w - R + 6, y0 + 12, r.note, "note", "start"));
      });
      return svg;
    }

    // 10. The frame-arrival strip: a 60 s axis, one tick per SSE frame at its
    // browser arrival time, the trailing 20 s shaded. smoke.sh's load-bearing
    // assertion is about spread in TIME -- a buffering proxy delivers the pile
    // at once -- and this is that assertion drawn. stats() is computed over
    // the shaded window only, because that is the window the suite reads, so
    // the caption's "K distinct frames spread over 20 s" is the same statement
    // the deploy makes.
    function arrivalStrip(container, opts) {
      var w = 460, h = 26, L = 2, R = 2;
      var windowS = opts.windowS || 60, shadeS = opts.shadeS || 20;
      var svg = frame(container, w, h, "arrival");
      var ticks = [];
      var x = linear(-windowS, 0, L, w - R);
      function within(nowMs, span) {
        return ticks.filter(function (t) { return t.at >= nowMs - span * 1000 && t.at <= nowMs; })
          .sort(function (a, b) { return a.at - b.at; });
      }
      function tick(atMs, distinct) { ticks.push({ at: atMs, distinct: !!distinct }); }
      function redraw(nowMs) {
        ticks = within(nowMs, windowS);
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        svg.appendChild(svgEl("rect", { x: x(-shadeS).toFixed(1), y: 0, width: (x(0) - x(-shadeS)).toFixed(1), height: h, fill: "var(--shade)" }, "shade"));
        svg.appendChild(svgEl("line", { x1: L, y1: h - 4, x2: w - R, y2: h - 4, stroke: "var(--rule)", "stroke-width": "0.75" }, "base"));
        ticks.forEach(function (t) {
          var cx = x((t.at - nowMs) / 1000).toFixed(1);
          svg.appendChild(svgEl("line", { x1: cx, y1: t.distinct ? 4 : 12, x2: cx, y2: h - 4, stroke: t.distinct ? "var(--ink)" : "var(--ink-faint)", "stroke-width": "1" }, "tick" + (t.distinct ? " distinct" : "")));
        });
        svg.appendChild(txt(L, h - 6 - 10, "−" + windowS + " s", "x", "start"));
        svg.appendChild(txt(x(-shadeS), h - 6 - 10, "−" + shadeS + " s", "x", "middle"));
        svg.appendChild(txt(w - R, h - 6 - 10, "now", "x", "end"));
      }
      function stats(nowMs) {
        var recent = within(nowMs, shadeS);
        var distinct = recent.filter(function (t) { return t.distinct; }).length;
        var spread = recent.length >= 2 ? Math.round((recent[recent.length - 1].at - recent[0].at) / 1000) : 0;
        return { frames: recent.length, distinct: distinct, spreadS: spread };
      }
      redraw(Date.now());
      return { svg: svg, tick: tick, redraw: redraw, stats: stats };
    }
  ```

  Then replace the `window.OhCamelCharts = { … };` object Task 10 wrote with:

  ```js
    window.OhCamelCharts = {
      sparkline: sparkline,
      dotLine: dotLine,
      pairedBars: pairedBars,
      tenorBuckets: tenorBuckets,
      strip: strip,
      timeline: timeline,
      selector: selector,
      dotWhisker: dotWhisker,
      signedBars: signedBars,
      arrivalStrip: arrivalStrip,
      estimatorStroke: estimatorStroke,
      _svg: svgEl, _text: txt, _frame: frame, _linear: linear, _log: logScale, _bar: bar,
    };
  ```

  How the Step 1 counts fall out, so a mismatch is diagnosable: `strip` draws one `line.base`, one `line.mark` per mark and one `rect.hit` per hit → `[1, 3, 1]`. `timeline` draws one `rect.ret` per finite realised entry (399), one `path.var` per kind (3), and for the selected kind one `circle.hit` per hit (parametric: 3), one `rect.burst` when its count is positive (1), and `text.x` only at year changes — 400 daily dates from 2019-06-03 cross exactly one boundary, so `2019` at index 0 and `2020` at index 212 → 2. `select("historical")` clears the `g.sel` group and redraws two hits and the text `2 exceptions in 21 sessions`. `selector` marks nothing `.on` until a click, so after the third link is clicked exactly one is. `dotWhisker` draws six `text.x`, one dot and one whisker per row given, and `update` replaces them wholesale. `signedBars` draws one `rect.bar` per finite value, `worst` on the flagged row, one `text.name` per row and a `text.note` only for a non-empty note (3 of 12). `arrivalStrip` prunes to the 60 s window on `redraw` (the −70 s tick goes), draws the three that remain with `.distinct` on two, and `stats` reads the shaded 20 s: three frames, two distinct, spread `−5 − (−15) = 10`.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  node --check web/charts.js && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -4
  ```
  Expected: no syntax error; `Test Successful` — the structure test from Task 10 still finds one `function sparkline(` and the scripts in order.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-charts-2.mjs && node check-charts-1.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected: `CHARTS-2 OK` and then `CHARTS-1 OK {...}` with no `pageerror` — the second run proves part 1's forms are untouched by the insertion.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add web/charts.js && git commit -m "web: the strips, timelines, whisker, stress bars and arrival strip, because a burst is a date and a p-value cannot show one"
  ```

---

### Task 12: The nine sections' prose cut from the README, the two new tokens, reduced motion, the narrow fallback, and the caption's provenance slot

**Files:**
- Modify: `web/index.html` (insert the `<article id="argument">` immediately above the line `<footer>`; nothing above `</main>` changes — Phase 4 owns the header, Figure 1 and the ledger)
- Modify: `web/page.css` (append)
- Modify: `web/quoted.json` (two tables added: `bench`, `verified`)
- Modify: `test/test_embedded_assets.ml` (the keys assertion in `test_quoted_parses_and_holds_the_four_tables`; two new cases)
- Test: `test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: Phase 1's `markers_in_order : string -> name:string -> markers:string list -> unit` helper in `test/test_embedded_assets.ml` (Task 10 already calls it); `Ohcamel.Quoted.json`; `Ohcamel.Verified.{tests : int; coverage_covered : int; coverage_lines : int; coverage_pct : float; dated : string}` (Phase 3 Task 9's `lib/verified.ml`). Those five are MEASURED by Phase 3 Task 9 at execution — `tests` is the registry's count, above the spec's 210 because Phases 1–3 added suites; `coverage_pct` is `make coverage`'s one-decimal percentage — and the JSON below copies them verbatim.
- Produces: the DOM contract `web/argument.js` fills — nine `<section class="arg" id="s01">…`id="s09"` in README order, each with a `div.frag[data-frag]` head, an `h2` whose text is the README heading verbatim, `div.prose` with `<data data-bind="…">—</data>` elements, and `figure.fig[data-fig]` slots (`s01a s01b s01c s02 s03a s03b s03c s04 s05 s06a s06b s06c s07 s08a s08b s08c`); the tokens `--second` and `--shade` in both schemes; the classes `.fig figcaption`, `.cap-title`, `.cap-prov`, `.prov[data-prov]`, `.quoted`, `.compare`, `.engine`, `.frag-list`, `.row3`, `.twocol`, `.est-links`, `.invariants`; `quoted.json` gains `bench.rows[].{instruments, incremental_us, polled_us, ratio}` and `verified.{tests, covered, of, coverage_pct, dated, per_file[].{file, pct}}`.

**Why the prose is in HTML and not in JS.** The spec's own fear is that the page's prose drifts from the README's, and its answer is that "the section headings are the README's by rule, the prose is short and cut from it". Prose that lives in `index.html` is prose a reader can diff against `README.md` with two files open; prose assembled by string concatenation in `argument.js` is not. So every sentence below is the README's, cut and never rewritten, and the numbers in it that a figure also shows are `<data>` elements that arrive empty (`—`) and are filled by `argument.js` from the same JSON the figure reads — so a number in the prose can never be a README number posing as this host's.

- [ ] **Step 1: Write the failing tests.** Append to `test/test_embedded_assets.ml`, above `let suite =`:

  ```ocaml
  (* The argument is nine sections in the README's order, and the order is the
     contract: the page promises to read as the README does. Each marker is
     the section's id followed by its README heading, so a section that kept
     its slot but lost its heading -- or swapped places with its neighbour --
     is a red test rather than a page that reads in a different order from the
     document it is cut from. *)
  let test_the_nine_sections_are_in_readme_order () =
    markers_in_order Dashboard_html.page ~name:"argument"
      ~markers:
        [
          "</main>";
          "<article id=\"argument\">";
          "id=\"s01\""; "Why it isn't a loop";
          "id=\"s02\""; "Where the risk is</h2>";
          "id=\"s03\""; "Where the risk is when it isn't linear";
          "id=\"s04\""; "Is the number any good";
          "id=\"s05\""; "The estimator that is implemented and deliberately not used";
          "id=\"s06\""; "The same battery, real crises";
          "id=\"s07\""; "What would break it";
          "id=\"s08\""; "Watching it";
          "id=\"s09\""; "What this is not";
          "</article>";
          "<footer>";
        ];
    (* Every section opens with the piece of the graph it interrogates. *)
    Alcotest.(check int)
      "nine fragment heads" 9
      (List.length
         (String.substr_index_all Dashboard_html.page ~may_overlap:false
            ~pattern:"<div class=\"frag\" data-frag="))

  (* The two tokens the report charts stroke on, defined in BOTH schemes. A
     token defined only for light falls back to currentColor in dark, which
     draws the parametric line in the ink -- the same colour as historical --
     and the page's one fixed legend silently becomes a lie at night. *)
  let test_the_two_new_tokens_exist_in_both_schemes () =
    let count pattern =
      List.length
        (String.substr_index_all Dashboard_html.page ~may_overlap:false ~pattern)
    in
    Alcotest.(check int) "--second: twice (light and dark)" 2 (count "--second:");
    Alcotest.(check int) "--shade: twice (light and dark)" 2 (count "--shade:");
    Alcotest.(check bool)
      "reduced motion is honoured" true
      (String.is_substring Dashboard_html.page ~substring:"prefers-reduced-motion");
    Alcotest.(check bool)
      "the narrow fallback exists" true
      (String.is_substring Dashboard_html.page ~substring:"max-width: 700px")

  (* quoted.json's verified block mirrors lib/verified.ml, and this is the
     pin. Phase 6 re-measures and re-dates both; a change to one without the
     other is a stale count on a public page, which is the failure a dated
     constant exists to prevent. *)
  let test_quoted_verified_matches_lib () =
    let v = U.member "verified" (Lazy.force quoted) in
    Alcotest.(check int) "tests" Ohcamel.Verified.tests (U.to_int (U.member "tests" v));
    Alcotest.(check (float 1e-9))
      "coverage percent" Ohcamel.Verified.coverage_pct
      (U.to_number (U.member "coverage_pct" v));
    Alcotest.(check int)
      "seventeen files in the bimodal table" 17
      (List.length (U.to_list (U.member "per_file" v)));
    Alcotest.(check int)
      "the bench table has the three book sizes" 3
      (List.length (rows "bench"))
  ```

  In `test_quoted_parses_and_holds_the_four_tables`, the key list becomes ten:

  ```ocaml
      [ "battery"; "bench"; "crisis"; "garch"; "machine"; "note"; "quoted_on"; "scaling"; "source"; "verified" ]
  ```

  and its description string changes to `"the six quoted tables, and where they were quoted from"`. Register the three cases in `suite`, after Task 10's:

  ```ocaml
        Alcotest.test_case "the nine sections are in README order" `Quick
          test_the_nine_sections_are_in_readme_order;
        Alcotest.test_case "the two new tokens exist in both schemes" `Quick
          test_the_two_new_tokens_exist_in_both_schemes;
        Alcotest.test_case "quoted.verified matches lib/verified.ml" `Quick
          test_quoted_verified_matches_lib;
  ```

- [ ] **Step 2: Run them and see them fail.**
  ```bash
  make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | grep -E "FAIL|Error" | head -5
  ```
  Expected: `[FAIL] … the nine sections are in README order` (`argument: "<article id=\"argument\">" does not appear after "</main>"`), `[FAIL] … the two new tokens exist in both schemes` (`--second: twice`: expected 2, got 0), `[FAIL] … web/quoted.json parses and holds the four tables` (the key list), and `[FAIL] … quoted.verified matches lib/verified.ml` (`Yojson.Safe.Util.Type_error` on the missing member).

- [ ] **Step 3: Minimal implementation, part (a) — `web/index.html`.** Insert this block immediately above the line `<footer>` (locate it with `grep -n "^<footer>" web/index.html`; there is exactly one). Every paragraph is `README.md` text; the source lines are noted in an HTML comment at the head of each section so the cut can be checked. `<data>` elements arrive as `—` and are filled by Task 13/14; a `<data>` that stays `—` is a binding `argument.js` forgot, visible on the page rather than hidden by a README number.

  ```html

  <article id="argument">

  <!-- §01 — README.md "Why it isn't a loop" and "What that costs in seconds" -->
  <section class="arg" id="s01" data-section="01">
    <div class="frag" data-frag="s01"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§01</span> Why it isn't a loop</h2>
    <div class="prose">
      <p>Most real-time risk systems poll. A timer fires, the process recomputes the whole book, renders it, sleeps. That design is easy to write and it is wrong in two ways at once. The number on screen is as old as the last tick of the <em>timer</em>, not the last tick of the <em>market</em>, so it is always slightly stale by construction. And the cost of an update scales with the size of the book rather than the size of the change: one print in one name pays for an n×n covariance matrix that no price could possibly have altered.</p>
      <p>OhCamel does the other thing. Risk here is a dependency graph. Prices, quantities, cash, return windows, the factor series and the current time are input cells; everything derived from them is a node with declared edges. A tick sets one cell, and the runtime works out what is downstream of it and recomputes exactly that. The rest of the graph is not "recomputed and found unchanged" — it is never visited.</p>
    </div>
    <div class="row3">
      <figure class="fig" data-fig="s01a"></figure>
      <figure class="fig" data-fig="s01b"></figure>
      <figure class="fig quoted" data-fig="s01c"></figure>
    </div>
    <div class="prose">
      <p>The middle column is flat and the right one is not. The cost of an event is set by what the event touches, not by how large the book is.</p>
      <h3>What that costs in seconds</h3>
      <p>At 400 names a full recompute costs <data data-bind="bench.polled_400_ms">—</data> milliseconds, which is not a slow number so much as a disqualifying one: it caps the engine at about nineteen events a second before it falls behind the market it is supposed to be watching. The incremental path is <data data-bind="bench.ratio_400">—</data> faster and allocates 330× fewer words.</p>
      <p>And now the honest part, which the node-count table hides. <strong>The incremental engine's cost per tick is not flat in wall-clock</strong> — <data data-bind="bench.inc_10">—</data>, <data data-bind="bench.inc_100">—</data>, <data data-bind="bench.inc_400">—</data> across the three sizes — even though the node count is (<data data-bind="scaling.per_tick_10">—</data>, <data data-bind="scaling.per_tick_100">—</data>, <data data-bind="scaling.per_tick_400">—</data>). Both are true and the second does not imply the first. A tick reaches about twenty-five nodes at every book size, but <em>those</em> nodes are not all O(1): the weights are O(n), the portfolio return series is O(n·w), and the Euler decomposition is a matrix-vector product at O(n²). What incrementality buys is that the covariance matrix — O(n²·w), the single most expensive thing here — is not among them.</p>
      <p>CI does not run this. Benchmark numbers from a shared runner are noise wearing a lab coat, so <code>make bench</code> is a local command and the table above is a quoted run rather than a gate.</p>
    </div>
  </section>

  <!-- §02 — README.md "Where the risk is" -->
  <section class="arg" id="s02" data-section="02">
    <div class="frag" data-frag="s02"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§02</span> Where the risk is</h2>
    <div class="prose">
      <p>A portfolio VaR of $12,000 is a fact about the book and not an instruction. It does not say what to sell, and the obvious way of finding out — sort the positions by size — answers a different question. Money and risk are not the same distribution.</p>
      <p>Portfolio volatility is homogeneous of degree one in the weights, so Euler's theorem splits it exactly. No approximation, no residual. Each term is one instrument's <em>contribution</em>, and because the split is exact the terms sum over any partition of the book — a sector, a strategy, a desk — and the parts still add to the whole. Parametric VaR is a constant multiple of <code>sigma_p</code>, so the same split carries into VaR units and the component VaRs sum to the portfolio's.</p>
    </div>
    <figure class="fig" data-fig="s02"></figure>
    <div class="prose">
      <p>That limit behaves differently from a notional cap in a way worth internalising before writing one: it is correlation-aware, so a name's number moves when <em>other</em> positions move. And a contribution can be <strong>negative</strong> — a position that moves against the book reduces portfolio volatility — so a hedge consumes none of its risk limit however large it is. <code>test_graph.ml</code> asserts that directly, because a stray <code>abs</code> anywhere in the chain would breach a risk limit for the act of hedging.</p>
      <p>Two honest limits. The decomposition is the <em>Gaussian</em> one: it needs a differentiable closed form for portfolio risk, and only the covariance path has one. So this says how risk is <em>shared out</em>, which is a question about correlation structure and is fairly robust, rather than how large the tail <em>is</em>, which is what normality gets wrong. Read it beside the historical number, not instead of it.</p>
    </div>
  </section>

  <!-- §03 — README.md "Where the risk is when it isn't linear" and its three subsections -->
  <section class="arg" id="s03" data-section="03">
    <div class="frag" data-frag="s03"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§03</span> Where the risk is when it isn't linear</h2>
    <p class="synthetic-line lbl" data-line="synthetic">—</p>
    <div class="prose">
      <p>Everything above measures risk in one dimension. An equity position's exposure is price times quantity, its P&amp;L moves linearly with the price, and a covariance matrix over returns says most of what there is to say. None of that survives contact with an option. A position can be flat in the underlying and still lose money on a move in either direction, or lose money on no move at all, or lose money because the market changed its mind about how much the underlying <em>will</em> move without the underlying moving.</p>
    </div>
    <div class="row3">
      <figure class="fig" data-fig="s03a"></figure>
      <figure class="fig" data-fig="s03b"></figure>
      <figure class="fig" data-fig="s03c"></figure>
    </div>
    <div class="prose">
      <p>The hedge is <data data-bind="options.hedge_shares">—</data> shares and the engine computed the ratio — it is the option leg's delta-equivalent exposure over the spot. Delta goes to zero — to within floating-point rounding, which is what the test asserts, at 1e-6, because (a / b) × b is not always a in IEEE arithmetic. Gamma and vega do not move at all, because a share is linear in its own price and contributes precisely none of either. That is the entire content of the phrase <em>first-order hedge</em>. The notional cap is clear because the book <em>is</em> delta flat. The vega cap is not, because it never was. An engine that measured only exposure would report this book as carrying no risk at all.</p>
      <p>Nothing was traded. The share count is identical. The book is no longer delta flat, because the contract decayed further out of the money, its delta fell, and the hedge that offset it exactly now over-hedges by <data data-bind="options.clock_delta">—</data>. A delta hedge is correct at an instant and stale immediately afterwards — which is why the number hangs off an edge instead of being stored.</p>
      <p>The total is zero and the book is not flat. It is short near-dated volatility and long far-dated volatility in equal parallel-shift size — a bet that the term structure steepens, with real P&amp;L, which one vega number reports as nothing at all. The gamma of <data data-bind="options.calendar_gamma">—</data> is a second exposure the same number is silent about.</p>
      <p>And <strong>live mode ships options risk disabled</strong>, with a line saying so. Greeks need an implied vol per contract, Alpaca's free tier does not provide an options chain, and the alternatives were to decline or to invent a surface.</p>
    </div>
    <p class="live-line" data-line="no-options">—</p>
  </section>

  <!-- §04 — README.md "Is the number any good" -->
  <section class="arg" id="s04" data-section="04">
    <div class="frag" data-frag="s04"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§04</span> Is the number any good</h2>
    <div class="prose">
      <p>"95% VaR" is a testable claim with precise content: the realised loss should exceed it on about 5% of days, and those exceedances should be scattered rather than bunched. An engine that reports the number and never checks the claim is asserting something it has no evidence for, and the failure is silent — an uncalibrated VaR looks exactly like a calibrated one until the day it matters.</p>
      <p><strong>Four</strong> statistics, because a model can fail in more ways than one test can tell apart — and the fourth was added because the first three were caught missing something. The whole analysis turns on one discipline, and it is enforced structurally rather than by care. <code>Var_backtest.rolling</code> hands the estimator <code>returns[t-w .. t-1]</code> and scores it against <code>returns[t]</code> — the day being forecast is not in the array the estimator receives, so it cannot be reached.</p>
    </div>
    <figure class="fig" data-fig="s04"></figure>
    <div class="prose">
      <p>The joint verdict rejects <data data-bind="battery.rejected">—</data> of nine configurations, and the duration test rejects two more that it passed. Two of those five are cases where one estimator passes and another fails on <em>identical</em> data. That is the point. A validation battery that has never failed anything is not evidence of anything; one where every estimator agrees is not telling you which to use; and one where every statistic agrees is not telling you what it cannot see.</p>
      <p class="absence">The live host's own track record is not here — the engine does not persist forecasts, so there is no coverage test of the live VaR; that needs persistence, which is a separate argument.</p>
    </div>
  </section>

  <!-- §05 — README.md "The estimator that is implemented and deliberately not used" -->
  <section class="arg" id="s05" data-section="05">
    <div class="frag" data-frag="s05"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§05</span> The estimator that is implemented and deliberately not used</h2>
    <div class="prose">
      <p>GARCH(1,1) is the obvious next step after EWMA. It adds the one thing EWMA has no notion of — <strong>mean reversion</strong>. With a single hand-set decay factor there is no long-run level to revert to; GARCH has one, and <code>alpha + beta</code>, the <em>persistence</em>, says how much of a volatility shock survives each period and therefore what its half-life is. That number is the entire reason to prefer it.</p>
      <p>It is implemented in <code>lib/vol_estimators.ml</code>, fitted by maximum likelihood with Engle variance targeting, and tested against a process it recovers correctly. It is <strong>not wired into the graph</strong>, and <code>make garch</code> is the measurement that says why.</p>
    </div>
    <figure class="fig" data-fig="s05"></figure>
    <div class="prose">
      <p><strong>This engine's return window is 60 observations.</strong> At 60 the persistence comes back at <data data-bind="garch.p60_mean">—</data> ± <data data-bind="garch.p60_sd">—</data> against a true <data data-bind="garch.truth">—</data>. Read both numbers: the standard deviation is roughly the size of the thing being estimated, so one fit carries almost no information — and the mean is <em>biased</em>, not merely noisy. A model that cannot tell a 34-day half-life from a 2-day one is not reporting a half-life.</p>
      <p>It seemed worth building the thing in order to find that out, and worth keeping it so the finding is reproducible rather than asserted. An absence that has been measured is a different claim from an absence that has not.</p>
    </div>
  </section>

  <!-- §06 — README.md "The same battery, real crises" -->
  <section class="arg" id="s06" data-section="06">
    <div class="frag" data-frag="s06"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§06</span> The same battery, real crises</h2>
    <div class="prose">
      <p>Everything above is scored against series whose regime was chosen by the person writing the test. That is the right way to <em>build</em> a coverage battery — it is the only setting where you know in advance which tests ought to reject — and it is not evidence that the model survives a real tail. Synthetic tail events are drawn from the distribution the author had in mind. Real ones are not, and the gap between those two sentences is most of what goes wrong with risk models.</p>
      <p><code>make backtest-crisis</code> changes exactly one thing: the data. Same 60-day window, same 95%, same three estimators, same <code>Var_backtest.rolling</code>. The method has to be visibly identical or the comparison says nothing.</p>
    </div>
    <figure class="fig" data-fig="s06a"></figure>
    <figure class="fig" data-fig="s06b"></figure>
    <figure class="fig" data-fig="s06c"></figure>
    <div class="prose">
      <p><strong>The joint verdict rejects nothing.</strong> Not the global financial crisis, not COVID, not 2022. The duration column rejects two rows, and the gap between those two facts is what this section is about. On the GFC window this book takes <data data-bind="crisis.gfc_burst">—</data> exceptions between 15 September and 7 October 2008 — seventeen sessions spanning Lehman and the TARP vote — and Christoffersen's independence statistic returns p = <data data-bind="crisis.gfc_hist_indep_p">—</data>. The test is not broken and it is not lying. It is a first-order Markov test. A burst that lands every third session is invisible to it.</p>
      <p>COVID is worse. <code>covid</code>/<code>parametric</code> takes <data data-bind="crisis.covid_par_burst">—</data> exceptions in a single 21-session window — ten times the independent expectation — with a joint p-value of <data data-bind="crisis.covid_par_joint_p">—</data>, which does not reject at 5%. <strong>The duration test says so: p = <data data-bind="crisis.covid_par_duration_p">—</data>, shape <data data-bind="crisis.covid_par_shape">—</data>.</strong> And it still does not catch the GFC. Three instruments, three different blind spots: adjacency, aggregation, and no distribution theory at all. Reading one of them alone is how a model breaching ten times in a month gets a passing grade.</p>
    </div>
  </section>

  <!-- §07 — README.md "What would break it" -->
  <section class="arg" id="s07" data-section="07">
    <div class="frag" data-frag="s07"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§07</span> What would break it</h2>
    <div class="prose">
      <p>Every other number here is backward-looking by construction. VaR summarises a distribution that has already been observed; a limit compares today's exposure to a line. Both share a blind spot that is not a flaw in the estimator but a property of the question — they can only speak about moves that have already happened somewhere in the return window. A scenario asks the other question.</p>
      <p>A price shock moves what the book is <em>worth</em>; a volatility shock moves what it is <em>expected to do</em>. Keeping the two separate is what stops a scenario from quietly answering a question nobody asked — and it is why shocking prices deliberately leaves the VaR <em>fraction</em> alone. The dollar VaR does move, because gross did.</p>
    </div>
    <figure class="fig" data-fig="s07"></figure>
    <div class="prose">
      <p>There is no scenario arithmetic anywhere in this repository, and that is the design decision in this module. Instead <code>Graph.fork</code> copies the engine, the shocks are written into the fork's input cells, and the answer is read out by exactly the nodes that produce the live one. The scenario is not a model of the engine; it is the engine, fed different inputs. <code>test_stress.ml</code> runs the entire suite and then asserts the live snapshot is unchanged field for field.</p>
    </div>
  </section>
  ```

  (continued in part (a′) below — the file is one insertion; the plan shows it in two blocks so each stays readable)

  **(a′) `web/index.html`, continued** — §08, §09 and the article's close, part of the same insertion above `<footer>`:

  ```html
  <!-- §08 — README.md "Watching it", "What's verified", "The same claims, over arbitrary inputs",
       "Coverage, and what it is not measuring", "CI runs on both platforms", "What happens when a limit breaks",
       and the eight invariants from docs/status.md, verbatim -->
  <section class="arg" id="s08" data-section="08">
    <div class="frag" data-frag="s08"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§08</span> Watching it</h2>
    <div class="prose">
      <p>That is <code>make demo</code> — the synthetic feed, no credentials, CVX going stale on schedule — on one small droplet behind Caddy with a Let's Encrypt certificate, restarting on its own after a crash or a reboot. It is up at three in the morning on a Sunday because the feed is generated rather than received. A second host, <code>live.ohcamel.ajaiupadhyaya.com</code>, is the same image against Alpaca and FRED: real prints, the real ten-year yield, real staleness. It sits behind a password, because it holds credentials and shows a real book.</p>
    </div>
    <figure class="fig" data-fig="s08a"></figure>
    <div class="prose">
      <p>The assertion in that suite worth naming is not the health route returning 200, which proves almost nothing. It is that <code>nodes_recomputed</code> in <code>/api/snapshot</code> <em>advances</em> between two reads two seconds apart — <data data-bind="smoke.nodes_delta">—</data> nodes, in this session — and that <code>/api/stream</code> delivers distinct frames spread across a twenty-second window rather than piled up at its end: <data data-bind="smoke.frames">—</data> of them. A frozen graph serves valid JSON forever. Those two checks are what distinguish a dashboard that is watching the market from one that rendered once and stopped.</p>
    </div>
    <figure class="fig" data-fig="s08b"></figure>
    <h3>What's verified</h3>
    <div class="prose">
      <p><code>make test</code> runs <data data-bind="verified.tests">—</data> tests, all hermetic — no network, no credentials, and nothing that waits on the wall clock. They cover the numerics against hand-computed values, the wire format, the alerting state machine, and the recomputation counts that make the graph's shape an assertion rather than a claim.</p>
      <p>Seven of them are worth knowing by name, because each catches a defect nothing else would. The <strong>Euler residual</strong> holds the decomposition to its own identity. The <strong>hedge test</strong> asserts a risk-reducing position does not breach a risk limit, which is what a stray absolute value would break. The <strong>lookahead test</strong> rebuilds each rolling window independently and demands the forecast match, so a backtest cannot see the day it is forecasting. The <strong>isolation test</strong> runs the whole scenario suite and then compares the live snapshot field for field, because a leaking fork produces numbers that are internally consistent and about the wrong world. The <strong>regime-break test</strong> asserts that the EWMA estimator reads a higher volatility than the equal-weighted one after a volatility shift is inserted partway through a synthetic series — the property, not the formula. The <strong>delta-hedged test</strong> builds a book that is flat in delta and asserts it still carries gamma and vega. And the <strong>two-clocks test</strong> asserts that advancing the staleness clock reprices no option and advancing the valuation clock touches no feed-liveness node.</p>
      <p>Four of those seven are <em>identities</em> rather than values — Euler, the hedge, lookahead, isolation — and <code>test/test_properties.ml</code> generalises them with qcheck: random books, random weights, random scenarios, 100 cases per property. The components sum to the total for <strong>any</strong> positive-semidefinite covariance matrix and <strong>any</strong> signed weight vector. The forecast is built from strictly prior days at <strong>any</strong> window size, series length and estimator. The live snapshot survives <strong>any</strong> compounded sequence of shocks. And the hedge property is constructed rather than picked. Adding a single <code>Float.abs</code> to <code>Attribution.component</code> fails three of the six properties and four example tests.</p>
    </div>
    <figure class="fig quoted" data-fig="s08c"></figure>
    <div class="prose">
      <p>The interesting thing about the number is that it is bimodal, and it should be read as two numbers rather than one. The left column is everything that computes a risk number. The right column is everything that talks to a network. That split is a design decision appearing in a metric, not a backlog: every test in this project is hermetic, so the code whose job is to hold a websocket open is exercised only as far as its pure parts go. CI builds on <code>ubuntu-latest</code> <em>and</em> <code>macos-latest</code>, and both legs also run the credential-free modes end to end, because a <code>printf</code> format string that only fails at run time is invisible to <code>make test</code>.</p>
      <p><code>lib/types.ml</code> makes <code>Price</code>, <code>Qty</code> and <code>Notional</code> abstract and mutually incompatible, so <code>price + qty</code> is a compile error rather than a plausible-looking number. <code>Contracts.t</code> is a distinct type from <code>Qty.t</code>, which is the units discipline earning its keep at the one place it bites hardest: one contract is a hundred shares, so a book holding "50" of something holds either 50 shares of delta or 5,000 depending on which type that 50 is. The multiplier has to be applied explicitly, in one function. Every limit crosses the wire with its unit.</p>
      <p>There are four kinds of limit and they are not four settings of one thing. <code>Gross_notional</code> caps exposure at any scope. <code>Value_at_risk</code> and <code>Max_drawdown</code> are portfolio-only, because neither decomposes. <code>Component_var</code> caps a scope's <em>share</em> of portfolio VaR and is valid everywhere, because that share is additive. <code>Greek_limit</code> caps <code>|gamma|</code> or <code>|vega|</code> and is valid at every scope: the book's gamma is the derivative of a sum, which <em>is</em> the sum of the derivatives. A Greek limit takes the <strong>magnitude</strong>; <code>Component_var</code> keeps the <strong>sign</strong>.</p>
      <p>The Basel zones are computed from the binomial rather than looked up, and reproduce the published 250-day table exactly: green 0–4, yellow 5–9, red 10+. That is asserted as a test. The deployment is a two-stage Dockerfile that fails the <em>build</em> if the runtime image is missing a shared object. When alerting is on it is edge-triggered with hysteresis, and lost data never clears an alert. Live mode marks the book from the last close during the REST backfill, through <code>Graph.set_price</code> and deliberately <em>not</em> <code>Graph.apply_tick</code> — a closing price is a real mark, but it is not evidence that the feed is alive. No unit test would have caught the bug that motivated it: gross read $217,590 against a true $459,266, with nothing on the page suggesting the number was wrong. Looking at the thing did.</p>
      <p>The math is written out once, densely and in standard notation, in <a href="https://github.com/ajaiupadhyaya/OhCamel/blob/main/docs/quant_notes.md"><code>docs/quant_notes.md</code></a> — every formula cross-referenced to the function that evaluates it.</p>
      <p class="demo-only" hidden>The kill-switch trip in the alert history was arranged: <code>nvda-cap</code> is set at $54,200 against a starting exposure of $54,000, so the first meaningful move crosses it and the whole path — edge-triggered alert, hysteresis on the way back down, the kill switch latching — happens within a few seconds of startup rather than never.</p>
      <h3>The invariants</h3>
      <ol class="invariants">
        <li>Every dependency is a graph edge. No node reads a global, a ref, or the network.</li>
        <li>No second implementation of exposure/equity/limit arithmetic. Counterfactuals go through <code>Graph.fork</code>.</li>
        <li>Units stay abstract. New money- or risk-shaped quantities get their own types with one named bridge.</li>
        <li>Pure numerics stay pure. Risk math lives in modules that do not know Incremental exists.</li>
        <li>A missing credential is fatal, not degraded. Never fall back to synthetic data in a mode that claims to be live.</li>
        <li>No order routing, ever. The kill switch stays a bool wired to nothing.</li>
        <li>Every new numeric module gets hand-derived test values. "Returns a number" is not a test.</li>
        <li>Comments explain <em>why</em>: the tension, the choice, and what breaks under the alternative.</li>
      </ol>
    </div>
  </section>

  <!-- §09 — README.md "What this is not", then the spec's "What the page does not show, and says so" -->
  <section class="arg" id="s09" data-section="09">
    <div class="frag" data-frag="s09"><p class="frag-list lbl"></p></div>
    <h2><span class="lbl">§09</span> What this is not</h2>
    <div class="prose">
      <p>There is no order routing and no execution — nothing here places, cancels or simulates a trade. There is no persistence: state lives in the running process, and a restart rebuilds the book from <code>book.sexp</code> and the feed. There is one broker, Alpaca, and one macro source, FRED.</p>
      <p>Nor is it a research platform. There is no strategy, no signal, no backtest of anything that could make money — <code>make backtest</code> validates the <em>risk model</em>, not a trading idea, and the distinction is the whole point of the mode. GARCH(1,1) <em>is</em> implemented and tested, and is deliberately not wired in. Nothing is optimised: the engine reports where risk is concentrated and never suggests what the weights should be, which is a different project with a different failure mode.</p>
      <p>The options path prices European contracts with Black-Scholes: no American exercise, no dividends, no term structure of rates, and no implied-volatility solve — vol is an input, because there is no chain to invert a price from. Portfolio vega is a parallel-shift number, reported alongside a tenor breakdown that shows what the parallel-shift sum hides — but it is not bucketed by strike, so skew risk is invisible. And options risk is <strong>off in live mode</strong>, stated rather than silently absent.</p>
      <p>It is a risk and limits engine, and it stops where a risk and limits engine should stop.</p>
      <h3>What this page does not show</h3>
      <ul class="not-shown">
        <li>The last smoke run's result. Nothing persists; the two assertions are re-run in the browser instead.</li>
        <li>Uptime as a percentage. It needs history nobody keeps.</li>
        <li>CPU share, Caddy's memory, request counts per route, certificate expiry. Not engine-observable; <code>docker stats</code> and Caddy's stdout have them.</li>
        <li>The live book from the public page, by name or by number. Gated, and the page does not route around the password.</li>
        <li>Options Greeks on either deployed book. No chain source; an invented surface would produce Greeks indistinguishable from real ones, so the figure is a synthetic one-name graph labelled as such.</li>
        <li>The live host's own VaR track record. The engine does not persist forecasts; validating itself is a separate argument.</li>
        <li>Microsecond timings from this host. <code>bench/</code> ships nowhere by design; the table is quoted from an M2 Pro with its date.</li>
        <li>The test count or coverage computed here. Dated constants, one of them pinned by a test.</li>
        <li>The CLI's stress table. The web suite runs on this host's book and its own return windows, and differs from the README's seeded figures by construction.</li>
        <li>A market calendar. The live host outside hours shows <span class="unknown">parked</span>, in the cannot-evaluate colour, and no more.</li>
        <li>Anything about orders, because there are none.</li>
      </ul>
    </div>
  </section>

  </article>
  ```

  **(b) `web/page.css`.** Append. The two tokens go into the **existing** `:root { … }` and `@media (prefers-color-scheme: dark) { :root { … } }` blocks Phase 1 moved verbatim — insert `--second` and `--shade` immediately after the `--mark:` line in each — so the test's count of two is a count of schemes, not of stray copies. Everything else is appended at the end of the file:

  ```css
    /* in :root, after --mark: #b8860b; */
    --second: #4a6d8c;
    --shade: rgba(22, 24, 26, 0.06);
    /* in the dark :root, after --mark: #d9a441; */
    --second: #7fa3c2;
    --shade: rgba(232, 235, 233, 0.06);
  ```

  ```css
  /* ---- the argument ------------------------------------------------------
     A document's discipline under the ledger: a 68ch reading column, figures
     wide beside or beneath it, rules not cards, captions that state the source.
     The one typographic distinction that did not exist before is between a
     number this process produced and a number it is quoting: .quoted sets a
     whole figure in --ink-soft, so a laptop's microseconds cannot pass for
     this droplet's. */
  #argument { max-width: 1180px; margin: 0 auto; padding: 8px 20px 40px; }
  .arg { padding: 28px 0 8px; border-top: 1px solid var(--rule); }
  .arg h2 { font-size: 22px; font-weight: 620; letter-spacing: -0.01em; margin: 14px 0 10px; }
  .arg h2 .lbl { margin-right: 10px; }
  .arg h3 { font-size: 15px; font-weight: 620; margin: 22px 0 6px; }
  .arg .prose { max-width: 68ch; font-size: 15px; line-height: 1.5; }
  .arg .prose p { margin: 0 0 12px; }
  .arg .prose code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
  .arg .absence, .arg .live-line, .arg .synthetic-line { color: var(--ink-soft); }
  .arg .synthetic-line { margin: 0 0 14px; }
  .arg data { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-variant-numeric: tabular-nums; font-size: 13px; }
  .arg data:not([value]) { color: var(--unknown); }

  /* Figures: caption above, title left and provenance right, a rule above and
     below, no border. Wide content scrolls inside its own box. */
  .fig { margin: 18px 0; padding: 8px 0 10px; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
  .fig figcaption { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; font-size: 12px; color: var(--ink-soft); margin-bottom: 8px; }
  .fig .cap-title { flex: 1 1 auto; }
  .fig .cap-prov { flex: 0 0 auto; }
  .prov { font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-soft); white-space: nowrap; }
  .prov[data-prov="live"] { color: var(--live); }
  .prov[data-prov="computing"] { color: var(--unknown); }
  .fig .fig-body { overflow-x: auto; }
  .fig .fig-notes { font-size: 12px; color: var(--ink-soft); margin-top: 6px; }
  .fig .fig-notes p { margin: 3px 0; }
  .fig table { border-collapse: collapse; font-size: 13px; }
  .fig td, .fig th { padding: 3px 10px 3px 0; text-align: right; vertical-align: top; white-space: nowrap; }
  .fig th { color: var(--ink-soft); font-weight: 400; font-size: 11px; }
  .fig td.k, .fig th.k { text-align: left; color: var(--ink-soft); }
  .fig td.v { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-variant-numeric: tabular-nums; }
  .fig tr.strip-row td { padding: 0 0 6px; }
  .fig tr.rejected td.verdict { color: var(--over); }
  .fig td.unknown, .fig .unknown { color: var(--unknown); }
  .quoted, .quoted td, .quoted th, .quoted .chart text { color: var(--ink-soft); fill: var(--ink-soft); }
  .compare { font-size: 12px; margin-top: 6px; }
  .compare.differs { color: var(--over); }
  pre.engine { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.4; white-space: pre; overflow-x: auto; padding: 8px 0; margin: 8px 0; border-top: 1px dashed var(--rule); }
  .row3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
  .twocol { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .est-links a { text-decoration: none; }
  .est-links a.on { text-decoration: underline; }
  .chart text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; fill: var(--ink-soft); }
  .chart text.name, .chart text.val, .chart text.end, .chart text.slot, .chart text.totallbl { fill: var(--ink); }
  .chart text.note, .chart text.val.neg { fill: var(--over); }
  .chart text.unknown { fill: var(--unknown); }
  .chart text.burst { fill: var(--ink-soft); font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; }
  .signed rect.worst { stroke: var(--ink); stroke-width: 0.75; }
  .invariants li, .not-shown li { margin: 0 0 6px; }
  .invariants { padding-left: 1.4em; }

  /* The fragment at the head of each section: the same renderer as Figure 1,
     compact, and below 700px a list of its node names instead of the SVG. */
  .frag { margin: 0 0 6px; overflow-x: auto; }
  .frag .frag-list { display: none; margin: 0; }
  .frag .frag-note { font-size: 11px; color: var(--ink-soft); margin: 2px 0 0; }

  /* Exactly one animation on this page, and it is the ledger's; nothing here
     adds a second. Under reduced motion the mark still appears -- it is a fact
     about the frame, not decoration -- and only the fade is dropped. */
  @media (prefers-reduced-motion: reduce) {
    .arg *, .frag * { animation: none !important; transition: none !important; }
  }
  @media (max-width: 900px) {
    .row3, .twocol { grid-template-columns: 1fr; }
  }
  @media (max-width: 700px) {
    .frag svg { display: none; }
    .frag .frag-list { display: block; }
    header { position: static; }
  }
  ```

  **(c) `web/quoted.json`.** Two tables the page quotes that Phase 1 did not transcribe, because Phase 1 transcribed the four tables the engine can also compute and these two it cannot: `bench/` ships nowhere by design, and the test count and coverage are dated constants. Insert both as new top-level members immediately before the closing `}` of the file (after the `"garch": { … }` block, with a comma after that block's `}`). `bench` is `README.md:76–81`, transcribed cell for cell. `verified` is copied from `lib/verified.ml` **as Phase 3 Task 9 left it** — `tests`, `covered` (= `coverage_covered`), `of` (= `coverage_lines`), `coverage_pct`, `dated` — not from the spec's `210` / `70.4` / `2026-08-25`, which are the values BEFORE Phases 1–3 added suites and library lines; Phase 3 Task 10 already rewrote `README.md:1071` and `docs/status.md:179,187` to the measured values, so the README agrees. The `per_file` table is `README.md:1151–1159` cell for cell. `coverage_pct` must be the same one-decimal literal `lib/verified.ml` holds (write `71.2`, not `71.20` or `71.19`): the test below pins it with `float 1e-9`. Read the five values first:

  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && grep -n 'let tests\|let coverage_covered\|let coverage_lines\|let coverage_pct\|let dated' lib/verified.ml
  ```

  and substitute them for the five `«…»` placeholders in the block below before saving it; the file is JSON and a placeholder left in would fail `test_quoted_parses_and_holds_the_four_tables` first.

  ```json
    "bench": {
      "note": "README.md, `make bench`. Apple M2 Pro, macOS 26.5, OCaml 5.2.1, Owl's C kernels at -O1. A quoted run rather than a gate: CI does not run this and the image does not contain bench/, so this host cannot reproduce it. Microseconds per event, incremental against a poll-and-recompute baseline built from the same pure functions.",
      "machine": "Apple M2 Pro, macOS 26.5, OCaml 5.2.1",
      "rows": [
        { "instruments": 10,  "incremental_us": 19.5,  "polled_us": 51.7,    "ratio": 2.7 },
        { "instruments": 100, "incremental_us": 89.1,  "polled_us": 3387.7,  "ratio": 38 },
        { "instruments": 400, "incremental_us": 492.7, "polled_us": 53426.8, "ratio": 108 }
      ]
    },

    "verified": {
      "note": "README.md `make test` and `make coverage`, and docs/status.md. Dated constants: the page cannot run the suite. lib/verified.ml holds the same numbers, measured by Phase 3, and test_embedded_assets.ml pins this block to it, so Phase 6 re-dates both or neither.",
      "tests": «Verified.tests»,
      "covered": «Verified.coverage_covered»,
      "of": «Verified.coverage_lines»,
      "coverage_pct": «Verified.coverage_pct, the same one-decimal literal»,
      "dated": "«Verified.dated»",
      "per_file": [
        { "file": "lib/history_buffer.ml", "pct": 94 }, { "file": "lib/alerts.ml", "pct": 37 },
        { "file": "lib/attribution.ml", "pct": 91 },    { "file": "lib/config.ml", "pct": 40 },
        { "file": "lib/graph.ml", "pct": 91 },          { "file": "lib/feed/alpaca_ws.ml", "pct": 39 },
        { "file": "lib/risk_metrics.ml", "pct": 90 },   { "file": "lib/feed/fred_client.ml", "pct": 47 },
        { "file": "lib/crisis_data.ml", "pct": 90 },    { "file": "lib/feed/alpaca_rest.ml", "pct": 50 },
        { "file": "lib/limits.ml", "pct": 88 },         { "file": "lib/server.ml", "pct": 50 },
        { "file": "lib/vol_estimators.ml", "pct": 87 }, { "file": "lib/types.ml", "pct": 47 },
        { "file": "lib/stress.ml", "pct": 85 },         { "file": "lib/options.ml", "pct": 75 },
        { "file": "lib/var_backtest.ml", "pct": 71 }
      ]
    }
  ```

  The `per_file` rows are in the README's reading order — left column and right column interleaved — so the page can print the same two-column table by taking them two at a time. Check the file still parses and still has no closing delimiter in it:

  ```bash
  python3 -c "import json; d=json.load(open('web/quoted.json')); print(sorted(d), len(d['verified']['per_file']), len(d['bench']['rows']))" && (grep -c -- '|ohcamel_json}' web/quoted.json; true)
  ```
  Expected: `['battery', 'bench', 'crisis', 'garch', 'machine', 'note', 'quoted_on', 'scaling', 'source', 'verified'] 17 3` then `0`.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -4 && eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && echo FMT-CLEAN
  ```
  Expected: `Test Successful`, and `FMT-CLEAN` (only OCaml and dune files are formatted; `web/` is outside `@fmt`).

  Then look at it once, because a stylesheet has no unit test: start the demo, open `http://localhost:8099/`, scroll below the history sparklines. Expected: nine ruled sections with `§01`…`§09` labels and the README headings, each with an empty `.frag` box above its heading and empty ruled `figure` slots (Tasks 13–14 fill them); every `<data>` reads `—` in the `--unknown` blue; in a window narrower than 700 px the header is no longer sticky. Toggle the OS colour scheme: the section rules and the `—` marks change with it.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && curl -s http://localhost:8099/ | grep -c 'class="arg"' && curl -s http://localhost:8099/ | grep -o 'data-bind=' | wc -l ; pkill -f "main.exe demo 8099"
  ```
  Expected: `9`, then `24` — the number of `<data>` elements Task 13 and Task 14 must bind, every one of which is listed in Task 13's binding table. (`grep -o … | wc -l` counts occurrences; `grep -c` would count lines, and several `<data>` elements share a line.)

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add web/index.html web/page.css web/quoted.json test/test_embedded_assets.ml && git commit -m "web: the nine sections cut from the README with their numbers left blank, because prose in HTML can be diffed against the document it claims to quote"
  ```

---

### Task 13: `web/argument.js`, part 1 — the data flow, provenance, computed-vs-quoted, `<data>` binding, fragments, and §01–§04

**Files:**
- Modify: `web/dashboard.js` (two one-line insertions, located by content)
- Modify: `web/argument.js` (replace Task 10's two-line stub wholesale)
- Test: `/tmp/ohcamel-phase5/pw/check-argument-1.mjs` (Playwright, dev-machine only; the OCaml structure test from Task 10 already pins this file's position in the page)

**Interfaces:**
- Consumes: `window.OhCamelFormat.{money, pct, el, vegaPoint}` (Task 10); `window.OhCamelCharts.{dotLine, pairedBars, tenorBuckets, strip}` (Tasks 10–11); `window.OhCamelGraph.{render(container, topology, {filter?, compact?, inspector?}) -> handle, filter(topology, {nodes?, families?}) -> sub-topology, closure(topology, names, 'down'|'up') -> Set}` and `handle.light(names)` (Phase 4 Task 12, which is `RULES.md`'s contract; below 700 px `window.OhCamelGraph` is still defined but every fragment renders as its node-name list, and the same list is what a fragment falls back to if `render` throws); `GET /api/reports` (Task 7; fields as Tasks 2–4 encode them), `GET /api/reports/garch` (Task 6), `GET /api/graph` (`nodes[].{name, family}`, `counts.named` — Phase 4), `GET /api/ops` (`mode` — Phase 2); the SSE frame's `recomputed: [{name, n}]`, `nodes_recomputed`, `positions[].{symbol, weight, component_var, marginal, standalone, risk_share, risk_over_money}`, `sectors[].{sector, component_var, risk_share}`, `by_node`, `euler_residual`, `attribution_covariance` (today's fields plus Phase 4's, by the spec's names); `/api/history`'s `appended` (exists — `lib/server.ml:130`); the `<script id="quoted" type="application/json">` block, which Task 10(e) of this plan added to the `lib/dune` rule between `(cat ../web/index.html)` and the `<script>` echo (Phase 1 deliberately did not: it would have changed the page's bytes under Phase 1's gate).
- Produces: two DOM events from `dashboard.js` — `ohcamel:frame` (`detail` = the parsed frame) and `ohcamel:history` (`detail` = the parsed `/api/history` body) — so the page has **one** EventSource and one history fetch, and `argument.js` never opens a second subscriber the footer would count; `window.OhCamelArgument = { load, state, bind, compare, provenance, fragment, fig, on, renderers }`, where `load()` is idempotent and returns a Promise of `state` once `/api/ops`, `/api/graph` and `/api/reports` have been fetched and §01–§04 drawn; the 24 `<data>` bindings by name; the fixed provenance wordings `LIVE · THIS HOST`, `COMPUTED · AT STARTUP · HH:MM:SSZ`, `COMPUTED · AT STARTUP · SYNTHETIC BOOK, NOT THIS HOST'S`, `QUOTED · README · M2 PRO · 2026-09`, `SYNTHETIC`; the two computed-vs-quoted wordings Phase 6's Task 7 reads verbatim: `agrees with the README's table to four decimals` and `computed on this host (<system>, <architecture>): differs from the README's table (<machine>) in N cells: …`.

- [ ] **Step 1: Write the failing test.** Create `/tmp/ohcamel-phase5/pw/check-argument-1.mjs`:

  ```js
  // §01-§04 rendered against a real demo server, with real frames.
  import { chromium } from "playwright";
  const base = process.env.BASE || "http://localhost:8099";
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/", { waitUntil: "networkidle" });
  await page.evaluate(() => window.OhCamelArgument.load());
  // Two frames, so §01(a) has a tally and §02 has a book to draw.
  await page.waitForFunction(() => window.OhCamelArgument.state.frames.length >= 2, null, { timeout: 20000 });
  const got = await page.evaluate(() => {
    const A = window.OhCamelArgument;
    const $ = (sel) => document.querySelectorAll(sel);
    const n = (sel) => $(sel).length;
    const text = (sel) => (document.querySelector(sel) || { textContent: "" }).textContent;
    const out = {};
    out.events = [A.state.frames.length >= 2, A.state.history !== null];
    out.fragLists = ["s01", "s02", "s03", "s04"].filter((id) => text('.frag[data-frag="' + id + '"] .frag-list').length > 0).length;
    out.s01a = [n('[data-fig="s01a"] table tr'), text('[data-fig="s01a"] .cap-prov')];
    out.s01b = [n('[data-fig="s01b"] svg.dotline:not(.quoted)'), n('[data-fig="s01b"] path.series'), /^(agrees|computed on this host)/.test(text('[data-fig="s01b"] .compare')), text('[data-fig="s01b"] .cap-prov').indexOf("COMPUTED · AT STARTUP") === 0];
    out.s01c = [n('[data-fig="s01c"] svg.dotline.quoted'), n('[data-fig="s01c"] text.ratio'), text('[data-fig="s01c"] .cap-prov').indexOf("QUOTED · README") === 0];
    out.s01data = n("#s01 data[value]");
    out.s02 = [n('[data-fig="s02"] svg.paired'), n('[data-fig="s02"] text.name') === A.state.frame.positions.length, /Euler residual/.test(text('[data-fig="s02"] .residual')), text('[data-fig="s02"] .cap-prov')];
    out.s03 = [text("#s03 .synthetic-line").indexOf("SYNTHETIC.") === 0, n('[data-fig="s03c"] svg.tenor'), n('[data-fig="s03a"] .lim') === A.state.reports.options.limits.length, n("#s03 data[value]")];
    out.s04 = [n('[data-fig="s04"] table tr.row'), n('[data-fig="s04"] svg.strip'), n('[data-fig="s04"] line.mark'), n('[data-fig="s04"] pre.engine'), n('[data-fig="s04"] .compare'), n("#s04 data[value]")];
    out.unbound = Array.from($("#s01 data, #s02 data, #s03 data, #s04 data")).filter((d) => !d.hasAttribute("value")).map((d) => d.getAttribute("data-bind"));
    return out;
  });
  const expect = {
    events: [true, true],
    fragLists: 4,
    s01a: [3, "LIVE · THIS HOST"],
    s01b: [1, 2, true, true],
    s01c: [1, 3, true],
    s01data: 8,
    s02: [1, true, true, "LIVE · THIS HOST"],
    s03: [true, 1, true, 3],
    s04: [9, 9, 3, 1, 1, 1],
    unbound: [],
  };
  const bad = errors.map((e) => "pageerror: " + e);
  for (const k of Object.keys(expect)) {
    if (JSON.stringify(got[k]) !== JSON.stringify(expect[k])) bad.push(k + ": expected " + JSON.stringify(expect[k]) + ", got " + JSON.stringify(got[k]));
  }
  await browser.close();
  if (bad.length) { console.error("FAIL\n  " + bad.join("\n  ")); process.exit(1); }
  console.log("ARGUMENT-1 OK");
  ```

  `s01a` is three rows — a header row and `tick` / `bar`. `s04`'s `line.mark` is three because the day-600 regime boundary is drawn on the three `vol-regime` rows only. `s01data` is the eight §01 bindings, `s03` ends with its three, `s04` with its one.

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-argument-1.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected failure: `TypeError: Cannot read properties of undefined (reading 'load')` from the first `evaluate`, because `window.OhCamelArgument` does not exist.

- [ ] **Step 3: Minimal implementation, part (a) — the two hooks in `web/dashboard.js`.** By content, because Phase 4 edits the same file. The line

  ```js
      try { render(JSON.parse(e.data)); }
  ```

  becomes

  ```js
      try {
        var frame = JSON.parse(e.data);
        render(frame);
        // The argument below reads the same frames. One EventSource per tab,
        // because the footer counts open subscribers and a second one would
        // make every reader look like two.
        document.dispatchEvent(new CustomEvent("ohcamel:frame", { detail: frame }));
      }
  ```

  and, in `refreshHistory`, the line `      .then(function (h) { renderHistory(h); })` becomes

  ```js
        .then(function (h) {
          renderHistory(h);
          document.dispatchEvent(new CustomEvent("ohcamel:history", { detail: h }));
        })
  ```

  Both anchor lines are byte-identical after Phase 4: its Task 13 rewrites `render` and the ledger renderers but states that the stream hookup (`var src = new EventSource("/api/stream");` and this `try` line) and `refreshHistory` are untouched, and its Task 14 adds a `/api/ops` poll beside them without touching either.

  **(b) `web/argument.js`, the core.** Replace the stub with this; part (c) appends the section renderers to the same IIFE, above its final `window.OhCamelArgument = …` line.

  ```js
  // web/argument.js -- the argument below the graph: nine sections in the
  // README's order, each headed by a fragment of Figure 1, each captioned
  // with where its numbers came from.
  //
  // Three provenances and no fourth. LIVE is read off the frames the ledger
  // already receives. COMPUTED is /api/reports and /api/reports/garch, which
  // this process produced at startup. QUOTED is web/quoted.json -- the
  // README's numbers -- set in the soft ink. Where a table is both computed
  // and quoted the page prints one line saying whether they agree.
  //
  // The client tallies and formats. It never recomputes risk: closures come
  // from the served topology, shares and ratios from the wire, bursts from
  // the encoder. The two arithmetic operations on a risk number in this file
  // -- an absolute value for a share of money, a reciprocal for one sentence
  // -- are named where they happen.
  (function () {
    "use strict";
    var F = window.OhCamelFormat, C = window.OhCamelCharts;
    var G = window.OhCamelGraph || null;
    var quoted = JSON.parse(document.getElementById("quoted").textContent);

    var state = {
      ops: null, graph: null, reports: null, garch: null, stress: null,
      frame: null, frames: [], history: null, loadedAt: null,
    };
    var on = { frame: [], history: [], garch: [] };
    var renderers = [];
    var fragments = {};
    var lastRaw = null;

    // ---- the feeds: dashboard.js's one EventSource, re-broadcast as events --
    document.addEventListener("ohcamel:frame", function (ev) {
      var f = ev.detail;
      var raw = JSON.stringify(f);
      var rec = (f.recomputed || []);
      state.frame = f;
      state.frames.push({
        at: Date.now(),
        size: rec.length,
        bar: rec.some(function (r) { return r.name === "covariance"; }),
        nodes_recomputed: f.nodes_recomputed,
        distinct: raw !== lastRaw,
      });
      lastRaw = raw;
      if (state.frames.length > 600) state.frames.shift();
      var lit = rec.map(function (r) { return r.name; });
      Object.keys(fragments).forEach(function (k) {
        if (fragments[k] && fragments[k].light) fragments[k].light(lit);
      });
      on.frame.forEach(function (fn) { fn(f); });
    });
    document.addEventListener("ohcamel:history", function (ev) {
      state.history = ev.detail;
      on.history.forEach(function (fn) { fn(ev.detail); });
    });

    function getJSON(path) {
      return fetch(path).then(function (r) {
        if (!r.ok) throw new Error(path + " " + r.status);
        return r.json();
      });
    }

    // /api/reports is 60 KB and is fetched once, when §01 approaches the
    // viewport; a reader who never scrolls below the ledger never pays for it.
    var loading = null;
    function load() {
      if (loading) return loading;
      state.loadedAt = Date.now();
      loading = Promise.all([
        getJSON("/api/ops").catch(function () { return null; }),
        getJSON("/api/graph").catch(function () { return null; }),
        getJSON("/api/reports"),
      ]).then(function (r) {
        state.ops = r[0]; state.graph = r[1]; state.reports = r[2];
        renderers.forEach(function (draw) { draw(); });
        pollGarch();
        return state;
      });
      return loading;
    }
    // The one poll on this page besides the footer's: the study's state is
    // not a graph change and has no Ivar to park on. Five seconds, and only
    // while it is still computing.
    function pollGarch() {
      getJSON("/api/reports/garch").then(function (g) {
        state.garch = g;
        on.garch.forEach(function (fn) { fn(g); });
        if (g.status !== "done") setTimeout(pollGarch, 5000);
      }).catch(function () { setTimeout(pollGarch, 5000); });
    }
    var first = document.getElementById("s01");
    if (first && "IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); load(); }
      }, { rootMargin: "800px 0px" });
      io.observe(first);
    } else {
      load();
    }

    // ---- formatting ---------------------------------------------------------
    function clock(iso) { return iso ? String(iso).slice(11, 19) + "Z" : "—"; }
    function fixed(x, dp) { return x === null || x === undefined || !isFinite(x) ? null : Number(x).toFixed(dp); }
    function num(x) { return x === null || x === undefined || !isFinite(x) ? null : Number(x).toLocaleString("en-US"); }
    function us(v) { return v < 1000 ? fixed(v, 1) + " µs" : fixed(v / 1000, 1) + " ms"; }

    // ---- provenance: three tags, fixed wordings ------------------------------
    function provenance(kind, text) {
      var s = F.el("span", "prov", text);
      s.setAttribute("data-prov", kind);
      return s;
    }
    function liveTag(extra) { return provenance("live", "LIVE · THIS HOST" + (extra ? " · " + extra : "")); }
    // On the live origin every COMPUTED figure was computed on the CLI's
    // synthetic book and seeds, not on the book the ledger shows, and the tag
    // says so instead of a time -- a time would read as "this book, then".
    function computedTag(prefix) {
      var text = state.ops && state.ops.mode === "live"
        ? "COMPUTED · AT STARTUP · SYNTHETIC BOOK, NOT THIS HOST'S"
        : "COMPUTED · AT STARTUP · " + clock(state.reports.computed_at);
      return provenance("computed", (prefix ? prefix + " · " : "") + text);
    }
    function quotedTag(extra) {
      var machine = String(quoted.machine).split(",")[0].replace(/^Apple /, "").toUpperCase();
      return provenance("quoted", "QUOTED · README · " + machine + " · " + String(quoted.quoted_on).slice(0, 7) + (extra ? " · " + extra : ""));
    }

    // A figure: caption above with the title left and the provenance right,
    // the body, then notes. Re-entrant, so a LIVE figure is rebuilt per frame.
    function fig(id, title, prov) {
      var el = document.querySelector('figure.fig[data-fig="' + id + '"]');
      while (el.firstChild) el.removeChild(el.firstChild);
      var cap = F.el("figcaption");
      cap.appendChild(F.el("span", "cap-title", title));
      var slot = F.el("span", "cap-prov");
      slot.appendChild(prov);
      cap.appendChild(slot);
      el.appendChild(cap);
      var body = F.el("div", "fig-body");
      var notes = F.el("div", "fig-notes");
      el.appendChild(body);
      el.appendChild(notes);
      return {
        el: el, body: body, notes: notes,
        note: function (text, cls) { var p = F.el("p", cls, text); notes.appendChild(p); return p; },
      };
    }

    // An HTML table in the ledger's td.k / td.v idiom. cols: [{label, cls?,
    // fmt}] where fmt(row) returns text, or null for "not yet", which renders
    // as an em dash in the cannot-evaluate colour and never as zero.
    function table(container, cols, rows, rowClass) {
      var t = F.el("table");
      var head = F.el("tr");
      cols.forEach(function (c) { head.appendChild(F.el("th", c.cls || "", c.label)); });
      t.appendChild(head);
      rows.forEach(function (r) {
        var tr = F.el("tr", "row" + (rowClass ? " " + rowClass(r) : ""));
        cols.forEach(function (c) {
          var v = c.fmt(r);
          var td = F.el("td", (c.cls || "v") + (v === null ? " unknown" : ""), v === null ? "—" : v);
          if (c.tdClass) td.classList.add(c.tdClass(r));
          tr.appendChild(td);
        });
        t.appendChild(tr);
      });
      container.appendChild(t);
      return t;
    }

    // ---- <data> binding -----------------------------------------------------
    // A number in the prose is filled from the same JSON as the figure that
    // shows it, or it stays an em dash. The value attribute is the raw number;
    // its absence is what page.css colours --unknown.
    function bind(name, text, raw) {
      document.querySelectorAll('data[data-bind="' + name + '"]').forEach(function (d) {
        if (text === null || text === undefined) { d.textContent = "—"; d.removeAttribute("value"); return; }
        d.textContent = text;
        d.setAttribute("value", raw === undefined ? text : String(raw));
      });
    }

    // ---- computed against quoted ---------------------------------------------
    // Cell by cell, at the precision the README prints each cell: a p-value to
    // four places, a Weibull shape to two, a GARCH mean to three, nodes per
    // tick to one. Half a unit in the last printed place is the tolerance,
    // because the README's cell IS the rounded number. Strings and integers
    // must match exactly; two nulls agree ("does not apply" on both sides).
    function same(a, b, dp) {
      var an = a === null || a === undefined, bn = b === null || b === undefined;
      if (an || bn) return an && bn;
      if (typeof a === "number" && typeof b === "number") {
        return dp === null ? a === b : Math.abs(a - b) <= 0.5 * Math.pow(10, -dp) + 1e-12;
      }
      return String(a) === String(b);
    }
    function show(v, dp) { return v === null || v === undefined ? "—" : typeof v === "number" && dp !== null ? Number(v).toFixed(dp) : String(v); }
    function compare(opts) {
      var index = {};
      opts.quoted.forEach(function (q) { index[opts.qkey(q)] = q; });
      var diffs = [];
      opts.computed.forEach(function (r) {
        var k = opts.key(r), q = index[k];
        if (!q) { diffs.push(k + " (no README row)"); return; }
        opts.cells.forEach(function (c) {
          var a = typeof c.c === "function" ? c.c(r) : r[c.c];
          if (!same(a, q[c.q], c.dp)) diffs.push(k + " " + c.q + " " + show(a, c.dp) + " ≠ " + show(q[c.q], c.dp));
        });
      });
      var platform = state.reports.platform || {};
      var readme = String(quoted.machine).split(",").slice(1).join(",").trim();
      var text = diffs.length === 0
        ? "agrees with the README's table to four decimals"
        : "computed on this host (" + platform.system + ", " + platform.architecture + "): differs from the README's table (" + readme + ") in " + diffs.length + " cells: " + diffs.join("; ");
      var el = F.el("p", "compare" + (diffs.length ? " differs" : ""), text);
      el.setAttribute("data-agrees", diffs.length === 0 ? "true" : "false");
      return { agrees: diffs.length === 0, diffs: diffs, text: text, el: el };
    }

    // ---- the fragment at the head of each section ---------------------------
    // The same renderer as Figure 1, filtered to the nodes the section
    // interrogates, compact, no inspector; lit per frame from the same
    // recomputed set. Below 700px page.css hides the SVG and shows the name
    // list, which is also what renders if graph.js is absent.
    function fragment(id, spec, note) {
      var box = document.querySelector('.frag[data-frag="' + id + '"]');
      var list = box.querySelector(".frag-list");
      var names = (spec.nodes || []).slice();
      if (G && state.graph) {
        var sub = G.filter(state.graph, spec);
        names = sub.nodes.map(function (n) { return n.name; });
        var wrap = F.el("div", "frag-svg");
        box.insertBefore(wrap, list);
        fragments[id] = G.render(wrap, sub, { compact: true, inspector: false });
      }
      list.textContent = names.join(" · ");
      if (note) box.appendChild(F.el("p", "frag-note lbl", note));
      return names;
    }
    function inputNames(prefix) {
      if (!state.graph) return [];
      return state.graph.nodes
        .filter(function (n) { return n.name.indexOf(prefix) === 0; })
        .map(function (n) { return n.name; });
    }
  ```

  Part (b) ends there: the IIFE is still open, `window.OhCamelArgument` is not yet assigned, and nothing has been pushed onto `renderers`. Part (c) appends the section renderers into the same IIFE and part (c′) closes it.

- [ ] **Step 3: Minimal implementation, part (c) — §01 and §02.** Append to `web/argument.js`, inside the IIFE, immediately after `inputNames`. Every element class named here is one the Step 1 test counts; where a count is not obvious it is derived beside the code.

  ```js
    // Node names for a fragment: exact names plus every node whose name starts
    // with one of the prefixes. Names the served topology does not have are
    // simply absent from the filter's result -- a live book with no options
    // has no greeks:ID, and the fragment says so rather than failing.
    function pick(exact, prefixes) {
      var names = exact.slice();
      (prefixes || []).forEach(function (p) { names = names.concat(inputNames(p)); });
      return { nodes: names };
    }
    // The estimator key the charts colour by, from the wire's kind.
    function estKey(est) { return est.kind === "parametric_ewma" ? "ewma" : est.kind; }

    // ---- §01 ----------------------------------------------------------------
    // (a) LIVE: the sizes of the recomputed sets since load, as two shapes. A
    // frame is a bar when `covariance` is in its set (a return window moved),
    // a tick otherwise. There is no clock-shaped frame to tally: a clock
    // advance that flips no status emits no frame, and the note says so.
    function drawS01a() {
      var named = state.graph && state.graph.counts ? state.graph.counts.named : null;
      var f = fig("s01a", "Measured while you watched — " + state.frames.length + " frames since load", liveTag());
      var shapes = [
        { shape: "tick", frames: state.frames.filter(function (x) { return !x.bar; }) },
        { shape: "bar", frames: state.frames.filter(function (x) { return x.bar; }) },
      ];
      var sizes = function (fs) { return fs.map(function (x) { return x.size; }); };
      table(f.body, [
        { label: "shape", cls: "k", fmt: function (r) { return r.shape; } },
        { label: "frames", fmt: function (r) { return String(r.frames.length); } },
        { label: "nodes ran (mean)", fmt: function (r) {
            if (!r.frames.length) return null;
            var s = sizes(r.frames).reduce(function (a, b) { return a + b; }, 0);
            return fixed(s / r.frames.length, 1) + (named ? " of " + named : "");
          } },
        { label: "smallest", fmt: function (r) { return r.frames.length ? String(Math.min.apply(null, sizes(r.frames))) : null; } },
        { label: "largest", fmt: function (r) { return r.frames.length ? String(Math.max.apply(null, sizes(r.frames))) : null; } },
      ], shapes);
      f.note("a clock advance that flips no status is cut off at feed:S and emits no frame; the six feed:S notes ride on the next tick's frame");
    }
    // (b) COMPUTED: the scaling probe, nodes in graph against nodes per tick.
    function drawS01b() {
      var sc = state.reports.scaling;
      var rows = sc.rows;
      var f = fig("s01b", "The probe — nodes in graph against nodes per tick, at " + rows.map(function (r) { return r.instruments; }).join(" / ") + " names", computedTag());
      C.dotLine(f.body, {
        x: rows.map(function (r) { return r.instruments; }),
        series: [
          { name: "nodes in graph", values: rows.map(function (r) { return r.nodes_in_graph; }), stroke: "var(--ink-soft)" },
          { name: "nodes per tick", values: rows.map(function (r) { return r.nodes_per_tick; }), stroke: "var(--ink)" },
        ],
        y: { unit: "count" },
      });
      f.note(sc.ticks + " set_price ticks per size, seed " + sc.seed + ", not this book; " + num(sc.cost_nodes_recomputed) + " node recomputations charged to the process-wide counter before the first frame");
      f.notes.appendChild(compare({
        computed: rows, quoted: quoted.scaling.rows,
        key: function (r) { return String(r.instruments); }, qkey: function (q) { return String(q.instruments); },
        cells: [
          { c: "nodes_in_graph", q: "nodes_in_graph", dp: null },
          { c: "nodes_per_tick", q: "nodes_per_tick", dp: 1 },
          { c: "if_polled", q: "if_polled", dp: null },
        ],
      }).el);
      rows.forEach(function (r) { bind("scaling.per_tick_" + r.instruments, fixed(r.nodes_per_tick, 1), r.nodes_per_tick); });
    }
    // (c) QUOTED: the bench table, log y, in the soft ink. Nothing here is
    // this host's -- bench/ is not in the image -- and the tag says so.
    function drawS01c() {
      var rows = quoted.bench.rows;
      var f = fig("s01c", "Microseconds per event, incremental against a poll-and-recompute baseline", quotedTag());
      C.dotLine(f.body, {
        x: rows.map(function (r) { return r.instruments; }),
        series: [
          { name: "incremental", values: rows.map(function (r) { return r.incremental_us; }), stroke: "var(--ink-soft)" },
          { name: "polled", values: rows.map(function (r) { return r.polled_us; }), stroke: "var(--ink-faint)", dashed: true },
        ],
        y: { log: true, unit: "µs" }, quoted: true,
        ratios: rows.map(function (r) { return r.ratio + "×"; }),
      });
      f.note("bench/ is not in the image and this host cannot reproduce it; " + quoted.bench.machine);
      var by = {};
      rows.forEach(function (r) { by[r.instruments] = r; });
      bind("bench.polled_400_ms", fixed(by[400].polled_us / 1000, 1), by[400].polled_us / 1000);
      bind("bench.ratio_400", by[400].ratio + "×", by[400].ratio);
      [10, 100, 400].forEach(function (n) { bind("bench.inc_" + n, us(by[n].incremental_us), by[n].incremental_us); });
    }
    renderers.push(function () {
      fragment("s01", pick(["gross_exposure", "net_exposure", "equity", "current_drawdown", "breaches", "covariance"], ["price[", "exposure:"]),
        "covariance sits beside the path a tick takes, and a tick never reaches it");
      drawS01a(); drawS01b(); drawS01c();
    });
    on.frame.push(function () { if (state.reports) drawS01a(); });

    // ---- §02 ----------------------------------------------------------------
    // LIVE, rebuilt from the frame at most twice a second. The shares and the
    // ratio are the encoder's (risk_share, risk_over_money); the ONE absolute
    // value in this file is |weight|, because a share of money has no sign,
    // and the ONE reciprocal is 1/diversification for a sentence.
    var s02At = 0;
    function drawS02() {
      var fr = state.frame;
      if (!fr || !fr.positions) return;
      var now = Date.now();
      if (now - s02At < 500) return;
      s02At = now;
      var bn = fr.by_node || {};
      var f = fig("s02", "Where the risk is — money against risk, by name", liveTag());
      var rows = fr.positions.map(function (p) {
        return { name: p.symbol, money: p.weight === null || p.weight === undefined ? null : Math.abs(p.weight), risk: p.risk_share, standalone: p.standalone, p: p };
      });
      var sum = function (k) { var s = 0, any = false; fr.positions.forEach(function (p) { if (p[k] !== null && p[k] !== undefined) { s += p[k]; any = true; } }); return any ? s : null; };
      var pv = bn.parametric_var, vn = bn.var_notional;
      f.note("shares sum to the parametric VaR, which decomposes — " + (sum("component_var") === null ? "warming up" : F.money(sum("component_var"))) + (pv === null || pv === undefined ? "" : " (" + F.pct(pv, 2) + " of gross)") + "; the headline var_notional " + (vn === null || vn === undefined ? "—" : F.money(vn)) + " is historical and does not", "lbl");
      C.pairedBars(f.body, rows);
      var cols = [
        { label: "", cls: "k", fmt: function (r) { return r.name; } },
        { label: "of money", fmt: function (r) { return r.money === null ? null : F.pct(r.money, 1); } },
        { label: "marginal", fmt: function (r) { return r.p.marginal === null || r.p.marginal === undefined ? null : fixed(r.p.marginal, 4); } },
        { label: "standalone", fmt: function (r) { return r.standalone === null || r.standalone === undefined ? null : fixed(r.standalone, 4); } },
        { label: "component VaR", fmt: function (r) { return r.p.component_var === null || r.p.component_var === undefined ? null : F.money(r.p.component_var); } },
        { label: "of risk", fmt: function (r) { return r.risk === null || r.risk === undefined ? null : F.pct(r.risk, 1); } },
        { label: "risk/money", fmt: function (r) { return r.p.risk_over_money === null || r.p.risk_over_money === undefined ? null : fixed(r.p.risk_over_money, 2) + "×"; } },
      ];
      var totals = { name: "total", money: sum("weight") === null ? null : Math.abs(sum("weight")), risk: sum("risk_share"), standalone: null, p: { marginal: null, component_var: sum("component_var"), risk_over_money: null } };
      var sectors = (fr.sectors || []).map(function (s) {
        return { name: s.sector, money: null, risk: s.risk_share, standalone: null, p: { marginal: null, component_var: s.component_var, risk_over_money: null } };
      });
      table(f.body, cols, rows.concat([totals]).concat(sectors), function (r) { return r.name === "total" ? "total" : "row"; });
      var dr = bn.diversification_ratio;
      f.note(dr === null || dr === undefined ? "diversification ratio — warming up"
        : "diversification ratio " + fixed(dr, 2) + "× — the book carries " + F.pct(1 / dr, 0) + " of the volatility its positions would if they all moved together");
      f.note("decomposition reads the " + (fr.attribution_covariance === "ewma" ? "EWMA" : "equal-weighted") + " covariance, fixed at construction");
      var res = fr.euler_residual;
      f.note(res === null || res === undefined ? "Euler residual — warming up"
        : "Euler residual Σ component − σₚ = " + Number(res).toExponential(1) + " on this frame (tests tolerate 1e-9)", "residual");
    }
    renderers.push(function () {
      fragment("s02", pick(["weights", "covariance", "attribution", "component_var_map", "component_var_sector_map", "diversification_ratio"], []));
      drawS02();
    });
    on.frame.push(function () { if (state.reports) drawS02(); });
  ```

  `table()` gives every data row the class `row` plus the row-class function's word, so `s02`'s `text.name` count is the SVG's — one per position — while the HTML table below it also carries the total and the sector rows; the test counts the SVG.

- [ ] **Step 3: Minimal implementation, part (c′) — §03, §04, and the IIFE's close.** Append to `web/argument.js` directly after part (c), still inside the IIFE, then close it with the `window.OhCamelArgument` assignment shown at the end. The breach record read here is `json_of_breach`'s (exists — `lib/server.ml:88–110`: `name, scope, unit, observed, threshold, excess, breached, utilisation`) plus the `text` Task 4 appends.

  ```js
    // ---- §03 ----------------------------------------------------------------
    // The ledger's .lim form. The engine's sentence IS the line; the record's
    // fields only decide its colour, so the page cannot re-compose a breach.
    function limLine(container, l) {
      var p = F.el("p", "lim" + (l.breached ? " over" : ""), l.text);
      container.appendChild(p);
      return p;
    }
    function walkTable(container, states) {
      table(container, [
        { label: "", cls: "k", fmt: function (s) { return s.label; } },
        { label: "delta-equivalent", fmt: function (s) { return F.money(s.delta_equivalent); } },
        { label: "gamma", fmt: function (s) { return fixed(s.gamma, 1); } },
        { label: "vega $/pt", fmt: function (s) { return F.money(F.vegaPoint(s.vega)); } },
      ], states);
    }
    function drawS03() {
      var o = state.reports.options;
      document.querySelector("#s03 .synthetic-line").textContent =
        "SYNTHETIC. σ(m) = " + o.surface.formula + ", invented in the CLI and nowhere in lib/. Neither deployed book holds options.";
      var s = o.setup;
      var a = fig("s03a", "Walk one — the option leg, then the hedge", computedTag(o.label));
      a.body.appendChild(F.el("p", "lbl", s.underlying + " spot " + F.money(s.spot) + " · " + num(s.strike) + " " + s.right + " · " + num(s.expiry_days) + " d · " + num(s.contracts) + " × " + num(s.multiplier) + " · σ " + F.pct(s.implied_vol, 1) + " · r " + F.pct(s.rate, 0)));
      walkTable(a.body, o.walk_hedge);
      a.note("the hedge is " + num(o.hedge_shares) + " shares; the engine computed the ratio");
      a.note("LIMITS", "lbl");
      o.limits.forEach(function (l) { limLine(a.notes, l); });
      var b = fig("s03b", "Walk two — the same book, " + num(o.clock_advance_days) + " days later", computedTag(o.label));
      walkTable(b.body, o.walk_clock);
      b.note("theta's effect, not a theta number; the engine never publishes theta");
      var cal = o.calendar;
      var c = fig("s03c", "The calendar spread — vega by tenor, and the parallel-shift total", computedTag(o.label));
      C.tenorBuckets(c.body, cal.buckets.map(function (x) { return { bucket: x.bucket, vega: F.vegaPoint(x.vega) }; }), F.vegaPoint(cal.portfolio_vega));
      c.note("near " + cal.near.id + " " + num(cal.near.days) + " d × " + num(cal.near.contracts) + " · far " + cal.far.id + " " + num(cal.far.days) + " d × " + num(cal.far.contracts) + " · gamma " + fixed(cal.portfolio_gamma, 1));
      c.note("vega per vol point: the wire carries dollars per 1.00 of vol and the page divides by 100 here, once, in OhCamelFormat.vegaPoint");
      bind("options.hedge_shares", num(o.hedge_shares), o.hedge_shares);
      var after = o.walk_clock[o.walk_clock.length - 1];
      bind("options.clock_delta", F.money(after.delta_equivalent), after.delta_equivalent);
      bind("options.calendar_gamma", fixed(cal.portfolio_gamma, 1), cal.portfolio_gamma);
    }
    // The closing line is LIVE: what the book above actually holds.
    function drawS03live() {
      var el = document.querySelector("#s03 .live-line");
      if (state.ops && state.ops.mode === "live") { el.textContent = "options DISABLED — no options-chain source"; return; }
      var bn = state.frame && state.frame.by_node;
      if (!bn) return;
      var g = bn.portfolio_gamma, v = bn.portfolio_vega;
      el.textContent = "the book above holds no options (gamma " + (g === null || g === undefined ? "—" : fixed(g, 1)) + ", vega " + (v === null || v === undefined ? "—" : F.money(v)) + ")";
    }
    renderers.push(function () {
      fragment("s03", pick(["price[NVDA]", "valuation_days", "rate", "exposure:NVDA", "gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"], ["contracts[", "implied_vol[", "greeks:", "option_exposure:"]),
        "on a book with no options the five singletons exist and ran once");
      drawS03(); drawS03live();
    });
    on.frame.push(function () { if (state.reports) drawS03live(); });

    // ---- §04 ----------------------------------------------------------------
    // The nine-row table with an exceedance strip under every row. The strip's
    // index space is forecasts, so a boundary at series day D sits at
    // D - window; the wire's duration_at_ceiling flag is what prints "the
    // search ceiling, not a fit" -- the page does not know the bound's value.
    function durationText(r) {
      if (r.duration_p === null || r.duration_p === undefined) return "does not apply — fewer than two exceptions";
      var s = fixed(r.duration_p, 4) + "  b = " + fixed(r.duration_shape, 2);
      return r.duration_at_ceiling ? s + " (the search ceiling, not a fit)" : s;
    }
    function batteryCols(labelKey) {
      return [
        { label: labelKey, cls: "k", fmt: function (r) { return r[labelKey]; } },
        { label: "estimator", cls: "k", fmt: function (r) { return r.estimator.label; }, tdClass: function (r) { return "est-" + estKey(r.estimator); } },
        { label: "n", fmt: function (r) { return String(r.observations); } },
        { label: "exceptions / expected", fmt: function (r) { return r.exceptions + " / " + fixed(r.expected_exceptions, 1); } },
        { label: "Kupiec p", fmt: function (r) { return fixed(r.kupiec_p, 4); } },
        { label: "indep p", fmt: function (r) { return fixed(r.independence_p, 4); } },
        { label: "joint p", fmt: function (r) { return fixed(r.conditional_coverage_p, 4); } },
        { label: "duration p", fmt: durationText, tdClass: function (r) { return r.duration_p === null || r.duration_p === undefined ? "unknown" : "v"; } },
        { label: "Basel", fmt: function (r) { return r.zone; } },
        { label: "verdict", fmt: function (r) { return r.verdict; }, tdClass: function () { return "verdict"; } },
      ];
    }
    function batteryTable(container, rows, labelKey, cfg, markAt) {
      var cols = batteryCols(labelKey);
      var t = table(container, cols, rows, function (r) { return r.rejected ? "rejected" : "ok"; });
      Array.prototype.slice.call(t.querySelectorAll("tr.row")).forEach(function (tr, i) {
        var r = rows[i];
        tr.querySelector("td.est-" + estKey(r.estimator)).style.color = C.estimatorStroke[estKey(r.estimator)];
        var strip = F.el("tr", "strip-row");
        var td = F.el("td");
        td.setAttribute("colspan", String(cols.length));
        C.strip(td, { length: r.observations, hits: r.hits, marks: (markAt ? markAt(r) : []).map(function (d) { return { at: d - cfg.window }; }) });
        strip.appendChild(td);
        tr.parentNode.insertBefore(strip, tr.nextSibling);
      });
      return t;
    }
    // The cells the README prints, at the precision it prints them.
    function batteryCells() {
      return [
        { c: "exceptions", q: "exceptions", dp: null },
        { c: "kupiec_p", q: "kupiec_p", dp: 4 },
        { c: "independence_p", q: "independence_p", dp: 4 },
        { c: "conditional_coverage_p", q: "joint_p", dp: 4 },
        { c: "duration_p", q: "duration_p", dp: 4 },
        { c: "duration_shape", q: "duration_shape", dp: 2 },
        { c: "zone", q: "zone", dp: null },
        { c: "verdict", q: "verdict", dp: null },
      ];
    }
    function drawS04() {
      var v = state.reports.validation, cfg = v.config, syn = v.synthetic;
      var f = fig("s04", "The synthetic battery — three series, three estimators, four statistics", computedTag());
      f.body.appendChild(F.el("p", "lbl", F.pct(cfg.confidence, 0) + " VaR · " + cfg.window + "-session strictly-prior window · α " + cfg.alpha + " · λ " + cfg.ewma_lambda + " · seed " + syn.generator.seed + " · " + syn.generator.method));
      syn.series.forEach(function (s) { f.body.appendChild(F.el("p", "lbl", s.name + " — " + s.description + " (" + s.length + " days, " + s.forecasts + " forecasts)")); });
      batteryTable(f.body, syn.rows, "series", cfg, function (r) { return r.series === "vol-regime" ? [600] : []; });
      if (syn.most_severe) {
        f.note("the engine's own text — the most severe rejection, Var_backtest.to_string verbatim (" + syn.most_severe.series + " / " + syn.most_severe.estimator + ")", "lbl");
        f.notes.appendChild(F.el("pre", "engine", syn.most_severe.text));
      } else {
        f.note("no configuration was rejected in this run", "unknown");
      }
      f.note("a rejection here is the suite working");
      f.notes.appendChild(compare({
        computed: syn.rows, quoted: quoted.battery.rows,
        key: function (r) { return r.series + " / " + r.estimator.label; }, qkey: function (q) { return q.series + " / " + q.estimator; },
        cells: batteryCells(),
      }).el);
      bind("battery.rejected", String(syn.rejected), syn.rejected);
    }
    renderers.push(function () {
      fragment("s04", pick(["aligned_returns", "covariance", "covariance_ewma", "portfolio_returns", "historical_var", "expected_shortfall", "parametric_var", "parametric_var_ewma"], ["returns["]));
      drawS04();
    });

    window.OhCamelArgument = {
      load: load, state: state, bind: bind, compare: compare, provenance: provenance,
      fragment: fragment, fig: fig, on: on, renderers: renderers,
    };
  })();
  ```

  **The binding table** — the 24 `<data data-bind>` names Task 12 put in the prose, each with the JSON it is filled from. Task 13 fills the first twelve; Task 14 the rest. A name missing from this table is a `—` on the page, in `--unknown`, which is the intended failure.

  | name | source | format |
  |---|---|---|
  | `bench.polled_400_ms` | `quoted.bench.rows[400].polled_us / 1000` | 1 dp |
  | `bench.ratio_400` | `quoted.bench.rows[400].ratio` | `108×` |
  | `bench.inc_10` `bench.inc_100` `bench.inc_400` | `quoted.bench.rows[n].incremental_us` | `us()` |
  | `scaling.per_tick_10` `…_100` `…_400` | `/api/reports.scaling.rows[n].nodes_per_tick` | 1 dp |
  | `options.hedge_shares` | `/api/reports.options.hedge_shares` | `num()` |
  | `options.clock_delta` | `/api/reports.options.walk_clock[last].delta_equivalent` | `money()` |
  | `options.calendar_gamma` | `/api/reports.options.calendar.portfolio_gamma` | 1 dp |
  | `battery.rejected` | `/api/reports.validation.synthetic.rejected` | integer |
  | `garch.p60_mean` `garch.p60_sd` | `/api/reports/garch.rows[n=60].persistence_{mean,sd}` | 3 dp (Task 14) |
  | `garch.truth` | `/api/reports/garch.truth.persistence` | 2 dp (Task 14) |
  | `crisis.gfc_burst` | `/api/reports.validation.crisis.rows[gfc/historical].burst` | integer (Task 14) |
  | `crisis.gfc_hist_indep_p` | `…rows[gfc/historical].independence_p` | 4 dp (Task 14) |
  | `crisis.covid_par_burst` | `…rows[covid/parametric].burst` | integer (Task 14) |
  | `crisis.covid_par_joint_p` | `…rows[covid/parametric].conditional_coverage_p` | 4 dp (Task 14) |
  | `crisis.covid_par_duration_p` | `…rows[covid/parametric].duration_p` | 4 dp (Task 14) |
  | `crisis.covid_par_shape` | `…rows[covid/parametric].duration_shape` | 2 dp (Task 14) |
  | `smoke.nodes_delta` | Δ `nodes_recomputed` across the last 2 s of frames | `+N` (Task 14) |
  | `smoke.frames` | the arrival strip's `stats().distinct` over 20 s | integer (Task 14) |
  | `verified.tests` | `quoted.verified.tests` | integer (Task 14) |

  How the Step 1 counts fall out: `s01a` is a header row plus `tick` and `bar` → 3 `tr`; `s01b` is one non-quoted `svg.dotline` with two `path.series`, a `.compare` line and a `COMPUTED · AT STARTUP` tag; `s01c` is one `svg.dotline.quoted` with three `text.ratio`; the eight §01 bindings are `bench.*` (5) and `scaling.per_tick_*` (3); `s02`'s `text.name` count is `pairedBars`'s, one per position; `s03`'s `.lim` count is `options.limits.length` and its three bindings are `options.*`; `s04` is nine `tr.row` (the strip rows carry `strip-row`, a different token), nine `svg.strip`, three `line.mark` (the `vol-regime` rows), one `pre.engine`, one `.compare`, one binding. `unbound` is empty because every `<data>` in §01–§04 is in the table above.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && node --check web/argument.js && node --check web/dashboard.js && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -4
  ```
  Expected: no syntax error; `Test Successful` — the structure test still finds `new EventSource(` in `dashboard.js` and `window.OhCamelCharts =` before it.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && curl -s http://localhost:8099/ | grep -o 'data-bind=' | wc -l && (cd /tmp/ohcamel-phase5/pw && node check-argument-1.mjs && node check-charts-1.mjs && node check-charts-2.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected: `24`, then `ARGUMENT-1 OK`, `CHARTS-1 OK {...}`, `CHARTS-2 OK` and no `pageerror`. Then look at it once: the §01(a) table's frame count climbs while you watch, §02 redraws as the ledger does, and every `<data>` in §01–§04 is set in the ink while §05–§08's are still `—` in blue.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add web/dashboard.js web/argument.js && git commit -m "web: the argument's first four sections, because a figure that cannot say where its number came from is decoration"
  ```

---

### Task 14: `web/argument.js`, part 2 — §05 the study as it runs, §06 the crises, §07 the forks, §08 the browser's own smoke run, §09

**Files:**
- Modify: `web/argument.js` (append five renderer blocks inside the IIFE, above the `window.OhCamelArgument = {` line Task 13 wrote; add one key to that object)
- Test: `/tmp/ohcamel-phase5/pw/check-argument-2.mjs` (Playwright, dev-machine only)

**Interfaces:**
- Consumes: everything Task 13 defined inside the IIFE — `state`, `on`, `renderers`, `quoted`, `getJSON`, `clock`, `fixed`, `num`, `provenance`, `liveTag`, `computedTag`, `quotedTag`, `fig`, `table`, `bind`, `compare`, `fragment`, `pick`, `estKey`, `limLine`, `batteryCols`, `batteryCells` — by their local names; `window.OhCamelCharts.{timeline, selector, dotWhisker, signedBars, arrivalStrip, strip, estimatorStroke}` (Task 11); `GET /api/reports/garch` (Task 6: `status, done, of, params.{seed, replications, sample_sizes, burn_in, coarse_steps}, truth.{omega, alpha, beta, persistence, half_life}, window, rows[].{n, alpha_mean, alpha_sd, beta_mean, beta_sd, persistence_mean, persistence_sd}, verdict_row, computed_in_ms, error`); `/api/reports.validation.crisis` (Task 3: `source, book.{positions[].{symbol, sector, mark, qty}, gross_notional, note}, windows[].{name, description, first, last, sessions, forecasts, worst_day, best_day, dates, realised}, rows[] = row + {burst, burst_start, burst_span, burst_expected, var}, rejected`); `GET /api/stress` **in the spec's extended shape** (`as_of, before.{gross_exposure, equity, value_at_risk_notional, breached}, scenarios[].{name, description, shocks[].{kind, symbol, sector, move, text}, pnl, pnl_fraction, equity_before, equity_after, drawdown_before, drawdown_after, gross_exposure, value_at_risk_notional, new_breaches[], cleared_breaches[], unestimated_betas[]}, worst, counter_cost, duration_ms` — Phase 4's; its plan has Tasks 1–3 written today and the stress task is not among them, so the field list here is the spec's *Technical architecture* verbatim and is flagged in this plan's risks); `GET /api/ops` (Phase 2: `mode, ocaml_version, build.{git_short, built_at, profile, architecture, system}, feed.staleness_threshold_s, feed_source.{kind, alpaca_feed, fred_series}`); `/api/history.appended` (exists — `lib/server.ml:130`); `quoted.garch.rows`, `quoted.crisis.rows`, `quoted.verified` (Phase 1 and Task 12).
- Produces: `window.OhCamelArgument.runStress() -> Promise` (debounced: one fetch per five seconds, the second call inside the window resolves to the last result); `state.{garch, stress, stressAt, stressRuns}`; the provenance wording `COMPUTING · k OF 180 FITS · ON A SECOND DOMAIN` (`.prov[data-prov="computing"]`, in `--unknown` by Task 12's rule) and, on the live origin, the same `SYNTHETIC BOOK, NOT THIS HOST'S` tag as §01–§04; the three §08 sentences Phase 6's Task 9 copies from the page: `nodes_recomputed +N across the last 2 s`, `book changed +M between the last two /api/history reads`, `K distinct frames spread over S s within the last 20 s` — and at night on the live host `no frame in the last N s` and `0 frames — nothing changed in the last N s; the stream is parked, not dead; the counter above still advances from the 5 s clock`.

- [ ] **Step 1: Write the failing test.** Create `/tmp/ohcamel-phase5/pw/check-argument-2.mjs`. It waits for the study to finish (the demo's 180 fits take five to fifteen seconds on this machine; the bound is the smoke suite's 120 s), asks for the stress run directly instead of scrolling, and clicks the re-run link inside the debounce window so the debounce is what is tested:

  ```js
  // §05-§09 rendered against a real demo server; waits for the GARCH study.
  import { chromium } from "playwright";
  const base = process.env.BASE || "http://localhost:8099";
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/", { waitUntil: "networkidle" });
  await page.evaluate(() => window.OhCamelArgument.load());
  await page.waitForFunction(() => window.OhCamelArgument.state.garch !== null, null, { timeout: 20000 });
  const provAtFirstPoll = await page.evaluate(() => document.querySelector('[data-fig="s05"] .cap-prov').textContent);
  await page.evaluate(() => window.OhCamelArgument.runStress());
  await page.waitForFunction(() => window.OhCamelArgument.state.stress !== null, null, { timeout: 20000 });
  await page.evaluate(() => document.querySelector('[data-fig="s07"] a.rerun').click());
  await page.waitForFunction(() => window.OhCamelArgument.state.frames.length >= 3 && Date.now() - window.OhCamelArgument.state.loadedAt > 2500, null, { timeout: 20000 });
  await page.waitForFunction(() => window.OhCamelArgument.state.garch && window.OhCamelArgument.state.garch.status === "done", null, { timeout: 120000 });
  await page.waitForFunction(() => document.querySelector('[data-fig="s05"] .compare') !== null, null, { timeout: 10000 });
  const got = await page.evaluate(() => {
    const A = window.OhCamelArgument;
    const $ = (sel) => document.querySelectorAll(sel);
    const n = (sel) => $(sel).length;
    const text = (sel) => (document.querySelector(sel) || { textContent: "" }).textContent;
    const out = {};
    out.s05 = [n('[data-fig="s05"] table.quoted tr.row'), n('[data-fig="s05"] table.engine tr.row'), n('[data-fig="s05"] svg.whisker'), n('[data-fig="s05"] circle.dot'), text('[data-fig="s05"] .cap-prov').indexOf("COMPUTED · AT STARTUP") === 0, n('[data-fig="s05"] .compare'), n("#s05 data[value]")];
    out.s06 = [n("#s06 svg.timeline"), n("#s06 rect.burst"), n("#s06 a.est"), n('[data-fig="s06c"] table.battery tr.row'), n("#s06 .compare"), n("#s06 data[value]")];
    $("#s06 a.est")[2].click();
    out.s06sel = Array.from($("#s06 svg.timeline")).map((s) => s.getAttribute("data-selected"));
    out.s07 = [n('[data-fig="s07"] svg.signed'), n('[data-fig="s07"] rect.bar'), n('[data-fig="s07"] rect.bar.worst'), n('[data-fig="s07"] table tr.row'), text('[data-fig="s07"] .cap-prov').indexOf("LIVE · THIS HOST'S BOOK") === 0, /moves by \d/.test(text('[data-fig="s07"] .counter')), A.state.stressRuns, n('[data-fig="s07"] .worst-case')];
    out.s08 = [n("#s08 li.smoke"), n("#s08 li.smoke.load"), /·/.test(text('[data-fig="s08a"] .build')), n('[data-fig="s08b"] svg.arrival'), n('[data-fig="s08c"] table.quoted tr.row'), document.querySelector("#s08 .demo-only").hidden, n("#s08 data[value]")];
    out.s09 = [text('.frag[data-frag="s09"] .frag-list').length > 0, n("#s09 ul.not-shown li")];
    out.unbound = Array.from($("#argument data")).filter((d) => !d.hasAttribute("value")).map((d) => d.getAttribute("data-bind"));
    return out;
  });
  const expect = {
    s05: [6, 6, 1, 6, true, 1, 3],
    s06: [3, 3, 3, 9, 1, 6],
    s06sel: ["ewma", "ewma", "ewma"],
    s07: [1, 12, 1, 12, true, true, 1, 1],
    s08: [9, 2, true, 1, 9, false, 3],
    s09: [true, 11],
    unbound: [],
  };
  const bad = errors.map((e) => "pageerror: " + e);
  if (!/^(COMPUTING · \d+ OF \d+ FITS|COMPUTED · AT STARTUP)/.test(provAtFirstPoll)) bad.push("s05 provenance at first poll: " + JSON.stringify(provAtFirstPoll));
  for (const k of Object.keys(expect)) {
    if (JSON.stringify(got[k]) !== JSON.stringify(expect[k])) bad.push(k + ": expected " + JSON.stringify(expect[k]) + ", got " + JSON.stringify(got[k]));
  }
  await browser.close();
  if (bad.length) { console.error("FAIL\n  " + bad.join("\n  ")); process.exit(1); }
  console.log("ARGUMENT-2 OK (§05 read " + JSON.stringify(provAtFirstPoll) + " at the first poll)");
  ```

  `s05`'s six `circle.dot` are the six completed rows; `s06`'s three `rect.burst` are the selected estimator's worst windows, one per timeline (every crisis row has a positive burst); `s07`'s `stressRuns` stays 1 because the click landed inside the five-second window; `s08`'s nine `table.quoted` rows are seventeen files two per row; `s09`'s eleven are the *not shown* list.

- [ ] **Step 2: Run it and see it fail.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-argument-2.mjs); pkill -f "main.exe demo 8099"
  ```
  Expected failure: `TypeError: Cannot read properties of null (reading 'textContent')` at `provAtFirstPoll` — `fig("s05", …)` has never run, so the figure has no caption.

- [ ] **Step 3: Minimal implementation, part (a) — §05.** Insert into `web/argument.js` immediately **above** the line `    window.OhCamelArgument = {`. The figure is rebuilt wholesale on every poll: `fig()` is re-entrant and a five-second cadence does not need an incremental redraw; the visible effect is the spec's — rows landing one by one, the whisker at 60 first.

  ```js
    // ---- §05 ----------------------------------------------------------------
    // Two columns: the README's table, always present, in the soft ink; and
    // this process's, rebuilt on every poll as the second domain reports in.
    // The only progress indicator on the page is data arriving.
    function garchCols() {
      var pm = function (m, s) { return m === null || m === undefined ? null : fixed(m, 3) + " ± " + fixed(s, 3); };
      return [
        { label: "n", cls: "k", fmt: function (r) { return String(r.n); } },
        { label: "alpha", fmt: function (r) { return pm(r.alpha_mean, r.alpha_sd); } },
        { label: "beta", fmt: function (r) { return pm(r.beta_mean, r.beta_sd); } },
        { label: "persistence", fmt: function (r) { return pm(r.persistence_mean, r.persistence_sd); } },
      ];
    }
    function drawS05() {
      var g = state.garch;
      if (!g) return;
      var running = g.status !== "done";
      var prov = running
        ? provenance("computing", "COMPUTING · " + g.done + " OF " + g.of + " FITS · ON A SECOND DOMAIN")
        : g.error ? provenance("computed", "COMPUTED · FAILED") : computedTag();
      var f = fig("s05", "GARCH(1,1) recovered from its own process — persistence by sample size, " + g.params.replications + " replications", prov);
      var t = g.truth, p = g.params;
      f.body.appendChild(F.el("p", "lbl", "truth: ω " + t.omega + " · α " + fixed(t.alpha, 2) + " · β " + fixed(t.beta, 2) + " · persistence " + fixed(t.persistence, 2) + " · half-life " + (t.half_life === null ? "—" : fixed(t.half_life, 0)) + " periods"));
      f.body.appendChild(F.el("p", "lbl", "study: seed " + p.seed + " · n ∈ {" + p.sample_sizes.join(", ") + "} · burn-in " + p.burn_in + " · " + p.coarse_steps + "×" + p.coarse_steps + " grid + pattern search"));
      var two = F.el("div", "twocol");
      var left = F.el("div", "quoted"), right = F.el("div");
      left.appendChild(F.el("p", "lbl", "README · quoted"));
      table(left, garchCols(), quoted.garch.rows).classList.add("quoted");
      right.appendChild(F.el("p", "lbl", running ? "this process · " + g.done + " of " + g.of + " fits" : "this process"));
      // Six slots, filled as rows land; an empty slot is a row of em dashes.
      var slots = p.sample_sizes.map(function (n) {
        var r = null;
        g.rows.forEach(function (x) { if (x.n === n) r = x; });
        return r || { n: n, alpha_mean: null, alpha_sd: null, beta_mean: null, beta_sd: null, persistence_mean: null, persistence_sd: null };
      });
      table(right, garchCols(), slots).classList.add("engine");
      two.appendChild(left); two.appendChild(right);
      f.body.appendChild(two);
      C.dotWhisker(f.body, { xs: p.sample_sizes, rows: g.rows, truth: t.persistence, window: g.window, yRange: [0, 1.1] });
      if (g.error) f.note("the study raised: " + g.error, "over");
      if (!running && !g.error) {
        f.note(g.of + " fits in " + fixed(g.computed_in_ms / 1000, 1) + " s on a second domain, after the socket bound");
        f.notes.appendChild(compare({
          computed: g.rows, quoted: quoted.garch.rows,
          key: function (r) { return String(r.n); }, qkey: function (q) { return String(q.n); },
          cells: ["alpha_mean", "alpha_sd", "beta_mean", "beta_sd", "persistence_mean", "persistence_sd"].map(function (k) { return { c: k, q: k, dp: 3 }; }),
        }).el);
      }
      var v = null;
      g.rows.forEach(function (x) { if (x.n === g.window) v = x; });
      bind("garch.p60_mean", v ? fixed(v.persistence_mean, 3) : null, v ? v.persistence_mean : undefined);
      bind("garch.p60_sd", v ? fixed(v.persistence_sd, 3) : null, v ? v.persistence_sd : undefined);
      bind("garch.truth", fixed(t.persistence, 2), t.persistence);
    }
    renderers.push(function () {
      fragment("s05", pick(["covariance_ewma", "parametric_var_ewma"], []), "garch — implemented, not wired in: no edge into the graph");
      drawS05();
    });
    on.garch.push(drawS05);
  ```

  At renderer time `state.garch` is still `null` (`load()` starts the poll after the renderers run), so §05's figure is empty until the first poll answers — a few milliseconds — and then rebuilt on each; the test's `provAtFirstPoll` is whichever state the study was in when the first answer came, which is why it accepts both wordings.

- [ ] **Step 3: Minimal implementation, part (b) — §06 and §07.** Insert directly after part (a), still above the `window.OhCamelArgument = {` line. `crisisTable` repeats `batteryTable`'s strip loop with one more column rather than parameterising Task 13's function, so each reads whole.

  ```js
    // ---- §06 ----------------------------------------------------------------
    // Three timelines sharing one y-range and one selector; the window's
    // facts above each, the constant-weights book and the nine-row table
    // under the third. Hits and bursts are the wire's, drawn at the encoder's
    // alignment (Task 11's contract) -- the page finds no burst itself.
    var ANNOTATED = { gfc: ["2008-09-15", "2008-10-07"] };
    function crisisTable(container, rows, cfg) {
      var cols = batteryCols("window");
      cols.splice(8, 0, { label: "burst", fmt: function (r) { return String(r.burst); } });
      var t = table(container, cols, rows, function (r) { return r.rejected ? "rejected" : "ok"; });
      Array.prototype.slice.call(t.querySelectorAll("tr.row")).forEach(function (tr, i) {
        var r = rows[i];
        tr.querySelector("td.est-" + estKey(r.estimator)).style.color = C.estimatorStroke[estKey(r.estimator)];
        var strip = F.el("tr", "strip-row");
        var td = F.el("td");
        td.setAttribute("colspan", String(cols.length));
        C.strip(td, { length: r.observations, hits: r.hits, marks: [] });
        strip.appendChild(td);
        tr.parentNode.insertBefore(strip, tr.nextSibling);
      });
      t.classList.add("battery");
      return t;
    }
    function drawS06() {
      var v = state.reports.validation, cfg = v.config, cr = v.crisis;
      var ids = ["s06a", "s06b", "s06c"];
      var timelines = [];
      cr.windows.forEach(function (w, i) {
        var rows = cr.rows.filter(function (r) { return r.window === w.name; });
        var byKind = function (field) { var o = {}; rows.forEach(function (r) { o[estKey(r.estimator)] = r[field]; }); return o; };
        var bursts = {};
        rows.forEach(function (r) { bursts[estKey(r.estimator)] = { start: r.burst_start, span: r.burst_span, count: r.burst }; });
        var f = fig(ids[i], w.name + " — " + w.description, computedTag());
        f.body.appendChild(F.el("p", "lbl", w.first + " → " + w.last + " · " + w.sessions + " sessions · " + w.forecasts + " forecasts · worst day " + F.pct(w.worst_day, 2) + " · best " + F.pct(w.best_day, 2)));
        timelines.push(C.timeline(f.body, {
          dates: w.dates, realised: w.realised, window: cfg.window,
          var: byKind("var"), hits: byKind("hits"), burst: bursts,
          yRange: [-0.08, 0.08], selected: "historical",
          annotations: (ANNOTATED[w.name] || []).map(function (d) { return { index: w.dates.indexOf(d), label: d }; }).filter(function (a) { return a.index >= 0; }),
        }));
        if (i === cr.windows.length - 1) {
          var links = C.selector(f.notes, ["historical", "parametric", "ewma"], ["historical", "parametric", "ewma(" + cfg.ewma_lambda + ")"],
            function (k) { timelines.forEach(function (t) { t.select(k); }); });
          links.querySelector('a.est[data-est="historical"]').classList.add("on");
          f.note("the book at constant weights — gross " + F.money(cr.book.gross_notional), "lbl");
          table(f.notes, [
            { label: "", cls: "k", fmt: function (p) { return p.symbol; } },
            { label: "sector", cls: "k", fmt: function (p) { return p.sector; } },
            { label: "mark", fmt: function (p) { return F.money(p.mark); } },
            { label: "qty", fmt: function (p) { return num(p.qty); } },
          ], cr.book.positions);
          f.note(cr.book.note);
          f.note(cr.source, "lbl");
          crisisTable(f.notes, cr.rows, cfg);
          f.note("burst: max exceptions in any " + rows[0].burst_span + " sessions; ≈ " + fixed(rows[0].burst_expected, 1) + " expected under independence; descriptive, not a test", "lbl");
          f.note(cr.rejected + " of " + cr.rows.length + " rejected at " + F.pct(cfg.alpha, 0) + " — and the README's caution stands: survival is not reassurance");
          f.notes.appendChild(compare({
            computed: cr.rows, quoted: quoted.crisis.rows,
            key: function (r) { return r.window + " / " + r.estimator.label; }, qkey: function (q) { return q.window + " / " + q.estimator; },
            cells: batteryCells().concat([{ c: "burst", q: "burst", dp: null }]),
          }).el);
        }
      });
      var row = function (w, kind) { var out = null; cr.rows.forEach(function (r) { if (r.window === w && estKey(r.estimator) === kind) out = r; }); return out; };
      var gh = row("gfc", "historical"), cp = row("covid", "parametric");
      if (gh) {
        bind("crisis.gfc_burst", String(gh.burst), gh.burst);
        bind("crisis.gfc_hist_indep_p", fixed(gh.independence_p, 4), gh.independence_p);
      }
      if (cp) {
        bind("crisis.covid_par_burst", String(cp.burst), cp.burst);
        bind("crisis.covid_par_joint_p", fixed(cp.conditional_coverage_p, 4), cp.conditional_coverage_p);
        bind("crisis.covid_par_duration_p", fixed(cp.duration_p, 4), cp.duration_p);
        bind("crisis.covid_par_shape", fixed(cp.duration_shape, 2), cp.duration_shape);
      }
    }
    renderers.push(function () {
      fragment("s06", pick(["aligned_returns", "covariance", "covariance_ewma", "portfolio_returns", "historical_var", "expected_shortfall", "parametric_var", "parametric_var_ewma"], ["returns["]),
        "the same nodes as §04; only the data changed");
      drawS06();
    });

    // ---- §07 ----------------------------------------------------------------
    // /api/stress is the one route on this page that COMPUTES on request
    // (twelve forks, ~70 ms), so it is fetched when the section scrolls into
    // view and re-run by a link debounced to one fetch per five seconds. A
    // second call inside the window resolves to the last answer and fetches
    // nothing -- the footer's counter must move because of a fork, never
    // because a reader leaned on a key.
    state.stress = null; state.stressAt = 0; state.stressRuns = 0;
    var stressPending = null;
    function runStress() {
      if (stressPending) return stressPending;
      if (Date.now() - state.stressAt < 5000) return Promise.resolve(state.stress);
      state.stressAt = Date.now();
      stressPending = getJSON("/api/stress").then(function (s) {
        stressPending = null;
        state.stressRuns += 1;
        state.stress = s;
        drawS07();
        return s;
      }).catch(function (e) {
        stressPending = null;
        var f = fig("s07", "Twelve scenarios on this host's book", provenance("live", "LIVE · THIS HOST'S BOOK"));
        f.note("/api/stress did not answer: " + String(e), "unknown");
        return null;
      });
      return stressPending;
    }
    function breachNames(list) { return (list || []).map(function (b) { return b.name; }).join(", "); }
    function drawS07() {
      var s = state.stress;
      var f = fig("s07", "Twelve scenarios on this host's book, each on a fork of the served graph",
        provenance("live", "LIVE · THIS HOST'S BOOK · as of " + clock(s.as_of) + " — not the README's table, which ran on the CLI's seeded book"));
      var b = s.before;
      f.body.appendChild(F.el("p", "lbl", "starting book: gross " + F.money(b.gross_exposure) + " · equity " + F.money(b.equity) + " · VaR " + (b.value_at_risk_notional === null ? "—" : F.money(b.value_at_risk_notional)) + " · breached: " + (b.breached.length ? b.breached.join(", ") : "none")));
      C.signedBars(f.body, s.scenarios.map(function (sc) {
        return { name: sc.name, value: sc.pnl, worst: sc.name === s.worst, note: breachNames(sc.new_breaches) };
      }));
      table(f.body, [
        { label: "scenario", cls: "k", fmt: function (r) { return r.name; } },
        { label: "P&L", fmt: function (r) { return F.money(r.pnl); } },
        { label: "%", fmt: function (r) { return F.pct(r.pnl_fraction, 2); } },
        { label: "gross after", fmt: function (r) { return F.money(r.gross_exposure); } },
        { label: "VaR after", fmt: function (r) { return r.value_at_risk_notional === null ? null : F.money(r.value_at_risk_notional); } },
        { label: "newly breached", cls: "k", fmt: function (r) { return breachNames(r.new_breaches) || "--"; } },
        { label: "no beta", cls: "k", fmt: function (r) { return r.unestimated_betas.length ? r.unestimated_betas.join(", ") : "--"; } },
      ], s.scenarios, function (r) { return r.name === s.worst ? "worst" : "ok"; });
      var w = null;
      s.scenarios.forEach(function (sc) { if (sc.name === s.worst) w = sc; });
      if (w) {
        var box = F.el("div", "worst-case");
        box.appendChild(F.el("p", "lbl", "WORST CASE · " + w.name));
        box.appendChild(F.el("p", "", w.description));
        w.shocks.forEach(function (sh) {
          box.appendChild(F.el("p", "", sh.kind + (sh.symbol ? " " + sh.symbol : "") + (sh.sector ? " " + sh.sector : "") + " " + sh.move + " — " + sh.text));
        });
        box.appendChild(F.el("p", "", "P&L " + F.money(w.pnl) + " (" + F.pct(w.pnl_fraction, 2) + ") · equity " + F.money(w.equity_before) + " → " + F.money(w.equity_after) + " · drawdown " + F.pct(w.drawdown_before, 2) + " → " + F.pct(w.drawdown_after, 2)));
        (w.new_breaches || []).forEach(function (br) { limLine(box, br); });
        if (w.unestimated_betas.length) box.appendChild(F.el("p", "unknown", "no beta: " + w.unestimated_betas.join(", ") + " — the factor shock could not be applied to these"));
        f.notes.appendChild(box);
      }
      var counter = F.el("p", "counter", s.scenarios.length + " forks, " + fixed(s.duration_ms, 0) + " ms; the process-wide counter in the footer moves by " + num(s.counter_cost) + " for a reason that is not a tick. ");
      var again = F.el("a", "rerun", "run it again on a fork");
      again.href = "#";
      again.addEventListener("click", function (ev) { ev.preventDefault(); runStress(); });
      counter.appendChild(again);
      f.notes.appendChild(counter);
    }
    renderers.push(function () {
      fragment("s07", pick(["gross_exposure", "net_exposure", "equity", "current_drawdown", "historical_var", "var_notional", "breaches"], []),
        "the spine, twice — live and fork: inputs copied → shocks written → read by the same nodes");
      var sec = document.getElementById("s07");
      if ("IntersectionObserver" in window) {
        var io = new IntersectionObserver(function (entries) {
          if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); runStress(); }
        }, { rootMargin: "400px 0px" });
        io.observe(sec);
      } else {
        runStress();
      }
    });
  ```

  The spec's `cleared_breaches` is on the wire and not drawn: a scenario that clears a breach is a scenario that moved the book the safe way, and a table of twelve rows has no column that would not read as reassurance. The field stays available to a reader of `/api/stress`.

- [ ] **Step 3: Minimal implementation, part (c) — §08, §09, and the exported `runStress`.** Insert directly after part (b), still above the `window.OhCamelArgument = {` line; then change that object's second line from `      fragment: fragment, fig: fig, on: on, renderers: renderers,` to `      fragment: fragment, fig: fig, on: on, renderers: renderers, runStress: runStress,`.

  ```js
    // ---- §08 ----------------------------------------------------------------
    // The smoke suite's nine assertions, listed in deploy/smoke.sh's order,
    // and the two load-bearing ones re-run in THIS browser from the frames
    // the ledger already receives: nodes_recomputed differenced across two
    // seconds, history.appended differenced between two reads (a number no
    // fork can inflate), and the arrival strip's twenty-second window. The
    // other seven are the droplet's; a browser cannot open port 8081.
    var SMOKE = [
      { text: "GET / answers 200" },
      { text: "GET /api/health answers 200" },
      { text: "GET /api/snapshot has positions and a gross exposure, and nodes_recomputed ADVANCES across 2 s", load: "counter" },
      { text: "SSE /api/stream delivers ≥ 2 distinct frames spread ≥ 2 s apart within 20 s — not a pile at the end", load: "stream" },
      { text: "http:// redirects to https:// (production only)" },
      { text: "the TLS certificate is valid for the host (production only)" },
      { text: "port 8080 is not reachable from outside" },
      { text: "port 8081 is not reachable from outside" },
      { text: "the live host answers 401 without credentials" },
    ];
    var arrival = null, historyLog = [], s08timer = null;
    on.history.push(function (h) {
      historyLog.push({ at: Date.now(), appended: h.appended });
      if (historyLog.length > 20) historyLog.shift();
    });
    // The counter delta: the newest frame against the newest frame at least
    // two seconds older. No frame for twenty seconds is "parked", a state,
    // not a zero.
    function counterDelta(nowMs) {
      var fs = state.frames;
      if (!fs.length) return null;
      var last = fs[fs.length - 1], ref = null;
      if (nowMs - last.at > 20000) return { parked: Math.round((nowMs - last.at) / 1000) };
      for (var i = fs.length - 1; i >= 0; i--) { if (last.at - fs[i].at >= 2000) { ref = fs[i]; break; } }
      if (!ref) return null;
      return { delta: last.nodes_recomputed - ref.nodes_recomputed };
    }
    function drawS08a() {
      var o = state.ops;
      var mode = o ? o.mode : null;
      var f = fig("s08a", "Two hosts, and which one this is", liveTag());
      table(f.body, [
        { label: "", cls: "k", fmt: function (h) { return h.host; } },
        { label: "mode", cls: "k", fmt: function (h) { return h.mode; } },
        { label: "feed", cls: "k", fmt: function (h) { return h.feed; } },
        { label: "", cls: "k", fmt: function (h) { return h.mode === mode ? "← this one" : ""; } },
      ], [
        { host: "ohcamel.ajaiupadhyaya.com", mode: "demo", feed: "synthetic feed · public" },
        { host: "live.ohcamel.ajaiupadhyaya.com", mode: "live", feed: "Alpaca + FRED · behind a password" },
      ]);
      if (o) {
        var fs = o.feed_source || {}, b = o.build || {};
        f.note("this host: " + o.mode + " · feed " + fs.kind + (fs.alpaca_feed ? " " + fs.alpaca_feed : "") + " · factor " + (fs.fred_series || "SYNTHETIC") + " · staleness threshold " + o.feed.staleness_threshold_s + " s");
        f.note((b.git_short || "unknown") + " · built " + (b.built_at || "unknown") + " · OCaml " + o.ocaml_version + " · " + b.profile + " · " + b.architecture + "/" + b.system, "build");
        f.note("the smoke suite compares this sha to the droplet's checkout on every deploy");
      } else {
        f.note("/api/ops did not answer; the build line needs it", "unknown");
      }
      var ul = F.el("ul", "smoke-list");
      SMOKE.forEach(function (a) {
        var li = F.el("li", "smoke" + (a.load ? " load" : ""));
        var mark = F.el("span", "mark lbl", a.load ? "—" : "not re-run here");
        if (a.load) mark.setAttribute("data-load", a.load);
        li.appendChild(mark);
        li.appendChild(document.createTextNode(" "));
        li.appendChild(a.load ? F.el("strong", "", a.text) : document.createTextNode(a.text));
        ul.appendChild(li);
      });
      f.notes.appendChild(ul);
      f.note("the seven marked not re-run here are the droplet's after every deploy; the two in bold are re-run from this page's own stream, below");
    }
    function drawS08b() {
      var f = fig("s08b", "Frame arrival — one tick per SSE frame at its browser arrival time, the smoke suite's 20 s shaded", liveTag());
      arrival = C.arrivalStrip(f.body, { windowS: 60, shadeS: 20 });
      state.frames.forEach(function (x) { arrival.tick(x.at, x.distinct); });
      f.note("", "arrival-caption");
      f.note("nodes recomputed (footer) includes every stress fork and the startup probe; history.appended counts only the served book's changes, which forks cannot touch");
    }
    function tickS08() {
      if (!arrival) return;
      var now = Date.now();
      arrival.redraw(now);
      var st = arrival.stats(now);
      var since = Math.round((now - state.loadedAt) / 1000);
      var cd = counterDelta(now);
      var parked = cd !== null && cd.parked !== undefined;
      var cap = document.querySelector('[data-fig="s08b"] .arrival-caption');
      cap.textContent = parked
        ? "0 frames — nothing changed in the last " + cd.parked + " s; the stream is parked, not dead; the counter above still advances from the 5 s clock"
        : st.distinct + " distinct frames spread over " + st.spreadS + " s within the last 20 s" + (since < 20 ? " (" + since + " s since load; the window fills at 20 s)" : "");
      var counterMark = document.querySelector('#s08 .mark[data-load="counter"]');
      var streamMark = document.querySelector('#s08 .mark[data-load="stream"]');
      var book = historyLog.length >= 2
        ? "book changed +" + num(historyLog[historyLog.length - 1].appended - historyLog[historyLog.length - 2].appended) + " between the last two /api/history reads"
        : "book changed — waiting for a second /api/history read";
      if (cd === null) {
        counterMark.textContent = "— · waiting for 2 s of frames";
      } else if (parked) {
        counterMark.textContent = "— · no frame in the last " + cd.parked + " s";
        bind("smoke.nodes_delta", null);
      } else {
        counterMark.textContent = (cd.delta > 0 ? "PASS" : "FAIL") + " · nodes_recomputed +" + num(cd.delta) + " across the last 2 s · " + book;
        counterMark.className = "mark lbl " + (cd.delta > 0 ? "pass" : "fail");
        bind("smoke.nodes_delta", "+" + num(cd.delta), cd.delta);
      }
      if (since < 20) {
        streamMark.textContent = "— · " + st.distinct + " distinct frames so far, " + since + " s of 20";
        bind("smoke.frames", null);
      } else {
        var pass = st.distinct >= 2 && st.spreadS >= 2;
        streamMark.textContent = (pass ? "PASS" : parked ? "PARKED" : "FAIL") + " · " + st.distinct + " distinct frames spread over " + st.spreadS + " s";
        streamMark.className = "mark lbl " + (pass ? "pass" : "fail");
        bind("smoke.frames", String(st.distinct), st.distinct);
      }
    }
    function drawS08c() {
      var v = quoted.verified;
      var f = fig("s08c", "What is verified — the count and the coverage, dated", quotedTag(v.dated));
      f.body.appendChild(F.el("p", "", num(v.tests) + " tests, all hermetic · pinned by a test that counts the registered suites"));
      f.body.appendChild(F.el("p", "", "coverage " + v.coverage_pct + "% = " + num(v.covered) + " / " + num(v.of) + " lines · bisect_ppx · measured " + v.dated));
      var pairs = [];
      for (var i = 0; i < v.per_file.length; i += 2) pairs.push({ a: v.per_file[i], b: v.per_file[i + 1] || null });
      table(f.body, [
        { label: "computes a risk number", cls: "k", fmt: function (p) { return p.a.file; } },
        { label: "", fmt: function (p) { return p.a.pct + "%"; } },
        { label: "talks to a network", cls: "k", fmt: function (p) { return p.b ? p.b.file : ""; } },
        { label: "", fmt: function (p) { return p.b ? p.b.pct + "%" : ""; } },
      ], pairs).classList.add("quoted");
      f.note("both constants are re-measured and re-dated in the last phase; the pin turns a stale count into a red build rather than a stale number");
      bind("verified.tests", num(v.tests), v.tests);
    }
    renderers.push(function () {
      fragment("s08", pick(["breaches", "feed_health"], ["feed:", "last_tick["]), "the observers — history, stream, alerts — sit outside the graph; the smoke suite reads two of them");
      drawS08a(); drawS08b(); drawS08c();
      var demoOnly = document.querySelector("#s08 .demo-only");
      if (demoOnly && state.ops && state.ops.mode === "demo") demoOnly.hidden = false;
      tickS08();
      if (s08timer) clearInterval(s08timer);
      s08timer = setInterval(function () { if (document.visibilityState === "visible") tickS08(); }, 1000);
    });
    on.frame.push(function () {
      if (!arrival) return;
      var last = state.frames[state.frames.length - 1];
      arrival.tick(last.at, last.distinct);
    });

    // ---- §09 ----------------------------------------------------------------
    // Prose only, from the HTML; the fragment is the one edge that ends in a
    // bar: alerts to a kill switch wired to nothing.
    renderers.push(function () {
      fragment("s09", pick(["breaches", "portfolio_gamma", "portfolio_vega"], []), "alerts → kill switch — wired to nothing; no node here routes an order");
    });
  ```

  How the Step 1 counts fall out: `li.smoke` is the nine entries of `SMOKE`, two of them `load`; `.build` is the note with that class, whose text has five ` · ` separators; `svg.arrival` is one strip; `table.quoted tr.row` is nine pairs of seventeen files; `.demo-only` is unhidden because the demo's `/api/ops.mode` is `demo`; §08's three bindings are `smoke.nodes_delta` (needs a frame two seconds older than the newest — the test waited 2.5 s), `smoke.frames` (set once twenty seconds have passed since `load()` — the GARCH wait covers it) and `verified.tests`.

- [ ] **Step 4: Run the tests and see them pass.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && node --check web/argument.js && make build && ./_build/default/test/test_ohcamel.exe test embedded_assets 2>&1 | tail -4
  ```
  Expected: no syntax error; `Test Successful`.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && (cd /tmp/ohcamel-phase5/pw && node check-argument-2.mjs && node check-argument-1.mjs); grep -c "garch       done" /tmp/ohcamel-demo.log; pkill -f "main.exe demo 8099"
  ```
  Expected: `ARGUMENT-2 OK (§05 read "COMPUTING · … OF 180 FITS · ON A SECOND DOMAIN" at the first poll)` — or `"COMPUTED · AT STARTUP · HH:MM:SSZ"` if the study beat the page — then `ARGUMENT-1 OK`, then `1`: the engine's completion line landed in the log while the page was polling. Then look at it once, and scroll: §05's right column fills row by row and its tag turns from blue to the soft ink; §07 fetches when it comes into view and the footer's counter jumps by the number the caption prints; §08's two bold lines read `—` for twenty seconds and then `PASS` with this session's numbers; a window narrower than 700 px shows every fragment as a name list.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add web/argument.js && git commit -m "web: the argument's last five sections, because a smoke result nobody persists can still be re-run in the reader's own browser"
  ```

---

### Task 15: `deploy/smoke.sh` — the five routes the page reads, asserted after every deploy

**Files:**
- Modify: `deploy/smoke.sh` (one block inserted immediately below Phase 2 Task 15's marker comment, between its block 4a and the TLS section; the four original assertions, the SSE probe and block 4a are untouched)
- Test: the suite itself, run against a local demo and against a port with nothing on it

**Interfaces:**
- Consumes: `GET /api/graph` (`nodes[].name`, `edges: [[from, to]]` — Phase 4 Task 9); `GET /api/reports` (`validation.synthetic.rows`, `validation.crisis.rows`, `computed_in_ms` — Tasks 3–4); `GET /api/reports/garch` (`status`, `done`, `of`, `rows`, `computed_in_ms`, `error` — Task 6); `GET /api/stress` (`scenarios[].name`, `worst`, `counter_cost` — Phase 4 Task 10); `GET /api/history` (`capacity`, `points`, `appended` — existing, `lib/server.ml:125–130`; §08 differences `appended`); the suite's own `ok` / `no` / `meh` helpers, `$BASE`, and its python3-or-grep pattern from assertion 3; Phase 2 Task 15's block 4a and the marker comment that closes it, `# -- Phase 5's block 4b (the routes the page reads) is inserted immediately below this line --`.
- Produces: five assertion lines Phase 6 quotes into `docs/status.md`: `GET /api/graph              N nodes, M edges, every edge endpoint served` · `GET /api/reports            18 validation rows (9 synthetic, 9 crisis), computed in N ms` · `GET /api/reports/garch      done, 6 rows in N ms on a second domain (after Ns of polling)` · `GET /api/stress             12 scenarios, worst <name>, N node recomputations on forks` · `GET /api/history            capacity 500, N points, appended M`. **Insertion point:** immediately below Phase 2's marker comment, so the suite reads build (4a) → the page's routes (4b) → TLS. Final totals, with block 4a's three assertions and Task 7's eleven-route 404 list: locally without `--expect-sha`, `12 passed, 0 failed, 3 skipped`; with it, `14 passed, 0 failed, 2 skipped`; on the droplet with `--live` and `--expect-sha`, `23 passed, 0 failed, 0 skipped` (Phase 2's eighteen plus these five).

**Why after the stream probe.** The study starts after the socket binds and takes five to fifteen seconds on the droplet; `deploy.sh` sleeps 15 s and the stream probe then spends another `SSE_WINDOW` (20 s), so by the time this block runs a healthy engine is done and the poll below exits on its first read. Putting it earlier would make the poll wait on every deploy and make a slow domain indistinguishable from a normal one. The 120 s bound is the spec's, and past it the failure names the container log's `garch` line as the first thing to read.

- [ ] **Step 1: Write the failing test.** The suite is its own test, so the failing case is the suite as it stands not mentioning the routes at all, and the passing case is the five `PASS` lines against a demo. Record the baseline:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && grep -c 'api/reports\|api/graph\|api/stress\|api/history' deploy/smoke.sh; grep -c "Phase 5's block 4b" deploy/smoke.sh; (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && deploy/smoke.sh http://localhost:8099 | tail -12; pkill -f "main.exe demo 8099"
  ```

- [ ] **Step 2: Run it and see it fail.** Expected from Step 1: `0`, then `1` (the marker is there), then the suite's four original `PASS` lines, Phase 2's block 4a (`PASS GET /api/ops … mode demo`, `SKIP build sha not given`, `PASS GET /ops`, `PASS GET /api/nope 404, lists the 11 routes in the table's order`), the two `SKIP`s and `7 passed, 0 failed, 3 skipped` — not one line about the routes the page is drawn from.

- [ ] **Step 3: Minimal implementation.** In `deploy/smoke.sh`, Phase 2's block 4a ends with this marker comment and one blank line (they are the anchor; nothing in them changes):

  ```bash
  # -- Phase 5's block 4b (the routes the page reads) is inserted immediately below this line --

  ```

  and the next non-blank line is the `# ----` rule above `# 5. TLS, and the redirect onto it (production only)`. Insert the following immediately below the marker comment, tab-indented as the file is:

  ```bash
  # ---------------------------------------------------------------------------
  # 4b. The routes the page reads
  #
  # The page below the ledger is drawn from five JSON routes, and a deploy that
  # serves the dashboard with any of them missing or malformed renders a page
  # full of em dashes -- valid HTML, 200 everywhere, and wrong. Each assertion
  # parses the body and checks the one property the page cannot draw without:
  # a topology whose edges all land on served nodes, eighteen validation rows,
  # a GARCH study that finished, twelve scenarios with a named worst, and a
  # history ring whose appended count is the one number a fork cannot inflate.
  #
  # GARCH is the only one that can legitimately be unfinished: the study runs
  # on a second domain after the socket binds and takes five to fifteen seconds
  # on the droplet. It is polled here, AFTER the stream probe has spent
  # SSE_WINDOW seconds, so a healthy engine is usually done by the first read;
  # the 120 s bound is the budget past which something is wrong with the
  # domain, not with the deploy.
  # ---------------------------------------------------------------------------
  if command -v python3 >/dev/null 2>&1; then
  	graph=$(curl -sS --max-time 15 "$BASE/api/graph" 2>/dev/null | python3 -c '
  import json, sys
  try:
      g = json.load(sys.stdin)
  except Exception as e:
      print("NOTJSON %s" % e); raise SystemExit
  nodes = g.get("nodes") or []; edges = g.get("edges") or []
  names = set(n.get("name") for n in nodes)
  if not nodes or not edges:
      print("EMPTY %d nodes, %d edges" % (len(nodes), len(edges))); raise SystemExit
  loose = [e for e in edges if e[0] not in names or e[1] not in names]
  if loose:
      print("LOOSE %d edge(s) name a node that is not served, e.g. %s" % (len(loose), loose[0])); raise SystemExit
  print("OK %d nodes, %d edges, every edge endpoint served" % (len(nodes), len(edges)))
  ' 2>/dev/null)
  	case "$graph" in
  	OK*) ok "GET /api/graph              ${graph#OK }" ;;
  	*)   no "GET /api/graph              malformed" "${graph:-no response}" ;;
  	esac

  	reports=$(curl -sS --max-time 15 "$BASE/api/reports" 2>/dev/null | python3 -c '
  import json, sys
  try:
      r = json.load(sys.stdin)
  except Exception as e:
      print("NOTJSON %s" % e); raise SystemExit
  v = r.get("validation") or {}
  syn = (v.get("synthetic") or {}).get("rows") or []
  cri = (v.get("crisis") or {}).get("rows") or []
  if len(syn) != 9 or len(cri) != 9:
      print("ROWS %d synthetic + %d crisis rows, expected 9 + 9" % (len(syn), len(cri))); raise SystemExit
  print("OK 18 validation rows (9 synthetic, 9 crisis), computed in %.0f ms" % (r.get("computed_in_ms") or 0))
  ' 2>/dev/null)
  	case "$reports" in
  	OK*) ok "GET /api/reports            ${reports#OK }" ;;
  	*)   no "GET /api/reports            malformed" "${reports:-no response}" ;;
  	esac

  	garch=""; waited=0
  	while [ "$waited" -le 120 ]; do
  		garch=$(curl -sS --max-time 15 "$BASE/api/reports/garch" 2>/dev/null | python3 -c '
  import json, sys
  try:
      g = json.load(sys.stdin)
  except Exception as e:
      print("NOTJSON %s" % e); raise SystemExit
  if g.get("error"):
      print("ERROR %s" % g["error"]); raise SystemExit
  if g.get("status") != "done":
      print("COMPUTING %s of %s" % (g.get("done"), g.get("of"))); raise SystemExit
  rows = g.get("rows") or []
  print("OK done, %d rows in %.0f ms on a second domain" % (len(rows), g.get("computed_in_ms") or 0))
  ' 2>/dev/null)
  		case "$garch" in COMPUTING*) ;; *) break ;; esac
  		/bin/sleep 5; waited=$((waited + 5))
  	done
  	case "$garch" in
  	OK*)        ok "GET /api/reports/garch      ${garch#OK } (after ${waited}s of polling)" ;;
  	COMPUTING*) no "GET /api/reports/garch      still ${garch#COMPUTING } fits after 120s" \
  			"the second domain has not finished; read the container log's garch line first" ;;
  	ERROR*)     no "GET /api/reports/garch      the study raised" "${garch#ERROR }" ;;
  	*)          no "GET /api/reports/garch      malformed" "${garch:-no response}" ;;
  	esac

  	# /api/stress computes on request -- twelve forks -- so it gets a longer
  	# timeout than the cached routes and is read exactly once.
  	stress=$(curl -sS --max-time 30 "$BASE/api/stress" 2>/dev/null | python3 -c '
  import json, sys
  try:
      s = json.load(sys.stdin)
  except Exception as e:
      print("NOTJSON %s" % e); raise SystemExit
  sc = s.get("scenarios") or []
  names = [x.get("name") for x in sc]
  if len(sc) != 12:
      print("SCENARIOS %d, expected 12" % len(sc)); raise SystemExit
  if s.get("worst") not in names:
      print("WORST %r is not a scenario name" % (s.get("worst"),)); raise SystemExit
  cost = s.get("counter_cost")
  if not isinstance(cost, int) or cost <= 0:
      print("COST counter_cost=%r, expected a positive int" % (cost,)); raise SystemExit
  print("OK 12 scenarios, worst %s, %d node recomputations on forks" % (s["worst"], cost))
  ' 2>/dev/null)
  	case "$stress" in
  	OK*) ok "GET /api/stress             ${stress#OK }" ;;
  	*)   no "GET /api/stress             malformed" "${stress:-no response}" ;;
  	esac

  	# /api/history: the ring §08 differences, and the one counter a fork
  	# cannot inflate. capacity is the configured 500; appended >= points
  	# because the ring keeps at most capacity of everything it was given.
  	hist=$(curl -sS --max-time 15 "$BASE/api/history" 2>/dev/null | python3 -c '
  import json, sys
  try:
      h = json.load(sys.stdin)
  except Exception as e:
      print("NOTJSON %s" % e); raise SystemExit
  cap, pts, app = h.get("capacity"), h.get("points"), h.get("appended")
  if cap != 500:
      print("CAPACITY %r, expected 500" % (cap,)); raise SystemExit
  if not isinstance(pts, int) or not isinstance(app, int) or app < pts:
      print("RING appended=%r points=%r, expected appended >= points" % (app, pts)); raise SystemExit
  print("OK capacity 500, %d points, appended %d" % (pts, app))
  ' 2>/dev/null)
  	case "$hist" in
  	OK*) ok "GET /api/history            ${hist#OK }" ;;
  	*)   no "GET /api/history            malformed" "${hist:-no response}" ;;
  	esac
  else
  	meh "report routes              python3 unavailable for a real parse; /api/graph, /api/reports, /api/reports/garch, /api/stress, /api/history not checked"
  fi

  ```

  The python blocks are written the way assertion 3's is — a word first, detail after — so the `case` reads the word and the human reads the detail. `set -u` is on: every variable read is assigned first, and the poll loop initialises `garch` before the `case` that could otherwise read it unset.

- [ ] **Step 4: Run the tests and see them pass.** Against a demo, then against nothing:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && bash -n deploy/smoke.sh && echo SYNTAX-OK && (./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4 && deploy/smoke.sh http://localhost:8099 | tail -17; pkill -f "main.exe demo 8099"
  ```
  Expected: `SYNTAX-OK`, then after the four original lines,
  ```
    PASS  GET /api/ops                mode demo, build <7 chars or unknown>, up 4 s
    SKIP  build sha                  not given (--expect-sha SHA), skipping
    PASS  GET /ops                    200, both host columns present
    PASS  GET /api/nope               404, lists the 11 routes in the table's order
    PASS  GET /api/graph              53 nodes, N edges, every edge endpoint served
    PASS  GET /api/reports            18 validation rows (9 synthetic, 9 crisis), computed in N ms
    PASS  GET /api/reports/garch      done, 6 rows in N ms on a second domain (after 0s of polling)
    PASS  GET /api/stress             12 scenarios, worst <name>, N node recomputations on forks
    PASS  GET /api/history            capacity 500, N points, appended M
    SKIP  TLS, redirect, port exposure -- localhost harness, not applicable
    SKIP  live host                  not given (--live URL), skipping

    12 passed, 0 failed, 3 skipped
  ```
  (53 nodes on the demo book; `after 0s` because the 20 s stream probe outlasts the study on this machine; with `--expect-sha "$(git rev-parse HEAD)"` on a `make build` binary the `SKIP build sha` line becomes `PASS build.git_sha` and `PASS uptime_s` and the summary reads `14 passed, 0 failed, 2 skipped`.) Then the failure path, which must fail and must name the route:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && deploy/smoke.sh http://localhost:8098 2>&1 | grep -E 'api/(graph|reports|stress|history)|passed'; echo "exit $?"
  ```
  Expected: five `FAIL … malformed` lines each followed by `no response`, a summary with `12 failed` (the four original, block 4a's three, these five; `3 skipped`), and the pipeline's `exit 0` is grep's — run `deploy/smoke.sh http://localhost:8098 >/dev/null 2>&1; echo $?` to see the suite's own `1`. The garch line here fails on its first read (`no response` is not `COMPUTING`), so an absent engine costs the suite seconds, not two minutes.

- [ ] **Step 5: Commit.**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add deploy/smoke.sh && git commit -m "deploy: assert the five routes the page is drawn from, because a page of em dashes is 200 everywhere"
  ```

---

## Done when

- `make test` is green with `test_reports.ml`'s cases registered, `dune build @fmt` is clean, and Phase 3 Task 1's `phase3-baseline/gate.sh` prints six `GATE ok` lines on the final `bin/main.ml` (the one gate every phase runs; Task 8 Step 1 re-captures its baseline only if scratch was cleared).
- `ohcamel demo` serves `GET /api/reports` (one cached string: `computed_at`, `computed_in_ms`, `platform`, `scaling`, `validation.{config, synthetic, crisis}` with 9 + 9 rows, `options` labelled `SYNTHETIC`) and `GET /api/reports/garch` (200 in every state), `/api/ops.reports` reads `{static: "ready", garch: …}`, and the log carries exactly one `garch       done -- 180 of 180 fits in <N> ms on a second domain; /api/reports/garch is complete` line, after the listen line.
- `/` renders nine ruled sections in README order under the ledger, each headed by a lit fragment of Figure 1 (or its name list below 700 px), with all 24 `<data>` elements bound from the JSON their figure reads and none left `—`; `check-charts-1`, `check-charts-2`, `check-argument-1` and `check-argument-2` all print `OK` against a demo on `:8099` with no `pageerror`.
- Every figure carries one of the fixed provenance tags, every computed-and-quoted table prints `agrees with the README's table to four decimals` or the `differs … in N cells` line, and on a live-mode server every COMPUTED tag reads `SYNTHETIC BOOK, NOT THIS HOST'S`.
- §08's two bold assertions turn to `PASS` with this session's numbers within twenty seconds of load on the demo, and §07's re-run link cannot fetch twice inside five seconds.
- `deploy/smoke.sh http://localhost:8099` prints `12 passed, 0 failed, 3 skipped` (`14 passed, 0 failed, 2 skipped` with `--expect-sha` on a `make build` binary), its 404 line lists the 11 routes, and `deploy/smoke.sh http://localhost:8098` exits 1 naming all five routes.
- No route mutates, nothing persists, no external asset is fetched, `argument.js` performs no risk arithmetic beyond the named `|weight|` and `1/diversification`, and the Caddyfile, the live host's gate and every `test_graph.ml` recomputation pin are untouched.
