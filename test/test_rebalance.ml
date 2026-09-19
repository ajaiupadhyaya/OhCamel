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

(* The account holds whatever the strategy does unless a case says otherwise:
   [account] defaults to the strategy's own position. *)
let plan ?(symbols = [ spy ]) ?(capital_fraction = 0.5) ?(equity = Ok 100_000.0)
    ?(current = Ok Symbol.Map.empty) ?account ~price weights =
  let account =
    match account with
    | Some a -> a
    | None -> Result.ok current |> Option.value ~default:Symbol.Map.empty
  in
  D.Rebalance.plan ~symbols ~weights ~capital_fraction ~equity ~account
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
       ~capital_fraction:0.5 ~equity:(Ok 100_000.0) ~account:Symbol.Map.empty
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

let with_oms ?(now = evening) ?(session_date = "2026-09-14")
    ?(closes = [ (aapl, 149.0); (msft, 299.0); (xom, 100.0) ])
    ?(spec = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled })
    ?(book_is_current = true) ?(held = [ (aapl, 400.0); (msft, 100.0); (xom, -200.0) ]) ~f
    () =
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
      List.iter held ~f:(fun (s, q) ->
          let p =
            List.Assoc.find_exn
              [ (aapl, 150.0); (msft, 300.0); (xom, 100.0) ]
              s ~equal:Symbol.equal
          in
          Graph.set_qty graph s (Qty.of_float q);
          Graph.set_returns graph s
            [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
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
        D.Oms.create ~graph ~journal ~spec
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
          ~now:(fun () -> now)
          ~rng:(Random.State.make [| 15 |]) ~on_change:ignore ~on_event:ignore
          ~after_fill:ignore
          ~book_is_current:(fun () -> book_is_current)
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
        ~substring:"order 2 of 2 (MSFT buy 30 market-on-open) at 299.00" why;
      let why =
        refusal (check [ leg aapl D.Order.Side.Buy 20; leg msft D.Order.Side.Buy 100 ])
      in
      contains "a rule, naming the order"
        ~substring:"order 2 of 2 (MSFT buy 100 market-on-open): notional: 100 x 299.00"
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
    D.Rebalance.calendar_refusal ~zone ~now:(at now) ~as_of:(d as_of)
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

(* C2, where the rebalance is checked: the review's case end to end. The
   newest recorded session is 28 August, the signal is of that day, and it is
   14 September at 19:15 EDT. Refused, naming the eleven weekdays, and
   nothing journaled. *)
let test_a_signal_from_before_an_outage_is_not_sized_at_a_weeks_old_close () =
  with_oms ~session_date:"2026-08-28"
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms
             ~source:
               { source with D.Rebalance.Source.as_of = Date.of_string "2026-08-28" }
             ~legs:[ leg aapl D.Order.Side.Buy 10 ])
      in
      contains "the weekdays"
        ~substring:"11 weekdays after the newest recorded session (2026-08-28)" why;
      Alcotest.(check int) "nothing journaled" 0 (journaled journal))
    ()

(* C1, the gate: a sell can go unfilled at the auction, or be refused, and
   leave its buy alone, so a rebalance is gated as if only its buys fill as
   well as whole. AAPL -160 and MSFT +80, each under the 25,000 order cap at
   its close (23,840 and 23,920):
     whole:      AAPL 240 x 150 + MSFT 180 x 300 = 36,000 + 54,000 =  90,000  passes
     buys only:  AAPL 400 x 150 + MSFT 180 x 300 = 60,000 + 54,000 = 114,000  over tech-cap
   Refused, naming the buys-only gate and tech-cap. The legs were given buy
   first and are checked sells first: order 1 of 2 is the sell. With MSFT +30
   instead, the buys alone come to 60,000 + 39,000 = 99,000, and it passes. *)
