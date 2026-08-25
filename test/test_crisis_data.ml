(* Unit tests for crisis_data.ml.

   The cache is a file format, and a file format's failure modes are all at its
   edges: a ragged row, a text field where a number belongs, a close of zero.
   Each of those has to be an error rather than a silently dropped row, because
   the alternative is a coverage test scored against a series that is quietly
   shorter or quietly wrong than the one the header claims.

   Everything below drives [of_string] with a literal rather than a fixture on
   disk -- the same split config.ml uses between [Book.of_string] and
   [Book.load], and for the same reason: a parser that can only be reached
   through the filesystem tends not to get tested at its edges at all.

   The one test that is not about parsing is the last. It checks that the
   portfolio return series comes out of the ENGINE rather than out of arithmetic
   in crisis_data.ml, using a two-name book whose weights are +0.5 and -0.5 so
   the expected returns can be read off the page. *)

open Core
module Crisis_data = Ohcamel.Crisis_data
open Ohcamel.Types

let feq = Alcotest.float 1e-12

let get name = function
  | Ok x -> x
  | Error e -> Alcotest.failf "%s: expected Ok, got %s" name (Error.to_string_hum e)

(* dune runs tests from inside _build, so the repository-relative cache path the
   binary uses does not resolve here. Walk up to the directory holding
   dune-project and point the loader at its docs/crisis.

   Worth doing rather than skipping these two tests: the committed CSVs are part
   of a published result, and "the cache is present, has six columns and enough
   sessions to be worth scoring" is exactly the assertion that fails when
   somebody deletes a window or truncates a file. *)
