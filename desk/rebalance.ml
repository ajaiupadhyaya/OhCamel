(* A live strategy's targets, as orders: pure, so every number is a test.

   The intake judges a signal accepted only when it passes R1-R7 and its
   strategy is (sizing live) -- ruling 2, which no code sets. Then this turns
   its weights into whole shares, and the order manager proposes them as one
   rebalance of market-on-open orders (Oms.propose_rebalance), through the
   same rules, the same gate and the same journal as a ticket.

   OVER THE STRATEGY'S OWN SYMBOLS. A signal lists only the names it wants
   held: its emitter drops a zero weight, so a signal that goes flat names
   nothing. Targets are therefore computed over the strategy's REGISTERED
   symbols, each at the signal's weight or at 0 when the signal does not name
   it -- without that, going flat would sell nothing.

   For each symbol, with capital_fraction f, equity E, weight w and the
   opening-auction price p (the last recorded close):

     target  = trunc(w x f x E / p)          toward zero: whole shares, and
                                             never more than the weight asks
     current = the strategy's OWN position: the net of the desk's fills of
               orders whose source is signal:<slug>:*
     order   = target - current              zero is no order

   CURRENT IS NEVER THE ACCOUNT'S. Going flat sells exactly what the
   strategy bought, and never a share the owner holds by hand.

   EVERY DOUBT IS NO TARGETS AND A REASON, never a guess: an unknown equity,
   an unknown or unusable price, an unknown fill history, a weight for a name
   the strategy does not own, a position in one it no longer owns, a position
   that is not a whole number of shares, a target too large to be one, or an
   account that holds less than the strategy's own position in the same
   direction -- a hand sale, a reverse split or a fill the desk never heard
   of, after which selling the strategy's shares would sell what is not
   there.

   AND NOTHING IS SIZED OFF THE CALENDAR ([calendar_refusal]). The signal,
   the close it is priced at and the newest recorded session must all be the
   same day, and no weekday may have passed since that day with no session
   recorded: after an outage, a signal from before it would otherwise be
   sized at a close weeks old and sent to the next open. *)

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

(* Whether an order GROWS its name's position: opens one from flat, takes it
   further from zero, or crosses zero -- a long sold into a short, a short
   bought into a long, which opens a position in the other direction even
   when the new one is smaller. An order that brings a position toward zero,
   to zero at most, SHRINKS it. For a long-only strategy the buys grow and
   the sells shrink; for a short one a sell opens risk and a buy closes it.
   [position] is the book's, as the gate reads it. *)
let grows ~(position : float) ~(side : Order.Side.t) ~(qty : int) =
  let after = position +. (Order.Side.sign side *. Float.of_int qty) in
  Float.( <> ) after 0.0
  && (Float.( = ) position 0.0
     || Float.( < ) (position *. after) 0.0
     || Float.( > ) (Float.abs after) (Float.abs position))

(* The order a rebalance's orders go in: those that shrink a position first,
   those that grow one last, then by name. Were anything to stop a rebalance
   between two submits -- the switch, or a venue that does not acknowledge an
   order -- what went out first takes risk off rather than on. *)
let shrinking_first ~(position : Symbol.t -> float)
    ((a_side, a_symbol, a_qty) : Order.Side.t * Symbol.t * int)
    ((b_side, b_symbol, b_qty) : Order.Side.t * Symbol.t * int) =
  let key side symbol qty =
    (Bool.to_int (grows ~position:(position symbol) ~side ~qty), symbol)
  in
  [%compare: int * Symbol.t] (key a_side a_symbol a_qty) (key b_side b_symbol b_qty)

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
   why. The zone is the tz database's; none refuses. *)
let calendar_refusal ~(zone : Timezone.t option) ~(now : Time_ns.t) ~(as_of : Date.t)
    ~(latest : Date.t option) ~(closes : Date.t list) : string option =
  match (zone, latest) with
  | None, _ ->
      Some
        "the America/New_York time zone could not be loaded, so the desk cannot tell \
         whether the newest recorded session is the last one to close"
  | _, None -> Some "no session is recorded, so there is no close to size at"
  | Some zone, Some latest -> (
      match List.find closes ~f:(fun d -> not (Date.equal d latest)) with
      | _ when not (Date.equal as_of latest) ->
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

let plan ~(symbols : Symbol.t list) ~(weights : (Symbol.t * float) list)
    ~(capital_fraction : float) ~(equity : (float, string) Result.t)
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
            Float.iround_towards_zero (weight *. capital_fraction *. equity /. price)
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
        (* The account's holding, from the book the gate reads, must cover
           the strategy's own in its direction: a hand sale or a reverse
           split can leave it short of it, and then selling the strategy's
           shares sells what is not there. *)
        let%bind () =
          let held = Option.value (Map.find account symbol) ~default:0.0 in
          let short_of_it =
            (current > 0 && Float.( < ) held (Float.of_int current -. whole_tolerance))
            || (current < 0 && Float.( > ) held (Float.of_int current +. whole_tolerance))
          in
          if short_of_it then
            Error
              (sprintf
                 "the account holds %.17g %s, less than the strategy's own %d: a hand \
                  sale, a split or a fill the desk never heard of; nothing is sized \
                  until a person settles it"
                 held name current)
          else Ok ()
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
