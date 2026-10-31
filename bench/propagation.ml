(* What each kind of input really recomputes.

   The public site cannot show the live graph: its node names are the book's
   positions. A drawing of a graph is not evidence, and a simulation in the
   browser would time JavaScript. So this drives the real [Graph], with the same
   hooks the served engine has, on a labelled example book of real closes, and
   records what happened: for every input, which named bodies ran, which values
   changed, and what one stabilize cost. docs/perf/propagation.json is the
   recording, and /engine replays it. Nothing here is modelled; a node is lit on
   that page because it ran here.

   THE BOOK STARTS AT ITS HIGH-WATER MARK. So a rise cannot move drawdown and a
   fall can, which is why both are recorded: one direction alone would present
   what a rise does as what a tick does.

   ONE EVENT, THEN BACK. Every event starts from the same state. It is applied,
   recorded and undone before the next, so "a tick in SPY" means the same thing
   for each name, and the difference between two events is the event.

   HELD IS NOT UNTOUCHED. A body that ran and whose value did not move is a
   cutoff holding, and nothing downstream of it ran because of it. The page draws
   that apart from the nodes that never ran, so [ran] and [changed] are both
   kept, not only a count.

   [scaling] IS THE BENCHMARK'S BOOK. The same book bench_graph measures, against
   the same baseline, reduced to medians for the page. It runs synthetic returns,
   as the benchmark does, because the point of those rows is the cost's shape as
   a book grows, and there are only nine ETFs to grow with. The file says so.

   Timings belong to the machine named on the command line and never to the
   droplet, and the file carries that name beside them. *)

open Core
open Ohcamel
open Ohcamel.Types

let sectors =
  [
    ("SPY", "INDEX");
    ("QQQ", "INDEX");
    ("IWM", "INDEX");
    ("TLT", "TREASURIES");
    ("IEF", "TREASURIES");
    ("GLD", "METALS");
    ("XLE", "ENERGY");
    ("XLF", "FINANCIALS");
    ("XLK", "TECH");
  ]

let position_notional = 100_000.0
let starting_cash = 100_000.0
let repeats = 501
let tick_move = 0.005
let fill_shares = 10.0
let cash_move = 1_000.0

(* The window seeded into the graph: every return up to the second-to-last
   close. The last one is the day the [roll] event adds. *)
type name = {
  symbol : Symbol.t;
  price : float;
  qty : float;
  window : float array;
  next : float;
}

type book = {
  as_of : string;
  names : name list;
  factor : float array;
  next_factor : float;
}

let read_book path =
  let rows =
    In_channel.read_lines path |> List.map ~f:(fun line -> String.split line ~on:',')
  in
  let header, rows =
    match rows with
    | header :: rows -> (Array.of_list header, List.map rows ~f:Array.of_list)
    | [] -> failwithf "propagation: %s is empty" path ()
  in
  let n = List.length rows in
  (* A window of returns needs one close more, and the [roll] event one more again. *)
  let want = Baseline.return_window + 2 in
  if n <> want then failwithf "propagation: %s has %d sessions, want %d" path n want ();
  let rows = Array.of_list rows in
  let column i = Array.map rows ~f:(fun row -> Float.of_string row.(i)) in
  let index_of name =
    match Array.findi header ~f:(fun _ h -> String.equal h name) with
    | Some (i, _) -> i
    | None -> failwithf "propagation: no column %s in %s" name path ()
  in
  let names =
    List.map sectors ~f:(fun (s, _) ->
        let closes = column (index_of s) in
        let returns =
          Array.init (n - 1) ~f:(fun i -> (closes.(i + 1) /. closes.(i)) -. 1.0)
        in
        let price = closes.(n - 2) in
        {
          symbol = Symbol.of_string s;
          price;
          qty = Float.round_nearest (position_notional /. price);
          window = Array.sub returns ~pos:0 ~len:(n - 2);
          next = returns.(n - 2);
        })
  in
  let factor = column (index_of "dgs10_change") in
  {
    as_of = rows.(n - 2).(0);
    names;
    factor = Array.sub factor ~pos:1 ~len:(n - 2);
    next_factor = factor.(n - 1);
  }

let median xs =
  let xs = Array.copy xs in
  Array.sort xs ~compare:Float.compare;
  xs.(Array.length xs / 2)

(* The window after a new day: the oldest return out, the new one in. *)
let rolled window next =
  Array.append (Array.sub window ~pos:1 ~len:(Array.length window - 1)) [| next |]

let elapsed_ns f =
  let t0 = Time_ns.now () in
  f ();
  Time_ns.Span.to_ns (Time_ns.diff (Time_ns.now ()) t0)

type event = {
  id : string;
  kind : string;
  label : string;
  about : string option;
  ran : string list;
  changed : string list;
  stabilize_ns : float;
}

