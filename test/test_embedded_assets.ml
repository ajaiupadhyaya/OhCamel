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
   reproducibility pins in the sense of invariant 7's exception -- values
   captured from a published run and held so a transcription slip is noticed --
   and they are not hand-derived. They do not count among this project's
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
        | Some i ->
            Alcotest.(check bool)
              (Printf.sprintf "%s: %S at %d follows %s at %d" name marker i previous_name
                 previous)
              true (i > previous);
            (i, Printf.sprintf "%S" marker))
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
  (* Phase 1 builds this module and no route reaches it. Asserting the
     placeholder's own words here is what makes Phase 2's job "replace the body"
     rather than "first find out whether the rule works at all". *)
  Alcotest.(check bool)
    "says, in the page, that it is not built yet" true
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
    ] )
