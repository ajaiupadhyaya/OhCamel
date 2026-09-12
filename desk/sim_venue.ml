(* A venue that exists only in this process.

   The demo host has no broker, and the tests may not have a network. Both
   need a venue that answers every question the real one does, from marks the
   caller supplies, so that the desk above it runs the same code on both.

   Its calendar is synthetic and says so: sessions of a fixed length from the
   moment the venue opened, dated from 2026-01-01. A demo that dated its
   sessions with today's date would put fabricated days into a record that
   looks like a real one.

   A held position with no mark makes the account an error. The whole engine
   is arranged against a position marked at a price that never existed, and a
   simulator that quietly valued an unpriced name at zero would bring that
   failure back through the one component nobody expects to be wrong. *)

open Core
open Async
open Ohcamel.Types

let name = "simulated"
let base_date = Date.create_exn ~y:2026 ~m:Month.Jan ~d:1

type t = {
  opened_at : Time_ns.t;
  session_length : Time_ns.Span.t;
  marks : Symbol.t -> Price.t option;
  now : unit -> Time_ns.t;
  half_spread_bps : Symbol.t -> float;
  mutable cash : Notional.t;
  mutable positions : Qty.t Symbol.Map.t;
}

let create ?(session_length = Time_ns.Span.of_min 5.0) ~opened_at ~marks ~now
    ~half_spread_bps ~cash ~positions () =
  {
    opened_at;
    session_length;
    marks;
    now;
    half_spread_bps;
    cash;
    positions =
      Symbol.Map.of_alist_reduce positions ~f:Qty.add
      |> Map.filter ~f:(fun q -> not (Qty.is_zero q));
  }

let positions (t : t) : Venue.Position.t list =
  Map.to_alist t.positions
  |> List.map ~f:(fun (symbol, qty) ->
      {
        Venue.Position.symbol;
        asset_class = "us_equity";
        qty;
        avg_entry_price = None;
        market_value = Option.map (t.marks symbol) ~f:(fun price -> notional ~price ~qty);
      })

let account (t : t) : Venue.Account.t Or_error.t =
  let unpriced =
    Map.keys t.positions |> List.filter ~f:(fun s -> Option.is_none (t.marks s))
  in
  if not (List.is_empty unpriced) then
    Or_error.errorf "simulated venue: no mark for %s, so the account cannot be valued"
      (String.concat ~sep:", " (List.map unpriced ~f:Symbol.to_string))
  else
    let market =
      Notional.sum
        (List.filter_map (positions t) ~f:(fun p -> p.Venue.Position.market_value))
    in
    let equity = Notional.add t.cash market in
    Ok
      {
        Venue.Account.equity;
        cash = t.cash;
        buying_power = equity;
        last_equity = None;
        status = "ACTIVE";
        trading_blocked = false;
        shorting_enabled = true;
      }

let quote (t : t) (symbol : Symbol.t) : Venue.Quote.t option =
  Option.map (t.marks symbol) ~f:(fun mark ->
      let m = Price.to_float mark and h = t.half_spread_bps symbol /. 10_000.0 in
      {
        Venue.Quote.bid = Price.of_float (m *. (1.0 -. h));
        ask = Price.of_float (m *. (1.0 +. h));
        at = t.now ();
      })

(* The k-th session (k >= 1) closes at opened_at + k x length and is dated
   base_date + (k - 1) days; the next close is the first strictly after now. *)
let clock (t : t) : Venue.Session_clock.t =
  let now = t.now () in
  let elapsed = Time_ns.diff now t.opened_at in
  let k =
    if Time_ns.Span.( < ) elapsed Time_ns.Span.zero then 1
    else Float.iround_down_exn (Time_ns.Span.( // ) elapsed t.session_length) + 1
  in
  let next_close =
    Time_ns.add t.opened_at (Time_ns.Span.scale t.session_length (Float.of_int k))
  in
  {
    Venue.Session_clock.now;
    is_open = true;
    next_open = now;
    next_close;
    next_close_date = Date.add_days base_date (k - 1);
  }

let read (t : t) : Venue.Read.t =
  {
    Venue.Read.name;
    account = (fun () -> return (account t));
    positions = (fun () -> return (Ok (positions t)));
    clock = (fun () -> return (Ok (clock t)));
    latest_quote = (fun symbol -> return (Ok (quote t symbol)));
    daily_bars =
      (fun _ ~days:_ ->
        return
          (Or_error.error_string
             "simulated venue: it keeps no bar history; the demo's returns come from its \
              own synthetic bars"));
  }
