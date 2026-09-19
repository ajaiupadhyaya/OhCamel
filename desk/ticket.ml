(* A ticket: what a person or a strategy asks the desk to trade, before the
   desk has given it an id or a price.

   Read from the JSON the page posts. The first thing that cannot be read is
   the error -- unlike the rules, which report everything: a ticket that is
   not a ticket has one problem, and the field is the useful half of saying
   so. A quantity of zero or less IS read. Whether it is a positive whole
   number is the whole_shares rule's to say, beside every other rule. *)

open Core
open Ohcamel.Types

type t = { symbol : Symbol.t; side : Order.Side.t; qty : int; kind : Order.Kind.t }

let to_request (t : t) ~(client_order_id : Ids.Client_order_id.t) : Order.Request.t =
  {
    Order.Request.client_order_id;
    symbol = t.symbol;
    side = t.side;
    qty = t.qty;
    kind = t.kind;
    (* A ticket is a person's order, and a person's order is a day order. *)
    tif = Order.Tif.Day;
  }

let of_json (json : Yojson.Safe.t) : (t, string) Result.t =
  let open Result.Let_syntax in
  let%bind fields =
    match json with `Assoc fields -> Ok fields | _ -> Error "a ticket is a JSON object"
  in
  let find key = List.Assoc.find fields key ~equal:String.equal in
  let%bind symbol =
    match find "symbol" with
    | Some (`String s) when not (String.is_empty (String.strip s)) ->
        Ok (Symbol.of_string (String.uppercase (String.strip s)))
    | _ -> Error "symbol: a ticker, as a string"
  in
  let%bind side =
    match find "side" with
    | Some (`String s) ->
        Result.of_option (Order.Side.of_string s) ~error:"side: buy or sell"
    | _ -> Error "side: buy or sell"
  in
  let%bind qty =
    match find "qty" with
    | Some (`Int n) -> Ok n
    | Some (`Float f) when Float.is_integer f && Float.( < ) (Float.abs f) 1e9 ->
        Ok (Float.to_int f)
    | _ -> Error "qty: a whole number of shares"
  in
  let number = function
    | Some (`Int n) -> Some (Float.of_int n)
    | Some (`Float f) -> Some f
    | _ -> None
  in
  let%map kind =
    match (find "type", number (find "limit_price")) with
    | (None | Some (`String "market")), None -> Ok Order.Kind.Market
    | (None | Some (`String "market")), Some _ ->
        Error "limit_price: only a limit order has one"
    | Some (`String "limit"), Some p when Float.is_finite p && Float.( > ) p 0.0 ->
        Ok (Order.Kind.Limit (Price.of_float p))
    | Some (`String "limit"), _ ->
        Error "limit_price: a limit order needs a positive price"
    | _ -> Error "type: market or limit"
  in
  { symbol; side; qty; kind }
