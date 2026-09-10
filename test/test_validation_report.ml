(* Reproducibility pins for validation_report.ml.

   THESE ARE PINS, NOT DERIVATIONS. Every expected value in the two table
   tests below is read out of web/quoted.json (Phase 1's transcription of
   README.md, compiled in as Quoted.json), and what is asserted is that the
   library reproduces the README's nine-by-nine tables exactly: exceptions,
   zones and verdicts to the integer and the word, p-values to the four
   decimals the README prints, Weibull shapes to two. Nothing here says WHY
   the vol-regime parametric row is rejected; var_backtest.ml's own tests do
   that against hand-derived statistics. What this file says is that moving the
   battery out of bin/main.ml did not move a single cell of the published
   result -- which is the byte-identical gate, restated as a unit test that
   runs without the CLI.

   The remaining tests are hand derivations about STRUCTURE: the order the
   generator consumes its stream in, the hit indices, the series lengths. *)

open Core
module Validation_report = Ohcamel.Validation_report
module Var_backtest = Ohcamel.Var_backtest
module Synthetic_book = Ohcamel.Synthetic_book
module U = Yojson.Safe.Util

let quoted = lazy (Yojson.Safe.from_string Ohcamel.Quoted.json)
let quoted_rows table = U.to_list (U.member "rows" (U.member table (Lazy.force quoted)))

(* One README row, found by its label column and its estimator string -- the
   same two strings the CLI prints in its first two columns. *)
let quoted_row table ~key ~value ~estimator =
  List.find_exn (quoted_rows table) ~f:(fun r ->
      String.equal (U.to_string (U.member key r)) value
      && String.equal (U.to_string (U.member "estimator" r)) estimator)

(* Compared as the strings printf would print, because the README's cells ARE
   printf's output ("%.4f" for p-values, "%.2f" for shapes, "%.1f" for the
   expected count). Rounding the computed float and comparing with a tolerance
   would disagree with the README at an exact tie and agree everywhere else,
   which is a worse test than the one that asks the same question the README
   asked. *)
let check_row_against_quoted ~table ~key (row : Validation_report.Row.t) =
  let r = row.Validation_report.Row.report in
  let estimator = Var_backtest.Estimator.to_string (Var_backtest.estimator r) in
  let q = quoted_row table ~key ~value:row.Validation_report.Row.label ~estimator in
  let where what =
    Printf.sprintf "%s/%s: %s" row.Validation_report.Row.label estimator what
  in
  let same fmt name value =
    Alcotest.(check string)
      (where name)
      (Printf.sprintf fmt (U.to_number (U.member name q)))
      (Printf.sprintf fmt value)
  in
  Alcotest.(check int)
    (where "n")
    (U.to_int (U.member "n" q))
    (Var_backtest.observations r);
  Alcotest.(check int)
    (where "exceptions")
    (U.to_int (U.member "exceptions" q))
    (Var_backtest.exceptions r);
  same "%.1f" "expected" (Var_backtest.expected_exceptions r);
  same "%.4f" "kupiec_p" (Var_backtest.kupiec_p r);
  same "%.4f" "independence_p" (Var_backtest.independence_p r);
  same "%.4f" "joint_p" (Var_backtest.conditional_coverage_p r);
  (match
     (U.member "duration_p" q, Var_backtest.duration_p r, Var_backtest.duration_shape r)
   with
  | `Null, None, None -> ()
  | `Null, _, _ ->
      Alcotest.fail (where "duration: the README prints --, the library computed one")
  | _, Some p, Some shape ->
      same "%.4f" "duration_p" p;
      same "%.2f" "duration_shape" shape
  | _, _, _ ->
      Alcotest.fail (where "duration: the README has a value, the library has none"));
  Alcotest.(check string)
    (where "zone")
    (U.to_string (U.member "zone" q))
    (Var_backtest.Zone.to_string (Var_backtest.zone r));
  Alcotest.(check string)
    (where "verdict")
    (U.to_string (U.member "verdict" q))
    (if Var_backtest.rejected r then "REJECTED" else "ok")

