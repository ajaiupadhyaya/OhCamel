(* The desk, read side: a book taken from a venue, and a page that says what
   the desk is, what it could not do, and what it could not hold.

   The venue's answers are handed to [sync_with] directly -- the Deferred
   around them is a single bind and no test here starts a scheduler -- so every
   case is a set of answers and what the desk must say afterwards. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Server = Ohcamel.Server
module Config = Ohcamel.Config
module Desk = Ohcamel_desk.Desk
module Journal = Ohcamel_desk.Journal
module Venue = Ohcamel_desk.Venue

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let tsla = Symbol.of_string "TSLA"
let at = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let minimal_book extra =
  sprintf
    "((cash 1000000.0) (positions (((symbol AAPL) (sector TECH) (qty 0.0)))) (limits ()) \
     %s)"
    extra

let test_the_desk_block_parses_defaults_to_no_trading_and_refuses_nonsense () =
  let plain = Or_error.ok_exn (Config.Book.of_string (minimal_book "")) in
  Alcotest.(check bool)
    "absent means trading disabled" true
    (Poly.equal plain.Config.Book.desk.Config.Book.Desk_spec.trading
       Config.Book.Desk_spec.Disabled);
  Alcotest.(check (float 0.0))
    "and the default order cap" 25_000.0
    plain.Config.Book.desk.Config.Book.Desk_spec.max_order_notional;
  let given =
    Or_error.ok_exn
      (Config.Book.of_string
         (minimal_book
            "(desk ((trading enabled) (max_order_notional 10000.0) (spread_bps ((SPY \
             2.0)))))"))
  in
  let d = given.Config.Book.desk in
  Alcotest.(check bool)
    "enabled" true
    (Poly.equal d.Config.Book.Desk_spec.trading Config.Book.Desk_spec.Enabled);
  Alcotest.(check (float 0.0))
    "the cap given" 10_000.0 d.Config.Book.Desk_spec.max_order_notional;
  Alcotest.(check (float 0.0))
    "the collar defaulted" 0.05 d.Config.Book.Desk_spec.price_collar;
  Alcotest.(check (list (pair string (float 0.0))))
    "the spread table"
    [ ("SPY", 2.0) ]
    d.Config.Book.Desk_spec.spread_bps;
  match Config.Book.of_string (minimal_book "(desk ((price_collar 1.5)))") with
  | Ok _ -> Alcotest.fail "a 150% price collar was accepted"
  | Error e ->
      Alcotest.(check bool)
        "the error names the field" true
        (String.is_substring (Error.to_string_hum e) ~substring:"price_collar")

let account ~cash ~equity =
  {
    Venue.Account.equity = Notional.of_float equity;
    cash = Notional.of_float cash;
    buying_power = Notional.of_float cash;
    last_equity = Some (Notional.of_float (equity -. 250.0));
    status = "ACTIVE";
    trading_blocked = false;
    shorting_enabled = true;
  }

let position symbol qty =
  {
    Venue.Position.symbol;
    asset_class = "us_equity";
    qty = Qty.of_float qty;
    avg_entry_price = None;
    market_value = None;
  }

let with_desk ?(venue = `Sim) ~f () =
  let tech = Sector.of_string "TECH" in
  let graph =
    Graph.create
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = tech };
          { Instrument.symbol = msft; sector = tech };
        ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Graph.set_price graph aapl (Price.of_float 150.0);
  Graph.set_price graph msft (Price.of_float 300.0);
  Graph.stabilize graph;
  let journal = Or_error.ok_exn (Journal.open_ ~path:":memory:") in
  let venue =
    match venue with
    | `Sim ->
        Desk.Reads
          (Ohcamel_desk.Sim_venue.read
             (Ohcamel_desk.Sim_venue.create ~opened_at:at
                ~marks:(fun _ -> None)
                ~now:(fun () -> at)
                ~half_spread_bps:(fun _ -> 5.0)
                ~cash:Notional.zero ~positions:[] ()))
    | `Unavailable reason -> Desk.Unavailable { name = "alpaca-paper"; reason }
  in
  let desk =
    Desk.create ~graph ~journal ~venue ~spec:Config.Book.Desk_spec.default
      ~on_change:(fun () -> ())
  in
  Exn.protect
    ~f:(fun () -> f desk graph journal)
    ~finally:(fun () ->
      Journal.close journal;
      Graph.destroy graph)

let field json key = Yojson.Safe.Util.member key json

let test_a_sync_sets_the_book_and_names_what_it_cannot_hold () =
  with_desk
    ~f:(fun desk graph _ ->
      Desk.sync_with desk
        ~account:(Ok (account ~cash:90_000.0 ~equity:91_500.0))
        ~positions:(Ok [ position aapl 10.0; position tsla 5.0 ])
        ~at;
      Alcotest.(check (float 0.0))
        "AAPL 10 in the graph" 10.0
        (Qty.to_float (Graph.qty graph aapl));
      let s = Desk.summary_json desk in
      Alcotest.(check int)
        "one unmanaged" 1
        (Yojson.Safe.Util.to_int (field s "unmanaged"));
      Alcotest.(check (float 1e-9))
        "equity from the venue" 91_500.0
        (Yojson.Safe.Util.to_number (field s "equity"));
      (* equity 91,500 against last_equity 91,250 *)
      Alcotest.(check (float 1e-9))
        "session P&L" 250.0
        (Yojson.Safe.Util.to_number (field s "session_pnl"));
      let b = Desk.body_json desk in
      (* graph: 90,000 + 10 x 150 = 91,500; venue 91,500 *)
      Alcotest.(check (float 1e-9))
        "the ledgers agree" 0.0
        (Yojson.Safe.Util.to_number (field b "equity_gap"));
      Alcotest.(check (list string))
        "TSLA named" [ "TSLA" ]
        (List.map
           (Yojson.Safe.Util.to_list (field b "unmanaged_positions"))
           ~f:(fun p -> Yojson.Safe.Util.(to_string (member "symbol" p))));
      (* A managed position is one in the book's universe (design §3.3):
         AAPL and MSFT, in symbol order -- TSLA, held by the venue but
         outside the universe, is unmanaged and does not appear here. *)
      let positions = Yojson.Safe.Util.to_list (field b "positions") in
      Alcotest.(check (list string))
        "the universe, in symbol order" [ "AAPL"; "MSFT" ]
        (List.map positions ~f:(fun p -> Yojson.Safe.Util.(to_string (member "symbol" p))));
      let aapl_row = List.nth_exn positions 0 and msft_row = List.nth_exn positions 1 in
      Alcotest.(check (float 1e-9))
        "AAPL venue_qty: what the venue reported" 10.0
        (Yojson.Safe.Util.to_number (Yojson.Safe.Util.member "venue_qty" aapl_row));
      Alcotest.(check (float 1e-9))
        "AAPL graph_qty: what the sync wrote" 10.0
        (Yojson.Safe.Util.to_number (Yojson.Safe.Util.member "graph_qty" aapl_row));
      Alcotest.(check bool)
        "MSFT venue_qty is null: the venue holds none" true
        (Poly.equal (Yojson.Safe.Util.member "venue_qty" msft_row) `Null);
      Alcotest.(check (float 0.0))
        "MSFT graph_qty is 0: the sync zeroed a universe name the venue does not hold" 0.0
        (Yojson.Safe.Util.to_number (Yojson.Safe.Util.member "graph_qty" msft_row)))
    ()

