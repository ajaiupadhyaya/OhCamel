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
module Argument_html = Ohcamel.Argument_html
module Risk_html = Ohcamel.Risk_html

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

(* web/quoted.json is embedded inside <script type="application/json">, and
   an HTML parser ends that element at the first "</script" whatever the JSON
   around it says. A quoted note that ever contains one would cut the block
   short and argument.js would silently compare nothing. *)
let test_the_quoted_block_cannot_end_early () =
  Alcotest.(check bool)
    "no </script in web/quoted.json" false
    (String.Caseless.is_substring Ohcamel.Quoted.json ~substring:"</script")

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
        (* The masthead partial is now catted before web/index.html, so
           <header> and its banner precede index.html's own comments. *)
        "<header>";
        "id=\"halt\"";
        "<nav class=\"sitenav\"";
        "THE DESIGN, AND WHY IT IS THIS AND NOT A TRADING TERMINAL PASTICHE";
        "THE SUCCESSOR, 2026-09-02";
        "<table id=\"pos\"></table>";
        (* The essay, its comment and its quoted-JSON pin all left with it in
           this phase: the dashboard's own markup now runs straight from the
           positions table to its footer, with no article and no script#quoted
           in between. *)
        "</footer>";
        "<script>";
        "\"use strict\"";
        (* dashboard.js's own subscription -- the last thing its file does,
           and the one marker here that only it still makes. charts.js and
           argument.js, and the marker that used to name argument.js's own
           subscription, moved to the argument page with the essay they
           serve; window.OhCamelCharts is no longer catted into this page at
           all. *)
        "window.OhCamelStream.onTopology(buildFigure)";
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
        "<nav class=\"sitenav\"";
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

(* The head and the stylesheet are authored once in web/ and catted into every
   rule -- three now that argument_html.ml has one of its own. If someone
   forks them -- a second <style> block on one page, a different <title> --
   the pages stop sharing a design and nothing fails, because each page still
   renders. Comparing the prefixes is the cheapest way to see the divergence.
   The boundary string is the one every rule echoes. *)
let test_all_three_pages_share_one_head_and_one_stylesheet () =
  let boundary = "</style>\n</head>\n<body>\n" in
  let head_and_style ~name page =
    match String.substr_index page ~pattern:boundary with
    | Some i -> String.sub page ~pos:0 ~len:(i + String.length boundary)
    | None -> Alcotest.failf "%s: no </style></head><body> boundary in the page" name
  in
  let dashboard = head_and_style ~name:"dashboard" Dashboard_html.page in
  let ops = head_and_style ~name:"ops" Ops_html.html in
  let argument = head_and_style ~name:"argument" Argument_html.page in
  Alcotest.(check string)
    "the dashboard and the ops page carry byte-for-byte the same head and stylesheet"
    dashboard ops;
  Alcotest.(check string)
    "the dashboard and the argument page carry byte-for-byte the same head and stylesheet"
    dashboard argument

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
  (* Moved here by Phase 3, which corrected README.md, docs/status.md and
     web/quoted.json together: 1272 is the current node set at 400 names, the
     five option singletons included. *)
  Alcotest.(check int)
    "scaling at 400 names: nodes in graph" 1272
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

(* Script order is load order. graph.js reads window.OhCamelFormat at load,
   shared.js formats with it and walks graph.js's closure on every frame,
   stream.js drives shared.js, and each page's own renderer subscribes to
   stream.js at load -- so the markers below must be in the page in this
   order, and a rule that catted them differently would fail here rather than
   in a browser console.

   charts.js and argument.js moved to the argument page with the essay in
   this phase, so the dashboard's list now ends with its own subscription --
   the last thing dashboard.js's file does -- rather than with a marker from a
   script it no longer loads.

   The argument page gets its own arm: its shared client loads in the same
   order, then graph.js (its first figure draws the graph), then charts.js,
   then argument.js, which is pinned by the subscription it makes rather than
   by an export, because it no longer has one -- the stream calls frame() and
   ops() now, and a global kept alive only so this list could find it would be
   a test steering the source. The quoted JSON pin is asserted first because
   argument.js reads it at load, before making that subscription; and
   OhCamelStream.start() is last, opening the connection only after every
   subscriber on the page has registered. *)
let test_the_page_scripts_are_in_order () =
  markers_in_order Dashboard_html.page ~name:"dashboard"
    ~markers:
      [
        "window.OhCamelFormat =";
        "window.OhCamelShared =";
        "new EventSource(";
        "window.OhCamelStream =";
        "window.OhCamelGraph =";
        "window.OhCamelStream.onTopology(buildFigure)";
      ];
  markers_in_order Argument_html.page ~name:"the argument page"
    ~markers:
      [
        "<script id=\"quoted\"";
        "window.OhCamelFormat =";
        "window.OhCamelShared =";
        "new EventSource(";
        "window.OhCamelStream =";
        "window.OhCamelGraph =";
        "window.OhCamelCharts =";
        "OhCamelStream.onFrame(frame)";
        "OhCamelStream.start()";
      ]

(* The shared client, in the order a page must load it: the formatters before
   the state that formats with them, the state before the stream that drives
   it, the stream before the renderer that subscribes, and start() last. A
   script that called OhCamelStream.onFrame before the object existed would
   throw on load, and the page would be blank with one line in a console
   nobody has open. *)
let test_the_shared_client_loads_in_order () =
  markers_in_order Dashboard_html.page ~name:"the dashboard"
    ~markers:
      [
        "function markCurrentNavLink";
        "window.OhCamelFormat";
        "window.OhCamelShared";
        "window.OhCamelStream";
        "OhCamelStream.onFrame(";
        "OhCamelStream.start()";
      ]

(* The shell. The masthead and the banners are one partial, catted into the
   pages whose client writes into them; the nav is catted into every page,
   including the ops page, which has a masthead of its own and keeps it. A rule
   that forgot the nav still compiles and still serves -- a page with no way to
   leave it. *)
let nav_markers =
  [
    "<nav class=\"sitenav\"";
    "href=\"/\"";
    "href=\"/risk\"";
    "href=\"/execution\"";
    "href=\"/argument\"";
    "href=\"/ops\"";
  ]

let test_the_dashboard_carries_the_shell () =
  markers_in_order Dashboard_html.page ~name:"the dashboard"
    ~markers:
      ([ "<header>"; "id=\"mode\""; "id=\"halt\""; "id=\"warn\"" ]
      @ nav_markers
      @ [ "function markCurrentNavLink" ])

(* The ops page takes the nav and nothing else: it has its own masthead, with
   its own ids, and its own stream. One page deliberately not sharing is
   cheaper than two mastheads or two connections. *)
let test_the_ops_page_carries_the_nav_and_keeps_its_masthead () =
  (* Scripts are catted after all body markup on both pages, so shell.js's
     function always comes after ops.html's own masthead, not before it --
     the nav sits above the masthead, and the shell script sits below both. *)
  markers_in_order Ops_html.html ~name:"the ops page"
    ~markers:(nav_markers @ [ "id=\"h-mode\""; "function markCurrentNavLink" ]);
  match String.substr_index Ops_html.html ~pattern:"id=\"halt\"" with
  | None -> ()
  | Some i ->
      Alcotest.failf
        "the ops page carries the engine pages' banner at byte %d, which nothing on it \
         fills"
        i

(* The essay's own page. The quoted JSON has to arrive before the script that
   reads it: argument.js fills each figure from that pin and prints one line
   saying whether the two agree, and a script that ran first would fill nothing
   and report no disagreement -- which is the one failure this page exists to
   catch. *)
let test_the_argument_page_is_the_essay () =
  markers_in_order Argument_html.page ~name:"the argument page"
    ~markers:
      ([ "<nav class=\"sitenav\""; "<article id=\"argument\"" ]
      @ List.init 9 ~f:(fun i -> Printf.sprintf "id=\"s%02d\"" (i + 1))
      @ [ "<script id=\"quoted\""; "window.OhCamelStream"; "OhCamelStream.start()" ])

(* And the Desk page is not still carrying it. A move that copied would leave
   both pages green on every other assertion in this file. *)
let test_the_dashboard_no_longer_carries_the_essay () =
  List.iter [ "<article id=\"argument\""; "id=\"s01\""; "<script id=\"quoted\"" ]
    ~f:(fun marker ->
      match String.substr_index Dashboard_html.page ~pattern:marker with
      | None -> ()
      | Some i ->
          Alcotest.failf
            "the dashboard still carries %S at byte %d, so the essay was copied rather \
             than moved"
            marker i)

(* The risk page: the ledger, the factor, the Greeks and the suite, moved off
   the Desk page and given their own route. Pinned by the same shell every
   other page carries, plus the four sections' own ids, plus the marker that
   says its own frame subscriber has registered before start() opens the
   connection. *)
let test_the_risk_page_has_its_sections () =
  markers_in_order Risk_html.page ~name:"the risk page"
    ~markers:
      [
        "<nav class=\"sitenav\"";
        "id=\"ledger\"";
        "id=\"factor\"";
        "id=\"greeks\"";
        "id=\"stress\"";
        "window.OhCamelShared";
        "OhCamelStream.onFrame(";
        "OhCamelStream.start()";
      ]

(* The ledger left the Desk page. Two renderings of one ledger would be one
   rendering nobody looks at, and that is the one that rots. *)
let test_the_dashboard_no_longer_carries_the_ledger () =
  match String.substr_index Dashboard_html.page ~pattern:"id=\"limits\"" with
  | None -> ()
  | Some i -> Alcotest.failf "the dashboard still carries the limits table at byte %d" i

let suite =
  ( "embedded_assets",
    [
      Alcotest.test_case "the document is assembled in the rule's order" `Quick
        test_the_document_is_assembled_in_order;
      Alcotest.test_case "the ops page is assembled in the rule's order" `Quick
        test_the_ops_page_is_assembled_in_order;
      Alcotest.test_case "all three pages share one head and one stylesheet" `Quick
        test_all_three_pages_share_one_head_and_one_stylesheet;
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
      Alcotest.test_case
        "dashboard: format, shared, stream, graph, in that order; argument page: format, \
         shared, stream, graph, charts, argument, in that order"
        `Quick test_the_page_scripts_are_in_order;
      Alcotest.test_case "the shared client loads in order" `Quick
        test_the_shared_client_loads_in_order;
      Alcotest.test_case "the dashboard carries the shell" `Quick
        test_the_dashboard_carries_the_shell;
      Alcotest.test_case "the ops page carries the nav and keeps its masthead" `Quick
        test_the_ops_page_carries_the_nav_and_keeps_its_masthead;
      Alcotest.test_case "the quoted block cannot be ended early" `Quick
        test_the_quoted_block_cannot_end_early;
      Alcotest.test_case "the argument page is the essay" `Quick
        test_the_argument_page_is_the_essay;
      Alcotest.test_case "the dashboard no longer carries the essay" `Quick
        test_the_dashboard_no_longer_carries_the_essay;
      Alcotest.test_case "the risk page has its sections" `Quick
        test_the_risk_page_has_its_sections;
      Alcotest.test_case "the dashboard no longer carries the ledger" `Quick
        test_the_dashboard_no_longer_carries_the_ledger;
    ] )
