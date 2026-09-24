(* Phase 5: does the VaR number mean what it says?

   The engine reports a 95% VaR. That is a testable claim with a precise
   content: over a long enough run, the realised loss should exceed it on about
   5% of days, and those exceedances should be scattered rather than bunched. A
   risk system that reports the number and never checks the claim is asserting
   something it has no evidence for, and the failure is silent -- an
   uncalibrated VaR looks exactly like a calibrated one until the day it
   matters.

   This module is the check. It is offline by construction: it consumes a
   history of (forecast, realisation) pairs and returns test statistics. It is
   not wired into the graph and must not be, because a calibration test needs
   hundreds of observations and answers a question about the model rather than
   about the book.

   THE THREE TESTS, AND WHY THREE

   A VaR model can fail in two independent ways, and a single test cannot tell
   them apart.

     Unconditional coverage (Kupiec 1995). Are there the right NUMBER of
     exceedances? A 95% model that is breached 12% of the time understates risk;
     one breached 0.5% of the time is so conservative that the limits built on
     it are not binding. Both are failures, and the test is two-sided.

     Independence (Christoffersen 1998). Are the exceedances INDEPENDENT? A
     model can have exactly 5% exceedances and still be useless if they all
     arrive in the same week, because that is the model failing to react to a
     change in volatility -- which is the only time anyone reads it. This is the
     test that catches an equal-weighted historical VaR during a vol regime
     shift, and it is the reason the pair of tests exists rather than just the
     first.

     Conditional coverage. The joint test, LR_uc + LR_ind, which is the sum of
     two asymptotically independent chi-squares and therefore chi-square with
     two degrees of freedom. Reported alongside its parts rather than instead of
     them: a joint rejection tells you the model is wrong, and the components
     tell you which half is wrong.

   Plus the Basel traffic light, which is not a hypothesis test but the
   regulatory decision rule -- how many exceedances a supervisor tolerates
   before it stops believing the model. Included because it is the answer to
   "so is this good enough", stated by someone other than the person who built
   the model.

   POINT-IN-TIME, ENFORCED BY THE TYPE

   The one way to get this analysis wrong is to let the forecast see the
   realisation. A VaR estimated from a window that includes the day it is
   predicting will look superb and mean nothing. [rolling] below is the only
   forecast generator in this module, and it slices the strictly-prior window by
   construction: the estimator receives [returns[t-window .. t-1]] and cannot
   reach [returns[t]] because that element is not in the array it is handed.
   test_var_backtest.ml asserts this against a series that is a step function,
   where lookahead would be visible as an impossibly good result.

   Nothing here references Incremental. *)

open Core

(* One day of the test: what was forecast, and what actually happened.

   [var] is a positive loss fraction, matching Risk_metrics' convention, and
   [realised] is a signed return. So the exceedance test is [realised < -.var]
   -- strict, because a realisation exactly equal to the forecast is the
   quantile being right rather than being breached, and at floating point that
   case never arises anyway. *)
module Observation = struct
  type t = { var : float; realised : float } [@@deriving sexp_of, fields ~getters]

  let create ~var ~realised = { var; realised }
  let exceeded (t : t) : bool = Float.( < ) t.realised (-.t.var)
end

(* Which estimator is being tested.

   Both are the ones the live engine actually uses, which is the point -- this
   is not a study of VaR estimators in general, it is a check on the two numbers
   graph.ml puts on a dashboard. *)
