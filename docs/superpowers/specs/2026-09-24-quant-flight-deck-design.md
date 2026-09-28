# OhCamel Quant — the Flight Deck

*2026-09-24. Requested by the owner ("a new tab on the dashboard ... that
incorporates the new graphs/visualizations, utilizing proper, real data and
live data"), designed and built without further gates at the owner's
instruction. Branch `quant/deck`. Builds on
[Figure 2](2026-09-24-figure-2-the-scope-design.md), whose targeting computer
it ports into the Quant app beside the two instruments agreed with it (the
radar and the lamp panel).*

## What it is

A tenth page in OhCamel Quant, **Flight Deck** at `/deck`, in the sidebar's
Home group under Markets. It shows one book as three film-style instruments
(after the *Star Wars* (1977) cockpit displays), each read from real data
and refreshed live:

| Instrument | Question it answers | Drawn from |
|---|---|---|
| **A. Targeting computer** | How close is the book to each of its limits? | limit evaluations |
| **B. Radar** | Where is the risk, and what is moving today? | Euler component VaR at live weights, today's moves |
| **C. Lamp panel** | Is the data behind this alive? | the session clock, quote ages per source, the recorder, the engine bridge |

Below the instruments: the **session tape** (the day so far, from the flight
recorder or the page's own trail), the **limits editor**, and the marks table
with provenance.

## Sources — three, one model

A segmented control picks what the deck is flying:

1. **Your portfolio** (default) — the app's global portfolio
   (`usePortfolio`). The page POSTs it to `/api/deck/reading` and polls:
   every 15 s while the US session is open, every 5 min while it is closed.
   Quotes are cached server-side for 60 s (`ttl_intraday_quote_s`), so the
   marks genuinely move about once a minute; the page says so. The session
   tape is the page's own in-memory trail (lost on reload; said on the panel).
2. **Reference book (recorded)** — the server's reference book, identical to
   the app's default portfolio ("Core multi-asset"). The **flight recorder**
   writes a reading for it every 60 s during the session, so its tape is the
   whole day and survives reloads, restarts and viewers.
3. **OCaml engine** — `/api/engine/snapshot` through the existing read-only
   bridge, polled every 2 s. The engine's limits, positions, feed health and
   counters map onto the same model. Where the bridge is off (as on the public
   host today) the deck renders the bridge's own 503 message — nothing
   simulated.

All three are adapted in the browser into one `DeckModel`, so each instrument
has one input shape.

## Data: what is real and what is live

- **Marks**: `MarketData.quotes` — Alpaca snapshots (SIP, falling back to IEX)
  when keys exist, Yahoo chart meta otherwise, stale-served from the Parquet
  cache when a vendor fails, each row with its source and `as_of`. Offline
  (tests, CI) they are the last committed daily closes, and the payload says so.
- **History**: `MarketData.returns` over the portfolio's window, the same
  daily returns every other page uses.
- **Live weights**: `w_i' = w_i (1 + r_i) / (1 + Σ_j w_j r_j)` with `r_i` each
  holding's move since the previous close (`change_pct` of its quote); cash is
  `1 − Σw` at 0 %. A holding with no quote keeps its close weight and is named
  in `notes`.
- **Risk**: 1-day parametric VaR/ES at `alpha` (default 0.95) from the EWMA
  covariance (λ 0.94, RiskMetrics) at the live weights, with its Euler split
  (`risk.decomposition.euler_parametric`), plus historical VaR/ES of the
  close-weight return series as a cross-check. Both in fractions of equity and
  in USD (× notional).
- **Day P&L**: `Σ w_i r_i` (fraction) and × notional (USD).
- **Drawdown**: of the constant-weight wealth path over the window, with
  today's live move appended as the last point.
- **Session clock**: Alpaca's `/v2/clock` when keys exist (holidays and early
  closes included); otherwise the regular session from New York wall clock
  (09:30–16:00, Mon–Fri) with a note that exchange holidays are not known
  without it.

Nothing is invented: a quantity that cannot be computed is `null` with a
reason, and a limit that depends on it is **unevaluated**, never "fine".

## Limits

Limits are the viewer's policy, not market data, so they have editable
defaults, stored in the browser (`localStorage["ohcamel.deck.limits.v1"]`)
and sent with each reading. (The default book is fully invested, so a gross or
net cap of 100 % would sit at exactly 100 % for ever; 150 % and 110 % are
ordinary policy lines.) Kinds:

| kind | observed | unit | default |
|---|---|---|---|
| `gross` | Σ\|w'\| | fraction | 1.50 |
| `net` | \|Σw'\| | fraction | 1.10 |
| `name` | max \|w'_i\| (or one `ticker`'s) | fraction | 0.45 |
| `var` | 1-day parametric VaR at `alpha`, live weights | money | 2 % of notional |
| `es` | 1-day parametric ES at `alpha`, live weights | money | 3 % of notional |
| `drawdown` | current drawdown incl. today | fraction | 0.15 |
| `day_loss` | max(0, −day P&L) | money | 1.5 % of notional |

