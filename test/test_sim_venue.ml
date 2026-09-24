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
     at +5) is done and the second closes at +10, dated the second synthetic
     weekday -- Friday 2026-01-02, since 2026-01-01 is a Thursday (the
     derivation is above [test_the_synthetic_calendar_skips_the_weekend]). *)
  let c = Sim.clock (venue ~now:(Time_ns.add t0 (Time_ns.Span.of_min 7.0)) ()) in
  Alcotest.(check bool) "always open" true c.Venue.Session_clock.is_open;
  Alcotest.(check string)
    "the second session closes at 13:40Z" "2026-09-14T13:40:00.000000000Z"
    (Ohcamel_desk.Desk_time.rfc3339 c.Venue.Session_clock.next_close);
  Alcotest.(check string)
    "dated 2026-01-02" "2026-01-02"
    (Date.to_string c.Venue.Session_clock.next_close_date)

(* Task 13a: the synthetic calendar is a WEEKDAY calendar, because the demo's
   clock block and its recorded sessions are read as a trading record and a
   session dated Saturday is a day the market it imitates never had.

   2026-01-01 is a Thursday: 2024-01-01 was a Monday, 2024 is a leap year
   (366 days = 52 x 7 + 2) so 2025-01-01 is a Wednesday, and 2025 has 365 days
   (52 x 7 + 1) so 2026-01-01 is a Thursday. So the first eight sessions are
   Thu 01-01, Fri 01-02, then Sat 01-03 and Sun 01-04 are skipped, Mon 01-05,
   Tue 01-06, Wed 01-07, Thu 01-08, Fri 01-09, then Sat 01-10 and Sun 01-11
   are skipped, Mon 01-12. *)
let test_the_synthetic_calendar_skips_the_weekend () =
  Alcotest.(check (list string))
    "the first eight synthetic sessions, weekdays only"
    [
      "2026-01-01";
      "2026-01-02";
      "2026-01-05";
      "2026-01-06";
      "2026-01-07";
      "2026-01-08";
      "2026-01-09";
      "2026-01-12";
    ]
    (List.init 8 ~f:(fun i -> Date.to_string (Sim.session_date (i + 1))));
  (* There is no zeroth session -- the demo counts from one -- and a caller
     that asked for one is answered with the first session's date rather than
     with the Wednesday before it, which nothing trades on. *)
  Alcotest.(check string)
    "and a zeroth session is the first" "2026-01-01"
    (Date.to_string (Sim.session_date 0));
  (* The clock's own date comes from this same function, so the block on the
     wire and the journal's session rows cannot disagree. A year of sessions,
     none of them on a weekend. *)
  Alcotest.(check (list string))
    "no weekend in the first 260 sessions" []
    (List.init 260 ~f:(fun i -> Sim.session_date (i + 1))
    |> List.filter ~f:(fun d -> Day_of_week.is_sun_or_sat (Date.day_of_week d))
    |> List.map ~f:Date.to_string)

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
      Alcotest.test_case "the synthetic calendar skips the weekend" `Quick
        test_the_synthetic_calendar_skips_the_weekend;
      Alcotest.test_case "a closed session holds an opening-auction order until it opens"
        `Quick test_a_closed_session_holds_an_opening_auction_order_until_it_opens;
    ] )
