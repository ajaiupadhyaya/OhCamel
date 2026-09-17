(* The desk, read side.

   What it holds is what the page needs to say about the account the book now
   comes from: which venue, whether the desk could reach it and why not, the
   account as last read, the names the venue holds that the book cannot, and
   how far the graph's equity is from the venue's. What it does is one thing:
   sync, which reads the venue and writes the book (Book_sync).

   A FAILED READ CHANGES NOTHING BUT THE ERROR. The last good account stands,
   the graph keeps its quantities, and last_error says what happened. Emptying
   the book because a request timed out would be the page reporting a flat
   book that is not flat.

   A DESK WITH NO VENUE is a state with a reason, printed. On the live host
   that is a trading key the paper host would refuse; the risk engine goes on
   running on its own credentials, and the page says in a sentence that the
   book is still the file's.

   THE FIRST READ APPLIED IS WHEN THE ACCOUNT ARRIVES. Until then the graph
   holds the book file's quantities and cash, so anything that belongs to the
   account -- the journal's equity trail, a session close -- waits for it:
   [on_first_sync] runs once, at that read, and [book_is_current] says whether
   the last one is recent enough to record a close from. *)

open Core
open Async
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Server = Ohcamel.Server
module Desk_spec = Ohcamel.Config.Book.Desk_spec

type venue = Reads of Venue.Read.t | Unavailable of { name : string; reason : string }

