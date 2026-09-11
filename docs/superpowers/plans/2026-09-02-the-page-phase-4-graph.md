# The page — Phase 4: the graph on the wire — Implementation Plan

> **Superseded 2026-09-10 for what was left of it.** All 14 tasks below are done. The wrap-up (Figure 1 fit, review, merge, deploy) is items 1–3 of `2026-09-10-the-page-finish-line.md`, which is now the only plan that sets order and scope.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Incremental dependency graph itself on the wire — every named node, every declared edge, taken from Incremental's own node table — and draw it on `/` with each frame's recomputation set lit on it.

**Architecture:** `lib/graph.ml` labels every node it already names (and every input cell) with `Incremental.append_user_info_graphviz`, then `Graph.topology` walks this graph's own observers with `Incremental.For_analyzer.traverse`, contracts the unnamed plumbing so edges run named-to-named, ranks by longest path from the Vars, and returns a `Topology.t`. `lib/server.ml` memoises that as `/api/graph` at `Server.create`, drains the Phase 3 recompute log in the broadcaster (and only there) into each frame's `recomputed`, and grows `json_of_snapshot` with the per-node values and the attribution fields the drawing reads. `web/graph.js` turns the served topology into inline SVG — rank columns, symbol rows, hairline cubic edges — and lights the nodes and incoming edges named by the frame.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route: every route stays a GET with no effect; nothing arms, trips, resets, submits, restarts or persists.
- No persistence: every report is computed into process memory and is gone at restart; the history ring stays 500 points.
- No external asset: no CDN, no web font, no charting library, no framework — every chart is inline SVG drawn by hand in the idiom the sparklines already use.
- The live host's gate is untouched: no Caddy CORS, no `basic_auth` matcher exemption, no `/api/up`.
- No invented vol surface on either deployed book.
- No number this process did not produce styled as if it had.
- No second implementation of anything the engine computes: the client derives closures from the served topology and tallies from served frames; it never recomputes risk.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged.
- The eight invariants in `docs/status.md` (§ *The invariants*) hold.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in the repo; `dune fmt` reformats dune files too and the ubuntu CI leg checks them).
- All tests stay hermetic: no network, no credentials, nothing waiting on a clock.
- The byte-identical stdout gate for the six credential-free modes (`synthetic`, `stress`, `backtest`, `backtest-crisis`, `options`, `garch`) applies wherever `bin/main.ml` is touched — Task 10 captures the baseline and diffs against it.

---

## Working conventions for this phase

- **Commands.** Run everything from the repository root, `/Users/ajaiupadhyaya/Documents/OhCamel`. The opam switch is project-local, so every raw dune command is `eval $(opam env --switch=$PWD --set-switch) && dune …`, or use the `make` target that already does it.
- **Ignore `claudecodehandoff.md`** at the repository root. It is untracked and belongs to a different project. Never stage it.
- **Commit per task**, in this repository's voice: a lowercase area prefix (`graph:`, `server:`, `web:`, `types:`, `stress:`, `deploy:`, `docs:`) then a sentence that says *why*. The executor adds the trailers; do not write them.
- **Never rely on record-field evaluation order.** OCaml leaves it unspecified and in practice evaluates right-to-left in *declaration* order, so a `{ …; xs = !xs }` field can be read before the fields whose evaluation pushed onto `xs`. Where this plan accumulates into a ref and then stores it, it binds `let xs = !xs in` *before* the record literal. `lib/graph.ml`'s existing `releases = !releases` happens to work today (verified: a destroyed graph's node bodies run 0 times on a later stabilize) but is not a pattern to copy.
- **Phases 1–3 are already merged** when this plan starts. Phase 1 created `web/head.html`, `web/page.css`, `web/index.html`, `web/dashboard.js`, `web/quoted.json`, `web/ops.html`, `web/ops.js`, deleted `lib/dashboard_html.ml`, and added the `lib/dune` rules that generate `dashboard_html.ml`, `ops_html.ml`, `crisis_csv.ml`, `build_info.ml` and `quoted.ml`. Phase 2 gave `Server.t` its `mode`, `started_at`, `port`, `peer`, `feed_stats` and `quiet` fields, the routes table, `/api/ops` and demo-mode CORS. Phase 3 produced `lib/recompute_log.ml`, `lib/scaling_probe.ml`, `lib/synthetic_book.ml`, `lib/validation_report.ml`, `lib/options_walk.ml` and `lib/garch_study.ml`.
- **The CSS tokens this phase uses already exist** in `web/page.css`, moved verbatim from the old `lib/dashboard_html.ml`: `--ground --panel --ink --ink-soft --ink-faint --rule --over --over-wash --unknown --unknown-wash --live --mark`, and the classes `.lbl` (10px, 0.14em, uppercase, `--ink-faint`), `.num` (tabular monospace), `td.k`, `td.v`, `.moved` (the 0.75 s `--mark` underline), `main.stale` (the hatch).
- **Running the demo for a browser check:** `make demo` blocks and a backgrounded Bash tool call takes the server down with it. Always detach:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel
  eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -3
  nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
  echo $! > /tmp/ohcamel-demo.pid
  ```
  and stop it with `kill "$(cat /tmp/ohcamel-demo.pid)"`.

---

## What this phase relies on that already exists

Read these before Task 1; every signature below was read out of the working tree or out of `_opam/lib` on 2026-09-03.

**`lib/graph.ml` (1791 lines), the file this phase modifies:**

| Line | What is there |
|---|---|
| 88–127 | `module Node_name`, the public contract of node label strings |
| 455 | `let create ?(on_compute = fun (_ : string) -> ()) ?(starting_cash = Notional.zero)` — the hook is a construction-time closure and nothing can change it later |
| 539 | `let note (name : string) = on_compute name in` |
| 540–541 | `let releases = ref [] in` / `let change_listeners = ref [] in` |
| 542–558 | `let observe node = … Inc.Observer.on_update_exn o ~f:… ; o in` — the only place observers are made, and the only place `releases` is pushed |
| 564–570 | `let make_var ~equal init = … Inc.set_cutoff (Inc.Var.watch v) (Inc.Cutoff.of_equal equal); v in` |
| 405–430 | the 27 `obs_*` fields of `type t` |
| 1350–1378 | the 27 `obs_* = observe …` fields of the record literal |
| 1379 | `releases = !releases;` |
| 1391–1424 | `let fork ?on_compute ?limits t` — **does not inherit the hook**, stated in its comment |
| 1746 | `let snapshot (t : t) : Snapshot.t = Inc.stabilize (); …` — snapshot stabilizes |
| 1789–1791 | `total_nodes_recomputed`, `total_stabilizes` |

**`lib/server.ml` (528 lines before Phase 2):**

| Line | What is there |
|---|---|
| 52–63 | `jfloat`, `jopt_float`, `jnotional`, `jopt_notional`, `jstring`, `jlist` — the house encoders |
| 91–111 | `json_of_breach` |
| 140–238 | `json_of_snapshot ~graph ~factor s` |
| 247–286 | `json_of_stress graph` |
| 351–357 | `let render (t : t) : string = let snapshot = Graph.snapshot t.graph in …` |
| 423 | `Pipe.write writer (sse_event (render t))` — every new subscriber's welcome frame |
| 444 | `| "/api/snapshot" -> … (render t)` — the poller |
| 379–389 | `run_broadcaster` |

**Verified against `_opam/lib` (Incremental v0.17.0), and re-verified by compiling and running a probe on this switch on 2026-09-03:**

- `_opam/lib/incremental/incremental_intf.ml:1675-1679` — `val append_user_info_graphviz : _ t -> label:string list -> attrs:string String.Map.t -> unit`
- `_opam/lib/incremental/incremental_intf.ml:1668` — `val user_info : _ t -> Info.t option` (lossy — it stringifies the sexp; **not** what this phase reads)
- `_opam/lib/incremental/incremental_intf.ml:1701` — `val pack : _ t -> Packed.t`
- `_opam/lib/incremental/incremental_intf.ml:1453` — `val observing : ('a, 'w) t -> ('a, 'w) incremental`
- `_opam/lib/incremental/incremental_intf.ml:2009-2010` — `module For_analyzer : For_analyzer_intf.S with type packed_node := Packed.t and type 'a state := 'a State.t`. It is a **top-level** member of `Incremental`, not of `Incremental.Make ()`, so it is reached as `Incremental.For_analyzer` and its `Packed.t` is the same type `Inc.pack` returns (`module type S … with type Packed.t = Packed.t`, `incremental_intf.ml:1981`).
- `_opam/lib/incremental/for_analyzer_intf.ml:98` — `val directly_observed : _ state -> packed_node list` (process-wide; **not** used here, because a stress fork alive in the same process would walk into the drawing)
- `_opam/lib/incremental/for_analyzer_intf.ml:100-112`:
  ```ocaml
  val traverse
    :  packed_node list
    -> add_node:
         (id:Node_id.t
          -> kind:Kind.t
          -> cutoff:Cutoff.t
          -> children:Node_id.t list
          -> bind_children:Node_id.t list
          -> user_info:Dot_user_info.t option
          -> recomputed_at:Stabilization_num.t
          -> changed_at:Stabilization_num.t
          -> height:int
          -> unit)
    -> unit
  ```
- `_opam/lib/incremental/for_analyzer_intf.ml:60-70` — `Dot_user_info.dot = { label : (string list, String.comparator_witness List.comparator_witness) Set.t ; attributes : string String.Map.t }` and `val to_dot : t -> dot`. `_opam/lib/incremental/dot_user_info.ml:41` — repeated `append_user_info_graphviz` calls **union** the label sets, which is why one node can carry both its `Node_name` and an `@observed` marker.
- `_opam/lib/incremental/for_analyzer_intf.ml:83-93` — `Node_id.to_int : t -> int`; `_opam/lib/incremental/for_analyzer.ml:90` — `module Node_id = Int`.
- `_opam/lib/incremental/for_analyzer_intf.ml:4-16` — `Cutoff.t = Always | Never | Phys_equal | Compare | Equal | F` with `to_string`. `Inc.Cutoff.of_equal` lands on `Equal` (probe output: `cutoff=Equal`).
- `_opam/lib/incremental/for_analyzer_intf.ml:18-56` — `Kind.t` including `Var`, `Const`, `Map`, `Map2`…`Map15`, `Array_fold`, with `to_string`. `Inc.all` builds an `Array_fold`; `Inc.all []` builds a **`Const`** with no children (probe output).
- `_opam/lib/incremental/node.ml:672-685` — `iter_descendants` dedups by node id, so `traverse` visits every node reachable from the roots exactly once, parent before children.
- `_opam/lib/incremental/incremental_intf.ml:1064-1083` — `State.num_nodes_created`, `num_var_sets`, `num_active_observers`, `num_nodes_recomputed`, `num_stabilizes`, all `_ t -> int`.

**`For_analyzer` gives children, so no hand-written adjacency is needed.** The fallback the spec named — a hand-written adjacency beside `Node_name` guarded by the closure tests — is **not** taken. The probe returned `price[AAPL]->gross_exposure` across two layers of unnamed plumbing (`Inc.map`, `Inc.all`), which is exactly the contraction Task 3 needs.

---

### Task 1: `Node_name.Input` and `Node_name.unit_of`

The names of the twelve input-cell shapes, and the unit every node's value is in. Pure string functions, so they are testable before a single node is labelled.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/graph.ml:88-127` (the `Node_name` module)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`

**Interfaces:**
- Consumes: `Types.Symbol.to_string : Symbol.t -> string` (`lib/types.ml:53`); the existing `Node_name` functions at `lib/graph.ml:89-126`.
- Produces:
  - `Graph.Node_name.Input.price : Symbol.t -> string` (`"price[AAPL]"`), `.qty`, `.returns`, `.last_tick` — same shape.
  - `Graph.Node_name.Input.contracts : string -> string` (`"contracts[NVDA-950C-30D]"`), `.implied_vol` — same shape.
  - `Graph.Node_name.Input.cash : string`, `.equity_history`, `.factor_returns`, `.rate`, `.valuation_days`, `.now`.
  - `Graph.Node_name.unit_of : string -> string`, total over every name this graph can produce, raising on anything else.
  - `Graph.Node_name.is_scalar : string -> bool` — true for `usd fraction ratio count price qty time`, false for `matrix array map state`.

- [ ] **Step 1: Write the failing test**

Add to `test/test_graph.ml`, immediately above `let test_position_change_is_local ()` (line 376):

```ocaml
(* ------------------------------------------------------------------------ *)
(* 2a. Node names and their units                                            *)
(* ------------------------------------------------------------------------ *)

(* The input cells are named with BRACKETS and the derived per-symbol nodes
   with a COLON, and the difference is load-bearing rather than cosmetic: the
   drawing puts cells in their own column, and deriving that from the name
   means there is no second table saying which names are cells for a refactor
   to leave behind. *)
let test_input_names () =
  Alcotest.(check string) "price cell" "price[AAPL]" (Graph.Node_name.Input.price aapl);
  Alcotest.(check string) "qty cell" "qty[AAPL]" (Graph.Node_name.Input.qty aapl);
  Alcotest.(check string)
    "return window" "returns[MSFT]"
    (Graph.Node_name.Input.returns msft);
  Alcotest.(check string)
    "liveness cell" "last_tick[XOM]"
    (Graph.Node_name.Input.last_tick xom);
  Alcotest.(check string)
    "contract count" "contracts[NVDA-950C]"
    (Graph.Node_name.Input.contracts "NVDA-950C");
  Alcotest.(check string)
    "vol mark" "implied_vol[NVDA-950C]"
    (Graph.Node_name.Input.implied_vol "NVDA-950C");
  Alcotest.(check string) "cash" "cash" Graph.Node_name.Input.cash;
  Alcotest.(check string) "now" "now" Graph.Node_name.Input.now;
  (* The two clocks are two names. graph.ml keeps them apart because they tick
     at different rates and only one of them is allowed upstream of a risk
     node; a page that called both "clock" would erase the distinction the
     module spends thirty lines defending. *)
  Alcotest.(check string)
    "the slow clock" "valuation_days" Graph.Node_name.Input.valuation_days

(* A unit for every name, and no unit invented for a name that does not exist.

   [unit_of] is what the drawing formats a value with -- dollars get a dollar
   sign, a fraction gets a percent, a matrix gets a run count instead of a
   number. Getting one wrong renders a fraction as dollars, which is the exact
   failure server.ml's json_of_breach ships a unit alongside every threshold to
   prevent. *)
let test_units () =
  let unit_is expected name =
    Alcotest.(check string) (name ^ " is " ^ expected) expected (Graph.Node_name.unit_of name)
  in
  unit_is "usd" "gross_exposure";
  unit_is "usd" "net_exposure";
  unit_is "usd" "equity";
  unit_is "usd" "var_notional";
  unit_is "usd" "es_notional";
  unit_is "usd" (Graph.Node_name.exposure aapl);
  unit_is "usd" (Graph.Node_name.sector tech);
  unit_is "usd" "cash";
  (* Vega is dollars per 1.00 of annualised vol -- server.ml says so where it
     refuses to divide by a hundred on the wire -- so it is money and formats
     as money. Gamma is not: it is delta-equivalent shares per unit move, and
     the only word in this vocabulary that prints a bare number for a quantity
     of shares is [qty]. *)
  unit_is "usd" "portfolio_vega";
  unit_is "qty" "portfolio_gamma";
  unit_is "fraction" "current_drawdown";
  unit_is "fraction" "historical_var";
  unit_is "fraction" "parametric_var_ewma";
  unit_is "fraction" "rate";
  unit_is "ratio" "portfolio_beta";
  unit_is "ratio" "diversification_ratio";
  unit_is "price" (Graph.Node_name.Input.price aapl);
  unit_is "qty" (Graph.Node_name.Input.qty aapl);
  unit_is "count" (Graph.Node_name.Input.contracts "NVDA-950C");
  unit_is "fraction" (Graph.Node_name.Input.implied_vol "NVDA-950C");
  unit_is "time" "valuation_days";
  (* [now] and [last_tick[S]] hold a timestamp, not a quantity, and this file
     already argues (Feed_health's note on [age]) that publishing a clock
     reading as a node value is a stale number wearing a fresh timestamp. They
     are [state]: the drawing prints a run count under them, never a number. *)
  unit_is "state" "now";
  unit_is "state" (Graph.Node_name.Input.last_tick aapl);
  unit_is "state" "feed_health";
  unit_is "state" (Graph.Node_name.feed aapl);
  unit_is "state" (Graph.Node_name.limit "aapl-cap");
  unit_is "matrix" "covariance";
  unit_is "matrix" "covariance_ewma";
  unit_is "matrix" "aligned_returns";
  unit_is "array" "weights";
  unit_is "array" "portfolio_returns";
  unit_is "array" "breaches";
  unit_is "array" (Graph.Node_name.Input.returns aapl);
  unit_is "map" "exposure_map";
  unit_is "map" "component_var_map";
  unit_is "map" "vega_by_bucket";
  unit_is "state" "attribution";
  unit_is "state" (Graph.Node_name.greeks "NVDA-950C");
  (* Loud on a name nobody declared. A silent default would let a new node
     reach the page with a plausible unit nobody chose, which is how a
     fraction ends up rendered as dollars. *)
  Alcotest.check_raises "an undeclared name has no unit"
    (Failure "graph: no unit declared for node \"invented\"") (fun () ->
      ignore (Graph.Node_name.unit_of "invented" : string))

let test_scalar_units () =
  List.iter [ "usd"; "fraction"; "ratio"; "count"; "price"; "qty"; "time" ] ~f:(fun u ->
      Alcotest.(check bool) (u ^ " is scalar") true (Graph.Node_name.is_scalar u));
  List.iter [ "matrix"; "array"; "map"; "state" ] ~f:(fun u ->
      Alcotest.(check bool) (u ^ " is not scalar") false (Graph.Node_name.is_scalar u))
```

and register the three cases in `test_graph.ml`'s `suite`, immediately before the case named `"changing one position recomputes only its dependents"`:

```ocaml
      Alcotest.test_case "input cells are named with brackets" `Quick test_input_names;
      Alcotest.test_case "every node name has a unit" `Quick test_units;
      Alcotest.test_case "scalar units are the ones with a number" `Quick
        test_scalar_units;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -20
```

Expected: a compile error, not a test failure —

```
Error: Unbound module Graph.Node_name.Input
```

- [ ] **Step 3: Minimal implementation**

In `lib/graph.ml`, inside `module Node_name`, immediately after `let feed_health = "feed_health"` (line 126) and before the closing `end` (line 127), add:

```ocaml

  (* The input cells, named the same way the derived nodes are and for the same
     reason: the drawing takes its labels from the graph, so a cell that had no
     name would be a node the reader can see an edge into and cannot read.

     Brackets rather than the colon the derived per-symbol nodes use. A drawing
     has to put cells in their own column and everything else downstream of
     them, and deriving that from the shape of the name means the classifier is
     one function over strings rather than a second list of which names are
     cells -- which is a list that goes stale the first time a node is added. *)
  module Input = struct
    let price (s : Symbol.t) = "price[" ^ Symbol.to_string s ^ "]"
    let qty (s : Symbol.t) = "qty[" ^ Symbol.to_string s ^ "]"
    let returns (s : Symbol.t) = "returns[" ^ Symbol.to_string s ^ "]"
    let last_tick (s : Symbol.t) = "last_tick[" ^ Symbol.to_string s ^ "]"
    let contracts (id : string) = "contracts[" ^ id ^ "]"
    let implied_vol (id : string) = "implied_vol[" ^ id ^ "]"
    let cash = "cash"
    let equity_history = "equity_history"
    let factor_returns = "factor_returns"
    let rate = "rate"
    let valuation_days = "valuation_days"
    let now = "now"
  end

  (* What a node's value is measured in.

     The drawing prints a value under every scalar node and a run count under
     every other, so this is the function that decides which -- and, for the
     scalars, how the number is formatted. It exists here rather than as a
     table in the client because a client-side map from node name to unit would
     be a second description of the encoder, one refactor away from silently
     labelling a fraction as dollars. json_of_snapshot emits the values; this
     says what they are; the test in test_server.ml asserts the two agree.

     Seven scalar words and four that are not. [state] is the one worth
     explaining: it covers a node whose value is a record or a variant -- a
     limit result, a Greeks record, feed health -- and also the two CLOCK
     cells. A clock reading is a number, but publishing it as a node value is
     what Feed_health's note on [age] refuses: age changes continuously, so a
     node holding it would wake everything downstream on every tick, and a node
     reporting it after cutting off would be a stale number wearing a fresh
     timestamp. The drawing shows a run count for both instead. *)
  let unit_of (name : string) : string =
    let has prefix = String.is_prefix name ~prefix in
    if has "exposure:" || has "sector:" then "usd"
    else if has "limit:" || has "feed:" || has "greeks:" || has "option_exposure:" then
      "state"
    else if has "price[" then "price"
    else if has "qty[" then "qty"
    else if has "contracts[" then "count"
    else if has "implied_vol[" then "fraction"
    else if has "returns[" then "array"
    else if has "last_tick[" then "state"
    else
      match name with
      | "gross_exposure" | "net_exposure" | "equity" | "var_notional" | "es_notional"
      | "cash" | "portfolio_vega" ->
          "usd"
      | "current_drawdown" | "historical_var" | "expected_shortfall" | "parametric_var"
      | "parametric_var_ewma" | "rate" ->
          "fraction"
      | "portfolio_beta" | "diversification_ratio" -> "ratio"
      | "portfolio_gamma" -> "qty"
      | "valuation_days" -> "time"
      | "aligned_returns" | "covariance" | "covariance_ewma" -> "matrix"
      | "weights" | "portfolio_returns" | "equity_history" | "factor_returns" | "breaches"
        ->
          "array"
      | "exposure_map" | "sector_map" | "gamma_map" | "vega_map" | "vega_by_bucket"
      | "component_var_map" | "component_var_sector_map" ->
          "map"
      | "attribution" | "feed_health" | "now" -> "state"
      | other -> failwithf "graph: no unit declared for node %S" other ()

  (* The units whose value is a number the wire can carry and the drawing can
     print. Everything else gets a run count instead -- "ran 1x since you
     opened this page" -- which is the honest rendering of a covariance matrix
     and is also, for [covariance] sitting at 1 while [exposure:NVDA] passes
     forty, the whole thesis as a number. *)
  let is_scalar (unit : string) : bool =
    List.mem [ "usd"; "fraction"; "ratio"; "count"; "price"; "qty"; "time" ] unit
      ~equal:String.equal
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -15
```

Expected: the `graph` suite reports three more passing cases and the whole run is green.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/graph.ml test/test_graph.ml
git commit -m "graph: name the input cells and give every node a unit, because a drawing taken from the graph cannot label an edge into a node that has no name"
```

---

### Task 2: `observe_silent`, the attribution observer, and the Snapshot fields the page reads

`attribution` is computed on every tick and nothing reads it directly — `component_var_map` and `diversification_ratio` each take one field out of it and the marginal, the standalone and the Euler residual are thrown away. §02 of the page is those three numbers. This task observes the node, and does it through a release-aware helper, because a bare `Inc.observe` survives `Graph.destroy` and would leave every stress fork and every report graph recomputing attribution on every stabilize forever.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/graph.ml:277-320` (the `Snapshot` type), `:405-435` (`type t`), `:542-558` (the `observe` helper), `:1350-1379` (the record literal), `:1746-1788` (`snapshot`)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`

**Interfaces:**
- Consumes: `Attribution.marginal : t -> float array`, `Attribution.standalone : t -> float array`, `Attribution.euler_residual : t -> float` (`lib/attribution.ml:88`, `:159`); `Graph.Covariance_estimator.t` and `.to_string` (`lib/graph.ml:143-147`); `Inc.Observer.value_exn`, `Inc.Observer.disallow_future_use`.
- Produces:
  - `Graph.Snapshot.marginal_by_instrument : Snapshot.t -> float Symbol.Map.t option`
  - `Graph.Snapshot.standalone_by_instrument : Snapshot.t -> float Symbol.Map.t option`
  - `Graph.Snapshot.euler_residual : Snapshot.t -> float option`
  - `Graph.Snapshot.covariance_for_attribution : Snapshot.t -> Covariance_estimator.t`
  - `Graph.Snapshot.prices : Snapshot.t -> Price.t Symbol.Map.t`
  - `Graph.Snapshot.quantities : Snapshot.t -> Qty.t Symbol.Map.t`
  - `Graph.attribution : t -> Attribution.t option`
  - internally: `observe_silent : 'a Inc.t -> 'a Inc.Observer.t`, which Task 3 extends with the `@observed` label.

- [ ] **Step 1: Write the failing test**

Add to `test/test_graph.ml`, immediately above `let test_component_var_limits ()` (line 987):

```ocaml
(* The decomposition, finally observed.

   [attribution] has been recomputed on every tick since Phase 5 and nothing
   read it: two downstream nodes each took one field and the rest was
   discarded. These are the fields §02 of the page is made of, and the
   residual is the cheap self-check attribution.ml exposes precisely so it can
   stay true at run time rather than only in a test.

   Hand-derived from the standard book. The weights are 0.3, 0.3, -0.4 and the
   covariance is built from [base_returns] with XOM's series negated, so every
   pair is perfectly +/-1 correlated and sigma_i = sqrt(0.0011) for all three.
   Perfect correlation makes the book behave like a single asset:
     sigma_p = |0.3 + 0.3 + 0.4| * sqrt(0.0011) = sqrt(0.0011)
   because XOM's -0.4 weight against a -1 correlated series adds rather than
   cancels. So each marginal is +/- sqrt(0.0011) with the sign of the name's
   own co-movement, each standalone is |w_i| * sqrt(0.0011), and the
   standalones sum to exactly sigma_p -- a diversification ratio of 1.00,
   which is what "correlations at one" means and is the degenerate case worth
   pinning because every other ratio is a departure from it. *)
let test_attribution_fields () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      let sigma = Float.sqrt base_variance in
      let marginal =
        Option.value_exn (Graph.Snapshot.marginal_by_instrument s)
          ~message:"marginal is present on a seeded book"
      in
      let standalone =
        Option.value_exn (Graph.Snapshot.standalone_by_instrument s)
          ~message:"standalone is present on a seeded book"
      in
      Alcotest.(check (list string))
        "keyed by every symbol, none missing" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Map.keys marginal) ~f:Symbol.to_string);
      Alcotest.(check (list string))
        "and the standalone map the same" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Map.keys standalone) ~f:Symbol.to_string);
      Alcotest.check float_eq "marginal(AAPL) = sigma_p" sigma (Map.find_exn marginal aapl);
      (* XOM's series is the negation, and its weight is negative, so its
         marginal is negative and its COMPONENT -- weight times marginal -- is
         positive. A hedge is the case where those two signs differ; this book
         does not have one, and test_a_hedge_does_not_breach_a_risk_limit is
         where that case lives. *)
      Alcotest.check float_eq "marginal(XOM) = -sigma_p" (-.sigma)
        (Map.find_exn marginal xom);
      Alcotest.check float_eq "standalone(AAPL) = |w| sigma" (0.3 *. sigma)
        (Map.find_exn standalone aapl);
      Alcotest.check float_eq "standalone(XOM) = 0.4 sigma" (0.4 *. sigma)
        (Map.find_exn standalone xom);
      (* Euler is exact in real arithmetic; anything beyond accumulation error
         means the matrix and the weights have gone out of alignment, which is
         the one failure of that module that produces confident, plausible,
         entirely wrong attributions. *)
      let residual =
        Option.value_exn (Graph.Snapshot.euler_residual s)
          ~message:"the residual is present on a seeded book"
      in
      Alcotest.(check bool)
        (Printf.sprintf "|Euler residual| < 1e-9 (got %g)" residual)
        true
        (Float.( < ) (Float.abs residual) 1e-9);
      Alcotest.(check string)
        "the snapshot says which matrix was decomposed" "equal_weighted"
        (Graph.Covariance_estimator.to_string
           (Graph.Snapshot.covariance_for_attribution s));
      (* The marks and positions the exposures above came from, read out of the
         same fixed point rather than fetched afterwards through a getter. *)
      Alcotest.check float_eq "price[AAPL] on the snapshot" 150.0
        (Price.to_float (Map.find_exn (Graph.Snapshot.prices s) aapl));
      Alcotest.check float_eq "qty[XOM] keeps its sign" (-400.0)
        (Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) xom)))
    ()

(* Unknown, never zero. A book with no return history has no covariance to
   decompose, and a marginal of 0.0 renders on a page as "this position
   contributes no risk" -- the single most dangerous wrong answer this system
   can give, which risk_metrics.ml names in as many words. *)
let test_attribution_fields_are_none_while_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph _ ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let s = Graph.snapshot graph in
      Alcotest.(check bool)
        "marginal is None" true
        (Option.is_none (Graph.Snapshot.marginal_by_instrument s));
      Alcotest.(check bool)
        "standalone is None" true
        (Option.is_none (Graph.Snapshot.standalone_by_instrument s));
      Alcotest.(check bool)
        "and so is the residual" true
        (Option.is_none (Graph.Snapshot.euler_residual s)))
    ()

(* The new observer must be invisible to everything that was already measured.

   It adds no named node -- [attribution] was already named and already ran --
   and it carries NO update handler, so it does not wake the change listeners.
   That second half is the one worth a test: server.ml's broadcaster and
   history_buffer.ml both hang off those listeners, and an extra one firing per
   tick would move the history ring's appended count and the frame rate without
   anything on the book having changed. *)
let test_the_attribution_observer_is_silent () =
  with_graph
    ~f:(fun graph recorder ->
      let wakeups = ref 0 in
      Graph.on_change graph ~f:(fun () -> incr wakeups);
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      (* The pinned set, unchanged. This is the assertion that says the new
         observer bought a value on the wire and cost nothing on the tick
         path. *)
      check_recomputed recorder
        ~msg:"a tick recomputes exactly what it did before the attribution observer"
        ~expected:downstream_of_aapl_tick;
      (* One wakeup per observer whose VALUE changed. The attribution observer
         has no update handler at all, so it contributes none -- and the count
         is the same as it was for the same tick before this task. *)
      Alcotest.(check bool)
        (Printf.sprintf "the tick woke listeners (%d) and the count is >0" !wakeups)
        true (!wakeups > 0);
      Alcotest.(check bool)
        "the attribution observer is readable all the same" true
        (Option.is_some (Graph.attribution graph)))
    ()
```

and register them in `test_graph.ml`'s `suite`, immediately after the existing `"attribution is None while warming up"` case:

```ocaml
      Alcotest.test_case "the attribution snapshot fields" `Quick test_attribution_fields;
      Alcotest.test_case "attribution fields are None while warming up" `Quick
        test_attribution_fields_are_none_while_warming_up;
      Alcotest.test_case "the attribution observer adds no node and no listener" `Quick
        test_the_attribution_observer_is_silent;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -20
```

Expected: a compile error —

```
Error: Unbound value Graph.Snapshot.marginal_by_instrument
```

- [ ] **Step 3: Minimal implementation**

**(a)** In `lib/graph.ml`, inside `module Snapshot`, replace line 283 —

```ocaml
    diversification_ratio : float option;
```

with:

```ocaml
    diversification_ratio : float option;
    (* The two Euler quantities the decomposition produces alongside the
         component shares, keyed by symbol here rather than left as the arrays
         attribution.ml returns.

         [Attribution.t] carries them positionally, aligned to [weights], which
         is aligned to [Map.keys instruments]. That alignment is a fact this
         file guarantees and no consumer can check -- get it wrong and the page
         reports NVDA's risk under XOM's name, internally consistent and about
         the wrong book. Zipping them here, once, beside the code that
         established the order, is what turns the convention into a fact.

         Read the two together and the difference matters: marginal is a RATE
         (how portfolio risk moves per unit of weight -- "should I add or
         trim") and standalone is an AMOUNT (|w_i| sigma_i, what the position
         would contribute if it moved with everything else). The product of the
         weight and the marginal is the component, already published in dollars
         as [component_var_by_instrument]; these two are in return space, as
         attribution.ml computes them, and the encoder converts nothing. *)
    marginal_by_instrument : float Symbol.Map.t option;
    standalone_by_instrument : float Symbol.Map.t option;
    (* Sum of the components minus portfolio sigma. Exact in real arithmetic by
         Euler's theorem, so anything here beyond float accumulation error means
         the covariance matrix and the weights have gone out of alignment --
         the one failure of attribution.ml that produces confident, plausible,
         entirely wrong numbers. Published rather than only asserted in a test,
         because a self-check that runs at run time on the real book is worth
         more than one that runs at build time on a seeded one. *)
    euler_residual : float option;
    (* Which of the two matrices the decomposition above read. Fixed at
         construction, and carried in the snapshot rather than assumed by the
         reader for exactly the reason [ewma_lambda] is: two runs under
         different choices publish different numbers under one field name, and
         a wire format that does not say which is one that cannot be compared
         against itself later. *)
    covariance_for_attribution : Covariance_estimator.t;
```

**(b)** In the same `Snapshot` type, replace line 316 —

```ocaml
    unevaluated_limits : string list;
```

with:

