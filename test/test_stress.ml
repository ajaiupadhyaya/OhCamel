(* Unit tests for stress.ml.

   The same three-name book as test_graph.ml, because its numbers are already
   hand-derived there and a scenario's expected P&L is then arithmetic anyone
   can check in their head:

     AAPL  150.00 x  200 = +30,000
     MSFT  300.00 x  100 = +30,000
     XOM   100.00 x -400 = -40,000
     cash 100,000, so equity = 100,000 + 20,000 = 120,000

   A uniform -10% takes every price down a tenth, so net exposure goes from
   +20,000 to +18,000 and equity to 118,000: a loss of exactly 2,000. The short
   leg is what makes that interesting -- it GAINS 4,000 while the longs lose
   6,000 -- and it is why a stress suite that only shocks downward would give
   this book a clean bill of health it does not deserve.

   The tests that matter most here are not the arithmetic ones. They are:

     the isolation test, which asserts the live graph is byte-for-byte unchanged
     after a scenario runs. A stress engine that leaves the book in the
     scenario's world is not a bug that shows up as a wrong number -- it shows
     up as a correct number about the wrong world.

     the separation test, which asserts that a price shock does NOT move the VaR
     fraction and a volatility shock does not move equity. Those are the two
     halves of what a scenario can mean, and conflating them is how a suite ends
     up answering a question nobody asked. *)

open Core
module Graph = Ohcamel.Graph
module Stress = Ohcamel.Stress
module Shock = Ohcamel.Stress.Shock
module Scenario = Ohcamel.Stress.Scenario
module Outcome = Ohcamel.Stress.Outcome
open Ohcamel.Types

let float_eq = Alcotest.float 1e-9
let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float
let aapl = sym "AAPL"
let msft = sym "MSFT"
let xom = sym "XOM"
let tech = sec "TECH"
let energy = sec "ENERGY"

let book =
  [
    { Instrument.symbol = aapl; sector = tech };
    { Instrument.symbol = msft; sector = tech };
    { Instrument.symbol = xom; sector = energy };
  ]

let limit name scope kind = { Limit.name; scope; kind }
let base_returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]
let negated_returns = Array.map base_returns ~f:Float.neg

(* A factor that moves with the TECH names and against XOM, so a factor shock
   has different signs for different instruments and the betas are checkable:
   the factor IS [base_returns], so beta(AAPL) = beta(MSFT) = 1 and
   beta(XOM) = -1 exactly. *)
let factor_returns = base_returns

let default_limits =
  [
    limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 150_000.0));
    limit "aapl-cap" (Limit.Instrument aapl) (Limit.Gross_notional (dollars 28_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 6_000.0));
  ]

let with_book ?(limits = default_limits) ~f () =
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_price graph msft (Price.of_float 300.0);
      Graph.set_price graph xom (Price.of_float 100.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      Graph.set_qty graph msft (Qty.of_float 100.0);
      Graph.set_qty graph xom (Qty.of_float (-400.0));
      Graph.set_returns graph aapl base_returns;
      Graph.set_returns graph msft base_returns;
      Graph.set_returns graph xom negated_returns;
      Graph.set_factor_returns graph factor_returns;
      Graph.stabilize graph;
      Graph.mark_equity graph;
      Graph.stabilize graph;
      f graph)

let scenario name shocks = Scenario.create ~name ~description:"" shocks
let equity_of s = Notional.to_float (Graph.Snapshot.equity s)

(* -0.10 on everything.

     AAPL 135 x  200 =  27,000   (-3,000)
     MSFT 270 x  100 =  27,000   (-3,000)
     XOM   90 x -400 = -36,000   (+4,000)
     net   = 18,000, equity = 118,000, P&L = -2,000

   The short leg makes 4,000 in a selloff. A book like this does not lose 10%
   in a 10% selloff and the whole reason to compute a scenario rather than
   multiply by a beta is that nobody can do that in their head reliably. *)
