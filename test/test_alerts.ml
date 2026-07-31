(* Phase 4. Alerting and the kill switch.

   NOTHING HERE SENDS ANYTHING. No webhook is posted, no file outside a temp
   directory is written, and no network call is made. That is not only about
   test hygiene: this is the module that can act on the outside world, and a
   test suite that occasionally posts to a real Slack channel would be a
   liability rather than a safeguard.

   Most of what follows checks what the tracker does NOT emit. An alerting
   system is judged on its silence -- a channel that fires on every tick is a
   channel people mute, and a muted channel is worse than none because everyone
   believes they are being watched. *)

open Core
module Alerts = Ohcamel.Alerts
module Config = Ohcamel.Config
module Graph = Ohcamel.Graph
open Ohcamel.Types

let aapl = Symbol.of_string "AAPL"
let tech = Sector.of_string "TECH"

let cap =
  {
    Limit.name = "aapl-cap";
    scope = Limit.Instrument aapl;
    kind = Limit.Gross_notional (Notional.of_float 100.0);
  }

let dd =
  { Limit.name = "dd-cap"; scope = Limit.Portfolio; kind = Limit.Max_drawdown 0.10 }

let at = Time.epoch

(* One evaluation of [cap] at the given observed value. Threshold is 100, so the
   observed value doubles as the utilisation percentage. *)
let evaluated ?(limit = cap) observed =
  (limit, Some (Ohcamel.Limits.evaluate ~limit ~observed))

let unevaluated ?(limit = cap) () = (limit, None)
let kinds events = List.map events ~f:(fun e -> e.Alerts.Event.kind)
let tracker ?(clear_below = 0.95) () = Alerts.Tracker.create ~clear_below

(* ------------------------------------------------------------------------ *)
(* Edge triggering                                                           *)
(* ------------------------------------------------------------------------ *)

(* A limit that has been breached for twenty minutes is one piece of news, not
   twenty. *)
let test_raises_once () =
  let t = tracker () in
  Alcotest.(check int)
    "quiet below the line" 0
    (List.length (Alerts.Tracker.step t ~at [ evaluated 90.0 ]));
  Alcotest.(check (list string))
    "crossing raises" [ "Raised" ]
    (List.map
       (kinds (Alerts.Tracker.step t ~at [ evaluated 120.0 ]))
       ~f:(fun k -> Sexp.to_string (Alerts.Event.sexp_of_kind k)));
  List.iter [ 121.0; 150.0; 200.0; 130.0 ] ~f:(fun observed ->
      Alcotest.(check int)
        (sprintf "still breached at %.0f: silent" observed)
        0
        (List.length (Alerts.Tracker.step t ~at [ evaluated observed ])));
  Alcotest.(check bool) "and it is still firing" true (Alerts.Tracker.firing t "aapl-cap")

(* Hysteresis. Without it, a limit resting exactly on its threshold flaps
   breached/cleared on every tick and produces an alert storm -- which is
   precisely how a channel earns its mute. *)
let test_hysteresis () =
  let t = tracker ~clear_below:0.95 () in
  ignore (Alerts.Tracker.step t ~at [ evaluated 120.0 ] : Alerts.Event.t list);
  (* Back under the line, but only just. Not resolution. *)
  List.iter [ 99.9; 99.0; 96.0; 95.5 ] ~f:(fun observed ->
      Alcotest.(check int)
        (sprintf "%.1f is inside the band, not cleared" observed)
        0
        (List.length (Alerts.Tracker.step t ~at [ evaluated observed ])));
  Alcotest.(check bool)
    "still firing throughout" true
    (Alerts.Tracker.firing t "aapl-cap");
  (* Properly back inside. *)
  Alcotest.(check int)
    "94.9 clears" 1
    (List.length (Alerts.Tracker.step t ~at [ evaluated 94.9 ]));
  Alcotest.(check bool) "no longer firing" false (Alerts.Tracker.firing t "aapl-cap");
  (* And having cleared, it can raise again. *)
  Alcotest.(check int)
    "re-raises on a fresh breach" 1
    (List.length (Alerts.Tracker.step t ~at [ evaluated 130.0 ]))

(* The flapping case stated directly: a value oscillating around the threshold
   must produce ONE alert, not one per tick. *)
let test_does_not_flap () =
  let t = tracker () in
  let events =
    List.concat_map [ 101.0; 99.0; 101.5; 98.0; 100.5; 99.5; 102.0 ] ~f:(fun observed ->
        Alerts.Tracker.step t ~at [ evaluated observed ])
  in
  Alcotest.(check int) "seven ticks across the line, one alert" 1 (List.length events);
  Alcotest.(check bool)
    "raised, not cleared" true
    (match kinds events with [ Alerts.Event.Raised ] -> true | _ -> false)

