# The page — Phase 2: the build sha and the operations surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed engine able to say which build it is, how long it has been that build, and what its feed, stream, alerts and process are doing — on one JSON route, one small HTML page, and one smoke assertion that fails a deploy which kept the old container.

**Architecture:** `lib/server.ml` gains a `Server.t` that knows what it is (`mode`, `started_at`, `port`, `peer`, `feed_stats`, `quiet`) and one table of routes that both `handle` and the 404 body are generated from; `json_of_ops` reads those fields plus Incremental's process counters, `Gc.quick_stat`, `/proc/self/statm` and the alerts tracker, and serves them at `GET /api/ops`. The build stamp reaches the process through dune's `%{env:…}` from `deploy/deploy.sh` → `docker-compose.yml` → `deploy/Dockerfile` (and from the `Makefile` locally), so a number nobody set reads `unknown` and never an invented date. `web/ops.html` + `web/ops.js` draw that JSON as two key/value columns — this host and, from the live origin only, the demo peer over the demo engine's own CORS header — with three strips whose flat line is the alarm.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route: every route stays a GET with no effect.
- No persistence: nothing this phase adds is written to disk; `started_at` and every counter die with the process.
- No external asset: no CDN, no web font, no charting library, no framework — `/ops` is one self-contained string in the binary and every mark on it is inline SVG.
- The live host's gate is untouched: no Caddy CORS, no `basic_auth` matcher exemption, no `/api/up`.
- No invented vol surface on either deployed book.
- No number this process did not produce styled as if it had: an absent build argument reads `unknown`, an absent count reads `null`, and neither is ever a zero or a plausible date.
- No second implementation of engine arithmetic: `/ops` reads served fields and differences them; it computes no risk.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged — nothing in this phase adds a node, an observer or a listener to the served graph.
- The eight invariants in `docs/status.md` (§ *The invariants*) hold.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in the repo; dune files are reformatted by `dune fmt` too and the ubuntu CI leg checks them).
- All tests stay hermetic: no network, no credentials, nothing waiting on a clock.
- The byte-identical stdout gate for the six credential-free modes (`synthetic`, `stress`, `backtest`, `backtest-crisis`, `options`, `garch`) applies wherever `bin/main.ml` is touched — Task 1 captures the baseline, Tasks 8 and 17 diff against it.

---

## Working conventions for this phase

- **Commands.** Run everything from the repository root, `/Users/ajaiupadhyaya/Documents/OhCamel`. The opam switch is project-local, so every raw dune command is `eval $(opam env --switch=$PWD --set-switch) && dune …`, or use the `make` target that already does it.
- **Ignore `claudecodehandoff.md`** at the repository root. It is untracked and belongs to a different project. Never stage it.
- **Commit per task**, in this repository's voice: a lowercase area prefix (`server:`, `graph:`, `alerts:`, `feed:`, `config:`, `web:`, `deploy:`, `docs:`) then a sentence that says *why*. The executor adds the trailers; do not write them.
- **Phase 1 is already merged** when this plan starts. It created `web/head.html`, `web/page.css`, `web/format.js`, `web/index.html`, `web/quoted.json` (plus empty or near-empty `web/graph.js`, `web/charts.js`, `web/dashboard.js`, `web/argument.js`), deleted `lib/dashboard_html.ml`, and added `lib/dune` rules generating `dashboard_html.ml`, `ops_html.ml` (a placeholder), `crisis_csv.ml`, `build_info.ml` and `quoted.ml`.
- **The CSS tokens this phase uses already exist** in `web/page.css`, moved verbatim from the old `lib/dashboard_html.ml`: `--ground --panel --ink --ink-soft --ink-faint --rule --over --over-wash --unknown --unknown-wash --live --mark`, and the classes `.lbl` (10px, 0.14em, uppercase, `--ink-faint`), `.num` (tabular monospace), `td.k` (`--ink-soft`, 13px), `td.v` (right-aligned, 13px), `section` (a `--panel` block with 18px/20px padding).

---

### Task 1: the build stamp, and a test that it is honest

**Files:**
- Modify: `lib/dune` (the `build_info.ml` rule Phase 1 created)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: Phase 1's `lib/dune` rule generating `build_info.ml` with `Build_info.git_sha`, `Build_info.built_at`, `Build_info.architecture`, `Build_info.system` (all `string`), from `%{env:OHCAMEL_GIT_SHA=unknown}`, `%{env:OHCAMEL_BUILT_AT=unknown}`, `%{ocaml-config:architecture}`, `%{ocaml-config:system}`.
- Produces: `Ohcamel.Build_info.profile : string` — dune's `%{profile}`, `"dev"` locally and `"release"` in the image. Verified expandable in a `rule`'s `echo` action under lang dune 3.16 on this switch.

- [ ] **Step 1: Capture the six credential-free modes' stdout, before anything changes**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
mkdir -p /tmp/ohcamel-phase2/before /tmp/ohcamel-phase2/after
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -5
for m in synthetic stress backtest backtest-crisis options garch; do
  (eval $(opam env --switch=$PWD --set-switch) && dune exec bin/main.exe -- "$m") \
    > "/tmp/ohcamel-phase2/before/$m.txt" 2>/dev/null
