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
   exactly the order this rule exists to stop.

   A MARKET-ON-OPEN ORDER (Order.Tif.Opg, a rebalance's) is judged by two of
   these differently, and by the rest the same:
   - [session] is the opening auction's window, not the regular session
     ([Opening_auction]);
   - [mark] is the newest recorded session's close for the name, not the
     feed's price. IEX stops printing about 17:00 ET and the feed calls a name
     stale 90 s after its last print, so in the evening every name is stale;
     the order will trade at the next open, and the last close is the price
     the desk recorded for it. [notional], [adv] and [collar] are computed on
     that close. No close recorded is no price, and the order is refused.

   A MARKET-ON-CLOSE ORDER (Order.Tif.Cls) differs in [session] alone
   ([Closing_auction]); it is priced at the feed's live mark, as a day order
   is, because it is sent while the session prints. *)

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
    (* Who proposed it: the order's journal source -- "ticket", "manual",
       signal:<slug>:<sequence>. The duplicate rule is one proposer's double
       click, so two strategies wanting the same order are not duplicates. *)
    source : string;
  }
  [@@deriving sexp_of]
end

(* The venue's clock as last read: the two times it named, and nothing decided
   from them until an order asks. A flag read once a minute let an order
   through for up to a minute after the close; the times are the venue's own,
   so the rule is right at the order's own instant.

   Alpaca names the NEXT of each. While the market is open, next_close is this
   session's close and next_open the one after it, so the close comes first;
   while it is shut, the open comes first. So at [now] the market is open when
   [now] is before the next close and either the close came first when the
   clock was read -- it was open then -- or the open has come since. Both
   times ahead of [now] is not enough: read overnight, the next close is ahead
   and so is the next open. Past next_close the clock no longer says when the
   session after it ends, so it reads closed until the clock is read again,
   which [Oms.refresh_forever] does at that moment. *)
