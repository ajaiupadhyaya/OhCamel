(* Unit tests for vol_estimators.ml.

   Three kinds of test, and the second two matter more than the first.

     THE FORMULA     one hand-derived covariance, at a lambda and a window
                     length chosen so the weights are 1/7, 2/7 and 4/7 and the
                     weighted mean is exactly zero. The expected variance is
                     6e-4 and can be read off the page.

     THE REDUCTION   at lambda -> 1 the weights go uniform, so this estimator
                     must converge on Risk_metrics.covariance_matrix entry for
                     entry. That property is the entire justification for
                     calling the two graph nodes SIBLINGS: it pins the
                     difference between them to weighting and nothing else. If
                     this test fails, a disagreement between the two numbers on
                     the dashboard stops being a regime signal and becomes an
                     unexplained discrepancy.

     THE POINT       EWMA responds faster to a volatility regime break. That is
                     why the feature exists, and the formula being right is not
                     the same claim. A test that only checked the arithmetic
                     would pass on an estimator with the decay running backwards
                     through the window -- which is a real and easy mistake,
                     since "most recent" is the LAST element of these arrays and
                     the exponent counts from it. *)

open Core
module Ewma = Ohcamel.Vol_estimators.Ewma
module RM = Ohcamel.Risk_metrics

let feq = Alcotest.float 1e-12
let feq_loose = Alcotest.float 1e-9

(* THE FORMULA.

   lambda = 0.5 over a 3-observation window. Unnormalised weights, newest
   first, are 1, 0.5, 0.25 -- summing to 1.75 -- so oldest to newest the
   normalised weights are

     1/7, 2/7, 4/7

   xs is chosen so the weighted mean is exactly zero:

     (1(-0.06) + 2(0.01) + 4(0.01)) / 7 = (-0.06 + 0.02 + 0.04) / 7 = 0

   which reduces the variance to a plain weighted sum of squares:

     var = (1(0.06^2) + 2(0.01^2) + 4(0.01^2)) / 7
         = (0.0036 + 0.0002 + 0.0004) / 7
         = 0.0042 / 7
         = 6.0e-4     exactly

   ys = -xs has weighted mean zero for the same reason, so var(ys) is the same
   number and cov(xs, ys) is its negative. The 2x2 matrix is therefore

     [[ 6e-4, -6e-4 ],
      [ -6e-4, 6e-4 ]]

   with no rounding anywhere in the derivation. *)
let xs = [| -0.06; 0.01; 0.01 |]
let ys = Array.map xs ~f:Float.neg

let test_weights () =
  let w = Ewma.weights ~n:3 ~lambda:0.5 in
  Alcotest.check feq "oldest" (1.0 /. 7.0) w.(0);
  Alcotest.check feq "middle" (2.0 /. 7.0) w.(1);
  Alcotest.check feq "newest" (4.0 /. 7.0) w.(2);
  Alcotest.check feq "weights sum to one" 1.0 (Array.fold w ~init:0.0 ~f:( +. ))

let test_mean_is_zero_by_construction () =
  Alcotest.check feq "weighted mean of xs" 0.0 (Ewma.mean ~returns:xs ~lambda:0.5);
  Alcotest.check feq "weighted mean of ys" 0.0 (Ewma.mean ~returns:ys ~lambda:0.5)

let test_variance_hand_derived () =
  Alcotest.check feq "var(xs)" 6.0e-4 (Ewma.variance ~returns:xs ~lambda:0.5);
  Alcotest.check feq "var(ys)" 6.0e-4 (Ewma.variance ~returns:ys ~lambda:0.5);
  Alcotest.check feq "stddev(xs)" (Float.sqrt 6.0e-4)
    (Ewma.stddev ~returns:xs ~lambda:0.5)

let test_covariance_hand_derived () =
  Alcotest.check feq "cov(xs, ys)" (-6.0e-4) (Ewma.covariance ~xs ~ys ~lambda:0.5);
  Alcotest.check feq "cov is symmetric" (-6.0e-4)
    (Ewma.covariance ~xs:ys ~ys:xs ~lambda:0.5);
  Alcotest.check feq "cov(xs, xs) is var(xs)" 6.0e-4
    (Ewma.covariance ~xs ~ys:xs ~lambda:0.5)

