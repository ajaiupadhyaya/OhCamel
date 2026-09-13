# Desk W1 — Figure 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Figure 1 shows a frame's work travelling through the graph rank by rank, distinguishes a node that ran and changed from one whose cutoff held, pulses the cell a frame set, weights edges by how often they have carried work and rules by what each node costs, bundles its edges into trunks, carries a legend and a set caption, and opens as a full-screen poster — with every fact it draws supplied by the server.

**Architecture:** The graph gains an optional per-node change hook (Incremental's `on_update`, registered only when a hook is given), and the recompute log keeps a per-frame changed set beside its ran set. The stream frame carries `changed`; the topology carries each node's `cost` class; `/api/heat` serves lifetime run counts. `web/graph.js` draws from those three facts and computes none of them.

**Tech Stack:** OCaml 5.2.1, Incremental v0.17, cohttp-async; hand-written JavaScript and inline SVG, no libraries.

**Spec:** `docs/superpowers/specs/2026-09-12-the-desk-design.md` §4 items 1–8 (on branch `desk/a1-record`; read it there with `git -C /Users/ajaiupadhyaya/Documents/OhCamel show desk/a1-record:docs/superpowers/specs/2026-09-12-the-desk-design.md`). The prototype this plan's drawing code comes from was built against a replay of the public demo's stream.

## Global Constraints

- Work in the worktree `/Users/ajaiupadhyaya/Documents/OhCamel-figure` on branch `desk/w1-figure`. It has no `_opam` of its own: every dune command runs after `eval $(opam env --switch=/Users/ajaiupadhyaya/Documents/OhCamel --set-switch)`, and the `make` targets do not work here (they look for the switch in the current directory). Use `dune build`, `dune runtest --force`, `dune build @fmt`, `dune fmt`.
- `dune build @fmt` clean before every commit (ocamlformat 0.29.0).
- `lib/verified.ml`'s `let tests = N` equals the registered case count; bump it in the same commit as any task that adds cases. Do not touch its other constants.
- Every named-node recomputation set pinned in `test/test_graph.ml` stays unchanged. The change hook creates no node and makes no node necessary.
- The client draws what the server says: no node's cost, change or heat is decided in the browser.
- No external assets: no font files, no CDN, no library. The caption's serif is a system stack: `ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif`.
- Both colour schemes, and `prefers-reduced-motion: reduce` shows state without movement.
- Commit exactly the files the task names (plus `lib/verified.ml` when the count changes). Never `git add -A`/`git add .`. Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp` or any `.env`.
- Commit subjects: lowercase area prefix, then the reason. Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Where `bin/main.ml` changes, the six credential-free modes (`synthetic stress backtest backtest-crisis options garch`) print byte-for-byte what they printed before.
- The strings `|ohcamel_html}`, `|ohcamel_csv}`, `|ohcamel_json}` and `|ohcamel_build}` never appear under `web/` or `docs/crisis/`.
- Comments explain why, in the repository's voice; every numeric assertion carries its derivation.

## File Structure

```
lib/graph.ml                 MODIFY  ?on_value_change hook; Node_name.cost_of; Topology.Node.cost
lib/recompute_log.ml         MODIFY  note_change, drain_changed, lifetime
lib/server.ml                MODIFY  frame "changed"; topology "cost"; /api/heat
bin/main.ml                  MODIFY  serve and demo pass the change hook
test/test_recompute_log.ml   MODIFY  the cutoff made visible; a fork's changes stay out
test/test_server.ml          MODIFY  the frame's changed set; the heat route; the cost field
test/test_graph.ml           MODIFY  every named node has a cost class
deploy/smoke.sh              MODIFY  EXPECTED_ROUTES gains /api/heat
web/graph.js                 MODIFY  wave, cutoff, origin, heat, cost, bundles, legend, poster
web/page.css                 MODIFY  their styles
web/dashboard.js             MODIFY  passes changed; loads heat
web/index.html               MODIFY  the figure's caption
README.md, docs/status.md    MODIFY  what the figure now shows
```

---

### Task 1: The graph says which values changed

**Files:**
- Modify: `lib/graph.ml`, `lib/recompute_log.ml`, `test/test_recompute_log.ml`, `lib/verified.ml`

**Interfaces:**
- Produces:
  - `Ohcamel.Graph.create` gains `?on_value_change:(string -> unit)`. When given, every named derived node and every input cell's watch node calls it with its name once per stabilization in which its value changed (Incremental's `Update.Changed`, i.e. after the cutoff). `Graph.fork` does not pass it on.
  - `Ohcamel.Recompute_log.note_change : t -> string -> unit`; `drain_changed : t -> string list` (each name once, sorted, the frame's changed table emptied).

- [ ] **Step 1: Write the failing tests.** Append to `test/test_recompute_log.ml`, above `let suite`:

```ocaml
(* A cutoff, made visible.

   [note] says a body ran; [note_change] says its value moved, after the
   cutoff had its say. The two differ exactly where a cutoff held, and the
   drawing's ghost is that difference. The book: one share of AAPL, no cash,
   one drawdown limit. Equity is the share's price.

   At 100 the trail is marked, so the history is [100] and the drawdown is
   (100 - 100) / 100 = 0. Moving the price to 110 makes a new peak: the
   drawdown over [100; 110] is (110 - 110) / 110 = 0 again. Its body RAN,
   because equity moved, and its value did not CHANGE, because Float.equal
   0.0 0.0 -- so limit:dd-cap, which reads only the drawdown, did not run at
   all. The same price sent again changes the cell by nothing, so the cell's
   own cutoff holds and nothing runs. *)
let test_a_cutoff_is_a_node_that_ran_and_did_not_change () =
  let log = Recompute_log.create () in
  let graph =
    Graph.create ~on_compute:(Recompute_log.note log) ~on_value_change:(Recompute_log.note_change log)
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:[ { Limit.name = "dd-cap"; scope = Limit.Portfolio; kind = Limit.Max_drawdown 0.10 } ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph aapl (Price.of_float 100.0);
      Graph.set_qty graph aapl (Qty.of_float 1.0);
      Graph.stabilize graph;
      Graph.mark_equity graph;
      Graph.stabilize graph;
      ignore (Recompute_log.drain log : (string * int) list);
      ignore (Recompute_log.drain_changed log : string list);
      Graph.set_price graph aapl (Price.of_float 110.0);
      Graph.stabilize graph;
      let ran = List.map (Recompute_log.drain log) ~f:fst and changed = Recompute_log.drain_changed log in
      let has xs x = List.mem xs x ~equal:String.equal in
      Alcotest.(check bool) "current_drawdown ran" true (has ran "current_drawdown");
      Alcotest.(check bool) "and did not change" false (has changed "current_drawdown");
      Alcotest.(check bool) "so limit:dd-cap did not run" false (has ran "limit:dd-cap");
      Alcotest.(check (list string)) "what did change, in order"
        [ "equity"; "exposure:AAPL"; "exposure_map"; "gross_exposure"; "net_exposure"; "price[AAPL]"; "sector:TECH"; "sector_map" ]
        (List.filter changed ~f:(fun n ->
             List.mem [ "equity"; "exposure:AAPL"; "exposure_map"; "gross_exposure"; "net_exposure"; "price[AAPL]"; "sector:TECH"; "sector_map" ] n
               ~equal:String.equal));
      Graph.set_price graph aapl (Price.of_float 110.0);
      Graph.stabilize graph;
      Alcotest.(check (list (pair string int))) "the same price again runs nothing" [] (Recompute_log.drain log);
      Alcotest.(check (list string)) "and changes nothing" [] (Recompute_log.drain_changed log))

