(* The order state machine, event by event.

   Every expected quantity and price is small enough to check by hand, and is
   checked beside the assertion. The machine is pure, so each case is a list
   of events written down and the state they must leave behind. *)

open Core
open Ohcamel.Types
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T14:31:00Z"

let request ?(qty = 100) ?(kind = Order.Kind.Market) () =
  {
    Order.Request.client_order_id =
      Option.value_exn (Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZZ");
    symbol = Symbol.of_string "AAPL";
    side = Order.Side.Buy;
    qty;
    kind;
  }

let fill ?(position_qty = None) id qty price =
  Order.Event.Venue_fill
    { Order.Fill.execution_id = id; qty; price = Price.of_float price; at; position_qty }

let run order events =
  List.fold events ~init:(order, []) ~f:(fun (o, anomalies) e ->
      let o, a = Order.apply o e in
      (o, Option.to_list a @ anomalies))

let state =
  Alcotest.testable
    (fun ppf s -> Fmt.string ppf (Order.State.to_string s))
    Order.State.equal

let float_eq = Alcotest.float 1e-9

let test_a_market_order_fills_in_two_parts () =
  let o, anomalies =
    run
      (Order.create (request ()))
      [
        Order.Event.Acknowledged "venue-1";
        Order.Event.Venue_accepted;
        fill "x1" 40.0 100.00;
        fill "x2" 60.0 100.50;
      ]
  in
  Alcotest.check state "filled" Order.State.Filled o.Order.state;
  Alcotest.(check (option string))
    "the venue's id" (Some "venue-1") o.Order.venue_order_id;
  Alcotest.check float_eq "filled 40 + 60 = 100" 100.0 o.Order.filled_qty;
  (* (40 x 100.00 + 60 x 100.50) / 100 = (4000 + 6030) / 100 = 100.30 *)
  Alcotest.check (Alcotest.option float_eq) "average 100.30" (Some 100.30)
    (Order.avg_fill_price o);
  Alcotest.(check int) "no anomalies" 0 (List.length anomalies)

let test_a_partial_fill_is_its_own_state () =
  let o, _ =
    run (Order.create (request ())) [ Order.Event.Acknowledged "v"; fill "x1" 30.0 99.0 ]
  in
  Alcotest.check state "partially filled" Order.State.Partially_filled o.Order.state;
  (* 100 ordered, 30 filled: 70 remain *)
  Alcotest.check float_eq "70 remain" 70.0 (Order.remaining_qty o)

let test_a_replayed_fill_changes_nothing () =
  let o, anomalies =
    run
      (Order.create (request ()))
      [ Order.Event.Acknowledged "v"; fill "x1" 30.0 99.0; fill "x1" 30.0 99.0 ]
  in
  Alcotest.check float_eq "still 30: the second x1 is the first x1 again" 30.0
    o.Order.filled_qty;
  Alcotest.(check int) "a replay is not an anomaly" 0 (List.length anomalies)

let test_a_fill_after_a_cancel_is_still_a_fill () =
  let o, anomalies =
    run
      (Order.create (request ()))
      [ Order.Event.Acknowledged "v"; Order.Event.Venue_cancelled; fill "x1" 10.0 101.0 ]
  in
  Alcotest.check state "the state stays cancelled" Order.State.Cancelled o.Order.state;
  Alcotest.check float_eq "but the ten shares are counted" 10.0 o.Order.filled_qty;
  Alcotest.(check (list string))
    "and the anomaly is named" [ "fill after cancelled" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)

let test_an_overfill_is_recorded_not_refused () =
  let o, anomalies =
    run
      (Order.create (request ~qty:10 ()))
      [ Order.Event.Acknowledged "v"; fill "x1" 6.0 50.0; fill "x2" 6.0 50.0 ]
  in
  Alcotest.check state "filled" Order.State.Filled o.Order.state;
  (* 6 + 6 = 12 against 10 ordered *)
  Alcotest.check float_eq "twelve shares, as the venue reported" 12.0 o.Order.filled_qty;
  Alcotest.(check (list string))
    "overfill named"
    [ "overfill: 12 filled against 10 ordered" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)

let test_an_unknown_submission_resolves_only_by_asking () =
  let unknown, _ =
    run (Order.create (request ())) [ Order.Event.Outcome_unknown "timed out" ]
  in
  Alcotest.check state "unknown is a state" Order.State.Submit_unknown unknown.Order.state;
  let found, _ = run unknown [ Order.Event.Found "venue-9" ] in
  Alcotest.check state "found becomes submitted" Order.State.Submitted found.Order.state;
  Alcotest.(check (option string))
    "with the venue's id" (Some "venue-9") found.Order.venue_order_id;
  let absent, _ = run unknown [ Order.Event.Not_found ] in
  Alcotest.check state "not found becomes failed" Order.State.Failed absent.Order.state

let test_a_fill_racing_a_cancel_completes_the_order () =
  let o, anomalies =
    run
      (Order.create (request ~qty:10 ()))
      [
        Order.Event.Acknowledged "v";
        Order.Event.Venue_accepted;
        Order.Event.Cancel_requested;
        fill "x1" 10.0 20.0;
      ]
  in
  Alcotest.check state "a fill that beat the cancel fills the order" Order.State.Filled
    o.Order.state;
  Alcotest.(check int) "and that is legal" 0 (List.length anomalies)

let test_an_illegal_event_leaves_the_state_alone () =
  let o, anomalies =
    run
      (Order.create (request ~qty:1 ()))
      [ Order.Event.Acknowledged "v"; fill "x1" 1.0 5.0; Order.Event.Cancel_requested ]
  in
  Alcotest.check state "still filled" Order.State.Filled o.Order.state;
  Alcotest.(check (list string))
    "named"
    [ "illegal: cancel_requested in filled" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)

let test_the_twelve_states_round_trip_and_six_are_terminal () =
  Alcotest.(check int) "twelve states" 12 (List.length Order.State.all);
  List.iter Order.State.all ~f:(fun s ->
      Alcotest.(check (option state))
        (Order.State.to_string s) (Some s)
        (Order.State.of_string (Order.State.to_string s)));
  Alcotest.(check (list string))
    "the terminal ones, in declaration order"
    [
      "rejected_pre_trade";
      "filled";
      "cancelled";
      "expired";
      "rejected_by_venue";
      "failed";
    ]
    (List.filter Order.State.all ~f:Order.State.is_terminal
    |> List.map ~f:Order.State.to_string)

(* Fills are facts: whatever else happens, the filled quantity is the sum of
   the distinct fills applied. Events are drawn at random; fills carry distinct
   execution ids so no case is a replay. *)
let prop_no_fill_is_ever_lost =
  let open QCheck in
  let event_gen =
    Gen.(
      oneof_weighted
        [
          (3, map (fun q -> `Fill (float_of_int (q + 1))) (int_bound 50));
          (1, return (`Other Order.Event.Venue_accepted));
          (1, return (`Other Order.Event.Cancel_requested));
          (1, return (`Other Order.Event.Venue_cancelled));
          (1, return (`Other Order.Event.Venue_expired));
          (1, return (`Other (Order.Event.Acknowledged "v")));
          (1, return (`Other (Order.Event.Outcome_unknown "t")));
          (1, return (`Other Order.Event.Not_found));
        ])
  in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"no fill is ever lost, in any order of events" ~count:300
       (make Gen.(list_size (int_bound 30) event_gen))
       (fun events ->
         let _, o, total =
           List.fold events
             ~init:(0, Order.create (request ()), 0.0)
             ~f:(fun (n, o, total) e ->
               match e with
               | `Fill q ->
                   ( n + 1,
                     fst (Order.apply o (fill (Printf.sprintf "x%d" n) q 10.0)),
                     total +. q )
               | `Other ev -> (n, fst (Order.apply o ev), total))
         in
         Float.( = ) o.Order.filled_qty total))

let suite =
  ( "desk_order",
    [
      Alcotest.test_case "a market order fills in two parts" `Quick
        test_a_market_order_fills_in_two_parts;
      Alcotest.test_case "a partial fill is its own state" `Quick
        test_a_partial_fill_is_its_own_state;
      Alcotest.test_case "a replayed fill changes nothing" `Quick
        test_a_replayed_fill_changes_nothing;
      Alcotest.test_case "a fill after a cancel is still a fill" `Quick
        test_a_fill_after_a_cancel_is_still_a_fill;
      Alcotest.test_case "an overfill is recorded, not refused" `Quick
        test_an_overfill_is_recorded_not_refused;
      Alcotest.test_case "an unknown submission resolves only by asking" `Quick
        test_an_unknown_submission_resolves_only_by_asking;
      Alcotest.test_case "a fill racing a cancel completes the order" `Quick
        test_a_fill_racing_a_cancel_completes_the_order;
      Alcotest.test_case "an illegal event leaves the state alone" `Quick
        test_an_illegal_event_leaves_the_state_alone;
      Alcotest.test_case "the twelve states round-trip and six are terminal" `Quick
        test_the_twelve_states_round_trip_and_six_are_terminal;
      prop_no_fill_is_ever_lost;
    ] )
