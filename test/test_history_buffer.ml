(* Unit tests for history_buffer.ml.

   A ring buffer has exactly one interesting behaviour and it happens once: the
   wrap. Before it, the physical order and the logical order agree and every
   test passes trivially; after it, they do not, and an implementation that
   returned [Array.to_list points] would produce a chart whose oldest points
   were in the middle. So most of what is below is about the boundary.

   The rest asserts the property the module's header is most insistent about --
   that this is bounded, in memory, and starts empty -- because that is the
   invariant a future change would erode by accident rather than on purpose. *)

open Core
module History_buffer = Ohcamel.History_buffer
module Point = Ohcamel.History_buffer.Point
module Graph = Ohcamel.Graph
open Ohcamel.Types

let feq = Alcotest.float 1e-9

let point n =
  {
    Point.time_ms = float_of_int n;
    gross = float_of_int n;
    net = 0.0;
    equity = 0.0;
    drawdown = 0.0;
    var_notional = None;
    es_notional = None;
  }

let grosses t = List.map (History_buffer.to_list t) ~f:Point.gross

let test_starts_empty () =
  let t = History_buffer.create ~capacity:4 () in
  Alcotest.(check int) "no points" 0 (History_buffer.length t);
  Alcotest.(check int) "and none appended" 0 (History_buffer.appended t);
  Alcotest.(check (list (float 1e-9))) "to_list is empty" [] (grosses t);
  (* A fresh buffer must not report [capacity] points of zero. That is the
     failure mode of a ring implemented as a plain array with no fill count, and
     it renders as a chart that opens with a flat line at the origin. *)
  Alcotest.(check int) "capacity is not length" 4 (History_buffer.capacity t)

let test_fills_in_order () =
  let t = History_buffer.create ~capacity:4 () in
  List.iter [ 1; 2; 3 ] ~f:(fun n -> History_buffer.append t (point n));
  Alcotest.(check int) "three points" 3 (History_buffer.length t);
  Alcotest.(check (list (float 1e-9))) "oldest first" [ 1.; 2.; 3. ] (grosses t)

(* THE WRAP.

   At exactly capacity the buffer is full and unwrapped. One more append must
   evict the oldest and leave the length pinned -- and the order must still come
   out oldest-first, which is the part a naive implementation gets wrong. *)
let test_evicts_the_oldest_at_capacity () =
  let t = History_buffer.create ~capacity:4 () in
  List.iter [ 1; 2; 3; 4 ] ~f:(fun n -> History_buffer.append t (point n));
  Alcotest.(check int) "full" 4 (History_buffer.length t);
  Alcotest.(check (list (float 1e-9))) "in order" [ 1.; 2.; 3.; 4. ] (grosses t);
  History_buffer.append t (point 5);
  Alcotest.(check int) "still full, never longer" 4 (History_buffer.length t);
  Alcotest.(check (list (float 1e-9)))
    "the oldest is gone and the order survived the wrap" [ 2.; 3.; 4.; 5. ] (grosses t);
  (* Several laps, so an off-by-one in the modulus shows up rather than
     cancelling. *)
  List.iter [ 6; 7; 8; 9; 10; 11 ] ~f:(fun n -> History_buffer.append t (point n));
  Alcotest.(check (list (float 1e-9)))
    "after several laps" [ 8.; 9.; 10.; 11. ] (grosses t);
  Alcotest.(check int) "length never exceeds capacity" 4 (History_buffer.length t)

(* Length is what is on screen; [appended] is what happened. A reader looking at
   four points out of eleven changes is looking at a third of the session, and
   only the second number says so. *)
let test_appended_counts_evictions () =
  let t = History_buffer.create ~capacity:4 () in
  List.iter (List.range 1 12) ~f:(fun n -> History_buffer.append t (point n));
  Alcotest.(check int) "eleven appends" 11 (History_buffer.appended t);
  Alcotest.(check int) "four survive" 4 (History_buffer.length t)

let test_capacity_of_one_is_a_valid_degenerate_ring () =
  let t = History_buffer.create ~capacity:1 () in
  History_buffer.append t (point 1);
  History_buffer.append t (point 2);
  Alcotest.(check (list (float 1e-9))) "only the newest" [ 2. ] (grosses t)

let test_clear () =
  let t = History_buffer.create ~capacity:4 () in
  List.iter [ 1; 2; 3; 4; 5 ] ~f:(fun n -> History_buffer.append t (point n));
  History_buffer.clear t;
  Alcotest.(check int) "empty again" 0 (History_buffer.length t);
  Alcotest.(check (list (float 1e-9))) "and reads empty" [] (grosses t);
  (* And it is usable afterwards, in order, rather than resuming mid-ring. *)
  History_buffer.append t (point 9);
  Alcotest.(check (list (float 1e-9))) "usable after clearing" [ 9. ] (grosses t)

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | _ -> Alcotest.failf "%s: expected Invalid_argument" name

