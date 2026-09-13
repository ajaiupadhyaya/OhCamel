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

let col_date r i = Date.of_string (col_text r i)

let col_time r i =
  Option.value_exn ~message:"journal: unreadable time" (Desk_time.parse (col_text r i))

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
  ]

let open_ ~(path : string) : t Or_error.t =
  Or_error.try_with (fun () ->
      let t = { db = Sqlite3.db_open path; location = path; version = 0 } in
      Sqlite3.busy_timeout t.db 5_000;
      (* A second connection to the same file (sqlite3 .backup, a person
         reading it) waits up to five seconds instead of failing at once. WAL
         lets a reader and the one writer proceed together; an in-memory
         database has no file for either to mean anything. *)
      if not (String.equal path ":memory:") then (
        exec t ~what:"journal_mode" "PRAGMA journal_mode=WAL";
        exec t ~what:"synchronous" "PRAGMA synchronous=NORMAL");
      exec t ~what:"meta" (List.hd_exn schema);
      (match
         query t ~what:"schema version"
           "SELECT value FROM meta WHERE key = 'schema_version'" [] ~row:(fun r ->
             col_text r 0)
       with
      | [] -> ()
      | [ v ] when String.equal v (Int.to_string schema_version) -> ()
      | [ v ] ->
          ignore (Sqlite3.db_close t.db : bool);
          failwithf
            "journal: %s is at schema version %s and this build knows version %d; it \
             will not guess at a layout it has never seen"
            path v schema_version ()
      | _ ->
          ignore (Sqlite3.db_close t.db : bool);
          failwith "journal: meta holds more than one schema version");
      List.iter (List.tl_exn schema) ~f:(exec t ~what:"schema");
      run t ~what:"schema version"
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)"
        [ text (Int.to_string schema_version) ];
      t)

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