module Estimator = struct
  (* [Parametric_ewma] carries its decay factor rather than reading a default,
     because lambda is not a detail of the estimator -- it IS the estimator. A
     report that said "parametric_ewma" without saying which lambda produced it
     could not be compared against a report from a differently-tuned run, which
     is exactly the comparison this module exists to make possible. *)
  type t = Historical | Parametric | Parametric_ewma of float
  [@@deriving sexp_of, compare, equal]

  let to_string = function
    | Historical -> "historical"
    | Parametric -> "parametric"
    | Parametric_ewma lambda -> Printf.sprintf "ewma(%.2f)" lambda

  let estimate t ~(window : float array) ~(confidence : float) : float =
    match t with
    | Historical -> Risk_metrics.historical_var ~returns:window ~confidence
    | Parametric ->
        Risk_metrics.parametric_var ~mean:(Risk_metrics.mean window)
          ~stddev:(Risk_metrics.stddev window) ~confidence
    (* The same closed form over the same window, weighted. Note what is NOT
       different: [rolling] below is untouched, so this estimator receives
       exactly the strictly-prior slice the other two do. A third estimator was
       the cheap part; the expensive part -- the point-in-time discipline -- is
       written once and cannot be got wrong separately here. *)
    | Parametric_ewma lambda ->
        Risk_metrics.parametric_var
          ~mean:(Vol_estimators.Ewma.mean ~returns:window ~lambda)
          ~stddev:(Vol_estimators.Ewma.stddev ~returns:window ~lambda)
          ~confidence
end

(* Walk a return series forward, forecasting each day from the days before it.

   The rolling origin is the whole discipline here. At step t the estimator sees
   exactly [returns[t-window .. t-1]] -- a slice that ends one element short of
   the day being predicted -- and the realisation is [returns[t]]. Nothing in
   this function can hand the estimator a value it would not have had on the
   morning of day t.

   [window] is the lookback the live engine uses; passing the same number here
   is what makes the result a statement about the live engine rather than about
   a differently-parameterised one.

   Raises if the series is too short to produce a single forecast, because an
   empty test is not a passing test and returning [] would be reported as
   "no exceedances". *)
let rolling ~(returns : float array) ~(window : int) ~(confidence : float)
    ~(estimator : Estimator.t) : Observation.t list =
  if window < 2 then
    invalid_argf "var_backtest: window must be at least 2, got %d" window ();
  let n = Array.length returns in
  if n <= window then
    invalid_argf
      "var_backtest: need more than %d observations to forecast even one day, got %d"
      window n ();
  List.init (n - window) ~f:(fun i ->
      let t = window + i in
      let past = Array.sub returns ~pos:(t - window) ~len:window in
      Observation.create
        ~var:(Estimator.estimate estimator ~window:past ~confidence)
        ~realised:returns.(t))

(* The exceedance indicator series: true where the realised loss beat the
   forecast. Everything below is a function of this and nothing else. *)
let exceedances (observations : Observation.t list) : bool array =
  Array.of_list_map observations ~f:Observation.exceeded

(* x * ln x with the 0 * ln 0 = 0 convention.

   Not decoration: every likelihood below evaluates a term whose count is zero
   in ordinary cases -- zero exceedances, or no two consecutive ones -- and
   [0.0 *. Float.log 0.0] is [0.0 *. neg_infinity] = nan, which would propagate
   through the statistic and out to a p-value of nan. The convention is the
   standard one and it is the limit of the expression. *)
let xlogx ~count ~probability =
  if count = 0 then 0.0
  else if Float.( <= ) probability 0.0 then Float.neg_infinity
  else float_of_int count *. Float.log probability

(* Right tail of the chi-square, i.e. the p-value for a likelihood-ratio
   statistic. Delegated to Owl for the same reason normal_ppf is. *)
let chi2_p ~statistic ~df =
  if Float.is_nan statistic then Float.nan
  else Owl.Stats.chi2_sf (Float.max 0.0 statistic) ~df:(float_of_int df)

(* Kupiec's proportion-of-failures test: unconditional coverage.

   Under the null the exceedance indicator is Bernoulli(p) with p = 1 -
   confidence, so the likelihood ratio between that and the unrestricted
   Bernoulli(x/n) is

       LR_uc = -2 [ (n-x) ln(1-p) + x ln p - (n-x) ln(1-pi) - x ln pi ]

   with pi = x/n, asymptotically chi-square with one degree of freedom.

   Two-sided, and deliberately so. Too FEW exceedances rejects as well, which
   surprises people who think of a risk model as something that should err
   safe. It should not: a VaR that is never breached is not measuring the
   quantile it claims to measure, the limits written against it are slack, and
   the capital it justifies is wrong in the direction that costs money rather
   than the direction that loses it. Both are model failures. *)
