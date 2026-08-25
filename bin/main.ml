(* Phase 1 driver: a synthetic feed, in process, printing what the graph does.

   The point of this program is not the numbers -- test_graph.ml checks those.
   It is the rightmost column, which counts how many nodes recomputed on each
   event. The run mixes three kinds of event on purpose, because they cost
   wildly different amounts and that difference IS the architecture:

     tick   a price update for one instrument. Touches that instrument's
            exposure, its sector, the book aggregates and the limits that read
            them. Does not touch the covariance matrix.

     fill   a position change. Same footprint as a tick, plus cash.

     bar    a bar close: a fresh return for every instrument, then an equity
            mark. This is the expensive one -- it rebuilds the covariance
            matrix -- and it happens once every ten events rather than on all
            of them.

   A poll-and-recompute engine would pay the bar cost on every event. The
   summary at the end prints both numbers side by side. *)

open Core
open Ohcamel
open Ohcamel.Types

(* ------------------------------------------------------------------------ *)
(* Formatting                                                                *)
(* ------------------------------------------------------------------------ *)

let with_commas (n : float) =
  let sign = if Float.is_negative n then "-" else "" in
  let digits = Printf.sprintf "%.0f" (Float.abs n) in
  let len = String.length digits in
  let buf = Buffer.create (len + (len / 3)) in
  String.iteri digits ~f:(fun i c ->
      if i > 0 && (len - i) % 3 = 0 then Buffer.add_char buf ',';
      Buffer.add_char buf c);
  sign ^ Buffer.contents buf

let money (n : Notional.t) = "$" ^ with_commas (Notional.to_float n)
let money_opt = function None -> "--" | Some n -> money n
let pct (f : float) = Printf.sprintf "%.2f%%" (f *. 100.0)
let rule width = String.make width '-'

(* ------------------------------------------------------------------------ *)
(* The synthetic book                                                        *)
(* ------------------------------------------------------------------------ *)

let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float

(* Long technology and financials, short energy: a book with real sector
   structure and a genuine short leg, so gross and net differ and the sector
   limits have something to say. *)
let book =
  [
    (sym "AAPL", sec "TECH", 150.00, 400.0);
    (sym "MSFT", sec "TECH", 300.00, 200.0);
    (sym "NVDA", sec "TECH", 900.00, 60.0);
    (sym "JPM", sec "FINANCIALS", 200.00, 250.0);
    (sym "XOM", sec "ENERGY", 100.00, -500.0);
    (sym "CVX", sec "ENERGY", 140.00, -300.0);
  ]

let instruments =
  List.map book ~f:(fun (symbol, sector, _, _) -> { Instrument.symbol; sector })

let limit name scope kind = { Limit.name; scope; kind }

let limits =
  [
    limit "nvda-cap"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Gross_notional (dollars 55_000.0));
    limit "aapl-cap"
      (Limit.Instrument (sym "AAPL"))
      (Limit.Gross_notional (dollars 80_000.0));
    limit "tech-cap"
      (Limit.Sector (sec "TECH"))
      (Limit.Gross_notional (dollars 200_000.0));
    limit "energy-cap"
      (Limit.Sector (sec "ENERGY"))
      (Limit.Gross_notional (dollars 100_000.0));
    limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 400_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 12_000.0));
    limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.02);
    (* Two limits on risk SHARE rather than on notional, so this book exercises
       all four limit kinds and the difference between the two is visible rather
       than described.

       NVDA already has a notional cap above. This one caps its Euler share of
       portfolio VaR, and the two measure genuinely different things: trimming
       an unrelated position raises NVDA's risk share without touching its
       notional at all, and a volatility shock moves this one while leaving the
       notional cap exactly where it was. `make stress` shows precisely that --
       the vol-regime row has zero P&L, unchanged gross, and breaks the sector
       one and nothing else on the page. *)
    limit "nvda-risk"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Component_var (dollars 900.0));
    limit "tech-risk" (Limit.Sector (sec "TECH")) (Limit.Component_var (dollars 2_000.0));
  ]

let starting_cash = dollars 1_000_000.0
let default_port = 8080
let confidence = 0.95
let return_window = 60
let steps = 60
let bar_every = 10

(* ------------------------------------------------------------------------ *)
(* Synthetic market                                                          *)
(* ------------------------------------------------------------------------ *)

(* Seeded explicitly so two runs print the same numbers. A demo whose output
   changes every time cannot be compared against itself, and the first question
   anyone asks of a risk figure is "what did it say last time". *)
let rng = Random.State.make [| 2026_07_30 |]

(* Box-Muller. Returns are drawn as normal noise with a small negative drift, so
   the book tends to bleed and the drawdown breaker has something to do. *)
let gaussian ~sigma =
  let u1 = Float.max 1e-12 (Random.State.float rng 1.0) in
  let u2 = Random.State.float rng 1.0 in
  sigma *. Float.sqrt (-2.0 *. Float.log u1) *. Float.cos (2.0 *. Float.pi *. u2)

let daily_return () = gaussian ~sigma:0.012 -. 0.0008

(* ------------------------------------------------------------------------ *)
(* Recompute accounting                                                      *)
(* ------------------------------------------------------------------------ *)

(* The instrumentation this whole demo exists to display. Same hook the tests
   use: it fires once per node body per recomputation. *)
module Counter = struct
  type t = {
    mutable step : int;
    (* Live mode reports work per trade, so it needs to know how many trades had
       arrived at the previous reading. Unused in synthetic mode, which counts
       per event instead. *)
    mutable trades_at_last_snapshot : int;
    total : int String.Table.t;
  }

  let create () =
    { step = 0; trades_at_last_snapshot = 0; total = String.Table.create () }

  let on_compute t name =
    t.step <- t.step + 1;
    Hashtbl.incr t.total name

  let take_step t =
    let n = t.step in
    t.step <- 0;
    n

  let distinct_nodes t = Hashtbl.length t.total
  let grand_total t = Hashtbl.fold t.total ~init:0 ~f:(fun ~key:_ ~data acc -> acc + data)

  let hottest t ~n =
    Hashtbl.to_alist t.total
    |> List.sort ~compare:(fun (name_a, a) (name_b, b) ->
        match Int.descending a b with 0 -> String.compare name_a name_b | c -> c)
    |> fun sorted -> List.take sorted n
end

(* ------------------------------------------------------------------------ *)
(* Breach transitions                                                        *)
(* ------------------------------------------------------------------------ *)

(* Only state CHANGES are printed. A limit that has been breached for twenty
   events is not twenty pieces of news, and a feed that repeats itself is a feed
   people stop reading -- which is the failure mode an alerting system most
   needs to avoid. Phase 4 replaces this printf with a real notifier; the
   edge-triggering is the part worth getting right now. *)
let report_transitions ~(previous : String.Set.t) (snapshot : Graph.Snapshot.t) =
  let current =
    Graph.Snapshot.breached snapshot
    |> List.map ~f:(fun b -> Limit.name (Breach.limit b))
    |> String.Set.of_list
  in
  let breach_by_name =
    Graph.Snapshot.breaches snapshot
    |> List.map ~f:(fun b -> (Limit.name (Breach.limit b), b))
    |> String.Map.of_alist_exn
  in
  Set.iter (Set.diff current previous) ~f:(fun name ->
      printf "         !! %s\n" (Limits.to_string (Map.find_exn breach_by_name name)));
  Set.iter (Set.diff previous current) ~f:(fun name ->
      match Map.find breach_by_name name with
      | Some b -> printf "         .. %s\n" (Limits.to_string b)
      | None -> printf "         .. %s cleared (no longer evaluable)\n" name);
  current

(* ------------------------------------------------------------------------ *)
(* Output                                                                    *)
(* ------------------------------------------------------------------------ *)

let header () =
  printf "\n%s\n" (rule 106);
  printf "  OhCamel -- reactive risk and limits engine\n";
  printf "  Phase 1: synthetic feed, in-process Incremental graph\n";
  printf "%s\n\n" (rule 106);
  printf "  book        %d instruments across %d sectors, starting cash %s\n"
    (List.length book)
    (List.length
       (List.dedup_and_sort
          (List.map book ~f:(fun (_, s, _, _) -> s))
          ~compare:Sector.compare))
    (money starting_cash);
  printf "  limits      %d  (confidence %.0f%%, return window %d)\n" (List.length limits)
    (confidence *. 100.0) return_window;
  printf "  events      %d, with a bar close every %d\n\n" steps bar_every

