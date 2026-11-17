(* The journal's two commands as functions. See the .mli for why they are
   not in bin/main.ml. *)

open Core

module Outcome = struct
  type t = { status : int; out : string list; err : string option }
  [@@deriving sexp_of, compare, equal]
end

(* The lines a command prints, gathered in order; [finish] and [fail] turn
   them into the outcome. No [exit] anywhere in this module: the status is
   returned, and bin/ is the one place that exits. *)
let lines () =
  let acc = ref [] in
  let say line = acc := line :: !acc in
  let all () = List.rev !acc in
  (say, all)

let finish all = { Outcome.status = 0; out = all (); err = None }
let fail all err = { Outcome.status = 1; out = all (); err = Some err }

let not_restorable path =
  sprintf "ohcamel: %s is not a journal this build would restore from" path

let verify path =
  let say, all = lines () in
  match Journal.verify path with
  | Error e -> fail all e
  | Ok report ->
      List.iter (Journal.Report.lines report) ~f:say;
      if Journal.Report.clean report then finish all else fail all (not_restorable path)

(* The names in the backup directory: read once before the copy is written, to
   judge its name, and again after, for the pruning. A directory that cannot
   be read is a failure and not an empty directory, because an empty directory
   is a list with nothing to delete and would exit zero. *)
let names_in dir =
  match Or_error.try_with (fun () -> Array.to_list (Sys_unix.readdir dir)) with
  | Ok names -> Ok names
  | Error e -> Error (sprintf "ohcamel: cannot list %s: %s" dir (Error.to_string_hum e))

(* Would [basename], once written into a directory holding [names], be one of
   the names the prune that follows deletes? Judged by the rule the prune
   uses, on the listing plus the name itself -- once, because a re-run on the
   same day finds today's name already there, and a duplicate would take two
   of the 14 places. The default name is today's daily, the newest at or
   before [now], and is never deleted. What this catches is a --name in the
   .tmp family, which is a leftover wherever it lands, and a daily's or
   pre-deploy's name that DIR's newer copies already rank out of its places:
   each of those the command used to write, verify, delete in its own prune,
   and report as "1 kept, 1 deleted", status 0, with no backup left. *)
let would_be_pruned ~now ~names basename =
  let names =
    if List.mem names basename ~equal:String.equal then names else basename :: names
  in
  let _kept, deleted = Backup_retention.keep ~now names in
  List.mem deleted basename ~equal:String.equal

(* A copy that failed its check is removed, and the sentence says so, or says
   that it could not be and must not be restored from. *)
let discard dst =
  match Or_error.try_with (fun () -> Core_unix.unlink dst) with
  | Ok () -> sprintf "%s was deleted, and nothing was pruned" dst
  | Error e ->
      sprintf
        "%s could not be deleted (%s) and must not be restored from; nothing was pruned"
        dst (Error.to_string_hum e)

let backup ~(now : Date.t) ~src ~dir ~name =
  let say, all = lines () in
  let basename =
    match name with Some n -> n | None -> sprintf "desk-%s.db" (Date.to_string now)
  in
  (* A NAME with a directory in it would write outside DIR and then be pruned
     by a rule that never saw it. *)
  if String.is_empty basename || String.exists basename ~f:(Char.equal '/') then
    fail all
      (sprintf "ohcamel: --name %S must be a file name, with no directory in it" basename)
  else
    match names_in dir with
    | Error e -> fail all e
    | Ok names when would_be_pruned ~now ~names basename ->
        (* A NAME the prune would delete is refused before the copy is
           written: written, it was verified, deleted and reported a success. *)
        fail all
          (sprintf
             "ohcamel: --name %S is a name this run's own prune of %s would delete, so \
              nothing was written; choose a name retention keeps"
             basename dir)
    | Ok _ -> (
        let dst = Filename.concat dir basename in
        (* What the source itself lacks, asked before the copy: an older journal
           is additive, and its copy is judged against it (see Journal.verify). *)
        match Journal.tables_absent_from src with
        | Error e -> fail all e
        | Ok absent_in_source -> (
            match Journal.backup ~src ~dst with
            | Error e -> fail all e
            | Ok () -> (
                say (sprintf "ohcamel: wrote %s" dst);
                (* THE COPY IS CHECKED BEFORE ANYTHING IS PRUNED, and a copy that
               fails the check does not stay: see the .mli. *)
                let verdict =
                  match Journal.verify ~absent_in_source dst with
                  | Error e -> Error e
                  | Ok report ->
                      List.iter (Journal.Report.lines report) ~f:say;
                      if Journal.Report.clean report then Ok ()
                      else Error (not_restorable dst)
                in
                match verdict with
                | Error e -> fail all (sprintf "%s; %s" e (discard dst))
                | Ok () -> (
                    match names_in dir with
                    | Error e -> fail all e
                    | Ok names ->
                        let kept, deleted = Backup_retention.keep ~now names in
                        let rec unlink = function
                          | [] ->
                              say
                                (sprintf "ohcamel: %d kept, %d deleted in %s"
                                   (List.length kept) (List.length deleted) dir);
                              finish all
                          | n :: rest -> (
                              let path = Filename.concat dir n in
                              match
                                Or_error.try_with (fun () -> Core_unix.unlink path)
                              with
                              | Ok () ->
                                  say (sprintf "ohcamel: deleted %s" path);
                                  unlink rest
                              | Error e ->
                                  fail all
                                    (sprintf "ohcamel: cannot delete %s: %s" path
                                       (Error.to_string_hum e)))
                        in
                        unlink deleted))))

