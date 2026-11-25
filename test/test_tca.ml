(* Costs in basis points, against a buy and a sell written out by hand.

   Positive is cost, for either side: a buy that fills above its decision
   price and a sell that fills below its decision price both cost. *)

open Core
open Ohcamel.Types
module Tca = Ohcamel_desk.Tca
module Order = Ohcamel_desk.Order

let aapl = Symbol.of_string "AAPL"
let bps = Alcotest.(option (float 1e-6))

let buy =
  {
    Tca.Inputs.symbol = aapl;
    side = Order.Side.Buy;
    qty = 10.0;
    decision = 100.00;
    bid = Some 100.02;
    ask = Some 100.06;
    fill = 100.07;
  }

let test_a_buy () =
  let c = Tca.of_fill ~model_half_spread_bps:2.0 buy in
  (* (100.07 - 100.00) / 100.00 x 10,000 = 7.0 *)
  Alcotest.(check (float 1e-6)) "shortfall" 7.0 c.Tca.Costs.shortfall_bps;
  (* mid (100.02 + 100.06) / 2 = 100.04; (100.04 - 100.00) / 100.00 x 10,000 = 4.0 *)
  Alcotest.check bps "delay" (Some 4.0) c.Tca.Costs.delay_bps;
  (* (100.07 - 100.04) / 100.04 x 10,000 = 2.998800479808... *)
  Alcotest.check bps "slippage"
    (Some (0.03 /. 100.04 *. 10_000.0))
    c.Tca.Costs.slippage_bps;
  (* (100.06 - 100.02) / 2 / 100.04 x 10,000 = 1.999200319872... *)
  Alcotest.check bps "half spread"
    (Some (0.02 /. 100.04 *. 10_000.0))
    c.Tca.Costs.half_spread_bps;
  (* slippage - 2.0 *)
  Alcotest.check bps "versus model"
    (Some ((0.03 /. 100.04 *. 10_000.0) -. 2.0))
    c.Tca.Costs.versus_model_bps

let test_a_sell_is_the_mirror () =
  let sell =
    {
      buy with
      Tca.Inputs.side = Order.Side.Sell;
      bid = Some 99.94;
      ask = Some 99.98;
      fill = 99.93;
    }
  in
  let c = Tca.of_fill ~model_half_spread_bps:2.0 sell in
  (* -1 x (99.93 - 100.00) / 100.00 x 10,000 = 7.0 *)
  Alcotest.(check (float 1e-6)) "shortfall" 7.0 c.Tca.Costs.shortfall_bps;
  (* mid 99.96: -1 x (99.96 - 100.00) / 100.00 x 10,000 = 4.0 *)
  Alcotest.check bps "delay" (Some 4.0) c.Tca.Costs.delay_bps;
  (* -1 x (99.93 - 99.96) / 99.96 x 10,000 = 3.001200480... *)
  Alcotest.check bps "slippage"
    (Some (0.03 /. 99.96 *. 10_000.0))
    c.Tca.Costs.slippage_bps

let test_without_a_quote_only_shortfall_is_known () =
  let c = Tca.of_fill ~model_half_spread_bps:2.0 { buy with Tca.Inputs.bid = None } in
  Alcotest.(check (float 1e-6))
    "shortfall needs only the decision" 7.0 c.Tca.Costs.shortfall_bps;
  Alcotest.check bps "delay unknown" None c.Tca.Costs.delay_bps;
  Alcotest.check bps "versus model unknown" None c.Tca.Costs.versus_model_bps

