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
   its six; this is the file a live host copies.

   The second half of this file is [Config.check] and the CLI that calls it
   (Task 13). Everything here still runs without the scheduler: [check-book] is
   a plain program, and a bad argument is refused before Async would start, so
   the cases that run the binary run a process that never opens a socket. Two
   cases hand the child three dummy credential values in a cleared environment
   so that [live] and [serve] reach their own refusals -- the universe cap, a
   book that is not there -- which come after Config.load and before the
   journal is opened or anything is connected to; that child starts Async's
   scheduler in its own process and exits under it, and still opens no
   socket. *)

open Core
module Book = Ohcamel.Config.Book
module Config = Ohcamel.Config
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module Summary = Ohcamel.Config.Summary

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

(* ------------------------------------------------------------------------- *)
(* check-book: every problem in one run                                       *)
(* ------------------------------------------------------------------------- *)

(* A book an operator has just typed, written where dune does not keep it.
   [Config.check] takes a path and not the contents, because the file that is
   not there is one of the answers it has to give. *)
let with_book contents ~f =
  let path = Stdlib.Filename.temp_file "t13-check-book" ".sexp" in
  Exn.protect
    ~f:(fun () ->
      Out_channel.write_all path ~data:contents;
      f path)
    ~finally:(fun () -> try Stdlib.Sys.remove path with _ -> ())

(* Two names and nothing else, so that each case below breaks exactly the part
   it is about. TECH and INDEX are the only sectors, which is what makes a limit
   on UTILITIES below an unknown sector rather than a typo nobody notices. *)
let book ~limits ~extra =
  sprintf
    "((cash 1000000.0) (positions (((symbol AAPL) (sector TECH) (qty 100.0)) ((symbol \
     SPY) (sector INDEX) (qty 0.0)))) (limits (%s)) %s)"
    limits extra

let refusal_of path =
  match Config.check path with
  | Ok _ -> Alcotest.failf "%s was accepted; this case needs it refused" path
  | Error errors -> errors

let names substring errors = List.exists errors ~f:(String.is_substring ~substring)

(* Five separate mistakes of five kinds, in one file: a VaR limit at instrument
   scope (a kind/scope pairing the graph refuses), a limit on a sector the book
   does not hold, a clear_below outside (0, 1], a 150% price collar, and a
   strategy naming a symbol the book does not declare (R7). Shared by the
   library case and the CLI case below, so that both are held to the same five.

   Each one is found by a different validator -- limits, alerts, the desk,
   signals -- which is the point: a checker that stopped at the first would send
   its reader back four more times, and the owner runs this after every edit. *)
let five_problem_limits =
  "((name var-aapl) (scope (Instrument AAPL)) (kind (Value_at_risk 1000.0))) ((name \
   util-cap) (scope (Sector UTILITIES)) (kind (Gross_notional 1000.0)))"

let five_problem_extra =
  "(alerts ((enabled true) (sinks (Log)) (clear_below 1.5) (kill_switch_enabled false) \
   (kill_switch_trips_on ()))) (desk ((price_collar 1.5))) (signals ((strategies (((name \
   s_one) (symbols (GOOG)) (capital_fraction 0.5))))))"

let five_problem_book = book ~limits:five_problem_limits ~extra:five_problem_extra

let five_problem_substrings =
  [ "var-aapl"; "UTILITIES"; "clear_below"; "price_collar"; "GOOG" ]

