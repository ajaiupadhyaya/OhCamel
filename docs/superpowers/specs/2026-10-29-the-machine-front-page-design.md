# The Machine: the public front page

*Design, 2026-10-29. Approved by the owner as a prototype ("Write it up") on 2026-10-29.
Supersedes the Front Page ("The Tape") of the Ship design §4, and its rule that "the Flight
Deck stays the one dark room". The rest of the Paper Tape site is unchanged by this document.*

The approved prototype is in [`2026-10-29-the-machine-prototype/`](2026-10-29-the-machine-prototype/):
`explorer.tpl.html` (the page, with `__GRAPH__` where the data goes) and `graph.py` (the
script that built the data from the repo and the public API on 28 Oct, run with
`quant/.venv/bin/python`). When this document and the prototype disagree, this document wins.
Where this document is silent on look or wording, the prototype is the reference.

---

## 1. Intent

The owner's verdict on the current site, in their words: it "looks too much like a
vibecoded tool"; the assets are "poor, low quality, boring"; the layout is "uninspired";
"the vibecoded feel comes from it being designed for a robot instead of it being designed for
a human. The UI and experience of the site should be intuitive and thoughtful." They want
"'moving' parts so it shows what the engine is doing" and the page to "show the amount of
work and complexity/features of the entire project", with scrolling that "takes the user
through the model and shows each part".

