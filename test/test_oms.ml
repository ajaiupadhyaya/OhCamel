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
let with_oms ?(book_is_current = true) ?(now = Time_ns.now) ?(extra_limits = [])
    ?equity_history ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        ([
           limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
           limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
           limit "book-cap" Limit.Portfolio 200_000.0;
         ]
        @ extra_limits)
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
      Option.iter equity_history ~f:(Graph.set_equity_history graph);
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

let limits moves = List.map moves ~f:(fun m -> m.Ohcamel.Gate.Move.limit)

(* What the proposal does to the book as it stands: with nothing resting, the
   only scenario there is. *)
let created (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.gate with
  | Some (Ok s) -> limits (D.Oms.Scenarios.as_it_stands s).Ohcamel.Gate.Verdict.created
  | Some (Error _) | None -> []

(* The first scenario the proposal fails under, as its description and the
   limits it created and worsened there; None when it fails under none. *)
let failing (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.gate with
  | Some (Ok s) ->
      Option.map (D.Oms.Scenarios.first_failure s) ~f:(fun (scenario, v) ->
          ( D.Oms.Scenario.describe scenario,
            limits v.Ohcamel.Gate.Verdict.created,
            limits v.Ohcamel.Gate.Verdict.worsened ))
  | Some (Error _) | None -> None

(* The scenarios the proposal was judged under, deduplicated, in order. *)
let scenarios (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.gate with
  | Some (Ok s) -> List.map s ~f:(fun (scenario, _) -> D.Oms.Scenario.describe scenario)
  | Some (Error _) | None -> []

let says (p : D.Oms.Preview.t) substring =
  List.exists (D.Oms.Preview.reasons p) ~f:(String.is_substring ~substring)

let as_it_stands = D.Oms.Scenario.describe D.Oms.Scenario.As_it_stands
let if_buys = D.Oms.Scenario.describe D.Oms.Scenario.Resting_buys
let if_sells = D.Oms.Scenario.describe D.Oms.Scenario.Resting_sells
let if_growing = D.Oms.Scenario.describe D.Oms.Scenario.Growing_side
let if_every = D.Oms.Scenario.describe D.Oms.Scenario.Every_resting

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
  let request =
    { D.Order.Request.client_order_id; symbol; side; qty; kind; tif = D.Order.Tif.Day }
  in
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

(* The gate counts a resting order (Task 3): several resting orders can each
   pass alone and only breach a limit once they are counted together, which
   nothing would see until they actually filled.

   TECH's weight is AAPL 400 x 150 = 60,000 plus MSFT 100 x 300 = 30,000 =
   90,000; tech-cap is 100,000, so 10,000 of room is left. Order 1, MSFT buy
   20 @ $300 = $6,000, rests: alone it would leave TECH at 96,000, under the
   cap. Order 2, MSFT buy 15 @ $300 = $4,500 -- a different quantity, so the
   duplicate rule does not speak -- would by itself also leave TECH at
   94,500, under the cap: that was the bug, each one judged alone. If the
   resting buy fills, the gate sees 90,000 + 6,000 + 4,500 = 100,500, over the
   100,000 cap, and refuses order 2, naming tech-cap and that scenario.

   A buy is all that rests, so there are two distinct bases: nothing, and the
   buy. The resting sells are none (the book as it stands), and the side that
   grows MSFT -- 100 + 15 + 20 = 135 against 115 -- and every resting order
   are the buy again. *)
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
        "two distinct bases" [ as_it_stands; if_buys ] (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "by tech-cap, if the resting buy fills"
        (Some (if_buys, [ "tech-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "and the reasons name the limit and the scenario" true
        (says p "tech-cap would be breached" && says p (sprintf "(%s)" if_buys)))
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
   doing so would let it pass for zero -- and the refusal says which order it
   is. AAPL is repriced to 0, "a cell nobody has written, not a price" in
   [Oms.live_mark]'s own words, so the AAPL market order resting below has
   neither a limit (it is a market order) nor a mark. A preview of an
   unrelated ticket, XOM buy 10 -- which passes every rule of its own and,
   judged alone, every limit too (XOM -200 -> -190, as in the
   book-is-not-current case above) -- is refused, and the reason names the
   resting order by its client id, symbol, side, remaining quantity and kind.
   [Oms.refuse], which [propose] calls for every refusal, journals it with
   that reason. *)
let test_a_resting_order_with_no_price_refuses_naming_it_and_is_journaled () =
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
      let why =
        sprintf
          "the resting order %s (submitted: AAPL buy, 50 remaining, market) has no limit \
           price and no mark, so the gate cannot count it"
          (D.Oms.key resting)
      in
      Alcotest.(check bool) "the reason names the order" true (says p why);
      let refused = D.Oms.refuse oms p ~source:"test" in
      Alcotest.(check string)
        "refused before the venue" "rejected_pre_trade"
        (D.Order.State.to_string refused.D.Order.state);
      let row =
        Option.value_exn
          (D.Journal.load_order journal
             refused.D.Order.request.D.Order.Request.client_order_id)
      in
      Alcotest.(check bool)
        "and the journal holds the reason" true
        (String.is_substring
           (Option.value row.D.Journal.Order_row.order.D.Order.reason ~default:"")
           ~substring:why))
    ()

(* A resting order in a symbol the book does not hold -- a journal written
   against another book -- cannot be counted either: [Graph.apply_fill] would
   raise on it, on every preview. The symbol is checked first, and the
   proposal (XOM buy 10, which passes everything of its own) is refused with a
   reason naming the order, without raising. *)
let test_a_resting_order_outside_the_book_refuses_and_does_not_raise () =
  with_oms
    ~f:(fun oms journal ->
      let resting =
        rest oms journal ~seed:104 (Symbol.of_string "ZZZ") D.Order.Side.Buy 10
          (D.Order.Kind.Limit (price 50.0))
      in
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check (list string)) "no rule of its own fails" [] (rules p);
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check bool)
        "the reason names the order and why" true
        (says p
           (sprintf
              "the resting order %s (submitted: ZZZ buy, 10 remaining, limit 50.00) is \
               in a symbol this book does not hold"
              (D.Oms.key resting))))
    ()

(* Task 3's review, C1, its first probe, as a regression. TECH is 90,000 of
   100,000. AAPL buy 100 at the 150 mark is 15,000: alone, TECH would be
   105,000, and the gate refuses it. A resting MSFT sell of 20 at 300 (6,000)
   assumed filled took TECH to 99,000 and let the buy through. Now the book as
   it stands is one of the scenarios, and it refuses the buy, naming tech-cap.
   The bases: nothing; the sell (the resting sells, and every resting order);
   the resting buys are none, and MSFT's growing side is its buys -- 100
   against 80 -- which are none too. *)
let test_a_resting_sell_makes_no_room_for_a_buy_the_book_cannot_take () =
  with_oms
    ~f:(fun oms journal ->
      ignore
        (rest oms journal ~seed:105 msft D.Order.Side.Sell 20
           (D.Order.Kind.Limit (price 300.0))
          : D.Order.t);
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 100) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string))
        "two distinct bases" [ as_it_stands; if_sells ] (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "by tech-cap, on the book as it stands"
        (Some (as_it_stands, [ "tech-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "the reason names it" true
        (says p "tech-cap would be breached" && says p (sprintf "(%s)" as_it_stands)))
    ()

(* The review's second probe. AAPL buy 160 limit 150 is 24,000 (under the
   25,000 order cap): alone, AAPL 560 x 150 = 84,000 > 80,000 and TECH 84,000
   + 30,000 = 114,000 > 100,000. A resting AAPL sell of 100 assumed filled
   took AAPL to 460 x 150 = 69,000 and TECH to 99,000, and let it through.
   Now it is refused on the book as it stands, naming both. AAPL's growing
   side is its buys (560 against 460), which are none. *)
let test_a_resting_sell_of_the_same_name_makes_no_room_either () =
  with_oms
    ~f:(fun oms journal ->
      ignore
        (rest oms journal ~seed:106 aapl D.Order.Side.Sell 100
           (D.Order.Kind.Limit (price 150.0))
          : D.Order.t);
      let p =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 150.0)) aapl D.Order.Side.Buy 160)
      in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "by aapl-cap and tech-cap, on the book as it stands"
        (Some (as_it_stands, [ "aapl-cap"; "tech-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "the reasons name both" true
        (says p "aapl-cap would be breached" && says p "tech-cap would be breached"))
    ()

(* Only the resting buys catch it. A sector's notional is the absolute value
   of a sum, so it is largest when every buy in it fills or when every sell
   does -- not necessarily when all fill, nor on each name's growing side.
   Resting: AAPL buy 20 @ 150, MSFT buy 20 @ 300, MSFT sell 300 @ 300 (each at
   the mark, so priced there). Proposed: AAPL buy 10 at the mark (1,500).
     as it stands:  AAPL 410 (61,500) + MSFT 30,000 = TECH 91,500
     resting buys:  base AAPL 420 (63,000) + MSFT 120 (36,000) = 99,000, clear;
                    with the proposal AAPL 430 (64,500): 100,500 -- created
     resting sells: AAPL 410 (61,500) + MSFT -200 (-60,000) = 1,500
     growing side:  AAPL's buys (430 against 410), MSFT's sells (|-200| against
                    120): 64,500 - 60,000 = 4,500; gross 64,500 + 60,000 +
                    20,000 = 144,500
     every order:   AAPL 430 (64,500) + MSFT -180 (-54,000) = 10,500; gross
                    138,500
   aapl-cap is at most 64,500 and book-cap at most 144,500 anywhere, so the
   one refusal is tech-cap, if the resting buys fill. *)
let test_only_the_resting_buys_catch_a_sector_breach () =
  with_oms
    ~f:(fun oms journal ->
      let at p = D.Order.Kind.Limit (price p) in
      ignore (rest oms journal ~seed:107 aapl D.Order.Side.Buy 20 (at 150.0) : D.Order.t);
      ignore (rest oms journal ~seed:108 msft D.Order.Side.Buy 20 (at 300.0) : D.Order.t);
      ignore
        (rest oms journal ~seed:109 msft D.Order.Side.Sell 300 (at 300.0) : D.Order.t);
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 10) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string))
        "five distinct bases"
        [ as_it_stands; if_buys; if_sells; if_growing; if_every ]
        (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "tech-cap, if the resting buys fill"
        (Some (if_buys, [ "tech-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "refused, the reason naming the scenario" true
        ((not (D.Oms.Preview.passed p)) && says p (sprintf "(%s)" if_buys)))
    ()

(* Only the resting sells catch it: the mirror, a sector driven through its
   line on the short side. Resting: AAPL sell 900 @ 150, AAPL buy 110 @ 150,
   MSFT sell 170 @ 300. Proposed: MSFT sell 20 at the mark (6,000).
     as it stands:  AAPL 60,000 + MSFT 80 (24,000) = TECH 84,000
     resting buys:  base AAPL 510 (76,500) + MSFT 30,000 = 106,500, over by
                    6,500; with the proposal 100,500, over by 500 -- reduced,
                    which passes
     resting sells: base AAPL -500 (-75,000) + MSFT -70 (-21,000) = -96,000,
                    clear; with the proposal MSFT -90 (-27,000): -102,000 --
                    created
     growing side:  AAPL's buys (510 against |-500|), MSFT's sells (|80 - 170|
                    = 90 against 80): 76,500 - 27,000 = 49,500
     every order:   AAPL -390 (-58,500) + MSFT -90 (-27,000) = -85,500
   aapl-cap is at most 76,500 (under 80,000) and book-cap at most 123,500. *)
let test_only_the_resting_sells_catch_a_sector_breach () =
  with_oms
    ~f:(fun oms journal ->
      let at p = D.Order.Kind.Limit (price p) in
      ignore
        (rest oms journal ~seed:110 aapl D.Order.Side.Sell 900 (at 150.0) : D.Order.t);
      ignore (rest oms journal ~seed:111 aapl D.Order.Side.Buy 110 (at 150.0) : D.Order.t);
      ignore
        (rest oms journal ~seed:112 msft D.Order.Side.Sell 170 (at 300.0) : D.Order.t);
      let p = D.Oms.preview oms (ticket msft D.Order.Side.Sell 20) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string))
        "five distinct bases"
        [ as_it_stands; if_buys; if_sells; if_growing; if_every ]
        (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "tech-cap, if the resting sells fill"
        (Some (if_sells, [ "tech-cap" ], []))
        (failing p);
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p))
    ()

(* Only each name's growing side catches it: portfolio gross, a sum of
   per-name absolute values, is largest when every name moves away from zero.
   Long AAPL with a resting buy of AAPL, short XOM with a resting sell of XOM
   -- and, beside each, a resting order the other way, so that every resting
   order filling nets to nothing. Resting: AAPL buy 60 and sell 60 @ 150, XOM
   sell 800 and buy 800 @ 100. Proposed: XOM sell 50 at the mark (5,000).
   book-cap is 200,000 of gross.
     as it stands:  60,000 + 30,000 + XOM -250 (25,000) = 115,000
     resting buys:  AAPL 460 (69,000) + 30,000 + XOM 550 (55,000) = 154,000
     resting sells: AAPL 340 (51,000) + 30,000 + XOM -1,050 (105,000) = 186,000
     growing side:  AAPL's buys (460 against 340), XOM's sells (|-250 - 800| =
                    1,050 against |-250 + 800| = 550): base 69,000 + 30,000 +
                    100,000 = 199,000, clear; with the proposal 204,000 --
                    created
     every order:   115,000, as it stands
   TECH is at most 99,000 (resting buys and growing side) and aapl-cap at
   most 69,000. *)
let test_only_the_growing_side_catches_a_gross_breach () =
  with_oms
    ~f:(fun oms journal ->
      let at p = D.Order.Kind.Limit (price p) in
      ignore (rest oms journal ~seed:113 aapl D.Order.Side.Buy 60 (at 150.0) : D.Order.t);
      ignore (rest oms journal ~seed:114 aapl D.Order.Side.Sell 60 (at 150.0) : D.Order.t);
      ignore (rest oms journal ~seed:115 xom D.Order.Side.Sell 800 (at 100.0) : D.Order.t);
      ignore (rest oms journal ~seed:116 xom D.Order.Side.Buy 800 (at 100.0) : D.Order.t);
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Sell 50) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string))
        "five distinct bases"
        [ as_it_stands; if_buys; if_sells; if_growing; if_every ]
        (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "book-cap, if each name grows"
        (Some (if_growing, [ "book-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "refused, naming book-cap" true
        ((not (D.Oms.Preview.passed p)) && says p "book-cap would be breached"))
    ()

(* The re-review's probe (Task 3's fix round, the Important): [Growing_side]
   must pick, for the name the PROPOSAL ITSELF trades, the resting side that
   moves the same way the proposal does -- not simply the larger-magnitude
   side, which can be the side the proposal is shrinking.

   Resting: XOM buy 250 @ 100 (at the mark) and XOM sell 60 @ 100; MSFT sell
   548 @ 300 (at the mark). Proposed: XOM buy 10, at the mark (1,000). The
   proposal's own name is XOM, and its net there is +10, positive, so
   [Growing_side] must take XOM's resting BUY (250) -- even though, alone,
   XOM's resting SELL is the larger-magnitude side: base position -200, so
   |-200 + 250| = 50 against |-200 - 60| = 260. MSFT is untouched by the
   proposal (net 0), so its side is unchanged by this fix -- only a resting
   sell rests there, and it is still taken.
     growing side base:  AAPL 60,000 + MSFT |100 - 548| x 300 (134,400) + XOM
                          |-200 + 250| x 100 (5,000) = 199,400, clear
     with the proposal:  XOM |-200 + 250 + 10| x 100 (6,000) -> 200,400 --
                          created, over book-cap's 200,000
   Picking XOM's sell instead (the bug the re-review found) puts the base
   already over book-cap from the resting orders' own sell (AAPL 60,000 +
   MSFT 134,400 + XOM |-200 - 60| x 100 (26,000) = 220,400), a breach the
   proposal only reduces (219,400) -- which the gate's own rule (reduced
   passes) lets through. Every scenario passed under the bug, though a real
   fill of the resting XOM buy and the MSFT sell, beside this order, breaches
   book-cap. *)