(* [--dividends FILE]: SYMBOL,EX_DATE,PER_SHARE a line, blank lines and lines from
   # ignored. A line it cannot read refuses the file and names the line: a diff
   built on a dividend half-read is a number about nothing. *)
let parse_dividends text : (Record.dividend list, string) Result.t =
  String.split_lines text
  |> List.filter_mapi ~f:(fun i line ->
      let line = String.strip line in
      if String.is_empty line || String.is_prefix line ~prefix:"#" then None
      else
        Some
          (match List.map (String.split line ~on:',') ~f:String.strip with
          | [ symbol; ex_date; per_share ] -> (
              match
                ( Option.try_with (fun () -> Date.of_string ex_date),
                  Option.bind (Float.of_string_opt per_share) ~f:(fun x ->
                      Option.some_if (Float.is_finite x) x) )
              with
              | Some ex_date, Some per_share when not (String.is_empty symbol) ->
                  Ok
                    {
                      Record.ex_date;
                      symbol = Ohcamel.Types.Symbol.of_string symbol;
                      per_share;
                    }
              | _ -> Error (sprintf "line %d: %S" (i + 1) line))
          | _ ->
              Error (sprintf "line %d: %S is not SYMBOL,EX_DATE,PER_SHARE" (i + 1) line)))
  |> Result.all

(* The strategies the journal has orders for, by their source's slug
   (signal:<slug>:<sequence>), in order. *)
let strategies_of journal =
  Journal.fills_by_source journal
  |> List.filter_map ~f:(fun (source, _, _) ->
      match String.split source ~on:':' with
      | "signal" :: rest when List.length rest >= 2 ->
          Some (String.concat ~sep:":" (List.drop_last_exn rest))
      | _ -> None)
  |> List.dedup_and_sort ~compare:String.compare

(* Task 1.8's before and after, per strategy, for the controller to run on a COPY
   of the live journal. Read-only by construction ([Journal.open_read_only]: no
   set_up, no pragma, every write raises), and a journal older than Task 1.8's
   tables reads as having no activities and no dividends rather than failing. The
   marks are the journal's under both rules, so a session's P&L can change here
   only by a dividend or a fee: a re-mark from the bar happens at the close, on the
   live journal, and is not something a read-only diff can make. *)
