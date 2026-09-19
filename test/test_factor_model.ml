(* Unit tests for factor_model.ml.

   Five kinds of test.

     BY HAND         a four-row, one-factor regression whose alpha, beta and
                     residual variance can be read off the page, and a
                     two-asset book whose systematic, idiosyncratic and total
                     variance, model VaR and sample variance can too.

     EXACT RECOVERY  a 256-row, five-factor case built from Walsh-Hadamard
                     columns. Those columns are mutually orthogonal and each is
                     orthogonal to the intercept, so when y is a known alpha,
                     known betas and a FURTHER Walsh-Hadamard column, OLS must
                     return exactly the planted alpha and betas and leave
                     exactly the planted column as the residual. No RNG, no
                     tolerance chosen to make a noisy estimate pass.

     THE SOLVE       a near-collinear design just inside the conditioning
                     floor, where QR recovers the betas to 1e-12 and the
                     normal equations do not.

     IDENTITIES      on the exact case, at 1e-9: residuals orthogonal to the
                     intercept and every factor, fitted plus residual equal to
                     y, contributions summing to the systematic variance, and
                     systematic plus idiosyncratic equal to the total.

     REFUSALS        a duplicated factor and a near-collinear one (the
                     conditioning check), lengths that differ, too few rows,
                     no factors, no residual degree of freedom, an infinite
                     input, a held name with no fit, and every way a nan could
                     reach a risk figure. A refusal is an Error, never an
                     exception and never an Ok with a nan in it.

   Variances in dollars squared run to 1e8 and beyond, where an absolute 1e-9
   is below one ulp; those identities are checked at 1e-9 RELATIVE, via
   [check_rel]. Everything in return space is checked absolutely. *)

open Core
module FM = Ohcamel.Factor_model

let feq = Alcotest.float 1e-12
let ident = Alcotest.float 1e-9

let check_rel msg expected actual =
  let tolerance = 1e-9 *. Float.max 1.0 (Float.abs expected) in
  Alcotest.check (Alcotest.float tolerance) msg expected actual

let expect_error ~substring = function
  | Ok _ -> Alcotest.failf "expected an Error naming %S, got Ok" substring
  | Error reason ->
      Alcotest.(check bool)
        (sprintf "%S names %S" reason substring)
        true
        (String.is_substring reason ~substring)

let ok_exn = function Ok x -> x | Error reason -> Alcotest.fail reason

let expect_invalid_arg ~substring f =
  match f () with
  | _ -> Alcotest.failf "expected Invalid_argument naming %S, got a result" substring
  | exception Invalid_argument reason ->
      Alcotest.(check bool)
        (sprintf "%S names %S" reason substring)
        true
        (String.is_substring reason ~substring)

(* BY HAND: one factor, four rows.

     f = [0.01; 0.02; 0.03; 0.04]      mean 0.025
     y = [0.01; 0.03; 0.02; 0.04]      mean 0.025

   Deviations from the means:

     f: -0.015, -0.005, 0.005, 0.015
     y: -0.015,  0.005, -0.005, 0.015

     sum dev_f * dev_y = 2.25e-4 - 0.25e-4 - 0.25e-4 + 2.25e-4 = 4e-4
     sum dev_f ^ 2     = 2.25e-4 + 0.25e-4 + 0.25e-4 + 2.25e-4 = 5e-4

     beta  = 4e-4 / 5e-4          = 0.8
     alpha = 0.025 - 0.8 * 0.025  = 0.005

   Fitted 0.013, 0.021, 0.029, 0.037, so the residuals are

     -0.003, 0.009, -0.009, 0.003     RSS = 9e-6 + 8.1e-5 + 8.1e-5 + 9e-6 = 1.8e-4

   and the residual variance divides by T - K - 1 = 4 - 1 - 1 = 2:

     1.8e-4 / 2 = 9e-5

   Four rows is far below the 120 [fit_one] demands, which is why this goes
   through For_testing; the public function must refuse the same data. *)
let one_f = [| 0.01; 0.02; 0.03; 0.04 |]
let one_y = [| 0.01; 0.03; 0.02; 0.04 |]

let test_one_factor_by_hand () =
  let fit = FM.For_testing.fit_unchecked ~y:one_y ~factors:[| one_f |] in
  Alcotest.check feq "alpha = 0.025 - 0.8 * 0.025" 0.005 fit.alpha;
  Alcotest.(check int) "one beta" 1 (Array.length fit.betas);
  Alcotest.check feq "beta = 4e-4 / 5e-4" 0.8 fit.betas.(0);
  Alcotest.check feq "residual variance = 1.8e-4 / (4 - 1 - 1)" 9e-5 fit.residual_variance;
  Alcotest.(check int) "four observations" 4 fit.observations;
  (* The same rows through the public door: 4 < 120. *)
  expect_error ~substring:"fewer than the 120" (FM.fit_one ~y:one_y ~factors:[| one_f |])