let test_covariance_matrix_hand_derived () =
  let m = Ewma.covariance_matrix ~series:[| xs; ys |] ~lambda:0.5 in
  Alcotest.check feq "m[0][0]" 6.0e-4 (Owl.Mat.get m 0 0);
  Alcotest.check feq "m[0][1]" (-6.0e-4) (Owl.Mat.get m 0 1);
  Alcotest.check feq "m[1][0]" (-6.0e-4) (Owl.Mat.get m 1 0);
  Alcotest.check feq "m[1][1]" 6.0e-4 (Owl.Mat.get m 1 1);
  (* Exact bit-for-bit symmetry, not symmetry to a tolerance. The matrix is
     mirrored rather than computed twice for the same reason
     Risk_metrics.covariance_matrix mirrors: two independently computed halves
     can differ in the last bit, which is enough to fail a positive-definiteness
     check downstream. *)
  Alcotest.(check bool)
    "exactly symmetric" true
    (Float.equal (Owl.Mat.get m 0 1) (Owl.Mat.get m 1 0))

(* THE REDUCTION.

   As lambda -> 1 every weight goes to 1/n, so this estimator becomes the
   equal-weighted one. Checked entry for entry against Risk_metrics rather than
   against a hand-derived matrix, because the claim under test is precisely
   "these two agree in the limit" and hand-deriving both sides would test
   arithmetic instead.

   lambda is 1 - 1e-6 rather than 1.0: the estimator rejects lambda = 1 (a
   nondecaying "exponential" weight is the other estimator, and offering it here
   would give the same number two names). 1e-6 over a 40-observation window
   leaves the weights uniform to about 4e-5, which is why the tolerance below is
   1e-9 in absolute terms against entries of order 1e-4 rather than something
   tighter. *)
let test_reduces_to_equal_weighted () =
  let n = 40 in
  (* A deterministic pair of series with genuinely different variances and a
     non-trivial covariance, so the comparison is not accidentally passing on a
     degenerate matrix. *)
  let a = Array.init n ~f:(fun i -> 0.01 *. Float.sin (float_of_int i)) in
  let b = Array.init n ~f:(fun i -> 0.004 *. Float.cos (float_of_int (2 * i))) in
  let ewma = Ewma.covariance_matrix ~series:[| a; b |] ~lambda:(1.0 -. 1e-6) in
  let equal = RM.covariance_matrix [| a; b |] in
  for i = 0 to 1 do
    for j = 0 to 1 do
      Alcotest.check feq_loose
        (Printf.sprintf "entry %d,%d" i j)
        (Owl.Mat.get equal i j) (Owl.Mat.get ewma i j)
    done
  done

(* THE POINT.

   A deterministic regime break, with no RNG anywhere: 100 observations
   alternating +/-0.005, then 20 alternating +/-0.020. An alternating series of
   magnitude c has mean zero and variance exactly c^2, so both regimes have a
   volatility that is known rather than estimated.

   The true volatility AFTER the break is 0.020. What each estimator reports
   over the whole 120-observation window:

     equal-weighted   var = (100(0.005^2) + 20(0.020^2)) / 120
                          = (0.0025 + 0.008) / 120
                          = 8.75e-5                          -> sigma ~ 0.00935

     EWMA(0.94)       weight mass on the last 20 observations
                          = 1 - 0.94^20 ~ 0.710
                      var ~ 0.710(4.0e-4) + 0.290(2.5e-5)
                          = 2.91e-4                          -> sigma ~ 0.0171

   So the equal-weighted estimate is less than half the volatility the market is
   actually printing, and the EWMA estimate is within 15% of it. That gap is the
   feature.

   The assertion is the inequality, not those numbers: the exact figures depend
   on the demeaning, which is weighted and therefore not exactly zero here, and
   pinning them would make this a second formula test rather than a property
   test. The formula is already pinned above. *)