let test_broad_selloff () =
  with_book
    ~f:(fun graph ->
      let o = Stress.run ~graph ~scenario:(scenario "down10" [ Shock.All (-0.10) ]) in
      Alcotest.check float_eq "P&L" (-2_000.0) (Notional.to_float (Outcome.pnl o));
      Alcotest.check float_eq "equity after" 118_000.0 (equity_of (Outcome.after o));
      Alcotest.check float_eq "as a fraction of starting equity" (-2_000.0 /. 120_000.0)
        (Outcome.pnl_fraction o);
      (* Gross falls with prices: 27,000 + 27,000 + 36,000 = 90,000. *)
      Alcotest.check float_eq "gross after" 90_000.0
        (Notional.to_float (Graph.Snapshot.gross_exposure (Outcome.after o))))
    ()

(* The mirror: a 10% rally costs this book money, because the short leg loses
   more than the two longs make in absolute terms... except it does not, and
   that is the point of running it. Longs gain 6,000, the short loses 4,000,
   net +2,000. Symmetric to the selloff only because the shock is uniform and
   proportional; the test exists so that a future asymmetric shock has a
   symmetric baseline to be compared against. *)
let test_a_rally_is_also_a_scenario () =
  with_book
    ~f:(fun graph ->
      let o = Stress.run ~graph ~scenario:(scenario "up10" [ Shock.All 0.10 ]) in
      Alcotest.check float_eq "P&L" 2_000.0 (Notional.to_float (Outcome.pnl o)))
    ()

(* Shocks add. TECH takes the broad move and its own on top; ENERGY takes only
   the broad one.

     broad -10%, TECH -10% more  ->  AAPL and MSFT at -20%, XOM at -10%
     AAPL 120 x  200 =  24,000   (-6,000)
     MSFT 240 x  100 =  24,000   (-6,000)
     XOM   90 x -400 = -36,000   (+4,000)
     P&L = -8,000 *)
let test_shocks_compose_additively () =
  with_book
    ~f:(fun graph ->
      let o =
        Stress.run ~graph
          ~scenario:
            (scenario "tech-led" [ Shock.All (-0.10); Shock.Sector (tech, -0.10) ])
      in
      Alcotest.check float_eq "P&L" (-8_000.0) (Notional.to_float (Outcome.pnl o));
      let after = Graph.Snapshot.exposure_by_instrument (Outcome.after o) in
      Alcotest.check float_eq "AAPL took both shocks" 24_000.0
        (Notional.to_float (Map.find_exn after aapl));
      Alcotest.check float_eq "XOM took only the broad one" (-36_000.0)
        (Notional.to_float (Map.find_exn after xom)))
    ()

(* A single-name shock leaves everything else exactly where it was.

     AAPL -50%: 75 x 200 = 15,000, a loss of 15,000. Nothing else moves. *)
let test_instrument_shock_is_local () =
  with_book
    ~f:(fun graph ->
      let o =
        Stress.run ~graph
          ~scenario:(scenario "aapl-halved" [ Shock.Instrument (aapl, -0.50) ])
      in
      Alcotest.check float_eq "P&L" (-15_000.0) (Notional.to_float (Outcome.pnl o));
      let after = Graph.Snapshot.exposure_by_instrument (Outcome.after o) in
      Alcotest.check float_eq "AAPL halved" 15_000.0
        (Notional.to_float (Map.find_exn after aapl));
      Alcotest.check float_eq "MSFT untouched" 30_000.0
        (Notional.to_float (Map.find_exn after msft));
      Alcotest.check float_eq "XOM untouched" (-40_000.0)
        (Notional.to_float (Map.find_exn after xom)))
    ()

