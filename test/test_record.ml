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
    dividends = 0.0;
    fees = 0.0;
    cost_measured = cost;
    cost_notional = turnover;
    cost_unknown = 0;
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

let d = Date.of_string
let aapl = Ohcamel.Types.Symbol.of_string "AAPL"

let fill ?(symbol = aapl) ?arrival_mid ?(fee = 0.0) date qty price =
  { Record.date = d date; symbol; qty; price; arrival_mid; fee }

let of_book ?(allocated = fun _ -> 1_000.0) ?(dividends = []) ~closes ~dates fills =
  Record.sessions_of ~dates:(List.map dates ~f:d)
    ~close:(fun date _ ->
      List.Assoc.find closes (Date.to_string date) ~equal:String.equal)
    ~allocated ~dividends fills

let pnls sessions = List.map sessions ~f:(fun s -> s.Record.pnl)

(* Buy 10 at 100, close 102: 1,020 - 1,000 = +20. Close 99: 990 - 1,020 = -30. Sell 4
   at 101, close 100: 600 - 990 + 404 = +14. The three sum to +4, which is the account:
   cash -1,000 + 404 = -596, and 6 held at 100 is +600. *)
let test_a_strategy's_pnl_is_marked_to_each_close () =
  let sessions =
    of_book
      ~dates:[ "2026-10-20"; "2026-10-21"; "2026-10-22" ]
      ~closes:[ ("2026-10-20", 102.0); ("2026-10-21", 99.0); ("2026-10-22", 100.0) ]
      [ fill "2026-10-20" 10.0 100.0; fill "2026-10-22" (-4.0) 101.0 ]
  in
  Alcotest.(check (list approx)) "marked daily" [ 20.0; -30.0; 14.0 ] (pnls sessions);
  Alcotest.(check (list approx))
    "turnover is dollars traded, either way" [ 1_000.0; 0.0; 404.0 ]
    (List.map sessions ~f:(fun s -> s.Record.turnover));
  Alcotest.(check approx)
    "and the sessions sum to the account" 4.0
    (List.sum (module Float) sessions ~f:(fun s -> s.Record.pnl))

let test_a_record_starts_at_the_first_fill () =
  let sessions =
    of_book
      ~dates:[ "2026-10-19"; "2026-10-20" ]
      ~closes:[ ("2026-10-19", 100.0); ("2026-10-20", 101.0) ]
      [ fill "2026-10-20" 1.0 100.0 ]
  in
  Alcotest.(check (list string))
    "no session before it" [ "2026-10-20" ]
    (List.map sessions ~f:(fun s -> Date.to_string s.Record.date))

(* 2026-10-27's session was lost: no row and no marks. A fill that day still happened, so
   it lands in the next recorded session, and that session's move runs from the last
   close there was. Buy 1 at 100 on the lost day; the 26th closed 90 and the 28th 110,
   so the 28th earns 110 - 100 = +10. A held share marks 90 to 110, +20, on top. *)
let test_a_lost_session's_fill_lands_in_the_next () =
  let sessions =
    of_book
      ~dates:[ "2026-10-26"; "2026-10-28" ]
      ~closes:[ ("2026-10-26", 90.0); ("2026-10-28", 110.0) ]
      [ fill "2026-10-26" 1.0 90.0; fill "2026-10-27" 1.0 100.0 ]
  in
  Alcotest.(check (list approx)) "nothing dropped" [ 0.0; 30.0 ] (pnls sessions)

let test_a_close_that_is_missing_holds_the_last () =
  let sessions =
    of_book
      ~dates:[ "2026-10-20"; "2026-10-21"; "2026-10-22" ]
      ~closes:[ ("2026-10-20", 100.0); ("2026-10-22", 105.0) ]
      [ fill "2026-10-20" 2.0 100.0 ]
  in
  Alcotest.(check (list approx)) "flat, then the move" [ 0.0; 0.0; 10.0 ] (pnls sessions)

(* Review focus: an ex-date for a monthly payer. Hold 100 TLT bought at 90.00; it closes
   90.00, then 89.70 on an ex-date paying 0.30. The 0.30 the price lost is the 0.30 a
   holder at the prior close is owed: 100 x 89.70 - 100 x 90.00 + 100 x 0.30 = 0.00, not
   -30.00. *)
let tlt = Ohcamel.Types.Symbol.of_string "TLT"
let tlt_paying ex = [ { Record.ex_date = d ex; symbol = tlt; per_share = 0.30 } ]
let tlt_closes = [ ("2026-11-02", 90.0); ("2026-11-03", 89.7); ("2026-11-04", 89.7) ]

