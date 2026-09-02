# OhCamel — state of the project

*As of 2026-09-02. This is the document to read first when coming back to the
repository after time away, and the one to update when the facts in it change.*

The [README](../README.md) argues: it makes the case for the design with real
numbers and is long because the case is. [`quant_notes.md`](quant_notes.md)
states: every formula in standard notation, cross-referenced to the function
that evaluates it. This file does neither. It is an inventory — what exists,
where it runs, how to drive it, what it will not do, and what comes next — kept
short enough to read in ten minutes and dated so its staleness is visible.

---

## What it is

A reactive risk and limits engine, written in OCaml on Jane Street's
Incremental, a library for self-adjusting computation. Positions and market
data go in; exposure, VaR, expected shortfall, beta, drawdown, Greeks and limit
breaches come out, and keep coming out as the market moves. The thesis is that
risk is a dependency graph, not a polling loop: a tick recomputes exactly what
is downstream of it and nothing else, so the cost of an event is set by what
the event touches rather than by the size of the book. The engine also answers
the three questions a risk number invites — *where* the risk is (an Euler
decomposition: an additive split of total risk across positions that sums back
exactly), *whether the number is any good* (a coverage battery run over point-
in-time forecasts, including against real crisis windows), and *what would
break it* (a scenario suite that shocks a fork of the live graph).

It computes and reports. It never places, cancels or simulates an order, and
that is a design invariant rather than a missing feature.

## Where it runs

