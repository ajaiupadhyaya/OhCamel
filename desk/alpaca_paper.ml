(* Alpaca's paper account: the read side, and the trading half's pure parts and
   transport (assembled with the order-update stream in alpaca_trade.ml).

   INVARIANT 9 LIVES HERE. The trading host is a constant, and the only
   function that builds a trading URI puts that constant in it. There is no
   argument, environment variable or book field that names a host, so there is
   no configuration mistake that sends this desk to the live-money endpoint --
   the mistake would have to be an edit to this line, in review.

   And a key is checked before it is used: Alpaca's paper keys begin PK and its
   live keys AK. A live key would be refused by the paper host anyway; refusing
   it here means the refusal is ours, is stated in words, and happens before a
   request carrying a live-money credential has left the machine.

   Money arrives as decimal strings ("123346.11") and leaves as Notional.t.
   A field that will not parse is an error naming the field, never a zero.

   The data host is separate and is the one the risk engine already uses for
   bars; quotes for an order's arrival price come from it too. *)

open Core
open Async
open Ohcamel.Types
module Secret = Ohcamel.Config.Secret

let name = "alpaca-paper"
let trading_host = "paper-api.alpaca.markets"
let data_host = "data.alpaca.markets"
let trading_uri ?query path = Uri.make ~scheme:"https" ~host:trading_host ~path ?query ()
let data_uri ?query path = Uri.make ~scheme:"https" ~host:data_host ~path ?query ()

module Credentials = struct
  type t = { key : Secret.t; secret : Secret.t }

  let key_var = "ALPACA_TRADING_API_KEY"
  let secret_var = "ALPACA_TRADING_SECRET_KEY"
  let paper_prefix = "PK"

  let present = function
    | Some v when not (String.is_empty (String.strip v)) -> Some (String.strip v)
    | _ -> None

  let choose ~(data : Ohcamel.Config.Credentials.t) ~trading_key ~trading_secret :
      t Or_error.t =
    let open Or_error.Let_syntax in
    let%bind t =
      match (present trading_key, present trading_secret) with
      | Some k, Some s -> Ok { key = Secret.of_string k; secret = Secret.of_string s }
      | None, None ->
          Ok
            {
              key = data.Ohcamel.Config.Credentials.alpaca_key;
              secret = data.Ohcamel.Config.Credentials.alpaca_secret;
            }
      | Some _, None | None, Some _ ->
          Or_error.errorf
            "%s and %s must be set together, or neither (the data keys are then used)"
            key_var secret_var
    in
    if String.is_prefix (Secret.to_string t.key) ~prefix:paper_prefix then Ok t
    else
      Or_error.errorf
        "the trading key does not begin %s, so it is not a paper key; this desk trades \
         Alpaca paper only (design invariant 9). Set %s and %s to a paper key pair."
        paper_prefix key_var secret_var

  let load ~data =
    choose ~data ~trading_key:(Sys.getenv key_var) ~trading_secret:(Sys.getenv secret_var)

  let headers t =
    Cohttp.Header.of_list
      [
        ("APCA-API-KEY-ID", Secret.to_string t.key);
        ("APCA-API-SECRET-KEY", Secret.to_string t.secret);
      ]

  (* The trade_updates stream authenticates in a frame, not in headers, so the
     two strings leave [Secret] here as well, each at one call site a grep for
     its name finds. Neither belongs in an error or a log line. *)
  let key_string t = Secret.to_string t.key
  let secret_string t = Secret.to_string t.secret
end

(* ---------------------------------------------------------------------- *)
(* Parsers                                                                  *)
(* ---------------------------------------------------------------------- *)

let field (json : Yojson.Safe.t) key =
  match json with
  | `Assoc fields -> List.Assoc.find fields key ~equal:String.equal
  | _ -> None

(* A plain decimal and nothing else: an optional "-", one or more digits, and
   an optional "." followed by one or more digits, which is how Alpaca writes
   every price and quantity. Float.of_string reads far more -- "1_0" as 10,
   "0x10" as 16, "1e5", "nan", "inf" -- and a field holding one of those holds
   something other than the number it names. *)
let is_plain_decimal (s : string) : bool =
  let digits part =
    (not (String.is_empty part)) && String.for_all part ~f:Char.is_digit
  in
  let unsigned = Option.value (String.chop_prefix s ~prefix:"-") ~default:s in
  match String.lsplit2 unsigned ~on:'.' with
  | None -> digits unsigned
  | Some (whole, fraction) -> digits whole && digits fraction

let decimal ~what (json : Yojson.Safe.t) key : float Or_error.t =
  match field json key with
  | Some (`String s) -> (
      (* Finite as well as plain: four hundred digits are a plain decimal and
         an infinite float. *)
      match if is_plain_decimal s then Float.of_string_opt s else None with
      | Some x when Float.is_finite x -> Ok x
      | _ -> Or_error.errorf "alpaca_paper: %s.%s is not a number: %S" what key s)
  (* Yojson reads a bare NaN or Infinity as a float, so the float is held to
     the rule the string is: a NaN that got past here would make every limit
     comparison it reached false. *)
  | Some (`Float x) when Float.is_finite x -> Ok x
  | Some (`Float x) ->
      Or_error.errorf "alpaca_paper: %s.%s is not a finite number: %g" what key x
  | Some (`Int n) -> Ok (Float.of_int n)
  | _ -> Or_error.errorf "alpaca_paper: %s.%s is missing" what key

let optional_decimal ~what json key =
  match field json key with
  | None | Some `Null -> Ok None
  | Some _ -> Or_error.map (decimal ~what json key) ~f:Option.some

let string_field ~what json key =
  match field json key with
  | Some (`String s) -> Ok s
  | _ -> Or_error.errorf "alpaca_paper: %s.%s is missing" what key

let bool_field json key ~default =
  match field json key with Some (`Bool b) -> b | _ -> default

let time_field ~what json key =
  let open Or_error.Let_syntax in
  let%bind s = string_field ~what json key in
  match Desk_time.parse s with
  | Some t -> Ok (t, s)
  | None -> Or_error.errorf "alpaca_paper: %s.%s is not a timestamp: %S" what key s

let account_of_json (json : Yojson.Safe.t) : Venue.Account.t Or_error.t =
  let open Or_error.Let_syntax in
  let what = "account" in
  let%bind equity = decimal ~what json "equity" in
  let%bind cash = decimal ~what json "cash" in
  let%bind buying_power = decimal ~what json "buying_power" in
  let%bind last_equity = optional_decimal ~what json "last_equity" in
  let%map status = string_field ~what json "status" in
  {
    Venue.Account.equity = Notional.of_float equity;
    cash = Notional.of_float cash;
    buying_power = Notional.of_float buying_power;
    last_equity = Option.map last_equity ~f:Notional.of_float;
    status;
    trading_blocked =
      bool_field json "trading_blocked" ~default:true
      || bool_field json "account_blocked" ~default:false;
    shorting_enabled = bool_field json "shorting_enabled" ~default:false;
  }

(* The side decides the sign. Alpaca reports a short's quantity as negative,
   and a parser that trusted the sign alone would be one vendor change from
   reading every short as long. *)
let position_of_json (json : Yojson.Safe.t) : Venue.Position.t Or_error.t =
  let open Or_error.Let_syntax in
  let what = "position" in
  let%bind symbol = string_field ~what json "symbol" in
  let%bind side = string_field ~what json "side" in
  let%bind qty = decimal ~what json "qty" in
  let%bind avg = optional_decimal ~what json "avg_entry_price" in
  let%bind market_value = optional_decimal ~what json "market_value" in
  let%bind sign =
    match side with
    | "long" -> Ok 1.0
    | "short" -> Ok (-1.0)
    | other -> Or_error.errorf "alpaca_paper: position %s has side %S" symbol other
  in
  return
    {
      Venue.Position.symbol = Symbol.of_string symbol;
      asset_class =
        Option.value
          (Result.ok (string_field ~what json "asset_class"))
          ~default:"us_equity";
      qty = Qty.of_float (sign *. Float.abs qty);
      avg_entry_price = Option.map avg ~f:Price.of_float;
      market_value = Option.map market_value ~f:Notional.of_float;
    }

let positions_of_json = function
  | `List items -> Or_error.all (List.map items ~f:position_of_json)
  | _ -> Or_error.error_string "alpaca_paper: positions is not a list"

let clock_of_json (json : Yojson.Safe.t) : Venue.Session_clock.t Or_error.t =
  let open Or_error.Let_syntax in
  let what = "clock" in
  let%bind now, _ = time_field ~what json "timestamp" in
  let%bind next_open, _ = time_field ~what json "next_open" in
  let%bind next_close, close_text = time_field ~what json "next_close" in
  let%bind next_close_date =
    match Desk_time.local_date close_text with
    | Some d -> Ok d
    | None -> Or_error.errorf "alpaca_paper: clock.next_close has no date: %S" close_text
  in
  let%map is_open =
    match field json "is_open" with
    | Some (`Bool b) -> Ok b
    | _ -> Or_error.error_string "alpaca_paper: clock.is_open is missing"
  in
  { Venue.Session_clock.now; is_open; next_open; next_close; next_close_date }

