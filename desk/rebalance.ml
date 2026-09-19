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
   that is not a whole number of shares, or a target too large to be one. *)

open Core
open Ohcamel.Types

(* Where a rebalance's orders came from, and what the journal's source column
   says: signal:<slug>:<sequence>. A slug matches ^[a-z][a-z0-9_]{1,63}$, so
   it holds no colon, and [prefix] -- which ends in one -- begins every source
   a strategy's rebalances carry and nobody else's. *)
module Source = struct
  type t = { strategy : string; sequence : int } [@@deriving sexp_of, compare, equal]

  let to_string t = sprintf "signal:%s:%d" t.strategy t.sequence
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

(* A position counted from fills is a sum of decimal quantities; one that is
   not within this of a whole number is not a position whole-share orders
   made, and is not sized against. *)
let whole_tolerance = 1e-6

let plan ~(symbols : Symbol.t list) ~(weights : (Symbol.t * float) list)
    ~(capital_fraction : float) ~(equity : (float, string) Result.t)
    ~(marks : (float, string) Result.t Symbol.Map.t)
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
  (* Sells before buys, then by name: were a halt to land between two
     submits, what went out first takes risk off rather than on. *)
  List.filter_opt legs
  |> List.sort ~compare:(fun (a : Leg.t) b ->
      [%compare: int * Symbol.t]
        ((match a.side with Order.Side.Sell -> 0 | Order.Side.Buy -> 1), a.symbol)
        ((match b.side with Order.Side.Sell -> 0 | Order.Side.Buy -> 1), b.symbol))
