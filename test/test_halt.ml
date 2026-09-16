(* The switch the desk obeys, with its source in the test's hands.

   The fake source is three refs: which limit tripped it and when, which
   limits are firing, and how many resets reached the kernel. The last case
   uses the kernel's real alerts, to pin the one line lib/alerts.ml gains. *)

open Core
module Halt = Ohcamel_desk.Halt

let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"
let at s = Time_ns.add t0 (Time_ns.Span.of_sec s)

let fake () =
  let tripped = ref None and firing = ref [] and resets = ref 0 in
  let source =
    {
      Halt.Source.tripped = (fun () -> !tripped);
      firing = (fun () -> !firing);
      reset =
        (fun () ->
          incr resets;
          tripped := None);
    }
  in
  (source, tripped, firing, resets)

let state t = Halt.State.name (Halt.state t)

let says t words =
  String.is_substring (Option.value (Halt.reason t) ~default:"") ~substring:words

let test_a_limit_trips_it_and_the_reason_names_the_limit () =
  let source, tripped, _, _ = fake () in
  let t = Halt.create source in
  Alcotest.(check string) "clear" "clear" (state t);
  Alcotest.(check (option string)) "no reason" None (Halt.reason t);
  tripped := Some ("nvda-cap", t0);
  Alcotest.(check string) "tripped" "tripped" (state t);
  Alcotest.(check bool) "the reason names the limit" true (says t "tripped by nvda-cap")

let test_a_hand_outranks_a_limit_and_a_reset_lifts_both () =
  let source, tripped, _, resets = fake () in
  let t = Halt.create source in
  tripped := Some ("nvda-cap", t0);
  Halt.halt t ~why:"the feed is lying" ~at:(at 5.0);
  Alcotest.(check string) "halted, not tripped" "halted" (state t);
  Halt.halt t ~why:"a second press" ~at:(at 9.0);
  Alcotest.(check bool) "the first press's words stand" true (says t "the feed is lying");
  Halt.reset t;
  Alcotest.(check string) "clear" "clear" (state t);
  Alcotest.(check int) "and the kernel's switch was reset too" 1 !resets

let test_the_demo_resets_ninety_seconds_after_the_limit_clears () =
  let source, tripped, firing, _ = fake () in
  let t = Halt.create ~auto_reset_after:(Time_ns.Span.of_sec 90.0) source in
  tripped := Some ("nvda-cap", t0);
  firing := [ "nvda-cap" ];
  Alcotest.(check bool)
    "still firing at 100 s: no reset" false
    (Halt.tick t ~now:(at 100.0));
  firing := [];
  Alcotest.(check bool) "clear from 110 s" false (Halt.tick t ~now:(at 110.0));
  Alcotest.(check bool) "199 s is 89 s clear" false (Halt.tick t ~now:(at 199.0));
  firing := [ "nvda-cap" ];
  Alcotest.(check bool)
    "firing again at 199.5 s: the clock restarts" false
    (Halt.tick t ~now:(at 199.5));
  firing := [];
  Alcotest.(check bool) "clear from 200 s" false (Halt.tick t ~now:(at 200.0));
  Alcotest.(check bool) "289 s is 89 s clear" false (Halt.tick t ~now:(at 289.0));
  Alcotest.(check bool) "290 s is 90 s clear: reset" true (Halt.tick t ~now:(at 290.0));
  Alcotest.(check string) "clear" "clear" (state t)

let test_nothing_resets_by_time_on_the_live_host_or_after_a_hand () =
  let source, tripped, _, _ = fake () in
  let live = Halt.create source in
  tripped := Some ("nvda-cap", t0);
  Alcotest.(check bool)
    "live: clear for an hour, no reset" false
    (Halt.tick live ~now:(at 3600.0));
  Alcotest.(check bool) "live: and a minute later" false (Halt.tick live ~now:(at 3660.0));
  Alcotest.(check string) "still tripped" "tripped" (state live);
  let source, _, _, _ = fake () in
  let demo = Halt.create ~auto_reset_after:(Time_ns.Span.of_sec 90.0) source in
  Halt.halt demo ~why:"by hand" ~at:t0;
  ignore (Halt.tick demo ~now:(at 1.0) : bool);
  Alcotest.(check bool)
    "demo: a hand's halt an hour later" false
    (Halt.tick demo ~now:(at 3600.0));
  Alcotest.(check string) "still halted" "halted" (state demo)

(* The kernel's half. aapl-cap is 100 dollars; 1 share at 150 is over it, so
   the first stabilize trips the switch and on_trip is called once; 2 shares
   keep it tripped and call nothing. Source.of_alerts reads that switch and
   resets it. *)
let test_alerts_call_on_trip_once_and_the_desk_reads_the_switch () =
  let open Ohcamel.Types in
  let module Graph = Ohcamel.Graph in
  let module Config = Ohcamel.Config in
  let aapl = Symbol.of_string "AAPL" in
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:
        [
          {
            Limit.name = "aapl-cap";
            scope = Limit.Instrument aapl;
            kind = Limit.Gross_notional (Notional.of_float 100.0);
          };
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      (* No sinks: nothing is printed, and nothing would be delivered without
         the scheduler anyway. *)
      let config =
        {
          Config.Alerts.default with
          Config.Alerts.enabled = true;
          sinks = [];
          kill_switch_enabled = true;
          kill_switch_trips_on = [ "aapl-cap" ];
        }
      in
      let alerts =
        Option.value_exn (Or_error.ok_exn (Ohcamel.Alerts.attach ~graph ~config))
      in
      let calls = ref 0 in
      Ohcamel.Alerts.on_trip alerts ~f:(fun _ -> incr calls);
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 1.0);
      Graph.stabilize graph;
      Alcotest.(check int) "once, on the trip" 1 !calls;
      Graph.set_qty graph aapl (Qty.of_float 2.0);
      Graph.stabilize graph;
      Alcotest.(check int) "not again while it stays tripped" 1 !calls;
      let halt = Halt.create (Halt.Source.of_alerts alerts) in
      Alcotest.(check string) "the desk sees the trip" "tripped" (state halt);
      Halt.reset halt;
      Alcotest.(check string) "and resets the kernel's switch" "clear" (state halt))

let suite =
  ( "halt",
    [
      Alcotest.test_case "a limit trips it and the reason names the limit" `Quick
        test_a_limit_trips_it_and_the_reason_names_the_limit;
      Alcotest.test_case "a hand outranks a limit, and a reset lifts both" `Quick
        test_a_hand_outranks_a_limit_and_a_reset_lifts_both;
      Alcotest.test_case "the demo resets ninety seconds after the limit clears" `Quick
        test_the_demo_resets_ninety_seconds_after_the_limit_clears;
      Alcotest.test_case "nothing resets by time on the live host, or after a hand" `Quick
        test_nothing_resets_by_time_on_the_live_host_or_after_a_hand;
      Alcotest.test_case "alerts call on_trip once, and the desk reads the switch" `Quick
        test_alerts_call_on_trip_once_and_the_desk_reads_the_switch;
    ] )