done
wc -l /tmp/ohcamel-phase2/before/*.txt
```

All six files must be non-empty. All six modes are deterministic — every seed is fixed and no mode prints a wall clock — so this is a byte-comparable baseline.

- [ ] **Step 2: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
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
    || String.length built = 20
       && Char.equal built.[10] 'T'
       && Char.equal built.[19] 'Z');
  (* These three come from dune itself and cannot be absent. An empty one would
     render as a blank cell on the page, which reads as "not measured" rather
     than as "this build is broken". *)
  List.iter
    [
      ("profile", Ohcamel.Build_info.profile);
      ("architecture", Ohcamel.Build_info.architecture);
      ("system", Ohcamel.Build_info.system);
    ] ~f:(fun (name, value) ->
      Alcotest.(check bool) (name ^ " is not empty") true (not (String.is_empty value)))
```

and register it in the suite list, as the first case:

```ocaml
      Alcotest.test_case "the build stamp is honest or absent" `Quick
        test_build_stamp_is_honest;
```

- [ ] **Step 3: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: a compile error, `Error: Unbound value Ohcamel.Build_info.profile`.

- [ ] **Step 4: Minimal implementation**

In `lib/dune`, replace the `build_info.ml` rule Phase 1 wrote with this one — the only change is the third `let`:

```
; The build stamp, threaded from the droplet's checkout through compose and
; the Dockerfile into the binary. %{env:VAR=default} is a TRACKED dependency,
; so this rule re-runs only when the value changes and the twenty-minute opam
; layer stays cached; %{profile} is dune's own, so a dev build cannot claim to
; be a release one. Absent, every field is the word `unknown` -- never a date
; this process made up, which on /api/ops would be indistinguishable from a
; real one.
(rule
 (targets build_info.ml)
 (action
  (with-stdout-to
   build_info.ml
   (echo
    "let git_sha = \"%{env:OHCAMEL_GIT_SHA=unknown}\"\nlet built_at = \"%{env:OHCAMEL_BUILT_AT=unknown}\"\nlet profile = \"%{profile}\"\nlet architecture = \"%{ocaml-config:architecture}\"\nlet system = \"%{ocaml-config:system}\"\n"))))
```

- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
cat _build/default/lib/build_info.ml
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

`_build/default/lib/build_info.ml` must show `let profile = "dev"` and real values for `architecture` and `system`.

- [ ] **Step 6: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/dune test/test_server.ml
git commit -m "server: the build stamp carries its profile, because a dev build must not claim to be a release"
```

---

### Task 2: the Makefile stamps a local build

**Files:**
- Modify: `Makefile` (the `build`, `demo` and `deploy-build` targets)

**Interfaces:**
- Consumes: `Build_info` from Task 1; the `OPAM_ENV` pattern already in the Makefile (`eval $$(opam env --switch=$(CURDIR) --set-switch)`).
- Produces: nothing in OCaml. A local `make build` now stamps the binary with the current `HEAD` and the build time.

- [ ] **Step 1: Write the failing test**

There is no OCaml surface here, so the test is the generated module. Save it as the assertion to run:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make build >/dev/null 2>&1
grep -q "let git_sha = \"$(git rev-parse HEAD)\"" _build/default/lib/build_info.ml \
  && echo "PASS: make build stamps the sha" \
  || echo "FAIL: make build did not stamp the sha"
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make build >/dev/null 2>&1
grep -q "let git_sha = \"$(git rev-parse HEAD)\"" _build/default/lib/build_info.ml \
  && echo "PASS" || echo "FAIL"
```

Expected: `FAIL`, and `grep 'let git_sha' _build/default/lib/build_info.ml` shows `unknown`.

- [ ] **Step 3: Minimal implementation**

In `Makefile`, add this block immediately after the `OPAM_ENV := …` line at the top:

```make
# The build stamp, so a locally built binary says which commit it is. Absent
# a git checkout it says `unknown`, which is the point: an invented date on
# /api/ops would be indistinguishable from a real one.
#
# A fresh OHCAMEL_BUILT_AT on every invocation relinks the binary, because
# dune tracks %{env:...} as a dependency. That costs a couple of seconds and
# buys a built_at that is not a lie. `make test` deliberately does NOT stamp,
# so alternating test and build relinks once each way -- annoying, cheap, and
# preferable to a test suite whose inputs change every second.
BUILD_STAMP := OHCAMEL_GIT_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
               OHCAMEL_BUILT_AT=$$(date -u +%FT%TZ)
```

Replace the `build` recipe:

```make
build:
	$(OPAM_ENV) && $(BUILD_STAMP) dune build
```

Replace the `demo` recipe (keep the comment above it as it is):

```make
demo: build
	$(OPAM_ENV) && $(BUILD_STAMP) dune exec bin/main.exe -- demo
```

Replace the `deploy-build` recipe (keep the comment above it as it is):

```make
deploy-build:
	docker build -f deploy/Dockerfile \
	  --build-arg OHCAMEL_GIT_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
	  --build-arg OHCAMEL_BUILT_AT=$$(date -u +%FT%TZ) \
	  -t ohcamel:latest .
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make build >/dev/null 2>&1
grep "let git_sha\|let built_at\|let profile" _build/default/lib/build_info.ml
grep -q "let git_sha = \"$(git rev-parse HEAD)\"" _build/default/lib/build_info.ml \
  && echo "PASS: make build stamps the sha" || echo "FAIL"
make test 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add Makefile
git commit -m "deploy: make build stamps the binary, so a local /api/ops says which commit it is"
```

---

### Task 3: the stamp reaches the image, and the live container learns its peer

**Files:**
- Modify: `deploy/Dockerfile` (after `COPY --chown=opam:opam . .`, before the release `RUN`)
- Modify: `deploy/docker-compose.yml` (`ohcamel-demo`'s `build:` block; `ohcamel-live`'s environment)
- Modify: `deploy/deploy.sh` (the `say "Building"` block)

**Interfaces:**
- Consumes: `%{env:OHCAMEL_GIT_SHA}` / `%{env:OHCAMEL_BUILT_AT}` from Task 1's dune rule.
- Produces: `OHCAMEL_PEER_ORIGIN` in the live container's environment, read by `Config.Runtime.peer_origin` in Task 7.

- [ ] **Step 1: Write the failing test**

Three greps, run as one check:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
fail=0
grep -q 'ARG OHCAMEL_GIT_SHA=unknown' deploy/Dockerfile || { echo "FAIL Dockerfile ARG"; fail=1; }
grep -q 'OHCAMEL_GIT_SHA: "\${OHCAMEL_GIT_SHA:-unknown}"' deploy/docker-compose.yml || { echo "FAIL compose args"; fail=1; }
grep -q 'OHCAMEL_PEER_ORIGIN: "https://\${OHCAMEL_DEMO_HOST}"' deploy/docker-compose.yml || { echo "FAIL compose peer"; fail=1; }
grep -q 'OHCAMEL_GIT_SHA=$(git -C "$REPO" rev-parse HEAD)' deploy/deploy.sh || { echo "FAIL deploy.sh prefix"; fail=1; }
grep -q '^export OHCAMEL_GIT_SHA' deploy/deploy.sh && { echo "FAIL deploy.sh must not export"; fail=1; }
[ $fail -eq 0 ] && echo PASS
```

- [ ] **Step 2: Run it and see it fail**

Run the block above. Expected: four `FAIL` lines and no `PASS`.

- [ ] **Step 3: Minimal implementation**

In `deploy/Dockerfile`, insert between `COPY --chown=opam:opam . .` and the comment block above the release `RUN`:

```dockerfile
# The build stamp, and it sits HERE for a reason. An ARG invalidates every
# layer below it, so putting the sha above `opam install` would throw away the
# twenty-minute dependency layer on every commit. Below the COPY it costs only
# the one-minute dune build, which is being re-run anyway because the source
# changed.
#
# ENV as well as ARG: dune reads these through %{env:...}, which is the
# process environment, not the build arguments.
ARG OHCAMEL_GIT_SHA=unknown
ARG OHCAMEL_BUILT_AT=unknown
ENV OHCAMEL_GIT_SHA=$OHCAMEL_GIT_SHA
ENV OHCAMEL_BUILT_AT=$OHCAMEL_BUILT_AT
```

In `deploy/docker-compose.yml`, replace `ohcamel-demo`'s build block:

```yaml
  ohcamel-demo:
    <<: *engine
    build:
      context: ..
      dockerfile: deploy/Dockerfile
      # Passed through to dune's %{env:...} inside the builder stage. Absent,
      # the image honestly says `unknown` rather than carrying a date nobody
      # set. ohcamel-live has no build block -- it runs this same image, so
      # both hosts always report the same sha, and a disagreement would mean
      # one of them did not restart.
      args:
        OHCAMEL_GIT_SHA: "${OHCAMEL_GIT_SHA:-unknown}"
        OHCAMEL_BUILT_AT: "${OHCAMEL_BUILT_AT:-unknown}"
```

and add to `ohcamel-live`, immediately after the `profiles: ["live"]` line:

```yaml
    # Where the OTHER host is. /ops on the live origin fills its second column
    # from the public demo engine, which is possible only in this direction:
    # the demo engine publishes a CORS header on its JSON in demo mode, and the
    # live host publishes none and stays behind the password. The demo service
    # deliberately gets no peer -- nothing crosses the other way.
    environment:
      OHCAMEL_PEER_ORIGIN: "https://${OHCAMEL_DEMO_HOST}"
```

In `deploy/deploy.sh`, replace the `"${COMPOSE[@]}" build` line under `say "Building"`:

```bash
# A per-command prefix, never an export. The rule is the one at the top of this
# file: nothing here may put a value into the environment that compose would
# then prefer over deploy/.env. These two are harmless to export and the next
# pair after them would not be, so the habit is kept rather than the exception
# made.
OHCAMEL_GIT_SHA="$(git -C "$REPO" rev-parse HEAD)" \
OHCAMEL_BUILT_AT="$(date -u +%FT%TZ)" \
	"${COMPOSE[@]}" build
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
bash -n deploy/deploy.sh && echo "deploy.sh parses"
docker compose --env-file deploy/local.env -f deploy/docker-compose.yml \
  -f deploy/docker-compose.local.yml config >/dev/null && echo "compose parses"
fail=0
grep -q 'ARG OHCAMEL_GIT_SHA=unknown' deploy/Dockerfile || { echo "FAIL Dockerfile ARG"; fail=1; }
grep -q 'OHCAMEL_GIT_SHA: "\${OHCAMEL_GIT_SHA:-unknown}"' deploy/docker-compose.yml || { echo "FAIL compose args"; fail=1; }
grep -q 'OHCAMEL_PEER_ORIGIN: "https://\${OHCAMEL_DEMO_HOST}"' deploy/docker-compose.yml || { echo "FAIL compose peer"; fail=1; }
grep -q 'OHCAMEL_GIT_SHA="$(git -C "$REPO" rev-parse HEAD)"' deploy/deploy.sh || { echo "FAIL deploy.sh prefix"; fail=1; }
[ $fail -eq 0 ] && echo PASS
```

If `docker compose config` is unavailable on this machine, skip that line and rely on the greps; the droplet deploy in Task 19 is where it is proved.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add deploy/Dockerfile deploy/docker-compose.yml deploy/deploy.sh
git commit -m "deploy: carry the sha and the build time into the image, because the smoke suite could not tell whether up -d replaced anything"
```

---

### Task 4: the process counters, and the staleness threshold, get names

**Files:**
- Modify: `lib/graph.ml` (append beside `total_nodes_recomputed` / `total_stabilizes` at the end of the file; and one accessor beside `let symbols`)
- Test: `test/test_graph.ml`

**Interfaces:**
- Consumes: `Inc.State.num_nodes_created`, `Inc.State.num_var_sets`, `Inc.State.num_active_observers`, each `_ Incremental.State.t -> int` — verified in `_opam/lib/incremental/incremental_intf.ml:1067,1080,1086`. The existing wrappers use `Inc.State.t` as the state value (`lib/graph.ml:1790`).
- Produces:
  - `Graph.total_nodes_created : unit -> int`
  - `Graph.total_var_sets : unit -> int`
  - `Graph.active_observers : unit -> int`
  - `Graph.staleness_threshold : Graph.t -> Types.Time.Span.t`

- [ ] **Step 1: Write the failing test**

Add to `test/test_graph.ml`, above `let suite =`:

```ocaml
(* ------------------------------------------------------------------------ *)
(* Incremental's own counters, for the operations page                       *)
(* ------------------------------------------------------------------------ *)

(* These are PROCESS-wide: one Incremental state serves every graph this binary
   ever builds, including the ones stress.ml forks and destroys. So nothing
   here asserts an absolute value -- another test in this same runner would
   change it -- only the direction, which is all these wrappers promise and all
   /api/ops claims. The page says the same thing in words. *)
let test_process_counters_move () =
  let created_before = Graph.total_nodes_created () in
  let observers_before = Graph.active_observers () in
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book
      ~limits:book_limits ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      Alcotest.(check bool)
        "building a graph creates nodes" true
        (Graph.total_nodes_created () > created_before);
      Alcotest.(check bool)
        "and makes observers active" true
        (Graph.active_observers () > observers_before);
      (* set_price is exactly one Inc.Var.set (graph.ml:1508), so this one is
         an equality rather than a direction: if it ever costs two, something
         is writing a cell nobody asked it to. *)
      let sets_before = Graph.total_var_sets () in
      Graph.set_price graph aapl (Price.of_float 150.0);
      Alcotest.(check int)
        "one set_price is one var set" (sets_before + 1)
        (Graph.total_var_sets ()))
    ~finally:(fun () -> Graph.destroy graph)

(* The threshold is configuration, not state, and /api/ops publishes it so a
   reader can tell 40 s of silence from stale. It has to come back out of the
   graph it went into: the demo host runs at 20 s and the live host at 90, and
   a page that assumed one of them would call the other host broken. *)
let test_staleness_threshold_round_trips () =
  let graph =
    Graph.create ~starting_cash:(dollars 100_000.0) ~instruments:book
      ~limits:book_limits ~confidence:0.95 ~return_window:10
      ~staleness_threshold:(Time.Span.of_sec 20.0) ()
  in
  Exn.protect
    ~f:(fun () ->
      Alcotest.check float_eq "20 s in, 20 s out" 20.0
        (Time.Span.to_sec (Graph.staleness_threshold graph)))
    ~finally:(fun () -> Graph.destroy graph)
```

and register both in the suite list, after the `"an unknown symbol is loud"` case:

```ocaml
      Alcotest.test_case "Incremental's process counters move" `Quick
        test_process_counters_move;
      Alcotest.test_case "the staleness threshold comes back out" `Quick
        test_staleness_threshold_round_trips;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Graph.total_nodes_created`.

- [ ] **Step 3: Minimal implementation**

In `lib/graph.ml`, replace the two-line block at the end of the file:

```ocaml
let total_nodes_recomputed () : int = Inc.State.num_nodes_recomputed Inc.State.t
let total_stabilizes () : int = Inc.State.num_stabilizes Inc.State.t
```

with:

```ocaml
let total_nodes_recomputed () : int = Inc.State.num_nodes_recomputed Inc.State.t
let total_stabilizes () : int = Inc.State.num_stabilizes Inc.State.t

(* Three more of Incremental's constant-time counters, for the operations page.

   Every one of them is PROCESS-wide and none of them is this graph's: one
   Incremental state serves the served graph, the startup probe, and every fork
   /api/stress makes and destroys, so [total_nodes_created] reads in the
   thousands for a fifty-node graph. That is not a defect to be corrected by
   filtering -- it is the honest number for "how much work has this process
   done" -- but it has to be LABELLED, because a reader who takes it for the
   graph's size will conclude the engine is enormous. /api/ops puts these under
   `process` and the page says `includes forks and the startup probe` beside
   them. The per-graph counts come from the on_compute hook instead. *)
let total_nodes_created () : int = Inc.State.num_nodes_created Inc.State.t
let total_var_sets () : int = Inc.State.num_var_sets Inc.State.t

(* Created and not yet disallowed -- a gauge, not a total, which is why it is
   not called total_. A number that stops falling after a stress run would mean
   a fork was not destroyed, and an undestroyed graph recomputes on every
   stabilize forever. *)
let active_observers () : int = Inc.State.num_active_observers Inc.State.t
```

and add, immediately after `let symbols (t : t) : Symbol.t list = Map.keys t.instruments` (line 1505):

```ocaml
(* Configuration, read back out. The demo host runs at 20 s and the live host
   at 90, and age is the reader's to compute (see Feed_health above), so the
   threshold it computes against has to be on the wire beside the ages. *)
let staleness_threshold (t : t) : Time.Span.t = t.staleness_threshold
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/graph.ml test/test_graph.ml
git commit -m "graph: name Incremental's other counters, because /ops has to say what the whole process is doing"
```

---

### Task 5: alerts can say what is firing and when the switch tripped

**Files:**
- Modify: `lib/alerts.ml` (a function inside `module Tracker`; three accessors at the end of the file)
- Test: `test/test_alerts.ml`

**Interfaces:**
- Consumes: `Alerts.t`'s existing private fields `config : Config.Alerts.t`, `tracker : Tracker.t`, `kill_switch : Kill_switch.t`; `Alerts.Kill_switch.state : t -> state` with `Tripped of { by : string; at : Types.Time.t }`.
- Produces:
  - `Alerts.Tracker.firing_names : Tracker.t -> string list` (sorted)
  - `Alerts.config : Alerts.t -> Config.Alerts.t`
  - `Alerts.firing_limits : Alerts.t -> string list`
  - `Alerts.tripped_at : Alerts.t -> Types.Time.t option`

- [ ] **Step 1: Write the failing test**

Add to `test/test_alerts.ml`, above `let suite =`:

```ocaml
(* What the wire could not say before.

   /api/snapshot has reported `enabled` and a kill-switch word since Phase 4,
   and nothing else -- so an operator could see that something had tripped and
   not which limits were still over the line, nor when. Worse, the tracker's
   hysteresis means "breached" and "still firing" are different states: a limit
   back under its threshold but above clear_below is not breached and is still
   firing, and only the tracker knows. These three accessors put that state on
   the wire. *)
let test_firing_and_tripped_at_are_readable () =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = tech } ]
      ~limits:[ cap ] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~f:(fun () ->
      match
        Alerts.attach ~graph
          ~config:(alerts_config ~kill:true ~trips_on:[ "aapl-cap" ] ())
      with
      | Error e -> Alcotest.failf "attach: %s" (Error.to_string_hum e)
      | Ok None -> Alcotest.fail "an enabled config must produce a notifier"
      | Ok (Some a) ->
          (* Nothing has been evaluated yet. Empty, and armed but not tripped. *)
          Graph.stabilize graph;
          Alcotest.(check (list string)) "nothing firing yet" [] (Alerts.firing_limits a);
          Alcotest.(check bool)
            "and nothing has tripped" true
            (Option.is_none (Alerts.tripped_at a));
          (* aapl-cap is a 100 gross-notional cap. 150 x 1 = 150, so
             utilisation is 1.5 and the limit is breached. *)
          Graph.set_price graph aapl (Price.of_float 150.0);
          Graph.set_qty graph aapl (Qty.of_float 1.0);
          Graph.stabilize graph;
          Alcotest.(check (list string))
            "the breached limit is firing" [ "aapl-cap" ] (Alerts.firing_limits a);
          Alcotest.(check bool)
            "and the switch it trips on has a time" true
            (Option.is_some (Alerts.tripped_at a));
          (* Back under the line but INSIDE the hysteresis band: 96 x 1 = 96,
             utilisation 0.96, which is above clear_below = 0.95. Not breached,
             still firing -- which is exactly the distinction the page needs
             and the one a `breached` list cannot express. *)
          Graph.set_price graph aapl (Price.of_float 96.0);
          Graph.stabilize graph;
          Alcotest.(check (list string))
            "inside the band it is still firing" [ "aapl-cap" ]
            (Alerts.firing_limits a);
          (* The config travels too, so the page can print the hysteresis it is
             looking at rather than assuming the default. *)
          Alcotest.(check (float 1e-9))
            "clear_below is readable" 0.95
            (Alerts.config a).Config.Alerts.clear_below;
          Alcotest.(check (list string))
            "and so is what the switch trips on" [ "aapl-cap" ]
            (Alerts.config a).Config.Alerts.kill_switch_trips_on)
    ~finally:(fun () -> Graph.destroy graph)
```

and register it in the suite list, after `"a book with an alerts block parses"`:

```ocaml
      Alcotest.test_case "firing limits and the trip time are readable" `Quick
        test_firing_and_tripped_at_are_readable;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Alerts.firing_limits`.

- [ ] **Step 3: Minimal implementation**

In `lib/alerts.ml`, inside `module Tracker`, add immediately after `let firing t name = equal_state (state t name) Firing`:

```ocaml
  (* Every limit currently in the Firing state, sorted.

     Sorted because this list goes on a page: an unsorted Hashtbl.keys would
     reorder itself between two polls and every row would look like it had
     changed, which is the one thing a monitoring page must not do. *)
  let firing_names t : string list =
    Hashtbl.fold t.states ~init:[] ~f:(fun ~key ~data acc ->
        match data with Firing -> key :: acc | Ok_ -> acc)
    |> List.sort ~compare:String.compare
```

and at the end of the file, immediately after `let failed t = t.failed`:

```ocaml
(* Three readers for the wire, added because /api/snapshot could report that
   something had tripped and not which limits were still over the line.

   [firing_limits] is the TRACKER's state, not the breach list: hysteresis
   means a limit back under its threshold but above clear_below is not
   breached and is still firing, and the two are different facts about the
   same limit. [tripped_at] is on the switch rather than derived from the
   event history, because the history is a bounded queue and the trip is the
   thing an operator most wants to still be there after fifty events. *)
let config t = t.config
let firing_limits t = Tracker.firing_names t.tracker

let tripped_at t =
  match Kill_switch.state t.kill_switch with
  | Kill_switch.Tripped { at; _ } -> Some at
  | Kill_switch.Armed | Kill_switch.Disarmed -> None
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/alerts.ml test/test_alerts.ml
git commit -m "alerts: expose what is firing and when the switch tripped, because the wire could only say that something had"
```

---

### Task 6: the two feed stat records learn to be JSON

**Files:**
- Modify: `lib/feed/alpaca_ws.ml` (inside `module Stats`, after `to_string`)
- Modify: `lib/feed/fred_client.ml` (inside `module Stats`, after `to_string`)
- Test: `test/test_feed.ml`

**Interfaces:**
- Consumes: `Alpaca_ws.Stats.t = { mutable frames : int; mutable trades : int; mutable rejected : int; mutable unknown_symbol : int; mutable reconnects : int; mutable last_error : string option }` (`lib/feed/alpaca_ws.ml:234`); `Fred_client.Stats.t = { mutable polls : int; mutable successes : int; mutable observations : int; mutable consecutive_failures : int; mutable last_success : Types.Time.t option; mutable last_error : string option }` (`lib/feed/fred_client.ml:209`). Both files already `open Core` and `open Async`.
- Produces:
  - `Alpaca_ws.Stats.to_json : Stats.t -> Yojson.Safe.t`
  - `Fred_client.Stats.to_json : Stats.t -> Yojson.Safe.t`

  These exist so `bin/main.ml` can hand `Server.create` a closure and `server.ml` never learns an Alpaca type. Task 8 defines that closure's shape.

- [ ] **Step 1: Write the failing test**

Add to `test/test_feed.ml`, above `let suite =`:

```ocaml
(* The feed's own counters, on the wire.

   Two rules, and they are the wire rules the snapshot already follows. An
   unknown is null and never a zero: `last_success: null` means FRED has not
   answered once, and a 0 there would render as the epoch. And an error string
   is carried as text rather than as a flag, because "the socket reconnected
   four times" and "the socket reconnected four times and the last reason was
   403" want different actions. *)
let test_alpaca_stats_json () =
  let s = Ohcamel.Alpaca_ws.Stats.create () in
  let field j key =
    match j with
    | `Assoc fields -> (
        match List.Assoc.find fields key ~equal:String.equal with
        | Some v -> v
        | None -> Alcotest.failf "missing key %S" key)
    | _ -> Alcotest.fail "not an object"
  in
  let fresh = Ohcamel.Alpaca_ws.Stats.to_json s in
  Alcotest.(check bool)
    "a socket that has never errored says null, not \"\"" true
    (match field fresh "last_error" with `Null -> true | _ -> false);
  Alcotest.(check int)
    "no frames yet" 0
    (match field fresh "frames" with `Int n -> n | _ -> Alcotest.fail "frames");
  s.Ohcamel.Alpaca_ws.Stats.frames <- 7;
  s.Ohcamel.Alpaca_ws.Stats.reconnects <- 2;
  s.Ohcamel.Alpaca_ws.Stats.last_error <- Some "connection reset";
  let used = Ohcamel.Alpaca_ws.Stats.to_json s in
  Alcotest.(check int)
    "frames" 7
    (match field used "frames" with `Int n -> n | _ -> Alcotest.fail "frames");
  Alcotest.(check int)
    "reconnects" 2
    (match field used "reconnects" with `Int n -> n | _ -> Alcotest.fail "reconnects");
  Alcotest.(check string)
    "the last reason survives" "connection reset"
    (match field used "last_error" with `String e -> e | _ -> "?")

let test_fred_stats_json () =
  let s = Ohcamel.Fred_client.Stats.create () in
  let field j key =
    match j with
    | `Assoc fields -> (
        match List.Assoc.find fields key ~equal:String.equal with
        | Some v -> v
        | None -> Alcotest.failf "missing key %S" key)
    | _ -> Alcotest.fail "not an object"
  in
  let fresh = Ohcamel.Fred_client.Stats.to_json s in
  Alcotest.(check bool)
    "never polled successfully is null, not the epoch" true
    (match field fresh "last_success" with `Null -> true | _ -> false);
  s.Ohcamel.Fred_client.Stats.polls <- 3;
  s.Ohcamel.Fred_client.Stats.successes <- 2;
  s.Ohcamel.Fred_client.Stats.observations <- 61;
  s.Ohcamel.Fred_client.Stats.consecutive_failures <- 1;
  s.Ohcamel.Fred_client.Stats.last_success <- Some Time.epoch;
  let used = Ohcamel.Fred_client.Stats.to_json s in
  Alcotest.(check int)
    "polls" 3
    (match field used "polls" with `Int n -> n | _ -> Alcotest.fail "polls");
  Alcotest.(check int)
    "observations" 61
    (match field used "observations" with
    | `Int n -> n
    | _ -> Alcotest.fail "observations");
  (* UTC, and the same rendering /api/snapshot uses for as_of, so two
     timestamps on one page can be compared without a second convention. *)
  Alcotest.(check string)
    "last_success is a UTC instant" "1970-01-01 00:00:00.000000000Z"
    (match field used "last_success" with `String t -> t | _ -> "?")
```

and register both in the suite list, after `"fred: the api key never reaches a log"`:

```ocaml
      Alcotest.test_case "alpaca: the stats record is JSON" `Quick test_alpaca_stats_json;
      Alcotest.test_case "fred: the stats record is JSON" `Quick test_fred_stats_json;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Ohcamel.Alpaca_ws.Stats.to_json`.

If the `last_success` string assertion fails, print the actual value once
(`Time_ns.to_string_utc Types.Time.epoch`) and use it verbatim — the rendering
is Core's and is not this project's to choose. Everything else in the test
stands as written.

- [ ] **Step 3: Minimal implementation**

In `lib/feed/alpaca_ws.ml`, inside `module Stats`, after `to_string`:

```ocaml
  (* The same counters as [to_string], for /api/ops rather than for a log line.

     Written out by hand rather than derived, for the reason server.ml gives at
     the head of its encoders: a derived encoder would emit last_error's sexp
     form, and "the last reason the socket dropped" is a sentence a person
     reads. An absent reason is null and never the empty string -- a socket
     that has never errored and one whose error was blank are different
     states, and only one of them is fine. *)
  let to_json t : Yojson.Safe.t =
    `Assoc
      [
        ("frames", `Int t.frames);
        ("trades", `Int t.trades);
        ("rejected", `Int t.rejected);
        ("unknown_symbol", `Int t.unknown_symbol);
        ("reconnects", `Int t.reconnects);
        ( "last_error",
          match t.last_error with None -> `Null | Some e -> `String e );
      ]
```

In `lib/feed/fred_client.ml`, inside `module Stats`, after `to_string`:

```ocaml
  (* As in alpaca_ws.ml: hand-written, and the unknowns are null.

     [last_success] especially. A FRED client that has never answered has no
     last success, and rendering that as the epoch would put 1970 on the page
     beside a live number -- which is not "unknown", it is a wrong answer with
     a plausible shape. UTC, in the same form /api/snapshot renders as_of. *)
  let to_json t : Yojson.Safe.t =
    `Assoc
      [
        ("polls", `Int t.polls);
        ("successes", `Int t.successes);
        ("observations", `Int t.observations);
        ("consecutive_failures", `Int t.consecutive_failures);
        ( "last_success",
          match t.last_success with
          | None -> `Null
          | Some at -> `String (Time_ns.to_string_utc at) );
        ( "last_error",
          match t.last_error with None -> `Null | Some e -> `String e );
      ]
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/feed/alpaca_ws.ml lib/feed/fred_client.ml test/test_feed.ml
git commit -m "feed: the two stats records encode themselves, so the server never learns an Alpaca type"
```

---

### Task 7: the live host learns where its peer is

**Files:**
- Modify: `lib/config.ml` (`module Runtime`)
- Test: `test/test_feed.ml` (where the other Config tests live)

**Interfaces:**
- Consumes: `Config.Runtime.t`'s existing record and `of_env`'s `string_var` idiom; `OHCAMEL_PEER_ORIGIN`, set on `ohcamel-live` by Task 3.
- Produces:
  - `Config.Runtime.t` gains `peer_origin : string option` (default `None`)
  - `Config.Runtime.peer_origin_of : string option -> string option` — the parse, split out so it is testable without mutating the process environment.

- [ ] **Step 1: Write the failing test**

Add to `test/test_feed.ml`, above `let suite =`:

```ocaml
(* Where the OTHER host is, if anywhere.

   Parsed through a pure function rather than tested by setting the variable,
   because putenv in one test leaks into every other test in the same binary
   and a suite whose result depends on execution order is a suite nobody
   trusts. What is asserted is the rule the rest of config.ml already follows:
   an empty variable is an absent variable. `export OHCAMEL_PEER_ORIGIN=` is a
   far more common way to end up without a peer than never setting it, and the
   failure it would otherwise produce is /ops trying to fetch "" and rendering
   `unreachable from this browser` about nothing. *)
let test_peer_origin_parsing () =
  let check name expected raw =
    Alcotest.(check (option string)) name expected (Config.Runtime.peer_origin_of raw)
  in
  check "unset" None None;
  check "empty" None (Some "");
  check "whitespace only" None (Some "   ");
  check "a real origin" (Some "https://ohcamel.example.com")
    (Some "https://ohcamel.example.com");
  check "trimmed" (Some "https://ohcamel.example.com")
    (Some "  https://ohcamel.example.com\n");
  (* And the default is no peer at all: the demo host is given none, and
     nothing crosses from the gated host to the public one. *)
  Alcotest.(check (option string))
    "no peer by default" None Config.Runtime.default.Config.Runtime.peer_origin
```

and register it in the suite list, after `"config: a missing credential names the variable"`:

```ocaml
      Alcotest.test_case "config: an empty peer origin is an absent one" `Quick
        test_peer_origin_parsing;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Config.Runtime.peer_origin_of`.

- [ ] **Step 3: Minimal implementation**

In `lib/config.ml`, inside `module Runtime`, add this field to the record, immediately after `snapshot_interval : Time_ns.Span.t;`:

```ocaml
    (* The origin of the other host, when there is one. Set only on the live
         container: /ops on the live origin fills its second column from the
         public demo engine, which is possible only in that direction, because
         the demo engine publishes a CORS header on its JSON in demo mode and
         the live one publishes none and stays behind the password. None here
         is not a failure -- it is the demo host, and its /ops says where to
         look for both. *)
    peer_origin : string option;
```

add `peer_origin = None;` to `default`, immediately after `snapshot_interval = Time_ns.Span.of_sec 10.0;`, and in `of_env` add the field to the returned record:

```ocaml
      peer_origin = peer_origin_of (Sys.getenv "OHCAMEL_PEER_ORIGIN");
```

with the parse itself defined just above `let of_env () =`:

```ocaml
  (* Split out of [of_env] so the "empty means absent" rule can be tested
     without a test that mutates the process environment. Same rule as
     Credentials.required, and for the same reason: `export FOO=` is how
     people end up without a value. *)
  let peer_origin_of (raw : string option) : string option =
    match raw with
    | Some v when not (String.is_empty (String.strip v)) -> Some (String.strip v)
    | Some _ | None -> None
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/config.ml test/test_feed.ml
git commit -m "config: the live host can be told where its peer is, because /ops needs both columns from one origin"
```

---

### Task 8: the server knows what it is

**Files:**
- Modify: `lib/server.ml` (`type t` at :292; `create` at :491; `start` at :514; accessors at :527)
- Modify: `bin/main.ml` (`run_live`'s `Server.create` at :1672; `run_demo`'s `Server.create` at :1943 — the two call sites; line numbers are approximate, grep for `Server.create`)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: `Types.Symbol.t`, `Types.Time.t`, `History_buffer.attach`, `Graph.on_change` — all unchanged.
- Produces, and every later phase depends on this exact signature:

```ocaml
type mode = [ `Demo | `Live ]

val Server.create :
  ?coalesce:Time_ns.Span.t ->
  ?history_capacity:int ->
  ?alerts:Alerts.t ->
  ?peer:string ->
  ?feed_stats:(unit -> Yojson.Safe.t) ->
  ?quiet:Types.Symbol.t list ->
  mode:mode ->
  graph:Graph.t ->
  factor:string ->
  unit ->
  t

val Server.mode : t -> mode
val Server.port : t -> int
val Server.started_at : t -> Types.Time.t
val Server.peer : t -> string option
val Server.quiet : t -> Types.Symbol.t list
```

  Phase 4 adds `?recompute_log` and Phase 5 adds `~reports` to the same call; both are optional-or-labelled additions to this list and neither changes what is above.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
(* A server over the seeded graph.

   Constructing one inside a test is safe with no Async scheduler running:
   [create] fills no Ivar, and Async's don't_wait_for is literally
   `let don't_wait_for (_ : unit t) = ()`, so the broadcaster is built, parks
   on an Ivar nobody fills, and costs nothing. Nothing here opens a socket. *)
let with_server ?(mode = `Demo) ?alerts ?peer ?feed_stats ?quiet ~f () =
  with_graph
    ~f:(fun graph ->
      let server =
        Server.create ?alerts ?peer ?feed_stats ?quiet ~mode ~graph ~factor:"SYNTHETIC"
          ()
      in
      f server graph)
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
```

and register both in the suite list, after `"/api/history of an empty buffer"`:

```ocaml
      Alcotest.test_case "the server knows what it is" `Quick
        test_server_knows_what_it_is;
      Alcotest.test_case "a demo server has no peer" `Quick test_a_demo_server_has_no_peer;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: This function has type ... It is applied to too many arguments` / `Unbound value Server.mode`.

- [ ] **Step 3: Minimal implementation — `lib/server.ml`**

Insert immediately above `type t = {` (which is under the `(* The broadcaster *)` banner):

```ocaml
(* Which host this process is.

   The two deployed engines run the same image with different arguments, and
   until now nothing in the process could tell them apart -- so the live host's
   page could not say `live · Alpaca + FRED`, and the demo host could not
   publish a CORS header without the live host publishing one too. A closed
   variant rather than a string: there are two hosts, there will not quietly be
   a third, and a typo in a string would have shipped as a mode nobody
   matched. *)
type mode = [ `Demo | `Live ]

let mode_to_string = function `Demo -> "demo" | `Live -> "live"
```

Replace the `type t = { … }` record with:

```ocaml
type t = {
  graph : Graph.t;
  factor : string;
  (* Present only when alerting is enabled, which is not the default. The
     dashboard reports what it finds; it does not turn anything on. *)
  alerts : Alerts.t option;
  (* What this process is, for /api/ops and for the CORS decision below. *)
  mode : mode;
  (* When it became this process. Nothing persists, so uptime is a difference
     against this and there is no uptime percentage anywhere -- that would need
     history nobody keeps. Read beside the build sha it is the "did up -d
     actually replace the container" check the smoke suite never had. *)
  started_at : Types.Time.t;
  (* Filled by [start]. 0 until then, and 0 is not a port: a reader who sees it
     is looking at a process that never bound, which is worth being able to see
     rather than a default that claims 8080. *)
  mutable port : int;
  (* The other host's origin, or None. Set only on the live container. /ops on
     the live origin fills its second column from the public demo engine, and
     nothing goes the other way. *)
  peer : string option;
  (* The feed's own counters, as a closure rather than as Alpaca and FRED
     records. This module must not learn a broker type -- it would then be
     linked into every mode including the ones that have no credentials -- so
     bin/main.ml closes over its two Stats records and hands back the object.
     None is the synthetic feed, and /api/ops says so in words. *)
  feed_stats : (unit -> Yojson.Safe.t) option;
  (* Symbols that are quiet ON PURPOSE. The demo book never ticks its last
     name so the stale path is visible; without this the page would report a
     working demonstration as a broken feed. Empty on the live host, where a
     quiet name means what it says. *)
  quiet : Types.Symbol.t list;
  (* Filled by Graph.on_change. The loop below reads it and immediately swaps in
     a fresh one, so changes arriving during a send are not lost. *)
  mutable changed : unit Ivar.t;
  mutable subscribers : string Pipe.Writer.t list;
  mutable frames_sent : int;
  coalesce : Time_ns.Span.t;
  (* A bounded in-memory trail, for the dashboard's sparklines. Fed by an
     observer rather than by this broadcaster, so a change is recorded whether or
     not anyone is subscribed -- a chart that only had history for the period
     someone was watching would be a strange thing to look at. See
     history_buffer.ml, which also states in as many words that it is not
     persistence. *)
  history : History_buffer.t;
}
```

Replace `create`:

```ocaml
let create ?(coalesce = Time_ns.Span.of_ms 80.0) ?history_capacity
    ?(alerts : Alerts.t option) ?(peer : string option)
    ?(feed_stats : (unit -> Yojson.Safe.t) option) ?(quiet : Types.Symbol.t list = [])
    ~(mode : mode) ~(graph : Graph.t) ~(factor : string) () =
  let t =
    {
      graph;
      factor;
      alerts;
      mode;
      started_at = Types.Time.now ();
      port = 0;
      peer;
      feed_stats;
      quiet;
      changed = Ivar.create ();
      subscribers = [];
      frames_sent = 0;
      coalesce;
      history =
        History_buffer.attach ?capacity:history_capacity ~graph ~now:Types.Time.now ();
    }
  in
  (* The link that makes this reactive rather than polled. Graph.on_change fires
     inside stabilization, so the handler does the minimum possible: fill an
     Ivar. All the work -- snapshotting, serializing, writing -- happens in the
     broadcaster, outside the graph. *)
  Graph.on_change graph ~f:(fun () -> Ivar.fill_if_empty t.changed ());
  don't_wait_for (run_broadcaster t);
  t
```

Replace `start`'s first line so the port is recorded before anything can be
served:

```ocaml
let start ?(port = 8080) (t : t) =
  (* Recorded here rather than passed to [create], because the port is the
     caller's decision at listen time and /api/ops must report the one actually
     bound rather than the one someone intended. *)
  t.port <- port;
  Cohttp_async.Server.create
```

(the rest of `start` is unchanged.)

Replace the last two lines of the file:

```ocaml
let frames_sent (t : t) = t.frames_sent
let subscriber_count (t : t) = List.length t.subscribers
let mode (t : t) = t.mode
let port (t : t) = t.port
let started_at (t : t) = t.started_at
let peer (t : t) = t.peer
let quiet (t : t) = t.quiet
```

- [ ] **Step 4: Minimal implementation — `bin/main.ml`, the two call sites**

In `run_live`, replace:

```ocaml
            let server =
              Server.create ~graph ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

with:

```ocaml
            let server =
              Server.create ~mode:`Live ~graph
                ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

In `run_demo`, replace:

```ocaml
  let server = Server.create ?alerts ~graph ~factor:"SYNTHETIC" () in
```

with:

```ocaml
  let server = Server.create ?alerts ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
```

- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 6: Diff the six credential-free modes against the baseline**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
for m in synthetic stress backtest backtest-crisis options garch; do
  (eval $(opam env --switch=$PWD --set-switch) && dune exec bin/main.exe -- "$m") \
    > "/tmp/ohcamel-phase2/after/$m.txt" 2>/dev/null
  if diff -q "/tmp/ohcamel-phase2/before/$m.txt" "/tmp/ohcamel-phase2/after/$m.txt" >/dev/null; then
    echo "IDENTICAL $m"
  else
    echo "CHANGED   $m"; diff "/tmp/ohcamel-phase2/before/$m.txt" "/tmp/ohcamel-phase2/after/$m.txt" | head -20
  fi
done
```

All six must print `IDENTICAL`. If any prints `CHANGED`, stop and fix it: this
phase changes no arithmetic and no printer, so a differing byte is a real
regression rather than an acceptable cost.

- [ ] **Step 7: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml bin/main.ml test/test_server.ml
git commit -m "server: the process learns which host it is and when it started, because nothing on the wire could say"
```

---

### Task 9: the demo host's JSON is readable cross-origin, and nothing else is

**Files:**
- Modify: `lib/server.ml` (`json_headers` at :395; the five `~headers:json_headers` sites inside `handle`)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: `Server.mode` from Task 8; `Cohttp.Header.of_list : (string * string) list -> t` and `Cohttp.Header.get : t -> string -> string option`, which matches header names case-insensitively (`_opam/lib/cohttp/header.ml:61`, via `caseless_equal`).
- Produces: `Server.json_headers : mode:mode -> Cohttp.Header.t`. `Server.html_headers` and `Server.sse_headers` keep their existing types and values.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
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
```

and register it in the suite list, after `"a demo server has no peer"`:

```ocaml
      Alcotest.test_case "CORS is on the demo host's JSON and nothing else" `Quick
        test_cors_is_demo_json_only;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: This expression has type Cohttp.Header.t ... but an expression was expected of type mode:… -> …` — `json_headers` is a value, not a function.

- [ ] **Step 3: Minimal implementation**

In `lib/server.ml`, replace:

```ocaml
let json_headers =
  Cohttp.Header.of_list
    [ ("Content-Type", "application/json"); ("Cache-Control", "no-store") ]
```

with:

```ocaml
let json_header_fields =
  [ ("Content-Type", "application/json"); ("Cache-Control", "no-store") ]

(* The demo host's JSON is readable from anywhere; the live host's is not.

   /ops on the live origin draws BOTH hosts, and the only way to do that from
   one page is to fetch the public engine's JSON cross-origin. The alternative
   the design rejected was a CORS rule in the Caddyfile or a counts-only route
   outside the password, and both of those widen the gate for a convenience
   the owner can get by opening the other tab. This does not touch the gate at
   all: it is one header, emitted by the engine that is already public, on the
   routes that already return the whole book to anyone who asks.

   Never on [html_headers] and never on [sse_headers]. A document is not a
   datum, and the stream carries the same book at a higher rate; if either
   carried this header the rule would be "the demo host is open", which is a
   larger claim than the one being made. *)
let json_headers ~(mode : mode) =
  Cohttp.Header.of_list
    (match mode with
    | `Demo -> ("Access-Control-Allow-Origin", "*") :: json_header_fields
    | `Live -> json_header_fields)
```

Then in `handle`, replace every `~headers:json_headers` with
`~headers:(json_headers ~mode:t.mode)` — five sites: `/api/snapshot`,
`/api/health`, `/api/history`, `/api/stress` and the `_` (404) branch. Verify
with:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
grep -c 'headers:(json_headers ~mode:t.mode)' lib/server.ml   # must print 5
grep -c 'headers:json_headers' lib/server.ml                  # must print 0
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: the public engine's JSON says it is public, so /ops can draw both hosts without touching the gate"
```

---

### Task 10: the alerts object says what is firing, on what, and since when

**Files:**
- Modify: `lib/server.ml` (`json_of_alerts` at :318)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: `Alerts.config`, `Alerts.firing_limits`, `Alerts.tripped_at` from Task 5; `Config.Alerts.t`'s `sinks : Sink.t list`, `clear_below : float`, `kill_switch_trips_on : string list`.
- Produces: `Server.json_of_alerts : Alerts.t option -> Yojson.Safe.t`, now emitting the same twelve keys in both branches: `enabled`, `kill_switch`, `tripped_by`, `tripped_at`, `halt_new_orders`, `firing`, `sent`, `failed`, `sinks`, `trips_on`, `clear_below`, `recent`. This is the object `/api/snapshot` already carries and the one `/api/ops` reuses in Task 11.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
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
    Graph.create
      ~starting_cash:(Notional.of_float 100_000.0)
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

(* Off is a state with facts in it, not an absence of fields.

   Both branches emit the same keys so the page never has to ask whether a
   field exists before asking what it says -- a client that branches on shape
   is a client that renders `undefined` the first time the other branch
   ships. clear_below is null rather than 0.95 when there is no notifier:
   there is no hysteresis to report, and a default printed as a measurement is
   the failure this whole wire format is arranged against. *)
let test_alerts_json_when_off () =
  let j = Server.json_of_alerts None in
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
  List.iter [ "firing"; "sinks"; "trips_on"; "recent" ] ~f:(fun key ->
      match field_exn j key with
      | `List [] -> ()
      | other -> Alcotest.failf "%s should be an empty list, got %s" key
                   (Yojson.Safe.to_string other))

let test_alerts_json_when_firing () =
  with_alerts
    ~f:(fun a ->
      let j = Server.json_of_alerts (Some a) in
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
      Alcotest.(check bool) "and orders are flagged halted" true
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
        "the hysteresis is on the wire, not assumed" 0.95 (num j "clear_below"))
    ()
```

and register both in the suite list, after the CORS case:

```ocaml
      Alcotest.test_case "the alerts object when nothing is armed" `Quick
        test_alerts_json_when_off;
      Alcotest.test_case "the alerts object when a limit is firing" `Quick
        test_alerts_json_when_firing;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `missing key "tripped_at"` from `test_alerts_json_when_off`.

- [ ] **Step 3: Minimal implementation**

In `lib/server.ml`, add above `json_of_alerts`:

```ocaml
(* A sink is named, never described.

   Config.Alerts.Sink.sexp_of_t renders `File "/var/log/ohcamel.log"` as
   `(File /var/log/ohcamel.log)`, and this object is served unauthenticated on
   the demo host. A filesystem path is information about the machine that
   nothing on the page needs, and "which kinds of sink are configured" is the
   whole question a reader is asking. *)
let sink_name (sink : Config.Alerts.Sink.t) : string =
  match sink with
  | Config.Alerts.Sink.Log -> "log"
  | Config.Alerts.Sink.File _ -> "file"
  | Config.Alerts.Sink.Slack -> "slack"
  | Config.Alerts.Sink.Dry_run -> "dry_run"
```

and replace `json_of_alerts` entirely:

```ocaml
(* What Phase 4 is doing, for the dashboard and for /ops to report.

   Reports and never mutates: there is no route that arms, trips or resets
   anything. A kill switch that could be flipped by an unauthenticated GET
   would be a worse hazard than the one it guards against.

   Both branches emit the same keys. A client that has to test for a field's
   existence before reading it is a client that renders `undefined` the first
   time the other branch ships, and the two branches here are two hosts. What
   differs is the VALUES: with no notifier there is no hysteresis to report, so
   clear_below is null rather than the default it would have had -- a default
   printed as a measurement is the one thing this wire format exists to
   prevent.

   [firing] is the tracker's state and not the breach list, and the difference
   is the hysteresis: a limit back under its threshold but above clear_below is
   not breached and is still firing. Only one of those two facts is in
   /api/snapshot's `limits`, and it is not the one an operator wants at 3am. *)
let json_of_alerts (alerts : Alerts.t option) : Yojson.Safe.t =
  match alerts with
  | None ->
      `Assoc
        [
          ("enabled", `Bool false);
          ("kill_switch", `String "off");
          ("tripped_by", `Null);
          ("tripped_at", `Null);
          ("halt_new_orders", `Bool false);
          ("firing", `List []);
          ("sent", `Int 0);
          ("failed", `Int 0);
          ("sinks", `List []);
          ("trips_on", `List []);
          ("clear_below", `Null);
          ("recent", `List []);
        ]
  | Some a ->
      let config = Alerts.config a in
      let state, tripped_by =
        match Alerts.Kill_switch.state (Alerts.kill_switch a) with
        | Alerts.Kill_switch.Disarmed -> ("off", `Null)
        | Alerts.Kill_switch.Armed -> ("armed", `Null)
        | Alerts.Kill_switch.Tripped { by; _ } -> ("tripped", `String by)
      in
      `Assoc
        [
          ("enabled", `Bool true);
          ("kill_switch", `String state);
          ("tripped_by", tripped_by);
          (* On the switch rather than dug out of the event history, because
             the history is a bounded queue of fifty and the trip is the event
             most likely to still matter after it has been evicted. *)
          ( "tripped_at",
            match Alerts.tripped_at a with
            | None -> `Null
            | Some at -> jstring (Time_ns.to_string_utc at) );
          ("halt_new_orders", `Bool (Alerts.halted a));
          ("firing", jlist jstring (Alerts.firing_limits a));
          ("sent", `Int (Alerts.sent a));
          ("failed", `Int (Alerts.failed a));
          ("sinks", jlist jstring (List.map config.Config.Alerts.sinks ~f:sink_name));
          ("trips_on", jlist jstring config.Config.Alerts.kill_switch_trips_on);
          ("clear_below", jfloat config.Config.Alerts.clear_below);
          ( "recent",
            `List
              (List.rev_map (Alerts.history a) ~f:(fun e ->
                   `Assoc
                     [
                       ( "kind",
                         `String
                           (Sexp.to_string
                              (Alerts.Event.sexp_of_kind e.Alerts.Event.kind)) );
                       ("limit", `String e.Alerts.Event.limit_name);
                       ("line", `String (Alerts.Event.to_line e));
                       ("at", `String (Time_ns.to_string_utc e.Alerts.Event.at));
                     ])) );
        ]
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: the alerts object carries the hysteresis state, because breached and still-firing are different facts"
```

---

### Task 11: json_of_ops — what the process is and how long it has been it

**Files:**
- Modify: `lib/server.ml` (lift `keepalive` out of `subscribe`; add `build_json`, `gc_json`, `rss_bytes`, `feed_source_json`, `json_of_ops` immediately after the headers block and before `subscribe`)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: `Build_info.{git_sha, built_at, profile, architecture, system}` (Tasks 1–2); `Graph.{total_nodes_recomputed, total_stabilizes, total_nodes_created, total_var_sets, active_observers, staleness_threshold, feed_health, symbols}` (Task 4 and existing); `Alerts` accessors via `json_of_alerts` (Task 10); `Server.t`'s `mode/started_at/port/peer/feed_stats/quiet` (Task 8); `Gc.quick_stat : unit -> Gc.Stat.t` with getters `heap_words top_heap_words minor_collections major_collections compactions minor_words promoted_words major_words` (`_opam/lib/core/gc.mli:192` for `type stat = Stat.t`, `:450` for `quick_stat`, and the 4.12–5.5 record branch which carries `[@@deriving … fields ~getters]`); `Async.Unix.getpid : unit -> Pid.t` and `Async.Unix.gethostname : unit -> string` (`_opam/lib/async_unix/unix_syscalls.mli:18,689`, both synchronous); `Stdlib.Sys.ocaml_version` and `Stdlib.Sys.executable_name` (Core's `Sys` does not export them and `open Async` shadows `Sys` with `Async_sys`).
- Produces: `Server.json_of_ops : t -> Yojson.Safe.t` and `Server.keepalive : Time_ns.Span.t`. The object's keys, which Phases 4 and 5 extend but never rename:
  `mode`, `started_at`, `uptime_s`, `port`, `pid`, `hostname`, `ocaml_version`,
  `build{git_sha, git_short, built_at, profile, architecture, system, executable}`,
  `process{nodes_recomputed, stabilizes, nodes_created, var_sets, active_observers}`,
  `graph{named}`, `stream{frames_sent, subscribers, coalesce_ms, keepalive_s}`,
  `history{appended, points, capacity}`, `alerts{…}`,
  `feed{healthy, symbols, stale, never_seen, quiet, staleness_threshold_s}`,
  `feed_source{kind, alpaca_feed, fred_series, alpaca, fred}`,
  `gc{…}`, `rss_bytes`, `reports{static, garch}`, `peer`.
- **Two slots are deliberately empty in this phase and are filled later.**
  `graph.named` is `null` until Phase 4's `Recompute_log` exists — it becomes
  `{distinct, total, hottest:[{name,n}]}`. `reports.static` and `reports.garch`
  are both the string `"absent"` until Phase 5's `Reports.compute` exists — they
  become `"ready"` and `"computing" | "done"`. Null and `"absent"` are chosen
  over `0` and `"ready"` for the reason the whole wire format exists: a count
  this build cannot produce must not arrive looking like a measurement.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
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
      Alcotest.(check string)
        "the mode is a word the client can switch on" "live"
        (match field_exn j "mode" with `String s -> s | _ -> "?");
      Alcotest.(check bool)
        "uptime is a number and not negative" true
        (Float.( >= ) (num j "uptime_s") 0.0);
      Alcotest.(check bool)
        "pid is an int" true
        (match field_exn j "pid" with `Int _ -> true | _ -> false);
      (* The build stamp, threaded from the droplet's checkout. git_short is
         seven characters of a real sha and the whole word when there is not
         one, because "unknow" would look like a sha that had been truncated. *)
      let build = field_exn j "build" in
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
      List.iter
        [ "nodes_recomputed"; "stabilizes"; "nodes_created"; "var_sets";
          "active_observers" ] ~f:(fun key ->
          match field_exn process key with
          | `Int n when n >= 0 -> ()
          | other ->
              Alcotest.failf "process.%s should be a non-negative int, got %s" key
                (Yojson.Safe.to_string other));
      (* Phase 4 fills this from the recompute log. Until then it is null and
         never a zero: "no named node ran" is the alarm, and a build that
         cannot tell must not raise it. *)
      Alcotest.(check bool)
        "graph.named is null until the recompute log exists" true
        (match field_exn (field_exn j "graph") "named" with `Null -> true | _ -> false);
      (* Phase 5 fills these. `absent` rather than `ready`, for the same
         reason. *)
      let reports = field_exn j "reports" in
      List.iter [ "static"; "garch" ] ~f:(fun key ->
          Alcotest.(check string)
            ("reports." ^ key ^ " is absent until Phase 5")
            "absent"
            (match field_exn reports key with `String s -> s | _ -> "?"));
      (* Counts, never names. This is the assertion that keeps the live book
         off a page the owner might open on a phone in a coffee shop. *)
      let feed = field_exn j "feed" in
      List.iter [ "symbols"; "stale"; "never_seen"; "quiet" ] ~f:(fun key ->
          match field_exn feed key with
          | `Int _ -> ()
          | other ->
              Alcotest.failf "feed.%s must be a count, got %s" key
                (Yojson.Safe.to_string other));
      Alcotest.(check int) "the quiet list is counted, not listed" 1 (Int.of_float (num feed "quiet"));
      Alcotest.(check bool)
        "no symbol name appears anywhere in the object" false
        (String.is_substring (Yojson.Safe.to_string j) ~substring:"AAPL"
        || String.is_substring (Yojson.Safe.to_string j) ~substring:"XOM");
      (* The threshold the ages are measured against travels with them. *)
      Alcotest.(check (float 1e-9)) "the default threshold" 90.0
        (num feed "staleness_threshold_s");
      (* No feed_stats closure was given, so this is the synthetic feed saying
         so in words rather than four nulls the client has to interpret. *)
      Alcotest.(check string)
        "no closure means the synthetic feed" "synthetic"
        (match field_exn (field_exn j "feed_source") "kind" with
        | `String s -> s
        | _ -> "?");
      (* Open pipes only. A browser that closed its tab must not keep counting
         as a subscriber, because "someone is watching" is the one thing this
         row is for. *)
      let stream = field_exn j "stream" in
      Alcotest.(check int) "no subscribers" 0 (Int.of_float (num stream "subscribers"));
      Alcotest.(check (float 1e-9)) "the coalesce window is on the wire" 80.0
        (num stream "coalesce_ms");
      Alcotest.(check (float 1e-9)) "and so is the keepalive" 20.0
        (num stream "keepalive_s");
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
```

and register both in the suite list, after the two alerts cases:

```ocaml
      Alcotest.test_case "/api/ops shape, and what it must not say" `Quick
        test_ops_shape;
      Alcotest.test_case "/api/ops takes its feed source from the closure" `Quick
        test_ops_feed_source_comes_from_the_closure;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Server.json_of_ops`.

- [ ] **Step 3: Minimal implementation — lift the keepalive**

In `lib/server.ml`, add immediately below `sse_headers`:

```ocaml
(* The SSE keepalive interval, named rather than written inline in [subscribe].

   /api/ops publishes it, and a published value that had drifted from the timer
   would be a lie about the one number a reader uses to tell `parked` from
   `dead`: a stream with no frame for longer than this and no keepalive either
   is a dropped connection, and a stream with keepalives and no frames is a
   market that is closed. *)
let keepalive = Time_ns.Span.of_sec 20.0
```

and in `subscribe`, replace

```ocaml
           after (Time_ns.Span.to_span_float_round_nearest (Time_ns.Span.of_sec 20.0))
```

with

```ocaml
           after (Time_ns.Span.to_span_float_round_nearest keepalive)
```

- [ ] **Step 4: Minimal implementation — the encoder**

In `lib/server.ml`, insert after `keepalive` and before `subscribe`:

```ocaml
(* ------------------------------------------------------------------------ *)
(* /api/ops                                                                  *)
(* ------------------------------------------------------------------------ *)

(* Which build this is.

   The field the deployment did not have and could not do without: `docker
   compose up -d` is a no-op when the image digest has not changed, so a build
   that silently failed to replace the container serves a healthy old dashboard
   forever, and none of the original nine smoke assertions could tell. Absent
   arguments read `unknown` all the way through -- never a date this process
   invented, which would make the comparison pass while being false. *)
let build_json () : Yojson.Safe.t =
  let sha = Build_info.git_sha in
  `Assoc
    [
      ("git_sha", jstring sha);
      (* Seven characters of a real sha, or the whole word. "unknow" would read
         as a sha that had been truncated rather than as an absence. *)
      ("git_short", jstring (if String.length sha = 40 then String.prefix sha 7 else sha));
      ("built_at", jstring Build_info.built_at);
      ("profile", jstring Build_info.profile);
      ("architecture", jstring Build_info.architecture);
      ("system", jstring Build_info.system);
      ("executable", jstring Stdlib.Sys.executable_name);
    ]

(* The heap, as the runtime sees it.

   quick_stat rather than stat: stat walks the major heap, and a monitoring
   route that triggers a full collection to report on collections is a route
   that changes what it measures. Words rather than bytes, because that is what
   the runtime counts; the page multiplies by the word size and says so. *)
let gc_json () : Yojson.Safe.t =
  let s = Gc.quick_stat () in
  `Assoc
    [
      ("heap_words", `Int (Gc.Stat.heap_words s));
      ("top_heap_words", `Int (Gc.Stat.top_heap_words s));
      ("minor_collections", `Int (Gc.Stat.minor_collections s));
      ("major_collections", `Int (Gc.Stat.major_collections s));
      ("compactions", `Int (Gc.Stat.compactions s));
      ("minor_words", jfloat (Gc.Stat.minor_words s));
      ("promoted_words", jfloat (Gc.Stat.promoted_words s));
      ("major_words", jfloat (Gc.Stat.major_words s));
    ]

(* Resident set size, or nothing.

   The heap above is what the runtime believes; this is what the kernel
   charges, and the gap between them is the answer to "is 41 MB the engine or
   the engine plus OpenBLAS". /proc/self/statm exists on Linux and nowhere
   else, so on macOS this is null -- not zero, which would render as a process
   using no memory at all.

   Read synchronously. The file is forty bytes the kernel materialises on read
   and never blocks on, so a blocking read costs less than making this whole
   encoder deferred would; the page size is 4096 on the amd64 Debian this image
   is built for, and any platform where it is not never reaches this line. *)
let rss_bytes () : int option =
  match Option.try_with (fun () -> Core.In_channel.read_all "/proc/self/statm") with
  | None -> None
  | Some contents -> (
      match String.split (String.strip contents) ~on:' ' with
      | _ :: resident :: _ ->
          Option.map (Option.try_with (fun () -> Int.of_string resident)) ~f:(fun pages ->
              pages * 4096)
      | _ -> None)

(* Where the numbers came from, from the caller rather than from here.

   server.ml must not learn an Alpaca type: it is linked into every mode,
   including the six that have no credentials, and a broker's record reaching
   this module would put the feed's vocabulary in the middle of the wire
   format. So bin/main.ml closes over its two Stats records and hands back the
   whole object. None is the synthetic feed, and it says so in a word rather
   than as four nulls the client has to interpret. *)
let feed_source_json (t : t) : Yojson.Safe.t =
  match t.feed_stats with
  | Some stats -> stats ()
  | None ->
      `Assoc
        [
          ("kind", jstring "synthetic");
          ("alpaca_feed", `Null);
          ("fred_series", `Null);
          ("alpaca", `Null);
          ("fred", `Null);
        ]

(* The operations object.

   Nothing here stabilizes, and that is the one design decision in this
   function. [Graph.feed_health] reads an observer; [Graph.snapshot] would
   settle the graph first, and a route that reported nodes_recomputed after
   advancing it would be measuring itself -- the liveness pulse on /ops would
   then show a flat line of ones whether or not the engine was alive, which is
   worse than no pulse. /api/health may stabilize because its job is the
   current answer; this one's job is the current state.

   Every count is a count and no list of names appears: the live host's book is
   behind a password, the owner reads this page on a phone, and "six symbols,
   one stale" is the whole of what the row is for. *)
let json_of_ops (t : t) : Yojson.Safe.t =
  let health = Graph.feed_health t.graph in
  `Assoc
    [
      ("mode", jstring (mode_to_string t.mode));
      ("started_at", jstring (Time_ns.to_string_utc t.started_at));
      (* A difference, not a percentage. Nothing persists, so there is no
         history to compute availability from and the page says so instead of
         inventing one. *)
      ( "uptime_s",
        jfloat (Time_ns.Span.to_sec (Time_ns.diff (Types.Time.now ()) t.started_at)) );
      ("port", `Int t.port);
      ("pid", `Int (Pid.to_int (Unix.getpid ())));
      (* The CONTAINER id, not the droplet's name, and the page says which. *)
      ("hostname", jstring (Unix.gethostname ()));
      ("ocaml_version", jstring Stdlib.Sys.ocaml_version);
      ("build", build_json ());
      (* Incremental's counters for the whole process: inflated by the startup
         probe and by every fork /api/stress makes. Published because "is it
         alive" is answered by the direction, and labelled on the page because
         a reader who takes nodes_created for the graph's size concludes the
         engine is enormous. *)
      ( "process",
        `Assoc
          [
            ("nodes_recomputed", `Int (Graph.total_nodes_recomputed ()));
            ("stabilizes", `Int (Graph.total_stabilizes ()));
            ("nodes_created", `Int (Graph.total_nodes_created ()));
            ("var_sets", `Int (Graph.total_var_sets ()));
            ("active_observers", `Int (Graph.active_observers ()));
          ] );
      (* The per-graph counts, which forks never reach. Phase 4 fills this from
         the recompute log; until then it is null, because a zero here would
         say "no named node ran", which is the alarm. *)
      ("graph", `Assoc [ ("named", `Null) ]);
      ( "stream",
        `Assoc
          [
            ("frames_sent", `Int t.frames_sent);
            (* Open pipes only. A browser that vanished without closing is
               dropped on the next broadcast, so this can lag by one frame and
               never by a session. *)
            ( "subscribers",
              `Int (List.count t.subscribers ~f:(fun w -> not (Pipe.is_closed w))) );
            ("coalesce_ms", jfloat (Time_ns.Span.to_ms t.coalesce));
            ("keepalive_s", jfloat (Time_ns.Span.to_sec keepalive));
          ] );
      ( "history",
        `Assoc
          [
            ("appended", `Int (History_buffer.appended t.history));
            ("points", `Int (List.length (History_buffer.to_list t.history)));
            ("capacity", `Int (History_buffer.capacity t.history));
          ] );
      ("alerts", json_of_alerts t.alerts);
      ( "feed",
        `Assoc
          [
            ("healthy", `Bool (Graph.Feed_health.all_healthy health));
            ("symbols", `Int (List.length (Graph.symbols t.graph)));
            ("stale", `Int (List.length (Graph.Feed_health.stale health)));
            ("never_seen", `Int (List.length (Graph.Feed_health.never_seen health)));
            (* Quiet ON PURPOSE. Without this the demo host's deliberate stale
               name reads as a broken feed, which is the opposite of the
               demonstration. *)
            ("quiet", `Int (List.length t.quiet));
            ( "staleness_threshold_s",
              jfloat (Time_ns.Span.to_sec (Graph.staleness_threshold t.graph)) );
          ] );
      ("feed_source", feed_source_json t);
      ("gc", gc_json ());
      ("rss_bytes", match rss_bytes () with None -> `Null | Some b -> `Int b);
      (* Phase 5 fills these from Reports.compute and the GARCH domain. Until
         then, absent -- a freshly deployed engine reading `ready` beside an
         empty figure would be the wrong kind of surprise. *)
      ("reports", `Assoc [ ("static", jstring "absent"); ("garch", jstring "absent") ]);
      ("peer", match t.peer with None -> `Null | Some url -> jstring url);
    ]
```

- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: an ops object, because frames_sent and subscriber_count had no reader"
```

---

### Task 12: one routes table, from which `handle` dispatches and the 404 body is generated

**Files:**
- Modify: `lib/server.ml` (replace `handle` at :440–485 wholesale; nothing above it moves)
- Test: `test/test_server.ml`

**Interfaces:**
- Consumes: `json_of_ops : t -> Yojson.Safe.t` (Task 11); `json_headers ~mode` (Task 9); `subscribe : t -> Cohttp_async.Server.response Deferred.t` (existing, :416); `Dashboard_html.page : string` (Phase 1); **`Ops_html.html : string`** — Phase 1's plan (its line 60) deliberately names the ops module's value `html`, not `page`, and explains why; use `html`. `Cohttp_async.Server.response = Response.t * Body.t` and `respond_string : ?flush -> ?headers -> ?status -> string -> response Deferred.t` (`_opam/lib/cohttp-async/server.mli:10,55`); `respond_string` is `respond`, which is `return (resp, body)` (`_opam/lib/cohttp-async/server.ml:125–134`), so the Deferred it returns is already determined and `Deferred.peek` reads it with no scheduler running — the same fact `Toolchain_check.async_peek` relies on. `Cohttp.Response.status`, `Cohttp.Response.headers` (`_opam/lib/cohttp/s.ml:133–135`); `Cohttp.Code.code_of_status : status_code -> int` (`_opam/lib/cohttp/code.mli:145`); `Cohttp.Body.t = [ `Empty | `String of string | `Strings of string list ]` (`_opam/lib/cohttp/body.mli:20`), which `Cohttp_async.Body.t` extends with `` `Pipe ``.
- Produces, and Phase 5's Task 7 appends two entries to it "immediately after the `/api/stress` entry":

```ocaml
type Server.handler = Server.t -> Cohttp_async.Server.response Deferred.t
val Server.routes : (string * string * Server.handler) list   (* path, one-line purpose, handler *)
val Server.route_paths : unit -> string list                  (* the paths, in table order *)
val Server.not_found_body : string                             (* the 404 JSON, generated from the table *)
val Server.lookup : string -> Server.handler option            (* what handle dispatches through *)
val Server.handle : Server.t -> path:string -> Cohttp_async.Server.response Deferred.t   (* unchanged signature *)
```

  The 404 body is `{error: "not found", routes: [path…], purposes: {path: purpose}}`. `routes` is the spec's list and what `deploy/smoke.sh` reads in Task 18; `purposes` is the same table's second column, so a caller reading the API by hand is told what each route is for.

- [ ] **Step 1: Write the failing test**

Add to `test/test_server.ml`, above `let suite =`:

```ocaml
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
    [ "/"; "/ops"; "/api/snapshot"; "/api/health"; "/api/stream"; "/api/history";
      "/api/stress"; "/api/ops" ]
    (Server.route_paths ());
  List.iter (Server.route_paths ()) ~f:(fun path ->
      Alcotest.(check bool) (path ^ " dispatches") true (Option.is_some (Server.lookup path)));
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
          Alcotest.(check int) "an unknown path is 404" 404
            (Cohttp.Code.code_of_status (Cohttp.Response.status response));
          Alcotest.(check string) "with the generated body" Server.not_found_body s
      | _ -> Alcotest.fail "the 404 did not answer as a string")
    ()
```

and register both in the suite list, after the two `/api/ops` cases from Task 11:

```ocaml
      Alcotest.test_case "the 404 body lists exactly the routes table" `Quick
        test_the_404_lists_exactly_the_routes;
      Alcotest.test_case "/api/ops and /ops answer through the table" `Quick
        test_the_two_new_routes_answer;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound value Server.not_found_body`.

- [ ] **Step 3: Minimal implementation**

In `lib/server.ml`, replace the whole of `handle` — from `let handle (t : t) ~(path : string) =` through the closing `]))` of its 404 arm (currently :440–485) — with:

```ocaml
(* ------------------------------------------------------------------------ *)
(* The routes table                                                          *)
(* ------------------------------------------------------------------------ *)

(* One table, two readers.

   [handle] dispatches from it and the 404 body lists it, and until now the
   two were separate literals -- a match with seven arms and a list of six
   strings -- which is how a route can be served for weeks while the 404 body
   tells a caller it does not exist. A route that is in one and not the other
   is now a compile-time impossibility rather than a review-time hope, and the
   test in test_server.ml that compares the two is there for the day someone
   reintroduces the second literal.

   The purpose is one line, in this repository's voice, and goes on the 404
   body beside the path: an unknown route is the one moment a caller is
   reading the API by hand, and a bare list of paths answers "what exists"
   without answering "which one did I mean".

   Handlers take [t] and nothing else. Every route is a GET whose method is
   ignored and whose query string is ignored, and that is the whole contract:
   a handler that wanted the request would be a handler that could be asked to
   do something, and no route here does anything. *)
type handler = t -> Cohttp_async.Server.response Deferred.t

let routes : (string * string * handler) list =
  [
    ( "/",
      "the dashboard: the book, its limits and the trail, over the stream",
      fun _ ->
        Cohttp_async.Server.respond_string ~headers:html_headers Dashboard_html.page );
    ( "/ops",
      "the operations page: which build, how long, and what the process is doing",
      fun _ -> Cohttp_async.Server.respond_string ~headers:html_headers Ops_html.html );
    ( "/api/snapshot",
      "the whole book as JSON, with the counter that proves the graph is alive",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode) (render t)
    );
    ( "/api/health",
      "feed liveness per symbol; healthy is false when anything is stale",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string
             (json_of_feed_health (Graph.Snapshot.feed_health (Graph.snapshot t.graph))))
    );
    ( "/api/stream",
      "server-sent events, one frame per graph change, coalesced over 80 ms",
      subscribe );
    (* Read from the buffer as it stands; nothing is computed here. The buffer
       is filled by an observer on the graph, so this route is a read of state
       that already exists rather than a request that causes work -- the same
       property /api/snapshot has, and the reason neither can stall the
       engine. *)
    ( "/api/history",
      "the in-memory trail, column-major, lost on restart",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_history t.history)) );
    (* Scenarios are computed on demand rather than pushed on the stream, and
       the reason is the cost asymmetry. A snapshot is read from observers
       that have already settled; a scenario suite forks the engine once per
       scenario and stabilizes each fork. Putting that behind the SSE loop
       would mean paying it on every tick to serve a number nobody is looking
       at most of the time.

       Still a GET with no body and no effect: stress.ml runs every scenario
       on a fork and destroys it, so this route cannot move the live book. *)
    ( "/api/stress",
      "the scenario suite, run on a fork of the book as it stands",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_stress t.graph)) );
    ( "/api/ops",
      "what this process is: build, uptime, counters, stream, feed, alerts, heap",
      fun t ->
        Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
          (Yojson.Safe.to_string (json_of_ops t)) );
  ]

let route_paths () : string list = List.map routes ~f:(fun (path, _, _) -> path)

(* The 404 body, generated from the table and rendered once: the table does
   not change after the module is initialised, and an unknown route is not a
   reason to serialise anything. *)
let not_found_body : string =
  Yojson.Safe.to_string
    (`Assoc
      [
        ("error", `String "not found");
        ("routes", jlist jstring (route_paths ()));
        ( "purposes",
          `Assoc (List.map routes ~f:(fun (path, purpose, _) -> (path, `String purpose))) );
      ])

(* /index.html is the one alias. It has been answered since Phase 3 and a
   bookmark to it must not start 404ing; it is not in the table because it is
   not a route, it is a spelling of one, and the 404 body should not offer a
   caller two names for the same page. *)
let lookup (path : string) : handler option =
  let path = if String.equal path "/index.html" then "/" else path in
  List.find_map routes ~f:(fun (p, _, h) -> if String.equal p path then Some h else None)

let handle (t : t) ~(path : string) =
  match lookup path with
  | Some handler -> handler t
  | None ->
      Cohttp_async.Server.respond_string ~headers:(json_headers ~mode:t.mode)
        ~status:`Not_found not_found_body
```

The `(* Routes *)` banner and the three header values above it stay where they are; `subscribe` and `json_of_ops` (Task 11) are already defined above this point, which is why the table can name them.

- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
grep -c 'headers:(json_headers ~mode:t.mode)' lib/server.ml   # 6 now: five from Task 9 plus /api/ops
```

- [ ] **Step 5: See the table on a real socket**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make build >/dev/null 2>&1
(./_build/default/bin/main.exe demo 8097 >/tmp/ohcamel-demo-8097.log 2>&1 &)
/bin/sleep 4
curl -sS http://localhost:8097/api/ops | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["mode"], d["build"]["git_short"], "up", round(d["uptime_s"]), "peer", d["peer"])'
curl -sS -o /dev/null -w 'GET /ops -> %{http_code} %{content_type}\n' http://localhost:8097/ops
curl -sS http://localhost:8097/api/nope | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["error"]); print("\n".join(d["routes"]))'
curl -sS -o /dev/null -w 'GET /index.html -> %{http_code}\n' http://localhost:8097/index.html
curl -sSI http://localhost:8097/api/ops | grep -i 'access-control-allow-origin'
pkill -f "main.exe demo 8097"
```

Expected: `demo <seven hex> up 4 peer None` (the sha is real because `make build` stamps it); `GET /ops -> 200 text/html; charset=utf-8`; `not found` followed by the eight paths in table order; `GET /index.html -> 200`; `Access-Control-Allow-Origin: *`. The `(… &)` subshell detaches the demo from this shell; do not run it as a background task of the tool, which takes the server down with it.

- [ ] **Step 6: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/server.ml test/test_server.ml
git commit -m "server: one routes table, because the dispatcher and the 404 body had each been listing routes on their own"
```

---

### Task 13: the two servers are told what they are — the live host's feed, peer and alerts; the demo's quiet name; the banner

**Files:**
- Create: `lib/feed/feed_source.ml`
- Modify: `bin/main.ml` (`run_live`'s options banner at :1571–1574; `run_live`'s `Server.create` at :1671–1673; `run_demo`'s `Server.create` at :1764 and the `let quiet, _, _, _ = List.last_exn book in` at :1780 — line numbers as of Task 8's commit; grep for `Server.create` and `Nothing here invents one`)
- Test: `test/test_feed.ml`

**Interfaces:**
- Consumes: `Server.create ?coalesce ?history_capacity ?alerts ?peer ?feed_stats ?quiet ~mode ~graph ~factor ()` exactly as Task 8 defines it; `Alpaca_ws.Stats.to_json` and `Fred_client.Stats.to_json` (Task 6); `Config.Runtime.peer_origin : string option` (Task 7); `run_live`'s existing bindings `alerts : Alerts.t option` (bin/main.ml:1579), `alpaca_stats : Alpaca_ws.Stats.t` (:1589), `fred_stats : Fred_client.Stats.t` (:1590), `runtime.Config.Runtime.alpaca_feed : string` and `runtime.Config.Runtime.fred_series_id : string` (lib/config.ml:250,253); `run_demo`'s `book`, whose last entry is `CVX` (bin/main.ml:63). `lib/dune` compiles every `.ml` under `lib/` and `lib/feed/` with `(include_subdirs unqualified)`, so the new module needs no dune edit.
- Produces: `Feed_source.live : alpaca_feed:string -> fred_series:string -> alpaca:Alpaca_ws.Stats.t -> fred:Fred_client.Stats.t -> unit -> Yojson.Safe.t` — partially applied, it is the `unit -> Yojson.Safe.t` closure `Server.create ?feed_stats` takes. Its five keys are the ones `Server.feed_source_json`'s synthetic branch (Task 11) emits as nulls: `kind`, `alpaca_feed`, `fred_series`, `alpaca`, `fred`.

Why this task exists at all: Task 8 changed the two call sites only enough to compile (`~mode`). Without this task the live host's `/api/ops` says `feed_source.kind: "synthetic"` and `peer: null`, and the demo host counts its deliberately quiet name as a broken feed — three lies on the one page that exists to tell the truth. The spec's build order lists all of it under Phase 2 (`docs/superpowers/specs/2026-09-02-the-page-design.md:302`).

- [ ] **Step 1: Write the failing test**

Add to `test/test_feed.ml`, above `let suite =`:

```ocaml
(* The closure run_live hands the server, and the one property it must have.

   It reads the two records when it is CALLED, not when it is built. Both
   Stats records are mutable and climb for the life of the process; a closure
   that captured their values at startup would report, forever, a socket that
   never received a frame -- which is precisely the failure /ops is meant to
   make visible, arriving disguised as a measurement. The key set is the one
   server.ml's synthetic branch emits with nulls, so the page can read either
   host's feed_source without first asking which host it is. *)
let test_feed_source_reads_at_call_time () =
  let alpaca = Alpaca.Stats.create () in
  let fred = Fred.Stats.create () in
  let source =
    Ohcamel.Feed_source.live ~alpaca_feed:"iex" ~fred_series:"DGS10" ~alpaca ~fred
  in
  let field j key =
    match j with
    | `Assoc fields -> (
        match List.Assoc.find fields key ~equal:String.equal with
        | Some v -> v
        | None -> Alcotest.failf "missing key %S" key)
    | _ -> Alcotest.fail "not an object"
  in
  let str j key = match field j key with `String s -> s | _ -> "?" in
  let int j key = match field j key with `Int n -> n | _ -> -1 in
  let first = source () in
  Alcotest.(check string) "kind" "alpaca" (str first "kind");
  Alcotest.(check string) "the feed name travels" "iex" (str first "alpaca_feed");
  Alcotest.(check string) "and the series id" "DGS10" (str first "fred_series");
  Alcotest.(check int) "no frames yet" 0 (int (field first "alpaca") "frames");
  Alcotest.(check int) "no polls yet" 0 (int (field first "fred") "polls");
  (* The records move, as they do for the life of the process ... *)
  alpaca.Alpaca.Stats.frames <- 12;
  alpaca.Alpaca.Stats.reconnects <- 1;
  fred.Fred.Stats.polls <- 3;
  (* ... and the SAME closure reports the new values. *)
  let second = source () in
  Alcotest.(check int) "frames, read at call time" 12 (int (field second "alpaca") "frames");
  Alcotest.(check int) "reconnects, read at call time" 1
    (int (field second "alpaca") "reconnects");
  Alcotest.(check int) "polls, read at call time" 3 (int (field second "fred") "polls");
  (* Exactly the five keys, in this order: a client that switches on `kind`
     and then reads the other four must find them on both hosts. *)
  Alcotest.(check (list string))
    "the five keys, in order"
    [ "kind"; "alpaca_feed"; "fred_series"; "alpaca"; "fred" ]
    (match second with `Assoc fields -> List.map fields ~f:fst | _ -> [])
```

and register it in the suite list, after `"config: an empty peer origin is an absent one"` (Task 7's case):

```ocaml
      Alcotest.test_case "feed_source: the live closure reads its records at call time"
        `Quick test_feed_source_reads_at_call_time;
```

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | tail -20
```

Expected: `Error: Unbound module Ohcamel.Feed_source`.

- [ ] **Step 3: Minimal implementation — `lib/feed/feed_source.ml`**

Create the file:

```ocaml
(* The live feed's counters, packaged for /api/ops.

   server.ml must not learn an Alpaca or FRED type. It is linked into every
   mode, including the six that have no credentials, and a broker's record
   reaching the wire module would put the feed's vocabulary in the middle of
   the wire format. So the object is assembled HERE, on the feed side of that
   line, and run_live hands Server.create the partially-applied result as a
   closure.

   The closure reads the two records at call time, never at creation. Both
   are mutable and climb for the life of the process; a snapshot taken at
   startup would report, forever, a socket that never received a frame --
   which is the failure /ops exists to show, arriving disguised as a
   measurement.

   The key set is the one server.ml's synthetic branch emits with nulls, so
   the page never has to ask which host it is reading before asking what the
   feed is doing. *)
let live ~(alpaca_feed : string) ~(fred_series : string) ~(alpaca : Alpaca_ws.Stats.t)
    ~(fred : Fred_client.Stats.t) () : Yojson.Safe.t =
  `Assoc
    [
      ("kind", `String "alpaca");
      ("alpaca_feed", `String alpaca_feed);
      ("fred_series", `String fred_series);
      ("alpaca", Alpaca_ws.Stats.to_json alpaca);
      ("fred", Fred_client.Stats.to_json fred);
    ]
```

- [ ] **Step 4: Minimal implementation — `bin/main.ml`, three edits**

(a) The banner. In `run_live`, the `printf` under the options comment ends:

```ocaml
        \              surface. Nothing here invents one.\n\n";
```

Replace that one line with:

```ocaml
        \              surface. Nothing here invents one for the live book.\n\n";
```

The qualification is the spec's (*amended*, `docs/superpowers/specs/2026-09-02-the-page-design.md:258`): Phase 5 makes this process build a synthetic-surface graph for the options figure, labelled synthetic, and the terminal must not contradict the page. It is a string literal, so ocamlformat leaves its line breaks alone.

(b) The live call site. In `run_live`, replace:

```ocaml
            let server =
              Server.create ~mode:`Live ~graph
                ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

with:

```ocaml
            (* Everything the process knows about itself, handed over once.
               ?alerts was omitted before this phase, so the live dashboard
               could not report the state the switch always had; the peer is
               the public demo host, when compose says where it is; the feed
               closure is read on every /api/ops, not captured here. *)
            let server =
              Server.create ?alerts ~mode:`Live ?peer:runtime.Config.Runtime.peer_origin
                ~feed_stats:
                  (Feed_source.live ~alpaca_feed:runtime.Config.Runtime.alpaca_feed
                     ~fred_series:runtime.Config.Runtime.fred_series_id
                     ~alpaca:alpaca_stats ~fred:fred_stats)
                ~graph ~factor:runtime.Config.Runtime.fred_series_id ()
            in
```

`alerts`, `alpaca_stats` and `fred_stats` are all bound above this point in `run_live` (:1579, :1589, :1590) and are already in scope; nothing moves.

(c) The demo call site. In `run_demo`, replace:

```ocaml
  let server = Server.create ?alerts ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
```

with:

```ocaml
  (* The one symbol that is never ticked -- the comment above [tickable] says
     why. Named here, above Server.create, because the server has to be TOLD
     it is quiet on purpose: without that, /api/ops counts it as a broken feed
     and the demonstration reads as an outage. *)
  let quiet, _, _, _ = List.last_exn book in
  let server =
    Server.create ?alerts ~mode:`Demo ~quiet:[ quiet ] ~graph ~factor:"SYNTHETIC" ()
  in
```

and, further down in `run_demo`, replace the two lines:

```ocaml
  let quiet, _, _, _ = List.last_exn book in
  let tickable = List.filter book ~f:(fun (s, _, _, _) -> not (Symbol.equal s quiet)) in
```

with the second line alone:

```ocaml
  let tickable = List.filter book ~f:(fun (s, _, _, _) -> not (Symbol.equal s quiet)) in
```

The why-comment that precedes those lines (`One symbol is deliberately never ticked. …`) stays exactly where it is; only the binding moves up.

- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make fmt
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -5 && dune runtest --force 2>&1 | tail -10
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt 2>&1 | tail -5
grep -n "Nothing here invents one" bin/main.ml
grep -c "let quiet, _, _, _ = List.last_exn book in" bin/main.ml
```

Expected: a green build and suite; exactly one `Nothing here invents one` line, reading `… invents one for the live book.\n\n";`; the `grep -c` prints `1`.

- [ ] **Step 6: See it on a real socket**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
make build >/dev/null 2>&1
(./_build/default/bin/main.exe demo 8097 >/tmp/ohcamel-demo-8097.log 2>&1 &)
/bin/sleep 25
curl -sS http://localhost:8097/api/ops | python3 -c '
import json, sys
d = json.load(sys.stdin)
f = d["feed"]
print("mode", d["mode"], "| feed_source.kind", d["feed_source"]["kind"], "| peer", d["peer"])
print("symbols", f["symbols"], "stale", f["stale"], "never_seen", f["never_seen"], "quiet", f["quiet"], "threshold_s", f["staleness_threshold_s"])
print("alerts.enabled", d["alerts"]["enabled"], "kill_switch", d["alerts"]["kill_switch"], "trips_on", d["alerts"]["trips_on"])
'
pkill -f "main.exe demo 8097"
```

Expected: `mode demo | feed_source.kind synthetic | peer None`; `symbols 6 stale 1 never_seen 0 quiet 1 threshold_s 20.0` — the 25-second sleep is so CVX has crossed the 20 s demo threshold and the page can be seen to count it as both stale and quiet-by-design; `alerts.enabled True kill_switch armed trips_on ['nvda-cap']` (or `tripped` with `tripped_by nvda-cap`, if the random walk has already crossed the 54,200 cap — both are correct readings of a live demo). The live call site cannot be exercised without credentials; its closure is the hermetic test above and its call site is type-checked by the build.

- [ ] **Step 7: Diff the six credential-free modes against the Task 1 baseline**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
for m in synthetic stress backtest backtest-crisis options garch; do
  (eval $(opam env --switch=$PWD --set-switch) && dune exec bin/main.exe -- "$m") \
    > "/tmp/ohcamel-phase2/after/$m.txt" 2>/dev/null
  if diff -q "/tmp/ohcamel-phase2/before/$m.txt" "/tmp/ohcamel-phase2/after/$m.txt" >/dev/null; then
    echo "IDENTICAL $m"
  else
    echo "CHANGED   $m"; diff "/tmp/ohcamel-phase2/before/$m.txt" "/tmp/ohcamel-phase2/after/$m.txt" | head -20
  fi
done
```

All six must print `IDENTICAL`. None of the six modes passes through `run_live` or `run_demo`, so a differing byte is a regression, not a cost. (Task 16 repeats this against a baseline built from a clean worktree, which is the gate of record; this is the quick check that makes Task 16 boring.)

- [ ] **Step 8: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add lib/feed/feed_source.ml bin/main.ml test/test_feed.ml
git commit -m "main: each server is told what it is, because the live host was reporting a synthetic feed and the demo a broken one"
```

---

### Task 14: `web/ops.html` + `web/ops.js` — the operations page, both hosts, three strips, and what it cannot see

**Files:**
- Modify: `web/ops.html` (replace Phase 1's placeholder wholesale)
- Modify: `web/ops.js` (replace Phase 1's placeholder wholesale)
- Modify: `test/test_embedded_assets.ml` (the one placeholder assertion inside `test_the_ops_page_is_assembled_in_order`)
- Test: `test/test_embedded_assets.ml`; a `curl` assertion against a local demo; a headless-Chrome DOM check (no Node, no Playwright)

**Interfaces:**
- Consumes: Phase 1's `lib/dune` rule for `ops_html.ml`, which cats `web/head.html`, `<style>`, `web/page.css`, `</style></head><body>`, **`web/ops.html`**, `<script>`, **`web/ops.js`**, `</script></body></html>` into `Ops_html.html : string` — note it cats `ops.js` alone, so everything the page runs is in that one file (Phase 1 creates no `web/format.js`; the `money/pct/el` helpers live in `dashboard.js`, which this page does not load). `GET /ops` from Task 12's routes table (`html_headers`, no CORS). The wire: `GET /api/ops` exactly as Task 11 keys it (`mode`, `uptime_s`, `started_at`, `pid`, `hostname`, `ocaml_version`, `build{git_sha,git_short,built_at,profile,architecture,system}`, `process{…}`, `graph{named}`, `stream{frames_sent,subscribers,coalesce_ms,keepalive_s}`, `history{appended,points,capacity}`, `alerts{…}` as Task 10, `feed{healthy,symbols,stale,never_seen,quiet,staleness_threshold_s}`, `feed_source{kind,alpaca_feed,fred_series,alpaca,fred}`, `gc{…}`, `rss_bytes`, `reports{static,garch}`, `peer`); `GET /api/health` as `lib/server.ml:65` encodes it today (`healthy`, `stale[]`, `never_seen[]`, `symbols[{symbol,last_tick,never_seen,stale}]`) — the per-symbol ages the feed-age strip needs, which `/api/ops` deliberately does not carry; `GET /api/stream` (same origin only: `sse_headers` never carry CORS, Task 9). The CSS tokens and classes in `web/page.css` (`--ink --ink-soft --ink-faint --rule --over --over-wash --unknown --unknown-wash --live --mark`, `.lbl`, `.num`, `td.k` with `em`, `td.v`, `section`, `header .stat`, `footer`). Demo-mode CORS on JSON (Task 9), which is what lets the live origin fetch the demo's two JSON routes.
- Produces: the page at `GET /ops`. Its DOM contract, which Task 15's smoke assertion and Task 17's deploy read by eye: `#this-host` and `#peer` (two `section.host`), each filled with `.grp` groups of `td.k`/`td.v` rows in the order **engine · (pulse strips) · feed · (feed-age strip) · [alpaca · fred] · stream · book moving · process · alerts · resources · reports**; `#cannot` last. The peer column's three fixed texts: `gated` + `the live host is behind a password — open live.ohcamel…/ops to see both` (demo origin, no peer); `unreachable from this browser` (a failed peer fetch — never `down`); `no peer configured` (live origin without `OHCAMEL_PEER_ORIGIN`). One query parameter, `?peer=<origin>`, overrides the peer for this browser only — it is how the unreachable branch is tested below and how the owner can point a laptop at any origin; it renders through `textContent` only, so an attacker-supplied origin can put nothing but its JSON's numbers on the page.

What the page is, in one paragraph, so the code below reads as choices rather than as a form: two equal columns, `this host` and `peer`, in the ledger's own key/value idiom; one column on a phone with this host first. It polls `/api/ops` and `/api/health` every 10 s while the tab is visible — sixty polls is ten minutes, the window the pulse strips show — and draws the *differences*: Δ `nodes_recomputed` and Δ `frames_sent` per poll as 1 px-gapped bars, where a run of zeros is a flat line and is the alarm, and a failed poll is one `--over` bar. Beneath this host's engine rows, the frame-arrival strip from the spec's §08: one tick per SSE frame at its browser arrival time on a 60 s axis, the trailing 20 s shaded, captioned `N distinct frames spread over M s` — the smoke suite's load-bearing assertion, drawn. The feed-age strip is one unlabelled dot per symbol on a 0..threshold axis, past the threshold in `--over`, never-seen as an open ring in `--unknown`. Nothing here computes risk; every number is a served field or the subtraction of two, and every unknown is a word in `--unknown`, never a zero.

- [ ] **Step 1: Write the failing test**

In `test/test_embedded_assets.ml`, inside `test_the_ops_page_is_assembled_in_order`, replace Phase 1's placeholder assertion:

```ocaml
  (* Phase 1 builds this module and no route reaches it. Asserting the
     placeholder's own words here is what makes Phase 2's job "replace the body"
     rather than "first find out whether the rule works at all". *)
  Alcotest.(check bool)
    "says, in the page, that it is not built yet" true
    (String.is_substring Ops_html.html ~substring:"this page is not built yet")
```

with:

```ocaml
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
    ]
    ~f:(fun needle ->
      Alcotest.(check bool)
        ("the ops page carries " ^ needle)
        true
        (String.is_substring Ops_html.html ~substring:needle));
  Alcotest.(check bool)
    "and no longer says it is not built" false
    (String.is_substring Ops_html.html ~substring:"this page is not built yet")
```

The rest of that test — the marker order (`<!doctype html>`, `<style>`, `--ground:`, `</style>`, `<body>`, `OhCamel<span>operations</span>`, `<script>`, `"use strict"`, `</script>`, `</html>`) and the shared-head comparison — stays exactly as Phase 1 wrote it, and the page below is written to keep passing it: the `h1` keeps `OhCamel<span>operations</span>`, `ops.js` opens with `"use strict"`, and the page's own `<style>` block sits in the body *after* `<body>`, so the "both pages share one head and one stylesheet" prefix comparison is untouched.

- [ ] **Step 2: Run it and see it fail**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune runtest --force 2>&1 | grep -A3 "ops page" | head -20
```

Expected: `[FAIL]` on `the ops page is assembled in the rule's order`, with `the ops page carries id="this-host"` — `Expected: true, Received: false`.

- [ ] **Step 3: Minimal implementation — `web/ops.html`**

Replace the file wholesale. Keep the leading blank line (the rule's `(echo "…<body>\n")` supplies the newline before it, the same shape `web/index.html` has):

```html

<style>
  /* The ops page's own rules. page.css is shared byte for byte with the
     dashboard -- test_embedded_assets asserts it -- so the few things this page
     needs that the ledger does not are scoped under main.ops and live here,
     inside the body, rather than forking the shared sheet or moving the
     dashboard's bytes. Two equal columns; one on a phone, this host first. */
  main.ops { grid-template-columns: 1fr 1fr; }
  @media (max-width: 900px) { main.ops { grid-template-columns: 1fr; } }
  main.ops #cannot { grid-column: 1 / -1; }
  main.ops #cannot p { max-width: 68ch; font-size: 13px; color: var(--ink-soft); margin: 0 0 10px; }
  main.ops #cannot code { font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
  .grp { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--rule); }
  .grp:first-child { margin-top: 0; padding-top: 0; border-top: 0; }
  .grp > .lbl { display: block; margin-bottom: 6px; }
  td.v.unknown, .unknown { color: var(--unknown); }
  td.v.over, .over { color: var(--over); }
  td.v.live, .live { color: var(--live); }
  /* Three strips. Inline SVG, sized by the column, drawn by ops.js. */
  .strip { margin: 8px 0 2px; }
  .strip svg { display: block; width: 100%; max-width: 360px; height: 26px; }
  .strip .cap { display: block; font-size: 11px; color: var(--ink-faint); margin-top: 3px; }
  /* The peer column's three fixed sentences: a cannot-evaluate, in the
     unknown wash, because none of them may look like fine or like failure. */
  .note { font-size: 13px; color: var(--unknown); background: var(--unknown-wash); padding: 10px 12px; margin: 6px 0 10px; }
  .note b { font-weight: 620; }
  section.host > .lbl i { font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace; }
