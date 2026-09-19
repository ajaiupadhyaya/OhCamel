(* Liquidity: how long a position takes to unwind, what unwinding it costs in
   market impact, and the liquidity-adjusted VaR the book is marked against.

   Pure, like risk_metrics.ml and vol_estimators.ml: nothing here references
   Incremental, nothing here is stateful, and every function is a plain map
   from a snapshot of positions to numbers -- callable from a graph node, from
   the backtester, or from a test, with no engine behind it. Task 6 wires this
   into the graph; this task is the kernel it wires.

   THREE FIGURES

   days_to_liquidate      how long, at a fixed participation rate of average
                          daily volume, unwinding this position would take.
   impact_fraction/cost   the price concession that trading pays for
                          immediacy: Toth et al. (2011)'s square-root law.
   LVaR                   Bangia et al. (1999)'s liquidity-adjusted VaR: the
                          book's own VaR, plus the cost of crossing the spread
                          on every position, marked to close, today.

   DAYS TO LIQUIDATE

   At participation rate p of average daily volume ADV, unwinding |q| shares
   takes

       days = |q| / (p * ADV)

   IMPACT, TOTH ET AL. (2011)

   The fractional price concession from trading a quantity q against a name's
   average daily volume ADV, where sigma is that name's daily return
   volatility, is

       impact_fraction = Y * sigma * sqrt(|q| / ADV)

   with Y an empirical constant near 1 across the markets the paper studied --
   this book's own [impact_coefficient]. The concession is scored against the
   position's full dollar exposure, because it is a statement about the whole
   position's liquidation cost rather than about one day's slice of it:

       impact_cost = impact_fraction * |q * price|

   LVAR, BANGIA ET AL. (1999)

   Bangia's spread-adjusted VaR is, in general,

       LVaR = VaR + 0.5 * Sum_S (mean_spread_S + z * spread_stddev_S) * |x_S|

   This book has no spread history to estimate a spread_stddev from -- ruling
   6 of the A4 constraints says so in as many words -- so that term is stated
   as exactly zero rather than guessed at, which collapses the formula to

       LVaR = VaR + Sum_S |x_S| * half_spread_S

   with half_spread_S already the half of the quoted spread (the book's
   [half_spread_bps] on each position, 5 bps by default -- config.ml's
   [spread_bps]/[spread_bps_default]). [lvar] below takes the book's VaR as a
   parameter and does not compute one itself: VaR lives in risk_metrics.ml and
   graph.ml, and the entire point of this module staying pure is that it does
   not need either.

   UNKNOWN IS NOT ZERO

   A position with no ADV, or no daily-volatility estimate, cannot have its
   days-to-liquidate or its impact computed -- not "computed as zero", which is
   what falling back to 0.0 would silently claim. Its line reports [None] for
   all three of days_to_liquidate, impact_fraction and impact_cost, and because
   an unknown is not a zero a sum can absorb invisibly, any such line also
   makes the BOOK-LEVEL totals [None]: a desk that cannot price the impact of
   unwinding one name cannot claim to know the book's total impact cost either.
   [spread_cost] is exempt from this, on every line and on the total -- it
   needs only price, qty and a half-spread, and the book always has all three,
   so it is a plain float rather than an option anywhere in this module.

   A position with zero quantity is the opposite edge: there is nothing to
   unwind, so its days, impact fraction and impact cost are [Some 0.0] even
   when ADV or sigma is missing for that name. Zero shares of an untraded,
   unmarked name is a known fact, not an unknown one. *)

open Core
open Types

type position = {
  symbol : Symbol.t;
  qty : float;
  price : float;
  adv20 : float option;
  daily_stddev : float option;
  half_spread_bps : float;
}

module Line = struct
  type t = {
    symbol : Symbol.t;
    days_to_liquidate : float option;
    impact_fraction : float option;
    impact_cost : float option;
    spread_cost : float;
  }
  [@@deriving sexp_of]
