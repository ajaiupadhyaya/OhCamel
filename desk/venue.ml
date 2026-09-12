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
