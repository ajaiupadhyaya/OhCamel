(* Phase 3. The wire format.

   No socket is opened here. What is tested is the JSON the dashboard is handed,
   because that is a contract between two languages: OCaml decides what a number
   means and JavaScript renders it, and nothing in between will catch a
   disagreement. The type system stops at the encoder.

   The cases that matter are the ones where a value could be misread rather than
   missing -- a fraction rendered as dollars, a NaN that breaks a parse, an
   "unknown" that arrives looking like a zero. *)

open Core
module Server = Ohcamel.Server
module Graph = Ohcamel.Graph
open Ohcamel.Types

let aapl = Symbol.of_string "AAPL"
let xom = Symbol.of_string "XOM"
let tech = Sector.of_string "TECH"
let energy = Sector.of_string "ENERGY"

let book =
  [
    { Instrument.symbol = aapl; sector = tech };
    { Instrument.symbol = xom; sector = energy };
  ]

let limit name scope kind = { Limit.name; scope; kind }

let limits =
  [
    limit "aapl-cap" (Limit.Instrument aapl)
      (Limit.Gross_notional (Notional.of_float 25_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (Notional.of_float 3_000.0));
    limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.10);
  ]

let returns = [| -0.05; -0.04; -0.03; -0.02; -0.01; 0.01; 0.02; 0.03; 0.04; 0.05 |]

(* AAPL 150 x 200 = +30,000 ; XOM 100 x -400 = -40,000. gross 70,000. *)
let with_graph ?(seed = true) ~f () =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 100_000.0) ~instruments:book ~limits
      ~confidence:0.95 ~return_window:10 ()
  in
  if seed then (
    Graph.set_price graph aapl (Price.of_float 150.0);
    Graph.set_price graph xom (Price.of_float 100.0);
    Graph.set_qty graph aapl (Qty.of_float 200.0);
    Graph.set_qty graph xom (Qty.of_float (-400.0));
    Graph.set_returns graph aapl returns;
    Graph.set_returns graph xom (Array.map returns ~f:Float.neg);
    Graph.stabilize graph);
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let encode graph = Server.json_of_snapshot ~graph ~factor:"DGS10" (Graph.snapshot graph)

