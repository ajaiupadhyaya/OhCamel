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
  Alcotest.(check (list (pair string int)))
    "the frame table is empty after a drain" [] (Recompute_log.drain log);
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
    Graph.create ~on_compute:(Recompute_log.note log)
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
      (* [seeded_graph] built and stabilized the whole book on this same log
         before this test ever ran a scenario, so the lifetime table is
         already populated with that legitimate history -- [Recompute_log]
         never clears it, by design. The claim this test makes is not that
         lifetime is zero; it is that the stress run does not add to it. *)
      let lifetime_before_stress = Recompute_log.total log in
      let before = Graph.total_nodes_recomputed () in
      let outcomes = Stress.run_all ~graph ~scenarios:(Stress.suite_for ~graph) in
      Alcotest.(check bool)
        "the suite actually ran something" true
        (List.length outcomes > 0 && Graph.total_nodes_recomputed () - before > 0);
      Alcotest.(check (list (pair string int)))
        "not one forked recomputation reached the parent's log" []
        (Recompute_log.drain log);
      Alcotest.(check int)
        "and the lifetime table is unchanged by the stress run too" lifetime_before_stress
        (Recompute_log.total log))

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