let test_capacity_must_be_positive () =
  check_invalid_arg "zero capacity" (fun () ->
      ignore (History_buffer.create ~capacity:0 () : History_buffer.t));
  check_invalid_arg "negative capacity" (fun () ->
      ignore (History_buffer.create ~capacity:(-1) () : History_buffer.t))

(* THE OBSERVER.

   It has to record a point when the graph's published values change, and NOT
   when they do not. The second half is the one worth testing: the buffer hangs
   off the same signal the SSE stream does, so a flat line in the chart should
   mean the market was quiet rather than that a sampler was asleep. Re-sending
   an unchanged price must therefore append nothing. *)
let test_attaches_to_change_and_only_to_change () =
  let aapl = Symbol.of_string "AAPL" in
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      (* A driven clock, not the wall clock, so the timestamps are assertable
         rather than merely present. *)
      let tick = ref 0.0 in
      let now () =
        tick := !tick +. 1000.0;
        Time.add Time.epoch (Time.Span.of_ms !tick)
      in
      let h = History_buffer.attach ~capacity:8 ~graph ~now () in
      Graph.set_price graph aapl (Price.of_float 100.0);
      Graph.set_qty graph aapl (Qty.of_float 10.0);
      Graph.stabilize graph;
      let after_first = History_buffer.length h in
      Alcotest.(check bool) "a change was recorded" true (after_first > 0);
      (* The same price again. graph.ml puts a value-equality cutoff on every
         input cell, so nothing downstream changes and no observer fires. *)
      Graph.set_price graph aapl (Price.of_float 100.0);
      Graph.stabilize graph;
      Alcotest.(check int)
        "an unchanged input records nothing" after_first (History_buffer.length h);
      (* A real move does. *)
      Graph.set_price graph aapl (Price.of_float 101.0);
      Graph.stabilize graph;
      Alcotest.(check bool)
        "a real move records" true
        (History_buffer.length h > after_first);
      let points = History_buffer.to_list h in
      let last = List.last_exn points in
      Alcotest.check feq "the newest point carries the new gross" 1010.0
        (Point.gross last);
      (* Timestamps increase, and they came from the injected clock. *)
      let times = List.map points ~f:Point.time_ms in
      Alcotest.(check bool)
        "timestamps are non-decreasing" true
        (List.is_sorted times ~compare:Float.compare))

(* A point carries what a chart needs and nothing else -- and the risk numbers
   stay nullable, because a zero during warm-up would draw a line at the bottom
   of the axis that reads as "no risk". *)
let test_point_of_snapshot_keeps_unknowns_unknown () =
  let aapl = Symbol.of_string "AAPL" in
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 1_000.0)
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph aapl (Price.of_float 50.0);
      Graph.set_qty graph aapl (Qty.of_float 4.0);
      Graph.stabilize graph;
      let p = History_buffer.point_of_snapshot ~time_ms:0.0 (Graph.snapshot graph) in
      Alcotest.check feq "gross" 200.0 (Point.gross p);
      Alcotest.check feq "net" 200.0 (Point.net p);
      Alcotest.check feq "equity is cash plus net" 1_200.0 (Point.equity p);
      Alcotest.(check bool)
        "VaR is unknown while warming up, not zero" true
        (Option.is_none (Point.var_notional p));
      Alcotest.(check bool) "and so is ES" true (Option.is_none (Point.es_notional p)))

let suite =
  ( "history_buffer",
    [
      Alcotest.test_case "a fresh buffer is empty, not full of zeros" `Quick
        test_starts_empty;
      Alcotest.test_case "fills oldest-first" `Quick test_fills_in_order;
      Alcotest.test_case "EVICTS THE OLDEST AT CAPACITY, order intact" `Quick
        test_evicts_the_oldest_at_capacity;
      Alcotest.test_case "appended counts evictions, length does not" `Quick
        test_appended_counts_evictions;
      Alcotest.test_case "capacity of one" `Quick
        test_capacity_of_one_is_a_valid_degenerate_ring;
      Alcotest.test_case "clear" `Quick test_clear;
      Alcotest.test_case "capacity must be positive" `Quick test_capacity_must_be_positive;
      Alcotest.test_case "records CHANGE, and only change" `Quick
        test_attaches_to_change_and_only_to_change;
      Alcotest.test_case "an unknown risk number stays unknown" `Quick
        test_point_of_snapshot_keeps_unknowns_unknown;
    ] )
