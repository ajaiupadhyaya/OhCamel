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
   recorded at 149, 299 and 100, and it is 10:00 EDT (14:00Z) the Tuesday
   after: the session is open, the clock names its close at 16:00 EDT, and
   09:35 has come. Each order is priced off a quote of the live mark a
   half-spread of 5 bps either side, stamped now ([quotes]): a buy at the ask
   x 1.001 rounded up to the cent -- AAPL 150.23, MSFT 300.46, XOM 100.16 --
   a sell at the bid x 0.999 rounded down -- AAPL 149.77, MSFT 299.55, XOM
   99.85. Twenty-day volume is a fixed 1,000,000 shares and the order cap is
   25,000. *)

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
let tuesday = at "2026-09-15T14:00:00Z"

(* ------------------------------------------------------------------------ *)
(* The targets, by hand                                                      *)
(* ------------------------------------------------------------------------ *)

(* The account holds whatever the strategy does unless a case says otherwise:
   [account] defaults to the strategy's own position. *)
let plan ?(symbols = [ spy ]) ?(capital_fraction = 0.5) ?(scale = 1.0)
    ?(equity = Ok 100_000.0) ?(current = Ok Symbol.Map.empty) ?account ~price weights =
  let account =
    match account with
    | Some a -> a
    | None -> Result.ok current |> Option.value ~default:Symbol.Map.empty
  in
  D.Rebalance.plan ~symbols ~weights ~capital_fraction ~scale ~equity ~account
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
       ~capital_fraction:0.5 ~scale:1.0 ~equity:(Ok 100_000.0) ~account:Symbol.Map.empty
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

(* The effective scale (Task 9; Task 11 wires the overlay that sets it):
   target = trunc(w x f x scale x E / p).
     scale 0.5: 1.0 x 0.5 x 0.5 x 100,000 / 100 = 250 -> buy 250 from 0
     scale 0:   1.0 x 0.5 x 0   x 100,000 / 100 = 0   -> sell the 300 held
   A scale outside [0, 1] is no targets: it would lever the strategy past
   its fraction, and a NaN is not in it either. *)
let test_scale_halves_the_target () =
  Alcotest.(check (list (triple string int int)))
    "250 bought"
    [ ("buy", 250, 250) ]
    (legs (plan ~scale:0.5 ~price:100.0 [ (spy, 1.0) ]))

let test_scale_zero_flattens () =
  Alcotest.(check (list (triple string int int)))
    "the 300 held sold"
    [ ("sell", 300, 0) ]
    (legs
       (plan ~scale:0.0 ~price:100.0
          ~current:(Ok (Symbol.Map.singleton spy 300.0))
          [ (spy, 1.0) ]))

let test_scale_1_5_refused () =
  List.iter [ 1.5; -0.1; Float.nan ] ~f:(fun scale ->
      match plan ~scale ~price:100.0 [ (spy, 1.0) ] with
      | Ok _ -> Alcotest.failf "scale %g was sized" scale
      | Error why ->
          Alcotest.(check bool)
            (sprintf "scale %g: %S" scale why)
            true
            (String.is_substring why ~substring:"is not in [0, 1]"))

(* Three incubation strategies at 0.025 under (incubation_cap 0.05): their
   fractions sum to 0.075, so the cap's ratio is
     min(1, 0.05 / 0.075) = 0.666667,
   folded into the scale, never the fraction: f stays 0.025, and with
   E 100,000, p 100, w 1.0 and an effective scale of 1:
     1.0 x 0.025 x (1 x 0.666667) x 100,000 / 100 = 16.667 -> 16,
   the same 0.016667 of E that check-book prints as the effective fraction. *)
let test_an_incubation_strategy_is_sized_at_its_effective_fraction () =
  let inc name =
    {
      Signals_spec.Strategy.name;
      symbols = [ "SPY" ];
      max_age = 3;
      sizing = Signals_spec.Incubation;
      capital_fraction = 0.025;
      cadence = Signals_spec.Cadence.Daily;
      adjust = Signals_spec.Adjust.Intraday;
      max_age_minutes = 20;
      horizon = Signals_spec.Horizon.Carry;
      stop = None;
      reinstated = None;
    }
  in
  let a = inc "inc_a" in
  let roster = { Signals_spec.strategies = [ a; inc "inc_b"; inc "inc_c" ] } in
  let ratio = Signals_spec.incubation_ratio ~incubation_cap:0.05 roster in
  Alcotest.(check (float 1e-9)) "0.05 / 0.075" (0.05 /. 0.075) ratio;
  Alcotest.(check (float 1e-9))
    "the effective fraction, 0.025 x 0.666667"
    (0.025 *. 0.05 /. 0.075)
    (Signals_spec.effective_fraction ~incubation_cap:0.05 roster a);
  Alcotest.(check (list (triple string int int)))
    "16 bought"
    [ ("buy", 16, 16) ]
    (legs
       (plan ~capital_fraction:0.025 ~scale:(1.0 *. ratio) ~price:100.0 [ (spy, 1.0) ]))

(* ------------------------------------------------------------------------ *)
(* The order manager's half                                                  *)
(* ------------------------------------------------------------------------ *)

let limit name scope n =
  { Limit.name; scope; kind = Limit.Gross_notional (Notional.of_float n) }

let with_oms ?(now = tuesday) ?(session_date = "2026-09-14")
    ?(closes = [ (aapl, 149.0); (msft, 299.0); (xom, 100.0) ])
    ?(spec = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled })
    ?(book_is_current = true) ?(held = [ (aapl, 400.0); (msft, 100.0); (xom, -200.0) ])
    ?(halt = D.Halt.create D.Halt.Source.none) ?(on_event = ignore) ?(trips_on = [])
    ?(returns = [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |])
    ?(limits =
      [
        limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
        limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
      ]) ~f () =
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter held ~f:(fun (s, q) ->
          let p =
            List.Assoc.find_exn
              [ (aapl, 150.0); (msft, 300.0); (xom, 100.0) ]
              s ~equal:Symbol.equal
          in
          Graph.set_qty graph s (Qty.of_float q);
          Graph.set_returns graph s returns;
          Graph.apply_tick graph
            { Tick.symbol = s; price = Price.of_float p; time = Time.now () });
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      D.Journal.record_marks journal
        (List.map closes ~f:(fun (symbol, close) ->
             {
               D.Journal.Mark.date = Date.of_string session_date;
               symbol;
               close;
               qty = 0.0;
             }));
      D.Journal.record_session journal
        {
          D.Journal.Session.date = Date.of_string session_date;
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
        D.Oms.create ~graph ~journal ~spec ~trips_on
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
          ~now:(fun () -> now)
          ~rng:(Random.State.make [| 15 |]) ~on_change:ignore ~on_event ~after_fill:ignore
          ~book_is_current:(fun () -> book_is_current)
          ()
      in
      D.Oms.set_clock oms
        (Some
           {
             D.Venue.Session_clock.now;
             is_open = true;
             next_open = at "2026-09-16T13:30:00Z";
             next_close = at "2026-09-15T20:00:00Z";
             next_close_date = Date.of_string "2026-09-15";
           });
      f oms journal)

(* The quote each order is priced off, as [send_rebalance] would have
   fetched it: the graph's live mark a half-spread of 5 bps either side,
   stamped at the order manager's now. *)
let quotes oms symbol =
  let m = Price.to_float (Graph.price oms.D.Oms.graph symbol) in
  let now = oms.D.Oms.now () in
  Some
    {
      D.Rebalance.Fetched.quote =
        {
          D.Venue.Quote.bid = Price.of_float (m *. 0.9995);
          ask = Price.of_float (m *. 1.0005);
          at = now;
        };
      at = now;
    }

(* The same quote, fetched (and so stamped) [age] seconds earlier. *)
let fetched_earlier oms age symbol =
  Option.map (quotes oms symbol) ~f:(fun f ->
      let back t = Time_ns.sub t (Time_ns.Span.of_sec age) in
      {
        D.Rebalance.Fetched.quote =
          {
            f.D.Rebalance.Fetched.quote with
            D.Venue.Quote.at = back f.D.Rebalance.Fetched.at;
          };
        at = back f.D.Rebalance.Fetched.at;
      })

let leg symbol side qty =
  { Leg.symbol; weight = 0.0; price = 0.0; target = 0; current = 0; side; qty }

let source =
  {
    D.Rebalance.Source.strategy = "exp_a01_tech";
    sequence = 2;
    as_of = Date.of_string "2026-09-14";
  }

let refusal = function
  | Ok _ -> Alcotest.fail "the rebalance was not refused"
  | Error why -> why

let contains what ~substring s =
  Alcotest.(check bool)
    (sprintf "%s: %S contains %S" what s substring)
    true
    (String.is_substring s ~substring)

let journaled journal = List.length (D.Journal.recent_orders journal ~limit:10)

let passes_check what r =
  match r with Ok _ -> () | Error why -> Alcotest.failf "%s was refused: %s" what why

(* A rebalance is gated as a unit. Each order alone passes: AAPL +20 at 150
   takes TECH to 60,000 + 3,000 + 30,000 = 93,000, and MSFT +30 at 300 to
   60,000 + 30,000 + 9,000 = 99,000, both under tech-cap's 100,000. Together
   they take it to 102,000, so the second order breaches it only beside the
   first, and the ONE gate call for the rebalance refuses the whole, naming
   tech-cap and each order. A rule failure refuses the whole as well, naming
   the order and the rule: MSFT +100 at its close of 299 is 29,900, over the
   25,000 cap. (The gate values TECH at the live marks; each order's price,
   named in the refusal, is its recorded close.)
   Nothing is journaled by either: [propose_rebalance] returns its refusal
   from this check, before its first write. *)
let test_a_rebalance_is_refused_as_a_unit_when_its_second_order_breaches_a_limit () =
  with_oms
    ~f:(fun oms journal ->
      let check legs = D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs in
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
        ~substring:"order 2 of 2 (MSFT buy 30 day limit at 300.46) at 300.46" why;
      let why =
        refusal (check [ leg aapl D.Order.Side.Buy 20; leg msft D.Order.Side.Buy 100 ])
      in
      contains "a rule, naming the order"
        ~substring:
          "order 2 of 2 (MSFT buy 100 day limit at 300.46): notional: 100 x 300.46"
        why;
      Alcotest.(check int) "and nothing journaled" 0 (journaled journal))
    ()

(* Nothing the desk decides is sent before 09:35 New York. At 09:31 EDT on
   Tuesday the session is open, but the rebalance is refused by the session
   rule for timing alone -- it may go unchanged at 09:35 -- naming the hour
   and 09:35, and nothing is journaled; at 09:35 it passes. *)
let test_nothing_is_sent_before_09_35_new_york () =
  with_oms ~now:(at "2026-09-15T13:31:00Z")
    ~f:(fun oms journal ->
      let timing = ref false in
      let why =
        refusal
          (D.Oms.check_rebalance ~timing oms ~quotes:(quotes oms) ~source
             ~legs:[ leg aapl D.Order.Side.Buy 20 ])
      in
      contains "the order and the session rule"
        ~substring:
          "order 1 of 1 (AAPL buy 20 day limit at 150.23): session: it is 2026-09-15 \
           09:31 ET: the desk sends its own orders from 09:35 ET"
        why;
      Alcotest.(check bool) "for timing alone" true !timing;
      Alcotest.(check (option string))
        "the send window says the same"
        (Some
           "it is 2026-09-15 09:31 ET: the desk sends its own orders from 09:35 ET, once \
            the opening quotes have settled")
        (D.Oms.send_window oms);
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ();
  with_oms ~now:(at "2026-09-15T13:35:00Z")
    ~f:(fun oms _ ->
      Alcotest.(check (option string))
        "09:35: the window is open" None (D.Oms.send_window oms);
      passes_check "at 09:35 it passes"
        (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
           ~legs:[ leg aapl D.Order.Side.Buy 20 ]))
    ()

let cid n =
  Option.value_exn
    (D.Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZ%02d" n))

(* An order written straight into the journal in [state], with [fills]. *)
let journal_order journal ~n ~source ?(symbol = aapl) ?(side = D.Order.Side.Buy) ~state
    ?(fills = []) qty =
  let o =
    {
      (D.Order.create
         {
           D.Order.Request.client_order_id = cid n;
           symbol;
           side;
           qty;
           kind = D.Order.Kind.Market;
           tif = D.Order.Tif.Day;
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
    cadence = Signals_spec.Cadence.Daily;
    adjust = Signals_spec.Adjust.Intraday;
    max_age_minutes = 20;
    horizon = Signals_spec.Horizon.Carry;
    stop = None;
    reinstated = None;
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
      match D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[] ~scale:1.0 with
      | Error why -> Alcotest.failf "no targets: %s" why
      | Ok (legs, _) ->
          Alcotest.(check (list (triple string int int)))
            "sell 100 to a target of 0"
            [ ("sell", 100, 0) ]
            (List.map legs ~f:(fun (l : Leg.t) ->
                 (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target))))
    ()

(* R2 at the order manager: a limit's trip -- here one restored from the
   journal, as a restart leaves it -- with the book's (reduce_through_trip)
   word on. The strategy owns AAPL +100 of the account's 400 and XOM -50 of
   the account's -200 (the book above). Its rules context carries both, so a
   sell of its 100 AAPL passes, and so does a buy of 50 XOM to cover. A sell
   of 120 AAPL is inside the account's 400 but takes the strategy through
   zero: refused whole, the order named with the switch, and nothing
   journaled -- never clipped to 100. A buy of 60 XOM through the strategy's
   -50 is refused the same way, and so is any order that grows a position.
   With the word off, the sell of 100 is refused as every order was before. *)
let test_a_strategy's_reduction_passes_a_limit's_trip_and_a_flip_is_refused_whole () =
  let halt = D.Halt.create D.Halt.Source.none in
  D.Halt.restore_trip halt ~limit:"aapl-cap" ~at:(at "2026-09-14T21:00:00Z");
  let fills journal =
    journal_order journal ~n:1 ~source:"signal:exp_a01_tech:1" ~state:D.Order.State.Filled
      ~fills:[ 100.0 ] 100;
    journal_order journal ~n:2 ~source:"signal:exp_a01_tech:1" ~symbol:xom
      ~side:D.Order.Side.Sell ~state:D.Order.State.Filled ~fills:[ 50.0 ] 50
  in
  let on = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled } in
  let on = { on with Desk_spec.reduce_through_trip = true } in
  with_oms ~halt ~spec:on
    ~f:(fun oms journal ->
      fills journal;
      let before = journaled journal in
      let check legs = D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs in
      Alcotest.(check bool)
        "sell 100 AAPL, the strategy's whole own position, passes the trip" true
        (Result.is_ok (check [ leg aapl D.Order.Side.Sell 100 ]));
      Alcotest.(check bool)
        "buy 50 XOM, covering the strategy's whole short, passes the trip" true
        (Result.is_ok (check [ leg xom D.Order.Side.Buy 50 ]));
      Alcotest.(check bool)
        "both at once pass" true
        (Result.is_ok
           (check [ leg aapl D.Order.Side.Sell 100; leg xom D.Order.Side.Buy 50 ]));
      let why = refusal (check [ leg aapl D.Order.Side.Sell 120 ]) in
      contains "sell 120 through the strategy's zero: refused, naming the switch"
        ~substring:"order 1 of 1 (AAPL sell 120 day limit at 149.77): kill_switch:" why;
      contains "with the restored trip's words" ~substring:"(held across a restart)" why;
      let why = refusal (check [ leg xom D.Order.Side.Buy 60 ]) in
      contains "buy 60 through the short's zero: refused"
        ~substring:"(XOM buy 60 day limit at 100.16): kill_switch:" why;
      let why =
        refusal (check [ leg aapl D.Order.Side.Sell 100; leg xom D.Order.Side.Buy 60 ])
      in
      contains "a flip beside a reduction refuses the rebalance whole"
        ~substring:"(XOM buy 60 day limit at 100.16): kill_switch:" why;
      let why = refusal (check [ leg aapl D.Order.Side.Buy 10 ]) in
      contains "a buy that grows the long: refused"
        ~substring:"(AAPL buy 10 day limit at 150.23): kill_switch:" why;
      Alcotest.(check int) "and nothing journaled" before (journaled journal))
    ();
  with_oms ~halt
    ~f:(fun oms journal ->
      fills journal;
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg aapl D.Order.Side.Sell 100 ])
      in
      contains "the word off: the sell of 100 is refused"
        ~substring:"(AAPL sell 100 day limit at 149.77): kill_switch:" why)
    ()

(* Two reductions in one name are judged together. The strategy owns AAPL
   +100 of the account's 400, and a hand's sell of 350 AAPL rests at the
   venue: if it fills the account holds 50, so the strategy's sell of 100
   under the trip -- no larger than its own 100 or the account's 400 -- would
   take the account through zero, and is refused. A sell of 50 is not. *)
let test_a_resting_reduction_in_the_name_shrinks_the_room_under_a_trip () =
  let halt = D.Halt.create D.Halt.Source.none in
  D.Halt.restore_trip halt ~limit:"aapl-cap" ~at:(at "2026-09-14T21:00:00Z");
  let on =
    {
      Desk_spec.default with
      Desk_spec.trading = Desk_spec.Enabled;
      reduce_through_trip = true;
    }
  in
  with_oms ~halt ~spec:on
    ~f:(fun oms journal ->
      journal_order journal ~n:1 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Filled ~fills:[ 100.0 ] 100;
      let resting =
        D.Order.create
          {
            D.Order.Request.client_order_id = cid 9;
            symbol = aapl;
            side = D.Order.Side.Sell;
            qty = 350;
            kind = D.Order.Kind.Market;
            tif = D.Order.Tif.Day;
          }
      in
      D.Journal.insert_order journal resting ~source:"manual"
        ~decision_price:(Price.of_float 150.0) ~arrival:None ~verdict:`Null
        ~at:(at "2026-09-14T23:00:00Z");
      ignore (D.Oms.record oms resting (D.Order.Event.Acknowledged "venue-9") : D.Order.t);
      let check legs = D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs in
      let why = refusal (check [ leg aapl D.Order.Side.Sell 100 ]) in
      contains "sell 100 beside the resting 350: refused"
        ~substring:"(AAPL sell 100 day limit at 149.77): kill_switch:" why;
      contains "naming the room left" ~substring:"the account's (50)" why;
      Alcotest.(check bool)
        "sell 50 fits what is left" true
        (Result.is_ok (check [ leg aapl D.Order.Side.Sell 50 ])))
    ()

(* And a failed order is counted as the open one is: a hand's sell of 350
   AAPL every lookup missed, created in the latest session and finished by no
   venue report, may still be worked and fill, so it takes the account's room
   to 50 as the resting one did. *)
let test_a_failed_reduction_the_venue_may_still_work_shrinks_the_room_too () =
  let halt = D.Halt.create D.Halt.Source.none in
  D.Halt.restore_trip halt ~limit:"aapl-cap" ~at:(at "2026-09-14T21:00:00Z");
  let on =
    {
      Desk_spec.default with
      Desk_spec.trading = Desk_spec.Enabled;
      reduce_through_trip = true;
    }
  in
  with_oms ~halt ~spec:on
    ~f:(fun oms journal ->
      journal_order journal ~n:1 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Filled ~fills:[ 100.0 ] 100;
      journal_order journal ~n:9 ~source:"manual" ~side:D.Order.Side.Sell
        ~state:D.Order.State.Failed 350;
      let check legs = D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs in
      let why = refusal (check [ leg aapl D.Order.Side.Sell 100 ]) in
      contains "sell 100 beside the failed 350: refused"
        ~substring:"(AAPL sell 100 day limit at 149.77): kill_switch:" why;
      contains "naming the room left" ~substring:"the account's (50)" why;
      Alcotest.(check bool)
        "sell 50 fits what is left" true
        (Result.is_ok (check [ leg aapl D.Order.Side.Sell 50 ])))
    ()

(* ------------------------------------------------------------------------ *)
(* One sub-ledger check: two strategies on opposite sides of one name        *)
(* ------------------------------------------------------------------------ *)

(* The pair, in AAPL (this book's stand-in for one ETF two strategies
   trade): alpha bought 100, beta sold 50 short, and the account holds the
   net -- +50 unless a case says otherwise. MSFT 100 and XOM -200 are held by
   no strategy: the droplet's seed lots, in names no strategy trades. *)
let alpha = { tech_strategy with Signals_spec.Strategy.name = "alpha" }
let beta = { tech_strategy with Signals_spec.Strategy.name = "beta" }

let source_of (s : Signals_spec.Strategy.t) =
  {
    D.Rebalance.Source.strategy = s.name;
    sequence = 2;
    as_of = Date.of_string "2026-09-14";
  }

let pair_fills journal =
  journal_order journal ~n:1 ~source:"signal:alpha:1" ~state:D.Order.State.Filled
    ~fills:[ 100.0 ] 100;
  journal_order journal ~n:2 ~source:"signal:beta:1" ~side:D.Order.Side.Sell
    ~state:D.Order.State.Filled ~fills:[ 50.0 ] 50

let pair_held aapl_qty = [ (aapl, aapl_qty); (msft, 100.0); (xom, -200.0) ]

let set_account oms q =
  Graph.set_qty oms.D.Oms.graph aapl (Qty.of_float q);
  Graph.stabilize oms.D.Oms.graph

let check_for oms s legs =
  D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source:(source_of s) ~legs

let passes what r =
  match r with Ok _ -> () | Error why -> Alcotest.failf "%s was refused: %s" what why

(* Both strategies of a netted pair are sizable. The old per-strategy rule
   asked each one alone whether the account held its own position: alpha's
   +100 against the account's +50 refused alpha, and beta's -50 against
   +50 refused beta, forever. Summed, 100 - 50 = 50 is the account, and
   each plans and passes: alpha up to 135 (0.2 x 0.1 x E / 150, the current
   mark, E = 1,000,000 + 7,500 + 30,000 - 20,000 = 1,017,500), beta down to
   -67. *)
let test_both_strategies_of_a_netted_pair_are_sized () =
  with_oms ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      let plan s weight =
        match
          D.Oms.plan_rebalance oms ~strategy:s ~targets:[ (aapl, weight) ] ~scale:1.0
        with
        | Ok (legs, _) -> legs
        | Error why ->
            Alcotest.failf "%s: no targets: %s" s.Signals_spec.Strategy.name why
      in
      let a = plan alpha 0.2 and b = plan beta (-0.1) in
      Alcotest.(check (list (triple string int int)))
        "alpha buys 35 to 135, beta sells 17 to -67"
        [ ("buy", 35, 135); ("sell", 17, -67) ]
        (List.map (a @ b) ~f:(fun (l : Leg.t) ->
             (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target)));
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      passes "alpha's rebalance" (check_for oms alpha a);
      passes "beta's rebalance" (check_for oms beta b);
      Alcotest.(check int)
        "no break alerted" 0
        (List.length (D.Journal.recent_alerts journal ~limit:10)))
    ()

(* A STOPPED STRATEGY CAN LEAVE (Task 1.9, round 1). The netted pair alone
   in the book -- alpha +100 AAPL, beta -50, the account +50 (7,500 at 150),
   MSFT and XOM flat, equity 1,007,500 -- under each exposure limit in turn,
   each set so the account's +50 is inside it and +100 is over it:
     name-cap   Name_weight 0.01:  7,500 / 1,007,500 = 0.74%; 100 shares at
                150 against equity 1,007,500 less the cover's half-spread =
                1.49%;
     class-tech Class_gross 0.01 (class-energy 0.35 beside it, so every
                sector has one): the same figures, TECH holding AAPL alone;
     book-cap   Gross_notional 10,000: 7,500, then 15,000.
   beta's stop has latched. Its flatten -- a cover of its own 50 -- raises
   the account's long, and:
   - as an ordinary rebalance it is refused, naming the limit, as before;
   - as the stop's flatten it passes;
   - as a flatten of a strategy the journal does not say is stopped, or one
     that would take beta through zero (a cover of 60), it is refused;
   - once the cover has filled (the account at +100, beta flat), alpha's raise
     is refused, naming the limit: the breach the flatten left blocks raises
     as any breach does, and a stop flatten's word does not help alpha. *)
let exposure_limit_cases =
  [
    ( "name-cap",
      [
        {
          Limit.name = "name-cap";
          scope = Limit.Portfolio;
          kind = Limit.Name_weight 0.01;
        };
      ] );
    ( "class-tech",
      [
        {
          Limit.name = "class-tech";
          scope = Limit.Sector (Sector.of_string "TECH");
          kind = Limit.Class_gross 0.01;
        };
        {
          Limit.name = "class-energy";
          scope = Limit.Sector (Sector.of_string "ENERGY");
          kind = Limit.Class_gross 0.35;
        };
      ] );
    ("book-cap", [ limit "book-cap" Limit.Portfolio 10_000.0 ]);
  ]

let stop journal s =
  D.Journal.set_strategy_state journal ~strategy:s ~state:`Stopped ~rule:(Some "drawdown")
    ~at:evening ~detail:"stopped for the test"

let flatten_for oms s legs =
  D.Oms.check_rebalance ~stop_flatten:true oms ~quotes:(quotes oms) ~source:(source_of s)
    ~legs

let test_a_stopped_strategy's_flatten_passes_the_exposure_limits () =
  let flat = [ (aapl, 50.0); (msft, 0.0); (xom, 0.0) ] in
  List.iter exposure_limit_cases ~f:(fun (name, limits) ->
      with_oms ~held:flat ~limits
        ~f:(fun oms journal ->
          pair_fills journal;
          let cover = [ leg aapl D.Order.Side.Buy 50 ] in
          contains
            (name ^ ": beta's cover, still active, as a flatten")
            ~substring:(name ^ " would be breached")
            (refusal (flatten_for oms beta cover));
          stop journal "beta";
          contains
            (name ^ ": beta's cover as an ordinary rebalance")
            ~substring:(name ^ " would be breached")
            (refusal (check_for oms beta cover));
          passes (name ^ ": beta's stop flatten") (flatten_for oms beta cover);
          contains
            (name ^ ": a flatten through beta's zero")
            ~substring:(name ^ " would be breached")
            (refusal (flatten_for oms beta [ leg aapl D.Order.Side.Buy 60 ]));
          (* The cover fills: beta flat, the account +100. *)
          journal_order journal ~n:3 ~source:"signal:beta:2" ~state:D.Order.State.Filled
            ~fills:[ 50.0 ] 50;
          set_account oms 100.0;
          let raise = [ leg aapl D.Order.Side.Buy 10 ] in
          contains
            (name ^ ": then alpha's raise")
            ~substring:(name ^ " would be further over its line")
            (refusal (check_for oms alpha raise));
          contains
            (name ^ ": and alpha's raise called a flatten")
            ~substring:(name ^ " would be further over its line")
            (refusal (flatten_for oms alpha raise)))
        ())

(* Round 1b: an unevaluable trip-limit does not hold a stop's flatten either.
   The netted pair alone in the book, es-cap the switch's trip-limit and
   unevaluable -- nine sessions of a ten-session window. beta's cover raises
   the account's gross (+50 to +100), so as an ordinary rebalance it is
   refused while es-cap is unknown; as the stop's flatten it passes. Once it
   has filled, alpha's raise is refused as before, flatten word or not. *)
let test_a_stop_flatten_passes_an_unevaluable_trip_limit () =
  with_oms
    ~held:[ (aapl, 50.0); (msft, 0.0); (xom, 0.0) ]
    ~returns:[| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01 |]
    ~trips_on:[ "es-cap" ]
    ~limits:
      [
        {
          Limit.name = "es-cap";
          scope = Limit.Portfolio;
          kind = Limit.Expected_shortfall (Notional.of_float 1e9);
        };
      ]
    ~f:(fun oms journal ->
      pair_fills journal;
      stop journal "beta";
      let cover = [ leg aapl D.Order.Side.Buy 50 ] in
      contains "beta's cover as an ordinary rebalance"
        ~substring:"es-cap cannot be evaluated"
        (refusal (check_for oms beta cover));
      passes "beta's stop flatten" (flatten_for oms beta cover);
      journal_order journal ~n:3 ~source:"signal:beta:2" ~state:D.Order.State.Filled
        ~fills:[ 50.0 ] 50;
      set_account oms 100.0;
      let raise = [ leg aapl D.Order.Side.Buy 10 ] in
      contains "then alpha's raise" ~substring:"es-cap cannot be evaluated"
        (refusal (check_for oms alpha raise));
      contains "and alpha's raise called a flatten"
        ~substring:"es-cap cannot be evaluated"
        (refusal (flatten_for oms alpha raise)))
    ()

(* Under a trip nothing changes: the switch's own rule (R2, its word on here)
   lets beta's reduction past the rules, and the gate still refuses the cover
   that breaches name-cap -- a stop's flatten or not, the same sentence. *)
let test_under_a_trip_a_stop_flatten_is_gated_as_before () =
  let halt = D.Halt.create D.Halt.Source.none in
  D.Halt.restore_trip halt ~limit:"es-cap" ~at:(at "2026-09-14T21:00:00Z");
  let spec =
    {
      Desk_spec.default with
      Desk_spec.trading = Desk_spec.Enabled;
      reduce_through_trip = true;
    }
  in
  with_oms ~halt ~spec
    ~held:[ (aapl, 50.0); (msft, 0.0); (xom, 0.0) ]
    ~limits:(snd (List.hd_exn exposure_limit_cases))
    ~f:(fun oms journal ->
      pair_fills journal;
      stop journal "beta";
      let cover = [ leg aapl D.Order.Side.Buy 50 ] in
      let ordinary = refusal (check_for oms beta cover) in
      let flatten = refusal (flatten_for oms beta cover) in
      contains "tripped: the gate refuses the flatten"
        ~substring:"name-cap would be breached" flatten;
      Alcotest.(check string)
        "and says exactly what it says to an ordinary rebalance" ordinary flatten)
    ()

(* The account at +40: a break, {AAPL; 50; 40}. Seen once it refuses
   nothing -- it may be a fill on its way from the venue to the journal --
   and seen again at the next settled check it stands. Then an increase of
   either strategy's own position is refused, naming both figures. A
   reduction goes only if it also shrinks the account's position by no more
   than it holds (fix round 1, I1): alpha's sell of 40 does; its sell of 100
   would take the account's +40 to -60, a short nobody holds, and is
   refused in the sub-ledger's words; beta's cover grows the account's long
   and is refused the same way until the break is settled. An order in a
   name with no break is not touched. *)
let test_a_standing_break_refuses_either_strategy's_increase_and_holds_a_reduction_to_the_account
    () =
  with_oms ~held:(pair_held 40.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      D.Oms.check_subledger oms ~at:evening;
      passes "alpha's buy at the first sight of the break"
        (check_for oms alpha [ leg aapl D.Order.Side.Buy 10 ]);
      D.Oms.check_subledger oms ~at:evening;
      List.iter
        [
          ("alpha's buy", alpha, leg aapl D.Order.Side.Buy 10);
          ("beta's sell", beta, leg aapl D.Order.Side.Sell 10);
          ("alpha's sell through its zero", alpha, leg aapl D.Order.Side.Sell 150);
          ( "alpha's sell of 100 through the account's zero",
            alpha,
            leg aapl D.Order.Side.Sell 100 );
          ("beta's cover, growing the account's long", beta, leg aapl D.Order.Side.Buy 50);
        ]
        ~f:(fun (what, s, l) ->
          let why = refusal (check_for oms s [ l ]) in
          contains what ~substring:"sub-ledger" why;
          contains what ~substring:"sum to 50" why;
          contains what ~substring:"holds 40" why);
      contains "a reduction's refusal says how to settle"
        ~substring:"reverse the trade made outside the desk at the venue"
        (refusal (check_for oms alpha [ leg aapl D.Order.Side.Sell 100 ]));
      passes "alpha's sell of 40, inside the account's 40"
        (check_for oms alpha [ leg aapl D.Order.Side.Sell 40 ]);
      passes "alpha's buy in MSFT, a name with no break"
        (check_for oms alpha [ leg msft D.Order.Side.Buy 10 ]))
    ()

(* A standing break binds while the name cannot be judged (fix round 1,
   C1). With several strategies in one ETF something often rests in it, and
   a name with an order that may still fill is not judged at all -- so the
   break must not drop out of the refusal then. Another strategy's order
   (gamma's buy of 5) resting in AAPL, and separately a hand's buy of 10:
   alpha's increase is still refused, naming the figures last judged. Once
   the resting order is done and a check judges the name whole, the break
   clears and the increase passes. *)
let test_a_standing_break_binds_while_an_order_rests_in_the_name () =
  let rest journal oms ~n ~source qty =
    let o =
      D.Order.create
        {
          D.Order.Request.client_order_id = cid n;
          symbol = aapl;
          side = D.Order.Side.Buy;
          qty;
          kind = D.Order.Kind.Market;
          tif = D.Order.Tif.Day;
        }
    in
    D.Journal.insert_order journal o ~source ~decision_price:(Price.of_float 150.0)
      ~arrival:None ~verdict:`Null ~at:(at "2026-09-14T23:00:00Z");
    D.Oms.record oms o (D.Order.Event.Acknowledged (sprintf "venue-%d" n))
  in
  List.iter
    [ ("another strategy's", "signal:gamma:1"); ("a hand's", "manual") ]
    ~f:(fun (whose, source) ->
      with_oms ~held:(pair_held 40.0)
        ~f:(fun oms journal ->
          pair_fills journal;
          D.Oms.check_subledger oms ~at:evening;
          D.Oms.check_subledger oms ~at:evening;
          let resting = rest journal oms ~n:9 ~source 5 in
          D.Oms.check_subledger oms ~at:evening;
          let why = refusal (check_for oms alpha [ leg aapl D.Order.Side.Buy 10 ]) in
          contains (whose ^ " order resting: alpha's increase") ~substring:"sum to 50" why;
          contains (whose ^ " order resting: the figures") ~substring:"holds 40" why;
          ignore (D.Oms.record oms resting D.Order.Event.Venue_cancelled : D.Order.t);
          set_account oms 50.0;
          D.Oms.check_subledger oms ~at:evening;
          passes
            (whose ^ " order done and the name whole: alpha's increase")
            (check_for oms alpha [ leg aapl D.Order.Side.Buy 10 ]))
        ())

(* One alert per break, at the second settled sight of it, and none after
   while it stands; a name with an order that may still fill is not judged
   at all; a name that sums again is said once, and a break after that is a
   new occurrence. The seed lots -- MSFT 100 and XOM -200, and AAPL's own
   hand ticket -- are never breaks. *)
let test_a_break_is_alerted_once_when_it_stands () =
  let lines = ref [] in
  with_oms ~held:(pair_held 40.0)
    ~on_event:(fun l -> lines := l :: !lines)
    ~f:(fun oms journal ->
      pair_fills journal;
      let alerts () =
        List.filter (D.Journal.recent_alerts journal ~limit:50) ~f:(fun a ->
            String.equal a.D.Journal.Alert.kind "subledger_break")
      in
      let count what n = Alcotest.(check int) what n (List.length (alerts ())) in
      D.Oms.check_subledger oms ~at:evening;
      count "seen once: no alert" 0;
      D.Oms.check_subledger oms ~at:evening;
      count "seen twice: one alert" 1;
      (match alerts () with
      | [ a ] ->
          Alcotest.(check string) "for AAPL" "AAPL" a.D.Journal.Alert.limit_name;
          contains "the line" ~substring:"sum to 50, and the account holds 40"
            a.D.Journal.Alert.line
      | _ -> ());
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      count "still standing: still one" 1;
      Alcotest.(check bool)
        "the log said it" true
        (List.exists !lines ~f:(String.is_substring ~substring:"sum to 50"));
      set_account oms 50.0;
      D.Oms.check_subledger oms ~at:evening;
      Alcotest.(check bool)
        "whole again, said" true
        (List.exists !lines ~f:(String.is_substring ~substring:"AAPL sums again"));
      (* A hand's buy of 10 resting in AAPL, and a read of the account that
         already holds its fill (+60) before the fill's report reaches the
         journal: the name is not judged while the order may fill, however
         many checks pass. *)
      let resting =
        D.Order.create
          {
            D.Order.Request.client_order_id = cid 9;
            symbol = aapl;
            side = D.Order.Side.Buy;
            qty = 10;
            kind = D.Order.Kind.Market;
            tif = D.Order.Tif.Day;
          }
      in
      D.Journal.insert_order journal resting ~source:"manual"
        ~decision_price:(Price.of_float 150.0) ~arrival:None ~verdict:`Null
        ~at:(at "2026-09-14T23:00:00Z");
      ignore (D.Oms.record oms resting (D.Order.Event.Acknowledged "venue-9") : D.Order.t);
      set_account oms 60.0;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      count "unsettled: not judged" 1;
      (* The report arrives: the hand's sub-ledger holds +10 beside the
         pair's 50, and the account's 60 sums. *)
      ignore
        (D.Oms.record oms resting
           (D.Order.Event.Venue_fill
              {
                D.Order.Fill.execution_id = "hand-10";
                qty = 10.0;
                price = Price.of_float 150.0;
                at = evening;
                position_qty = None;
              })
          : D.Order.t);
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      count "the hand's ticket is its own sub-ledger: whole" 1;
      set_account oms 55.0;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      count "a new break, a new alert" 2)
    ();
  (* The droplet's book alone: lots no strategy holds, in names no strategy
     trades. Nothing to judge, nothing alerted. *)
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:2 ~source:"manual" ~state:D.Order.State.Filled
        ~fills:[ 50.0 ] 50;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      Alcotest.(check int)
        "the seed lots are no break" 0
        (List.length (D.Journal.recent_alerts journal ~limit:10)))
    ()

(* The nightly close says each standing break once, as its own alert, and a
   close with none says so on the log. *)
let test_the_close_says_every_standing_break () =
  let lines = ref [] in
  with_oms ~held:(pair_held 40.0)
    ~on_event:(fun l -> lines := l :: !lines)
    ~f:(fun oms journal ->
      pair_fills journal;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.close_subledger oms ~date:(Date.of_string "2026-09-14") ~at:evening;
      let alerts =
        List.filter (D.Journal.recent_alerts journal ~limit:50) ~f:(fun a ->
            String.equal a.D.Journal.Alert.kind "subledger_break")
      in
      Alcotest.(check int)
        "two alerts: the sync's, then the close's" 2 (List.length alerts);
      (match alerts with
      | newest :: _ ->
          contains "the close's line" ~substring:"the close of 2026-09-14"
            newest.D.Journal.Alert.line
      | [] -> ());
      set_account oms 50.0;
      D.Oms.close_subledger oms ~date:(Date.of_string "2026-09-15") ~at:evening;
      Alcotest.(check bool)
        "a whole close is said" true
        (List.exists !lines
           ~f:
             (String.is_substring
                ~substring:"the close of 2026-09-15: 1 name checked, no break")))
    ()

(* R2 under netting. A limit's trip with the book's (reduce_through_trip)
   word on; alpha +100 and beta -50 net to the account's +50. Under the 1.2
   line alone -- an order must shrink the strategy's own position AND the
   account's, by no more than either -- alpha's sell of 100 is more than the
   account's 50, and beta's buy to cover grows the account's long: neither
   could ever be flattened. With the sub-ledger whole and nothing in the
   name that may still fill, the account moves by exactly what the
   strategy's own position does, so each flatten passes; a flip of either is
   refused whole, and so is any increase. *)
let trip_spec =
  {
    Desk_spec.default with
    Desk_spec.trading = Desk_spec.Enabled;
    reduce_through_trip = true;
  }

let tripped () =
  let halt = D.Halt.create D.Halt.Source.none in
  D.Halt.restore_trip halt ~limit:"dd-cap" ~at:(at "2026-09-14T21:00:00Z");
  halt

let test_both_flattens_of_a_netted_pair_pass_a_limit's_trip () =
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      passes "alpha's flatten, sell 100"
        (check_for oms alpha [ leg aapl D.Order.Side.Sell 100 ]);
      passes "beta's flatten, buy 50"
        (check_for oms beta [ leg aapl D.Order.Side.Buy 50 ]);
      passes "alpha's part, sell 30"
        (check_for oms alpha [ leg aapl D.Order.Side.Sell 30 ]);
      List.iter
        [
          ("alpha's flip, sell 150", alpha, leg aapl D.Order.Side.Sell 150);
          ("beta's flip, buy 60", beta, leg aapl D.Order.Side.Buy 60);
          ("alpha's increase, buy 10", alpha, leg aapl D.Order.Side.Buy 10);
          ("beta's increase, sell 10", beta, leg aapl D.Order.Side.Sell 10);
        ]
        ~f:(fun (what, s, l) ->
          contains what ~substring:"kill_switch:" (refusal (check_for oms s [ l ]))))
    ();
  (* One after the other, in either order: the first fills, the journal and
     the account move together, and the second passes. *)
  let fill journal ~n ~source side qty =
    journal_order journal ~n ~source ~side ~state:D.Order.State.Filled
      ~fills:[ Float.of_int qty ]
      qty
  in
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      passes "alpha first" (check_for oms alpha [ leg aapl D.Order.Side.Sell 100 ]);
      fill journal ~n:3 ~source:"signal:alpha:2" D.Order.Side.Sell 100;
      set_account oms (-50.0);
      passes "then beta" (check_for oms beta [ leg aapl D.Order.Side.Buy 50 ]))
    ();
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      passes "beta first" (check_for oms beta [ leg aapl D.Order.Side.Buy 50 ]);
      fill journal ~n:3 ~source:"signal:beta:2" D.Order.Side.Buy 50;
      set_account oms 100.0;
      passes "then alpha" (check_for oms alpha [ leg aapl D.Order.Side.Sell 100 ]))
    ();
  (* And at the wire: the order being sent is itself open by then, and is
     not what makes the name unsettled. *)
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      let request =
        {
          D.Order.Request.client_order_id = cid 7;
          symbol = aapl;
          side = D.Order.Side.Buy;
          qty = 50;
          kind = D.Order.Kind.Market;
          tif = D.Order.Tif.Day;
        }
      in
      D.Oms.admit oms
        [
          {
            D.Journal.Insert.order = D.Order.create request;
            source = "signal:beta:2";
            decision_price = Price.of_float 149.0;
            arrival = None;
            arrival_at = None;
            verdict = `Null;
          };
        ];
      match D.Oms.pre_wire oms ~own:(-50.0) request with
      | Ok _ -> ()
      | Error f -> Alcotest.failf "beta's cover at the wire: %s" (D.Oms.reason f))
    ()

(* What the netting cannot make safe is refused. A break in the name (the
   account +40 against the sub-ledger's 50): the account's move is no longer
   known to be the strategy's, so only the 1.2 line passes -- alpha's sell of
   40 shrinks both and is no larger than either, its sell of 100 would take
   the account through zero to a short nobody holds, and beta's cover grows
   the account. An order of the desk's that may still fill in the name (a
   hand's buy of 10, resting): the sum is not known, and beta's cover is
   refused until it is. *)
let test_a_netted_flatten_is_refused_when_the_sum_is_not_known () =
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 40.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      passes "alpha's sell of 40" (check_for oms alpha [ leg aapl D.Order.Side.Sell 40 ]);
      contains "alpha's sell of 100 through the account's zero" ~substring:"kill_switch:"
        (refusal (check_for oms alpha [ leg aapl D.Order.Side.Sell 100 ]));
      contains "beta's cover" ~substring:"kill_switch:"
        (refusal (check_for oms beta [ leg aapl D.Order.Side.Buy 50 ])))
    ();
  with_oms ~halt:(tripped ()) ~spec:trip_spec ~held:(pair_held 50.0)
    ~f:(fun oms journal ->
      pair_fills journal;
      let resting =
        D.Order.create
          {
            D.Order.Request.client_order_id = cid 9;
            symbol = aapl;
            side = D.Order.Side.Buy;
            qty = 10;
            kind = D.Order.Kind.Market;
            tif = D.Order.Tif.Day;
          }
      in
      D.Journal.insert_order journal resting ~source:"manual"
        ~decision_price:(Price.of_float 150.0) ~arrival:None ~verdict:`Null
        ~at:(at "2026-09-14T23:00:00Z");
      ignore (D.Oms.record oms resting (D.Order.Event.Acknowledged "venue-9") : D.Order.t);
      contains "beta's cover beside a resting order" ~substring:"kill_switch:"
        (refusal (check_for oms beta [ leg aapl D.Order.Side.Buy 50 ])))
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
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0));
      journal_order journal ~n:5 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Submitted 10;
      contains "its own order resting" ~substring:"1 of its orders may still fill"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0));
      contains "and check_rebalance says the same" ~substring:"may still fill"
        (refusal
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 1 ]));
      Alcotest.(check int) "nothing journaled by the refusal" 2 (journaled journal))
    ();
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:6 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Failed 10;
      contains "a failed one the venue may still work" ~substring:"may still fill"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0));
      let failed =
        (Option.value_exn (D.Journal.load_order journal (cid 6)))
          .D.Journal.Order_row.order
      in
      D.Journal.update_order journal failed ~event:D.Order.Event.Venue_expired
        ~anomaly:None ~at:(at "2026-09-15T13:31:00Z");
      Alcotest.(check bool)
        "once the venue reports it expired: sized" true
        (Result.is_ok
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0)))
    ()