(* A bare NaN or Infinity is a float to Yojson, and is read here as the field
   being absent, which the callers already make None: a quote with an infinite
   side has no mid, and a bar with an infinite field is no bar. It never
   becomes a price. *)
let number = function
  | `Float x when Float.is_finite x -> Some x
  | `Int n -> Some (Float.of_int n)
  | _ -> None

(* A price of 0 is Alpaca saying "no active bid" or "no active ask". A quote
   missing a side has no mid, and an arrival price taken from half a quote
   would be a number with no meaning, so such a symbol maps to None. *)
let quotes_of_json (json : Yojson.Safe.t) : Venue.Quote.t option Symbol.Map.t Or_error.t =
  match field json "quotes" with
  | Some (`Assoc per_symbol) ->
      Ok
        (Symbol.Map.of_alist_reduce
           ~f:(fun a _ -> a)
           (List.map per_symbol ~f:(fun (symbol, q) ->
                let quote =
                  match
                    ( Option.bind (field q "bp") ~f:number,
                      Option.bind (field q "ap") ~f:number,
                      Option.bind (field q "t") ~f:(function
                        | `String s -> Desk_time.parse s
                        | _ -> None) )
                  with
                  | Some bid, Some ask, Some at
                    when Float.( > ) bid 0.0 && Float.( > ) ask 0.0 ->
                      Some
                        {
                          Venue.Quote.bid = Price.of_float bid;
                          ask = Price.of_float ask;
                          at;
                        }
                  | _ -> None
                in
                (Symbol.of_string symbol, quote))))
  | _ -> Or_error.error_string "alpaca_paper: no quotes object"

