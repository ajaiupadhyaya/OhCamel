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
     decimals because a sub-penny limit above a dollar was refused by the rules
     before an order reached this function (Rule 612). *)
  Alcotest.(check string)
    "limit, two decimals"
    {|{"symbol":"AAPL","qty":"2","side":"buy","type":"limit","time_in_force":"day","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","limit_price":"150.00"}|}
    (Yojson.Safe.to_string
       (Alpaca.order_request_json (request (Order.Kind.Limit (Price.of_float 150.0)))))

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
  (* 422 is a request the venue could not process and 429 a request it rate
     limited: in both it did not take the order, so each is a refusal. *)
  List.iter [ 422; 429 ] ~f:(fun status ->
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
      | _ -> Alcotest.failf "%d %S was not an unknown" status body)

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
