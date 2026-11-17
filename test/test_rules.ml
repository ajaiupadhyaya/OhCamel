(* The rules, each on both sides of its line.

   The base context is an order that passes everything: AAPL in the universe,
   trading enabled, no halt, the session open -- Monday 2026-09-14 at 10:00
   EDT, on a clock that names the close at 16:00 and Tuesday's open at 09:30
   -- a fresh mark of 150, twenty-day volume of 16,600 shares, nothing recent,
   no open orders. Each case moves one thing and names the rule that must
   fail -- except the last, which moves three and requires all three named. *)

open Core
open Ohcamel.Types
module Rules = Ohcamel_desk.Rules
module Order = Ohcamel_desk.Order
module Halt = Ohcamel_desk.Halt
module Desk_spec = Ohcamel.Config.Book.Desk_spec

let aapl = Symbol.of_string "AAPL"
let at = Time_ns.of_string_with_utc_offset
let now = at "2026-09-14T14:00:00Z"

(* The venue's clock as Alpaca answers it during Monday's session: the close
   at 16:00 EDT (20:00Z), and the next open Tuesday at 09:30 EDT (13:30Z). *)
let during_monday =
  Some
    {
      Rules.Session.next_open = at "2026-09-15T13:30:00Z";
      next_close = at "2026-09-14T20:00:00Z";
    }

let spec = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }

let context =
  {
    Rules.Context.spec;
    universe = Symbol.Set.of_list [ aapl; Symbol.of_string "MSFT" ];
    can_trade = Ok ();
    halt_state = Halt.State.Clear;
    halt_why = None;
    own = 0.0;
    account = 0.0;
    netted = false;
    session = during_monday;
    mark = Some (Price.of_float 150.0);
    stale = false;
    origin = Rules.Origin.Hand;
    new_york = None;
    adv20 = Some 16_600.0;
    recent = [];
    open_orders = 0;
    resting = [];
    source = "manual";
    now;
  }

