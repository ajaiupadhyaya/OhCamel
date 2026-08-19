# OhCamel Readable Front Door — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the agent handoff brief that currently serves as `README.md` with a description of the finished system, and back its claims with CI and screenshots.

**Architecture:** Documentation and CI only. No file under `lib/`, `bin/` or `test/` changes behaviour. The brief moves to `docs/brief.md` with provenance; a new README is written against the code as it exists; a GitHub Actions workflow builds, tests and format-checks on every push.

**Tech Stack:** OCaml 5.2.1, dune 3.16, alcotest, ocamlformat 0.29.0, GitHub Actions (`ocaml/setup-ocaml@v3`).

**Spec:** `docs/superpowers/specs/2026-08-19-readable-front-door-design.md`

## Global Constraints

- OCaml version floor is `>= 5.1.0` per `dune-project`; the local switch is 5.2.1. CI pins **5.2.1**.
- ocamlformat is pinned to **0.29.0** in `.ocamlformat`. CI must install exactly that version or `dune fmt` will report spurious diffs.
- Owl is a real dependency (17 call sites in `lib/risk_metrics.ml`) and needs OpenBLAS. On Linux CI this means `libopenblas-dev` and `pkg-config`.
- The macOS-only Owl workarounds in the `Makefile` (`OWL_CFLAGS` lowering `-O3` to `-O1`, `OWL_LDLIBS` adding `-lomp`) exist because Apple clang 21 segfaults. **Do not export them in Linux CI** — gcc compiles Owl at `-O3` without incident, and forcing `-O1` there would be cargo-culting a macOS bug.
- `make test` currently reports **94 tests, 0 failures**. That number appears in the README and must match what CI prints.
- The kill switch sets a flag and is wired to nothing. Every mention must preserve that as a deliberate decision.

---

### Task 1: Archive the brief with its provenance

**Files:**
- Create: `docs/brief.md`
- Modify: `README.md` (deleted in this task, rewritten in Task 2)

**Interfaces:**
- Consumes: nothing.
- Produces: `docs/brief.md` — the path Task 2's README links to as the project's origin document.

- [ ] **Step 1: Move the brief, preserving history**

```bash
cd ~/Documents/OhCamel
mkdir -p docs
git mv README.md docs/brief.md
```

- [ ] **Step 2: Add the provenance header**

Insert these lines at the very top of `docs/brief.md`, above the existing `# Project brief: reactive risk & limits engine`:

```markdown
> **Archived — this is the original brief, kept for provenance.**
>
> This document was written *to* the agent that built OhCamel, before any code
> existed. It describes a phased build order that is now complete: all four
> phases shipped, and the engine it specifies is the engine in `lib/`. It is
> preserved because it records why the architecture is what it is — in
> particular why incremental recomputation was non-negotiable — not because
> anything here is still an instruction.
>
> For what the system actually is and how to run it, see [the README](../README.md).

```

- [ ] **Step 3: Verify the move is staged as a rename, not a delete-plus-add**

Run: `git status --short`
Expected: a single line beginning `R ` mapping `README.md -> docs/brief.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/brief.md
git commit -m "docs: archive the build brief, with provenance

It was written to the agent that built this, before any code existed. All
four phases it specifies are done, so it is a record of why the architecture
is what it is, not a set of instructions. The README slot it was occupying
belongs to a description of the finished system."
```

---

### Task 2: Write the README against the code that exists

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: `docs/brief.md` from Task 1 (linked as the origin document).
- Produces: `README.md` containing a `## Verified` section whose test count Task 3's CI badge substantiates, and placeholder-free image references `docs/media/dashboard.png` and `docs/media/demo.png` that Task 4 creates.

- [ ] **Step 1: Read the source material before writing a word**

Do not write the README from this plan. Read these first, because the README must describe what they do:

```bash
cd ~/Documents/OhCamel
sed -n '1,60p' lib/graph.ml      # the graph shape comment is the architecture section
sed -n '689,760p' bin/main.ml    # what `make demo` actually runs
sed -n '1,40p' lib/limits.ml
sed -n '1,40p' lib/alerts.ml
grep -n "^[a-z-]*:" Makefile     # the exact target names
```

- [ ] **Step 2: Write `README.md`**

Structure, in this order. Prose, not bullets, wherever a claim needs a reason.

1. **Title + one-sentence definition.** A reactive risk and limits engine: positions and ticks in; continuous exposure, VaR, expected shortfall and limit breaches out.
2. **The one idea.** Most real-time risk dashboards poll — recompute the whole book on a timer, regardless of what changed, and so are always slightly stale and scale badly. OhCamel models risk as a dependency graph and recomputes only what is downstream of a change, using Jane Street's `incremental`. Say why that forced OCaml. Quote the rule from the top of `lib/graph.ml`: a node may only read its declared inputs, because a dependency Incremental cannot see is a dependency it will happily serve a stale answer for.
3. **The graph.** Reproduce the ASCII graph shape from the `lib/graph.ml` header comment in a fenced block, then name the modules: `graph.ml` (the nodes), `feed/` (Alpaca websocket + FRED, folded in as top-level modules per `lib/dune`), `limits.ml`, `risk_metrics.ml`, `alerts.ml`, `server.ml`, `dashboard_html.ml`.
4. **Run it.** `make demo` first — dashboard on a synthetic feed, no credentials, no network, works when the market is closed, and one symbol is deliberately never ticked so the staleness path is visible rather than theoretical. Then `make test`. Then, clearly marked as needing keys, `make run-live` and `make serve`, including the note that a free Alpaca plan allows one concurrent market-data stream per account.
5. **Verified.** 94 tests. The dashboard checked against live Alpaca and FRED. The bug the dashboard itself surfaced in Phase 3 — read the `aede5ca` commit message for what it was and say so concretely.
6. **The kill switch.** It sets a flag and is wired to nothing, on purpose. A risk system that can flatten a book by itself is a different and far more dangerous project than this one; the flag is the seam where that decision would be made deliberately.
7. **Building it.** Point at `make deps` and `make doctor`, and note the local `./_opam` switch. Link the Owl/clang workaround comments in the `Makefile` rather than restating them.
8. **What this is not.** No order routing, no execution, no persistence beyond the running process, one broker.
9. **Origin.** One line linking `docs/brief.md`.

Include the CI badge line, which will be dark until Task 3 lands:

```markdown
[![ci](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml/badge.svg)](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml)
```

And the two image references Task 4 will satisfy:

```markdown
![The dashboard, driven by the synthetic feed](docs/media/dashboard.png)
![make demo](docs/media/demo.png)
```

- [ ] **Step 3: Run every command the README prints, exactly as written**

```bash
cd ~/Documents/OhCamel
make build
make test
make demo &   # then open http://localhost:8080, confirm it renders, then: kill %1
make doctor
```

Expected: `make test` prints `Test Successful in <time>. 94 tests run.` If it prints a different count, correct the README to the real number — the number follows the suite, never the reverse.

- [ ] **Step 4: Verify no link in the README 404s**

```bash
grep -oE '\]\([^)h][^)]*\)' README.md | tr -d ']()' | while read -r f; do
  [ -e "$f" ] || echo "MISSING: $f"
done
```

Expected: only `docs/media/dashboard.png` and `docs/media/demo.png` are reported, since Task 4 creates them. Any other missing path is a defect to fix now.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: a README that describes the finished engine

What it is, why incremental recomputation rather than polling is the whole
point, the graph shape, and how to run it without credentials. The kill
switch is documented as the deliberate no-op it is."
```

---

### Task 3: CI that proves the 94 tests

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the badge URL committed in Task 2.
- Produces: a green `ci` run on `main`, which is the evidence Task 2's `## Verified` section rests on.

