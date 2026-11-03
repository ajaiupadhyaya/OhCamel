(* The record's statistics: pure arithmetic over a strategy's sessions, with no
   verdict in it. The charter scores a record with these, so the PSR is pinned to
   the Quant side's own output (ohcamel_quant.backtest.metrics) on one series: two
   implementations of one statistic may not drift apart unnoticed. *)

open Core
module Record = Ohcamel_desk.Record

let approx = Alcotest.float 1e-9
let close = Alcotest.float 1e-6

let session ?(allocated = 1_000.0) ?(cost = 0.0) ?(turnover = 0.0) r =
  {
    Record.date = Date.of_string "2026-10-21";
    allocated;
    pnl = r *. allocated;
    cost;
    turnover;
  }

let test_returns_compound_and_draw_down () =
  let s = Record.summarise (List.map [ 0.01; -0.01; 0.02 ] ~f:session) in
  (* 1.01 x 0.99 x 1.02 - 1 = 0.019898 *)
  Alcotest.check approx "compounded" 0.019898 s.Record.compounded;
  (* the peak 1.01 to the trough 0.9999: 0.0101 / 1.01 *)
  Alcotest.check approx "drawdown" 0.01 s.Record.max_drawdown;
  Alcotest.(check (option (float 1e-9))) "hit rate" (Some (2.0 /. 3.0)) s.Record.hit_rate

let test_a_session_with_nothing_allocated_is_skipped () =
  let s =
    Record.summarise [ session 0.01; session ~allocated:0.0 0.5; session (-0.01) ]
  in
  Alcotest.(check int) "two sessions count" 2 s.Record.sessions;
  Alcotest.check approx "and the empty one earned nothing" (-0.0001) s.Record.compounded

let series = [ 0.01; -0.01; 0.02; 0.0; 0.015; -0.005; 0.03; -0.02; 0.005; 0.01 ]

let test_sharpe_and_psr_agree_with_quant () =
  let s = Record.summarise (List.map series ~f:session) in
  Alcotest.(check (option close))
    "annualised Sharpe" (Some 5.897605365691202) s.Record.sharpe;
  Alcotest.(check (option close))
    "PSR against zero" (Some 0.8553159231380001) s.Record.psr

let test_psr_needs_a_series_with_a_spread () =
  let none = Alcotest.(option (float 0.0)) in
  Alcotest.check none "no spread, no Sharpe" None
    (Record.psr ~returns:[ 0.01; 0.01; 0.01; 0.01; 0.01 ] ~benchmark:0.0);
  Alcotest.check none "too short for its moments" None
    (Record.psr ~returns:[ 0.01; -0.01; 0.02 ] ~benchmark:0.0)

let test_nothing_to_score () =
  let s = Record.summarise [] in
  Alcotest.(check int) "no sessions" 0 s.Record.sessions;
  Alcotest.check approx "flat" 0.0 s.Record.compounded;
  Alcotest.check approx "no drawdown" 0.0 s.Record.max_drawdown;
  Alcotest.(check (option (float 0.0))) "no Sharpe" None s.Record.sharpe

let test_cost_and_turnover () =
  let s =
    Record.summarise
      [ session ~cost:1.0 ~turnover:100.0 0.01; session ~cost:3.0 ~turnover:300.0 0.0 ]
  in
  (* 4 dollars on 400 traded is 100 bps; the mean of 0.1 and 0.3 of allocated *)
  Alcotest.(check (option (float 1e-9))) "cost" (Some 100.0) s.Record.cost_bps;
  Alcotest.(check (option (float 1e-9))) "turnover" (Some 0.2) s.Record.turnover_mean

let suite =
  ( "record",
    [
      Alcotest.test_case "returns compound and draw down" `Quick
        test_returns_compound_and_draw_down;
      Alcotest.test_case "a session with nothing allocated is skipped" `Quick
        test_a_session_with_nothing_allocated_is_skipped;
      Alcotest.test_case "Sharpe and PSR agree with Quant" `Quick
        test_sharpe_and_psr_agree_with_quant;
      Alcotest.test_case "PSR needs a series with a spread" `Quick
        test_psr_needs_a_series_with_a_spread;
      Alcotest.test_case "nothing to score" `Quick test_nothing_to_score;
      Alcotest.test_case "cost and turnover" `Quick test_cost_and_turnover;
    ] )