let test_a_fork's_changes_never_reach_the_parent's_log () =
  let log = Recompute_log.create () in
  let graph =
    Graph.create ~on_compute:(Recompute_log.note log) ~on_value_change:(Recompute_log.note_change log)
      ~starting_cash:Synthetic_book.starting_cash ~instruments:Synthetic_book.instruments ~limits:Synthetic_book.limits
      ~confidence:Synthetic_book.confidence ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Synthetic_book.seed_returns ~rng:(Random.State.make [| 1 |]) ~graph;
      Graph.stabilize graph;
      ignore (Recompute_log.drain_changed log : string list);
      ignore (Stress.run_all ~graph ~scenarios:(Stress.suite_for ~graph) : Stress.Outcome.t list);
      Alcotest.(check (list string)) "twelve forked scenarios changed nothing the live log can see" []
        (Recompute_log.drain_changed log))
```

  Add to `suite`:

```ocaml
      Alcotest.test_case "a cutoff is a node that ran and did not change" `Quick
        test_a_cutoff_is_a_node_that_ran_and_did_not_change;
      Alcotest.test_case "A FORK'S CHANGES NEVER REACH THE PARENT'S LOG" `Quick
        test_a_fork's_changes_never_reach_the_parent's_log;
```

  Check the names this test uses against the file's existing test `test_a_fork_never_reaches_the_parents_log`, which already builds a graph from `Synthetic_book`: use exactly the constructors and seeding it uses (the names `Synthetic_book.starting_cash`, `instruments`, `limits`, `confidence`, `return_window`, `seed_returns` are what `lib/synthetic_book.ml` exports; if a name differs, follow that test). If `exposure_map` or `sector_map` is not a node name in this graph, remove it from both lists and say so in the report.

- [ ] **Step 2: Run and watch it fail.** `dune runtest --force 2>&1 | tail -20`. Expected: `The function applied to this argument has type ... on_value_change` or `Unbound value Recompute_log.note_change`.

- [ ] **Step 3: Implement in `lib/recompute_log.ml`.** Add a third table and three functions; extend the header comment with one paragraph:

```ocaml
   WHY A CHANGED TABLE TOO. A body that ran is not a value that changed: a
   cutoff can hold a recomputed value equal to the old one, and then nothing
   downstream runs because of it. The drawing shows those two apart -- gold for
   a change, a ghost for a cutoff that held -- so the log keeps the frame's
   changed names beside its ran counts. A set, not a count: Incremental reports
   a change at most once per stabilization, and a frame that spans several
   only needs to know whether it moved.
```

```ocaml
type t = {
  frame : int String.Table.t;
  lifetime : int String.Table.t;
  (* Cleared by [drain_changed]. The names whose value changed since the last frame. *)
  changed : unit String.Table.t;
}

let create () =
  { frame = String.Table.create (); lifetime = String.Table.create (); changed = String.Table.create () }

(* Called from an on_update handler, inside stabilization. Total, like [note]. *)
let note_change (t : t) (name : string) : unit = Hashtbl.set t.changed ~key:name ~data:()

let drain_changed (t : t) : string list =
  let names = Hashtbl.keys t.changed |> List.sort ~compare:String.compare in
  Hashtbl.clear t.changed;
  names
```

- [ ] **Step 4: Implement in `lib/graph.ml`.** Add `?on_value_change` to `create`'s optional arguments (after `?on_compute`). Directly above `let make_var ~name ~equal init =`, add:

```ocaml
  (* The second diagnostic hook, and the one a drawing needs to show a
     cutoff. [on_compute] says a body RAN. This says a value CHANGED, after its
     cutoff had its say: Incremental's own on_update [Changed]. A node that ran
     and did not change is a node whose cutoff held, and nothing downstream of
     it ran on its account.

     Registered only when a hook is given. An on_update handler costs little,
     but the forks stress.ml builds, the scaling probe's graphs and every test
     graph have no use for one, so they pay nothing. Like [on_compute], it runs
     inside stabilization and must only record. *)
  let watch_change (type a) (name : string) (node : a Inc.t) : unit =
    match on_value_change with
    | None -> ()
    | Some f ->
        Inc.on_update node ~f:(function
          | Inc.Update.Changed _ -> f name
          | Inc.Update.Necessary _ | Inc.Update.Invalidated | Inc.Update.Unnecessary -> ())
  in
```

  In `make_var`, after the `append_user_info_graphviz` line: `watch_change name watch;`. In `named`, before returning `node`: `watch_change name node;`. (`fork` calls `create ?on_compute` and must not pass `?on_value_change`: leave it as is.)

- [ ] **Step 5: Run and watch it pass.** `dune runtest --force 2>&1 | tail -20`; the count case names the new total (current + 2); set it in `lib/verified.ml`; re-run.

- [ ] **Step 6: Format and commit.**

```bash
eval $(opam env --switch=/Users/ajaiupadhyaya/Documents/OhCamel --set-switch) && dune fmt 2>/dev/null; dune fmt 2>/dev/null; dune build @fmt && dune runtest --force 2>&1 | tail -3
git add lib/graph.ml lib/recompute_log.ml test/test_recompute_log.ml lib/verified.ml
git commit -F - <<'EOF'
graph: a hook for values that changed, beside the one for bodies that ran, because the difference between them is a cutoff and the drawing could not show a cutoff

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The frame carries the changed set

**Files:**
- Modify: `lib/server.ml`, `bin/main.ml`, `test/test_server.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Graph.create ?on_value_change`, `Recompute_log.note_change`, `Recompute_log.drain_changed`.
- Produces: every stream frame carries `"changed"` directly after `"recomputed"`: the sorted list of names whose value changed since the previous frame, or `null` on `/api/snapshot`, on a welcome frame, and on a server with no recompute log. `serve` and `demo` build their graphs with `~on_value_change:(Recompute_log.note_change log)`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_server.ml` above its `suite`:

```ocaml
(* The frame's changed set: what moved, drained with what ran, and never
   stolen by a poll. With the price of AAPL moved from 150 to 151 the cell
   changed and so did the exposure it feeds (151 x 200 = 30,200, was 30,000). *)