let table_header () =
  printf "  %-4s %-22s %12s %12s %12s %10s %10s %8s %6s\n" "evt" "what changed" "gross"
    "net" "equity" "VaR 95%" "ES 95%" "drawdn" "nodes";
  printf "  %s\n" (rule 104)

let row ~event ~what ~(snapshot : Graph.Snapshot.t) ~nodes =
  printf "  %-4d %-22s %12s %12s %12s %10s %10s %8s %6d\n" event what
    (money (Graph.Snapshot.gross_exposure snapshot))
    (money (Graph.Snapshot.net_exposure snapshot))
    (money (Graph.Snapshot.equity snapshot))
    (money_opt (Graph.Snapshot.value_at_risk_notional snapshot))
    (money_opt (Graph.Snapshot.expected_shortfall_notional snapshot))
    (pct (Graph.Snapshot.current_drawdown snapshot))
    nodes

let sector_of symbol =
  List.find_map book ~f:(fun (s, sector, _, _) ->
      if Symbol.equal s symbol then Some sector else None)
  |> Option.value_exn

let final_report (graph : Graph.t) (counter : Counter.t) =
  let snapshot = Graph.snapshot graph in
  printf "\n%s\n  FINAL BOOK\n%s\n\n" (rule 106) (rule 106);
  printf "  %-8s %-14s %14s %10s\n" "symbol" "sector" "exposure" "weight";
  printf "  %s\n" (rule 50);
  let weights = Graph.Snapshot.weights snapshot in
  Map.iteri (Graph.Snapshot.exposure_by_instrument snapshot) ~f:(fun ~key:symbol ~data ->
      printf "  %-8s %-14s %14s %9.1f%%\n" (Symbol.to_string symbol)
        (Sector.to_string (sector_of symbol))
        (money data)
        (Map.find_exn weights symbol *. 100.0));
  printf "  %s\n" (rule 50);
  Map.iteri (Graph.Snapshot.exposure_by_sector snapshot) ~f:(fun ~key:sector ~data ->
      printf "  %-8s %-14s %14s\n" "" (Sector.to_string sector) (money data));
  (* WHERE the risk is. The exposure table above says where the MONEY is, and
     on a book with any correlation structure at all those are different
     questions with different answers -- which is the entire reason to compute
     a decomposition rather than sort by position size. *)
  (match Graph.Snapshot.component_var_by_instrument snapshot with
  | None -> ()
  | Some shares ->
      let total = Notional.to_float (Notional.sum (Map.data shares)) in
      printf "\n%s\n  WHERE THE RISK IS  (Euler decomposition of parametric VaR)\n%s\n\n"
        (rule 106) (rule 106);
      (* The two share columns side by side are the point. A name's share of the
         MONEY is its weight; its share of the RISK is its Euler component. On
         any book with correlation structure those differ, and the rightmost
         column -- risk share over money share -- is the one that says which
         positions are punching above their size. *)
      printf "  %-8s %12s %14s %12s %10s\n" "symbol" "of money" "component VaR" "of risk"
        "risk/money";
      printf "  %s\n" (rule 62);
      Map.iteri shares ~f:(fun ~key:symbol ~data ->
          let risk_share =
            if Float.equal total 0.0 then 0.0 else Notional.to_float data /. total
          in
          let money_share = Float.abs (Map.find_exn weights symbol) in
          printf "  %-8s %11.1f%% %14s %11.1f%% %10s\n" (Symbol.to_string symbol)
            (money_share *. 100.0) (money data) (risk_share *. 100.0)
            (if Float.equal money_share 0.0 then "--"
             else Printf.sprintf "%.2fx" (risk_share /. money_share)));
      printf "  %s\n" (rule 62);
      printf "  %-8s %11.1f%% %14s %11.1f%%\n" "total" 100.0
        (money (Notional.of_float total))
        100.0;
      (match Graph.Snapshot.component_var_by_sector snapshot with
      | None -> ()
      | Some by_sector ->
          (* Sector shares are a PLAIN SUM of their members' -- component risk
             is additive, which standalone risk is not -- so they belong in the
             same columns rather than in a separate table. *)
          Map.iteri by_sector ~f:(fun ~key:sector ~data ->
              printf "  %-21s %14s %11.1f%%\n" (Sector.to_string sector) (money data)
                (if Float.equal total 0.0 then 0.0
                 else 100.0 *. Notional.to_float data /. total)));
      Option.iter (Graph.Snapshot.diversification_ratio snapshot) ~f:(fun d ->
          printf
            "\n\
            \  diversification ratio %.2f -- the book carries %.0f%% of the volatility\n\
            \  its positions would if they all moved together.\n"
            d (100.0 /. d)));
  (* The two closed-form VaRs, side by side.

     Neither number is the finding. The RATIO is. They are computed from the
     identical return window through the identical formula and differ only in
     how quickly the past stops counting, so the gap between them cannot be
     anything except a statement about how fast volatility is moving right now
     -- which is not observable from either number on its own, and is precisely
     what an equal-weighted window is slowest to tell you.

     This is the same move the engine already makes with historical against
     parametric VaR: two estimators of the same quantity, kept side by side,
     with the disagreement as the diagnostic. There the gap reads on tail
     fatness. Here it reads on regime. *)
  (match
     (Graph.Snapshot.parametric_var snapshot, Graph.Snapshot.parametric_var_ewma snapshot)
   with
  | Some equal_weighted, Some ewma ->
      let gross = Notional.to_float (Graph.Snapshot.gross_exposure snapshot) in
      let lambda = Graph.Snapshot.ewma_lambda snapshot in
      printf "\n%s\n  VOLATILITY REGIME  (the same VaR, weighted two ways)\n%s\n\n"
        (rule 106) (rule 106);
      printf "  %-28s %14s %12s\n" "estimator" "parametric VaR" "of gross";
      printf "  %s\n" (rule 56);
      printf "  %-28s %14s %11.2f%%\n" "equal-weighted"
        (money (Notional.of_float (equal_weighted *. gross)))
        (equal_weighted *. 100.0);
      printf "  %-28s %14s %11.2f%%\n"
        (Printf.sprintf "EWMA, lambda = %.2f" lambda)
        (money (Notional.of_float (ewma *. gross)))
        (ewma *. 100.0);
      printf "  %s\n" (rule 56);
      let ratio =
        if Float.equal equal_weighted 0.0 then 1.0 else ewma /. equal_weighted
      in
      printf "  ratio %.2fx -- %s\n" ratio
        (if Float.( > ) ratio 1.05 then
           "recent moves are larger than the flat window has absorbed.\n\
           \  The equal-weighted number is still averaging in a calmer past."
         else if Float.( < ) ratio 0.95 then
           "a shock is ageing out of the flat window that the market\n\
           \  has already stopped pricing. The equal-weighted number is the stale one."
         else
           "the two estimators agree, so the window is not currently\n\
           \  hiding a change in regime. This is the uninformative case, and it is\n\
           \  worth being able to see rather than infer.")
  | _ -> ());
  printf "\n%s\n  LIMITS  (sorted by utilisation)\n%s\n\n" (rule 106) (rule 106);
  Graph.Snapshot.breaches snapshot
  |> List.sort ~compare:(fun a b ->
      Float.descending (Limits.utilisation a) (Limits.utilisation b))
  |> List.iter ~f:(fun breach ->
      printf "  %-6s %6.1f%%  %s\n"
        (if Breach.breached breach then "BREACH" else "ok")
        (Limits.utilisation breach *. 100.0)
        (Limits.to_string breach));
  List.iter (Graph.Snapshot.unevaluated_limits snapshot) ~f:(fun name ->
      printf "  %-6s %6s   %s: input unavailable (which is not the same as passing)\n"
        "??" "--" name);
  (* The comparison the project exists to make. "Recompute everything" is the
     honest counterfactual for a poll-based engine: every node it knows about,
     once per event. *)
  let distinct = Counter.distinct_nodes counter in
  let actual = Counter.grand_total counter in
  let polled = distinct * steps in
  printf "\n%s\n  RECOMPUTATION\n%s\n\n" (rule 106) (rule 106);
  printf "  distinct nodes in the graph        %d\n" distinct;
  printf "  events processed                   %d\n" steps;
  printf "  node recomputations, incremental   %d\n" actual;
  printf "  node recomputations, if polled     %d  (every node, every event)\n" polled;
  printf "  work avoided                       %.1f%%\n"
    (100.0 *. (1.0 -. (float_of_int actual /. float_of_int polled)));
  printf "\n  hottest nodes:\n";
  List.iter (Counter.hottest counter ~n:6) ~f:(fun (name, n) ->
      printf "    %-24s %5d\n" name n);
  printf
    "\n\
    \  Incremental's own counters: %d stabilizes, %d node recomputations.\n\
    \  Its total is higher than the tally above because it also counts the input\n\
    \  cells and the plumbing nodes that graph.ml never gave a name to.\n\n"
    (Graph.total_stabilizes ())
    (Graph.total_nodes_recomputed ())

