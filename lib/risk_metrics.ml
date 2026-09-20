(* Pure risk functions.

   Nothing here references Incremental, and nothing here is stateful. Every
   function is a plain map from numbers to numbers, so each can be unit-tested
   against a hand-computed value and reused outside the live engine -- called
   directly from the backtester, for instance.

   CONVENTIONS

   - Returns are simple fractional returns (0.01 = +1%), not log returns, and not
     percent. Mixing the two is a silent 1% error at small magnitudes and a large
     one in the tail, which is exactly where these functions are read.

   - VaR and Expected Shortfall are reported as POSITIVE loss magnitudes. A VaR
     of 0.05 means "a loss of 5% of value". This is the desk convention and it
     keeps limit comparisons in the obvious direction (observed > threshold means
     trouble). A negative result is meaningful and not an error: it says the
     tail quantile is still a gain.

   - Structurally invalid input raises. An empty return window is a bug in the
     caller, not a market condition, and the alternative -- returning 0.0 --
     renders as "no risk" on a dashboard, which is the single most dangerous
     wrong answer this module could give. Callers in graph.ml guard before
     calling, so these exceptions should never reach a node body.

   - A NON-FINITE observation is structurally invalid in exactly that sense,
     and raises for exactly that reason. [Long_panel] is this tree's first
     legitimate producer of arrays containing [nan] -- an instrument's returns
     carry one on every panel date it has no bar for, and the rates factor
     carries one on its trailing unpublished run -- and float arithmetic does
     not raise on [nan], it propagates it. A window here handed such an array
     returned [nan], which renders on a page as a figure this module vouched
     for. So every function that reads a whole series checks it
     ([validate_finite] below), and the caller's job is to have removed the
     gaps first: Long_panel.present_returns does that, Factor_model drops whole
     [nan] rows, Liquidity.line guards with [is_finite]. Raising, rather than
     returning an [Error] or an option: a [nan] here is a programming error at
     the call site, one of a kind with an empty window, and the same argument
     applies -- a [None] or an [Error] that a caller resolved with a default
     would put 0.0 back on the dashboard. [cornish_fisher_var] is the one
     exception and returns an [Error], because it was written that way for
     four refusals a caller may want to REPORT (too few rows, a flat series,
     a non-monotone expansion), and a non-finite input joined that list. *)

open Core

let validate_confidence ~confidence =
  if not (Float.( > ) confidence 0.0 && Float.( < ) confidence 1.0) then
    invalid_argf "risk_metrics: confidence must be strictly between 0 and 1, got %f"
      confidence ()

let validate_non_empty ~name (xs : float array) =
  if Array.is_empty xs then invalid_argf "risk_metrics: %s must be non-empty" name ()

