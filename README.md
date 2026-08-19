# OhCamel

[![ci](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml/badge.svg)](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml)

A reactive risk and limits engine. Positions and market data go in; per-instrument
and per-sector exposure, gross and net, VaR, expected shortfall, portfolio beta,
drawdown and limit breaches come out — and keep coming out, updated as the market
moves rather than as a clock ticks.

![The dashboard, driven by the synthetic feed](docs/media/dashboard.png)

## Why it isn't a loop

Most real-time risk systems poll. A timer fires, the process recomputes the whole
book, renders it, sleeps. That design is easy to write and it is wrong in two ways
at once. The number on screen is as old as the last tick of the *timer*, not the
last tick of the *market*, so it is always slightly stale by construction. And the
cost of an update scales with the size of the book rather than the size of the
change: one print in one name pays for an n×n covariance matrix that no price
could possibly have altered, because covariance is computed from a return window
that a mid-session print does not touch.

OhCamel does the other thing. Risk here is a dependency graph. Prices, quantities,
cash, return windows, the factor series and the current time are input cells;
everything derived from them is a node with declared edges. A tick sets one cell,
and the runtime works out what is downstream of it and recomputes exactly that.
The rest of the graph is not "recomputed and found unchanged" — it is never
visited.

`make run` prints that claim as a table. It walks a synthetic book through sixty
events, counts what Incremental actually recomputed for each one, and then asks
what the same graph would cost at three book sizes:

```
 instruments   nodes in graph     nodes per tick        if polled
------------------------------------------------------------------
          10               52               20.6               52
         100              331               20.2              331
         400             1261               21.0             1261
```

The middle column is flat and the right one is not. The cost of an event is set
by what the event touches, not by how large the book is.