let kupiec_pof ~(n : int) ~(x : int) ~(confidence : float) : float * float =
  if n <= 0 then invalid_arg "var_backtest: kupiec_pof needs at least one observation";
  if x < 0 || x > n then
    invalid_argf "var_backtest: exceedance count %d out of range for %d observations" x n
      ();
  Risk_metrics.validate_confidence ~confidence;
  let p = 1.0 -. confidence in
  let pi = float_of_int x /. float_of_int n in
  let restricted =
    xlogx ~count:(n - x) ~probability:(1.0 -. p) +. xlogx ~count:x ~probability:p
  in
  let unrestricted =
    xlogx ~count:(n - x) ~probability:(1.0 -. pi) +. xlogx ~count:x ~probability:pi
  in
  let statistic = -2.0 *. (restricted -. unrestricted) in
  (statistic, chi2_p ~statistic ~df:1)

(* Christoffersen's independence test.

   Counts the four transitions of the indicator series -- n_ij is the number of
   days that went from state i to state j -- and tests the first-order Markov
   chain with transition probabilities pi_01 and pi_11 against the restricted
   chain where both equal a single pi. Rejecting means today's exceedance
   predicts tomorrow's, which is exactly the clustering signature of a model
   that is not tracking volatility.

   The degenerate cases are the ones worth reading carefully, because they are
   common on short samples and each has a defensible answer:

   - No exceedance ever follows another (n11 = 0, the usual case on a
     well-behaved book). pi_11 = 0, and the [xlogx] convention makes its terms
     vanish rather than produce nan. The statistic is finite and small.

   - No day ever follows an exceedance (n10 + n11 = 0: no exceedances at all, or
     one only on the final day). pi_11 is undefined -- there is no evidence
     about what follows an exceedance -- and the unrestricted model collapses
     onto the restricted one. Statistic 0, p-value 1: no evidence of clustering,
     which is the correct reading of no evidence at all, and is different from
     evidence of independence.

   The first observation contributes no transition, so this test uses n-1
   transitions from n days. *)
let christoffersen_independence ~(exceedances : bool array) : float * float =
  let n = Array.length exceedances in
  if n < 2 then (0.0, 1.0)
  else begin
    let n00 = ref 0 and n01 = ref 0 and n10 = ref 0 and n11 = ref 0 in
    for t = 1 to n - 1 do
      match (exceedances.(t - 1), exceedances.(t)) with
      | false, false -> incr n00
      | false, true -> incr n01
      | true, false -> incr n10
      | true, true -> incr n11
    done;
    let n00 = !n00 and n01 = !n01 and n10 = !n10 and n11 = !n11 in
    let from_calm = n00 + n01 and from_breach = n10 + n11 in
    if from_breach = 0 then (0.0, 1.0)
    else begin
      let pi01 = float_of_int n01 /. float_of_int (Int.max 1 from_calm) in
      let pi11 = float_of_int n11 /. float_of_int from_breach in
      let pi = float_of_int (n01 + n11) /. float_of_int (from_calm + from_breach) in
      let restricted =
        xlogx ~count:(n00 + n10) ~probability:(1.0 -. pi)
        +. xlogx ~count:(n01 + n11) ~probability:pi
      in
      let unrestricted =
        xlogx ~count:n00 ~probability:(1.0 -. pi01)
        +. xlogx ~count:n01 ~probability:pi01
        +. xlogx ~count:n10 ~probability:(1.0 -. pi11)
        +. xlogx ~count:n11 ~probability:pi11
      in
      let statistic = -2.0 *. (restricted -. unrestricted) in
      (statistic, chi2_p ~statistic ~df:1)
    end
  end

(* ------------------------------------------------------------------------ *)
(* Independence again, this time without the adjacency blind spot            *)
(* ------------------------------------------------------------------------ *)

