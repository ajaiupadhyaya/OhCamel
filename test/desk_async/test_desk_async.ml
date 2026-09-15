(* The desk with the scheduler running, and never the wall clock.

   A case that needs time to pass creates a clock of its own (Time_source.create)
   and advances it, so a bound of thirty seconds is crossed in no time at all.
   A case sees only its own timers: moving its clock fires nothing an earlier
   case left behind, and nothing here depends on which case ran first. *)

open Core
open Async
module D = Ohcamel_desk

let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let case name test =
  Alcotest.test_case name `Quick (fun () -> Thread_safe.block_on_async_exn test)

(* The transport's bound (A1's final review, I3), at the production span. A
   request that never answers ends as an error naming what was asked and the
   bound. The request is told it has been abandoned, which is what the
   transport closes a connection on when it can: one still connecting, or one
   that has answered. One nanosecond short of 30 s nothing has fired; at 30 s
   it has. An answer inside the bound is that answer. *)
let test_a_request_that_never_answers_is_an_error_at_its_bound () =
  let clock = Time_source.create ~now:t0 () in
  let time_source = Time_source.read_only clock in
  let abandoned = ref None in
  let never =
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout
      ~what:"GET /v2/account" (fun ~abandon ->
        abandoned := Some abandon;
        Deferred.never ())
  in
  let%bind answered =
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout
      ~what:"GET /v2/clock" (fun ~abandon:_ -> return (Ok 42))
  in
  Alcotest.(check (option int))
    "an answer inside the bound is that answer, the 42 given" (Some 42)
    (Result.ok answered);
  let settle () = Scheduler.yield_until_no_jobs_remain () in
  (* 14:00:00 + 30 s - 1 ns = 14:00:29.999999999 *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(D.Alpaca_paper.request_timeout - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check bool)
    "nothing at 1 ns short of 30 s" false
    (Deferred.is_determined never);
  (* 14:00:00 + 30 s *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 D.Alpaca_paper.request_timeout)
  in
  let%map () = settle () in
  (match Deferred.peek never with
  | Some (Error e) ->
      (* the bound, 30 s, as Time_ns.Span.to_string_hum prints it *)
      Alcotest.(check string)
        "the request and the bound, and nothing else"
        "alpaca_paper: GET /v2/account did not answer within 30s" (Error.to_string_hum e)
  | Some (Ok _) -> Alcotest.fail "a Deferred that never fills answered"
  | None -> Alcotest.fail "the bound had not fired at 30 s on the test's clock");
  Alcotest.(check bool)
    "the request was told it is abandoned" true
    (Option.value_map !abandoned ~default:false ~f:Deferred.is_determined)

let suites =
  [
    ( "transport",
      [
        case "a request that never answers is an error at its bound"
          test_a_request_that_never_answers_is_an_error_at_its_bound;
      ] );
  ]

(* The registry counts itself against lib/verified.ml's [scheduler_tests], as
   test_ohcamel.ml counts itself against [tests]. The +1 is this case. It needs
   no scheduler. *)
let test_the_count_is_the_dated_one () =
  let registered =
    1 + List.sum (module Int) suites ~f:(fun (_, cases) -> List.length cases)
  in
  Alcotest.(check int)
    (sprintf "lib/verified.ml says %d scheduler tests; the registry holds %d"
       Ohcamel.Verified.scheduler_tests registered)
    Ohcamel.Verified.scheduler_tests registered

let () =
  Alcotest.run "ohcamel desk, with the scheduler"
    (suites
    @ [
        ( "verified",
          [
            Alcotest.test_case "the scheduler suite's count is the dated one" `Quick
              test_the_count_is_the_dated_one;
          ] );
      ])
