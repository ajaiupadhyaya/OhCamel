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

(* SHORTFALL AGAINST THE SESSION'S OWN BENCHMARKS (Task 4.9). The decision-price
   shortfall above is the cost the desk can hold itself to; the open, the close
   and the session's VWAP are what an order is customarily compared with, and
   a fill is read against all three, signed as everywhere here: positive is
   cost. A buy at 50.10 against an open of 50.00 paid 20 bp; against a close of
   50.20 it saved 19.92 bp (-0.10 / 50.20 x 10,000), and against a VWAP of
   50.05 it paid 9.99 bp.

   Each is a venue figure and an unknown stays unknown: a session with no bar
   has no open, close or VWAP, and a VWAP the venue's bar did not carry is
   [None] with the reason in [vwap_reason] -- never one made up from the bar's
   high, low and close, which is a typical price and not a volume-weighted one.
   The venue's bars are daily and unadjusted (Venue.Read.session_bars); on
   Alpaca's IEX feed they are IEX's session, one venue's, not the
   consolidated tape. *)
module Benchmark = struct
  type t = {
    open_ : float option;
    close : float option;
    vwap : float option;
    vwap_reason : string option;
  }
  [@@deriving sexp_of]

  let none reason = { open_ = None; close = None; vwap = None; vwap_reason = Some reason }
end

module Bench_costs = struct
  type t = {
    vs_open : float option;
    vs_close : float option;
    vs_vwap : float option;
    vwap_reason : string option;
  }
  [@@deriving sexp_of]
end

let usable x = Float.is_finite x && Float.( > ) x 0.0

let benchmarks (i : Inputs.t) (b : Benchmark.t) : Bench_costs.t =
  let s = Order.Side.sign i.side in
  let against = function
    | Some p when usable p -> Some (s *. (i.fill -. p) /. p *. bp)
    | _ -> None
  in
  let vs_vwap = against b.vwap in
  {
    Bench_costs.vs_open = against b.open_;
    vs_close = against b.close;
    vs_vwap;
    vwap_reason =
      (match (vs_vwap, b.vwap, b.vwap_reason) with
      | Some _, _, _ -> None
      | None, _, Some r -> Some r
      | None, Some _, None ->
          Some "the venue's bar carried a volume-weighted price that is not positive"
      | None, None, None -> Some "the venue's bar carried no volume-weighted price");
  }

module Bench_summary = struct
  type t = {
    count : int;
    open_n : int;
    close_n : int;
    vwap_n : int;
    mean_vs_open_bps : float option;
    mean_vs_close_bps : float option;
    mean_vs_vwap_bps : float option;
  }
  [@@deriving sexp_of]
end

(* A roll-up is counts and basis points only: no quantity, notional or price, so
   it can be public. A mean is over the fills that HAVE the benchmark, and the
   count beside it says how many that is. *)
let summarize_benchmarks (rows : Bench_costs.t list) : Bench_summary.t =
  let pick f = List.filter_map rows ~f in
  let o = pick (fun c -> c.Bench_costs.vs_open)
  and c = pick (fun c -> c.Bench_costs.vs_close)
  and v = pick (fun c -> c.Bench_costs.vs_vwap) in
  {
    Bench_summary.count = List.length rows;
    open_n = List.length o;
    close_n = List.length c;
    vwap_n = List.length v;
    mean_vs_open_bps = mean o;
    mean_vs_close_bps = mean c;
    mean_vs_vwap_bps = mean v;
  }

let by_strategy (rows : (string * Bench_costs.t) list) : (string * Bench_summary.t) list =
  List.sort_and_group rows ~compare:(fun (a, _) (b, _) -> String.compare a b)
  |> List.map ~f:(fun group ->
      (fst (List.hd_exn group), summarize_benchmarks (List.map group ~f:snd)))
