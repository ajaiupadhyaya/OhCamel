# The page — Phase 1: plumbing, no behaviour change — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the served page out of a 767-line OCaml string literal into `web/`, add the four other generated modules later phases need (`Ops_html`, `Quoted`, `Crisis_csv`, `Build_info`) plus `Crisis_data.load_all_embedded`, and prove that the page a browser receives did not change.

**Architecture:** `lib/dashboard_html.ml` today is one `{html|…|html}` literal holding 29,386 bytes of HTML, CSS and JavaScript. This phase splits that literal into `web/head.html`, `web/page.css`, `web/index.html` and `web/dashboard.js` and adds five `(rule …)` stanzas to `lib/dune` that concatenate source files into generated `.ml` modules at build time — so the binary stays self-contained (no asset path, nothing to deploy separately) while the sources become files with syntax highlighting, formatters and readable diffs. Nothing in `lib/server.ml`, `bin/main.ml` or `lib/graph.ml` is touched; `Dashboard_html.page` keeps its name and its bytes.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route.
- No persistence.
- No external asset (no CDN, no web font, no charting library, no framework).
- The live host's gate untouched (no Caddy CORS, no basic-auth matcher, no `/api/up`).
- No invented vol surface on either deployed book.
- No number the process did not produce styled as if it had.
- No second implementation of engine arithmetic.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged.
- The eight invariants in `docs/status.md`.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in repo).
- All tests hermetic.
- The byte-identical stdout gate for the six credential-free modes wherever `bin/main.ml` is touched. *(This phase never touches `bin/main.ml`, so that gate is not triggered; the analogous gate here is the byte-identical served page, Task 2.)*

## How to run anything in this repo

