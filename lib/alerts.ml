(* Phase 4. Alerting and the kill switch.

   The one part of this system that can act on the outside world, which is why
   the brief singles it out:

     "This phase touches things that could send messages or take actions -- keep
      it behind explicit config/flags and don't wire it to anything that actually
      places real trades without me explicitly asking for that later."

   So, three commitments, each structural rather than a matter of care.

   1. EVERYTHING IS OFF BY DEFAULT. Config.Alerts.default has enabled = false.
      A breach is computed, displayed, and otherwise ignored until someone
      writes down that they want otherwise. The kill switch is a second,
      separate flag, because "tell me when a limit breaks" and "act when a limit
      breaks" are different levels of trust.

   2. THE KILL SWITCH SETS A FLAG AND NOTHING ELSE. There is no order-placement
      code anywhere in this repository, and this module does not import the
      Alpaca client -- it cannot reach a trading endpoint even by mistake. What
      [Kill_switch.halt_new_orders] returns is a bool for a human or a future
      execution layer to read. Wiring it to anything that trades is a decision
      for a later conversation, not a default.

   3. EFFECTS HANG OFF AN OBSERVER, NEVER A NODE BODY. limits.ml has said why
      since Phase 1: Incremental may recompute a node whenever it likes, so a
      node that sent a Slack message could send several. Graph.on_breaches
      attaches to the observer, which fires on CHANGE -- and even then the
      handler only writes to a pipe. All the sending happens in an Async
      consumer, outside stabilization, so a slow webhook cannot stall the graph.

   ------------------------------------------------------------------------
   WHY limits.ml STAYED PURE

   The brief puts this in limits.ml. It is in its own module instead, and the
   reason is structural: Limits.evaluate is called from INSIDE node bodies. If
   the sending code lived beside it, an effect would be one careless call away
   from firing during a stabilize. Separating them makes the invariant something
   the module graph enforces rather than something a reader has to remember. *)

open Core
open Async

(* ------------------------------------------------------------------------ *)
(* Events                                                                    *)
(* ------------------------------------------------------------------------ *)

module Event = struct
  type kind = Raised | Cleared | Kill_switch_tripped
  [@@deriving sexp_of, compare, equal]

  type t = {
    kind : kind;
    limit_name : string;
    scope : string;
    observed : float;
    threshold : float;
    utilisation : float;
    (* Whether the numbers above are dollars or a fraction. Carried on the event
       rather than looked up at render time, because an alert is formatted long
       after the limit that produced it has gone out of scope. *)
    money : bool;
    at : Types.Time.t;
  }
  [@@deriving sexp_of, fields ~getters]

  let of_breach ~kind ~at (breach : Types.Breach.t) =
    let limit = Types.Breach.limit breach in
    {
      kind;
      limit_name = Types.Limit.name limit;
      scope = Types.Limit.scope_to_string (Types.Limit.scope limit);
      observed = Types.Breach.observed breach;
      threshold = Types.Breach.threshold breach;
      utilisation = Limits.utilisation breach;
      money =
        (match Limits.unit_of (Types.Limit.kind limit) with
        | Limits.Money -> true
        | Limits.Fraction -> false);
      at;
    }

  let render t v =
    if t.money then Printf.sprintf "$%.2f" v else Printf.sprintf "%.2f%%" (v *. 100.0)

  (* One line, and it has to be readable at 3am on a phone. Says what broke,
     by how much, and in which direction it is moving. *)
  let to_line t =
    match t.kind with
    | Raised ->
        Printf.sprintf "BREACH  %s [%s]  %s over %s  (%.0f%% of limit)" t.limit_name
          t.scope (render t t.observed) (render t t.threshold) (t.utilisation *. 100.0)
    | Cleared ->
        Printf.sprintf "cleared %s [%s]  back to %s against %s  (%.0f%% of limit)"
          t.limit_name t.scope (render t t.observed) (render t t.threshold)
          (t.utilisation *. 100.0)
    | Kill_switch_tripped ->
        Printf.sprintf
          "KILL SWITCH TRIPPED by %s [%s] at %s against %s -- new orders flagged as \
           halted"
          t.limit_name t.scope (render t t.observed) (render t t.threshold)

  (* Slack's incoming-webhook shape. Deliberately plain text rather than blocks:
     a payload that renders correctly in the notification preview matters more
     than one that looks good once opened, and this message exists to be read on
     a lock screen. *)
  let to_slack_json t =
    `Assoc
      [
        ( "text",
          `String
            (Printf.sprintf "%s  ohcamel: %s"
               (match t.kind with
               | Raised -> ":rotating_light:"
               | Cleared -> ":white_check_mark:"
               | Kill_switch_tripped -> ":octagonal_sign:")
               (to_line t)) );
      ]