</style>

<header>
  <h1>OhCamel<span>operations</span></h1>
  <div class="stat"><span class="lbl">this host</span><span class="v" id="h-mode">—</span></div>
  <div class="stat"><span class="lbl">build</span><span class="v num" id="h-build">—</span></div>
  <div class="stat"><span class="lbl">up</span><span class="v num" id="h-up">—</span></div>
  <div class="stat spacer"><span class="lbl">last poll</span><span class="v num" id="h-poll">—</span></div>
  <div class="stat"><a href="/">dashboard</a></div>
</header>

<main id="main" class="ops">
  <section id="this-host" class="host">
    <span class="lbl">this host <i id="this-origin"></i></span>
    <div id="this-rows"><p class="cap">asking /api/ops…</p></div>
  </section>

  <section id="peer" class="host">
    <span class="lbl">peer <i id="peer-origin"></i></span>
    <div id="peer-rows"><p class="cap">waiting for this host to say whether it has one</p></div>
  </section>

  <section id="cannot">
    <span class="lbl">what this page cannot see</span>
    <p>Nothing persists, so there is no uptime percentage and no record of the
    last smoke run; its two load-bearing assertions — the counter advancing
    across two seconds and frames spread across a window rather than piled at
    its end — are re-run in this browser instead, in the strips above.</p>
    <p>CPU share, Caddy's memory, request counts per route and the
    certificate's expiry are not engine-observable; <code>docker stats</code>
    and Caddy's stdout have them. <code>hostname</code> is the container id,
    not the droplet.</p>
    <p>From the public origin the live book is not visible, by name or by
    number: it is gated, and this page does not route around the password.
    The peer column is filled only from the live origin, over the demo
    engine's own CORS header, and nothing crosses the other way.</p>
    <p>The stream state is this browser's reading. A proxy that dropped the
    stream silently would read as <code>parked</code> until the browser
    reconnected; what separates <code>parked</code> from dead is that
    <code>/api/ops</code> still answers, and that is the reading used.</p>
    <p>The counters under <em>process</em> are Incremental's for the whole
    process — the startup probe and every fork <code>/api/stress</code> makes —
    and are not this graph's size. <em>book moving</em> is the one rate a fork
    cannot inflate.</p>
  </section>
