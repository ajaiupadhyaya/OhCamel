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
   book is still the file's. *)

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
  mutable account : Venue.Account.t option;
  mutable positions : Venue.Position.t list;
  mutable unmanaged : Venue.Position.t list;
  mutable equity_gap : float option;
  mutable last_sync : Time_ns.t option;
  mutable last_error : string option;
  (* The session count, re-read only when the journal has been written since:
     this object rides on every frame, and a frame is not a reason to query. *)
  mutable sessions_seen : int * int;
}

let create ~graph ~journal ~venue ~spec ~on_change =
  {
    graph;
    journal;
    venue;
    spec;
    on_change;
    account = None;
    positions = [];
    unmanaged = [];
    equity_gap = None;
    last_sync = None;
    last_error = None;
    sessions_seen = (-1, 0);
  }

let venue_name t =
  match t.venue with Reads r -> r.Venue.Read.name | Unavailable { name; _ } -> name

let sync_with t ~account ~positions ~at =
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
      t.last_error <- None
  | Error e, _ | _, Error e -> t.last_error <- Some (Error.to_string_hum e));
  t.on_change ()

let sync t : unit Or_error.t Deferred.t =
  match t.venue with
  | Unavailable _ -> return (Ok ())
  | Reads read -> (
      let%bind account = read.Venue.Read.account () in
      let%map positions = read.Venue.Read.positions () in
      sync_with t ~account ~positions ~at:(Time_ns.now ());
      match (account, positions) with
      | Ok _, Ok _ -> Ok ()
      | Error e, _ | _, Error e -> Error e)

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
    let count = List.length (Journal.sessions t.journal) in
    t.sessions_seen <- (version, count);
    count

let summary_fields t =
  [
    ( "status",
      `String (match t.venue with Reads _ -> "enabled" | Unavailable _ -> "disabled") );
    ( "reason",
      match t.venue with Reads _ -> `Null | Unavailable { reason; _ } -> `String reason );
    ("venue", `String (venue_name t));
    ("trading", `Bool false);
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
    ("unmanaged", `Int (List.length t.unmanaged));
    ("sessions", `Int (sessions_count t));
    ("last_sync", jopt (fun at -> `String (Desk_time.rfc3339 at)) t.last_sync);
    ("last_error", jopt (fun e -> `String e) t.last_error);
  ]

let summary_json t : Yojson.Safe.t = `Assoc (summary_fields t)

let body_json t : Yojson.Safe.t =
  let snapshot_exposure = Graph.exposure_by_instrument t.graph in
  let sessions = Journal.sessions t.journal in
  let recent = List.drop sessions (Int.max 0 (List.length sessions - 30)) in
  let forecasts = Journal.forecasts t.journal in
  let last_date =
    List.last forecasts |> Option.map ~f:(fun f -> f.Journal.Forecast.date)
  in
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
            (List.filter_map forecasts ~f:(fun f ->
                 if Option.equal Date.equal (Some f.Journal.Forecast.date) last_date then
                   Some
                     (`Assoc
                        [
                          ("estimator", `String f.Journal.Forecast.estimator);
                          ("confidence", jnum f.Journal.Forecast.confidence);
                          ("var_fraction", jopt jnum f.Journal.Forecast.var_fraction);
                          ("var_notional", jopt jnum f.Journal.Forecast.var_notional);
                          ("es_notional", jopt jnum f.Journal.Forecast.es_notional);
                        ])
                 else None)) );
      ])

let extensions t : Server.extension list =
  [
    {
      Server.path = "/api/desk";
      purpose =
        "the desk: its venue, the account, positions held and unmanaged, recorded \
         sessions";
      handle =
        (fun server _request ->
          Server.respond_json server (Yojson.Safe.to_string (body_json t)));
    };
  ]