let test_a_failed_read_keeps_the_last_good_account_and_says_what_failed () =
  with_desk
    ~f:(fun desk graph _ ->
      Desk.sync_with desk
        ~account:(Ok (account ~cash:90_000.0 ~equity:91_500.0))
        ~positions:(Ok [ position aapl 10.0 ])
        ~at;
      Desk.sync_with desk
        ~account:(Error (Error.of_string "GET /v2/account timed out"))
        ~positions:(Ok []) ~at;
      let s = Desk.summary_json desk in
      Alcotest.(check (float 1e-9))
        "the last good equity stands" 91_500.0
        (Yojson.Safe.Util.to_number (field s "equity"));
      Alcotest.(check string)
        "the failure is said" "GET /v2/account timed out"
        (Yojson.Safe.Util.to_string (field s "last_error"));
      Alcotest.(check (float 0.0))
        "and the book was not emptied by a failed read" 10.0
        (Qty.to_float (Graph.qty graph aapl)))
    ()

let test_a_desk_without_a_venue_says_why () =
  with_desk ~venue:(`Unavailable "the trading key does not begin PK")
    ~f:(fun desk _ _ ->
      let s = Desk.summary_json desk in
      Alcotest.(check string)
        "disabled" "disabled"
        (Yojson.Safe.Util.to_string (field s "status"));
      Alcotest.(check string)
        "why" "the trading key does not begin PK"
        (Yojson.Safe.Util.to_string (field s "reason"));
      Alcotest.(check bool)
        "no equity it does not know" true
        (Poly.equal (field s "equity") `Null))
    ()