let test_a_frame_carries_what_changed_and_a_poll_does_not () =
  let log = Ohcamel.Recompute_log.create () in
  with_graph ~on_compute:(Ohcamel.Recompute_log.note log)
    ~f:(fun graph ->
      let server = Server.create ~recompute_log:log ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
      ignore (Server.next_frame server : string);
      Graph.set_price graph aapl (Price.of_float 151.0);
      Graph.stabilize graph;
      let poll = Yojson.Safe.from_string (Server.render server) in
      Alcotest.(check bool) "a poll says null" true (Poly.equal (field_exn poll "changed") `Null);
      let frame = Yojson.Safe.from_string (Server.next_frame server) in
      let changed = names_of (field_exn frame "changed") in
      Alcotest.(check bool) "price[AAPL] changed" true (List.mem changed "price[AAPL]" ~equal:String.equal);
      Alcotest.(check bool) "exposure:AAPL changed" true (List.mem changed "exposure:AAPL" ~equal:String.equal);
      Alcotest.(check bool) "XOM's exposure did not" false (List.mem changed "exposure:XOM" ~equal:String.equal))
    ()
```

  `with_graph` in this file takes `?on_compute`; give it `?on_value_change` too and pass both to `Graph.create`, and make this test call `with_graph ~on_compute:(Ohcamel.Recompute_log.note log) ~on_value_change:(Ohcamel.Recompute_log.note_change log)`. `names_of` already exists in this file and reads a list of `{name, n}` objects; add a sibling `strings_of : Yojson.Safe.t -> string list` for a plain list of strings and use it for `changed`. Register the case in `suite`.

- [ ] **Step 2: Run and watch it fail.** Expected: `missing key "changed"`.

- [ ] **Step 3: Implement.**
  - `json_of_snapshot` gains `?changed:string list`; directly after the `("recomputed", ...)` pair add `("changed", match changed with None -> `Null | Some names -> jlist jstring names);`.
  - `render` gains `?changed:(unit -> string list)` alongside `?recomputed`, drains it in the same branch that drains `recomputed`, and passes `~changed:(drain ())` to `json_of_snapshot`.
  - `next_frame`: `| Some log -> render ~recomputed:(fun () -> Recompute_log.drain log) ~changed:(fun () -> Recompute_log.drain_changed log) t`.
  - In `run_broadcaster`'s no-subscriber branch, drain both: `ignore (Recompute_log.drain_changed log : string list);` beside the existing drain.
  - `bin/main.ml`: in `run_live`, the `Graph.create` call gains `~on_value_change:(Recompute_log.note_change log)`; in `run_demo`, the same. First capture the six modes' output (`for m in synthetic stress backtest backtest-crisis options garch; do ./_build/default/bin/main.exe $m > /tmp/w1-gate/$m.before 2>&1; done` after `mkdir -p /tmp/w1-gate` and a build), then after the edit compare (`cmp`), six `GATE ok` lines.

- [ ] **Step 4: Run and watch it pass.** Bump the count by 1.

- [ ] **Step 5: Format and commit.**

```bash
git add lib/server.ml bin/main.ml test/test_server.ml lib/verified.ml
git commit -F - <<'EOF'
server: each frame says which values changed, drained beside what ran, because a poll must not steal the set and the drawing lights the two differently

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The topology says what each node costs

**Files:**
- Modify: `lib/graph.ml`, `lib/server.ml`, `test/test_graph.ml`, `test/test_server.ml` (the node key pin, if one exists), `lib/verified.ml`

**Interfaces:**
- Produces: `Ohcamel.Graph.Node_name.cost_of : string -> string option`; `Topology.Node.t` gains `cost : string option`; `/api/graph` node objects gain `"cost"` (string or null) after `"unit"`. The vocabulary is exactly: `"O(1)"`, `"O(w)"`, `"O(h)"`, `"O(n)"`, `"O(L)"`, `"O(w log w)"`, `"O(n·w)"`, `"O(n²)"`, `"O(n²·w)"`. Input cells are `None`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_graph.ml` above its `suite`:

```ocaml
(* Every named node's cost class, in the README's terms: n instruments, w
   observations per window, h equity marks, L limits.

   The two the README argues from are pinned by name: covariance rebuilds an
   n-by-n matrix over w observations, O(n²·w), and is the thing a tick never
   reaches; weights divides n exposures by gross, O(n), and is reached by every
   tick. The rest are pinned as a set: every non-input node has a class, from
   the vocabulary, so a node added tomorrow without one fails here. *)
let test_every_named_node_has_a_cost_class () =
  let vocabulary = [ "O(1)"; "O(w)"; "O(h)"; "O(n)"; "O(L)"; "O(w log w)"; "O(n·w)"; "O(n²)"; "O(n²·w)" ] in
  let module N = Graph.Node_name in
  Alcotest.(check (option string)) "covariance" (Some "O(n²·w)") (N.cost_of "covariance");
  Alcotest.(check (option string)) "weights" (Some "O(n)") (N.cost_of "weights");
  Alcotest.(check (option string)) "an exposure" (Some "O(1)") (N.cost_of "exposure:AAPL");
  Alcotest.(check (option string)) "a cell costs nothing to name" None (N.cost_of "price[AAPL]");
  Alcotest.(check (option string)) "the option aggregates" (Some "O(n)") (N.cost_of "portfolio_vega");
  with_book ~f:(fun graph ->
      List.iter (Graph.Topology.nodes (Graph.topology graph)) ~f:(fun n ->
          let name = Graph.Topology.Node.name n in
          match (Graph.Topology.Node.family n, Graph.Topology.Node.cost n) with
          | Graph.Topology.Family.Input, None -> ()
          | Graph.Topology.Family.Input, Some c -> Alcotest.failf "%s is a cell and has a cost %s" name c
          | _, None -> Alcotest.failf "%s has no cost class" name
          | _, Some c ->
              Alcotest.(check bool) (name ^ " is in the vocabulary") true (List.mem vocabulary c ~equal:String.equal)))
```

  `with_book` stands for whichever helper in `test/test_graph.ml` builds its standard multi-name book with limits (read the top of the file and use it; if it takes different arguments, adapt the call). Register the case.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value Graph.Node_name.cost_of`.

- [ ] **Step 3: Implement.** In `lib/graph.ml`, inside `module Node_name`, after `unit_of`:

```ocaml
  (* What one recomputation of a named node costs, in the README's own terms:
     n instruments, w observations in a return window, h equity marks, L
     limits. From the code, not a measurement -- the drawing weights a node's
     rule by it and the inspector prints it -- and kept beside [unit_of] so a
     node is classified the day it is named. Unclassified is None, and the
     topology test fails on a named node that is None. *)
  let cost_of (name : string) : string option =
    let starts p = String.is_prefix name ~prefix:p in
    if String.is_suffix name ~suffix:"]" then None
    else if starts "exposure:" || starts "feed:" || starts "greeks:" || starts "option_exposure:" || starts "limit:" then
      Some "O(1)"
    else if starts "sector:" then Some "O(n)"
    else
      match name with
      | "cash" | "equity_history" | "factor_returns" | "rate" | "valuation_days" | "now" -> None
      | "equity" | "var_notional" | "es_notional" -> Some "O(1)"
      | "portfolio_beta" -> Some "O(w)"
      | "current_drawdown" -> Some "O(h)"
      | "breaches" -> Some "O(L)"
      | "historical_var" | "expected_shortfall" -> Some "O(w log w)"
      | "aligned_returns" | "portfolio_returns" -> Some "O(n·w)"
      | "parametric_var" | "parametric_var_ewma" | "attribution" -> Some "O(n²)"
      | "covariance" | "covariance_ewma" -> Some "O(n²·w)"
      | "exposure_map" | "sector_map" | "gross_exposure" | "net_exposure" | "weights" | "component_var_map"
      | "component_var_sector_map" | "diversification_ratio" | "feed_health" | "gamma_map" | "vega_map"
      | "portfolio_gamma" | "portfolio_vega" | "vega_by_bucket" ->
          Some "O(n)"
      | _ -> None
```

  Add `cost : string option;` to `Topology.Node.t` after `unit`, set `cost = Node_name.cost_of name` where the nodes are built in `topology`, and in `lib/server.ml`'s `json_of_graph` add `("cost", jopt jstring (T.Node.cost n));` after the `"unit"` pair. If a test in `test/test_server.ml` pins a node object's exact key list, insert `"cost"` after `"unit"` there.

- [ ] **Step 4: Run and watch it pass.** Bump the count by 1.

- [ ] **Step 5: Format and commit.**

```bash
git add lib/graph.ml lib/server.ml test/test_graph.ml test/test_server.ml lib/verified.ml
git commit -F - <<'EOF'
graph: every named node carries its cost class in the README's terms, because "where the time goes" should be drawn and not only described

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: `/api/heat`

**Files:**
- Modify: `lib/recompute_log.ml`, `lib/server.ml`, `test/test_server.ml`, `deploy/smoke.sh`, `lib/verified.ml`

**Interfaces:**
- Produces: `Ohcamel.Recompute_log.lifetime : t -> (string * int) list` (every name ever noted with its count, sorted by name). Route `/api/heat`, directly after `/api/graph` in the routes table, purpose `"how often each named node has run since this process started, for the drawing's heat"`, body `{"started_at": <Time_ns.to_string_utc>, "stabilizes": <int>, "nodes": {<name>: <int>, ...} | null}` (`null` on a server with no recompute log). `EXPECTED_ROUTES` in `deploy/smoke.sh` gains `/api/heat` directly after `/api/graph`.

- [ ] **Step 1: Write the failing tests.** Append to `test/test_server.ml` above `suite`:

```ocaml
(* The heat the drawing starts from is the log's lifetime table, served as it
   stands -- the same table a frame's drain never clears -- and a server with
   no log says null rather than an empty object that reads as "nothing ran". *)
let test_heat_is_the_lifetime_table () =
  let log = Ohcamel.Recompute_log.create () in
  with_graph ~on_compute:(Ohcamel.Recompute_log.note log)
    ~f:(fun graph ->
      let server = Server.create ~recompute_log:log ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
      Graph.set_price graph aapl (Price.of_float 151.0);
      Graph.stabilize graph;
      ignore (Server.next_frame server : string);
      let status, _, body = respond server "/api/heat" in
      Alcotest.(check int) "200" 200 status;
      let nodes = field_exn (Yojson.Safe.from_string body) "nodes" in
      let served = match nodes with `Assoc kv -> List.map kv ~f:(fun (k, v) -> (k, Yojson.Safe.Util.to_int v)) | _ -> [] in
      Alcotest.(check (list (pair string int))) "exactly the lifetime table, drained frame or not"
        (Ohcamel.Recompute_log.lifetime log) served)
    ()

let test_heat_without_a_log_is_null () =
  with_server
    ~f:(fun server _ ->
      let _, _, body = respond server "/api/heat" in
      Alcotest.(check bool) "nodes null" true (Poly.equal (field_exn (Yojson.Safe.from_string body) "nodes") `Null))
    ()