let record graph log ~id ~kind ~label ~symbol ~apply ~undo =
  ignore (Recompute_log.drain log : (string * int) list);
  ignore (Recompute_log.drain_changed log : string list);
  apply ();
  Graph.stabilize graph;
  let ran = List.map (Recompute_log.drain log) ~f:fst in
  let changed = Recompute_log.drain_changed log in
  undo ();
  Graph.stabilize graph;
  (* Timed apart from the recording above: the median of the stabilize alone,
     as /api/ops reports it, undoing between runs off the clock. *)
  let samples =
    Array.init repeats ~f:(fun _ ->
        apply ();
        let ns = elapsed_ns (fun () -> Graph.stabilize graph) in
        undo ();
        Graph.stabilize graph;
        ns)
  in
  ignore (Recompute_log.drain log : (string * int) list);
  ignore (Recompute_log.drain_changed log : string list);
  {
    id;
    kind;
    label;
    about = symbol;
    ran = List.sort ran ~compare:String.compare;
    changed = List.sort changed ~compare:String.compare;
    stabilize_ns = median samples;
  }

let limits (book : book) =
  List.map book.names ~f:(fun n ->
      {
        Limit.name = "cap-" ^ Symbol.to_string n.symbol;
        scope = Limit.Instrument n.symbol;
        kind = Limit.Gross_notional (Notional.of_float (4.0 *. position_notional));
      })
  @ [
      {
        Limit.name = "book-cap";
        scope = Limit.Portfolio;
        kind = Limit.Gross_notional (Notional.of_float (20.0 *. position_notional));
      };
      {
        Limit.name = "var-cap";
        scope = Limit.Portfolio;
        kind = Limit.Value_at_risk (Notional.of_float position_notional);
      };
      { Limit.name = "dd-cap"; scope = Limit.Portfolio; kind = Limit.Max_drawdown 0.25 };
    ]

let recorded (book : book) =
  let log = Recompute_log.create () in
  let instruments =
    List.map2_exn book.names sectors ~f:(fun n (_, sector) ->
        { Instrument.symbol = n.symbol; sector = Sector.of_string sector })
  in
  let graph =
    Graph.create ~on_compute:(Recompute_log.note log)
      ~on_value_change:(Recompute_log.note_change log)
      ~starting_cash:(Notional.of_float starting_cash)
      ~instruments ~limits:(limits book) ~confidence:Baseline.confidence
      ~return_window:Baseline.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      (* A real tick carries a new time even when its price repeats, and that
         time is an input of its own: feed[S] reads it, whatever the price did. *)
      let clock = ref Time.epoch in
      let tick_time () =
        clock := Time.add !clock (Time.Span.of_ms 1.0);
        !clock
      in
      Graph.set_now graph Time.epoch;
      List.iter book.names ~f:(fun n ->
          Graph.apply_tick graph
            {
              Tick.symbol = n.symbol;
              price = Price.of_float n.price;
              time = tick_time ();
            };
          Graph.set_qty graph n.symbol (Qty.of_float n.qty);
          Graph.set_returns graph n.symbol n.window);
      Graph.set_factor_returns graph book.factor;
      Graph.stabilize graph;
      Graph.mark_equity graph;
      Graph.stabilize graph;
      let per_name n =
        let symbol = Symbol.to_string n.symbol in
        let tick_to price =
          Graph.apply_tick graph
            { Tick.symbol = n.symbol; price = Price.of_float price; time = tick_time () }
        in
        let fill qty =
          Graph.apply_fill graph
            {
              Fill.symbol = n.symbol;
              qty = Qty.of_float qty;
              price = Price.of_float n.price;
              time = tick_time ();
            }
        in
        [
          record graph log ~id:("tick:" ^ symbol) ~kind:"tick" ~symbol:(Some symbol)
            ~label:(sprintf "A %.1f%% rise in %s" (100.0 *. tick_move) symbol)
            ~apply:(fun () -> tick_to (n.price *. (1.0 +. tick_move)))
            ~undo:(fun () -> tick_to n.price);
          record graph log ~id:("fall:" ^ symbol) ~kind:"fall" ~symbol:(Some symbol)
            ~label:(sprintf "A %.1f%% fall in %s" (100.0 *. tick_move) symbol)
            ~apply:(fun () -> tick_to (n.price *. (1.0 -. tick_move)))
            ~undo:(fun () -> tick_to n.price);
          record graph log ~id:("repeat:" ^ symbol) ~kind:"repeat" ~symbol:(Some symbol)
            ~label:(sprintf "A repeat of %s's price at a new time" symbol)
            ~apply:(fun () -> tick_to n.price)
            ~undo:(fun () -> ());
          record graph log ~id:("fill:" ^ symbol) ~kind:"fill" ~symbol:(Some symbol)
            ~label:(sprintf "A fill of %.0f %s shares" fill_shares symbol)
            ~apply:(fun () -> fill fill_shares)
            ~undo:(fun () ->
              (* Set back, not traded back: a reverse fill leaves a cash residue. *)
              Graph.set_qty graph n.symbol (Qty.of_float n.qty);
              Graph.set_cash graph (Notional.of_float starting_cash));
          record graph log ~id:("roll:" ^ symbol) ~kind:"roll" ~symbol:(Some symbol)
            ~label:(sprintf "A new day's return for %s" symbol)
            ~apply:(fun () -> Graph.set_returns graph n.symbol (rolled n.window n.next))
            ~undo:(fun () -> Graph.set_returns graph n.symbol n.window);
        ]
      in
      let whole_book =
        [
          record graph log ~id:"factor" ~kind:"factor" ~symbol:None
            ~label:"A new day's factor return"
            ~apply:(fun () ->
              Graph.set_factor_returns graph (rolled book.factor book.next_factor))
            ~undo:(fun () -> Graph.set_factor_returns graph book.factor);
          record graph log ~id:"cash" ~kind:"cash" ~symbol:None
            ~label:
              (sprintf "A $%s change in cash"
                 (Int.to_string_hum ~delimiter:',' (Float.to_int cash_move)))
            ~apply:(fun () ->
              Graph.set_cash graph (Notional.of_float (starting_cash +. cash_move)))
            ~undo:(fun () -> Graph.set_cash graph (Notional.of_float starting_cash));
        ]
      in
      let events = List.concat_map book.names ~f:per_name @ whole_book in
      (events, Server.json_of_graph (Graph.topology graph)))