let regime_break =
  Array.init 120 ~f:(fun i ->
      let magnitude = if i < 100 then 0.005 else 0.020 in
      if i % 2 = 0 then magnitude else -.magnitude)

let test_responds_to_a_regime_break () =
  let true_post_break = 0.020 in
  let ewma = Ewma.stddev ~returns:regime_break ~lambda:Ewma.default_lambda in
  let equal = RM.stddev regime_break in
  Alcotest.(check bool) "EWMA reads the higher volatility" true (Float.( > ) ewma equal);
  Alcotest.(check bool)
    "EWMA is closer to the volatility actually being printed" true
    (Float.( < )
       (Float.abs (ewma -. true_post_break))
       (Float.abs (equal -. true_post_break)));
  (* Both bounds, so a regression that overshoots is caught as well as one that
     lags. An estimator that simply returned the last observation's magnitude
     would pass the two assertions above and fail this one. *)
  Alcotest.(check bool)
    "and still below it, because the calm period is still in the window" true
    (Float.( < ) ewma true_post_break)

(* A lower lambda forgets faster, which is the knob's entire meaning. Monotone
   in lambda over the same break: 0.90 must read higher than 0.94, which must
   read higher than 0.98. This is what catches the decay running the wrong way
   through the array -- an error that leaves every other test here passing,
   because a reversed weighting is still a valid weighting. *)
let test_lower_lambda_forgets_faster () =
  let at lambda = Ewma.stddev ~returns:regime_break ~lambda in
  let fast = at 0.90 and standard = at 0.94 and slow = at 0.98 in
  Alcotest.(check bool) "0.90 > 0.94" true (Float.( > ) fast standard);
  Alcotest.(check bool) "0.94 > 0.98" true (Float.( > ) standard slow)

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, got a value" name

let test_invalid_inputs () =
  check_invalid_arg "lambda = 0" (fun () -> Ewma.variance ~returns:xs ~lambda:0.0);
  check_invalid_arg "lambda = 1" (fun () -> Ewma.variance ~returns:xs ~lambda:1.0);
  check_invalid_arg "lambda > 1" (fun () -> Ewma.variance ~returns:xs ~lambda:1.5);
  check_invalid_arg "lambda < 0" (fun () -> Ewma.variance ~returns:xs ~lambda:(-0.5));
  check_invalid_arg "empty series" (fun () -> Ewma.variance ~returns:[||] ~lambda:0.94);
  check_invalid_arg "mismatched lengths" (fun () ->
      Ewma.covariance ~xs:[| 1.0; 2.0 |] ~ys:[| 1.0; 2.0; 3.0 |] ~lambda:0.94);
  check_invalid_arg "no series at all" (fun () ->
      Ewma.covariance_matrix ~series:[||] ~lambda:0.94);
  check_invalid_arg "ragged series" (fun () ->
      Ewma.covariance_matrix ~series:[| [| 1.0; 2.0 |]; [| 1.0 |] |] ~lambda:0.94)

let suite =
  ( "vol_estimators",
    [
      Alcotest.test_case "decay weights" `Quick test_weights;
      Alcotest.test_case "weighted mean" `Quick test_mean_is_zero_by_construction;
      Alcotest.test_case "EWMA variance" `Quick test_variance_hand_derived;
      Alcotest.test_case "EWMA covariance" `Quick test_covariance_hand_derived;
      Alcotest.test_case "EWMA covariance matrix" `Quick
        test_covariance_matrix_hand_derived;
      Alcotest.test_case "lambda -> 1 IS the equal-weighted estimator" `Quick
        test_reduces_to_equal_weighted;
      Alcotest.test_case "EWMA RESPONDS FASTER TO A REGIME BREAK" `Quick
        test_responds_to_a_regime_break;
      Alcotest.test_case "a lower lambda forgets faster" `Quick
        test_lower_lambda_forgets_faster;
      Alcotest.test_case "invalid inputs raise" `Quick test_invalid_inputs;
    ] )
