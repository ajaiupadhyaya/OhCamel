(* The risk model, scored (Task 1.10).

   Every session close journals a one-day VaR forecast per estimator
   ([Session_close.forecasts]). This module tests those forecasts against what
   happened -- Kupiec's coverage test, Christoffersen's independence test and
   the Basel traffic light, all in [Ohcamel.Var_backtest.score] -- and says the
   result as counts, p-values and a light. It never says a size: nothing here
   reads or returns a dollar figure, and the realised numbers it builds are
   fractions of gross that never leave this file.

   WHICH REALISED P&L. The forecast made at the close of day d is a statement
   about the positions held at that close, so the realised number is what THOSE
   positions made over the next session: each name's quantity at d's mark times
   its move from d's close to the next recorded session's close, over the gross
   of d's marks (the unit of [var_fraction], which is a fraction of gross). That
   is the hypothetical, or clean, P&L -- Basel's own backtesting convention --
   and not the book's actual session P&L, which also holds the day's trading,
   fees and dividends, none of which the forecast was about. The marks table
   supports it exactly (a close and a quantity per name per session); the
   account's equity would not, since a trade during the session changes it
   without being a forecast error.

   What it does not see: a distribution or split no record dates moves the
   raw close like a loss (the close's own check, [Session_close.unrecorded_moves],
   defers the drawdown rule for it, and this score does not); a name whose mark
   of the next session is missing cannot be repriced and the pair is left out
   and counted; and a close not recorded between two sessions is a gap, not a
   day, so a pair more than [max_gap_days] calendar days apart is left out and
   counted. The journal holds no calendar, so a single missed mid-week close
   (two days apart) is not told from a holiday weekend.

   WHICH WINDOW. Task 1.9 moved the return window from 60 to 250 sessions and
   journals it on every forecast. A series is never mixed across that move:
   only the forecasts made over the window of the NEWEST forecast are scored (a
   row journaled before the window was recorded was a 60-session forecast, and
   counts as 60), and the sessions made under another window are reported as a
   count. The window is on the score, so a reader can see which model it is
   about.

   WHAT THE JOURNAL DOES NOT RECORD. Historical and parametric VaR align every
   declared name, so a zero-held name with a short history can shorten the
   window VaR actually ran over (lib/graph.ml); the forecast row records the
   window asked for, not the number of observations used. The score is of the
   forecasts as made. *)

open Core
module VB = Ohcamel.Var_backtest

(* A pair of recorded sessions further apart than this is not one day: a long
   weekend with a holiday is four. *)
let max_gap_days = 4

(* The window a forecast journaled before the window was recorded was made
   over: every one of them, per the journal. *)
let unrecorded_window = 60

type realised = {
  returns : (Date.t * float) list;
      (** Keyed by the date the forecast was MADE: the hypothetical return, as a fraction
          of gross, over the session that followed. *)
  unscorable : int;
  through : Date.t option;  (** The newest session a return runs to. *)
}

let hypothetical_returns ~(sessions : Date.t list)
    ~(marks : Date.t -> Journal.Mark.t list) : realised =
  let cache = Date.Table.create () in
  let marks d = Hashtbl.find_or_add cache d ~default:(fun () -> marks d) in
  let pairs =
    match List.sort sessions ~compare:Date.compare with
    | [] -> []
    | _ :: rest as all -> List.zip_exn (List.drop_last_exn all) rest
  in
  let results =
    List.map pairs ~f:(fun (d, next) ->
        let usable (m : Journal.Mark.t) =
          Float.is_finite m.close && Float.( > ) m.close 0.0 && Float.is_finite m.qty
        in
        let held =
          List.filter (marks d) ~f:(fun m -> usable m && not (Float.equal m.qty 0.0))
        in
        let gross =
          List.sum (module Float) held ~f:(fun m -> Float.abs (m.qty *. m.close))
        in
        let after =
          List.filter (marks next) ~f:usable
          |> List.map ~f:(fun (m : Journal.Mark.t) -> (m.symbol, m.close))
          |> Ohcamel.Types.Symbol.Map.of_alist_reduce ~f:(fun _ later -> later)
        in
        let changes =
          List.map held ~f:(fun m ->
              Option.map (Map.find after m.symbol) ~f:(fun c -> m.qty *. (c -. m.close)))
        in
        if
          Date.diff next d > max_gap_days
          || List.is_empty held || Float.( <= ) gross 0.0
          || List.exists changes ~f:Option.is_none
        then Error ()
        else
          Ok (d, next, List.sum (module Float) (List.filter_opt changes) ~f:Fn.id /. gross))
  in
  {
    returns =
      List.filter_map results ~f:(function
        | Ok (d, _, r) -> Some (d, r)
        | Error () -> None);
    unscorable = List.count results ~f:Result.is_error;
    through =
      List.filter_map results ~f:(function
        | Ok (_, next, _) -> Some next
        | Error () -> None)
      |> List.max_elt ~compare:Date.compare;
  }

