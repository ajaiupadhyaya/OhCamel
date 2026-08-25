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

(* ------------------------------------------------------------------------ *)
(* GARCH(1,1)                                                                *)
(* ------------------------------------------------------------------------ *)

(* The conditional volatility model, and the one estimator in this file that is
   FITTED rather than parameterised.

   EWMA above tracks a regime change and has nothing to say about what happens
   next: with one hand-set decay factor it will hold an elevated volatility
   forecast for as long as the elevated returns keep arriving and then decay
   toward whatever comes after. GARCH(1,1) adds the piece EWMA is missing, which
   is MEAN REVERSION -- a long-run level the process is pulled back toward:

     sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2

   alpha is how hard yesterday's surprise moves today's forecast, beta is how
   much of yesterday's forecast persists, and alpha + beta is the PERSISTENCE:
   the fraction of a volatility shock that survives one period. It is the number
   that matters, because it sets the half-life of a shock, and 1 - alpha - beta
   is the pull toward the long-run variance omega / (1 - alpha - beta). EWMA is
   the boundary case alpha + beta = 1, where shocks never decay and there is no
   long-run level to revert to -- which is exactly why RiskMetrics can hand-set
   lambda and this cannot.

   VARIANCE TARGETING, which is why only two parameters are searched.

   omega is not fitted. It is pinned by requiring the model's unconditional
   variance to equal the sample's:

     omega = sample_variance * (1 - alpha - beta)

   Engle's variance targeting, and it is the right trade on short samples for
   two reasons. It removes the parameter the likelihood is flattest in -- omega
   is small and multiplicative and the surface barely moves in it -- and it
   guarantees the fitted model reproduces the volatility level actually
   observed, so a bad fit can be wrong about the DYNAMICS without also being
   wrong about the LEVEL. A three-parameter search can get both wrong at once.

   READ THE WARNING ON [fit] BEFORE USING THIS ON A SHORT WINDOW.

   Nothing here is wired into graph.ml, and that is a measured decision rather
   than an omission -- see [Sample_size_study] below and the README section it
   feeds. *)