</main>

<footer>
  <span>this is the one page that asks on a timer, because a process's uptime is not a graph change</span>
  <span id="cadence">polls /api/ops every 10 s while this tab is visible · sixty polls is ten minutes</span>
</footer>
```

- [ ] **Step 4a: Minimal implementation — `web/ops.js`, the helpers and the three strips**

Replace the file wholesale. This is the first half; Step 4b appends the second half to the same file, and the IIFE closes there. The file must open exactly as shown — Phase 1's marker test looks for `"use strict"` after `<script>`.

```javascript
(function () {
  "use strict";

  // /ops is the one page that asks on a timer. The dashboard is pushed a frame
  // when a value changes; a process's uptime, its heap and its build sha are
  // not graph changes and never will be. So this page polls /api/ops -- and
  // /api/health, for the per-symbol ages /api/ops deliberately does not
  // carry -- every ten seconds while the tab is visible, and draws the
  // DIFFERENCES between polls. Sixty polls is ten minutes, the window the
  // pulse strips show. Nothing here computes risk: every number is a served
  // field or the subtraction of two, and every unknown is a word in the
  // unknown colour, never a zero.
  var POLL_MS = 10000;
  var WINDOW = 60;         // polls kept per host
  var ARRIVAL_MS = 60000;  // the frame-arrival strip's axis
  var SPREAD_MS = 20000;   // the smoke suite's window, shaded on that axis

  document.title = "OhCamel — operations";

  // ---- helpers ------------------------------------------------------------
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function int(x) { return isNum(x) ? Math.round(x).toLocaleString("en-US") : null; }
  // The runtime counts words; the page multiplies by the word size and says so.
  function mb(words) { return isNum(words) ? (words * 8 / 1e6).toFixed(1) + " MB" : null; }

  // Core's Time_ns.to_string_utc: "2026-09-03 12:34:56.123456789Z". Date.parse
  // is not promised to read a space and nine fractional digits, so it is read
  // by hand; a string that does not match is null, never NaN.
  function parseUtc(s) {
    var m = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z$/.exec(s || "");
    if (!m) return null;
    var ms = m[7] ? parseInt((m[7] + "000").slice(0, 3), 10) : 0;
    return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms);
  }
  function stamp(s) { return typeof s === "string" && s.length >= 19 ? s.slice(0, 19) + "Z" : s; }
  // 3d 4h 12m, the spec's form; under an hour, minutes and seconds.
  function duration(s) {
    if (!isNum(s)) return null;
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d > 0) return d + "d " + h + "h " + m + "m";
    if (h > 0) return h + "h " + m + "m";
    return m + "m " + Math.floor(s % 60) + "s";
  }
  // An age, re-rendered once a second as text with no transition.
  function age(ms) {
    if (!isNum(ms)) return null;
    if (ms < 10000) return (ms / 1000).toFixed(1) + " s";
    if (ms < 120000) return Math.round(ms / 1000) + " s";
    return Math.floor(ms / 60000) + " m " + Math.round(ms % 60000 / 1000) + " s";
  }

  // A group of rows in the ledger's idiom: td.k, an em note, td.v. A row is
  // [key, value, className, note, id]; a null value renders as the em dash,
  // which is the page's word for "not measured" and is never a zero.
  function group(container, label, rows) {
    var g = el("div", "grp");
    g.appendChild(el("span", "lbl", label));
    var t = el("table");
    rows.forEach(function (r) {
      var tr = el("tr");
      var k = el("td", "k", r[0]);
      if (r[3]) k.appendChild(el("em", null, r[3]));
      tr.appendChild(k);
      var v = el("td", "v num" + (r[2] ? " " + r[2] : ""),
                 r[1] === null || r[1] === undefined ? "—" : String(r[1]));
      if (r[4]) v.id = r[4];
      tr.appendChild(v);
      t.appendChild(tr);
    });
    g.appendChild(t);
    container.appendChild(g);
    return g;
  }
  function note(container, word, sentence) {
    var n = el("div", "note");
    n.appendChild(el("b", null, word));
    if (sentence) n.appendChild(document.createTextNode(" — " + sentence));
    container.appendChild(n);
    return n;
  }

  // ---- the three strips ---------------------------------------------------

  // Δ per poll as 1 px-gapped bars, scaled to the window's own maximum and to
  // nothing else: a run of zeros is a flat line and is the alarm, and needs no
  // threshold. A failed poll is one full-height --over bar, because "the page
  // could not ask" and "the engine answered zero" are different facts.
  function pulse(container, label, deltas, key) {
    var W = WINDOW * 4, H = 24, max = 0;
    deltas.forEach(function (d) { if (!d.failed && d[key] > max) max = d[key]; });
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("line", { x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5, stroke: "var(--rule)", "stroke-width": 1 }));
    deltas.forEach(function (d, i) {
      var x = (WINDOW - deltas.length + i) * 4;
      if (d.failed) { s.appendChild(svg("rect", { x: x, y: 0, width: 3, height: H, fill: "var(--over)" })); return; }
      if (!(d[key] > 0)) return;
      var h = Math.max(1, Math.round(d[key] / max * (H - 2)));
      s.appendChild(svg("rect", { x: x, y: H - 1 - h, width: 3, height: h, fill: "var(--ink)" }));
    });
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    var last = deltas.length ? deltas[deltas.length - 1] : null;
    wrap.appendChild(el("span", "cap",
      label + " · " + deltas.length + " of " + WINDOW + " polls · latest " +
      (last === null ? "—" : last.failed ? "poll failed" : "+" + int(last[key])) + " · max " + int(max)));
    container.appendChild(wrap);
  }

  // One dot per symbol on a 0..threshold axis, unlabelled. Staleness is age
  // against a threshold and nothing else; names stay off this strip even where
  // they are public, so the two hosts' strips read the same way. Past the
  // threshold the dot sits at the right end in --over; never seen is an open
  // ring in --unknown at the left, because it has no age to be placed by.
  function ageStrip(container, health, threshold_s, now) {
    var W = 240, H = 16, L = 6, R = W - 6;
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("line", { x1: L, y1: H / 2, x2: R, y2: H / 2, stroke: "var(--rule)", "stroke-width": 1 }));
    s.appendChild(svg("line", { x1: R, y1: 2, x2: R, y2: H - 2, stroke: "var(--ink-faint)", "stroke-width": 1 }));
    var symbols = health.symbols || [], over = 0, never = 0;
    symbols.forEach(function (sym) {
      var t = sym.never_seen ? null : parseUtc(sym.last_tick);
      if (t === null) {
        never++;
        s.appendChild(svg("circle", { cx: L, cy: H / 2, r: 3, fill: "none", stroke: "var(--unknown)", "stroke-width": 1.5 }));
        return;
      }
      var a = (now - t) / 1000, frac = Math.min(1, Math.max(0, a / threshold_s));
      if (a > threshold_s) over++;
      s.appendChild(svg("circle", { cx: L + frac * (R - L), cy: H / 2, r: 3, fill: a > threshold_s ? "var(--over)" : "var(--ink)" }));
    });
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    wrap.appendChild(el("span", "cap",
      "age of last print, 0 → " + threshold_s + " s · " + symbols.length + " symbols · " +
      over + " past the threshold · " + never + " never seen"));
    container.appendChild(wrap);
  }

  // One tick per SSE frame at its browser arrival time on a 60 s axis, the
  // trailing 20 s shaded: the smoke suite's spread assertion, drawn. The
  // caption counts distinct frames in that 20 s and the seconds they spread
  // over, which is what deploy/smoke.sh prints; a frame is distinct when its
  // data differs, as the suite's `sort -u` counts it. The parked caption is
  // the live host's at night. On the demo host, whose feed ticks every 400 ms,
  // the same reading means the engine, not the market, has stopped -- and the
  // caption says which host it is on.
  function arrivalStrip(container, frames, now, state, mode) {
    var W = 300, H = 18, shadeX = W * (1 - SPREAD_MS / ARRIVAL_MS);
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("rect", { x: shadeX, y: 0, width: W - shadeX, height: H, fill: "var(--rule)", opacity: 0.55 }));
    s.appendChild(svg("line", { x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5, stroke: "var(--rule)", "stroke-width": 1 }));
    var recent = [], seen = {};
    frames.forEach(function (f) {
      var x = W * (1 - (now - f.t) / ARRIVAL_MS);
      if (x < 0) return;
      s.appendChild(svg("line", { x1: x, y1: 3, x2: x, y2: H - 3, stroke: "var(--ink)", "stroke-width": 1 }));
      if (now - f.t <= SPREAD_MS) { recent.push(f); seen[f.data] = true; }
    });
    var distinct = Object.keys(seen).length;
    var spread = recent.length >= 2 ? ((recent[recent.length - 1].t - recent[0].t) / 1000).toFixed(1) : "0";
    var cap;
    if (recent.length === 0 && state === "parked" && mode === "live") {
      cap = "0 frames — nothing changed; the stream is parked, not dead; the counter above still advances from the 5 s clock";
    } else if (recent.length === 0 && state === "parked") {
      cap = "0 frames on the demo host, whose feed ticks every 400 ms — this is the engine not moving, not the market";
    } else {
      cap = distinct + " distinct frame" + (distinct === 1 ? "" : "s") + " spread over " + spread +
            " s of the last 20 · " + frames.length + " in 60 s";
    }
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    wrap.appendChild(el("span", "cap", cap));
    container.appendChild(wrap);
  }
