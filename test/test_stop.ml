(* INC-02's stop rules (a draft awaiting the owner): a strategy is stopped when its
   drawdown from peak on allocated capital reaches twice its own ex-ante volatility
   (floored at 5%, capped at 25%), or when the book's count of sessions
   has passed since its first fill. Stopped, it is recorded with the rule, said in
   the alerts, sized no more and flattened; only the owner's (reinstated DATE)
   brings it back. Until 2026-11-03 nothing evaluated them. *)

open Core
open Ohcamel.Types
module D = Ohcamel_desk
module Spec = Ohcamel.Config.Book.Signals_spec

let at = Time_ns.of_string_with_utc_offset
let d = Date.of_string
let zone = Time_float.Zone.of_utc_offset ~hours:(-4)

let stop_words ?(sessions = 500) ?(floor = 0.05) ?(cap = 0.25) vol =
  { Spec.Stop.vol_multiple = 2.0; floor; cap; sessions; ex_ante_vol = vol }

(* 2.0 x 0.075 = 0.15: the line INC-01 drew for everyone, so the fixtures below keep
   their arithmetic. *)
let line = stop_words ~sessions:250 0.075
let summary ~drawdown = { (D.Record.summarise []) with D.Record.max_drawdown = drawdown }

let test_the_drawdown_rule_fires_at_its_line () =
  let check dd = D.Stop.check ~spec:line ~record:(summary ~drawdown:dd) ~sessions:10 in
  (match check 0.15 with
  | `Stop (rule, detail) ->
      Alcotest.(check string) "the rule" "drawdown" rule;
      Alcotest.(check string)
        "the sentence"
        "drawdown 15.0% reached its line of 15.0% (2 x its ex-ante volatility of 7.5%, \
         floored at 5.0%, capped at 25.0%)"
        detail
  | `Continue -> Alcotest.fail "15% did not stop at a 15% line");
  match check 0.149 with
  | `Continue -> ()
  | `Stop _ -> Alcotest.fail "14.9% stopped at a 15% line"

let test_the_sessions_rule_fires_at_its_count () =
  let check n = D.Stop.check ~spec:line ~record:(summary ~drawdown:0.0) ~sessions:n in
  (match check 250 with
  | `Stop (rule, _) -> Alcotest.(check string) "the rule" "sessions" rule
  | `Continue -> Alcotest.fail "250 sessions did not stop");
  match check 249 with `Continue -> () | `Stop _ -> Alcotest.fail "249 sessions stopped"