(* One row of the benchmark's table: the same book, the same two engines, and
   the median tick, since a page wants one number and a median is the honest one
   when a stabilize can land on a minor collection. *)
let scaling_row ~instrument_count =
  let log = Recompute_log.create () in
  let graph, symbols =
    Baseline.seeded_graph ~on_compute:(Recompute_log.note log) ~instrument_count ()
  in
  let named = Recompute_log.distinct log in
  ignore (Recompute_log.drain log : (string * int) list);
  let ticks = 2_000 in
  let ran = ref 0 in
  let incremental =
    Array.init ticks ~f:(fun i ->
        let symbol = symbols.(i % Array.length symbols) in
        Graph.set_price graph symbol
          (Price.of_float (100.0 +. (0.01 *. float_of_int (i % 97))));
        let ns = elapsed_ns (fun () -> Graph.stabilize graph) in
        ran := !ran + List.sum (module Int) (Recompute_log.drain log) ~f:snd;
        ns)
  in
  Graph.destroy graph;
  let polled = Baseline.Polled.create ~instrument_count in
  let full =
    Array.init
      (if instrument_count >= 400 then 41 else 201)
      ~f:(fun i -> elapsed_ns (fun () -> Baseline.Polled.tick polled ~i))
  in
  `Assoc
    [
      ("instruments", `Int instrument_count);
      ("named_nodes", `Int named);
      ("nodes_per_tick", `Float (float_of_int !ran /. float_of_int ticks));
      ("incremental_ns", `Float (median incremental));
      ("full_ns", `Float (median full));
    ]

let json_of_event e =
  let names xs = `List (List.map xs ~f:(fun x -> `String x)) in
  `Assoc
    [
      ("id", `String e.id);
      ("kind", `String e.kind);
      ("label", `String e.label);
      ("symbol", match e.about with Some s -> `String s | None -> `Null);
      ("ran", names e.ran);
      ("changed", names e.changed);
      ("stabilize_ns", `Float e.stabilize_ns);
    ]

let () =
  match Sys.get_argv () with
  | [| _; path; sha; machine |] ->
      let book = read_book path in
      let events, graph = recorded book in
      let scaling =
        List.map [ 10; 100; 400 ] ~f:(fun n -> scaling_row ~instrument_count:n)
      in
      let positions =
        List.map2_exn book.names sectors ~f:(fun n (_, sector) ->
            `Assoc
              [
                ("symbol", `String (Symbol.to_string n.symbol));
                ("sector", `String sector);
                ("price", `Float n.price);
                ("qty", `Float n.qty);
              ])
      in
      print_endline
        (Yojson.Safe.to_string
           (`Assoc
              [
                ("schema", `Int 1);
                ("generated_at", `String (Time_ns.to_string_utc (Time.now ())));
                ("sha", `String sha);
                ("machine", `String machine);
                ("ocaml", `String Sys.ocaml_version);
                ("samples", `Int repeats);
                ( "book",
                  `Assoc
                    [
                      ("as_of", `String book.as_of);
                      ("label", `String "example book: equal notional in nine ETFs");
                      ("source", `String "docs/perf/propagation_book.csv, from fixtures/");
                      ("positions", `List positions);
                    ] );
                ("graph", graph);
                ("events", `List (List.map events ~f:json_of_event));
                ("scaling", `List scaling);
                ( "scaling_book",
                  `String "the benchmark's book: synthetic returns, ten names per sector"
                );
              ]))
  | _ ->
      eprintf "usage: propagation <book.csv> <sha> <machine>\n";
      exit 2
