(* The simulated venue's read side: the demo host's account and the tests'.

   It has to be right in the ways the real one is right -- equity is cash plus
   signed market value, a short is negative, a quote brackets the mark -- and
   it has to refuse in the one way that matters: a held name with no mark is
   an error, never a zero. *)

open Core
open Ohcamel.Types
module Sim = Ohcamel_desk.Sim_venue
module Venue = Ohcamel_desk.Venue

let aapl = Symbol.of_string "AAPL"
let xom = Symbol.of_string "XOM"
let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T13:30:00Z"

let venue ?(marks = [ (aapl, 150.0); (xom, 100.0) ]) ?(now = t0) () =
  let marks =
    Symbol.Map.of_alist_exn (List.map marks ~f:(fun (s, p) -> (s, Price.of_float p)))
  in
  Sim.create ~opened_at:t0 ~marks:(Map.find marks)
    ~now:(fun () -> now)
    ~half_spread_bps:(fun _ -> 5.0)
    ~cash:(Notional.of_float 100_000.0)
    ~positions:[ (aapl, Qty.of_float 10.0); (xom, Qty.of_float (-20.0)) ]
    ()

let test_equity_is_cash_plus_signed_market_value () =
  let a = Or_error.ok_exn (Sim.account (venue ())) in
  (* 100,000 + 10 x 150 - 20 x 100 = 100,000 + 1,500 - 2,000 = 99,500 *)
  Alcotest.(check (float 1e-9))
    "equity 99,500" 99_500.0
    (Notional.to_float a.Venue.Account.equity);
  Alcotest.(check (float 1e-9))
    "cash as given" 100_000.0
    (Notional.to_float a.Venue.Account.cash)

let test_positions_are_signed_and_sorted () =
  Alcotest.(check (list string))
    "AAPL long 10 worth 1,500; XOM short 20 worth -2,000"
    [ "AAPL 10 1500"; "XOM -20 -2000" ]
    (List.map
       (Sim.positions (venue ()))
       ~f:(fun p ->
         sprintf "%s %g %g"
           (Symbol.to_string p.Venue.Position.symbol)
           (Qty.to_float p.Venue.Position.qty)
           (Notional.to_float (Option.value_exn p.Venue.Position.market_value))))

let test_a_quote_brackets_the_mark_by_the_half_spread () =
  let q = Option.value_exn (Sim.quote (venue ()) xom) in
  (* 5 bps of 100 is 0.05: bid 99.95, ask 100.05, mid 100 *)
  Alcotest.(check (float 1e-9)) "bid" 99.95 (Price.to_float q.Venue.Quote.bid);
  Alcotest.(check (float 1e-9)) "ask" 100.05 (Price.to_float q.Venue.Quote.ask);
  Alcotest.(check (float 1e-9)) "mid" 100.0 (Venue.Quote.mid q)

let test_a_held_name_with_no_mark_is_an_error_not_a_zero () =
  match Sim.account (venue ~marks:[ (aapl, 150.0) ] ()) with
  | Ok a ->
      Alcotest.failf "an account was marked with XOM unpriced: equity %g"
        (Notional.to_float a.Venue.Account.equity)
  | Error e ->
      Alcotest.(check bool)
        "the error names the unpriced symbol" true
        (String.is_substring (Error.to_string_hum e) ~substring:"XOM")

let test_the_synthetic_calendar () =
  (* Five-minute sessions from t0. Seven minutes in, the first session (closed
     at +5) is done and the second closes at +10, dated base_date + 1 day. *)
  let c = Sim.clock (venue ~now:(Time_ns.add t0 (Time_ns.Span.of_min 7.0)) ()) in
  Alcotest.(check bool) "always open" true c.Venue.Session_clock.is_open;
  Alcotest.(check string)
    "the second session closes at 13:40Z" "2026-09-14T13:40:00.000000000Z"
    (Ohcamel_desk.Desk_time.rfc3339 c.Venue.Session_clock.next_close);
  Alcotest.(check string)
    "dated 2026-01-02" "2026-01-02"
    (Date.to_string c.Venue.Session_clock.next_close_date)