(* ------------------------------------------------------------------------ *)
(* How the saving scales                                                     *)
(* ------------------------------------------------------------------------ *)

(* The percentage above is a floor, and a small book is the worst case for it:
   with six names the portfolio-level nodes -- gross, net, weights, the three
   risk numbers -- genuinely depend on everything, so they dominate the tally
   and there is not much left to skip.

   The interesting quantity is how the cost of ONE tick moves as the book grows.
   In a poll-and-recompute engine it grows with the book, because everything is
   redone. Here it does not move at all: a tick still touches one instrument,
   one sector, the aggregates, and the limits that read them. This probe
   measures exactly that, at three book sizes. *)

let probe ~(instrument_count : int) ~(ticks : int) =
  let counter = Counter.create () in
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
        limit
          ("cap-" ^ Symbol.to_string symbol)
          (Limit.Instrument symbol)
          (Limit.Gross_notional (dollars 100_000.0)))
    @ [
        limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 1e9));
        limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 1e9));
        limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.5);
      ]
  in
  let graph =
    Graph.create ~on_compute:(Counter.on_compute counter) ~starting_cash ~instruments
      ~limits ~confidence ~return_window ()
  in
  let prices = Symbol.Table.create () in
  List.iter symbols ~f:(fun symbol ->
      Hashtbl.set prices ~key:symbol ~data:100.0;
      Graph.set_price graph symbol (Price.of_float 100.0);
      Graph.set_qty graph symbol (Qty.of_float 100.0);
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun _ -> daily_return ())));
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  (* Every node has now run at least once, so this is the size of the graph --
     and therefore what a polling engine would redo per event. *)
  let graph_size = Counter.distinct_nodes counter in
  ignore (Counter.take_step counter : int);
  let total = ref 0 in
  let ring = Array.of_list symbols in
  for i = 1 to ticks do
    let symbol = ring.(i % Array.length ring) in
    let price = Hashtbl.find_exn prices symbol *. (1.0 +. gaussian ~sigma:0.004) in
    Hashtbl.set prices ~key:symbol ~data:price;
    Graph.set_price graph symbol (Price.of_float price);
    Graph.stabilize graph;
    total := !total + Counter.take_step counter
  done;
  Graph.destroy graph;
  (graph_size, float_of_int !total /. float_of_int ticks)

let scaling_report () =
  printf "%s\n  HOW THAT SCALES\n%s\n\n" (rule 106) (rule 106);
  printf "  %12s %16s %18s %16s\n" "instruments" "nodes in graph" "nodes per tick"
    "if polled";
  printf "  %s\n" (rule 66);
  List.iter [ 10; 100; 400 ] ~f:(fun instrument_count ->
      let graph_size, per_tick = probe ~instrument_count ~ticks:50 in
      printf "  %12d %16d %18.1f %16d\n" instrument_count graph_size per_tick graph_size);
  printf
    "\n\
    \  The middle column is flat and the right one is not. That is the whole\n\
    \  argument: the cost of an event is set by what the event touches, not by\n\
    \  how large the book is.\n\n"

(* ------------------------------------------------------------------------ *)
(* Synthetic mode                                                            *)
(* ------------------------------------------------------------------------ *)

let run_synthetic () =
  header ();
  let counter = Counter.create () in
  let graph =
    Graph.create ~on_compute:(Counter.on_compute counter) ~starting_cash ~instruments
      ~limits ~confidence ~return_window ()
  in
  (* Mark the book and backfill a full return window, so the risk numbers are
     live from the first event rather than reading "--" for the first sixty.
     The driver keeps its own copy of the last price because it needs to
     generate the next one; the graph is told, not asked. *)
  let last_price = Symbol.Table.create () in
  List.iter book ~f:(fun (symbol, _, price, qty) ->
      Hashtbl.set last_price ~key:symbol ~data:price;
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty);
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun _ -> daily_return ())));
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  (* Construction and seeding are not events; do not charge them to the tally. *)
  ignore (Counter.take_step counter : int);
  table_header ();
  let previous = ref String.Set.empty in
  for event = 1 to steps do
    let symbol, _, _, _ = List.nth_exn book (event % List.length book) in
    let what =
      if event % bar_every = 0 then (
        (* Bar close: every instrument gets a return and the equity mark is
           closed, so the covariance matrix and the drawdown peak both move.
           This is the expensive event, and it is one in ten. *)
        List.iter book ~f:(fun (symbol, _, _, _) ->
            let r = daily_return () in
            let price = Hashtbl.find_exn last_price symbol *. (1.0 +. r) in
            Hashtbl.set last_price ~key:symbol ~data:price;
            Graph.set_price graph symbol (Price.of_float price);
            Graph.push_return graph symbol r);
        Graph.mark_equity graph;
        "BAR close, all names")
      else if event % 17 = 0 then (
        (* A fill. Position and cash move together, so equity does not jump. *)
        let qty = if event % 2 = 0 then 100.0 else -100.0 in
        Graph.apply_fill graph
          {
            Fill.symbol;
            qty = Qty.of_float qty;
            price = Price.of_float (Hashtbl.find_exn last_price symbol);
            time = Time.now ();
          };
        Printf.sprintf "FILL %+.0f %s" qty (Symbol.to_string symbol))
      else
        (* An ordinary tick: one name reprices and nothing else is told
           anything. This is the common case, and its cost is the whole point. *)
        let price =
          Hashtbl.find_exn last_price symbol *. (1.0 +. gaussian ~sigma:0.004)
        in
        Hashtbl.set last_price ~key:symbol ~data:price;
        Graph.apply_tick graph
          { Tick.symbol; price = Price.of_float price; time = Time.now () };
        Printf.sprintf "tick %s %.2f" (Symbol.to_string symbol) price
    in
    let snapshot = Graph.snapshot graph in
    row ~event ~what ~snapshot ~nodes:(Counter.take_step counter);
    previous := report_transitions ~previous:!previous snapshot
  done;
  final_report graph counter;
  (* Released before the scaling probes, so their stabilizes are not also
     recomputing this graph. The Incremental state is shared across every
     Graph.t in the process. *)
  Graph.destroy graph;
  scaling_report ()

(* ------------------------------------------------------------------------ *)
(* Stress mode                                                               *)
(* ------------------------------------------------------------------------ *)

(* The scenario suite against the same six-name book, seeded exactly as
   synthetic mode seeds it.

   No credentials, no network, and no writes to any live state: every scenario
   runs on a fork (see stress.ml) and the graph this function builds is
   destroyed on the way out. *)

(* Rate sensitivity per sector, as a return per percentage point of yield
   change: a 100bp rise costs a technology name 3% and pays an energy name 1%.

   These are assumptions, not measurements, and they are written down here
   rather than buried in a generator because the rate-shock scenario is only as
   meaningful as they are. The signs are the conventional ones -- long-duration
   growth equity discounts badly when rates rise, financials earn a wider spread
   -- and the magnitudes are the order of a real regression rather than the
   result of one.

   In live mode nothing like this is assumed. The betas come out of
   Risk_metrics.beta against the actual FRED series and the actual price
   history, which is the whole point of expressing a macro move as a factor
   shock. This exists so the synthetic book has a factor structure to shock at
   all; a factor uncorrelated with everything would make every beta zero and
   the scenario would truthfully report that nothing moved, which is a real
   state and a poor demonstration. *)