(* ------------------------------------------------------------------------ *)
(* Task 15's review: the calendar, the buys, the account, the cap            *)
(* ------------------------------------------------------------------------ *)

let new_york = Timezone.find "America/New_York"

(* C2, pure. The signal, the close and the newest recorded session must be
   one date, and no weekday may have closed since it with no session
   recorded: D_ref is New York's date now when its time is 16:00 or later,
   else the day before, and a weekday in (latest, D_ref] refuses.
   - The review's case: the newest session is Friday 28 August, a signal of
     that day is read on Monday 14 September at 19:15 EDT. Eleven weekdays
     have closed since (31 Aug-4 Sep, 7-11 Sep, and the 14th; Labor Day is a
     weekday with no session, and can only make this refuse): refused.
   - Friday 18 September's signal at 19:15 EDT that Friday: passes. Read on
     Saturday at 10:00 EDT (D_ref Friday), or Monday at 06:00 or at 12:00
     EDT -- 16:00Z, past 16:00 only in UTC (D_ref Sunday): passes.
   - Wednesday 16 September's session and signal, read on Thursday at 19:15
     EDT with Thursday's close not recorded: one weekday: refused.
   - Friday 30 October's, read on Monday 2 November at 08:30 EST, the week
     the clocks go back (D_ref Sunday): passes; at 16:00 EST that Monday
     (21:00Z; D_ref Monday) with Monday unrecorded: refused.
   - A signal of another day than the newest session, a close of another
     day, no session, and no zone: each refused, saying which. *)
let test_the_calendar_ties_the_signal_and_the_close_to_the_newest_session () =
  let d = Date.of_string in
  let refusal ?(zone = new_york) ?(closes = []) ~now ~as_of latest =
    D.Rebalance.calendar_refusal ~zone ~now:(at now) ~as_of:(d as_of) ~standing:false
      ~latest:(Option.map latest ~f:d) ~closes:(List.map closes ~f:d)
  in
  let passes what r = Alcotest.(check (option string)) what None r in
  let refused what ~substring r =
    match r with
    | None -> Alcotest.failf "%s: not refused" what
    | Some why ->
        Alcotest.(check bool)
          (sprintf "%s: %S says %S" what why substring)
          true
          (String.is_substring why ~substring)
  in
  refused "an outage: 28 Aug's signal on 14 Sep"
    ~substring:
      "11 weekdays after the newest recorded session (2026-08-28) have closed by \
       2026-09-14"
    (refusal ~now:"2026-09-14T23:15:00Z" ~as_of:"2026-08-28" (Some "2026-08-28"));
  passes "Friday's signal, Friday 19:15 EDT"
    (refusal ~now:"2026-09-18T23:15:00Z" ~as_of:"2026-09-18" (Some "2026-09-18"));
  passes "Friday's signal, Saturday 10:00 EDT"
    (refusal ~now:"2026-09-19T14:00:00Z" ~as_of:"2026-09-18" (Some "2026-09-18"));
  passes "Friday's signal, Monday 06:00 EDT"
    (refusal ~now:"2026-09-21T10:00:00Z" ~as_of:"2026-09-18" (Some "2026-09-18"));
  (* 16:00Z is noon in New York: Monday's close has not come, whatever UTC's
     clock says, so D_ref is Sunday. *)
  passes "Friday's signal, Monday 12:00 EDT (16:00Z)"
    (refusal ~now:"2026-09-21T16:00:00Z" ~as_of:"2026-09-18" (Some "2026-09-18"));
  refused "Wednesday's, on Thursday evening with Thursday unrecorded"
    ~substring:
      "1 weekday after the newest recorded session (2026-09-16) has closed by 2026-09-17"
    (refusal ~now:"2026-09-17T23:15:00Z" ~as_of:"2026-09-16" (Some "2026-09-16"));
  passes "Friday 30 Oct's, Monday 2 Nov 08:30 EST"
    (refusal ~now:"2026-11-02T13:30:00Z" ~as_of:"2026-10-30" (Some "2026-10-30"));
  refused "Friday 30 Oct's, Monday 2 Nov 16:00 EST, Monday unrecorded"
    ~substring:"has closed by 2026-11-02"
    (refusal ~now:"2026-11-02T21:00:00Z" ~as_of:"2026-10-30" (Some "2026-10-30"));
  refused "a signal of the day before the newest session"
    ~substring:
      "the signal is as of 2026-09-17, but the newest recorded session is 2026-09-18"
    (refusal ~now:"2026-09-18T23:15:00Z" ~as_of:"2026-09-17" (Some "2026-09-18"));
  refused "a close of another day" ~substring:"is 2026-09-17's, not the newest session's"
    (refusal ~closes:[ "2026-09-17" ] ~now:"2026-09-18T23:15:00Z" ~as_of:"2026-09-18"
       (Some "2026-09-18"));
  refused "a newest session after D_ref: Monday's, read at 10:00 EDT that Monday"
    ~substring:"the newest recorded session (2026-09-21) is after 2026-09-20"
    (refusal ~now:"2026-09-21T14:00:00Z" ~as_of:"2026-09-21" (Some "2026-09-21"));
  refused "no session" ~substring:"no session is recorded"
    (refusal ~now:"2026-09-18T23:15:00Z" ~as_of:"2026-09-18" None);
  refused "no zone" ~substring:"time zone could not be loaded"
    (refusal ~zone:None ~now:"2026-09-18T23:15:00Z" ~as_of:"2026-09-18"
       (Some "2026-09-18"))

(* C2 for a STANDING document: one the desk has already sent orders for, on
   its own close. A monthly strategy's document holds until its next one, so
   re-sizing it -- an overlay move, or completing orders the auction let
   expire -- is sized at the newest close, not the signal's.
   - The case found live: the farm's documents are of Tuesday 20 October,
     their market-on-open orders expired at the 21st's auction, the newest
     recorded session is Wednesday the 28th, and it is 10:00 EDT on the 29th.
     Standing: passes. Not standing -- a first sizing -- the same reading is
     refused, as before.
   - The rest of C2 still holds a standing document to the newest session: a
     close of another day, a weekday closed unrecorded since it (the 27th,
     read on the 28th at 10:00 EDT with the newest session the 26th), and a
     signal dated after the newest session, each refused. *)
let test_a_standing_document_is_resized_at_the_newest_close () =
  let d = Date.of_string in
  let refusal ?(closes = []) ~standing ~now ~as_of latest =
    D.Rebalance.calendar_refusal ~zone:new_york ~now:(at now) ~as_of:(d as_of) ~standing
      ~latest:(Some (d latest))
      ~closes:(List.map closes ~f:d)
  in
  let refused what ~substring r =
    match r with
    | None -> Alcotest.failf "%s: not refused" what
    | Some why -> contains what ~substring why
  in
  Alcotest.(check (option string))
    "standing: 20 Oct's document, 28 Oct the newest session, 29 Oct 10:00 EDT" None
    (refusal ~standing:true ~closes:[ "2026-10-28" ] ~now:"2026-10-29T14:00:00Z"
       ~as_of:"2026-10-20" "2026-10-28");
  refused "a first sizing of the same document"
    ~substring:
      "the signal is as of 2026-10-20, but the newest recorded session is 2026-10-28"
    (refusal ~standing:false ~now:"2026-10-29T14:00:00Z" ~as_of:"2026-10-20" "2026-10-28");
  refused "standing, a close of another day"
    ~substring:"is 2026-10-27's, not the newest session's"
    (refusal ~standing:true ~closes:[ "2026-10-27" ] ~now:"2026-10-29T14:00:00Z"
       ~as_of:"2026-10-20" "2026-10-28");
  refused "standing, a weekday closed unrecorded since the newest session"
    ~substring:
      "1 weekday after the newest recorded session (2026-10-26) has closed by 2026-10-27"
    (refusal ~standing:true ~now:"2026-10-28T14:00:00Z" ~as_of:"2026-10-20" "2026-10-26");
  refused "standing, a signal dated after the newest session"
    ~substring:
      "the signal is as of 2026-10-29, after the newest recorded session (2026-10-28)"
    (refusal ~standing:true ~now:"2026-10-29T23:15:00Z" ~as_of:"2026-10-29" "2026-10-28")

(* C2 where the rebalance is checked, for a standing document: an older
   document (as of 10 September, the newest session the 14th) is refused
   while the journal holds no order of its source -- its first sizing, late
   -- and passes the calendar once an order of that source is there, which
   only a check of this kind on the document's own close let through. *)
let test_a_document_through_the_gate_once_is_resized_at_a_later_close () =
  let older = { source with D.Rebalance.Source.as_of = Date.of_string "2026-09-10" } in
  let check oms =
    D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source:older
      ~legs:[ leg aapl D.Order.Side.Buy 10 ]
  in
  with_oms
    ~f:(fun oms journal ->
      contains "a first sizing, late"
        ~substring:
          "the signal is as of 2026-09-10, but the newest recorded session is 2026-09-14"
        (refusal (check oms));
      journal_order journal ~n:1
        ~source:(D.Rebalance.Source.to_string older)
        ~state:D.Order.State.Expired 10;
      match check oms with
      | Ok _ -> ()
      | Error why -> Alcotest.failf "standing, refused: %s" why)
    ()

(* C2, where the rebalance is checked: the review's case end to end. The
   newest recorded session is 28 August, the signal is of that day, and it is
   14 September at 19:15 EDT. Refused, naming the eleven weekdays, and
   nothing journaled. *)
let test_a_signal_from_before_an_outage_is_not_sized_at_a_weeks_old_close () =
  with_oms ~session_date:"2026-08-28"
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms)
             ~source:
               { source with D.Rebalance.Source.as_of = Date.of_string "2026-08-28" }
             ~legs:[ leg aapl D.Order.Side.Buy 10 ])
      in
      contains "the weekdays"
        ~substring:"11 weekdays after the newest recorded session (2026-08-28)" why;
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* C1, the gate: a sell can go unfilled, or be refused, and
   leave its buy alone, so a rebalance is gated on the buy alone as well as
   whole -- one of the subsets every rebalance is gated on. AAPL -160 and
   MSFT +80, each under the 25,000 order cap at its close (23,840 and
   23,920):
     whole:          AAPL 240 x 150 + MSFT 180 x 300 = 36,000 + 54,000 =  90,000  passes
     the sell alone: AAPL 240 x 150 + MSFT 100 x 300 = 36,000 + 30,000 =  66,000  passes
     the buy alone:  AAPL 400 x 150 + MSFT 180 x 300 = 60,000 + 54,000 = 114,000  over tech-cap
   Refused, naming the buy alone and tech-cap. The legs were given buy first
   and are checked sells first: order 1 of 2 is the sell. With MSFT +30
   instead, the buy alone comes to 60,000 + 39,000 = 99,000, and it passes. *)