Every command below is run **from the repository root** `/Users/ajaiupadhyaya/Documents/OhCamel`, and every `dune` command needs the local opam switch:

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build
```

`make build`, `make test`, `make fmt`, `make demo` wrap exactly that (`Makefile:9` defines `OPAM_ENV := eval $$(opam env --switch=$(CURDIR) --set-switch)`).

**Two notes on running the server:** `ohcamel demo [port]` takes the port as a bare positional argument (`bin/main.ml:1862`). Start it **detached** — a background Bash task in an agent session takes the server down with it when the task ends — and kill it by PID when done. Every recipe below does that.

## Things this plan verified before it was written

Do not re-litigate these; they are checked facts, each with the command that produced them.

| Claim | How it was checked |
|---|---|
| `(cat a b c)` with several files works under `(lang dune 3.16)` | scratch project, dune 3.24.1, built and inspected |
| `\n` inside a dune quoted string is a real newline | same |
| `(echo "x")` emits exactly `x`, no trailing newline added | same |
| `%{env:OHCAMEL_GIT_SHA=unknown}` resolves to `unknown` with no env var and to the value with one | `OHCAMEL_GIT_SHA=deadbeef dune build`, read the output |
| `%{ocaml-config:architecture}` → `arm64`, `%{ocaml-config:system}` → `macosx` on this machine | same |
| A stray `\|ohcamel_html}` inside the concatenated text is a **syntax error**, not a truncation | injected one, got `File "lib/gen.ml", line 4 … Error: Syntax error` |
| The four-file split reassembles to the current page **byte for byte** (29,386 bytes) | reassembled with `cat`/`printf` and `diff`ed |
| The three CSVs total 104,722 bytes and contain **zero** `\|` characters | `wc -c`, `grep -c '\|'` |
| `.dockerignore` with `docs/` followed by `!docs/crisis/*.csv` re-includes exactly the three CSVs under BuildKit | real `docker build` with `RUN find . -type f`, Docker 29.7.2 |
| dune refuses a rule target that shadows a source file, with a usable message | created both, got `Error: Multiple rules generated for _build/default/lib/dashboard_html.ml … Hint: rm -f lib/dashboard_html.ml` |
| The finished `lib/dune` is `dune fmt`-clean and the `;` comment block survives formatting verbatim | `dune build @fmt` on the exact final text |

## Naming note, read before Task 4

The spec's *Routes* section writes the ops page's value as `Ops_html.page`. **This plan produces `Ops_html.html : string`**, because that is the interface Phases 2–6 were briefed against. `Dashboard_html.page` keeps the name `page` because `lib/server.ml:443` already reads it and this phase changes no behaviour. The two modules therefore disagree on the name of their one value; that is deliberate and it is the only place it matters.

## Two kinds of test in this plan

Most tasks below are build-system plumbing, and the honest test for "does the build produce the right bytes" is a shell command with an exact expected output, not an alcotest case. Where a task produces an OCaml value that later phases call, the test is real alcotest code in `test/test_embedded_assets.ml` or `test/test_crisis_data.ml` and it is written first. Both kinds are marked.

---

### Task 1: `web/` — the page split into the four files it always was

**Files:**
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/head.html`
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/page.css`
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/index.html`
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/dashboard.js`
- Read only: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dashboard_html.ml` (unchanged this task)
- Test: a shell gate, no OCaml (nothing compiles differently yet)

**Interfaces:**
- Consumes: `lib/dashboard_html.ml`'s single binding, which today is exactly `let page =` (line 49), `  {html|<!doctype html>` (line 50) … `</html>` (line 766), `|html}` (line 767).
- Produces: four source files whose concatenation, with five structural lines supplied by the dune rule in Task 2, is byte-for-byte today's page. Nothing OCaml consumes them yet.

The split points are the five structural lines that open or close a region, and they go into the dune rule rather than into any file, so that no file both opens and closes a tag:

| Line in `lib/dashboard_html.ml` | Content | Goes to |
|---|---|---|
| 50 (after `  {html\|`) – 56 | `<!doctype html>` … `<link rel="icon" …>` | `web/head.html` |
| 57 | `<style>` | the rule |
| 58 – 259 | the CSS, `:root {` … `a { color: inherit; }` | `web/page.css` |
| 260 – 262 | `</style>` `</head>` `<body>` | the rule |
| 263 – 304 | the body markup, `<header>` … `</footer>` | `web/index.html` |
| 305 | `<script>` | the rule |
| 306 – 763 | the client, `(function () {` … `})();` | `web/dashboard.js` |
| 764 – 766 | `</script>` `</body>` `</html>` | the rule |

- [ ] **Step 1: Write the failing test** — the reassembly gate. Run this now; it must fail, because `web/` does not exist yet.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel

# Record the commit this phase starts from, and extract the page exactly as it
# is served today. Everything downstream diffs against this file. If /tmp is
# wiped later, regenerate it with the same two commands -- the base commit is
# the parent of the commit that added web/head.html:
#   git log --diff-filter=A --format=%H -- web/head.html   (then that commit^)
git rev-parse HEAD | tee /tmp/ohcamel-phase1-base.txt
git show "$(cat /tmp/ohcamel-phase1-base.txt):lib/dashboard_html.ml" \
  | sed -n '50,766p' | sed '1s/^  {html|//' > /tmp/ohcamel-page-before.html
wc -c /tmp/ohcamel-page-before.html

# The gate: web/ plus the five structural lines must reassemble to that file.
{ cat web/head.html
  printf '<style>\n'
  cat web/page.css
  printf '</style>\n</head>\n<body>\n'
  cat web/index.html
  printf '<script>\n'
  cat web/dashboard.js
  printf '</script>\n</body>\n</html>\n'
} > /tmp/ohcamel-page-reassembled.html
cmp /tmp/ohcamel-page-before.html /tmp/ohcamel-page-reassembled.html \
  && echo "REASSEMBLY OK"
```

- [ ] **Step 2: Run it and see it fail.** Run the block above. Expected: `29386 /tmp/ohcamel-page-before.html`, then four `cat: web/…: No such file or directory` lines, then `cmp: EOF on /tmp/ohcamel-page-reassembled.html` and **no** `REASSEMBLY OK`.

- [ ] **Step 3: Minimal implementation** — cut the four files out of the literal.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
mkdir -p web
sed -n '50,56p'   lib/dashboard_html.ml | sed '1s/^  {html|//' > web/head.html
sed -n '58,259p'  lib/dashboard_html.ml > web/page.css
sed -n '263,304p' lib/dashboard_html.ml > web/index.html
sed -n '306,763p' lib/dashboard_html.ml > web/dashboard.js
wc -c web/head.html web/page.css web/index.html web/dashboard.js
```

Expected byte counts, exactly: `518` `7821` `1412` `19568`.

- [ ] **Step 4: Run the tests and see them pass.** Re-run the gate block from Step 1 (the `git show` half is idempotent). Expected last line: `REASSEMBLY OK`. Then confirm nothing about the build changed: `eval $(opam env --switch=$PWD --set-switch) && dune build && dune runtest --force` — green, because `lib/dashboard_html.ml` is still the only thing OCaml reads and `web/` has no `dune` file, so dune treats it as inert source.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/head.html web/page.css web/index.html web/dashboard.js
git commit -m "web: the page cut into the four files it always was, because a 767-line string literal has no highlighter, no formatter and a one-line diff"
```

---

### Task 2: `lib/dune` builds the page from `web/`, and the literal is deleted in the same commit

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune:37` (append after the existing `(library …)` stanza, which ends at line 37)
- Delete: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dashboard_html.ml`
- Test: shell gates — the generated literal, and the page a browser actually receives

**Interfaces:**
- Consumes: `web/head.html`, `web/page.css`, `web/index.html`, `web/dashboard.js` from Task 1.
- Produces: generated module `Dashboard_html` with `val page : string`, byte-identical to the value `lib/server.ml:443` reads today (`Cohttp_async.Server.respond_string ~headers:html_headers Dashboard_html.page`). No signature changes, no callers change.
- **The one-line extension point:** the `(cat ../web/dashboard.js)` line inside the dashboard rule — **line 81** of the finished `lib/dune` (see the map at the end of this plan). Phase 5 replaces that one line with `(cat ../web/format.js ../web/graph.js ../web/charts.js ../web/dashboard.js ../web/argument.js)`. Phase 5 also inserts the quoted-JSON block between `(cat ../web/index.html)` and `(echo "<script>\n")`, which is why `web/quoted.json` is a separate module in this phase and is **not** in the page yet: putting it in now would change the page's bytes and break this task's gate.

- [ ] **Step 1: Append the rule and its rationale to `lib/dune`.** Append exactly this to the end of the file (there is currently no trailing blank line after `  (backend bisect_ppx)))` on line 37):

```
; ---------------------------------------------------------------------------
; Generated modules: the two pages, the crisis cache, the README's quoted
; tables, and the build stamp.
;
; Each of these is a string that a program has to carry but a human has to
; author. dashboard_html.ml was 767 lines of OCaml holding 29 KB of HTML, CSS
; and JavaScript, with editor support for none of the three -- no highlighting,
; no formatter, no linter, and a one-line diff for any change to the page. The
; sources now live in web/ and docs/crisis/ as the files they are, and dune
; concatenates them at build time. What is NOT given up is the property that
; made the literal worth having in the first place: the binary stays
; self-contained, so there is no asset path to get wrong, nothing to forget to
; deploy, and `ohcamel serve` still works from any working directory.
;
; The delimiters are {ohcamel_html|...|ohcamel_html}, {ohcamel_csv|...} and
; {ohcamel_json|...}. A CLOSING delimiter inside a source file ends the literal
; early. In practice the trailing bytes are then not valid OCaml and the build
; fails -- but it fails inside a GENERATED file, at a line number nobody can
; map back to the file they actually edited. CI therefore greps web/ and
; docs/crisis/ for the three closing delimiters and names the real file, and
; that check does not depend on the trailing bytes happening to be ill-formed.
;
; These modules are outside `dune build @fmt` (they are build artefacts, not
; sources) and inside bisect instrumentation, which costs nothing: a string
; literal has no branch to cover.
; ---------------------------------------------------------------------------

(rule
 (targets dashboard_html.ml)
 (deps
  (glob_files ../web/*))
 (action
  (with-stdout-to
   dashboard_html.ml
   (progn
    (echo "let page = {ohcamel_html|")
    (cat ../web/head.html)
    (echo "<style>\n")
    (cat ../web/page.css)
    (echo "</style>\n</head>\n<body>\n")
    (cat ../web/index.html)
    (echo "<script>\n")
    (cat ../web/dashboard.js)
    (echo "</script>\n</body>\n</html>\n|ohcamel_html}\n")))))
```

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build
```

Expected, exactly — this is dune refusing to let a rule target shadow a source file, which is *why* the deletion must be in this commit and not a follow-up:

```
Error: Multiple rules generated for _build/default/lib/dashboard_html.ml:
- lib/dune:66
- file present in source tree
-> required by alias default
Hint: rm -f lib/dashboard_html.ml
```

- [ ] **Step 3: Delete the literal.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git rm lib/dashboard_html.ml
eval $(opam env --switch=$PWD --set-switch) && dune build && echo "BUILD OK"
```

Expected: `BUILD OK`.

- [ ] **Step 4: Format the new dune stanza.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && echo "FMT CLEAN"
```

Expected: `FMT CLEAN`, and `git diff lib/dune` shows **no** change from `make fmt` — the stanza above is already in `dune fmt`'s canonical layout (verified). If it does change something, take dune's version.

- [ ] **Step 5: Gate one — the generated literal is the old page, byte for byte.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
python3 - <<'PY'
src = open('_build/default/lib/dashboard_html.ml', 'rb').read()
pre, post = b'let page = {ohcamel_html|', b'|ohcamel_html}\n'
assert src.startswith(pre), 'the rule no longer opens with the expected binding'
assert src.endswith(post), 'the rule no longer closes with the expected delimiter'
page = src[len(pre):-len(post)]
before = open('/tmp/ohcamel-page-before.html', 'rb').read()
print('GENERATED PAGE BYTE-IDENTICAL:', page == before, len(page), len(before))
PY
```

Expected: `GENERATED PAGE BYTE-IDENTICAL: True 29386 29386`.

- [ ] **Step 6: Gate two — the page a browser actually receives.** The literal being right is not the same claim as the route being right; this runs the real server and reads `/`.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch)
dune build ./bin/main.exe
# Detached, and the built binary rather than `dune exec`, so $! is the pid of
# the process that actually holds the port and `kill` reaches it.
nohup ./_build/default/bin/main.exe demo 8137 > /tmp/ohcamel-demo-8137.log 2>&1 &
echo $! > /tmp/ohcamel-demo-8137.pid
curl -s --retry 30 --retry-delay 1 --retry-connrefused \
  http://127.0.0.1:8137/ > /tmp/ohcamel-page-served.html
kill "$(cat /tmp/ohcamel-demo-8137.pid)"
cmp /tmp/ohcamel-page-before.html /tmp/ohcamel-page-served.html \
  && echo "SERVED PAGE BYTE-IDENTICAL"
```

Expected last line: `SERVED PAGE BYTE-IDENTICAL`. Then `dune runtest --force` — green, unchanged.

- [ ] **Step 7: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/dune
git add -u lib/dashboard_html.ml
git commit -m "web: the page built from web/ by dune, because the literal and its sources cannot both be the source of truth"
```

---

### Task 3: the design essay archived verbatim at the head of `web/index.html`, with its successor beneath it

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/web/index.html` (prepend; the file is 42 lines, currently starting with one blank line then `<header>`)
- Test: shell gate — the page grows by exactly the archived block and by nothing else

**Interfaces:**
- Consumes: `/tmp/ohcamel-page-before.html` and `/tmp/ohcamel-phase1-base.txt` from Task 1; the generated `Dashboard_html` from Task 2.
- Produces: nothing OCaml consumes. It changes the served page's bytes — the **one** intentional byte change in this phase, and the reason this task follows the byte-identity gate rather than preceding it. The block is an HTML comment, so it is invisible in the rendered page and visible in view-source, which is where the argument for the page belongs. It costs 4,480 bytes (78 lines), compressed by Caddy's existing `encode zstd gzip` like the rest of the document.

`docs/superpowers/specs/2026-09-02-the-page-design.md:3` says the essay "is kept — archived verbatim at the head of `web/index.html`, where the successor is". Verbatim here means the 47 lines of text, with only the OCaml comment delimiters `(* ` and ` *)` removed; nothing is reflowed, retitled or corrected.

- [ ] **Step 1: Write the failing test** — build the archive block, and assert it is not in the page yet.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel

# The 47 lines, with only `(* ` off the front of line 1 and ` *)` off the end of
# line 47. lib/dashboard_html.ml is gone from the working tree as of Task 2, so
# this reads it out of the base commit.
git show "$(cat /tmp/ohcamel-phase1-base.txt):lib/dashboard_html.ml" \
  | sed -n '1,47p' | sed '1s/^(\* //' | sed '$s/ \*)$//' > /tmp/ohcamel-essay.txt
wc -l /tmp/ohcamel-essay.txt   # expect 47

cat > /tmp/ohcamel-successor.txt <<'SUCCESSOR'
   THE SUCCESSOR, 2026-09-02

   The essay above describes the page this file replaced, and it is kept rather
   than rewritten because its three moves are kept. The layout is still the
   dependency order, changed values are still marked, and the numbers still lose
   their authority when the feed does. What changes is one level of remove. The
   old page asserted the graph by column order and inferred what had moved by
   diffing successive snapshots: the engine knew exactly which node bodies had
   run, and the client was guessing from the values. The successor draws the
   graph itself, taken from Incremental's own node table, and lights the
   recomputation set the engine reports. The same three moves, done by the thing
   they were always about.

   Two of them get sharper in the process. "Changed values are marked" becomes
   "the engine states what ran", which is a fact rather than an inference. And
   staleness stops being scoped by column and starts being scoped by dependency:
   the downstream closure of price[S] and nothing beside it, which leaves
   limit:aapl-cap at full authority while CVX is quiet. That is the one place the
   CSS above over-dims, and it is corrected by applying the rule the essay
   already states rather than by changing the rule.

   The design is docs/superpowers/specs/2026-09-02-the-page-design.md. This file
   is the body of the document at /; web/page.css is the styling; web/head.html
   is the head both pages share; the client is the .js files beside them. A rule
   in lib/dune concatenates them into lib/dashboard_html.ml at build time, so the
   binary is still self-contained and there is still nothing to fetch.
SUCCESSOR

# `printf --` because `-->` begins with a dash and would otherwise be read as a flag.
{ printf '<!--\n'
  cat /tmp/ohcamel-essay.txt
  printf -- '-->\n\n<!--\n'
  cat /tmp/ohcamel-successor.txt
  printf -- '-->\n'
} > /tmp/ohcamel-archive-block.txt

# Neither half may contain a comment terminator, or the archive would end early
# and dump the rest of itself into the rendered page.
grep -c -- '-->' /tmp/ohcamel-essay.txt /tmp/ohcamel-successor.txt   # expect 0 for both

# The gate itself: removing the block from the served page must give back the
# old page exactly.
python3 - <<'PY'
before = open('/tmp/ohcamel-page-before.html', 'rb').read()
src = open('_build/default/lib/dashboard_html.ml', 'rb').read()
pre, post = b'let page = {ohcamel_html|', b'|ohcamel_html}\n'
after = src[len(pre):-len(post)]
block = open('/tmp/ohcamel-archive-block.txt', 'rb').read()
n = after.count(block)
print('block present exactly once:', n == 1)
if n == 1:
    i = after.index(block)
    print('page minus the block equals the old page:',
          after[:i] + after[i + len(block):] == before)
    print('bytes added:', len(after) - len(before), 'block is', len(block))
PY
```

- [ ] **Step 2: Run it and see it fail.** Run the block above. Expected: `47 /tmp/ohcamel-essay.txt`, then `0` and `0` from the two greps (grep exits 1 when it counts none, which is fine here), then `block present exactly once: False` — the archive is built but not in the page.

- [ ] **Step 3: Minimal implementation** — prepend the block to `web/index.html`.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
cat /tmp/ohcamel-archive-block.txt web/index.html > /tmp/ohcamel-index-new.html
mv /tmp/ohcamel-index-new.html web/index.html
head -3 web/index.html
tail -3 web/index.html
eval $(opam env --switch=$PWD --set-switch) && dune build
```

Expected: the file now opens `<!--` / `Phase 3. The dashboard page, served from memory by server.ml.` / `` (blank), still ends `</footer>` preceded by the two footer spans, and the build is clean.

- [ ] **Step 4: Run the tests and see them pass.** Re-run only the `python3` gate from Step 1. Expected exactly:

```
block present exactly once: True
page minus the block equals the old page: True
bytes added: 4480 block is 4480
```

(The two byte figures must be equal to each other; the absolute number depends only on the block and is printed so a later edit to the block is noticed.) Then `dune runtest --force` — green.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/index.html
git commit -m "web: the old page's design essay archived at the head of index.html, because its three moves are kept and an argument deleted with its file is an argument nobody can check"
```

---

### Task 4: `Ops_html` — the placeholder ops page, and the suite that watches the generated modules

**Files:**
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/ops.html`
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/ops.js`
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune` (append the `ops_html.ml` rule after the `dashboard_html.ml` rule)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_ohcamel.ml:30-46` (register the new suite)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: `web/head.html`, `web/page.css` from Task 1; `Dashboard_html.page : string` from Task 2.
- Produces: **`module Ops_html` with `val html : string`** — consumed by Phase 2, which adds the `/ops` case to `lib/server.ml`'s `handle`. Also produces `Test_embedded_assets.suite : string * unit Alcotest.test_case list`, which Tasks 5, 6 and 7 extend.
- The ops rule reuses `web/head.html` and `web/page.css` unchanged, which is the spec's "It shares one header, authored once in `web/`, with `/`" (`docs/superpowers/specs/2026-09-02-the-page-design.md:42`). One test below asserts that sharing byte for byte.

- [ ] **Step 1: Write the failing test.** Create `test/test_embedded_assets.ml`:

```ocaml
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

let suite =
  ( "embedded_assets",
    [
      Alcotest.test_case "the document is assembled in the rule's order" `Quick
        test_the_document_is_assembled_in_order;
      Alcotest.test_case "the ops page is assembled in the rule's order" `Quick
        test_the_ops_page_is_assembled_in_order;
      Alcotest.test_case "both pages share one head and one stylesheet" `Quick
        test_both_pages_share_one_head_and_one_stylesheet;
    ] )
```

Register it in `test/test_ohcamel.ml`. Replace

```ocaml
        ] );
      Test_risk_metrics.suite;
```

with

```ocaml
        ] );
      Test_embedded_assets.suite;
      Test_risk_metrics.suite;
```

(the `] );` closing the inline `"link"` suite is at `test/test_ohcamel.ml:38`; `Test_risk_metrics.suite;` is the next line).

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -20
```

Expected: `Error: Unbound module Ohcamel.Ops_html` from `test/test_embedded_assets.ml`.

- [ ] **Step 3: Minimal implementation.** Create `web/ops.html` — note the leading blank line, which is the same shape `web/index.html` has, because the rule's `(echo "…<body>\n")` supplies the newline before it:

```html

<header>
  <h1>OhCamel<span>operations</span></h1>
  <div class="stat"><span class="lbl">this page</span><span class="v">placeholder</span></div>
</header>

<main id="main">
  <section>
    <span class="lbl">ops <i>— not built yet</i></span>
    <p>this page is not built yet. Phase 2 of the page design fills it in: two
    columns, this host and its peer, in the ledger's key/value style, with the
    liveness pulse under the engine rows and, last, the paragraph on what the
    engine cannot see.</p>
    <p>Nothing routes here in Phase 1 — <code>lib/server.ml</code> has no
    <code>/ops</code> case. The module exists first so that the rule which
    builds it, and the head and stylesheet it shares with <code>/</code>, are
    proven before there is anything on the page to blame.</p>
  </section>
</main>

<footer>
  <span>ops — placeholder</span>
</footer>
```

Create `web/ops.js`:

```javascript
(function () {
  "use strict";
  // Phase 1 has nothing to run here, and the file is deliberately not absent:
  // the dune rule cats it, so a missing file is a build failure rather than a
  // page that quietly loses its client. Phase 2 replaces this wholesale with
  // the two-column poller and the pulse strips.
})();
```

Append to `lib/dune`, after the `dashboard_html.ml` rule:

```
(rule
 (targets ops_html.ml)
 (deps
  (glob_files ../web/*))
 (action
  (with-stdout-to
   ops_html.ml
   (progn
    (echo "let html = {ohcamel_html|")
    (cat ../web/head.html)
    (echo "<style>\n")
    (cat ../web/page.css)
    (echo "</style>\n</head>\n<body>\n")
    (cat ../web/ops.html)
    (echo "<script>\n")
    (cat ../web/ops.js)
    (echo "</script>\n</body>\n</html>\n|ohcamel_html}\n")))))
```

- [ ] **Step 4: Run the tests and see them pass.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -20
```

Expected: the alcotest summary reports the `embedded_assets` suite with 3 passing cases and the whole run green. Then re-run Task 3's `python3` gate — the dashboard page must still be `page minus the block equals the old page: True`, because nothing in this task touches `web/index.html`.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/ops.html web/ops.js lib/dune test/test_embedded_assets.ml test/test_ohcamel.ml
git commit -m "web: an ops page and the suite that watches the generated modules, because a rule that cats the wrong file still produces a string that compiles"
```

---

### Task 5: `Quoted` — the README's four tables, transcribed, and the pins that catch a slip

**Files:**
- Create: `/Users/ajaiupadhyaya/Documents/OhCamel/web/quoted.json`
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune` (append the `quoted.ml` rule after the `ops_html.ml` rule)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml` (three cases and their suite entries)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: nothing from earlier tasks except the `lib/dune` file itself.
- Produces: **`module Quoted` with `val json : string`** — the raw JSON text, so the test binary can parse it and Phase 5 can put it in the page inside a `<script id="quoted" type="application/json">` block without re-encoding. Phase 5's `argument.js` reads the four tables `scaling`, `battery`, `crisis`, `garch`, each of which has a `rows` array; Phase 5 also prints, beside each, the same table computed at startup, and one line saying whether they agree.
- Delimiter: `{ohcamel_json|…|ohcamel_json}`, distinct from the pages' so a page and a table cannot terminate each other.
- **`web/quoted.json` is NOT catted into the page in this phase.** Doing so would change the page's bytes and break Tasks 2 and 3's gate. Phase 5 adds it, between `(cat ../web/index.html)` and `(echo "<script>\n")` in the `dashboard_html.ml` rule.

- [ ] **Step 1: Write the failing test.** Append these three cases to `test/test_embedded_assets.ml`, immediately above the existing `let suite =`:

```ocaml
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
  | None -> Alcotest.failf "%s: no row for %s = %S, estimator %S" table key value estimator

(* Hand-typed JSON that nothing parses until it is on a web page is hand-typed
   JSON that ships broken. This is the parse. *)
let test_quoted_parses_and_holds_the_four_tables () =
  let json = Lazy.force quoted in
  Alcotest.(check (slist string String.compare))
    "the four quoted tables, and where they were quoted from"
    [ "battery"; "crisis"; "garch"; "machine"; "note"; "quoted_on"; "scaling"; "source" ]
    (U.keys json);
  Alcotest.(check int) "scaling: three book sizes" 3 (List.length (rows "scaling"));
  Alcotest.(check int) "battery: three series x three estimators" 9 (List.length (rows "battery"));
  Alcotest.(check int) "crisis: three windows x three estimators" 9 (List.length (rows "crisis"));
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
  Alcotest.(check int) "jumps/historical: exceptions" 0 (U.to_int (U.member "exceptions" jh));
  Alcotest.(check (float 1e-12))
    "jumps/historical: Kupiec p" 0.0
    (U.to_number (U.member "kupiec_p" jh));
  Alcotest.(check bool)
    "jumps/historical: the duration test does not apply, and that is null not zero" true
    (match U.member "duration_p" jh with `Null -> true | _ -> false);
  Alcotest.(check string) "jumps/historical: Basel zone" "green" (U.to_string (U.member "zone" jh));
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
```

Add three entries to `suite`, after `"both pages share one head and one stylesheet"`:

```ocaml
      Alcotest.test_case "web/quoted.json parses and holds the four tables" `Quick
        test_quoted_parses_and_holds_the_four_tables;
      Alcotest.test_case "the quoted rows are uniform" `Quick
        test_the_quoted_rows_are_uniform;
      Alcotest.test_case "PINS: the transcribed cells match README.md" `Quick
        test_the_pinned_cells_match_the_readme;
```

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -20
```

Expected: `Error: Unbound module Ohcamel.Quoted`.

- [ ] **Step 3a: Minimal implementation — `web/quoted.json`.** Create it with exactly this content. The four tables are `README.md:53-57` (scaling), `README.md:471-481` (battery), `README.md:682-692` (crisis) and `README.md:619-626` (GARCH), transcribed cell for cell.

```json
{
  "note": "The numbers this repository publishes but did not compute in this process. Everything here is transcribed by hand out of README.md. The page sets it in --ink-soft and tags it QUOTED, so a laptop's figures cannot pass for the droplet's, and Phase 5 prints the same table computed here beside each of these with one line saying whether they agree.",
  "source": "README.md",
  "quoted_on": "2026-09-02",
  "machine": "Apple M2 Pro, macOS, arm64",

  "scaling": {
    "note": "README.md, the closing table of `make run`. STALE ON PURPOSE: this transcribes the README as it stands today. The current node set is 63 / 342 / 1272 -- the five option singletons landed after this table was recorded -- and Phase 3 corrects README.md, docs/status.md and this block in one change. Until then the page's computed-vs-quoted line for this table will honestly report the cells that differ, which is the mechanism working rather than failing.",
    "columns": ["instruments", "nodes_in_graph", "nodes_per_tick", "if_polled"],
    "rows": [
      { "instruments": 10,  "nodes_in_graph": 58,   "nodes_per_tick": 25.6, "if_polled": 58 },
      { "instruments": 100, "nodes_in_graph": 337,  "nodes_per_tick": 25.2, "if_polled": 337 },
      { "instruments": 400, "nodes_in_graph": 1267, "nodes_per_tick": 26.0, "if_polled": 1267 }
    ]
  },

  "battery": {
    "note": "README.md, `make backtest`. Three synthetic series against three estimators. The `--` the CLI prints in the duration column means `does not apply -- fewer than two exceptions`, and is null here rather than zero, because the two are not the same claim.",
    "confidence": 0.95,
    "window": 60,
    "seed": "2026_08_24",
    "rows": [
      { "series": "iid-normal", "estimator": "historical", "n": 940, "exceptions": 45, "expected": 47.0, "kupiec_p": 0.7631, "independence_p": 0.3595, "joint_p": 0.6280, "duration_p": 0.6291, "duration_shape": 0.95, "zone": "green",  "verdict": "ok" },
      { "series": "iid-normal", "estimator": "parametric", "n": 940, "exceptions": 48, "expected": 47.0, "kupiec_p": 0.8814, "independence_p": 0.0229, "joint_p": 0.0744, "duration_p": 0.8975, "duration_shape": 1.01, "zone": "green",  "verdict": "ok" },
      { "series": "iid-normal", "estimator": "ewma(0.94)", "n": 940, "exceptions": 54, "expected": 47.0, "kupiec_p": 0.3057, "independence_p": 0.0102, "joint_p": 0.0219, "duration_p": 0.0577, "duration_shape": 1.24, "zone": "green",  "verdict": "REJECTED" },
      { "series": "vol-regime", "estimator": "historical", "n": 940, "exceptions": 52, "expected": 47.0, "kupiec_p": 0.4616, "independence_p": 0.9405, "joint_p": 0.7605, "duration_p": 0.9438, "duration_shape": 0.99, "zone": "green",  "verdict": "ok" },
      { "series": "vol-regime", "estimator": "parametric", "n": 940, "exceptions": 65, "expected": 47.0, "kupiec_p": 0.0107, "independence_p": 0.8028, "joint_p": 0.0373, "duration_p": 0.9592, "duration_shape": 1.00, "zone": "yellow", "verdict": "REJECTED" },
      { "series": "vol-regime", "estimator": "ewma(0.94)", "n": 940, "exceptions": 59, "expected": 47.0, "kupiec_p": 0.0836, "independence_p": 0.6864, "joint_p": 0.2063, "duration_p": 0.2446, "duration_shape": 1.13, "zone": "yellow", "verdict": "ok" },
      { "series": "jumps",      "estimator": "historical", "n": 940, "exceptions": 0,  "expected": 47.0, "kupiec_p": 0.0000, "independence_p": 1.0000, "joint_p": 0.0000, "duration_p": null,   "duration_shape": null, "zone": "green",  "verdict": "REJECTED" },
      { "series": "jumps",      "estimator": "parametric", "n": 940, "exceptions": 47, "expected": 47.0, "kupiec_p": 1.0000, "independence_p": 0.0277, "joint_p": 0.0886, "duration_p": 0.0000, "duration_shape": 20.00, "zone": "green", "verdict": "ok" },
      { "series": "jumps",      "estimator": "ewma(0.94)", "n": 940, "exceptions": 47, "expected": 47.0, "kupiec_p": 1.0000, "independence_p": 0.0277, "joint_p": 0.0886, "duration_p": 0.0000, "duration_shape": 20.00, "zone": "green", "verdict": "ok" }
    ]
  },

  "crisis": {
    "note": "README.md, `make backtest-crisis`. The same battery over three real windows from docs/crisis/*.csv, at today's book held at constant weights. `burst` is the largest number of exceptions in any 21 consecutive sessions -- about 1.1 expected under independence -- and is descriptive, not a test.",
    "confidence": 0.95,
    "window": 60,
    "burst_span": 21,
    "rows": [
      { "window": "gfc",        "estimator": "historical", "n": 570, "exceptions": 25, "expected": 28.5, "kupiec_p": 0.4925, "independence_p": 0.9207, "joint_p": 0.7862, "duration_p": 0.7483, "duration_shape": 0.95, "burst": 5,  "zone": "green",  "verdict": "ok" },
      { "window": "gfc",        "estimator": "parametric", "n": 570, "exceptions": 27, "expected": 28.5, "kupiec_p": 0.7713, "independence_p": 0.5347, "joint_p": 0.7906, "duration_p": 0.2265, "duration_shape": 0.84, "burst": 5,  "zone": "green",  "verdict": "ok" },
      { "window": "gfc",        "estimator": "ewma(0.94)", "n": 570, "exceptions": 34, "expected": 28.5, "kupiec_p": 0.3043, "independence_p": 0.9811, "joint_p": 0.5899, "duration_p": 0.2243, "duration_shape": 1.20, "burst": 5,  "zone": "green",  "verdict": "ok" },
      { "window": "covid",      "estimator": "historical", "n": 339, "exceptions": 18, "expected": 17.0, "kupiec_p": 0.7955, "independence_p": 0.0699, "joint_p": 0.1870, "duration_p": 0.0160, "duration_shape": 0.66, "burst": 8,  "zone": "green",  "verdict": "ok" },
      { "window": "covid",      "estimator": "parametric", "n": 339, "exceptions": 22, "expected": 17.0, "kupiec_p": 0.2279, "independence_p": 0.0521, "joint_p": 0.0733, "duration_p": 0.0033, "duration_shape": 0.65, "burst": 10, "zone": "green",  "verdict": "ok" },
      { "window": "covid",      "estimator": "ewma(0.94)", "n": 339, "exceptions": 23, "expected": 17.0, "kupiec_p": 0.1517, "independence_p": 0.2659, "joint_p": 0.1928, "duration_p": 0.3409, "duration_shape": 0.86, "burst": 5,  "zone": "green",  "verdict": "ok" },
      { "window": "rates-2022", "estimator": "historical", "n": 340, "exceptions": 24, "expected": 17.0, "kupiec_p": 0.1000, "independence_p": 0.8083, "joint_p": 0.2511, "duration_p": 0.7096, "duration_shape": 1.06, "burst": 4,  "zone": "yellow", "verdict": "ok" },
      { "window": "rates-2022", "estimator": "parametric", "n": 340, "exceptions": 22, "expected": 17.0, "kupiec_p": 0.2330, "independence_p": 0.6877, "joint_p": 0.4530, "duration_p": 0.5292, "duration_shape": 1.11, "burst": 4,  "zone": "green",  "verdict": "ok" },
      { "window": "rates-2022", "estimator": "ewma(0.94)", "n": 340, "exceptions": 22, "expected": 17.0, "kupiec_p": 0.2330, "independence_p": 0.6877, "joint_p": 0.4530, "duration_p": 0.2816, "duration_shape": 1.21, "burst": 4,  "zone": "green",  "verdict": "ok" }
    ]
  },

  "garch": {
    "note": "README.md, `make garch`. A known GARCH(1,1) simulated and fitted back, thirty replications at each sample size. The row that matters is the first: at n = 60, this engine's own return window, the persistence standard deviation is two thirds of the mean, which is the measured reason GARCH is implemented and not wired in.",
    "seed": "2026_08_25",
    "replications": 30,
    "burn_in": 500,
    "engine_window": 60,
    "truth": { "omega": 4e-6, "alpha": 0.10, "beta": 0.88, "persistence": 0.98, "half_life": 34 },
    "rows": [
      { "n": 60,   "alpha_mean": 0.112, "alpha_sd": 0.104, "beta_mean": 0.444, "beta_sd": 0.375, "persistence_mean": 0.556, "persistence_sd": 0.364 },
      { "n": 125,  "alpha_mean": 0.097, "alpha_sd": 0.093, "beta_mean": 0.607, "beta_sd": 0.346, "persistence_mean": 0.704, "persistence_sd": 0.334 },
      { "n": 250,  "alpha_mean": 0.099, "alpha_sd": 0.047, "beta_mean": 0.841, "beta_sd": 0.108, "persistence_mean": 0.939, "persistence_sd": 0.102 },
      { "n": 500,  "alpha_mean": 0.098, "alpha_sd": 0.034, "beta_mean": 0.859, "beta_sd": 0.049, "persistence_mean": 0.957, "persistence_sd": 0.032 },
      { "n": 1000, "alpha_mean": 0.092, "alpha_sd": 0.019, "beta_mean": 0.880, "beta_sd": 0.023, "persistence_mean": 0.973, "persistence_sd": 0.012 },
      { "n": 2000, "alpha_mean": 0.103, "alpha_sd": 0.014, "beta_mean": 0.872, "beta_sd": 0.015, "persistence_mean": 0.975, "persistence_sd": 0.009 }
    ]
  }
}
```

- [ ] **Step 3b: Minimal implementation — the rule.** Append to `lib/dune`, after the `ops_html.ml` rule. This one names its dependency directly rather than globbing, because it cats exactly one file:

```
(rule
 (targets quoted.ml)
 (deps ../web/quoted.json)
 (action
  (with-stdout-to
   quoted.ml
   (progn
    (echo "let json = {ohcamel_json|")
    (cat ../web/quoted.json)
    (echo "|ohcamel_json}\n")))))
```

- [ ] **Step 4: Run the tests and see them pass.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -20
# and the delimiter that would silently end the literal is not in the file
grep -c -- '|ohcamel_json}' web/quoted.json   # expect 0 (grep exits 1)
```

Expected: `embedded_assets` now reports 6 passing cases and the whole run is green.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/quoted.json lib/dune test/test_embedded_assets.ml
git commit -m "web: the README's four tables as data, because a page that quotes a number has to be able to say where it came from"
```

---

### Task 6: `Crisis_csv` — the three crisis windows in the binary

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune` (append the `crisis_csv.ml` rule after the `quoted.ml` rule)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml` (one case and its suite entry)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: `docs/crisis/gfc.csv`, `docs/crisis/covid.csv`, `docs/crisis/rates-2022.csv`, unchanged and untouched.
- Produces: **`module Crisis_csv` with three values, `val gfc : string`, `val covid : string`, `val rates_2022 : string`** — the file bytes verbatim, comment lines and all. Task 8 turns them into `Crisis_data.Window.t`s; Phase 3's CLI and Phase 5's reports read them through that.
- Note the name: the window is `rates-2022` (a hyphen, because it is a filename and a `Window.name`) and the OCaml value is `rates_2022` (an underscore, because a hyphen is not an identifier). `Crisis_data.embedded` in Task 8 is where the two names are paired, once.
- The `#` comment lines are **kept**. `Crisis_data.of_string` (`lib/crisis_data.ml`) finds the first line beginning `#` and makes it the window's `description`, which the crisis table prints as its heading; stripping them would silently retitle three windows.

- [ ] **Step 1: Write the failing test.** Append to `test/test_embedded_assets.ml`, above `let suite =`:

```ocaml
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
        false
        (String.mem contents '|'))
```

Add to `suite`, after the `PINS` entry:

```ocaml
      Alcotest.test_case "the three crisis windows are in the binary" `Quick
        test_the_crisis_windows_are_in_the_binary;
```

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -20
```

Expected: `Error: Unbound module Ohcamel.Crisis_csv`.

- [ ] **Step 3: Minimal implementation.** Append to `lib/dune`, after the `quoted.ml` rule:

```
(rule
 (targets crisis_csv.ml)
 (deps
  (glob_files ../docs/crisis/*.csv))
 (action
  (with-stdout-to
   crisis_csv.ml
   (progn
    (echo "let gfc = {ohcamel_csv|")
    (cat ../docs/crisis/gfc.csv)
    (echo "|ohcamel_csv}\n\nlet covid = {ohcamel_csv|")
    (cat ../docs/crisis/covid.csv)
    (echo "|ohcamel_csv}\n\nlet rates_2022 = {ohcamel_csv|")
    (cat ../docs/crisis/rates-2022.csv)
    (echo "|ohcamel_csv}\n")))))
```

- [ ] **Step 4: Run the tests and see them pass.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -20
# the three strings really are the three files
python3 - <<'PY'
import re
src = open('_build/default/lib/crisis_csv.ml', 'rb').read()
head, *rest = src.split(b'{ohcamel_csv|')
chunks = [c.split(b'|ohcamel_csv}')[0] for c in rest]
files = ['docs/crisis/gfc.csv', 'docs/crisis/covid.csv', 'docs/crisis/rates-2022.csv']
for path, chunk in zip(files, chunks):
    original = open(path, 'rb').read()
    print(path, 'identical:', chunk == original, len(chunk), len(original))
PY
```

Expected: three `identical: True` lines with `44538`, `29499`, `30685`, and a green suite (`embedded_assets` now has 7 cases).

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/dune test/test_embedded_assets.ml
git commit -m "crisis: the three windows embedded at build time, because a backtest that reads its inputs from the working directory does not run in the image"
```

---

### Task 7: `Build_info` — the build stamp, and its right to say "unknown"

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/dune` (append the `build_info.ml` rule after the `crisis_csv.ml` rule)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml` (one case and its suite entry)
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_embedded_assets.ml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: **`module Build_info` with `val git_sha : string`, `val built_at : string`, `val architecture : string`, `val system : string`** — consumed by Phase 2, which puts them on `/api/ops` as `build: {git_sha, git_short, built_at, profile, architecture, system, executable}` and threads `OHCAMEL_GIT_SHA` / `OHCAMEL_BUILT_AT` from the droplet's checkout through `docker-compose.yml`'s `build.args` into the `Dockerfile`, so that `smoke.sh --expect-sha` can prove `up -d` actually replaced the container.
- `%{env:VAR=default}` is a **tracked** dune dependency: the rule re-runs when and only when the value changes, so setting the variables does not invalidate the cached opam layer in the image build. Verified.
- The default is the literal string `unknown`, never an invented date. That is the whole design of this module: a build that does not know which commit it is must be able to say so, and `/api/ops` reporting `unknown` is what tells Phase 6 that the build args did not reach the container.

- [ ] **Step 1: Write the failing test.** Append to `test/test_embedded_assets.ml`, above `let suite =`:

```ocaml
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
```

Add to `suite`, after the crisis-windows entry:

```ocaml
      Alcotest.test_case "the build stamp can say it does not know" `Quick
        test_the_build_stamp_can_say_it_does_not_know;
```

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -20
```

Expected: `Error: Unbound module Ohcamel.Build_info`.

- [ ] **Step 3: Minimal implementation.** Append to `lib/dune`, after the `crisis_csv.ml` rule. This is the finished file's last stanza:

```
(rule
 (targets build_info.ml)
 (action
  (with-stdout-to
   build_info.ml
   (echo
    "let git_sha = \"%{env:OHCAMEL_GIT_SHA=unknown}\"\nlet built_at = \"%{env:OHCAMEL_BUILT_AT=unknown}\"\nlet architecture = \"%{ocaml-config:architecture}\"\nlet system = \"%{ocaml-config:system}\"\n"))))
```

- [ ] **Step 4: Run the tests and see them pass.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -20
cat _build/default/lib/build_info.ml
# and the part no OCaml test can reach: the variables really do arrive.
OHCAMEL_GIT_SHA=0123456789abcdef0123456789abcdef01234567 \
OHCAMEL_BUILT_AT=2026-09-03T00:00:00Z \
  dune build ./lib/build_info.ml
cat _build/default/lib/build_info.ml
dune build   # back to unknown
cat _build/default/lib/build_info.ml
```

Expected: a green suite (`embedded_assets` now has 8 cases); then

```
let git_sha = "unknown"
let built_at = "unknown"
let architecture = "arm64"
let system = "macosx"
```

then the same four lines with `git_sha = "0123456789abcdef0123456789abcdef01234567"` and `built_at = "2026-09-03T00:00:00Z"`, then back to `unknown`. (`architecture` and `system` are this machine's; on the Linux CI leg they are `amd64` and `linux`, which is why the test does not pin them.)

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/dune test/test_embedded_assets.ml
git commit -m "deploy: a build stamp in the binary, because .git/ is dockerignored and the image currently cannot say which commit it is"
```

---

### Task 8: `Crisis_data.embedded` and `Crisis_data.load_all_embedded`

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/lib/crisis_data.ml` (append after `load_all`, which is the last function before the `From closes to a book's return series` banner)
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_crisis_data.ml:14-17` (the header paragraph) and its `suite`
- Test: `/Users/ajaiupadhyaya/Documents/OhCamel/test/test_crisis_data.ml`

**Interfaces:**
- Consumes: `Crisis_csv.gfc`, `Crisis_csv.covid`, `Crisis_csv.rates_2022` from Task 6; the existing `Crisis_data.of_string : name:string -> string -> Window.t Or_error.t` and `Crisis_data.window_names : string list` (`= [ "gfc"; "covid"; "rates-2022" ]`).
- Produces: **`val embedded : (string * string) list`** (window name paired with its CSV text, in `window_names` order) and **`val load_all_embedded : unit -> Window.t list`** — consumed by Phase 3, where the CLI's `backtest-crisis` mode switches to it so the mode works from any working directory and inside the image, by Phase 5's `Validation_report.crisis`, and by Phase 6's fallback plan.
- `load_all_embedded` returns a plain list, **not** an `Or_error.t`. That is deliberate and it is the opposite of `load`'s contract, for the opposite reason; the comment in the implementation says why.

- [ ] **Step 1: Write the failing test.** In `test/test_crisis_data.ml`, first correct the header paragraph. Replace lines 14-17:

```
   The one test that is not about parsing is the last. It checks that the
   portfolio return series comes out of the ENGINE rather than out of arithmetic
   in crisis_data.ml, using a two-name book whose weights are +0.5 and -0.5 so
   the expected returns can be read off the page. *)
```

with:

```
   Two tests are not about parsing. One checks that the portfolio return series
   comes out of the ENGINE rather than out of arithmetic in crisis_data.ml,
   using a two-name book whose weights are +0.5 and -0.5 so the expected returns
   can be read off the page. The other, last, checks that the three windows
   compiled into the binary are the three files in docs/crisis -- the assertion
   that keeps lib/dune's embedding rule honest. *)
```

Then append this case after `test_the_committed_cache_loads`:

```ocaml
(* The embedded windows and the files on disk are the same data by construction:
   a rule in lib/dune cats docs/crisis/*.csv into crisis_csv.ml. "By
   construction" is exactly the kind of claim that stops being true the first
   time somebody edits a rule, so it is asserted rather than assumed.

   This is the only test in this file that needs the repository on disk, because
   comparing the binary against the disk requires the disk. Phase 3 removes the
   OTHER two cwd-walking tests when the CLI switches to load_all_embedded; this
   one keeps the walk, because the walk is half of what it is comparing.

   Float equality is exact, with no tolerance, on purpose: the same bytes through
   the same parser must produce the same floats, or one of those two statements
   is false. A tolerance here would hide the failure the test exists to catch. *)
let test_the_embedded_windows_are_the_files_on_disk () =
  Alcotest.(check (list string))
    "the embedded names, paired in window_names' order"
    Crisis_data.window_names
    (List.map Crisis_data.embedded ~f:fst);
  let on_disk = get "load_all" (Crisis_data.load_all ~dir:(crisis_dir ()) ()) in
  let embedded = Crisis_data.load_all_embedded () in
  Alcotest.(check int)
    "one embedded window per window on disk"
    (List.length on_disk) (List.length embedded);
  List.iter2_exn on_disk embedded ~f:(fun disk built_in ->
      let name = Crisis_data.Window.name disk in
      Alcotest.(check string) "the window's name" name (Crisis_data.Window.name built_in);
      Alcotest.(check string)
        (name ^ ": description -- the first # line, printed as the table's heading")
        (Crisis_data.Window.description disk)
        (Crisis_data.Window.description built_in);
      Alcotest.(check (list string))
        (name ^ ": every session date, in order")
        (Array.to_list (Crisis_data.Window.dates disk))
        (Array.to_list (Crisis_data.Window.dates built_in));
      let disk_closes = Crisis_data.Window.closes disk in
      let built_in_closes = Crisis_data.Window.closes built_in in
      Alcotest.(check (list string))
        (name ^ ": the same six symbols")
        (List.map (Map.keys disk_closes) ~f:Symbol.to_string)
        (List.map (Map.keys built_in_closes) ~f:Symbol.to_string);
      Map.iteri disk_closes ~f:(fun ~key:symbol ~data ->
          Alcotest.(check (list (float 0.0)))
            (Printf.sprintf "%s/%s: every adjusted close, exactly" name
               (Symbol.to_string symbol))
            (Array.to_list data)
            (Array.to_list (Map.find_exn built_in_closes symbol))))
```

Add to `suite`, after the existing last entry:

```ocaml
      Alcotest.test_case "THE EMBEDDED WINDOWS ARE THE FILES ON DISK" `Quick
        test_the_embedded_windows_are_the_files_on_disk;
```

- [ ] **Step 2: Run it and see it fail.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -20
```

Expected: `Error: Unbound value Crisis_data.embedded` (or `Crisis_data.load_all_embedded`, whichever the compiler reaches first).

- [ ] **Step 3: Minimal implementation.** In `lib/crisis_data.ml`, insert this between `load_all` and the `From closes to a book's return series` banner comment:

```ocaml
(* ------------------------------------------------------------------------ *)
(* The same three windows, compiled in                                       *)
(* ------------------------------------------------------------------------ *)

(* [load_all] reads docs/crisis relative to the working directory, which is an
   honest description of how the CLI is run -- from the repository root, as every
   Makefile target does -- and is exactly wrong in the two places this data now
   has to work. The image does not carry docs/ at all (.dockerignore), and the
   served process's working directory is /app; `ohcamel backtest-crisis` run from
   any other directory fails for a reason that has nothing to do with the
   analysis.

   So the same three files are also compiled in, by a rule in lib/dune that cats
   docs/crisis/*.csv into crisis_csv.ml. One source of data, two ways in: the
   files on disk stay the thing a reviewer reads and tools/fetch_crisis_data.py
   rewrites, and the strings below are those files byte for byte at the moment
   the binary was built. test_crisis_data.ml asserts the two agree.

   This list is the one place the two spellings of a window meet: `rates-2022` is
   the filename and the Window.name, `rates_2022` is the OCaml identifier. *)
let embedded : (string * string) list =
  [
    ("gfc", Crisis_csv.gfc);
    ("covid", Crisis_csv.covid);
    ("rates-2022", Crisis_csv.rates_2022);
  ]

(* Raises rather than returning an [Or_error.t], which is the opposite of [load]
   and is right for the opposite reason. [load]'s failure is a real runtime
   condition -- a working tree can genuinely be missing the cache -- and the
   caller can act on it, so it gets a named, actionable error naming the script
   that repopulates it. A parse failure HERE is a build that shipped malformed
   bytes: the data is inside the executable, there is nothing to repopulate at
   run time, and no behaviour a caller could choose is better than stopping.
   Threading an [Or_error.t] through every report for a case that cannot occur
   without a broken build would be error handling as decoration.

   The order is [window_names]' order, which is the order the crisis table
   prints its rows in. *)
let load_all_embedded () : Window.t list =
  List.map embedded ~f:(fun (name, contents) ->
      Or_error.ok_exn (of_string ~name contents))
```

- [ ] **Step 4: Run the tests and see them pass.**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | tail -20
```

Expected: the `crisis_data` suite reports 7 passing cases (six existing plus the new one) and the whole run is green.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/crisis_data.ml test/test_crisis_data.ml
git commit -m "crisis: windows readable from the binary as well as from disk, because the image has no docs/ and /app is not the repository root"
```

---

### Task 9: `.dockerignore` lets the crisis cache into the build context

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/.dockerignore:25` (the line `docs/`)
- Test: a real `docker build` that lists what reached the context

**Interfaces:**
- Consumes: the `crisis_csv.ml` rule from Task 6, which declares `(glob_files ../docs/crisis/*.csv)` as a dependency. Without this change the image build reaches `dune build`, finds no such files, and fails.
- Produces: `!docs/crisis/*.csv` in `.dockerignore` — consumed by Phase 6, whose deploy plan re-verifies the re-inclusion on the droplet's own Docker and carries a fallback if it behaves differently there.
- `web/` at the repository root is already inside the context; nothing excludes it.

- [ ] **Step 1: Write the failing test.** Run this now; the three CSVs must be absent.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
docker build --progress=plain --no-cache -f - -t ohcamel-context-check . <<'DOCKERFILE'
FROM alpine:3
WORKDIR /ctx
COPY . .
RUN echo "--- docs/ in the build context ---" \
 && (find docs -type f | sort || echo "(no docs/ reached the context at all)") \
 && echo "--- web/ in the build context ---" \
 && find web -type f | sort
DOCKERFILE
```

- [ ] **Step 2: Run it and see it fail.** Expected: under `--- docs/ ---`, `find: docs: No such file or directory` followed by `(no docs/ reached the context at all)`; under `--- web/ ---`, the seven files `web/dashboard.js`, `web/head.html`, `web/index.html`, `web/ops.html`, `web/ops.js`, `web/page.css`, `web/quoted.json`. The docs half is the failure: the crisis CSVs the `crisis_csv.ml` rule depends on are not in the context.

- [ ] **Step 3: Minimal implementation.** In `.dockerignore`, replace the single line

```
docs/
```

with

```
docs/
# ...except the crisis cache. lib/dune's crisis_csv.ml rule cats these three
# files into the binary at build time, so they are a BUILD input rather than
# documentation, and the builder's `COPY . .` has to see them. Excluding a
# directory and then re-including files under it does work: verified against
# this repository's own context with BuildKit, where `find . -type f` inside the
# builder listed exactly these three files out of docs/. If it ever stops
# working, the failure is loud -- `dune build` cannot find a declared dependency
# and the image never builds -- rather than an image that serves a page with an
# empty crisis table.
!docs/crisis/*.csv
```

- [ ] **Step 4: Run the tests and see them pass.** Re-run the `docker build` block from Step 1, then clean up:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
docker image rm ohcamel-context-check
```

Expected under `--- docs/ ---`, exactly and only:

```
docs/crisis/covid.csv
docs/crisis/gfc.csv
docs/crisis/rates-2022.csv
```

and the same seven `web/` files as before. If Docker is not available on the machine running this task, skip Steps 1, 2 and 4 and record that the re-inclusion is unverified locally; Phase 6 verifies it on the droplet at the first deploy and carries the fallback (a runtime `COPY docs/crisis /app/docs/crisis` with `load_all` reading files, which the read-only root filesystem permits).

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add .dockerignore
git commit -m "deploy: the crisis cache re-included in the build context, because it stopped being documentation the moment a dune rule read it"
```

---

### Task 10: CI fails on a stray closing delimiter, and names the file that has it

**Files:**
- Modify: `/Users/ajaiupadhyaya/Documents/OhCamel/.github/workflows/ci.yml` (insert a step in `build-and-test`, immediately after `- uses: actions/checkout@v4`)
- Test: run the same command locally, first against a deliberately broken file

**Interfaces:**
- Consumes: the three delimiters chosen in Tasks 2, 5 and 6 — `|ohcamel_html}`, `|ohcamel_csv}`, `|ohcamel_json}`.
- Produces: nothing OCaml consumes. It is the guarantee behind the delimiter choice, and it is the only one that does not depend on luck: a closing delimiter inside a source file *usually* leaves trailing bytes that are not valid OCaml, and the build then fails inside a generated file with a line number nobody can map back to what they edited — but "usually" is not a guarantee, and the grep is.

The step greps for the three **closing** delimiters only. An opening `{ohcamel_html|` inside a source file is harmless; only a closing one ends the literal.

- [ ] **Step 1: Write the failing test.** Run this locally against a deliberately broken file:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
printf '\n/* |ohcamel_html} */\n' >> web/ops.js
if grep -rn -e '|ohcamel_html}' -e '|ohcamel_csv}' -e '|ohcamel_json}' web docs/crisis; then
  echo "a closing quoted-string delimiter appears in an embedded source"
  echo "rename it, or change the delimiter in lib/dune"
  exit 1
