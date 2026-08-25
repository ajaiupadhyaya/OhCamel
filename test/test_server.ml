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

let suite =
  ( "server",
    [
      Alcotest.test_case "the snapshot parses back as JSON" `Quick test_round_trips;
      Alcotest.test_case "values and positions" `Quick test_values;
      Alcotest.test_case "each limit carries its unit" `Quick test_limit_units;
      Alcotest.test_case "unknown arrives as null, never as zero" `Quick
        test_unknown_is_not_zero;
      Alcotest.test_case "feed health shape" `Quick test_feed_health_shape;
      Alcotest.test_case "non-finite floats become null" `Quick
        test_non_finite_becomes_null;
      Alcotest.test_case "SSE event framing" `Quick test_sse_framing;
    ] )
