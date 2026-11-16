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

(* One breached evaluation raises, as it did before Task 1.3: the cases below
   test edge detection and hysteresis, which the held-breach rule does not
   change, so they run with the rule set to its one-evaluation form. The rule
   itself is tested with the engine's defaults further down. *)
let tracker ?(clear_below = 0.95) ?(raise_after_n = 1) ?(raise_after_s = 0.0) () =
  Alerts.Tracker.create ~clear_below ~raise_after_n ~raise_after_s

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
(* A held breach, not one print (Task 1.3)                                   *)
(* ------------------------------------------------------------------------ *)

(* The engine's defaults: three consecutive breached evaluations, the first
   at least thirty seconds before the one that raises. Both, not either. *)
let held () =
  let d = Config.Alerts.default in
  tracker ~raise_after_n:d.Config.Alerts.raise_after_n
    ~raise_after_s:d.Config.Alerts.raise_after_s ()

let at_s s = Time.add Time.epoch (Time.Span.of_sec s)

(* Steps [t] through [(seconds, evaluation)] and returns the events of each. *)
let run t steps =
  List.map steps ~f:(fun (s, evaluation) ->
      Alerts.Tracker.step t ~at:(at_s s) [ evaluation ])

let counts events = List.map events ~f:List.length

let test_defaults_are_three_over_thirty () =
  let d = Config.Alerts.default in
  Alcotest.(check int) "three evaluations" 3 d.Config.Alerts.raise_after_n;
  Alcotest.(check (float 0.0)) "thirty seconds" 30.0 d.Config.Alerts.raise_after_s

let test_one_breached_evaluation_raises_nothing () =
  let t = held () in
  Alcotest.(check (list int))
    "one bad print: silent" [ 0 ]
    (counts (run t [ (0.0, evaluated 150.0) ]));
  Alcotest.(check bool) "and not firing" false (Alerts.Tracker.firing t "aapl-cap")

let test_three_within_ten_seconds_raise_nothing () =
  let t = held () in
  Alcotest.(check (list int))
    "three breached evaluations over 10 s: silent" [ 0; 0; 0 ]
    (counts
       (run t [ (0.0, evaluated 150.0); (5.0, evaluated 151.0); (10.0, evaluated 152.0) ]));
  Alcotest.(check bool) "not firing" false (Alerts.Tracker.firing t "aapl-cap")

let test_three_spanning_thirty_one_seconds_raise_once () =
  let t = held () in
  let events =
    run t
      [
        (0.0, evaluated 150.0);
        (15.0, evaluated 151.0);
        (31.0, evaluated 152.0);
        (40.0, evaluated 153.0);
        (90.0, evaluated 154.0);
      ]
  in
  Alcotest.(check (list int))
    "raised at the third, once" [ 0; 0; 1; 0; 0 ] (counts events);
  Alcotest.(check bool)
    "a Raised event" true
    (match List.nth_exn events 2 with
    | [ e ] -> Alerts.Event.equal_kind e.Alerts.Event.kind Alerts.Event.Raised
    | _ -> false);
  Alcotest.(check bool) "firing" true (Alerts.Tracker.firing t "aapl-cap")

(* Both conditions: thirty seconds held over two evaluations is not three. *)
let test_time_alone_is_not_enough () =
  let t = held () in
  Alcotest.(check (list int))
    "two evaluations 40 s apart: silent" [ 0; 0 ]
    (counts (run t [ (0.0, evaluated 150.0); (40.0, evaluated 151.0) ]));
  Alcotest.(check (list int))
    "the third raises, the first still the start" [ 1 ]
    (counts (run t [ (41.0, evaluated 152.0) ]))

let test_a_clear_evaluation_restarts_the_count () =
  let t = held () in
  Alcotest.(check (list int))
    "breach, clear, then three breaches over 19 s: silent" [ 0; 0; 0; 0; 0 ]
    (counts
       (run t
          [
            (0.0, evaluated 150.0);
            (15.0, evaluated 90.0);
            (31.0, evaluated 150.0);
            (40.0, evaluated 150.0);
            (50.0, evaluated 150.0);
          ]));
  Alcotest.(check (list int))
    "thirty-one seconds after the restart it raises" [ 1 ]
    (counts (run t [ (62.0, evaluated 150.0) ]))

(* "I cannot tell" neither advances the count nor resets it: an unevaluable
   round is not a breached evaluation, and a flapping input must not hold a
   real breach off forever. *)
let test_an_unevaluable_round_neither_counts_nor_resets () =
  let t = held () in
  Alcotest.(check (list int))
    "breach, no data, breach at 31 s: two evaluations, silent" [ 0; 0; 0 ]
    (counts
       (run t [ (0.0, evaluated 150.0); (15.0, unevaluated ()); (31.0, evaluated 150.0) ]));
  Alcotest.(check (list int))
    "the third breached evaluation raises, timed from the first" [ 1 ]
    (counts (run t [ (32.0, evaluated 150.0) ]))

(* n <= 1 and no time: exactly the old rule, one evaluation raises. n = 0 is
   refused by the config, and a tracker given one treats it as 1. *)
let test_one_evaluation_and_no_time_is_the_old_rule () =
  List.iter [ 0; 1 ] ~f:(fun n ->
      let t = tracker ~raise_after_n:n ~raise_after_s:0.0 () in
      Alcotest.(check (list int))
        (sprintf "n = %d: the first breach raises" n)
        [ 1; 0 ]
        (counts (run t [ (0.0, evaluated 150.0); (1.0, evaluated 151.0) ])))

(* The recheck re-reads the graph's standing evaluation on the clock: it raises
   a breach that has met its count and is waiting only on time, and it never
   counts as an evaluation itself -- so a single print the market never moved
   off is still one evaluation. *)
let test_recheck_raises_on_time_and_never_counts () =
  let t = held () in
  ignore
    (run t [ (0.0, evaluated 150.0); (5.0, evaluated 151.0); (10.0, evaluated 152.0) ]);
  Alcotest.(check int)
    "recheck at 20 s: not yet thirty" 0
    (List.length (Alerts.Tracker.recheck t ~at:(at_s 20.0) [ evaluated 152.0 ]));
  Alcotest.(check int)
    "recheck at 31 s: raised" 1
    (List.length (Alerts.Tracker.recheck t ~at:(at_s 31.0) [ evaluated 152.0 ]));
  let single = held () in
  ignore (run single [ (0.0, evaluated 150.0) ]);
  List.iter [ 31.0; 62.0; 93.0 ] ~f:(fun s ->
      Alcotest.(check int)
        (sprintf "one print, recheck at %.0f s: silent" s)
        0
        (List.length (Alerts.Tracker.recheck single ~at:(at_s s) [ evaluated 150.0 ])));
  Alcotest.(check bool) "never firing" false (Alerts.Tracker.firing single "aapl-cap")

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

(* One breached evaluation raises here unless a test says otherwise: these
   cases are about the switch and the notifier's wiring, and each drives one
   stabilize to a breach. *)
let alerts_config ?(enabled = true) ?(kill = false) ?(trips_on = []) ?(raise_after_n = 1)
    ?(raise_after_s = 0.0) () =
  {
    Config.Alerts.enabled;
    sinks = [ Config.Alerts.Sink.Dry_run ];
    clear_below = 0.95;
    kill_switch_enabled = kill;
    kill_switch_trips_on = trips_on;
    raise_after_n;
    raise_after_s;
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
  invalid "raise_after_n of zero" { Config.Alerts.default with raise_after_n = 0 };
  invalid "negative raise_after_n" { Config.Alerts.default with raise_after_n = -2 };
  invalid "negative raise_after_s" { Config.Alerts.default with raise_after_s = -1.0 };
  invalid "nan raise_after_s" { Config.Alerts.default with raise_after_s = Float.nan };
  invalid "infinite raise_after_s"
    { Config.Alerts.default with raise_after_s = Float.infinity };
  Alcotest.(check bool)
    "one evaluation and no time is a sane config" true
    (Or_error.is_ok
       (Config.Alerts.validate
          { Config.Alerts.default with raise_after_n = 1; raise_after_s = 0.0 }));
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
      match Alerts.attach ~graph ~config:Config.Alerts.default () with
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
        "trips on" [ "dd-cap" ] a.Config.Alerts.kill_switch_trips_on;
      Alcotest.(check int)
        "an alerts block without the rule takes its default count" 3
        a.Config.Alerts.raise_after_n;
      Alcotest.(check (float 0.0))
        "and its default time" 30.0 a.Config.Alerts.raise_after_s

let test_book_with_raise_after_parses () =
  let sexp =
    {|((cash 1000.0)
       (positions (((symbol AAPL) (sector TECH) (qty 1.0))))
       (limits ())
       (alerts ((enabled true) (sinks (Log)) (clear_below 0.9)
                (kill_switch_enabled false) (kill_switch_trips_on ())
                (raise_after_n 5) (raise_after_s 60.0))))|}
  in
  match Config.Book.of_string sexp with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok book ->
      let a = book.Config.Book.alerts in
      Alcotest.(check int) "raise_after_n" 5 a.Config.Alerts.raise_after_n;
      Alcotest.(check (float 0.0)) "raise_after_s" 60.0 a.Config.Alerts.raise_after_s

(* The notifier, the switch and its trip handler on the engine's defaults,
   with the clock injected: one breached stabilize trips nothing; the breach
   held over three evaluations spanning 31 s trips it once, and [on_trip]
   hears it once. *)
let test_the_switch_trips_on_a_held_breach () =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
      ~limits:[ cap ] ~confidence:0.95 ~return_window:10 ()
  in
  let clock = ref 0.0 in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let d = Config.Alerts.default in
      match
        Alerts.attach
          ~now:(fun () -> at_s !clock)
          ~graph
          ~config:
            (alerts_config ~kill:true ~trips_on:[ "aapl-cap" ]
               ~raise_after_n:d.Config.Alerts.raise_after_n
               ~raise_after_s:d.Config.Alerts.raise_after_s ())
          ()
      with
      | Error e -> Alcotest.failf "attach: %s" (Error.to_string_hum e)
      | Ok None -> Alcotest.fail "an enabled config must produce a notifier"
      | Ok (Some a) ->
          let trips = ref 0 in
          Alerts.on_trip a ~f:(fun _ -> Int.incr trips);
          Graph.set_qty graph aapl (Qty.of_float 1.0);
          let mark s price =
            clock := s;
            Graph.set_price graph aapl (Price.of_float price);
            Graph.stabilize graph
          in
          mark 0.0 150.0;
          Alcotest.(check (pair bool int))
            "one breached print: not tripped" (false, 0)
            (Alerts.halted a, !trips);
          mark 10.0 151.0;
          mark 20.0 152.0;
          Alcotest.(check (pair bool int))
            "three in 20 s: not tripped" (false, 0)
            (Alerts.halted a, !trips);
          clock := 31.0;
          Alerts.recheck a ~graph;
          Alcotest.(check (pair bool int))
            "the recheck at 31 s trips it, once" (true, 1)
            (Alerts.halted a, !trips);
          mark 40.0 153.0;
          Alcotest.(check int) "and only once" 1 !trips)

(* What the wire could not say before.

   /api/snapshot has reported `enabled` and a kill-switch word since Phase 4,
   and nothing else -- so an operator could see that something had tripped and
   not which limits were still over the line, nor when. Worse, the tracker's
   hysteresis means "breached" and "still firing" are different states: a limit
   back under its threshold but above clear_below is not breached and is still
   firing, and only the tracker knows. These three accessors put that state on
   the wire. *)
let test_firing_and_tripped_at_are_readable () =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
      ~limits:[ cap ] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      match
        Alerts.attach ~graph
          ~config:(alerts_config ~kill:true ~trips_on:[ "aapl-cap" ] ())
          ()
      with
      | Error e -> Alcotest.failf "attach: %s" (Error.to_string_hum e)
      | Ok None -> Alcotest.fail "an enabled config must produce a notifier"
      | Ok (Some a) ->
          (* Nothing has been evaluated yet. Empty, and armed but not tripped. *)
          Graph.stabilize graph;
          Alcotest.(check (list string)) "nothing firing yet" [] (Alerts.firing_limits a);
          Alcotest.(check bool)
            "and nothing has tripped" true
            (Option.is_none (Alerts.tripped_at a));
          (* aapl-cap is a 100 gross-notional cap. 150 x 1 = 150, so
             utilisation is 1.5 and the limit is breached. *)
          Graph.set_price graph aapl (Price.of_float 150.0);
          Graph.set_qty graph aapl (Qty.of_float 1.0);
          Graph.stabilize graph;
          Alcotest.(check (list string))
            "the breached limit is firing" [ "aapl-cap" ] (Alerts.firing_limits a);
          Alcotest.(check bool)
            "and the switch it trips on has a time" true
            (Option.is_some (Alerts.tripped_at a));
          (* Back under the line but INSIDE the hysteresis band: 96 x 1 = 96,
             utilisation 0.96, which is above clear_below = 0.95. Not breached,
             still firing -- which is exactly the distinction the page needs
             and the one a `breached` list cannot express. *)
          Graph.set_price graph aapl (Price.of_float 96.0);
          Graph.stabilize graph;
          Alcotest.(check (list string))
            "inside the band it is still firing" [ "aapl-cap" ] (Alerts.firing_limits a);
          (* The config travels too, so the page can print the hysteresis it is
             looking at rather than assuming the default. *)
          Alcotest.(check (float 1e-9))
            "clear_below is readable" 0.95 (Alerts.config a).Config.Alerts.clear_below;
          Alcotest.(check (list string))
            "and so is what the switch trips on" [ "aapl-cap" ]
            (Alerts.config a).Config.Alerts.kill_switch_trips_on)
    ~finally:(fun () -> Graph.destroy graph)

