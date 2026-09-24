(* The journal: the one thing in this project that writes to disk.

   What is checked is what persistence is FOR (spec §3.5): a record survives
   the process, a date recorded twice is one record, an unknown stays unknown,
   and a file this build does not understand is refused rather than guessed
   at. The file-backed cases use a temp file and remove it and its WAL
   siblings afterwards. *)

open Core
open Ohcamel.Types
module Journal = Ohcamel_desk.Journal

let date = Date.of_string
let at = Time_ns.of_string_with_utc_offset "2026-09-11T20:05:00Z"

let with_temp_path ~f =
  let path = Stdlib.Filename.temp_file "ohcamel-journal-" ".db" in
  let cleanup () =
    List.iter
      [ path; path ^ "-wal"; path ^ "-shm" ]
      ~f:(fun p -> if Stdlib.Sys.file_exists p then Stdlib.Sys.remove p)
  in
  Exn.protect ~f:(fun () -> f path) ~finally:cleanup

let open_exn path = Or_error.ok_exn (Journal.open_ ~path)

let session ?(equity = 100_000.0) d =
  {
    Journal.Session.date = date d;
    equity_close = equity;
    cash_close = 40_000.0;
    gross_close = 90_000.0;
    net_close = 60_000.0;
    recorded_at = at;
  }

let sessions_t =
  Alcotest.testable
    (fun ppf s -> Sexp.pp_hum ppf (Journal.Session.sexp_of_t s))
    Journal.Session.equal

(* The indexes the journal makes itself, by name: SQLite's own for a primary
   key or a UNIQUE column have no SQL, and are not what this reads. *)
let own_indexes j =
  let names = ref [] in
  ignore
    (Sqlite3.exec_not_null_no_headers (Journal.For_testing.db j)
       ~cb:(fun row -> names := row.(0) :: !names)
       "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY \
        name"
      : Sqlite3.Rc.t);
  List.rev !names

let journal_indexes =
  [
    "fills_by_at";
    "fills_by_order";
    "order_events_by_order";
    "orders_by_created";
    "orders_by_source";
    "orders_open";
    "signals_by_strategy";
  ]

(* A restart is also how a journal written before the indexes existed meets
   them: the file below has none when it is closed, as a file from that build
   would, and opening it again adds all seven without touching a row or the
   schema version. orders_by_source is Task 15's: a strategy's own orders, by
   the source signal:<slug>:<sequence> they carry. *)
let test_a_session_survives_a_restart () =
  with_temp_path ~f:(fun path ->
      let j = open_exn path in
      List.iter [ "2026-09-09"; "2026-09-10"; "2026-09-11" ] ~f:(fun d ->
          Journal.record_session j (session d));
      List.iter journal_indexes ~f:(fun name ->
          ignore
            (Sqlite3.exec (Journal.For_testing.db j) ("DROP INDEX IF EXISTS " ^ name)
              : Sqlite3.Rc.t));
      Alcotest.(check (list string)) "no indexes, as it is closed" [] (own_indexes j);
      Journal.close j;
      let j = open_exn path in
      Alcotest.(check (list sessions_t))
        "the three sessions, as written"
        [ session "2026-09-09"; session "2026-09-10"; session "2026-09-11" ]
        (Journal.sessions j);
      Alcotest.(check (list string))
        "reopened: the seven indexes, added at open" journal_indexes (own_indexes j);
      let version = ref [] in
      ignore
        (Sqlite3.exec_not_null_no_headers (Journal.For_testing.db j)
           ~cb:(fun row -> version := row.(0) :: !version)
           "SELECT value FROM meta WHERE key = 'schema_version'"
          : Sqlite3.Rc.t);
      Alcotest.(check (list string)) "and the file is still version 1" [ "1" ] !version;
      Journal.close j;
      (* and a third open, onto a file that has them, changes nothing *)
      let j = open_exn path in
      Alcotest.(check (list string))
        "opened again: the same seven, and no error" journal_indexes (own_indexes j);
      Journal.close j)

