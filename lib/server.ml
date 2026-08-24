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
      ("value_at_risk_notional", jopt_notional (Graph.Snapshot.value_at_risk_notional s));
      ( "expected_shortfall_notional",
        jopt_notional (Graph.Snapshot.expected_shortfall_notional s) );
      ("portfolio_beta", jopt_float (Graph.Snapshot.portfolio_beta s));
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

type t = {
  graph : Graph.t;
  factor : string;
  (* Present only when alerting is enabled, which is not the default. The
     dashboard reports what it finds; it does not turn anything on. *)
  alerts : Alerts.t option;
  (* Filled by Graph.on_change. The loop below reads it and immediately swaps in
     a fresh one, so changes arriving during a send are not lost. *)
  mutable changed : unit Ivar.t;
  mutable subscribers : string Pipe.Writer.t list;
  mutable frames_sent : int;
  coalesce : Time_ns.Span.t;
}

(* What Phase 4 is doing, for the dashboard to report.

   Reports and never mutates: there is no route that arms, trips or resets
   anything. A kill switch that could be flipped by an unauthenticated GET would
   be a worse hazard than the one it guards against. *)
let json_of_alerts (alerts : Alerts.t option) : Yojson.Safe.t =
  match alerts with
  | None -> `Assoc [ ("enabled", `Bool false); ("kill_switch", `String "off") ]
  | Some a ->
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
          ("halt_new_orders", `Bool (Alerts.halted a));
          ("sent", `Int (Alerts.sent a));
          ("failed", `Int (Alerts.failed a));
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

let json_headers =
  Cohttp.Header.of_list
    [ ("Content-Type", "application/json"); ("Cache-Control", "no-store") ]

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
         let%bind () =
           after (Time_ns.Span.to_span_float_round_nearest (Time_ns.Span.of_sec 20.0))
         in
         if Pipe.is_closed writer then return (`Finished ())
         else
           let%map () = Pipe.write writer ": keepalive\n\n" in
           `Repeat ()));
  Cohttp_async.Server.respond_with_pipe ~flush:true ~headers:sse_headers reader

let handle (t : t) ~(path : string) =
  match path with
  | "/" | "/index.html" ->
      Cohttp_async.Server.respond_string ~headers:html_headers Dashboard_html.page
  | "/api/snapshot" -> Cohttp_async.Server.respond_string ~headers:json_headers (render t)
  | "/api/health" ->
      Cohttp_async.Server.respond_string ~headers:json_headers
        (Yojson.Safe.to_string
           (json_of_feed_health (Graph.Snapshot.feed_health (Graph.snapshot t.graph))))
  | "/api/stream" -> subscribe t
  (* Scenarios are computed on demand rather than pushed on the stream, and the
     reason is the cost asymmetry. A snapshot is read from observers that have
     already settled; a scenario suite forks the engine once per scenario and
     stabilizes each fork. Putting that behind the SSE loop would mean paying it
     on every tick to serve a number nobody is looking at most of the time.

     Still a GET with no body and no effect: stress.ml runs every scenario on a
     fork and destroys it, so this route cannot move the live book. *)
  | "/api/stress" ->
      Cohttp_async.Server.respond_string ~headers:json_headers
        (Yojson.Safe.to_string (json_of_stress t.graph))
  | _ ->
      Cohttp_async.Server.respond_string ~headers:json_headers ~status:`Not_found
        (Yojson.Safe.to_string
           (`Assoc
              [
                ("error", `String "not found");
                ( "routes",
                  `List
                    (List.map
                       [
                         "/"; "/api/snapshot"; "/api/health"; "/api/stream"; "/api/stress";
                       ] ~f:(fun r -> `String r)) );
              ]))

(* ------------------------------------------------------------------------ *)
(* Starting                                                                  *)
(* ------------------------------------------------------------------------ *)

let create ?(coalesce = Time_ns.Span.of_ms 80.0) ?(alerts : Alerts.t option)
    ~(graph : Graph.t) ~(factor : string) () =
  let t =
    {
      graph;
      factor;
      alerts;
      changed = Ivar.create ();
      subscribers = [];
      frames_sent = 0;
      coalesce;
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