let test_a_rebalance_is_gated_as_if_only_its_buys_fill () =
  with_oms
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg msft D.Order.Side.Buy 80; leg aapl D.Order.Side.Sell 160 ])
      in
      contains "the buy alone"
        ~substring:
          "the gate refuses the rebalance if only order 2 of 2 fills, as when the other \
           is refused, never sent or left unfilled"
        why;
      contains "tech-cap" ~substring:"tech-cap would be breached" why;
      contains "sells first"
        ~substring:"(order 2 of 2 (MSFT buy 80 day limit at 300.46) at 300.46): tech-cap"
        why;
      Alcotest.(check bool)
        "MSFT +30 beside the sell: passes" true
        (Result.is_ok
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Sell 160; leg msft D.Order.Side.Buy 30 ]));
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* Which orders grow a position: from flat, away from zero, or across it;
   and which shrink one: toward zero, to zero at most.
     long 100:  buy 10 -> 110 grows;   sell 40 -> 60 shrinks;
                sell 100 -> 0 shrinks; sell 160 -> -60 crosses, grows
                (a short opened, though |-60| < |100|)
     short -330: buy 150 -> -180 shrinks;  short -100: sell 80 -> -180 grows
     flat:      buy 1 grows, sell 1 grows *)
let test_which_orders_grow_a_position () =
  let grows position side qty = D.Rebalance.grows ~position ~side ~qty in
  let buy = D.Order.Side.Buy and sell = D.Order.Side.Sell in
  Alcotest.(check (list bool))
    "grows?"
    [ true; false; false; true; false; true; true; true ]
    [
      grows 100.0 buy 10;
      grows 100.0 sell 40;
      grows 100.0 sell 100;
      grows 100.0 sell 160;
      grows (-330.0) buy 150;
      grows (-100.0) sell 80;
      grows 0.0 buy 1;
      grows 0.0 sell 1;
    ]