fi
echo "no stray delimiters"
```

- [ ] **Step 2: Run it and see it fail.** Expected: `web/ops.js:N:/* |ohcamel_html} */` followed by the two message lines. Confirm the compiler agrees that this is worth catching early — `eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -5` reports a syntax error in `lib/ops_html.ml`, a *generated* file, at a line that corresponds to nothing anyone edited. Then undo the damage:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git checkout -- web/ops.js
grep -c -- '|ohcamel_html}' web/ops.js   # expect 0
```

- [ ] **Step 3: Minimal implementation.** In `.github/workflows/ci.yml`, in the `build-and-test` job, insert immediately after `      - uses: actions/checkout@v4` (the first step of that job) and before `      # Owl links against OpenBLAS.`:

```yaml
      # The two pages, the three crisis CSVs and the quoted tables are OCaml
      # quoted string literals: lib/dune cats web/* and docs/crisis/*.csv into
      # generated modules between {ohcamel_html|…|ohcamel_html}, {ohcamel_csv|…}
      # and {ohcamel_json|…}. A CLOSING delimiter inside one of those sources
      # ends the literal early. In practice the trailing bytes are then not valid
      # OCaml and the build fails — but it fails inside a GENERATED file, at a
      # line number nobody can map back to the file they edited, and "in
      # practice" is not a guarantee. This names the real file, costs a second,
      # and runs before the forty-minute Owl build rather than after it.
      #
      # One platform is enough, for the same reason the format check is Linux
      # only: the answer does not depend on the OS.
      - name: No stray quoted-string delimiters in the embedded sources
        if: runner.os == 'Linux'
        run: |
          if grep -rn -e '|ohcamel_html}' -e '|ohcamel_csv}' -e '|ohcamel_json}' \
               web docs/crisis; then
            echo "a closing quoted-string delimiter appears in an embedded source."
            echo "rename it, or change the delimiter in lib/dune."
            exit 1
          fi
          echo "no stray delimiters in web/ or docs/crisis/"
```

