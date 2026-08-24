# OhCamel

[![ci](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml/badge.svg)](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml)

A reactive risk and limits engine. Positions and market data go in; per-instrument
and per-sector exposure, gross and net, VaR, expected shortfall, portfolio beta,
drawdown and limit breaches come out — and keep coming out, updated as the market
moves rather than as a clock ticks.

It also answers the three questions a risk number invites and usually does not
get asked. **Where** is the risk: an exact Euler decomposition of portfolio VaR
across instruments and sectors, so a limit can be written against one name's
*share* of the total. **Is the number any good**: Kupiec coverage, Christoffersen
independence and the Basel traffic light, run over rolling point-in-time
forecasts. And **what would break it**: a scenario suite that shocks prices,
sectors, a macro factor or volatility itself and reports which limits move
across their line.

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
          10               56               24.6               56
         100              335               24.2              335
         400             1265               25.0             1265
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

  -- the decomposition, which reads the same two inputs as parametric_var --

  weights ------+
                +--> attribution --+--> component_var[S] --+--> component_var[K]
  covariance ---+                  +--> diversification_ratio

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

## Where the risk is

A portfolio VaR of $12,000 is a fact about the book and not an instruction. It
does not say what to sell, and the obvious way of finding out — sort the
positions by size — answers a different question. Money and risk are not the
same distribution.

Portfolio volatility is homogeneous of degree one in the weights, so Euler's
theorem splits it exactly:

```
sigma_p = sum_i w_i * d(sigma_p)/d(w_i)
```

No approximation, no residual. Each term is one instrument's *contribution*, and
because the split is exact the terms sum over any partition of the book — a
sector, a strategy, a desk — and the parts still add to the whole. Parametric VaR
is a constant multiple of `sigma_p`, so the same split carries into VaR units and
the component VaRs sum to the portfolio's. `make run` prints it:

```
  symbol       of money  component VaR      of risk risk/money
  --------------------------------------------------------------
  AAPL            18.5%           $429        18.8%      1.02x
  CVX             18.5%           $619        27.1%      1.47x
  JPM             10.3%            $88         3.9%      0.38x
  MSFT            20.7%           $337        14.8%      0.71x
  NVDA            17.9%           $522        22.9%      1.28x
  XOM             14.0%           $285        12.5%      0.90x
  --------------------------------------------------------------
  total          100.0%         $2,281       100.0%
  ENERGY                          $904        39.6%
  FINANCIALS                       $88         3.9%
  TECH                          $1,288        56.5%
```

CVX and AAPL are the same size and CVX carries half as much risk again. JPM is a
tenth of the money and a twenty-fifth of the risk. Nothing in the exposure table
says either of those things.

This is what makes an instrument-scoped VaR limit well posed, and it retires a
restriction the code used to state and enforce. `Limits.scope_is_valid` still
rejects a `Value_at_risk` limit on one name, and correctly: two names each with
$10,000 of standalone VaR do not carry $20,000 together unless they are perfectly
correlated, so summing standalone quantiles over-counts the book's risk by
exactly the diversification between its parts. A `Component_var` limit is
accepted at every scope, because the shares are additive by construction. You
*can* put a VaR limit on one name — it has to be the share, not a standalone
quantile, and the two are different numbers.

That limit behaves differently from a notional cap in a way worth internalising
before writing one: it is correlation-aware, so a name's number moves when
*other* positions move. Adding a hedge can bring a name back inside its component
limit without trading that name at all. And a contribution can be **negative** —
a position that moves against the book reduces portfolio volatility — so a hedge
consumes none of its risk limit however large it is. `test_graph.ml` asserts that
directly, because a stray `abs` anywhere in the chain would breach a risk limit
for the act of hedging.

Two honest limits. The decomposition is the *Gaussian* one: it needs a
differentiable closed form for portfolio risk, and only the covariance path has
one. Historical VaR is an order statistic of the sample — its derivative with
respect to a weight is zero almost everywhere — and the kernel-smoothed
alternatives are too noisy at these sample sizes to be worth trading against. So
this says how risk is *shared out*, which is a question about correlation
structure and is fairly robust, rather than how large the tail *is*, which is
what normality gets wrong. Read it beside the historical number, not instead of
it. And this branch *is* downstream of price — weights move on every tick — so
unlike `covariance` it recomputes constantly. What it costs is a matrix-vector
product against the matrix rebuild a polling design would do.

