(* Unit tests for attribution.ml.

   Every expected value here is derived by hand and the derivation is written
   above the test, per the project convention. Two of the cases were chosen
   because they can be read off without arithmetic at all:

     UNCORRELATED    two independent names, so the covariance matrix is
                     diagonal and each component reduces to w_i^2 * sigma_i^2
                     over sigma_p. The percentages are then variance shares --
                     0.8 and 0.2 -- and any error in the matrix-vector product
                     moves them off those round numbers immediately.

     PERFECT HEDGE   two names with correlation exactly -1. The smaller leg has
                     a NEGATIVE contribution, which is the property most likely
                     to be broken by a stray absolute value somewhere in the
                     chain, and the one that no amount of eyeballing a dashboard
                     would catch.

   The Euler identity -- components sum to the total, exactly -- is asserted in
   every case rather than in one, because it is the invariant that makes the
   whole decomposition meaningful and it is cheap to check. *)

open Core
module A = Ohcamel.Attribution

let feq = Alcotest.float 1e-12
let feq_loose = Alcotest.float 1e-9
let mat rows = Owl.Mat.of_arrays rows

let get = function
  | Some x -> x
  | None -> Alcotest.fail "attribution: expected Some, got None"

let sum = Array.fold ~init:0.0 ~f:( +. )

(* UNCORRELATED: sigma_1 = 0.2, sigma_2 = 0.1, rho = 0, w = [0.5; 0.5].

     S      = [[0.04, 0], [0, 0.01]]
     w'Sw   = 0.25*0.04 + 0.25*0.01 = 0.0125
     sigma_p= sqrt(0.0125)          = 0.111803398874989...
     Sw     = [0.02; 0.005]
     marg   = [0.02; 0.005] / sigma_p = [0.178885438...; 0.044721359...]
     comp   = 0.5 * marg              = [0.089442719...; 0.022360679...]
     sum(comp) = 0.111803398... = sigma_p                     <- Euler
     pct    = comp / sigma_p          = [0.8; 0.2]            <- variance shares
     stand  = [0.5*0.2; 0.5*0.1]      = [0.1; 0.05]
     DR     = 0.15 / sigma_p          = 1.341640786499874 *)
let uncorrelated_cov = mat [| [| 0.04; 0.0 |]; [| 0.0; 0.01 |] |]
let uncorrelated_w = [| 0.5; 0.5 |]

let test_uncorrelated () =
  let t = get (A.compute ~weights:uncorrelated_w ~covariance:uncorrelated_cov) in
  Alcotest.check feq "sigma_p" 0.11180339887498948 (A.portfolio_stddev t);
  Alcotest.(check (array feq))
    "marginal"
    [| 0.17888543819998318; 0.04472135954999579 |]
    (A.marginal t);
  Alcotest.(check (array feq))
    "component"
    [| 0.08944271909999159; 0.022360679774997894 |]
    (A.component t);
  Alcotest.(check (array feq)) "percent" [| 0.8; 0.2 |] (A.percent t);
  Alcotest.(check (array feq)) "standalone" [| 0.1; 0.05 |] (A.standalone t);
  Alcotest.check feq "diversification ratio" 1.341640786499874 (A.diversification_ratio t)

(* PERFECT HEDGE: sigma_1 = sigma_2 = 0.2, rho = -1, w = [0.75; 0.25].

     S      = [[0.04, -0.04], [-0.04, 0.04]]
     w'Sw   = 0.04 * (0.75 - 0.25)^2 = 0.04 * 0.25 = 0.01
     sigma_p= 0.1
     Sw     = [0.02; -0.02]
     marg   = [0.2; -0.2]
     comp   = [0.15; -0.05]         sum = 0.10 = sigma_p      <- Euler holds
     pct    = [1.5; -0.5]           sum = 1.0

   The second name contributes NEGATIVE risk. It is not a small position that
   happens to matter little; holding it makes the book strictly less volatile
   than holding the first name alone, and the decomposition says so. Its
   standalone risk is a perfectly ordinary +0.05, which is exactly why
   standalone risk is the wrong number to size a limit against. *)
let hedged_cov = mat [| [| 0.04; -0.04 |]; [| -0.04; 0.04 |] |]
let hedged_w = [| 0.75; 0.25 |]

