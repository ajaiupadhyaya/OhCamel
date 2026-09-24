(* A ticket, as the page posts it. A quantity of zero or less is read, not
   refused: whole_shares is a rule, and the rules report it with the rest. *)

open Core
open Ohcamel.Types
module Ticket = Ohcamel_desk.Ticket
module Order = Ohcamel_desk.Order

let parse s = Ticket.of_json (Yojson.Safe.from_string s)

let test_a_ticket_reads_as_the_page_sends_it () =
  (match parse {|{"symbol":" aapl ","side":"buy","qty":10}|} with
  | Ok t ->
      Alcotest.(check string)
        "symbol, trimmed and upper case" "AAPL"
        (Symbol.to_string t.Ticket.symbol);
      Alcotest.(check int) "qty" 10 t.Ticket.qty;
      Alcotest.(check bool)
        "market when no type is given" true
        (Order.Kind.equal t.Ticket.kind Order.Kind.Market)
  | Error e -> Alcotest.fail e);
  (match
     parse
       {|{"symbol":"MSFT","side":"sell","qty":5.0,"type":"limit","limit_price":301.25}|}
   with
  | Ok t ->
      Alcotest.(check bool) "a sell" true (Order.Side.equal t.Ticket.side Order.Side.Sell);
      Alcotest.(check int) "5.0 is five shares" 5 t.Ticket.qty;
      Alcotest.(check (option (float 0.0)))
        "the limit" (Some 301.25)
        (Option.map (Order.Kind.limit_price t.Ticket.kind) ~f:Price.to_float)
  | Error e -> Alcotest.fail e);
  match parse {|{"symbol":"XOM","side":"buy","qty":0}|} with
  | Ok t -> Alcotest.(check int) "zero is read, for the rules to refuse" 0 t.Ticket.qty
  | Error e -> Alcotest.fail e

let test_what_cannot_be_read_is_named () =
  List.iter
    [
      ({|[1,2]|}, "a ticket is a JSON object");
      ({|{"side":"buy","qty":1}|}, "symbol: a ticker, as a string");
      ({|{"symbol":"AAPL","side":"short","qty":1}|}, "side: buy or sell");
      ({|{"symbol":"AAPL","side":"buy","qty":1.5}|}, "qty: a whole number of shares");
      ( {|{"symbol":"AAPL","side":"buy","qty":1,"type":"limit"}|},
        "limit_price: a limit order needs a positive price" );
      ( {|{"symbol":"AAPL","side":"buy","qty":1,"limit_price":150}|},
        "limit_price: only a limit order has one" );
      ({|{"symbol":"AAPL","side":"buy","qty":1,"type":"stop"}|}, "type: market or limit");
    ]
    ~f:(fun (body, expected) ->
      match parse body with
      | Ok _ -> Alcotest.failf "%s was read as a ticket" body
      | Error e -> Alcotest.(check string) body expected e)

let suite =
  ( "ticket",
    [
      Alcotest.test_case "a ticket reads as the page sends it" `Quick
        test_a_ticket_reads_as_the_page_sends_it;
      Alcotest.test_case "what cannot be read is named" `Quick
        test_what_cannot_be_read_is_named;
    ] )