- [ ] **Step 4: Run the tests and see them pass.** Run the grep block from Step 1 again, now against the repaired tree.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
if grep -rn -e '|ohcamel_html}' -e '|ohcamel_csv}' -e '|ohcamel_json}' web docs/crisis; then
  echo "a closing quoted-string delimiter appears in an embedded source"
  exit 1
fi
echo "no stray delimiters in web/ or docs/crisis/"
eval $(opam env --switch=$PWD --set-switch) && dune build && dune runtest --force 2>&1 | tail -5
```

Expected: `no stray delimiters in web/ or docs/crisis/`, and a green build and suite. Also confirm the YAML still parses — `python3 -c "import sys,yaml;yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "ci.yml parses"` if PyYAML is available; otherwise `git diff .github/workflows/ci.yml` and read the indentation, which must be six spaces for `- name:` and eight for its keys, matching the steps around it.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add .github/workflows/ci.yml
git commit -m "ci: grep the embedded sources for a closing delimiter, because the alternative is a syntax error in a file nobody wrote"
```

---

## Done when

- `lib/dashboard_html.ml` is gone from the source tree and `_build/default/lib/dashboard_html.ml` is generated from `web/head.html`, `web/page.css`, `web/index.html` and `web/dashboard.js`; the served `/` is byte-identical to the page at commit `7e4a265` (Task 2's `SERVED PAGE BYTE-IDENTICAL` line).
- The 47-line design essay from the old literal sits verbatim in an HTML comment at the head of `web/index.html`.
- `Ops_html.html` (the value is `html`, not the spec's `page` — see the naming note above Task 4), `Quoted.json`, `Crisis_csv.{gfc, covid, rates_2022}` and `Build_info.{git_sha, built_at, architecture, system}` compile, and their generated-module tests pass; `Build_info.git_sha` reads `unknown` on a dev build.
- `Crisis_data.embedded` equals `Crisis_data.load_all "docs/crisis"` on the dev machine (Task 8's test), and `.dockerignore` re-includes `docs/crisis/*.csv`.
- CI fails on the literal delimiters `|ohcamel_html}`, `|ohcamel_csv}` or `|ohcamel_json}` anywhere under `web/` or `docs/crisis/`.
- `make test` is green with the suite count raised by exactly the tests this plan adds, `dune build @fmt` passes, and `bin/main.ml` is untouched (so the six-mode stdout gate is not triggered).
- One commit per task, each in the repo's voice, none touching `deploy/` or the Caddyfile.
