(* Alpaca paper's read side, against payloads copied from Alpaca's own API
   reference. No socket is opened: the parsers are pure, and the transport is
   four lines that the live host exercises.

   What matters here is not the JSON library. It is the four places a paper
   account's numbers can be misread: money that arrives as a string, a short
   whose sign lives in [side], a one-sided quote with no mid, and a clock
   whose date belongs to New York. And one refusal: a key that is not a paper
   key never leaves this module. *)

open Core
open Ohcamel.Types
module Alpaca = Ohcamel_desk.Alpaca_paper
module Venue = Ohcamel_desk.Venue
module Config = Ohcamel.Config

let json = Yojson.Safe.from_string

let test_the_account_arrives_as_strings_and_leaves_as_money () =
  let a =
    Or_error.ok_exn
      (Alpaca.account_of_json
         (json
            {|{"account_blocked":false,"buying_power":"245432.61","cash":"122086.5","equity":"123346.11",
               "last_equity":"122011.09751111286868","shorting_enabled":true,"status":"ACTIVE",
               "trading_blocked":false}|}))
  in
  Alcotest.(check (float 1e-6))
    "equity" 123_346.11
    (Notional.to_float a.Venue.Account.equity);
  Alcotest.(check (float 1e-6)) "cash" 122_086.5 (Notional.to_float a.Venue.Account.cash);
  Alcotest.(check (float 1e-6))
    "buying power" 245_432.61
    (Notional.to_float a.Venue.Account.buying_power);
  Alcotest.(check (option (float 1e-6)))
    "last equity" (Some 122_011.09751111286868)
    (Option.map a.Venue.Account.last_equity ~f:Notional.to_float);
  Alcotest.(check string) "status" "ACTIVE" a.Venue.Account.status;
  Alcotest.(check bool) "trading not blocked" false a.Venue.Account.trading_blocked