| | |
|---|---|
| Public demo | **https://ohcamel.ajaiupadhyaya.com** — synthetic feed, no credentials, always on |
| Live host | `https://live.ohcamel.ajaiupadhyaya.com` — same image against Alpaca and FRED, basic-auth. Caddy serves it and demands a password; **the engine container behind it is not started yet** (see *Next*) |
| Host | One DigitalOcean droplet, `s-2vcpu-4gb`, Ubuntu 24.04, nyc3, at `138.197.116.165` |
| Cost | $24/month, metered hourly, capped |
| Proxy / TLS | Caddy, Let's Encrypt, HTTP→HTTPS 308, HSTS. Only Caddy has a host port; the engines are on an internal Docker network |
| DNS | Porkbun. Two A records, `ohcamel` and `live.ohcamel`, on a domain whose apex is unrelated (the owner's portfolio site) |
| Deployed | 2026-09-02, verified by the production smoke suite: 8 passed, 0 failed, live host skipped |

Resource use at rest is small enough to be worth stating so nobody adds a
bigger box for the wrong reason: the engine sits at about 41 MB and six percent
of one core with the demo feed ticking one name every 400 ms; Caddy at 27 MB
and one percent. The droplet is at roughly three percent of capacity. The
4 GB was chosen for the twenty-minute OCaml *build*, not the runtime.

Everything about the deployment is under [`deploy/`](../deploy/) and is
specified in
[the server-side design](superpowers/specs/2026-08-31-server-side-deployment-design.md),
which also records the two bugs the first production deploy found and why the
local harness could not have caught either.

## What it computes

**Exposure and the book.** Per-instrument and per-sector exposure, gross and
net, equity (cash plus net), drawdown from peak, and per-instrument weights.
`Price`, `Qty` and `Notional` are abstract and mutually incompatible types, so
`price + qty` is a compile error.

**Risk measures.** Historical VaR and expected shortfall at 95% over a rolling
return window (nearest-rank convention, with the ε-rounding artefact in the
tail rank found and documented); parametric variance–covariance VaR from an
equal-weighted covariance *and*, as a sibling node rather than a replacement,
from an exponentially weighted covariance at λ = 0.94, the RiskMetrics daily
convention. The two parametric numbers disagreeing is the regime-change
diagnostic; historical and parametric disagreeing is the tail-fatness
diagnostic. Portfolio beta to a single macro factor series.

**Attribution.** An exact Euler decomposition of portfolio volatility into
marginal, component and standalone risk, per instrument and per sector, signs
preserved so a hedge shows negative component risk. A residual check guards
the one failure mode that yields confident, plausible, wrong numbers. Limits
can be written against a name's *share* of total risk.

**Validation.** Rolling-origin, point-in-time VaR forecasts scored against
realised returns with Kupiec's unconditional coverage test, Christoffersen's
Markov independence test, their conditional-coverage combination, a
Weibull duration-based independence test (added because the Markov test has a
blind spot for clustered exceptions at lags beyond one), and the Basel
traffic-light zones computed from the binomial rather than looked up. Runs
against deterministic synthetic series (`make backtest`) and against real
crisis windows from a committed cache (`make backtest-crisis`): the COVID crash
(2020-02 → 2020-04) and the 2022 rate shock. **The 2008 window is not there**
— Alpaca's history begins in 2016 — and the README says so rather than
substituting anything.

**Options.** Black-Scholes European pricing with delta, gamma, vega and theta,
tested against Hull's textbook values and put-call parity. Delta-equivalent
exposure folds into the ordinary exposure sum; gamma and vega are reported
separately because convexity cannot. Portfolio vega is given as a parallel
shift *and* bucketed by tenor, so a calendar spread stops reading as flat.
Theta forced a second clock: a valuation-date cell that moves only when a
caller advances it, kept separate from the staleness clock, with tests that
neither can do the other's job. **Options risk is off in live mode**, stated
rather than silent, because there is no options-chain source and an invented
vol surface would produce Greeks indistinguishable from real ones.

**Scenarios.** `make stress` shocks a *fork* of the live graph and reads the
result through the same nodes — zero duplicated arithmetic. Five scenario
kinds: everything by a proportion, one instrument, one sector, the macro
factor (through each name's beta), and volatility (a multiplier on the return
window). The suite reports which limits cross their line under each. An
isolation test asserts the live snapshot is unchanged, field for field,
after the whole suite runs.

**Limits and alerting.** Five limit kinds — gross notional, VaR, component
VaR, a Greek limit, and max drawdown — at instrument, sector or portfolio
scope, with validity reasoned per kind: a standalone-VaR limit on a single
name is rejected because VaR is not additive; component VaR is valid
everywhere because it is additive by construction. A breach is computed as
data (a bool and a magnitude), never as an effect. Alerting is an observer
outside the graph: edge-triggered, with hysteresis on the way back down, off
by default, and a kill switch that sets a flag and does nothing else.

**Staleness.** Each symbol carries the time of its last tick. A name that has
not printed within the threshold is stale, and the dashboard desaturates it
and everything computed downstream of it — scoped by dependency, not by page —
because a limit reading "not breached" off an old mark is not information.

**A trail, not a database.** A bounded in-memory ring of the last 500 changes
drives four sparklines and `/api/history`. It writes nothing to disk and is
empty after a restart, deliberately; making it durable would be *adding
persistence to the project* and must be argued as such.

**Implemented and deliberately not wired in.** GARCH(1,1), fitted by maximum
likelihood and tested. `make garch` prints why it stays out: on a 60-observation
window the persistence parameter cannot be estimated, and an estimator that
cannot be estimated should not be producing a VaR.

## How to drive it

One binary, `ohcamel`, with a mode as its first argument. Every `make` target
below re-enters the project-local opam switch, so they work from a clean shell.

| Mode | `make` | Needs | What it does |
|---|---|---|---|
| `synthetic` (default) | `run` | nothing | The fastest way to see the numbers without a browser. Sixty events over a generated book; prints the whole book after each, a breach and recovery, the decomposition, and the recomputation-count table. Never starts Async |
| `stress` | `stress` | nothing | The scenario suite against the synthetic book |
| `backtest` | `backtest` | nothing | The coverage battery over three deterministic series, three estimators each |
| `backtest-crisis` | `backtest-crisis` | nothing (committed cache) | The same battery over COVID and 2022 |
| `options` | `options` | nothing | The options book, Greeks, tenor buckets, and the two-clocks walk |
| `garch` | `garch` | nothing | The measurement behind not wiring GARCH in |
| `demo [port]` | `demo` | nothing | The dashboard on a synthetic feed. This is what the public URL runs |
| `live [book]` | `run-live` | Alpaca + FRED keys | Real market data, terminal output |
| `serve [port]` | `serve` | Alpaca + FRED keys | Real market data, dashboard |

Also: `make test`, `make bench` (local only, a minute or two), `make coverage`,
`make fmt`, `make deps`, `make doctor` (diagnoses the macOS build of Owl, the
OCaml numerics library underneath the covariance math), and the
`deploy-*` targets that build the image and run the proxy harness on
`localhost:8000`.

Live modes read `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `FRED_API_KEY` from
the environment and refuse to start without them. Positions come from
`book.sexp`, which is gitignored; copy `book.example.sexp`. A free Alpaca
account allows one concurrent market-data stream.

## The interface

Read-only, unauthenticated at the engine, gated at the proxy for the live
host. No route mutates anything today; the first planned feature (see *Next*)
would be the first that does.

| Route | |
|---|---|
| `/` | The dashboard, a single embedded HTML string with inline SVG and no external assets |
| `/api/snapshot` | The whole book as JSON, including `nodes_recomputed` — the counter that proves the graph is alive |
| `/api/health` | Feed liveness per symbol; `healthy: false` when anything is stale |
| `/api/stream` | Server-sent events, emitted only on an actual graph change (parked on an `Ivar`, not a timer), coalesced over 80 ms |
| `/api/history` | The in-memory trail |
| `/api/stress` | The scenario suite, run on a fork |

## What is verified

- **210 hermetic tests** — no network, no credentials, nothing waiting on a
  clock. Expected values are derived by hand with the derivation beside the
  assertion. Seven are worth knowing by name: Euler residual, hedge (no stray
  `abs`), lookahead, stress-fork isolation, regime-break, delta-hedged, and
  two-clocks.
- **Property tests** (qcheck) generalise the identities over random inputs:
  Euler additivity, component VaR summing to portfolio VaR, a hedge reducing
  variance, VaR monotone in confidence, fork isolation, backtest lookahead.
- **Coverage 70.4%**, with a 60% floor in CI that exists to make deleting
  tests noticeable, not as a target. The number is bimodal by design: the pure
  numeric core is above 90% and the network edges near 40%, because exercising
  them means mocking a broker, which raises the number and establishes nothing.
- **CI** on every push, `ubuntu-latest` and `macos-latest`. The macOS leg
  exports the Owl workarounds the Makefile documents, so a green run is
  evidence they still work. Both legs run all six credential-free modes end to
  end. Benchmarks run only on manual dispatch, because shared-runner timings
  are noise.
- **Recomputation counts are asserted**, not claimed: `test_graph.ml` pins
  how many nodes a tick reaches.
- **Production smoke suite** after every deploy: the dashboard renders,
  `nodes_recomputed` *advances* across two reads two seconds apart, the SSE
  stream delivers distinct frames *spread over* a twenty-second window, HTTP
  redirects, the certificate validates, the engine ports are unreachable from
  outside, and the live host returns 401 without credentials. A failure is a
  failed deploy, not a warning.

## Numbers worth knowing

From `make run`, the architectural claim:

| instruments | nodes in graph | nodes per tick | if polled |
|---|---|---|---|
| 10 | 58 | 25.6 | 58 |
| 100 | 337 | 25.2 | 337 |
| 400 | 1267 | 26.0 | 1267 |

From `make bench` on an M2 Pro, the honest version of it — node count per tick
is flat, wall-clock is not, because the ~25 nodes reached include O(n) and
O(n²) ones; what incrementality removes is the O(n²·w) covariance rebuild:

| book | incremental | polled | ratio |
|---|---|---|---|
| 10 | 19.5 µs | 51.7 µs | 2.7× |
| 100 | 89 µs | 3.4 ms | 38× |
| 400 | 493 µs | 53.4 ms | 108× |

From the production smoke run that verified the deployment: 234 nodes
recomputed across a two-second gap, 52 distinct SSE frames over twenty seconds.

Size: about 8,200 lines in `lib/`, 6,600 in `test/`.

## The invariants

Stated in full in [`handoff.md` §2](handoff.md); listed here because they are
the part of that document still in force. A change that ships faster by
breaking one is a regression even if the tests pass.

1. Every dependency is a graph edge. No node reads a global, a ref, or the network.
2. No second implementation of exposure/equity/limit arithmetic. Counterfactuals go through `Graph.fork`.
3. Units stay abstract. New money- or risk-shaped quantities get their own types with one named bridge.
4. Pure numerics stay pure. Risk math lives in modules that do not know Incremental exists.
5. A missing credential is fatal, not degraded. Never fall back to synthetic data in a mode that claims to be live.
6. No order routing, ever. The kill switch stays a bool wired to nothing.
7. Every new numeric module gets hand-derived test values. "Returns a number" is not a test.
8. Comments explain *why*: the tension, the choice, and what breaks under the alternative.

## What it is not, and known limits

- No order routing, no execution, no simulated fills.
- No persistence. State is the running process; a restart rebuilds from `book.sexp` and the feed.
- One broker (Alpaca, IEX feed on the free tier), one macro source (FRED, `DGS10` by default), one macro factor.
- Not a research platform: no signals, no strategy, no backtest of anything that could make money. `make backtest` validates the *risk model*.
- Nothing is optimised: the engine reports concentration and never suggests weights.
- Options: European only, one flat rate, one vol per contract, no dividends, no implied-vol solve, vega not bucketed by strike, and off in live mode.
- Volatility: equal-weighted or EWMA; GARCH is present but not wired in, for a measured reason.
- Validation windows: COVID and 2022 only. No 2008.
- Positions are a static file. Only prices are live.
- Single droplet, no replica, by design: a second copy of an in-memory graph is a second, differently aged truth.

## How it got here

| When | What |
|---|---|
| Phases 0–4 | Scaffold; the Incremental graph and risk metrics with architecture tests; config, feed-health and factor-exposure nodes; real market data verified live; the dashboard; alerting and the kill switch |
| Phase 5 | Attribution, validation, stress — the three questions a risk number invites |
| Roadmap A–H, through 2026-08-25 | EWMA as a sibling estimator; property tests; crisis backtests (re-specced when 2008 proved unreachable); CI on macOS plus coverage; options and Greeks (which forced the second clock); latency and allocation benchmarks; the bounded trail and sparklines; `quant_notes.md` |
| After the roadmap | The Weibull duration test; GARCH(1,1) implemented and measured out; vega by tenor bucket |
| 2026-08-31 | The server-side spec; the engine containerised behind the proxy it ships behind, verified against a local harness |
| 2026-09-01 → 02 | Droplet provisioned, DNS, first production deploy. Two bugs found and fixed: a fresh clone has no `book.sexp` (gitignored), and `deploy.sh` sourced its env file into bash, which turned the `$$` in the bcrypt hash into process IDs. Smoke suite green. README gained *Watching it* |

Plans and specs live under [`superpowers/`](superpowers/): the readable-front-door
design (the README rewrite), the eight-phase roadmap (marked complete, with its three deviations
recorded), and the deployment design.

## Next

**Immediately, and blocked only on the owner:** the live host. Install the
three credentials at `/etc/ohcamel/live.env` on the droplet, root-owned and
0600, per `deploy/live.env.example`, then `deploy/deploy.sh --live`. The
Alpaca keys are the owner's existing pair. Another project of the owner's used
to share them, which mattered because a free account allows one stream; that
project is inactive, so there is no contention. Then the production smoke suite
with `--live`, which closes the deployment spec.

**Direction, decided 2026-09-01:** a *standalone* tool first — something a
person other than the author can point at *their* book — then integration
into the owner's own trading stack later. The first design is therefore
*submit a book, get a report*: post positions, receive the full report (VaR
and ES under each estimator, the Euler decomposition, the scenario suite,
validation) against real market data using the operator's keys, with nothing
persisted. It is architectural — one graph per request instead of one graph —
and it introduces a mutating route, which the deployment spec's non-goals
excluded only *as a side effect of deploying*. It has not been designed; it
starts with a spec, not code.

**Where that work now lives (2026-09-02).** The standalone direction became
its own repository, [`ohcamel-alpha`](https://github.com/ajaiupadhyaya/ohcamel-alpha):
OhCamel linked as a library and left exactly as it is, Five Dollar Quant's
validation battery as a package, and an OCaml core between them that enforces
a signal contract, will run pre-trade checks on a fork of this engine's graph,
and simulates fills. Its Phase 0 is done; its spec records why paper execution
lives there and not here. What this repository will gain, when that project
reaches its Phase 2, is one pure function: proposed fill in, breached limits
out, through `Graph.fork` — the read-only calculation invariant 6 permits.

**After that, in rough order of leverage:**
- Reading positions from the owner's Alpaca account, and a *pre-trade check*
  — post a proposed fill, fork the graph, report which limits it would breach,
  discard the fork. The desk-realistic function, inside every invariant.
- Live options risk, if Alpaca's options snapshots (implied vol and Greeks)
  are available on the account's tier. Verify before planning on it.
- The engine validating *itself*: persist daily forecasts and realised P&L,
  run the coverage battery on the live track record, show the Basel zone on
  the dashboard. Needs persistence, which is a separate argument to have first.
- Build the image in GitHub Actions and push to a registry so the droplet only
  pulls: deploys drop from a minute to seconds, and the box can shrink to the
  $12 plan.

## Operating it

All on the droplet, as the deploy user, from `~/OhCamel`.

```
# a fresh box, once, as root (idempotent; prints the next steps when done)
ssh root@DROPLET 'bash -s' < deploy/provision.sh

# redeploy -- pull FIRST, then run. deploy.sh pulls too, but bash reads a
# script as it goes, so a script that replaces itself mid-run keeps executing
# the old text; pulling first means the version that runs is the one you meant
git pull --ff-only && deploy/deploy.sh          # public demo
git pull --ff-only && deploy/deploy.sh --live   # plus the live host

# look
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail 100 [caddy|ohcamel-demo|ohcamel-live]
deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com [--live https://live.ohcamel.ajaiupadhyaya.com]

# change the book without a rebuild: edit book.sexp, then
docker compose -f deploy/docker-compose.yml restart ohcamel-demo
```

Things not to do: delete the `caddy_data` volume (it holds the certificate and
the ACME account; Let's Encrypt rate-limits re-issuance); add `ports:` to an
engine service (the firewall will not save it — smoke assertion 6 checks from
outside); source `deploy/.env` into a shell (the `$$` becomes a PID — see the
spec); commit `book.sexp` or any `.env`.

Credentials: the live host's basic-auth *hash* is in `deploy/.env` on the
droplet, the password is the owner's. Alpaca and FRED keys go only in
`/etc/ohcamel/live.env`, never in the repository, never in the Docker build
context, never in an image layer.
