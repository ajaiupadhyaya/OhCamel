# OhCamel — a readable front door

## Problem

The engine is finished. All four phases are complete, 94 tests pass, and the
system has been verified against live Alpaca and FRED data. None of that is
visible to someone who opens the repository.

`README.md` is the handoff brief that was written *to* the building agent. It
opens with "This file is a handoff brief for Claude Code. Read it fully before
writing any code," describes a build order for work that is already done, and
never shows the finished system. A reader arrives at a set of instructions for
a project that no longer needs them.

There is also no CI, so the 94 tests are a claim rather than a demonstrated
fact, and there is no visual evidence anywhere that the dashboard exists.

## Goal

A reader who has never seen this project understands, within a minute, what it
is, why the architecture is the interesting part, and how to run it — and can
verify the test claim without taking anyone's word for it.

Non-goal: any change to the engine. The OCaml under `lib/` is done and this
work does not touch its behaviour.

## Design

### 1. The README becomes a description of what exists

Rewritten around the finished system, in this order:

- **What it is** — a reactive risk and limits engine: positions and ticks in,
  continuous exposure, risk metrics, and limit breaches out.
- **The one idea** — most real-time risk dashboards poll, recomputing the whole
  book on a timer regardless of what changed. This one models risk as a
  dependency graph and recomputes only what is downstream of a change, using
  Jane Street's `incremental`. State the contrast concretely; it is the reason
  the project exists and the reason OCaml was the right language.
- **Architecture** — what a node is, what happens when a tick arrives, where
  the graph is defined (`lib/graph.ml`), and how the feed, limits, risk metrics
  and server compose.
- **Run it** — `make demo`, which needs no credentials, then `make test`.
  Live mode and its configuration second, clearly marked as needing keys.
- **What is verified** — 94 tests; the dashboard checked against live Alpaca
  and FRED; the bug that the dashboard itself surfaced during Phase 3.
- **The kill switch** — sets a flag and is deliberately wired to nothing.
  Presented as the design decision it is, not an omission: a risk system that
  can flatten a book on its own is a different and much more dangerous project.
- **Limits** — what this is not, so no reader has to discover it by trying.

### 2. The brief is archived, not deleted

Moved to `docs/brief.md` with a one-line header recording what it was and that
the build it describes is complete. It is the origin document for the whole
project and stays in the repository with its provenance intact.

### 3. CI

`.github/workflows/ci.yml` on `ubuntu-latest` using `ocaml/setup-ocaml`:
`make deps`, `make build`, `make test`, and a `make fmt` check that fails on a
diff. Badge in the README so the test count is backed by a link, not a
sentence.

### 4. Evidence

A screenshot of the running dashboard and of a `make demo` run, committed under
`docs/media/` and embedded in the README. A risk engine that is never shown
working reads as a library; showing it makes it a system.

## Verification

- `make build`, `make test`, `make fmt` pass locally before CI is added, so a
  red first run means the workflow is wrong rather than the code.
- The CI badge is green on `main` before the README claims the test count.
- Every command printed in the README is run exactly as written, from a clean
  checkout, before it ships.
- The screenshots are of the actual build at this commit.

## Out of scope

Engine changes, new risk metrics, wiring the kill switch to anything, and any
broker integration beyond what already exists.