let bar_of_json (json : Yojson.Safe.t) : Venue.Bar.t option =
  let n key = Option.bind (field json key) ~f:number in
  match (field json "t", n "o", n "h", n "l", n "c", n "v") with
  | Some (`String t), Some open_, Some high, Some low, Some close, Some volume -> (
      match Desk_time.local_date t with
      | Some date -> Some { Venue.Bar.date; open_; high; low; close; volume }
      | None -> None)
  | _ -> None

let bars_of_json (json : Yojson.Safe.t) :
    (Venue.Bar.t list Symbol.Map.t * string option) Or_error.t =
  match field json "bars" with
  | Some (`Assoc per_symbol) ->
      let bars =
        Symbol.Map.of_alist_reduce ~f:( @ )
          (List.map per_symbol ~f:(fun (symbol, items) ->
               ( Symbol.of_string symbol,
                 match items with
                 | `List xs -> List.filter_map xs ~f:bar_of_json
                 | _ -> [] )))
      in
      let token =
        match field json "next_page_token" with Some (`String s) -> Some s | _ -> None
      in
      Ok (bars, token)
  | _ -> Or_error.error_string "alpaca_paper: no bars object"

(* ---------------------------------------------------------------------- *)
(* The trading half: pure parts                                             *)
(* ---------------------------------------------------------------------- *)

(* Quantities and prices go as strings, as Alpaca's reference writes them. A
   limit price goes with two decimals: the rules' tick refused any limit that
   is not a whole cent, at every price, before this was reached, so formatting
   here rounds nothing. time_in_force is the request's own: "day" for every
   ticket, "opg" for a rebalance's market-on-open order, which Alpaca queues
   for the next opening auction when it arrives after 19:00 ET. *)
let order_request_json (r : Order.Request.t) : Yojson.Safe.t =
  `Assoc
    ([
       ("symbol", `String (Symbol.to_string r.Order.Request.symbol));
       ("qty", `String (Int.to_string r.Order.Request.qty));
       ("side", `String (Order.Side.to_string r.Order.Request.side));
       ("type", `String (Order.Kind.to_string r.Order.Request.kind));
       ("time_in_force", `String (Order.Tif.to_string r.Order.Request.tif));
       ( "client_order_id",
         `String (Ids.Client_order_id.to_string r.Order.Request.client_order_id) );
     ]
    @ Option.value_map (Order.Kind.limit_price r.Order.Request.kind) ~default:[]
        ~f:(fun p -> [ ("limit_price", `String (sprintf "%.2f" (Price.to_float p))) ]))

