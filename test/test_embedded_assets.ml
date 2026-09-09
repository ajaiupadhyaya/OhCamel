(* The generated modules, and whether the build actually produced them.

   Five modules in lib/ have no source file. dashboard_html.ml, ops_html.ml,
   quoted.ml, crisis_csv.ml and build_info.ml are written by rules in lib/dune
   that concatenate files out of web/ and docs/crisis/, and that puts a whole
   class of failure past the type checker. A rule that cats the wrong file, or
   cats two in the wrong order, or drops one, still produces a `string`, still
   compiles, and still serves: a page with no stylesheet, or a page whose
   <script> arrives before the markup it addresses, is a green build.

   So what is asserted here is STRUCTURE, not content -- that the pieces are
   present and in the order the rule claims. The page itself is not retyped,
   because a test that repeated the page would be a second copy of the page and
   would need editing every time the first one did.

   THE QUOTED CELLS ARE PINS, NOT DERIVATIONS. The `quoted` cases added in a
   later task check numbers transcribed by hand out of README.md. They are
   reproducibility pins in the sense of the amendment to invariant 7 recorded
   in docs/superpowers/specs/2026-09-02-the-page-design.md -- values captured
   from a published run and held so a transcription slip is noticed -- and
   they are not hand-derived. They do not count among this project's
   hand-derived tests. *)

open Core
module Dashboard_html = Ohcamel.Dashboard_html
module Ops_html = Ohcamel.Ops_html

(* Each marker must occur AFTER the previous one, so the search starts past the
   previous hit rather than at zero. That makes the assertion "these appear in
   this order" rather than "these all appear somewhere", which is the thing a
   reordered (cat ...) actually breaks -- and it is robust to a marker that
   legitimately occurs more than once, as `--ground:` does in the two colour
   schemes. *)
let markers_in_order page ~name ~markers =
  let (_ : int * string) =
    List.fold markers ~init:(-1, "the start of the page")
      ~f:(fun (previous, previous_name) marker ->
        match String.substr_index page ~pos:(previous + 1) ~pattern:marker with
        | None ->
            Alcotest.failf "%s: %S does not appear after %s" name marker previous_name
        | Some i -> (i, Printf.sprintf "%S" marker))
  in
  ()

let test_the_document_is_assembled_in_order () =
  markers_in_order Dashboard_html.page ~name:"dashboard"
    ~markers:
      [
        "<!doctype html>";
        "<title>OhCamel";
        "<style>";
        "--ground:";
        "</style>";
        "<body>";
        "THE DESIGN, AND WHY IT IS THIS AND NOT A TRADING TERMINAL PASTICHE";
        "THE SUCCESSOR, 2026-09-02";
        "<header>";
        "<table id=\"pos\"></table>";
        "</footer>";
        "<script>";
        "\"use strict\"";
        "</script>";
        "</html>";
      ];
  Alcotest.(check bool)
    "opens with the doctype and nothing before it" true
    (String.is_prefix Dashboard_html.page ~prefix:"<!doctype html>");
  Alcotest.(check bool)
    "closes with </html> and exactly one newline" true
    (String.is_suffix Dashboard_html.page ~suffix:"</html>\n")

let test_the_ops_page_is_assembled_in_order () =
  markers_in_order Ops_html.html ~name:"ops"
    ~markers:
      [
        "<!doctype html>";
        "<style>";
        "--ground:";
        "</style>";
        "<body>";
        "OhCamel<span>operations</span>";
        "<script>";
        "\"use strict\"";
        "</script>";
        "</html>";
      ];
  Alcotest.(check bool)
    "opens with the doctype and nothing before it" true
    (String.is_prefix Ops_html.html ~prefix:"<!doctype html>");
  Alcotest.(check bool)
    "closes with </html> and exactly one newline" true
    (String.is_suffix Ops_html.html ~suffix:"</html>\n");
  (* The built page, by its load-bearing markup: the two host columns, the
     cannot-see section, the stream it opens, and the two sentences the peer
     column must be able to say. Substrings rather than a retyped page, for the
     reason at the head of this file. The placeholder's own sentence must be
     GONE: a page that says it is not built while serving is the kind of lie
     this page exists against. *)
  List.iter
    [
      "id=\"this-host\"";
      "id=\"peer\"";
      "id=\"cannot\"";
      "the live host is behind a password";
      "unreachable from this browser";
      "new EventSource(\"/api/stream\")";
    ] ~f:(fun needle ->
      Alcotest.(check bool)
        ("the ops page carries " ^ needle)
        true
        (String.is_substring Ops_html.html ~substring:needle));
  Alcotest.(check bool)
    "and no longer says it is not built" false
    (String.is_substring Ops_html.html ~substring:"this page is not built yet")

(* The head and the stylesheet are authored once in web/ and catted into both
   rules. If someone forks them -- a second <style> block on one page, a
   different <title> -- the two pages stop sharing a design and nothing fails,
   because each page still renders. Comparing the prefixes is the cheapest way
   to see the divergence. The boundary string is the one the two rules echo. *)