Alongside it, the diversification ratio: standalone risks summed, over portfolio
risk, so at least 1.0. The synthetic book runs about 2.5, meaning it carries
around 40% of the volatility its positions would if they all moved together. It
is the number that collapses toward 1.0 in a crisis, because correlations going
to one is what a crisis mechanically *is*.

## Is the number any good

"95% VaR" is a testable claim with precise content: the realised loss should
exceed it on about 5% of days, and those exceedances should be scattered rather
than bunched. An engine that reports the number and never checks the claim is
asserting something it has no evidence for, and the failure is silent — an
uncalibrated VaR looks exactly like a calibrated one until the day it matters.

`make backtest` is the check.

![make backtest](docs/media/backtest.png)

Three statistics, because a model can fail in two independent ways and one test
cannot tell them apart. **Kupiec**'s proportion-of-failures test asks whether
there are the right *number* of exceedances, and it is two-sided — too few is a
rejection too, because a VaR that is never breached is not measuring the quantile
it claims to and every limit written against it is slack by an unknown amount.
**Christoffersen**'s test asks whether the exceedances are *independent*: a model
can be breached exactly 5% of the time and still be useless if they all land in
one week, which is the signature of a model that is not tracking volatility.
**Conditional coverage** is the joint test, reported next to its components
rather than instead of them, because a joint rejection says the model is wrong
and the parts say which half.

The whole analysis turns on one discipline, and it is enforced structurally
rather than by care. `Var_backtest.rolling` hands the estimator
`returns[t-w .. t-1]` and scores it against `returns[t]` — the day being
forecast is not in the array the estimator receives, so it cannot be reached. A
VaR estimated from a window that includes the day it is predicting looks superb
and means nothing, and the output gives no sign. `test_var_backtest.ml` asserts
the slicing directly against independently-built windows.

Two rows of that table are worth reading rather than skimming.

The `jumps`/historical row reports **zero exceptions in 940 days**, a Kupiec
p-value that rounds to zero, and a **Basel zone of green**. That is not an
inconsistency. Basel's traffic light is one-sided by design — it asks whether a
bank is *understating* risk, because that is the direction that threatens
solvency — while a coverage test is two-sided. A model can be comprehensively
wrong and still be green. The zone is a supervisor's tolerance, not a verdict.
(The zones are computed from the binomial rather than looked up, and reproduce
the published 250-day table exactly: green 0–4, yellow 5–9, red 10+. That is
asserted as a test.)

And `iid-normal`/parametric usually shows an independence p-value near or below
5%, on data that is independent by construction — a 5% test doing what a 5% test
does one time in twenty. It is the argument for gating on the joint statistic
rather than on whichever component looks worst, which is multiple testing wearing
a lab coat.

The suite rejects two of six configurations. That is the point. A validation
battery that has never failed anything is not evidence of anything.

## What would break it

Every other number here is backward-looking by construction. VaR summarises a
distribution that has already been observed; a limit compares today's exposure to
a line. Both share a blind spot that is not a flaw in the estimator but a
property of the question — they can only speak about moves that have already
happened somewhere in the return window.

A scenario asks the other question. `make stress`:

![make stress](docs/media/stress.png)

Four of the five shock kinds move prices, and those compose additively, so a
scenario reads as a sentence: a market move, plus a sector move, plus an
idiosyncratic one. The fifth, `Volatility`, scales the return window instead and
therefore multiplies. `Factor` is the one that
earns its keep — the macro factor moves, and each name responds through *its own*
beta to it, estimated from the return windows the engine already holds. A rate
shock does not hit every name equally, which is the entire reason to express it
as a factor move rather than a price move. Names whose beta cannot be estimated
do not move, and are *named in the output*: "this name did not move" and "this
name could not be moved" look identical in a P&L table and mean opposite things.

The two rows that carry the argument are `vol-regime` and `crash`. `vol-regime`
shocks nothing but volatility: P&L is exactly zero, gross is unchanged, every
notional cap sits exactly where it did — and it breaks a component-VaR limit.
`crash` moves every price 20% and breaks the drawdown limit and nothing else.

A price shock moves what the book is *worth*; a volatility shock moves what it is
*expected to do*. Keeping the two
separate is what stops a scenario from quietly answering a question nobody asked
— and it is why shocking prices deliberately leaves the VaR *fraction* alone. A
hypothetical move today is not in the return window and is not evidence about the
distribution. The dollar VaR does move, because gross did.