let venue_order_of_json (json : Yojson.Safe.t) : Venue.Venue_order.t Or_error.t =
  let open Or_error.Let_syntax in
  let what = "order" in
  let%bind id = string_field ~what json "id" in
  let%bind client_order_id = string_field ~what json "client_order_id" in
  let%bind symbol = string_field ~what json "symbol" in
  let%bind side_text = string_field ~what json "side" in
  let%bind side =
    match Order.Side.of_string side_text with
    | Some s -> Ok s
    | None -> Or_error.errorf "alpaca_paper: order %s has side %S" id side_text
  in
  let%bind qty = decimal ~what json "qty" in
  let%bind filled_qty = decimal ~what json "filled_qty" in
  let%bind filled_avg_price = optional_decimal ~what json "filled_avg_price" in
  let%bind limit_price = optional_decimal ~what json "limit_price" in
  let%map status = string_field ~what json "status" in
  {
    Venue.Venue_order.id;
    client_order_id;
    symbol = Symbol.of_string symbol;
    side;
    qty;
    filled_qty;
    filled_avg_price;
    status;
    limit_price;
  }

(* Whether an order, as the venue sends it, is one this desk placed: its
   client_order_id begins with the prefix every id the desk mints carries
   (Ids.Client_order_id). Read before anything else in the order, because the
   paper account can also hold orders placed by hand in Alpaca's own
   interface, and one of those -- a notional order, whose qty is null -- must
   not make the desk's own orders unreadable. Only the prefix is checked: an
   order carrying it is the desk's whatever else is wrong with it, so reading
   it fails closed. *)
let is_desk_order (json : Yojson.Safe.t) : bool =
  match field json "client_order_id" with
  | Some (`String id) -> String.is_prefix id ~prefix:Ids.Client_order_id.prefix
  | _ -> false

(* The desk's orders out of a venue's list, each read in full, the others
   skipped unread. One of the desk's own that cannot be read fails the whole
   list: a reconciliation that quietly lost one of its own orders would
   compare the journal with a venue that is missing it. *)
let desk_orders (items : Yojson.Safe.t list) : Venue.Venue_order.t list Or_error.t =
  Or_error.all
    (List.filter_map items ~f:(fun order ->
         if is_desk_order order then Some (venue_order_of_json order) else None))

(* A refusal is a status the venue uses to say "I did not take this order" --
   the request was bad, the account cannot afford it, it was rate limited.
   Anything else that is not a readable 200 is Unknown: a 5xx can arrive after
   the order was accepted, and treating it as a refusal would invite the one
   thing invariant 10 forbids, sending the order again. *)
let classify_submission ~(status : int) ~(body : string) : Venue.Submission.t =
  let message () =
    match Option.try_with (fun () -> Yojson.Safe.from_string body) with
    | Some json -> (
        match field json "message" with
        | Some (`String m) -> m
        | _ -> String.prefix body 120)
    | None -> String.prefix body 120
  in
  match status with
  | 200 -> (
      match Option.try_with (fun () -> Yojson.Safe.from_string body) with
      | None -> Venue.Submission.Unknown "200 with a body that is not JSON"
      | Some json -> (
          match venue_order_of_json json with
          | Ok o -> Venue.Submission.Accepted o
          | Error e ->
              Venue.Submission.Unknown
                ("200 with an unreadable order: " ^ Error.to_string_hum e)))
  | 400 | 401 | 403 | 404 | 409 | 422 | 429 ->
      Venue.Submission.Rejected (sprintf "%d: %s" status (message ()))
  | other -> Venue.Submission.Unknown (sprintf "%d: %s" other (message ()))