type t = {
  graph : Graph.t;
  journal : Journal.t;
  venue : venue;
  spec : Desk_spec.t;
  on_change : unit -> unit;
  on_first_sync : unit -> unit;
  mutable synced : bool;
  mutable account : Venue.Account.t option;
  mutable positions : Venue.Position.t list;
  mutable unmanaged : Venue.Position.t list;
  mutable equity_gap : float option;
  mutable last_sync : Time_ns.t option;
  mutable last_error : string option;
  (* Reads are numbered as they start. [applied_seq] is the number of the read
     the book came from; [raced_through] is the highest number taken before
     the desk's latest fill. Zero is "none". *)
  mutable reads_started : int;
  mutable applied_seq : int;
  mutable raced_through : int;
  (* The order manager, handed back once it exists (bin/main.ml builds it
     after the desk, since its fills re-read the account through this desk's
     [sync]). None until then, and for any desk this task's tests build
     without one -- the frame still owes an honest answer, not a crash. *)
  mutable oms : Oms.t option;
  (* The read [after_fill] starts: none out, one out, or one out and one more
     owed, because a fill landed while it was out. *)
  mutable fill_read : [ `Idle | `Running | `Owed ];
  (* The session count, re-read only when the journal has been written since:
     this object rides on every frame, and a frame is not a reason to query. *)
  mutable sessions_seen : int * int;
}

let create ~graph ~journal ~venue ~spec ~on_change ~on_first_sync =
  {
    graph;
    journal;
    venue;
    spec;
    on_change;
    on_first_sync;
    synced = false;
    account = None;
    positions = [];
    unmanaged = [];
    equity_gap = None;
    last_sync = None;
    last_error = None;
    reads_started = 0;
    applied_seq = 0;
    raced_through = 0;
    oms = None;
    fill_read = `Idle;
    sessions_seen = (-1, 0);
  }

(* The order manager is made after the desk -- its fills re-read the account
   through [sync] -- and handed back here, so the frame can say what the desk
   can do. *)
let set_oms t oms = t.oms <- Some oms

let venue_name t =
  match t.venue with Reads r -> r.Venue.Read.name | Unavailable { name; _ } -> name

(* [last_sync] is when the read the book came from started (see [sync]). A desk
   whose last applied read is older than [within] -- the caller's sync
   interval, doubled -- has a sync that is failing or stalled, and a desk that
   never applied one still holds the book file's; a session close records
   neither. *)
let book_is_current t ~now ~within =
  match t.last_sync with
  | None -> false
  | Some at -> Time_ns.Span.( <= ) (Time_ns.diff now at) within

(* A read's place in the order this desk started its reads. The order is by
   number and not by [at], because the wall clock can step backwards -- an
   NTP correction, a resumed VM -- and a guard on time would then refuse every
   read until the clock passed the old stamp, silently, with the book frozen
   (A1's ledger, line 128). *)
let start_read t =
  t.reads_started <- t.reads_started + 1;
  t.reads_started

(* The desk's own fill has moved the graph's book: the order manager applied
   it and set the venue's position_qty, so positions follow fills. A read
   that started before the fill carries the account from before it -- cash
   without the trade, or quantities with it beside cash without -- and
   applying it would move the book backwards by the fill's notional until the
   next read (A1's final review, M4). So every read already started is
   refused, and the read [after_fill] starts is the correction. *)
let fill_applied t = t.raced_through <- t.reads_started

let sync_with t ~account ~positions ~at ~seq =
  if seq <= t.applied_seq || seq <= t.raced_through then
    (* Older than what the book already holds: a read started before the last
       applied one, or before one of the desk's own fills. *)
    ()
  else (
    (match (account, positions) with
    | Ok account, Ok positions ->
        let plan = Book_sync.plan ~universe:(Graph.symbols t.graph) ~positions ~account in
        Book_sync.apply t.graph plan;
        t.account <- Some account;
        (* Design §3.3's own definition of "managed": a name in the book's
           universe. Read here rather than recovered from [plan.unmanaged] by
           subtraction -- that would agree with [plan] only because
           [Book_sync.plan] happens to build [unmanaged] as an untransformed
           filter of [positions], which is Book_sync's implementation and not a
           fact desk.ml is entitled to lean on. [t.unmanaged] still comes from
           the plan, because Book_sync owns that decision; the two now agree by
           definition, not by construction. *)
        let universe = Symbol.Set.of_list (Graph.symbols t.graph) in
        t.unmanaged <- plan.Book_sync.Plan.unmanaged;
        t.positions <-
          List.filter positions ~f:(fun p -> Set.mem universe p.Venue.Position.symbol);
        t.equity_gap <-
          Some
            (Notional.to_float (Graph.equity t.graph)
            -. Notional.to_float account.Venue.Account.equity);
        t.last_sync <- Some at;
        t.applied_seq <- seq;
        t.last_error <- None;
        (* The first moment the graph's book is the account's, and so the
           first moment the journal's closes -- the account's equity -- can go
           back into the trail beside it. [Book_sync.apply] has already
           stabilized on the account's book, so no stabilize sees those closes
           beside the file's. Once only: a second restore would replace the
           trail again and drop every mark made since.

           A restore that raises is caught neither here nor by any of the
           three callers of [sync]: bin/main.ml's first sync, [sync_forever],
           and [after_fill]. The last is the order manager's, after a fill and
           at the end of a reconciliation that found open orders, so the
           startup reconciliation reaches it whenever the first sync timed
           out. The exception leaves [sync] through the monitor of whoever
           started the read. From the first two that is the process's own, and
           it ends the process, as the startup restore it replaced did. From
           [after_fill] it is the monitor of the manager's job, which has
           finished by the time the venue answers; the manager's sequencer
           logs an exception that arrives after its job, so the process goes
           on with the trail unrestored. Either way there is no second
           restore: the flag is set first, so no read after this one tries
           again. *)
        if not t.synced then (
          t.synced <- true;
          t.on_first_sync ())
    | Error e, _ | _, Error e -> t.last_error <- Some (Error.to_string_hum e));
    t.on_change ())

let sync t : unit Or_error.t Deferred.t =
  match t.venue with
  | Unavailable _ -> return (Ok ())
  | Reads read -> (
      (* Numbered and stamped when the read starts, not when it answers: two
         syncs can overlap, and [sync_with] orders them by the number. *)
      let seq = start_read t in
      let started = Time_ns.now () in
      let%bind account = read.Venue.Read.account () in
      let%map positions = read.Venue.Read.positions () in
      sync_with t ~account ~positions ~at:started ~seq;
      match (account, positions) with
      | Ok _, Ok _ -> Ok ()
      | Error e, _ | _, Error e -> Error e)

(* What the order manager calls once one of the desk's fills is in the graph:
   the reads in flight are refused, and a new one starts, so the account
   catches up with the fill -- the sync corrects the book the fills drive.
   Not awaited: the manager's one-at-a-time jobs must not wait on the
   venue's account.

   One such read at a time, and one owed. A read is two requests, the account
   and the positions, and Alpaca allows 200 a minute for everything the desk
   sends. A rebalance that fills thirty names, or a stream of partial fills,
   would otherwise spend that budget on reads the next fill refuses, and it is
   the budget a submit, a cancel and a kill's cancels need; a 429 on a submit
   is a refusal. A fill that lands while the read is out refuses it, as any
   fill does, and is owed a read that starts when that one answers. So the
   read that finally applies started after the last fill. *)
let rec after_fill t =
  fill_applied t;
  match t.fill_read with
  | `Running | `Owed -> t.fill_read <- `Owed
  | `Idle ->
      t.fill_read <- `Running;
      upon (sync t) (fun (_ : unit Or_error.t) ->
          match t.fill_read with
          | `Owed ->
              t.fill_read <- `Idle;
              after_fill t
          | _ -> t.fill_read <- `Idle)

let rec sync_forever t ~every ~on_event =
  let%bind () = Clock_ns.after every in
  let%bind result = sync t in
  (match result with
  | Ok () -> ()
  | Error e -> on_event ("desk      sync failed: " ^ Error.to_string_hum e));
  sync_forever t ~every ~on_event

let jnum x = if Float.is_finite x then `Float x else `Null
let jopt f = function None -> `Null | Some x -> f x
let money n = jnum (Notional.to_float n)

let sessions_count t =
  let version = Journal.version t.journal in
  let seen_version, count = t.sessions_seen in
  if version = seen_version then count
  else
    let count = Journal.session_count t.journal in
    t.sessions_seen <- (version, count);
    count

let summary_fields t =
  [
    ( "status",
      `String (match t.venue with Reads _ -> "enabled" | Unavailable _ -> "disabled") );
    ( "reason",
      match t.venue with Reads _ -> `Null | Unavailable { reason; _ } -> `String reason );
    ("venue", `String (venue_name t));
    ("trading", `Bool (match t.oms with Some o -> Oms.can_trade o | None -> false));
    ( "kill_switch",
      match t.oms with
      | Some o -> `String (Halt.State.name (Halt.state (Oms.halt o)))
      | None -> `Null );
    ( "tickets",
      `String
        (match t.oms with
        | Some o when Oms.accepts_tickets o -> "accepted"
        | Some _ -> "preview only"
        | None -> "none") );
    ( "journal",
      `String
        (if String.equal (Journal.location t.journal) ":memory:" then "memory" else "file")
    );
    ("version", `Int (Journal.version t.journal));
    ("equity", jopt (fun a -> money a.Venue.Account.equity) t.account);
    ("cash", jopt (fun a -> money a.Venue.Account.cash) t.account);
    ( "session_pnl",
      match t.account with
      | Some { Venue.Account.equity; last_equity = Some last; _ } ->
          jnum (Notional.to_float equity -. Notional.to_float last)
      | _ -> `Null );
    ("open_orders", `Int (match t.oms with Some o -> Oms.open_count o | None -> 0));
    ("unmanaged", `Int (List.length t.unmanaged));
    ("sessions", `Int (sessions_count t));
    ("last_sync", jopt (fun at -> `String (Desk_time.rfc3339 at)) t.last_sync);
    ("last_error", jopt (fun e -> `String e) t.last_error);
  ]

let summary_json t : Yojson.Safe.t = `Assoc (summary_fields t)

let body_json t : Yojson.Safe.t =
  let snapshot_exposure = Graph.exposure_by_instrument t.graph in
  let recent = Journal.recent_sessions t.journal ~limit:30 in
  let forecasts = Journal.latest_forecasts t.journal in
  `Assoc
    (summary_fields t
    @ [
        ( "account",
          jopt
            (fun (a : Venue.Account.t) ->
              `Assoc
                [
                  ("equity", money a.equity);
                  ("cash", money a.cash);
                  ("buying_power", money a.buying_power);
                  ("last_equity", jopt money a.last_equity);
                  ("status", `String a.status);
                  ("trading_blocked", `Bool a.trading_blocked);
                  ("shorting_enabled", `Bool a.shorting_enabled);
                ])
            t.account );
        ( "positions",
          `List
            (List.map (Graph.symbols t.graph) ~f:(fun symbol ->
                 let venue =
                   List.find t.positions ~f:(fun p ->
                       Symbol.equal p.Venue.Position.symbol symbol)
                 in
                 `Assoc
                   [
                     ("symbol", `String (Symbol.to_string symbol));
                     ( "venue_qty",
                       jopt (fun p -> jnum (Qty.to_float p.Venue.Position.qty)) venue );
                     ("graph_qty", jnum (Qty.to_float (Graph.qty t.graph symbol)));
                     ( "venue_market_value",
                       jopt money
                         (Option.bind venue ~f:(fun p -> p.Venue.Position.market_value))
                     );
                     ("graph_exposure", jopt money (Map.find snapshot_exposure symbol));
                   ])) );
        ( "unmanaged_positions",
          `List
            (List.map t.unmanaged ~f:(fun p ->
                 `Assoc
                   [
                     ("symbol", `String (Symbol.to_string p.Venue.Position.symbol));
                     ("asset_class", `String p.Venue.Position.asset_class);
                     ("qty", jnum (Qty.to_float p.Venue.Position.qty));
                     ("market_value", jopt money p.Venue.Position.market_value);
                   ])) );
        ("equity_gap", jopt jnum t.equity_gap);
        ( "recent_sessions",
          `List
            (List.map recent ~f:(fun s ->
                 `Assoc
                   [
                     ("date", `String (Date.to_string s.Journal.Session.date));
                     ("equity_close", jnum s.Journal.Session.equity_close);
                     ("gross_close", jnum s.Journal.Session.gross_close);
                     ("net_close", jnum s.Journal.Session.net_close);
                   ])) );
        ( "last_forecasts",
          `List
            (List.map forecasts ~f:(fun f ->
                 `Assoc
                   [
                     ("estimator", `String f.Journal.Forecast.estimator);
                     ("confidence", jnum f.Journal.Forecast.confidence);
                     ("var_fraction", jopt jnum f.Journal.Forecast.var_fraction);
                     ("var_notional", jopt jnum f.Journal.Forecast.var_notional);
                     ("es_notional", jopt jnum f.Journal.Forecast.es_notional);
                   ])) );
        ( "switch",
          match t.oms with
          | Some o -> Halt.to_json (Oms.halt o) ~now:(Time_ns.now ())
          | None -> `Null );
        ( "orders",
          `Assoc
            [
              ("open", `List (List.map (Journal.open_orders t.journal) ~f:Oms.order_json));
              ( "recent",
                `List
                  (List.map (Journal.recent_orders t.journal ~limit:20) ~f:Oms.order_json)
              );
            ] );
        ( "fills",
          match t.oms with
          | Some o -> Oms.fills_json o (Journal.recent_fills t.journal ~limit:20)
          | None -> `List [] );
        ("tca", match t.oms with Some o -> Oms.tca_json o | None -> `Null);
      ])

let extensions t : Server.extension list =
  [
    {
      Server.path = "/api/desk";
      purpose =
        "the desk: its venue, the account, positions held and unmanaged, orders, fills \
         and their costs, the switch, recorded sessions";
      handle =
        (fun server _request ->
          Server.respond_json server (Yojson.Safe.to_string (body_json t)));
    };
  ]
