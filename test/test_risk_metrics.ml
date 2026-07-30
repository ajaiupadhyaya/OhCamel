(* Unit tests for the pure functions in risk_metrics.ml.

   Per the project conventions, every expected value below is hand-computed and
   the derivation is written next to it. A test whose expected value was pasted
   from the function's own first run proves only that the code is deterministic
   -- it locks the bug in and then guards it.

   The base series is deliberately tiny and symmetric so quantiles can be read
   off by eye:

     R = [-0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05]

   ten observations, already sorted ascending, so the k-th worst loss is just
   R[k-1]. *)

open Core
module RM = Ohcamel.Risk_metrics

let feq = Alcotest.float 1e-12
let returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]

(* Same multiset, shuffled, to prove the functions sort rather than assume. *)
let returns_shuffled =
  [| 0.03; -0.02; 0.05; -0.05; 0.01; -0.04; 0.04; -0.01; 0.02; -0.03 |]

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, but it returned" name

(* n = 10.
   c = 0.95 -> tail is ceil(0.5)  = 1 obs  -> VaR = -R[0] = 0.05
   c = 0.80 -> tail is ceil(2.0)  = 2 obs  -> VaR = -R[1] = 0.04
   c = 0.70 -> tail is ceil(3.0)  = 3 obs  -> VaR = -R[2] = 0.03 *)
let test_historical_var () =
  Alcotest.check feq "95%" 0.05 (RM.historical_var ~returns ~confidence:0.95);
  Alcotest.check feq "80%" 0.04 (RM.historical_var ~returns ~confidence:0.80);
  Alcotest.check feq "70%" 0.03 (RM.historical_var ~returns ~confidence:0.70)

(* The 70% case is the regression test for the tail_count epsilon:
   (1.0 -. 0.70) *. 10.0 is 3.0000000000000004 in binary floating point, so a
   naive ceiling widens the tail to 4 observations and returns 0.02 here. *)
let test_historical_var_float_rank_artefact () =
  Alcotest.check feq "70% must use a 3-observation tail, not 4" 0.03
    (RM.historical_var ~returns ~confidence:0.70)

let test_historical_var_is_order_independent () =
  Alcotest.check feq "shuffled input gives the same answer"
    (RM.historical_var ~returns ~confidence:0.80)
    (RM.historical_var ~returns:returns_shuffled ~confidence:0.80)

(* c = 0.95 -> 1 obs  -> ES = -mean(-0.05)               = 0.05
   c = 0.80 -> 2 obs  -> ES = -mean(-0.05, -0.04)        = 0.045
   c = 0.70 -> 3 obs  -> ES = -mean(-0.05, -0.04, -0.03) = 0.04 *)
let test_expected_shortfall () =
  Alcotest.check feq "95%" 0.05 (RM.expected_shortfall ~returns ~confidence:0.95);
  Alcotest.check feq "80%" 0.045 (RM.expected_shortfall ~returns ~confidence:0.80);
  Alcotest.check feq "70%" 0.04 (RM.expected_shortfall ~returns ~confidence:0.70)

(* ES >= VaR is a mathematical invariant, not a coincidence of this series: the
   mean of the tail cannot be less severe than the tail's least severe point.
   Equality holds only when the tail is a single observation. *)
let test_es_dominates_var () =
  List.iter [ 0.70; 0.80; 0.90; 0.95; 0.99 ] ~f:(fun confidence ->
      let var = RM.historical_var ~returns ~confidence in
      let es = RM.expected_shortfall ~returns ~confidence in
      if Float.( < ) es (var -. 1e-12) then
        Alcotest.failf "ES (%f) < VaR (%f) at confidence %f" es var confidence)

(* z(0.05) = -1.6448536269514722 and z(0.025) = -1.959963984540054 are the
   standard normal quantiles from any table; z(0.5) = 0 by symmetry. *)
let test_normal_ppf () =
  Alcotest.check feq "median" 0.0 (RM.normal_ppf ~p:0.5);
  Alcotest.check (Alcotest.float 1e-9) "5%" (-1.6448536269514722) (RM.normal_ppf ~p:0.05);
  Alcotest.check (Alcotest.float 1e-9) "2.5%" (-1.959963984540054)
    (RM.normal_ppf ~p:0.025)

