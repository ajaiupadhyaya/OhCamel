(* The journal's two commands, `ohcamel journal-backup` and `ohcamel
   journal-verify`, as functions rather than as code in bin/main.ml.

   Both are plain programs: no scheduler, no book, no feeds, no credentials.
   `journal-backup` runs in a one-shot container off a host systemd timer at
   06:30 UTC (ruling 11), so everything it can be wrong about must be an exit
   status, and every exit status but zero must mean "do not trust today's
   copy". Three of the things it can be wrong about are an ORDER and two
   REFUSALS -- the copy is verified before anything is pruned, a name with a
   directory in it is refused, a dirty report exits non-zero -- and each was
   one deleted line from silently wrong while the bodies lived in bin/, where
   test/ cannot drive them. Here they return the exit status and the lines
   they would print, and bin/main.ml prints and exits. *)

open Core

module Outcome : sig
  type t = {
    status : int;  (** The exit status: 0, or 1 for any failure. *)
    out : string list;  (** What goes to stdout, one line each, in order. *)
    err : string option;
        (** The one line a failure writes to stderr, naming what went wrong. None on
            success. *)
  }
  [@@deriving sexp_of, compare, equal]
end

val verify : string -> Outcome.t
(** [ohcamel journal-verify FILE]: [Journal.verify]'s report on stdout, one line each,
    problems last, and status 1 on any problem or on a file that could not be read at all.
*)

val backup : now:Date.t -> src:string -> dir:string -> name:string option -> Outcome.t
(** [ohcamel journal-backup SRC DIR [--name NAME]], reckoned from [now] (the timer's
    today, UTC): copies the journal at [src] to [DIR/NAME] -- [desk-YYYY-MM-DD.db] when no
    name is given -- verifies the copy, and only then prunes [DIR] by
    [Backup_retention.keep ~now].

    Refused before anything is written, with status 1: a NAME that is empty or has a
    directory in it, because a copy written outside DIR would be pruned by a rule that
    never saw it; a NAME that this run's own prune would delete -- one in the .tmp family,
    which retention reads as a leftover wherever it lands, or a daily's or pre-deploy's
    name that the copies already in DIR rank out of its places -- because the command
    otherwise wrote the copy, verified it, deleted it and exited 0 with no backup; and a
    destination that is the source's own file ([Journal.backup]'s refusal).

    A copy that fails verification -- unreadable, or a report with a problem -- is DELETED
    and nothing is pruned, status 1: left in place under a daily's name it would take one
    of the 14 places from a good daily on every prune after, and a pruner that ran before
    the check could delete the fourteenth-oldest good daily to make room for a broken new
    one. The report is printed either way, so the operator reads the counts beside the
    trouble.

    A delete the prune cannot make -- a directory under a daily's name, a file this
    process may not remove -- stops the prune at that file, status 1, with the path on
    stderr: what was deleted before it is named on stdout, nothing after it is touched,
    and the copy, verified before the prune began, stays. *)
