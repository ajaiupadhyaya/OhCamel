(* The reports, as records and as the wire format the page reads.

   Nothing here re-derives a risk number. Validation_report, Scaling_probe,
   Options_walk and Garch_study are tested against hand-derived and
   README-pinned values in their own files. What is tested here is the
   ORCHESTRATION -- one call produces all four reports, and no graph they
   created survives it -- and the ENCODER, which is a contract between two
   languages that nothing in between would catch a disagreement in. *)

open Core
module Reports = Ohcamel.Reports
module Server = Ohcamel.Server

(* One compute for the whole file. The scaling probe is cut to one size and
   five ticks: its values are pinned in test_scaling_probe.ml, and the 400-name
   probe here would buy a slower suite and no assertion. *)
let computed = lazy (Reports.compute ~sizes:[ 10 ] ~ticks:5 ())
let json = lazy (Yojson.Safe.from_string (Reports.to_string (Lazy.force computed)))

let field j k =
  match j with
  | `Assoc fields -> (
      match List.Assoc.find fields k ~equal:String.equal with
      | Some v -> v
      | None -> Alcotest.failf "no field %s" k)
  | _ -> Alcotest.failf "not an object looking for %s" k

let list_of = function `List xs -> xs | _ -> Alcotest.fail "not a list"
let int_of = function `Int i -> i | _ -> Alcotest.fail "not an int"
let string_of = function `String s -> s | _ -> Alcotest.fail "not a string"

let test_one_call_produces_all_four () =
  let r = Lazy.force computed in
  Alcotest.(check int) "one scaling row per size asked for" 1 (List.length r.scaling);
  Alcotest.(check bool) "the probe cost the counter something" true (r.scaling_cost > 0);
  Alcotest.(check int) "nine synthetic rows" 9 (List.length r.synthetic.rows);
  Alcotest.(check int) "nine crisis rows" 9 (List.length r.crisis.rows);
  Alcotest.(check int) "four states in the options walk" 4 (List.length r.options.states);
  Alcotest.(check bool)
    "computed_in_ms is a real elapsed time" true
    (Float.( > ) r.computed_in_ms 0.0 && Float.is_finite r.computed_in_ms)

(* An undestroyed report graph keeps its observers alive and recomputes on
   every stabilize in this process forever. Every report graph is destroyed,
   so a fresh compute leaves the process holding exactly the observers it held
   before. (The first compute is forced before the count, so Incremental's own
   one-time setup is not counted against it.) *)
let test_no_report_graph_survives () =
  let _ = Lazy.force computed in
  let before = Ohcamel.Graph.active_observers () in
  let (_ : Reports.t) = Reports.compute ~sizes:[ 10 ] ~ticks:2 () in
  Alcotest.(check int)
    "active observers unchanged by a compute" before
    (Ohcamel.Graph.active_observers ())

(* The string parses (so no NaN reached it), and holds what the page draws:
   eighteen validation rows -- the number smoke.sh asserts on the deployed host --
   hit indices inside each row, and a VaR path only where a timeline needs one. *)
