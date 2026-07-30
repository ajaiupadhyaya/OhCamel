(* Phase 2.

   Alpaca WebSocket market data client. Parses ticks and pushes them into the
   Var.t cells created in graph.ml. This module's only job is to move the
   outside world into the graph's inputs -- it must not compute risk, and it
   must not read derived nodes.

   Reconnect/backoff is a requirement, not a nicety: feeds drop, and a graph
   whose inputs quietly stop updating still serves confident, stale numbers to
   the dashboard. Staleness needs to be visible -- a last-tick timestamp that
   the graph can reason about, so "no data" is distinguishable from "no change."

   ------------------------------------------------------------------------
   THE WIRE PROTOCOL (v2, checked against Alpaca's docs rather than recalled)

     connect   wss://stream.data.alpaca.markets/v2/{feed}
               feed is iex (free), sip (paid), or delayed_sip (15 min behind)
     server -> [{"T":"success","msg":"connected"}]
     client -> {"action":"auth","key":"...","secret":"..."}
     server -> [{"T":"success","msg":"authenticated"}]
     client -> {"action":"subscribe","trades":["AAPL","MSFT"]}
     server -> [{"T":"subscription","trades":[...],...}]
     server -> [{"T":"t","S":"AAPL","p":126.55,"s":1,"t":"2021-02-22T15:51:44.208Z",
                 "i":96921,"x":"D","c":["@","I"],"z":"C"}, ...]

   Every server message is a JSON ARRAY, batched. That batching is why the
   engine's set-then-stabilize split exists: a frame carrying forty trades marks
   forty cells and settles the graph once.

   ------------------------------------------------------------------------
   VALIDATION IS THIS MODULE'S JOB

   types.ml deliberately left Price.of_float unguarded, with a note that
   "feed-level validation is the right place for that". This is that place. A
   negative, zero, infinite or NaN price must never reach a Var: it would
   propagate to exposure, to VaR, to a limit, and be rendered as a number
   someone acts on. Bad prints are dropped and counted, never clamped -- a
   clamped price is a fabricated observation, and once it is in the graph it is
   indistinguishable from a real one. *)

open Core
open Async

(* ------------------------------------------------------------------------ *)
(* Reconnect backoff                                                         *)
(* ------------------------------------------------------------------------ *)

(* Exponential backoff with jitter, capped.

   A pure function of the attempt number, so it can be unit-tested without
   sleeping. A backoff schedule verified by watching a log is a backoff schedule
   nobody verifies.

   The jitter guards a failure this process cannot cause on its own but will
   eventually meet: clients reconnecting in lockstep after a provider outage,
   delivering a thundering herd to a service that is already unwell. It costs
   nothing here and is the difference between being part of the recovery and
   part of the problem.

   [attempt] is 1-based -- the first retry waits [base], not zero. *)
module Backoff = struct
  type t = { base : Time_ns.Span.t; cap : Time_ns.Span.t; jitter : float }

  let default =
    { base = Time_ns.Span.of_sec 1.0; cap = Time_ns.Span.of_sec 60.0; jitter = 0.25 }

  (* The deterministic part, separate so tests can assert the shape of the
     schedule without reasoning about randomness. *)
  let base_delay t ~attempt =
    if attempt < 1 then
      invalid_argf "alpaca_ws: backoff attempt must be >= 1, got %d" attempt ();
    (* Doubling happens on the float scale and is clamped BEFORE it becomes a
       span. As an integer shift this would overflow to a negative span after
       about sixty consecutive failures -- roughly an hour of outage, which is
       precisely the situation this code exists to survive. *)
    let scale = Float.min (2.0 ** float_of_int (attempt - 1)) 1e9 in
    let delay = Time_ns.Span.scale t.base scale in
    if Time_ns.Span.( > ) delay t.cap then t.cap else delay

  (* Jitter is applied downward only, so the cap stays a genuine cap. *)
  let delay ?(random = Random.float) t ~attempt =
    Time_ns.Span.scale (base_delay t ~attempt) (1.0 -. (t.jitter *. random 1.0))
end

(* ------------------------------------------------------------------------ *)
(* Message parsing                                                           *)
(* ------------------------------------------------------------------------ *)

module Message = struct
  (* Only what this engine consumes. Alpaca sends quotes, bars, trading
     statuses, LULD bands and more on the same socket; anything unrecognised
     becomes [Other]. That is deliberate -- an unknown message type is a
     protocol addition on their side, not an error on ours, and failing on one
     would make the feed brittle against a routine vendor release. *)
  type t =
    | Trade of Types.Tick.t
    | Success of string
    | Subscription of string
    | Error of { code : int; msg : string }
    | Other of string
  [@@deriving sexp_of]

  let member key = function
    | `Assoc fields -> List.Assoc.find fields key ~equal:String.equal
    | _ -> None

  let to_string_opt = function Some (`String s) -> Some s | _ -> None

  let to_int_opt = function
    | Some (`Int i) -> Some i
    | Some (`Float f) -> Some (Float.to_int f)
    | _ -> None

  let to_float_opt = function
    | Some (`Float f) -> Some f
    | Some (`Int i) -> Some (float_of_int i)
    | _ -> None

  (* Alpaca timestamps are RFC-3339, nanosecond precision, always UTC.

     A timestamp that will not parse is NOT grounds for dropping the print. The
     price is the load-bearing field; the timestamp only feeds staleness, and
     falling back to local receipt time there is both honest -- we did just
     receive it -- and strictly better than discarding a real trade over a clock
     format. The reverse trade-off, accepting a bad price to keep a good
     timestamp, would be indefensible, which is why prices are handled the
     opposite way. *)
  let parse_time = function
    | None -> Types.Time.now ()
    | Some s -> (
        match Option.try_with (fun () -> Time_ns.of_string_with_utc_offset s) with
        | Some t -> t
        | None -> Types.Time.now ())

  (* A price that reaches a Var is a price the whole engine will believe.

     Rejected: non-finite (NaN, +/-inf), zero, and negative. NaN is the one
     worth naming. It does not raise -- it propagates silently through every
     arithmetic operation downstream, so one NaN print turns exposure, VaR and
     every limit comparison into NaN. And since every float comparison against
     NaN is false, a NaN exposure reads as "not breached" on every limit in the
     book. That is an entire risk system switched off by one bad JSON field. *)
  let valid_price p = Float.is_finite p && Float.( > ) p 0.0

  let of_json (json : Yojson.Safe.t) : t =
    match to_string_opt (member "T" json) with
    | Some "t" -> (
        match (to_string_opt (member "S" json), to_float_opt (member "p" json)) with
        | Some symbol, Some price when valid_price price ->
            Trade
              {
                Types.Tick.symbol = Types.Symbol.of_string symbol;
                price = Types.Price.of_float price;
                time = parse_time (to_string_opt (member "t" json));
              }
        | _ -> Other (Yojson.Safe.to_string json))
    | Some "success" ->
        Success (Option.value (to_string_opt (member "msg" json)) ~default:"")
    | Some "subscription" -> Subscription (Yojson.Safe.to_string json)
    | Some "error" ->
        Error
          {
            code = Option.value (to_int_opt (member "code" json)) ~default:0;
            msg = Option.value (to_string_opt (member "msg" json)) ~default:"";
          }
    | _ -> Other (Yojson.Safe.to_string json)

  (* Every frame is a JSON array. A frame that is not an array, or is not JSON
     at all, yields the empty list rather than raising: this runs inside the read
     loop, and one malformed frame must not take the feed down. *)
  let of_frame (payload : string) : t list =
    match Option.try_with (fun () -> Yojson.Safe.from_string payload) with
    | None -> []
    | Some (`List items) -> List.map items ~f:of_json
    | Some single -> [ of_json single ]
end

(* ------------------------------------------------------------------------ *)
(* Error classification                                                      *)
(* ------------------------------------------------------------------------ *)

(* Alpaca's documented error codes, and -- more usefully -- whether retrying
   could possibly help.

   That distinction is the entire point of this module. Reconnecting after "auth
   failed" or "connection limit exceeded" fails identically every time, forever,
   while printing a reconnect message that looks like progress. Those conditions
   need a person, so they stop the feed with an explanation of what to go and
   do. *)
module Failure = struct
  type t = Fatal of string | Retryable of string [@@deriving sexp_of]

  let of_code ~code ~msg =
    let detail = sprintf "alpaca: error %d (%s)" code msg in
    let fatal suffix = Fatal (detail ^ " -- " ^ suffix) in
    match code with
    | 402 ->
        fatal "the credentials were rejected. Check ALPACA_API_KEY and ALPACA_SECRET_KEY."
    | 406 ->
        fatal
          "this account already has as many concurrent streams as its plan allows. A \
           free Alpaca plan permits ONE. Something else is holding it open -- another \
           OhCamel, or another system using the same keys."
    | 409 ->
        fatal
          "the account's plan does not include this data feed. A free plan gets iex; sip \
           requires a subscription. See OHCAMEL_ALPACA_FEED."
    | 410 -> fatal "this channel is not available on this feed."
    | 405 -> fatal "the book subscribes to more symbols than the plan allows."
    | 400 | 401 | 403 | 404 ->
        (* Protocol-sequencing faults. Retryable in the narrow sense that a
           fresh connection restarts the handshake from the top, which is the
           only thing that could resolve a sequencing problem. *)
        Retryable detail
    | 407 ->
        Retryable
          (detail
         ^ " -- the server considers this client too slow. If it recurs, the graph is \
            taking too long per frame.")
    | _ -> Retryable detail
end

(* ------------------------------------------------------------------------ *)
(* Statistics                                                                *)
(* ------------------------------------------------------------------------ *)

(* Counters, so an operator can tell a quiet market from a broken feed.

   [rejected] and [unknown_symbol] are separate on purpose. The first means
   Alpaca sent something this module refused to believe, which is alarming. The
   second means it sent a symbol the book does not hold, which during a
   subscription change is entirely ordinary. Collapsing them would make the
   alarming case invisible inside the ordinary one. *)
module Stats = struct
  type t = {
    mutable frames : int;
    mutable trades : int;
    mutable rejected : int;
    mutable unknown_symbol : int;
    mutable reconnects : int;
    mutable last_error : string option;
  }

  let create () =
    {
      frames = 0;
      trades = 0;
      rejected = 0;
      unknown_symbol = 0;
      reconnects = 0;
      last_error = None;
    }

  let to_string t =
    sprintf "frames=%d trades=%d rejected=%d unknown_symbol=%d reconnects=%d%s" t.frames
      t.trades t.rejected t.unknown_symbol t.reconnects
      (match t.last_error with None -> "" | Some e -> sprintf " last_error=%S" e)
end

(* ------------------------------------------------------------------------ *)
(* Applying a frame                                                          *)
(* ------------------------------------------------------------------------ *)

(* Apply one frame's worth of trades and settle the graph ONCE.

   This is where the engine's design pays off. A busy frame carries dozens of
   trades; each [apply_tick] marks two cells and computes nothing, and the
   single [stabilize] propagates all of them in one pass. Stabilizing per trade
   would do the same total work over and over.

   Pure with respect to the network, so the tests drive it with captured
   payloads and never open a socket. Returns the non-trade messages for the
   caller to act on. *)
let apply_frame ~(graph : Graph.t) ~(stats : Stats.t) (messages : Message.t list) =
  let control = ref [] in
  let applied = ref 0 in
  List.iter messages ~f:(fun message ->
      match message with
      | Message.Trade tick ->
          if Graph.knows_symbol graph (Types.Tick.symbol tick) then (
            Graph.apply_tick graph tick;
            stats.Stats.trades <- stats.Stats.trades + 1;
            Int.incr applied)
          else
            (* Not an error. A subscription can legitimately outlive a position,
               and Graph.set_price raises on an unknown symbol by design, so the
               filtering has to happen here. *)
            stats.Stats.unknown_symbol <- stats.Stats.unknown_symbol + 1
      | Message.Other _ ->
          (* Either a message type this engine does not consume, or a trade that
             failed validation. Counted rather than logged per occurrence: a
             malformed feed would otherwise produce unbounded log volume at
             exactly the moment the log needs to stay readable. *)
          stats.Stats.rejected <- stats.Stats.rejected + 1
      | other -> control := other :: !control);
  if !applied > 0 then Graph.stabilize graph;
  List.rev !control

(* ------------------------------------------------------------------------ *)
(* Connection                                                                *)
(* ------------------------------------------------------------------------ *)

(* websocket-async draws the Sec-WebSocket-Key nonce from Mirage_crypto_rng,
   whose default generator has to be seeded once per process before first use.
   Nothing says so: skip it and every handshake raises "The default generator is
   not yet initialized" from inside the library, which -- because the handshake
   result is easy to discard -- presents as a connection that closes 80ms after
   opening, with no error anywhere.

   Seeded here rather than in main.ml so that a caller does not have to know
   that this module's transport has a cryptographic dependency. [lazy] because
   it must happen exactly once and there is no natural startup hook in a library
   that is only ever entered through [run]. *)
let rng_initialized = lazy (Mirage_crypto_rng_unix.use_default ())
let default_host = "stream.data.alpaca.markets"
let stream_uri ~host ~feed = Uri.make ~scheme:"wss" ~host ~path:(sprintf "/v2/%s" feed) ()

let auth_frame ~(credentials : Config.Credentials.t) =
  `Assoc
    [
      ("action", `String "auth");
      ("key", `String (Config.Secret.to_string credentials.Config.Credentials.alpaca_key));
      ( "secret",
        `String (Config.Secret.to_string credentials.Config.Credentials.alpaca_secret) );
    ]
  |> Yojson.Safe.to_string

let subscribe_frame ~symbols =
  `Assoc
    [
      ("action", `String "subscribe");
      ("trades", `List (List.map symbols ~f:(fun s -> `String (Types.Symbol.to_string s))));
    ]
  |> Yojson.Safe.to_string

(* Resolve, open TLS, and hand the reader/writer to websocket-async's framing
   layer.

   The framing itself is bought, not written. It is exactly the kind of code
   that is easy to write, hard to get right, and catastrophic to get subtly
   wrong: a mis-framed feed does not crash, it delivers the wrong bytes, which
   here means wrong prices.

   [client] rather than the friendlier [client_ez], for one reason that cost
   real time to learn. [client_ez] runs the handshake in the background and
   discards its result -- a rejected upgrade surfaces only as pipes that quietly
   close, and the actual HTTP status goes to a [logs] source that has no
   reporter attached. [client] returns [unit Deferred.Or_error.t] and fills an
   [initialized] ivar, so a handshake failure arrives as a sentence instead of a
   silence. The price is handling Ping/Pong/Close here, which is a dozen lines
   and is worth seeing in a component whose liveness is the point. *)
let with_connection uri ~f =
  let host = Option.value_exn (Uri.host uri) ~message:"alpaca: stream URI has no host" in
  let port = Option.value (Uri.port uri) ~default:443 in
  let%bind addresses =
    Unix.Addr_info.get ~host [ Unix.Addr_info.AI_FAMILY Unix.PF_INET ]
  in
  match addresses with
  | [] -> failwithf "alpaca: cannot resolve %s" host ()
  | { Unix.Addr_info.ai_addr; _ } :: _ ->
      let ip =
        match ai_addr with
        (* Via the dotted-quad string rather than Ipaddr_unix.of_inet_addr:
           ipaddr-unix is not in this switch, and a round trip through the
           textual form costs one allocation once per connection. *)
        | Unix.ADDR_INET (addr, _) -> Ipaddr.of_string_exn (Unix.Inet_addr.to_string addr)
        | Unix.ADDR_UNIX _ -> failwithf "alpaca: %s resolved to a unix socket" host ()
      in
      Conduit_async.V2.with_connection
        (`OpenSSL (ip, port, Conduit_async.V2.Ssl.Config.create ~hostname:host ()))
        (fun reader writer -> f ~reader ~writer)

(* The handshake is a sequence, and it is SERVER-DRIVEN.

   Alpaca greets a new connection with {"T":"success","msg":"connected"} and
   expects the auth message only after that. Sending auth immediately -- the
   obvious thing to write -- fails twice over: the greeting has not arrived, and
   on a fresh socket the WebSocket upgrade itself may not have completed, so the
   write lands on a pipe that is not open yet. That failure surfaces as nothing
   more informative than "write to closed pipe", several layers from its cause.

   So each step is triggered by the message that licenses it. *)
module Phase = struct
  type t = Awaiting_greeting | Awaiting_auth | Streaming [@@deriving sexp_of, equal]
end

(* How a session ended, and therefore whether to try again.

   This is a type rather than a bool because of a bug it exists to prevent. A
   fatal error and a dropped connection ARRIVE TOGETHER: Alpaca sends error 402,
   then closes the socket, so the read loop raises End_of_file a few
   milliseconds later. When the supervisor learned about the disconnect through
   an exception and about the fatality through a side channel, the exception won
   the race and it reconnected -- five times, on credentials that could never
   work, while faithfully printing the message explaining that they never would.

   So a session reports one verdict, decided in one place, with the fatal
   condition taking precedence over whatever the socket did on its way down. *)
module Outcome = struct
  type t =
    | Fatal of string (* no retry can fix this; stop and say why *)
    | Disconnected of string (* the ordinary case; back off and reconnect *)
end

(* One connection, handshake to disconnect. Never raises: every way a session can
   end is folded into a single [Outcome.t] at the bottom. *)
let run_session ~graph ~credentials ~feed_uri ~stats ~on_control ~on_event =
  (* Declared outside the connection callback because [with_connection] insists
     its callback return unit -- so the session's verdict has to be carried out
     by reference rather than returned. *)
  let fatal = ref None in
  let phase = ref Phase.Awaiting_greeting in
  let handshake_error = ref None in
  let%map connection =
    Monitor.try_with ~extract_exn:true (fun () ->
        with_connection feed_uri ~f:(fun ~reader ~writer ->
            (* Four pipes, and the naming follows websocket-async's convention:
           app_to_ws carries frames this module writes, ws_to_app carries frames
           it reads. *)
            let app_to_ws, send_frame = Pipe.create () in
            let received_frames, ws_to_app = Pipe.create () in
            let initialized = Ivar.create () in
            let send text =
              Pipe.write send_frame
                (Websocket.Frame.create ~opcode:Websocket.Frame.Opcode.Text ~content:text
                   ())
            in
            let handle_payload payload =
              stats.Stats.frames <- stats.Stats.frames + 1;
              let control = apply_frame ~graph ~stats (Message.of_frame payload) in
              (* Handled in order, and each may need to write a reply, so this is a
             sequential deferred iteration rather than a plain one. *)
              Deferred.List.iter control ~how:`Sequential ~f:(fun message ->
                  on_control message;
                  match (message, !phase) with
                  | Message.Success "connected", Phase.Awaiting_greeting ->
                      phase := Phase.Awaiting_auth;
                      send (auth_frame ~credentials)
                  | Message.Success "authenticated", Phase.Awaiting_auth ->
                      phase := Phase.Streaming;
                      let symbols = Graph.symbols graph in
                      on_event
                        (sprintf "authenticated; subscribing to %d symbols"
                           (List.length symbols));
                      send (subscribe_frame ~symbols)
                  | Message.Error { code; msg }, _ ->
                      (match Failure.of_code ~code ~msg with
                      | Failure.Fatal detail ->
                          stats.Stats.last_error <- Some detail;
                          fatal := Some detail
                      | Failure.Retryable detail ->
                          stats.Stats.last_error <- Some detail;
                          on_event detail);
                      Deferred.unit
                  | _ -> Deferred.unit)
            in
            (* Control frames are the protocol's own housekeeping and are answered
           here rather than passed on. A server Ping that goes unanswered gets
           the connection dropped for being unresponsive, which would present as
           a mysterious periodic disconnect. *)
            let read_loop () =
              Pipe.iter received_frames ~f:(fun (frame : Websocket.Frame.t) ->
                  match frame.Websocket.Frame.opcode with
                  | Websocket.Frame.Opcode.Ping ->
                      Pipe.write send_frame
                        (Websocket.Frame.create ~opcode:Websocket.Frame.Opcode.Pong
                           ~content:frame.Websocket.Frame.content ())
                  | Websocket.Frame.Opcode.Close ->
                      Pipe.close send_frame;
                      Deferred.unit
                  | Websocket.Frame.Opcode.Text | Websocket.Frame.Opcode.Binary ->
                      let%bind () = handle_payload frame.Websocket.Frame.content in
                      if Option.is_some !fatal then Pipe.close send_frame;
                      Deferred.unit
                  | Websocket.Frame.Opcode.Pong | _ -> Deferred.unit)
            in
            don't_wait_for (read_loop ());
            don't_wait_for
              (let%map () = Ivar.read initialized in
               on_event "websocket handshake complete");
            match%map
              Websocket_async.client ~initialized ~app_to_ws ~ws_to_app ~net_to_ws:reader
                ~ws_to_net:writer feed_uri
            with
            | Ok () -> ()
            | Error error -> handshake_error := Some (Error.to_string_hum error)))
  in
  (* Order matters, and it is the whole point of this block. A fatal error and
     the socket closing behind it are the SAME event seen twice; the fatal
     reading is the one that tells you what to do. *)
  match !fatal with
  | Some detail -> Outcome.Fatal detail
  | None ->
      (* A session that closes without ever reaching Streaming is not an
         ordinary disconnect, and saying so is the difference between a readable
         log and a mystery. *)
      let context =
        if Phase.equal !phase Phase.Streaming then ""
        else
          sprintf " while still %s -- the handshake did not complete"
            (Sexp.to_string (Phase.sexp_of_t !phase))
      in
      let reason =
        match (!handshake_error, connection) with
        | Some detail, _ -> detail
        | None, Error exn -> Exn.to_string exn
        | None, Ok () -> "stream closed"
      in
      Outcome.Disconnected (reason ^ context)

(* The supervisor. Reconnects with backoff until a fatal condition.

   Deliberately never raises into the caller. The graph keeps serving whatever it
   last knew, and feed_health -- downstream of the last-tick cells this module
   writes, and of nothing else -- is what tells the operator the numbers have
   stopped moving. A feed that took the process down with it would at least be
   obvious; a feed that died quietly while the dashboard stayed green is the
   failure this entire design is arranged against. *)
let run ?(backoff = Backoff.default) ?(host = default_host)
    ?(on_control = fun (_ : Message.t) -> ()) ?(on_event = fun (_ : string) -> ())
    ~(graph : Graph.t) ~(credentials : Config.Credentials.t) ~(runtime : Config.Runtime.t)
    ~(stats : Stats.t) () =
  Lazy.force rng_initialized;
  let feed_uri = stream_uri ~host ~feed:runtime.Config.Runtime.alpaca_feed in
  let rec attempt n =
    on_event (sprintf "connecting to %s (attempt %d)" (Uri.to_string feed_uri) n);
    match%bind run_session ~graph ~credentials ~feed_uri ~stats ~on_control ~on_event with
    | Outcome.Fatal detail ->
        on_event (sprintf "STOPPING: %s" detail);
        return (Error detail)
    | Outcome.Disconnected reason -> retry (n + 1) ~reason
  and retry n ~reason =
    stats.Stats.reconnects <- stats.Stats.reconnects + 1;
    let delay = Backoff.delay backoff ~attempt:(n - 1) in
    on_event
      (sprintf "disconnected (%s); reconnecting in %s" reason
         (Time_ns.Span.to_string_hum delay));
    let%bind () = after (Time_ns.Span.to_span_float_round_nearest delay) in
    attempt n
  in
  attempt 1
