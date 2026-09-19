(* A4: the factor model, as pure code.

   Every figure risk_metrics.ml and attribution.ml produce is about the book's
   own return history: how much it has moved, and how that movement is shared
   between the names in it. None of them says WHY the book moves. A factor
   model answers that. It regresses each instrument on a small set of common
   factors, then prices the book's risk through the factor covariance, and it
   leaves a residual per name that it assumes no other name shares.

   Nothing here references Incremental, Graph, or Types.Factor. K, the number
   of factors, comes from the arrays; the caller decides what the columns mean
   and in what order. graph.ml runs [fit_one] and [factor_covariance] when a
   panel is written, outside stabilization, and only [risk] is cheap enough to
   sit on the tick path.

   THE REGRESSION (ruling 5)

   One ordinary-least-squares regression with intercept per instrument,

       y_t = alpha + sum_k beta_k f_kt + e_t,

   over the rows where y and every factor are present. A row with nan anywhere
   in it is dropped, and dropped whole: a regression cannot use half a row.

   Solved by Householder QR, never by the normal equations. Forming X'X squares
   the condition number of the design, so a design that QR solves to eight
   digits the normal equations solve to none; and an explicit inverse is the
   normal equations with extra rounding. QR factors X = QR with Q orthonormal,
   so the least-squares solution is the triangular solve R theta = Q'y, and the
   conditioning that solve sees is the design's own, not its square.

   The design QR factors is [1 | Z], where Z is the standardised factor matrix:
   each column centred and scaled to unit population variance. That is the same
   column space as [1 | F], so the fitted values and residuals are the same, and
   the coefficients come back by an exact change of variable,

       beta_k = gamma_k / s_k,        alpha = theta_0 - sum_k beta_k m_k,

   with m_k and s_k the column's mean and population standard deviation. The
   point of doing it this way is that the check below and the solve then look
   at the SAME matrix. Every column of Z is orthogonal to the intercept (it is
   centred), and every column has sum of squares T (unit variance), so the
   squared singular values of Z average exactly T. The intercept column's
   singular value, sqrt T, therefore lies between sigma_min(Z) and
   sigma_max(Z), and the condition number of [1 | Z] is sigma_max / sigma_min
   of Z -- precisely the ratio the refusal bounds. A fit this module returns
   was solved on a system whose conditioning it measured.

   At the 0.01 floor below that condition number is at most 100, so the normal
   equations would square it to 10^4 and lose about four digits QR keeps:
   near the floor they miss betas of order one by about 1e-11, where QR misses
   by about 1e-14. test_factor_model.ml's near-collinear case pins the solve
   at 1e-12, between the two.

   TWO REFUSALS, WITH A REASON EACH

   - Fewer than [min_observations] (120) rows. A five-factor regression on a
     few dozen days fits noise and reports it as exposure; the refusal reads
     as "unknown", which is the honest figure.
   - sigma_min / sigma_max of the standardised design below 0.01, from Owl's
     [svdvals]. Two factors that move together over the rows used cannot be
     told apart, and a regression that is asked to anyway returns a pair of
     large, opposite, confident betas whose sum is the only real number in
     them. Standardising first makes the ratio a statement about the factors'
     correlation structure, not about their units: rates in percentage points
     and the market in fractions would otherwise differ by a factor of ten on
     scale alone. A factor that does not move at all over the rows used cannot
     be standardised, and is the extreme case of the same refusal.

     Why 0.01 (ruling 5, as amended): it is a variance-inflation bound. Z'Z/T
     is the factors' correlation matrix C, whose eigenvalues are
     lambda_i = sigma_i^2 / T and average 1, so lambda_max >= 1. A ratio of
     at least 0.01 means lambda_min >= 1e-4 lambda_max >= 1e-4, and every
     beta's variance-inflation factor, (C^-1)_kk <= 1 / lambda_min, is then
     at most 10^4 -- its standard error at most 100 times what orthogonal
     factors would give. The first floor, 1e-8, bounded nothing a reader
     could use: it accepted betas of plus and minus nine thousand.

   Both are Errors, never exceptions, because the caller runs every name and
   one thin name must not stop the rest. So is an infinite input: nan is
   dropped as missing, but inf is a data error (a zero close upstream), and
   LAPACK raises on it rather than returning.

   THE DIVISORS

   The factor covariance uses the population divisor T, over the rows where
   every factor is present -- the Risk_metrics convention, and it is computed
   by Risk_metrics.covariance_matrix. The residual variance is the one figure
   that divides by T - K - 1: an OLS residual has used up K + 1 degrees of
   freedom fitting the intercept and the betas, and RSS / T would understate
   the idiosyncratic risk by exactly the amount the fit absorbed.

   THE RISK (ruling 4)

   Exposures x_i are dollars, qty * price. The book's exposure to factor k is

       b_k = sum_i x_i beta_ik        (dollars per 1.00 of the factor),

   and its variance splits as

       V = b' Sigma_f b  +  sum_i x_i^2 sigma_e,i^2
           systematic       idiosyncratic

   in dollars squared. The systematic term is homogeneous of degree two in b,
   so Euler splits it exactly: contribution_k = b_k (Sigma_f b)_k, and the
   contributions sum to the systematic variance with no residual. A
   contribution can be negative -- a factor the book is hedged on -- and is not
   absolute-valued, for the reason attribution.ml gives.

   The idiosyncratic term assumes the residuals are uncorrelated across names.
   That is the model's one strong assumption, and [risk] can publish the
   figure it leaves out beside it: x' A x for a sample asset covariance A, as
   [sample_variance]. The gap between the two is mostly the residual
   correlation the model does not see.

   The model's VaR is -z sqrt(V), z the normal quantile at 1 - confidence: a
   sibling figure, never the book's VaR. *)

open Core

(* Ruling 5's floor: the fewest observations a name may be fitted on. *)
let min_observations = 120

(* sigma_min / sigma_max of the standardised design below this is refused: the
   variance-inflation bound of 10^4 derived in the header. *)
let condition_floor = 0.01

module Fit = struct
  type t = {
    alpha : float;
    (* One per factor, in the caller's column order. *)
    betas : float array;
    (* RSS / (observations - K - 1). *)
    residual_variance : float;
    (* The rows the regression used, after dropping every row with a nan. *)
    observations : int;
  }
  [@@deriving sexp]
end

(* The indices of the rows at which every column is present. *)
let complete_rows ~(columns : float array array) ~length =
  Array.filter (Array.init length ~f:Fn.id) ~f:(fun t ->
      Array.for_all columns ~f:(fun c -> not (Float.is_nan c.(t))))

let take ~rows column = Array.map rows ~f:(fun t -> column.(t))
let sum = Array.fold ~init:0.0 ~f:( +. )

let fit ~enforce_minimum ~(y : float array) ~(factors : float array array) :
    (Fit.t, string) Result.t =
  let open Result.Let_syntax in
  let k = Array.length factors in
  let length = Array.length y in
  let%bind () =
    if k = 0 then Error "factor model: there are no factors to regress on" else Ok ()
  in
  let%bind () =
    match Array.findi factors ~f:(fun _ f -> Array.length f <> length) with
    | Some (i, f) ->
        Error
          (sprintf "factor model: factor %d has %d rows, and y has %d" i (Array.length f)
             length)
    | None -> Ok ()
  in
  let rows = complete_rows ~columns:(Array.append [| y |] factors) ~length in
  let n = Array.length rows in
  let%bind () =
    if enforce_minimum && n < min_observations then
      Error
        (sprintf
           "factor model: %d observations after dropping rows with nan, fewer than the \
            %d a fit needs"
           n min_observations)
    else Ok ()
  in
  (* Not the minimum-observations rule, and For_testing does not skip it: with
     n <= K + 1 the residual variance's divisor is zero or negative, and there
     is no residual to speak of. *)
  let%bind () =
    if n < k + 2 then
      Error
        (sprintf
           "factor model: %d observations cannot fit an intercept and K = %d factors \
            with a residual degree of freedom left"
           n k)
    else Ok ()
  in
  let y = take ~rows y in
  let f = Array.map factors ~f:(take ~rows) in
  (* nan was dropped as missing; anything still not finite is infinite, a data
     error rather than a gap, and LAPACK's svdvals raises on it instead of
     returning. Refused here, naming the column and the caller's row, so one
     bad name cannot abort the fit of every other. *)
  let%bind () =
    let first_infinite column =
      Array.findi column ~f:(fun _ v -> not (Float.is_finite v))
      |> Option.map ~f:(fun (t, v) -> (rows.(t), v))
    in
    match first_infinite y with
    | Some (row, v) -> Error (sprintf "factor model: y is %g at row %d" v row)
    | None -> (
        match
          Array.find_mapi f ~f:(fun i c ->
              Option.map (first_infinite c) ~f:(fun (row, v) -> (i, row, v)))
        with
        | Some (i, row, v) ->
            Error (sprintf "factor model: factor %d is %g at row %d" i v row)
        | None -> Ok ())
  in
  let%bind () =
    match Array.findi f ~f:(fun _ c -> Risk_metrics.is_effectively_constant c) with
    | Some (i, _) ->
        Error
          (sprintf
             "factor model: factor %d does not move over the %d rows used, so the \
              standardised design is singular (sigma_min / sigma_max = 0)"
             i n)
    | None -> Ok ()
  in
  let means = Array.map f ~f:Risk_metrics.mean in
  let sds = Array.map f ~f:Risk_metrics.stddev in
  let z = Owl.Mat.init_2d n k (fun t j -> (f.(j).(t) -. means.(j)) /. sds.(j)) in
  let singular_values = Owl.Linalg.D.svdvals z in
  let ratio = Owl.Mat.min' singular_values /. Owl.Mat.max' singular_values in
  let%bind () =
    if Float.is_nan ratio || Float.( < ) ratio condition_floor then
      Error
        (sprintf
           "factor model: the standardised design is ill-conditioned over the %d rows \
            used: sigma_min / sigma_max = %.4g, below %g, the floor that bounds every \
            beta's variance inflation at 10^4"
           n ratio condition_floor)
    else Ok ()
  in
  (* [1 | Z], factored thin: Q is n x (K+1), R is (K+1) x (K+1) upper
     triangular, and theta solves R theta = Q'y. *)
  let design =
    Owl.Mat.init_2d n (k + 1) (fun t j -> if j = 0 then 1.0 else Owl.Mat.get z t (j - 1))
  in
  let q, r, _ = Owl.Linalg.D.qr ~thin:true design in
  let qty = Owl.Mat.dot (Owl.Mat.transpose q) (Owl.Mat.of_array y n 1) in
  let theta = Owl.Linalg.D.triangular_solve ~upper:true r qty in
  let betas = Array.init k ~f:(fun j -> Owl.Mat.get theta (j + 1) 0 /. sds.(j)) in
  let alpha =
    Owl.Mat.get theta 0 0 -. sum (Array.mapi betas ~f:(fun j b -> b *. means.(j)))
  in
  (* The residuals from the coefficients this function returns, so the
     residual variance it reports is the one those coefficients leave. *)
  let rss =
    Array.foldi y ~init:0.0 ~f:(fun t acc yt ->
        let fitted =
          Array.foldi betas ~init:alpha ~f:(fun j acc b -> acc +. (b *. f.(j).(t)))
        in
        let e = yt -. fitted in
        acc +. (e *. e))
  in
  let residual_variance = rss /. float_of_int (n - k - 1) in
  (* Every input that reaches here is finite and the design is well
     conditioned, but finite inputs can still overflow: a y of order 1e200
     leaves residuals whose squares are infinite. nan never reaches a figure. *)
  if
    Float.is_finite alpha
    && Array.for_all betas ~f:Float.is_finite
    && Float.is_finite residual_variance
  then Ok { Fit.alpha; betas; residual_variance; observations = n }
  else
    Error
      (sprintf
         "factor model: the fit over %d rows is not finite (does an input overflow?)" n)

let fit_one ~y ~factors = fit ~enforce_minimum:true ~y ~factors

module For_testing = struct
  (* [fit_one] without the minimum-observations rule, so a four-row case can be
     derived by hand. Every other refusal still applies, and raises. *)
  let fit_unchecked ~y ~factors =
    match fit ~enforce_minimum:false ~y ~factors with
    | Ok fit -> fit
    | Error reason -> invalid_arg reason
end

(* The K x K factor covariance, population divisor, over the rows where every
   factor is present.

   Ruling 2 lets only rates be nan, and only on its trailing run of dates FRED
   has not published yet; those rows are dropped here exactly as [fit_one]
   drops them. Structurally invalid input raises, the Risk_metrics convention:
   the panel guarantees equal lengths and at least one complete row, so a
   violation is a caller bug, not a market condition. *)
let factor_covariance ~(factors : float array array) : Owl.Mat.mat =
  if Array.is_empty factors then
    invalid_arg "factor_model: factor_covariance needs at least one factor";
  let length = Array.length factors.(0) in
  Array.iteri factors ~f:(fun i f ->
      if Array.length f <> length then
        invalid_argf "factor_model: factor %d has %d rows, factor 0 has %d" i
          (Array.length f) length ());
  let rows = complete_rows ~columns:factors ~length in
  if Array.is_empty rows then invalid_arg "factor_model: no row has every factor present";
  Risk_metrics.covariance_matrix (Array.map factors ~f:(take ~rows))

module Risk = struct
  type t = {
    (* b_k = sum_i x_i beta_ik: dollars per 1.00 of factor k. *)
    exposures : float array;
    (* b' Sigma_f b, dollars squared. *)
    systematic_variance : float;
    (* sum_i x_i^2 sigma_e,i^2, dollars squared. *)
    idiosyncratic_variance : float;
    total_variance : float;
    (* b_k (Sigma_f b)_k; sums to [systematic_variance]. Signed. *)
    contributions : float array;
    (* -z sqrt(total_variance), dollars; a positive number is a loss. *)
    model_var : float;
    (* x' A x over the held names (x_i <> 0), when an asset covariance was
       given. *)
    sample_variance : float option;
  }
  [@@deriving sexp]
end

(* The book's factor risk.

   [fits], [exposures] and [asset_covariance] are indexed by instrument, in
   one order the caller owns; [covariance] is the K x K factor covariance.

   An instrument with no fit and a zero exposure contributes nothing and is
   not an error: it is a name the book does not hold. An instrument with no fit
   and a NONZERO exposure is an Error naming its index, because its risk is
   unknown and unknown is not zero -- leaving it out would report a book that
   holds it as though it did not. The caller maps the index to a symbol. *)
let risk ~(fits : Fit.t option array) ~(exposures : float array)
    ~(covariance : Owl.Mat.mat) ?(asset_covariance : Owl.Mat.mat option) ~confidence () :
    (Risk.t, string) Result.t =
  let open Result.Let_syntax in
  let n = Array.length exposures in
  let k, k_cols = Owl.Mat.shape covariance in
  let%bind () =
    if Array.length fits <> n then
      Error (sprintf "factor model: %d fits for %d exposures" (Array.length fits) n)
    else Ok ()
  in
  let%bind () =
    if k = 0 || k <> k_cols then
      Error (sprintf "factor model: the factor covariance is %dx%d, not square" k k_cols)
    else Ok ()
  in
  let%bind () =
    if Float.( > ) confidence 0.0 && Float.( < ) confidence 1.0 then Ok ()
    else
      Error
        (sprintf "factor model: confidence must lie strictly between 0 and 1, got %g"
           confidence)
  in
  let%bind () =
    match Array.findi exposures ~f:(fun _ x -> not (Float.is_finite x)) with
    | Some (i, x) -> Error (sprintf "factor model: instrument %d's exposure is %g" i x)
    | None -> Ok ()
  in
  let%bind () =
    Array.foldi fits ~init:(Ok ()) ~f:(fun i acc fit ->
        let%bind () = acc in
        match fit with
        | Some { Fit.betas; _ } when Array.length betas <> k ->
            Error
              (sprintf
                 "factor model: instrument %d has %d betas and the covariance is %dx%d" i
                 (Array.length betas) k k)
        | Some _ -> Ok ()
        | None when Float.equal exposures.(i) 0.0 -> Ok ()
        | None ->
            Error
              (sprintf
                 "factor model: instrument %d has a nonzero exposure (%g) and no fit, so \
                  the book's factor risk is unknown"
                 i exposures.(i)))
  in
  let%bind () =
    match asset_covariance with
    | None -> Ok ()
    | Some a ->
        let rows, cols = Owl.Mat.shape a in
        if rows = n && cols = n then Ok ()
        else
          Error
            (sprintf "factor model: the asset covariance is %dx%d for %d exposures" rows
               cols n)
  in
  let b =
    Array.init k ~f:(fun j ->
        Array.foldi fits ~init:0.0 ~f:(fun i acc fit ->
            match fit with
            | None -> acc
            | Some { Fit.betas; _ } -> acc +. (exposures.(i) *. betas.(j))))
  in
  let b_col = Owl.Mat.of_array b k 1 in
  let sigma_b = Owl.Mat.dot covariance b_col in
  let contributions = Array.init k ~f:(fun j -> b.(j) *. Owl.Mat.get sigma_b j 0) in
  let systematic_variance =
    Owl.Mat.get (Owl.Mat.dot (Owl.Mat.transpose b_col) sigma_b) 0 0
  in
  let idiosyncratic_variance =
    Array.foldi fits ~init:0.0 ~f:(fun i acc fit ->
        match fit with
        | None -> acc
        | Some { Fit.residual_variance; _ } ->
            acc +. (exposures.(i) *. exposures.(i) *. residual_variance))
  in
  let total_variance = systematic_variance +. idiosyncratic_variance in
  let z = Risk_metrics.normal_ppf ~p:(1.0 -. confidence) in
  (* A population covariance is positive semi-definite, so a negative total can
     only be rounding on a book whose variance is zero; clamp rather than take
     the square root of it. *)
  let model_var = -.z *. Float.sqrt (Float.max 0.0 total_variance) in
  (* x'Ax over the HELD names only. A name the book does not hold may have no
     column worth the name -- nan where it has no observations on the common
     rows -- and 0 * nan is nan, so summing over every name would let a name
     with no position turn the book's figure into nan. Over the held names,
     every entry is one the figure needs, so a non-finite one is an Error
     naming it. *)
  let%bind sample_variance =
    match asset_covariance with
    | None -> Ok None
    | Some a -> (
        let held =
          Array.filter (Array.init n ~f:Fn.id) ~f:(fun i ->
              not (Float.equal exposures.(i) 0.0))
        in
        let m = Array.length held in
        let bad =
          Array.find_map held ~f:(fun i ->
              Array.find_map held ~f:(fun j ->
                  let v = Owl.Mat.get a i j in
                  if Float.is_finite v then None else Some (i, j, v)))
        in
        match bad with
        | Some (i, j, v) ->
            Error
              (sprintf
                 "factor model: the asset covariance's entry (%d, %d) is %g, and \
                  instruments %d and %d are both held"
                 i j v i j)
        | None when m = 0 -> Ok (Some 0.0)
        | None ->
            let x = Owl.Mat.of_array (Array.map held ~f:(fun i -> exposures.(i))) m 1 in
            let a = Owl.Mat.init_2d m m (fun p q -> Owl.Mat.get a held.(p) held.(q)) in
            Ok
              (Some
                 (Owl.Mat.get (Owl.Mat.dot (Owl.Mat.transpose x) (Owl.Mat.dot a x)) 0 0)))
  in
  let risk =
    {
      Risk.exposures = b;
      systematic_variance;
      idiosyncratic_variance;
      total_variance;
      contributions;
      model_var;
      sample_variance;
    }
  in
  (* The last word: never Ok with a nan in it. Nothing above checks the factor
     covariance's entries or a fit's residual variance, and a nan in either
     reaches every figure downstream of it. *)
  let finite = Float.is_finite in
  if
    Array.for_all risk.exposures ~f:finite
    && finite risk.systematic_variance
    && finite risk.idiosyncratic_variance
    && finite risk.total_variance
    && Array.for_all risk.contributions ~f:finite
    && finite risk.model_var
    && Option.for_all risk.sample_variance ~f:finite
  then Ok risk
  else
    Error
      "factor model: the book's factor risk is not finite (is an entry of the factor \
       covariance, or a fit's residual variance, nan?)"