(* Christoffersen and Pelletier's duration-based independence test (2004).

   WHY A SECOND INDEPENDENCE TEST EXISTS AT ALL

   The Markov test above is first-order: it compares P(breach | breach
   yesterday) against P(breach | calm yesterday). That detects exceedances
   arriving BACK TO BACK and is blind to a cluster whose members are not on
   adjacent days -- and the crisis backtest found exactly that case rather than
   inventing it. Through the COVID window the standard book takes ten
   exceedances inside twenty-one sessions, against roughly one expected, and the
   Markov statistic returns p = 0.07. It is not wrong; it is answering a
   narrower question than a reader assumes it answered.

   THE IDEA

   Under correct conditional coverage the exceedance process is Bernoulli with
   parameter p, so the DURATIONS between exceedances are geometric -- memoryless,
   and in continuous time exponential with mean 1/p. Memorylessness is the whole
   content of independence: how long you have waited says nothing about how much
   longer you will wait.

   So embed the exponential in a family that can express memory, and test the
   restriction. The Weibull, with density

     f(d) = a^b b d^(b-1) exp(-(a d)^b)

   is exponential exactly when b = 1. The shape parameter reads directly:

     b < 1   decreasing hazard -- an exceedance makes another one SOONER than
             chance. This is clustering, and it is what a volatility model that
             is not keeping up looks like.
     b = 1   memoryless. Independence.
     b > 1   increasing hazard -- exceedances are more regular than chance. Rarer,
             and it is what a model whose tail is mechanically periodic looks
             like rather than a market.

   H0: b = 1, tested by likelihood ratio against chi-square with one degree of
   freedom. Crucially the durations enter as durations, so a burst landing every
   third session is as visible as one landing on consecutive days.

   CENSORING, WHICH IS NOT OPTIONAL

   The first duration runs from the start of the sample to the first exceedance
   and the last from the final exceedance to the end. Neither is a complete
   waiting time -- we do not know when the previous exceedance was, nor when the
   next will be -- so both contribute the survival function rather than the
   density. Treating them as complete would bias the shape estimate downward on
   any series that happens to start or end quietly, which is most of them.

   WHAT IT STILL CANNOT DO

   It is a GLOBAL test on the whole duration distribution. A single localised
   burst inside a long otherwise-calm series moves the fitted shape very little:
   on the GFC window this returns b = 0.95 and p = 0.75 despite the five
   exceedances around Lehman, because twenty-five durations spread over 570 days
   still look roughly exponential in aggregate. That is not a defect being
   hidden -- it is why `backtest-crisis` also prints a plain worst-burst count,
   which is the only one of the three that sees a local cluster. Three
   instruments, three different blind spots. *)

(* Waiting times between exceedances, with the two censored ends flagged.

   [None] when there are fewer than two exceedances: with none or one there is
   no complete duration to fit anything to, and the honest answer is that the
   test does not apply rather than a p-value of 1. *)
let exceedance_durations ~(exceedances : bool array) : (float array * bool array) option =
  let n = Array.length exceedances in
  let hits =
    Array.filter_mapi exceedances ~f:(fun i hit -> if hit then Some i else None)
  in
  if Array.length hits < 2 then None
  else begin
    let interior =
      Array.init
        (Array.length hits - 1)
        ~f:(fun i -> float_of_int (hits.(i + 1) - hits.(i)))
    in
    let durations =
      Array.concat
        [
          [| float_of_int (hits.(0) + 1) |];
          interior;
          [| float_of_int (n - hits.(Array.length hits - 1)) |];
        ]
    in
    let censored =
      Array.concat [ [| true |]; Array.map interior ~f:(fun _ -> false); [| true |] ]
    in
    Some (durations, censored)
  end

