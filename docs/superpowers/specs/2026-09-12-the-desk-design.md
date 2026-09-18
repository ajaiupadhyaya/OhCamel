# The desk — design

*Approved 2026-09-12 by the owner ("do whatever you recommend"), from the
proposal of the same date, whose ten recommendations are the decisions in §1.
This file is the binding authority for every phase plan written under it:
where a plan and this file disagree, this file wins; where this file is
silent, the plan's ruling stands and is recorded in that plan's ledger. §8
lists every place this design departs from the proposal, and why.*

---

## 0. What changes

OhCamel today computes risk on a book typed into a file and refuses three
things: trading, persistence and strategy. This design keeps the risk kernel
exactly as strict as it is and builds the rest of a desk around it, in the
same repository and the same process:

```
  research ──▶ signal ──▶ size ──▶ rules ──▶ gate ──▶ order ──▶ venue ──▶ fill
     ▲          (A3)      (A3)     (A2)    (A2,lib)   (A2)   (A1/A2)      │
     │                                                                    │
     └── validation (A5) ◀── sessions, TCA (A1/A2) ◀── positions, cash ◀──┘
```

Every arc is real on the live host against Alpaca's **paper** account, and
every arc is visible on the public demo host against a simulated venue that
says it is one.

## 1. Decisions

| | Decision | Reason |
|---|---|---|
| Q1 | `ohcamel-alpha`'s OCaml core, contract and research layer fold into this repository; Alpha is archived with a pointer | Alpaca's free plan allows one market-data stream per account, held by the live engine, so execution must share its process; and a reviewer reads one repository |
| Q2 | Invariant 6 becomes a library boundary enforced by dune (§2) | "the risk kernel cannot place an order" checked on every build is stronger than a sentence about a repository |
| Q3 | SQLite, one file, WAL, on a named volume, through the `sqlite3` opam package | one writer on one box; the whole record is one file that `.backup` copies |
| Q4 | Free Alpaca plan; SIP and more symbols stay a configuration change | the 30-symbol cap binds before cost does |
| Q5 | US equities and ETFs traded; options risk-only; no crypto | one microstructure done properly |
| Q6 | First hypothesis: Faber (2007), 10-month moving average on SPY and TLT, pre-registered; a manual ticket exists | published, one parameter, cheap to falsify; a desk has a ticket |
| Q7 | A single-owner desk; multi-tenant is not planned | supersedes the 2026-09-01 "submit a book" direction |
| Q8 | All of Figure 1's changes (§4), in three cuts | |
| Q9 | Track W runs alongside the backend from the first day | independent code paths |
| Q10 | No spending change | |

## 2. Invariants

The eight from `docs/handoff.md` §2 stay in force, with 6 rewritten. Five are
added.

1. Every dependency is a graph edge.
2. No second implementation of exposure, equity or limit arithmetic.
   Counterfactuals — stress, and now the pre-trade gate — go through
   `Graph.fork`.
3. Units stay abstract.
4. Pure numerics stay pure.
5. A missing credential is fatal, not degraded.
6. **The risk kernel cannot place an order.** Library `ohcamel` (`lib/`)
   holds no trading client, no order state, no journal and no persistence.
   Execution lives in library `ohcamel_desk` (`desk/`), which depends on
   `ohcamel`; dune rejects the reverse edge as a cycle, and CI greps `lib/`
   for the trading host and the orders path. The pre-trade gate is in `lib/`
   because it is a read-only calculation on a fork — the category the old
   invariant always permitted.
7. Every numeric module gets hand-derived test values.
8. Comments explain why.
9. **Paper only, by construction.** The trading host is the constant
   `paper-api.alpaca.markets`. A trading key that does not begin `PK` is
   refused before any request is made. Nothing — flag, environment variable,
   book field — can point the desk at a live-money endpoint.