let test_an_ex_date_is_not_a_loss () =
  let sessions =
    of_book ~dividends:(tlt_paying "2026-11-03")
      ~dates:[ "2026-11-02"; "2026-11-03" ]
      ~closes:tlt_closes
      [ fill ~symbol:tlt "2026-11-02" 100.0 90.0 ]
  in
  Alcotest.(check (list approx)) "flat through the ex-date" [ 0.0; 0.0 ] (pnls sessions);
  Alcotest.(check (list approx))
    "the 30.00 is credited on the ex-date" [ 0.0; 30.0 ]
    (List.map sessions ~f:(fun s -> s.Record.dividends));
  Alcotest.(check approx)
    "and summarised" 30.0 (Record.summarise sessions).Record.dividends

(* Short 100 TLT at 90.00 through the same ex-date: the price falls 0.30 in its favour,
   +30.00, and a short owes the dividend, -30.00. *)
let test_a_short_through_an_ex_date_is_debited () =
  let sessions =
    of_book ~dividends:(tlt_paying "2026-11-03")
      ~dates:[ "2026-11-02"; "2026-11-03" ]
      ~closes:tlt_closes
      [ fill ~symbol:tlt "2026-11-02" (-100.0) 90.0 ]
  in
  Alcotest.(check (list approx))
    "debited" [ 0.0; -30.0 ]
    (List.map sessions ~f:(fun s -> s.Record.dividends));
  Alcotest.(check (list approx)) "and flat" [ 0.0; 0.0 ] (pnls sessions)

(* The holder is whoever held at the close BEFORE the ex-date: 100 bought on the
   ex-date itself at 89.70 is owed nothing. And an ex-date with no recorded session
   lands in the next one, as a fill does: 100 held from the 2nd, the 3rd lost, the 4th
   closes 89.70: -30.00 marked, +30.00 credited there. *)
let test_the_holder_is_the_prior_close's () =
  let bought_on_the_day =
    of_book ~dividends:(tlt_paying "2026-11-03")
      ~dates:[ "2026-11-02"; "2026-11-03" ]
      ~closes:tlt_closes
      [ fill ~symbol:tlt "2026-11-03" 100.0 89.7 ]
  in
  Alcotest.(check (list approx))
    "nothing for a buyer on the ex-date" [ 0.0 ]
    (List.map bought_on_the_day ~f:(fun s -> s.Record.dividends));
  let lost =
    of_book ~dividends:(tlt_paying "2026-11-03")
      ~dates:[ "2026-11-02"; "2026-11-04" ]
      ~closes:tlt_closes
      [ fill ~symbol:tlt "2026-11-02" 100.0 90.0 ]
  in
  Alcotest.(check (list approx))
    "a lost ex-date's dividend lands in the next session" [ 0.0; 30.0 ]
    (List.map lost ~f:(fun s -> s.Record.dividends));
  Alcotest.(check (list approx)) "flat" [ 0.0; 0.0 ] (pnls lost)

(* The brief's worked case: BUY 100 at 50.05 against an arrival mid of 50.00 with a fee
   of 0.10 costs 0.05 x 100 + 0.10 = 5.10, which is 5.10 / (100 x 50.00) = 10.2 bps. It
   closes at 50.00: the P&L is 5,000.00 - 5,005.00 - 0.10 = -5.10, the cost once and not
   -10.20, because the 5.00 is already in the price paid. *)
let test_a_fill's_cost_is_against_its_arrival_mid () =
  let sessions =
    of_book ~dates:[ "2026-11-02" ]
      ~closes:[ ("2026-11-02", 50.0) ]
      [ fill ~arrival_mid:50.0 ~fee:0.10 "2026-11-02" 100.0 50.05 ]
  in
  Alcotest.(check (list approx))
    "cost" [ 5.10 ]
    (List.map sessions ~f:(fun s -> s.Record.cost));
  Alcotest.(check (list approx)) "P&L, the cost once" [ -5.10 ] (pnls sessions);
  let s = Record.summarise sessions in
  Alcotest.(check (option (float 1e-9))) "10.2 bps" (Some 10.2) s.Record.cost_bps;
  Alcotest.(check int) "none unknown" 0 s.Record.cost_unknown

let test_cost_is_worse_than_mid_on_either_side () =
  let cost = Record.fill_cost in
  Alcotest.(check approx)
    "a sell below the mid costs" 5.0
    (cost (fill ~arrival_mid:50.0 "2026-11-02" (-100.0) 49.95));
  Alcotest.(check approx)
    "price improvement is a negative cost" (-2.0)
    (cost (fill ~arrival_mid:50.0 "2026-11-02" 100.0 49.98));
  Alcotest.(check approx)
    "no mid, the fee alone" 0.10
    (cost (fill ~fee:0.10 "2026-11-02" 100.0 50.05))

(* A fill with no arrival mid costs its fee and is counted; bps are over the measured
   fills only. The worked fill (5.10 on 5,000.00 at mid) beside 100 at 50.05 with no mid
   and a 0.10 fee: cost 5.20, one unknown, and still 10.2 bps. *)
