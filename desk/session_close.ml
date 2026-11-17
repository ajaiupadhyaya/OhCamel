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

   A NAME THAT DID NOT TRADE IS NOT A LATE BAR. SIZE printed nothing on IEX on
   six of the ten sessions to 2026-10-30, and each time the rule above held back
   every window in the book, for a bar that does not exist at the close. The
   venue files it hours later with no trades and yesterday's close, which is a
   return of zero, and that is what a restart reads back. So a missing bar is
   checked against the name's latest print: older than the session, and the name
   rolls at zero with the rest at once; printed today, or unknown, and it is a
   late bar, and the close waits. If every bar is late, every name printed today
   and nothing is zeroed.

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

(* The names whose latest print is older than the session: they did not trade in
   it, on the feed the bars come from. Absence is not silence -- a name with no
   print on record is unknown, and stays a missing bar. *)
let silent ~(date : Date.t) ~zone ~(prints : Time_ns.t Symbol.Map.t) =
  Map.filter prints ~f:(fun at -> Date.( < ) (Time_ns.to_date at ~zone) date)
  |> Map.keys |> Symbol.Set.of_list

let roll_plan ~(date : Date.t) ~(symbols : Symbol.t list) ~(silent : Symbol.Set.t)
    (bars : Venue.Bar.t list Symbol.Map.t) =
  let results =
    List.map symbols ~f:(fun s ->
        ( s,
          match Option.bind (Map.find bars s) ~f:(session_return ~date) with
          | None when Set.mem silent s -> Some 0.0
          | return -> return ))
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

(* THE OFFICIAL CLOSE (Task 1.8). A session's marks were the graph's price five
   minutes after the close: the feed's last print, which on a thin name can be a
   trade from the afternoon and is never the closing auction. The venue's daily
   bar for the session is its own close, so a mark is taken from it when the
   venue has filed it -- on a half-day too, since the bar is the day's whatever
   hour it ended -- and from the last print only when it has not, tagged
   [`Last_print] so the next close re-marks it from the bar ([remark_recent]).
   On Alpaca's IEX feed the bar's close is IEX's last trade in the session, the
   venue's close and not the consolidated closing print, which would need the
   SIP feed; and a bar read at 16:05 is tagged [`Bar] and kept, never re-marked
   if the venue later revises it. *)
let official_close (bars : Venue.Bar.t Symbol.Map.t)
    ~(last_print : Symbol.t -> float option) ~(date : Date.t) (symbol : Symbol.t) :
    (float * [ `Bar | `Last_print ]) option =
  match Map.find bars symbol with
  | Some (b : Venue.Bar.t)
    when Date.equal b.date date && Float.is_finite b.close && Float.( > ) b.close 0.0 ->
      Some (b.close, `Bar)
  | _ -> Option.map (last_print symbol) ~f:(fun p -> (p, `Last_print))