(* /api/desk's last query that grew with the journal: SQLite's own plan for
   [recent_fills], asked of the file this build actually opens, must read
   fills_by_at backwards for the ORDER BY and never sort. Asked before any
   fill is written -- the plan is a fact about the schema and the index, not
   about how many rows are in the table. *)
let test_recent_fills_reads_by_the_index_not_a_sort () =
  let j = open_exn ":memory:" in
  let plan =
    Journal.For_testing.query_plan j Journal.For_testing.recent_fills_sql
      [ Sqlite3.Data.INT 20L ]
  in
  Alcotest.(check bool)
    "walks fills_by_at" true
    (List.exists plan ~f:(fun step -> String.is_substring step ~substring:"fills_by_at"));
  Alcotest.(check bool)
    "no separate sort for the ORDER BY" false
    (List.exists plan ~f:(fun step -> String.is_substring step ~substring:"B-TREE"))

let test_a_date_recorded_twice_is_one_session () =
  let j = open_exn ":memory:" in
  Journal.record_session j (session ~equity:100_000.0 "2026-09-11");
  Journal.record_session j (session ~equity:101_000.0 "2026-09-11");
  Alcotest.(check (list sessions_t))
    "the second write replaced the first"
    [ session ~equity:101_000.0 "2026-09-11" ]
    (Journal.sessions j)

let test_sessions_come_back_in_date_order () =
  let j = open_exn ":memory:" in
  List.iter [ "2026-09-11"; "2026-09-09"; "2026-09-10" ] ~f:(fun d ->
      Journal.record_session j (session d));
  Alcotest.(check (list string))
    "ascending"
    [ "2026-09-09"; "2026-09-10"; "2026-09-11" ]
    (List.map (Journal.sessions j) ~f:(fun s -> Date.to_string s.Journal.Session.date))

let test_an_unknown_forecast_stays_unknown () =
  let j = open_exn ":memory:" in
  let known =
    {
      Journal.Forecast.date = date "2026-09-11";
      estimator = "historical";
      confidence = 0.95;
      var_fraction = Some 0.0123;
      var_notional = Some 1_107.0;
      es_notional = Some 1_530.5;
    }
  in
  let warming =
    {
      known with
      estimator = "ewma";
      var_fraction = None;
      var_notional = None;
      es_notional = None;
    }
  in
  Journal.record_forecasts j [ known; warming ];
  let back = Journal.forecasts j in
  Alcotest.(check (list string))
    "ordered by estimator within a date" [ "ewma"; "historical" ]
    (List.map back ~f:(fun f -> f.Journal.Forecast.estimator));
  let ewma = List.hd_exn back and historical = List.nth_exn back 1 in
  Alcotest.(check (option (float 0.0)))
    "a warming-up estimator reads None, not 0.0" None ewma.Journal.Forecast.var_fraction;
  Alcotest.(check (option (float 1e-12)))
    "a known fraction round-trips" (Some 0.0123) historical.Journal.Forecast.var_fraction;
  Alcotest.(check (option (float 1e-9)))
    "and its notional" (Some 1_107.0) historical.Journal.Forecast.var_notional

let test_marks_round_trip_in_symbol_order () =
  let j = open_exn ":memory:" in
  let mark s close qty =
    { Journal.Mark.date = date "2026-09-11"; symbol = Symbol.of_string s; close; qty }
  in
  Journal.record_marks j [ mark "XOM" 101.25 (-500.0); mark "AAPL" 227.5 400.0 ];
  Alcotest.(check (list string))
    "AAPL before XOM"
    [ "AAPL 227.5 400"; "XOM 101.25 -500" ]
    (List.map
       (Journal.marks j (date "2026-09-11"))
       ~f:(fun m ->
         sprintf "%s %s %s"
           (Symbol.to_string m.Journal.Mark.symbol)
           (Float.to_string_hum ~strip_zero:true m.Journal.Mark.close)
           (Float.to_string_hum ~strip_zero:true m.Journal.Mark.qty)));
  Alcotest.(check int)
    "another date holds nothing" 0
    (List.length (Journal.marks j (date "2026-09-10")))

