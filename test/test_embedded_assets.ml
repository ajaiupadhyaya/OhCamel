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
module Execution_html = Ohcamel.Execution_html
module Research_html = Ohcamel.Research_html

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
        "FIGURE 2, 2026-09-24";
        "<figure id=\"scope\"";
        "<table id=\"pos\"></table>";
        (* The essay, its comment and its quoted-JSON pin all left with it in
           this phase: the dashboard's own markup now runs straight from the
           positions table to its footer, with no article and no script#quoted
           in between. *)
        "</footer>";
        "<script>";
        "\"use strict\"";
        (* desk.js's own subscription -- the last thing its file does,
           and the one marker here that only it still makes. charts.js and
           argument.js, and the marker that used to name argument.js's own
           subscription, moved to the argument page with the essay they
           serve; window.OhCamelCharts is no longer catted into this page at
           all. *)
        (* scope.js before desk.js: desk.js calls window.OhCamelScope per
           frame, so the object has to exist by the first one. *)
        "root.OhCamelScope = api";
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

(* Six pages, six titles. One title for six pages is a history and a set of
   bookmarks that cannot tell them apart, and a screen reader that announces the
   same name on arrival at each. *)
let test_each_page_names_itself () =
  List.iter
    [
      (Dashboard_html.page, "<title>OhCamel — Desk</title>", "the desk page");
      (Risk_html.page, "<title>OhCamel — Risk</title>", "the risk page");
      (Execution_html.page, "<title>OhCamel — Execution</title>", "the execution page");
      (Research_html.page, "<title>OhCamel — Research</title>", "the research page");
      (Argument_html.page, "<title>OhCamel — Argument</title>", "the argument page");
      (Ops_html.html, "<title>OhCamel — Ops</title>", "the ops page");
    ]
    ~f:(fun (page, title, name) ->
      match String.substr_index page ~pattern:title with
      | Some _ -> ()
      | None -> Alcotest.failf "%s does not carry %S" name title)

(* The head and the stylesheet are authored once in web/ and catted into every
   rule -- six now that every page has its own route. If someone forks them --
   a second <style> block on one page, a stray rule only one page gets -- the
   pages stop sharing a design and nothing fails, because each page still
   renders. Comparing the prefixes is the cheapest way to see the divergence.
   The boundary string is the one every rule echoes.

   The one line every rule is now ALLOWED to differ on is its own <title>: each
   page echoes that immediately after (cat ../web/head.html), so it sits inside
   this very prefix. It is stripped out by name before the comparison, so the
   assertion stays "the rest of the head and the whole stylesheet are shared
   byte for byte" rather than silently passing because six titles happen to
   make six different strings. *)
let test_all_six_pages_share_one_head_and_one_stylesheet () =
  let boundary = "</style>\n</head>\n<body>\n" in
  let head_and_style ~name page =
    match String.substr_index page ~pattern:boundary with
    | Some i -> String.sub page ~pos:0 ~len:(i + String.length boundary)
    | None -> Alcotest.failf "%s: no </style></head><body> boundary in the page" name
  in
  let strip_title ~name ~title prefix =
    match String.substr_index prefix ~pattern:title with
    | Some i ->
        String.sub prefix ~pos:0 ~len:i
        ^ String.sub prefix
            ~pos:(i + String.length title)
            ~len:(String.length prefix - i - String.length title)
    | None -> Alcotest.failf "%s: does not carry %S" name title
  in
  let pages =
    [
      (Dashboard_html.page, "<title>OhCamel — Desk</title>\n", "dashboard");
      (Risk_html.page, "<title>OhCamel — Risk</title>\n", "risk");
      (Execution_html.page, "<title>OhCamel — Execution</title>\n", "execution");
      (Research_html.page, "<title>OhCamel — Research</title>\n", "research");
      (Argument_html.page, "<title>OhCamel — Argument</title>\n", "argument");
      (Ops_html.html, "<title>OhCamel — Ops</title>\n", "ops");
    ]
  in
  let stripped =
    List.map pages ~f:(fun (page, title, name) ->
        let prefix = head_and_style ~name page in
        (name, strip_title ~name ~title prefix))
  in
  let _, reference = List.hd_exn stripped in
  List.iter stripped ~f:(fun (name, prefix) ->
      Alcotest.(check string)
        (Printf.sprintf
           "%s carries byte-for-byte the same head and stylesheet as the rest, apart \
            from its own <title>"
           name)
        reference prefix)

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
   the last thing desk.js's file does -- rather than with a marker from a
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
    "href=\"/research\"";
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

(* The execution page: what is working, what the fills cost, and what the
   journal recorded, moved off the Desk page and given its own route. The
   blotter and the fills stay on the Desk page -- see the pair of tests below
   -- and what moves here is the analysis: open orders, the TCA summary and
   note, the sessions the journal closed, and the last VaR forecasts. The
   subscription is execution.js's own line, so a rule that stopped catting the
   script -- four empty tables under their headings -- fails here too. *)
let test_the_execution_page_has_its_sections () =
  markers_in_order Execution_html.page ~name:"the execution page"
    ~markers:
      [
        "<nav class=\"sitenav\"";
        "id=\"openorders\"";
        "id=\"tca\"";
        "id=\"sessions\"";
        "id=\"forecasts\"";
        "OhCamelStream.onFrame(poll)";
        "OhCamelStream.start()";
      ]

(* The research page: the sixth, between Execution and Argument in the nav.
   Its five sections in the order the page promises -- the strategies with
   their backtest verdicts, the latest signal, the evidence, the sentence
   that says the comparison with live is A5's, and R8 -- then its own
   subscription, and start() last. No graph.js: nothing here draws a figure
   or reads a stale closure, and a page that carried it would be paying for a
   script it never calls. *)
