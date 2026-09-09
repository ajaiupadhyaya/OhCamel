(* Phase 3.

   HTTP/JSON layer exposing current graph state (positions, exposure, VaR, limit
   status), plus a push stream (SSE or WebSocket) for live updates.

   The stream should be driven by Incremental.Observer callbacks firing on
   change -- not by a timer that serializes the whole book every second. A
   polling transport bolted onto a reactive core would reintroduce exactly the
   staleness the engine exists to remove.

   Built on cohttp-async (stable) rather than dream (1.0.0~alpha) -- see the
   toolchain note in README.md.

   ------------------------------------------------------------------------
   THE STREAM IS NOT A TIMER, AND THE DIFFERENCE IS THE PROJECT

   The broadcaster below blocks on an Ivar that Graph.on_change fills. If
   nothing in the book moves, the loop is parked and not one byte is serialized
   -- no wakeup, no snapshot, no frame. That is the whole chain closed: a tick
   arrives, Incremental decides what it touched, the observers whose values
   actually changed fire, and only then does anything reach the browser.

   There IS an [after] in the loop, and it is worth being precise about what it
   is. It runs AFTER a change has already been observed, to coalesce a burst of
   ticks into one frame; it never causes a wakeup on its own. A timer asks "has
   anything changed?"; this asks "how many more changes arrive in the next
   80ms?". A browser cannot render three hundred frames a second anyway, so the
   choice is between coalescing and dropping.

   SSE rather than WebSocket. The traffic is one-directional -- the dashboard
   reads and never writes -- so half of what a WebSocket provides is unused,
   and the half that is used comes free over an ordinary chunked response.
   EventSource also reconnects on its own, which matters for a page whose job is
   to be watched all day on a laptop that sleeps. *)

open Core
open Async

(* ------------------------------------------------------------------------ *)
(* JSON                                                                      *)
(* ------------------------------------------------------------------------ *)

(* Hand-written rather than derived. The snapshot's types are abstract on
   purpose (Notional.t and Symbol.t exist so they cannot be confused with
   floats and strings), and a derived encoder would either need those
   abstractions opened up or would emit their sexp form, which is not what a
   browser wants. Writing it out also makes the wire format an explicit choice:
   money crosses as a plain JSON number, and the UNIT of each limit crosses
   beside it, so the client never has to guess whether 0.02 is two cents or two
   percent. *)

let jfloat (x : float) : Yojson.Safe.t =
  (* NaN and infinity are not JSON. They should be impossible here -- the feed
     rejects non-finite prices -- but "should be impossible" is not a wire
     format, and a NaN would break the client's parse rather than showing up as
     a bad number. null renders as an em-dash, which is at least honest. *)
  if Float.is_finite x then `Float x else `Null

let jopt_float = function None -> `Null | Some x -> jfloat x
let jnotional (n : Types.Notional.t) = jfloat (Types.Notional.to_float n)
let jopt_notional = function None -> `Null | Some n -> jnotional n
let jstring s = `String s
let jlist f xs = `List (List.map xs ~f)

let json_of_feed_health (h : Graph.Feed_health.t) : Yojson.Safe.t =
  `Assoc
    [
      ("healthy", `Bool (Graph.Feed_health.all_healthy h));
      ( "stale",
        jlist (fun s -> jstring (Types.Symbol.to_string s)) (Graph.Feed_health.stale h) );
      ( "never_seen",
        jlist
          (fun s -> jstring (Types.Symbol.to_string s))
          (Graph.Feed_health.never_seen h) );
      ( "symbols",
        jlist
          (fun (st : Graph.Feed_health.Symbol_state.t) ->
            `Assoc
              [
                ("symbol", jstring (Types.Symbol.to_string st.symbol));
                ( "last_tick",
                  match st.last_tick with
                  | None -> `Null
                  | Some t -> jstring (Time_ns.to_string_utc t) );
                ("never_seen", `Bool st.never_seen);
                ("stale", `Bool st.stale);
              ])
          (Graph.Feed_health.symbols h) );
    ]

let json_of_breach (breach : Types.Breach.t) : Yojson.Safe.t =
  let limit = Types.Breach.limit breach in
  `Assoc
    [
      ("name", jstring (Types.Limit.name limit));
      ("scope", jstring (Types.Limit.scope_to_string (Types.Limit.scope limit)));
      (* The unit travels with the number. Gross notional and VaR are dollars;
         drawdown is a fraction. Types.ml keeps them apart precisely so a
         drawdown cannot be compared against a dollar exposure, and shipping
         them to a client as bare floats would undo that at the last step. *)
      ( "unit",
        jstring
          (match Limits.unit_of (Types.Limit.kind limit) with
          | Limits.Money -> "money"
          | Limits.Fraction -> "fraction") );
      ("observed", jfloat (Types.Breach.observed breach));
      ("threshold", jfloat (Types.Breach.threshold breach));
      ("excess", jfloat (Types.Breach.excess breach));
      ("breached", `Bool (Types.Breach.breached breach));
      ("utilisation", jfloat (Limits.utilisation breach));
    ]

