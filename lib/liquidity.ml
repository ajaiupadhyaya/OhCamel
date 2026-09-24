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

   [spread_cost] needs only price, qty and a half-spread, and config.ml's
   [Desk_spec.validate] already refuses a book whose [spread_bps_default] or
   [spread_bps] is negative or not finite -- so in the ordinary path every
   position that reaches this module carries a good [half_spread_bps]. [line]
   below still checks [Float.is_finite half_spread_bps] itself, as a second,
   independent guard: a node body in graph.ml may not raise (only [Error] or
   [None] read as "the engine kept running"), so the one caller that cannot
   simply propagate an [Or_error] needs this module to hand back an option
   instead of a nan. Because that guard exists, [spread_cost] is no longer a
   plain float anywhere in this module: it is [None] on the one line whose
   half-spread failed the check, and [None] is what [line] returns for that
   position's whole record, on the same reasoning as the ADV/sigma case above
   -- a corrupt half-spread is not evidence the rest of the row's inputs are
   trustworthy either, so the simplest honest answer is "this line is
   unknown," not "every field except spread_cost is known." [compute] still
   emits one [Line.t] per position, so the position is never silently dropped
   from the book -- the unknown line simply carries [None] in all four fields
   -- and it poisons the book-level totals exactly as a missing ADV does,
   [spread_cost]'s total included.

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
    (* [None] exactly when the position's [half_spread_bps] failed
       [Float.is_finite] -- see the module header. *)
    spread_cost : float option;
  }
  [@@deriving sexp_of]
end

type t = {
  lines : Line.t list;
  spread_cost : float option;
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

(* One position's line, or [None] when [half_spread_bps] is not a finite
   number. That guard runs before anything else: a nan or infinite spread
   would otherwise reach [spread_cost] untouched (unlike ADV or sigma, it is
   used directly with no division or square root to turn it into a visible
   nan), and the second-guard note in the module header explains why this
   module checks it again even though config.ml already refuses such a book
   at load time.

   [x] is the signed dollar exposure qty * price. Every cost below is scored
   on a magnitude -- |x| or |qty| -- rather than on the signed value, because
   the cost of unwinding a position does not depend on which side of it the
   book is on: a short position of a given size costs exactly as much to
   cover as a long position of the same size costs to sell. That is the
   property the short-position test in test_liquidity.ml pins. *)
let line ~participation ~impact_coefficient (p : position) : Line.t option =
  if not (Float.is_finite p.half_spread_bps) then None
  else
    let x = p.qty *. p.price in
    let spread_cost = Some (Float.abs x *. p.half_spread_bps *. 1e-4) in
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
    Some
      {
        Line.symbol = p.symbol;
        days_to_liquidate;
        impact_fraction;
        impact_cost;
        spread_cost;
      }

(* Folds one line's optional figure into a running optional total: [None]
   once, [None] forever after, on the reasoning above -- an unknown poisons
   the sum it would otherwise have joined, permanently, because a total that
   silently dropped the unknown line would be reporting a partial sum as a
   complete one. *)
let fold_option lines ~f ~combine =
  List.fold lines ~init:(Some 0.0) ~f:(fun acc l ->
      match (acc, f l) with Some acc, Some v -> Some (combine acc v) | _ -> None)

(* The line a position with a non-finite half-spread gets: present, so the
   position is never silently missing from the book, but unknown in every
   figure -- the same shape [line] itself would report for an ADV or a sigma
   it could not use, extended to spread_cost now that spread_cost too can
   fail its own guard. *)
let unknown_line (p : position) : Line.t =
  {
    Line.symbol = p.symbol;
    days_to_liquidate = None;
    impact_fraction = None;
    impact_cost = None;
    spread_cost = None;
  }

let compute ~participation ~impact_coefficient (positions : position list) : t =
  validate_participation ~participation;
  validate_impact_coefficient ~impact_coefficient;
  let lines =
    List.map positions ~f:(fun p ->
        match line ~participation ~impact_coefficient p with
        | Some l -> l
        | None -> unknown_line p)
  in
  let spread_cost = fold_option lines ~f:(fun l -> l.Line.spread_cost) ~combine:( +. ) in
  let impact_cost = fold_option lines ~f:(fun l -> l.Line.impact_cost) ~combine:( +. ) in
  let max_days_to_liquidate =
    fold_option lines ~f:(fun l -> l.Line.days_to_liquidate) ~combine:Float.max
  in
  { lines; spread_cost; impact_cost; max_days_to_liquidate }

(* Bangia with the spread-volatility term at zero: LVaR is the book's VaR plus
   the total half-spread cost computed above, and nothing else. [None] when
   the total spread cost itself is unknown -- a book with one unpriceable
   spread cannot claim to know its liquidity-adjusted VaR either. *)
let lvar ~var (t : t) : float option = Option.map t.spread_cost ~f:(fun sc -> var +. sc)
