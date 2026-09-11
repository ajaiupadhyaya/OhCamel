(* The reports the CLI prints, computed by the serving process and put on the
   wire, so the deployed page can show what the README only quotes.

   Every table here exists as the terminal output of an `ohcamel <mode>` and as
   README prose, and the deployed host -- which can reproduce all of them --
   reproduced none. This module is the fix, and the shape of the fix is the
   argument: the reports are COMPUTED, once, at startup, into process memory,
   and served as a string. Not recomputed per request, because a scaling probe
   in a request handler is a denial of service with a URL; not persisted,
   because nothing in this project persists.

   The records come from the same library modules the CLI prints from
   (Scaling_probe, Validation_report, Options_walk, Garch_study), so the web and
   the terminal cannot drift. The only arithmetic here is a sum of absolute
   notionals for a caption and a clock.

   WHY THE ORDER MATTERS. The scaling probe creates graphs of up to 400 names.
   [compute] runs it first, and the caller runs [compute] before the served
   graph exists, so those node creations land before the first tick and the
   smoke suite's "nodes_recomputed advances" read is the engine's pulse, not
   the probe's tail. Every graph a report creates is destroyed by the module
   that created it.

   GARCH is the exception. The study is 180 fits and takes seconds, so it runs
   AFTER the socket binds, on a second domain, and the route reports its
   progress until it is done. [Garch] below. *)

open Core

(* The CLI's scaling seed (bin/main.ml's [rng]). *)
let scaling_seed = 2026_07_30

type t = {
  computed_at : Time_ns.t;
  computed_in_ms : float;
  (* What the probe was actually asked for, so the wire cannot claim fifty ticks
     when a test ran five. *)
  ticks : int;
  scaling : Scaling_probe.row list;
  (* The probe's cost against Incremental's process-wide counter, which the
     footer prints: the reason it starts in the thousands on a 53-node graph. *)
  scaling_cost : int;
  synthetic : Validation_report.t;
  crisis : Validation_report.t;
  options : Options_walk.t;
}

let compute ?(sizes = Scaling_probe.default_sizes) ?(ticks = Scaling_probe.default_ticks)
    () : t =
  let started = Time_ns.now () in
  let before = Graph.total_nodes_recomputed () in
  let scaling = Scaling_probe.rows ~seed:scaling_seed ~sizes ~ticks in
  let scaling_cost = Graph.total_nodes_recomputed () - before in
  let synthetic = Validation_report.synthetic () in
  let crisis = Validation_report.crisis (Crisis_data.load_all_embedded ()) in
  let options = Options_walk.run () in
  let finished = Time_ns.now () in
  {
    computed_at = finished;
    computed_in_ms = Time_ns.Span.to_ms (Time_ns.diff finished started);
    ticks;
    scaling;
    scaling_cost;
    synthetic;
    crisis;
    options;
  }

(* ------------------------------------------------------------------------ *)
(* The wire                                                                  *)
(* ------------------------------------------------------------------------ *)

(* NaN and infinity are not JSON; null is what the page renders as unknown. *)
let jfloat x : Yojson.Safe.t = if Float.is_finite x then `Float x else `Null
let jopt = function None -> `Null | Some x -> jfloat x

(* Six significant digits for the long series the page draws and nobody reads
   as a number: a crisis window's VaR path at seventeen digits is twice the
   bytes and says nothing more. Tables keep full precision, because the page
   compares them against the README's to four decimals. *)
let jseries (xs : float array) : Yojson.Safe.t =
  `List
    (Array.to_list xs
    |> List.map ~f:(fun x ->
        if Float.is_finite x then `Float (Float.of_string (sprintf "%.6g" x)) else `Null)
    )

let jstr s : Yojson.Safe.t = `String s
let jint i : Yojson.Safe.t = `Int i