module Session = struct
  type t = { next_open : Time_ns.t; next_close : Time_ns.t }

  let is_open t ~now =
    Time_ns.( < ) now t.next_close
    && (Time_ns.( <= ) t.next_close t.next_open || Time_ns.( >= ) now t.next_open)

  (* The nearer of the two times still ahead of [now]: the next moment the
     clock's answer can change, or stop being an answer. *)
  let next_change t ~now =
    List.filter [ t.next_open; t.next_close ] ~f:(fun at -> Time_ns.( > ) at now)
    |> List.min_elt ~compare:Time_ns.compare
end

(* When a market-on-open order may be sent. Alpaca refuses one sent between
   09:28 and 19:00 ET and queues one sent after 19:00 for the next opening
   auction. So an Opg order is legal only when all three hold, and the
   refusal names the one that failed:
   - the regular session is closed;
   - it is before [next_open] less two minutes (09:28 on a 09:30 open);
   - it is at or after 19:00 America/New_York following the last close.

   The venue's clock names the NEXT open and close, never the last close, so
   the third is decided this way: it holds when the time of day in New York
   is 19:00 or later (the last close was today or earlier, and its 19:00 has
   come), or when the next open is later today in New York (today's session
   has not opened, so the last close was on an earlier day, whose 19:00 has
   passed). On a trading day that is exactly the rule. Between midnight and
   19:00 on a weekend or a holiday it refuses an order the rule would allow,
   because the clock cannot tell that day from the evening of a trading day
   before 19:00: failing closed, at hours the research service, which runs at
   19:15 ET on trading days, never sends at.

   The zone is the tz database's America/New_York (Desk_time.new_york), never
   a fixed offset, which would put 19:00 an hour wrong for half the year; no
   zone refuses every Opg order. *)
module Opening_auction = struct
  let evening = Time_ns.Ofday.create ~hr:19 ()
  let last_call = Time_ns.Span.of_min 2.0

  let et zone at =
    let date, ofday = Time_ns.to_date_ofday at ~zone in
    sprintf "%s %s ET" (Date.to_string date)
      (String.prefix (Time_ns.Ofday.to_string ofday) 5)

  let refusal ~(zone : Timezone.t option) ~(session : Session.t option) ~(now : Time_ns.t)
      : string option =
    match (session, zone) with
    | None, _ ->
        Some
          "no venue clock has answered, so the opening auction cannot be timed and no \
           market-on-open order is sent"
    | Some s, _ when Session.is_open s ~now ->
        Some
          "the regular session is open; a market-on-open order is sent after the close, \
           from 19:00 ET"
    | Some _, None ->
        Some
          "the America/New_York time zone could not be loaded from the tz database, so \
           19:00 ET cannot be told from any other hour and no market-on-open order is \
           sent"
    | Some s, Some zone ->
        let cutoff = Time_ns.sub s.next_open last_call in
        if Time_ns.( >= ) now cutoff then
          Some
            (sprintf
               "it is %s, not before %s, two minutes before the next open the clock \
                names (%s): the venue takes no market-on-open order from then until \
                19:00 ET"
               (et zone now) (et zone cutoff) (et zone s.next_open))
        else
          let today, ofday = Time_ns.to_date_ofday now ~zone in
          if
            Time_ns.Ofday.( >= ) ofday evening
            || Date.equal (Time_ns.to_date s.next_open ~zone) today
          then None
          else
            Some
              (sprintf
                 "it is %s, before 19:00 ET: the venue takes a market-on-open order only \
                  from 19:00 ET after a close, and the next open (%s) is not today's, so \
                  today's session may already have closed"
                 (et zone now) (et zone s.next_open))
end

(* When a market-on-close order may be sent. Alpaca takes one for the
   closing auction while the regular session is open, until 15:50 ET -- ten
   minutes before a 16:00 close -- and refuses it after. The venue's clock
   names this session's close while it is open, so the cut-off is that close
   less ten minutes, which is right on an early-close day too. Legal only when
   the session is open and [now] is before the cut-off; the refusal names the
   cut-off in New York's time, so no zone refuses every one, failing closed
   as the opening auction's rule does. *)
module Closing_auction = struct
  let last_call = Time_ns.Span.of_min 10.0

  let refusal ~(zone : Timezone.t option) ~(session : Session.t option) ~(now : Time_ns.t)
      : string option =
    match (session, zone) with
    | None, _ ->
        Some
          "no venue clock has answered, so the closing auction cannot be timed and no \
           market-on-close order is sent"
    | Some s, _ when not (Session.is_open s ~now) ->
        Some
          "the regular session is closed; a market-on-close order is sent while it is \
           open, until ten minutes before its close"
    | Some _, None ->
        Some
          "the America/New_York time zone could not be loaded from the tz database, so \
           the closing auction's cut-off cannot be named and no market-on-close order is \
           sent"
    | Some s, Some zone ->
        let cutoff = Time_ns.sub s.next_close last_call in
        if Time_ns.( < ) now cutoff then None
        else
          Some
            (sprintf
               "it is %s, not before %s, ten minutes before the close the clock names \
                (%s): the venue takes no market-on-close order from then until the \
                session ends"
               (Opening_auction.et zone now)
               (Opening_auction.et zone cutoff)
               (Opening_auction.et zone s.next_close))
end

(* The session rule for an order of [tif] at [now]: None when it may go, else
   why not. One function, because the order manager asks it again after the
   arrival quote and before each submit (Oms.pre_wire), when time has passed
   since [check] asked, and the two must never answer differently. *)
let session_refusal ~(tif : Order.Tif.t) ~(zone : Timezone.t option)
    ~(session : Session.t option) ~(now : Time_ns.t) : string option =
  match tif with
  | Order.Tif.Opg -> Opening_auction.refusal ~zone ~session ~now
  | Order.Tif.Cls -> Closing_auction.refusal ~zone ~session ~now
  | Order.Tif.Day -> (
      match session with
      | Some s when Session.is_open s ~now -> None
      | Some _ | None -> Some "the regular session is closed")

module Context = struct
  type t = {
    spec : Desk_spec.t;
    universe : Symbol.Set.t;
    can_trade : (unit, string) Result.t;
    (* The switch: its state decides ([switch_refusal]), and its words
       (Halt.reason, a restored trip's "(held across a restart)" among them)
       are what a refusal quotes. *)
    halt_state : Halt.State.t;
    halt_why : string option;
    (* The proposer's own position in the order's name, from its fills
       (Oms.strategy_position), and the account's as the gate reads it, less
       what the desk's own resting orders that would shrink it take off
       (Oms.reducible_account): what [reduces] judges under a limit's trip. A
       ticket is no strategy's, and its own position is 0, so no ticket
       passes a trip. *)
    own : float;
    account : float;
    (* Whether the name's sub-ledger is known to sum (Oms.netted): no order
       of the desk's in it may still fill, and the strategies' own positions
       with the hand's are the account's (Subledger.check finds no break).
       Then every fill in the name moves the account by exactly what it
       moves its owner's own position, which is what [reduces] needs to let
       one strategy of a netted pair flatten. Read only under a limit's trip
       with the book's (reduce_through_trip) word on; false otherwise, and
       false whenever it cannot be read. *)
    netted : bool;
    (* None: no clock has answered, or the last read failed -- closed. *)
    session : Session.t option;
    mark : Price.t option;
    stale : bool;
    (* The newest recorded session's close for the order's symbol, and that
       session's date: an Opg order's price. None when there is none. *)
    close : (Date.t * Price.t) option;
    (* America/New_York, for an Opg order's window; None when the tz database
       could not supply it. *)
    new_york : Timezone.t option;
    adv20 : float option;
    recent : Recent.t list;
    open_orders : int;
    (* The desk's own orders resting at the venue, each as its name and
       side: the wash rule's reading. Alpaca refuses an order while one of
       the account's own rests on the other side of the same name ("403:
       potential wash trade detected"), whoever proposed either. *)
    resting : (Symbol.t * Order.Side.t) list;
    (* The proposal's own source, as [Recent.source] names one. *)
    source : string;
    now : Time_ns.t;
  }
end

module Failure = struct
  type t = { rule : string; why : string } [@@deriving sexp_of, compare, equal]
end

(* Whether a refusal of an order of [tif] is one of TIMING -- the order may
   go later, unchanged, and nothing about it is wrong: its failures are all
   the session rule's (a market-on-open order before 19:00 ET or after its
   last call, a day order while the session is closed, no venue clock yet)
   or the wash rule's (an order of the desk's own rests on the other side of
   the name, and will have filled or expired by a later pass), and, for a
   day or market-on-close order refused by the session rule, a mark the feed
   calls stale or has not printed, because outside the session every name
   is stale. Any other failure -- the switch, the mandate, a price, a cap --
   is final. The intake keeps a judgement whose rebalance was refused for
   timing pending and tries it again (design §4.3); an empty list is not a
   refusal at all. *)
let is_timing_refusal ~(tif : Order.Tif.t) (failures : Failure.t list) =
  let session (f : Failure.t) = String.equal f.Failure.rule "session" in
  let timing (f : Failure.t) = session f || String.equal f.Failure.rule "wash" in
  (not (List.is_empty failures))
  && List.exists failures ~f:timing
  && List.for_all failures ~f:(fun f ->
      timing f
      || String.equal f.Failure.rule "mark"
         && (not (Order.Tif.equal tif Order.Tif.Opg))
         && List.exists failures ~f:session)

(* Whether an order of [qty] on [side] only shrinks a position: what a
   limit's trip lets through (R2). Exactly:

     (A) it shrinks the proposer's own position [own] (from its fills)
         without crossing zero: [own] is on the side of zero opposite the
         order, and [qty] is no larger than |own|;
   and one of
     (B1) it shrinks the account's position [account] (net of the desk's
          other resting reductions in the name, Oms.reducible_account)
          without crossing zero, the same way: [account] opposite the order,
          [qty] no larger than |account|;
     (B2) [netted]: the name's sub-ledger is known to sum (Rules.Context's
          [netted]) -- nothing in it may still fill, and the strategies' and
          the hand's own positions are the account's.

   Why B2 is safe. What must never happen under a trip is a position taken
   through zero, or the account holding in the name more than its owners
   together do. With the sub-ledger whole, the account IS the sum of its
   owners' positions, before the order and -- since a fill moves the account
   and its owner's own position by the same quantity -- after it, however
   much of it fills; the order only shrinks its owner's position (A), so the
   sum of the owners' absolute positions, which bounds the account's,
   strictly falls, and no owner's position crosses zero. The account's own
   net may move away from zero, and that is the netting: a strategy long 100
   beside one short 50 is an account long 50, and the short's cover leaves
   the account long 100 -- the long's own position, which the trip may cut
   next -- while the long's sale leaves it short 50, the short's. Without B2
   neither could ever be flattened through a trip: the long's 100 is more
   than the account's 50, and the short's cover grows the account's long.
   When the sum is not known -- a break, an order that may still fill, a
   journal that cannot be read -- B1 alone binds, as before netting.

   WHAT THIS WIDENS, FOR WHOEVER APPROVES R2. Before netting, an order that
   passed a trip never moved the account's position away from zero. Under
   B2 it can: a short's cover raises the account's NET position in the name
   by up to the cover's size, which is at most what the other strategies
   hold on the other side -- their long, hedged until then, becomes
   unhedged. Under a drawdown trip nothing at the account's level bounds
   that beyond the other strategies' own positions; the gate still judges
   every such order against every limit, so under a gross or VaR trip the
   cover may still be refused there. R2 is the spec's default awaiting the
   owner, and the book's word stays off until the owner approves it.

   An order larger than its room is not clipped to it -- nothing here
   changes an order -- it is not a reduction, and the switch refuses it
   whole. Nor is a flat own position. A number that is not a number: a NaN
   [own] fails (A), so it always refuses; a NaN [account] fails only B1, so
   it refuses unless [netted] holds -- which it cannot then, since the
   account's NaN comes from a journal that could not be read
   (Oms.reducible_account), and the same unreadable journal makes [netted]
   false (Oms.netted). Order.epsilon absorbs the noise of a float sum of
   whole fills, and nothing more. *)
let reduces ~(own : float) ~(account : float) ~(netted : bool) ~(side : Order.Side.t)
    ~(qty : int) =
  let shrinks position =
    Float.is_finite position
    && (match side with
      | Order.Side.Sell -> Float.( > ) position 0.0
      | Order.Side.Buy -> Float.( < ) position 0.0)
    && Float.( <= ) (Float.of_int qty) (Float.abs position +. Order.epsilon)
  in
  qty > 0 && shrinks own && (shrinks account || netted)

(* The switch's rule for an order: None when it may go, else why not. One
   function, because the order manager asks it again at the wire
   (Oms.pre_wire) and the two must never answer differently -- the reason
   [session_refusal] is one function.

   A LIMIT'S TRIP LETS A REDUCTION THROUGH, AND NOTHING ELSE DOES (ruling R2,
   the completion spec's default, awaiting the owner's approval; the word
   stays false on any real book until then). With the book's (reduce_through_trip)
   word on, an order [reduces] passes while the switch is Tripped -- a trip
   this process's source made, or one restored from the journal, which
   Halt.state reads the same -- so a stop's flatten and a governor's cut are
   not refused by the very switch they answer to. A hand's halt and the
   engine's stop refuse everything: the person who pressed it knew something
   the limits did not, and after the engine's stop no fill would be heard.
   The refusal quotes the switch's own words ([halt_why], Halt.reason), and
   under a trip with the word on says what would have passed. A context
   whose words and state disagree -- words with no state -- is refused.
   Nothing upstream compares one strategy's position with the account's any
   more (the one sub-ledger check replaced that, desk/subledger.ml): a
   strategy's own position may exceed the account's, or sit on the other
   side of zero from it, because other strategies hold the name too. So
   both halves of [reduces] bind here -- the strategy's own, and either the
   account's, netted of the desk's resting reductions
   (Oms.reducible_account), or a sub-ledger known to sum ([netted]). *)
