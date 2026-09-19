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

(* Population moments of [1;2;3;10]: mean 16/4 = 4, deviations -3,-2,-1,6.
     m2 = (9+4+1+36)/4    = 50/4    = 12.5
     m3 = (-27-8-1+216)/4 = 180/4   = 45
     m4 = (81+16+1+1296)/4 = 1394/4 = 348.5
   g1 = m3 / m2^1.5 = 45 / (12.5 * sqrt 12.5) = 45 / 44.194174 = 1.018234
   g2 = m4 / m2^2 - 3 = 348.5 / 156.25 - 3 = 2.2304 - 3 = -0.7696 *)
let test_skewness_and_kurtosis () =
  let xs = [| 1.; 2.; 3.; 10. |] in
  Alcotest.check (Alcotest.float 1e-6) "skewness" 1.018234 (RM.skewness xs);
  Alcotest.check (Alcotest.float 1e-6) "excess kurtosis" (-0.7696) (RM.excess_kurtosis xs)

(* With skew and excess kurtosis both zero, every correction term in the
   expansion carries a g1 or a g2 factor and vanishes exactly -- the result
   is z itself, not merely close to it. *)
let test_cornish_fisher_z_no_correction () =
  Alcotest.check feq "z unchanged at zero skew and kurtosis" (-1.6448536269514722)
    (RM.cornish_fisher_z ~z:(-1.6448536269514722) ~skew:0.0 ~excess_kurtosis:0.0)

(* z = normal_ppf(0.01) = -2.3263478740408408 (a standard tabulated
   constant), skew g1 = -0.5, excess kurtosis g2 = 3. From the formula
   z + (z^2-1)g1/6 + (z^3-3z)g2/24 - (2z^3-5z)g1^2/36:
     z^2 = 5.411894...   ->  (z^2-1)*g1/6       = 4.411894*(-0.5)/6  = -0.367658
     z^3 = -12.589950...  -> (z^3-3z)*g2/24     = -5.610906*3/24     = -0.701363
                          -> -(2z^3-5z)*g1^2/36 = -(-13.548159)*0.25/36 = +0.094084
     z_cf = z + (-0.367658) + (-0.701363) + 0.094084 = -3.301284
   The 1e-12 check below recomputes the same formula independently in this
   test (not by calling the library) and compares it to what the library
   returns, so it catches a coding error in the implementation rather than a
   duplicated error in the hand arithmetic. *)
let test_cornish_fisher_three_term () =
  let z = -2.3263478740408408 in
  let g1 = -0.5 and g2 = 3.0 in
  let z2 = z *. z in
  let z3 = z2 *. z in
  let term1 = (z2 -. 1.0) *. g1 /. 6.0 in
  let term2 = (z3 -. (3.0 *. z)) *. g2 /. 24.0 in
  let term3 = -.(((2.0 *. z3) -. (5.0 *. z)) *. g1 *. g1) /. 36.0 in
  let expected_z_cf = z +. term1 +. term2 +. term3 in
  Alcotest.check (Alcotest.float 1e-6) "(z^2-1)g1/6" (-0.367658) term1;
  Alcotest.check (Alcotest.float 1e-6) "(z^3-3z)g2/24" (-0.701363) term2;
  Alcotest.check (Alcotest.float 1e-6) "-(2z^3-5z)g1^2/36" 0.094084 term3;
  Alcotest.check (Alcotest.float 1e-6) "z_cf" (-3.301284) expected_z_cf;
  Alcotest.check (Alcotest.float 1e-12) "matches the library's formula" expected_z_cf
    (RM.cornish_fisher_z ~z ~skew:g1 ~excess_kurtosis:g2)

(* Zero skew and kurtosis: the closed-form quadratic A*z^2+B*z+C collapses to
   A=0, B=0, C=1, a constant 1 at every z, so the minimum over [-4,4] is 1,
   strictly positive -- monotone. *)
let test_cornish_fisher_monotone_at_zero () =
  Alcotest.(check bool)
    "zero skew and kurtosis is monotone" true
    (RM.cornish_fisher_is_monotone ~skew:0.0 ~excess_kurtosis:0.0)