(* A factor shock is not a uniform shock. Each name moves by its own beta.

   The factor series here IS [base_returns], and AAPL and MSFT carry that series
   while XOM carries its negation, so:

     beta(AAPL) = cov(r, r) / var(r) =  1
     beta(MSFT) =                       1
     beta(XOM)  = cov(-r, r) / var(r) = -1

   A +10% factor move therefore takes the TECH names up 10% and XOM DOWN 10%:

     AAPL 165 x  200 =  33,000   (+3,000)
     MSFT 330 x  100 =  33,000   (+3,000)
     XOM   90 x -400 = -36,000   (+4,000)
     P&L = +10,000

   Every leg makes money, which no uniform shock can produce on a book with a
   short. That is the whole argument for expressing a macro move as a factor
   rather than as a price move. *)
let test_factor_shock_uses_each_name_s_own_beta () =
  with_book
    ~f:(fun graph ->
      let o = Stress.run ~graph ~scenario:(scenario "factor-up" [ Shock.Factor 0.10 ]) in
      Alcotest.check float_eq "P&L" 10_000.0 (Notional.to_float (Outcome.pnl o));
      let after = Graph.Snapshot.exposure_by_instrument (Outcome.after o) in
      Alcotest.check float_eq "AAPL follows the factor" 33_000.0
        (Notional.to_float (Map.find_exn after aapl));
      Alcotest.check float_eq "XOM moves against it" (-36_000.0)
        (Notional.to_float (Map.find_exn after xom));
      Alcotest.(check (list string))
        "every beta was estimable" []
        (List.map (Outcome.unestimated_betas o) ~f:Symbol.to_string))
    ()

(* A factor that does not move makes every beta undefined, so nothing moves --
   and the names it happened to are reported rather than silently sitting
   still. "This name did not move" and "this name could not be moved" look
   identical in a P&L table and mean opposite things. *)
let test_an_unestimable_factor_is_reported_not_hidden () =
  with_book
    ~f:(fun graph ->
      Graph.set_factor_returns graph (Array.create ~len:10 0.02);
      Graph.stabilize graph;
      let o =
        Stress.run ~graph ~scenario:(scenario "flat-factor" [ Shock.Factor 0.10 ])
      in
      Alcotest.check float_eq "nothing moved" 0.0 (Notional.to_float (Outcome.pnl o));
      Alcotest.(check (list string))
        "and every name is named" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Outcome.unestimated_betas o) ~f:Symbol.to_string))
    ()

(* THE SEPARATION. A price shock moves the book's value and not the risk
   estimate; a volatility shock moves the risk estimate and not the book's
   value.

   Both halves are asserted here because each on its own is easy to satisfy by
   accident, and together they say the two kinds of scenario are answering two
   different questions.

   The VaR FRACTION is the quantity to check for the price shock, not the dollar
   VaR: the dollar figure is the fraction times gross, and gross genuinely
   moved. Checking the dollars would fail for the right reason and look like the
   wrong one. *)
let test_a_price_shock_does_not_move_the_risk_estimate () =
  with_book
    ~f:(fun graph ->
      let o = Stress.run ~graph ~scenario:(scenario "down20" [ Shock.All (-0.20) ]) in
      Alcotest.(check (option (Alcotest.float 1e-12)))
        "the VaR fraction is estimated from the return window, which did not move"
        (Graph.Snapshot.historical_var (Outcome.before o))
        (Graph.Snapshot.historical_var (Outcome.after o));
      (* But the dollar VaR does move, because gross did. Asserted so that the
         line above cannot be read as "the scenario changed nothing". *)
      let dollars_before =
        Option.value_exn (Graph.Snapshot.value_at_risk_notional (Outcome.before o))
      in
      let dollars_after =
        Option.value_exn (Graph.Snapshot.value_at_risk_notional (Outcome.after o))
      in
      Alcotest.check float_eq "dollar VaR falls with gross"
        (Notional.to_float dollars_before *. 0.8)
        (Notional.to_float dollars_after))
    ()