let switch_refusal ~(spec : Desk_spec.t) ~(halt_state : Halt.State.t)
    ~(halt_why : string option) ~own ~account ~netted ~side ~qty : string option =
  let words =
    match halt_why with
    | Some why -> why
    | None -> sprintf "the kill switch reads %s" (Halt.State.name halt_state)
  in
  match (halt_state, halt_why) with
  | Halt.State.Clear, None -> None
  | Halt.State.Tripped _, _ when spec.Desk_spec.reduce_through_trip ->
      if reduces ~own ~account ~netted ~side ~qty then None
      else
        Some
          (sprintf
             "%s; under a limit's trip only an order that shrinks the strategy's own \
              position (%.17g) without crossing zero passes, and only if it also shrinks \
              the account's (%.17g) by no more than it holds, or the name's sub-ledger \
              is known to sum (%s); a %s of %d does not"
             words own account
             (if netted then "it is" else "it is not")
             (Order.Side.to_string side) qty)
  | Halt.State.Clear, Some _
  | Halt.State.Tripped _, _
  | Halt.State.Halted _, _
  | Halt.State.Stopped _, _ ->
      Some words

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
    "wash";
    "open_orders";
  ]

let check (c : Context.t) (r : Order.Request.t) : Failure.t list =
  let fail rule why = Some { Failure.rule; why } in
  let sym = Symbol.to_string r.Order.Request.symbol in
  let opening = Order.Tif.equal r.Order.Request.tif Order.Tif.Opg in
  (* A close of zero or less is a cell nobody wrote, recorded at a close: not
     a price. *)
  let close =
    Option.filter c.close ~f:(fun (_, p) ->
        let p = Price.to_float p in
        Float.is_finite p && Float.( > ) p 0.0)
  in
  let priced =
    if opening then Option.map close ~f:(fun (_, p) -> Price.to_float p)
    else
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
      Option.map
        (switch_refusal ~spec:c.spec ~halt_state:c.halt_state ~halt_why:c.halt_why
           ~own:c.own ~account:c.account ~netted:c.netted ~side:r.Order.Request.side
           ~qty:r.Order.Request.qty) ~f:(fun why -> { Failure.rule = "kill_switch"; why });
      Option.map
        (session_refusal ~tif:r.Order.Request.tif ~zone:c.new_york ~session:c.session
           ~now:c.now) ~f:(fun why -> { Failure.rule = "session"; why });
      (if opening then
         match close with
         | Some _ -> None
         | None ->
             fail "mark"
               (sprintf
                  "%s has no recorded session close, and a market-on-open order is \
                   priced at the last close"
                  sym)
       else
         match (c.mark, c.stale) with
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
             && String.equal x.Recent.source c.source
             && Float.( < )
                  (Time_ns.Span.to_sec (Time_ns.diff c.now x.Recent.at))
                  c.spec.Desk_spec.duplicate_window_s)
       then
         fail "duplicate"
           (sprintf "the same order was proposed less than %g s ago"
              c.spec.Desk_spec.duplicate_window_s)
       else None);
      (* The venue's own rule, asked here so the desk never sends what it
         would refuse: on 2026-10-20 one strategy's BIL sell met another's
         BIL buy resting for the same auction, Alpaca answered "403:
         potential wash trade detected", and the refusal stopped the rest
         of the selling strategy's rebalance. A timing refusal
         ([is_timing_refusal]): the resting order fills or expires, and the
         proposal goes unchanged. *)
      (match
         List.find c.resting ~f:(fun (s, side) ->
             Symbol.equal s r.Order.Request.symbol
             && not (Order.Side.equal side r.Order.Request.side))
       with
      | Some (_, side) ->
          fail "wash"
            (sprintf
               "a %s of %s of the desk's own rests at the venue, which refuses an order \
                on the other side of the name as a wash trade until it is done"
               (Order.Side.to_string side) sym)
      | None -> None);
      (if c.open_orders < c.spec.Desk_spec.max_open_orders then None
       else
         fail "open_orders"
           (sprintf "%d orders are already open; the cap is %d" c.open_orders
              c.spec.Desk_spec.max_open_orders));
    ]