let rate_beta sector =
  match Sector.to_string sector with
  | "TECH" -> -0.030
  | "FINANCIALS" -> 0.009
  | "ENERGY" -> 0.010
  | _ -> 0.0

let seeded_demo_graph ?on_compute () =
  let graph =
    Graph.create ?on_compute ~starting_cash ~instruments ~limits ~confidence
      ~return_window ()
  in
  (* Daily changes in a ten-year yield, in percentage points -- the units
     fred_client.ml delivers. A standard deviation of 5bp a day is about right
     for DGS10. *)
  let factor = Array.init return_window ~f:(fun _ -> gaussian ~sigma:0.05) in
  Graph.set_factor_returns graph factor;
  List.iter book ~f:(fun (symbol, sector, price, qty) ->
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty);
      (* A common factor component plus idiosyncratic noise, so the betas the
         scenario recovers are the ones written above rather than zero. *)
      let beta = rate_beta sector in
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun i ->
             (beta *. factor.(i)) +. gaussian ~sigma:0.009)));
  Graph.stabilize graph;
  Graph.mark_equity graph;
  Graph.stabilize graph;
  graph

let stress_row (o : Stress.Outcome.t) =
  let scenario = Stress.Outcome.scenario o in
  let after = Stress.Outcome.after o in
  let names bs =
    if List.is_empty bs then "--"
    else String.concat ~sep:" " (List.map bs ~f:(fun b -> Limit.name (Breach.limit b)))
  in
  printf "  %-18s %14s %9.2f%% %14s %14s  %s\n"
    (Stress.Scenario.name scenario)
    (money (Stress.Outcome.pnl o))
    (Stress.Outcome.pnl_fraction o *. 100.0)
    (money (Graph.Snapshot.gross_exposure after))
    (money_opt (Graph.Snapshot.value_at_risk_notional after))
    (names (Stress.Outcome.new_breaches o))

let run_stress () =
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  STRESS (synthetic book, no credentials, no network)\n\n";
  let graph = seeded_demo_graph () in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      let before = Graph.snapshot graph in
      printf "  starting book   %s gross, %s equity, VaR %s\n"
        (money (Graph.Snapshot.gross_exposure before))
        (money (Graph.Snapshot.equity before))
        (money_opt (Graph.Snapshot.value_at_risk_notional before));
      printf "  starting state  %d limit%s breached\n\n"
        (List.length (Graph.Snapshot.breached before))
        (if List.length (Graph.Snapshot.breached before) = 1 then "" else "s");
      let scenarios = Stress.suite_for ~graph in
      let outcomes = Stress.run_all ~graph ~scenarios in
      printf "%s\n  SCENARIOS\n%s\n\n" (rule 106) (rule 106);
      printf "  %-18s %14s %10s %14s %14s  %s\n" "scenario" "P&L" "of equity" "gross"
        "VaR" "newly breached";
      printf "  %s\n" (rule 104);
      List.iter outcomes ~f:stress_row;
      printf "  %s\n" (rule 104);
      (* The worst case gets its own block, because a row in a table is not
         where anyone should first read that a scenario breaks the book. *)
      (match Stress.worst outcomes with
      | None -> ()
      | Some w -> (
          let scenario = Stress.Outcome.scenario w in
          printf "\n%s\n  WORST CASE: %s\n%s\n\n" (rule 106)
            (Stress.Scenario.name scenario)
            (rule 106);
          printf "  %s\n\n" (Stress.Scenario.description scenario);
          List.iter (Stress.Scenario.shocks scenario) ~f:(fun shock ->
              printf "  shock           %s\n" (Stress.Shock.to_string shock));
          printf "  P&L             %s (%.2f%% of equity)\n"
            (money (Stress.Outcome.pnl w))
            (Stress.Outcome.pnl_fraction w *. 100.0);
          printf "  equity          %s -> %s\n"
            (money (Graph.Snapshot.equity (Stress.Outcome.before w)))
            (money (Graph.Snapshot.equity (Stress.Outcome.after w)));
          printf "  drawdown        %s -> %s\n"
            (pct (Graph.Snapshot.current_drawdown (Stress.Outcome.before w)))
            (pct (Graph.Snapshot.current_drawdown (Stress.Outcome.after w)));
          (match Stress.Outcome.new_breaches w with
          | [] -> printf "  limits          nothing new breaks\n"
          | bs ->
              List.iter bs ~f:(fun b ->
                  printf "  BREACH          %s\n" (Limits.to_string b)));
          match Stress.Outcome.unestimated_betas w with
          | [] -> ()
          | names ->
              printf "  no beta         %s (did not move -- could not be moved)\n"
                (String.concat ~sep:" " (List.map names ~f:Symbol.to_string))));
      printf
        "\n\
        \  Every row above was computed by forking the engine and writing shocked\n\
        \  prices into the fork's input cells. There is no separate scenario\n\
        \  arithmetic anywhere in this repository -- the numbers come out of the\n\
        \  same nodes that produce the live ones, so they cannot drift from them.\n\n")

(* ------------------------------------------------------------------------ *)
(* Backtest mode                                                             *)
(* ------------------------------------------------------------------------ *)

(* Is the VaR this engine reports actually a 95% quantile?

   The three series below are chosen so the battery both passes and fails in
   front of you. A validation suite that has never rejected anything is not
   evidence of anything, and the one that only ever rejects is not either.

     IID NORMAL     what the parametric estimator assumes. Both estimators
                    should pass, and if they do not, something is wrong with
                    the ENGINE rather than with the data.

     VOL REGIME     calm, then sustained violence. An equal-weighted window
                    takes as long as the window to absorb the change, and every
                    day of that lag is a forecast built from the wrong world.

     JUMPS          calm days interrupted by an identical large loss at a fixed
                    interval. Exactly 5% of days are the tail, so a 60-day
                    historical window always holds three of them and its 95%
                    quantile locks onto the jump size -- after which the
                    realised loss never strictly beats the forecast, and the
                    model reports zero breaches forever.

   All three are generated from an explicit seed, so two runs print the same
   numbers.

   Two things in the output are worth reading rather than skimming past.

   The JUMPS/historical row shows zero exceptions in 940 days, a Kupiec p-value
   that rounds to zero, and a BASEL ZONE OF GREEN. That is not an inconsistency
   in this code. Basel's traffic light is one-sided by design -- it asks whether
   a bank is UNDERSTATING risk, because that is the direction that threatens
   solvency -- while a coverage test is two-sided, because a quantile that is
   never reached is not the quantile it claims to be. A model can be
   comprehensively wrong and still be green. The zone is a supervisor's
   tolerance, not a verdict.

   And the IID-NORMAL/parametric row usually shows an independence p-value near
   or below 5%, on data that is independent by construction. That is a 5% test
   doing what a 5% test does one time in twenty. It is the argument for gating
   on the joint statistic rather than on whichever component happens to look
   worst -- picking the smaller of two p-values and calling it the answer is
   multiple testing, and it is the exact mistake this module is supposed to be
   above. *)

let backtest_rng = Random.State.make [| 2026_08_24 |]

let backtest_gaussian ~sigma =
  let u1 = Float.max 1e-12 (Random.State.float backtest_rng 1.0) in
  let u2 = Random.State.float backtest_rng 1.0 in
  sigma *. Float.sqrt (-2.0 *. Float.log u1) *. Float.cos (2.0 *. Float.pi *. u2)

let backtest_length = 1000
let backtest_window = return_window

let backtest_series =
  [
    ( "iid-normal",
      "Independent normal returns -- exactly what the parametric estimator assumes.",
      Array.init backtest_length ~f:(fun _ -> backtest_gaussian ~sigma:0.011) );
    ( "vol-regime",
      "Calm for 600 days, then four times as volatile. The window takes 60 days to \
       notice.",
      Array.init backtest_length ~f:(fun i ->
          backtest_gaussian ~sigma:(if i < 600 then 0.006 else 0.024)) );
    ( "jumps",
      "Quiet days with an identical -8% loss every twentieth. Exactly 5% of days are the \
       tail.",
      Array.init backtest_length ~f:(fun i ->
          if i % 20 = 19 then -0.08 else backtest_gaussian ~sigma:0.004) );
  ]