let test_growing_side_takes_the_proposals_own_direction_not_the_larger_side () =
  with_oms
    ~f:(fun oms journal ->
      let at p = D.Order.Kind.Limit (price p) in
      ignore (rest oms journal ~seed:130 xom D.Order.Side.Buy 250 (at 100.0) : D.Order.t);
      ignore (rest oms journal ~seed:131 xom D.Order.Side.Sell 60 (at 100.0) : D.Order.t);
      ignore
        (rest oms journal ~seed:132 msft D.Order.Side.Sell 548 (at 300.0) : D.Order.t);
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string))
        "five distinct bases"
        [ as_it_stands; if_buys; if_sells; if_growing; if_every ]
        (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "book-cap, if each name grows"
        (Some (if_growing, [ "book-cap" ], []))
        (failing p);
      Alcotest.(check bool)
        "refused, naming book-cap" true
        ((not (D.Oms.Preview.passed p)) && says p "book-cap would be breached"))
    ()

(* Resting orders alone can take a limit over its line (the review's minor
   a). They are a base, not part of the proposal, so a proposal is charged
   only with what it does to them. Resting: AAPL buy 100 @ 150. If it fills,
   AAPL is 500 x 150 = 75,000 and TECH 105,000, 5,000 over.
   - AAPL sell 20 at the mark takes TECH to 102,000 there, 2,000 over:
     reduced, which passes (and 87,000 as it stands). Judged the first way --
     every fill from the live 90,000 -- it read as a breach created.
   - MSFT buy 10 at the mark, an add the breached limit covers, takes it to
     108,000, 8,000 over: worsened, refused, if the resting buy fills.
   - XOM sell 10, an add the breached limit does not cover, leaves TECH where
     the resting buy puts it and gross at 126,000: neither created nor
     worsened, which passes -- where it too read as a breach created. *)