```

  Register both. The existing 404 route-list test compares against `Server.route_paths ()` and needs no edit.

- [ ] **Step 2: Run and watch it fail.** Expected: `/api/heat is not routed`.

- [ ] **Step 3: Implement.** In `lib/recompute_log.ml`:

```ocaml
let lifetime (t : t) : (string * int) list =
  Hashtbl.to_alist t.lifetime |> List.sort ~compare:(fun (a, _) (b, _) -> String.compare a b)
```

  In `lib/server.ml`'s `routes`, directly after the `/api/graph` entry:

```ocaml
    (* Lifetime run counts, for the drawing's heat. The log's lifetime table,
       which a frame's drain never clears, so a page opened after an hour
       starts from the hour and not from nothing. A read of a table; nothing
       here stabilizes. *)
    ( "/api/heat",
      "how often each named node has run since this process started, for the drawing's heat",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string
             (`Assoc
               [
                 ("started_at", jstring (Time_ns.to_string_utc t.started_at));
                 ("stabilizes", `Int (Graph.total_stabilizes ()));
                 ( "nodes",
                   match t.recompute_log with
                   | None -> `Null
                   | Some log -> `Assoc (List.map (Recompute_log.lifetime log) ~f:(fun (k, n) -> (k, `Int n))) );
               ])) );
```

  In `deploy/smoke.sh`, `EXPECTED_ROUTES` becomes `"/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/heat /api/reports /api/reports/garch /api/ops"`, and in section 6 the live host's path list does not change.

- [ ] **Step 4: Run and watch it pass.** Bump the count by 2.

- [ ] **Step 5: Format and commit.**

```bash
git add lib/recompute_log.ml lib/server.ml test/test_server.ml deploy/smoke.sh lib/verified.ml
git commit -F - <<'EOF'
server: /api/heat serves the lifetime run counts, because the drawing's heat should start from the process's history and not from the moment a tab opened

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The drawing

**Files:**
- Modify: `web/graph.js`, `web/page.css`, `web/dashboard.js`, `web/index.html`

**Interfaces:**
- Consumes: the frame's `changed` (Task 2), each topology node's `cost` (Task 3), `/api/heat`'s `nodes` (Task 4).
- Produces: `OhCamelGraph.render(...)`'s handle gains `light(names, changed)` (the second argument optional), `heat(counts)` and `poster(force)`. Under the main figure (inspector mode) a legend and a poster button appear; the essay's fragments (no inspector) draw as before, with no heat, legend or poster.

The drawing code was built and tuned in a prototype against a replay of the public demo's stream, and is given here as two patches that apply cleanly to this branch's `web/` as it stands before this task. Both patch files are already in the workspace: `/Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-graph.patch` and `/Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-web.patch`. They are reproduced below so the review can read them.

What the patches do:
1. **The wave.** `applyLit` sets a CSS custom property `--d` on every lit node and edge, `STEP` = 36 ms per rank counted from the first rank the frame ran, and the stylesheet delays each node's fade and each edge's draw by it. Edges are measured once when drawn (`--len`) and draw themselves along their length before fading.
2. **Ran versus changed.** `light(names, changed)` keeps the changed set; a lit node or edge whose target is not in it gets class `cut` (dimmed name, dotted gold rule, dotted edge) instead of `lit`. The caption counts them.
3. **The origin.** Input cells in the changed set get `pulse` on their square.
4. **Heat.** `applyHeat` sets each edge's stroke width (0.45 to 1.70) and opacity (0.30 to 1.00) from the square root of runs over max runs of its target, where runs are `/api/heat`'s lifetime counts plus the runs this tab has seen. Main figure only.
5. **Cost.** A node's rule width comes from its `cost` class (`COST_W`); a folded band takes its costliest member's. The inspector line prints the class.
6. **Bundled edges.** `edgePath` routes an edge along a horizontal stub into its source column's gutter, one cubic across, and a stub from the target column's gutter.
7. **Legend and caption.** A legend of seven drawn swatches; the caption's title in a system serif.
8. **Poster.** A button toggles `poster` on the enclosing `figure` and redraws; Escape closes it; the page's scroll is locked while open.

- [ ] **Step 1: Apply the patches.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel-figure && git apply --check /Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-graph.patch && git apply --check /Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-web.patch && git apply /Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-graph.patch && git apply /Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/w1-web.patch && git diff --stat
```

Expected: four files changed (`web/graph.js`, `web/page.css`, `web/dashboard.js`, `web/index.html`).

`w1-graph.patch`:

```diff
--- a/web/graph.js
+++ b/web/graph.js
@@ -59,6 +59,12 @@
     ["exposure", ["exposure:", "option_exposure:", "greeks:"]]
   ];
   var SCALAR = { usd: 1, fraction: 1, ratio: 1, count: 1, price: 1, qty: 1, time: 1 };
+  // The wave: a frame lights its nodes in rank order, STEP ms a rank, so a
+  // tick is seen travelling from its cell to the limits.
+  var STEP = 36;
+  // A node's rule weight is its cost class, from the topology.
+  var COST_W = { "O(1)": 0.75, "O(w)": 1.15, "O(n)": 1.15, "O(n·w)": 1.7, "O(w log w)": 1.7, "O(n²)": 2.4, "O(n²·w)": 3.4 };
+  var COST_ORDER = ["O(1)", "O(w)", "O(n)", "O(w log w)", "O(n·w)", "O(n²)", "O(n²·w)"];
   function isFeed(n) { return n.name === "now" || n.name === "feed_health" || n.name.indexOf("feed:") === 0 || n.name.indexOf("last_tick[") === 0; }
   function stageOf(n) {
     if (n.family === "input") return "inputs";
@@ -277,7 +283,7 @@
     // per-name one.
     var compact = opts.compact === undefined ? true : !!opts.compact;
     var inspector = !!opts.inspector;
-    var st = { runs: {}, values: null, notes: {}, lit: [], litCount: 0, stale: [], staleSet: new Set(), partial: {}, open: null, hover: null, dead: false, L: null, svg: null, cap: null, insp: null };
+    var st = { runs: {}, base: {}, changed: null, origins: null, cutCount: 0, poster: false, values: null, notes: {}, lit: [], litCount: 0, stale: [], staleSet: new Set(), partial: {}, open: null, hover: null, dead: false, L: null, svg: null, cap: null, insp: null };
     var maxRank = 0; topo.nodes.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });
 
     // Escaped: a limit's name comes from the owner's book, and a quote in one
@@ -296,7 +302,7 @@
       if (SCALAR[n.unit] && (n.family === "input" || n.family === "singleton")) return st.values ? unitFormat(n.unit, st.values[n.name], n.name) : "—";
       return "ran " + (st.runs[n.name] || 0) + "×";
     }
-    function captionText() { var c = topo.counts || {}; return "The Incremental graph, taken from Incremental. " + c.named + " named nodes, " + c.observed + " observed. This frame: " + st.litCount + " ran."; }
+    function captionText() { var c = topo.counts || {}; return c.named + " named nodes, " + c.observed + " observed. This frame: " + st.litCount + " ran" + (st.changed ? ", " + st.cutCount + " stopped at a cutoff" : "") + "."; }
     // A band with some stale members and some fresh ones says how many after
     // its value, in the cannot-evaluate ink (the feed's own band in --over).
     function writeVal(g, n) {
@@ -316,13 +322,57 @@
     function drawnSet(names) { var s = new Set(); names.forEach(function (m) { var a = st.L.alias[m]; if (a) s.add(a); }); return s; }
     function applyLit() {
       if (!st.svg) return;
-      var lit = drawnSet(st.lit);
-      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node, path.edge"), function (e) { e.classList.remove("lit"); });
-      void st.svg.getBoundingClientRect(); // restart the 0.75 s fade for a node lit on consecutive frames
-      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) { if (lit.has(g.getAttribute("data-name"))) g.classList.add("lit"); });
-      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) { if (lit.has(p.getAttribute("data-to"))) p.classList.add("lit"); });
+      var lit = drawnSet(st.lit), changed = st.changed ? drawnSet(st.changed) : null;
+      var rankOf = {}; st.L.drawn.forEach(function (n) { rankOf[n.name] = n.rank; });
+      var r0 = Infinity; lit.forEach(function (m) { if (rankOf[m] !== undefined && rankOf[m] < r0) r0 = rankOf[m]; });
+      if (r0 === Infinity) r0 = 1;
+      // rank 0 is a cell; the first thing a frame RUNS is rank 1 at the earliest
+      r0 = Math.max(0, r0 - 1);
+      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node, path.edge, rect.cell"), function (e) { e.classList.remove("lit", "cut", "pulse", "origin"); });
+      void st.svg.getBoundingClientRect(); // restart the fade for anything lit on consecutive frames
+      var cut = 0;
+      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) {
+        var name = g.getAttribute("data-name");
+        if (!lit.has(name)) return;
+        g.style.setProperty("--d", ((rankOf[name] - r0) * STEP) + "ms");
+        var c = changed && !changed.has(name);
+        if (c) cut++;
+        g.classList.add(c ? "cut" : "lit");
+      });
+      st.cutCount = cut;
+      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) {
+        var to = p.getAttribute("data-to"), from = p.getAttribute("data-from");
+        if (!lit.has(to)) return;
+        var dr = rankOf[from] !== undefined ? Math.max(0, rankOf[from] - r0) : 0;
+        p.style.setProperty("--d", (dr * STEP) + "ms");
+        p.classList.add(changed && !changed.has(to) ? "cut" : "lit");
+      });
+      if (st.origins) drawnSet(st.origins).forEach(function (m) {
+        var g = nodeGroup(m); if (!g) return;
+        g.classList.add("origin");
+        var cell = g.querySelector("rect.cell"); if (cell) cell.classList.add("pulse");
+      });
       if (st.cap) st.cap.querySelector(".cap-title").textContent = captionText();
     }
+    // Session heat: an edge's weight and ink grow with how often its target
+    // has run, base (from the process) plus what this page has watched.
+    function runsOf(name) {
+      var n = st.L && st.L.drawn.filter(function (d) { return d.name === name; })[0];
+      if (n && n.band) { var s = 0; n.members.forEach(function (m) { s += (st.base[m] || 0) + (st.runs[m] || 0); }); return s; }
+      return (st.base[name] || 0) + (st.runs[name] || 0);
+    }
+    function applyHeat() {
+      // The main figure only: a fragment in the essay is not lit by frames,
+      // and heat with no runs behind it would fade every edge to its floor.
+      if (!st.svg || !inspector) return;
+      var paths = st.svg.querySelectorAll("path.edge"), runs = [], max = 1;
+      Array.prototype.forEach.call(paths, function (p, i) { runs[i] = runsOf(p.getAttribute("data-to")); if (runs[i] > max) max = runs[i]; });
+      Array.prototype.forEach.call(paths, function (p, i) {
+        var h = Math.sqrt(runs[i] / max);
+        p.style.strokeWidth = (0.45 + 1.25 * h).toFixed(2);
+        p.style.strokeOpacity = (0.3 + 0.7 * h).toFixed(2);
+      });
+    }
     // A band dims only when every name it holds is dimmed. Dimming price[S] × 5
     // because one of five is quiet would say four fresh prices are stale, which
     // is more than the engine knows.
@@ -351,6 +401,7 @@
         "inputs: " + (ins.length ? ins.join(", ") : "none"), "outputs: " + (outs.length ? outs.join(", ") : "none"),
         "upstream " + closure(topo, [n.name], "up").size + " / downstream " + closure(topo, [n.name], "down").size,
         "ran " + (st.runs[n.name] || 0) + "× since you opened this page"];
+      if (n.cost) parts.splice(3, 0, "cost " + n.cost);
       if (SENTENCES[n.name]) parts.push(SENTENCES[n.name]);
       return parts.join(" · ");
     }
@@ -381,7 +432,18 @@
       container.appendChild(ol);
       if (inspector) container.appendChild(el("div", "graph-note", "The drawing reads best on a laptop. Here are its node names by rank, with the ones this frame ran marked."));
     }
-    function edgePath(a, b) { var x1 = a.x + a.w + 8, y1 = a.y - 4, x2 = b.x - 4, y2 = b.y - 4, mx = (x1 + x2) / 2; return "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2; }
+    // Bundled: an edge leaves its node along a stub into its column's gutter,
+    // crosses as one curve, and arrives along a stub from the target's gutter,
+    // so the fans out of a node and into a node share a trunk.
+    function edgePath(a, b, na, nb, L) {
+      var x1 = a.x + a.w + 8, y1 = a.y - 4, x2 = b.x - 4, y2 = b.y - 4;
+      if (!na || !nb || !L || nb.rank <= na.rank) { var m0 = (x1 + x2) / 2; return "M" + x1 + "," + y1 + " C" + m0 + "," + y1 + " " + m0 + "," + y2 + " " + x2 + "," + y2; }
+      var g1 = Math.max(x1, L.colX(na.rank) + (L.colW[na.rank] || 0) + GUT * 0.4);
+      var g2 = Math.min(x2, L.colX(nb.rank) - GUT * 0.4);
+      if (g2 < g1) g2 = g1;
+      var mx = (g1 + g2) / 2;
+      return "M" + x1 + "," + y1 + " H" + g1.toFixed(1) + " C" + mx.toFixed(1) + "," + y1 + " " + mx.toFixed(1) + "," + y2 + " " + g2.toFixed(1) + "," + y2 + " H" + x2;
+    }
     function drawSvg() {
       // The observers belong to the whole graph; a filtered fragment is a
       // piece of the risk chain and does not carry them. A column holding
@@ -417,9 +479,10 @@
         c.lines.forEach(function (s, i) { var sp = svgEl("tspan", { x: c.x, dy: i ? 13 : 0 }); sp.textContent = s; t.appendChild(sp); });
         svg.appendChild(t);
       });
+      var drawnBy = {}; L.drawn.forEach(function (n) { drawnBy[n.name] = n; });
       L.edges.forEach(function (e) {
         var a = L.pos[e[0]], b = L.pos[e[1]]; if (!a || !b) return;
-        svg.appendChild(svgEl("path", { d: edgePath(a, b), "data-from": e[0], "data-to": e[1] }, "edge"));
+        svg.appendChild(svgEl("path", { d: edgePath(a, b, drawnBy[e[0]], drawnBy[e[1]], L), "data-from": e[0], "data-to": e[1] }, "edge"));
       });
       L.drawn.forEach(function (n) {
         var p = L.pos[n.name];
@@ -435,7 +498,10 @@
         var g = svgEl("g", { "data-name": n.name, "data-family": n.family, transform: "translate(" + p.x + "," + p.y + ")" }, "node" + (n.band ? " band" : ""));
         if (n.family === "input" && !n.band) g.appendChild(svgEl("rect", { x: -13, y: -10, width: 8, height: 8 }, "cell"));
         g.appendChild(text(0, 0, n.label, "name"));
-        g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: p.w, y2: 3 }, "rule"));
+        var rule = svgEl("line", { x1: 0, y1: 3, x2: p.w, y2: 3 }, "rule");
+        var cost = n.band ? n.members.map(function (m) { return (byName[m] || {}).cost; }).sort(function (x, y) { return COST_ORDER.indexOf(y) - COST_ORDER.indexOf(x); })[0] : n.cost;
+        if (cost && COST_W[cost]) { rule.style.strokeWidth = COST_W[cost]; g.setAttribute("data-cost", cost); }
+        g.appendChild(rule);
         if (n.observed) g.appendChild(svgEl("circle", { cx: p.w + 5, cy: -3, r: 2.5 }, "obs"));
         g.appendChild(text(0, 13, valueText(n), "val"));
         if (inspector && !n.band) {
@@ -482,16 +548,25 @@
         svg.appendChild(go);
       }
       container.appendChild(svg);
+      Array.prototype.forEach.call(svg.querySelectorAll("path.edge"), function (p) { p.style.setProperty("--len", Math.ceil(p.getTotalLength()) + "px"); });
       if (inspector) {
         st.insp = el("div", "inspector", ""); container.appendChild(st.insp);
+        container.appendChild(legend());
         st.cap = el("figcaption", "graph-cap");
-        st.cap.appendChild(el("span", "cap-title", captionText()));
-        st.cap.appendChild(el("span", "cap-prov", "LIVE · THIS HOST"));
+        var capTitle = el("span", "cap-title", captionText());
+        st.cap.appendChild(capTitle);
+        var right = el("span", "cap-right");
+        right.appendChild(el("span", "cap-prov", "LIVE · THIS HOST"));
+        var pb = el("button", "poster-btn", st.poster ? "close ✕" : "poster ⤢");
+        pb.type = "button";
+        pb.addEventListener("click", function () { togglePoster(); });
+        right.appendChild(pb);
+        st.cap.appendChild(right);
         container.appendChild(st.cap);
         container.appendChild(el("div", "graph-note", "nodes recomputed (footer) is Incremental's process-wide count and includes watch nodes, plumbing, every stress fork and the startup probe; the number above is named node bodies, from the hook the tests pin."));
         if (compact) container.appendChild(el("div", "graph-note", "per-symbol families are drawn as one band each (" + L.symbols.length + " names); the name a frame ticked opens its own row, and a frame without a tick folds it again"));
       }
-      applyLit(); applyStale();
+      applyHeat(); applyLit(); applyStale();
       if (st.hover) hover(L.pos[st.hover.name] ? st.hover : null);
     }
     function draw() {
@@ -508,8 +583,10 @@
         drawList();
       }
     }
-    function light(names) {
+    function light(names, changed) {
       if (st.dead) return;
+      st.changed = changed ? changed.slice() : null;
+      st.origins = changed ? changed.filter(function (m) { var n = byName[m]; return n && n.family === "input"; }) : null;
       var lit = [], open = null;
       (names || []).forEach(function (x) {
         var name = typeof x === "string" ? x : x.name, n = typeof x === "string" ? 1 : (x.n || 1);
@@ -522,7 +599,7 @@
       st.lit = lit; st.litCount = lit.length;
       if (compact && open !== st.open) { st.open = open; draw(); applyValues(); return; }
       if (!st.svg) { draw(); return; }
-      applyLit(); applyValues();
+      applyHeat(); applyLit(); applyValues();
       if (st.hover && st.insp) st.insp.textContent = inspectorLine(st.hover);
     }
     function dim(staleSymbols) {
@@ -536,7 +613,36 @@
     function setNote(name, t) { st.notes[name] = t; if (!st.dead) applyValues(); }
     function destroy() { st.dead = true; st.hover = null; container.textContent = ""; container.classList.remove("ohcamel-graph"); st.svg = null; st.cap = null; st.insp = null; }
     draw();
-    return { light: light, dim: dim, setValues: setValues, setNote: setNote, destroy: destroy, redraw: draw };
+    function heat(counts) { st.base = counts || {}; if (!st.dead) applyHeat(); }
+    function legend() {
+      var box = el("div", "graph-legend");
+      function sw(kind) {
+        var s = svgEl("svg", { width: 26, height: 12, viewBox: "0 0 26 12" }, "sw " + kind);
+        if (kind === "cell") s.appendChild(svgEl("rect", { x: 8, y: 2, width: 8, height: 8 }, "cell"));
+        else if (kind === "obs") s.appendChild(svgEl("circle", { cx: 13, cy: 6, r: 2.5 }, "obs"));
+        else if (kind === "lit") s.appendChild(svgEl("line", { x1: 1, y1: 6, x2: 25, y2: 6 }, "lit"));
+        else if (kind === "cut") s.appendChild(svgEl("line", { x1: 1, y1: 6, x2: 25, y2: 6 }, "cut"));
+        else if (kind === "heat") { s.appendChild(svgEl("line", { x1: 1, y1: 9, x2: 25, y2: 9 }, "cold")); s.appendChild(svgEl("line", { x1: 1, y1: 3, x2: 25, y2: 3 }, "hot")); }
+        else if (kind === "cost") { s.appendChild(svgEl("line", { x1: 1, y1: 3, x2: 25, y2: 3 }, "c1")); s.appendChild(svgEl("line", { x1: 1, y1: 9, x2: 25, y2: 9 }, "c3")); }
+        else if (kind === "absent") s.appendChild(svgEl("rect", { x: 2, y: 2, width: 22, height: 8, rx: 1.5 }, "slot"));
+        return s;
+      }
+      [["cell", "an input cell"], ["obs", "observed by the stream"], ["lit", "ran, and its value changed"], ["cut", "ran, and a cutoff held its value"],
+       ["heat", "edge weight: how often it has carried work"], ["cost", "rule weight: the node's cost class"], ["absent", "present, and not wired in"]].forEach(function (x) {
+        var item = el("span", "lg-item"); item.appendChild(sw(x[0])); item.appendChild(el("span", "lg-text", x[1])); box.appendChild(item);
+      });
+      return box;
+    }
+    function togglePoster(force) {
+      var fig = container.closest ? container.closest("figure") : null;
+      if (!fig) return;
+      st.poster = force === undefined ? !st.poster : force;
+      fig.classList.toggle("poster", st.poster);
+      document.documentElement.classList.toggle("poster-open", st.poster);
+      draw(); applyValues();
+    }
+    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && st.poster) togglePoster(false); });
+    return { light: light, dim: dim, setValues: setValues, setNote: setNote, heat: heat, destroy: destroy, redraw: draw, poster: togglePoster };
   }
 
   window.OhCamelGraph = { render: render, filter: filter, closure: closure };
```

`w1-web.patch`:

```diff
--- a/web/page.css
+++ b/web/page.css
@@ -217,17 +217,80 @@
   .ohcamel-graph rect.cell { fill: none; stroke: var(--ink); stroke-width: 1; }
   .ohcamel-graph circle.obs { fill: var(--ink); }
   .ohcamel-graph g.band text.name { fill: var(--ink-soft); }
-  /* the mark: the same 0.75 s fade the ledger's underline uses, because it is the same fact */
+  /* the mark: the same fade the ledger's underline uses, because it is the same
+     fact -- now in rank order. graph.js sets --d on every lit node and edge,
+     STEP ms a rank from the first thing the frame ran, so a tick is seen
+     travelling from its cell toward the limits, and seen not reaching what it
+     does not reach. */
   @keyframes graphmark { from { stroke: var(--mark); } to { stroke: var(--rule); } }
   @keyframes graphmarkink { from { fill: var(--mark); } to { fill: var(--ink); } }
-  .ohcamel-graph g.node.lit text.name { animation: graphmarkink .75s ease-out forwards; }
-  .ohcamel-graph g.node.lit line.rule { animation: graphmark .75s ease-out forwards; stroke-width: 1.5; }
-  .ohcamel-graph path.edge.lit { animation: graphmark .75s ease-out forwards; }
+  .ohcamel-graph path.edge { transition: stroke-width .6s ease, stroke-opacity .6s ease; }
+  .ohcamel-graph g.node.lit text.name { animation: graphmarkink .95s ease-out var(--d, 0ms) forwards; }
+  .ohcamel-graph g.node.lit line.rule { animation: graphmark .95s ease-out var(--d, 0ms) forwards; }
+  /* An edge draws itself along its own length (--len, measured once when the
+     figure is drawn), then fades as a node does. */
+  .ohcamel-graph path.edge.lit {
+    stroke-dasharray: var(--len) var(--len);
+    animation: edgedraw .36s cubic-bezier(.25, .7, .25, 1) var(--d, 0ms) forwards,
+               graphmark .9s ease-out calc(var(--d, 0ms) + 360ms) forwards;
+  }
+  @keyframes edgedraw { from { stroke-dashoffset: var(--len); stroke: var(--mark); } to { stroke-dashoffset: 0; stroke: var(--mark); } }
+  /* Ran, and a cutoff held the value: the name dims instead of lighting, its
+     rule breaks into gold dots, and the edge that reached it arrives dotted.
+     Solid gold is "this moved"; dotted gold is "this ran and stopped here". */
+  .ohcamel-graph g.node.cut text.name { animation: graphghost 1.2s ease-out var(--d, 0ms) forwards; }
+  .ohcamel-graph g.node.cut line.rule { stroke-dasharray: 1 2.5; stroke-width: 1.6 !important; animation: graphmark 1.2s ease-out var(--d, 0ms) forwards; }
+  @keyframes graphghost { 0% { fill: var(--ink-faint); } 70% { fill: var(--ink-faint); } 100% { fill: var(--ink); } }
+  .ohcamel-graph path.edge.cut { stroke-dasharray: 1 3 !important; animation: graphmark 1.1s ease-out var(--d, 0ms) forwards; }
+  /* The cell a frame set fills with the mark and empties. */
+  .ohcamel-graph rect.cell.pulse { animation: cellpulse 1s ease-out forwards; }
+  @keyframes cellpulse { 0% { fill: var(--mark); stroke: var(--mark); } 35% { fill: var(--mark); } 100% { fill: transparent; stroke: var(--ink); } }
+  @media (prefers-color-scheme: dark) {
+    .ohcamel-graph g.node.lit text.name { filter: drop-shadow(0 0 2px rgba(226, 173, 74, .55)); }
+  }
   @media (prefers-reduced-motion: reduce) {
-    .ohcamel-graph g.node.lit text.name, .ohcamel-graph g.node.lit line.rule, .ohcamel-graph path.edge.lit { animation: none; }
-    .ohcamel-graph g.node.lit text.name { fill: var(--mark); }
-    .ohcamel-graph g.node.lit line.rule, .ohcamel-graph path.edge.lit { stroke: var(--mark); }
+    .ohcamel-graph g.node.lit text.name, .ohcamel-graph g.node.lit line.rule, .ohcamel-graph path.edge.lit,
+    .ohcamel-graph g.node.cut text.name, .ohcamel-graph g.node.cut line.rule, .ohcamel-graph path.edge.cut,
+    .ohcamel-graph rect.cell.pulse { animation: none; }
+    .ohcamel-graph path.edge.lit { stroke-dasharray: none; stroke: var(--mark); }
+    .ohcamel-graph g.node.lit text.name { fill: var(--mark); }
+    .ohcamel-graph g.node.lit line.rule { stroke: var(--mark); }
+    .ohcamel-graph g.node.cut text.name { fill: var(--ink-faint); }
   }
+  /* The legend: what each mark on the drawing means, drawn with the marks. */
+  .ohcamel-graph .graph-legend { display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 10px 0 2px; font-size: 11px; color: var(--ink-soft); }
+  .ohcamel-graph .graph-legend .lg-item { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
+  .ohcamel-graph svg.sw { min-width: 0 !important; display: inline-block; }
+  .ohcamel-graph svg.sw rect.cell { fill: none; stroke: var(--ink); stroke-width: 1; }
+  .ohcamel-graph svg.sw circle.obs { fill: var(--ink); }
+  .ohcamel-graph svg.sw line.lit { stroke: var(--mark); stroke-width: 1.6; }
+  .ohcamel-graph svg.sw line.cut { stroke: var(--mark); stroke-width: 1.2; stroke-dasharray: 1 3; }
+  .ohcamel-graph svg.sw line.cold { stroke: var(--rule); stroke-width: .5; }
+  .ohcamel-graph svg.sw line.hot { stroke: var(--ink-soft); stroke-width: 2; }
+  .ohcamel-graph svg.sw line.c1 { stroke: var(--ink-soft); stroke-width: .75; }
+  .ohcamel-graph svg.sw line.c3 { stroke: var(--ink-soft); stroke-width: 3.4; }
+  .ohcamel-graph svg.sw rect.slot { fill: none; stroke: var(--ink-faint); stroke-width: .75; stroke-dasharray: 2 2; }
+  /* The caption is a figure's title and is set as one: a system serif, so
+     there is no font file to fetch, license or fail to load. */
+  #graph > .lbl { font-family: ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif; font-size: 17px; letter-spacing: 0; text-transform: none; color: var(--ink); font-weight: 600; }
+  #graph > .lbl i { font-weight: 400; color: var(--ink-soft); }
+  .ohcamel-graph figcaption.graph-cap .cap-title { font-family: ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif; font-size: 14px; color: var(--ink-soft); }
+  .ohcamel-graph .cap-right { display: inline-flex; gap: 14px; align-items: baseline; }
+  .ohcamel-graph .poster-btn { font: 10px ui-monospace, Menlo, monospace; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-soft); background: none; border: 1px solid var(--rule); border-radius: 2px; padding: 3px 8px; cursor: pointer; }
+  .ohcamel-graph .poster-btn:hover { color: var(--ink); border-color: var(--ink-soft); }
+  /* Poster mode: the figure alone, on its own dark ground, as wide as the
+     screen. The palette is set on the figure, so every token under it follows
+     whichever scheme the page is in. */
+  figure.fig.poster {
+    position: fixed; inset: 0; z-index: 100; margin: 0; overflow: auto; padding: 30px 40px 24px;
+    --ground: #0a0c0e; --panel: #101316; --ink: #eceeed; --ink-soft: #9aa29e; --ink-faint: #59615d; --rule: #252b2f;
+    --mark: #e6b04c; --over: #ec6c4f; --unknown: #8e9de3; --live: #53c08f;
+    background: radial-gradient(ellipse at 30% 20%, #13171b 0%, #0a0c0e 70%); color: var(--ink); border: 0;
+  }
+  figure.fig.poster .ohcamel-graph svg { min-width: 0 !important; max-width: none !important; width: 100% !important; }
+  figure.fig.poster .ohcamel-graph g.node.lit text.name { filter: drop-shadow(0 0 3px rgba(230, 176, 76, .6)); }
+  figure.fig.poster .graph-note { display: none; }
+  html.poster-open { overflow: hidden; }
   /* staleness by closure: the ledger's opacity, on exactly these nodes */
   .ohcamel-graph g.node.stale { opacity: .38; }
   .ohcamel-graph g.node.over text.name { fill: var(--over); }
--- a/web/dashboard.js
+++ b/web/dashboard.js
@@ -33,7 +33,9 @@
     graph.setValues(s.by_node || {});
     var over = 0; s.limits.forEach(function (l) { if (l.breached) over++; });
     graph.setNote("breaches", over + " of " + (s.limits.length + s.unevaluated.length));
-    graph.light(s.recomputed || []);
+    // What changed rides beside what ran: the drawing lights the first and
+    // ghosts the difference, which is where a cutoff held.
+    graph.light(s.recomputed || [], s.changed || null);
   }
   // A frame that arrived before the topology (or, on the live host, before
   // /api/ops) was set without it: stale rows by symbol rather than by closure,
@@ -51,6 +53,10 @@
     fetch("/api/graph").then(function (r) { return r.json(); }).then(function (t) {
       topology = t;
       graph = window.OhCamelGraph.render(box, topology, { inspector: true });
+      // The heat starts from the process's history, not from this tab's.
+      fetch("/api/heat").then(function (r) { return r.json(); }).then(function (h) {
+        if (graph && h && h.nodes) graph.heat(h.nodes);
+      }).catch(function () { /* the heat builds from this tab's frames instead */ });
       if (pendingFrame) { resetLedger(); renderGraphFrame(pendingFrame); }
     }).catch(function () { /* the figure stays empty; the ledger does not depend on it */ });
   }
--- a/web/index.html
+++ b/web/index.html
@@ -94,7 +94,7 @@
 <div id="warn"></div>
 
 <figure id="graph" class="fig">
-  <span class="lbl">figure 1 <i>— the graph, taken from the graph</i></span>
+  <span class="lbl">Figure 1. <i>The graph, taken from the graph.</i></span>
   <div id="graphbox"></div>
 </figure>
 
```

- [ ] **Step 2: Build and test.** `eval $(opam env --switch=/Users/ajaiupadhyaya/Documents/OhCamel --set-switch) && dune build 2>&1 | tail -5 && dune runtest --force 2>&1 | tail -5`. Expected: green, with no count change (the embedded-asset tests check structure, and the structure did not move). A grep of `web/` for the closing delimiters `|ohcamel_html}` and `|ohcamel_json}` (use `grep -rF`) prints nothing.

- [ ] **Step 3: The wire, from a running demo.**

```bash
lsof -nP -iTCP:8097 -sTCP:LISTEN; (./_build/default/bin/main.exe demo 8097 > /tmp/w1-demo.log 2>&1 &)
curl -sS --retry 30 --retry-connrefused --retry-delay 1 -m 40 http://localhost:8097/api/graph | python3 -c 'import json,sys; t=json.load(sys.stdin); n=[x for x in t["nodes"] if x["family"]!="input"]; print(len(n), "named;", sum(1 for x in n if x["cost"]), "with a cost")'
curl -sS -m 5 http://localhost:8097/api/heat | python3 -c 'import json,sys; h=json.load(sys.stdin); print(len(h["nodes"] or {}), "nodes with heat")'
curl -sS -N -m 4 http://localhost:8097/api/stream 2>/dev/null | head -c 60000 | grep -o '"changed":\[[^]]*\]' | head -2
```

Expected: every named node has a cost; heat covers the nodes that have run; a frame carries a non-empty `changed` list (the welcome frame's is `null`, so look at the second match).

- [ ] **Step 4: Look at it.** With the Playwright tools (`mcp__plugin_playwright_playwright__browser_*`; headless Chrome's `--dump-dom` hangs on a page holding an EventSource): navigate to `http://localhost:8097`, wait eight seconds, take a screenshot into `/Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/task-5-light.png`; switch to dark with `browser_run_code_unsafe` running `await page.emulateMedia({ colorScheme: 'dark' })`, wait two seconds, screenshot `/Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/task-5-dark.png`; run `document.querySelector('.poster-btn').click()` with `browser_evaluate`, wait two seconds, screenshot `/Users/ajaiupadhyaya/Documents/OhCamel-figure/.superpowers/sdd/2026-09-12-desk-w1-figure/task-5-poster.png`; press Escape and confirm `document.getElementById('graph').className` no longer contains `poster`; read `browser_console_messages` and confirm there are no errors. In the report, say what each screenshot shows: gold edges drawing out from the ticked name, at least one dotted ghost over a run, the legend, the serif caption, poster on a dark ground. If a check cannot be made, say which and why.

- [ ] **Step 5: Stop the server.** `pkill -f "main.exe demo 8097"`; then `lsof -nP -iTCP:8097 -sTCP:LISTEN` prints nothing.

- [ ] **Step 6: Commit.**

```bash
git add web/graph.js web/page.css web/dashboard.js web/index.html
git commit -F - <<'MSG'
web: Figure 1 draws a frame's work travelling rank by rank, ghosts where a cutoff held, and weights edges by heat and rules by cost, because the drawing should show the engine's economy and not only its shape

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 6: The documents say what the figure shows

**Files:**
- Modify: `README.md`, `web/index.html` (prose only), `docs/status.md`, `docs/overview.md`

- [ ] **Step 1: README.** In the section *Watching it*, replace the sentence that begins `Figure 1 is the dependency graph taken from Incremental's own node table` (through `until a bar closes.`) with:

> Figure 1 is the dependency graph taken from Incremental's own node table, and each frame's work travels across it rank by rank: a print in AAPL pulses its price cell, lights its exposure, the aggregates, the estimators and the limits that read them, and visibly leaves `covariance` dark until a bar closes. A node that ran and whose cutoff held its value is drawn dimmed with a dotted rule rather than lit, so the place a change stopped is on the page. An edge's weight is how often it has carried work since the process started (`/api/heat`), and a node's rule weight is its cost class, from O(1) for an exposure to O(n²·w) for the covariance. The figure opens full-screen as a poster.

- [ ] **Step 2: The page's own prose.** In `web/index.html`, find the sentence `Figure 1 is that graph, and every frame lights what ran.` and replace it with `Figure 1 is that graph, and every frame lights what ran, in the order it ran, and dims what ran and was stopped by a cutoff.`
- [ ] **Step 3: Route tables.** In `docs/status.md` (*The interface*) and `docs/overview.md` (*The page and the API*), add a row for `/api/heat` directly after `/api/graph`: `How often each named node has run since the process started; the drawing's heat`. In `docs/status.md`, the `/` row's description gains `, drawn rank by rank with cutoffs, heat and cost classes` after `lit per frame`.
- [ ] **Step 4: Test and commit.** `dune runtest --force 2>&1 | tail -3`, then:

```bash
git add README.md web/index.html docs/status.md docs/overview.md
git commit -F - <<'MSG'
docs: the README, the page and the route tables say what Figure 1 now draws, because a figure the prose describes wrongly is a figure a reader stops trusting

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Self-review (the plan writer's)

- **Spec coverage (§4 items 1 to 8):** the wave (T5 patch item 1), ran versus changed (T1 hook, T2 frame, T5 item 2), the origin (T5 item 3), heat (T4 route, T5 item 4), cost (T3 topology, T5 item 5), bundled edges (T5 item 6), legend and caption (T5 item 7), poster (T5 item 8). Item 9, the desk bands, belongs to phase A2.
- **Counts:** main's 309, + 2 (T1) + 1 (T2) + 1 (T3) + 2 (T4) = 315.
- **Type consistency:** `on_value_change`, `note_change`, `drain_changed`, `lifetime`, `cost_of`, `Topology.Node.cost`, the frame key `changed`, the node key `cost`, the route `/api/heat` and its `nodes` key are spelled the same in every task that names them.
- **Merging with desk/a1-record:** both branches edit `lib/server.ml`, `lib/graph.ml`, `test/test_server.ml`, `lib/verified.ml` and `deploy/smoke.sh`'s `EXPECTED_ROUTES`; the conflicts are additive and are resolved at merge by keeping both sides and re-summing the count.