(* VaR = -(mu + z*sigma).
   mu = 0,    sigma = 0.02, c = 0.95 -> 1.6448536269514722 * 0.02 = 0.0328970725390294
   mu = 0.01, sigma = 0.02, c = 0.95 -> 0.0328970725390294 - 0.01 = 0.0228970725390294 *)
let test_parametric_var () =
  Alcotest.check (Alcotest.float 1e-12) "zero mean" 0.032897072539029444
    (RM.parametric_var ~mean:0.0 ~stddev:0.02 ~confidence:0.95);
  Alcotest.check (Alcotest.float 1e-12) "positive drift reduces VaR" 0.022897072539029444
    (RM.parametric_var ~mean:0.01 ~stddev:0.02 ~confidence:0.95)

(* A riskless book has zero VaR regardless of confidence. *)
let test_parametric_var_zero_vol () =
  Alcotest.check feq "no vol, no VaR" 0.0
    (RM.parametric_var ~mean:0.0 ~stddev:0.0 ~confidence:0.99)

(* Population moments over [1;2;3;4]: mean 2.5, deviations -1.5 -0.5 0.5 1.5,
   squares 2.25 0.25 0.25 2.25, sum 5.0, / 4 = 1.25. *)
let test_variance () =
  Alcotest.check feq "var [1;2;3;4]" 1.25 (RM.variance [| 1.; 2.; 3.; 4. |])

(* cov([1;2;3;4], [2;4;6;8]): means 2.5 and 5.0; deviation products
   (-1.5)(-3) + (-0.5)(-1) + (0.5)(1) + (1.5)(3) = 4.5 + 0.5 + 0.5 + 4.5 = 10,
   / 4 = 2.5. *)
let test_covariance () =
  Alcotest.check feq "cov" 2.5 (RM.covariance [| 1.; 2.; 3.; 4. |] [| 2.; 4.; 6.; 8. |])

(* beta = cov / var(factor).
   asset = 2 * factor exactly  -> 2.5 / 1.25 = 2.0
   asset = factor reversed     -> -2.5 / 1.25 = -1.0 *)
let test_beta () =
  Alcotest.check feq "asset = 2x factor" 2.0
    (RM.beta ~asset:[| 2.; 4.; 6.; 8. |] ~factor:[| 1.; 2.; 3.; 4. |]);
  Alcotest.check feq "perfectly inverted" (-1.0)
    (RM.beta ~asset:[| 4.; 3.; 2.; 1. |] ~factor:[| 1.; 2.; 3.; 4. |])

(* "Constant" has to mean constant-up-to-float-noise, not variance = 0.0.

   Ten copies of 0.0425 have a true variance of zero, but 0.0425 has no exact
   binary representation, so the computed mean is off by a rounding residue and
   the computed variance lands around 1e-33 rather than on zero.

   This is the case that motivated the function. Before it existed, [beta]
   tested for exact zero, let this series through, and divided one noise term by
   another -- returning -0.3. Finite, plausible, and pure float error. Read on a
   dashboard it asserts the book is inversely exposed to rates. *)
