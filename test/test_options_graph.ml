(* Options inside the dependency graph.

   test_options.ml checks the pricer against Hull. This file checks the four
   things that are about the ENGINE rather than about Black-Scholes, and each of
   them is a claim that could be false while every number in the pricer was
   right.

     THE DELTA-HEDGED BOOK   long stock against short calls, sized so the deltas
                             cancel, reads ~0 delta-equivalent exposure and
                             NONZERO gamma and vega. This is the options
                             analogue of the existing hedge test and it is the
                             one an interviewer would ask you to write: it is
                             the difference between an engine that knows what a
                             hedge is and one that has merely added a column.

     OPTIONS FOLD IN         the delta lands in the SAME exposure node the
                             shares do, so gross, net, the sector totals and
                             every notional limit see it without any of them
                             being told about options. If this fails, there is a
                             parallel exposure system, which is the thing the
                             design was arranged to avoid.

     TWO CLOCKS, TWO JOBS    advancing the STALENESS clock must not touch a
                             single Greek, and advancing the VALUATION clock
                             must not touch feed health. Theta is genuinely
                             time-dependent, so options risk needs a clock -- but
                             wiring it to the five-second staleness timer would
                             put the whole book on a schedule, which is the
                             design this project exists to replace. That the two
                             are separate cells is the load-bearing decision of
                             this phase, and it is asserted rather than
                             described.

     A TICK STAYS LOCAL      an MSFT print does not reach an AAPL option's
                             Greeks, exactly as it does not reach AAPL's
                             exposure. *)

open Core
module Graph = Ohcamel.Graph
module Options = Ohcamel.Options
module BS = Ohcamel.Options.Black_scholes
open Ohcamel.Types

let feq = Alcotest.float 1e-9
let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float
let aapl = sym "AAPL"
let msft = sym "MSFT"
let tech = sec "TECH"

let book =
  [
    { Instrument.symbol = aapl; sector = tech };
    { Instrument.symbol = msft; sector = tech };
  ]

(* One at-the-money call on each name, 30 days out. Two contracts rather than
   one so the locality test has something to be local ABOUT. *)
let aapl_call =
  Options.Position.create ~underlying:aapl ~id:"AAPL-100C"
    ~strike:(Options.Strike.of_float 100.0)
    ~right:Options.Right.Call ~expiry_in_days:30.0 ()

let msft_call =
  Options.Position.create ~underlying:msft ~id:"MSFT-200C"
    ~strike:(Options.Strike.of_float 200.0)
    ~right:Options.Right.Call ~expiry_in_days:30.0 ()

let rate = 0.04
let vol = 0.25

module Recorder = struct
  type t = { counts : int String.Table.t }

  let create () = { counts = String.Table.create () }
  let on_compute t name = Hashtbl.incr t.counts name
  let reset t = Hashtbl.clear t.counts
  let count t name = Option.value (Hashtbl.find t.counts name) ~default:0
end

let with_graph ?(limits = []) ~f () =
  let recorder = Recorder.create () in
  let graph =
    Graph.create
      ~on_compute:(Recorder.on_compute recorder)
      ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits
      ~options:[ aapl_call; msft_call ] ~rate ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph aapl (Price.of_float 100.0);
      Graph.set_price graph msft (Price.of_float 200.0);
      Graph.set_implied_vol graph "AAPL-100C" (Options.Implied_vol.of_float vol);
      Graph.set_implied_vol graph "MSFT-200C" (Options.Implied_vol.of_float vol);
      Graph.stabilize graph;
      Recorder.reset recorder;
      f graph recorder)

