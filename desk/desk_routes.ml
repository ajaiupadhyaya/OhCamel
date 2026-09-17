(* The desk's routes (design §3.11).

   The read routes and the preview answer on both hosts. A preview creates
   nothing, so the public demo lets anyone ask what a trade would do to its
   limits.

   THE ROUTES THAT CHANGE THE DESK ARE PROTECTED BY WHERE A REQUEST CAME
   FROM, NOT BY A SECRET. On the live host Caddy's basic auth decides who
   reaches the engine at all; what is left is a page on another site getting a
   signed-in browser to post here. A cross-site form cannot set a custom
   header, the live host publishes no CORS header, and a browser labels its
   own requests with Sec-Fetch-Site and Origin -- so a request must carry
   X-OhCamel-Desk: 1 and say it came from this site, or it is refused. A token
   handed to the page would be handed to anyone past the password, and would
   stop nothing this does not (§8.1).

   HOST IS A SECOND SOURCE OF TRUTH, AND THE TWO MUST AGREE. [extensions] is
   given [host] once, at construction, separately from the [Server.t] it is
   handed on every request; nothing stops a caller from building one with the
   other host's argument. A mismatch is refused before [Protection.check]
   ever runs -- failing closed rather than trusting either side alone is what
   stops a wiring mistake (the demo built with [`Live]) from turning every
   visitor into someone who can halt or reset the desk.

   On the demo host each of those routes answers 405, with a sentence saying
   what can be done instead.

   A CAUGHT EXCEPTION NEVER SPEAKS IN ITS OWN WORDS, AND NEVER GOES UNANSWERED.
   cohttp-async writes a response only for a `Response; lib/server.ml's
   [on_handler_error] only logs. So a raise that reached neither would leave
   the caller a closed socket with no status -- on /orders, unable to tell
   whether an order exists. Every route here is guarded: the four that never
   await anything (tca, sessions, preview, kill/reset) by a plain try/with,
   which never touches the scheduler and keeps them peekable in the main
   suite; the parse step common to orders, cancel and kill by the same plain
   try/with, because nothing has reached the journal or the venue yet at that
   point; and the asynchronous rest of those three by [Monitor.try_with],
   because a raise inside a job run through Oms's own sequencer surfaces
   through its monitor, not as a synchronous exception a try/with would see.
   Every guard answers a fixed sentence -- never [Exn.to_string], which can
   carry a query fragment or a path -- and logs the real text through
   [Oms.on_event], the one line every other error in the order manager
   already prints through. The desk keeps running either way: the guard
   answers the request that failed, and the next one still dispatches. *)

open Core
open Async
module Server = Ohcamel.Server

type host = [ `Demo | `Live ]

module Protection = struct
  let header = "X-OhCamel-Desk"
  let authority host = function Some port -> sprintf "%s:%d" host port | None -> host

  let check ~(host : host) (r : Server.Request.t) :
      (unit, Cohttp.Code.status_code * string) Result.t =
    match host with
    | `Demo ->
        Error
          ( `Method_not_allowed,
            "the public demo takes no orders; POST a ticket to /api/desk/preview to see \
             what the rules and the limits would say, which creates nothing" )
    | `Live ->
        let get = Cohttp.Header.get r.Server.Request.headers in
        if not (Poly.equal r.Server.Request.meth `POST) then
          Error (`Method_not_allowed, "this route takes a POST")
        else if not (Option.equal String.equal (get header) (Some "1")) then
          Error
            (`Forbidden, "a request that changes the desk must carry X-OhCamel-Desk: 1")
        else
          let same_origin =
            Option.equal String.equal (get "sec-fetch-site") (Some "same-origin")
          in
          (* M2: the scheme must be http or https and the host must be
             non-empty. No browser ever sends "Origin: null", a
             protocol-relative "//host", or a scheme-only "https://" with
             nothing after it, but nothing stops another client from
             sending exactly that, and none of the three names a site this
             request came from. *)
          let origin_is_host =
            match (get "origin", get "host") with
            | Some origin, Some host -> (
                let uri = Uri.of_string origin in
                match Option.map (Uri.scheme uri) ~f:String.lowercase with
                | Some ("http" | "https") -> (
                    match Uri.host uri with
                    | Some h when not (String.is_empty h) ->
                        String.equal
                          (String.lowercase (authority h (Uri.port uri)))
                          (String.lowercase host)
                    | _ -> false)
                | _ -> false)
            | _ -> false
          in
          if same_origin || origin_is_host then Ok ()
          else
            Error (`Forbidden, "a request that changes the desk must come from this site")
end

let respond_error server status message =
  Server.respond_json ~status server
    (Yojson.Safe.to_string (`Assoc [ ("error", `String message) ]))

let respond server json = Server.respond_json server (Yojson.Safe.to_string json)

let body_json (r : Server.Request.t) =
  Option.try_with (fun () -> Yojson.Safe.from_string r.Server.Request.body)

let field (r : Server.Request.t) name =
  match body_json r with
  | Some (`Assoc fields) -> List.Assoc.find fields name ~equal:String.equal
  | _ -> None

(* Fixed sentences a caught exception answers with -- never the exception's
   own text, which can carry a query fragment, a file path, or a fragment of
   SQL. The real text still reaches the process's log, through [log_exn]
   below. Two, not one: a read (tca, sessions, a malformed body) has sent
   nothing anywhere, so it can say so; the asynchronous half of orders,
   cancel and kill may already have reached the journal or the venue by the
   time it raises, so it must not. *)
let read_error_sentence =
  "the desk hit an internal error and could not answer this request"

let mutation_error_sentence =
  "the desk hit an internal error partway through; the outcome is unknown -- look this \
   order up, or check /api/desk, before trying again"

let log_exn oms ~route exn =
  Oms.on_event oms (sprintf "desk      %s raised: %s" route (Exn.to_string exn))

(* Guards a handler that never awaits anything: tca, sessions, preview and
   kill/reset. A plain try/with, not [Monitor.try_with] -- the latter always
   schedules a job to collect its result, which is exactly the scheduler
   boundary these four routes must not cross to stay peekable in the main
   suite (test_desk_routes.ml's own header comment). [sentence] is the
   caller's to choose: tca, sessions and preview have sent nothing anywhere
   by the time they could raise, but kill/reset's [Halt.reset] may already
   have run before a later step (the event line, say) raises, so that one
   route passes [mutation_error_sentence] instead. *)
let guard_sync oms ~route ~sentence server
    (f : unit -> Cohttp_async.Server.response Deferred.t) :
    Cohttp_async.Server.response Deferred.t =
  try f ()
  with exn ->
    log_exn oms ~route exn;
    respond_error server `Internal_server_error sentence

(* The synchronous half of orders, cancel and kill: turning the body into
   what Oms needs. A field the rules reject ("side: buy or sell") is not an
   exception -- [parse] answers it as [Error] and this guard turns that into
   its own 400, same as ever. Nothing has reached the journal or the venue
   yet at this point, so a raise here is answered by [read_error_sentence]:
   the "nothing was sent" a route this far could still claim is exactly what
   [guard_async] below may no longer say once [k] has started. *)
let guard_parse oms ~route server (parse : unit -> ('a, string) Result.t)
    ~(k : 'a -> Cohttp_async.Server.response Deferred.t) :
    Cohttp_async.Server.response Deferred.t =
  match try Ok (parse ()) with exn -> Error exn with
  | Error exn ->
      log_exn oms ~route exn;
      respond_error server `Internal_server_error read_error_sentence
  | Ok (Error why) -> respond_error server `Bad_request why
  | Ok (Ok x) -> k x

(* The asynchronous half: the sequencer, the journal write before the wire,
   and the venue. [Monitor.try_with] is what actually catches a raise here --
   a job run through Oms's own sequencer that raises surfaces through its
   monitor, not as a synchronous OCaml exception, so a plain try/with placed
   here would never see it. *)
let guard_async oms ~route server (f : unit -> Cohttp_async.Server.response Deferred.t) :
    Cohttp_async.Server.response Deferred.t =
  match%bind Monitor.try_with ~extract_exn:true f with
  | Ok response -> return response
  | Error exn ->
      log_exn oms ~route exn;
      respond_error server `Internal_server_error mutation_error_sentence

let parse_ticket (r : Server.Request.t) : (Ticket.t, string) Result.t =
  match body_json r with
  | None -> Error "the body is not JSON"
  | Some json -> Ticket.of_json json

let with_ticket server r ~f =
  match parse_ticket r with
  | Ok ticket -> f ticket
  | Error why -> respond_error server `Bad_request why

let parse_cancel_id (r : Server.Request.t) : (Ids.Client_order_id.t, string) Result.t =
  match field r "client_order_id" with
  | Some (`String s) -> (
      match Ids.Client_order_id.of_string s with
      | Some id -> Ok id
      | None -> Error "client_order_id: one of this desk's order ids")
  | _ -> Error "client_order_id: one of this desk's order ids"

let parse_kill_reason (r : Server.Request.t) : (string, string) Result.t =
  Ok
    (match field r "why" with
    | Some (`String s) when not (String.is_empty (String.strip s)) ->
        String.prefix (String.strip s) 200
    | _ -> "no reason given")

(* M5: [host] is a second source of truth, given once at construction and
   never checked against the [Server.t] a request actually arrives on. A
   mismatch is refused before [Protection.check] runs at all -- failing
   closed rather than letting a wiring mistake (the demo built with [`Live])
   run the header check on a server whose [mode] says otherwise. Logged, not
   only answered: a 403 alone leaves no trace that the reason was a wiring
   mistake rather than an ordinary refusal. *)
let protected ~host ~oms ~f server r =
  let mode = Server.mode server in
  if not (Poly.equal host mode) then (
    Oms.on_event oms
      (sprintf
         "desk      a route built believing it is on the %s host was asked to answer as \
          %s; refusing rather than trusting either"
         (Server.mode_to_string host) (Server.mode_to_string mode));
    respond_error server `Forbidden
      "the desk's own host does not match this server; refusing rather than trusting \
       either")
  else
    match Protection.check ~host r with
    | Ok () -> f server r
    | Error (status, why) -> respond_error server status why

(* The newest 250 session closes, oldest first: a year of sessions, the
   window phase A5 validates over. Bounded, because this route answers anyone
   on the demo host, which records a session every five minutes -- the reason
   A1's final review (M5) bounded /api/desk's own queries. *)
let sessions_json journal : Yojson.Safe.t =
  let num x = if Float.is_finite x then `Float x else `Null in
  `List
    (List.map (Journal.recent_sessions journal ~limit:250) ~f:(fun s ->
         `Assoc
           [
             ("date", `String (Date.to_string s.Journal.Session.date));
             ("equity_close", num s.Journal.Session.equity_close);
             ("cash_close", num s.Journal.Session.cash_close);
             ("gross_close", num s.Journal.Session.gross_close);
             ("net_close", num s.Journal.Session.net_close);
           ]))

