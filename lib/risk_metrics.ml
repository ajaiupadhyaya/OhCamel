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
     calling, so these exceptions should never reach a node body. *)

open Core

let validate_confidence ~confidence =
  if not (Float.( > ) confidence 0.0 && Float.( < ) confidence 1.0) then
    invalid_argf "risk_metrics: confidence must be strictly between 0 and 1, got %f"
      confidence ()

let validate_non_empty ~name (xs : float array) =
  if Array.is_empty xs then invalid_argf "risk_metrics: %s must be non-empty" name ()

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
let covariance xs ys =
  validate_non_empty ~name:"series" xs;
  if Array.length xs <> Array.length ys then
    invalid_argf "risk_metrics: series length mismatch (%d vs %d)" (Array.length xs)
      (Array.length ys) ();
  let mx = mean xs and my = mean ys in
  let total =
    Array.foldi xs ~init:0.0 ~f:(fun i acc x -> acc +. ((x -. mx) *. (ys.(i) -. my)))
  in
  total /. float_of_int (Array.length xs)

let variance xs = covariance xs xs
let stddev xs = Float.sqrt (variance xs)

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
  Array.iteri series ~f:(fun i s ->
      if Array.length s <> len then
        invalid_argf "risk_metrics: series %d has length %d, expected %d" i
          (Array.length s) len ());
  let m = Owl.Mat.zeros n n in
  for i = 0 to n - 1 do
    (* Symmetric, so only compute the upper triangle and mirror it. Besides
       halving the work, this guarantees exact symmetry -- computing both halves
       independently can leave them differing in the last bit, which is enough to
       make a matrix fail a positive-definiteness check downstream. *)
    for j = i to n - 1 do
      let c = covariance series.(i) series.(j) in
      Owl.Mat.set m i j c;
      Owl.Mat.set m j i c
    done
  done;
  m

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
