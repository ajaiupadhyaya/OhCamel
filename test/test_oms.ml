(* The order manager's preview, on the gate's book:

     AAPL  400 x 150 = 60,000   TECH
     MSFT  100 x 300 = 30,000   TECH
     XOM  -200 x 100 = -20,000  ENERGY

   Limits aapl-cap 80,000, tech-cap 100,000, book-cap 200,000. Every name has
   printed just now, the session is open, twenty-day volume is a fixed
   1,000,000 shares (1% of it is 10,000), and the order cap is 25,000. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let money = Notional.of_float
let price = Price.of_float
let limit name scope n = { Limit.name; scope; kind = Limit.Gross_notional (money n) }

(* [book_is_current] is true unless a case says otherwise: every case but one
   gates an order against a book the account holds. The trailing unit is what
   lets that default be taken, as test_gate.ml's [with_book] takes its own:
   an optional argument before the last labelled one is never erased. *)
let with_oms ?(book_is_current = true) ?(now = Time_ns.now) ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        [
          limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
          limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
          limit "book-cap" Limit.Portfolio 200_000.0;
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter
        [ (aapl, 150.0, 400.0); (msft, 300.0, 100.0); (xom, 100.0, -200.0) ]
        ~f:(fun (s, p, q) ->
          Graph.set_qty graph s (Qty.of_float q);
          Graph.set_returns graph s
            [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
          Graph.apply_tick graph
            { Tick.symbol = s; price = Price.of_float p; time = Time.now () });
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun s -> Some (Graph.price graph s))
          ~now:Time_ns.now
          ~half_spread_bps:(fun _ -> 5.0)
          ~cash:(money 1_000_000.0) ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0) ~now
          ~rng:(Random.State.make [| 3 |]) ~on_change:ignore ~on_event:ignore
          ~after_fill:ignore
          ~book_is_current:(fun () -> book_is_current)
          ()
      in
      D.Oms.set_market oms ~session_open:true ~adv20:[];
      f oms journal)

let ticket ?(kind = D.Order.Kind.Market) symbol side qty =
  { D.Ticket.symbol; side; qty; kind }

let rules (p : D.Oms.Preview.t) =
  List.map p.D.Oms.Preview.failures ~f:(fun x -> x.D.Rules.Failure.rule)

