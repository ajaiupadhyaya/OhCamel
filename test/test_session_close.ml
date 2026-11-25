(* The session close: the one moment each day the engine's return windows move
   and its forecast is written down.

   What is pinned is ordering and all-or-nothing. The windows roll BEFORE the
   forecast is taken, so the forecast for the next session knows this one. A
   return missing for one name rolls no name, because a window that moved for
   AAPL and not for XOM would pair one day's return with another day's, unless
   the name did not trade, when its return is zero. And a date already recorded
   rolls nothing twice. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Journal = Ohcamel_desk.Journal
module Venue = Ohcamel_desk.Venue
module Close = Ohcamel_desk.Session_close

let date = Date.of_string
let aapl = Symbol.of_string "AAPL"
let xom = Symbol.of_string "XOM"

let bar d close =
  {
    Venue.Bar.date = date d;
    open_ = close;
    high = close;
    low = close;
    close;
    volume = 1_000.0;
  }

let test_the_close_is_recorded_five_minutes_after_the_venue_closes () =
  let close = Time_ns.of_string_with_utc_offset "2026-09-11T20:00:00Z" in
  let clock =
    {
      Venue.Session_clock.now = close;
      is_open = true;
      next_open = close;
      next_close = close;
      next_close_date = date "2026-09-11";
    }
  in
  Alcotest.(check string)
    "20:05Z" "2026-09-11T20:05:00.000000000Z"
    (Ohcamel_desk.Desk_time.rfc3339 (Close.due clock))

(* A failed journal write is retried until the session is recorded or a LATER
   close is due (Task 7's ruling). Every venue clock read after a close already
   names the next session, so a later date on the clock is not the signal; the
   later close's due time is. *)
let decision =
  Alcotest.testable
    (fun ppf d ->
      Format.pp_print_string ppf
        (match d with `Retry -> "retry" | `Give_up -> "give up"))
    Poly.equal

let alpaca_clock body =
  Or_error.ok_exn (Ohcamel_desk.Alpaca_paper.clock_of_json (Yojson.Safe.from_string body))

let utc = Time_ns.of_string_with_utc_offset
let friday = date "2026-09-11"

(* Friday's clock as Alpaca answers it at 16:15 New York, fifteen minutes after
   the close: the market is shut and next_close is already Monday the 14th's. *)
let friday_after_the_close =
  alpaca_clock
    {|{"is_open":false,"next_close":"2026-09-14T16:00:00-04:00","next_open":"2026-09-14T09:30:00-04:00","timestamp":"2026-09-11T16:15:00-04:00"}|}

let test_a_failed_write_seen_fifteen_minutes_after_the_close_is_retried () =
  Alcotest.(check string)
    "the clock already names Monday" "2026-09-14"
    (Date.to_string friday_after_the_close.Venue.Session_clock.next_close_date);
  (* 16:15 at -04:00 is 20:15Z on the 11th. Monday's close is 16:00 at -04:00,
     20:00Z on the 14th, due five minutes later at 20:05Z: nearly three days
     after 20:15Z Friday, so Friday's record is tried again. *)
  Alcotest.check decision "retry" `Retry
    (Close.retry_or_give_up ~now:(utc "2026-09-11T20:15:00Z") ~date:friday
       friday_after_the_close)

let test_a_failed_write_is_given_up_once_the_next_close_is_due () =
  (* Monday's close, 16:00 at -04:00, is 20:00Z on the 14th; plus the five
     minute delay, it is due at 20:05:00Z. *)
  Alcotest.check decision "20:04:59Z Monday, one second before it is due: retry" `Retry
    (Close.retry_or_give_up ~now:(utc "2026-09-14T20:04:59Z") ~date:friday
       friday_after_the_close);
  Alcotest.check decision "20:05:00Z Monday, the moment it is due: give up" `Give_up
    (Close.retry_or_give_up ~now:(utc "2026-09-14T20:05:00Z") ~date:friday
       friday_after_the_close);
  (* The same instant, asked of the venue afresh. Monday has closed, so the
     clock names Tuesday: 16:00 at -04:00 on the 15th is 20:00Z, due 20:05Z,
     a day after 20:05Z Monday. A loop that re-read the clock at every retry
     would therefore never give up, which is why run_forever decides on the
     clock it read after the failure. *)
  let monday_after_its_close =
    alpaca_clock
      {|{"is_open":false,"next_close":"2026-09-15T16:00:00-04:00","next_open":"2026-09-15T09:30:00-04:00","timestamp":"2026-09-14T16:05:00-04:00"}|}
  in
  Alcotest.check decision "a fresh read at 20:05Z Monday names Tuesday: retry" `Retry
    (Close.retry_or_give_up ~now:(utc "2026-09-14T20:05:00Z") ~date:friday
       monday_after_its_close)

let test_a_clock_that_still_names_the_session_is_retried () =
  (* Read at 15:55 at -04:00, before Friday's close: next_close is the 11th's
     own 16:00, 20:00Z, due 20:05Z. At 20:15Z that time has passed, but the
     clock names no later session, so there is nothing to give Friday up for. *)
  let friday_before_the_close =
    alpaca_clock
      {|{"is_open":true,"next_close":"2026-09-11T16:00:00-04:00","next_open":"2026-09-14T09:30:00-04:00","timestamp":"2026-09-11T15:55:00-04:00"}|}
  in
  Alcotest.check decision "retry" `Retry
    (Close.retry_or_give_up ~now:(utc "2026-09-11T20:15:00Z") ~date:friday
       friday_before_the_close)

let test_a_session's_return_needs_the_session's_own_bar () =
  (* 102 / 100 - 1 = 0.02 *)
  Alcotest.(check (option (float 1e-12)))
    "the 11th over the 10th" (Some 0.02)
    (Close.session_return ~date:(date "2026-09-11")
       [ bar "2026-09-10" 100.0; bar "2026-09-11" 102.0 ]);
  Alcotest.(check (option (float 0.0)))
    "no bar for the 12th yet, so no return for it" None
    (Close.session_return ~date:(date "2026-09-12")
       [ bar "2026-09-10" 100.0; bar "2026-09-11" 102.0 ]);
  Alcotest.(check (option (float 0.0)))
    "a zero close is not a price" None
    (Close.session_return ~date:(date "2026-09-11")
       [ bar "2026-09-10" 0.0; bar "2026-09-11" 102.0 ])

let test_a_missing_name_rolls_no_name () =
  let bars =
    Symbol.Map.of_alist_exn
      [
        (aapl, [ bar "2026-09-10" 100.0; bar "2026-09-11" 102.0 ]);
        (xom, [ bar "2026-09-10" 50.0; bar "2026-09-11" 49.0 ]);
      ]
  in
  (match
     Close.roll_plan ~date:(date "2026-09-11") ~symbols:[ aapl; xom ]
       ~silent:Symbol.Set.empty bars
   with
  | Ok returns ->
      (* 102/100 - 1 = 0.02; 49/50 - 1 = -0.02 *)
      Alcotest.(check (list (pair string (float 1e-12))))
        "both"
        [ ("AAPL", 0.02); ("XOM", -0.02) ]
        (List.map returns ~f:(fun (s, r) -> (Symbol.to_string s, r)))
  | Error why -> Alcotest.failf "a complete set was refused: %s" why);
  let partial = Map.set bars ~key:xom ~data:[ bar "2026-09-10" 50.0 ] in
  match
    Close.roll_plan ~date:(date "2026-09-11") ~symbols:[ aapl; xom ]
      ~silent:Symbol.Set.empty partial
  with
  | Ok _ -> Alcotest.fail "AAPL rolled while XOM had no bar for the session"
  | Error why ->
      Alcotest.(check bool)
        "the error names XOM" true
        (String.is_substring why ~substring:"XOM")

(* SIZE printed nothing on IEX on six of the ten sessions to 2026-10-30, and each
   time held 47 other names' windows back. A session with no print has no bar to
   wait for, and the venue files it later at yesterday's close: a return of zero. *)
let test_a_name_that_printed_nothing_rolls_at_zero () =
  let bars =
    Symbol.Map.of_alist_exn
      [
        (aapl, [ bar "2026-09-10" 100.0; bar "2026-09-11" 102.0 ]);
        (xom, [ bar "2026-09-09" 50.0; bar "2026-09-10" 49.0 ]);
      ]
  in
  match
    Close.roll_plan ~date:(date "2026-09-11") ~symbols:[ aapl; xom ]
      ~silent:(Symbol.Set.singleton xom) bars
  with
  | Ok returns ->
      Alcotest.(check (list (pair string (float 1e-12))))
        "AAPL's own return, and nothing for XOM"
        [ ("AAPL", 0.02); ("XOM", 0.0) ]
        (List.map returns ~f:(fun (s, r) -> (Symbol.to_string s, r)))
  | Error why -> Alcotest.failf "a silent name held the roll back: %s" why

(* A name is silent only on a print dated before the session, by New York's date.
   Late bars are the other reason a bar is missing, and in that case the name
   printed today: it stays out, and the close waits as it always did. *)
let test_a_name_is_silent_only_on_a_print_before_the_session () =
  let zone = Time_float.Zone.of_utc_offset ~hours:(-4) in
  let at = Time_ns.of_string_with_utc_offset in
  let silent =
    Close.silent ~date:(date "2026-10-30") ~zone
      ~prints:
        (Symbol.Map.of_alist_exn
           [
             (Symbol.of_string "SIZE", at "2026-10-26T17:25:13Z");
             (Symbol.of_string "EVENING", at "2026-10-29T23:50:00Z");
             (Symbol.of_string "TODAY", at "2026-10-30T19:59:55Z");
             (Symbol.of_string "AFTER", at "2026-10-31T00:30:00Z");
           ])
  in
  Alcotest.(check (list string))
    "an old print and last evening's, not today's nor tonight's, and a name with no \
     print on record is not one"
    [ "EVENING"; "SIZE" ]
    (List.map (Set.to_list silent) ~f:Symbol.to_string)

let returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]

(* AAPL 10 at 100 = +1,000; XOM -10 at 50 = -500; cash 10,000.
   gross 1,500, net 500, equity 10,500. *)
let with_book ~f =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 10_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Graph.set_price graph aapl (Price.of_float 100.0);
  Graph.set_price graph xom (Price.of_float 50.0);
  Graph.set_qty graph aapl (Qty.of_float 10.0);
  Graph.set_qty graph xom (Qty.of_float (-10.0));
  Graph.set_returns graph aapl returns;
  Graph.set_returns graph xom (Array.map returns ~f:Float.neg);
  Graph.stabilize graph;
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  Exn.protect
    ~f:(fun () -> f graph journal)
    ~finally:(fun () ->
      Journal.close journal;
      Graph.destroy graph)

let at = Time_ns.of_string_with_utc_offset "2026-09-11T20:05:00Z"

let record ?(session_bars = Symbol.Map.empty) graph journal =
  Close.record ~session_bars ~graph ~journal ~date:(date "2026-09-11")
    ~returns:[ (aapl, 0.01); (xom, -0.01) ]
    ~mark_equity:true ~confidence:0.95 ~recorded_at:at

let test_the_close_rolls_then_marks_then_records () =
  with_book ~f:(fun graph journal ->
      Alcotest.(check bool) "recorded" true (Poly.equal (record graph journal) `Recorded);
      let window = Graph.returns graph aapl in
      Alcotest.(check int) "the window keeps its length" 10 (Array.length window);
      Alcotest.(check (float 0.0)) "and ends with the session's return" 0.01 window.(9);
      let s = Option.value_exn (Journal.session journal (date "2026-09-11")) in
      Alcotest.(check (float 1e-9))
        "equity 10,000 + 1,000 - 500" 10_500.0 s.Journal.Session.equity_close;
      Alcotest.(check (float 1e-9))
        "gross 1,000 + 500" 1_500.0 s.Journal.Session.gross_close;
      Alcotest.(check (float 1e-9)) "net 1,000 - 500" 500.0 s.Journal.Session.net_close;
      Alcotest.(check (float 1e-9)) "cash" 10_000.0 s.Journal.Session.cash_close;
      Alcotest.(check (list string))
        "marks, symbol order"
        [ "AAPL 100 10"; "XOM 50 -10" ]
        (List.map
           (Journal.marks journal (date "2026-09-11"))
           ~f:(fun m ->
             sprintf "%s %g %g"
               (Symbol.to_string m.Journal.Mark.symbol)
               m.Journal.Mark.close m.Journal.Mark.qty));
      let fs = Journal.forecasts journal in
      Alcotest.(check (list string))
        "three estimators"
        [ "ewma"; "historical"; "parametric" ]
        (List.map fs ~f:(fun f -> f.Journal.Forecast.estimator));
      (* The graph's rule for dollars: the fraction times gross (1,500). *)
      List.iter fs ~f:(fun f ->
          match (f.Journal.Forecast.var_fraction, f.Journal.Forecast.var_notional) with
          | Some frac, Some dollars ->
              Alcotest.(check (float 1e-9))
                (f.Journal.Forecast.estimator ^ ": notional = fraction x 1,500")
                (frac *. 1_500.0) dollars
          | None, None -> ()
          | _ ->
              Alcotest.failf "%s: a fraction without its notional, or the reverse"
                f.Journal.Forecast.estimator);
      let history = Graph.equity_history graph in
      Alcotest.(check (float 1e-9))
        "the equity trail gained the close" 10_500.0
        history.(Array.length history - 1))

let test_a_recorded_date_rolls_nothing_twice () =
  with_book ~f:(fun graph journal ->
      ignore (record graph journal : [ `Recorded | `Already_recorded ]);
      let window = Graph.returns graph aapl and version = Journal.version journal in
      Alcotest.(check bool)
        "already recorded" true
        (Poly.equal (record graph journal) `Already_recorded);
      Alcotest.(check (array (float 0.0)))
        "the window did not roll again" window (Graph.returns graph aapl);
      Alcotest.(check int) "nothing was written" version (Journal.version journal))

(* The defect a review caught in this file's own first version: [record]
   pushed returns and marked equity on the LIVE graph before writing the
   journal. A write that then raised -- here, a trigger standing in for a
   disk error or a locked file -- left the graph a day ahead of a journal that
   still had no session row for that date, so [Journal.session] still
   answered [None] and the retry that followed rolled the same day's return a
   second time on top of the first. [record] now computes on a fork and
   applies to the live graph only once every write has committed, so this
   proves two things: a failed write leaves the graph exactly where it found
   it, and a retry after the failure rolls the day exactly once -- checked by
   running the same close on an untouched twin and comparing. *)
let test_a_failed_write_leaves_the_graph_untouched_and_a_retry_rolls_once () =
  with_book ~f:(fun a ja ->
      with_book ~f:(fun b jb ->
          let before = Graph.snapshot a in
          ignore
            (Sqlite3.exec (Journal.For_testing.db ja)
               "CREATE TRIGGER fail_session BEFORE INSERT ON sessions BEGIN SELECT \
                RAISE(ABORT, 'the test refuses this session'); END"
              : Sqlite3.Rc.t);
          (match record a ja with
          | (_ : [ `Recorded | `Already_recorded ]) ->
              Alcotest.fail "the trigger should have made the session write fail"
          | exception Failure _ -> ()
          | exception e -> Alcotest.failf "expected Failure, got %s" (Exn.to_string e));
          let after = Graph.snapshot a in
          Alcotest.(check (float 0.0))
            "equity: untouched by the failed write"
            (Notional.to_float (Graph.Snapshot.equity before))
            (Notional.to_float (Graph.Snapshot.equity after));
          Alcotest.(check (float 0.0))
            "gross: untouched"
            (Notional.to_float (Graph.Snapshot.gross_exposure before))
            (Notional.to_float (Graph.Snapshot.gross_exposure after));
          Alcotest.(check (option (float 0.0)))
            "historical VaR: untouched"
            (Graph.Snapshot.historical_var before)
            (Graph.Snapshot.historical_var after);
          Alcotest.(check (option (float 0.0)))
            "parametric VaR: untouched"
            (Graph.Snapshot.parametric_var before)
            (Graph.Snapshot.parametric_var after);
          Alcotest.(check (option (float 0.0)))
            "EWMA VaR: untouched"
            (Graph.Snapshot.parametric_var_ewma before)
            (Graph.Snapshot.parametric_var_ewma after);
          Alcotest.(check (float 0.0))
            "drawdown: untouched"
            (Graph.Snapshot.current_drawdown before)
            (Graph.Snapshot.current_drawdown after);
          Alcotest.(check (array (float 0.0)))
            "the window did not roll on the failed attempt" (Graph.returns a aapl)
            (Graph.returns b aapl);
          ignore
            (Sqlite3.exec (Journal.For_testing.db ja) "DROP TRIGGER fail_session"
              : Sqlite3.Rc.t);
          Alcotest.(check bool)
            "recorded on retry" true
            (Poly.equal (record a ja) `Recorded);
          Alcotest.(check bool)
            "recorded once on the untouched twin" true
            (Poly.equal (record b jb) `Recorded);
          let sa = Graph.snapshot a and sb = Graph.snapshot b in
          Alcotest.(check (array (float 0.0)))
            "A's window rolled exactly once, matching B's single roll"
            (Graph.returns b aapl) (Graph.returns a aapl);
          Alcotest.(check (float 1e-9))
            "A's equity == B's"
            (Notional.to_float (Graph.Snapshot.equity sb))
            (Notional.to_float (Graph.Snapshot.equity sa));
          Alcotest.(check (option (float 1e-9)))
            "A's historical VaR == B's, not a window rolled twice"
            (Graph.Snapshot.historical_var sb)
            (Graph.Snapshot.historical_var sa);
          Alcotest.(check (option (float 1e-9)))
            "A's parametric VaR == B's"
            (Graph.Snapshot.parametric_var sb)
            (Graph.Snapshot.parametric_var sa)))

let test_drawdown_survives_a_restart () =
  with_book ~f:(fun graph journal ->
      List.iter
        [
          ("2026-09-09", 100_000.0); ("2026-09-10", 104_000.0); ("2026-09-11", 102_000.0);
        ]
        ~f:(fun (d, e) ->
          Journal.record_session journal
            {
              Journal.Session.date = date d;
              equity_close = e;
              cash_close = e;
              gross_close = 0.0;
              net_close = 0.0;
              recorded_at = at;
            });
      (* A flat book with 102,000 of cash: equity 102,000. *)
      Graph.set_qty graph aapl Qty.zero;
      Graph.set_qty graph xom Qty.zero;
      Graph.set_cash graph (Notional.of_float 102_000.0);
      Alcotest.(check int) "three closes restored" 3 (Close.restore ~graph ~journal);
      Alcotest.(check (array (float 0.0)))
        "the trail"
        [| 100_000.0; 104_000.0; 102_000.0 |]
        (Graph.equity_history graph);
      (* Risk_metrics.current_drawdown: (peak - latest) / peak over the trail
         with live equity appended: (104,000 - 102,000) / 104,000 = 1/52. *)
      Alcotest.(check (float 1e-12))
        "measured from the restored peak" (2_000.0 /. 104_000.0)
        (Graph.current_drawdown graph))

(* The official close (Task 1.8): the venue's daily bar for the session, and the last
   print only when the bar is not filed -- tagged so, to be re-marked. A bar for
   another date is not this session's. On a half-day the bar is the day's close
   whenever it came, so the rule is the same. *)
let test_the_official_close_prefers_the_bar () =
  let bars =
    Symbol.Map.of_alist_exn
      [ (aapl, bar "2026-09-11" 101.0); (xom, bar "2026-09-10" 49.0) ]
  in
  let last_print s =
    if Symbol.equal s aapl then Some 100.5
    else if Symbol.equal s xom then Some 50.0
    else None
  in
  let official s = Close.official_close bars ~last_print ~date:(date "2026-09-11") s in
  let show = function
    | None -> "none"
    | Some (p, `Bar) -> sprintf "%g bar" p
    | Some (p, `Last_print) -> sprintf "%g last_print" p
  in
  Alcotest.(check string) "the bar" "101 bar" (show (official aapl));
  Alcotest.(check string)
    "yesterday's bar is not today's" "50 last_print"
    (show (official xom));
  Alcotest.(check string)
    "nothing at all" "none"
    (show (official (Symbol.of_string "SPY")))

(* The close marks AAPL from its bar, 101, and XOM, with no bar filed, from the
   graph's 50 -- each tagged with where it came from. *)
let test_the_close_marks_at_the_official_close () =
  with_book ~f:(fun graph journal ->
      ignore
        (record
           ~session_bars:(Symbol.Map.of_alist_exn [ (aapl, bar "2026-09-11" 101.0) ])
           graph journal
          : [ `Recorded | `Already_recorded ]);
      Alcotest.(check (list string))
        "marks and their origins"
        [ "AAPL 101 bar"; "XOM 50 last_print" ]
        (List.map
           (Journal.marks journal (date "2026-09-11"))
           ~f:(fun m ->
             sprintf "%s %g %s"
               (Symbol.to_string m.Journal.Mark.symbol)
               m.Journal.Mark.close
               (match
                  Journal.mark_source journal (date "2026-09-11") m.Journal.Mark.symbol
                with
               | Some `Bar -> "bar"
               | Some `Last_print -> "last_print"
               | None -> "unknown"))))

(* The night after, XOM's bar is filed: its last-print mark is re-marked to the bar's
   49.80, and AAPL's bar mark is left alone. *)
let test_a_last_print_is_re_marked_when_the_bar_is_filed () =
  with_book ~f:(fun graph journal ->
      ignore
        (record
           ~session_bars:(Symbol.Map.of_alist_exn [ (aapl, bar "2026-09-11" 101.0) ])
           graph journal
          : [ `Recorded | `Already_recorded ]);
      Alcotest.(check int)
        "a bar for another day re-marks nothing" 0
        (Close.remark_from ~journal ~date:(date "2026-09-11")
           (Symbol.Map.of_alist_exn [ (xom, bar "2026-09-12" 49.8) ]));
      Alcotest.(check int)
        "one re-marked" 1
        (Close.remark_from ~journal ~date:(date "2026-09-11")
           (Symbol.Map.of_alist_exn
              [ (aapl, bar "2026-09-11" 1.0); (xom, bar "2026-09-11" 49.8) ]));
      Alcotest.(check (list (float 1e-9)))
        "XOM from its bar, AAPL untouched" [ 101.0; 49.8 ]
        (List.map
           (Journal.marks journal (date "2026-09-11"))
           ~f:(fun m -> m.Journal.Mark.close)))

let activity ?symbol ?per_share ?(status = Some "executed") ~id ~kind ~on net =
  {
    Venue.Activity.id;
    activity_type = kind;
    sub_type = None;
    date = date on;
    symbol;
    qty = None;
    per_share;
    net_amount = net;
    status;
    order_id = None;
  }

let tlt = Symbol.of_string "TLT"
let lqd = Symbol.of_string "LQD"

let cash ~id ~symbol ~ex ~pay rate =
  {
    Venue.Cash_dividend.id;
    symbol;
    ex_date = date ex;
    payable_date = Some (date pay);
    rate;
  }

(* The nightly pull, decided without a venue. Every activity is recorded; a cash
   dividend only once it has gone ex (LQD's ex-date is after the session); the pull is
   marked through the session. An LQD dividend paid with no record is said as credited
   to no strategy, and an account fee as assigned to none. The same pull again records
   nothing new. A pull that fails leaves the mark where it was, so the next night asks
   from there. *)
let pulled = function `Pulled -> "pulled" | `Not_pulled -> "not pulled"
let page items = { Venue.Page.items; skipped = [] }

let test_the_nightly_pull_records_what_has_gone_ex () =
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let on = date "2026-11-04" in
  (* LQD's posted on 2026-11-04 with no record; HYG's posted 2026-11-09, two days after
     its record's payable date 2026-11-07, which is within the three-day window. *)
  let hyg = Symbol.of_string "HYG" in
  let activities =
    Ok
      (page
         [
           activity ~id:"a1" ~kind:"DIV" ~on:"2026-11-04" ~symbol:lqd ~per_share:0.25 25.0;
           activity ~id:"a2" ~kind:"FEE" ~on:"2026-11-03" (-0.05);
           activity ~id:"a3" ~kind:"DIV" ~on:"2026-11-09" ~symbol:hyg ~per_share:0.4 40.0;
         ])
  in
  let dividends =
    Ok
      (page
         [
           cash ~id:"c1" ~symbol:tlt ~ex:"2026-11-03" ~pay:"2026-11-06" 0.30;
           cash ~id:"c2" ~symbol:lqd ~ex:"2026-11-05" ~pay:"2026-11-07" 0.27;
           cash ~id:"c3" ~symbol:hyg ~ex:"2026-11-03" ~pay:"2026-11-07" 0.40;
         ])
  in
  let outcome, lines =
    Close.apply_cash_pull ~journal ~date:on ~recorded_at:at ~activities ~dividends
  in
  Alcotest.(check string) "pulled" "pulled" (pulled outcome);
  Alcotest.(check (list string))
    "what the close says"
    [
      "session 2026-11-04: dividends and fees pulled: 3 new activities, 2 cash dividends \
       gone ex on record";
      "session 2026-11-04: LQD paid 25.00 on 2026-11-04 with no ex-date on record: \
       credited to no strategy";
      "session 2026-11-04: 1 account fee totalling 0.05 names no order: assigned to no \
       strategy";
    ]
    lines;
  Alcotest.(check (list string))
    "only TLT's and HYG's have gone ex" [ "c1"; "c3" ]
    (List.map (Journal.cash_dividends journal) ~f:(fun c -> c.Venue.Cash_dividend.id));
  Alcotest.(check (option string))
    "pulled through the session" (Some "2026-11-04")
    (Option.map (Journal.cash_pulled_through journal) ~f:Date.to_string);
  let _, again =
    Close.apply_cash_pull ~journal ~date:on ~recorded_at:at ~activities ~dividends
  in
  Alcotest.(check int) "again: nothing twice" 3 (List.length (Journal.activities journal));
  Alcotest.(check bool)
    "and the unmatched dividend is not said a second night" false
    (List.exists again ~f:(String.is_substring ~substring:"credited to no strategy"));
  let failed_outcome, failed =
    Close.apply_cash_pull ~journal ~date:(date "2026-11-05") ~recorded_at:at ~activities
      ~dividends:(Or_error.error_string "503")
  in
  Alcotest.(check string) "a failed pull" "not pulled" (pulled failed_outcome);
  Alcotest.(check bool)
    "is said" true
    (List.exists failed ~f:(String.is_substring ~substring:"not pulled"));
  Alcotest.(check (option string))
    "and the pull is not marked past it" (Some "2026-11-04")
    (Option.map (Journal.cash_pulled_through journal) ~f:Date.to_string);
  (* Two records with different ids for TLT's one ex-date: both kept, and said. *)
  let _, twice =
    Close.apply_cash_pull ~journal ~date:(date "2026-11-06") ~recorded_at:at
      ~activities:(Ok (page []))
      ~dividends:
        (Ok
           (page
              [ cash ~id:"c1-bis" ~symbol:tlt ~ex:"2026-11-03" ~pay:"2026-11-06" 0.30 ]))
  in
  Alcotest.(check bool)
    "a second record for one ex-date is named" true
    (List.exists twice ~f:(fun l ->
         String.is_substring l
           ~substring:"TLT has 2 dividend records for ex-date 2026-11-03"
         && String.is_substring l ~substring:"c1"
         && String.is_substring l ~substring:"c1-bis"));
  Alcotest.(check int)
    "and not deduplicated" 3
    (List.length (Journal.cash_dividends journal));
  Journal.close journal;
  (* Fix round 3: on a night the dividends read failed, a paid dividend is not
     reported as unmatched for want of records that were never read. *)
  let fresh = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let _, lines =
    Close.apply_cash_pull ~journal:fresh ~date:on ~recorded_at:at
      ~activities:
        (Ok (page [ activity ~id:"x1" ~kind:"DIV" ~on:"2026-11-04" ~symbol:lqd 25.0 ]))
      ~dividends:(Or_error.error_string "403")
  in
  Alcotest.(check bool)
    "no unmatched report without the records" false
    (List.exists lines ~f:(String.is_substring ~substring:"credited to no strategy"));
  let outcome, lines =
    Close.apply_cash_pull ~journal:fresh ~date:on ~recorded_at:at
      ~activities:(Ok { Venue.Page.items = []; skipped = [ "bad::2: no date" ] })
      ~dividends:(Ok (page []))
  in
  Alcotest.(check string) "a page with a skipped item is pulled" "pulled" (pulled outcome);
  Alcotest.(check bool)
    "and the skip is said" true
    (List.mem lines
       "session 2026-11-04: 1 activities skipped as unreadable (first: bad::2: no date); \
        the rest recorded"
       ~equal:String.equal);
  Journal.close fresh

(* An unrecorded distribution (fix round 1, I1): the venue's adjusted series moves
   the day before when a distribution or split is processed, the raw one does not.
   TLT: adjusted 89.70 -> 89.70, raw 90.00 -> 89.70: the ratios differ by 0.33%, and
   with no record for 2026-11-03 it is flagged; with one, it is not. SPY: both series
   600 -> 606: the same ratio, not flagged. A name missing either bar is not judged. *)
let test_an_unrecorded_distribution_is_found_in_the_bars () =
  let spy = Symbol.of_string "SPY" in
  let adjusted =
    Symbol.Map.of_alist_exn
      [
        (tlt, [ bar "2026-11-02" 89.7; bar "2026-11-03" 89.7 ]);
        (spy, [ bar "2026-11-02" 600.0; bar "2026-11-03" 606.0 ]);
        (lqd, [ bar "2026-11-02" 110.0; bar "2026-11-03" 110.0 ]);
      ]
  in
  let raw_today =
    Symbol.Map.of_alist_exn
      [ (tlt, bar "2026-11-03" 89.7); (spy, bar "2026-11-03" 606.0) ]
  in
  let raw_before =
    Symbol.Map.of_alist_exn
      [ (tlt, bar "2026-11-02" 90.0); (spy, bar "2026-11-02" 600.0) ]
  in
  let found recorded =
    Close.unrecorded_moves ~date:(date "2026-11-03") ~adjusted ~raw_today ~raw_before
      ~recorded
    |> List.map ~f:Symbol.to_string
  in
  Alcotest.(check (list string)) "TLT" [ "TLT" ] (found (fun _ -> false));
  Alcotest.(check (list string))
    "not once recorded" []
    (found (fun s -> Symbol.equal s tlt))

(* ------------------------------------------------------------------------ *)
(* Task 1.10: the forecasts scored against the positions they were made on   *)
(* ------------------------------------------------------------------------ *)

module Score = Ohcamel_desk.Var_score

let mark d symbol close qty = { Journal.Mark.date = date d; symbol; close; qty }
let marks_of table d = Option.value (Map.find table d) ~default:[]
let table rows = Date.Map.of_alist_exn (List.map rows ~f:(fun (d, ms) -> (date d, ms)))

(* AAPL long 10 and XOM short 5, from one close to the next:
     10 * (98 - 100) + (-5) * (52 - 50) = -30
   against a gross of 10 * 100 + 5 * 50 = 1250, which is -2.4% of gross. The
   forecast's fraction is of gross (Session_close.forecasts), so the realised is
   in the same unit, and it is the positions of the FIRST close that are held:
   a trade made during the second session changes nothing here. *)
let test_the_hypothetical_return_is_the_first_closes_positions () =
  let marks =
    table
      [
        ( "2026-09-10",
          [ mark "2026-09-10" aapl 100.0 10.0; mark "2026-09-10" xom 50.0 (-5.0) ] );
        ( "2026-09-11",
          [ mark "2026-09-11" aapl 98.0 99.0; mark "2026-09-11" xom 52.0 7.0 ] );
      ]
  in
  let r =
    Score.hypothetical_returns
      ~sessions:[ date "2026-09-10"; date "2026-09-11" ]
      ~marks:(marks_of marks) ~dividends:[]
  in
  Alcotest.(check (list (pair string (float 1e-12))))
    "keyed by the date the forecast was made"
    [ ("2026-09-10", -0.024) ]
    (List.map r.Score.returns ~f:(fun (d, x) -> (Date.to_string d, x)));
  Alcotest.(check int) "nothing unscorable" 0 r.Score.unscorable

let test_a_pair_that_cannot_be_priced_is_counted_not_guessed () =
  let held d p = [ mark d aapl p 10.0; mark d xom 50.0 5.0 ] in
  let marks =
    table
      [
        ("2026-09-08", held "2026-09-08" 100.0);
        (* XOM has no mark on the 9th: the held name cannot be repriced. *)
        ("2026-09-09", [ mark "2026-09-09" aapl 101.0 10.0 ]);
        ("2026-09-10", held "2026-09-10" 100.0);
        (* A flat book has no exposure to be wrong about. *)
        ( "2026-09-11",
          [ mark "2026-09-11" aapl 100.0 0.0; mark "2026-09-11" xom 50.0 0.0 ] );
        ("2026-09-14", held "2026-09-14" 100.0);
        (* A week with no close recorded between two sessions is not one day. *)
        ("2026-09-25", held "2026-09-25" 100.0);
      ]
  in
  let r =
    Score.hypothetical_returns
      ~sessions:
        (List.map
           [
             "2026-09-08";
             "2026-09-09";
             "2026-09-10";
             "2026-09-11";
             "2026-09-14";
             "2026-09-25";
           ]
           ~f:date)
      ~marks:(marks_of marks) ~dividends:[]
  in
  Alcotest.(check (list (pair string (float 1e-12))))
    "09-09 holds only AAPL (XOM has no mark that day) and 09-10 is flat"
    [ ("2026-09-09", -10.0 /. 1010.0); ("2026-09-10", 0.0) ]
    (List.map r.Score.returns ~f:(fun (d, x) -> (Date.to_string d, x)));
  Alcotest.(check int)
    "08->09 (XOM held, no mark), 11->14 (flat book), 14->25 (a gap)" 3 r.Score.unscorable

(* A cash dividend gone ex between the closes is not a loss: the raw close
   falls by it. AAPL long 10 at 100 falls to 98 on its ex-date with a 2.00
   dividend, so the hypothetical return is 10 * (98 - 100) + 10 * 2 = 0; a
   short owes it, so XOM short 5 at 50 that falls to 48 with a 1.00 dividend
   gains 5 * 2 and owes 5 * 1. A dividend gone ex before the first close or
   after the second is not this pair's. *)
let test_a_dividend_gone_ex_is_added_back () =
  let marks =
    table
      [
        ("2026-09-10", [ mark "2026-09-10" aapl 100.0 10.0 ]);
        ("2026-09-11", [ mark "2026-09-11" aapl 98.0 10.0 ]);
        ("2026-09-14", [ mark "2026-09-14" xom 50.0 (-5.0) ]);
        ("2026-09-15", [ mark "2026-09-15" xom 48.0 (-5.0) ]);
      ]
  in
  let dividend id symbol ex rate =
    { Venue.Cash_dividend.id; symbol; ex_date = date ex; payable_date = None; rate }
  in
  let r =
    Score.hypothetical_returns
      ~sessions:(List.map [ "2026-09-10"; "2026-09-11" ] ~f:date)
      ~marks:(marks_of marks)
      ~dividends:
        [
          dividend "a" aapl "2026-09-11" 2.0;
          dividend "early" aapl "2026-09-10" 5.0;
          dividend "late" aapl "2026-09-12" 5.0;
        ]
  in
  Alcotest.(check (list (pair string (float 1e-12))))
    "the dividend offsets the fall"
    [ ("2026-09-10", 0.0) ]
    (List.map r.Score.returns ~f:(fun (d, x) -> (Date.to_string d, x)));
  let short =
    Score.hypothetical_returns
      ~sessions:(List.map [ "2026-09-14"; "2026-09-15" ] ~f:date)
      ~marks:(marks_of marks)
      ~dividends:[ dividend "x" xom "2026-09-15" 1.0 ]
  in
  (* 5 * 2 (the fall) - 5 * 1 (owed) = 5, over gross 250. *)
  Alcotest.(check (list (pair string (float 1e-12))))
    "a short owes it"
    [ ("2026-09-14", 5.0 /. 250.0) ]
    (List.map short.Score.returns ~f:(fun (d, x) -> (Date.to_string d, x)))

(* A close missed mid-week is two weekdays, not one: Wed to Fri is not a
   1-day move. Fri to Mon is one. *)
let test_a_missed_midweek_close_is_not_one_day () =
  let held d p = [ mark d aapl p 10.0 ] in
  let marks =
    table
      [
        ("2026-09-09", held "2026-09-09" 100.0);
        ("2026-09-11", held "2026-09-11" 90.0);
        ("2026-09-14", held "2026-09-14" 91.0);
      ]
  in
  let r =
    Score.hypothetical_returns
      ~sessions:(List.map [ "2026-09-09"; "2026-09-11"; "2026-09-14" ] ~f:date)
      ~marks:(marks_of marks) ~dividends:[]
  in
  Alcotest.(check (list string))
    "only Friday to Monday is scored" [ "2026-09-11" ]
    (List.map r.Score.returns ~f:(fun (d, _) -> Date.to_string d));
  Alcotest.(check int) "Wednesday to Friday is counted" 1 r.Score.unscorable

(* A journal with [n] consecutive sessions from 2026-03-02 (every calendar day:
   the score reads dates, not a calendar), holding AAPL long 10 at 100 that
   moves +0.5% or, on [loss_days], -5%. Forecasts of 2% at 99% are written for
   each, on [windows] i. *)
let scored_journal ~n ~loss_days ~window_of ~(estimators : string list) =
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let start = date "2026-03-02" in
  let close = ref 100.0 in
  List.iter (List.init n ~f:Fn.id) ~f:(fun i ->
      let d = Date.add_days start i in
      if i > 0 then
        close := !close *. if List.mem loss_days i ~equal:Int.equal then 0.95 else 1.005;
      Journal.record_marks journal
        [ { Journal.Mark.date = d; symbol = aapl; close = !close; qty = 10.0 } ];
      Journal.record_session journal
        {
          Journal.Session.date = d;
          equity_close = 100_000.0;
          cash_close = 99_000.0;
          gross_close = 1_000.0;
          net_close = 1_000.0;
          recorded_at = at;
        };
      Journal.record_forecasts journal
        (List.map estimators ~f:(fun estimator ->
             {
               Journal.Forecast.date = d;
               estimator;
               confidence = 0.99;
               var_fraction = Some 0.02;
               var_notional = Some 20.0;
               es_notional = None;
               return_window = window_of i;
             })));
  journal

let field_of j k = Yojson.Safe.Util.member k j
let scores_of j = Yojson.Safe.Util.to_list (field_of j "scores")

let test_a_score_is_published_as_counts_p_values_and_a_light () =
  let journal =
    scored_journal ~n:131 ~loss_days:[ 20; 100 ]
      ~window_of:(fun _ -> Some 250)
      ~estimators:[ "historical"; "ewma" ]
  in
  let j = Score.to_json (Score.of_journal journal) in
  Alcotest.(check int)
    "the window it is for" 250
    (Yojson.Safe.Util.to_int (field_of j "window"));
  Alcotest.(check string) "the level" "0.99" (Yojson.Safe.to_string (field_of j "level"));
  Alcotest.(check string)
    "as of the last session scored" "2026-07-10"
    (Yojson.Safe.Util.to_string (field_of j "as_of"));
  (match scores_of j with
  | [ a; b ] ->
      Alcotest.(check (list string))
        "by estimator" [ "ewma"; "historical" ]
        (List.map [ a; b ] ~f:(fun s ->
             Yojson.Safe.Util.to_string (field_of s "estimator")));
      (* 131 sessions make 130 forecasts with an outcome, and the last
         session's forecast has none yet. Two days lost 5% against a 2% VaR. *)
      Alcotest.(check int) "n" 130 (Yojson.Safe.Util.to_int (field_of a "n"));
      Alcotest.(check int)
        "exceptions" 2
        (Yojson.Safe.Util.to_int (field_of a "exceptions"));
      Alcotest.(check string)
        "green" "green"
        (Yojson.Safe.Util.to_string (field_of a "light"));
      Alcotest.(check bool)
        "a p-value" true
        (Float.( > ) (Yojson.Safe.Util.to_number (field_of a "kupiec_p")) 0.05)
  | l -> Alcotest.failf "two estimators expected, got %d" (List.length l));
  (* Nothing in what is published names a size, a loss or a limit. *)
  let rec keys = function
    | `Assoc kv -> List.concat_map kv ~f:(fun (k, v) -> k :: keys v)
    | `List l -> List.concat_map l ~f:keys
    | _ -> []
  in
  List.iter (keys j) ~f:(fun k ->
      List.iter [ "var"; "pnl"; "dollar"; "notional"; "es_" ] ~f:(fun bad ->
          if String.is_substring k ~substring:bad then
            Alcotest.failf "the published score has a field named %s" k));
  Journal.close journal

let test_fewer_than_sixty_sessions_says_n_of_60 () =
  let journal =
    scored_journal ~n:13 ~loss_days:[]
      ~window_of:(fun _ -> Some 250)
      ~estimators:[ "historical" ]
  in
  let j = Score.to_json (Score.of_journal journal) in
  (match scores_of j with
  | [ s ] ->
      Alcotest.(check bool) "no light" true (Poly.equal (field_of s "light") `Null);
      Alcotest.(check bool) "no p-value" true (Poly.equal (field_of s "kupiec_p") `Null);
      Alcotest.(check int)
        "12 outcomes so far" 12
        (Yojson.Safe.Util.to_int (field_of s "n"));
      Alcotest.(check string)
        "the message" "12 of 60 sessions scored"
        (Yojson.Safe.Util.to_string (field_of s "message"))
  | _ -> Alcotest.fail "one estimator expected");
  Journal.close journal

(* Task 1.9 moved the return window from 60 to 250 and journals it. A series
   is never mixed across the move: only the window of the newest forecast is
   scored, a row journaled before the window was recorded counts as 60, and
   the sessions under another window are reported as a count. *)
let test_a_series_is_never_mixed_across_the_window_change () =
  (* 70 sessions with no recorded window (60), then 80 at 250. The 60-window
     sessions would score on their own (69 outcomes, 6 of them losses = a
     failing series); mixed in with the 250s they would change the answer. *)
  let journal =
    scored_journal ~n:150 ~loss_days:[ 5; 10; 15; 20; 25; 30; 35 ]
      ~window_of:(fun i -> if i < 70 then None else Some 250)
      ~estimators:[ "historical" ]
  in
  let j = Score.to_json (Score.of_journal journal) in
  Alcotest.(check int)
    "the current window" 250
    (Yojson.Safe.Util.to_int (field_of j "window"));
  Alcotest.(check int)
    "the 60-window sessions are counted apart" 70
    (Yojson.Safe.Util.to_int (field_of j "other_window_sessions"));
  (match scores_of j with
  | [ s ] ->
      (* 80 sessions at 250, the last with no outcome yet: 79, no losses. *)
      Alcotest.(check int)
        "only the current window" 79
        (Yojson.Safe.Util.to_int (field_of s "n"));
      Alcotest.(check int)
        "none of the old window's exceptions" 0
        (Yojson.Safe.Util.to_int (field_of s "exceptions"))
  | _ -> Alcotest.fail "one estimator expected");
  Journal.close journal

(* The same series with the window recorded as 60 throughout: a NULL row is a
   60-window row, so it is in the same series as an explicit 60. *)
let test_a_row_without_a_window_is_a_sixty_window_row () =
  let journal =
    scored_journal ~n:100 ~loss_days:[]
      ~window_of:(fun i -> if i < 50 then None else Some 60)
      ~estimators:[ "historical" ]
  in
  let j = Score.to_json (Score.of_journal journal) in
  Alcotest.(check int) "window 60" 60 (Yojson.Safe.Util.to_int (field_of j "window"));
  Alcotest.(check int)
    "nothing set apart" 0
    (Yojson.Safe.Util.to_int (field_of j "other_window_sessions"));
  Journal.close journal

(* Bounded: the demo host records a session every five minutes, so the score
   reads the newest 250 sessions' forecasts and no more. *)
let test_the_score_reads_a_bounded_tail () =
  let journal =
    scored_journal ~n:400 ~loss_days:[ 10 ]
      ~window_of:(fun _ -> Some 250)
      ~estimators:[ "historical" ]
  in
  let j = Score.to_json (Score.of_journal journal) in
  (match scores_of j with
  | [ s ] ->
      Alcotest.(check int)
        "250 outcomes of 400 sessions" 250
        (Yojson.Safe.Util.to_int (field_of s "n"));
      Alcotest.(check int)
        "the old loss is out of the tail" 0
        (Yojson.Safe.Util.to_int (field_of s "exceptions"))
  | _ -> Alcotest.fail "one estimator expected");
  Journal.close journal

let test_an_empty_journal_scores_nothing () =
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let j = Score.to_json (Score.of_journal journal) in
  Alcotest.(check int) "no estimators" 0 (List.length (scores_of j));
  Alcotest.(check bool) "no window" true (Poly.equal (field_of j "window") `Null);
  Journal.close journal

let suite =
  ( "session_close",
    [
      Alcotest.test_case "the official close prefers the bar" `Quick
        test_the_official_close_prefers_the_bar;
      Alcotest.test_case "the close marks at the official close" `Quick
        test_the_close_marks_at_the_official_close;
      Alcotest.test_case "a last print is re-marked when the bar is filed" `Quick
        test_a_last_print_is_re_marked_when_the_bar_is_filed;
      Alcotest.test_case "the nightly pull records what has gone ex" `Quick
        test_the_nightly_pull_records_what_has_gone_ex;
      Alcotest.test_case "an unrecorded distribution is found in the bars" `Quick
        test_an_unrecorded_distribution_is_found_in_the_bars;
      Alcotest.test_case "the close is recorded five minutes after the venue closes"
        `Quick test_the_close_is_recorded_five_minutes_after_the_venue_closes;
      Alcotest.test_case "a failed write seen fifteen minutes after the close is retried"
        `Quick test_a_failed_write_seen_fifteen_minutes_after_the_close_is_retried;
      Alcotest.test_case "a failed write is given up once the next close is due" `Quick
        test_a_failed_write_is_given_up_once_the_next_close_is_due;
      Alcotest.test_case "a clock that still names the session is retried" `Quick
        test_a_clock_that_still_names_the_session_is_retried;
      Alcotest.test_case "a session's return needs the session's own bar" `Quick
        test_a_session's_return_needs_the_session's_own_bar;
      Alcotest.test_case "a missing name rolls no name" `Quick
        test_a_missing_name_rolls_no_name;
      Alcotest.test_case "a name that printed nothing rolls at zero" `Quick
        test_a_name_that_printed_nothing_rolls_at_zero;
      Alcotest.test_case "a name is silent only on a print before the session" `Quick
        test_a_name_is_silent_only_on_a_print_before_the_session;
      Alcotest.test_case "the close rolls, then marks, then records" `Quick
        test_the_close_rolls_then_marks_then_records;
      Alcotest.test_case "a recorded date rolls nothing twice" `Quick
        test_a_recorded_date_rolls_nothing_twice;
      Alcotest.test_case
        "a failed write leaves the graph untouched, and a retry rolls once" `Quick
        test_a_failed_write_leaves_the_graph_untouched_and_a_retry_rolls_once;
      Alcotest.test_case "drawdown survives a restart" `Quick
        test_drawdown_survives_a_restart;
      Alcotest.test_case "SCORE: the hypothetical return is the first close's positions"
        `Quick test_the_hypothetical_return_is_the_first_closes_positions;
      Alcotest.test_case "SCORE: a pair that cannot be priced is counted, not guessed"
        `Quick test_a_pair_that_cannot_be_priced_is_counted_not_guessed;
      Alcotest.test_case "SCORE: a dividend gone ex is added back" `Quick
        test_a_dividend_gone_ex_is_added_back;
      Alcotest.test_case "SCORE: a missed mid-week close is not one day" `Quick
        test_a_missed_midweek_close_is_not_one_day;
      Alcotest.test_case "SCORE: published as counts, p-values and a light" `Quick
        test_a_score_is_published_as_counts_p_values_and_a_light;
      Alcotest.test_case "SCORE: fewer than sixty sessions says n of 60" `Quick
        test_fewer_than_sixty_sessions_says_n_of_60;
      Alcotest.test_case "SCORE: never mixed across the window change" `Quick
        test_a_series_is_never_mixed_across_the_window_change;
      Alcotest.test_case "SCORE: a row without a window is a 60-window row" `Quick
        test_a_row_without_a_window_is_a_sixty_window_row;
      Alcotest.test_case "SCORE: reads a bounded tail" `Quick
        test_the_score_reads_a_bounded_tail;
      Alcotest.test_case "SCORE: an empty journal scores nothing" `Quick
        test_an_empty_journal_scores_nothing;
    ] )