end

(* ------------------------------------------------------------------------ *)
(* Edge detection                                                            *)
(* ------------------------------------------------------------------------ *)

(* Which state changes are worth telling someone about.

   Pure and separately testable, because the interesting behaviour here is all
   about what it DOESN'T emit. Three rules:

   - Edge-triggered. A limit that has been breached for twenty minutes is one
     piece of news, not twenty. A feed that repeats itself is a feed people stop
     reading, which is the failure mode an alerting system most needs to avoid.

   - Hysteresis on the way down. A limit sitting exactly on its threshold would
     otherwise flap breached/cleared on every tick. Once raised, an alert clears
     only when utilisation falls back below [clear_below] -- it has to come
     properly back inside the line, not merely stop being outside it.

   - AN ALERT NEVER CLEARS BECAUSE THE DATA WENT AWAY. If a firing limit becomes
     unevaluable -- the VaR input vanishes, the feed dies -- the alert stays
     firing. "I can no longer tell" is not "it is fine", and treating it as
     resolution is the exact failure this whole engine is arranged against. *)
module Tracker = struct
  type state = Ok_ | Firing [@@deriving sexp_of, compare, equal]
  type t = { clear_below : float; states : state String.Table.t }

  let create ~clear_below = { clear_below; states = String.Table.create () }
  let state t name = Option.value (Hashtbl.find t.states name) ~default:Ok_
  let firing t name = equal_state (state t name) Firing

  (* Feed the current results; get back the events worth sending.

     [results] pairs each configured limit with its evaluation, [None] meaning
     the limit could not be evaluated this round. *)
  let step t ~(at : Types.Time.t) (results : (Types.Limit.t * Types.Breach.t option) list)
      : Event.t list =
    List.filter_map results ~f:(fun (limit, breach) ->
        let name = Types.Limit.name limit in
        let previous = state t name in
        match breach with
        | None ->
            (* Unevaluable. Deliberately no transition in either direction: a
               firing alert stays firing, and a quiet limit stays quiet. *)
            None
        | Some breach -> (
            let utilisation = Limits.utilisation breach in
            let breached = Types.Breach.breached breach in
            match (previous, breached) with
            | Ok_, true ->
                Hashtbl.set t.states ~key:name ~data:Firing;
                Some (Event.of_breach ~kind:Event.Raised ~at breach)
            | Firing, false when Float.( < ) utilisation t.clear_below ->
                Hashtbl.set t.states ~key:name ~data:Ok_;
                Some (Event.of_breach ~kind:Event.Cleared ~at breach)
            | Firing, false ->
                (* Inside the hysteresis band: back under the line but not far
                   enough to call it resolved. *)
                None
            | Firing, true | Ok_, false -> None))
end

(* ------------------------------------------------------------------------ *)
(* The kill switch                                                           *)
(* ------------------------------------------------------------------------ *)

(* A flag. That is the entire mechanism, and it is the entire point.

   [halt_new_orders] returns a bool. Nothing in this repository reads it to
   place, cancel or modify an order, because nothing in this repository places,
   cancels or modifies orders. This module does not depend on Alpaca_ws or
   Alpaca_rest and cannot reach a trading endpoint.

   Tripping is one-way until someone calls [reset]. A breaker that re-armed
   itself when the number came back under the line would be a breaker that
   silently un-halted a book while nobody was looking, which is worse than
   having none: the operator would believe a decision was still in force. *)
