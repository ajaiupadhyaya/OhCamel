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
make run       # synthetic: generated ticks and fills, prints, exits. No keys.
make demo      # synthetic feed + live dashboard on :8080. No keys, no network.
make run-live  # live Alpaca + FRED, printing to the terminal (needs keys)
make serve     # live Alpaca + FRED + dashboard on :8080 (needs keys)
make test      # the alcotest suite (hermetic -- never touches the network)
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

## Phase 2 status

Complete and **verified against live market data**. `make test` passes **70/70**
and never touches the network; `make run-live` connects to Alpaca and FRED.

A real run, mid-session:

```
  2026-07-30 16:45:56Z backfill  6 symbols, 60 daily returns each
  2026-07-30 16:45:56Z alpaca  websocket handshake complete
  2026-07-30 16:45:56Z alpaca  authenticated; subscribing to 6 symbols

  gross $458,433   net $187,551   equity $1,187,551   drawdown 0.00%
  VaR95 $7,596   ES95 $9,081   beta -0.046
  feed: all symbols live
  alpaca[frames=157 trades=206 rejected=0 unknown_symbol=0 reconnects=0]
  fred[polls=1 ok=1 observations=57 consecutive_failures=0]
  work[nodes=693 over 59 trades, 11.7 per trade]
```

That last line is the Phase 1 claim measured on real traffic. It is *lower* than
the ~20 nodes/tick the synthetic scaling probe reports, because a frame carrying
several prints of the same symbol coalesces into one propagation — which is
exactly what the set-then-stabilize split was built for.

### What was added

| File | What it does |
|---|---|
| `lib/config.ml` | Credentials from the environment behind a `Secret.t` that cannot be printed; the book (positions, cash, limits) from a sexp file |
| `lib/feed/alpaca_ws.ml` | The v2 market-data stream: handshake state machine, price validation, error classification, exponential backoff with jitter |
| `lib/feed/alpaca_rest.ml` | One-shot historical backfill of daily bars, so the risk numbers exist at startup |
| `lib/feed/fred_client.ml` | FRED macro series on a slow poll, differenced into changes |
| `test/test_feed.ml` | 27 cases over captured fixtures — mostly hostile input |

Plus, in `graph.ml`: a **feed-health** branch and a **portfolio-beta** node.

### Three things worth knowing

**1. The staleness clock is a dead end, deliberately.** Feed health is genuinely
a function of wall-clock time, so the graph has a `now` cell that a timer
advances. That cell is the most dangerous thing in the codebase: if *any* risk
node were downstream of it, the timer would recompute the book on a schedule and
this engine would have quietly become the poller it was written to replace —
while still passing every value-based test, because all the numbers would be
right. Only a recomputation-set assertion catches that, so
`test_graph.ml` has one.

**2. Live mode needs a backfill, not just a stream.** The WebSocket delivers
prices, not history, and without history there is no distribution to take a
quantile of. Waiting for a 60-observation daily window to fill from the live feed
would take three months, so `alpaca_rest.ml` fetches it once at startup. Daily
bars specifically, because FRED publishes DGS10 daily and a beta between series
at different frequencies is arithmetically fine and economically meaningless.

**3. Nothing here sends a message or takes an action.** `limits.ml` still
computes breaches as data. Alerting and the kill-switch remain Phase 4, behind
explicit config, wired to nothing that places orders.

### Two bugs the tests caught

**`beta` fabricated a rates exposure.** It tested its factor for `variance = 0.0`
exactly. Ten copies of `0.0425` have a true variance of zero but a computed one
near `1e-33`, because `0.0425` is not representable — so `beta` divided one
rounding residue by another and returned **-0.3**. Finite, plausible, and read on
a dashboard it asserts the book is inversely exposed to rates. Now
`is_effectively_constant`, which tests relative to the series' own magnitude.

**The feed reconnected forever on credentials that could never work.** Error 402
was classified fatal correctly, but Alpaca sends the error *and then* closes the
socket, so `End_of_file` reached the supervisor first and won the race. Five
reconnects, each faithfully printing the message explaining that reconnecting was
pointless. A session now reports one `Outcome`, decided in one place, with the
fatal reading taking precedence over whatever the socket did on the way down.

### Running it

```sh
cp book.example.sexp book.sexp        # then edit: positions, cash, limits
set -a; source /path/to/.env; set +a  # ALPACA_API_KEY, ALPACA_SECRET_KEY, FRED_API_KEY
make run-live
```

Neither `.env` nor `book.sexp` is tracked. The engine reads keys from the
environment only — it never reads a committed file — and **refuses to start** if
one is missing rather than degrading to something that looks live.

