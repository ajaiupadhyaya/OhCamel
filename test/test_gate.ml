(* The gate, on a book small enough to check by hand.

     AAPL  400 x 150 = 60,000   TECH
     MSFT  100 x 300 = 30,000   TECH
     XOM  -200 x 100 = -20,000  ENERGY
     cash 1,000,000; TECH 90,000; gross 110,000; net 70,000; equity 1,070,000

   Limits: aapl-cap 80,000 on AAPL, tech-cap 100,000 on TECH, book-cap 200,000
   on the portfolio. Every fill below is priced at the mark unless it says
   otherwise, so equity moves only where the test says it does. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Gate = Ohcamel.Gate

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let money = Notional.of_float
let limit name scope n = { Limit.name; scope; kind = Limit.Gross_notional (money n) }

let with_book ?(msft_qty = 100.0) ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        [
          limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
          limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
          limit "book-cap" Limit.Portfolio 200_000.0;
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  List.iter
    [ (aapl, 150.0, 400.0); (msft, 300.0, msft_qty); (xom, 100.0, -200.0) ]
    ~f:(fun (s, p, q) ->
      Graph.set_price graph s (Price.of_float p);
      Graph.set_qty graph s (Qty.of_float q);
      (* A full window, as test_stress.ml's book has, so every node a snapshot
         reads is evaluable; no limit here reads it. *)
      Graph.set_returns graph s
        [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |]);
  Graph.stabilize graph;
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let fill symbol qty price =
  { Gate.Fill.symbol; qty = Qty.of_float qty; price = Price.of_float price }

let names moves = List.map moves ~f:(fun m -> m.Gate.Move.limit)

let test_a_buy_that_takes_tech_over_its_cap_fails_naming_it () =
  with_book
    ~f:(fun graph ->
      (* AAPL 500 x 150 = 75,000 (under 80,000); TECH 75,000 + 30,000 = 105,000 (over 100,000) *)
      let v = Gate.check graph ~fills:[ fill aapl 100.0 150.0 ] in
      Alcotest.(check bool) "fails" false v.Gate.Verdict.passed;
      Alcotest.(check (list string))
        "created: tech-cap only" [ "tech-cap" ]
        (names v.Gate.Verdict.created);
      Alcotest.(check (float 1e-9))
        "gross after 110,000 + 15,000" 125_000.0
        (Notional.to_float v.Gate.Verdict.gross_after);
      Alcotest.(check bool)
        "the reason names the limit" true
        (List.exists (Gate.Verdict.reasons v)
           ~f:(String.is_substring ~substring:"tech-cap")))
    ()

let test_the_same_trade_the_other_way_passes () =
  with_book
    ~f:(fun graph ->
      (* TECH 45,000 + 30,000 = 75,000 *)
      let v = Gate.check graph ~fills:[ fill aapl (-100.0) 150.0 ] in
      Alcotest.(check bool) "passes" true v.Gate.Verdict.passed;
      Alcotest.(check (list string)) "nothing created" [] (names v.Gate.Verdict.created);
      Alcotest.(check (float 1e-9))
        "gross 110,000 - 15,000" 95_000.0
        (Notional.to_float v.Gate.Verdict.gross_after))
    ()

let test_worsening_a_breach_fails_and_reducing_it_passes () =
  (* MSFT 200 x 300 = 60,000, so TECH is 120,000 and 20,000 over. *)
  with_book ~msft_qty:200.0
    ~f:(fun graph ->
      (* +10 MSFT: TECH 123,000, 23,000 over > 20,000 *)
      let up = Gate.check graph ~fills:[ fill msft 10.0 300.0 ] in
      Alcotest.(check bool) "worsening fails" false up.Gate.Verdict.passed;
      Alcotest.(check (list string))
        "worsened: tech-cap" [ "tech-cap" ]
        (names up.Gate.Verdict.worsened);
      (* -10 MSFT: TECH 117,000, 17,000 over < 20,000 -- still breached, and better *)
      let down = Gate.check graph ~fills:[ fill msft (-10.0) 300.0 ] in
      Alcotest.(check bool) "reducing passes" true down.Gate.Verdict.passed;
      Alcotest.(check (list string)) "not worsened" [] (names down.Gate.Verdict.worsened);
      Alcotest.(check (list string))
        "not cleared either: still over" []
        (names down.Gate.Verdict.cleared))
    ()

let test_a_rebalance_is_gated_as_one_set_of_fills () =
  with_book
    ~f:(fun graph ->
      (* +100 AAPL alone breaches TECH (test above). With -100 MSFT beside it,
         TECH is 75,000 + 0 = 75,000. *)
      let v =
        Gate.check graph ~fills:[ fill aapl 100.0 150.0; fill msft (-100.0) 300.0 ]
      in
      Alcotest.(check bool) "the pair passes" true v.Gate.Verdict.passed)
    ()

let test_a_price_away_from_the_mark_moves_equity_by_exactly_the_difference () =
  with_book
    ~f:(fun graph ->
      (* Buy 10 AAPL at 151 against a mark of 150: cash falls 1,510, exposure
         rises 1,500, equity falls 10. 1,070,000 - 10 = 1,069,990. *)
      let v = Gate.check graph ~fills:[ fill aapl 10.0 151.0 ] in
      Alcotest.(check (float 1e-6))
        "equity before" 1_070_000.0
        (Notional.to_float v.Gate.Verdict.equity_before);
      Alcotest.(check (float 1e-6))
        "equity after" 1_069_990.0
        (Notional.to_float v.Gate.Verdict.equity_after))
    ()

let test_the_live_book_does_not_move () =
  with_book
    ~f:(fun graph ->
      ignore
        (Gate.check graph ~fills:[ fill aapl 100.0 150.0; fill xom 50.0 100.0 ]
          : Gate.Verdict.t);
      Alcotest.(check (float 0.0))
        "AAPL still 400" 400.0
        (Qty.to_float (Graph.qty graph aapl));
      Alcotest.(check (float 0.0))
        "XOM still -200" (-200.0)
        (Qty.to_float (Graph.qty graph xom));
      Alcotest.(check (float 0.0))
        "cash still 1,000,000" 1_000_000.0
        (Notional.to_float (Graph.cash graph));
      Alcotest.(check (float 1e-9))
        "gross still 110,000" 110_000.0
        (Notional.to_float (Graph.gross_exposure graph)))
    ()

(* Isolation over arbitrary proposals: whatever is proposed, the live book's
   quantities and cash are what they were. *)
let prop_the_gate_never_moves_the_live_book =
  let open QCheck in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"the gate never moves the live book, whatever is proposed" ~count:100
       (list_size Gen.(int_range 1 4) (pair (int_bound 2) (int_range (-50) 50)))
       (fun proposals ->
         let symbols = [| aapl; msft; xom |] and marks = [| 150.0; 300.0; 100.0 |] in
         with_book
           ~f:(fun graph ->
             let before =
               List.map [ aapl; msft; xom ] ~f:(fun s -> Qty.to_float (Graph.qty graph s))
             in
             let fills =
               List.filter_map proposals ~f:(fun (i, q) ->
                   if q = 0 then None
                   else Some (fill symbols.(i) (Float.of_int q) marks.(i)))
             in
             ignore (Gate.check graph ~fills : Gate.Verdict.t);
             List.equal Float.equal before
               (List.map [ aapl; msft; xom ] ~f:(fun s ->
                    Qty.to_float (Graph.qty graph s)))
             && Float.equal 1_000_000.0 (Notional.to_float (Graph.cash graph)))
           ()))

(* Spec §6: the gate never passes a proposal whose fork creates a breach --
   checked against the live book, not the fork. Whatever passed is applied for
   real, and no limit that was clear before is over its line after. If the
   fork and the live graph ever disagreed, this is where it would show. The
   range is wide enough (up to 300 shares, 90,000 of MSFT) that both answers
   occur. *)
let prop_what_the_gate_passes_creates_no_breach_when_traded =
  let open QCheck in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"what the gate passes creates no breach when it is traded" ~count:100
       (list_size Gen.(int_range 1 4) (pair (int_bound 2) (int_range (-300) 300)))
       (fun proposals ->
         let symbols = [| aapl; msft; xom |] and marks = [| 150.0; 300.0; 100.0 |] in
         with_book
           ~f:(fun graph ->
             let fills =
               List.filter_map proposals ~f:(fun (i, q) ->
                   if q = 0 then None
                   else Some (fill symbols.(i) (Float.of_int q) marks.(i)))
             in
             let clear_before =
               List.filter_map (Graph.limit_results graph) ~f:(fun (l, b) ->
                   match b with
                   | Some b when not (Breach.breached b) -> Some (Limit.name l)
                   | _ -> None)
             in
             let v = Gate.check graph ~fills in
             (not v.Gate.Verdict.passed)
             ||
             (List.iter fills ~f:(fun (f : Gate.Fill.t) ->
                  Graph.apply_fill graph
                    {
                      Fill.symbol = f.Gate.Fill.symbol;
                      qty = f.Gate.Fill.qty;
                      price = f.Gate.Fill.price;
                      time = Time.epoch;
                    });
              Graph.stabilize graph;
              List.for_all (Graph.limit_results graph) ~f:(fun (l, b) ->
                  (not (List.mem clear_before (Limit.name l) ~equal:String.equal))
                  || match b with Some b -> not (Breach.breached b) | None -> true)))
           ()))

let suite =
  ( "gate",
    [
      Alcotest.test_case "a buy that takes TECH over its cap fails, naming it" `Quick
        test_a_buy_that_takes_tech_over_its_cap_fails_naming_it;
      Alcotest.test_case "the same trade the other way passes" `Quick
        test_the_same_trade_the_other_way_passes;
      Alcotest.test_case "worsening a breach fails and reducing it passes" `Quick
        test_worsening_a_breach_fails_and_reducing_it_passes;
      Alcotest.test_case "a rebalance is gated as one set of fills" `Quick
        test_a_rebalance_is_gated_as_one_set_of_fills;
      Alcotest.test_case
        "a price away from the mark moves equity by exactly the difference" `Quick
        test_a_price_away_from_the_mark_moves_equity_by_exactly_the_difference;
      Alcotest.test_case "the live book does not move" `Quick
        test_the_live_book_does_not_move;
      prop_the_gate_never_moves_the_live_book;
      prop_what_the_gate_passes_creates_no_breach_when_traded;
    ] )
