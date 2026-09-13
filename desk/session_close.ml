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

   A CLOSE RECORDS THE ACCOUNT, OR NOTHING. A session row is the account's
   equity, and [restore] puts it back into the drawdown's trail on every start
   after. Until a sync has applied the account's book, and while the last one
   applied is recent, the graph may hold something else -- at startup, the
   book file's quantities and cash -- so a close whose book is not current
   tries again on the same schedule as late bars, and if it stays that way it
   records nothing, and says so.

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

(* Whether a close whose journal write failed is tried again. Task 7's ruling:
   until it is recorded or a LATER close is due. Pure, so the one decision the
   retry loop makes can be tested without a scheduler.

   A later date on the clock is no signal by itself: every venue clock read
   after a close already names the next session, and giving up on that gave up
   at the first retry. What ends the retry is the later close coming DUE,
   because past that point holding this date would cost the next one. And
   [clock] has to be one read after the failure and kept, not one read at
   [now]: a venue names a close still ahead of it, so a clock read at [now] is
   never past its own due time. *)
let retry_or_give_up ~(now : Time_ns.t) ~(date : Date.t) (clock : Venue.Session_clock.t) =
  if
    Date.( > ) clock.Venue.Session_clock.next_close_date date
    && Time_ns.( >= ) now (due clock)
  then `Give_up
  else `Retry

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

(* The journal is the record, and the live graph must never be ahead of it.
   Roll and mark a FORK, not the live graph -- the fork shares the same code,
   so what it computes is exactly what the live graph would have computed --
   and write the journal from what the fork saw. Only once every write has
   committed does the live graph get the same returns and the same mark.

   A write can raise (a disk error, a locked file). Before this, that raised
   AFTER the live graph had already rolled: Journal.session for the date still
   answered None, so whatever retried the close rolled the same day's return a
   second time on top of the first, with nothing in the journal to show even
   the first roll had happened. Computing on a fork means a raise here leaves
   the live graph exactly where it found it -- still one roll behind, same as
   the journal -- so a retry after a failed write records the session, and
   rolls its windows, exactly once. *)
