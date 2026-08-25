(* What a tick costs, in seconds and in words.

   `make run` already proves the architectural claim in NODE-COUNT terms: the
   number of nodes recomputed per tick is flat as the book grows, while a polling
   design's is linear in it. That is the right proof of the design and it is not
   a proof of anything a latency-conscious reader cares about. A node count says
   nothing about whether a node costs a nanosecond or a millisecond, and "we
   recompute 25 nodes instead of 1,267" is not the same claim as "a tick costs
   two microseconds instead of two hundred".

   So this measures the two numbers that turn the architecture from a structural
   argument into a quantitative one:

     TIME PER TICK        wall clock, at the three book sizes the README's
                          recomputation table already uses.

     ALLOCATION PER TICK  minor and major words. On OCaml this matters
                          independently of time: allocation is what schedules
                          the GC, and a per-tick allocation that grows with the
                          book is a design that will develop a latency tail
                          under load even if its average looks fine.

   AND THE BASELINE, which is the point.

   A "recompute everything" function is implemented below, deliberately as
   throwaway code inside bench/, because the incremental-versus-polling contrast
   is otherwise a comparison against a thing nobody has measured. It is the
   honest counterfactual: what a straightforward poll-and-render risk engine
   would do on each event.

   It lives HERE and not in lib/ on purpose. bench/ is a separate dune stanza
   that does not ship in the binary, so this second implementation of the book's
   arithmetic cannot be called by accident from anywhere that matters -- which
   would be a direct violation of the rule stress.ml goes to some length to keep.
   It exists to be measured and then ignored.

   RUNNING IT

     make bench

   The numbers move with the machine. The README quotes a run and names the
   hardware, the same way the Makefile's Owl section names the compiler its bugs
   belong to. CI does not gate on these: benchmark numbers from a shared runner
   are noise wearing a lab coat. *)

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

let seeded_graph ~instrument_count =
  let symbols, instruments, limits = book ~instrument_count in
  let graph =
    Graph.create
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

(* ------------------------------------------------------------------------ *)
(* Allocation, measured separately                                           *)
(* ------------------------------------------------------------------------ *)

(* core_bench reports allocation itself, but it reports it for the whole
   benchmarked thunk under its own harness. This measures it directly across a
   long run of ticks, which is the number a reader wants: words per tick, in
   steady state, on the real graph.

   [Gc.minor_words] is a float counter of words allocated since the program
   started, so the difference across a run divided by the tick count is the
   per-tick figure. Major words are counted separately because they are the ones
   that eventually cost a major collection -- a design allocating only in the
   minor heap is one whose garbage dies young, which is the good case. *)
let allocation_per_tick ~ticks ~f =
  Gc.full_major ();
  (* Core's [Gc.minor_words] returns an int; the stdlib's returns a float. Both
     count words since program start, so the difference across the run is what
     is wanted either way -- but the conversion has to be explicit or the
     division below is integer division and every small figure rounds to zero. *)
  let before_minor = float_of_int (Gc.minor_words ()) in
  let before_major = (Gc.quick_stat ()).major_words in
  for i = 1 to ticks do
    f i
  done;
  let minor = (float_of_int (Gc.minor_words ()) -. before_minor) /. float_of_int ticks in
  let major = ((Gc.quick_stat ()).major_words -. before_major) /. float_of_int ticks in
  (minor, major)

let sizes = [ 10; 100; 400 ]
let allocation_ticks = 2_000

let report_allocations () =
  printf "\n  ALLOCATION PER TICK (words)\n";
  printf "  %s\n" (String.make 74 '-');
  printf "  %12s %14s %14s %14s %14s\n" "instruments" "incr minor" "incr major"
    "polled minor" "polled major";
  printf "  %s\n" (String.make 74 '-');
  List.iter sizes ~f:(fun instrument_count ->
      let graph, symbols = seeded_graph ~instrument_count in
      let incremental_minor, incremental_major =
        allocation_per_tick ~ticks:allocation_ticks ~f:(fun i ->
            let symbol = symbols.(i % Array.length symbols) in
            Graph.set_price graph symbol
              (Price.of_float (100.0 +. (0.01 *. float_of_int (i % 97))));
            Graph.stabilize graph)
      in
      Graph.destroy graph;
      let polled = Polled.create ~instrument_count in
      (* Far fewer ticks for the baseline at 400 names: a full recompute at that
         size is milliseconds, and 2,000 of them is a minute of waiting to
         measure a number that is already unambiguous. *)
      let polled_ticks = if instrument_count >= 400 then 100 else 500 in
      let polled_minor, polled_major =
        allocation_per_tick ~ticks:polled_ticks ~f:(fun i -> Polled.tick polled ~i)
      in
      printf "  %12d %14.0f %14.1f %14.0f %14.1f\n" instrument_count incremental_minor
        incremental_major polled_minor polled_major);
  printf "  %s\n" (String.make 74 '-')

