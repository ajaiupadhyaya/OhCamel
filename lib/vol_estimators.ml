(* Exponentially weighted volatility and covariance.

   The equal-weighted estimator in risk_metrics.ml treats an observation from
   sixty days ago and one from yesterday as equally informative about tomorrow.
   That is a claim about the world, not a neutral default, and it is false in
   exactly the situation where anybody reads a VaR number. It has two visible
   failure modes and this engine already prints both of them: volatility that
   lags a regime change by most of the window on the way in, and -- the one
   people forget -- a "ghost feature" on the way out, where a single crash-day
   return drops off the back of the window and the reported risk falls by a
   third overnight for no reason that happened in the market. `make backtest`
   rejects the equal-weighted parametric estimator on its vol-regime series for
   precisely this reason.

   The fix that costs one parameter is to weight the past geometrically. With
   decay factor lambda, the observation k periods back gets weight proportional
   to lambda^k, so the window has no back edge to fall off -- old data fades
   instead of being deleted.

   This module is a SIBLING to risk_metrics.ml, not a replacement. graph.ml
   exposes both covariance matrices as nodes hanging off the same
   [aligned_returns] edge and reports both parametric VaRs, because the number
   that matters is not either estimate on its own: it is the GAP between them.
   Equal-weighted below EWMA says volatility is rising faster than the window
   has absorbed. The two together are a regime-change diagnostic in the same way
   the README's historical-versus-parametric gap is a tail-fatness diagnostic.
   Swapping one for the other silently would have thrown that away to change a
   number.

   Nothing here references Incremental, and nothing here is stateful -- same
   contract as risk_metrics.ml, and for the same reason: every function is a
   plain map from numbers to numbers, unit-testable against a hand-computed
   value and callable from the backtester without an engine.

   Conventions are inherited from risk_metrics.ml and are not restated per
   function: simple fractional returns, positive-magnitude losses, and
   structurally invalid input raises rather than returning a plausible zero. *)

open Core

