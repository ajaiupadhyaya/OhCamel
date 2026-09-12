(* The book, from the venue.

   Two facts carry the module. A universe name the venue holds nothing in is
   SET to zero -- including when the last sync left it holding something --
   and a venue position outside the universe is named, not absorbed. The
   graph test then checks the identity that makes the two ledgers agree:
   Alpaca's equity is cash plus signed market value, the graph's is cash plus
   net exposure, and those are the same sum. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Book_sync = Ohcamel_desk.Book_sync
module Venue = Ohcamel_desk.Venue

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let tsla = Symbol.of_string "TSLA"

let position symbol qty =
  {
    Venue.Position.symbol;
    asset_class = "us_equity";
    qty = Qty.of_float qty;
    avg_entry_price = None;
    market_value = None;
  }

let account cash =
  {
    Venue.Account.equity = Notional.of_float cash;
    cash = Notional.of_float cash;
    buying_power = Notional.of_float cash;
    last_equity = None;
    status = "ACTIVE";
    trading_blocked = false;
    shorting_enabled = true;
  }

let test_the_plan_zeroes_what_is_not_held_and_names_what_cannot_be () =
  let p =
    Book_sync.plan ~universe:[ msft; aapl ]
      ~positions:[ position tsla 5.0; position aapl 10.0 ]
      ~account:(account 90_000.0)
  in
  Alcotest.(check (list (pair string (float 0.0))))
    "every universe name in symbol order, MSFT at zero"
    [ ("AAPL", 10.0); ("MSFT", 0.0) ]
    (List.map p.Book_sync.Plan.quantities ~f:(fun (s, q) ->
         (Symbol.to_string s, Qty.to_float q)));
  Alcotest.(check (float 0.0))
    "cash is the venue's" 90_000.0
    (Notional.to_float p.Book_sync.Plan.cash);
  Alcotest.(check (list string))
    "TSLA is outside the universe" [ "TSLA" ]
    (List.map p.Book_sync.Plan.unmanaged ~f:(fun u ->
         Symbol.to_string u.Venue.Position.symbol))

let with_graph ~f =
  let tech = Sector.of_string "TECH" in
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = tech };
          { Instrument.symbol = msft; sector = tech };
        ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Graph.set_price graph aapl (Price.of_float 150.0);
  Graph.set_price graph msft (Price.of_float 300.0);
  Graph.stabilize graph;
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let test_the_graph_agrees_with_the_venue_on_equity () =
  with_graph ~f:(fun graph ->
      Book_sync.apply graph
        (Book_sync.plan ~universe:(Graph.symbols graph)
           ~positions:[ position aapl 10.0 ]
           ~account:(account 90_000.0));
      Alcotest.(check (float 0.0)) "AAPL 10" 10.0 (Qty.to_float (Graph.qty graph aapl));
      Alcotest.(check (float 0.0)) "MSFT 0" 0.0 (Qty.to_float (Graph.qty graph msft));
      (* Alpaca: cash + long market value = 90,000 + 10 x 150 = 91,500.
         The graph: cash + net exposure = 90,000 + (1,500 + 0) = 91,500. *)
      Alcotest.(check (float 1e-9))
        "equity 91,500 on both ledgers" 91_500.0
        (Notional.to_float (Graph.equity graph)))

let test_a_position_the_venue_closed_goes_to_zero () =
  with_graph ~f:(fun graph ->
      Graph.set_qty graph msft (Qty.of_float 25.0);
      Graph.stabilize graph;
      Book_sync.apply graph
        (Book_sync.plan ~universe:(Graph.symbols graph) ~positions:[]
           ~account:(account 100_000.0));
      Alcotest.(check (float 0.0))
        "set to zero, not left at 25" 0.0
        (Qty.to_float (Graph.qty graph msft)))

let suite =
  ( "book_sync",
    [
      Alcotest.test_case "the plan zeroes what is not held and names what cannot be"
        `Quick test_the_plan_zeroes_what_is_not_held_and_names_what_cannot_be;
      Alcotest.test_case "the graph agrees with the venue on equity" `Quick
        test_the_graph_agrees_with_the_venue_on_equity;
      Alcotest.test_case "a position the venue closed goes to zero" `Quick
        test_a_position_the_venue_closed_goes_to_zero;
    ] )