let repo_root =
  let rec up dir depth =
    if depth > 8 then None
    else
      match Sys_unix.file_exists (Filename.concat dir "dune-project") with
      | `Yes -> Some dir
      | `No | `Unknown -> up (Filename.concat dir Filename.parent_dir_name) (depth + 1)
  in
  up (Sys_unix.getcwd ()) 0

let crisis_dir () =
  match repo_root with
  | Some root -> Filename.concat root "docs/crisis"
  | None -> Alcotest.fail "could not locate the repository root from the test's cwd"

let expect_error name r =
  match r with
  | Error _ -> ()
  | Ok _ -> Alcotest.failf "%s: expected an error, got Ok" name

(* Two names, three sessions, and closes chosen so every return is a round
   number:

     A: 100 -> 110 -> 99     returns  +0.10, -0.10
     B:  50 ->  45 -> 54     returns  -0.10, +0.20

   Comment lines are present because the real files carry three of them, and the
   first is load-bearing -- it becomes the window's description. *)
let sample =
  "# A two-name window used only by the tests.\n\
   # Adjusted daily closes.\n\
   date,A,B\n\
   2020-01-02,100.0,50.0\n\
   2020-01-03,110.0,45.0\n\
   2020-01-06,99.0,54.0\n"

let a = Symbol.of_string "A"
let b = Symbol.of_string "B"

let test_parses () =
  let w = get "parse" (Crisis_data.of_string ~name:"sample" sample) in
  Alcotest.(check int) "sessions" 3 (Crisis_data.Window.sessions w);
  Alcotest.(check string) "name" "sample" (Crisis_data.Window.name w);
  Alcotest.(check string)
    "description comes from the first comment line"
    "A two-name window used only by the tests."
    (Crisis_data.Window.description w);
  Alcotest.(check (list string))
    "dates, ascending"
    [ "2020-01-02"; "2020-01-03"; "2020-01-06" ]
    (Array.to_list (Crisis_data.Window.dates w));
  Alcotest.(check (list string))
    "symbols come from the header" [ "A"; "B" ]
    (List.map (Crisis_data.Window.symbols w) ~f:Symbol.to_string);
  let closes = Crisis_data.Window.closes w in
  Alcotest.check feq "A's second close" 110.0 (Map.find_exn closes a).(1);
  Alcotest.check feq "B's third close" 54.0 (Map.find_exn closes b).(2)

(* Simple returns, through the same function the live REST backfill uses. n
   closes give n-1 returns, so the series is one shorter than [dates] -- which
   is exactly the off-by-one that would show up as a misaligned backtest if this
   module did its own differencing. *)
let test_returns () =
  let w = get "parse" (Crisis_data.of_string ~name:"sample" sample) in
  let r = Crisis_data.returns w in
  let ra = Map.find_exn r a and rb = Map.find_exn r b in
  Alcotest.(check int) "one fewer return than sessions" 2 (Array.length ra);
  Alcotest.check feq "A: 110/100 - 1" 0.10 ra.(0);
  Alcotest.check feq "A: 99/110 - 1" (-0.10) ra.(1);
  Alcotest.check feq "B: 45/50 - 1" (-0.10) rb.(0);
  Alcotest.check feq "B: 54/45 - 1" 0.20 rb.(1)

(* THE ONE THAT MATTERS.

   A book long A and short B in equal notional:

     A   price 100, qty  +2  ->  exposure  +200
     B   price  50, qty  -4  ->  exposure  -200

   gross = 400, so the weights are +0.5 and -0.5 and the book's return series is

     r_p(0) = 0.5(+0.10) + (-0.5)(-0.10) = +0.05 + 0.05 = +0.10
     r_p(1) = 0.5(-0.10) + (-0.5)(+0.20) = -0.05 - 0.10 = -0.15

   Both terms are non-zero in both periods and the short leg flips the sign of
   its contribution, so a sign error anywhere in the chain moves these numbers
   rather than cancelling out.

   What this really asserts is that the series came from graph.ml's
   [portfolio_returns] node. The alternative implementation -- four lines of
   sum(w_i r_i) in crisis_data.ml -- would produce these same two numbers today
   and would be free to drift from the engine the first time somebody changed a
   convention in one and not the other. The backtest would then be validating a
   model nobody runs. *)
let test_portfolio_returns_come_from_the_engine () =
  let w = get "parse" (Crisis_data.of_string ~name:"sample" sample) in
  let instruments =
    [
      { Instrument.symbol = a; sector = Sector.of_string "X" };
      { Instrument.symbol = b; sector = Sector.of_string "Y" };
    ]
  in
  match
    Crisis_data.portfolio_returns_of_book ~instruments
      ~positions:[ (a, Qty.of_float 2.0); (b, Qty.of_float (-4.0)) ]
      ~marks:[ (a, Price.of_float 100.0); (b, Price.of_float 50.0) ]
      w
  with
  | None -> Alcotest.fail "expected a return series"
  | Some rp ->
      Alcotest.(check int) "two periods" 2 (Array.length rp);
      Alcotest.check feq "0.5(+0.10) - 0.5(-0.10)" 0.10 rp.(0);
      Alcotest.check feq "0.5(-0.10) - 0.5(+0.20)" (-0.15) rp.(1)

(* Every one of these is a row that could plausibly appear in a hand-edited
   cache, and every one of them must stop the run rather than shorten the
   series. A dropped row is a session the coverage test never scores, and
   nothing in the output would say so. *)
let test_malformed_input_is_an_error () =
  expect_error "ragged row"
    (Crisis_data.of_string ~name:"x" "date,A,B\n2020-01-02,100.0,50.0\n2020-01-03,110.0\n");
  expect_error "text where a number belongs"
    (Crisis_data.of_string ~name:"x" "date,A\n2020-01-02,n/a\n");
  expect_error "a close of zero is not a price"
    (Crisis_data.of_string ~name:"x" "date,A\n2020-01-02,0.0\n");
  expect_error "a negative close is not a price"
    (Crisis_data.of_string ~name:"x" "date,A\n2020-01-02,-5.0\n");
  expect_error "header with no data rows" (Crisis_data.of_string ~name:"x" "date,A\n");
  expect_error "no header at all" (Crisis_data.of_string ~name:"x" "# only a comment\n");
  expect_error "header without a date column"
    (Crisis_data.of_string ~name:"x" "day,A\n2020-01-02,100.0\n");
  expect_error "header with no symbols"
    (Crisis_data.of_string ~name:"x" "date\n2020-01-02\n")

(* A missing cache fails loudly and names the command that fixes it. Asserted
   because the alternative -- falling back to the synthetic series -- would
   produce a table indistinguishable from the real one under a heading claiming
   it had been run against a real crisis. That is the single worst thing this
   module could do, so it gets a test rather than a comment. *)
let test_a_missing_cache_is_fatal_and_says_how_to_fix_it () =
  match Crisis_data.load ~dir:(crisis_dir ()) ~name:"no-such-window-exists" () with
  | Ok _ -> Alcotest.fail "a missing cache must not load"
  | Error e ->
      let message = Error.to_string_hum e in
      Alcotest.(check bool)
        "names the missing file" true
        (String.is_substring message ~substring:"no-such-window-exists.csv");
      Alcotest.(check bool)
        "names the command that repopulates it" true
        (String.is_substring message ~substring:"tools/fetch_crisis_data.py")

(* The committed cache is part of the repository's published result, so its
   presence and shape are asserted rather than assumed. This is the test that
   fails if somebody deletes a window, renames a column, or commits a file with
   two rows in it. *)
let test_the_committed_cache_loads () =
  List.iter Crisis_data.window_names ~f:(fun name ->
      let w = get name (Crisis_data.load ~dir:(crisis_dir ()) ~name ()) in
      Alcotest.(check (list string))
        (name ^ ": the six names of the synthetic book")
        [ "AAPL"; "CVX"; "JPM"; "MSFT"; "NVDA"; "XOM" ]
        (List.map (Crisis_data.Window.symbols w) ~f:Symbol.to_string);
      (* Enough sessions for the 60-day window to leave a testable number of
         forecasts behind. A coverage test on a handful of observations has no
         power to reject anything, so a short window is a silent no-op. *)
      Alcotest.(check bool)
        (Printf.sprintf "%s: at least 300 sessions (has %d)" name
           (Crisis_data.Window.sessions w))
        true
        (Crisis_data.Window.sessions w >= 300);
      let returns = Crisis_data.returns w in
      Map.iteri returns ~f:(fun ~key:symbol ~data ->
          Alcotest.(check int)
            (Printf.sprintf "%s/%s: one return per session gap" name
               (Symbol.to_string symbol))
            (Crisis_data.Window.sessions w - 1)
            (Array.length data);
          (* No adjusted daily equity return should be beyond +/-60%. This is
             the split check: an unadjusted 2-for-1 shows up as exactly -50% and
             would become the entire tail of a 60-day window at 95%. *)
          Array.iter data ~f:(fun r ->
              Alcotest.(check bool)
                (Printf.sprintf "%s/%s: %.4f is a plausible adjusted daily return" name
                   (Symbol.to_string symbol) r)
                true
                (Float.( < ) (Float.abs r) 0.60))))

let suite =
  ( "crisis_data",
    [
      Alcotest.test_case "parses the cache format" `Quick test_parses;
      Alcotest.test_case "returns, through the live path's own function" `Quick
        test_returns;
      Alcotest.test_case "THE BOOK'S SERIES COMES FROM THE ENGINE" `Quick
        test_portfolio_returns_come_from_the_engine;
      Alcotest.test_case "malformed input is an error, never a shorter series" `Quick
        test_malformed_input_is_an_error;
      Alcotest.test_case "a missing cache is fatal and says how to fix it" `Quick
        test_a_missing_cache_is_fatal_and_says_how_to_fix_it;
      Alcotest.test_case "the committed cache loads and is the right shape" `Quick
        test_the_committed_cache_loads;
    ] )
