(* The desk's routes, dispatched through the server with no scheduler: every
   answer below is determined before it is returned, because the paths tested
   here -- protection, a preview, a malformed ticket, a reset -- wait on
   nothing. The routes that wait on a venue are the async suite's. *)

open Core
open Ohcamel.Types
module Server = Ohcamel.Server
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk

let aapl = Symbol.of_string "AAPL"

let request ?(meth = `POST) ?(headers = []) ?(body = "") path =
  { Server.Request.meth; path; headers = Cohttp.Header.of_list headers; body }

let from_this_site = [ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "same-origin") ]

let dispatched server (r : Server.Request.t) =
  match Async.Deferred.peek (Server.dispatch server r) with
  | None -> Alcotest.failf "%s did not answer without the scheduler" r.Server.Request.path
  | Some (response, body) ->
      let text =
        match body with
        | `String s -> s
        | `Empty -> ""
        | `Strings ss -> String.concat ss
        | `Pipe _ -> Alcotest.failf "%s answered with a pipe" r.Server.Request.path
      in
      (Cohttp.Code.code_of_status (Cohttp.Response.status response), text)

let with_routes ~(host : D.Desk_routes.host) ~f =
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_returns graph aapl
        [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 150.0; time = Time.now () };
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun _ -> Some (Price.of_float 150.0))
          ~now:Time_ns.now
          ~half_spread_bps:(fun _ -> 5.0)
          ~cash:(Notional.of_float 1_000_000.0)
          ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:(Poly.equal host `Live) ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
          ~now:Time_ns.now ~rng:(Random.State.make [| 11 |]) ~on_change:ignore
          ~on_event:ignore ~after_fill:ignore
          ~book_is_current:(fun () -> true)
          ()
      in
      D.Oms.set_market oms ~session_open:true ~adv20:[];
      let server =
        Server.create
          ~extensions:(D.Desk_routes.extensions ~host ~oms)
          ~mode:host ~graph ~factor:"SYNTHETIC" ()
      in
      f server oms)

let test_who_may_change_the_desk () =
  let check what expected ~host r =
    let got =
      match D.Desk_routes.Protection.check ~host r with
      | Ok () -> 200
      | Error (status, _) -> Cohttp.Code.code_of_status status
    in
    Alcotest.(check int) what expected got
  in
  let p = "/api/desk/orders" in
  check "the demo: 405, whatever the request says" 405 ~host:`Demo
    (request ~headers:from_this_site p);
  check "live, a GET: 405" 405 ~host:`Live (request ~meth:`GET ~headers:from_this_site p);
  check "live, no header: 403" 403 ~host:`Live
    (request ~headers:[ ("Sec-Fetch-Site", "same-origin") ] p);
  check "live, the header with another value: 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "yes"); ("Sec-Fetch-Site", "same-origin") ] p);
  check "live, the header and same-origin, any case: allowed" 200 ~host:`Live
    (request ~headers:[ ("x-ohcamel-desk", "1"); ("sec-fetch-site", "same-origin") ] p);
  check "live, the header and an Origin that is the Host: allowed" 200 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "https://live.example.com");
           ("Host", "live.example.com");
         ]
       p);
  check "live, with a port on both: allowed" 200 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "http://localhost:8099");
           ("Host", "localhost:8099");
         ]
       p);
  check "live, a foreign Origin: 403" 403 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "https://elsewhere.example");
           ("Host", "live.example.com");
         ]
       p);
  check "live, cross-site: 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "cross-site") ] p)

let test_the_demo_refuses_orders_and_answers_previews () =
  with_routes ~host:`Demo ~f:(fun server _ ->
      let body = {|{"symbol":"AAPL","side":"buy","qty":10}|} in
      let code, text = dispatched server (request ~body "/api/desk/orders") in
      Alcotest.(check int) "an order: 405" 405 code;
      Alcotest.(check bool)
        "with a sentence pointing at the preview" true
        (String.is_substring text ~substring:"/api/desk/preview");
      let code, text = dispatched server (request ~body "/api/desk/preview") in
      Alcotest.(check int) "a preview: 200" 200 code;
      let json = Yojson.Safe.from_string text in
      (* 10 x 150 = 1,500: every rule passes, and a book with no limits has
         nothing to breach. *)
      Alcotest.(check bool)
        "passed" true
        Yojson.Safe.Util.(to_bool (member "passed" json));
      Alcotest.(check (float 1e-9))
        "priced at the mark" 150.0
        Yojson.Safe.Util.(to_number (member "decision_price" json)))

let test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field () =
  with_routes ~host:`Demo ~f:(fun server _ ->
      let code, text =
        dispatched server
          (request ~body:{|{"symbol":"AAPL","side":"sideways","qty":10}|}
             "/api/desk/preview")
      in
      Alcotest.(check int) "400" 400 code;
      Alcotest.(check string) "the field" {|{"error":"side: buy or sell"}|} text;
      let code, _ = dispatched server (request ~body:"not json" "/api/desk/preview") in
      Alcotest.(check int) "not JSON: 400" 400 code;
      let code, _ = dispatched server (request ~meth:`GET "/api/desk/preview") in
      Alcotest.(check int) "a GET: 405" 405 code)

let test_a_reset_must_say_so () =
  with_routes ~host:`Live ~f:(fun server oms ->
      let halt = D.Oms.halt oms in
      D.Halt.halt halt ~why:"a test" ~at:(Time_ns.now ());
      let code, _ =
        dispatched server
          (request ~headers:from_this_site ~body:"{}" "/api/desk/kill/reset")
      in
      Alcotest.(check int) "no confirmation: 400" 400 code;
      Alcotest.(check string)
        "still halted" "halted"
        (D.Halt.State.name (D.Halt.state halt));
      let code, _ =
        dispatched server (request ~body:{|{"confirm":"reset"}|} "/api/desk/kill/reset")
      in
      Alcotest.(check int) "confirmed, but not from this site: 403" 403 code;
      let code, text =
        dispatched server
          (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|}
             "/api/desk/kill/reset")
      in
      Alcotest.(check int) "confirmed: 200" 200 code;
      Alcotest.(check string)
        "clear" "clear"
        Yojson.Safe.Util.(to_string (member "state" (Yojson.Safe.from_string text))))

let suite =
  ( "desk_routes",
    [
      Alcotest.test_case "who may change the desk" `Quick test_who_may_change_the_desk;
      Alcotest.test_case "the demo refuses orders and answers previews" `Quick
        test_the_demo_refuses_orders_and_answers_previews;
      Alcotest.test_case "a ticket that cannot be read is a 400 naming the field" `Quick
        test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field;
      Alcotest.test_case "a reset must say so" `Quick test_a_reset_must_say_so;
    ] )