let backtest_row ~name ~(estimator : Var_backtest.Estimator.t) (r : Var_backtest.report) =
  printf "  %-12s %-12s %6d %8d %9.1f %10.4f %10.4f %10.4f  %-7s %s\n" name
    (Var_backtest.Estimator.to_string estimator)
    (Var_backtest.observations r) (Var_backtest.exceptions r)
    (Var_backtest.expected_exceptions r)
    (Var_backtest.kupiec_p r)
    (Var_backtest.independence_p r)
    (Var_backtest.conditional_coverage_p r)
    (Var_backtest.Zone.to_string (Var_backtest.zone r))
    (if Var_backtest.rejected r then "REJECTED" else "ok")

let run_backtest () =
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  BACKTEST (VaR model validation, no credentials, no network)\n\n";
  printf "  confidence      %.0f%%\n" (confidence *. 100.0);
  printf "  window          %d observations, rolling\n" backtest_window;
  printf "  series length   %d, so %d forecasts each\n" backtest_length
    (backtest_length - backtest_window);
  printf
    "  estimators      historical, parametric (equal-weighted), parametric (EWMA at\n\
    \                  lambda = %.2f). The last two differ ONLY in how the window is\n\
    \                  weighted, so a difference in verdict is a statement about\n\
    \                  weighting and about nothing else.\n"
    Vol_estimators.Ewma.default_lambda;
  printf
    "  discipline      each forecast is built from the %d days BEFORE the day it is\n\
    \                  scored against, and cannot see that day.\n\n"
    backtest_window;
  printf "%s\n  COVERAGE AND INDEPENDENCE\n%s\n\n" (rule 106) (rule 106);
  printf "  %-12s %-12s %6s %8s %9s %10s %10s %10s  %-7s %s\n" "series" "estimator" "n"
    "excepts" "expected" "Kupiec p" "indep p" "joint p" "Basel" "verdict";
  printf "  %s\n" (rule 104);
  let reports =
    List.concat_map backtest_series ~f:(fun (name, _, returns) ->
        List.map
          [
            Var_backtest.Estimator.Historical;
            Var_backtest.Estimator.Parametric;
            (* The third row is the argument of Phase A, and it is put in front
               of the same battery as the other two rather than described. If
               the equal-weighted parametric estimator is rejected on the
               vol-regime series and the EWMA one is not, that is the claim
               demonstrated. If EWMA is rejected too, the table says so, which
               is what a validation suite is for. *)
            Var_backtest.Estimator.Parametric_ewma Vol_estimators.Ewma.default_lambda;
          ] ~f:(fun estimator ->
            let r =
              Var_backtest.of_returns ~returns ~window:backtest_window ~confidence
                ~estimator
            in
            backtest_row ~name ~estimator r;
            (name, estimator, r)))
  in
  printf "  %s\n\n" (rule 104);
  List.iter backtest_series ~f:(fun (name, description, _) ->
      printf "  %-12s %s\n" name description);
  (* One report in full, and it is the WORST failure rather than the first. A
     table of p-values is a summary; the failure is the finding, and the most
     severe one is the finding worth printing. *)
  (match
     List.filter reports ~f:(fun (_, _, r) -> Var_backtest.rejected r)
     |> List.min_elt ~compare:(fun (_, _, a) (_, _, b) ->
         Float.compare
           (Var_backtest.conditional_coverage_p a)
           (Var_backtest.conditional_coverage_p b))
   with
  | None ->
      printf
        "\n\
        \  Nothing was rejected, which on this set of series would itself be a\n\
        \  finding: two of the three are built to break an equal-weighted window.\n\n"
  | Some (name, _, r) ->
      printf "\n%s\n  IN FULL: the most severe rejection (%s)\n%s\n\n" (rule 106) name
        (rule 106);
      printf "%s\n" (Var_backtest.to_string r));
  printf
    "\n\
    \  A rejection here is the suite working. The point of a coverage test is\n\
    \  not that the model passes it -- it is that a model which does not can be\n\
    \  told apart from one which does, before the difference is discovered by\n\
    \  losing money.\n\n"

(* ------------------------------------------------------------------------ *)
(* Crisis backtest: the same battery, real data                              *)
(* ------------------------------------------------------------------------ *)

(* Everything above this line is validated against series whose regime the
   author chose. That is the right way to BUILD a coverage battery -- it is the
   only setting where you know in advance which tests ought to reject -- and it
   is not evidence that the model survives a real tail.

   This mode changes exactly one thing: the data. Same window, same confidence,
   same three estimators, same Var_backtest.rolling. The method has to be
   visibly identical or the comparison says nothing. *)

let crisis_estimators =
  [
    Var_backtest.Estimator.Historical;
    Var_backtest.Estimator.Parametric;
    Var_backtest.Estimator.Parametric_ewma Vol_estimators.Ewma.default_lambda;
  ]

(* The most exceptions any [span] consecutive sessions contained.

   This is a descriptive statistic over Var_backtest's own exported exceedance
   array, not a new test and not a second implementation of anything -- but it
   is here because the real data exposed a gap the synthetic series never did.

   Christoffersen's independence statistic is a FIRST-ORDER MARKOV test: it
   compares P(exception | exception yesterday) against P(exception | none
   yesterday). That catches exceptions arriving back to back and is blind to
   exceptions arriving in a burst that is not literally consecutive. On the GFC
   window this book takes five exceptions between 15 September and 7 October
   2008 -- seventeen sessions, against 0.85 expected at 95% -- and because only
   one pair anywhere in that series falls on adjacent days, the independence
   test returns p = 0.92. It is not wrong. It is answering a narrower question
   than the one a reader assumes it answered.

   So the burst count sits in the table next to the p-value it qualifies. A
   proper fix is a duration-based test (Christoffersen-Pelletier), which models
   the time BETWEEN exceptions rather than the day after each one; that is not
   implemented here and the README says so rather than leaving the reader to
   assume the column is a hypothesis test. *)
let worst_burst ~(span : int) (hits : bool array) : int =
  let n = Array.length hits in
  if n = 0 then 0
  else begin
    let worst = ref 0 in
    let running = ref 0 in
    Array.iteri hits ~f:(fun i hit ->
        if hit then incr running;
        if i >= span && hits.(i - span) then decr running;
        worst := Int.max !worst !running);
    !worst
  end

(* One month of sessions. Short enough that a burst inside it is a burst rather
   than a season, long enough that a single bad week does not fill it. *)
let burst_span = 21

let crisis_row ~window_name ~(estimator : Var_backtest.Estimator.t) ~(burst : int)
    (r : Var_backtest.report) =
  printf "  %-12s %-12s %6d %8d %9.1f %10.4f %10.4f %10.4f  %5d  %-7s %s\n" window_name
    (Var_backtest.Estimator.to_string estimator)
    (Var_backtest.observations r) (Var_backtest.exceptions r)
    (Var_backtest.expected_exceptions r)
    (Var_backtest.kupiec_p r)
    (Var_backtest.independence_p r)
    (Var_backtest.conditional_coverage_p r)
    burst
    (Var_backtest.Zone.to_string (Var_backtest.zone r))
    (if Var_backtest.rejected r then "REJECTED" else "ok")

