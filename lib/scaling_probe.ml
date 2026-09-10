(* How the cost of one tick moves as the book grows.

   The percentage `make run` prints is a floor, and a small book is the worst
   case for it: with six names the portfolio-level nodes -- gross, net,
   weights, the three risk numbers -- genuinely depend on everything, so they
   dominate the tally and there is not much left to skip.

   The interesting quantity is how the cost of ONE tick moves as the book
   grows. In a poll-and-recompute engine it grows with the book, because
   everything is redone. Here it does not move at all: a tick still touches one
   instrument, one sector, the aggregates, and the limits that read them. This
   probe measures exactly that, at whatever book sizes it is asked for.

   WHY THE STATE IS A PARAMETER. The CLI draws every mode from one shared
   Random.State in program order, and its printed table -- 25.6 / 25.2 / 26.0
   -- is the average over a path drawn from wherever that stream had reached
   after sixty synthetic events. The served process has no such stream and
   runs this at startup, from a seed of its own; [rows] builds that state and
   never touches anyone else's. The two therefore agree on [named_nodes]
   exactly and on [nodes_per_tick] to within the drawdown node's coin flips,
   which is what the page's computed-vs-quoted line is for.

   WHY THE GRAPH IS DESTROYED IN Exn.protect. Incremental's state is shared by
   every Graph.t in the process. A probe graph left alive after a raise would
   recompute on every stabilize of the served graph forever, and the symptom
   -- a process-wide counter climbing for no reason -- is exactly the number
   the smoke suite reads to decide the deploy is alive. *)

open Core
open Types

type row = {
  instrument_count : int;
  (* Every named node ran at least once during seeding, so this is the size of
     the graph -- and therefore what a polling engine would redo per event. *)
  named_nodes : int;
  nodes_per_tick : float;
  (* Wall clock, this process, this machine. Real, and labelled as what it is
     wherever it is shown; never compared against the README's bench table,
     which ran under core_bench on named hardware. *)
  ns_per_tick : float;
}

let default_sizes = [ 10; 100; 400 ]
let default_ticks = 50

let probe ~(rng : Random.State.t) ~(instrument_count : int) ~(ticks : int) : row =
  if ticks < 1 then invalid_argf "scaling_probe: need at least one tick, got %d" ticks ();
  let log = Recompute_log.create () in
  let symbols =
    List.init instrument_count ~f:(fun i -> Symbol.of_string (Printf.sprintf "SYM%04d" i))
  in
  let instruments =
    List.map symbols ~f:(fun symbol ->
        (* Ten names per sector, so the sector nodes are neither degenerate (one
           member each) nor a single bucket holding the whole book. *)
        {
          Instrument.symbol;
          sector =
            Sector.of_string
              (Printf.sprintf "SEC%03d"
                 (Int.of_string (String.drop_prefix (Symbol.to_string symbol) 3) / 10));
        })
  in
  (* One cap per name, as a real book has, plus the three portfolio limits. The
     per-name limits are the part that a polling engine re-evaluates in full on
     every tick and that this one leaves untouched. *)
  let limits =
    List.map symbols ~f:(fun symbol ->
        Synthetic_book.limit
          ("cap-" ^ Symbol.to_string symbol)
          (Limit.Instrument symbol)
          (Limit.Gross_notional (Notional.of_float 100_000.0)))
    @ [
        Synthetic_book.limit "book-cap" Limit.Portfolio
          (Limit.Gross_notional (Notional.of_float 1e9));
        Synthetic_book.limit "var-cap" Limit.Portfolio
          (Limit.Value_at_risk (Notional.of_float 1e9));
        Synthetic_book.limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.5);
      ]
  in
  let graph =
    Graph.create ~on_compute:(Recompute_log.note log)
      ~starting_cash:Synthetic_book.starting_cash ~instruments ~limits
      ~confidence:Synthetic_book.confidence ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let prices = Symbol.Table.create () in
      List.iter symbols ~f:(fun symbol ->
          Hashtbl.set prices ~key:symbol ~data:100.0;
          Graph.set_price graph symbol (Price.of_float 100.0);
          Graph.set_qty graph symbol (Qty.of_float 100.0);
          Graph.set_returns graph symbol
            (Array.init Synthetic_book.return_window ~f:(fun _ ->
                 Synthetic_book.daily_return ~rng)));
      Graph.stabilize graph;
      Graph.mark_equity graph;
      Graph.stabilize graph;
      let named_nodes = Recompute_log.distinct log in
      (* Construction and seeding are not ticks; do not charge them. *)
      ignore (Recompute_log.drain log : (string * int) list);
      let total = ref 0 in
      let ring = Array.of_list symbols in
      let started = Time.now () in
      for i = 1 to ticks do
        let symbol = ring.(i % Array.length ring) in
        let price =
          Hashtbl.find_exn prices symbol
          *. (1.0 +. Synthetic_book.gaussian ~rng ~sigma:0.004)
        in
        Hashtbl.set prices ~key:symbol ~data:price;
        Graph.set_price graph symbol (Price.of_float price);
        Graph.stabilize graph;
        total :=
          !total
          + List.fold (Recompute_log.drain log) ~init:0 ~f:(fun acc (_, n) -> acc + n)
      done;
      let elapsed_ns = Time.Span.to_int_ns (Time.diff (Time.now ()) started) in
      {
        instrument_count;
        named_nodes;
        nodes_per_tick = float_of_int !total /. float_of_int ticks;
        ns_per_tick = float_of_int elapsed_ns /. float_of_int ticks;
      })

(* One state for the whole table, consumed size by size, as the CLI's shared
   state is. Never a global: the served process calls this once at startup and
   the demo feed must not find its draws shifted by it. *)
let rows ~(seed : int) ~(sizes : int list) ~(ticks : int) : row list =
  let rng = Random.State.make [| seed |] in
  List.map sizes ~f:(fun instrument_count -> probe ~rng ~instrument_count ~ticks)