```ocaml
    unevaluated_limits : string list;
    (* The marks and the positions every exposure above was computed from.

         Here rather than read back through [Graph.price] and [Graph.qty] by the
         encoder, and the reason is the one stated at the head of this module: a
         snapshot is a consistent read of ONE fixed point of the graph, and a
         getter called afterwards reads whatever the cells hold then. The page
         prints price and quantity on the same row as the exposure they
         multiply out to; a row whose three numbers came from two different
         moments is the kind of inconsistency that makes a risk display
         untrustworthy in exactly the minute it matters. *)
    prices : Price.t Symbol.Map.t;
    quantities : Qty.t Symbol.Map.t;
```

**(c)** Replace the `observe` helper at `lib/graph.ml:542-558` — from `let observe node =` through the `in` — with:

```ocaml
  (* Observe a node and remember how to let it go.

     Split from [observe] below because the attribution node needs the first
     half and must not have the second. A bare [Inc.observe] would survive
     [destroy] entirely: the observer would stay live, the node would stay
     necessary, and every stress fork and every report graph in this process
     would keep recomputing an abandoned book's attribution on every stabilize,
     forever. The releases list is the only thing that prevents that, so
     nothing in this module may create an observer outside these two
     functions. *)
  let observe_silent node =
    let o = Inc.observe node in
    releases := (fun () -> Inc.Observer.disallow_future_use o) :: !releases;
    o
  in
  let observe node =
    let o = observe_silent node in
    (* Every published value carries an update handler, which is what lets a
       consumer be told that something changed instead of asking. Incremental
       fires these only when a value actually changes -- the cutoffs upstream
       have already decided that -- so a quiet market produces no callbacks at
       all rather than a stream of "still the same".

       This is the last link in the chain the project is arguing for. Without
       it, a dashboard would have to poll the engine, and an engine that is
       reactive internally but polled at its edge has moved the timer rather
       than removed it.

       [observe_silent] is the deliberate exception and there is exactly one
       user of it: [attribution] is observed so its arrays can be read out, and
       it is observed WITHOUT a handler because [component_var_map] is already
       observed, changes whenever attribution does, and already wakes the same
       listeners. A second handler on the same event would double the history
       ring's append rate to publish nothing new. *)
    Inc.Observer.on_update_exn o ~f:(fun _ ->
        List.iter !change_listeners ~f:(fun listener -> listener ()));
    o
  in
```

**(d)** In `type t`, replace line 423 —

```ocaml
  obs_diversification_ratio : float option Inc.Observer.t;
```

with:

```ocaml
  obs_diversification_ratio : float option Inc.Observer.t;
  (* The decomposition itself, not just the two numbers taken out of it.

       Created through [observe_silent], so it is released by [destroy] like
       every other observer and carries no update handler -- see the note
       there. It adds no node: [attribution] was already necessary, because
       [component_var_map] reads it. *)
  obs_attribution : Attribution.t option Inc.Observer.t;
```

**(e)** In the record literal, replace line 1370 —

```ocaml
      obs_component_var_by_sector = observe component_var_sector_node;
```

with:

```ocaml
      obs_component_var_by_sector = observe component_var_sector_node;
      obs_attribution = observe_silent attribution_node;
```

**(f)** After the `let limit_results` definition and before `let snapshot` (line 1745), add:

```ocaml
(* The Euler decomposition as attribution.ml returns it: arrays in the order of
   [weights], which is [Map.keys instruments]. [Snapshot] zips them against the
   symbols; this is here for a caller that wants the record. *)
let attribution (t : t) : Attribution.t option = Inc.Observer.value_exn t.obs_attribution
```

**(g)** In `snapshot`, replace lines 1746-1749 —

```ocaml
let snapshot (t : t) : Snapshot.t =
  Inc.stabilize ();
  let results = limit_results t in
  let historical_var = historical_var t in
```

with:

```ocaml
let snapshot (t : t) : Snapshot.t =
  Inc.stabilize ();
  let results = limit_results t in
  let historical_var = historical_var t in
  let attribution = attribution t in
  (* One zip, used twice. [symbols t] is [Map.keys t.instruments] and the
     arrays came from [weights], which is [Map.data] of the same map, so the
     two line up -- the alignment the whole risk chain rests on, asserted here
     by [List.zip_exn] raising rather than by a comment. *)
  let by_symbol (field : Attribution.t -> float array) =
    Option.map attribution ~f:(fun a ->
        Symbol.Map.of_alist_exn (List.zip_exn (symbols t) (Array.to_list (field a))))
  in
```

**(h)** In the same record literal, replace the line `    diversification_ratio = diversification_ratio t;` with:

```ocaml
    diversification_ratio = diversification_ratio t;
    marginal_by_instrument = by_symbol Attribution.marginal;
    standalone_by_instrument = by_symbol Attribution.standalone;
    euler_residual = Option.map attribution ~f:Attribution.euler_residual;
    covariance_for_attribution = t.covariance_for_attribution;
    prices = Map.map t.price_vars ~f:Inc.Var.value;
    quantities = Map.map t.qty_vars ~f:Inc.Var.value;
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -15
```

Expected: green, and in particular the pre-existing `test_price_tick_is_local`, `test_position_change_is_local`, `test_return_push_is_local` and `test_clock_cannot_reach_the_risk_chain` still pass with their sets unchanged — that is the assertion that this observer cost nothing.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/graph.ml test/test_graph.ml
git commit -m "graph: observe the decomposition itself, because the marginal and the residual were computed on every tick and thrown away"
```

---

### Task 3: `named` — every node the hook names is labelled on the node itself, and the label table is read back through `For_analyzer`

`Graph.create` names every derived node at its `note Node_name.x` site, but the name lives only in the closure the hook calls; Incremental's own node table knows nothing of it. This task puts the same string ON the node with `append_user_info_graphviz`, puts each input cell's `Node_name.Input` name on its watch node, marks each observed node with a second label, and adds the traverse that reads the table back. Task 4 builds the topology on that traverse; this task stops at a flat label list so the labelling is testable on its own.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/graph.ml` — `Node_name` (after Task 1's `is_scalar`), `make_var` (the `let make_var ~equal init =` at :564 of the 7e4a265 tree), `observe_silent` (Task 2's), every node-construction site inside `create`, and a new block after `let destroy`
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`

**Interfaces:**
- Consumes:
  - `val append_user_info_graphviz : _ t -> label:string list -> attrs:string String.Map.t -> unit` — `_opam/lib/incremental/incremental_intf.ml:1675-1679`; inside `Incremental.Make ()`'s `S`, `_ t` is `_ Inc.t`. Repeated calls UNION the label sets (`_opam/lib/incremental/dot_user_info.ml:41-50`, `Append` → `Set.union prior.label new_.label`), which is what lets one node carry both its name and the observed marker.
  - `Inc.Var.watch v` returns the Var's one watch node — `_opam/lib/incremental/incremental.ml:126`, `let watch t = t.watch` — so a label put on it once is on the node every later `Inc.Var.watch v` returns.
  - `val observing : ('a, 'w) t -> ('a, 'w) incremental` — `incremental_intf.ml:1453`; `val pack : _ t -> Packed.t` — `:1701`; `Inc.Packed.t` is `Incremental.Packed.t` by the `with type Packed.t = Packed.t` constraint at `:1981`.
  - `Incremental.For_analyzer.traverse : Packed.t list -> add_node:(id:Node_id.t -> kind:Kind.t -> cutoff:Cutoff.t -> children:Node_id.t list -> bind_children:Node_id.t list -> user_info:Dot_user_info.t option -> recomputed_at:Stabilization_num.t -> changed_at:Stabilization_num.t -> height:int -> unit) -> unit` — `_opam/lib/incremental/for_analyzer_intf.ml:100-112`; `children` are the nodes the visited node READS (`for_analyzer.ml:118-121`, `Node.iteri_children`), and `Node.Packed.iter_descendants` visits each reachable node once (`node.ml:672-685`).
  - `Incremental.For_analyzer.Dot_user_info.to_dot : t -> dot` with `dot = { label : (string list, …) Set.t; attributes : … }` — `for_analyzer_intf.ml:60-70`; `Node_id.to_int` — `:83-93`; `Kind.to_string`, `Cutoff.to_string` — `:14, :56` (both `Sexp.to_string` of the constructor, so `Var`, `Map2`, `Const`, `Array_fold`, `Equal`, `Phys_equal`).
  - Task 2's `observe_silent` and `observe`.
- Produces:
  - `Graph.observed_marker : string` = `"@observed"`.
  - internally `named : string -> 'a Inc.t -> 'a Inc.t`; `make_var ~name ~equal init`.
  - `Graph.Raw.t = { id : int; kind : string; cutoff : string; children : int list; name : string option; observed : bool }` and `Graph.walk : t -> Raw.t list` — every node reachable from this graph's 28 observers, as Incremental reports it. Task 4 contracts it.
  - `Graph.labelled_nodes : t -> (string * bool) list` — (name, observed) for every named node reachable from the observers, sorted by name.

- [ ] **Step 1: Write the failing test**

Add to `test/test_graph.ml`, immediately above `let test_position_change_is_local ()`:

```ocaml
(* ------------------------------------------------------------------------ *)
(* 2b. The label table                                                       *)
(* ------------------------------------------------------------------------ *)

(* Every node the hook can name is also named ON the node, and the two lists
   are the same list.

   This is the first half of "the drawing is the graph, taken from the graph".
   graph.ml has called [note Node_name.x] inside every node body since Phase 1;
   what is new is that the same string now sits in Incremental's own node
   record, where For_analyzer can read it back beside the node's children. If
   a site is named in the body and not on the node, the page draws an edge into
   a node it cannot label; if it is named on the node and not in the body, the
   page lights a node the tests never counted. Both are caught here, by name.

   Hand-counted on the standard book -- three names, two sectors, seven limits,
   no options:

     inputs    cash equity_history factor_returns valuation_days now       5
               price[S] qty[S] returns[S] last_tick[S]  x 3                12
     derived   exposure:S feed:S  x 3                                       6
               sector:K  x 2                                                2
               limit:name  x 7                                              7
               singletons                                                  29
                                                                           --
                                                                           61

   [rate] is NOT in the list, and that is the traverse being honest rather
   than a bug: on a book with no options nothing reads the rate cell, so it is
   reachable from no observer and is not part of this graph. It joins the
   moment a contract does. *)
let expected_labels =
  [
    "aligned_returns"; "attribution"; "breaches"; "cash"; "component_var_map";
    "component_var_sector_map"; "covariance"; "covariance_ewma"; "current_drawdown";
    "diversification_ratio"; "equity"; "equity_history"; "es_notional"; "expected_shortfall";
    "exposure:AAPL"; "exposure:MSFT"; "exposure:XOM"; "exposure_map"; "factor_returns";
    "feed:AAPL"; "feed:MSFT"; "feed:XOM"; "feed_health"; "gamma_map"; "gross_exposure";
    "historical_var"; "last_tick[AAPL]"; "last_tick[MSFT]"; "last_tick[XOM]";
    "limit:aapl-cap"; "limit:book-cap"; "limit:dd-cap"; "limit:energy-cap";
    "limit:msft-cap"; "limit:tech-cap"; "limit:var-cap"; "net_exposure"; "now";
    "parametric_var"; "parametric_var_ewma"; "portfolio_beta"; "portfolio_gamma";
    "portfolio_returns"; "portfolio_vega"; "price[AAPL]"; "price[MSFT]"; "price[XOM]";
    "qty[AAPL]"; "qty[MSFT]"; "qty[XOM]"; "returns[AAPL]"; "returns[MSFT]"; "returns[XOM]";
    "sector:ENERGY"; "sector:TECH"; "sector_map"; "valuation_days"; "var_notional";
    "vega_by_bucket"; "vega_map"; "weights";
  ]

(* The 27 observers of Phase 1-5 plus Task 2's attribution observer. Every
   published value and nothing else. *)
let expected_observed =
  [
    "attribution"; "breaches"; "component_var_map"; "component_var_sector_map";
    "covariance"; "covariance_ewma"; "current_drawdown"; "diversification_ratio";
    "equity"; "es_notional"; "expected_shortfall"; "exposure_map"; "feed_health";
    "gamma_map"; "gross_exposure"; "historical_var"; "net_exposure"; "parametric_var";
    "parametric_var_ewma"; "portfolio_beta"; "portfolio_gamma"; "portfolio_returns";
    "portfolio_vega"; "sector_map"; "var_notional"; "vega_by_bucket"; "vega_map"; "weights";
  ]

let test_every_node_is_labelled () =
  with_graph
    ~f:(fun graph _ ->
      let labels = Graph.labelled_nodes graph in
      Alcotest.(check int) "sixty-one named nodes on the standard book" 61 (List.length labels);
      Alcotest.(check (list string))
        "the names, and no others" expected_labels (List.map labels ~f:fst);
      Alcotest.(check (list string))
        "the twenty-eight observed" expected_observed
        (List.filter_map labels ~f:(fun (name, observed) ->
             if observed then Some name else None));
      (* The marker is a label, never a name. *)
      Alcotest.(check bool)
        "the observed marker is not itself a node" false
        (List.exists labels ~f:(fun (name, _) -> String.equal name Graph.observed_marker));
      (* Every derived name has a unit, which Task 1 made a total function
         over this graph's vocabulary; a name that raises here is a node
         someone added without deciding what it is measured in. *)
      List.iter labels ~f:(fun (name, _) -> ignore (Graph.Node_name.unit_of name : string)))
    ()

(* Reading the table is free. Labels are metadata on nodes that already
   exist; walking them must not stabilize, must not observe and must not run a
   single node body. The recorder is the witness. *)
let test_reading_labels_costs_nothing () =
  with_graph
    ~f:(fun graph recorder ->
      let before = Graph.total_nodes_recomputed () in
      ignore (Graph.labelled_nodes graph : (string * bool) list);
      ignore (Graph.walk graph : Graph.Raw.t list);
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"no node body ran" ~expected:[];
      Alcotest.(check int)
        "Incremental's own counter did not move either" before
        (Graph.total_nodes_recomputed ()))
    ()
```

and register them in `suite`, immediately after Task 1's `"scalar units are the ones with a number"` case:

```ocaml
      Alcotest.test_case "every node the hook names is named on the node" `Quick
        test_every_node_is_labelled;
      Alcotest.test_case "reading the label table runs nothing" `Quick
        test_reading_labels_costs_nothing;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -20
```

Expected: `Error: Unbound value Graph.labelled_nodes`.

- [ ] **Step 3: Minimal implementation — the helpers**

**(a)** In `lib/graph.ml`, immediately after `module Node_name = struct … end` (after Task 1's `is_scalar`, before `module Covariance_estimator`), add:

```ocaml
(* The second label an observed node carries, beside its name.

   Not a node name and never returned as one: it rides in the same label set
   [append_user_info_graphviz] unions, so the traverse in [walk] can tell an
   observed node from an interior one without a second table saying which
   twenty-eight names have observers -- a table that would be wrong the day a
   twenty-ninth is added and nobody updates it. *)
let observed_marker = "@observed"
```

**(b)** Replace `make_var` (`let make_var ~equal init =` through its `v\n  in`) with:

```ocaml
  let make_var ~name ~equal init =
    let v = Inc.Var.create init in
    let watch = Inc.Var.watch v in
    Inc.set_cutoff watch (Inc.Cutoff.of_equal equal);
    (* The cell's name goes on its WATCH node, which is the node the graph
       reads and For_analyzer reports as a child; the Var itself is not a
       node. Var.watch returns the same node every time, so this is done once
       here rather than at every read site. *)
    Inc.append_user_info_graphviz watch ~label:[ name ] ~attrs:String.Map.empty;
    v
  in
  (* Name a derived node on the node. The same string the body passes to
     [note], put where Incremental's own table can return it. Returns the node
     so it wraps a construction site without moving anything. *)
  let named (name : string) (node : 'a Inc.t) : 'a Inc.t =
    Inc.append_user_info_graphviz node ~label:[ name ] ~attrs:String.Map.empty;
    node
  in
```

**(c)** In Task 2's `observe_silent`, replace

```ocaml
  let observe_silent node =
    let o = Inc.observe node in
```

with

```ocaml
  let observe_silent node =
    Inc.append_user_info_graphviz node ~label:[ observed_marker ] ~attrs:String.Map.empty;
    let o = Inc.observe node in
```

**(d)** The twelve input cells. Each `make_var ~equal:…` call gains `~name:`:

| site (the current text) | becomes |
|---|---|
| `(s, make_var ~equal:Price.equal (Price.of_float 0.0))` | `(s, make_var ~name:(Node_name.Input.price s) ~equal:Price.equal (Price.of_float 0.0))` |
| `(s, make_var ~equal:Qty.equal Qty.zero)` | `(s, make_var ~name:(Node_name.Input.qty s) ~equal:Qty.equal Qty.zero)` |
| `(s, make_var ~equal:(Array.equal Float.equal) [||])` | `(s, make_var ~name:(Node_name.Input.returns s) ~equal:(Array.equal Float.equal) [||])` |
| `let cash_var = make_var ~equal:Notional.equal starting_cash in` | `let cash_var = make_var ~name:Node_name.Input.cash ~equal:Notional.equal starting_cash in` |
| `let equity_history_var = make_var ~equal:(Array.equal Float.equal) [||] in` | `let equity_history_var = make_var ~name:Node_name.Input.equity_history ~equal:(Array.equal Float.equal) [||] in` |
| `let factor_returns_var = make_var ~equal:(Array.equal Float.equal) [||] in` | `let factor_returns_var = make_var ~name:Node_name.Input.factor_returns ~equal:(Array.equal Float.equal) [||] in` |
| `(s, make_var ~equal:(Option.equal Time.equal) None)` | `(s, make_var ~name:(Node_name.Input.last_tick s) ~equal:(Option.equal Time.equal) None)` |
| `let now_var = make_var ~equal:Time.equal Time.epoch in` | `let now_var = make_var ~name:Node_name.Input.now ~equal:Time.equal Time.epoch in` |
| `Map.map option_positions ~f:(fun _ ->\n        make_var ~equal:Options.Contracts.equal (Options.Contracts.of_float 0.0))` | `Map.mapi option_positions ~f:(fun ~key:id ~data:_ ->\n        make_var ~name:(Node_name.Input.contracts id) ~equal:Options.Contracts.equal (Options.Contracts.of_float 0.0))` |
| `Map.map option_positions ~f:(fun _ ->\n        make_var ~equal:Options.Implied_vol.equal (Options.Implied_vol.of_float 0.0))` | `Map.mapi option_positions ~f:(fun ~key:id ~data:_ ->\n        make_var ~name:(Node_name.Input.implied_vol id) ~equal:Options.Implied_vol.equal (Options.Implied_vol.of_float 0.0))` |
| `let rate_var = make_var ~equal:Float.equal rate in` | `let rate_var = make_var ~name:Node_name.Input.rate ~equal:Float.equal rate in` |
| `let valuation_days_var = make_var ~equal:Float.equal 0.0 in` | `let valuation_days_var = make_var ~name:Node_name.Input.valuation_days ~equal:Float.equal 0.0 in` |

**(e)** The derived nodes. Each construction site is wrapped in `named <the same name the body notes> ( … )`. The wrapper goes around the OUTERMOST constructor — outside `cutoff` where there is one — so the node that carries the cutoff is the node that carries the name. The first two are shown in full; the rest follow the identical pattern and are listed by their opening line.

`exposure_nodes`, currently

```ocaml
        let option_legs = Inc.all (option_nodes_for symbol) in
        cutoff ~equal:Notional.equal
          (Inc.map2 shares option_legs ~f:(fun shares legs ->
               note (Node_name.exposure symbol);
               List.fold legs ~init:shares ~f:(fun acc (delta_equivalent, _, _) ->
                   Notional.add acc delta_equivalent))))
```

becomes

```ocaml
        let option_legs = Inc.all (option_nodes_for symbol) in
        named (Node_name.exposure symbol)
          (cutoff ~equal:Notional.equal
             (Inc.map2 shares option_legs ~f:(fun shares legs ->
                  note (Node_name.exposure symbol);
                  List.fold legs ~init:shares ~f:(fun acc (delta_equivalent, _, _) ->
                      Notional.add acc delta_equivalent)))))
```

`gross_node`, currently

```ocaml
  let gross_node =
    cutoff ~equal:Notional.equal
      (Inc.map exposure_list ~f:(fun xs ->
           note Node_name.gross;
           Notional.sum (List.map xs ~f:Notional.abs)))
  in
```

becomes

```ocaml
  let gross_node =
    named Node_name.gross
      (cutoff ~equal:Notional.equal
         (Inc.map exposure_list ~f:(fun xs ->
              note Node_name.gross;
              Notional.sum (List.map xs ~f:Notional.abs))))
  in