let test_a_summary_weights_by_quantity () =
  let at shortfall qty =
    ( { buy with Tca.Inputs.qty },
      {
        (Tca.of_fill ~model_half_spread_bps:2.0 buy) with
        Tca.Costs.shortfall_bps = shortfall;
      } )
  in
  let s = Tca.summarize [ at 7.0 10.0; at 3.0 30.0 ] in
  Alcotest.(check int) "two fills" 2 s.Tca.Summary.count;
  (* mean (7 + 3) / 2 = 5; median of two = 5; weighted (7 x 10 + 3 x 30) / 40 = 160 / 40 = 4 *)
  Alcotest.check bps "mean" (Some 5.0) s.Tca.Summary.mean_shortfall_bps;
  Alcotest.check bps "median" (Some 5.0) s.Tca.Summary.median_shortfall_bps;
  Alcotest.check bps "weighted" (Some 4.0) s.Tca.Summary.weighted_shortfall_bps;
  Alcotest.check bps "an empty summary knows nothing" None
    (Tca.summarize []).Tca.Summary.mean_shortfall_bps;
  let msft = { buy with Tca.Inputs.symbol = Symbol.of_string "MSFT" } in
  Alcotest.(check (list (pair string int)))
    "by symbol: sorted, each summarized alone"
    [ ("AAPL", 2); ("MSFT", 1) ]
    (List.map
       (Tca.by_symbol
          [
            at 7.0 10.0; (msft, Tca.of_fill ~model_half_spread_bps:2.0 msft); at 3.0 30.0;
          ])
       ~f:(fun (s, x) -> (Symbol.to_string s, x.Tca.Summary.count)))

(* A buy at 50.10 against open 50.00, close 50.20 and VWAP 50.05, by the formula
   side x (fill - benchmark) / benchmark x 10,000:
     open   (50.10 - 50.00) / 50.00 x 10,000 = +20.0
     close  (50.10 - 50.20) / 50.20 x 10,000 = -19.920... (-20 bp to the nearest bp)
     vwap   (50.10 - 50.05) / 50.05 x 10,000 = +9.990... (+10 bp to the nearest bp) *)
let bench ?vwap_reason ~open_ ~close ~vwap () =
  { Tca.Benchmark.open_; close; vwap; vwap_reason }

let test_a_buy_against_the_open_the_close_and_vwap () =
  let b =
    Tca.benchmarks
      { buy with Tca.Inputs.fill = 50.10 }
      (bench ~open_:(Some 50.00) ~close:(Some 50.20) ~vwap:(Some 50.05) ())
  in
  Alcotest.check bps "vs open" (Some 20.0) b.Tca.Bench_costs.vs_open;
  Alcotest.check bps "vs close"
    (Some (-0.10 /. 50.20 *. 10_000.0))
    b.Tca.Bench_costs.vs_close;
  Alcotest.check bps "vs vwap"
    (Some (0.05 /. 50.05 *. 10_000.0))
    b.Tca.Bench_costs.vs_vwap;
  let near x = function
    | Some v -> Float.( < ) (Float.abs (v -. x)) 0.1
    | None -> false
  in
  Alcotest.(check bool)
    "close is -20 bp to 0.1" true
    (near (-20.0) b.Tca.Bench_costs.vs_close);
  Alcotest.(check bool) "vwap is +10 bp to 0.1" true (near 10.0 b.Tca.Bench_costs.vs_vwap);
  Alcotest.(check (option string))
    "no reason when it is known" None b.Tca.Bench_costs.vwap_reason

let test_a_sell_against_the_benchmarks_is_the_mirror () =
  (* Selling at 50.10: above the open and the VWAP is price improvement (negative),
     below the close is cost (positive). *)
  let b =
    Tca.benchmarks
      { buy with Tca.Inputs.side = Order.Side.Sell; fill = 50.10 }
      (bench ~open_:(Some 50.00) ~close:(Some 50.20) ~vwap:(Some 50.05) ())
  in
  Alcotest.check bps "vs open" (Some (-20.0)) b.Tca.Bench_costs.vs_open;
  Alcotest.check bps "vs close"
    (Some (0.10 /. 50.20 *. 10_000.0))
    b.Tca.Bench_costs.vs_close;
  Alcotest.check bps "vs vwap"
    (Some (-0.05 /. 50.05 *. 10_000.0))
    b.Tca.Bench_costs.vs_vwap

