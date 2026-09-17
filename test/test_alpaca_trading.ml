(* Alpaca paper's trading half, pure parts, against the shapes in Alpaca's
   API reference and streaming guide.

   What matters is where a mistake would cost money in a real account and an
   honest record in a paper one: a quantity or price sent in the wrong form, a
   refusal read as an unknown (and so looked up forever), an unknown read as a
   refusal (and so sent again), and a fill whose position or execution id is
   misread.

   No socket is opened. The transport ([request_json]) and the stream's session
   ([Trade_updates.run]) are exercised by no test here: they are observed on a
   host with paper keys, as A1's read side is on every sync. *)

open Core
open Ohcamel.Types
module Alpaca = Ohcamel_desk.Alpaca_paper
module Updates = Ohcamel_desk.Trade_updates
module Venue = Ohcamel_desk.Venue
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let cid = "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"

let request kind =
  {
    Order.Request.client_order_id = Option.value_exn (Ids.Client_order_id.of_string cid);
    symbol = Symbol.of_string "AAPL";
    side = Order.Side.Buy;
    qty = 2;
    kind;
  }

let test_an_order_is_sent_as_strings () =
  (* Six fields in the order order_request_json writes them: the quantity 2 as
     the string "2", because Alpaca's reference writes quantities as strings;
     "day" because the desk sends day orders only (spec §3.6); and no
     limit_price, because a market order has none. *)
  Alcotest.(check string)
    "market"
    {|{"symbol":"AAPL","qty":"2","side":"buy","type":"market","time_in_force":"day","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"}|}
    (Yojson.Safe.to_string (Alpaca.order_request_json (request Order.Kind.Market)));
  (* The same six, then the limit: 150.0 printed "%.2f" is "150.00", two
     decimals because a limit that is not a whole cent, at any price, was
     refused by the rules' tick before an order reached this function. *)
  Alcotest.(check string)
    "limit, two decimals"
    {|{"symbol":"AAPL","qty":"2","side":"buy","type":"limit","time_in_force":"day","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","limit_price":"150.00"}|}
    (Yojson.Safe.to_string
       (Alpaca.order_request_json (request (Order.Kind.Limit (Price.of_float 150.0)))));
  (* "" would send DELETE /v2/orders/, and ".." and "a/b" would leave the order's own path. *)
  List.iter [ ""; ".."; "a/b" ] ~f:(fun id ->
      match Alpaca.cancel_uri id with
      | Error _ -> ()
      | Ok uri -> Alcotest.failf "cancel id %S was accepted as %s" id (Uri.to_string uri));
  (* A UUID, the shape of every id Alpaca issues, is its own order's path on the paper host. *)
  Alcotest.(check string)
    "a UUID's cancel URI"
    "https://paper-api.alpaca.markets/v2/orders/7b08df51-c1ac-453c-99f9-323a5f075f0d"
    (Uri.to_string
       (Or_error.ok_exn (Alpaca.cancel_uri "7b08df51-c1ac-453c-99f9-323a5f075f0d")))

let accepted_body =
  {|{"asset_class":"us_equity","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","filled_avg_price":null,"filled_qty":"0","id":"7b08df51-c1ac-453c-99f9-323a5f075f0d","limit_price":"150","qty":"2","side":"buy","status":"accepted","symbol":"AAPL","time_in_force":"day","type":"limit"}|}

let test_the_three_answers_to_a_submission () =
  (match Alpaca.classify_submission ~status:200 ~body:accepted_body with
  | Venue.Submission.Accepted o ->
      (* The body's "id", the one the venue cancels by. *)
      Alcotest.(check string)
        "the venue's id" "7b08df51-c1ac-453c-99f9-323a5f075f0d" o.Venue.Venue_order.id;
      (* "150" read as 150.0, compared with no tolerance: 150 is a whole number
         and exact as a double. *)
      Alcotest.(check (option (float 0.0)))
        "the limit" (Some 150.0) o.Venue.Venue_order.limit_price
  | _ -> Alcotest.fail "a 200 with an order was not accepted");
  (match
     Alpaca.classify_submission ~status:403
       ~body:{|{"code":40310000,"message":"insufficient buying power"}|}
   with
  | Venue.Submission.Rejected why ->
      (* "403" and ": " and the body's "message", Alpaca's own words, so the
         person reading the refusal reads the venue's reason. *)
      Alcotest.(check string)
        "a refusal, with Alpaca's words" "403: insufficient buying power" why
  | _ -> Alcotest.fail "a 403 was not a refusal");
  (* 400 a bad request, 409 a conflict, 422 unprocessable, 429 rate limited:
     in each the venue did not take the order, so each is a refusal. *)
  List.iter [ 400; 409; 422; 429 ] ~f:(fun status ->
      match Alpaca.classify_submission ~status ~body:"{}" with
      | Venue.Submission.Rejected _ -> ()
      | _ -> Alcotest.failf "%d was not a refusal" status);
  (* A 5xx can arrive after the venue took the order, and a 200 that cannot be
     read may carry one: each is unknown, looked up and never sent again
     (invariant 10). *)
  List.iter
    [ (500, "{}"); (503, ""); (200, "not json") ]
    ~f:(fun (status, body) ->
      match Alpaca.classify_submission ~status ~body with
      | Venue.Submission.Unknown _ -> ()
      | _ -> Alcotest.failf "%d %S was not an unknown" status body);
  (* Float.of_string reads "1_0" as 10, "0x10" as 16 and "1e5" as 100000, and Alpaca writes none of them. *)
  List.iter [ "1_0"; "0x10"; "1e5" ] ~f:(fun s ->
      match Alpaca.decimal ~what:"order" (`Assoc [ ("qty", `String s) ]) "qty" with
      | Ok x -> Alcotest.failf "%S was read as the number %g" s x
      | Error e ->
          (* The refusal names the field, so the log says which number was malformed. *)
          Alcotest.(check bool)
            (sprintf "%S names order.qty" (Error.to_string_hum e))
            true
            (String.is_substring (Error.to_string_hum e) ~substring:"order.qty"));
  (* JSON, but no readable order in it: the venue said 200, so it may hold the order (invariant 10). *)
  match
    Alpaca.classify_submission ~status:200
      ~body:{|{"id":"7b08df51-c1ac-453c-99f9-323a5f075f0d","status":"accepted"}|}
  with
  | Venue.Submission.Unknown _ -> ()
  | _ -> Alcotest.fail "a JSON 200 whose order cannot be read was not an unknown"

(* An order as trade_updates carries it, with only the fields the parser
   requires, and [extra] spliced in before the closing brace. *)
let order_json ?(extra = "") () =
  sprintf
    {|{"id":"a5be8f5e-fdfa-41f5-a644-7a74fe947a8f","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","symbol":"XOM","side":"sell","qty":"30","filled_qty":"30","status":"filled"%s}|}
    extra

let test_trade_update_messages () =
  let of_string s = Updates.Message.of_json (Yojson.Safe.from_string s) in
  (* The authorization stream's status decides: "authorized" is the only
     answer that lets the listen be sent. *)
  (match
     of_string
       {|{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}|}
   with
  | Updates.Message.Authorized -> ()
  | _ -> Alcotest.fail "authorized");
  (* Any other status is a refusal, and the session stops on it. *)
  (match
     of_string
       {|{"stream":"authorization","data":{"status":"unauthorized","action":"authenticate"}}|}
   with
  | Updates.Message.Unauthorized -> ()
  | _ -> Alcotest.fail "unauthorized");
  (* The one stream the listen asked for, named back. *)
  (match of_string {|{"stream":"listening","data":{"streams":["trade_updates"]}}|} with
  | Updates.Message.Listening [ "trade_updates" ] -> ()
  | _ -> Alcotest.fail "listening");
  (* Yojson reads a bare Infinity as a float; an infinite bid has no mid, so AAPL has no quote. *)
  (match
     Alpaca.quotes_of_json
       (Yojson.Safe.from_string
          {|{"quotes":{"AAPL":{"ap":172.7,"bp":Infinity,"t":"2022-08-17T10:07:40.286587431Z"}}}|})
   with
  | Ok quotes -> (
      match Map.find quotes (Symbol.of_string "AAPL") with
      | Some None -> ()
      | Some (Some q) ->
          Alcotest.failf "an infinite bid became a quote, bid %g"
            (Price.to_float q.Venue.Quote.bid)
      | None -> Alcotest.fail "AAPL was dropped rather than read as having no quote")
  | Error e -> Alcotest.failf "the quotes were refused whole: %s" (Error.to_string_hum e));
  let fill_update ~order ~numbers =
    sprintf {|{"stream":"trade_updates","data":{"event":"fill","order":%s,%s}}|} order
      numbers
  in
  let refused ~field message =
    match of_string message with
    | Updates.Message.Unreadable why ->
        (* The field, so the log line says which number the venue sent wrong. *)
        Alcotest.(check bool)
          (sprintf "%S names %s" why field)
          true
          (String.is_substring why ~substring:field);
        (* The order's id, so the order the lost update belongs to can be found. *)
        Alcotest.(check bool)
          (sprintf "%S names the order" why)
          true
          (String.is_substring why ~substring:"a5be8f5e-fdfa-41f5-a644-7a74fe947a8f")
    | _ -> Alcotest.failf "an update with an unreadable %s was not refused" field
  in
  (* "NaN" parses as a float, and a NaN price would make every cost it touches a NaN. *)
  refused ~field:"trade_update.price"
    (fill_update ~order:(order_json ())
       ~numbers:{|"execution_id":"e1","price":"NaN","qty":"30"|});
  (* "inf" parses as a float too, and an infinite quantity sets no position. *)
  refused ~field:"trade_update.qty"
    (fill_update ~order:(order_json ())
       ~numbers:{|"execution_id":"e1","price":"105.9","qty":"inf"|});
  (* Yojson reads a bare NaN as a float, which the order's parser refuses as it refuses "NaN". *)
  refused ~field:"order.filled_avg_price"
    (fill_update
       ~order:(order_json ~extra:{|,"filled_avg_price":NaN|} ())
       ~numbers:{|"execution_id":"e1","price":"105.9","qty":"30"|});
  (* An order placed by hand for a dollar amount: qty null, and a client order id Alpaca minted. *)
  let notional ~client_order_id =
    sprintf
      {|{"id":"61e69015-8549-4bfd-b9c3-01e75843f47d","client_order_id":"%s","symbol":"AAPL","side":"buy","qty":null,"notional":"500","filled_qty":"0","status":"new"}|}
      client_order_id
  in
  let hand_placed = notional ~client_order_id:"b0b6dd9d-8b9b-48a9-ba46-b9d54906e415" in
  (* Not the desk's, so skipped before its null qty is read, and the desk's order beside it is still read. *)
  (match
     Alpaca.desk_orders
       [ Yojson.Safe.from_string hand_placed; Yojson.Safe.from_string accepted_body ]
   with
  | Ok orders ->
      Alcotest.(check (list string))
        "only the desk's order"
        [ "7b08df51-c1ac-453c-99f9-323a5f075f0d" ]
        (List.map orders ~f:(fun o -> o.Venue.Venue_order.id))
  | Error e ->
      Alcotest.failf "a hand-placed notional order failed the read: %s"
        (Error.to_string_hum e));
  (* The same null qty on an ohc- order fails the whole read: the desk's own orders fail closed. *)
  (match
     Alpaca.desk_orders [ Yojson.Safe.from_string (notional ~client_order_id:cid) ]
   with
  | Error _ -> ()
  | Ok _ -> Alcotest.fail "a desk order with a null qty was read");
  (* Its trade update is ignored as not the desk's before the "NaN" price is read, not called unreadable. *)
  (match
     of_string (fill_update ~order:hand_placed ~numbers:{|"price":"NaN","qty":"1"|})
   with
  | Updates.Message.Not_ours _ -> ()
  | _ ->
      Alcotest.fail
        "an update for an order the desk did not place was not ignored as such");
  (match
     of_string
       (fill_update ~order:(order_json ()) ~numbers:{|"price":"105.9","qty":"30"|})
   with
  | Updates.Message.Update { Venue.Update.fill = Some f; _ } ->
      (* No execution id or timestamp: the order's id and its cumulative "30", the same for a redelivered copy. *)
      Alcotest.(check string)
        "the fallback execution id" "a5be8f5e-fdfa-41f5-a644-7a74fe947a8f:30"
        f.Order.Fill.execution_id
  | _ -> Alcotest.fail "a fill with no execution id or timestamp was not read");
  match
    of_string
      {|{"stream":"trade_updates","data":{"event":"fill","execution_id":"2f63ea93-423d-4169-b3f6-3fdafc10c418","order":{"client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","filled_avg_price":"105.8988475","filled_qty":"30","id":"a5be8f5e-fdfa-41f5-a644-7a74fe947a8f","limit_price":null,"qty":"30","side":"sell","status":"filled","symbol":"XOM","type":"market"},"position_qty":"-30","price":"105.8988475","qty":"30","timestamp":"2022-04-19T17:45:05.024916716Z"}}|}
  with
  | Updates.Message.Update { Venue.Update.event = "fill"; fill = Some f; order; _ } ->
      (* The venue's execution id, the key a replayed fill is recognised by. *)
      Alcotest.(check string)
        "execution id" "2f63ea93-423d-4169-b3f6-3fdafc10c418" f.Order.Fill.execution_id;
      (* "105.8988475" parsed to its nearest double, which is within 2e-14 of
         the decimal; 1e-9 is far above that and far below a cent. *)
      Alcotest.(check (float 1e-9))
        "price" 105.8988475
        (Price.to_float f.Order.Fill.price);
      (* "30", the shares in this execution: a whole number, exact. *)
      Alcotest.(check (float 0.0)) "qty" 30.0 f.Order.Fill.qty;
      (* "-30": the venue's position after the fill, short 30, kept with its
         sign because it is the number the position is set to. *)
      Alcotest.(check (option (float 0.0)))
        "the venue's position, signed" (Some (-30.0)) f.Order.Fill.position_qty;
      (* The order's "side" is "sell", read into the order's side. *)
      Alcotest.(check bool)
        "a sell" true
        (Order.Side.equal order.Venue.Venue_order.side Order.Side.Sell)
  | _ -> Alcotest.fail "a fill update was not read as one"

let suite =
  ( "alpaca_trading",
    [
      Alcotest.test_case "an order is sent as strings" `Quick
        test_an_order_is_sent_as_strings;
      Alcotest.test_case "the three answers to a submission" `Quick
        test_the_three_answers_to_a_submission;
      Alcotest.test_case "trade update messages" `Quick test_trade_update_messages;
    ] )
