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
    Graph.create ~on_compute:(Recompute_log.note log)
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
      Alcotest.(check int)
        "and its lifetime table did not move" before (Recompute_log.total log))

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