(* THE ONE THAT MATTERS MOST.

   If a firing limit becomes unevaluable -- the VaR input vanishes, the feed
   dies -- the alert must NOT clear. "I can no longer tell" is not "it is fine",
   and an alerting system that resolves an incident because it lost sight of it
   is worse than one that never fired: it actively reports the all-clear.

   This is the same principle the graph applies to unevaluated limits and the
   dashboard applies to stale prices, at the one layer where getting it wrong
   sends someone back to bed. *)
let test_lost_data_never_clears () =
  let t = tracker () in
  ignore (Alerts.Tracker.step t ~at [ evaluated 150.0 ] : Alerts.Event.t list);
  Alcotest.(check bool) "firing" true (Alerts.Tracker.firing t "aapl-cap");
  List.iter [ 1; 2; 3 ] ~f:(fun round ->
      Alcotest.(check int)
        (sprintf "round %d of no data: nothing emitted" round)
        0
        (List.length (Alerts.Tracker.step t ~at [ unevaluated () ])));
  Alcotest.(check bool)
    "STILL firing after the data went away" true
    (Alerts.Tracker.firing t "aapl-cap");
  (* And when data returns showing it genuinely recovered, then it clears. *)
  Alcotest.(check int)
    "recovery on real data clears" 1
    (List.length (Alerts.Tracker.step t ~at [ evaluated 50.0 ]))

(* A limit that was never breached and then goes unevaluable stays quiet. Silence
   in, silence out. *)
let test_unevaluated_from_quiet_is_quiet () =
  let t = tracker () in
  Alcotest.(check int)
    "no data, never breached, nothing to say" 0
    (List.length (Alerts.Tracker.step t ~at [ unevaluated () ]))

(* Limits are tracked independently -- one breaching says nothing about another. *)
let test_limits_are_independent () =
  let t = tracker () in
  let events = Alerts.Tracker.step t ~at [ evaluated 150.0; evaluated ~limit:dd 0.05 ] in
  Alcotest.(check int) "only the breached one fires" 1 (List.length events);
  Alcotest.(check string)
    "and it is the right one" "aapl-cap" (List.hd_exn events).Alerts.Event.limit_name;
  Alcotest.(check bool) "the other is not firing" false (Alerts.Tracker.firing t "dd-cap")

(* ------------------------------------------------------------------------ *)
(* Rendering                                                                 *)
(* ------------------------------------------------------------------------ *)

(* Units survive into the message. A drawdown alert saying "$0.12 over $0.10"
   instead of "12.00% over 10.00%" is the sort of thing that gets misread at
   speed, which is the only speed alerts are read at. *)
let test_units_in_messages () =
  let t = tracker () in
  let money = List.hd_exn (Alerts.Tracker.step t ~at [ evaluated 150.0 ]) in
  Alcotest.(check bool)
    "a notional limit renders as dollars" true
    (String.is_substring (Alerts.Event.to_line money) ~substring:"$150.00");
  let t = tracker () in
  let fraction = List.hd_exn (Alerts.Tracker.step t ~at [ evaluated ~limit:dd 0.125 ]) in
  let line = Alerts.Event.to_line fraction in
  Alcotest.(check bool)
    "a drawdown limit renders as a percentage" true
    (String.is_substring line ~substring:"12.50%");
  Alcotest.(check bool)
    "and says what it breached" true
    (String.is_substring line ~substring:"10.00%");
  Alcotest.(check bool)
    "no dollar sign anywhere near it" false
    (String.is_substring line ~substring:"$")

let test_slack_payload_shape () =
  let t = tracker () in
  let event = List.hd_exn (Alerts.Tracker.step t ~at [ evaluated 150.0 ]) in
  let json = Alerts.Event.to_slack_json event in
  match json with
  | `Assoc [ ("text", `String text) ] ->
      Alcotest.(check bool)
        "names the limit" true
        (String.is_substring text ~substring:"aapl-cap");
      Alcotest.(check bool)
        "and identifies the sender" true
        (String.is_substring text ~substring:"ohcamel")
  | other -> Alcotest.failf "unexpected slack payload: %s" (Yojson.Safe.to_string other)

(* ------------------------------------------------------------------------ *)
(* The kill switch                                                           *)
(* ------------------------------------------------------------------------ *)

let alerts_config ?(enabled = true) ?(kill = false) ?(trips_on = []) () =
  {
    Config.Alerts.enabled;
    sinks = [ Config.Alerts.Sink.Dry_run ];
    clear_below = 0.95;
    kill_switch_enabled = kill;
    kill_switch_trips_on = trips_on;
  }