type estimator_score = { estimator : string; n : int; score : VB.score option }

type t = {
  window : int option;
  level : float option;
  as_of : Date.t option;
  scores : estimator_score list;
  other_window_sessions : int;
  unscorable : int;
}

let window_of (f : Journal.Forecast.t) =
  Option.value f.return_window ~default:unrecorded_window

let score ~(forecasts : Journal.Forecast.t list) ~(realised : realised) : t =
  match
    List.max_elt forecasts ~compare:(fun (a : Journal.Forecast.t) b ->
        Date.compare a.date b.date)
  with
  | None ->
      {
        window = None;
        level = None;
        as_of = None;
        scores = [];
        other_window_sessions = 0;
        unscorable = realised.unscorable;
      }
  | Some newest ->
      let window = window_of newest and level = newest.confidence in
      let ours, others = List.partition_tf forecasts ~f:(fun f -> window_of f = window) in
      let other_window_sessions =
        List.map others ~f:(fun (f : Journal.Forecast.t) -> f.date)
        |> Date.Set.of_list |> Set.length
      in
      let realised_dates = Date.Set.of_list (List.map realised.returns ~f:fst) in
      let scores =
        List.filter ours ~f:(fun f ->
            Float.( < ) (Float.abs (f.confidence -. level)) 1e-9)
        |> List.sort_and_group ~compare:(fun (a : Journal.Forecast.t) b ->
            String.compare a.estimator b.estimator)
        |> List.map ~f:(fun group ->
            let estimator = (List.hd_exn group).Journal.Forecast.estimator in
            let forecasts =
              List.filter_map group ~f:(fun (f : Journal.Forecast.t) ->
                  Option.bind f.var_fraction ~f:(fun v ->
                      Option.some_if
                        (Float.is_finite v && Set.mem realised_dates f.date)
                        (f.date, v)))
            in
            {
              estimator;
              n = List.length forecasts;
              score = VB.score ~forecasts ~realised:realised.returns ~level;
            })
      in
      {
        window = Some window;
        level = Some level;
        as_of = realised.through;
        scores;
        other_window_sessions;
        unscorable = realised.unscorable;
      }

(* The newest 250 sessions' forecasts (251 sessions make 250 pairs): a year, the
   window Basel's table is written for, and a bound -- this runs on the demo
   host, which records a session every five minutes, and a score that read every
   session ever written would grow with the uptime. *)
let sessions_scored = 250

let of_journal (journal : Journal.t) : t =
  let sessions =
    List.map
      (Journal.recent_sessions journal ~limit:(sessions_scored + 1))
      ~f:(fun s -> s.Journal.Session.date)
  in
  let forecasts =
    match sessions with
    | [] -> []
    | oldest :: _ -> Journal.forecasts_since journal ~date:oldest
  in
  score ~forecasts
    ~realised:(hypothetical_returns ~sessions ~marks:(Journal.marks journal))

let source =
  "Each close's journaled forecast, tested against the return its positions made over \
   the next session"

let to_json (t : t) : Yojson.Safe.t =
  let num x = if Float.is_finite x then `Float x else `Null in
  let opt f = function None -> `Null | Some x -> f x in
  let one (e : estimator_score) =
    match e.score with
    | Some s ->
        `Assoc
          [
            ("estimator", `String e.estimator);
            ("n", `Int s.n);
            ("exceptions", `Int s.exceptions);
            ("kupiec_p", num s.kupiec_p);
            ("christoffersen_p", num s.christoffersen_p);
            ( "light",
              `String
                (match s.light with
                | `Green -> "green"
                | `Yellow -> "yellow"
                | `Red -> "red") );
            ("message", `Null);
          ]
    | None ->
        `Assoc
          [
            ("estimator", `String e.estimator);
            ("n", `Int e.n);
            ("exceptions", `Null);
            ("kupiec_p", `Null);
            ("christoffersen_p", `Null);
            ("light", `Null);
            ("message", `String (sprintf "%d of %d sessions scored" e.n VB.min_sessions));
          ]
  in
  `Assoc
    [
      ("source", `String source);
      ("as_of", opt (fun d -> `String (Date.to_string d)) t.as_of);
      ("window", opt (fun w -> `Int w) t.window);
      ("level", opt num t.level);
      ("min_sessions", `Int VB.min_sessions);
      ("other_window_sessions", `Int t.other_window_sessions);
      ("unscorable_sessions", `Int t.unscorable);
      ("scores", `List (List.map t.scores ~f:one));
    ]
