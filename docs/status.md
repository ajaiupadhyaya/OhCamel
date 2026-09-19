# OhCamel — state of the project

*As of 2026-09-02. This is the document to read first when coming back to the
repository after time away, and the one to update when the facts in it change.*

The [README](../README.md) argues: it makes the case for the design with real
numbers and is long because the case is. [`quant_notes.md`](quant_notes.md)
states: every formula in standard notation, cross-referenced to the function
that evaluates it. This file does neither. It is an inventory — what exists,
where it runs, how to drive it, what it will not do, and what comes next — kept
short enough to read in ten minutes and dated so its staleness is visible.
[`overview.md`](overview.md) is the short summary of what the project is and
can do.

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

It computes and reports, and a desk built around it places paper orders:
every one passes the rules and the book's limits first, and the kernel
itself still cannot place one -- a library boundary, invariant 6.

## Where it runs

| | |
|---|---|
| Public demo | **https://ohcamel.ajaiupadhyaya.com** — synthetic feed, no credentials, always on |
| Live host | `https://live.ohcamel.ajaiupadhyaya.com` — same image against Alpaca (IEX) and FRED, behind basic-auth. Up since 2026-09-02; the owner holds the password |
| Host | One DigitalOcean droplet, `s-2vcpu-4gb`, Ubuntu 24.04, nyc3, at `138.197.116.165` |
| Cost | $24/month, metered hourly, capped |
| Proxy / TLS | Caddy, Let's Encrypt, HTTP→HTTPS 308, HSTS. Only Caddy has a host port; the engines are on an internal Docker network |
| DNS | Porkbun. Two A records, `ohcamel` and `live.ohcamel`, on a domain whose apex is unrelated (the owner's portfolio site) |
| Deployed | 2026-09-17, both hosts, from `e0f5a71` (phase A2: the order manager -- the twelve pre-trade rules, the book's own limits re-evaluated on a fork of the live graph, the journal before the wire, reconciliation on restart, the kill switch the desk obeys, costs per fill, and the page's switch, ticket and blotter) -- the demo reports it as `build.git_sha` on `/api/ops`, built `2026-09-17T17:55:08Z` -- verified by the production smoke suite: 26 passed, 0 failed, 0 skipped, and both engines report healthy. On the live host the engine opened `/data/desk.db` with three session closes recorded (the latest 2026-09-16), read the paper account, and its first sync succeeded, which restored those three into the equity trail; it logs `Alpaca paper, read side` and `trading off -- the book does not enable trading`, so the live desk previews and places nothing until `book.sexp` gains a `desk` block with `(trading enabled)`. The public demo serves the whole desk against its simulated venue: its own trader's orders and fills, the costs of each, and a switch that trips on `nvda-cap` and resets itself 90 s after the limit clears. Not yet deployed: `4e04b29`, the documented `desk` block in `book.example.sexp` and the test that parses it. Previous deploy, and the rollback reference: `cf79764` (2026-09-13, phase A1: the journal and the paper-account sync) |

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
crisis windows from a committed cache (`make backtest-crisis`): the 2008
financial crisis (2007-07 → 2009-12), the COVID crash (2019-06 → 2020-12) and
the 2022 rate shock (2021-06 → 2022-12) — adjusted daily closes from Yahoo
Finance via `tools/fetch_crisis_data.py`, committed under `docs/crisis/` and
compiled into the binary, so the mode runs from any directory and inside the
image. Alpaca's own history begins in 2016, which is why the cache exists.

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
by default, and a kill switch the desk obeys: a trip refuses new orders and
cancels open ones.

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
`book.sexp`, which is gitignored; copy `book.example.sexp`. When the Alpaca key
they run with is a paper key, one beginning `PK` (`ALPACA_TRADING_API_KEY` if
that pair is set, else `ALPACA_API_KEY`), the account's quantities and cash
replace the file's every minute. Their journal is written to `OHCAMEL_JOURNAL`,
by default `desk.db` in the working directory, which is also gitignored. A free
Alpaca account allows one concurrent market-data stream.

## The interface

The site is five pages, one binary, one shared shell and client:

- **Desk** (`/`) — the account and its ticket, Figure 1 (with the orders and
  fills bands the desk writes), positions and their share of risk, the
  book's aggregates, the blotter and fills, and the equity trail.
- **Risk** (`/risk`) — the limits ledger, the macro factor and the book's
  beta to it, the option Greeks, and the scenario suite behind a button.
- **Execution** (`/execution`) — the open orders, the cost analysis overall
  and by symbol, the session record and the VaR forecasts.
- **Argument** (`/argument`) — the README's case, moved intact.
- **Ops** (`/ops`) — which build is running, its uptime, and what the
  process has recomputed, with its own masthead and its own stream; W2 gave
  it the site's navigation and its own title, and left its readings as they
  were.

Unauthenticated at the engine, gated at the proxy for the live host. Four
routes change the desk -- `/api/desk/orders`, `/api/desk/cancel`,
`/api/desk/kill`, `/api/desk/kill/reset` -- on the live host only, and only
for a request carrying `X-OhCamel-Desk: 1` and either `Sec-Fetch-Site:
same-origin` or an `Origin` equal to its `Host`; on the public demo each
answers 405.

| Route | |
|---|---|
| `/` | The Desk: the account and its ticket, Figure 1 (the graph from Incremental's node table, lit per frame, drawn rank by rank with cutoffs, heat and cost classes, with the orders and fills bands the desk writes), positions and their share of risk, the book's aggregates, the blotter and fills, and the equity trail. One embedded HTML string, inline SVG, no external assets |
| `/argument` | The README's argument, moved intact: the same headings, the same tables, computed by this process and checked against the README |
| `/risk` | The limits ledger, the macro factor and the book's beta to it, the option Greeks, and the scenario suite behind a button |
| `/execution` | The open orders, the cost analysis overall and by symbol, the session record and the VaR forecasts |
| `/api/snapshot` | The whole book as JSON, including `nodes_recomputed` — the counter that proves the graph is alive |
| `/api/health` | Feed liveness per symbol; `healthy: false` when anything is stale |
| `/api/stream` | Server-sent events, emitted only on an actual graph change (parked on an `Ivar`, not a timer), coalesced over 80 ms |
| `/api/history` | The in-memory trail |
| `/api/stress` | The scenario suite, run on forks of the live book: structured shocks, before and after, breaches with their text, the counter's cost |
| `/api/graph` | The topology: named nodes, edges, ranks, the readers outside the graph. Memoised at startup |
| `/api/heat` | How often each named node has run since the process started; the drawing's heat |
| `/api/reports` | The CLI's reports computed by this process before it listened: scaling probe, synthetic and crisis batteries, options walk. One cached string, about 86 KB |
| `/api/reports/garch` | The 180-fit GARCH study, run on a second domain after listen: `computing` with a count, then `done` with its rows (about 5 s on an M-series laptop) |
| `/ops` | The operator's view: what the process is, how long it has been up, what it has recomputed |
| `/api/ops` | The same as JSON, including the recompute log's distinct and total counts and its hottest nodes |
| `/api/desk` | The desk, served as an extension route: `enabled` with its venue or `disabled` with the reason, the account as last read with `last_sync` and `last_error`, each universe name's venue and graph quantity, the names the account holds outside the universe, the gap between the graph's equity and the venue's, and the journal's newest 30 sessions and latest forecasts, read with bounded queries |
| `GET /api/desk/tca` | Costs per fill, overall and by symbol (both hosts) |
| `GET /api/desk/sessions` | The newest 250 recorded session closes (both hosts) |
| `POST /api/desk/preview` | The rules' and the limits' answer to a ticket; creates nothing (both hosts) |
| `POST /api/desk/orders` | A ticket through the rules, the limits, the journal and the venue (live host only) |
| `POST /api/desk/cancel` | Cancel one open order (live host only) |
| `POST /api/desk/kill` | Halt the desk, answer, then cancel every open order (live host only) |
| `POST /api/desk/kill/reset` | Lift the halt; the body must say `{"confirm":"reset"}` (live host only) |

Each page is assembled at build time from `web/` by its own rule in `lib/dune`; none of the five generated `*_html.ml` modules (`dashboard_html`, `ops_html`, `argument_html`, `risk_html`, `execution_html`) is a committed source file. The 47-line design essay that headed the Desk page is archived verbatim, as an HTML comment, at the head of `web/index.html`, with the successor paragraphs beneath it.

## What is verified

- **460 hermetic tests**, plus fifteen scheduler cases in `test/desk_async` —
  no network, no credentials, nothing waiting on a clock: the scheduler's
  cases move a clock of their own. Expected values are derived by hand with
  the derivation beside the assertion. Seven are worth knowing by name: Euler
  residual, hedge (no stray `abs`), lookahead, stress-fork isolation,
  regime-break, delta-hedged, and two-clocks.
- **Property tests** (qcheck) generalise the identities over random inputs:
  Euler additivity, component VaR summing to portfolio VaR, a hedge reducing
  variance, VaR monotone in confidence, fork isolation, backtest lookahead.
- **Coverage 76.6%** (5,945 / 7,765 instrumented points in `lib/` and `desk/`,
  measured 2026-09-18), with a 60% floor in CI that exists to make deleting
  tests noticeable, not as a target. The number is bimodal by design: the pure
  numeric core is above 85% and the network-IO files run 36% to 64%, because
  exercising them means mocking a broker, which raises the number and
  establishes nothing.
- **CI** on every push, `ubuntu-latest` and `macos-latest`. The macOS leg
  exports the Owl workarounds the Makefile documents, so a green run is
  evidence they still work. Both legs run all six credential-free modes end to
  end. Benchmarks run only on manual dispatch, because shared-runner timings
  are noise.
  Since the extraction phase both legs also run the README-value pins — the
  nine synthetic and nine crisis validation rows, the delta-hedge walk and the
  GARCH truth — so the tables are reproduced on amd64/linux, the droplet's
  platform, as well as on the arm64 macOS the README quotes, to the four
  decimals it prints (run 34488696654, 2026-09-10).
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
| 10 | 63 | 25.6 | 63 |
| 100 | 342 | 25.2 | 342 |
| 400 | 1272 | 26.0 | 1272 |

(The node counts were 58 / 337 / 1267 until the five option singletons —
`gamma_map`, `vega_map`, `portfolio_gamma`, `portfolio_vega`,
`vega_by_bucket` — were added to every graph, whether or not it holds
options. The per-tick column did not move, which is the point of the table.)

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

Size, counted on 2026-09-13: about 11,300 lines of OCaml in `lib/`, 2,000 in
`desk/`, 1,700 in `bin/` and 13,700 in `test/`.

## The invariants

The binding list is §2 of the desk design,
[`superpowers/specs/2026-09-12-the-desk-design.md`](superpowers/specs/2026-09-12-the-desk-design.md):
the eight from [`handoff.md` §2](handoff.md) stay in force with 6 rewritten,
and five are added. A change that ships faster by breaking one is a
regression even if the tests pass. The rewritten one and the five new ones, a
line each (10 to 12 bind the order path, `desk/oms.ml`):

- **6. The risk kernel cannot place an order.** `lib/` holds no trading client, no order state, no journal and no persistence; execution lives in `desk/`, which depends on `lib/`, dune rejects the reverse edge as a cycle, and CI greps `lib/` for the trading host and the orders path.
- **9. Paper only, by construction.** The trading host is the constant `paper-api.alpaca.markets`, a trading key that does not begin `PK` is refused before any request is made, and nothing can point the desk at a live-money endpoint.
- **10. Journal before wire.** An order is in the journal as `Pending_submit` before the request that submits it is sent, and a submission whose outcome is unknown becomes `Submit_unknown`, resolved by asking the venue for its client order id, never by sending it again.
- **11. Fills are facts.** A fill is recorded even when it arrives in a state that makes the transition illegal, with the anomaly beside it, and a position is set from the venue's reported position, not incremented.
- **12. Every trade passes the engine first.** Every order is checked by the rules and then against the limits on a fork of the live graph before it exists at a venue, and one that creates a breach or worsens one is rejected naming the limits.
- **13. Persistence is the journal, and only the journal.** One SQLite file; the engine's root filesystem stays read-only, the in-memory trail stays in memory, and nothing else writes to disk.

## What it is not, and known limits

- Paper orders only, to Alpaca's paper account; the demo's venue is simulated in this process. Whole shares, market and limit orders, day orders, regular hours.
- Persistence is one journal (`desk/journal.ml`, SQLite): every order, its events and its fills, and each session's close, marks and VaR forecasts. `run-live` and `serve` keep it in a file (`/data/desk.db` on the live host) and restore the drawdown trail from it at the first successful sync after startup; the demo's is in memory and starts empty each run. Nothing else — the graph, the rest of the book — survives a restart.
- One broker (Alpaca, IEX feed on the free tier), one macro source (FRED, `DGS10` by default), one macro factor.
- Not a research platform: no signals, no strategy, no backtest of anything that could make money. `make backtest` validates the *risk model*.
- Nothing is optimised: the engine reports concentration and never suggests weights.
- Options: European only, one flat rate, one vol per contract, no dividends, no implied-vol solve, vega not bucketed by strike, and off in live mode.
- Volatility: equal-weighted or EWMA; GARCH is present but not wired in, for a measured reason.
- Validation windows: three US equity episodes, scored at TODAY's six names held at constant weights — what this book would have done, not what the book of the day did.
- Positions are a static file unless `run-live` or `serve` runs with an Alpaca paper key, when the desk reads that account every minute for quantities and cash; the book file still declares the universe, the limits and the alerts, and names the account holds outside it show as unmanaged.
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
| 2026-09-12 | The desk design approved: paper trading around the risk kernel, in phases, with its spec and first backend phase on the `desk/a1-record` branch until they merge. Phase W1 deployed: Figure 1 draws a frame's work rank by rank, a dotted rule where a cutoff held, edge weight from lifetime run counts and rule weight from each node's cost class, with a legend and a poster mode. Phase A1 landed on that branch: the desk library (`ohcamel_desk`, which the kernel cannot depend on); a SQLite journal of each session's close, its marks and a VaR forecast per estimator, recorded by `run-live` or `serve` when they run with a paper key, and in memory by the demo; and, in those paper-key runs, the book's quantities and cash synced from the Alpaca paper account every minute and the return windows rolling at each session's close |
| 2026-09-13 | Phase A1 merged to main and deployed to both hosts from `cf79764`, after its whole-branch review and one fix wave: a record whose write fails is retried until the next session's close is due, a close and the equity trail wait for a current account book so a file book is never journaled, and every request to the paper host is bounded by a timeout. The live engine opened its journal on the `desk_data` volume, read its Alpaca paper account, and its first sync succeeded. Coverage was measured again at 82.0% of the kernel's instrumented points; the desk library is not instrumented yet |
| 2026-09-17 | Phase A2 merged to main and deployed to both hosts from `e0f5a71`, after twenty reviewed tasks, a whole-branch review and one fix wave: the rules and the pre-trade gate on a fork of the live graph; the order manager (the journal before the wire, a timed-out submission resolved by lookup, reconciliation on restart, and no order while the book is not the account's); Alpaca paper's trading half behind a ten-second bound on every request; the kill switch wired to the desk, which refuses new orders and cancels open ones, including one that was already resting when a partial fill arrived; previews for anyone and orders only from the live host's own page; costs per fill; the ticket and the blotter. The fix wave also indexed the journal, because `/api/desk` had been scanning whole tables on the scheduler thread. Acceptance on the live host -- one paper order filled -- waits for the owner: the `desk` block in `book.sexp`, the basic-auth password, and market hours |
| 2026-09-18 | Phase W2 merged to main after nine reviewed tasks, a whole-branch review and one fix wave: the site became five pages under one navigation -- the Desk at `/`, Risk, Execution, the Argument and Ops -- with one shared head and stylesheet and one stream connection per page. The limits ledger moved to `/risk`, which added the macro factor, the option Greeks and the scenario suite behind a button; `/execution` added the open orders, the costs, the session record and the VaR forecasts from the journal; the README's argument moved to `/argument` intact. Figure 1 draws the desk's two bands, orders leaving the engine and fills arriving at `qty[S]`, routed through the figure's own gutters. The Research page was deferred to A3, because everything §4 of the design assigns it is A3's deliverable; the factor model and liquidity stay with A4. 454 tests and seven scheduler cases, coverage 76.6%. Not yet deployed: the live host still serves A2's `e0f5a71` until the owner redeploys |

Plans and specs live under [`superpowers/`](superpowers/): the readable-front-door
design (the README rewrite), the eight-phase roadmap (marked complete, with its three deviations
recorded), and the deployment design.

## Next

**The desk's remaining phases**, in the order the design lays out
(`docs/superpowers/specs/2026-09-12-the-desk-design.md` §5):

- **A3** — signals: Alpha's contract and rules R1–R7 move in, a research
  service, EXP-A01 pre-registered and run, intake and sizing; and the
  Research page and its nav link, with Figure 1's signals band. W2 deferred
  the page here because everything §4 assigns it -- strategies, manifests,
  gates, advisory signals, backtest against live -- is this phase's
  deliverable, and a page before it could only have promised them.
- **A4** — risk depth: the long return window, the factor model, liquidity
  and impact, indicative option marks from Alpaca, GARCH wired in as a third
  estimator, Cornish–Fisher VaR.
- **A5** — self-validation: the coverage battery run on the live VaR record
  from the journal, the Basel zone shown on the page.
- **A6** — operations: the image built and pushed in CI so the droplet only
  pulls, a nightly journal backup, the desk added to the smoke suite.

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

# roll back: deploy.sh always builds origin/main, so a rollback is a revert on
# main, pushed, then the redeploy above -- not a checkout on the droplet, which
# the next deploy's pull would undo

# look
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail 100 [caddy|ohcamel-demo|ohcamel-live]
deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com [--live https://live.ohcamel.ajaiupadhyaya.com] [--expect-sha "$(git rev-parse HEAD)"]
open https://ohcamel.ajaiupadhyaya.com/ops       # which build, how long, what the process is doing; the live host's /ops draws both

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