(* Heavy positive skew with no offsetting kurtosis breaks monotonicity.
   g1 = 3, g2 = 0:
     A = g2/8 - g1^2/6 = 0 - 9/6       = -1.5     (<= 0, minimum at an endpoint)
     B = g1/3           = 1
     C = 1 - g2/8 + 5g1^2/36 = 1 + 45/36 = 2.25
     f(-4) = 16A - 4B + C = -24 - 4 + 2.25  = -25.75
     f(4)  = 16A + 4B + C = -24 + 4 + 2.25  = -17.75
   both negative, so the minimum over [-4, 4] is negative and the expansion
   is not monotone there. *)
let test_cornish_fisher_not_monotone () =
  Alcotest.(check bool)
    "skew 3, excess kurtosis 0 is not monotone on [-4, 4]" false
    (RM.cornish_fisher_is_monotone ~skew:3.0 ~excess_kurtosis:0.0)

(* The reviewer's counterexample. A grid sampled every 0.01 across [-4, 4]
   called this monotone, because the violation is a shallow dip -- on the
   order of 1e-5 deep -- far smaller than a value difference that grid can
   resolve reliably against floating-point noise. The closed form finds it
   exactly, by locating the quadratic's actual vertex rather than sampling
   near it.
     g1 = 1.6896, g2 = 10.40274.  g1^2 = 1.6896^2 = 2.85474816 exactly
     (1.6896 has 4 decimal digits, so its square is an exact 8-digit decimal).
     A = g2/8 - g1^2/6 = 1.3003425 - 0.47579136 = 0.82455114      (> 0)
     B = g1/3 = 0.5632
     C = 1 - g2/8 + 5*g1^2/36 = 1 - 1.3003425 + 0.3964928 = 0.0961503
     vertex = -B/(2A) = -0.5632 / 1.64910228 ~= -0.341519, inside [-4, 4]
     minimum = C - B^2/(4A) = 0.0961503 - 0.31719424/3.29820456
             ~= 0.0961503 - 0.0961718 ~= -0.0000215
   negative (on the order of -1e-5, as the reviewer found), so the closed
   form must reject monotonicity here even though it barely misses. *)
let test_cornish_fisher_reviewer_counterexample () =
  let g1 = 1.6896 and g2 = 10.40274 in
  let a = (g2 /. 8.0) -. (g1 *. g1 /. 6.0) in
  let b = g1 /. 3.0 in
  let c = 1.0 -. (g2 /. 8.0) +. (5.0 *. g1 *. g1 /. 36.0) in
  Alcotest.(check bool) "the quadratic opens upward here" true (Float.( > ) a 0.0);
  let vertex = -.b /. (2.0 *. a) in
  if not (Float.( >= ) vertex (-4.0) && Float.( <= ) vertex 4.0) then
    Alcotest.failf "expected the vertex %f to fall inside [-4, 4]" vertex;
  let minimum = c -. (b *. b /. (4.0 *. a)) in
  if not (Float.( < ) minimum 0.0) then
    Alcotest.failf "expected a negative minimum derivative, got %.9f" minimum;
  Alcotest.(check bool)
    "the closed form rejects a violation a coarse grid would miss" false
    (RM.cornish_fisher_is_monotone ~skew:g1 ~excess_kurtosis:g2)

(* Below 120 observations there is not enough data to trust a third and
   fourth moment estimate at all, so the function refuses outright. This
   series alternates so it is unambiguously not "flat" -- the refusal here is
   solely about the observation count, not the other refusal condition. *)
let test_cornish_fisher_var_too_few_observations () =
  let xs = Array.init 119 ~f:(fun i -> if i % 2 = 0 then 0.01 else -0.01) in
  match RM.cornish_fisher_var ~returns:xs ~confidence:0.95 with
  | Error _ -> ()
  | Ok v -> Alcotest.failf "expected Error below 120 observations, got Ok %f" v

(* A flat series (150 observations, above the floor) has no variation to
   standardise by: skewness and kurtosis are both undefined for it, so the
   function refuses rather than divide zero by zero. *)
let test_cornish_fisher_var_flat_series () =
  let xs = Array.create ~len:150 0.0425 in
  match RM.cornish_fisher_var ~returns:xs ~confidence:0.95 with
  | Error _ -> ()
  | Ok v -> Alcotest.failf "expected Error for a flat series, got Ok %f" v