let raised observed =
  let t = tracker () in
  List.hd_exn (Alerts.Tracker.step t ~at [ evaluated observed ])

(* Disarmed is the default and it does nothing at all. *)
let test_kill_switch_off_by_default () =
  let k = Alerts.Kill_switch.create ~config:Config.Alerts.default in
  Alcotest.(check bool) "not armed" false (Alerts.Kill_switch.is_armed k);
  Alcotest.(check bool) "not halting" false (Alerts.Kill_switch.halt_new_orders k);
  Alcotest.(check bool)
    "and a breach does not trip it" true
    (Option.is_none (Alerts.Kill_switch.consider k ~at (raised 150.0)));
  Alcotest.(check bool) "still not halting" false (Alerts.Kill_switch.halt_new_orders k)

(* Armed, but only for the limits named. A switch that trips on everything is a
   switch someone will disable. *)
let test_kill_switch_trips_only_on_named_limits () =
  let k =
    Alerts.Kill_switch.create ~config:(alerts_config ~kill:true ~trips_on:[ "dd-cap" ] ())
  in
  Alcotest.(check bool) "armed" true (Alerts.Kill_switch.is_armed k);
  Alcotest.(check bool)
    "an unnamed limit does not trip it" true
    (Option.is_none (Alerts.Kill_switch.consider k ~at (raised 150.0)));
  Alcotest.(check bool) "still not halting" false (Alerts.Kill_switch.halt_new_orders k);
  (* Now the named one. *)
  let t = tracker () in
  let event = List.hd_exn (Alerts.Tracker.step t ~at [ evaluated ~limit:dd 0.5 ]) in
  match Alerts.Kill_switch.consider k ~at event with
  | None -> Alcotest.fail "the named limit should have tripped it"
  | Some tripped ->
      Alcotest.(check bool)
        "emits a kill-switch event" true
        (Alerts.Event.equal_kind tripped.Alerts.Event.kind
           Alerts.Event.Kill_switch_tripped);
      Alcotest.(check bool) "now halting" true (Alerts.Kill_switch.halt_new_orders k);
      Alcotest.(check bool)
        "and the message says what tripped it" true
        (String.is_substring (Alerts.Event.to_line tripped) ~substring:"dd-cap")

(* Tripping is one-way until someone resets it.

   A breaker that re-armed itself when the number came back under the line would
   silently un-halt a book while nobody was looking -- worse than having none,
   because the operator would believe a decision was still in force. *)
let test_kill_switch_latches () =
  let k =
    Alerts.Kill_switch.create
      ~config:(alerts_config ~kill:true ~trips_on:[ "aapl-cap" ] ())
  in
  ignore (Alerts.Kill_switch.consider k ~at (raised 150.0) : Alerts.Event.t option);
  Alcotest.(check bool) "halting" true (Alerts.Kill_switch.halt_new_orders k);
  (* Further breaches do not re-announce a halt already in force. *)
  Alcotest.(check bool)
    "no second announcement" true
    (Option.is_none (Alerts.Kill_switch.consider k ~at (raised 200.0)));
  Alcotest.(check bool) "still halting" true (Alerts.Kill_switch.halt_new_orders k);
  (* Only an explicit reset re-arms it. *)
  Alerts.Kill_switch.reset k;
  Alcotest.(check bool)
    "reset clears the halt" false
    (Alerts.Kill_switch.halt_new_orders k);
  Alcotest.(check bool) "and re-arms" true (Alerts.Kill_switch.is_armed k);
  (* A disarmed switch cannot be reset into an armed one. *)
  let disarmed = Alerts.Kill_switch.create ~config:Config.Alerts.default in
  Alerts.Kill_switch.reset disarmed;
  Alcotest.(check bool)
    "reset does not arm a disabled switch" false
    (Alerts.Kill_switch.is_armed disarmed)

(* ------------------------------------------------------------------------ *)
(* Configuration                                                             *)
(* ------------------------------------------------------------------------ *)

(* The default has to be inert, and this is the test that says so. If it ever
   changes, someone has moved the safety and should have to update this line. *)
let test_default_config_is_inert () =
  let d = Config.Alerts.default in
  Alcotest.(check bool) "alerting off" false d.Config.Alerts.enabled;
  Alcotest.(check bool) "kill switch off" false d.Config.Alerts.kill_switch_enabled;
  Alcotest.(check (list string))
    "and trips on nothing" [] d.Config.Alerts.kill_switch_trips_on;
  (* No sink that leaves the machine. *)
  Alcotest.(check bool)
    "no slack by default" false
    (List.mem d.Config.Alerts.sinks Config.Alerts.Sink.Slack
       ~equal:Config.Alerts.Sink.equal)