```

The remaining sites, each wrapped the same way — the opening line to find, and the name to wrap it in:

| construction site (opening line) | wrap in |
|---|---|
| `greeks_nodes`: `Inc.map4 price vol (Inc.Var.watch valuation_days_var) (Inc.Var.watch rate_var)` | `named (Node_name.greeks id) (…)` |
| `option_exposure_nodes`: `Inc.map3 greeks contracts price ~f:(fun greeks contracts price ->` | `named (Node_name.option_exposure id) (…)` |
| `exposure_map_node`: `Inc.map exposure_list ~f:(fun xs ->` | `named Node_name.exposure_map (…)` |
| `greek_by_instrument select name`: the outer `Inc.map\n      (Inc.all\n         (List.map symbols …` | `named name (…)` |
| `sum_map name node`: `Inc.map node ~f:(fun m ->` | `named name (…)` |
| `vega_by_bucket_node`: `Inc.map2\n      (Inc.all\n         (List.map (Map.to_alist option_positions) …` | `named Node_name.vega_by_bucket (…)` |
| `sector_nodes`: `cutoff ~equal:Notional.equal\n          (Inc.map (Inc.all member_nodes) ~f:(fun exposures ->` | `named (Node_name.sector sector) (…)` |
| `sector_map_node`: `Inc.map\n      (Inc.all (Map.data sector_nodes))` | `named Node_name.sector_map (…)` |
| `net_node`: `cutoff ~equal:Notional.equal\n      (Inc.map exposure_list ~f:(fun xs ->\n           note Node_name.net;` | `named Node_name.net (…)` |
| `weights_node`: `cutoff ~equal:(Array.equal Float.equal)\n      (Inc.map2 exposure_list gross_node` | `named Node_name.weights (…)` |
| `aligned_returns_node`: `Inc.map\n      (Inc.all (List.map (Map.data returns_vars) ~f:Inc.Var.watch))` | `named Node_name.aligned_returns (…)` |
| `covariance_node`: `Inc.map aligned_returns_node ~f:(fun series ->\n        note Node_name.covariance;` | `named Node_name.covariance (…)` |
| `covariance_ewma_node`: `Inc.map aligned_returns_node ~f:(fun series ->\n        note Node_name.covariance_ewma;` | `named Node_name.covariance_ewma (…)` |
| `portfolio_returns_node`: `Inc.map2 aligned_returns_node weights_node ~f:(fun series weights ->` | `named Node_name.portfolio_returns (…)` |
| `historical_var_node`: `Inc.map portfolio_returns_node ~f:(fun returns ->\n        note Node_name.historical_var;` | `named Node_name.historical_var (…)` |
| `expected_shortfall_node`: `Inc.map portfolio_returns_node ~f:(fun returns ->\n        note Node_name.expected_shortfall;` | `named Node_name.expected_shortfall (…)` |
| `parametric_var_node`: `Inc.map2 weights_node covariance_node ~f:(fun weights covariance ->` | `named Node_name.parametric_var (…)` |
| `parametric_var_ewma_node`: `Inc.map2 weights_node covariance_ewma_node ~f:(fun weights covariance ->` | `named Node_name.parametric_var_ewma (…)` |
| `attribution_node`: `Inc.map2 weights_node attribution_covariance_node ~f:(fun weights covariance ->` | `named Node_name.attribution (…)` |
| `component_var_node`: `Inc.map2 attribution_node gross_node ~f:(fun attribution gross ->` | `named Node_name.component_var_map (…)` |
| `component_var_sector_node`: `Inc.map component_var_node ~f:(fun by_instrument ->` | `named Node_name.component_var_sector_map (…)` |
| `diversification_ratio_node`: `Inc.map attribution_node ~f:(fun attribution ->\n        note Node_name.diversification_ratio;` | `named Node_name.diversification_ratio (…)` |
| `portfolio_beta_node`: `Inc.map2 portfolio_returns_node (Inc.Var.watch factor_returns_var)` | `named Node_name.portfolio_beta (…)` |
| `to_notional name fraction_node`: `Inc.map2 fraction_node gross_node ~f:(fun fraction gross ->` | `named name (…)` |
| `equity_node`: `cutoff ~equal:Notional.equal\n      (Inc.map2 (Inc.Var.watch cash_var) net_node` | `named Node_name.equity (…)` |
| `drawdown_node`: `cutoff ~equal:Float.equal\n      (Inc.map2 (Inc.Var.watch equity_history_var) equity_node` | `named Node_name.drawdown (…)` |
| `breaches_node`: `Inc.map (Inc.all breach_nodes) ~f:(fun results ->` | `named Node_name.breaches (…)` |
| `feed_nodes`: `cutoff ~equal:Feed_health.Symbol_state.equal\n          (Inc.map2 (Inc.Var.watch last_tick_var) (Inc.Var.watch now_var)` | `named (Node_name.feed symbol) (…)` |
| `feed_health_node`: `Inc.map\n      (Inc.all (Map.data feed_nodes))` | `named Node_name.feed_health (…)` |

`breach_node` is the one site with a `match`: the whole `match (Limit.kind limit, Limit.scope limit) with … ` expression is the node, so it is wrapped as a unit. Replace

```ocaml
    match (Limit.kind limit, Limit.scope limit) with
    | Limit.Gross_notional _, Limit.Instrument symbol ->
```

with

```ocaml
    named name
      (match (Limit.kind limit, Limit.scope limit) with
      | Limit.Gross_notional _, Limit.Instrument symbol ->
```

and close the extra parenthesis after the final arm's `(Limit.name limit) ()` — the line becomes `(Limit.name limit) ())`. `make fmt` re-indents the arms.

`greek_by_instrument`, `sum_map` and `to_notional` each take the name as a parameter and note it in the body, so `named name` inside them names every node they build; nothing at their call sites changes.

**(f)** The read-back. Immediately after `let destroy (t : t) : unit = …` (the two-line function ending `List.iter t.releases ~f:(fun release -> release ())`), add:

```ocaml
(* -------------------------------------------------------------------------
   The graph, read back out of Incremental
   ------------------------------------------------------------------------- *)

(* One packed root per observer -- THIS graph's, not the state's.

   For_analyzer.directly_observed would hand back every observer in the
   process, which includes a stress fork alive in a request handler and the
   startup probe's abandoned observers until their next stabilize. Starting
   from the observers held in [t] is what makes the walk a walk of this book:
   a fork's nodes are reachable from a fork's observers and from nothing
   here. Twenty-eight roots, differently typed, so they are packed one by one;
   [destroy] releases the same twenty-eight, and the two lists must be kept
   together. *)
let observed_roots (t : t) : Inc.Packed.t list =
  let root o = Inc.pack (Inc.Observer.observing o) in
  [
    root t.obs_exposure_by_instrument;
    root t.obs_exposure_by_sector;
    root t.obs_gross;
    root t.obs_net;
    root t.obs_weights;
    root t.obs_gamma_by_instrument;
    root t.obs_vega_by_instrument;
    root t.obs_portfolio_gamma;
    root t.obs_portfolio_vega;
    root t.obs_vega_by_bucket;
    root t.obs_portfolio_returns;
    root t.obs_covariance;
    root t.obs_covariance_ewma;
    root t.obs_historical_var;
    root t.obs_expected_shortfall;
    root t.obs_parametric_var;
    root t.obs_parametric_var_ewma;
    root t.obs_component_var_by_instrument;
    root t.obs_component_var_by_sector;
    root t.obs_diversification_ratio;
    root t.obs_attribution;
    root t.obs_var_notional;
    root t.obs_es_notional;
    root t.obs_equity;
    root t.obs_drawdown;
    root t.obs_breaches;
    root t.obs_portfolio_beta;
    root t.obs_feed_health;
  ]

(* A node as Incremental reports it, before any contraction. [children] are
   the nodes this one READS -- For_analyzer's word for an input -- by
   Incremental's own id. [name] is the label [named] or [make_var] put on it,
   None for the plumbing this module never named (an [Inc.all] fold, an
   intermediate [map2]). *)
module Raw = struct
  type t = {
    id : int;
    kind : string;
    cutoff : string;
    children : int list;
    name : string option;
    observed : bool;
  }
  [@@deriving sexp_of, fields ~getters]
end

(* The label set, read out of the dot form. A node carries at most one name
   and possibly the observed marker; two names on one node is a construction
   bug and is loud rather than resolved by picking the first. *)
let name_of_user_info (info : Incremental.For_analyzer.Dot_user_info.t option) :
    string option * bool =
  match info with
  | None -> (None, false)
  | Some info ->
      let dot = Incremental.For_analyzer.Dot_user_info.to_dot info in
      let labels = Set.to_list dot.Incremental.For_analyzer.Dot_user_info.label in
      let observed =
        List.exists labels ~f:(fun l -> List.equal String.equal l [ observed_marker ])
      in
      let names =
        List.filter_map labels ~f:(function
          | [ name ] when not (String.equal name observed_marker) -> Some name
          | _ -> None)
      in
      (match names with
      | [] -> (None, observed)
      | [ name ] -> (Some name, observed)
      | many ->
          failwithf "graph: a node carries %d names (%s)" (List.length many)
            (String.concat ~sep:", " many) ())

(* Every node reachable from this graph's observers, once each, exactly as
   Incremental holds it. Costs nothing on the tick path: it reads node records
   and never stabilizes, observes or evaluates. test_graph.ml asserts that
   with the recorder. *)
let walk (t : t) : Raw.t list =
  let acc = ref [] in
  Incremental.For_analyzer.traverse (observed_roots t)
    ~add_node:(fun
        ~id ~kind ~cutoff ~children ~bind_children:_ ~user_info ~recomputed_at:_
        ~changed_at:_ ~height:_ ->
      let name, observed = name_of_user_info user_info in
      acc :=
        {
          Raw.id = Incremental.For_analyzer.Node_id.to_int id;
          kind = Incremental.For_analyzer.Kind.to_string kind;
          cutoff = Incremental.For_analyzer.Cutoff.to_string cutoff;
          children = List.map children ~f:Incremental.For_analyzer.Node_id.to_int;
          name;
          observed;
        }
        :: !acc);
  List.rev !acc

(* The named nodes and whether each is observed, by name. The flat form of
   the table; [topology] below it is the contracted one. *)
let labelled_nodes (t : t) : (string * bool) list =
  walk t
  |> List.filter_map ~f:(fun r ->
         Option.map r.Raw.name ~f:(fun name -> (name, r.Raw.observed)))
  |> List.sort ~compare:(fun (a, _) (b, _) -> String.compare a b)
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -15
grep -c "named " lib/graph.ml
```

Expected: green. The `grep -c` prints at least 31 — the helper's definition plus thirty wrapped sites; a lower count means a site was missed, and the failing assertion names it (`the names, and no others` prints the two lists side by side). The pre-existing architecture tests pass with their sets unchanged: a label is metadata and no body ran differently.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/graph.ml test/test_graph.ml
git commit -m "graph: name every node on the node, because a drawing taken from Incremental's table can only label what the table knows"
```

---

### Task 4: `Graph.topology` — the contracted graph, ranked, with the first three closure tests

Task 3's `walk` returns every node Incremental holds, plumbing included. This task contracts it: every named node's inputs are its nearest named ancestors through whatever unnamed nodes sit between (an `Inc.all` fold, the `map2` that multiplies price by quantity, the `map` that selects one symbol out of a Greek map), so every edge runs named-to-named; ranks are the longest path from an input cell; families come from the shape of the name. The tests are the spec's: the drawing's downstream closure of `{price[AAPL], last_tick[AAPL]}` equals the recomputation set `test_graph.ml` already pins for a tick, `returns[MSFT]`'s equals the pinned push_return set, and no price cell reaches either covariance matrix.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/graph.ml` — a new `Topology` module and `topology` function immediately after Task 3's `labelled_nodes`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml` — one named list lifted out of `test_return_push_is_local`, three new cases
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`

**Interfaces:**
- Consumes: `Graph.Raw.t` and `Graph.walk` (Task 3); `Graph.Node_name.unit_of`, `Node_name.Input.*` (Task 1); `Graph.observed_marker` (Task 3); `t.instruments : Instrument.t Symbol.Map.t`, `t.limits : Limit.t list`, `t.option_positions : Options.Position.t String.Map.t`, `t.covariance_for_attribution` (existing fields of `type t`); `Types.Limit.{name, kind, scope}` getters, `Types.Limit.scope_to_string` (`lib/types.ml:289`), `Types.Greek.to_string` (`lib/types.ml:226`); `Options.Position.underlying`; `String.chop_prefix`/`chop_suffix` (Base); `[%compare: string * string]` (ppx_jane).
- Produces:
  - `Graph.Topology.Family.t = Input | Per_symbol | Per_sector | Per_limit | Per_option | Singleton` with `to_string` giving `input | per_symbol | per_sector | per_limit | per_option | singleton`.
  - `Graph.Topology.Node.t = { name : string; family : Family.t; kind : string; cutoff : string; rank : int; observed : bool; unit : string; symbol : Symbol.t option; sector : Sector.t option; limit : Limit.t option; contract : (string * Symbol.t) option }` with getters. `symbol` is set on `price[S]`-style cells, on `exposure:S`/`feed:S`, and — as the UNDERLYING — on every per-option node and option input cell, so "rows by symbol" is one field on the client. `contract` is `(id, underlying)` and goes on the wire as `option`.
  - `Graph.Topology.Outside.t = { name : string; reads : string list; present : bool; wired_to : string option }`.
  - `Graph.Topology.Counts.t = { instruments; sectors; limits; options; named; inputs; observed; incremental_nodes : int }` — `named` counts non-input nodes (the spec's 53 on the demo book), `inputs` the cells.
  - `Graph.Topology.t = { nodes : Node.t list; edges : (string * string) list; outside : Outside.t list; attribution_covariance : Covariance_estimator.t; counts : Counts.t }`, nodes sorted by `(rank, name)`, edges `(from, into)` sorted, both deduplicated.
  - `Graph.Topology.{names; find; inputs_of; outputs_of} ` and `Graph.Topology.closure : t -> string list -> [ \`Down | \`Up ] -> String.Set.t` — every node strictly reachable from the seeds along the edges; the seeds themselves are NOT in the set unless another seed reaches them.
  - `Graph.Topology.limit_kind_to_string : Limit.kind -> string` = `gross_notional | value_at_risk | max_drawdown | greek:gamma | greek:vega | component_var`.
  - `Graph.topology : ?alerts:bool -> t -> Topology.t` (`alerts` defaults to `false`; it is the only fact about the outside the graph cannot know, and `Server.create` passes `Option.is_some alerts`).

- [ ] **Step 1: Write the failing test**

First, lift the pinned push_return set into a name. In `test/test_graph.ml`, immediately after `let downstream_of_aapl_tick = …` (line 372), add:

```ocaml
(* The pinned set for one new return observation, lifted out of
   [test_return_push_is_local] so the topology test below can assert the
   DRAWING's closure of [returns[MSFT]] is this exact list. One copy, two
   readers: the recorder pins what ran, the topology pins what is wired, and
   the two must be the same list or one of them is lying. *)
let downstream_of_a_return =
  [
    "aligned_returns";
    "covariance";
    (* A return genuinely reaches BOTH matrices, and this is where the second
       estimator's cost actually lands: one new observation now rebuilds two
       n x n matrices instead of one. That is the honest trade and it belongs
       in an assertion rather than a comment -- the return window moves once a
       day, and the tick path, which moves thousands of times a day, is
       untouched. *)
    "covariance_ewma";
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
    "limit:var-cap";
    "breaches";
  ]
```

and in `test_return_push_is_local` replace the literal list — from `~expected:\n          [\n            "aligned_returns";` through `"breaches";\n          ]` (lines 456–482 of the 7e4a265 tree, comment included) — with `~expected:downstream_of_a_return`. The comment moved with the list; nothing else in that test changes.

Then add, immediately after Task 3's `test_reading_labels_costs_nothing`:

```ocaml
(* ------------------------------------------------------------------------ *)
(* 2c. The topology: the drawing is the graph, taken from the graph          *)
(* ------------------------------------------------------------------------ *)

let sorted xs = List.sort xs ~compare:String.compare
let set_to_list s = Set.to_list s

(* The three edges graph.ml's header says are worth staring at, as
   assertions on the CONTRACTED graph rather than on the recorder.

   The recorder pins what ran on a tick. The topology says what is wired. If
   the two agree, the drawing the page makes from the topology is a drawing
   of the thing the tests measure, and lighting the recorder's set on it lights
   exactly the nodes that are downstream of the cell that moved. If they
   disagree, either an edge exists the hook never saw run (a dependency with
   a cutoff nobody meant), or a node ran that no edge explains (impossible in
   Incremental, and worth being told about). *)
let test_topology_closures_match_the_pinned_sets () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let down names = sorted (set_to_list (Graph.Topology.closure topo names `Down)) in
      Alcotest.(check (list string))
        "downstream of {price[AAPL], last_tick[AAPL]} is the pinned tick set"
        (sorted downstream_of_aapl_tick)
        (down [ Graph.Node_name.Input.price aapl; Graph.Node_name.Input.last_tick aapl ]);
      Alcotest.(check (list string))
        "downstream of returns[MSFT] is the pinned push_return set"
        (sorted downstream_of_a_return)
        (down [ Graph.Node_name.Input.returns msft ]);
      (* The expensive half is upstream of no price. This is the
         [test_prices_never_reach_covariance] fact, stated structurally. *)
      List.iter [ aapl; msft; xom ] ~f:(fun s ->
          let reach = Graph.Topology.closure topo [ Graph.Node_name.Input.price s ] `Down in
          List.iter [ "aligned_returns"; "covariance"; "covariance_ewma" ] ~f:(fun m ->
              Alcotest.(check bool)
                (Printf.sprintf "%s is not downstream of price[%s]" m (Symbol.to_string s))
                false (Set.mem reach m))))
    ()

(* Every edge joins two named nodes, and the node list is the label table. The
   contraction must invent nothing and lose nothing: an edge whose endpoint is
   not a node would be a dangling stroke on the page, and a labelled node the
   topology dropped would be a name the frame can light and the drawing cannot
   find. *)
let test_topology_is_closed_over_its_names () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let names = String.Set.of_list (Graph.Topology.names topo) in
      Alcotest.(check (list string))
        "the topology's nodes are the label table" (List.map (Graph.labelled_nodes graph) ~f:fst)
        (sorted (Set.to_list names));
      List.iter (Graph.Topology.edges topo) ~f:(fun (from, into) ->
          Alcotest.(check bool) (from ^ " -> " ^ into ^ ": both ends are nodes") true
            (Set.mem names from && Set.mem names into));
      Alcotest.(check bool)
        "there is at least one edge per non-input node"
        true
        (List.length (Graph.Topology.edges topo)
        >= Graph.Topology.Counts.named (Graph.Topology.counts topo));
      (* The one edge the whole design is about: the decomposition reads the
         equal-weighted matrix, and which one is on the wire beside it. *)
      Alcotest.(check (list string))
        "attribution reads weights and covariance, nothing else"
        [ "covariance"; "weights" ]
        (sorted (Graph.Topology.inputs_of topo "attribution"));
      Alcotest.(check string) "and the topology says which matrix" "equal_weighted"
        (Graph.Covariance_estimator.to_string (Graph.Topology.attribution_covariance topo)))
    ()

(* Families from the shape of the name, and the typed fields beside them. A
   per-symbol node carries its symbol as a Symbol.t the graph recognises, a
   limit node carries the Limit.t it evaluates, so the page can put the
   instrument-scoped cap on the instrument's row without parsing a string. *)
let test_topology_families () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let node name =
        Option.value_exn (Graph.Topology.find topo name) ~message:("no node " ^ name)
      in
      let family name = Graph.Topology.Family.to_string (Graph.Topology.Node.family (node name)) in
      Alcotest.(check string) "price[AAPL] is an input" "input" (family "price[AAPL]");
      Alcotest.(check string) "cash is an input" "input" (family "cash");
      Alcotest.(check string) "exposure:AAPL is per-symbol" "per_symbol"
        (family "exposure:AAPL");
      Alcotest.(check string) "feed:XOM is per-symbol" "per_symbol" (family "feed:XOM");
      Alcotest.(check string) "sector:TECH is per-sector" "per_sector" (family "sector:TECH");
      Alcotest.(check string) "limit:dd-cap is per-limit" "per_limit" (family "limit:dd-cap");
      Alcotest.(check string) "covariance is a singleton" "singleton" (family "covariance");
      Alcotest.(check (option string))
        "price[AAPL] knows its symbol" (Some "AAPL")
        (Option.map (Graph.Topology.Node.symbol (node "price[AAPL]")) ~f:Symbol.to_string);
      Alcotest.(check (option string))
        "sector:ENERGY knows its sector" (Some "ENERGY")
        (Option.map (Graph.Topology.Node.sector (node "sector:ENERGY")) ~f:Sector.to_string);
      (match Graph.Topology.Node.limit (node "limit:aapl-cap") with
      | Some l ->
          Alcotest.(check string) "limit:aapl-cap carries its limit" "aapl-cap" (Limit.name l);
          Alcotest.(check string) "and its kind" "gross_notional"
            (Graph.Topology.limit_kind_to_string (Limit.kind l))
      | None -> Alcotest.fail "limit:aapl-cap has no limit");
      Alcotest.(check string) "an input cell is a Var" "Var"
        (Graph.Topology.Node.kind (node "qty[XOM]"));
      Alcotest.(check string) "and carries the value-equality cutoff" "Equal"
        (Graph.Topology.Node.cutoff (node "qty[XOM]"));
      Alcotest.(check int) "a cell has rank 0" 0 (Graph.Topology.Node.rank (node "qty[XOM]"));
      Alcotest.(check int) "exposure:XOM is one step in" 1
        (Graph.Topology.Node.rank (node "exposure:XOM"));
      Alcotest.(check string) "every node has the unit Task 1 gave it" "usd"
        (Graph.Topology.Node.unit (node "exposure:XOM")))
    ()
```

and register the three in `suite`, immediately after Task 3's `"reading the label table runs nothing"` case:

```ocaml
      Alcotest.test_case "TOPOLOGY: closures equal the pinned recomputation sets" `Quick
        test_topology_closures_match_the_pinned_sets;
      Alcotest.test_case "TOPOLOGY: edges join named nodes and nothing is lost" `Quick
        test_topology_is_closed_over_its_names;
      Alcotest.test_case "TOPOLOGY: families and the typed fields beside them" `Quick
        test_topology_families;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -20
```

Expected: `Error: Unbound value Graph.topology` (the `Topology` module does not exist yet either; the first error dune reports is the one on the first use).

- [ ] **Step 3: Minimal implementation**

In `lib/graph.ml`, immediately after Task 3's `let labelled_nodes …` (before the `Inputs` banner), add:

```ocaml
(* -------------------------------------------------------------------------
   The topology: named nodes, named-to-named edges, ranks
   ------------------------------------------------------------------------- *)

module Topology = struct
  (* Six families, from the SHAPE of the name and nothing else. Brackets are a
     cell (Task 1 chose that on purpose), a colon prefix is a per-something
     node, and the rest are the singleton spine. Classifying from the name
     rather than from a second table means a node added tomorrow is drawn in
     the right column the day it is named. *)
  module Family = struct
    type t = Input | Per_symbol | Per_sector | Per_limit | Per_option | Singleton
    [@@deriving sexp_of, compare, equal]

    let to_string = function
      | Input -> "input"
      | Per_symbol -> "per_symbol"
      | Per_sector -> "per_sector"
      | Per_limit -> "per_limit"
      | Per_option -> "per_option"
      | Singleton -> "singleton"
  end

  module Node = struct
    type t = {
      name : string;
      family : Family.t;
      kind : string;
      cutoff : string;
      (* Longest path from an input cell. A cell is 0; every other node is one
         more than the deepest of its inputs. Longest rather than shortest so
         that an edge never runs backwards on the page: a node's inputs are all
         strictly to its left, whatever route they took to get there. *)
      rank : int;
      observed : bool;
      unit : string;
      (* The instrument this node is about, when it is about one -- and for
         an option node, its UNDERLYING, so the page has one field to put a
         row on. *)
      symbol : Symbol.t option;
      sector : Sector.t option;
      limit : Limit.t option;
      (* (id, underlying). On the wire as [option]. *)
      contract : (string * Symbol.t) option;
    }
    [@@deriving sexp_of, fields ~getters]
  end

  (* What reads the graph from outside it. Not nodes -- they have no children
     Incremental knows about -- but the page draws them, and it draws them from
     this list rather than inventing them. *)
  module Outside = struct
    type t = { name : string; reads : string list; present : bool; wired_to : string option }
    [@@deriving sexp_of, fields ~getters]
  end

  module Counts = struct
    type t = {
      instruments : int;
      sectors : int;
      limits : int;
      options : int;
      (* Non-input named nodes: the ones with a body the hook can name. *)
      named : int;
      inputs : int;
      observed : int;
      (* Counted inside the traverse, so it is THIS graph's node count and not
         the process's -- State.num_nodes_created is cumulative across every
         probe and fork and reads in the thousands for a sixty-node book. *)
      incremental_nodes : int;
    }
    [@@deriving sexp_of, fields ~getters]
  end

  type t = {
    nodes : Node.t list;
    edges : (string * string) list;
    outside : Outside.t list;
    attribution_covariance : Covariance_estimator.t;
    counts : Counts.t;
  }
  [@@deriving sexp_of, fields ~getters]

  let limit_kind_to_string : Limit.kind -> string = function
    | Limit.Gross_notional _ -> "gross_notional"
    | Limit.Value_at_risk _ -> "value_at_risk"
    | Limit.Max_drawdown _ -> "max_drawdown"
    | Limit.Greek_limit (greek, _) -> "greek:" ^ Greek.to_string greek
    | Limit.Component_var _ -> "component_var"

  let names (t : t) : string list = List.map t.nodes ~f:Node.name
  let find (t : t) (name : string) : Node.t option =
    List.find t.nodes ~f:(fun n -> String.equal n.Node.name name)

  let inputs_of (t : t) (name : string) : string list =
    List.filter_map t.edges ~f:(fun (from, into) ->
        if String.equal into name then Some from else None)

  let outputs_of (t : t) (name : string) : string list =
    List.filter_map t.edges ~f:(fun (from, into) ->
        if String.equal from name then Some into else None)

  (* Everything strictly reachable from the seeds. The seeds are not in the
     result unless another seed reaches them, which is what lets the test
     compare it against a recorder set that never contains the cell that was
     written. Lists rather than an adjacency table: sixty nodes, called from
     tests and once per /api/graph, never on the tick path. *)
  let closure (t : t) (seeds : string list) (direction : [ `Down | `Up ]) : String.Set.t =
    let step = match direction with `Down -> outputs_of t | `Up -> inputs_of t in
    let rec go seen = function
      | [] -> seen
      | name :: rest ->
          let fresh = List.filter (step name) ~f:(fun m -> not (Set.mem seen m)) in
          go (Set.union seen (String.Set.of_list fresh)) (fresh @ rest)
    in
    go String.Set.empty seeds
end

(* The graph, contracted to its names.

   [walk] hands back every node Incremental holds, plumbing included: the
   [Inc.all] that gathers exposures, the [map2] that multiplies a price by a
   quantity, the [map] that picks one symbol out of a Greek map. None of those
   has a name, and none should -- they are how a named node is built, not what
   it is. So each named node's INPUTS are its nearest named ancestors, found
   by walking through whatever unnamed nodes sit between; every edge then runs
   named-to-named and the drawing has one stroke per dependency a reader can
   name. Nothing here is hand-written that could drift from the wiring. *)
let topology ?(alerts = false) (t : t) : Topology.t =
  let raw = walk t in
  let by_id = Int.Table.of_alist_exn (List.map raw ~f:(fun r -> (r.Raw.id, r))) in
  let node_exn id =
    match Hashtbl.find by_id id with
    | Some r -> r
    | None -> failwithf "graph: node %d is read by a visited node and was not visited" id ()
  in
  (* Memoised by id: the exposure fold is read by gross, net, weights and the
     map, and walking it four times would be four times the work for the same
     answer. *)
  let memo = Int.Table.create () in
  let rec named_inputs (id : int) : string list =
    match Hashtbl.find memo id with
    | Some xs -> xs
    | None ->
        let xs =
          List.concat_map (node_exn id).Raw.children ~f:(fun child ->
              match (node_exn child).Raw.name with
              | Some name -> [ name ]
              | None -> named_inputs child)
          |> List.dedup_and_sort ~compare:String.compare
        in
        Hashtbl.set memo ~key:id ~data:xs;
        xs
  in
  let named = List.filter raw ~f:(fun r -> Option.is_some r.Raw.name) in
  let inputs_by_name = String.Table.create () in
  List.iter named ~f:(fun r ->
      Hashtbl.set inputs_by_name ~key:(Option.value_exn r.Raw.name)
        ~data:(named_inputs r.Raw.id));
  let edges =
    List.concat_map named ~f:(fun r ->
        let into = Option.value_exn r.Raw.name in
        List.map (Hashtbl.find_exn inputs_by_name into) ~f:(fun from -> (from, into)))
    |> List.dedup_and_sort ~compare:[%compare: string * string]
  in
  let ranks = String.Table.create () in
  let rec rank name =
    match Hashtbl.find ranks name with
    | Some r -> r
    | None ->
        let r =
          match Hashtbl.find_exn inputs_by_name name with
          | [] -> 0
          | inputs -> 1 + List.fold inputs ~init:0 ~f:(fun acc i -> Int.max acc (rank i))
        in
        Hashtbl.set ranks ~key:name ~data:r;
        r
  in
  (* Family and the typed fields, from the name's shape and this graph's own
     maps. A symbol parsed out of a name is checked against [instruments]
     rather than trusted: the name came from this graph, so a miss is a bug in
     [Node_name], and it should be loud here rather than a blank row on a page. *)
  let symbol_exn s =
    let symbol = Symbol.of_string s in
    if Map.mem t.instruments symbol then symbol
    else failwithf "graph: topology names instrument %S, which this book does not hold" s ()
  in
  let contract_exn id =
    match Map.find t.option_positions id with
    | Some o -> (id, Options.Position.underlying o)
    | None -> failwithf "graph: topology names option %S, which this book does not hold" id ()
  in
  let limit_exn name =
    match List.find t.limits ~f:(fun l -> String.equal (Limit.name l) name) with
    | Some l -> l
    | None -> failwithf "graph: topology names limit %S, which is not configured" name ()
  in
  let bracketed prefix name =
    match String.chop_prefix name ~prefix:(prefix ^ "[") with
    | Some rest -> String.chop_suffix rest ~suffix:"]"
    | None -> None
  in
  let classify name =
    let none = (None, None, None, None) in
    match List.find_map [ "price"; "qty"; "returns"; "last_tick" ] ~f:(fun p -> bracketed p name) with
    | Some s -> (Topology.Family.Input, (Some (symbol_exn s), None, None, None))
    | None -> (
        match List.find_map [ "contracts"; "implied_vol" ] ~f:(fun p -> bracketed p name) with
        | Some id ->
            let id, underlying = contract_exn id in
            (Topology.Family.Input, (Some underlying, None, None, Some (id, underlying)))
        | None -> (
            match name with
            | "cash" | "equity_history" | "factor_returns" | "rate" | "valuation_days" | "now" ->
                (Topology.Family.Input, none)
            | _ -> (
                match String.chop_prefix name ~prefix:"exposure:" with
                | Some s -> (Topology.Family.Per_symbol, (Some (symbol_exn s), None, None, None))
                | None -> (
                    match String.chop_prefix name ~prefix:"feed:" with
                    | Some s -> (Topology.Family.Per_symbol, (Some (symbol_exn s), None, None, None))
                    | None -> (
                        match String.chop_prefix name ~prefix:"sector:" with
                        | Some k ->
                            (Topology.Family.Per_sector, (None, Some (Sector.of_string k), None, None))
                        | None -> (
                            match String.chop_prefix name ~prefix:"limit:" with
                            | Some l ->
                                (Topology.Family.Per_limit, (None, None, Some (limit_exn l), None))
                            | None -> (
                                match
                                  List.find_map [ "greeks:"; "option_exposure:" ] ~f:(fun p ->
                                      String.chop_prefix name ~prefix:p)
                                with
                                | Some id ->
                                    let id, underlying = contract_exn id in
                                    ( Topology.Family.Per_option,
                                      (Some underlying, None, None, Some (id, underlying)) )
                                | None -> (Topology.Family.Singleton, none))))))))
  in
  let nodes =
    List.map named ~f:(fun r ->
        let name = Option.value_exn r.Raw.name in
        let family, (symbol, sector, limit, contract) = classify name in
        {
          Topology.Node.name;
          family;
          kind = r.Raw.kind;
          cutoff = r.Raw.cutoff;
          rank = rank name;
          observed = r.Raw.observed;
          unit = Node_name.unit_of name;
          symbol;
          sector;
          limit;
          contract;
        })
    |> List.sort ~compare:(fun a b ->
           match Int.compare a.Topology.Node.rank b.Topology.Node.rank with
           | 0 -> String.compare a.Topology.Node.name b.Topology.Node.name
           | c -> c)
  in
  let observed_names =
    List.filter_map nodes ~f:(fun n ->
        if n.Topology.Node.observed then Some n.Topology.Node.name else None)
  in
  let is_input n = Topology.Family.equal n.Topology.Node.family Topology.Family.Input in
  let sectors =
    Map.data t.instruments
    |> List.map ~f:Instrument.sector
    |> List.dedup_and_sort ~compare:Sector.compare
    |> List.length
  in
  (* The readers outside the graph, as facts rather than drawing instructions.
     history_buffer.ml reads six published values (its Point has six fields);
     the stream reads every observed value; the alerts tracker hangs off
     [breaches] and is present only when the caller attached one; the kill
     switch reads the tracker and is wired to nothing -- [wired_to = None] is
     the invariant on the wire. *)
  let outside =
    [
      { Topology.Outside.name = "alerts"; reads = [ Node_name.breaches ]; present = alerts; wired_to = None };
      {
        Topology.Outside.name = "history";
        reads =
          [
            Node_name.gross; Node_name.net; Node_name.equity; Node_name.drawdown;
            Node_name.var_notional; Node_name.es_notional;
          ];
        present = true;
        wired_to = None;
      };
      { Topology.Outside.name = "stream"; reads = observed_names; present = true; wired_to = None };
      { Topology.Outside.name = "kill_switch"; reads = [ "alerts" ]; present = alerts; wired_to = None };
    ]
  in
  let counts =
    {
      Topology.Counts.instruments = Map.length t.instruments;
      sectors;
      limits = List.length t.limits;
      options = Map.length t.option_positions;
      named = List.count nodes ~f:(fun n -> not (is_input n));
      inputs = List.count nodes ~f:is_input;
      observed = List.length observed_names;
      incremental_nodes = List.length raw;
    }
  in
  { Topology.nodes; edges; outside; attribution_covariance = t.covariance_for_attribution; counts }
```

`Greek` is `Types.Greek` (`lib/types.ml:224`), reachable unqualified because `graph.ml` opens `Types`. `make fmt` will fold the `classify` cascade; its shape is a nest of `match` because `String.chop_prefix` is the only test and a guard chain of `is_prefix` would parse the name twice.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -15
```

Expected: green, including the unchanged `test_return_push_is_local` now reading `downstream_of_a_return`. If `closures equal the pinned recomputation sets` fails with a node MISSING from the closure, a construction site in `create` is not wrapped in `named` (Task 3's table) and the contraction walked past it; if it fails with an EXTRA node, an edge exists that the recorder never sees run — look at that node's cutoff before touching the test.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/graph.ml test/test_graph.ml
git commit -m "graph: the topology, contracted to its names and ranked, because the drawing's closure of a price must be the set the tests pin"
```

---

### Task 5: The rest of the closure tests — the limit's one input, the clock's dead end, the count formula, ranks, the recorder, and a fork's observers

No production code. Task 4's topology is asserted from five more directions, each one a sentence graph.ml's comments already make: an instrument cap hangs off one exposure; nothing in the risk chain is downstream of `now`; the named-node count is a formula in the book's shape; ranks never run backwards; asking for the topology costs nothing and changes no pinned set; and a fork's twenty-eight observers are released by `destroy`, so a stress run cannot leak into the served drawing.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_graph.ml`

**Interfaces:**
- Consumes: `Graph.topology`, `Graph.Topology.{closure, inputs_of, edges, nodes, counts, find}` (Task 4); `Graph.labelled_nodes`, `expected_observed` (Task 3); `Graph.active_observers : unit -> int` (Phase 2 Task 4 — `Inc.State.num_active_observers Inc.State.t`; verified `_opam/lib/incremental/state.ml:1157-1166`: `disallow_future_use` decrements the counter immediately, so no stabilize is needed between `destroy` and the read); `Graph.fork`, `Graph.destroy`; `Options.Position.create ?multiplier ~underlying ~id ~strike ~right ~expiry_in_days ()` (`lib/options.ml:378`), `Options.Strike.of_float`, `Options.Right.Call` (`:183`) — the same fixture shape `test/test_options_graph.ml:61` uses; `downstream_of_aapl_tick`, `check_recomputed`, `with_graph` (existing).
- Produces: nothing new in `lib/`. The count formula, which Task 9's `/api/graph` test and the spec's caption rely on: `named = 2|S| + |K| + |L| + 2|O| + 29` and `inputs = 4|S| + 5 + 2|O| + (1 if |O| > 0 else 0)`.

- [ ] **Step 1: Write the failing test**

Add to `test/test_graph.ml`, immediately after Task 4's `test_topology_families`:

```ocaml
(* An instrument cap hangs off ONE exposure, and that exposure off one price
   and one quantity. This is why the page can leave limit:aapl-cap at full
   authority while CVX is quiet: its whole upstream is three nodes, none of
   them CVX's. *)
let test_a_limit_has_one_input () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "limit:aapl-cap reads exposure:AAPL and nothing else" [ "exposure:AAPL" ]
        (Graph.Topology.inputs_of topo "limit:aapl-cap");
      Alcotest.(check (list string))
        "and its whole upstream is three nodes"
        [ "exposure:AAPL"; "price[AAPL]"; "qty[AAPL]" ]
        (sorted (set_to_list (Graph.Topology.closure topo [ "limit:aapl-cap" ] `Up))))
    ()

(* The clock's dead end, structurally. [test_clock_cannot_reach_the_risk_chain]
   shows it by running the clock; this shows there is no edge to run along.
   The two sets must agree with each other and with graph.ml's diagram. *)
let test_no_risk_node_is_downstream_of_now () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "downstream of now: the liveness branch, whole and alone"
        [ "feed:AAPL"; "feed:MSFT"; "feed:XOM"; "feed_health" ]
        (sorted (set_to_list (Graph.Topology.closure topo [ "now" ] `Down)));
      (* And from the other side: nothing upstream of feed_health is a price,
         a quantity or a return. The branch shares no edge with the book. *)
      let up = Graph.Topology.closure topo [ "feed_health" ] `Up in
      Alcotest.(check (list string))
        "upstream of feed_health: three cells, three feed nodes, the clock"
        [
          "feed:AAPL"; "feed:MSFT"; "feed:XOM"; "last_tick[AAPL]"; "last_tick[MSFT]";
          "last_tick[XOM]"; "now";
        ]
        (sorted (set_to_list up)))
    ()

(* The count as a formula in the book's shape. Hand-derived:

     per symbol   exposure:S feed:S                                   2|S|
     per sector   sector:K                                             |K|
     per limit    limit:name                                           |L|
     per option   greeks:ID option_exposure:ID                        2|O|
     singletons   the 29 derived names Task 3 lists                     29
                                                             named = 2|S| + |K| + |L| + 2|O| + 29
     cells        price qty returns last_tick  x |S|                  4|S|
                  cash equity_history factor_returns valuation_days now  5
                  contracts implied_vol  x |O|                        2|O|
                  rate -- reachable only once a contract reads it       [|O| > 0]

   Standard book: 3, 2, 7, 0 -> named 44, inputs 17, total 61 (Task 3's count).
   With one call on AAPL: 3, 2, 7, 1 -> named 46, inputs 20, total 66. *)
let aapl_call_for_topology =
  Options.Position.create ~underlying:aapl ~id:"AAPL-100C"
    ~strike:(Options.Strike.of_float 100.0) ~right:Options.Right.Call ~expiry_in_days:30.0
    ()

let test_named_node_count_formula () =
  let formula ~symbols ~sectors ~limits ~options =
    ( (2 * symbols) + sectors + limits + (2 * options) + 29,
      (4 * symbols) + 5 + (2 * options) + (if options > 0 then 1 else 0) )
  in
  with_graph
    ~f:(fun graph _ ->
      let c = Graph.Topology.counts (Graph.topology graph) in
      let named, inputs = formula ~symbols:3 ~sectors:2 ~limits:7 ~options:0 in
      Alcotest.(check int) "named on the standard book" named (Graph.Topology.Counts.named c);
      Alcotest.(check int) "inputs on the standard book" inputs (Graph.Topology.Counts.inputs c);
      Alcotest.(check int) "instruments" 3 (Graph.Topology.Counts.instruments c);
      Alcotest.(check int) "sectors" 2 (Graph.Topology.Counts.sectors c);
      Alcotest.(check int) "limits" 7 (Graph.Topology.Counts.limits c);
      Alcotest.(check int) "options" 0 (Graph.Topology.Counts.options c);
      Alcotest.(check int) "observed" 28 (Graph.Topology.Counts.observed c);
      (* The traverse's own count is strictly larger than the named count: the
         plumbing exists, it is just not drawn. *)
      Alcotest.(check bool)
        (Printf.sprintf "incremental_nodes (%d) > named + inputs (%d)"
           (Graph.Topology.Counts.incremental_nodes c) (named + inputs))
        true
        (Graph.Topology.Counts.incremental_nodes c > named + inputs))
    ();
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits:book_limits
      ~confidence:0.95 ~return_window:10 ~options:[ aapl_call_for_topology ] ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let topo = Graph.topology graph in
      let c = Graph.Topology.counts topo in
      let named, inputs = formula ~symbols:3 ~sectors:2 ~limits:7 ~options:1 in
      Alcotest.(check int) "named with one contract" named (Graph.Topology.Counts.named c);
      Alcotest.(check int) "inputs with one contract" inputs (Graph.Topology.Counts.inputs c);
      Alcotest.(check bool) "rate joined the graph" true
        (Option.is_some (Graph.Topology.find topo "rate"));
      (* The four declared edges of a Greeks node, and no fifth. *)
      Alcotest.(check (list string))
        "greeks reads price, vol, the slow clock and the rate"
        [ "implied_vol[AAPL-100C]"; "price[AAPL]"; "rate"; "valuation_days" ]
        (Graph.Topology.inputs_of topo "greeks:AAPL-100C");
      Alcotest.(check (list string))
        "option_exposure reads the Greeks, the count and the spot"
        [ "contracts[AAPL-100C]"; "greeks:AAPL-100C"; "price[AAPL]" ]
        (Graph.Topology.inputs_of topo "option_exposure:AAPL-100C");
      Alcotest.(check bool) "and folds into the underlying's exposure" true
        (List.mem (Graph.Topology.inputs_of topo "exposure:AAPL") "option_exposure:AAPL-100C"
           ~equal:String.equal);
      (match Graph.Topology.find topo "greeks:AAPL-100C" with
      | Some n ->
          Alcotest.(check (option string)) "the option node rows under its underlying"
            (Some "AAPL")
            (Option.map (Graph.Topology.Node.symbol n) ~f:Symbol.to_string);
          Alcotest.(check (option string)) "and names its contract" (Some "AAPL-100C")
            (Option.map (Graph.Topology.Node.contract n) ~f:fst)
      | None -> Alcotest.fail "greeks:AAPL-100C is not in the topology"))

(* The observed flags are the label table's, and ranks never run backwards. *)
let test_observed_flags_and_ranks () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "the twenty-eight observed, by name" expected_observed
        (List.filter_map (Graph.Topology.nodes topo) ~f:(fun n ->
             if Graph.Topology.Node.observed n then Some (Graph.Topology.Node.name n) else None));
      let rank name =
        Graph.Topology.Node.rank
          (Option.value_exn (Graph.Topology.find topo name) ~message:("no node " ^ name))
      in
      List.iter (Graph.Topology.edges topo) ~f:(fun (from, into) ->
          Alcotest.(check bool)
            (Printf.sprintf "%s (rank %d) -> %s (rank %d) runs left to right" from (rank from)
               into (rank into))
            true
            (rank from < rank into));
      (* Two anchors, derived by hand along the longest path:
           price -> exposure:S -> gross -> weights -> portfolio_returns -> historical_var
           -> var_notional -> limit:var-cap -> breaches  is 8 steps, and nothing
           into breaches is longer. *)
      Alcotest.(check int) "breaches is the deepest node" 8 (rank "breaches");
      Alcotest.(check int) "limit:aapl-cap sits beside the aggregates, not in a limits column" 2
        (rank "limit:aapl-cap"))
    ()

(* Asking for the drawing runs nothing and changes no pinned set. *)
let test_topology_costs_nothing () =
  with_graph
    ~f:(fun graph recorder ->
      let before = Graph.total_nodes_recomputed () in
      ignore (Graph.topology graph : Graph.Topology.t);
      Alcotest.(check int) "no node ran for the topology" before (Graph.total_nodes_recomputed ());
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"and the next tick recomputes exactly the pinned set"
        ~expected:downstream_of_aapl_tick)
    ()

(* A fork's observers come and go with the fork. [walk] starts from THIS
   graph's observers, so a fork alive in the same process is invisible to the
   served topology; and [destroy] releases the fork's twenty-eight, so
   /api/stress's twelve forks leave the process with the observer count it
   had. Both halves matter: the first is what makes the drawing this book's,
   the second is what keeps a scenario from costing every later stabilize. *)
let test_a_forks_observers_are_released () =
  with_graph
    ~f:(fun graph _ ->
      let before = Graph.active_observers () in
      let served = Graph.topology graph in
      let forked = Graph.fork graph in
      Alcotest.(check int) "a fork adds exactly its twenty-eight observers" (before + 28)
        (Graph.active_observers ());
      let fork_topology = Graph.topology forked in
      Alcotest.(check int) "the fork has its own copy of every node"
        (List.length (Graph.Topology.nodes served))
        (List.length (Graph.Topology.nodes fork_topology));
      Alcotest.(check int) "and the served topology did not grow"
        (List.length (Graph.Topology.nodes served))
        (List.length (Graph.Topology.nodes (Graph.topology graph)));
      Graph.destroy forked;
      Alcotest.(check int) "destroy releases all twenty-eight" before (Graph.active_observers ()))
    ()
```

and register them in `suite`, immediately after Task 4's `"TOPOLOGY: families and the typed fields beside them"` case:

```ocaml
      Alcotest.test_case "TOPOLOGY: an instrument cap has one input" `Quick
        test_a_limit_has_one_input;
      Alcotest.test_case "TOPOLOGY: no risk node is downstream of now" `Quick
        test_no_risk_node_is_downstream_of_now;
      Alcotest.test_case "TOPOLOGY: the named-node count is a formula in the book" `Quick
        test_named_node_count_formula;
      Alcotest.test_case "TOPOLOGY: observed flags and monotone ranks" `Quick
        test_observed_flags_and_ranks;
      Alcotest.test_case "TOPOLOGY: asking for it costs nothing" `Quick
        test_topology_costs_nothing;
      Alcotest.test_case "TOPOLOGY: a fork's observers are released by destroy" `Quick
        test_a_forks_observers_are_released;
```

`test_graph.ml` does not yet open `Ohcamel.Options`; `Options.Position.create` resolves because the file's `open Ohcamel.Types` does not shadow it and `Ohcamel` is the library — write `Ohcamel.Options.Position.create`, `Ohcamel.Options.Strike.of_float`, `Ohcamel.Options.Right.Call` if the bare names fail to resolve (the test file has `module Graph = Ohcamel.Graph` and `module Limits = Ohcamel.Limits` at its head; add `module Options = Ohcamel.Options` beside them).

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | grep -E 'FAIL|Error|passed|failed' | head
```

Expected: the suite compiles (every name exists after Task 4) and the six cases run. They should pass on the first run — this task exists to pin, not to drive a change — EXCEPT that the two hand-derived rank anchors are the assertions most likely to be wrong by one. If `breaches is the deepest node` reports 7 or 9, recount the longest path from the `edges` the failure prints (`dune exec test/test_ohcamel.exe -- test graph` then read the `-> ` lines), fix the integer in the test, and say so in the commit. Do not change ranks to fit the test.

- [ ] **Step 3: Minimal implementation**

None. If a case fails for a reason other than the two anchors, the defect is in Task 4's contraction or Task 3's labelling and is fixed there: a wrong family means `classify`'s prefix order (`option_exposure:` must not match `exposure:` — `String.chop_prefix` anchors at the start, so it does not); a wrong observer count means a `named` wrapper landed inside a `cutoff` rather than outside it (the observed marker goes on the node `observe_silent` receives, which must be the same node `named` labelled).

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -15
```

Expected: green; the `graph` suite now has fourteen TOPOLOGY/label cases from Tasks 3–5.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add test/test_graph.ml
git commit -m "graph: pin the topology from five more directions, because a drawing the tests do not hold is a drawing that can drift"
```

---

### Task 6: The served graphs are built with the recompute log, the log is handed to `Server.create`, and the probe runs before the served graph exists

Three wiring changes and a gate. `Server.create` gains `?recompute_log`; `run_demo` (which today builds its graph with NO hook) and `run_live` build the served graph with a hook that notes into a `Recompute_log.t` and pass that log to the server; and the scaling probe — three throwaway graphs of up to four hundred names — runs BEFORE the served graph is created, so its thousands of node creations land before the first tick and never inside the two-second window `smoke.sh` reads "the counter advances" over. `bin/main.ml` is touched, so the six credential-free modes are captured from the commit before this task and diffed after it.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/server.ml` — `type t`, `create`, one accessor
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/bin/main.ml` — `run_live` (the `Graph.create` inside `| Ok config ->` and the `Server.create` inside `| Some port ->`), `run_demo` (its `Graph.create` and `Server.create`)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml` — `with_graph`, `with_server`, one new helper, one new case
- Create (scratchpad, nothing in the repository): `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/{capture-before.sh,gate.sh}` and six captures
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml`; the gate

**Interfaces:**
- Consumes: `Recompute_log.{create : unit -> t; note : t -> string -> unit}` (Phase 3 Task 2); `Scaling_probe.rows : seed:int -> sizes:int list -> ticks:int -> row list`, `Scaling_probe.default_sizes = [10; 100; 400]`, `Scaling_probe.default_ticks = 50` (Phase 3 Task 4); `bin/main.ml`'s `Counter` wrapper as Phase 3 leaves it — `Counter.create`, `Counter.on_compute : Counter.t -> string -> unit`, field `log : Recompute_log.t` (Phase 3 Task 2's replacement block); `Server.create ?coalesce ?history_capacity ?alerts ?peer ?feed_stats ?quiet ~mode ~graph ~factor ()` exactly as Phase 2 Task 8 defines it; `test/test_server.ml`'s `with_graph` and `with_server` as Phase 2 Task 8 leaves them; Phase 3 Task 1's `gate.sh` (copied verbatim into a second directory, because it resolves its baseline from its own location).
- Produces: `Server.create` gains **`?recompute_log:Recompute_log.t`** (the label is `recompute_log`; Phase 5's Task 7 adds the required `~reports` and `~garch` to the same signature and, in the same task, rewrites `with_server` and `with_logged_server` below to pass them — that rewrite keeps `?recompute_log` in `with_server`'s pass-through and `~recompute_log:log` in `with_logged_server`, and is the only later edit to either helper); `Server.recompute_log : t -> Recompute_log.t option`; `test_server.ml`'s `with_graph ?seed ?on_compute ~f ()` and `with_logged_server ~f ()` where `f : Server.t -> Graph.t -> Recompute_log.t -> unit`; in `bin/main.ml`, `run_demo`'s and `run_live`'s served graphs carry a hook and the probe precedes them. `/api/ops`'s `graph.named` stays `null` until Task 8.

- [ ] **Step 1: Write the failing test**

In `test/test_server.ml`, replace the head of `with_graph` —

```ocaml
let with_graph ?(seed = true) ~f () =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 100_000.0) ~instruments:book ~limits
      ~confidence:0.95 ~return_window:10 ()
  in
```

with

```ocaml
let with_graph ?(seed = true) ?on_compute ~f () =
  let graph =
    Graph.create ?on_compute ~starting_cash:(Notional.of_float 100_000.0) ~instruments:book
      ~limits ~confidence:0.95 ~return_window:10 ()
  in
```

and replace Phase 2's `with_server` in full with:

```ocaml
let with_server ?(mode = `Demo) ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~f () =
  with_graph
    ~f:(fun graph ->
      let server =
        Server.create ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~mode ~graph
          ~factor:"SYNTHETIC" ()
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
      let server = Server.create ~recompute_log:log ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
      f server graph log)
    ()
```

Then add, before `let suite =`:

```ocaml
(* The server holds the log it was given, and nothing when it was given none.
   An option rather than a default-constructed log, because a server with no
   hook wired into its graph would drain an empty table forever and publish
   [recomputed: []] on every frame -- "nothing ran", which is the alarm, said
   about a graph that simply was not asked. *)
let test_the_server_holds_the_log () =
  with_logged_server
    ~f:(fun server _graph log ->
      match Server.recompute_log server with
      | Some held -> Alcotest.(check bool) "the same log" true (phys_equal held log)
      | None -> Alcotest.fail "the log was dropped")
    ();
  with_server
    ~f:(fun server _graph ->
      Alcotest.(check bool) "absent when none was given" true
        (Option.is_none (Server.recompute_log server)))
    ()
```

and register it in `suite`, after the last existing case:

```ocaml
      Alcotest.test_case "the server holds the recompute log" `Quick
        test_the_server_holds_the_log;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -12
```

Expected: `Error: This expression has type ... The function applied to this argument has type ...` at `?recompute_log` in `with_server` — `Server.create` has no such argument yet.

- [ ] **Step 3: Minimal implementation**

**(a) Capture the baseline FIRST**, from the commit this task starts on (Task 5's), in a worktree, so the captures are of `bin/main.ml` as it is before any edit below:

```bash
mkdir -p /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate
cp /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh \
   /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/gate.sh
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/capture-before.sh <<'SH'
#!/usr/bin/env bash
# Capture the six credential-free modes from the commit BEFORE Phase 4 touches
# bin/main.ml, built in a detached worktree with the repository's own switch.
# Run once; gate.sh beside this file diffs the working tree against it.
set -eu
BASE="$(cd "$(dirname "$0")" && pwd)"
REPO="/Users/ajaiupadhyaya/Documents/OhCamel"
WT="/tmp/ohcamel-phase4-before"
SHA="$(git -C "$REPO" rev-parse HEAD)"
rm -rf "$WT"
git -C "$REPO" worktree prune
git -C "$REPO" worktree add --detach "$WT" "$SHA"
eval "$(opam env --switch="$REPO" --set-switch)"
( cd "$WT" && dune build bin/main.exe )
echo "$SHA" > "$BASE/BASELINE_SHA"
for mode in synthetic stress backtest backtest-crisis options garch; do
  ( cd "$WT" && ./_build/default/bin/main.exe "$mode" ) > "$BASE/$mode.txt" 2>&1
  echo "captured $mode ($(wc -l < "$BASE/$mode.txt") lines)"
done
shasum -a 256 "$BASE"/*.txt > "$BASE/BASELINE_SHA256"
git -C "$REPO" worktree remove --force "$WT"
SH
chmod +x /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/*.sh
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/capture-before.sh
```

Expected: a full build in the worktree (minutes), then six `captured <mode> (N lines)` lines. `garch` is the slow one (the 180-fit study). If Phase 3's `phase3-baseline/*.txt` still exist, `diff` any one of them against the new capture — they must be identical, because Phase 3's final gate passed on the commit these were taken from.

**(b) `lib/server.ml`.** In `type t`, after the `history : History_buffer.t;` field, add:

```ocaml
  (* The hook's tally for THIS graph, drained by the broadcaster and nowhere
     else. An option: a server can be built over a graph that was given no
     hook (the tests do), and it must then say [null] rather than drain an
     empty table and report that nothing ran. *)
  recompute_log : Recompute_log.t option;
```

In `create`, add `?(recompute_log : Recompute_log.t option)` to the parameter list immediately after `?(quiet : Types.Symbol.t list = [])`, and `recompute_log;` to the record literal immediately after `history = …;`. At the end of the file, after Phase 2's `let quiet (t : t) = t.quiet`, add:

```ocaml
let recompute_log (t : t) = t.recompute_log
```

**(c) `bin/main.ml`, `run_demo`.** Immediately before `let graph =\n    Graph.create ~starting_cash ~instruments ~limits:demo_limits …`, insert:

```ocaml
  (* The probe runs before the served graph exists.

     It builds three graphs of ten, a hundred and four hundred names and ticks
     each fifty times, and Incremental's counters are process-wide. Run after
     the engine were up, its tail would sit inside the two-second window
     smoke.sh reads "the counter advances" over, and the one assertion that
     carries a deploy's weight would be measuring the probe instead of the
     pulse. The rows are dropped here; Phase 5 keeps them for /api/reports. *)
  (* Phase 5 replaces this with Reports.compute, which runs the same probe. *)
  let (_ : Scaling_probe.row list) =
    Scaling_probe.rows ~seed:2026_07_30 ~sizes:Scaling_probe.default_sizes
      ~ticks:Scaling_probe.default_ticks
  in
  printf "  probe       10 / 100 / 400 names, 50 ticks each, before the served graph\n";
  (* The served graph's hook. Every named node body notes into this log; the
     server drains it once per frame, and a fork -- which does not inherit the
     hook -- can never reach it. *)
  let log = Recompute_log.create () in
```

then change that `Graph.create` to begin `Graph.create ~on_compute:(Recompute_log.note log) ~starting_cash …` (the rest of the call unchanged), and add `~recompute_log:log` to `run_demo`'s `Server.create` call immediately after `?alerts`. Phase 2's Task 13 left it as

```ocaml
  let server =
    Server.create ?alerts ~mode:`Demo ~quiet:[ quiet ] ~graph ~factor:"SYNTHETIC" ()
  in
```

and it becomes exactly

```ocaml
  let server =
    Server.create ?alerts ~recompute_log:log ~mode:`Demo ~quiet:[ quiet ] ~graph
      ~factor:"SYNTHETIC" ()
  in
```

(Phase 5's Task 7 adds `~reports ~garch` to this same call, immediately after `~recompute_log:log`.)

**(d) `bin/main.ml`, `run_live`.** Inside `| Ok config ->`, immediately before `let counter = Counter.create () in`, insert the same probe block (the same comment, the same `let (_ : Scaling_probe.row list) = …` and the same `printf`), then:

```ocaml
      (* Two readers of one hook. The terminal's per-trade column drains the
         Counter's log, and the page's per-frame set drains this one; a single
         log would let each steal the other's set. Two increments per node body
         instead of one, on a path that does thirty of them per tick. *)
      let log = Recompute_log.create () in
```

and change the served graph's hook from `~on_compute:(Counter.on_compute counter)` to

```ocaml
          ~on_compute:(fun name ->
            Counter.on_compute counter name;
            Recompute_log.note log name)
```

and add `~recompute_log:log` to `run_live`'s `Server.create` call inside `| Some port ->`, immediately before `~graph`. Phase 2's Task 13 left it as

```ocaml
            let server =
              Server.create ?alerts ~mode:`Live ?peer:runtime.Config.Runtime.peer_origin
                ~feed_stats:
                  (Feed_source.live ~alpaca_feed:runtime.Config.Runtime.alpaca_feed
                     ~fred_series:runtime.Config.Runtime.fred_series_id
                     ~alpaca:alpaca_stats ~fred:fred_stats)
                ~graph ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

and it becomes exactly

```ocaml
            let server =
              Server.create ?alerts ~mode:`Live ?peer:runtime.Config.Runtime.peer_origin
                ~feed_stats:
                  (Feed_source.live ~alpaca_feed:runtime.Config.Runtime.alpaca_feed
                     ~fred_series:runtime.Config.Runtime.fred_series_id
                     ~alpaca:alpaca_stats ~fred:fred_stats)
                ~recompute_log:log ~graph ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

(`make fmt` may re-wrap either call; the argument set is what matters.)

**What Phase 5 does to this later:** its Task 7 replaces, in both functions, the block from the marker comment `(* Phase 5 replaces this with Reports.compute, which runs the same probe. *)` through the `printf "  probe …"` line with `let reports_record = Reports.compute () in …` — `Reports.compute` runs the same probe (same seed, sizes and tick count) before the served graph exists, so the two must never both run — and adds `~reports ~garch` to the two `Server.create` calls shown above, keeping `~recompute_log:log`. The marker comment is the anchor Phase 5 quotes; keep it exactly as written.

- [ ] **Step 4: Run the tests, the gate, and see the demo carry the hook**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -8
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-gate/gate.sh
```

Expected: green; then six lines `GATE ok        synthetic` … `GATE ok        garch`, exit 0 — none of the six modes goes through `run_demo` or `run_live`, so their stdout cannot have moved; a `GATE DIFFERS` here means an edit landed outside those two functions.

Then the demo, detached:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 4
grep -n 'probe\|dashboard' /tmp/ohcamel-demo.log
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: the `probe` line printed BEFORE the `dashboard` line. (The demo's port is positional — `ohcamel demo [port]`, usage text at `bin/main.ml:1830`, dispatch at `:1862` — which is why the command reads `main.exe demo 8137` and not `--port`.)

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml bin/main.ml test/test_server.ml
git commit -m "server: the served graph writes a log the server holds, and the probe runs first, because a frame must say what ran and the smoke window must not read the probe's tail"
```

---

### Task 7: The broadcaster drains the log into `render ?recomputed`; `/api/snapshot` carries `recomputed: null`; the stolen-set and empty-follow-up tests

Only the stream knows what ran between frames. `render` is called by `/api/snapshot` and by every new subscriber's welcome frame (`lib/server.ml:423, 444` of the 7e4a265 tree), and if either drained the log it would steal the set the next stream frame was about to carry. So `render` itself never drains: the broadcaster hands it a drain thunk, which `render` calls AFTER `Graph.snapshot`'s stabilize and BEFORE a byte is serialised — the only order in which the set is the frame's. The two counters' deltas are computed in the same place and against the previous frame. A render-time stabilize that changes something fires `on_change` inside `Graph.snapshot`, fills the next Ivar, and produces a follow-up frame whose set is tiny or empty; it is emitted honestly and tested as such.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/server.ml` — `type t`, `create`, `json_of_snapshot`'s signature and four keys, `render`, `run_broadcaster`, two new functions
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml`

**Interfaces:**
- Consumes: `Recompute_log.drain : t -> (string * int) list` (Phase 3 Task 2); `Graph.total_stabilizes`, `Graph.total_nodes_recomputed` (existing, `lib/graph.ml:1790-1791`); `Ivar.is_full : 'a t -> bool`, `Ivar.fill_if_empty` (`_opam/lib/async_kernel/ivar.mli:33,39`); Incremental bumps `stabilization_num` on every `stabilize`, dirty or not (`_opam/lib/incremental/state.ml:1328`, reached from `stabilize` at `:1369`), so one `Graph.snapshot` is exactly one in `stabilizes_delta`; `with_logged_server`, `field_exn`, `num` (Task 6 and existing helpers in `test_server.ml`).
- Produces: `Server.json_of_snapshot ?recomputed:(string * int) list ?stabilizes_delta:int ?nodes_recomputed_delta:int ~graph ~factor s` — the three are `null` when absent; new keys `recomputed`, `stabilizes`, `stabilizes_delta`, `nodes_recomputed_delta`; `Server.render : ?recomputed:(unit -> (string * int) list) -> t -> string` (the thunk is called after the snapshot's stabilize; `render t` with no thunk is what `/api/snapshot` and the welcome frame send, with the three keys `null`); `Server.next_frame : t -> string` — exactly the string the broadcaster broadcasts; `Server.pending_frame : t -> bool` — whether a change has been observed since the last frame was taken (the Ivar is full); `Server.t` fields `mutable last_stabilizes : int` and `mutable last_nodes_recomputed : int`.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, before `let suite =`:

```ocaml
let parse s = Yojson.Safe.from_string s

let names_of (json : Yojson.Safe.t) : string list =
  match json with
  | `List entries ->
      List.map entries ~f:(fun e ->
          match field_exn e "name" with `String s -> s | _ -> Alcotest.fail "name is not a string")
  | other -> Alcotest.failf "recomputed is not a list: %s" (Yojson.Safe.to_string other)

(* A poller cannot steal the stream's set.

   /api/snapshot renders, and rendering stabilizes; if it also drained the
   log, a curl between two frames would leave the next frame saying nothing
   ran. So the snapshot path says [null] -- "the stream knows, ask it" -- and
   the set is still there for the frame that follows. *)
let test_a_poller_cannot_steal_the_streams_set () =
  with_logged_server
    ~f:(fun server graph log ->
      ignore (Ohcamel.Recompute_log.drain log : (string * int) list);
      ignore (Server.next_frame server : string);
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      let poll = parse (Server.render server) in
      Alcotest.(check bool) "/api/snapshot carries recomputed: null" true
        (match field_exn poll "recomputed" with `Null -> true | _ -> false);
      Alcotest.(check bool) "and no deltas" true
        (match (field_exn poll "stabilizes_delta", field_exn poll "nodes_recomputed_delta") with
        | `Null, `Null -> true
        | _ -> false);
      Alcotest.(check bool) "but the process-wide stabilizes total" true
        (Float.( > ) (num poll "stabilizes") 0.0);
      let frame = parse (Server.next_frame server) in
      let ran = names_of (field_exn frame "recomputed") in
      Alcotest.(check bool) "the frame after the poll still carries the tick's set" true
        (List.mem ran "exposure:AAPL" ~equal:String.equal
        && List.mem ran "feed:AAPL" ~equal:String.equal);
      Alcotest.(check bool) "and only the tick's set" false
        (List.mem ran "covariance" ~equal:String.equal
        || List.mem ran "exposure:XOM" ~equal:String.equal);
      (match field_exn frame "recomputed" with
      | `List entries ->
          List.iter entries ~f:(fun e ->
              Alcotest.(check bool) "each ran once inside one coalesce window" true
                (Float.equal (num e "n") 1.0))
      | _ -> Alcotest.fail "recomputed is not a list");
      Alcotest.(check bool) "the deltas are numbers on a frame" true
        (Float.( >= ) (num frame "stabilizes_delta") 1.0
        && Float.( > ) (num frame "nodes_recomputed_delta") 0.0))
    ()

(* The follow-up frame. A render-time stabilize that changes something fires
   on_change INSIDE Graph.snapshot, which fills the next Ivar; the broadcaster
   wakes again, coalesces, and takes another frame. That frame's set is empty
   -- the work was credited to the frame whose stabilize did it -- and it is
   sent as [recomputed: []] rather than suppressed, because a frame that
   arrives and says "nothing" is a fact about the engine and dropping it would
   make the frame-arrival strip lie. *)
let test_a_render_time_stabilize_produces_an_empty_follow_up () =
  with_logged_server
    ~f:(fun server graph log ->
      ignore (Ohcamel.Recompute_log.drain log : (string * int) list);
      ignore (Server.next_frame server : string);
      Alcotest.(check bool) "quiet: no frame pending" false (Server.pending_frame server);
      (* A write with NO stabilize: the broadcaster's own snapshot will be the
         first stabilize to see it. *)
      Graph.set_price graph aapl (Price.of_float 152.0);
      Alcotest.(check bool) "a bare Var.set observes nothing yet" false
        (Server.pending_frame server);
      let first = parse (Server.next_frame server) in
      Alcotest.(check bool) "the frame whose stabilize did the work carries it" true
        (List.mem (names_of (field_exn first "recomputed")) "exposure:AAPL" ~equal:String.equal);
      Alcotest.(check bool) "and that stabilize filled the next Ivar" true
        (Server.pending_frame server);
      let follow_up = parse (Server.next_frame server) in
      Alcotest.(check (list string)) "the follow-up carries recomputed: []" []
        (names_of (field_exn follow_up "recomputed"));
      Alcotest.(check bool) "one stabilize, zero node bodies" true
        (Float.equal (num follow_up "stabilizes_delta") 1.0
        && Float.equal (num follow_up "nodes_recomputed_delta") 0.0))
    ()
```

and register both in `suite`, after Task 6's case:

```ocaml
      Alcotest.test_case "a poller cannot steal the stream's recomputed set" `Quick
        test_a_poller_cannot_steal_the_streams_set;
      Alcotest.test_case "a render-time stabilize produces an empty follow-up frame" `Quick
        test_a_render_time_stabilize_produces_an_empty_follow_up;
```

`test_server.ml` needs `Tick` in scope: it already has `open Ohcamel.Types`, and `Tick` is a `Types` module (`test_graph.ml` uses `{ Tick.symbol = …}` under the same open).

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -12
```

Expected: `Error: Unbound value Server.next_frame`.

- [ ] **Step 3: Minimal implementation**

**(a) `type t`.** After Task 6's `recompute_log` field, add:

```ocaml
  (* The two process-wide counters as they stood when the previous frame was
     taken. Deltas are computed against these, in the broadcaster only, so a
     frame's delta means "since the frame before it" and a poll in between
     cannot reset them. *)
  mutable last_stabilizes : int;
  mutable last_nodes_recomputed : int;
```

and in `create`'s record literal, after `recompute_log;`:

```ocaml
      last_stabilizes = Graph.total_stabilizes ();
      last_nodes_recomputed = Graph.total_nodes_recomputed ();
```

**(b) `json_of_snapshot`.** Change its head to

```ocaml
let json_of_snapshot ?recomputed ?stabilizes_delta ?nodes_recomputed_delta ~(graph : Graph.t)
    ~(factor : string) (s : Graph.Snapshot.t) : Yojson.Safe.t =
  let weights = Graph.Snapshot.weights s in
  let jopt_int = function None -> `Null | Some n -> `Int n in
```

and replace its last field, `("nodes_recomputed", `Int (Graph.total_nodes_recomputed ()));`, with:

```ocaml
      ("nodes_recomputed", `Int (Graph.total_nodes_recomputed ()));
      ("stabilizes", `Int (Graph.total_stabilizes ()));
      (* What ran since the previous frame, by name, from the hook the tests
         pin. [null] on /api/snapshot and on a welcome frame: only the stream
         knows what ran between two of its frames, and a poller that could
         drain the set would leave the next frame saying nothing ran. [n] is
         greater than one when a node ran in more than one stabilize inside
         the coalesce window. *)
      ( "recomputed",
        match recomputed with
        | None -> `Null
        | Some entries ->
            jlist
              (fun (name, n) -> `Assoc [ ("name", jstring name); ("n", `Int n) ])
              entries );
      ("stabilizes_delta", jopt_int stabilizes_delta);
      ("nodes_recomputed_delta", jopt_int nodes_recomputed_delta);
```

**(c) `render`, `next_frame`, `pending_frame`.** Replace `render` with:

```ocaml
(* One serialised frame.

   [recomputed] is a DRAIN, not a list, and it is called here between the
   snapshot and the encoder because that is the only order in which the set is
   the frame's: [Graph.snapshot] stabilizes, and a node body that runs inside
   that stabilize must be credited to this frame and not to the next. Called
   with no drain by /api/snapshot and by every subscriber's welcome frame,
   which then say [null] -- neither is allowed to take the stream's set. *)
let render ?recomputed (t : t) : string =
  let snapshot = Graph.snapshot t.graph in
  let json =
    match recomputed with
    | None -> json_of_snapshot ~graph:t.graph ~factor:t.factor snapshot
    | Some drain ->
        let entries = drain () in
        let stabilizes = Graph.total_stabilizes () in
        let nodes = Graph.total_nodes_recomputed () in
        let stabilizes_delta = stabilizes - t.last_stabilizes in
        let nodes_recomputed_delta = nodes - t.last_nodes_recomputed in
        t.last_stabilizes <- stabilizes;
        t.last_nodes_recomputed <- nodes;
        json_of_snapshot ~recomputed:entries ~stabilizes_delta ~nodes_recomputed_delta
          ~graph:t.graph ~factor:t.factor snapshot
  in
  match json with
  | `Assoc fields ->
      Yojson.Safe.to_string (`Assoc (fields @ [ ("alerts", json_of_alerts t.alerts) ]))
  | other -> Yojson.Safe.to_string other

(* The frame the broadcaster sends: the snapshot, then the drain. With no log
   -- a server over a graph that was given no hook -- the three keys stay
   [null] on every frame, which is "this process is not counting" and is
   different from [] , "nothing ran". *)
let next_frame (t : t) : string =
  match t.recompute_log with
  | None -> render t
  | Some log -> render ~recomputed:(fun () -> Recompute_log.drain log) t

(* Whether a change has been observed since the last frame was taken. The
   test for the follow-up frame reads it; nothing else does. *)
let pending_frame (t : t) : bool = Ivar.is_full t.changed
```

**(d) `run_broadcaster`.** Replace the `let%bind () = if List.is_empty t.subscribers then Deferred.unit else broadcast t (render t) in` line with:

```ocaml
  let%bind () =
    if List.is_empty t.subscribers then (
      (* Nobody to send to, but the window still closes: the set is dropped
         and the counters advanced, so a subscriber arriving after an hour of
         quiet gets a welcome frame and then frames about what happens NEXT,
         not one frame carrying an hour of history. No snapshot is taken --
         the point of the empty-subscriber branch was never to serialise. *)
      Option.iter t.recompute_log ~f:(fun log ->
          ignore (Recompute_log.drain log : (string * int) list));
      t.last_stabilizes <- Graph.total_stabilizes ();
      t.last_nodes_recomputed <- Graph.total_nodes_recomputed ();
      Deferred.unit)
    else broadcast t (next_frame t)
  in
```

`/api/snapshot`'s route (Phase 2's table) calls `render t` and needs no change; the welcome frame in `subscribe` calls `render t` and needs no change.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -8
```

Expected: green. Then on a socket — the stream's second frame carries a list and the poll carries null:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
curl -s http://localhost:8137/api/snapshot | python3 -c 'import json,sys; d=json.load(sys.stdin); print("snapshot recomputed:", d["recomputed"], "stabilizes:", d["stabilizes"])'
curl -s -N --max-time 2 http://localhost:8137/api/stream | grep '^data:' | sed -n '2p' | cut -c7- | python3 -c 'import json,sys; d=json.load(sys.stdin); print("frame recomputed:", len(d["recomputed"]), "names; deltas", d["stabilizes_delta"], d["nodes_recomputed_delta"])'
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `snapshot recomputed: None stabilizes: <N>` and `frame recomputed: <between 20 and 40> names; deltas <small ints>` (the first `data:` line is the welcome frame with null, which is why the second is read).

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: each frame says what ran since the last one, because a poller must not steal the stream's set and a render-time stabilize must be credited to its own frame"
```

---

### Task 8: `json_of_snapshot` grows the fields the drawing and the ledger read — `by_node`, the attribution fields, `risk_share`, `quiet` — and `/api/ops` reports the log

The drawing prints a value under every scalar node, and the encoder emits those values itself, keyed by the `Node_name` string, next to the fields it already reads. A client-side table from node name to snapshot field would be a second description of the encoder, one refactor from silently mislabelling a node; `Node_name.unit_of` says what each value is and a test asserts that every scalar node in the topology has a `by_node` key on a warmed-up snapshot. The shares and ratios §02 prints come from the encoder too (invariant 2 applied to a division). `/api/ops`'s `graph.named`, `null` since Phase 2, is filled from the log forks cannot reach.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/server.ml` — `json_of_snapshot` (positions, sectors, six new keys), a new `by_node` encoder, `render` (passes `quiet`), `json_of_ops`'s `graph` slot
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml`

**Interfaces:**
- Consumes: `Graph.Snapshot.{prices, quantities, marginal_by_instrument, standalone_by_instrument, euler_residual, covariance_for_attribution}` (Task 2); `Graph.Node_name.{Input.*, exposure, sector, unit_of, is_scalar}` (Task 1); `Graph.topology`, `Graph.Topology.{nodes, Node.name, Node.unit}` (Task 4); `Graph.{cash, rate, valuation_days, option_ids, contracts, implied_vol}` (existing, `lib/graph.ml:1522,1541,1547,1550,1552,1568`); `Options.Contracts.to_float`, `Options.Implied_vol.to_float` (`lib/options.ml:97,118`); `Recompute_log.{distinct, total, hottest ~n}` (Phase 3); `Server.t.quiet : Types.Symbol.t list` (Phase 2 Task 8); Phase 2 Task 11's placeholder in `json_of_ops`, quoted below.
- Produces: `json_of_snapshot` gains `?(quiet : Types.Symbol.t list = [])`; keys `positions[].{price, qty, marginal, standalone, risk_share, risk_over_money}`, `sectors[].risk_share`, `euler_residual`, `attribution_covariance`, `by_node`, `quiet`; `Server.by_node : graph:Graph.t -> Graph.Snapshot.t -> Yojson.Safe.t`; `/api/ops.graph.named = {distinct, total, hottest: [{name, n}]}` (ten hottest) or `null` when the server holds no log. `risk_share` is this row's component VaR over the sum of every instrument's component VaR (the Euler total, which is the parametric VaR notional); `risk_over_money` is `risk_share / |weight|`; both `null` while warming up, at a zero total, or (the ratio) at zero weight.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, before `let suite =`:

```ocaml
(* Every scalar node has a value, and the values are the snapshot's.

   The topology says which nodes have a unit a number can carry; the encoder
   says what the numbers are; this is the assertion that the two agree, so
   the drawing can never point at a scalar node and find nothing under it.
   [rate] is the one key allowed to exist without a node: on a book with no
   contracts nothing reads the rate cell, so it is not in the graph, but the
   cell is real and its value is emitted anyway. *)
let test_every_scalar_node_has_a_by_node_value () =
  with_graph
    ~f:(fun graph ->
      let s = Graph.snapshot graph in
      let json = Server.json_of_snapshot ~graph ~factor:"SYNTHETIC" s in
      let by_node = field_exn json "by_node" in
      let keys =
        match by_node with
        | `Assoc kv -> List.map kv ~f:fst
        | _ -> Alcotest.fail "by_node is not an object"
      in
      let topo = Graph.topology graph in
      let names = String.Set.of_list (Graph.Topology.names topo) in
      List.iter (Graph.Topology.nodes topo) ~f:(fun n ->
          if Graph.Node_name.is_scalar (Graph.Topology.Node.unit n) then
            Alcotest.(check bool)
              (Graph.Topology.Node.name n ^ " has a by_node value")
              true
              (List.mem keys (Graph.Topology.Node.name n) ~equal:String.equal));
      List.iter keys ~f:(fun key ->
          Alcotest.(check bool)
            (key ^ " is a scalar node of this graph, or the rate cell")
            true
            (String.equal key "rate"
            || (Set.mem names key && Graph.Node_name.is_scalar (Graph.Node_name.unit_of key))));
      Alcotest.(check (float 1e-9)) "exposure:AAPL" 30_000.0 (num by_node "exposure:AAPL");
      Alcotest.(check (float 1e-9)) "price[AAPL]" 150.0 (num by_node "price[AAPL]");
      Alcotest.(check (float 1e-9)) "qty[XOM] keeps its sign" (-400.0) (num by_node "qty[XOM]");
      Alcotest.(check (float 1e-9)) "cash" 100_000.0 (num by_node "cash");
      Alcotest.(check (float 1e-9)) "gross_exposure" 70_000.0 (num by_node "gross_exposure");
      Alcotest.(check bool) "historical_var is a number once warm" true
        (match field_exn by_node "historical_var" with `Float _ -> true | _ -> false))
    ()

(* The shares and the ratio, hand-derived on this file's two-name book.

   AAPL 30,000 and XOM -40,000 on returns r and -r: weights 3/7 and -4/7,
   every pair perfectly correlated, so the book behaves as one asset with
   sigma_p = sigma. marginal(AAPL) = sigma, marginal(XOM) = -sigma;
   component = weight x marginal = 3/7 sigma and 4/7 sigma; they sum to
   sigma_p. So risk_share is 3/7 and 4/7, and risk over money -- share over
   |weight| -- is exactly 1.0 for both: with correlations at one, every
   dollar carries the same risk. The interesting books are the ones where it
   is not 1.0, and this is the reference they are read against. *)
let test_risk_share_and_risk_over_money () =
  with_graph
    ~f:(fun graph ->
      let json = encode graph in
      let positions =
        match field_exn json "positions" with `List ps -> ps | _ -> Alcotest.fail "positions"
      in
      let by_symbol s =
        List.find_exn positions ~f:(fun p ->
            match field_exn p "symbol" with `String x -> String.equal x s | _ -> false)
      in
      let a = by_symbol "AAPL" and x = by_symbol "XOM" in
      Alcotest.(check (float 1e-9)) "AAPL risk_share" (3.0 /. 7.0) (num a "risk_share");
      Alcotest.(check (float 1e-9)) "XOM risk_share" (4.0 /. 7.0) (num x "risk_share");
      Alcotest.(check (float 1e-9)) "AAPL risk over money" 1.0 (num a "risk_over_money");
      Alcotest.(check (float 1e-9)) "XOM risk over money" 1.0 (num x "risk_over_money");
      Alcotest.(check (float 1e-9)) "price rides on the row" 100.0 (num x "price");
      Alcotest.(check (float 1e-9)) "and qty, signed" (-400.0) (num x "qty");
      Alcotest.(check bool) "marginal(XOM) is negative" true (Float.( < ) (num x "marginal") 0.0);
      Alcotest.(check bool) "standalone(XOM) is positive" true
        (Float.( > ) (num x "standalone") 0.0);
      let sectors =
        match field_exn json "sectors" with `List ss -> ss | _ -> Alcotest.fail "sectors"
      in
      let shares = List.map sectors ~f:(fun k -> num k "risk_share") in
      Alcotest.(check (float 1e-9)) "sector shares sum to one" 1.0
        (List.fold shares ~init:0.0 ~f:( +. ));
      Alcotest.(check bool) "the Euler residual is on the wire and tiny" true
        (Float.( < ) (Float.abs (num json "euler_residual")) 1e-9);
      Alcotest.(check bool) "and which matrix was decomposed" true
        (match field_exn json "attribution_covariance" with
        | `String "equal_weighted" -> true
        | _ -> false))
    ()

(* Unknown stays unknown: a share of a total that does not exist yet is null,
   not zero, on every row. *)
let test_risk_share_is_null_while_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let json = encode graph in
      (match field_exn json "positions" with
      | `List ps ->
          List.iter ps ~f:(fun p ->
              List.iter [ "marginal"; "standalone"; "risk_share"; "risk_over_money" ]
                ~f:(fun k ->
                  Alcotest.(check bool) (k ^ " is null while warming up") true
                    (match field_exn p k with `Null -> true | _ -> false)))
      | _ -> Alcotest.fail "positions");
      Alcotest.(check bool) "euler_residual is null too" true
        (match field_exn json "euler_residual" with `Null -> true | _ -> false))
    ()

(* The demo's quiet name travels on the frame, so a stale symbol that is stale
   ON PURPOSE can be labelled as such by the page instead of reading as a
   broken feed. Empty on a live server, which has no such name. *)
let test_quiet_is_on_the_wire () =
  with_server ~quiet:[ xom ]
    ~f:(fun server _graph ->
      let json = parse (Server.render server) in
      Alcotest.(check (list string)) "quiet: [XOM]" [ "XOM" ]
        (match field_exn json "quiet" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> Alcotest.fail "quiet"))
    ();
  with_server
    ~f:(fun server _graph ->
      let json = parse (Server.render server) in
      Alcotest.(check (list string)) "quiet: [] by default" []
        (match field_exn json "quiet" with `List xs -> List.map xs ~f:(fun _ -> "x") | _ -> [ "?" ]))
    ()

(* /api/ops says which named nodes are hot, from the log forks never reach. *)
let test_ops_reports_the_named_log () =
  with_logged_server
    ~f:(fun server graph _log ->
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      let _, _, body = respond server "/api/ops" in
      let named = field_exn (field_exn (parse body) "graph") "named" in
      Alcotest.(check bool) "distinct > 0" true (Float.( > ) (num named "distinct") 0.0);
      Alcotest.(check bool) "total >= distinct" true
        (Float.( >= ) (num named "total") (num named "distinct"));
      match field_exn named "hottest" with
      | `List entries ->
          Alcotest.(check bool) "at most ten hottest" true (List.length entries <= 10);
          List.iter entries ~f:(fun e ->
              ignore (field_exn e "name" : Yojson.Safe.t);
              ignore (num e "n" : float))
      | _ -> Alcotest.fail "hottest is not a list")
    ()
```

and register the five in `suite`, after Task 7's cases:

```ocaml
      Alcotest.test_case "every scalar node has a by_node value" `Quick
        test_every_scalar_node_has_a_by_node_value;
      Alcotest.test_case "risk_share and risk over money are the encoder's" `Quick
        test_risk_share_and_risk_over_money;
      Alcotest.test_case "risk_share is null while warming up" `Quick
        test_risk_share_is_null_while_warming_up;
      Alcotest.test_case "the quiet list is on the wire" `Quick test_quiet_is_on_the_wire;
      Alcotest.test_case "/api/ops reports the named log" `Quick test_ops_reports_the_named_log;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | grep -E 'FAIL|Error|missing key' | head
```

Expected: the suite compiles (no new lib names are referenced from the test) and the first failure is `missing key "by_node"` from `field_exn`.

- [ ] **Step 3: Minimal implementation**

**(a) `by_node`.** In `lib/server.ml`, immediately before `let json_of_snapshot`, add:

```ocaml
(* Every scalar named node's value, in its own unit, keyed by the Node_name
   string.

   Emitted by the encoder rather than assembled by the client from the other
   fields, because a client-side map from node name to snapshot field would be
   a second description of this encoder -- one refactor from labelling a
   fraction as dollars. Node_name.unit_of says what each value is measured in,
   the topology carries that unit beside the node, and test_server.ml asserts
   that every scalar node in the topology has a key here. Map-, array- and
   state-valued nodes are absent on purpose: the drawing prints a run count
   under those, never a number.

   The three cells read through getters -- cash, rate, valuation_days -- are
   read after the snapshot, and that is safe for exactly the reason the
   Snapshot module's note on [prices] says it is not safe in general: nothing
   writes these three between a stabilize and the encoder in a single Async
   job, whereas prices and quantities are what every tick writes. *)
let by_node ~(graph : Graph.t) (s : Graph.Snapshot.t) : Yojson.Safe.t =
  let module N = Graph.Node_name in
  let per_symbol =
    List.concat_map (Map.to_alist (Graph.Snapshot.exposure_by_instrument s))
      ~f:(fun (symbol, exposure) ->
        [
          (N.exposure symbol, jnotional exposure);
          ( N.Input.price symbol,
            jfloat (Types.Price.to_float (Map.find_exn (Graph.Snapshot.prices s) symbol)) );
          ( N.Input.qty symbol,
            jfloat (Types.Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) symbol)) );
        ])
  in
  let per_sector =
    List.map (Map.to_alist (Graph.Snapshot.exposure_by_sector s)) ~f:(fun (sector, exposure) ->
        (N.sector sector, jnotional exposure))
  in
  let per_option =
    List.concat_map (Graph.option_ids graph) ~f:(fun id ->
        [
          (N.Input.contracts id, jfloat (Options.Contracts.to_float (Graph.contracts graph id)));
          ( N.Input.implied_vol id,
            jfloat (Options.Implied_vol.to_float (Graph.implied_vol graph id)) );
        ])
  in
  let singletons =
    [
      (N.gross, jnotional (Graph.Snapshot.gross_exposure s));
      (N.net, jnotional (Graph.Snapshot.net_exposure s));
      (N.equity, jnotional (Graph.Snapshot.equity s));
      (N.drawdown, jfloat (Graph.Snapshot.current_drawdown s));
      (N.historical_var, jopt_float (Graph.Snapshot.historical_var s));
      (N.expected_shortfall, jopt_float (Graph.Snapshot.expected_shortfall s));
      (N.parametric_var, jopt_float (Graph.Snapshot.parametric_var s));
      (N.parametric_var_ewma, jopt_float (Graph.Snapshot.parametric_var_ewma s));
      (N.var_notional, jopt_notional (Graph.Snapshot.value_at_risk_notional s));
      (N.es_notional, jopt_notional (Graph.Snapshot.expected_shortfall_notional s));
      (N.portfolio_beta, jopt_float (Graph.Snapshot.portfolio_beta s));
      (N.diversification_ratio, jopt_float (Graph.Snapshot.diversification_ratio s));
      (N.portfolio_gamma, jfloat (Graph.Snapshot.portfolio_gamma s));
      (N.portfolio_vega, jfloat (Graph.Snapshot.portfolio_vega s));
      (N.Input.cash, jnotional (Graph.cash graph));
      (N.Input.rate, jfloat (Graph.rate graph));
      (N.Input.valuation_days, jfloat (Graph.valuation_days graph));
    ]
  in
  `Assoc (per_symbol @ per_sector @ per_option @ singletons)
```

**(b) `json_of_snapshot`.** Its head becomes:

```ocaml
let json_of_snapshot ?recomputed ?stabilizes_delta ?nodes_recomputed_delta
    ?(quiet : Types.Symbol.t list = []) ~(graph : Graph.t) ~(factor : string)
    (s : Graph.Snapshot.t) : Yojson.Safe.t =
  let weights = Graph.Snapshot.weights s in
  let jopt_int = function None -> `Null | Some n -> `Int n in
  (* The Euler total every share is taken over: the sum of the instrument
     components, which by construction is the parametric VaR notional. Summed
     from the same map the numerators come from, so the denominator is
     provably the same numbers -- the reason the old client summed it too.
     None while warming up; None at exactly zero, because a share of nothing
     is not a number. *)
  let component_total =
    Option.bind (Graph.Snapshot.component_var_by_instrument s) ~f:(fun shares ->
        let total =
          Map.fold shares ~init:0.0 ~f:(fun ~key:_ ~data acc ->
              acc +. Types.Notional.to_float data)
        in
        if Float.equal total 0.0 then None else Some total)
  in
  let share_of (component : Types.Notional.t option) : float option =
    Option.both component component_total
    |> Option.map ~f:(fun (c, total) -> Types.Notional.to_float c /. total)
  in
  let lookup map key = Option.bind map ~f:(fun m -> Map.find m key) in
```

In the `positions` encoder, after the `("component_var", …)` field, add:

```ocaml
                (* The marks and the position the exposure above multiplies
                   out to, read from the same fixed point (Snapshot.prices). *)
                ( "price",
                  jfloat (Types.Price.to_float (Map.find_exn (Graph.Snapshot.prices s) symbol))
                );
                ( "qty",
                  jfloat (Types.Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) symbol))
                );
                (* The Euler pair, in return space as attribution.ml computes
                   them: marginal is a RATE (portfolio sigma per unit of
                   weight), standalone an AMOUNT (|w| sigma_i). null while
                   warming up. *)
                ( "marginal",
                  jopt_float (lookup (Graph.Snapshot.marginal_by_instrument s) symbol) );
                ( "standalone",
                  jopt_float (lookup (Graph.Snapshot.standalone_by_instrument s) symbol) );
                (* Share of the Euler total, and that share over the share of
                   money. Computed HERE and not in the browser: invariant 2
                   applied to a division. A hedge's share is negative and its
                   ratio is negative; the client must not take a magnitude. *)
                (let risk_share =
                   share_of (lookup (Graph.Snapshot.component_var_by_instrument s) symbol)
                 in
                 ("risk_share", jopt_float risk_share));
                ( "risk_over_money",
                  let risk_share =
                    share_of (lookup (Graph.Snapshot.component_var_by_instrument s) symbol)
                  in
                  match (risk_share, Map.find weights symbol) with
                  | Some share, Some w when not (Float.equal w 0.0) ->
                      jfloat (share /. Float.abs w)
                  | _ -> `Null );
```

In the `sectors` encoder, after its `("component_var", …)` field, add:

```ocaml
                ( "risk_share",
                  jopt_float (share_of (lookup (Graph.Snapshot.component_var_by_sector s) sector))
                );
```

And after the `("diversification_ratio", …)` field of the top-level object, add:

```ocaml
      (* Sum of the components minus portfolio sigma. Exact in real arithmetic;
         published so the self-check runs on the real book, not only on a
         seeded one in a test. *)
      ("euler_residual", jopt_float (Graph.Snapshot.euler_residual s));
      ( "attribution_covariance",
        jstring
          (Graph.Covariance_estimator.to_string (Graph.Snapshot.covariance_for_attribution s))
      );
      ("by_node", by_node ~graph s);
      (* Names that are stale on purpose -- the demo's never-ticked symbol --
         so the page can label them rather than report a broken feed. *)
      ("quiet", jlist (fun sym -> jstring (Types.Symbol.to_string sym)) quiet);
```

**(c) `render`.** Both `json_of_snapshot` calls inside Task 7's `render` gain `~quiet:t.quiet`.

**(d) `json_of_ops`.** Phase 2's Task 11 wrote, in the `process`/`stream` neighbourhood:

```ocaml
      (* The per-graph counts, which forks never reach. Phase 4 fills this from
         the recompute log; until then it is null, because a zero here would
         say "no named node ran", which is the alarm. *)
      ("graph", `Assoc [ ("named", `Null) ]);
```

Replace those four lines with:

```ocaml
      (* The per-graph counts, from the log the served graph's hook writes and
         a fork never reaches. null when this server holds no log, which is
         "not counting" and is not the zero that would mean "nothing ran". *)
      ( "graph",
        `Assoc
          [
            ( "named",
              match t.recompute_log with
              | None -> `Null
              | Some log ->
                  `Assoc
                    [
                      ("distinct", `Int (Recompute_log.distinct log));
                      ("total", `Int (Recompute_log.total log));
                      ( "hottest",
                        jlist
                          (fun (name, n) -> `Assoc [ ("name", jstring name); ("n", `Int n) ])
                          (Recompute_log.hottest log ~n:10) );
                    ] );
          ] );
```

Phase 2's `test_ops_shape` asserts `graph.named` is `Null` on a `with_server` server — still true, since `with_server` passes no log.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -8
```

Expected: green, including the pre-existing `test_round_trips` (the new keys survive a parse) and `test_non_finite_becomes_null`. If `every scalar node has a by_node value` names a node, `by_node` is missing that key — add it beside its neighbours and never as a client-side alias.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: the encoder says what every scalar node is worth and how risk is shared, because a client table from names to fields is a second encoder that can lie"
```

---

### Task 9: `GET /api/graph` — the topology as JSON, memoised at `Server.create`, in the routes table and in the smoke suite's shadow of it

The topology costs nothing on the tick path and never changes after construction, so it is encoded once when the server is built and served as the same string forever. The route joins Phase 2's table immediately before `/api/ops` — so Phase 5's two report routes, which its Task 7 inserts "immediately after `/api/stress`", land between `/api/stress` and this one without either plan editing the other's line — and `deploy/smoke.sh`'s `EXPECTED_ROUTES` string gains it in the same position, in the same commit.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/server.ml` — `json_of_graph` (new), `type t`, `create`, `routes`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/deploy/smoke.sh` — the `EXPECTED_ROUTES=` line Phase 2's Task 15 added
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml`

**Interfaces:**
- Consumes: `Graph.topology ?alerts`, `Graph.Topology.{t, Node, Outside, Counts, Family.to_string, limit_kind_to_string}` (Task 4); `Types.Limit.{name, kind, scope}`, `Types.Limit.scope_to_string` (`lib/types.ml:289`); `Server.routes : (string * string * handler) list`, `Server.route_paths`, `json_headers ~mode` (Phase 2 Tasks 9 and 12); `respond` (Phase 2 Task 12's test helper); `deploy/smoke.sh`'s `EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/ops"` (Phase 2 Task 15, its plan line 3426).
- Produces: `Server.json_of_graph : Graph.Topology.t -> Yojson.Safe.t`; `Server.t.graph_json : string`; `GET /api/graph` = `{nodes: [{name, family, kind, rank, observed, cutoff, unit, symbol: string | null, sector: string | null, limit: {name, kind, scope} | null, option: {id, underlying} | null}], edges: [[from, to]], outside: [{name, reads: [string], present: bool, wired_to: string | null}], attribution_covariance: "equal_weighted" | "ewma", counts: {instruments, sectors, limits, options, named, inputs, observed, incremental_nodes}}` — Phase 5's `argument.js` reads `nodes[].{name, family}`, `edges`, `outside`, `counts.named`; `Server.routes` order becomes `/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/ops`; `EXPECTED_ROUTES` the same nine tokens.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, before `let suite =`:

```ocaml
(* /api/graph is the topology, once, as a string. Two reads are the same
   bytes and cost the engine nothing; every edge endpoint is a served node;
   the counts are the formula Task 5 pinned, on this file's two-name book:
   named = 2*2 + 2 + 3 + 0 + 29 = 38, inputs = 4*2 + 5 = 13, 51 nodes. *)
let test_api_graph () =
  with_server
    ~f:(fun server _graph ->
      let before = Graph.total_nodes_recomputed () in
      let status, content_type, body = respond server "/api/graph" in
      Alcotest.(check int) "200" 200 status;
      Alcotest.(check (option string)) "JSON" (Some "application/json") content_type;
      let _, _, again = respond server "/api/graph" in
      Alcotest.(check bool) "memoised: the same string object" true (phys_equal body again);
      Alcotest.(check int) "and no node ran to serve it" before (Graph.total_nodes_recomputed ());
      let json = parse body in
      let nodes = match field_exn json "nodes" with `List ns -> ns | _ -> Alcotest.fail "nodes" in
      let names =
        String.Set.of_list
          (List.map nodes ~f:(fun n ->
               match field_exn n "name" with `String s -> s | _ -> Alcotest.fail "name"))
      in
      Alcotest.(check int) "51 nodes on the two-name book" 51 (List.length nodes);
      let counts = field_exn json "counts" in
      Alcotest.(check (float 0.0)) "counts.named" 38.0 (num counts "named");
      Alcotest.(check (float 0.0)) "counts.inputs" 13.0 (num counts "inputs");
      Alcotest.(check (float 0.0)) "counts.observed" 28.0 (num counts "observed");
      Alcotest.(check (float 0.0)) "counts.instruments" 2.0 (num counts "instruments");
      (match field_exn json "edges" with
      | `List edges ->
          Alcotest.(check bool) "there are edges" true (not (List.is_empty edges));
          List.iter edges ~f:(function
            | `List [ `String from; `String into ] ->
                Alcotest.(check bool) (from ^ " -> " ^ into ^ " joins served nodes") true
                  (Set.mem names from && Set.mem names into)
            | other -> Alcotest.failf "edge is not a pair: %s" (Yojson.Safe.to_string other))
      | _ -> Alcotest.fail "edges");
      (* One node of each shape, field by field. *)
      let node name =
        List.find_exn nodes ~f:(fun n ->
            match field_exn n "name" with `String s -> String.equal s name | _ -> false)
      in
      let str n key = match field_exn n key with `String s -> s | _ -> "<not a string>" in
      let cell = node "price[AAPL]" in
      Alcotest.(check string) "family" "input" (str cell "family");
      Alcotest.(check string) "kind" "Var" (str cell "kind");
      Alcotest.(check string) "cutoff" "Equal" (str cell "cutoff");
      Alcotest.(check string) "unit" "price" (str cell "unit");
      Alcotest.(check string) "symbol" "AAPL" (str cell "symbol");
      Alcotest.(check (float 0.0)) "rank" 0.0 (num cell "rank");
      Alcotest.(check bool) "not observed" false
        (match field_exn cell "observed" with `Bool b -> b | _ -> true);
      Alcotest.(check bool) "limit and option are null on a cell" true
        (match (field_exn cell "limit", field_exn cell "option") with
        | `Null, `Null -> true
        | _ -> false);
      let cap = node "limit:aapl-cap" in
      Alcotest.(check string) "per_limit" "per_limit" (str cap "family");
      let l = field_exn cap "limit" in
      Alcotest.(check string) "limit.name" "aapl-cap" (str l "name");
      Alcotest.(check string) "limit.kind" "gross_notional" (str l "kind");
      Alcotest.(check string) "limit.scope" "instrument:AAPL" (str l "scope");
      Alcotest.(check string) "the matrix the decomposition reads" "equal_weighted"
        (str json "attribution_covariance");
      (* The outside, as facts. No alerts were attached to this server. *)
      (match field_exn json "outside" with
      | `List outs ->
          Alcotest.(check (list string)) "four readers, in order"
            [ "alerts"; "history"; "stream"; "kill_switch" ]
            (List.map outs ~f:(fun o -> str o "name"));
          let by name = List.find_exn outs ~f:(fun o -> String.equal (str o "name") name) in
          Alcotest.(check bool) "alerts absent" false
            (match field_exn (by "alerts") "present" with `Bool b -> b | _ -> true);
          Alcotest.(check bool) "the kill switch is wired to nothing" true
            (match field_exn (by "kill_switch") "wired_to" with `Null -> true | _ -> false);
          Alcotest.(check (list string)) "history reads six" 
            [ "gross_exposure"; "net_exposure"; "equity"; "current_drawdown"; "var_notional"; "es_notional" ]
            (match field_exn (by "history") "reads" with
            | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
            | _ -> []);
          Alcotest.(check int) "the stream reads every observed node" 28
            (match field_exn (by "stream") "reads" with `List xs -> List.length xs | _ -> 0)
      | _ -> Alcotest.fail "outside");
      Alcotest.(check (list string)) "the route sits immediately before /api/ops"
        [ "/api/graph"; "/api/ops" ]
        (List.drop (Server.route_paths ()) (List.length (Server.route_paths ()) - 2)))
    ()
```

and register it in `suite`, after Task 8's cases:

```ocaml
      Alcotest.test_case "/api/graph is the topology, memoised" `Quick test_api_graph;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | grep -E 'FAIL|Error|not routed' | head -5
```

Expected: `[FAIL] … /api/graph is the topology, memoised` with `/api/graph is not routed` (from `respond`).

- [ ] **Step 3: Minimal implementation**

**(a) `json_of_graph`.** In `lib/server.ml`, immediately after `json_of_stress`, add:

```ocaml
(* The topology, in the spec's field list. Optional fields are null rather
   than absent, so a client reads [n.symbol] on every node and never tests for
   a key. The limit's kind and scope are strings a reader can print; the
   thresholds are not here, because the ledger already carries them with
   their units and a drawing that repeated them would be a second place for
   them to be wrong. *)
let json_of_graph (topo : Graph.Topology.t) : Yojson.Safe.t =
  let module T = Graph.Topology in
  let jopt f = function None -> `Null | Some x -> f x in
  let node (n : T.Node.t) =
    `Assoc
      [
        ("name", jstring (T.Node.name n));
        ("family", jstring (T.Family.to_string (T.Node.family n)));
        ("kind", jstring (T.Node.kind n));
        ("rank", `Int (T.Node.rank n));
        ("observed", `Bool (T.Node.observed n));
        ("cutoff", jstring (T.Node.cutoff n));
        ("unit", jstring (T.Node.unit n));
        ("symbol", jopt (fun s -> jstring (Types.Symbol.to_string s)) (T.Node.symbol n));
        ("sector", jopt (fun k -> jstring (Types.Sector.to_string k)) (T.Node.sector n));
        ( "limit",
          jopt
            (fun (l : Types.Limit.t) ->
              `Assoc
                [
                  ("name", jstring (Types.Limit.name l));
                  ("kind", jstring (T.limit_kind_to_string (Types.Limit.kind l)));
                  ("scope", jstring (Types.Limit.scope_to_string (Types.Limit.scope l)));
                ])
            (T.Node.limit n) );
        ( "option",
          jopt
            (fun (id, underlying) ->
              `Assoc
                [
                  ("id", jstring id); ("underlying", jstring (Types.Symbol.to_string underlying));
                ])
            (T.Node.contract n) );
      ]
  in
  let outside (o : T.Outside.t) =
    `Assoc
      [
        ("name", jstring (T.Outside.name o));
        ("reads", jlist jstring (T.Outside.reads o));
        ("present", `Bool (T.Outside.present o));
        ("wired_to", jopt jstring (T.Outside.wired_to o));
      ]
  in
  let c = T.counts topo in
  `Assoc
    [
      ("nodes", jlist node (T.nodes topo));
      ("edges", jlist (fun (from, into) -> `List [ jstring from; jstring into ]) (T.edges topo));
      ("outside", jlist outside (T.outside topo));
      ( "attribution_covariance",
        jstring (Graph.Covariance_estimator.to_string (T.attribution_covariance topo)) );
      ( "counts",
        `Assoc
          [
            ("instruments", `Int (T.Counts.instruments c));
            ("sectors", `Int (T.Counts.sectors c));
            ("limits", `Int (T.Counts.limits c));
            ("options", `Int (T.Counts.options c));
            ("named", `Int (T.Counts.named c));
            ("inputs", `Int (T.Counts.inputs c));
            ("observed", `Int (T.Counts.observed c));
            ("incremental_nodes", `Int (T.Counts.incremental_nodes c));
          ] );
    ]
```

**(b) `type t` and `create`.** After Task 7's `last_nodes_recomputed` field, add:

```ocaml
  (* The topology, encoded once. It cannot change after construction -- every
     edge is declared when the graph is built -- so serving it is a string
     copy, and the drawing on a thousand tabs costs the engine nothing. *)
  graph_json : string;
```

and in `create`'s record literal, after `last_nodes_recomputed = …;`:

```ocaml
      graph_json =
        Yojson.Safe.to_string
          (json_of_graph (Graph.topology ~alerts:(Option.is_some alerts) graph));
```

**(c) The route.** In Phase 2's `routes` list, immediately BEFORE the `( "/api/ops", …)` entry, insert:

```ocaml
    (* Memoised at [create]: the topology is a fact about the construction of
       the graph and cannot change afterwards, so this is a string copy. *)
    ( "/api/graph",
      "the dependency graph as Incremental holds it: named nodes, edges, ranks",
      fun t -> Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode) t.graph_json
    );
```

**(d) `deploy/smoke.sh`.** The line

```bash
EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/ops"
```

becomes

```bash
EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/ops"
```

(Phase 5's Task 7 later inserts `/api/reports /api/reports/garch` after `/api/stress` in this string; both plans then agree on the final order `… /api/stress /api/reports /api/reports/garch /api/graph /api/ops`.) The 404 assertion's count is computed from the string, so `lists the 9 routes` prints without a further edit; Phase 2's `docs/status.md` line quoting `8 routes` is Phase 6's to refresh.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -8
bash -n deploy/smoke.sh && grep -c '/api/graph' deploy/smoke.sh
```

Expected: green (Phase 2's `the 404 body lists exactly the routes table` still passes — it compares the body to `route_paths ()`, not to a literal); `1`. Then on a socket:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
curl -s http://localhost:8137/api/graph | python3 -c 'import json,sys; g=json.load(sys.stdin); c=g["counts"]; print(len(g["nodes"]), "nodes,", len(g["edges"]), "edges; named", c["named"], "observed", c["observed"], "incremental", c["incremental_nodes"]); print(sorted(set(n["rank"] for n in g["nodes"])))'
curl -s http://localhost:8137/api/nope | python3 -c 'import json,sys; print(json.load(sys.stdin)["routes"])'
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `82 nodes, <N> edges; named 53 observed 28 incremental <a few hundred>` on the six-name, three-sector, nine-limit demo book (2·6 + 3 + 9 + 29 = 53 named; 4·6 + 5 = 29 inputs), ranks `[0, 1, …, 8]`, and the 404 list ending `'/api/graph', '/api/ops'`.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml deploy/smoke.sh test/test_server.ml
git commit -m "server: the graph on the wire, memoised, because the drawing must be taken from the graph and the graph cannot change after it is built"
```

---

### Task 10: `GET /api/stress` replaced — structured shocks, the book before, equity and drawdown on both sides, breaches as records with their text, the counter's cost and the wall time

The one breaking change in the design. The old body listed breach NAMES and shock SENTENCES; §07 needs the breach records in the ledger's `.lim` form with `Limits.to_string` beside them, each shock as structure (`kind`, `symbol`, `sector`, `move`) AND as the CLI's sentence, the book as it stood before any fork (so "equity before → after" is one object, not a diff of two), and two numbers the page prints to make the point that a scenario is real work on a fork: how far `total_nodes_recomputed` moved and how long the twelve forks took. `Stress.Shock.to_json` lives beside `to_string` so the two can never disagree about what a shock is.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/stress.ml` — `Shock.to_json` after `Shock.to_string`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/server.ml` — `json_of_stress` replaced in full
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_stress.ml` (one case), `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_server.ml` (one case)

**Interfaces:**
- Consumes: `Stress.{Shock.t, Shock.to_string, Scenario.{name, description, shocks}, Outcome.{scenario, before, after, pnl, pnl_fraction, new_breaches, cleared_breaches, unestimated_betas}, suite_for, run_all, worst}` (existing, `lib/stress.ml`); `Limits.to_string : Breach.t -> string` (`lib/limits.ml:188`); `Server.json_of_breach` (existing, `lib/server.ml:91`); `Types.Time.{now, diff}` (`lib/types.ml:178-179`), `Time_ns.Span.to_ms : t -> float` (verified: `_opam/lib/core/span_intf.ml:117`, included into `Time_ns.Span` by `_opam/lib/core/time_ns_intf.ml:16`; `lib/history_buffer.ml:198` already calls `Types.Time.Span.to_ms`, and Phase 2's `json_of_ops` uses the same form for `coalesce_ms`); `Graph.total_nodes_recomputed`; `Graph.Snapshot.{gross_exposure, equity, current_drawdown, value_at_risk_notional, breaches}`.
- Produces: `Stress.Shock.to_json : t -> Yojson.Safe.t` = `{kind: "all" | "instrument" | "sector" | "factor" | "volatility", symbol: string | null, sector: string | null, move: float, text: string}`; `GET /api/stress` = `{as_of, before: {gross_exposure, equity, value_at_risk_notional | null, breached: [name]}, scenarios: [{name, description, shocks: [shock], pnl, pnl_fraction, equity_before, equity_after, drawdown_before, drawdown_after, gross_exposure, value_at_risk_notional | null, new_breaches: [breach + text], cleared_breaches: [breach + text], unestimated_betas: [symbol]}], worst: name | null, counter_cost: int, duration_ms: float}` — exactly the shape Phase 5's `argument.js` consumes; `breach + text` is `json_of_breach`'s eight keys plus `text`.

- [ ] **Step 1: Write the failing test**

In `test/test_stress.ml`, before its `let suite =`, add (the file already has `module Stress = Ohcamel.Stress` and `open Ohcamel.Types`; if it does not open `Types`, qualify `Symbol`/`Sector`):

```ocaml
(* A shock as structure and as its own sentence, side by side, so the page can
   draw the one and print the other and a reader can check them against each
   other. [move] for a factor shock is in the factor series' units -- 100bp is
   1.0 -- and the sentence says so; the JSON does not convert. *)
let test_shock_to_json () =
  let get j k = match j with `Assoc kv -> List.Assoc.find_exn kv k ~equal:String.equal | _ -> `Null in
  let all = Stress.Shock.to_json (Stress.Shock.All (-0.10)) in
  Alcotest.(check bool) "all: kind" true (match get all "kind" with `String "all" -> true | _ -> false);
  Alcotest.(check bool) "all: move" true (match get all "move" with `Float f -> Float.equal f (-0.10) | _ -> false);
  Alcotest.(check bool) "all: no symbol, no sector" true
    (match (get all "symbol", get all "sector") with `Null, `Null -> true | _ -> false);
  Alcotest.(check bool) "all: text is to_string" true
    (match get all "text" with `String s -> String.equal s "everything -10.0%" | _ -> false);
  let sector = Stress.Shock.to_json (Stress.Shock.Sector (Sector.of_string "TECH", 0.2)) in
  Alcotest.(check bool) "sector: named" true
    (match get sector "sector" with `String "TECH" -> true | _ -> false);
  let factor = Stress.Shock.to_json (Stress.Shock.Factor 1.0) in
  Alcotest.(check bool) "factor: move is in the series' units" true
    (match get factor "move" with `Float f -> Float.equal f 1.0 | _ -> false);
  let vol = Stress.Shock.to_json (Stress.Shock.Volatility 3.0) in
  Alcotest.(check bool) "volatility: kind and scale" true
    (match (get vol "kind", get vol "move") with
    | `String "volatility", `Float f -> Float.equal f 3.0
    | _ -> false)
```

and register it in that file's `suite`: `Alcotest.test_case "a shock is JSON beside its sentence" `Quick test_shock_to_json;`.

In `test/test_server.ml`, before `let suite =`:

```ocaml
(* The stress body, hand-derived on this file's book.

   AAPL 150 x 200 = +30,000; XOM 100 x -400 = -40,000; cash 100,000; net
   -10,000; equity 90,000. Before any fork: aapl-cap (25,000) is already over
   at 30,000, and var-cap (3,000) is over at 0.05 x 70,000 = 3,500 -- the
   historical VaR at 95% over ten observations is the single worst return.
   So [before.breached] = [aapl-cap; var-cap], in configured order.

   broad-selloff, everything -10%: AAPL 27,000, XOM -36,000, net -9,000,
   equity 91,000 -> P&L +1,000: a short book makes money when everything
   falls, and the suite must be allowed to say so.
   energy-squeeze, XOM +20%: XOM -48,000, net -18,000, equity 82,000 ->
   P&L -8,000, the worst of the ten.
   tech-selloff, AAPL -20%: AAPL 24,000, under its 25,000 cap -> aapl-cap
   is CLEARED; var-cap stays over (0.05 x 64,000 = 3,200). *)
let test_stress_shape () =
  with_graph
    ~f:(fun graph ->
      let counter_before = Graph.total_nodes_recomputed () in
      let json = Server.json_of_stress graph in
      let str j k = match field_exn j k with `String s -> s | _ -> "<not a string>" in
      let strings j k =
        match field_exn j k with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> Alcotest.failf "%s is not a list" k
      in
      let before = field_exn json "before" in
      Alcotest.(check (float 1e-9)) "before.gross" 70_000.0 (num before "gross_exposure");
      Alcotest.(check (float 1e-9)) "before.equity" 90_000.0 (num before "equity");
      Alcotest.(check (float 1e-9)) "before.var_notional" 3_500.0
        (num before "value_at_risk_notional");
      Alcotest.(check (list string)) "before.breached" [ "aapl-cap"; "var-cap" ]
        (strings before "breached");
      let scenarios =
        match field_exn json "scenarios" with `List ss -> ss | _ -> Alcotest.fail "scenarios"
      in
      Alcotest.(check int) "six standard + two per sector, two sectors" 10 (List.length scenarios);
      let by name = List.find_exn scenarios ~f:(fun s -> String.equal (str s "name") name) in
      let selloff = by "broad-selloff" in
      (match field_exn selloff "shocks" with
      | `List [ shock ] ->
          Alcotest.(check string) "kind" "all" (str shock "kind");
          Alcotest.(check (float 1e-12)) "move" (-0.10) (num shock "move");
          Alcotest.(check string) "text" "everything -10.0%" (str shock "text")
      | _ -> Alcotest.fail "broad-selloff has one shock");
      Alcotest.(check (float 1e-6)) "selloff P&L" 1_000.0 (num selloff "pnl");
      Alcotest.(check (float 1e-9)) "selloff P&L fraction" (1_000.0 /. 90_000.0)
        (num selloff "pnl_fraction");
      Alcotest.(check (float 1e-6)) "equity_before" 90_000.0 (num selloff "equity_before");
      Alcotest.(check (float 1e-6)) "equity_after" 91_000.0 (num selloff "equity_after");
      Alcotest.(check (float 1e-6)) "gross after" 63_000.0 (num selloff "gross_exposure");
      List.iter [ "drawdown_before"; "drawdown_after"; "value_at_risk_notional" ] ~f:(fun k ->
          ignore (num selloff k : float));
      Alcotest.(check string) "worst is a name" "energy-squeeze" (str json "worst");
      Alcotest.(check (float 1e-6)) "and its P&L" (-8_000.0) (num (by "energy-squeeze") "pnl");
      (match field_exn (by "tech-selloff") "cleared_breaches" with
      | `List [ b ] ->
          Alcotest.(check string) "tech-selloff clears aapl-cap" "aapl-cap" (str b "name");
          Alcotest.(check bool) "as a record with its utilisation" true
            (Float.( < ) (num b "utilisation") 1.0);
          Alcotest.(check bool) "and the engine's own sentence" true
            (String.is_substring (str b "text") ~substring:"aapl-cap")
      | _ -> Alcotest.fail "tech-selloff clears exactly one breach");
      Alcotest.(check (list string)) "rate-shock: no factor history, no beta, both named"
        [ "AAPL"; "XOM" ]
        (strings (by "rate-shock") "unestimated_betas");
      Alcotest.(check bool) "counter_cost is the forks' work" true
        (Float.( > ) (num json "counter_cost") 0.0
        && Float.( <= ) (num json "counter_cost")
             (Float.of_int (Graph.total_nodes_recomputed () - counter_before)));
      Alcotest.(check bool) "duration_ms is a non-negative number" true
        (Float.( >= ) (num json "duration_ms") 0.0))
    ()
```

and register it in `suite`, after Task 9's case:

```ocaml
      Alcotest.test_case "/api/stress in the new shape, hand-derived" `Quick test_stress_shape;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | head -8
```

Expected: `Error: Unbound value Stress.Shock.to_json`.

- [ ] **Step 3: Minimal implementation**

**(a) `lib/stress.ml`.** Immediately after `Shock.to_string`'s last arm (`| Volatility k -> Printf.sprintf "volatility x%.2f" k`), inside `module Shock`, add:

```ocaml

  (* The same shock as structure, with the sentence beside it. Beside
     [to_string] rather than in server.ml so that what a shock IS is decided in
     one place: a page that drew the structure and printed the sentence from
     two encoders could show a sector move over a factor sentence. [move] is
     never converted -- a factor shock's move is in the series' own units, and
     the sentence says so. *)
  let to_json (t : t) : Yojson.Safe.t =
    let kind, symbol, sector, move =
      match t with
      | All f -> ("all", None, None, f)
      | Instrument (s, f) -> ("instrument", Some (Symbol.to_string s), None, f)
      | Sector (s, f) -> ("sector", None, Some (Sector.to_string s), f)
      | Factor f -> ("factor", None, None, f)
      | Volatility k -> ("volatility", None, None, k)
    in
    let opt = function None -> `Null | Some s -> `String s in
    `Assoc
      [
        ("kind", `String kind);
        ("symbol", opt symbol);
        ("sector", opt sector);
        (* Finite by construction (a literal or a validated scale), but the
           wire rule is server.ml's and it is cheap to keep here too. *)
        ("move", if Float.is_finite move then `Float move else `Null);
        ("text", `String (to_string t));
      ]
```

**(b) `lib/server.ml`.** Replace `json_of_stress` — from its comment `(* The scenario suite, run against the book as it stands right now.` through the closing `]` of its `` `Assoc `` — with:

```ocaml
(* The scenario suite, run against the book as it stands right now.

   [before] is one snapshot taken here, not the per-outcome one stress.ml
   carries: a page that prints "equity before -> after" wants one before, and
   twelve copies of the same object would be twelve chances for a reader to
   wonder whether they differ. Breaches travel as the ledger's record plus
   the engine's own sentence. [counter_cost] and [duration_ms] are the two
   facts that make the point that a scenario is real work on a fork -- the
   process-wide counter moves for a reason that is not a tick -- and the
   recompute log does not move at all, because a fork has no hook. *)
let json_of_stress (graph : Graph.t) : Yojson.Safe.t =
  let started = Types.Time.now () in
  let counter_before = Graph.total_nodes_recomputed () in
  let before = Graph.snapshot graph in
  let scenarios = Stress.suite_for ~graph in
  let outcomes = Stress.run_all ~graph ~scenarios in
  let counter_cost = Graph.total_nodes_recomputed () - counter_before in
  let duration_ms = Time_ns.Span.to_ms (Types.Time.diff (Types.Time.now ()) started) in
  let breach_with_text (b : Types.Breach.t) =
    match json_of_breach b with
    | `Assoc fields -> `Assoc (fields @ [ ("text", jstring (Limits.to_string b)) ])
    | other -> other
  in
  let breached_names (s : Graph.Snapshot.t) =
    Graph.Snapshot.breaches s
    |> List.filter ~f:Types.Breach.breached
    |> jlist (fun b -> jstring (Types.Limit.name (Types.Breach.limit b)))
  in
  `Assoc
    [
      ("as_of", jstring (Time_ns.to_string_utc (Types.Time.now ())));
      ( "before",
        `Assoc
          [
            ("gross_exposure", jnotional (Graph.Snapshot.gross_exposure before));
            ("equity", jnotional (Graph.Snapshot.equity before));
            ( "value_at_risk_notional",
              jopt_notional (Graph.Snapshot.value_at_risk_notional before) );
            ("breached", breached_names before);
          ] );
      ( "scenarios",
        jlist
          (fun (o : Stress.Outcome.t) ->
            let scenario = Stress.Outcome.scenario o in
            let b = Stress.Outcome.before o and a = Stress.Outcome.after o in
            `Assoc
              [
                ("name", jstring (Stress.Scenario.name scenario));
                ("description", jstring (Stress.Scenario.description scenario));
                ("shocks", jlist Stress.Shock.to_json (Stress.Scenario.shocks scenario));
                ("pnl", jnotional (Stress.Outcome.pnl o));
                ("pnl_fraction", jfloat (Stress.Outcome.pnl_fraction o));
                ("equity_before", jnotional (Graph.Snapshot.equity b));
                ("equity_after", jnotional (Graph.Snapshot.equity a));
                ("drawdown_before", jfloat (Graph.Snapshot.current_drawdown b));
                ("drawdown_after", jfloat (Graph.Snapshot.current_drawdown a));
                ("gross_exposure", jnotional (Graph.Snapshot.gross_exposure a));
                ( "value_at_risk_notional",
                  jopt_notional (Graph.Snapshot.value_at_risk_notional a) );
                ("new_breaches", jlist breach_with_text (Stress.Outcome.new_breaches o));
                ("cleared_breaches", jlist breach_with_text (Stress.Outcome.cleared_breaches o));
                ( "unestimated_betas",
                  jlist
                    (fun s -> jstring (Types.Symbol.to_string s))
                    (Stress.Outcome.unestimated_betas o) );
              ])
          outcomes );
      (* A name, not a copy of the object: the client finds it in [scenarios]. *)
      ( "worst",
        match Stress.worst outcomes with
        | None -> `Null
        | Some w -> jstring (Stress.Scenario.name (Stress.Outcome.scenario w)) );
      ("counter_cost", `Int counter_cost);
      ("duration_ms", jfloat duration_ms);
    ]
```

The route entry in Phase 2's table calls `json_of_stress t.graph` and needs no change. `deploy/smoke.sh`'s existing stress assertion (if it reads `.scenarios | length` or `.worst`) still holds; Phase 5's Task 15 adds the `12 scenarios, worst <name>, N node recomputations on forks` line against this shape.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -8
```

Expected: green. If `before.breached` disagrees on `var-cap`, read `test_limit_units` in the same file — it pins the same book's utilisations — before touching either the derivation or the encoder. Then on a socket:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
curl -s http://localhost:8137/api/stress | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["scenarios"]), "scenarios; worst", d["worst"], "; counter_cost", d["counter_cost"], "; duration_ms", round(d["duration_ms"],1)); print(d["scenarios"][5]["shocks"])'
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `12 scenarios; worst <name> ; counter_cost <hundreds> ; duration_ms <tens>` and the rate-shock's one shock `[{'kind': 'factor', 'symbol': None, 'sector': None, 'move': 1.0, 'text': 'factor +1.00 (through each name's beta)'}]`.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/stress.ml lib/server.ml test/test_stress.ml test/test_server.ml
git commit -m "stress: the suite on the wire as structure and sentence, with the book before and the forks' cost, because a scenario is real work and the page should be able to say so"
```

---

### Task 11: The six-name topology as a static SVG, from real `/api/graph` output, looked at before `graph.js` is written

Before the renderer exists, one page draws the served topology with the layout Task 12 will use — rank columns, rows by symbol, the singleton spine, one row per limit, the feed branch under a gap — from the real `/api/graph` JSON of the demo book, and a screenshot is taken and READ. The decision at the end is a checkbox: readable, and Task 12 draws per-name rows; or a hairball, and Task 12's default for this book becomes the family-band layout with per-name rows only for the ticking name. Nothing in the repository changes.

**Files:**
- Create (scratchpad, nothing in the repository): `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-proto/proto.html`, `shot.py`, `proto.png`
- Test: the screenshot, read with the Read tool; the crossing count the page prints

**Interfaces:**
- Consumes: `GET /api/graph` (Task 9) on the demo at `http://localhost:8137`, whose JSON routes carry `Access-Control-Allow-Origin: *` in demo mode (Phase 2 Task 9), so a `file://` page may fetch it; `web/page.css`'s tokens are NOT loaded — the prototype uses literal greys, because it is a look at geometry, not at ink.
- Produces: the layout rules Task 12 implements verbatim (`bands`, `colX`, `rowY` below), and one ticked checkbox.

- [ ] **Step 1: Write the page**

```bash
mkdir -p /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-proto
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-proto/proto.html <<'HTML'
<!doctype html><meta charset="utf-8"><title>topology prototype</title>
<style>
  body { margin: 20px; font: 12px ui-monospace, Menlo, monospace; color: #222; background: #fff; }
  svg text { font: 11px ui-monospace, Menlo, monospace; }
  .head { font-size: 10px; letter-spacing: .14em; text-transform: uppercase; fill: #999; }
  .rule { stroke: #bbb; stroke-width: .75; fill: none; }
  .edge { stroke: #c8c8c8; stroke-width: .75; fill: none; }
  .obs { fill: #222; }
  .cell { fill: none; stroke: #222; stroke-width: 1; }
  #stats { margin-bottom: 10px; }
</style>
<div id="stats">fetching /api/graph …</div>
<div id="out"></div>
<script>
(function () {
  var COL0 = 320, COL = 150, LINE = 14, GAP = 22, LEFT = 24, TOP = 34, CHAR = 6.6;
  var FEED = function (n) { return n.name === "now" || n.name === "feed_health" || n.name.indexOf("feed:") === 0 || n.name.indexOf("last_tick[") === 0; };
  // Bands, in page order. Each band is a list of rows; each row is a list of
  // columns; each column a list of nodes. Rows: one per symbol in the symbol
  // band, one per sector, one per limit; the spine is one row whose columns
  // stack; the feed band repeats the symbol rows under a gap.
  function bands(topo) {
    var symbols = [], sectors = [], limits = [];
    topo.nodes.forEach(function (n) {
      if (n.symbol && symbols.indexOf(n.symbol) < 0) symbols.push(n.symbol);
      if (n.sector && sectors.indexOf(n.sector) < 0) sectors.push(n.sector);
      if (n.limit) limits.push(n.name);
    });
    symbols.sort(); sectors.sort();
    var maxRank = 0; topo.nodes.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });
    function row() { var r = []; for (var i = 0; i <= maxRank; i++) r.push([]); return r; }
    var B = { symbols: symbols.map(row), sectors: sectors.map(row), spine: [row()], limits: limits.map(row), feed: symbols.map(row).concat([row()]) };
    topo.nodes.forEach(function (n) {
      var target;
      if (FEED(n)) target = n.symbol ? B.feed[symbols.indexOf(n.symbol)] : B.feed[symbols.length];
      else if (n.limit) target = B.limits[limits.indexOf(n.name)];
      else if (n.symbol) target = B.symbols[symbols.indexOf(n.symbol)];
      else if (n.sector) target = B.sectors[sectors.indexOf(n.sector)];
      else target = B.spine[0];
      target[n.rank].push(n);
    });
    return { order: ["symbols", "sectors", "spine", "limits", "feed"], bands: B, maxRank: maxRank, symbols: symbols };
  }
  function colX(rank) { return rank === 0 ? LEFT : LEFT + COL0 + (rank - 1) * COL; }
  // Column 0 of a symbol row lays its cells side by side (price, qty,
  // returns); everywhere else a cell's nodes stack.
  function place(topo) {
    var L = bands(topo), pos = {}, y = TOP, bandTops = {};
    L.order.forEach(function (name) {
      bandTops[name] = y;
      L.bands[name].forEach(function (r) {
        var h = 1;
        r.forEach(function (col, rank) { if (!(rank === 0 && name === "symbols")) h = Math.max(h, col.length); });
        r.forEach(function (col, rank) {
          col.sort(function (a, b) { return a.name < b.name ? -1 : 1; });
          col.forEach(function (n, i) {
            var side = rank === 0 && name === "symbols";
            pos[n.name] = { x: colX(rank) + (side ? i * 104 : 0), y: y + (side ? 0 : i * LINE), w: n.name.length * CHAR };
          });
        });
        y += h * LINE + 4;
      });
      y += GAP;
    });
    return { pos: pos, height: y, layout: L, bandTops: bandTops };
  }
  function svgEl(tag, attrs, cls) { var e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (cls) e.setAttribute("class", cls); return e; }
  function draw(topo) {
    var P = place(topo), width = colX(P.layout.maxRank) + COL + 40;
    var svg = svgEl("svg", { width: width, height: P.height, viewBox: "0 0 " + width + " " + P.height });
    for (var r = 0; r <= P.layout.maxRank; r++) { var h = svgEl("text", { x: colX(r), y: 14 }, "head"); h.textContent = "rank " + r; svg.appendChild(h); }
    var g = svgEl("text", { x: LEFT, y: P.bandTops.feed - 8 }, "head"); g.textContent = "deliberately disconnected"; svg.appendChild(g);
    var crossings = 0, segs = [];
    topo.edges.forEach(function (e) {
      var a = P.pos[e[0]], b = P.pos[e[1]]; if (!a || !b) return;
      var x1 = a.x + a.w + 6, y1 = a.y - 4, x2 = b.x - 4, y2 = b.y - 4, mx = (x1 + x2) / 2;
      svg.appendChild(svgEl("path", { d: "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2 }, "edge"));
      segs.push([x1, y1, x2, y2]);
    });
    function cross(s, t) { function o(ax, ay, bx, by, cx, cy) { return Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)); }
      return o(s[0], s[1], s[2], s[3], t[0], t[1]) !== o(s[0], s[1], s[2], s[3], t[2], t[3]) && o(t[0], t[1], t[2], t[3], s[0], s[1]) !== o(t[0], t[1], t[2], t[3], s[2], s[3]); }
    for (var i = 0; i < segs.length; i++) for (var j = i + 1; j < segs.length; j++) if (cross(segs[i], segs[j])) crossings++;
    topo.nodes.forEach(function (n) {
      var p = P.pos[n.name];
      if (n.family === "input") svg.appendChild(svgEl("rect", { x: p.x - 12, y: p.y - 10, width: 8, height: 8 }, "cell"));
      var t = svgEl("text", { x: p.x, y: p.y }); t.textContent = n.name; svg.appendChild(t);
      svg.appendChild(svgEl("line", { x1: p.x, y1: p.y + 3, x2: p.x + p.w, y2: p.y + 3 }, "rule"));
      if (n.observed) svg.appendChild(svgEl("circle", { cx: p.x + p.w + 5, cy: p.y - 3, r: 2.5 }, "obs"));
    });
    document.getElementById("out").appendChild(svg);
    var c = topo.counts;
    document.getElementById("stats").textContent =
      topo.nodes.length + " nodes (" + c.named + " named, " + c.inputs + " cells, " + c.observed + " observed), " +
      topo.edges.length + " edges, " + crossings + " straight-line crossings, " + (P.layout.maxRank + 1) + " ranks, " + P.height + "px tall";
  }
  fetch("http://localhost:8137/api/graph").then(function (r) { return r.json(); }).then(draw)
    .catch(function (e) { document.getElementById("stats").textContent = "fetch failed: " + e.message + " (is the demo on 8137?)"; });
})();
</script>
HTML
cat > /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-proto/shot.py <<'PY'
import pathlib, sys
from playwright.sync_api import sync_playwright
here = pathlib.Path(__file__).resolve().parent
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 900})
    page.goto((here / "proto.html").as_uri())
    page.wait_for_selector("svg", timeout=10000)
    stats = page.text_content("#stats")
    page.screenshot(path=str(here / "proto.png"), full_page=True)
    b.close()
print(stats)
PY
```

- [ ] **Step 2: Run it against the demo and look**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase4-proto
uv run --with playwright python -m playwright install chromium
uv run --with playwright python shot.py
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: one stats line like `82 nodes (53 named, 29 cells, 28 observed), <N> edges, <C> straight-line crossings, 9 ranks, <H>px tall`, and `proto.png` beside it. Then READ the PNG (the Read tool renders images) and answer the three questions below. `uv run --with playwright` resolves the latest `playwright` wheel; the browser install is a one-time ~150 MB download to `~/Library/Caches/ms-playwright`. If `uv` cannot fetch, the same page can be shot with the headless Chrome already on this Mac — `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --window-size=1500,1400 --screenshot=proto.png --virtual-time-budget=3000 "file://$PWD/proto.html"` — which does not print the stats line; read it off the image.

- [ ] **Step 3: Decide, and tick one box**

Three questions, answered from the image and the stats line:

1. Can `price[NVDA] → exposure:NVDA → gross_exposure → weights → … → breaches` be followed by eye as one horizontal line that bends once into the spine?
2. Is the straight-line crossing count under `2 × edges`? (Fifty-odd edges into a six-row symbol band fan into the spine; some crossing is the shape of the thing. Crossings that make a label unreadable are the failure, and the count is the proxy.)
3. Does the limits band read as one row per limit, and the feed band as a separate thing under its caption?

- [ ] **READABLE** (yes to all three): Task 12's default is per-name rows; `opts.compact` stays the `> 12` names fallback only.
- [ ] **HAIRBALL** (no to any): Task 12's `render` defaults `compact` to `true` for this book too — one band per per-symbol family (`exposure[S] × 6`, `price[S] × 6`, …) with the spine, limits and feed bands as above, and a per-name row opened only for the symbol whose nodes the current frame lit (`handle.light` unfolds it, the next quiet frame folds it). The caption says so. Everything else in Task 12 is unchanged; the choice is one default.

Record the decision and the stats line in the commit message of Task 12.

- [ ] **Step 4: Nothing to run**

No repository file changed. `git status --porcelain` prints nothing (the untracked `claudecodehandoff.md` excepted).

- [ ] **Step 5: No commit**

The prototype lives in the scratchpad and is referenced by Task 12's commit message only.

---

### Task 12: `web/graph.js` — `window.OhCamelGraph`: the topology drawn as inline SVG, the frame lit on it, staleness by closure, the hover inspector, the two absences, the observers column, the collapses, the narrow fallback

The renderer. It takes `/api/graph` JSON and produces the drawing Task 11 prototyped, with everything the spec's Figure 1 asks for, behind exactly the contract Phase 5's `argument.js` consumes: `render(container, topology, opts) → handle`, `filter(topology, {nodes?, families?})`, `closure(topology, names, 'down'|'up')`, and `handle.{light, dim, setValues, destroy}`. No library, no framework: `document.createElementNS` and hand-set attributes, in the idiom the sparkline already uses. `web/format.js` is created beside it with the three formatters copied out of `dashboard.js` (Phase 5 adds `vegaPoint` to it), and `lib/dune`'s one-line extension point grows to `(cat ../web/format.js ../web/graph.js ../web/dashboard.js)`. The page does not call it until Task 13; this task's test drives the library from a Playwright script.

**Files:**
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/format.js`, `/Users/ajaiupadhyaya/Documents/OhCamel/web/graph.js`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune` — the `(cat ../web/dashboard.js)` line of the `dashboard_html.ml` rule (Phase 1 Task 2's "one-line extension point", line 81 of the finished file)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml` — one marker case
- Test: `/tmp/ohcamel-phase4/pw/check-graph.mjs` (Playwright, dev machine only, the same install Phase 5 uses); `test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: `GET /api/graph` (Task 9) — `nodes[].{name, family, kind, rank, observed, cutoff, unit, symbol, sector, limit, option}`, `edges[[from, to]]`, `outside[].{name, reads, present, wired_to}`, `counts.{named, inputs, observed, options}`; the frame's `by_node` (Task 8) and `recomputed[].{name, n}` (Task 7) through `setValues` and `light`; `money`, `pct`, `el` — the three functions at the head of `web/dashboard.js`'s IIFE, copied character for character into `format.js` (`dashboard.js` keeps its private copies; Phase 5's reconcile pass removes them); Phase 1's `markers_in_order : string -> name:string -> markers:string list -> unit` in `test/test_embedded_assets.ml`; Task 11's layout rules and its ticked box.
- Produces: `window.OhCamelFormat = { money, pct, el }`; `window.OhCamelGraph = { render, filter, closure }` where
  - `render(container, topology, opts) → handle`; `opts = {filter?: {nodes?: string[], families?: string[]}, compact?: boolean, inspector?: boolean}`. `inspector: true` draws Figure 1's caption line and the hover inspector; fragments pass `false`. `compact` defaults to `symbols > 12` (or to `true` for every book if Task 11 ticked HAIRBALL).
  - `handle.light(names)` — `names` is `string[]` or `[{name, n}]`; those nodes and their incoming edges take class `lit` (page.css animates it to `--mark` over 0.75 s, Task 13), the run counts advance, the caption's "This frame: N ran" updates; in compact mode the lit symbol's row is opened.
  - `handle.dim(staleSymbols)` — class `stale` on `price[S]` and its downstream closure, class `over` on `feed:S` and `feed_health`; returns the `Set` of dimmed names so the ledger can dim the same rows.
  - `handle.setValues(byNode)` — the value under every scalar node in its unit; `handle.setNote(name, text)` — a caption under one node (`breaches` → `1 of 9`), an extra the contract permits; `handle.destroy()` — empties the container and drops the listeners.
  - `filter(topology, {nodes?, families?}) → sub-topology` (kept nodes, edges between them only, `outside`/`counts`/`attribution_covariance` carried through).
  - `closure(topology, names, 'down'|'up') → Set` — strictly reachable, seeds excluded, the same rule as `Graph.Topology.closure`.
  - DOM contract inside the container: `svg > g.node[data-name][data-family]` (with `rect.cell` on inputs, `text.name`, `line.rule`, `circle.obs`, `text.val`), `path.edge[data-from][data-to]`, `text.head`, `g.absent` (the two absences), `g.observers`, `div.inspector`, `figcaption.graph-cap` (inspector mode), `ol.graph-list` (under 700 px).

- [ ] **Step 1: Write the failing test**

Playwright, installed once into scratch exactly as Phase 5 does (`~/Documents/gridbox` already pins `playwright@1.61.1`, so its Chromium is in `~/Library/Caches/ms-playwright` and nothing is downloaded):

```bash
mkdir -p /tmp/ohcamel-phase4/pw && cd /tmp/ohcamel-phase4/pw && npm init -y >/dev/null && npm i --silent playwright@1.61.1 && npx playwright install chromium 2>&1 | tail -1
cat > /tmp/ohcamel-phase4/pw/check-graph.mjs <<'MJS'
// Drives window.OhCamelGraph on the served page with the served topology.
// Usage: node check-graph.mjs [http://localhost:8137]
import { chromium } from "playwright";
const base = process.argv[2] || "http://localhost:8137";
const topo = await (await fetch(base + "/api/graph")).json();
const snap = await (await fetch(base + "/api/snapshot")).json();
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto(base + "/");
const r = await page.evaluate(([topo, snap]) => {
  const G = window.OhCamelGraph, out = {};
  const div = document.createElement("div"); div.style.width = "1300px"; document.body.appendChild(div);
  const h = G.render(div, topo, { inspector: true });
  const svg = div.querySelector("svg");
  const collapsed = topo.counts.options === 0 ? ["gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"] : [];
  out.nodes = svg.querySelectorAll("g.node").length;
  out.nodesExpected = topo.nodes.length - collapsed.length;
  out.edges = svg.querySelectorAll("path.edge").length;
  out.edgesExpected = topo.edges.filter(e => collapsed.indexOf(e[0]) < 0 && collapsed.indexOf(e[1]) < 0).length;
  out.heads = Array.from(svg.querySelectorAll("text.head")).map(t => t.textContent);
  out.absent = svg.querySelectorAll("g.absent").length;
  out.observers = svg.querySelector("g.observers") !== null;
  const sym = topo.nodes.find(n => n.family === "per_symbol").symbol;
  const lit = ["exposure:" + sym, "gross_exposure", { name: "weights", n: 2 }];
  h.light(lit);
  out.litNodes = svg.querySelectorAll("g.node.lit").length;
  out.litEdges = svg.querySelectorAll("path.edge.lit").length;
  out.litEdgesExpected = topo.edges.filter(e => ["exposure:" + sym, "gross_exposure", "weights"].indexOf(e[1]) >= 0).length;
  out.caption = (div.querySelector("figcaption.graph-cap") || {}).textContent || "";
  const down = G.closure(topo, ["price[" + sym + "]"], "down");
  out.closureSize = down.size; out.closureHasCovariance = down.has("covariance"); out.closureHasSeed = down.has("price[" + sym + "]");
  const dimmed = h.dim([sym]);
  out.dimmedReturned = dimmed.size; out.staleNodes = svg.querySelectorAll("g.node.stale").length; out.overNodes = svg.querySelectorAll("g.node.over").length;
  h.setValues(snap.by_node);
  out.grossText = svg.querySelector('g.node[data-name="gross_exposure"] text.val').textContent;
  out.covText = svg.querySelector('g.node[data-name="covariance"] text.val').textContent;
  h.setNote("breaches", "1 of 9");
  out.breachesText = svg.querySelector('g.node[data-name="breaches"] text.val').textContent;
  const sub = G.filter(topo, { families: ["singleton"] });
  out.subOk = sub.nodes.every(n => n.family === "singleton") && sub.edges.every(e => sub.nodes.some(n => n.name === e[0]) && sub.nodes.some(n => n.name === e[1]));
  const d2 = document.createElement("div"); d2.style.width = "1300px"; document.body.appendChild(d2);
  const frag = G.render(d2, topo, { filter: { nodes: ["weights", "covariance", "attribution"] }, inspector: false });
  out.fragNodes = d2.querySelectorAll("g.node").length; frag.destroy();
  h.destroy(); out.emptyAfterDestroy = div.childNodes.length === 0;
  return out;
}, [topo, snap]);
await b.close();
const checks = [
  ["node count", r.nodes === r.nodesExpected, r.nodes + " vs " + r.nodesExpected],
  ["edge count", r.edges === r.edgesExpected, r.edges + " vs " + r.edgesExpected],
  ["column heads start at inputs", r.heads[0] === "inputs", r.heads.join(" | ")],
  ["two absences drawn", r.absent === 2, r.absent],
  ["observers column", r.observers, ""],
  ["three lit nodes", r.litNodes === 3, r.litNodes],
  ["their incoming edges lit", r.litEdges === r.litEdgesExpected, r.litEdges + " vs " + r.litEdgesExpected],
  ["caption counts the frame", /This frame: 3 ran/.test(r.caption), r.caption],
  ["closure excludes the seed and covariance", r.closureSize > 20 && !r.closureHasCovariance && !r.closureHasSeed, r.closureSize],
  ["dim: closure + the cell, and two --over", r.staleNodes === r.closureSize + 1 && r.dimmedReturned === r.closureSize + 1 && r.overNodes === 2, r.staleNodes + "/" + r.overNodes],
  ["gross_exposure carries a dollar value", /^\-?\$/.test(r.grossText), r.grossText],
  ["covariance carries a run count", /ran \d+×/.test(r.covText), r.covText],
  ["breaches carries its note", r.breachesText === "1 of 9", r.breachesText],
  ["filter keeps edges inside the kept set", r.subOk, ""],
  ["a three-node fragment draws three nodes", r.fragNodes === 3, r.fragNodes],
  ["destroy empties the container", r.emptyAfterDestroy, ""],
];
let failed = 0;
for (const [name, ok, detail] of checks) { console.log((ok ? "PASS  " : "FAIL  ") + name + (ok ? "" : "  -- " + detail)); if (!ok) failed++; }
console.log(failed ? failed + " failed" : "all " + checks.length + " passed");
process.exit(failed ? 1 : 0);
MJS
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-graph.mjs http://localhost:8137; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: the script throws inside `page.evaluate` — `TypeError: Cannot read properties of undefined (reading 'render')` — because the page has no `window.OhCamelGraph`.

- [ ] **Step 3: Minimal implementation, part (a) — `web/format.js` and the first half of `web/graph.js`**

`web/format.js`, the three bodies copied character for character from the head of `web/dashboard.js`'s IIFE (`function money(x) {` … `function el(tag, cls, text) {` … `}`):

```js
// web/format.js -- the three formatters every script on the page shares.
// The bodies are dashboard.js's, copied verbatim; dashboard.js keeps its own
// private copies until the reconcile pass removes them.
(function () {
  "use strict";
  function money(x) {
    if (x === null || x === undefined) return null;
    var s = Math.abs(Math.round(x)).toLocaleString("en-US");
    return (x < 0 ? "-$" : "$") + s;
  }
  function pct(x, dp) {
    if (x === null || x === undefined) return null;
    return (x * 100).toFixed(dp === undefined ? 2 : dp) + "%";
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  window.OhCamelFormat = { money: money, pct: pct, el: el };
})();
```

`web/graph.js`, first half — create the file with this:

```js
// web/graph.js -- the Incremental graph, drawn from what Incremental holds.
//
// Everything here is taken from /api/graph: the nodes, the edges, the ranks,
// the readers outside. The client lays out rows and strokes; it never decides
// what depends on what and it never recomputes a number. The values under the
// nodes come from a frame's by_node, the closures from the served edges, and
// the lit set from the frame's recomputed -- the same hook the tests pin.
(function () {
  "use strict";
  var F = window.OhCamelFormat || {};
  var money = F.money, pct = F.pct, el = F.el;
  var NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, cls) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, attrs[k]);
    if (cls) e.setAttribute("class", cls);
    return e;
  }
  function text(x, y, s, cls) { var t = svgEl("text", { x: x, y: y }, cls); t.textContent = s; return t; }

  // ---- geometry: Task 11's rules, with a second line under every node ----
  var COL0 = 320, COL = 150, LINE = 24, GAP = 22, LEFT = 24, TOP = 34, CHAR = 6.6, OBS_GAP = 40, OBS_W = 240;
  var OPTION_SINGLETONS = ["gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"];
  // graph.ml's own sentences, for the five nodes whose comments are the argument.
  var SENTENCES = {
    "covariance": "hangs off aligned_returns and nothing else: a tick cannot reach it, only a new return can",
    "attribution": "reads the equal-weighted matrix, fixed at construction; which one is on the wire as attribution_covariance",
    "current_drawdown": "a Float.equal cutoff: at a new peak the value is 0.0 again, so limit:dd-cap is spared the recompute",
    "feed_health": "disconnected on purpose: nothing in the risk chain is downstream of now",
    "valuation_days": "the second clock, in days; wiring the Greeks to now would put the options book on a five-second timer"
  };
  // Column heads: the stage most of a column's symbol and spine nodes belong
  // to. Limit and feed nodes sit in their own bands and do not vote; a column
  // holding only limits is headed "limits". Ties go to the deeper stage.
  var STAGES = [
    ["breaches", ["breaches"]],
    ["attribution", ["attribution", "component_var_map", "component_var_sector_map", "diversification_ratio"]],
    ["estimators", ["aligned_returns", "covariance", "covariance_ewma", "portfolio_returns", "historical_var", "expected_shortfall", "parametric_var", "parametric_var_ewma", "var_notional", "es_notional", "portfolio_beta", "current_drawdown"]],
    ["aggregates", ["exposure_map", "sector:", "sector_map", "gross_exposure", "net_exposure", "weights", "equity", "gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"]],
    ["exposure", ["exposure:", "option_exposure:", "greeks:"]]
  ];
  var SCALAR = { usd: 1, fraction: 1, ratio: 1, count: 1, price: 1, qty: 1, time: 1 };
  function isFeed(n) { return n.name === "now" || n.name === "feed_health" || n.name.indexOf("feed:") === 0 || n.name.indexOf("last_tick[") === 0; }
  function stageOf(n) {
    if (n.family === "input") return "inputs";
    for (var i = 0; i < STAGES.length; i++) for (var j = 0; j < STAGES[i][1].length; j++) {
      var k = STAGES[i][1][j];
      if (k.slice(-1) === ":" ? n.name.indexOf(k) === 0 : n.name === k) return STAGES[i][0];
    }
    return "spine";
  }
  function familyKey(name) {
    var i = name.indexOf("["); if (i > 0) return name.slice(0, i) + "[S]";
    i = name.indexOf(":"); return i > 0 ? name.slice(0, i) + ":S" : name;
  }
  function colX(rank) { return rank === 0 ? LEFT : LEFT + COL0 + (rank - 1) * COL; }
  function unitFormat(unit, v) {
    if (v === null || v === undefined) return "—";
    switch (unit) {
      case "usd": return money(v);
      case "fraction": return pct(v);
      case "ratio": return v.toFixed(2) + "×";
      case "price": return v.toFixed(2);
      case "qty": case "count": return Math.round(v).toLocaleString("en-US");
      case "time": return v.toFixed(1) + " d";
      default: return String(v);
    }
  }

  // ---- the contract's pure functions: closures and fragments ----
  function adjacency(topo, dir) {
    var m = {};
    topo.edges.forEach(function (e) {
      var from = dir === "down" ? e[0] : e[1], to = dir === "down" ? e[1] : e[0];
      (m[from] = m[from] || []).push(to);
    });
    return m;
  }
  // Strictly reachable from the seeds; the seeds themselves are not in the set
  // unless another seed reaches them -- the same rule as Graph.Topology.closure,
  // so the OCaml test and this agree on what "downstream of price[S]" is.
  function closure(topo, names, dir) {
    var next = adjacency(topo, dir), seen = new Set(), stack = names.slice();
    while (stack.length) {
      var n = stack.pop();
      (next[n] || []).forEach(function (m) { if (!seen.has(m)) { seen.add(m); stack.push(m); } });
    }
    return seen;
  }
  function filter(topo, spec) {
    spec = spec || {};
    var keep = new Set();
    topo.nodes.forEach(function (n) {
      if ((spec.nodes && spec.nodes.indexOf(n.name) >= 0) || (spec.families && spec.families.indexOf(n.family) >= 0)) keep.add(n.name);
    });
    return {
      nodes: topo.nodes.filter(function (n) { return keep.has(n.name); }),
      edges: topo.edges.filter(function (e) { return keep.has(e[0]) && keep.has(e[1]); }),
      outside: topo.outside, attribution_covariance: topo.attribution_covariance, counts: topo.counts
    };
  }

  // ---- layout ----
  // Bands in page order: symbol rows (or, compact, one pseudo-node per
  // per-symbol family plus the open symbol's row), sector rows, the spine
  // (singletons stacked per column), one row per limit, and the feed branch
  // under a gap. Column 0 of a symbol row lays its cells side by side.
  function layout(topo, compact, open) {
    var collapsed = topo.counts && topo.counts.options === 0 ? OPTION_SINGLETONS : [];
    var byName = {}, symbols = [], sectors = [], limits = [];
    topo.nodes.forEach(function (n) {
      byName[n.name] = n;
      if (n.symbol && symbols.indexOf(n.symbol) < 0) symbols.push(n.symbol);
      if (n.sector && sectors.indexOf(n.sector) < 0) sectors.push(n.sector);
      if (n.limit) limits.push(n.name);
    });
    symbols.sort(); sectors.sort();
    var drawn = [], alias = {}, bands = {};
    topo.nodes.forEach(function (n) {
      if (collapsed.indexOf(n.name) >= 0) { alias[n.name] = null; return; }
      if (compact && n.symbol && n.symbol !== open) {
        var key = familyKey(n.name);
        if (!bands[key]) { bands[key] = { name: key, family: n.family, rank: n.rank, unit: "state", observed: false, band: true, members: [], feed: isFeed(n) }; drawn.push(bands[key]); }
        bands[key].members.push(n.name); alias[n.name] = key; return;
      }
      alias[n.name] = n.name; drawn.push(n);
    });
    drawn.forEach(function (n) { n.label = n.band ? n.name + " × " + n.members.length : n.name; });
    // The first absence: a slot beside covariance_ewma with no edge into it.
    if (byName["covariance_ewma"]) drawn.push({ name: "covariance_ewma~garch", label: "garch — implemented, not wired in", rank: byName["covariance_ewma"].rank, absent: true, unit: "state", family: "singleton" });
    var edges = [], seenEdge = {};
    topo.edges.forEach(function (e) {
      var a = alias[e[0]], b = alias[e[1]];
      if (!a || !b || a === b) return;
      var k = a + "→" + b;
      if (!seenEdge[k]) { seenEdge[k] = true; edges.push([a, b]); }
    });
    var maxRank = 0; drawn.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });
    function row() { var r = []; for (var i = 0; i <= maxRank; i++) r.push([]); return r; }
    var rowSyms = compact ? (open ? [open] : []) : symbols;
    var B = { symbols: rowSyms.map(row), bands: [row()], sectors: sectors.map(row), spine: [row()], limits: limits.map(row), feed: rowSyms.map(row).concat([row()]) };
    drawn.forEach(function (n) {
      var t;
      if (n.band) t = n.feed ? B.feed[rowSyms.length] : B.bands[0];
      else if (isFeed(n)) t = n.symbol ? B.feed[rowSyms.indexOf(n.symbol)] : B.feed[rowSyms.length];
      else if (n.limit) t = B.limits[limits.indexOf(n.name)];
      else if (n.symbol) t = B.symbols[rowSyms.indexOf(n.symbol)];
      else if (n.sector) t = B.sectors[sectors.indexOf(n.sector)];
      else t = B.spine[0];
      t[n.rank].push(n);
    });
    var sections = [["symbols", B.symbols], ["bands", B.bands], ["sectors", B.sectors], ["spine", B.spine], ["limits", B.limits], ["feed", B.feed]];
    var pos = {}, y = TOP, tops = {};
    sections.forEach(function (s) {
      var name = s[0], any = false, side0 = name === "symbols" || name === "bands";
      s[1].forEach(function (r) {
        var h = 0;
        r.forEach(function (col, rank) {
          col.sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; });
          h = Math.max(h, rank === 0 && side0 ? (col.length ? 1 : 0) : col.length);
        });
        if (!h) return;
        if (!any) { tops[name] = y; any = true; }
        r.forEach(function (col, rank) {
          col.forEach(function (n, i) {
            var side = rank === 0 && side0;
            pos[n.name] = { x: colX(rank) + (side ? i * 104 : 0), y: y + (side ? 0 : i * LINE), w: n.label.length * CHAR };
          });
        });
        y += h * LINE + 4;
      });
      if (any) y += GAP;
    });
    return { drawn: drawn, edges: edges, pos: pos, height: y, maxRank: maxRank, symbols: symbols, limits: limits, tops: tops, alias: alias, byName: byName, collapsed: collapsed };
  }
```

- [ ] **Step 3: Minimal implementation, part (b) — the second half of `web/graph.js`, the dune line, the marker test**

Append to `web/graph.js` (the file continues inside the same IIFE; this block ends it):

```js
  // ---- render ----
  function render(container, topology, opts) {
    opts = opts || {};
    var topo = opts.filter ? filter(topology, opts.filter) : topology;
    var byName = {}, seenSym = {}, symbolCount = 0;
    topo.nodes.forEach(function (n) { byName[n.name] = n; if (n.symbol && !seenSym[n.symbol]) { seenSym[n.symbol] = 1; symbolCount++; } });
    // Task 11's decision is this one line: READABLE keeps `symbolCount > 12`;
    // HAIRBALL makes the default `true`.
    var compact = opts.compact === undefined ? symbolCount > 12 : !!opts.compact;
    var inspector = !!opts.inspector;
    var st = { runs: {}, values: null, notes: {}, lit: [], litCount: 0, stale: [], staleSet: new Set(), open: null, L: null, svg: null, cap: null, insp: null };
    var maxRank = 0; topo.nodes.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });

    function nodeGroup(name) { return st.svg ? st.svg.querySelector('g.node[data-name="' + name + '"]') : null; }
    // A value under a scalar cell or singleton; a run count under everything
    // else. Per-symbol, per-sector and per-limit nodes carry only the count --
    // the ledger has their numbers, and one place per number is the rule.
    function valueText(n) {
      if (n.absent) return "";
      if (st.notes[n.name] !== undefined) return st.notes[n.name];
      if (n.band) { var s = 0; n.members.forEach(function (m) { s += st.runs[m] || 0; }); return "ran " + s + "×"; }
      if (SCALAR[n.unit] && (n.family === "input" || n.family === "singleton")) return st.values ? unitFormat(n.unit, st.values[n.name]) : "—";
      return "ran " + (st.runs[n.name] || 0) + "×";
    }
    function captionText() { var c = topo.counts || {}; return "The Incremental graph, taken from Incremental. " + c.named + " named nodes, " + c.observed + " observed. This frame: " + st.litCount + " ran."; }
    function applyValues() {
      if (!st.svg) return;
      st.L.drawn.forEach(function (n) { var g = nodeGroup(n.name); if (!g) return; var v = g.querySelector("text.val"); if (v) v.textContent = valueText(n); });
    }
    function drawnSet(names) { var s = new Set(); names.forEach(function (m) { var a = st.L.alias[m]; if (a) s.add(a); }); return s; }
    function applyLit() {
      if (!st.svg) return;
      var lit = drawnSet(st.lit);
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node, path.edge"), function (e) { e.classList.remove("lit"); });
      void st.svg.getBoundingClientRect(); // restart the 0.75 s fade for a node lit on consecutive frames
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) { if (lit.has(g.getAttribute("data-name"))) g.classList.add("lit"); });
      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) { if (lit.has(p.getAttribute("data-to"))) p.classList.add("lit"); });
      if (st.cap) st.cap.querySelector(".cap-title").textContent = captionText();
    }
    function applyStale() {
      if (!st.svg) return;
      var stale = drawnSet(Array.from(st.staleSet)), over = new Set();
      st.stale.forEach(function (s) { var a = st.L.alias["feed:" + s]; if (a) over.add(a); });
      if (st.stale.length && st.L.alias["feed_health"]) over.add("feed_health");
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) { var name = g.getAttribute("data-name"); g.classList.toggle("stale", stale.has(name)); g.classList.toggle("over", over.has(name)); });
    }
    function inspectorLine(n) {
      var ins = topo.edges.filter(function (e) { return e[1] === n.name; }).map(function (e) { return e[0]; });
      var outs = topo.edges.filter(function (e) { return e[0] === n.name; }).map(function (e) { return e[1]; });
      var parts = [n.name, n.family.replace("_", " "), n.observed ? "observed" : "not observed", "cutoff " + n.cutoff,
        "inputs: " + (ins.length ? ins.join(", ") : "none"), "outputs: " + (outs.length ? outs.join(", ") : "none"),
        "upstream " + closure(topo, [n.name], "up").size + " / downstream " + closure(topo, [n.name], "down").size,
        "ran " + (st.runs[n.name] || 0) + "× since you opened this page"];
      if (SENTENCES[n.name]) parts.push(SENTENCES[n.name]);
      return parts.join(" · ");
    }
    function stroke(n, on) {
      if (!st.svg) return;
      var up = on ? drawnSet(Array.from(closure(topo, [n.name], "up")).concat([n.name])) : new Set();
      var down = on ? drawnSet(Array.from(closure(topo, [n.name], "down")).concat([n.name])) : new Set();
      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) {
        p.classList.toggle("up", on && up.has(p.getAttribute("data-to")));
        p.classList.toggle("down", on && down.has(p.getAttribute("data-from")));
      });
    }
    function drawList() {
      var ol = el("ol", "graph-list");
      for (var r = 0; r <= maxRank; r++) {
        var names = topo.nodes.filter(function (n) { return n.rank === r; }).map(function (n) { return n.name; });
        var li = el("li", null, "rank " + r + " · ");
        names.forEach(function (name, i) { li.appendChild(el(st.lit.indexOf(name) >= 0 ? "b" : "span", null, name)); if (i < names.length - 1) li.appendChild(document.createTextNode(", ")); });
        ol.appendChild(li);
      }
      container.appendChild(ol); st.svg = null;
    }
    function edgePath(a, b) { var x1 = a.x + a.w + 8, y1 = a.y - 4, x2 = b.x - 4, y2 = b.y - 4, mx = (x1 + x2) / 2; return "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2; }
    function draw() {
      container.textContent = "";
      container.classList.add("ohcamel-graph");
      if (container.clientWidth && container.clientWidth < 700) { drawList(); return; }
      var L = st.L = layout(topo, compact, st.open);
      var obsX = colX(L.maxRank) + COL + OBS_GAP, width = obsX + OBS_W, height = L.height + (L.collapsed.length ? 18 : 0);
      var svg = st.svg = svgEl("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "the dependency graph" });
      var prev = null, order = ["inputs"].concat(STAGES.map(function (s) { return s[0]; })).concat(["spine"]);
      for (var r = 0; r <= L.maxRank; r++) {
        var votes = {}, best = null;
        L.drawn.forEach(function (n) { if (n.rank !== r || n.absent || n.limit || isFeed(n) || (n.band && n.feed)) return; var s = stageOf(n); votes[s] = (votes[s] || 0) + 1; });
        order.forEach(function (s) { if (votes[s] && (!best || votes[s] > votes[best])) best = s; });
        if (!best) best = L.drawn.some(function (n) { return n.rank === r && n.limit; }) ? "limits" : "";
        svg.appendChild(text(colX(r), 14, best === prev ? "·" : best, "head"));
        prev = best;
      }
      if (L.tops.limits !== undefined) svg.appendChild(text(LEFT, L.tops.limits - 8, "limits — one row each, at the rank of what it reads", "head"));
      if (L.tops.feed !== undefined) svg.appendChild(text(LEFT, L.tops.feed - 8, "deliberately disconnected", "head"));
      L.edges.forEach(function (e) {
        var a = L.pos[e[0]], b = L.pos[e[1]]; if (!a || !b) return;
        svg.appendChild(svgEl("path", { d: edgePath(a, b), "data-from": e[0], "data-to": e[1] }, "edge"));
      });
      L.drawn.forEach(function (n) {
        var p = L.pos[n.name];
        if (n.absent) {
          var ga = svgEl("g", { transform: "translate(" + p.x + "," + p.y + ")" }, "absent");
          ga.appendChild(svgEl("rect", { x: -4, y: -11, width: p.w + 8, height: 15, rx: 2 }, "slot"));
          var a = svgEl("a", { href: "#s05" }); a.appendChild(text(0, 0, n.label, "name")); ga.appendChild(a);
          svg.appendChild(ga); return;
        }
        var g = svgEl("g", { "data-name": n.name, "data-family": n.family, transform: "translate(" + p.x + "," + p.y + ")" }, "node" + (n.band ? " band" : ""));
        if (n.family === "input" && !n.band) g.appendChild(svgEl("rect", { x: -13, y: -10, width: 8, height: 8 }, "cell"));
        g.appendChild(text(0, 0, n.label, "name"));
        g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: p.w, y2: 3 }, "rule"));
        if (n.observed) g.appendChild(svgEl("circle", { cx: p.w + 5, cy: -3, r: 2.5 }, "obs"));
        g.appendChild(text(0, 13, valueText(n), "val"));
        if (inspector && !n.band) {
          g.addEventListener("mouseenter", function () { if (st.insp) st.insp.textContent = inspectorLine(n); stroke(n, true); });
          g.addEventListener("mouseleave", function () { if (st.insp) st.insp.textContent = ""; stroke(n, false); });
        }
        svg.appendChild(g);
      });
      if (L.collapsed.length) svg.appendChild(text(LEFT, height - 6, "greeks · option_exposure · gamma_map · vega_map · portfolio_gamma · portfolio_vega · vega_by_bucket — no options in this book; the five singletons exist and ran once", "head collapsed"));
      // The readers outside the graph, from the served list; the kill switch's
      // dotted edge to a bar is the second absence.
      var go = svgEl("g", {}, "observers"), oy = TOP, entries = {};
      go.appendChild(svgEl("line", { x1: obsX - 20, y1: 6, x2: obsX - 20, y2: height }, "rule"));
      go.appendChild(text(obsX, 14, "observers — outside the graph", "head"));
      (topo.outside || []).forEach(function (o) {
        if (o.name === "kill_switch") return;
        var label = o.name === "history" ? "history · reads " + o.reads.length + " · 500 points"
          : o.name === "stream" ? "stream · reads " + o.reads.length
          : "alerts · reads " + o.reads.join(", ") + (o.present ? "" : " · not attached");
        var g = svgEl("g", { "data-outside": o.name, transform: "translate(" + obsX + "," + oy + ")" }, "outside" + (o.present ? "" : " missing"));
        g.appendChild(text(0, 0, label, "name"));
        g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: label.length * CHAR, y2: 3 }, "rule"));
        go.appendChild(g); entries[o.name] = oy;
        if (o.name === "alerts") o.reads.forEach(function (r) { var a = L.pos[L.alias[r]]; if (a) go.appendChild(svgEl("path", { d: edgePath(a, { x: obsX, y: oy }), "data-from": r, "data-to": "alerts" }, "reads")); });
        oy += LINE;
      });
      var ks = (topo.outside || []).filter(function (o) { return o.name === "kill_switch"; })[0];
      if (ks) {
        var ky = oy + 4, gk = svgEl("g", {}, "absent kill");
        gk.appendChild(svgEl("path", { d: "M" + (obsX + 20) + "," + ((entries.alerts !== undefined ? entries.alerts : oy - LINE) + 6) + " L" + (obsX + 20) + "," + ky }, "dotted"));
        gk.appendChild(svgEl("line", { x1: obsX + 12, y1: ky, x2: obsX + 28, y2: ky }, "bar"));
        gk.appendChild(text(obsX + 34, ky + 4, "kill switch — wired to " + (ks.wired_to === null ? "nothing" : ks.wired_to), "name"));
        go.appendChild(gk);
      }
      svg.appendChild(go);
      container.appendChild(svg);
      if (inspector) {
        st.insp = el("div", "inspector", ""); container.appendChild(st.insp);
        st.cap = el("figcaption", "graph-cap");
        st.cap.appendChild(el("span", "cap-title", captionText()));
        st.cap.appendChild(el("span", "cap-prov", "LIVE · THIS HOST"));
        container.appendChild(st.cap);
        container.appendChild(el("div", "graph-note", "nodes recomputed (footer) is Incremental's process-wide count and includes watch nodes, plumbing, every stress fork and the startup probe; the number above is named node bodies, from the hook the tests pin."));
        if (compact) container.appendChild(el("div", "graph-note", "per-symbol families are drawn as one band each (" + L.symbols.length + " names); the name that ticked opens its own row"));
      }
      applyLit(); applyStale();
    }
    function light(names) {
      var lit = [], symbolsLit = [];
      (names || []).forEach(function (x) {
        var name = typeof x === "string" ? x : x.name, n = typeof x === "string" ? 1 : (x.n || 1);
        lit.push(name); st.runs[name] = (st.runs[name] || 0) + n;
        var node = byName[name]; if (node && node.symbol && symbolsLit.indexOf(node.symbol) < 0) symbolsLit.push(node.symbol);
      });
      st.lit = lit; st.litCount = lit.length;
      if (compact) { var want = symbolsLit.length ? symbolsLit[0] : null; if (want !== st.open) { st.open = want; draw(); applyValues(); return; } }
      if (!st.svg) { draw(); return; }
      applyLit(); applyValues();
    }
    function dim(staleSymbols) {
      var set = new Set();
      (staleSymbols || []).forEach(function (s) { var cell = "price[" + s + "]"; if (byName[cell]) { set.add(cell); closure(topo, [cell], "down").forEach(function (m) { set.add(m); }); } });
      st.stale = staleSymbols || []; st.staleSet = set;
      applyStale();
      return set;
    }
    function setValues(byNode) { st.values = byNode || {}; applyValues(); }
    function setNote(name, t) { st.notes[name] = t; applyValues(); }
    function destroy() { container.textContent = ""; container.classList.remove("ohcamel-graph"); st.svg = null; st.cap = null; st.insp = null; }
    draw();
    return { light: light, dim: dim, setValues: setValues, setNote: setNote, destroy: destroy, redraw: draw };
  }

  window.OhCamelGraph = { render: render, filter: filter, closure: closure };
})();
```

**`lib/dune`.** The line `(cat ../web/dashboard.js)` in the `dashboard_html.ml` rule becomes:

```
    (cat ../web/format.js ../web/graph.js ../web/dashboard.js)
```

(Phase 5's Task 10(e) later makes it `(cat ../web/format.js ../web/graph.js ../web/charts.js ../web/dashboard.js ../web/argument.js)` and says it expects exactly this intermediate form.) Run `make fmt` afterwards; `dune fmt` re-indents the rule.

**`test/test_embedded_assets.ml`.** Before `let suite =`, add:

```ocaml
(* Script order is load order. graph.js reads window.OhCamelFormat at load
   and dashboard.js will call window.OhCamelGraph on its first frame, so the
   three must be in the page in this order, and a rule that catted them
   differently would fail here rather than in a browser console. *)
let test_the_page_scripts_are_in_order () =
  markers_in_order Dashboard_html.page ~name:"dashboard"
    ~markers:[ "window.OhCamelFormat ="; "window.OhCamelGraph ="; "new EventSource(" ]
```

and register it: `Alcotest.test_case "format.js, graph.js, dashboard.js, in that order" `Quick test_the_page_scripts_are_in_order;`.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
node --check web/format.js && node --check web/graph.js && echo SYNTAX-OK
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-graph.mjs http://localhost:8137; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `SYNTAX-OK`; the OCaml suite green (Phase 1's byte-identity test of the page is already gone by Phase 5's account — if a test still pins the page's bytes, it is the test that changes, with the new size); then sixteen `PASS` lines and `all 16 passed`. A `FAIL  edge count` means an edge touches a name the layout dropped — print `topo.edges.filter(e => !alias[e[0]] || !alias[e[1]])` in the page console before touching the count.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/format.js web/graph.js lib/dune test/test_embedded_assets.ml
git commit -m "web: the graph drawn from the graph, because a hand-drawn diagram of a dependency structure is a diagram that can lie -- prototype <READABLE|HAIRBALL>: <the stats line from Task 11>"
```

---

### Task 13: The page wires the drawing in — Figure 1 above the ledger, the frame lit on both, staleness by closure on both, the ledger's `risk/money` column, the `quiet by design` label, the `options DISABLED` line, and the new header

`dashboard.js` learns to fetch `/api/graph` once, render Figure 1 into the new `<figure id="graph">`, and on every frame light the same names on the drawing that it underlines in the ledger; to dim, on both, exactly the downstream closure of each stale price; to take `risk_share` and `risk_over_money` from the wire rather than dividing; to label the demo's quiet name; and to say `options DISABLED — no options-chain source` on the live host. The header becomes the spec's: mode word, feed, last print, last frame, `N of M` named nodes, book, alerts, as-of, one link to `/ops`; `nodes recomputed` moves to the footer. Every edit is by content, and Phase 5's two anchor lines in this file (`try { render(JSON.parse(e.data)); }` and `.then(function (h) { renderHistory(h); })`) are not touched.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/index.html` — the `<header>` block, one `<figure>` inserted between `<div id="warn"></div>` and `<main id="main">`, one span in `<footer>`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/page.css` — two stale rules removed, one block appended
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/dashboard.js` — `riskCell`, `row`, `renderPositions`, `renderBook`, `renderLimits`, `renderHealth`, `render`, plus a new block at the head of the IIFE
- Test: `/tmp/ohcamel-phase4/pw/check-page.mjs` (Playwright, against a local `ohcamel demo`)

**Interfaces:**
- Consumes: `window.OhCamelGraph.{render, closure}` and the handle's `light`, `dim`, `setValues`, `setNote` (Task 12); the frame's `recomputed`, `by_node`, `quiet`, `positions[].{risk_share, risk_over_money}`, `sectors[].risk_share`, `feed.symbols[].last_tick` (Tasks 7–8 and existing); `GET /api/graph` (Task 9); `GET /api/ops`'s `mode`, `feed_source.kind`, `feed.staleness_threshold_s` (Phase 2 Task 11), fetched once at load; `web/page.css`'s tokens and `.q.moved`, `tr.rowstale`, `.lim` classes (Phase 1, verbatim from the old literal); the `#feed #nsym #nodes #ks #asof #halt #warn #main #pos #sectors #book #limits #conn #alertstat` ids `dashboard.js` already uses.
- Produces: the DOM contract Phase 5's `index.html` edits sit under (`nothing above </main> changes — Phase 4 owns the header, Figure 1 and the ledger`): `header > h1 > span#mode`, `#feed`, `#lastprint`, `#lastframe`, `#ran`, `#nsym`, `#ks`, `#asof`, `a#opslink[href="/ops"]`; `figure#graph.fig > span.lbl + div#graphbox` (the renderer's container); `footer span#nodes`; ledger classes `td.risk > span.rm`, `tr.quiet > td.k > em.quietlbl`, `tr#optoff`, `.lim.stale`; `tr.rowstale` now set by closure. Header wordings: `demo · synthetic feed` / `live · Alpaca + FRED`; `parked` beside a last-frame age over 60 s.

- [ ] **Step 1: Write the failing test**

```bash
cat > /tmp/ohcamel-phase4/pw/check-page.mjs <<'MJS'
// The served page, with Figure 1 wired to the stream. Usage: node check-page.mjs [http://localhost:8137]
import { chromium } from "playwright";
const base = process.argv[2] || "http://localhost:8137";
const topo = await (await fetch(base + "/api/graph")).json();
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1400, height: 1200 } });
await page.goto(base + "/");
await page.waitForSelector("#graphbox svg g.node", { timeout: 10000 });
await page.waitForSelector("#graphbox g.node.lit", { timeout: 5000 });          // a frame arrives every 400 ms
const early = await page.evaluate(() => ({
  nodes: document.querySelectorAll("#graphbox g.node").length,
  mode: document.getElementById("mode").textContent,
  ran: document.getElementById("ran").textContent,
  lastframe: document.getElementById("lastframe").textContent,
  lastprint: document.getElementById("lastprint").textContent,
  caption: document.querySelector("#graphbox figcaption.graph-cap").textContent,
  ops: (document.getElementById("opslink") || {}).getAttribute && document.getElementById("opslink").getAttribute("href"),
  rm: Array.from(document.querySelectorAll("#pos td.risk .rm")).map(e => e.textContent),
  quiet: Array.from(document.querySelectorAll("#pos tr.quiet em.quietlbl")).map(e => e.textContent),
  optoff: document.getElementById("optoff") !== null,
  footerNodes: (document.querySelector("footer #nodes") || {}).textContent,
  litLedger: document.querySelectorAll("#book td.moved, #pos td.moved").length >= 0,
}));
// The demo's quiet name goes stale at its 20 s threshold; wait for the closure to dim.
await page.waitForSelector("#graphbox g.node.stale", { timeout: 40000 });
const late = await page.evaluate((topo) => {
  const G = window.OhCamelGraph;
  const quiet = Array.from(document.querySelectorAll("#pos tr.quiet td.k")).map(td => td.firstChild.textContent)[0];
  const closure = G.closure(topo, ["price[" + quiet + "]"], "down");
  return {
    quiet,
    staleNodes: document.querySelectorAll("#graphbox g.node.stale").length,
    closurePlusCell: closure.size + 1,
    overNodes: document.querySelectorAll("#graphbox g.node.over").length,
    staleRows: document.querySelectorAll("#pos tr.rowstale, #sectors tr.rowstale, #book tr.rowstale").length,
    staleLims: document.querySelectorAll("#limits .lim.stale").length,
    lims: document.querySelectorAll("#limits .lim").length,
    warn: document.getElementById("warn").className,
  };
}, topo);
await b.close();
const checks = [
  ["figure 1 draws every non-collapsed node", early.nodes === topo.nodes.length - 5, early.nodes],
  ["mode word", early.mode === "demo · synthetic feed", early.mode],
  ["N of M named nodes", /^\d+ of 53$/.test(early.ran), early.ran],
  ["last frame is a client age", /^\d+(\.\d)? s$/.test(early.lastframe), early.lastframe],
  ["last print is a client age", /^\d+(\.\d)? s$/.test(early.lastprint), early.lastprint],
  ["caption counts from the topology", /53 named nodes, 28 observed/.test(early.caption), early.caption],
  ["one link, ops", early.ops === "/ops", early.ops],
  ["risk/money on every position row", early.rm.length === 6 && early.rm.every(t => /×$|^--$/.test(t)), early.rm.join(" ")],
  ["the quiet name is labelled", early.quiet.length === 1 && early.quiet[0] === "quiet by design", early.quiet.join()],
  ["no options-DISABLED line on the demo", early.optoff === false, early.optoff],
  ["nodes recomputed moved to the footer", /^\d[\d,]*$/.test(early.footerNodes || ""), early.footerNodes],
  ["stale: the drawing dims the closure and the cell", late.staleNodes === late.closurePlusCell, late.staleNodes + " vs " + late.closurePlusCell],
  ["stale: feed:S and feed_health in --over", late.overNodes === 2, late.overNodes],
  ["stale: some ledger rows dim", late.staleRows >= 1, late.staleRows],
  ["stale: not every limit dims (the over-dim is corrected)", late.staleLims >= 1 && late.staleLims < late.lims, late.staleLims + " of " + late.lims],
  ["the stale banner is on", late.warn === "on", late.warn],
];
let failed = 0;
for (const [name, ok, detail] of checks) { console.log((ok ? "PASS  " : "FAIL  ") + name + (ok ? "" : "  -- " + detail)); if (!ok) failed++; }
console.log(failed ? failed + " failed" : "all " + checks.length + " passed");
process.exit(failed ? 1 : 0);
MJS
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-page.mjs http://localhost:8137; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `TimeoutError: page.waitForSelector: Timeout 10000ms exceeded` waiting for `#graphbox svg g.node` — the page has no `#graphbox` and nothing calls the renderer.

- [ ] **Step 3: Minimal implementation**

**(a) `web/index.html`.** Replace the `<header>` block — from `<header>` through `</header>` (Phase 1 moved it verbatim: `<h1>OhCamel<span>reactive risk &amp; limits</span></h1>` and the five `.stat` divs `feed`, `book`, `nodes recomputed`, `alerts`, `as of`) — with:

```html
<header>
  <h1>OhCamel<span id="mode">reactive risk &amp; limits</span></h1>
  <div class="stat"><span class="lbl">feed</span><span class="v" id="feed"><span class="dot idle"></span>connecting</span></div>
  <div class="stat"><span class="lbl">last print</span><span class="v num" id="lastprint">—</span></div>
  <div class="stat"><span class="lbl">last frame</span><span class="v num" id="lastframe">—</span></div>
  <div class="stat"><span class="lbl">named nodes</span><span class="v num" id="ran">—</span></div>
  <div class="stat"><span class="lbl">book</span><span class="v num" id="nsym">—</span></div>
  <div class="stat spacer"><span class="lbl">alerts</span><span class="v ks off" id="ks">off</span></div>
  <div class="stat"><span class="lbl">as of</span><span class="v num" id="asof">—</span></div>
  <a class="stat" id="opslink" href="/ops">ops</a>
</header>
```

Between `<div id="warn"></div>` and `<main id="main">` insert:

```html
<figure id="graph" class="fig">
  <span class="lbl">figure 1 <i>— the graph, taken from the graph</i></span>
  <div id="graphbox"></div>
</figure>
```

In `<footer>`, after `<span>updates arrive when a value changes, not on a timer</span>`, insert:

```html
  <span>nodes recomputed <span class="num" id="nodes">—</span> <i>— process-wide: includes forks and the startup probe</i></span>
```

**(b) `web/page.css`.** Replace the two over-dimming rules —

```css
  main.stale section:nth-child(2) .num,
  main.stale section:nth-child(3) .num,
  main.stale section:nth-child(3) .detail { opacity: .38; }
  main.stale section:nth-child(2), main.stale section:nth-child(3) {
    background-image: repeating-linear-gradient(
      -45deg, transparent 0 9px, color-mix(in srgb, var(--over) 7%, transparent) 9px 10px);
  }
```

with

```css
  /* By dependency, now literally: dashboard.js takes the downstream closure of
     each stale price from /api/graph and marks exactly those rows. The hatch
     stays as the sign of "computed from old marks"; it lands on a row, never a
     column, so limit:aapl-cap keeps its authority while CVX is quiet. */
  tr.rowstale, .lim.stale {
    background-image: repeating-linear-gradient(
      -45deg, transparent 0 9px, color-mix(in srgb, var(--over) 7%, transparent) 9px 10px);
  }
  .lim.stale .name, .lim.stale .pct, .lim.stale .detail { opacity: .38; }
```

and append at the end of the file:

```css
  /* ---- figure 1: the graph ---- */
  #graph { margin: 0; padding: 14px 20px 6px; border-bottom: 1px solid var(--rule); overflow-x: auto; }
  #graph > .lbl { display: block; margin-bottom: 8px; }
  .ohcamel-graph svg { display: block; max-width: none; }
  .ohcamel-graph text { font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; fill: var(--ink); }
  .ohcamel-graph text.head { font-size: 10px; letter-spacing: .14em; text-transform: uppercase; fill: var(--ink-faint); }
  .ohcamel-graph text.val { font-size: 8.5px; fill: var(--ink-soft); font-variant-numeric: tabular-nums; }
  .ohcamel-graph line.rule { stroke: var(--rule); stroke-width: .75; }
  .ohcamel-graph path.edge, .ohcamel-graph path.reads { stroke: var(--rule); stroke-width: .75; fill: none; }
  .ohcamel-graph path.edge.up { stroke: var(--ink); }
  .ohcamel-graph path.edge.down { stroke: var(--ink-soft); }
  .ohcamel-graph rect.cell { fill: none; stroke: var(--ink); stroke-width: 1; }
  .ohcamel-graph circle.obs { fill: var(--ink); }
  .ohcamel-graph g.band text.name { fill: var(--ink-soft); }
  /* the mark: the same 0.75 s fade the ledger's underline uses, because it is the same fact */
  @keyframes graphmark { from { stroke: var(--mark); } to { stroke: var(--rule); } }
  @keyframes graphmarkink { from { fill: var(--mark); } to { fill: var(--ink); } }
  .ohcamel-graph g.node.lit text.name { animation: graphmarkink .75s ease-out forwards; }
  .ohcamel-graph g.node.lit line.rule { animation: graphmark .75s ease-out forwards; stroke-width: 1.5; }
  .ohcamel-graph path.edge.lit { animation: graphmark .75s ease-out forwards; }
  @media (prefers-reduced-motion: reduce) {
    .ohcamel-graph g.node.lit text.name, .ohcamel-graph g.node.lit line.rule, .ohcamel-graph path.edge.lit { animation: none; }
    .ohcamel-graph g.node.lit text.name { fill: var(--mark); }
    .ohcamel-graph g.node.lit line.rule, .ohcamel-graph path.edge.lit { stroke: var(--mark); }
  }
  /* staleness by closure: the ledger's opacity, on exactly these nodes */
  .ohcamel-graph g.node.stale { opacity: .38; }
  .ohcamel-graph g.node.over text.name { fill: var(--over); }
  .ohcamel-graph g.node.over line.rule { stroke: var(--over); }
  /* the two absences */
  .ohcamel-graph g.absent rect.slot { fill: none; stroke: var(--ink-faint); stroke-width: .75; stroke-dasharray: 2 2; }
  .ohcamel-graph g.absent text { fill: var(--ink-faint); }
  .ohcamel-graph g.absent path.dotted { stroke: var(--ink-faint); stroke-width: .75; stroke-dasharray: 2 2; fill: none; }
  .ohcamel-graph g.absent line.bar { stroke: var(--ink); stroke-width: 1.5; }
  .ohcamel-graph g.outside.missing text { fill: var(--ink-faint); }
  .ohcamel-graph .inspector { min-height: 16px; margin-top: 8px; font-size: 11px; color: var(--ink-soft); font-family: ui-monospace, Menlo, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ohcamel-graph figcaption.graph-cap { display: flex; justify-content: space-between; gap: 20px; margin-top: 6px; font-size: 12px; }
  .ohcamel-graph .cap-prov { font-size: 10px; letter-spacing: .14em; text-transform: uppercase; color: var(--ink-faint); white-space: nowrap; }
  .ohcamel-graph .graph-note { font-size: 11px; color: var(--ink-faint); margin-top: 2px; }
  .ohcamel-graph ol.graph-list { font-family: ui-monospace, Menlo, monospace; font-size: 11px; padding-left: 20px; }
  .ohcamel-graph ol.graph-list b { color: var(--mark); }
  /* the header's two clocks and its one link */
  header .v.warm { color: var(--mark); }
  header .v.over { color: var(--over); }
  header .v.parked { color: var(--unknown); }
  header a.stat { color: var(--ink-faint); text-decoration: none; font-size: 12px; }
  /* the ledger's additions */
  td.risk .rm { color: var(--ink-faint); margin-left: 8px; }
  em.quietlbl { color: var(--unknown); }
  em.quietlbl::before { content: " · "; }
```

**(c) `web/dashboard.js`.** Seven edits, each by content.

1. After the line `  var firstFrame = true;  // do not mark everything as "moved" on load` insert:

```js

  // ---- Figure 1, and the header's two clocks ----
  // The topology is fetched once: it cannot change after construction. The
  // handle lights the same names the ledger underlines, from the same frame,
  // because it is the same fact; the stale closure dims the same rows for the
  // same reason.
  var topology = null, graph = null, ops = null, pendingFrame = null;
  var staleNodes = new Set();          // downstream closure of every stale price, by node name
  var lastFrameAt = null, lastPrintAt = null;
  var NODE_OF_ROW = {
    gross: "gross_exposure", net: "net_exposure", equity: "equity", dd: "current_drawdown",
    var: "var_notional", es: "es_notional", pvar: "parametric_var", pvarewma: "parametric_var_ewma",
    beta: "portfolio_beta", gamma: "portfolio_gamma", vega: "portfolio_vega", dr: "diversification_ratio"
  };
  function nodeOfRow(key) {
    if (NODE_OF_ROW[key]) return NODE_OF_ROW[key];
    if (key.indexOf("pos:") === 0) return "exposure:" + key.slice(4);
    if (key.indexOf("sec:") === 0) return "sector:" + key.slice(4);
    return null;
  }
  function renderGraphFrame(s) {
    pendingFrame = s;
    if (!graph) return;
    graph.setValues(s.by_node || {});
    var over = 0; s.limits.forEach(function (l) { if (l.breached) over++; });
    graph.setNote("breaches", over + " of " + (s.limits.length + s.unevaluated.length));
    graph.light(s.recomputed || []);
  }
  function loadGraph() {
    var box = document.getElementById("graphbox");
    if (!box || !window.OhCamelGraph) return;
    fetch("/api/graph").then(function (r) { return r.json(); }).then(function (t) {
      topology = t;
      graph = window.OhCamelGraph.render(box, topology, { inspector: true });
      if (pendingFrame) renderGraphFrame(pendingFrame);
    }).catch(function () { /* the figure stays empty; the ledger does not depend on it */ });
  }
  function loadOps() {
    fetch("/api/ops").then(function (r) { return r.json(); }).then(function (o) {
      ops = o;
      document.getElementById("mode").textContent =
        o.mode === "live" ? "live · Alpaca + FRED" : "demo · synthetic feed";
    }).catch(function () { /* the header keeps its default word */ });
  }
  // Time_ns.to_string_utc prints nanoseconds and a space; Date.parse wants
  // milliseconds and a T.
  function parseUtc(s) { return Date.parse(s.replace(" ", "T").replace(/(\.\d{1,3})\d*Z$/, "$1Z")); }
  function age(ms) { return ms < 60000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms / 60000) + " min"; }
  function clocks() {
    var now = Date.now();
    var lf = document.getElementById("lastframe"), lp = document.getElementById("lastprint");
    if (lastFrameAt !== null) {
      var f = now - lastFrameAt;
      lf.textContent = age(f) + (f > 60000 ? " parked" : "");
      lf.className = "v num" + (f > 60000 ? " parked" : "");
    }
    if (lastPrintAt !== null) {
      var p = now - lastPrintAt, threshold = ((ops && ops.feed && ops.feed.staleness_threshold_s) || 90) * 1000;
      lp.textContent = age(p);
      lp.className = "v num" + (p > threshold ? " over" : p > threshold / 2 ? " warm" : "");
    }
  }
  // A display clock, not a poll: it reads two timestamps and asks the server nothing.
  setInterval(clocks, 250);
  loadOps(); loadGraph();
```

2. In `row`, after `    var tr = el("tr", rowCls || null);` insert:

```js
    var node = nodeOfRow(key);
    if (node && staleNodes.has(node)) tr.classList.add("rowstale");
```

3. Replace `riskCell` in full — from its comment `  // A row's share of portfolio VaR, appended as a third cell.` through the function's closing `  }` — with:

```js
  // A row's share of portfolio VaR, and that share over its share of money.
  //
  // Both come from the encoder (risk_share, risk_over_money): invariant 2
  // applied to a division. This page used to sum the components and divide
  // here; it no longer does arithmetic on risk. A NEGATIVE share is a hedge:
  // it gets the ok colour and keeps its sign.
  function riskCell(tr, key, share, ratio) {
    var td = el("td", "risk");
    if (share === null || share === undefined) {
      td.textContent = "—";
    } else {
      var shown = (share * 100).toFixed(1) + "%";
      td.appendChild(document.createTextNode(shown));
      if (share < 0) td.classList.add("hedge");
      if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) td.classList.add("moved");
      prev[key] = shown;
    }
    if (ratio !== undefined) td.appendChild(el("span", "rm", ratio === null ? "--" : ratio.toFixed(2) + "×"));
    tr.appendChild(td);
  }
```

4. Replace `renderPositions` in full — from `  function renderPositions(s) {` through the `  }` that closes it (the one before `  function renderBook(s) {`) — with:

```js
  function renderPositions(s) {
    var quiet = s.quiet || [];
    var t = document.getElementById("pos");
    t.textContent = "";
    s.positions.forEach(function (p) {
      var tr = row(t, "pos:" + p.symbol, p.symbol, p.sector,
          money(p.exposure), p.exposure < 0 ? "neg" : null,
          staleSet[p.symbol] ? "rowstale" : null);
      // Stale on schedule is not a broken feed. The demo never ticks one name
      // so the stale path can be watched, and the frame says which one.
      if (quiet.indexOf(p.symbol) >= 0) {
        tr.classList.add("quiet");
        tr.firstChild.appendChild(el("em", "quietlbl", "quiet by design"));
      }
      riskCell(tr, "risk:" + p.symbol, p.risk_share, p.risk_over_money);
    });

    var st = document.getElementById("sectors");
    st.textContent = "";
    s.sectors.forEach(function (x) {
      // A sector total is only as good as its worst member.
      var tr = row(st, "sec:" + x.sector, x.sector, null,
          money(x.exposure), x.exposure < 0 ? "neg" : null,
          staleSectors[x.sector] ? "rowstale" : null);
      riskCell(tr, "risk:sec:" + x.sector, x.risk_share);
    });
  }
```

5. In `renderBook`, the gamma/vega block ends with

```js
      }
    }
    // Sum of standalone position volatilities over portfolio volatility, so at
```

Insert between the `    }` and the comment:

```js
    else if (ops && ops.mode === "live") {
      // Said in the place the rows would be, rather than left to be
      // discovered: the live host has no options-chain source, so the options
      // branch of the graph is built and never fed.
      row(t, "optoff", "options", "DISABLED — no options-chain source", "off", null, "gap").id = "optoff";
    }
```

6. In `renderLimits`, after `      var d = el("div", "lim" + (l.breached ? " over" : ""));` insert:

```js
      if (staleNodes.has("limit:" + l.name)) d.classList.add("stale");
```

7. In `renderHealth`, after the two lines

```js
    h.stale.concat(h.never_seen).forEach(function (sym) { staleSet[sym] = true; });
    s.positions.forEach(function (p) {
      if (staleSet[p.symbol] && p.sector) staleSectors[p.sector] = true;
    });
```

insert:

```js
    // Staleness follows the edges, not the column: the downstream closure of
    // each stale price, from the served topology, on the drawing and on the
    // ledger's rows alike. Until the topology has arrived the rows fall back
    // to the symbol match above.
    staleNodes = new Set();
    var staleSyms = h.stale.concat(h.never_seen);
    if (topology && window.OhCamelGraph) {
      staleSyms.forEach(function (sym) {
        var cell = "price[" + sym + "]";
        staleNodes.add(cell);
        window.OhCamelGraph.closure(topology, [cell], "down").forEach(function (n) { staleNodes.add(n); });
      });
    }
    if (graph) graph.dim(staleSyms);
    lastPrintAt = null;
    (h.symbols || []).forEach(function (st) {
      if (!st.last_tick) return;
      var t = parseUtc(st.last_tick);
      if (!isNaN(t) && (lastPrintAt === null || t > lastPrintAt)) lastPrintAt = t;
    });
```

8. In `render`, after `    renderLimits(s);` insert:

```js
    renderGraphFrame(s);
    lastFrameAt = Date.now();
    document.getElementById("ran").textContent =
      (s.recomputed ? s.recomputed.length : "—") + " of " + (topology ? topology.counts.named : "—");
```

The line `document.getElementById("nodes").textContent = …` stays; `#nodes` now lives in the footer. The stream hookup at the bottom of the file (`var src = new EventSource("/api/stream");` and the `try { render(JSON.parse(e.data)); }` line Phase 5 anchors on) is untouched, and so is `refreshHistory`.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
node --check web/dashboard.js && echo SYNTAX-OK
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-graph.mjs http://localhost:8137 | tail -1 && node check-page.mjs http://localhost:8137; cd /Users/ajaiupadhyaya/Documents/OhCamel
cd /tmp/ohcamel-phase4/pw && npx playwright screenshot --full-page --viewport-size=1400,1200 --wait-for-timeout=25000 http://localhost:8137/ /tmp/ohcamel-phase4/page.png; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `SYNTAX-OK`; the OCaml suite green (the markers test still finds the three scripts in order); `all 16 passed` from `check-graph.mjs` and `all 16 passed` from `check-page.mjs` — the second takes about 25 s, most of it waiting for CVX to go stale at the demo's 20 s threshold. Then READ `/tmp/ohcamel-phase4/page.png`: the header reads `OhCamel demo · synthetic feed`, Figure 1 sits above the three ledger columns with CVX's row and its downstream nodes hatched and dimmed while the other five rows and `limit:*-cap` on the other names keep full ink; the caption says `53 named nodes, 28 observed`. If `stale: not every limit dims` fails with `9 of 9`, `staleNodes` is being filled from the old symbol match rather than the closure — check that `topology` was set before the stale frame (the fetch races the first frames; the fallback is by design, the assertion is on a frame twenty seconds later).

Under 700 px the figure falls back to the list; check once by hand in a narrowed browser window — `ol.graph-list` with the lit names in `--mark`.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/index.html web/page.css web/dashboard.js
git commit -m "web: the frame lights the drawing and the ledger from one fact, and staleness follows the edges, because the column dim was the one place the page said more than the engine knew"
```

---

### Task 14: The footer — what `/api/ops` knows, polled every 30 s while the tab is visible

The spec's footer is the process's own account of itself: stream state, the two process-wide counters, frames sent and open subscribers, the history ring, alerts sent and failed, pid and hostname, GC heap and collections, RSS, and on the live host the Alpaca and FRED counters — polled from `/api/ops` every 30 s while the tab is visible, with one sentence on what the engine cannot see. Task 13 moved `nodes recomputed` into the footer and fetched `/api/ops` once for the header's mode word; this task turns that one fetch into the page's only poll and gives the footer its rows. The stream hookup and `refreshHistory` are still untouched, so Phase 5's two anchor lines in `dashboard.js` stay byte-identical, and the `<footer>` line stays at column 0 because Phase 5's Task 12 inserts the argument immediately above it.

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/index.html` — the `<footer>` block Task 13 left (four spans), replaced in full; nothing else
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/dashboard.js` — Task 13's `loadOps` replaced by a poll, one new `renderOps`, one line in `render`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/page.css` — three footer rules appended
- Test: `/tmp/ohcamel-phase4/pw/check-footer.mjs` (Playwright, against a local `ohcamel demo`)

**Interfaces:**
- Consumes: `GET /api/ops` (Phase 2 Task 11): `mode`, `pid`, `hostname`, `process.{nodes_recomputed, stabilizes}`, `stream.{frames_sent, subscribers, coalesce_ms}`, `history.{appended, points, capacity}`, `alerts.{enabled, sent, failed}`, `gc.{heap_words, major_collections}`, `rss_bytes` (`null` on macOS), `feed_source.{kind, alpaca: {frames, trades, rejected, reconnects, last_error} | null, fred: {polls, last_success} | null}` (Phase 2 Tasks 6 and 13); the frame's `stabilizes` (Task 7) and `nodes_recomputed` (existing); Task 13's `ops` variable, `loadOps`, and the footer's `#conn`, `#nodes`, `#alertstat`; Phase 2 Task 14's visibility idiom in `web/ops.js` (`visibilitychange` → start/stop), reused here so both pages poll the same way; `History_buffer.default_capacity = 500` (`lib/history_buffer.ml:91`, what the demo runs with).
- Produces: the footer DOM Phase 5's §08 prose and Phase 6's screenshot read: `footer > span#conn`, `span#nodes`, `span#stabilizes`, `span#frames`, `span#subs`, `span#coalesce`, `span#hist`, `span#alertstat`, `span#pid`, `span#host`, `span#gcheap`, `span#gcmajor`, `span#rss`, `span#feedstats` (hidden on a synthetic feed), `span.cannot`; `dashboard.js`'s `renderOps(o)`, `startOps()`, `stopOps()`; exactly one `/api/ops` request per 30 s per visible tab, none while hidden.

- [ ] **Step 1: Write the failing test**

```bash
cat > /tmp/ohcamel-phase4/pw/check-footer.mjs <<'MJS'
// The footer, filled from /api/ops on a 30 s poll. Usage: node check-footer.mjs [http://localhost:8137]
import { chromium } from "playwright";
const base = process.argv[2] || "http://localhost:8137";
const ops = await (await fetch(base + "/api/ops")).json();
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1400, height: 1200 } });
let opsRequests = 0;
page.on("request", (r) => { if (r.url().endsWith("/api/ops")) opsRequests++; });
await page.goto(base + "/");
// #pid is filled only by the poll (no frame carries it), so it is the row that proves the poll ran.
await page.waitForFunction(() => { var e = document.getElementById("pid"); return !!e && /^\d/.test(e.textContent); }, null, { timeout: 10000 });
await page.waitForTimeout(5000);
const early = opsRequests;   // the load-time poll and nothing else in the first 5 s
// The load-time poll is issued before this tab's EventSource is registered on the server, so its
// subscriber count may not include this tab; the second poll, at the 30 s mark, certainly does.
await page.waitForTimeout(27000);
await page.waitForFunction(() => Number(document.getElementById("subs").textContent) >= 1, null, { timeout: 5000 }).catch(() => {});
const f = await page.evaluate(() => {
  const t = (id) => (document.getElementById(id) || { textContent: null }).textContent;
  return {
    pid: t("pid"), host: t("host"), stab: t("stabilizes"), frames: t("frames"), subs: t("subs"),
    coalesce: t("coalesce"), hist: t("hist"), gcheap: t("gcheap"), gcmajor: t("gcmajor"), rss: t("rss"),
    feedHidden: document.getElementById("feedstats").hidden,
    cannot: (document.querySelector("footer .cannot") || {}).textContent,
    footerFirst: document.querySelector("footer").firstElementChild.textContent,
    nodes: t("nodes"), alertstat: t("alertstat"), conn: t("conn"),
  };
});
await b.close();
const checks = [
  ["pid is the process's", f.pid === String(ops.pid), f.pid + " vs " + ops.pid],
  ["hostname is the container id, not the droplet", f.host === ops.hostname, f.host],
  ["stabilizes is a count", /^\d[\d,]*$/.test(f.stab), f.stab],
  ["nodes recomputed still from the frame", /^\d[\d,]*$/.test(f.nodes), f.nodes],
  ["frames sent", /^\d[\d,]*$/.test(f.frames), f.frames],
  ["open subscribers counts this tab", /^\d+$/.test(f.subs) && Number(f.subs) >= 1, f.subs],
  ["coalesce 80 ms", f.coalesce === "80 ms", f.coalesce],
  ["history appended / points / capacity", /^\d[\d,]* appended \/ \d+ points \/ 500 capacity$/.test(f.hist), f.hist],
  ["GC heap in MB", /^\d+(\.\d)? MB$/.test(f.gcheap), f.gcheap],
  ["major collections", /^\d[\d,]*$/.test(f.gcmajor), f.gcmajor],
  ["RSS is a dash on macOS, MB on Linux", f.rss === "—" || /^\d+(\.\d)? MB$/.test(f.rss), f.rss],
  ["no Alpaca/FRED line on a synthetic feed", f.feedHidden === true, f.feedHidden],
  ["the not-observable sentence", /docker stats has them/.test(f.cannot || ""), f.cannot],
  ["first line unchanged", f.footerFirst === "updates arrive when a value changes, not on a timer", f.footerFirst],
  ["alerts sent/failed still from the frame", /alerts sent \d+|alerting disabled/.test(f.alertstat), f.alertstat],
  ["one /api/ops request in the first 5 s -- a 30 s poll, not a tick", early === 1, early],
  ["a second request by the 32 s mark -- the poll is every 30 s", opsRequests === 2, opsRequests],
];
let failed = 0;
for (const [name, ok, detail] of checks) { console.log((ok ? "PASS  " : "FAIL  ") + name + (ok ? "" : "  -- " + detail)); if (!ok) failed++; }
console.log(failed ? failed + " failed" : "all " + checks.length + " passed");
process.exit(failed ? 1 : 0);
MJS
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-footer.mjs http://localhost:8137; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `TimeoutError: page.waitForFunction: Timeout 10000ms exceeded` — there is no `#pid`; `document.getElementById("pid")` is null and the predicate is false until the timeout.

- [ ] **Step 3: Minimal implementation**

**(a) `web/index.html`.** Replace the `<footer>` block — from `<footer>` through `</footer>`, which after Task 13 holds the `updates arrive` span, the `nodes recomputed` span, `#conn` and `#alertstat` — with:

```html
<footer>
  <span>updates arrive when a value changes, not on a timer</span>
  <span id="conn">—</span>
  <span>nodes recomputed <span class="num" id="nodes">—</span> · stabilizes <span class="num" id="stabilizes">—</span> <i>— process-wide: includes forks and the startup probe</i></span>
  <span>frames sent <span class="num" id="frames">—</span> · open subscribers <span class="num" id="subs">—</span> · coalesce <span class="num" id="coalesce">—</span></span>
  <span>history <span class="num" id="hist">—</span></span>
  <span id="alertstat">—</span>
  <span>pid <span class="num" id="pid">—</span> · <span id="host">—</span> <i>— the container id, not the droplet</i></span>
  <span>GC heap <span class="num" id="gcheap">—</span> · major collections <span class="num" id="gcmajor">—</span> · RSS <span class="num" id="rss">—</span></span>
  <span id="feedstats" hidden></span>
  <span class="cannot">CPU share, Caddy and the certificate are not engine-observable; <code>docker stats</code> has them.</span>
</footer>
```

The `<footer>` line stays at column 0 and there is still exactly one: Phase 5's Task 12 locates it with `grep -n "^<footer>"`.

**(b) `web/dashboard.js`.** Replace Task 13's `loadOps` — from `  function loadOps() {` through its closing `  }` — with:

```js
  // ---- The footer: what /api/ops knows, every 30 s while the tab is visible ----
  // The only poll on this page. Everything above the footer arrives on the
  // stream because it is a graph change; a process's pid, uptime and heap are
  // not, so the page asks for them on a timer and the footer's first line says
  // so. Stopped while the tab is hidden, as /ops does, because a hundred
  // background tabs asking every 30 s is a load the engine did not sign up for.
  function opsNum(x) { return x === null || x === undefined ? "—" : Number(x).toLocaleString("en-US"); }
  function opsMb(bytes) { return bytes === null || bytes === undefined ? "—" : (bytes / 1048576).toFixed(1) + " MB"; }
  function renderOps(o) {
    var $ = function (id) { return document.getElementById(id); };
    $("mode").textContent = o.mode === "live" ? "live · Alpaca + FRED" : "demo · synthetic feed";
    var p = o.process || {}, st = o.stream || {}, h = o.history || {}, gc = o.gc || {};
    $("stabilizes").textContent = opsNum(p.stabilizes);
    $("frames").textContent = opsNum(st.frames_sent);
    $("subs").textContent = opsNum(st.subscribers);
    $("coalesce").textContent = st.coalesce_ms === undefined ? "—" : opsNum(Math.round(st.coalesce_ms)) + " ms";
    $("hist").textContent = h.capacity === undefined ? "—"
      : opsNum(h.appended) + " appended / " + opsNum(h.points) + " points / " + opsNum(h.capacity) + " capacity";
    // A pid is an identifier, not a quantity: no thousands separator, so it greps.
    $("pid").textContent = o.pid === undefined || o.pid === null ? "—" : String(o.pid);
    $("host").textContent = o.hostname || "—";
    // OCaml words are 8 bytes on every platform this runs on.
    $("gcheap").textContent = gc.heap_words === undefined ? "—" : opsMb(gc.heap_words * 8);
    $("gcmajor").textContent = opsNum(gc.major_collections);
    $("rss").textContent = opsMb(o.rss_bytes);
    var fs = o.feed_source || {}, f = $("feedstats");
    if (fs.kind === "alpaca" && fs.alpaca && fs.fred) {
      f.hidden = false;
      f.textContent = "Alpaca frames " + opsNum(fs.alpaca.frames) + " · trades " + opsNum(fs.alpaca.trades)
        + " · rejected " + opsNum(fs.alpaca.rejected) + " · reconnects " + opsNum(fs.alpaca.reconnects)
        + " · last error " + (fs.alpaca.last_error || "none")
        + " — FRED polls " + opsNum(fs.fred.polls) + " · last success " + (fs.fred.last_success || "never");
    } else {
      f.hidden = true;
    }
  }
  function loadOps() {
    fetch("/api/ops").then(function (r) { return r.json(); }).then(function (o) {
      ops = o;
      renderOps(o);
    }).catch(function () { /* the header keeps its word and the footer its dashes */ });
  }
  var opsTimer = null;
  function startOps() { if (opsTimer === null) { loadOps(); opsTimer = setInterval(loadOps, 30000); } }
  function stopOps() { if (opsTimer !== null) { clearInterval(opsTimer); opsTimer = null; } }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") startOps(); else stopOps();
  });
```

and change Task 13's last line of that block, `  loadOps(); loadGraph();`, to

```js
  if (document.visibilityState !== "hidden") startOps();
  loadGraph();
```

In `render`, immediately after the line `    document.getElementById("nodes").textContent = s.nodes_recomputed.toLocaleString("en-US");`, insert:

```js
    if (s.stabilizes !== undefined && s.stabilizes !== null)
      document.getElementById("stabilizes").textContent = s.stabilizes.toLocaleString("en-US");
```

— both counters come from the frame when a frame is there (Task 7 put `stabilizes` on it), and from the poll between frames, so the live host at night still shows them moving from the 5 s clock.

**(c) `web/page.css`.** Append:

```css
/* The footer: the process's own account of itself, one poll every 30 s. */
footer span i { font-style: italic; color: var(--ink-faint); }
footer span[hidden] { display: none; }
footer .cannot { display: block; margin-top: 4px; color: var(--ink-soft); }
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
node --check web/dashboard.js && echo SYNTAX-OK
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -6
grep -c '^<footer>' web/index.html
grep -n 'try { render(JSON.parse(e.data)); }\|\.then(function (h) { renderHistory(h); })' web/dashboard.js
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo.log 2>&1 &
echo $! > /tmp/ohcamel-demo.pid
sleep 3
cd /tmp/ohcamel-phase4/pw && node check-footer.mjs http://localhost:8137 && node check-page.mjs http://localhost:8137 | tail -1; cd /Users/ajaiupadhyaya/Documents/OhCamel
kill "$(cat /tmp/ohcamel-demo.pid)"
```

Expected: `SYNTAX-OK`; green; `1`; the two anchor lines, one hit each, unchanged; `all 17 passed` from `check-footer.mjs` — it takes about 35 s, most of it waiting for the second poll at the 30 s mark — and `all 16 passed` from `check-page.mjs` (Task 13's `nodes recomputed moved to the footer` check still holds). If `open subscribers counts this tab` fails with `0`, the second poll did not happen: check that `startOps` ran (the tab was visible at load) and that `opsTimer` is the `setInterval` handle, not `null`.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/index.html web/dashboard.js web/page.css
git commit -m "web: the footer is the process's own account of itself, because pid, heap and uptime are not graph changes and the page had nowhere to say so"
```

---

## Done when

- `Graph.topology` returns every named node and every named-to-named edge from Incremental's own table, and `test_graph.ml` pins it from nine directions: the downstream closure of `{price[AAPL], last_tick[AAPL]}` is the recorder's tick set, `returns[MSFT]`'s is the push_return set, no price reaches a covariance matrix, `limit:aapl-cap` reads `exposure:AAPL` alone, nothing risk-bearing is downstream of `now`, the count is `2|S| + |K| + |L| + 2|O| + 29`, the observed flags are the 28, ranks are monotone on every edge, and a fork's observers leave with `destroy`.
- Every pre-existing named-node recomputation set in `test_graph.ml` is unchanged, and the six credential-free modes are byte-identical to the captures taken from the commit before Task 6 (`phase4-gate/gate.sh` prints six `GATE ok` lines).
- `/api/stream` frames carry `recomputed[].{name, n}`, `stabilizes_delta` and `nodes_recomputed_delta`; `/api/snapshot` and every welcome frame carry `null` for all three; a poll between two frames steals nothing, and a render-time stabilize's follow-up frame is `recomputed: []`.
- `json_of_snapshot` carries `by_node` with a key for every scalar node in the topology, `positions[].{price, qty, marginal, standalone, risk_share, risk_over_money}`, `sectors[].risk_share`, `euler_residual`, `attribution_covariance` and `quiet`; `/api/ops.graph.named` is `{distinct, total, hottest}` on a served graph.
- `/api/graph` is memoised at `Server.create`, sits immediately before `/api/ops` in `Server.routes` and in `smoke.sh`'s `EXPECTED_ROUTES`, and every edge endpoint it serves is a node it serves; `/api/stress` has the spec's shape with `before`, structured shocks, `counter_cost` and `duration_ms`, and `Stress.Shock.to_json` lives beside `to_string`.
- `web/graph.js` exposes `window.OhCamelGraph.{render, filter, closure}` with `handle.{light, dim, setValues, destroy}` exactly as RULES.md's contract states, drawn as inline SVG with no library; `check-graph.mjs` and `check-page.mjs` print `all 16 passed` and `check-footer.mjs` prints `all 17 passed` against a local demo, and the page screenshot has been looked at.
- The footer's rows are filled from `/api/ops` on a 30 s poll that stops while the tab is hidden, `<footer>` is still one line at column 0, and `dashboard.js` still holds `try { render(JSON.parse(e.data)); }` and `.then(function (h) { renderHistory(h); })` byte for byte, for Phase 5.
- `dune build @fmt` and `dune runtest` are green, CI is green on both legs, and the working tree is clean apart from `claudecodehandoff.md`.
