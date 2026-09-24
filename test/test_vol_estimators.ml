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

(* ------------------------------------------------------------------------ *)
(* GARCH(1,1)                                                                 *)
(* ------------------------------------------------------------------------ *)

module Garch = Ohcamel.Vol_estimators.Garch11

(* Standard normal innovations from a fixed seed, produced by Box-Muller.

   Deterministic on purpose: a test whose verdict depends on an RNG seed the
   runner controls is a test that fails one morning for no reason anybody can
   reproduce. The seed is written down here and the same draws come out every
   run on every machine. *)
let innovations ~n ~seed =
  let rng = Random.State.make [| seed |] in
  Array.init n ~f:(fun _ ->
      let u1 = Float.max 1e-12 (Random.State.float rng 1.0) in
      let u2 = Random.State.float rng 1.0 in
      Float.sqrt (-2.0 *. Float.log u1) *. Float.cos (2.0 *. Float.pi *. u2))

(* Textbook daily-equity parameters: persistence 0.98, which is a shock
   half-life of about 34 days. *)
let true_params = Garch.{ omega = 4e-6; alpha = 0.10; beta = 0.88 }

(* THE RECURSION, checked by hand.

   Two steps of sigma_t^2 = omega + alpha r_{t-1}^2 + beta sigma_{t-1}^2 against
   arithmetic done on paper. The seed is the sample variance, which under
   variance targeting is also the long-run variance -- stated in the module and
   worth pinning, because a port that seeded at omega instead would produce a
   path that looks plausible and is wrong for the first several observations. *)
let test_conditional_variance_recursion () =
  let returns = [| 0.02; -0.03; 0.01; 0.04 |] in
  let sample_variance = RM.variance returns in
  let p = Garch.{ omega = 1e-5; alpha = 0.10; beta = 0.85 } in
  let path = Garch.conditional_variances ~returns p in
  Alcotest.check feq "seeded at the sample variance" sample_variance path.(0);
  Alcotest.check feq "step 1: omega + alpha*0.02^2 + beta*seed"
    (1e-5 +. (0.10 *. 0.02 *. 0.02) +. (0.85 *. sample_variance))
    path.(1);
  Alcotest.check feq "step 2: omega + alpha*(-0.03)^2 + beta*previous"
    (1e-5 +. (0.10 *. 0.03 *. 0.03) +. (0.85 *. path.(1)))
    path.(2)

(* Persistence, the long-run level and the shock half-life are the three numbers
   a fitted GARCH is read for, and each is a one-line consequence of the
   parameters. Hand-checked so a sign or a reciprocal cannot drift. *)
let test_derived_quantities () =
  let p = Garch.{ omega = 4e-6; alpha = 0.10; beta = 0.88 } in
  Alcotest.check feq "persistence is alpha + beta" 0.98 (Garch.persistence p);
  Alcotest.check feq "long-run variance is omega / (1 - persistence)" (4e-6 /. 0.02)
    (Garch.long_run_variance p);
  Alcotest.check feq "long-run stddev"
    (Float.sqrt (4e-6 /. 0.02))
    (Garch.long_run_stddev p);
  match Garch.shock_half_life p with
  | None -> Alcotest.fail "a stationary process has a half-life"
  | Some h ->
      Alcotest.check (Alcotest.float 1e-6) "ln(0.5)/ln(0.98)"
        (Float.log 0.5 /. Float.log 0.98)
        h;
      Alcotest.(check bool) "about 34 days" true (Float.( > ) h 33.0 && Float.( < ) h 35.0)

(* THE ONE THAT MATTERS: the fit recovers a process it was given.

   Simulate 4,000 observations from known parameters and fit them back. This is
   the only test here that can tell a correct optimiser from one that stops on
   the likelihood's flat ridge, and it is the reason [fit] uses a grid before
   refining rather than a gradient step from a guess.

   The tolerances are loose because 4,000 observations is a real sample and not
   an infinite one -- the sampling standard deviation of beta at this length is
   around 0.02, so demanding three decimals would be asserting the seed. *)
let test_fit_recovers_a_known_process () =
  let path =
    Garch.simulate ~innovations:(innovations ~n:4500 ~seed:20260825) true_params
  in
  Alcotest.(check int) "4000 observations after burn-in" 4000 (Array.length path);
  let fitted = Garch.fit ~returns:path () in
  Alcotest.(check bool)
    (Printf.sprintf "alpha near 0.10 (got %.4f)" (Garch.alpha fitted))
    true
    (Float.( > ) (Garch.alpha fitted) 0.05 && Float.( < ) (Garch.alpha fitted) 0.16);
  Alcotest.(check bool)
    (Printf.sprintf "beta near 0.88 (got %.4f)" (Garch.beta fitted))
    true
    (Float.( > ) (Garch.beta fitted) 0.82 && Float.( < ) (Garch.beta fitted) 0.93);
  Alcotest.(check bool)
    (Printf.sprintf "persistence near 0.98 (got %.4f)" (Garch.persistence fitted))
    true
    (Float.( > ) (Garch.persistence fitted) 0.95
    && Float.( < ) (Garch.persistence fitted) 0.999);
  (* Variance targeting is an identity, not an approximation: the fitted model's
     unconditional variance must equal the sample's exactly, whatever the search
     found. This is the assertion that catches omega being computed from the
     wrong variance or from the wrong persistence. *)
  Alcotest.check feq_loose "variance targeting holds exactly" (RM.variance path)
    (Garch.long_run_variance fitted)

