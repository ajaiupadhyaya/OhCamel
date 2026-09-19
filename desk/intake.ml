(* The intake: signal files in, judgements out (design §3.12).

   The research service writes one JSON document per signal into a directory
   the desk can read and never writes. Once a minute this reads that
   directory, judges each new document against the contract's rules R1-R7
   (contract.ml) and three checks of the desk's own, and records every
   judgement in the journal with the whole document beside it. It sizes
   nothing: an accepted signal is recorded as accepted, and Task 15 attaches
   the rebalance to that verdict.

   THIS IS A TRUST BOUNDARY, AND EVERY DOUBT IS A REFUSAL. The files come
   from another process in another language, and the contract's own principle
   is that the desk never trusts Python's timing, only its output. So:

   - A file that is not a regular file, is larger than a signal can be, is
     not valid UTF-8, is not JSON, or lacks a field is recorded once in
     signal_files and never read again, never guessed at, never retried. A
     symlink is never read: lstat refuses one, and if one is swapped in
     between lstat and open(2), which follows it, the opened file's
     (st_dev, st_ino) is not the one lstat saw and nothing is read from it.
     A failure to parse is recorded only when the file is unchanged since
     the pass before -- the same inode, size and mtime -- so a file caught
     half-written is read again, not thrown away. What is recorded is a
     sentence of the desk's with a position or a field's name, never the
     file's own bytes: a parser's message echoes what it choked on.
   - Names and strategies go into the log with %S, so a newline in either
     cannot forge a line of it.
   - A judged file is remembered by (name, inode, mtime, size) and not read
     again while it is unchanged, and at most [max_reads_per_pass] files are
     read in a pass, first by name. The memory is only an economy: the
     journal's (strategy, sequence) is what decides whether a document has
     been judged.
   - The only clock is the journal's sessions table: the closes the desk has
     itself recorded. No wall-clock date ever stands in for a session, so
     with none recorded yet every file waits, and nothing is recorded at all.
   - The clock's three facts -- the earliest session, the latest, and how
     many fall after a date -- come from ONE read of the table per pass. A
     COUNT and a MAX asked separately could straddle a close the recorder
     writes between them, and Contract.Clock.create raises when the latest
     bar is not the newest date its count sees.
   - A pass that raises is logged, and the next one runs a minute later. A
     single raise never stops the intake.

   THE ORDER OF JUDGEMENT is the contract's, R1 through R7 with the first
   failure reported, and three checks of the desk's slot in where their
   rules do:

   1. R3 IS DEFERRED, NEVER LOST TO A RACE, AND NEVER OUTWAITED. A document
      dated after the latest recorded session is not judged yet: the close
      it needs may land after the service writes, be retried, or be missed
      across a restart. It is looked at again each minute and judged as
      soon as a session on or after its as_of exists. Its first sighting --
      the latest session then -- is written to the journal's
      signal_deferrals by (strategy, sequence), once, and if max_age
      sessions are recorded after that one with none reaching its as_of, it
      is rejected at R3. Because the count is the journal's and keyed by the
      document, neither a restart nor a rename starts it again (a review
      found that restarting every two sessions got a file dated ahead of its
      data accepted at age 0). And a document so far ahead that no wait
      could end well -- more than max_age weekdays past the latest session,
      after the same slack of 2 the R4 bound allows -- is rejected at R3 at
      once.
   2. R4'S WEEKDAY BOUND. Contract.R4 counts recorded sessions after as_of,
      and the sessions record can have holes: the close is not recorded
      while the engine is down. After a five-week outage and two sessions
      since, a signal dated mid-outage would read two bars old. So a
      document that passes the contract's R4 is also measured in weekdays
      from its as_of to the latest session, and rejected AT R4 when that
      count, less a slack of 2, exceeds max_age. Holidays are weekdays with
      no session, so the count is only ever too high and the bound only
      ever errs stale; the slack keeps a window holding a holiday or two
      from refusing a signal the exchange calendar calls fresh. It runs
      after Contract.validate, and only when the contract's first failure
      came after R4 or there was none, so it never displaces R1-R3 and it
      is always reported as R4. The contract stays pure and unchanged.
   3. A SYMBOL NAMED TWICE is rejected AT R7. R7 is the rule on the targets'
      well-formedness ("malformed or over-levered targets", in
      interface/README.md's words), and [SPY 0.5; SPY 0.5] is exactly a
      malformed target list: it passes the contract's per-weight and sum
      checks while meaning nothing a rebalance could size. It is applied
      only where R7 is -- after R1-R6 pass -- so a document that fails R6
      is still advisory, as the contract's first-failure semantics say.
   4. THE STRATEGY'S OWN SYMBOLS, after the contract passes: a document
      naming a target outside its strategy's registered symbols is rejected,
      rule "strategy". An exp_a01_spy signal must not size TLT.

   THE VERDICTS:
   - accepted: passes all of it, and its strategy is (sizing live);
   - advisory: the contract's first failure is R6 -- R1-R5 passed, and R7
     was never reached -- or it passes all of it and its strategy is
     (sizing advisory). The rule column says which: "R6" or "sizing";
   - rejected: any other first failure, with its rule.

   R5's baseline is the highest sequence among the strategy's accepted and
   advisory judgements: both passed R1-R5, and a signal whose sequence is
   not above one the desk has already shown is a replay. A document whose
   (strategy, sequence) is already recorded is skipped, never judged a second
   time. Within a pass documents are judged in (strategy, sequence) order,
   and the baseline is read again before each one. *)

open Core
open Async
open Ohcamel.Types
module Signals_spec = Ohcamel.Config.Book.Signals_spec
module Verdict = Journal.Signal.Verdict

let env_var = "OHCAMEL_SIGNALS_DIR"

(* Ruling 3, served as it stands: §3.12 defers R8 until its hash recipe
   stops depending on how two languages print a float, and
   interface/README.md, ported verbatim, still lists it. *)
let r8_sentence = "R8 (data hash) is not enforced; see the design §3.12"

(* A signal is well under a kilobyte. A file past this is not one, and is
   refused without being read into memory. *)
let max_file_bytes = 64 * 1024

(* Weekdays less this may not exceed max_age: see the header's checks 1 and
   2. *)
let weekday_slack = 2
let every = Time_ns.Span.of_min 1.0

(* A pass reads at most this many files. The research service writes one a
   strategy a day, so only a backlog -- a restart onto a year of files, whose
   judgements the memory has not yet seen -- ever reaches it; the rest wait a
   minute, not the scheduler. *)
let max_reads_per_pass = 100

(* ------------------------------------------------------------------------ *)
(* The directory                                                             *)
(* ------------------------------------------------------------------------ *)

(* Unset is no intake. Set is a promise that there is a directory to read, so
   set-but-empty and set-but-unreadable both refuse to start, naming the
   variable: an intake that silently read nothing would look exactly like a
   research service that wrote nothing. Listing it is the test of "readable",
   because that is the one thing a pass does with it. *)
let directory (raw : string option) : (string option, string) Result.t =
  match raw with
  | None -> Ok None
  | Some dir when String.is_empty (String.strip dir) ->
      Error
        (sprintf
           "ohcamel: %s is set but empty. Unset it for no signal intake, or name a \
            readable directory."
           env_var)
  | Some dir -> (
      let refuse why =
        Error
          (sprintf
             "ohcamel: %s is set to %s, which is not a readable directory (%s). Unset it \
              for no signal intake, or fix the path or its permissions."
             env_var dir why)
      in
      match Core_unix.stat dir with
      | exception Core_unix.Unix_error (e, _, _) -> refuse (Core_unix.Error.message e)
      | { st_kind = S_DIR; _ } -> (
          match Sys_unix.readdir dir with
          | (_ : string array) -> Ok (Some dir)
          | exception Core_unix.Unix_error (e, _, _) -> refuse (Core_unix.Error.message e)
          | exception Sys_error why -> refuse why)
      | _ -> refuse "not a directory")

(* A file as lstat saw it, and as fstat must see it once it is open. *)
type ident = { dev : int; ino : int; size : Int64.t; mtime : float } [@@deriving equal]

let ident_of (st : Core_unix.stats) =
  { dev = st.st_dev; ino = st.st_ino; size = st.st_size; mtime = st.st_mtime }

let not_regular = "not a regular file; a symlink, directory, device or pipe is never read"

let too_large size =
  sprintf "the file is %Ld bytes, more than the %d a signal may be" size max_file_bytes

(* What lstat says of a name, before anything is opened: a regular file of a
   signal's size, or a refusal. *)
type look =
  | Regular of ident
  | Refused of string  (** recorded once in signal_files *)
  | Vanished  (** listed, then removed before it was looked at: nothing to record *)
  | Io_error of string  (** tried again next pass, never recorded *)

let look path : look =
  match Core_unix.lstat path with
  | exception Core_unix.Unix_error (ENOENT, _, _) -> Vanished
  | exception Core_unix.Unix_error (e, _, _) -> Io_error (Core_unix.Error.message e)
  | { st_kind = S_REG; st_size; _ } when Int64.(st_size > of_int max_file_bytes) ->
      Refused (too_large st_size)
  | { st_kind = S_REG; _ } as st -> Regular (ident_of st)
  | _ -> Refused not_regular

type read =
  | Text of string
  | Changed  (** not the file lstat saw, or grown past the bound: read again next pass *)
  | Gone
  | Unreadable of string

(* One file, no further than a signal can be long. The descriptor is opened
   non-blocking, so a pipe put where a file was cannot hold the scheduler in
   open(2) or read(2). open(2) follows a symlink, so fstat must name the very
   file lstat saw -- the same (st_dev, st_ino) -- or nothing is read: a
   symlink swapped in between the two is caught here and never read.
   [between] runs between lstat's answer and the open, and exists for the
   test that does the swap. *)
let read ?(between = fun () -> ()) path (seen : ident) : read =
  between ();
  match Core_unix.openfile path ~mode:[ O_RDONLY; O_NONBLOCK ] with
  | exception Core_unix.Unix_error (ENOENT, _, _) -> Gone
  | exception Core_unix.Unix_error (e, _, _) -> Unreadable (Core_unix.Error.message e)
  | fd -> (
      match
        Exn.protect
          ~finally:(fun () -> Core_unix.close fd)
          ~f:(fun () ->
            match Core_unix.fstat fd with
            | { st_kind = S_REG; st_dev; st_ino; _ }
              when st_dev = seen.dev && st_ino = seen.ino ->
                let buf = Bytes.create (max_file_bytes + 1) in
                let rec fill pos =
                  if pos > max_file_bytes then pos
                  else
                    match Core_unix.read fd ~buf ~pos ~len:(max_file_bytes + 1 - pos) with
                    | 0 -> pos
                    | n -> fill (pos + n)
                in
                let n = fill 0 in
                if n > max_file_bytes then Changed
                else Text (Bytes.To_string.sub buf ~pos:0 ~len:n)
            | _ -> Changed)
      with
      | read -> read
      | exception Core_unix.Unix_error (e, _, _) -> Unreadable (Core_unix.Error.message e)
      )

(* Where a JSON parser stopped, from Yojson's own message -- its "Line L,
   bytes A-B" and nothing after it, because what follows quotes the token it
   choked on, and that token is the file's content. *)
let position (msg : string) =
  match Scanf.sscanf msg "Line %d, bytes %d-%d" (fun l a b -> (l, a, b)) with
  | l, a, b -> sprintf " at line %d, bytes %d-%d" l a b
  | exception _ -> ""

(* The first field, in the schema's order, that is absent or not what the
   contract reads: the field's NAME, which is the contract's, never its
   value, which is the file's. *)
let shape_error (json : Yojson.Safe.t) : string =
  let find fields name = List.Assoc.find fields name ~equal:String.equal in
  let int = function Some (`Int _) -> true | _ -> false in
  let str = function Some (`String _) -> true | _ -> false in
  let number = function `Int _ | `Float _ -> true | _ -> false in
  let opt_number = function None | Some `Null -> true | Some j -> number j in
  let opt_str = function None | Some `Null | Some (`String _) -> true | _ -> false in
  let date = function
    | Some (`String d) -> Option.is_some (Option.try_with (fun () -> Date.of_string d))
    | _ -> false
  in
  let first checks =
    List.find_map checks ~f:(fun (name, ok) -> if ok () then None else Some name)
  in
  let missing =
    match json with
    | `Assoc fields ->
        let field = find fields in
        first
          [
            ("schema_version", fun () -> int (field "schema_version"));
            ("strategy", fun () -> str (field "strategy"));
            ("params_hash", fun () -> str (field "params_hash"));
            ("as_of", fun () -> date (field "as_of"));
            ("computed_at", fun () -> str (field "computed_at"));
            ("data_hash", fun () -> str (field "data_hash"));
            ("sequence", fun () -> int (field "sequence"));
            ( "validation",
              fun () ->
                match field "validation" with
                | Some (`Assoc v) ->
                    let v = find v in
                    str (v "status")
                    && str (v "gates_version")
                    && opt_number (v "dsr")
                    && opt_number (v "psr")
                    && opt_number (v "pbo")
                    && opt_str (v "manifest")
                | _ -> false );
            ( "targets",
              fun () ->
                match field "targets" with
                | Some (`List ts) ->
                    List.for_all ts ~f:(function
                      | `Assoc t -> (
                          str (find t "symbol")
                          &&
                          match find t "weight" with
                          | Some w -> number w
                          | None -> false)
                      | _ -> false)
                | _ -> false );
          ]
    | _ -> Some "the document itself, which is not a JSON object"
  in
  match missing with
  | Some name -> sprintf "not a signal: %s is absent, or not what the contract reads" name
  | None -> "not a signal: the contract could not read it"

(* Any failure to parse is the file's, whatever raised it, and is described
   in the desk's words: not valid UTF-8 (the document is stored as text and
   its strings are served to the page), not JSON with where, or not a signal
   with which field. *)