let test_perfect_hedge () =
  let t = get (A.compute ~weights:hedged_w ~covariance:hedged_cov) in
  Alcotest.check feq_loose "sigma_p" 0.1 (A.portfolio_stddev t);
  Alcotest.(check (array feq_loose)) "marginal" [| 0.2; -0.2 |] (A.marginal t);
  Alcotest.(check (array feq_loose)) "component" [| 0.15; -0.05 |] (A.component t);
  Alcotest.(check (array feq_loose)) "percent" [| 1.5; -0.5 |] (A.percent t);
  Alcotest.(check (array feq_loose)) "standalone" [| 0.15; 0.05 |] (A.standalone t);
  Alcotest.check feq_loose "diversification ratio" 2.0 (A.diversification_ratio t)

(* The hedge case again, stated as the property rather than the numbers: the
   contribution is negative and the standalone is positive. This is the
   assertion that fails if anyone ever "tidies up" a sign, and it is written
   separately from the value test so the failure message says what broke. *)
let test_a_hedge_contributes_negative_risk () =
  let t = get (A.compute ~weights:hedged_w ~covariance:hedged_cov) in
  Alcotest.(check bool)
    "the hedging leg reduces portfolio risk" true
    (Float.is_negative (A.component t).(1));
  Alcotest.(check bool)
    "but its standalone risk is positive" true
    (Float.( > ) (A.standalone t).(1) 0.0)

(* Euler on something with no round numbers in it, so the identity is being
   checked rather than a coincidence of the fixture.

     sigma = [0.30; 0.18; 0.11], rho_12 = 0.6, rho_13 = -0.25, rho_23 = 0.10
     w     = [0.5; -0.3; 0.2]  (a short leg, and |w| summing to one)

   The point is not the values, which are not hand-derivable, but that
   sum(component) = sigma_p and sum(percent) = 1 hold to float precision on an
   arbitrary matrix. *)
let messy_cov =
  let s = [| 0.30; 0.18; 0.11 |] in
  let rho = [| [| 1.0; 0.6; -0.25 |]; [| 0.6; 1.0; 0.10 |]; [| -0.25; 0.10; 1.0 |] |] in
  mat
    (Array.init 3 ~f:(fun i -> Array.init 3 ~f:(fun j -> rho.(i).(j) *. s.(i) *. s.(j))))

let messy_w = [| 0.5; -0.3; 0.2 |]

let test_euler_identity_on_an_arbitrary_matrix () =
  let t = get (A.compute ~weights:messy_w ~covariance:messy_cov) in
  Alcotest.check feq "components sum to sigma_p" (A.portfolio_stddev t)
    (sum (A.component t));
  Alcotest.check feq "percentages sum to one" 1.0 (sum (A.percent t));
  Alcotest.check feq "euler_residual is zero" 0.0 (A.euler_residual t)

(* Component VaR inherits the identity, because parametric VaR is a constant
   multiple of sigma_p and the constant does not depend on the weights.

   At 95%, z = 1.6448536269514729:
     sigma_p = 0.11180339887498948
     VaR_p   = z * sigma_p = 0.18390022614502866
     comp    = [0.089442719...; 0.022360679...]
     cVaR    = z * comp    = [0.147120181...; 0.036780045...]  sum = VaR_p

   This is the property that makes an instrument-scoped VaR limit well posed,
   so it is asserted against Risk_metrics' own portfolio number rather than
   against a literal -- if the two ever disagree, the limit is measuring
   something that is not a share of the total. *)
let test_component_var_sums_to_parametric_var () =
  let t = get (A.compute ~weights:uncorrelated_w ~covariance:uncorrelated_cov) in
  let cvar = A.component_var t ~confidence:0.95 in
  (* 1e-9 rather than 1e-12 here alone: these literals come from a different
     implementation of the normal quantile than Owl's, and the two agree to
     about eleven digits. The identity below is what this test is really for,
     and it is checked at full precision. *)
  Alcotest.(check (array feq_loose))
    "component VaR"
    [| 0.1471201810228229; 0.03678004525570573 |]
    cvar;
  Alcotest.check feq "sums to the portfolio parametric VaR"
    (Ohcamel.Risk_metrics.portfolio_parametric_var ~weights:uncorrelated_w
       ~covariance:uncorrelated_cov ~confidence:0.95)
    (sum cvar)

(* A single-name book is entirely its own risk. Trivial, and worth pinning:
   it is the case a percentage display is most likely to divide by the wrong
   thing. *)