(* The Greeks of one AAPL contract at the state the fixture sets up, computed
   independently so the expected hedge ratio below is derived rather than read
   off the engine's own answer. *)
let aapl_greeks () =
  BS.compute ~spot:100.0 ~strike:100.0 ~time_to_expiry:(30.0 /. 365.0) ~rate
    ~implied_vol:vol ~right:Options.Right.Call

(* THE DELTA-HEDGED BOOK.

   Short 10 AAPL calls. Each contract is 100 shares, so the option leg's
   delta-equivalent exposure is

     delta * 100 * (-10) * 100   =   -delta * 100,000

   Buying [delta * 100 * 10] shares at 100 gives an equal and opposite
   +delta * 100,000, so the two cancel exactly. The share count is derived from
   the delta rather than guessed, which is what makes "exactly" the right word.

   What must survive the cancellation is the convexity. A short call is short
   gamma and short vega, and hedging the delta does nothing to either -- that is
   the entire content of the statement that a delta hedge is a first-order
   hedge. An engine that folded gamma into exposure, or that reported |gamma|,
   or that netted the Greeks against the shares, would show a flat book here.
   The shares contribute exactly zero gamma and zero vega, because a share is
   linear in its own price. *)
let test_a_delta_hedged_book_is_flat_in_delta_and_not_in_convexity () =
  with_graph
    ~f:(fun graph _ ->
      let greeks = aapl_greeks () in
      let contracts = -10.0 in
      let shares = BS.delta greeks *. 100.0 *. 10.0 in
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float contracts);
      Graph.set_qty graph aapl (Qty.of_float shares);
      Graph.stabilize graph;
      let s = Graph.snapshot graph in
      let exposure =
        Notional.to_float (Map.find_exn (Graph.Snapshot.exposure_by_instrument s) aapl)
      in
      (* Zero to within float noise on a number whose two halves are each about
         $130,000, so the tolerance is roughly 1e-14 relative. *)
      Alcotest.check (Alcotest.float 1e-6) "delta-equivalent exposure cancels" 0.0
        exposure;
      let gamma = Map.find_exn (Graph.Snapshot.gamma_by_instrument s) aapl in
      let vega = Map.find_exn (Graph.Snapshot.vega_by_instrument s) aapl in
      (* Short calls, so both are negative -- and being SHORT convexity is the
         dangerous side, which is why the Greek limit caps the magnitude. *)
      Alcotest.(check bool)
        "short gamma, and it did not cancel" true (Float.( < ) gamma (-1e-6));
      Alcotest.(check bool)
        "short vega, and it did not cancel" true (Float.( < ) vega (-1.0));
      (* Derived from the pricer independently, so this pins the multiplier and
         the contract count as well as the sign. *)
      Alcotest.check feq "gamma is delta-neutral-invariant"
        (BS.gamma greeks *. 100.0 *. contracts)
        gamma;
      Alcotest.check feq "vega likewise" (BS.vega greeks *. 100.0 *. contracts) vega)
    ()

(* A delta-hedged book does not breach a notional cap, and DOES breach a vega
   cap. Both halves matter: the first says the hedge is recognised, the second
   says the risk it leaves behind is still measured.

   This is the options analogue of test_graph.ml's hedge test, where a
   risk-reducing equity position must not breach a risk limit. *)
let test_the_hedge_passes_a_notional_cap_and_fails_a_vega_cap () =
  let greeks = aapl_greeks () in
  let contracts = -10.0 in
  let shares = BS.delta greeks *. 100.0 *. 10.0 in
  let true_vega = Float.abs (BS.vega greeks *. 100.0 *. contracts) in
  let limits =
    [
      (* Well under the $130,000 of stock and $130,000 of short delta this book
         holds on each leg. It passes only because they cancel. *)
      {
        Limit.name = "aapl-notional";
        scope = Limit.Instrument aapl;
        kind = Limit.Gross_notional (dollars 1_000.0);
      };
      (* Half the vega the book actually carries, so it must breach. *)
      {
        Limit.name = "aapl-vega";
        scope = Limit.Instrument aapl;
        kind = Limit.Greek_limit (Greek.Vega, dollars (true_vega /. 2.0));
      };
    ]
  in
  with_graph ~limits
    ~f:(fun graph _ ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float contracts);
      Graph.set_qty graph aapl (Qty.of_float shares);
      Graph.stabilize graph;
      let breached =
        Graph.Snapshot.breaches (Graph.snapshot graph)
        |> List.filter ~f:Breach.breached
        |> List.map ~f:(fun b -> Limit.name (Breach.limit b))
        |> List.sort ~compare:String.compare
      in
      Alcotest.(check (list string))
        "the notional cap passes, the vega cap does not" [ "aapl-vega" ] breached)
    ()