let run_backtest_crisis () =
  printf "\n  OhCamel -- reactive risk and limits engine\n";
  printf "  CRISIS BACKTEST (real market data, cached, no credentials, no network)\n\n";
  match Crisis_data.load_all () with
  | Error e ->
      (* Loud, named, and NOT a fallback to the synthetic series. A crisis
         backtest quietly scoring generated data would print a table
         indistinguishable from the real one under a heading claiming
         otherwise. *)
      printf "%s\n\n" (Error.to_string_hum e);
      exit 1
  | Ok windows ->
      printf "  book            the same six names as `make run`: long TECH and\n";
      printf "                  FINANCIALS, short ENERGY, %s gross.\n"
        (money (Notional.of_float 316_000.0));
      printf
        "  weights         held constant across each window, which is graph.ml's own\n\
        \                  approximation. The question is what TODAY's book would have\n\
        \                  done through that history, not what the book of the day did.\n";
      printf "  confidence      %.0f%%\n" (confidence *. 100.0);
      printf "  window          %d observations, rolling\n" backtest_window;
      printf
        "  data            docs/crisis/*.csv -- adjusted daily closes, committed, so\n\
        \                  this table reproduces with no API key and no network.\n\n";
      printf "%s\n  THE WINDOWS\n%s\n\n" (rule 106) (rule 106);
      let series =
        List.map windows ~f:(fun window ->
            let returns =
              Crisis_data.portfolio_returns_of_book ~instruments
                ~positions:(List.map book ~f:(fun (s, _, _, q) -> (s, Qty.of_float q)))
                ~marks:(List.map book ~f:(fun (s, _, p, _) -> (s, Price.of_float p)))
                window
            in
            (window, returns))
      in
      List.iter series ~f:(fun (window, returns) ->
          let name = Crisis_data.Window.name window in
          match returns with
          | None -> printf "  %-12s too short to form a return series\n" name
          | Some returns ->
              let worst = Array.fold returns ~init:0.0 ~f:Float.min in
              let best = Array.fold returns ~init:0.0 ~f:Float.max in
              printf "  %-12s %d sessions, %d forecasts. Book's worst day %s, best %s.\n"
                name
                (Crisis_data.Window.sessions window)
                (Array.length returns - backtest_window)
                (pct worst) (pct best);
              printf "  %-12s %s\n\n" "" (Crisis_data.Window.description window));
      printf "%s\n  COVERAGE AND INDEPENDENCE\n%s\n\n" (rule 106) (rule 106);
      printf "  %-12s %-12s %6s %8s %9s %10s %10s %10s  %5s  %-7s %s\n" "window"
        "estimator" "n" "excepts" "expected" "Kupiec p" "indep p" "joint p" "burst"
        "Basel" "verdict";
      printf "  %s\n" (rule 112);
      let reports =
        List.concat_map series ~f:(fun (window, returns) ->
            match returns with
            | None -> []
            | Some returns ->
                let window_name = Crisis_data.Window.name window in
                List.map crisis_estimators ~f:(fun estimator ->
                    (* [rolling] then [run], rather than [of_returns], only so
                       the exceedance array is in hand for the burst column.
                       var_backtest.ml exports both steps and composes them the
                       same way, so this is the same path and not a second
                       one. *)
                    let observations =
                      Var_backtest.rolling ~returns ~window:backtest_window ~confidence
                        ~estimator
                    in
                    let r = Var_backtest.run ~observations ~estimator ~confidence in
                    let burst =
                      worst_burst ~span:burst_span (Var_backtest.exceedances observations)
                    in
                    crisis_row ~window_name ~estimator ~burst r;
                    (window_name, estimator, r)))
      in
      printf "  %s\n" (rule 112);
      printf
        "\n\
        \  burst  the most exceptions any %d consecutive sessions held. Expected\n\
        \         under independence: about %.1f. It is in this table because\n\
        \         Christoffersen's statistic is a first-order Markov test and\n\
        \         cannot see a cluster whose members are not on adjacent days.\n"
        burst_span
        (float_of_int burst_span *. (1.0 -. confidence));
      let rejected = List.count reports ~f:(fun (_, _, r) -> Var_backtest.rejected r) in
      printf "\n  %d of %d configurations rejected at 5%%.\n" rejected
        (List.length reports);
      (match
         List.filter reports ~f:(fun (_, _, r) -> Var_backtest.rejected r)
         |> List.min_elt ~compare:(fun (_, _, a) (_, _, b) ->
             Float.compare
               (Var_backtest.conditional_coverage_p a)
               (Var_backtest.conditional_coverage_p b))
       with
      | None -> ()
      | Some (name, _, r) ->
          printf "\n%s\n  IN FULL: the most severe rejection (%s)\n%s\n\n" (rule 106) name
            (rule 106);
          printf "%s\n" (Var_backtest.to_string r));
      printf
        "\n\
        \  Read the two sharp windows against the slow one. An equal-weighted\n\
        \  volatility window fails in a specific way when volatility JUMPS -- it\n\
        \  under-forecasts for as long as the window takes to absorb the change --\n\
        \  and 2022 is the control: a large drawdown with no single day over 10%%.\n\
        \  A model that fails there is failing for a different reason than one that\n\
        \  fails in 2008, and the point of running all three is to be able to tell.\n\n"

(* ------------------------------------------------------------------------ *)
(* Live mode                                                                 *)
(* ------------------------------------------------------------------------ *)

(* Real prices from Alpaca, a real macro series from FRED, and the book from a
   local file.

   Three concurrent activities, none of which drives the others:

     the feed        writes price and last-tick cells as frames arrive
     the FRED poller writes the factor cell every few hours
     the clock       advances [now] every few seconds

   Nothing here polls the graph to make it compute. Each of the three writes the
   cells it owns and stabilizes; the graph decides what that implies. The
   snapshot printer below is the only thing on a timer, and it is a DISPLAY
   refresh -- it reads a fixed point that already exists rather than causing one.
   That distinction is the whole project: if this loop were computing the risk
   numbers rather than reading them, everything else would be decoration. *)

let live_line what = printf "  %-30s %s\n%!" (Time_ns.to_string_utc (Time.now ())) what

let print_live_snapshot ~(graph : Graph.t) ~(counter : Counter.t)
    ~(alpaca : Alpaca_ws.Stats.t) ~(fred : Fred_client.Stats.t) =
  let s = Graph.snapshot graph in
  let health = Graph.Snapshot.feed_health s in
  printf "\n  %s\n" (rule 96);
  printf "  gross %s   net %s   equity %s   drawdown %s\n"
    (money (Graph.Snapshot.gross_exposure s))
    (money (Graph.Snapshot.net_exposure s))
    (money (Graph.Snapshot.equity s))
    (pct (Graph.Snapshot.current_drawdown s));
  printf "  VaR95 %s   ES95 %s   beta %s%s\n"
    (money_opt (Graph.Snapshot.value_at_risk_notional s))
    (money_opt (Graph.Snapshot.expected_shortfall_notional s))
    (match Graph.Snapshot.portfolio_beta s with
    | None -> "--"
    | Some b -> Printf.sprintf "%.3f" b)
    (if Graph.Snapshot.warming_up s then "   [WARMING UP -- risk numbers unavailable]"
     else "");
  (* Feed health is printed before the breaches, not after, because it governs
     whether the breaches mean anything. A limit that is not breached according
     to a price from twenty minutes ago is not a limit that is not breached. *)
  if Graph.Feed_health.all_healthy health then printf "  feed: all symbols live\n"
  else (
    (match Graph.Feed_health.never_seen health with
    | [] -> ()
    | symbols ->
        printf "  feed: NEVER SEEN %s (subscription may not have taken)\n"
          (String.concat ~sep:"," (List.map symbols ~f:Symbol.to_string)));
    match Graph.Feed_health.stale health with
    | [] -> ()
    | symbols ->
        printf "  feed: STALE %s -- the numbers above are computed from old prices\n"
          (String.concat ~sep:"," (List.map symbols ~f:Symbol.to_string)));
  List.iter (Graph.Snapshot.breached s) ~f:(fun b ->
      printf "  !! %s\n" (Limits.to_string b));
  List.iter (Graph.Snapshot.unevaluated_limits s) ~f:(fun name ->
      printf "  ?? %s: input unavailable (which is not the same as passing)\n" name);
  printf "  alpaca[%s]\n  fred[%s]\n"
    (Alpaca_ws.Stats.to_string alpaca)
    (Fred_client.Stats.to_string fred);
  (* Nodes recomputed since the last snapshot, over trades applied in the same
     window. This is the synthetic driver's headline claim, measured on live
     data: the ratio should sit near the per-tick cost the scaling probe reports
     and should NOT grow with the size of the book. If it climbs, something has
     acquired a dependency it should not have. *)
  let nodes = Counter.take_step counter in
  let trades = alpaca.Alpaca_ws.Stats.trades - counter.Counter.trades_at_last_snapshot in
  counter.Counter.trades_at_last_snapshot <- alpaca.Alpaca_ws.Stats.trades;
  printf "  work[nodes=%d over %d trades%s]\n%!" nodes trades
    (if trades > 0 then
       Printf.sprintf ", %.1f per trade" (float_of_int nodes /. float_of_int trades)
     else "")