(* EXACT RECOVERY: the Walsh-Hadamard case.

   The Sylvester Hadamard matrix of order 256 has entry
   H(t, c) = (-1)^popcount(t land c). Its columns are mutually orthogonal,
   column 0 is all ones (the intercept), and every other column holds 128 of
   +1 and 128 of -1, so it sums to zero: orthogonal to the intercept too.

   Factors are columns 1..5, offset and scaled to daily sizes,
   f_k = m_k + s_k H(., k+1):

     market    m  0.0004   s 0.010
     size         -0.0002    0.006
     value         0.0001    0.005
     momentum      0.0003    0.007
     rates        -0.002     0.060 (pp)

   so factor k has mean m_k and population variance s_k^2 exactly, and the
   standardised design is the +/-1 columns themselves: Z'Z = 256 I, every
   singular value 16, sigma_min / sigma_max = 1.

   Instrument A is  0.0002 + sum_k beta_A,k f_k + 0.012 H(., 6)
   Instrument B is -0.0001 + sum_k beta_B,k f_k + 0.020 H(., 7)

   Columns 6 and 7 are orthogonal to the intercept and to columns 1..5, so the
   OLS projection removes nothing of them: the betas come back exactly, the
   residual is exactly the planted column, and

     RSS_A = 256 * 0.012^2 = 0.036864     / (256 - 5 - 1) = 1.47456e-4
     RSS_B = 256 * 0.020^2 = 0.1024       / 250           = 4.096e-4

   The offsets are there for alpha. The solve returns the intercept of the
   CENTRED design, mean(y), and alpha = mean(y) - sum_k beta_k m_k. For A the
   centring term is

     1.1(0.0004) + 0.3(-0.0002) - 0.2(0.0001) + 0.15(0.0003) - 0.02(-0.002)
       = 0.00044 - 0.00006 - 0.00002 + 0.000045 + 0.00004 = 0.000445,

   spread over all five factors, so a centring that dropped a factor, or all
   of them, would miss alpha by up to 4.45e-4. *)
let rows = 256
let hadamard ~row ~col = if Int.popcount (row land col) % 2 = 0 then 1.0 else -1.0

let column ~col ~offset ~scale =
  Array.init rows ~f:(fun row -> offset +. (scale *. hadamard ~row ~col))

let offsets = [| 0.0004; -0.0002; 0.0001; 0.0003; -0.002 |]
let scales = [| 0.010; 0.006; 0.005; 0.007; 0.060 |]

let wh_factors () =
  Array.mapi scales ~f:(fun k scale -> column ~col:(k + 1) ~offset:offsets.(k) ~scale)

let planted ~alpha ~betas ~residual_col ~residual_scale =
  let factors = wh_factors () in
  Array.init rows ~f:(fun t ->
      Array.foldi betas ~init:alpha ~f:(fun k acc beta ->
          acc +. (beta *. factors.(k).(t)))
      +. (residual_scale *. hadamard ~row:t ~col:residual_col))

let alpha_a = 0.0002
let betas_a = [| 1.10; 0.30; -0.20; 0.15; -0.02 |]
let alpha_b = -0.0001
let betas_b = [| 0.80; -0.40; 0.50; -0.10; 0.03 |]
let y_a () = planted ~alpha:alpha_a ~betas:betas_a ~residual_col:6 ~residual_scale:0.012
let y_b () = planted ~alpha:alpha_b ~betas:betas_b ~residual_col:7 ~residual_scale:0.020

let test_walsh_hadamard_recovers_every_coefficient () =
  let check name ~y ~alpha ~betas ~residual_variance =
    let fit = ok_exn (FM.fit_one ~y ~factors:(wh_factors ())) in
    Alcotest.check ident (name ^ ": alpha") alpha fit.alpha;
    Array.iteri betas ~f:(fun k beta ->
        Alcotest.check ident (sprintf "%s: beta %d" name k) beta fit.betas.(k));
    Alcotest.check ident
      (name ^ ": residual variance")
      residual_variance fit.residual_variance;
    Alcotest.(check int) (name ^ ": all 256 rows used") 256 fit.observations
  in
  (* 256 * 0.012^2 / 250 and 256 * 0.020^2 / 250, as derived above. *)
  check "A" ~y:(y_a ()) ~alpha:alpha_a ~betas:betas_a ~residual_variance:1.47456e-4;
  check "B" ~y:(y_b ()) ~alpha:alpha_b ~betas:betas_b ~residual_variance:4.096e-4

