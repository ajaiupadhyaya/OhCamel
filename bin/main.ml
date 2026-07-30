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
  printf "  Phase 3: DEMO (synthetic feed, no credentials)\n";
  printf "%s\n\n" (rule 96);
  let graph =
    Graph.create ~starting_cash ~instruments ~limits ~confidence ~return_window
      ~staleness_threshold:(Time.Span.of_sec 20.0) ()
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
  let server = Server.create ~graph ~factor:"SYNTHETIC" () in
  let%bind (_ : (_, _) Cohttp_async.Server.t) = Server.start ~port server in
  printf "  dashboard   http://localhost:%d\n" port;
  printf "  book        %d instruments, %d limits\n" (List.length book)
    (List.length limits);
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
  printf "  quiet       %s is never ticked, so the stale path is visible\n\n%!"
    (Symbol.to_string quiet);
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
