(* Phase 1. The test that validates the project's premise -- per the README,
   the one not to skip.

   Asserts that changing one input recomputes only the nodes downstream of it.
   Everything else in the system could work perfectly and the project would
   still have failed its purpose if this test does not hold.

   Approach: Graph.create takes an [on_compute] hook that fires once per node
   body per recomputation, carrying that node's name. A test seeds the book,
   stabilizes, clears the recorder, changes exactly one input, stabilizes again,
   and asserts on the EXACT SET of names that ran.

   Set equality, not "the right ones ran": the interesting failure is a node
   running that should not have, and only an exact comparison catches that.
   Asserting on values alone cannot distinguish a dependency graph from a full
   recompute that happens to produce the same answers.

   If this engine ever degrades into recompute-everything, these tests fail --
   which is the point. *)

open Core
module Graph = Ohcamel.Graph
module Limits = Ohcamel.Limits
module Options = Ohcamel.Options
open Ohcamel.Types

let float_eq = Alcotest.float 1e-9
let opt_float = Alcotest.(option (float 1e-9))
let sym = Symbol.of_string
let sec = Sector.of_string

(* ------------------------------------------------------------------------ *)
(* Recording which nodes recomputed                                          *)
(* ------------------------------------------------------------------------ *)

module Recorder = struct
  type t = { counts : int String.Table.t }

  let create () = { counts = String.Table.create () }
  let on_compute t name = Hashtbl.incr t.counts name
  let reset t = Hashtbl.clear t.counts
  let count t name = Option.value (Hashtbl.find t.counts name) ~default:0
  let names t = List.sort (Hashtbl.keys t.counts) ~compare:String.compare
end

(* Sorted so the failure diff is readable: Alcotest prints both lists, and an
   unexpected node then shows up as an insertion rather than as a reordering. *)
let check_recomputed recorder ~msg ~expected =
  Alcotest.(check (list string))
    msg
    (List.sort expected ~compare:String.compare)
    (Recorder.names recorder)

(* ------------------------------------------------------------------------ *)
(* The standard book                                                         *)
(* ------------------------------------------------------------------------ *)

(* Three instruments across two sectors, one of them short.

     AAPL  TECH     150.00 x  200 =  +30,000
     MSFT  TECH     300.00 x  100 =  +30,000
     XOM   ENERGY   100.00 x -400 =  -40,000

   gross = 30,000 + 30,000 + 40,000 = 100,000   (magnitudes)
   net   = 30,000 + 30,000 - 40,000 =  20,000   (signed)
   TECH  = +60,000     ENERGY = -40,000
   weights = exposure / gross = 0.3, 0.3, -0.4

   The short leg is not decoration. It is what proves gross sums magnitudes
   while net and the weights keep their sign, and that an instrument limit
   measures |exposure| rather than the signed number.

   Cash is 100,000, so equity = 100,000 + 20,000 = 120,000. *)

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
let dollars = Notional.of_float

let book_limits =
  [
    limit "aapl-cap" (Limit.Instrument aapl) (Limit.Gross_notional (dollars 25_000.0));
    limit "msft-cap" (Limit.Instrument msft) (Limit.Gross_notional (dollars 50_000.0));
    limit "tech-cap" (Limit.Sector tech) (Limit.Gross_notional (dollars 80_000.0));
    limit "energy-cap" (Limit.Sector energy) (Limit.Gross_notional (dollars 30_000.0));
    limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 150_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 3_000.0));
    limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.10);
  ]

(* The same ten-observation series used in test_risk_metrics.ml, where its VaR
   and ES are hand-derived. At 95% over 10 observations the tail is the single
   worst return, so VaR = ES = 0.05.

   AAPL and MSFT get this series and XOM its negation, which makes the portfolio
   return series come out exactly equal to it:

     r_p = 0.3*r + 0.3*r + (-0.4)*(-r) = (0.3 + 0.3 + 0.4) * r = r

   That is why these weights were chosen: the book-level risk numbers are then
   the same hand-checked values, with no second derivation to get wrong. *)
let base_returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]
let negated_returns = Array.map base_returns ~f:Float.neg

(* Population variance of [base_returns]. The mean is zero, so it is the mean of
   the squares: 2 * (0.0025 + 0.0016 + 0.0009 + 0.0004 + 0.0001) / 10 = 0.0011. *)
let base_variance = 0.0011

(* Standard normal 95th percentile, from tables. *)
let z95 = 1.6448536269514722

let with_graph ?(seed = true) ~f () =
  let recorder = Recorder.create () in
  let graph =
    Graph.create
      ~on_compute:(Recorder.on_compute recorder)
      ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits:book_limits
      ~confidence:0.95 ~return_window:10 ()
  in
  if seed then (
    Graph.set_price graph aapl (Price.of_float 150.0);
    Graph.set_price graph msft (Price.of_float 300.0);
    Graph.set_price graph xom (Price.of_float 100.0);
    Graph.set_qty graph aapl (Qty.of_float 200.0);
    Graph.set_qty graph msft (Qty.of_float 100.0);
    Graph.set_qty graph xom (Qty.of_float (-400.0));
    Graph.set_returns graph aapl base_returns;
    Graph.set_returns graph msft base_returns;
    Graph.set_returns graph xom negated_returns;
    Graph.stabilize graph;
    (* Close an equity mark at the seeded level so the drawdown node has a peak
       to measure against. Without a recorded peak, current drawdown is
       identically zero and its cutoff would suppress every later change --
       correct behaviour, but it would make the propagation tests below assert
       something weaker than they appear to. *)
    Graph.mark_equity graph;
    (* [mark_equity] settles the graph in order to read a consistent equity, but
       then writes the history cell and leaves it dirty -- setters do not
       stabilize, which is the whole point of batching. Settle again so the seed
       is fully absorbed before the recorder is cleared; otherwise the pending
       history write shows up in the next test's stabilize and looks like a
       spurious dependency. *)
    Graph.stabilize graph);
  Recorder.reset recorder;
  Exn.protect ~f:(fun () -> f graph recorder) ~finally:(fun () -> Graph.destroy graph)

(* ------------------------------------------------------------------------ *)
(* 1. The book is computed correctly at all                                  *)
(* ------------------------------------------------------------------------ *)

let notional_eq name expected actual =
  Alcotest.check float_eq name expected (Notional.to_float actual)

let test_exposures () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      let by_instrument = Graph.Snapshot.exposure_by_instrument s in
      notional_eq "AAPL 150 x 200" 30_000.0 (Map.find_exn by_instrument aapl);
      notional_eq "MSFT 300 x 100" 30_000.0 (Map.find_exn by_instrument msft);
      notional_eq "XOM 100 x -400" (-40_000.0) (Map.find_exn by_instrument xom);
      let by_sector = Graph.Snapshot.exposure_by_sector s in
      notional_eq "TECH = AAPL + MSFT" 60_000.0 (Map.find_exn by_sector tech);
      notional_eq "ENERGY = XOM" (-40_000.0) (Map.find_exn by_sector energy);
      notional_eq "gross sums magnitudes" 100_000.0 (Graph.Snapshot.gross_exposure s);
      notional_eq "net keeps signs" 20_000.0 (Graph.Snapshot.net_exposure s);
      notional_eq "equity = cash + net" 120_000.0 (Graph.Snapshot.equity s);
      let weights = Graph.Snapshot.weights s in
      Alcotest.check float_eq "w(AAPL) = 30000/100000" 0.3 (Map.find_exn weights aapl);
      Alcotest.check float_eq "w(MSFT) = 30000/100000" 0.3 (Map.find_exn weights msft);
      Alcotest.check float_eq "w(XOM) = -40000/100000, a short stays negative" (-0.4)
        (Map.find_exn weights xom))
    ()

let test_risk_numbers () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      Alcotest.(check bool)
        "not warming up once seeded" false (Graph.Snapshot.warming_up s);
      (* r_p = r by construction (see [base_returns]), so these are the values
         hand-derived in test_risk_metrics.ml for the same series. *)
      Alcotest.check opt_float "historical VaR 95%" (Some 0.05)
        (Graph.Snapshot.historical_var s);
      Alcotest.check opt_float "ES 95% equals VaR when the tail holds one observation"
        (Some 0.05)
        (Graph.Snapshot.expected_shortfall s);
      (* Parametric VaR from the closed form, z * sqrt(w' Sigma w). The
         portfolio variance is the variance of the base series because the
         weights reproduce that series exactly. *)
      Alcotest.check opt_float "parametric VaR = z95 * sqrt(0.0011)"
        (Some (z95 *. Float.sqrt base_variance))
        (Graph.Snapshot.parametric_var s);
      (* Dollar VaR is the fraction times gross, because the weights are
         normalised by gross: 0.05 * 100,000. *)
      Alcotest.check opt_float "VaR in dollars" (Some 5_000.0)
        (Option.map (Graph.Snapshot.value_at_risk_notional s) ~f:Notional.to_float);
      Alcotest.check opt_float "ES in dollars" (Some 5_000.0)
        (Option.map (Graph.Snapshot.expected_shortfall_notional s) ~f:Notional.to_float))
    ()

let test_covariance_matrix () =
  with_graph
    ~f:(fun graph _ ->
      Graph.stabilize graph;
      match Graph.covariance graph with
      | None -> Alcotest.fail "covariance should be available once windows are seeded"
      | Some cov ->
          let get i j = Owl.Mat.get cov i j in
          (* AAPL and MSFT hold the same series and XOM its negation, so every
           entry is the base variance up to sign, and the sign is that of the
           product of the two series. Row order is by symbol: AAPL, MSFT, XOM. *)
          Alcotest.check float_eq "var(AAPL)" base_variance (get 0 0);
          Alcotest.check float_eq "var(MSFT)" base_variance (get 1 1);
          Alcotest.check float_eq "var(XOM)" base_variance (get 2 2);
          Alcotest.check float_eq "cov(AAPL,MSFT), identical series" base_variance
            (get 0 1);
          Alcotest.check float_eq "cov(AAPL,XOM), negated series" (-.base_variance)
            (get 0 2);
          Alcotest.check float_eq "cov(MSFT,XOM), negated series" (-.base_variance)
            (get 1 2);
          Alcotest.check float_eq "symmetric (1,0)" (get 0 1) (get 1 0);
          Alcotest.check float_eq "symmetric (2,0)" (get 0 2) (get 2 0);
          Alcotest.check float_eq "symmetric (2,1)" (get 1 2) (get 2 1))
    ()

(* Beta of the book against the macro factor.

   The portfolio return series is [base_returns] by construction, so a factor of
   exactly twice that series gives a hand-derivable answer:

     beta = cov(r, 2r) / var(2r) = 2*var(r) / 4*var(r) = 0.5

   No arithmetic on the variance is needed, which is the point of choosing it. *)