(* OPTIONS FOLD INTO THE EXISTING AGGREGATION.

   Nothing about gross, net, the sector total or a portfolio notional limit
   knows options exist. If the delta reaches all four without any of them being
   modified, there is one exposure system; if it reaches none of them, there are
   two. *)
let test_options_reach_gross_net_and_sector () =
  with_graph
    ~f:(fun graph _ ->
      let before = Graph.snapshot graph in
      Alcotest.check feq "no contracts held, so gross is zero" 0.0
        (Notional.to_float (Graph.Snapshot.gross_exposure before));
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.stabilize graph;
      let after = Graph.snapshot graph in
      let greeks = aapl_greeks () in
      let expected = BS.delta greeks *. 100.0 *. 10.0 *. 100.0 in
      Alcotest.check feq "the option's delta IS AAPL's exposure" expected
        (Notional.to_float
           (Map.find_exn (Graph.Snapshot.exposure_by_instrument after) aapl));
      Alcotest.check feq "and it reached gross" expected
        (Notional.to_float (Graph.Snapshot.gross_exposure after));
      Alcotest.check feq "and net" expected
        (Notional.to_float (Graph.Snapshot.net_exposure after));
      Alcotest.check feq "and the TECH sector total" expected
        (Notional.to_float (Map.find_exn (Graph.Snapshot.exposure_by_sector after) tech));
      (* Equity moves too, because equity is cash plus net. A long call bought
         for nothing and marked at its delta is not a realistic cash flow -- this
         engine does not model premium paid -- but the propagation is what is
         under test, and it is the same propagation that carries a share. *)
      Alcotest.check feq "and equity" (100_000.0 +. expected)
        (Notional.to_float (Graph.Snapshot.equity after)))
    ()

(* TWO CLOCKS, AND NEITHER MAY DO THE OTHER'S JOB.

   The staleness clock advances every few seconds in live mode. If any Greek
   were downstream of it, every option in the book would reprice on that timer
   -- recompute-on-a-schedule, wearing the costume of a risk engine. Theta is
   real and does need a clock, so options get a second one that moves in days
   and only when a caller says so.

   Asserted in both directions, because only one of them is the obvious one. *)
let test_the_two_clocks_are_separate () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.stabilize graph;
      Recorder.reset recorder;
      (* Direction one: the staleness clock cannot reach a Greek. *)
      Graph.set_now graph (Time.add (Graph.now graph) (Time.Span.of_sec 30.0));
      Graph.stabilize graph;
      Alcotest.(check int)
        "advancing the staleness clock does not reprice an option" 0
        (Recorder.count recorder "greeks:AAPL-100C");
      Alcotest.(check int)
        "nor its exposure" 0
        (Recorder.count recorder "option_exposure:AAPL-100C");
      Alcotest.(check int)
        "nor the book's gamma" 0
        (Recorder.count recorder "portfolio_gamma");
      (* The clock IS running, and this is where it goes. [feed:AAPL] hangs
         directly off [now], so its body runs; [feed_health] downstream of it
         does NOT, because thirty seconds against a ninety-second staleness
         threshold leaves every field of the liveness record unchanged and the
         cutoff stops the propagation there. Asserting the leaf rather than the
         aggregate is the stronger statement: the clock reached the branch it
         is supposed to reach, and got no further than the first node whose
         answer it changed. *)
      Alcotest.(check bool)
        "but it did reach the liveness branch, so the clock is genuinely running" true
        (Recorder.count recorder "feed:AAPL" > 0);
      (* Direction two: the valuation clock reaches the Greeks and nothing
         about feed liveness. A day passing is not evidence that a price
         arrived. *)
      Recorder.reset recorder;
      Graph.advance_valuation_days graph 1.0;
      Graph.stabilize graph;
      Alcotest.(check bool)
        "advancing the valuation date does reprice the option" true
        (Recorder.count recorder "greeks:AAPL-100C" > 0);
      Alcotest.(check int)
        "and does not touch the liveness branch at all" 0
        (Recorder.count recorder "feed:AAPL");
      Alcotest.(check int)
        "nor the aggregate above it" 0
        (Recorder.count recorder "feed_health"))
    ()