```

- [ ] **Step 4b: Minimal implementation — `web/ops.js`, the hosts, the peer column and the loop**

The same file continues, with no break, from the closing brace of `arrivalStrip`. First the state one column keeps between polls and the renderer for one host; the second block below it holds the peer's three sentences, the stream this page opens, and the loop, and closes the IIFE.

```javascript

  // ---- one host's column -------------------------------------------------

  // Everything a column knows about its host between polls. `deltas` is the
  // last WINDOW polls as {nodes, frames, failed}; `first` is the earliest ops
  // object still trusted, which is what "book moving" divides by. A failed
  // poll leaves `last` alone, so the next good delta spans two intervals and
  // the bar beside the --over bar is taller than its neighbours: that is the
  // honest shape, not a smoothing problem.
  function hostState() {
    return { deltas: [], last: null, first: null, firstAt: 0, lastAt: 0 };
  }

  function recordPoll(st, ops, now) {
    if (ops === null) {
      st.deltas.push({ nodes: 0, frames: 0, failed: true });
    } else {
      if (st.last !== null) {
        st.deltas.push({
          nodes: Math.max(0, ops.process.nodes_recomputed - st.last.process.nodes_recomputed),
          frames: Math.max(0, ops.stream.frames_sent - st.last.stream.frames_sent),
          failed: false
        });
      }
      // First sight of this host, or a restart: uptime went backwards, so the
      // counters did too, and a rate that straddled the restart would be a lie.
      if (st.first === null || (st.last !== null && ops.uptime_s < st.last.uptime_s)) {
        st.first = ops; st.firstAt = now;
      }
      st.last = ops; st.lastAt = now;
    }
    while (st.deltas.length > WINDOW) st.deltas.shift();
  }

  // The column, redrawn whole on every poll: ~60 rows every ten seconds costs
  // nothing measurable, and it keeps this a function of (ops, health, state)
  // with no DOM to reconcile. `browser` is the EventSource this page holds --
  // passed for this host, null for the peer, whose stream carries no CORS
  // header on purpose and is therefore not this page's to read.
  function renderHost(rows, ops, health, st, now, browser) {
    rows.textContent = "";
    var b = ops.build, p = ops.process, s = ops.stream, h = ops.history, f = ops.feed;
    var fs = ops.feed_source, a = ops.alerts, gc = ops.gc, r = ops.reports;

    group(rows, "engine", [
      ["mode", ops.mode, ops.mode === "live" ? "live" : ""],
      ["up", duration(ops.uptime_s)],
      ["started", stamp(ops.started_at)],
      ["pid", ops.pid],
      ["container id", ops.hostname, "", "not the droplet"],
      ["OCaml", ops.ocaml_version],
      ["build", b.git_short, b.git_sha === "unknown" ? "unknown" : "", b.profile],
      ["built", stamp(b.built_at), b.built_at === "unknown" ? "unknown" : ""],
      ["platform", b.architecture + " " + b.system]
    ]);
    pulse(rows, "Δ nodes_recomputed per poll", st.deltas, "nodes");
    pulse(rows, "Δ frames_sent per poll", st.deltas, "frames");

    group(rows, "feed", [
      ["source", fs.kind],
      ["threshold", f.staleness_threshold_s + " s"],
      ["symbols", f.symbols],
      ["healthy", f.healthy ? "yes" : "no", f.healthy ? "" : "over"],
      ["stale", f.stale, f.stale > 0 ? "over" : ""],
      ["never seen", f.never_seen, f.never_seen > 0 ? "unknown" : ""],
      ["quiet by design", f.quiet, "", "counted, not named"]
    ]);
    if (health) {
      ageStrip(rows, health, f.staleness_threshold_s, now);
    } else {
      var miss = el("div", "strip");
      miss.appendChild(el("span", "cap unknown", "feed ages not drawn — /api/health did not answer"));
      rows.appendChild(miss);
    }

    // The two live-only groups. On the demo host both are null and neither is
    // drawn; a row of em dashes under "alpaca" on a synthetic feed would say
    // the feed had been asked and had nothing, which is not what happened.
    if (fs.alpaca) group(rows, "alpaca", [
      ["feed", fs.alpaca_feed],
      ["frames", int(fs.alpaca.frames)],
      ["trades", int(fs.alpaca.trades)],
      ["rejected", int(fs.alpaca.rejected), fs.alpaca.rejected > 0 ? "over" : ""],
      ["unknown symbol", int(fs.alpaca.unknown_symbol)],
      ["reconnects", int(fs.alpaca.reconnects), fs.alpaca.reconnects > 0 ? "over" : ""],
      ["last error", fs.alpaca.last_error, fs.alpaca.last_error ? "over" : ""]
    ]);
    if (fs.fred) group(rows, "fred", [
      ["series", fs.fred_series],
      ["polls", int(fs.fred.polls)],
      ["successes", int(fs.fred.successes)],
      ["observations", int(fs.fred.observations)],
      ["consecutive failures", int(fs.fred.consecutive_failures), fs.fred.consecutive_failures > 0 ? "over" : ""],
      ["last success", stamp(fs.fred.last_success)],
      ["last error", fs.fred.last_error, fs.fred.last_error ? "over" : ""]
    ]);

    var st8 = browser ? browser.state : null;
    group(rows, "stream", [
      ["state", browser ? st8 : "not opened",
        browser ? (st8 === "open" ? "live" : st8 === "reconnecting" ? "over" : "") : "unknown",
        browser ? "this browser's reading" : "cross-origin; the stream carries no CORS header on purpose"],
      ["frames in 60 s", browser ? browser.frames.length : null],
      ["last frame", browser ? age(browser.lastFrameAt === null ? null : now - browser.lastFrameAt) : null,
        "", "", browser ? "s-lastframe" : ""],
      ["frames_sent", int(s.frames_sent), "", "delivered to ≥ 1 subscriber; welcome frames excluded"],
      ["subscribers", s.subscribers, "", "open pipes only"],
      ["coalesce", s.coalesce_ms + " ms"],
      ["keepalive", s.keepalive_s + " s"]
    ]);
    if (browser) arrivalStrip(rows, browser.frames, now, st8, ops.mode);

    // history.appended per minute is the one rate a fork cannot inflate: the
    // buffer is filled by an observer on the served graph and nothing else.
    // It needs two polls to exist and says so until it has them.
    var mins = st.first !== null && st.last !== st.first ? (st.lastAt - st.firstAt) / 60000 : 0;
    var perMin = mins > 0 ? ((h.appended - st.first.history.appended) / mins).toFixed(1) : null;
    group(rows, "book moving", [
      ["appended / min", perMin, perMin === null ? "unknown" : "",
        perMin === null ? "needs two polls" : "over " + mins.toFixed(1) + " min"],
      ["appended", int(h.appended)],
      ["points", int(h.points) + " of " + int(h.capacity), "", "lost on restart"]
    ]);

    var named = ops.graph && ops.graph.named ? ops.graph.named : null;
    group(rows, "process", [
      ["nodes_recomputed", int(p.nodes_recomputed), "", "whole process: the startup probe and every fork included"],
      ["stabilizes", int(p.stabilizes)],
      ["nodes_created", int(p.nodes_created), "", "cumulative; not this graph's size"],
      ["var_sets", int(p.var_sets)],
      ["active observers", int(p.active_observers)],
      ["named nodes", named ? named.distinct + " distinct, " + int(named.total) + " runs" : "not in this build",
        named ? "" : "unknown", "the recompute log, which forks never reach"]
    ]);

    group(rows, "alerts", [
      ["enabled", a.enabled ? "yes" : "no"],
      ["kill switch", a.kill_switch, a.kill_switch === "tripped" ? "over" : ""],
      ["tripped by", a.tripped_by],
      ["tripped at", stamp(a.tripped_at)],
      ["halt new orders", a.halt_new_orders ? "yes" : "no", a.halt_new_orders ? "over" : ""],
      ["firing", a.firing.length ? a.firing.join(", ") : "none", a.firing.length ? "over" : "",
        "hysteresis: over the line, or not yet back under clear_below"],
      ["sent / failed", int(a.sent) + " / " + int(a.failed), a.failed > 0 ? "over" : ""],
      ["sinks", a.sinks.length ? a.sinks.join(", ") : "none"],
      ["trips on", a.trips_on.length ? a.trips_on.join(", ") : "none"],
      ["clear below", a.clear_below]
    ]);

    group(rows, "resources", [
      ["heap", mb(gc.heap_words), "", "words × 8"],
      ["top heap", mb(gc.top_heap_words)],
      ["minor / major", int(gc.minor_collections) + " / " + int(gc.major_collections), "", "collections"],
      ["compactions", int(gc.compactions)],
      ["RSS", isNum(ops.rss_bytes) ? (ops.rss_bytes / 1e6).toFixed(1) + " MB" : "not on this platform",
        isNum(ops.rss_bytes) ? "" : "unknown", "/proc/self/statm"]
    ]);

    group(rows, "reports", [
      ["static", r.static, r.static === "absent" ? "unknown" : ""],
      ["garch", r.garch, r.garch === "absent" ? "unknown" : ""]
    ]);
  }

  function renderHeader(ops) {
    document.getElementById("h-mode").textContent = ops.mode;
    document.getElementById("h-build").textContent = ops.build.git_short;
    document.getElementById("h-up").textContent = duration(ops.uptime_s) || "—";
  }