let test_effectively_constant () =
  let repeated v n = Array.create ~len:n v in
  Alcotest.(check bool)
    "0.0425 repeated: variance is ~1e-33, not 0.0, but the series is constant" true
    (RM.is_effectively_constant (repeated 0.0425 10));
  Alcotest.(check bool)
    "exactly representable repeats" true
    (RM.is_effectively_constant (repeated 0.5 10));
  Alcotest.(check bool)
    "all zeros -- constant, and no magnitude to be relative to" true
    (RM.is_effectively_constant (repeated 0.0 10));
  Alcotest.(check bool)
    "a single observation cannot vary" true
    (RM.is_effectively_constant [| 0.0425 |]);
  (* And it must not swallow real movement, however small. A one-basis-point
     move in a rate series is a genuine observation, not noise. *)
  Alcotest.(check bool)
    "a 1bp move is real" false
    (RM.is_effectively_constant [| 0.0425; 0.0426; 0.0425 |]);
  Alcotest.(check bool)
    "ordinary returns" false
    (RM.is_effectively_constant [| -0.05; 0.01; 0.03 |]);
  (* The threshold is relative, so the same absolute movement is real at one
     scale and noise at another. 1e-9 against values of order 1 is real; against
     values of order 1e6 it is fifteen orders down and is not. *)
  Alcotest.(check bool)
    "1e-9 movement at scale 1 is real" false
    (RM.is_effectively_constant [| 1.0; 1.000000001 |]);
  Alcotest.(check bool)
    "the same movement at scale 1e6 is not" true
    (RM.is_effectively_constant [| 1e6; 1e6 +. 1e-9 |])

(* The regression, stated at the level it actually bit: beta itself. *)
let test_beta_rejects_a_noise_constant_factor () =
  check_invalid_arg "factor constant only up to float noise" (fun () ->
      RM.beta ~asset:[| 0.01; -0.02; 0.03; 0.00 |] ~factor:(Array.create ~len:4 0.0425))

(* w'Sigma w with w = [0.5; 0.5].

   Uncorrelated, Sigma = [[0.04, 0], [0, 0.04]]:
     0.25*0.04 + 0.25*0.04 = 0.02          -> sd = sqrt(0.02)
   Correlated, Sigma = [[0.04, 0.02], [0.02, 0.04]]:
     0.25*0.04 + 0.25*0.02 + 0.25*0.02 + 0.25*0.04 = 0.03  -> sd = sqrt(0.03)

   Correlation raises portfolio risk at identical weights and variances, which is
   the entire reason the covariance matrix is carried around instead of a vector
   of standalone vols. *)
let test_portfolio_stddev () =
  let weights = [| 0.5; 0.5 |] in
  let uncorrelated = Owl.Mat.of_arrays [| [| 0.04; 0.00 |]; [| 0.00; 0.04 |] |] in
  let correlated = Owl.Mat.of_arrays [| [| 0.04; 0.02 |]; [| 0.02; 0.04 |] |] in
  Alcotest.check feq "uncorrelated" (Float.sqrt 0.02)
    (RM.portfolio_stddev ~weights ~covariance:uncorrelated);
  Alcotest.check feq "correlated" (Float.sqrt 0.03)
    (RM.portfolio_stddev ~weights ~covariance:correlated)

(* VaR = |z(0.05)| * sigma_p, with sigma_p = sqrt(0.02) from the case above. *)
let test_portfolio_parametric_var () =
  let weights = [| 0.5; 0.5 |] in
  let cov = Owl.Mat.of_arrays [| [| 0.04; 0.00 |]; [| 0.00; 0.04 |] |] in
  Alcotest.check (Alcotest.float 1e-12) "portfolio VaR"
    (1.6448536269514722 *. Float.sqrt 0.02)
    (RM.portfolio_parametric_var ~weights ~covariance:cov ~confidence:0.95)

(* Series [1;2;3;4] and [2;4;6;8]: var 1.25 and 5.0, covariance 2.5 (above).
   Also asserts exact symmetry, which the upper-triangle-and-mirror construction
   is there to guarantee. *)
let test_covariance_matrix () =
  let m = RM.covariance_matrix [| [| 1.; 2.; 3.; 4. |]; [| 2.; 4.; 6.; 8. |] |] in
  Alcotest.check feq "var of series 0" 1.25 (Owl.Mat.get m 0 0);
  Alcotest.check feq "var of series 1" 5.0 (Owl.Mat.get m 1 1);
  Alcotest.check feq "cov" 2.5 (Owl.Mat.get m 0 1);
  Alcotest.check feq "exactly symmetric" (Owl.Mat.get m 0 1) (Owl.Mat.get m 1 0)

(* equity = [100; 120; 90; 110]; running peak = [100; 120; 120; 120].
   drawdowns = [0; 0; 30/120 = 0.25; 10/120 = 0.08333...].
   max = 0.25 (a historical fact), current = 0.08333... (recovers with the book). *)