(* The night's dividends and fees, decided from what the venue answered: every
   activity recorded by its id; a cash dividend once it has gone ex (one ex after
   the session is asked for again on the night it does); the pull marked through
   the session only when both answered, so a failed night is asked again from
   where the last good one ended -- and [`Not_pulled] tells the close to defer
   the drawdown rule tonight. What a strategy cannot be credited with is said on
   the night it arrives: a paid dividend no record dates (within three days of
   its payable date), a fee that names no order, and a second record, under
   another id, for a name's ex-date (both are kept: which is right is not this
   code's to guess). *)
let apply_cash_pull ~(journal : Journal.t) ~(date : Date.t) ~(recorded_at : Time_ns.t)
    ~(activities : Venue.Activity.t list Or_error.t)
    ~(dividends : Venue.Cash_dividend.t list Or_error.t) :
    [ `Pulled | `Not_pulled ] * string list =
  let day = Date.to_string date in
  let added =
    match activities with
    | Ok xs -> Some (Journal.record_activities journal xs ~recorded_at)
    | Error _ -> None
  in
  let known =
    String.Set.of_list
      (List.map (Journal.cash_dividends journal) ~f:(fun (c : Venue.Cash_dividend.t) ->
           c.id))
  in
  let gone_ex =
    match dividends with
    | Ok xs ->
        let gone =
          List.filter xs ~f:(fun (c : Venue.Cash_dividend.t) ->
              Date.( <= ) c.ex_date date)
        in
        Journal.record_cash_dividends journal gone ~recorded_at;
        Some gone
    | Error _ -> None
  in
  let plural n word = sprintf "%d %s%s" n word (if n = 1 then "" else "s") in
  let outcome, headline =
    match (added, gone_ex) with
    | Some a, Some g ->
        Journal.set_cash_pulled_through journal date;
        ( `Pulled,
          sprintf "session %s: dividends and fees pulled: %s, %s gone ex on record" day
            (if List.length a = 1 then "1 new activity"
             else sprintf "%d new activities" (List.length a))
            (plural (List.length g) "cash dividend") )
    | _ ->
        let why what = function
          | Ok _ -> None
          | Error e -> Some (sprintf "%s: %s" what (Error.to_string_hum e))
        in
        ( `Not_pulled,
          sprintf
            "session %s: dividends and fees not pulled (%s); asked again at the next \
             close"
            day
            (String.concat ~sep:"; "
               (List.filter_opt
                  [ why "activities" activities; why "cash dividends" dividends ])) )
  in
  let cash_dividends = Journal.cash_dividends journal in
  let fresh = Option.value added ~default:[] in
  let unmatched =
    Stop.unmatched_dividends ~cash_dividends ~activities:fresh
    |> List.map ~f:(fun (a : Venue.Activity.t) ->
        sprintf
          "session %s: %s paid %.2f on %s with no ex-date on record: credited to no \
           strategy"
          day
          (Option.value_map a.symbol ~default:"?" ~f:Symbol.to_string)
          a.net_amount (Date.to_string a.date))
  in
  let _, unassigned, count = Stop.fees_by_order fresh in
  let fees =
    if count = 0 then []
    else
      [
        sprintf "session %s: %s totalling %.2f names no order: assigned to no strategy"
          day (plural count "account fee") unassigned;
      ]
  in
  let duplicates =
    List.sort_and_group cash_dividends ~compare:(fun (a : Venue.Cash_dividend.t) b ->
        [%compare: Symbol.t * Date.t] (a.symbol, a.ex_date) (b.symbol, b.ex_date))
    |> List.filter_map ~f:(fun group ->
        match group with
        | (first : Venue.Cash_dividend.t) :: _ :: _
          when List.exists group ~f:(fun (c : Venue.Cash_dividend.t) ->
                   not (Set.mem known c.id)) ->
            Some
              (sprintf
                 "session %s: %s has %d dividend records for ex-date %s (%s); each is \
                  credited -- check which the venue meant"
                 day
                 (Symbol.to_string first.symbol)
                 (List.length group)
                 (Date.to_string first.ex_date)
                 (String.concat ~sep:", "
                    (List.map group ~f:(fun (c : Venue.Cash_dividend.t) -> c.id))))
        | _ -> None)
  in
  (outcome, (headline :: unmatched) @ fees @ duplicates)

(* A DISTRIBUTION NOTHING RECORDS (fix round 1). When the venue processes a
   distribution or a split it rescales its ADJUSTED bars before the ex-date, and
   leaves the raw ones as they traded, so the two series' day-over-day ratios
   part on that day. A name whose ratios differ by more than [ratio_tolerance]
   (a hundredth of a percent: a 0.30 dividend on 90 is 0.33%) and that no
   dividend record dates is one whose fall a strategy's record may read as a
   loss; the close defers the drawdown rule of a strategy that held it. A name
   missing any of the four closes is not judged. *)
let ratio_tolerance = 1e-4

let unrecorded_moves ~(date : Date.t) ~(adjusted : Venue.Bar.t list Symbol.Map.t)
    ~(raw_today : Venue.Bar.t Symbol.Map.t) ~(raw_before : Venue.Bar.t Symbol.Map.t)
    ~(recorded : Symbol.t -> bool) : Symbol.t list =
  Map.to_alist adjusted
  |> List.filter_map ~f:(fun (symbol, bars) ->
      let usable (b : Venue.Bar.t) = Float.is_finite b.close && Float.( > ) b.close 0.0 in
      let today = List.find bars ~f:(fun (b : Venue.Bar.t) -> Date.equal b.date date) in
      let before =
        List.filter bars ~f:(fun (b : Venue.Bar.t) -> Date.( < ) b.date date)
        |> List.max_elt ~compare:(fun (a : Venue.Bar.t) b -> Date.compare a.date b.date)
      in
      match (today, before, Map.find raw_today symbol, Map.find raw_before symbol) with
      | Some at, Some ab, Some rt, Some rb
        when Date.equal rt.date date && Date.equal rb.date ab.date
             && List.for_all [ at; ab; rt; rb ] ~f:usable ->
          let adjusted_ratio = at.close /. ab.close
          and raw_ratio = rt.close /. rb.close in
          if
            Float.( > ) (Float.abs ((adjusted_ratio /. raw_ratio) -. 1.0)) ratio_tolerance
            && not (recorded symbol)
          then Some symbol
          else None
      | _ -> None)

(* A last-print mark of [date] re-marked from the bar the venue has since filed;
   the number re-marked. *)
let remark_from ~(journal : Journal.t) ~(date : Date.t) (bars : Venue.Bar.t Symbol.Map.t)
    =
  Journal.last_print_marks journal ~since:date
  |> List.filter ~f:(fun (d, _) -> Date.equal d date)
  |> List.count ~f:(fun (_, symbol) ->
      match official_close bars ~last_print:(fun _ -> None) ~date symbol with
      | Some (close, `Bar) ->
          Journal.remark journal ~date ~symbol ~close;
          true
      | Some (_, `Last_print) | None -> false)

(* Each name's mark at the venue's close, and where that close came from. *)
let marks ~(date : Date.t) ~(session_bars : Venue.Bar.t Symbol.Map.t)
    (s : Graph.Snapshot.t) : (Journal.Mark.t * [ `Bar | `Last_print ]) list =
  let prices = Graph.Snapshot.prices s in
  Map.to_alist prices
  |> List.map ~f:(fun (symbol, price) ->
      let close, source =
        Option.value
          (official_close session_bars
             ~last_print:(fun s -> Option.map (Map.find prices s) ~f:Price.to_float)
             ~date symbol)
          ~default:(Price.to_float price, `Last_print)
      in
      ( {
          Journal.Mark.date;
          symbol;
          close;
          qty = Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) symbol);
        },
        source ))

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
let record ~(session_bars : Venue.Bar.t Symbol.Map.t) ~(graph : Graph.t)
    ~(journal : Journal.t) ~(date : Date.t) ~(returns : (Symbol.t * float) list)
    ~(mark_equity : bool) ~(confidence : float) ~(recorded_at : Time_ns.t) =
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
          let marked = marks ~date ~session_bars s in
          let sources =
            Symbol.Map.of_alist_reduce
              ~f:(fun a _ -> a)
              (List.map marked ~f:(fun ((m : Journal.Mark.t), source) ->
                   (m.symbol, source)))
          in
          Journal.record_marks journal
            ~source:(fun symbol ->
              Option.value (Map.find sources symbol) ~default:`Last_print)
            (List.map marked ~f:fst);
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
let record_if_current ~(book_is_current : unit -> bool) ~session_bars ~graph ~journal
    ~date ~returns ~mark_equity ~confidence ~recorded_at =
  if book_is_current () then
    record ~session_bars ~graph ~journal ~date ~returns ~mark_equity ~confidence
      ~recorded_at
  else `Book_not_current

let retry_every = Time_ns.Span.of_min 10.0
let attempts = 6

(* Asked only for the names with no bar, so an ordinary close makes no extra
   call. A read that fails silences nobody: the bars stay missing and the close
   tries again, as it would have. *)
let ask_silent ~(read : Venue.Read.t) ~(date : Date.t) ~(symbols : Symbol.t list)
    (bars : Venue.Bar.t list Symbol.Map.t) =
  match
    List.filter symbols ~f:(fun s ->
        Option.is_none (Option.bind (Map.find bars s) ~f:(session_return ~date)))
  with
  | [] -> return Symbol.Set.empty
  | missing -> (
      match%map read.Venue.Read.last_prints missing with
      | Error _ -> Symbol.Set.empty
      | Ok prints ->
          let zone =
            Option.value (Lazy.force Desk_time.new_york) ~default:Time_float.Zone.utc
          in
          silent ~date ~zone ~prints)

let not_current =
  "the desk has not applied a recent read of the account, so the book may not be the \
   account's"

(* The night's sub-ledger check runs once a session is on the record
   ([on_recorded]); a night whose session never is says so instead of
   staying silent, so the log never reads as a clean night. *)
let sub_ledger_skipped date =
  sprintf "session %s: sub-ledger not checked: session not recorded" (Date.to_string date)

(* How far back a last-print mark is looked for: two weeks of sessions is long
   past the hours a late bar takes to be filed. *)
let remark_window = 14

(* A step of the night that may raise -- a journal read, a closure the venue
   handed over -- is caught whole, synchronous or not, and said: none of them
   may stop the close's loop. *)
let guarded ~(on_event : string -> unit) ~(what : string) ~(date : Date.t) ~(default : 'a)
    (f : unit -> 'a Deferred.t) : 'a Deferred.t =
  match%map Monitor.try_with ~extract_exn:true ~rest:`Log f with
  | Ok x -> x
  | Error exn ->
      on_event
        (sprintf "session %s: %s failed: %s" (Date.to_string date) what
           (Exn.to_string exn));
      default

let remark_recent ~(read : Venue.Read.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(on_event : string -> unit) : unit Deferred.t =
  guarded ~on_event ~what:"re-marking from the bar" ~date ~default:() (fun () ->
      let pending =
        Journal.last_print_marks journal ~since:(Date.add_days date (-remark_window))
        |> List.filter ~f:(fun (d, _) -> Date.( < ) d date)
        |> List.Assoc.sort_and_group ~compare:Date.compare
      in
      Deferred.List.iter ~how:`Sequential pending ~f:(fun (d, symbols) ->
          match%map read.Venue.Read.session_bars symbols ~date:d with
          | Error e ->
              on_event
                (sprintf "session %s: marks of %s not re-marked from the bar: %s"
                   (Date.to_string date) (Date.to_string d) (Error.to_string_hum e))
          | Ok bars -> (
              match remark_from ~journal ~date:d bars with
              | 0 -> ()
              | n ->
                  on_event
                    (sprintf "session %s: %d mark%s of %s re-marked from the venue's bar"
                       (Date.to_string date) n
                       (if n = 1 then "" else "s")
                       (Date.to_string d)))))

(* The symbols any strategy's own orders have filled in: whose dividends its
   record may be owed. The hand's are not a strategy's. *)
let strategy_symbols (journal : Journal.t) =
  Journal.fills_by_source journal
  |> List.filter_map ~f:(fun (source, symbol, _) ->
      Option.some_if (String.is_prefix source ~prefix:"signal:") symbol)
  |> Symbol.Set.of_list |> Set.to_list

(* A process date runs to about the payable date, so the window for the venue's
   cash dividends reaches back a month before the last pull, for one processed
   ahead of its payable date, and three months ahead of the session, for an
   ex-date already passed whose cash is still to come. [`Not_pulled] -- a read
   that failed or raised -- defers the night's drawdown rule. *)
let pull_cash ~(read : Venue.Read.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(on_event : string -> unit) : [ `Pulled | `Not_pulled ] Deferred.t =
  guarded ~on_event ~what:"pulling the venue's dividends and fees" ~date
    ~default:`Not_pulled (fun () ->
      let since =
        match Journal.cash_pulled_through journal with
        | Some d -> d
        | None -> ( match Journal.session_dates journal with d :: _ -> d | [] -> date)
      in
      let%bind activities = read.Venue.Read.activities ~since ~kinds:[ "DIV"; "FEE" ] in
      (* The strategies' names, and any name the venue just paid a dividend on, so
         a paid dividend finds its record in the same pull. *)
      let symbols =
        (strategy_symbols journal
        @
        match activities with
        | Ok xs -> List.filter_map xs ~f:(fun (a : Venue.Activity.t) -> a.symbol)
        | Error _ -> [])
        |> Symbol.Set.of_list |> Set.to_list
      in
      let%map dividends =
        read.Venue.Read.cash_dividends symbols ~start:(Date.add_days since (-30))
          ~end_:(Date.add_days date 90)
      in
      let outcome, lines =
        apply_cash_pull ~journal ~date ~recorded_at:(Time_ns.now ()) ~activities
          ~dividends
      in
      List.iter lines ~f:on_event;
      outcome)

(* The names whose bars show a distribution or split no record dates (see
   [unrecorded_moves]): the day before's raw bar is asked for here, beside the
   adjusted series the close already read and today's raw bars. A read that
   fails judges nothing, and says so. *)
let unrecorded_today ~(read : Venue.Read.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(adjusted : Venue.Bar.t list Symbol.Map.t) ~(session_bars : Venue.Bar.t Symbol.Map.t)
    ~(on_event : string -> unit) : Symbol.t list Deferred.t =
  guarded ~on_event ~what:"checking the bars for an unrecorded distribution" ~date
    ~default:[] (fun () ->
      let before =
        Map.data adjusted |> List.concat
        |> List.filter ~f:(fun (b : Venue.Bar.t) -> Date.( < ) b.date date)
        |> List.map ~f:(fun (b : Venue.Bar.t) -> b.date)
        |> List.max_elt ~compare:Date.compare
      in
      match before with
      | None -> return []
      | Some before -> (
          match%map read.Venue.Read.session_bars (Map.keys adjusted) ~date:before with
          | Error e ->
              on_event
                (sprintf
                   "session %s: %s's raw bars could not be read, so no distribution was \
                    looked for: %s"
                   (Date.to_string date) (Date.to_string before) (Error.to_string_hum e));
              []
          | Ok raw_before ->
              let recorded symbol =
                List.exists (Journal.cash_dividends journal)
                  ~f:(fun (c : Venue.Cash_dividend.t) ->
                    Symbol.equal c.symbol symbol && Date.equal c.ex_date date)
              in
              unrecorded_moves ~date ~adjusted ~raw_today:session_bars ~raw_before
                ~recorded))

(* AFTER A SESSION IS ON THE RECORD, in this order, because each step changes
   what the next reads: last-print marks of the sessions before re-marked from
   the bars now filed; the venue's dividends and fees pulled; the bars checked
   for a distribution nothing records; then INC-01's stop rules, with the
   drawdown rule deferred for every strategy on a night the pull failed, and for
   a strategy that held a name the check flagged; then the night's sub-ledger
   check. Every step is guarded: a failure is said and the close's loop goes
   on. *)
let after_recorded ~(strategies : Ohcamel.Config.Book.Signals_spec.Strategy.t list)
    ~(read : Venue.Read.t) ~(journal : Journal.t) ~(date : Date.t)
    ~(adjusted : Venue.Bar.t list Symbol.Map.t) ~(session_bars : Venue.Bar.t Symbol.Map.t)
    ~(on_recorded : Date.t -> unit) ~(on_event : string -> unit) : unit Deferred.t =
  let day = Date.to_string date in
  let zone = Option.value (Lazy.force Desk_time.new_york) ~default:Time_float.Zone.utc in
  (match
     Or_error.try_with (fun () ->
         let marks = Journal.marks journal date in
         let from_bar =
           List.count marks ~f:(fun (m : Journal.Mark.t) ->
               Poly.equal (Journal.mark_source journal date m.symbol) (Some `Bar))
         in
         (from_bar, List.length marks))
   with
  | Ok (_, 0) -> ()
  | Ok (n, m) ->
      on_event
        (sprintf "session %s: %d of %d marks from the venue's bar%s" day n m
           (if n = m then "" else "; the rest from last prints, re-marked once filed"))
  | Error _ -> ());
  let%bind () = remark_recent ~read ~journal ~date ~on_event in
  let%bind pulled = pull_cash ~read ~journal ~date ~on_event in
  let%bind unrecorded =
    unrecorded_today ~read ~journal ~date ~adjusted ~session_bars ~on_event
  in
  (match pulled with
  | `Pulled -> ()
  | `Not_pulled ->
      on_event
        (sprintf
           "session %s: the drawdown rule is deferred tonight for every strategy: the \
            venue's dividends could not be pulled, so an ex-date's fall may lack its \
            credit; the sessions rule runs"
           day));
  let defer_drawdown strategy =
    match pulled with
    | `Not_pulled -> Some "the venue's dividends could not be pulled tonight"
    | `Pulled -> (
        let held = Stop.held_before ~journal ~zone ~strategy date in
        match List.filter unrecorded ~f:(List.mem held ~equal:Symbol.equal) with
        | [] -> None
        | names ->
            Some
              (sprintf
                 "%s moved by a distribution or split no record dates (the venue's \
                  adjusted and raw closes disagree)"
                 (String.concat ~sep:", " (List.map names ~f:Symbol.to_string))))
  in
  (match
     Or_error.try_with (fun () ->
         Stop.evaluate ~defer_drawdown ~journal ~strategies ~zone ~at:(Time_ns.now ())
           ~on_event)
   with
  | Ok _ -> ()
  | Error e ->
      on_event
        (sprintf "session %s: the stop rules could not be evaluated: %s" day
           (Error.to_string_hum e)));
  (* The night's sub-ledger check (bin/main.ml passes Oms.close_subledger):
     the strategies' positions summed per name against the account the
     session was recorded from, and every standing break said. *)
  (match Or_error.try_with (fun () -> on_recorded date) with
  | Ok () -> ()
  | Error e ->
      on_event
        (sprintf "session %s: the sub-ledger could not be checked: %s" day
           (Error.to_string_hum e)));
  return ()

let run_forever ~(strategies : Ohcamel.Config.Book.Signals_spec.Strategy.t list)
    ~(read : Venue.Read.t) ~(graph : Graph.t) ~(journal : Journal.t) ~(confidence : float)
    ~(book_is_current : unit -> bool) ~(on_recorded : Date.t -> unit)
    ~(on_event : string -> unit) : unit Deferred.t =
  let symbols = Graph.symbols graph in
  let day = Date.to_string in
  let record_now date returns ~session_bars =
    Or_error.try_with (fun () ->
        record_if_current ~book_is_current ~session_bars ~graph ~journal ~date ~returns
          ~mark_equity:true ~confidence ~recorded_at:(Time_ns.now ()))
  in
  (* The session's closes from the venue's bars, for its marks: a read that fails marks every
     name from its last print, tagged so, and re-marked once the bar is filed. *)
  let session_bars_of date =
    match%map read.Venue.Read.session_bars symbols ~date with
    | Ok bars -> bars
    | Error _ -> Symbol.Map.empty
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
    let%bind plan, silent_names, adjusted =
      match%bind read.Venue.Read.daily_bars symbols ~days:2 with
      | Error e ->
          return (Error (Error.to_string_hum e), Symbol.Set.empty, Symbol.Map.empty)
      | Ok bars ->
          let%map silent = ask_silent ~read ~date ~symbols bars in
          (roll_plan ~date ~symbols ~silent bars, silent, bars)
    in
    let last = attempt >= attempts in
    let%bind session_bars =
      match plan with
      | Error _ when not last -> return Symbol.Map.empty
      | _ -> session_bars_of date
    in
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
          | Ok r ->
              ( r,
                sprintf "%d windows rolled%s" (List.length r)
                  (if Set.is_empty silent_names then ""
                   else
                     sprintf ", at zero for %s: no print in the session"
                       (String.concat ~sep:", "
                          (List.map (Set.to_list silent_names) ~f:Symbol.to_string))) )
          | Error why -> ([], "windows NOT rolled: " ^ why)
        in
        match record_now date returns ~session_bars with
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
            on_event (sub_ledger_skipped date);
            wait_for_close ()
        | Ok ((`Recorded | `Already_recorded) as outcome) ->
            journaled date how outcome ~held:None ~adjusted ~session_bars
        | Error e ->
            on_event
              (sprintf
                 "session %s: the record FAILED (%s); the live graph was not touched, \
                  retrying in 10 min"
                 (day date) (Error.to_string_hum e));
            retry date returns how ~adjusted ~session_bars ~why:(Error.to_string_hum e)
              ~held:None)
  and journaled date how outcome ~held ~adjusted ~session_bars =
    on_event
      (match outcome with
      | `Recorded -> sprintf "session %s recorded, %s" (day date) how
      | `Already_recorded -> sprintf "session %s was already recorded" (day date));
    (* Already recorded too, since the process that recorded it may have stopped
       before evaluating; a stopped strategy is never stopped twice. *)
    let%bind () =
      after_recorded ~strategies ~read ~journal ~date ~adjusted ~session_bars ~on_recorded
        ~on_event
    in
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
  and retry date returns how ~adjusted ~session_bars ~why ~held =
    let%bind () = Clock_ns.after retry_every in
    let again ~held =
      match record_now date returns ~session_bars with
      | Ok ((`Recorded | `Already_recorded) as outcome) ->
          journaled date how outcome ~held ~adjusted ~session_bars
      | Ok `Book_not_current ->
          on_event
            (sprintf "session %s: still not recorded (%s); again in 10 min" (day date)
               not_current);
          retry date returns how ~adjusted ~session_bars ~why:not_current ~held
      | Error e ->
          on_event
            (sprintf
               "session %s: the record FAILED again (%s); the live graph was not \
                touched, retrying in 10 min"
               (day date) (Error.to_string_hum e));
          retry date returns how ~adjusted ~session_bars ~why:(Error.to_string_hum e)
            ~held
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
            on_event (sub_ledger_skipped date);
            close_when_due clock
        | `Retry -> again ~held)
  in
  wait_for_close ()
