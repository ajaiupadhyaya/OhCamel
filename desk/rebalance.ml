(* A sized strategy's targets, as orders: pure, so every number is a test.

   The intake judges a signal accepted only when it passes R1-R7 and its
   strategy is (sizing live) -- ruling 2, which no code sets -- or
   accepted_incubation when its strategy is (sizing incubation), on the
   paper venue only, whatever its validation status. Then this turns
   its weights into whole shares, and the order manager proposes them as one
   rebalance of day limit orders priced off the quote fetched as they are
   sent, from 09:35 New York (Oms.propose_rebalance), through the same
   rules, the same gate and the same journal as a ticket.

   OVER THE STRATEGY'S OWN SYMBOLS. A signal lists only the names it wants
   held: its emitter drops a zero weight, so a signal that goes flat names
   nothing. Targets are therefore computed over the strategy's REGISTERED
   symbols, each at the signal's weight or at 0 when the signal does not name
   it -- without that, going flat would sell nothing.

   For each symbol, with capital_fraction f (an incubation strategy's
   effective fraction under the book's incubation_cap), the effective scale
   s in [0, 1] (1 until the overlay sets it; 0 flattens), equity E, weight w
   and the current mark p (the feed's live price, fresh -- never yesterday's
   close: Task 1.7, Oms.plan_rebalance):

     target  = trunc(w x f x s x E / p)      toward zero: whole shares, and
                                             never more than the weight asks
     current = the strategy's OWN position: the net of the desk's fills of
               orders whose source is signal:<slug>:*
     order   = target - current              zero is no order

   CURRENT IS NEVER THE ACCOUNT'S. Going flat sells exactly what the
   strategy bought, and never a share the owner holds by hand.

   EVERY DOUBT IS NO TARGETS AND A REASON, never a guess: an unknown equity,
   an unknown, stale or unusable price, an unknown fill history, a weight for a name
   the strategy does not own, a position in one it no longer owns, a position
   that is not a whole number of shares, or a target too large to be one.
   Whether the account still holds what the desk's fills say it should -- a
   hand sale, a reverse split, a fill the desk never heard of -- is asked of
   all the strategies at once, per name, by the one sub-ledger check
   (desk/subledger.ml, Oms.check_rebalance): a strategy's own position alone
   cannot be compared with an account that nets it against others'.

   AND NOTHING IS SIZED OFF THE CALENDAR ([calendar_refusal]). The signal,
   the close it is priced at and the newest recorded session must all be the
   same day, and no weekday may have passed since that day with no session
   recorded: after an outage, a signal from before it would otherwise be
   sized on a record weeks old and sent at the next session. A document already
   sized on its own close is re-sized at the newest one ([calendar_refusal]'s
   standing case). *)

open Core
open Ohcamel.Types

(* Where a rebalance's orders came from, and what the journal's source column
   says: signal:<slug>:<sequence>. A slug matches ^[a-z][a-z0-9_]{1,63}$, so
   it holds no colon, and [prefix] -- which ends in one -- begins every source
   a strategy's rebalances carry and nobody else's. *)
module Source = struct
  type t = { strategy : string; sequence : int; as_of : Date.t }
  [@@deriving sexp_of, compare, equal]

  let name ~strategy ~sequence = sprintf "signal:%s:%d" strategy sequence
  let to_string t = name ~strategy:t.strategy ~sequence:t.sequence
  let prefix ~strategy = sprintf "signal:%s:" strategy
end

(* One order of a rebalance, with the numbers it was derived from, so the
   outcome the intake records can show the arithmetic. *)
module Leg = struct
  type t = {
    symbol : Symbol.t;
    weight : float;
    price : float;
    target : int;
    current : int;
    side : Order.Side.t;
    qty : int;
  }
  [@@deriving sexp_of, compare, equal]

  let describe t =
    sprintf "%s %s %d (target %d at %.2f, weight %g; the strategy holds %d)"
      (Symbol.to_string t.symbol) (Order.Side.to_string t.side) t.qty t.target t.price
      t.weight t.current
end

let positive_finite x = Float.is_finite x && Float.( > ) x 0.0

(* A desk order's limit, from the quote fetched as it is sent (Task 1.7):
   a buy [band_bps] above the ask, a sell [band_bps] below the bid, so the
   order is marketable at once and never far -- the band is validated below
   the collar (Desk_spec). THE TICK: Alpaca takes a limit at or above $1.00
   in whole cents and one below $1.00 in hundredths of a cent; the desk sends
   two decimals at every price (desk/alpaca_paper.ml), and a whole cent is a
   whole number of hundredths, so a cent is a legal tick everywhere and the
   rules' [tick] holds every limit to it. Rounded AWAY from the quote: a buy
   up, so its limit is never below the ask, a sell down, so its limit is
   never above the bid -- 100.00 x 1.001 = 100.10, and an ask of 100.005 x
   1.001 = 100.105005 is 100.11, never 100.10. The 1e-6 of a cent absorbs
   the noise of the float product, and no more. *)
let quote_limit ~(side : Order.Side.t) ~(bid : float) ~(ask : float) ~(band_bps : float) :
    float =
  let band = band_bps /. 10_000.0 in
  match side with
  | Order.Side.Buy -> Float.round_up ((ask *. (1.0 +. band) *. 100.0) -. 1e-6) /. 100.0
  | Order.Side.Sell -> Float.round_down ((bid *. (1.0 -. band) *. 100.0) +. 1e-6) /. 100.0

(* Whether a quote fetched at send can price a desk order: None when it can,
   else why not -- the rules' [quote] refusal (Rules.check, Oms.pre_wire).
   No quote; a side that is zero, negative or not a number (one-sided: the
   venue's "no active bid"); a bid above the ask (crossed); or one stamped
   more than [max_age_s] from [now], either way -- older, or from a clock
   ahead of the desk's by more than that. A locked quote, bid equal to ask,
   prices: it is a market, if a thin one. *)
let quote_refusal ~(now : Time_ns.t) ~(max_age_s : float) (q : Venue.Quote.t option) :
    string option =
  match q with
  | None -> Some "no quote answered for it"
  | Some q -> (
      let bid = Price.to_float q.Venue.Quote.bid
      and ask = Price.to_float q.Venue.Quote.ask in
      let age = Time_ns.Span.to_sec (Time_ns.diff now q.Venue.Quote.at) in
      match () with
      | ()
        when not
               (Float.is_finite bid && Float.is_finite ask && Float.( > ) bid 0.0
              && Float.( > ) ask 0.0) ->
          Some (sprintf "its quote (%g x %g) is one-sided or not a price" bid ask)
      | () when Float.( > ) bid ask ->
          Some
            (sprintf "its quote is crossed: the bid %.4f is above the ask %.4f" bid ask)
      | () when not (Float.( <= ) (Float.abs age) max_age_s) ->
          Some
            (sprintf
               "its quote is %.1f s from now, and a quote prices an order within %g s" age
               max_age_s)
      | () -> None)

(* Legs worth less than [min_leg_notional] at their price, |qty x price|, are
   not sent: an order that small costs more in spread and attention than it
   moves the strategy. A leg whose target is 0 is NEVER dropped, however
   small: it closes the name, and dropping it would leave a residue the desk
   could never sell (an overlay of 0, a flat signal, a stop). (kept,
   dropped), each in the order given; the caller says what it dropped. *)
let drop_small ~(legs : Leg.t list) ~(min_leg_notional : float) : Leg.t list * Leg.t list
    =
  List.partition_tf legs ~f:(fun (l : Leg.t) ->
      l.Leg.target = 0
      || Float.( >= ) (Float.abs (Float.of_int l.Leg.qty *. l.Leg.price)) min_leg_notional)

(* Whether an order GROWS its name's position: opens one from flat, takes it
   further from zero, or crosses zero -- a long sold into a short, a short
   bought into a long, which opens a position in the other direction even
   when the new one is smaller. An order that brings a position toward zero,
   to zero at most, SHRINKS it. For a long-only strategy the buys grow and
   the sells shrink; for a short one a sell opens risk and a buy closes it.
   [position] is the book's, as the gate reads it. It decides the send order
   ([shrinking_first]), not what is gated: the gate takes every combination
   of the orders ([fill_sets]), since an order that shrinks one name can
   still take a sector over its line on its own. *)
let grows ~(position : float) ~(side : Order.Side.t) ~(qty : int) =
  let after = position +. (Order.Side.sign side *. Float.of_int qty) in
  Float.( <> ) after 0.0
  && (Float.( = ) position 0.0
     || Float.( < ) (position *. after) 0.0
     || Float.( > ) (Float.abs after) (Float.abs position))

(* The order a rebalance's orders go in: those that shrink a position first,
   those that grow one last, then by name. Were anything to stop a rebalance
   between two submits -- the switch, or a venue that does not acknowledge an
   order -- what went out first takes risk off rather than on. The gate does
   not lean on it: every combination of the orders is gated ([fill_sets]).
   It decides which orders reach the venue when the loop stops. *)
let shrinking_first ~(position : Symbol.t -> float)
    ((a_side, a_symbol, a_qty) : Order.Side.t * Symbol.t * int)
    ((b_side, b_symbol, b_qty) : Order.Side.t * Symbol.t * int) =
  let key side symbol qty =
    (Bool.to_int (grows ~position:(position symbol) ~side ~qty), symbol)
  in
  [%compare: int * Symbol.t] (key a_side a_symbol a_qty) (key b_side b_symbol b_qty)

(* The most orders a rebalance may have: every combination of them is gated
   ([fill_sets]), 2^n - 1 gate calls, so six orders are 63, each under every
   resting-order scenario. EXP-A01's strategies have one order each. *)
let max_legs = 6

(* Which of a rebalance's n orders, by their places 0..n-1 in the send
   order, could be all of it that fills: EVERY NON-EMPTY SUBSET. The orders
   go out in the send order and the loop stops at the first the venue does
   not acknowledge, or when the switch, the book or the session fails
   between two submits, so what reaches the venue is a prefix; and any order
   that reached it can still go unfilled -- a day limit the market moved
   away from -- or be refused after it was acknowledged. So the fills can be any
   subset of the orders. A sector's notional is the size of a net sum: an
   order that shrinks one name's position can take a sector over its line
   without the order that offsets it -- a cover of a short in one name
   leaves a long in another uncovered -- whichever of the two went first.
   The unit first, then the rest by size, smallest first, each size in send
   order. *)
let fill_sets n : int list list =
  let rec choose k = function
    | _ when k = 0 -> [ [] ]
    | [] -> []
    | x :: rest -> List.map (choose (k - 1) rest) ~f:(fun c -> x :: c) @ choose k rest
  in
  let all = List.init n ~f:Fn.id in
  if n <= 0 then []
  else
    all
    :: List.concat_map (List.init (n - 1) ~f:(fun k -> k + 1)) ~f:(fun k -> choose k all)

(* The regular session's close in New York. Before it, today's close has not
   happened, so the last date whose close should be recorded is yesterday's. *)
let regular_close = Time_ns.Ofday.create ~hr:16 ()

(* Weekdays d with a < d <= b: diff_weekdays counts [a, b), moved to (a, b]
   by its two ends, as desk/intake.ml's weekday bound does. *)
let weekdays_between a b =
  if Date.( <= ) b a then 0
  else
    Date.diff_weekdays b a
    - Bool.to_int (Date.is_weekday a)
    + Bool.to_int (Date.is_weekday b)

(* Whether a rebalance may be sized off the calendar, and why not:
   (a) the signal's as_of, every close it is priced at, and the newest
       recorded session are one date;
   (b) no weekday lies after that date and on or before D_ref -- New York's
       date now if its time is 16:00 or later, else the day before -- so the
       newest recorded session is the last one that has closed; and that
       date is not after D_ref, which would be a record ahead of the clock
       (a close before 16:00, or a clock that is wrong).
   A holiday is a weekday with no session, so it can only make (b) refuse,
   never pass: an owner who sees that refusal the day after a holiday reads
   why. The zone is the tz database's; none refuses.

   A STANDING DOCUMENT is one the desk already sent orders for -- so it
   passed this check once, on its own close -- and is being sized again: an
   overlay move, or the completion of orders that ended unfilled. Its
   weights hold until its strategy's next document, a month for a monthly
   strategy, so (a) asks of it only that its as_of is not after the newest
   session; the closes are still all the newest session's and (b) still
   holds, so it is never priced at a close older than the last. Without this,
   no move of the overlay, a flatten included, reached a monthly strategy
   after the first session of its month. *)
let calendar_refusal ~(zone : Timezone.t option) ~(now : Time_ns.t) ~(as_of : Date.t)
    ~(standing : bool) ~(latest : Date.t option) ~(closes : Date.t list) : string option =
  match (zone, latest) with
  | None, _ ->
      Some
        "the America/New_York time zone could not be loaded, so the desk cannot tell \
         whether the newest recorded session is the last one to close"
  | _, None -> Some "no session is recorded, so there is no close to size at"
  | Some zone, Some latest -> (
      match List.find closes ~f:(fun d -> not (Date.equal d latest)) with
      | _ when standing && Date.( > ) as_of latest ->
          Some
            (sprintf
               "the signal is as of %s, after the newest recorded session (%s): its \
                close is not recorded"
               (Date.to_string as_of) (Date.to_string latest))
      | _ when (not standing) && not (Date.equal as_of latest) ->
          Some
            (sprintf
               "the signal is as of %s, but the newest recorded session is %s: a \
                rebalance is sized only on the close of the signal's own day"
               (Date.to_string as_of) (Date.to_string latest))
      | Some d ->
          Some
            (sprintf
               "a close it would be priced at is %s's, not the newest session's (%s)"
               (Date.to_string d) (Date.to_string latest))
      | None ->
          let today, ofday = Time_ns.to_date_ofday now ~zone in
          let reference =
            if Time_ns.Ofday.( >= ) ofday regular_close then today
            else Date.add_days today (-1)
          in
          let missed = weekdays_between latest reference in
          if Date.( > ) latest reference then
            Some
              (sprintf
                 "the newest recorded session (%s) is after %s, the last date whose \
                  16:00 ET close has come: the record is ahead of the clock, and nothing \
                  is sized on it"
                 (Date.to_string latest) (Date.to_string reference))
          else if missed = 0 then None
          else
            Some
              (sprintf
                 "%d weekday%s after the newest recorded session (%s) %s closed by %s \
                  with no session recorded -- an outage, or a holiday -- so %s's close \
                  may not be the last, and nothing is sized on it"
                 missed
                 (if missed = 1 then "" else "s")
                 (Date.to_string latest)
                 (if missed = 1 then "has" else "have")
                 (Date.to_string reference) (Date.to_string latest)))

(* A position counted from fills is a sum of decimal quantities; one that is
   not within this of a whole number is not a position whole-share orders
   made, and is not sized against. *)
let whole_tolerance = 1e-6

(* The account's holding of a name is NOT compared here with the strategy's
   own: several strategies may hold one name, on opposite sides, and the
   account holds their net, so a strategy's own position may exceed the
   account's or sit across zero from it with nothing wrong. The account is
   checked against the strategies' positions SUMMED, per name, by the one
   sub-ledger check (desk/subledger.ml), which Oms.check_rebalance asks under
   the order manager's queue. [account] here orders the legs and nothing
   else. *)
let plan ~(symbols : Symbol.t list) ~(weights : (Symbol.t * float) list)
    ~(capital_fraction : float) ~(scale : float) ~(equity : (float, string) Result.t)
    ~(account : float Symbol.Map.t) ~(marks : (float, string) Result.t Symbol.Map.t)
    ~(current : (float Symbol.Map.t, string) Result.t) : (Leg.t list, string) Result.t =
  let open Result.Let_syntax in
  let owned s = List.mem symbols s ~equal:Symbol.equal in
  let%bind () =
    if List.is_empty symbols then Error "the strategy registers no symbol" else Ok ()
  in
  let%bind () =
    if Float.( > ) capital_fraction 0.0 && Float.( <= ) capital_fraction 1.0 then Ok ()
    else Error (sprintf "capital_fraction %g is not in (0, 1]" capital_fraction)
  in
  (* Written as two positive comparisons, so a NaN -- for which both are
     false -- is refused, never sized. 0 is a flatten, not an error. *)
  let%bind () =
    if Float.( >= ) scale 0.0 && Float.( <= ) scale 1.0 then Ok ()
    else Error (sprintf "scale %g is not in [0, 1]" scale)
  in
  let%bind equity =
    match equity with
    | Error why -> Error ("the book's equity is unknown: " ^ why)
    | Ok e when positive_finite e -> Ok e
    | Ok e -> Error (sprintf "the book's equity is %g, not a positive number" e)
  in
  let%bind current =
    Result.map_error current ~f:(fun why ->
        "the strategy's fill history is unknown: " ^ why)
  in
  let%bind () =
    match List.find weights ~f:(fun (s, _) -> not (owned s)) with
    | Some (s, _) ->
        Error
          (sprintf "the signal weights %s, which is not the strategy's"
             (Symbol.to_string s))
    | None -> (
        match
          List.find_a_dup weights ~compare:(fun (a, _) (b, _) -> Symbol.compare a b)
        with
        | Some (s, _) ->
            Error (sprintf "the signal weights %s twice" (Symbol.to_string s))
        | None -> Ok ())
  in
  let%bind () =
    match
      Map.to_alist current
      |> List.find ~f:(fun (s, q) ->
          (not (owned s)) && Float.( > ) (Float.abs q) whole_tolerance)
    with
    | Some (s, q) ->
        Error
          (sprintf
             "the strategy holds %.17g %s, which is no longer one of its symbols; \
              nothing is sized until a person settles it"
             q (Symbol.to_string s))
    | None -> Ok ()
  in
  let%map legs =
    List.map symbols ~f:(fun symbol ->
        let name = Symbol.to_string symbol in
        let weight =
          Option.value (List.Assoc.find weights symbol ~equal:Symbol.equal) ~default:0.0
        in
        let%bind price =
          match Map.find marks symbol with
          | None -> Error (sprintf "%s has no price" name)
          | Some (Error why) -> Error why
          | Some (Ok p) when positive_finite p -> Ok p
          | Some (Ok p) ->
              Error (sprintf "%s's price is %g, not a positive number" name p)
        in
        let%bind () =
          if Float.is_finite weight then Ok ()
          else Error (sprintf "%s's weight is not a number" name)
        in
        let%bind target =
          match
            Float.iround_towards_zero
              (weight *. capital_fraction *. scale *. equity /. price)
          with
          | Some n -> Ok n
          | None ->
              Error (sprintf "%s's target is not a representable number of shares" name)
        in
        let held = Option.value (Map.find current symbol) ~default:0.0 in
        let%bind current =
          let n = Float.round_nearest held in
          if Float.( <= ) (Float.abs (held -. n)) whole_tolerance then
            match Float.iround_nearest held with
            | Some n -> Ok n
            | None ->
                Error (sprintf "the strategy's %s position is not representable" name)
          else
            Error
              (sprintf
                 "the strategy's own %s position is %.17g shares, not a whole number; \
                  the desk will not size against it"
                 name held)
        in
        let order = target - current in
        Ok
          (Option.some_if (order <> 0)
             {
               Leg.symbol;
               weight;
               price;
               target;
               current;
               side = (if order > 0 then Order.Side.Buy else Order.Side.Sell);
               qty = Int.abs order;
             }))
    |> Result.all
  in
  let position s = Option.value (Map.find account s) ~default:0.0 in
  List.filter_opt legs
  |> List.sort ~compare:(fun (a : Leg.t) b ->
      shrinking_first ~position (a.side, a.symbol, a.qty) (b.side, b.symbol, b.qty))
