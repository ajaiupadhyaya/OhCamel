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

(* The names in the backup directory, for the pruning. A directory that cannot
   be read is a failure and not an empty directory, because an empty directory
   is a list with nothing to delete and would exit zero. *)
let names_in dir =
  match Or_error.try_with (fun () -> Array.to_list (Sys_unix.readdir dir)) with
  | Ok names -> Ok names
  | Error e -> Error (sprintf "ohcamel: cannot list %s: %s" dir (Error.to_string_hum e))

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
    let dst = Filename.concat dir basename in
    match Journal.backup ~src ~dst with
    | Error e -> fail all e
    | Ok () -> (
        say (sprintf "ohcamel: wrote %s" dst);
        (* THE COPY IS CHECKED BEFORE ANYTHING IS PRUNED, and a copy that fails
           the check does not stay: see the .mli. *)
        let verdict =
          match Journal.verify dst with
          | Error e -> Error e
          | Ok report ->
              List.iter (Journal.Report.lines report) ~f:say;
              if Journal.Report.clean report then Ok () else Error (not_restorable dst)
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
                        (sprintf "ohcamel: %d kept, %d deleted in %s" (List.length kept)
                           (List.length deleted) dir);
                      finish all
                  | n :: rest -> (
                      let path = Filename.concat dir n in
                      match Or_error.try_with (fun () -> Core_unix.unlink path) with
                      | Ok () ->
                          say (sprintf "ohcamel: deleted %s" path);
                          unlink rest
                      | Error e ->
                          fail all
                            (sprintf "ohcamel: cannot delete %s: %s" path
                               (Error.to_string_hum e)))
                in
                unlink deleted))
