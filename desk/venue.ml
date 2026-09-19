(* What the desk needs to know from a venue, as a record of closures.

   A record rather than a module type, because the venue is chosen at run time
   -- Alpaca paper on the live host, the simulator on the demo host and in
   every test -- and a test builds one in a line. This is the READ half: an
   account, positions, a clock, quotes and daily bars. Submitting, cancelling
   and the stream of order updates are the trading half and arrive with the
   order manager; a desk with no trading half cannot trade, which is a state
   the page states rather than a stub that answers "not yet".

   Money is Notional.t and prices Price.t, as everywhere else. Alpaca sends
   both as decimal strings; parsing them is the adapter's job, and nothing
   past this interface sees a string where a number belongs. *)

open Core
open Async
open Ohcamel.Types

(* Core deprecated [Time_ns.sexp_of_t] to a value that does not typecheck (see
   lib/types.ml's [Time] module and desk/order.ml for the same fix):
   [Alternate_sexp] is the same [t] with a real [sexp_of_t], which is what
   [Quote.t] and [Session_clock.t] below derive against. *)
module Time_ns = Time_ns.Alternate_sexp

module Account = struct
  type t = {
    equity : Notional.t;
    cash : Notional.t;
    buying_power : Notional.t;
    (* Equity at the previous session's close, when the venue says; the
       session's P&L is the difference. *)
    last_equity : Notional.t option;
    status : string;
    trading_blocked : bool;
    shorting_enabled : bool;
  }
  [@@deriving sexp_of, compare, equal]
end

module Position = struct
  type t = {
    symbol : Symbol.t;
    (* us_equity, us_option, ... -- carried so a position the universe cannot
       hold can say what it is. *)
    asset_class : string;
    qty : Qty.t;
    avg_entry_price : Price.t option;
    market_value : Notional.t option;
  }
  [@@deriving sexp_of, compare, equal]
end

module Quote = struct
  type t = { bid : Price.t; ask : Price.t; at : Time_ns.t }
  [@@deriving sexp_of, compare, equal]

  let mid t = (Price.to_float t.bid +. Price.to_float t.ask) /. 2.0
end

module Session_clock = struct
  type t = {
    now : Time_ns.t;
    is_open : bool;
    next_open : Time_ns.t;
    next_close : Time_ns.t;
    (* The exchange's date for [next_close], taken from the venue's own text
       -- see Desk_time.local_date for why it is not computed. *)
    next_close_date : Date.t;
  }
  [@@deriving sexp_of, compare, equal]
end

module Bar = struct
  type t = {
    date : Date.t;
    open_ : float;
    high : float;
    low : float;
    close : float;
    volume : float;
  }
  [@@deriving sexp_of, compare, equal]
end

(* The trading half (design §3.4). Kept apart from [Read] so a desk that can
   read an account and not trade it is a value, not a stub that answers "not
   yet": [Trade.t option] is None, and the rules say why. *)
module Venue_order = struct
  type t = {
    id : string;
    client_order_id : string;
    symbol : Symbol.t;
    side : Order.Side.t;
    qty : float;
    filled_qty : float;
    filled_avg_price : float option;
    status : string;
    limit_price : float option;
  }
  [@@deriving sexp_of, compare, equal]

  (* Whether the venue still works the order: it can still fill, and a DELETE
     would stop it. Alpaca's statuses for a resting order; the simulated venue
     uses new and partially_filled. Not pending_cancel, done_for_day or a
     terminal status: a cancel of any of those stops nothing. *)
  let rests t =
    List.mem
      [
        "new";
        "accepted";
        "pending_new";
        "accepted_for_bidding";
        "partially_filled";
        "held";
      ]
      t.status ~equal:String.equal
end

module Submission = struct
  (* Three answers, because a request has three outcomes: the venue has the
     order, the venue refused it, or nobody knows. The third is not an error to
     retry; it is invariant 10's unknown, resolved only by asking. *)
  type t = Accepted of Venue_order.t | Rejected of string | Unknown of string
  [@@deriving sexp_of]
end

module Update = struct
  type t = {
    event : string;
    order : Venue_order.t;
    fill : Order.Fill.t option;
    at : Time_ns.t;
  }
  [@@deriving sexp_of]

  (* Alpaca's trade_updates vocabulary, onto the state machine's. Events that
     change nothing the machine models -- done_for_day, pending_cancel,
     calculated -- map to None and are logged by the caller, not dropped
     silently. A fill event without a fill is None too: the machine counts
     executions, and an event that names one without describing it has
     nothing to count. *)
  let to_event (t : t) : Order.Event.t option =
    match (t.event, t.fill) with
    | ("new" | "accepted" | "pending_new"), _ -> Some Order.Event.Venue_accepted
    | ("fill" | "partial_fill"), Some f -> Some (Order.Event.Venue_fill f)
    | "canceled", _ -> Some Order.Event.Venue_cancelled
    | "expired", _ -> Some Order.Event.Venue_expired
    | "rejected", _ -> Some (Order.Event.Venue_rejected "rejected by the venue")
    | _ -> None
end

(* ['permit t]: the submit takes a permit, and only the wire module can make
   one (desk/wire.mli). An adapter builds its submit for any permit, and
   ignores it; the order manager holds the wire module's permit type and
   submits through it in one place (Oms.submit_journaled). So a second call
   of the venue's submit anywhere else does not compile: it has no permit to
   pass. *)
module Trade = struct
  type 'permit t = {
    submit : 'permit -> Order.Request.t -> Submission.t Deferred.t;
    cancel : string -> unit Or_error.t Deferred.t;
    find_order : Ids.Client_order_id.t -> Venue_order.t option Or_error.t Deferred.t;
    open_orders : unit -> Venue_order.t list Or_error.t Deferred.t;
    updates : Update.t Pipe.Reader.t;
  }
end

module Read = struct
  type t = {
    name : string;
    account : unit -> Account.t Or_error.t Deferred.t;
    positions : unit -> Position.t list Or_error.t Deferred.t;
    clock : unit -> Session_clock.t Or_error.t Deferred.t;
    latest_quote : Symbol.t -> Quote.t option Or_error.t Deferred.t;
    daily_bars :
      Symbol.t list -> days:int -> Bar.t list Symbol.Map.t Or_error.t Deferred.t;
  }
end