let test_the_wire () =
  let j = Lazy.force json in
  let v = field j "validation" in
  let synthetic = list_of (field (field v "synthetic") "rows") in
  let crisis = list_of (field (field v "crisis") "rows") in
  Alcotest.(check int)
    "eighteen validation rows" 18
    (List.length synthetic + List.length crisis);
  List.iter synthetic ~f:(fun row ->
      Alcotest.(check bool)
        "a synthetic row carries no var path" true
        (match row with
        | `Assoc fs -> not (List.Assoc.mem fs "var" ~equal:String.equal)
        | _ -> false));
  List.iter crisis ~f:(fun row ->
      let n = int_of (field row "observations") in
      Alcotest.(check int)
        "a crisis row's var path is one per forecast" n
        (List.length (list_of (field row "var")));
      List.iter
        (list_of (field row "hits"))
        ~f:(fun h ->
          let h = int_of h in
          Alcotest.(check bool) "hit index inside the window" true (h >= 0 && h < n)));
  List.iter
    (list_of (field (field v "crisis") "windows"))
    ~f:(fun w ->
      Alcotest.(check int)
        "dates and realised align"
        (List.length (list_of (field w "dates")))
        (List.length (list_of (field w "realised"))));
  Alcotest.(check string)
    "options say what they are" "SYNTHETIC"
    (string_of (field (field j "options") "label"));
  Alcotest.(check int)
    "six tenor slots" 6
    (List.length (list_of (field (field (field j "options") "calendar") "buckets")));
  Alcotest.(check int)
    "one scaling row" 1
    (List.length (list_of (field (field j "scaling") "rows")))

let tiny () = Reports.Garch.create ~replications:3 ~sample_sizes:[ 60; 125 ] ()

let test_garch_before_and_after () =
  let g = tiny () in
  let before = Reports.Garch.to_json g in
  Alcotest.(check string)
    "scheduled before start" "scheduled"
    (string_of (field before "status"));
  Alcotest.(check int) "nothing fitted yet" 0 (int_of (field before "done"));
  Alcotest.(check int) "of = replications x sizes" 6 (int_of (field before "of"));
  Alcotest.(check int) "no rows yet" 0 (List.length (list_of (field before "rows")));
  Reports.Garch.run_here g;
  let after = Reports.Garch.to_json g in
  Alcotest.(check string) "done after" "done" (string_of (field after "status"));
  Alcotest.(check int) "every fit counted" 6 (int_of (field after "done"));
  Alcotest.(check int) "one row per size" 2 (List.length (list_of (field after "rows")));
  Alcotest.(check int)
    "the verdict row is the engine's window" 60
    (int_of (field (field after "verdict_row") "n"))

(* The study is the same on a spawned domain as on this one: it takes its own
   Random.State and touches nothing shared. *)
let test_garch_on_a_domain_is_the_same_study () =
  let here = tiny () and there = tiny () in
  Reports.Garch.run_here here;
  let spawned =
    Stdlib.Domain.join (Stdlib.Domain.spawn (fun () -> Reports.Garch.run_study there))
  in
  let rows g =
    match g.Reports.Garch.status with
    | Done (r, _) -> r.Ohcamel.Garch_study.rows
    | _ -> Alcotest.fail "not done"
  in
  Alcotest.(check bool)
    "identical rows" true
    (List.equal
       (fun (a : Ohcamel.Garch_study.Row.t) (b : Ohcamel.Garch_study.Row.t) ->
         a.n = b.n
         && Float.equal a.persistence_mean b.persistence_mean
         && Float.equal a.persistence_sd b.persistence_sd)
       (rows here) spawned.rows)

(* The two routes, over a server built the way run_demo builds one. *)
let test_the_routes () =
  let reports = Lazy.force computed in
  Test_server.with_graph
    ~f:(fun graph ->
      let garch = tiny () in
      let server =
        Server.create ~reports ~garch ~mode:`Demo ~graph ~factor:"SYNTHETIC" ()
      in
      let status, _, body = Test_server.respond server "/api/reports" in
      Alcotest.(check int) "/api/reports 200" 200 status;
      Alcotest.(check string)
        "the cached string, as computed" (Reports.to_string reports) body;
      let status, _, body = Test_server.respond server "/api/reports/garch" in
      Alcotest.(check int) "/api/reports/garch 200 while scheduled" 200 status;
      Alcotest.(check string)
        "and says so" "scheduled"
        (string_of (field (Yojson.Safe.from_string body) "status"));
      let ops =
        Yojson.Safe.from_string (Server.json_of_ops server |> Yojson.Safe.to_string)
      in
      Alcotest.(check string)
        "/api/ops: static ready" "ready"
        (string_of (field (field ops "reports") "static"));
      Alcotest.(check string)
        "/api/ops: garch scheduled" "scheduled"
        (string_of (field (field ops "reports") "garch")))
    ();
  Test_server.with_server
    ~f:(fun server _graph ->
      let status, _, _ = Test_server.respond server "/api/reports" in
      Alcotest.(check int) "a server built without reports says 503" 503 status;
      let status, _, body = Test_server.respond server "/api/reports/garch" in
      Alcotest.(check int) "and garch still answers 200" 200 status;
      Alcotest.(check string)
        "absent" "absent"
        (string_of (field (Yojson.Safe.from_string body) "status")))
    ()

let suite =
  ( "reports",
    [
      Alcotest.test_case "one call produces all four reports" `Quick
        test_one_call_produces_all_four;
      Alcotest.test_case "no report graph survives compute" `Quick
        test_no_report_graph_survives;
      Alcotest.test_case "the wire holds what the page draws" `Quick test_the_wire;
      Alcotest.test_case "garch before and after" `Quick test_garch_before_and_after;
      Alcotest.test_case "garch on a domain is the same study" `Quick
        test_garch_on_a_domain_is_the_same_study;
      Alcotest.test_case "the report routes and the ops slot" `Quick test_the_routes;
    ] )
