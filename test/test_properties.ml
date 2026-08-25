(* Property-based tests for the risk identities.

   This file is deliberately separate from the example-based suites, and both
   stay in the runner, because they establish different things and neither
   subsumes the other.

   An example test says "on THIS book, with these hand-derived numbers, the
   answer is 0.111803398874989". It is falsifiable by inspection, it documents
   the arithmetic, and it is what catches a formula that has been mistyped. What
   it cannot do is tell you whether the identity it checks holds anywhere except
   at the point it was checked -- and the four tests this project is proudest of
   are all identities, not values.

   So the six properties below are the generalisations of those four -- Euler
   additivity is checked twice, in volatility units and again in VaR units,
   because it is the second one that makes an instrument-scoped Component_var
   limit well posed -- plus one with no ancestor:

     EULER ADDITIVITY      test_attribution.ml asserts the components sum to the
                           total on two hand-built matrices. Here it is asserted
                           over arbitrary positive-semidefinite covariance
                           matrices and arbitrary signed weights.

     THE HEDGE             test_graph.ml asserts that one specific risk-reducing
                           position does not breach a risk limit. Here a hedge
                           is CONSTRUCTED for a randomly generated book -- a
                           position whose returns are the negation of that
                           book's own -- and the claim is the general one: it
                           lowers portfolio volatility and its risk contribution
                           is negative. A stray absolute value anywhere in the
                           chain fails this on almost every generated case.

     LOOKAHEAD             test_var_backtest.ml rebuilds one fixed set of
                           rolling windows independently. Here the window size,
                           the series length and the estimator all vary.

     FORK ISOLATION        test_stress.ml runs the fixed scenario suite and
                           compares the live snapshot. Here the scenarios
                           themselves are generated -- random shock kinds,
                           magnitudes and targets, compounded in random order.

     VAR MONOTONICITY      new, and the one property with no example-based
                           ancestor. Both VaR estimators must be non-decreasing
                           in the confidence level. It is obvious, which is
                           exactly why nobody writes the test, and it is the
                           invariant that an off-by-one in the tail rank breaks.

   WHAT A GENERATOR HAS TO BE HONEST ABOUT

   A covariance matrix of random entries is not a covariance matrix -- it is
   almost never positive semidefinite, and feeding one to a decomposition
   produces failures that are artefacts of the generator rather than of the
   code. So [gen_book] generates random RETURN SERIES and runs them through
   Risk_metrics.covariance_matrix, which is PSD by construction and is also the
   only way this engine ever obtains a covariance matrix. The generator produces
   the kind of input the system actually sees.

   Trial counts default to 100 per property and can be raised with
   QCHECK_TRIALS for a longer local run. The environment read lives here and
   nowhere in lib/, because a library that changes behaviour based on the
   environment is exactly the invisible dependency graph.ml forbids. *)

open Core
module Gen = QCheck2.Gen
module RM = Ohcamel.Risk_metrics
module Attribution = Ohcamel.Attribution
module Var_backtest = Ohcamel.Var_backtest
module Graph = Ohcamel.Graph
module Stress = Ohcamel.Stress
module Shock = Ohcamel.Stress.Shock
module Scenario = Ohcamel.Stress.Scenario
open Ohcamel.Types

let trials =
  match Sys.getenv "QCHECK_TRIALS" with
  | Some s -> ( try Int.max 1 (Int.of_string s) with _ -> 100)
  | None -> 100