let record_diff ~journal:path ~since ~dividends ~strategies:specs ~zone =
  let say, all = lines () in
  let money = sprintf "%.2f" in
  let read_dividends () =
    match dividends with
    | None -> Ok None
    | Some file -> (
        match Or_error.try_with (fun () -> In_channel.read_all file) with
        | Error e ->
            Error (sprintf "ohcamel: cannot read %s: %s" file (Error.to_string_hum e))
        | Ok text ->
            Result.map_error (parse_dividends text) ~f:(fun why ->
                sprintf "ohcamel: %s: %s" file why)
            |> Result.map ~f:Option.some)
  in
  match (read_dividends (), Journal.tables_absent_from path) with
  | Error e, _ | _, Error e -> fail all e
  | Ok given, Ok absent -> (
      match Journal.open_read_only path with
      | Error e -> fail all e
      | Ok journal ->
          Exn.protect
            ~finally:(fun () -> Journal.close journal)
            ~f:(fun () ->
              match
                Or_error.try_with (fun () ->
                    let has table = not (List.mem absent table ~equal:String.equal) in
                    let activities =
                      if has "activities" then Journal.activities journal else []
                    in
                    let dividends, from =
                      match given with
                      | Some ds -> (ds, Option.value_exn dividends)
                      | None when has "dividends" ->
                          ( Stop.dividends_of
                              ~cash_dividends:(Journal.cash_dividends journal)
                              ~activities,
                            "the journal's dividends table" )
                      | None -> ([], "none: the journal has no dividends table")
                    in
                    say
                      (sprintf
                         "record-diff: %s, read-only; sessions from %s; dividends from %s"
                         path
                         (Option.value_map since ~default:"each strategy's first fill"
                            ~f:Date.to_string)
                         from);
                    say
                      "Before = the record as computed before Task 1.8 (no dividend, no \
                       fee, cost not measured); after = with dividends on their \
                       ex-dates, fees, and cost against the arrival mid. Both read the \
                       journal's marks.";
                    (let _, unassigned, count = Stop.fees_by_order activities in
                     say
                       (sprintf
                          "account fees naming no order: %d totalling %s (the account's, \
                           in no strategy's record)"
                          count (money unassigned)));
                    (match specs with
                    | None ->
                        say
                          "No --book: the stop lines are not known, so no drawdown is \
                           judged against one."
                    | Some _ -> ());
                    List.iter (strategies_of journal) ~f:(fun strategy ->
                        let before =
                          Stop.sessions_under ~rule:`Before_1_8 ~journal ~zone ~strategy
                            ~since ()
                        in
                        let after =
                          Stop.sessions_under ~rule:`Current ~dividends ~activities
                            ~journal ~zone ~strategy ~since ()
                        in
                        let total ss =
                          List.sum
                            (module Float)
                            ss
                            ~f:(fun (x : Record.session) -> x.pnl)
                        in
                        let summary = Record.summarise after in
                        let sum f = List.sum (module Float) after ~f in
                        say
                          (sprintf
                             "%s  sessions %d  P&L %s -> %s  dividends %s  fees %s  cost \
                              %s  cost_unknown %d  cost_bps %s"
                             strategy (List.length after)
                             (money (total before))
                             (money (total after))
                             (money (sum (fun x -> x.dividends)))
                             (money (sum (fun x -> x.fees)))
                             (money (sum (fun x -> x.cost)))
                             (List.sum (module Int) after ~f:(fun x -> x.cost_unknown))
                             (Option.value_map summary.cost_bps ~default:"none" ~f:money));
                        let earlier =
                          Date.Map.of_alist_reduce
                            ~f:(fun a _ -> a)
                            (List.map before ~f:(fun (x : Record.session) ->
                                 (x.date, x.pnl)))
                        in
                        List.iter after ~f:(fun (x : Record.session) ->
                            let was =
                              Option.value (Map.find earlier x.date) ~default:0.0
                            in
                            if Float.( > ) (Float.abs (x.pnl -. was)) 1e-9 then
                              say
                                (sprintf "  %s  %s -> %s  %s" (Date.to_string x.date)
                                   (money was) (money x.pnl)
                                   (String.concat ~sep:", "
                                      (List.filter_opt
                                         [
                                           Option.some_if
                                             (Float.( <> ) x.dividends 0.0)
                                             "dividend";
                                           Option.some_if (Float.( <> ) x.fees 0.0) "fee";
                                         ])));
                            (* The stop rules' reading: from the strategy's reinstatement
                           when the book names one, as Stop.evaluate reads it. *)
                            let spec =
                              Option.bind specs ~f:(fun ss ->
                                  List.find ss
                                    ~f:(fun
                                        (x : Ohcamel.Config.Book.Signals_spec.Strategy.t)
                                      -> String.equal x.name strategy))
                            in
                            let reinstated =
                              Option.bind spec
                                ~f:(fun
                                    (x : Ohcamel.Config.Book.Signals_spec.Strategy.t) ->
                                  x.reinstated)
                            in
                            let read rule =
                              let ss =
                                Stop.sessions_under ~rule ~dividends ~activities ~journal
                                  ~zone ~strategy ~since:reinstated ()
                              in
                              (Record.summarise ss, List.length ss)
                            in
                            let (b, bn), (a, an) = (read `Before_1_8, read `Current) in
                            let pct x = sprintf "%.2f%%" (100.0 *. x) in
                            let stopped =
                              match Journal.strategy_state journal ~strategy with
                              | Some (`Stopped, rule, at, _) ->
                                  sprintf "  (already stopped%s at %s; it stays stopped)"
                                    (Option.value_map rule ~default:"" ~f:(fun r ->
                                         " by " ^ r))
                                    at
                              | Some (`Active, _, _, _) | None -> ""
                            in
                            match
                              Option.bind spec
                                ~f:(fun
                                    (x : Ohcamel.Config.Book.Signals_spec.Strategy.t) ->
                                  x.stop)
                            with
                            | None ->
                                say
                                  (sprintf "%s  drawdown %s -> %s (%s)  sessions %d%s"
                                     strategy (pct b.max_drawdown) (pct a.max_drawdown)
                                     (if Option.is_none spec then "no stop line known"
                                      else "no stop line in the book")
                                     an stopped)
                            | Some line ->
                                let fires (r : Record.summary) n =
                                  match Stop.check ~spec:line ~record:r ~sessions:n with
                                  | `Stop _ -> true
                                  | `Continue -> false
                                in
                                let verdict =
                                  match (fires b bn, fires a an) with
                                  | false, true -> "  WOULD STOP AFTER, NOT BEFORE"
                                  | true, false -> "  STOPS BEFORE, NOT AFTER"
                                  | true, true -> "  stops before and after"
                                  | false, false -> ""
                                in
                                say
                                  (sprintf
                                     "%s  drawdown %s -> %s (line %s)  sessions %d of \
                                      %d%s%s"
                                     strategy (pct b.max_drawdown) (pct a.max_drawdown)
                                     (pct
                                        line
                                          .Ohcamel.Config.Book.Signals_spec.Stop.drawdown)
                                     an
                                     line.Ohcamel.Config.Book.Signals_spec.Stop.sessions
                                     verdict stopped))))
              with
              | Ok () -> finish all
              | Error e ->
                  fail all
                    (sprintf "ohcamel: record-diff on %s failed: %s" path
                       (Error.to_string_hum e))))
