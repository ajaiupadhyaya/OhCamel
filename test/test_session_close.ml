(* The session close: the one moment each day the engine's return windows move
   and its forecast is written down.

   What is pinned is ordering and all-or-nothing. The windows roll BEFORE the
   forecast is taken, so the forecast for the next session knows this one. A
   return missing for one name rolls no name, because a window that moved for
   AAPL and not for XOM would pair one day's return with another day's. And a
   date already recorded rolls nothing twice. *)

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
  (match Close.roll_plan ~date:(date "2026-09-11") ~symbols:[ aapl; xom ] bars with
  | Ok returns ->
      (* 102/100 - 1 = 0.02; 49/50 - 1 = -0.02 *)
      Alcotest.(check (list (pair string (float 1e-12))))
        "both"
        [ ("AAPL", 0.02); ("XOM", -0.02) ]
        (List.map returns ~f:(fun (s, r) -> (Symbol.to_string s, r)))
  | Error why -> Alcotest.failf "a complete set was refused: %s" why);
  let partial = Map.set bars ~key:xom ~data:[ bar "2026-09-10" 50.0 ] in
  match Close.roll_plan ~date:(date "2026-09-11") ~symbols:[ aapl; xom ] partial with
  | Ok _ -> Alcotest.fail "AAPL rolled while XOM had no bar for the session"
  | Error why ->
      Alcotest.(check bool)
        "the error names XOM" true
        (String.is_substring why ~substring:"XOM")

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

let record graph journal =
  Close.record ~graph ~journal ~date:(date "2026-09-11")
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
            (Sqlite3.exec ja.Journal.db
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
          ignore (Sqlite3.exec ja.Journal.db "DROP TRIGGER fail_session" : Sqlite3.Rc.t);
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

let suite =
  ( "session_close",
    [
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
      Alcotest.test_case "the close rolls, then marks, then records" `Quick
        test_the_close_rolls_then_marks_then_records;
      Alcotest.test_case "a recorded date rolls nothing twice" `Quick
        test_a_recorded_date_rolls_nothing_twice;
      Alcotest.test_case
        "a failed write leaves the graph untouched, and a retry rolls once" `Quick
        test_a_failed_write_leaves_the_graph_untouched_and_a_retry_rolls_once;
      Alcotest.test_case "drawdown survives a restart" `Quick
        test_drawdown_survives_a_restart;
    ] )
