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
     from the three facts (earliest_bar, latest_bar, bars_after) that R3 and
     R4 read (see contract.mli). That group is dropped; [clock] below builds
     the same four-date clock through the new Clock.create, and the R4
     boundary and staleness cases already ported below exercise
     [bars_after] exactly as Alpha's "clock" group exercised [age].
   - "R3: no bars seen means nothing is current", which built
     Contract.Clock.empty, has no port: Clock.create's [latest_bar] is
     mandatory here, so an "empty" clock is not representable. This is a
     concern for whoever wires the journal-backed clock (Task 14) before any
     session exists -- see the Task 5 report.
   - Everything else (accept, reject, order, parse) is copied with only the
     module qualification changed.
   - "oracle": new. Loads every interface/examples/*.json against
     interface/examples/expected.json and checks the core's verdict on each,
     which is ruling 6's cross-language check -- Task 6's Python suite reads
     the same expected.json and checks schema_valid instead.

   Fix round (four holes a reviewer found in the port above):
   - R7's two NaN/Infinity cases: every ordering comparison against NaN is
     false, so the old ">" tests in contract.ml let a NaN or infinite weight
     through. Two new cases build one from a raw JSON string, since %.6f
     cannot print "NaN".
   - The oracle now asserts, not just uses, that interface/examples/*.json
     (minus expected.json) is exactly the set of expected.json's case keys,
     and that the set is non-empty -- an oracle that silently checked zero
     examples, or skipped a file nobody added to "cases", used to pass. A
     missing file is now a named failure in the list the others come back
     as, not an uncaught exception that aborts before they run.
   - Four new order cases (R1, R3, R4, R5 each before R6) alongside the
     ported R2-before-R6 and R6-before-R7, so a first failure at R6 pins
     that R1 through R5 all passed -- the property Task 14's intake relies
     on when it reports an R6 rejection as "advisory, otherwise sound".
   - Clock.create takes a third fact, [earliest_bar]: an [as_of] before it
     has an age this clock cannot state, and R4 now rejects it outright
     instead of asking [bars_after] for a number it was never built to
     answer for a date that old. Four new cases below (the boundary, the
     day before it, and the two ways [create] now raises). *)

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
  Contract.Clock.create ~earliest_bar:(List.hd_exn clock_dates)
    ~latest_bar:(List.last_exn clock_dates) ~bars_after:(fun d ->
      List.count clock_dates ~f:(fun x -> Date.( > ) x d))

(* A longer clock, so R4's age check is reached: with [clock] above, every
   as_of older than max_age is also before its earliest bar and fails there
   first. Eight trading days around Christmas 2020 (the 25th is a holiday):
   the 24th has four bars after it (28, 29, 30, 31), one past max_age = 3;
   the 28th has three, exactly max_age. *)
let long_clock_dates =
  List.map ~f:Date.of_string
    [
      "2020-12-21";
      "2020-12-22";
      "2020-12-23";
      "2020-12-24";
      "2020-12-28";
      "2020-12-29";
      "2020-12-30";
      "2020-12-31";
    ]

let long_clock =
  Contract.Clock.create ~earliest_bar:(List.hd_exn long_clock_dates)
    ~latest_bar:(List.last_exn long_clock_dates) ~bars_after:(fun d ->
      List.count long_clock_dates ~f:(fun x -> Date.( > ) x d))

let registry : Contract.registry =
  { strategies = [ "ma_crossover" ]; last_sequence = [ ("ma_crossover", 5) ] }

let universe = Contract.default_universe
let max_age = 3

let judge s =
  match Contract.parse_string s with
  | Ok s -> Contract.validate ~clock ~registry ~universe ~max_age s
  | Error e -> failwithf "test document did not parse: %s" e ()

let judge_with_clock c s =
  match Contract.parse_string s with
  | Ok s -> Contract.validate ~clock:c ~registry ~universe ~max_age s
  | Error e -> failwithf "test document did not parse: %s" e ()

(* I4: a clock whose [bars_after] was clearly written with only recent dates
   in mind -- it answers "1 bar old" for any date it does not otherwise
   recognise, including one from long before its own history. A caller that
   judged a signal's age from [bars_after] alone, without also checking
   [earliest_bar], would call a year-old signal "1 bar old" and accept it;
   that is the exact bug this task closes. [bars_after latest_bar] is still
   0, so [create] accepts this clock -- the bug is not in [create]'s two
   invariants, it is in trusting [bars_after] outside the range it was ever
   asked to be right about. *)
let clock_naive_before_its_own_history =
  Contract.Clock.create ~earliest_bar:(Date.of_string "2020-12-28")
    ~latest_bar:(Date.of_string "2020-12-31") ~bars_after:(fun d ->
      if Date.( >= ) d (Date.of_string "2020-12-31") then 0 else 1)

let expect_clock_create_raises ~earliest_bar ~latest_bar ~bars_after =
  match Contract.Clock.create ~earliest_bar ~latest_bar ~bars_after with
  | _ -> Alcotest.fail "expected Clock.create to raise, it returned a clock"
  | exception Failure _ -> ()
  | exception exn -> Alcotest.failf "expected Failure, got %s" (Exn.to_string exn)

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

(* I4: expected.json's earliest bar_dates entry is the oracle's
   [earliest_bar], the same way its max was already [latest_bar]. Checked
   against every example below (see the header comment and the fix report):
   only r4_stale.json's as_of (2020-12-24) falls before it (2020-12-28), and
   its core verdict was already REJECT R4 -- unchanged, since a signal older
   than the clock's history still fails R4, just for the more specific
   reason now. *)
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
      ~earliest_bar:(List.min_elt bar_dates ~compare:Date.compare |> Option.value_exn)
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

(* I2: the set of *.json files actually in interface/examples/, minus
   expected.json itself -- the set the oracle ought to be checking, as
   opposed to whatever subset expected.json's "cases" happens to name. *)
let example_files_on_disk () =
  Sys_unix.ls_dir examples_dir
  |> List.filter ~f:(fun f ->
      String.is_suffix f ~suffix:".json" && not (String.equal f "expected.json"))
  |> String.Set.of_list

let case_keys cases = String.Set.of_list (List.map cases ~f:fst)

(* I2: an oracle that only ever iterates expected.json's "cases" can pass
   while checking nothing -- a case whose file was never committed, or a
   file nobody added to "cases", is invisible to it, and an empty "cases"
   would make it pass vacuously. This asserts both directions of the set
   equality, and that the set is not empty, before
   [test_every_example_matches_the_shared_oracle] below trusts it. *)
let test_the_oracle_checks_every_file_and_only_real_files () =
  let _, _, _, _, cases = oracle_clock_and_config () in
  let keys = case_keys cases in
  let files = example_files_on_disk () in
  Alcotest.(check bool)
    "expected.json names at least one case" true
    (not (Set.is_empty keys));
  if Set.equal files keys then ()
  else
    let only_on_disk = Set.to_list (Set.diff files keys) in
    let only_in_cases = Set.to_list (Set.diff keys files) in
    Alcotest.failf
      "interface/examples/*.json and expected.json's cases disagree -- on disk but not a \
       case: [%s]; a case but no file on disk: [%s]"
      (String.concat ~sep:", " only_on_disk)
      (String.concat ~sep:", " only_in_cases)

let test_every_example_matches_the_shared_oracle () =
  let clock, registry, universe, max_age, cases = oracle_clock_and_config () in
  let open Yojson.Safe.Util in
  let failures =
    List.filter_map cases ~f:(fun (name, spec) ->
        let expected_core = member "core" spec |> to_string in
        (* I2: a row naming a file that is not there is reported here, by
           name, alongside whatever else disagrees -- not an uncaught
           exception that stops the other rows from being checked at all. *)
        match In_channel.read_all (examples_dir ^ name) with
        | exception exn ->
            Some (sprintf "%s: could not be read: %s" name (Exn.to_string exn))
        | text -> (
            match Contract.parse_string text with
            | Error e -> Some (sprintf "%s: did not parse: %s" name e)
            | Ok doc ->
                let got =
                  core_label (Contract.validate ~clock ~registry ~universe ~max_age doc)
                in
                if String.equal got expected_core then None
                else
                  Some
                    (sprintf "%s: expected.json says %s, core says %s" name expected_core
                       got)))
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
      t "R4: an as_of before the clock's history" (fun () ->
          expect_rule Contract.Rule.R4 (judge (doc ~as_of:"2020-12-24" ())));
      t "R4: one bar past max_age, inside the clock's history" (fun () ->
          match judge_with_clock long_clock (doc ~as_of:"2020-12-24" ()) with
          | Contract.Rejected (Contract.Rule.R4, why) ->
              Alcotest.(check bool)
                (sprintf "the age check, not the history check: %s" why)
                true
                (String.is_substring why ~substring:"is 4 bars old")
          | Contract.Rejected (r, why) ->
              Alcotest.failf "expected R4, got %s: %s" (Contract.Rule.to_string r) why
          | Contract.Accepted _ -> Alcotest.fail "expected R4, got ACCEPT");
      t "R4 boundary: exactly max_age bars old, inside the clock's history" (fun () ->
          expect_accept (judge_with_clock long_clock (doc ~as_of:"2020-12-28" ())));
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
      (* I1: every ordering comparison against NaN is false, so R7's old
         "> 1.0" test let a NaN weight through -- it satisfies neither
         "<= 1.0" nor "> 1.0". %.6f cannot print "NaN" or "Infinity", so
         these substitute the raw JSON token directly; Yojson.Safe parses
         both as a float (confirmed against this build, not assumed), so
         [judge] reaches r7 rather than failing to parse. *)
      t "R7: a NaN weight is rejected, not silently accepted" (fun () ->
          let s =
            String.substr_replace_first (doc ()) ~pattern:{|"weight":1.000000|}
              ~with_:{|"weight":NaN|}
          in
          expect_rule Contract.Rule.R7 (judge s));
      t "R7: an infinite weight is rejected, not silently accepted" (fun () ->
          let s =
            String.substr_replace_first (doc ()) ~pattern:{|"weight":1.000000|}
              ~with_:{|"weight":Infinity|}
          in
          expect_rule Contract.Rule.R7 (judge s));
      (* I3: the intake (Task 14) reports an R6 rejection as "advisory, and
         everything else about the signal is otherwise sound" -- true only
         if a first failure at R6 means R1 through R5 all passed. Each case
         below fails both its named rule and R6; if the named rule were not
         checked first, R6 would be reported instead and the test would
         fail. R2-before-R6 and R6-before-R7 are already ported above. *)
      t "R1 before R6" (fun () ->
          expect_rule Contract.Rule.R1 (judge (doc ~version:2 ~status:"unvalidated" ())));
      t "R3 before R6" (fun () ->
          expect_rule Contract.Rule.R3
            (judge (doc ~as_of:"2021-01-04" ~status:"unvalidated" ())));
      t "R4 before R6" (fun () ->
          expect_rule Contract.Rule.R4
            (judge (doc ~as_of:"2020-12-24" ~status:"unvalidated" ())));
      t "R5 before R6" (fun () ->
          expect_rule Contract.Rule.R5 (judge (doc ~seq:5 ~status:"unvalidated" ())));
      (* I4: a signal dated before the clock's earliest recorded bar has an
         age this clock cannot state, and must fail R4 regardless of what
         [bars_after] answers for it -- a desk a few sessions into its life
         must not call a year-old signal fresh. *)
      t
        "R4: one day before earliest_bar is rejected even though bars_after says 1 bar \
         old" (fun () ->
          expect_rule Contract.Rule.R4
            (judge_with_clock clock_naive_before_its_own_history
               (doc ~as_of:"2020-12-27" ())));
      t "R4: as_of equal to earliest_bar is judged on its age, not rejected outright"
        (fun () ->
          expect_accept
            (judge_with_clock clock_naive_before_its_own_history
               (doc ~as_of:"2020-12-28" ())));
      t "Clock.create raises when earliest_bar is after latest_bar" (fun () ->
          expect_clock_create_raises ~earliest_bar:(Date.of_string "2021-01-01")
            ~latest_bar:(Date.of_string "2020-12-31") ~bars_after:(fun _ -> 0));
      t "Clock.create raises when bars_after latest_bar is not 0" (fun () ->
          expect_clock_create_raises ~earliest_bar:(Date.of_string "2020-12-28")
            ~latest_bar:(Date.of_string "2020-12-31") ~bars_after:(fun _ -> 1));
      t "the oracle checks every example on disk, and only real files"
        test_the_oracle_checks_every_file_and_only_real_files;
      t "every example matches interface/examples/expected.json's core verdict"
        test_every_example_matches_the_shared_oracle;
    ] )