let test_drawdown () =
  let equity = [| 100.; 120.; 90.; 110. |] in
  Alcotest.check feq "max" 0.25 (RM.max_drawdown ~equity);
  Alcotest.check feq "current" (10.0 /. 120.0) (RM.current_drawdown ~equity)

let test_drawdown_monotonic () =
  let equity = [| 100.; 110.; 120. |] in
  Alcotest.check feq "never down, no max drawdown" 0.0 (RM.max_drawdown ~equity);
  Alcotest.check feq "never down, no current drawdown" 0.0 (RM.current_drawdown ~equity)

(* Structurally invalid input must raise rather than return a plausible number.
   An empty window returning 0.0 would render as "no risk", which is the most
   dangerous wrong answer available. *)
let test_invalid_inputs () =
  check_invalid_arg "empty returns" (fun () ->
      RM.historical_var ~returns:[||] ~confidence:0.95);
  check_invalid_arg "empty returns (ES)" (fun () ->
      RM.expected_shortfall ~returns:[||] ~confidence:0.95);
  check_invalid_arg "confidence = 1" (fun () ->
      RM.historical_var ~returns ~confidence:1.0);
  check_invalid_arg "confidence = 0" (fun () ->
      RM.historical_var ~returns ~confidence:0.0);
  check_invalid_arg "confidence > 1" (fun () ->
      RM.historical_var ~returns ~confidence:1.5);
  check_invalid_arg "negative stddev" (fun () ->
      RM.parametric_var ~mean:0.0 ~stddev:(-0.01) ~confidence:0.95);
  check_invalid_arg "constant factor has undefined beta" (fun () ->
      RM.beta ~asset:[| 1.; 2.; 3. |] ~factor:[| 2.; 2.; 2. |]);
  check_invalid_arg "mismatched series lengths" (fun () ->
      RM.covariance [| 1.; 2. |] [| 1.; 2.; 3. |]);
  check_invalid_arg "covariance shape must match weights" (fun () ->
      RM.portfolio_stddev ~weights:[| 0.5; 0.5 |]
        ~covariance:(Owl.Mat.of_arrays [| [| 0.04 |] |]));
  check_invalid_arg "empty equity" (fun () -> RM.max_drawdown ~equity:[||])

let suite =
  ( "risk_metrics",
    [
      Alcotest.test_case "historical VaR" `Quick test_historical_var;
      Alcotest.test_case "historical VaR float rank artefact" `Quick
        test_historical_var_float_rank_artefact;
      Alcotest.test_case "historical VaR sorts its input" `Quick
        test_historical_var_is_order_independent;
      Alcotest.test_case "expected shortfall" `Quick test_expected_shortfall;
      Alcotest.test_case "ES >= VaR invariant" `Quick test_es_dominates_var;
      Alcotest.test_case "normal ppf" `Quick test_normal_ppf;
      Alcotest.test_case "parametric VaR" `Quick test_parametric_var;
      Alcotest.test_case "parametric VaR with zero vol" `Quick
        test_parametric_var_zero_vol;
      Alcotest.test_case "variance" `Quick test_variance;
      Alcotest.test_case "covariance" `Quick test_covariance;
      Alcotest.test_case "beta" `Quick test_beta;
      Alcotest.test_case "constant means constant up to float noise" `Quick
        test_effectively_constant;
      Alcotest.test_case "beta rejects a factor that is constant up to noise" `Quick
        test_beta_rejects_a_noise_constant_factor;
      Alcotest.test_case "portfolio stddev" `Quick test_portfolio_stddev;
      Alcotest.test_case "portfolio parametric VaR" `Quick test_portfolio_parametric_var;
      Alcotest.test_case "covariance matrix" `Quick test_covariance_matrix;
      Alcotest.test_case "drawdown" `Quick test_drawdown;
      Alcotest.test_case "drawdown on a monotonic curve" `Quick test_drawdown_monotonic;
      Alcotest.test_case "invalid inputs raise" `Quick test_invalid_inputs;
    ] )