(* The review's short case. The book is short AAPL 330 and MSFT 100; the
   rebalance covers AAPL 150 and shorts MSFT 80 more. For a short, the buy
   shrinks and the sell grows, so the cover goes first -- and the gate asks
   what the book is if only the sell fills:
     whole:           AAPL 180 x 150 + MSFT 180 x 300 = 27,000 + 54,000 =  81,000
     the cover alone: AAPL 180 x 150 + MSFT 100 x 300 = 27,000 + 30,000 =  57,000
     the sell alone:  AAPL 330 x 150 + MSFT 180 x 300 = 49,500 + 54,000 = 103,500
   which is over tech-cap's 100,000: refused, naming the sell alone.
   Shorting MSFT 20 instead, the sell alone is 49,500 + 36,000 = 85,500, and it
   passes, the cover first whatever order the legs came in. *)
let test_a_short_strategy's_rebalance_covers_first_and_is_gated_on_its_sells () =
  with_oms
    ~held:[ (aapl, -330.0); (msft, -100.0); (xom, -200.0) ]
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg msft D.Order.Side.Sell 80; leg aapl D.Order.Side.Buy 150 ])
      in
      contains "the sell alone"
        ~substring:"the gate refuses the rebalance if only order 2 of 2 fills" why;
      contains "tech-cap" ~substring:"tech-cap would be breached" why;
      contains "the cover first, the short last"
        ~substring:"(order 2 of 2 (MSFT sell 80 day limit at 299.55) at 299.55): tech-cap"
        why;
      (match
         D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
           ~legs:[ leg msft D.Order.Side.Sell 20; leg aapl D.Order.Side.Buy 150 ]
       with
      | Error why -> Alcotest.failf "MSFT -20 refused: %s" why
      | Ok checked ->
          Alcotest.(check (list (pair string string)))
            "MSFT -20: passes, the cover first"
            [ ("AAPL", "buy"); ("MSFT", "sell") ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 ( Symbol.to_string r.D.Order.Request.symbol,
                   D.Order.Side.to_string r.D.Order.Request.side ))));
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* An order that takes a position across zero grows it. Long AAPL 100, sell
   160 flips it to short 60 -- smaller, but a short opened -- so it goes after
   MSFT -50, which only shrinks a long: MSFT first, then AAPL, where by name
   alone AAPL would lead. And it is gated as growing. A sector's notional is
   the size of its NET sum, so the flip that grows TECH is a short bought
   into a long beside a long: short AAPL 50 and long MSFT 280 (|-7,500 +
   84,000| = 76,500), AAPL +160 (to +110) with MSFT -80:
     whole:          AAPL 110 x 150 + MSFT 200 x 300 = 16,500 + 60,000 =  76,500
     the flip alone: AAPL 110 x 150 + MSFT 280 x 300 = 16,500 + 84,000 = 100,500
   over tech-cap: refused. A name twice is refused before any of it. *)
