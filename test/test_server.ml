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
let with_graph ?(seed = true) ?on_compute ~f () =
  let graph =
    Graph.create ?on_compute ~starting_cash:(Notional.of_float 100_000.0)
      ~instruments:book ~limits ~confidence:0.95 ~return_window:10 ()
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
let with_server ?(mode = `Demo) ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~f () =
  with_graph
    ~f:(fun graph ->
      let server =
        Server.create ?alerts ?peer ?feed_stats ?quiet ?recompute_log ~mode ~graph
          ~factor:"SYNTHETIC" ()
      in
      f server graph)
    ()

(* A server over a graph whose hook writes into the log the server holds --
   the shape run_demo and run_live build. The seeding runs BEFORE the log is
   read by anything, so a test that wants a clean frame drains once first. *)
let with_logged_server ~f () =
  let log = Ohcamel.Recompute_log.create () in
  with_graph
    ~on_compute:(Ohcamel.Recompute_log.note log)
    ~f:(fun graph ->
      let server =
        Server.create ~recompute_log:log ~mode:`Demo ~graph ~factor:"SYNTHETIC" ()
      in
      f server graph log)
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

(* The twelve keys the brief names, in the order [json_of_alerts] writes
   them. Compared as a SET (sorted) against the object's own keys, so an
   extra key or a dropped one fails here rather than in whichever field
   [field_exn] happened to be asked for -- [field_exn] proves a key it is
   given is present, never that no other key is missing. *)
let alert_keys =
  [
    "enabled";
    "kill_switch";
    "tripped_by";
    "tripped_at";
    "halt_new_orders";
    "firing";
    "sent";
    "failed";
    "sinks";
    "trips_on";
    "clear_below";
    "recent";
  ]

