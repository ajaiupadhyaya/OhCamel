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

let suite =
  ( "tca",
    [
      Alcotest.test_case "a buy" `Quick test_a_buy;
      Alcotest.test_case "a sell is the mirror" `Quick test_a_sell_is_the_mirror;
      Alcotest.test_case "without a quote, only shortfall is known" `Quick
        test_without_a_quote_only_shortfall_is_known;
      Alcotest.test_case "a summary weights by quantity" `Quick
        test_a_summary_weights_by_quantity;
    ] )