let test_an_order_that_crosses_zero_is_ordered_and_gated_as_growing () =
  with_oms
    ~held:[ (aapl, 100.0); (msft, 280.0); (xom, -200.0) ]
    ~f:(fun oms _ ->
      match
        D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
          ~legs:[ leg aapl D.Order.Side.Sell 160; leg msft D.Order.Side.Sell 50 ]
      with
      | Error why -> Alcotest.failf "refused: %s" why
      | Ok checked ->
          Alcotest.(check (list string))
            "the shrinking sell first, the flip last" [ "MSFT"; "AAPL" ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 Symbol.to_string r.D.Order.Request.symbol)))
    ();
  with_oms
    ~held:[ (aapl, -50.0); (msft, 280.0); (xom, -200.0) ]
    ~f:(fun oms _ ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg aapl D.Order.Side.Buy 160; leg msft D.Order.Side.Sell 80 ])
      in
      contains "the flip, gated as growing"
        ~substring:
          "if only order 2 of 2 fills, as when the other is refused, never sent or left \
           unfilled (order 2 of 2 (AAPL buy 160 day limit at 150.23) at 150.23): \
           tech-cap"
        why;
      contains "a name twice" ~substring:"the rebalance names AAPL twice"
        (refusal
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 1; leg aapl D.Order.Side.Buy 2 ])))
    ()

