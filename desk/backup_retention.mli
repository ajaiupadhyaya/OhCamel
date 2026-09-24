(* Which backups to keep, decided from their names alone.

   Pure on purpose. The retention rule is the part of the backup job that can
   be wrong in a way nobody notices until the day a restore is needed, and the
   case that matters -- a directory with two months of dailies in it -- is
   impossible to set up honestly against a real filesystem and a real clock.
   So this module reads no directory and asks no clock: it takes the names and
   the date to reckon from, and answers which names to keep and which to
   delete. The caller does the unlinking. *)

open Core

val daily_kept : int
(** 14: the newest dailies kept whatever day they fall on (ruling 11). *)

val sundays_kept : int
(** 8: among the dailies older than those 14, the newest that fall on a Sunday. *)

val pre_deploy_kept : int
(** 5: the newest [pre-deploy-*] copies. *)

val keep : now:Date.t -> string list -> string list * string list
(** [keep ~now names] splits [names] -- the basenames in one backup directory -- into the
    ones to keep and the ones to delete, both sorted by name.

    A daily is [desk-YYYY-MM-DD.db]; a pre-deploy copy is any name beginning
    [pre-deploy-]. Kept: the newest [daily_kept] dailies dated at or before [now], then
    the newest [sundays_kept] Sundays among the dailies older than those, then the newest
    [pre_deploy_kept] pre-deploy copies by name -- which is newest-first for the
    date-stamped names the deploy writes, and the only order a module with no filesystem
    can have.

    Two names are never deleted. A name this rule does not recognise is kept, because a
    pruner that deletes what it cannot parse is a pruner that deletes the wrong thing
    once. A daily dated after [now] is kept and does not consume one of the [daily_kept]
    places, so a clock that ran backwards, or a copy carried in by hand from another host,
    cannot push 14 real dailies out of the window. *)