There is no scenario arithmetic anywhere in this repository, and that is the
design decision in this module. The obvious implementation multiplies positions
by shocked prices and re-checks the limits — a second implementation of exposure,
equity, drawdown and every limit rule, living next to the first and drifting from
it the first time someone changes a convention in one and not the other. Instead
`Graph.fork` copies the engine, the shocks are written into the fork's input
cells, and the answer is read out by exactly the nodes that produce the live one.
The scenario is not a model of the engine; it is the engine, fed different
inputs. `test_stress.ml` runs the entire suite and then asserts the live
snapshot is unchanged field for field — a fork that shared an input cell with its
parent would produce numbers that were still internally consistent and were about
a world that never happened.

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

[`lib/attribution.ml`](lib/attribution.ml) is the Euler decomposition — marginal,
component and standalone risk, and the residual check that would catch the
weights and the covariance matrix going out of alignment, which is this module's
one failure mode that produces confident, plausible, entirely wrong answers.
[`lib/var_backtest.ml`](lib/var_backtest.ml) holds the coverage battery and the
rolling-origin forecast generator that keeps it point-in-time; it is offline by
construction and is deliberately *not* wired into the graph, because a
calibration test needs hundreds of observations and answers a question about the
model rather than about the book. [`lib/stress.ml`](lib/stress.ml) is the
scenario suite, and it contains no arithmetic at all — it resolves a scenario
into a set of input-cell writes and hands them to a fork of the engine.

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
  DEMO (synthetic feed, no credentials, no network)

  dashboard   http://localhost:8080
  book        6 instruments, 9 limits (2 of them on risk SHARE, not notional)
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

`make run` is the mode that printed the tables above: a generated feed, sixty
events, the whole book after each one, a breach and its recovery in the middle,
the risk decomposition at the end, and then it exits — without ever starting the
Async scheduler. It is the fastest way to see the numbers without a browser.

`make stress` and `make backtest` are the other two credential-free modes, and
they are what the previous three sections are about: the scenario suite against
the synthetic book, and the coverage battery against three deterministic return
series. Both are hermetic and both are deterministic, so two runs print the same
numbers and a change in the output means a change in the engine.

`make test` runs the suite; a later section says what it establishes.

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

`make test` runs 140 tests, all hermetic — no network, no credentials, and nothing
that waits on the wall clock. They cover the numerics against hand-computed
values, the wire format, the alerting state machine, and the recomputation counts
that make the graph's shape an assertion rather than a claim.

Every expected value is derived by hand with the derivation written beside it,
which the newer modules make easy in a way worth noting: the standard test book
holds three names whose return series are the same series and its negation, so
every pair is perfectly correlated in magnitude and the risk shares come out as
the weight magnitudes exactly — `[0.3, 0.3, 0.4]`, readable without arithmetic.
A perfectly correlated book is one bet, so each position's share of the risk is
just its share of the money, and that is the ceiling any real book sits below.

Four of those tests are the ones that would catch a defect nothing else would.
The **Euler residual** holds the decomposition to its own identity. The
**hedge test** asserts a risk-reducing position does not breach a risk limit,
which is what a stray absolute value would break. The **lookahead test** rebuilds
each rolling window independently and demands the forecast match, so a backtest
cannot see the day it is forecasting. And the **isolation test** runs the whole
scenario suite and then compares the live snapshot field for field, because a
leaking fork produces numbers that are internally consistent and about the wrong
world.

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

There are four kinds of limit and they are not four settings of one thing.
`Gross_notional` caps exposure at any scope. `Value_at_risk` and `Max_drawdown`
are portfolio-only, because neither decomposes. `Component_var` caps a scope's
*share* of portfolio VaR and is valid everywhere, because that share is additive
— and at portfolio scope it measures the parametric total, deliberately a
different estimator from `Value_at_risk`'s historical one. Writing both is not
redundant: the two disagreeing is the tail-fatness diagnostic, and having a limit
on each says which estimator you are willing to be wrong about.

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
broker, Alpaca, and one macro source, FRED.

Nor is it a research platform. There is no strategy, no signal, no backtest of
anything that could make money — `make backtest` validates the *risk model*, not
a trading idea, and the distinction is the whole point of the mode. The
volatility estimator is equal-weighted, which the `vol-regime` row of that same
output shows is its weakest assumption; EWMA or GARCH would track a regime change
faster and neither is here. Nothing is optimised: the engine reports where risk
is concentrated and never suggests what the weights should be, which is a
different project with a different failure mode.

It is a risk and limits engine, and it stops where a risk and limits engine
should stop.

## Origin

This was built from a written brief, which is archived with provenance at
[`docs/brief.md`](docs/brief.md).