(* IDENTITIES of the fit, at 1e-9.

   With residual r_t = y_t - alpha_hat - sum_k beta_hat_k f_kt:

   - OLS's normal equations say X'r = 0: sum_t r_t = 0 (the intercept) and
     sum_t r_t f_kt = 0 for every k. A beta off by d would leave
     d * sum_t f_kt^2 = d * 256 s_k^2 in the k-th sum -- 0.0256 d for the
     market -- so 1e-9 catches an error in the eighth digit.
   - fitted plus the planted residual 0.012 H(., 6) equals y, row by row;
     equivalently the residual IS the planted column.
   - sum r_t^2 / (256 - 5 - 1) is the reported residual variance. *)
let test_walsh_hadamard_fit_identities () =
  let y = y_a () in
  let factors = wh_factors () in
  let fit = ok_exn (FM.fit_one ~y ~factors) in
  let fitted t =
    Array.foldi fit.betas ~init:fit.alpha ~f:(fun k acc b ->
        acc +. (b *. factors.(k).(t)))
  in
  let residual = Array.init rows ~f:(fun t -> y.(t) -. fitted t) in
  Alcotest.check ident "residuals orthogonal to the intercept" 0.0
    (Array.fold residual ~init:0.0 ~f:( +. ));
  Array.iteri factors ~f:(fun k f ->
      Alcotest.check ident
        (sprintf "residuals orthogonal to factor %d" k)
        0.0
        (Array.foldi residual ~init:0.0 ~f:(fun t acc r -> acc +. (r *. f.(t)))));
  for t = 0 to rows - 1 do
    Alcotest.check ident
      (sprintf "fitted + residual = y at row %d" t)
      y.(t)
      (fitted t +. (0.012 *. hadamard ~row:t ~col:6))
  done;
  Alcotest.check ident "RSS / (T - K - 1) is the residual variance" fit.residual_variance
    (Array.fold residual ~init:0.0 ~f:(fun acc r -> acc +. (r *. r)) /. 250.0)

(* THE SOLVE: a near-collinear design, pinned to QR.

   Three factors from Sylvester columns H1, H2, H3, with y exactly linear in
   them -- no residual at all:

     a = 0.0004 + 0.01 H1
     b = -0.0002 + 0.01 (H1 + eps H2)
     c = 0.006 H3
     y = 0.0002 + 1.1 a - 0.3 b + 0.5 c

   Standardised, z_a = H1, z_b = (H1 + eps H2) / sqrt(1 + eps^2) and z_c = H3,
   so Z'Z / 256 is the correlation matrix [[1, rho, 0], [rho, 1, 0], [0, 0, 1]]
   with rho = 1 / sqrt(1 + eps^2). Its eigenvalues are 1 + rho, 1 - rho and 1,
   and with rho = cos theta, tan theta = eps,

     sigma_min / sigma_max = sqrt((1 - rho) / (1 + rho)) = tan(theta / 2).

   The ratio is exactly 0.01 at eps = tan(2 arctan 0.01) = 0.020002. So

     eps = 0.02     theta/2 = 0.0099987, ratio = 0.0099990   refused
     eps = 0.02001  theta/2 = 0.0100037, ratio = 0.0100040   accepted

   and the accepted design's betas for a and b have a variance-inflation
   factor of 1 + 1/eps^2 = 2,498.5, inside the 10^4 bound.

   WHY 1e-12 AND NOT 1e-9. Inside the 0.01 floor cond([1 | Z]) <= 100, so the
   normal equations square it to at most 10^4 and lose about four digits:
   swept over sixty eps from 0.02001 to 0.0206, they missed these betas by
   4e-13 to 3e-11 -- never by 1e-9 -- while QR missed by 1e-15 to 3e-14. No
   accepted design can separate the two at 1e-9. At 1e-12 they separate: at
   eps = 0.02001 QR misses by about 1e-14 and the normal equations (LU, an
   explicit inverse, or Cholesky, on the standardised or the raw design) by
   1.2e-11 to 2.1e-11. Replacing the QR solve with the normal equations makes
   this test fail; that was checked by mutation. *)
let near_collinear ~eps =
  let h col = Array.init rows ~f:(fun row -> hadamard ~row ~col) in
  let h1 = h 1 and h2 = h 2 and h3 = h 3 in
  let a = Array.init rows ~f:(fun t -> 0.0004 +. (0.01 *. h1.(t))) in
  let b =
    Array.init rows ~f:(fun t -> -0.0002 +. (0.01 *. (h1.(t) +. (eps *. h2.(t)))))
  in
  let c = Array.init rows ~f:(fun t -> 0.006 *. h3.(t)) in
  let y =
    Array.init rows ~f:(fun t ->
        0.0002 +. (1.1 *. a.(t)) -. (0.3 *. b.(t)) +. (0.5 *. c.(t)))
  in
  (y, [| a; b; c |])