(* The history, in the same wire conventions /api/snapshot uses: nulls rather
   than zeros for a number that does not exist yet, and money as plain floats.

   Column-major -- six arrays rather than an array of six-field objects. It is
   about a third of the bytes for five hundred points, and it is the shape a
   chart wants anyway: drawing a line means iterating one series, not
   re-projecting a list of records. The [time] array indexes all of them. *)
let json_of_history (h : History_buffer.t) : Yojson.Safe.t =
  let points = History_buffer.to_list h in
  let series f = `List (List.map points ~f) in
  `Assoc
    [
      ("points", `Int (List.length points));
      ("capacity", `Int (History_buffer.capacity h));
      (* Total changes recorded, including the ones evicted. "500 points" and
         "500 points out of 40,000 changes" say different things about how much
         of the session is on screen, and only the second is honest. *)
      ("appended", `Int (History_buffer.appended h));
      ("time", series (fun p -> jfloat (History_buffer.Point.time_ms p)));
      ("gross", series (fun p -> jfloat (History_buffer.Point.gross p)));
      ("net", series (fun p -> jfloat (History_buffer.Point.net p)));
      ("equity", series (fun p -> jfloat (History_buffer.Point.equity p)));
      ("drawdown", series (fun p -> jfloat (History_buffer.Point.drawdown p)));
      ("var_notional", series (fun p -> jopt_float (History_buffer.Point.var_notional p)));
      ("es_notional", series (fun p -> jopt_float (History_buffer.Point.es_notional p)));
    ]

let json_of_snapshot ~(graph : Graph.t) ~(factor : string) (s : Graph.Snapshot.t) :
    Yojson.Safe.t =
  let weights = Graph.Snapshot.weights s in
  `Assoc
    [
      ("as_of", jstring (Time_ns.to_string_utc (Types.Time.now ())));
      ("factor", jstring factor);
      ( "positions",
        jlist
          (fun (symbol, exposure) ->
            `Assoc
              [
                ("symbol", jstring (Types.Symbol.to_string symbol));
                ( "sector",
                  match Graph.sector_of graph symbol with
                  | None -> `Null
                  | Some sector -> jstring (Types.Sector.to_string sector) );
                ("exposure", jnotional exposure);
                ( "weight",
                  match Map.find weights symbol with None -> `Null | Some w -> jfloat w );
                (* This name's Euler share of portfolio VaR, in dollars. Can be
                   negative -- a position that moves against the book reduces
                   portfolio risk -- so the client must not format it as a
                   magnitude. [null] while warming up, which is not zero. *)
                ( "component_var",
                  match Graph.Snapshot.component_var_by_instrument s with
                  | None -> `Null
                  | Some shares -> (
                      match Map.find shares symbol with
                      | None -> `Null
                      | Some share -> jnotional share) );
              ])
          (Map.to_alist (Graph.Snapshot.exposure_by_instrument s)) );
      ( "sectors",
        jlist
          (fun (sector, exposure) ->
            `Assoc
              [
                ("sector", jstring (Types.Sector.to_string sector));
                ("exposure", jnotional exposure);
                ( "component_var",
                  match Graph.Snapshot.component_var_by_sector s with
                  | None -> `Null
                  | Some shares -> (
                      match Map.find shares sector with
                      | None -> `Null
                      | Some share -> jnotional share) );
              ])
          (Map.to_alist (Graph.Snapshot.exposure_by_sector s)) );
      ("gross_exposure", jnotional (Graph.Snapshot.gross_exposure s));
      ("net_exposure", jnotional (Graph.Snapshot.net_exposure s));
      ("equity", jnotional (Graph.Snapshot.equity s));
      ("current_drawdown", jfloat (Graph.Snapshot.current_drawdown s));
      ("historical_var", jopt_float (Graph.Snapshot.historical_var s));
      ("expected_shortfall", jopt_float (Graph.Snapshot.expected_shortfall s));
      ("parametric_var", jopt_float (Graph.Snapshot.parametric_var s));
      (* The same quantity under an exponentially weighted covariance matrix,
         plus the decay factor that produced it. The lambda is on the wire and
         not assumed by the reader, because two runs at different lambdas would
         otherwise publish different numbers under one field name. *)
      ("parametric_var_ewma", jopt_float (Graph.Snapshot.parametric_var_ewma s));
      ("ewma_lambda", jfloat (Graph.Snapshot.ewma_lambda s));
      ("value_at_risk_notional", jopt_notional (Graph.Snapshot.value_at_risk_notional s));
      ( "expected_shortfall_notional",
        jopt_notional (Graph.Snapshot.expected_shortfall_notional s) );
      ("portfolio_beta", jopt_float (Graph.Snapshot.portfolio_beta s));
      (* The two Greeks that have no place in the exposure sum. Plain floats
         rather than nullable, because a book with no options has a gamma of
         exactly zero -- that is a fact, not a missing value, and it is the one
         case where 0.0 is the honest answer rather than the dangerous one.

         Vega is on the wire in the ENGINE's unit, dollars per 1.00 of
         annualised vol, not the desk's per-vol-point. A wire format that
         silently applied a display convention would be a hundred-fold
         discrepancy between the API and the limit thresholds written against
         it; the /100 belongs in the renderer and is applied there. *)
      ("portfolio_gamma", jfloat (Graph.Snapshot.portfolio_gamma s));
      ("portfolio_vega", jfloat (Graph.Snapshot.portfolio_vega s));
      (* Vega regrouped by tenor, in the same units as the total above. An
         object rather than an array, keyed by the bucket's label, because the
         set of occupied buckets varies with the book and a positional array
         would make the consumer track which index means what. Absent buckets
         are absent rather than zero -- a book with nothing in the 6-12m bucket
         has no 6-12m exposure, which is different from having measured it to be
         zero. *)
      ( "vega_by_bucket",
        `Assoc
          (List.filter_map Options.Tenor_bucket.ordered ~f:(fun bucket ->
               match Map.find (Graph.Snapshot.vega_by_bucket s) bucket with
               | None -> None
               | Some vega -> Some (Options.Tenor_bucket.to_string bucket, jfloat vega)))
      );
      ("diversification_ratio", jopt_float (Graph.Snapshot.diversification_ratio s));
      ("warming_up", `Bool (Graph.Snapshot.warming_up s));
      ("feed", json_of_feed_health (Graph.Snapshot.feed_health s));
      ("limits", jlist json_of_breach (Graph.Snapshot.breaches s));
      ("unevaluated", jlist jstring (Graph.Snapshot.unevaluated_limits s));
      ("nodes_recomputed", `Int (Graph.total_nodes_recomputed ()));
    ]

(* The scenario suite, run against the book as it stands right now.

   Each outcome reports the shocked totals and the limits the scenario would
   move across their line in either direction -- new breaches and cleared ones.
   The full before/after snapshots are deliberately NOT serialized: they would
   multiply the payload by the number of scenarios to say something the client
   can already see, and the differences are what a scenario is for. *)
let json_of_stress (graph : Graph.t) : Yojson.Safe.t =
  let scenarios = Stress.suite_for ~graph in
  let outcomes = Stress.run_all ~graph ~scenarios in
  let names bs = jlist (fun b -> jstring (Types.Limit.name (Types.Breach.limit b))) bs in
  `Assoc
    [
      ("as_of", jstring (Time_ns.to_string_utc (Types.Time.now ())));
      ( "scenarios",
        jlist
          (fun (o : Stress.Outcome.t) ->
            let scenario = Stress.Outcome.scenario o in
            let after = Stress.Outcome.after o in
            `Assoc
              [
                ("name", jstring (Stress.Scenario.name scenario));
                ("description", jstring (Stress.Scenario.description scenario));
                ( "shocks",
                  jlist
                    (fun shock -> jstring (Stress.Shock.to_string shock))
                    (Stress.Scenario.shocks scenario) );
                ("pnl", jnotional (Stress.Outcome.pnl o));
                ("pnl_fraction", jfloat (Stress.Outcome.pnl_fraction o));
                ("gross_exposure", jnotional (Graph.Snapshot.gross_exposure after));
                ("equity", jnotional (Graph.Snapshot.equity after));
                ("current_drawdown", jfloat (Graph.Snapshot.current_drawdown after));
                ( "value_at_risk_notional",
                  jopt_notional (Graph.Snapshot.value_at_risk_notional after) );
                ("new_breaches", names (Stress.Outcome.new_breaches o));
                ("cleared_breaches", names (Stress.Outcome.cleared_breaches o));
                ( "unestimated_betas",
                  jlist
                    (fun s -> jstring (Types.Symbol.to_string s))
                    (Stress.Outcome.unestimated_betas o) );
              ])
          outcomes );
      ( "worst",
        match Stress.worst outcomes with
        | None -> `Null
        | Some w -> jstring (Stress.Scenario.name (Stress.Outcome.scenario w)) );
    ]

(* ------------------------------------------------------------------------ *)
(* The broadcaster                                                           *)
(* ------------------------------------------------------------------------ *)

(* Which host this process is.

   The two deployed engines run the same image with different arguments, and
   until now nothing in the process could tell them apart -- so the live host's
   page could not say `live · Alpaca + FRED`, and the demo host could not
   publish a CORS header without the live host publishing one too. A closed
   variant rather than a string: there are two hosts, there will not quietly be
   a third, and a typo in a string would have shipped as a mode nobody
   matched. *)
type mode = [ `Demo | `Live ]

let mode_to_string = function `Demo -> "demo" | `Live -> "live"

type t = {
  graph : Graph.t;
  factor : string;
  (* Present only when alerting is enabled, which is not the default. The
     dashboard reports what it finds; it does not turn anything on. *)
  alerts : Alerts.t option;
  (* What this process is, for /api/ops and for the CORS decision below. *)
  mode : mode;
  (* When it became this process. Nothing persists, so uptime is a difference
     against this and there is no uptime percentage anywhere -- that would need
     history nobody keeps. Read beside the build sha it is the "did up -d
     actually replace the container" check the smoke suite never had. *)
  started_at : Types.Time.t;
  (* Filled by [start]. 0 until then, and 0 is not a port: a reader who sees it
     is looking at a process that never bound, which is worth being able to see
     rather than a default that claims 8080. *)
  mutable port : int;
  (* The other host's origin, or None. Set only on the live container. /ops on
     the live origin fills its second column from the public demo engine, and
     nothing goes the other way. *)
  peer : string option;
  (* The feed's own counters, as a closure rather than as Alpaca and FRED
     records. This module must not learn a broker type -- it would then be
     linked into every mode including the ones that have no credentials -- so
     bin/main.ml closes over its two Stats records and hands back the object.
     None is the synthetic feed, and /api/ops says so in words. *)
  feed_stats : (unit -> Yojson.Safe.t) option;
  (* Symbols that are quiet ON PURPOSE. The demo book never ticks its last
     name so the stale path is visible; without this the page would report a
     working demonstration as a broken feed. Empty on the live host, where a
     quiet name means what it says. *)
  quiet : Types.Symbol.t list;
  (* Filled by Graph.on_change. The loop below reads it and immediately swaps in
     a fresh one, so changes arriving during a send are not lost. *)
  mutable changed : unit Ivar.t;
  mutable subscribers : string Pipe.Writer.t list;
  mutable frames_sent : int;
  coalesce : Time_ns.Span.t;
  (* A bounded in-memory trail, for the dashboard's sparklines. Fed by an
     observer rather than by this broadcaster, so a change is recorded whether or
     not anyone is subscribed -- a chart that only had history for the period
     someone was watching would be a strange thing to look at. See
     history_buffer.ml, which also states in as many words that it is not
     persistence. *)
  history : History_buffer.t;
}

(* A sink is named, never described.

   Config.Alerts.Sink.sexp_of_t renders `File "/var/log/ohcamel.log"` as
   `(File /var/log/ohcamel.log)`, and this object is served unauthenticated on
   the demo host. A filesystem path is information about the machine that
   nothing on the page needs, and "which kinds of sink are configured" is the
   whole question a reader is asking. *)
let sink_name (sink : Config.Alerts.Sink.t) : string =
  match sink with
  | Config.Alerts.Sink.Log -> "log"
  | Config.Alerts.Sink.File _ -> "file"
  | Config.Alerts.Sink.Slack -> "slack"
  | Config.Alerts.Sink.Dry_run -> "dry_run"

(* What Phase 4 is doing, for the dashboard and for /ops to report.

   Reports and never mutates: there is no route that arms, trips or resets
   anything. A kill switch that could be flipped by an unauthenticated GET
   would be a worse hazard than the one it guards against.

   Both branches emit the same keys. A client that has to test for a field's
   existence before reading it is a client that renders `undefined` the first
   time the other branch ships, and the two branches here are two hosts. What
   differs is the VALUES: with no notifier there is no hysteresis to report, so
   clear_below is null rather than the default it would have had -- a default
   printed as a measurement is the one thing this wire format exists to
   prevent.

   [firing] is the tracker's state and not the breach list, and the difference
   is the hysteresis: a limit back under its threshold but above clear_below is
   not breached and is still firing. Only one of those two facts is in
   /api/snapshot's `limits`, and it is not the one an operator wants at 3am. *)
let json_of_alerts (alerts : Alerts.t option) : Yojson.Safe.t =
  match alerts with
  | None ->
      `Assoc
        [
          ("enabled", `Bool false);
          ("kill_switch", `String "off");
          ("tripped_by", `Null);
          ("tripped_at", `Null);
          ("halt_new_orders", `Bool false);
          ("firing", `List []);
          ("sent", `Int 0);
          ("failed", `Int 0);
          ("sinks", `List []);
          ("trips_on", `List []);
          ("clear_below", `Null);
          ("recent", `List []);
        ]
  | Some a ->
      let config = Alerts.config a in
      let state, tripped_by =
        match Alerts.Kill_switch.state (Alerts.kill_switch a) with
        | Alerts.Kill_switch.Disarmed -> ("off", `Null)
        | Alerts.Kill_switch.Armed -> ("armed", `Null)
        | Alerts.Kill_switch.Tripped { by; _ } -> ("tripped", `String by)
      in
      `Assoc
        [
          ("enabled", `Bool true);
          ("kill_switch", `String state);
          ("tripped_by", tripped_by);
          (* On the switch rather than dug out of the event history, because
             the history is a bounded queue of fifty and the trip is the event
             most likely to still matter after it has been evicted. *)
          ( "tripped_at",
            match Alerts.tripped_at a with
            | None -> `Null
            | Some at -> jstring (Time_ns.to_string_utc at) );
          ("halt_new_orders", `Bool (Alerts.halted a));
          ("firing", jlist jstring (Alerts.firing_limits a));
          ("sent", `Int (Alerts.sent a));
          ("failed", `Int (Alerts.failed a));
          ("sinks", jlist jstring (List.map config.Config.Alerts.sinks ~f:sink_name));
          ("trips_on", jlist jstring config.Config.Alerts.kill_switch_trips_on);
          ("clear_below", jfloat config.Config.Alerts.clear_below);
          ( "recent",
            `List
              (List.rev_map (Alerts.history a) ~f:(fun e ->
                   `Assoc
                     [
                       ( "kind",
                         `String
                           (Sexp.to_string
                              (Alerts.Event.sexp_of_kind e.Alerts.Event.kind)) );
                       ("limit", `String e.Alerts.Event.limit_name);
                       ("line", `String (Alerts.Event.to_line e));
                       ("at", `String (Time_ns.to_string_utc e.Alerts.Event.at));
                     ])) );
        ]

let render (t : t) : string =
  let snapshot = Graph.snapshot t.graph in
  let json = json_of_snapshot ~graph:t.graph ~factor:t.factor snapshot in
  match json with
  | `Assoc fields ->
      Yojson.Safe.to_string (`Assoc (fields @ [ ("alerts", json_of_alerts t.alerts) ]))
  | other -> Yojson.Safe.to_string other

(* One SSE event. The blank line terminates it; without the second newline the
   browser buffers the frame indefinitely waiting for more. *)
let sse_event (payload : string) = "data: " ^ payload ^ "\n\n"

let broadcast (t : t) (payload : string) =
  let live, closed =
    List.partition_tf t.subscribers ~f:(fun w -> not (Pipe.is_closed w))
  in
  List.iter closed ~f:(fun w -> Pipe.close w);
  t.subscribers <- live;
  if not (List.is_empty live) then t.frames_sent <- t.frames_sent + 1;
  Deferred.List.iter live ~how:`Parallel ~f:(fun w ->
      (* A subscriber that has stopped reading must not hold up the others, and
         must not let this loop accumulate unbounded backlog. Pipe.write blocks
         on pushback, so the write is bounded by whether the pipe is still
         open -- a browser that vanished without closing the socket is dropped
         on the next pass by the partition above. *)
      if Pipe.is_closed w then Deferred.unit else Pipe.write w (sse_event payload))

(* Blocks until something changes. Never wakes on its own. *)
let rec run_broadcaster (t : t) =
  let%bind () = Ivar.read t.changed in
  t.changed <- Ivar.create ();
  (* Coalesce. See the note at the top of this file: this delay happens after a
     change has already been observed, so it bounds frame rate without ever
     being the reason a frame is produced. *)
  let%bind () = after (Time_ns.Span.to_span_float_round_nearest t.coalesce) in
  let%bind () =
    if List.is_empty t.subscribers then Deferred.unit else broadcast t (render t)
  in
  run_broadcaster t

(* ------------------------------------------------------------------------ *)
(* Routes                                                                    *)
(* ------------------------------------------------------------------------ *)

let json_header_fields =
  [ ("Content-Type", "application/json"); ("Cache-Control", "no-store") ]

(* The demo host's JSON is readable from anywhere; the live host's is not.

   /ops on the live origin draws BOTH hosts, and the only way to do that from
   one page is to fetch the public engine's JSON cross-origin. The alternative
   the design rejected was a CORS rule in the Caddyfile or a counts-only route
   outside the password, and both of those widen the gate for a convenience
   the owner can get by opening the other tab. This does not touch the gate at
   all: it is one header, emitted by the engine that is already public, on the
   routes that already return the whole book to anyone who asks.

   Never on [html_headers] and never on [sse_headers]. A document is not a
   datum, and the stream carries the same book at a higher rate; if either
   carried this header the rule would be "the demo host is open", which is a
   larger claim than the one being made. *)
let json_headers ~(mode : mode) =
  Cohttp.Header.of_list
    (match mode with
    | `Demo -> ("Access-Control-Allow-Origin", "*") :: json_header_fields
    | `Live -> json_header_fields)

let html_headers =
  Cohttp.Header.of_list
    [ ("Content-Type", "text/html; charset=utf-8"); ("Cache-Control", "no-store") ]

let sse_headers =
  Cohttp.Header.of_list
    [
      ("Content-Type", "text/event-stream");
      ("Cache-Control", "no-store");
      ("Connection", "keep-alive");
      (* Nginx and friends buffer streaming responses by default, which turns a
         live feed into a feed that arrives in one lump when the connection
         finally closes. Harmless when nothing is proxying; essential when
         something is. *)
      ("X-Accel-Buffering", "no");
    ]

(* The SSE keepalive interval, named rather than written inline in [subscribe].

   /api/ops publishes it, and a published value that had drifted from the timer
   would be a lie about the one number a reader uses to tell `parked` from
   `dead`: a stream with no frame for longer than this and no keepalive either
   is a dropped connection, and a stream with keepalives and no frames is a
   market that is closed. *)
let keepalive = Time_ns.Span.of_sec 20.0

(* ------------------------------------------------------------------------ *)
(* /api/ops                                                                  *)
(* ------------------------------------------------------------------------ *)

(* Which build this is.

   The field the deployment did not have and could not do without: `docker
   compose up -d` is a no-op when the image digest has not changed, so a build
   that silently failed to replace the container serves a healthy old dashboard
   forever, and none of the original nine smoke assertions could tell. Absent
   arguments read `unknown` all the way through -- never a date this process
   invented, which would make the comparison pass while being false. *)
let build_json () : Yojson.Safe.t =
  let sha = Build_info.git_sha in
  `Assoc
    [
      ("git_sha", jstring sha);
      (* Seven characters of a real sha, or the whole word. "unknow" would read
         as a sha that had been truncated rather than as an absence. *)
      ("git_short", jstring (if String.length sha = 40 then String.prefix sha 7 else sha));
      ("built_at", jstring Build_info.built_at);
      ("profile", jstring Build_info.profile);
      ("architecture", jstring Build_info.architecture);
      ("system", jstring Build_info.system);
      ("executable", jstring Stdlib.Sys.executable_name);
    ]

(* The heap, as the runtime sees it.

   quick_stat rather than stat: stat walks the major heap, and a monitoring
   route that triggers a full collection to report on collections is a route
   that changes what it measures. Words rather than bytes, because that is what
   the runtime counts; the page multiplies by the word size and says so. *)
let gc_json () : Yojson.Safe.t =
  let s = Gc.quick_stat () in
  `Assoc
    [
      ("heap_words", `Int (Gc.Stat.heap_words s));
      ("top_heap_words", `Int (Gc.Stat.top_heap_words s));
      ("minor_collections", `Int (Gc.Stat.minor_collections s));
      ("major_collections", `Int (Gc.Stat.major_collections s));
      ("compactions", `Int (Gc.Stat.compactions s));
      ("minor_words", jfloat (Gc.Stat.minor_words s));
      ("promoted_words", jfloat (Gc.Stat.promoted_words s));
      ("major_words", jfloat (Gc.Stat.major_words s));
    ]

(* Resident set size, or nothing.

   The heap above is what the runtime believes; this is what the kernel
   charges, and the gap between them is the answer to "is 41 MB the engine or
   the engine plus OpenBLAS". /proc/self/statm exists on Linux and nowhere
   else, so on macOS this is null -- not zero, which would render as a process
   using no memory at all.

   Read synchronously. The file is forty bytes the kernel materialises on read
   and never blocks on, so a blocking read costs less than making this whole
   encoder deferred would; the page size is 4096 on the amd64 Debian this image
   is built for, and any platform where it is not never reaches this line. *)
let rss_bytes () : int option =
  match Option.try_with (fun () -> Core.In_channel.read_all "/proc/self/statm") with
  | None -> None
  | Some contents -> (
      match String.split (String.strip contents) ~on:' ' with
      | _ :: resident :: _ ->
          Option.map
            (Option.try_with (fun () -> Int.of_string resident))
            ~f:(fun pages -> pages * 4096)
      | _ -> None)

(* Where the numbers came from, from the caller rather than from here.

   server.ml must not learn an Alpaca type: it is linked into every mode,
   including the six that have no credentials, and a broker's record reaching
   this module would put the feed's vocabulary in the middle of the wire
   format. So bin/main.ml closes over its two Stats records and hands back the
   whole object. None is the synthetic feed, and it says so in a word rather
   than as four nulls the client has to interpret. *)
let feed_source_json (t : t) : Yojson.Safe.t =
  match t.feed_stats with
  | Some stats -> stats ()
  | None ->
      `Assoc
        [
          ("kind", jstring "synthetic");
          ("alpaca_feed", `Null);
          ("fred_series", `Null);
          ("alpaca", `Null);
          ("fred", `Null);
        ]

(* The operations object.

   Nothing here stabilizes, and that is the one design decision in this
   function. [Graph.feed_health] reads an observer; [Graph.snapshot] would
   settle the graph first, and a route that reported nodes_recomputed after
   advancing it would be measuring itself -- the liveness pulse on /ops would
   then show a flat line of ones whether or not the engine was alive, which is
   worse than no pulse. /api/health may stabilize because its job is the
   current answer; this one's job is the current state.

   Every count is a count and no list of names appears: the live host's book is
   behind a password, the owner reads this page on a phone, and "six symbols,
   one stale" is the whole of what the row is for. *)
let json_of_ops (t : t) : Yojson.Safe.t =
  let health = Graph.feed_health t.graph in
  `Assoc
    [
      ("mode", jstring (mode_to_string t.mode));
      ("started_at", jstring (Time_ns.to_string_utc t.started_at));
      (* A difference, not a percentage. Nothing persists, so there is no
         history to compute availability from and the page says so instead of
         inventing one. *)
      ( "uptime_s",
        jfloat (Time_ns.Span.to_sec (Time_ns.diff (Types.Time.now ()) t.started_at)) );
      ("port", `Int t.port);
      ("pid", `Int (Pid.to_int (Unix.getpid ())));
      (* The CONTAINER id, not the droplet's name, and the page says which. *)
      ("hostname", jstring (Unix.gethostname ()));
      ("ocaml_version", jstring Stdlib.Sys.ocaml_version);
      ("build", build_json ());
      (* Incremental's counters for the whole process: inflated by the startup
         probe and by every fork /api/stress makes. Published because "is it
         alive" is answered by the direction, and labelled on the page because
         a reader who takes nodes_created for the graph's size concludes the
         engine is enormous. *)
      ( "process",
        `Assoc
          [
            ("nodes_recomputed", `Int (Graph.total_nodes_recomputed ()));
            ("stabilizes", `Int (Graph.total_stabilizes ()));
            ("nodes_created", `Int (Graph.total_nodes_created ()));
            ("var_sets", `Int (Graph.total_var_sets ()));
            ("active_observers", `Int (Graph.active_observers ()));
          ] );
      (* The per-graph counts, which forks never reach. Phase 4 fills this from
         the recompute log; until then it is null, because a zero here would
         say "no named node ran", which is the alarm. *)
      ("graph", `Assoc [ ("named", `Null) ]);
      ( "stream",
        `Assoc
          [
            ("frames_sent", `Int t.frames_sent);
            (* Open pipes only. A browser that vanished without closing is
               dropped on the next broadcast, so this can lag by one frame and
               never by a session. *)
            ( "subscribers",
              `Int (List.count t.subscribers ~f:(fun w -> not (Pipe.is_closed w))) );
            ("coalesce_ms", jfloat (Time_ns.Span.to_ms t.coalesce));
            ("keepalive_s", jfloat (Time_ns.Span.to_sec keepalive));
          ] );
      ( "history",
        `Assoc
          [
            ("appended", `Int (History_buffer.appended t.history));
            ("points", `Int (List.length (History_buffer.to_list t.history)));
            ("capacity", `Int (History_buffer.capacity t.history));
          ] );
      ("alerts", json_of_alerts t.alerts);
      ( "feed",
        `Assoc
          [
            ("healthy", `Bool (Graph.Feed_health.all_healthy health));
            ("symbols", `Int (List.length (Graph.symbols t.graph)));
            ("stale", `Int (List.length (Graph.Feed_health.stale health)));
            ("never_seen", `Int (List.length (Graph.Feed_health.never_seen health)));
            (* Quiet ON PURPOSE. Without this the demo host's deliberate stale
               name reads as a broken feed, which is the opposite of the
               demonstration. *)
            ("quiet", `Int (List.length t.quiet));
            ( "staleness_threshold_s",
              jfloat (Time_ns.Span.to_sec (Graph.staleness_threshold t.graph)) );
          ] );
      ("feed_source", feed_source_json t);
      ("gc", gc_json ());
      ("rss_bytes", match rss_bytes () with None -> `Null | Some b -> `Int b);
      (* Phase 5 fills these from Reports.compute and the GARCH domain. Until
         then, absent -- a freshly deployed engine reading `ready` beside an
         empty figure would be the wrong kind of surprise. *)
      ("reports", `Assoc [ ("static", jstring "absent"); ("garch", jstring "absent") ]);
      ("peer", match t.peer with None -> `Null | Some url -> jstring url);
    ]

let subscribe (t : t) =
  let reader, writer = Pipe.create () in
  t.subscribers <- writer :: t.subscribers;
  (* Send the current state immediately rather than making the page wait for the
     first tick. A dashboard that is blank until the market moves is a dashboard
     that looks broken outside of trading hours. *)
  don't_wait_for
    (let%map () = Pipe.write writer (sse_event (render t)) in
     ());
  (* A keepalive, and the one genuine timer in this file. SSE comments are
     ignored by the client and exist solely so an idle connection is not
     reaped by a proxy or a laptop's power management. It carries no data, so
     it is transport plumbing rather than a polling loop. *)
  don't_wait_for
    (Deferred.repeat_until_finished () (fun () ->
         let%bind () = after (Time_ns.Span.to_span_float_round_nearest keepalive) in
         if Pipe.is_closed writer then return (`Finished ())
         else
           let%map () = Pipe.write writer ": keepalive\n\n" in
           `Repeat ()));
  Cohttp_async.Server.respond_with_pipe ~flush:true ~headers:sse_headers reader

(* ------------------------------------------------------------------------ *)
(* The routes table                                                          *)
(* ------------------------------------------------------------------------ *)

(* One table, two readers.

   [handle] dispatches from it and the 404 body lists it, and until now the
   two were separate literals -- a match with seven arms and a list of six
   strings -- which is how a route can be served for weeks while the 404 body
   tells a caller it does not exist. A route that is in one and not the other
   is now a compile-time impossibility rather than a review-time hope, and the
   test in test_server.ml that compares the two is there for the day someone
   reintroduces the second literal.

   The purpose is one line, in this repository's voice, and goes on the 404
   body beside the path: an unknown route is the one moment a caller is
   reading the API by hand, and a bare list of paths answers "what exists"
   without answering "which one did I mean".

   Handlers take [t] and nothing else. Every route is a GET whose method is
   ignored and whose query string is ignored, and that is the whole contract:
   a handler that wanted the request would be a handler that could be asked to
   do something, and no route here does anything. *)
type handler = t -> Cohttp_async.Server.response Deferred.t

let routes : (string * string * handler) list =
  [
    ( "/",
      "the dashboard: the book, its limits and the trail, over the stream",
      fun _ ->
        Cohttp_async.Server.respond_string ~headers:html_headers Dashboard_html.page );
    ( "/ops",
      "the operations page: which build, how long, and what the process is doing",
      fun _ -> Cohttp_async.Server.respond_string ~headers:html_headers Ops_html.html );
    ( "/api/snapshot",
      "the whole book as JSON, with the counter that proves the graph is alive",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode) (render t)
    );
    ( "/api/health",
      "feed liveness per symbol; healthy is false when anything is stale",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string
             (json_of_feed_health (Graph.Snapshot.feed_health (Graph.snapshot t.graph))))
    );
    ( "/api/stream",
      "server-sent events, one frame per graph change, coalesced over 80 ms",
      subscribe );
    (* Read from the buffer as it stands; nothing is computed here. The buffer
       is filled by an observer on the graph, so this route is a read of state
       that already exists rather than a request that causes work -- the same
       property /api/snapshot has, and the reason neither can stall the
       engine. *)
    ( "/api/history",
      "the in-memory trail, column-major, lost on restart",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_history t.history)) );
    (* Scenarios are computed on demand rather than pushed on the stream, and
       the reason is the cost asymmetry. A snapshot is read from observers
       that have already settled; a scenario suite forks the engine once per
       scenario and stabilizes each fork. Putting that behind the SSE loop
       would mean paying it on every tick to serve a number nobody is looking
       at most of the time.

       Still a GET with no body and no effect: stress.ml runs every scenario
       on a fork and destroys it, so this route cannot move the live book. *)
    ( "/api/stress",
      "the scenario suite, run on a fork of the book as it stands",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_stress t.graph)) );
    ( "/api/ops",
      "what this process is: build, uptime, counters, stream, feed, alerts, heap",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_ops t)) );
  ]

let route_paths () : string list = List.map routes ~f:(fun (path, _, _) -> path)

(* The 404 body, generated from the table and rendered once: the table does
   not change after the module is initialised, and an unknown route is not a
   reason to serialise anything. *)
let not_found_body : string =
  Yojson.Safe.to_string
    (`Assoc
       [
         ("error", `String "not found");
         ("routes", jlist jstring (route_paths ()));
         ( "purposes",
           `Assoc (List.map routes ~f:(fun (path, purpose, _) -> (path, `String purpose)))
         );
       ])

