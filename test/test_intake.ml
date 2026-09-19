(* The signal intake, one pass at a time, and never the scheduler.

   Each case writes signal files into a directory of its own, records the
   sessions it needs in an in-memory journal, and runs [Intake.pass] by hand:
   the pass is synchronous, so every judgement is recorded before the call
   returns and the case reads the journal directly. The minute loop, which
   does need the scheduler, is test/desk_async's.

   Two strategies unless a case says otherwise: exp_a01_spy on SPY, LIVE, and
   exp_a01_tlt on TLT, advisory, each at half the capital and max_age 3. The
   universe is SPY, TLT and QQQ, so QQQ is a name R7 allows and neither
   strategy owns. Session dates are real weekdays of September 2026: the 14th
   is a Monday and the 18th a Friday. *)

open Core
open Ohcamel.Types
module Signals_spec = Ohcamel.Config.Book.Signals_spec
module Book = Ohcamel.Config.Book
module Server = Ohcamel.Server
module Graph = Ohcamel.Graph
module D = Ohcamel_desk
module Journal = D.Journal
module Intake = D.Intake
module Verdict = Journal.Signal.Verdict

let at = Time_ns.of_string_with_utc_offset "2026-09-18T23:16:00Z"
let spy = Symbol.of_string "SPY"
let universe = List.map [ "SPY"; "TLT"; "QQQ" ] ~f:Symbol.of_string

let strategy ?(sizing = Signals_spec.Advisory) ?(max_age = 3) name symbols =
  { Signals_spec.Strategy.name; symbols; max_age; sizing; capital_fraction = 0.5 }

let spy_live = strategy ~sizing:Signals_spec.Live "exp_a01_spy" [ "SPY" ]
let tlt_advisory = strategy "exp_a01_tlt" [ "TLT" ]
let zeros = "sha256:" ^ String.make 64 '0'

(* One document, as the research service writes it, with every field the
   schema requires. *)