(* The likelihood at the fitted parameters must be at least the likelihood
   anywhere else the search could have stopped -- including at the EWMA-like
   boundary and at the coarse grid's own corners.

   A weaker but much sharper statement than "the parameters are near the truth":
   it holds on any series, including ones with no GARCH structure at all, and it
   fails immediately if the refinement walks downhill. *)
let test_fit_maximises_the_likelihood_it_searched () =
  let path = Garch.simulate ~innovations:(innovations ~n:1200 ~seed:7) true_params in
  let fitted = Garch.fit ~returns:path () in
  let at = Garch.log_likelihood ~returns:path in
  let best = at fitted in
  let sample_variance = RM.variance path in
  List.iter
    [ (0.02, 0.90); (0.05, 0.90); (0.10, 0.85); (0.20, 0.70); (0.30, 0.50); (0.05, 0.94) ]
    ~f:(fun (alpha, beta) ->
      let other = Garch.of_params ~sample_variance ~alpha ~beta in
      Alcotest.(check bool)
        (Printf.sprintf "no better at alpha=%.2f beta=%.2f" alpha beta)
        true
        (Float.( >= ) best (at other -. 1e-9)))

(* The forecast is strictly one step ahead: it uses the last return and the last
   conditional variance and nothing after them.

   Checked by construction rather than by inspection -- append one more
   observation and the previous forecast must equal the new path's final
   conditional variance. If the recursion were off by one, these would differ. *)
let test_forecast_is_one_step_ahead () =
  let returns = [| 0.01; -0.02; 0.015; -0.005; 0.03; -0.01 |] in
  let p = Garch.{ omega = 1e-5; alpha = 0.08; beta = 0.90 } in
  let forecast = Garch.forecast_stddev ~returns p in
  (* The seed of [conditional_variances] is the sample variance, which changes
     when a return is appended, so the two paths are not directly comparable --
     the check is that the forecast equals the recursion applied to the LAST
     step of the path it was given. *)
  let path = Garch.conditional_variances ~returns p in
  let n = Array.length returns in
  let expected =
    Float.sqrt
      (1e-5 +. (0.08 *. returns.(n - 1) *. returns.(n - 1)) +. (0.90 *. path.(n - 1)))
  in
  Alcotest.check feq "one step past the last observation" expected forecast

(* Simulation is deterministic in its innovations, and the burn-in is real.

   The second half matters: without it every simulated path would start exactly
   at the long-run variance, which understates how far a real path wanders early
   on and would quietly flatter the fit in the test above. *)
let test_simulation_is_deterministic_and_burns_in () =
  let z = innovations ~n:900 ~seed:11 in
  let a = Garch.simulate ~innovations:z true_params in
  let b = Garch.simulate ~innovations:z true_params in
  Alcotest.(check bool) "same innovations, same path" true (Array.equal Float.equal a b);
  Alcotest.(check int) "burn-in is consumed, not returned" 400 (Array.length a);
  let short = Garch.simulate ~burn_in:0 ~innovations:z true_params in
  Alcotest.(check int) "burn_in:0 returns everything" 900 (Array.length short);
  (* The burn-in discards a PREFIX of the same path rather than generating a
     different one, so the burned-in path is the tail of the unburned one
     exactly. Worth pinning: it says the burn-in is doing what it claims -- the
     returned observations start after the process has wandered away from its
     seeding at the unconditional variance -- and not quietly re-seeding the
     recursion partway through, which would produce a discontinuity no reader
     would ever see. *)
  Alcotest.check feq "the burned-in path is the tail of the unburned one" short.(500)
    a.(0);
  Alcotest.check feq "and stays aligned" short.(899) a.(399)

let test_garch_invalid_inputs () =
  check_invalid_arg "too few observations" (fun () ->
      Garch.fit ~returns:[| 0.01; 0.02 |] ());
  check_invalid_arg "a constant series has no variance to target" (fun () ->
      Garch.fit ~returns:(Array.create ~len:50 0.01) ());
  check_invalid_arg "burn-in longer than the innovations" (fun () ->
      Garch.simulate ~burn_in:100 ~innovations:(innovations ~n:50 ~seed:1) true_params)

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
      Alcotest.test_case "GARCH: the conditional-variance recursion" `Quick
        test_conditional_variance_recursion;
      Alcotest.test_case "GARCH: persistence, long-run level, half-life" `Quick
        test_derived_quantities;
      Alcotest.test_case "GARCH: THE FIT RECOVERS A KNOWN PROCESS" `Quick
        test_fit_recovers_a_known_process;
      Alcotest.test_case "GARCH: the fit maximises what it searched" `Quick
        test_fit_maximises_the_likelihood_it_searched;
      Alcotest.test_case "GARCH: the forecast is one step ahead" `Quick
        test_forecast_is_one_step_ahead;
      Alcotest.test_case "GARCH: simulation is deterministic and burns in" `Quick
        test_simulation_is_deterministic_and_burns_in;
      Alcotest.test_case "GARCH: invalid inputs raise" `Quick test_garch_invalid_inputs;
    ] )