let test_the_frame's_desk_object_has_exactly_these_keys () =
  with_desk
    ~f:(fun desk _ _ ->
      Alcotest.(check (list string))
        "thirteen, in order"
        [
          "status";
          "reason";
          "venue";
          "trading";
          "journal";
          "version";
          "equity";
          "cash";
          "session_pnl";
          "unmanaged";
          "sessions";
          "last_sync";
          "last_error";
        ]
        (Yojson.Safe.Util.keys (Desk.summary_json desk)))
    ()

let test_api_desk_answers_through_the_server () =
  with_desk
    ~f:(fun desk graph _ ->
      let server =
        Server.create ~extensions:(Desk.extensions desk) ~mode:`Demo ~graph
          ~factor:"SYNTHETIC" ()
      in
      match
        Async.Deferred.peek
          (Server.dispatch server
             {
               Server.Request.meth = `GET;
               path = "/api/desk";
               headers = Cohttp.Header.init ();
               body = "";
             })
      with
      | Some (response, `String body) ->
          Alcotest.(check int)
            "200" 200
            (Cohttp.Code.code_of_status (Cohttp.Response.status response));
          Alcotest.(check string)
            "the simulated venue" "simulated"
            Yojson.Safe.Util.(to_string (member "venue" (Yojson.Safe.from_string body)))
      | _ ->
          Alcotest.fail
            "/api/desk did not answer with a string body without the scheduler")
    ()

(* Two syncs overlap: the first, whose read started at 14:00, times out and
   answers late; the minute sync, whose read started at 14:01, answers first.
   The late answer is the older book and must not land on the newer one. *)
let test_a_read_that_started_before_the_last_applied_one_changes_nothing () =
  with_desk
    ~f:(fun desk graph _ ->
      (* 14:00:00 + 60 s = 14:01:00 *)
      let newer = Time_ns.add at (Time_ns.Span.of_sec 60.0) in
      (* graph equity: 80,000 + 20 x 150 = 83,000, given as the venue's too *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:80_000.0 ~equity:83_000.0))
        ~positions:(Ok [ position aapl 20.0 ])
        ~at:newer;
      (* 14:00:00 is strictly before 14:01:00, the last applied read *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:90_000.0 ~equity:91_500.0))
        ~positions:(Ok [ position aapl 10.0 ])
        ~at;
      (* the newer read's 20 stands; the older read's 10 was not applied *)
      Alcotest.(check (float 0.0))
        "AAPL is still the newer read's 20" 20.0
        (Qty.to_float (Graph.qty graph aapl));
      let s = Desk.summary_json desk in
      (* the newer read's 80,000 stands; the older read's 90,000 was not applied *)
      Alcotest.(check (float 1e-9))
        "cash is still the newer read's" 80_000.0
        (Yojson.Safe.Util.to_number (field s "cash"));
      (* 14:01:00, the newer read's start, printed by Desk_time.rfc3339 *)
      Alcotest.(check string)
        "last_sync is still the newer read's start" "2026-09-14T14:01:00.000000000Z"
        (Yojson.Safe.Util.to_string (field s "last_sync")))
    ()

let suite =
  ( "desk",
    [
      Alcotest.test_case "the desk block parses, defaults to no trading, refuses nonsense"
        `Quick test_the_desk_block_parses_defaults_to_no_trading_and_refuses_nonsense;
      Alcotest.test_case "a sync sets the book and names what it cannot hold" `Quick
        test_a_sync_sets_the_book_and_names_what_it_cannot_hold;
      Alcotest.test_case "a failed read keeps the last good account and says what failed"
        `Quick test_a_failed_read_keeps_the_last_good_account_and_says_what_failed;
      Alcotest.test_case "a desk without a venue says why" `Quick
        test_a_desk_without_a_venue_says_why;
      Alcotest.test_case "the frame's desk object has exactly these keys" `Quick
        test_the_frame's_desk_object_has_exactly_these_keys;
      Alcotest.test_case "/api/desk answers through the server" `Quick
        test_api_desk_answers_through_the_server;
      Alcotest.test_case "a read that started before the last applied one changes nothing"
        `Quick test_a_read_that_started_before_the_last_applied_one_changes_nothing;
    ] )