let test_a_breach_the_resting_orders_make_is_theirs_not_the_proposal's () =
  with_oms
    ~f:(fun oms journal ->
      ignore
        (rest oms journal ~seed:117 aapl D.Order.Side.Buy 100
           (D.Order.Kind.Limit (price 150.0))
          : D.Order.t);
      let sell = D.Oms.preview oms (ticket aapl D.Order.Side.Sell 20) in
      Alcotest.(check (list string))
        "the sell: two distinct bases" [ as_it_stands; if_buys ] (scenarios sell);
      Alcotest.(check bool) "the sell passes" true (D.Oms.Preview.passed sell);
      let add = D.Oms.preview oms (ticket msft D.Order.Side.Buy 10) in
      Alcotest.(check bool) "the MSFT add is refused" false (D.Oms.Preview.passed add);
      Alcotest.(check (option (triple string (list string) (list string))))
        "as worsening tech-cap, if the resting buy fills"
        (Some (if_buys, [], [ "tech-cap" ]))
        (failing add);
      Alcotest.(check bool)
        "the reason says further over" true
        (says add "tech-cap would be further over its line");
      Alcotest.(check bool)
        "the XOM add passes" true
        (D.Oms.Preview.passed (D.Oms.preview oms (ticket xom D.Order.Side.Sell 10))))
    ()

