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

(* Shared by [dispatched] (through the server's own table) and, in fix round
   2, a handler looked up from a DIFFERENT extension list and called on this
   server directly -- an extension takes the server as its own argument
   (lib/server.ml's [extension.handle]), so no mismatched [Server.t] needs
   building to test what a mismatched [host] does. *)
let answer_of path (d : Cohttp_async.Server.response Async.Deferred.t) =
  match Async.Deferred.peek d with
  | None -> Alcotest.failf "%s did not answer without the scheduler" path
  | Some (response, body) ->
      let text =
        match body with
        | `String s -> s
        | `Empty -> ""
        | `Strings ss -> String.concat ss
        | `Pipe _ -> Alcotest.failf "%s answered with a pipe" path
      in
      (Cohttp.Code.code_of_status (Cohttp.Response.status response), text)

let dispatched server (r : Server.Request.t) =
  answer_of r.Server.Request.path (Server.dispatch server r)

let find_handle extensions path =
  match
    List.find extensions ~f:(fun (e : Server.extension) ->
        String.equal e.Server.path path)
  with
  | Some e -> e.Server.handle
  | None -> Alcotest.failf "no extension for %s" path

let with_routes ?(on_event = ignore) ~(host : D.Desk_routes.host) ~f =
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
          ~now:Time_ns.now ~rng:(Random.State.make [| 11 |]) ~on_change:ignore ~on_event
          ~after_fill:ignore
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
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "cross-site") ] p);
  (* Fix round 1, M2: hostile edges the opus security review asked to be
     pinned down as tests, not left to a one-off probe. *)
  check "live, same-site (not same-origin): 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "same-site") ] p);
  check "live, Origin: null: 403" 403 ~host:`Live
    (request
       ~headers:
         [ ("X-OhCamel-Desk", "1"); ("Origin", "null"); ("Host", "live.example.com") ]
       p);
  check "live, a look-alike suffix host: 403" 403 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "https://live.example.com.evil.com");
           ("Host", "live.example.com");
         ]
       p);
  check "live, a port mismatch: 403" 403 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "http://localhost:8099");
           ("Host", "localhost:9000");
         ]
       p);
  check "live, an Origin with no Host: 403" 403 ~host:`Live
    (request
       ~headers:[ ("X-OhCamel-Desk", "1"); ("Origin", "https://live.example.com") ]
       p);
  check "live, a non-http(s) scheme: 403" 403 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "ftp://live.example.com");
           ("Host", "live.example.com");
         ]
       p);
  (* Fix round 2, minor: the two inputs M2 actually named, each pinning one
     of the two checks on its own -- "null" and "ftp://..." above both fail
     the scheme check already, so neither would notice the host check being
     deleted, and vice versa. The empty host is sent with an empty Host
     header: an empty Origin host equals an empty Host, so only the
     non-empty-host guard refuses it. Against a non-empty Host the two would
     differ, and the case would pass with the guard deleted. *)
  check "live, Origin: https:// and an empty Host (both hosts empty): 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Origin", "https://"); ("Host", "") ] p);
  check "live, Origin: //live.example.com (a host, no scheme): 403" 403 ~host:`Live
    (request
       ~headers:
         [
           ("X-OhCamel-Desk", "1");
           ("Origin", "//live.example.com");
           ("Host", "live.example.com");
         ]
       p)

let test_the_demo_refuses_orders_and_answers_previews () =
  let events = ref [] in
  with_routes ~host:`Demo
    ~on_event:(fun line -> events := line :: !events)
    ~f:(fun server oms ->
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
        Yojson.Safe.Util.(to_number (member "decision_price" json));
      (* Fix round 1: the demo's 405 through dispatch, for every mutating
         route, not only /orders (Protection.check's Demo branch is
         unconditional, but this exercises it through [protected] and the
         actual dispatch table). *)
      let code, _ =
        dispatched server
          (request ~body:{|{"client_order_id":"ohc-1"}|} "/api/desk/cancel")
      in
      Alcotest.(check int) "a cancel: 405" 405 code;
      let code, _ =
        dispatched server (request ~body:{|{"why":"test"}|} "/api/desk/kill")
      in
      Alcotest.(check int) "a kill: 405" 405 code;
      let code, _ =
        dispatched server (request ~body:{|{"confirm":"reset"}|} "/api/desk/kill/reset")
      in
      Alcotest.(check int) "a reset: 405" 405 code;
      (* Fix round 2, Open 1 (M5): [extension.handle] takes the server as
         its own argument (lib/server.ml), so a mismatched Server.t is not
         needed to test a mismatched host -- a Live-built extension list,
         called directly on THIS Demo server, is exactly the wiring mistake
         [protected]'s host check exists to catch. The check must run before
         Protection.check's own Live logic, or a same-site, correctly
         headered Live request would be let through onto the public demo. *)
      let live_ext = D.Desk_routes.extensions ~host:`Live ~oms in
      let live_handle path = find_handle live_ext path in
      let code, _ =
        answer_of "/api/desk/kill/reset"
          (live_handle "/api/desk/kill/reset" server
             (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|}
                "/api/desk/kill/reset"))
      in
      Alcotest.(check int)
        "a Live-built reset handler, called on this Demo server: 403, not 200" 403 code;
      let code, _ =
        answer_of "/api/desk/kill"
          (live_handle "/api/desk/kill" server
             (request ~headers:from_this_site ~body:{|{"why":"test"}|} "/api/desk/kill"))
      in
      Alcotest.(check int)
        "a Live-built kill handler, called on this Demo server: 403, not 200" 403 code;
      Alcotest.(check string)
        "the desk was never halted -- Oms.kill was never reached" "clear"
        (D.Halt.State.name (D.Halt.state (D.Oms.halt oms)));
      Alcotest.(check bool)
        "the mismatch is logged, not only answered with a 403" true
        (List.exists !events ~f:(fun line ->
             String.is_substring line ~substring:"believing it is on"));
      (* Fix round 2, Open 2: the dispatch check added in round 1 ran on an
         empty journal and expected [], which any limit would answer -- it
         tested that the route dispatches, not that it is bounded. 251
         sessions, day i = first + i for i = 0..250, oldest to newest;
         written newest first (test_desk.ml's own shape for this exact
         query, test_desk.ml:441-452) so the order checked is the query's,
         not the insertion's. *)
      let first = Date.of_string "2025-01-01" in
      let at = Time_ns.now () in
      List.iter
        (List.rev (List.init 251 ~f:Fn.id))
        ~f:(fun i ->
          D.Journal.record_session (D.Oms.journal oms)
            {
              D.Journal.Session.date = Date.add_days first i;
              equity_close = 100_000.0 +. Float.of_int i;
              cash_close = 0.0;
              gross_close = 0.0;
              net_close = 0.0;
              recorded_at = at;
            });
      let code, text = dispatched server (request ~meth:`GET "/api/desk/sessions") in
      Alcotest.(check int) "sessions: 200" 200 code;
      let shown = Yojson.Safe.Util.to_list (Yojson.Safe.from_string text) in
      (* 251 recorded, 250 shown: day 0 -- the single oldest -- is dropped. *)
      Alcotest.(check int)
        "251 recorded, but the route is bounded at 250" 250 (List.length shown);
      let date_of s = Yojson.Safe.Util.(to_string (member "date" s)) in
      Alcotest.(check string)
        "the oldest of the 250 kept is day 1's (day 0 dropped)"
        (Date.to_string (Date.add_days first 1))
        (date_of (List.hd_exn shown));
      Alcotest.(check string)
        "the newest is day 250's"
        (Date.to_string (Date.add_days first 250))
        (date_of (List.last_exn shown));
      (* Fix round 1, I1: a raised exception inside a handler answers a
         fixed sentence -- never Exn.to_string, never a closed socket -- and
         the desk keeps running. Closing the journal makes the very next
         read raise ("A DATABASE ERROR RAISES", desk/journal.ml's own
         header comment; empirically Sqlite3.Error "... called with closed
         database"). *)
      D.Journal.close (D.Oms.journal oms);
      let code, text = dispatched server (request ~meth:`GET "/api/desk/tca") in
      Alcotest.(check int) "a journal failure still answers, not a closed socket" 500 code;
      Alcotest.(check string)
        "the fixed sentence, never the exception's own text"
        {|{"error":"the desk hit an internal error and could not answer this request"}|}
        text)

let test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field () =
  with_routes ~host:`Demo ~on_event:ignore ~f:(fun server _ ->
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
  with_routes ~host:`Live ~on_event:ignore ~f:(fun server oms ->
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
      (* Fix round 1: a refused reset must not have reset anything -- the
         403 above comes from Protection.check, before guard_sync's body
         (and Halt.reset) ever runs. *)
      Alcotest.(check string)
        "still halted after the 403" "halted"
        (D.Halt.State.name (D.Halt.state halt));
      let code, text =
        dispatched server
          (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|}
             "/api/desk/kill/reset")
      in
      Alcotest.(check int) "confirmed: 200" 200 code;
      Alcotest.(check string)
        "clear" "clear"
        Yojson.Safe.Util.(to_string (member "state" (Yojson.Safe.from_string text))));
  (* Fix round 2, minor: kill/reset's own [guard_sync] must answer the
     mutation sentence, not the read one, because [Halt.reset] runs before
     the event line that is made to raise here -- by the time the guard
     answers 500 the switch is already clear, so "nothing was sent" would be
     false about the one thing this route did. [on_event] raises only once,
     so [log_exn]'s own call (after the catch) still succeeds and the guard
     can answer normally instead of a second, uncaught raise. *)
  let boomed = ref false in
  with_routes ~host:`Live
    ~on_event:(fun _ ->
      if not !boomed then (
        boomed := true;
        failwith "boom"))
    ~f:(fun server oms ->
      let halt = D.Oms.halt oms in
      (* Halted first, through the switch itself and not [Oms.kill], whose own
         event line would spend the one raise: a switch that was never halted
         reads clear whether or not the reset ran, and the last check below
         could not tell. *)
      D.Halt.halt halt ~why:"a test" ~at:(Time_ns.now ());
      Alcotest.(check string)
        "halted before the reset" "halted"
        (D.Halt.State.name (D.Halt.state halt));
      let code, text =
        dispatched server
          (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|}
             "/api/desk/kill/reset")
      in
      Alcotest.(check int)
        "a raise partway through a reset: 500, not a closed socket" 500 code;
      Alcotest.(check string)
        "the mutation sentence, because Halt.reset already ran"
        {|{"error":"the desk hit an internal error partway through; the outcome is unknown -- look this order up, or check /api/desk, before trying again"}|}
        text;
      Alcotest.(check string)
        "the halt really was reset before the raise" "clear"
        (D.Halt.State.name (D.Halt.state halt)))

(* The engine's stop: what stopped -- the venue's order updates -- has not
   recovered, so a confirmed reset from this site is refused with 409 and a
   fixed sentence, and changes nothing. The order path refuses in the stop's
   own words, never a hand's. *)
let test_a_reset_is_409_while_the_engine_has_stopped_the_desk () =
  let events = ref [] in
  with_routes ~host:`Live
    ~on_event:(fun line -> events := line :: !events)
    ~f:(fun server oms ->
      let halt = D.Oms.halt oms in
      D.Halt.stop halt ~why:"the venue's order updates stopped" ~at:(Time_ns.now ());
      let code, text =
        dispatched server
          (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|}
             "/api/desk/kill/reset")
      in
      Alcotest.(check int) "a confirmed reset from this site: 409" 409 code;
      Alcotest.(check string)
        "a fixed sentence saying a restart clears it"
        {|{"error":"the engine stopped this desk, and a reset cannot lift that: what stopped it has not recovered -- only a restart of the engine clears it, reconnecting to the venue and reconciling"}|}
        text;
      Alcotest.(check string)
        "still stopped" "stopped"
        (D.Halt.State.name (D.Halt.state halt));
      Alcotest.(check bool)
        "no line saying the switch was reset" false
        (List.exists !events ~f:(fun line ->
             String.is_substring line ~substring:"reset by request"));
      let code, text =
        dispatched server
          (request ~body:{|{"symbol":"AAPL","side":"buy","qty":10}|} "/api/desk/preview")
      in
      Alcotest.(check int) "a preview: 200" 200 code;
      let json = Yojson.Safe.from_string text in
      let rules = Yojson.Safe.Util.(to_list (member "rules" json)) in
      Alcotest.(check (list string))
        "refused by the switch alone" [ "kill_switch" ]
        (List.map rules ~f:(fun r -> Yojson.Safe.Util.(to_string (member "rule" r))));
      let why = Yojson.Safe.Util.(to_string (member "why" (List.hd_exn rules))) in
      Alcotest.(check bool)
        "in the stop's own words" true
        (String.is_substring why ~substring:"stopped by the engine");
      Alcotest.(check bool)
        "never a hand's" false
        (String.is_substring why ~substring:"by hand"))

let suite =
  ( "desk_routes",
    [
      Alcotest.test_case "who may change the desk" `Quick test_who_may_change_the_desk;
      Alcotest.test_case "the demo refuses orders and answers previews" `Quick
        test_the_demo_refuses_orders_and_answers_previews;
      Alcotest.test_case "a ticket that cannot be read is a 400 naming the field" `Quick
        test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field;
      Alcotest.test_case "a reset must say so" `Quick test_a_reset_must_say_so;
      Alcotest.test_case "a reset is 409 while the engine has stopped the desk" `Quick
        test_a_reset_is_409_while_the_engine_has_stopped_the_desk;
    ] )
