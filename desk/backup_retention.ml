(* Which backups to keep, decided from their names alone. See the .mli for why
   this module reads no directory and asks no clock.

   Ruling 11's rule, in three lines: the 14 newest dailies, the 8 most recent
   Sundays among the dailies older than those, and the 5 newest pre-deploy
   copies. Everything else goes. The Sunday tier is what makes a two-month-old
   restore possible without keeping sixty files: 14 dailies reach back a
   fortnight, and the Sundays reach back another eight weeks at one file a
   week.

   And a fourth line the rule needs because of how a copy is written: a name
   in the .tmp family is the leftover of a run that died, never a backup, and
   always deleted. *)

open Core

let daily_kept = 14
let sundays_kept = 8
let pre_deploy_kept = 5
let daily_prefix = "desk-"
let daily_suffix = ".db"
let pre_deploy_prefix = "pre-deploy-"

(* What [Journal.backup] has beside a copy while the copy is being written:
   the copy itself under [.tmp], and the -wal, -shm and -journal SQLite makes
   for the destination handle while the copy leaves WAL mode. A run killed
   between opening those and the rename -- SIGKILL, an OOM kill, a host
   reboot -- leaves them under THAT day's name, and the next run's own tidying
   knows only its own name, so the leftovers of every other day are this
   module's to delete. *)
let leftover_suffixes = [ ".tmp"; ".tmp-wal"; ".tmp-shm"; ".tmp-journal" ]

type kind = Daily of Date.t | Pre_deploy | Leftover | Unrecognised

(* A leftover is judged FIRST, and only when what is under the suffix is a name
   this rule recognises. First, because judged as a pre-deploy copy,
   "pre-deploy-2026-09-08.db.tmp" sorts newest by name and takes a place from
   a real rollback copy. Only over a recognised name, because the promise that
   nothing unparseable is deleted must survive this: "notes.tmp" is somebody's
   file and is kept.

   A daily is [desk-YYYY-MM-DD.db] and nothing looser. The length is checked
   as well as the parse because [Date.of_string] accepts the compact
   "20260901", which is not a name anything here writes, and treating it as a
   daily would have it compete for one of the 14 places with a file whose
   date nobody chose. A name beginning [pre-deploy-] is a pre-deploy copy
   whatever follows, because the deploy names those and the rule for them is
   a glob (ruling 11).

   A bare "-wal", "-shm" or "-journal" beside a FINISHED name is not a
   leftover: nothing here writes one, a hot -journal is how SQLite rolls a
   partial write back, and deleting one from under a database is how a
   database is corrupted by hand. Those stay unrecognised, and kept. *)
let rec classify (name : string) : kind =
  match
    List.find_map leftover_suffixes ~f:(fun suffix -> String.chop_suffix name ~suffix)
  with
  | Some stem -> (
      match classify stem with
      | Daily _ | Pre_deploy -> Leftover
      | Leftover | Unrecognised -> Unrecognised)
  | None -> (
      if String.is_prefix name ~prefix:pre_deploy_prefix then Pre_deploy
      else
        match String.chop_prefix name ~prefix:daily_prefix with
        | None -> Unrecognised
        | Some rest -> (
            match String.chop_suffix rest ~suffix:daily_suffix with
            | Some stamp when String.length stamp = 10 -> (
                match Option.try_with (fun () -> Date.of_string stamp) with
                | Some d -> Daily d
                | None -> Unrecognised)
            | _ -> Unrecognised))

let keep ~(now : Date.t) (names : string list) : string list * string list =
  let dailies, pre_deploys, leftovers, unrecognised =
    List.fold names ~init:([], [], [], []) ~f:(fun (dailies, pre, gone, odd) name ->
        match classify name with
        | Daily d -> ((name, d) :: dailies, pre, gone, odd)
        | Pre_deploy -> (dailies, name :: pre, gone, odd)
        | Leftover -> (dailies, pre, name :: gone, odd)
        | Unrecognised -> (dailies, pre, gone, name :: odd))
  in
  (* A daily dated after [now] is kept and takes none of the 14 places: a
     clock that ran backwards, or a copy carried in by hand, must not push
     fourteen real dailies out of the window. *)
  let ahead, dated = List.partition_tf dailies ~f:(fun (_, d) -> Date.( > ) d now) in
  let newest_first = List.sort dated ~compare:(fun (_, a) (_, b) -> Date.compare b a) in
  let newest, older = List.split_n newest_first daily_kept in
  let sundays =
    List.filter older ~f:(fun (_, d) ->
        Day_of_week.equal (Date.day_of_week d) Day_of_week.Sun)
    |> fun l -> List.take l sundays_kept
  in
  (* Newest by name, which is newest by date for the date-stamped names the
     deploy writes, and the only order available to a module that never sees a
     file's mtime. *)
  let pre_newest_first = List.sort pre_deploys ~compare:(fun a b -> String.compare b a) in
  let pre_kept, pre_deleted = List.split_n pre_newest_first pre_deploy_kept in
  let kept = List.map (ahead @ newest @ sundays) ~f:fst @ pre_kept @ unrecognised in
  let keeping = Set.of_list (module String) kept in
  let deleted =
    List.filter (List.map older ~f:fst) ~f:(fun n -> not (Set.mem keeping n))
    @ pre_deleted @ leftovers
  in
  (List.sort kept ~compare:String.compare, List.sort deleted ~compare:String.compare)