let test_portfolio_beta () =
  with_graph
    ~f:(fun graph _ ->
      Graph.set_factor_returns graph (Array.map base_returns ~f:(fun r -> 2.0 *. r));
      let s = Graph.snapshot graph in
      Alcotest.check opt_float "beta against a 2x factor" (Some 0.5)
        (Graph.Snapshot.portfolio_beta s);
      (* A factor identical to the book's own returns gives exactly one. *)
      Graph.set_factor_returns graph base_returns;
      Alcotest.check opt_float "beta against itself" (Some 1.0)
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph));
      (* Inverted factor, inverted sign. *)
      Graph.set_factor_returns graph negated_returns;
      Alcotest.check opt_float "beta against the negation" (Some (-1.0))
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph)))
    ()

(* The case that would otherwise take the graph down.

   Risk_metrics.beta raises on a zero-variance factor, and it is right to: a
   constant factor explains nothing, so beta is undefined rather than zero. But
   a flat rate series is ROUTINE -- rates do not move most days -- so what is an
   exceptional condition for a pure function is an ordinary Tuesday for a live
   node. An exception raised inside a node body during stabilization takes the
   whole graph with it, so this must be None and must never raise. *)
let test_portfolio_beta_is_total () =
  with_graph
    ~f:(fun graph _ ->
      Graph.set_factor_returns graph (Array.create ~len:10 0.0425);
      Alcotest.check opt_float "a flat factor gives None, not an exception nor zero" None
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph));
      Graph.set_factor_returns graph [||];
      Alcotest.check opt_float "no factor data yet" None
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph));
      Graph.set_factor_returns graph [| 0.01 |];
      Alcotest.check opt_float "a single observation has no second moment" None
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph));
      (* And it recovers the moment the factor starts moving again. *)
      Graph.set_factor_returns graph base_returns;
      Alcotest.check opt_float "recovers once the factor moves" (Some 1.0)
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph)))
    ()

(* FRED publishes daily and the book's window fills at its own rate, so the two
   series are almost never the same length. They are trimmed to a common length
   at the RIGHT edge -- the most recent observations -- because aligning at the
   left would regress this week's book against last month's rates. *)