(* The Weibull log-likelihood with the scale parameter PROFILED OUT.

   Worth doing rather than optimising in two dimensions, because the scale has a
   closed form given the shape. Writing U for the number of uncensored durations
   and T(b) = sum over ALL durations of d^b, the first-order condition in a is

     U b / a  =  b a^(b-1) T(b)     =>     a^b = U / T(b)

   which holds with censoring too, since a censored observation contributes
   -(a d)^b and no log-a term. Substituting back, a^b T(b) = U cancels the last
   term and leaves a function of b alone:

     l(b) = U ln(U / T(b)) + U ln b + (b - 1) * sum of ln d over uncensored - U

   One smooth unimodal dimension, which a golden-section search handles without
   derivatives or an initial guess. *)
let weibull_profile_log_likelihood ~(durations : float array) ~(censored : bool array)
    ~(shape : float) : float =
  let uncensored = Array.count censored ~f:not in
  let total = Array.fold durations ~init:0.0 ~f:(fun acc d -> acc +. (d ** shape)) in
  let log_sum =
    Array.foldi durations ~init:0.0 ~f:(fun i acc d ->
        if censored.(i) then acc else acc +. Float.log d)
  in
  let u = float_of_int uncensored in
  (u *. Float.log (u /. total))
  +. (u *. Float.log shape)
  +. ((shape -. 1.0) *. log_sum)
  -. u

(* The shape is searched over [0.05, 20] rather than over the positive reals,
   and the bound is load-bearing at the top end.

   A series whose exceedances are PERFECTLY regular -- every twentieth day, say --
   has zero duration variance, and the Weibull likelihood is then increasing in
   b without limit: the MLE diverges. Bounding it means the reported shape is a
   ceiling artefact in that case rather than a fitted value, which is worth
   knowing; the statistic is enormous either way and the verdict is not in doubt.
   The lower bound is the mirror case and is far from anything real. *)
let shape_bounds = (0.05, 20.0)