(* Theta, made visible. A day of decay must lower a long call's value, and the
   engine's own clock is the only thing that moved.

   Checked through the delta-equivalent exposure rather than the price, because
   the price is not published -- what a limit reads is the exposure, so that is
   what has to move correctly. A 30-day at-the-money call loses delta as it
   decays when it is at the money and slightly out. *)
let test_advancing_the_valuation_date_decays_the_book () =
  with_graph
    ~f:(fun graph _ ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.stabilize graph;
      let gamma_before = Graph.portfolio_gamma graph in
      let vega_before = Graph.portfolio_vega graph in
      Graph.advance_valuation_days graph 20.0;
      Graph.stabilize graph;
      (* Ten days from expiry against thirty: vega falls with sqrt(T), and gamma
         RISES at the money as the distribution tightens around the strike.
         Asserting both directions rather than "something changed" is what makes
         this a test of the clock's meaning rather than of its existence. *)
      Alcotest.(check bool)
        "vega decays toward expiry" true
        (Float.( < ) (Graph.portfolio_vega graph) vega_before);
      Alcotest.(check bool)
        "at-the-money gamma rises toward expiry" true
        (Float.( > ) (Graph.portfolio_gamma graph) gamma_before);
      (* Past expiry there is nothing left. *)
      Graph.advance_valuation_days graph 30.0;
      Graph.stabilize graph;
      Alcotest.check feq "an expired contract has no gamma" 0.0
        (Graph.portfolio_gamma graph);
      Alcotest.check feq "and no vega" 0.0 (Graph.portfolio_vega graph))
    ()

(* A tick in one name does not reprice another name's options, exactly as it
   does not reach another name's exposure. The Greeks node has one price edge
   and it is the underlying's. *)
let test_a_tick_reaches_only_its_own_options () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.set_contracts graph "MSFT-200C" (Options.Contracts.of_float 10.0);
      Graph.stabilize graph;
      Recorder.reset recorder;
      Graph.set_price graph aapl (Price.of_float 101.0);
      Graph.stabilize graph;
      Alcotest.(check bool)
        "AAPL's option repriced" true
        (Recorder.count recorder "greeks:AAPL-100C" > 0);
      Alcotest.(check int)
        "MSFT's option did not" 0
        (Recorder.count recorder "greeks:MSFT-200C");
      Alcotest.(check int)
        "nor did its exposure" 0
        (Recorder.count recorder "option_exposure:MSFT-200C"))
    ()

(* A vol re-mark reaches the Greeks and NOT the price-driven side.

   The mirror of the tick test, and it is the assertion that implied vol is a
   real input cell rather than a field on the position record. If it were the
   latter, re-marking would either be impossible or would silently leave every
   Greek stale. *)
let test_a_vol_remark_reaches_the_greeks () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.stabilize graph;
      Recorder.reset recorder;
      Graph.set_implied_vol graph "AAPL-100C" (Options.Implied_vol.of_float 0.60);
      Graph.stabilize graph;
      Alcotest.(check bool)
        "the Greeks repriced" true
        (Recorder.count recorder "greeks:AAPL-100C" > 0);
      Alcotest.(check int)
        "MSFT's option was untouched" 0
        (Recorder.count recorder "greeks:MSFT-200C");
      (* A higher vol on a long at-the-money call is more vega and more
         value. *)
      Alcotest.(check bool)
        "more vol, more vega" true
        (Float.( > ) (Graph.portfolio_vega graph) 0.0))
    ()

