(* The book and the full-recompute baseline, shared by what measures them.

   bench_graph.ml times them under core_bench and propagation.ml records them
   for the page; both must measure the same book and the same counterfactual,
   or their tables stop being comparable. Read bench_graph.ml's header for why
   this second implementation of the book's arithmetic lives here in bench/ and
   nowhere else: nothing in lib/ or bin/ can reach it. *)

open Core
open Ohcamel
open Ohcamel.Types

let confidence = 0.95
let return_window = 60

(* The same book shape bin/main.ml's scaling probe builds, so the timings below
   line up with the recomputation counts the README already publishes. Ten names
   per sector, one notional cap per name plus three portfolio limits. A
   per-name limit is exactly the thing a polling engine re-evaluates in full on
   every event and this one leaves untouched. *)
let book ~(instrument_count : int) =
  let symbols =
    List.init instrument_count ~f:(fun i -> Symbol.of_string (Printf.sprintf "SYM%04d" i))
  in
  let instruments =
    List.mapi symbols ~f:(fun i symbol ->
        {
          Instrument.symbol;
          sector = Sector.of_string (Printf.sprintf "SEC%03d" (i / 10));
        })
  in
  let limits =
    List.map symbols ~f:(fun symbol ->
        {
          Limit.name = "cap-" ^ Symbol.to_string symbol;
          scope = Limit.Instrument symbol;
          kind = Limit.Gross_notional (Notional.of_float 100_000.0);
        })
    @ [
        {
          Limit.name = "book-cap";
          scope = Limit.Portfolio;
          kind = Limit.Gross_notional (Notional.of_float 1e9);
        };
        {
          Limit.name = "var-cap";
          scope = Limit.Portfolio;
          kind = Limit.Value_at_risk (Notional.of_float 1e9);
        };
        { Limit.name = "dd-cap"; scope = Limit.Portfolio; kind = Limit.Max_drawdown 0.5 };
      ]
  in
  (symbols, instruments, limits)

(* A deterministic return series. No RNG, so two runs of the benchmark measure
   the same arithmetic and a difference between them is the machine rather than
   the data. *)
let returns_for i =
  Array.init return_window ~f:(fun t ->
      0.01 *. Float.sin (float_of_int ((i * 7) + (t * 13))))

let seeded_graph ?on_compute ~instrument_count () =
  let symbols, instruments, limits = book ~instrument_count in
  let graph =
    Graph.create ?on_compute
      ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments ~limits ~confidence ~return_window ()
  in
  List.iteri symbols ~f:(fun i symbol ->
      Graph.set_price graph symbol (Price.of_float 100.0);
      Graph.set_qty graph symbol (Qty.of_float 100.0);
      Graph.set_returns graph symbol (returns_for i));
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  (graph, Array.of_list symbols)

(* ------------------------------------------------------------------------ *)
(* The baseline: what polling would cost                                     *)
(* ------------------------------------------------------------------------ *)

(* THROWAWAY CODE. Read the header before using any of it for anything.

   This is the straightforward implementation: on every event, walk the whole
   book and recompute everything from scratch. No caching, no dependency
   tracking, no cleverness -- which is exactly what makes it the right
   comparison, because it is what the obvious version of this program does.

   It is a faithful counterfactual rather than a strawman. It does the same work
   the graph does, in the same order, using the SAME pure functions from
   risk_metrics.ml and attribution.ml -- so the difference measured below is
   attributable to incrementality alone and not to one side using a slower
   covariance routine. What it does not do is remember anything between calls,
   and that is the whole of the difference. *)
module Polled = struct
  type t = {
    symbols : Symbol.t array;
    sectors : Sector.t array;
    prices : float array;
    quantities : float array;
    returns : float array array;
    thresholds : float array;
    cash : float;
    equity_history : float array;
  }

  let create ~instrument_count =
    let symbols, instruments, _ = book ~instrument_count in
    {
      symbols = Array.of_list symbols;
      sectors = Array.of_list_map instruments ~f:Instrument.sector;
      prices = Array.create ~len:instrument_count 100.0;
      quantities = Array.create ~len:instrument_count 100.0;
      returns = Array.init instrument_count ~f:returns_for;
      thresholds = Array.create ~len:instrument_count 100_000.0;
      cash = 1_000_000.0;
      equity_history = [| 1_010_000.0 |];
    }

  (* One full recompute. Every quantity the graph publishes, rebuilt.

     The line that matters is the covariance matrix. It is O(n^2 * w) and it is
     recomputed here on every single event -- which is correct for a polling
     design, because a polling design has no way to know that a price change
     cannot have altered it. That one line is the architectural argument, priced. *)
  let recompute_everything (t : t) : unit =
    let n = Array.length t.symbols in
    let exposures = Array.init n ~f:(fun i -> t.prices.(i) *. t.quantities.(i)) in
    let gross = Array.fold exposures ~init:0.0 ~f:(fun acc x -> acc +. Float.abs x) in
    let net = Array.fold exposures ~init:0.0 ~f:( +. ) in
    let by_sector = Hashtbl.create (module String) in
    Array.iteri exposures ~f:(fun i x ->
        Hashtbl.update by_sector
          (Sector.to_string t.sectors.(i))
          ~f:(function None -> x | Some running -> running +. x));
    let weights =
      if Float.( <= ) gross 0.0 then Array.create ~len:n 0.0
      else Array.map exposures ~f:(fun x -> x /. gross)
    in
    let covariance = Risk_metrics.covariance_matrix t.returns in
    let periods = Array.length t.returns.(0) in
    let portfolio_returns =
      Array.init periods ~f:(fun p ->
          Array.foldi t.returns ~init:0.0 ~f:(fun i acc s ->
              acc +. (weights.(i) *. s.(p))))
    in
    let historical_var =
      Risk_metrics.historical_var ~returns:portfolio_returns ~confidence
    in
    let expected_shortfall =
      Risk_metrics.expected_shortfall ~returns:portfolio_returns ~confidence
    in
    let parametric_var =
      Risk_metrics.portfolio_parametric_var ~weights ~covariance ~confidence
    in
    let attribution = Attribution.compute ~weights ~covariance in
    let equity = t.cash +. net in
    let drawdown =
      Risk_metrics.current_drawdown ~equity:(Array.append t.equity_history [| equity |])
    in
    (* Every limit, re-evaluated. In the graph each of these is its own node off
       the one quantity it measures, so a tick in one name touches exactly one
       of them; here they are all redone because there is nothing that knows
       otherwise. *)
    let breaches = ref 0 in
    Array.iteri exposures ~f:(fun i x ->
        if Float.( > ) (Float.abs x) t.thresholds.(i) then incr breaches);
    if Float.( > ) gross 1e9 then incr breaches;
    if Float.( > ) (historical_var *. gross) 1e9 then incr breaches;
    if Float.( > ) drawdown 0.5 then incr breaches;
    (* Consume the results so nothing above is optimised away. *)
    ignore
      (Sys.opaque_identity (expected_shortfall, parametric_var, attribution, !breaches))

  let tick (t : t) ~(i : int) =
    let slot = i % Array.length t.prices in
    t.prices.(slot) <- t.prices.(slot) *. 1.0001;
    recompute_everything t
end
