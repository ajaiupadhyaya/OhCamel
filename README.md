# OhCamel

[![ci](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml/badge.svg)](https://github.com/ajaiupadhyaya/OhCamel/actions/workflows/ci.yml)
[![coverage 79%](https://img.shields.io/badge/coverage-79%25-brightgreen)](#coverage-and-what-it-is-not-measuring)

**Live:** [ohcamel.ajaiupadhyaya.com](https://ohcamel.ajaiupadhyaya.com) — the
synthetic demo, no credentials, always on. [How it is deployed](#watching-it).

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

The math is written out once, densely and in standard notation, in
[`docs/quant_notes.md`](docs/quant_notes.md) — every formula cross-referenced to
the function that evaluates it, so a claim here can be checked against the code
without reading OCaml. This README argues; that document states. A short
summary of what it does, without the argument, is
[`docs/overview.md`](docs/overview.md).

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
          10               63               25.6               63
         100              342               25.2              342
         400             1272               26.0             1272
```

The middle column is flat and the right one is not. The cost of an event is set
by what the event touches, not by how large the book is.

### What that costs in seconds

A node count is the right proof of the *design* and it is not a proof of
anything a latency-conscious reader cares about — it says nothing about whether
a node costs a nanosecond or a millisecond. `make bench` measures the two
numbers that do, against a throwaway poll-and-recompute baseline implemented in
[`bench/bench_graph.ml`](bench/bench_graph.ml) using the *same* pure functions
the graph does, so the difference is incrementality and not one side having a
slower covariance routine.

```
  Name                 Time/Run       mWd/Run    mjWd/Run   Percentage
 ----------------- ------------- ------------- ----------- ------------
  incremental/10        19.5us         9.08kw      27.3w         0.04%
  incremental/100       89.1us        44.52kw     603.2w         0.17%
  incremental/400      492.7us       180.69kw   9_212.2w         0.92%
  polled/10             51.7us        47.94kw       9.8w         0.10%
  polled/100         3_387.7us      3_756.06kw    325.8w         6.34%
  polled/400        53_426.8us     59_345.84kw  3_948.8w       100.00%
```

Apple M2 Pro, macOS 26.5, OCaml 5.2.1, Owl's C kernels at `-O1` because of the
clang bug the Makefile documents. Run-to-run variance is 3–5% on time and
essentially zero on allocation. Numbers from your machine will differ; the
ratios should not.

At 400 names a full recompute costs **53 milliseconds**, which is not a slow
number so much as a disqualifying one: it caps the engine at about nineteen
events a second before it falls behind the market it is supposed to be watching.
The incremental path is **108× faster** and allocates **330× fewer words**.

And now the honest part, which the node-count table above hides. **The
incremental engine's cost per tick is not flat in wall-clock** — 19.5µs, 89µs,
493µs across the three sizes — even though the node count is (25.6, 25.2, 26.0).
Both are true and the second does not imply the first. A tick reaches about
twenty-five nodes at every book size, but *those* nodes are not all O(1): the
weights are O(n), the portfolio return series is O(n·w), and the Euler
decomposition is a matrix-vector product at O(n²). What incrementality buys is
that the covariance matrix — O(n²·w), the single most expensive thing here — is
not among them. The polling baseline scales as n² because it rebuilds that
matrix on every event; this one scales as the *cheap* part of n², which is why
the gap widens from 2.7× to 108× as the book grows rather than staying constant.

CI does not run this. Benchmark numbers from a shared runner are noise wearing a
lab coat, so `make bench` is a local command and the table above is a quoted run
rather than a gate.

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
                         |       +--> covariance_ewma          |
                         |                      \             /
                         +----------------------+-> portfolio_returns
                                                 \      |
                                                  \     +--> historical_var --> var_notional
                                                   \    +--> expected_shortfall --> es_notional
                                                    +------> parametric_var
                                                    +------> parametric_var_ewma
                                                         |
  factor_returns ----------------------------------------+--> portfolio_beta
                                                                     |
  (each limit reads exactly one of the nodes above) --> limit[name] -+--> breaches

  -- the decomposition, which reads the same two inputs as parametric_var --

  weights ------+
                +--> attribution --+--> component_var[S] --+--> component_var[K]
  covariance ---+                  +--> diversification_ratio

  -- options: delta folds INTO exposure[S] above; convexity cannot --

  contracts[o] --+
  implied_vol[o] +--> greeks[o] --+--> delta_equivalent[o] --> (exposure[S])
  price[S] ------+                +--> gamma[S] --> portfolio_gamma
  valuation_days +                +--> vega[S]  --> portfolio_vega
  rate ----------+

  -- and, deliberately disconnected from everything above --

  last_tick[S] --+
                 +--> feed[S] --> feed_health
  now -----------+
```

The two `covariance` rows are siblings: the same matrix under flat and under
exponentially decaying weights, hanging off the same edge, each feeding its own
parametric VaR. They are stacked rather than drawn separately because their
dependency structure is identical, which is the whole reason the comparison
between them means something.

Three edges in that picture are the argument for the architecture.

`covariance` hangs off `aligned_returns` and nothing else. It is the most
expensive thing this engine computes, and a price tick does not reach it. In a
poll-and-recompute design it would be rebuilt on every pass, for nothing.
`covariance_ewma` is its sibling on the same edge, so the engine now computes
that most expensive thing *twice* — and the table above prices that decision
exactly: **two more nodes in the graph, and one more node per tick**, at every
book size. Not two more per tick. Neither matrix is downstream of price, so what
a tick pays for is a second matrix-vector product, never a second matrix rebuild.
An estimator costs what its inputs cost, and its inputs move once a day.

Each limit is its own node hanging off the single quantity it measures, so an
instrument-scoped limit on AAPL is downstream of `exposure[AAPL]` alone and a tick
in an unrelated name leaves it strictly untouched.

The options branch has a clock of its own, and it is a *different* clock. `now`
below drives feed staleness and ticks every few seconds; `valuation_days` drives
theta and moves only when a caller advances it. Two cells because there are two
questions, ticking three orders of magnitude apart — see
[Where the risk is when it isn't linear](#where-the-risk-is-when-it-isnt-linear).

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

## Where the risk is when it isn't linear

Everything above measures risk in one dimension. An equity position's exposure
is price times quantity, its P&L moves linearly with the price, and a covariance
matrix over returns says most of what there is to say. None of that survives
contact with an option. A position can be flat in the underlying and still lose
money on a move in either direction, or lose money on no move at all, or lose
money because the market changed its mind about how much the underlying *will*
move without the underlying moving.

`make options` is that argument as a program. Short 50 NVDA 950-strike calls,
30 days out, spot $900:

```
                      delta-equiv          gamma    vega / vol pt
  ----------------------------------------------------------------
  options only        $-1,204,067          -23.7          $-4,246
  + the hedge                  $0          -23.7          $-4,246
  ----------------------------------------------------------------
```

The hedge is 1,338 shares and the engine computed the ratio — it is the option
leg's delta-equivalent exposure over the spot. Delta goes to exactly zero.
Gamma and vega do not move at all, because a share is linear in its own price
and contributes precisely none of either. That is the entire content of the
phrase *first-order hedge*, and it is why the limits then read:

```
  BREACH  141.5%  nvda-vega       $424,637 > $300,000
  ok        5.9%  nvda-gamma      $23.72 <= $400.00
  ok        0.0%  nvda-notional   $0.00 <= $1,000,000.00
```

The notional cap is clear because the book *is* delta flat. The vega cap is not,
because it never was. An engine that measured only exposure would report this
book as carrying no risk at all.

### Delta folds in; convexity cannot

An option's **delta-equivalent exposure** — `delta × multiplier × contracts ×
spot` — is added into the *same* `exposure[S]` node the shares produce. Not
alongside it, into it. Gross, net, the sector totals, the weights, equity,
drawdown and every `Gross_notional` limit at every scope are already functions
of that node, so all of them account for options without a line of change and,
far more importantly, without a second definition of what "exposure" means. A
parallel options-exposure system would be exactly the duplication `stress.ml`
goes to some length to avoid having.

Gamma and vega deliberately do **not** fold in anywhere. Delta-equivalent
exposure is a first-order statement — "this behaves like N dollars of the
underlying for a small move" — and gamma is precisely the statement that it
stops being true for a large one. There is no honest way to express convexity as
a quantity of underlying, so they get their own nodes rather than a fabricated
column in a linear sum.

`Contracts.t` is a distinct type from `Qty.t`, which is the units discipline
earning its keep at the one place it bites hardest: one contract is a hundred
shares, so a book holding "50" of something holds either 50 shares of delta or
5,000 depending on which type that 50 is, and both readings produce a plausible
exposure. The multiplier has to be applied explicitly, in one function.

### Two clocks, and why they are not one

Theta is genuinely time-dependent, which is a problem, because the one thing
this architecture cannot have is a risk node downstream of a running clock. The
staleness branch is a dead end on purpose — `now` advances every few seconds,
and anything hanging off it would recompute the book on a timer.

So options get a **second clock**: a valuation-date cell that moves in days and
only when a caller advances it. Two clocks because there are two questions, and
they tick three orders of magnitude apart — a contract's value changes
immeasurably over five seconds and materially overnight. Wiring the Greeks to
the staleness timer would have bought a decay invisible below a day at the cost
of putting the entire options book on a five-second schedule.
`test_options_graph.ml` asserts both directions: advancing the staleness clock
recomputes no Greek, and advancing the valuation clock touches nothing about
feed liveness.

Advancing it twenty days shows something worth staring at:

```
                      delta-equiv          gamma    vega / vol pt
  ----------------------------------------------------------------
  today                        $0          -23.7          $-4,246
  +20 days               $657,690          -25.2          $-1,502
  ----------------------------------------------------------------
```

Nothing was traded. The share count is identical. The book is no longer delta
flat, because the contract decayed further out of the money, its delta fell, and
the hedge that offset it exactly now over-hedges by $657,690. A delta hedge is
correct at an instant and stale immediately afterwards — which is why the number
hangs off an edge instead of being stored.

### Greek limits, and one caveat that the arithmetic hides

`Greek_limit` caps `|gamma|` or `|vega|` and is valid at **every** scope, for a
reason worth separating from `Component_var`'s. Gamma and vega are sums of
derivatives, not quantiles: the book's gamma is the derivative of a sum, which
*is* the sum of the derivatives, exactly and by linearity, with no correlation
term and no diversification to double-count. That is precisely what fails for
`Value_at_risk` — a quantile of a sum is not the sum of quantiles.

Note the sign conventions differ, and each is argued where it is defined. A
Greek limit takes the **magnitude**: a short option book has negative gamma, and
being short convexity is the dangerous side, not the safe one. `Component_var`
keeps the **sign**: a negative contribution there means a position is reducing
portfolio risk, so absolute-valuing it would breach a limit for hedging.

The caveat is real, and rather than describe it the engine now shows it.

Summing vega across contracts at *different expiries* adds sensitivities to
different volatilities — the 25-day implied and the 180-day implied move
together but not identically — so a single portfolio vega treats the whole term
structure as one number shifting in parallel. Every desk does this and calls it
parallel-shift vega. The case it gets badly wrong is the **calendar spread**,
and `make options` builds one:

```
  long     50 NVDA 950 calls, 180 days out
  short   170 NVDA 950 calls, 25 days out

  portfolio vega                      $0   <- the parallel-shift number
  portfolio gamma                  -72.5

  by tenor bucket:
    1w-1m                       $-12,559
    3-6m                         $12,559
```

The total is zero and the book is not flat. It is short near-dated volatility
and long far-dated volatility in equal parallel-shift size — a bet that the term
structure steepens, with real P&L, which one vega number reports as nothing at
all. The gamma of −72.5 is a second exposure the same number is silent about.

Bucketing does not make the sum exact: vega inside a bucket is still added
across the expiries within it. What it does is make the thing being approximated
visible, which is the difference between an approximation and a blind spot. The
buckets are cut on days *remaining*, so a position slides from one to the next
as the valuation clock advances with nothing traded — which is why `graph.ml`
hangs the bucketing off that clock rather than assigning a bucket once at
construction, a choice that would look correct for about a month.
`test_options_graph.ml` walks a contract from 3-6m to 1-3m to ≤1w to expired by
advancing the clock alone.

### What the options path does not do

No implied-volatility solve. Inverting Black-Scholes for sigma needs a market
price to invert *from*, and there is no options-chain source here, so it would
be the second half of a bridge to nowhere. Implied vol is an input.

No American exercise, no dividends, no term structure of rates — Black-Scholes
with one flat rate and one vol per contract. Each is a real simplification and
each is named rather than left to be discovered. Vega is bucketed by tenor but
not by strike, so a book long the wings and short the body reads flat within a
bucket the way a calendar spread used to read flat across them; a skew
decomposition is the same idea one axis over, and is not here.

And **live mode ships options risk disabled**, with a line saying so. Greeks need
an implied vol per contract, Alpaca's free tier does not provide an options
chain, and the alternatives were to decline or to invent a surface. An invented
surface would produce a full set of Greeks, a portfolio vega and a vega limit
that all looked exactly like the real thing — the same failure as a book marked
at a plausible default price, and worse than no number because nothing on the
page would say it was fiction. `make options` runs the whole path against a
smiled surface that is labelled synthetic in every line it appears in.

## Is the number any good

"95% VaR" is a testable claim with precise content: the realised loss should
exceed it on about 5% of days, and those exceedances should be scattered rather
than bunched. An engine that reports the number and never checks the claim is
asserting something it has no evidence for, and the failure is silent — an
uncalibrated VaR looks exactly like a calibrated one until the day it matters.

`make backtest` is the check. Three deterministic return series, three
estimators, nine verdicts:

```
 series       estimator         n  excepts  expected  Kupiec p   indep p   joint p      duration p  Basel   verdict
 ---------------------------------------------------------------------------------------------------------------------
 iid-normal   historical      940       45      47.0    0.7631    0.3595    0.6280  0.6291 b= 0.95  green   ok
 iid-normal   parametric      940       48      47.0    0.8814    0.0229    0.0744  0.8975 b= 1.01  green   ok
 iid-normal   ewma(0.94)      940       54      47.0    0.3057    0.0102    0.0219  0.0577 b= 1.24  green   REJECTED
 vol-regime   historical      940       52      47.0    0.4616    0.9405    0.7605  0.9438 b= 0.99  green   ok
 vol-regime   parametric      940       65      47.0    0.0107    0.8028    0.0373  0.9592 b= 1.00  yellow  REJECTED
 vol-regime   ewma(0.94)      940       59      47.0    0.0836    0.6864    0.2063  0.2446 b= 1.13  yellow  ok
 jumps        historical      940        0      47.0    0.0000    1.0000    0.0000        --        green   REJECTED
 jumps        parametric      940       47      47.0    1.0000    0.0277    0.0886  0.0000 b=20.00  green   ok
 jumps        ewma(0.94)      940       47      47.0    1.0000    0.0277    0.0886  0.0000 b=20.00  green   ok
```

**Four** statistics, because a model can fail in more ways than one test can
tell apart — and the fourth was added because the first three were caught
missing something. Start with the three. **Kupiec**'s proportion-of-failures test asks whether
there are the right *number* of exceedances, and it is two-sided — too few is a
rejection too, because a VaR that is never breached is not measuring the quantile
it claims to and every limit written against it is slack by an unknown amount.
**Christoffersen**'s test asks whether the exceedances are *independent*: a model
can be breached exactly 5% of the time and still be useless if they all land in
one week, which is the signature of a model that is not tracking volatility.
**Conditional coverage** is the joint test, reported next to its components
rather than instead of them, because a joint rejection says the model is wrong
and the parts say which half.

And then **duration** — Christoffersen and Pelletier's test, which asks the
independence question a different way and catches what the Markov one cannot.
Under correct conditional coverage the *waiting times between* exceedances are
memoryless: how long you have waited says nothing about how much longer you
will. So fit a Weibull to those durations, whose shape parameter $b$ collapses
to the exponential exactly at $b = 1$, and test the restriction. $b < 1$ means a
breach makes the next one arrive sooner than chance — clustering. $b > 1$ means
breaches are *more regular* than chance. Because it works on durations rather
than on adjacency, a burst landing every third day is as visible as one landing
on consecutive days.

It is reported beside the joint verdict and deliberately **not folded into it**.
Conditional coverage is Kupiec plus the *first-order* independence test, and its
two degrees of freedom follow from exactly those two pieces; adding a third
statistic to that sum would produce something with no distribution anyone has
derived, and would silently change the meaning of every verdict this suite has
already published.

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

And `iid-normal`/parametric shows an independence p-value of 0.023, on data that
is independent by construction — a 5% test doing what a 5% test does one time in
twenty. It is the argument for gating on the joint statistic rather than on
whichever component looks worst, which is multiple testing wearing a lab coat.

### The row where the fourth test earns its place

Look at `jumps`/parametric. That series is quiet days with an identical −8% loss
**every twentieth day**, so exactly 5% of days are the tail. Kupiec is perfect:
47 exceptions against 47 expected, p = 1.0000. The Markov independence test
gives 0.028. The joint verdict is 0.089 — **not rejected**. Basel is green.
Three statistics, and the model passes.

The duration test rejects it at **p < 0.0001**, with the fitted shape pinned at
the search's upper bound of 20.

It is right to. A tail that arrives every twentieth day without fail is not a
market — it is a metronome, and the waiting times have *zero variance*, which is
as far from memoryless as a sample can get. The Weibull likelihood is increasing
in $b$ without limit there, so the reported 20.00 is a bound rather than a fit
and the code says so. Nothing else in the battery could see it: the count was
right, and no two breaches were ever adjacent.

The mirror case is one row up. `jumps`/historical reports **zero** exceptions, so
there are fewer than two durations to fit anything to and the column reads `--`.
That is "the test does not apply", which is a different statement from "no
evidence of clustering" and is printed differently on purpose.

### What the third estimator bought, and what it cost

The `parametric` and `ewma(0.94)` rows are the same closed-form VaR over the
same window, differing only in how quickly the past stops counting. Any
difference in verdict between them is therefore a statement about *weighting*
and about nothing else, which is why they are computed as siblings rather than
one replacing the other. Two rows moved, in opposite directions.

On `vol-regime` — calm for 600 days, then four times as volatile — the
equal-weighted estimator is **rejected** (65 exceptions against 47 expected,
joint p = 0.037) and the EWMA one is **not** (59 exceptions, joint p = 0.206).
That is the failure the README has admitted to since the first version, fixed,
and it is fixed in the specific way the theory predicts: Kupiec moves, because
the number of breaches was the problem, while independence was never rejected
for either. An equal-weighted sixty-day window does not mistime its breaches
during a regime shift; it simply runs too small a number for sixty days.

On `iid-normal` the trade runs the other way. EWMA is **rejected** (joint
p = 0.022) where equal weighting passes, and the component that rejects it is
independence, at p = 0.010. This is not a bug and it is worth stating plainly:
at λ = 0.94 the effective sample size is about 31 observations against the flat
window's 60, so on data with no regime to track, the estimator is paying its
whole variance cost for nothing and chasing noise it should be averaging away.
Responsiveness is not free. It is bought with estimator variance, and this table
is what the receipt looks like.

Which is why neither estimator is the default and both are on the dashboard.
Their **ratio** carries information neither number does: EWMA above
equal-weighted means volatility is rising faster than the flat window has
absorbed, and EWMA below it means a shock is ageing out of that window which the
market has already stopped pricing. That second case is the one nobody expects —
reported risk falling by a third overnight because a crash day left a
sixty-observation window, an event in the *estimator* that looks exactly like an
event in the market.

The joint verdict rejects three of nine configurations, and the duration test
rejects two more that it passed. Two of those five are cases where one estimator
passes and another fails on *identical* data. That is the point. A validation
battery that has never failed anything is not evidence of anything; one where
every estimator agrees is not telling you which to use; and one where every
statistic agrees is not telling you what it cannot see.

### The estimator that is implemented and deliberately not used

GARCH(1,1) is the obvious next step after EWMA. It adds the one thing EWMA has
no notion of — **mean reversion**. With a single hand-set decay factor there is
no long-run level to revert to; GARCH has one, and `alpha + beta`, the
*persistence*, says how much of a volatility shock survives each period and
therefore what its half-life is. That number is the entire reason to prefer it.

It is implemented in [`lib/vol_estimators.ml`](lib/vol_estimators.ml), fitted by
maximum likelihood with Engle variance targeting, and tested against a process
it recovers correctly. It is **not wired into the graph**, and `make garch` is
the measurement that says why:

```
         n   alpha (mean +/- sd)    beta (mean +/- sd)     persistence (mean +/- sd)
  --------------------------------------------------------------------------------
        60     0.112 +/- 0.104         0.444 +/- 0.375         0.556 +/- 0.364
       125     0.097 +/- 0.093         0.607 +/- 0.346         0.704 +/- 0.334
       250     0.099 +/- 0.047         0.841 +/- 0.108         0.939 +/- 0.102
       500     0.098 +/- 0.034         0.859 +/- 0.049         0.957 +/- 0.032
      1000     0.092 +/- 0.019         0.880 +/- 0.023         0.973 +/- 0.012
      2000     0.103 +/- 0.014         0.872 +/- 0.015         0.975 +/- 0.009
```

Simulate a known process — `alpha = 0.10`, `beta = 0.88`, persistence 0.98, a
34-day shock half-life — fit it back, thirty replications at each length. Fixed
seed, so the table is the same on any machine.

**This engine's return window is 60 observations.** At 60 the persistence comes
back at **0.556 ± 0.364** against a true 0.98. Read both numbers: the standard
deviation is roughly the size of the thing being estimated, so one fit carries
almost no information — and the mean is *biased*, not merely noisy. Sixty
observations do not contain enough evidence of a long memory to distinguish it
from a short one, so the fit systematically understates persistence. A model
that cannot tell a 34-day half-life from a 2-day one is not reporting a
half-life.

The row where it becomes defensible is `n = 250` and up — a year of daily data,
which is what a GARCH deserves. So the engine ships equal-weighted and EWMA,
both *parameterised* rather than fitted, and therefore with no sampling
distribution to be wrong about at this window length.

It seemed worth building the thing in order to find that out, and worth keeping
it so the finding is reproducible rather than asserted. An absence that has been
measured is a different claim from an absence that has not.

## The same battery, real crises

Everything above is scored against series whose regime was chosen by the person
writing the test. That is the right way to *build* a coverage battery — it is
the only setting where you know in advance which tests ought to reject — and it
is not evidence that the model survives a real tail. Synthetic tail events are
drawn from the distribution the author had in mind. Real ones are not, and the
gap between those two sentences is most of what goes wrong with risk models.

`make backtest-crisis` changes exactly one thing: the data. Same 60-day window,
same 95%, same three estimators, same `Var_backtest.rolling`. The method has to
be visibly identical or the comparison says nothing.

Three windows, each the crisis plus enough either side that the rolling window
has something to warm up on — two sharp shocks and one slow grind, because a
model that fails in a volatility *jump* is failing for a different reason than
one that fails in a year-long drawdown, and running only jumps would hide the
difference:

| window | span | sessions | the book's worst day |
|---|---|---|---|
| `gfc` | 2007-07 → 2009-12 | 631 | −7.02% |
| `covid` | 2019-06 → 2020-12 | 400 | −7.07% |
| `rates-2022` | 2021-06 → 2022-12 | 401 | −3.81% |

The book is the same six names `make run` uses — long tech and financials,
short energy — held at constant weights across each window. That is graph.ml's
own documented approximation, and it is the question a limit is actually
asking: what would *today's* book have done through this history.

```
 window       estimator         n  excepts  expected  Kupiec p   indep p   joint p      duration p  burst  Basel   verdict
 -------------------------------------------------------------------------------------------------------------------------
 gfc          historical      570       25      28.5    0.4925    0.9207    0.7862  0.7483 b= 0.95      5  green   ok
 gfc          parametric      570       27      28.5    0.7713    0.5347    0.7906  0.2265 b= 0.84      5  green   ok
 gfc          ewma(0.94)      570       34      28.5    0.3043    0.9811    0.5899  0.2243 b= 1.20      5  green   ok
 covid        historical      339       18      17.0    0.7955    0.0699    0.1870  0.0160 b= 0.66      8  green   ok
 covid        parametric      339       22      17.0    0.2279    0.0521    0.0733  0.0033 b= 0.65     10  green   ok
 covid        ewma(0.94)      339       23      17.0    0.1517    0.2659    0.1928  0.3409 b= 0.86      5  green   ok
 rates-2022   historical      340       24      17.0    0.1000    0.8083    0.2511  0.7096 b= 1.06      4  yellow  ok
 rates-2022   parametric      340       22      17.0    0.2330    0.6877    0.4530  0.5292 b= 1.11      4  green   ok
 rates-2022   ewma(0.94)      340       22      17.0    0.2330    0.6877    0.4530  0.2816 b= 1.21      4  green   ok
```

**The joint verdict rejects nothing.** Not the global financial crisis, not
COVID, not 2022. The duration column rejects two rows, and the gap between those
two facts is what this section is about.

### Why the model survived, and why that is not reassuring

Part of it is real. This book is genuinely hedged — long technology and
financials against a short energy leg — and in the second half of 2008 energy
fell harder than technology did, so the short side paid on the worst days. The
book's daily volatility through the GFC window is 1.6%, rising to 2.5% in the
Lehman quarter. That is a 1.5× regime change, not the 4× one the synthetic
`vol-regime` series inflicts, and a 60-day window absorbs 1.5× tolerably.

The rest of it is the tests not seeing what is in front of them, and this window
is what put a fourth statistic in the battery.

On the GFC window this book takes **five exceptions between 15 September and 7
October 2008** — seventeen sessions spanning Lehman and the TARP vote — and
Christoffersen's independence statistic returns **p = 0.92**. The test is not
broken and it is not lying. It is a first-order Markov test: it compares
P(exception | exception yesterday) against P(exception | no exception
yesterday), so it detects exceedances arriving *back to back*. Across that
entire 570-day series exactly one pair falls on adjacent days. A burst that
lands every third session is invisible to it, and a reader who takes "p = 0.92"
to mean "the exceedances were well scattered" has read something the statistic
never said.

COVID is worse. `covid`/`parametric` takes **10 exceptions in a single
21-session window** — ten times the independent expectation — with a joint
p-value of 0.073, which does not reject at 5%. A model breaching its 95% VaR ten
times in a month is not a calibrated model.

**The duration test says so: p = 0.0033, shape 0.65.** A shape well below 1 is a
decreasing hazard — a breach makes the next one arrive sooner than chance, which
is the definition of clustering — and because the statistic works on waiting
times rather than on adjacency, the three-day spacing that hid the cluster from
the Markov test does not hide it here. `covid`/`historical` rejects too, at
0.016 with a shape of 0.66.

**And it still does not catch the GFC**, at p = 0.75 with a shape of 0.95. That
is not a defect being glossed over; it is what the test is. It is a **global**
fit over the whole duration distribution, and twenty-five durations spread
across 570 days still look roughly exponential in aggregate even with five of
them bunched around Lehman. One local burst inside a long calm series barely
moves it.

Which is why the `burst` column stays. It is not a hypothesis test and is
labelled as such — just the most exceptions any 21 consecutive sessions
contained, against roughly 1.1 expected under independence — and it is the only
one of the three that sees a *local* cluster. Three instruments, three different
blind spots: adjacency, aggregation, and no distribution theory at all. Reading
one of them alone is how a model breaching ten times in a month gets a passing
grade.

### What EWMA bought here

The clearest thing in the table, and now visible three independent ways at once.
On the COVID window the EWMA estimator cuts the worst burst from **10 to 5**,
moves the Markov independence p-value from 0.052 to 0.266, and moves the
duration p-value from **0.0033 to 0.34** — from a decisive rejection to nowhere
near one, with the fitted shape rising from 0.65 to 0.86. Three statistics that
fail in different ways all agree: it is tracking the volatility spike the flat
window is still averaging away, which is exactly the failure the EWMA estimator
was added for, showing up on data nobody constructed.

It is not free, and the same table says so: on the GFC window EWMA takes **34
exceptions against 27** for equal weighting, on a window where the regime
change was mild enough that the flat estimator was already adequate. That is
the estimator-variance cost the synthetic `iid-normal` row priced, appearing
again on real data. Responsiveness where there is nothing to respond to is
noise, and the reason both estimators are on the dashboard is that neither
dominates.

### Where the data comes from

`docs/crisis/*.csv` — adjusted daily closes, committed to the repository, so
this table reproduces with no API key, no network and no Python. Adjusted, not
raw: an unadjusted 2-for-1 split reads as a −50% single-day return and *becomes*
the entire tail of a 60-day window at 95% confidence, and four of these six
names split inside these windows or since.

The cache is populated by [`tools/fetch_crisis_data.py`](tools/fetch_crisis_data.py),
which is a script and not part of the library because it runs once and the
engine never calls it. If the cache is missing, `make backtest-crisis` fails
with the command that rebuilds it and **does not** fall back to the synthetic
series — a crisis backtest quietly scoring generated data would print a table
indistinguishable from this one under a heading claiming otherwise.

One honest note about provenance. The obvious source was Alpaca, which this
project already speaks to and which the roadmap for this work originally
specified. Alpaca's historical stock bars begin in **2016**, so the 2008 window
is unreachable through it at any subscription tier — the constraint was checked
rather than assumed. The data here comes from Yahoo's public chart endpoint
instead, which is keyless and unofficial: there is no contract and no guarantee
it still exists next year. That is precisely why the output is cached and
committed rather than fetched on demand. The reproducibility of this table does
not depend on that endpoint, only its provenance does.

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
[`lib/vol_estimators.ml`](lib/vol_estimators.ml) is the same contract for the
decay-weighted estimators, and it is a separate module rather than more functions
in the first one because the two are *alternatives to each other*: keeping them
apart is what makes "which estimator produced this number" a question with a
one-word answer.
[`lib/options.ml`](lib/options.ml) is Black-Scholes and the Greeks, plus the
contract types — `Strike`, `Implied_vol`, `Contracts` — that keep a contract
count from ever being mistaken for a share count.
[`lib/limits.ml`](lib/limits.ml) defines limits and evaluates a breach as *data*: a
bool and the magnitude by which the threshold was passed. It has no side effects
at all, which is the point — a node body may be recomputed whenever the runtime
likes, so anything that sends a message has to live outside one.

[`lib/attribution.ml`](lib/attribution.ml) is the Euler decomposition — marginal,
component and standalone risk, and the residual check that would catch the
weights and the covariance matrix going out of alignment, which is this module's
one failure mode that produces confident, plausible, entirely wrong answers.
[`lib/var_backtest.ml`](lib/var_backtest.ml) holds the coverage battery and the
rolling-origin forecast generator that keeps it point-in-time;
[`lib/crisis_data.ml`](lib/crisis_data.ml) feeds that same battery real crisis
data out of a committed cache, and computes the book's return series by writing
into a graph and reading `portfolio_returns` back out rather than by
reimplementing `sum(w_i r_i)` next door; it is offline by
construction and is deliberately *not* wired into the graph, because a
calibration test needs hundreds of observations and answers a question about the
model rather than about the book. [`lib/stress.ml`](lib/stress.ml) is the
scenario suite, and it contains no arithmetic at all — it resolves a scenario
into a set of input-cell writes and hands them to a fork of the engine.

The tests come in two files per claim rather than one.
[`test/test_properties.ml`](test/test_properties.ml) holds the qcheck
generalisations of the identities; every other `test_*.ml` holds hand-derived
values. They are kept apart because they fail differently — a property failure
hands you a shrunk counterexample and an example failure hands you a number that
was supposed to be 0.111803398874989 — and mixing the two styles in one file
makes it harder to tell at a glance which kind of claim a given test is making.

That outside is [`lib/alerts.ml`](lib/alerts.ml), which hangs off an observer
rather than a node and is the only module here that can reach the world.
[`lib/history_buffer.ml`](lib/history_buffer.ml) is the bounded in-memory trail
behind those sparklines, hanging off an observer rather than living in the graph.
[`lib/server.ml`](lib/server.ml) serves `/api/snapshot`, `/api/health`,
`/api/history` and an SSE stream at `/api/stream`; the six pages are authored
in [`web/`](web/) and concatenated into string modules at build time by the
rules in [`lib/dune`](lib/dune), so the binary is self-contained. The feed lives
in [`lib/feed/`](lib/feed) — the Alpaca websocket, an Alpaca REST backfill for
history, and a FRED client for the factor series — and is folded into the library
as top-level modules by `include_subdirs unqualified` in [`lib/dune`](lib/dune),
because it is not a separable component. Its entire job is to write into input
cells.

### A trail, and not a database

The dashboard used to show one number per metric, and one number is a state
rather than a story. Gross exposure of $316,819 says nothing about whether it
has been flat since the open or moved twice in the last minute, and drawdown is
the clearest case: 0.05% is unremarkable if it has been 0.05% all session and is
the only thing on the page worth looking at if it was zero four minutes ago.

So there is a bounded trail — `/api/history`, four sparklines at the foot of the
page, and a header that reads *"last 500 of 1,263 changes, in memory only, lost
on restart."* Both halves of that line are load-bearing. The first says how much
of the session is on screen, because "500 points" and "500 of 1,263 changes" are
different statements and only the second is honest. The second says what this is
not.

Because it is emphatically **not persistence**. `lib/history_buffer.ml` is a
fixed-capacity ring in memory: it writes nothing to disk, it is not restored on
startup, and it drops its oldest entry rather than growing. A restart empties it,
which is correct — the numbers in it are about a process that is no longer
running. This specific trail has none, and that remains true regardless of what
else in the project now does: the desk's journal (`desk/journal.ml`) persists a
session's close, its marks and a forecast per estimator, and it exists because it
was argued for on its own terms in the desk's design doc, not because someone
wanted a longer chart. Making this trail durable too would need that same kind of
argument. The module header says so too, in the file, where someone about to make
that change would read it.

It is an **observer, not a node** — the same seam `lib/alerts.ml` uses, and for
the same reason. A node body may be recomputed whenever the runtime likes, so a
node that appended to a buffer would append an unpredictable number of times.
Appending is an effect and effects live outside the graph. A useful consequence:
the trail is driven by the same signal the SSE stream is, so a flat line means
the book genuinely did not move rather than that a sampler was asleep. Re-sending
an unchanged price appends nothing, and there is a test that asserts exactly that.

Writing it turned up a real constraint worth recording. The first version called
`Graph.snapshot` from inside the change handler, which is wrong: `snapshot`
stabilizes, `Graph.on_change` fires from *inside* an Incremental update handler,
and Incremental refuses to stabilize re-entrantly — `cannot stabilize during
on-update handlers`. It was not caught by reasoning about it; it was caught by a
test that drove the observer. The fix reads the already-settled observers
directly, which is both correct and cheaper.

The chart is inline SVG built by hand, about sixty lines, with no charting
library. Not because one would be hard to add — because `dashboard_html.ml` is a
single string compiled into the binary, and the point of that is a dashboard with
nothing to fetch, version, or fail to fetch. A CDN script tag would make the page
stop working on a machine with no route to the internet, which is exactly the
machine a risk dashboard is most likely to be pinned to.

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
              a trip refuses new orders and cancels open ones, and resets 90 s
              after the limit clears.
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

`make garch` prints the measurement behind a design decision — about five
seconds, no credentials. `make bench` is the other local-only command — a minute or two, and it is what
produced the timing table above. `make stress`, `make backtest`,
`make backtest-crisis` and `make options` are the
other credential-free modes, and they are what the previous sections are about: the scenario suite against
the synthetic book, and the coverage battery against three deterministic return
series. Both are hermetic and both are deterministic, so two runs print the same
numbers and a change in the output means a change in the engine.

`make test` runs the suite; a later section says what it establishes.

**The rest of this section needs API keys.** `make run-live` takes real Alpaca
market data and the FRED factor series; `make serve` does the same with the
dashboard on `http://localhost:8080`. Both read positions from `book.sexp` — copy
[`book.example.sexp`](book.example.sexp) and edit it — unless the Alpaca key they
run with is a paper key, one beginning `PK`; then the paper account's quantities
and cash replace the file's every minute. Both write their journal to
`OHCAMEL_JOURNAL`, by default `desk.db` in the working directory, and both expect
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `FRED_API_KEY` in the environment:

```
$ set -a; source /path/to/.env; set +a
$ make serve
```

A missing key is fatal by design. The engine refuses to start rather than
degrading into something that looks live and is showing made-up numbers. Note also
that a free Alpaca plan allows **one concurrent market-data stream per account**;
if anything else is using the same keys, this gets a 406 and stops.

The dashboard is unauthenticated at the engine, and should be bound to
localhost or put behind a password, as the live host's proxy does. Four
routes change the desk -- orders, cancel, kill and its reset -- and each
refuses a request without the page's header, and one that carries neither
the browser's `Sec-Fetch-Site: same-origin` label nor an `Origin` equal to
its `Host`, which a cross-site page cannot forge (the password, not this
check, decides who else may send one; see *Orders*); on the public demo
each answers 405.

## Watching it

None of the above is required to see it move. The demo is deployed:

**https://ohcamel.ajaiupadhyaya.com**

That is `make demo` — the synthetic feed, no credentials, CVX going stale on
schedule — on one small droplet behind Caddy with a Let's Encrypt certificate,
restarting on its own after a crash or a reboot. It is up at three in the
morning on a Sunday because the feed is generated rather than received. A second
host, `live.ohcamel.ajaiupadhyaya.com`, is the same image against Alpaca and
FRED: real prints, the real ten-year yield, real staleness. It sits behind a
password, because it holds credentials and shows a real book.

The page at that address is the Desk, and it is the engine drawn from itself.
Figure 1 is the dependency graph taken from Incremental's own node table, and
each frame's work travels across it rank by rank: a print in AAPL pulses its
price cell, lights its exposure, the aggregates, the estimators and the
limits that read them, and visibly leaves `covariance` dark until a bar
closes. A node that ran and whose cutoff held its value is drawn with a
dotted rule rather than lit, so the place a change stopped is on the page. An
edge's weight is how often both its ends have run since the process started
(`/api/heat`) — the lesser of the two counts, or its target's alone where it
leaves an input cell, which is set and never run — and a node's rule weight
is its cost class, from O(1) for an exposure to O(n²·w) for the covariance.
The figure opens full-screen as a poster, and it also draws two more bands
now: orders, and fills, whose arrow lands on the quantity row. Three things
left the Desk in this phase, each for a route of its own: the limits ledger
went to `/risk`, the engine's note on what it could not cost went to
`/execution`, and this README's argument went to `/argument`. The two new
routes also draw what the Desk never did. `/risk` renders the macro factor
and the book's beta to it, and the option Greeks, as tables of their own,
and runs the scenario suite behind a button; `/execution` reads the journal
for the orders still working at the venue, the costs overall and by symbol,
the session record and the VaR each estimator wrote at the last close. The
argument moved intact, under the same headings, except that its tables are
*computed by the deployed process at startup* — the scaling probe, both
backtest batteries, the options walk, and the GARCH study on a second domain
— and each is checked against the numbers quoted in this file, cell by cell,
with one printed line saying whether they agree. The
droplet is Linux on amd64 and this README was written on macOS on arm64, so
that line is a second platform reproducing every table here, or naming the
cell where it does not.

The deployment is [`deploy/`](deploy/): a two-stage Dockerfile that fails the
*build* if the runtime image is missing a shared object, a compose file in which
only Caddy has a host port, and a smoke suite that runs after every deploy and
turns a failure into a failed deploy rather than a warning. The assertion in
that suite worth naming is not the health route returning 200, which proves
almost nothing. It is that `nodes_recomputed` in `/api/snapshot` *advances*
between two reads two seconds apart — 234 nodes, in the run that verified this
paragraph — and that `/api/stream` delivers distinct frames spread across a
twenty-second window rather than piled up at its end: 52 of them. A frozen
graph serves valid JSON forever. Those two checks are what distinguish a
dashboard that is watching the market from one that rendered once and stopped.

The first production deploy failed twice, the first live deploy once more,
and all three failures were the deployment's own. A fresh clone has no `book.sexp` — it is gitignored, being the
owner's — so the image now bakes in the committed example instead. And
`deploy.sh` sourced its env file into bash, which reads the `$$` that compose
requires in a bcrypt hash as its own process id; Caddy was handed the result and
refused to start while the engine behind it sat healthy. And the live host's
secrets file was root-only, as specified, which is precisely why the deploy
user's compose could not read it. None could have been caught on the laptop,
and all three are written up in
[the spec](docs/superpowers/specs/2026-08-31-server-side-deployment-design.md)
next to the failure the design had actually prepared for, which never happened.

Redeploying is one command on the droplet, `deploy/deploy.sh`: pull, rebuild —
about a minute, since the dependency layer is cached — restart, verify. Live-mode
credentials live in a root-owned file outside the repository, readable by the
deploy user alone, and reach the container as environment, never as a layer.

## What's verified

`make test` runs 616 tests, plus thirty in `test/desk_async` that run with
the scheduler -- the order manager's cases, a live strategy's rebalance, the
transport's bound, the signal intake's minute loop and that suite's own count --
all hermetic — no network, no credentials, and nothing that waits on the wall
clock: the scheduler's cases move a clock of their own. They cover the numerics against hand-computed values, the wire format,
the alerting state machine, and the recomputation counts that make the
graph's shape an assertion rather than a claim.

Every expected value is derived by hand with the derivation written beside it,
which the newer modules make easy in a way worth noting: the standard test book
holds three names whose return series are the same series and its negation, so
every pair is perfectly correlated in magnitude and the risk shares come out as
the weight magnitudes exactly — `[0.3, 0.3, 0.4]`, readable without arithmetic.
A perfectly correlated book is one bet, so each position's share of the risk is
just its share of the money, and that is the ceiling any real book sits below.

Seven of them are worth knowing by name, because each catches a defect nothing
else would.
The **Euler residual** holds the decomposition to its own identity. The
**hedge test** asserts a risk-reducing position does not breach a risk limit,
which is what a stray absolute value would break. The **lookahead test** rebuilds
each rolling window independently and demands the forecast match, so a backtest
cannot see the day it is forecasting. The **isolation test** runs the whole
scenario suite and then compares the live snapshot field for field, because a
leaking fork produces numbers that are internally consistent and about the wrong
world. The **regime-break test** asserts that the EWMA estimator reads a
higher volatility than the equal-weighted one after a volatility shift is
inserted partway through a synthetic series — the property, not the formula. A
formula test passes on an estimator whose decay runs backwards through the
window, because a reversed weighting is still a valid weighting; it just answers
a question about ancient history. Only the property catches that. The
**delta-hedged test** builds a book that is flat in delta and asserts it still
carries gamma and vega — the options analogue of the hedge test, and the one that
fails if convexity were ever folded into the exposure sum. And the
**two-clocks test** asserts that advancing the staleness clock reprices no
option and advancing the valuation clock touches no feed-liveness node, which is
the assertion that keeps options risk off a five-second timer.

### The same claims, over arbitrary inputs

Four of those seven are *identities* rather than values — Euler, the hedge,
lookahead, isolation — and an example test can only say an identity held at the
one point it was checked. `test/test_properties.ml`
generalises them with [qcheck](https://github.com/c-cube/qcheck): random books,
random weights, random scenarios, 100 cases per property by default and a whole
suite that still runs in under a tenth of a second.

The components sum to the total for **any** positive-semidefinite covariance
matrix and **any** signed weight vector, not just the two hand-built ones. The
forecast is built from strictly prior days at **any** window size, series length
and estimator. The live snapshot survives **any** compounded sequence of shocks,
not just the fixed suite. And the hedge property is constructed rather than
picked: for a randomly generated book, take a position whose returns are the
negation of that book's *own* return series, size it at weight `h`, and the new
portfolio return is `(1-2h)·r_p` — so its volatility is provably lower for
`h < 0.5` and the hedge's Euler contribution is provably negative. One
generator, and every book is its own worked example.

The generator is the part worth being careful about. A matrix of random entries
is not a covariance matrix — it is almost never positive semidefinite, and
feeding one to a decomposition produces failures that belong to the generator
rather than to the code. So the properties generate random *return series* and
run them through `Risk_metrics.covariance_matrix`, which is PSD by construction
and is the only way this engine ever obtains a covariance matrix at all. The
inputs are the kind the system actually sees.

These tests were checked the only way a test can be: by breaking the code on
purpose. Adding a single `Float.abs` to `Attribution.component` — the exact
one-reflex mistake the module's header comment warns about — fails three of the
six properties and four example tests. `QCHECK_TRIALS=5000 make test` runs
30,000 cases in under two seconds if you want more confidence than that.

### Coverage, and what it is not measuring

`make coverage` runs the suite under `bisect_ppx` and reports **79%** (measured
2026-09-19). The badge above is that number; CI enforces a floor of 60% and
prints the full per-file table into the run summary, so a drop is visible
without anyone remembering to look. The number covers both libraries: the risk
kernel, `ohcamel` in `lib/`, and the desk, `ohcamel_desk` in `desk/`.

The interesting thing about the number is that it is bimodal, and it should be
read as two numbers rather than one:

```
  95%  lib/history_buffer.ml     79%  desk/intake.ml
  95%  lib/crisis_data.ml        78%  desk/rebalance.ml
  94%  desk/rules.ml             77%  desk/desk_routes.ml
  94%  desk/ticket.ml            75%  lib/options.ml
  94%  lib/graph.ml              70%  desk/tca.ml
  92%  desk/desk_time.ml         65%  desk/alpaca_paper.ml
  91%  lib/attribution.ml        64%  desk/book_sync.ml
  90%  lib/stress.ml             63%  desk/order.ml
  90%  desk/desk.ml              58%  lib/config.ml
  90%  lib/risk_metrics.ml       56%  lib/alerts.ml
  89%  lib/gate.ml               51%  lib/feed/fred_client.ml
  89%  lib/limits.ml             51%  lib/types.ml
  88%  desk/sim_venue.ml         51%  desk/session_close.ml
  88%  lib/reports.ml            50%  lib/feed/alpaca_rest.ml
  87%  lib/vol_estimators.ml     40%  lib/feed/alpaca_ws.ml
  87%  desk/oms.ml               36%  desk/trade_updates.ml
  87%  desk/reconcile.ml         13%  desk/venue.ml
  86%  desk/contract.ml
  86%  lib/server.ml
  85%  lib/var_backtest.ml
  84%  desk/halt.ml
  82%  desk/ids.ml
  81%  desk/journal.ml
```

The left column is the numeric core, the HTTP, JSON and server-sent-events
layer that serves it (`lib/server.ml`), and the desk's simpler pieces — a
rule, a ticket, the switch, a reconciliation — each tested directly against a
hand-computed value or a fixed scenario. At this measurement (2026-09-19)
`lib/gate.ml`, which answers what an order would do to the book, and
`desk/oms.ml`, the order manager, join it -- A3's gate and rebalance tests
moved them from 68% and 71% -- and `desk/contract.ml`, the signal contract,
enters it new. The desk's routes, A3's intake and its rebalance planner sit
in the right column, each just under the line. The split sits at 80% of each
file's unrounded figure.

The right column is not one thing. Six files in it perform network IO
themselves — `lib/feed/alpaca_ws.ml`, `lib/feed/alpaca_rest.ml`,
`lib/feed/fred_client.ml`, `lib/alerts.ml`'s Slack sink, `desk/alpaca_paper.ml`
(the desk's own Alpaca transport), and `desk/trade_updates.ml` (the venue's
trade-updates socket, a connection separate from the market-data stream) —
and that group is the design decision worth keeping as a metric, not a
backlog: every test in this project is hermetic — no network, no
credentials, nothing waiting on a clock — so a websocket or an HTTP client
is exercised only as far as its pure parts go. Raising these six would mean
testing them against a mock broker, which moves the number up and
establishes nothing about the real one. The bug that mattered in this
codebase was found by pointing it at the actual market, and it is written up
two sections down.

The rest of the right column touches no network and is worth naming rather
than hiding, each against what `bisect-ppx-report`'s own line data says is
unvisited, not a guess: `types.ml` is mostly single-line accessors on
abstract wrappers, many of which nothing calls yet; `options.ml` carries
display and position helpers the pricing tests do not reach; `lib/config.ml`
loads credentials and the book file, and most of what is untested is the
missing-or-malformed-input error messages a hermetic run has no reason to
trigger; `desk/venue.ml` is mostly record types for the venue's read and
trade interfaces, whose derived `sexp_of`/`compare`/`equal` only a handful
of tests call; `desk/order.ml` is a pure state machine — tests drive its
transitions, not the per-variant `to_string` conversions and derived
boilerplate on its state and event types; `desk/book_sync.ml` is pure too,
and its gap is that same derived boilerplate plus a comparator that only
sorts when a sync leaves two or more unmanaged positions, which no test has
done yet.

The paragraphs from here on name each file's gap as `bisect-ppx-report`
showed it at the 2026-09-18 measurement, before A3. Since then A3's tests have
closed much of what they say `lib/gate.ml` and `desk/oms.ml` left unvisited
(the table above is current); what they say about the other files still
stands.

`lib/gate.ml`'s whole gap is the derived `sexp_of` on its three record types
(`Fill.t`, `Move.t`, `Verdict.t`) and the `None`, or fallback, arm of four
functions that answer a limit the fork could not evaluate -- `describe`,
`breached`, `worsened` and `cleared`. `describe`'s `None` arm is unreachable
through `reasons`, its only caller: `reasons` maps only `created` and
`worsened`, and both hold only moves whose `after` is `Some` (gate.ml:73-74,
112-121). `breached`'s, `worsened`'s and `cleared`'s `None`/fallback arms are
reachable but untested -- no test proposes a fill against a limit still
warming up, and `cleared`'s asks for more than that: a limit the live book
could evaluate and breaches (`before` is `Some`), which the fork then
cannot. Every arm of those four that decides created, worsened or
cleared for a limit the fork *could* evaluate does run. `desk/tca.ml`'s
whole gap is the same kind of thing at smaller scale: the derived `sexp_of`
on `Inputs.t`, `Costs.t` and `Summary.t` is the only unvisited code — every
branch of `of_fill`, `mean`, `median` and `weighted_shortfall_bps` runs.
`desk/session_close.ml` is tested everywhere except inside `run_forever`,
the loop that waits for the close, rolls the windows and retries a failed
record: neither suite starts it, so the function that would run in
production every session close has not run in a test yet. (`desk/desk.ml`'s
own `after_fill` and its async `sync` are likewise untouched by any test --
every test builds its order manager with
`Oms.create ~after_fill:ignore` -- but that is a gap in `desk.ml`, not in
this file.)

`desk/desk_routes.ml`'s guard against a host that does not match the server
it answers on, and `guard_sync`'s own exception branch, are both covered.
What is not: the bodies of the three async mutating routes past the
protection check (`orders`, `cancel`, `kill`), and the two guards they
share, `guard_parse` and `guard_async`, along with `parse_cancel_id` and
`parse_kill_reason` — the main suite's four route cases drive the header
check, the host mismatch, and the read routes, but none of them posts a
well-formed ticket, cancel or kill request past protection and into the
sequencer, and that path needs Async, which is `test/desk_async`'s job and
not yet done there.

`desk/oms.ml`, at 71%, sits above the four files just explained --
`lib/gate.ml` at 68%, `desk/tca.ml` at 70%, `desk/session_close.ml` at 51%
and `desk/desk_routes.ml` at 65% -- and still has the largest gap by far,
for a mix of reasons bisect's line data gives separately rather than one
story: `resolve`'s one-minute fallback once its
2/10/30 s schedule is exhausted, and its own lookup-error branch;
`on_update`'s branch for a fill that arrives after this desk already
declared the order failed (the comment there at oms.ml:1027 calls this
"fills after failed"), its already-counted-execution branch, and its
lookup for an order this desk holds no record of; `propose`'s re-checks
after the arrival quote, for the switch tripping or the book aging stale
while the quote was in flight; and, larger than all of those together, the
`refresh_forever` loop and the `refresh` call it makes each turn -- which
reads the session clock every time, and the twenty-day volume on its first
turn, on each turn after a read that failed, and an hour after one that
answered, but never when the volume is fixed, as the demo's is -- and
`fills_json`'s row-building body, which no test calls directly (the nine
cases added since, described next, narrow `refresh_forever`'s own gap).
The scheduler suite's cases are not all order-manager scenarios
either: two are Task 4's, the transport bound and the suite's own
count assertion. The five that exercised `oms.ml` when that figure was
measured -- three fills, an unresent unknown answer, a halt, a limit's
trip, and a restart -- record zero visits on every branch just named.
None of them proposes past a tripped switch or a stale book, none makes
`resolve`'s own lookup fail or delivers a fill after failed, and none
runs `refresh` at all. The nine added since, which the figure predates,
reach two of those: a book that goes stale while the arrival quote is
fetched, and `refresh_forever` reading the clock again as a session ends
-- beside a failed order the venue reports resting, which is cancelled;
a raise just after a kill's halt, through the route and through
`Oms.kill`, which does not stop the cancels; the engine's stop, whose
cancels are asked for when the stream ends, sent again under it when the
venue refuses them, and sent again by a restart's reconciliation; and a
resting order the gate now counts, which `propose` refuses under a real,
submitted order the same way `preview` does, journaling the limit and the
way of filling it fails under.

So the floor exists to make deleting tests noticeable, and that is all it is
for. A coverage target would be an instruction to write the tests that raise it.

### CI runs on both platforms

The [`Makefile`](Makefile) carries two long write-ups of Owl bugs that appear
only on arm64 macOS — a clang segfault at any optimisation level above `-O1`,
and OpenMP code that Owl compiles but never links a runtime for — with the
bisection that found each and the cost of each workaround. Prose describing a
workaround that nothing runs is a claim. So CI builds on `ubuntu-latest` *and*
`macos-latest`, and the macOS leg exports exactly the two variables the Makefile
sets, which makes a green run evidence that the documented workaround still
works on a machine that is not the author's. Both legs also run the four
credential-free modes end to end, because a `printf` format string that only
fails at run time is invisible to `make test`.

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

The kill switch is still a flag in the kernel. `lib/alerts.ml` does not import the Alpaca client and could not reach a trading endpoint by mistake. But something now obeys the flag: the desk, outside the kernel. A trip refuses every new order and cancels every open one, and it never flattens a position. A risk system that closes a book by itself is a different and far more dangerous project than this one; the switch stops the desk adding risk and leaves what is held to a person. On the live host only a deliberate reset lifts it.

## Orders

Every order the desk sends has passed two checks, in this order, and the page shows both answers.

**The rules** (`desk/rules.ml`) ask whether the order should exist. Every failing rule is reported, not only the first:
- the symbol is in the book's universe, and the quantity is a positive whole number;
- the book enables trading, the desk has a trading half, and the book is the account's -- read within the last two sync intervals;
- the kill switch is clear, and the regular session is open at the order's own instant, on the venue's clock as last read -- its next open and next close, not a flag read a minute ago;
- the name has a mark that is not stale;
- a limit price is a whole cent at every price -- stricter than Rule 612 asks below a dollar, because the order goes to the venue with two decimals -- and within the book's collar of the mark;
- quantity × mark is within the order cap;
- the quantity is within the book's share of twenty-day volume, and unknown volume refuses;
- the same order was not proposed within the duplicate window;
- fewer orders are open than the cap.

**The limits** (`lib/gate.ml`) answer what the order would do to the book, on a fork of the live graph with the fill applied -- so no second implementation of exposure or of any limit exists to drift. An order that takes a clear limit over its line, or leaves a breached limit further over, is refused naming the limit. One that brings a breached limit closer to its line passes: a desk must be able to trade out of a breach.

Then the order is written to the journal as `pending_submit` **before** the request that sends it. An answer that never comes is resolved by looking the client order id up, never by sending the order again. An order declared failed because every lookup missed, which the venue later reports it still works, is cancelled by the venue's id whatever the switch reads, because nothing else would manage it. The request is bounded at ten seconds. The bound closes a connection that is still connecting or has answered; one held open before its status line lasts until the peer or the kernel ends it. Fills are journaled whatever state their order is in. The book's position follows each fill and is set from the venue's own figure, and the account is read again after it; a read that was already out when the fill landed is refused, so it cannot undo the fill. A restart reconciles every open order against the venue before it sends another order; startup waits up to 20 s for it.

**The kill switch** refuses every new order and cancels every open one when a limit it trips on is breached, or when someone halts the desk by hand. It does not flatten positions. A halt by hand is answered as soon as the desk is halted; the cancels go on behind the answer, one at a time, and the log says when the last has been asked for. If something raises just after the halt is set, the answer still carries the switch, with a fixed sentence beside it that the page shows, and the cancels still go. Until the open orders reach zero the page says it is cancelling them, and only then that they are cancelled. Under the switch, a cancel whose answer is not a confirmation is sent again, 2, 10 and 30 s apart, while the order is still open, and a partial fill in between does not end that. Each order has at most one schedule of cancel retries and one of lookups at a time, however many kills, trips or reconciliations ask for one. On the live host only a deliberate reset lifts the switch -- except when the engine has stopped the desk itself, because the venue's order updates ended and no fill would be heard: the switch reads `stopped`, never "halted by hand", a reset is refused with 409, and only a restart, which reconnects and reconciles, clears it. The stop asks the venue to cancel every open order, as a trip and a kill do, but the venue's answers travel on the stream that ended, so none can be confirmed: the page says the cancels were asked for, never that they happened, and the orders stay `pending_cancel`. The stream ends only when the paper host refuses the key, so those DELETEs are likely refused too; the restart's reconciliation sends the DELETE again, once per venue id, for any `pending_cancel` order the venue reports still resting. On the demo it resets itself 90 s after its limit clears, and the page says that only the demo does this.

**Costs** (`desk/tca.ml`, after Perold 1988) are measured per fill from the decision price. Each is split into delay (the market's move before the order arrived) and slippage against the arrival quote's mid, shown beside the quoted half-spread and the difference from the book's modelled half-spread. On the paper account every cost measures Alpaca's fill simulator against IEX's quote -- one venue's, not the national best.

| Route | Host | What it does |
|---|---|---|
| `GET /api/desk/tca` | both | costs per fill, overall and by symbol |
| `GET /api/desk/sessions` | both | the newest 250 recorded session closes |
| `POST /api/desk/preview` | both | the rules' and the limits' answer to a ticket; creates nothing |
| `POST /api/desk/orders` | live | a ticket through the rules, the limits, the journal and the venue |
| `POST /api/desk/cancel` | live | cancel one open order |
| `POST /api/desk/kill` | live | halt the desk, answer, then cancel every open order |
| `POST /api/desk/kill/reset` | live | lift the halt; the body must say `{"confirm":"reset"}` |
| `GET /api/research` | both | the registered signal strategies with their sizing and capital fraction, each one's latest judgement, the files the intake is holding, and that R8 (data hash) is not enforced; the demo registers none |
| `GET /api/research/evidence` | both | EXP-A01's two manifests, embedded at build time and served byte for byte as committed |

The live routes require the header `X-OhCamel-Desk: 1` and a request the browser labels as from this site (`Sec-Fetch-Site: same-origin`, or an `Origin` equal to the `Host`), behind the host's password. The demo answers each with 405 and a sentence. Its own trader proposes a small order every 45 seconds, so the blotter has something to show, including refusals.

Limits, stated:
- whole shares; market and limit orders; day orders; regular hours;
- the gate judges each proposal on at most five forks of the live book, after deduplication, because nobody knows which resting orders will fill: with none of them filled; with every resting buy filled; with every resting sell filled; with, for each name the proposal itself trades, that name's resting buys if the proposal's own quantity there is positive and its resting sells if negative (and, for every other name, whichever of its resting buys or resting sells takes its position further from zero); and with all of them filled. The proposal passes only if it passes on every one -- created or worsened fails, reduced passes, each judged against that fork's own book, so a breach the resting orders alone would make refuses only a proposal that makes it worse. For per-name and sector notional caps these are the worst cases; for gross notional, exactly so for a proposal that trades one name, and only an approximate bound for one that trades several (a rebalance); VaR and the Greek limits are not linear in the positions either, so for them too the five bound it only approximately. A resting order counts at its remaining quantity, priced no better than the mark (a buy at the higher of its limit and the mark, a sell at the lower), which moves equity and drawdown only, never a notional cap. An order the desk declared failed after every lookup missed still counts, at what remains of it, until the venue reports it filled, cancelled, expired or rejected, or it was created before the date of the latest session close recorded in the journal, since a day order cannot outlive its session. A resting order the desk cannot price, or in a name the book does not hold, is not gated as zero: the proposal is refused, naming that order;
- Alpaca paper only, by construction: the trading host is a constant, and a key that does not begin `PK` is refused before a request is sent.

## Signals and research

A3 added a research layer beside the desk, and a contract neither side can
bypass keeps them apart: [`research/`](research/) runs backtests and writes
JSON documents; `desk/contract.ml` decides whether one is ever acted on, and
it trusts nothing about *when* except the bars it has itself recorded.
[`interface/README.md`](interface/README.md) states the rules, ported from
Alpha's original contract, now enforced by OhCamel's own `desk/`.

**The contract, R1 through R7.** A signal names a strategy, an `as_of` date,
target weights and a `validation` block. `desk/intake.ml` reads at most a
bounded number of signal files per pass from `OHCAMEL_SIGNALS_DIR`, judges
each against the desk's own session clock — never wall-clock time or the
file's own `computed_at` — and records the outcome in the journal: accepted,
rejected naming the first rule it failed, or advisory when every rule but R6
(`validation.status == pass`) passes. A signal from the future is refused
(R3); one older than `max_age` trading days, counted in bars the desk has
actually seen, is refused (R4); a duplicate or out-of-order sequence number
is refused (R5); a symbol outside the universe, a symbol named twice in the
same document, an over-levered target or a NaN weight is refused (R7) — and,
once R7 passes, a document naming a symbol outside its own strategy's
declared set is refused separately, so a strategy can only ever move the
names it declares. A signal dated after the latest recorded session — not a
stale one — is deferred rather than rejected: the close it needs may not
have landed yet, so it is judged again each minute until a session on or
after its `as_of` exists, and its first sighting is written to the journal's
deferrals, keyed by strategy and sequence, so neither a restart nor a file
rename resets its clock and lets a wait already under way start over from
age zero — the fix for a real failure: a review found that restarting every
two sessions let a file dated ahead of its data get accepted at age 0. A
document so far ahead that no wait could end well is rejected at R3 at once.
Because the sessions record itself can have gaps — no close is recorded
while the desk is down — R4's own weekday bound catches what a session
count alone would call fresher than it is: a document is also measured in
weekdays from its `as_of` to the latest session, less a slack of two, and
rejected at R4 when that count exceeds `max_age`, so an outage cannot make a
stale signal look young. Malformed files — bad JSON, non-UTF-8, a schema
violation — are recorded by name and never read again, unless the file is
still changing: one caught half-written, whose identity differs from the
last attempt, is read again next pass rather than given up on. A file whose
(strategy, sequence) the journal has already judged is skipped even when its
body differs from what was judged; that is logged once, by name, and the
first judgement stands. **R8**, which would re-check a signal's own hash of the bars
it read against the desk's, is not enforced (ruling 3): its recipe depends on
how two languages print a float, and until that is fixed, the sentence
"R8 (data hash) is not enforced; see the design §3.12" is what `/api/research`
and the Research page say, verbatim — `interface/README.md` still lists R8 in
its table because it is ported from Alpha's own contract, with two noted
edits (the enforcer's name, and the `data_hash` paragraph, since OhCamel does
not implement R8's second computation the way Alpha's `core/lib/bars.ml`
did), not rewritten to drop a rule OhCamel itself does not enforce.

**Sizing is the owner's, and defaults off.** Every registered strategy
carries `(sizing advisory)` or `(sizing live)` in `book.sexp`, default
`advisory`, and a `capital_fraction` of equity its weights would apply to if
it were ever promoted. A signal is sized only when it passes every rule
**and** its strategy is `live`; on an `advisory` strategy the desk shows the
signal's weights and prices nothing from them. No task in this project sets
a strategy `live` — that switch is the owner's alone, made in `book.sexp`
after reading a strategy's report, never inferred from a verdict.

**The rebalance, for a strategy the owner has set `live`.** A validated
signal becomes one market-on-open rebalance, sent between 19:00 ET after a
close and two minutes before the next open — inside Alpaca's opening-auction
window and after the day's close is on record, so a weekend or a holiday
before 19:00 refuses rather than guesses, because the venue's clock cannot
yet tell that evening from a trading one. Nothing is sized unless the
signal's `as_of`, the priced close and the newest recorded session are the
same day, and no weekday has closed since unrecorded. The rebalance is
proposed and gated as one unit: the legs that shrink a position go out
first, and the whole unit is judged as if only the legs that grow a position
fill, so a short strategy's own rebalance cannot leave a limit breached on
the strength of a leg the venue never acknowledged. It stops at the first
leg the venue does not acknowledge. Every leg reaches the venue through one
function, `Wire.submit`, whose `permit` type is abstract outside
`desk/wire.ml`: nothing else can construct one, so a call written against the
venue's own record field, or an adapter typed at a weaker permit, does not
compile. Which module actually calls `Wire.submit` — today, the order
manager alone — is held by a test that fails if any other file in `desk/` or
`bin/` names the module for anything but its permit type.

**The research service**
([`research/src/ohcamel_research/service.py`](research/src/ohcamel_research/service.py),
Docker Compose's `ohcamel-research`, `live` profile only) computes each
registered strategy's weight once a trading day, from 19:15 America/New_York
once that day's bar can be fetched (00:16 UTC: fdq sends its end date as
23:59:59 UTC and SIP's last 15 minutes are restricted, so 20:16 New York in
summer and 19:16 in winter), after the desk's own close is on record and
inside the OPG window for the next session. The weight is 0.9 when the rule
is on -- the fraction the evidence held invested, fdq's `cash_buffer_pct`
0.10 -- and 0 when it is off, computed from the strategy's manifest and
`selected_params` alone. It calls the same `signal.emit`/fdq class the battery ran to produce that
manifest, never a second implementation of "is the close above its SMA", so
the live signal and the backtest that validated it cannot drift apart by one
of them keeping its own copy of the rule. It refuses to write anything
unless the fetched bars' own provenance sidecar reads `source: alpaca` —
`fdq`'s fetcher falls back to Yahoo Finance silently on an Alpaca failure,
and a signal computed on another vendor's bars with no trace of it is
exactly the kind of drift this project exists to prevent. It accepts only
`ma_crossover`: Donchian's signal is history-dependent and the service's bar
window is truncated to a lookback margin, which would be wrong for a channel
rule, so nothing else is accepted. The write is atomic — temp file, fsync,
rename — and the live engine only ever reads what is already on the shared
volume; the image had never been built as of this phase (the local Docker
daemon was unhealthy throughout it), so its first build is at deploy, where
its own staleness check fails loudly if anything about it is off.

**EXP-A01.** The first and only battery run against this contract. Its
pre-registration
([`hypothesis.md`](research/experiments/EXP-A01/hypothesis.md),
[`config.yaml`](research/experiments/EXP-A01/config.yaml)) was committed
before a line of the battery existed; its two manifests
(`manifest.exp_a01_spy.json`, `manifest.exp_a01_tlt.json`) are what
`/api/research/evidence` serves, byte for byte, and what the report below is
built from. The full report is
[`research/experiments/EXP-A01/report.md`](research/experiments/EXP-A01/report.md);
its verdict, quoted rather than paraphrased:

> | Strategy | Verdict | Sizing | verdict_line |
> |---|---|---|---|
> | `exp_a01_spy` (SPY) | **fail** | advisory | fail: 1 of 5 gates failed (regimes_positive); PBO 0.786, above 0.5 |
> | `exp_a01_tlt` (TLT) | **fail** | advisory | fail: 5 of 5 gates failed (holdout_positive, dsr, psr, bootstrap_sharpe_lower5, regimes_positive); PBO 0.981, above 0.5 |
>
> Both strategies fail. The pre-registration's kill criterion is "any charter
> gate failing is a fail", and each strategy fails at least one gate.

`report.md` states, gate by gate, why each failed, in its own words:

> - **SPY fails one gate, the regimes.** It was positive in 2 of the 5 regimes. Those
>   two are 2023 and 2024, the calm bull years the owner named before the run. It was
>   negative in all three stress regimes: the 2018 Q4 selloff, the 2020 COVID crash and
>   the 2022 rate shock. SPY passes the holdout, DSR, PSR and bootstrap gates. Had its
>   regime gate passed, its verdict would have been "pass, fragile", because its PBO is
>   0.786, and under the kill criteria it would still not have been promoted without a
>   second, independent window.
> - **TLT fails every decided gate.** Its holdout is negative. Its DSR, PSR and
>   bootstrap lower bound are far below their lines. It was positive in 2 of 5 regimes,
>   the 2018 Q4 selloff and the 2020 COVID crash, and negative in 2022, 2023 and 2024.
>
> **PBO with three configurations.** With 3 configurations, a set with no skill at all
> scores a PBO of about 2/3, since the in-sample best lands at or below the
> out-of-sample median about two times in three by chance. "Pass, fragile" (PBO above
> 0.5) was therefore the expected label for a passing strategy here, not evidence
> against it. The hypothesis said so before the run. Both figures here, 0.786 and
> 0.981, are above 2/3.

**With status `fail`, neither signal can be sized under R6, whatever `book.sexp`
says.** Both strategies ship `advisory`; promotion, if it ever happens, is the
owner's decision alone, made after reading the report.

Two disclosures the report states, repeated here because they touch claims
made elsewhere in this file: the charter's Data section
([`docs/CHARTER.md`](docs/CHARTER.md)) says the bars come from "Alpaca (IEX,
daily)"; the ten-year history EXP-A01 actually reads is consolidated (SIP)
volume, unadjusted, so returns exclude distributions — a bias against the
rule passing, not for it, and one the charter does not carry because it is a
verbatim port. And `fdq` 1.0.0 books an exit's half-spread to its cost ledger
but never to equity, so every round trip paid half the stated spread until
the runner doubled it, under one named constant (ruling 11b); the same run
also settles which of two PBO implementations' figures is trusted when they
diverge, by taking the higher one, so neither tool's own defect can flatter a
verdict (ruling 11c). Both are one sentence here; the report has the
arithmetic.

**The Research page**, `/research` — the sixth page, between Execution and
Argument — reads `/api/research` (the registered strategies with their
sizing and capital fraction, each one's latest judgement, how many files the
intake is holding, and the R8 sentence) and `/api/research/evidence` (the
two manifests above) and computes nothing itself. On the public demo no
strategy is registered — its synthetic book holds neither SPY nor TLT — so
the page says exactly that, and shows the EXP-A01 evidence regardless,
because the evidence is a fact about the build and not about which book is
loaded. Figure 1 draws a signals band into the orders band only where an
intake actually runs, so the demo's own figure, which has none, stays
byte-identical to the one before this phase.

`research/` has its own test suite, run by `make research-test`: 355 Python
tests, hermetic, offline, seeded, checked with `ruff`. `lib/verified.ml`
and this file's counts cover only the OCaml suites; the Python count is
reported here and in `docs/status.md`, never folded into either.

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

Orders go to one place, Alpaca's paper account, and only after the rules and
the book's limits pass them (see *Orders*). Nothing here can reach a
live-money endpoint, and the public demo trades a venue simulated in this
process. Persistence is one journal, `desk/journal.ml`: every order, its
events and its fills, and each session's close, marks and VaR forecasts.
`run-live` and `serve` keep it in a file, `/data/desk.db` on the live host,
and restore the drawdown trail from it at the first successful sync after
startup; the demo keeps it in memory, so it starts empty each run. The book
itself still rebuilds from `book.sexp` and the feed on every restart; when
`run-live` or `serve` runs with an Alpaca paper key, the desk then resyncs its
quantities and cash from that account every minute. There is one broker, Alpaca,
and one macro source, FRED.

It is still not a strategy platform, and A3 drew that line rather than erased
it. [`research/`](research/) now runs a walk-forward, cost-swept,
multiple-testing-corrected battery against ten years of Alpaca bars and writes
the verdict as a JSON manifest — but validating a signal and trading on
it are kept apart on purpose. `desk/contract.ml` reads that verdict under rules
R1 through R7 (see *Signals and research*, above, and
[`interface/README.md`](interface/README.md)), and every registered strategy
carries `(sizing advisory)` or `(sizing live)` in `book.sexp`, default
`advisory`, with no task in this project ever setting one `live`. A passing
signal on an advisory strategy is shown, never sized. EXP-A01, the only
battery run so far, tested two strategies and both fail their gates, so
nothing here has ever been promoted. `make backtest` is a different thing
entirely: it validates the *risk model* against synthetic and crisis return
series, never a trading idea, and that distinction is the whole point of the
mode. The volatility estimator is equal-weighted *or* exponentially weighted and the engine
reports both, but neither is conditional in the sense a GARCH(1,1) is: EWMA has
one hand-set decay factor rather than a fitted mean-reversion, so it tracks a
regime change but does not forecast the return to normal after one. That fit is
not here — GARCH(1,1) *is* implemented and tested, and is deliberately not wired
in, because `make garch` shows its persistence parameter cannot be estimated on
a 60-observation window. Nothing is optimised: the engine reports where risk
is concentrated and never suggests what the weights should be, which is a
different project with a different failure mode.

The options path prices European contracts with Black-Scholes: no American
exercise, no dividends, no term structure of rates, and no implied-volatility
solve — vol is an input, because there is no chain to invert a price from.
Portfolio vega is a parallel-shift number, now reported alongside a tenor
breakdown that shows what the parallel-shift sum hides — but it is not bucketed
by strike, so skew risk is invisible. And options risk is **off in live mode**, stated
rather than silently absent: with no options-chain source, the choice was to
decline or to invent a surface, and an invented one produces Greeks that look
exactly like real ones.

It is a risk and limits engine, and it stops where a risk and limits engine
should stop.

## The math, written down

[`docs/quant_notes.md`](docs/quant_notes.md) is the reference companion to this
file: the VaR and ES definitions with the nearest-rank convention actually
implemented, the Euler derivation in equations rather than prose, the EWMA
recursion and what its effective sample size costs, the Kupiec and
Christoffersen statistics with their degrees of freedom and their nulls stated
explicitly, the Weibull duration test with its scale parameter profiled out and
its censoring spelled out, the Basel zone boundaries computed rather than
quoted, and the Black-Scholes Greeks. Every formula names the file and function that evaluates
it, and every limitation this project found the hard way — the ε-rounding
artefact in the tail rank, the first-order Markov blind spot that
motivated adding a duration test, the duration test's own blind spot for local
bursts, the parallel-shift approximation in portfolio vega — is stated where the
formula is, not left for a reader to discover.

## Origin

This was built from a written brief, which is archived with provenance at
[`docs/brief.md`](docs/brief.md).
What it is *now* — built, deployed, and next — is kept current, and dated, in
[`docs/status.md`](docs/status.md).