let test_the_research_page_has_its_sections () =
  markers_in_order Research_html.page ~name:"the research page"
    ~markers:
      [
        "<nav class=\"sitenav\"";
        "href=\"/execution\"";
        "href=\"/research\"";
        "href=\"/argument\"";
        "id=\"strategiessec\"";
        "id=\"strategies\"";
        "id=\"signalsec\"";
        "id=\"signals\"";
        "id=\"deferred\"";
        "id=\"evidencesec\"";
        "id=\"evidence\"";
        "id=\"livesec\"";
        "phase A5";
        "id=\"r8sec\"";
        "id=\"r8\"";
        "window.OhCamelFormat =";
        "window.OhCamelShared =";
        "window.OhCamelStream =";
        "\"/api/research/evidence\"";
        "\"/api/research\"";
        "OhCamelStream.onFrame(onFrame)";
        "OhCamelStream.start()";
      ];
  match String.substr_index Research_html.page ~pattern:"window.OhCamelGraph =" with
  | None -> ()
  | Some i -> Alcotest.failf "the research page carries graph.js at byte %d" i

(* Text only, never markup: every string on this page came from a server --
   a strategy's name, a judgement's detail, a manifest's notes -- and the
   manifests are files a research run writes. So research.js, from its first
   line to the start() that ends the page, names none of the ways a string
   becomes markup. stream.js, earlier on the page, writes one constant into
   #feed with innerHTML; that is its own, fixed, and before this script. *)
let test_the_research_script_writes_no_markup () =
  let page = Research_html.page in
  let from =
    match String.substr_index page ~pattern:"/* Research: /api/research" with
    | Some i -> i
    | None -> Alcotest.fail "the research page does not carry research.js"
  in
  let script = String.drop_prefix page from in
  List.iter [ "innerHTML"; "outerHTML"; "insertAdjacentHTML"; "document.write"; "eval(" ]
    ~f:(fun needle ->
      match String.substr_index script ~pattern:needle with
      | None -> ()
      | Some i ->
          Alcotest.failf "research.js writes markup: %S at byte %d" needle (from + i))

(* The acceptance's "an advisory signal shown with its rule", as far as a test
   that runs no browser can hold it: /api/research serves an advisory
   judgement's rule -- "R6", or "sizing" when it passed everything and its
   strategy is advisory, pinned in test_intake.ml -- and research.js has a
   sentence for each, so neither reaches the page as a bare word, and an
   accepted one's rebalance is printed when it is present. Checked in a
   browser against all four, in Task 17's report. *)
let test_the_research_page_names_an_advisory_judgements_rule () =
  List.iter
    [
      "\"advisory at \" + rule";
      "advisory: its strategy's sizing is advisory";
      "\"rejected at \" + rule";
      "kv(dl, \"rebalance\", rb)";
    ] ~f:(fun needle ->
      Alcotest.(check bool)
        ("research.js carries " ^ needle)
        true
        (String.is_substring Research_html.page ~substring:needle))

(* The blotter and the fills stay where §4 puts them, on the Desk page. *)
let test_the_desk_keeps_the_blotter_and_the_fills () =
  List.iter [ "id=\"blotter\""; "id=\"fills\"" ] ~f:(fun marker ->
      match String.substr_index Dashboard_html.page ~pattern:marker with
      | Some _ -> ()
      | None -> Alcotest.failf "the desk page lost %S" marker)

(* The Desk page in reading order: what the account is worth, whether the desk
   may trade and a ticket to act on it, then the picture of the engine, then
   what is held, then what the desk did, then the trail. The ticket sits with
   the account because it is the account's control; the blotter and the fills
   sit after the positions because they are the record of acting on them.
   #ticket, #blotter and #fills are asserted as markers in their own right, so
   a later edit that moved one back inside another section would fail here. *)
let test_the_desk_page_is_in_reading_order () =
  markers_in_order Dashboard_html.page ~name:"the desk page"
    ~markers:
      [
        "<nav class=\"sitenav\"";
        "id=\"desksec\"";
        "id=\"desk\"";
        "id=\"ticket\"";
        "id=\"graph\"";
        "id=\"pos\"";
        "id=\"book\"";
        "id=\"tradesec\"";
        "id=\"blotter\"";
        "id=\"fills\"";
        "id=\"histsec\"";
      ]

let suite =
  ( "embedded_assets",
    [
      Alcotest.test_case "the document is assembled in the rule's order" `Quick
        test_the_document_is_assembled_in_order;
      Alcotest.test_case "the ops page is assembled in the rule's order" `Quick
        test_the_ops_page_is_assembled_in_order;
      Alcotest.test_case "each page names itself" `Quick test_each_page_names_itself;
      Alcotest.test_case "all six pages share one head and one stylesheet" `Quick
        test_all_six_pages_share_one_head_and_one_stylesheet;
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
      Alcotest.test_case "the execution page has its sections" `Quick
        test_the_execution_page_has_its_sections;
      Alcotest.test_case "the desk keeps the blotter and the fills" `Quick
        test_the_desk_keeps_the_blotter_and_the_fills;
      Alcotest.test_case "the desk page is in reading order" `Quick
        test_the_desk_page_is_in_reading_order;
      Alcotest.test_case "the research page has its sections" `Quick
        test_the_research_page_has_its_sections;
      Alcotest.test_case "the research script writes no markup" `Quick
        test_the_research_script_writes_no_markup;
      Alcotest.test_case "the research page names an advisory judgement's rule" `Quick
        test_the_research_page_names_an_advisory_judgements_rule;
    ] )