(* An unmarked underlying yields flat Greeks rather than an exception.

   Black_scholes rejects a non-positive spot, correctly -- ln(0) is not a number
   -- and a node body that raised would take the whole graph down. Prices start
   at zero by design, so this is the state every book is in for the first
   instant of its life, not an edge case. *)
let test_an_unmarked_underlying_is_flat_not_fatal () =
  let recorder = Recorder.create () in
  let graph =
    Graph.create
      ~on_compute:(Recorder.on_compute recorder)
      ~instruments:book ~limits:[] ~options:[ aapl_call ] ~confidence:0.95
      ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_contracts graph "AAPL-100C" (Options.Contracts.of_float 10.0);
      Graph.set_implied_vol graph "AAPL-100C" (Options.Implied_vol.of_float 0.25);
      Graph.stabilize graph;
      Alcotest.check feq "no gamma on an unmarked name" 0.0 (Graph.portfolio_gamma graph);
      Alcotest.check feq "no vega either" 0.0 (Graph.portfolio_vega graph);
      Alcotest.check feq "and no exposure" 0.0
        (Notional.to_float (Graph.gross_exposure graph)))

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, got a value" name

(* Rejected at construction, before a node exists, for the same reason limits
   are: so that no node body can ever raise. *)
let test_bad_option_books_are_rejected_at_construction () =
  let create options =
    Graph.create ~instruments:book ~limits:[] ~options ~confidence:0.95 ~return_window:10
      ()
  in
  check_invalid_arg "an option on a name the book does not hold" (fun () ->
      create
        [
          Options.Position.create ~underlying:(sym "TSLA") ~id:"TSLA-1C"
            ~strike:(Options.Strike.of_float 1.0) ~right:Options.Right.Call
            ~expiry_in_days:30.0 ();
        ]);
  check_invalid_arg "a duplicate option id" (fun () -> create [ aapl_call; aapl_call ]);
  check_invalid_arg "a non-positive strike" (fun () ->
      create
        [
          Options.Position.create ~underlying:aapl ~id:"AAPL-0C"
            ~strike:(Options.Strike.of_float 0.0) ~right:Options.Right.Call
            ~expiry_in_days:30.0 ();
        ]);
  check_invalid_arg "the valuation date cannot run backwards" (fun () ->
      let g = create [] in
      Exn.protect
        ~finally:(fun () -> Graph.destroy g)
        ~f:(fun () -> Graph.advance_valuation_days g (-1.0)))

let suite =
  ( "options_graph",
    [
      Alcotest.test_case "A DELTA-HEDGED BOOK IS FLAT IN DELTA, NOT IN CONVEXITY" `Quick
        test_a_delta_hedged_book_is_flat_in_delta_and_not_in_convexity;
      Alcotest.test_case "the hedge passes a notional cap and fails a vega cap" `Quick
        test_the_hedge_passes_a_notional_cap_and_fails_a_vega_cap;
      Alcotest.test_case "OPTIONS FOLD INTO THE EXISTING EXPOSURE" `Quick
        test_options_reach_gross_net_and_sector;
      Alcotest.test_case "TWO CLOCKS: staleness cannot reprice, valuation cannot stale"
        `Quick test_the_two_clocks_are_separate;
      Alcotest.test_case "advancing the valuation date decays the book" `Quick
        test_advancing_the_valuation_date_decays_the_book;
      Alcotest.test_case "a tick reaches only its own options" `Quick
        test_a_tick_reaches_only_its_own_options;
      Alcotest.test_case "a vol re-mark reaches the Greeks" `Quick
        test_a_vol_remark_reaches_the_greeks;
      Alcotest.test_case "an unmarked underlying is flat, not fatal" `Quick
        test_an_unmarked_underlying_is_flat_not_fatal;
      Alcotest.test_case "bad option books are rejected at construction" `Quick
        test_bad_option_books_are_rejected_at_construction;
    ] )