(* A partly filled resting order counts at what remains. MSFT buy 30 @ 300
   rests and 20 fill: the fill is recorded and applied to the book as
   [on_update] does it, so MSFT is 120 (36,000) and TECH 96,000, and 10
   remain (3,000).
   - AAPL buy 5 at the mark (750): 96,000 + 3,000 + 750 = 99,750, which
     passes. Counted at the whole 30 it would be 105,750, and refused.
   - AAPL buy 10 (1,500): 100,500 -- refused, if the resting buy fills. *)
let test_a_partly_filled_resting_order_counts_at_what_remains () =
  with_oms
    ~f:(fun oms journal ->
      let o =
        rest oms journal ~seed:118 msft D.Order.Side.Buy 30
          (D.Order.Kind.Limit (price 300.0))
      in
      let f =
        {
          D.Order.Fill.execution_id = "exec-118";
          qty = 20.0;
          price = price 300.0;
          at = Time_ns.now ();
          position_qty = None;
        }
      in
      let o = D.Oms.record oms o (D.Order.Event.Venue_fill f) in
      D.Oms.apply_to_graph oms o f;
      Alcotest.(check (pair string int))
        "partly filled, and still open" ("partially_filled", 1)
        (D.Order.State.to_string o.D.Order.state, D.Oms.open_count oms);
      Alcotest.(check (float 1e-9))
        "MSFT is 120" 120.0
        (Qty.to_float (Graph.qty oms.D.Oms.graph msft));
      Alcotest.(check bool)
        "AAPL buy 5 passes against the 10 that remain" true
        (D.Oms.Preview.passed (D.Oms.preview oms (ticket aapl D.Order.Side.Buy 5)));
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 10) in
      Alcotest.(check (option (triple string (list string) (list string))))
        "AAPL buy 10 does not"
        (Some (if_buys, [ "tech-cap" ], []))
        (failing p))
    ()

