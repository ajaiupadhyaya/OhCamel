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