let test_a_rebalance_is_gated_as_if_only_its_buys_fill () =
  with_oms
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~source
             ~legs:[ leg msft D.Order.Side.Buy 80; leg aapl D.Order.Side.Sell 160 ])
      in
      contains "the buys-only gate"
        ~substring:
          "the gate refuses the rebalance if only the orders that grow a position fill"
        why;
      contains "tech-cap" ~substring:"tech-cap would be breached" why;
      contains "sells first"
        ~substring:"order 2 of 2 (MSFT buy 80 market-on-open) at 299.00" why;
      Alcotest.(check bool)
        "MSFT +30 beside the sell: passes" true
        (Result.is_ok
           (D.Oms.check_rebalance oms ~source
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
     whole:          AAPL 180 x 150 + MSFT 180 x 300 = 27,000 + 54,000 =  81,000
     the sell alone: AAPL 330 x 150 + MSFT 180 x 300 = 49,500 + 54,000 = 103,500
   which is over tech-cap's 100,000: refused, naming the growing-only gate.
   Shorting MSFT 20 instead, the sell alone is 49,500 + 36,000 = 85,500, and it
   passes, the cover first whatever order the legs came in. *)
let test_a_short_strategy's_rebalance_covers_first_and_is_gated_on_its_sells () =
  with_oms
    ~held:[ (aapl, -330.0); (msft, -100.0); (xom, -200.0) ]
    ~f:(fun oms journal ->
      let why =
        refusal
          (D.Oms.check_rebalance oms ~source
             ~legs:[ leg msft D.Order.Side.Sell 80; leg aapl D.Order.Side.Buy 150 ])
      in
      contains "the growing-only gate"
        ~substring:
          "the gate refuses the rebalance if only the orders that grow a position fill"
        why;
      contains "tech-cap" ~substring:"tech-cap would be breached" why;
      contains "the cover first, the short last"
        ~substring:"(order 2 of 2 (MSFT sell 80 market-on-open) at 299.00): tech-cap" why;
      (match
         D.Oms.check_rebalance oms ~source
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
        D.Oms.check_rebalance oms ~source
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
          (D.Oms.check_rebalance oms ~source
             ~legs:[ leg aapl D.Order.Side.Buy 160; leg msft D.Order.Side.Sell 80 ])
      in
      contains "the flip, gated as growing"
        ~substring:
          "if only the orders that grow a position fill, as an unfilled or refused order \
           that shrinks one would leave them (order 2 of 2 (AAPL buy 160 market-on-open) \
           at 149.00): tech-cap"
        why;
      contains "a name twice" ~substring:"the rebalance names AAPL twice"
        (refusal
           (D.Oms.check_rebalance oms ~source
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
           (D.Oms.check_rebalance oms ~source ~legs:[ leg aapl D.Order.Side.Buy 1 ]));
      contains "the second of two"
        ~substring:
          "order 2 of 2 (MSFT buy 1 market-on-open): open_orders: 1 orders are already \
           open; the cap is 1"
        (refusal
           (D.Oms.check_rebalance oms ~source
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
           (D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ])))
    ()

(* I2: the account must hold at least the strategy's own position in its
   direction. The strategy holds SPY 100 and the account only 60 -- a hand
   sale, a reverse split, a fill the desk missed -- so a flat signal would
   sell 100 where 60 are held: no targets. Short 50 against an account short
   only 20, the same. An account holding 100, or 300 with a hand lot beside
   the strategy's, sells exactly the strategy's 100. *)
let test_no_targets_when_the_account_holds_less_than_the_strategy's_own () =
  let current q = Ok (Symbol.Map.singleton spy q) in
  let account q = Symbol.Map.singleton spy q in
  (match plan ~current:(current 100.0) ~account:(account 60.0) ~price:500.0 [] with
  | Ok _ -> Alcotest.fail "a flat signal sold what the account does not hold"
  | Error why ->
      contains "long"
        ~substring:"the account holds 60 SPY, less than the strategy's own 100" why);
  (match plan ~current:(current (-50.0)) ~account:(account (-20.0)) ~price:500.0 [] with
  | Ok _ -> Alcotest.fail "a short the account does not hold was bought back"
  | Error why ->
      contains "short"
        ~substring:"the account holds -20 SPY, less than the strategy's own -50" why);
  List.iter [ 100.0; 300.0 ] ~f:(fun held ->
      Alcotest.(check (list (triple string int int)))
        (sprintf "the account holds %g: sell exactly 100" held)
        [ ("sell", 100, 0) ]
        (legs (plan ~current:(current 100.0) ~account:(account held) ~price:500.0 [])))

(* The recorded close, never the live mark. E is 1,000,000 + 400 x 150 +
   100 x 300 - 200 x 100 = 1,070,000 at the live book; at a weight of 1 and a
   fraction of 0.1 that is 107,000 of AAPL. At the recorded close of 149 it
   is 107,000 / 149 = 718.12 -> 718 shares; at the live mark of 150 it would
   be 713.33 -> 713. And the order is checked and journaled at 149. *)
let test_a_rebalance_is_sized_and_priced_at_the_close_not_the_live_mark () =
  with_oms
    ~f:(fun oms _ ->
      (match
         D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[ (aapl, 1.0) ]
       with
      | Error why -> Alcotest.failf "no targets: %s" why
      | Ok ls ->
          Alcotest.(check (list (triple string int int)))
            "718 at 149, not 713 at 150"
            [ ("buy", 718, 718) ]
            (List.map ls ~f:(fun (l : Leg.t) ->
                 (D.Order.Side.to_string l.Leg.side, l.Leg.qty, l.Leg.target))));
      match D.Oms.check_rebalance oms ~source ~legs:[ leg aapl D.Order.Side.Buy 10 ] with
      | Error why -> Alcotest.failf "refused: %s" why
      | Ok checked ->
          Alcotest.(check (list (float 0.0)))
            "the decision price is the close" [ 149.0 ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (_, p) ->
                 Price.to_float p)))
    ()

(* The final review's probe: every prefix of the send order is gated. The
   account holds AAPL -500 by hand and MSFT +350, the strategy's own (one
   fill, journaled); a long-only two-symbol strategy buys AAPL 500 and sells
   MSFT 350. Both orders shrink a position -- AAPL -500 to 0, MSFT 350 to 0
   -- so the growing-only gate has nothing to gate, and by name the AAPL buy
   goes first. The order cap is raised to 200,000 for this case, so no rule
   stands in front of the gate (74,500 and 104,650 at the closes). TECH is
   the size of a net sum:
     the unit:           AAPL 0 x 150 + MSFT   0 x 300 =       0  passes
     the AAPL buy alone: AAPL 0 x 150 + MSFT 350 x 300 = 105,000  over tech-cap
   and the AAPL buy alone is what reaches the book when the venue does not
   acknowledge the MSFT sell, or the switch is set before it. Refused, naming
   the prefix, its order and tech-cap, and nothing journaled. With MSFT 300
   held instead, the AAPL buy alone is 90,000, and the rebalance passes, the
   buy first: the prefixes refuse what breaches and nothing else. (A single
   order's only prefix is the rebalance itself, gated once: the cases above
   are judged as they were.) *)
let test_every_prefix_of_the_send_order_is_gated () =
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
      let why = refusal (D.Oms.check_rebalance oms ~source ~legs:(legs 350)) in
      contains "the prefix"
        ~substring:"the gate refuses the rebalance if it stops after order 1 of 2" why;
      contains "its one order, and tech-cap"
        ~substring:"(order 1 of 2 (AAPL buy 500 market-on-open) at 149.00): tech-cap" why;
      Alcotest.(check int)
        "nothing journaled but the strategy's own fill" 1 (journaled journal))
    ();
  with_oms ~spec
    ~held:[ (aapl, -500.0); (msft, 300.0); (xom, -200.0) ]
    ~f:(fun oms _ ->
      match D.Oms.check_rebalance oms ~source ~legs:(legs 300) with
      | Error why -> Alcotest.failf "MSFT 300: refused: %s" why
      | Ok checked ->
          Alcotest.(check (list string))
            "MSFT 300: passes, the AAPL buy first" [ "AAPL"; "MSFT" ]
            (List.map checked.D.Oms.Checked_rebalance.orders ~f:(fun (r, _) ->
                 Symbol.to_string r.D.Order.Request.symbol)))
    ()