let labels (rows : Validation_report.Row.t list) =
  List.map rows ~f:(fun row ->
      ( row.Validation_report.Row.label,
        Var_backtest.Estimator.to_string
          (Var_backtest.estimator row.Validation_report.Row.report) ))

let test_the_nine_synthetic_rows_are_the_readmes () =
  let t = Validation_report.synthetic () in
  Alcotest.(check (list (pair string string)))
    "nine rows, series-major, in the README's order"
    [
      ("iid-normal", "historical");
      ("iid-normal", "parametric");
      ("iid-normal", "ewma(0.94)");
      ("vol-regime", "historical");
      ("vol-regime", "parametric");
      ("vol-regime", "ewma(0.94)");
      ("jumps", "historical");
      ("jumps", "parametric");
      ("jumps", "ewma(0.94)");
    ]
    (labels t.Validation_report.rows);
  List.iter t.Validation_report.rows
    ~f:(check_row_against_quoted ~table:"battery" ~key:"series");
  Alcotest.(check (float 1e-12)) "confidence" 0.95 t.Validation_report.confidence;
  Alcotest.(check int) "window" 60 t.Validation_report.window;
  Alcotest.(check (float 1e-12)) "alpha" 0.05 t.Validation_report.alpha;
  Alcotest.(check (float 1e-12)) "ewma lambda" 0.94 t.Validation_report.ewma_lambda

(* Read off the quoted table: three rows say REJECTED -- iid-normal/ewma at
   joint p 0.0219, vol-regime/parametric at 0.0373, jumps/historical at 0.0000
   -- and the smallest of the three is the jumps/historical row, the estimator
   that saw fifty identical -8% days and forecast none of them. *)
let test_most_severe_is_the_smallest_joint_p_among_the_rejected () =
  let t = Validation_report.synthetic () in
  Alcotest.(check int) "three rejected" 3 t.Validation_report.rejected;
  match t.Validation_report.most_severe with
  | None -> Alcotest.fail "three rejections and no most-severe"
  | Some row ->
      Alcotest.(check string) "the jumps series" "jumps" row.Validation_report.Row.label;
      Alcotest.(check string)
        "the historical estimator" "historical"
        (Var_backtest.Estimator.to_string
           (Var_backtest.estimator row.Validation_report.Row.report))

(* [hits] are indices into forecast space, so they must be strictly increasing,
   inside [0, forecasts), and exactly as many as the report counts. [var_series]
   and [realised_series] are the same observations laid out for a chart, so a
   hit at index i is exactly the i where realised < -var. *)
let test_hit_indices_index_the_forecast_series () =
  let t = Validation_report.synthetic () in
  List.iter t.Validation_report.rows ~f:(fun row ->
      let open Validation_report.Row in
      let forecasts = Array.length row.var_series in
      Alcotest.(check int) (row.label ^ ": 940 forecasts") 940 forecasts;
      Alcotest.(check int)
        (row.label ^ ": realised alongside")
        forecasts
        (Array.length row.realised_series);
      Alcotest.(check int)
        (row.label ^ ": one index per exception")
        (Var_backtest.exceptions row.report)
        (List.length row.hits);
      Alcotest.(check bool)
        (row.label ^ ": strictly increasing")
        true
        (List.is_sorted_strictly row.hits ~compare:Int.compare);
      List.iter row.hits ~f:(fun i ->
          Alcotest.(check bool)
            (Printf.sprintf "%s: index %d is a hit" row.label i)
            true
            (i >= 0 && i < forecasts
            && Float.( < ) row.realised_series.(i) (-.row.var_series.(i)))))

let test_series_facts () =
  let t = Validation_report.synthetic () in
  Alcotest.(check (list (pair string (pair int int))))
    "three series of 1000, 940 forecasts each"
    [ ("iid-normal", (1000, 940)); ("vol-regime", (1000, 940)); ("jumps", (1000, 940)) ]
    (List.map t.Validation_report.series ~f:(fun s ->
         Validation_report.Series.(s.name, (s.length, s.forecasts))));
  Alcotest.(check int)
    "no windows on the synthetic report" 0
    (List.length t.Validation_report.windows)