(* The live engine starts on the BOOK FILE's quantities and replaces them with
   the account's at its first sync. On 2026-10-27 the live book's file still
   held its demo positions -- about $500k gross -- and the account $53k: the
   switch, armed for the first time, tripped on book-cap in the second the
   engine started, on a book nobody held, and latched. [hold] holds every
   evaluation until the caller says the graph is the book it means; [release]
   is then one pass over the graph as it stands.

   Here aapl-cap is 100 and the "file" holds 150 of AAPL: breached, and held,
   so nothing fires and nothing trips. Then the "account" lands at 50, and
   [release] finds nothing. A second case: the account itself is in breach --
   200 -- and [release] raises it and trips the switch, which the observer
   alone would never do, because the breach set did not change when the
   account landed. *)
let test_nothing_is_evaluated_until_the_book_is_ready () =
  let with_attached ~f =
    let graph =
      Graph.create
        ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
        ~limits:[ cap ] ~confidence:0.95 ~return_window:10 ()
    in
    Exn.protect
      ~finally:(fun () -> Graph.destroy graph)
      ~f:(fun () ->
        match
          Alerts.attach ~graph
            ~config:(alerts_config ~kill:true ~trips_on:[ "aapl-cap" ] ())
            ()
        with
        | Error e -> Alcotest.failf "attach: %s" (Error.to_string_hum e)
        | Ok None -> Alcotest.fail "an enabled config must produce a notifier"
        | Ok (Some a) ->
            Alerts.hold a;
            f graph a)
  in
  with_attached ~f:(fun graph a ->
      Graph.set_price graph aapl (Price.of_float 1.0);
      Graph.set_qty graph aapl (Qty.of_float 150.0);
      Graph.stabilize graph;
      Alcotest.(check (pair (list string) bool))
        "the file's breach: nothing firing, nothing tripped" ([], false)
        (Alerts.firing_limits a, Option.is_some (Alerts.tripped_at a));
      Graph.set_qty graph aapl (Qty.of_float 50.0);
      Graph.stabilize graph;
      Alerts.release a ~graph;
      Alcotest.(check (pair (list string) bool))
        "the account's book, inside the cap: still nothing" ([], false)
        (Alerts.firing_limits a, Option.is_some (Alerts.tripped_at a)));
  with_attached ~f:(fun graph a ->
      Graph.set_price graph aapl (Price.of_float 1.0);
      Graph.set_qty graph aapl (Qty.of_float 150.0);
      Graph.stabilize graph;
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      Graph.stabilize graph;
      Alerts.release a ~graph;
      Alcotest.(check (pair (list string) bool))
        "the account's own breach: firing, and tripped" ([ "aapl-cap" ], true)
        (Alerts.firing_limits a, Option.is_some (Alerts.tripped_at a)))

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
      Alcotest.test_case "the held-breach defaults are three over thirty" `Quick
        test_defaults_are_three_over_thirty;
      Alcotest.test_case "one breached evaluation raises nothing" `Quick
        test_one_breached_evaluation_raises_nothing;
      Alcotest.test_case "three breached evaluations within 10 s raise nothing" `Quick
        test_three_within_ten_seconds_raise_nothing;
      Alcotest.test_case "three spanning 31 s raise once" `Quick
        test_three_spanning_thirty_one_seconds_raise_once;
      Alcotest.test_case "time held over two evaluations is not enough" `Quick
        test_time_alone_is_not_enough;
      Alcotest.test_case "a clear evaluation restarts the count" `Quick
        test_a_clear_evaluation_restarts_the_count;
      Alcotest.test_case "an unevaluable round neither counts nor resets" `Quick
        test_an_unevaluable_round_neither_counts_nor_resets;
      Alcotest.test_case "one evaluation and no time is the old rule" `Quick
        test_one_evaluation_and_no_time_is_the_old_rule;
      Alcotest.test_case "the recheck raises on time and never counts" `Quick
        test_recheck_raises_on_time_and_never_counts;
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
      Alcotest.test_case "a book with the held-breach rule parses" `Quick
        test_book_with_raise_after_parses;
      Alcotest.test_case "the switch trips on a held breach, not one print" `Quick
        test_the_switch_trips_on_a_held_breach;
      Alcotest.test_case "firing limits and the trip time are readable" `Quick
        test_firing_and_tripped_at_are_readable;
      Alcotest.test_case "nothing is evaluated until the book is ready" `Quick
        test_nothing_is_evaluated_until_the_book_is_ready;
    ] )
