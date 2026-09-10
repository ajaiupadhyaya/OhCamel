(* The VaR validation battery as a value.

   WHY A RECORD. `make backtest` and `make backtest-crisis` were two printers
   with the battery inlined into each; the served page needs the same nine
   rows twice over, plus the hit indices and the forecast series a chart is
   made of, none of which a printf can hand back. So the battery is computed
   here, once, into a record, and both the CLI and the page are readers of it.
   Nothing in this file is a statistic: every p-value, zone and verdict is
   Var_backtest's, reached through the same [rolling] then [run] the CLI has
   always used. This module decides WHICH series and WHICH estimators, and
   lays the answers out.

   WHAT IS NOT HERE. Formatting -- money, percentages, the rules and the prose
   stay in bin/main.ml. And no random state: [synthetic] makes its own from
   [seed] every call, because the README's table is the table of a fresh
   Random.State.make [| 2026_08_24 |] and nothing else, and the CLI's shared
   demo stream must never be touched by it. *)

open Core

module Series = struct
  type t = { name : string; description : string; length : int; forecasts : int }
end

(* One real window, as the page and the CLI describe it. [dates] and
   [realised] are in FORECAST space -- one entry per observation, aligned with
   every row's [var_series] -- not in session space; the alignment is derived
   at [crisis] in Task 6. *)
module Window_facts = struct
  type t = {
    name : string;
    description : string;
    first : string;
    last : string;
    sessions : int;
    forecasts : int;
    symbols : string list;
    worst_day : float;
    best_day : float;
    dates : string array;
    realised : float array;
  }
end

module Row = struct
  type t = {
    (* The series name or the window name -- the CLI's first column. *)
    label : string;
    (* Var_backtest's own report, whole. The estimator travels inside it
       (Var_backtest.estimator), so a row cannot be separated from which
       lambda produced it. *)
    report : Var_backtest.report;
    (* Indices into forecast space where realised < -var, strictly increasing. *)
    hits : int list;
    (* (count, start index) of the worst [burst_span] run; None on synthetic
       rows, which never printed one. Filled by Task 6. *)
    burst : (int * int) option;
    var_series : float array;
    realised_series : float array;
  }
end

type t = {
  rows : Row.t list;
  rejected : int;
  most_severe : Row.t option;
  series : Series.t list;
  windows : Window_facts.t list;
  confidence : float;
  window : int;
  alpha : float;
  ewma_lambda : float;
}

let seed = 2026_08_24
let length = 1000
let alpha = 0.05

(* The three estimators, in the CLI's column order. The third is the argument
   of Phase A, put in front of the same battery as the other two rather than
   described: if the equal-weighted parametric estimator is rejected on the
   vol-regime series and the EWMA one is not, that is the claim demonstrated.
   If EWMA is rejected too, the table says so, which is what a validation
   suite is for. *)
let estimators =
  [
    Var_backtest.Estimator.Historical;
    Var_backtest.Estimator.Parametric;
    Var_backtest.Estimator.Parametric_ewma Vol_estimators.Ewma.default_lambda;
  ]

(* The three synthetic series, drawn from [rng] in the order that reproduces the
   README.

   DRAW ORDER IS LOAD-BEARING, AND IT IS NOT THE LIST'S ORDER. bin/main.ml
   built these as one list literal, and OCaml evaluates a list literal's
   elements right to left -- so for as long as that table has existed, the
   stream was consumed jumps first, then vol-regime, then iid-normal. Written
   here as three lets in exactly that order, so the fact is on the page rather
   than in the compiler, and the list is assembled afterwards. The jumps series
   does not draw on a jump day: every twentieth value is the literal -0.08, so
   it consumes 950 draws, not 1000, and iid-normal's first value is draw 1951
   of a fresh state. test_validation_report.ml pins that number. *)
let synthetic_series ~(rng : Random.State.t) : (string * string * float array) list =
  let gaussian ~sigma = Synthetic_book.gaussian ~rng ~sigma in
  let jumps =
    Array.init length ~f:(fun i -> if i % 20 = 19 then -0.08 else gaussian ~sigma:0.004)
  in
  let vol_regime =
    Array.init length ~f:(fun i -> gaussian ~sigma:(if i < 600 then 0.006 else 0.024))
  in
  let iid_normal = Array.init length ~f:(fun _ -> gaussian ~sigma:0.011) in
  [
    ( "iid-normal",
      "Independent normal returns -- exactly what the parametric estimator assumes.",
      iid_normal );
    ( "vol-regime",
      "Calm for 600 days, then four times as volatile. The window takes 60 days to \
       notice.",
      vol_regime );
    ( "jumps",
      "Quiet days with an identical -8% loss every twentieth. Exactly 5% of days are the \
       tail.",
      jumps );
  ]

(* [rolling] then [run], rather than [of_returns], so the observations are in
   hand for the hit indices and the two series. var_backtest.ml exports both
   steps and composes them the same way, so this is the same path and not a
   second one. *)
let row ~(label : string) ~(returns : float array) ~(estimator : Var_backtest.Estimator.t)
    : Row.t =
  let observations =
    Var_backtest.rolling ~returns ~window:Synthetic_book.return_window
      ~confidence:Synthetic_book.confidence ~estimator
  in
  let report =
    Var_backtest.run ~observations ~estimator ~confidence:Synthetic_book.confidence
  in
  let hits =
    Array.foldi (Var_backtest.exceedances observations) ~init:[] ~f:(fun i acc hit ->
        if hit then i :: acc else acc)
    |> List.rev
  in
  {
    Row.label;
    report;
    hits;
    burst = None;
    var_series = Array.of_list_map observations ~f:Var_backtest.Observation.var;
    realised_series = Array.of_list_map observations ~f:Var_backtest.Observation.realised;
  }

(* The WORST failure rather than the first: the rejected row with the smallest
   conditional-coverage p. A table of p-values is a summary; the failure is the
   finding, and the most severe one is the finding worth printing in full.
   List.min_elt keeps the first of equal minima, as the CLI's did. *)
let most_severe (rows : Row.t list) : Row.t option =
  List.filter rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report)
  |> List.min_elt ~compare:(fun a b ->
      Float.compare
        (Var_backtest.conditional_coverage_p a.Row.report)
        (Var_backtest.conditional_coverage_p b.Row.report))

let synthetic () : t =
  let rng = Random.State.make [| seed |] in
  let generated = synthetic_series ~rng in
  let rows =
    List.concat_map generated ~f:(fun (label, _, returns) ->
        List.map estimators ~f:(fun estimator -> row ~label ~returns ~estimator))
  in
  {
    rows;
    rejected = List.count rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report);
    most_severe = most_severe rows;
    series =
      List.map generated ~f:(fun (name, description, returns) ->
          {
            Series.name;
            description;
            length = Array.length returns;
            forecasts = Array.length returns - Synthetic_book.return_window;
          });
    windows = [];
    confidence = Synthetic_book.confidence;
    window = Synthetic_book.return_window;
    alpha;
    ewma_lambda = Vol_estimators.Ewma.default_lambda;
  }
