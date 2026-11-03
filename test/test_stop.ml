(* INC-01's stop rules: a strategy is stopped when its drawdown from peak on
   allocated capital reaches the book's line, or when the book's count of sessions
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
let line = { Spec.Stop.drawdown = 0.15; sessions = 250 }
let summary ~drawdown = { (D.Record.summarise []) with D.Record.max_drawdown = drawdown }

let test_the_drawdown_rule_fires_at_its_line () =
  let check dd = D.Stop.check ~spec:line ~record:(summary ~drawdown:dd) ~sessions:10 in
  (match check 0.15 with
  | `Stop (rule, detail) ->
      Alcotest.(check string) "the rule" "drawdown" rule;
      Alcotest.(check string)
        "the sentence" "drawdown 15.0% reached its line of 15.0%" detail
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
    D.Stop.evaluate ~journal:j ~strategies ~zone ~at:(at "2026-10-21T21:05:00Z")
      ~on_event:ignore
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
    (D.Stop.evaluate ~journal:j
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
    (D.Stop.evaluate ~journal:j
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

let suite =
  ( "stop",
    [
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