let test_check_accepts_the_example_book_and_says_what_it_holds () =
  match Config.check path with
  | Error errors ->
      Alcotest.failf "the example book was refused: %s" (String.concat ~sep:"; " errors)
  | Ok summary ->
      Alcotest.(check (list string))
        "the universe, in book order"
        [ "AAPL"; "MSFT"; "NVDA"; "JPM"; "XOM"; "CVX"; "SPY"; "TLT" ]
        (List.map summary.Summary.universe ~f:(fun h -> h.Summary.Holding.symbol));
      Alcotest.(check (list string))
        "the five sectors those names sit in"
        [ "ENERGY"; "FINANCIALS"; "INDEX"; "TECH"; "TREASURIES" ]
        summary.Summary.sectors;
      (* Rendered through the graph's own renderers, so the summary says what the
         engine will call each scope and each threshold rather than a second
         spelling of it. *)
      Alcotest.(check (list (triple string string string)))
        "seven limits, with the scope and threshold the graph will read"
        [
          ("aapl-cap", "instrument:AAPL", "$150000.00");
          ("nvda-cap", "instrument:NVDA", "$60000.00");
          ("tech-cap", "sector:TECH", "$260000.00");
          ("energy-cap", "sector:ENERGY", "$150000.00");
          ("book-cap", "portfolio", "$500000.00");
          ("var-cap", "portfolio", "$12000.00");
          ("dd-cap", "portfolio", "2.00%");
        ]
        (List.map summary.Summary.limits ~f:(fun l ->
             ( l.Summary.Limit_line.name,
               l.Summary.Limit_line.scope,
               l.Summary.Limit_line.threshold )));
      Alcotest.(check (list string))
        "five notional caps, one VaR cap, one drawdown cap"
        [
          "gross_notional";
          "gross_notional";
          "gross_notional";
          "gross_notional";
          "gross_notional";
          "value_at_risk";
          "max_drawdown";
        ]
        (List.map summary.Summary.limits ~f:(fun l -> l.Summary.Limit_line.kind));
      Alcotest.(check (list (pair string string)))
        "two strategies, both advisory"
        [ ("exp_a01_spy", "advisory"); ("exp_a01_tlt", "advisory") ]
        (List.map summary.Summary.strategies ~f:(fun s ->
             (s.Summary.Strategy_line.name, s.Summary.Strategy_line.sizing)));
      Alcotest.(check (option string))
        "the desk ships disabled" (Some "disabled")
        (List.Assoc.find summary.Summary.desk "trading" ~equal:String.equal);
      Alcotest.(check (option string))
        "alerting ships off" (Some "false")
        (List.Assoc.find summary.Summary.alerts "enabled" ~equal:String.equal);
      Alcotest.(check (option string))
        "and the kill switch disarmed" (Some "disabled")
        (List.Assoc.find summary.Summary.alerts "kill_switch" ~equal:String.equal)

let test_check_names_an_unknown_limit_kind () =
  with_book
    (book ~limits:"((name dd-cap) (scope Portfolio) (kind (Draw_down 0.02)))" ~extra:"")
    ~f:(fun p ->
      let errors = refusal_of p in
      (* A file that does not parse has no further problems to report: there is
         no book yet to validate. *)
      Alcotest.(check int) "one problem, because nothing parsed" 1 (List.length errors);
      Alcotest.(check bool)
        "and it names the constructor nobody wrote a case for" true
        (names "Draw_down" errors))

let test_check_names_a_strategy_symbol_outside_the_universe () =
  with_book
    (book ~limits:""
       ~extra:
         "(signals ((strategies (((name s_one) (symbols (GOOG)) (capital_fraction \
          0.5))))))") ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "one problem" 1 (List.length errors);
      Alcotest.(check bool) "R7's symbol, named" true (names "GOOG" errors);
      Alcotest.(check bool)
        "beside the strategy that named it" true (names "s_one" errors))

let test_check_reports_every_problem_in_one_run () =
  with_book five_problem_book ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "all five, from one run" 5 (List.length errors);
      List.iter five_problem_substrings ~f:(fun substring ->
          Alcotest.(check bool)
            (sprintf "the list names %s" substring)
            true (names substring errors)))

let test_check_reports_a_duplicate_limit_name_beside_each_bad_limit () =
  (* Two limits with one name is a rule about the SET, which no single limit can
     fail, and it is checked before any single limit is looked at. A negative
     threshold is a rule about one limit. Each has to survive the other being
     present, or the set rule becomes the only thing a book with both ever
     reports. *)
  let limits =
    "((name cap) (scope Portfolio) (kind (Gross_notional 1000.0))) ((name cap) (scope \
     (Instrument SPY)) (kind (Gross_notional 2000.0))) ((name neg) (scope Portfolio) \
     (kind (Gross_notional -1.0)))"
  in
  with_book (book ~limits ~extra:"") ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int)
        "the duplicate and the negative threshold" 2 (List.length errors);
      Alcotest.(check bool) "the negative threshold, named" true (names "\"neg\"" errors);
      Alcotest.(check bool)
        "and the duplicate name, which only the whole set shows" true
        (names "duplicate limit name" errors))

