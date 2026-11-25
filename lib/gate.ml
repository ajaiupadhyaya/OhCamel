(* The pre-trade gate: what a proposed set of fills would do to the book's
   limits, answered by the engine itself.

   Design §3.7 and invariant 12: every order passes here before it exists at a
   venue. The answer comes from a fork of the live graph with the fills
   applied, as stress.ml's scenarios do, so there is no second implementation
   of exposure, equity or any limit rule to drift from the live one (invariant
   2). It lives in the risk kernel rather than the desk because it is a
   read-only calculation on a fork -- the category invariant 6 always
   permitted -- and it names nothing that trades.

   CREATED OR WORSENED FAILS; REDUCED PASSES. A proposal that takes a clear
   limit over its line fails, naming it. One that leaves a breached limit
   further over its line fails too. One that brings a breached limit closer to
   its line passes, still breached: a desk must be able to trade out of a
   breach, and a gate that refused every trade on a breached book would hold
   the book exactly where it should not stay.

   Excess is compared within one limit, in that limit's own unit, so "further
   over" needs no conversion; [epsilon] keeps a difference of float rounding
   in a limit the fills did not touch from reading as a worsening. A limit the
   fork cannot evaluate -- a VaR still warming up, a drawdown that cannot be
   measured -- is reported as unevaluable: unknown is not a breach, and it is
   not a pass either, which is why it is reported rather than folded into
   one.

   UNKNOWN IS NOT PERMISSION (Task 1.5). A limit the caller names in
   [trips_on] -- the book's kill_switch_trips_on, the limits hard enough to
   stop the desk -- that the fork cannot evaluate after the fills refuses a
   proposal that RAISES GROSS NOTIONAL: [Refused_unevaluable], naming every
   such limit in the order the book configures its limits. "Raises" is the
   portfolio's gross exposure after the fills against before them -- the
   snapshot's [gross_exposure], the quantity a Portfolio Gross_notional limit
   reads, and "before" is the base's book when there is one -- strictly
   greater, with no epsilon: equal gross is not a raise, and a swap that
   lowers one name and raises another by more is one. A gross either side
   that is not a number counts as a raise: it cannot be shown not to be. A
   proposal that lowers gross or leaves it equal passes as before, so a desk
   can still trade out of risk it cannot measure; an unevaluable limit the
   switch does not trip on behaves as before. A breach created or worsened
   outranks it in the outcome, and the reasons say both.

   THE BASE. A caller may pass [base], fills applied to the fork before
   anything is read: the book as it would stand if resting orders filled.
   "Before" is then that book, not the live one, and created, worsened and
   reduced are all judged relative to it -- so a breach the base alone makes
   is not charged to a proposal that leaves it where it is or brings it
   back, and a proposal that takes it further over fails as worsened. With no
   base, "before" is the live graph itself, read exactly as it was before the
   base existed. The base is still a
   hypothetical on a fork: which resting orders to assume fill, and at what
   price, is the caller's to decide, and nothing here knows an order exists. *)

open Core
open Types

let epsilon = 1e-9

module Fill = struct
  type t = { symbol : Symbol.t; qty : Qty.t; price : Price.t } [@@deriving sexp_of]
end

module Move = struct
  type t = { limit : string; before : Breach.t option; after : Breach.t option }
  [@@deriving sexp_of]
end

(* What the gate answers. Every consumer matches it whole: there is no
   wildcard that could read a new refusal as a pass. *)
module Outcome = struct
  type t =
    | Passed
    | Refused_breach  (** a limit's breach created or worsened *)
    | Refused_unevaluable of string list
        (** trip-limits that cannot be evaluated, and the proposal raises gross *)
  [@@deriving sexp_of, equal]

  let passed = function Passed -> true | Refused_breach | Refused_unevaluable _ -> false

  let word = function
    | Passed -> "passed"
    | Refused_breach -> "refused_breach"
    | Refused_unevaluable _ -> "refused_unevaluable"
end

module Verdict = struct
  type t = {
    outcome : Outcome.t;
    created : Move.t list;
    worsened : Move.t list;
    cleared : Move.t list;
    unevaluable : Move.t list;
    (* The trip-limits among [unevaluable] when the proposal raises gross,
       in book order; empty otherwise. Kept beside [outcome] so a breach
       that outranks them still leaves them in the reasons. *)
    unknown_trips : string list;
    (* Exposure limits ([is_exposure]) the fills would breach or take
       further over, which a stop's flatten is let through anyway
       ([waive_exposure]); empty for every other proposal, whose created and
       worsened hold them instead. *)
    waived : Move.t list;
    gross_before : Notional.t;
    gross_after : Notional.t;
    equity_before : Notional.t;
    equity_after : Notional.t;
  }
  [@@deriving sexp_of]

  let describe verb (m : Move.t) =
    match m.Move.after with
    | None -> sprintf "%s would be %s" m.Move.limit verb
    | Some after ->
        sprintf "%s would be %s: %s" m.Move.limit verb (Limits.to_string after)

  let passed t = Outcome.passed t.outcome

  let reasons t =
    List.map t.created ~f:(describe "breached")
    @ List.map t.worsened ~f:(describe "further over its line")
    @
    match t.unknown_trips with
    | [] -> []
    | names ->
        [
          sprintf
            "%s cannot be evaluated, and the kill switch trips on %s: a proposal that \
             raises gross notional is refused while %s unknown"
            (String.concat ~sep:", " names)
            (match names with [ _ ] -> "it" | _ -> "them")
            (match names with [ _ ] -> "it is" | _ -> "they are");
        ]
end

let breached = function Some b -> Breach.breached b | None -> false

(* The limits that cap EXPOSURE -- how much the book holds, in a name, a
   class or the whole -- as opposed to risk statistics (VaR, shortfall,
   component VaR, Greeks) and the drawdown. A stop's flatten may breach
   these and nothing else ([waive_exposure]). *)
let is_exposure (kind : Limit.kind) =
  match kind with
  | Limit.Gross_notional _ | Limit.Name_weight _ | Limit.Class_gross _ -> true
  | Limit.Value_at_risk _ | Limit.Expected_shortfall _ | Limit.Component_var _
  | Limit.Max_drawdown _ | Limit.Greek_limit _ ->
      false

let apply (graph : Graph.t) (fills : Fill.t list) =
  List.iter fills ~f:(fun (f : Fill.t) ->
      Graph.apply_fill graph
        { Types.Fill.symbol = f.symbol; qty = f.qty; price = f.price; time = Time.epoch })

(* [base] is optional and comes first so that it is erased by the graph
   argument: every call without one reads exactly as it did before it
   existed. *)
(* A STOP'S FLATTEN IS NOT HELD BY AN EXPOSURE LIMIT (Task 1.9, round 1).
   With [waive_exposure], a breach the fills create or worsen on an exposure
   limit ([is_exposure]: a Gross_notional at any scope, a Name_weight, a
   Class_gross) does not refuse them: it is reported in [waived] instead.
   Why: these limits read the ACCOUNT's position, and with two strategies on
   opposite sides of one name, the short's cover raises the account's long.
   A strategy whose stop has latched must be able to leave, whatever the
   other strategy holds; the breach its flatten leaves then refuses every
   raise as usual. Every other limit -- the risk statistics, the drawdown --
   still refuses, and so does an unevaluable trip-limit on a raise. The
   caller decides when a proposal is such a flatten (Oms.check_rebalance:
   a latched stop, every leg shrinking the strategy's own position without
   crossing zero, and the switch clear); nothing here knows a strategy. *)
let check ?(base : Fill.t list = []) ?(trips_on : string list = [])
    ?(waive_exposure = false) (graph : Graph.t) ~(fills : Fill.t list) : Verdict.t =
  (* The live snapshot first: it stabilizes, so any write the live graph had
     not settled is settled before the fork copies its cells. *)
  let live = Graph.snapshot graph in
  let fork = Graph.fork graph in
  Exn.protect
    ~finally:(fun () -> Graph.destroy fork)
    ~f:(fun () ->
      (* One fork: the base first, "before" read from it, then the fills. With
         no base, "before" is the live graph's, as it always was. *)
      let before, limits_before =
        match base with
        | [] -> (live, Graph.limit_results graph)
        | base ->
            apply fork base;
            let s = Graph.snapshot fork in
            (s, Graph.limit_results fork)
      in
      apply fork fills;
      let after = Graph.snapshot fork in
      let moves =
        List.map2_exn limits_before (Graph.limit_results fork) ~f:(fun (l, b) (_, a) ->
            { Move.limit = Limit.name l; before = b; after = a })
      in
      let created =
        List.filter moves ~f:(fun m -> breached m.after && not (breached m.before))
      in
      let worsened =
        List.filter moves ~f:(fun m ->
            match (m.before, m.after) with
            | Some b, Some a ->
                Breach.breached b && Breach.breached a
                && Float.( > ) (Breach.excess a) (Breach.excess b +. epsilon)
            | _ -> false)
      in
      let waivable (m : Move.t) =
        waive_exposure
        && List.exists limits_before ~f:(fun (l, _) ->
            String.equal (Limit.name l) m.Move.limit && is_exposure (Limit.kind l))
      in
      let waived = List.filter (created @ worsened) ~f:waivable in
      let created = List.filter created ~f:(fun m -> not (waivable m)) in
      let worsened = List.filter worsened ~f:(fun m -> not (waivable m)) in
      let cleared =
        List.filter moves ~f:(fun m ->
            breached m.before
            && match m.after with Some a -> not (Breach.breached a) | None -> false)
      in
      let unevaluable = List.filter moves ~f:(fun m -> Option.is_none m.after) in
      let gross_before = before.Graph.Snapshot.gross_exposure in
      let gross_after = after.Graph.Snapshot.gross_exposure in
      (* Written as "not (after <= before)": a gross that is not a number
         compares false, and counts as a raise. *)
      let raises_gross =
        not Float.(Notional.to_float gross_after <= Notional.to_float gross_before)
      in
      let unknown_trips =
        if not raises_gross then []
        else
          List.filter_map unevaluable ~f:(fun m ->
              Option.some_if
                (List.mem trips_on m.Move.limit ~equal:String.equal)
                m.Move.limit)
      in
      let outcome =
        if not (List.is_empty created && List.is_empty worsened) then
          Outcome.Refused_breach
        else
          match unknown_trips with
          | [] -> Outcome.Passed
          | names -> Outcome.Refused_unevaluable names
      in
      {
        Verdict.outcome;
        created;
        worsened;
        cleared;
        unevaluable;
        unknown_trips;
        waived;
        gross_before;
        gross_after;
        equity_before = before.Graph.Snapshot.equity;
        equity_after = after.Graph.Snapshot.equity;
      })