(* THE DRAW ORDER, derived by hand. bin/main.ml built the three series as one
   list literal, and OCaml evaluates a list literal's elements RIGHT TO LEFT
   (verified on this switch: [f 1; f 2; f 3] prints 321). So the stream was
   consumed jumps first, then vol-regime, then iid-normal, and the README's
   table is the table of THAT order. Two uniforms per gaussian, always. jumps
   draws on 950 of its 1000 days (every twentieth is a literal -0.08), and
   vol-regime on all 1000: the first iid-normal value is therefore gaussian draw
   number 1951 of a fresh state, whatever sigma the 1950 before it used.

   The 1951 check alone pins where iid-normal STARTS but not which of the
   other two series went first between them -- jumps-then-vol-regime and
   vol-regime-then-jumps both hand iid-normal exactly 1950 prior draws, so a
   test that only checks draw 1951 would still pass with those two swapped.
   The two checks below close that gap directly, one per series, each
   against a fresh state rather than against each other:

     jumps.(0) is draw 1 of a totally fresh state. Index 0 is not itself a
     jump day (0 mod 20 = 0, not 19), so no jump-day branching is needed to
     predict it -- it is simply the first gaussian a fresh stream produces,
     at jumps' sigma. If vol-regime were drawn first, jumps.(0) would need
     1000 prior draws instead of zero, and this check would fail.

     vol_regime.(0) is draw 951 -- after jumps' 950 (1000 days, 50 of them
     literal jump days that draw nothing) and none of iid-normal's, because
     vol-regime is the SECOND series drawn. If vol-regime were drawn first,
     this would need 0 prior draws instead of 950, and this check would fail
     too. Its sigma is 0.006 because index 0 is in the calm regime (i < 600).

   Between them, the three checks pin all three relative positions: jumps
   first (0 prior draws), vol-regime second (950 prior draws), iid-normal
   third (1950 prior draws). Swapping any two of the three lets in
   [synthetic_series] moves at least one series off its pinned draw index. *)
let test_the_generator_reads_one_stream_jumps_first () =
  let rng = Random.State.make [| Validation_report.seed |] in
  let generated = Validation_report.synthetic_series ~rng in
  let series name =
    List.Assoc.find_exn
      (List.map generated ~f:(fun (n, _, r) -> (n, r)))
      ~equal:String.equal name
  in
  let jumps = series "jumps" in
  let vol_regime = series "vol-regime" in
  let iid_normal = series "iid-normal" in
  let advance replay n =
    for _ = 1 to n do
      ignore (Synthetic_book.gaussian ~rng:replay ~sigma:1.0 : float)
    done
  in
  let jumps_replay = Random.State.make [| Validation_report.seed |] in
  Alcotest.(check (float 0.0))
    "jumps.(0) is draw 1 (jumps drawn first)"
    (Synthetic_book.gaussian ~rng:jumps_replay ~sigma:0.004)
    jumps.(0);
  let vol_regime_replay = Random.State.make [| Validation_report.seed |] in
  advance vol_regime_replay 950;
  Alcotest.(check (float 0.0))
    "vol-regime.(0) is draw 951 (after jumps' 950, before iid-normal's)"
    (Synthetic_book.gaussian ~rng:vol_regime_replay ~sigma:0.006)
    vol_regime.(0);
  let replay = Random.State.make [| Validation_report.seed |] in
  advance replay (950 + 1000);
  Alcotest.(check (float 0.0))
    "iid-normal.(0) is draw 1951"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011)
    iid_normal.(0);
  Alcotest.(check (float 0.0))
    "iid-normal.(1) is draw 1952"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011)
    iid_normal.(1)

module Crisis_data = Ohcamel.Crisis_data

let crisis = lazy (Validation_report.crisis (Crisis_data.load_all_embedded ()))

