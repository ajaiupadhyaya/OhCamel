(* Test registry.

   Every expected value in this project is hand-computed, not captured from a
   first run -- per the project conventions, a test that only asserts "it did
   not crash" is not a test.

   The suites, in the order they matter:

     link           the two libraries Phase 1 does not exercise, checked for
                    linkage only (see lib/toolchain_check.ml)
     risk_metrics   the pure risk functions, against hand-derived values
     graph          the Incremental dependency graph -- including the
                    recomputation tests that validate the architecture *)

open Core

let float_eq = Alcotest.float 1e-9

(* inv (2 * I3) = 0.5 * I3, whose trace is 0.5 + 0.5 + 0.5 = 1.5. *)
let test_owl_lapack () =
  Alcotest.check float_eq "trace (inv (2*I3))" 1.5
    (Ohcamel.Toolchain_check.owl_linalg_inv_trace ())

let test_async () =
  Alcotest.(check (option int))
    "Deferred.peek (return 42)" (Some 42)
    (Ohcamel.Toolchain_check.async_peek ())

let suites =
  [
    ( "link",
      [
        Alcotest.test_case "owl reaches LAPACK (inv)" `Quick test_owl_lapack;
        Alcotest.test_case "async deferred round trip" `Quick test_async;
      ] );
    Test_embedded_assets.suite;
    Test_risk_metrics.suite;
    Test_vol_estimators.suite;
    Test_options.suite;
    Test_options_graph.suite;
    Test_attribution.suite;
    Test_var_backtest.suite;
    Test_crisis_data.suite;
    Test_recompute_log.suite;
    Test_synthetic_book.suite;
    Test_scaling_probe.suite;
    Test_validation_report.suite;
    Test_options_walk.suite;
    Test_garch_study.suite;
    Test_stress.suite;
    Test_graph.suite;
    Test_feed.suite;
    Test_history_buffer.suite;
    Test_server.suite;
    Test_reports.suite;
    Test_alerts.suite;
    Test_properties.suite;
  ]

(* The registry counts itself against lib/verified.ml.

   The page says "N hermetic tests" in a block labelled with a date, and the
   README and docs/status.md say it too. A count that lives in three prose
   files and nowhere the compiler can see is a count that drifts the day a
   suite gains a case; this is the assertion that makes the drift a red test
   instead of a stale page. The +1 is this case, which Alcotest.run below
   registers alongside the list. *)
let test_the_count_is_the_dated_one () =
  let registered =
    1 + List.sum (module Int) suites ~f:(fun (_, cases) -> List.length cases)
  in
  Alcotest.(check int)
    (Printf.sprintf
       "lib/verified.ml says %d tests; the registry holds %d. If the registry is right, \
        set Verified.tests to %d and re-date it."
       Ohcamel.Verified.tests registered registered)
    Ohcamel.Verified.tests registered

let () =
  Alcotest.run "ohcamel"
    (suites
    @ [
        ( "verified",
          [
            Alcotest.test_case "the test count is the dated one" `Quick
              test_the_count_is_the_dated_one;
          ] );
      ])