let test_single_instrument () =
  let t = get (A.compute ~weights:[| 1.0 |] ~covariance:(mat [| [| 0.09 |] |])) in
  Alcotest.check feq "sigma_p is the instrument's own vol" 0.3 (A.portfolio_stddev t);
  Alcotest.(check (array feq)) "all of it" [| 1.0 |] (A.percent t);
  Alcotest.check feq "nothing to diversify" 1.0 (A.diversification_ratio t)

(* A book holding nothing has no risk to divide up.

   [None], not a record of zeros: every field would be a division by
   sigma_p. The engine surfaces this the same way it surfaces a warming-up VaR
   -- as unknown -- because a decomposition rendered as all-zeros reads as "no
   position is contributing risk", which is a different and false claim. *)
let test_flat_book_is_none () =
  Alcotest.(check bool)
    "flat book" true
    (Option.is_none (A.compute ~weights:[| 0.0; 0.0 |] ~covariance:uncorrelated_cov))

(* The float-noise case, which is the one that produces fabricated numbers
   rather than obvious ones.

   Two names with correlation exactly -1 and equal vol, held in exactly
   offsetting weights: the true portfolio variance is zero, but it is computed
   as a sum of cancelling products and lands somewhere around 1e-18 instead.
   sqrt of that is ~1e-9 -- not zero, so an equality test lets it through, and
   then every marginal is a rounding residue over another one. The threshold in
   [compute] is relative to the largest single-name vol, so it catches this. *)
let test_near_degenerate_book_is_none () =
  Alcotest.(check bool)
    "offsetting legs of a perfectly anti-correlated pair" true
    (Option.is_none (A.compute ~weights:[| 0.5; 0.5 |] ~covariance:hedged_cov))

let test_dimension_mismatch_raises () =
  (match A.compute ~weights:[| 0.5; 0.3; 0.2 |] ~covariance:uncorrelated_cov with
  | exception Invalid_argument _ -> ()
  | exception e -> Alcotest.failf "expected Invalid_argument, got %s" (Exn.to_string e)
  | _ -> Alcotest.fail "a 3-vector against a 2x2 matrix should not be accepted");
  match A.compute ~weights:[||] ~covariance:uncorrelated_cov with
  | exception Invalid_argument _ -> ()
  | exception e -> Alcotest.failf "expected Invalid_argument, got %s" (Exn.to_string e)
  | _ -> Alcotest.fail "an empty book should not be accepted"

(* Diversification ratio is bounded below by one, by the triangle inequality,
   and equals one only when everything moves together. Checked on a perfectly
   correlated pair, where the book really is just one bet. *)
let test_diversification_ratio_floor () =
  let all_the_same = mat [| [| 0.04; 0.04 |]; [| 0.04; 0.04 |] |] in
  let t = get (A.compute ~weights:[| 0.6; 0.4 |] ~covariance:all_the_same) in
  Alcotest.check feq_loose "perfectly correlated book diversifies nothing" 1.0
    (A.diversification_ratio t);
  let t' = get (A.compute ~weights:messy_w ~covariance:messy_cov) in
  Alcotest.(check bool)
    "and a real book diversifies something" true
    (Float.( > ) (A.diversification_ratio t') 1.0)

let suite =
  ( "attribution",
    [
      Alcotest.test_case "uncorrelated pair, by hand" `Quick test_uncorrelated;
      Alcotest.test_case "perfect hedge, by hand" `Quick test_perfect_hedge;
      Alcotest.test_case "a hedge contributes negative risk" `Quick
        test_a_hedge_contributes_negative_risk;
      Alcotest.test_case "Euler identity on an arbitrary matrix" `Quick
        test_euler_identity_on_an_arbitrary_matrix;
      Alcotest.test_case "component VaR sums to parametric VaR" `Quick
        test_component_var_sums_to_parametric_var;
      Alcotest.test_case "single instrument" `Quick test_single_instrument;
      Alcotest.test_case "flat book has no decomposition" `Quick test_flat_book_is_none;
      Alcotest.test_case "near-degenerate book has no decomposition" `Quick
        test_near_degenerate_book_is_none;
      Alcotest.test_case "dimension mismatch raises" `Quick test_dimension_mismatch_raises;
      Alcotest.test_case "diversification ratio is at least one" `Quick
        test_diversification_ratio_floor;
    ] )
