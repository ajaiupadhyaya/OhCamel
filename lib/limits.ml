(* Phase 1 (definitions and breach logic) -> Phase 4 (side effects).

   Limit definitions and breach evaluation. A breach is data -- a bool plus the
   magnitude by which the limit was exceeded -- computed as an ordinary node in
   the graph.

   The side effects stay out of the node. Phase 4 attaches them via an
   Incremental.Observer: alerts first, then a kill-switch behind an explicit
   config flag. Firing effects from inside a node body would make recomputation
   observable, which breaks the model -- Incremental is free to recompute a node
   whenever it likes, so a node that sends a Slack message could send several.

   Per the README: the kill-switch must not be wired to anything that places
   real orders without an explicit instruction to do so.

   Everything in this module is a pure function of its arguments. Nothing here
   references Incremental; graph.ml wires these into nodes. *)

open Core
open Types

(* The threshold as a bare float, in whatever unit the kind implies.

   Deliberately not exposed as a Notional.t: Max_drawdown's threshold is a
   fraction, not money, and handing back a Notional.t for it would be exactly
   the unit confusion Types.ml exists to prevent. Callers pair this with
   [unit_of] or with [render_value], which knows the kind. *)
let threshold (kind : Limit.kind) : float =
  match kind with
  | Limit.Gross_notional n -> Notional.to_float n
  | Limit.Value_at_risk n -> Notional.to_float n
  | Limit.Component_var n -> Notional.to_float n
  | Limit.Greek_limit (_, n) -> Notional.to_float n
  | Limit.Max_drawdown f -> f

type measure = Money | Fraction

let unit_of (kind : Limit.kind) : measure =
  match kind with
  | Limit.Gross_notional _ | Limit.Value_at_risk _ | Limit.Component_var _
  | Limit.Greek_limit _ ->
      Money
  | Limit.Max_drawdown _ -> Fraction

let render_value (kind : Limit.kind) (v : float) : string =
  match unit_of kind with
  | Money -> Printf.sprintf "$%.2f" v
  | Fraction -> Printf.sprintf "%.2f%%" (v *. 100.0)

(* Which scopes each kind is meaningful over.

   Gross notional is well defined at every level -- one instrument, one sector,
   or the whole book -- because notional exposure is additive.

   VaR and drawdown are not. Both are portfolio-level statistics: a quantile of
   a correlated return series, and a path property of an equity curve. Neither
   decomposes. Two names each carrying $10,000 of standalone VaR do not carry
   $20,000 together unless they are perfectly correlated, and evaluating a
   sector VaR limit against a standalone sector quantile would over-count the
   book's risk by exactly the diversification between its parts. So those
   pairings stay rejected.

   Component VaR is the one that does decompose, and it is here because it
   decomposes EXACTLY: the Euler shares in attribution.ml sum to the portfolio
   number with no residual, so a limit on one instrument's share is a limit on
   a real fraction of a real total. This is the correct answer to the question
   "why can I not put a VaR limit on one name" -- you can, but it has to be the
   share, not a standalone quantile, and the two are different numbers.

   At Portfolio scope a component limit measures the whole book, which by the
   Euler identity is the PARAMETRIC VaR -- deliberately a different estimator
   from [Value_at_risk], which is the historical one. Writing both is not
   redundant: the two disagreeing is the tail-fatness diagnostic the README
   describes, and having a limit on each says which estimator you are willing
   to be wrong about. *)
(* GREEK LIMITS ARE VALID AT EVERY SCOPE, and the argument is worth writing out
   because it is the same shape as the Component_var one and reaches the same
   conclusion for a different reason.

   Gamma and vega are SUMS OF DERIVATIVES, not quantiles. Each position's gamma
   is a partial derivative of its own price with respect to its own underlying,
   and the book's gamma is the derivative of a sum, which is the sum of the
   derivatives -- exactly and by linearity, with no correlation term and no
   diversification benefit to double-count. That is what fails for
   [Value_at_risk]: two names each with $10,000 of standalone VaR do not carry
   $20,000 together, because a quantile of a sum is not the sum of quantiles.
   Derivatives have no such problem. So a sector gamma limit is a limit on a
   real sector total in a way a sector VaR limit would not be.

   ONE HONEST CAVEAT, which the arithmetic hides. Adding vega across contracts
   at DIFFERENT EXPIRIES adds sensitivities to different volatilities: the
   30-day implied and the 180-day implied are distinct quantities that move
   together but not identically, so a single portfolio vega treats the whole
   term structure as one number that shifts in parallel. Every desk does this
   and calls it a parallel-shift vega; it is a bucketed approximation, not an
   identity, and it understates the risk of a calendar spread -- a position that
   is long one expiry and short another nets to near zero vega here while
   carrying real exposure to the term structure twisting.

   That is a limitation of the MEASURE and not of the scope rule, which is why
   the pairing is still accepted rather than restricted to a single expiry.
   Bucketing vega by expiry is the fix, it is a larger change than this phase,
   and the README names it rather than leaving a reader to discover it. *)
let scope_is_valid (kind : Limit.kind) (scope : Limit.scope) : bool =
  match (kind, scope) with
  | (Limit.Gross_notional _ | Limit.Component_var _ | Limit.Greek_limit _), _ -> true
  | (Limit.Value_at_risk _ | Limit.Max_drawdown _), Limit.Portfolio -> true
  | (Limit.Value_at_risk _ | Limit.Max_drawdown _), (Limit.Instrument _ | Limit.Sector _)
    ->
      false

