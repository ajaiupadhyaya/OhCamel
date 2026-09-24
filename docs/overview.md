# OhCamel, in brief

*Written 2026-09-12. The [README](../README.md) argues the design at length,
[`quant_notes.md`](quant_notes.md) writes out the math, and
[`status.md`](status.md) is the operator's inventory. This is the short
version: what the project is, what it can do, and where its edges are, for
someone deciding whether to read the rest.*

---

## What it is

OhCamel is a real-time risk and limits engine written in OCaml. You give it a
book (positions, cash and a set of limits) and a market data feed. It gives
back the numbers a risk desk watches: exposure by instrument and sector, VaR
and expected shortfall, beta to a macro factor, drawdown, and which limits are
breached. The numbers stay current as prices move.

The unusual part is *how* they stay current. Most systems like this poll: a
timer fires and the whole book is recomputed. OhCamel treats risk as a
dependency graph, built on Jane Street's
[Incremental](https://github.com/janestreet/incremental) library. A price tick
updates one input, and only what depends on that input is recomputed. A tick
reaches about 25 nodes whether the book holds 10 names or 400. At 400 names
that is about half a millisecond per update, against 53 ms to recompute
everything (`make bench`, M2 Pro).

On top of the headline numbers it answers the three questions a risk number
invites. **Where is the risk?** An exact Euler decomposition across names and
sectors. **Is the VaR any good?** A battery of statistical coverage tests,
run on real crisis data as well as generated data. **What would break the
book?** A scenario suite that runs on a copy of the live graph.

It computes and reports, and a desk built around it places paper orders, each
one judged by the engine first.

## Is the data real?

The prices are real. The portfolio is not.

| Input | Source | Real? |
|---|---|---|
| Stock prices (live host) | Alpaca's market-data websocket on the IEX feed, with a REST backfill for history | Yes |
| Macro factor | FRED: the daily change in the 10-year Treasury yield (`DGS10`) | Yes |
| Crisis backtests | Adjusted daily closes from Yahoo Finance, committed under [`crisis/`](crisis/) | Yes |
| Positions | [`book.example.sexp`](../book.example.sexp): long AAPL, MSFT, NVDA and JPM, short XOM and CVX, SPY and TLT held at zero for the signal strategies, $1M cash | No. It is an example book |
| Public demo | A generated feed, so it runs with no keys and outside market hours | No, on purpose |
| Options | A generated volatility surface, labelled synthetic wherever it prints | No, and switched off in live mode |

Checked on 2026-09-12: the live host's last session delivered 80,679 trades
from Alpaca with none rejected and no reconnects, and the ticks stopped at the
4 pm close. IEX is a single exchange with a small share of US volume, so a
mark is the last IEX print rather than the consolidated tape.

Positions come from a file by design, unless `serve` or `run-live` runs with
an Alpaca paper key: then the desk reads the paper account once a minute and
treats its quantities and cash as the book's, while the file still declares
the universe, the limits and the alerts. Otherwise, Alpaca supplies only the
marks and the file says what is held.

## What it computes

| Area | What you get | Code |
|---|---|---|
| Exposure | Per-instrument and per-sector exposure, gross and net, weights, equity, drawdown from peak | [`graph.ml`](../lib/graph.ml) |
| Risk measures | Historical VaR and expected shortfall at 95%; parametric VaR from an equal-weighted *and* an EWMA (λ = 0.94) covariance, side by side; portfolio beta to the macro factor | [`risk_metrics.ml`](../lib/risk_metrics.ml), [`vol_estimators.ml`](../lib/vol_estimators.ml) |
| Attribution | Marginal, component and standalone risk per name and sector. Contributions are signed, so a hedge shows negative, and component VaRs sum exactly to portfolio VaR. Also a diversification ratio and a residual check | [`attribution.ml`](../lib/attribution.ml) |
| Validation | Kupiec, Christoffersen independence, conditional coverage, a Weibull duration test, the Basel traffic light and a 21-session burst count, all over rolling point-in-time forecasts | [`var_backtest.ml`](../lib/var_backtest.ml), [`validation_report.ml`](../lib/validation_report.ml) |
| Scenarios | Six standard shocks run on a fork of the live graph, reporting P&L and which limits each one breaks | [`stress.ml`](../lib/stress.ml) |
| Options | Black-Scholes European pricing with delta, gamma, vega and theta; delta folded into exposure; vega broken out by tenor | [`options.ml`](../lib/options.ml), [`options_walk.ml`](../lib/options_walk.ml) |
| Limits and alerts | Breaches computed as data; edge-triggered alerts; a kill switch the desk obeys | [`limits.ml`](../lib/limits.ml), [`alerts.ml`](../lib/alerts.ml) |
| Staleness | Last tick per symbol. A stale name is flagged, along with everything computed from it | [`graph.ml`](../lib/graph.ml) |
| Orders | The rules, then the book's limits on a fork of the live graph; the journal before the wire; reconciliation against the venue on restart; each fill's cost in basis points. The public demo previews and takes no order from a visitor | [`rules.ml`](../desk/rules.ml), [`gate.ml`](../lib/gate.ml), [`oms.ml`](../desk/oms.ml), [`tca.ml`](../desk/tca.ml) |
| Signals and research | A contract (R1-R7; R8 not enforced) that judges each signal file against the desk's own session clock; a research service that emits one weight a strategy a trading day from its committed manifest; EXP-A01, the one battery run so far -- both its strategies fail and stay `advisory` | [`contract.ml`](../desk/contract.ml), [`intake.ml`](../desk/intake.ml), [`research/`](../research/) |

A few of these deserve more than a table row.

**Limits** are written in the book file at instrument, sector or portfolio
scope, and there are four kinds. *Gross notional* works at any scope. *VaR*
and *max drawdown* are portfolio-only, because neither adds up across names.
*Component VaR* caps a scope's share of total risk and works at any scope,
because that share does add up. An impossible combination, such as a VaR
limit on one stock, is rejected at startup. The engine also has a fifth kind,
a cap on |gamma| or |vega|, which the options book uses. It can't be written
in a book file yet.

**Alerts and the kill switch.** Alerting is off unless the book turns it on.
When on, an alert fires once when a limit is crossed and clears only after the
value falls back below a set fraction of the limit, so a number resting on the
line doesn't flap. Sinks are the log, a file, a dry run that prints the
payload, or Slack. The kill switch is a separate setting. It trips on limits
you name; the desk then refuses every new order and cancels every open one,
and leaves positions alone. On the live host a person can also halt the desk
by hand.

**Scenarios.** The standard suite:

| Scenario | Shock |
|---|---|
| `broad-selloff` | Everything down 10% |
| `crash` | Everything down 20% |
| `melt-up` | Everything up 15%, because the short leg loses in a rally |
| `vol-regime` | Prices unchanged, volatility doubled |
| `panic` | Everything down 12%, volatility tripled |
| `rate-shock` | The 10-year yield up 100 bp, passed through each name's own beta |

Custom scenarios combine five shock kinds: every name, one name, one sector,
the macro factor, and volatility. Each scenario runs on `Graph.fork`, a copy of
the engine with the shocks written into its inputs. That means there is no
second copy of the exposure or limit arithmetic to drift, and a test checks
that the live book is untouched afterwards.

**Validation on real crises.** The same battery runs over the 2008 financial
crisis (631 sessions), the COVID crash (400) and the 2022 rate shock (401),
with three estimators on each. The joint coverage test rejects none of the
nine. The duration test rejects two, both in COVID, and in 2020 the parametric
model breached ten times in one 21-session stretch. A single passing test is
not reassurance, which is why the battery reports four tests and a burst
count rather than one verdict.

**GARCH, measured and left out.** GARCH(1,1) is implemented and tested but not
used. On the engine's 60-day window its persistence comes back 0.556 ± 0.364
against a true 0.98, which is biased as well as noisy. It becomes defensible at
around 250 observations. `make garch` reproduces the measurement.

**Options.** The Greeks are tested against Hull's textbook values and
put-call parity. Gamma and vega are reported separately from exposure, because
convexity doesn't fold into a dollar number. Vega is also split by time to
expiry, so a calendar spread no longer reads as flat. Theta needed a second
clock: a valuation date that moves only when told to, kept apart from the
staleness clock. Options are European only, with one flat rate, no dividends,
no implied-vol solve and no strike buckets, and they are off on the live host
because there is no options-chain source.

## Running it

It's one binary, and the mode is its first argument. Each mode has a `make`
target, and every target enters the project-local opam switch itself
(`make deps` installs it the first time).

| `make` | Keys? | What you see |
|---|---|---|
| `demo` | No | The web page at `localhost:8080` on a generated feed, which is what the public site runs. One name ticks every 400 ms, CVX never ticks so it goes stale, and one limit is rigged to breach at startup |
| `run` | No | In the terminal: 60 events over a generated book, a breach and recovery, the risk decomposition and the recompute-count table. Then it exits |
| `stress` | No | The scenario suite |
| `backtest` | No | The coverage battery on three generated return series |
| `backtest-crisis` | No | The same battery on the three real crisis windows |
| `options` | No | The options book, its Greeks, the tenor buckets and the two clocks |
| `garch` | No | The measurement behind leaving GARCH out, in about 5 seconds |
| `serve` | Alpaca + FRED | Real prices, with the web page on `localhost:8080` |
| `run-live` | Alpaca + FRED | Real prices, in the terminal |

Also: `make test`, `make coverage`, `make bench` (local only), `make fmt` and
`make doctor`. The live modes read `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and
`FRED_API_KEY` from the environment and refuse to start without them, rather
than falling back to generated data. Positions come from `book.sexp` (copy the
example and edit it) unless the Alpaca key they run with is a paper key; then
the account's quantities and cash replace the file's every minute. The live
modes write their journal to `OHCAMEL_JOURNAL`, by default `desk.db` in the
working directory. A free Alpaca account allows one data stream at a time.

## The pages and the API

The site is six pages compiled into one binary, with no external assets. `/`
is the Desk: the account and its ticket, Figure 1 (the dependency graph,
drawn from Incremental's own node table, lighting the nodes each update
recomputed, with the orders, fills and signals bands the desk writes),
positions and their share of risk, the book's aggregates, the blotter and
fills, and the equity trail. `/risk` holds the limits ledger, the macro
factor and the book's beta to it, the option Greeks, and the scenario suite
behind a button. `/execution` holds the open orders, the cost analysis
overall and by symbol, the session record and the VaR forecasts. `/research`
(added in phase A3) leads with each registered strategy's backtest verdict,
read from the committed EXP-A01 manifests, then its latest signal and how the
desk judged it, then the manifest's own gates -- on the public demo no
strategy is registered, and the page says so, showing the evidence
regardless.
`/argument` is the README's case, moved intact, under the same headings; its
tables are computed by the running process and checked cell by cell against
the numbers the README quotes, so the deployed Linux host reproduces results
written on a Mac. `/ops` shows which build is running, its uptime, and how
much the graph has recomputed.

| Route | What it returns |
|---|---|
| `/api/snapshot` | The whole book as JSON, including the recompute counter |
| `/api/health` | Feed liveness per symbol |
| `/api/stream` | Server-sent events, sent only when the graph actually changes |
| `/api/history` | The last 500 changes, in memory only |
| `/api/stress` | The scenario suite, run on forks of the current book |
| `/api/graph` | The graph's topology |
| `/api/heat` | How often each named node has run since the process started; the drawing's heat |
| `/api/reports` | The backtest, options and scaling reports, computed at startup |
| `/api/reports/garch` | The GARCH study, computed in parallel after startup |
| `/api/ops` | What `/ops` shows, as JSON |
| `/api/desk` | The desk: its venue and whether it could reach it, the account as last read, each book name's quantity at the venue and in the graph, names held outside the book, and the last 30 recorded sessions with the latest forecasts |
| `GET /api/desk/tca` | Costs per fill, overall and by symbol |
| `GET /api/desk/sessions` | The newest 250 recorded session closes |
| `POST /api/desk/preview` | The rules' and the limits' answer to a ticket; creates nothing |
| `POST /api/desk/orders` | A ticket through the rules, the limits, the journal and the venue (live host only) |
| `POST /api/desk/cancel` | Cancel one open order (live host only) |
| `POST /api/desk/kill` | Halt the desk, answer, then cancel every open order (live host only) |
| `POST /api/desk/kill/reset` | Lift the halt; the body must say `{"confirm":"reset"}` (live host only) |
| `GET /api/research` | The registered signal strategies with their sizing and capital fraction, each one's latest judgement, and that R8 (data hash) is not enforced; the demo registers none |
| `GET /api/research/evidence` | EXP-A01's two manifests, embedded at build time and served byte for byte |

Four routes change the desk -- orders, cancel, kill and its reset -- on the
live host only, and only for a request carrying the page's header and either
the browser's `Sec-Fetch-Site: same-origin` label or an `Origin` equal to its
`Host`, which a cross-site page cannot forge (the password decides who else
may send one); on the public demo each answers 405.

## Where it runs

- **Public demo:** [ohcamel.ajaiupadhyaya.com](https://ohcamel.ajaiupadhyaya.com),
  on the generated feed and always on.
- **Live host:** `live.ohcamel.ajaiupadhyaya.com`, the same image on Alpaca and
  FRED, behind a password.
- **Infrastructure:** one DigitalOcean droplet (2 vCPU, 4 GB, $24/month), Docker
  Compose, and Caddy for TLS. Last deployed 2026-09-17 from `e0f5a71`.
- **Deploy check:** a smoke suite runs after every deploy, and a failure fails
  the deploy. The checks that matter are that the recompute counter *advances*
  between two reads and that the stream delivers frames spread over 20 seconds.
  A frozen graph would still serve valid JSON.

## How it's checked

- **<!-- count:ocaml-tests -->663<!-- /count --> tests**, plus <!-- count:scheduler-tests -->30<!-- /count --> scheduler cases in `test/desk_async`, all
  hermetic: no network, no credentials, no waiting on a clock -- the
  scheduler's cases move a clock of their own. Expected values are derived by
  hand beside each assertion, and each suite checks its own count against
  [`verified.ml`](../lib/verified.ml).
- **Property tests** (QCheck) cover the identities over random inputs: Euler
  additivity, component VaR summing to the total, a hedge reducing variance,
  fork isolation, and no lookahead in the backtest.
- **Architecture tests** pin how many nodes a tick recomputes, and fail if the
  staleness clock ever feeds a risk number. That test is the guard against the
  engine quietly becoming a poller.
- **79.9% coverage** of `lib/` and `desk/` together (8,126 of 10,173
  instrumented points, measured 2026-09-20). Read as two modes, not one: 26
  files run 81% to 95%, and the six that reach a network themselves run 36%
  to 65%, because the tests never touch a network.
- **CI on Ubuntu and macOS** for every push: the build, the tests, a formatting
  check, every credential-free mode run end to end, and the README's quoted
  tables reproduced on both platforms.
- **`make research-test`** runs the research layer's own suite separately:
  <!-- count:research-tests -->388<!-- /count --> Python tests, hermetic and offline, checked with `ruff`. Not folded
  into [`lib/verified.ml`](../lib/verified.ml)'s counts, which cover the
  OCaml suites only.

## What it doesn't do

- **Paper trading only.** Orders go to Alpaca's paper account, after the
  rules and the limits; the demo trades a venue simulated in this process
  and takes orders only from its own trader.
- **One journal.** A SQLite journal (`desk/journal.ml`) holds every order,
  its events and its fills, and each session's close, marks and VaR
  forecasts. `serve` and `run-live`
  keep it in a file (`/data/desk.db` on the live host) and restore the
  drawdown trail from it at the first successful sync after startup; the demo
  keeps it in memory, empty at each start. Nothing else survives a restart.
- **Positions.** Positions are a file, and only prices are live — unless
  `serve` or `run-live` runs with an Alpaca paper key, when the desk syncs
  quantities and cash from that account every minute; the book file still
  declares the universe, the limits and the alerts, and anything the account
  holds outside it shows as unmanaged.
- **One of each source:** one broker (Alpaca, IEX feed), one macro source
  (FRED) and one macro factor.
- **Limited options:** European only, and off in live mode.
- **Not a strategy platform, on purpose.** `make backtest` validates the risk
  model, not a trading idea, and nothing is optimised. `research/` does
  validate trading ideas now (phase A3), against the charter's gates, but
  validating and trading are kept apart: every strategy ships `(sizing
  advisory)` by default and no task has ever set one `live`. EXP-A01, the
  one battery run so far, tested two strategies and both fail -- see
  [`research/experiments/EXP-A01/report.md`](../research/experiments/EXP-A01/report.md).
- **One server,** with no replica.

## Built with

OCaml 5.2.1; Jane Street's Core, Async and Incremental; Owl over BLAS and LAPACK
for the linear algebra; cohttp-async, websocket-async and async_ssl for HTTP and
the Alpaca stream; yojson; Alcotest and QCheck; bisect_ppx; core_bench. The
front end is hand-written JavaScript and inline SVG with no libraries. It is
deployed with Docker, Caddy and DigitalOcean.

As of 2026-09-20 (`find DIR -name '*.ml' -o -name '*.mli' | xargs cat | wc
-l`) that comes to about 13,000 lines of OCaml in `lib/`, 1,940 in `bin/`,
26,200 in `test/`, and 5,200 lines of JavaScript, HTML and CSS in `web/`.
The desk library, `desk/`, adds about 9,650 more lines of OCaml (five
`.mli` files included).

## Reading further

- [README](../README.md): the full argument, with the tables and the reasoning
  behind each design choice.
- [`quant_notes.md`](quant_notes.md): every formula, and the function that
  computes it.
- [`status.md`](status.md): where it runs, how to operate it, and what's next.
- [`brief.md`](brief.md): the original brief the project was built from.