10. **Journal before wire.** An order is in the journal as `Pending_submit`
    before the request that submits it is sent. A submission whose outcome is
    unknown (timeout, 5xx, dropped connection) becomes `Submit_unknown` and is
    resolved by asking the venue for its client order id — never by sending
    it again.
11. **Fills are facts.** A fill is recorded even when it arrives in a state
    that makes the transition illegal (a fill after a cancel was
    acknowledged); the anomaly is recorded beside it. A position is set from
    the venue's reported position, not incremented, so a replayed event
    cannot double a position.
12. **Every trade passes the engine first.** Every order — manual, signal or
    demo — is checked by the rules and then against the limits on a fork of
    the live graph before it exists at a venue. A proposal that creates a
    breach, or worsens one that exists, is rejected naming the limits.
13. **Persistence is the journal, and only the journal.** One SQLite file.
    The engine's root filesystem stays read-only; the in-memory trail stays in
    memory; nothing else writes to disk.

## 3. Architecture

### 3.1 Libraries

```
lib/     ohcamel        the risk kernel. Gains lib/gate.ml (A2) and, for the
                        figure, per-node change reporting and cost classes (W1)
desk/    ohcamel_desk   depends on ohcamel. ids, order, journal, venue
                        interface, sim venue, Alpaca paper client, trade
                        updates stream, book sync, session close, rules, OMS,
                        TCA, contract, sizing, desk routes
bin/                    links both; `serve` and `demo` attach a desk
research/               Python (uv), from Alpha: fdq's battery, signal emission
interface/              the signal contract, from Alpha
```

`server.ml` cannot learn a desk type. It gains three generic seams, and the
desk plugs into them from `bin/main.ml`:

- `?extensions` — extra routes, each `(path, purpose, handler)` where the
  handler receives the request and its body. The 404 body lists them with the
  rest.