(* A resting sell is a sell. MSFT sell 40 @ 300 rests; AAPL buy 20 at the mark
   (3,000) is proposed: 93,000 as it stands, 90,000 - 12,000 + 3,000 = 81,000
   if the sell fills, and MSFT's growing side is its buys (100 against 60),
   which are none. It passes. Were the side ignored, the sell would count as
   a buy of 40: 90,000 + 12,000 + 3,000 = 105,000, refused. *)
let test_a_resting_sell_counts_as_a_sell () =
  with_oms
    ~f:(fun oms journal ->
      ignore
        (rest oms journal ~seed:119 msft D.Order.Side.Sell 40
           (D.Order.Kind.Limit (price 300.0))
          : D.Order.t);
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 20) in
      Alcotest.(check (list string))
        "two distinct bases" [ as_it_stands; if_sells ] (scenarios p);
      Alcotest.(check bool) "passes" true (D.Oms.Preview.passed p))
    ()

(* A resting fill is priced no better than the mark, so none books equity it
   has not earned. The book's peak equity is 1,100,000 and it stands at
   1,070,000, a drawdown of 30,000 / 1,100,000 = 2.7273%; the limit is 2.75%,
   so equity may fall to 1,100,000 x 0.9725 = 1,069,750. Resting: XOM buy 40
   limit 105, above the 100 mark, priced at its limit (it pays 200 more than
   the shares are worth), and XOM buy 20 limit 95, below the mark, priced AT
   the mark (0 -- at its own limit it would book 100 nobody has earned).
   Proposed: XOM buy 25 limit 104 (inside the 5% collar), which pays 100 over
   the mark.
     as it stands:  1,070,000 - 100 = 1,069,900, 2.7364%, clear
     resting buys:  base 1,070,000 - 200 = 1,069,800 (2.7455%, clear); with
                    the proposal 1,069,700, 2.7545% -- created
   XOM's growing side is its sells (|-175| against |-175 + 60|), which are
   none. Priced at its own limit, the passive buy would lift the base to
   1,069,900, the proposal would leave 1,069,800 (2.7455%), and it would
   pass. *)