let extensions ~(host : host) ~(oms : Oms.t) : Server.extension list =
  let switch () = Halt.to_json (Oms.halt oms) ~now:(Time_ns.now ()) in
  [
    {
      Server.path = "/api/desk/tca";
      purpose = "what each fill cost in basis points, overall and by symbol";
      handle =
        (fun server _ ->
          guard_sync oms ~route:"tca" ~sentence:read_error_sentence server (fun () ->
              respond server (Oms.tca_json oms)));
    };
    {
      Server.path = "/api/desk/sessions";
      purpose = "the newest 250 session closes, oldest first";
      handle =
        (fun server _ ->
          guard_sync oms ~route:"sessions" ~sentence:read_error_sentence server (fun () ->
              respond server (sessions_json (Oms.journal oms))));
    };
    {
      Server.path = "/api/desk/preview";
      purpose =
        "POST a ticket: the rules' and the limits' answer to it, creating nothing (both \
         hosts)";
      handle =
        (fun server r ->
          guard_sync oms ~route:"preview" ~sentence:read_error_sentence server (fun () ->
              if not (Poly.equal r.Server.Request.meth `POST) then
                respond_error server `Method_not_allowed "POST a ticket to preview it"
              else
                with_ticket server r ~f:(fun ticket ->
                    respond server (Oms.Preview.to_json (Oms.preview oms ticket)))));
    };
    {
      Server.path = "/api/desk/orders";
      purpose =
        "POST a ticket: the rules, the limits, the journal, the venue (live host only)";
      handle =
        protected ~host ~oms ~f:(fun server r ->
            guard_parse oms ~route:"orders" server
              (fun () -> parse_ticket r)
              ~k:(fun ticket ->
                guard_async oms ~route:"orders" server (fun () ->
                    let%bind p, order = Oms.propose oms ~source:"ticket" ticket in
                    let refused =
                      Order.State.equal order.Order.state Order.State.Rejected_pre_trade
                    in
                    Server.respond_json server
                      ~status:(if refused then `Unprocessable_entity else `OK)
                      (Yojson.Safe.to_string
                         (`Assoc
                            [
                              ( "client_order_id",
                                `String
                                  (Ids.Client_order_id.to_string
                                     order.Order.request.Order.Request.client_order_id) );
                              ("state", `String (Order.State.to_string order.Order.state));
                              ( "reason",
                                Option.value_map order.Order.reason ~default:`Null
                                  ~f:(fun s -> `String s) );
                              ("preview", Oms.Preview.to_json p);
                            ])))));
    };
    {
      Server.path = "/api/desk/cancel";
      purpose = "POST {client_order_id}: cancel one open order (live host only)";
      handle =
        protected ~host ~oms ~f:(fun server r ->
            guard_parse oms ~route:"cancel" server
              (fun () -> parse_cancel_id r)
              ~k:(fun id ->
                guard_async oms ~route:"cancel" server (fun () ->
                    match%bind Oms.cancel oms id with
                    | Ok o ->
                        respond server
                          (`Assoc
                             [ ("state", `String (Order.State.to_string o.Order.state)) ])
                    | Error why -> respond_error server `Conflict why)));
    };
    {
      Server.path = "/api/desk/kill";
      purpose =
        "POST {why}: halt the desk and cancel every open order; positions are not \
         touched (live host only)";
      handle =
        protected ~host ~oms ~f:(fun server r ->
            guard_parse oms ~route:"kill" server
              (fun () -> parse_kill_reason r)
              ~k:(fun why ->
                guard_async oms ~route:"kill" server (fun () ->
                    (* The answer is the halt, not the cancels. [Oms.kill] halts
                       before it returns, and its cancels then go one at a
                       time behind the order manager's other jobs, each bounded
                       at 10 s: twenty open orders on a slow venue would hold
                       the person who pressed the button for minutes. So they
                       go on without the response, under a monitor of their
                       own, so a raise among them reaches the log and not the
                       process. A cancel the venue does not confirm logs its own
                       line; the run's end, or its raise, is logged below. *)
                    don't_wait_for
                      (match%map
                         Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
                             Oms.kill oms ~why)
                       with
                      | Ok () ->
                          Oms.on_event oms
                            "desk      the kill has asked the venue to cancel every open \
                             order it has an id for"
                      | Error exn -> log_exn oms ~route:"kill's cancels" exn);
                    respond server (switch ()))));
    };
    {
      Server.path = "/api/desk/kill/reset";
      purpose = {|POST {"confirm":"reset"}: lift the halt (live host only)|};
      handle =
        protected ~host ~oms ~f:(fun server r ->
            guard_sync oms ~route:"kill/reset" ~sentence:mutation_error_sentence server
              (fun () ->
                match field r "confirm" with
                | Some (`String "reset") ->
                    Halt.reset (Oms.halt oms);
                    (* M6: a reset is exactly as deliberate a change as a
                       kill, and gets the same line kill already writes. *)
                    Oms.on_event oms "desk      the switch was reset by request";
                    Oms.changed oms;
                    respond server (switch ())
                | _ ->
                    respond_error server `Bad_request
                      {|a reset must say so: {"confirm":"reset"}|}));
    };
  ]