let record ~(graph : Graph.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(returns : (Symbol.t * float) list) ~(mark_equity : bool) ~(confidence : float)
    ~(recorded_at : Time_ns.t) =
  match Journal.session journal date with
  | Some _ -> `Already_recorded
  | None ->
      let apply (g : Graph.t) =
        List.iter returns ~f:(fun (symbol, r) -> Graph.push_return g symbol r);
        if mark_equity then Graph.mark_equity g
      in
      let fork = Graph.fork graph in
      Exn.protect
        ~finally:(fun () -> Graph.destroy fork)
        ~f:(fun () ->
          apply fork;
          let s = Graph.snapshot fork in
          Journal.record_marks journal (marks ~date s);
          Journal.record_forecasts journal (forecasts ~date ~confidence s);
          Journal.record_session journal
            {
              Journal.Session.date;
              equity_close = Notional.to_float (Graph.Snapshot.equity s);
              cash_close = Notional.to_float (Graph.cash fork);
              gross_close = Notional.to_float (Graph.Snapshot.gross_exposure s);
              net_close = Notional.to_float (Graph.Snapshot.net_exposure s);
              recorded_at;
            });
      apply graph;
      `Recorded

(* The journal's closes, back into the equity trail. bin/main.ml calls this
   from the desk's first applied sync and never earlier: the closes are the
   account's equity, and set beside a graph still holding the book file's
   quantities and cash, the stabilize below would measure the file's equity
   against the account's peak -- a drawdown no account had, and an armed kill
   switch latched by it. *)
let restore ~(graph : Graph.t) ~(journal : Journal.t) : int =
  let closes =
    List.map (Journal.sessions journal) ~f:(fun s -> s.Journal.Session.equity_close)
  in
  Graph.set_equity_history graph (Array.of_list closes);
  Graph.stabilize graph;
  List.length closes

(* [record], for a close run from a venue's clock: only a book that is the
   account's as of a recent read is written down. The demo calls [record]
   directly, because its venue is simulated from the book it marks, which is
   current by construction. *)
let record_if_current ~(book_is_current : unit -> bool) ~graph ~journal ~date ~returns
    ~mark_equity ~confidence ~recorded_at =
  if book_is_current () then
    record ~graph ~journal ~date ~returns ~mark_equity ~confidence ~recorded_at
  else `Book_not_current

let retry_every = Time_ns.Span.of_min 10.0
let attempts = 6

let not_current =
  "the desk has not applied a recent read of the account, so the book may not be the \
   account's"

let run_forever ~(read : Venue.Read.t) ~(graph : Graph.t) ~(journal : Journal.t)
    ~(confidence : float) ~(book_is_current : unit -> bool) ~(on_event : string -> unit) :
    unit Deferred.t =
  let symbols = Graph.symbols graph in
  let day = Date.to_string in
  let record_now date returns =
    Or_error.try_with (fun () ->
        record_if_current ~book_is_current ~graph ~journal ~date ~returns
          ~mark_equity:true ~confidence ~recorded_at:(Time_ns.now ()))
  in
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
    | Ok clock -> close_when_due clock
  and close_when_due clock =
    let%bind () = Clock_ns.at (due clock) in
    close clock.Venue.Session_clock.next_close_date 1
  and close date attempt =
    let%bind plan =
      match%map read.Venue.Read.daily_bars symbols ~days:2 with
      | Error e -> Error (Error.to_string_hum e)
      | Ok bars -> roll_plan ~date ~symbols bars
    in
    let last = attempt >= attempts in
    match plan with
    | Error why when not last ->
        on_event
          (sprintf
             "session %s: windows not rolled yet (%s); attempt %d of %d, again in 10 min"
             (day date) why attempt attempts);
        let%bind () = Clock_ns.after retry_every in
        close date (attempt + 1)
    | _ -> (
        let returns, how =
          match plan with
          | Ok r -> (r, sprintf "%d windows rolled" (List.length r))
          | Error why -> ([], "windows NOT rolled: " ^ why)
        in
        match record_now date returns with
        | Ok `Book_not_current when not last ->
            on_event
              (sprintf
                 "session %s: not recorded yet (%s); attempt %d of %d, again in 10 min"
                 (day date) not_current attempt attempts);
            let%bind () = Clock_ns.after retry_every in
            close date (attempt + 1)
        | Ok `Book_not_current ->
            on_event
              (sprintf "session %s NOT recorded after %d attempts: %s" (day date) attempts
                 not_current);
            wait_for_close ()
        | Ok ((`Recorded | `Already_recorded) as outcome) ->
            journaled date how outcome ~held:None
        | Error e ->
            on_event
              (sprintf
                 "session %s: the record FAILED (%s); the live graph was not touched, \
                  retrying in 10 min"
                 (day date) (Error.to_string_hum e));
            retry date returns how ~why:(Error.to_string_hum e) ~held:None)
  and journaled date how outcome ~held =
    on_event
      (match outcome with
      | `Recorded -> sprintf "session %s recorded, %s" (day date) how
      | `Already_recorded -> sprintf "session %s was already recorded" (day date));
    match held with
    | Some clock -> close_when_due clock
    | None ->
        (* Past this close before the clock is asked again, so its answer is
           the next session's close and not this one's. *)
        let%bind () = Clock_ns.after (Time_ns.Span.of_min 1.0) in
        wait_for_close ()
  (* A write that failed (Session_close.record's own comment: the journal is
     the record) left the live graph untouched, so the safe thing to do is
     exactly what the journal is still waiting for: try the same close again,
     every retry_every. There is no attempt cap, unlike the bar retry above --
     a transient disk error should not cost a session -- and a book that stops
     being current meanwhile is one more reason to wait, never a book to
     record from.

     What stops it is the NEXT close coming due (retry_or_give_up), because
     past that, holding this date costs that one. The clock that decides is
     the first one read after the failure that names a later session, and it
     is KEPT. Asked afresh, a venue always names a close still ahead of it, so
     a re-read clock would never say the later close is due; and once that
     close has passed it names the one after, which is how the session in
     between would be skipped. For the same reason a retry that gives up, or
     records late, goes to the kept clock's session directly instead of asking
     the venue what comes next. *)
  and retry date returns how ~why ~held =
    let%bind () = Clock_ns.after retry_every in
    let again ~held =
      match record_now date returns with
      | Ok ((`Recorded | `Already_recorded) as outcome) ->
          journaled date how outcome ~held
      | Ok `Book_not_current ->
          on_event
            (sprintf "session %s: still not recorded (%s); again in 10 min" (day date)
               not_current);
          retry date returns how ~why:not_current ~held
      | Error e ->
          on_event
            (sprintf
               "session %s: the record FAILED again (%s); the live graph was not \
                touched, retrying in 10 min"
               (day date) (Error.to_string_hum e));
          retry date returns how ~why:(Error.to_string_hum e) ~held
    in
    match%bind
      match held with Some clock -> return (Ok clock) | None -> read.Venue.Read.clock ()
    with
    | Error e ->
        on_event
          (sprintf
             "session close: the venue's clock is unavailable (%s); retrying %s's record \
              anyway"
             (Error.to_string_hum e) (day date));
        again ~held:None
    | Ok clock -> (
        let held =
          if Date.( > ) clock.Venue.Session_clock.next_close_date date then Some clock
          else None
        in
        match retry_or_give_up ~now:(Time_ns.now ()) ~date clock with
        | `Give_up ->
            on_event
              (sprintf
                 "session %s NOT recorded (%s); %s's close is now due, so that session \
                  is closed instead"
                 (day date) why
                 (day clock.Venue.Session_clock.next_close_date));
            close_when_due clock
        | `Retry -> again ~held)
  in
  wait_for_close ()
