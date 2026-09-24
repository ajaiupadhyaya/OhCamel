(* The simulated venue's trading half, with its clock and its marks in the
   test's hands.

   Cash 100,000, no positions, a half-spread of 5 bps, 200 ms of latency, and
   the arithmetic of every fill written beside it. *)

open Core
open Ohcamel.Types
module Sim = Ohcamel_desk.Sim_venue
module Venue = Ohcamel_desk.Venue
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let aapl = Symbol.of_string "AAPL"
let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let fixture () =
  let now = ref t0 and mark = ref 150.0 in
  let venue =
    Sim.create ~latency:(Time_ns.Span.of_ms 200.0) ~opened_at:t0
      ~marks:(fun s -> if Symbol.equal s aapl then Some (Price.of_float !mark) else None)
      ~now:(fun () -> !now)
      ~half_spread_bps:(fun _ -> 5.0)
      ~cash:(Notional.of_float 100_000.0) ~positions:[] ()
  in
  (venue, now, mark)

let id n =
  Option.value_exn
    (Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ%d" n))

let request ?(side = Order.Side.Buy) ?(kind = Order.Kind.Market) n qty =
  {
    Order.Request.client_order_id = id n;
    symbol = aapl;
    side;
    qty;
    kind;
    tif = Order.Tif.Day;
  }

let later now ms = now := Time_ns.add t0 (Time_ns.Span.of_ms ms)

let test_a_market_buy_fills_at_the_ask_after_the_latency () =
  let venue, now, _ = fixture () in
  (match Sim.For_testing.submit_now venue (request 1 10) with
  | Venue.Submission.Accepted o ->
      Alcotest.(check string) "accepted as new" "new" o.Venue.Venue_order.status
  | _ -> Alcotest.fail "the simulated venue refused a plain market order");
  later now 100.0;
  Alcotest.(check int) "nothing at 100 ms" 0 (List.length (Sim.step venue));
  later now 250.0;
  match Sim.step venue with
  | [ { Venue.Update.event = "fill"; fill = Some f; _ } ] ->
      (* 150 x (1 + 5/10,000) = 150.075 *)
      Alcotest.(check (float 1e-9))
        "at the ask" 150.075
        (Price.to_float f.Order.Fill.price);
      Alcotest.(check (option (float 0.0)))
        "position 10" (Some 10.0) f.Order.Fill.position_qty;
      let a = Or_error.ok_exn (Sim.account venue) in
      (* cash 100,000 - 10 x 150.075 = 98,499.25; equity 98,499.25 + 1,500 = 99,999.25 *)
      Alcotest.(check (float 1e-6))
        "cash" 98_499.25
        (Notional.to_float a.Venue.Account.cash);
      Alcotest.(check (float 1e-6))
        "equity is down exactly the half-spread" 99_999.25
        (Notional.to_float a.Venue.Account.equity)
  | updates ->
      Alcotest.failf "expected one fill at 250 ms, got %d updates" (List.length updates)

let test_a_limit_buy_waits_for_the_ask_to_reach_it () =
  let venue, now, mark = fixture () in
  ignore
    (Sim.For_testing.submit_now venue
       (request ~kind:(Order.Kind.Limit (Price.of_float 149.0)) 2 5)
      : Venue.Submission.t);
  later now 300.0;
  (* ask 150.075 > 149: no fill *)
  Alcotest.(check int) "not at 150" 0 (List.length (Sim.step venue));
  mark := 148.9;
  match Sim.step venue with
  | [ { Venue.Update.fill = Some f; _ } ] ->
      (* ask 148.9 x 1.0005 = 148.97445 <= 149 *)
      Alcotest.(check (float 1e-9))
        "at the ask" 148.97445
        (Price.to_float f.Order.Fill.price)
  | _ -> Alcotest.fail "the limit did not fill once the ask reached it"

let test_a_sell_from_flat_is_a_short () =
  let venue, now, _ = fixture () in
  ignore
    (Sim.For_testing.submit_now venue (request ~side:Order.Side.Sell 3 5)
      : Venue.Submission.t);
  later now 250.0;
  match Sim.step venue with
  | [ { Venue.Update.fill = Some f; _ } ] ->
      (* bid 150 x 0.9995 = 149.925; cash 100,000 + 5 x 149.925 = 100,749.625 *)
      Alcotest.(check (option (float 0.0)))
        "position -5" (Some (-5.0)) f.Order.Fill.position_qty;
      Alcotest.(check (float 1e-6))
        "cash" 100_749.625
        (Notional.to_float (Or_error.ok_exn (Sim.account venue)).Venue.Account.cash)
  | _ -> Alcotest.fail "the sell did not fill"

let test_a_cancelled_order_never_fills () =
  let venue, now, _ = fixture () in
  let o =
    match Sim.For_testing.submit_now venue (request 4 10) with
    | Venue.Submission.Accepted o -> o
    | _ -> Alcotest.fail "refused"
  in
  (match Sim.cancel_now venue o.Venue.Venue_order.id with
  | Ok u -> Alcotest.(check string) "canceled" "canceled" u.Venue.Update.event
  | Error e -> Alcotest.fail (Error.to_string_hum e));
  later now 500.0;
  Alcotest.(check int) "no fill" 0 (List.length (Sim.step venue));
  Alcotest.(check bool)
    "and it can be found by its client id, cancelled" true
    (match Sim.find_now venue (id 4) with
    | Some v -> String.equal v.Venue.Venue_order.status "canceled"
    | None -> false)

let test_venue_events_map_to_order_events () =
  let order =
    {
      Venue.Venue_order.id = "v";
      client_order_id = "c";
      symbol = aapl;
      side = Order.Side.Buy;
      qty = 1.0;
      filled_qty = 0.0;
      filled_avg_price = None;
      status = "new";
      limit_price = None;
    }
  in
  let update event fill = { Venue.Update.event; order; fill; at = t0 } in
  let name u =
    Option.value_map (Venue.Update.to_event u) ~default:"none" ~f:Order.Event.name
  in
  let f =
    {
      Order.Fill.execution_id = "x";
      qty = 1.0;
      price = Price.of_float 1.0;
      at = t0;
      position_qty = None;
    }
  in
  Alcotest.(check (list string))
    "the table"
    [
      "venue_accepted";
      "venue_accepted";
      "venue_accepted";
      "venue_fill";
      "venue_fill";
      "venue_cancelled";
      "venue_expired";
      "venue_rejected";
      "none";
      "none";
      "none";
    ]
    (List.map ~f:name
       [
         update "new" None;
         update "accepted" None;
         update "pending_new" None;
         update "fill" (Some f);
         update "partial_fill" (Some f);
         update "canceled" None;
         update "expired" None;
         update "rejected" None;
         update "done_for_day" None;
         update "pending_cancel" None;
         update "fill" None;
       ])

let suite =
  ( "sim_trade",
    [
      Alcotest.test_case "a market buy fills at the ask after the latency" `Quick
        test_a_market_buy_fills_at_the_ask_after_the_latency;
      Alcotest.test_case "a limit buy waits for the ask to reach it" `Quick
        test_a_limit_buy_waits_for_the_ask_to_reach_it;
      Alcotest.test_case "a sell from flat is a short" `Quick
        test_a_sell_from_flat_is_a_short;
      Alcotest.test_case "a cancelled order never fills" `Quick
        test_a_cancelled_order_never_fills;
      Alcotest.test_case "venue events map to order events" `Quick
        test_venue_events_map_to_order_events;
    ] )
