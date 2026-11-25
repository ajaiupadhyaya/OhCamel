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
      Alcotest.(check bool) "fails" false (Gate.Verdict.passed v);
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
      Alcotest.(check bool) "passes" true (Gate.Verdict.passed v);
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
      Alcotest.(check bool) "worsening fails" false (Gate.Verdict.passed up);
      Alcotest.(check (list string))
        "worsened: tech-cap" [ "tech-cap" ]
        (names up.Gate.Verdict.worsened);
      (* -10 MSFT: TECH 117,000, 17,000 over < 20,000 -- still breached, and better *)
      let down = Gate.check graph ~fills:[ fill msft (-10.0) 300.0 ] in
      Alcotest.(check bool) "reducing passes" true (Gate.Verdict.passed down);
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
      Alcotest.(check bool) "the pair passes" true (Gate.Verdict.passed v))
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

(* THE BASE (Task 3's fix). A base is applied to the fork before "before" is
   read, and created, worsened and reduced are judged against it. *)

let verdict_sexp v = Sexp.to_string (Gate.Verdict.sexp_of_t v)

(* With no base, "before" is the live book, read as it always was: an explicit
   empty base is the same verdict, field for field, for a proposal that
   creates a breach, one that reduces, and a pair. A base that nets to
   nothing -- 10 AAPL bought and sold at the 150 mark, so AAPL is 400 and cash
   1,000,000 again -- reads "before" from the fork instead, and still gives
   the same verdict, which is what lets the fork stand in for the live book.
   Gross before is the live 110,000 and equity before the live 1,070,000
   throughout. *)
let test_an_empty_base_is_the_check_without_one () =
  with_book
    ~f:(fun graph ->
      List.iter
        [
          [ fill aapl 100.0 150.0 ];
          [ fill aapl (-100.0) 150.0 ];
          [ fill aapl 100.0 150.0; fill msft (-100.0) 300.0 ];
        ]
        ~f:(fun fills ->
          let without = Gate.check graph ~fills in
          Alcotest.(check string)
            "an explicit empty base" (verdict_sexp without)
            (verdict_sexp (Gate.check ~base:[] graph ~fills));
          Alcotest.(check string)
            "a base that nets to nothing" (verdict_sexp without)
            (verdict_sexp
               (Gate.check
                  ~base:[ fill aapl 10.0 150.0; fill aapl (-10.0) 150.0 ]
                  graph ~fills));
          Alcotest.(check (float 1e-9))
            "gross before is the live 110,000" 110_000.0
            (Notional.to_float without.Gate.Verdict.gross_before);
          Alcotest.(check (float 1e-6))
            "equity before is the live 1,070,000" 1_070_000.0
            (Notional.to_float without.Gate.Verdict.equity_before)))
    ()

(* A base that breaches, and a proposal that brings the breach back: it
   passes. The base buys 100 AAPL at the mark: AAPL 500 x 150 = 75,000 (under
   80,000), TECH 75,000 + 30,000 = 105,000, 5,000 over. Selling 20 AAPL takes
   TECH to 102,000, 2,000 over: still breached, and reduced. The same two
   fills as ONE list, judged from the live book (TECH 90,000, clear), would
   read as a breach created -- the base is why they do not. *)
let test_a_base_that_breaches_and_a_proposal_that_reduces_it_passes () =
  with_book
    ~f:(fun graph ->
      let base = [ fill aapl 100.0 150.0 ] and fills = [ fill aapl (-20.0) 150.0 ] in
      let v = Gate.check ~base graph ~fills in
      Alcotest.(check bool) "passes" true (Gate.Verdict.passed v);
      Alcotest.(check (list string)) "nothing created" [] (names v.Gate.Verdict.created);
      Alcotest.(check (list string)) "nothing worsened" [] (names v.Gate.Verdict.worsened);
      Alcotest.(check (list string))
        "not cleared: 2,000 over is still over" []
        (names v.Gate.Verdict.cleared);
      Alcotest.(check (float 1e-9))
        "gross before is the base's: 110,000 + 15,000" 125_000.0
        (Notional.to_float v.Gate.Verdict.gross_before);
      Alcotest.(check (float 1e-9))
        "gross after: 125,000 - 3,000" 122_000.0
        (Notional.to_float v.Gate.Verdict.gross_after);
      let flat = Gate.check graph ~fills:(base @ fills) in
      Alcotest.(check (list string))
        "from the live book the same fills create it" [ "tech-cap" ]
        (names flat.Gate.Verdict.created))
    ()

(* A base that breaches, and a proposal that takes it further over: it fails
   as worsened, not created. Base as above, TECH 105,000, 5,000 over; buying
   10 MSFT at 300 takes it to 108,000, 8,000 over. *)
let test_a_base_that_breaches_and_a_proposal_that_worsens_it_fails () =
  with_book
    ~f:(fun graph ->
      let v =
        Gate.check ~base:[ fill aapl 100.0 150.0 ] graph ~fills:[ fill msft 10.0 300.0 ]
      in
      Alcotest.(check bool) "fails" false (Gate.Verdict.passed v);
      Alcotest.(check (list string))
        "worsened: tech-cap" [ "tech-cap" ]
        (names v.Gate.Verdict.worsened);
      Alcotest.(check (list string)) "not created" [] (names v.Gate.Verdict.created);
      Alcotest.(check bool)
        "the reason says further over" true
        (List.exists (Gate.Verdict.reasons v)
           ~f:(String.is_substring ~substring:"tech-cap would be further over its line")))
    ()

(* A clear base, and a proposal that breaches on it though it would not on
   the live book: it fails as created. The base buys 50 AAPL: AAPL 450 x 150
   = 67,500, TECH 97,500, 2,500 of room. Buying 10 MSFT at 300 (3,000) takes
   TECH to 100,500 on the base; on the live book it would be 93,000. *)
let test_a_clear_base_and_a_proposal_that_breaches_on_it_fails () =
  with_book
    ~f:(fun graph ->
      let fills = [ fill msft 10.0 300.0 ] in
      let v = Gate.check ~base:[ fill aapl 50.0 150.0 ] graph ~fills in
      Alcotest.(check bool) "fails" false (Gate.Verdict.passed v);
      Alcotest.(check (list string))
        "created: tech-cap" [ "tech-cap" ]
        (names v.Gate.Verdict.created);
      Alcotest.(check (float 1e-9))
        "gross before: 110,000 + 7,500" 117_500.0
        (Notional.to_float v.Gate.Verdict.gross_before);
      Alcotest.(check bool)
        "the live book alone would take it" true
        (Gate.Verdict.passed (Gate.check graph ~fills)))
    ()

(* UNKNOWN IS NOT PERMISSION (Task 1.5). The book above with a VaR limit
   whose window is never filled: no returns are set, so var-cap -- a
   Value_at_risk limit -- cannot be evaluated, before or after any fill.
   [trips_on] names the limits the kill switch trips on; [var2] adds a
   second VaR limit, configured after the first. *)
let var_limit name =
  { Limit.name; scope = Limit.Portfolio; kind = Limit.Value_at_risk (money 1e9) }

let with_warming_book ?(var2 = false) ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        ([
           limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
           limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
           var_limit "var-cap";
           limit "book-cap" Limit.Portfolio 200_000.0;
         ]
        @ if var2 then [ var_limit "var-cap-2" ] else [])
      ~confidence:0.95 ~return_window:10 ()
  in
  List.iter
    [ (aapl, 150.0, 400.0); (msft, 300.0, 100.0); (xom, 100.0, -200.0) ]
    ~f:(fun (s, p, q) ->
      Graph.set_price graph s (Price.of_float p);
      Graph.set_qty graph s (Qty.of_float q));
  Graph.stabilize graph;
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let outcome =
  Alcotest.testable
    (Fmt.of_to_string (fun o -> Sexp.to_string (Gate.Outcome.sexp_of_t o)))
    Gate.Outcome.equal

(* +10 AAPL: gross 110,000 -> 111,500, a raise, with var-cap unevaluable and
   named in kill_switch_trips_on: refused, naming it. *)
let test_an_unevaluable_trip_limit_refuses_a_gross_raising_proposal () =
  with_warming_book
    ~f:(fun graph ->
      let v = Gate.check ~trips_on:[ "var-cap" ] graph ~fills:[ fill aapl 10.0 150.0 ] in
      Alcotest.check outcome "refused, naming var-cap"
        (Gate.Outcome.Refused_unevaluable [ "var-cap" ]) v.Gate.Verdict.outcome;
      Alcotest.(check bool) "does not pass" false (Gate.Verdict.passed v);
      Alcotest.(check (list string))
        "var-cap is unevaluable" [ "var-cap" ]
        (names v.Gate.Verdict.unevaluable);
      Alcotest.(check bool)
        "the reason names the limit" true
        (List.exists (Gate.Verdict.reasons v)
           ~f:(String.is_substring ~substring:"var-cap cannot be evaluated")))
    ()

(* -10 AAPL: gross 110,000 -> 108,500. The same unevaluable trip-limit lets a
   proposal that lowers gross through: it is still reported unevaluable. *)
let test_the_same_with_a_gross_lowering_proposal_passes () =
  with_warming_book
    ~f:(fun graph ->
      let v =
        Gate.check ~trips_on:[ "var-cap" ] graph ~fills:[ fill aapl (-10.0) 150.0 ]
      in
      Alcotest.check outcome "passes" Gate.Outcome.Passed v.Gate.Verdict.outcome;
      Alcotest.(check bool) "passes" true (Gate.Verdict.passed v);
      Alcotest.(check (list string))
        "still reported" [ "var-cap" ]
        (names v.Gate.Verdict.unevaluable);
      (* Covering 10 XOM of the -200 short lowers gross too. *)
      let cover =
        Gate.check ~trips_on:[ "var-cap" ] graph ~fills:[ fill xom 10.0 100.0 ]
      in
      Alcotest.check outcome "a cover passes" Gate.Outcome.Passed
        cover.Gate.Verdict.outcome)
    ()

(* The boundary. +10 MSFT (+3,000) beside -20 AAPL (-3,000): gross 110,000
   either side, exactly -- equal is not a raise, and it passes. -10 AAPL
   (-1,500) beside +10 MSFT (+3,000) lowers one name and raises another by
   more: gross 111,500, a raise, refused. *)
let test_equal_gross_is_not_a_raise_and_a_swap_that_raises_it_is () =
  with_warming_book
    ~f:(fun graph ->
      let equal =
        Gate.check ~trips_on:[ "var-cap" ] graph
          ~fills:[ fill msft 10.0 300.0; fill aapl (-20.0) 150.0 ]
      in
      Alcotest.(check (float 0.0))
        "gross unchanged"
        (Notional.to_float equal.Gate.Verdict.gross_before)
        (Notional.to_float equal.Gate.Verdict.gross_after);
      Alcotest.check outcome "equal passes" Gate.Outcome.Passed equal.Gate.Verdict.outcome;
      let swap =
        Gate.check ~trips_on:[ "var-cap" ] graph
          ~fills:[ fill aapl (-10.0) 150.0; fill msft 10.0 300.0 ]
      in
      Alcotest.(check (float 1e-9))
        "gross 111,500" 111_500.0
        (Notional.to_float swap.Gate.Verdict.gross_after);
      Alcotest.check outcome "the raising swap is refused"
        (Gate.Outcome.Refused_unevaluable [ "var-cap" ]) swap.Gate.Verdict.outcome)
    ()

(* An unevaluable limit the switch does not trip on behaves as before: a
   raise passes, the limit reported unevaluable. *)
let test_an_unevaluable_limit_that_is_not_a_trip_limit_passes_as_before () =
  with_warming_book
    ~f:(fun graph ->
      List.iter [ []; [ "tech-cap" ] ] ~f:(fun trips_on ->
          let v = Gate.check ~trips_on graph ~fills:[ fill aapl 10.0 150.0 ] in
          Alcotest.check outcome "passes" Gate.Outcome.Passed v.Gate.Verdict.outcome;
          Alcotest.(check (list string))
            "reported" [ "var-cap" ]
            (names v.Gate.Verdict.unevaluable));
      let v = Gate.check graph ~fills:[ fill aapl 10.0 150.0 ] in
      Alcotest.check outcome "no trips_on passes" Gate.Outcome.Passed
        v.Gate.Verdict.outcome)
    ()

(* Two unevaluable trip-limits are named in the order the book configures
   its limits, whatever order kill_switch_trips_on lists them in. *)
let test_the_refusal_names_the_limits_in_the_order_they_are_configured () =
  with_warming_book ~var2:true
    ~f:(fun graph ->
      let v =
        Gate.check ~trips_on:[ "var-cap-2"; "var-cap" ] graph
          ~fills:[ fill aapl 10.0 150.0 ]
      in
      Alcotest.check outcome "both, in book order"
        (Gate.Outcome.Refused_unevaluable [ "var-cap"; "var-cap-2" ])
        v.Gate.Verdict.outcome)
    ()

(* A breach created outranks the unevaluable refusal in the outcome, and the
   reasons say both: +100 AAPL takes TECH to 105,000 with var-cap unknown. *)
let test_a_breach_and_an_unevaluable_trip_limit_both_give_reasons () =
  with_warming_book
    ~f:(fun graph ->
      let v = Gate.check ~trips_on:[ "var-cap" ] graph ~fills:[ fill aapl 100.0 150.0 ] in
      Alcotest.check outcome "refused for the breach" Gate.Outcome.Refused_breach
        v.Gate.Verdict.outcome;
      let reasons = Gate.Verdict.reasons v in
      Alcotest.(check (pair bool bool))
        "tech-cap and var-cap both named" (true, true)
        ( List.exists reasons ~f:(String.is_substring ~substring:"tech-cap"),
          List.exists reasons
            ~f:(String.is_substring ~substring:"var-cap cannot be evaluated") ))
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
             (not (Gate.Verdict.passed v))
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

(* [waive_exposure] (Task 1.9, round 1: a stop's flatten) lets past a breach
   of an exposure limit and of nothing else. One name, flat; a buy of 100 at
   150 is 15,000 of gross over a 10,000 book-cap, and 0.02 x 15,000 = 300 of
   VaR over a $1 var-cap. Waived, book-cap is reported in [waived] and passes;
   var-cap still refuses, and without the word book-cap refuses too. *)
let test_waive_exposure_waives_exposure_and_nothing_else () =
  let spy = Symbol.of_string "SPY" in
  let run limits ~waive =
    let graph =
      Graph.create ~starting_cash:(money 1_000_000.0)
        ~instruments:[ { Instrument.symbol = spy; sector = Sector.of_string "INDEX" } ]
        ~limits ~confidence:0.95 ~return_window:10 ()
    in
    Exn.protect
      ~finally:(fun () -> Graph.destroy graph)
      ~f:(fun () ->
        Graph.set_price graph spy (Price.of_float 150.0);
        Graph.set_returns graph spy
          [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
        Graph.stabilize graph;
        Gate.check ~waive_exposure:waive graph ~fills:[ fill spy 100.0 150.0 ])
  in
  let book_cap = limit "book-cap" Limit.Portfolio 10_000.0 in
  let var_cap =
    {
      Limit.name = "var-cap";
      scope = Limit.Portfolio;
      kind = Limit.Value_at_risk (money 1.0);
    }
  in
  let v = run [ book_cap ] ~waive:true in
  Alcotest.(check bool) "book-cap waived: passes" true (Gate.Verdict.passed v);
  Alcotest.(check (list string))
    "reported as waived" [ "book-cap" ] (names v.Gate.Verdict.waived);
  Alcotest.(check (list string)) "not as created" [] (names v.Gate.Verdict.created);
  let v = run [ book_cap ] ~waive:false in
  Alcotest.(check (list string))
    "unwaived, created" [ "book-cap" ]
    (names v.Gate.Verdict.created);
  Alcotest.(check bool) "and refused" false (Gate.Verdict.passed v);
  (* Round 1b: an unevaluable trip-limit on a raise is waived too, and said.
     A shortfall over the whole ten-session window, nine sessions in. *)
  let es_cap =
    {
      Limit.name = "es-cap";
      scope = Limit.Portfolio;
      kind = Limit.Expected_shortfall (money 1e9);
    }
  in
  let unknown ~waive =
    let graph =
      Graph.create ~starting_cash:(money 1_000_000.0)
        ~instruments:[ { Instrument.symbol = spy; sector = Sector.of_string "INDEX" } ]
        ~limits:[ es_cap ] ~confidence:0.95 ~return_window:10 ()
    in
    Exn.protect
      ~finally:(fun () -> Graph.destroy graph)
      ~f:(fun () ->
        Graph.set_price graph spy (Price.of_float 150.0);
        Graph.set_returns graph spy
          [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01 |];
        Graph.stabilize graph;
        Gate.check ~trips_on:[ "es-cap" ] ~waive_exposure:waive graph
          ~fills:[ fill spy 100.0 150.0 ])
  in
  let v = unknown ~waive:false in
  Alcotest.(check bool)
    "an unknown trip-limit refuses a raise" true
    (Gate.Outcome.equal v.Gate.Verdict.outcome
       (Gate.Outcome.Refused_unevaluable [ "es-cap" ]));
  let v = unknown ~waive:true in
  Alcotest.(check bool) "waived: passes" true (Gate.Verdict.passed v);
  Alcotest.(check (list string))
    "es-cap reported as waived" [ "es-cap" ] (names v.Gate.Verdict.waived);
  Alcotest.(check (list string))
    "and not as an unknown trip" [] v.Gate.Verdict.unknown_trips;
  let v = run [ book_cap; var_cap ] ~waive:true in
  Alcotest.(check (list string))
    "var-cap is not exposure: still created" [ "var-cap" ]
    (names v.Gate.Verdict.created);
  Alcotest.(check bool) "so refused" false (Gate.Verdict.passed v)

(* [reductions_at_mark] (Task 1.12): a reduction's Max_drawdown is judged with
   its fills at the mark. SPY 1,000 at 100 and 900,000 cash is an equity of
   1,000,000 against a peak of 1,010,000: a drawdown of 0.990%, under a 1%
   dd-cap. Selling all 1,000 at 99 costs 1,000: 0.990% -> 1.089%, a breach the
   sale creates when valued at its price, and none at the mark. With the peak
   at 1,020,000 the cap is already breached (1.96%), and the same sale takes
   it further over at its price and leaves it where it was at the mark. The
   flag moves the drawdown only: the verdict's equity_after is still the
   price's. *)
let test_a_reduction's_drawdown_is_judged_at_the_mark () =
  let spy = Symbol.of_string "SPY" in
  let dd_cap =
    { Limit.name = "dd-cap"; scope = Limit.Portfolio; kind = Limit.Max_drawdown 0.01 }
  in
  let run ~peak ~at_mark fills =
    let graph =
      Graph.create ~starting_cash:(money 900_000.0)
        ~instruments:[ { Instrument.symbol = spy; sector = Sector.of_string "INDEX" } ]
        ~limits:[ dd_cap ] ~confidence:0.95 ~return_window:10 ()
    in
    Exn.protect
      ~finally:(fun () -> Graph.destroy graph)
      ~f:(fun () ->
        Graph.set_price graph spy (Price.of_float 100.0);
        Graph.set_qty graph spy (Qty.of_float 1000.0);
        Graph.set_returns graph spy
          [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
        Graph.set_equity_history graph [| peak |];
        Graph.stabilize graph;
        Gate.check ~reductions_at_mark:at_mark graph ~fills)
  in
  let sale = [ fill spy (-1000.0) 99.0 ] in
  let v = run ~peak:1_010_000.0 ~at_mark:false sale in
  Alcotest.(check (pair bool (list string)))
    "at its price: the sale's cost creates the breach" (false, [ "dd-cap" ])
    (Gate.Verdict.passed v, names v.Gate.Verdict.created);
  let v = run ~peak:1_010_000.0 ~at_mark:true sale in
  Alcotest.(check bool) "at the mark: passes" true (Gate.Verdict.passed v);
  Alcotest.(check (float 1e-6))
    "equity_after is still the price's" 999_000.0
    (Notional.to_float v.Gate.Verdict.equity_after);
  let v = run ~peak:1_020_000.0 ~at_mark:false sale in
  Alcotest.(check (pair bool (list string)))
    "breached already: at its price, further over" (false, [ "dd-cap" ])
    (Gate.Verdict.passed v, names v.Gate.Verdict.worsened);
  let v = run ~peak:1_020_000.0 ~at_mark:true sale in
  Alcotest.(check bool) "at the mark: not worsened, passes" true (Gate.Verdict.passed v)

let suite =
  ( "gate",
    [
      Alcotest.test_case "a reduction's drawdown is judged at the mark" `Quick
        test_a_reduction's_drawdown_is_judged_at_the_mark;
      Alcotest.test_case "waive_exposure waives exposure and nothing else" `Quick
        test_waive_exposure_waives_exposure_and_nothing_else;
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
      Alcotest.test_case "an empty base is the check without one" `Quick
        test_an_empty_base_is_the_check_without_one;
      Alcotest.test_case "a base that breaches and a proposal that reduces it passes"
        `Quick test_a_base_that_breaches_and_a_proposal_that_reduces_it_passes;
      Alcotest.test_case "a base that breaches and a proposal that worsens it fails"
        `Quick test_a_base_that_breaches_and_a_proposal_that_worsens_it_fails;
      Alcotest.test_case "a clear base and a proposal that breaches on it fails" `Quick
        test_a_clear_base_and_a_proposal_that_breaches_on_it_fails;
      Alcotest.test_case "an unevaluable trip-limit refuses a gross-raising proposal"
        `Quick test_an_unevaluable_trip_limit_refuses_a_gross_raising_proposal;
      Alcotest.test_case "the same with a gross-lowering proposal passes" `Quick
        test_the_same_with_a_gross_lowering_proposal_passes;
      Alcotest.test_case "equal gross is not a raise, and a swap that raises it is" `Quick
        test_equal_gross_is_not_a_raise_and_a_swap_that_raises_it_is;
      Alcotest.test_case "an unevaluable limit that is not a trip-limit passes as before"
        `Quick test_an_unevaluable_limit_that_is_not_a_trip_limit_passes_as_before;
      Alcotest.test_case "the refusal names the limits in the order they are configured"
        `Quick test_the_refusal_names_the_limits_in_the_order_they_are_configured;
      Alcotest.test_case "a breach and an unevaluable trip-limit both give reasons" `Quick
        test_a_breach_and_an_unevaluable_trip_limit_both_give_reasons;
      prop_the_gate_never_moves_the_live_book;
      prop_what_the_gate_passes_creates_no_breach_when_traded;
    ] )