let test_a_near_collinear_design_is_solved_by_qr () =
  let pin = Alcotest.float 1e-12 in
  let y, factors = near_collinear ~eps:0.02001 in
  let fit = ok_exn (FM.fit_one ~y ~factors) in
  Alcotest.check pin "alpha = 0.0002" 0.0002 fit.alpha;
  Alcotest.check pin "beta_a = 1.1" 1.1 fit.betas.(0);
  Alcotest.check pin "beta_b = -0.3" (-0.3) fit.betas.(1);
  Alcotest.check pin "beta_c = 0.5" 0.5 fit.betas.(2)

(* The factor covariance of the Walsh-Hadamard factors, by hand: every
   column's deviations from its mean m_k are +/-s_k, and every pair of +/-1
   columns is orthogonal -- the offsets do not enter a covariance -- so with
   the population divisor

     Sigma_f = diag(0.010^2, 0.006^2, 0.005^2, 0.007^2, 0.060^2)
             = diag(1e-4, 3.6e-5, 2.5e-5, 4.9e-5, 3.6e-3)

   with zeros off the diagonal. *)
let test_walsh_hadamard_factor_covariance () =
  let c = FM.factor_covariance ~factors:(wh_factors ()) in
  let expected = [| 1e-4; 3.6e-5; 2.5e-5; 4.9e-5; 3.6e-3 |] in
  Alcotest.(check (pair int int)) "5 x 5" (5, 5) (Owl.Mat.shape c);
  for i = 0 to 4 do
    for j = 0 to 4 do
      Alcotest.check feq
        (sprintf "Sigma_f[%d][%d]" i j)
        (if i = j then expected.(i) else 0.0)
        (Owl.Mat.get c i j)
    done
  done