(* Every observation finite, or [Invalid_argument] -- see the non-finite
   convention in the header for why it raises rather than returning an [Error].

   [who] is the name of the function the CALLER called, not of whichever one
   ends up reading the array: [stddev] delegates to the covariance arithmetic,
   and a message reading "risk_metrics: covariance" for a [stddev] call would
   send a reader to the wrong line. ([is_effectively_constant] and [beta] have
   no [who] of their own and inherit their delegate's.)

   Names the index and the value rather than just "not finite". On a
   250-observation panel those two are what tell a missing observation ([nan],
   Long_panel's own marker for a date with no bar) apart from an infinity,
   which can only have come from a zero close upstream and is a data error
   rather than a gap. *)
let validate_finite ~who (xs : float array) =
  match Array.findi xs ~f:(fun _ x -> not (Float.is_finite x)) with
  | None -> ()
  | Some (i, x) ->
      invalid_argf "risk_metrics: %s: observation %d of %d is %g, not a finite number" who
        i (Array.length xs) x ()

(* How many observations fall in the loss tail at this confidence level.

   Uses the nearest-rank convention: at 95% over 100 observations the tail is the
   worst 5. Always at least 1 -- a confidence so high that the tail rounds to
   zero observations should give the single worst loss, not a division by zero.

   The epsilon is not decoration. (1.0 -. 0.70) *. 10.0 evaluates to
   3.0000000000000004, and a bare ceiling turns that into 4, quietly widening
   the tail by one observation and biasing every VaR computed at that confidence.
   Subtracting a tolerance before rounding up removes the artefact without
   affecting genuinely fractional ranks. *)
let tail_count ~n ~confidence =
  let raw = (1.0 -. confidence) *. float_of_int n in
  let k = Float.iround_up_exn (raw -. 1e-9) in
  Int.max 1 (Int.min n k)

(* The [tail_count] worst returns, ascending (most negative first). *)
let loss_tail ~returns ~confidence =
  let sorted = Array.copy returns in
  Array.sort sorted ~compare:Float.compare;
  Array.sub sorted ~pos:0 ~len:(tail_count ~n:(Array.length returns) ~confidence)

(* Historical (empirical) VaR: the loss at the confidence quantile of the
   observed return distribution.

   Makes no distributional assumption, which is its whole appeal -- but it can
   only report losses it has already seen, so it is blind to any tail the window
   does not contain. That is the argument for reading it alongside
   [expected_shortfall] rather than instead of it. *)
let historical_var ~returns ~confidence =
  validate_confidence ~confidence;
  validate_non_empty ~name:"returns" returns;
  (* Before the sort, not after: [Float.compare] gives [nan] a place in the
     total order (below every number), so a [nan] observation sorts INTO the
     loss tail and comes back out as the reported VaR. *)
  validate_finite ~who:"historical_var" returns;
  let tail = loss_tail ~returns ~confidence in
  -.tail.(Array.length tail - 1)

(* Expected Shortfall (CVaR): the mean loss given that the VaR threshold was
   breached.

   The README asks for this to be prioritised over VaR, and the reason is
   structural: VaR reports where the tail begins and says nothing about its
   shape, so two books with identical VaR can have completely different
   behaviour past it. ES averages over the tail and therefore responds to how
   bad the bad case actually is.

   ES >= VaR always, with equality only when the tail holds a single
   observation. That invariant is asserted in the tests. *)
let expected_shortfall ~returns ~confidence =
  validate_confidence ~confidence;
  validate_non_empty ~name:"returns" returns;
  validate_finite ~who:"expected_shortfall" returns;
  let tail = loss_tail ~returns ~confidence in
  -.(Array.fold tail ~init:0.0 ~f:( +. ) /. float_of_int (Array.length tail))

(* The standard normal quantile, e.g. -1.6449 at p = 0.05.

   Delegated to Owl rather than hand-rolled: an inverse-CDF approximation is easy
   to write and easy to get subtly wrong in the far tail, which is the only place
   it is ever evaluated here. *)
let normal_ppf ~p = Owl.Stats.gaussian_ppf p ~mu:0.0 ~sigma:1.0

(* Parametric (variance-covariance) VaR for a single return series, assuming
   normality.

   Faster and smoother than the historical estimate, and it can extrapolate past
   the observed sample -- but it inherits the normal distribution's thin tails,
   so it will understate risk on a book with real tail exposure. Both are
   computed in the graph on purpose: the gap between them is itself the
   diagnostic. *)
let parametric_var ~mean ~stddev ~confidence =
  validate_confidence ~confidence;
  (* Both scalars, and BEFORE the sign check: [Float.is_negative nan] is false,
     so a [nan] sigma passed the old guard untouched and came back as a [nan]
     VaR. This is also where a [nan] out of [mean] or [stddev] over a panel
     window arrives, one call later, when a caller computed the moments
     itself -- var_backtest.ml passes both. *)
  if not (Float.is_finite mean) then
    invalid_argf "risk_metrics: parametric_var: mean is %g, not a finite number" mean ();
  if not (Float.is_finite stddev) then
    invalid_argf "risk_metrics: parametric_var: stddev is %g, not a finite number" stddev
      ();
  if Float.is_negative stddev then
    invalid_argf "risk_metrics: stddev must be non-negative, got %f" stddev ();
  let z = normal_ppf ~p:(1.0 -. confidence) in
  -.(mean +. (z *. stddev))

let mean xs =
  validate_non_empty ~name:"series" xs;
  Array.fold xs ~init:0.0 ~f:( +. ) /. float_of_int (Array.length xs)

(* Population (not sample) moments throughout.

   Beta is a ratio of a covariance to a variance, so the 1/n versus 1/(n-1)
   choice cancels there provided both use the same denominator -- which is the
   real reason to fix one convention here and use it everywhere rather than
   mixing. *)
(* The arithmetic alone, on input a caller above has already checked. Split out
   so [covariance_matrix] can sweep each of its n series for a non-finite
   observation ONCE, naming the series, instead of re-sweeping the same two
   arrays inside each of its n(n+1)/2 inner calls.

   Internal by convention, not by an interface: this library declares no .mli,
   so the name is reachable from outside and the discipline has to be read
   rather than enforced. Every call inside this module validates first; anything
   outside it wanting a covariance wants [covariance], which does. *)
let covariance_core xs ys =
  let mx = mean xs and my = mean ys in
  let total =
    Array.foldi xs ~init:0.0 ~f:(fun i acc x -> acc +. ((x -. mx) *. (ys.(i) -. my)))
  in
  total /. float_of_int (Array.length xs)

(* [who] names the function the caller actually called -- see [validate_finite]. *)
let covariance_checked ~who xs ys =
  validate_non_empty ~name:"series" xs;
  if Array.length xs <> Array.length ys then
    invalid_argf "risk_metrics: series length mismatch (%d vs %d)" (Array.length xs)
      (Array.length ys) ();
  validate_finite ~who xs;
  validate_finite ~who ys;
  covariance_core xs ys

let covariance xs ys = covariance_checked ~who:"covariance" xs ys

(* [variance] and [stddev] pass their own names rather than inheriting
   [covariance]'s: the two are the same computation, but a caller reading a
   message wants the line it wrote. *)
let variance xs = covariance_checked ~who:"variance" xs xs
let stddev xs = Float.sqrt (covariance_checked ~who:"stddev" xs xs)

(* Is this series constant for practical purposes?

   Not the same question as [variance xs = 0.0], and the difference matters more
   than it looks. Take ten copies of 0.0425: the true variance is zero, but the
   mean cannot be represented exactly, so each deviation is a rounding residue
   around 1e-17 and the computed variance is about 1e-33 -- small, but not zero.
   Divide one such residue by another, as [beta] does, and the answer is a ratio
   of two noise terms: a number like -0.3, finite and plausible and completely
   fabricated. On a dashboard that reads as "the book is inversely exposed to
   rates", which is a claim about the world derived entirely from float error.

   So the test is relative to the series' own magnitude. A relative standard
   deviation below 1e-12 is not a small movement; it is four orders of magnitude
   below anything float arithmetic can distinguish from noise at this scale, and
   twelve below anything an economic series does.

   The all-zero series is constant by definition and is handled first, since
   there is no magnitude to be relative to. *)
let is_effectively_constant ?(relative_tolerance = 1e-12) xs =
  validate_non_empty ~name:"series" xs;
  let scale = Array.fold xs ~init:0.0 ~f:(fun acc x -> Float.max acc (Float.abs x)) in
  if Float.equal scale 0.0 then true
  else Float.( <= ) (stddev xs) (relative_tolerance *. scale)

(* Rolling beta of an asset against a factor: cov(asset, factor) / var(factor).

   The caller decides what "rolling" means by choosing the window it passes; this
   function has no memory. Raises if the factor never moves, since beta is
   genuinely undefined there rather than zero -- a constant factor explains
   nothing, and reporting 0.0 would read as "no exposure". *)
let beta ~asset ~factor =
  (* [is_effectively_constant] rather than a test against exact zero. A factor
     that is constant up to float noise divides one rounding residue by another
     and returns a finite, plausible, entirely fabricated number -- see the note
     on that function. Raising here is the same judgement as the original: a
     constant factor explains nothing, so beta is undefined, and 0.0 would be
     misread as "no exposure".

     Live callers must not let this escape a node body. graph.ml checks the same
     predicate before calling, because a flat rate series is an ordinary day. *)
  if is_effectively_constant factor then
    invalid_arg
      "risk_metrics: factor series does not move, beta is undefined (a constant factor \
       explains nothing; 0.0 would be misread as 'no exposure')";
  covariance asset factor /. variance factor

(* Portfolio standard deviation from weights and a covariance matrix:
   sqrt(w' * Sigma * w).

   Goes through Owl (and therefore BLAS) rather than a hand-written double loop,
   because this is the operation that grows with the square of the book and is
   the first thing to become hot. *)
let portfolio_stddev ~weights ~covariance:cov =
  let n = Array.length weights in
  if n = 0 then invalid_arg "risk_metrics: weights must be non-empty";
  let rows, cols = Owl.Mat.shape cov in
  if rows <> n || cols <> n then
    invalid_argf "risk_metrics: covariance must be %dx%d to match weights, got %dx%d" n n
      rows cols ();
  let w = Owl.Mat.of_array weights 1 n in
  (* w (1xn) * Sigma (nxn) * w' (nx1) -> 1x1 *)
  let variance =
    Owl.Mat.get (Owl.Mat.dot (Owl.Mat.dot w cov) (Owl.Mat.transpose w)) 0 0
  in
  (* Rounding can push a variance that is mathematically zero very slightly
     negative; clamp rather than return nan from sqrt. Genuinely negative
     variance would mean a non-PSD covariance matrix, which is a caller bug, but
     it is not distinguishable from float noise at this magnitude. *)
  Float.sqrt (Float.max 0.0 variance)

(* Parametric VaR for a whole book. *)
let portfolio_parametric_var ~weights ~covariance:cov ~confidence =
  validate_confidence ~confidence;
  let sigma = portfolio_stddev ~weights ~covariance:cov in
  let z = normal_ppf ~p:(1.0 -. confidence) in
  -.z *. sigma

(* Sample covariance matrix from per-instrument return series.

   [series.(i)] is instrument i's return window; all must be the same length. *)
let covariance_matrix (series : float array array) =
  if Array.is_empty series then invalid_arg "risk_metrics: need at least one series";
  let n = Array.length series in
  let len = Array.length series.(0) in
  (* One sweep per series, up front, naming the series: in an n x T panel the
     index is the only thing that says WHICH instrument has the gap, and the
     inner loop below reads every series n times over. [validate_non_empty] is
     hoisted here for the same reason -- the inner calls no longer do it. *)
  Array.iteri series ~f:(fun i s ->
      if Array.length s <> len then
        invalid_argf "risk_metrics: series %d has length %d, expected %d" i
          (Array.length s) len ();
      validate_non_empty ~name:"series" s;
      validate_finite ~who:(Printf.sprintf "covariance_matrix: series %d" i) s);
  let m = Owl.Mat.zeros n n in
  for i = 0 to n - 1 do
    (* Symmetric, so only compute the upper triangle and mirror it. Besides
       halving the work, this guarantees exact symmetry -- computing both halves
       independently can leave them differing in the last bit, which is enough to
       make a matrix fail a positive-definiteness check downstream. *)
    for j = i to n - 1 do
      let c = covariance_core series.(i) series.(j) in
      Owl.Mat.set m i j c;
      Owl.Mat.set m j i c
    done
  done;
  m

(* Cornish-Fisher expansion: a normal-quantile correction built from a
   series' own skewness and excess kurtosis, so a VaR estimate can respond to
   a fat or skewed tail without either assuming the empirical history already
   contains its worst case (as [historical_var] does) or discarding the third
   and fourth moments entirely (as [parametric_var] does).

   Population moments throughout -- same divide-by-n convention as
   [variance] and everywhere else in this module, never n-1. *)

(* The population 2nd, 3rd and 4th central moments in one pass, sharing the
   demeaning step and the squared deviation between the 3rd and 4th powers.
   Built from explicit multiplications rather than [Float.( ** )]: these are
   only ever a square and a cube, which a multiply chain computes exactly,
   with no call into a general pow routine. *)
let central_moments xs =
  let m = mean xs in
  let n = float_of_int (Array.length xs) in
  let s2 = ref 0.0 and s3 = ref 0.0 and s4 = ref 0.0 in
  Array.iter xs ~f:(fun x ->
      let d = x -. m in
      let d2 = d *. d in
      s2 := !s2 +. d2;
      s3 := !s3 +. (d2 *. d);
      s4 := !s4 +. (d2 *. d2));
  (!s2 /. n, !s3 /. n, !s4 /. n)

(* g1 = m3 / m2^1.5, written as [m2 *. sqrt m2] rather than a general
   exponentiation to 1.5 -- the same value, but computed as "times its own
   square root" instead of through a pow routine meant for arbitrary
   exponents. *)
let skewness xs =
  let m2, m3, _ = central_moments xs in
  m3 /. (m2 *. Float.sqrt m2)

let excess_kurtosis xs =
  let m2, _, m4 = central_moments xs in
  (m4 /. (m2 *. m2)) -. 3.0

(* The Cornish-Fisher quantile correction, given a standard normal quantile z
   and a series' population skew and excess kurtosis. Zero skew and zero
   excess kurtosis leave z untouched: every correction term carries a g1 or a
   g2 factor, so the expansion collapses to the identity exactly, not merely
   in the limit. *)
let cornish_fisher_z ~z ~skew ~excess_kurtosis =
  let g1 = skew and g2 = excess_kurtosis in
  let z2 = z *. z in
  let z3 = z2 *. z in
  z
  +. ((z2 -. 1.0) *. g1 /. 6.0)
  +. ((z3 -. (3.0 *. z)) *. g2 /. 24.0)
  -. (((2.0 *. z3) -. (5.0 *. z)) *. g1 *. g1 /. 36.0)

(* The expansion is a valid quantile transform only where it strictly
   increases in z: past that point, a larger standard-normal quantile could
   map to a *smaller* Cornish-Fisher one, which is incoherent for anything
   read as a quantile.

   Decided in closed form, not by sampling a grid: a grid can miss a
   violation narrower than its spacing, and the reviewer's counterexample
   test below is exactly such a case. The derivative of [cornish_fisher_z]
   with respect to z is
     1 + z*g1/3 + (z^2-1)*g2/8 - (6z^2-5)*g1^2/36
   Expanding (z^2-1)*g2/8 as z^2*g2/8 - g2/8, and -(6z^2-5)*g1^2/36 as
   -z^2*g1^2/6 + 5*g1^2/36, and collecting powers of z, this is the
   quadratic A*z^2 + B*z + C with
     A = g2/8 - g1^2/6
     B = g1/3
     C = 1 - g2/8 + 5*g1^2/36

   The expansion is strictly increasing on [-4, 4] iff this quadratic's
   minimum over that interval is strictly positive. A downward-opening or
   linear quadratic (A <= 0) attains its minimum over a closed interval at
   an endpoint, never in the interior. An upward-opening one (A > 0) is
   minimised at its vertex -B/(2A) when that vertex falls inside the
   interval; otherwise it is monotonic across the interval and, like every
   A <= 0 case, its minimum there is at an endpoint. Evaluating both
   endpoints, plus the vertex when A > 0 and the vertex lies in [-4, 4],
   covers every case exactly. *)
let cornish_fisher_is_monotone ~skew ~excess_kurtosis =
  let g1 = skew and g2 = excess_kurtosis in
  let a = (g2 /. 8.0) -. (g1 *. g1 /. 6.0) in
  let b = g1 /. 3.0 in
  let c = 1.0 -. (g2 /. 8.0) +. (5.0 *. g1 *. g1 /. 36.0) in
  let f z = (a *. z *. z) +. (b *. z) +. c in
  let lo = -4.0 and hi = 4.0 in
  let candidates = [ f lo; f hi ] in
  let candidates =
    if Float.( > ) a 0.0 then
      let vertex = -.b /. (2.0 *. a) in
      if Float.( >= ) vertex lo && Float.( <= ) vertex hi then f vertex :: candidates
      else candidates
    else candidates
  in
  Float.( > ) (List.fold candidates ~init:Float.infinity ~f:Float.min) 0.0

(* The fewest observations a THIRD- AND FOURTH-MOMENT estimate here is trusted
   on: below this, [cornish_fisher_var] (just below) is estimating a skewness
   and an excess kurtosis from too little data and reporting the result as a
   figure. Named for the property, not for the module, because it is shared
   with a genuinely SEPARATE decision that currently lands on the same number:
   [Factor_model.min_observations] is about degrees of freedom -- six OLS
   parameters, an intercept and five betas, fitted without fitting noise -- and
   the two arguments have nothing in common but their answer.

   Shared anyway, rather than written twice as a literal, so that the two
   cannot drift apart by a typo: [Factor_model.min_observations] is defined as
   this value, which is safe because [Factor_model] already depends on this
   module (it calls [is_effectively_constant], [mean], [stddev] and
   [covariance_matrix]) while this module depends on nothing in it -- the
   reference runs one way and makes no cycle. test_factor_model.ml pins both
   halves: that the two floors agree at 120 today, and that Factor_model's is
   still READING this constant rather than having quietly acquired a literal of
   its own. Moving one floor on a new argument is a legitimate change; the
   name is what keeps it from reading as a bug. *)
let min_observations_higher_moment = 120

(* Cornish-Fisher VaR for a single return series: the same zero-mean,
   population-sigma convention [portfolio_parametric_var] uses, with the
   normal quantile replaced by its skew/kurtosis-corrected counterpart.
   [Error], never a number that merely looks plausible, in the four cases
   the expansion cannot be trusted: a non-finite observation (which would
   otherwise poison every downstream moment silently, since nan and
   infinity propagate through arithmetic without raising), too little data
   to estimate a third and fourth moment at all, a series with no variation
   to standardise by, or a moment combination that makes the expansion
   non-monotone. *)
let cornish_fisher_var ~returns ~confidence =
  validate_confidence ~confidence;
  let n = Array.length returns in
  let non_finite = Array.count returns ~f:(fun x -> not (Float.is_finite x)) in
  if non_finite > 0 then
    Error
      (Printf.sprintf
         "risk_metrics: cornish_fisher_var: %d of %d observations are not finite"
         non_finite n)
  else if n < min_observations_higher_moment then
    Error
      (Printf.sprintf
         "risk_metrics: cornish_fisher_var needs at least %d observations, got %d"
         min_observations_higher_moment n)
  else if is_effectively_constant returns then
    Error
      "risk_metrics: cornish_fisher_var: series is effectively constant, skewness and \
       kurtosis are undefined"
  else
    let g1 = skewness returns in
    let g2 = excess_kurtosis returns in
    if not (cornish_fisher_is_monotone ~skew:g1 ~excess_kurtosis:g2) then
      Error "risk_metrics: cornish_fisher_var: outside the expansion's valid region"
    else
      let z = normal_ppf ~p:(1.0 -. confidence) in
      let z_cf = cornish_fisher_z ~z ~skew:g1 ~excess_kurtosis:g2 in
      Ok (-.(z_cf *. stddev returns))

(* Peak-to-trough decline as a positive fraction of the peak.

   [max_drawdown] is the worst such decline anywhere in the series; it is a
   historical fact and never improves. [current_drawdown] is the decline from the
   running peak to the latest point, and recovers as the book does.

   The circuit breaker in limits.ml uses the current value, not the max: a
   breaker keyed to the max would latch on forever after one bad morning. *)
let max_drawdown ~equity =
  validate_non_empty ~name:"equity" equity;
  let worst, _ =
    Array.fold equity ~init:(0.0, Float.neg_infinity) ~f:(fun (worst, peak) v ->
        let peak = Float.max peak v in
        (* Guard the divisor: an equity curve that starts at or crosses zero would
         otherwise produce infinity or nan and propagate it into a limit check. *)
        let dd = if Float.( > ) peak 0.0 then (peak -. v) /. peak else 0.0 in
        (Float.max worst dd, peak))
  in
  worst

let current_drawdown ~equity =
  validate_non_empty ~name:"equity" equity;
  let peak = Array.fold equity ~init:Float.neg_infinity ~f:Float.max in
  let latest = equity.(Array.length equity - 1) in
  if Float.( > ) peak 0.0 then (peak -. latest) /. peak else 0.0