let test_the_version_counts_writes_and_a_transaction_is_one () =
  let j = open_exn ":memory:" in
  Alcotest.(check int) "a fresh journal" 0 (Journal.version j);
  Journal.record_session j (session "2026-09-11");
  Alcotest.(check int) "one session" 1 (Journal.version j);
  Journal.record_marks j
    (List.map [ "A"; "B"; "C" ] ~f:(fun s ->
         {
           Journal.Mark.date = date "2026-09-11";
           symbol = Symbol.of_string s;
           close = 1.0;
           qty = 1.0;
         }));
  Alcotest.(check int)
    "three marks in one transaction are one write" 2 (Journal.version j);
  Journal.record_alert j
    {
      Journal.Alert.at;
      kind = "raised";
      limit_name = "tech-cap";
      line = "tech-cap breached";
    };
  Alcotest.(check int) "an alert" 3 (Journal.version j)

let test_a_file_from_a_newer_build_is_refused () =
  with_temp_path ~f:(fun path ->
      (* Written with the raw binding, as a future build would have left it. *)
      let db = Sqlite3.db_open path in
      ignore
        (Sqlite3.exec db "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
          : Sqlite3.Rc.t);
      ignore
        (Sqlite3.exec db "INSERT INTO meta VALUES ('schema_version', '2')" : Sqlite3.Rc.t);
      ignore (Sqlite3.db_close db : bool);
      match Journal.open_ ~path with
      | Ok _ ->
          Alcotest.fail "a journal at schema version 2 was opened by a build that knows 1"
      | Error e ->
          Alcotest.(check bool)
            "the error names both versions" true
            (String.is_substring (Error.to_string_hum e) ~substring:"schema version 2"
            && String.is_substring (Error.to_string_hum e) ~substring:"version 1"))