The runtime is [Incremental](https://github.com/janestreet/incremental), Jane
Street's self-adjusting computation library, and it is the reason this is an OCaml
project. Incremental is the mature implementation of this idea and it does not
exist outside OCaml; writing one would have *been* the project rather than a
dependency of it. The rest of the language pulls its weight too — `lib/types.ml`
makes `Price`, `Qty` and `Notional` abstract and mutually incompatible, so
`price + qty` is a compile error rather than a plausible-looking number, and
Async gives the feed, the HTTP server and the staleness clock one scheduler
instead of two runtimes to reconcile.

One rule makes the whole thing work, and it is stated at the top of
[`lib/graph.ml`](lib/graph.ml):

> A node may only read its declared inputs. Reaching outside the graph for a
> value — a global, a mutable ref, a fresh API call — makes that dependency
> invisible to Incremental, which will then happily serve a stale answer because
> it has no idea anything changed. Every dependency must be an edge.

## The graph

```
  price[S] ---+
              +--> exposure[S] --+--> sector[K] --+
  qty[S]   ---+                  |                |
                                 +----------------+--> exposure_map
                                 |                +--> gross ---+---> weights
                                 |                +--> net --+  |
                                 |                           |  |
  cash --------------------------|---------------------------+--|--> equity
                                 |                              |     |
  equity_history ----------------|------------------------------|-----+--> drawdown
                                 |                              |
  returns[S] --> aligned_returns +--> covariance                |
                         |                     \               /
                         +----------------------+-> portfolio_returns
                                                 \      |
                                                  \     +--> historical_var --> var_notional
                                                   \    +--> expected_shortfall --> es_notional
                                                    +------> parametric_var
                                                         |
  factor_returns ----------------------------------------+--> portfolio_beta
                                                                     |
  (each limit reads exactly one of the nodes above) --> limit[name] -+--> breaches

  -- and, deliberately disconnected from everything above --

  last_tick[S] --+
                 +--> feed[S] --> feed_health
  now -----------+
```

Three edges in that picture are the argument for the architecture.

`covariance` hangs off `aligned_returns` and nothing else. It is the most
expensive thing this engine computes, and a price tick does not reach it. In a
poll-and-recompute design it would be rebuilt on every pass, for nothing.

Each limit is its own node hanging off the single quantity it measures, so an
instrument-scoped limit on AAPL is downstream of `exposure[AAPL]` alone and a tick
in an unrelated name leaves it strictly untouched.

The feed-health branch is a dead end on purpose. `now` is advanced by a timer, and
if any risk node were downstream of it that timer would recompute the book on a
schedule — which is the design this project exists to replace. Staleness is
time-dependent and has to be; the discipline is that nothing else may be. Note the
asymmetry: `feed[S]` depends on `last_tick[S]` and never on `price[S]`, so the two
branches share an event but not an edge.

`test/test_graph.ml` asserts all three as recomputation counts, which makes the
architectural premise an executable claim rather than a comment.

## The modules

[`lib/graph.ml`](lib/graph.ml) is the engine: every input cell, every derived
node, and a comment on each one explaining *why* it depends on what it depends on.
[`lib/risk_metrics.ml`](lib/risk_metrics.ml) holds the numerics as ordinary
functions — historical and parametric VaR, expected shortfall, covariance, beta,
portfolio standard deviation through Owl and BLAS — none of which know Incremental
exists, so each can be unit-tested against a hand-computed value.
[`lib/limits.ml`](lib/limits.ml) defines limits and evaluates a breach as *data*: a
bool and the magnitude by which the threshold was passed. It has no side effects
at all, which is the point — a node body may be recomputed whenever the runtime
likes, so anything that sends a message has to live outside one.

That outside is [`lib/alerts.ml`](lib/alerts.ml), which hangs off an observer
rather than a node and is the only module here that can reach the world.
[`lib/server.ml`](lib/server.ml) serves `/api/snapshot`, `/api/health` and an SSE
stream at `/api/stream`; [`lib/dashboard_html.ml`](lib/dashboard_html.ml) is the
page itself, embedded as a string so the binary is self-contained. The feed lives
in [`lib/feed/`](lib/feed) — the Alpaca websocket, an Alpaca REST backfill for
history, and a FRED client for the factor series — and is folded into the library
as top-level modules by `include_subdirs unqualified` in [`lib/dune`](lib/dune),
because it is not a separable component. Its entire job is to write into input
cells.

The stream is not a timer either. The broadcaster parks on an `Ivar` that
`Graph.on_change` fills, so when nothing in the book moves, not one byte is
serialized. There is an 80ms delay in that loop and it is worth being precise
about it: it runs *after* a change has already been observed, to coalesce a burst
of ticks into a single frame, and it never causes a wakeup. A timer asks "has
anything changed?"; this asks "how many more changes arrive in the next 80ms?"

## Running it

Start with the demo. It needs no credentials, touches no network, and works when
the market is closed.

```
$ make demo

  OhCamel -- reactive risk and limits engine
  Phase 3: DEMO (synthetic feed, no credentials)

  dashboard   http://localhost:8080
  book        6 instruments, 7 limits
  ticking     one name every 400ms, a bar every 15s
  quiet       CVX is never ticked, so the stale path is visible
  alerts      on, logging to this terminal. Kill switch armed on nvda-cap --
              it sets a flag and nothing else. Nothing here places orders.
```

![make demo](docs/media/demo.png)

Two things in that book are rigged, and both are rigged so that the interesting
behaviour is visible instead of theoretical. CVX is never ticked, so about twenty
seconds in it goes stale — and the dashboard responds by desaturating CVX, the
ENERGY sector and everything computed downstream of them, while the other five
instruments stay at full strength because their exposures are still exactly right.
The dimming is scoped by dependency, not by page. That is the same discipline the
graph applies internally, expressed in CSS, and it exists because a limit reading
"not breached" off a twenty-minute-old mark is not information and should not look
like information.

The other rig is `nvda-cap`, set at $54,200 against a starting exposure of
$54,000, so the first meaningful move crosses it and the whole Phase 4 path —
edge-triggered alert, hysteresis on the way back down, the kill switch latching —
happens within a few seconds of startup rather than never.

`make run` is the mode that printed the table above: a generated feed, sixty
events, the whole book after each one, a breach and its recovery in the middle,
and then it exits — without ever starting the Async scheduler. It is the fastest
way to see the numbers without a browser. `make test` runs the suite; the next
section says what it establishes.

**The rest of this section needs API keys.** `make run-live` takes real Alpaca
market data and the FRED factor series; `make serve` does the same with the
dashboard on `http://localhost:8080`. Both read positions from `book.sexp` — copy
[`book.example.sexp`](book.example.sexp) and edit it — and both expect
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `FRED_API_KEY` in the environment:

```
$ set -a; source /path/to/.env; set +a
$ make serve
```

A missing key is fatal by design. The engine refuses to start rather than
degrading into something that looks live and is showing made-up numbers. Note also
that a free Alpaca plan allows **one concurrent market-data stream per account**;
if anything else is using the same keys, this gets a 406 and stops.

The dashboard is read-only and unauthenticated, and should be bound to localhost.
There is nothing to authorise because no route mutates anything, and nothing in
this codebase sends a message or takes an action on its own.

## What's verified

`make test` runs 94 tests, all hermetic — no network, no credentials, and nothing
that waits on the wall clock. They cover the numerics against hand-computed
values, the wire format, the alerting state machine, and the recomputation counts
that make the graph's shape an assertion rather than a claim.

The live path has been run against real Alpaca market data and real FRED series,
which is how the most instructive bug in the project was found. Live mode set
quantities from the book file but took prices *only* from the tick stream, so any
symbol that had not printed yet sat at its initial zero — and a position marked at
zero contributes zero to exposure. After the close, gross read **$217,590 against
a true $459,266**, with nothing on the page suggesting the number was wrong. That
is a worse failure than staleness and a different kind: a stale price is a real
price from earlier, a zero is a price that never existed.

The fix marks the book from the last close during the REST backfill that was
already being fetched, through `Graph.set_price` and deliberately *not*
`Graph.apply_tick` — a closing price is a real mark, but it is not evidence that
the feed is alive, so feed health goes on correctly reporting those symbols as
never-seen. That distinction is exactly why the two setters are separate. No unit
test would have caught this. Looking at the thing did.

## What happens when a limit breaks

A breach is computed as data and displayed. Everything beyond that is off unless
someone writes down that they want it: `Config.Alerts.default` has
`enabled = false`, and the kill switch is a second, separate flag, because "tell
me when a limit breaks" and "act when a limit breaks" are different levels of
trust. When alerting is on it is edge-triggered with hysteresis, so a value
oscillating across a threshold does not produce a stream of pages, and lost data
never clears an alert. The sending happens in an Async consumer downstream of an
observer, never inside a node body, so a slow webhook cannot stall the graph.

The kill switch sets a flag and is wired to nothing. There is no order-placement
code anywhere in this repository, and `lib/alerts.ml` does not import the Alpaca
client, so it could not reach a trading endpoint by mistake. What
`Kill_switch.halt_new_orders` returns is a bool for a human or a future execution
layer to read. This is not an unfinished feature. A risk system that can flatten a
book by itself is a different and far more dangerous project than this one, and
the flag is the seam where that decision would get made deliberately, by someone
who meant it.

## Building it

The opam switch is project-local, in `./_opam`, so the compiler and every
dependency live inside this directory and are not on your `PATH`. Every target in
the [`Makefile`](Makefile) re-enters the switch itself, so `make test` works from a
clean shell with no setup; if you would rather use dune directly, run
`eval $(opam env)` once in the repo first.

```
$ make deps     # install dependencies from dune-project into ./_opam
$ make doctor   # print what is actually installed
```

`make doctor` exists because on macOS a build failure here is usually not your
code — it is Owl. Two separate Owl problems on arm64 macOS are worked around by
`OWL_CFLAGS` and `OWL_LDLIBS` at the top of the [`Makefile`](Makefile): a clang
segfault at any optimisation level above `-O1`, and an OpenMP runtime that Owl
compiles against but never links. Both are written up there in full, with the
bisection that found them and the cost of each workaround, which is a better place
for that detail than here. OCaml 5.1.0 or newer is required; this switch is on
5.2.1.

## What this is not

There is no order routing and no execution — nothing here places, cancels or
simulates a trade. There is no persistence: state lives in the running process,
and a restart rebuilds the book from `book.sexp` and the feed. There is one
broker, Alpaca, and one macro source, FRED. It is a risk and limits engine, and it
stops where a risk and limits engine should stop.

## Origin

This was built from a written brief, which is archived with provenance at
[`docs/brief.md`](docs/brief.md).