let field json key =
  match json with
  | `Assoc fields -> List.Assoc.find fields key ~equal:String.equal
  | _ -> None

let field_exn json key =
  match field json key with Some v -> v | None -> Alcotest.failf "missing key %S" key

let num json key =
  match field_exn json key with
  | `Float f -> f
  | `Int i -> float_of_int i
  | other -> Alcotest.failf "%s is not a number: %s" key (Yojson.Safe.to_string other)

(* The snapshot has to survive a round trip through a real JSON parser. Yojson
   will happily emit things it cannot read back -- `Float nan` among them -- so
   serialising and reparsing is the only honest check. *)
let test_round_trips () =
  with_graph
    ~f:(fun graph ->
      let text = Yojson.Safe.to_string (encode graph) in
      match Option.try_with (fun () -> Yojson.Safe.from_string text) with
      | None -> Alcotest.fail "the snapshot did not parse back as JSON"
      | Some json ->
          List.iter
            [
              "as_of";
              "factor";
              "positions";
              "sectors";
              "gross_exposure";
              "net_exposure";
              "equity";
              "current_drawdown";
              "historical_var";
              "expected_shortfall";
              "parametric_var";
              "parametric_var_ewma";
              "ewma_lambda";
              "value_at_risk_notional";
              "expected_shortfall_notional";
              "portfolio_beta";
              "portfolio_gamma";
              "portfolio_vega";
              "vega_by_bucket";
              "warming_up";
              "feed";
              "limits";
              "unevaluated";
              "nodes_recomputed";
            ] ~f:(fun key -> ignore (field_exn json key : Yojson.Safe.t)))
    ()

let test_values () =
  with_graph
    ~f:(fun graph ->
      let j = encode graph in
      Alcotest.(check (float 1e-6)) "gross" 70_000.0 (num j "gross_exposure");
      Alcotest.(check (float 1e-6)) "net" (-10_000.0) (num j "net_exposure");
      Alcotest.(check (float 1e-6)) "equity = cash + net" 90_000.0 (num j "equity");
      Alcotest.(check string)
        "factor name travels" "DGS10"
        (match field_exn j "factor" with `String s -> s | _ -> "?");
      (* Positions carry their sector, so the dashboard can group without a
         second request. *)
      match field_exn j "positions" with
      | `List (first :: _) ->
          Alcotest.(check string)
            "first position is AAPL" "AAPL"
            (match field_exn first "symbol" with `String s -> s | _ -> "?");
          Alcotest.(check string)
            "with its sector" "TECH"
            (match field_exn first "sector" with `String s -> s | _ -> "?");
          Alcotest.(check (float 1e-6)) "and its exposure" 30_000.0 (num first "exposure")
      | _ -> Alcotest.fail "positions should be a non-empty list")
    ()

(* The unit travels beside the number, and this is the case it exists for.

   A Gross_notional threshold of 25000 and a Max_drawdown threshold of 0.10 are
   both bare JSON numbers. Without the unit field the client has to infer which
   is money and which is a fraction from the limit's NAME, which is a guess.
   Types.ml keeps the two apart specifically so a drawdown cannot be compared
   against a dollar exposure; shipping them as undifferentiated floats would
   undo that at the last step. *)
let test_limit_units () =
  with_graph
    ~f:(fun graph ->
      let j = encode graph in
      let limits =
        match field_exn j "limits" with
        | `List xs -> xs
        | _ -> Alcotest.fail "limits should be a list"
      in
      let by_name name =
        match
          List.find limits ~f:(fun l ->
              match field_exn l "name" with
              | `String s -> String.equal s name
              | _ -> false)
        with
        | Some l -> l
        | None -> Alcotest.failf "no limit named %S" name
      in
      let unit_of l = match field_exn l "unit" with `String s -> s | _ -> "?" in
      Alcotest.(check string)
        "a notional cap is money" "money"
        (unit_of (by_name "aapl-cap"));
      Alcotest.(check string) "a VaR cap is money" "money" (unit_of (by_name "var-cap"));
      Alcotest.(check string)
        "a drawdown cap is a fraction" "fraction"
        (unit_of (by_name "dd-cap"));
      (* And the numbers are in those units, not normalised to one of them. *)
      Alcotest.(check (float 1e-9))
        "drawdown threshold stays 0.10" 0.10
        (num (by_name "dd-cap") "threshold");
      Alcotest.(check (float 1e-6))
        "notional threshold stays 25000" 25_000.0
        (num (by_name "aapl-cap") "threshold");
      (* AAPL is 30,000 against 25,000. *)
      Alcotest.(check bool)
        "aapl-cap is breached" true
        (match field_exn (by_name "aapl-cap") "breached" with `Bool b -> b | _ -> false);
      Alcotest.(check (float 1e-6))
        "utilisation" 1.2
        (num (by_name "aapl-cap") "utilisation"))
    ()

(* "Cannot be evaluated" must not arrive looking like "fine".

   An unevaluated limit is absent from [limits] and present in [unevaluated], so
   the client cannot render it as a passing row by accident -- it has to decide
   what to do with a name that appears nowhere else. *)
let test_unknown_is_not_zero () =
  with_graph ~seed:false
    ~f:(fun graph ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let j = encode graph in
      Alcotest.(check bool)
        "warming up" true
        (match field_exn j "warming_up" with `Bool b -> b | _ -> false);
      (* The risk numbers are null, not 0.0. A zero would render as "no risk",
         which risk_metrics.ml calls the most dangerous wrong answer available. *)
      List.iter
        [
          "historical_var";
          "expected_shortfall";
          "parametric_var";
          (* The EWMA sibling warms up on the same schedule, and it is asserted
             separately because it is a separate node: an estimator that
             returned 0.0 rather than null on an empty window would render as
             "no risk" on the dashboard, which is the one wrong answer this
             codebase treats as worse than no answer. [ewma_lambda] is
             deliberately NOT in this list -- it is configuration, known before
             any data arrives, and nulling it would be a different mistake. *)
          "parametric_var_ewma";
          "value_at_risk_notional";
          "expected_shortfall_notional";
          "portfolio_beta";
        ] ~f:(fun key ->
          match field_exn j key with
          | `Null -> ()
          | other ->
              Alcotest.failf "%s should be null while warming up, got %s" key
                (Yojson.Safe.to_string other));
      Alcotest.(check (list string))
        "the VaR limit is listed as unevaluated" [ "var-cap" ]
        (match field_exn j "unevaluated" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []);
      Alcotest.(check bool)
        "and does not appear among the evaluated limits" false
        (match field_exn j "limits" with
        | `List xs ->
            List.exists xs ~f:(fun l ->
                match field_exn l "name" with
                | `String s -> String.equal s "var-cap"
                | _ -> false)
        | _ -> false))
    ()

let test_feed_health_shape () =
  with_graph
    ~f:(fun graph ->
      let feed = field_exn (encode graph) "feed" in
      (* Nothing has arrived over a wire -- the seed uses set_price -- so every
         symbol is never-seen, and never-seen is NOT stale. *)
      Alcotest.(check bool)
        "not healthy" false
        (match field_exn feed "healthy" with `Bool b -> b | _ -> true);
      Alcotest.(check (list string))
        "never seen" [ "AAPL"; "XOM" ]
        (match field_exn feed "never_seen" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []);
      Alcotest.(check (list string))
        "and none stale -- you cannot go stale without ever being fresh" []
        (match field_exn feed "stale" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> [ "?" ]);
      (* A tick makes exactly that symbol live. *)
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.now () };
      Graph.set_now graph (Time.now ());
      let feed = field_exn (encode graph) "feed" in
      Alcotest.(check (list string))
        "AAPL has now printed" [ "XOM" ]
        (match field_exn feed "never_seen" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []))
    ()

(* NaN and infinity are not JSON. They should be unreachable -- the feed rejects
   non-finite prices -- but "unreachable" is not a wire format. Yojson will emit
   `Float nan` as the bare token NaN, which no browser will parse, so a single
   escaped NaN would take out the whole dashboard rather than one number. *)
let test_non_finite_becomes_null () =
  Alcotest.(check string) "nan" "null" (Yojson.Safe.to_string (Server.jfloat Float.nan));
  Alcotest.(check string)
    "+inf" "null"
    (Yojson.Safe.to_string (Server.jfloat Float.infinity));
  Alcotest.(check string)
    "-inf" "null"
    (Yojson.Safe.to_string (Server.jfloat Float.neg_infinity));
  Alcotest.(check bool)
    "and an ordinary float survives" true
    (String.is_prefix (Yojson.Safe.to_string (Server.jfloat 1.5)) ~prefix:"1.5")

(* SSE framing. The blank line terminates the event; without the second newline
   the browser buffers the frame forever waiting for more, which presents as a
   dashboard that connects successfully and then never updates. *)
let test_sse_framing () =
  let e = Server.sse_event "{\"a\":1}" in
  Alcotest.(check string) "framed" "data: {\"a\":1}\n\n" e;
  Alcotest.(check bool) "ends with a blank line" true (String.is_suffix e ~suffix:"\n\n")

(* /api/history, in the same wire conventions /api/snapshot uses.

   Column-major, so the shape under test is "every series is the same length as
   [time]". A ragged response is the failure mode a chart would render as a line
   whose points had drifted off their own timestamps -- plausible-looking and
   entirely wrong, which is the category of bug this project's tests exist
   for. *)
let test_history_wire_format () =
  let h = Ohcamel.History_buffer.create ~capacity:4 () in
  let point n =
    {
      Ohcamel.History_buffer.Point.time_ms = float_of_int (n * 1000);
      gross = float_of_int (n * 100);
      net = float_of_int n;
      equity = 0.0;
      drawdown = 0.01 *. float_of_int n;
      (* One known and one unknown, so the null path is exercised alongside the
         numeric one in a single response. *)
      var_notional = (if n % 2 = 0 then Some (float_of_int n) else None);
      es_notional = None;
    }
  in
  List.iter [ 1; 2; 3; 4; 5 ] ~f:(fun n -> Ohcamel.History_buffer.append h (point n));
  let j = Ohcamel.Server.json_of_history h in
  let field = field_exn j in
  Alcotest.(check int)
    "points is the number on the wire, after eviction" 4
    (match field "points" with `Int n -> n | _ -> Alcotest.fail "points");
  Alcotest.(check int)
    "appended counts the evicted one too" 5
    (match field "appended" with `Int n -> n | _ -> Alcotest.fail "appended");
  Alcotest.(check int)
    "capacity" 4
    (match field "capacity" with `Int n -> n | _ -> Alcotest.fail "capacity");
  let series name =
    match field name with `List xs -> xs | _ -> Alcotest.failf "%s is not a list" name
  in
  List.iter
    [ "time"; "gross"; "net"; "equity"; "drawdown"; "var_notional"; "es_notional" ]
    ~f:(fun name ->
      Alcotest.(check int)
        (Printf.sprintf "%s has one entry per point" name)
        4
        (List.length (series name)));
  (* Oldest first, and the oldest is 2 because 1 was evicted. *)
  (match series "gross" with
  | [ `Float a; _; _; `Float d ] ->
      Alcotest.(check (float 1e-9)) "oldest surviving gross" 200.0 a;
      Alcotest.(check (float 1e-9)) "newest gross" 500.0 d
  | _ -> Alcotest.fail "gross series shape");
  (* Unknown arrives as null, never as 0.0 -- the same rule /api/snapshot
     follows, and for the same reason: a zero renders as "no risk". *)
  Alcotest.(check bool)
    "every es_notional is null" true
    (List.for_all (series "es_notional") ~f:(function `Null -> true | _ -> false));
  Alcotest.(check bool)
    "var_notional carries both nulls and numbers" true
    (List.exists (series "var_notional") ~f:(function `Null -> true | _ -> false)
    && List.exists (series "var_notional") ~f:(function `Float _ -> true | _ -> false))

let test_history_of_an_empty_buffer_is_well_formed () =
  let h = Ohcamel.History_buffer.create ~capacity:4 () in
  let j = Ohcamel.Server.json_of_history h in
  Alcotest.(check int)
    "no points" 0
    (match field_exn j "points" with `Int n -> n | _ -> Alcotest.fail "points");
  (* Empty arrays, not absent keys. A consumer that has to distinguish "no
     history" from "no such field" is a consumer that will get it wrong. *)
  List.iter [ "time"; "gross"; "drawdown" ] ~f:(fun name ->
      match field_exn j name with
      | `List [] -> ()
      | _ -> Alcotest.failf "%s should be an empty list" name)

(* The build stamp is either the truth or the word "unknown", and never
   anything in between.

   This is the field /api/ops exists for: the smoke suite compares it to the
   sha deploy.sh just built from, and `docker compose up -d` is a no-op when
   the image digest has not changed, so a deploy that silently kept the old
   container is otherwise indistinguishable from one that worked. A plausible
   default here -- today's date, a short hash of nothing -- would make that
   comparison pass while being a lie, which is worse than the absence it
   replaced. So: forty hex characters or the word `unknown`, an ISO-8601 UTC
   instant or the word `unknown`, and nothing else. *)
let test_build_stamp_is_honest () =
  let sha = Ohcamel.Build_info.git_sha in
  Alcotest.(check bool)
    "git_sha is `unknown` or forty hex characters" true
    (String.equal sha "unknown"
    || String.length sha = 40
       && String.for_all sha ~f:(fun c ->
           Char.is_digit c || Char.between c ~low:'a' ~high:'f'));
  (* `date -u +%FT%TZ` is exactly "2026-09-03T12:34:56Z": twenty characters,
     T at index 10, Z at index 19. *)
  let built = Ohcamel.Build_info.built_at in
  Alcotest.(check bool)
    "built_at is `unknown` or an ISO-8601 UTC instant" true
    (String.equal built "unknown"
    || (String.length built = 20 && Char.equal built.[10] 'T' && Char.equal built.[19] 'Z')
    );
  (* These three come from dune itself and cannot be absent. An empty one would
     render as a blank cell on the page, which reads as "not measured" rather
     than as "this build is broken". *)
  List.iter
    [
      ("profile", Ohcamel.Build_info.profile);
      ("architecture", Ohcamel.Build_info.architecture);
      ("system", Ohcamel.Build_info.system);
    ]
    ~f:(fun (name, value) ->
      Alcotest.(check bool) (name ^ " is not empty") true (not (String.is_empty value)))

(* A server over the seeded graph.

   Constructing one inside a test is safe with no Async scheduler running:
   [create] fills no Ivar, and Async's don't_wait_for is literally
   `let don't_wait_for (_ : unit t) = ()`, so the broadcaster is built, parks
   on an Ivar nobody fills, and costs nothing. Nothing here opens a socket. *)
let with_server ?(mode = `Demo) ?alerts ?peer ?feed_stats ?quiet ~f () =
  with_graph
    ~f:(fun graph ->
      let server =
        Server.create ?alerts ?peer ?feed_stats ?quiet ~mode ~graph ~factor:"SYNTHETIC" ()
      in
      f server graph)
    ()

(* What the process is, as opposed to what the book is.

   None of this existed before: the live host could not say it was the live
   host, nothing recorded a start time, and the port was known only to the
   caller. Each of the five is a row on /ops and an assertion in the smoke
   suite, and each is a field rather than a computation because a monitoring
   page that derives its own facts is a page that can be wrong on its own. *)
let test_server_knows_what_it_is () =
  with_server ~mode:`Live ~peer:"https://ohcamel.example.com" ~quiet:[ xom ]
    ~f:(fun server _graph ->
      Alcotest.(check bool)
        "it is the live host" true
        (match Server.mode server with `Live -> true | `Demo -> false);
      Alcotest.(check (option string))
        "and it knows where the other one is" (Some "https://ohcamel.example.com")
        (Server.peer server);
      Alcotest.(check (list string))
        "the deliberately quiet names travel" [ "XOM" ]
        (List.map (Server.quiet server) ~f:Symbol.to_string);
      (* 0 until [start] binds. 0 is not a port, so a reader who sees it is
         looking at a process that never listened -- which is a fact worth
         being able to see rather than a default that lies about 8080. *)
      Alcotest.(check int) "no port until start" 0 (Server.port server);
      Alcotest.(check bool)
        "started_at is not in the future" true
        (Float.( >= )
           (Time_ns.Span.to_sec (Time_ns.diff (Time.now ()) (Server.started_at server)))
           0.0))
    ()

let test_a_demo_server_has_no_peer () =
  with_server
    ~f:(fun server _graph ->
      Alcotest.(check bool)
        "demo" true
        (match Server.mode server with `Demo -> true | `Live -> false);
      (* Nothing crosses from the gated host to the public one, so the public
         one is given no peer at all rather than a peer it may not fetch. *)
      Alcotest.(check (option string)) "no peer" None (Server.peer server);
      Alcotest.(check (list string))
        "and nothing is quiet unless said so" []
        (List.map (Server.quiet server) ~f:Symbol.to_string))
    ()

(* The one header that is not the same on both hosts.

   /ops on the live origin fills its second column by fetching the PUBLIC
   engine's /api/ops cross-origin, which a browser allows only if that engine
   says so. The demo engine says so; the live engine says nothing, and the
   Caddyfile says nothing on either -- the gate is not weakened, it is simply
   not the thing being asked.

   Two rules make it safe rather than merely convenient. It is on JSON only,
   never on the pages and never on the stream, so nothing that carries a book
   into a document context is readable from elsewhere. And it is on the host
   whose entire book is already public: the day this appears in live mode, a
   page on any origin can read the owner's positions. *)
let test_cors_is_demo_json_only () =
  let cors headers = Cohttp.Header.get headers "Access-Control-Allow-Origin" in
  Alcotest.(check (option string))
    "demo JSON is readable cross-origin" (Some "*")
    (cors (Server.json_headers ~mode:`Demo));
  Alcotest.(check (option string))
    "live JSON is not" None
    (cors (Server.json_headers ~mode:`Live));
  Alcotest.(check (option string))
    "the stream never is, in either mode" None (cors Server.sse_headers);
  Alcotest.(check (option string))
    "and neither are the pages" None (cors Server.html_headers);
  (* What was already there stays there in both modes. A cached snapshot is a
     stale snapshot wearing a fresh timestamp. *)
  List.iter [ `Demo; `Live ] ~f:(fun mode ->
      Alcotest.(check (option string))
        "no-store" (Some "no-store")
        (Cohttp.Header.get (Server.json_headers ~mode) "Cache-Control");
      Alcotest.(check (option string))
        "application/json" (Some "application/json")
        (Cohttp.Header.get (Server.json_headers ~mode) "Content-Type"))

(* A graph with exactly one limit, so the firing list is derivable by hand
   rather than by running the thing and writing down what came out.

   AAPL at 150 x 200 = 30,000 against a 25,000 cap: utilisation 1.2, breached.
   Nothing else is configured, so the tracker can be firing on one name and
   only one. Dry_run is the sink because it formats what would be sent and
   sends nothing; no test in this project may reach a network. *)
let alerted_limit =
  {
    Limit.name = "aapl-cap";
    scope = Limit.Instrument aapl;
    kind = Limit.Gross_notional (Notional.of_float 25_000.0);
  }

let with_alerts ~f () =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 100_000.0)
      ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
      ~limits:[ alerted_limit ] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      match
        Ohcamel.Alerts.attach ~graph
          ~config:
            {
              Ohcamel.Config.Alerts.enabled = true;
              sinks = [ Ohcamel.Config.Alerts.Sink.Dry_run ];
              clear_below = 0.95;
              kill_switch_enabled = true;
              kill_switch_trips_on = [ "aapl-cap" ];
            }
      with
      | Error e -> Alcotest.failf "attach: %s" (Error.to_string_hum e)
      | Ok None -> Alcotest.fail "an enabled config must produce a notifier"
      | Ok (Some a) ->
          Graph.set_price graph aapl (Price.of_float 150.0);
          Graph.set_qty graph aapl (Qty.of_float 200.0);
          Graph.stabilize graph;
          f a)
    ~finally:(fun () -> Graph.destroy graph)

(* Off is a state with facts in it, not an absence of fields.

   Both branches emit the same keys so the page never has to ask whether a
   field exists before asking what it says -- a client that branches on shape
   is a client that renders `undefined` the first time the other branch
   ships. clear_below is null rather than 0.95 when there is no notifier:
   there is no hysteresis to report, and a default printed as a measurement is
   the failure this whole wire format is arranged against. *)
let test_alerts_json_when_off () =
  let j = Server.json_of_alerts None in
  Alcotest.(check bool)
    "not enabled" true
    (match field_exn j "enabled" with `Bool b -> not b | _ -> false);
  Alcotest.(check string)
    "the switch is off" "off"
    (match field_exn j "kill_switch" with `String s -> s | _ -> "?");
  List.iter [ "tripped_by"; "tripped_at"; "clear_below" ] ~f:(fun key ->
      match field_exn j key with
      | `Null -> ()
      | other ->
          Alcotest.failf "%s should be null with no notifier, got %s" key
            (Yojson.Safe.to_string other));
  List.iter [ "firing"; "sinks"; "trips_on"; "recent" ] ~f:(fun key ->
      match field_exn j key with
      | `List [] -> ()
      | other ->
          Alcotest.failf "%s should be an empty list, got %s" key
            (Yojson.Safe.to_string other))

let test_alerts_json_when_firing () =
  with_alerts
    ~f:(fun a ->
      let j = Server.json_of_alerts (Some a) in
      Alcotest.(check (list string))
        "one limit, and it is the one over its line" [ "aapl-cap" ]
        (match field_exn j "firing" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []);
      Alcotest.(check string)
        "the switch latched" "tripped"
        (match field_exn j "kill_switch" with `String s -> s | _ -> "?");
      Alcotest.(check string)
        "and named what did it" "aapl-cap"
        (match field_exn j "tripped_by" with `String s -> s | _ -> "?");
      Alcotest.(check bool)
        "with a time, because a bounded event queue will forget the event" true
        (match field_exn j "tripped_at" with `String _ -> true | _ -> false);
      Alcotest.(check bool)
        "and orders are flagged halted" true
        (match field_exn j "halt_new_orders" with `Bool b -> b | _ -> false);
      (* The sink is a WORD, not the sexp of its constructor. A File sink
         carries a path, /api/ops is public on the demo host, and a filesystem
         path is information about the machine that nothing on the page needs. *)
      Alcotest.(check (list string))
        "the sink is named, never described" [ "dry_run" ]
        (match field_exn j "sinks" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []);
      Alcotest.(check (list string))
        "and so is what the switch trips on" [ "aapl-cap" ]
        (match field_exn j "trips_on" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> []);
      Alcotest.(check (float 1e-9))
        "the hysteresis is on the wire, not assumed" 0.95 (num j "clear_below"))
    ()

let suite =
  ( "server",
    [
      Alcotest.test_case "the build stamp is honest or absent" `Quick
        test_build_stamp_is_honest;
      Alcotest.test_case "the snapshot parses back as JSON" `Quick test_round_trips;
      Alcotest.test_case "values and positions" `Quick test_values;
      Alcotest.test_case "each limit carries its unit" `Quick test_limit_units;
      Alcotest.test_case "unknown arrives as null, never as zero" `Quick
        test_unknown_is_not_zero;
      Alcotest.test_case "feed health shape" `Quick test_feed_health_shape;
      Alcotest.test_case "non-finite floats become null" `Quick
        test_non_finite_becomes_null;
      Alcotest.test_case "SSE event framing" `Quick test_sse_framing;
      Alcotest.test_case "/api/history wire format" `Quick test_history_wire_format;
      Alcotest.test_case "/api/history of an empty buffer" `Quick
        test_history_of_an_empty_buffer_is_well_formed;
      Alcotest.test_case "the server knows what it is" `Quick test_server_knows_what_it_is;
      Alcotest.test_case "a demo server has no peer" `Quick test_a_demo_server_has_no_peer;
      Alcotest.test_case "CORS is on the demo host's JSON and nothing else" `Quick
        test_cors_is_demo_json_only;
      Alcotest.test_case "the alerts object when nothing is armed" `Quick
        test_alerts_json_when_off;
      Alcotest.test_case "the alerts object when a limit is firing" `Quick
        test_alerts_json_when_firing;
    ] )