(* websocket-async reports handshake failures through the [logs] library, and a
   [logs] with no reporter installed throws them away. That is how a rejected
   HTTP upgrade surfaces as nothing but a closed pipe further downstream, which
   is unreadable.

   Off by default, because the debug level prints every frame. Set
   OHCAMEL_LOG_LEVEL to error, warning, info or debug. *)
let install_log_reporter () =
  match Sys.getenv "OHCAMEL_LOG_LEVEL" with
  | None -> ()
  | Some level ->
      Logs.set_reporter (Logs.format_reporter ());
      Logs.set_level
        (match String.lowercase level with
        | "debug" -> Some Logs.Debug
        | "info" -> Some Logs.Info
        | "warning" | "warn" -> Some Logs.Warning
        | "error" -> Some Logs.Error
        | "none" | "off" -> None
        | other ->
            eprintf "ohcamel: unknown OHCAMEL_LOG_LEVEL %S, using error\n" other;
            Some Logs.Error)

let run_live ~book_path ~(serve_port : int option) =
  let open Async in
  install_log_reporter ();
  printf "\n%s\n" (rule 96);
  printf "  OhCamel -- reactive risk and limits engine\n";
  printf "  Phase 2: LIVE (Alpaca market data + FRED macro)\n";
  printf "%s\n\n" (rule 96);
  match Config.load ~book_path () with
  | Error error ->
      (* Refuse to start rather than degrade to something that looks live.
         Printed to stderr and exited non-zero so a supervisor notices. *)
      prerr_endline (Error.to_string_hum error);
      exit 1
  | Ok config ->
      let book = config.Config.book in
      let runtime = config.Config.runtime in
      let instruments = Config.Book.instruments book in
      let limits = Config.Book.limits book in
      let counter = Counter.create () in
      let graph =
        Graph.create ~on_compute:(Counter.on_compute counter)
          ~starting_cash:(Notional.of_float book.Config.Book.cash)
          ~instruments ~limits ~confidence:runtime.Config.Runtime.confidence
          ~return_window:runtime.Config.Runtime.return_window
          ~staleness_threshold:runtime.Config.Runtime.staleness_threshold ()
      in
      List.iter book.Config.Book.positions ~f:(fun p ->
          Graph.set_qty graph
            (Symbol.of_string p.Config.Book.Position_spec.symbol)
            (Qty.of_float p.Config.Book.Position_spec.qty));
      Graph.stabilize graph;
      printf "  book        %d instruments, %d limits, cash %s\n"
        (List.length instruments) (List.length limits)
        (money (Notional.of_float book.Config.Book.cash));
      printf "  feed        alpaca %s\n" runtime.Config.Runtime.alpaca_feed;
      printf "  factor      FRED %s\n\n" runtime.Config.Runtime.fred_series_id;
      (* Phase 4. Off unless the book file says otherwise, and a bad alerting
         config is fatal rather than silently ignored -- someone who wrote an
         alerts block meant to be alerted, and starting up with it quietly
         disabled would be the worst of both. *)
      let%bind alerts =
        match Alerts.attach ~graph ~config:book.Config.Book.alerts with
        | Error error ->
            prerr_endline (Error.to_string_hum error);
            exit 1
        | Ok alerts -> return alerts
      in
      (match alerts with
      | None -> live_line "alerts    disabled (default) -- breaches are displayed only"
      | Some a -> live_line (sprintf "alerts    %s" (Alerts.status a)));
      let alpaca_stats = Alpaca_ws.Stats.create () in
      let fred_stats = Fred_client.Stats.create () in
      (* Backfill the return windows before anything else runs.

         Without this the engine has prices but no distribution to take a
         quantile of, so VaR, ES, parametric VaR and beta all stay None and the
         display reads WARMING UP indefinitely -- waiting for sixty daily
         observations to arrive from the live stream would take three months.

         Awaited rather than launched concurrently: the first snapshot the
         operator sees should have real risk numbers in it, not blanks that fill
         in later. *)
      let%bind () =
        match%map
          Alpaca_rest.backfill ~graph ~credentials:config.Config.credentials ~runtime
        with
        | Error error ->
            (* Not fatal. Exposure, sector limits and the drawdown breaker all
               work without history; only the distribution-based numbers do not,
               and the snapshot says so plainly. *)
            live_line
              (sprintf "backfill FAILED -- risk numbers will stay unavailable: %s"
                 (Error.to_string_hum error))
        | Ok counts ->
            let short =
              List.filter counts ~f:(fun (_, n) ->
                  n < runtime.Config.Runtime.return_window)
            in
            live_line
              (sprintf "backfill  %d symbols, %d daily returns each" (List.length counts)
                 (List.fold counts ~init:Int.max_value ~f:(fun acc (_, n) ->
                      Int.min acc n)));
            List.iter short ~f:(fun (symbol, n) ->
                live_line
                  (sprintf
                     "backfill  %s has only %d observations -- the common window is \
                      capped by the shortest series"
                     (Symbol.to_string symbol) n))
      in
      (* The clock. The ONLY writer of the [now] cell, and the reason
         test_graph.ml asserts that no risk node is downstream of it: if one
         were, this timer would be recomputing the book every few seconds and
         the engine would have quietly become a poller. *)
      let clock =
        Clock_ns.every' runtime.Config.Runtime.clock_interval (fun () ->
            Graph.set_now graph (Time.now ());
            Graph.stabilize graph;
            Deferred.unit);
        Deferred.never ()
      in
      let display =
        Clock_ns.every' runtime.Config.Runtime.snapshot_interval (fun () ->
            print_live_snapshot ~graph ~counter ~alpaca:alpaca_stats ~fred:fred_stats;
            Deferred.unit);
        Deferred.never ()
      in
      let fred =
        Fred_client.run
          ~on_event:(fun e -> live_line ("fred    " ^ e))
          ~graph ~credentials:config.Config.credentials ~runtime ~stats:fred_stats ()
      in
      let alpaca =
        match%bind
          Alpaca_ws.run
            ~on_event:(fun e -> live_line ("alpaca  " ^ e))
            ~on_control:(fun message ->
              match message with
              | Alpaca_ws.Message.Success msg -> live_line ("alpaca  " ^ msg)
              | Alpaca_ws.Message.Subscription raw -> live_line ("alpaca  " ^ raw)
              | _ -> ())
            ~graph ~credentials:config.Config.credentials ~runtime ~stats:alpaca_stats ()
        with
        | Ok () -> Deferred.unit
        | Error fatal ->
            prerr_endline "";
            prerr_endline fatal;
            exit 1
      in
      let http =
        match serve_port with
        | None -> Deferred.never ()
        | Some port ->
            let server =
              Server.create ~graph ~factor:runtime.Config.Runtime.fred_series_id ()
            in
            let%bind (_ : (_, _) Cohttp_async.Server.t) = Server.start ~port server in
            live_line (sprintf "dashboard  http://localhost:%d" port);
            Deferred.never ()
      in
      (* The feed is the only one of these that can finish. When it does, it has
         hit something no retry can fix, and it has already said what. *)
      Deferred.all_unit
        [
          alpaca;
          Deferred.ignore_m clock;
          Deferred.ignore_m display;
          fred;
          Deferred.ignore_m http;
        ]

(* ------------------------------------------------------------------------ *)
(* Demo mode: the dashboard, driven synthetically                            *)
(* ------------------------------------------------------------------------ *)

(* The same six-name book as synthetic mode, ticking in real time and served
   over HTTP, with no credentials and no network.

   This exists so the dashboard can be developed and demonstrated without an
   Alpaca account, outside market hours, and on a plane. It is also the only
   configuration in which the stale path can be exercised deliberately: stop
   ticking a symbol and watch the page lose confidence in its own numbers. *)
