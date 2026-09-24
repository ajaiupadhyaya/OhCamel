(* The example book, parsed.

   deploy/Dockerfile bakes this file into the image as /app/book.sexp, and
   deploy/deploy.sh copies it to create book.sexp on a host that has none. So a
   typo in it is not a documentation bug: it is a container that refuses to
   start, on the one path nobody exercises locally. Nothing else parsed it.

   The second half of the case is the shipped default. A book copied from this
   file must not trade until someone edits it: the desk block is present so that
   enabling it is one word rather than a guess, and it says disabled.

   Eight names since phase A3: the six the documentation describes, and SPY and
   TLT at zero, because a signal may target only a name the book declares (R7)
   and EXP-A01's two strategies trade those two. The demo's synthetic book keeps
   its six; this is the file a live host copies. *)

open Core
module Book = Ohcamel.Config.Book
module Desk_spec = Ohcamel.Config.Book.Desk_spec

(* dune runs the suite in _build/default/test, and the stanza's dep puts the
   file one level up. *)
let path = "../book.example.sexp"

let load () =
  match Book.load path with
  | Ok book -> book
  | Error e ->
      Alcotest.failf
        "the example book does not parse, so a fresh deploy would refuse to start: %s"
        (Error.to_string_hum e)

let test_the_example_book_parses_and_ships_inert () =
  let book = load () in
  (* The universe and the limits the rest of the documentation describes. *)
  Alcotest.(check int) "eight instruments" 8 (List.length book.Book.positions);
  Alcotest.(check (list string))
    "the six the documentation describes, then SPY and TLT"
    [ "AAPL"; "MSFT"; "NVDA"; "JPM"; "XOM"; "CVX"; "SPY"; "TLT" ]
    (List.map book.Book.positions ~f:(fun p -> p.Book.Position_spec.symbol));
  Alcotest.(check (list (float 0.0)))
    "SPY and TLT held at zero" [ 0.0; 0.0 ]
    (List.filter_map book.Book.positions ~f:(fun p ->
         match p.Book.Position_spec.symbol with
         | "SPY" | "TLT" -> Some p.Book.Position_spec.qty
         | _ -> None));
  Alcotest.(check int) "seven limits" 7 (List.length book.Book.limits);
  (* Inert as shipped: no orders, and no alerting, until a person edits it. *)
  Alcotest.(check bool)
    "the desk ships disabled" true
    (Desk_spec.equal_trading book.Book.desk.Desk_spec.trading Desk_spec.Disabled);
  Alcotest.(check bool)
    "alerting ships off" false book.Book.alerts.Ohcamel.Config.Alerts.enabled;
  (* The kill switch lives inside alerting, so an armed switch under disabled
     alerting would be a configuration that does nothing. Shipped, both are
     off. *)
  Alcotest.(check bool)
    "the kill switch ships disarmed" false
    book.Book.alerts.Ohcamel.Config.Alerts.kill_switch_enabled

(* Ruling 2, as shipped: two strategies, one per symbol, both advisory. A book
   copied from this file shows a passing signal and sizes nothing until the
   owner writes (sizing live) by hand. *)
let test_the_example_book_registers_two_advisory_strategies () =
  let book = load () in
  let module S = Ohcamel.Config.Book.Signals_spec in
  match book.Book.signals with
  | None -> Alcotest.fail "the example book has no signals block"
  | Some { S.strategies } ->
      Alcotest.(check (list (triple string (list string) (float 0.0))))
        "one strategy per symbol, each at half the capital"
        [ ("exp_a01_spy", [ "SPY" ], 0.5); ("exp_a01_tlt", [ "TLT" ], 0.5) ]
        (List.map strategies ~f:(fun s ->
             (s.S.Strategy.name, s.S.Strategy.symbols, s.S.Strategy.capital_fraction)));
      Alcotest.(check (list string))
        "both advisory, neither live" [ "advisory"; "advisory" ]
        (List.map strategies ~f:(fun s -> S.sizing_to_string s.S.Strategy.sizing));
      Alcotest.(check (list int))
        "max_age 3, as written" [ 3; 3 ]
        (List.map strategies ~f:(fun s -> s.S.Strategy.max_age))

(* One cost configuration (Task 15). research/config/friction_v1.yaml is the
   file the backtest was costed with, and EXP-A01's pre-registration reads its
   spreads as the whole round-trip spread, "halved per fill". The book's
   spread_bps is a per-fill half-spread -- what one fill pays from the mid
   (Oms.model_half_spread_bps) -- so for each name the strategies trade, the
   book's number is the file's divided by 2: SPY 2.0 / 2 = 1.0 and TLT 3.0 /
   2 = 1.5. The file's own comment calls its numbers half-spreads; it
   contradicts both fdq's code and the hypothesis, and the hypothesis
   governs (the ledger's ruling 11b). The file is read as the lines under
   spread_bps_by_symbol, "  SYM: number", which is its whole shape there. The
   demo's simulated venue keeps a flat 5 bps: a departure docs/status.md
   records, because the demo is never evidence. *)
let friction_path = "../research/config/friction_v1.yaml"

let friction_spreads () =
  let lines = In_channel.read_lines friction_path in
  match
    List.drop_while lines ~f:(fun l ->
        not (String.equal (String.rstrip l) "spread_bps_by_symbol:"))
  with
  | [] -> Alcotest.failf "%s has no spread_bps_by_symbol" friction_path
  | _ :: rest ->
      List.take_while rest ~f:(String.is_prefix ~prefix:"  ")
      |> List.map ~f:(fun l ->
          match String.lsplit2 (String.strip l) ~on:':' with
          | Some (sym, v) -> (sym, Float.of_string (String.strip v))
          | None -> Alcotest.failf "%s: unreadable line %S" friction_path l)

let test_the_book's_half_spreads_are_friction_v1's_halved () =
  let book = load () in
  let file = friction_spreads () in
  List.iter
    [ ("SPY", 2.0); ("TLT", 3.0) ]
    ~f:(fun (sym, full) ->
      Alcotest.(check (option (float 0.0)))
        (sprintf "friction v1's %s round-trip spread, as the file says" sym)
        (Some full)
        (List.Assoc.find file sym ~equal:String.equal));
  List.iter [ "SPY"; "TLT" ] ~f:(fun sym ->
      let file_bps = List.Assoc.find_exn file sym ~equal:String.equal in
      Alcotest.(check (option (float 0.0)))
        (sprintf "the book's %s half-spread is the file's / 2" sym)
        (Some (file_bps /. 2.0))
        (List.Assoc.find book.Book.desk.Desk_spec.spread_bps sym ~equal:String.equal))

let suite =
  ( "example book",
    [
      Alcotest.test_case "the example book parses and ships inert" `Quick
        test_the_example_book_parses_and_ships_inert;
      Alcotest.test_case "the example book registers two advisory strategies" `Quick
        test_the_example_book_registers_two_advisory_strategies;
      Alcotest.test_case "the book's half-spreads are friction v1's, halved" `Quick
        test_the_book's_half_spreads_are_friction_v1's_halved;
    ] )