(* Validate a limit set against the book it will be applied to.

   Called once, at graph construction. The payoff is that every failure mode
   below becomes impossible later: graph.ml can look up the node for a limit's
   scope with [find_exn] and a breach node body can never raise. A node that
   raises during stabilization poisons the whole graph, so the invariant is
   worth buying at construction time.

   Raises [Invalid_argument] with the offending limit named. *)
let validate ~(instruments : Instrument.t list) (limits : Limit.t list) : unit =
  let known_symbols = Symbol.Set.of_list (List.map instruments ~f:Instrument.symbol) in
  let known_sectors = Sector.Set.of_list (List.map instruments ~f:Instrument.sector) in
  (* Duplicate names are rejected because the name is the limit's identity
     everywhere downstream: it is the graph node's label, the alert's subject,
     and the dashboard's row key. Two limits sharing one would make a breach
     ambiguous at exactly the moment someone is reading it in a hurry. *)
  (match
     List.find_a_dup limits ~compare:(fun a b ->
         String.compare (Limit.name a) (Limit.name b))
   with
  | Some dup -> invalid_argf "limits: duplicate limit name %S" (Limit.name dup) ()
  | None -> ());
  List.iter limits ~f:(fun limit ->
      let name = Limit.name limit in
      let kind = Limit.kind limit in
      let scope = Limit.scope limit in
      if String.is_empty name then invalid_arg "limits: limit name must be non-empty";
      if not (scope_is_valid kind scope) then
        invalid_argf
          "limits: limit %S is scoped to %s, but that kind of limit is only meaningful \
           over the whole portfolio"
          name (Limit.scope_to_string scope) ();
      (match scope with
      | Limit.Instrument symbol ->
          if not (Set.mem known_symbols symbol) then
            invalid_argf "limits: limit %S references unknown instrument %S" name
              (Symbol.to_string symbol) ()
      | Limit.Sector sector ->
          if not (Set.mem known_sectors sector) then
            invalid_argf "limits: limit %S references unknown sector %S" name
              (Sector.to_string sector) ()
      | Limit.Portfolio -> ());
      match kind with
      | Limit.Greek_limit (_, n) ->
          if Float.is_negative (Notional.to_float n) then
            invalid_argf
              "limits: %S has a negative threshold (%f). A Greek limit caps a MAGNITUDE \
               -- a short option book's gamma is negative and its risk is the size of \
               that number, not its sign."
              (Limit.name limit) (Notional.to_float n) ()
      | Limit.Gross_notional n | Limit.Value_at_risk n | Limit.Component_var n ->
          if Float.is_negative (Notional.to_float n) then
            invalid_argf
              "limits: limit %S has a negative threshold (%f); a notional cap is an \
               absolute magnitude"
              name (Notional.to_float n) ()
      | Limit.Max_drawdown f ->
          if not (Float.( > ) f 0.0 && Float.( <= ) f 1.0) then
            invalid_argf
              "limits: limit %S has drawdown threshold %f; must be a fraction in (0, 1]"
              name f ())

(* Evaluate one limit against one observed value.

   [observed] must already be in the limit's own unit -- dollars for
   Gross_notional and Value_at_risk, a fraction for Max_drawdown. This function
   cannot check that, which is why graph.ml wires each limit kind to a
   specifically-chosen node rather than to a generic "current value". *)
let evaluate ~(limit : Limit.t) ~(observed : float) : Breach.t =
  Breach.create ~limit ~observed ~threshold:(threshold (Limit.kind limit))

(* One line, readable in a terminal or a log.

   Non-breaches report headroom rather than nothing: "how close am I" is the
   question a risk limit is actually asked, and a display that only speaks up
   at the moment of breach gives no warning. *)
let to_string (breach : Breach.t) : string =
  let limit = Breach.limit breach in
  let kind = Limit.kind limit in
  let render = render_value kind in
  let prefix =
    Printf.sprintf "%s [%s]" (Limit.name limit)
      (Limit.scope_to_string (Limit.scope limit))
  in
  if Breach.breached breach then
    Printf.sprintf "%s BREACH: %s > %s (over by %s)" prefix
      (render (Breach.observed breach))
      (render (Breach.threshold breach))
      (render (Breach.excess breach))
  else
    Printf.sprintf "%s ok: %s <= %s (headroom %s)" prefix
      (render (Breach.observed breach))
      (render (Breach.threshold breach))
      (render (-.Breach.excess breach))

(* Utilisation as a fraction of the limit: 0.8 means "80% of the way to the
   line". Above 1.0 is a breach.

   Kept separate from [Breach.excess] because excess is in the limit's units and
   therefore not comparable across limits, while utilisation is dimensionless --
   it is what lets a dashboard sort every limit in the book by how hot it is.

   A zero threshold means the limit forbids the exposure outright; there is no
   meaningful percentage of zero, so anything non-zero reports infinity, which
   sorts to the top exactly as it should. *)
let utilisation (breach : Breach.t) : float =
  let threshold = Breach.threshold breach in
  let observed = Breach.observed breach in
  if Float.equal threshold 0.0 then
    if Float.equal observed 0.0 then 0.0 else Float.infinity
  else observed /. threshold
