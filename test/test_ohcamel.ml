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

let float_eq = Alcotest.float 1e-9

(* inv (2 * I3) = 0.5 * I3, whose trace is 0.5 + 0.5 + 0.5 = 1.5. *)
let test_owl_lapack () =
  Alcotest.check float_eq "trace (inv (2*I3))" 1.5
    (Ohcamel.Toolchain_check.owl_linalg_inv_trace ())

let test_async () =
  Alcotest.(check (option int))
    "Deferred.peek (return 42)" (Some 42)
    (Ohcamel.Toolchain_check.async_peek ())

let () =
  Alcotest.run "ohcamel"
    [
      ( "link",
        [
          Alcotest.test_case "owl reaches LAPACK (inv)" `Quick test_owl_lapack;
          Alcotest.test_case "async deferred round trip" `Quick test_async;
        ] );
      Test_risk_metrics.suite;
      Test_vol_estimators.suite;
      Test_options.suite;
      Test_options_graph.suite;
      Test_attribution.suite;
      Test_var_backtest.suite;
      Test_crisis_data.suite;
      Test_stress.suite;
      Test_graph.suite;
      Test_feed.suite;
      Test_server.suite;
      Test_alerts.suite;
      Test_properties.suite;
    ]