let parse text : (Contract.t, string) Result.t =
  if not (Stdlib.String.is_valid_utf_8 text) then Error "not valid UTF-8"
  else
    match Yojson.Safe.from_string text with
    | exception Yojson.Json_error msg -> Error ("not JSON" ^ position msg)
    | exception _ -> Error "not JSON"
    | json -> (
        match Contract.parse json with
        | Ok doc -> Ok doc
        | Error _ -> Error (shape_error json)
        | exception _ -> Error (shape_error json))

(* ------------------------------------------------------------------------ *)
(* The clock, and the judgement                                              *)
(* ------------------------------------------------------------------------ *)

(* The three facts, from [dates] alone: one read of sessions, oldest first,
   each date once (it is the table's primary key). None when there is no
   session: there is no empty clock, and no placeholder date. The clock is
   opaque, so the latest date and the count it was built from are kept beside
   it for the checks the intake makes itself. *)
type clock = { clock : Contract.Clock.t; latest : Date.t; bars_after : Date.t -> int }

let clock_of_dates (dates : Date.t list) : clock option =
  match dates with
  | [] -> None
  | earliest_bar :: _ ->
      let sorted = Array.of_list dates in
      let n = Array.length sorted in
      let latest = sorted.(n - 1) in
      let bars_after d =
        match
          Array.binary_search sorted ~compare:Date.compare `First_strictly_greater_than d
        with
        | Some i -> n - i
        | None -> 0
      in
      Some
        {
          clock = Contract.Clock.create ~earliest_bar ~latest_bar:latest ~bars_after;
          latest;
          bars_after;
        }

(* Weekdays d with as_of < d <= latest: bars_after's own interval, counted on
   the calendar instead of the record. diff_weekdays counts [t2, t1); moving
   that to (t2, t1] by adding and subtracting the two ends, rather than by
   Date.add_days, keeps it total for every date a document can parse -- a day
   added to 9999-12-31 raises, and one such file would stop every pass. *)
let weekdays_after ~as_of ~latest =
  Date.diff_weekdays latest as_of
  - Bool.to_int (Date.is_weekday as_of)
  + Bool.to_int (Date.is_weekday latest)

let weekday_bound ~as_of ~latest ~bars ~max_age : (unit, string) Result.t =
  let weekdays = weekdays_after ~as_of ~latest in
  if weekdays - weekday_slack > max_age then
    Error
      (sprintf
         "as_of %s is %d weekdays before the latest session %s, but the sessions record \
          holds only %d after it: the record has a gap, and %d weekdays less a slack of \
          %d is more than max_age %d"
         (Date.to_string as_of) weekdays (Date.to_string latest) bars weekdays
         weekday_slack max_age)
  else Ok ()

module Judgement = struct
  type t = { verdict : Verdict.t; rule : string option; detail : string }
  [@@deriving sexp_of, compare, equal]
end

let rejected rule detail =
  { Judgement.verdict = Verdict.Rejected; rule = Some rule; detail }

let duplicate (targets : Contract.target list) =
  List.find_a_dup targets ~compare:(fun (a : Contract.target) b ->
      Symbol.compare a.symbol b.symbol)

(* One document's judgement, from the contract's verdict: pure, so every
   branch is a test with a hand-built clock. [`Defer why] is R3 -- the caller
   decides between waiting and giving up. [spec] is the document's strategy,
   None when it is not registered (R2 has then failed first). *)