let run_demo ~port =
  let open Async in
  printf "\n%s\n" (rule 96);
  printf "  OhCamel -- reactive risk and limits engine\n";
  printf "  DEMO (synthetic feed, no credentials, no network)\n";
  printf "%s\n\n" (rule 96);
  (* One limit is set deliberately close to its current exposure.

     Same reasoning as the quiet symbol below: alerting is only demonstrable
     when something actually breaches, and waiting for a random walk to drift
     2% is not a demo. NVDA sits at 60 x 900 = 54,000 against a 54,200 cap, so
     the first meaningful move crosses it -- and then the hysteresis, the
     edge-triggering and the kill switch all become visible rather than
     theoretical. *)
  let demo_limits =
    List.map limits ~f:(fun l ->
        if String.equal (Limit.name l) "nvda-cap" then
          { l with Limit.kind = Limit.Gross_notional (dollars 54_200.0) }
        else l)
  in
  let graph =
    Graph.create ~starting_cash ~instruments ~limits:demo_limits ~confidence
      ~return_window ~staleness_threshold:(Time.Span.of_sec 20.0) ()
  in
  let last_price = Symbol.Table.create () in
  List.iter book ~f:(fun (symbol, _, price, qty) ->
      Hashtbl.set last_price ~key:symbol ~data:price;
      Graph.set_price graph symbol (Price.of_float price);
      Graph.set_qty graph symbol (Qty.of_float qty);
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun _ -> daily_return ())));
  Graph.set_factor_returns graph
    (Array.init return_window ~f:(fun _ -> gaussian ~sigma:0.04));
  Graph.stabilize graph;
  Graph.mark_equity graph;
  (* One print each at startup, so every symbol begins LIVE. The quiet one below
     then transitions live -> stale, which is the path worth watching; without
     this it would sit in never-seen forever and the more interesting half of
     feed health would never be exercised. *)
  List.iter book ~f:(fun (symbol, _, price, _) ->
      Graph.apply_tick graph
        { Tick.symbol; price = Price.of_float price; time = Time.now () });
  Graph.set_now graph (Time.now ());
  Graph.stabilize graph;
  (* Demo mode turns alerting on deliberately, with the sink that cannot
     leave the machine. The point is to exercise the Phase 4 path -- edge
     triggering, hysteresis, the kill switch latching -- where it can be watched
     and where it cannot page anyone. Slack is never a demo sink. *)
  let demo_alerts =
    {
      Config.Alerts.enabled = true;
      sinks = [ Config.Alerts.Sink.Log ];
      clear_below = 0.95;
      kill_switch_enabled = true;
      kill_switch_trips_on = [ "nvda-cap" ];
    }
  in
  let%bind alerts =
    match Alerts.attach ~graph ~config:demo_alerts with
    | Error error ->
        prerr_endline (Error.to_string_hum error);
        exit 1
    | Ok alerts -> return alerts
  in
  let server = Server.create ?alerts ~graph ~factor:"SYNTHETIC" () in
  let%bind (_ : (_, _) Cohttp_async.Server.t) = Server.start ~port server in
  printf "  dashboard   http://localhost:%d\n" port;
  printf
    "  book        %d instruments, %d limits (2 of them on risk SHARE, not notional)\n"
    (List.length book) (List.length demo_limits);
  printf "  ticking     one name every 400ms, a bar every 15s\n";
  (* One symbol is deliberately never ticked.

     Feed health is the part of this engine hardest to demonstrate, because it
     only shows itself when something goes wrong -- and "unplug the network" is
     not a demo step. Leaving one name permanently quiet makes the stale path
     visible on purpose: after the threshold it goes stale, the dashboard says
     so, and the risk numbers below it visibly lose their authority. Which is
     the behaviour worth showing, since it is the one the whole design is
     arranged around. *)
  let quiet, _, _, _ = List.last_exn book in
  let tickable = List.filter book ~f:(fun (s, _, _, _) -> not (Symbol.equal s quiet)) in
  printf "  quiet       %s is never ticked, so the stale path is visible\n"
    (Symbol.to_string quiet);
  printf
    "  alerts      on, logging to this terminal. Kill switch armed on nvda-cap --\n\
    \              it sets a flag and nothing else. Nothing here places orders.\n\n\
     %!";
  (* A tick. One name reprices; the graph decides what that implies. *)
  Clock_ns.every' (Time_ns.Span.of_ms 400.0) (fun () ->
      let symbol, _, _, _ =
        List.nth_exn tickable (Random.State.int rng (List.length tickable))
      in
      let price = Hashtbl.find_exn last_price symbol *. (1.0 +. gaussian ~sigma:0.003) in
      Hashtbl.set last_price ~key:symbol ~data:price;
      Graph.apply_tick graph
        { Tick.symbol; price = Price.of_float price; time = Time.now () };
      Graph.stabilize graph;
      Deferred.unit);
  (* A bar close: new returns for everyone, and an equity mark. *)
  Clock_ns.every' (Time_ns.Span.of_sec 15.0) (fun () ->
      List.iter book ~f:(fun (symbol, _, _, _) ->
          Graph.push_return graph symbol (daily_return ()));
      Graph.mark_equity graph;
      Graph.stabilize graph;
      Deferred.unit);
  (* The staleness clock, exactly as in live mode. *)
  Clock_ns.every' (Time_ns.Span.of_sec 3.0) (fun () ->
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      Deferred.unit);
  Deferred.never ()

(* ------------------------------------------------------------------------ *)
(* Entry point                                                               *)
(* ------------------------------------------------------------------------ *)

(* Argv is parsed by hand rather than with Command: [command_unix] is not in this
   switch, and two subcommands with one optional argument do not justify adding
   a dependency for it. *)

let usage () =
  printf
    "ohcamel -- reactive risk and limits engine\n\n\
    \  ohcamel synthetic          generated ticks and fills, prints, exits\n\
    \  ohcamel stress             scenario suite against the synthetic book\n\
    \  ohcamel backtest           VaR model validation (Kupiec, Christoffersen)\n\
    \  ohcamel backtest-crisis    the same battery against real crisis data\n\
    \  ohcamel demo [port]        synthetic feed + live dashboard, NO credentials\n\
    \  ohcamel live [book.sexp]   live Alpaca market data and FRED macro\n\
    \  ohcamel serve [port]       live feeds + dashboard on http://localhost:PORT\n\n\
     Live and serve need ALPACA_API_KEY, ALPACA_SECRET_KEY and FRED_API_KEY in\n\
     the environment, and a book file (default %s):\n\n\
    \  set -a; source /path/to/.env; set +a\n\
    \  ohcamel serve\n\n\
     Demo needs none of that, and is the way to look at the dashboard when the\n\
     market is closed:\n\n\
    \  ohcamel demo\n\n"
    Config.default_book_path

let () =
  match Array.to_list (Sys.get_argv ()) with
  | _ :: ("synthetic" | "syn") :: _ | [ _ ] -> run_synthetic ()
  | _ :: "stress" :: _ -> run_stress ()
  | _ :: "backtest" :: _ -> run_backtest ()
  | _ :: ("backtest-crisis" | "crisis") :: _ -> run_backtest_crisis ()
  | _ :: "live" :: rest ->
      let book_path =
        match rest with path :: _ -> path | [] -> Config.default_book_path
      in
      (* Async only from here. Synthetic mode never starts the scheduler, which
         keeps it usable as a plain program. *)
      Async.Thread_safe.block_on_async_exn (fun () ->
          run_live ~book_path ~serve_port:None)
  | _ :: "serve" :: rest ->
      let port = match rest with p :: _ -> Int.of_string p | [] -> default_port in
      Async.Thread_safe.block_on_async_exn (fun () ->
          run_live ~book_path:Config.default_book_path ~serve_port:(Some port))
  | _ :: "demo" :: rest ->
      let port = match rest with p :: _ -> Int.of_string p | [] -> default_port in
      Async.Thread_safe.block_on_async_exn (fun () -> run_demo ~port)
  | _ :: ("-h" | "--help" | "help") :: _ -> usage ()
  | _ :: unknown :: _ ->
      printf "ohcamel: unknown mode %S\n\n" unknown;
      usage ();
      exit 2
  | [] -> usage ()