- [ ] **Step 1: Write the workflow**

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  # Only collapse superseded PR runs. A push to main must never cancel main's
  # own last recorded result — that is how an Actions tab ends up showing a
  # cancellation as its newest outcome.
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      # Owl links against OpenBLAS. The Makefile's OWL_CFLAGS/OWL_LDLIBS
      # workarounds are deliberately NOT exported here: they exist because
      # Apple clang 21 segfaults compiling Owl's C stubs above -O1, which is a
      # macOS-only compiler bug. gcc builds Owl at its own -O3 fine.
      - name: System dependencies for Owl and async_ssl
        run: |
          sudo apt-get update
          sudo apt-get install -y pkg-config libopenblas-dev liblapacke-dev \
                                  libffi-dev libssl-dev

      - uses: ocaml/setup-ocaml@v3
        with:
          ocaml-compiler: "5.2.1"
          dune-cache: true

      - name: Install dependencies
        run: opam install --deps-only --with-test -y .

      - name: Build
        run: opam exec -- dune build

      - name: Test
        run: opam exec -- dune runtest --force

      - name: Format check
        run: |
          opam install -y ocamlformat.0.29.0
          opam exec -- dune build @fmt
```

- [ ] **Step 2: Confirm the format check agrees with the local pin before pushing**

```bash
cd ~/Documents/OhCamel
grep version .ocamlformat
eval $(opam env --switch=$(pwd) --set-switch) && dune build @fmt
```

Expected: `.ocamlformat` reports `version = 0.29.0`, matching the version pinned in the workflow, and `dune build @fmt` exits 0 with no diff. If it prints a diff, run `make fmt`, review it, and commit that separately before continuing — CI should not be the thing that discovers formatting drift.

- [ ] **Step 3: Commit and push, then watch the run**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: build, test and format-check on every push

The 94 tests were previously a sentence in a document. Ubuntu rather than
macOS because Owl's clang workarounds are a macOS compiler bug, not a
portable requirement."
git push
gh run watch --exit-status
```

- [ ] **Step 4: Verify the run is green and the count matches**

```bash
gh run list -w ci.yml --limit 1
gh run view --log | grep -E "tests run|Test Successful"
```

Expected: conclusion `success`, and the log contains `94 tests run`. If the count differs from the README, fix the README.

If the run fails on Owl or `async_ssl`, that is a real portability finding and not a reason to weaken the workflow: read the error, add the missing system package, and push again. Do not switch the runner to macOS without first exhausting the apt route — a Linux CI is the stronger claim.

---

### Task 4: Show the thing running

**Files:**
- Create: `docs/media/dashboard.png`
- Create: `docs/media/demo.png`

**Interfaces:**
- Consumes: the two image paths referenced by Task 2's README.
- Produces: nothing downstream.

- [ ] **Step 1: Start the demo**

```bash
cd ~/Documents/OhCamel
make demo
```

Leave it running. It serves on `http://localhost:8080`, needs no credentials, and deliberately leaves one symbol unticked so the staleness indicator is visible.

- [ ] **Step 2: Capture the dashboard**

Open `http://localhost:8080` in a browser and capture the window to `docs/media/dashboard.png`. Frame it so a limit breach and the stale symbol are both visible — those two states are what distinguish this from a table of numbers.

Do not synthesize mouse or keyboard events to arrange the screen; capture what the demo renders on its own.

- [ ] **Step 3: Capture the terminal**

Capture the terminal showing the `make demo` invocation and its startup output to `docs/media/demo.png`. Crop to the command and the first screenful — the point is that one command with no arguments and no credentials produces a running system.

- [ ] **Step 4: Confirm the README now has no missing links**

```bash
grep -oE '\]\([^)h][^)]*\)' README.md | tr -d ']()' | while read -r f; do
  [ -e "$f" ] || echo "MISSING: $f"
done
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add docs/media/dashboard.png docs/media/demo.png
git commit -m "docs: screenshots of the demo and the dashboard

A risk engine nobody has seen running reads as a library."
git push
```