end

type t = {
  lines : Line.t list;
  spread_cost : float;
  impact_cost : float option;
  max_days_to_liquidate : float option;
}
[@@deriving sexp_of]

let validate_participation ~participation =
  if
    not
      (Float.is_finite participation && Float.( > ) participation 0.0
      && Float.( <= ) participation 1.0)
  then invalid_argf "liquidity: participation must lie in (0, 1], got %f" participation ()

let validate_impact_coefficient ~impact_coefficient =
  if not (Float.is_finite impact_coefficient && Float.( >= ) impact_coefficient 0.0) then
    invalid_argf "liquidity: impact_coefficient must be finite and >= 0, got %f"
      impact_coefficient ()

(* One position's line.

   [x] is the signed dollar exposure qty * price. Every cost below is scored
   on a magnitude -- |x| or |qty| -- rather than on the signed value, because
   the cost of unwinding a position does not depend on which side of it the
   book is on: a short position of a given size costs exactly as much to
   cover as a long position of the same size costs to sell. That is the
   property the short-position test in test_liquidity.ml pins. *)
let line ~participation ~impact_coefficient (p : position) : Line.t =
  let x = p.qty *. p.price in
  let spread_cost = Float.abs x *. p.half_spread_bps *. 1e-4 in
  let days_to_liquidate, impact_fraction, impact_cost =
    if Float.equal p.qty 0.0 then (Some 0.0, Some 0.0, Some 0.0)
    else
      (* Present is not enough: an ADV of 0 or below, a sigma that is not a
         finite non-negative number, or a price that is not a finite positive
         number would turn a division or a square root into infinity or nan,
         and an infinity is not a liquidation horizon. Each is an unknown, and
         unknown is not zero -- so it takes the None branch, and poisons the
         totals exactly as a missing ADV does. *)
      match (p.adv20, p.daily_stddev) with
      | Some adv, Some sigma
        when Float.is_finite adv && Float.( > ) adv 0.0 && Float.is_finite sigma
             && Float.( >= ) sigma 0.0 && Float.is_finite p.price
             && Float.( > ) p.price 0.0 ->
          let aq = Float.abs p.qty in
          let days = aq /. (participation *. adv) in
          let fraction = impact_coefficient *. sigma *. Float.sqrt (aq /. adv) in
          let cost = fraction *. Float.abs x in
          (Some days, Some fraction, Some cost)
      | _ -> (None, None, None)
  in
  { Line.symbol = p.symbol; days_to_liquidate; impact_fraction; impact_cost; spread_cost }

(* Folds one line's optional figure into a running optional total: [None]
   once, [None] forever after, on the reasoning above -- an unknown poisons
   the sum it would otherwise have joined, permanently, because a total that
   silently dropped the unknown line would be reporting a partial sum as a
   complete one. *)
let fold_option lines ~f ~combine =
  List.fold lines ~init:(Some 0.0) ~f:(fun acc l ->
      match (acc, f l) with Some acc, Some v -> Some (combine acc v) | _ -> None)

let compute ~participation ~impact_coefficient (positions : position list) : t =
  validate_participation ~participation;
  validate_impact_coefficient ~impact_coefficient;
  let lines = List.map positions ~f:(line ~participation ~impact_coefficient) in
  let spread_cost =
    List.fold lines ~init:0.0 ~f:(fun acc l -> acc +. l.Line.spread_cost)
  in
  let impact_cost = fold_option lines ~f:(fun l -> l.Line.impact_cost) ~combine:( +. ) in
  let max_days_to_liquidate =
    fold_option lines ~f:(fun l -> l.Line.days_to_liquidate) ~combine:Float.max
  in
  { lines; spread_cost; impact_cost; max_days_to_liquidate }

(* Bangia with the spread-volatility term at zero: LVaR is the book's VaR plus
   the total half-spread cost computed above, and nothing else. *)
let lvar ~var (t : t) = var +. t.spread_cost