let test_rfc3339_round_trips_to_the_nanosecond () =
  let t = Time_ns.of_string_with_utc_offset "2026-09-11T20:05:00.123456789Z" in
  let s = Ohcamel_desk.Desk_time.rfc3339 t in
  Alcotest.(check string) "a T, not a space" "2026-09-11T20:05:00.123456789Z" s;
  Alcotest.(check bool)
    "parses back to the same instant" true
    (Option.equal Time_ns.equal (Some t) (Ohcamel_desk.Desk_time.parse s));
  (* 16:00 at -04:00 is 20:00Z on the same date; the date Alpaca means is the
     exchange's, which is the prefix. *)
  Alcotest.(check (option string))
    "the exchange-local date" (Some "2026-09-11")
    (Option.map ~f:Date.to_string
       (Ohcamel_desk.Desk_time.local_date "2026-09-11T16:00:00-04:00"))

(* A deferred foreign key is checked at COMMIT, not at the INSERT that will
   violate it -- the one path that puts a failure on the far side of "BEGIN
   IMMEDIATE ... COMMIT" instead of inside it, where the ordinary exception
   path already runs. SQLite's own rule for a COMMIT that fails this way is
   that the transaction is left open: the caller rolls it back, not SQLite
   (https://sqlite.org/lang_transaction.html). Skip that rollback and the
   connection is still inside the aborted transaction when the next caller's
   BEGIN IMMEDIATE runs, which fails with "cannot start a transaction within
   a transaction" -- one bad write taking down every write after it. *)
let test_a_failed_commit_rolls_back_before_the_next_write () =
  let j = open_exn ":memory:" in
  ignore
    (Sqlite3.exec (Journal.For_testing.db j) "PRAGMA foreign_keys = ON" : Sqlite3.Rc.t);
  ignore
    (Sqlite3.exec (Journal.For_testing.db j)
       "CREATE TABLE parent (id INTEGER PRIMARY KEY)"
      : Sqlite3.Rc.t);
  ignore
    (Sqlite3.exec (Journal.For_testing.db j)
       "CREATE TABLE child (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES parent(id) \
        DEFERRABLE INITIALLY DEFERRED)"
      : Sqlite3.Rc.t);
  (match
     Journal.For_testing.write j ~what:"child" (fun () ->
         Journal.For_testing.run j ~what:"child insert"
           "INSERT INTO child (id, parent) VALUES (1, 99)" [])
   with
  | () ->
      Alcotest.fail
        "a deferred foreign key violation should fail the commit, not the insert"
  | exception Failure _ -> ()
  | exception e -> Alcotest.failf "expected Failure, got %s" (Exn.to_string e));
  Alcotest.(check int) "the failed commit did not move the version" 0 (Journal.version j);
  (* Before the fix: this BEGIN IMMEDIATE fails with "cannot start a
     transaction within a transaction", because nothing rolled back the one
     the aborted COMMIT above left open. *)
  Journal.record_session j (session "2026-09-11");
  Alcotest.(check int)
    "an ordinary write afterwards still counts as one" 1 (Journal.version j)

(* Spec §3.5: a file journal runs in WAL mode. SQLite answers PRAGMA
   journal_mode with the mode it actually set, and open_ now refuses any
   answer but wal; this pins the answer on a real file. An in-memory database
   has no file for WAL to mean anything, and SQLite reports it as "memory". *)
let test_a_file_journal_runs_in_wal_mode () =
  with_temp_path ~f:(fun path ->
      let j = open_exn path in
      Alcotest.(check string)
        "a file: wal, as SQLite reports it" "wal"
        (Journal.For_testing.journal_mode j);
      Journal.close j);
  let m = open_exn ":memory:" in
  Alcotest.(check string)
    "in memory: memory" "memory"
    (Journal.For_testing.journal_mode m);
  Journal.close m;
  (* wal_check is the refusal's decision alone, with no SQLite: this
     filesystem always answers "wal" for real, so without driving the
     decision directly with an answer of our own choosing, the refusal
     branch could be deleted and the two checks above would stay green --
     exactly what a review found the plan's own RED attempt could not catch. *)
  let path = "/journal-test.db" in
  let ok modes = Result.is_ok (Journal.For_testing.wal_check ~path modes) in
  let refused modes = Result.is_error (Journal.For_testing.wal_check ~path modes) in
  (* SQLite reports the mode it set in lowercase; the check should not depend
     on that never changing. *)
  Alcotest.(check bool) "wal is accepted" true (ok [ "wal" ]);
  Alcotest.(check bool) "WAL, any case, is accepted" true (ok [ "WAL" ]);
  (* memory is what :memory: answers -- a file journal must never accept it. *)
  Alcotest.(check bool) "memory is refused" true (refused [ "memory" ]);
  (* delete is SQLite's default rollback journal, the mode WAL replaces. *)
  Alcotest.(check bool) "delete is refused" true (refused [ "delete" ]);
  (* No row at all is not "wal" either. *)
  Alcotest.(check bool) "no answer is refused" true (refused []);
  (* More than one row is not the single, unambiguous "wal" this checks for. *)
  Alcotest.(check bool) "two answers are refused" true (refused [ "wal"; "wal" ]);
  let error_names_the_path modes =
    match Journal.For_testing.wal_check ~path modes with
    | Ok () -> false
    | Error msg -> String.is_substring msg ~substring:path
  in
  (* Every refusal should let a reader find which file was rejected. *)
  Alcotest.(check bool)
    "memory's refusal names the path" true
    (error_names_the_path [ "memory" ]);
  Alcotest.(check bool)
    "delete's refusal names the path" true
    (error_names_the_path [ "delete" ]);
  Alcotest.(check bool)
    "no-answer's refusal names the path" true (error_names_the_path []);
  Alcotest.(check bool)
    "two-answers' refusal names the path" true
    (error_names_the_path [ "wal"; "wal" ])

(* Task 3's re-review, minor: [unfinished_failed_orders] must let SQLite use
   orders_by_created (created_at, client_order_id) as a bounded SEARCH, not
   merely to walk every row in order. The old clause, "(? IS NULL OR
   created_at > ?)", is not sargable -- a column compared to NULL in the same
   OR cannot drive an index range -- so SQLite still used the index for the
   ORDER BY (checked below) but as a full SCAN, touching every row; the fixed
   query always binds a value (a date string, or "" when there is no session
   yet) and compares with a plain ">=", which SQLite can answer as a SEARCH,
   bounded by that value. There is no [Journal.For_testing.query_plan] on
   this branch (a later task adds one elsewhere), so this reads SQLite's own
   answer directly, through the raw handle [For_testing.db] exposes. *)
let test_the_failed_order_query_is_a_bounded_search_not_a_scan () =
  let j = open_exn ":memory:" in
  let plan =
    let lines = ref [] in
    ignore
      (Sqlite3.exec_not_null_no_headers (Journal.For_testing.db j)
         ~cb:(fun row -> lines := String.concat_array ~sep:" " row :: !lines)
         "EXPLAIN QUERY PLAN SELECT client_order_id FROM orders WHERE state = 'failed' \
          AND created_at >= '2026-01-01' ORDER BY created_at, client_order_id"
        : Sqlite3.Rc.t);
    String.concat ~sep:"; " (List.rev !lines)
  in
  Alcotest.(check bool)
    (sprintf "a bounded SEARCH on orders_by_created (plan: %s)" plan)
    true
    (String.is_substring plan ~substring:"SEARCH orders USING INDEX orders_by_created");
  Alcotest.(check bool)
    (sprintf "not a SCAN (plan: %s)" plan)
    false
    (String.is_substring plan ~substring:"SCAN orders");
  Journal.close j

(* Task 15's two columns, onto a file written before them. The file is made
   by hand in the shape an older build wrote -- orders with no tif, signals
   with no rebalance -- with one order and one judgement in it. Opening it
   adds both columns, once: the old order reads back as a day order, which
   every order then was, and the old judgement as having no rebalance. A new
   market-on-open order round-trips its tif. Opening it again finds both
   columns there and adds nothing -- ALTER TABLE ADD COLUMN twice would fail
   -- and the file is still schema version 1. *)
let test_the_migration_adds_tif_and_rebalance_to_an_existing_file () =
  with_temp_path ~f:(fun path ->
      let db = Sqlite3.db_open path in
      let exec sql =
        match Sqlite3.exec db sql with
        | Sqlite3.Rc.OK -> ()
        | rc -> Alcotest.failf "%s: %s" (Sqlite3.Rc.to_string rc) sql
      in
      List.iter
        [
          "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)";
          "INSERT INTO meta VALUES ('schema_version', '1')";
          "CREATE TABLE orders (client_order_id TEXT PRIMARY KEY, venue_order_id TEXT, \
           source TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT \
           NULL, kind TEXT NOT NULL, limit_price REAL, state TEXT NOT NULL, filled_qty \
           REAL NOT NULL DEFAULT 0, avg_fill_price REAL, decision_price REAL NOT NULL, \
           arrival_bid REAL, arrival_ask REAL, verdict TEXT NOT NULL, created_at TEXT \
           NOT NULL, updated_at TEXT NOT NULL)";
          "INSERT INTO orders (client_order_id, source, symbol, side, qty, kind, state, \
           decision_price, verdict, created_at, updated_at) VALUES \
           ('ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1', 'manual', 'AAPL', 'buy', 10, 'market', \
           'rejected_pre_trade', 150, 'null', '2026-09-11T14:00:00.000000000Z', \
           '2026-09-11T14:00:00.000000000Z')";
          "CREATE TABLE signals (strategy TEXT NOT NULL, sequence INTEGER NOT NULL, \
           as_of TEXT NOT NULL, received_at TEXT NOT NULL, verdict TEXT NOT NULL, rule \
           TEXT, detail TEXT, document TEXT NOT NULL, PRIMARY KEY (strategy, sequence))";
          "INSERT INTO signals VALUES ('exp_a01_spy', 1, '2026-09-11', \
           '2026-09-11T23:16:00.000000000Z', 'advisory', 'sizing', 'shown', '{}')";
        ]
        ~f:exec;
      ignore (Sqlite3.db_close db : bool);
      let j = open_exn path in
      let has j table column =
        List.count (Journal.For_testing.columns j ~table) ~f:(String.equal column)
      in
      Alcotest.(check (pair int int))
        "orders.tif and signals.rebalance, each once" (1, 1)
        (has j "orders" "tif", has j "signals" "rebalance");
      let old =
        Option.value_exn
          (Journal.load_order j
             (Option.value_exn
                (Ohcamel_desk.Ids.Client_order_id.of_string
                   "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1")))
      in
      Alcotest.(check string)
        "the old order is a day order" "day"
        (Ohcamel_desk.Order.Tif.to_string
           old.Journal.Order_row.order.Ohcamel_desk.Order.request
             .Ohcamel_desk.Order.Request.tif);
      Alcotest.(check (option string))
        "the old judgement has no rebalance" None
        (Option.bind (Journal.signal j ~strategy:"exp_a01_spy" ~sequence:1) ~f:(fun s ->
             s.Journal.Signal.rebalance));
      let opg =
        Ohcamel_desk.Order.create
          {
            Ohcamel_desk.Order.Request.client_order_id =
              Option.value_exn
                (Ohcamel_desk.Ids.Client_order_id.of_string
                   "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ2");
            symbol = Symbol.of_string "SPY";
            side = Ohcamel_desk.Order.Side.Buy;
            qty = 3;
            kind = Ohcamel_desk.Order.Kind.Market;
            tif = Ohcamel_desk.Order.Tif.Opg;
          }
      in
      Journal.insert_order j opg ~source:"signal:exp_a01_spy:2"
        ~decision_price:(Price.of_float 500.0) ~arrival:None ~verdict:`Null ~at;
      Alcotest.(check (option string))
        "a market-on-open order round-trips" (Some "opg")
        (Option.map
           (Journal.load_order j
              opg.Ohcamel_desk.Order.request.Ohcamel_desk.Order.Request.client_order_id)
           ~f:(fun r ->
             Ohcamel_desk.Order.Tif.to_string
               r.Journal.Order_row.order.Ohcamel_desk.Order.request
                 .Ohcamel_desk.Order.Request.tif));
      Journal.close j;
      let j = open_exn path in
      Alcotest.(check (pair int int))
        "opened again: still once each, and no error" (1, 1)
        (has j "orders" "tif", has j "signals" "rebalance");
      let version = ref [] in
      ignore
        (Sqlite3.exec_not_null_no_headers (Journal.For_testing.db j)
           ~cb:(fun row -> version := row.(0) :: !version)
           "SELECT value FROM meta WHERE key = 'schema_version'"
          : Sqlite3.Rc.t);
      Alcotest.(check (list string)) "and the file is still version 1" [ "1" ] !version;
      Journal.close j)

(* A market-on-open order's price: the newest SESSION's close. Marks for the
   16th with no session row are a close that never finished -- the session
   row is written last -- and are not read; the 15th's are. A name with no
   mark that session, or a journal with no session, has no close. *)
let test_the_latest_close_is_the_newest_session's () =
  let j = open_exn ":memory:" in
  let spy = Symbol.of_string "SPY" and tlt = Symbol.of_string "TLT" in
  Alcotest.(check bool)
    "no session, no close" true
    (Option.is_none (Journal.latest_close j spy));
  let mark d symbol close = { Journal.Mark.date = date d; symbol; close; qty = 0.0 } in
  Journal.record_marks j [ mark "2026-09-14" spy 500.0; mark "2026-09-14" tlt 90.0 ];
  Journal.record_session j (session "2026-09-14");
  Journal.record_marks j [ mark "2026-09-15" spy 501.25 ];
  Journal.record_session j (session "2026-09-15");
  Journal.record_marks j [ mark "2026-09-16" spy 999.0 ];
  Alcotest.(check (option (pair string (float 0.0))))
    "SPY: the 15th's close, not the unfinished 16th's"
    (Some ("2026-09-15", 501.25))
    (Option.map (Journal.latest_close j spy) ~f:(fun (d, c) -> (Date.to_string d, c)));
  Alcotest.(check bool)
    "TLT: no mark on the newest session, so no close" true
    (Option.is_none (Journal.latest_close j tlt))

(* A strategy's own fills: the source range selects signal:exp_a01_spy:*
   and nothing else -- not a hand order, not signal:exp_a01_spy_2:*, whose
   slug merely begins the same way -- and SQLite reads it through
   orders_by_source rather than scanning orders. *)
let test_a_strategy's_fills_are_read_through_orders_by_source () =
  let j = open_exn ":memory:" in
  let lo, hi = Journal.For_testing.source_range "signal:exp_a01_spy:" in
  Alcotest.(check (pair string string))
    "the range: its prefix, to the prefix with ':' moved on to ';'"
    ("signal:exp_a01_spy:", "signal:exp_a01_spy;")
    (Sqlite3.Data.to_string_coerce lo, Sqlite3.Data.to_string_coerce hi);
  let plan =
    String.concat ~sep:" | "
      (Journal.For_testing.query_plan j Journal.For_testing.source_fills_sql [ lo; hi ])
  in
  Alcotest.(check bool)
    (sprintf "orders_by_source (plan: %s)" plan)
    true
    (String.is_substring plan ~substring:"orders_by_source");
  Alcotest.(check bool)
    (sprintf "not a SCAN of orders (plan: %s)" plan)
    false
    (String.is_substring plan ~substring:"SCAN o ");
  Journal.close j

let suite =
  ( "journal",
    [
      Alcotest.test_case "a session survives a restart" `Quick
        test_a_session_survives_a_restart;
      Alcotest.test_case "recent fills reads by the index, not a sort" `Quick
        test_recent_fills_reads_by_the_index_not_a_sort;
      Alcotest.test_case "a date recorded twice is one session" `Quick
        test_a_date_recorded_twice_is_one_session;
      Alcotest.test_case "sessions come back in date order" `Quick
        test_sessions_come_back_in_date_order;
      Alcotest.test_case "an unknown forecast stays unknown" `Quick
        test_an_unknown_forecast_stays_unknown;
      Alcotest.test_case "marks round-trip in symbol order" `Quick
        test_marks_round_trip_in_symbol_order;
      Alcotest.test_case "the version counts writes, and a transaction is one" `Quick
        test_the_version_counts_writes_and_a_transaction_is_one;
      Alcotest.test_case "a file from a newer build is refused" `Quick
        test_a_file_from_a_newer_build_is_refused;
      Alcotest.test_case "RFC 3339 round-trips to the nanosecond" `Quick
        test_rfc3339_round_trips_to_the_nanosecond;
      Alcotest.test_case "a failed commit rolls back before the next write" `Quick
        test_a_failed_commit_rolls_back_before_the_next_write;
      Alcotest.test_case "a file journal runs in WAL mode" `Quick
        test_a_file_journal_runs_in_wal_mode;
      Alcotest.test_case "the failed-order query is a bounded search, not a scan" `Quick
        test_the_failed_order_query_is_a_bounded_search_not_a_scan;
      Alcotest.test_case "the migration adds tif and rebalance to an existing file" `Quick
        test_the_migration_adds_tif_and_rebalance_to_an_existing_file;
      Alcotest.test_case "the latest close is the newest session's" `Quick
        test_the_latest_close_is_the_newest_session's;
      Alcotest.test_case "a strategy's fills are read through orders_by_source" `Quick
        test_a_strategy's_fills_are_read_through_orders_by_source;
    ] )
