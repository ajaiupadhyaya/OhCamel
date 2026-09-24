(* Backing the journal up, checking the copy, and deciding what to delete.

   The journal is the only persistence in the project (invariant 13), so the
   backup is the only thing standing between a corrupted file and a desk with
   no record of the orders it sent. Three separable claims, and each is tested
   on its own because each fails differently:

     THE COPY IS A COPY      SQLite's online backup API against a live WAL
                             database with a second connection open. `cp` on
                             such a file can catch it mid-transaction and
                             produce a file that opens and is wrong, which is
                             the worst possible failure for a backup: it looks
                             fine until the restore.

     THE COPY IS CHECKED     the copy is reopened read-only -- read-only
                             because an [open_] would add any table the copy
                             was missing and then report it sound -- and asked
                             for its schema version, its integrity, its row
                             counts and its newest rows. A file that answers
                             badly is still printed; it just exits non-zero.

     THE PRUNING IS DECIDED  Backup_retention is pure, so the case that
                             matters -- two months of dailies -- is a list of
                             names rather than a filesystem and a clock nobody
                             can move. Every number below is derived in the
                             comment above it. *)

open Core
open Ohcamel.Types
module Journal = Ohcamel_desk.Journal
module Retention = Ohcamel_desk.Backup_retention
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T14:31:00Z"
let date = Date.of_string

(* One temp directory per case, removed afterwards with everything in it: the
   journal, its -wal and -shm siblings, the copy, and any .tmp a failed backup
   left behind. *)
let with_temp_dir ~f =
  let dir = Filename_unix.temp_dir "ohcamel-journal-backup-" "" in
  let cleanup () =
    Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
        let path = Filename.concat dir name in
        match Core_unix.lstat path with
        | { st_kind = S_DIR; _ } -> Core_unix.rmdir path
        | _ -> Core_unix.unlink path);
    Core_unix.rmdir dir
  in
  Exn.protect ~f:(fun () -> f dir) ~finally:cleanup

let open_exn path = Or_error.ok_exn (Journal.open_ ~path)

let read_only_exn path =
  match Journal.open_read_only path with
  | Ok j -> j
  | Error e -> Alcotest.failf "open_read_only %s: %s" path e

let backup_exn ~src ~dst =
  match Journal.backup ~src ~dst with
  | Ok () -> ()
  | Error e -> Alcotest.failf "backup: %s" e

let verify_exn path =
  match Journal.verify path with
  | Ok r -> r
  | Error e -> Alcotest.failf "verify %s: %s" path e

(* Every row of one table, in rowid order, each column rendered with its own
   constructor so a NULL cannot read as an empty string and an INT cannot read
   as the REAL beside it. The backup API copies pages, so rowid order in the
   copy is rowid order in the source; comparing these lists is comparing the
   tables row for row. *)
let table_rows db table =
  let stmt = Sqlite3.prepare db (sprintf "SELECT * FROM %s ORDER BY rowid" table) in
  let rows =
    match
      Sqlite3.fold stmt ~init:[] ~f:(fun acc row ->
          String.concat ~sep:"|"
            (Array.to_list (Array.map row ~f:Sqlite3.Data.to_string_debug))
          :: acc)
    with
    | Sqlite3.Rc.DONE, acc -> List.rev acc
    | rc, _ -> Alcotest.failf "reading %s: %s" table (Sqlite3.Rc.to_string rc)
  in
  ignore (Sqlite3.finalize stmt : Sqlite3.Rc.t);
  rows

let sqlite_tables db =
  let stmt =
    Sqlite3.prepare db
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' \
       ORDER BY name"
  in
  let names =
    match
      Sqlite3.fold stmt ~init:[] ~f:(fun acc row ->
          Sqlite3.Data.to_string_coerce row.(0) :: acc)
    with
    | Sqlite3.Rc.DONE, acc -> List.rev acc
    | rc, _ -> Alcotest.failf "reading sqlite_master: %s" (Sqlite3.Rc.to_string rc)
  in
  ignore (Sqlite3.finalize stmt : Sqlite3.Rc.t);
  names