(* ------------------------------------------------------------------------ *)
(* Time, via core_bench                                                      *)
(* ------------------------------------------------------------------------ *)

(* One benchmark per (size, engine). core_bench runs each for long enough to
   separate it from timer noise and reports a per-call time with a confidence
   interval, which is why it is worth the dependency over a hand-rolled loop --
   the hand-rolled version reports a number and no idea how much to trust it.

   The graph is built ONCE per benchmark and reused across calls, because
   construction is not what is being measured: a risk engine builds its graph at
   startup and then runs for a day. Including it would measure the wrong thing
   and would flatter the polling baseline, which has almost no construction cost
   and pays on every event instead. *)
let time_benchmarks () =
  let incremental =
    List.map sizes ~f:(fun instrument_count ->
        let graph, symbols = seeded_graph ~instrument_count in
        let i = ref 0 in
        Core_bench.Bench.Test.create
          ~name:(Printf.sprintf "incremental/%d" instrument_count) (fun () ->
            Int.incr i;
            let symbol = symbols.(!i % Array.length symbols) in
            Graph.set_price graph symbol
              (Price.of_float (100.0 +. (0.01 *. float_of_int (!i % 97))));
            Graph.stabilize graph))
  in
  let polled =
    List.map sizes ~f:(fun instrument_count ->
        let t = Polled.create ~instrument_count in
        let i = ref 0 in
        Core_bench.Bench.Test.create ~name:(Printf.sprintf "polled/%d" instrument_count)
          (fun () ->
            Int.incr i;
            Polled.tick t ~i:!i))
  in
  Command_unix.run (Core_bench.Bench.make_command (List.concat [ incremental; polled ]))

let () =
  match Sys.get_argv () with
  (* No arguments: the allocation table, then hand off to core_bench's own
     command line for the timings. core_bench owns a rich set of flags
     (-quota, -ci-absolute, ...) and wrapping them would be worse than
     forwarding them. *)
  | [| _ |] ->
      printf "\n  OhCamel -- what a tick costs\n";
      printf
        "\n\
        \  The recomputation table in `make run` counts NODES. This counts seconds and\n\
        \  words, against a throwaway poll-and-recompute baseline implemented in\n\
        \  bench/bench_graph.ml. The baseline uses the same pure functions the graph\n\
        \  does, so the difference is incrementality and not a slower covariance\n\
        \  routine.\n";
      report_allocations ();
      printf "\n  TIME PER TICK\n\n";
      (* core_bench parses argv itself, so it is invoked with a synthesised one.
         A shorter quota than the default keeps `make bench` under a minute
         while still being far longer than the timer's resolution. *)
      (* core_bench parses its own command line, so it gets a synthesised one.
         A two-second quota per benchmark keeps `make bench` around a minute
         while staying several orders of magnitude above the timer's
         resolution. Default columns: they already report time per run with a
         confidence interval, which is the reason to use this harness at all. *)
      let argv = [ "bench"; "-quota"; "2"; "-ascii" ] in
      Command_unix.run ~argv
        (Core_bench.Bench.make_command
           (List.concat
              [
                List.map sizes ~f:(fun instrument_count ->
                    let graph, symbols = seeded_graph ~instrument_count in
                    let i = ref 0 in
                    Core_bench.Bench.Test.create
                      ~name:(Printf.sprintf "incremental/%d" instrument_count) (fun () ->
                        Int.incr i;
                        let symbol = symbols.(!i % Array.length symbols) in
                        Graph.set_price graph symbol
                          (Price.of_float (100.0 +. (0.01 *. float_of_int (!i % 97))));
                        Graph.stabilize graph));
                List.map sizes ~f:(fun instrument_count ->
                    let t = Polled.create ~instrument_count in
                    let i = ref 0 in
                    Core_bench.Bench.Test.create
                      ~name:(Printf.sprintf "polled/%d" instrument_count) (fun () ->
                        Int.incr i;
                        Polled.tick t ~i:!i));
              ]))
  | _ -> time_benchmarks ()
