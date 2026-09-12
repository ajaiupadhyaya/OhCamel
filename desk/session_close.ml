(* The session close.

   Design §3.10, and the fix for a defect found while writing it: the live
   engine backfilled sixty daily returns at startup and never pushed another,
   so after a week of uptime its VaR window was a week stale; and it never
   marked equity, so its drawdown had no history. Once a session, five minutes
   after the venue's close, this rolls every return window by the session's
   return, marks equity, and writes the session, its marks and one forecast per
   estimator into the journal.

   THE ORDER IS THE POINT. Returns roll first and the forecast is taken after,
   so the forecast written for tomorrow is the one the engine will actually be
   running tomorrow -- point in time, as the backtest's rolling origin is.

   ALL OR NOTHING. A daily bar can arrive late for one name. Rolling the names
   that have one and not the others would leave windows ending on different
   days, and the covariance of misaligned windows is a number about no real
   portfolio. So a close whose bars are incomplete tries again, and if they
   stay incomplete it records the session without rolling any window, and
   says so.

   The session row is written LAST. Its presence means the close completed; a
   crash before it leaves marks and forecasts that the retried close replaces,
   and a present row means the date is never rolled a second time. *)

open Core
open Async
open Ohcamel.Types
module Graph = Ohcamel.Graph

let delay = Time_ns.Span.of_min 5.0

let due (clock : Venue.Session_clock.t) =
  Time_ns.add clock.Venue.Session_clock.next_close delay

let session_return ~(date : Date.t) (bars : Venue.Bar.t list) : float option =
  match List.rev bars with
  | latest :: previous :: _
    when Date.equal latest.Venue.Bar.date date
         && Float.( > ) previous.Venue.Bar.close 0.0
         && Float.( > ) latest.Venue.Bar.close 0.0 ->
      Some ((latest.Venue.Bar.close /. previous.Venue.Bar.close) -. 1.0)
  | _ -> None

let roll_plan ~(date : Date.t) ~(symbols : Symbol.t list)
    (bars : Venue.Bar.t list Symbol.Map.t) =
  let results =
    List.map symbols ~f:(fun s ->
        (s, Option.bind (Map.find bars s) ~f:(session_return ~date)))
  in
  match
    List.filter_map results ~f:(fun (s, r) -> if Option.is_none r then Some s else None)
  with
  | [] -> Ok (List.map results ~f:(fun (s, r) -> (s, Option.value_exn r)))
  | missing ->
      Error
        (sprintf "no %s bar yet for %s" (Date.to_string date)
           (String.concat ~sep:", " (List.map missing ~f:Symbol.to_string)))

(* The graph's own rule for a dollar VaR (graph.ml, to_notional): the
   return-space fraction times gross, because the weights are normalised by
   gross. The parametric estimators have no notional node, so the same
   multiplication is applied to their fractions here -- by the one number the
   graph multiplies by, not by a second definition of VaR. *)
let forecasts ~(date : Date.t) ~(confidence : float) (s : Graph.Snapshot.t) :
    Journal.Forecast.t list =
  let gross = Notional.to_float (Graph.Snapshot.gross_exposure s) in
  let dollars = Option.map ~f:(fun f -> f *. gross) in
  let row estimator var_fraction var_notional es_notional =
    {
      Journal.Forecast.date;
      estimator;
      confidence;
      var_fraction;
      var_notional;
      es_notional;
    }
  in
  [
    row "ewma"
      (Graph.Snapshot.parametric_var_ewma s)
      (dollars (Graph.Snapshot.parametric_var_ewma s))
      None;
    row "historical"
      (Graph.Snapshot.historical_var s)
      (Option.map (Graph.Snapshot.value_at_risk_notional s) ~f:Notional.to_float)
      (Option.map (Graph.Snapshot.expected_shortfall_notional s) ~f:Notional.to_float);
    row "parametric"
      (Graph.Snapshot.parametric_var s)
      (dollars (Graph.Snapshot.parametric_var s))
      None;
  ]

