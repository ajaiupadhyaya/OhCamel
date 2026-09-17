(* The rules an order must pass before the gate is even asked.

   Design §3.7. The gate asks what an order does to the book's limits; these
   ask whether the order should exist at all -- is it in the mandate, is the
   desk allowed to trade, is there a price to trade against, is it the size of
   a mistake. Pure: the order manager gathers the context, and every rule is a
   line of arithmetic on it.

   EVERY FAILURE IS REPORTED. The signal contract stops at its first failing
   rule because a signal is a document to reject; a ticket is something a
   person is fixing, and fixing one problem to discover the next is a slow way
   to be told three things at once.

   A missing or stale mark fails [mark] and skips the rules that need a price
   ([notional], [collar]): a notional computed against a price nobody has is a
   number with no meaning, and reporting it would bury the one reason that
   matters. The same for an unknown twenty-day volume, which fails [adv]
   rather than passing it: an order sized against liquidity nobody measured is
   exactly the order this rule exists to stop. *)

open Core
open Ohcamel.Types
module Desk_spec = Ohcamel.Config.Book.Desk_spec

module Recent = struct
  (* Alternate_sexp is Time_ns.t itself, with a sexp_of Core has not poisoned.
     The field names it so the record can derive one; the arithmetic below
     keeps Core's Time_ns, which the alias module does not carry. *)
  type t = {
    symbol : Symbol.t;
    side : Order.Side.t;
    qty : int;
    at : Time_ns.Alternate_sexp.t;
  }
  [@@deriving sexp_of]
end

module Context = struct
  type t = {
    spec : Desk_spec.t;
    universe : Symbol.Set.t;
    can_trade : (unit, string) Result.t;
    halted : string option;
    session_open : bool;
    mark : Price.t option;
    stale : bool;
    adv20 : float option;
    recent : Recent.t list;
    open_orders : int;
    now : Time_ns.t;
  }
end

module Failure = struct
  type t = { rule : string; why : string } [@@deriving sexp_of, compare, equal]
end

let names =
  [
    "universe";
    "whole_shares";
    "trading";
    "kill_switch";
    "session";
    "mark";
    "tick";
    "collar";
    "notional";
    "adv";
    "duplicate";
    "open_orders";
  ]

let check (c : Context.t) (r : Order.Request.t) : Failure.t list =
  let fail rule why = Some { Failure.rule; why } in
  let sym = Symbol.to_string r.Order.Request.symbol in
  let priced =
    match c.mark with Some p when not c.stale -> Some (Price.to_float p) | _ -> None
  in
  let limit =
    Option.map (Order.Kind.limit_price r.Order.Request.kind) ~f:Price.to_float
  in
  List.filter_opt
    [
      (if Set.mem c.universe r.Order.Request.symbol then None
       else fail "universe" (sprintf "%s is not in the book's universe" sym));
      (if r.Order.Request.qty > 0 then None
       else
         fail "whole_shares"
           (sprintf "%d shares is not a positive whole number" r.Order.Request.qty));
      (match (c.spec.Desk_spec.trading, c.can_trade) with
      | Desk_spec.Disabled, _ -> fail "trading" "the book disables trading"
      | Desk_spec.Enabled, Error why -> fail "trading" why
      | Desk_spec.Enabled, Ok () -> None);
      Option.map c.halted ~f:(fun why -> { Failure.rule = "kill_switch"; why });
      (if c.session_open then None else fail "session" "the regular session is closed");
      (match (c.mark, c.stale) with
      | None, _ -> fail "mark" (sprintf "%s has no mark" sym)
      | Some _, true -> fail "mark" (sprintf "%s's mark is stale" sym)
      | Some _, false -> None);
      (* A whole cent at every price. Rule 612 allows four decimals below a
         dollar, but desk/alpaca_paper.ml sends a limit with two, so a finer
         limit would be journaled and gated at one price and reach the venue
         at another. *)
      (match limit with
      | Some p
        when Float.( > )
               (Float.abs ((p *. 100.0) -. Float.round_nearest (p *. 100.0)))
               1e-6 ->
          fail "tick" (sprintf "a limit of %g is not a whole cent" p)
      | _ -> None);
      (match (limit, priced) with
      | Some p, Some m
        when Float.( > ) (Float.abs (p -. m) /. m) (c.spec.Desk_spec.price_collar +. 1e-12)
        ->
          fail "collar"
            (sprintf
               "a limit of %.2f is %.2f%% from the mark of %.2f; the collar is %.2f%%" p
               (100.0 *. Float.abs (p -. m) /. m)
               m
               (100.0 *. c.spec.Desk_spec.price_collar))
      | _ -> None);
      (match priced with
      | Some m
        when Float.( > )
               (Float.of_int r.Order.Request.qty *. m)
               c.spec.Desk_spec.max_order_notional ->
          fail "notional"
            (sprintf "%d x %.2f = %.2f exceeds the order cap of %.2f" r.Order.Request.qty
               m
               (Float.of_int r.Order.Request.qty *. m)
               c.spec.Desk_spec.max_order_notional)
      | _ -> None);
      (match c.adv20 with
      | None -> fail "adv" (sprintf "%s's twenty-day volume is unknown" sym)
      | Some adv
        when Float.( > )
               (Float.of_int r.Order.Request.qty)
               (c.spec.Desk_spec.max_adv_participation *. adv) ->
          fail "adv"
            (sprintf "%d shares is more than %.2f%% of %s's twenty-day volume of %.0f"
               r.Order.Request.qty
               (100.0 *. c.spec.Desk_spec.max_adv_participation)
               sym adv)
      | Some _ -> None);
      (if
         List.exists c.recent ~f:(fun x ->
             Symbol.equal x.Recent.symbol r.Order.Request.symbol
             && Order.Side.equal x.Recent.side r.Order.Request.side
             && x.Recent.qty = r.Order.Request.qty
             && Float.( < )
                  (Time_ns.Span.to_sec (Time_ns.diff c.now x.Recent.at))
                  c.spec.Desk_spec.duplicate_window_s)
       then
         fail "duplicate"
           (sprintf "the same order was proposed less than %g s ago"
              c.spec.Desk_spec.duplicate_window_s)
       else None);
      (if c.open_orders < c.spec.Desk_spec.max_open_orders then None
       else
         fail "open_orders"
           (sprintf "%d orders are already open; the cap is %d" c.open_orders
              c.spec.Desk_spec.max_open_orders));
    ]
