(* Orders in the journal: what the order manager writes before the wire and
   what a restart reads back.

   The round trip is checked against the state machine itself: an order is
   driven through Order.apply, journaled at every step, and the row read back
   must rebuild the same order -- state, venue id, filled quantity, average
   price, the set of executions, the reason. *)

open Core
open Ohcamel.Types
module Journal = Ohcamel_desk.Journal
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T14:31:00Z"

let id n =
  Option.value_exn
    (Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ%d" n))

let request n qty =
  {
    Order.Request.client_order_id = id n;
    symbol = Symbol.of_string "AAPL";
    side = Order.Side.Buy;
    qty;
    kind = Order.Kind.Market;
    tif = Order.Tif.Day;
  }

let open_journal () = Or_error.ok_exn (Journal.open_ ~path:":memory:")

let insert j o =
  Journal.insert_order j o ~source:"manual" ~decision_price:(Price.of_float 100.0)
    ~arrival:None ~verdict:`Null ~at

let step j o event =
  let o, anomaly = Order.apply o event in
  (match event with
  | Order.Event.Venue_fill f -> ignore (Journal.record_fill j o f : bool)
  | _ -> ());
  Journal.update_order j o ~event ~anomaly ~at;
  o

let fill id qty price =
  Order.Event.Venue_fill
    {
      Order.Fill.execution_id = id;
      qty;
      price = Price.of_float price;
      at;
      position_qty = None;
    }

let test_an_order_round_trips_through_the_journal () =
  let j = open_journal () in
  let o = Order.create (request 1 100) in
  insert j o;
  let o =
    List.fold
      [
        Order.Event.Acknowledged "venue-1";
        Order.Event.Venue_accepted;
        fill "x1" 40.0 100.0;
        fill "x2" 60.0 100.5;
      ]
      ~init:o ~f:(step j)
  in
  let back = (Option.value_exn (Journal.load_order j (id 1))).Journal.Order_row.order in
  Alcotest.(check string) "state" "filled" (Order.State.to_string back.Order.state);
  Alcotest.(check (option string)) "venue id" (Some "venue-1") back.Order.venue_order_id;
  Alcotest.(check (float 1e-9)) "filled 100" 100.0 back.Order.filled_qty;
  (* (40 x 100 + 60 x 100.5) / 100 = 100.30 *)
  Alcotest.(check (option (float 1e-9)))
    "average 100.30" (Some 100.30) (Order.avg_fill_price back);
  Alcotest.(check (list string))
    "executions" [ "x1"; "x2" ]
    (Set.to_list back.Order.execution_ids);
  Alcotest.(check bool)
    "the rebuilt order equals the machine's" true
    (Float.equal back.Order.filled_notional o.Order.filled_notional
    && Order.State.equal back.Order.state o.Order.state)

let test_a_fill_is_recorded_once () =
  let j = open_journal () in
  let o = Order.create (request 2 10) in
  insert j o;
  let f =
    {
      Order.Fill.execution_id = "x1";
      qty = 10.0;
      price = Price.of_float 50.0;
      at;
      position_qty = Some 10.0;
    }
  in
  Alcotest.(check bool) "new" true (Journal.record_fill j o f);
  Alcotest.(check bool) "a replay is not" false (Journal.record_fill j o f);
  Alcotest.(check int)
    "one fill on the page" 1
    (List.length (Journal.recent_fills j ~limit:10))

let test_open_orders_are_the_non_terminal_ones_oldest_first () =
  let j = open_journal () in
  let o1 = Order.create (request 3 10)
  and o2 = Order.create (request 4 10)
  and o3 = Order.create (request 5 10) in
  List.iter [ o1; o2; o3 ] ~f:(insert j);
  ignore (step j o1 (Order.Event.Acknowledged "a") : Order.t);
  ignore
    (List.fold [ Order.Event.Acknowledged "b"; fill "y" 10.0 1.0 ] ~init:o2 ~f:(step j)
      : Order.t);
  ignore (step j o3 (Order.Event.Outcome_unknown "timed out") : Order.t);
  Alcotest.(check (list string))
    "submitted and unknown are open; filled is not"
    [ "submitted"; "submit_unknown" ]
    (List.map (Journal.open_orders j) ~f:(fun r ->
         Order.State.to_string r.Journal.Order_row.order.Order.state))

let test_a_venue's_reason_survives_the_restart () =
  let j = open_journal () in
  let o = Order.create (request 6 10) in
  insert j o;
  ignore
    (step j o (Order.Event.Venue_rejected_submission "403: insufficient buying power")
      : Order.t);
  Alcotest.(check (option string))
    "the reason" (Some "403: insufficient buying power")
    (Option.value_exn (Journal.load_order j (id 6))).Journal.Order_row.order.Order.reason

(* Spec §6: a journal round trip is the identity. Whatever events the machine
   is given -- legal or not, fills replayed or late -- journaled step by step,
   the row reads back as the order the machine holds. *)
let prop_a_journal_round_trip_is_the_identity =
  let open QCheck in
  let event =
    Gen.(
      oneof_weighted
        [
          (2, return (Order.Event.Acknowledged "venue-9"));
          (1, return Order.Event.Venue_accepted);
          ( 1,
            map
              (fun why -> Order.Event.Outcome_unknown why)
              (oneof_list [ "timed out"; "503" ]) );
          (1, return (Order.Event.Found "venue-9"));
          (1, return Order.Event.Not_found);
          (1, return Order.Event.Cancel_requested);
          (1, return Order.Event.Venue_cancelled);
          ( 1,
            map
              (fun why -> Order.Event.Venue_rejected why)
              (oneof_list [ "halted"; "no borrow" ]) );
          ( 4,
            map2
              (fun n q ->
                fill (sprintf "x%d" n) (Float.of_int q) (100.0 +. Float.of_int n))
              (int_bound 5) (int_range 1 6) );
        ])
  in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"a journal round trip is the identity, whatever the events"
       ~count:200
       (make Gen.(list_size (int_range 0 12) event))
       (fun events ->
         let j = open_journal () in
         let o = Order.create (request 7 10) in
         insert j o;
         let o = List.fold events ~init:o ~f:(step j) in
         let back =
           (Option.value_exn (Journal.load_order j (id 7))).Journal.Order_row.order
         in
         Journal.close j;
         Order.State.equal back.Order.state o.Order.state
         && Option.equal String.equal back.Order.venue_order_id o.Order.venue_order_id
         && Float.( < ) (Float.abs (back.Order.filled_qty -. o.Order.filled_qty)) 1e-9
         && Float.( < )
              (Float.abs (back.Order.filled_notional -. o.Order.filled_notional))
              1e-6
         && Set.equal back.Order.execution_ids o.Order.execution_ids
         && Option.equal String.equal back.Order.reason o.Order.reason))

let suite =
  ( "journal_orders",
    [
      Alcotest.test_case "an order round-trips through the journal" `Quick
        test_an_order_round_trips_through_the_journal;
      Alcotest.test_case "a fill is recorded once" `Quick test_a_fill_is_recorded_once;
      Alcotest.test_case "open orders are the non-terminal ones, oldest first" `Quick
        test_open_orders_are_the_non_terminal_ones_oldest_first;
      Alcotest.test_case "a venue's reason survives the restart" `Quick
        test_a_venue's_reason_survives_the_restart;
      prop_a_journal_round_trip_is_the_identity;
    ] )
