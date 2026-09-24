(* Phase 5: risk decomposition.

   Every number in risk_metrics.ml answers "how much risk is there". None of
   them answers "where is it", and on a real book that is the more actionable
   question. A portfolio VaR of $12,000 does not tell you what to sell. A
   decomposition that says NVDA is 61% of it does.

   THE DECOMPOSITION

   Portfolio volatility sigma_p = sqrt(w' S w) is homogeneous of degree one in
   the weights: scale every position by k and sigma_p scales by k. Euler's
   theorem for homogeneous functions then gives an exact additive split,

       sigma_p = sum_i w_i * d(sigma_p)/d(w_i)

   with no approximation and no residual. The i-th term is instrument i's
   CONTRIBUTION to portfolio risk, and because the split is exact the terms can
   be summed over any partition of the book -- a sector, a strategy, a desk --
   and the parts still add to the whole. That property is the entire reason to
   prefer this over standalone risk, which does not add to anything.

   Three quantities, and they are routinely confused:

     marginal    d(sigma_p)/d(w_i). The rate of change: how much portfolio risk
                 moves per unit increase in this weight. A sensitivity, not an
                 amount. Answers "should I add or trim".

     component   w_i * marginal_i. An amount, in the same units as sigma_p,
                 and these are what sum to the total. Answers "how much of my
                 risk is this".

     standalone  |w_i| * sigma_i. What the position would contribute if it were
                 perfectly correlated with the rest of the book. Always at least
                 as large as |component|; the gap is the diversification the
                 position is buying.

   SIGNS ARE REAL AND MUST NOT BE ABSOLUTE-VALUED

   A component contribution can be negative. That is not an error and it is not
   noise: it means the position moves against the rest of the book, so holding
   it makes the portfolio less volatile. Taking |.| here -- which is easy to do
   by reflex, since VaR itself is reported as a positive magnitude -- would
   report a hedge as a risk contributor and would break the additivity that is
   the whole point. The tests assert the sum, which is what catches this.

   WHY PARAMETRIC AND NOT HISTORICAL

   This decomposition needs a differentiable closed form for portfolio risk, and
   only the covariance path has one. Historical VaR is an order statistic of the
   observed return sample -- it is a single observation, and its derivative with
   respect to a weight is a step function that is zero almost everywhere. There
   are kernel-smoothed estimators of component historical VaR; they are noisy at
   the sample sizes this engine runs on, and a noisy attribution is worse than
   none because it invites trading against sampling error.

   So: attribution here is the Gaussian one, and it inherits the Gaussian
   assumption's thin tails. It says how risk is SHARED OUT, which is a question
   about correlation structure and is fairly robust, rather than how large the
   tail IS, which is the question normality gets wrong. Read it alongside
   graph.ml's historical VaR, not instead of it.

   Nothing here references Incremental. graph.ml wires these into nodes. *)

open Core

(* The book's per-unit-gross risk decomposition.

   All fields are in return space -- fractions of gross exposure, matching
   [Risk_metrics.portfolio_stddev] -- and are converted to dollars by graph.ml,
   which owns the gross exposure edge. Keeping the conversion out of here means
   this module never has to know what a Notional is. *)
type t = {
  (* sigma_p = sqrt(w' S w). Strictly positive; a book with no risk yields
     [None] from [compute] rather than a record with a zero here, because every
     other field would be a division by it. *)
  portfolio_stddev : float;
  (* d(sigma_p)/d(w_i), one per instrument, in the order of the weights. *)
  marginal : float array;
  (* w_i * marginal_i. Sums to [portfolio_stddev] exactly (Euler). *)
  component : float array;
  (* component_i / sigma_p. Sums to 1.0. Dimensionless, so it is the field a
     display sorts on -- percentages are comparable across books and dollars
     are not. *)
  percent : float array;
  (* |w_i| * sigma_i: the position's risk ignoring every correlation. *)
  standalone : float array;
}
[@@deriving fields ~getters]

(* Sum of standalone risks over portfolio risk, >= 1 by the triangle
   inequality.

   The single number that says how much the book is getting from correlation
   structure. 1.0 means every position moves together and there is no
   diversification at all; 2.0 means the book carries half the volatility that
   its individual positions would suggest. Worth watching over time, because it
   collapses toward 1.0 in a crisis -- correlations going to one is what a
   crisis IS, mechanically, and this is the number that shows it happening. *)
let diversification_ratio (t : t) : float =
  Array.fold t.standalone ~init:0.0 ~f:( +. ) /. t.portfolio_stddev

(* Decompose portfolio risk across the positions that make it up.

   [weights] are signed and normalised by gross exposure, so their absolute
   values sum to one -- the same convention as graph.ml's weights node, because
   these are the same weights.

   [covariance] must be the n x n matrix over the same instruments in the same
   order. graph.ml guarantees the ordering by deriving both from [Symbol.Map],
   whose iteration order is its key order.

   Returns [None] when the book carries no risk to divide up: a flat book (all
   weights zero) or a degenerate covariance matrix. That is a real state during
   warm-up and it is not an error, but every field of [t] divides by
   [portfolio_stddev], so there is nothing to return. Callers surface it the
   same way they surface a warming-up VaR: as unknown, never as zero. *)
let compute ~(weights : float array) ~(covariance : Owl.Mat.mat) : t option =
  let n = Array.length weights in
  if n = 0 then invalid_arg "attribution: weights must be non-empty";
  let rows, cols = Owl.Mat.shape covariance in
  if rows <> n || cols <> n then
    invalid_argf "attribution: covariance must be %dx%d to match weights, got %dx%d" n n
      rows cols ();
  let sigma_p = Risk_metrics.portfolio_stddev ~weights ~covariance in
  (* Not [Float.equal sigma_p 0.0]. A book of near-offsetting positions can
     produce a variance of 1e-34 whose square root is 1e-17: non-zero, so the
     equality test passes it through, and then every marginal is a rounding
     residue divided by another one. The threshold is relative to the largest
     single-instrument volatility, so it scales with the book rather than
     assuming one. *)
  let largest_own_vol =
    Array.foldi weights ~init:0.0 ~f:(fun i acc _ ->
        Float.max acc (Float.sqrt (Float.max 0.0 (Owl.Mat.get covariance i i))))
  in
  if Float.( <= ) sigma_p (1e-12 *. Float.max largest_own_vol 1.0) then None
  else begin
    (* S w, once. Every marginal is one entry of it, so computing this as a
       single matrix-vector product rather than n dot products keeps the whole
       decomposition at one BLAS call. *)
    let w = Owl.Mat.of_array weights n 1 in
    let sw = Owl.Mat.dot covariance w in
    let marginal = Array.init n ~f:(fun i -> Owl.Mat.get sw i 0 /. sigma_p) in
    let component = Array.init n ~f:(fun i -> weights.(i) *. marginal.(i)) in
    let percent = Array.map component ~f:(fun c -> c /. sigma_p) in
    let standalone =
      Array.init n ~f:(fun i ->
          Float.abs weights.(i) *. Float.sqrt (Float.max 0.0 (Owl.Mat.get covariance i i)))
    in
    Some { portfolio_stddev = sigma_p; marginal; component; percent; standalone }
  end

(* The Euler residual: sum of components minus the total it should equal.

   Exposed rather than kept in the tests because it is a cheap self-check that
   stays true at run time. The identity is exact in real arithmetic, so anything
   here beyond float accumulation error means the covariance matrix and the
   weights have gone out of alignment -- which is the one failure mode of this
   module that produces confident, plausible, entirely wrong attributions. *)
let euler_residual (t : t) : float =
  Array.fold t.component ~init:0.0 ~f:( +. ) -. t.portfolio_stddev

(* Component VaR: the same split, scaled into VaR units.

   Parametric VaR is z * sigma_p for a constant z that does not depend on the
   weights, so scaling every component by that same z preserves the Euler
   identity -- the component VaRs sum to the portfolio's parametric VaR exactly.
   That is what makes a per-instrument VaR limit well posed, and it is why
   limits.ml can accept an instrument-scoped [Component_var] while still
   refusing an instrument-scoped [Value_at_risk]: a standalone VaR per name adds
   to nothing and would double-count correlated risk, while these add to the
   total by construction.

   Positive is the desk convention for a loss, as everywhere else here. *)
let component_var (t : t) ~(confidence : float) : float array =
  Risk_metrics.validate_confidence ~confidence;
  let z = -.Risk_metrics.normal_ppf ~p:(1.0 -. confidence) in
  Array.map t.component ~f:(fun c -> z *. c)