let json_of_estimator (e : Var_backtest.Estimator.t) : Yojson.Safe.t =
  let kind, lambda =
    match e with
    | Historical -> ("historical", None)
    | Parametric -> ("parametric", None)
    | Parametric_ewma l -> ("ewma", Some l)
  in
  `Assoc
    [
      ("kind", jstr kind);
      ("lambda", jopt lambda);
      ("label", jstr (Var_backtest.Estimator.to_string e));
    ]

let json_of_row ~(with_var : bool) (row : Validation_report.Row.t) : Yojson.Safe.t =
  let r = row.report in
  `Assoc
    ([
       ("label", jstr row.label);
       ("estimator", json_of_estimator r.estimator);
       ("observations", jint r.observations);
       ("exceptions", jint r.exceptions);
       ("expected_exceptions", jfloat r.expected_exceptions);
       ("kupiec_p", jfloat r.kupiec_p);
       ("independence_p", jfloat r.independence_p);
       ("conditional_coverage_p", jfloat r.conditional_coverage_p);
       ("duration_shape", jopt r.duration_shape);
       ("duration_p", jopt r.duration_p);
       ("zone", jstr (Var_backtest.Zone.to_string r.zone));
       ("worst_loss", jfloat r.worst_loss);
       ("var_at_worst_loss", jfloat r.var_at_worst_loss);
       ("rejected", `Bool (Var_backtest.rejected r));
       ("verdict", jstr (Var_backtest.verdict r));
       ("hits", `List (List.map row.hits ~f:jint));
       ( "burst",
         match row.burst with
         | None -> `Null
         | Some (count, start) -> `Assoc [ ("count", jint count); ("start", jint start) ]
       );
     ]
    @ if with_var then [ ("var", jseries row.var_series) ] else [])