let test_the_nine_crisis_rows_are_the_readmes () =
  let t = Lazy.force crisis in
  Alcotest.(check (list (pair string string)))
    "nine rows, window-major, in the README's order"
    [
      ("gfc", "historical");
      ("gfc", "parametric");
      ("gfc", "ewma(0.94)");
      ("covid", "historical");
      ("covid", "parametric");
      ("covid", "ewma(0.94)");
      ("rates-2022", "historical");
      ("rates-2022", "parametric");
      ("rates-2022", "ewma(0.94)");
    ]
    (labels t.Validation_report.rows);
  List.iter t.Validation_report.rows ~f:(fun row ->
      check_row_against_quoted ~table:"crisis" ~key:"window" row;
      let r = row.Validation_report.Row.report in
      let estimator = Var_backtest.Estimator.to_string (Var_backtest.estimator r) in
      let q =
        quoted_row "crisis" ~key:"window" ~value:row.Validation_report.Row.label
          ~estimator
      in
      Alcotest.(check int)
        (Printf.sprintf "%s/%s: burst" row.Validation_report.Row.label estimator)
        (U.to_int (U.member "burst" q))
        (Option.value_map row.Validation_report.Row.burst ~default:(-1) ~f:fst));
  Alcotest.(check int)
    "no synthetic series on the crisis report" 0
    (List.length t.Validation_report.series)

(* Read off the quoted table: every crisis verdict is "ok". *)
let test_crisis_rejects_nothing_so_there_is_no_most_severe () =
  let t = Lazy.force crisis in
  Alcotest.(check int) "none rejected" 0 t.Validation_report.rejected;
  Alcotest.(check bool)
    "so no most-severe row" true
    (Option.is_none t.Validation_report.most_severe)

(* Hand-traced. Span 3 over F T T F F T T T F:

     i   hit   +hit   -hits.(i-3)   running   worst (end)
     0   F                            0        0
     1   T     1                      1        1 (1)
     2   T     1                      2        2 (2)
     3   F           hits.(0)=F       2
     4   F           hits.(1)=T  -1   1
     5   T     1     hits.(2)=T  -1   1
     6   T     1     hits.(3)=F       2
     7   T     1     hits.(4)=F       3        3 (7)
     8   F           hits.(5)=T  -1   2

   worst 3, first attained at i = 7, so the run starts at 7 - 3 + 1 = 5.
   The count is exactly what bin/main.ml's worst_burst returned (the README's
   burst column); the start is new. *)