let doc ?(schema_version = 1) ?(strategy = "exp_a01_spy") ?(sequence = 1)
    ?(status = "pass") ?(targets = [ ("SPY", 1.0) ]) as_of =
  Yojson.Safe.to_string
    (`Assoc
       [
         ("schema_version", `Int schema_version);
         ("strategy", `String strategy);
         ("params_hash", `String zeros);
         ("as_of", `String as_of);
         ("computed_at", `String "2026-09-18T23:15:00Z");
         ("data_hash", `String zeros);
         ("sequence", `Int sequence);
         ( "validation",
           `Assoc
             [
               ("status", `String status);
               ("gates_version", `String "2026-09-02");
               ("dsr", `Float 0.31);
               ("psr", `Float 0.72);
               ("pbo", `Float 0.2);
               ("manifest", `String "manifest.exp_a01_spy.json");
             ] );
         ( "targets",
           `List
             (List.map targets ~f:(fun (s, w) ->
                  `Assoc [ ("symbol", `String s); ("weight", `Float w) ])) );
       ])

let write dir name text = Out_channel.write_all (Filename.concat dir name) ~data:text

(* Removes what a case left: files, a symlink, a directory named *.json. *)
let remove_all dir =
  if Stdlib.Sys.file_exists dir then (
    Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
        let path = Filename.concat dir name in
        match Core_unix.lstat path with
        | { st_kind = S_DIR; _ } -> Core_unix.rmdir path
        | _ -> Core_unix.unlink path);
    Core_unix.rmdir dir)

let with_dir ~f =
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  Exn.protect ~f:(fun () -> f dir) ~finally:(fun () -> remove_all dir)

let session journal d =
  Journal.record_session journal
    {
      Journal.Session.date = Date.of_string d;
      equity_close = 100_000.0;
      cash_close = 100_000.0;
      gross_close = 0.0;
      net_close = 0.0;
      recorded_at = at;
    }

type fixture = {
  journal : Journal.t;
  dir : string;
  intake : Intake.t;
  events : string Queue.t;
}

let with_intake ?(strategies = [ spy_live; tlt_advisory ]) ?(sessions = []) ~f () =
  with_dir ~f:(fun dir ->
      let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
      List.iter sessions ~f:(session journal);
      let events = Queue.create () in
      let intake =
        Intake.create ~journal ~dir ~strategies ~universe ~on_event:(Queue.enqueue events)
          ~now:(fun () -> at)
      in
      Exn.protect
        ~f:(fun () -> f { journal; dir; intake; events })
        ~finally:(fun () -> Journal.close journal))

(* A judgement as (verdict, rule), or None when nothing is recorded. *)
let judged f strategy sequence =
  Option.map (Journal.signal f.journal ~strategy ~sequence) ~f:(fun s ->
      (Verdict.to_string s.Journal.Signal.verdict, s.Journal.Signal.rule))

let judgement = Alcotest.(option (pair string (option string)))

let detail f strategy sequence =
  match Journal.signal f.journal ~strategy ~sequence with
  | Some s -> s.Journal.Signal.detail
  | None -> Alcotest.failf "%s %d was not judged" strategy sequence

let contains what ~substring s =
  Alcotest.(check bool)
    (sprintf "%s: %S contains %S" what s substring)
    true
    (String.is_substring s ~substring)

let week = [ "2026-09-14"; "2026-09-15"; "2026-09-16"; "2026-09-17"; "2026-09-18" ]

(* ------------------------------------------------------------------------ *)
(* The book's signals block                                                  *)
(* ------------------------------------------------------------------------ *)

let book signals =
  sprintf
    "((cash 100000.0) (positions (((symbol SPY) (sector INDEX) (qty 0.0)) ((symbol TLT) \
     (sector TREASURIES) (qty 0.0)))) (limits ()) %s)"
    signals

let test_a_signals_block_parses_and_sizing_defaults_to_advisory () =
  let plain = Or_error.ok_exn (Book.of_string (book "")) in
  Alcotest.(check bool) "absent: no block" true (Option.is_none plain.Book.signals);
  let given =
    Or_error.ok_exn
      (Book.of_string
         (book
            "(signals ((strategies (((name exp_a01_spy) (symbols (SPY)) \
             (capital_fraction 0.5)) ((name exp_a01_tlt) (symbols (TLT)) (max_age 5) \
             (sizing live) (capital_fraction 1.0))))))"))
  in
  match given.Book.signals with
  | None -> Alcotest.fail "the block was dropped"
  | Some { Signals_spec.strategies = [ a; b ] } ->
      Alcotest.(check string)
        "sizing absent is advisory" "advisory"
        (Signals_spec.sizing_to_string a.sizing);
      Alcotest.(check int) "max_age absent is 3, the README's default" 3 a.max_age;
      Alcotest.(check string)
        "sizing live, as written" "live"
        (Signals_spec.sizing_to_string b.sizing);
      Alcotest.(check int) "max_age 5, as written" 5 b.max_age
  | Some _ -> Alcotest.fail "not two strategies"

(* Each refusal names what it refused. The live sum: 0.6 + 0.5 = 1.1 > 1; the
   same two with one advisory are 0.6 <= 1 and parse. *)
let test_a_signals_block_is_refused_when_it_cannot_be_fed () =
  let refused what signals ~naming =
    match Book.of_string (book (sprintf "(signals ((strategies (%s))))" signals)) with
    | Ok _ -> Alcotest.failf "%s was accepted" what
    | Error e -> contains what ~substring:naming (Error.to_string_hum e)
  in
  let s ?(name = "exp_a") ?(symbols = "(SPY)") ?(extra = "") fraction =
    sprintf "((name %s) (symbols %s) (capital_fraction %s)%s)" name symbols fraction extra
  in
  refused "an upper-case name" (s ~name:"Exp_a" "0.5") ~naming:"^[a-z][a-z0-9_]{1,63}$";
  refused "a one-letter name" (s ~name:"e" "0.5") ~naming:"^[a-z][a-z0-9_]{1,63}$";
  refused "a symbol outside the universe" (s ~symbols:"(QQQ)" "0.5")
    ~naming:"QQQ is not in the book's universe";
  refused "no symbols" (s ~symbols:"()" "0.5") ~naming:"symbols is empty";
  refused "a symbol twice" (s ~symbols:"(SPY SPY)" "0.5") ~naming:"lists SPY twice";
  refused "a zero fraction" (s "0.0") ~naming:"capital_fraction must be in (0, 1]";
  refused "a fraction over 1" (s "1.5") ~naming:"capital_fraction must be in (0, 1]";
  refused "a NaN fraction" (s "nan") ~naming:"capital_fraction must be in (0, 1]";
  refused "a negative max_age"
    (s ~extra:" (max_age -1)" "0.5")
    ~naming:"max_age may not be negative";
  refused "two strategies of one name"
    (s "0.1" ^ " " ^ s ~symbols:"(TLT)" "0.1")
    ~naming:"two strategies have this name";
  refused "live fractions over 1"
    (s ~extra:" (sizing live)" "0.6"
    ^ " "
    ^ s ~name:"exp_b" ~symbols:"(TLT)" ~extra:" (sizing live)" "0.5")
    ~naming:"sum to 1.1";
  refused "a sizing that is neither"
    (s ~extra:" (sizing sometimes)" "0.5")
    ~naming:"sometimes";
  match
    Book.of_string
      (book
         (sprintf "(signals ((strategies (%s %s))))"
            (s ~extra:" (sizing live)" "0.6")
            (s ~name:"exp_b" ~symbols:"(TLT)" "0.5")))
  with
  | Ok _ -> ()
  | Error e ->
      Alcotest.failf "an advisory fraction was counted against the live sum: %s"
        (Error.to_string_hum e)

(* ------------------------------------------------------------------------ *)
(* The directory                                                             *)
(* ------------------------------------------------------------------------ *)

let test_no_directory_means_no_intake () =
  Alcotest.(check (result (option string) string))
    "unset: no directory" (Ok None) (Intake.directory None);
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let signals = Some { Signals_spec.strategies = [ spy_live ] } in
  let off what = function
    | Intake.Off why -> why
    | Intake.Running _ -> Alcotest.failf "%s: an intake runs" what
  in
  contains "no directory, a signals block" ~substring:"OHCAMEL_SIGNALS_DIR is not set"
    (off "no directory"
       (Intake.setup ~journal ~universe ~signals ~dir:None ~on_event:ignore
          ~now:(fun () -> at)));
  contains "a directory, no signals block" ~substring:"the book has no signals block"
    (off "no block"
       (Intake.setup ~journal ~universe ~signals:None ~dir:(Some "/tmp") ~on_event:ignore
          ~now:(fun () -> at)));
  (match
     Intake.setup ~journal ~universe ~signals ~dir:(Some "/tmp") ~on_event:ignore
       ~now:(fun () -> at)
   with
  | Intake.Running _ -> ()
  | Intake.Off why -> Alcotest.failf "both halves, and no intake: %s" why);
  Journal.close journal

let test_a_directory_set_but_unreadable_refuses_naming_the_variable () =
  let refused what raw =
    match Intake.directory raw with
    | Ok _ -> Alcotest.failf "%s was accepted" what
    | Error why -> contains what ~substring:"OHCAMEL_SIGNALS_DIR" why
  in
  refused "set but empty" (Some "");
  refused "set but blank" (Some "  ");
  refused "a path that does not exist" (Some "/nonexistent/ohcamel-signals");
  with_dir ~f:(fun dir ->
      let file = Filename.concat dir "not-a-directory" in
      write dir "not-a-directory" "x";
      refused "a file, not a directory" (Some file);
      Alcotest.(check (result (option string) string))
        "a readable directory" (Ok (Some dir)) (Intake.directory (Some dir));
      Core_unix.chmod dir ~perm:0o000;
      Exn.protect
        ~f:(fun () ->
          (* A superuser lists it anyway, and this case says nothing then. *)
          if Core_unix.getuid () <> 0 then
            refused "a directory it may not list" (Some dir))
        ~finally:(fun () -> Core_unix.chmod dir ~perm:0o700))

(* ------------------------------------------------------------------------ *)
(* The verdicts                                                              *)
(* ------------------------------------------------------------------------ *)

(* One file per verdict, in one pass: the live strategy's passing signal is
   accepted; the advisory strategy's failed one is advisory at R6 and its
   passing one advisory by sizing; an unregistered strategy is rejected at
   R2. Every row carries its document whole. *)
let test_one_file_per_verdict () =
  with_intake ~sessions:week
    ~f:(fun f ->
      let accepted = doc ~sequence:20260918 "2026-09-18" in
      write f.dir "exp_a01_spy-2026-09-18.json" accepted;
      write f.dir "exp_a01_tlt-2026-09-17.json"
        (doc ~strategy:"exp_a01_tlt" ~sequence:20260917 ~status:"fail"
           ~targets:[ ("TLT", 1.0) ]
           "2026-09-17");
      write f.dir "exp_a01_tlt-2026-09-18.json"
        (doc ~strategy:"exp_a01_tlt" ~sequence:20260918
           ~targets:[ ("TLT", 1.0) ]
           "2026-09-18");
      write f.dir "ghost-2026-09-18.json"
        (doc ~strategy:"ghost" ~sequence:20260918 "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "live, passing: accepted"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 20260918);
      Alcotest.check judgement "validation failed: advisory at R6"
        (Some ("advisory", Some "R6"))
        (judged f "exp_a01_tlt" 20260917);
      contains "the R6 detail says R7 was not reached" ~substring:"R7 was not reached"
        (detail f "exp_a01_tlt" 20260917);
      Alcotest.check judgement "advisory strategy, passing: advisory by sizing"
        (Some ("advisory", Some "sizing"))
        (judged f "exp_a01_tlt" 20260918);
      Alcotest.check judgement "unregistered: rejected at R2"
        (Some ("rejected", Some "R2"))
        (judged f "ghost" 20260918);
      Alcotest.(check (option string))
        "the document, whole" (Some accepted)
        (Option.map (Journal.signal f.journal ~strategy:"exp_a01_spy" ~sequence:20260918)
           ~f:(fun s -> s.Journal.Signal.document));
      Alcotest.(check int) "nothing waits" 0 (Intake.deferred f.intake))
    ()

(* Ruling 2: a signal that passes everything is still advisory while its
   strategy is, and the detail says it is never sized. An advisory judgement
   passed R1-R5, so it is R5's baseline too: a lower sequence after it is a
   replay. *)
let test_an_advisory_strategy's_passing_signal_is_advisory_by_sizing () =
  with_intake
    ~strategies:[ strategy "exp_a01_spy" [ "SPY" ] ]
    ~sessions:week
    ~f:(fun f ->
      write f.dir "a.json" (doc ~sequence:5 "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "advisory, rule sizing"
        (Some ("advisory", Some "sizing"))
        (judged f "exp_a01_spy" 5);
      contains "the detail"
        ~substring:"sizing is advisory, so it is shown and never sized"
        (detail f "exp_a01_spy" 5);
      write f.dir "b.json" (doc ~sequence:4 "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "4 after an advisory 5: rejected at R5"
        (Some ("rejected", Some "R5"))
        (judged f "exp_a01_spy" 4))
    ()

(* R5's baseline is the highest accepted-or-advisory sequence: 5, accepted.
   Two files carry 5 in the first pass, and only the first is judged. A lower
   4 is rejected at R5. A third file carrying 5 is skipped -- no second row,
   no second log line, not deferred -- and a rejected 7 (at R4) does not
   raise the baseline, so 6 is then accepted. *)
let test_r5_rejects_a_lower_sequence_and_the_same_one_is_skipped () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "five-a.json" (doc ~sequence:5 "2026-09-18");
      write f.dir "five-b.json" (doc ~sequence:5 ~status:"fail" "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "5: accepted -- five-a.json, the first of the two by name"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 5);
      write f.dir "four.json" (doc ~sequence:4 "2026-09-18");
      write f.dir "five-again.json" (doc ~sequence:5 ~status:"fail" "2026-09-18");
      write f.dir "seven.json" (doc ~sequence:7 "2026-09-11");
      Intake.pass f.intake;
      Alcotest.check judgement "4: rejected at R5"
        (Some ("rejected", Some "R5"))
        (judged f "exp_a01_spy" 4);
      contains "R5's detail" ~substring:"sequence 4 is not after the last accepted 5"
        (detail f "exp_a01_spy" 4);
      Alcotest.check judgement "5: the first judgement stands, never re-judged"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 5);
      Alcotest.check judgement "7: rejected at R4"
        (Some ("rejected", Some "R4"))
        (judged f "exp_a01_spy" 7);
      write f.dir "six.json" (doc ~sequence:6 "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "6: a rejected 7 is no baseline"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 6);
      Alcotest.(check int)
        "one line per judgement: 5, then 4 and 7, then 6" 4
        (Queue.count f.events ~f:(String.is_substring ~substring:"exp_a01_spy "));
      Alcotest.(check int) "nothing waits" 0 (Intake.deferred f.intake))
    ()

(* In one pass, sequence order, not file order: "a-six.json" sorts before
   "b-five.json" by name, and judged in that order 5 would be a replay of 6.
   Judged 5 then 6, both pass. *)
let test_one_pass_judges_in_sequence_order_not_file_order () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "a-six.json" (doc ~sequence:6 "2026-09-18");
      write f.dir "b-five.json" (doc ~sequence:5 "2026-09-17");
      Intake.pass f.intake;
      Alcotest.check judgement "5: accepted"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 5);
      Alcotest.check judgement "6: accepted"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 6))
    ()

(* Two files, two kinds of failure: not JSON at all, and JSON without a
   sequence. Each is recorded once in signal_files and never read again: the
   second pass logs nothing about either, and the error recorded is the
   first pass's. *)
let test_a_malformed_file_and_a_parse_failure_are_each_recorded_once () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "garbage.json" "{ this is not json";
      let without_sequence =
        match Yojson.Safe.from_string (doc "2026-09-18") with
        | `Assoc fields ->
            Yojson.Safe.to_string
              (`Assoc
                 (List.filter fields ~f:(fun (k, _) -> not (String.equal k "sequence"))))
        | _ -> assert false
      in
      write f.dir "missing.json" without_sequence;
      Intake.pass f.intake;
      contains "not JSON" ~substring:"not JSON"
        (Option.value ~default:""
           (Journal.signal_file_error f.journal ~name:"garbage.json"));
      Alcotest.(check bool)
        "a missing field is recorded" true
        (Journal.signal_file_recorded f.journal ~name:"missing.json");
      write f.dir "garbage.json" (doc "2026-09-18");
      Intake.pass f.intake;
      Intake.pass f.intake;
      contains "the first error stands" ~substring:"not JSON"
        (Option.value ~default:""
           (Journal.signal_file_error f.journal ~name:"garbage.json"));
      Alcotest.(check int)
        "one line each, across three passes" 2
        (Queue.count f.events ~f:(String.is_substring ~substring:"is not a signal"));
      Alcotest.check judgement "and its later content is never judged" None
        (judged f "exp_a01_spy" 1))
    ()

(* Never followed, never read past a signal's length: a symlink to a valid
   signal, a directory named like one, and a file one byte over the bound are
   each recorded as refused. A file of exactly the bound is read. *)
let test_what_is_not_a_regular_file_or_is_too_large_is_refused () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "real.txt" (doc "2026-09-18");
      Core_unix.symlink
        ~target:(Filename.concat f.dir "real.txt")
        ~link_name:(Filename.concat f.dir "link.json");
      Core_unix.mkdir (Filename.concat f.dir "dir.json");
      write f.dir "large.json" (String.make (Intake.max_file_bytes + 1) ' ');
      write f.dir "exact.json" (String.make Intake.max_file_bytes ' ');
      Intake.pass f.intake;
      contains "a file of exactly the bound is read, and is not JSON"
        ~substring:"not JSON"
        (Option.value ~default:""
           (Journal.signal_file_error f.journal ~name:"exact.json"));
      List.iter [ "link.json"; "dir.json" ] ~f:(fun name ->
          contains name ~substring:"not a regular file"
            (Option.value ~default:"" (Journal.signal_file_error f.journal ~name)));
      contains "large.json" ~substring:"65537 bytes"
        (Option.value ~default:""
           (Journal.signal_file_error f.journal ~name:"large.json"));
      Alcotest.check judgement "the link's target is never judged" None
        (judged f "exp_a01_spy" 1))
    ()

(* ------------------------------------------------------------------------ *)
(* The clock                                                                 *)
(* ------------------------------------------------------------------------ *)

(* No session recorded: every file waits, a malformed one included, and
   nothing is written. The first session ends the wait for both. *)
let test_an_empty_sessions_table_defers_every_file () =
  with_intake
    ~f:(fun f ->
      write f.dir "a.json" (doc ~sequence:1 "2026-09-18");
      write f.dir "garbage.json" "nope";
      Intake.pass f.intake;
      Alcotest.(check int) "both wait" 2 (Intake.deferred f.intake);
      Alcotest.check judgement "nothing judged" None (judged f "exp_a01_spy" 1);
      Alcotest.(check bool)
        "nothing refused" false
        (Journal.signal_file_recorded f.journal ~name:"garbage.json");
      Alcotest.(check int) "and nothing logged" 0 (Queue.length f.events);
      session f.journal "2026-09-18";
      Intake.pass f.intake;
      Alcotest.check judgement "the first session: judged"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 1);
      Alcotest.(check bool)
        "and the malformed one recorded" true
        (Journal.signal_file_recorded f.journal ~name:"garbage.json");
      Alcotest.(check int) "nothing waits" 0 (Intake.deferred f.intake))
    ()

(* The service writes as_of 2026-09-18 before the desk records that close:
   deferred, not recorded, and judged at the next pass after the session. *)
let test_r3_defers_and_judges_once_a_session_appears () =
  with_intake
    ~sessions:[ "2026-09-16"; "2026-09-17" ]
    ~f:(fun f ->
      write f.dir "a.json" (doc ~sequence:20260918 "2026-09-18");
      Intake.pass f.intake;
      Alcotest.(check int) "deferred" 1 (Intake.deferred f.intake);
      Alcotest.check judgement "not recorded" None (judged f "exp_a01_spy" 20260918);
      Intake.pass f.intake;
      Alcotest.(check int) "still deferred a minute later" 1 (Intake.deferred f.intake);
      session f.journal "2026-09-18";
      Intake.pass f.intake;
      Alcotest.check judgement "judged once the close is recorded"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 20260918);
      Alcotest.(check int) "nothing waits" 0 (Intake.deferred f.intake))
    ()

(* as_of 2026-09-25, first seen with the latest session 2026-09-14. max_age is
   3: after 15, 16 and 17 -- three sessions, none on or after the 25th -- it
   is rejected at R3. After two it still waits. *)
let test_r3_deferral_ends_rejected_after_max_age_sessions () =
  with_intake ~sessions:[ "2026-09-14" ]
    ~f:(fun f ->
      write f.dir "future.json" (doc ~sequence:1 "2026-09-25");
      Intake.pass f.intake;
      List.iter [ "2026-09-15"; "2026-09-16" ] ~f:(fun d ->
          session f.journal d;
          Intake.pass f.intake;
          Alcotest.(check int) ("still waiting after " ^ d) 1 (Intake.deferred f.intake));
      Alcotest.check judgement "not yet recorded" None (judged f "exp_a01_spy" 1);
      session f.journal "2026-09-17";
      Intake.pass f.intake;
      Alcotest.check judgement "the third session: rejected at R3"
        (Some ("rejected", Some "R3"))
        (judged f "exp_a01_spy" 1);
      contains "the detail counts from the first look"
        ~substring:"latest session was 2026-09-14, and 3 sessions"
        (detail f "exp_a01_spy" 1);
      Alcotest.(check int) "nothing waits" 0 (Intake.deferred f.intake))
    ()

(* A deferred file is never recorded, so a restart reads it again and its
   count of sessions starts again from the latest one then. *)
let test_a_restart_reads_a_deferred_file_again_and_its_wait_restarts () =
  with_intake ~sessions:[ "2026-09-14" ]
    ~f:(fun f ->
      write f.dir "future.json" (doc ~sequence:1 "2026-09-25");
      Intake.pass f.intake;
      session f.journal "2026-09-15";
      session f.journal "2026-09-16";
      Intake.pass f.intake;
      let restarted =
        Intake.create ~journal:f.journal ~dir:f.dir ~strategies:[ spy_live; tlt_advisory ]
          ~universe ~on_event:ignore ~now:(fun () -> at)
      in
      session f.journal "2026-09-17";
      Intake.pass restarted;
      Alcotest.(check int)
        "three since the first look, one since the restart's: waiting" 1
        (Intake.deferred restarted);
      Alcotest.check judgement "not recorded" None (judged f "exp_a01_spy" 1))
    ()

(* The contract's own R4 limit on history: an as_of before the earliest
   recorded session has no age this clock can state. *)
let test_an_as_of_before_the_earliest_session_fails_r4 () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "old.json" (doc ~sequence:1 "2026-09-11");
      Intake.pass f.intake;
      Alcotest.check judgement "rejected at R4"
        (Some ("rejected", Some "R4"))
        (judged f "exp_a01_spy" 1);
      contains "the detail" ~substring:"older than the clock's history"
        (detail f "exp_a01_spy" 1))
    ()

(* Recorded: a week in July, then nothing for five weeks, then 17 and 18
   August. A signal dated 29 July, mid-outage, is 2 recorded sessions old --
   inside max_age 3 by the record alone. The weekdays after it up to the 18th:
   30, 31 July (2), 3-7 and 10-14 August (10), 17, 18 August (2) = 14, and
   14 - 2 = 12 > 3, so it is rejected at R4, naming the gap. A failed
   validation does not make it advisory: R4 comes before R6. A document that
   fails R1 as well is still reported at R1. *)
let test_a_five_week_outage_cannot_make_a_stale_signal_fresh () =
  with_intake
    ~sessions:
      [
        "2026-07-06";
        "2026-07-07";
        "2026-07-08";
        "2026-07-09";
        "2026-07-10";
        "2026-08-17";
        "2026-08-18";
      ]
    ~f:(fun f ->
      Alcotest.(check int)
        "14 weekdays after 29 July, up to 18 August" 14
        (Intake.weekdays_after ~as_of:(Date.of_string "2026-07-29")
           ~latest:(Date.of_string "2026-08-18"));
      write f.dir "mid-outage.json" (doc ~sequence:1 "2026-07-29");
      write f.dir "mid-outage-failed.json" (doc ~sequence:2 ~status:"fail" "2026-07-29");
      write f.dir "mid-outage-v2.json" (doc ~schema_version:2 ~sequence:3 "2026-07-29");
      write f.dir "fresh.json" (doc ~sequence:4 "2026-08-18");
      Intake.pass f.intake;
      Alcotest.check judgement "mid-outage: rejected at R4"
        (Some ("rejected", Some "R4"))
        (judged f "exp_a01_spy" 1);
      contains "the detail names the gap" ~substring:"the record has a gap"
        (detail f "exp_a01_spy" 1);
      contains "and the numbers" ~substring:"holds only 2 after it"
        (detail f "exp_a01_spy" 1);
      Alcotest.check judgement "failed validation too: still R4, not advisory"
        (Some ("rejected", Some "R4"))
        (judged f "exp_a01_spy" 2);
      Alcotest.check judgement "R1 first, as ever"
        (Some ("rejected", Some "R1"))
        (judged f "exp_a01_spy" 3);
      Alcotest.check judgement "the latest session's own signal: accepted"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 4))
    ()

(* The bound's edge, with the record holding only 8 and 18 September. From
   Friday the 11th to the 18th: 14-18 September, 5 weekdays, 5 - 2 = 3, not
   more than max_age 3: accepted. From Thursday the 10th: those and the 11th,
   6, and 6 - 2 = 4 > 3: rejected at R4. *)
let test_the_weekday_bound_at_its_edge () =
  with_intake
    ~sessions:[ "2026-09-08"; "2026-09-18" ]
    ~f:(fun f ->
      write f.dir "thu.json" (doc ~sequence:1 "2026-09-10");
      write f.dir "fri.json" (doc ~sequence:2 "2026-09-11");
      Intake.pass f.intake;
      Alcotest.check judgement "6 weekdays: rejected at R4"
        (Some ("rejected", Some "R4"))
        (judged f "exp_a01_spy" 1);
      Alcotest.check judgement "5 weekdays: accepted"
        (Some ("accepted", None))
        (judged f "exp_a01_spy" 2))
    ()

(* ------------------------------------------------------------------------ *)
(* The desk's own checks on the targets                                      *)
(* ------------------------------------------------------------------------ *)

(* TLT is in the universe, so R7 passes, but it is not exp_a01_spy's: an SPY
   signal must not size TLT. *)
let test_a_target_outside_the_strategy's_symbols_is_rejected () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "a.json" (doc ~sequence:1 ~targets:[ ("TLT", 1.0) ] "2026-09-18");
      write f.dir "b.json"
        (doc ~sequence:2 ~targets:[ ("SPY", 0.5); ("QQQ", 0.5) ] "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "TLT under exp_a01_spy: rejected, rule strategy"
        (Some ("rejected", Some "strategy"))
        (judged f "exp_a01_spy" 1);
      contains "the detail" ~substring:"TLT is not one of exp_a01_spy's symbols (SPY)"
        (detail f "exp_a01_spy" 1);
      Alcotest.check judgement "one name of two outside: rejected, rule strategy"
        (Some ("rejected", Some "strategy"))
        (judged f "exp_a01_spy" 2))
    ()

(* [SPY 0.5; SPY 0.5] passes the contract's per-weight and sum checks. It is
   rejected at R7 -- where R7 is applied: the same targets on a document that
   fails R6 are advisory, because R7 is never reached. *)
let test_a_symbol_named_twice_is_rejected_at_r7 () =
  with_intake ~sessions:week
    ~f:(fun f ->
      let twice = [ ("SPY", 0.5); ("SPY", 0.5) ] in
      write f.dir "a.json" (doc ~sequence:1 ~targets:twice "2026-09-18");
      write f.dir "b.json" (doc ~sequence:2 ~status:"fail" ~targets:twice "2026-09-18");
      Intake.pass f.intake;
      Alcotest.check judgement "rejected at R7"
        (Some ("rejected", Some "R7"))
        (judged f "exp_a01_spy" 1);
      contains "the detail" ~substring:"SPY is named twice" (detail f "exp_a01_spy" 1);
      Alcotest.check judgement "failing R6 first: advisory at R6"
        (Some ("advisory", Some "R6"))
        (judged f "exp_a01_spy" 2))
    ()

(* ------------------------------------------------------------------------ *)
(* /api/research                                                             *)
(* ------------------------------------------------------------------------ *)

let with_server ~extensions ~f =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 100_000.0)
      ~instruments:[ { Instrument.symbol = spy; sector = Sector.of_string "INDEX" } ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let server = Server.create ~extensions ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
      match
        Async.Deferred.peek
          (Server.dispatch server
             {
               Server.Request.meth = `GET;
               path = "/api/research";
               headers = Cohttp.Header.init ();
               body = "";
             })
      with
      | Some (response, `String body) ->
          Alcotest.(check int)
            "200" 200
            (Cohttp.Code.code_of_status (Cohttp.Response.status response));
          f (Yojson.Safe.from_string body)
      | _ -> Alcotest.fail "/api/research did not answer without the scheduler")

let keys = function
  | `Assoc fields -> List.map fields ~f:fst
  | _ -> Alcotest.fail "not an object"

let field json name = Yojson.Safe.Util.member name json

(* The live shape: both strategies, the SPY one with its latest judgement --
   the advisory one recorded after an accepted one, so "latest" is the order
   of recording -- and the TLT one with none; one file deferred; R8's
   sentence exactly. *)
let test_api_research's_shape () =
  with_intake ~sessions:week
    ~f:(fun f ->
      write f.dir "a.json" (doc ~sequence:1 "2026-09-17");
      Intake.pass f.intake;
      write f.dir "b.json" (doc ~sequence:2 ~status:"unvalidated" "2026-09-18");
      write f.dir "c.json" (doc ~sequence:3 "2026-09-21");
      Intake.pass f.intake;
      let strategies = [ spy_live; tlt_advisory ] in
      with_server
        ~extensions:
          (D.Desk.research_extensions ~journal:f.journal ~strategies
             ~intake:(Intake.Running f.intake) ~on_event:ignore) ~f:(fun json ->
          Alcotest.(check (list string))
            "the top level"
            [ "intake"; "reason"; "deferred"; "last_pass"; "strategies"; "r8" ]
            (keys json);
          Alcotest.(check string)
            "running" "running"
            (Yojson.Safe.Util.to_string (field json "intake"));
          Alcotest.(check int)
            "one file deferred" 1
            (Yojson.Safe.Util.to_int (field json "deferred"));
          Alcotest.(check string)
            "R8, exactly" "R8 (data hash) is not enforced; see the design §3.12"
            (Yojson.Safe.Util.to_string (field json "r8"));
          match Yojson.Safe.Util.to_list (field json "strategies") with
          | [ spy_json; tlt_json ] ->
              Alcotest.(check (list string))
                "a strategy"
                [ "name"; "symbols"; "max_age"; "sizing"; "capital_fraction"; "latest" ]
                (keys spy_json);
              Alcotest.(check string)
                "its sizing, in words" "live"
                (Yojson.Safe.Util.to_string (field spy_json "sizing"));
              Alcotest.(check (float 0.0))
                "its fraction" 0.5
                (Yojson.Safe.Util.to_number (field spy_json "capital_fraction"));
              let latest = field spy_json "latest" in
              Alcotest.(check (list string))
                "its latest judgement"
                [
                  "verdict";
                  "rule";
                  "detail";
                  "sequence";
                  "as_of";
                  "received_at";
                  "targets";
                  "validation";
                ]
                (keys latest);
              Alcotest.(check (list string))
                "sequence 2, advisory at R6, as of 18 September"
                [ "advisory"; "R6"; "2"; "2026-09-18" ]
                [
                  Yojson.Safe.Util.to_string (field latest "verdict");
                  Yojson.Safe.Util.to_string (field latest "rule");
                  Int.to_string (Yojson.Safe.Util.to_int (field latest "sequence"));
                  Yojson.Safe.Util.to_string (field latest "as_of");
                ];
              Alcotest.(check string)
                "the weights" {|[{"symbol":"SPY","weight":1.0}]|}
                (Yojson.Safe.to_string (field latest "targets"));
              Alcotest.(check string)
                "the validation block"
                {|{"status":"unvalidated","gates_version":"2026-09-02","dsr":0.31,"psr":0.72,"pbo":0.2,"manifest":"manifest.exp_a01_spy.json"}|}
                (Yojson.Safe.to_string (field latest "validation"));
              Alcotest.(check string)
                "TLT: nothing judged yet" "null"
                (Yojson.Safe.to_string (field tlt_json "latest"))
          | _ -> Alcotest.fail "not two strategies"))
    ()

(* The demo registers no strategy, and says why. *)
let test_api_research_on_the_demo () =
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  with_server
    ~extensions:
      (D.Desk.research_extensions ~journal ~strategies:[] ~intake:Intake.demo_status
         ~on_event:ignore) ~f:(fun json ->
      Alcotest.(check string)
        "the whole answer"
        {|{"intake":"off","reason":"no strategy is registered on the demo: its synthetic book holds neither SPY nor TLT, and adding them would move Figure 1","deferred":0,"last_pass":null,"strategies":[],"r8":"R8 (data hash) is not enforced; see the design §3.12"}|}
        (Yojson.Safe.to_string json));
  Journal.close journal

(* The latest judgement per strategy is one seek on signals_by_strategy, with
   no sort, however many judgements the table holds -- asked of SQLite's own
   planner, about the query the route runs -- and it is the one recorded
   last, not the one with the latest wall-clock stamp. *)
let test_the_latest_judgement_is_one_index_seek () =
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  Alcotest.(check (list string))
    "the plan"
    [ "SEARCH signals USING INDEX signals_by_strategy (strategy=?)" ]
    (Journal.For_testing.query_plan journal Journal.For_testing.latest_signal_sql
       [ Sqlite3.Data.TEXT "exp_a01_spy" ]);
  (* Latest is the order of recording: sequence 2, then a replayed 1 -- whose
     received_at is EARLIER, as a clock stepped back would make it -- and 1 is
     the latest. *)
  let row sequence received_at =
    {
      Journal.Signal.strategy = "exp_a01_spy";
      sequence;
      as_of = Date.of_string "2026-09-18";
      received_at;
      verdict = (if sequence = 2 then Verdict.Advisory else Verdict.Rejected);
      rule = Some (if sequence = 2 then "R6" else "R5");
      detail = "";
      document = "{}";
    }
  in
  Journal.record_signal journal (row 2 at);
  Journal.record_signal journal (row 1 (Time_ns.sub at (Time_ns.Span.of_hr 1.0)));
  Alcotest.(check (option int))
    "the one recorded last" (Some 1)
    (Option.map (Journal.latest_signal journal ~strategy:"exp_a01_spy") ~f:(fun s ->
         s.Journal.Signal.sequence));
  Alcotest.(check (option int))
    "and no other strategy's" None
    (Option.map (Journal.latest_signal journal ~strategy:"exp_a01_tlt") ~f:(fun s ->
         s.Journal.Signal.sequence));
  Journal.close journal

let suite =
  ( "intake",
    [
      Alcotest.test_case "a signals block parses, and sizing defaults to advisory" `Quick
        test_a_signals_block_parses_and_sizing_defaults_to_advisory;
      Alcotest.test_case "a signals block is refused when it cannot be fed" `Quick
        test_a_signals_block_is_refused_when_it_cannot_be_fed;
      Alcotest.test_case "no directory means no intake" `Quick
        test_no_directory_means_no_intake;
      Alcotest.test_case "a directory set but unreadable refuses, naming the variable"
        `Quick test_a_directory_set_but_unreadable_refuses_naming_the_variable;
      Alcotest.test_case "one file per verdict" `Quick test_one_file_per_verdict;
      Alcotest.test_case "an advisory strategy's passing signal is advisory by sizing"
        `Quick test_an_advisory_strategy's_passing_signal_is_advisory_by_sizing;
      Alcotest.test_case "R5 rejects a lower sequence, and the same one is skipped" `Quick
        test_r5_rejects_a_lower_sequence_and_the_same_one_is_skipped;
      Alcotest.test_case "one pass judges in sequence order, not file order" `Quick
        test_one_pass_judges_in_sequence_order_not_file_order;
      Alcotest.test_case "a malformed file and a parse failure are each recorded once"
        `Quick test_a_malformed_file_and_a_parse_failure_are_each_recorded_once;
      Alcotest.test_case "what is not a regular file, or is too large, is refused" `Quick
        test_what_is_not_a_regular_file_or_is_too_large_is_refused;
      Alcotest.test_case "an empty sessions table defers every file" `Quick
        test_an_empty_sessions_table_defers_every_file;
      Alcotest.test_case "R3 defers, and judges once a session appears" `Quick
        test_r3_defers_and_judges_once_a_session_appears;
      Alcotest.test_case "R3's deferral ends rejected after max_age sessions" `Quick
        test_r3_deferral_ends_rejected_after_max_age_sessions;
      Alcotest.test_case "a restart reads a deferred file again, and its wait restarts"
        `Quick test_a_restart_reads_a_deferred_file_again_and_its_wait_restarts;
      Alcotest.test_case "an as_of before the earliest session fails R4" `Quick
        test_an_as_of_before_the_earliest_session_fails_r4;
      Alcotest.test_case "a five-week outage cannot make a stale signal fresh" `Quick
        test_a_five_week_outage_cannot_make_a_stale_signal_fresh;
      Alcotest.test_case "the weekday bound at its edge" `Quick
        test_the_weekday_bound_at_its_edge;
      Alcotest.test_case "a target outside the strategy's symbols is rejected" `Quick
        test_a_target_outside_the_strategy's_symbols_is_rejected;
      Alcotest.test_case "a symbol named twice is rejected at R7" `Quick
        test_a_symbol_named_twice_is_rejected_at_r7;
      Alcotest.test_case "/api/research's shape" `Quick test_api_research's_shape;
      Alcotest.test_case "/api/research on the demo" `Quick test_api_research_on_the_demo;
      Alcotest.test_case "the latest judgement is one index seek" `Quick
        test_the_latest_judgement_is_one_index_seek;
    ] )
