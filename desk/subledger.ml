(* The strategies' positions against the account's, as one sum per name.
   subledger.mli says what each value means; this is the arithmetic. *)

open Core
open Ohcamel.Types

type break = { symbol : Symbol.t; strategies : float; account : float }
[@@deriving sexp_of, compare, equal]

let hand = "hand"

(* signal:<slug>:<sequence>, the source a strategy's rebalance carries
   (Rebalance.Source); a slug holds no colon. Anything else -- "ticket",
   "manual", a source with no slug -- is the hand's. *)
let owner ~source =
  match String.split source ~on:':' with
  | "signal" :: slug :: _ :: _ when not (String.is_empty slug) -> slug
  | _ -> hand

let tolerance = 0.5

let check ~(own : (string * Symbol.t * float) list) ~(account : (Symbol.t * float) list)
    ~(tolerance : float) : break list =
  let sum entries =
    List.fold entries ~init:Symbol.Map.empty ~f:(fun acc (symbol, q) ->
        Map.update acc symbol ~f:(fun x -> Option.value x ~default:0.0 +. q))
  in
  let strategies = sum (List.map own ~f:(fun (_, symbol, q) -> (symbol, q))) in
  let account = sum account in
  Map.merge strategies account ~f:(fun ~key:symbol -> function
    | `Left s -> Some (symbol, s, 0.0)
    | `Right a -> Some (symbol, 0.0, a)
    | `Both (s, a) -> Some (symbol, s, a))
  |> Map.data
  |> List.filter_map ~f:(fun (symbol, strategies, account) ->
      let diff = strategies -. account in
      (* Written as the one comparison that holds, so a NaN on either side --
         for which it is false -- is a break, never a sum. *)
      if Float.is_finite diff && Float.( <= ) (Float.abs diff) tolerance then None
      else Some { symbol; strategies; account })

let describe b =
  sprintf
    "%s: the strategies' own positions and the desk's hand tickets sum to %.17g, and the \
     account holds %.17g"
    (Symbol.to_string b.symbol) b.strategies b.account

let settle =
  "To settle it, reverse the trade made outside the desk at the venue -- a holding that \
   predates the journal is closed at the venue -- since a desk ticket's fill moves the \
   hand's sub-ledger and the account alike and never clears a break."

let reduction_refusal b ~(account : float) ~(side : Order.Side.t) ~(qty : int) =
  sprintf
    "the sub-ledger breaks in %s; a reduction in %s goes only if it also shrinks the \
     account's position (%.17g, net of the desk's resting reductions) by no more than it \
     holds, and a %s of %d does not: it could take the account through zero to a \
     position nobody holds. %s"
    (describe b) (Symbol.to_string b.symbol) account (Order.Side.to_string side) qty
    settle

let refuses (breaks : break list) ~(symbol : Symbol.t) ~(increases : bool) : string option
    =
  if not increases then None
  else
    List.find breaks ~f:(fun b -> Symbol.equal b.symbol symbol)
    |> Option.map ~f:(fun b ->
        sprintf
          "the sub-ledger breaks in %s; nothing that grows a strategy's position in %s \
           is sent until a person settles it (a reduction still goes)"
          (describe b) (Symbol.to_string symbol))