let test_a_resting_fill_is_priced_no_better_than_the_mark () =
  with_oms
    ~extra_limits:
      [
        {
          Limit.name = "drawdown";
          scope = Limit.Portfolio;
          kind = Limit.Max_drawdown 0.0275;
        };
      ]
    ~equity_history:[| 1_100_000.0 |]
    ~f:(fun oms journal ->
      let at p = D.Order.Kind.Limit (price p) in
      ignore (rest oms journal ~seed:120 xom D.Order.Side.Buy 40 (at 105.0) : D.Order.t);
      ignore (rest oms journal ~seed:121 xom D.Order.Side.Buy 20 (at 95.0) : D.Order.t);
      let p = D.Oms.preview oms (ticket ~kind:(at 104.0) xom D.Order.Side.Buy 25) in
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string))
        "two distinct bases" [ as_it_stands; if_buys ] (scenarios p);
      Alcotest.(check (option (triple string (list string) (list string))))
        "the drawdown, if the resting buys fill"
        (Some (if_buys, [ "drawdown" ], []))
        (failing p))
    ()

(* An order this desk declared failed, as [resolve] leaves one: journaled at
   [at], its submission's answer unknown, and every lookup missing. It is out
   of the open set, as every terminal order is. *)
let fail oms journal ~seed ~at symbol side qty kind =
  let client_order_id =
    D.Ids.Client_order_id.generate ~now:at ~rng:(Random.State.make [| seed |])
  in
  let request =
    { D.Order.Request.client_order_id; symbol; side; qty; kind; tif = D.Order.Tif.Day }
  in
  let o = D.Order.create request in
  D.Journal.insert_order journal o ~source:"test" ~decision_price:(price 0.0)
    ~arrival:None ~verdict:`Null ~at;
  let o = D.Oms.record oms o (D.Order.Event.Outcome_unknown "the venue did not answer") in
  D.Oms.record oms o D.Order.Event.Not_found

(* A failed order the venue may still work counts until the venue finishes
   it. MSFT buy 20 @ 300 fails: every lookup missed, and it is out of the
   open set, but nothing says the venue never took it. MSFT buy 15 @ 300 is
   then refused exactly as beside a resting order (90,000 + 6,000 + 4,500 =
   100,500). The venue then reports the failed order cancelled: the machine
   keeps it failed and journals the report beside an anomaly, and that report
   ends its count -- 94,500, passed. *)
let test_a_failed_order_counts_until_the_venue_finishes_it () =
  with_oms
    ~f:(fun oms journal ->
      let o =
        fail oms journal ~seed:122 ~at:(Time_ns.now ()) msft D.Order.Side.Buy 20
          (D.Order.Kind.Limit (price 300.0))
      in
      Alcotest.(check (pair string int))
        "failed, and not open" ("failed", 0)
        (D.Order.State.to_string o.D.Order.state, D.Oms.open_count oms);
      let order2 () =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 300.0)) msft D.Order.Side.Buy 15)
      in
      Alcotest.(check (option (triple string (list string) (list string))))
        "counted: tech-cap, if the resting buy fills"
        (Some (if_buys, [ "tech-cap" ], []))
        (failing (order2 ()));
      let o = D.Oms.record oms o D.Order.Event.Venue_cancelled in
      Alcotest.(check string)
        "the machine keeps it failed" "failed"
        (D.Order.State.to_string o.D.Order.state);
      Alcotest.(check bool)
        "the venue's report ends its count" true
        (D.Oms.Preview.passed (order2 ())))
    ()

(* A failed order is a day order, and a day order does not outlive its
   session. The bound is the latest recorded session's DATE, not when
   Session_close actually wrote that row (the re-review's minor: bounding by
   [recorded_at] can drop a still-live order -- the next test). MSFT buy 20 @
   300 fails on 2026-09-14 and is refused against, as above; a session dated
   2026-09-15 -- the day after -- is then recorded (whenever that write
   actually lands), and the order, from before that date, no longer
   counts. *)
let test_a_failed_order_from_an_earlier_session_does_not_count () =
  let at = Time_ns.of_string_with_utc_offset in
  with_oms
    ~f:(fun oms journal ->
      ignore
        (fail oms journal ~seed:123 ~at:(at "2026-09-14T19:00:00Z") msft D.Order.Side.Buy
           20
           (D.Order.Kind.Limit (price 300.0))
          : D.Order.t);
      let order2 () =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 300.0)) msft D.Order.Side.Buy 15)
      in
      Alcotest.(check bool)
        "counted while its session lasts" false
        (D.Oms.Preview.passed (order2 ()));
      D.Journal.record_session journal
        {
          D.Journal.Session.date = Date.of_string "2026-09-15";
          equity_close = 1_070_000.0;
          cash_close = 1_000_000.0;
          gross_close = 110_000.0;
          net_close = 70_000.0;
          recorded_at = at "2026-09-15T20:05:00Z";
        };
      Alcotest.(check bool)
        "not once a session dated after it is recorded" true
        (D.Oms.Preview.passed (order2 ())))
    ()

(* The re-review's minor: the bound must be the session's DATE, not
   [recorded_at] -- when Session_close writes a close late, during the NEXT
   session (it missed one and only caught up afterwards), [recorded_at] is
   later than orders that are still live and must not be dropped.

   Monday 2026-09-14's close is recorded on Tuesday afternoon, a day late,
   but dated 2026-09-14 -- the session it closes, not when the row was
   written. MSFT buy 20 @ 300 fails Tuesday morning, in the session that has
   not closed yet, before that late write. Bounding by the session's date
   (2026-09-14) still counts it, since it was created on or after that date;
   bounding by [recorded_at] (Tuesday afternoon) would read this Tuesday
   morning order as older than the write and drop it, though the venue has
   said nothing about it. *)
let test_a_close_recorded_late_still_counts_the_next_sessions_failed_order () =
  let at = Time_ns.of_string_with_utc_offset in
  with_oms
    ~f:(fun oms journal ->
      D.Journal.record_session journal
        {
          D.Journal.Session.date = Date.of_string "2026-09-14";
          equity_close = 1_070_000.0;
          cash_close = 1_000_000.0;
          gross_close = 110_000.0;
          net_close = 70_000.0;
          recorded_at = at "2026-09-15T18:00:00Z";
        };
      ignore
        (fail oms journal ~seed:124 ~at:(at "2026-09-15T13:35:00Z") msft D.Order.Side.Buy
           20
           (D.Order.Kind.Limit (price 300.0))
          : D.Order.t);
      let order2 () =
        D.Oms.preview oms
          (ticket ~kind:(D.Order.Kind.Limit (price 300.0)) msft D.Order.Side.Buy 15)
      in
      Alcotest.(check bool)
        "still counted, though created before the late write" false
        (D.Oms.Preview.passed (order2 ())))
    ()

(* The failed orders are read from the journal on every preview, so a journal
   that cannot be read cannot count them -- and a preview must still answer.
   With the journal's handle closed, XOM buy 10 (which passes everything of
   its own) is refused, saying why, and nothing raises. *)
let test_a_journal_that_cannot_be_read_refuses_and_does_not_raise () =
  with_oms
    ~f:(fun oms journal ->
      D.Journal.close journal;
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check (list string)) "no rule of its own fails" [] (rules p);
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check bool)
        "saying why" true
        (says p "the journal's failed orders could not be read"))
    ()

(* A failed order's DELETE is journaled before it is sent, as an open
   order's cancel is ([Oms.cancel_failed]). MSFT buy 20 @ 300 fails; the
   venue then reports it resting under venue-9, and the desk keeps that id
   (an anomaly, as Order.keep_venue_id says). At the instant the venue's
   cancel is called, the journal's last event for the order is already
   cancel_requested, the state still failed and no anomaly beside it. A
   second report of the same order sends no second DELETE and journals
   nothing more. *)
let test_a_failed_order's_delete_is_journaled_before_it_is_sent () =
  with_oms
    ~f:(fun oms journal ->
      let o =
        fail oms journal ~seed:125 ~at:(Time_ns.now ()) msft D.Order.Side.Buy 20
          (D.Order.Kind.Limit (price 300.0))
      in
      let client =
        D.Ids.Client_order_id.to_string o.D.Order.request.D.Order.Request.client_order_id
      in
      let events () =
        let rows = ref [] in
        ignore
          (Sqlite3.exec_not_null_no_headers
             (D.Journal.For_testing.db journal)
             ~cb:(fun row -> rows := String.concat_array ~sep:" / " row :: !rows)
             (sprintf
                "SELECT event, state_after, COALESCE(anomaly, '-') FROM order_events \
                 WHERE client_order_id = '%s' ORDER BY seq"
                client)
            : Sqlite3.Rc.t);
        List.rev !rows
      in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun _ -> None)
          ~now:Time_ns.now
          ~half_spread_bps:(fun _ -> 5.0)
          ~cash:(money 1_000_000.0) ~positions:[] ()
      in
      let deletes = ref [] in
      let trade =
        {
          (D.Sim_venue.trade ~auto:false venue) with
          D.Venue.Trade.cancel =
            (fun id ->
              deletes := (id, List.last (events ())) :: !deletes;
              Async.return (Ok ()));
        }
      in
      let resting =
        {
          D.Venue.Venue_order.id = "venue-9";
          client_order_id = client;
          symbol = msft;
          side = D.Order.Side.Buy;
          qty = 20.0;
          filled_qty = 0.0;
          filled_avg_price = None;
          status = "new";
          limit_price = Some 300.0;
        }
      in
      let o = D.Oms.record oms o (D.Order.Event.Found "venue-9") in
      ignore (D.Oms.cancel_failed oms trade o resting : unit Async.Deferred.t);
      Alcotest.(check (list (pair string (option string))))
        "at the DELETE, cancel_requested is journaled: the order failed, no anomaly"
        [ ("venue-9", Some "cancel_requested / failed / -") ]
        !deletes;
      let journaled = List.length (events ()) in
      ignore (D.Oms.cancel_failed oms trade o resting : unit Async.Deferred.t);
      Alcotest.(check (pair int int))
        "a second report: no second DELETE, nothing more journaled" (1, journaled)
        (List.length !deletes, List.length (events ())))
    ()

(* Which of the venue's statuses mean it still works the order -- it can
   still fill, and a DELETE would stop it -- and which do not. Alpaca's
   resting statuses, of which the simulator uses new and partially_filled,
   and stopped: guaranteed a fill that has not happened yet; then
   pending_cancel, done_for_day, the terminal ones and the rest of Alpaca's
   list, none of which a DELETE stops, and two that are not statuses at
   all: the test is exact, not a prefix or a case-folding. *)
let test_which_venue_statuses_rest () =
  let rests status =
    D.Venue.Venue_order.rests
      {
        D.Venue.Venue_order.id = "venue-1";
        client_order_id = "client-1";
        symbol = aapl;
        side = D.Order.Side.Buy;
        qty = 1.0;
        filled_qty = 0.0;
        filled_avg_price = None;
        status;
        limit_price = None;
      }
  in
  let resting =
    [
      "new";
      "accepted";
      "pending_new";
      "accepted_for_bidding";
      "partially_filled";
      "held";
      "stopped";
    ]
  and not_resting =
    [
      "pending_cancel";
      "done_for_day";
      "filled";
      "canceled";
      "expired";
      "rejected";
      "replaced";
      "pending_replace";
      "suspended";
      "calculated";
      "NEW";
      "";
    ]
  in
  Alcotest.(check (pair (list string) (list string)))
    "resting, and not" (resting, not_resting)
    (List.partition_tf (resting @ not_resting) ~f:rests)

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
      Alcotest.test_case
        "a resting order with no price refuses, naming it, and is journaled" `Quick
        test_a_resting_order_with_no_price_refuses_naming_it_and_is_journaled;
      Alcotest.test_case "a resting order outside the book refuses and does not raise"
        `Quick test_a_resting_order_outside_the_book_refuses_and_does_not_raise;
      Alcotest.test_case "a resting sell makes no room for a buy the book cannot take"
        `Quick test_a_resting_sell_makes_no_room_for_a_buy_the_book_cannot_take;
      Alcotest.test_case "a resting sell of the same name makes no room either" `Quick
        test_a_resting_sell_of_the_same_name_makes_no_room_either;
      Alcotest.test_case "only the resting buys catch a sector breach" `Quick
        test_only_the_resting_buys_catch_a_sector_breach;
      Alcotest.test_case "only the resting sells catch a sector breach" `Quick
        test_only_the_resting_sells_catch_a_sector_breach;
      Alcotest.test_case "only the growing side catches a gross breach" `Quick
        test_only_the_growing_side_catches_a_gross_breach;
      Alcotest.test_case
        "growing side takes the proposal's own direction, not the larger side" `Quick
        test_growing_side_takes_the_proposals_own_direction_not_the_larger_side;
      Alcotest.test_case "a breach the resting orders make is theirs, not the proposal's"
        `Quick test_a_breach_the_resting_orders_make_is_theirs_not_the_proposal's;
      Alcotest.test_case "a partly filled resting order counts at what remains" `Quick
        test_a_partly_filled_resting_order_counts_at_what_remains;
      Alcotest.test_case "a resting sell counts as a sell" `Quick
        test_a_resting_sell_counts_as_a_sell;
      Alcotest.test_case "a resting fill is priced no better than the mark" `Quick
        test_a_resting_fill_is_priced_no_better_than_the_mark;
      Alcotest.test_case "a failed order counts until the venue finishes it" `Quick
        test_a_failed_order_counts_until_the_venue_finishes_it;
      Alcotest.test_case "a failed order from an earlier session does not count" `Quick
        test_a_failed_order_from_an_earlier_session_does_not_count;
      Alcotest.test_case
        "a close recorded late still counts the next session's failed order" `Quick
        test_a_close_recorded_late_still_counts_the_next_sessions_failed_order;
      Alcotest.test_case "a journal that cannot be read refuses and does not raise" `Quick
        test_a_journal_that_cannot_be_read_refuses_and_does_not_raise;
      Alcotest.test_case "a failed order's DELETE is journaled before it is sent" `Quick
        test_a_failed_order's_delete_is_journaled_before_it_is_sent;
      Alcotest.test_case "which venue statuses rest" `Quick test_which_venue_statuses_rest;
    ] )