let maximise_shape ~durations ~censored =
  let lo, hi = shape_bounds in
  let at shape = weibull_profile_log_likelihood ~durations ~censored ~shape in
  let phi = (Float.sqrt 5.0 -. 1.0) /. 2.0 in
  let rec go lo hi steps =
    if steps = 0 then (lo +. hi) /. 2.0
    else
      let c1 = hi -. (phi *. (hi -. lo)) and c2 = lo +. (phi *. (hi -. lo)) in
      if Float.( < ) (at c1) (at c2) then go c1 hi (steps - 1) else go lo c2 (steps - 1)
  in
  (* 120 golden-section steps shrinks the bracket by 0.618^120, which is far
     past double precision -- the loop is bounded rather than convergence-tested
     so that it cannot fail to terminate inside a node body's worth of time. *)
  go lo hi 120

(* Returns (fitted shape, LR statistic, p-value), or [None] when there are fewer
   than two exceedances and the test does not apply. *)
let duration_independence ~(exceedances : bool array) : (float * float * float) option =
  match exceedance_durations ~exceedances with
  | None -> None
  | Some (durations, censored) ->
      let restricted = weibull_profile_log_likelihood ~durations ~censored ~shape:1.0 in
      let shape = maximise_shape ~durations ~censored in
      let unrestricted = weibull_profile_log_likelihood ~durations ~censored ~shape in
      (* Clamped at zero. The unrestricted maximum cannot be below the
         restricted one in exact arithmetic -- b = 1 is inside the bracket -- but
         the search stops at finite precision, and a statistic of -1e-14 would
         come back from chi2_p as a p-value above 1. *)
      let statistic = Float.max 0.0 (-2.0 *. (restricted -. unrestricted)) in
      Some (shape, statistic, chi2_p ~statistic ~df:1)

(* Basel's traffic light: the supervisor's decision rule rather than a
   hypothesis test.

   Zones are cut by the cumulative binomial probability of seeing AT MOST this
   many exceedances when the model is correct. Green below 95%, yellow up to
   99.99%, red above -- so the boundaries are not fixed counts but fall out of
   the sample size and the confidence level, which is the honest generalisation
   of a table that is usually quoted only for 250 days at 99% (green 0-4, yellow
   5-9, red 10+; test_var_backtest.ml checks this module reproduces exactly
   those cuts at those parameters).

   Yellow is not a failure. It is the zone where the exceedance count is
   improbable enough to warrant a look but not improbable enough to reject the
   model -- a distinction worth keeping, because collapsing it into pass/fail is
   how a model gets thrown out for a bad quarter or kept through a bad year. *)
module Zone = struct
  type t = Green | Yellow | Red [@@deriving sexp_of, compare, equal]

  let to_string = function Green -> "green" | Yellow -> "yellow" | Red -> "red"
end

let traffic_light ~(n : int) ~(x : int) ~(confidence : float) : Zone.t * float =
  if n <= 0 then invalid_arg "var_backtest: traffic_light needs at least one observation";
  Risk_metrics.validate_confidence ~confidence;
  let p = 1.0 -. confidence in
  let cumulative = Owl.Stats.binomial_cdf x ~p ~n in
  let zone =
    if Float.( < ) cumulative 0.95 then Zone.Green
    else if Float.( < ) cumulative 0.9999 then Zone.Yellow
    else Zone.Red
  in
  (zone, cumulative)

(* Everything the battery found, in one value.

   [expected] is kept next to [observed] rather than left for the reader to
   compute, because the whole report is a comparison of those two numbers and
   every test below is a different way of asking whether the gap is real. *)
type report = {
  estimator : Estimator.t;
  confidence : float;
  observations : int;
  exceptions : int;
  expected_exceptions : float;
  observed_rate : float;
  expected_rate : float;
  kupiec_statistic : float;
  kupiec_p : float;
  independence_statistic : float;
  independence_p : float;
  conditional_coverage_statistic : float;
  conditional_coverage_p : float;
  (* The duration-based independence test, which sees clustering the Markov
     statistic above is blind to. [None] when there were fewer than two
     exceedances to measure a duration between.

     Deliberately NOT folded into [conditional_coverage_statistic]. That number
     is Christoffersen's decomposition -- coverage times FIRST-ORDER
     independence -- and its degrees of freedom follow from exactly those two
     pieces. Adding a third statistic to the sum would produce something with no
     distribution anybody has derived, and would silently change the meaning of
     every verdict this module has already published. It is reported alongside,
     with its own verdict, which is also how the disagreement between the two
     independence tests stays visible. *)
  duration_shape : float option;
  duration_statistic : float option;
  duration_p : float option;
  zone : Zone.t;
  zone_cumulative_probability : float;
  (* The worst single realised loss, and the forecast that failed to cover it.
     A model can pass every test above and still have been wrong by a mile once,
     which is the observation that motivates reading expected shortfall
     alongside VaR. *)
  worst_loss : float;
  var_at_worst_loss : float;
}
[@@deriving fields ~getters]

let run ~(observations : Observation.t list) ~(estimator : Estimator.t)
    ~(confidence : float) : report =
  if List.is_empty observations then
    invalid_arg "var_backtest: cannot run a backtest on an empty observation series";
  Risk_metrics.validate_confidence ~confidence;
  let hits = exceedances observations in
  let n = Array.length hits in
  let x = Array.count hits ~f:Fn.id in
  let kupiec_statistic, kupiec_p = kupiec_pof ~n ~x ~confidence in
  let independence_statistic, independence_p =
    christoffersen_independence ~exceedances:hits
  in
  (* The joint statistic is the SUM of the two, not a separately estimated
     quantity: Christoffersen's decomposition is exactly that conditional
     coverage factors into coverage times independence, so the log-likelihood
     ratios add and so do the degrees of freedom. Computing it any other way
     would be a third model to keep consistent with the first two. *)
  let conditional_coverage_statistic = kupiec_statistic +. independence_statistic in
  let conditional_coverage_p = chi2_p ~statistic:conditional_coverage_statistic ~df:2 in
  let duration = duration_independence ~exceedances:hits in
  let zone, zone_cumulative_probability = traffic_light ~n ~x ~confidence in
  let worst =
    List.min_elt observations ~compare:(fun a b ->
        Float.compare (Observation.realised a) (Observation.realised b))
    |> Option.value_exn ~message:"var_backtest: non-empty list has a minimum"
  in
  {
    estimator;
    confidence;
    observations = n;
    exceptions = x;
    expected_exceptions = float_of_int n *. (1.0 -. confidence);
    observed_rate = float_of_int x /. float_of_int n;
    expected_rate = 1.0 -. confidence;
    kupiec_statistic;
    kupiec_p;
    independence_statistic;
    independence_p;
    conditional_coverage_statistic;
    conditional_coverage_p;
    duration_shape = Option.map duration ~f:(fun (shape, _, _) -> shape);
    duration_statistic = Option.map duration ~f:(fun (_, statistic, _) -> statistic);
    duration_p = Option.map duration ~f:(fun (_, _, p) -> p);
    zone;
    zone_cumulative_probability;
    worst_loss = -.Observation.realised worst;
    var_at_worst_loss = Observation.var worst;
  }

(* Convenience: forecast and test in one call, which is how every caller uses
   it. Kept as a composition of the two exported steps rather than a separate
   path, so there is no second place for the point-in-time discipline to be got
   wrong. *)
let of_returns ~(returns : float array) ~(window : int) ~(confidence : float)
    ~(estimator : Estimator.t) : report =
  run
    ~observations:(rolling ~returns ~window ~confidence ~estimator)
    ~estimator ~confidence

(* Reject at the conventional 5%.

   A helper rather than a field, because "did it pass" is a decision made
   against a threshold that belongs to the reader, not to the test. Conditional
   coverage is the one to gate on: it is the joint null, and gating on the two
   components separately without correcting for testing twice is exactly the
   multiple-comparison mistake this whole module is supposed to be above. *)
let rejected ?(alpha = 0.05) (r : report) : bool =
  Float.( < ) r.conditional_coverage_p alpha

let verdict ?(alpha = 0.05) (r : report) : string =
  if rejected ~alpha r then
    Printf.sprintf "REJECTED at %.0f%% (conditional coverage p = %.4f)" (alpha *. 100.0)
      r.conditional_coverage_p
  else
    Printf.sprintf "not rejected at %.0f%% (conditional coverage p = %.4f)"
      (alpha *. 100.0) r.conditional_coverage_p

let to_string (r : report) : string =
  let pct f = Printf.sprintf "%.2f%%" (f *. 100.0) in
  String.concat ~sep:"\n"
    [
      Printf.sprintf "  estimator            %s at %s confidence"
        (Estimator.to_string r.estimator)
        (pct r.confidence);
      Printf.sprintf "  observations         %d" r.observations;
      Printf.sprintf "  exceptions           %d observed, %.1f expected (%s vs %s)"
        r.exceptions r.expected_exceptions (pct r.observed_rate) (pct r.expected_rate);
      Printf.sprintf "  worst loss           %s against a %s forecast" (pct r.worst_loss)
        (pct r.var_at_worst_loss);
      Printf.sprintf "  Kupiec  (coverage)   LR = %7.3f   p = %.4f" r.kupiec_statistic
        r.kupiec_p;
      Printf.sprintf "  Christoffersen (ind) LR = %7.3f   p = %.4f"
        r.independence_statistic r.independence_p;
      Printf.sprintf "  conditional coverage LR = %7.3f   p = %.4f"
        r.conditional_coverage_statistic r.conditional_coverage_p;
      (match (r.duration_shape, r.duration_statistic, r.duration_p) with
      | Some shape, Some statistic, Some p ->
          Printf.sprintf
            "  duration (ind)       LR = %7.3f   p = %.4f   Weibull shape %.3f (%s)"
            statistic p shape
            (if Float.( < ) shape 0.95 then "clustered"
             else if Float.( > ) shape 1.05 then "over-regular"
             else "memoryless")
      | _ -> "  duration (ind)       not applicable (fewer than two exceptions)");
      Printf.sprintf "  Basel zone           %s (P[X <= %d] = %.4f)"
        (Zone.to_string r.zone) r.exceptions r.zone_cumulative_probability;
      Printf.sprintf "  verdict              %s" (verdict r);
    ]
