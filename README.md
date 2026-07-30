# Project brief: reactive risk & limits engine

## Who this is for

This file is a handoff brief for Claude Code. Read it fully before writing any code. It describes the project's purpose, architecture, tech stack, and a phased build order. Work phase by phase — don't jump ahead to the dashboard before the core computation graph works and is tested.

## Context

I'm a CS/Econ student building quant infrastructure. I already have two related projects:
- A backtesting system with rigorous methodology (triple-barrier labeling, Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward validation).
- A stock screening/signals dashboard (Google Sheets + Apps Script) pulling live data from Alpaca and macro data from FRED.

This new project is the missing piece between them: a **real-time risk and limits engine** that takes positions/signals and continuously computes exposure, risk metrics, and limit breaches — the kind of system a prop desk runs, not a retail dashboard.

## The core idea (this is the point of the project, not an implementation detail)

Most "real-time" risk dashboards poll: recompute everything on a timer (every N seconds), regardless of what changed. That doesn't scale and it's always slightly stale.

This system should instead be **reactive / incremental**: model risk quantities as nodes in a dependency graph, where each node knows its inputs. When one input changes (a tick, a fill), only the nodes downstream of that change recompute — not the whole book. In OCaml this is `incremental` (Jane Street's own self-adjusting-computation library, part of the Core ecosystem). This is the single most important architectural decision in this project. If it degrades into "run a loop every second and recompute everything," the project has lost its reason for existing.

## Tech stack

Default to **OCaml**, using:
- `dune` — build system
- `core` / `core_kernel` — stdlib replacement
- `async` — concurrency (for the market data feed, WebSocket client, timers)
- `incremental` — the reactive computation graph (this is the heart of the system)
- `owl` — numerics (matrix/linear algebra for covariance matrices, VaR)
- `cohttp-async` or `dream` — lightweight HTTP/JSON layer to expose state to a dashboard
- `alcotest` or `ounit2` — testing

If any of these libraries turn out to be unmaintained, hard to install, or a poor fit once you start scaffolding, tell me before switching — don't silently substitute a different language or drop the incremental-computation requirement, since that's the architectural core of the project.

## Repository layout (propose, then create)

```
risk-engine/
  dune-project
  bin/
    main.ml              -- entrypoint, wires up the graph and starts the feed
  lib/
    types.ml             -- core domain types: Position, Fill, Tick, Instrument, Limit
    graph.ml             -- the Incremental dependency graph: exposure, VaR, Greeks, limit checks
    risk_metrics.ml       -- pure functions: historical VaR, parametric VaR, expected shortfall, rolling beta
    feed/
      alpaca_ws.ml        -- Alpaca WebSocket client -> feeds Incremental.Var.t cells
      fred_client.ml      -- macro data pull (lower frequency, polling is fine here)
    limits.ml             -- limit definitions and breach logic; the node that can trigger alerts/kill-switch
    server.ml             -- HTTP/JSON layer exposing current graph state
  test/
    test_risk_metrics.ml  -- unit tests for VaR/ES formulas against known reference values
    test_graph.ml         -- tests that changing one input only recomputes the expected downstream nodes
  README.md
```

## Phased build order

Work through these phases in order. Each phase should be runnable/testable before moving to the next.

### Phase 0 — scaffold
- `dune-project` + empty `lib`/`bin`/`test` structure compiling and running "hello world."
- Confirm `core`, `async`, `incremental`, `owl` all install and link cleanly via opam.

### Phase 1 — core graph with synthetic data (no live feed yet)
- Define `types.ml`: `Position`, `Fill`, `Tick`, `Instrument`, `Limit`.
- Build the `Incremental` graph in `graph.ml`:
  - Input `Var.t` cells for positions and prices.
  - Derived node: exposure (per-instrument, per-sector).
  - Derived node: VaR (start with simple historical VaR on a rolling window; parametric VaR next).
  - Derived node: limit check (exposure vs. a configured limit → bool + magnitude of breach).
- Feed it synthetic/randomly generated ticks and fills in a loop and print the recomputed values, to prove the reactive-recompute property. Write a test that asserts: changing one position only triggers recomputation of nodes that depend on it (this is the test that validates the whole architectural premise — don't skip it).

### Phase 2 — real market data
- `alpaca_ws.ml`: connect to Alpaca's WebSocket market data feed, parse ticks, push into the `Var.t` cells from Phase 1.
- `fred_client.ml`: pull macro series (rates, etc.) on a slower polling cadence, feed into a beta/factor-exposure node.
- Handle reconnect/backoff — market data feeds drop connections; the graph shouldn't crash or silently stop updating.

### Phase 3 — dashboard
- `server.ml`: expose current graph state (positions, exposure, VaR, limit status) as JSON over HTTP, plus a WebSocket or SSE stream for live updates.
- A minimal frontend (can be a simple HTML/JS page, doesn't need to be fancy yet) that displays live exposure and VaR and highlights limit breaches.

### Phase 4 — alerting and kill-switch
- `limits.ml`: when a limit-check node breaches, trigger a side effect via an `Incremental.Observer` — start with a logged alert / Slack webhook, then (carefully, behind a config flag) a kill-switch action (e.g., flag "halt new orders").
- This phase touches things that could send messages or take actions — keep it behind explicit config/flags and don't wire it to anything that actually places real trades without me explicitly asking for that later.

## Risk metrics to implement (in `risk_metrics.ml`, as pure functions with unit tests)

- Historical VaR (empirical quantile of historical P&L distribribution)
- Parametric (variance-covariance) VaR
- Expected Shortfall / CVaR (mean of losses beyond the VaR threshold) — prioritize this over VaR alone, since VaR is a weak tail measure
- Rolling beta / factor exposure against macro series (ties into the FRED data)
- Drawdown-based circuit breaker logic (trigger if intraday drawdown exceeds a configurable threshold)

Write these as pure, well-tested functions decoupled from the `Incremental` graph, then wire them in as node computations — this keeps them independently testable and reusable if I ever want to run them outside the live graph (e.g. in the backtester).

## Conventions

- Prefer explicit types over `'a` soup — this is a risk system, illegible types are how bugs hide.
- No implicit float truncation of money/quantity values — use a clear numeric type for prices/quantities from the start.
- Every risk-metric function needs a unit test with a hand-checked reference value, not just a "does it run" smoke test.
- Comment *why* a node depends on what it depends on in `graph.ml` — the dependency structure is the point of the project, so it should be legible to someone reading the code cold.

## What to do right now

Start at Phase 0. Scaffold the repo, confirm the toolchain installs cleanly, and stop to show me the empty structure and any dependency issues before writing the Phase 1 graph logic.

---

# Build notes

Everything below this line was added while scaffolding Phase 0. The brief above is unchanged.

## Quick start

```sh
make build     # compile
make run       # run bin/main.exe (the Phase 1 synthetic-feed driver)
make test      # run the alcotest suite
make fmt       # ocamlformat, in place
make doctor    # print installed versions -- start here when a build looks wrong
```

The opam switch is **project-local** (`./_opam`), so the compiler and every
dependency live inside this repo and are not on your `PATH` by default. Each
`make` target re-enters the switch itself, so the commands above work from a
clean shell. To use `dune` directly instead, run `eval $(opam env)` once inside
the repo first.

`opam init` was run with `--no-setup`, so **your `~/.zshrc` was not modified**.
If you want `cd`-ing into an opam project to configure the environment
automatically, add the hook yourself with `opam init --enable-shell-hook`.

## Toolchain as installed

| Package | Version | Notes |
|---|---|---|
| OCaml | 5.2.1 | Pinned deliberately -- see below |
| dune | 3.24.1 | |
| core | v0.17.2 | requires OCaml >= 5.1.0 |
| async | v0.17.0 | requires OCaml >= 5.1.0 |
| incremental | v0.17.0 | requires OCaml >= 5.1.0 |
| owl | 1.2 | needs the workaround below to build |
| cohttp-async | 6.2.2 | |
| alcotest | 1.9.1 | |

OCaml **5.2.1** rather than the newest 5.5.0: Jane Street's v0.17 releases
require `>= 5.1.0` and are tested against 5.1/5.2. The newest compilers
routinely break `ppxlib`, which every `ppx_*` package in the Core ecosystem
depends on, and `core`/`async`/`incremental` are non-negotiable here while owl
is replaceable. Worth revisiting when Jane Street publishes a v0.18.

Homebrew packages installed to satisfy opam's system dependencies: `opam`,
`zlib`, `pkg-config`, `gpatch`. (`openblas`, `gmp`, and `libomp` were already
present.)

## Deviations from the brief

Three, all small, none architectural:

1. **Layout is at the repo root**, not nested inside a `risk-engine/`
   subdirectory. The repo is already named OhCamel and holds nothing else, so
   the extra level would only add a path segment. `lib/`, `bin/`, `test/` and
   the module names are exactly as specified.

2. **`cohttp-async` over `dream`** for Phase 3. The brief allows either. Dream's
   only releases are `1.0.0~alpha7` and `~alpha8`; cohttp-async 6.2.2 is stable
   and shares async with the rest of the system, so there is no second
   concurrency runtime to reconcile. Nothing is written against it yet -- this
   is reversible at Phase 3 for the price of one file.

3. **`lib/toolchain_check.ml` exists and is not in the brief's layout.** It was
   Phase 0 scaffolding proving the four libraries link and run. Phase 1 now
   exercises `core`, `incremental` and owl's BLAS path for real, so those checks
   were removed; what remains is the two link paths no real code reaches yet
   (owl's LAPACK entry point, and `async`). The file names the condition under
   which each of those should go too.

## Known issue: owl 1.2 does not build out of the box on Apple clang 21

Two independent problems, both worked around in the `Makefile` (which documents
each in full). Recorded here because they cost real time to diagnose and will
recur on any fresh machine:

1. **Apple clang 21.0.0 segfaults** compiling
   `src/owl/core/owl_ndarray_maths_stub.c` at any optimisation level above
   `-O1`, and owl's build appends `-O3 -march=native` on arm64 macOS. Bisected
   against the exact failing command: `-O3` crashes with or without
   `-march=native`, `-O2` crashes, `-O1` compiles. Worked around with
   `OWL_CFLAGS`.

2. **The link then fails on missing OpenMP symbols** (`___kmpc_fork_call` and
   friends). Homebrew's OpenBLAS is built `USE_OPENMP=1` and advertises
   `-Xpreprocessor -fopenmp` in its pkg-config cflags; owl passes those to its
   compiler and emits OpenMP code, but only links `-lomp` when
   `OWL_ENABLE_OPENMP=1`, which is unset. So it compiles OpenMP code it never
   links a runtime for. Worked around with `OWL_LDLIBS`.

**Cost:** owl's C kernels are built at `-O1`. This does *not* affect BLAS or
LAPACK -- those calls land in Homebrew's separately-compiled OpenBLAS, and the
heavy linear algebra here (covariance, parametric VaR) goes through BLAS. Owl's
own ndarray loops stay OpenMP-parallel. The practical impact should be small,
but it is worth re-measuring if a risk node ever profiles hot.

If owl becomes a liability rather than a convenience, the realistic swap is
`lacaml` (direct BLAS/LAPACK bindings, no C kernels of its own). Per the brief,
that is your call, not one to make silently -- and it is not needed today.

## Phase 0 status

Complete. The toolchain installs and links; see the owl section above for the
one thing that did not go smoothly.

## Phase 1 status

Complete. `make test` passes **36/36**, `make run` drives the graph with a
synthetic feed, and every expected value in the suite is hand-derived with the
derivation written next to it.

### What exists

| File | What it holds |
|---|---|
| `lib/types.ml` | `Symbol`, `Sector`, `Qty`, `Price`, `Notional` -- abstract and mutually incompatible; `Instrument`, `Tick`, `Fill`, `Position`, `Limit`, `Breach` |
| `lib/risk_metrics.ml` | Pure functions: historical VaR, expected shortfall, parametric VaR, covariance matrix, beta, portfolio stddev, drawdown. No Incremental anywhere in the file |
| `lib/limits.ml` | Limit validation, breach evaluation, utilisation, rendering. Still no side effects -- those are Phase 4 |
| `lib/graph.ml` | The Incremental graph. Input cells, ~30 derived nodes, observers, the snapshot API |
| `bin/main.ml` | Synthetic driver: 60 events over a six-name long/short book, then a scaling probe |
| `test/test_risk_metrics.ml` | 17 cases against hand-computed reference values |
| `test/test_graph.ml` | 17 cases, six of which are the architecture tests |

### The test that validates the premise

The brief asked for one test above all others. It is
`test/test_graph.ml`, and it is six tests rather than one, because the claim has
more than one edge to it. Each seeds the book, clears a per-node recomputation
counter, changes **exactly one input**, stabilizes, and asserts on the *exact
set* of node names that ran -- set equality, so a node running that should not
have is a failure, not a silent pass.

```
[OK]  graph  3  ARCHITECTURE: a position change recomputes only its dependents
[OK]  graph  4  ARCHITECTURE: a price tick recomputes only its dependents
[OK]  graph  5  ARCHITECTURE: no price change can reach the covariance matrix
[OK]  graph  6  ARCHITECTURE: new returns leave the exposure side alone
[OK]  graph  7  ARCHITECTURE: an unchanged input recomputes nothing
[OK]  graph  8  ARCHITECTURE: propagation stops where values stop changing
```

Test 5 is the one to read first. The covariance matrix is the most expensive
thing the engine computes and it depends on the return windows and nothing else,
so no price or position change can reach it. A poll-and-recompute engine rebuilds
it on every tick.

Test 8 is the subtle one: flatten a position to zero shares and its exposure
becomes zero whatever the price does. The exposure node still runs -- it sits on
the price cell and has to look -- but the cutoff holds the line there and the
twenty-odd nodes behind it never wake up.

### What `make run` shows

Sixty events over a six-name book: ordinary ticks, occasional fills, and a bar
close every tenth event (which is the only event that touches the covariance
matrix). The rightmost column is nodes recomputed:

```
  evt  what changed                  gross          net       equity    VaR 95%   drawdn  nodes
  8    tick NVDA 900.70           $316,407     $132,179   $1,132,179     $2,290    0.00%     21
  9    tick JPM 201.74            $316,448     $132,220   $1,132,220     $2,290    0.00%     18
  10   BAR close, all names       $312,675     $130,287   $1,130,287     $2,241    0.15%     33
  17   FILL -100 CVX              $325,758     $116,178   $1,129,836     $2,381    0.19%     18
         !! energy-cap [sector:ENERGY] BREACH: $104790.05 > $100000.00 (over by $4790.05)
```

Breaches are **edge-triggered** -- only transitions print, in either direction.
A limit that has been breached for twenty events is not twenty pieces of news,
and Phase 4 replaces that `printf` with a real notifier.

On this six-name book the headline saving is only ~30%, which is honest and also
the worst case: with six instruments the portfolio-level nodes genuinely depend
on everything, so there is not much left to skip. The number that matters is how
the cost of one tick moves as the book grows, which the run also measures:

```
   instruments   nodes in graph     nodes per tick        if polled
            10               40               19.6               40
           100              229               19.2              229
           400              859               20.0              859
```

Flat middle column, linear right one. The cost of an event is set by what the
event touches, not by how large the book is.

### Design decisions worth your attention

Six calls I made that a reasonable person could have made differently:

1. **Equity is modelled, so drawdown is live.** The graph carries a cash cell;
   equity is cash plus net exposure, and a fill moves both, so equity does not
   jump at execution (there is a test for exactly that). The alternative --
   equity as a pushed input -- would have made the drawdown breaker blind to
   price moves between fills.

2. **The drawdown breaker reads *current* drawdown, not maximum.** A breaker
   keyed to the historical max latches on forever after one bad morning and can
   never be cleared by recovery. Equity marks are closed explicitly by the
   driver (`mark_equity`) at bar boundaries, not per tick, because a peak made
   of every intraday print is measuring noise.

3. **Risk numbers are `float option`, not `float`.** Before every instrument has
   enough return history there is no distribution to take a quantile of. The
   snapshot says `warming_up` and lists the VaR limit under
   `unevaluated_limits` rather than quietly among the limits that passed --
   because a limit missing from a list of breaches reads as a limit that is
   fine. Returning `0.0` would render as "no risk".

4. **Portfolio returns use *current* weights across the whole window.** This
   answers "what would today's book have done through this history", which is
   the question a limit is asking, rather than "what did the book that existed
   then actually do". It is the standard approximation but it is an
   approximation.

5. **Value-equality cutoffs on every input cell.** A live feed republishes the
   same last price constantly; under Incremental's default physical-equality
   cutoff each identical float is a distinct box and would drag a full VaR
   recomputation behind it. Test 7 pins this: three no-op writes cost exactly
   three node recomputations (one watch node each) and nothing downstream.

6. **`Value_at_risk` and `Max_drawdown` limits are portfolio-scoped only.**
   Both are statistics of a correlated return series and an equity curve, and
   this engine maintains neither per instrument. Rather than answer a
   sector-scoped VaR limit with the portfolio number, the pairing is rejected at
   construction. All limit validation happens before a single node exists, which
   is what lets every node body be total -- an exception during stabilization
   takes the whole graph down.

### Not done, deliberately

- `lib/feed/alpaca_ws.ml` and `lib/feed/fred_client.ml` are still comment-only
  (Phase 2), as is `lib/server.ml` (Phase 3).
- Nothing sends a message or takes an action. `limits.ml` computes breaches as
  data only; the alerting and kill-switch wiring is Phase 4 and stays behind
  explicit config per the brief.
- Sector exposure is netted, not gross-within-sector. Both are defensible; say
  the word if you want the other one, or both.