- `?frame_extra : unit -> (string * Yojson.Safe.t) list` — fields appended to
  every frame and snapshot (the desk's summary object).
- `Server.notify : t -> unit` — request a frame when state outside the graph
  changes (an order acknowledged, a cancel).

### 3.2 Data plane

| Source | Transport | Writes | Phase |
|---|---|---|---|
| Alpaca IEX trades | market-data websocket (exists; the one stream) | `price[S]`, `last_tick[S]` | — |
| Alpaca daily bars | REST at startup (exists) and at each session close | `returns[S]`, opening marks | A1 roll |
| FRED DGS10 | REST poll (exists) | `factor_returns` | — |
| Alpaca paper account, positions, orders, clock | REST, `paper-api.alpaca.markets` | `qty[S]`, `cash`; the journal | A1 |
| Alpaca latest quote | REST at submission | the order's arrival quote | A2 |
| Alpaca `trade_updates` | websocket on the paper host, binary frames of JSON; not the market-data stream | order events, fills | A2 |
| ETF factor bars | REST daily bars | `returns_long[S]`, factor series | A4 |
| Alpaca option snapshots (indicative) | REST poll | `implied_vol[o]` | A4 |

### 3.3 The book: universe from the file, quantities from the venue

`book.sexp` keeps its format and meaning, with one change of role. Its
`positions` list declares the **universe** — symbol and sector — and the limits
and alerts are read as before. When a desk is attached to a venue, the file's
`qty` and `cash` are ignored: quantities and cash come from the venue, and a
universe name the venue holds no position in is zero.

A venue position in a symbol outside the universe is **unmanaged**. It is named
on the page, on `/api/desk` and in the log; it is not in the graph, because the
graph's shape is fixed at construction; and every order in it is refused by
rule. Adding a name is an edit to the book and a restart. A desk trades a
mandate, not whatever the account happens to hold.

The universe may hold at most 30 names on the free plan; the engine refuses a
larger book at startup and names the cap.

An optional `desk` block configures the rules (§3.7) and the spread table TCA
compares against:

```
(desk
 ((trading enabled)                 ; enabled | disabled
  (max_order_notional 25000.0)
  (max_adv_participation 0.01)      ; of 20-day average daily volume
  (price_collar 0.05)               ; a limit price within 5% of the mark
  (duplicate_window_s 10)
  (max_open_orders 20)
  (spread_bps_default 5.0)
  (spread_bps ((SPY 2.0) (QQQ 3.0) (TLT 3.0)))))
```

Absent, the defaults above apply with `trading disabled`.

### 3.4 The venue interface

A record of closures, so a venue is chosen at run time and a test builds one
in a line:

```ocaml
type t = {
  name : string;                                    (* "alpaca-paper" | "simulated" *)
  account : unit -> Account.t Or_error.t Deferred.t;
  positions : unit -> Position.t list Or_error.t Deferred.t;
  open_orders : unit -> Venue_order.t list Or_error.t Deferred.t;
  find_order : Client_order_id.t -> Venue_order.t option Or_error.t Deferred.t;
  submit : Order.Request.t -> Submission.t Deferred.t;   (* Accepted | Rejected | Unknown *)
  cancel : Venue_order_id.t -> unit Or_error.t Deferred.t;
  clock : unit -> Session_clock.t Or_error.t Deferred.t;
  latest_quote : Symbol.t -> Quote.t option Or_error.t Deferred.t;
  daily_bars : Symbol.t list -> days:int -> Bar.t list Symbol.Map.t Or_error.t Deferred.t;
  updates : unit -> Update.t Pipe.Reader.t;
}
```

Two implementations:

- **`Alpaca_paper`** — REST on the constant paper host, the `trade_updates`
  websocket, and market-data REST for quotes and bars. Parsers are pure and
  tested against payloads written from Alpaca's documentation; transport is
  thin.
- **`Sim_venue`** — in process and deterministic given a mark source and a
  clock. A market order fills after a fixed latency at the mark moved against
  the order by the book's half-spread for that symbol; a limit order fills
  when the mark crosses it; the account is marked from the same source. It is
  the demo host's venue and every test's.

### 3.5 The journal

SQLite, WAL mode, `synchronous=NORMAL`, schema version in `meta`, migrations in
code. Writes are synchronous on the Async thread: a single-row insert in WAL
mode costs tens of microseconds, and moving it to a thread would put the write
and the wire in a race the invariant exists to exclude. Times are RFC 3339 UTC
text; prices and quantities are REAL.

```sql
meta          (key TEXT PRIMARY KEY, value TEXT NOT NULL)
orders        (client_order_id TEXT PRIMARY KEY, venue_order_id TEXT,
               source TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
               qty REAL NOT NULL, kind TEXT NOT NULL, limit_price REAL,
               tif TEXT NOT NULL, state TEXT NOT NULL,
               filled_qty REAL NOT NULL DEFAULT 0, avg_fill_price REAL,
               decision_price REAL NOT NULL, arrival_bid REAL, arrival_ask REAL,
               verdict TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)
order_events  (seq INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL,
               at TEXT NOT NULL, event TEXT NOT NULL, state_after TEXT NOT NULL,
               anomaly TEXT, detail TEXT)
fills         (execution_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
               symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL,
               price REAL NOT NULL, at TEXT NOT NULL, position_qty REAL)
sessions      (date TEXT PRIMARY KEY, equity_close REAL NOT NULL, cash_close REAL NOT NULL,
               gross_close REAL NOT NULL, net_close REAL NOT NULL, recorded_at TEXT NOT NULL)
marks         (date TEXT NOT NULL, symbol TEXT NOT NULL, close REAL NOT NULL,
               qty REAL NOT NULL, PRIMARY KEY (date, symbol))
forecasts     (date TEXT NOT NULL, estimator TEXT NOT NULL, confidence REAL NOT NULL,
               var_fraction REAL, var_notional REAL, es_notional REAL,
               PRIMARY KEY (date, estimator))
signals       (strategy TEXT NOT NULL, sequence INTEGER NOT NULL, as_of TEXT NOT NULL,
               received_at TEXT NOT NULL, verdict TEXT NOT NULL, rule TEXT,
               detail TEXT, document TEXT NOT NULL, PRIMARY KEY (strategy, sequence))
alerts        (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
               limit_name TEXT NOT NULL, line TEXT NOT NULL)
```

The live host's journal is `/data/desk.db` on the named volume `desk_data`,
the one writable mount in the engine container. The demo host's journal is
`:memory:`, and its page says so.

At startup the `sessions` table rebuilds the `equity_history` cell, so
drawdown survives a restart — persistence used for the purpose argued for it.

### 3.6 The order lifecycle

A pure state machine, `Order.apply : t -> Event.t -> t * anomaly option`.
Orders are whole shares, market or limit, time in force `day`, regular hours.

| State | Meaning | Terminal |
|---|---|---|
| `Rejected_pre_trade` | failed a rule or the gate; never reached a venue | yes |
| `Pending_submit` | journaled; request not yet answered | |
| `Submit_unknown` | request sent, outcome unknown; must be resolved by lookup | |
| `Submitted` | the venue answered 200 with an order id | |
| `Accepted` | the venue reported `new`, `accepted` or `pending_new` | |
| `Partially_filled` | at least one fill, less than the quantity | |
| `Filled` | filled quantity equals the order quantity | yes |
| `Pending_cancel` | a cancel was requested | |
| `Cancelled` | the venue confirmed the cancel | yes |
| `Expired` | the venue expired it | yes |
| `Rejected_by_venue` | the venue refused it | yes |
| `Failed` | an unknown submission the venue confirms it never received | yes |

A fill in any non-terminal state advances filled quantity and the
quantity-weighted average price, and moves to `Partially_filled` or `Filled`.
A fill in a terminal state leaves the state and records the anomaly `fill
after <state>`. A fill that takes filled quantity past the order quantity is
recorded with the anomaly `overfill`. A fill whose execution id was already
applied changes nothing. Every other illegal transition leaves the state and
records `illegal: <event> in <state>`.

### 3.7 Pre-trade: rules, then the gate

**Rules** (`desk/rules.ml`, pure). Every failing rule is reported, not only
the first: someone fixing a ticket needs the whole list.

| Rule | Passes when |
|---|---|
| `universe` | the symbol is in the book's universe |
| `whole_shares` | the quantity is a positive integer |
| `trading` | the book enables trading and the desk is enabled |
| `kill_switch` | the kill switch is not tripped |
| `session` | the venue's clock says the regular session is open |
| `mark` | the symbol has a mark and its feed is not stale |
| `collar` | a limit price is within `price_collar` of the mark |
| `notional` | quantity × mark ≤ `max_order_notional` |
| `adv` | quantity ≤ `max_adv_participation` × 20-day ADV; unknown ADV fails |
| `duplicate` | no order with the same symbol, side and quantity within `duplicate_window_s` |
| `open_orders` | fewer than `max_open_orders` orders are open |

**The gate** (`lib/gate.ml`). `Gate.check graph ~fills` forks the graph,
applies each proposed fill with `Graph.apply_fill` at its decision price (the
mark for a market order, the limit price for a limit order), stabilizes the
fork, reads every limit before and after, and destroys the fork. It reports,
per limit: **created** (clear before, breached after), **worsened** (breached
before and after, observed moved away from the threshold), **cleared**, and
**unevaluable after**. A verdict fails on any created or worsened breach. A
trade that reduces an existing breach passes: a desk must be able to trade out
of one. A signal's rebalance is gated as one set of fills and passes or fails
as a unit.

### 3.8 The kill switch

Tripping it — by a limit it trips on, or by hand — makes the `kill_switch`
rule fail every proposal and cancels every open order the desk owns, each
cancellation journaled. It does not flatten positions. Resetting it is a
deliberate request (§3.11) on the live host. On the demo host the switch
resets itself 90 seconds after the limit that tripped it clears, and the page
says that this happens only on the demo. The topology's `kill_switch` entry is
`wired_to: "desk.submit"` whenever a desk is attached.

### 3.9 Transaction cost analysis

For side sign s (+1 buy, −1 sell), fill price p, decision mark d, arrival bid
b and ask a, mid m = (a + b)/2:

- implementation shortfall, bps = s · (p − d) / d · 10⁴
- delay, bps = s · (m − d) / d · 10⁴
- arrival slippage, bps = s · (p − m) / m · 10⁴
- quoted half-spread, bps = (a − b) / 2 / m · 10⁴
- versus model, bps = arrival slippage − the book's half-spread for the symbol

Shortfall equals delay plus arrival slippage to first order; both are
reported. Positive is cost. Per order, fills are quantity-weighted.
Aggregates are count, mean, median and quantity-weighted mean, overall and by
symbol. Reference: Perold (1988). On the paper account every number measures
Alpaca's fill simulator, and the page says so.

### 3.10 The session close

Scheduled from the venue's clock: five minutes after `next_close`.

1. Fetch the session's daily bar for every universe symbol; push each
   close-to-close return into `returns[S]` with `Graph.push_return`.
2. `Graph.mark_equity`.
3. Journal the session row, one mark per symbol, and one forecast per
   estimator (historical, parametric, EWMA) at the engine's confidence.

Idempotent per date. On the demo host a session closes every twenty synthetic
bars, dated from a synthetic calendar the page labels.

This fixes a defect found while writing this design: the live engine has
never pushed a return or marked equity after startup, so its VaR window froze
at the backfill and its drawdown had no history.

### 3.11 HTTP

Read routes, on both hosts: `/api/desk` (status, venue, account, managed and
unmanaged positions, open and recent orders with verdicts, recent fills with
TCA), `/api/desk/tca`, `/api/desk/sessions`.

`POST /api/desk/preview` runs the rules and the gate on a proposed order and
creates nothing. It is on both hosts: the public demo lets anyone ask what a
trade would do to its limits.

Mutating routes: `POST /api/desk/orders`, `POST /api/desk/cancel`,
`POST /api/desk/kill`, `POST /api/desk/kill/reset` (body
`{"confirm":"reset"}`). On the demo host each answers 405 with a sentence. On
the live host each requires the header `X-OhCamel-Desk: 1` and either
`Sec-Fetch-Site: same-origin` or an `Origin` equal to the request's host, or
it answers 403; Caddy's basic auth is the authentication. A cross-site form
cannot set the header, and the live host publishes no CORS header, so a
foreign page cannot make the browser send it.

Every frame gains a `desk` object: status, venue, trading, kill switch,
equity, cash, session P&L, open order count, and a `version` that increments
on every journal write. The page fetches `/api/desk` when the version moves,
as it fetches `/api/history` today.

### 3.12 Signals (A3)

Alpha's signal contract v1 and rules R1–R7 move to `interface/` and
`desk/contract.ml` with their tests and examples. Alpha's Phase 1 plan runs
here: EXP-A01 (Faber 2007 on SPY and TLT) pre-registered and committed before
any run, fdq's walk-forward, DSR and PBO plus PSR, stationary-block bootstrap,
regime and cost-sweep gates, a manifest, and a report that leads with the
gates. The gates are the charter's and are applied literally.

A research service (`ohcamel-research`, compose profile `live`) runs after the
close, refreshes bars and writes a signal whose `validation` block is copied
from the manifest into `signals/` on a shared volume. The desk reads new files
from that directory once a minute and judges them R1–R7; a signal that fails
R6 is recorded as advisory with the rule named and is never sized. A passing
signal's weights become whole-share targets against equity, rounded toward
zero; the difference from current positions is one rebalance, gated as a unit
and submitted as market-on-open orders.

R8 (the data hash) is deferred until the hash recipe no longer depends on how
two languages print a float; the page says R8 is not enforced.

### 3.13 Risk depth (A4)

- `returns_long[S]`: a 250-observation window beside the 60-observation VaR
  window, so the README's published tables stay true.
- `lib/factor_model.ml`: OLS betas with intercept on ETF-proxy factors (market
  SPY; size IWM − SPY; value IWD − IWF; momentum MTUM − SPY; rates ΔDGS10),
  factor covariance, portfolio exposures Bᵀw, systematic variance bᵀΣ_f b,
  idiosyncratic Σ wᵢ²σ²_ε,ᵢ, and an Euler split across factors. A
  `Factor_exposure` limit kind.
- `lib/liquidity.ml`: 20-day ADV, days to liquidate at a participation rate,
  square-root impact (Tóth et al. 2011) and a liquidity-adjusted VaR (Bangia
  et al. 1999) as a sibling to VaR, never a replacement.
- Option marks from Alpaca's indicative snapshots into `implied_vol[o]` for
  contracts declared in the book; the engine computes its own Greeks; Alpaca's
  are a cross-check column; every cell says *indicative*.
- GARCH(1,1) wired on `returns_long` as a third sibling parametric estimator,
  with `make garch`'s finding printed beside it.
- Cornish–Fisher VaR as a sibling to parametric VaR.

### 3.14 Self-validation (A5)

From `forecasts` and `sessions`: a session's loss is the fall in equity close
to close, net of cash flows; an exceedance is a loss beyond the previous
session's VaR notional. Kupiec, Christoffersen, conditional coverage, the
duration test, the burst count and the Basel zone run on that record through
the existing `Var_backtest` functions. The page reads "session n of 250" and
prints no verdict before 60 sessions. `ohcamel replay DATE` prints a session's
orders and fills from the journal.

### 3.15 Operations (A6)

The image is built in GitHub Actions on main and pushed to GHCR; the droplet
pulls. A nightly `.backup` of the journal. The smoke suite gains the desk:
status on both hosts, the paper account reachable on the live host, the
mutating routes 403 without the header on the live host and 405 on the demo.

## 4. Track W: Figure 1 and the site

Figure 1 stays hand-drawn SVG from `/api/graph`, with no libraries. It gains:

1. **The wave.** Lit nodes light in rank order, 36 ms per rank, and each lit
   edge draws itself along its length in the same order, so a tick is seen
   travelling from its cell to the limits, and seen not reaching `covariance`.
2. **Ran versus changed.** The frame carries the named nodes and cells whose
   value changed (Incremental's `on_update`, after cutoff). Ran and changed
   is gold; ran and cut off is a hollow ghost. The cutoffs become visible.
3. **The origin.** The cell a frame set pulses.
4. **Session heat.** Edge weight and ink grow with lifetime run counts from
   `/api/heat`, so the drawing becomes a long exposure of where the work goes.
5. **Cost in the glyph.** Every named node carries an asymptotic cost class
   in the topology (`cost`), from the code, labelled as such; a node's rule
   weight encodes it.
6. **Bundled edges.** Edges leave a node through a stub into its column's
   gutter and arrive through a stub from the target's gutter, so fans share
   trunks.
7. **A legend and a caption** set in a system serif stack.
8. **Poster mode.** The figure full-screen on a dark ground; Esc leaves.
9. **The desk bands** (with A2 and A3): orders, fills entering `qty[S]`,
   signals entering the desk, and the kill switch's dotted line connected to
   `desk.submit`.

The site becomes a desk with a navigation: **Desk** (`/`: account, session
P&L, Figure 1, positions with risk share, blotter, fills, the ticket on the
live host and the what-if preview on both), **Risk** (the ledger, stress,
factors, liquidity, options), **Research** (strategies, manifests, gates,
advisory signals, backtest against live), **Execution** (TCA), **Argument**
(the essay, moved intact) and **Ops**. One binary, no external assets, both
colour schemes.

## 5. Phases

Each phase: a plan, subagent-driven execution with a review per task and a
whole-branch review, merge to main, deploy both hosts, `docs/status.md`
updated.

| Phase | Branch | Delivers | Accepted when |
|---|---|---|---|
| W1 | `desk/w1-figure` | §4 items 1–8 | both hosts show them; tests pin `changed`, `cost` and `/api/heat` |
| A1 | `desk/a1-record` | `desk/`, journal, venue interface, sim venue, Alpaca paper read side, book sync, session close, `/api/desk`, the volume | a restart keeps the equity trail; the live host shows the paper account; a session row is written; unmanaged names are shown |
| A2 | `desk/a2-orders` | rules, gate, OMS, trade updates, reconciliation, kill switch wired, the routes and their protection, preview, TCA, ticket and blotter | a hand-computable three-fill P&L with costs; an order breaching `tech-cap` rejected naming it; a tripped switch rejects and cancels; a process killed mid-order reconciles on restart; one paper order filled on the live host |
| W2 | `desk/w2-site` | the site as a desk (§4) | every page on both hosts; the essay unchanged |
| A3 | `desk/a3-signals` | contract, research, EXP-A01, intake, sizing, research service | the hypothesis committed before the run; a verdict led by the gates; an advisory signal shown with its rule |
| A4 | `desk/a4-depth` | §3.13 | identities to 1e-9; hand-derived values |
| A5 | `desk/a5-validation` | §3.14 | session n of 250 on the page |
| A6 | `desk/a6-ops` | §3.15 | a deploy that pulls; a backup that restores |

## 6. Testing

Hermetic, as today: the sim venue, `:memory:` journals, and payloads written
from Alpaca's documentation stand in for the network. Hand-derived values
beside every numeric assertion. Properties: no fill is ever lost by the state
machine; a journal round trip is the identity; the gate never passes a
proposal whose fork creates a breach. The recomputation sets `test_graph.ml`
pins are unchanged by the desk, which only writes cells; the topology pins
change deliberately where §3.8 says.

## 7. Limits, stated

- Marks and arrival quotes are IEX, a single venue: last print and IEX BBO,
  not the consolidated tape or the NBBO.
- Paper fills are Alpaca's simulation; paper TCA measures the simulator.
- At most 30 names on the free plan.
- Whole shares; market and limit; day orders; regular hours.
- The live VaR record has no statistical power for months.
- Alpaca's history begins in 2016; the crisis windows stay on the cache.
- Signals trade ETFs only, because the data source has no delisted names.

## 8. Departures from the proposal

1. **Mutating routes are protected by an origin check and a custom header
   behind Caddy's basic auth, not a bearer token.** A token would have to be
   delivered to the page, which is served to anyone past the password, and
   would add a secret to rotate without adding protection against a
   cross-site request.
2. **No embedded typeface.** A system serif stack costs no bytes, no download
   and no licence.
3. **Long-window estimators use a separate 250-observation window.** The VaR
   window stays 60, so the README's published tables stay true.
4. **The universe stays declared in the book.** Quantities and cash come from
   the venue. The graph's shape is fixed at construction and its topology is
   memoised.
5. **Trading keys may differ from data keys** (`ALPACA_TRADING_API_KEY`,
   `ALPACA_TRADING_SECRET_KEY`, defaulting to the data keys). A key the paper
   host rejects disables trading on the page and on `/api/ops`, in words; it
   does not stop the risk engine, which has its own credentials and its own
   fatal rule.
6. **The demo desk trades a simulated venue** with a labelled synthetic
   rebalancer, and its kill switch resets itself 90 seconds after its limit
   clears, so the public page shows trading and halting both.
7. **The daily roll** (§3.10) was not in the proposal; the defect it fixes was
   found while writing this.
8. **W2 shipped five pages, and A3 owns the sixth.** §4's Research page
   (strategies, manifests, gates, advisory signals, backtest against live) is
   A3's deliverable in every part, so W2 wrote no page and no nav link for
   it; A3 adds both, with Figure 1's signals band. Risk's factor-model and
   liquidity sections likewise arrive with A4, which computes them.