(* The closed phase a test sets (Task 15). After Monday's close the clock is
   closed and names Tuesday's open and close; a market-on-open order taken
   then is held -- a step an hour later fills nothing -- until the test opens
   the session, when it fills as a market order does: AAPL 10 bought at 150
   plus half a spread of 5 bps, 150 x 1.0005 = 150.075. *)
let test_a_closed_session_holds_an_opening_auction_order_until_it_opens () =
  let now = ref (Time_ns.of_string_with_utc_offset "2026-09-14T23:15:00Z") in
  let v =
    Sim.create ~opened_at:t0
      ~marks:(fun s -> if Symbol.equal s aapl then Some (Price.of_float 150.0) else None)
      ~now:(fun () -> !now)
      ~half_spread_bps:(fun _ -> 5.0)
      ~cash:(Notional.of_float 100_000.0) ~positions:[] ()
  in
  let next_open = Time_ns.of_string_with_utc_offset "2026-09-15T13:30:00Z" in
  let next_close = Time_ns.of_string_with_utc_offset "2026-09-15T20:00:00Z" in
  Sim.set_session v (Sim.Session.Closed { next_open; next_close });
  let c = Sim.clock v in
  Alcotest.(check (triple bool string string))
    "closed, naming Tuesday's open and close"
    (false, "2026-09-15T13:30:00.000000000Z", "2026-09-15T20:00:00.000000000Z")
    ( c.Venue.Session_clock.is_open,
      Ohcamel_desk.Desk_time.rfc3339 c.Venue.Session_clock.next_open,
      Ohcamel_desk.Desk_time.rfc3339 c.Venue.Session_clock.next_close );
  (match
     Sim.For_testing.submit_now v
       {
         Ohcamel_desk.Order.Request.client_order_id =
           Option.value_exn
             (Ohcamel_desk.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1");
         symbol = aapl;
         side = Ohcamel_desk.Order.Side.Buy;
         qty = 10;
         kind = Ohcamel_desk.Order.Kind.Market;
         tif = Ohcamel_desk.Order.Tif.Opg;
       }
   with
  | Venue.Submission.Accepted _ -> ()
  | _ -> Alcotest.fail "the venue did not take the order");
  now := Time_ns.add !now (Time_ns.Span.of_hr 1.0);
  Alcotest.(check int) "an hour later, still held" 0 (List.length (Sim.step v));
  now := next_open;
  Sim.set_session v
    (Sim.Session.Open
       { next_close; next_open = Time_ns.add next_open (Time_ns.Span.of_day 1.0) });
  Alcotest.(check bool)
    "the clock reads open" true (Sim.clock v).Venue.Session_clock.is_open;
  match Sim.step v with
  | [ { Venue.Update.event = "fill"; fill = Some f; _ } ] ->
      Alcotest.(check (float 1e-9))
        "filled at 150 x 1.0005" 150.075
        (Price.to_float f.Ohcamel_desk.Order.Fill.price);
      Alcotest.(check (float 0.0)) "all 10" 10.0 f.Ohcamel_desk.Order.Fill.qty
  | updates ->
      Alcotest.failf "expected one fill at the open, got %d updates" (List.length updates)

let suite =
  ( "sim_venue",
    [
      Alcotest.test_case "equity is cash plus signed market value" `Quick
        test_equity_is_cash_plus_signed_market_value;
      Alcotest.test_case "positions are signed and sorted" `Quick
        test_positions_are_signed_and_sorted;
      Alcotest.test_case "a quote brackets the mark by the half-spread" `Quick
        test_a_quote_brackets_the_mark_by_the_half_spread;
      Alcotest.test_case "a held name with no mark is an error, not a zero" `Quick
        test_a_held_name_with_no_mark_is_an_error_not_a_zero;
      Alcotest.test_case "the synthetic calendar" `Quick test_the_synthetic_calendar;
      Alcotest.test_case "a closed session holds an opening-auction order until it opens"
        `Quick test_a_closed_session_holds_an_opening_auction_order_until_it_opens;
    ] )