Each evaluation has the OCaml engine's wire shape — `name, scope, unit,
observed, threshold, excess, breached, utilisation` — so the targeting
computer's layout is the same function for all three sources. Money limits
are entered as a percent of notional and evaluated in dollars.

## The flight recorder (the database)

- **Store**: SQLite (stdlib `sqlite3`, WAL) at
  `{OHCAMEL_QUANT_DATA_DIR}/deck/recorder.sqlite` — on the droplet, the
  existing writable `quant_data` volume. One table,
  `readings(book TEXT, session TEXT, ts TEXT, payload TEXT)`, primary key
  `(book, ts)`, index on `(book, session)`. The payload is a compact JSON
  reading (day P&L, VaR/ES, each limit's utilisation, each holding's price and
  move).
- **Workflow**: a daemon thread started in the app's lifespan beside the
  existing cache refresher. Every `recorder_interval_s` (60) it asks the
  session clock; while the session is open it computes the reference book's
  reading with the same function the endpoint uses and inserts it. After the
  close it prunes to the newest `recorder_keep_sessions` (30) sessions.
  Disabled offline and when `OHCAMEL_QUANT_RECORDER=0`; a failed tick is
  logged and the loop continues.
- **Load**: one reading a minute, ~390 rows a session, ~12k rows kept.

## API (the contract the page is built against)

`POST /api/deck/reading` — body: `PortfolioIn` + `limits: LimitIn[] | null`
(null = the defaults) + `alpha` (0.95) + `ewma_lambda` (0.94). Returns:

```
{ as_of, source: "portfolio",
  clock: {is_open, next_open, next_close, source, note},
  book: {notional, equity_usd, day_pnl, day_pnl_usd, gross, net, cash, observations, start, end},
  risk: {alpha, var, es, var_usd, es_usd, hist_var, hist_es, model, ewma_lambda},
  marks: [{ticker, price, prev_close, change_pct, as_of, age_s, source, weight, live_weight, error}],
  blips: [{ticker, live_weight, change_pct, component_var, component_var_usd, pct_var}],
  limits: [{name, kind, scope, unit, observed, threshold, excess, breached, utilisation}],
  unevaluated: [{name, kind, reason}],
  feeds: [{key, label, state: "ok"|"stale"|"down"|"off", detail, age_s}],
  notes: [...], provenance: [...] }
```

`GET /api/deck/books` — the reference books and the recorder's status
(`enabled, running, db_path, last_write, sessions, rows, interval_s`).

`GET /api/deck/tape?book=core&session=YYYY-MM-DD` — that session's recorded
readings as columns: `ts[], day_pnl_usd[], var_usd[], es_usd[],
utilisation: {limit: []}, change_pct: {ticker: []}` (session defaults to the
newest recorded). 404 for an unknown book; an empty tape is `n: 0`, not an
error.

## The page

- `pages/Deck.tsx` and `pages/deck/` (class prefix `dk-`): `model.ts`
  (types, `fromReading`, `fromEngine`), `scope.ts` (the targeting computer's
  layout, ported from `web/scope.js`), `radar.ts` (blip geometry),
  `TargetingComputer.tsx`, `Radar.tsx`, `LampPanel.tsx`, `SessionTape.tsx`,
  `LimitsEditor.tsx`, `deck.css`.
- **Radar**: a polar scope. Each holding is a blip at a fixed bearing (book
  order); its range is its share of VaR (rings at 10 / 25 / 50 %, clamped at
  the rim); its size is |live weight|; it is green/red by today's move and
  hollow when its component VaR is negative (a hedge). The sweep turns once
  each time a new reading arrives — motion only on data.
- **Lamp panel**: square lamps per feed (`feeds`) and per limit state, and
  seven-segment counters: day P&L, VaR, marks age, and time to the close (or
  to the open). The counters are drawn as SVG segments, as Figure 2's are.
- The CRT palette is added to `styles/tokens.css` as `--crt-*` tokens (dark
  in both themes), so the page writes no raw hex.
- Reduced motion: no glides, no sweep, no blinks.
- Every instrument also has a plain-text equivalent (lamps list, marks table,
  `aria-label` summaries).

## Testing and workflows

- Python (offline, committed real fixtures — the default book's six ETFs are
  all in `fixtures/`): `test_deck_limits.py`, `test_deck_live.py`,
  `test_deck_clock.py`, `test_deck_recorder.py`, `test_deck_router.py`, with
  hand-derived values beside the arithmetic.
- Web: Vitest for `scope.ts`, `radar.ts` and `model.ts` (`npm test`), added to
  CI's quant web step.
- Deploy: `OHCAMEL_QUANT_RECORDER` in compose (default on), the smoke suite
  checks `/api/deck/books`, the quant README and route registry name the page.