(* A venue order id goes into a DELETE's path, so it is checked before a path
   is built. Uri.make escapes "?", "#" and spaces and nothing else: an empty id
   would send DELETE /v2/orders/, and "../positions" DELETE
   /v2/orders/../positions -- cancel every open order, or close every
   position, wherever the host or a proxy normalizes the path. Alpaca's ids
   are UUIDs, so a non-empty run of letters, digits and hyphens admits every
   id it issues and none that can leave its own order's path. *)
let cancel_uri (id : string) : Uri.t Or_error.t =
  if
    (not (String.is_empty id))
    && String.for_all id ~f:(fun c -> Char.is_alphanum c || Char.equal c '-')
  then Ok (trading_uri ("/v2/orders/" ^ id))
  else
    Or_error.errorf
      "alpaca_paper: a venue order id must be a non-empty run of letters, digits and \
       hyphens; %S is not, so no cancel was sent"
      id

(* ---------------------------------------------------------------------- *)
(* Transport                                                                *)
(* ---------------------------------------------------------------------- *)

(* Every request to either host is bounded, reading its body included. One
   that never answers -- a connection held open by something between here and
   Alpaca -- would otherwise park the minute sync or the session close on a
   single Deferred for the life of the process, with no error to log and
   nothing to retry.

   [f] is handed [abandon], filled when the bound passes. An abandoned request
   is not cancelled by being ignored: its connection stays open until something
   closes it, and a desk that abandoned one a minute would leak a socket a
   minute.

   [time_source] is the wall clock everywhere but the test that pins the bound,
   which advances a clock of its own, so no test waits on the wall's. *)
let request_timeout = Time_ns.Span.of_sec 30.0

let within ?(time_source = Time_source.wall_clock ()) ~(span : Time_ns.Span.t)
    ~(what : string) (f : abandon:unit Deferred.t -> 'a Or_error.t Deferred.t) :
    'a Or_error.t Deferred.t =
  let abandoned = Ivar.create () in
  match%map
    Time_source.with_timeout time_source span (f ~abandon:(Ivar.read abandoned))
  with
  | `Result r -> r
  | `Timeout ->
      Ivar.fill_if_empty abandoned ();
      Or_error.errorf "alpaca_paper: %s did not answer within %s" what
        (Time_ns.Span.to_string_hum span)

(* The URI carries no credential -- the keys go in headers -- so its path is
   safe to name in an error.

   cohttp closes a client connection in only two ways, so [abandon] goes to
   both: [~interrupt] aborts a connect still in progress, which is all
   Tcp.connect does with it, and closing the body's pipe is what closes a
   connection that has answered. A connection that opened and never sent its
   status line is out of reach of either, and lasts until the peer or the
   kernel ends it. [~rest:`Log] because an abandoned request can still raise
   after its answer was thrown away, and raised to the main monitor that would
   end the process this bound exists to keep running. *)
let get_json ~credentials (uri : Uri.t) : Yojson.Safe.t Or_error.t Deferred.t =
  let path = Uri.path uri in
  within ~span:request_timeout ~what:("GET " ^ path) (fun ~abandon ->
      match%map
        Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
            let%bind response, body =
              Cohttp_async.Client.get ~interrupt:abandon
                ~headers:(Credentials.headers credentials)
                uri
            in
            upon abandon (fun () ->
                match body with `Pipe pipe -> Pipe.close_read pipe | _ -> ());
            let%map body = Cohttp_async.Body.to_string body in
            (Cohttp.Response.status response, body))
      with
      | Error exn ->
          Or_error.errorf "alpaca_paper: GET %s failed: %s" path (Exn.to_string exn)
      | Ok (`OK, body) -> (
          match Yojson.Safe.from_string body with
          | json -> Ok json
          | exception Yojson.Json_error msg ->
              Or_error.errorf "alpaca_paper: GET %s was not JSON: %s" path msg)
      | Ok (status, body) ->
          Or_error.errorf "alpaca_paper: GET %s returned %s (%s)" path
            (Cohttp.Code.string_of_status status)
            (String.prefix body 200))

(* The trading half's transport: any method, a body when there is one, and the
   answer as its status code and text, for the caller to judge.

   Bounded by [within], and closed as [get_json] is, because cohttp's one-shot
   calls close a connection in only two ways: [~interrupt] aborts a connect
   still in progress, and closing the body's pipe closes a connection that has
   answered. A connection that opened and never sent its status line is out of
   reach of both, and lasts until the peer or the kernel ends it.
   [Client.Connection] reaches it no better: closing one kills its sequencer,
   and a killed sequencer cleans a connection only once the request holding it
   returns (async_kernel's throttle.ml, [kill] and [start_job]).

   [~rest:`Log], as in [get_json]: an abandoned request can still raise after
   its answer was thrown away, and raised to the main monitor that would end
   the process this bound exists to keep running.

   The URI carries no credential -- the keys go in headers -- so its path is
   safe to name in an error. *)
let request_json ?(span = request_timeout) ~(meth : Cohttp.Code.meth)
    ?(body : string option) ~(credentials : Credentials.t) (uri : Uri.t) :
    (int * string) Or_error.t Deferred.t =
  let what = Cohttp.Code.string_of_method meth ^ " " ^ Uri.path uri in
  within ~span ~what (fun ~abandon ->
      match%map
        Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
            let headers =
              match body with
              | None -> Credentials.headers credentials
              | Some _ ->
                  Cohttp.Header.add
                    (Credentials.headers credentials)
                    "Content-Type" "application/json"
            in
            let%bind response, answer =
              Cohttp_async.Client.call ~interrupt:abandon ~headers ~chunked:false
                ?body:(Option.map body ~f:Cohttp_async.Body.of_string)
                meth uri
            in
            upon abandon (fun () ->
                match answer with `Pipe pipe -> Pipe.close_read pipe | _ -> ());
            let%map text = Cohttp_async.Body.to_string answer in
            (Cohttp.Code.code_of_status (Cohttp.Response.status response), text))
      with
      | Error exn ->
          Or_error.errorf "alpaca_paper: %s failed: %s" what (Exn.to_string exn)
      | Ok answer -> Ok answer)

let read ~(credentials : Credentials.t) ~(feed : string) : Venue.Read.t =
  let get uri parse =
    Deferred.Or_error.bind (get_json ~credentials uri) ~f:(fun j -> return (parse j))
  in
  let daily_bars symbols ~days =
    (* Enough calendar days to cover [days] sessions with holidays to spare;
       pages are followed until the token runs out. *)
    let start =
      Date.to_string
        (Date.add_days (Date.today ~zone:Time_float.Zone.utc) (-((days * 2) + 10)))
    in
    let rec page token acc =
      let query =
        [
          ("symbols", [ String.concat ~sep:"," (List.map symbols ~f:Symbol.to_string) ]);
          ("timeframe", [ "1Day" ]);
          ("start", [ start ]);
          ("adjustment", [ "all" ]);
          ("feed", [ feed ]);
          ("limit", [ "10000" ]);
        ]
        @ Option.value_map token ~default:[] ~f:(fun t -> [ ("page_token", [ t ]) ])
      in
      match%bind get (data_uri ~query "/v2/stocks/bars") bars_of_json with
      | Error _ as e -> return e
      | Ok (bars, next) -> (
          let acc = Map.merge_skewed acc bars ~combine:(fun ~key:_ a b -> a @ b) in
          match next with Some t -> page (Some t) acc | None -> return (Ok acc))
    in
    Deferred.Or_error.map (page None Symbol.Map.empty) ~f:(fun m ->
        Map.map m ~f:(fun bars -> List.take (List.rev bars) days |> List.rev))
  in
  {
    Venue.Read.name;
    account = (fun () -> get (trading_uri "/v2/account") account_of_json);
    positions = (fun () -> get (trading_uri "/v2/positions") positions_of_json);
    clock = (fun () -> get (trading_uri "/v2/clock") clock_of_json);
    latest_quote =
      (fun symbol ->
        Deferred.Or_error.map
          (get
             (data_uri
                ~query:[ ("symbols", [ Symbol.to_string symbol ]); ("feed", [ feed ]) ]
                "/v2/stocks/quotes/latest")
             quotes_of_json)
          ~f:(fun qs -> Option.join (Map.find qs symbol)));
    daily_bars;
  }
