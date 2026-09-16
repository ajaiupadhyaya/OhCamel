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

   On the demo host each of those routes answers 405, with a sentence saying
   what can be done instead. *)

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
          let origin_is_host =
            match (get "origin", get "host") with
            | Some origin, Some host -> (
                let uri = Uri.of_string origin in
                match Uri.host uri with
                | Some h ->
                    String.equal
                      (String.lowercase (authority h (Uri.port uri)))
                      (String.lowercase host)
                | None -> false)
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

let with_ticket server r ~f =
  match body_json r with
  | None -> respond_error server `Bad_request "the body is not JSON"
  | Some json -> (
      match Ticket.of_json json with
      | Ok ticket -> f ticket
      | Error why -> respond_error server `Bad_request why)

let protected ~host ~f server r =
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
      handle = (fun server _ -> respond server (Oms.tca_json oms));
    };
    {
      Server.path = "/api/desk/sessions";
      purpose = "the newest 250 session closes, oldest first";
      handle = (fun server _ -> respond server (sessions_json (Oms.journal oms)));
    };
    {
      Server.path = "/api/desk/preview";
      purpose =
        "POST a ticket: the rules' and the limits' answer to it, creating nothing (both \
         hosts)";
      handle =
        (fun server r ->
          if not (Poly.equal r.Server.Request.meth `POST) then
            respond_error server `Method_not_allowed "POST a ticket to preview it"
          else
            with_ticket server r ~f:(fun ticket ->
                respond server (Oms.Preview.to_json (Oms.preview oms ticket))));
    };
    {
      Server.path = "/api/desk/orders";
      purpose =
        "POST a ticket: the rules, the limits, the journal, the venue (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            with_ticket server r ~f:(fun ticket ->
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
                        ]))));
    };
    {
      Server.path = "/api/desk/cancel";
      purpose = "POST {client_order_id}: cancel one open order (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            match
              Option.bind (field r "client_order_id") ~f:(function
                | `String s -> Ids.Client_order_id.of_string s
                | _ -> None)
            with
            | None ->
                respond_error server `Bad_request
                  "client_order_id: one of this desk's order ids"
            | Some id -> (
                match%bind Oms.cancel oms id with
                | Ok o ->
                    respond server
                      (`Assoc [ ("state", `String (Order.State.to_string o.Order.state)) ])
                | Error why -> respond_error server `Conflict why));
    };
    {
      Server.path = "/api/desk/kill";
      purpose =
        "POST {why}: halt the desk and cancel every open order; positions are not \
         touched (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            let why =
              match field r "why" with
              | Some (`String s) when not (String.is_empty (String.strip s)) ->
                  String.prefix (String.strip s) 200
              | _ -> "no reason given"
            in
            let%bind () = Oms.kill oms ~why in
            respond server (switch ()));
    };
    {
      Server.path = "/api/desk/kill/reset";
      purpose = {|POST {"confirm":"reset"}: lift the halt (live host only)|};
      handle =
        protected ~host ~f:(fun server r ->
            match field r "confirm" with
            | Some (`String "reset") ->
                Halt.reset (Oms.halt oms);
                Oms.changed oms;
                respond server (switch ())
            | _ ->
                respond_error server `Bad_request
                  {|a reset must say so: {"confirm":"reset"}|});
    };
  ]