let json_of_most_severe = function
  | None -> `Null
  | Some (row : Validation_report.Row.t) ->
      `Assoc
        [
          ("label", jstr row.label);
          ("estimator", jstr (Var_backtest.Estimator.to_string row.report.estimator));
          (* The engine's own text, verbatim, so the page can print it as such. *)
          ("text", jstr (Var_backtest.to_string row.report));
        ]

let json_of_battery ~(with_var : bool) (v : Validation_report.t) : Yojson.Safe.t =
  `Assoc
    [
      ( "series",
        `List
          (List.map v.series ~f:(fun (s : Validation_report.Series.t) ->
               `Assoc
                 [
                   ("name", jstr s.name);
                   ("description", jstr s.description);
                   ("length", jint s.length);
                   ("forecasts", jint s.forecasts);
                 ])) );
      ( "windows",
        `List
          (List.map v.windows ~f:(fun (w : Validation_report.Window_facts.t) ->
               `Assoc
                 [
                   ("name", jstr w.name);
                   ("description", jstr w.description);
                   ("first", jstr w.first);
                   ("last", jstr w.last);
                   ("sessions", jint w.sessions);
                   ("forecasts", jint w.forecasts);
                   ("symbols", `List (List.map w.symbols ~f:jstr));
                   ("worst_day", jfloat w.worst_day);
                   ("best_day", jfloat w.best_day);
                   ("dates", `List (Array.to_list (Array.map w.dates ~f:jstr)));
                   ("realised", jseries w.realised);
                 ])) );
      ("rows", `List (List.map v.rows ~f:(json_of_row ~with_var)));
      ("rejected", jint v.rejected);
      ("most_severe", json_of_most_severe v.most_severe);
    ]

(* The book the crisis battery holds at constant weights: today's synthetic
   marks, not crisis-era prices. *)
let json_of_crisis_book () : Yojson.Safe.t =
  let positions = Synthetic_book.book in
  let gross =
    List.sum
      (module Float)
      positions
      ~f:(fun (_, _, mark, qty) -> Float.abs (mark *. qty))
  in
  `Assoc
    [
      ( "positions",
        `List
          (List.map positions ~f:(fun (symbol, sector, mark, qty) ->
               `Assoc
                 [
                   ("symbol", jstr (Types.Symbol.to_string symbol));
                   ("sector", jstr (Types.Sector.to_string sector));
                   ("mark", jfloat mark);
                   ("qty", jfloat qty);
                 ])) );
      ("gross_notional", jfloat gross);
    ]

let json_of_breach (b : Types.Breach.t) : Yojson.Safe.t =
  `Assoc
    [
      ("name", jstr (Types.Limit.name (Types.Breach.limit b)));
      ("observed", jfloat (Types.Breach.observed b));
      ("threshold", jfloat (Types.Breach.threshold b));
      ("utilisation", jfloat (Limits.utilisation b));
      ("breached", `Bool (Types.Breach.breached b));
      ("text", jstr (Limits.to_string b));
    ]

let json_of_options (o : Options_walk.t) : Yojson.Safe.t =
  let state (s : Options_walk.State.t) =
    `Assoc
      [
        ("label", jstr s.label);
        ("delta_equivalent", jfloat s.delta_equivalent);
        ("gamma", jfloat s.gamma);
        (* Per 1.00 of vol, as the engine holds it; the page divides by 100. *)
        ("vega", jfloat s.vega);
      ]
  in
  let leg (l : Options_walk.Leg.t) =
    `Assoc
      [ ("id", jstr l.id); ("days", jfloat l.days); ("contracts", jfloat l.contracts) ]
  in
  `Assoc
    [
      ("label", jstr "SYNTHETIC");
      ( "surface",
        `Assoc
          [
            ("formula", jstr o.surface.formula);
            ("floor", jfloat o.surface.floor);
            ("at_setup", jfloat o.surface.at_setup);
          ] );
      ( "setup",
        `Assoc
          [
            ("underlying", jstr o.setup.underlying);
            ("id", jstr o.setup.id);
            ("strike", jfloat o.setup.strike);
            ("right", jstr o.setup.right);
            ("expiry_days", jfloat o.setup.expiry_days);
            ("contracts", jfloat o.setup.contracts);
            ("multiplier", jfloat o.setup.multiplier);
            ("spot", jfloat o.setup.spot);
            ("rate", jfloat o.setup.rate);
            ("implied_vol", jfloat o.setup.implied_vol);
          ] );
      ("states", `List (List.map o.states ~f:state));
      ("hedge_shares", jfloat o.hedge_shares);
      ("breaches", `List (List.map o.breaches ~f:json_of_breach));
      ("clock_advance_days", jfloat o.clock_advance_days);
      ( "calendar",
        `Assoc
          [
            ("far", leg o.calendar.far);
            ("near", leg o.calendar.near);
            ("portfolio_vega", jfloat o.calendar.portfolio_vega);
            ("portfolio_gamma", jfloat o.calendar.portfolio_gamma);
            ( "buckets",
              `List
                (List.map o.calendar.buckets ~f:(fun (bucket, vega) ->
                     `Assoc [ ("bucket", jstr bucket); ("vega", jfloat vega) ])) );
          ] );
    ]

let platform () : Yojson.Safe.t =
  `Assoc
    [
      ("ocaml_version", jstr Stdlib.Sys.ocaml_version);
      ("architecture", jstr Build_info.architecture);
      ("system", jstr Build_info.system);
    ]

let to_json (t : t) : Yojson.Safe.t =
  `Assoc
    [
      ("computed_at", jstr (Time_ns.to_string_utc t.computed_at));
      ("computed_in_ms", jfloat t.computed_in_ms);
      ("platform", platform ());
      ( "scaling",
        `Assoc
          [
            ("seed", jint scaling_seed);
            ("ticks", jint t.ticks);
            ("cost_nodes_recomputed", jint t.scaling_cost);
            ( "rows",
              `List
                (List.map t.scaling ~f:(fun (r : Scaling_probe.row) ->
                     `Assoc
                       [
                         ("instruments", jint r.instrument_count);
                         ("nodes_in_graph", jint r.named_nodes);
                         ("nodes_per_tick", jfloat r.nodes_per_tick);
                         (* Polling redoes the whole graph, so it is the graph's size. *)
                         ("if_polled", jint r.named_nodes);
                         ("ns_per_tick", jfloat r.ns_per_tick);
                       ])) );
          ] );
      ( "validation",
        `Assoc
          [
            ( "config",
              `Assoc
                [
                  ("confidence", jfloat t.synthetic.confidence);
                  ("window", jint t.synthetic.window);
                  ("alpha", jfloat t.synthetic.alpha);
                  ("ewma_lambda", jfloat t.synthetic.ewma_lambda);
                  ("seed", jint Validation_report.seed);
                ] );
            ("synthetic", json_of_battery ~with_var:false t.synthetic);
            ( "crisis",
              match json_of_battery ~with_var:true t.crisis with
              | `Assoc fields -> `Assoc (fields @ [ ("book", json_of_crisis_book ()) ])
              | other -> other );
          ] );
      ("options", json_of_options t.options);
    ]

let to_string (t : t) : string = Yojson.Safe.to_string (to_json t)

(* ------------------------------------------------------------------------ *)
(* GARCH, on a second domain                                                 *)
(* ------------------------------------------------------------------------ *)

module Garch = struct
  (* The study that keeps GARCH out of the graph, run by the serving process.

     Garch11.fit is pure OCaml over float arrays, so the study runs on its own
     domain in parallel with the engine and never holds the Async scheduler.
     The domain touches nothing of Async's: it takes its own Random.State (inside
     Garch_study.run), publishes its fit count through an Atomic, and sets a
     second Atomic when it has finished, whether it succeeded or raised. The main
     domain notices on a one-second poll and joins it. Until then the route
     reports how far it has got. *)

  type status =
    | Scheduled
    | Computing
    | Done of Garch_study.result * float
    | Failed of string

  type t = {
    seed : int;
    replications : int;
    sample_sizes : int list;
    burn_in : int;
    truth : Vol_estimators.Garch11.t;
    fitted : int Stdlib.Atomic.t;
    finished : bool Stdlib.Atomic.t;
    mutable status : status;
  }

  let create ?(seed = Garch_study.default_seed)
      ?(replications = Garch_study.default_replications)
      ?(sample_sizes = Garch_study.default_sample_sizes)
      ?(burn_in = Garch_study.default_burn_in) () : t =
    {
      seed;
      replications;
      sample_sizes;
      burn_in;
      truth = Garch_study.default_truth;
      fitted = Stdlib.Atomic.make 0;
      finished = Stdlib.Atomic.make false;
      status = Scheduled;
    }

  let fits_total (t : t) = t.replications * List.length t.sample_sizes

  let run_study (t : t) =
    Garch_study.run
      ~progress:(fun k -> Stdlib.Atomic.set t.fitted k)
      ~seed:t.seed ~replications:t.replications ~sample_sizes:t.sample_sizes
      ~truth:t.truth ~burn_in:t.burn_in ()

  (* Spawns the domain and returns; the Deferred it leaves behind resolves when
     the result has been collected. Call it once, after the server listens. *)
  let start (t : t) : unit Async.Deferred.t =
    let started = Time_ns.now () in
    t.status <- Computing;
    let domain =
      Stdlib.Domain.spawn (fun () ->
          let result = Result.try_with (fun () -> run_study t) in
          Stdlib.Atomic.set t.finished true;
          result)
    in
    let rec wait () =
      if Stdlib.Atomic.get t.finished then (
        (match Stdlib.Domain.join domain with
        | Ok result ->
            t.status <-
              Done (result, Time_ns.Span.to_ms (Time_ns.diff (Time_ns.now ()) started))
        | Error exn -> t.status <- Failed (Exn.to_string exn));
        Async.Deferred.unit)
      else Async.Deferred.bind (Async.Clock_ns.after (Time_ns.Span.of_sec 1.0)) ~f:wait
    in
    wait ()

  (* For tests: the same study on the calling domain, to completion. *)
  let run_here (t : t) : unit =
    let started = Time_ns.now () in
    let result = run_study t in
    t.status <- Done (result, Time_ns.Span.to_ms (Time_ns.diff (Time_ns.now ()) started))

  let status_word (t : t) =
    match t.status with
    | Scheduled -> "scheduled"
    | Computing -> "computing"
    | Done _ -> "done"
    | Failed _ -> "failed"

  (* The engine's return window: the sample size the verdict is about. *)
  let engine_window = Synthetic_book.return_window

  let to_json (t : t) : Yojson.Safe.t =
    let module G = Vol_estimators.Garch11 in
    let row (r : Garch_study.Row.t) =
      `Assoc
        [
          ("n", jint r.n);
          ("alpha_mean", jfloat r.alpha_mean);
          ("alpha_sd", jfloat r.alpha_sd);
          ("beta_mean", jfloat r.beta_mean);
          ("beta_sd", jfloat r.beta_sd);
          ("persistence_mean", jfloat r.persistence_mean);
          ("persistence_sd", jfloat r.persistence_sd);
        ]
    in
    let rows, verdict_row, computed_in_ms =
      match t.status with
      | Done (result, ms) ->
          ( List.map result.rows ~f:row,
            (match
               List.find result.rows ~f:(fun (r : Garch_study.Row.t) ->
                   r.n = engine_window)
             with
            | Some r -> row r
            | None -> `Null),
            jfloat ms )
      | _ -> ([], `Null, `Null)
    in
    `Assoc
      [
        ("status", jstr (status_word t));
        ( "done",
          jint
            (match t.status with
            | Done _ -> fits_total t
            | _ -> Stdlib.Atomic.get t.fitted) );
        ("of", jint (fits_total t));
        ( "params",
          `Assoc
            [
              ("seed", jint t.seed);
              ("replications", jint t.replications);
              ("sample_sizes", `List (List.map t.sample_sizes ~f:jint));
              ("burn_in", jint t.burn_in);
            ] );
        ( "truth",
          `Assoc
            [
              ("omega", jfloat t.truth.omega);
              ("alpha", jfloat t.truth.alpha);
              ("beta", jfloat t.truth.beta);
              ("persistence", jfloat (G.persistence t.truth));
              ("half_life", jopt (G.shock_half_life t.truth));
            ] );
        ("window", jint engine_window);
        ("rows", `List rows);
        ("verdict_row", verdict_row);
        ("computed_in_ms", computed_in_ms);
        ("error", match t.status with Failed e -> jstr e | _ -> `Null);
      ]
end