(* Each order of a rebalance counts the rebalance's orders before it against
   the open-order cap: with a cap of 1 and nothing open, one order passes and
   the second of two is refused, naming the rule. *)
let test_each_order_counts_the_ones_before_it_against_the_open_order_cap () =
  with_oms
    ~spec:
      {
        Desk_spec.default with
        Desk_spec.trading = Desk_spec.Enabled;
        max_open_orders = 1;
      }
    ~f:(fun oms _ ->
      Alcotest.(check bool)
        "one order: passes" true
        (Result.is_ok
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 1 ]));
      contains "the second of two"
        ~substring:
          "order 2 of 2 (MSFT buy 1 day limit at 300.46): open_orders: 1 orders are \
           already open; the cap is 1"
        (refusal
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 1; leg msft D.Order.Side.Buy 1 ])))
    ()

(* E is the book's equity only while the book is the account's: with no
   recent read applied, no targets, and the trading rule's own words. *)
let test_no_targets_while_the_book_is_not_the_account's () =
  with_oms ~book_is_current:false
    ~f:(fun oms _ ->
      contains "equity unknown"
        ~substring:
          "the book's equity is unknown: the desk has not applied a recent read of the \
           account"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0)))
    ()

(* Formerly I2 (Task 15): the plan refused a strategy whose own position the
   account did not cover in its direction. Task 1.6 moved that question to
   the one sub-ledger check, which sums every strategy's position per name
   (desk/subledger.ml; its cases are test_subledger.ml's and the order
   manager's above), because a name two strategies hold on opposite sides is
   an account that covers neither alone: own +100 beside an account of +60
   is a second strategy short 40, and own -50 beside +50 is a long 100
   beside it. So the plan sizes from the strategy's own position whatever
   the account nets it to -- a flat signal sells exactly the strategy's 100,
   or buys back exactly its 50 -- and the account orders the legs and
   nothing else. *)
let test_the_plan_sizes_a_strategy_whatever_the_account_nets_it_to () =
  let current q = Ok (Symbol.Map.singleton spy q) in
  let account q = Symbol.Map.singleton spy q in
  List.iter [ 100.0; 300.0; 60.0; -40.0 ] ~f:(fun held ->
      Alcotest.(check (list (triple string int int)))
        (sprintf "own +100, the account %g: sell exactly 100" held)
        [ ("sell", 100, 0) ]
        (legs (plan ~current:(current 100.0) ~account:(account held) ~price:500.0 [])));
  List.iter [ -20.0; 50.0 ] ~f:(fun held ->
      Alcotest.(check (list (triple string int int)))
        (sprintf "own -50, the account %g: buy back exactly 50" held)
        [ ("buy", 50, 0) ]
        (legs (plan ~current:(current (-50.0)) ~account:(account held) ~price:500.0 [])))

(* The current mark, never yesterday's close (Task 1.7). E is 1,000,000 +
   400 x 150 + 100 x 300 - 200 x 100 = 1,070,000 at the live book; at a
   weight of 1 and a fraction of 0.1 that is 107,000 of AAPL. At the live
   mark of 150 it is 107,000 / 150 = 713.33 -> 713 shares; at Monday's
   recorded close of 149 it would have been 718.12 -> 718. The order is
   checked and gated at its limit off the quote, 150.23 -- the ask, 150.075,
   moved 10 bps up and rounded up to the cent -- never the close. With the
   feed calling the name stale, there is no current mark: no targets, and
   saying so, rather than a size off an old price. *)
let test_a_rebalance_is_sized_at_the_current_mark_not_yesterday's_close () =
  with_oms
    ~f:(fun oms _ ->
      (match
         D.Oms.plan_rebalance oms ~strategy:tech_strategy
           ~targets:[ (aapl, 1.0) ]
           ~scale:1.0
       with
      | Error why -> Alcotest.failf "no targets: %s" why
      | Ok (ls, _) ->
          Alcotest.(check (list (triple string int int)))
            "713 at 150, not 718 at 149"
            [ ("buy", 713, 713) ]
            (List.map ls ~f:(fun (l : Leg.t) ->
                 (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target))));
      (match
         D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
           ~legs:[ leg aapl D.Order.Side.Buy 10 ]
       with
      | Error why -> Alcotest.failf "refused: %s" why
      | Ok checked ->
          Alcotest.(check (list (float 1e-9)))
            "the decision price is the limit off the quote" [ 150.23 ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (_, p) ->
                 Price.to_float p)));
      Graph.set_now oms.D.Oms.graph (Time.add (Time.now ()) (Time.Span.of_sec 3600.0));
      Graph.stabilize oms.D.Oms.graph;
      contains "a stale mark sizes nothing"
        ~substring:"AAPL's mark is stale, and a rebalance is sized only at a current one"
        (refusal
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy
              ~targets:[ (aapl, 1.0) ]
              ~scale:1.0));
      (* ... and the mark rule refuses a desk order on it for timing alone
         (fix round 1, I1): the name will print again, and the order go. *)
      let timing = ref false in
      contains "the mark rule" ~substring:"mark: AAPL's mark is stale"
        (refusal
           (D.Oms.check_rebalance ~timing oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 10 ]));
      Alcotest.(check bool) "for timing alone" true !timing)
    ()

(* The final review's probe: a prefix of the send order is gated. The
   account holds AAPL -500 by hand and MSFT +350, the strategy's own (one
   fill, journaled); a long-only two-symbol strategy buys AAPL 500 and sells
   MSFT 350. Both orders shrink a position -- AAPL -500 to 0, MSFT 350 to 0
   -- and by name the AAPL buy goes first. The order cap is raised to
   200,000 for this case, so no rule stands in front of the gate (74,500 and
   104,650 at the closes). TECH is the size of a net sum:
     the unit:           AAPL 0 x 150 + MSFT   0 x 300 =       0  passes
     the AAPL buy alone: AAPL 0 x 150 + MSFT 350 x 300 = 105,000  over tech-cap
   and the AAPL buy alone is what reaches the book when the venue does not
   acknowledge the MSFT sell, or the switch is set before it. Refused, naming
   that subset, its order and tech-cap, and nothing journaled. With MSFT 300
   held instead, the AAPL buy alone is 90,000 and the MSFT sell alone 75,000,
   and the rebalance passes, the buy first: the subsets refuse what breaches
   and nothing else. (A single order's only subset is the rebalance itself,
   gated once: the one-order cases above are judged as they were.) *)
let test_a_prefix_of_the_send_order_is_gated () =
  let spec =
    {
      Desk_spec.default with
      Desk_spec.trading = Desk_spec.Enabled;
      max_order_notional = 200_000.0;
    }
  in
  let legs msft_qty =
    [ leg msft D.Order.Side.Sell msft_qty; leg aapl D.Order.Side.Buy 500 ]
  in
  with_oms ~spec
    ~held:[ (aapl, -500.0); (msft, 350.0); (xom, -200.0) ]
    ~f:(fun oms journal ->
      journal_order journal ~n:7 ~source:"signal:exp_a01_tech:1" ~symbol:msft
        ~state:D.Order.State.Filled ~fills:[ 350.0 ] 350;
      let why =
        refusal (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs:(legs 350))
      in
      contains "the prefix, as a subset"
        ~substring:"the gate refuses the rebalance if only order 1 of 2 fills" why;
      contains "its one order, and tech-cap"
        ~substring:"(order 1 of 2 (AAPL buy 500 day limit at 150.23) at 150.23): tech-cap"
        why;
      Alcotest.(check int)
        "nothing journaled but the strategy's own fill" 1 (journaled journal))
    ();
  with_oms ~spec
    ~held:[ (aapl, -500.0); (msft, 300.0); (xom, -200.0) ]
    ~f:(fun oms _ ->
      match D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs:(legs 300) with
      | Error why -> Alcotest.failf "MSFT 300: refused: %s" why
      | Ok checked ->
          Alcotest.(check (list string))
            "MSFT 300: passes, the AAPL buy first" [ "AAPL"; "MSFT" ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 Symbol.to_string r.D.Order.Request.symbol)))
    ()

(* A later order alone is gated too: an order the venue took can still go
   unfilled at the opening auction, or be refused at the open, so the fills
   can be any subset of the orders, not only a prefix. The account holds
   AAPL +700 and MSFT -300 (aapl-cap is already over its line, and nothing
   here moves AAPL past where it is); the rebalance sells AAPL 700 and buys
   MSFT 300. Both shrink a position, so by name the AAPL sell goes first.
   The order cap is raised to 200,000 (104,300 and 89,700 at the closes):
     the unit:            AAPL   0 x 150 + MSFT    0 x 300 =       0  passes
     the AAPL sell alone: AAPL   0 x 150 + MSFT -300 x 300 =  90,000  passes (the one prefix)
     the MSFT buy alone:  AAPL 700 x 150 + MSFT    0 x 300 = 105,000  over tech-cap
   The MSFT buy alone is the book when the AAPL sell goes unfilled. Refused,
   naming that subset and tech-cap. *)
let test_a_later_order_alone_is_gated () =
  with_oms
    ~spec:
      {
        Desk_spec.default with
        Desk_spec.trading = Desk_spec.Enabled;
        max_order_notional = 200_000.0;
      }
    ~held:[ (aapl, 700.0); (msft, -300.0); (xom, -200.0) ]
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg aapl D.Order.Side.Sell 700; leg msft D.Order.Side.Buy 300 ])
      in
      contains "the MSFT buy alone"
        ~substring:"the gate refuses the rebalance if only order 2 of 2 fills" why;
      contains "and tech-cap"
        ~substring:
          "(order 2 of 2 (MSFT buy 300 day limit at 300.46) at 300.46): tech-cap would \
           be breached"
        why;
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* At most six orders: every combination of a rebalance's orders is gated,
   2^n - 1 of them, and six make 63. Seven are refused before anything else
   is asked -- the names here are not even the book's -- saying why, and
   nothing is journaled. The combinations themselves: for three orders, the
   unit, then each alone, then each pair, in send order; for six, 63
   distinct non-empty sets. *)