(* A symmetric, mesokurtic 120-point series built so skewness and excess
   kurtosis are exactly zero, not merely close. The base pattern
   [1;1;-1;-1;2;-2;0;0;0;0;0;0] (12 points) gives, by hand:
     sum            = 1+1-1-1+2-2+0*6       = 0    -> mean = 0/12 = 0
     sum of squares = 1+1+1+1+4+4+0*6       = 12   -> m2 = 12/12 = 1
     sum of cubes   = 1+1-1-1+8-8+0*6       = 0    -> m3 = 0/12  = 0
     sum of 4th pow = 1+1+1+1+16+16+0*6     = 36   -> m4 = 36/12 = 3
     g1 = m3/m2^1.5 = 0/1 = 0
     g2 = m4/m2^2 - 3 = 3/1 - 3 = 0
   every one of those divisions is an exact integer ratio (0/12, 12/12,
   36/12), not a floating approximation, so g1 and g2 come out exactly zero.
   Repeating the pattern ten times (120 points, meeting the 120-observation
   floor) leaves every population moment unchanged -- replaying a
   population's whole history again does not move its mean, variance, skew
   or kurtosis -- while the sums above simply scale by 10 and cancel in the
   division by n. With g1 = g2 = 0, cornish_fisher_z reduces to the identity
   (see test_cornish_fisher_z_no_correction), so cornish_fisher_var here must
   equal the ordinary parametric estimate -(z * population sigma) to 1e-12. *)
let test_cornish_fisher_var_matches_parametric_when_normal () =
  let pattern = [| 1.; 1.; -1.; -1.; 2.; -2.; 0.; 0.; 0.; 0.; 0.; 0. |] in
  let returns = Array.concat (List.init 10 ~f:(fun _ -> pattern)) in
  Alcotest.check feq "skewness is exactly zero" 0.0 (RM.skewness returns);
  Alcotest.check feq "excess kurtosis is exactly zero" 0.0 (RM.excess_kurtosis returns);
  let confidence = 0.95 in
  let z = RM.normal_ppf ~p:(1.0 -. confidence) in
  let expected = -.(z *. RM.stddev returns) in
  match RM.cornish_fisher_var ~returns ~confidence with
  | Error e -> Alcotest.failf "expected Ok, got Error %s" e
  | Ok v ->
      Alcotest.check (Alcotest.float 1e-12) "matches the parametric estimate" expected v

(* A single non-finite observation must never reach a mean, moment or
   quantile computation: nan and infinity both propagate through arithmetic
   silently rather than raising, so without an explicit check
   cornish_fisher_var would otherwise return [Ok nan] here -- a number that
   looks like an ordinary, if strange, VaR figure. 130 observations
   (comfortably above the 120 floor) alternating so the series is
   unambiguously not flat, with one swapped to nan: the refusal is solely
   about finiteness. *)
let test_cornish_fisher_var_refuses_non_finite () =
  let xs = Array.init 130 ~f:(fun i -> if i % 2 = 0 then 0.01 else -0.01) in
  xs.(64) <- Float.nan;
  (match RM.cornish_fisher_var ~returns:xs ~confidence:0.95 with
  | Error msg ->
      if not (String.is_substring msg ~substring:"1 of 130") then
        Alcotest.failf
          "expected the Error to name 1 of 130 non-finite observations, got %s" msg
  | Ok v -> Alcotest.failf "expected Error for a nan observation, got Ok %f" v);
  (* The count is genuinely counted, not merely "is there one" -- a nan and
     two infinities together must be named as three. *)
  let ys = Array.init 130 ~f:(fun i -> if i % 2 = 0 then 0.01 else -0.01) in
  ys.(10) <- Float.nan;
  ys.(20) <- Float.infinity;
  ys.(30) <- Float.neg_infinity;
  match RM.cornish_fisher_var ~returns:ys ~confidence:0.95 with
  | Error msg ->
      if not (String.is_substring msg ~substring:"3 of 130") then
        Alcotest.failf
          "expected the Error to name 3 of 130 non-finite observations, got %s" msg
  | Ok v -> Alcotest.failf "expected Error for non-finite observations, got Ok %f" v