let test_check_reports_the_live_sum_beside_a_strategy's_own_problem () =
  (* Signals_spec.validate checks the duplicate-name rule before the members but
     the live fractions' sum AFTER them, so on the startup path a member's own
     problem hides the sum, and round 1's review found check-book hiding it for
     one round too (N1). Two live strategies at 0.7 each: 0.7 + 0.7 = 1.4, exact
     in binary because doubling is, and %g prints it as "1.4". A third names
     GOOG, which the book does not hold. Both problems, in one run. *)
  let extra =
    "(signals ((strategies (((name s_a) (symbols (SPY)) (sizing Live) (capital_fraction \
     0.7)) ((name s_b) (symbols (AAPL)) (sizing Live) (capital_fraction 0.7)) ((name \
     s_c) (symbols (GOOG)) (capital_fraction 0.5))))))"
  in
  with_book (book ~limits:"" ~extra) ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "GOOG and the sum, in one run" 2 (List.length errors);
      Alcotest.(check bool) "s_c's GOOG" true (names "GOOG" errors);
      Alcotest.(check bool) "and the live sum of 1.4" true (names "sum to 1.4" errors))

let test_check_reports_a_duplicate_strategy_name_beside_a_member's_problem () =
  (* The other set rule, and the reason the whole set is still validated as a
     whole: two strategies named s_one, the first naming GOOG. A whole-set run
     over only the strategies that passed alone would see one s_one and no
     duplicate, so this case holds the run over all of them in place. *)
  let extra =
    "(signals ((strategies (((name s_one) (symbols (GOOG)) (capital_fraction 0.5)) \
     ((name s_one) (symbols (SPY)) (capital_fraction 0.5))))))"
  in
  with_book (book ~limits:"" ~extra) ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "GOOG and the duplicate name" 2 (List.length errors);
      Alcotest.(check bool) "the GOOG" true (names "GOOG" errors);
      Alcotest.(check bool)
        "and the name two strategies share" true
        (names "two strategies have this name" errors))

let test_check_on_a_file_that_is_not_there_is_one_sentence () =
  let errors = refusal_of "../no-such-book-t13.sexp" in
  Alcotest.(check int) "one sentence, and no exception" 1 (List.length errors);
  Alcotest.(check bool)
    "naming the path that was typed" true
    (names "no-such-book-t13.sexp" errors)

(* ------------------------------------------------------------------------- *)
(* The universe: what the engine refuses AFTER Config.load                     *)
(* ------------------------------------------------------------------------- *)

(* Three refusals the startup path applies to the instrument list, none of them
   in Book.of_string: Graph.create refuses an empty list and a duplicate symbol
   (lib/graph.ml), and run_live refuses more names than the free Alpaca plan
   streams (bin/main.ml, reading Config.universe_cap). Round 1's review found
   check-book accepting all three and printing "is a book this build would start
   on" for each. deploy.sh will run check-book before the restart precisely so
   the engine never dies on a book at startup, and for each of these the deploy
   would have proceeded and the live desk gone down at Graph.create, with the
   checker's blessing on the log. *)

(* The copy-paste edit: AAPL twice, and nothing else wrong. The copied row has
   its sector edited and its symbol not, so the two rows differ as whole
   instruments and agree only on the symbol. That is the value at which the
   graph's comparison (Symbol.compare over Instrument.symbol, lib/graph.ml) and
   the one a later edit would reach for (Types.Instrument.compare, which the
   derived compare invites) DISAGREE. Round 2's review found both rows in TECH,
   where the two find the same duplicate, and a checker comparing whole
   instruments passed every case while Graph.create refused the book. *)
let duplicate_symbol_book =
  "((cash 1000000.0) (positions (((symbol AAPL) (sector TECH) (qty 100.0)) ((symbol \
   AAPL) (sector INDEX) (qty 50.0)))) (limits ()))"

let empty_universe_book = "((cash 1000000.0) (positions ()) (limits ()))"

(* A book of [n] names S01 .. Sn, one sector, no limits: the only thing that can
   be wrong with it is its size. *)
let book_of_n_names n =
  sprintf "((cash 1000000.0) (positions (%s)) (limits ()))"
    (String.concat ~sep:" "
       (List.init n ~f:(fun i ->
            sprintf "((symbol S%02d) (sector TECH) (qty 1.0))" (i + 1))))

let test_check_refuses_a_duplicate_position_symbol () =
  with_book duplicate_symbol_book ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "one problem: the duplicate" 1 (List.length errors);
      (* The symbol, quoted the way the graph quotes it, and "twice", the graph's
         own word for it. *)
      Alcotest.(check bool) "naming AAPL" true (names "\"AAPL\"" errors);
      Alcotest.(check bool) "as appearing twice" true (names "twice" errors);
      (* The sentence is restated from Graph.create, so it is held to the
         refusal it restates: the same list, handed to the graph, is refused
         with the same word. The duplicate check is the graph's second act, so
         nothing is built; a graph that did come back is destroyed. *)
      let instruments = Book.instruments (Or_error.ok_exn (Book.load p)) in
      Alcotest.(check bool)
        "and Graph.create refuses the same list" true
        (match
           Ohcamel.Graph.create ~instruments ~limits:[] ~confidence:0.95 ~return_window:60
             ()
         with
        | exception Invalid_argument m -> String.is_substring m ~substring:"appears twice"
        | graph ->
            Ohcamel.Graph.destroy graph;
            false))

