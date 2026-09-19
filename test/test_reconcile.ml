(* Reconciliation as a table: the journal's order, the venue's answer, and the
   state the machine reaches from the events -- with the arithmetic of every
   recovered fill beside it. *)

open Core
open Ohcamel.Types
module Order = Ohcamel_desk.Order
module Venue = Ohcamel_desk.Venue
module Reconcile = Ohcamel_desk.Reconcile
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T15:00:00Z"
let aapl = Symbol.of_string "AAPL"
let cid = "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"

let order ?(events = []) qty =
  let request =
    {
      Order.Request.client_order_id = Option.value_exn (Ids.Client_order_id.of_string cid);
      symbol = aapl;
      side = Order.Side.Buy;
      qty;
      kind = Order.Kind.Market;
      tif = Order.Tif.Day;
    }
  in
  List.fold events ~init:(Order.create request) ~f:(fun o e -> fst (Order.apply o e))

let venue ?(filled = 0.0) ?avg status =
  {
    Venue.Venue_order.id = "v-1";
    client_order_id = cid;
    symbol = aapl;
    side = Order.Side.Buy;
    qty = 100.0;
    filled_qty = filled;
    filled_avg_price = avg;
    status;
    limit_price = None;
  }

let fill id qty price =
  Order.Event.Venue_fill
    {
      Order.Fill.execution_id = id;
      qty;
      price = Price.of_float price;
      at;
      position_qty = None;
    }

let run o v =
  List.fold (Reconcile.events_for o v ~at) ~init:o ~f:(fun o e -> fst (Order.apply o e))

let names o v = List.map (Reconcile.events_for o v ~at) ~f:Order.Event.name
let state o = Order.State.to_string o.Order.state

let test_what_a_restart_learns () =
  let pending = order 100 in
  Alcotest.(check (list string))
    "pending, and the venue has not heard of it: an unknown, for the lookups to decide"
    [ "outcome_unknown" ] (names pending None);
  Alcotest.(check string) "submit_unknown" "submit_unknown" (state (run pending None));
  let unknown = order ~events:[ Order.Event.Outcome_unknown "timed out" ] 100 in
  Alcotest.(check (list string))
    "unknown, and still not found: nothing decided on one miss" [] (names unknown None);
  Alcotest.(check (list string))
    "pending, and the venue has it"
    [ "outcome_unknown"; "found"; "venue_accepted" ]
    (names pending (Some (venue "new")));
  let resolved = run pending (Some (venue "new")) in
  Alcotest.(check string) "accepted" "accepted" (state resolved);
  Alcotest.(check (option string))
    "with the venue's id" (Some "v-1") resolved.Order.venue_order_id;
  let acknowledged = order ~events:[ Order.Event.Acknowledged "v-1" ] 100 in
  Alcotest.(check (list string))
    "acknowledged, and the lookup finds nothing: nothing invented" []
    (names acknowledged None);
  (* A fill of 30 at 100.00 that the stream delivered before the POST's answer
     was read, and then the process stopped: partially filled, with no venue
     id. The venue's 30 filled equals the journal's 30, so nothing is missing,
     and partially_filled maps to no status event: the lookup adds the id and
     nothing else. *)
  let unidentified = order ~events:[ fill "x1" 30.0 100.0 ] 100 in
  let lookup = Some (venue ~filled:30.0 ~avg:100.0 "partially_filled") in
  Alcotest.(check (list string))
    "moved on without its id: the id, and nothing else" [ "found" ]
    (names unidentified lookup);
  Alcotest.(check (option string))
    "and it is kept" (Some "v-1") (run unidentified lookup).Order.venue_order_id;
  (* Accepted with nothing filled; the venue filled all 100 at 100.30 while
     nobody listened: one recovered fill of 100 at (100 x 100.30 - 0) / 100. *)
  let accepted =
    order ~events:[ Order.Event.Acknowledged "v-1"; Order.Event.Venue_accepted ] 100
  in
  let filled = run accepted (Some (venue ~filled:100.0 ~avg:100.30 "filled")) in
  Alcotest.(check string) "filled" "filled" (state filled);
  Alcotest.(check (option (float 1e-9)))
    "at the venue's average" (Some 100.30) (Order.avg_fill_price filled);
  (* 40 filled at 100.00 before the stop. The venue says 70 filled at an
     average of 100.20, then cancelled. Missing: 30 shares, whose notional is
     70 x 100.20 - 40 x 100.00 = 7,014 - 4,000 = 3,014, so 3,014 / 30 =
     100.4666... each. *)
  let partial =
    order ~events:[ Order.Event.Acknowledged "v-1"; fill "x1" 40.0 100.0 ] 100
  in
  let answer = Some (venue ~filled:70.0 ~avg:100.20 "canceled") in
  Alcotest.(check (list string))
    "a missing fill, then the cancel"
    [ "venue_fill"; "venue_cancelled" ]
    (names partial answer);
  let cancelled = run partial answer in
  Alcotest.(check string) "cancelled" "cancelled" (state cancelled);
  Alcotest.(check (float 1e-9)) "70 filled" 70.0 cancelled.Order.filled_qty;
  Alcotest.(check (float 1e-6))
    "notional 7,014, the venue's" 7_014.0 cancelled.Order.filled_notional

let update ?fill cumulative =
  {
    Venue.Update.event = "fill";
    order = venue ~filled:cumulative ~avg:100.0 "partially_filled";
    fill;
    at;
  }

let execution id qty =
  {
    Order.Fill.execution_id = id;
    qty;
    price = Price.of_float 100.0;
    at;
    position_qty = None;
  }

let test_a_late_update_for_reconciled_shares_is_not_counted_twice () =
  let plain = order ~events:[ Order.Event.Acknowledged "v-1" ] 100 in
  Alcotest.(check bool)
    "no reconciliation: a new execution counts" true
    (Reconcile.fill_is_new plain (update ~fill:(execution "x1" 40.0) 40.0));
  let seen = order ~events:[ Order.Event.Acknowledged "v-1"; fill "x1" 40.0 100.0 ] 100 in
  Alcotest.(check bool)
    "a replay does not" false
    (Reconcile.fill_is_new seen (update ~fill:(execution "x1" 40.0) 40.0));
  let reconciled = run plain (Some (venue ~filled:40.0 ~avg:100.0 "partially_filled")) in
  Alcotest.(check bool)
    "the late update for the same 40: cumulative 40 is not past 40" false
    (Reconcile.fill_is_new reconciled (update ~fill:(execution "x1" 40.0) 40.0));
  Alcotest.(check bool)
    "a later 60: cumulative 100 is past 40" true
    (Reconcile.fill_is_new reconciled (update ~fill:(execution "x2" 60.0) 100.0));
  Alcotest.(check bool)
    "an update with no fill is not a fill" false
    (Reconcile.fill_is_new plain (update 0.0))

let suite =
  ( "reconcile",
    [
      Alcotest.test_case "what a restart learns" `Quick test_what_a_restart_learns;
      Alcotest.test_case "a late update for reconciled shares is not counted twice" `Quick
        test_a_late_update_for_reconciled_shares_is_not_counted_twice;
    ] )