let marks ~(date : Date.t) (s : Graph.Snapshot.t) : Journal.Mark.t list =
  Map.to_alist (Graph.Snapshot.prices s)
  |> List.map ~f:(fun (symbol, price) ->
      {
        Journal.Mark.date;
        symbol;
        close = Price.to_float price;
        qty = Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) symbol);
      })

let record ~(graph : Graph.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(returns : (Symbol.t * float) list) ~(mark_equity : bool) ~(confidence : float)
    ~(recorded_at : Time_ns.t) =
  match Journal.session journal date with
  | Some _ -> `Already_recorded
  | None ->
      List.iter returns ~f:(fun (symbol, r) -> Graph.push_return graph symbol r);
      if mark_equity then Graph.mark_equity graph;
      let s = Graph.snapshot graph in
      Journal.record_marks journal (marks ~date s);
      Journal.record_forecasts journal (forecasts ~date ~confidence s);
      Journal.record_session journal
        {
          Journal.Session.date;
          equity_close = Notional.to_float (Graph.Snapshot.equity s);
          cash_close = Notional.to_float (Graph.cash graph);
          gross_close = Notional.to_float (Graph.Snapshot.gross_exposure s);
          net_close = Notional.to_float (Graph.Snapshot.net_exposure s);
          recorded_at;
        };
      `Recorded

let restore ~(graph : Graph.t) ~(journal : Journal.t) : int =
  let closes =
    List.map (Journal.sessions journal) ~f:(fun s -> s.Journal.Session.equity_close)
  in
  Graph.set_equity_history graph (Array.of_list closes);
  Graph.stabilize graph;
  List.length closes

let retry_every = Time_ns.Span.of_min 10.0
let attempts = 6

let run_forever ~(read : Venue.Read.t) ~(graph : Graph.t) ~(journal : Journal.t)
    ~(confidence : float) ~(on_event : string -> unit) : unit Deferred.t =
  let symbols = Graph.symbols graph in
  let rec wait_for_close () =
    match%bind read.Venue.Read.clock () with
    | Error e ->
        on_event
          (sprintf
             "session close: the venue's clock is unavailable (%s); asking again in 10 \
              min"
             (Error.to_string_hum e));
        let%bind () = Clock_ns.after retry_every in
        wait_for_close ()
    | Ok clock ->
        let%bind () = Clock_ns.at (due clock) in
        close clock.Venue.Session_clock.next_close_date 1
  and close date attempt =
    let%bind plan =
      match%map read.Venue.Read.daily_bars symbols ~days:2 with
      | Error e -> Error (Error.to_string_hum e)
      | Ok bars -> roll_plan ~date ~symbols bars
    in
    match plan with
    | Error why when attempt < attempts ->
        on_event
          (sprintf
             "session %s: windows not rolled yet (%s); attempt %d of %d, again in 10 min"
             (Date.to_string date) why attempt attempts);
        let%bind () = Clock_ns.after retry_every in
        close date (attempt + 1)
    | _ ->
        let returns, how =
          match plan with
          | Ok r -> (r, sprintf "%d windows rolled" (List.length r))
          | Error why -> ([], "windows NOT rolled: " ^ why)
        in
        (match
           Or_error.try_with (fun () ->
               record ~graph ~journal ~date ~returns ~mark_equity:true ~confidence
                 ~recorded_at:(Time_ns.now ()))
         with
        | Ok `Recorded ->
            on_event (sprintf "session %s recorded, %s" (Date.to_string date) how)
        | Ok `Already_recorded ->
            on_event (sprintf "session %s was already recorded" (Date.to_string date))
        | Error e ->
            on_event
              (sprintf "session %s: the record FAILED: %s" (Date.to_string date)
                 (Error.to_string_hum e)));
        (* Past this close before the clock is asked again, so its answer is the
           next session's close and not this one's. *)
        let%bind () = Clock_ns.after (Time_ns.Span.of_min 1.0) in
        wait_for_close ()
  in
  wait_for_close ()