let request ?(symbol = aapl) ?(side = Order.Side.Buy) ?(kind = Order.Kind.Market)
    ?(tif = Order.Tif.Day) qty =
  {
    Order.Request.client_order_id =
      Option.value_exn
        (Ohcamel_desk.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZZ");
    symbol;
    side;
    qty;
    kind;
    tif;
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
    {
      context with
      halt_state = Halt.State.Tripped { limit = "nvda-cap"; at = now };
      halt_why = Some "tripped by nvda-cap";
    }
    (request 10);
  check_rules "no clock read, a closed session" [ "session" ]
    { context with session = None }
    (request 10)

(* The session, read at the order's own time from the clock's two times,
   and never from a flag set when the clock was read. On Monday's clock an
   order a nanosecond before 16:00 passes; one at 16:00 is refused, and so is
   one at 16:00:30, inside the minute a flag read at 15:59:30 still said open.
   A clock read overnight names Tuesday's open first: an order at 04:00 or a
   nanosecond before 09:30 is refused, one at 09:30 passes with no second
   read, and Tuesday's own close ends it. A clock whose two times are both
   still ahead is not "open" for that alone: read overnight, the next close
   is in the future and so is the next open. *)
let test_the_session_is_read_at_the_order's_own_time () =
  let overnight =
    Some
      {
        Rules.Session.next_open = at "2026-09-15T13:30:00Z";
        next_close = at "2026-09-15T20:00:00Z";
      }
  in
  let nanosecond_before s = Time_ns.sub (at s) Time_ns.Span.nanosecond in
  let check what expected session now =
    check_rules what expected { context with session; now } (request 10)
  in
  check "Monday 15:59:59.999999999" [] during_monday
    (nanosecond_before "2026-09-14T20:00:00Z");
  check "Monday 16:00, the close" [ "session" ] during_monday (at "2026-09-14T20:00:00Z");
  check "Monday 16:00:30, the minute a flag would have missed" [ "session" ] during_monday
    (at "2026-09-14T20:00:30Z");
  check "Tuesday 04:00, both times ahead" [ "session" ] overnight
    (at "2026-09-15T08:00:00Z");
  check "Tuesday 09:29:59.999999999" [ "session" ] overnight
    (nanosecond_before "2026-09-15T13:30:00Z");
  check "Tuesday 09:30, the open" [] overnight (at "2026-09-15T13:30:00Z");
  check "Tuesday 16:00, that session's close" [ "session" ] overnight
    (at "2026-09-15T20:00:00Z")

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
      source = "manual";
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

(* Two strategies may want the same order: the rule is one proposer's
   double-click, not two strategies' agreement (Task 9). SPY Buy 10 from
   signal:a:1, 5 s ago, inside the 10 s window: signal:b:1's SPY Buy 10
   passes, and signal:a:1's again is the duplicate. *)
let test_duplicate_is_per_source () =
  let spy = Symbol.of_string "SPY" in
  let ctx =
    {
      context with
      universe = Set.add context.universe spy;
      recent =
        [
          {
            Rules.Recent.symbol = spy;
            side = Order.Side.Buy;
            qty = 10;
            at = Time_ns.sub now (Time_ns.Span.of_sec 5.0);
            source = "signal:a:1";
          };
        ];
    }
  in
  check_rules "another source" []
    { ctx with source = "signal:b:1" }
    (request ~symbol:spy 10);
  check_rules "the same source" [ "duplicate" ]
    { ctx with source = "signal:a:1" }
    (request ~symbol:spy 10)

let test_open_orders () =
  (* The default max_open_orders, 200 (lib/config.ml): 199 rest and one more
     may, 200 rest and none may. *)
  check_rules "199 open" [] { context with open_orders = 199 } (request 10);
  check_rules "200 open" [ "open_orders" ] { context with open_orders = 200 } (request 10)

(* The venue's wash-trade rule, asked before the wire: an order of the
   desk's own resting on the other side of the name refuses the proposal,
   whoever proposed either; the same side does not, nor another name. It is
   a timing refusal -- the resting order fills or expires -- so an intake
   keeps the judgement pending rather than closing it. *)
let test_an_opposite_resting_order_is_a_wash () =
  let spy = Symbol.of_string "SPY" in
  let ctx =
    { context with resting = [ (aapl, Order.Side.Sell); (spy, Order.Side.Buy) ] }
  in
  check_rules "a buy against the desk's own resting sell" [ "wash" ] ctx (request 10);
  check_rules "a sell beside it" [] ctx (request ~side:Order.Side.Sell 10);
  check_rules "another name" [] ctx (request ~symbol:(Symbol.of_string "MSFT") 10);
  Alcotest.(check bool)
    "it names the resting side and the name" true
    (List.exists
       (Rules.check ctx (request 10))
       ~f:(fun f ->
         String.equal f.Rules.Failure.rule "wash"
         && String.is_substring f.Rules.Failure.why ~substring:"a sell of AAPL"));
  let f rule = { Rules.Failure.rule; why = "x" } in
  Alcotest.(check (list bool))
    "timing: wash alone, wash with session; final: wash with a cap" [ true; true; false ]
    [
      Rules.is_timing_refusal ~tif:Order.Tif.Day [ f "wash" ];
      Rules.is_timing_refusal ~tif:Order.Tif.Day [ f "session"; f "wash" ];
      Rules.is_timing_refusal ~tif:Order.Tif.Day [ f "wash"; f "notional" ];
    ]

let test_every_failure_is_reported_in_rule_order () =
  check_rules "three at once"
    [ "universe"; "kill_switch"; "session" ]
    {
      context with
      halt_state = Halt.State.Halted { why = "halted by hand"; at = now };
      halt_why = Some "halted by hand";
      session = None;
    }
    (request ~symbol:(Symbol.of_string "TSLA") 10)

(* ------------------------------------------------------------------------ *)
(* An order that only shrinks a position, through a limit's trip (R2)        *)
(* ------------------------------------------------------------------------ *)

(* The switch as each state leaves it, the book's (reduce_through_trip) word
   on unless a case turns it off, and the strategy's own position and the
   account's in AAPL as the case gives them. [netted] -- the name's
   sub-ledger known to sum (Task 1.6) -- is false unless a case sets it, so
   every case written for Task 1.2 still judges the 1.2 line alone: the
   strategy's own position and the account's both shrink. *)
let switched ?(on = true) ?(netted = false) ~state ~own ~account () =
  let halt_state, halt_why =
    match state with
    | `Tripped ->
        ( Halt.State.Tripped { limit = "aapl-cap"; at = now },
          Some "the kill switch was tripped by aapl-cap at 2026-09-14T14:00:00Z" )
    | `Halted ->
        ( Halt.State.Halted { why = "halted by hand"; at = now },
          Some "the desk was halted by hand at 2026-09-14T14:00:00Z: halted by hand" )
    | `Stopped ->
        ( Halt.State.Stopped { why = "the stream ended"; at = now },
          Some "the desk was stopped by the engine: the stream ended" )
  in
  {
    context with
    spec = { spec with Desk_spec.reduce_through_trip = on };
    halt_state;
    halt_why;
    own;
    account;
    netted;
  }

let sell = request ~side:Order.Side.Sell
let buy = request ~side:Order.Side.Buy

(* The brief's cases, long: own +100, the account +80. A sell of 50 shrinks
   both and is no larger than either, so it passes the trip; 90 is more than
   the account's 80; 120 is more than both, and would take the strategy
   through zero to short -- refused whole, never clipped to 80 or 100,
   because the rules only refuse. Any buy grows the position. A hand's halt
   and the engine's stop refuse the sell of 50, and so does the trip itself
   with the word off. *)
let test_a_sell_that_shrinks_a_long_passes_a_limit's_trip () =
  let tripped = switched ~state:`Tripped ~own:100.0 ~account:80.0 () in
  check_rules "sell 50 against own +100, account +80" [] tripped (sell 50);
  check_rules "sell 90: more than the account's 80" [ "kill_switch" ] tripped (sell 90);
  check_rules "sell 120: through zero" [ "kill_switch" ] tripped (sell 120);
  check_rules "sell 120 inside the account's 400, through the strategy's zero"
    [ "kill_switch" ]
    (switched ~state:`Tripped ~own:100.0 ~account:400.0 ())
    (sell 120);
  check_rules "sell 120 inside the strategy's 400, through the account's zero"
    [ "kill_switch" ]
    (switched ~state:`Tripped ~own:400.0 ~account:100.0 ())
    (sell 120);
  check_rules "sell 80: exactly the account's whole position" [] tripped (sell 80);
  check_rules "buy 50: grows it" [ "kill_switch" ] tripped (buy 50);
  check_rules "a hand's halt refuses the sell of 50" [ "kill_switch" ]
    (switched ~state:`Halted ~own:100.0 ~account:80.0 ())
    (sell 50);
  check_rules "the engine's stop refuses the sell of 50" [ "kill_switch" ]
    (switched ~state:`Stopped ~own:100.0 ~account:80.0 ())
    (sell 50);
  check_rules "the word off refuses the sell of 50" [ "kill_switch" ]
    (switched ~on:false ~state:`Tripped ~own:100.0 ~account:80.0 ())
    (sell 50)

(* The same, short: own -100, the account -80. A buy to cover 50 passes; 90
   is more than the account's 80; 120 would take it through zero to long,
   and is refused whole; any sell grows the short. *)
let test_a_buy_that_covers_a_short_passes_a_limit's_trip () =
  let tripped = switched ~state:`Tripped ~own:(-100.0) ~account:(-80.0) () in
  check_rules "buy 50 against own -100, account -80" [] tripped (buy 50);
  check_rules "buy 90: more than the account's 80" [ "kill_switch" ] tripped (buy 90);
  check_rules "buy 120: through zero" [ "kill_switch" ] tripped (buy 120);
  check_rules "buy 120 inside the account's -400, through the strategy's zero"
    [ "kill_switch" ]
    (switched ~state:`Tripped ~own:(-100.0) ~account:(-400.0) ())
    (buy 120);
  check_rules "sell 50: grows the short" [ "kill_switch" ] tripped (sell 50);
  check_rules "a hand's halt refuses the buy of 50" [ "kill_switch" ]
    (switched ~state:`Halted ~own:(-100.0) ~account:(-80.0) ())
    (buy 50)

(* When in doubt, refused: nothing held by either, the strategy flat, the
   account flat, or the two on opposite sides is no reduction of both. *)
let test_what_is_not_a_reduction_of_both_is_refused () =
  List.iter
    [
      ("both flat", 0.0, 0.0);
      ("the strategy flat", 0.0, 80.0);
      ("the account flat", 100.0, 0.0);
      ("the strategy long, the account short", 100.0, -80.0);
      ("the strategy short, the account long", -100.0, 80.0);
    ]
    ~f:(fun (what, own, account) ->
      check_rules (what ^ ": sell 10") [ "kill_switch" ]
        (switched ~state:`Tripped ~own ~account ())
        (sell 10);
      check_rules (what ^ ": buy 10") [ "kill_switch" ]
        (switched ~state:`Tripped ~own ~account ())
        (buy 10))

(* [Rules.reduces] itself, which the order manager asks again at the wire:
   the share counts on both sides of the line, whole and through zero. *)
let test_reduces_is_the_line () =
  let r ~own ~account side qty = Rules.reduces ~own ~account ~netted:false ~side ~qty in
  Alcotest.(check (list bool))
    "sell 50 / 80 / 81 / 120 against +100 and +80; buy 50; sell 0"
    [ true; true; false; false; false; false ]
    [
      r ~own:100.0 ~account:80.0 Order.Side.Sell 50;
      r ~own:100.0 ~account:80.0 Order.Side.Sell 80;
      r ~own:100.0 ~account:80.0 Order.Side.Sell 81;
      r ~own:100.0 ~account:80.0 Order.Side.Sell 120;
      r ~own:100.0 ~account:80.0 Order.Side.Buy 50;
      r ~own:100.0 ~account:80.0 Order.Side.Sell 0;
    ];
  Alcotest.(check (list bool))
    "buy 80 / 81 against -100 and -80; a nan position" [ true; false; false ]
    [
      r ~own:(-100.0) ~account:(-80.0) Order.Side.Buy 80;
      r ~own:(-100.0) ~account:(-80.0) Order.Side.Buy 81;
      r ~own:Float.nan ~account:80.0 Order.Side.Sell 10;
    ]

(* Under netting (Task 1.6): alpha long 100 and beta short 50 in one name,
   the account the net +50, the sub-ledger whole ([netted]). Each flatten
   passes the trip -- alpha's sell of 100 is more than the account's 50, and
   beta's buy grows the account's long, so neither passes the 1.2 line
   alone, which the cases above keep with [netted] false. A flip of either
   strategy's own position is refused whole, so is any increase of it, and a
   hand's halt still refuses everything. *)
let test_a_netted_pair's_flattens_pass_a_limit's_trip () =
  let alpha ?(netted = true) () =
    switched ~netted ~state:`Tripped ~own:100.0 ~account:50.0 ()
  in
  let beta ?(netted = true) () =
    switched ~netted ~state:`Tripped ~own:(-50.0) ~account:50.0 ()
  in
  check_rules "alpha sells its 100" [] (alpha ()) (sell 100);
  check_rules "beta covers its 50" [] (beta ()) (buy 50);
  check_rules "alpha sells 150: through its zero" [ "kill_switch" ] (alpha ()) (sell 150);
  check_rules "beta buys 60: through its zero" [ "kill_switch" ] (beta ()) (buy 60);
  check_rules "alpha buys 10: grows it" [ "kill_switch" ] (alpha ()) (buy 10);
  check_rules "beta sells 10: grows its short" [ "kill_switch" ] (beta ()) (sell 10);
  check_rules "alpha's 100, the sum not known" [ "kill_switch" ] (alpha ~netted:false ())
    (sell 100);
  check_rules "beta's cover, the sum not known" [ "kill_switch" ] (beta ~netted:false ())
    (buy 50);
  check_rules "a hand's halt refuses alpha's sale" [ "kill_switch" ]
    (switched ~netted:true ~state:`Halted ~own:100.0 ~account:50.0 ())
    (sell 100);
  check_rules "a ticket owns nothing, netted or not" [ "kill_switch" ]
    (switched ~netted:true ~state:`Tripped ~own:0.0 ~account:50.0 ())
    (sell 10);
  let r ~own ~account side qty = Rules.reduces ~own ~account ~netted:true ~side ~qty in
  Alcotest.(check (list bool))
    "netted: the strategy's own is the whole line"
    [ true; true; false; false; false ]
    [
      r ~own:100.0 ~account:50.0 Order.Side.Sell 100;
      r ~own:(-50.0) ~account:50.0 Order.Side.Buy 50;
      r ~own:100.0 ~account:50.0 Order.Side.Sell 101;
      r ~own:Float.nan ~account:50.0 Order.Side.Sell 10;
      r ~own:100.0 ~account:50.0 Order.Side.Sell 0;
    ]

(* A refusal under the trip still quotes the switch's own words -- a restored
   trip's "(held across a restart)" among them -- and says what would pass. *)
let test_a_trip's_refusal_quotes_the_switch () =
  let why =
    List.map
      (Rules.check
         {
           (switched ~state:`Tripped ~own:100.0 ~account:80.0 ()) with
           halt_why =
             Some
               "the kill switch was tripped by aapl-cap at 2026-09-14T14:00:00Z (held \
                across a restart)";
         }
         (sell 90))
      ~f:(fun f -> f.Rules.Failure.why)
  in
  Alcotest.(check bool)
    (sprintf "the switch's words, and the line: %s" (String.concat why))
    true
    (match why with
    | [ w ] ->
        String.is_substring w ~substring:"(held across a restart)"
        && String.is_substring w ~substring:"shrinks"
    | _ -> false)

(* ------------------------------------------------------------------------ *)
(* The desk's own orders: from 09:35, off a fresh quote (Task 1.7)           *)
(* ------------------------------------------------------------------------ *)

let new_york = Timezone.find "America/New_York"

(* A quote stamped [age] seconds before [now] (after it, for a negative
   age): 149.95 x 150.05 around the mark of 150 unless a case says. *)
let quote ?(bid = 149.95) ?(ask = 150.05) ?(at = now) age =
  Some
    {
      Ohcamel_desk.Venue.Quote.bid = Price.of_float bid;
      ask = Price.of_float ask;
      at = Time_ns.sub at (Time_ns.Span.of_sec age);
    }

(* A desk order's context: Monday's session, the zone loaded, and a quote
   one second old. Its limit, 150.21, is the ask moved 10 bps up and
   rounded up to the cent -- 150.05 x 1.001 = 150.20005. *)
let desk ?(q = quote 1.0) ?(at = now) () =
  { context with origin = Rules.Origin.Desk q; new_york; now = at }

let desk_buy = request ~kind:(Order.Kind.Limit (Price.of_float 150.21))

let why ctx req =
  List.map (Rules.check ctx req) ~f:(fun f ->
      f.Rules.Failure.rule ^ ": " ^ f.Rules.Failure.why)

let says what ~substring ctx req =
  Alcotest.(check bool)
    (sprintf "%s: %s" what (String.concat ~sep:" | " (why ctx req)))
    true
    (List.exists (why ctx req) ~f:(String.is_substring ~substring))

let nanosecond_before s = Time_ns.sub (at s) Time_ns.Span.nanosecond

(* Nothing the desk decides is sent before 09:35 New York. 09:35 is 13:35Z
   under daylight saving and 14:35Z under standard time, so the line is
   tried on both sides in each: Monday 14 September 2026 (EDT) and Monday 7
   December 2026 (EST), whose session the clock says is open from 09:30. A
   fixed offset of -4 would call 14:34Z in December 09:34 and refuse nothing
   at 10:34 EST -- or, the other way about, let an order through at 08:35.
   A person's ticket is sent as it is priced, from the open. With no zone no
   desk order goes, saying why; a ticket still does. *)
let test_a_desk_order_waits_for_09_35_new_york_in_both_seasons () =
  let monday = during_monday in
  let december =
    Some
      {
        Rules.Session.next_open = at "2026-12-08T14:30:00Z";
        next_close = at "2026-12-07T21:00:00Z";
      }
  in
  let check what expected session now =
    check_rules what expected
      { (desk ~q:(quote ~at:now 1.0) ~at:now ()) with session }
      (desk_buy 100)
  in
  check "EDT 09:34:59.999999999" [ "session" ] monday
    (nanosecond_before "2026-09-14T13:35:00Z");
  check "EDT 09:35" [] monday (at "2026-09-14T13:35:00Z");
  check "EST 09:34:59.999999999" [ "session" ] december
    (nanosecond_before "2026-12-07T14:35:00Z");
  check "EST 09:35 (14:35Z, which a fixed -4 would call 10:35)" [] december
    (at "2026-12-07T14:35:00Z");
  says "the refusal names the hour and 09:35"
    ~substring:"it is 2026-09-14 09:31 ET: the desk sends its own orders from 09:35 ET"
    (desk
       ~q:(quote ~at:(at "2026-09-14T13:31:00Z") 1.0)
       ~at:(at "2026-09-14T13:31:00Z") ())
    (desk_buy 100);
  check_rules "a ticket at 09:31 goes" []
    { context with new_york; now = at "2026-09-14T13:31:00Z" }
    (request 100);
  says "no zone, no desk order" ~substring:"09:35 ET cannot be told"
    { (desk ()) with new_york = None }
    (desk_buy 100);
  check_rules "no zone, and a ticket still goes" [] context (request 100);
  check_rules "a closed session refuses the desk's order too" [ "session" ]
    { (desk ()) with session = None }
    (desk_buy 100)

(* The desk's order is priced off the quote fetched as it is sent, and with
   no usable one it is refused, rule [quote]: none answered; one more than
   quote_max_age_s (5 s) old -- exactly 5 s is inside; one stamped more than
   5 s ahead of the desk's clock; crossed; one-sided (Alpaca's 0 for no
   bid); not a number. A locked quote (bid = ask) prices. A ticket never
   asks for one. *)
let test_a_desk_order_with_no_fresh_quote_is_refused () =
  let refused what ?(substring = "") q =
    check_rules what [ "quote" ] (desk ~q ()) (desk_buy 100);
    says what ~substring (desk ~q ()) (desk_buy 100)
  in
  check_rules "a quote 1 s old prices it" [] (desk ()) (desk_buy 100);
  check_rules "a quote exactly 5 s old still does" []
    (desk ~q:(quote 5.0) ())
    (desk_buy 100);
  refused "none answered" ~substring:"AAPL: no quote answered for it" None;
  refused "5.001 s old" ~substring:"within 5 s" (quote 5.001);
  refused "6 s ahead of the clock" (quote (-6.0));
  refused "crossed" ~substring:"crossed" (quote ~bid:150.10 ~ask:150.05 1.0);
  refused "no bid" ~substring:"one-sided" (quote ~bid:0.0 1.0);
  refused "not a number" ~substring:"one-sided" (quote ~ask:Float.nan 1.0);
  check_rules "locked prices" []
    (desk ~q:(quote ~bid:150.05 ~ask:150.05 1.0) ())
    (desk_buy 100);
  check_rules "the book's bound, 10 s, lets a 6 s quote through" []
    {
      (desk ~q:(quote 6.0) ()) with
      spec = { spec with Desk_spec.quote_max_age_s = 10.0 };
    }
    (desk_buy 100);
  check_rules "a ticket asks for none" [] context (request 100)

(* The collar and the order cap are evaluated against the limit actually
   sent. A buy of 166 at a limit of 151.00 is 25,066, over the 25,000 cap,
   though at the mark of 150 it would be 24,900 and pass; at 150.21 it is
   24,934.86 and passes. A sell of 166 at 149.79 is 24,865.14. The collar
   reads the limit against the mark, as it does a ticket's. *)
let test_collar_and_notional_read_the_limit_sent () =
  let ctx = { (desk ()) with adv20 = Some 1_000_000.0 } in
  let limit p = Order.Kind.Limit (Price.of_float p) in
  check_rules "166 at 150.21" [] ctx (request ~kind:(limit 150.21) 166);
  check_rules "166 at 151.00" [ "notional" ] ctx (request ~kind:(limit 151.0) 166);
  says "computed at the limit" ~substring:"166 x 151.00 = 25066.00" ctx
    (request ~kind:(limit 151.0) 166);
  check_rules "a sell of 166 at 149.79" [] ctx
    (request ~side:Order.Side.Sell ~kind:(limit 149.79) 166);
  check_rules "a limit past the collar" [ "collar" ] ctx (request ~kind:(limit 157.51) 10)

(* No market-on-open order is built, and one that reached the rules -- an
   old journal row's tif -- would be refused by the session rule, whoever
   sent it. *)
let test_a_market_on_open_order_is_refused () =
  says "a ticket's" ~substring:"no market-on-open order" context
    (request ~tif:Order.Tif.Opg 100);
  says "the desk's" ~substring:"no market-on-open order" (desk ())
    (request ~tif:Order.Tif.Opg 100)

(* ------------------------------------------------------------------------ *)
(* A market-on-close order (Order.Tif.Cls): until ten minutes before close   *)
(* ------------------------------------------------------------------------ *)

(* Monday's session, its close at 16:00 EDT (20:00Z): the cut-off is 15:50
   EDT (19:50Z). At 15:30 EDT (19:30Z) a market-on-close order passes, priced
   at the live mark as a day order is; at 15:51 (19:51Z) it is refused,
   naming 15:50; after the close, at 16:30 EDT (20:30Z) on a clock read then
   -- naming Tuesday's open first -- it is refused because the session is
   closed. *)
(* After Monday's close: the clock names Tuesday's open at 09:30 EDT (13:30Z)
   and its close at 16:00 EDT (20:00Z). *)
let after_monday =
  Some
    {
      Rules.Session.next_open = at "2026-09-15T13:30:00Z";
      next_close = at "2026-09-15T20:00:00Z";
    }

let test_a_market_on_close_order_until_ten_minutes_before_the_close () =
  let cls = request ~tif:Order.Tif.Cls in
  let monday now = { context with now = at now; new_york } in
  check_rules "15:30 EDT" [] (monday "2026-09-14T19:30:00Z") (cls 100);
  check_rules "15:51 EDT" [ "session" ] (monday "2026-09-14T19:51:00Z") (cls 100);
  says "the refusal names the cut-off"
    ~substring:
      "it is 2026-09-14 15:51 ET, not before 2026-09-14 15:50 ET, ten minutes before the \
       close the clock names (2026-09-14 16:00 ET)"
    (monday "2026-09-14T19:51:00Z")
    (cls 100);
  check_rules "16:30 EDT, closed" [ "session" ]
    { (monday "2026-09-14T20:30:00Z") with session = after_monday }
    (cls 100);
  says "closed, in the rule's words" ~substring:"the regular session is closed"
    { (monday "2026-09-14T20:30:00Z") with session = after_monday }
    (cls 100)

(* A refusal of timing alone (the stage review's A-I1): every failure the
   session rule's, the wash rule's or the quote rule's -- or a stale mark
   beside the session's, since outside the session every name is stale.
   Anything else beside them is final; no failure is no refusal. *)
let test_a_timing_refusal_is_the_session_rule_alone () =
  let f rule = { Rules.Failure.rule; why = "x" } in
  let timing fs = Rules.is_timing_refusal ~tif:Order.Tif.Day fs in
  Alcotest.(check (list bool))
    "session alone; session and notional; mark alone; none" [ true; false; false; false ]
    [
      timing [ f "session" ];
      timing [ f "session"; f "notional" ];
      timing [ f "mark" ];
      timing [];
    ];
  Alcotest.(check (list bool))
    "session and a stale mark; the switch beside it; quote alone; quote and a cap"
    [ true; false; true; false ]
    [
      timing [ f "session"; f "mark" ];
      timing [ f "session"; f "kill_switch" ];
      timing [ f "quote" ];
      timing [ f "quote"; f "notional" ];
    ]

let suite =
  ( "rules",
    [
      Alcotest.test_case "a timing refusal is the session rule alone" `Quick
        test_a_timing_refusal_is_the_session_rule_alone;
      Alcotest.test_case
        "Cls legal at 15:30, refused at 15:51 naming the cut-off, refused when closed"
        `Quick test_a_market_on_close_order_until_ten_minutes_before_the_close;
      Alcotest.test_case "an order inside every line passes" `Quick
        test_an_order_inside_every_line_passes;
      Alcotest.test_case "the book and the desk decide whether to trade at all" `Quick
        test_the_book_and_the_desk_decide_whether_to_trade_at_all;
      Alcotest.test_case "the session is read at the order's own time" `Quick
        test_the_session_is_read_at_the_order's_own_time;
      Alcotest.test_case "an order needs a live mark" `Quick
        test_an_order_needs_a_live_mark;
      Alcotest.test_case "the tick and the collar" `Quick test_the_tick_and_the_collar;
      Alcotest.test_case "the notional cap" `Quick test_the_notional_cap;
      Alcotest.test_case "participation in twenty-day volume" `Quick
        test_participation_in_twenty_day_volume;
      Alcotest.test_case "a duplicate within the window" `Quick
        test_a_duplicate_within_the_window;
      Alcotest.test_case "duplicate is per source" `Quick test_duplicate_is_per_source;
      Alcotest.test_case "open orders" `Quick test_open_orders;
      Alcotest.test_case "every failure is reported, in rule order" `Quick
        test_every_failure_is_reported_in_rule_order;
      Alcotest.test_case "a sell that shrinks a long passes a limit's trip" `Quick
        test_a_sell_that_shrinks_a_long_passes_a_limit's_trip;
      Alcotest.test_case "a buy that covers a short passes a limit's trip" `Quick
        test_a_buy_that_covers_a_short_passes_a_limit's_trip;
      Alcotest.test_case "what is not a reduction of both is refused" `Quick
        test_what_is_not_a_reduction_of_both_is_refused;
      Alcotest.test_case "reduces is the line" `Quick test_reduces_is_the_line;
      Alcotest.test_case "a netted pair's flattens pass a limit's trip" `Quick
        test_a_netted_pair's_flattens_pass_a_limit's_trip;
      Alcotest.test_case "a trip's refusal quotes the switch" `Quick
        test_a_trip's_refusal_quotes_the_switch;
      Alcotest.test_case "an opposite resting order is a wash" `Quick
        test_an_opposite_resting_order_is_a_wash;
      Alcotest.test_case "a desk order waits for 09:35 New York, in EDT and in EST" `Quick
        test_a_desk_order_waits_for_09_35_new_york_in_both_seasons;
      Alcotest.test_case "a desk order with no fresh quote is refused" `Quick
        test_a_desk_order_with_no_fresh_quote_is_refused;
      Alcotest.test_case "the collar and the order cap read the limit sent" `Quick
        test_collar_and_notional_read_the_limit_sent;
      Alcotest.test_case "a market-on-open order is refused" `Quick
        test_a_market_on_open_order_is_refused;
    ] )