let id n =
  Option.value_exn
    (Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ%d" n))

(* One row in every table the schema creates, so a backup that dropped a table
   or a row cannot pass. The orders/order_events/fills triple is written the
   way the order manager writes it: insert, then the state machine's events
   journaled as they are applied. *)
let fill_a_journal j =
  Journal.record_session j
    {
      Journal.Session.date = date "2026-09-14";
      equity_close = 101_250.0;
      cash_close = 40_000.0;
      gross_close = 90_000.0;
      net_close = 60_000.0;
      recorded_at = at;
    };
  Journal.record_marks j
    [
      {
        Journal.Mark.date = date "2026-09-14";
        symbol = Symbol.of_string "AAPL";
        close = 231.5;
        qty = 100.0;
      };
    ];
  Journal.record_forecasts j
    [
      {
        Journal.Forecast.date = date "2026-09-14";
        estimator = "historical";
        confidence = 0.95;
        var_fraction = Some 0.0123;
        var_notional = Some 1_107.0;
        es_notional = None;
      };
    ];
  Journal.record_alert j
    { Journal.Alert.at; kind = "breach"; limit_name = "gross_exposure"; line = "over" };
  Journal.record_signal j
    {
      Journal.Signal.strategy = "exp-a01-spy";
      sequence = 1;
      as_of = date "2026-09-14";
      received_at = at;
      verdict = Journal.Signal.Verdict.Accepted;
      rule = None;
      detail = "accepted";
      document = "(signal (strategy exp-a01-spy))";
      rebalance = Some "pending";
    };
  Journal.record_signal_file j ~name:"broken.json" ~received_at:at ~error:"not a signal";
  Journal.record_deferral j ~strategy:"exp-a01-spy" ~sequence:2
    ~first_latest:(date "2026-09-14");
  let order =
    Order.create
      {
        Order.Request.client_order_id = id 1;
        symbol = Symbol.of_string "AAPL";
        side = Order.Side.Buy;
        qty = 100;
        kind = Order.Kind.Market;
        tif = Order.Tif.Day;
      }
  in
  Journal.insert_order j order ~source:"manual" ~decision_price:(Price.of_float 231.5)
    ~arrival:None ~verdict:`Null ~at;
  let step o event =
    let o, anomaly = Order.apply o event in
    (match event with
    | Order.Event.Venue_fill f -> ignore (Journal.record_fill j o f : bool)
    | _ -> ());
    Journal.update_order j o ~event ~anomaly ~at;
    o
  in
  ignore
    (List.fold
       [
         Order.Event.Acknowledged "venue-1";
         Order.Event.Venue_accepted;
         Order.Event.Venue_fill
           {
             Order.Fill.execution_id = "x1";
             qty = 100.0;
             price = Price.of_float 231.5;
             at;
             position_qty = None;
           };
       ]
       ~init:order ~f:step
      : Order.t)

(* THE COPY IS A COPY.

   The source is a file journal in WAL mode with a SECOND connection open on
   it, which is the state the live desk is in when the timer fires at 06:30
   UTC: one writer inside the container, and this reader. Every one of the
   eleven tables is compared row for row. *)
let test_a_live_journal_round_trips_through_the_backup () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      (* the second connection, open across the whole backup *)
      let reader = open_exn src in
      Alcotest.(check string)
        "the source is in WAL mode, which is the case the API is for" "wal"
        (Journal.For_testing.journal_mode j);
      let dst = Filename.concat dir "desk-2026-09-14.db" in
      backup_exn ~src ~dst;
      let copy = read_only_exn dst in
      (* the copy is in the rollback journal mode, not WAL: SQLite will not open
         a WAL database read-only unless it can create the -shm beside it, and a
         backup that needs a writable directory to be read is not a backup *)
      Alcotest.(check string)
        "the copy needs no -shm to be read" "delete"
        (Journal.For_testing.journal_mode copy);
      Alcotest.(check (list string))
        "the copy holds the same eleven tables"
        (List.sort Journal.tables ~compare:String.compare)
        (sqlite_tables (Journal.For_testing.db copy));
      List.iter Journal.tables ~f:(fun table ->
          Alcotest.(check (list string))
            (sprintf "%s, row for row" table)
            (table_rows (Journal.For_testing.db j) table)
            (table_rows (Journal.For_testing.db copy) table));
      let report = verify_exn dst in
      Alcotest.(check (option string))
        "the copy's schema version is this build's" (Some "1")
        report.Journal.Report.schema_version;
      Alcotest.(check (list string))
        "integrity_check" [ "ok" ] report.Journal.Report.integrity;
      Alcotest.(check bool)
        "and nothing is wrong with it" true (Journal.Report.clean report);
      Journal.close copy;
      Journal.close reader;
      Journal.close j)

(* The source is opened SQLITE_OPEN_READONLY, so the backup cannot have
   written to it -- not a page, not a checkpoint, not a journal-mode change.
   Checked from the other side: the tables read the same afterwards, the file
   is still in WAL mode, and the connection that was open across the backup can
   still write. *)
let test_the_backup_only_reads_the_source () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let before =
        List.map Journal.tables ~f:(fun t -> (t, table_rows (Journal.For_testing.db j) t))
      in
      backup_exn ~src ~dst:(Filename.concat dir "copy.db");
      List.iter before ~f:(fun (table, rows) ->
          Alcotest.(check (list string))
            (sprintf "%s is unchanged" table)
            rows
            (table_rows (Journal.For_testing.db j) table));
      Alcotest.(check string)
        "and the source is still in WAL mode" "wal"
        (Journal.For_testing.journal_mode j);
      (* the writer still writes: a second session, and the journal's own
         version counter moves for it *)
      let version = Journal.version j in
      Journal.record_session j
        {
          Journal.Session.date = date "2026-09-15";
          equity_close = 101_000.0;
          cash_close = 40_000.0;
          gross_close = 90_000.0;
          net_close = 60_000.0;
          recorded_at = at;
        };
      Alcotest.(check int) "the writer still writes" (version + 1) (Journal.version j);
      Alcotest.(check int) "two sessions now" 2 (Journal.session_count j);
      Journal.close j)

(* SQLITE_OPEN_READONLY, and the proof is that a write fails. Without the mode
   flag every one of these cases still passes -- the copy would simply be
   writable -- so this is the case that pins the flag. *)
let test_a_read_only_handle_refuses_a_write () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      let copy = read_only_exn dst in
      let wrote =
        match
          Sqlite3.exec (Journal.For_testing.db copy)
            "INSERT INTO meta (key, value) VALUES ('x', 'y')"
        with
        | Sqlite3.Rc.OK -> true
        | _ -> false
      in
      Alcotest.(check bool) "a write through the read-only handle fails" false wrote;
      Alcotest.(check int)
        "and meta still holds one row" 1
        (List.length (table_rows (Journal.For_testing.db copy) "meta"));
      Journal.close copy)

(* NO [set_up]. An empty file is a valid empty SQLite database, so [open_]
   would open it, create eleven tables and an index each, write the schema
   version, and hand back a handle onto something that is not a backup of
   anything. [open_read_only] must leave the file exactly as it found it --
   here, zero bytes long. *)
let test_a_read_only_open_creates_nothing () =
  with_temp_dir ~f:(fun dir ->
      let path = Filename.concat dir "empty.db" in
      Out_channel.write_all path ~data:"";
      let j = read_only_exn path in
      Alcotest.(check (list string))
        "no tables, because none were created" []
        (sqlite_tables (Journal.For_testing.db j));
      Journal.close j;
      Alcotest.(check int)
        "and the file is still empty" 0
        (String.length (In_channel.read_all path)))

(* Mode 0640: readable by the owner and the group that pulls it off the box,
   and by nobody else, because the journal carries the desk's whole record.
   And no .tmp left behind, because the copy is written to one and renamed --
   a reader of the directory sees either no file or a whole one, never a
   partial page copy under the name a restore would reach for. *)
let test_the_copy_is_group_readable_and_no_tmp_survives () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "desk-2026-09-14.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      (* st_perm is the permission bits alone; 0o640 = rw-r----- *)
      Alcotest.(check int) "0640" 0o640 (Core_unix.stat dst).st_perm;
      (* Not just the .tmp: the destination handle makes a -shm for itself
         while the copy is still in the source's WAL mode, and SQLite does not
         unlink it. Every name beginning "desk-2026-09-14.db.tmp" must be gone,
         or the backup directory grows one file a day that retention will
         never delete, because it keeps what it cannot parse. *)
      Alcotest.(check (list string))
        "nothing beginning .tmp survives the rename" []
        (List.filter
           (List.sort (Array.to_list (Sys_unix.readdir dir)) ~compare:String.compare)
           ~f:(fun name -> String.is_prefix name ~prefix:(Filename.basename dst ^ ".tmp"))))

(* THE COPY IS CHECKED, on a copy that is sound.

   The counts are the rows [fill_a_journal] writes, one table at a time:

     meta              1   the schema_version row
     orders            1   one order
     order_events      4   created, then Acknowledged, Venue_accepted, Venue_fill
     fills             1   execution x1
     sessions          1   2026-09-14
     marks             1   AAPL
     forecasts         1   historical
     signals           1   exp-a01-spy #1
     alerts            1   the gross_exposure breach
     signal_files      1   broken.json
     signal_deferrals  1   exp-a01-spy #2

   in [Journal.tables] order, which is the schema's own. *)
let test_verify_reports_the_version_the_counts_and_the_newest_rows () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      let r = verify_exn dst in
      Alcotest.(check string) "the report names the file" dst r.Journal.Report.path;
      Alcotest.(check (option string))
        "schema version" (Some "1") r.Journal.Report.schema_version;
      Alcotest.(check (list string)) "integrity_check" [ "ok" ] r.Journal.Report.integrity;
      Alcotest.(check (list string))
        "no table is missing" [] r.Journal.Report.missing_tables;
      Alcotest.(check (list (pair string int)))
        "the eleven counts, in schema order"
        [
          ("meta", 1);
          ("orders", 1);
          ("order_events", 4);
          ("fills", 1);
          ("sessions", 1);
          ("marks", 1);
          ("forecasts", 1);
          ("signals", 1);
          ("alerts", 1);
          ("signal_files", 1);
          ("signal_deferrals", 1);
        ]
        r.Journal.Report.counts;
      Alcotest.(check (option string))
        "the newest session" (Some "2026-09-14")
        (Option.map r.Journal.Report.newest_session ~f:Date.to_string);
      Alcotest.(check (option string))
        "the newest order"
        (Some (Ids.Client_order_id.to_string (id 1)))
        (Option.map r.Journal.Report.newest_order ~f:fst);
      Alcotest.(check bool) "clean" true (Journal.Report.clean r);
      (* the printed report says the version and the integrity answer, because
         that is what the operator reads when the timer mails him the output *)
      let text = String.concat ~sep:"\n" (Journal.Report.lines r) in
      Alcotest.(check bool)
        "the printed report names the file" true
        (String.is_substring text ~substring:dst);
      Alcotest.(check bool)
        "and the integrity answer" true
        (String.is_substring text ~substring:"ok"))

(* An empty journal is sound, and its newest session and newest order are
   unknown rather than zero (invariant: unknown is not zero). *)
let test_verify_reports_an_empty_journal_as_empty_not_broken () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      Journal.close j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      let r = verify_exn dst in
      Alcotest.(check bool) "clean" true (Journal.Report.clean r);
      Alcotest.(check (option string))
        "no newest session" None
        (Option.map r.Journal.Report.newest_session ~f:Date.to_string);
      Alcotest.(check (option string))
        "no newest order" None
        (Option.map r.Journal.Report.newest_order ~f:fst);
      Alcotest.(check int)
        "every table counted, all but meta empty" 11
        (List.length r.Journal.Report.counts))

(* A truncated copy is what a full disk or a killed container leaves behind,
   and it is the failure a backup that is never checked hides. Whether SQLite
   refuses the file outright or answers and fails integrity_check is its
   business; what [journal-verify] promises is that neither reads as clean,
   and so that it exits non-zero. *)
let test_verify_never_calls_a_truncated_copy_clean () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      let whole = In_channel.read_all dst in
      Alcotest.(check bool)
        "the copy is more than one page long" true
        (String.length whole > 4096);
      Out_channel.write_all dst ~data:(String.prefix whole (String.length whole / 2));
      let clean =
        match Journal.verify dst with Ok r -> Journal.Report.clean r | Error _ -> false
      in
      Alcotest.(check bool) "a truncated copy is never clean" false clean)

(* THE COPY IS CHECKED, on answers of our own choosing.

   Ruling 11: the copy is reopened and integrity_check'ed before rotation. The
   corruptions SQLite REPORTS rather than refuses -- "row 3 missing from index
   fills_by_at", and the rest of integrity_check's own vocabulary -- cannot be
   manufactured by a hermetic test without writing bytes at page offsets this
   build's SQLite happens to use, or without a writable_schema that a defensive
   build refuses outright (both were tried). So the verdict is driven directly,
   exactly as [wal_check] is driven with a journal_mode answer of our own, and
   for the same reason: a case that only read a real file could never tell a
   working integrity check from a deleted one.

   The four kinds of trouble, and the order the report prints them in. *)
let test_the_verdict_names_every_kind_of_trouble () =
  let verdict ?(schema_version = Some "1") ?(integrity = [ "ok" ]) ?(missing_tables = [])
      () =
    Journal.For_testing.problems_of ~schema_version ~integrity ~missing_tables
  in
  Alcotest.(check (list string)) "a sound copy: nothing is wrong with it" [] (verdict ());
  Alcotest.(check (list string))
    "integrity_check's own words, joined"
    [
      "integrity_check answered row 3 missing from index fills_by_at; wrong # of entries \
       in index orders_open";
    ]
    (verdict
       ~integrity:
         [
           "row 3 missing from index fills_by_at";
           "wrong # of entries in index orders_open";
         ]
       ());
  Alcotest.(check (list string))
    "a table that is not there is named, one problem each"
    [ "no alerts table"; "no signal_files table" ]
    (verdict ~missing_tables:[ "alerts"; "signal_files" ] ());
  Alcotest.(check (list string))
    "a file with no schema version at all"
    [ "no schema version recorded" ]
    (verdict ~schema_version:None ());
  (* the sentence names both numbers, because the operator needs to know which
     build wrote the file and which build is refusing it *)
  Alcotest.(check (list string))
    "a version this build does not know"
    [ "schema version 2, and this build knows version 1" ]
    (verdict ~schema_version:(Some "2") ());
  (* all three at once, integrity first, then the tables, then the version:
     [Report.lines] prints them in this order and the exit status is the same
     for any one of them *)
  Alcotest.(check (list string))
    "and all of it at once, in the order the report prints"
    [
      "integrity_check answered database disk image is malformed";
      "no marks table";
      "schema version 2, and this build knows version 1";
    ]
    (verdict ~schema_version:(Some "2")
       ~integrity:[ "database disk image is malformed" ]
       ~missing_tables:[ "marks" ] ())

(* A copy missing a table is the failure [open_read_only] exists to expose: an
   [open_] would create the table, find it empty, and report the file sound.
   The problem is named, and named in the printed report, because an operator
   reading the timer's output has nothing else to go on. *)
let test_verify_names_a_missing_table () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      let db = Sqlite3.db_open dst in
      ignore (Sqlite3.exec db "DROP TABLE alerts" : Sqlite3.Rc.t);
      ignore (Sqlite3.db_close db : bool);
      let r = verify_exn dst in
      Alcotest.(check (list string))
        "alerts is missing" [ "alerts" ] r.Journal.Report.missing_tables;
      Alcotest.(check bool) "so the copy is not clean" false (Journal.Report.clean r);
      Alcotest.(check int)
        "ten tables counted, not eleven" 10
        (List.length r.Journal.Report.counts);
      Alcotest.(check bool)
        "and the printed report names it" true
        (List.exists (Journal.Report.lines r) ~f:(fun line ->
             String.is_substring line ~substring:"alerts")))

(* A schema version this build does not know is a file it must not guess at --
   [open_] refuses it outright. [verify] must not refuse it, because the
   operator wants the counts printed beside the trouble; it reports it as a
   problem, and the exit status is the same. *)
let test_verify_names_a_schema_version_this_build_does_not_know () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let dst = Filename.concat dir "copy.db" in
      backup_exn ~src ~dst;
      Journal.close j;
      let db = Sqlite3.db_open dst in
      ignore
        (Sqlite3.exec db "UPDATE meta SET value = '99' WHERE key = 'schema_version'"
          : Sqlite3.Rc.t);
      ignore (Sqlite3.db_close db : bool);
      let r = verify_exn dst in
      Alcotest.(check (option string))
        "it says 99" (Some "99") r.Journal.Report.schema_version;
      Alcotest.(check bool) "so it is not clean" false (Journal.Report.clean r);
      Alcotest.(check bool)
        "and the problem names both versions" true
        (List.exists r.Journal.Report.problems ~f:(fun p ->
             String.is_substring p ~substring:"99"
             && String.is_substring p ~substring:(Int.to_string Journal.schema_version))))

(* [Journal.tables] is read out of the schema rather than written down twice,
   so this pins what the schema holds today: eleven tables, in the order the
   CREATE TABLE statements appear. A twelfth is a line in this list and a
   count in the report, both in the task that adds it. *)
let test_tables_is_the_schemas_eleven_in_order () =
  Alcotest.(check (list string))
    "the schema's tables, in its order"
    [
      "meta";
      "orders";
      "order_events";
      "fills";
      "sessions";
      "marks";
      "forecasts";
      "signals";
      "alerts";
      "signal_files";
      "signal_deferrals";
    ]
    Journal.tables

(* ------------------------------------------------------------------------ *)
(* THE PRUNING IS DECIDED                                                    *)
(* ------------------------------------------------------------------------ *)

let daily d = sprintf "desk-%s.db" (Date.to_string d)

(* 2026-09-01 .. 2026-10-31 inclusive: September's 30 days and October's 31,
   61 names, oldest first. *)
let sixty_one_dailies =
  List.map ~f:daily
    (List.init 30 ~f:(fun i -> Date.add_days (date "2026-09-01") i)
    @ List.init 31 ~f:(fun i -> Date.add_days (date "2026-10-01") i))

let now = date "2026-10-31"

(* The premise the whole retention case rests on, so it is pinned rather than
   asserted in prose: 2026-10-31 is a Saturday. Derivation -- 2024-01-01 was a
   Monday; 2024 is a leap year, so 2025-01-01 is Monday + 366 mod 7 = Monday +
   2 = Wednesday; 2025 is common, so 2026-01-01 is Thursday. 2026-10-31 is day
   31+28+31+30+31+30+31+31+30+31 = 304 of the year, 303 days after January 1,
   and 303 = 43*7 + 2, so it is Thursday + 2 = Saturday. *)
let test_the_reckoning_day_is_a_saturday () =
  Alcotest.(check string)
    "2026-10-31" "SAT"
    (Day_of_week.to_string (Date.day_of_week now))

(* THE RETENTION CASE (ruling 11, and the plan's own arithmetic).

   61 dailies, `now` = Saturday 2026-10-31.

     the 14 newest      10-31 back to 10-18, because 31 - 18 + 1 = 14
     what is left       09-01 .. 10-17, which is 30 + 17 = 47 names
     Sundays in those   now is a Saturday, so the Sundays going back are
                        10-25, 10-18, 10-11, 10-04, 09-27, 09-20, 09-13,
                        09-06. 10-25 and 10-18 are both already inside the 14,
                        so six are left: 10-11, 10-04, 09-27, 09-20, 09-13,
                        09-06 -- fewer than the 8 the rule allows, so all six
                        are kept.
     kept               14 + 6 = 20
     deleted            47 - 6 = 41, and 20 + 41 = 61, so nothing is lost.

   The 20 are written out below in name order, which for these names is date
   order: the six Sundays first (they are all in September or early October),
   then 10-18 through 10-31. *)
let expected_kept =
  [
    "desk-2026-09-06.db";
    "desk-2026-09-13.db";
    "desk-2026-09-20.db";
    "desk-2026-09-27.db";
    "desk-2026-10-04.db";
    "desk-2026-10-11.db";
    "desk-2026-10-18.db";
    "desk-2026-10-19.db";
    "desk-2026-10-20.db";
    "desk-2026-10-21.db";
    "desk-2026-10-22.db";
    "desk-2026-10-23.db";
    "desk-2026-10-24.db";
    "desk-2026-10-25.db";
    "desk-2026-10-26.db";
    "desk-2026-10-27.db";
    "desk-2026-10-28.db";
    "desk-2026-10-29.db";
    "desk-2026-10-30.db";
    "desk-2026-10-31.db";
  ]

let test_retention_keeps_twenty_of_sixty_one_dailies () =
  Alcotest.(check int) "the fixture is 61 names" 61 (List.length sixty_one_dailies);
  Alcotest.(check int) "and 20 are expected" 20 (List.length expected_kept);
  let kept, deleted = Retention.keep ~now sixty_one_dailies in
  Alcotest.(check (list string))
    "the 14 newest and the six older Sundays" expected_kept kept;
  (* 61 - 20 = 41: the complement, with nothing invented and nothing lost *)
  Alcotest.(check int) "41 deleted" 41 (List.length deleted);
  Alcotest.(check (list string))
    "and they are exactly the rest"
    (List.filter sixty_one_dailies ~f:(fun n ->
         not (List.mem expected_kept n ~equal:String.equal)))
    deleted;
  Alcotest.(check int)
    "the two halves cover the input" 61
    (List.length kept + List.length deleted)

(* The rule's two constants, so a change to either is a change to a test and
   not a silent change to how long a restore can reach back. *)
let test_the_retention_constants_are_the_rulings () =
  Alcotest.(check int) "14 dailies" 14 Retention.daily_kept;
  Alcotest.(check int) "8 Sundays" 8 Retention.sundays_kept;
  Alcotest.(check int) "5 pre-deploy copies" 5 Retention.pre_deploy_kept

(* Seven pre-deploy copies, the five newest kept. They carry no weekday rule:
   a pre-deploy copy is the file a rollback reaches for, and five is two more
   than the number of deploys any one day has seen. Newest is greatest by
   name, which for these date-stamped names is newest by date: 10-26, 10-25,
   10-24, 10-23, 10-22 kept; 10-21 and 10-20 deleted. *)
let test_retention_keeps_the_five_newest_pre_deploy_copies () =
  let names =
    List.map [ "20"; "21"; "22"; "23"; "24"; "25"; "26" ] ~f:(fun d ->
        sprintf "pre-deploy-2026-10-%s.db" d)
  in
  let kept, deleted = Retention.keep ~now names in
  Alcotest.(check (list string))
    "the five newest"
    [
      "pre-deploy-2026-10-22.db";
      "pre-deploy-2026-10-23.db";
      "pre-deploy-2026-10-24.db";
      "pre-deploy-2026-10-25.db";
      "pre-deploy-2026-10-26.db";
    ]
    kept;
  Alcotest.(check (list string))
    "the two oldest"
    [ "pre-deploy-2026-10-20.db"; "pre-deploy-2026-10-21.db" ]
    deleted

(* Two things this rule never deletes.

   A name it does not recognise: a pruner that deletes what it cannot parse
   deletes the wrong thing exactly once. "desk-latest.db" is not a date at all,
   "README" is somebody's note, and "desk-20260901.db" is the trap -- Core's
   [Date.of_string] accepts the compact form, so without the ten-character check
   in [classify] that file would be read as 2026-09-01 and compete for one of
   the 14 places with the "desk-2026-09-01.db" this actually writes.

   A daily dated after `now`: a clock that ran backwards, or a copy carried in
   by hand from another host. It is kept, and it does not consume one of the 14
   places -- so the 61 real dailies are pruned to the same 20 as above and the
   two November files are kept beside them: 20 + 2 = 22 kept, 41 deleted.

   TWO November files, not one, and the reason is worth writing down. With one,
   a broken guard would let it take a place, pushing 10-18 out of the 14 -- but
   10-18 is itself a Sunday, so the Sunday tier would take it straight back and
   the answer would be identical. Two pushes 10-19 out as well, and 10-19 is a
   Monday, which nothing takes back. A mutation run found this: the one-file
   version of this case passed with the guard deleted. *)
let test_retention_keeps_what_it_cannot_parse_and_what_postdates_now () =
  let odd = [ "README"; "desk-20260901.db"; "desk-latest.db" ] in
  let kept, deleted = Retention.keep ~now (odd @ sixty_one_dailies) in
  Alcotest.(check (list string))
    "the 20, plus the three it will not judge"
    (List.sort (expected_kept @ odd) ~compare:String.compare)
    kept;
  Alcotest.(check int) "still 41 deleted" 41 (List.length deleted);
  let november = [ "desk-2026-11-01.db"; "desk-2026-11-02.db" ] in
  let kept, deleted = Retention.keep ~now (november @ sixty_one_dailies) in
  Alcotest.(check (list string))
    "November's two are kept and cost no place"
    (List.sort (november @ expected_kept) ~compare:String.compare)
    kept;
  Alcotest.(check int) "and 41 are still deleted" 41 (List.length deleted)

let suite =
  ( "journal backup",
    [
      Alcotest.test_case "a live journal round trips through the backup" `Quick
        test_a_live_journal_round_trips_through_the_backup;
      Alcotest.test_case "the backup only reads the source" `Quick
        test_the_backup_only_reads_the_source;
      Alcotest.test_case "a read-only handle refuses a write" `Quick
        test_a_read_only_handle_refuses_a_write;
      Alcotest.test_case "a read-only open creates nothing" `Quick
        test_a_read_only_open_creates_nothing;
      Alcotest.test_case "the copy is group-readable and no tmp survives" `Quick
        test_the_copy_is_group_readable_and_no_tmp_survives;
      Alcotest.test_case "verify reports the version, counts and newest rows" `Quick
        test_verify_reports_the_version_the_counts_and_the_newest_rows;
      Alcotest.test_case "verify reports an empty journal as empty, not broken" `Quick
        test_verify_reports_an_empty_journal_as_empty_not_broken;
      Alcotest.test_case "verify never calls a truncated copy clean" `Quick
        test_verify_never_calls_a_truncated_copy_clean;
      Alcotest.test_case "the verdict names every kind of trouble" `Quick
        test_the_verdict_names_every_kind_of_trouble;
      Alcotest.test_case "verify names a missing table" `Quick
        test_verify_names_a_missing_table;
      Alcotest.test_case "verify names a schema version this build does not know" `Quick
        test_verify_names_a_schema_version_this_build_does_not_know;
      Alcotest.test_case "tables is the schema's eleven, in order" `Quick
        test_tables_is_the_schemas_eleven_in_order;
      Alcotest.test_case "the reckoning day is a Saturday" `Quick
        test_the_reckoning_day_is_a_saturday;
      Alcotest.test_case "retention keeps twenty of sixty-one dailies" `Quick
        test_retention_keeps_twenty_of_sixty_one_dailies;
      Alcotest.test_case "the retention constants are the ruling's" `Quick
        test_the_retention_constants_are_the_rulings;
      Alcotest.test_case "retention keeps the five newest pre-deploy copies" `Quick
        test_retention_keeps_the_five_newest_pre_deploy_copies;
      Alcotest.test_case "retention keeps what it cannot parse and what postdates now"
        `Quick test_retention_keeps_what_it_cannot_parse_and_what_postdates_now;
    ] )