`OHCAMEL_LOG_LEVEL=debug` turns on the websocket library's own logging. Worth
knowing it exists: `websocket-async` reports handshake failures through `logs`,
and with no reporter installed a rejected upgrade surfaces as nothing but a
socket that closes 80ms after opening.

### Gotchas that cost time

- **`websocket-async` needs `Mirage_crypto_rng` seeded** before first use, or
  every handshake fails from inside the library. Nothing documents this.
  `alpaca_ws.ml` does it lazily so callers need not know.
- **A free Alpaca plan allows ONE concurrent market-data stream per account.** If
  anything else uses the same keys, this gets error 406 and stops. Deliberately
  fatal rather than retried.
- **`adjustment=all` on the bars request is not optional.** Unadjusted closes
  turn a 2-for-1 split into a -50% single-day return, which in a 60-day window at
  95% *is* the tail — VaR would report a 50% loss and hold there for months.
- **FRED writes `"."` for missing observations.** A lenient parser coerces that
  to 0.0, which in a yield series is a claim that the 10-year yielded nothing
  that day.

### Not done in Phase 2

- Positions are configured, not live. The brief scoped Phase 2 to market data +
  FRED, so Alpaca is the source of *marks*, not of the position set. Wiring
  `/v2/positions` and the `trade_updates` stream is a small addition if you want
  it — `Types.Fill` and `Graph.apply_fill` already exist and are tested.
- The bars request does not paginate. Fine to several hundred instruments; a
  larger book would need the `next_page_token` loop.

## Phase 3 status

Complete. `make test` passes **78/78**, still hermetic. `make demo` serves a live
dashboard with no credentials at all; `make serve` does it against the real
market.

```
lib/server.ml           JSON + HTTP + SSE, ~330 lines
lib/dashboard_html.ml   the page, embedded so the binary is self-contained
test/test_server.ml     7 cases over the wire format
```

Routes: `/` (the page), `/api/snapshot`, `/api/health`, `/api/stream` (SSE).

### The stream is not a timer, and that is the point

The broadcaster blocks on an `Ivar` that `Graph.on_change` fills, and
`Graph.on_change` is wired to `Incremental.Observer.on_update_exn` on every
published node. If nothing in the book moves, the loop is parked and not one
byte is serialized — no wakeup, no snapshot, no frame.

That closes the chain the whole project is arguing for. An engine that is
reactive internally but *polled at its edge* has moved the timer, not removed
it.

There is an 80ms `after` in the loop and it is worth being precise about what it
is: it runs **after** a change has already been observed, to coalesce a burst of
ticks into one frame. It never causes a wakeup. A timer asks "has anything
changed?"; this asks "how many more changes arrive in the next 80ms?".

SSE rather than WebSocket: the traffic is one-directional, so half of what a
WebSocket offers is unused, and `EventSource` reconnects on its own — which
matters for a page meant to be watched all day on a laptop that sleeps.

### The dashboard

Three design decisions, each derived from this engine rather than from what risk
dashboards usually look like.

**The layout is the graph.** Three columns left to right: positions, book
aggregates, limits. That is the dependency order in `graph.ml` — reading the
page left to right is reading the graph downstream.

**Changed values are marked.** Every number that moved since the last frame gets
a brief rule under it. This is the architecture made visible, and it is real
data: the client diffs successive snapshots, so a mark means that value actually
changed. A tick in one name lights that instrument, its sector and the
aggregates, and visibly does not light the others.

**The numbers lose authority when the feed does.** If prices go stale the risk
figures desaturate and pick up a hatch — and the dimming is scoped *by
dependency*, not by page. A stale print on CVX dims CVX, the ENERGY sector, and
everything computed downstream; the other five instruments stay at full
strength, because their own exposures are still exactly right. Same discipline
as the graph, expressed in CSS.

Both colour schemes are authored rather than one inverted, and neither uses
green-on-black. Monospace means data and nothing else.

### The bug the dashboard found

Looking at the live page after the close, four of six positions read **$0**.

Live mode set quantities from the book file but took prices *only* from the tick
stream — so any symbol that had not printed yet sat at its initial zero, and a
position marked at zero contributes zero to exposure. Gross read **$217,590**
against a true **$459,266**, with nothing on the page suggesting the number was
wrong.

That is worse than staleness and a different kind of wrong: a stale price is a
real price from earlier, a zero is a price that never existed. Fixed by marking
the book from the last close during the REST backfill, which was already being
fetched. Deliberately via `Graph.set_price`, not `Graph.apply_tick` — a closing
price is a real mark but it is *not* evidence the feed is alive, so feed health
correctly goes on reporting those symbols as never-seen. That distinction is
exactly why the two setters are separate.

