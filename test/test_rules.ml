(* The rules, each on both sides of its line.

   The base context is an order that passes everything: AAPL in the universe,
   trading enabled, no halt, the session open, a fresh mark of 150, twenty-day
   volume of 16,600 shares, nothing recent, no open orders. Each case moves one
   thing and names the rule that must fail -- except the last, which moves
   three and requires all three named. *)

open Core
open Ohcamel.Types
module Rules = Ohcamel_desk.Rules
module Order = Ohcamel_desk.Order
module Desk_spec = Ohcamel.Config.Book.Desk_spec

let aapl = Symbol.of_string "AAPL"
let now = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"
let spec = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }

let context =
  {
    Rules.Context.spec;
    universe = Symbol.Set.of_list [ aapl; Symbol.of_string "MSFT" ];
    can_trade = Ok ();
    halted = None;
    session_open = true;
    mark = Some (Price.of_float 150.0);
    stale = false;
    adv20 = Some 16_600.0;
    recent = [];
    open_orders = 0;
    now;
  }

let request ?(symbol = aapl) ?(side = Order.Side.Buy) ?(kind = Order.Kind.Market) qty =
  {
    Order.Request.client_order_id =
      Option.value_exn
        (Ohcamel_desk.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZZ");
    symbol;
    side;
    qty;
    kind;
  }

let failed ctx req = List.map (Rules.check ctx req) ~f:(fun f -> f.Rules.Failure.rule)

let check_rules what expected ctx req =
  Alcotest.(check (list string)) what expected (failed ctx req)

let test_an_order_inside_every_line_passes () =
  check_rules "no failures" [] context (request 100)

let test_the_book_and_the_desk_decide_whether_to_trade_at_all () =
  check_rules "a name outside the universe" [ "universe" ] context
    (request ~symbol:(Symbol.of_string "TSLA") 10);
  check_rules "zero shares" [ "whole_shares" ] context (request 0);
  check_rules "a book that disables trading" [ "trading" ]
    { context with spec = Desk_spec.default }
    (request 10);
  check_rules "a desk with no trading half" [ "trading" ]
    { context with can_trade = Error "the venue has no trading half" }
    (request 10);
  check_rules "a tripped switch" [ "kill_switch" ]
    { context with halted = Some "tripped by nvda-cap" }
    (request 10);
  check_rules "a closed session" [ "session" ]
    { context with session_open = false }
    (request 10)

let test_an_order_needs_a_live_mark () =
  (* With no mark there is nothing to price the notional or the collar
     against, so those two are not evaluated -- the mark rule is the reason. *)
  check_rules "no mark" [ "mark" ] { context with mark = None } (request 10);
  check_rules "a stale mark" [ "mark" ] { context with stale = true } (request 10)

let test_the_tick_and_the_collar () =
  let limit p = Order.Kind.Limit (Price.of_float p) in
  check_rules "a whole-cent limit" [] context (request ~kind:(limit 149.5) 10);
  check_rules "a sub-penny limit on a $150 stock" [ "tick" ] context
    (request ~kind:(limit 149.505) 10);
  (* Below a dollar too. The wire sends a limit with two decimals, so a limit
     of 0.1234 would be journaled and gated at 0.1234 and reach the venue as
     0.12. Against a mark of 0.12 both are inside the collar (0.0034 / 0.12 is
     2.8%, under 5%) and 10 shares are 1.20, so the tick is the only line
     either could cross. *)
  let sub_dollar = { context with mark = Some (Price.of_float 0.12) } in
  Alcotest.(check (list (pair string string)))
    "0.1234 under a dollar: refused, 12.34 cents not being whole"
    [ ("tick", "a limit of 0.1234 is not a whole cent") ]
    (List.map
       (Rules.check sub_dollar (request ~kind:(limit 0.1234) 10))
       ~f:(fun f -> (f.Rules.Failure.rule, f.Rules.Failure.why)));
  check_rules "0.12 under a dollar: passes, 12 cents being whole" [] sub_dollar
    (request ~kind:(limit 0.12) 10);
  (* collar 5% of 150 = 7.50: 157.50 is on the line, 157.51 is past it *)
  check_rules "exactly 5% away" [] context (request ~kind:(limit 157.5) 10);
  check_rules "just past 5%" [ "collar" ] context (request ~kind:(limit 157.51) 10)

let test_the_notional_cap () =
  (* 25,000 at 150: 166 x 150 = 24,900 passes; 167 x 150 = 25,050 fails.
     ADV is raised here so the volume rule is not the one that speaks. *)
  let ctx = { context with adv20 = Some 1_000_000.0 } in
  check_rules "166 shares" [] ctx (request 166);
  check_rules "167 shares" [ "notional" ] ctx (request 167)

let test_participation_in_twenty_day_volume () =
  (* 1% of 16,600 = 166 shares *)
  check_rules "166 shares" [] context (request 166);
  check_rules "167 shares" [ "adv" ]
    { context with spec = { spec with Desk_spec.max_order_notional = 1e9 } }
    (request 167);
  check_rules "unknown volume refuses" [ "adv" ] { context with adv20 = None } (request 1)

let test_a_duplicate_within_the_window () =
  let recent seconds_ago qty =
    {
      Rules.Recent.symbol = aapl;
      side = Order.Side.Buy;
      qty;
      at = Time_ns.sub now (Time_ns.Span.of_sec seconds_ago);
    }
  in
  check_rules "the same order 9 s ago" [ "duplicate" ]
    { context with recent = [ recent 9.0 10 ] }
    (request 10);
  check_rules "the same order 11 s ago" []
    { context with recent = [ recent 11.0 10 ] }
    (request 10);
  check_rules "a different quantity 1 s ago" []
    { context with recent = [ recent 1.0 11 ] }
    (request 10)

let test_open_orders () =
  check_rules "19 open" [] { context with open_orders = 19 } (request 10);
  check_rules "20 open" [ "open_orders" ] { context with open_orders = 20 } (request 10)

let test_every_failure_is_reported_in_rule_order () =
  check_rules "three at once"
    [ "universe"; "kill_switch"; "session" ]
    { context with halted = Some "halted by hand"; session_open = false }
    (request ~symbol:(Symbol.of_string "TSLA") 10)

let suite =
  ( "rules",
    [
      Alcotest.test_case "an order inside every line passes" `Quick
        test_an_order_inside_every_line_passes;
      Alcotest.test_case "the book and the desk decide whether to trade at all" `Quick
        test_the_book_and_the_desk_decide_whether_to_trade_at_all;
      Alcotest.test_case "an order needs a live mark" `Quick
        test_an_order_needs_a_live_mark;
      Alcotest.test_case "the tick and the collar" `Quick test_the_tick_and_the_collar;
      Alcotest.test_case "the notional cap" `Quick test_the_notional_cap;
      Alcotest.test_case "participation in twenty-day volume" `Quick
        test_participation_in_twenty_day_volume;
      Alcotest.test_case "a duplicate within the window" `Quick
        test_a_duplicate_within_the_window;
      Alcotest.test_case "open orders" `Quick test_open_orders;
      Alcotest.test_case "every failure is reported, in rule order" `Quick
        test_every_failure_is_reported_in_rule_order;
    ] )