(* Task 2.2: the line is the strategy's own. *)
let test_the_threshold_is_twice_the_volatility_between_a_floor_and_a_cap () =
  let t vol = D.Stop.threshold ~ex_ante_vol:vol ~multiple:2.0 ~floor:0.05 ~cap:0.25 in
  Alcotest.(check (float 1e-12)) "vol 0.04 gives 0.08" 0.08 (t 0.04);
  Alcotest.(check (float 1e-12)) "vol 0.02 gives the floor 0.05" 0.05 (t 0.02);
  Alcotest.(check (float 1e-12)) "vol 0.15 gives the cap 0.25" 0.25 (t 0.15);
  Alcotest.(check (float 1e-12)) "vol 0.025 is exactly the floor" 0.05 (t 0.025);
  Alcotest.(check (float 1e-12)) "vol 0.125 is exactly the cap" 0.25 (t 0.125)

let test_a_quiet_strategy_stops_at_its_own_line_a_volatile_one_at_the_cap () =
  let quiet = stop_words 0.04 and wild = stop_words 0.15 in
  let check spec dd = D.Stop.check ~spec ~record:(summary ~drawdown:dd) ~sessions:10 in
  (match check quiet 0.08 with
  | `Stop ("drawdown", _) -> ()
  | _ -> Alcotest.fail "8% did not stop a strategy of 4% volatility");
  (match check quiet 0.079 with
  | `Continue -> ()
  | `Stop _ -> Alcotest.fail "7.9% stopped a strategy of 4% volatility");
  (match check wild 0.24 with
  | `Continue -> ()
  | `Stop _ -> Alcotest.fail "24% stopped a strategy whose line is the 25% cap");
  match check wild 0.25 with
  | `Stop ("drawdown", _) -> ()
  | _ -> Alcotest.fail "25% did not stop at the cap"

let universe = [ "SPY"; "TLT" ]

let strategy_text ?(extra = "") name =
  sprintf
    "((name %s) (symbols (SPY)) (max_age 3) (sizing incubation) (capital_fraction 0.025) \
     %s)"
    name extra

let load text =
  Or_error.try_with (fun () ->
      Spec.t_of_sexp (Sexp.of_string (sprintf "((strategies (%s)))" text)))
  |> Or_error.bind ~f:(fun t -> Or_error.map (Spec.validate ~universe t) ~f:(fun () -> t))

let test_the_book_words_load_with_their_defaults () =
  (match load (strategy_text ~extra:"(ex_ante_vol 0.04)" "s_a") with
  | Ok { Spec.strategies = [ s ] } ->
      Alcotest.(check bool)
        "the four words default to INC-02's values" true
        (Option.equal Spec.Stop.equal s.Spec.Strategy.stop
           (Some
              {
                Spec.Stop.vol_multiple = 2.0;
                floor = 0.05;
                cap = 0.25;
                sessions = 500;
                ex_ante_vol = 0.04;
              }))
  | Ok _ | Error _ -> Alcotest.fail "a strategy with an ex_ante_vol did not load");
  match
    load
      (strategy_text
         ~extra:
           "(stop_vol_multiple 3.0) (stop_floor 0.06) (stop_cap 0.2) (stop_sessions 400) \
            (ex_ante_vol 0.05)"
         "s_b")
  with
  | Ok { Spec.strategies = [ s ] } ->
      Alcotest.(check bool)
        "written words are read" true
        (Option.equal Spec.Stop.equal s.Spec.Strategy.stop
           (Some
              {
                Spec.Stop.vol_multiple = 3.0;
                floor = 0.06;
                cap = 0.2;
                sessions = 400;
                ex_ante_vol = 0.05;
              }));
      Alcotest.(check bool)
        "and round-trip through the book's spelling" true
        (Spec.Strategy.equal s (Spec.Strategy.t_of_sexp (Spec.Strategy.sexp_of_t s)))
  | Ok _ | Error _ -> Alcotest.fail "the stop words did not load"

let test_a_strategy_with_no_ex_ante_vol_does_not_load_and_is_named () =
  match load (strategy_text "s_no_vol") with
  | Ok _ -> Alcotest.fail "a strategy with no ex_ante_vol loaded"
  | Error e ->
      let msg = Error.to_string_hum e in
      Alcotest.(check bool)
        "the message names the strategy and the word" true
        (String.is_substring msg ~substring:"s_no_vol"
        && String.is_substring msg ~substring:"ex_ante_vol")

let test_a_bad_stop_word_does_not_load () =
  let bad extra =
    match load (strategy_text ~extra "s_bad") with Ok _ -> false | Error _ -> true
  in
  Alcotest.(check bool) "zero vol" true (bad "(ex_ante_vol 0.0)");
  Alcotest.(check bool)
    "a floor above the cap" true
    (bad "(ex_ante_vol 0.04) (stop_floor 0.3)");
  Alcotest.(check bool) "no sessions" true (bad "(ex_ante_vol 0.04) (stop_sessions 0)");
  Alcotest.(check bool)
    "an old (stop ...) alone is no ex_ante_vol" true
    (match
       load (strategy_text ~extra:"(stop ((drawdown 0.15) (sessions 250)))" "s_old")
     with
    | Ok _ -> false
    | Error _ -> true)

(* Two strategies on one journal, each with its own name and fills. s_dd buys 10 AAPL
   at 100 on its first session and AAPL closes 100 then 80: -200 on 1,000 allocated,
   20% down. s_ok buys 10 MSFT at 100 and MSFT closes 100 then 95: 5% down. *)
let journal_with_two_strategies () =
  let j = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
  let aapl = Symbol.of_string "AAPL" and msft = Symbol.of_string "MSFT" in
  List.iteri
    [ ("s_dd", aapl); ("s_ok", msft) ]
    ~f:(fun n (name, symbol) ->
      let o =
        {
          (D.Order.create
             {
               D.Order.Request.client_order_id =
                 Option.value_exn
                   (D.Ids.Client_order_id.of_string
                      (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZ%02d" (90 + n)));
               symbol;
               side = D.Order.Side.Buy;
               qty = 10;
               kind = D.Order.Kind.Market;
               tif = D.Order.Tif.Opg;
             })
          with
          D.Order.state = D.Order.State.Filled;
        }
      in
      D.Journal.insert_order j o
        ~source:(D.Rebalance.Source.name ~strategy:name ~sequence:1)
        ~decision_price:(Price.of_float 100.0) ~arrival:None ~verdict:`Null
        ~at:(at "2026-10-19T23:15:00Z");
      ignore
        (D.Journal.record_fill j o
           {
             D.Order.Fill.execution_id = sprintf "x-stop-%d" n;
             qty = 10.0;
             price = Price.of_float 100.0;
             at = at "2026-10-20T13:30:00Z";
             position_qty = None;
           }
          : bool);
      D.Journal.record_sizing j ~strategy:name ~sequence:1 ~at:(at "2026-10-19T23:15:00Z")
        ~tier:"incubation" ~scale:1.0 ~allocated:1_000.0 ~mode:"open");
  List.iter
    [ ("2026-10-20", 100.0, 100.0); ("2026-10-21", 80.0, 95.0) ]
    ~f:(fun (date, a, m) ->
      D.Journal.record_marks j
        [
          { D.Journal.Mark.date = d date; symbol = aapl; close = a; qty = 10.0 };
          { D.Journal.Mark.date = d date; symbol = msft; close = m; qty = 10.0 };
        ];
      D.Journal.record_session j
        {
          D.Journal.Session.date = d date;
          equity_close = 1_000_000.0;
          cash_close = 0.0;
          gross_close = 0.0;
          net_close = 0.0;
          recorded_at = at (date ^ "T21:05:00Z");
        });
  j

let strategy ?reinstated ?(stop = Some line) name =
  {
    Spec.Strategy.name;
    symbols = [ "AAPL"; "MSFT" ];
    max_age = 3;
    sizing = Spec.Incubation;
    capital_fraction = 0.025;
    cadence = Spec.Cadence.Daily;
    adjust = Spec.Adjust.Intraday;
    max_age_minutes = 20;
    horizon = Spec.Horizon.Carry;
    stop;
    reinstated;
  }

let state j name =
  match D.Journal.strategy_state j ~strategy:name with
  | Some (`Stopped, rule, _, _) -> "stopped " ^ Option.value rule ~default:"?"
  | Some (`Active, _, _, _) -> "active"
  | None -> "none"

let test_the_close_stops_the_strategy_past_its_line () =
  let j = journal_with_two_strategies () in
  let strategies = [ strategy "s_dd"; strategy "s_ok"; strategy ~stop:None "s_none" ] in
  let evaluate () =
    D.Stop.evaluate
      ~defer_drawdown:(fun _ -> None)
      ~journal:j ~strategies ~zone ~at:(at "2026-10-21T21:05:00Z") ~on_event:ignore
  in
  Alcotest.(check (list string)) "s_dd alone, by its drawdown" [ "s_dd" ] (evaluate ());
  Alcotest.(check (list string))
    "and the states"
    [ "stopped drawdown"; "none"; "none" ]
    (List.map [ "s_dd"; "s_ok"; "s_none" ] ~f:(state j));
  Alcotest.(check (list string))
    "said once in the alerts" [ "incubation_stopped s_dd" ]
    (List.map (D.Journal.recent_alerts j ~limit:10) ~f:(fun a ->
         a.D.Journal.Alert.kind ^ " " ^ a.D.Journal.Alert.limit_name));
  Alcotest.(check (list string))
    "a stopped strategy is not stopped twice" [] (evaluate ());
  Alcotest.(check int)
    "nor said twice" 1
    (List.length (D.Journal.recent_alerts j ~limit:10));
  D.Journal.close j

let test_only_a_later_reinstatement_brings_it_back () =
  let j = journal_with_two_strategies () in
  ignore
    (D.Stop.evaluate
       ~defer_drawdown:(fun _ -> None)
       ~journal:j
       ~strategies:[ strategy "s_dd" ]
       ~zone ~at:(at "2026-10-21T21:05:00Z") ~on_event:ignore
      : string list);
  D.Stop.reinstate ~journal:j ~zone
    ~strategies:[ strategy ~reinstated:(d "2026-10-20") "s_dd" ]
    ~on_event:ignore;
  Alcotest.(check string)
    "a date before the stop does nothing" "stopped drawdown" (state j "s_dd");
  D.Stop.reinstate ~journal:j ~zone
    ~strategies:[ strategy ~reinstated:(d "2026-10-22") "s_dd" ]
    ~on_event:ignore;
  Alcotest.(check string) "a later one re-activates" "active" (state j "s_dd");
  (* Its record starts again at the reinstatement: the fall before it is the stop's
     answer, not a reason to stop again. *)
  Alcotest.(check (list string))
    "and the old drawdown does not stop it again" []
    (D.Stop.evaluate
       ~defer_drawdown:(fun _ -> None)
       ~journal:j
       ~strategies:[ strategy ~reinstated:(d "2026-10-22") "s_dd" ]
       ~zone ~at:(at "2026-10-22T21:05:00Z") ~on_event:ignore);
  D.Journal.close j

(* The review's false stop: s_dd is cut from 1,000 to 200 at 09:45 on the 21st, as
   the governor does when the book is losing, but the day's -200 was earned on the
   full position. Divided by the 200 in force at the close it reads -100%; the
   capital is the most the strategy held in force since the last close, 1,000, so
   it reads -20%. *)
let test_a_scale_cut_in_the_session_does_not_inflate_its_loss () =
  let j = journal_with_two_strategies () in
  D.Journal.record_sizing j ~strategy:"s_dd" ~sequence:1 ~at:(at "2026-10-21T13:45:00Z")
    ~tier:"incubation" ~scale:0.2 ~allocated:200.0 ~mode:"adjust";
  let record = D.Stop.sessions ~journal:j ~zone ~strategy:"s_dd" ~since:None in
  Alcotest.(check (list (float 1e-9)))
    "the capital each session" [ 1_000.0; 1_000.0 ]
    (List.map record ~f:(fun s -> s.D.Record.allocated));
  Alcotest.(check (float 1e-9))
    "so 20% down, not 100%" 0.2 (D.Record.summarise record).D.Record.max_drawdown;
  D.Journal.close j

(* And a session sized to 0 still counts the loss it took before the sale. *)
let test_a_session_sized_to_zero_still_counts () =
  let j = journal_with_two_strategies () in
  D.Journal.record_sizing j ~strategy:"s_dd" ~sequence:1 ~at:(at "2026-10-21T14:00:00Z")
    ~tier:"incubation" ~scale:0.0 ~allocated:0.0 ~mode:"adjust";
  let record = D.Stop.sessions ~journal:j ~zone ~strategy:"s_dd" ~since:None in
  Alcotest.(check int)
    "both sessions count" 2 (D.Record.summarise record).D.Record.sessions;
  D.Journal.close j

(* Task 1.8, through the journal. s_tlt buys 100 TLT at 90.00 on 2026-11-02, a day
   order sent at 10:00 New York against a 89.99/90.01 quote; TLT closes 90.00, then
   89.70 on 2026-11-03, its ex-date for 0.30 paid 2026-11-06. Its record reads 0.00
   and 0.00, where the record before Task 1.8 read 0.00 and -30.00 -- the ex-date as
   a loss. The fill's cost is 100 x (90.00 - 90.00) = 0.00 against the mid. *)
let tlt = Symbol.of_string "TLT"

let journal_holding_tlt ?(tif = D.Order.Tif.Day) ?(created = "2026-11-02T14:00:00Z")
    ?(arrival = Some (89.99, 90.01)) ?(stamp_age = Some 2.0) ?(through_pay_date = false)
    () =
  let j = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
  let o =
    {
      (D.Order.create
         {
           D.Order.Request.client_order_id =
             Option.value_exn
               (D.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZ80");
           symbol = tlt;
           side = D.Order.Side.Buy;
           qty = 100;
           kind = D.Order.Kind.Limit (Price.of_float 90.02);
           tif;
         })
      with
      D.Order.state = D.Order.State.Filled;
    }
  in
  D.Journal.insert_order
    ?arrival_at:
      (Option.map stamp_age ~f:(fun secs ->
           Time_ns.sub (at created) (Time_ns.Span.of_sec secs)))
    j o
    ~source:(D.Rebalance.Source.name ~strategy:"s_tlt" ~sequence:1)
    ~decision_price:(Price.of_float 90.0)
    ~arrival:(Option.map arrival ~f:(fun (b, a) -> (Price.of_float b, Price.of_float a)))
    ~verdict:`Null ~at:(at created);
  D.Journal.For_testing.run j ~what:"venue id"
    "UPDATE orders SET venue_order_id = 'venue-tlt-1'" [];
  ignore
    (D.Journal.record_fill j o
       {
         D.Order.Fill.execution_id = "x-tlt";
         qty = 100.0;
         price = Price.of_float 90.0;
         at = at "2026-11-02T14:00:01Z";
         position_qty = None;
       }
      : bool);
  D.Journal.record_sizing j ~strategy:"s_tlt" ~sequence:1 ~at:(at created)
    ~tier:"incubation" ~scale:1.0 ~allocated:10_000.0 ~mode:"open";
  List.iter
    ([ ("2026-11-02", 90.0); ("2026-11-03", 89.7) ]
    @ if through_pay_date then [ ("2026-11-06", 89.7) ] else [])
    ~f:(fun (date, c) ->
      D.Journal.record_marks j
        [ { D.Journal.Mark.date = d date; symbol = tlt; close = c; qty = 100.0 } ];
      D.Journal.record_session j
        {
          D.Journal.Session.date = d date;
          equity_close = 1_000_000.0;
          cash_close = 0.0;
          gross_close = 0.0;
          net_close = 0.0;
          recorded_at = at (date ^ "T21:05:00Z");
        });
  D.Journal.record_cash_dividends j
    [
      {
        D.Venue.Cash_dividend.id = "ca-tlt-nov";
        symbol = tlt;
        ex_date = d "2026-11-03";
        payable_date = Some (d "2026-11-06");
        rate = 0.30;
      };
    ]
    ~recorded_at:(at "2026-11-03T21:06:00Z");
  j

let pnls record = List.map record ~f:(fun (s : D.Record.session) -> s.D.Record.pnl)

let test_a_monthly_payer's_ex_date_is_not_a_loss () =
  let j = journal_holding_tlt () in
  let now = D.Stop.sessions ~journal:j ~zone ~strategy:"s_tlt" ~since:None in
  Alcotest.(check (list (float 1e-9))) "flat through the ex-date" [ 0.0; 0.0 ] (pnls now);
  Alcotest.(check (list (float 1e-9)))
    "before Task 1.8, a loss" [ 0.0; -30.0 ]
    (pnls
       (D.Stop.sessions_under ~rule:`Before_1_8 ~journal:j ~zone ~strategy:"s_tlt"
          ~since:None ()));
  Alcotest.(check (option (float 1e-9)))
    "measured against the 90.00 mid: 0 bps" (Some 0.0)
    (D.Record.summarise now).D.Record.cost_bps;
  D.Journal.close j

(* The pay date's cash is never a second dividend: the DIV activity posted on the
   payable date, 2026-11-06, a recorded session here, sets the rate paid (0.30, the
   same) and adds nothing to that session. A fee that
   names the fill's order is the fill's: 0.02 off the first session's P&L. A fee that
   names none is the account's and nobody's. *)
let test_the_cash_receipt_is_not_counted_twice () =
  let j = journal_holding_tlt ~through_pay_date:true () in
  let activity ?order_id ?symbol ?per_share ~id ~kind ~date net =
    {
      D.Venue.Activity.id;
      activity_type = kind;
      sub_type = None;
      date = d date;
      symbol;
      qty = None;
      per_share;
      net_amount = net;
      status = Some "executed";
      order_id;
    }
  in
  ignore
    (D.Journal.record_activities j
       [
         activity ~id:"div" ~kind:"DIV" ~date:"2026-11-06" ~symbol:tlt ~per_share:0.30
           30.0;
         activity ~id:"fee-named" ~kind:"FEE" ~date:"2026-11-03" ~order_id:"venue-tlt-1"
           (-0.02);
         activity ~id:"fee-account" ~kind:"FEE" ~date:"2026-11-03" (-0.05);
       ]
       ~recorded_at:(at "2026-11-07T21:06:00Z")
      : D.Venue.Activity.t list);
  let record = D.Stop.sessions ~journal:j ~zone ~strategy:"s_tlt" ~since:None in
  Alcotest.(check (list (float 1e-9)))
    "the fee once, the dividend once" [ -0.02; 0.0; 0.0 ] (pnls record);
  Alcotest.(check (float 1e-9))
    "30.00 credited, not 60.00" 30.0 (D.Record.summarise record).D.Record.dividends;
  let _, unassigned, count = D.Stop.fees_by_order (D.Journal.activities j) in
  Alcotest.(check (pair (float 1e-9) int))
    "the account's fee, nobody's" (0.05, 1) (unassigned, count);
  Alcotest.(check int)
    "every paid dividend has its record" 0
    (List.length
       (D.Stop.unmatched_dividends ~cash_dividends:(D.Journal.cash_dividends j)
          ~activities:(D.Journal.activities j)));
  D.Journal.close j

(* Which arrival quote is one. A day order at 10:00 New York against 89.99/90.01,
   stamped 2 s before the order: the mid, 90.00. A market-on-open order (its quote read
   at 19:15 the evening before), a day order created at 19:15, a crossed quote and no
   quote at all: unknown. And fix round 1's rule: a quote whose own stamp is more than
   60 s older than its order, or an arrival with no stamp at all -- every order
   journaled before the stamp was (a pair of numbers that may have been a stale
   print's) -- is unknown. *)
let test_only_a_session's_quote_is_an_arrival () =
  let mid ?tif ?created ?arrival ?stamp_age () =
    let j = journal_holding_tlt ?tif ?created ?arrival ?stamp_age () in
    let f =
      List.hd_exn
        (D.Journal.source_fill_details j
           ~prefix:(D.Rebalance.Source.prefix ~strategy:"s_tlt"))
    in
    D.Journal.close j;
    D.Stop.arrival_mid ~zone f
  in
  let unknown = Alcotest.(option (float 1e-9)) in
  Alcotest.check unknown "a session's quote" (Some 90.0) (mid ());
  Alcotest.check unknown "an auction's" None
    (mid ~tif:D.Order.Tif.Opg ~created:"2026-11-02T23:15:00Z" ());
  Alcotest.check unknown "after hours" None (mid ~created:"2026-11-02T23:15:00Z" ());
  Alcotest.check unknown "crossed" None (mid ~arrival:(Some (90.01, 89.99)) ());
  Alcotest.check unknown "none journaled" None (mid ~arrival:None ());
  Alcotest.check unknown "stamped 90 s before its order" None
    (mid ~stamp_age:(Some 90.0) ());
  Alcotest.check unknown "journaled with no stamp" None (mid ~stamp_age:None ())

(* Fix round 1, I1: a night whose dividends could not be read, or whose bars show a
   distribution nothing records, defers the DRAWDOWN rule for the strategies it names --
   said once -- and the sessions rule still runs. s_dd is 20% down on a 15% line: with
   its drawdown deferred it is not stopped, and the next night, with nothing deferred,
   it is. A strategy past its sessions count is stopped whatever is deferred. *)
let test_a_deferred_drawdown_rule_does_not_fire_that_night () =
  let j = journal_with_two_strategies () in
  let said = ref [] in
  let evaluate ~defer strategies =
    D.Stop.evaluate ~defer_drawdown:defer ~journal:j ~strategies ~zone
      ~at:(at "2026-10-21T21:05:00Z") ~on_event:(fun l -> said := l :: !said)
  in
  Alcotest.(check (list string))
    "deferred: not stopped" []
    (evaluate
       ~defer:(fun name ->
         Option.some_if (String.equal name "s_dd")
           "the venue's dividends could not be read")
       [ strategy "s_dd" ]);
  Alcotest.(check bool)
    "and said" true
    (List.exists !said ~f:(fun l ->
         String.is_substring l ~substring:"s_dd drawdown rule deferred tonight"
         && String.is_substring l ~substring:"the venue's dividends could not be read"));
  Alcotest.(check (list string))
    "a sessions count still stops a deferred strategy" [ "s_ok" ]
    (evaluate
       ~defer:(fun _ -> Some "deferred")
       [ strategy ~stop:(Some { line with Spec.Stop.sessions = 2 }) "s_ok" ]);
  Alcotest.(check (list string))
    "the next night, nothing deferred, it fires" [ "s_dd" ]
    (evaluate ~defer:(fun _ -> None) [ strategy "s_dd" ]);
  D.Journal.close j

(* Fix round 1, minor 9. The rate PAID wins over the rate announced: the record says
   0.30, the DIV activity posted on 2026-11-07 (a day after the payable date, inside
   the three-day window) says 0.31, so 100 shares are credited 31.00. *)
let test_the_rate_paid_wins () =
  let j = journal_holding_tlt () in
  ignore
    (D.Journal.record_activities j
       [
         {
           D.Venue.Activity.id = "div-paid";
           activity_type = "DIV";
           sub_type = Some "CDIV";
           date = d "2026-11-07";
           symbol = Some tlt;
           qty = Some 100.0;
           per_share = Some 0.31;
           net_amount = 31.0;
           status = Some "executed";
           order_id = None;
         };
       ]
       ~recorded_at:(at "2026-11-08T21:06:00Z")
      : D.Venue.Activity.t list);
  Alcotest.(check (float 1e-9))
    "31.00, not 30.00" 31.0
    (D.Record.summarise (D.Stop.sessions ~journal:j ~zone ~strategy:"s_tlt" ~since:None))
      .D.Record.dividends;
  D.Journal.close j

(* One journal, two strategies on TLT through its ex-date: s_tlt long 100 is
   credited 30.00, s_short short 100 is debited 30.00, and the two net to 0.00 -- the
   account, flat in TLT at the prior close, is paid nothing. *)
let test_a_long_and_a_short_net_to_the_account's_receipt () =
  let j = journal_holding_tlt () in
  let o =
    {
      (D.Order.create
         {
           D.Order.Request.client_order_id =
             Option.value_exn
               (D.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZ81");
           symbol = tlt;
           side = D.Order.Side.Sell;
           qty = 100;
           kind = D.Order.Kind.Limit (Price.of_float 89.98);
           tif = D.Order.Tif.Day;
         })
      with
      D.Order.state = D.Order.State.Filled;
    }
  in
  D.Journal.insert_order j o
    ~source:(D.Rebalance.Source.name ~strategy:"s_short" ~sequence:1)
    ~decision_price:(Price.of_float 90.0) ~arrival:None ~verdict:`Null
    ~at:(at "2026-11-02T14:00:00Z");
  ignore
    (D.Journal.record_fill j o
       {
         D.Order.Fill.execution_id = "x-short";
         qty = 100.0;
         price = Price.of_float 90.0;
         at = at "2026-11-02T14:00:02Z";
         position_qty = None;
       }
      : bool);
  D.Journal.record_sizing j ~strategy:"s_short" ~sequence:1
    ~at:(at "2026-11-02T14:00:00Z") ~tier:"incubation" ~scale:1.0 ~allocated:10_000.0
    ~mode:"open";
  let dividends strategy =
    (D.Record.summarise (D.Stop.sessions ~journal:j ~zone ~strategy ~since:None))
      .D.Record.dividends
  in
  Alcotest.(check (float 1e-9)) "the long credited" 30.0 (dividends "s_tlt");
  Alcotest.(check (float 1e-9)) "the short debited" (-30.0) (dividends "s_short");
  Alcotest.(check (float 1e-9))
    "netting to the flat account's 0.00" 0.0
    (dividends "s_tlt" +. dividends "s_short");
  D.Journal.close j

let suite =
  ( "stop",
    [
      Alcotest.test_case "the rate paid wins" `Quick test_the_rate_paid_wins;
      Alcotest.test_case "a long and a short net to the account's receipt" `Quick
        test_a_long_and_a_short_net_to_the_account's_receipt;
      Alcotest.test_case "a deferred drawdown rule does not fire that night" `Quick
        test_a_deferred_drawdown_rule_does_not_fire_that_night;
      Alcotest.test_case "a monthly payer's ex-date is not a loss" `Quick
        test_a_monthly_payer's_ex_date_is_not_a_loss;
      Alcotest.test_case "the cash receipt is not counted twice" `Quick
        test_the_cash_receipt_is_not_counted_twice;
      Alcotest.test_case "only a session's quote is an arrival" `Quick
        test_only_a_session's_quote_is_an_arrival;
      Alcotest.test_case "the threshold is twice the volatility, floored and capped"
        `Quick test_the_threshold_is_twice_the_volatility_between_a_floor_and_a_cap;
      Alcotest.test_case "a quiet strategy stops at its own line" `Quick
        test_a_quiet_strategy_stops_at_its_own_line_a_volatile_one_at_the_cap;
      Alcotest.test_case "the book words load with their defaults" `Quick
        test_the_book_words_load_with_their_defaults;
      Alcotest.test_case "no ex_ante_vol does not load, and is named" `Quick
        test_a_strategy_with_no_ex_ante_vol_does_not_load_and_is_named;
      Alcotest.test_case "a bad stop word does not load" `Quick
        test_a_bad_stop_word_does_not_load;
      Alcotest.test_case "the drawdown rule fires at its line" `Quick
        test_the_drawdown_rule_fires_at_its_line;
      Alcotest.test_case "the sessions rule fires at its count" `Quick
        test_the_sessions_rule_fires_at_its_count;
      Alcotest.test_case "a scale cut in the session does not inflate its loss" `Quick
        test_a_scale_cut_in_the_session_does_not_inflate_its_loss;
      Alcotest.test_case "a session sized to zero still counts" `Quick
        test_a_session_sized_to_zero_still_counts;
      Alcotest.test_case "the close stops the strategy past its line" `Quick
        test_the_close_stops_the_strategy_past_its_line;
      Alcotest.test_case "only a later reinstatement brings it back" `Quick
        test_only_a_later_reinstatement_brings_it_back;
    ] )