let test_a_volatility_shock_does_not_move_the_book () =
  with_book
    ~f:(fun graph ->
      let o = Stress.run ~graph ~scenario:(scenario "vol2x" [ Shock.Volatility 2.0 ]) in
      Alcotest.check float_eq "no P&L: nothing was repriced" 0.0
        (Notional.to_float (Outcome.pnl o));
      Alcotest.check float_eq "gross unchanged"
        (Notional.to_float (Graph.Snapshot.gross_exposure (Outcome.before o)))
        (Notional.to_float (Graph.Snapshot.gross_exposure (Outcome.after o)));
      (* Historical VaR is a quantile of the return series, and scaling every
         return by two scales any quantile of it by two exactly. *)
      let before = Option.value_exn (Graph.Snapshot.historical_var (Outcome.before o)) in
      let after = Option.value_exn (Graph.Snapshot.historical_var (Outcome.after o)) in
      Alcotest.check float_eq "doubling the returns doubles the VaR" (before *. 2.0) after)
    ()

(* A scenario reports which limits it would break, and which it would relieve.

   AAPL sits at 30,000 against a 28,000 cap, so it is ALREADY breached before
   any scenario runs. A -20% shock takes it to 24,000 and clears it. A +20%
   shock takes the book's gross to 120,000... still inside the 150,000 cap, so
   nothing new breaks there; the scenario that breaks book-cap has to be larger.

   Both directions are asserted because a stress report that only lists new
   breaches quietly implies the existing ones are unaffected. *)
let test_new_and_cleared_breaches () =
  with_book
    ~f:(fun graph ->
      let before = Graph.snapshot graph in
      Alcotest.(check (list string))
        "AAPL is over its cap to begin with" [ "aapl-cap" ]
        (List.map (Graph.Snapshot.breached before) ~f:(fun b ->
             Limit.name (Breach.limit b)));
      let relief =
        Stress.run ~graph ~scenario:(scenario "down20" [ Shock.All (-0.20) ])
      in
      Alcotest.(check (list string))
        "a selloff takes AAPL back inside its cap" [ "aapl-cap" ]
        (List.map (Outcome.cleared_breaches relief) ~f:(fun b ->
             Limit.name (Breach.limit b)));
      Alcotest.(check (list string))
        "and breaks nothing new" []
        (List.map (Outcome.new_breaches relief) ~f:(fun b -> Limit.name (Breach.limit b)));
      (* Big enough to push gross past 150,000: +100% takes it to 200,000.

         It breaks the VaR cap too, and that is worth reading rather than
         asserting past. The VaR FRACTION did not move -- the return window is
         untouched, as the separation test above insists -- but the dollar VaR
         is that fraction times gross, and gross doubled. So a scenario with no
         opinion about volatility at all still breaches a risk limit, purely by
         making the book bigger. 0.05 * 200,000 = 10,000 against a 6,000 cap.

         That is the correct behaviour and it is the reason a notional cap and a
         VaR cap are not redundant with each other in one direction but are in
         the other: any pure-price scenario that breaks the notional cap by
         enough will eventually break the VaR cap as well, while a volatility
         scenario breaks the VaR cap alone. *)
      let squeeze = Stress.run ~graph ~scenario:(scenario "double" [ Shock.All 1.0 ]) in
      Alcotest.(check (list string))
        "doubling every price breaks the notional cap and, through gross, the VaR cap"
        [ "book-cap"; "var-cap" ]
        (List.map (Outcome.new_breaches squeeze) ~f:(fun b -> Limit.name (Breach.limit b))))
    ()

(* THE ISOLATION TEST.

   A scenario must not leave a trace on the live graph. This runs the whole
   standard suite -- every shock kind, several of them compounding -- and then
   asserts the engine's own snapshot is identical to what it was before. If a
   fork ever started sharing an input cell with its parent, this is what would
   catch it, and nothing else would: the live numbers would still be internally
   consistent, they would just be about a world that never happened. *)