(* [plan_rebalance] runs outside the order manager's queue, and
   [check_rebalance] inside it, so what the plan read is asked again there.
   The strategy owns AAPL 100 (one fill, journaled) and the account holds
   400: a flat signal plans a sale of 100. Then, before the rebalance's turn,
   the account's AAPL reads 60 -- a hand sale -- and the check refuses in
   I2's words. And with the book no longer the account's, the equity is
   unknown: refused in the sizing's words, ahead of the rules. *)
let test_the_rebalance_asks_again_what_its_plan_read () =
  with_oms
    ~f:(fun oms journal ->
      journal_order journal ~n:8 ~source:"signal:exp_a01_tech:1"
        ~state:D.Order.State.Filled ~fills:[ 100.0 ] 100;
      let legs =
        match D.Oms.plan_rebalance oms ~strategy:tech_strategy ~targets:[] with
        | Ok legs -> legs
        | Error why -> Alcotest.failf "the plan: %s" why
      in
      Alcotest.(check (list (pair string int)))
        "the plan: sell 100"
        [ ("sell", 100) ]
        (List.map legs ~f:(fun (l : Leg.t) ->
             (D.Order.Side.to_string l.Leg.side, l.Leg.qty)));
      Graph.set_qty oms.D.Oms.graph aapl (Qty.of_float 60.0);
      Graph.stabilize oms.D.Oms.graph;
      contains "I2, asked again"
        ~substring:"the account holds 60 AAPL, less than the strategy's own 100"
        (refusal (D.Oms.check_rebalance oms ~source ~legs)))
    ();
  with_oms ~book_is_current:false
    ~f:(fun oms _ ->
      contains "the equity, asked again"
        ~substring:
          "the book's equity is unknown: the desk has not applied a recent read of the \
           account"
        (refusal
           (D.Oms.check_rebalance oms ~source ~legs:[ leg aapl D.Order.Side.Buy 10 ])))
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
      Alcotest.test_case
        "the calendar ties the signal and the close to the newest session" `Quick
        test_the_calendar_ties_the_signal_and_the_close_to_the_newest_session;
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
      Alcotest.test_case "no targets when the account holds less than the strategy's own"
        `Quick test_no_targets_when_the_account_holds_less_than_the_strategy's_own;
      Alcotest.test_case "a rebalance is sized and priced at the close, not the live mark"
        `Quick test_a_rebalance_is_sized_and_priced_at_the_close_not_the_live_mark;
      Alcotest.test_case "every prefix of the send order is gated" `Quick
        test_every_prefix_of_the_send_order_is_gated;
      Alcotest.test_case "the rebalance asks again what its plan read" `Quick
        test_the_rebalance_asks_again_what_its_plan_read;
    ] )
