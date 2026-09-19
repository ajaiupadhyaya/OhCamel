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
   fork cannot evaluate -- a VaR still warming up -- is reported as
   unevaluable and does not fail the proposal: unknown is not a breach, and it
   is not a pass either, which is why it is reported rather than folded into
   one.

   THE BASE. A caller may pass [base], fills applied to the fork before
   anything is read: the book as it would stand if resting orders filled.
   "Before" is then that book, not the live one, and created, worsened and
   reduced are all judged relative to it -- so a breach the base alone makes
   is not charged to a proposal that leaves it where it is or brings it
   back, and a proposal that takes it further over fails as worsened. With no base, "before" is the live graph itself, read
   exactly as it was before the base existed. The base is still a
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

module Verdict = struct
  type t = {
    passed : bool;
    created : Move.t list;
    worsened : Move.t list;
    cleared : Move.t list;
    unevaluable : Move.t list;
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

  let reasons t =
    List.map t.created ~f:(describe "breached")
    @ List.map t.worsened ~f:(describe "further over its line")
end

let breached = function Some b -> Breach.breached b | None -> false

let apply (graph : Graph.t) (fills : Fill.t list) =
  List.iter fills ~f:(fun (f : Fill.t) ->
      Graph.apply_fill graph
        { Types.Fill.symbol = f.symbol; qty = f.qty; price = f.price; time = Time.epoch })

(* [base] is optional and comes first so that it is erased by the graph
   argument: every call without one reads exactly as it did before it
   existed. *)
let check ?(base : Fill.t list = []) (graph : Graph.t) ~(fills : Fill.t list) : Verdict.t
    =
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
      let cleared =
        List.filter moves ~f:(fun m ->
            breached m.before
            && match m.after with Some a -> not (Breach.breached a) | None -> false)
      in
      let unevaluable = List.filter moves ~f:(fun m -> Option.is_none m.after) in
      {
        Verdict.passed = List.is_empty created && List.is_empty worsened;
        created;
        worsened;
        cleared;
        unevaluable;
        gross_before = before.Graph.Snapshot.gross_exposure;
        gross_after = after.Graph.Snapshot.gross_exposure;
        equity_before = before.Graph.Snapshot.equity;
        equity_after = after.Graph.Snapshot.equity;
      })