(* cornish_fisher_var's non-monotone refusal, tested end to end from a real
   returns series rather than from hand-picked (skew, kurtosis) parameters --
   using the already-derived [1;2;3;10] moments (g1 = 1.018234, g2 = -0.7696),
   tiled 30 times to reach the 120-observation floor with the population
   moments unchanged (see test_skewness_and_kurtosis and
   test_cornish_fisher_var_matches_parametric_when_normal for why tiling
   preserves them).

   Correct assignment (skew = g1, excess_kurtosis = g2):
     A = g2/8 - g1^2/6 = -0.0962 - 0.172800 = -0.269000      (<= 0)
     B = g1/3 = 0.339411
     C = 1 - g2/8 + 5g1^2/36 = 1.0962 + 0.144000 = 1.240200
     f(-4) = 16A - 4B + C = -4.304 - 1.357644 + 1.2402 = -4.421444
     f(4)  = 16A + 4B + C = -4.304 + 1.357644 + 1.2402 = -1.706156
   both negative: not monotone.

   This series is chosen, not merely skewed, so that this test kills a
   mutant that swaps g1 and g2 at cornish_fisher_var's call site. Swapped
   (skew = g2, excess_kurtosis = g1) evaluates a *different*, and here
   monotone, quadratic:
     A' = g1/8 - g2^2/6 = 0.127279 - 0.098699 = 0.028580     (> 0)
     B' = g2/3 = -0.256533
     C' = 1 - g1/8 + 5g2^2/36 = 0.872721 + 0.082249 = 0.954970
     vertex' = -B'/(2A') ~= 4.489, outside [-4, 4], so the minimum on the
     interval is at z = 4:
     f'(4) = 16A' + 4B' + C' = 0.45728 - 1.026132 + 0.954970 = 0.386118
   positive: monotone. So a mutant swap would make cornish_fisher_var return
   Ok here instead of Error, and the assertions below catch that directly:
   first on the pure function with the real computed moments in both
   orders, then end to end through cornish_fisher_var itself. *)
let test_cornish_fisher_var_not_monotone () =
  let pattern = [| 1.; 2.; 3.; 10. |] in
  let returns = Array.concat (List.init 30 ~f:(fun _ -> pattern)) in
  let g1 = RM.skewness returns and g2 = RM.excess_kurtosis returns in
  Alcotest.(check bool)
    "the correct assignment (skew, excess kurtosis) is not monotone" false
    (RM.cornish_fisher_is_monotone ~skew:g1 ~excess_kurtosis:g2);
  Alcotest.(check bool)
    "the swapped assignment (excess kurtosis as skew, skew as excess kurtosis) stays \
     monotone"
    true
    (RM.cornish_fisher_is_monotone ~skew:g2 ~excess_kurtosis:g1);
  match RM.cornish_fisher_var ~returns ~confidence:0.95 with
  | Error _ -> ()
  | Ok v -> Alcotest.failf "expected Error for a non-monotone expansion, got Ok %f" v

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
      Alcotest.test_case "skewness and excess kurtosis of [1;2;3;10]" `Quick
        test_skewness_and_kurtosis;
      Alcotest.test_case "cornish-fisher z is unchanged at zero skew and kurtosis" `Quick
        test_cornish_fisher_z_no_correction;
      Alcotest.test_case "cornish-fisher z three-term case" `Quick
        test_cornish_fisher_three_term;
      Alcotest.test_case "cornish-fisher expansion is monotone at zero skew and kurtosis"
        `Quick test_cornish_fisher_monotone_at_zero;
      Alcotest.test_case "cornish-fisher expansion rejects heavy skew" `Quick
        test_cornish_fisher_not_monotone;
      Alcotest.test_case
        "cornish-fisher closed form catches the reviewer's counterexample" `Quick
        test_cornish_fisher_reviewer_counterexample;
      Alcotest.test_case "cornish-fisher VaR refuses too few observations" `Quick
        test_cornish_fisher_var_too_few_observations;
      Alcotest.test_case "cornish-fisher VaR refuses a flat series" `Quick
        test_cornish_fisher_var_flat_series;
      Alcotest.test_case "cornish-fisher VaR matches the parametric estimate when normal"
        `Quick test_cornish_fisher_var_matches_parametric_when_normal;
      Alcotest.test_case "cornish-fisher VaR refuses non-finite observations" `Quick
        test_cornish_fisher_var_refuses_non_finite;
      Alcotest.test_case
        "cornish-fisher VaR non-monotone refusal through the VaR function" `Quick
        test_cornish_fisher_var_not_monotone;
    ] )