let prop ~name ?print gen f =
  QCheck_alcotest.to_alcotest ~speed_level:`Quick
    (QCheck2.Test.make ~count:trials ~name ?print gen f)

(* ------------------------------------------------------------------------ *)
(* Generators                                                                *)
(* ------------------------------------------------------------------------ *)

(* Daily returns of a plausible magnitude. Bounded rather than unbounded on
   purpose: [Gen.float] produces subnormals and values near max_float, and a
   covariance matrix built from those overflows to infinity. That would be a
   test of float arithmetic rather than of this engine, and it would fail
   loudly enough to look like a real defect. *)
let gen_return = Gen.float_range (-0.15) 0.15

(* Signed weights normalised so their magnitudes sum to one -- the same
   convention graph.ml's weights node uses, because these stand in for it.

   A generated vector can be all-zero, which normalises to nothing. That case
   falls back to equal weights rather than being discarded: a flat book is a
   real state, and the properties below should hold on it too. *)
let normalise (raw : float array) =
  let gross = Array.fold raw ~init:0.0 ~f:(fun acc x -> acc +. Float.abs x) in
  if Float.( < ) gross 1e-12 then
    Array.map raw ~f:(fun _ -> 1.0 /. float_of_int (Array.length raw))
  else Array.map raw ~f:(fun x -> x /. gross)

(* A book: n instruments, m observations each, and a weight per instrument.

   m is at least n + 2 so the sample covariance matrix has some chance of being
   non-degenerate; with fewer observations than instruments it is singular by
   construction, which is a real state the code handles by returning [None] but
   is not an interesting one to generate exclusively. *)
let gen_book =
  let open Gen in
  let* n = int_range 2 6 in
  let* m = int_range (n + 2) 30 in
  let* series = array_size (return n) (array_size (return m) gen_return) in
  let* raw = array_size (return n) (float_range (-1.0) 1.0) in
  return (normalise raw, series)

let print_book (weights, series) =
  Printf.sprintf "%d instruments x %d observations, weights [%s]" (Array.length series)
    (if Array.is_empty series then 0 else Array.length series.(0))
    (String.concat ~sep:"; "
       (Array.to_list (Array.map weights ~f:(Printf.sprintf "%.4f"))))

(* ------------------------------------------------------------------------ *)
(* 1. Euler additivity                                                       *)
(* ------------------------------------------------------------------------ *)

(* The identity the entire decomposition rests on: portfolio volatility is
   homogeneous of degree one in the weights, so Euler's theorem splits it
   exactly, with no residual term to absorb the error.

   Tolerance is absolute at 1e-9 against a sigma_p of order 1e-2. That is around
   seven orders of magnitude of headroom over the float accumulation a six-term
   sum can produce, and seven orders below any real misalignment between the
   weights and the matrix -- which is the failure this identity exists to catch,
   and which produces residuals of the same order as sigma_p itself, not of the
   last bit. *)
let euler_tolerance = 1e-9

let property_euler_additivity =
  prop ~name:"EULER: components sum to portfolio volatility, exactly" ~print:print_book
    gen_book (fun (weights, series) ->
      let covariance = RM.covariance_matrix series in
      match Attribution.compute ~weights ~covariance with
      (* [None] is a documented state, not a failure: a flat book or a
         degenerate matrix has no risk to divide up. Passing here rather than
         discarding the case keeps the trial count honest -- QCheck would
         otherwise report 100 trials when it had run far fewer meaningful
         ones. *)
      | None -> true
      | Some a -> Float.( < ) (Float.abs (Attribution.euler_residual a)) euler_tolerance)

(* The same identity carried into VaR units, which is the step that makes an
   instrument-scoped Component_var limit well posed.

   Parametric VaR is a constant multiple of sigma_p that does not depend on the
   weights, so scaling every component by that constant must preserve the sum.
   If it ever did not, limits.ml's argument for accepting a Component_var limit
   at instrument scope while rejecting a Value_at_risk one would be wrong -- the
   shares would no longer add to a real total. *)
let property_component_var_sums_to_portfolio_var =
  prop ~name:"EULER: component VaRs sum to the book's parametric VaR" ~print:print_book
    gen_book (fun (weights, series) ->
      let covariance = RM.covariance_matrix series in
      match Attribution.compute ~weights ~covariance with
      | None -> true
      | Some a ->
          let confidence = 0.95 in
          let total =
            Array.fold (Attribution.component_var a ~confidence) ~init:0.0 ~f:( +. )
          in
          let expected = RM.portfolio_parametric_var ~weights ~covariance ~confidence in
          Float.( < ) (Float.abs (total -. expected)) euler_tolerance)

(* ------------------------------------------------------------------------ *)
(* 2. The hedge                                                              *)
(* ------------------------------------------------------------------------ *)

(* Generalises test_graph.ml's hedge test from one fixed book to any book.

   Construction: take a generated book, compute its own return series
   r_p(t) = sum_i w_i r_i(t), and append an instrument whose series is exactly
   -r_p. Then scale the original weights by (1-h) and give the hedge weight h,
   which keeps the magnitudes summing to one so the two books are compared at
   equal gross rather than at different sizes.

   The new book's return series is then

     (1-h) r_p + h (-r_p) = (1-2h) r_p

   so its volatility is |1-2h| * sigma_p exactly, and for h in (0, 0.5) that is
   strictly smaller. The hedge's covariance with the new book is
   -(1-2h) sigma_p^2, which is negative, so its Euler contribution -- weight
   times marginal -- is negative too.

   Both halves are asserted. The volatility inequality is what a reader would
   expect a hedge to do; the negative contribution is the part that is easy to
   destroy by reflex, because VaR itself is reported as a positive magnitude and
   an absolute value applied one line too early turns a hedge into the book's
   largest risk contributor while leaving every number plausible. *)
let gen_hedged_book =
  let open Gen in
  let* weights, series = gen_book in
  (* Away from 0.5, where the book is exactly flat and sigma_p is zero, and away
     from 0, where there is no hedge to speak of. *)
  let* h = float_range 0.05 0.45 in
  return (weights, series, h)

let property_a_hedge_reduces_risk =
  prop
    ~name:"HEDGE: an offsetting position lowers volatility and contributes negatively"
    ~print:(fun (w, s, h) -> Printf.sprintf "%s, hedge weight %.4f" (print_book (w, s)) h)
    gen_hedged_book
    (fun (weights, series, h) ->
      let covariance = RM.covariance_matrix series in
      let sigma_p = RM.portfolio_stddev ~weights ~covariance in
      (* A book with no volatility has nothing to hedge, and every claim below
         divides by sigma_p. Skipped rather than asserted trivially true. *)
      if Float.( < ) sigma_p 1e-9 then true
      else begin
        let periods = Array.length series.(0) in
        let portfolio_returns =
          Array.init periods ~f:(fun t ->
              Array.foldi series ~init:0.0 ~f:(fun i acc s ->
                  acc +. (weights.(i) *. s.(t))))
        in
        let hedged_series =
          Array.append series [| Array.map portfolio_returns ~f:Float.neg |]
        in
        let hedged_weights =
          Array.append (Array.map weights ~f:(fun w -> (1.0 -. h) *. w)) [| h |]
        in
        let hedged_covariance = RM.covariance_matrix hedged_series in
        let sigma_hedged =
          RM.portfolio_stddev ~weights:hedged_weights ~covariance:hedged_covariance
        in
        let lowers_volatility = Float.( < ) sigma_hedged sigma_p in
        let contributes_negatively =
          match
            Attribution.compute ~weights:hedged_weights ~covariance:hedged_covariance
          with
          | None -> true
          | Some a ->
              let component = Attribution.component a in
              Float.( <= ) component.(Array.length component - 1) 0.0
        in
        lowers_volatility && contributes_negatively
      end)

(* ------------------------------------------------------------------------ *)
(* 3. VaR is monotone in confidence                                          *)
(* ------------------------------------------------------------------------ *)

(* A 99% VaR can never be smaller than a 95% one. Both estimators, arbitrary
   series.

   Non-strict on purpose, and for different reasons in each case. Historical VaR
   is a step function of the confidence level -- the tail rank is an integer, so
   a range of confidences maps to the same observation -- and parametric VaR is
   flat when the series has zero variance. Demanding strictness would be
   asserting something false. *)
let gen_series_and_confidences =
  let open Gen in
  let* m = int_range 2 40 in
  let* series = array_size (return m) gen_return in
  let* a = float_range 0.50 0.995 in
  let* b = float_range 0.50 0.995 in
  return (series, Float.min a b, Float.max a b)

let property_var_monotone_in_confidence =
  prop ~name:"VAR: both estimators are non-decreasing in confidence"
    ~print:(fun (s, lo, hi) ->
      Printf.sprintf "%d observations, confidence %.4f -> %.4f" (Array.length s) lo hi)
    gen_series_and_confidences
    (fun (returns, lo, hi) ->
      let historical c = RM.historical_var ~returns ~confidence:c in
      let parametric c =
        RM.parametric_var ~mean:(RM.mean returns) ~stddev:(RM.stddev returns)
          ~confidence:c
      in
      (* An absolute slack of 1e-12, not zero. The parametric branch evaluates
         Owl's inverse normal CDF at two nearby points; two confidences that are
         equal to within float representation can produce quantiles differing in
         the last bit, and a bare [<=] would report that as a monotonicity
         violation. *)
      let slack = 1e-12 in
      Float.( <= ) (historical lo) (historical hi +. slack)
      && Float.( <= ) (parametric lo) (parametric hi +. slack))

(* ------------------------------------------------------------------------ *)
(* 4. The backtest cannot see the day it is forecasting                      *)
(* ------------------------------------------------------------------------ *)

(* Generalises the fixed lookahead test across window sizes, series lengths and
   all three estimators.

   The check rebuilds every rolling window independently -- by slicing the
   original series rather than by asking [rolling] what it used -- and demands
   the forecast match. A [rolling] that handed the estimator one element too
   many would produce a different number here on any series where the extra day
   matters, which is every series with a non-constant tail.

   The realisation is checked too, and separately. A forecast built correctly
   but scored against the wrong day is the same bug wearing a different hat, and
   it is the half that an eyeball on the output would never catch. *)
let gen_backtest_shape =
  let open Gen in
  let* window = int_range 2 20 in
  let* extra = int_range 1 40 in
  let* series = array_size (return (window + extra)) gen_return in
  let* which = int_range 0 2 in
  let estimator =
    match which with
    | 0 -> Var_backtest.Estimator.Historical
    | 1 -> Var_backtest.Estimator.Parametric
    | _ -> Var_backtest.Estimator.Parametric_ewma 0.94
  in
  return (series, window, estimator)

let property_backtest_has_no_lookahead =
  prop ~name:"LOOKAHEAD: every forecast is built only from strictly prior days"
    ~print:(fun (s, w, e) ->
      Printf.sprintf "%d observations, window %d, %s" (Array.length s) w
        (Var_backtest.Estimator.to_string e))
    gen_backtest_shape
    (fun (returns, window, estimator) ->
      let confidence = 0.95 in
      let observations = Var_backtest.rolling ~returns ~window ~confidence ~estimator in
      List.for_alli observations ~f:(fun i observation ->
          let t = window + i in
          let past = Array.sub returns ~pos:(t - window) ~len:window in
          let expected =
            Var_backtest.Estimator.estimate estimator ~window:past ~confidence
          in
          Float.equal (Var_backtest.Observation.var observation) expected
          && Float.equal (Var_backtest.Observation.realised observation) returns.(t)))

(* ------------------------------------------------------------------------ *)
(* 5. A scenario leaves no trace on the live graph                           *)
(* ------------------------------------------------------------------------ *)

(* The same three-name book the stress tests use, so a failure here and a
   failure there are about the same engine. *)
let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let tech = Sector.of_string "TECH"
let energy = Sector.of_string "ENERGY"

let stress_book =
  [
    { Instrument.symbol = aapl; sector = tech };
    { Instrument.symbol = msft; sector = tech };
    { Instrument.symbol = xom; sector = energy };
  ]

let stress_limits =
  [
    {
      Limit.name = "book-cap";
      scope = Limit.Portfolio;
      kind = Limit.Gross_notional (Notional.of_float 150_000.0);
    };
    {
      Limit.name = "var-cap";
      scope = Limit.Portfolio;
      kind = Limit.Value_at_risk (Notional.of_float 6_000.0);
    };
  ]

let stress_returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]

(* Only shocks the book can actually take: symbols and sectors it holds, and a
   strictly positive volatility multiplier. Generating a shock against an unknown
   symbol would test the validation path, which already has its own example-based
   test, and would crowd out the case this property is about. *)
let gen_shock =
  let open Gen in
  let* which = int_range 0 4 in
  match which with
  | 0 ->
      let* f = float_range (-0.4) 0.4 in
      return (Shock.All f)
  | 1 ->
      let* i = int_range 0 2 in
      let* f = float_range (-0.4) 0.4 in
      return (Shock.Instrument (List.nth_exn [ aapl; msft; xom ] i, f))
  | 2 ->
      let* i = int_range 0 1 in
      let* f = float_range (-0.4) 0.4 in
      return (Shock.Sector (List.nth_exn [ tech; energy ] i, f))
  | 3 ->
      let* f = float_range (-2.0) 2.0 in
      return (Shock.Factor f)
  | _ ->
      let* k = float_range 0.25 4.0 in
      return (Shock.Volatility k)

let gen_scenarios =
  let open Gen in
  let* count = int_range 1 5 in
  let* scenarios = list_size (return count) (list_size (int_range 1 3) gen_shock) in
  return
    (List.mapi scenarios ~f:(fun i shocks ->
         Scenario.create
           ~name:(Printf.sprintf "generated-%d" i)
           ~description:"generated by test_properties.ml" shocks))

let property_a_scenario_leaves_no_trace =
  prop ~name:"ISOLATION: a generated scenario suite leaves the live graph untouched"
    ~print:(fun scenarios ->
      String.concat ~sep:" | "
        (List.map scenarios ~f:(fun s ->
             String.concat ~sep:" + " (List.map (Scenario.shocks s) ~f:Shock.to_string))))
    gen_scenarios
    (fun scenarios ->
      let graph =
        Graph.create ~starting_cash:(Notional.of_float 100_000.0) ~instruments:stress_book
          ~limits:stress_limits ~confidence:0.95 ~return_window:10 ()
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
          Graph.set_returns graph aapl stress_returns;
          Graph.set_returns graph msft stress_returns;
          Graph.set_returns graph xom (Array.map stress_returns ~f:Float.neg);
          Graph.set_factor_returns graph stress_returns;
          Graph.stabilize graph;
          let before = Graph.Snapshot.sexp_of_t (Graph.snapshot graph) in
          let outcomes = Stress.run_all ~graph ~scenarios in
          let after = Graph.Snapshot.sexp_of_t (Graph.snapshot graph) in
          (* The run has to have happened, or "unchanged" is trivially true.
             This is the assertion that would catch a generator that quietly
             produced no scenarios. *)
          List.length outcomes = List.length scenarios
          && String.equal (Sexp.to_string before) (Sexp.to_string after)))

let suite =
  ( "properties",
    [
      property_euler_additivity;
      property_component_var_sums_to_portfolio_var;
      property_a_hedge_reduces_risk;
      property_var_monotone_in_confidence;
      property_backtest_has_no_lookahead;
      property_a_scenario_leaves_no_trace;
    ] )
