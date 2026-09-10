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
   number 1951 of a fresh state, whatever sigma the 1950 before it used. *)
let test_the_generator_reads_one_stream_jumps_first () =
  let rng = Random.State.make [| Validation_report.seed |] in
  let generated = Validation_report.synthetic_series ~rng in
  let iid_normal =
    List.Assoc.find_exn
      (List.map generated ~f:(fun (n, _, r) -> (n, r)))
      ~equal:String.equal "iid-normal"
  in
  let replay = Random.State.make [| Validation_report.seed |] in
  for _ = 1 to 950 + 1000 do
    ignore (Synthetic_book.gaussian ~rng:replay ~sigma:1.0 : float)
  done;
  Alcotest.(check (float 0.0))
    "iid-normal.(0) is draw 1951"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011)
    iid_normal.(0);
  Alcotest.(check (float 0.0))
    "iid-normal.(1) is draw 1952"
    (Synthetic_book.gaussian ~rng:replay ~sigma:0.011)
    iid_normal.(1)

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
    ] )