(* /index.html is the one alias. It has been answered since Phase 3 and a
   bookmark to it must not start 404ing; it is not in the table because it is
   not a route, it is a spelling of one, and the 404 body should not offer a
   caller two names for the same page. *)
let lookup (path : string) : handler option =
  let path = if String.equal path "/index.html" then "/" else path in
  List.find_map routes ~f:(fun (p, _, h) -> if String.equal p path then Some h else None)

let handle (t : t) ~(path : string) =
  match lookup path with
  | Some handler -> handler t
  | None ->
      Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
        ~status:`Not_found not_found_body

(* ------------------------------------------------------------------------ *)
(* Starting                                                                  *)
(* ------------------------------------------------------------------------ *)

let create ?(coalesce = Time_ns.Span.of_ms 80.0) ?history_capacity
    ?(alerts : Alerts.t option) ?(peer : string option)
    ?(feed_stats : (unit -> Yojson.Safe.t) option) ?(quiet : Types.Symbol.t list = [])
    ~(mode : mode) ~(graph : Graph.t) ~(factor : string) () =
  let t =
    {
      graph;
      factor;
      alerts;
      mode;
      started_at = Types.Time.now ();
      port = 0;
      peer;
      feed_stats;
      quiet;
      changed = Ivar.create ();
      subscribers = [];
      frames_sent = 0;
      coalesce;
      history =
        History_buffer.attach ?capacity:history_capacity ~graph ~now:Types.Time.now ();
    }
  in
  (* The link that makes this reactive rather than polled. Graph.on_change fires
     inside stabilization, so the handler does the minimum possible: fill an
     Ivar. All the work -- snapshotting, serializing, writing -- happens in the
     broadcaster, outside the graph. *)
  Graph.on_change graph ~f:(fun () -> Ivar.fill_if_empty t.changed ());
  don't_wait_for (run_broadcaster t);
  t

let start ?(port = 8080) (t : t) =
  (* Recorded here rather than passed to [create], because the port is the
     caller's decision at listen time and /api/ops must report the one actually
     bound rather than the one someone intended. *)
  t.port <- port;
  Cohttp_async.Server.create
    ~on_handler_error:
      (`Call
         (fun _ exn ->
           (* One bad request must not take the server down. A dashboard that
             dies because a browser sent something odd is worse than no
             dashboard, because the operator believes they are being watched. *)
           eprintf "ohcamel/server: %s\n%!" (Exn.to_string exn)))
    (Tcp.Where_to_listen.of_port port)
    (fun ~body:_ _address request ->
      handle t ~path:(Uri.path (Cohttp.Request.uri request)))

let frames_sent (t : t) = t.frames_sent
let subscriber_count (t : t) = List.length t.subscribers
let mode (t : t) = t.mode
let port (t : t) = t.port
let started_at (t : t) = t.started_at
let peer (t : t) = t.peer
let quiet (t : t) = t.quiet
