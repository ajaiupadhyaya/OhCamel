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
      Alcotest.test_case "a session's return needs the session's own bar" `Quick
        test_a_session's_return_needs_the_session's_own_bar;
      Alcotest.test_case "a missing name rolls no name" `Quick
        test_a_missing_name_rolls_no_name;
      Alcotest.test_case "the close rolls, then marks, then records" `Quick
        test_the_close_rolls_then_marks_then_records;
      Alcotest.test_case "a recorded date rolls nothing twice" `Quick
        test_a_recorded_date_rolls_nothing_twice;
      Alcotest.test_case "drawdown survives a restart" `Quick
        test_drawdown_survives_a_restart;
    ] )
