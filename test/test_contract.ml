(* One test per rule, on both sides of its boundary, plus the ordering rule,
   plus the oracle Task 6's Python suite reads the same file to check.

   Every expected outcome here is derivable by hand from the four-date clock
   below; none was produced by running the code and pasting the answer.

   Ported from Alpha's core/test/test_contract.ml (d972f7c), adapted:
   - "open Alpha_core" becomes "module Contract = Ohcamel_desk.Contract",
     following this project's convention (see test_desk_ids.ml,
     test_rules.ml) rather than opening the desk library wholesale.
   - Alpha's "clock" group tested Clock.of_dates/age/length directly -- an
     API this module no longer has, since the clock is now a parameter built
     from just the two projections (latest_bar, bars_after) that R3 and R4
     read (see contract.mli). That group is dropped; [clock] below builds
     the same four-date clock through the new Clock.create, and the R4
     boundary and staleness cases already ported below exercise
     [bars_after] exactly as Alpha's "clock" group exercised [age].
   - "R3: no bars seen means nothing is current", which built
     Contract.Clock.empty, has no port: Clock.create's [latest_bar] is
     mandatory here, so an "empty" clock is not representable. This is a
     concern for whoever wires the journal-backed clock (Task 13) before any
     session exists -- see the Task 5 report.
   - Everything else (accept, reject, order, parse) is copied with only the
     module qualification changed.
   - "oracle": new. Loads every interface/examples/*.json against
     interface/examples/expected.json and checks the core's verdict on each,
     which is ruling 6's cross-language check -- Task 6's Python suite reads
     the same expected.json and checks schema_valid instead. *)

open Core
module Contract = Ohcamel_desk.Contract

(* A minimal, valid signal, with knobs for exactly the fields the rules read.
   Weights are printed with %.6f because OCaml's Float.to_string writes "1."
   which is not JSON. *)
let doc ?(version = 1) ?(strategy = "ma_crossover") ?(as_of = "2020-12-31") ?(seq = 6)
    ?(status = "pass") ?(targets = [ ("SPY", 1.0) ]) () =
  let targets =
    List.map targets ~f:(fun (s, w) -> sprintf {|{"symbol":"%s","weight":%.6f}|} s w)
    |> String.concat ~sep:","
  in
  sprintf
    {|{"schema_version":%d,"strategy":"%s","params_hash":"sha256:%s","as_of":"%s","computed_at":"2026-09-02T00:00:00Z","data_hash":"sha256:%s","sequence":%d,"validation":{"status":"%s","gates_version":"2026-09-02","dsr":0.31,"psr":0.72,"pbo":0.2,"manifest":null},"targets":[%s]}|}
    version strategy (String.make 64 'a') as_of (String.make 64 'b') seq status targets

(* The clock: four consecutive trading days at the end of 2020. A signal
   dated on the 31st is 0 bars old; on the 28th, 3 bars old; on the 24th (a
   date the clock never saw, being Christmas Eve's early close in the
   fixture's absence), 4 bars old. Built through Clock.create rather than
   Alpha's Clock.of_dates: [latest_bar] is the last of the four dates and
   [bars_after d] counts how many of them fall strictly after [d], which is
   exactly what Alpha's [age] computed over the list it kept internally. *)
let clock_dates =
  List.map ~f:Date.of_string [ "2020-12-28"; "2020-12-29"; "2020-12-30"; "2020-12-31" ]

let clock =
  Contract.Clock.create ~latest_bar:(List.last_exn clock_dates) ~bars_after:(fun d ->
      List.count clock_dates ~f:(fun x -> Date.( > ) x d))

let registry : Contract.registry =
  { strategies = [ "ma_crossover" ]; last_sequence = [ ("ma_crossover", 5) ] }

let universe = Contract.default_universe
let max_age = 3

let judge s =
  match Contract.parse_string s with
  | Ok s -> Contract.validate ~clock ~registry ~universe ~max_age s
  | Error e -> failwithf "test document did not parse: %s" e ()

let expect_accept v =
  match v with
  | Contract.Accepted _ -> ()
  | Contract.Rejected (r, why) ->
      Alcotest.failf "expected ACCEPT, got %s: %s" (Contract.Rule.to_string r) why

let expect_rule rule v =
  match v with
  | Contract.Rejected (r, _) ->
      Alcotest.(check string)
        "rule"
        (Contract.Rule.to_string rule)
        (Contract.Rule.to_string r)
  | Contract.Accepted _ ->
      Alcotest.failf "expected %s, got ACCEPT" (Contract.Rule.to_string rule)

(* ------------------------------------------------------------------------ *)
(* The oracle: interface/examples/*.json against expected.json. dune runs
   this suite in _build/default/test, and test/dune's glob dep puts
   interface/ one level up (see test_example_book.ml for the same pattern
   with book.example.sexp). *)

let examples_dir = "../interface/examples/"

let oracle_clock_and_config () =
  let expected = Yojson.Safe.from_file (examples_dir ^ "expected.json") in
  let open Yojson.Safe.Util in
  let clock_json = member "clock" expected in
  let bar_dates =
    member "bar_dates" clock_json |> to_list
    |> List.map ~f:(fun j -> Date.of_string (to_string j))
  in
  let max_age = member "max_age" clock_json |> to_int in
  let clock =
    Contract.Clock.create
      ~latest_bar:(List.max_elt bar_dates ~compare:Date.compare |> Option.value_exn)
      ~bars_after:(fun d -> List.count bar_dates ~f:(fun x -> Date.( > ) x d))
  in
  let registry_json = member "registry" expected in
  let registry : Contract.registry =
    {
      strategies = member "strategies" registry_json |> to_list |> List.map ~f:to_string;
      last_sequence =
        member "last_sequence" registry_json
        |> to_assoc
        |> List.map ~f:(fun (k, v) -> (k, to_int v));
    }
  in
  let universe =
    member "universe" expected |> to_list
    |> List.map ~f:(fun j -> Ohcamel.Types.Symbol.of_string (to_string j))
  in
  let cases = member "cases" expected |> to_assoc in
  (clock, registry, universe, max_age, cases)

let core_label = function
  | Contract.Accepted _ -> "ACCEPT"
  | Contract.Rejected (r, _) -> sprintf "REJECT %s" (Contract.Rule.to_string r)

let test_every_example_matches_the_shared_oracle () =
  let clock, registry, universe, max_age, cases = oracle_clock_and_config () in
  let open Yojson.Safe.Util in
  let failures =
    List.filter_map cases ~f:(fun (name, spec) ->
        let expected_core = member "core" spec |> to_string in
        let text = In_channel.read_all (examples_dir ^ name) in
        match Contract.parse_string text with
        | Error e -> Some (sprintf "%s: did not parse: %s" name e)
        | Ok doc ->
            let got =
              core_label (Contract.validate ~clock ~registry ~universe ~max_age doc)
            in
            if String.equal got expected_core then None
            else
              Some
                (sprintf "%s: expected.json says %s, core says %s" name expected_core got))
  in
  match failures with [] -> () | fs -> Alcotest.failf "%s" (String.concat ~sep:"; " fs)

let t name f = Alcotest.test_case name `Quick f

let suite =
  ( "contract",
    [
      t "a current, validated, well-formed signal" (fun () ->
          expect_accept (judge (doc ())));
      t "R4 boundary: exactly max_age bars old is still fresh" (fun () ->
          expect_accept (judge (doc ~as_of:"2020-12-28" ())));
      t "R5 boundary: sequence one above the last accepted" (fun () ->
          expect_accept (judge (doc ~seq:6 ())));
      t "R7: a short is a weight like any other" (fun () ->
          expect_accept (judge (doc ~targets:[ ("SPY", -0.5); ("TLT", 0.5) ] ())));
      t "R7: flat is a valid target" (fun () ->
          expect_accept (judge (doc ~targets:[] ())));
      t "R1: schema_version 2" (fun () ->
          expect_rule Contract.Rule.R1 (judge (doc ~version:2 ())));
      t "R2: an unregistered strategy" (fun () ->
          expect_rule Contract.Rule.R2 (judge (doc ~strategy:"nobody" ())));
      t "R3: as_of after the latest bar" (fun () ->
          expect_rule Contract.Rule.R3 (judge (doc ~as_of:"2021-01-04" ())));
      t "R4: one bar past max_age" (fun () ->
          expect_rule Contract.Rule.R4 (judge (doc ~as_of:"2020-12-24" ())));
      t "R5: a replayed sequence" (fun () ->
          expect_rule Contract.Rule.R5 (judge (doc ~seq:5 ())));
      t "R5: an ancient sequence" (fun () ->
          expect_rule Contract.Rule.R5 (judge (doc ~seq:1 ())));
      t "R6: unvalidated is advisory" (fun () ->
          expect_rule Contract.Rule.R6 (judge (doc ~status:"unvalidated" ())));
      t "R6: a failed battery is advisory" (fun () ->
          expect_rule Contract.Rule.R6 (judge (doc ~status:"fail" ())));
      t "R7: a symbol outside the universe" (fun () ->
          expect_rule Contract.Rule.R7 (judge (doc ~targets:[ ("TSLA", 1.0) ] ())));
      t "R7: |weight| above one" (fun () ->
          expect_rule Contract.Rule.R7 (judge (doc ~targets:[ ("SPY", 1.5) ] ())));
      t "R7: weights summing above one" (fun () ->
          expect_rule Contract.Rule.R7
            (judge (doc ~targets:[ ("SPY", 0.6); ("TLT", 0.6) ] ())));
      t "R7: absolute weights summing above one" (fun () ->
          expect_rule Contract.Rule.R7
            (judge (doc ~targets:[ ("SPY", 0.6); ("TLT", -0.6) ] ())));
      t "the earliest failing rule is reported: R2 before R6" (fun () ->
          expect_rule Contract.Rule.R2
            (judge (doc ~strategy:"nobody" ~status:"unvalidated" ())));
      t "R3 before R5" (fun () ->
          expect_rule Contract.Rule.R3 (judge (doc ~as_of:"2021-01-04" ~seq:1 ())));
      t "R6 before R7" (fun () ->
          expect_rule Contract.Rule.R6
            (judge (doc ~status:"fail" ~targets:[ ("TSLA", 1.0) ] ())));
      t "a missing required field is an error, not a default" (fun () ->
          let s =
            String.substr_replace_first (doc ()) ~pattern:{|"sequence":6,|} ~with_:""
          in
          match Contract.parse_string s with
          | Error _ -> ()
          | Ok _ -> Alcotest.fail "parsed a document with no sequence");
      t "an integer weight is a number" (fun () ->
          let s =
            String.substr_replace_first (doc ()) ~pattern:{|"weight":1.000000|}
              ~with_:{|"weight":1|}
          in
          expect_accept (judge s));
      t "not JSON is an error" (fun () ->
          match Contract.parse_string "not json" with
          | Error _ -> ()
          | Ok _ -> Alcotest.fail "parsed garbage");
      t "every example matches interface/examples/expected.json's core verdict"
        test_every_example_matches_the_shared_oracle;
    ] )