module Garch11 = struct
  (* Stationarity requires alpha + beta < 1, and the search is bounded slightly
     inside it. At exactly 1 the process is IGARCH -- shocks never decay, the
     unconditional variance is undefined, and variance targeting divides by
     zero. Approaching it, omega goes to zero and the likelihood surface becomes
     a ridge, so the bound is doing numerical work as well as economic. *)
  let max_persistence = 0.999

  type t = {
    (* The constant, pinned by variance targeting rather than searched. *)
    omega : float;
    (* Reaction: how much of yesterday's squared surprise enters today. *)
    alpha : float;
    (* Persistence of the previous forecast. *)
    beta : float;
  }
  [@@deriving sexp_of, fields ~getters]

  (* The fraction of a volatility shock surviving one period, and the single
     most informative number in a fitted GARCH. Equity daily series typically
     land near 0.97-0.99, which is a shock half-life of roughly 25 to 70 days. *)
  let persistence t = t.alpha +. t.beta

  (* The level the process reverts to. Undefined at unit persistence, which the
     bound above makes unreachable. *)
  let long_run_variance t = t.omega /. (1.0 -. persistence t)
  let long_run_stddev t = Float.sqrt (Float.max 0.0 (long_run_variance t))

  (* Shock half-life in periods: how long until half of a volatility surprise
     has decayed out of the forecast. [None] at persistence >= 1, where nothing
     decays. *)
  let shock_half_life t =
    let p = persistence t in
    if Float.( >= ) p 1.0 || Float.( <= ) p 0.0 then None
    else Some (Float.log 0.5 /. Float.log p)

  (* The conditional variance path implied by a parameter set.

     Seeded at the sample variance rather than at the long-run variance, which
     are the same number under variance targeting -- stated because they are NOT
     the same under a free-omega fit, and someone porting this would have to
     choose. The recursion is strictly one-step-ahead: sigma_t^2 is formed from
     information through t-1 and is the variance the return at t is scored
     against, so nothing here can see the observation it is predicting. The same
     discipline var_backtest.ml enforces structurally, applied inside the
     estimator. *)
  let conditional_variances ~(returns : float array) (t : t) : float array =
    let n = Array.length returns in
    let seed = Risk_metrics.variance returns in
    let path = Array.create ~len:n seed in
    for i = 1 to n - 1 do
      path.(i) <-
        t.omega
        +. (t.alpha *. returns.(i - 1) *. returns.(i - 1))
        +. (t.beta *. path.(i - 1))
    done;
    path

  (* Gaussian log-likelihood. Returns [Float.neg_infinity] outside the
     stationary region rather than raising, because the optimiser walks the
     boundary and a raise there would make the search order load-bearing. *)
  let log_likelihood ~(returns : float array) (t : t) : float =
    if
      Float.( <= ) t.alpha 0.0 || Float.( <= ) t.beta 0.0
      || Float.( >= ) (persistence t) max_persistence
      || Float.( <= ) t.omega 0.0
    then Float.neg_infinity
    else begin
      let path = conditional_variances ~returns t in
      Array.foldi returns ~init:0.0 ~f:(fun i acc r ->
          let v = path.(i) in
          if Float.( <= ) v 1e-300 then Float.neg_infinity
          else
            acc -. (0.5 *. (Float.log (2.0 *. Float.pi) +. Float.log v +. (r *. r /. v))))
    end

  let of_params ~sample_variance ~alpha ~beta =
    { omega = sample_variance *. (1.0 -. alpha -. beta); alpha; beta }

  (* Fit by maximum likelihood over (alpha, beta), with omega variance-targeted.

     A coarse grid over the stationary region followed by a shrinking pattern
     search. Deliberately not a gradient method: the GARCH likelihood has a
     well-known flat ridge along alpha + beta near 1, and a gradient method
     started in the wrong place walks along it and stops somewhere arbitrary.
     A grid cannot, because it evaluates the whole region before refining, and
     the cost is trivial at these sample sizes.

     THE WARNING. A GARCH(1,1) fitted on a short sample does not merely have
     wide standard errors -- it is BIASED. Recovering a known process with
     alpha = 0.10, beta = 0.88 (persistence 0.98), thirty replications each:

       n = 60     beta = 0.558 +/- 0.310     persistence = 0.651 +/- 0.327
       n = 250    beta = 0.839 +/- 0.071     persistence = 0.939 +/- 0.039
       n = 1000   beta = 0.875 +/- 0.030     persistence = 0.971 +/- 0.017

     At sixty observations the persistence -- the quantity that sets how long a
     volatility shock lasts, and the entire reason to prefer GARCH over EWMA --
     is estimated a third of the way from its true value with a standard
     deviation of the same size. That is not an estimate; it is a number.

     This engine's return window is sixty observations. So GARCH is implemented
     here, tested here, and NOT wired into graph.ml, and `make garch`
     regenerates the table above so the decision is a measurement anybody can
     re-run rather than a claim -- it drives [simulate] below at each sample
     size and fits what it generated. *)
  let fit ?(coarse_steps = 40) ~(returns : float array) () : t =
    if Array.length returns < 3 then
      invalid_argf
        "vol_estimators: GARCH(1,1) needs at least 3 observations to have a recursion at \
         all, got %d"
        (Array.length returns) ();
    (* [is_effectively_constant] rather than a test against exact zero, and the
       tests caught the difference rather than the comment predicting it. Fifty
       copies of 0.01 have a computed variance of 1.2e-35 -- not zero, because
       the mean is not exactly representable -- so an exact test passes the
       series straight through, variance targeting pins omega at a rounding
       residue, and every conditional variance below is one residue divided by
       another. Same argument as [Risk_metrics.beta]'s guard against a constant
       factor, and the same function does the work. *)
    if Risk_metrics.is_effectively_constant returns then
      invalid_arg
        "vol_estimators: GARCH(1,1) cannot be fitted to a series that is constant up to \
         float noise -- variance targeting would pin omega at a rounding residue and \
         every conditional variance with it";
    let sample_variance = Risk_metrics.variance returns in
    let score alpha beta =
      log_likelihood ~returns (of_params ~sample_variance ~alpha ~beta)
    in
    let best_alpha = ref 0.05 and best_beta = ref 0.90 in
    let best = ref Float.neg_infinity in
    let consider alpha beta =
      let s = score alpha beta in
      if Float.( > ) s !best then begin
        best := s;
        best_alpha := alpha;
        best_beta := beta
      end
    in
    for i = 1 to coarse_steps do
      for j = 1 to coarse_steps do
        (* alpha over (0, 0.5] and beta over (0, 1): the reaction coefficient of
           a real equity series is rarely above 0.3, while beta routinely sits
           near 0.9, so an even grid over both would spend most of its budget
           where nothing is. *)
        let alpha = 0.5 *. float_of_int i /. float_of_int coarse_steps in
        let beta = float_of_int j /. float_of_int (coarse_steps + 1) in
        if Float.( < ) (alpha +. beta) max_persistence then consider alpha beta
      done
    done;
    let step = ref 0.02 in
    while Float.( > ) !step 1e-7 do
      let improved = ref false in
      List.iter [ -1.0; 0.0; 1.0 ] ~f:(fun da ->
          List.iter [ -1.0; 0.0; 1.0 ] ~f:(fun db ->
              let alpha = !best_alpha +. (da *. !step) in
              let beta = !best_beta +. (db *. !step) in
              if
                Float.( > ) alpha 0.0 && Float.( > ) beta 0.0
                && Float.( < ) (alpha +. beta) max_persistence
              then
                let s = score alpha beta in
                if Float.( > ) s !best then begin
                  best := s;
                  best_alpha := alpha;
                  best_beta := beta;
                  improved := true
                end));
      (* Shrink only when a full sweep of the neighbourhood found nothing. That
         is what makes this a pattern search rather than a fixed-step walk: it
         takes as many steps as it can at each scale before halving, so it
         cannot stall by halving through a region it was still crossing. *)
      if not !improved then step := !step /. 2.0
    done;
    of_params ~sample_variance ~alpha:!best_alpha ~beta:!best_beta

  (* One-step-ahead conditional standard deviation: the forecast for the period
     AFTER the last observation, which is the quantity a VaR is built from. *)
  let forecast_stddev ~(returns : float array) (t : t) : float =
    let n = Array.length returns in
    let path = conditional_variances ~returns t in
    let next =
      t.omega
      +. (t.alpha *. returns.(n - 1) *. returns.(n - 1))
      +. (t.beta *. path.(n - 1))
    in
    Float.sqrt (Float.max 0.0 next)

  (* Fit and forecast in one call, which is how every caller uses it. *)
  let fitted_forecast_stddev ?coarse_steps ~(returns : float array) () : float =
    forecast_stddev ~returns (fit ?coarse_steps ~returns ())

  (* Generate a return path from a known parameter set.

     [innovations] are standard normal shocks supplied by the caller, which is
     what keeps this a pure function: the randomness lives in the driver, so a
     test can hand it a fixed array and get the same path every time. It is also
     the only honest way to write the sample-size study -- an experiment about
     estimator variance has to be able to run the same process many times with
     different draws, and a simulator with an RNG baked in would make each
     replication depend on call order.

     [burn_in] shocks are consumed before the returned path starts, so the
     process has forgotten its seeding at the unconditional variance. Without
     it, every simulated path would begin at exactly the long-run level and the
     first few observations would understate how far a real one wanders. *)
  let simulate ?(burn_in = 500) ~(innovations : float array) (t : t) : float array =
    let total = Array.length innovations in
    if total <= burn_in then
      invalid_argf
        "vol_estimators: need more than %d innovations to simulate past the \
         burn-in,          got %d"
        burn_in total ();
    let variance = ref (long_run_variance t) in
    let out = Array.create ~len:(total - burn_in) 0.0 in
    Array.iteri innovations ~f:(fun i z ->
        let r = z *. Float.sqrt (Float.max 0.0 !variance) in
        if i >= burn_in then out.(i - burn_in) <- r;
        variance := t.omega +. (t.alpha *. r *. r) +. (t.beta *. !variance));
    out
end