let created (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.verdict with
  | Some v ->
      List.map v.Ohcamel.Gate.Verdict.created ~f:(fun m -> m.Ohcamel.Gate.Move.limit)
  | None -> []

(* A resting order, injected exactly as [Oms.propose] leaves one once the
   venue has acknowledged it: journaled first (invariant 10, [Journal.insert_order]),
   then run through [Oms.record]'s own [Acknowledged], which is what moves an
   order into [oms.open_]. No venue call is made -- these tests are about what
   the gate does with an order already resting, not about getting one there --
   but the two functions doing the work are the ones production calls. *)
let rest oms journal ~seed symbol side qty kind =
  let client_order_id =
    D.Ids.Client_order_id.generate ~now:(Time_ns.now ())
      ~rng:(Random.State.make [| seed |])
  in
  let request = { D.Order.Request.client_order_id; symbol; side; qty; kind } in
  let o = D.Order.create request in
  D.Journal.insert_order journal o ~source:"test" ~decision_price:(price 0.0)
    ~arrival:None ~verdict:`Null ~at:(Time_ns.now ());
  D.Oms.record oms o (D.Order.Event.Acknowledged (sprintf "venue-%d" seed))

(* The venue confirming a cancel: pending_cancel, then cancelled -- the same
   two steps [cancel] and its confirmation leave behind. *)
let cancel_resting oms o =
  let o = D.Oms.record oms o D.Order.Event.Cancel_requested in
  D.Oms.record oms o D.Order.Event.Venue_cancelled

let test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing () =
  with_oms
    ~f:(fun oms journal ->
      (* 100 x 150 = 15,000: under the order cap and 1% of volume, so every
         rule passes. TECH 75,000 + 30,000 = 105,000 > 100,000. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 100) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string)) "by tech-cap" [ "tech-cap" ] (created p);
      Alcotest.(check bool)
        "and the reasons name it" true
        (List.exists (D.Oms.Preview.reasons p)
           ~f:(String.is_substring ~substring:"tech-cap"));
      Alcotest.(check int)
        "nothing was journaled" 0
        (List.length (D.Journal.recent_orders journal ~limit:10)))
    ()

let test_the_rules_and_the_limits_both_speak () =
  with_oms
    ~f:(fun oms _ ->
      (* 200 x 150 = 30,000 > 25,000: the notional rule, and no other -- 200
         shares is under 1% of 1,000,000. AAPL 600 x 150 = 90,000 > 80,000
         and TECH 90,000 + 30,000 = 120,000 > 100,000: two limits, in the
         order they are configured. One rule and two limits: three reasons. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 200) in
      Alcotest.(check (list string)) "the rule" [ "notional" ] (rules p);
      Alcotest.(check (list string)) "the limits" [ "aapl-cap"; "tech-cap" ] (created p);
      Alcotest.(check int)
        "every reason, rules first" 3
        (List.length (D.Oms.Preview.reasons p)))
    ()

let test_a_preview's_json_has_exactly_these_keys () =
  with_oms
    ~f:(fun oms _ ->
      let json =
        D.Oms.Preview.to_json (D.Oms.preview oms (ticket xom D.Order.Side.Buy 10))
      in
      Alcotest.(check (list string))
        "ten, in order"
        [
          "passed";
          "symbol";
          "side";
          "qty";
          "type";
          "limit_price";
          "decision_price";
          "rules";
          "gate";
          "reasons";
        ]
        (Yojson.Safe.Util.keys json);
      (* XOM -200 -> -190: every limit further inside its line *)
      Alcotest.(check bool)
        "passed" true
        Yojson.Safe.Util.(to_bool (member "passed" json));
      Alcotest.(check (float 1e-9))
        "priced at the mark" 100.0
        Yojson.Safe.Util.(to_number (member "decision_price" json)))
    ()

(* The rules refuse an order on a book that is not the account's: before the
   first read is applied, or once the last is older than two sync intervals,
   the graph may hold the book file's quantities and cash, and the gate would
   judge a book nobody holds. XOM 10 x 100 = 1,000 passes every other rule and
   every limit (XOM -200 -> -190), so the trading rule speaks alone. *)
let test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule () =
  with_oms ~book_is_current:false
    ~f:(fun oms _ ->
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by the trading rule alone" [ "trading" ] (rules p);
      Alcotest.(check bool)
        "saying why" true
        (List.exists (D.Oms.Preview.reasons p)
           ~f:(String.is_substring ~substring:"recent read of the account"));
      (* The frame's [trading] is [can_trade], so it reads off while the rule
         would refuse. *)
      Alcotest.(check bool)
        "and the frame's trading reads off" false (D.Oms.can_trade oms))
    ()

(* The manager keeps the venue's clock, and the session rule reads it when an
   order is previewed, not when the clock was read. The clock as Alpaca
   answers it at 15:59:30 EDT on Monday 2026-09-14: open, the close at 16:00
   (20:00Z), the next open Tuesday at 09:30 (13:30Z). XOM 10 x 100 = 1,000
   passes every other rule and every limit. At 15:59:59 it passes; at
   16:00:30, on the same clock with no read between -- inside the minute a
   flag read once a minute still said open -- the session rule refuses it,
   alone. *)
let test_an_order_after_the_close_is_refused_on_the_clock_last_read () =
  let at = Time_ns.of_string_with_utc_offset in
  let now = ref (at "2026-09-14T19:59:30Z") in
  with_oms
    ~now:(fun () -> !now)
    ~f:(fun oms _ ->
      D.Oms.set_clock oms
        (Some
           {
             D.Venue.Session_clock.now = !now;
             is_open = true;
             next_open = at "2026-09-15T13:30:00Z";
             next_close = at "2026-09-14T20:00:00Z";
             next_close_date = Date.of_string "2026-09-14";
           });
      let preview_at s =
        now := at s;
        D.Oms.preview oms (ticket xom D.Order.Side.Buy 10)
      in
      let before = preview_at "2026-09-14T19:59:59Z" in
      Alcotest.(check (pair bool (list string)))
        "15:59:59: passed, no rule" (true, [])
        (D.Oms.Preview.passed before, rules before);
      let after = preview_at "2026-09-14T20:00:30Z" in
      Alcotest.(check (pair bool (list string)))
        "16:00:30: refused by the session alone" (false, [ "session" ])
        (D.Oms.Preview.passed after, rules after))
    ()

(* The gate now counts a resting order as if it fills (Task 3): several
   resting orders can each pass alone and only breach a limit once they are
   all counted together, which nothing would see until they actually filled.

   TECH's weight is AAPL 400 x 150 = 60,000 plus MSFT 100 x 300 = 30,000 =
   90,000; tech-cap is 100,000, so 10,000 of room is left. Order 1, MSFT buy
   20 @ $300 = $6,000, rests: alone it would leave TECH at 96,000, under the
   cap. Order 2, MSFT buy 15 @ $300 = $4,500 -- a different quantity, so the
   duplicate rule does not speak -- would by itself also leave TECH at
   94,500, under the cap: that was the bug, each one judged alone. Counting
   order 1 as resting, the gate now sees 90,000 + 6,000 + 4,500 = 100,500,
   over the 100,000 cap, and refuses order 2, naming tech-cap. *)
let test_a_resting_order_the_gate_now_counts_pushes_a_second_over_tech_cap () =
  with_oms
    ~f:(fun oms journal ->
      let resting =
        rest oms journal ~seed:101 msft D.Order.Side.Buy 20
          (D.Order.Kind.Limit (price 300.0))
      in
      Alcotest.(check string)
        "the first rests" "submitted"
        (D.Order.State.to_string resting.D.Order.state);
      let p =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 300.0)) msft D.Order.Side.Buy 15)
      in
      Alcotest.(check bool) "the second is refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by no rule of its own" [] (rules p);
      Alcotest.(check (list string))
        "by tech-cap, counting the resting order" [ "tech-cap" ] (created p);
      Alcotest.(check bool)
        "and the reasons name it" true
        (List.exists (D.Oms.Preview.reasons p)
           ~f:(String.is_substring ~substring:"tech-cap")))
    ()

(* Cancelling the resting order gives its room back. Same book and orders as
   above: order 1 rests, order 2 is refused by tech-cap; then order 1 is
   cancelled (pending_cancel, then the venue's confirmation -- the two steps
   [Oms.cancel] and its answer leave), which takes it out of [oms.open_], and
   order 2's own 4,500 alone -- 90,000 + 4,500 = 94,500 -- is under the
   100,000 cap again. *)
let test_cancelling_the_resting_order_frees_the_room_again () =
  with_oms
    ~f:(fun oms journal ->
      let resting =
        rest oms journal ~seed:102 msft D.Order.Side.Buy 20
          (D.Order.Kind.Limit (price 300.0))
      in
      let order2 () =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 300.0)) msft D.Order.Side.Buy 15)
      in
      Alcotest.(check bool)
        "refused while the first rests" false
        (D.Oms.Preview.passed (order2 ()));
      let cancelled = cancel_resting oms resting in
      Alcotest.(check string)
        "cancelled" "cancelled"
        (D.Order.State.to_string cancelled.D.Order.state);
      Alcotest.(check int) "and out of the open set" 0 (D.Oms.open_count oms);
      Alcotest.(check bool) "the room is back" true (D.Oms.Preview.passed (order2 ())))
    ()

(* A resting order the gate cannot price is not dropped from the count --
   doing so would let it pass for zero. AAPL is repriced to 0, "a cell nobody
   has written, not a price" in [Oms.live_mark]'s own words, so the AAPL
   market order resting below has neither a limit (it is a market order) nor
   a mark. [Oms.preview] then runs no gate at all for an unrelated ticket,
   XOM sell 10 -- which passes every rule of its own and, judged alone, every
   limit too (XOM -200 -> -190, as in the book-is-not-current case above) --
   failing closed exactly as a proposal with no decision price of its own
   already does: no verdict is a proposal that is not passed. *)
let test_a_resting_market_order_with_no_mark_fails_the_gate_closed () =
  with_oms
    ~f:(fun oms journal ->
      let resting =
        rest oms journal ~seed:103 aapl D.Order.Side.Buy 50 D.Order.Kind.Market
      in
      Alcotest.(check string)
        "still resting" "submitted"
        (D.Order.State.to_string resting.D.Order.state);
      Graph.apply_tick oms.D.Oms.graph
        { Tick.symbol = aapl; price = price 0.0; time = Time.now () };
      Graph.stabilize oms.D.Oms.graph;
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check (list string)) "no rule of its own fails" [] (rules p);
      Alcotest.(check bool)
        "the gate could not be run, so it is not passed" false (D.Oms.Preview.passed p);
      Alcotest.(check bool)
        "no verdict at all -- fail closed" true
        (Option.is_none p.D.Oms.Preview.verdict))
    ()

let suite =
  ( "oms",
    [
      Alcotest.test_case
        "a preview that takes TECH over its cap names it and creates nothing" `Quick
        test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing;
      Alcotest.test_case "the rules and the limits both speak" `Quick
        test_the_rules_and_the_limits_both_speak;
      Alcotest.test_case "a preview's JSON has exactly these keys" `Quick
        test_a_preview's_json_has_exactly_these_keys;
      Alcotest.test_case "a book that is not the account's is refused by the trading rule"
        `Quick test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule;
      Alcotest.test_case "an order after the close is refused on the clock last read"
        `Quick test_an_order_after_the_close_is_refused_on_the_clock_last_read;
      Alcotest.test_case
        "a resting order the gate now counts pushes a second over tech-cap" `Quick
        test_a_resting_order_the_gate_now_counts_pushes_a_second_over_tech_cap;
      Alcotest.test_case "cancelling the resting order frees the room again" `Quick
        test_cancelling_the_resting_order_frees_the_room_again;
      Alcotest.test_case "a resting market order with no mark fails the gate closed"
        `Quick test_a_resting_market_order_with_no_mark_fails_the_gate_closed;
    ] )
