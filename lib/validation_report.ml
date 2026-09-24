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
open Types

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

(* One month of sessions. Short enough that a burst inside it is a burst rather
   than a season, long enough that a single bad week does not fill it. *)
let burst_span = 21

(* The most exceptions any [span] consecutive observations contained, and where
   that run starts.

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

   The duration-based test (Christoffersen-Pelletier) IS implemented --
   Var_backtest.duration_independence, the duration p column -- and it sees a
   cluster at any spacing, but it is a GLOBAL fit: one local burst inside a
   long calm series barely moves it. So the burst count sits in the table next
   to both p-values as the only one of the three that sees a LOCAL cluster.
   Not a hypothesis test, and labelled as such wherever it is shown.

   The start index is for the page, which shades the run; the CLI prints the
   count alone, exactly as it did. The count is attained at the first index
   where the running total reaches its maximum, and the run is the [span]
   observations ending there, floored at 0. *)
let worst_burst ~(span : int) (hits : bool array) : int * int =
  let n = Array.length hits in
  if n = 0 then (0, 0)
  else begin
    let worst = ref 0 in
    let worst_end = ref 0 in
    let running = ref 0 in
    Array.iteri hits ~f:(fun i hit ->
        if hit then incr running;
        if i >= span && hits.(i - span) then decr running;
        if !running > !worst then begin
          worst := !running;
          worst_end := i
        end);
    (!worst, Int.max 0 (!worst_end - span + 1))
  end

(* [rolling] then [run], rather than [of_returns], so the observations are in
   hand for the hit indices, the two series and the burst. var_backtest.ml
   exports both steps and composes them the same way, so this is the same path
   and not a second one.

   [@warning "-16"]: the documented signature is `?burst_span:int -> label:...
   -> returns:... -> estimator:... -> Row.t`, with no trailing positional
   argument, so the compiler cannot prove [burst_span] is erasable and (16)
   unerasable-optional-argument is fatal under this project's dev profile.
   Every call site either supplies [~burst_span] explicitly (crisis) or
   forwards the option explicitly with [?burst_span:None] (synthetic) rather
   than omitting the label, so the argument is never actually left ambiguous
   at a call site -- only the general function type is. *)
let[@warning "-16"] row ?burst_span ~(label : string) ~(returns : float array)
    ~(estimator : Var_backtest.Estimator.t) : Row.t =
  let observations =
    Var_backtest.rolling ~returns ~window:Synthetic_book.return_window
      ~confidence:Synthetic_book.confidence ~estimator
  in
  let report =
    Var_backtest.run ~observations ~estimator ~confidence:Synthetic_book.confidence
  in
  let exceedances = Var_backtest.exceedances observations in
  let hits =
    Array.foldi exceedances ~init:[] ~f:(fun i acc hit -> if hit then i :: acc else acc)
    |> List.rev
  in
  {
    Row.label;
    report;
    hits;
    burst = Option.map burst_span ~f:(fun span -> worst_burst ~span exceedances);
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
        List.map estimators ~f:(fun estimator ->
            row ~label ~returns ~estimator ?burst_span:None))
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

(* The same battery, real data.

   Everything in [synthetic] is validated against series whose regime the
   author chose. That is the right way to BUILD a coverage battery -- it is the
   only setting where you know in advance which tests ought to reject -- and it
   is not evidence that the model survives a real tail. This changes exactly one
   thing: the data. Same window, same confidence, same three estimators, same
   Var_backtest.rolling. The method has to be visibly identical or the
   comparison says nothing.

   The book's return series comes out of the ENGINE, through
   Crisis_data.portfolio_returns_of_book, at today's book held at constant
   weights -- graph.ml's own approximation, and the question a limit asks.

   A window too short to form a series is a raise, not a skipped row. The
   windows are compiled into the binary and are hundreds of sessions long; the
   only way to get here is a broken build, and a report that quietly scored two
   windows under a heading promising three is the outcome this module exists
   to make impossible.

   DATE ALIGNMENT. returns.(k) is the return realised on dates.(k + 1), and
   Var_backtest.rolling's observation j scores returns.(window + j), so
   forecast j belongs to session window + 1 + j -- dates.(61 + j) at this
   engine's window. [Window_facts.dates] and [Window_facts.realised] are laid
   out in that forecast space, one entry per observation, so a chart draws
   every row's [var_series] over them without re-deriving the offset. *)
let crisis (windows : Crisis_data.Window.t list) : t =
  let window = Synthetic_book.return_window in
  let scored =
    List.map windows ~f:(fun w ->
        match
          Crisis_data.portfolio_returns_of_book ~instruments:Synthetic_book.instruments
            ~positions:
              (List.map Synthetic_book.book ~f:(fun (s, _, _, q) -> (s, Qty.of_float q)))
            ~marks:
              (List.map Synthetic_book.book ~f:(fun (s, _, p, _) -> (s, Price.of_float p)))
            w
        with
        | Some returns when Array.length returns > window -> (w, returns)
        | Some returns ->
            invalid_argf
              "validation_report: window %s has %d returns, fewer than the %d a rolling \
               forecast needs"
              (Crisis_data.Window.name w) (Array.length returns) window ()
        | None ->
            invalid_argf
              "validation_report: window %s is too short to form a return series"
              (Crisis_data.Window.name w) ())
  in
  let rows =
    List.concat_map scored ~f:(fun (w, returns) ->
        List.map estimators ~f:(fun estimator ->
            row ~burst_span ~label:(Crisis_data.Window.name w) ~returns ~estimator))
  in
  let facts =
    List.map scored ~f:(fun (w, returns) ->
        let dates = Crisis_data.Window.dates w in
        let sessions = Crisis_data.Window.sessions w in
        let forecasts = Array.length returns - window in
        {
          Window_facts.name = Crisis_data.Window.name w;
          description = Crisis_data.Window.description w;
          first = dates.(0);
          last = dates.(sessions - 1);
          sessions;
          forecasts;
          symbols = List.map (Crisis_data.Window.symbols w) ~f:Symbol.to_string;
          (* Folded from 0.0, as the CLI folded: the worst day is at most flat
             and the best at least flat, which is a fact about the fold and is
             kept because the printed line is a published figure. *)
          worst_day = Array.fold returns ~init:0.0 ~f:Float.min;
          best_day = Array.fold returns ~init:0.0 ~f:Float.max;
          dates = Array.init forecasts ~f:(fun j -> dates.(window + 1 + j));
          realised = Array.sub returns ~pos:window ~len:forecasts;
        })
  in
  {
    rows;
    rejected = List.count rows ~f:(fun r -> Var_backtest.rejected ~alpha r.Row.report);
    most_severe = most_severe rows;
    series = [];
    windows = facts;
    confidence = Synthetic_book.confidence;
    window;
    alpha;
    ewma_lambda = Vol_estimators.Ewma.default_lambda;
  }