```

The file goes on, still inside the IIFE:

```javascript

  // ---- asking ---------------------------------------------------------------

  // One JSON read with a deadline. A failure is a null -- never a throw the
  // loop would have to catch twice, and never a stand-in object: a column that
  // cannot be read draws one --over bar and a sentence, not a zero.
  function getJson(url, ms) {
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
    return fetch(url, { cache: "no-store", signal: ctl ? ctl.signal : undefined })
      .then(function (res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
      .catch(function () { return null; })
      .then(function (v) { if (timer) clearTimeout(timer); return v; });
  }

  var thisState = hostState(), peerState = hostState();
  var thisRows = document.getElementById("this-rows");
  var peerRows = document.getElementById("peer-rows");
  var lastPollAt = null;
  document.getElementById("this-origin").textContent = location.origin;

  // ?peer= is this browser's override -- the one input to this page that is
  // not a served field. It is how the unreachable branch is tested and how the
  // owner points a laptop at any origin, and it reaches the DOM through
  // textContent only, so an attacker-supplied origin can put nothing on the
  // page but its own JSON's numbers.
  var override = new URLSearchParams(location.search).get("peer");

  // ---- the peer column: three fixed sentences, or a host ---------------------
  function renderPeer(ops, origin, peerOps, peerHealth, now) {
    document.getElementById("peer-origin").textContent = origin || "";
    peerRows.textContent = "";
    if (!origin) {
      if (ops === null) {
        note(peerRows, "unknown", "this host did not answer, and the peer is whatever this host says it is");
      } else if (ops.mode === "demo") {
        note(peerRows, "gated", "the live host is behind a password — open live.ohcamel…/ops to see both");
      } else {
        note(peerRows, "no peer configured", "OHCAMEL_PEER_ORIGIN is not set on this host");
      }
      return;
    }
    if (peerOps === null) {
      // Never "down": a 401, a CORS refusal, a wrong origin and a dead host
      // all read the same from a browser, and this page does not guess.
      recordPoll(peerState, null, now);
      note(peerRows, "unreachable from this browser",
           "this browser could not read " + origin + "/api/ops; from here a 401, a CORS refusal and a dead host are the same fact");
      pulse(peerRows, "Δ nodes_recomputed per poll", peerState.deltas, "nodes");
      return;
    }
    recordPoll(peerState, peerOps, now);
    renderHost(peerRows, peerOps, peerHealth, peerState, now, null);
  }

  // ---- the stream, this host only -------------------------------------------
  var browser = { state: "reconnecting", frames: [], lastFrameAt: null, es: null };
  function openStream() {
    var es = new EventSource("/api/stream");
    browser.es = es;
    es.onmessage = function (e) {
      var now = Date.now();
      browser.frames.push({ t: now, data: e.data });
      browser.lastFrameAt = now;
      while (browser.frames.length && now - browser.frames[0].t > ARRIVAL_MS) browser.frames.shift();
    };
    // The browser reconnects on its own after a dropped connection. readyState
    // 2 means it gave up, which happens on a non-200 and nowhere else; then it
    // is reopened here after one poll interval, so a proxy restart is survived
    // without a reload.
    es.onerror = function () { if (es.readyState === 2) setTimeout(openStream, POLL_MS); };
  }
  // open: the socket is up and a frame arrived inside the strip's 60 s.
  // parked: the socket is up and nothing has changed for longer than that --
  // the live host at night, and what separates it from dead is that /api/ops
  // still answers. reconnecting: the socket is not up.
  function streamState(now) {
    if (!browser.es || browser.es.readyState !== 1) return "reconnecting";
    return browser.lastFrameAt !== null && now - browser.lastFrameAt <= ARRIVAL_MS ? "open" : "parked";
  }

  // ---- the loop ---------------------------------------------------------------
  // This host first, because the peer's origin is what this host says it is;
  // then health and the peer's two routes together. Promise.all takes the
  // plain values as they are, so an absent peer costs no request.
  function poll() {
    getJson("/api/ops", 8000).then(function (ops) {
      var origin = override || (ops && ops.peer) || null;
      return Promise.all([
        ops, origin,
        getJson("/api/health", 8000),
        origin ? getJson(origin + "/api/ops", 8000) : null,
        origin ? getJson(origin + "/api/health", 8000) : null
      ]);
    }).then(function (r) {
      var ops = r[0], origin = r[1], health = r[2], peerOps = r[3], peerHealth = r[4];
      var now = Date.now();
      lastPollAt = now;
      recordPoll(thisState, ops, now);
      if (ops === null) {
        thisRows.textContent = "";
        note(thisRows, "unreachable", "/api/ops did not answer from its own origin; the strip keeps the poll that failed");
        pulse(thisRows, "Δ nodes_recomputed per poll", thisState.deltas, "nodes");
      } else {
        browser.state = streamState(now);
        renderHeader(ops);
        renderHost(thisRows, ops, health, thisState, now, browser);
      }
      renderPeer(ops, origin, peerOps, peerHealth, now);
    });
  }

  var timer = null;
  function start() { if (timer === null) { poll(); timer = setInterval(poll, POLL_MS); } }
  function stop() { if (timer !== null) { clearInterval(timer); timer = null; } }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") start(); else stop();
  });

  // The two ages are re-rendered as text once a second with no transition:
  // the header's "last poll" and this host's "last frame".
  setInterval(function () {
    var now = Date.now();
    document.getElementById("h-poll").textContent = lastPollAt === null ? "—" : age(now - lastPollAt) + " ago";
    var lf = document.getElementById("s-lastframe");
    if (lf) lf.textContent = browser.lastFrameAt === null ? "—" : age(now - browser.lastFrameAt);
  }, 1000);

  openStream();
  if (document.visibilityState !== "hidden") start();
})();
```

- [ ] **Step 5: Run the tests and see them pass**

Three checks: the embedded-assets test, a `curl` against a local demo, and a headless-Chrome DOM check (Chrome is at `/Applications/Google Chrome.app`; no Node, no Playwright). The demo on `:8099` is started once and used by all three, with a second port left deliberately empty for the unreachable branch.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && dune runtest --force 2>&1 | grep -E "ops page|embedded" | head -5
(./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4
curl -sS http://localhost:8099/ops | grep -c 'id="this-host"\|id="peer"\|id="cannot"\|new EventSource("/api/stream")'
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' http://localhost:8099/ops
```