module Kill_switch = struct
  type state =
    | Disarmed (* not enabled in config *)
    | Armed
    | Tripped of { by : string; at : Types.Time.t }
  [@@deriving sexp_of]

  type t = { trips_on : String.Set.t; mutable state : state }

  let create ~(config : Config.Alerts.t) =
    {
      trips_on = String.Set.of_list config.Config.Alerts.kill_switch_trips_on;
      state = (if config.Config.Alerts.kill_switch_enabled then Armed else Disarmed);
    }

  let state t = t.state

  let halt_new_orders t =
    match t.state with Tripped _ -> true | Armed | Disarmed -> false

  let is_armed t = match t.state with Armed -> true | Tripped _ | Disarmed -> false

  (* Returns an event only on the transition into Tripped, so a persistent
     breach does not re-announce a halt that is already in force. *)
  let consider t ~(at : Types.Time.t) (event : Event.t) : Event.t option =
    match (t.state, event.Event.kind) with
    | Armed, Event.Raised when Set.mem t.trips_on event.Event.limit_name ->
        t.state <- Tripped { by = event.Event.limit_name; at };
        Some { event with Event.kind = Event.Kill_switch_tripped; at }
    | _ -> None

  let reset t =
    match t.state with Tripped _ -> t.state <- Armed | Armed | Disarmed -> ()
end

(* ------------------------------------------------------------------------ *)
(* Sinks                                                                     *)
(* ------------------------------------------------------------------------ *)

(* Where an event goes. Slack is the only one that leaves the machine, and it is
   the only one that can fail, so it is the only one that reports. *)