let assoc_keys json = match json with `Assoc fields -> List.map fields ~f:fst | _ -> []

let check_alert_key_set j =
  Alcotest.(check (list string))
    "exactly the twelve alert keys, no more and no fewer"
    (List.sort ~compare:String.compare alert_keys)
    (List.sort ~compare:String.compare (assoc_keys j))

(* /api/ops's alerts object omits [recent]: the event history's lines are
   [Alerts.Event.to_line], which carries a symbol scope (e.g. "Instrument
   AAPL"), and the spec's ops `alerts` list deliberately does not have this
   key. Eleven keys, not check_alert_key_set's twelve -- /api/snapshot's
   alerts object (built by [render], via the default [~recent:true]) is what
   still owns the twelve-key contract. *)
let ops_alert_keys = List.filter alert_keys ~f:(fun k -> not (String.equal k "recent"))

let check_ops_alert_key_set j =
  Alcotest.(check (list string))
    "exactly the eleven ops alert keys (no recent), no more and no fewer"
    (List.sort ~compare:String.compare ops_alert_keys)
    (List.sort ~compare:String.compare (assoc_keys j))

(* The general form of [check_alert_key_set], for every nesting level of
   /api/ops's object. Compared as a SET (sorted) rather than probed key by
   key, so a rename or a dropped key fails HERE -- at the one place that
   speaks for the whole object -- rather than silently passing every
   [field_exn] a test happened to ask for and never asking about the rest. *)
let check_key_set ~what expected json =
  Alcotest.(check (list string))
    (Printf.sprintf "exactly the %s keys, no more and no fewer" what)
    (List.sort ~compare:String.compare expected)
    (List.sort ~compare:String.compare (assoc_keys json))

(* Off is a state with facts in it, not an absence of fields.

   Both branches emit the same keys so the page never has to ask whether a
   field exists before asking what it says -- a client that branches on shape
   is a client that renders `undefined` the first time the other branch
   ships. clear_below is null rather than 0.95 when there is no notifier:
   there is no hysteresis to report, and a default printed as a measurement is
   the failure this whole wire format is arranged against. *)
let test_alerts_json_when_off () =
  let j = Server.json_of_alerts None in
  check_alert_key_set j;
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
  Alcotest.(check bool)
    "orders are not flagged halted" false
    (match field_exn j "halt_new_orders" with `Bool b -> b | _ -> true);
  List.iter [ "sent"; "failed" ] ~f:(fun key ->
      Alcotest.(check int)
        (key ^ " is zero with no notifier")
        0
        (match field_exn j key with `Int i -> i | _ -> -1));
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
      check_alert_key_set j;
      Alcotest.(check bool)
        "enabled, because there is a notifier" true
        (match field_exn j "enabled" with `Bool b -> b | _ -> false);
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
        "the hysteresis is on the wire, not assumed" 0.95 (num j "clear_below");
      (* [sent] and [failed] are counted by [deliver], which runs off the
         reader end of a Pipe under [don't_wait_for] -- an Async job that
         needs the scheduler to turn a cycle. This test calls [Graph.stabilize]
         and reads the notifier synchronously, inside a plain (non-Async)
         Alcotest case that never starts the scheduler, so that job never
         runs: no sink has actually delivered anything yet. 0 is the real,
         honest count of what has been sent so far, not a stand-in for
         "untested" -- the wire format must not lie about that either. The
         same absence of a scheduler tick is why [history] -- and so
         [recent] -- is still empty here, even though a limit has fired. *)
      Alcotest.(check int)
        "nothing has been delivered yet" 0
        (match field_exn j "sent" with `Int i -> i | _ -> -1);
      Alcotest.(check int)
        "and so nothing has failed to deliver" 0
        (match field_exn j "failed" with `Int i -> i | _ -> -1);
      match field_exn j "recent" with
      | `List [] -> ()
      | other ->
          Alcotest.failf "recent should still be empty (no scheduler tick): got %s"
            (Yojson.Safe.to_string other))
    ()

(* /api/ops is the route the owner reads at two in the morning, so what is
   asserted here is mostly about what it must NOT say.

   It must not name a symbol: the live host's book is behind a password and the
   counts are the whole reason a counts-only object exists. It must not count a
   subscriber whose pipe has closed. It must not report a graph.named count or
   a reports state this build cannot produce. And uptime has to be a number,
   because the smoke suite compares it to 300 and a string would pass every
   naive check while proving nothing. *)
let test_ops_shape () =
  with_server ~mode:`Live ~peer:"https://ohcamel.example.com" ~quiet:[ xom ]
    ~f:(fun server _graph ->
      let j = Server.json_of_ops server in
      (* Every key /api/ops promises, and no others. A rename or a dropped key
         hides from every [field_exn] below -- each proves only the one key it
         was given -- but not from this: the set is compared whole, sorted,
         against the brief's list. *)
      check_key_set ~what:"top-level ops"
        [
          "mode";
          "started_at";
          "uptime_s";
          "port";
          "pid";
          "hostname";
          "ocaml_version";
          "build";
          "process";
          "graph";
          "stream";
          "history";
          "alerts";
          "feed";
          "feed_source";
          "gc";
          "rss_bytes";
          "reports";
          "peer";
        ]
        j;
      Alcotest.(check string)
        "the mode is a word the client can switch on" "live"
        (match field_exn j "mode" with `String s -> s | _ -> "?");
      Alcotest.(check bool)
        "started_at is a string" true
        (match field_exn j "started_at" with `String _ -> true | _ -> false);
      Alcotest.(check bool)
        "uptime is a number and not negative" true
        (Float.( >= ) (num j "uptime_s") 0.0);
      Alcotest.(check bool)
        "port is an int" true
        (match field_exn j "port" with `Int _ -> true | _ -> false);
      Alcotest.(check bool)
        "pid is an int" true
        (match field_exn j "pid" with `Int _ -> true | _ -> false);
      Alcotest.(check bool)
        "hostname is a non-empty string" true
        (match field_exn j "hostname" with
        | `String s -> not (String.is_empty s)
        | _ -> false);
      Alcotest.(check bool)
        "ocaml_version is a string" true
        (match field_exn j "ocaml_version" with `String _ -> true | _ -> false);
      (* The build stamp, threaded from the droplet's checkout. git_short is
         seven characters of a real sha and the whole word when there is not
         one, because "unknow" would look like a sha that had been truncated. *)
      let build = field_exn j "build" in
      check_key_set ~what:"build"
        [
          "git_sha";
          "git_short";
          "built_at";
          "profile";
          "architecture";
          "system";
          "executable";
        ]
        build;
      let sha = match field_exn build "git_sha" with `String s -> s | _ -> "?" in
      let short = match field_exn build "git_short" with `String s -> s | _ -> "?" in
      Alcotest.(check bool)
        "git_short is seven of a real sha, or the whole word unknown" true
        (if String.equal sha "unknown" then String.equal short "unknown"
         else String.equal short (String.prefix sha 7));
      List.iter [ "built_at"; "profile"; "architecture"; "system"; "executable" ]
        ~f:(fun key ->
          match field_exn build key with
          | `String s when not (String.is_empty s) -> ()
          | other ->
              Alcotest.failf "build.%s should be a non-empty string, got %s" key
                (Yojson.Safe.to_string other));
      (* Incremental's counters for the WHOLE process, which is why the page
         labels them so. Direction only -- another test in this runner moves
         them. *)
      let process = field_exn j "process" in
      check_key_set ~what:"process"
        [
          "nodes_recomputed";
          "stabilizes";
          "nodes_created";
          "var_sets";
          "active_observers";
        ]
        process;
      List.iter
        [
          "nodes_recomputed";
          "stabilizes";
          "nodes_created";
          "var_sets";
          "active_observers";
        ] ~f:(fun key ->
          match field_exn process key with
          | `Int n when n >= 0 -> ()
          | other ->
              Alcotest.failf "process.%s should be a non-negative int, got %s" key
                (Yojson.Safe.to_string other));
      (* Filled from the recompute log when the server holds one (see
         test_ops_reports_the_named_log). This server was given none, so it is
         null and never a zero: "no named node ran" is the alarm, and a server
         that is not counting must not raise it. *)
      let ops_graph = field_exn j "graph" in
      check_key_set ~what:"graph" [ "named" ] ops_graph;
      Alcotest.(check bool)
        "graph.named is null when the server holds no recompute log" true
        (match field_exn ops_graph "named" with `Null -> true | _ -> false);
      (* Phase 5 fills these. `absent` rather than `ready`, for the same
         reason. *)
      let reports = field_exn j "reports" in
      check_key_set ~what:"reports" [ "static"; "garch" ] reports;
      List.iter [ "static"; "garch" ] ~f:(fun key ->
          Alcotest.(check string)
            ("reports." ^ key ^ " is absent until Phase 5")
            "absent"
            (match field_exn reports key with `String s -> s | _ -> "?"));
      (* Counts, never names. This is the assertion that keeps the live book
         off a page the owner might open on a phone in a coffee shop. *)
      let feed = field_exn j "feed" in
      check_key_set ~what:"feed"
        [ "healthy"; "symbols"; "stale"; "never_seen"; "quiet"; "staleness_threshold_s" ]
        feed;
      List.iter [ "symbols"; "stale"; "never_seen"; "quiet" ] ~f:(fun key ->
          match field_exn feed key with
          | `Int _ -> ()
          | other ->
              Alcotest.failf "feed.%s must be a count, got %s" key
                (Yojson.Safe.to_string other));
      Alcotest.(check int)
        "the quiet list is counted, not listed" 1
        (Int.of_float (num feed "quiet"));
      Alcotest.(check bool)
        "no symbol name appears anywhere in the object" false
        (String.is_substring (Yojson.Safe.to_string j) ~substring:"AAPL"
        || String.is_substring (Yojson.Safe.to_string j) ~substring:"XOM");
      (* The threshold the ages are measured against travels with them. *)
      Alcotest.(check (float 1e-9))
        "the default threshold" 90.0
        (num feed "staleness_threshold_s");
      (* No feed_stats closure was given, so this is the synthetic feed saying
         so in words rather than four nulls the client has to interpret. *)
      let feed_source = field_exn j "feed_source" in
      check_key_set ~what:"feed_source"
        [ "kind"; "alpaca_feed"; "fred_series"; "alpaca"; "fred" ]
        feed_source;
      Alcotest.(check string)
        "no closure means the synthetic feed" "synthetic"
        (match field_exn feed_source "kind" with `String s -> s | _ -> "?");
      (* Open pipes only. A browser that closed its tab must not keep counting
         as a subscriber, because "someone is watching" is the one thing this
         row is for. *)
      let stream = field_exn j "stream" in
      check_key_set ~what:"stream"
        [ "frames_sent"; "subscribers"; "coalesce_ms"; "keepalive_s" ]
        stream;
      Alcotest.(check bool)
        "frames_sent is an int" true
        (match field_exn stream "frames_sent" with `Int _ -> true | _ -> false);
      Alcotest.(check int) "no subscribers" 0 (Int.of_float (num stream "subscribers"));
      Alcotest.(check (float 1e-9))
        "the coalesce window is on the wire" 80.0 (num stream "coalesce_ms");
      Alcotest.(check (float 1e-9))
        "and so is the keepalive" 20.0 (num stream "keepalive_s");
      (* The bounded trail. Nothing has appended in this test (no observer
         tick, no scheduler cycle), so appended = points = 0 here -- but the
         invariant asserted is the general one: capacity is never smaller
         than what the buffer is actually holding. *)
      let history = field_exn j "history" in
      check_key_set ~what:"history" [ "appended"; "points"; "capacity" ] history;
      let history_int key =
        match field_exn history key with
        | `Int n -> n
        | other ->
            Alcotest.failf "history.%s should be an int, got %s" key
              (Yojson.Safe.to_string other)
      in
      let points = history_int "points" in
      let capacity = history_int "capacity" in
      ignore (history_int "appended" : int);
      Alcotest.(check bool)
        "history.capacity is never smaller than history.points" true (capacity >= points);
      (* The alerts object's contract at THIS nesting level is eleven keys,
         not Task 10's twelve: /api/ops omits [recent] so no symbol name can
         reach this object through the event history. See
         [check_ops_alert_key_set] and the comment on [json_of_alerts]. *)
      check_ops_alert_key_set (field_exn j "alerts");
      Alcotest.(check bool)
        "recent does not appear in the ops alerts object at all" false
        (List.mem (assoc_keys (field_exn j "alerts")) "recent" ~equal:String.equal);
      (* The heap counters: presence and shape only. Their values are the
         runtime's, and this test asserting a specific heap size would pin a
         number the GC is free to change between OCaml releases. *)
      let gc = field_exn j "gc" in
      check_key_set ~what:"gc"
        [
          "heap_words";
          "top_heap_words";
          "minor_collections";
          "major_collections";
          "compactions";
          "minor_words";
          "promoted_words";
          "major_words";
        ]
        gc;
      (* Linux only, and null everywhere else -- not zero, which would read as
         a process using no memory. *)
      (match field_exn j "rss_bytes" with
      | `Null | `Int _ -> ()
      | other ->
          Alcotest.failf "rss_bytes should be null or an int, got %s"
            (Yojson.Safe.to_string other));
      Alcotest.(check (option string))
        "the peer travels" (Some "https://ohcamel.example.com")
        (match field_exn j "peer" with `String s -> Some s | _ -> None);
      (* And the whole thing survives a real parser, like every other route. *)
      Alcotest.(check bool)
        "it parses back" true
        (Option.is_some
           (Option.try_with (fun () -> Yojson.Safe.from_string (Yojson.Safe.to_string j)))))
    ()

(* The demo host's own reading: no peer, and a feed_source that names its
   closure's answer rather than the synthetic default. *)
let test_ops_feed_source_comes_from_the_closure () =
  with_server
    ~feed_stats:(fun () ->
      `Assoc
        [
          ("kind", `String "alpaca");
          ("alpaca_feed", `String "iex");
          ("fred_series", `String "DGS10");
          ("alpaca", `Assoc [ ("frames", `Int 3) ]);
          ("fred", `Assoc [ ("polls", `Int 1) ]);
        ])
    ~f:(fun server _graph ->
      let j = Server.json_of_ops server in
      Alcotest.(check string)
        "the closure's answer is used verbatim" "alpaca"
        (match field_exn (field_exn j "feed_source") "kind" with
        | `String s -> s
        | _ -> "?");
      Alcotest.(check bool)
        "and the demo host has no peer" true
        (match field_exn j "peer" with `Null -> true | _ -> false))
    ()

(* One table, two readers, and this is the test that keeps them one.

   Before this phase the dispatcher was a seven-arm match and the 404 body was
   a list of six strings, written separately -- which is how a route can be
   served for weeks while the 404 body tells a caller it does not exist. Now
   both are generated from Server.routes, and this asserts the generation
   rather than trusting it: the 404 body parsed back must list exactly the
   table's paths in the table's order, every listed path must dispatch, and a
   path that is not listed must not. The eight paths are written out because
   a route added to the table without being added here is the one change
   this test exists to make somebody look at. Phase 5 adds two. *)
let test_the_404_lists_exactly_the_routes () =
  let listed =
    match Yojson.Safe.from_string Server.not_found_body with
    | `Assoc fields -> (
        match List.Assoc.find fields "routes" ~equal:String.equal with
        | Some (`List xs) -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> Alcotest.fail "the 404 body has no routes list")
    | _ -> Alcotest.fail "the 404 body is not an object"
  in
  Alcotest.(check (list string))
    "the 404 body lists the table, in the table's order" (Server.route_paths ()) listed;
  Alcotest.(check (list string))
    "the routes this phase ships"
    [
      "/";
      "/ops";
      "/api/snapshot";
      "/api/health";
      "/api/stream";
      "/api/history";
      "/api/stress";
      "/api/ops";
    ]
    (Server.route_paths ());
  List.iter (Server.route_paths ()) ~f:(fun path ->
      Alcotest.(check bool)
        (path ^ " dispatches") true
        (Option.is_some (Server.lookup path)));
  (* The one alias, kept because it has answered since Phase 3 and a bookmark
     must not start 404ing; it is not in the table because it is not a route,
     it is a spelling of one. *)
  Alcotest.(check bool)
    "/index.html is the one alias" true
    (Option.is_some (Server.lookup "/index.html"));
  Alcotest.(check bool)
    "an unknown path does not dispatch" false
    (Option.is_some (Server.lookup "/api/nope"));
  (* Every route carries a purpose. The 404 body prints them, and a route whose
     purpose is the empty string is a route nobody described. *)
  List.iter Server.routes ~f:(fun (path, purpose, _) ->
      Alcotest.(check bool) (path ^ " has a purpose") true (not (String.is_empty purpose)))

(* A route's answer, read without a socket.

   respond_string is `return (response, body)` -- an already-determined
   Deferred -- so Deferred.peek reads it with no scheduler running, exactly as
   the `async deferred round trip` link test does. The body of a string
   response is the `String constructor and is matched directly rather than
   through Body.to_string, which would hand back another Deferred. *)
let respond server path =
  match Server.lookup path with
  | None -> Alcotest.failf "%s is not routed" path
  | Some handler -> (
      match Async.Deferred.peek (handler server) with
      | None -> Alcotest.failf "%s did not answer without the scheduler" path
      | Some (response, body) -> (
          let status = Cohttp.Code.code_of_status (Cohttp.Response.status response) in
          let content_type =
            Cohttp.Header.get (Cohttp.Response.headers response) "Content-Type"
          in
          match body with
          | `String s -> (status, content_type, s)
          | `Empty -> (status, content_type, "")
          | `Strings ss -> (status, content_type, String.concat ss)
          | `Pipe _ -> Alcotest.failf "%s answered with a pipe" path))

let test_the_two_new_routes_answer () =
  with_server
    ~f:(fun server _graph ->
      let status, content_type, body = respond server "/api/ops" in
      Alcotest.(check int) "/api/ops is 200" 200 status;
      Alcotest.(check (option string))
        "and is JSON" (Some "application/json") content_type;
      Alcotest.(check string)
        "and says which host it is" "demo"
        (match field_exn (Yojson.Safe.from_string body) "mode" with
        | `String s -> s
        | _ -> "?");
      let status, content_type, body = respond server "/ops" in
      Alcotest.(check int) "/ops is 200" 200 status;
      Alcotest.(check (option string))
        "and is HTML" (Some "text/html; charset=utf-8") content_type;
      Alcotest.(check bool)
        "and is the operations page" true
        (String.is_substring body ~substring:"OhCamel<span>operations</span>");
      (* The 404 goes out with the JSON headers, so a demo host's 404 is
         readable cross-origin like its other JSON. Read through handle, which
         is the only caller lookup has. *)
      match Async.Deferred.peek (Server.handle server ~path:"/api/nope") with
      | Some (response, `String s) ->
          Alcotest.(check int)
            "an unknown path is 404" 404
            (Cohttp.Code.code_of_status (Cohttp.Response.status response));
          Alcotest.(check string) "with the generated body" Server.not_found_body s
      | _ -> Alcotest.fail "the 404 did not answer as a string")
    ()

(* Task 9's CORS test (above, [test_cors_is_demo_json_only]) proved
   json_headers / sse_headers / html_headers in isolation: three header
   values, built and inspected without ever being attached to a response.
   That leaves open the possibility a route is wired to the wrong header
   value -- the constructor is right and the table entry names the other
   one -- and nothing would catch it. This reads the header off what a real
   dispatch actually returns, through the same lookup + Deferred.peek
   [respond] uses, so it is the CORS claim proven through the table rather
   than through the function that builds one header on its own.

   /api/stream's handler is [subscribe], which answers through
   respond_with_pipe -- itself `respond`, so `return (resp, body)`, an
   already-determined Deferred exactly like every other route's. It is
   peekable here for the same reason [respond] above can peek /api/ops and
   /ops; the only difference is this reads the header and never touches the
   body, so the `Pipe body respond`'s body-match would fail on is never
   reached. subscribe's don't_wait_for calls (the initial frame, the
   keepalive loop) never run without a scheduler -- as the [with_server]
   comment notes, don't_wait_for is `let don't_wait_for (_ : unit t) = ()`
   here -- so peeking it costs nothing and leaves nothing scheduled. *)
let cors_header server path =
  match Server.lookup path with
  | None -> Alcotest.failf "%s is not routed" path
  | Some handler -> (
      match Async.Deferred.peek (handler server) with
      | None -> Alcotest.failf "%s did not answer without the scheduler" path
      | Some (response, _body) ->
          Cohttp.Header.get
            (Cohttp.Response.headers response)
            "access-control-allow-origin")

(* A route is JSON iff it is under /api/ and is not the stream: /api/stream is
   the same book at a higher rate served through respond_with_pipe, not data,
   and never carries this header (see the comment on [json_headers]). Named
   here rather than read off Content-Type, because the point of this test is
   that every entry in [Server.routes] -- not just the four the previous
   version of this test named by hand -- gets the header its own route type
   promises, including the three JSON routes (/api/snapshot, /api/history,
   /api/stress) that used to be proven only by [test_cors_is_demo_json_only]
   in isolation from the table. *)
let is_json_route path =
  String.is_prefix path ~prefix:"/api/" && not (String.equal path "/api/stream")

let test_cors_survives_being_read_through_the_table_and_not_just_built () =
  with_server ~mode:`Demo
    ~f:(fun server _graph ->
      List.iter Server.routes ~f:(fun (path, _, _) ->
          let expected = if is_json_route path then Some "*" else None in
          Alcotest.(check (option string))
            (Printf.sprintf "%s dispatches with %s" path
               (if is_json_route path then "the open header" else "no CORS header"))
            expected (cors_header server path)))
    ();
  with_server ~mode:`Live
    ~f:(fun server _graph ->
      Alcotest.(check (option string))
        "a live host's JSON dispatches with no such header at all" None
        (cors_header server "/api/health"))
    ()

(* The server holds the log it was given, and nothing when it was given none.
   An option rather than a default-constructed log, because a server with no
   hook wired into its graph would drain an empty table forever and publish
   [recomputed: []] on every frame -- "nothing ran", which is the alarm, said
   about a graph that simply was not asked. *)
let test_the_server_holds_the_log () =
  with_logged_server
    ~f:(fun server _graph log ->
      match Server.recompute_log server with
      | Some held -> Alcotest.(check bool) "the same log" true (phys_equal held log)
      | None -> Alcotest.fail "the log was dropped")
    ();
  with_server
    ~f:(fun server _graph ->
      Alcotest.(check bool)
        "absent when none was given" true
        (Option.is_none (Server.recompute_log server)))
    ()

let parse s = Yojson.Safe.from_string s

let names_of (json : Yojson.Safe.t) : string list =
  match json with
  | `List entries ->
      List.map entries ~f:(fun e ->
          match field_exn e "name" with
          | `String s -> s
          | _ -> Alcotest.fail "name is not a string")
  | other -> Alcotest.failf "recomputed is not a list: %s" (Yojson.Safe.to_string other)

(* A poller cannot steal the stream's set.

   /api/snapshot renders, and rendering stabilizes; if it also drained the
   log, a curl between two frames would leave the next frame saying nothing
   ran. So the snapshot path says [null] -- "the stream knows, ask it" -- and
   the set is still there for the frame that follows. *)
let test_a_poller_cannot_steal_the_streams_set () =
  with_logged_server
    ~f:(fun server graph log ->
      ignore (Ohcamel.Recompute_log.drain log : (string * int) list);
      ignore (Server.next_frame server : string);
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      let poll = parse (Server.render server) in
      Alcotest.(check bool)
        "/api/snapshot carries recomputed: null" true
        (match field_exn poll "recomputed" with `Null -> true | _ -> false);
      Alcotest.(check bool)
        "and no deltas" true
        (match
           (field_exn poll "stabilizes_delta", field_exn poll "nodes_recomputed_delta")
         with
        | `Null, `Null -> true
        | _ -> false);
      Alcotest.(check bool)
        "but the process-wide stabilizes total" true
        (Float.( > ) (num poll "stabilizes") 0.0);
      let frame = parse (Server.next_frame server) in
      let ran = names_of (field_exn frame "recomputed") in
      Alcotest.(check bool)
        "the frame after the poll still carries the tick's set" true
        (List.mem ran "exposure:AAPL" ~equal:String.equal
        && List.mem ran "feed:AAPL" ~equal:String.equal);
      Alcotest.(check bool)
        "and only the tick's set" false
        (List.mem ran "covariance" ~equal:String.equal
        || List.mem ran "exposure:XOM" ~equal:String.equal);
      (match field_exn frame "recomputed" with
      | `List entries ->
          List.iter entries ~f:(fun e ->
              Alcotest.(check bool)
                "each ran once inside one coalesce window" true
                (Float.equal (num e "n") 1.0))
      | _ -> Alcotest.fail "recomputed is not a list");
      Alcotest.(check bool)
        "the deltas are numbers on a frame" true
        (Float.( >= ) (num frame "stabilizes_delta") 1.0
        && Float.( > ) (num frame "nodes_recomputed_delta") 0.0))
    ()

(* The follow-up frame. A render-time stabilize that changes something fires
   on_change INSIDE Graph.snapshot, which fills the next Ivar; the broadcaster
   wakes again, coalesces, and takes another frame. That frame's set is empty
   -- the work was credited to the frame whose stabilize did it -- and it is
   sent as [recomputed: []] rather than suppressed, because a frame that
   arrives and says "nothing" is a fact about the engine and dropping it would
   make the frame-arrival strip lie. *)
let test_a_render_time_stabilize_produces_an_empty_follow_up () =
  with_logged_server
    ~f:(fun server graph log ->
      ignore (Ohcamel.Recompute_log.drain log : (string * int) list);
      ignore (Server.next_frame server : string);
      Alcotest.(check bool) "quiet: no frame pending" false (Server.pending_frame server);
      (* A write with NO stabilize: the broadcaster's own snapshot will be the
         first stabilize to see it. *)
      Graph.set_price graph aapl (Price.of_float 152.0);
      Alcotest.(check bool)
        "a bare Var.set observes nothing yet" false (Server.pending_frame server);
      let first = parse (Server.next_frame server) in
      Alcotest.(check bool)
        "the frame whose stabilize did the work carries it" true
        (List.mem
           (names_of (field_exn first "recomputed"))
           "exposure:AAPL" ~equal:String.equal);
      Alcotest.(check bool)
        "and that stabilize filled the next Ivar" true (Server.pending_frame server);
      let follow_up = parse (Server.next_frame server) in
      Alcotest.(check (list string))
        "the follow-up carries recomputed: []" []
        (names_of (field_exn follow_up "recomputed"));
      Alcotest.(check bool)
        "one stabilize, zero node bodies" true
        (Float.equal (num follow_up "stabilizes_delta") 1.0
        && Float.equal (num follow_up "nodes_recomputed_delta") 0.0))
    ()

(* A subscriber that stops reading does not hold up one that reads.

   Phase 2's final review measured the stall: frames were written with
   pushback and the broadcaster waited on every write, so one stream that
   stopped reading delayed the next frame for all of them. What [broadcast]
   returns must now already be determined -- the loop waits on nobody -- and
   the subscriber that fell [subscriber_backlog] frames behind is closed and
   dropped, while the one that reads has every frame.

   No socket and no scheduler. [subscribe] answers with an already-determined
   response whose body is the subscriber's reader (the CORS test above peeks
   the same route), so both ends of both pipes are in hand, and
   [Pipe.read_now'] is the reading subscriber reading. *)
let test_a_stalled_subscriber_does_not_stall_a_reading_one () =
  with_logged_server
    ~f:(fun server _graph _log ->
      let open_stream () =
        match Async.Deferred.peek (Server.subscribe server) with
        | Some (_, `Pipe reader) -> reader
        | _ -> Alcotest.fail "/api/stream did not answer with a pipe"
      in
      let reading = open_stream () in
      let stalled = open_stream () in
      let read_all () =
        match Async.Pipe.read_now' reading with
        | `Ok frames -> Queue.length frames
        | `Nothing_available | `Eof -> 0
      in
      (* The welcome frame. *)
      let received = ref (read_all ()) in
      let never_waited = ref true in
      let frames = Server.subscriber_backlog + 3 in
      for i = 1 to frames do
        let sent = Server.broadcast server (Printf.sprintf "{\"frame\":%d}" i) in
        if not (Async.Deferred.is_determined sent) then never_waited := false;
        received := !received + read_all ()
      done;
      Alcotest.(check bool) "no broadcast waited on a reader" true !never_waited;
      Alcotest.(check int)
        "the reader has the welcome frame and every frame" (frames + 1) !received;
      Alcotest.(check bool)
        "the stalled subscriber was let go" true
        (Async.Pipe.is_closed stalled);
      Alcotest.(check bool)
        "holding no more than its budget" true
        (Async.Pipe.length stalled <= Server.subscriber_backlog);
      Alcotest.(check int)
        "and only the reader is still subscribed" 1
        (Server.subscriber_count server))
    ()

(* Every scalar node has a value, and the values are the snapshot's.

   The topology says which nodes have a unit a number can carry; the encoder
   says what the numbers are; this is the assertion that the two agree, so
   the drawing can never point at a scalar node and find nothing under it.
   [rate] is the one key allowed to exist without a node: on a book with no
   contracts nothing reads the rate cell, so it is not in the graph, but the
   cell is real and its value is emitted anyway. *)
let test_every_scalar_node_has_a_by_node_value () =
  with_graph
    ~f:(fun graph ->
      let s = Graph.snapshot graph in
      let json = Server.json_of_snapshot ~graph ~factor:"SYNTHETIC" s in
      let by_node = field_exn json "by_node" in
      let keys =
        match by_node with
        | `Assoc kv -> List.map kv ~f:fst
        | _ -> Alcotest.fail "by_node is not an object"
      in
      let topo = Graph.topology graph in
      let names = String.Set.of_list (Graph.Topology.names topo) in
      List.iter (Graph.Topology.nodes topo) ~f:(fun n ->
          if Graph.Node_name.is_scalar (Graph.Topology.Node.unit n) then
            Alcotest.(check bool)
              (Graph.Topology.Node.name n ^ " has a by_node value")
              true
              (List.mem keys (Graph.Topology.Node.name n) ~equal:String.equal));
      List.iter keys ~f:(fun key ->
          Alcotest.(check bool)
            (key ^ " is a scalar node of this graph, or the rate cell")
            true
            (String.equal key "rate"
            || Set.mem names key
               && Graph.Node_name.is_scalar (Graph.Node_name.unit_of key)));
      Alcotest.(check (float 1e-9)) "exposure:AAPL" 30_000.0 (num by_node "exposure:AAPL");
      Alcotest.(check (float 1e-9)) "price[AAPL]" 150.0 (num by_node "price[AAPL]");
      Alcotest.(check (float 1e-9))
        "qty[XOM] keeps its sign" (-400.0) (num by_node "qty[XOM]");
      Alcotest.(check (float 1e-9)) "cash" 100_000.0 (num by_node "cash");
      Alcotest.(check (float 1e-9))
        "gross_exposure" 70_000.0
        (num by_node "gross_exposure");
      Alcotest.(check bool)
        "historical_var is a number once warm" true
        (match field_exn by_node "historical_var" with `Float _ -> true | _ -> false);
      (* The rest by hand where the book makes it arithmetic, and every
         singleton against the field the snapshot already publishes under its
         own name: the same number, compared as the bytes on the wire, so a
         null has to be a null on both sides. *)
      Alcotest.(check (float 1e-9))
        "exposure:XOM" (-40_000.0) (num by_node "exposure:XOM");
      Alcotest.(check (float 1e-9)) "price[XOM]" 100.0 (num by_node "price[XOM]");
      Alcotest.(check (float 1e-9)) "qty[AAPL]" 200.0 (num by_node "qty[AAPL]");
      Alcotest.(check (float 1e-9)) "sector:TECH" 30_000.0 (num by_node "sector:TECH");
      Alcotest.(check (float 1e-9))
        "sector:ENERGY" (-40_000.0) (num by_node "sector:ENERGY");
      Alcotest.(check (float 1e-9))
        "net_exposure" (-10_000.0) (num by_node "net_exposure");
      Alcotest.(check (float 1e-9)) "equity" 90_000.0 (num by_node "equity");
      Alcotest.(check (float 1e-9))
        "rate is the cell's" (Graph.rate graph) (num by_node "rate");
      Alcotest.(check (float 1e-9))
        "valuation_days is the cell's" (Graph.valuation_days graph)
        (num by_node "valuation_days");
      let wire j key = Yojson.Safe.to_string (field_exn j key) in
      List.iter
        [
          ("gross_exposure", "gross_exposure");
          ("net_exposure", "net_exposure");
          ("equity", "equity");
          ("current_drawdown", "current_drawdown");
          ("historical_var", "historical_var");
          ("expected_shortfall", "expected_shortfall");
          ("parametric_var", "parametric_var");
          ("parametric_var_ewma", "parametric_var_ewma");
          ("var_notional", "value_at_risk_notional");
          ("es_notional", "expected_shortfall_notional");
          ("portfolio_beta", "portfolio_beta");
          ("diversification_ratio", "diversification_ratio");
          ("portfolio_gamma", "portfolio_gamma");
          ("portfolio_vega", "portfolio_vega");
        ]
        ~f:(fun (node, top) ->
          Alcotest.(check string)
            (node ^ " is the snapshot's " ^ top)
            (wire json top) (wire by_node node));
      (match field_exn json "positions" with
      | `List rows ->
          List.iter rows ~f:(fun row ->
              let symbol =
                match field_exn row "symbol" with `String x -> x | _ -> "?"
              in
              List.iter
                [
                  ("exposure:" ^ symbol, "exposure");
                  ("price[" ^ symbol ^ "]", "price");
                  ("qty[" ^ symbol ^ "]", "qty");
                ]
                ~f:(fun (node, key) ->
                  Alcotest.(check string)
                    (node ^ " is the row's " ^ key)
                    (wire row key) (wire by_node node)))
      | _ -> Alcotest.fail "positions");
      match field_exn json "sectors" with
      | `List rows ->
          List.iter rows ~f:(fun row ->
              let sector =
                match field_exn row "sector" with `String x -> x | _ -> "?"
              in
              Alcotest.(check string)
                ("sector:" ^ sector ^ " is the row's exposure")
                (wire row "exposure")
                (wire by_node ("sector:" ^ sector)))
      | _ -> Alcotest.fail "sectors")
    ()

(* The shares and the ratio, hand-derived on this file's two-name book.

   AAPL 30,000 and XOM -40,000 on returns r and -r: weights 3/7 and -4/7,
   every pair perfectly correlated, so the book behaves as one asset with
   sigma_p = sigma. marginal(AAPL) = sigma, marginal(XOM) = -sigma;
   component = weight x marginal = 3/7 sigma and 4/7 sigma; they sum to
   sigma_p. So risk_share is 3/7 and 4/7, and risk over money -- share over
   |weight| -- is exactly 1.0 for both: with correlations at one, every
   dollar carries the same risk. The interesting books are the ones where it
   is not 1.0, and this is the reference they are read against. *)
let test_risk_share_and_risk_over_money () =
  with_graph
    ~f:(fun graph ->
      let json = encode graph in
      let positions =
        match field_exn json "positions" with
        | `List ps -> ps
        | _ -> Alcotest.fail "positions"
      in
      let by_symbol s =
        List.find_exn positions ~f:(fun p ->
            match field_exn p "symbol" with `String x -> String.equal x s | _ -> false)
      in
      let a = by_symbol "AAPL" and x = by_symbol "XOM" in
      Alcotest.(check (float 1e-9)) "AAPL risk_share" (3.0 /. 7.0) (num a "risk_share");
      Alcotest.(check (float 1e-9)) "XOM risk_share" (4.0 /. 7.0) (num x "risk_share");
      Alcotest.(check (float 1e-9)) "AAPL risk over money" 1.0 (num a "risk_over_money");
      Alcotest.(check (float 1e-9)) "XOM risk over money" 1.0 (num x "risk_over_money");
      Alcotest.(check (float 1e-9)) "price rides on the row" 100.0 (num x "price");
      Alcotest.(check (float 1e-9)) "and qty, signed" (-400.0) (num x "qty");
      Alcotest.(check bool)
        "marginal(XOM) is negative" true
        (Float.( < ) (num x "marginal") 0.0);
      Alcotest.(check bool)
        "standalone(XOM) is positive" true
        (Float.( > ) (num x "standalone") 0.0);
      (* The exact row, and the rest of the derivation above: with sigma =
         marginal(AAPL), marginal(XOM) is -sigma and the standalones are 3/7
         and 4/7 of it. *)
      List.iter positions ~f:(fun p ->
          check_key_set ~what:"position row"
            [
              "symbol";
              "sector";
              "exposure";
              "weight";
              "component_var";
              "price";
              "qty";
              "marginal";
              "standalone";
              "risk_share";
              "risk_over_money";
            ]
            p);
      Alcotest.(check (float 1e-9)) "AAPL price" 150.0 (num a "price");
      Alcotest.(check (float 1e-9)) "AAPL qty" 200.0 (num a "qty");
      let sigma = num a "marginal" in
      Alcotest.(check bool) "sigma is positive" true (Float.( > ) sigma 0.0);
      Alcotest.(check (float 1e-12)) "marginal(XOM) = -sigma" (-.sigma) (num x "marginal");
      Alcotest.(check (float 1e-12))
        "standalone(AAPL) = 3/7 sigma"
        (3.0 /. 7.0 *. sigma)
        (num a "standalone");
      Alcotest.(check (float 1e-12))
        "standalone(XOM) = 4/7 sigma"
        (4.0 /. 7.0 *. sigma)
        (num x "standalone");
      let sectors =
        match field_exn json "sectors" with
        | `List ss -> ss
        | _ -> Alcotest.fail "sectors"
      in
      let shares = List.map sectors ~f:(fun k -> num k "risk_share") in
      Alcotest.(check (float 1e-9))
        "sector shares sum to one" 1.0
        (List.fold shares ~init:0.0 ~f:( +. ));
      List.iter sectors ~f:(fun k ->
          check_key_set ~what:"sector row"
            [ "sector"; "exposure"; "component_var"; "risk_share" ]
            k;
          (* One name per sector, so each sector's share is its name's. *)
          let expected =
            match field_exn k "sector" with
            | `String "TECH" -> 3.0 /. 7.0
            | `String "ENERGY" -> 4.0 /. 7.0
            | other -> Alcotest.failf "unexpected sector %s" (Yojson.Safe.to_string other)
          in
          Alcotest.(check (float 1e-9)) "sector risk_share" expected (num k "risk_share"));
      Alcotest.(check bool)
        "the Euler residual is on the wire and tiny" true
        (Float.( < ) (Float.abs (num json "euler_residual")) 1e-9);
      Alcotest.(check bool)
        "and which matrix was decomposed" true
        (match field_exn json "attribution_covariance" with
        | `String "equal_weighted" -> true
        | _ -> false))
    ()

(* Unknown stays unknown: a share of a total that does not exist yet is null,
   not zero, on every row. *)
let test_risk_share_is_null_while_warming_up () =
  with_graph ~seed:false
    ~f:(fun graph ->
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 200.0);
      let json = encode graph in
      (match field_exn json "positions" with
      | `List ps ->
          List.iter ps ~f:(fun p ->
              List.iter [ "marginal"; "standalone"; "risk_share"; "risk_over_money" ]
                ~f:(fun k ->
                  Alcotest.(check bool)
                    (k ^ " is null while warming up")
                    true
                    (match field_exn p k with `Null -> true | _ -> false)))
      | _ -> Alcotest.fail "positions");
      (match field_exn json "sectors" with
      | `List ks ->
          List.iter ks ~f:(fun k ->
              Alcotest.(check bool)
                "a sector's risk_share is null while warming up" true
                (match field_exn k "risk_share" with `Null -> true | _ -> false))
      | _ -> Alcotest.fail "sectors");
      Alcotest.(check bool)
        "euler_residual is null too" true
        (match field_exn json "euler_residual" with `Null -> true | _ -> false))
    ()

(* The demo's quiet name travels on the frame, so a stale symbol that is stale
   ON PURPOSE can be labelled as such by the page instead of reading as a
   broken feed. Empty on a live server, which has no such name. *)
let test_quiet_is_on_the_wire () =
  with_server ~quiet:[ xom ]
    ~f:(fun server _graph ->
      let json = parse (Server.render server) in
      Alcotest.(check (list string))
        "quiet: [XOM]" [ "XOM" ]
        (match field_exn json "quiet" with
        | `List xs -> List.map xs ~f:(function `String s -> s | _ -> "?")
        | _ -> Alcotest.fail "quiet"))
    ();
  with_server
    ~f:(fun server _graph ->
      let json = parse (Server.render server) in
      Alcotest.(check (list string))
        "quiet: [] by default" []
        (match field_exn json "quiet" with
        | `List xs -> List.map xs ~f:(fun _ -> "x")
        | _ -> [ "?" ]))
    ()

(* /api/ops says which named nodes are hot, from the log forks never reach. *)
let test_ops_reports_the_named_log () =
  with_logged_server
    ~f:(fun server graph log ->
      Graph.apply_tick graph
        { Tick.symbol = aapl; price = Price.of_float 151.0; time = Time.epoch };
      Graph.stabilize graph;
      let _, _, body = respond server "/api/ops" in
      let named = field_exn (field_exn (parse body) "graph") "named" in
      Alcotest.(check bool) "distinct > 0" true (Float.( > ) (num named "distinct") 0.0);
      Alcotest.(check bool)
        "total >= distinct" true
        (Float.( >= ) (num named "total") (num named "distinct"));
      (* The exact object, and every number in it is the log's own. *)
      check_key_set ~what:"graph.named" [ "distinct"; "total"; "hottest" ] named;
      Alcotest.(check int)
        "distinct is the log's"
        (Ohcamel.Recompute_log.distinct log)
        (Int.of_float (num named "distinct"));
      Alcotest.(check int)
        "total is the log's"
        (Ohcamel.Recompute_log.total log)
        (Int.of_float (num named "total"));
      match field_exn named "hottest" with
      | `List entries ->
          Alcotest.(check bool) "at most ten hottest" true (List.length entries <= 10);
          List.iter entries ~f:(fun e ->
              ignore (field_exn e "name" : Yojson.Safe.t);
              ignore (num e "n" : float));
          List.iter entries ~f:(fun e ->
              check_key_set ~what:"hottest entry" [ "name"; "n" ] e);
          Alcotest.(check (list (pair string int)))
            "the log's ten hottest, in the log's order"
            (Ohcamel.Recompute_log.hottest log ~n:10)
            (List.map entries ~f:(fun e ->
                 ( (match field_exn e "name" with `String s -> s | _ -> "?"),
                   Int.of_float (num e "n") )))
      | _ -> Alcotest.fail "hottest is not a list")
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
      Alcotest.test_case "/api/ops shape, and what it must not say" `Quick test_ops_shape;
      Alcotest.test_case "/api/ops takes its feed source from the closure" `Quick
        test_ops_feed_source_comes_from_the_closure;
      Alcotest.test_case "the 404 body lists exactly the routes table" `Quick
        test_the_404_lists_exactly_the_routes;
      Alcotest.test_case "/api/ops and /ops answer through the table" `Quick
        test_the_two_new_routes_answer;
      Alcotest.test_case "CORS survives being read through the table, not just built"
        `Quick test_cors_survives_being_read_through_the_table_and_not_just_built;
      Alcotest.test_case "the server holds the recompute log" `Quick
        test_the_server_holds_the_log;
      Alcotest.test_case "a poller cannot steal the stream's recomputed set" `Quick
        test_a_poller_cannot_steal_the_streams_set;
      Alcotest.test_case "a render-time stabilize produces an empty follow-up frame"
        `Quick test_a_render_time_stabilize_produces_an_empty_follow_up;
      Alcotest.test_case "a subscriber that stops reading does not stall one that reads"
        `Quick test_a_stalled_subscriber_does_not_stall_a_reading_one;
      Alcotest.test_case "every scalar node has a by_node value" `Quick
        test_every_scalar_node_has_a_by_node_value;
      Alcotest.test_case "risk_share and risk over money are the encoder's" `Quick
        test_risk_share_and_risk_over_money;
      Alcotest.test_case "risk_share is null while warming up" `Quick
        test_risk_share_is_null_while_warming_up;
      Alcotest.test_case "the quiet list is on the wire" `Quick test_quiet_is_on_the_wire;
      Alcotest.test_case "/api/ops reports the named log" `Quick
        test_ops_reports_the_named_log;
    ] )