No unit test would have caught it. Looking at the thing did.

### Not done in Phase 3

- The dashboard is read-only and unauthenticated — bind it to localhost. There is
  no auth because there is nothing to authorise: no route mutates anything.

## Phase 4 status

Complete. **94 tests**, still hermetic — no test posts a webhook, writes outside
a temp path, or opens a socket.

```
lib/alerts.ml        edge detection, sinks, the kill switch
test/test_alerts.ml  16 cases, mostly about what it does NOT send
```

### Everything is off by default

`Config.Alerts.default` has `enabled = false`. A breach is computed, displayed,
and otherwise ignored until someone writes down that they want otherwise. The
kill switch is a **second, separate flag**, because "tell me when a limit
breaks" and "act when a limit breaks" are different levels of trust and should
not share a switch.

The `alerts` block in `book.example.sexp` is optional — omit it entirely and the
engine does nothing. An existing book file keeps working and keeps being inert.

### What the kill switch actually does

It sets a flag. `halt_new_orders` returns a bool, shown on the dashboard and in
the API.

**Nothing in this repository places, cancels or modifies an order**, and
`lib/alerts.ml` does not import the Alpaca client — it cannot reach a trading
endpoint even by mistake. Connecting it to execution is a deliberate later
decision, not a default. Per your brief, I have not wired it to anything.

Tripping latches until an explicit reset. A breaker that re-armed itself when
the number came back under the line would silently un-halt a book while nobody
was looking — worse than having none, because you would believe a decision was
still in force.

### Effects hang off an observer, never a node body

`Graph.on_breaches` attaches to the breaches **observer**, which is what the
brief asks for and what `limits.ml` has been pointing at since Phase 1:
Incremental may recompute a node whenever it likes, so a node that sent a Slack
message could send several. An observer fires on *change*.

Even then the handler only writes to a pipe; an Async consumer does the sending,
so a slow webhook cannot stall the graph.

`limits.ml` stayed pure, which deviates from the brief's placement. The reason is
structural: `Limits.evaluate` is called from *inside* node bodies, so if the
sending code lived beside it an effect would be one careless call away from
firing during a stabilize. Separating them makes the invariant something the
module graph enforces rather than something a reader has to remember.

### Three things the tracker deliberately does not do

- **It does not repeat itself.** A limit breached for twenty minutes is one piece
  of news, not twenty. A channel that fires on every tick is a channel people
  mute, and a muted channel is worse than none because everyone believes they are
  being watched.
- **It does not flap.** A value oscillating across its threshold produces *one*
  alert. Once raised, it clears only when utilisation falls back below
  `clear_below` (default 0.95) — it has to come properly back inside the line,
  not merely stop being outside it.
- **It never clears because the data went away.** If a firing limit becomes
  unevaluable, the alert stays firing. "I can no longer tell" is not "it is
  fine", and a system that resolves an incident because it lost sight of it
  actively reports the all-clear. This is the same principle the graph applies to
  unevaluated limits and the dashboard applies to stale prices, at the one layer
  where getting it wrong sends someone back to bed.

### How it was verified

`make demo` turns alerting on with a log-only sink and arms the kill switch on
one limit, sized so it actually breaches:

```
ALERT  BREACH  nvda-cap [instrument:NVDA]  $54307.75 over $54200.00  (100% of limit)
ALERT  KILL SWITCH TRIPPED by nvda-cap [instrument:NVDA] ... new orders flagged as halted
```

The dashboard then shows an inverted **NEW ORDERS HALTED** banner naming what
tripped it — the loudest thing the page can display, outranking even the stale
warning.

The Slack sink was verified end-to-end against a **local listener on
127.0.0.1**, never a real webhook:

```
RECEIVED POST /hook  ct=application/json
  {"text":":rotating_light:  ohcamel: BREACH  aapl-cap [instrument:AAPL] ..."}
RECEIVED POST /hook  ct=application/json
  {"text":":octagonal_sign:  ohcamel: KILL SWITCH TRIPPED by aapl-cap ..."}
```

Your real `SLACK_WEBHOOK_URL` was never contacted. Use the `Dry_run` sink first
— it prints the exact payload it *would* send and sends nothing.

### Not done

- The kill switch is not connected to execution, by design and per your brief.
  Say the word and it becomes a small change; it should be a conversation, not a
  default.
- No alert deduplication across restarts. A restart re-raises anything still
  breached, which is arguably correct (the new process has not told you yet) but
  is worth knowing.