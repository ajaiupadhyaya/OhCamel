(* Which backups to keep, decided from their names alone. See the .mli for why
   this module reads no directory and asks no clock.

   Ruling 11's rule, in three lines: the 14 newest dailies, the 8 most recent
   Sundays among the dailies older than those, and the 5 newest pre-deploy
   copies. Everything else goes. The Sunday tier is what makes a two-month-old
   restore possible without keeping sixty files: 14 dailies reach back a
   fortnight, and the Sundays reach back another eight weeks at one file a
   week. *)

open Core

let daily_kept = 14
let sundays_kept = 8
let pre_deploy_kept = 5
let daily_prefix = "desk-"
let daily_suffix = ".db"
let pre_deploy_prefix = "pre-deploy-"

type kind = Daily of Date.t | Pre_deploy | Unrecognised

(* [desk-YYYY-MM-DD.db] and nothing looser. The length is checked as well as
   the parse because [Date.of_string] accepts "2026-9-1", which is not a name
   anything here writes, and treating it as a daily would have it compete for
   one of the 14 places with a file whose date nobody chose. A name beginning
   [pre-deploy-] is a pre-deploy copy whatever follows, because the deploy
   names those and the rule for them is a glob (ruling 11). *)
let classify (name : string) : kind =
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
        | _ -> Unrecognised)

let keep ~(now : Date.t) (names : string list) : string list * string list =
  let dailies, pre_deploys, unrecognised =
    List.fold names ~init:([], [], []) ~f:(fun (dailies, pre, odd) name ->
        match classify name with
        | Daily d -> ((name, d) :: dailies, pre, odd)
        | Pre_deploy -> (dailies, name :: pre, odd)
        | Unrecognised -> (dailies, pre, name :: odd))
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
    @ pre_deleted
  in
  (List.sort kept ~compare:String.compare, List.sort deleted ~compare:String.compare)