Expected: `[OK]` on `the ops page is assembled in the rule's order`; `4` (the four needles, one per line of the page); `200 text/html; charset=utf-8`.

Then the DOM as a browser draws it. `--virtual-time-budget` runs the page's timers fast, so the first poll and its render have happened when the DOM is dumped:

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --no-first-run --virtual-time-budget=15000 --dump-dom "http://localhost:8099/ops" 2>/dev/null > /tmp/ops-dom.html
grep -o '<span class="lbl">[a-zA-Z ]*</span>' /tmp/ops-dom.html | sed 's/<[^>]*>//g' | tr '\n' ' '; echo
grep -o '<div class="note"><b>[^<]*</b>[^<]*' /tmp/ops-dom.html | sed 's/<[^>]*>//g'
grep -c '<rect ' /tmp/ops-dom.html
"$CHROME" --headless=new --disable-gpu --no-first-run --virtual-time-budget=15000 --dump-dom "http://localhost:8099/ops?peer=http://localhost:8098" 2>/dev/null | grep -o '<div class="note"><b>[^<]*</b>' | sed 's/<[^>]*>//g'
pkill -f "main.exe demo 8099"
```

Expected, line by line: the group labels in the Produces order for a demo host with no live-only groups — `this host peer what this page cannot see engine feed stream book moving process alerts resources reports ` (the three section labels first, then the eight groups; `alpaca` and `fred` absent because `feed_source.alpaca` and `.fred` are null on a synthetic feed); the peer column's sentence `gated — the live host is behind a password — open live.ohcamel…/ops to see both`; a `<rect` count of at least `2` (the arrival strip's shade plus at least one pulse bar — on the first poll the pulse strips have no delta yet, so a count of exactly `1` means the demo's stream delivered no frame inside the virtual budget, and a second run with `--virtual-time-budget=30000` should show more); and, against the empty port, `unreachable from this browser`. If the label line is empty, the page's first poll had not run when the DOM was dumped: check `document.visibilityState` is not `hidden` under this Chrome by adding `--window-size=1200,900`, and re-run.

- [ ] **Step 6: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add web/ops.html web/ops.js test/test_embedded_assets.ml
git commit -m "web: the ops page draws both hosts and the difference between polls, because a counter nobody diffs cannot alarm"
```

---

### Task 15: `deploy/smoke.sh` learns which build answered — `--expect-sha`, the ops page, the 404 list, the live host's five 401s; `deploy/deploy.sh` hands it the sha

**Files:**
- Modify: `deploy/smoke.sh:6–7` (usage comment), `:20` (one variable and the routes shadow after `SSE_WINDOW`), `:33` (one parser arm), one block inserted between `:177` (the stream probe's closing `fi`) and `:179` (the `# ----` rule above `# 5. TLS`), and `:218–220` (block 6's single 401 check becomes five)
- Modify: `deploy/deploy.sh:103` (the `SMOKE_ARGS=` line under `say "Verifying"` — `:110` once Task 3's eight-line build edit is in)
- Test: the suite itself — against a local demo built bare (the sha assertion must FAIL and say why), against the same demo built by `make build` (it must pass), against a port with nothing on it, and with Phase 6's exact argument order

**Interfaces:**
- Consumes: `GET /api/ops` as Task 11 keys it (`mode`, `uptime_s`, `build.git_sha`); `GET /ops` from Task 12's table, whose body carries `id="this-host"` and `id="peer"` (Task 14); `Server.not_found_body` = `{error: "not found", routes: [path…], purposes: {…}}` with `routes` in the table's order — `/`, `/ops`, `/api/snapshot`, `/api/health`, `/api/stream`, `/api/history`, `/api/stress`, `/api/ops` (Task 12); the suite's own `ok` / `no` / `meh`, `$BASE`, `$LIVE` and its python3-or-skip pattern from assertion 3; `deploy.sh`'s `$REPO` and its per-command-prefix rule (Task 3); the `make build` stamp (Task 2), which is what makes a local sha match.
- Produces: the flag `--expect-sha <sha>`, accepted anywhere after the base URL and in either order with `--live` — Phase 6 invokes exactly `deploy/smoke.sh <base> --live <url> --expect-sha <sha>` and `deploy.sh` invokes `deploy/smoke.sh <base> --expect-sha <sha> [--live <url>]`; `EXPECTED_ROUTES`, one space-separated string near the top of `smoke.sh`, the suite's shadow of `Server.routes`, which Phase 5's Task 7 must extend with `/api/reports /api/reports/garch` after `/api/stress` in the same commit that adds the routes; the assertion lines Phase 6 quotes into `docs/status.md`: `GET /api/ops                mode demo, build <short>, up <N> s` · `build.git_sha               matches <short>` · `uptime_s                    <N> s, under 300: this deploy's container` · `GET /ops                    200, both host columns present` · `GET /api/nope               404, lists the 8 routes in the table's order` · `GET <LIVE><path>  401 without credentials` for each of `/`, `/ops`, `/api/ops`, `/api/snapshot`, `/api/health`; and the marker comment that closes the new block, `# -- Phase 5's block 4b (the routes the page reads) is inserted immediately below this line --`, which is the anchor Phase 5's Task 15 uses instead of the `# 5. TLS` banner.

**Why these and not more.** The suite had nine assertions and could pass against yesterday's container: `up -d` is a no-op when the image did not change, and a build that failed left the old image serving, so `9 passed` said nothing about whether the commit just pushed was the one answering. The sha closes that, and uptime closes the hole the sha leaves — a matching sha on a container up for a week means the deploy replaced nothing. The 404 list is asserted as equality in the table's order, not membership, because a route that is served and not listed is the exact drift Task 12's table exists to make impossible. The five 401s replace one because the tempting way to fill the ops page's peer column from the demo origin is a Caddy matcher that exempts `/api/ops` from `basic_auth`, and that hole would show up here as a 200 on one path while `/` still says 401; the page fills its peer column the other way round (Task 14), so the live host never needs one.

- [ ] **Step 1: Write the failing test**

The suite is its own test; the failing case is the suite as it stands refusing the flag before it makes a single request. Record the baseline:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
grep -c 'expect-sha\|EXPECTED_ROUTES\|GET /ops\|api/nope' deploy/smoke.sh
deploy/smoke.sh http://localhost:8099 --live http://localhost:8097 --expect-sha "$(git rev-parse HEAD)"; echo "exit $?"
```

- [ ] **Step 2: Run it and see it fail**

Expected from Step 1: `0`, then `smoke: unknown argument --expect-sha` and `exit 2` — Phase 6's invocation, rejected by the parser.

- [ ] **Step 3: Minimal implementation**

Five edits, all in the file's own idiom (tabs, `ok`/`no`/`meh`, a word first from python and the detail after).

**(a) The usage comment.** After `deploy/smoke.sh:7`, which reads

```bash
#   ./smoke.sh https://ohcamel.example.com --live https://live.ohcamel.example.com
```

add:

```bash
#   ./smoke.sh https://ohcamel.example.com --live https://live.ohcamel.example.com \
#              --expect-sha "$(git rev-parse HEAD)"      # what deploy.sh runs
```

**(b) One variable and the routes shadow.** `deploy/smoke.sh:18–20` read

```bash
BASE="http://localhost:8000"
LIVE=""
SSE_WINDOW=20
```

Replace with:

```bash
BASE="http://localhost:8000"
LIVE=""
SSE_WINDOW=20
EXPECT_SHA=""

# The routes table, as this suite knows it. lib/server.ml's `routes` is the
# one table the dispatcher and the 404 body are both generated from, and this
# string is its shadow: a shell script cannot read OCaml, so it carries the
# list and asserts the 404 body equals it, in order. Adding a route means
# adding it here in the same commit -- the assertion fails until you do,
# which is the point of having it.
EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/ops"
```

**(c) The parser arm.** `deploy/smoke.sh:31–33` read

```bash
	--live) LIVE="${2:-}"; shift 2 ;;
	--sse-window) SSE_WINDOW="${2:-20}"; shift 2 ;;
	*) echo "smoke: unknown argument $1" >&2; exit 2 ;;
```

Replace with:

```bash
	--live) LIVE="${2:-}"; shift 2 ;;
	--sse-window) SSE_WINDOW="${2:-20}"; shift 2 ;;
	--expect-sha) EXPECT_SHA="${2:-}"; shift 2 ;;
	*) echo "smoke: unknown argument $1" >&2; exit 2 ;;
```

**(d) The new block.** The stream probe ends at `deploy/smoke.sh:175–177` with

```bash
else
	ok "SSE /api/stream            $distinct distinct frames spread over ${spread}s"
fi
```

and `:179–180` are the `# ----` rule and `# 5. TLS, and the redirect onto it (production only)`. Insert between them, ending with the marker comment and one blank line:

```bash

# ---------------------------------------------------------------------------
# 4a. Which build this is, the page that says so, and the 404 that lists it
#
# Before this block the suite could pass against yesterday's container: `up -d`
# is a no-op when the image did not change, and a build that failed left the
# old image serving, so "9 passed" said nothing about whether the commit just
# pushed was the one answering. /api/ops carries the sha dune was given at
# build time, deploy.sh hands this suite the sha it built from, and the two
# must agree; and uptime must be under five minutes, because a matching sha
# on a container that has been up for a week means the deploy replaced
# nothing. Neither check needs the page; both are what the page would show a
# human, made mechanical.
# ---------------------------------------------------------------------------
ops_sha=""; ops_up=""
if command -v python3 >/dev/null 2>&1; then
	ops=$(curl -sS --max-time 15 "$BASE/api/ops" 2>/dev/null | python3 -c '
import json, sys
try:
    o = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
mode = o.get("mode"); up = o.get("uptime_s"); b = o.get("build") or {}
if mode not in ("demo", "live"):
    print("NOMODE mode=%r" % (mode,)); raise SystemExit
if not isinstance(up, (int, float)):
    print("NOUPTIME uptime_s=%r" % (up,)); raise SystemExit
if not isinstance(b.get("git_sha"), str) or not b.get("git_sha"):
    print("NOSHA build.git_sha=%r" % (b.get("git_sha"),)); raise SystemExit
print("OK %s %s %d" % (mode, b["git_sha"], up))
' 2>/dev/null)
	case "$ops" in
	OK*)
		read -r _ ops_mode ops_sha ops_up <<<"$ops"
		ok "GET /api/ops                mode $ops_mode, build ${ops_sha:0:7}, up ${ops_up} s"
		;;
	*) no "GET /api/ops                malformed" "${ops:-no response}" ;;
	esac

	if [ -n "$EXPECT_SHA" ]; then
		if [ -n "$ops_sha" ] && [ "$ops_sha" = "$EXPECT_SHA" ]; then
			ok "build.git_sha               matches ${EXPECT_SHA:0:7}"
		else
			no "build.git_sha               ${ops_sha:-unreadable}, expected ${EXPECT_SHA:0:7}" \
				"the image answering was not built from this checkout: the build failed, or up -d kept the old image"
		fi
		if [ -n "$ops_up" ] && [ "$ops_up" -lt 300 ]; then
			ok "uptime_s                    ${ops_up} s, under 300: this deploy's container"
		else
			no "uptime_s                    ${ops_up:-unreadable} s, expected under 300" \
				"a container this old was not replaced by this deploy"
		fi
	else
		meh "build sha                  not given (--expect-sha SHA), skipping"
	fi
else
	meh "GET /api/ops                python3 unavailable for a real parse; build sha and uptime not checked"
fi
```

The block continues — the page, then the 404, then the marker that closes it:

```bash
# The page exists and is the page: both host columns are in the body, which
# is the DOM contract ops.js fills. No python needed; the two ids are literal.
page=$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/ops" 2>/dev/null)
page_code="${page##*$'\n'}"
case "$page_code:$page" in
200:*'id="this-host"'*'id="peer"'*) ok "GET /ops                    200, both host columns present" ;;
200:*) no "GET /ops                    200, but not the ops page" "the body has no #this-host / #peer" ;;
*)     no "GET /ops                    ${page_code:-no response}" ;;
esac

# The 404 body is generated from the same table `handle` dispatches on, and
# EXPECTED_ROUTES is that table's shadow. Equality in order, not membership: a
# route that is served and not listed is the exact drift the table was
# introduced to make impossible, and a suite that only asked "is /api/ops in
# there" would wave it through.
if command -v python3 >/dev/null 2>&1; then
	listed=$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/api/nope" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
try:
    body = json.loads(raw[0]); code = raw[1]
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if code != "404":
    print("CODE %s, expected 404" % code); raise SystemExit
if body.get("error") != "not found":
    print("ERROR error=%r" % (body.get("error"),)); raise SystemExit
print("OK " + " ".join(body.get("routes") or []))
' 2>/dev/null)
	case "$listed" in
	OK*)
		if [ "${listed#OK }" = "$EXPECTED_ROUTES" ]; then
			ok "GET /api/nope               404, lists the $(echo "$EXPECTED_ROUTES" | wc -w | tr -d ' ') routes in the table's order"
		else
			no "GET /api/nope               404, but its routes are not EXPECTED_ROUTES" "served:   ${listed#OK }"
			printf '        %s\n' "expected: $EXPECTED_ROUTES"
		fi
		;;
	*) no "GET /api/nope               malformed" "${listed:-no response}" ;;
	esac
else
	meh "GET /api/nope               python3 unavailable for a real parse; the 404 route list not checked"
fi
# -- Phase 5's block 4b (the routes the page reads) is inserted immediately below this line --

```

`set -u` is on: `ops_sha` and `ops_up` are initialised before the python branch so the `--expect-sha` branch can read them when `/api/ops` was malformed, and then fails with `unreadable` rather than aborting the suite. `read -r _ ops_mode ops_sha ops_up` splits the python line on spaces; a sha has none.

**(e) Block 6, one path becomes five.** `deploy/smoke.sh:217–223` read

```bash
if [ -n "$LIVE" ]; then
	code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$LIVE/" 2>/dev/null)
	[ "$code" = "401" ] && ok "GET $LIVE/  401 without credentials" \
		|| no "GET $LIVE/  $code, expected 401" "the live host is not gated"
else
	meh "live host                  not given (--live URL), skipping"
fi
```

Replace the three lines between `if` and `else` with:

```bash
	# Five paths, not one. The gate is Caddy's basic_auth on the whole host,
	# and the tempting way to fill the ops page's peer column from the public
	# origin is a matcher that exempts /api/ops from it. That hole would show
	# up here as a 200 on one path while / still said 401. The page fills its
	# peer column the other way round -- the live origin reads the demo, over
	# the demo engine's own CORS header -- so the live host never needs one.
	for path in / /ops /api/ops /api/snapshot /api/health; do
		code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$LIVE$path" 2>/dev/null)
		[ "$code" = "401" ] && ok "GET $LIVE$path  401 without credentials" \
			|| no "GET $LIVE$path  $code, expected 401" "the live host is not gated on $path"
	done
```

**(f) `deploy/deploy.sh` hands over the sha.** Under `say "Verifying"`, the two lines (`:103–104` today, `:110–111` after Task 3)

```bash
SMOKE_ARGS=("https://${OHCAMEL_DEMO_HOST}")
[ ${#PROFILE[@]} -gt 0 ] && SMOKE_ARGS+=(--live "https://${OHCAMEL_LIVE_HOST}")
```

become:

```bash
# The sha this script just built from, read from the checkout and not from
# the container: the assertion is that the two agree, and one side of an
# agreement has to come from somewhere the other side cannot reach.
SMOKE_ARGS=("https://${OHCAMEL_DEMO_HOST}" --expect-sha "$(git -C "$REPO" rev-parse HEAD)")
[ ${#PROFILE[@]} -gt 0 ] && SMOKE_ARGS+=(--live "https://${OHCAMEL_LIVE_HOST}")
```

The order `--expect-sha` then `--live` differs from Phase 6's hand-typed `--live … --expect-sha`; the parser is a `case` in a loop and takes either.

- [ ] **Step 4: Run the tests and see them pass**