let test_beta_aligns_at_the_recent_edge () =
  with_graph
    ~f:(fun graph _ ->
      (* Ten portfolio observations against a much longer factor history whose
         OLDEST values are garbage. If alignment took the left edge, the garbage
         would dominate; taking the right edge, the last ten are exactly 2x the
         book's series and beta is 0.5. *)
      let noise = Array.create ~len:40 99.0 in
      let recent = Array.map base_returns ~f:(fun r -> 2.0 *. r) in
      Graph.set_factor_returns graph (Array.append noise recent);
      Alcotest.check opt_float "only the most recent overlap is used" (Some 0.5)
        (Graph.Snapshot.portfolio_beta (Graph.snapshot graph)))
    ()

(* ------------------------------------------------------------------------ *)
(* 2. The architecture tests                                                 *)
(* ------------------------------------------------------------------------ *)

(* Everything downstream of one instrument's exposure, and nothing else.

   Read this list as the answer to "what actually depends on an AAPL position":
   AAPL's own exposure; the TECH sector it belongs to; the book-level
   aggregates; the weights, and therefore every risk number computed from them;
   equity and drawdown, because a position is marked to market; and exactly the
   five limits that measure one of those quantities.

   Absent, and asserted absent by the set equality below: exposure:MSFT,
   exposure:XOM, sector:ENERGY, aligned_returns, covariance, covariance_ewma,
   limit:msft-cap, limit:energy-cap -- and the whole feed-health branch, which a
   position change has no business touching. *)
let downstream_of_aapl_position =
  [
    "exposure:AAPL";
    "exposure_map";
    "sector:TECH";
    "sector_map";
    "gross_exposure";
    "net_exposure";
    "weights";
    "portfolio_returns";
    "historical_var";
    "expected_shortfall";
    "parametric_var";
    (* Both parametric VaRs, because both are weights x covariance and a
       position change moves the weights. The two MATRICES are absent from this
       list and stay absent -- see [test_prices_never_reach_covariance] -- which
       is the distinction that matters: adding a second estimator doubled what
       an update to the return window costs and added nothing to what a tick
       costs. *)
    "parametric_var_ewma";
    (* The attribution branch. It IS downstream of price, and deliberately so:
       a weight is a position over a total, so repricing the book changes how
       risk is shared out even when the covariance matrix that shapes it has
       not moved. What stays cached is the expensive half -- see
       [test_prices_never_reach_covariance] -- so a tick pays for a
       matrix-vector product and not a matrix rebuild. *)
    "attribution";
    "component_var_map";
    "component_var_sector_map";
    "diversification_ratio";
    "portfolio_beta";
    "var_notional";
    "es_notional";
    "equity";
    "current_drawdown";
    "limit:aapl-cap";
    "limit:tech-cap";
    "limit:book-cap";
    "limit:var-cap";
    "limit:dd-cap";
    "breaches";
  ]

(* A tick reaches everything a position change does, plus exactly two more
   nodes: the liveness record for that one symbol, and the aggregate that
   summarises it.

   That difference is the whole reason [apply_tick] and [set_price] are separate
   entry points. A tick is evidence that the feed is alive; a manual reprice is
   not, and must not be allowed to make a dead feed look healthy. *)
let downstream_of_aapl_tick = downstream_of_aapl_position @ [ "feed:AAPL"; "feed_health" ]

(* The pinned set for one new return observation, lifted out of
   [test_return_push_is_local] so the topology test below can assert the
   DRAWING's closure of [returns[MSFT]] is this exact list. One copy, two
   readers: the recorder pins what ran, the topology pins what is wired, and
   the two must be the same list or one of them is lying. *)
let downstream_of_a_return =
  [
    "aligned_returns";
    "covariance";
    (* A return genuinely reaches BOTH matrices, and this is where the second
       estimator's cost actually lands: one new observation now rebuilds two
       n x n matrices instead of one. That is the honest trade and it belongs
       in an assertion rather than a comment -- the return window moves once a
       day, and the tick path, which moves thousands of times a day, is
       untouched. *)
    "covariance_ewma";
    "portfolio_returns";
    "historical_var";
    "expected_shortfall";
    "parametric_var";
    "parametric_var_ewma";
    "attribution";
    "component_var_map";
    "component_var_sector_map";
    "diversification_ratio";
    "portfolio_beta";
    "var_notional";
    "es_notional";
    "limit:var-cap";
    "breaches";
  ]

(* ------------------------------------------------------------------------ *)
(* 2a. Node names and their units                                            *)
(* ------------------------------------------------------------------------ *)

(* The input cells are named with BRACKETS and the derived per-symbol nodes
   with a COLON, and the difference is load-bearing rather than cosmetic: the
   drawing puts cells in their own column, and deriving that from the name
   means there is no second table saying which names are cells for a refactor
   to leave behind. *)
let test_input_names () =
  Alcotest.(check string) "price cell" "price[AAPL]" (Graph.Node_name.Input.price aapl);
  Alcotest.(check string) "qty cell" "qty[AAPL]" (Graph.Node_name.Input.qty aapl);
  Alcotest.(check string)
    "return window" "returns[MSFT]"
    (Graph.Node_name.Input.returns msft);
  Alcotest.(check string)
    "liveness cell" "last_tick[XOM]"
    (Graph.Node_name.Input.last_tick xom);
  Alcotest.(check string)
    "contract count" "contracts[NVDA-950C]"
    (Graph.Node_name.Input.contracts "NVDA-950C");
  Alcotest.(check string)
    "vol mark" "implied_vol[NVDA-950C]"
    (Graph.Node_name.Input.implied_vol "NVDA-950C");
  Alcotest.(check string) "cash" "cash" Graph.Node_name.Input.cash;
  Alcotest.(check string) "now" "now" Graph.Node_name.Input.now;
  (* The two clocks are two names. graph.ml keeps them apart because they tick
     at different rates and only one of them is allowed upstream of a risk
     node; a page that called both "clock" would erase the distinction the
     module spends thirty lines defending. *)
  Alcotest.(check string)
    "the slow clock" "valuation_days" Graph.Node_name.Input.valuation_days

(* A unit for every name, and no unit invented for a name that does not exist.

   [unit_of] is what the drawing formats a value with -- dollars get a dollar
   sign, a fraction gets a percent, a matrix gets a run count instead of a
   number. Getting one wrong renders a fraction as dollars, which is the exact
   failure server.ml's json_of_breach ships a unit alongside every threshold to
   prevent. *)
let test_units () =
  let unit_is expected name =
    Alcotest.(check string)
      (name ^ " is " ^ expected)
      expected
      (Graph.Node_name.unit_of name)
  in
  unit_is "usd" "gross_exposure";
  unit_is "usd" "net_exposure";
  unit_is "usd" "equity";
  unit_is "usd" "var_notional";
  unit_is "usd" "es_notional";
  unit_is "usd" (Graph.Node_name.exposure aapl);
  unit_is "usd" (Graph.Node_name.sector tech);
  unit_is "usd" "cash";
  (* Vega is dollars per 1.00 of annualised vol -- server.ml says so where it
     refuses to divide by a hundred on the wire -- so it is money and formats
     as money. Gamma is not: it is delta-equivalent shares per unit move, and
     the only word in this vocabulary that prints a bare number for a quantity
     of shares is [qty]. *)
  unit_is "usd" "portfolio_vega";
  unit_is "qty" "portfolio_gamma";
  unit_is "fraction" "current_drawdown";
  unit_is "fraction" "historical_var";
  unit_is "fraction" "parametric_var_ewma";
  unit_is "fraction" "rate";
  unit_is "ratio" "portfolio_beta";
  unit_is "ratio" "diversification_ratio";
  unit_is "price" (Graph.Node_name.Input.price aapl);
  unit_is "qty" (Graph.Node_name.Input.qty aapl);
  unit_is "count" (Graph.Node_name.Input.contracts "NVDA-950C");
  unit_is "fraction" (Graph.Node_name.Input.implied_vol "NVDA-950C");
  unit_is "time" "valuation_days";
  (* [now] and [last_tick[S]] hold a timestamp, not a quantity, and this file
     already argues (Feed_health's note on [age]) that publishing a clock
     reading as a node value is a stale number wearing a fresh timestamp. They
     are [state]: the drawing prints a run count under them, never a number. *)
  unit_is "state" "now";
  unit_is "state" (Graph.Node_name.Input.last_tick aapl);
  unit_is "state" "feed_health";
  unit_is "state" (Graph.Node_name.feed aapl);
  unit_is "state" (Graph.Node_name.limit "aapl-cap");
  unit_is "matrix" "covariance";
  unit_is "matrix" "covariance_ewma";
  unit_is "matrix" "aligned_returns";
  unit_is "array" "weights";
  unit_is "array" "portfolio_returns";
  unit_is "array" "breaches";
  unit_is "array" (Graph.Node_name.Input.returns aapl);
  unit_is "map" "exposure_map";
  unit_is "map" "component_var_map";
  unit_is "map" "vega_by_bucket";
  unit_is "state" "attribution";
  unit_is "state" (Graph.Node_name.greeks "NVDA-950C");
  (* Loud on a name nobody declared. A silent default would let a new node
     reach the page with a plausible unit nobody chose, which is how a
     fraction ends up rendered as dollars. *)
  Alcotest.check_raises "an undeclared name has no unit"
    (Failure "graph: no unit declared for node \"invented\"") (fun () ->
      ignore (Graph.Node_name.unit_of "invented" : string))

let test_scalar_units () =
  List.iter [ "usd"; "fraction"; "ratio"; "count"; "price"; "qty"; "time" ] ~f:(fun u ->
      Alcotest.(check bool) (u ^ " is scalar") true (Graph.Node_name.is_scalar u));
  List.iter [ "matrix"; "array"; "map"; "state" ] ~f:(fun u ->
      Alcotest.(check bool) (u ^ " is not scalar") false (Graph.Node_name.is_scalar u))

(* ------------------------------------------------------------------------ *)
(* 2b. The label table                                                       *)
(* ------------------------------------------------------------------------ *)

(* Every node the hook can name is also named ON the node, and the two lists
   are the same list.

   This is the first half of "the drawing is the graph, taken from the graph".
   graph.ml has called [note Node_name.x] inside every node body since Phase 1;
   what is new is that the same string now sits in Incremental's own node
   record, where For_analyzer can read it back beside the node's children. If
   a site is named in the body and not on the node, the page draws an edge into
   a node it cannot label; if it is named on the node and not in the body, the
   page lights a node the tests never counted. Both are caught here, by name.

   Hand-counted on the standard book -- three names, two sectors, seven limits,
   no options:

     inputs    cash equity_history factor_returns valuation_days now       5
               price[S] qty[S] returns[S] last_tick[S]  x 3                12
     derived   exposure:S feed:S  x 3                                       6
               sector:K  x 2                                                2
               limit:name  x 7                                              7
               singletons                                                  29
                                                                           --
                                                                           61

   [rate] is NOT in the list, and that is the traverse being honest rather
   than a bug: on a book with no options nothing reads the rate cell, so it is
   reachable from no observer and is not part of this graph. It joins the
   moment a contract does. *)
let expected_labels =
  [
    "aligned_returns";
    "attribution";
    "breaches";
    "cash";
    "component_var_map";
    "component_var_sector_map";
    "covariance";
    "covariance_ewma";
    "current_drawdown";
    "diversification_ratio";
    "equity";
    "equity_history";
    "es_notional";
    "expected_shortfall";
    "exposure:AAPL";
    "exposure:MSFT";
    "exposure:XOM";
    "exposure_map";
    "factor_returns";
    "feed:AAPL";
    "feed:MSFT";
    "feed:XOM";
    "feed_health";
    "gamma_map";
    "gross_exposure";
    "historical_var";
    "last_tick[AAPL]";
    "last_tick[MSFT]";
    "last_tick[XOM]";
    "limit:aapl-cap";
    "limit:book-cap";
    "limit:dd-cap";
    "limit:energy-cap";
    "limit:msft-cap";
    "limit:tech-cap";
    "limit:var-cap";
    "net_exposure";
    "now";
    "parametric_var";
    "parametric_var_ewma";
    "portfolio_beta";
    "portfolio_gamma";
    "portfolio_returns";
    "portfolio_vega";
    "price[AAPL]";
    "price[MSFT]";
    "price[XOM]";
    "qty[AAPL]";
    "qty[MSFT]";
    "qty[XOM]";
    "returns[AAPL]";
    "returns[MSFT]";
    "returns[XOM]";
    "sector:ENERGY";
    "sector:TECH";
    "sector_map";
    "valuation_days";
    "var_notional";
    "vega_by_bucket";
    "vega_map";
    "weights";
  ]

(* The 27 observers of Phase 1-5 plus Task 2's attribution observer. Every
   published value and nothing else. *)
let expected_observed =
  [
    "attribution";
    "breaches";
    "component_var_map";
    "component_var_sector_map";
    "covariance";
    "covariance_ewma";
    "current_drawdown";
    "diversification_ratio";
    "equity";
    "es_notional";
    "expected_shortfall";
    "exposure_map";
    "feed_health";
    "gamma_map";
    "gross_exposure";
    "historical_var";
    "net_exposure";
    "parametric_var";
    "parametric_var_ewma";
    "portfolio_beta";
    "portfolio_gamma";
    "portfolio_returns";
    "portfolio_vega";
    "sector_map";
    "var_notional";
    "vega_by_bucket";
    "vega_map";
    "weights";
  ]

let test_every_node_is_labelled () =
  with_graph
    ~f:(fun graph _ ->
      let labels = Graph.labelled_nodes graph in
      Alcotest.(check int)
        "sixty-one named nodes on the standard book" 61 (List.length labels);
      Alcotest.(check (list string))
        "the names, and no others" expected_labels (List.map labels ~f:fst);
      Alcotest.(check (list string))
        "the twenty-eight observed" expected_observed
        (List.filter_map labels ~f:(fun (name, observed) ->
             if observed then Some name else None));
      (* The marker is a label, never a name. *)
      Alcotest.(check bool)
        "the observed marker is not itself a node" false
        (List.exists labels ~f:(fun (name, _) -> String.equal name Graph.observed_marker));
      (* Every derived name has a unit, which Task 1 made a total function
         over this graph's vocabulary; a name that raises here is a node
         someone added without deciding what it is measured in. *)
      List.iter labels ~f:(fun (name, _) ->
          ignore (Graph.Node_name.unit_of name : string)))
    ()

(* Reading the table is free. Labels are metadata on nodes that already
   exist; walking them must not stabilize, must not observe and must not run a
   single node body. The recorder is the witness. *)
let test_reading_labels_costs_nothing () =
  with_graph
    ~f:(fun graph recorder ->
      let before = Graph.total_nodes_recomputed () in
      ignore (Graph.labelled_nodes graph : (string * bool) list);
      ignore (Graph.walk graph : Graph.Raw.t list);
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"no node body ran" ~expected:[];
      Alcotest.(check int)
        "Incremental's own counter did not move either" before
        (Graph.total_nodes_recomputed ()))
    ()

(* ------------------------------------------------------------------------ *)
(* 2c. The topology: the drawing is the graph, taken from the graph          *)
(* ------------------------------------------------------------------------ *)

let sorted xs = List.sort xs ~compare:String.compare
let set_to_list s = Set.to_list s

(* The three edges graph.ml's header says are worth staring at, as
   assertions on the CONTRACTED graph rather than on the recorder.

   The recorder pins what ran on a tick. The topology says what is wired. If
   the two agree, the drawing the page makes from the topology is a drawing
   of the thing the tests measure, and lighting the recorder's set on it lights
   exactly the nodes that are downstream of the cell that moved. If they
   disagree, either an edge exists the hook never saw run (a dependency with
   a cutoff nobody meant), or a node ran that no edge explains (impossible in
   Incremental, and worth being told about). *)
let test_topology_closures_match_the_pinned_sets () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let down names = sorted (set_to_list (Graph.Topology.closure topo names `Down)) in
      Alcotest.(check (list string))
        "downstream of {price[AAPL], last_tick[AAPL]} is the pinned tick set"
        (sorted downstream_of_aapl_tick)
        (down [ Graph.Node_name.Input.price aapl; Graph.Node_name.Input.last_tick aapl ]);
      Alcotest.(check (list string))
        "downstream of returns[MSFT] is the pinned push_return set"
        (sorted downstream_of_a_return)
        (down [ Graph.Node_name.Input.returns msft ]);
      (* The expensive half is upstream of no price. This is the
         [test_prices_never_reach_covariance] fact, stated structurally. *)
      List.iter [ aapl; msft; xom ] ~f:(fun s ->
          let reach =
            Graph.Topology.closure topo [ Graph.Node_name.Input.price s ] `Down
          in
          List.iter [ "aligned_returns"; "covariance"; "covariance_ewma" ] ~f:(fun m ->
              Alcotest.(check bool)
                (Printf.sprintf "%s is not downstream of price[%s]" m (Symbol.to_string s))
                false (Set.mem reach m))))
    ()

(* Every edge joins two named nodes, and the node list is the label table. The
   contraction must invent nothing and lose nothing: an edge whose endpoint is
   not a node would be a dangling stroke on the page, and a labelled node the
   topology dropped would be a name the frame can light and the drawing cannot
   find. *)
let test_topology_is_closed_over_its_names () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let names = String.Set.of_list (Graph.Topology.names topo) in
      Alcotest.(check (list string))
        "the topology's nodes are the label table"
        (List.map (Graph.labelled_nodes graph) ~f:fst)
        (sorted (Set.to_list names));
      List.iter (Graph.Topology.edges topo) ~f:(fun (from, into) ->
          Alcotest.(check bool)
            (from ^ " -> " ^ into ^ ": both ends are nodes")
            true
            (Set.mem names from && Set.mem names into));
      Alcotest.(check bool)
        "there is at least one edge per non-input node" true
        (List.length (Graph.Topology.edges topo)
        >= Graph.Topology.Counts.named (Graph.Topology.counts topo));
      (* The one edge the whole design is about: the decomposition reads the
         equal-weighted matrix, and which one is on the wire beside it. *)
      Alcotest.(check (list string))
        "attribution reads weights and covariance, nothing else"
        [ "covariance"; "weights" ]
        (sorted (Graph.Topology.inputs_of topo "attribution"));
      Alcotest.(check string)
        "and the topology says which matrix" "equal_weighted"
        (Graph.Covariance_estimator.to_string
           (Graph.Topology.attribution_covariance topo)))
    ()

(* Families from the shape of the name, and the typed fields beside them. A
   per-symbol node carries its symbol as a Symbol.t the graph recognises, a
   limit node carries the Limit.t it evaluates, so the page can put the
   instrument-scoped cap on the instrument's row without parsing a string. *)
let test_topology_families () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      let node name =
        Option.value_exn (Graph.Topology.find topo name) ~message:("no node " ^ name)
      in
      let family name =
        Graph.Topology.Family.to_string (Graph.Topology.Node.family (node name))
      in
      Alcotest.(check string) "price[AAPL] is an input" "input" (family "price[AAPL]");
      Alcotest.(check string) "cash is an input" "input" (family "cash");
      Alcotest.(check string)
        "exposure:AAPL is per-symbol" "per_symbol" (family "exposure:AAPL");
      Alcotest.(check string) "feed:XOM is per-symbol" "per_symbol" (family "feed:XOM");
      Alcotest.(check string)
        "sector:TECH is per-sector" "per_sector" (family "sector:TECH");
      Alcotest.(check string)
        "limit:dd-cap is per-limit" "per_limit" (family "limit:dd-cap");
      Alcotest.(check string)
        "covariance is a singleton" "singleton" (family "covariance");
      Alcotest.(check (option string))
        "price[AAPL] knows its symbol" (Some "AAPL")
        (Option.map (Graph.Topology.Node.symbol (node "price[AAPL]")) ~f:Symbol.to_string);
      Alcotest.(check (option string))
        "sector:ENERGY knows its sector" (Some "ENERGY")
        (Option.map
           (Graph.Topology.Node.sector (node "sector:ENERGY"))
           ~f:Sector.to_string);
      (match Graph.Topology.Node.limit (node "limit:aapl-cap") with
      | Some l ->
          Alcotest.(check string)
            "limit:aapl-cap carries its limit" "aapl-cap" (Limit.name l);
          Alcotest.(check string)
            "and its kind" "gross_notional"
            (Graph.Topology.limit_kind_to_string (Limit.kind l))
      | None -> Alcotest.fail "limit:aapl-cap has no limit");
      Alcotest.(check string)
        "an input cell is a Var" "Var"
        (Graph.Topology.Node.kind (node "qty[XOM]"));
      Alcotest.(check string)
        "and carries the value-equality cutoff" "Equal"
        (Graph.Topology.Node.cutoff (node "qty[XOM]"));
      Alcotest.(check int)
        "a cell has rank 0" 0
        (Graph.Topology.Node.rank (node "qty[XOM]"));
      Alcotest.(check int)
        "exposure:XOM is one step in" 1
        (Graph.Topology.Node.rank (node "exposure:XOM"));
      Alcotest.(check string)
        "every node has the unit Task 1 gave it" "usd"
        (Graph.Topology.Node.unit (node "exposure:XOM")))
    ()

(* An instrument cap hangs off ONE exposure, and that exposure off one price
   and one quantity. This is why the page can leave limit:aapl-cap at full
   authority while CVX is quiet: its whole upstream is three nodes, none of
   them CVX's. *)
let test_a_limit_has_one_input () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "limit:aapl-cap reads exposure:AAPL and nothing else" [ "exposure:AAPL" ]
        (Graph.Topology.inputs_of topo "limit:aapl-cap");
      Alcotest.(check (list string))
        "and its whole upstream is three nodes"
        [ "exposure:AAPL"; "price[AAPL]"; "qty[AAPL]" ]
        (sorted (set_to_list (Graph.Topology.closure topo [ "limit:aapl-cap" ] `Up))))
    ()

(* The clock's dead end, structurally. [test_clock_cannot_reach_the_risk_chain]
   shows it by running the clock; this shows there is no edge to run along.
   The two sets must agree with each other and with graph.ml's diagram. *)
let test_no_risk_node_is_downstream_of_now () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "downstream of now: the liveness branch, whole and alone"
        [ "feed:AAPL"; "feed:MSFT"; "feed:XOM"; "feed_health" ]
        (sorted (set_to_list (Graph.Topology.closure topo [ "now" ] `Down)));
      (* And from the other side: nothing upstream of feed_health is a price,
         a quantity or a return. The branch shares no edge with the book. *)
      let up = Graph.Topology.closure topo [ "feed_health" ] `Up in
      Alcotest.(check (list string))
        "upstream of feed_health: three cells, three feed nodes, the clock"
        [
          "feed:AAPL";
          "feed:MSFT";
          "feed:XOM";
          "last_tick[AAPL]";
          "last_tick[MSFT]";
          "last_tick[XOM]";
          "now";
        ]
        (sorted (set_to_list up)))
    ()

(* The count as a formula in the book's shape. Hand-derived:

     per symbol   exposure:S feed:S                                   2|S|
     per sector   sector:K                                             |K|
     per limit    limit:name                                           |L|
     per option   greeks:ID option_exposure:ID                        2|O|
     singletons   the 29 derived names Task 3 lists                     29
                                                             named = 2|S| + |K| + |L| + 2|O| + 29
     cells        price qty returns last_tick  x |S|                  4|S|
                  cash equity_history factor_returns valuation_days now  5
                  contracts implied_vol  x |O|                        2|O|
                  rate -- reachable only once a contract reads it       [|O| > 0]

   Standard book: 3, 2, 7, 0 -> named 44, inputs 17, total 61 (Task 3's count).
   With one call on AAPL: 3, 2, 7, 1 -> named 46, inputs 20, total 66. *)
let aapl_call_for_topology =
  Options.Position.create ~underlying:aapl ~id:"AAPL-100C"
    ~strike:(Options.Strike.of_float 100.0)
    ~right:Options.Right.Call ~expiry_in_days:30.0 ()

let test_named_node_count_formula () =
  let formula ~symbols ~sectors ~limits ~options =
    ( (2 * symbols) + sectors + limits + (2 * options) + 29,
      (4 * symbols) + 5 + (2 * options) + if options > 0 then 1 else 0 )
  in
  with_graph
    ~f:(fun graph _ ->
      let c = Graph.Topology.counts (Graph.topology graph) in
      let named, inputs = formula ~symbols:3 ~sectors:2 ~limits:7 ~options:0 in
      Alcotest.(check int)
        "named on the standard book" named
        (Graph.Topology.Counts.named c);
      Alcotest.(check int)
        "inputs on the standard book" inputs
        (Graph.Topology.Counts.inputs c);
      Alcotest.(check int) "instruments" 3 (Graph.Topology.Counts.instruments c);
      Alcotest.(check int) "sectors" 2 (Graph.Topology.Counts.sectors c);
      Alcotest.(check int) "limits" 7 (Graph.Topology.Counts.limits c);
      Alcotest.(check int) "options" 0 (Graph.Topology.Counts.options c);
      Alcotest.(check int) "observed" 28 (Graph.Topology.Counts.observed c);
      (* The traverse's own count is strictly larger than the named count: the
         plumbing exists, it is just not drawn. *)
      Alcotest.(check bool)
        (Printf.sprintf "incremental_nodes (%d) > named + inputs (%d)"
           (Graph.Topology.Counts.incremental_nodes c)
           (named + inputs))
        true
        (Graph.Topology.Counts.incremental_nodes c > named + inputs))
    ();
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits:book_limits
      ~confidence:0.95 ~return_window:10 ~options:[ aapl_call_for_topology ] ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let topo = Graph.topology graph in
      let c = Graph.Topology.counts topo in
      let named, inputs = formula ~symbols:3 ~sectors:2 ~limits:7 ~options:1 in
      Alcotest.(check int) "named with one contract" named (Graph.Topology.Counts.named c);
      Alcotest.(check int)
        "inputs with one contract" inputs
        (Graph.Topology.Counts.inputs c);
      Alcotest.(check bool)
        "rate joined the graph" true
        (Option.is_some (Graph.Topology.find topo "rate"));
      (* The four declared edges of a Greeks node, and no fifth. *)
      Alcotest.(check (list string))
        "greeks reads price, vol, the slow clock and the rate"
        [ "implied_vol[AAPL-100C]"; "price[AAPL]"; "rate"; "valuation_days" ]
        (Graph.Topology.inputs_of topo "greeks:AAPL-100C");
      Alcotest.(check (list string))
        "option_exposure reads the Greeks, the count and the spot"
        [ "contracts[AAPL-100C]"; "greeks:AAPL-100C"; "price[AAPL]" ]
        (Graph.Topology.inputs_of topo "option_exposure:AAPL-100C");
      Alcotest.(check bool)
        "and folds into the underlying's exposure" true
        (List.mem
           (Graph.Topology.inputs_of topo "exposure:AAPL")
           "option_exposure:AAPL-100C" ~equal:String.equal);
      match Graph.Topology.find topo "greeks:AAPL-100C" with
      | Some n ->
          Alcotest.(check (option string))
            "the option node rows under its underlying" (Some "AAPL")
            (Option.map (Graph.Topology.Node.symbol n) ~f:Symbol.to_string);
          Alcotest.(check (option string))
            "and names its contract" (Some "AAPL-100C")
            (Option.map (Graph.Topology.Node.contract n) ~f:fst)
      | None -> Alcotest.fail "greeks:AAPL-100C is not in the topology")

(* The observed flags are the label table's, and ranks never run backwards. *)
let test_observed_flags_and_ranks () =
  with_graph
    ~f:(fun graph _ ->
      let topo = Graph.topology graph in
      Alcotest.(check (list string))
        "the twenty-eight observed, by name" expected_observed
        (* Sorted, because the topology's nodes come in (rank, name) order and
           [expected_observed] is by name alone. *)
        (sorted
           (List.filter_map (Graph.Topology.nodes topo) ~f:(fun n ->
                if Graph.Topology.Node.observed n then Some (Graph.Topology.Node.name n)
                else None)));
      let rank name =
        Graph.Topology.Node.rank
          (Option.value_exn (Graph.Topology.find topo name) ~message:("no node " ^ name))
      in
      List.iter (Graph.Topology.edges topo) ~f:(fun (from, into) ->
          Alcotest.(check bool)
            (Printf.sprintf "%s (rank %d) -> %s (rank %d) runs left to right" from
               (rank from) into (rank into))
            true
            (rank from < rank into));
      (* Two anchors, derived by hand along the longest path:
           price -> exposure:S -> gross -> weights -> portfolio_returns -> historical_var
           -> var_notional -> limit:var-cap -> breaches  is 8 steps, and nothing
           into breaches is longer. *)
      Alcotest.(check int) "breaches is the deepest node" 8 (rank "breaches");
      Alcotest.(check int)
        "limit:aapl-cap sits beside the aggregates, not in a limits column" 2
        (rank "limit:aapl-cap"))
    ()

(* Asking for the drawing runs nothing and changes no pinned set. *)
let test_topology_costs_nothing () =
  with_graph
    ~f:(fun graph recorder ->
      let before = Graph.total_nodes_recomputed () in
      ignore (Graph.topology graph : Graph.Topology.t);
      Alcotest.(check int)
        "no node ran for the topology" before
        (Graph.total_nodes_recomputed ());
      (* Same tick [test_price_tick_is_local] pins, not a fresh one: a price
         RISE leaves drawdown at 0.0 under its cutoff, so [limit:dd-cap] would
         legitimately sit out and the assertion would be testing that cutoff
         rather than the cost of asking for the topology. *)
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 120.0; time = Time.epoch };
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"and the next tick recomputes exactly the pinned set"
        ~expected:downstream_of_aapl_tick)
    ()

(* A fork's observers come and go with the fork. [walk] starts from THIS
   graph's observers, so a fork alive in the same process is invisible to the
   served topology; and [destroy] releases the fork's twenty-eight, so
   /api/stress's twelve forks leave the process with the observer count it
   had. Both halves matter: the first is what makes the drawing this book's,
   the second is what keeps a scenario from costing every later stabilize. *)
let test_a_forks_observers_are_released () =
  with_graph
    ~f:(fun graph _ ->
      let before = Graph.active_observers () in
      let served = Graph.topology graph in
      let forked = Graph.fork graph in
      Alcotest.(check int)
        "a fork adds exactly its twenty-eight observers" (before + 28)
        (Graph.active_observers ());
      let fork_topology = Graph.topology forked in
      Alcotest.(check int)
        "the fork has its own copy of every node"
        (List.length (Graph.Topology.nodes served))
        (List.length (Graph.Topology.nodes fork_topology));
      Alcotest.(check int)
        "and the served topology did not grow"
        (List.length (Graph.Topology.nodes served))
        (List.length (Graph.Topology.nodes (Graph.topology graph)));
      Graph.destroy forked;
      Alcotest.(check int)
        "destroy releases all twenty-eight" before (Graph.active_observers ()))
    ()

(* The README's test, in its own words: "changing one position only triggers
   recomputation of nodes that depend on it". *)
let test_position_change_is_local () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_qty graph aapl (Qty.of_float 100.0);
      Graph.stabilize graph;
      check_recomputed recorder
        ~msg:"changing the AAPL position recomputes only AAPL's dependents"
        ~expected:downstream_of_aapl_position;
      (* And each ran exactly once. More than once would mean the graph is
         re-entering nodes within a single stabilize, which would quietly
         multiply the cost of every tick. *)
      List.iter downstream_of_aapl_position ~f:(fun name ->
          Alcotest.(check int)
            (Printf.sprintf "%s recomputed exactly once" name)
            1
            (Recorder.count recorder name)))
    ()

(* A price tick reaches the same risk nodes as a position change -- exposure is
   the product of the two, and neither reaches anything the other does not --
   plus the liveness record for that one symbol. *)
let test_price_tick_is_local () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 120.0; time = Time.epoch };
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"an AAPL tick recomputes only AAPL's dependents"
        ~expected:downstream_of_aapl_tick;
      (* Only AAPL's liveness was touched. A tick in one name says nothing about
         whether any other name is still printing. *)
      Alcotest.(check int)
        "MSFT liveness untouched" 0
        (Recorder.count recorder "feed:MSFT");
      Alcotest.(check int) "XOM liveness untouched" 0 (Recorder.count recorder "feed:XOM"))
    ()

(* The headline claim, stated on its own: repricing the entire book does not
   touch the covariance matrix. That matrix is the most expensive thing the
   engine computes and it is a property of the return series, not of what is
   held. A poll-and-recompute design rebuilds it on every tick. *)
let test_prices_never_reach_covariance () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_price graph aapl (Price.of_float 151.0);
      Graph.set_price graph msft (Price.of_float 301.0);
      Graph.set_price graph xom (Price.of_float 101.0);
      Graph.set_qty graph aapl (Qty.of_float 201.0);
      Graph.stabilize graph;
      Alcotest.(check int)
        "covariance untouched by any price or position change" 0
        (Recorder.count recorder "covariance");
      (* The EWMA sibling inherits the isolation, and asserting it separately is
         not redundant. The two matrices are independent nodes; wiring the new
         one to [weights] instead of [aligned_returns] -- an easy mistake, since
         everything else in the EWMA path does read weights -- would leave this
         zero while the one above stayed zero too. The claim being defended is
         about the node, not about the module. *)
      Alcotest.(check int)
        "the EWMA covariance is isolated the same way" 0
        (Recorder.count recorder "covariance_ewma");
      Alcotest.(check int)
        "the return windows were not even re-read" 0
        (Recorder.count recorder "aligned_returns");
      (* Sanity: the stabilize did do work, so the zeros above are a real
         absence rather than a graph that never ran. *)
      Alcotest.(check int)
        "gross did recompute" 1
        (Recorder.count recorder "gross_exposure"))
    ()

(* The mirror image: new return history recomputes the statistical side and
   leaves every exposure, sector total and position-based limit alone. *)
let test_return_push_is_local () =
  with_graph
    ~f:(fun graph recorder ->
      (* A new worst-ever loss, so the tail quantile genuinely moves and nothing
         is being suppressed by a cutoff further down. *)
      Graph.push_return graph msft (-0.10);
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"a new return recomputes only the statistical side"
        ~expected:downstream_of_a_return)
    ()

(* Re-sending a value that has not changed costs nothing at all.

   Not a micro-optimisation: a live feed republishes the same last price
   constantly, and under Incremental's default physical-equality cutoff each of
   those identical floats is a distinct box and would drag a full VaR
   recomputation behind it. graph.ml puts a value-equality cutoff on every input
   cell; this is that decision under test. *)
let test_idempotent_input_costs_nothing () =
  with_graph
    ~f:(fun graph recorder ->
      let before = Graph.total_nodes_recomputed () in
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph msft (Qty.of_float 100.0);
      Graph.set_returns graph xom negated_returns;
      Graph.stabilize graph;
      check_recomputed recorder ~msg:"no derived node recomputed" ~expected:[];
      (* Corroborated against Incremental's own counter, which sees every node
         in the state including the plumbing this module never named.

         The delta is three, not zero, and three is the floor: writing a cell
         marks its watch node, and that node has to run to produce a value
         before the cutoff has anything to compare. So each of the three writes
         costs exactly one node and stops dead. What is being asserted is that
         nothing at all lies beyond those three -- no aggregate, no risk number,
         no limit. *)
      Alcotest.(check int)
        "exactly one node per written cell, and nothing downstream" (before + 3)
        (Graph.total_nodes_recomputed ()))
    ()

(* Propagation stops the moment a value stops changing, not at the input.

   XOM is flattened to zero shares, after which its exposure is zero whatever
   the price does. The exposure node still runs -- it sits directly on the price
   cell and has to look -- but its output is unchanged, so the cutoff holds the
   line there and the twenty-odd nodes behind it never wake up. *)
let test_cutoff_stops_dead_ends () =
  with_graph
    ~f:(fun graph recorder ->
      Graph.set_qty graph xom Qty.zero;
      Graph.stabilize graph;
      Recorder.reset recorder;
      Graph.set_price graph xom (Price.of_float 120.0);
      Graph.stabilize graph;
      check_recomputed recorder
        ~msg:"a price move on a flat position stops at that instrument's exposure"
        ~expected:[ "exposure:XOM" ])
    ()

(* The staleness clock cannot reach a single risk number.

   This is the newest architecture test and the one guarding the newest hazard.
   Feed health is genuinely a function of wall-clock time -- a symbol goes stale
   by the passage of time, not by any event. So the graph has a [now] cell that a
   timer advances every few seconds.

   That cell is a loaded gun pointed at the whole design. If ANY risk node were
   downstream of it, the timer would recompute the book on a schedule, and this
   engine would have quietly become the polling system it was written to
   replace -- while still passing every other test in this file, because the
   numbers would all be correct. Only a recomputation-set assertion catches it.

   The second half of the test shows the cutoff doing real work: advancing the
   clock inside the staleness threshold re-examines each symbol's age and stops,
   because no symbol's status changed. The aggregate is not woken to be told
   nothing happened. *)
let test_clock_cannot_reach_the_risk_chain () =
  let tick_time = Time.epoch in
  with_graph
    ~f:(fun graph recorder ->
      (* Give every symbol a live print, so they are neither never-seen nor
         stale and there is a real status for the clock to change. *)
      List.iter [ aapl; msft; xom ] ~f:(fun symbol ->
          Graph.apply_tick graph
            { Tick.symbol; price = Graph.price graph symbol; time = tick_time });
      Graph.set_now graph tick_time;
      Graph.stabilize graph;
      Recorder.reset recorder;
      (* Ten seconds against a ninety-second threshold: everything ages, nothing
         changes status. *)
      Graph.set_now graph (Time.add tick_time (Time.Span.of_sec 10.0));
      Graph.stabilize graph;
      check_recomputed recorder
        ~msg:"a clock tick inside the threshold stops at the per-symbol liveness nodes"
        ~expected:[ "feed:AAPL"; "feed:MSFT"; "feed:XOM" ];
      (* Now past the threshold. The statuses flip, so the aggregate does wake --
         and still nothing else does. *)
      Recorder.reset recorder;
      Graph.set_now graph (Time.add tick_time (Time.Span.of_sec 120.0));
      Graph.stabilize graph;
      check_recomputed recorder
        ~msg:"crossing the threshold wakes the aggregate and nothing else"
        ~expected:[ "feed:AAPL"; "feed:MSFT"; "feed:XOM"; "feed_health" ];
      let health = Graph.feed_health graph in
      Alcotest.(check bool)
        "feed is not healthy" false
        (Graph.Feed_health.all_healthy health);
      Alcotest.(check (list string))
        "every symbol reported stale" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Graph.Feed_health.stale health) ~f:Symbol.to_string);
      Alcotest.(check (list string))
        "and none reported as never-seen" []
        (List.map (Graph.Feed_health.never_seen health) ~f:Symbol.to_string))
    ()

(* "Never printed" and "printed then went quiet" are different problems and are
   reported differently. The first is a subscription that did not take; the
   second is a feed that dropped. A single "no data" flag would hide which. *)
let test_never_seen_is_not_staleness () =
  with_graph
    ~f:(fun graph _ ->
      (* The seed uses set_price, never apply_tick, so nothing has "printed". *)
      Graph.set_now graph (Time.add Time.epoch (Time.Span.of_hr 1.0));
      Graph.stabilize graph;
      let health = Graph.feed_health graph in
      Alcotest.(check (list string))
        "all three never seen" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Graph.Feed_health.never_seen health) ~f:Symbol.to_string);
      Alcotest.(check (list string))
        "and none called stale -- you cannot go stale without ever being fresh" []
        (List.map (Graph.Feed_health.stale health) ~f:Symbol.to_string);
      Alcotest.(check bool)
        "not healthy either way" false
        (Graph.Feed_health.all_healthy health);
      (* One symbol prints; it alone becomes healthy. *)
      Graph.apply_tick graph
        {
          Tick.symbol = aapl;
          price = Price.of_float 150.0;
          time = Time.add Time.epoch (Time.Span.of_hr 1.0);
        };
      Graph.stabilize graph;
      let health = Graph.feed_health graph in
      Alcotest.(check (list string))
        "AAPL has now been seen" [ "MSFT"; "XOM" ]
        (List.map (Graph.Feed_health.never_seen health) ~f:Symbol.to_string))
    ()

(* ------------------------------------------------------------------------ *)
(* 3. Limits                                                                 *)
(* ------------------------------------------------------------------------ *)

let find_breach snapshot name =
  match
    List.find (Graph.Snapshot.breaches snapshot) ~f:(fun b ->
        String.equal (Limit.name (Breach.limit b)) name)
  with
  | Some b -> b
  | None -> Alcotest.failf "no result for limit %S" name

let test_breaches () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      Alcotest.(check (list string))
        "every limit could be evaluated" []
        (Graph.Snapshot.unevaluated_limits s);
      (* AAPL is 30,000 against a 25,000 cap. *)
      let aapl_cap = find_breach s "aapl-cap" in
      Alcotest.(check bool) "aapl-cap breached" true (Breach.breached aapl_cap);
      Alcotest.check float_eq "aapl-cap observed" 30_000.0 (Breach.observed aapl_cap);
      Alcotest.check float_eq "aapl-cap over by 5,000" 5_000.0 (Breach.excess aapl_cap);
      (* ENERGY is short 40,000 against a 30,000 cap. The limit measures
         magnitude, so a short breaches exactly as a long would. *)
      let energy_cap = find_breach s "energy-cap" in
      Alcotest.(check bool)
        "energy-cap breached by a SHORT" true (Breach.breached energy_cap);
      Alcotest.check float_eq "energy-cap observed the magnitude of -40,000" 40_000.0
        (Breach.observed energy_cap);
      Alcotest.check float_eq "energy-cap over by 10,000" 10_000.0
        (Breach.excess energy_cap);
      (* MSFT is 30,000 against 50,000: not breached, 20,000 of headroom. *)
      let msft_cap = find_breach s "msft-cap" in
      Alcotest.(check bool) "msft-cap not breached" false (Breach.breached msft_cap);
      Alcotest.check float_eq "msft-cap headroom 20,000" (-20_000.0)
        (Breach.excess msft_cap);
      Alcotest.check float_eq "msft-cap 60% utilised" 0.6 (Limits.utilisation msft_cap);
      (* Dollar VaR is 5,000 against a 3,000 cap. *)
      let var_cap = find_breach s "var-cap" in
      Alcotest.(check bool) "var-cap breached" true (Breach.breached var_cap);
      Alcotest.check float_eq "var-cap observed" 5_000.0 (Breach.observed var_cap);
      (* Book gross is 100,000 against 150,000. *)
      let book_cap = find_breach s "book-cap" in
      Alcotest.(check bool) "book-cap not breached" false (Breach.breached book_cap);
      Alcotest.check float_eq "book-cap two-thirds utilised" (2.0 /. 3.0)
        (Limits.utilisation book_cap);
      (* Equity is at its recorded peak, so there is no drawdown yet. *)
      let dd_cap = find_breach s "dd-cap" in
      Alcotest.(check bool)
        "dd-cap not breached at the peak" false (Breach.breached dd_cap);
      Alcotest.check float_eq "no drawdown at the peak" 0.0 (Breach.observed dd_cap);
      Alcotest.(check int)
        "exactly three limits are breached" 3
        (List.length (Graph.Snapshot.breached s));
      Alcotest.(check string)
        "a breach renders with its magnitude"
        "aapl-cap [instrument:AAPL] BREACH: $30000.00 > $25000.00 (over by $5000.00)"
        (Limits.to_string aapl_cap);
      Alcotest.(check string)
        "a pass renders with its headroom"
        "msft-cap [instrument:MSFT] ok: $30000.00 <= $50000.00 (headroom $20000.00)"
        (Limits.to_string msft_cap))
    ()

(* The circuit breaker. Equity peaks at 120,000 and is marked there; halving the
   AAPL position drops net exposure by 15,000, so equity is 105,000 and the
   drawdown from the recorded peak is 15,000 / 120,000 = 0.125, past the 10%
   limit. *)
let test_drawdown_breaker () =
  with_graph
    ~f:(fun graph _ ->
      Graph.set_qty graph aapl (Qty.of_float 100.0);
      let s = Graph.snapshot graph in
      notional_eq "equity fell to 105,000" 105_000.0 (Graph.Snapshot.equity s);
      Alcotest.check float_eq "drawdown = 15,000 / 120,000" 0.125
        (Graph.Snapshot.current_drawdown s);
      let dd_cap = find_breach s "dd-cap" in
      Alcotest.(check bool) "dd-cap breached" true (Breach.breached dd_cap);
      Alcotest.check float_eq "over the 10% line by 2.5 points" 0.025
        (Breach.excess dd_cap);
      Alcotest.(check string)
        "renders as a percentage, not as dollars"
        "dd-cap [portfolio] BREACH: 12.50% > 10.00% (over by 2.50%)"
        (Limits.to_string dd_cap))
    ()

(* Before any return history exists there is no distribution to take a quantile
   of. The engine says "unknown" rather than "zero", and the VaR limit is
   reported as unevaluated rather than quietly listed among the limits that are
   fine. *)
let test_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph _ ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let s = Graph.snapshot graph in
      Alcotest.(check bool) "warming up" true (Graph.Snapshot.warming_up s);
      Alcotest.check opt_float "no VaR yet" None (Graph.Snapshot.historical_var s);
      Alcotest.check opt_float "no ES yet" None (Graph.Snapshot.expected_shortfall s);
      Alcotest.check opt_float "no parametric VaR yet" None
        (Graph.Snapshot.parametric_var s);
      Alcotest.(check (list string))
        "the VaR limit is reported as unevaluated, not as passing" [ "var-cap" ]
        (Graph.Snapshot.unevaluated_limits s);
      Alcotest.(check bool)
        "and it is absent from the evaluated results" false
        (List.exists (Graph.Snapshot.breaches s) ~f:(fun b ->
             String.equal (Limit.name (Breach.limit b)) "var-cap")))
    ()

(* ------------------------------------------------------------------------ *)
(* 4. Fills, cash and quantity handling                                      *)
(* ------------------------------------------------------------------------ *)

(* A fill at the current mark moves cash and position by equal and opposite
   amounts, so equity does not move. If this ever fails, the engine is booking a
   phantom profit or loss at the moment of execution. *)
let test_fill_conserves_equity () =
  with_graph ~seed:false
    ~f:(fun graph _ ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.stabilize graph;
      notional_eq "starts as cash only" 100_000.0 (Graph.equity graph);
      Graph.apply_fill graph
        {
          Fill.symbol = aapl;
          qty = Qty.of_float 100.0;
          price = Price.of_float 150.0;
          time = Time.epoch;
        };
      Graph.stabilize graph;
      notional_eq "cash paid out" 85_000.0 (Graph.cash graph);
      Alcotest.check float_eq "position acquired" 100.0
        (Qty.to_float (Graph.qty graph aapl));
      notional_eq "equity unchanged by the fill itself" 100_000.0 (Graph.equity graph);
      (* Now the mark moves: 100 shares x $10 = $1,000 of P&L. *)
      Graph.set_price graph aapl (Price.of_float 160.0);
      Graph.stabilize graph;
      notional_eq "equity tracks the mark" 101_000.0 (Graph.equity graph);
      (* Selling it all returns the cash plus the profit and flattens the book. *)
      Graph.apply_fill graph
        {
          Fill.symbol = aapl;
          qty = Qty.of_float (-100.0);
          price = Price.of_float 160.0;
          time = Time.epoch;
        };
      Graph.stabilize graph;
      Alcotest.check float_eq "flat" 0.0 (Qty.to_float (Graph.qty graph aapl));
      notional_eq "profit realised into cash" 101_000.0 (Graph.cash graph);
      notional_eq "equity unchanged by realising" 101_000.0 (Graph.equity graph))
    ()

(* The return window is bounded: pushing past its length drops the oldest
   observation rather than growing without limit. *)
let test_return_window_is_bounded () =
  with_graph
    ~f:(fun graph _ ->
      Graph.push_return graph aapl 0.11;
      Graph.push_return graph aapl 0.12;
      let window = Graph.returns graph aapl in
      Alcotest.(check int) "still ten observations" 10 (Array.length window);
      Alcotest.check float_eq "oldest two dropped" (-0.03) window.(0);
      Alcotest.check float_eq "newest kept" 0.12 window.(9))
    ()

(* Bulk seeding longer than the window keeps the most recent observations, not
   the first ones -- the opposite would silently feed the engine stale
   history. *)
let test_set_returns_keeps_the_recent_tail () =
  with_graph
    ~f:(fun graph _ ->
      Graph.set_returns graph aapl (Array.init 25 ~f:(fun i -> float_of_int i /. 1000.0));
      let window = Graph.returns graph aapl in
      Alcotest.(check int) "trimmed to the window" 10 (Array.length window);
      Alcotest.check float_eq "starts at observation 15" 0.015 window.(0);
      Alcotest.check float_eq "ends at observation 24" 0.024 window.(9))
    ()

(* ------------------------------------------------------------------------ *)
(* 5. Construction-time validation                                           *)
(* ------------------------------------------------------------------------ *)

(* Every one of these is rejected before a single node exists, which is what
   lets the breach node bodies be total. A node that raises during stabilization
   takes the whole graph with it. *)

let check_invalid msg f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" msg (Exn.to_string e)
  | () -> Alcotest.failf "%s: expected Invalid_argument, but it was accepted" msg

let make ?(instruments = book) ?(limits = book_limits) ?(confidence = 0.95)
    ?(return_window = 10) () =
  Graph.destroy (Graph.create ~instruments ~limits ~confidence ~return_window ())

let test_validation () =
  check_invalid "empty book" (fun () -> make ~instruments:[] ());
  check_invalid "duplicate instrument" (fun () ->
      make ~instruments:(book @ [ { Instrument.symbol = aapl; sector = energy } ]) ());
  check_invalid "confidence of 1" (fun () -> make ~confidence:1.0 ());
  check_invalid "confidence of 0" (fun () -> make ~confidence:0.0 ());
  check_invalid "single-observation window" (fun () -> make ~return_window:1 ());
  check_invalid "duplicate limit name" (fun () ->
      make ~limits:(book_limits @ [ List.hd_exn book_limits ]) ());
  check_invalid "limit on an unknown symbol" (fun () ->
      make
        ~limits:
          [
            limit "ghost"
              (Limit.Instrument (sym "NVDA"))
              (Limit.Gross_notional (dollars 1.0));
          ]
        ());
  check_invalid "limit on an unknown sector" (fun () ->
      make
        ~limits:
          [
            limit "ghost"
              (Limit.Sector (sec "UTILITIES"))
              (Limit.Gross_notional (dollars 1.0));
          ]
        ());
  (* VaR and drawdown are portfolio statistics and this engine has no per-name
     version of either, so the scoping is refused rather than answered with the
     book-level number. *)
  check_invalid "instrument-scoped VaR" (fun () ->
      make
        ~limits:
          [ limit "bad" (Limit.Instrument aapl) (Limit.Value_at_risk (dollars 1.0)) ]
        ());
  check_invalid "sector-scoped drawdown" (fun () ->
      make ~limits:[ limit "bad" (Limit.Sector tech) (Limit.Max_drawdown 0.1) ] ());
  check_invalid "drawdown above 100%" (fun () ->
      make ~limits:[ limit "bad" Limit.Portfolio (Limit.Max_drawdown 1.5) ] ());
  check_invalid "drawdown of zero" (fun () ->
      make ~limits:[ limit "bad" Limit.Portfolio (Limit.Max_drawdown 0.0) ] ());
  check_invalid "negative notional cap" (fun () ->
      make
        ~limits:[ limit "bad" Limit.Portfolio (Limit.Gross_notional (dollars (-1.0))) ]
        ());
  check_invalid "empty limit name" (fun () ->
      make ~limits:[ limit "" Limit.Portfolio (Limit.Gross_notional (dollars 1.0)) ] ())

(* A feed pushing a symbol the book does not hold means the subscription and the
   position set have diverged. That is not a routine event, so it is loud. *)
let test_unknown_symbol_is_loud () =
  with_graph
    ~f:(fun graph _ ->
      Alcotest.(check bool) "knows AAPL" true (Graph.knows_symbol graph aapl);
      Alcotest.(check bool)
        "does not know NVDA" false
        (Graph.knows_symbol graph (sym "NVDA"));
      match Graph.set_price graph (sym "NVDA") (Price.of_float 1.0) with
      | exception _ -> ()
      | () -> Alcotest.fail "expected an exception for an unknown symbol")
    ()

(* ------------------------------------------------------------------------ *)
(* Risk attribution                                                          *)
(* ------------------------------------------------------------------------ *)

(* The standard book is an unusually clean case for a decomposition, and that
   is why the numbers below can be written down rather than captured.

   AAPL and MSFT carry [base_returns]; XOM carries its negation. So every pair
   is perfectly correlated in magnitude -- rho = +1 within TECH, rho = -1
   against ENERGY -- and the covariance matrix is 0.0011 times a matrix of
   plus and minus ones. With w = [0.3; 0.3; -0.4]:

     Sigma w   = 0.0011 * [1; 1; -1]
     w' Sigma w= 0.0011 * (0.3 + 0.3 + 0.4) = 0.0011      <- the base variance
     sigma_p   = sqrt(0.0011) = 0.033166247903554
     marginal  = [sigma_p; sigma_p; -sigma_p]
     component = [0.3; 0.3; 0.4] * sigma_p
     percent   = [0.3; 0.3; 0.4]

   The percentages are the WEIGHT MAGNITUDES, exactly. That is what perfect
   correlation means: the book is one bet, so each position's share of the risk
   is just its share of the money. Any real book would land somewhere below
   this, and this is the ceiling.

   Two things are worth reading twice. XOM's marginal is NEGATIVE -- adding
   dollars to a short that is perfectly anti-correlated with the longs would
   reduce sigma_p -- while its component is POSITIVE, because the weight is
   negative too and the two signs cancel. That is not a contradiction: the
   marginal answers "what happens if I add to it" and the component answers
   "how much of the current risk is it", and on a short position those have
   different signs. Confusing the two is how a risk report ends up recommending
   that a desk increase a position in order to reduce risk.

   And the diversification ratio is exactly 1.0: standalone risks sum to
   (0.3 + 0.3 + 0.4) * sigma_p = sigma_p. There is nothing to diversify in a
   book whose three names are the same trade. *)
let sigma_p = 0.033166247903554

let test_attribution () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      let shares =
        match Graph.Snapshot.component_var_by_instrument s with
        | Some shares -> shares
        | None -> Alcotest.fail "component VaR should be available on a seeded book"
      in
      (* Component VaR in dollars: z95 * component_fraction * gross, gross being
         100,000. z95 * 0.3 * sigma_p * 100000 = 1636.6086946959736. *)
      notional_eq "AAPL is 30% of the risk" 1636.6086946959736 (Map.find_exn shares aapl);
      notional_eq "MSFT likewise" 1636.6086946959736 (Map.find_exn shares msft);
      notional_eq "XOM's short is 40% of it, and positively so" 2182.1449262612987
        (Map.find_exn shares xom);
      (* The identity that makes any of this meaningful. *)
      let total = Notional.to_float (Notional.sum (Map.data shares)) in
      Alcotest.check float_eq "the shares sum to the parametric VaR notional"
        (z95 *. sigma_p *. 100_000.0)
        total;
      let by_sector =
        match Graph.Snapshot.component_var_by_sector s with
        | Some by_sector -> by_sector
        | None -> Alcotest.fail "sector attribution should be available"
      in
      notional_eq "TECH = AAPL + MSFT, a plain sum" 3273.217389391947
        (Map.find_exn by_sector tech);
      notional_eq "ENERGY = XOM" 2182.1449262612987 (Map.find_exn by_sector energy);
      Alcotest.check opt_float "a book of one trade diversifies nothing" (Some 1.0)
        (Graph.Snapshot.diversification_ratio s))
    ()

(* Attribution is unavailable for exactly as long as the covariance matrix is,
   and reports [None] rather than zeros while it is.

   A decomposition of zeros renders as "no position is contributing risk",
   which is a claim, and a false one. Warming up is the absence of a claim. *)
let test_attribution_is_none_while_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      Alcotest.(check bool) "warming up" true (Graph.Snapshot.warming_up s);
      Alcotest.(check bool)
        "no instrument attribution" true
        (Option.is_none (Graph.Snapshot.component_var_by_instrument s));
      Alcotest.(check bool)
        "no sector attribution" true
        (Option.is_none (Graph.Snapshot.component_var_by_sector s));
      Alcotest.(check bool)
        "no diversification ratio" true
        (Option.is_none (Graph.Snapshot.diversification_ratio s)))
    ()

(* Component VaR limits, at all three scopes.

   These use their own graph rather than the standard book's limit set, because
   adding limits to [book_limits] would change the expected node lists in every
   architecture test above -- and those lists are the point of this file.

   AAPL's share is $1,636.61 and TECH's is $3,273.22, so a $2,000 cap on AAPL
   holds while a $3,000 cap on TECH does not. The portfolio-scope limit is set
   at $5,000 against the $5,455.36 total, and the fact that it breaches at
   exactly the same number the instrument shares add up to is the Euler identity
   showing through the limit layer. *)
(* The decomposition, finally observed.

   [attribution] has been recomputed on every tick since Phase 5 and nothing
   read it: two downstream nodes each took one field and the rest was
   discarded. These are the fields §02 of the page is made of, and the
   residual is the cheap self-check attribution.ml exposes precisely so it can
   stay true at run time rather than only in a test.

   Hand-derived from the standard book. The weights are 0.3, 0.3, -0.4 and the
   covariance is built from [base_returns] with XOM's series negated, so every
   pair is perfectly +/-1 correlated and sigma_i = sqrt(0.0011) for all three.
   Perfect correlation makes the book behave like a single asset:
     sigma_p = |0.3 + 0.3 + 0.4| * sqrt(0.0011) = sqrt(0.0011)
   because XOM's -0.4 weight against a -1 correlated series adds rather than
   cancels. So each marginal is +/- sqrt(0.0011) with the sign of the name's
   own co-movement, each standalone is |w_i| * sqrt(0.0011), and the
   standalones sum to exactly sigma_p -- a diversification ratio of 1.00,
   which is what "correlations at one" means and is the degenerate case worth
   pinning because every other ratio is a departure from it. *)
let test_attribution_fields () =
  with_graph
    ~f:(fun graph _ ->
      let s = Graph.snapshot graph in
      let sigma = Float.sqrt base_variance in
      let marginal =
        Option.value_exn
          (Graph.Snapshot.marginal_by_instrument s)
          ~message:"marginal is present on a seeded book"
      in
      let standalone =
        Option.value_exn
          (Graph.Snapshot.standalone_by_instrument s)
          ~message:"standalone is present on a seeded book"
      in
      Alcotest.(check (list string))
        "keyed by every symbol, none missing" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Map.keys marginal) ~f:Symbol.to_string);
      Alcotest.(check (list string))
        "and the standalone map the same" [ "AAPL"; "MSFT"; "XOM" ]
        (List.map (Map.keys standalone) ~f:Symbol.to_string);
      Alcotest.check float_eq "marginal(AAPL) = sigma_p" sigma
        (Map.find_exn marginal aapl);
      (* XOM's series is the negation, and its weight is negative, so its
         marginal is negative and its COMPONENT -- weight times marginal -- is
         positive. A hedge is the case where those two signs differ; this book
         does not have one, and test_a_hedge_does_not_breach_a_risk_limit is
         where that case lives. *)
      Alcotest.check float_eq "marginal(XOM) = -sigma_p" (-.sigma)
        (Map.find_exn marginal xom);
      Alcotest.check float_eq "standalone(AAPL) = |w| sigma" (0.3 *. sigma)
        (Map.find_exn standalone aapl);
      Alcotest.check float_eq "standalone(XOM) = 0.4 sigma" (0.4 *. sigma)
        (Map.find_exn standalone xom);
      (* Euler is exact in real arithmetic; anything beyond accumulation error
         means the matrix and the weights have gone out of alignment, which is
         the one failure of that module that produces confident, plausible,
         entirely wrong attributions. *)
      let residual =
        Option.value_exn
          (Graph.Snapshot.euler_residual s)
          ~message:"the residual is present on a seeded book"
      in
      Alcotest.(check bool)
        (Printf.sprintf "|Euler residual| < 1e-9 (got %g)" residual)
        true
        (Float.( < ) (Float.abs residual) 1e-9);
      Alcotest.(check string)
        "the snapshot says which matrix was decomposed" "equal_weighted"
        (Graph.Covariance_estimator.to_string
           (Graph.Snapshot.covariance_for_attribution s));
      (* The marks and positions the exposures above came from, read out of the
         same fixed point rather than fetched afterwards through a getter. *)
      Alcotest.check float_eq "price[AAPL] on the snapshot" 150.0
        (Price.to_float (Map.find_exn (Graph.Snapshot.prices s) aapl));
      Alcotest.check float_eq "qty[XOM] keeps its sign" (-400.0)
        (Qty.to_float (Map.find_exn (Graph.Snapshot.quantities s) xom)))
    ()

(* Unknown, never zero. A book with no return history has no covariance to
   decompose, and a marginal of 0.0 renders on a page as "this position
   contributes no risk" -- the single most dangerous wrong answer this system
   can give, which risk_metrics.ml names in as many words. *)
let test_attribution_fields_are_none_while_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph _ ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let s = Graph.snapshot graph in
      Alcotest.(check bool)
        "marginal is None" true
        (Option.is_none (Graph.Snapshot.marginal_by_instrument s));
      Alcotest.(check bool)
        "standalone is None" true
        (Option.is_none (Graph.Snapshot.standalone_by_instrument s));
      Alcotest.(check bool)
        "and so is the residual" true
        (Option.is_none (Graph.Snapshot.euler_residual s)))
    ()

(* The new observer must be invisible to everything that was already measured.

   It adds no named node -- [attribution] was already named and already ran --
   and it carries NO update handler, so it does not wake the change listeners.
   That second half is the one worth a test: server.ml's broadcaster and
   history_buffer.ml both hang off those listeners, and an extra one firing per
   tick would move the history ring's appended count and the frame rate without
   anything on the book having changed. *)
let test_the_attribution_observer_is_silent () =
  with_graph
    ~f:(fun graph recorder ->
      let wakeups = ref 0 in
      Graph.on_change graph ~f:(fun () -> incr wakeups);
      (* Same tick [test_price_tick_is_local] pins, not a fresh one: a price
         RISE leaves drawdown at 0.0 under its cutoff, so [limit:dd-cap] would
         legitimately sit out and an upward tick would end up testing that
         cutoff instead of the observer added here. *)
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 120.0; time = Time.epoch };
      Graph.stabilize graph;
      (* The pinned set, unchanged. This is the assertion that says the new
         observer bought a value on the wire and cost nothing on the tick
         path. *)
      check_recomputed recorder
        ~msg:"a tick recomputes exactly what it did before the attribution observer"
        ~expected:downstream_of_aapl_tick;
      (* One wakeup per observer whose VALUE changed. The attribution observer
         has no update handler at all, so it contributes none -- and the count
         is the same as it was for the same tick before this task. *)
      Alcotest.(check bool)
        (Printf.sprintf "the tick woke listeners (%d) and the count is >0" !wakeups)
        true (!wakeups > 0);
      Alcotest.(check bool)
        "the attribution observer is readable all the same" true
        (Option.is_some (Graph.attribution graph)))
    ()

let test_component_var_limits () =
  let limits =
    [
      limit "aapl-risk" (Limit.Instrument aapl) (Limit.Component_var (dollars 2_000.0));
      limit "tech-risk" (Limit.Sector tech) (Limit.Component_var (dollars 3_000.0));
      limit "book-risk" Limit.Portfolio (Limit.Component_var (dollars 5_000.0));
    ]
  in
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
      let s = Graph.snapshot graph in
      let breach name =
        match
          List.find (Graph.Snapshot.breaches s) ~f:(fun b ->
              String.equal (Limit.name (Breach.limit b)) name)
        with
        | Some b -> b
        | None -> Alcotest.failf "limit %S was not evaluated" name
      in
      Alcotest.check float_eq "AAPL's share" 1636.6086946959736
        (Breach.observed (breach "aapl-risk"));
      Alcotest.(check bool)
        "$1,636 of risk is inside a $2,000 cap" false
        (Breach.breached (breach "aapl-risk"));
      Alcotest.check float_eq "TECH's share" 3273.217389391947
        (Breach.observed (breach "tech-risk"));
      Alcotest.(check bool)
        "$3,273 of risk is not inside a $3,000 cap" true
        (Breach.breached (breach "tech-risk"));
      Alcotest.check float_eq "the whole book's share is the parametric VaR"
        (z95 *. sigma_p *. 100_000.0)
        (Breach.observed (breach "book-risk"));
      Alcotest.(check bool)
        "and it breaches $5,000" true
        (Breach.breached (breach "book-risk")))

(* A limit on a position that REDUCES portfolio risk must not breach, however
   large the position is.

   XOM is flipped long, which makes it perfectly anti-correlated with the two
   TECH names it is now held alongside, so its component contribution goes
   negative -- holding it makes the book less volatile. A stray absolute value
   anywhere between attribution.ml and the limit node would turn that into a
   positive number and breach a risk limit for the act of hedging. This is the
   test that would catch it. *)
let test_a_hedge_does_not_breach_a_risk_limit () =
  let limits =
    [ limit "xom-risk" (Limit.Instrument xom) (Limit.Component_var (dollars 1_000.0)) ]
  in
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
      (* Long, not short: now XOM moves against the book instead of with it. *)
      Graph.set_qty graph xom (Qty.of_float 400.0);
      Graph.set_returns graph aapl base_returns;
      Graph.set_returns graph msft base_returns;
      Graph.set_returns graph xom negated_returns;
      let s = Graph.snapshot graph in
      let shares = Option.value_exn (Graph.Snapshot.component_var_by_instrument s) in
      let xom_share = Notional.to_float (Map.find_exn shares xom) in
      Alcotest.(check bool)
        (Printf.sprintf "XOM now contributes negative risk (%.2f)" xom_share)
        true (Float.is_negative xom_share);
      match Graph.Snapshot.breaches s with
      | [ b ] ->
          Alcotest.(check bool)
            "a risk-reducing position does not breach a risk limit" false
            (Breach.breached b)
      | other -> Alcotest.failf "expected one evaluated limit, got %d" (List.length other))

(* ------------------------------------------------------------------------ *)
(* Incremental's own counters, for the operations page                       *)
(* ------------------------------------------------------------------------ *)

(* These are PROCESS-wide: one Incremental state serves every graph this binary
   ever builds, including the ones stress.ml forks and destroys. So nothing
   here asserts an absolute value -- another test in this same runner would
   change it -- only the direction, which is all these wrappers promise and all
   /api/ops claims. The page says the same thing in words. *)
let test_process_counters_move () =
  let created_before = Graph.total_nodes_created () in
  let observers_before = Graph.active_observers () in
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits:book_limits
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      Alcotest.(check bool)
        "building a graph creates nodes" true
        (Graph.total_nodes_created () > created_before);
      Alcotest.(check bool)
        "and makes observers active" true
        (Graph.active_observers () > observers_before);
      (* set_price is exactly one Inc.Var.set (graph.ml:1508), so this one is
         an equality rather than a direction: if it ever costs two, something
         is writing a cell nobody asked it to. *)
      let sets_before = Graph.total_var_sets () in
      Graph.set_price graph aapl (Price.of_float 150.0);
      Alcotest.(check int)
        "one set_price is one var set" (sets_before + 1) (Graph.total_var_sets ()))
    ~finally:(fun () -> Graph.destroy graph)

(* The threshold is configuration, not state, and /api/ops publishes it so a
   reader can tell 40 s of silence from stale. It has to come back out of the
   graph it went into: the demo host runs at 20 s and the live host at 90, and
   a page that assumed one of them would call the other host broken. *)
let test_staleness_threshold_round_trips () =
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book ~limits:book_limits
      ~confidence:0.95 ~return_window:10 ~staleness_threshold:(Time.Span.of_sec 20.0) ()
  in
  Exn.protect
    ~f:(fun () ->
      Alcotest.check float_eq "20 s in, 20 s out" 20.0
        (Time.Span.to_sec (Graph.staleness_threshold graph)))
    ~finally:(fun () -> Graph.destroy graph)

let suite =
  ( "graph",
    [
      Alcotest.test_case "exposures, sectors and weights" `Quick test_exposures;
      Alcotest.test_case "VaR, ES and parametric VaR" `Quick test_risk_numbers;
      Alcotest.test_case "covariance matrix" `Quick test_covariance_matrix;
      Alcotest.test_case "risk attribution across instruments and sectors" `Quick
        test_attribution;
      Alcotest.test_case "attribution is unknown, not zero, while warming up" `Quick
        test_attribution_is_none_while_warming_up;
      Alcotest.test_case "the attribution snapshot fields" `Quick test_attribution_fields;
      Alcotest.test_case "attribution fields are None while warming up" `Quick
        test_attribution_fields_are_none_while_warming_up;
      Alcotest.test_case "the attribution observer adds no node and no listener" `Quick
        test_the_attribution_observer_is_silent;
      Alcotest.test_case "component VaR limits at every scope" `Quick
        test_component_var_limits;
      Alcotest.test_case "a hedge does not breach a risk limit" `Quick
        test_a_hedge_does_not_breach_a_risk_limit;
      Alcotest.test_case "portfolio beta against a macro factor" `Quick
        test_portfolio_beta;
      Alcotest.test_case "beta is total: a flat factor gives None, never raises" `Quick
        test_portfolio_beta_is_total;
      Alcotest.test_case "beta aligns the two series at the recent edge" `Quick
        test_beta_aligns_at_the_recent_edge;
      Alcotest.test_case "input cells are named with brackets" `Quick test_input_names;
      Alcotest.test_case "every node name has a unit" `Quick test_units;
      Alcotest.test_case "scalar units are the ones with a number" `Quick
        test_scalar_units;
      Alcotest.test_case "every node the hook names is named on the node" `Quick
        test_every_node_is_labelled;
      Alcotest.test_case "reading the label table runs nothing" `Quick
        test_reading_labels_costs_nothing;
      Alcotest.test_case "TOPOLOGY: closures equal the pinned recomputation sets" `Quick
        test_topology_closures_match_the_pinned_sets;
      Alcotest.test_case "TOPOLOGY: edges join named nodes and nothing is lost" `Quick
        test_topology_is_closed_over_its_names;
      Alcotest.test_case "TOPOLOGY: families and the typed fields beside them" `Quick
        test_topology_families;
      Alcotest.test_case "TOPOLOGY: an instrument cap has one input" `Quick
        test_a_limit_has_one_input;
      Alcotest.test_case "TOPOLOGY: no risk node is downstream of now" `Quick
        test_no_risk_node_is_downstream_of_now;
      Alcotest.test_case "TOPOLOGY: the named-node count is a formula in the book" `Quick
        test_named_node_count_formula;
      Alcotest.test_case "TOPOLOGY: observed flags and monotone ranks" `Quick
        test_observed_flags_and_ranks;
      Alcotest.test_case "TOPOLOGY: asking for it costs nothing" `Quick
        test_topology_costs_nothing;
      Alcotest.test_case "TOPOLOGY: a fork's observers are released by destroy" `Quick
        test_a_forks_observers_are_released;
      Alcotest.test_case "ARCHITECTURE: a position change recomputes only its dependents"
        `Quick test_position_change_is_local;
      Alcotest.test_case "ARCHITECTURE: a price tick recomputes only its dependents"
        `Quick test_price_tick_is_local;
      Alcotest.test_case "ARCHITECTURE: no price change can reach the covariance matrix"
        `Quick test_prices_never_reach_covariance;
      Alcotest.test_case "ARCHITECTURE: new returns leave the exposure side alone" `Quick
        test_return_push_is_local;
      Alcotest.test_case "ARCHITECTURE: an unchanged input recomputes nothing" `Quick
        test_idempotent_input_costs_nothing;
      Alcotest.test_case "ARCHITECTURE: propagation stops where values stop changing"
        `Quick test_cutoff_stops_dead_ends;
      Alcotest.test_case "ARCHITECTURE: the staleness clock cannot reach a risk node"
        `Quick test_clock_cannot_reach_the_risk_chain;
      Alcotest.test_case "never-seen and stale are different states" `Quick
        test_never_seen_is_not_staleness;
      Alcotest.test_case "limit breaches" `Quick test_breaches;
      Alcotest.test_case "drawdown circuit breaker" `Quick test_drawdown_breaker;
      Alcotest.test_case "warming up reports unknown, not zero" `Quick test_warming_up;
      Alcotest.test_case "a fill conserves equity" `Quick test_fill_conserves_equity;
      Alcotest.test_case "the return window is bounded" `Quick
        test_return_window_is_bounded;
      Alcotest.test_case "bulk-seeded returns keep the recent tail" `Quick
        test_set_returns_keeps_the_recent_tail;
      Alcotest.test_case "construction-time validation" `Quick test_validation;
      Alcotest.test_case "an unknown symbol is loud" `Quick test_unknown_symbol_is_loud;
      Alcotest.test_case "Incremental's process counters move" `Quick
        test_process_counters_move;
      Alcotest.test_case "the staleness threshold comes back out" `Quick
        test_staleness_threshold_round_trips;
    ] )