let decide ~(latest : Date.t) ~(bars_after : Date.t -> int)
    ~(spec : Signals_spec.Strategy.t option) (doc : Contract.t)
    (verdict : Contract.verdict) : [ `Defer of string | `Judged of Judgement.t ] =
  let r4_gap () =
    match spec with
    | None -> Ok ()
    | Some s ->
        weekday_bound ~as_of:doc.as_of ~latest ~bars:(bars_after doc.as_of)
          ~max_age:s.Signals_spec.Strategy.max_age
  in
  match verdict with
  | Contract.Rejected (R3, why) -> (
      match spec with
      | None ->
          (* R2 comes first, so this cannot be; failing closed if it ever is. *)
          `Judged (rejected "R2" (sprintf "%S is not registered" doc.strategy))
      | Some s ->
          let ahead = weekdays_after ~as_of:latest ~latest:doc.as_of in
          if ahead - weekday_slack > s.max_age then
            `Judged
              (rejected "R3"
                 (sprintf
                    "%s, %d weekdays past it: %d less a slack of %d is more than max_age \
                     %d, so no wait for its session can end with it fresh"
                    why ahead ahead weekday_slack s.max_age))
          else `Defer why)
  | Rejected (((R1 | R2 | R4) as rule), why) ->
      `Judged (rejected (Contract.Rule.to_string rule) why)
  | Rejected (((R5 | R6 | R7) as rule), why) -> (
      match (r4_gap (), rule) with
      | Error gap, _ -> `Judged (rejected "R4" gap)
      | Ok (), R6 ->
          `Judged
            {
              verdict = Advisory;
              rule = Some "R6";
              detail =
                why
                ^ "; R1-R5 passed and R7 was not reached, so it is shown and never sized";
            }
      | Ok (), _ -> `Judged (rejected (Contract.Rule.to_string rule) why))
  | Accepted doc -> (
      match (r4_gap (), duplicate doc.targets, spec) with
      | Error gap, _, _ -> `Judged (rejected "R4" gap)
      | Ok (), Some t, _ ->
          `Judged
            (rejected "R7"
               (sprintf "%S is named twice in targets" (Symbol.to_string t.symbol)))
      | Ok (), None, None ->
          (* R2 passed, so the strategy is registered; failing closed all the
             same if that ever stops being true. *)
          `Judged (rejected "R2" (sprintf "%S is not registered" doc.strategy))
      | Ok (), None, Some s -> (
          let own = s.Signals_spec.Strategy.symbols in
          match
            List.find doc.targets ~f:(fun t ->
                not (List.mem own (Symbol.to_string t.symbol) ~equal:String.equal))
          with
          | Some t ->
              `Judged
                (rejected "strategy"
                   (sprintf "%S is not one of %s's symbols (%s)"
                      (Symbol.to_string t.symbol) s.name (String.concat ~sep:", " own)))
          | None -> (
              match s.sizing with
              | Live ->
                  `Judged
                    {
                      verdict = Accepted;
                      rule = None;
                      detail =
                        "passed R1-R7 and names only its strategy's own symbols; the \
                         strategy is live";
                    }
              | Advisory ->
                  `Judged
                    {
                      verdict = Advisory;
                      rule = Some "sizing";
                      detail =
                        "passed R1-R7 and names only its strategy's own symbols; the \
                         strategy's sizing is advisory, so it is shown and never sized";
                    })))

(* ------------------------------------------------------------------------ *)
(* The intake                                                                *)
(* ------------------------------------------------------------------------ *)

type t = {
  journal : Journal.t;
  dir : string;
  strategies : Signals_spec.Strategy.t list;
  universe : Contract.universe;
  on_event : string -> unit;
  now : unit -> Time_ns.t;
  (* Files whose document the journal already holds a judgement for, as
     lstat last saw them: while a name's identity is unchanged it is not read
     again. Pruned to the names each pass lists. *)
  judged : ident String.Table.t;
  (* Names recorded in signal_files, so a pass need not ask the journal
     again; only ever added to after the journal holds the name. *)
  refused : String.Hash_set.t;
  (* Files that failed to parse last pass, as they were then: a failure is
     recorded only when the file is still exactly that. *)
  mutable unsettled : ident String.Map.t;
  (* Files the last pass left unjudged and unrecorded: deferred at R3, not
     yet parseable, or waiting for a first session. *)
  mutable deferred : int;
  mutable last_pass : Time_ns.t option;
  (* Lines said once: an IO error, a file changed mid-read, a judged key
     with a different body -- by kind and name, so a stuck file costs one
     line, not one a minute. *)
  said : String.Hash_set.t;
}

let create ~journal ~dir ~strategies ~universe ~on_event ~now =
  {
    journal;
    dir;
    strategies;
    universe;
    on_event;
    now;
    judged = String.Table.create ();
    refused = String.Hash_set.create ();
    unsettled = String.Map.empty;
    deferred = 0;
    last_pass = None;
    said = String.Hash_set.create ();
  }

let deferred t = t.deferred
let last_pass t = t.last_pass

type status = Running of t | Off of string

(* Both halves, or no intake: the directory, and a book that registers
   strategies. The reason is what /api/research says. *)
let setup ~journal ~universe ~(signals : Signals_spec.t option) ~(dir : string option)
    ~on_event ~now : status =
  match (dir, signals) with
  | None, _ -> Off (sprintf "%s is not set, so no signal file is read" env_var)
  | Some _, None ->
      Off
        "the book has no signals block, so no strategy is registered and no file is read"
  | Some dir, Some spec ->
      Running
        (create ~journal ~dir ~strategies:spec.Signals_spec.strategies ~universe ~on_event
           ~now)

let demo_status =
  Off
    "no strategy is registered on the demo: its synthetic book holds neither SPY nor \
     TLT, and adding them would move Figure 1"

let log t fmt = ksprintf (fun s -> t.on_event ("intake    " ^ s)) fmt

let once t ~kind ~name f =
  let key = kind ^ "\000" ^ name in
  if not (Hash_set.mem t.said key) then (
    Hash_set.add t.said key;
    f ())

let record t ~name ~ident ~text (doc : Contract.t) (j : Judgement.t) =
  Journal.record_signal t.journal
    {
      Journal.Signal.strategy = doc.strategy;
      sequence = doc.sequence;
      as_of = doc.as_of;
      received_at = t.now ();
      verdict = j.verdict;
      rule = j.rule;
      detail = j.detail;
      document = text;
    };
  Hashtbl.set t.judged ~key:name ~data:ident;
  log t "%S: %S %d, as_of %s: %s%s -- %S" name doc.strategy doc.sequence
    (Date.to_string doc.as_of) (Verdict.to_string j.verdict)
    (match j.rule with Some r -> " at " ^ r | None -> "")
    j.detail

let refuse t ~name ~why =
  Journal.record_signal_file t.journal ~name ~received_at:(t.now ()) ~error:why;
  Hash_set.add t.refused name;
  log t "%S is not a signal, recorded once and never read again: %s" name why

(* The journal's key, asked before any judgement: true when the document's
   (strategy, sequence) is already judged, and the file is then remembered
   and skipped. A body different from the one judged is said once -- it is
   not a replay of that document but another one claiming its place -- and
   skipped all the same: the first judgement is the judgement. *)
let already_judged t ~name ~ident ~text (doc : Contract.t) =
  match Journal.signal t.journal ~strategy:doc.strategy ~sequence:doc.sequence with
  | None -> false
  | Some recorded ->
      if not (String.equal recorded.Journal.Signal.document text) then
        once t ~kind:"differs" ~name (fun () ->
            log t
              "%S carries %S %d, which is already judged from a different document; it \
               is skipped, and never judged"
              name doc.strategy doc.sequence);
      Hashtbl.set t.judged ~key:name ~data:ident;
      true

type candidate =
  | Refusal of string * string
  | Unparsed of string * ident * string
  | Parsed of string * ident * string * Contract.t

(* One pass: every step the header describes, synchronously. A raise leaves
   this function -- [run] catches it -- and whatever was recorded before it
   stays recorded; the next pass skips it as already judged. *)
let pass t =
  let names =
    Sys_unix.readdir t.dir |> Array.to_list
    |> List.filter ~f:(String.is_suffix ~suffix:".json")
    |> List.sort ~compare:String.compare
  in
  let listed = String.Hash_set.of_list names in
  Hashtbl.filter_keys_inplace t.judged ~f:(Hash_set.mem listed);
  let clock = clock_of_dates (Journal.session_dates t.journal) in
  let reads = ref 0 in
  let held = ref 0 in
  let candidates =
    List.filter_map names ~f:(fun name ->
        if Hash_set.mem t.refused name then None
        else if Journal.signal_file_recorded t.journal ~name then (
          Hash_set.add t.refused name;
          None)
        else
          let path = Filename.concat t.dir name in
          let io_error why =
            once t ~kind:"io" ~name (fun () ->
                log t "%S could not be read, and is tried again each minute: %s" name why);
            None
          in
          match look path with
          | Vanished -> None
          | Io_error why -> io_error why
          | Refused why -> Some (Refusal (name, why))
          | Regular ident ->
              if Option.equal equal_ident (Hashtbl.find t.judged name) (Some ident) then
                None
              else if !reads >= max_reads_per_pass then (
                incr held;
                None)
              else (
                incr reads;
                match read path ident with
                | Gone -> None
                | Unreadable why ->
                    (* A file that cannot be opened is not a read the cap should
                       spend: a hundred of them would hold every new file back. *)
                    decr reads;
                    io_error why
                | Changed ->
                    once t ~kind:"changed" ~name (fun () ->
                        log t "%S changed while it was read; it is read again next pass"
                          name);
                    None
                | Text text -> (
                    Hash_set.remove t.said ("io\000" ^ name);
                    Hash_set.remove t.said ("changed\000" ^ name);
                    match parse text with
                    | Error why -> Some (Unparsed (name, ident, why))
                    | Ok doc ->
                        if already_judged t ~name ~ident ~text doc then None
                        else Some (Parsed (name, ident, text, doc)))))
  in
  if !held > 0 then
    log t "%d files wait for the next pass: a pass reads at most %d" !held
      max_reads_per_pass;
  let unsettled = ref String.Map.empty in
  let deferred = ref 0 in
  let settle name ident =
    unsettled := Map.set !unsettled ~key:name ~data:ident;
    incr deferred
  in
  let unchanged name ident =
    Option.equal equal_ident (Map.find t.unsettled name) (Some ident)
  in
  (match clock with
  | None ->
      (* No session yet: nothing is recorded, a malformed file included, and
         every file waits for the first close the desk records. A file that
         will not parse is remembered as it is, so the first pass with a
         clock can record it if it has not changed. *)
      List.iter candidates ~f:(function
        | Unparsed (name, ident, _) -> settle name ident
        | Refusal _ | Parsed _ -> incr deferred)
  | Some { clock; latest; bars_after } ->
      let parsed =
        List.filter_map candidates ~f:(function
          | Refusal (name, why) ->
              refuse t ~name ~why;
              None
          | Unparsed (name, ident, why) ->
              if unchanged name ident then refuse t ~name ~why else settle name ident;
              None
          | Parsed (name, ident, text, doc) -> Some (name, ident, text, doc))
        |> List.sort ~compare:(fun (n1, _, _, (d1 : Contract.t)) (n2, _, _, d2) ->
            [%compare: string * int * string]
              (d1.strategy, d1.sequence, n1)
              (d2.strategy, d2.sequence, n2))
      in
      let registered = List.map t.strategies ~f:(fun s -> s.Signals_spec.Strategy.name) in
      List.iter parsed ~f:(fun (name, ident, text, (doc : Contract.t)) ->
          (* One document that raises is logged and left unjudged, and the
             pass goes on: it must not hold back every document after it. *)
          try
            (* Asked again here, not only in [candidates]: two files in one pass
             can carry the same (strategy, sequence), and the first judged is
             the one judgement. *)
            if not (already_judged t ~name ~ident ~text doc) then
              let spec =
                List.find t.strategies ~f:(fun s ->
                    String.equal s.Signals_spec.Strategy.name doc.strategy)
              in
              let baseline =
                Journal.highest_sequence t.journal ~strategy:doc.strategy
                  ~verdicts:[ Accepted; Advisory ]
              in
              let registry =
                {
                  Contract.strategies = registered;
                  last_sequence =
                    Option.value_map baseline ~default:[] ~f:(fun n ->
                        [ (doc.strategy, n) ]);
                }
              in
              (* An unregistered strategy fails R2 before max_age is read. *)
              let max_age = Option.value_map spec ~default:0 ~f:(fun s -> s.max_age) in
              let verdict =
                Contract.validate ~clock ~registry ~universe:t.universe ~max_age doc
              in
              match decide ~latest ~bars_after ~spec doc verdict with
              | `Judged j -> record t ~name ~ident ~text doc j
              | `Defer why ->
                  (* The first sighting is the journal's, by the document's key:
                   a restart or a rename finds it where it was left. *)
                  let since =
                    match
                      Journal.deferral_since t.journal ~strategy:doc.strategy
                        ~sequence:doc.sequence
                    with
                    | Some since -> since
                    | None ->
                        Journal.record_deferral t.journal ~strategy:doc.strategy
                          ~sequence:doc.sequence ~first_latest:latest;
                        latest
                  in
                  let passed = bars_after since in
                  if passed >= max_age then
                    record t ~name ~ident ~text doc
                      (rejected "R3"
                         (sprintf
                            "%s; first deferred when the latest session was %s, and %d \
                             sessions have been recorded since with none on or after its \
                             as_of (max_age %d)"
                            why (Date.to_string since) passed max_age))
                  else incr deferred
          with exn ->
            incr deferred;
            once t ~kind:"raised" ~name (fun () ->
                log t "%S could not be judged, and is left unjudged: %s" name
                  (Exn.to_string exn))));
  t.unsettled <- !unsettled;
  t.deferred <- !deferred;
  t.last_pass <- Some (t.now ())

(* Once a minute, from now: the first pass at once, so a restart judges what
   arrived while it was down without waiting a minute. [pass] is synchronous,
   so a plain try/with sees every raise it can make. *)
let run ?(time_source = Time_source.wall_clock ()) t : unit Deferred.t =
  let rec loop () =
    (try pass t
     with exn ->
       log t "a pass raised, and the next one runs in a minute: %s" (Exn.to_string exn));
    let%bind () = Time_source.after time_source every in
    loop ()
  in
  loop ()

(* ------------------------------------------------------------------------ *)
(* /api/research                                                             *)
(* ------------------------------------------------------------------------ *)

let jnum x = if Float.is_finite x then `Float x else `Null
let jopt f = function None -> `Null | Some x -> f x

(* The weights and the validation block, from the recorded document parsed
   again -- never the document's own JSON passed through, so what reaches the
   page is exactly these fields, each a string or a finite number. *)
let judgement_json (s : Journal.Signal.t) : Yojson.Safe.t =
  let doc = match parse s.document with Ok d -> Some d | Error _ -> None in
  `Assoc
    [
      ("verdict", `String (Verdict.to_string s.verdict));
      ("rule", jopt (fun r -> `String r) s.rule);
      ("detail", `String s.detail);
      ("sequence", `Int s.sequence);
      ("as_of", `String (Date.to_string s.as_of));
      ("received_at", `String (Desk_time.rfc3339 s.received_at));
      ( "targets",
        jopt
          (fun (d : Contract.t) ->
            `List
              (List.map d.targets ~f:(fun (t : Contract.target) ->
                   `Assoc
                     [
                       ("symbol", `String (Symbol.to_string t.symbol));
                       ("weight", jnum t.weight);
                     ])))
          doc );
      ( "validation",
        jopt
          (fun (d : Contract.t) ->
            let v = d.validation in
            `Assoc
              [
                ("status", `String v.status);
                ("gates_version", `String v.gates_version);
                ("dsr", jopt jnum v.dsr);
                ("psr", jopt jnum v.psr);
                ("pbo", jopt jnum v.pbo);
                ("manifest", jopt (fun m -> `String m) v.manifest);
              ])
          doc );
    ]