let test_a_fill_with_no_arrival_mid_is_counted_unknown () =
  let sessions =
    of_book ~dates:[ "2026-11-02" ]
      ~closes:[ ("2026-11-02", 50.0) ]
      [
        fill ~arrival_mid:50.0 ~fee:0.10 "2026-11-02" 100.0 50.05;
        fill ~fee:0.10 "2026-11-02" 100.0 50.05;
      ]
  in
  Alcotest.(check (list approx))
    "its fee counts" [ 5.20 ]
    (List.map sessions ~f:(fun s -> s.Record.cost));
  let s = Record.summarise sessions in
  Alcotest.(check int) "one unknown" 1 s.Record.cost_unknown;
  Alcotest.(check (option (float 1e-9)))
    "bps over the measured fill alone" (Some 10.2) s.Record.cost_bps;
  let none =
    Record.summarise
      (of_book ~dates:[ "2026-11-02" ]
         ~closes:[ ("2026-11-02", 50.0) ]
         [ fill ~fee:0.10 "2026-11-02" 100.0 50.05 ])
  in
  Alcotest.(check (option (float 0.0)))
    "nothing measured, no bps" None none.Record.cost_bps

(* Notional-weighted, not a mean of each fill's bps: 5.10 on 5,000.00 and 10.00 on
   1,000.00 (10 at 101.00 against 100.00) is 15.10 / 6,000.00 = 25.1667 bps, where the
   mean of 10.2 and 100 would be 55.1. *)
let test_cost_bps_is_notional_weighted () =
  let msft = Ohcamel.Types.Symbol.of_string "MSFT" in
  let s =
    Record.summarise
      (Record.sessions_of
         ~dates:[ d "2026-11-02" ]
         ~close:(fun _ s ->
           if Ohcamel.Types.Symbol.equal s msft then Some 100.0 else Some 50.0)
         ~allocated:(fun _ -> 10_000.0)
         ~dividends:[]
         [
           fill ~arrival_mid:50.0 ~fee:0.10 "2026-11-02" 100.0 50.05;
           fill ~symbol:msft ~arrival_mid:100.0 "2026-11-02" 10.0 101.0;
         ])
  in
  Alcotest.(check (option (float 1e-9)))
    "weighted"
    (Some (15.10 /. 6_000.0 *. 10_000.0))
    s.Record.cost_bps

(* Fix round 1, minor 9: a SELLER on the ex-date held at the close before it, so is
   owed the dividend. 100 bought on the 2nd at 90.00, sold on the 3rd at 89.70: the
   30.00 fall is marked and the 30.00 credited, 0.00. *)
let test_a_seller_on_the_ex_date_is_credited () =
  let sessions =
    of_book ~dividends:(tlt_paying "2026-11-03")
      ~dates:[ "2026-11-02"; "2026-11-03" ]
      ~closes:tlt_closes
      [
        fill ~symbol:tlt "2026-11-02" 100.0 90.0;
        fill ~symbol:tlt "2026-11-03" (-100.0) 89.7;
      ]
  in
  Alcotest.(check (list approx))
    "credited" [ 0.0; 30.0 ]
    (List.map sessions ~f:(fun s -> s.Record.dividends));
  Alcotest.(check (list approx)) "flat" [ 0.0; 0.0 ] (pnls sessions)

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
      Alcotest.test_case "a strategy's P&L is marked to each close" `Quick
        test_a_strategy's_pnl_is_marked_to_each_close;
      Alcotest.test_case "a record starts at the first fill" `Quick
        test_a_record_starts_at_the_first_fill;
      Alcotest.test_case "a lost session's fill lands in the next" `Quick
        test_a_lost_session's_fill_lands_in_the_next;
      Alcotest.test_case "a close that is missing holds the last" `Quick
        test_a_close_that_is_missing_holds_the_last;
      Alcotest.test_case "an ex-date is not a loss" `Quick test_an_ex_date_is_not_a_loss;
      Alcotest.test_case "a short through an ex-date is debited" `Quick
        test_a_short_through_an_ex_date_is_debited;
      Alcotest.test_case "the holder is the prior close's" `Quick
        test_the_holder_is_the_prior_close's;
      Alcotest.test_case "a fill's cost is against its arrival mid" `Quick
        test_a_fill's_cost_is_against_its_arrival_mid;
      Alcotest.test_case "cost is worse than mid on either side" `Quick
        test_cost_is_worse_than_mid_on_either_side;
      Alcotest.test_case "a fill with no arrival mid is counted unknown" `Quick
        test_a_fill_with_no_arrival_mid_is_counted_unknown;
      Alcotest.test_case "cost bps is notional-weighted" `Quick
        test_cost_bps_is_notional_weighted;
      Alcotest.test_case "a seller on the ex-date is credited" `Quick
        test_a_seller_on_the_ex_date_is_credited;
    ] )