let test_worst_burst_on_hand_built_indicators () =
  let t = true and f = false in
  Alcotest.(check (pair int int))
    "F T T F F T T T F, span 3" (3, 5)
    (Validation_report.worst_burst ~span:3 [| f; t; t; f; f; t; t; t; f |]);
  (* Two hits further apart than the span never share a window: the count is 1
     and the run is the first hit's own window, floored at index 0. *)
  Alcotest.(check (pair int int))
    "T F F F T, span 3" (1, 0)
    (Validation_report.worst_burst ~span:3 [| t; f; f; f; t |]);
  (* A span wider than the series holds everything. *)
  Alcotest.(check (pair int int))
    "five hits, span 21" (5, 0)
    (Validation_report.worst_burst ~span:21 [| t; t; t; t; t |]);
  Alcotest.(check (pair int int))
    "no hits" (0, 0)
    (Validation_report.worst_burst ~span:21 [| f; f; f |]);
  Alcotest.(check (pair int int))
    "empty" (0, 0)
    (Validation_report.worst_burst ~span:21 [||])

(* The window facts come from the CSV headers: "631 sessions common to all six
   names" (gfc), 400 (covid), 401 (rates-2022). Returns are one per session
   gap, so 630 / 399 / 400 of them, and a 60-day rolling window leaves
   570 / 339 / 340 forecasts -- the n column of the README's crisis table. *)
let test_window_facts () =
  let t = Lazy.force crisis in
  Alcotest.(check (list (pair string (pair int int))))
    "sessions and forecasts per window"
    [ ("gfc", (631, 570)); ("covid", (400, 339)); ("rates-2022", (401, 340)) ]
    (List.map t.Validation_report.windows ~f:(fun w ->
         Validation_report.Window_facts.(w.name, (w.sessions, w.forecasts))));
  List.iter t.Validation_report.windows ~f:(fun w ->
      let open Validation_report.Window_facts in
      Alcotest.(check (list string))
        (w.name ^ ": the six names")
        [ "AAPL"; "CVX"; "JPM"; "MSFT"; "NVDA"; "XOM" ]
        w.symbols;
      Alcotest.(check bool)
        (w.name ^ ": worst day is a loss")
        true (Float.( < ) w.worst_day 0.0);
      Alcotest.(check bool)
        (w.name ^ ": best day is a gain")
        true (Float.( > ) w.best_day 0.0);
      Alcotest.(check int)
        (w.name ^ ": one date per forecast")
        w.forecasts (Array.length w.dates);
      Alcotest.(check int)
        (w.name ^ ": one realised return per forecast")
        w.forecasts (Array.length w.realised))

(* DATE ALIGNMENT, derived by hand. returns.(k) is close.(k+1) / close.(k) - 1:
   the return REALISED on dates.(k+1). Var_backtest.rolling's observation j
   forecasts from returns.(j .. j+59) and scores returns.(60 + j), which is the
   return realised on dates.(61 + j). So forecast j belongs to session 61 + j,
   the first forecast to dates.(61), and the last to dates.(sessions - 1). *)
let test_forecast_j_is_session_61_plus_j () =
  let t = Lazy.force crisis in
  let gfc =
    List.find_exn (Crisis_data.load_all_embedded ()) ~f:(fun w ->
        String.equal (Crisis_data.Window.name w) "gfc")
  in
  let facts =
    List.find_exn t.Validation_report.windows ~f:(fun w ->
        String.equal w.Validation_report.Window_facts.name "gfc")
  in
  let sessions = Crisis_data.Window.dates gfc in
  let open Validation_report.Window_facts in
  Alcotest.(check string) "first session" sessions.(0) facts.first;
  Alcotest.(check string) "last session" sessions.(630) facts.last;
  Alcotest.(check string) "forecast 0 is session 61" sessions.(61) facts.dates.(0);
  Alcotest.(check string) "forecast 569 is session 630" sessions.(630) facts.dates.(569);
  (* And every gfc row's realised series IS the window's, so a chart can draw
     the three estimators' VaR over one realised line. *)
  List.iter t.Validation_report.rows ~f:(fun row ->
      if String.equal row.Validation_report.Row.label "gfc" then
        Alcotest.(check (array (float 0.0)))
          "row realised = window realised" facts.realised
          row.Validation_report.Row.realised_series)

let suite =
  ( "validation_report",
    [
      Alcotest.test_case "THE NINE SYNTHETIC ROWS ARE THE README'S" `Quick
        test_the_nine_synthetic_rows_are_the_readmes;
      Alcotest.test_case "most severe is the smallest joint p among the rejected" `Quick
        test_most_severe_is_the_smallest_joint_p_among_the_rejected;
      Alcotest.test_case "hit indices index the forecast series" `Quick
        test_hit_indices_index_the_forecast_series;
      Alcotest.test_case "series facts" `Quick test_series_facts;
      Alcotest.test_case "the generator reads one stream, jumps first" `Quick
        test_the_generator_reads_one_stream_jumps_first;
      Alcotest.test_case "THE NINE CRISIS ROWS ARE THE README'S" `Quick
        test_the_nine_crisis_rows_are_the_readmes;
      Alcotest.test_case "crisis rejects nothing, so there is no most-severe" `Quick
        test_crisis_rejects_nothing_so_there_is_no_most_severe;
      Alcotest.test_case "worst_burst on hand-built indicators" `Quick
        test_worst_burst_on_hand_built_indicators;
      Alcotest.test_case "window facts" `Quick test_window_facts;
      Alcotest.test_case "forecast j is session 61 + j" `Quick
        test_forecast_j_is_session_61_plus_j;
    ] )