let test_the_live_graph_is_untouched () =
  with_book
    ~f:(fun graph ->
      let before = Graph.snapshot graph in
      let outcomes = Stress.run_all ~graph ~scenarios:(Stress.suite_for ~graph) in
      Alcotest.(check bool)
        "the suite actually ran" true
        (List.length outcomes >= List.length Stress.standard);
      let after = Graph.snapshot graph in
      Alcotest.(check string)
        "the live snapshot is unchanged, field for field"
        (Sexp.to_string_hum (Graph.Snapshot.sexp_of_t before))
        (Sexp.to_string_hum (Graph.Snapshot.sexp_of_t after)))
    ()

(* The suite has to contain a scenario that is good for this book, or it is not
   a stress suite, it is a list of things the author already feared. This book
   is short energy, so an energy selloff makes money. *)
let test_the_suite_covers_both_directions () =
  with_book
    ~f:(fun graph ->
      let outcomes = Stress.run_all ~graph ~scenarios:(Stress.suite_for ~graph) in
      let pnl o = Notional.to_float (Outcome.pnl o) in
      Alcotest.(check bool)
        "some scenario loses money" true
        (List.exists outcomes ~f:(fun o -> Float.is_negative (pnl o)));
      Alcotest.(check bool)
        "and some scenario makes it" true
        (List.exists outcomes ~f:(fun o -> Float.( > ) (pnl o) 0.0));
      match Stress.worst outcomes with
      | None -> Alcotest.fail "a non-empty suite has a worst case"
      | Some w ->
          Alcotest.(check bool)
            (Printf.sprintf "the worst case is the worst case (%s, %.0f)"
               (Scenario.name (Outcome.scenario w))
               (pnl w))
            true
            (List.for_all outcomes ~f:(fun o -> Float.( >= ) (pnl o) (pnl w))))
    ()

let test_a_scenario_cannot_shock_what_the_book_does_not_hold () =
  with_book
    ~f:(fun graph ->
      let check msg shocks =
        match Stress.run ~graph ~scenario:(scenario "bad" shocks) with
        | exception Invalid_argument _ -> ()
        | exception e -> Alcotest.failf "%s: got %s" msg (Exn.to_string e)
        | _ -> Alcotest.failf "%s: expected Invalid_argument" msg
      in
      check "unknown instrument" [ Shock.Instrument (sym "NVDA", -0.1) ];
      check "sector nothing is held in" [ Shock.Sector (sec "UTILITIES", -0.1) ];
      check "non-positive volatility scale" [ Shock.Volatility 0.0 ])
    ()

let suite =
  ( "stress",
    [
      Alcotest.test_case "a broad selloff, by hand" `Quick test_broad_selloff;
      Alcotest.test_case "a rally is also a scenario" `Quick
        test_a_rally_is_also_a_scenario;
      Alcotest.test_case "shocks compose additively" `Quick test_shocks_compose_additively;
      Alcotest.test_case "a single-name shock is local" `Quick
        test_instrument_shock_is_local;
      Alcotest.test_case "a factor shock uses each name's own beta" `Quick
        test_factor_shock_uses_each_name_s_own_beta;
      Alcotest.test_case "an unestimable beta is reported, not hidden" `Quick
        test_an_unestimable_factor_is_reported_not_hidden;
      Alcotest.test_case "a price shock does not move the risk estimate" `Quick
        test_a_price_shock_does_not_move_the_risk_estimate;
      Alcotest.test_case "a volatility shock does not move the book" `Quick
        test_a_volatility_shock_does_not_move_the_book;
      Alcotest.test_case "new and cleared breaches" `Quick test_new_and_cleared_breaches;
      Alcotest.test_case "ISOLATION: the live graph is untouched" `Quick
        test_the_live_graph_is_untouched;
      Alcotest.test_case "the suite covers both directions" `Quick
        test_the_suite_covers_both_directions;
      Alcotest.test_case "a scenario cannot shock what is not held" `Quick
        test_a_scenario_cannot_shock_what_the_book_does_not_hold;
    ] )
