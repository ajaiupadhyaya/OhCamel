(* What an execution cost, in basis points (design §3.9; Perold 1988).

   Shortfall is measured from the DECISION price -- the graph's mark when the
   order was proposed -- because that is the price the desk decided at, and the
   only one it can hold itself to. The venue's quote at submission splits it:
   the market's move between deciding and arriving (delay) and what the fill
   paid against the quote it arrived to (slippage). The two add to the
   shortfall to first order, and both are reported rather than one derived
   from the other.

   Positive is cost for both sides, by multiplying by the side's sign once.

   On the paper account every number here measures Alpaca's fill simulator,
   and the IEX quote is one venue's, not the national best: the page says so
   wherever these appear. *)

open Core
open Ohcamel.Types

module Inputs = struct
  type t = {
    symbol : Symbol.t;
    side : Order.Side.t;
    qty : float;
    decision : float;
    bid : float option;
    ask : float option;
    fill : float;
  }
  [@@deriving sexp_of]
end

module Costs = struct
  type t = {
    shortfall_bps : float;
    delay_bps : float option;
    slippage_bps : float option;
    half_spread_bps : float option;
    versus_model_bps : float option;
  }
  [@@deriving sexp_of]
end

let bp = 10_000.0

let of_fill ~(model_half_spread_bps : float) (i : Inputs.t) : Costs.t =
  let s = Order.Side.sign i.side in
  let shortfall_bps = s *. (i.fill -. i.decision) /. i.decision *. bp in
  match (i.bid, i.ask) with
  | Some b, Some a when Float.( > ) b 0.0 && Float.( > ) a b ->
      let mid = (a +. b) /. 2.0 in
      let slippage = s *. (i.fill -. mid) /. mid *. bp in
      {
        Costs.shortfall_bps;
        delay_bps = Some (s *. (mid -. i.decision) /. i.decision *. bp);
        slippage_bps = Some slippage;
        half_spread_bps = Some ((a -. b) /. 2.0 /. mid *. bp);
        versus_model_bps = Some (slippage -. model_half_spread_bps);
      }
  | _ ->
      {
        Costs.shortfall_bps;
        delay_bps = None;
        slippage_bps = None;
        half_spread_bps = None;
        versus_model_bps = None;
      }

module Summary = struct
  type t = {
    count : int;
    mean_shortfall_bps : float option;
    median_shortfall_bps : float option;
    weighted_shortfall_bps : float option;
    mean_versus_model_bps : float option;
  }
  [@@deriving sexp_of]
end

let mean xs =
  if List.is_empty xs then None
  else Some (List.sum (module Float) xs ~f:Fn.id /. Float.of_int (List.length xs))

let median xs =
  match List.sort xs ~compare:Float.compare with
  | [] -> None
  | sorted ->
      let n = List.length sorted in
      if n % 2 = 1 then Some (List.nth_exn sorted (n / 2))
      else Some ((List.nth_exn sorted ((n / 2) - 1) +. List.nth_exn sorted (n / 2)) /. 2.0)

let summarize (rows : (Inputs.t * Costs.t) list) : Summary.t =
  let shortfalls = List.map rows ~f:(fun (_, c) -> c.Costs.shortfall_bps) in
  let qty = List.sum (module Float) rows ~f:(fun (i, _) -> i.Inputs.qty) in
  {
    Summary.count = List.length rows;
    mean_shortfall_bps = mean shortfalls;
    median_shortfall_bps = median shortfalls;
    weighted_shortfall_bps =
      (if Float.( > ) qty 0.0 then
         Some
           (List.sum
              (module Float)
              rows
              ~f:(fun (i, c) -> i.Inputs.qty *. c.Costs.shortfall_bps)
           /. qty)
       else None);
    mean_versus_model_bps =
      mean (List.filter_map rows ~f:(fun (_, c) -> c.Costs.versus_model_bps));
  }

let by_symbol (rows : (Inputs.t * Costs.t) list) : (Symbol.t * Summary.t) list =
  List.sort_and_group rows ~compare:(fun (a, _) (b, _) ->
      Symbol.compare a.Inputs.symbol b.Inputs.symbol)
  |> List.map ~f:(fun group -> ((fst (List.hd_exn group)).Inputs.symbol, summarize group))
