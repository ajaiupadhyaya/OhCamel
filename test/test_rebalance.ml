(* A live strategy's rebalance (Task 15), without the scheduler.

   The targets are pure (Rebalance.plan), so every number is written here by
   hand. The order manager's half is asked through its synchronous parts:
   [Oms.check_rebalance] -- the rules per order and the one gate call, which
   [propose_rebalance] runs first and which, when it refuses, returns before
   anything is written -- and [Oms.plan_rebalance], which reads the equity,
   the recorded closes and the strategy's own fills. The flows that wait on a
   venue are test/desk_async's.

   The book, for the order manager's cases: AAPL 400 at 150 (60,000) and
   MSFT 100 at 300 (30,000), both TECH, and XOM -200 at 100 in ENERGY;
   aapl-cap 80,000 and tech-cap 100,000. Monday 14 September 2026's close is
   recorded at those prices, and it is 19:15 EDT (23:15Z) that evening: the
   session is closed, the clock names Tuesday's open at 09:30 EDT, and the
   opening auction's window is open. Twenty-day volume is a fixed 1,000,000
   shares and the order cap is 25,000. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module Signals_spec = Ohcamel.Config.Book.Signals_spec
module D = Ohcamel_desk
module Leg = D.Rebalance.Leg

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let spy = Symbol.of_string "SPY"
let at = Time_ns.of_string_with_utc_offset
let evening = at "2026-09-14T23:15:00Z"

(* ------------------------------------------------------------------------ *)
(* The targets, by hand                                                      *)
(* ------------------------------------------------------------------------ *)

let plan ?(symbols = [ spy ]) ?(capital_fraction = 0.5) ?(equity = Ok 100_000.0)
    ?(current = Ok Symbol.Map.empty) ~price weights =
  D.Rebalance.plan ~symbols ~weights ~capital_fraction ~equity
    ~marks:(Symbol.Map.of_alist_exn (List.map symbols ~f:(fun s -> (s, Ok price))))
    ~current

let legs = function
  | Ok ls ->
      List.map ls ~f:(fun (l : Leg.t) ->
          (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target))
  | Error why -> Alcotest.failf "no targets: %s" why

let test_targets_on_both_sides_of_a_share_boundary () =
  (* w x f x E = 1.0 x 0.5 x 100,000 = 50,000 of SPY.
       at 500.00: 50,000 / 500.00 = 100 exactly            -> 100
       at 500.01: 50,000 / 500.01 = 99.998000...          -> 99, never 100
       at 499.99: 50,000 / 499.99 = 100.002000...         -> 100, never 101
     With 40 already the strategy's own, 100 - 40 = 60 is bought. *)
  let check what expected r =
    Alcotest.(check (list (triple string int int))) what expected (legs r)
  in
  check "at 500.00, exactly 100" [ ("buy", 100, 100) ] (plan ~price:500.0 [ (spy, 1.0) ]);
  check "at 500.01, 99.998 is 99" [ ("buy", 99, 99) ] (plan ~price:500.01 [ (spy, 1.0) ]);
  check "at 499.99, 100.002 is 100"
    [ ("buy", 100, 100) ]
    (plan ~price:499.99 [ (spy, 1.0) ]);
  check "holding 40, 60 more"
    [ ("buy", 60, 100) ]
    (plan ~price:500.0 ~current:(Ok (Symbol.Map.singleton spy 40.0)) [ (spy, 1.0) ]);
  check "holding exactly the target, no order" []
    (plan ~price:500.0 ~current:(Ok (Symbol.Map.singleton spy 100.0)) [ (spy, 1.0) ])

let test_a_negative_weight_rounds_toward_zero () =
  (* -1.0 x 0.5 x 100,000 / 500.01 = -99.998...: toward zero is -99, where a
     floor would sell 100 -- one share more short than the weight asks. At
     500.00 it is -100 exactly. *)
  let check what expected r =
    Alcotest.(check (list (triple string int int))) what expected (legs r)
  in
  check "at 500.01, -99.998 is -99"
    [ ("sell", 99, -99) ]
    (plan ~price:500.01 [ (spy, -1.0) ]);
  check "at 500.00, -100" [ ("sell", 100, -100) ] (plan ~price:500.0 [ (spy, -1.0) ])

(* No targets and a reason, never a guess: each unknown in turn, and the
   names a strategy does not own. *)
let test_every_unknown_makes_no_targets_and_says_why () =
  let refused what ~substring r =
    match r with
    | Ok _ -> Alcotest.failf "%s: targets were made" what
    | Error why ->
        Alcotest.(check bool)
          (sprintf "%s: %S says %S" what why substring)
          true
          (String.is_substring why ~substring)
  in
  refused "unknown equity" ~substring:"the book's equity is unknown: not current"
    (plan ~equity:(Error "not current") ~price:500.0 [ (spy, 1.0) ]);
  refused "equity of zero" ~substring:"not a positive number"
    (plan ~equity:(Ok 0.0) ~price:500.0 [ (spy, 1.0) ]);
  refused "unknown fill history" ~substring:"fill history is unknown: an order may fill"
    (plan ~current:(Error "an order may fill") ~price:500.0 [ (spy, 1.0) ]);
  refused "no price" ~substring:"SPY has no recorded session close"
    (D.Rebalance.plan ~symbols:[ spy ]
       ~weights:[ (spy, 1.0) ]
       ~capital_fraction:0.5 ~equity:(Ok 100_000.0)
       ~marks:(Symbol.Map.singleton spy (Error "SPY has no recorded session close"))
       ~current:(Ok Symbol.Map.empty));
  refused "a price of zero" ~substring:"not a positive number"
    (plan ~price:0.0 [ (spy, 1.0) ]);
  refused "a weight for a name not its own" ~substring:"AAPL, which is not the strategy's"
    (plan ~price:500.0 [ (aapl, 1.0) ]);
  refused "a position in a name no longer its own"
    ~substring:"no longer one of its symbols"
    (plan ~price:500.0 ~current:(Ok (Symbol.Map.singleton aapl 5.0)) [ (spy, 1.0) ]);
  refused "a position that is not whole" ~substring:"not a whole number"
    (plan ~price:500.0 ~current:(Ok (Symbol.Map.singleton spy 10.5)) [ (spy, 1.0) ])

(* ------------------------------------------------------------------------ *)
(* The order manager's half                                                  *)
(* ------------------------------------------------------------------------ *)

let limit name scope n =
  { Limit.name; scope; kind = Limit.Gross_notional (Notional.of_float n) }

let with_oms ?(now = evening) ~f () =
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
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
      D.Journal.record_marks journal
        (List.map
           [ (aapl, 150.0); (msft, 300.0); (xom, 100.0) ]
           ~f:(fun (symbol, close) ->
             {
               D.Journal.Mark.date = Date.of_string "2026-09-14";
               symbol;
               close;
               qty = 0.0;
             }));
      D.Journal.record_session journal
        {
          D.Journal.Session.date = Date.of_string "2026-09-14";
          equity_close = 1_060_000.0;
          cash_close = 1_000_000.0;
          gross_close = 110_000.0;
          net_close = 70_000.0;
          recorded_at = at "2026-09-14T20:05:00Z";
        };
      let venue =
        D.Sim_venue.create ~opened_at:now
          ~marks:(fun s -> Some (Graph.price graph s))
          ~now:(fun () -> now)
          ~half_spread_bps:(fun _ -> 5.0)
          ~cash:(Notional.of_float 1_000_000.0)
          ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
          ~now:(fun () -> now)
          ~rng:(Random.State.make [| 15 |]) ~on_change:ignore ~on_event:ignore
          ~after_fill:ignore
          ~book_is_current:(fun () -> true)
          ()
      in
      D.Oms.set_clock oms
        (Some
           {
             D.Venue.Session_clock.now;
             is_open = false;
             next_open = at "2026-09-15T13:30:00Z";
             next_close = at "2026-09-15T20:00:00Z";
             next_close_date = Date.of_string "2026-09-15";
           });
      f oms journal)

let leg symbol side qty =
  { Leg.symbol; weight = 0.0; price = 0.0; target = 0; current = 0; side; qty }

let source = { D.Rebalance.Source.strategy = "exp_a01_tech"; sequence = 2 }

let refusal = function
  | Ok _ -> Alcotest.fail "the rebalance was not refused"
  | Error why -> why

let contains what ~substring s =
  Alcotest.(check bool)
    (sprintf "%s: %S contains %S" what s substring)
    true
    (String.is_substring s ~substring)

let journaled journal = List.length (D.Journal.recent_orders journal ~limit:10)

(* A rebalance is gated as a unit. Each order alone passes: AAPL +20 at 150
   takes TECH to 60,000 + 3,000 + 30,000 = 93,000, and MSFT +30 at 300 to
   60,000 + 30,000 + 9,000 = 99,000, both under tech-cap's 100,000. Together
   they take it to 102,000, so the second order breaches it only beside the
   first, and the ONE gate call for the rebalance refuses the whole, naming
   tech-cap and each order. A rule failure refuses the whole as well, naming
   the order and the rule: MSFT +100 at 300 is 30,000, over the 25,000 cap.
   Nothing is journaled by either: [propose_rebalance] returns its refusal
   from this check, before its first write. *)
let test_a_rebalance_is_refused_as_a_unit_when_its_second_order_breaches_a_limit () =
  with_oms
    ~f:(fun oms journal ->
      let check legs = D.Oms.check_rebalance oms ~source ~legs in
      Alcotest.(check bool)
        "AAPL +20 alone passes" true
        (Result.is_ok (check [ leg aapl D.Order.Side.Buy 20 ]));
      Alcotest.(check bool)
        "MSFT +30 alone passes" true
        (Result.is_ok (check [ leg msft D.Order.Side.Buy 30 ]));
      let why =
        refusal (check [ leg aapl D.Order.Side.Buy 20; leg msft D.Order.Side.Buy 30 ])
      in
      contains "as a unit" ~substring:"the gate refuses the rebalance as a unit" why;
      contains "naming the limit" ~substring:"tech-cap would be breached" why;
      contains "naming the second order"
        ~substring:"order 2 of 2 (MSFT buy 30 market-on-open) at 300.00" why;
      let why =
        refusal (check [ leg aapl D.Order.Side.Buy 20; leg msft D.Order.Side.Buy 100 ])
      in
      contains "a rule, naming the order"
        ~substring:"order 2 of 2 (MSFT buy 100 market-on-open): notional: 100 x 300.00"
        why;
      Alcotest.(check int) "and nothing journaled" 0 (journaled journal))
    ()

(* At 17:00 EDT the session is closed but the opening auction's window is
   not open: the rebalance is refused, naming 19:00 ET. *)
let test_a_rebalance_outside_the_window_is_refused_naming_the_boundary () =
  with_oms ~now:(at "2026-09-14T21:00:00Z")
    ~f:(fun oms journal ->
      let why =
        refusal (D.Oms.check_rebalance oms ~source ~legs:[ leg aapl D.Order.Side.Buy 20 ])
      in
      contains "the order and the session rule"
        ~substring:
          "order 1 of 1 (AAPL buy 20 market-on-open): session: it is 2026-09-14 17:00 \
           ET, before 19:00 ET"
        why;
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

let cid n =
  Option.value_exn
    (D.Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZ%02d" n))

(* An order written straight into the journal in [state], with [fills]. *)
let journal_order journal ~n ~source ?(side = D.Order.Side.Buy) ~state ?(fills = []) qty =
  let o =
    {
      (D.Order.create
         {
           D.Order.Request.client_order_id = cid n;
           symbol = aapl;
           side;
           qty;
           kind = D.Order.Kind.Market;
           tif = D.Order.Tif.Opg;
         })
      with
      D.Order.state;
    }
  in
  D.Journal.insert_order journal o ~source ~decision_price:(Price.of_float 150.0)
    ~arrival:None ~verdict:`Null ~at:(at "2026-09-14T23:15:00Z");
  List.iteri fills ~f:(fun i q ->
      ignore
        (D.Journal.record_fill journal o
           {
             D.Order.Fill.execution_id = sprintf "x-%d-%d" n i;
             qty = q;
             price = Price.of_float 150.0;
             at = at "2026-09-15T13:30:00Z";
             position_qty = None;
           }
          : bool))

let tech_strategy =
  {
    Signals_spec.Strategy.name = "exp_a01_tech";
    symbols = [ "AAPL" ];
    max_age = 3;
    sizing = Signals_spec.Live;
    capital_fraction = 0.1;
  }

(* The account holds AAPL 400 (the book above), and the journal says whose:
   the strategy bought 100 in two fills (60 and 40), the owner bought 50 by
   hand, and a strategy whose slug merely begins the same way,
   exp_a01_tech_2, bought 7. The strategy's own position is 100 -- not 400,
   not 150, not 107 -- so a flat signal (no targets, every registered symbol
   at weight 0) sells exactly 100 and leaves the owner's 300 alone. *)
let test_a_flat_signal_sells_exactly_the_strategy's_own_shares () =
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:1 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Filled ~fills:[ 60.0; 40.0 ] 100;
      journal_order journal ~n:2 ~source:"manual" ~state:D.Order.State.Filled
        ~fills:[ 50.0 ] 50;
      journal_order journal ~n:3 ~source:"signal:exp_a01_tech_2:1"
        ~state:D.Order.State.Filled ~fills:[ 7.0 ] 7;
      Alcotest.(check (list (pair string (float 0.0))))
        "its own fills: AAPL 100"
        [ ("AAPL", 100.0) ]
        (List.map (D.Journal.source_fills journal ~prefix:"signal:exp_a01_tech:")
           ~f:(fun (s, q) -> (Symbol.to_string s, q)));
      match D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[] with
      | Error why -> Alcotest.failf "no targets: %s" why
      | Ok legs ->
          Alcotest.(check (list (triple string int int)))
            "sell 100 to a target of 0"
            [ ("sell", 100, 0) ]
            (List.map legs ~f:(fun (l : Leg.t) ->
                 (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target))))
    ()

(* An order of the strategy's that may still fill makes its position
   unknown: an open one (submitted, resting at the venue until the open), and
   a failed one the venue has not said it finished. Sizing on top of either
   would count its shares once as resting and again in the target. A hand
   order resting in the same name does not. A failed order the venue
   reported expired is settled. *)
let test_a_strategy_with_an_order_that_may_still_fill_is_not_sized () =
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:4 ~source:"manual" ~state:D.Order.State.Submitted 10;
      Alcotest.(check bool)
        "a hand order resting: sized" true
        (Result.is_ok
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ]));
      journal_order journal ~n:5 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Submitted 10;
      contains "its own order resting" ~substring:"1 of its orders may still fill"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ]));
      contains "and check_rebalance says the same" ~substring:"may still fill"
        (refusal
           (D.Oms.check_rebalance oms ~source ~legs:[ leg aapl D.Order.Side.Buy 1 ]));
      Alcotest.(check int) "nothing journaled by the refusal" 2 (journaled journal))
    ();
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:6 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Failed 10;
      contains "a failed one the venue may still work" ~substring:"may still fill"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ]));
      let failed =
        (Option.value_exn (D.Journal.load_order journal (cid 6)))
          .D.Journal.Order_row.order
      in
      D.Journal.update_order journal failed ~event:D.Order.Event.Venue_expired
        ~anomaly:None ~at:(at "2026-09-15T13:31:00Z");
      Alcotest.(check bool)
        "once the venue reports it expired: sized" true
        (Result.is_ok
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ])))
    ()

(* Invariant 10's other half: one submit site. The venue's submit is CALLED
   in exactly one place in desk/ -- Oms.submit_journaled -- so a ticket and a
   rebalance reach the wire through the same journal-first path. Every other
   mention is the field's declaration (venue.ml) or an adapter defining it
   (sim_venue.ml, alpaca_trade.ml). *)
let test_the_venue's_submit_is_called_in_one_place () =
  let dir = "../desk" in
  let hits =
    Sys_unix.readdir dir |> Array.to_list
    |> List.filter ~f:(String.is_suffix ~suffix:".ml")
    |> List.sort ~compare:String.compare
    |> List.concat_map ~f:(fun file ->
        In_channel.read_lines (Filename.concat dir file)
        |> List.filter_map ~f:(fun line ->
            if String.is_substring line ~substring:"Trade.submit" then
              Some (file, String.strip line)
            else None))
  in
  let calls, definitions =
    List.partition_tf hits ~f:(fun (_, line) ->
        not
          (String.is_suffix line ~suffix:"Venue.Trade.submit ="
          || String.is_substring line ~substring:"{ Venue.Trade.submit;"))
  in
  Alcotest.(check (list (pair string string)))
    "one call, in oms.ml"
    [ ("oms.ml", "let%map submission = trade.Venue.Trade.submit request in") ]
    calls;
  Alcotest.(check (list string))
    "the two adapters that define it"
    [ "alpaca_trade.ml"; "sim_venue.ml" ]
    (List.map definitions ~f:fst)

let suite =
  ( "rebalance",
    [
      Alcotest.test_case "targets on both sides of a share boundary" `Quick
        test_targets_on_both_sides_of_a_share_boundary;
      Alcotest.test_case "a negative weight rounds toward zero" `Quick
        test_a_negative_weight_rounds_toward_zero;
      Alcotest.test_case "every unknown makes no targets, and says why" `Quick
        test_every_unknown_makes_no_targets_and_says_why;
      Alcotest.test_case
        "a rebalance is refused as a unit when its second order breaches a limit" `Quick
        test_a_rebalance_is_refused_as_a_unit_when_its_second_order_breaches_a_limit;
      Alcotest.test_case "a rebalance outside the window is refused, naming the boundary"
        `Quick test_a_rebalance_outside_the_window_is_refused_naming_the_boundary;
      Alcotest.test_case
        "a flat signal sells exactly the strategy's own shares, not a hand-bought lot"
        `Quick test_a_flat_signal_sells_exactly_the_strategy's_own_shares;
      Alcotest.test_case "a strategy with an order that may still fill is not sized"
        `Quick test_a_strategy_with_an_order_that_may_still_fill_is_not_sized;
      Alcotest.test_case "the venue's submit is called in one place" `Quick
        test_the_venue's_submit_is_called_in_one_place;
    ] )