Four runs. The first is deliberately against a binary built with a bare `dune build`, whose stamp reads `unknown`: the sha assertion must fail and name the cause, or it is not load-bearing.

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
bash -n deploy/smoke.sh && bash -n deploy/deploy.sh && echo SYNTAX-OK
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | head -3
(./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4
deploy/smoke.sh http://localhost:8099 --expect-sha "$(git rev-parse HEAD)" | grep -E 'api/ops|git_sha|uptime_s|GET /ops|api/nope|passed'; pkill -f "main.exe demo 8099"
```

Expected: `SYNTAX-OK`, no build output, then

```
  PASS  GET /api/ops                mode demo, build unknown, up 4 s
  FAIL  build.git_sha               unknown, expected <7 chars of HEAD>
        the image answering was not built from this checkout: the build failed, or up -d kept the old image
  PASS  uptime_s                    4 s, under 300: this deploy's container
  PASS  GET /ops                    200, both host columns present
  PASS  GET /api/nope               404, lists the 8 routes in the table's order

  8 passed, 1 failed, 2 skipped
```

Now the stamped binary (Task 2's `make build` sets `OHCAMEL_GIT_SHA` to `HEAD`; dune tracks the variable and regenerates `build_info.ml`):

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel && make build 2>&1 | tail -1
(./_build/default/bin/main.exe demo 8099 >/tmp/ohcamel-demo.log 2>&1 &) && /bin/sleep 4
deploy/smoke.sh http://localhost:8099 --expect-sha "$(git rev-parse HEAD)" | tail -14; pkill -f "main.exe demo 8099"
```

Expected: the four original `PASS` lines, then `PASS GET /api/ops … build <7 chars>, up 4 s`, `PASS build.git_sha matches <7 chars>`, `PASS uptime_s 4 s, under 300`, `PASS GET /ops`, `PASS GET /api/nope … 8 routes`, `SKIP TLS, redirect, port exposure`, `SKIP live host not given`, and `9 passed, 0 failed, 2 skipped`. Without `--expect-sha` the same run reads `7 passed, 0 failed, 3 skipped`, the third skip being `build sha not given (--expect-sha SHA), skipping`.

Then nothing on the port, and Phase 6's exact argument order — which must parse, then fail on every request rather than on the parser:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
deploy/smoke.sh http://localhost:8098 --live http://localhost:8097 --expect-sha deadbeef 2>&1 | grep -c 'unknown argument'
deploy/smoke.sh http://localhost:8098 --live http://localhost:8097 --expect-sha deadbeef >/dev/null 2>&1; echo "exit $?"
deploy/smoke.sh http://localhost:8098 --live http://localhost:8097 --expect-sha deadbeef 2>&1 | grep -E 'git_sha|uptime_s|api/nope|8097' | head -8
```

Expected: `0`; `exit 1`; then `FAIL build.git_sha unreadable, expected deadbee`, `FAIL uptime_s unreadable s, expected under 300`, `FAIL GET /api/nope malformed` / `no response`, and five `FAIL GET http://localhost:8097<path>  000, expected 401` lines — curl's `000` for a refused connection, one per path.

The five 401s cannot pass locally (`deploy/Caddyfile.local` has no `basic_auth`, on purpose); Task 17's production run is where they first go green.

- [ ] **Step 5: Commit**

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git add deploy/smoke.sh deploy/deploy.sh
git commit -m "deploy: the smoke suite checks which build answered, because up -d had been passing yesterday's container"
```

---

### Task 16: the byte-identical stdout gate — the six credential-free modes, before Task 8 and at HEAD

**Files:**
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase2-gate/gate.sh` (Phase 3's Task 1 script, byte for byte)
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase2-gate/capture.sh` (Phase 3's Task 1 script, byte for byte) and `capture-before.sh` (the same, pointed at the worktree — a two-line `sed`, with the diff shown)
- Create: `/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/gate-before/` (a git worktree at the commit before Task 8's; removed at the end)
- Modify: nothing in the repository
- Test: `gate.sh` itself — six `GATE ok` lines

**Interfaces:**
- Consumes: the six credential-free modes `synthetic`, `stress`, `backtest`, `backtest-crisis`, `options`, `garch` (`bin/main.ml`'s usage text; Makefile targets); Task 8's commit, found by its message `server: the process learns which host it is and when it started, because nothing on the wire could say`; the local switch at `/Users/ajaiupadhyaya/Documents/OhCamel/_opam`, which the worktree has no copy of; Phase 3's Task 1 `capture.sh` and `gate.sh`, reused verbatim.
- Produces: nothing in the repository. Six baseline captures under `phase2-gate/` that Phase 3 does not use — Phase 3 captures its own baseline, once, at its own start, in `phase3-baseline/`, which is why this task keeps a separate directory rather than pre-filling that one.

**Why this task exists.** Tasks 8 and 13 touched `bin/main.ml` — the `Server.create` call sites and the two servers' `~mode`/`~peer`/`~feed_stats`/`~quiet` wiring — and the Global Constraints say the six modes' stdout is gated wherever that file is touched. The gate is mechanical: the same binary, six modes, `diff -u` against a capture taken from the last commit before the file changed. That commit is Task 7's, and the capture is taken from a worktree at it rather than from a stash or a checkout, so HEAD's `_build` is never disturbed and nothing in the working tree moves.

- [ ] **Step 1: Write the failing test**

The test is the gate. If Phase 3 has already been executed, its scripts exist and are copied; otherwise they are created here with the exact contents of Phase 3's Task 1. Either way the two files are identical to Phase 3's — `gate.sh` is used untouched, and the only derivative is the capture pointed at the worktree in Step 3.

```bash
SCRATCH=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad
mkdir -p "$SCRATCH/phase2-gate"
if [ -f "$SCRATCH/phase3-baseline/gate.sh" ] && [ -f "$SCRATCH/phase3-baseline/capture.sh" ]; then
  cp "$SCRATCH/phase3-baseline/gate.sh" "$SCRATCH/phase3-baseline/capture.sh" "$SCRATCH/phase2-gate/"
  echo "copied Phase 3's scripts"
else
cat > "$SCRATCH/phase2-gate/gate.sh" <<'SH'
#!/usr/bin/env bash
# The byte-identical gate for Phase 3.
#
# The extraction moves the arithmetic behind six printed reports out of
# bin/main.ml. Without this gate that move is churn with a chance of silently
# changing the tables the README quotes; with it, it is the one-implementation
# rule stress.ml already enforces, applied to reports.
#
#   gate.sh                 diff every mode against its baseline
#   gate.sh synthetic garch diff only those modes
set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
REPO="/Users/ajaiupadhyaya/Documents/OhCamel"
MODES=("$@")
if [ ${#MODES[@]} -eq 0 ]; then
  MODES=(synthetic stress backtest backtest-crisis options garch)
fi
cd "$REPO" || exit 2
eval "$(opam env --switch="$REPO" --set-switch)"
dune build bin/main.exe 2>&1 || { echo "GATE: build failed"; exit 2; }
status=0
for mode in "${MODES[@]}"; do
  if [ ! -f "$BASE/$mode.txt" ]; then
    echo "GATE: no baseline for $mode -- run capture.sh first"
    status=2
    continue
  fi
  ./_build/default/bin/main.exe "$mode" > "$BASE/$mode.now.txt" 2>&1
  if diff -u "$BASE/$mode.txt" "$BASE/$mode.now.txt" > "$BASE/$mode.diff"; then
    echo "GATE ok        $mode"
  else
    echo "GATE DIFFERS   $mode  -- see $BASE/$mode.diff"
    status=1
  fi
done
exit $status
SH
cat > "$SCRATCH/phase2-gate/capture.sh" <<'SH'
#!/usr/bin/env bash
# Capture the stdout of the six credential-free modes, ONCE, before the
# extraction begins. Run exactly once. Re-running after a move would re-baseline
# the very thing the gate exists to catch.
set -eu
BASE="$(cd "$(dirname "$0")" && pwd)"
REPO="/Users/ajaiupadhyaya/Documents/OhCamel"
cd "$REPO"
eval "$(opam env --switch="$REPO" --set-switch)"
dune build bin/main.exe
git rev-parse HEAD > "$BASE/BASELINE_SHA"
for mode in synthetic stress backtest backtest-crisis options garch; do
  ./_build/default/bin/main.exe "$mode" > "$BASE/$mode.txt" 2>&1
  echo "captured $mode ($(wc -l < "$BASE/$mode.txt") lines)"
done
shasum -a 256 "$BASE"/*.txt > "$BASE/BASELINE_SHA256"
SH
  echo "created Phase 3's scripts"
fi
chmod +x "$SCRATCH/phase2-gate/gate.sh" "$SCRATCH/phase2-gate/capture.sh"
```

- [ ] **Step 2: Run it and see it fail**

```bash
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase2-gate/gate.sh; echo "exit $?"
```

Expected: HEAD builds, then six lines `GATE: no baseline for synthetic -- run capture.sh first` … `garch`, and `exit 2`. There is nothing to compare against yet, which is the right first failure.

- [ ] **Step 3: Minimal implementation — the worktree at the commit before Task 8, and the capture from it**

`capture.sh` hardcodes `REPO` to the main checkout and activates the switch at `$REPO`; the worktree has no `_opam`, so the derivative changes exactly those two lines — `REPO` to the worktree, the switch to the main checkout — and the `diff` is shown so the change is the whole change.

```bash
SCRATCH=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad
cd /Users/ajaiupadhyaya/Documents/OhCamel
TASK8=$(git log --format=%H --grep='server: the process learns which host it is and when it started' -n 1)
BEFORE=$(git rev-parse "${TASK8}^")
git log --oneline -1 "$BEFORE"
git worktree add "$SCRATCH/gate-before" "$BEFORE"
sed -e "s|^REPO=\"/Users/ajaiupadhyaya/Documents/OhCamel\"|REPO=\"$SCRATCH/gate-before\"|" \
    -e 's|^eval "$(opam env --switch="$REPO" --set-switch)"|eval "$(opam env --switch=/Users/ajaiupadhyaya/Documents/OhCamel --set-switch)"|' \
    "$SCRATCH/phase2-gate/capture.sh" > "$SCRATCH/phase2-gate/capture-before.sh"
chmod +x "$SCRATCH/phase2-gate/capture-before.sh"
diff "$SCRATCH/phase2-gate/capture.sh" "$SCRATCH/phase2-gate/capture-before.sh"
"$SCRATCH/phase2-gate/capture-before.sh"
cat "$SCRATCH/phase2-gate/BASELINE_SHA"
```

Expected: the one-line log of Task 7's commit (`config: the live host can be told where its peer is, …`); `Preparing worktree (detached HEAD <sha>)`; a `diff` of exactly four lines — `7c7` with the two `REPO=` lines and `9c9` with the two `eval` lines, nothing else; then a full build in the worktree (its first, a few minutes) and six `captured <mode> (<N> lines)` lines; and `BASELINE_SHA` equal to `$BEFORE`. `backtest-crisis` reads `docs/crisis/*.csv` from the working directory in this era of the code, which is why the derivative `cd`s into the worktree, where those files are tracked.

- [ ] **Step 4: Run the tests and see them pass**

`gate.sh` is untouched: it builds HEAD in the main checkout and diffs each mode against the capture just taken.

```bash
/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase2-gate/gate.sh; echo "exit $?"
```

Expected, exactly:

```
GATE ok        synthetic
GATE ok        stress
GATE ok        backtest
GATE ok        backtest-crisis
GATE ok        options
GATE ok        garch
exit 0
```

If any line reads `GATE DIFFERS`, stop: read the named `.diff`, and the fix goes into the task whose commit changed the bytes (Tasks 8 or 13 are the only two that touched `bin/main.ml`), not into the baseline. Re-running `capture-before.sh` would re-baseline the thing the gate exists to catch.

- [ ] **Step 5: Remove the worktree; nothing to commit**

```bash
SCRATCH=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad
cd /Users/ajaiupadhyaya/Documents/OhCamel
git worktree remove --force "$SCRATCH/gate-before"
git worktree prune
git worktree list
git status --porcelain
```

Expected: `git worktree list` shows only `/Users/ajaiupadhyaya/Documents/OhCamel`, and `git status --porcelain` prints nothing but `?? claudecodehandoff.md` (a different project's file, ignored throughout). `--force` is needed because the worktree's `_build` is untracked; the captures stay in `phase2-gate/` for the record and are not part of the repository. Do not commit.

---

### Task 17: deploy both hosts, and read the sha back from each of them

**Files:**
- Modify: `docs/status.md:43` (the `Deployed` row) and its `Operating it` block (`:320–340`: the `deploy/smoke.sh …` line gains `--expect-sha`; one `open …/ops` line under `# look`)
- Modify: nothing else — no file under `deploy/`, nothing on the droplet by hand
- Test: `deploy/deploy.sh --live` on the droplet, whose smoke run must end `18 passed, 0 failed, 0 skipped`; then `build.git_sha` read from both origins and equal to the pushed HEAD

**Interfaces:**
- Consumes: `ssh ohcamel` — the deploy user, `~/OhCamel` (the `ohcamel-root` alias exists and is not used here: nothing in this task needs root); `deploy/deploy.sh --live` as Tasks 3 and 15 left it; `deploy/smoke.sh … --expect-sha` (Task 15); `GET /api/ops` on both origins (`mode`, `build.git_sha`, `build.built_at`, `peer`, `feed_source.kind` — Tasks 11 and 13); the live host's gate exactly as `docs/status.md` 'Operating it' describes it: Caddy `basic_auth` on the whole host, the username as `OHCAMEL_LIVE_USER` in `deploy/.env` on the droplet (read with `sed`, the file is never sourced), the password the owner's and typed at a prompt; `GET /ops` on both origins for the eye check Task 14's DOM contract was written for.
- Produces: both hosts serving this phase's HEAD, each saying so on `/api/ops`; `docs/status.md`'s `Deployed` row naming that sha; the commit `docs: both hosts say which commit they are, …`, pushed. Phase 6 starts from here.

**Three rules, restated because this is the task where they bite.** `deploy/.env` is never sourced (`$$` becomes a pid; `deploy.sh:43`). The `caddy_data` volume is never touched — it holds the certificate and the ACME account. The live host's gate is not weakened for the ops page: no Caddy matcher, no exempted path; the page fills its peer column from the live origin outward (Task 14), and Task 15's five 401s are the proof that nothing was opened.

- [ ] **Step 1: Write the failing test**

The route this phase adds does not exist on the deployed build. From the Mac:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
git status --porcelain
git log --oneline origin/main..HEAD | wc -l
curl -sS -o /dev/null -w '%{http_code}\n' https://ohcamel.ajaiupadhyaya.com/api/ops
curl -sS -o /dev/null -w '%{http_code}\n' https://ohcamel.ajaiupadhyaya.com/ops
```

- [ ] **Step 2: Run it and see it fail**

Expected: a clean tree (nothing, or only `?? claudecodehandoff.md`); a positive count — this phase's commits, none of them on the droplet; and `404` twice: the deployed image predates `/api/ops` and `/ops`. (If the first line shows anything else, stop and commit or discard it first — `deploy.sh` deploys what `origin/main` has, and a dirty tree is a sign a task above was not finished.)

- [ ] **Step 3: Minimal implementation — push, then deploy on the droplet**

Pull first on the droplet, then run: `deploy.sh` pulls too, but bash reads a script as it goes, and a script that replaces itself mid-run — this deploy replaces `deploy.sh` — keeps executing the old text. `docs/status.md` says the same. The build is native on the droplet (amd64; several minutes) and the smoke suite runs at the end.

```bash
SCRATCH=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad
cd /Users/ajaiupadhyaya/Documents/OhCamel
git push origin main
HEAD_SHA=$(git rev-parse HEAD); echo "$HEAD_SHA"
ssh ohcamel 'cd ~/OhCamel && git pull --ff-only && deploy/deploy.sh --live' 2>&1 | tee "$SCRATCH/phase2-deploy.txt"
grep -E 'PASS|FAIL|SKIP|passed' "$SCRATCH/phase2-deploy.txt"
```

Expected: `==> Pulling` (fast-forward to `$HEAD_SHA`), `==> Book` (`book.sexp present, leaving it alone`), `==> Building` (minutes), `==> Starting`, `==> Settling`, the `ps` table with `caddy`, `ohcamel-demo` and `ohcamel-live` all `Up`, `==> Verifying`, and eighteen `PASS` lines — the four original, `GET /api/ops … mode demo, build <7 of HEAD_SHA>, up <N> s`, `build.git_sha matches <7 of HEAD_SHA>`, `uptime_s <N> s, under 300`, `GET /ops 200, both host columns present`, `GET /api/nope 404, lists the 8 routes`, the redirect, TLS, the two unreachable ports, and five `GET https://live.ohcamel.ajaiupadhyaya.com<path>  401 without credentials` — then `18 passed, 0 failed, 0 skipped` and `==> Deployed`. If the last line reads `Deployed, but the smoke suite FAILED`, `deploy.sh` has exited 1 and both containers are still up: read the failing line, then `ssh ohcamel 'docker compose -f ~/OhCamel/deploy/docker-compose.yml logs --tail 100 ohcamel-demo'`. A `build.git_sha … unknown` here means the sha never reached dune — Task 3's compose `build.args` or the Dockerfile's `ARG`/`ENV` pair is the place to look, and nothing on the droplet is edited by hand.

- [ ] **Step 4: Run the tests and see them pass — the sha from both origins, and the page by eye**

The public origin first, from the Mac. Three things: the sha, the CORS header Task 9 added to the demo's JSON (the mechanism the live page's peer column depends on), and the live origin refusing the same route anonymously:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
HEAD_SHA=$(git rev-parse HEAD)
curl -sS https://ohcamel.ajaiupadhyaya.com/api/ops | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["mode"], d["build"]["git_sha"], d["build"]["built_at"], d["build"]["profile"], d["peer"], d["feed_source"]["kind"])'
curl -sSI https://ohcamel.ajaiupadhyaya.com/api/ops | grep -i '^access-control-allow-origin'
curl -sSI https://ohcamel.ajaiupadhyaya.com/api/stream --max-time 3 2>/dev/null | grep -ic '^access-control' ; true
curl -sS -o /dev/null -w '%{http_code}\n' https://live.ohcamel.ajaiupadhyaya.com/api/ops
```

Expected: `demo <HEAD_SHA> <YYYY-MM-DDTHH:MM:SSZ> release None synthetic`; `access-control-allow-origin: *`; `0` (the stream carries no CORS header); `401`.

The live origin, with credentials. This is the one step an agent does not run: `curl` asks for the owner's password on the terminal, and the password appears nowhere else — not on a command line, not in shell history, not in this plan. The username is read from `deploy/.env` with `sed`, the way `deploy.sh` reads its hostnames; the file is not sourced. In a terminal:

```bash
ssh ohcamel
# then, on the droplet:
cd ~/OhCamel && curl -sS -u "$(sed -n 's/^OHCAMEL_LIVE_USER=//p' deploy/.env | tail -n1)" https://live.ohcamel.ajaiupadhyaya.com/api/ops | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["mode"], d["build"]["git_sha"], d["build"]["built_at"], d["peer"], d["feed_source"]["kind"], d["feed"]["symbols"], "quiet", d["feed"]["quiet"])'
exit
```

Expected: `Enter host password for user '<user>':`, then `live <HEAD_SHA> <the same built_at as the demo> https://ohcamel.ajaiupadhyaya.com alpaca <N> quiet 0` — the same sha and the same `built_at` on both hosts, because one `docker compose build` produced the one image both run; `peer` set from `OHCAMEL_PEER_ORIGIN` (Task 3); the feed named `alpaca` with a symbol count and no symbol names anywhere in the object (Task 11). If `peer` is `None`, the live service's `environment:` in `deploy/docker-compose.yml` did not carry `OHCAMEL_PEER_ORIGIN` — Task 3 — and the page's peer column will say `no peer configured`, which is the honest reading and not a reason to touch Caddy.

Then the page, by eye, in a browser. `https://ohcamel.ajaiupadhyaya.com/ops`: the header reads `demo · <7 of HEAD_SHA> · <uptime>`; the `this host` column has the eight groups in Task 14's order with no `alpaca`/`fred`; the `peer` column is the one sentence `gated — the live host is behind a password — open live.ohcamel…/ops to see both`. `https://live.ohcamel.ajaiupadhyaya.com/ops` (the browser asks for the password): both columns filled, the peer headed `https://ohcamel.ajaiupadhyaya.com`, `alpaca` and `fred` groups present on this host only, and after a minute — six polls — the demo peer's `Δ nodes_recomputed` strip shows bars while the live host's, outside market hours, is a flat line with the stream row reading `parked` and the arrival caption saying so. A flat line on the demo peer is the alarm the page exists for and would mean the demo engine has stopped moving; it is not expected here.

- [ ] **Step 5: `docs/status.md` says which commit is deployed; commit and push**

Two lines change and one is added. The `Deployed` row at `docs/status.md:43` reads

```
| Deployed | 2026-09-02, both hosts, verified by the production smoke suite: 9 passed, 0 failed |
```

and becomes, with the date of the deploy and the seven-character prefix of `$HEAD_SHA` (the sha both origins reported in Step 4 — not the sha of the docs commit below, which the droplet will not have until Phase 6 redeploys):

```
| Deployed | <date of the deploy>, both hosts, from `<7 of HEAD_SHA>` — both origins report it as `build.git_sha` on `/api/ops` — verified by the production smoke suite: 18 passed, 0 failed |
```

In the `Operating it` block, the line

```
deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com [--live https://live.ohcamel.ajaiupadhyaya.com]
```

becomes

```
deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com [--live https://live.ohcamel.ajaiupadhyaya.com] [--expect-sha "$(git rev-parse HEAD)"]
open https://ohcamel.ajaiupadhyaya.com/ops       # which build, how long, what the process is doing; the live host's /ops draws both
```

Then:

```bash
cd /Users/ajaiupadhyaya/Documents/OhCamel
grep -n 'Deployed\|expect-sha\|/ops' docs/status.md
git diff --stat
git add docs/status.md
git commit -m "docs: both hosts say which commit they are, because until now the deploy log was the only witness"
git push origin main
```

Expected: the three lines above in `grep`'s output; `1 file changed, 3 insertions(+), 2 deletions(-)`; the commit and the push. The droplet is now one docs-only commit behind `origin/main`, which is the state Phase 6's first task starts from.

---

## Done when

- `GET /api/ops` answers on both origins with every key Task 11 names, `build.git_sha` equal to the deployed HEAD on both, `"unknown"` nowhere, and no symbol name anywhere in the live host's object.
- `GET /ops` renders both columns from the live origin and the `gated` sentence from the demo origin, with the two pulse strips, the feed-age strip and the frame-arrival strip drawn from served fields and their differences only.
- `deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com --live https://live.ohcamel.ajaiupadhyaya.com --expect-sha "$(git rev-parse HEAD)"` prints `18 passed, 0 failed, 0 skipped` on the droplet, and `deploy/deploy.sh --live` runs it with that sha without exporting anything.
- The 404 body lists exactly `Server.routes` in order, asserted twice: by `test_server.ml` in-process and by the smoke suite through `EXPECTED_ROUTES`.
- The six credential-free modes' stdout is byte-identical to the capture taken from the commit before Task 8 — six `GATE ok` lines — and `git worktree list` shows one tree.
- `dune build @fmt` and `dune runtest` are green; the live host's Caddy config, `caddy_data` and `deploy/.env` are untouched; the demo's JSON carries `Access-Control-Allow-Origin: *` and its stream does not.
- `docs/status.md`'s `Deployed` row names the sha both origins report, and that commit is pushed.