let test_both_pages_share_one_head_and_one_stylesheet () =
  let boundary = "</style>\n</head>\n<body>\n" in
  let head_and_style ~name page =
    match String.substr_index page ~pattern:boundary with
    | Some i -> String.sub page ~pos:0 ~len:(i + String.length boundary)
    | None -> Alcotest.failf "%s: no </style></head><body> boundary in the page" name
  in
  Alcotest.(check string)
    "the two pages carry byte-for-byte the same head and stylesheet"
    (head_and_style ~name:"dashboard" Dashboard_html.page)
    (head_and_style ~name:"ops" Ops_html.html)

module Quoted = Ohcamel.Quoted
module U = Yojson.Safe.Util

(* Parsed once. The string is 8 KB and every case below reads it. *)
let quoted = lazy (Yojson.Safe.from_string Quoted.json)
let rows table = U.to_list (U.member "rows" (U.member table (Lazy.force quoted)))

let row_of table ~key ~value ~estimator =
  match
    List.find (rows table) ~f:(fun r ->
        String.equal (U.to_string (U.member key r)) value
        && String.equal (U.to_string (U.member "estimator" r)) estimator)
  with
  | Some r -> r
  | None ->
      Alcotest.failf "%s: no row for %s = %S, estimator %S" table key value estimator

(* Hand-typed JSON that nothing parses until it is on a web page is hand-typed
   JSON that ships broken. This is the parse. *)
let test_quoted_parses_and_holds_the_four_tables () =
  let json = Lazy.force quoted in
  Alcotest.(check (slist string String.compare))
    "the four quoted tables, and where they were quoted from"
    [ "battery"; "crisis"; "garch"; "machine"; "note"; "quoted_on"; "scaling"; "source" ]
    (U.keys json);
  Alcotest.(check int) "scaling: three book sizes" 3 (List.length (rows "scaling"));
  Alcotest.(check int)
    "battery: three series x three estimators" 9
    (List.length (rows "battery"));
  Alcotest.(check int)
    "crisis: three windows x three estimators" 9
    (List.length (rows "crisis"));
  Alcotest.(check int) "garch: six sample sizes" 6 (List.length (rows "garch"))

(* A row that lost a field renders as a blank cell rather than as a failure, so
   the uniformity of the key sets is asserted rather than the presence of any
   one key. *)
let test_the_quoted_rows_are_uniform () =
  List.iter [ "battery"; "crisis"; "garch"; "scaling" ] ~f:(fun table ->
      let all = rows table in
      let keys r = List.sort (U.keys r) ~compare:String.compare in
      let first = keys (List.hd_exn all) in
      List.iteri all ~f:(fun i r ->
          Alcotest.(check (list string))
            (Printf.sprintf "%s row %d carries the same fields as row 0" table i)
            first (keys r)))

(* PINS, NOT DERIVATIONS. Every value below was transcribed by hand out of
   README.md and is held so that a slip in the transcription is a red test
   rather than a wrong number on a public page.

   The jumps/historical row is the one worth pinning by hand, because it is the
   row where four columns disagree on purpose: zero exceptions in 940 days, a
   Kupiec p that rounds to zero, a duration column that reads `--` because two
   exceptions are needed before a duration exists, and a GREEN Basel zone --
   green because Basel's light is one-sided and only asks about too MANY
   breaches. A transcription that quietly "fixed" any one of those four would
   destroy the argument the fourth test exists to make. *)
let test_the_pinned_cells_match_the_readme () =
  let jh = row_of "battery" ~key:"series" ~value:"jumps" ~estimator:"historical" in
  Alcotest.(check int)
    "jumps/historical: exceptions" 0
    (U.to_int (U.member "exceptions" jh));
  Alcotest.(check (float 1e-12))
    "jumps/historical: Kupiec p" 0.0
    (U.to_number (U.member "kupiec_p" jh));
  Alcotest.(check bool)
    "jumps/historical: the duration test does not apply, and that is null not zero" true
    (match U.member "duration_p" jh with `Null -> true | _ -> false);
  Alcotest.(check string)
    "jumps/historical: Basel zone" "green"
    (U.to_string (U.member "zone" jh));
  Alcotest.(check string)
    "jumps/historical: verdict" "REJECTED"
    (U.to_string (U.member "verdict" jh));
  let cp = row_of "crisis" ~key:"window" ~value:"covid" ~estimator:"parametric" in
  Alcotest.(check int)
    "covid/parametric: worst 21-session burst" 10
    (U.to_int (U.member "burst" cp));
  let garch_60 = List.hd_exn (rows "garch") in
  Alcotest.(check int)
    "the first GARCH row is n = 60, this engine's own return window" 60
    (U.to_int (U.member "n" garch_60));
  Alcotest.(check (float 1e-12))
    "n = 60: persistence mean" 0.556
    (U.to_number (U.member "persistence_mean" garch_60));
  Alcotest.(check (float 1e-12))
    "n = 60: persistence sd -- two thirds of the mean, which is the whole verdict" 0.364
    (U.to_number (U.member "persistence_sd" garch_60));
  Alcotest.(check (float 1e-12))
    "the persistence the study fits back" 0.98
    (U.to_number
       (U.member "persistence" (U.member "truth" (U.member "garch" (Lazy.force quoted)))));
  (* Stale on purpose; see the note in the scaling block. Phase 3 corrects
     README.md, docs/status.md and this file together, and moves this pin. *)
  Alcotest.(check int)
    "scaling at 400 names: nodes in graph, AS THE README STILL SAYS" 1267
    (U.to_int (U.member "nodes_in_graph" (List.last_exn (rows "scaling"))))

module Crisis_csv = Ohcamel.Crisis_csv

(* Three facts about the embedded CSVs, each of which is a way the rule can go
   wrong without failing to compile.

   The first line is load-bearing: crisis_data.ml's [of_string] takes the first
   `#` line as the window's description and the crisis table prints it as a
   heading, so a rule that stripped comments or catted the wrong file would
   retitle a window rather than break.

   The absence of `|` is why the {ohcamel_csv|...|ohcamel_csv} delimiter is safe
   for this data at all: a file of ISO dates and decimal closes has no reason to
   contain a pipe, and if one ever appears the check here is cheaper to read
   than a syntax error inside a generated 104 KB literal. *)