let test_check_refuses_an_empty_universe () =
  with_book empty_universe_book ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "one problem: no instrument" 1 (List.length errors);
      Alcotest.(check bool)
        "saying the graph needs at least one" true (names "at least one" errors))

let test_check_refuses_a_book_over_the_universe_cap_and_accepts_one_at_it () =
  (* The cap is 30 because Alpaca's free plan streams at most 30 symbols. It is
     pinned as a number here so that moving it is a deliberate edit to this case
     as well as to the constant. 31 = 30 + 1 is the smallest book over the cap
     and 30 the largest book under it, so the pair fixes the boundary exactly: a
     cap of 29 fails the second half, a cap of 31 -- or no cap at all -- fails
     the first. *)
  Alcotest.(check int) "the cap is Alpaca's free-plan 30" 30 Config.universe_cap;
  with_book (book_of_n_names 31) ~f:(fun p ->
      let errors = refusal_of p in
      Alcotest.(check int) "one problem: the size" 1 (List.length errors);
      Alcotest.(check bool) "naming the 31 names" true (names "31 names" errors);
      Alcotest.(check bool) "and the cap of 30" true (names "at most 30" errors));
  with_book (book_of_n_names 30) ~f:(fun p ->
      match Config.check p with
      | Error errors ->
          Alcotest.failf "30 names were refused: %s" (String.concat ~sep:"; " errors)
      | Ok summary ->
          Alcotest.(check int)
            "30 names, accepted" 30
            (List.length summary.Summary.universe));
  (* run_live applies the same cap before it connects, and the live case in
     the CLI section observes both halves of it (31 refused with its sentence,
     30 let through to the fuse). What that cannot show is the NUMBER as the
     engine spells it -- the sentence prints the constant whatever the
     comparison reads -- so the one line that carries the comparison is held
     here as text, the way the shared-minimum case reads lib/factor_model.ml.
     The whole comparison and not a substring of it: round 2's review found
     "Config.universe_cap" satisfied by a comment and by the refusal's own text
     while the comparison read a literal 29, and "+ 1" past the constant
     satisfied it too; a literal, an offset or another operator on this line
     now fails. ocamlformat decides the spelling, so the spelling is stable.
     The negative half stays: a second definition is the drift this section is
     about. *)
  let source = In_channel.read_all "../bin/main.ml" in
  Alcotest.(check bool)
    "run_live compares the universe against Config.universe_cap, and nothing else" true
    (String.is_substring source
       ~substring:"if List.length instruments > Config.universe_cap then (");
  Alcotest.(check bool)
    "and defines no cap of its own" false
    (String.is_substring source ~substring:"let universe_cap")

(* ------------------------------------------------------------------------- *)
(* The CLI: a bad argument is a usage message and exit 2, never a backtrace   *)
(* ------------------------------------------------------------------------- *)

(* The binary, run the way an operator runs it -- deploy.sh runs check-book
   before it restarts the engine, and the exit code is the whole of what it
   reads, so the exit code is what these cases assert.

   Most modes below never enter Async: check-book is a plain program, and a bad
   argument is refused in the dispatcher, before [block_on_async_exn]. Two
   cases run live and serve far enough to reach a refusal of their own, which
   is past Config.load and so past the three credential variables. They are
   given [cleared_environment] -- three dummy values and a fuse, never this
   process's environment, which on the owner's machine may hold the real keys
   -- and the refusal comes before the journal is opened or the first request
   is made, so the values go nowhere; a child that got past it would stop at
   the fuse. That child starts Async's scheduler in its own process and exits
   under it; it opens no socket, and this suite's own process still starts
   none.

   That holds today, and the runner is written for the day it stops holding. A
   regression in the port parse that reads "0x1f90" as 8080 -- Core's
   Int.of_string does -- turns `demo 0x1f90` into `demo 8080`, and the process
   this case spawned is then a demo server on a port, which never exits. Under a
   runner built on Sys.command that is not a failure but a hang, and it was
   observed when the rule was mutated on purpose: the suite sat on a server it
   had started, and the server outlived the runner. So the child is forked with
   no shell in between and waited on against a deadline; past it the child is
   killed and the case reports a status no exit code can be, so the failure
   reads as "killed", not as a wrong number, and nothing outlives the test.

   Twenty seconds is the bound; every mode here answers in well under one,
   because each is decided before anything is connected to. *)
