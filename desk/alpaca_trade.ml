(* Alpaca paper's trading half, assembled: the transport and the pure parts
   from Alpaca_paper, the order updates from Trade_updates.

   A module of its own only because Trade_updates depends on Alpaca_paper's
   credentials and host, and this record depends on Trade_updates: defined in
   alpaca_paper.ml it would be a cycle.

   Every request is bounded at 10 s. The order manager runs one job at a
   time, and a request that never answers would otherwise hold every job
   behind it, a kill included. A bound that passes frees the job at once. It
   closes the request's connection if that connection is still connecting or
   has answered; a peer that never sends its status line keeps the socket
   until the peer or the kernel ends it. *)

open Core
open Async

let span = Time_ns.Span.of_sec 10.0

(* Enough of a body to read Alpaca's reason, bounded so a proxy's error page
   does not become the log line. *)
let excerpt body = String.prefix body 120

let json_of ~what body : Yojson.Safe.t Or_error.t =
  match Yojson.Safe.from_string body with
  | json -> Ok json
  | exception Yojson.Json_error msg ->
      Or_error.errorf "alpaca_paper: %s was not JSON: %s" what msg

let trade ~(credentials : Alpaca_paper.Credentials.t)
    ~(on_connected : unit -> unit Deferred.t) ~(on_event : string -> unit) : Venue.Trade.t
    =
  let request ?body meth uri =
    Alpaca_paper.request_json ~span ~meth ?body ~credentials uri
  in
  (* The method and the path, never the query or a header: what an operator
     needs to find the call, and nothing that carries a key. *)
  let unexpected ~what status body =
    Or_error.errorf "alpaca_paper: %s returned %d (%s)" what status (excerpt body)
  in
  (* An [Error] here is the bound passing or the transport raising. Either way
     the order may be at the venue, so it is invariant 10's unknown: looked up
     by its client order id, never sent again. *)
  let submit (r : Order.Request.t) =
    match%map
      request
        ~body:(Yojson.Safe.to_string (Alpaca_paper.order_request_json r))
        `POST
        (Alpaca_paper.trading_uri "/v2/orders")
    with
    | Ok (status, body) -> Alpaca_paper.classify_submission ~status ~body
    | Error e -> Venue.Submission.Unknown (Error.to_string_hum e)
  in
  (* An id [Alpaca_paper.cancel_uri] refuses is an error before anything is
     sent: a malformed id's path could name every order or every position. *)
  let cancel id =
    match Alpaca_paper.cancel_uri id with
    | Error e -> return (Error e)
    | Ok uri -> (
        match%map request `DELETE uri with
        | Error e -> Error e
        | Ok (204, _) -> Ok ()
        | Ok (422, body) -> Or_error.errorf "not cancelable: %s" (excerpt body)
        | Ok (status, body) -> unexpected ~what:("DELETE " ^ Uri.path uri) status body)
  in
  let find_order (id : Ids.Client_order_id.t) =
    let uri =
      Alpaca_paper.trading_uri
        ~query:[ ("client_order_id", [ Ids.Client_order_id.to_string id ]) ]
        "/v2/orders:by_client_order_id"
    in
    let what = "GET " ^ Uri.path uri in
    match%map request `GET uri with
    | Error e -> Error e
    | Ok (200, body) ->
        Or_error.bind (json_of ~what body) ~f:(fun json ->
            Or_error.map (Alpaca_paper.venue_order_of_json json) ~f:Option.some)
    | Ok (404, _) -> Ok None
    | Ok (status, body) -> unexpected ~what status body
  in
  let open_orders () =
    let uri =
      Alpaca_paper.trading_uri
        ~query:[ ("status", [ "open" ]); ("limit", [ "500" ]); ("direction", [ "asc" ]) ]
        "/v2/orders"
    in
    let what = "GET " ^ Uri.path uri in
    match%map request `GET uri with
    | Error e -> Error e
    | Ok (200, body) -> (
        match json_of ~what body with
        | Error e -> Error e
        | Ok (`List items) ->
            (* Only the desk's orders, each read in full; the account's others
               are skipped unread (see [Alpaca_paper.desk_orders]). *)
            Alpaca_paper.desk_orders items
        | Ok _ -> Or_error.errorf "alpaca_paper: %s is not a list" what)
    | Ok (status, body) -> unexpected ~what status body
  in
  let updates, writer = Pipe.create () in
  (* Closed when the stream gives up for good, so Oms.run returns and the engine
     stops the desk. *)
  don't_wait_for
    (let%map () = Trade_updates.run ~credentials ~writer ~on_connected ~on_event in
     Pipe.close writer);
  { Venue.Trade.submit; cancel; find_order; open_orders; updates }