let test_a_rebalance_of_more_than_six_orders_is_refused () =
  Alcotest.(check (list (list int)))
    "three orders: seven sets, the unit first"
    [ [ 0; 1; 2 ]; [ 0 ]; [ 1 ]; [ 2 ]; [ 0; 1 ]; [ 0; 2 ]; [ 1; 2 ] ]
    (D.Rebalance.fill_sets 3);
  let six = D.Rebalance.fill_sets 6 in
  Alcotest.(check (pair int int))
    "six orders: 63 sets, all distinct and non-empty" (63, 63)
    ( List.length
        (List.dedup_and_sort six ~compare:(List.compare Int.compare)
        |> List.filter ~f:(Fn.non List.is_empty)),
      List.length six );
  with_oms
    ~f:(fun oms journal ->
      let seven =
        List.map [ "AAPL"; "AMZN"; "GOOG"; "META"; "MSFT"; "NVDA"; "XOM" ] ~f:(fun s ->
            leg (Symbol.of_string s) D.Order.Side.Buy 1)
      in
      contains "seven orders"
        ~substring:
          "the rebalance has 7 orders, and the desk takes at most 6: it gates every \
           combination of a rebalance's orders that could be its fills, 127 for 7 orders \
           and 63 for 6"
        (refusal (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs:seven));
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* [plan_rebalance] runs outside the order manager's queue, and
   [check_rebalance] inside it, so what the plan read is asked again there.
   The strategy owns AAPL 100 (one fill, journaled) and the account holds
   100: a flat signal plans a sale of 100. Then, before the rebalance's turn,
   the account's AAPL reads 60 -- a hand sale outside the desk -- and two
   sync checks find the break standing. Formerly I2 refused the sale here;
   since Task 1.6 the check asks the sub-ledger instead, which refuses an
   order that would grow the strategy's position, naming both figures, and
   lets the sale go: a desk fill moves the account and the sub-ledger
   together, so selling the strategy's own shares neither widens the break
   nor narrows it, and a strategy in a broken name can still be cut. And
   with the book no longer the account's, the equity is unknown: refused in
   the sizing's words, ahead of the rules. *)
let test_the_rebalance_asks_again_what_its_plan_read () =
  with_oms
    ~held:[ (aapl, 100.0); (msft, 100.0); (xom, -200.0) ]
    ~f:(fun oms journal ->
      journal_order journal ~n:8 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Filled ~fills:[ 100.0 ] 100;
      let legs =
        match D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[] ~scale:1.0 with
        | Ok (legs, _) -> legs
        | Error why -> Alcotest.failf "the plan: %s" why
      in
      Alcotest.(check (list (pair string int)))
        "the plan: sell 100"
        [ ("sell", 100) ]
        (List.map legs ~f:(fun (l : Leg.t) ->
             (D.Order.Side.to_string l.Leg.side, l.Leg.qty)));
      Graph.set_qty oms.D.Oms.graph aapl (Qty.of_float 60.0);
      Graph.stabilize oms.D.Oms.graph;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      contains "the planned sale of 100 against the account's 60: refused"
        ~substring:
          "order 1 of 1 (AAPL sell 100): the sub-ledger breaks in AAPL: the strategies' \
           own positions and the desk's hand tickets sum to 100, and the account holds \
           60; a reduction in AAPL goes only if it also shrinks the account's position \
           (60"
        (refusal (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source ~legs));
      Alcotest.(check bool)
        "a sale of 60, inside the account's 60, goes" true
        (Result.is_ok
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Sell 60 ]));
      let why =
        refusal
          (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
             ~legs:[ leg aapl D.Order.Side.Buy 10 ])
      in
      contains "the sub-ledger, asked again"
        ~substring:
          "order 1 of 1 (AAPL buy 10): the sub-ledger breaks in AAPL: the strategies' \
           own positions and the desk's hand tickets sum to 100, and the account holds \
           60"
        why)
    ();
  (* The short mirror: the strategy short 50 of its own (XOM, one fill) and
     the account short 50; the account's XOM then reads -20. A cover of 50
     would take the account through zero to +30, a long nobody holds, and is
     refused; a cover of 20 goes. *)
  with_oms
    ~held:[ (aapl, 0.0); (msft, 100.0); (xom, -50.0) ]
    ~f:(fun oms journal ->
      journal_order journal ~n:8 ~source:"signal:exp_a01_tech:1" ~symbol:xom
        ~side:D.Order.Side.Sell ~state:D.Order.State.Filled ~fills:[ 50.0 ] 50;
      Graph.set_qty oms.D.Oms.graph xom (Qty.of_float (-20.0));
      Graph.stabilize oms.D.Oms.graph;
      D.Oms.check_subledger oms ~at:evening;
      D.Oms.check_subledger oms ~at:evening;
      contains "a cover of 50 against the account's -20: refused"
        ~substring:"a buy of 50 does not"
        (refusal
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg xom D.Order.Side.Buy 50 ]));
      Alcotest.(check bool)
        "a cover of 20 goes" true
        (Result.is_ok
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg xom D.Order.Side.Buy 20 ])))
    ();
  with_oms ~book_is_current:false
    ~f:(fun oms _ ->
      contains "the equity, asked again"
        ~substring:
          "the book's equity is unknown: the desk has not applied a recent read of the \
           account"
        (refusal
           (D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
              ~legs:[ leg aapl D.Order.Side.Buy 10 ])))
    ()

(* Invariant 10's other half: one submit site. What the compiler holds:
   the venue's submit takes a permit (Venue.Trade.t's parameter); both
   adapters build their trading half at desk/wire.ml's permit, whose values
   nothing outside that module can make; so an adapter's submit cannot be
   called against its field, under any alias, and an adapter cannot be typed
   at another permit to get round it. What this case holds, across every .ml
   and .mli in desk/ and bin/:
   - the wire module is named, outside wire.ml(i), only for its permit TYPE
     -- in oms.ml and in the two adapters that build a trading half at it --
     and once more, as the call in oms.ml's submit_journaled. An alias of the
     module (module W = ..., open, include) or of its function names it
     again, and fails here;
   - submit_journaled, the function that makes that call, is named in
     oms.ml alone: nothing else can hand an order to it;
   - the simulator's own submit, which takes no permit, is named only where
     sim_venue.ml defines it (in For_testing) and where its trading half
     calls it: the one venue-side way in that skips the permit is the
     tests', and the permit's. *)
let test_the_venue's_submit_is_called_in_one_place () =
  let files dir =
    Sys_unix.readdir dir |> Array.to_list
    (* The sources, not the preprocessed copies the build keeps beside them. *)
    |> List.filter ~f:(fun f ->
        (String.is_suffix f ~suffix:".ml" || String.is_suffix f ~suffix:".mli")
        && not (String.is_substring f ~substring:".pp."))
    |> List.sort ~compare:String.compare
    |> List.map ~f:(fun f -> dir ^ "/" ^ f)
  in
  let sources = files "../desk" @ files "../bin" in
  (* Every occurrence of [word] as a whole identifier in [line], each with
     the text after it. *)
  let occurrences ~word line =
    let n = String.length line and w = String.length word in
    let ident c = Char.is_alphanum c || Char.equal c '_' || Char.equal c '\'' in
    let rec go i acc =
      match String.substr_index line ~pattern:word ~pos:i with
      | None -> List.rev acc
      | Some j ->
          let k = j + w in
          if (j = 0 || not (ident line.[j - 1])) && (k >= n || not (ident line.[k])) then
            go k (String.drop_prefix line k :: acc)
          else go k acc
    in
    go 0 []
  in
  let found ~word =
    List.concat_map sources ~f:(fun file ->
        In_channel.read_lines file
        |> List.concat_map ~f:(fun line ->
            List.map (occurrences ~word line) ~f:(fun rest ->
                (file, rest, String.strip line))))
  in
  let wire =
    List.filter_map (found ~word:"Wire") ~f:(fun (file, rest, line) ->
        if List.mem [ "../desk/wire.ml"; "../desk/wire.mli" ] file ~equal:String.equal
        then None
        else
          Some
            ( file,
              (if String.is_prefix rest ~prefix:".permit" then "Wire.permit"
               else if String.is_prefix rest ~prefix:".submit" then "Wire.submit"
               else "Wire"),
              line ))
  in
  Alcotest.(check (list (triple string string string)))
    "outside wire.ml(i), beside its permit type: one call, in oms.ml's submit_journaled"
    [
      ( "../desk/oms.ml",
        "Wire.submit",
        "let%map submission = Wire.submit trade request in" );
    ]
    (List.filter wire ~f:(fun (_, what, _) -> not (String.equal what "Wire.permit")));
  Alcotest.(check (list string))
    "the permit type is named by the order manager and the two adapters"
    [ "../desk/alpaca_trade.ml"; "../desk/oms.ml"; "../desk/sim_venue.ml" ]
    (List.filter_map wire ~f:(fun (file, what, _) ->
         Option.some_if (String.equal what "Wire.permit") file)
    |> List.dedup_and_sort ~compare:String.compare);
  Alcotest.(check (list string))
    "submit_journaled is named in oms.ml alone" [ "../desk/oms.ml" ]
    (List.map (found ~word:"submit_journaled") ~f:(fun (file, _, _) -> file)
    |> List.dedup_and_sort ~compare:String.compare);
  (* The simulator taking an order with no permit and no journal
     (Sim_venue.For_testing): the tests' way in, and trade's. Named where it
     is defined and where trade's submit calls it, in sim_venue.ml, and
     nowhere else in desk/ or bin/ -- a call from the engine, or an alias
     that re-exports it, names it again and fails here. *)
  Alcotest.(check (list (pair string string)))
    "the simulator's own submit: its definition and trade's call, and nothing else"
    [
      ( "../desk/sim_venue.ml",
        "let submit_now (t : t) (r : Order.Request.t) : Venue.Submission.t =" );
      ("../desk/sim_venue.ml", "let s = For_testing.submit_now t r in");
    ]
    (List.map (found ~word:"submit_now") ~f:(fun (file, _, line) -> (file, line)))

(* ------------------------------------------------------------------------ *)
(* Task 10: the intraday mode -- the limit, the floor, the day order         *)
(* ------------------------------------------------------------------------ *)

let price_check what expected got = Alcotest.(check (float 1e-9)) what expected got