let test_the_crisis_windows_are_in_the_binary () =
  let cases =
    [
      ( "gfc",
        Crisis_csv.gfc,
        "# Global financial crisis: the quant quake, Bear Stearns, Lehman, and the March \
         2009 bottom." );
      ( "covid",
        Crisis_csv.covid,
        "# COVID crash: the fastest 30% drawdown on record, and the recovery." );
      ( "rates_2022",
        Crisis_csv.rates_2022,
        "# 2022 rate shock: a slow grind rather than a spike -- the useful contrast to \
         the other two." );
    ]
  in
  List.iter cases ~f:(fun (name, contents, first_line) ->
      Alcotest.(check string)
        (name ^ ": the first comment line, which becomes the window's description")
        first_line
        (List.hd_exn (String.split_lines contents));
      Alcotest.(check string)
        (name ^ ": the header row, six names inner-joined on their common sessions")
        "date,AAPL,CVX,JPM,MSFT,NVDA,XOM"
        (List.nth_exn (String.split_lines contents) 3);
      Alcotest.(check bool)
        (name ^ ": no pipe, so the quoted-string delimiter cannot be ended by the data")
        false (String.mem contents '|'))

module Build_info = Ohcamel.Build_info

(* The build stamp. Four strings, and the only interesting question about each is
   whether it can lie.

   [git_sha] and [built_at] come from build arguments, and a plain local build
   has none -- so the honest value is the literal "unknown". Not an invented
   date, and not the empty string: the page has to be able to say "this build
   does not know which commit it is", and an empty string renders as an absent
   field rather than as ignorance. Phase 6 reads a `git_sha` of "unknown" on the
   droplet as proof that the args did not reach the container, which only works
   if the default is a word rather than a blank.

   [architecture] and [system] come from %{ocaml-config:...} and are always
   known. They are asserted non-empty and space-free rather than pinned to this
   laptop's `arm64`/`macosx`, because CI runs this suite on ubuntu as well and a
   test that pins the author's machine fails on the Linux leg for a reason that
   has nothing to do with the code. *)
let test_the_build_stamp_can_say_it_does_not_know () =
  let sha = Build_info.git_sha in
  Alcotest.(check bool)
    "git_sha is either the literal \"unknown\" or a 40-character lowercase hex sha" true
    (String.equal sha "unknown"
    || (String.length sha = 40 && String.for_all sha ~f:Char.is_hex_digit_lower));
  Alcotest.(check bool)
    "built_at is non-empty, so an absent build argument reads as ignorance not absence"
    true
    (not (String.is_empty Build_info.built_at));
  List.iter
    [ ("architecture", Build_info.architecture); ("system", Build_info.system) ]
    ~f:(fun (name, value) ->
      Alcotest.(check bool) (name ^ " is non-empty") true (not (String.is_empty value));
      Alcotest.(check bool)
        (name ^ " is one word, so a build line can print it without quoting")
        false
        (String.exists value ~f:Char.is_whitespace))

let suite =
  ( "embedded_assets",
    [
      Alcotest.test_case "the document is assembled in the rule's order" `Quick
        test_the_document_is_assembled_in_order;
      Alcotest.test_case "the ops page is assembled in the rule's order" `Quick
        test_the_ops_page_is_assembled_in_order;
      Alcotest.test_case "both pages share one head and one stylesheet" `Quick
        test_both_pages_share_one_head_and_one_stylesheet;
      Alcotest.test_case "web/quoted.json parses and holds the four tables" `Quick
        test_quoted_parses_and_holds_the_four_tables;
      Alcotest.test_case "the quoted rows are uniform" `Quick
        test_the_quoted_rows_are_uniform;
      Alcotest.test_case "PINS: the transcribed cells match README.md" `Quick
        test_the_pinned_cells_match_the_readme;
      Alcotest.test_case "the three crisis windows are in the binary" `Quick
        test_the_crisis_windows_are_in_the_binary;
      Alcotest.test_case "the build stamp can say it does not know" `Quick
        test_the_build_stamp_can_say_it_does_not_know;
    ] )