module Ewma = struct
  (* RiskMetrics' published daily decay factor, and the reason it is the default
     rather than a number chosen here.

     J.P. Morgan fitted lambda across roughly 480 series in the 1996 RiskMetrics
     Technical Document and found 0.94 minimised out-of-sample forecast error
     for daily data on average -- 0.97 for monthly. The value is worth keeping
     not because that fit is authoritative today but because it is the number
     every risk desk recognises, which makes this estimator's output comparable
     to something rather than to nothing.

     What it means concretely: weight halves about every 11 observations
     (ln 0.5 / ln 0.94 = 11.2), and the effective sample size -- the reciprocal
     of the sum of squared weights -- is about 31 observations against the 60 in
     the engine's default window. That is the trade the parameter makes.
     Responsiveness is bought with estimator variance, and a lambda tuned low
     enough to track every move is an estimator reporting mostly noise. *)
  let default_lambda = 0.94

  (* lambda = 1 is rejected rather than accepted as "no decay".

     It is arithmetically fine -- uniform weights, which is exactly
     Risk_metrics.covariance_matrix -- and that is the problem. Two names for
     one estimator is how a codebase ends up with a dashboard showing the same
     number twice under different labels and nobody noticing the EWMA node has
     been inert for a month. The reduction to equal weighting as lambda
     approaches 1 is a property worth having and it is asserted in the tests;
     it is not worth an alias. *)
  let validate_lambda ~lambda =
    if not (Float.( > ) lambda 0.0 && Float.( < ) lambda 1.0) then
      invalid_argf
        "vol_estimators: lambda must be strictly between 0 and 1, got %f (lambda = 1 is \
         the equal-weighted estimator and lives in risk_metrics.ml)"
        lambda ()

  let validate_non_empty ~name (xs : float array) =
    if Array.is_empty xs then invalid_argf "vol_estimators: %s must be non-empty" name ()

  (* Normalised decay weights over an n-observation window, OLDEST FIRST.

     The ordering is the one place this module can go quietly wrong. Every
     return array in this codebase runs oldest-to-newest -- graph.ml's
     [aligned_returns] trims at the right edge precisely to keep the most recent
     observation last -- so the largest weight belongs at index n-1, and the
     exponent counts backwards from there. A reversed weighting is still a valid
     set of weights summing to one, so it produces a plausible covariance matrix
     and fails no invariant; it just answers a question about ancient history.
     The regime-break test is what catches it.

     Built by repeated multiplication from the newest end rather than by
     evaluating lambda^k per element: it is one multiply per observation instead
     of a power, and it accumulates the same rounding the normaliser does, so
     the weights sum to 1.0 more exactly than two independent computations
     would.

     The normaliser is the explicit sum of the unnormalised weights, not the
     closed form (1 - lambda) / (1 - lambda^n). They are equal in real
     arithmetic. The closed form is 0/0 in the limit this estimator's central
     property is stated about, and near-0/near-0 just outside it, which is a
     numerically terrible place to leave the one line that decides whether the
     weights sum to one. *)
  let weights ~n ~lambda =
    validate_lambda ~lambda;
    if n < 1 then
      invalid_argf "vol_estimators: need at least one observation, got %d" n ();
    let w = Array.create ~len:n 0.0 in
    let running = ref 1.0 in
    let total = ref 0.0 in
    for i = n - 1 downto 0 do
      w.(i) <- !running;
      total := !total +. !running;
      running := !running *. lambda
    done;
    Array.map w ~f:(fun x -> x /. !total)

  (* The decay-weighted mean.

     RiskMetrics' own convention is to assume a zero mean and skip this, on the
     argument that a daily drift estimate is mostly sampling noise. This module
     estimates it anyway, and the reason is not statistical -- it is that the
     estimator has to be COMPARABLE to its sibling.

     With a weighted mean, this converges on Risk_metrics.covariance_matrix
     exactly as lambda approaches 1, so the difference between the two nodes
     graph.ml publishes is a difference in weighting and nothing else. Under the
     zero-mean convention the two would differ in mean treatment as well, and
     the claim the dashboard makes -- "these two disagreeing tells you the
     regime is moving" -- would be measuring two effects at once and attributing
     both to the first. At daily horizons the drift term is second-order either
     way. Being able to say precisely what a disagreement means is not. *)
  let mean ~returns ~lambda =
    validate_non_empty ~name:"returns" returns;
    let w = weights ~n:(Array.length returns) ~lambda in
    Array.foldi returns ~init:0.0 ~f:(fun i acc r -> acc +. (w.(i) *. r))

  let covariance ~xs ~ys ~lambda =
    validate_non_empty ~name:"series" xs;
    if Array.length xs <> Array.length ys then
      invalid_argf "vol_estimators: series length mismatch (%d vs %d)" (Array.length xs)
        (Array.length ys) ();
    let w = weights ~n:(Array.length xs) ~lambda in
    let mx = mean ~returns:xs ~lambda and my = mean ~returns:ys ~lambda in
    Array.foldi xs ~init:0.0 ~f:(fun i acc x ->
        acc +. (w.(i) *. (x -. mx) *. (ys.(i) -. my)))

  let variance ~returns ~lambda = covariance ~xs:returns ~ys:returns ~lambda

  (* Clamped at zero before the square root for the same reason
     Risk_metrics.portfolio_stddev clamps: a variance that is mathematically
     zero can come out very slightly negative, and nan is a far worse answer
     than 0.0 on a quantity a limit is compared against. *)
  let stddev ~returns ~lambda = Float.sqrt (Float.max 0.0 (variance ~returns ~lambda))

  (* The decay-weighted covariance matrix, in the same shape and with the same
     contract as Risk_metrics.covariance_matrix: [series.(i)] is instrument i's
     return window, all the same length, and the result is n x n in that order.

     Identical in structure to its equal-weighted sibling on purpose. The two
     are read as alternatives to each other, so any difference between them
     should be the weighting and not an incidental choice about shape, ordering
     or symmetry. Upper triangle then mirrored, for the same reason as there:
     computing both halves independently can leave them differing in the last
     bit, which is enough to fail a positive-definiteness check downstream. *)
  let covariance_matrix ~(series : float array array) ~lambda =
    validate_lambda ~lambda;
    if Array.is_empty series then invalid_arg "vol_estimators: need at least one series";
    let n = Array.length series in
    let len = Array.length series.(0) in
    Array.iteri series ~f:(fun i s ->
        if Array.length s <> len then
          invalid_argf "vol_estimators: series %d has length %d, expected %d" i
            (Array.length s) len ());
    validate_non_empty ~name:"series" series.(0);
    let m = Owl.Mat.zeros n n in
    for i = 0 to n - 1 do
      for j = i to n - 1 do
        let c = covariance ~xs:series.(i) ~ys:series.(j) ~lambda in
        Owl.Mat.set m i j c;
        Owl.Mat.set m j i c
      done
    done;
    m
end
