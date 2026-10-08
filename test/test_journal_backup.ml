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

(* THE SOURCE IS NEVER THE DESTINATION (promoted from the reviewer's note (a)).

   `ohcamel journal-backup /data/desk.db /data --name desk.db` is a line an
   operator can type, and before this guard it returned Ok: the copy was
   written to desk.db.tmp and renamed onto the live journal's own name, so
   the file the desk had open was replaced under it -- a new inode, the mode
   silently 0644 -> 0640, and a -wal left describing pages of a file that no
   longer existed. The reviewer could not demonstrate data loss; a backup
   whose destination can be its own source is refused regardless. Three ways
   the destination can be the source, each tried here: the same path once
   every symlink is resolved, the same inode under another name, and the
   source's own -wal, -shm or -journal, which a rename onto would corrupt the
   journal for certain. Refused before anything is touched: no .tmp is
   written, and the source's inode and mode are what they were. *)
let test_backup_refuses_its_own_source () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let before = Core_unix.stat src in
      let names () =
        List.sort (Array.to_list (Sys_unix.readdir dir)) ~compare:String.compare
      in
      let listing = names () in
      let refused what dst =
        match Journal.backup ~src ~dst with
        | Ok () -> Alcotest.failf "%s: a backup onto its own source returned Ok" what
        | Error e ->
            Alcotest.(check bool)
              (what ^ ": the refusal names the source")
              true
              (String.is_substring e ~substring:src)
      in
      refused "the same path" src;
      (* the same file by another path: through a symlink to the directory *)
      let link = Filename.concat dir "link" in
      Core_unix.symlink ~target:dir ~link_name:link;
      refused "through a symlinked directory" (Filename.concat link "desk.db");
      (* the same inode under another name *)
      let hard = Filename.concat dir "hard.db" in
      Core_unix.link ~target:src ~link_name:hard ();
      refused "a hard link to the source" hard;
      (* and the source's own siblings, whether or not they exist yet: the
         -wal does, because the journal is in WAL mode with rows in it *)
      Alcotest.(check bool)
        "the -wal exists, so that case is a real file" true
        (Stdlib.Sys.file_exists (src ^ "-wal"));
      refused "the source's -wal" (src ^ "-wal");
      refused "the source's -shm" (src ^ "-shm");
      refused "the source's -journal" (src ^ "-journal");
      let after = Core_unix.stat src in
      Alcotest.(check int) "the source's inode is unchanged" before.st_ino after.st_ino;
      Alcotest.(check int) "and so is its mode" before.st_perm after.st_perm;
      Alcotest.(check (list string))
        "and nothing was written beside it"
        (List.sort (listing @ [ "hard.db"; "link" ]) ~compare:String.compare)
        (names ());
      (* the desk's handle still writes *)
      Journal.record_session j
        {
          Journal.Session.date = date "2026-09-15";
          equity_close = 101_000.0;
          cash_close = 40_000.0;
          gross_close = 90_000.0;
          net_close = 60_000.0;
          recorded_at = at;
        };
      Alcotest.(check int) "two sessions" 2 (Journal.session_count j);
      (* and a backup to a name of its own still works from the same directory *)
      backup_exn ~src ~dst:(Filename.concat dir "desk-2026-09-15.db");
      Journal.close j)

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

(* THE COPY IS CHECKED, on real corruption, one page at a time.

   Ruling 11: the copy is reopened and integrity_check'ed before rotation. The
   truncation case above cannot pin that pragma, because SQLite refuses a
   half-file at [prepare] and the verdict comes through the [Error] branch;
   the reviewer showed that replacing the pragma's answer with ["ok"] left
   every case green. This case pins it without knowing anything about the
   page layout of whichever SQLite the host linked: a real backup is taken,
   and for EVERY page after the first a copy is written with that one page
   zeroed and verified. SQLite has two possible answers to a zeroed page --
   it refuses the file at some later query, or it answers and
   integrity_check reports the page -- and neither may read as clean. At
   least one page must reach integrity_check's verdict (in its rows, or as the
   pragma's own failure on a SQLite that fails it), because that is the answer
   the mutation above forges: a b-tree root that no query after the pragma
   reads (the schema has seven declared indexes, and a row count walks at
   most one b-tree per table) is found by integrity_check alone. Which pages those are
   is SQLite's business and is not asserted.

   The page size is read from the file's own header rather than assumed:
   a big-endian 16-bit value at offset 16, where 1 means 65536 (SQLite file
   format, "The Database Header"). *)
let test_no_single_zeroed_page_ever_reads_clean () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let good = Filename.concat dir "good.db" in
      backup_exn ~src ~dst:good;
      Journal.close j;
      let whole = In_channel.read_all good in
      let page_size =
        match (Char.to_int whole.[16] lsl 8) lor Char.to_int whole.[17] with
        | 1 -> 65536
        | n -> n
      in
      let pages = String.length whole / page_size in
      Alcotest.(check int)
        "the copy is a whole number of pages" 0
        (String.length whole mod page_size);
      Alcotest.(check bool)
        "and more than one of them, so there is a page after the header" true (pages > 1);
      let broken = Filename.concat dir "broken.db" in
      let reported = ref [] and refused = ref [] in
      for page = 2 to pages do
        let bytes = Bytes.of_string whole in
        Bytes.fill bytes ~pos:((page - 1) * page_size) ~len:page_size '\000';
        Out_channel.write_all broken ~data:(Bytes.to_string bytes);
        match Journal.verify broken with
        | Ok r when Journal.Report.clean r ->
            Alcotest.failf "page %d of %d zeroed, and verify called the copy clean" page
              pages
        | Ok r ->
            Alcotest.(check bool)
              (sprintf "page %d zeroed: the problem is integrity_check's own" page)
              false
              (List.equal String.equal r.Journal.Report.integrity [ "ok" ]);
            reported := page :: !reported
        | Error e -> refused := (page, e) :: !refused
      done;
      Alcotest.(check int)
        "every page after the first was tried" (pages - 1)
        (List.length !reported + List.length !refused);
      (* The pragma's verdict is consumed if at least one zeroed page is either
         reported in its rows or refused BY the pragma itself. Which of the two a
         host gives depends on the SQLite it links: macOS's answers with rows,
         Ubuntu 24.04's 3.45 fails the pragma with SQLITE_CORRUPT. Either way a
         forged ["ok"] would let the pages only integrity_check reaches read
         clean, which the loop above refuses. *)
      let by_pragma =
        List.filter !refused ~f:(fun (_, e) ->
            String.is_substring e ~substring:"integrity_check")
      in
      Alcotest.(check bool)
        (sprintf
           "at least one zeroed page reaches integrity_check's verdict (%d reported, %d \
            refused by the pragma, %d refused in all of %d; first refusal: %s)"
           (List.length !reported) (List.length by_pragma) (List.length !refused)
           (pages - 1)
           (match List.last !refused with Some (_, e) -> e | None -> "none"))
        true
        (not (List.is_empty !reported && List.is_empty by_pragma));
      (* and the untouched copy still reads clean, so the loop above was
         testing the zeroing and not a broken fixture *)
      Alcotest.(check bool)
        "the good copy is clean" true
        (Journal.Report.clean (verify_exn good)))

(* THE COPY IS CHECKED, on answers of our own choosing.

   [problems_of] is the verdict as a pure function of the three answers
   [verify] collects, and this drives it directly for what only a pure
   function can pin cheaply: the wording of every problem sentence, and the
   order the report prints them in. It is NOT the pin on integrity_check
   itself -- the zeroed-page sweep above is, because this case says nothing
   about whether SQLite was asked.

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

(* THE SUNDAY CAP, IN BOTH DIRECTIONS (the reviewer's finding 3).

   The 61-day case above has six Sundays in its tail, fewer than the 8 the
   rule allows, so it cannot tell "the 8 most recent Sundays" from "every
   Sunday" or from "the 8 OLDEST Sundays" -- and a pruner that kept the eight
   oldest would still keep 22 files and look sane in a listing while the
   restore window silently became a fortnight plus whatever ancient Sundays
   lingered. This case has nineteen Sundays in its tail.

   Dailies 2026-06-01 .. 2026-10-31, `now` = Saturday 2026-10-31:

     the fixture        June 30 + July 31 + August 31 + September 30 +
                        October 31 = 153 names
     the 14 newest      10-18 .. 10-31, as before
     what is left       06-01 .. 10-17 = 139 names
     Sundays in those   2026-06-01 is a Monday: it is day 152 of the year,
                        151 days after Thursday 01-01, and 151 = 21*7 + 4,
                        so Thursday + 4. The first Sunday is therefore 06-07,
                        and the Sundays at or before 10-17 are 06-07, 06-14,
                        06-21, 06-28, 07-05, 07-12, 07-19, 07-26, 08-02,
                        08-09, 08-16, 08-23, 08-30, 09-06, 09-13, 09-20,
                        09-27, 10-04, 10-11: nineteen.
     the 8 most recent  10-11, 10-04, 09-27, 09-20, 09-13, 09-06, 08-30, 08-23
     kept               14 + 8 = 22
     deleted            153 - 22 = 131

   Dropping the cap would keep 08-16 and ten older Sundays (33 kept); taking
   the eight oldest would keep 06-07 .. 07-26 and delete 08-23 .. 10-11.
   Either changes the list below, which is written out in name order. *)
let one_hundred_fifty_three_dailies =
  let month first days = List.init days ~f:(fun i -> Date.add_days (date first) i) in
  List.map ~f:daily
    (month "2026-06-01" 30 @ month "2026-07-01" 31 @ month "2026-08-01" 31
   @ month "2026-09-01" 30 @ month "2026-10-01" 31)

let test_retention_keeps_the_eight_most_recent_sundays_and_no_more () =
  Alcotest.(check int)
    "the fixture is 153 names" 153
    (List.length one_hundred_fifty_three_dailies);
  Alcotest.(check string)
    "and begins on a Monday" "MON"
    (Day_of_week.to_string (Date.day_of_week (date "2026-06-01")));
  let kept, deleted = Retention.keep ~now one_hundred_fifty_three_dailies in
  Alcotest.(check (list string))
    "the 14 newest and the EIGHT MOST RECENT older Sundays"
    ([
       "desk-2026-08-23.db";
       "desk-2026-08-30.db";
       "desk-2026-09-06.db";
       "desk-2026-09-13.db";
       "desk-2026-09-20.db";
       "desk-2026-09-27.db";
       "desk-2026-10-04.db";
       "desk-2026-10-11.db";
     ]
    @ List.map (List.init 14 ~f:(fun i -> Date.add_days (date "2026-10-18") i)) ~f:daily)
    kept;
  Alcotest.(check int) "22 kept" 22 (List.length kept);
  Alcotest.(check int) "131 deleted" 131 (List.length deleted);
  Alcotest.(check bool)
    "the ninth Sunday back, 08-16, is deleted" true
    (List.mem deleted "desk-2026-08-16.db" ~equal:String.equal);
  Alcotest.(check bool)
    "and so is the oldest, 06-07" true
    (List.mem deleted "desk-2026-06-07.db" ~equal:String.equal)

(* A LEFTOVER IS NEVER A BACKUP (the reviewer's finding 1, reproduced).

   [Journal.backup] writes to [dst.tmp] and renames; SQLite makes [.tmp-wal],
   [.tmp-shm] and, while the copy leaves WAL mode, [.tmp-journal] beside it.
   A run killed between the open and the rename -- SIGKILL, an OOM kill, a
   host reboot -- leaves those behind under THAT day's name, and the next
   run's own tidying only knows its own name. So retention must know them:
   a name ending in the .tmp family is a leftover, deleted, and never a
   backup that competes for a place.

   The reviewer's fixture: seven pre-deploy copies 09-01 .. 09-07 and one
   zero-byte "pre-deploy-2026-09-08.db.tmp". Before the fix the leftover was
   classified pre-deploy, sorted newest by name, took one of the five places
   and evicted the good pre-deploy-2026-09-03.db. Expected now: the five
   newest real copies 09-03 .. 09-07 kept; 09-01, 09-02 and every leftover
   deleted. The daily leftovers ride along to pin the other half of the
   finding: a dead "desk-2026-09-19.db.tmp" with its -shm and -wal siblings,
   and a "-journal", from a run on a day that is not today. *)
let leftovers =
  [
    "pre-deploy-2026-09-08.db.tmp";
    "desk-2026-09-19.db.tmp";
    "desk-2026-09-19.db.tmp-shm";
    "desk-2026-09-19.db.tmp-wal";
    "desk-2026-09-20.db.tmp-journal";
  ]

let seven_pre_deploys =
  List.map [ "01"; "02"; "03"; "04"; "05"; "06"; "07" ] ~f:(fun d ->
      sprintf "pre-deploy-2026-09-%s.db" d)

let test_retention_never_keeps_a_leftover_and_never_lets_one_take_a_place () =
  let kept, deleted =
    Retention.keep ~now:(date "2026-09-24") (leftovers @ seven_pre_deploys)
  in
  Alcotest.(check (list string))
    "the five newest REAL pre-deploy copies, 09-03 among them"
    [
      "pre-deploy-2026-09-03.db";
      "pre-deploy-2026-09-04.db";
      "pre-deploy-2026-09-05.db";
      "pre-deploy-2026-09-06.db";
      "pre-deploy-2026-09-07.db";
    ]
    kept;
  Alcotest.(check (list string))
    "the two oldest copies and every leftover, whatever its date"
    (List.sort
       ([ "pre-deploy-2026-09-01.db"; "pre-deploy-2026-09-02.db" ] @ leftovers)
       ~compare:String.compare)
    deleted;
  (* a leftover is deleted even when it is the only thing in the directory:
     nothing about it is a backup *)
  let kept, deleted = Retention.keep ~now:(date "2026-09-24") leftovers in
  Alcotest.(check (list string)) "alone, nothing is kept" [] kept;
  Alcotest.(check int) "and all five go" 5 (List.length deleted);
  (* a bare -journal, -wal or -shm beside a FINISHED backup is not one of
     ours -- SQLite would need a hot journal to roll a partial write back, and
     deleting one under it is how a database is corrupted by hand -- so those
     stay unrecognised and kept. And a .tmp is a leftover ONLY over a daily or
     pre-deploy name: "notes.tmp" is somebody's file and "desk-latest.db.tmp"
     is over a stem this rule does not parse, so both are kept -- the .mli's
     promise that nothing unparseable is deleted, which a rule reading every
     .tmp as a leftover would break exactly there *)
  let odd =
    [
      "desk-2026-09-14.db-journal";
      "desk-2026-09-14.db-wal";
      "desk-latest.db.tmp";
      "notes.tmp";
    ]
  in
  let kept, deleted = Retention.keep ~now:(date "2026-09-24") odd in
  Alcotest.(check (list string))
    "a bare sibling and a .tmp over no backup are kept" odd kept;
  Alcotest.(check (list string)) "and nothing is deleted" [] deleted

(* ------------------------------------------------------------------------ *)
(* THE COMMANDS                                                              *)
(* ------------------------------------------------------------------------ *)

(* [ohcamel journal-backup] and [ohcamel journal-verify], driven through
   [Journal_cli] -- the bodies bin/main.ml prints and exits on. The reviewer
   showed that with those bodies in bin/, three things could each be deleted
   with the whole suite green: the verify-the-copy step that runs before the
   prune, the --name guard, and journal-verify's non-zero exit on a dirty
   report. Each is pinned below, on outcomes a test can read. *)
module Cli = Ohcamel_desk.Journal_cli

let plant dir name = Out_channel.write_all (Filename.concat dir name) ~data:""

let names_in dir =
  List.sort (Array.to_list (Sys_unix.readdir dir)) ~compare:String.compare

(* 2026-09-14, the day these cases reckon from: day 257 of 2026 (243 through
   August, plus 14), 256 days after Thursday 01-01, and 256 = 36*7 + 4, so a
   Monday. *)
let reckoning = date "2026-09-14"

let test_the_commands_reckoning_day_is_a_monday () =
  Alcotest.(check string)
    "2026-09-14" "MON"
    (Day_of_week.to_string (Date.day_of_week reckoning))

(* A file journal this build would not restore from: real rows, then the
   schema version rewritten to 99 underneath. [Journal.backup] copies it
   faithfully -- the backup API copies pages and judges nothing -- and
   [verify] then reports the version, which is how a copy fails its check
   without a byte of it being corrupt. *)
let a_journal_of_version_99 dir =
  let src = Filename.concat dir "desk.db" in
  let j = open_exn src in
  fill_a_journal j;
  Journal.close j;
  let db = Sqlite3.db_open src in
  ignore
    (Sqlite3.exec db "UPDATE meta SET value = '99' WHERE key = 'schema_version'"
      : Sqlite3.Rc.t);
  ignore (Sqlite3.db_close db : bool);
  src

(* journal-verify: status 0 and the report on a clean copy; on a dirty one,
   status 1 with the report still printed and its PROBLEM line the last thing
   on the screen, and one stderr line naming the file; on a file that cannot
   be read, status 1 and nothing on stdout. *)
let test_journal_verify_exits_non_zero_on_a_dirty_report () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let good = Filename.concat dir "good.db" in
      backup_exn ~src ~dst:good;
      Journal.close j;
      let o = Cli.verify good in
      Alcotest.(check int) "a clean copy: status 0" 0 o.Cli.Outcome.status;
      Alcotest.(check (option string)) "and nothing on stderr" None o.Cli.Outcome.err;
      Alcotest.(check (list string))
        "stdout is the report, line for line"
        (Journal.Report.lines (verify_exn good))
        o.Cli.Outcome.out;
      let db = Sqlite3.db_open good in
      ignore
        (Sqlite3.exec db "UPDATE meta SET value = '99' WHERE key = 'schema_version'"
          : Sqlite3.Rc.t);
      ignore (Sqlite3.db_close db : bool);
      let o = Cli.verify good in
      Alcotest.(check int) "a dirty copy: status 1" 1 o.Cli.Outcome.status;
      Alcotest.(check bool)
        "the report is still printed, its PROBLEM line last" true
        (String.is_prefix
           (List.last_exn o.Cli.Outcome.out)
           ~prefix:"  PROBLEM: schema version 99");
      Alcotest.(check bool)
        "and the stderr line names the file" true
        (Option.value_map o.Cli.Outcome.err ~default:false
           ~f:(String.is_substring ~substring:good));
      let o = Cli.verify (Filename.concat dir "absent.db") in
      Alcotest.(check int) "a file that is not there: status 1" 1 o.Cli.Outcome.status;
      Alcotest.(check (list string)) "nothing on stdout" [] o.Cli.Outcome.out;
      Alcotest.(check bool) "the error on stderr" true (Option.is_some o.Cli.Outcome.err))

(* THE COMMAND, END TO END, with the reviewer's leftovers in the directory.

   The source lives in one temp directory and the backups in another, so the
   journal's own -wal and -shm are not in the listing the prune reads.
   Planted in DIR before the run, zero bytes each, because retention reads
   names and nothing else:

     desk-2026-09-01.db .. desk-2026-09-13.db   13 dailies
     desk-2026-08-31.db                          a Monday: day 243, 242 = 34*7 + 4
     desk-2026-08-30.db                          a Sunday: day 242, 241 = 34*7 + 3
     desk-2026-09-10.db.tmp, .tmp-shm            a killed run's leftovers, four days old
     pre-deploy-2026-09-01.db .. 07.db           seven rollback copies
     pre-deploy-2026-09-08.db.tmp                the reviewer's leftover
     README                                      somebody's note
     notes.tmp                                   somebody's .tmp, over no backup's name

   27 names, and the run writes a 28th, desk-2026-09-14.db (no --name, so
   today's). Retention at 2026-09-14:

     dailies at or before now   08-30, 08-31, 09-01 .. 09-14: 16
     the 14 newest              09-01 .. 09-14
     older                      08-31 (Monday, deleted), 08-30 (Sunday, kept)
     pre-deploy                 the five newest 09-03 .. 09-07 kept; 09-01, 09-02 deleted
     leftovers                  all three deleted
     README, notes.tmp          kept: unrecognised, and a .tmp is a leftover only
                                over a daily or pre-deploy name

   kept = 14 + 1 + 5 + 2 = 22, deleted = 1 + 2 + 3 = 6, and 22 + 6 = 28.
   Before finding 1 the leftover "pre-deploy-2026-09-08.db.tmp" took 09-03's
   place and the three leftovers stayed for ever; a rule that read every .tmp
   as a leftover would delete notes.tmp here. *)
let test_journal_backup_writes_todays_copy_verifies_it_and_prunes () =
  with_temp_dir ~f:(fun home ->
      with_temp_dir ~f:(fun dir ->
          let src = Filename.concat home "desk.db" in
          let j = open_exn src in
          fill_a_journal j;
          let september =
            List.init 13 ~f:(fun i -> daily (Date.add_days (date "2026-09-01") i))
          in
          List.iter september ~f:(plant dir);
          List.iter
            [
              "desk-2026-08-31.db";
              "desk-2026-08-30.db";
              "desk-2026-09-10.db.tmp";
              "desk-2026-09-10.db.tmp-shm";
              "pre-deploy-2026-09-08.db.tmp";
              "README";
              "notes.tmp";
            ]
            ~f:(plant dir);
          List.iter seven_pre_deploys ~f:(plant dir);
          Alcotest.(check int) "27 names before the run" 27 (List.length (names_in dir));
          let o = Cli.backup ~now:reckoning ~src ~dir ~name:None in
          Alcotest.(check (option string)) "nothing on stderr" None o.Cli.Outcome.err;
          Alcotest.(check int) "status 0" 0 o.Cli.Outcome.status;
          let dst = Filename.concat dir "desk-2026-09-14.db" in
          Alcotest.(check (list string))
            "the 22 kept are what is left in DIR, notes.tmp among them"
            (List.sort
               (september
               @ [ "desk-2026-09-14.db"; "desk-2026-08-30.db"; "README"; "notes.tmp" ]
               @ List.drop seven_pre_deploys 2)
               ~compare:String.compare)
            (names_in dir);
          Alcotest.(check bool)
            "today's copy is a real journal, and clean" true
            (Journal.Report.clean (verify_exn dst));
          Alcotest.(check int) "0640" 0o640 (Core_unix.stat dst).st_perm;
          (* what it printed: the write first, the tally last, the report and
             one line per deletion between *)
          Alcotest.(check string)
            "the first line is the write"
            (sprintf "ohcamel: wrote %s" dst)
            (List.hd_exn o.Cli.Outcome.out);
          Alcotest.(check string)
            "the last line is the tally"
            (sprintf "ohcamel: 22 kept, 6 deleted in %s" dir)
            (List.last_exn o.Cli.Outcome.out);
          Alcotest.(check (list string))
            "six deletions, each named"
            (List.map
               [
                 "desk-2026-08-31.db";
                 "desk-2026-09-10.db.tmp";
                 "desk-2026-09-10.db.tmp-shm";
                 "pre-deploy-2026-09-01.db";
                 "pre-deploy-2026-09-02.db";
                 "pre-deploy-2026-09-08.db.tmp";
               ] ~f:(fun n -> sprintf "ohcamel: deleted %s" (Filename.concat dir n)))
            (List.filter o.Cli.Outcome.out
               ~f:(String.is_prefix ~prefix:"ohcamel: deleted "));
          Alcotest.(check bool)
            "and the report is printed, with its integrity answer" true
            (List.exists o.Cli.Outcome.out ~f:(fun l ->
                 String.is_prefix l ~prefix:"  integrity_check"
                 && String.is_suffix l ~suffix:" ok"));
          Journal.close j))

(* VERIFY BEFORE PRUNE, and a copy that fails is not left under a daily's name.

   The source is a version-99 journal, so the copy is written whole and then
   fails its check. Planted: 25 dailies, 2026-08-20 .. 2026-09-13 (12 in
   August, 13 in September), eleven more than the window. A prune that ran
   would keep the 14 newest, 08-31 .. 09-13, and of the eleven older -- 08-20
   .. 08-30 -- the Sundays 08-23 (day 235, 234 = 33*7 + 3) and 08-30, deleting
   nine. Expected instead: all 25 still there, the copy gone, no .tmp
   anywhere, status 1, the report printed with its PROBLEM line, no tally and
   no "deleted" line at all. *)
let test_journal_backup_deletes_a_copy_that_fails_its_check_and_prunes_nothing () =
  with_temp_dir ~f:(fun home ->
      with_temp_dir ~f:(fun dir ->
          let src = a_journal_of_version_99 home in
          let planted =
            List.map ~f:daily
              (List.init 12 ~f:(fun i -> Date.add_days (date "2026-08-20") i)
              @ List.init 13 ~f:(fun i -> Date.add_days (date "2026-09-01") i))
          in
          List.iter planted ~f:(plant dir);
          Alcotest.(check int) "25 planted" 25 (List.length planted);
          let o = Cli.backup ~now:reckoning ~src ~dir ~name:None in
          Alcotest.(check int) "status 1" 1 o.Cli.Outcome.status;
          Alcotest.(check (list string))
            "every planted daily is still there, the copy is gone, and no .tmp"
            (List.sort planted ~compare:String.compare)
            (names_in dir);
          Alcotest.(check bool)
            "the report was printed, with the problem" true
            (List.exists o.Cli.Outcome.out
               ~f:(String.is_prefix ~prefix:"  PROBLEM: schema version 99"));
          Alcotest.(check (list string))
            "and nothing was deleted by a prune" []
            (List.filter o.Cli.Outcome.out
               ~f:(String.is_prefix ~prefix:"ohcamel: deleted "));
          Alcotest.(check bool)
            "no tally either" false
            (List.exists o.Cli.Outcome.out ~f:(String.is_substring ~substring:" kept, "));
          Alcotest.(check bool)
            "the stderr line names the copy and says it was deleted" true
            (Option.value_map o.Cli.Outcome.err ~default:false ~f:(fun e ->
                 String.is_substring e
                   ~substring:(Filename.concat dir "desk-2026-09-14.db")
                 && String.is_substring e ~substring:"deleted"))))

(* THE TWO REFUSALS, before anything is written.

   A NAME with a directory in it, or empty. The subdirectory "a" exists, so
   without the guard "a/b.db" would be written there successfully -- outside
   the prune's view -- and a case that only checked the status would pass for
   the wrong reason. And the source's own name in the source's own directory,
   which is the operator's `journal-backup /data/desk.db /data --name
   desk.db`, and its -wal. *)
let test_journal_backup_refuses_a_bad_name_and_its_own_source () =
  with_temp_dir ~f:(fun dir ->
      let src = Filename.concat dir "desk.db" in
      let j = open_exn src in
      fill_a_journal j;
      let sub = Filename.concat dir "a" in
      Core_unix.mkdir sub;
      let before = names_in dir and inode = (Core_unix.stat src).st_ino in
      let refused what name ~naming =
        let o = Cli.backup ~now:reckoning ~src ~dir ~name:(Some name) in
        Alcotest.(check int) (what ^ ": status 1") 1 o.Cli.Outcome.status;
        Alcotest.(check (list string)) (what ^ ": nothing printed") [] o.Cli.Outcome.out;
        Alcotest.(check bool)
          (what ^ ": the stderr line says why")
          true
          (Option.value_map o.Cli.Outcome.err ~default:false
             ~f:(String.is_substring ~substring:naming));
        Alcotest.(check (list string)) (what ^ ": nothing written") before (names_in dir)
      in
      refused "a name with a directory in it" "a/b.db" ~naming:"--name";
      Alcotest.(check bool)
        "and a/b.db was not written" false
        (Stdlib.Sys.file_exists (Filename.concat sub "b.db"));
      refused "an empty name" "" ~naming:"--name";
      refused "the source's own name" "desk.db" ~naming:src;
      refused "the source's -wal" "desk.db-wal" ~naming:src;
      Alcotest.(check int)
        "the source's inode is what it was" inode (Core_unix.stat src).st_ino;
      Alcotest.(check int) "and it still has its one session" 1 (Journal.session_count j);
      Journal.close j)

(* A DELETE THAT FAILS IS A FAILURE (plan item 3: non-zero on ANY failure).

   Planted: the 14 dailies 2026-08-31 .. 2026-09-13, and a DIRECTORY named
   "desk-2020-01-01.db" -- a name retention reads as a daily, and one that
   unlink cannot remove. The run writes desk-2026-09-14.db, so there are 16
   dailies at or before now; the 14 newest are 09-01 .. 09-14 and the two
   older go: 08-31, a Monday, and 2020-01-01, which the Sunday tier would
   take back if it were a Sunday and is a Wednesday -- 2024-01-01 was a
   Monday (the Saturday derivation above), so 2023-01-01 was Monday - 365
   mod 7 = Monday - 1 = Sunday, 2022-01-01 Saturday, 2021-01-01 Friday, and
   2020 is a leap year, so 2020-01-01 = Friday - 366 mod 7 = Friday - 2 =
   Wednesday. Sorted by name the directory comes first, so its unlink is the
   first the prune attempts, and it fails.

   Expected: status 1; the stderr line names the directory's path and says it
   cannot be deleted; no "deleted" line and no tally, because the prune stops
   at the first failure rather than deleting past it -- so 08-31, second in
   the deletion order, is still there, and the listing is exactly what was
   planted plus today's copy, which was written and verified before the prune
   began and stays. A prune that skipped the failure and went on would delete
   08-31, print "14 kept, 2 deleted" with the directory counted among the
   deleted, and exit 0. *)
let test_journal_backup_stops_at_a_delete_that_fails_and_exits_non_zero () =
  Alcotest.(check string)
    "2020-01-01 is a Wednesday, so not a Sunday the tier would keep" "WED"
    (Day_of_week.to_string (Date.day_of_week (date "2020-01-01")));
  with_temp_dir ~f:(fun home ->
      with_temp_dir ~f:(fun dir ->
          let src = Filename.concat home "desk.db" in
          let j = open_exn src in
          fill_a_journal j;
          let planted =
            List.init 14 ~f:(fun i -> daily (Date.add_days (date "2026-08-31") i))
          in
          List.iter planted ~f:(plant dir);
          let undeletable = Filename.concat dir "desk-2020-01-01.db" in
          Core_unix.mkdir undeletable;
          let o = Cli.backup ~now:reckoning ~src ~dir ~name:None in
          Alcotest.(check int) "status 1" 1 o.Cli.Outcome.status;
          Alcotest.(check bool)
            "the stderr line names the path that could not be deleted" true
            (Option.value_map o.Cli.Outcome.err ~default:false ~f:(fun e ->
                 String.is_substring e ~substring:"cannot delete"
                 && String.is_substring e ~substring:undeletable));
          Alcotest.(check (list string))
            "the listing is what was planted plus today's copy, 08-31 included"
            (List.sort
               ("desk-2020-01-01.db" :: "desk-2026-09-14.db" :: planted)
               ~compare:String.compare)
            (names_in dir);
          Alcotest.(check (list string))
            "nothing was deleted before the failure" []
            (List.filter o.Cli.Outcome.out
               ~f:(String.is_prefix ~prefix:"ohcamel: deleted "));
          Alcotest.(check bool)
            "and no tally" false
            (List.exists o.Cli.Outcome.out ~f:(String.is_substring ~substring:" kept, "));
          Alcotest.(check bool)
            "today's copy was written and verified before the prune, and stays" true
            (Journal.Report.clean (verify_exn (Filename.concat dir "desk-2026-09-14.db")));
          Journal.close j))

(* A NAME THE PRUNE WOULD DELETE IS REFUSED BEFORE THE COPY IS WRITTEN.

   `journal-backup SRC DIR --name desk-2020-01-02.db.tmp` wrote the copy,
   verified it clean, deleted it as a leftover in its own prune, printed
   "1 kept, 1 deleted" and exited 0 -- a backup command reporting success
   with no backup. Two more names do the same once DIR is full enough: a
   daily's name that fourteen newer dailies already rank out of the window,
   and a pre-deploy's name that five newer ones outrank. So the name is
   judged against what DIR holds, before anything is written.

   Planted: the 14 dailies 2026-08-31 .. 09-13 and the 5 pre-deploys
   2026-09-01 .. 05, nineteen names. Refused, each with status 1, nothing on
   stdout, a stderr line naming --name and the name, and the listing
   unchanged:

     desk-2020-01-02.db.tmp    a leftover's name, deleted whatever else is there
     desk-2020-01-02.db        with 14 newer dailies it is the fifteenth, and
                               a Thursday (2020-01-01 is a Wednesday, above),
                               so the Sunday tier does not take it either
     pre-deploy-2020-01-01.db  sixth by name among six; the five newer are kept

   And the deploy's own use of --name still works: "pre-deploy-2026-09-14.db"
   sorts newest of six, so it is written, verified and kept, and the prune
   that follows deletes the oldest, 09-01: 14 + 5 = 19 kept, 1 deleted, and
   the listing is the nineteen planted less 09-01 plus the new copy. *)
let test_journal_backup_refuses_a_name_its_own_prune_would_delete () =
  with_temp_dir ~f:(fun home ->
      with_temp_dir ~f:(fun dir ->
          let src = Filename.concat home "desk.db" in
          let j = open_exn src in
          fill_a_journal j;
          let dailies =
            List.init 14 ~f:(fun i -> daily (Date.add_days (date "2026-08-31") i))
          in
          let pre_deploys =
            List.map [ "01"; "02"; "03"; "04"; "05" ] ~f:(fun d ->
                sprintf "pre-deploy-2026-09-%s.db" d)
          in
          List.iter (dailies @ pre_deploys) ~f:(plant dir);
          let before = names_in dir in
          Alcotest.(check int) "19 planted" 19 (List.length before);
          let refused what name =
            let o = Cli.backup ~now:reckoning ~src ~dir ~name:(Some name) in
            Alcotest.(check int) (what ^ ": status 1") 1 o.Cli.Outcome.status;
            Alcotest.(check (list string))
              (what ^ ": nothing printed") [] o.Cli.Outcome.out;
            Alcotest.(check bool)
              (what ^ ": the stderr line names --name and the name")
              true
              (Option.value_map o.Cli.Outcome.err ~default:false ~f:(fun e ->
                   String.is_substring e ~substring:"--name"
                   && String.is_substring e ~substring:name));
            Alcotest.(check (list string))
              (what ^ ": nothing written, nothing pruned")
              before (names_in dir)
          in
          refused "a leftover's name" "desk-2020-01-02.db.tmp";
          refused "a daily's name fourteen newer dailies outrank" "desk-2020-01-02.db";
          refused "a pre-deploy's name five newer copies outrank"
            "pre-deploy-2020-01-01.db";
          (* the positive control: the deploy's own kind of name *)
          let o =
            Cli.backup ~now:reckoning ~src ~dir ~name:(Some "pre-deploy-2026-09-14.db")
          in
          Alcotest.(check (option string))
            "a name retention keeps: nothing on stderr" None o.Cli.Outcome.err;
          Alcotest.(check int) "and status 0" 0 o.Cli.Outcome.status;
          let dst = Filename.concat dir "pre-deploy-2026-09-14.db" in
          Alcotest.(check bool)
            "the copy is there, and clean" true
            (Journal.Report.clean (verify_exn dst));
          Alcotest.(check (list string))
            "the listing: the nineteen planted, less 09-01, plus the copy"
            (List.sort
               ("pre-deploy-2026-09-14.db"
               :: List.filter before ~f:(fun n ->
                   not (String.equal n "pre-deploy-2026-09-01.db")))
               ~compare:String.compare)
            (names_in dir);
          Alcotest.(check string)
            "19 kept, 1 deleted"
            (sprintf "ohcamel: 19 kept, 1 deleted in %s" dir)
            (List.last_exn o.Cli.Outcome.out);
          Journal.close j))

(* AN OLDER JOURNAL BACKS UP. Found 2026-10-08: the live host's journal was
   written by a build from before signal_files and signal_deferrals existed,
   and the new build's pre-deploy backup refused its faithful copy as "not a
   journal this build would restore from" -- every deploy that adds a table
   would have been blocked. The journal is additive: the build creates a
   missing table on open. So a table absent from the SOURCE too is reported,
   not a problem; a table the source has and the copy lacks still is. *)
let an_old_journal dir =
  let src = Filename.concat dir "desk.db" in
  let j = open_exn src in
  fill_a_journal j;
  Journal.close j;
  let db = Sqlite3.db_open src in
  List.iter [ "signal_files"; "signal_deferrals" ] ~f:(fun t ->
      ignore (Sqlite3.exec db ("DROP TABLE " ^ t) : Sqlite3.Rc.t));
  ignore (Sqlite3.db_close db : bool);
  src

let test_journal_backup_takes_an_older_journal_that_lacks_newer_tables () =
  with_temp_dir ~f:(fun dir ->
      let src = an_old_journal dir in
      let out = Filename.concat dir "backups" in
      Core_unix.mkdir out;
      let o = Cli.backup ~now:reckoning ~src ~dir:out ~name:None in
      Alcotest.(check int) "status 0: the copy is restorable" 0 o.Cli.Outcome.status;
      Alcotest.(check (list string))
        "the copy stays" [ "desk-2026-09-14.db" ] (names_in out);
      Alcotest.(check bool)
        "and the report says the two tables are absent in the source too" true
        (List.exists o.Cli.Outcome.out ~f:(fun l ->
             String.is_substring l ~substring:"signal_files"
             && String.is_substring l ~substring:"absent in the source"));
      List.iter (names_in out) ~f:(fun n -> Core_unix.unlink (Filename.concat out n));
      Core_unix.rmdir out)

let test_verify_against_a_source_stays_strict_for_tables_the_source_had () =
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
      let strict = Result.ok_or_failwith (Journal.verify ~absent_in_source:[] dst) in
      Alcotest.(check bool)
        "the source had alerts, the copy does not: not clean" false
        (Journal.Report.clean strict);
      let additive =
        Result.ok_or_failwith (Journal.verify ~absent_in_source:[ "alerts" ] dst)
      in
      Alcotest.(check bool)
        "absent in the source too: clean, and still listed" true
        (Journal.Report.clean additive
        && List.equal String.equal additive.Journal.Report.absent_in_source [ "alerts" ]))

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
      Alcotest.test_case "backup refuses its own source" `Quick
        test_backup_refuses_its_own_source;
      Alcotest.test_case "verify reports the version, counts and newest rows" `Quick
        test_verify_reports_the_version_the_counts_and_the_newest_rows;
      Alcotest.test_case "verify reports an empty journal as empty, not broken" `Quick
        test_verify_reports_an_empty_journal_as_empty_not_broken;
      Alcotest.test_case "verify never calls a truncated copy clean" `Quick
        test_verify_never_calls_a_truncated_copy_clean;
      Alcotest.test_case "no single zeroed page ever reads clean" `Quick
        test_no_single_zeroed_page_ever_reads_clean;
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
      Alcotest.test_case "retention keeps the eight most recent Sundays and no more"
        `Quick test_retention_keeps_the_eight_most_recent_sundays_and_no_more;
      Alcotest.test_case
        "retention never keeps a leftover and never lets one take a place" `Quick
        test_retention_never_keeps_a_leftover_and_never_lets_one_take_a_place;
      Alcotest.test_case "the commands' reckoning day is a Monday" `Quick
        test_the_commands_reckoning_day_is_a_monday;
      Alcotest.test_case "journal-verify exits non-zero on a dirty report" `Quick
        test_journal_verify_exits_non_zero_on_a_dirty_report;
      Alcotest.test_case "journal-backup writes today's copy, verifies it and prunes"
        `Quick test_journal_backup_writes_todays_copy_verifies_it_and_prunes;
      Alcotest.test_case
        "journal-backup deletes a copy that fails its check and prunes nothing" `Quick
        test_journal_backup_deletes_a_copy_that_fails_its_check_and_prunes_nothing;
      Alcotest.test_case "journal-backup refuses a bad name and its own source" `Quick
        test_journal_backup_refuses_a_bad_name_and_its_own_source;
      Alcotest.test_case "journal-backup stops at a delete that fails and exits non-zero"
        `Quick test_journal_backup_stops_at_a_delete_that_fails_and_exits_non_zero;
      Alcotest.test_case "journal-backup refuses a name its own prune would delete" `Quick
        test_journal_backup_refuses_a_name_its_own_prune_would_delete;
      Alcotest.test_case "journal-backup takes an older journal that lacks newer tables"
        `Quick test_journal_backup_takes_an_older_journal_that_lacks_newer_tables;
      Alcotest.test_case "verify against a source stays strict for tables the source had"
        `Quick test_verify_against_a_source_stays_strict_for_tables_the_source_had;
    ] )
