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

(* The session the venue's clock reports. [Rolling] is the demo's: always
   open, one session of [session_length] after another. A test sets the other
   two with [set_session] (Task 15), to put the venue after a close -- where a
   market-on-open order is taken and held -- and then into the session that
   opens, where it fills. [Closed] names the next open and the close after it;
   [Open] names this session's close and the next open, as a venue's clock
   does. *)
module Session = struct
  type t =
    | Rolling
    | Closed of { next_open : Time_ns.t; next_close : Time_ns.t }
    | Open of { next_close : Time_ns.t; next_open : Time_ns.t }
end

type t = {
  opened_at : Time_ns.t;
  session_length : Time_ns.Span.t;
  marks : Symbol.t -> Price.t option;
  now : unit -> Time_ns.t;
  half_spread_bps : Symbol.t -> float;
  mutable cash : Notional.t;
  mutable positions : Qty.t Symbol.Map.t;
  latency : Time_ns.Span.t;
  mutable next_id : int;
  (* Venue id -> (the order as last reported, the request that made it, the
     time it becomes fillable). Keyed by venue id because that is what
     [cancel_now] and [step] address by; [by_client] is the second index. *)
  mutable orders : (Venue.Venue_order.t * Order.Request.t * Time_ns.t) String.Map.t;
  mutable by_client : string String.Map.t;
  (* None until [trade] is called; see [emit]. *)
  mutable updates : Venue.Update.t Pipe.Writer.t option;
  mutable session : Session.t;
}

let create ?(session_length = Time_ns.Span.of_min 5.0)
    ?(latency = Time_ns.Span.of_ms 150.0) ~opened_at ~marks ~now ~half_spread_bps ~cash
    ~positions () =
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
    latency;
    next_id = 0;
    orders = String.Map.empty;
    by_client = String.Map.empty;
    updates = None;
    session = Session.Rolling;
  }

let set_session t session = t.session <- session

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

(* A session a test set is reported as set, dated by its close's UTC date:
   the test chooses the times, and nothing here pretends to know the
   exchange's calendar. [Rolling]: the k-th session (k >= 1) closes at
   opened_at + k x length and is dated base_date + (k - 1) days; the next
   close is the first strictly after now. *)
let clock (t : t) : Venue.Session_clock.t =
  let now = t.now () in
  let set ~is_open ~next_open ~next_close =
    {
      Venue.Session_clock.now;
      is_open;
      next_open;
      next_close;
      next_close_date = Time_ns.to_date next_close ~zone:Timezone.utc;
    }
  in
  match t.session with
  | Session.Closed { next_open; next_close } -> set ~is_open:false ~next_open ~next_close
  | Session.Open { next_close; next_open } -> set ~is_open:true ~next_open ~next_close
  | Session.Rolling ->
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

(* The trading half. A limit fills against the side of the quote the order
   trades on -- a buy against the ask, a sell against the bid -- once that
   quote reaches the limit; that is the rule a market order already pays, and
   the ruling above [Read] says why a limit is held to it too, not to the
   mark. *)

let half t symbol = t.half_spread_bps symbol /. 10_000.0

let submit_now (t : t) (r : Order.Request.t) : Venue.Submission.t =
  t.next_id <- t.next_id + 1;
  let id = sprintf "sim-%d" t.next_id in
  let order =
    {
      Venue.Venue_order.id;
      client_order_id = Ids.Client_order_id.to_string r.Order.Request.client_order_id;
      symbol = r.Order.Request.symbol;
      side = r.Order.Request.side;
      qty = Float.of_int r.Order.Request.qty;
      filled_qty = 0.0;
      filled_avg_price = None;
      status = "new";
      limit_price =
        Option.map (Order.Kind.limit_price r.Order.Request.kind) ~f:Price.to_float;
    }
  in
  t.orders <- Map.set t.orders ~key:id ~data:(order, r, Time_ns.add (t.now ()) t.latency);
  t.by_client <- Map.set t.by_client ~key:order.Venue.Venue_order.client_order_id ~data:id;
  Venue.Submission.Accepted order

let find_now (t : t) (client : Ids.Client_order_id.t) : Venue.Venue_order.t option =
  Option.bind
    (Map.find t.by_client (Ids.Client_order_id.to_string client))
    ~f:(fun id -> Option.map (Map.find t.orders id) ~f:(fun (o, _, _) -> o))

let open_now (t : t) : Venue.Venue_order.t list =
  Map.data t.orders
  |> List.filter_map ~f:(fun (o, _, _) ->
      if
        List.mem [ "new"; "partially_filled" ] o.Venue.Venue_order.status
          ~equal:String.equal
      then Some o
      else None)