let exe = "../bin/main.exe"
let deadline_s = 20.0

(* Negative, because an exit code is 0..255: a case asserting `exit 2` against
   it fails, with a number that says the child was killed. *)
let killed = -1

(* Three values that are not keys, named so that nothing reading a log could
   mistake them for one, in the shape deploy/ci.env uses. Config.load reads
   these three, and nothing that would send them is reached. *)
let dummy_credentials =
  [|
    "ALPACA_API_KEY=t13-dummy-not-a-key";
    "ALPACA_SECRET_KEY=t13-dummy-not-a-secret";
    "FRED_API_KEY=t13-dummy-not-a-key";
  |]

(* The fuse, for a child that gets PAST the refusal a case expects. run_live's
   acts after the cap are, in order: the COHTTP_DEBUG refusal, the intake, the
   journal, the reports, the graph, the feeds. Two of them are made stops.
   COHTTP_DEBUG set to anything is refused right after the cap, before anything
   is opened, because with it cohttp-async would log key headers -- the
   engine's own promise that no request follows, which is the promise a test
   needs. Behind it the journal is pointed at a file in a directory that does
   not exist, so a child that somehow reached the journal would exit there,
   before the reports and the feeds. Under every mutation of the engine a child
   given this environment therefore creates no file and opens no socket, and
   its output says which stop it reached. Learned on the way here: before the
   fuse, the cap mutated to "+ 1" let the 31-name child past the cap, and it
   exited 1 somewhere later, within the second, with the case unable to say
   where. *)
let fuse =
  [|
    "COHTTP_DEBUG=t13-fuse";
    "OHCAMEL_JOURNAL="
    ^ Stdlib.Filename.concat
        (Stdlib.Filename.get_temp_dir_name ())
        "t13-no-such-dir/desk.db";
  |]

