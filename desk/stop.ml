(* INC-01's stop rules, decided at the close.

   Per strategy, in the book before it trades: a drawdown from peak on allocated
   capital, and a count of sessions since its first fill. Either one stops it. A
   stopped strategy is recorded with the rule that fired (strategy_state), said
   once in the alerts, sized no more, and flattened by the intake's adjust pass.
   The desk never un-stops: only the owner's (reinstated DATE), later than the
   stop, brings a strategy back, at the next start, and its record then begins
   again at that date, because the fall before it is what the stop answered.

   Nothing evaluated these until 2026-11-03, while 23 strategies traded with them
   in the book. The record is Record.sessions_of over the strategy's own fills,
   so a stop and the page read one drawdown. *)

open Core
module Spec = Ohcamel.Config.Book.Signals_spec

let check ~(spec : Spec.Stop.t) ~(record : Record.summary) ~sessions =
  if Float.( >= ) record.Record.max_drawdown spec.Spec.Stop.drawdown then
    `Stop
      ( "drawdown",
        sprintf "drawdown %.1f%% reached its line of %.1f%%"
          (100.0 *. record.Record.max_drawdown)
          (100.0 *. spec.Spec.Stop.drawdown) )
  else if sessions >= spec.Spec.Stop.sessions then
    `Stop
      ( "sessions",
        sprintf "%d sessions since its first fill, the book's count of %d" sessions
          spec.Spec.Stop.sessions )
  else `Continue

let close_of ~zone date =
  Time_ns.of_date_ofday ~zone date (Time_ns.Ofday.create ~hr:16 ())

(* A session's capital is the most the strategy had allocated in force at any
   moment from the previous close to this one. The day's P&L was earned on the
   position before any re-size inside it: when the governor cut a strategy to a
   fifth at 09:45, as it does when the book is losing, the allocation at the close
   made a 3% loss read 15%, and a stop could fire on that alone. The largest never
   inflates a return, and a session sized to 0 before its sale still counts. *)
let capital ~zone ~allocations ~previous date =
  let close = close_of ~zone date in
  let from = Option.map previous ~f:(close_of ~zone) in
  List.fold allocations ~init:(None, 0.0) ~f:(fun (held, most) (at, dollars) ->
      if Time_ns.( > ) at close then (held, most)
      else
        match from with
        | Some from when Time_ns.( <= ) at from -> (Some dollars, most)
        | Some _ | None -> (held, Float.max most dollars))
  |> fun (held, most) -> Float.max (Option.value held ~default:0.0) most

let sessions ~journal ~zone ~strategy ~since =
  match Journal.source_fill_rows journal ~prefix:(Rebalance.Source.prefix ~strategy) with
  | [] -> []
  | (_, _, _, first) :: _ as rows -> (
      let first_date = Time_ns.to_date first ~zone in
      let closes = Hashtbl.Poly.create () in
      List.iter (Journal.closes_since journal ~since:first_date) ~f:(fun (date, s, c) ->
          Hashtbl.set closes ~key:(date, Ohcamel.Types.Symbol.to_string s) ~data:c);
      let allocations = Journal.allocations journal ~strategy in
      let all_dates = Journal.session_dates journal in
      let previous =
        match all_dates with
        | [] -> Date.Map.empty
        | _ :: later ->
            Date.Map.of_alist_reduce
              ~f:(fun a _ -> a)
              (List.map2_exn (List.drop_last_exn all_dates) later ~f:(fun p d -> (d, p)))
      in
      let allocated date =
        capital ~zone ~allocations ~previous:(Map.find previous date) date
      in
      let record =
        Record.sessions_of
          ~dates:(List.filter all_dates ~f:(fun date -> Date.( >= ) date first_date))
          ~close:(fun date s ->
            Hashtbl.find closes (date, Ohcamel.Types.Symbol.to_string s))
          ~allocated
          (List.map rows ~f:(fun (symbol, qty, price, at) ->
               { Record.date = Time_ns.to_date at ~zone; symbol; qty; price }))
      in
      match since with
      | None -> record
      | Some since ->
          List.filter record ~f:(fun (s : Record.session) -> Date.( >= ) s.date since))

let stopped journal name =
  match Journal.strategy_state journal ~strategy:name with
  | Some (`Stopped, _, _, _) -> true
  | Some (`Active, _, _, _) | None -> false

let evaluate_one ~journal ~zone ~at ~on_event (s : Spec.Strategy.t) =
  match s.stop with
  | None -> None
  | Some _ when stopped journal s.name -> None
  | Some spec -> (
      let record = sessions ~journal ~zone ~strategy:s.name ~since:s.reinstated in
      match
        check ~spec ~record:(Record.summarise record) ~sessions:(List.length record)
      with
      | `Continue -> None
      | `Stop (rule, detail) ->
          Journal.set_strategy_state journal ~strategy:s.name ~state:`Stopped
            ~rule:(Some rule) ~at ~detail;
          let line =
            sprintf
              "%s STOPPED: %s. It is sized no more and its position is flattened; only a \
               (reinstated DATE) in the book brings it back"
              s.name detail
          in
          Journal.record_alert journal
            { Journal.Alert.at; kind = "incubation_stopped"; limit_name = s.name; line };
          on_event ("stop      " ^ line);
          Some s.name)

(* Each strategy is guarded on its own, so a read that fails for one leaves every
   other evaluated, and is said. *)
let evaluate ~journal ~strategies ~zone ~at ~on_event =
  List.filter_map strategies ~f:(fun (s : Spec.Strategy.t) ->
      match Or_error.try_with (fun () -> evaluate_one ~journal ~zone ~at ~on_event s) with
      | Ok stopped -> stopped
      | Error e ->
          on_event
            (sprintf "stop      %s could not be evaluated: %s" s.name
               (Error.to_string_hum e));
          None)

let reinstate ~journal ~zone ~strategies ~on_event =
  List.iter strategies ~f:(fun (s : Spec.Strategy.t) ->
      match (s.reinstated, Journal.strategy_state journal ~strategy:s.name) with
      | Some date, Some (`Stopped, _, at, _) -> (
          match Desk_time.parse at with
          | Some stopped_at when Date.( > ) date (Time_ns.to_date stopped_at ~zone) ->
              let detail = sprintf "reinstated by the book on %s" (Date.to_string date) in
              Journal.set_strategy_state journal ~strategy:s.name ~state:`Active
                ~rule:None ~at:(Time_ns.now ()) ~detail;
              on_event (sprintf "stop      %s %s" s.name detail)
          | Some _ | None -> ())
      | _ -> ())
