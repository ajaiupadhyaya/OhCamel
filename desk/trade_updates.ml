(* Alpaca's trade_updates stream: the venue telling the desk what happened to
   its orders.

   It is a different socket from the market-data stream the risk engine holds
   -- the paper trading host, binary frames carrying JSON -- so it does not
   spend the free plan's one data stream. Authentication is the client's
   first message; the server answers on the "authorization" stream, and a
   "listen" for trade_updates is answered on "listening".

   Reconnection reuses the market-data client's backoff. After every
   successful listen the desk reconciles against the venue's REST view, since
   an update sent while the socket was down is an update this stream will
   never deliver. *)

open Core
open Async
open Ohcamel.Types

module Message = struct
  type t =
    | Authorized
    | Unauthorized
    | Listening of string list
    | Update of Venue.Update.t
    | Stream_error of string
    | Other

  let member k = function
    | `Assoc fs -> List.Assoc.find fs k ~equal:String.equal
    | _ -> None

  let str = function Some (`String s) -> Some s | _ -> None

  let num = function
    | Some (`String s) -> Float.of_string_opt s
    | Some (`Float f) -> Some f
    | Some (`Int n) -> Some (Float.of_int n)
    | _ -> None

  let of_json (json : Yojson.Safe.t) : t =
    let data = member "data" json in
    match (str (member "stream" json), data) with
    | Some "authorization", Some d -> (
        match str (member "status" d) with
        | Some "authorized" -> Authorized
        | _ -> Unauthorized)
    | Some "listening", Some d -> (
        match member "streams" d with
        | Some (`List xs) ->
            Listening (List.filter_map xs ~f:(function `String s -> Some s | _ -> None))
        | _ -> Listening [])
    | Some "trade_updates", Some d -> (
        match
          ( str (member "event" d),
            Option.map (member "order" d) ~f:Alpaca_paper.venue_order_of_json )
        with
        | Some event, Some (Ok order) ->
            let at =
              Option.value ~default:(Time_ns.now ())
                (Option.bind (str (member "timestamp" d)) ~f:Desk_time.parse)
            in
            let fill =
              match (num (member "qty" d), num (member "price" d)) with
              | Some qty, Some price
                when List.mem [ "fill"; "partial_fill" ] event ~equal:String.equal ->
                  Some
                    {
                      Order.Fill.execution_id =
                        Option.value
                          (str (member "execution_id" d))
                          ~default:
                            (order.Venue.Venue_order.id ^ ":" ^ Desk_time.rfc3339 at);
                      qty;
                      price = Price.of_float price;
                      at;
                      position_qty = num (member "position_qty" d);
                    }
              | _ -> None
            in
            Update { Venue.Update.event; order; fill; at }
        | _ -> Other)
    | _ -> (
        match (str (member "action" json), data) with
        | Some "error", Some d ->
            Stream_error
              (Option.value (str (member "error_message" d)) ~default:"stream error")
        | _ -> Other)
end

(* ------------------------------------------------------------------------ *)
(* The session                                                               *)
(* ------------------------------------------------------------------------ *)

module Alpaca_ws = Ohcamel.Alpaca_ws

(* Built by [Alpaca_paper.trading_uri] with its scheme changed, so the host is
   the constant invariant 9 names and the only function that puts a host in a
   trading URI is still the one beside that constant. *)
let stream_uri = Uri.with_scheme (Alpaca_paper.trading_uri "/stream") (Some "wss")

(* The key and secret leave [Secret] for this frame and nowhere else in this
   module: the frame goes to the socket, never to [on_event]. *)
let auth_frame (credentials : Alpaca_paper.Credentials.t) =
  Yojson.Safe.to_string
    (`Assoc
       [
         ("action", `String "auth");
         ("key", `String (Alpaca_paper.Credentials.key_string credentials));
         ("secret", `String (Alpaca_paper.Credentials.secret_string credentials));
       ])

let listen_frame =
  Yojson.Safe.to_string
    (`Assoc
       [
         ("action", `String "listen");
         ("data", `Assoc [ ("streams", `List [ `String "trade_updates" ]) ]);
       ])

(* One connection, handshake to disconnect, folded into the market-data
   client's [Outcome.t] -- for the reason alpaca_ws.ml gives beside that type:
   a refused key and the socket closing behind it arrive together, and the
   refusal is the reading that says what to do.

   [~rest:`Log], as in [Alpaca_paper.get_json]: a job this session started can
   still raise after its verdict is in, and raised to the main monitor that
   would end the process the engine and the desk both run in. Replies go
   through [Pipe.write_if_open] for the same reason: a Ping that arrives after
   a Close would otherwise write to a closed pipe and raise. *)
let run_session ~credentials ~(writer : Venue.Update.t Pipe.Writer.t) ~on_connected
    ~on_event =
  let fatal = ref None in
  let listening = ref false in
  let handshake_error = ref None in
  let%map connection =
    Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
        Alpaca_ws.with_connection stream_uri ~f:(fun ~reader ~writer:net_writer ->
            (* The four pipes, named as alpaca_ws.ml names them: app_to_ws
               carries frames this module writes, ws_to_app frames it reads. *)
            let app_to_ws, send_frame = Pipe.create () in
            let received_frames, ws_to_app = Pipe.create () in
            let initialized = Ivar.create () in
            let send text =
              Pipe.write_if_open send_frame
                (Websocket.Frame.create ~opcode:Websocket.Frame.Opcode.Text ~content:text
                   ())
            in
            let handle (message : Message.t) =
              match message with
              | Message.Authorized ->
                  on_event "authorized; asking to listen to trade_updates";
                  send listen_frame
              | Message.Unauthorized ->
                  (* A key the paper host refuses will be refused again, so this
                     ends the stream rather than a session. *)
                  fatal :=
                    Some
                      (sprintf
                         "the paper host refused the trading key. Check %s and %s; the \
                          trade_updates stream will not reconnect on a key it refuses."
                         Alpaca_paper.Credentials.key_var
                         Alpaca_paper.Credentials.secret_var);
                  Pipe.close send_frame;
                  Deferred.unit
              | Message.Listening streams
                when List.mem streams "trade_updates" ~equal:String.equal ->
                  listening := true;
                  on_event "listening to trade_updates; reconciling with the venue";
                  (* Not awaited: a read loop that waited on a reconciliation
                     would stop answering pings while it ran, and the venue
                     would drop the socket. *)
                  don't_wait_for (on_connected ());
                  Deferred.unit
              | Message.Listening _ -> Deferred.unit
              | Message.Update update ->
                  (* Without pushback, for the same reason: a slow reader of
                     the updates must not stall the socket's replies. *)
                  Pipe.write_without_pushback_if_open writer update;
                  Deferred.unit
              | Message.Stream_error why ->
                  on_event ("stream error: " ^ why);
                  Deferred.unit
              | Message.Other -> Deferred.unit
            in
            (* Frames arrive as Binary on this host, and are read exactly as
               Text is. A frame that is not JSON is skipped, as the market-data
               client skips one: one malformed frame must not end the stream. *)
            let read_loop () =
              Pipe.iter received_frames ~f:(fun (frame : Websocket.Frame.t) ->
                  match frame.Websocket.Frame.opcode with
                  | Websocket.Frame.Opcode.Ping ->
                      Pipe.write_if_open send_frame
                        (Websocket.Frame.create ~opcode:Websocket.Frame.Opcode.Pong
                           ~content:frame.Websocket.Frame.content ())
                  | Websocket.Frame.Opcode.Close ->
                      Pipe.close send_frame;
                      Deferred.unit
                  | Websocket.Frame.Opcode.Text | Websocket.Frame.Opcode.Binary -> (
                      match
                        Option.try_with (fun () ->
                            Yojson.Safe.from_string frame.Websocket.Frame.content)
                      with
                      | Some json -> handle (Message.of_json json)
                      | None -> Deferred.unit)
                  | Websocket.Frame.Opcode.Pong | _ -> Deferred.unit)
            in
            don't_wait_for (read_loop ());
            (* Authentication is the first frame, sent once the upgrade is
               complete: before it, nothing reads the pipe it is written to. *)
            don't_wait_for
              (let%bind () = Ivar.read initialized in
               send (auth_frame credentials));
            match%map
              Websocket_async.client ~initialized ~app_to_ws ~ws_to_app ~net_to_ws:reader
                ~ws_to_net:net_writer stream_uri
            with
            | Ok () -> ()
            | Error error -> handshake_error := Some (Error.to_string_hum error)))
  in
  match !fatal with
  | Some detail -> Alpaca_ws.Outcome.Fatal detail
  | None ->
      let context = if !listening then "" else " before the stream was listening" in
      let reason =
        match (!handshake_error, connection) with
        | Some detail, _ -> detail
        | None, Error exn -> Exn.to_string exn
        | None, Ok () -> "stream closed"
      in
      Alpaca_ws.Outcome.Disconnected (reason ^ context)

(* Reconnects on every disconnect, on the market-data client's schedule, and
   returns only when the paper host refuses the key. The random generator the
   upgrade's nonce is drawn from is seeded first, as [Alpaca_ws.run] seeds it:
   unseeded, every handshake fails from inside the library. *)
let run ~(credentials : Alpaca_paper.Credentials.t)
    ~(writer : Venue.Update.t Pipe.Writer.t) ~(on_connected : unit -> unit Deferred.t)
    ~(on_event : string -> unit) : unit Deferred.t =
  Lazy.force Alpaca_ws.rng_initialized;
  let rec attempt n =
    on_event (sprintf "connecting to %s (attempt %d)" (Uri.to_string stream_uri) n);
    match%bind run_session ~credentials ~writer ~on_connected ~on_event with
    | Alpaca_ws.Outcome.Fatal detail ->
        on_event (sprintf "STOPPING: %s" detail);
        Deferred.unit
    | Alpaca_ws.Outcome.Disconnected reason ->
        (* [attempt] is 1-based, as Backoff counts: the first retry waits the
           base delay. *)
        let delay = Alpaca_ws.Backoff.delay Alpaca_ws.Backoff.default ~attempt:n in
        on_event
          (sprintf "disconnected (%s); reconnecting in %s" reason
             (Time_ns.Span.to_string_hum delay));
        let%bind () = Clock_ns.after delay in
        attempt (n + 1)
  in
  attempt 1
