(* The journal: SQLite, one file, and the only persistence in the project.

   Invariant 13 says persistence is this and only this, and the README spent a
   section arguing that adding persistence had to be argued. The argument is
   the design's §2.3 and §3.5: an order manager without a record forgets the
   orders it sent, and a risk engine that validates itself needs its own
   forecasts from yesterday. This module is the whole of the answer.

   WRITES ARE SYNCHRONOUS, ON THE CALLER'S THREAD. A one-row insert in WAL mode
   costs tens of microseconds. Moving it to a thread would put the write and
   the network request that follows it into a race, and invariant 10 -- the
   journal before the wire -- is precisely the promise that there is no race.

   A DATABASE ERROR RAISES. The order manager must not submit an order it
   could not record, so an exception is what it wants; the session close would
   rather log and carry on, and wraps its calls in Or_error.try_with. Returning
   an Or_error from every write would put the choice in the wrong place.

   The orders, order_events, fills and signals tables are created here, in
   schema version 1, and written by later phases: a second migration a week
   from now would be the same schema with a version bump and a code path that
   runs once. *)

open Core
open Ohcamel.Types

(* Core deprecated [Time_ns.sexp_of_t] to a value that does not typecheck (see
   lib/types.ml's [Time] module and desk/order.ml for the same fix):
   [Alternate_sexp] is the same [t] with a real [sexp_of_t], which is what
   [Session.t] and [Alert.t] below derive against. *)
module Time_ns = Time_ns.Alternate_sexp
module Rc = Sqlite3.Rc
module Data = Sqlite3.Data

let schema_version = 1

type t = { db : Sqlite3.db; location : string; mutable version : int }

let location t = t.location
let version t = t.version

let failed t ~what rc =
  failwithf "journal: %s failed: %s (%s)" what (Rc.to_string rc) (Sqlite3.errmsg t.db) ()

let exec t ~what sql =
  match Sqlite3.exec t.db sql with Rc.OK -> () | rc -> failed t ~what rc

let with_stmt t ~what sql params ~f =
  let stmt = Sqlite3.prepare t.db sql in
  Exn.protect
    ~finally:(fun () -> ignore (Sqlite3.finalize stmt : Rc.t))
    ~f:(fun () ->
      List.iteri params ~f:(fun i d ->
          match Sqlite3.bind stmt (i + 1) d with Rc.OK -> () | rc -> failed t ~what rc);
      f stmt)

let run t ~what sql params =
  with_stmt t ~what sql params ~f:(fun stmt ->
      match Sqlite3.step stmt with Rc.DONE -> () | rc -> failed t ~what rc)

let query t ~what sql params ~(row : Data.t array -> 'a) : 'a list =
  with_stmt t ~what sql params ~f:(fun stmt ->
      match Sqlite3.fold stmt ~init:[] ~f:(fun acc r -> row r :: acc) with
      | Rc.DONE, acc -> List.rev acc
      | rc, _ -> failed t ~what rc)

(* One transaction, one write on the counter. The page asks for /api/desk when
   the counter moves; three marks written together are one change to it.

   A COMMIT can still fail: a deferred foreign key is checked here, not at
   the statement that will violate it, so this is a second place -- besides
   [f] raising -- that a transaction ends badly. SQLite's own rule for a
   failed COMMIT is that the transaction is left open; the caller rolls it
   back, not SQLite. Skip that and the connection is still inside this
   transaction when the next write's BEGIN IMMEDIATE runs, which fails with
   "cannot start a transaction within a transaction" -- one bad write taking
   down every write after it. *)
let write t ~what (f : unit -> unit) =
  exec t ~what:(what ^ ": begin") "BEGIN IMMEDIATE";
  (match f () with
  | () -> (
      match Sqlite3.exec t.db "COMMIT" with
      | Rc.OK -> ()
      | rc ->
          ignore (Sqlite3.exec t.db "ROLLBACK" : Rc.t);
          failed t ~what:(what ^ ": commit") rc)
  | exception e ->
      ignore (Sqlite3.exec t.db "ROLLBACK" : Rc.t);
      raise e);
  t.version <- t.version + 1

let text s = Data.TEXT s
let real x = Data.FLOAT x
let opt_real = Data.opt_float
let date d = text (Date.to_string d)
let time x = text (Desk_time.rfc3339 x)
let col_text (r : Data.t array) i = Data.to_string_coerce r.(i)

let col_real (r : Data.t array) i =
  Option.value_exn ~message:"journal: expected a number" (Data.to_float r.(i))

(* NULL is None. A REAL column that SQLite handed back as an INT (a whole
   number written as 1107.0 can be stored that way) is still a number. *)
let col_opt_real (r : Data.t array) i =
  match r.(i) with
  | Data.NULL | Data.NONE -> None
  | Data.FLOAT x -> Some x
  | Data.INT n -> Some (Int64.to_float n)
  | other -> Data.to_float other

(* NULL is None. [col_text]'s NULL-to-"" coercion cannot tell a venue id that
   has not arrived yet from an order whose venue id is, somehow, the empty
   string -- this one distinguishes them. *)
let col_opt_text (r : Data.t array) i =
  match r.(i) with
  | Data.NULL | Data.NONE -> None
  | other -> Some (Data.to_string_coerce other)

let col_date r i = Date.of_string (col_text r i)

let col_time r i =
  Option.value_exn ~message:"journal: unreadable time" (Desk_time.parse (col_text r i))

(* The states an order does not leave, as SQL: [open_orders] asks for every
   order not in one, and the index that answers it is declared over the same
   words, because SQLite uses a partial index only for a query whose WHERE
   contains the index's own. One string, so the two cannot drift apart. *)
let terminal_states =
  "('rejected_pre_trade','filled','cancelled','expired','rejected_by_venue','failed')"

(* The indexes at the end are for /api/desk, which every open page asks for
   again after each journal write, and which anyone may ask for on the demo.
   Without them every order it loads scans the whole of fills and
   order_events, [recent_orders] sorts every order, and [open_orders] reads
   them all -- synchronously, on the scheduler that serves the page and runs
   the desk, and in the demo's in-memory journal, which gains an order every
   45 s for as long as the process lives. They change no table, so a journal
   written before them is still schema version 1: CREATE INDEX IF NOT EXISTS
   adds them the first time this build opens it, and is nothing on every open
   after. *)
let schema =
  [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)";
    "CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY, venue_order_id \
     TEXT, source TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT \
     NULL, kind TEXT NOT NULL, limit_price REAL, tif TEXT NOT NULL, state TEXT NOT NULL, \
     filled_qty REAL NOT NULL DEFAULT 0, avg_fill_price REAL, decision_price REAL NOT \
     NULL, arrival_bid REAL, arrival_ask REAL, verdict TEXT NOT NULL, created_at TEXT \
     NOT NULL, updated_at TEXT NOT NULL)";
    "CREATE TABLE IF NOT EXISTS order_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, \
     client_order_id TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL, state_after \
     TEXT NOT NULL, anomaly TEXT, detail TEXT)";
    "CREATE TABLE IF NOT EXISTS fills (execution_id TEXT PRIMARY KEY, client_order_id \
     TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price \
     REAL NOT NULL, at TEXT NOT NULL, position_qty REAL)";
    "CREATE TABLE IF NOT EXISTS sessions (date TEXT PRIMARY KEY, equity_close REAL NOT \
     NULL, cash_close REAL NOT NULL, gross_close REAL NOT NULL, net_close REAL NOT NULL, \
     recorded_at TEXT NOT NULL)";
    "CREATE TABLE IF NOT EXISTS marks (date TEXT NOT NULL, symbol TEXT NOT NULL, close \
     REAL NOT NULL, qty REAL NOT NULL, PRIMARY KEY (date, symbol))";
    "CREATE TABLE IF NOT EXISTS forecasts (date TEXT NOT NULL, estimator TEXT NOT NULL, \
     confidence REAL NOT NULL, var_fraction REAL, var_notional REAL, es_notional REAL, \
     PRIMARY KEY (date, estimator))";
    "CREATE TABLE IF NOT EXISTS signals (strategy TEXT NOT NULL, sequence INTEGER NOT \
     NULL, as_of TEXT NOT NULL, received_at TEXT NOT NULL, verdict TEXT NOT NULL, rule \
     TEXT, detail TEXT, document TEXT NOT NULL, PRIMARY KEY (strategy, sequence))";
    "CREATE TABLE IF NOT EXISTS alerts (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT \
     NOT NULL, kind TEXT NOT NULL, limit_name TEXT NOT NULL, line TEXT NOT NULL)";
    "CREATE INDEX IF NOT EXISTS order_events_by_order ON order_events (client_order_id, \
     seq)";
    "CREATE INDEX IF NOT EXISTS fills_by_order ON fills (client_order_id)";
    "CREATE INDEX IF NOT EXISTS orders_by_created ON orders (created_at, client_order_id)";
    "CREATE INDEX IF NOT EXISTS orders_open ON orders (created_at, client_order_id) \
     WHERE state NOT IN " ^ terminal_states;
  ]

(* The refusal's decision alone, with no SQLite and no IO: [modes] is
   whatever PRAGMA journal_mode=WAL answered (zero, one, or -- if SQLite ever
   surprised us -- more than one row). Ok only for the single, unambiguous
   answer "wal", any case, since that is the one thing this build can trust
   the file to behave like. Pulled out of [set_up] so the refusal can be
   driven directly with an answer of our own choosing: this build's own test
   filesystem always enters WAL for real, so a case that only opened a real
   file could never tell a working refusal from a deleted one (a review's
   finding on this file's first version). *)
let wal_check ~path (modes : string list) : (unit, string) Result.t =
  match modes with
  | [ mode ] when String.equal (String.lowercase mode) "wal" -> Ok ()
  | modes ->
      Error
        (sprintf
           "journal: %s answered journal_mode %s, not wal; this journal runs only in WAL \
            mode"
           path
           (String.concat ~sep:"," modes))

(* Everything [open_] does once the handle exists. Each refusal raises, and
   [open_] closes the handle.

   A second connection to the same file (sqlite3 .backup, a person reading
   it) waits up to five seconds instead of failing at once. WAL lets a reader
   and the one writer proceed together; an in-memory database has no file for
   either to mean anything.

   SQLite answers PRAGMA journal_mode=WAL with the mode it actually set. On a
   filesystem without shared memory it stays in its rollback journal and says
   so. A journal that only asked would run on unnoticed, and a .backup reader
   could then block the one writer (A1's final review, M2). *)
let set_up t ~path =
  Sqlite3.busy_timeout t.db 5_000;
  if not (String.equal path ":memory:") then (
    (match
       wal_check ~path
         (query t ~what:"journal_mode" "PRAGMA journal_mode=WAL" [] ~row:(fun r ->
              col_text r 0))
     with
    | Ok () -> ()
    | Error msg -> failwith msg);
    exec t ~what:"synchronous" "PRAGMA synchronous=NORMAL");
  exec t ~what:"meta" (List.hd_exn schema);
  (match
     query t ~what:"schema version" "SELECT value FROM meta WHERE key = 'schema_version'"
       [] ~row:(fun r -> col_text r 0)
   with
  | [] -> ()
  | [ v ] when String.equal v (Int.to_string schema_version) -> ()
  | [ v ] ->
      failwithf
        "journal: %s is at schema version %s and this build knows version %d; it will \
         not guess at a layout it has never seen"
        path v schema_version ()
  | _ -> failwith "journal: meta holds more than one schema version");
  List.iter (List.tl_exn schema) ~f:(exec t ~what:"schema");
  run t ~what:"schema version"
    "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)"
    [ text (Int.to_string schema_version) ]

(* A refusal after the handle opened closes it, once, here. Before, only the
   two schema-version refusals did, and a failed pragma, a file that is not a
   database, or a failed insert left the handle open behind the error (A1's
   final review, M1): a caller that tried again would leak one per try. The
   schema-version branches no longer close the handle themselves, so it is
   never closed twice. *)
let open_ ~(path : string) : t Or_error.t =
  Or_error.try_with (fun () ->
      let t = { db = Sqlite3.db_open path; location = path; version = 0 } in
      match set_up t ~path with
      | () -> t
      | exception e ->
          ignore (Sqlite3.db_close t.db : bool);
          raise e)

let close t = ignore (Sqlite3.db_close t.db : bool)

module Session = struct
  type t = {
    date : Date.t;
    equity_close : float;
    cash_close : float;
    gross_close : float;
    net_close : float;
    recorded_at : Time_ns.t;
  }
  [@@deriving sexp_of, compare, equal]
end

(* INSERT OR REPLACE: a close that is recorded, crashes, and is recorded again
   on restart is one session with the later numbers, not two. *)
let record_session t (s : Session.t) =
  write t ~what:"session" (fun () ->
      run t ~what:"session"
        "INSERT OR REPLACE INTO sessions (date, equity_close, cash_close, gross_close, \
         net_close, recorded_at) VALUES (?, ?, ?, ?, ?, ?)"
        [
          date s.date;
          real s.equity_close;
          real s.cash_close;
          real s.gross_close;
          real s.net_close;
          time s.recorded_at;
        ])

let session_of_row r =
  {
    Session.date = col_date r 0;
    equity_close = col_real r 1;
    cash_close = col_real r 2;
    gross_close = col_real r 3;
    net_close = col_real r 4;
    recorded_at = col_time r 5;
  }

let session_columns =
  "date, equity_close, cash_close, gross_close, net_close, recorded_at"

let sessions t =
  query t ~what:"sessions"
    ("SELECT " ^ session_columns ^ " FROM sessions ORDER BY date")
    [] ~row:session_of_row

(* The page's count and its last thirty sessions are asked of SQLite rather
   than read out of the whole table. The demo host closes a session every five
   minutes, 288 a day, and /api/desk answers anyone who asks. *)
let session_count t =
  match
    query t ~what:"session count" "SELECT COUNT(*) FROM sessions" [] ~row:(fun r ->
        Data.to_int_exn r.(0))
  with
  | [ n ] -> n
  | _ -> failwith "journal: COUNT(*) did not answer with one row"

(* Newest first from SQLite, so LIMIT keeps the newest; oldest first to the
   caller, the order [sessions] answers in. *)
let recent_sessions t ~limit =
  List.rev
    (query t ~what:"recent sessions"
       ("SELECT " ^ session_columns ^ " FROM sessions ORDER BY date DESC LIMIT ?")
       [ Data.INT (Int64.of_int limit) ]
       ~row:session_of_row)

let session t d =
  List.hd
    (query t ~what:"session"
       ("SELECT " ^ session_columns ^ " FROM sessions WHERE date = ?")
       [ date d ]
       ~row:session_of_row)

module Mark = struct
  type t = { date : Date.t; symbol : Symbol.t; close : float; qty : float }
  [@@deriving sexp_of, compare, equal]
end

let record_marks t (marks : Mark.t list) =
  write t ~what:"marks" (fun () ->
      List.iter marks ~f:(fun (m : Mark.t) ->
          run t ~what:"mark"
            "INSERT OR REPLACE INTO marks (date, symbol, close, qty) VALUES (?, ?, ?, ?)"
            [ date m.date; text (Symbol.to_string m.symbol); real m.close; real m.qty ]))

let marks t d =
  query t ~what:"marks"
    "SELECT date, symbol, close, qty FROM marks WHERE date = ? ORDER BY symbol"
    [ date d ]
    ~row:(fun r ->
      {
        Mark.date = col_date r 0;
        symbol = Symbol.of_string (col_text r 1);
        close = col_real r 2;
        qty = col_real r 3;
      })

module Forecast = struct
  type t = {
    date : Date.t;
    estimator : string;
    confidence : float;
    var_fraction : float option;
    var_notional : float option;
    es_notional : float option;
  }
  [@@deriving sexp_of, compare, equal]
end

let record_forecasts t (fs : Forecast.t list) =
  write t ~what:"forecasts" (fun () ->
      List.iter fs ~f:(fun (f : Forecast.t) ->
          run t ~what:"forecast"
            "INSERT OR REPLACE INTO forecasts (date, estimator, confidence, \
             var_fraction, var_notional, es_notional) VALUES (?, ?, ?, ?, ?, ?)"
            [
              date f.date;
              text f.estimator;
              real f.confidence;
              opt_real f.var_fraction;
              opt_real f.var_notional;
              opt_real f.es_notional;
            ]))

let forecast_columns =
  "date, estimator, confidence, var_fraction, var_notional, es_notional"

let forecast_of_row r =
  {
    Forecast.date = col_date r 0;
    estimator = col_text r 1;
    confidence = col_real r 2;
    var_fraction = col_opt_real r 3;
    var_notional = col_opt_real r 4;
    es_notional = col_opt_real r 5;
  }

let forecasts t =
  query t ~what:"forecasts"
    ("SELECT " ^ forecast_columns ^ " FROM forecasts ORDER BY date, estimator")
    [] ~row:forecast_of_row

(* The latest date's forecasts only: three rows a session, and the page shows
   one session's. Dates are YYYY-MM-DD text, so the greatest is the latest. *)
let latest_forecasts t =
  query t ~what:"latest forecasts"
    ("SELECT " ^ forecast_columns
   ^ " FROM forecasts WHERE date = (SELECT MAX(date) FROM forecasts) ORDER BY estimator")
    [] ~row:forecast_of_row

module Alert = struct
  type t = { at : Time_ns.t; kind : string; limit_name : string; line : string }
  [@@deriving sexp_of, compare, equal]
end

let record_alert t (a : Alert.t) =
  write t ~what:"alert" (fun () ->
      run t ~what:"alert"
        "INSERT INTO alerts (at, kind, limit_name, line) VALUES (?, ?, ?, ?)"
        [ time a.at; text a.kind; text a.limit_name; text a.line ])

let recent_alerts t ~limit =
  query t ~what:"alerts"
    "SELECT at, kind, limit_name, line FROM alerts ORDER BY seq DESC LIMIT ?"
    [ Data.INT (Int64.of_int limit) ]
    ~row:(fun r ->
      {
        Alert.at = col_time r 0;
        kind = col_text r 1;
        limit_name = col_text r 2;
        line = col_text r 3;
      })

(* Orders, their events and their fills: A2's addition to the record.

   [Order_row.t] and [Fill_row.t] carry [Order.t] and [Order.Fill.t] whole
   rather than duplicating their shape -- the state machine already knows how
   to hold an order, and a second definition of it here would be a second
   thing to keep in sync with the first. *)

module Order_row = struct
  type t = {
    order : Order.t;
    source : string;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
    verdict : Yojson.Safe.t;
    created_at : Time_ns.t;
    updated_at : Time_ns.t;
  }
end

module Fill_row = struct
  type t = {
    client_order_id : Ids.Client_order_id.t;
    symbol : Symbol.t;
    side : Order.Side.t;
    fill : Order.Fill.t;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
  }
end

let client_order_id_of_text s =
  Option.value_exn ~message:"journal: unparsable client order id"
    (Ids.Client_order_id.of_string s)

let side_of_text s =
  Option.value_exn ~message:"journal: unknown side" (Order.Side.of_string s)

let state_of_text s =
  Option.value_exn ~message:"journal: unknown order state" (Order.State.of_string s)

let arrival_of_row r ~bid ~ask =
  match (col_opt_real r bid, col_opt_real r ask) with
  | Some b, Some a -> Some (Price.of_float b, Price.of_float a)
  | _ -> None

(* The JSON object order_events.detail holds: the order's OWN reason (never
   the event's -- an illegal event that carried a reason must not become the
   reason a restart reads back), the venue id an Acknowledged or Found
   carried, or a fill's execution id. None -- written as SQL NULL, not "{}"
   -- when none of the three applies, so [load_order]'s reason lookup can
   filter on the text "reason" appearing at all. *)
let order_event_detail (o : Order.t) (event : Order.Event.t) : string option =
  let fields =
    match o.Order.reason with Some r -> [ ("reason", `String r) ] | None -> []
  in
  let fields =
    match event with
    | Order.Event.Acknowledged id | Order.Event.Found id ->
        ("venue_order_id", `String id) :: fields
    | Order.Event.Venue_fill f ->
        ("execution_id", `String f.Order.Fill.execution_id) :: fields
    | _ -> fields
  in
  match fields with [] -> None | fields -> Some (Yojson.Safe.to_string (`Assoc fields))

let reason_of_detail (detail : string) : string option =
  match Yojson.Safe.from_string detail with
  | `Assoc fields -> (
      match List.Assoc.find fields "reason" ~equal:String.equal with
      | Some (`String r) -> Some r
      | _ -> None)
  | _ -> None

(* One transaction: the row -- pending_submit, no venue id, no fills -- and
   the [created] event. The order manager calls this before the request that
   submits the order is sent: invariant 10, the journal before the wire. *)
let insert_order t (o : Order.t) ~source ~decision_price ~arrival ~verdict ~at =
  write t ~what:"insert order" (fun () ->
      let request = o.Order.request in
      let cid = Ids.Client_order_id.to_string request.Order.Request.client_order_id in
      let bid, ask =
        match arrival with
        | None -> (Data.NULL, Data.NULL)
        | Some (b, a) -> (real (Price.to_float b), real (Price.to_float a))
      in
      run t ~what:"order"
        "INSERT INTO orders (client_order_id, venue_order_id, source, symbol, side, qty, \
         kind, limit_price, tif, state, filled_qty, avg_fill_price, decision_price, \
         arrival_bid, arrival_ask, verdict, created_at, updated_at) VALUES (?, NULL, ?, \
         ?, ?, ?, ?, ?, 'day', ?, 0, NULL, ?, ?, ?, ?, ?, ?)"
        [
          text cid;
          text source;
          text (Symbol.to_string request.Order.Request.symbol);
          text (Order.Side.to_string request.Order.Request.side);
          real (Float.of_int request.Order.Request.qty);
          text (Order.Kind.to_string request.Order.Request.kind);
          opt_real
            (Option.map
               (Order.Kind.limit_price request.Order.Request.kind)
               ~f:Price.to_float);
          text (Order.State.to_string o.Order.state);
          real (Price.to_float decision_price);
          bid;
          ask;
          text (Yojson.Safe.to_string verdict);
          time at;
          time at;
        ];
      run t ~what:"order created event"
        "INSERT INTO order_events (client_order_id, at, event, state_after, anomaly, \
         detail) VALUES (?, ?, 'created', ?, NULL, NULL)"
        [ text cid; time at; text (Order.State.to_string o.Order.state) ])

(* One transaction: the row's state, venue id, filled quantity and average
   price -- a cache for a reader that would rather not pay [load_order]'s
   three queries -- and one order_events row. [load_order] itself never reads
   filled_qty or avg_fill_price back: both are rebuilt from the fills table,
   which is the ledger the cache is a cache of. *)
let update_order t (o : Order.t) ~event ~anomaly ~at =
  write t ~what:"update order" (fun () ->
      let cid =
        Ids.Client_order_id.to_string o.Order.request.Order.Request.client_order_id
      in
      run t ~what:"order"
        "UPDATE orders SET venue_order_id = ?, state = ?, filled_qty = ?, avg_fill_price \
         = ?, updated_at = ? WHERE client_order_id = ?"
        [
          (match o.Order.venue_order_id with None -> Data.NULL | Some id -> text id);
          text (Order.State.to_string o.Order.state);
          real o.Order.filled_qty;
          opt_real (Order.avg_fill_price o);
          time at;
          text cid;
        ];
      run t ~what:"order event"
        "INSERT INTO order_events (client_order_id, at, event, state_after, anomaly, \
         detail) VALUES (?, ?, ?, ?, ?, ?)"
        [
          text cid;
          time at;
          text (Order.Event.name event);
          text (Order.State.to_string o.Order.state);
          (match anomaly with
          | None -> Data.NULL
          | Some a -> text (Order.Anomaly.to_string a));
          (match order_event_detail o event with None -> Data.NULL | Some s -> text s);
        ])

(* INSERT OR IGNORE: true when the execution id is new. [Sqlite3.changes]
   answers for the statement just stepped, whether or not the surrounding
   transaction has committed yet -- [run] has already driven it to DONE. *)
let record_fill t (o : Order.t) (f : Order.Fill.t) : bool =
  let inserted = ref false in
  write t ~what:"fill" (fun () ->
      run t ~what:"fill"
        "INSERT OR IGNORE INTO fills (execution_id, client_order_id, symbol, side, qty, \
         price, at, position_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        [
          text f.Order.Fill.execution_id;
          text
            (Ids.Client_order_id.to_string o.Order.request.Order.Request.client_order_id);
          text (Symbol.to_string o.Order.request.Order.Request.symbol);
          text (Order.Side.to_string o.Order.request.Order.Request.side);
          real f.Order.Fill.qty;
          real (Price.to_float f.Order.Fill.price);
          time f.Order.Fill.at;
          opt_real f.Order.Fill.position_qty;
        ];
      inserted := Sqlite3.changes t.db = 1);
  !inserted

let fill_of_fills_row r =
  {
    Order.Fill.execution_id = col_text r 0;
    qty = col_real r 1;
    price = Price.of_float (col_real r 2);
    at = col_time r 3;
    position_qty = col_opt_real r 4;
  }

(* Rebuilt, not looked up: [state] comes from the row, but [filled_qty],
   [filled_notional] and [execution_ids] are summed from the fills table
   itself, and [reason] from the latest event whose detail carries one. A
   restart that trusted the row's own filled_qty/avg_fill_price would be
   trusting update_order's cache instead of the ledger it caches. *)
let load_order t (id : Ids.Client_order_id.t) : Order_row.t option =
  let cid = Ids.Client_order_id.to_string id in
  match
    query t ~what:"order"
      "SELECT client_order_id, venue_order_id, source, symbol, side, qty, kind, \
       limit_price, state, decision_price, arrival_bid, arrival_ask, verdict, \
       created_at, updated_at FROM orders WHERE client_order_id = ?"
      [ text cid ]
      ~row:Fn.id
  with
  | [] -> None
  | row :: _ ->
      let symbol = Symbol.of_string (col_text row 3) in
      let side = side_of_text (col_text row 4) in
      let qty = Float.iround_nearest_exn (col_real row 5) in
      let kind_text = col_text row 6 in
      let kind =
        if String.equal kind_text "limit" then
          Order.Kind.Limit
            (Price.of_float
               (Option.value_exn ~message:"journal: a limit order with no limit price"
                  (col_opt_real row 7)))
        else Order.Kind.Market
      in
      let fills =
        query t ~what:"order fills"
          "SELECT execution_id, qty, price, at, position_qty FROM fills WHERE \
           client_order_id = ? ORDER BY at, execution_id"
          [ text cid ]
          ~row:fill_of_fills_row
      in
      let filled_qty = List.sum (module Float) fills ~f:(fun f -> f.Order.Fill.qty) in
      let filled_notional =
        List.sum
          (module Float)
          fills
          ~f:(fun f -> f.Order.Fill.qty *. Price.to_float f.Order.Fill.price)
      in
      let execution_ids =
        String.Set.of_list (List.map fills ~f:(fun f -> f.Order.Fill.execution_id))
      in
      let reason =
        match
          query t ~what:"order reason"
            "SELECT detail FROM order_events WHERE client_order_id = ? AND detail LIKE \
             '%\"reason\"%' ORDER BY seq DESC LIMIT 1"
            [ text cid ]
            ~row:(fun r -> col_text r 0)
        with
        | [] -> None
        | detail :: _ -> reason_of_detail detail
      in
      let order : Order.t =
        {
          Order.request =
            {
              Order.Request.client_order_id = client_order_id_of_text (col_text row 0);
              symbol;
              side;
              qty;
              kind;
            };
          state = state_of_text (col_text row 8);
          venue_order_id = col_opt_text row 1;
          filled_qty;
          filled_notional;
          execution_ids;
          reason;
        }
      in
      Some
        {
          Order_row.order;
          source = col_text row 2;
          decision_price = Price.of_float (col_real row 9);
          arrival = arrival_of_row row ~bid:10 ~ask:11;
          verdict = Yojson.Safe.from_string (col_text row 12);
          created_at = col_time row 13;
          updated_at = col_time row 14;
        }

let orders_of_ids t ids =
  List.filter_map ids ~f:(fun cid -> load_order t (client_order_id_of_text cid))

(* Non-terminal states, oldest first: created_at, then client_order_id to
   break a tie between orders journaled at the same instant -- every test in
   this file that inserts more than one order does exactly that. *)
let open_orders t : Order_row.t list =
  query t ~what:"open orders"
    ("SELECT client_order_id FROM orders WHERE state NOT IN " ^ terminal_states
   ^ " ORDER BY created_at, client_order_id")
    []
    ~row:(fun r -> col_text r 0)
  |> orders_of_ids t

(* The events by which a venue says it has finished with an order that is
   not a fill: its cancel, its expiry, its rejection. For an order already
   failed the machine keeps the state and journals the event beside an
   anomaly (Order.apply), so the event row is the record that the venue
   finished it. The names are the machine's own, so they cannot drift from
   what [update_order] writes. *)
let finishing_events =
  sprintf "(%s)"
    (String.concat ~sep:","
       (List.map
          [
            Order.Event.Venue_cancelled;
            Order.Event.Venue_expired;
            Order.Event.Venue_rejected "";
          ] ~f:(fun e -> sprintf "'%s'" (Order.Event.name e))))

(* Failed orders created at or after the start of [since] -- every one, when
   None -- with no finishing event since: the ones a venue has not said it is
   done with. [since] is a SESSION'S DATE, not a recorded_at: the caller
   passes the latest recorded session's date, so that a close recorded late
   does not bound out an order still live in the session after it (oms.ml's
   [failed_still_working] says why). created_at is RFC 3339 UTC text
   (Desk_time.rfc3339) at a fixed width, so text order is time order, as
   [open_orders] already relies on, and comparing it against a bare
   "YYYY-MM-DD" bounds it at midnight UTC of that date: "2026-09-18" sorts
   before "2026-09-18T00:00:00.000000000Z" and everything after it, being a
   prefix of it, so [since] itself needs no time-of-day appended.

   Always binds a value -- the date's text, or "" (before any real
   created_at can sort) when there is no session yet -- so the predicate is
   sargable and SQLite can use orders_by_created's (created_at,
   client_order_id) index for both the range scan and this query's own
   ORDER BY, rather than "(? IS NULL OR created_at > ?)", which is not: SQLite
   cannot use an index range scan on a column also compared to NULL in the
   same OR, so every row was scanned and sorted (Task 3's re-review). *)
let unfinished_failed_orders t ~(since : Date.t option) : Order_row.t list =
  let since = match since with None -> "" | Some d -> Date.to_string d in
  query t ~what:"unfinished failed orders"
    ("SELECT client_order_id FROM orders WHERE state = 'failed' AND created_at >= ? AND \
      NOT EXISTS (SELECT 1 FROM order_events e WHERE e.client_order_id = \
      orders.client_order_id AND e.event IN " ^ finishing_events
   ^ ") ORDER BY created_at, client_order_id")
    [ text since ]
    ~row:(fun r -> col_text r 0)
  |> orders_of_ids t

let recent_orders t ~limit : Order_row.t list =
  query t ~what:"recent orders"
    "SELECT client_order_id FROM orders ORDER BY created_at DESC, client_order_id DESC \
     LIMIT ?"
    [ Data.INT (Int64.of_int limit) ]
    ~row:(fun r -> col_text r 0)
  |> orders_of_ids t

let recent_fills t ~limit : Fill_row.t list =
  query t ~what:"recent fills"
    "SELECT f.execution_id, f.client_order_id, f.symbol, f.side, f.qty, f.price, f.at, \
     f.position_qty, o.decision_price, o.arrival_bid, o.arrival_ask FROM fills f JOIN \
     orders o ON o.client_order_id = f.client_order_id ORDER BY f.at DESC, \
     f.execution_id DESC LIMIT ?"
    [ Data.INT (Int64.of_int limit) ]
    ~row:(fun r ->
      let fill : Order.Fill.t =
        {
          execution_id = col_text r 0;
          qty = col_real r 4;
          price = Price.of_float (col_real r 5);
          at = col_time r 6;
          position_qty = col_opt_real r 7;
        }
      in
      {
        Fill_row.client_order_id = client_order_id_of_text (col_text r 1);
        symbol = Symbol.of_string (col_text r 2);
        side = side_of_text (col_text r 3);
        fill;
        decision_price = Price.of_float (col_real r 8);
        arrival = arrival_of_row r ~bid:9 ~ask:10;
      })

module For_testing = struct
  let db t = t.db
  let write = write
  let run = run
  let wal_check = wal_check

  let journal_mode t =
    match
      query t ~what:"journal_mode" "PRAGMA journal_mode" [] ~row:(fun r -> col_text r 0)
    with
    | [ mode ] -> mode
    | _ -> failwith "journal: PRAGMA journal_mode did not answer with one row"
end