The quality bar is [lyramakesmusic/neuron-explorer](https://github.com/lyramakesmusic/neuron-explorer),
"not in replication but in inspiration". Three rounds were rejected on the way here: three
static mockups in paper, dark and magazine styles ("a poor reconstruction of what I gave
you"), then a scroll story through glowing glass sheets ("I could find a thousand sites that
look exactly like this"). What separated the approved prototype from those, and what this
build must keep:

1. **An instrument, not a presentation.** You select one real thing and see its real web: what
   it reads and what it feeds. The page is a tool for understanding OhCamel, and the tour is a
   guided use of that tool.
2. **Every mark is real.** Every node is a real entity in the system and every line is a real
   dependency with a named source (§5). There are no random edges and no particles or glows that
   don't stand for something.
3. **Focus.** Everything outside the selected web goes dark. The lit part is what you're
   looking at.
4. **Specific findings in plain words.** The panels say what a thing is and what it did, with
   its numbers and the caveats the system records about itself. For example: "Tonight the
   combined book forecasts 3.4% volatility against a 10% target, so the scale is 1.00"; or
   "a farm verdict was earned at 2–3× leverage while the desk trades 1×".

## 2. Goals, non-goals, anti-goals

**Goals**
- `/` becomes **The Machine**: OhCamel's dependency graph as an interactive 3-D instrument, on
  live data, with a scroll-driven tour, readings, search and a replay of the last day.
- It shows what the engine is doing *now*. A running job is visible on the clock with its
  progress, and the data it reads is visibly flowing.
- It works for a first-time visitor (the tour), for someone exploring (select, search, deep
  links), on a phone, and without WebGL (§6.9).
- Every number on the page traces to a field in the API response.

**Non-goals**
- Restyling the other pages. They keep Paper Tape and their current shell. Carrying this
  language to them is a later spec.
- Changing any computation, schedule or artifact. This page only reads.
- Exposing anything private about the live book beyond what §7 Q1 decides.

**Anti-goals** (any of these would make it a failure even if it works)
- Decorative marks: random edges, ambient particles, glow that encodes nothing.
- Bloom, film grain, vignette, glassy slabs, lens effects.
- Marketing copy: hero slogans, stat-card grids, "every point is…" headlines.
- A number in the copy that isn't interpolated from data. Literal numbers in copy go stale and
  lie.
- A node or edge without a source in §5.

## 3. Constraints

- **Stack.** `quant/web` is Vite + React 18 + TypeScript (strict), with Vitest for DOM-free
  unit tests. The API is FastAPI in `quant/src/ohcamel_quant/api/` with pytest. Both run in CI
  and gate the deploy.
- **Style guard** (`scripts/check-style.mjs`, run by `npm run build`). It forbids non-zero
  border-radius, box-shadow, any gradient (CSS, SVG or canvas), and the Inter Variable and
  Fraunces faces. It allows green only in the Flight Deck. The machine follows all of these:
  square corners, no shadows, no gradients, no green, no Inter. Shader code doesn't trip the
  guard, and nothing should try to.
- **Bundle guard** (`scripts/check-bundle.mjs`). The entry chunk must stay ≤ 250 KB gzip. three.js
  must load only through the machine page's lazy chunk. Extend the guard with the same strictness
  it applies to Plotly: `three` reachable only from `src/pages/machine/**`.
- **Colours from tokens.** No raw hex in page code. Add a machine palette to `styles/tokens.css`
  (§6.6), and have the scene read it at init via `getComputedStyle`.
- **Fonts.** Use the system UI stack (SF Pro and SF Mono on Apple) with the self-hosted IBM Plex
  Sans and IBM Plex Mono as fallbacks. No Google Fonts, so the measured per-route CSP stays
  `self`.
- **Public host.** Every `/api` GET is public. The public host has no engine bridge
  (`/api/engine/status` → `engine_unavailable`), so the live book's positions and kill-switch
  state are not public today (§7 Q1).
- **Server.** One droplet with two shared vCPUs. `/api/machine` must be cheap: built from files
  and the warehouse read-only, cached, never a job.

## 4. What the owner decided

- Direction "B": a dark, luminous instrument (chosen over paper and magazine).
- The bar: neuron-explorer, as inspiration.
- Scrolling walks the visitor through the system part by part.
- The prototype in `2026-10-29-the-machine-prototype/` is the approved direction.

Everything else below is the designer's call, marked where it matters, and open to the owner.

## 5. The graph: what is drawn, and where each piece comes from

The page draws the system as **rings that narrow to one book**. The outside is the world's data
and the centre is the paper account it all serves. From the outside in:

| Ring | Radius | Nodes | Count (28 Oct) | Source of truth |
|---|---|---|---|---|
| Feeds | 8.4 | one per dataset family: prices, minute bars, option chains, rates & macro, factors, filings, 13F | 7 | `warehouse/freshness` datasets; vendor names from `data/prices.py`'s vendor chain and each ingest module |
| Warehouse, daily track | 7.0 | every ETF, every S&P 500 and Nasdaq-100 constituent, every FRED series, the two factor sets, the 13F filers | 563 tickers, 35 FRED, 2, 6 | `universes.json` (ETFs, 13F filers), `warehouse/constituents/sp500.csv` and `ndx100.csv`, `macro/series.json` plus `data.market.TREASURY_SERIES` |
| …filings track | 6.6 | one per equity | 518 | the equities above |
| …minute track | 6.25 | one per minute-bar name | 50 | `warehouse/ingest/minute.MINUTE_TICKERS` |
| …options track | 5.9 | one per option underlying | 30 | `warehouse/ingest/options.OPTION_UNDERLYINGS` |
| The clock | 4.25 | every scheduled job kind, at its time of day in New York; a tick for every fire | 24 kinds | `jobs/schedules.yaml` via `load_schedules()` and `cron.fire_times()`, converted to America/New_York |
| Products | 3.25 | what each job publishes, plus the research service | 15 | latest artifact manifest per kind: verdict, detail, `code_sha`, `data_asof`, `finished_at`, `cpu_seconds`, engine, notes, stale flag |
| Results | 2.35 | farm cells, league models, covariance estimators, atlas measures | 21 + 4 + 7 + 4 | farm `cells` and `leaderboard`, vol league `summary`, cov league `league`, atlas `summary` (α = 0.99) |
| Strategies | 1.45 | the desk's roster | 23 | `signals/roster.py` `load_roster()` (INC-01 `roster.yaml`) |
| Limits and kill switch | 0.78 | the live book's limits (names, kinds, scopes) and its kill switch | 7 + 1 | `desk.report`'s `book_summary` table (§7 Q1) |
| The book | 0 | the paper account | 1 | roster caps, plus `book_summary` (positions count, kill-switch state) |

Edges are directed in the direction data flows. Each kind has exactly one rule:

| Edge | From → to | Rule |
|---|---|---|
| pull | feed → its ingest job | the ingest job that fetches that dataset |
| write | ingest job → series | the job's dataset (e.g. `ingest.bars_daily` writes every daily-track ticker) |
| read | series → job | the job's artifact provenance (`detail.symbol`, `tickers`), with these exceptions: the risk atlas reads its `members` table's tickers; the farm reads the union of the roster's `signals.daily` cells' symbols; the vol league also reads the minute track (its target is minute RV where stored); the regime model reads SPY and `VIXCLS`; the surface job reads the options track; data integrity reads every daily-track ticker; the factor screen reads both factor sets |
| publish | job → product | the job kind that writes the artifact kind |
| result | product → result | the tables named in the Results row |
| read | product → job | `signals.daily` reads the farm; `signals.overlay` reads the vol and covariance leagues, as each artifact's own provenance lists (`farm.sweep`; `cov.league`, `vol.forecast_league`) |
| roster | farm cell → strategy | the roster cell with the same strategy and universe |
| signal | daily signals or research service → strategy | the roster row's `producer` |
| trade | strategy → book | every roster row |
| scale | exposure overlay → book | the overlay sets the book's exposure scale |
| limit, trip, halt | limit → book; limit → kill switch; kill switch → book | `book_summary.limits` and `book_summary.trips_on` (§7 Q1); absent → no limit, trip or halt edges |
| read | ticker → limit | only an `(Instrument T)`-scoped limit reads ticker T's daily series. Sector- and portfolio-scoped limits read nothing (they bound the book as a whole). *Designer's call.* A ticker "reaches the book through a limit" only through such an edge, so the ticker reading and tour step 3 mention a limit only when one exists in the book §7 Q1 makes visible |
| read | book → desk report | `desk.report` reads the live engine |

**An edge with no rule in this table is a bug.** A test enforces it (§6.10). On 28 Oct the
rules produced 1,318 nodes and 3,162 edges.

**Readouts carried on nodes.** Daily tickers carry a sparkline (the last 252 closes, downsampled
to about 60), the one-year return, annualised volatility, and the six most-correlated names over
the last 252 sessions. Jobs carry their fire times, cron strings, `heavy` and memory class.
Products carry their manifest fields and the specific tables their reading needs (§6.5). Feeds
carry keys, `data_asof`, last OK pull and keys behind.

## 6. The design

### 6.1 Data: `GET /api/machine`

A new module `quant/src/ohcamel_quant/machine/graph.py` ports the prototype's `graph.py`. A new
router, `api/routers/machine.py`, serves it.

- **Response.** `{asof, nodes: [{id, k, l, n, ring, m}], edges: [[a, b, kind]], runs: [...], x: {...}}`,
  as in the prototype but with the layout removed. The server sends semantics, not
  coordinates: for each node its ring, track, section and order within the section (series),
  or its hour of day (jobs; products inherit their job's hour). The client lays out (§6.3).
- **Runs.** The jobs of the last 24 hours, including running and queued ones: kind, start, end,
  cpu-seconds, state, progress, message. Read from the jobs DB the way `/api/jobs` reads it.
- **Cost.** Build the graph at most once every five minutes, and again when a newer artifact
  manifest appears. Read the warehouse read-only and memoise in process. `runs` is cheap and
  always fresh. Response budget: ≤ 250 KB gzip.
- **Honesty.** A missing or failed source leaves the node present with `status: "missing"` and
  the reason. It never fabricates a value. An artifact past its max age carries the existing
  `stale` flag. Ticker readouts come only from the warehouse; a name with no bars has no
  readout.
- **Live.** The page also subscribes to the existing `GET /api/jobs/events` (SSE) and patches
  `runs` in place. No new stream is needed.

### 6.2 What changes in the site

- `/` (code `FRONT`) renders `pages/Machine.tsx`, titled "The machine".
- **The Tape moves to `/tape`, unchanged**, and is linked from the machine's header. Nothing on it
  is lost. *Designer's call.*
- **Shell.** On `/` the `AppShell` renders a `machine` variant: no masthead, tape or footer, and
  a 46 px top bar instead (§6.4). Every other route is unchanged.
- The command line's `/` key, on `/` only, focuses the machine's search (§6.7). Cmd/Ctrl-K still
  opens the command line everywhere.

### 6.3 Layout (pure, client side: `pages/machine/layout.ts`)

- Rings sit in the x–z plane at height `y = 0.34 × radius`, a shallow cone falling to the book at
  the centre.
- **Clock angle.** `θ = hour / 24 × 2π`, with midnight at the far side and the clock running
  clockwise as seen from above. Job angle: the single fire; for a run of fires under 12 hours
  apart, the midpoint of the run; otherwise the last fire. Products sit at their job's angle.
  Results spread around their product's angle (farm cells across 0.95 rad, others ≈ 0.2–0.4).
  Strategies are evenly spaced. Limits are evenly spaced on their ring.
- **Warehouse angle.** Sections laid end to end with a six-slot gap: ETFs (by universe), equities
  (by correlation-cluster order, then alphabetical), FRED, factors, 13F. The filings, minute and
  options tracks sit at their ticker's angle.
- **Feeds.** At the circular mean of their targets' angles. Filings is nudged 0.18 rad off
  prices.

### 6.4 The screen

- **Top bar.** The wordmark; plain-word links to Tape, Markets, Risk, Portfolios, Optimizer,
  Research, Volatility, Rates, Companies, Desk and System; the web mode (web, reads, feeds,
  no web); search; and status (New York time and session state from `shell/dateline` and
  `shell/ops`).
- **Canvas.** Full screen behind everything.
- **Focus panel**, left, 380 px, shown when something is selected:
  - an id line (kind, id) and the plain name;
  - the verdict stamp with its detail;
  - provenance: code sha, data as-of, cpu-seconds, engine, finished time;
  - a lens specific to the kind (§6.5);
  - "reads" and "feeds", each a one-line summary (e.g. `563 series · 13 jobs · 12 products ·
    36 results · 21 strategies · 1 book`) and clickable chips, closest first, at most 48 then
    "+N more".
- **Side panel**, right, 410 px, with tabs:
  - **Tour** (default on arrival);
  - **Reading** (the selection in plain words);
  - **The day** (the runs of the last 24 hours, with replay).
- A **legend** along the bottom: selected, what it reads, what it feeds, failed a gate, and
  "drag to turn · click to select".
- **View offset.** `camera.setViewOffset` keeps the open panels from ever covering the machine.
- **URL state.** `?sel=<node id>&tab=tour|read|day&step=<n>` makes every view a deep link.

### 6.5 Readings and lenses (`pages/machine/readings.ts`)

Pure functions from a node plus the graph to structured content that React renders. Rules:
every number is interpolated from the response; nothing is said that the data can't support;
caveats the system writes about itself are quoted in plain words, not hidden.

Per kind (the prototype's copy is the reference):

- **Daily ticker.** Its tracks, one-year return and volatility; the jobs that read it
  **directly** (depth 1 only; the prototype first got this wrong); what it reaches (strategies,
  book). It says plainly when no strategy trades it but it still reaches the book through the
  overlay or a limit. It also names the three names it moves with most. The lens shows the
  sparkline, last close / one year / volatility, its tracks, and "moves most with" chips with the
  correlation.
- **Feed.** Keys, data as-of, last pull, keys behind (and that slow series lag by nature), and
  what it reaches. The lens shows the same as figures.
- **Job.** Schedule in words, whether it's heavy (never in session), runs and cpu-seconds in the
  last day, what it reads directly, and what it reaches. The lens shows the schedule and the last
  runs.
- **Product.** The verdict and detail, then one paragraph specific to the product:
  - **Farm.** Cells and variants (from row counts), the best chosen variant's out-of-sample
    Sharpe and DSR, that every cell failed, and that these cells are 21 of the desk's strategies
    at 2.5% each. The lens is the leaderboard.
  - **Vol league.** The best-set rates. The lens is the models table.
  - **Covariance league.** The winners by universe. The lens is the estimators table.
  - **Risk atlas.** Paths, and VaR and ES at 10 days. The lens is the measures table.
  - **Cross-section model.** The decile story. The lens is the decile bars.
  - **Regime model.** p(high) now, and why it stays descriptive.
  - **Vol surface.** ATM 30-day vol and the contract count.
  - **Kronos shadow.** Why it still runs.
  - **Data integrity.** That it detects and never repairs.
  - **Daily signals.** Written / written again / held / refused, parsed from its first note, and
    its two leverage and fill caveats.
  - **Exposure overlay.** Forecast volatility against the target and the resulting scale, parsed
    from its `exposure_scale` note, plus its fallback estimator when the note says so.
  - **Anything else.** Its notes, verbatim.
- **Farm cell.** Variants, Sharpe, PSR, DSR, PBO, verdict and detail, and the roster strategy it
  becomes.
- **Strategy.** Capital fraction, symbols, producer, and its farm cell.
- **Book, limit, kill switch.** As the prototype, from `book_summary` (§7 Q1): counts, states,
  names, kinds and scopes, "as of" its time. Never thresholds, position names or sizes.

### 6.6 Look

- **Palette** (tokens in `styles/tokens.css`):
  - `--mx-ground` black, `--mx-ink` #d4d4d4, `--mx-bright` #f2f2f2, `--mx-dim` #8a8a8a,
    `--mx-line` #252525, `--mx-structure` #2c3a42;
  - `--mx-reads` #ffb547, `--mx-feeds` #38c8ff, `--mx-fail` #ff4f9a;
  - PASS is white (no green).
- **Nodes.** Additive points: a soft disc with a crisp core. Sizes by kind as in the prototype:
  series 2.4 px (other tracks 1.8), job 6, product 9, result and strategy 5.5, feed 11, book 18.
  Base colours by kind and verdict.
- **Edges.**
  - **Shape.** Each edge is a quadratic curve in 16 segments. Its control point sits at 72% of
    the angular way to the target, at the mean radius, raised above the cone (`max height + 0.25
    + 0.12 × |Δradius|`). Fibres arch in toward what they feed.
  - **Unselected.** The faint loom at brightness 0.07.
  - **Selected.** Edges outside the selected web drop to 0.014. Edges in the web brighten by hop
    depth (`1 / (1 + 0.55 d)`), and a pulse travels each one in the direction of data flow.
- **Structure.** Ring circles, hour ticks, a tick for every scheduled fire, the market session as
  an amber arc (09:30–16:00), and the hand at the current New York time.
- **Labels.** A DOM overlay, placed by priority with collision avoidance:
  - selected and in-web labels first, then the book, feeds, products, kill switch, jobs,
    sections, then hours;
  - series labels only inside a web of fewer than 140 nodes.
- **Panels.** Translucent black with a backdrop blur, square corners, 1 px hairlines. Mono for
  ids and numbers (tabular figures).

### 6.7 Interaction

- **Hover.** Nearest node in screen space (6 px for series, 12 px for everything else); a
  tooltip with name, kind and verdict.
- **Click.** A node selects it. Empty space deselects. Esc also deselects.
- **Drag.** Turns the machine (OrbitControls with damping, polar 0.12–1.38, no pan).
- **Wheel.** Steps the tour while the Tour tab is open; zooms otherwise.
- **Search** (`/` to focus). Matches node ids, labels, names and kinds, ranked book, product,
  feed, job, strategy, result, ticker, track, with exact label first. If nothing in the graph
  matches, Enter hands the text to `parseCommand`, so function codes still work.
- **Web mode.** web, reads, feeds or no web.
- **Camera.**
  - Selecting flies there in 1.5 s with ease-in-out-cubic.
  - A web of more than 60 nodes frames the whole machine. A small web frames its node, with the
    node's angle brought to the near side.
  - The book, limits, kill switch and strategies frame the centre.

### 6.8 The tour and the day

- **Tour.** Eight steps:
  1. overview;
  2. the price feed;
  3. one stock (NVDA);
  4. the clock (the heaviest job of the last day);
  5. the farm;
  6. where research becomes orders (the overlay);
  7. the book;
  8. "now it's yours", with starting points.

  Each step names its selection and camera. Its text is written from live data at render time,
  with the prototype's copy as reference. Two rules:
  - A step whose subject is missing or stale says so instead of asserting.
  - Step 4 chooses "the heaviest job of the last day" from `runs`, not by name.

  Keys: arrow keys and PageUp/PageDown; there are dots and back/next buttons.
- **The day.** The runs of the last 24 hours, listed with time, name and cpu-seconds.
  - **Now.** A **running** job shows at the top. On the clock, its node pulses with a progress
    arc and its read edges carry pulses.
  - **Replay** sweeps a hand across the last day in 26 seconds, lighting each job's web as it
    runs and highlighting its row. Clicking a row selects the job.

### 6.9 Mobile, motion, access, fallback

- **Under 860 px.** Canvas in the top 60%. The side panel becomes a bottom sheet holding the focus
  content too. Tap selects. The tour advances by scrolling the sheet or with next/back.
- **`prefers-reduced-motion`.** No pulses, no camera tweens (cuts instead), and replay steps run
  by run.
- **No WebGL.** Show "The machine as a list": an accessible outline by ring, each node with its
  reading and its reads/feeds lists, and the same search and deep links. The same outline is
  linked from the canvas (`aria-hidden`) for screen readers. The panels are `aria-live="polite"`.

### 6.10 Tests and acceptance

**Python** (`quant/tests/test_machine_graph.py`, `test_machine_api.py`):
- Every edge kind comes from its §5 rule, and no other kinds appear.
- Jobs land at their New York hour: vol league 21.5, farm 23.0, daily bars 18.5. This is the bug
  the prototype hit when it used `fire_times`' UTC instants.
- The roster gives 23 strategies (21 farm cells, 2 research).
- `desk.report` writes `book_summary` with no threshold and no symbol-sized field (a test on the
  job's output).
- The machine renders the book from it, and with it absent shows no limits or kill switch.
- A missing artifact gives `status: "missing"`.
- Response schema holds; gzip size stays ≤ 250 KB on the fixture warehouse.

**Web** (Vitest, DOM-free):
- `layout`: angles, rings, the NY clock.
- `web`: BFS depths and the up/down split.
- `readings`: on a fixture graph, no output contains `NaN`, `undefined` or `—` where data exists,
  and every number in the output appears in the fixture.
- Every tour step's selection resolves.
- Search ranking.

**Guards and screenshots.** `npm run build` passes the style and bundle guards. `npm run
screenshots` adds tour steps 1, 2, 5 and 7 at 1440 × 900 and 375 × 812 into
`docs/media/quant/machine/`.

**Acceptance.**
- On the deployed public host, `/` loads the machine from live data within 2.5 s on broadband.
- It holds 60 fps at 1440 × 900 on an Apple M-series and at least 30 fps on integrated graphics.
- The tour runs end to end, a running job is visible while one runs, deep links restore the view,
  the list view works with WebGL off, and phone width works.
- The owner looks at it and does not call it vibecoded.

## 7. Open for the owner

**Q1: What may the public `/` show about the live book, and where does it come from?**
**Decided by the owner, 2026-10-29: configuration and counts.**

`/` may show:
- the kill switch's state (enabled, armed or halted) and its trip list;
- every limit's name, kind and scope;
- the number of positions.

It never shows position names or sizes, and never shows limit thresholds.

How (*designer's call*, chosen to open no new path from the public host to the engine):
- `desk.report` already runs on the live profile and reads the live engine every session day at
  16:30. Add one table to its artifact, `book_summary`, with `as_of`, `positions` (a count),
  `kill_switch` (`disabled` / `armed` / `halted`), `trips_on` (limit names) and `limits`
  (name, kind, scope only).
- Strip the thresholds in the job, so they never reach the artifact store.
- `/api/machine` reads that table like any artifact. The book, limits and kill switch come from
  it, labelled "as of" its time. There's no compose change and no bridge on the public host.
- Until the table exists, or if it's stale, the book node shows the roster and the overlay only,
  with the limits and kill switch absent and the reason stated. The example book is never
  presented as the live one.
- `desk.report`'s other tables (fills, per-strategy P&L, refusals) are not rendered on `/`.
  They stay where they already are, at `/api/artifacts`. On the machine, its node shows only
  its run times.
- "Strategies" means the committed INC-01 roster (23).
- Instrument-scoped limits in `book_summary.limits` produce the ticker → limit edges of §5.

Superseded wording elsewhere: wherever §5 or §6 says "the example book" for the book, limits or
kill switch, read `book_summary`.

The prototype showed "kill switch armed, trips on book-cap, var-cap and dd-cap" and "5
positions". Those facts live only in the droplet's private `book.sexp` and behind the engine
bridge, which is deliberately off on the public host because its snapshot carries positions.
What the quant API can read today:
- `book.example.sexp`: kill switch disabled, no trip list.
- The public `desk.report` artifact: the session's fills, per-strategy judgements, the overlay
  reading.

The answer sets:
- whether a deploy change is needed: a read-only `book.sexp` mount for configuration, or a
  narrow count-only read from the engine;
- whether trip and halt edges exist;
- what the book, limit and kill-switch readings say;
- tour step 7's copy;
- whether `desk.report`'s contents appear on `/`.

## 8. Left to the builder

- Plain three.js in an effect hook (as in the prototype) or React Three Fiber.
- Component boundaries inside `src/pages/machine/`.
- The caching mechanism for `/api/machine`.
- Exact label priorities and collision padding.
- Shader details within §6.6.
- Whether the warehouse correlation peers are computed in the endpoint or by a small nightly job
  that writes them (either way, read-only from the page's point of view).

## 9. References

- The prototype: [`2026-10-29-the-machine-prototype/`](2026-10-29-the-machine-prototype/).
- The bar: [lyramakesmusic/neuron-explorer](https://github.com/lyramakesmusic/neuron-explorer),
  `explorer/static/map3d.js` (three.js r169, additive glow points, narrow 32° camera).
- [The Ship design](2026-10-05-the-ship-design.md) §3 (Paper Tape) and §3.7 (the Flight Deck), which
  this supersedes for `/` only.
- [INC-01 roster](../../../research/experiments/INC-01/roster.yaml), [`book.example.sexp`](../../../book.example.sexp),
  [`schedules.yaml`](../../../quant/src/ohcamel_quant/jobs/schedules.yaml).