(* A buy's limit is the ask moved 10 bps up, rounded UP to the cent; a
   sell's the bid moved 10 bps down, rounded DOWN -- so a buy never rounds
   below the ask, nor a sell above the bid:
     ask 100.00 x 1.001 = 100.10 exactly (the float's noise absorbed)
     bid 100.00 x 0.999 =  99.90
     ask 123.455 x 1.001 = 123.578455 -> 123.58; bid 123.455 x 0.999 =
       123.331545 -> 123.33
     ask 100.005 at no band -> 100.01, never 100.00, below the ask
     bid  99.995 at no band ->  99.99, never 100.00, above the bid
   The 1.001 is the book's intraday_slip_bps, 10 by default. *)
let test_a_desk_order's_limit_is_off_the_far_side_of_the_quote () =
  let lim side ~bid ~ask band_bps = D.Rebalance.quote_limit ~side ~bid ~ask ~band_bps in
  let buy = D.Order.Side.Buy and sell = D.Order.Side.Sell in
  price_check "ask 100.00, 10 bps up" 100.10 (lim buy ~bid:99.0 ~ask:100.0 10.0);
  price_check "bid 100.00, 10 bps down" 99.90 (lim sell ~bid:100.0 ~ask:101.0 10.0);
  price_check "ask 123.455 -> 123.58" 123.58 (lim buy ~bid:123.0 ~ask:123.455 10.0);
  price_check "bid 123.455 -> 123.33" 123.33 (lim sell ~bid:123.455 ~ask:124.0 10.0);
  price_check "ask 100.005, no band: up to 100.01" 100.01
    (lim buy ~bid:99.0 ~ask:100.005 0.0);
  price_check "bid 99.995, no band: down to 99.99" 99.99
    (lim sell ~bid:99.995 ~ask:101.0 0.0);
  List.iter [ 0.37; 1.0; 9.99; 150.075; 300.15; 412.3456 ] ~f:(fun q ->
      let b = lim buy ~bid:q ~ask:q 10.0 and s = lim sell ~bid:q ~ask:q 10.0 in
      Alcotest.(check bool)
        (sprintf "at %g: the buy %g is not below it, the sell %g not above it" q b s)
        true
        Float.(b >= q && s <= q))

(* A floor of 200: one share at 150.00 is 150 < 200, dropped; two are 300,
   kept. A sell is measured the same, |qty x price| -- here one that leaves 4
   of 5 held, so its target is not 0. *)
let test_drop_small () =
  let at150 qty side =
    {
      (leg spy side qty) with
      Leg.price = 150.0;
      target = (match side with D.Order.Side.Buy -> qty | D.Order.Side.Sell -> 4);
    }
  in
  let names (ls : Leg.t list) =
    List.map ls ~f:(fun l ->
        sprintf "%s %d" (D.Order.Side.to_string l.Leg.side) l.Leg.qty)
  in
  let kept, dropped =
    D.Rebalance.drop_small
      ~legs:
        [ at150 1 D.Order.Side.Buy; at150 2 D.Order.Side.Buy; at150 1 D.Order.Side.Sell ]
      ~min_leg_notional:200.0
  in
  Alcotest.(check (pair (list string) (list string)))
    "1 x 150 = 150 dropped, 2 x 150 = 300 kept"
    ([ "buy 2" ], [ "buy 1"; "sell 1" ])
    (names kept, names dropped);
  (* And Oms.plan_rebalance drops at the book's floor, 200 by default. E is
     1,070,000 and the fraction 0.1: a weight of 0.002 is 214 of AAPL at its
     mark of 150, 1.43 -> 1 share, 150 < 200, dropped; a weight of 0.003 is
     321 / 150 = 2.14 -> 2 shares, 300, kept. *)
  with_oms
    ~f:(fun oms _ ->
      let planned w =
        match
          D.Oms.plan_rebalance oms ~strategy:tech_strategy
            ~targets:[ (aapl, w) ]
            ~scale:1.0
        with
        | Ok (kept, dropped) -> (names kept, names dropped)
        | Error why -> Alcotest.failf "no targets: %s" why
      in
      Alcotest.(check (pair (list string) (list string)))
        "1 share at 150 dropped" ([], [ "buy 1" ]) (planned 0.002);
      Alcotest.(check (pair (list string) (list string)))
        "2 shares at 150 kept" ([ "buy 2" ], []) (planned 0.003))
    ()

(* The floor is the adjust pass's alone, and never on a closing leg (the
   stage review's A-I6, withdrawing Task 10's "both modes"; kept under day
   limits by fix round 1):
   - an overlay-0 flatten of a 1-share position at 150: the plan at scale 0
     sells the 1 to a target of 0, 150 < 200, and drop_small keeps it;
   - a signal's own rebalance ([floor] false) with a leg of 1 share at 150
     (w 0.002 of E 1,070,000 x 0.1 = 214 -> 1 share) sends it;
   - the same leg on the adjust pass grows a position (target 1), is
     dropped, and is handed back as dropped for the sentence to say. *)
let test_the_floor_is_never_on_a_closing_leg () =
  let names (ls : Leg.t list) =
    List.map ls ~f:(fun l ->
        sprintf "%s %d" (D.Order.Side.to_string l.Leg.side) l.Leg.qty)
  in
  (match
     plan ~scale:0.0 ~price:150.0
       ~current:(Ok (Symbol.Map.singleton spy 1.0))
       [ (spy, 1.0) ]
   with
  | Error why -> Alcotest.failf "no targets: %s" why
  | Ok legs ->
      let kept, dropped = D.Rebalance.drop_small ~legs ~min_leg_notional:200.0 in
      Alcotest.(check (pair (list string) (list string)))
        "the flatten of 1 share at 150 is kept" ([ "sell 1" ], [])
        (names kept, names dropped));
  with_oms
    ~f:(fun oms _ ->
      let planned floor =
        match
          D.Oms.plan_rebalance ~floor oms ~strategy:tech_strategy
            ~targets:[ (aapl, 0.002) ]
            ~scale:1.0
        with
        | Ok (kept, dropped) -> (names kept, names dropped)
        | Error why -> Alcotest.failf "no targets: %s" why
      in
      Alcotest.(check (pair (list string) (list string)))
        "a signal's: 1 share at 150 is sent" ([ "buy 1" ], []) (planned false);
      Alcotest.(check (pair (list string) (list string)))
        "the adjust pass's: the growing leg is dropped" ([], [ "buy 1" ]) (planned true))
    ()

(* Tuesday 15 September at 11:00 EDT (15:00Z): the session is open and
   09:35 has come; Monday's close is still the newest whose 16:00 has come,
   so the calendar passes. Every leg is a day limit order off its quote,
   judged as the desk's own day order and gated at its limit: XOM +10 covers
   part of the short of 200 (it shrinks, so it goes first), at the ask
   100.05 x 1.001 = 100.15005 -> 100.16; AAPL +10 at 150.075 x 1.001 =
   150.225075 -> 150.23. No market-on-open order is built. With no quote for
   AAPL, or one six seconds old, the rebalance is refused by the quote rule
   -- for timing alone, since the next pass fetches another -- and with the
   book's bound raised to ten seconds the six-second quote prices it. *)
let test_every_leg_is_a_day_limit_off_its_quote () =
  with_oms ~now:(at "2026-09-15T15:00:00Z")
    ~f:(fun oms _ ->
      (match
         D.Oms.check_rebalance oms ~quotes:(quotes oms) ~source
           ~legs:[ leg aapl D.Order.Side.Buy 10; leg xom D.Order.Side.Buy 10 ]
       with
      | Error why -> Alcotest.failf "refused: %s" why
      | Ok checked ->
          Alcotest.(check (list (pair string (pair string (float 1e-9)))))
            "XOM then AAPL, each a day limit off its quote, gated there"
            [ ("XOM day", ("limit", 100.16)); ("AAPL day", ("limit", 150.23)) ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 ( sprintf "%s %s"
                     (Symbol.to_string r.D.Order.Request.symbol)
                     (D.Order.Tif.to_string r.D.Order.Request.tif),
                   ( D.Order.Kind.to_string r.D.Order.Request.kind,
                     Price.to_float
                       (Option.value_exn
                          (D.Order.Kind.limit_price r.D.Order.Request.kind)) ) )));
          Alcotest.(check (list (float 1e-9)))
            "gated at the limits" [ 100.16; 150.23 ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (_, p) ->
                 Price.to_float p));
          Alcotest.(check bool)
            "no market-on-open order" false
            (List.exists checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 D.Order.Tif.equal r.D.Order.Request.tif D.Order.Tif.Opg)));
      let refused_for_timing what ~substring q =
        let timing = ref false in
        let why =
          refusal
            (D.Oms.check_rebalance ~timing oms ~quotes:q ~source
               ~legs:[ leg aapl D.Order.Side.Buy 10 ])
        in
        contains what ~substring why;
        Alcotest.(check bool) (what ^ ": for timing alone") true !timing
      in
      refused_for_timing "no quote" ~substring:"quote: AAPL: no quote answered for it"
        (fun _ -> None);
      refused_for_timing "a quote six seconds old"
        ~substring:"quote: AAPL: its quote was fetched 6.0 s ago"
        (fetched_earlier oms 6.0))
    ();
  with_oms ~now:(at "2026-09-15T15:00:00Z")
    ~spec:
      {
        Desk_spec.default with
        Desk_spec.trading = Desk_spec.Enabled;
        quote_max_age_s = 10.0;
      }
    ~f:(fun oms _ ->
      passes_check "a quote six seconds old under a bound of ten"
        (D.Oms.check_rebalance oms ~quotes:(fetched_earlier oms 6.0) ~source
           ~legs:[ leg aapl D.Order.Side.Buy 10 ]))
    ()

let suite =
  ( "rebalance",
    [
      Alcotest.test_case "a desk order's limit is off the far side of the quote" `Quick
        test_a_desk_order's_limit_is_off_the_far_side_of_the_quote;
      Alcotest.test_case "drop_small" `Quick test_drop_small;
      Alcotest.test_case "a stopped strategy's flatten passes the exposure limits" `Quick
        test_a_stopped_strategy's_flatten_passes_the_exposure_limits;
      Alcotest.test_case "under a trip a stop flatten is gated as before" `Quick
        test_under_a_trip_a_stop_flatten_is_gated_as_before;
      Alcotest.test_case "a stop flatten passes an unevaluable trip-limit" `Quick
        test_a_stop_flatten_passes_an_unevaluable_trip_limit;
      Alcotest.test_case "the floor is the adjust pass's, never on a closing leg" `Quick
        test_the_floor_is_never_on_a_closing_leg;
      Alcotest.test_case "every leg is a day limit off its quote" `Quick
        test_every_leg_is_a_day_limit_off_its_quote;
      Alcotest.test_case "targets on both sides of a share boundary" `Quick
        test_targets_on_both_sides_of_a_share_boundary;
      Alcotest.test_case "a negative weight rounds toward zero" `Quick
        test_a_negative_weight_rounds_toward_zero;
      Alcotest.test_case "every unknown makes no targets, and says why" `Quick
        test_every_unknown_makes_no_targets_and_says_why;
      Alcotest.test_case
        "a rebalance is refused as a unit when its second order breaches a limit" `Quick
        test_a_rebalance_is_refused_as_a_unit_when_its_second_order_breaches_a_limit;
      Alcotest.test_case "nothing is sent before 09:35 New York" `Quick
        test_nothing_is_sent_before_09_35_new_york;
      Alcotest.test_case
        "a flat signal sells exactly the strategy's own shares, not a hand-bought lot"
        `Quick test_a_flat_signal_sells_exactly_the_strategy's_own_shares;
      Alcotest.test_case
        "a strategy's reduction passes a limit's trip, and a flip is refused whole" `Quick
        test_a_strategy's_reduction_passes_a_limit's_trip_and_a_flip_is_refused_whole;
      Alcotest.test_case "a resting reduction in the name shrinks the room under a trip"
        `Quick test_a_resting_reduction_in_the_name_shrinks_the_room_under_a_trip;
      Alcotest.test_case
        "a failed reduction the venue may still work shrinks the room too" `Quick
        test_a_failed_reduction_the_venue_may_still_work_shrinks_the_room_too;
      Alcotest.test_case "both strategies of a netted pair are sized" `Quick
        test_both_strategies_of_a_netted_pair_are_sized;
      Alcotest.test_case
        "a standing break refuses an increase and holds a reduction to the account" `Quick
        test_a_standing_break_refuses_either_strategy's_increase_and_holds_a_reduction_to_the_account;
      Alcotest.test_case "a standing break binds while an order rests in the name" `Quick
        test_a_standing_break_binds_while_an_order_rests_in_the_name;
      Alcotest.test_case "a break is alerted once when it stands" `Quick
        test_a_break_is_alerted_once_when_it_stands;
      Alcotest.test_case "the close says every standing break" `Quick
        test_the_close_says_every_standing_break;
      Alcotest.test_case "both flattens of a netted pair pass a limit's trip" `Quick
        test_both_flattens_of_a_netted_pair_pass_a_limit's_trip;
      Alcotest.test_case "a netted flatten is refused when the sum is not known" `Quick
        test_a_netted_flatten_is_refused_when_the_sum_is_not_known;
      Alcotest.test_case "a strategy with an order that may still fill is not sized"
        `Quick test_a_strategy_with_an_order_that_may_still_fill_is_not_sized;
      Alcotest.test_case "the venue's submit is called in one place" `Quick
        test_the_venue's_submit_is_called_in_one_place;
      Alcotest.test_case
        "the calendar ties the signal and the close to the newest session" `Quick
        test_the_calendar_ties_the_signal_and_the_close_to_the_newest_session;
      Alcotest.test_case "a standing document is re-sized at the newest close" `Quick
        test_a_standing_document_is_resized_at_the_newest_close;
      Alcotest.test_case "a document through the gate once is re-sized at a later close"
        `Quick test_a_document_through_the_gate_once_is_resized_at_a_later_close;
      Alcotest.test_case
        "a signal from before an outage is not sized at a weeks-old close" `Quick
        test_a_signal_from_before_an_outage_is_not_sized_at_a_weeks_old_close;
      Alcotest.test_case "a rebalance is gated as if only its buys fill" `Quick
        test_a_rebalance_is_gated_as_if_only_its_buys_fill;
      Alcotest.test_case "which orders grow a position" `Quick
        test_which_orders_grow_a_position;
      Alcotest.test_case
        "a short strategy's rebalance covers first and is gated on its sells" `Quick
        test_a_short_strategy's_rebalance_covers_first_and_is_gated_on_its_sells;
      Alcotest.test_case "an order that crosses zero is ordered and gated as growing"
        `Quick test_an_order_that_crosses_zero_is_ordered_and_gated_as_growing;
      Alcotest.test_case "each order counts the ones before it against the open-order cap"
        `Quick test_each_order_counts_the_ones_before_it_against_the_open_order_cap;
      Alcotest.test_case "no targets while the book is not the account's" `Quick
        test_no_targets_while_the_book_is_not_the_account's;
      Alcotest.test_case "the plan sizes a strategy whatever the account nets it to"
        `Quick test_the_plan_sizes_a_strategy_whatever_the_account_nets_it_to;
      Alcotest.test_case "a rebalance is sized at the current mark, not yesterday's close"
        `Quick test_a_rebalance_is_sized_at_the_current_mark_not_yesterday's_close;
      Alcotest.test_case "a prefix of the send order is gated" `Quick
        test_a_prefix_of_the_send_order_is_gated;
      Alcotest.test_case "a later order alone is gated" `Quick
        test_a_later_order_alone_is_gated;
      Alcotest.test_case "a rebalance of more than six orders is refused" `Quick
        test_a_rebalance_of_more_than_six_orders_is_refused;
      Alcotest.test_case "the rebalance asks again what its plan read" `Quick
        test_the_rebalance_asks_again_what_its_plan_read;
      Alcotest.test_case "scale halves the target" `Quick test_scale_halves_the_target;
      Alcotest.test_case "scale zero flattens" `Quick test_scale_zero_flattens;
      Alcotest.test_case "scale 1.5 refused" `Quick test_scale_1_5_refused;
      Alcotest.test_case "an incubation strategy is sized at its effective fraction"
        `Quick test_an_incubation_strategy_is_sized_at_its_effective_fraction;
    ] )