let test_config_validation () =
  let invalid name c =
    match Config.Alerts.validate c with
    | Ok () -> Alcotest.failf "%s should have been rejected" name
    | Error _ -> ()
  in
  invalid "clear_below above 1" { Config.Alerts.default with clear_below = 1.5 };
  invalid "clear_below of zero" { Config.Alerts.default with clear_below = 0.0 };
  (* An armed switch that trips on nothing is a configuration someone will
     misread as protection. *)
  invalid "kill switch armed with no triggers"
    { Config.Alerts.default with kill_switch_enabled = true };
  Alcotest.(check bool)
    "a sane config passes" true
    (Or_error.is_ok
       (Config.Alerts.validate (alerts_config ~kill:true ~trips_on:[ "dd-cap" ] ())))

(* attach returns None when disabled, so a caller cannot end up with a live
   notifier by forgetting a flag -- the type makes the off state unmistakable
   rather than merely likely. *)
let test_attach_is_off_by_default () =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
      ~limits:[ cap ] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      match Alerts.attach ~graph ~config:Config.Alerts.default with
      | Ok None -> ()
      | Ok (Some _) -> Alcotest.fail "the default config must not produce a notifier"
      | Error e -> Alcotest.failf "unexpected error: %s" (Error.to_string_hum e))
    ~finally:(fun () -> Graph.destroy graph)

(* The book file's alerts block is optional, and a file without one keeps
   working and keeps doing nothing. *)
let test_book_without_alerts_is_inert () =
  let sexp =
    {|((cash 1000.0)
       (positions (((symbol AAPL) (sector TECH) (qty 1.0))))
       (limits ()))|}
  in
  match Config.Book.of_string sexp with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok book ->
      Alcotest.(check bool)
        "alerting off" false book.Config.Book.alerts.Config.Alerts.enabled;
      Alcotest.(check bool)
        "kill switch off" false book.Config.Book.alerts.Config.Alerts.kill_switch_enabled

let test_book_with_alerts_parses () =
  let sexp =
    {|((cash 1000.0)
       (positions (((symbol AAPL) (sector TECH) (qty 1.0))))
       (limits ())
       (alerts ((enabled true)
                (sinks (Log Dry_run))
                (clear_below 0.9)
                (kill_switch_enabled true)
                (kill_switch_trips_on (dd-cap)))))|}
  in
  match Config.Book.of_string sexp with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok book ->
      let a = book.Config.Book.alerts in
      Alcotest.(check bool) "enabled" true a.Config.Alerts.enabled;
      Alcotest.(check int) "two sinks" 2 (List.length a.Config.Alerts.sinks);
      Alcotest.(check (float 1e-9)) "clear_below" 0.9 a.Config.Alerts.clear_below;
      Alcotest.(check (list string))
        "trips on" [ "dd-cap" ] a.Config.Alerts.kill_switch_trips_on

let suite =
  ( "alerts",
    [
      Alcotest.test_case "a persistent breach raises once" `Quick test_raises_once;
      Alcotest.test_case "hysteresis on the way back down" `Quick test_hysteresis;
      Alcotest.test_case "a value oscillating across the line does not flap" `Quick
        test_does_not_flap;
      Alcotest.test_case "LOST DATA NEVER CLEARS AN ALERT" `Quick
        test_lost_data_never_clears;
      Alcotest.test_case "no data on a quiet limit stays quiet" `Quick
        test_unevaluated_from_quiet_is_quiet;
      Alcotest.test_case "limits are tracked independently" `Quick
        test_limits_are_independent;
      Alcotest.test_case "units survive into the message" `Quick test_units_in_messages;
      Alcotest.test_case "slack payload shape" `Quick test_slack_payload_shape;
      Alcotest.test_case "the kill switch is off by default" `Quick
        test_kill_switch_off_by_default;
      Alcotest.test_case "it trips only on the limits named" `Quick
        test_kill_switch_trips_only_on_named_limits;
      Alcotest.test_case "and latches until explicitly reset" `Quick
        test_kill_switch_latches;
      Alcotest.test_case "the default config is inert" `Quick test_default_config_is_inert;
      Alcotest.test_case "config validation" `Quick test_config_validation;
      Alcotest.test_case "attach returns nothing when disabled" `Quick
        test_attach_is_off_by_default;
      Alcotest.test_case "a book with no alerts block is inert" `Quick
        test_book_without_alerts_is_inert;
      Alcotest.test_case "a book with an alerts block parses" `Quick
        test_book_with_alerts_parses;
    ] )