module Sink = struct
  let slack_webhook_var = "SLACK_WEBHOOK_URL"

  let post_to_slack ~(webhook : Config.Secret.t) (event : Event.t) :
      unit Or_error.t Deferred.t =
    let uri = Uri.of_string (Config.Secret.to_string webhook) in
    match%map
      Monitor.try_with ~extract_exn:true (fun () ->
          let%bind response, body =
            Cohttp_async.Client.post
              ~headers:(Cohttp.Header.of_list [ ("Content-Type", "application/json") ])
              ~body:
                (Cohttp_async.Body.of_string
                   (Yojson.Safe.to_string (Event.to_slack_json event)))
              uri
          in
          let%map body = Cohttp_async.Body.to_string body in
          (Cohttp.Response.status response, body))
    with
    (* The webhook URL is itself the credential -- anyone holding it can post to
       the channel -- so it is never named in an error. *)
    | Error exn -> Or_error.errorf "slack: post failed: %s" (Exn.to_string exn)
    | Ok (`OK, _) -> Ok ()
    | Ok (status, body) ->
        Or_error.errorf "slack: %s (%s)"
          (Cohttp.Code.string_of_status status)
          (String.prefix body 120)
end

(* ------------------------------------------------------------------------ *)
(* The notifier                                                              *)
(* ------------------------------------------------------------------------ *)

type t = {
  config : Config.Alerts.t;
  tracker : Tracker.t;
  kill_switch : Kill_switch.t;
  slack_webhook : Config.Secret.t option;
  (* The observer handler writes here and returns; an Async consumer drains it.
     A Pipe is exactly the right primitive: write_without_pushback is safe to
     call from inside a stabilize, and everything slow happens on the other
     side. *)
  writer : Event.t Pipe.Writer.t;
  history : Event.t Queue.t;
  mutable sent : int;
  mutable failed : int;
}

let history_limit = 50

let record t event =
  Queue.enqueue t.history event;
  while Queue.length t.history > history_limit do
    ignore (Queue.dequeue t.history : Event.t option)
  done

let deliver t (event : Event.t) =
  record t event;
  Deferred.List.iter t.config.Config.Alerts.sinks ~how:`Sequential ~f:(fun sink ->
      match sink with
      | Config.Alerts.Sink.Log ->
          printf "  ALERT  %s\n%!" (Event.to_line event);
          t.sent <- t.sent + 1;
          Deferred.unit
      | Config.Alerts.Sink.Dry_run ->
          (* Formats exactly what the Slack sink would send, and sends nothing.
             The way to see what alerting will do before letting it do it. *)
          printf "  ALERT (dry run, nothing sent)  %s\n         payload: %s\n%!"
            (Event.to_line event)
            (Yojson.Safe.to_string (Event.to_slack_json event));
          t.sent <- t.sent + 1;
          Deferred.unit
      | Config.Alerts.Sink.File path -> (
          match%map
            Monitor.try_with ~extract_exn:true (fun () ->
                Writer.with_file ~append:true path ~f:(fun w ->
                    Writer.write_line w
                      (Printf.sprintf "%s  %s"
                         (Time_ns.to_string_utc event.Event.at)
                         (Event.to_line event));
                    Writer.flushed w))
          with
          | Ok () -> t.sent <- t.sent + 1
          | Error exn ->
              t.failed <- t.failed + 1;
              eprintf "  ALERT sink %s failed: %s\n%!" path (Exn.to_string exn))
      | Config.Alerts.Sink.Slack -> (
          match t.slack_webhook with
          | None ->
              (* Configured but unusable. Loud, and every time: a silent alerting
                 channel is indistinguishable from a quiet market. *)
              t.failed <- t.failed + 1;
              eprintf
                "  ALERT slack sink configured but %s is not set -- NOT SENT: %s\n%!"
                Sink.slack_webhook_var (Event.to_line event);
              Deferred.unit
          | Some webhook -> (
              match%map Sink.post_to_slack ~webhook event with
              | Ok () -> t.sent <- t.sent + 1
              | Error error ->
                  t.failed <- t.failed + 1;
                  eprintf "  ALERT slack failed: %s\n%!" (Error.to_string_hum error))))

(* Attach to a graph.

   Returns [None] when alerting is disabled, which is the default -- so the
   caller cannot accidentally get a live notifier by forgetting a flag. *)
let attach ~(graph : Graph.t) ~(config : Config.Alerts.t) : t option Or_error.t =
  if not config.Config.Alerts.enabled then Ok None
  else
    let open Or_error.Let_syntax in
    let%map () = Config.Alerts.validate config in
    let reader, writer = Pipe.create () in
    let slack_webhook =
      if
        List.mem config.Config.Alerts.sinks Config.Alerts.Sink.Slack
          ~equal:Config.Alerts.Sink.equal
      then
        Option.map (Sys.getenv Sink.slack_webhook_var) ~f:(fun v ->
            Config.Secret.of_string (String.strip v))
      else None
    in
    let t =
      {
        config;
        tracker = Tracker.create ~clear_below:config.Config.Alerts.clear_below;
        kill_switch = Kill_switch.create ~config;
        slack_webhook;
        writer;
        history = Queue.create ();
        sent = 0;
        failed = 0;
      }
    in
    (* The observer hook the brief asks for. Everything it does is enqueue. *)
    Graph.on_breaches graph ~f:(fun _results ->
        let at = Types.Time.now () in
        let events = Tracker.step t.tracker ~at (Graph.limit_results graph) in
        List.iter events ~f:(fun event ->
            if not (Pipe.is_closed writer) then Pipe.write_without_pushback writer event;
            match Kill_switch.consider t.kill_switch ~at event with
            | None -> ()
            | Some tripped ->
                if not (Pipe.is_closed writer) then
                  Pipe.write_without_pushback writer tripped));
    don't_wait_for (Pipe.iter reader ~f:(fun event -> deliver t event));
    Some t

let kill_switch t = t.kill_switch
let halted t = Kill_switch.halt_new_orders t.kill_switch
let history t = Queue.to_list t.history
let sent t = t.sent
let failed t = t.failed

let status t =
  Printf.sprintf "sinks=%s sent=%d failed=%d kill_switch=%s"
    (String.concat ~sep:","
       (List.map t.config.Config.Alerts.sinks ~f:(fun s ->
            Sexp.to_string (Config.Alerts.Sink.sexp_of_t s))))
    t.sent t.failed
    (Sexp.to_string (Kill_switch.sexp_of_state (Kill_switch.state t.kill_switch)))