(* The whole of the child's environment when a case gives one: create_process_env
   replaces, it does not extend. Without it the child inherits this process's,
   as an operator's shell would hand it. *)
let cleared_environment = Array.append dummy_credentials fuse

let run ?env (args : string list) : int * string =
  let out = Stdlib.Filename.temp_file "t13-cli" ".out" in
  Exn.protect
    ~f:(fun () ->
      let fd = Caml_unix.openfile out [ Caml_unix.O_WRONLY; Caml_unix.O_TRUNC ] 0o600 in
      let argv = Array.of_list (exe :: args) in
      let pid =
        Exn.protect
          ~f:(fun () ->
            match env with
            | None -> Caml_unix.create_process exe argv Caml_unix.stdin fd fd
            | Some env -> Caml_unix.create_process_env exe argv env Caml_unix.stdin fd fd)
          ~finally:(fun () -> Caml_unix.close fd)
      in
      let started = Caml_unix.gettimeofday () in
      let rec wait () =
        match Caml_unix.waitpid [ Caml_unix.WNOHANG ] pid with
        | 0, _ ->
            if Float.(Caml_unix.gettimeofday () -. started > deadline_s) then (
              Caml_unix.kill pid Stdlib.Sys.sigkill;
              ignore (Caml_unix.waitpid [] pid : int * Caml_unix.process_status);
              killed)
            else (
              Caml_unix.sleepf 0.01;
              wait ())
        | _, Caml_unix.WEXITED code -> code
        | _, (Caml_unix.WSIGNALED _ | Caml_unix.WSTOPPED _) -> killed
      in
      let status = wait () in
      (status, In_channel.read_all out))
    ~finally:(fun () -> try Stdlib.Sys.remove out with _ -> ())

let usage_printed out =
  String.is_substring out ~substring:"ohcamel -- reactive risk and limits engine"

(* An uncaught exception reaches the terminal as "Fatal error: exception ...".
   Asserting its absence is the point of the whole mode: a person who mistyped a
   port is owed a usage message and not a stack trace. *)
let no_backtrace out = not (String.is_substring out ~substring:"Fatal error")

let test_check_book_exits_zero_on_the_example_book () =
  let status, out = run [ "check-book"; path ] in
  Alcotest.(check int) "exit 0" 0 status;
  List.iter [ "AAPL"; "aapl-cap"; "exp_a01_spy"; "trading"; "disabled" ]
    ~f:(fun substring ->
      Alcotest.(check bool)
        (sprintf "the summary says %s" substring)
        true
        (String.is_substring out ~substring))

let test_check_book_exits_one_and_lists_every_problem () =
  with_book five_problem_book ~f:(fun p ->
      let status, out = run [ "check-book"; p ] in
      Alcotest.(check int) "exit 1" 1 status;
      (* One bullet per problem. An operator reads the bullets, so the count of
         them is the assertion, not the length of the output. *)
      Alcotest.(check int)
        "five bullets" 5
        (List.count (String.split_lines out) ~f:(String.is_prefix ~prefix:"  - "));
      List.iter five_problem_substrings ~f:(fun substring ->
          Alcotest.(check bool)
            (sprintf "the output names %s" substring)
            true
            (String.is_substring out ~substring));
      Alcotest.(check bool) "and no backtrace" true (no_backtrace out))

let test_check_book_with_no_path_reads_the_default_book () =
  (* The same resolution serve now uses: a path if one is given, book.sexp if
     not. The suite runs in _build/default/test, which has no book.sexp, so the
     message has to name the default rather than ask what file was meant. *)
  if Stdlib.Sys.file_exists "book.sexp" then
    Alcotest.fail
      "there is a book.sexp beside the test runner; this case needs the default to be \
       absent";
  let status, out = run [ "check-book" ] in
  Alcotest.(check int) "exit 1" 1 status;
  Alcotest.(check bool)
    "naming the default it looked for" true
    (String.is_substring out ~substring:"book.sexp");
  Alcotest.(check bool) "and no backtrace" true (no_backtrace out)

let test_check_book_exits_one_on_a_book_the_engine_refuses_at_startup () =
  (* The three books of round 1's finding, through the binary deploy.sh will
     run. Each is exit 1 with one bullet, and the last line no longer says the
     engine would start on it. *)
  List.iter
    [
      ("a duplicate symbol", duplicate_symbol_book, "\"AAPL\" appears twice");
      ("an empty universe", empty_universe_book, "at least one");
      ("31 names", book_of_n_names 31, "31 names");
    ]
    ~f:(fun (what, contents, sentence) ->
      with_book contents ~f:(fun p ->
          let status, out = run [ "check-book"; p ] in
          Alcotest.(check int) (sprintf "%s exits 1" what) 1 status;
          Alcotest.(check int)
            (sprintf "%s is one bullet" what)
            1
            (List.count (String.split_lines out) ~f:(String.is_prefix ~prefix:"  - "));
          Alcotest.(check bool)
            (sprintf "%s: the bullet says %s" what sentence)
            true
            (String.is_substring out ~substring:sentence);
          Alcotest.(check bool)
            (sprintf "%s: and nothing says the engine would start on it" what)
            false
            (String.is_substring out ~substring:"would start on");
          Alcotest.(check bool) (sprintf "%s: no backtrace" what) true (no_backtrace out)))

let test_live_applies_the_universe_cap_before_it_connects () =
  (* The engine's half of the cap, APPLIED, which no source line can show. Both
     halves, as in the library case: 31 = 30 + 1 is the smallest book over the
     cap and 30 the largest under it. With the dummy values and the fuse as the
     child's whole environment, live on 31 names is exit 1 with run_live's own
     sentence and never reaches the fuse; live on 30 names passes the cap -- the
     sentence is absent -- and is stopped by the fuse one act later, naming
     COHTTP_DEBUG, before the journal, the reports or a feed. A cap of 29 fails
     the second half, a cap of 31 or none fails the first. The comparison line
     pinned in the library case holds the number as text; this holds what the
     binary does with it. *)
  let cap_sentence out = String.is_substring out ~substring:"at most 30" in
  let fuse_sentence out = String.is_substring out ~substring:"COHTTP_DEBUG is set" in
  with_book (book_of_n_names 31) ~f:(fun p ->
      let status, out = run ~env:cleared_environment [ "live"; p ] in
      Alcotest.(check int) "31 names: exit 1" 1 status;
      Alcotest.(check bool)
        "31 names: naming the 31" true
        (String.is_substring out ~substring:"31 names");
      Alcotest.(check bool) "31 names: and the cap of 30" true (cap_sentence out);
      Alcotest.(check bool)
        "31 names: refused at the cap, before the fuse" false (fuse_sentence out);
      Alcotest.(check bool) "31 names: no backtrace" true (no_backtrace out));
  with_book (book_of_n_names 30) ~f:(fun p ->
      let status, out = run ~env:cleared_environment [ "live"; p ] in
      Alcotest.(check int) "30 names: exit 1, at the fuse" 1 status;
      Alcotest.(check bool) "30 names: the cap let it through" false (cap_sentence out);
      Alcotest.(check bool)
        "30 names: and the fuse stopped it, naming COHTTP_DEBUG" true (fuse_sentence out);
      Alcotest.(check bool) "30 names: no backtrace" true (no_backtrace out))

let test_check_book_prints_a_multi_line_parse_error_as_one_bullet () =
  (* Sexplib's parse error echoes the input, so an unclosed book written over
     three lines is echoed over three lines. Config.check folds each sentence
     onto one line so that the bullet count still means "problems"; without the
     fold the bullet would be the first of three lines and the file's own text
     would follow it, for an operator to count as more problems or as none.
     Round 1's review mutated the fold to the identity and nothing failed (N2);
     this case is the pin. *)
  with_book
    "((cash 1000000.0)\n\
    \ (positions (((symbol AAPL) (sector TECH) (qty 100.0))))\n\
    \ (limits ())\n" ~f:(fun p ->
      let status, out = run [ "check-book"; p ] in
      Alcotest.(check int) "exit 1" 1 status;
      let lines =
        List.filter (String.split_lines out) ~f:(fun l ->
            not (String.is_empty (String.strip l)))
      in
      Alcotest.(check int)
        "one bullet" 1
        (List.count lines ~f:(String.is_prefix ~prefix:"  - "));
      Alcotest.(check bool)
        "and it is the last line: nothing of the file spills after it" true
        (match List.last lines with
        | Some l -> String.is_prefix l ~prefix:"  - "
        | None -> false);
      Alcotest.(check bool)
        "the one line says the book does not parse" true
        (String.is_substring out ~substring:"does not parse"))

let test_serve_with_a_port_that_is_not_a_number_prints_the_usage_and_exits_two () =
  (* "0x1f90" is the reason the rule is "digits only" and not Int.of_string:
     Core reads it as 8080, and a port nobody typed is worse than a refusal.
     "1_000" reads as 1000 for the same reason, and "0" and "99999" are numbers
     that are not ports. *)
  List.iter [ "abc"; "80a"; "0x1f90"; "1_000"; "0"; "99999"; "-1" ] ~f:(fun raw ->
      let status, out = run [ "serve"; raw ] in
      Alcotest.(check int) (sprintf "serve %s exits 2" raw) 2 status;
      Alcotest.(check bool)
        (sprintf "serve %s prints the usage" raw)
        true (usage_printed out);
      Alcotest.(check bool)
        (sprintf "serve %s prints no backtrace" raw)
        true (no_backtrace out))

let test_demo_with_a_port_that_is_not_a_number_prints_the_usage_and_exits_two () =
  (* The second of the two raising parses. It is not the one the plan names, and
     a fix that left it would have left the demo -- the mode a stranger runs
     first -- throwing on a typo. *)
  List.iter [ "abc"; "0x1f90"; "70000" ] ~f:(fun raw ->
      let status, out = run [ "demo"; raw ] in
      Alcotest.(check int) (sprintf "demo %s exits 2" raw) 2 status;
      Alcotest.(check bool)
        (sprintf "demo %s prints the usage" raw)
        true (usage_printed out);
      Alcotest.(check bool)
        (sprintf "demo %s prints no backtrace" raw)
        true (no_backtrace out))

let test_serve_takes_a_port_and_then_a_book () =
  (* serve PORT [BOOK], where it took only PORT and always read the default.
     A book path in the port's place is a bad argument, and a third argument is
     one too -- both are how a grammar gets discovered by an operator rather
     than guessed at. *)
  let status, out = run [ "serve"; path ] in
  Alcotest.(check int) "a book path where the port goes exits 2" 2 status;
  Alcotest.(check bool)
    "saying it is not a port" true
    (String.is_substring out ~substring:"is not a port");
  let status, out = run [ "serve"; "8080"; "a.sexp"; "b.sexp" ] in
  Alcotest.(check int) "a third argument exits 2" 2 status;
  Alcotest.(check bool) "with the usage" true (usage_printed out);
  let status, help = run [ "--help" ] in
  Alcotest.(check int) "--help exits 0" 0 status;
  Alcotest.(check bool)
    "and the usage advertises the book argument" true
    (String.is_substring help ~substring:"serve [port] [book.sexp]");
  (* That the path it read is the path it RUNS on cannot be seen from outside
     without starting the scheduler, and this suite starts none: serve's next act
     after the parse is Config.load, which asks for three credentials. So the one
     line that carries the argument is pinned as text, the way the one-submit-site
     case reads desk/ and bin/ -- it is the line the plan names, where serve
     passed ~book_path:Config.default_book_path outright, and a refactor that
     moves it should say so out loud rather than quietly go back to the default.
     ocamlformat decides the spelling, so the spelling is stable. *)
  let source = In_channel.read_all "../bin/main.ml" in
  Alcotest.(check bool)
    "serve's second positional becomes the book it runs on" true
    (String.is_substring source
       ~substring:"| [ p; book ] -> (port_arg ~mode:\"serve\" p, book)");
  Alcotest.(check bool)
    "and no mode hands run_live the default outright any more" false
    (String.is_substring source ~substring:"~book_path:Config.default_book_path")

let suite =
  ( "example book",
    [
      Alcotest.test_case "the example book parses and ships inert" `Quick
        test_the_example_book_parses_and_ships_inert;
      Alcotest.test_case "the example book registers two advisory strategies" `Quick
        test_the_example_book_registers_two_advisory_strategies;
      Alcotest.test_case "the book's half-spreads are friction v1's, halved" `Quick
        test_the_book's_half_spreads_are_friction_v1's_halved;
      Alcotest.test_case "check accepts the example book and says what it holds" `Quick
        test_check_accepts_the_example_book_and_says_what_it_holds;
      Alcotest.test_case "check names an unknown limit kind" `Quick
        test_check_names_an_unknown_limit_kind;
      Alcotest.test_case "check names a strategy symbol outside the universe" `Quick
        test_check_names_a_strategy_symbol_outside_the_universe;
      Alcotest.test_case "check reports every problem in one run" `Quick
        test_check_reports_every_problem_in_one_run;
      Alcotest.test_case "check reports a duplicate limit name beside each bad limit"
        `Quick test_check_reports_a_duplicate_limit_name_beside_each_bad_limit;
      Alcotest.test_case "check reports the live sum beside a strategy's own problem"
        `Quick test_check_reports_the_live_sum_beside_a_strategy's_own_problem;
      Alcotest.test_case
        "check reports a duplicate strategy name beside a member's problem" `Quick
        test_check_reports_a_duplicate_strategy_name_beside_a_member's_problem;
      Alcotest.test_case "check on a file that is not there is one sentence" `Quick
        test_check_on_a_file_that_is_not_there_is_one_sentence;
      Alcotest.test_case "check refuses a duplicate position symbol" `Quick
        test_check_refuses_a_duplicate_position_symbol;
      Alcotest.test_case "check refuses an empty universe" `Quick
        test_check_refuses_an_empty_universe;
      Alcotest.test_case
        "check refuses a book over the universe cap and accepts one at it" `Quick
        test_check_refuses_a_book_over_the_universe_cap_and_accepts_one_at_it;
      Alcotest.test_case "check-book exits zero on the example book" `Quick
        test_check_book_exits_zero_on_the_example_book;
      Alcotest.test_case "check-book exits one and lists every problem" `Quick
        test_check_book_exits_one_and_lists_every_problem;
      Alcotest.test_case "check-book with no path reads the default book" `Quick
        test_check_book_with_no_path_reads_the_default_book;
      Alcotest.test_case "check-book exits one on a book the engine refuses at startup"
        `Quick test_check_book_exits_one_on_a_book_the_engine_refuses_at_startup;
      Alcotest.test_case "live applies the universe cap before it connects" `Quick
        test_live_applies_the_universe_cap_before_it_connects;
      Alcotest.test_case "check-book prints a multi-line parse error as one bullet" `Quick
        test_check_book_prints_a_multi_line_parse_error_as_one_bullet;
      Alcotest.test_case "serve with a port that is not a number prints the usage" `Quick
        test_serve_with_a_port_that_is_not_a_number_prints_the_usage_and_exits_two;
      Alcotest.test_case "demo with a port that is not a number prints the usage" `Quick
        test_demo_with_a_port_that_is_not_a_number_prints_the_usage_and_exits_two;
      Alcotest.test_case "serve takes a port and then a book" `Quick
        test_serve_takes_a_port_and_then_a_book;
    ] )