let test_a_short_is_negative_whatever_sign_its_quantity_carries () =
  let ps =
    Or_error.ok_exn
      (Alpaca.positions_of_json
         (json
            {|[{"symbol":"AAPL","asset_class":"us_equity","qty":"5","side":"long","avg_entry_price":"100.0","market_value":"600.0"},
               {"symbol":"XOM","asset_class":"us_equity","qty":"-20","side":"short","avg_entry_price":"105.0","market_value":"-2000.0"},
               {"symbol":"CVX","asset_class":"us_equity","qty":"30","side":"short","avg_entry_price":"140.0","market_value":"-4200.0"}]|}))
  in
  (* The sign is the side's: XOM's -20 and CVX's 30 are both short. *)
  Alcotest.(check (list string))
    "long 5, short 20, short 30"
    [ "AAPL 5"; "XOM -20"; "CVX -30" ]
    (List.map ps ~f:(fun p ->
         sprintf "%s %g"
           (Symbol.to_string p.Venue.Position.symbol)
           (Qty.to_float p.Venue.Position.qty)))

let test_the_clock's_date_is_new_york's () =
  let c =
    Or_error.ok_exn
      (Alpaca.clock_of_json
         (json
            {|{"is_open":true,"next_close":"2025-06-24T16:00:00-04:00","next_open":"2025-06-25T09:30:00-04:00","timestamp":"2025-06-24T14:15:22-04:00"}|}))
  in
  (* 16:00 at -04:00 is 20:00Z; the session is the 24th wherever this runs. *)
  Alcotest.(check string)
    "next close in UTC" "2025-06-24T20:00:00.000000000Z"
    (Ohcamel_desk.Desk_time.rfc3339 c.Venue.Session_clock.next_close);
  Alcotest.(check string)
    "the exchange's date" "2025-06-24"
    (Date.to_string c.Venue.Session_clock.next_close_date);
  Alcotest.(check bool) "open" true c.Venue.Session_clock.is_open

let test_a_one_sided_quote_has_no_mid () =
  let qs =
    Or_error.ok_exn
      (Alpaca.quotes_of_json
         (json
            {|{"quotes":{"AAPL":{"ap":172.7,"as":1,"bp":172.62,"bs":2,"t":"2022-08-17T10:07:40.286587431Z"},
                         "TSLA":{"ap":0,"as":0,"bp":911.3,"bs":1,"t":"2022-08-17T10:07:49.387064037Z"}}}|}))
  in
  let aapl = Option.value_exn (Map.find_exn qs (Symbol.of_string "AAPL")) in
  (* (172.62 + 172.70) / 2 = 172.66 *)
  Alcotest.(check (float 1e-9)) "AAPL mid" 172.66 (Venue.Quote.mid aapl);
  Alcotest.(check bool)
    "TSLA's ask of 0 means no ask, so no quote" true
    (Option.is_none (Map.find_exn qs (Symbol.of_string "TSLA")))

let test_daily_bars_carry_their_dates_and_the_page_token () =
  let bars, token =
    Or_error.ok_exn
      (Alpaca.bars_of_json
         (json
            {|{"bars":{"SPY":[{"t":"2026-09-10T04:00:00Z","o":650.1,"h":655.0,"l":648.2,"c":654.3,"v":61234567,"n":1,"vw":652.0},
                              {"t":"2026-09-11T04:00:00Z","o":654.0,"h":657.5,"l":651.0,"c":656.9,"v":58000000,"n":1,"vw":655.0}]},
               "next_page_token":"abc"}|}))
  in
  Alcotest.(check (list string))
    "two sessions, dated by their UTC prefix"
    [ "2026-09-10 654.3"; "2026-09-11 656.9" ]
    (List.map
       (Map.find_exn bars (Symbol.of_string "SPY"))
       ~f:(fun b -> sprintf "%s %g" (Date.to_string b.Venue.Bar.date) b.Venue.Bar.close));
  Alcotest.(check (option string)) "the token" (Some "abc") token

let data_credentials key =
  (* Config.Credentials.t has no constructor; build it the way Config does, from strings. *)
  {
    Config.Credentials.alpaca_key = Config.Secret.of_string key;
    alpaca_secret = Config.Secret.of_string "data-secret";
    fred_api_key = Config.Secret.of_string "fred";
  }

let test_only_a_paper_key_can_trade_and_the_error_never_echoes_it () =
  (match
     Alpaca.Credentials.choose
       ~data:(data_credentials "PKDATA123")
       ~trading_key:None ~trading_secret:None
   with
  | Ok _ -> ()
  | Error e -> Alcotest.failf "a PK data key was refused: %s" (Error.to_string_hum e));
  (match
     Alpaca.Credentials.choose
       ~data:(data_credentials "AKLIVE999SECRETISH")
       ~trading_key:None ~trading_secret:None
   with
  | Ok _ -> Alcotest.fail "a live (AK) key was accepted for trading"
  | Error e ->
      let msg = Error.to_string_hum e in
      Alcotest.(check bool)
        "names the rule" true
        (String.is_substring msg ~substring:"PK");
      Alcotest.(check bool)
        "never the key" false
        (String.is_substring msg ~substring:"AKLIVE999SECRETISH"));
  (match
     Alpaca.Credentials.choose ~data:(data_credentials "AKLIVE")
       ~trading_key:(Some "PKTRADE") ~trading_secret:(Some "s")
   with
  | Ok _ -> ()
  | Error e ->
      Alcotest.failf "separate paper trading keys were refused: %s"
        (Error.to_string_hum e));
  match
    Alpaca.Credentials.choose ~data:(data_credentials "PKDATA")
      ~trading_key:(Some "PKTRADE") ~trading_secret:None
  with
  | Ok _ -> Alcotest.fail "a trading key without its secret was accepted"
  | Error e ->
      Alcotest.(check bool)
        "names both variables" true
        (String.is_substring (Error.to_string_hum e)
           ~substring:"ALPACA_TRADING_SECRET_KEY")

let test_the_trading_host_is_the_paper_host () =
  Alcotest.(check (option string))
    "trading" (Some "paper-api.alpaca.markets")
    (Uri.host (Alpaca.trading_uri "/v2/account"));
  Alcotest.(check (option string))
    "data" (Some "data.alpaca.markets")
    (Uri.host (Alpaca.data_uri "/v2/stocks/bars"))

let suite =
  ( "alpaca_paper",
    [
      Alcotest.test_case "the account arrives as strings and leaves as money" `Quick
        test_the_account_arrives_as_strings_and_leaves_as_money;
      Alcotest.test_case "a short is negative whatever sign its quantity carries" `Quick
        test_a_short_is_negative_whatever_sign_its_quantity_carries;
      Alcotest.test_case "the clock's date is New York's" `Quick
        test_the_clock's_date_is_new_york's;
      Alcotest.test_case "a one-sided quote has no mid" `Quick
        test_a_one_sided_quote_has_no_mid;
      Alcotest.test_case "daily bars carry their dates and the page token" `Quick
        test_daily_bars_carry_their_dates_and_the_page_token;
      Alcotest.test_case "only a paper key can trade, and the error never echoes it"
        `Quick test_only_a_paper_key_can_trade_and_the_error_never_echoes_it;
      Alcotest.test_case "the trading host is the paper host" `Quick
        test_the_trading_host_is_the_paper_host;
    ] )