let cancel_now (t : t) (id : string) : Venue.Update.t Or_error.t =
  match Map.find t.orders id with
  | Some (o, r, due) when String.equal o.Venue.Venue_order.status "new" ->
      let o = { o with Venue.Venue_order.status = "canceled" } in
      t.orders <- Map.set t.orders ~key:id ~data:(o, r, due);
      Ok { Venue.Update.event = "canceled"; order = o; fill = None; at = t.now () }
  | Some _ -> Or_error.errorf "simulated venue: order %s is not cancelable" id
  | None -> Or_error.errorf "simulated venue: no order %s" id

let step (t : t) : Venue.Update.t list =
  let now = t.now () in
  Map.to_alist t.orders
  |> List.filter_map ~f:(fun (id, (o, r, due)) ->
      (* Nothing trades while a set session is closed: a market-on-open order
         taken after the close waits there for the session a test opens, and
         then fills as a market order does, half a spread from the mark --
         every fill here pays the spread, the opening auction's included. *)
      let closed = match t.session with Session.Closed _ -> true | _ -> false in
      if
        (not (String.equal o.Venue.Venue_order.status "new"))
        || Time_ns.( < ) now due || closed
      then None
      else
        match t.marks o.Venue.Venue_order.symbol with
        | None -> None
        | Some mark -> (
            let m = Price.to_float mark and h = half t o.Venue.Venue_order.symbol in
            let price =
              match (o.Venue.Venue_order.side, o.Venue.Venue_order.limit_price) with
              | Order.Side.Buy, None -> Some (m *. (1.0 +. h))
              | Order.Side.Sell, None -> Some (m *. (1.0 -. h))
              | Order.Side.Buy, Some l ->
                  if Float.( <= ) (m *. (1.0 +. h)) l then Some (m *. (1.0 +. h))
                  else None
              | Order.Side.Sell, Some l ->
                  if Float.( >= ) (m *. (1.0 -. h)) l then Some (m *. (1.0 -. h))
                  else None
            in
            match price with
            | None -> None
            | Some price ->
                let signed =
                  Order.Side.sign o.Venue.Venue_order.side *. o.Venue.Venue_order.qty
                in
                let position =
                  Qty.add
                    (Option.value
                       (Map.find t.positions o.Venue.Venue_order.symbol)
                       ~default:Qty.zero)
                    (Qty.of_float signed)
                in
                t.positions <-
                  Map.set t.positions ~key:o.Venue.Venue_order.symbol ~data:position;
                t.cash <- Notional.sub t.cash (Notional.of_float (signed *. price));
                let o =
                  {
                    o with
                    Venue.Venue_order.status = "filled";
                    filled_qty = o.Venue.Venue_order.qty;
                    filled_avg_price = Some price;
                  }
                in
                t.orders <- Map.set t.orders ~key:id ~data:(o, r, due);
                Some
                  {
                    Venue.Update.event = "fill";
                    order = o;
                    fill =
                      Some
                        {
                          Order.Fill.execution_id = id ^ "-1";
                          qty = o.Venue.Venue_order.qty;
                          price = Price.of_float price;
                          at = now;
                          position_qty = Some (Qty.to_float position);
                        };
                    at = now;
                  }))

let received (t : t) = t.next_id

(* Updates go to the pipe of the most recent [trade]: a test that builds a
   second order manager over the same venue -- a restart -- hears the venue on
   the new pipe, as a restarted process hears it on a new socket. *)
let emit (t : t) (u : Venue.Update.t) =
  Option.iter t.updates ~f:(fun w -> Pipe.write_without_pushback_if_open w u)

let pump (t : t) = List.iter (step t) ~f:(emit t)

(* [auto] is false only in tests, which pump by hand so every fill happens
   where the test says: a global constraint of this plan is that no test
   waits on the wall clock, and [Clock_ns.every] below runs on it. *)
let trade ?(auto = true) (t : t) : _ Venue.Trade.t =
  let reader, writer = Pipe.create () in
  t.updates <- Some writer;
  if auto then Clock_ns.every (Time_ns.Span.of_ms 100.0) (fun () -> pump t);
  {
    Venue.Trade.submit =
      (fun _permit r ->
        let s = submit_now t r in
        (match s with
        | Venue.Submission.Accepted o ->
            emit t { Venue.Update.event = "new"; order = o; fill = None; at = t.now () }
        | _ -> ());
        return s);
    cancel = (fun id -> return (Or_error.map (cancel_now t id) ~f:(emit t)));
    find_order = (fun c -> return (Ok (find_now t c)));
    open_orders = (fun () -> return (Ok (open_now t)));
    updates = reader;
  }