(* IDENTITIES of the risk, on the two fitted Walsh-Hadamard names, held at
   x = (1.5e6, -0.5e6) -- a long and a short.

   b_k = 1.5e6 beta_A,k - 0.5e6 beta_B,k:

     market    1.65e6  - 0.4e6   =  1.25e6
     size      0.45e6  + 0.2e6   =  0.65e6
     value    -0.3e6   - 0.25e6  = -0.55e6
     momentum  0.225e6 + 0.05e6  =  0.275e6
     rates    -0.03e6  - 0.015e6 = -0.045e6

   On the diagonal covariance above, contribution_k = b_k^2 s_k^2:

     1.5625e12 * 1e-4   = 1.5625e8
     4.225e11  * 3.6e-5 = 1.521e7
     3.025e11  * 2.5e-5 = 7.5625e6
     7.5625e10 * 4.9e-5 = 3.705625e6
     2.025e9   * 3.6e-3 = 7.29e6          systematic = 1.90018125e8

     idiosyncratic = 2.25e12 * 1.47456e-4 + 2.5e11 * 4.096e-4
                   = 3.31776e8 + 1.024e8  = 4.34176e8
     total         = 1.90018125e8 + 4.34176e8 = 6.24194125e8

   A diagonal covariance would let a wrong split -- b_k^2 Sigma_kk, say -- pass
   the sum. So the split is checked again on a covariance with off-diagonal
   terms: the factor covariance over rows 0..100 only, where the columns are no
   longer orthogonal. Over those 101 rows H(., 1) and H(., 2) each sum to 1
   (the last five rows, 96..100, run + - + - + and + + - - +), and their
   product, H(., 3), sums to 1 too (+ - - + +); the offsets drop out of a
   covariance, so

     Sigma[0][1] = 0.010 * 0.006 * (1/101 - 1/101^2) = 6e-5 * 100 / 10201

   There each contribution must be b_k * sum_j Sigma_kj b_j, computed here by
   an independent loop, and they must still sum to b' Sigma b. *)
let test_walsh_hadamard_risk_identities () =
  let factors = wh_factors () in
  let fit_a = ok_exn (FM.fit_one ~y:(y_a ()) ~factors) in
  let fit_b = ok_exn (FM.fit_one ~y:(y_b ()) ~factors) in
  let fits = [| Some fit_a; Some fit_b |] in
  let exposures = [| 1.5e6; -0.5e6 |] in
  let run covariance =
    ok_exn (FM.risk ~fits ~exposures ~covariance ~confidence:0.99 ())
  in
  let sum = Array.fold ~init:0.0 ~f:( +. ) in
  let r = run (FM.factor_covariance ~factors) in
  Array.iteri [| 1.25e6; 0.65e6; -0.55e6; 0.275e6; -0.045e6 |] ~f:(fun k b ->
      check_rel (sprintf "b_%d = sum_i x_i beta_ik" k) b r.exposures.(k));
  Array.iteri [| 1.5625e8; 1.521e7; 7.5625e6; 3.705625e6; 7.29e6 |] ~f:(fun k c ->
      check_rel (sprintf "contribution %d = b_k^2 s_k^2" k) c r.contributions.(k));
  check_rel "systematic, by hand" 1.90018125e8 r.systematic_variance;
  check_rel "idiosyncratic, by hand" 4.34176e8 r.idiosyncratic_variance;
  check_rel "total, by hand" 6.24194125e8 r.total_variance;
  check_rel "contributions sum to the systematic variance" r.systematic_variance
    (sum r.contributions);
  check_rel "systematic + idiosyncratic = total" r.total_variance
    (r.systematic_variance +. r.idiosyncratic_variance);
  (* The off-diagonal case. *)
  let head = Array.map factors ~f:(fun f -> Array.sub f ~pos:0 ~len:101) in
  let dense = FM.factor_covariance ~factors:head in
  Alcotest.check feq "Sigma[0][1] over 101 rows = 6e-5 * 100 / 10201"
    (6e-5 *. 100.0 /. 10201.0)
    (Owl.Mat.get dense 0 1);
  let r = run dense in
  let b = r.exposures in
  let sigma_b k = sum (Array.init 5 ~f:(fun j -> Owl.Mat.get dense k j *. b.(j))) in
  Array.iteri r.contributions ~f:(fun k c ->
      check_rel (sprintf "contribution %d = b_k (Sigma b)_k" k) (b.(k) *. sigma_b k) c);
  check_rel "b' Sigma b by a double loop"
    (sum (Array.init 5 ~f:(fun k -> b.(k) *. sigma_b k)))
    r.systematic_variance;
  check_rel "contributions sum to the systematic variance" r.systematic_variance
    (sum r.contributions);
  check_rel "systematic + idiosyncratic = total" r.total_variance
    (r.systematic_variance +. r.idiosyncratic_variance)

(* nan ROWS ARE DROPPED.

   Instrument A's y with nan at ten rows, and rates (factor 4) nan on the
   trailing three rows, 253..255 -- ruling 2's unpublished DGS10. The row sets
   do not overlap, so 256 - 10 - 3 = 243 rows survive, and the fit on them
   must be the fit on those 243 rows passed clean: the same computation on the
   same numbers, so the same coefficients.

   [factor_covariance] drops the three rates rows and nothing else (the y
   nans are not its business), so it equals the covariance of rows 0..252,
   whose diagonal is known by hand. The offset shifts the mean and not the
   variance, so only the +/-1 column matters. H(., 1) over rows 0..252 holds
   127 of +1 and 126 of -1, so market's deviations are +/-0.010 around a mean
   0.0004 + 0.010 / 253, and

     var = 1e-4 - (0.010 / 253)^2 = 1e-4 (1 - 1/253^2)

   H(., 5) (rates) repeats + - + - - + - + every 8 rows; 253 = 31 * 8 + 5,
   and the last five rows run + - + - -, so its sum is -1 and

     var = 3.6e-3 (1 - 1/253^2)

   With divisor 252 both would be larger by 253/252: this pins the
   population divisor over the rows kept. *)
let test_nan_rows_are_dropped () =
  let y_nan_rows = [ 3; 17; 29; 64; 65; 111; 150; 199; 222; 240 ] in
  let rates_nan_rows = [ 253; 254; 255 ] in
  let y = y_a () in
  List.iter y_nan_rows ~f:(fun t -> y.(t) <- Float.nan);
  let factors = wh_factors () in
  List.iter rates_nan_rows ~f:(fun t -> factors.(4).(t) <- Float.nan);
  let kept =
    List.filter (List.init rows ~f:Fn.id) ~f:(fun t ->
        not (List.mem y_nan_rows t ~equal:Int.equal || t >= 253))
    |> Array.of_list
  in
  let take column = Array.map kept ~f:(fun t -> column.(t)) in
  let dirty = ok_exn (FM.fit_one ~y ~factors) in
  let clean = ok_exn (FM.fit_one ~y:(take y) ~factors:(Array.map factors ~f:take)) in
  Alcotest.(check int) "observations = 256 - 13" 243 dirty.observations;
  Alcotest.(check int) "the clean subset has 243 rows" 243 clean.observations;
  Alcotest.check feq "alpha as on the clean subset" clean.alpha dirty.alpha;
  Array.iteri clean.betas ~f:(fun k beta ->
      Alcotest.check feq (sprintf "beta %d as on the clean subset" k) beta dirty.betas.(k));
  Alcotest.check feq "residual variance as on the clean subset" clean.residual_variance
    dirty.residual_variance;
  let with_nan = FM.factor_covariance ~factors in
  let head =
    FM.factor_covariance
      ~factors:(Array.map factors ~f:(fun f -> Array.sub f ~pos:0 ~len:253))
  in
  for i = 0 to 4 do
    for j = 0 to 4 do
      Alcotest.check feq
        (sprintf "Sigma_f[%d][%d] drops the three rates rows" i j)
        (Owl.Mat.get head i j) (Owl.Mat.get with_nan i j)
    done
  done;
  let shrink = 1.0 -. (1.0 /. (253.0 *. 253.0)) in
  Alcotest.check feq "market variance = 1e-4 (1 - 1/253^2)" (1e-4 *. shrink)
    (Owl.Mat.get with_nan 0 0);
  Alcotest.check feq "rates variance = 3.6e-3 (1 - 1/253^2)" (3.6e-3 *. shrink)
    (Owl.Mat.get with_nan 4 4)

(* REFUSAL: the conditioning check.

   Factor 2 is a copy of factor 0, so the standardised design has two
   identical columns, rank 2 of 3, and sigma_min is zero up to rounding: far
   below 0.01 of sigma_max. 256 rows clear the minimum, so this is the
   conditioning check refusing and not the row count.

   The near-collinear design above at eps = 0.02 has a ratio of 0.0099990,
   just under the floor, and is refused; at eps = 0.02001 (0.0100040) the
   test above accepts it. Together they put the floor at 0.01, not 1e-8.

   A factor that never moves is the extreme case: it cannot be standardised
   at all (its standard deviation is zero), and is refused as a singular
   design rather than divided by. *)
let test_refuses_an_ill_conditioned_design () =
  let factors = wh_factors () in
  expect_error ~substring:"ill-conditioned over the 256 rows used: sigma_min / sigma_max"
    (FM.fit_one ~y:(y_a ()) ~factors:[| factors.(0); factors.(1); factors.(0) |]);
  let y, factors_near = near_collinear ~eps:0.02 in
  expect_error ~substring:"sigma_min / sigma_max = 0.009999, below 0.01"
    (FM.fit_one ~y ~factors:factors_near);
  expect_error ~substring:"factor 1 does not move"
    (FM.fit_one ~y:(y_a ()) ~factors:[| factors.(0); Array.create ~len:rows 0.001 |])

(* REFUSAL: an infinite input is an Error, never an exception.

   nan is missing and dropped; inf is a data error (a zero close upstream),
   and LAPACK's svdvals raises Invalid_argument on it. Uncaught, that would
   abort the fit of every name in the panel. So:

   - factor 2 = +inf at row 50 is refused, naming factor 2 and row 50;
   - y = -inf at row 77 is refused, naming y and row 77 -- the CALLER's row:
     y is also nan at row 3, so row 77 sits at index 76 of the rows kept, and
     the message must say 77;
   - a y that is finite but of order 1e200 is refused by the last guard: its
     residuals are about 1.2e198, whose squares overflow to inf, so the
     residual variance is not a number the fit can return. *)
let test_an_infinite_input_is_an_error () =
  let factors = wh_factors () in
  factors.(2).(50) <- Float.infinity;
  expect_error ~substring:"factor 2 is inf at row 50" (FM.fit_one ~y:(y_a ()) ~factors);
  let y = y_a () in
  y.(3) <- Float.nan;
  y.(77) <- Float.neg_infinity;
  expect_error ~substring:"y is -inf at row 77" (FM.fit_one ~y ~factors:(wh_factors ()));
  let huge = Array.map (y_a ()) ~f:(fun v -> v *. 1e200) in
  expect_error ~substring:"is not finite" (FM.fit_one ~y:huge ~factors:(wh_factors ()))

(* REFUSAL, through For_testing: no factors. K = 0 is not a factor model, and
   fit_unchecked, which skips only the minimum-observations rule, still
   raises. *)
let test_fit_unchecked_refuses_no_factors () =
  expect_invalid_arg ~substring:"there are no factors" (fun () ->
      FM.For_testing.fit_unchecked ~y:one_y ~factors:[||])

(* REFUSAL, through For_testing: no residual degree of freedom. Two rows and
   one factor: n = K + 1 = 2, so RSS / (n - K - 1) would divide by zero. *)
let test_fit_unchecked_refuses_no_residual_degree_of_freedom () =
  expect_invalid_arg ~substring:"2 observations cannot fit an intercept and K = 1"
    (fun () ->
      FM.For_testing.fit_unchecked ~y:[| 0.01; 0.03 |] ~factors:[| [| 0.01; 0.02 |] |])

(* REFUSAL: lengths that differ. y has 256 rows and factor 1 has 255, and the
   error names the factor. *)
let test_refuses_lengths_that_differ () =
  let factors = wh_factors () in
  expect_error ~substring:"factor 1 has 255 rows, and y has 256"
    (FM.fit_one ~y:(y_a ())
       ~factors:[| factors.(0); Array.sub factors.(1) ~pos:0 ~len:255 |])

(* REFUSAL: a held name with no fit.

   Instrument 1 holds 2e6 and has no fit, so the book's factor risk is
   unknown, and the error names index 1 for the caller to map to a symbol.

   The same book with instrument 1 FLAT is fine: a name the book does not hold
   has no risk to be unknown. It is then one name, beta 1.0, residual variance
   1e-4, x = 1e6, var(f) = 2e-4:

     b = 1e6;  systematic = 1e12 * 2e-4 = 2e8;  idiosyncratic = 1e12 * 1e-4 = 1e8
     total = 3e8 *)
let one_name =
  { FM.Fit.alpha = 0.0; betas = [| 1.0 |]; residual_variance = 1e-4; observations = 250 }

let var_f = Owl.Mat.of_array [| 2e-4 |] 1 1

let test_refuses_a_held_name_with_no_fit () =
  expect_error ~substring:"instrument 1 has a nonzero exposure"
    (FM.risk ~fits:[| Some one_name; None |] ~exposures:[| 1e6; 2e6 |] ~covariance:var_f
       ~confidence:0.99 ());
  let r =
    ok_exn
      (FM.risk ~fits:[| Some one_name; None |] ~exposures:[| 1e6; 0.0 |] ~covariance:var_f
         ~confidence:0.99 ())
  in
  check_rel "b = 1e6" 1e6 r.exposures.(0);
  check_rel "systematic = 1e12 * 2e-4" 2e8 r.systematic_variance;
  check_rel "idiosyncratic = 1e12 * 1e-4" 1e8 r.idiosyncratic_variance;
  check_rel "total = 3e8" 3e8 r.total_variance

(* nan NEVER REACHES THE SAMPLE VARIANCE: it reads only the held names.

   The asset covariance has nan throughout row 2 (and so, by symmetry, in
   column 2): instrument 1 had no observation on the common rows. With
   x = (1e6, 0) the book does not hold it, and summing over every name would
   give 0 * nan = nan. Over the held names alone,

     x'Ax = 1e12 * 3e-4 = 3e8,

   and the model's figures are the flat case's above: total 3e8.

   Hold instrument 1 as well, x = (1e6, 2e6), and entry (0, 1) is one the
   figure needs, so the answer is an Error naming it, not a nan. *)
let test_the_sample_variance_reads_only_held_names () =
  let a = Owl.Mat.of_array [| 3e-4; Float.nan; Float.nan; Float.nan |] 2 2 in
  let r =
    ok_exn
      (FM.risk ~fits:[| Some one_name; None |] ~exposures:[| 1e6; 0.0 |] ~covariance:var_f
         ~asset_covariance:a ~confidence:0.99 ())
  in
  (match r.sample_variance with
  | Some v -> check_rel "x'Ax over the one held name = 1e12 * 3e-4" 3e8 v
  | None -> Alcotest.fail "an asset covariance was given, so x'Ax is known");
  check_rel "total = 3e8, as flat" 3e8 r.total_variance;
  let half =
    {
      FM.Fit.alpha = 0.0;
      betas = [| 0.5 |];
      residual_variance = 4e-4;
      observations = 250;
    }
  in
  expect_error ~substring:"entry (0, 1) is nan, and instruments 0 and 1 are both held"
    (FM.risk
       ~fits:[| Some one_name; Some half |]
       ~exposures:[| 1e6; 2e6 |] ~covariance:var_f ~asset_covariance:a ~confidence:0.99 ())

(* NEVER Ok WITH A nan IN IT.

   Nothing before the last guard reads the factor covariance's entries or a
   fit's residual variance. A nan factor variance makes b' Sigma b nan; a nan
   residual variance makes the idiosyncratic term nan; either would reach the
   total and the model VaR. Both are Errors. *)
let test_a_nan_figure_is_an_error () =
  expect_error ~substring:"factor risk is not finite"
    (FM.risk ~fits:[| Some one_name |] ~exposures:[| 1e6 |]
       ~covariance:(Owl.Mat.of_array [| Float.nan |] 1 1)
       ~confidence:0.99 ());
  expect_error ~substring:"factor risk is not finite"
    (FM.risk
       ~fits:[| Some { one_name with residual_variance = Float.nan } |]
       ~exposures:[| 1e6 |] ~covariance:var_f ~confidence:0.99 ())

(* BY HAND: the risk of a two-asset, one-factor book.

   Betas 1.0 and 0.5, residual variances 1e-4 and 4e-4, x = (1e6, 2e6),
   var(f) = 2e-4.

     b             = 1e6 * 1.0 + 2e6 * 0.5          = 2e6
     systematic    = b^2 var(f) = 4e12 * 2e-4        = 8e8   (the one contribution)
     idiosyncratic = 1e12 * 1e-4 + 4e12 * 4e-4
                   = 1e8 + 1.6e9                     = 1.7e9
     total         = 8e8 + 1.7e9                     = 2.5e9, sqrt = 50,000

   At 99%, z = Phi^-1(0.01) = -2.3263478740 (to ten places), so

     model_var = 2.3263478740 * 50,000 = 116,317.39370

   good to 2.5e-6 from the table's rounding, hence the 1e-5 tolerance.

   With A = [[3e-4, 1.5e-4], [1.5e-4, 6e-4]]:

     x'Ax = 1e12 * 3e-4 + 2 * (1e6 * 2e6) * 1.5e-4 + 4e12 * 6e-4
          = 3e8 + 6e8 + 2.4e9 = 3.3e9

   which exceeds the model's 2.5e9: the sample sees a residual correlation the
   model assumes away. Without A there is no sample figure. *)
let test_risk_by_hand () =
  let fits =
    [|
      Some one_name;
      Some
        {
          FM.Fit.alpha = 0.0;
          betas = [| 0.5 |];
          residual_variance = 4e-4;
          observations = 250;
        };
    |]
  in
  let exposures = [| 1e6; 2e6 |] in
  let asset_covariance = Owl.Mat.of_array [| 3e-4; 1.5e-4; 1.5e-4; 6e-4 |] 2 2 in
  let r =
    ok_exn
      (FM.risk ~fits ~exposures ~covariance:var_f ~asset_covariance ~confidence:0.99 ())
  in
  check_rel "b = 2e6" 2e6 r.exposures.(0);
  check_rel "systematic = 4e12 * 2e-4" 8e8 r.systematic_variance;
  check_rel "the one contribution is the systematic variance" 8e8 r.contributions.(0);
  check_rel "idiosyncratic = 1e8 + 1.6e9" 1.7e9 r.idiosyncratic_variance;
  check_rel "total = 2.5e9" 2.5e9 r.total_variance;
  Alcotest.check (Alcotest.float 1e-5) "model VaR = 2.3263478740 * 50,000" 116_317.39370
    r.model_var;
  (match r.sample_variance with
  | Some v -> check_rel "x'Ax = 3e8 + 6e8 + 2.4e9" 3.3e9 v
  | None -> Alcotest.fail "an asset covariance was given, so x'Ax is known");
  let without = ok_exn (FM.risk ~fits ~exposures ~covariance:var_f ~confidence:0.99 ()) in
  Alcotest.(check (option (float 0.0)))
    "no asset covariance, no sample figure" None without.sample_variance

let suite =
  ( "factor_model",
    [
      Alcotest.test_case "one factor by hand; fit_one refuses four rows" `Quick
        test_one_factor_by_hand;
      Alcotest.test_case "Walsh-Hadamard: every alpha and beta recovered exactly" `Quick
        test_walsh_hadamard_recovers_every_coefficient;
      Alcotest.test_case "Walsh-Hadamard: residuals orthogonal, fitted + residual = y"
        `Quick test_walsh_hadamard_fit_identities;
      Alcotest.test_case "a near-collinear design is solved by QR, to 1e-12" `Quick
        test_a_near_collinear_design_is_solved_by_qr;
      Alcotest.test_case "Walsh-Hadamard: the factor covariance by hand" `Quick
        test_walsh_hadamard_factor_covariance;
      Alcotest.test_case "Walsh-Hadamard: the Euler split sums; sys + idio = total" `Quick
        test_walsh_hadamard_risk_identities;
      Alcotest.test_case "nan rows are dropped, from the fit and the covariance" `Quick
        test_nan_rows_are_dropped;
      Alcotest.test_case "refuses an ill-conditioned design" `Quick
        test_refuses_an_ill_conditioned_design;
      Alcotest.test_case "refuses lengths that differ" `Quick
        test_refuses_lengths_that_differ;
      Alcotest.test_case "an infinite input is an Error, not an exception" `Quick
        test_an_infinite_input_is_an_error;
      Alcotest.test_case "fit_unchecked refuses no factors" `Quick
        test_fit_unchecked_refuses_no_factors;
      Alcotest.test_case "fit_unchecked refuses no residual degree of freedom" `Quick
        test_fit_unchecked_refuses_no_residual_degree_of_freedom;
      Alcotest.test_case "refuses a held name with no fit" `Quick
        test_refuses_a_held_name_with_no_fit;
      Alcotest.test_case "the sample variance reads only held names" `Quick
        test_the_sample_variance_reads_only_held_names;
      Alcotest.test_case "a nan figure is an Error, never Ok" `Quick
        test_a_nan_figure_is_an_error;
      Alcotest.test_case "two assets, one factor, by hand" `Quick test_risk_by_hand;
    ] )