let test_an_unknown_benchmark_is_null_with_a_reason () =
  let b = Tca.benchmarks buy (Tca.Benchmark.none "the venue keeps no bar history") in
  Alcotest.check bps "open" None b.Tca.Bench_costs.vs_open;
  Alcotest.check bps "close" None b.Tca.Bench_costs.vs_close;
  Alcotest.check bps "vwap is not made up" None b.Tca.Bench_costs.vs_vwap;
  Alcotest.(check (option string))
    "says why" (Some "the venue keeps no bar history") b.Tca.Bench_costs.vwap_reason;
  let no_vw =
    Tca.benchmarks buy (bench ~open_:(Some 100.0) ~close:(Some 100.0) ~vwap:None ())
  in
  Alcotest.check bps "a bar with no vwap still has its open" (Some 7.0)
    no_vw.Tca.Bench_costs.vs_open;
  Alcotest.(check bool)
    "and a reason for the vwap" true
    (Option.is_some no_vw.Tca.Bench_costs.vwap_reason);
  let bad =
    Tca.benchmarks buy (bench ~open_:(Some 100.0) ~close:(Some 100.0) ~vwap:(Some 0.0) ())
  in
  Alcotest.check bps "a non-positive vw is no vwap" None bad.Tca.Bench_costs.vs_vwap;
  Alcotest.(check (option string))
    "and the reason says so, not that it was absent"
    (Some "the venue's bar carried a volume-weighted price that is not positive")
    bad.Tca.Bench_costs.vwap_reason

let test_a_strategy_roll_up_is_counts_and_basis_points () =
  let cost o c v =
    { Tca.Bench_costs.vs_open = o; vs_close = c; vs_vwap = v; vwap_reason = None }
  in
  let rolled =
    Tca.by_strategy
      [
        ("b", cost (Some 4.0) None None);
        ("a", cost (Some 20.0) (Some (-20.0)) (Some 10.0));
        ("a", cost (Some 10.0) None (Some 20.0));
      ]
  in
  Alcotest.(check (list string)) "sorted" [ "a"; "b" ] (List.map rolled ~f:fst);
  let a = List.Assoc.find_exn rolled ~equal:String.equal "a" in
  Alcotest.(check int) "two fills" 2 a.Tca.Bench_summary.count;
  Alcotest.(check int) "one has a close" 1 a.Tca.Bench_summary.close_n;
  Alcotest.check bps "mean open" (Some 15.0) a.Tca.Bench_summary.mean_vs_open_bps;
  Alcotest.check bps "mean close" (Some (-20.0)) a.Tca.Bench_summary.mean_vs_close_bps;
  Alcotest.check bps "mean vwap" (Some 15.0) a.Tca.Bench_summary.mean_vs_vwap_bps;
  let b = List.Assoc.find_exn rolled ~equal:String.equal "b" in
  Alcotest.check bps "no vwap, no mean" None b.Tca.Bench_summary.mean_vs_vwap_bps

let suite =
  ( "tca",
    [
      Alcotest.test_case "a buy" `Quick test_a_buy;
      Alcotest.test_case "a sell is the mirror" `Quick test_a_sell_is_the_mirror;
      Alcotest.test_case "without a quote, only shortfall is known" `Quick
        test_without_a_quote_only_shortfall_is_known;
      Alcotest.test_case "a summary weights by quantity" `Quick
        test_a_summary_weights_by_quantity;
      Alcotest.test_case "a buy against the open, the close and vwap" `Quick
        test_a_buy_against_the_open_the_close_and_vwap;
      Alcotest.test_case "a sell against the benchmarks is the mirror" `Quick
        test_a_sell_against_the_benchmarks_is_the_mirror;
      Alcotest.test_case "an unknown benchmark is null with a reason" `Quick
        test_an_unknown_benchmark_is_null_with_a_reason;
      Alcotest.test_case "a strategy roll-up is counts and basis points" `Quick
        test_a_strategy_roll_up_is_counts_and_basis_points;
    ] )