(* Bounded by the book: one indexed seek per registered strategy, and the
   deferred count from the last pass, in memory. *)
let research_json ~journal ~(strategies : Signals_spec.Strategy.t list) (status : status)
    : Yojson.Safe.t =
  `Assoc
    [
      ("intake", `String (match status with Running _ -> "running" | Off _ -> "off"));
      ("reason", match status with Running _ -> `Null | Off why -> `String why);
      ("deferred", `Int (match status with Running t -> t.deferred | Off _ -> 0));
      ( "last_pass",
        match status with
        | Running t -> jopt (fun at -> `String (Desk_time.rfc3339 at)) t.last_pass
        | Off _ -> `Null );
      ( "strategies",
        `List
          (List.map strategies ~f:(fun (s : Signals_spec.Strategy.t) ->
               `Assoc
                 [
                   ("name", `String s.name);
                   ("symbols", `List (List.map s.symbols ~f:(fun x -> `String x)));
                   ("max_age", `Int s.max_age);
                   ("sizing", `String (Signals_spec.sizing_to_string s.sizing));
                   ("capital_fraction", jnum s.capital_fraction);
                   ( "latest",
                     jopt judgement_json (Journal.latest_signal journal ~strategy:s.name)
                   );
                 ])) );
      ("r8", `String r8_sentence);
    ]
