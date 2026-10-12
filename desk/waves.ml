(* An intraday rebalance's waves. Pure: which orders go together, and in what
   order, decided from the legs and the book's positions alone; the order
   manager gates and sends each wave in turn (Oms.propose_rebalance). See the
   interface for the rule. *)

open Core

let split ~(legs : Rebalance.Leg.t list) ~(position : Ohcamel.Types.Symbol.t -> float)
    ~(max_legs : int) : Rebalance.Leg.t list list =
  let shrinking, growing =
    List.partition_tf legs ~f:(fun (l : Rebalance.Leg.t) ->
        not
          (Rebalance.grows
             ~position:(position l.Rebalance.Leg.symbol)
             ~side:l.Rebalance.Leg.side ~qty:l.Rebalance.Leg.qty))
  in
  let notional (l : Rebalance.Leg.t) =
    Float.abs (Float.of_int l.Rebalance.Leg.qty *. l.Rebalance.Leg.price)
  in
  let waves legs =
    List.stable_sort legs ~compare:(fun a b -> Float.compare (notional b) (notional a))
    |> List.chunks_of ~length:(Int.max 1 max_legs)
  in
  waves shrinking @ waves growing
