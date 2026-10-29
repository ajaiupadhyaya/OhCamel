# The Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the public front page `/` with The Machine: OhCamel's real dependency graph as a live, explorable 3-D instrument, served by a new cached `GET /api/machine`, with The Tape moved unchanged to `/tape`.

**Architecture:** A pure Python graph builder (`quant/src/ohcamel_quant/machine/`) turns the repo's config, the latest artifact manifests, the warehouse (read-only) and the jobs DB into semantic nodes and edges. One router serves it, cached for five minutes, with fresh runs. `desk.report` gains a threshold-free `book_summary` table, so the book's limits and kill switch reach the public page with no new path to the engine. The client (`quant/web/src/pages/machine/`) does layout, webs, readings, the tour and the day in pure, Vitest-tested modules, and renders them with plain three.js in one effect hook inside a lazy chunk.

**Tech Stack:** Python 3.12, FastAPI, pandas, numpy, scipy, DuckDB (read-only), SQLite jobs DB, pytest. Vite 8, React 18, TypeScript strict, three.js 0.169 (with `three/examples/jsm/controls/OrbitControls.js`), Vitest, Playwright screenshots.

**Spec:** [`docs/superpowers/specs/2026-10-29-the-machine-front-page-design.md`](../specs/2026-10-29-the-machine-front-page-design.md). The approved prototype is in [`../specs/2026-10-29-the-machine-prototype/`](../specs/2026-10-29-the-machine-prototype/) (`explorer.tpl.html`, `graph.py`). The spec wins where they disagree; where the spec is silent on look or wording, the prototype is the reference. Line references to `explorer.tpl.html` below are to that file.

## Global Constraints

- **Every mark is real.** No node or edge without a rule in spec §5; no decorative particles, glow, bloom, grain, vignette, glass or lens effects.
- **No literal numbers in copy.** Every number shown is interpolated from the `/api/machine` response. No marketing copy, hero slogans or stat-card grids.
- **Honesty.** A missing or failed source leaves the node present with `status: "missing"` and a `why`; nothing is fabricated; ticker readouts come only from the warehouse; artifacts past max age carry `stale: true`.
- **Live book, owner decision 2026-10-29:** the public page may show the kill switch's state and trip list, every limit's name, kind and scope, and the position count. **Never** position names or sizes, **never** limit thresholds. `desk.report`'s fills, per-strategy P&L, refusals and notes are not rendered on `/`.
- **Style guard** (`quant/web/scripts/check-style.mjs`, run by `npm run build`): border-radius 0, no `box-shadow` other than `none`, no gradient of any kind. The guard matches the text `gradient(`, `linearGradient`/`radialGradient`/`conicGradient` and `fillgradient` anywhere in `src/`, including shader strings and comments. No `Inter Variable` or `Fraunces`. No green. Do not name a variable `rx` or `ry`: the guard's SVG-radius rule matches `rx = …`.
- **Bundle guard** (`quant/web/scripts/check-bundle.mjs`): the entry chunk ≤ 250 KB gzip; `three` is reachable only from `src/pages/machine/**` and `src/pages/Machine.tsx`, never statically from the entry (Task 8 adds the rule).
- **Colours from tokens only.** No raw hex in page code. The palette, in `quant/web/src/styles/tokens.css`: `--mx-ground` #000000, `--mx-ink` #d4d4d4, `--mx-bright` #f2f2f2, `--mx-dim` #8a8a8a, `--mx-line` #252525, `--mx-structure` #2c3a42, `--mx-reads` #ffb547, `--mx-feeds` #38c8ff, `--mx-fail` #ff4f9a. PASS is white (`--mx-bright`). No green.
- **Fonts:** the system UI stack (`system-ui, -apple-system, "SF Pro Text"`; mono `ui-monospace, "SF Mono"`) with the self-hosted IBM Plex Sans and IBM Plex Mono as fallbacks. No Google Fonts or any external origin.
- **CSP** (`deploy/Caddyfile.snippets`): `default-src 'self'`, `worker-src 'none'`, `style-src-elem` hashed. So: no CDN, no web workers, no injected `<style>` elements, no `eval`. Setting `element.style.*` from script is allowed.
- **`/api/machine`:** response ≤ 250 KB gzip; the graph is built at most once every five minutes, or again when a newer artifact manifest appears; it never runs a job; the warehouse is opened read-only; `runs` is always fresh.
- **Layout constants (spec §6.3, §6.6):** ring radii feeds 8.4, daily 7.0, filings 6.6, minute 6.25, options 5.9, clock 4.25, products 3.25, results 2.35, strategies 1.45, limits 0.78, book 0. Height `y = 0.34 × radius`. `θ = hour / 24 × 2π`, midnight at the far side, clockwise from above.
- **Commits:** the repo's style, an area prefix and a ` -- ` clause (e.g. `machine: … -- …`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Python checks: `cd quant && uv run --frozen ruff check && uv run --frozen pytest -q <files>`. Web checks: `cd quant/web && npm run typecheck && npm test -- <files>`; `npm run build` runs both guards.
- Work on a branch in a worktree (superpowers:using-git-worktrees). Push and deploy only in Task 20, on the owner's go-ahead.

## Review Focus

1. **The warehouse is locked or absent** (the nightly writer holds it past 2 s, or a fresh host has none): `/api/machine` must still answer 200 with every node present, the feeds `missing` with the reason, and no readouts, never a 503. Test: Task 6, `test_a_locked_warehouse_leaves_the_graph_whole`.
2. **A daylight-saving day, or a job that does not fire on the build day** (weekly, monthly): jobs must still sit at their New York wall-clock hour. Test: Task 2, `test_hours_hold_across_daylight_saving` and `test_a_weekly_job_is_placed_on_the_day_it_fires`.
3. **A job event for a run the page has never seen** (a job starts after load), or the SSE stream refused at its cap (429/503): the page refetches `/api/machine`, and falls back to polling. It never drops the event silently or throws. Test: Task 9, `applyJobEvent` "unknown id" case.
4. **A deep link naming a node that does not exist**, or hostile text in `?sel=`: the selection is ignored and the rest of the view restores. No text from the URL or the data is ever written as HTML. Test: Task 13, `parseView` cases; Task 17 renders every label and reading as React text, never `innerHTML`.
5. **An artifact present but missing a table or a column** (pruned, or schema drift): the node stays with its manifest, its lens is omitted with the reason, and no `NaN` or `undefined` reaches the page. Test: Task 5, `test_a_lens_with_a_missing_column_is_omitted_with_the_reason`; Task 12 fixture variant.

---

## File structure

**Python, new** (`quant/src/ohcamel_quant/machine/`):
- `__init__.py`: package docstring only.
- `world.py`: the static world from repo config (ETFs, equities, FRED, factors, 13F filers, minute and options names).
- `clock.py`: schedules to New York hours.
- `readouts.py`: ticker sparkline, return, volatility, peers, and the equities' cluster order.
- `graph.py`: the pure §5 assembly: `Inputs` in, `{nodes, edges}` out, with `RULES`.
- `lenses.py`: per-product lens payloads.
- `load.py`: all I/O: artifacts, warehouse, roster, runs.
- `service.py`: `build_machine` and the five-minute cache.
- `quant/src/ohcamel_quant/api/routers/machine.py`: `GET /api/machine` (auto-mounted by `api/app.py`).

**Python, modified:** `quant/src/ohcamel_quant/signals/report.py` (`book_summary`).

**Python tests:** `quant/tests/test_machine_graph.py`, `quant/tests/test_machine_api.py`, `quant/tests/test_signals_report.py` (extended).

**Web, new** (`quant/web/src/pages/machine/`): `types.ts`, `fixture.ts`, `runs.ts`, `useMachine.ts`, `fmt.ts`, `layout.ts`, `web.ts`, `notes.ts`, `readings.ts`, `tour.ts`, `search.ts`, `day.ts`, `url.ts`, `labels.ts`, `palette.ts`, `scene.ts`, `Segs.tsx`, `TopBar.tsx`, `FocusPanel.tsx`, `SidePanel.tsx`, `Legend.tsx`, `ListView.tsx`, `machine.css`, and a `*.test.ts` beside each pure module. Plus `quant/web/src/pages/Machine.tsx`.

**Web, modified:** `package.json` and `package-lock.json`, `vite.config.ts`, `scripts/check-bundle.mjs`, `scripts/screenshots.mjs`, `src/styles/tokens.css` and `tokens.test.ts`, `src/lib/routes.ts` and `routes.test.ts`, `src/shell/command.ts` and `command.test.ts`, `src/shell/AppShell.tsx`, `src/shell/hotkeys.ts`.

**Docs:** `docs/status.md`, `docs/media/quant/machine/`.

---

## Wire contract (Tasks 4–7 produce it, Tasks 9–18 consume it)

```ts
type NodeKind = "feed" | "series" | "job" | "product" | "result" | "strategy" | "limit" | "kill" | "book";
type Ring = "feeds" | "daily" | "filings" | "minute" | "options" | "clock" | "products" | "results" | "strategies" | "limits" | "book";
type EdgeKind = "pull" | "write" | "read" | "publish" | "result" | "roster" | "signal" | "trade" | "scale" | "limit" | "trip" | "halt";
interface MNode {
  id: string;            // "feed:prices" "d:NVDA" "s:NVDA" "m:NVDA" "o:NVDA" "f:VIXCLS" "ff:FF5" "13f:<cik>" "j:<kind>" "p:<kind>" "p:research"
                         // "c:<strategy>@<universe>" "lm:<model>" "ce:<estimator>" "am:<method>:<horizon>" "st:<slug>" "lim:<name>" "kill" "book"
  k: NodeKind; l: string /* short label */; n: string /* plain name */; ring: Ring;
  at: { sec?: "etf" | "eq" | "fred" | "ff" | "13f"; i?: number; n?: number; of?: string; h?: number; beside?: string };
  m: Record<string, unknown>;          // per-kind fields, listed in Tasks 4 and 5
  status?: "missing"; why?: string;
}
type Edge = [number, number, EdgeKind];   // indices into nodes, in the direction data flows
interface Run { id: string; kind: string; state: "queued" | "running" | "done" | "failed" | "cancelled";
  start: string | null; end: string | null; cpu: number | null; progress: number | null; msg: string | null }
interface MachineOut { asof: string; nodes: MNode[]; edges: Edge[]; runs: Run[];
  x: { fires: [string, number][] }; provenance: unknown[]; notes: string[] }
```

`at` by kind: daily-ring series `{sec, i}` (index within its section); filings, minute and options series `{of: "d:<T>"}`; job `{h}`; product `{of: "j:<kind>"}`, except `p:research` `{beside: "p:signals.daily"}`; result `{of: "p:<kind>", i, n}`; strategy and limit `{i, n}`; kill `{i: <limit count>, n: <limit count + 1>}` (it sits with the limits); feed and book `{}`.

---

### Task 1: `desk.report` writes `book_summary`

**Files:**
- Modify: `quant/src/ohcamel_quant/signals/report.py`
- Test: `quant/tests/test_signals_report.py`

**Interfaces:**
- Consumes: the live engine's `GET /api/desk` (already read; `positions[]` rows carry `symbol`, `graph_qty`, see `desk/desk.ml:497`), `GET /api/graph` (`nodes[]`, each with a nullable `limit: {name, kind, scope}`, see `lib/server.ml:510-516`), and `GET /api/ops` (`alerts: {enabled, kill_switch: "off"|"armed"|"tripped", trips_on: [str]}`, see `lib/server.ml:766-812`). Scope strings are `instrument:<SYM>`, `sector:<NAME>` and `portfolio` (`lib/types.ml:336`).
- Produces: `report.BOOK_COLUMNS = ["as_of", "positions", "kill_switch", "trips_on", "limits"]`. The `book_summary` table has 0 rows when the engine is unset or unreachable, else 1 row. `positions` is an int or null. `kill_switch` is `"disabled" | "armed" | "halted"` or null. `trips_on` is a JSON list of strings or null. `limits` is a JSON list of `{"name", "kind", "scope"}` sorted by name, or null. Null means "not read", and the notes say why. `report.ROUTES` gains `"/api/graph"` and `"/api/ops"`.

- [ ] **Step 1: Write the failing tests.** Add canned `GRAPH` and `OPS` bodies to `_full()`, and a `positions` list to a copy of `DESK`.

```python
GRAPH = {"nodes": [
    {"name": "limit/aapl-cap", "limit": {"name": "aapl-cap", "kind": "gross_notional", "scope": "instrument:AAPL",
                                         "threshold": 150000.0}},          # an extra key must never be copied
    {"name": "breach/aapl-cap", "limit": {"name": "aapl-cap", "kind": "gross_notional", "scope": "instrument:AAPL"}},
    {"name": "limit/dd-cap", "limit": {"name": "dd-cap", "kind": "max_drawdown", "scope": "portfolio"}},
    {"name": "price/AAPL", "limit": None}]}
OPS = {"alerts": {"enabled": True, "kill_switch": "armed", "trips_on": ["dd-cap"], "firing": ["aapl-cap"],
                  "recent": [{"line": "Instrument AAPL over 150000"}]}}
DESK_HELD = {**DESK, "positions": [{"symbol": "AAPL", "graph_qty": 400.0}, {"symbol": "MSFT", "graph_qty": 200.0},
                                   {"symbol": "SPY", "graph_qty": 0.0}]}

def test_book_summary_is_config_and_counts(tmp_path):
    routes = {**_full(), "/api/desk": _ok(DESK_HELD)}
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    b = spec.tables["book_summary"]
    assert list(b.columns) == report.BOOK_COLUMNS and len(b) == 1
    row = b.iloc[0]
    assert row["positions"] == 2 and row["kill_switch"] == "armed"
    assert json.loads(row["trips_on"]) == ["dd-cap"]
    assert json.loads(row["limits"]) == [
        {"name": "aapl-cap", "kind": "gross_notional", "scope": "instrument:AAPL"},
        {"name": "dd-cap", "kind": "max_drawdown", "scope": "portfolio"}]
    text = b.to_json()
    for leaked in ("threshold", "150000", "MSFT", "400", "firing", "recent"):
        assert leaked not in text

@pytest.mark.parametrize("alerts,want", [
    ({"enabled": False, "kill_switch": "off"}, "disabled"), ({"enabled": True, "kill_switch": "off"}, "disabled"),
    ({"enabled": True, "kill_switch": "armed"}, "armed"), ({"enabled": True, "kill_switch": "tripped"}, "halted"),
    ({"enabled": True, "kill_switch": "sideways"}, None)])
def test_the_kill_switch_in_the_page_words(tmp_path, alerts, want): ...
    # run with OPS = {"alerts": alerts}; assert row["kill_switch"] == want (pd.isna for None);
    # for "sideways" assert a note names the unknown state

def test_a_missing_graph_route_leaves_limits_unread(tmp_path): ...
    # routes without "/api/graph" (404): one row, limits is null, a note names GET /api/graph
```

Extend the existing tests:
- In `test_a_full_report`: the table set is `{"fills", "pnl_by_strategy", "refusals", "overlay_path", "book_summary"}`, and `sorted(eng.seen) == sorted(report.ROUTES)` still holds.
- In `test_an_unreachable_engine_is_a_done_job_that_says_so` and `test_an_unset_engine_url_is_a_done_job_that_says_so`: `len(spec.tables["book_summary"]) == 0`.

- [ ] **Step 2: Run them to see them fail.**
Run: `cd quant && uv run --frozen pytest -q tests/test_signals_report.py`
Expected: FAIL (`KeyError: 'book_summary'`, `AttributeError: BOOK_COLUMNS`).

- [ ] **Step 3: Implement `_book_summary(desk: dict | None, graph: dict | None, ops: dict | None, read_at: str, notes: list[str]) -> list[dict]` in `report.py`.**
  - Add `/api/graph` and `/api/ops` to `ROUTES`, and as `dict` to `want`.
  - Return `[]` when all three bodies are None.
  - Copy limit fields by whitelist (`name`, `kind`, `scope`, each a `str`), deduplicated by name.
  - A position counts when `graph_qty` is a finite non-zero number (`_num`).
  - Map the kill switch as the test table says.
  - Append the note `"book_summary: configuration and counts only (owner, 2026-10-29): no position names or sizes, no limit thresholds"`.
  - Thread the rows through `_spec(…, book=…)`; the two early returns pass `[]`.
  - Update the module docstring's table list.

- [ ] **Step 4: Run the tests to see them pass.**
Run: `cd quant && uv run --frozen pytest -q tests/test_signals_report.py && uv run --frozen ruff check src/ohcamel_quant/signals/report.py`
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/signals/report.py quant/tests/test_signals_report.py
git commit -m "desk: desk.report writes book_summary -- the live book's configuration and counts for the public front page, read from /api/graph and /api/ops, thresholds and names never copied"
```

---

### Task 2: The world and the clock

**Files:**
- Create: `quant/src/ohcamel_quant/machine/__init__.py`, `machine/world.py`, `machine/clock.py`
- Test: `quant/tests/test_machine_graph.py`

**Interfaces:**
- Produces, in `machine/world.py`:
  - `World` (frozen dataclass): `etfs: tuple[tuple[str, str, tuple[str, ...]], ...]` (ticker, name, universe labels; `universes.json` order, first appearance); `equities: dict[str, str]` (ticker → name from `warehouse/constituents/sp500.csv` then `ndx100.csv`, `#` lines skipped, ETFs excluded); `fred: tuple[tuple[str, str], ...]` (`macro/series.json` ids then `data.market.TREASURY_SERIES` keys, unique; a Treasury name is `"Treasury yield, {v:g}-year"`, or `"…, {round(v*12)}-month"` under one year); `factors = (("FF5", "Fama–French five factors, daily"), ("MOM", "Momentum factor, daily"))`; `filers: tuple[dict[str, str], ...]` (`universes.json` `notable_13f_filers`: `cik`, `name`, `manager`); `minute: tuple[str, ...]` (`warehouse.ingest.minute.MINUTE_TICKERS`); `options: tuple[str, ...]` (`warehouse.ingest.options.OPTION_UNDERLYINGS`).
  - `World.daily() -> list[str]`: ETF tickers, then equity tickers.
  - `load_world() -> World`.
- Produces, in `machine/clock.py`:
  - `NY = ZoneInfo("America/New_York")`; `ny_hour(t: datetime) -> float` (hour + minute/60 in New York).
  - `place_hour(fires: Sequence[float]) -> float`.
  - `JobClock` (frozen dataclass): `kind: str`, `fires: tuple[float, ...]` (sorted), `hour: float`, `cron: tuple[str, ...]`, `heavy: bool`, `mem: str`, `when: tuple[str, ...]`.
  - `job_clock(schedules: Schedules, day: date) -> dict[str, JobClock]`.
  - `PRETTY: dict[str, str]`: plain job names, copied from the prototype's `PRETTY`.

- [ ] **Step 1: Write the failing tests.**

```python
from datetime import UTC, date, datetime
from ohcamel_quant.jobs.schedules import load_schedules
from ohcamel_quant.machine.clock import job_clock, ny_hour, place_hour
from ohcamel_quant.machine.world import load_world

def test_jobs_land_at_their_new_york_hour():   # spec §6.10: the prototype's UTC bug
    c = job_clock(load_schedules(), date(2026, 10, 28))
    assert c["vol.forecast_league"].hour == 21.5
    assert c["farm.sweep"].hour == 23.0
    assert c["ingest.bars_daily"].hour == 18.5

@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1), date(2027, 1, 13)])
def test_hours_hold_across_daylight_saving(day):            # Review Focus 2
    assert job_clock(load_schedules(), day)["farm.sweep"].hour == 23.0

def test_a_weekly_job_is_placed_on_the_day_it_fires():      # Review Focus 2
    c = job_clock(load_schedules(), date(2026, 10, 28))
    assert all(j.fires for j in c.values())                # every scheduled kind has a time of day

def test_place_hour():
    assert place_hour([18.5]) == 18.5
    assert place_hour([9.5, 10.0, 15.75]) == pytest.approx(12.625)   # a run under 12 h apart: its middle
    assert place_hour([9.6667, 18.8333]) == 18.8333                   # spread wider: the last fire

def test_ny_hour():
    assert ny_hour(datetime(2026, 10, 28, 22, 30, tzinfo=UTC)) == 18.5

def test_the_world_is_disjoint_and_complete():
    w = load_world()
    etf = {t for t, _, _ in w.etfs}
    assert etf.isdisjoint(w.equities) and len({f for f, _ in w.fred}) == len(w.fred)
    assert set(w.minute) <= etf | set(w.equities) and set(w.options) <= etf | set(w.equities)
    assert len(w.filers) == 6 and all(f["cik"] and f["manager"] for f in w.filers)
```

- [ ] **Step 2: Run them to see them fail.**
Run: `cd quant && uv run --frozen pytest -q tests/test_machine_graph.py`
Expected: FAIL (`ModuleNotFoundError: ohcamel_quant.machine`).

- [ ] **Step 3: Implement `world.py` and `clock.py`.**
  - `job_clock`:
    - Convert every `cron.fire_times(e.parsed, day)` instant with `ny_hour`.
    - When an entry has no fire on `day`, search `day + 1 … day + 31` for its first firing day.
    - Merge entries by `e.kind`.
    - `when` per entry is `"daily"` (`dow_star and dom_star`), `"weekdays"` (`dom_star` and five `dows`), `"monthly"` (not `dom_star`), else `"weekly"`.
    - `hour = place_hour(fires)`.
  - `place_hour`: one fire → it; a run whose first and last fire are under 12 h apart → their midpoint; otherwise → the last fire.

- [ ] **Step 4: Run them to see them pass.** Same command. Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine quant/tests/test_machine_graph.py
git commit -m "machine: the world and the clock -- the warehouse's names from repo config, every scheduled job at its New York hour"
```

---

### Task 3: Ticker readouts and the cluster order

**Files:**
- Create: `quant/src/ohcamel_quant/machine/readouts.py`
- Test: `quant/tests/test_machine_graph.py`

**Interfaces:**
- Consumes: `closes`, a wide DataFrame (DatetimeIndex ascending × ticker columns, `adj_close`, NaN where absent).
- Produces:
  - `ticker_readouts(closes: pd.DataFrame, *, window: int = 252, points: int = 60, n_peers: int = 6, min_overlap: int = 120) -> dict[str, dict]`. Per ticker with at least 2 finite closes in its last `window + 1` rows: `{"spark": [...], "ret1y": float, "vol": float, "last": float, "peers": [[ticker, corr], ...]}`.
    - `spark`: every `ceil(len / points)`-th close divided by the first, 4 dp, at most `points` values plus the last.
    - `ret1y`: last / first − 1, 4 dp.
    - `vol`: sample std (ddof 1) of daily log returns × √252, 4 dp.
    - `last`: 2 dp.
    - `peers`: the top `n_peers` by Pearson correlation of log returns over the window, among tickers with ≥ `min_overlap` shared returns, itself excluded, corr to 2 dp.
    - A ticker with no closes has no key.
  - `cluster_order(closes: pd.DataFrame, tickers: Sequence[str], *, window: int = 252, min_overlap: int = 120) -> list[str]`: average-linkage hierarchical clustering (`scipy.cluster.hierarchy.linkage(squareform(1 - corr), "average")`, then `leaves_list`) of the tickers with enough data. The rest follow alphabetically. Every input ticker appears exactly once.

- [ ] **Step 1: Write the failing tests** on a synthetic 300-day frame: `A` geometric growth of 0.1% a day, `B = 2·A` plus small noise, `C` with returns the negative of `A`'s noise-free returns plus its own noise, `D` all NaN.
  - `test_readouts_on_known_series`: `A`'s `vol` ≈ 0 (constant growth), `ret1y` = `1.001**252 − 1` to 4 dp; `len(A["spark"]) <= 61`; `D` absent; `B` is `A`'s first peer.
  - `test_peers_need_overlap`: a ticker with 50 closes has a readout but appears in no one's peers.
  - `test_cluster_order_keeps_the_pair_together`: `A` and `B` are adjacent; `D` is last; the length equals the input length.

- [ ] **Step 2: Run them to see them fail.** `cd quant && uv run --frozen pytest -q tests/test_machine_graph.py -k "readouts or peers or cluster"`. Expected: FAIL (import).

- [ ] **Step 3: Implement both functions** with numpy and pandas (`DataFrame.corr(min_periods=min_overlap)`), NaN-safe.

- [ ] **Step 4: Run them to see them pass.** Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine/readouts.py quant/tests/test_machine_graph.py
git commit -m "machine: ticker readouts -- sparkline, one-year return, volatility and the names each moves with, from warehouse closes only"
```

---

### Task 4: The graph: spec §5's rings and edge rules

**Files:**
- Create: `quant/src/ohcamel_quant/machine/graph.py`
- Test: `quant/tests/test_machine_graph.py`

**Interfaces:**
- Consumes: `World`, `JobClock` (Task 2); readouts and order (Task 3); `signals.roster.Roster` (`cells` with `slug`, `producer`, `strategy`, `universe`, `symbols`, `capital_fraction`; `incubation_cap`, `canary_cap`, `vol_target`).
- Produces:
  - `Product` (frozen dataclass): `kind: str`, `manifest: dict | None`, `stale: bool = False`, `tables: Mapping[str, pd.DataFrame] = {}`, `rows: Mapping[str, int] = {}` (row counts of every table the manifest lists, from parquet metadata), `why: str | None = None`.
  - `Inputs` (frozen dataclass): `world: World`, `clock: Mapping[str, JobClock]`, `roster: Roster`, `products: Mapping[str, Product]` (keyed by artifact kind: every `PROD` kind, plus `"desk.report"`), `freshness: Mapping[str, dict] | None` (warehouse `datasets`; None when unavailable), `readouts: Mapping[str, dict]`, `order: Sequence[str]`, `why: Mapping[str, str]`.
  - `PROD: dict[str, tuple[str, str]]` (kind → (code, name)), copied from the prototype.
  - `FEEDS`: a tuple of `(key, label, who, jobs: tuple[str, ...], dataset, track)`:
    - `prices` (Yahoo, Alpaca, Stooq): `ingest.bars_daily`, `bars_daily`, daily tickers;
    - `minute` (Alpaca (IEX)): `ingest.bars_minute`, `bars_minute`;
    - `cboe` (Cboe, 15-minute delay): `ingest.option_snapshots`, `option_snapshots`;
    - `fred` (FRED): `ingest.fred_warehouse` and `ingest.fred`, `fred`;
    - `french` (Kenneth French): `ingest.factors`, `factors`;
    - `sec` (SEC EDGAR): `ingest.sec_facts`, `sec_facts`, the filings track;
    - `sec13f` (SEC EDGAR): `ingest.holdings_13f`, `holdings_13f`.
  - `RULES: dict[str, frozenset[tuple[str, str]]]`, from edge kind to the allowed (from-kind, to-kind) pairs:
    - pull {(feed, job)}; write {(job, series)}; publish {(job, product)}; result {(product, result)};
    - read {(series, job), (product, job), (series, limit), (book, job)};
    - roster {(result, strategy)}; signal {(product, strategy)}; trade {(strategy, book)}; scale {(product, book)};
    - limit {(limit, book)}; trip {(limit, kill)}; halt {(kill, book)}.
  - `provenance_symbols(manifest: dict | None) -> list[tuple[str, str]]`: `(dataset, SYMBOL)` pairs from every provenance entry's `detail.symbol`, `detail.tickers`, `symbol` and `tickers`. `dataset` is `detail.dataset` or `"bars_daily"`.
  - `build_graph(inp: Inputs) -> dict` returning `{"nodes": [...], "edges": [[a, b, kind], ...]}` in the wire contract's shape.

The rules, in the spec's words, with the ids from the wire contract:

| Edge | Built from |
|---|---|
| pull, write | `FEEDS`: the feed pulls each of its jobs; its first job writes every series on its track |
| read series → job | `provenance_symbols` of the kind's artifact (`bars_minute` → `m:` id, otherwise `d:`), except: `risk.mc_atlas` reads its `members` table's `ticker` column; `farm.sweep` reads the union of `symbols` of roster cells with `producer == "signals.daily"`; `vol.forecast_league` also reads the whole minute track; `regime.hmm` reads `d:SPY` and `f:VIXCLS`; `vol.surface_history` reads the options track; `ops.data_integrity` reads every daily ticker; `factors.screen` reads `ff:FF5` and `ff:MOM` |
| read product → job | for each artifact, every provenance entry whose `source` is a `PROD` kind: `p:<source>` → `j:<kind>` (this yields `farm.sweep → signals.daily`, and `cov.league` and `vol.forecast_league → signals.overlay`) |
| publish | `j:<kind>` → `p:<kind>` for every `PROD` kind that has a clock entry |
| result | farm `cells` (+ `leaderboard` by `cell_id`), vol league `summary`, cov league `league` (one node per estimator, universes in `m`), atlas `summary` rows with `alpha == 0.99` |
| roster | `c:<strategy>@<universe>` → `st:<slug>` for cells with `producer == "signals.daily"` |
| signal | `p:signals.daily` for `signals.daily`, `p:research` for `research-service`, otherwise `p:<producer>` if that node exists |
| trade, scale | every strategy → `book`; `p:signals.overlay` → `book` |
| limit, trip, halt, read series → limit | only from a **usable** `desk.report` `book_summary` row (present, ≥ 1 row, `limits` not null, artifact not stale): `lim:<name>` → `book`; a `trips_on` name → `kill`; `kill` → `book` when its state is `armed` or `halted`; `d:<T>` → `lim:<name>` for scope `instrument:<T>` |
| read book → job | `book` → `j:desk.report` |

Node `m` fields:
- series (daily): `track`, `unis` (ETFs), and the readout fields;
- other series: `track`, plus `manager` for 13F;
- feed: `who`, `dataset`, `keys`, `asof`, `ok`, `behind` (from freshness `keys`, `data_asof`, `last_ok_at`, `keys_behind`);
- job: `kind`, `fires`, `cron`, `heavy`, `mem`, `when`;
- product: `kind`, `verdict`, `detail`, `code`, `asof`, `finished`, `cpu`, `engine`, `notes`, `art`, `stale` (from the manifest's `verdict`, `verdict_detail`, `code_sha`, `data_asof`, `finished_at`, `cpu_seconds`, `engine`, `notes`, `id`). **`p:desk.report` carries only `kind`, `finished` and `stale`.**
- `p:research`: `producer`, `strategies` (count); no verdict;
- results: the table fields named in the prototype's results section;
- strategy: `producer`, `strategy`, `universe`, `symbols`, `capital`;
- book: `strategies`, `capital` (sum of fractions), `incubation_cap`, `canary_cap`, `vol_target`; from a usable `book_summary`, also `positions`, `kill`, `limits` (count) and `summary_asof`; otherwise `summary_why`;
- limit: `kind`, `scope`, `trips`;
- kill: `state`, `trips_on`.

Series order on the daily ring (`at.i`): ETFs in `World.etfs` order (by universe); equities in `inp.order` (cluster order, the rest alphabetical); FRED in `World.fred` order; FF5 then MOM; filers in `universes.json` order.

A product with no artifact, or a feed with no freshness, gets `status: "missing"` and a `why`.

- [ ] **Step 1: Write the failing tests.** Build a small `Inputs` in a helper `_inputs(**over)`:
  - the real `load_world()` and `job_clock(load_schedules(), date(2026, 10, 28))`;
  - the real `load_roster()`;
  - `Product`s built from small DataFrames and manifests with provenance (`detail.symbol` "NVDA"; overlay provenance with `{"source": "cov.league"}`, `{"source": "vol.forecast_league"}`; daily with `{"source": "farm.sweep"}`);
  - a `desk.report` product whose `book_summary` has one row: limits `aapl-cap` (instrument:AAPL), `tech-cap` (sector:TECH) and `dd-cap` (portfolio); `trips_on ["dd-cap"]`; `kill_switch "armed"`; `positions 5`.

```python
def test_every_edge_follows_a_rule():                          # spec §5: "an edge with no rule is a bug"
    g = build_graph(_inputs())
    k = [n["k"] for n in g["nodes"]]
    for a, b, kind in g["edges"]:
        assert (k[a], k[b]) in RULES[kind], (g["nodes"][a]["id"], g["nodes"][b]["id"], kind)
    assert {e[2] for e in g["edges"]} <= set(RULES)

def test_the_roster_gives_23_strategies():
    g = build_graph(_inputs())
    st = [n for n in g["nodes"] if n["k"] == "strategy"]
    assert len(st) == 23
    assert sum(n["m"]["producer"] == "signals.daily" for n in st) == 21

def test_the_book_comes_from_book_summary():
    g = build_graph(_inputs())
    ids = _ids(g)
    assert {"lim:aapl-cap", "lim:tech-cap", "lim:dd-cap", "kill"} <= ids
    assert _edge(g, "d:AAPL", "lim:aapl-cap", "read") and not _edges_into(g, "lim:tech-cap", "read")
    assert _edge(g, "lim:dd-cap", "kill", "trip") and _edge(g, "kill", "book", "halt")

@pytest.mark.parametrize("why", ["absent", "empty", "stale"])
def test_without_a_usable_summary_there_are_no_limits(why):
    g = build_graph(_inputs(book_summary=why))
    assert not [n for n in g["nodes"] if n["k"] in ("limit", "kill")]
    assert not [e for e in g["edges"] if e[2] in ("limit", "trip", "halt")]
    assert _node(g, "book")["m"]["summary_why"]

def test_a_missing_artifact_is_a_missing_node():
    n = _node(build_graph(_inputs(drop="cov.league")), "p:cov.league")
    assert n["status"] == "missing" and n["why"]

def test_product_reads_come_from_provenance():
    g = build_graph(_inputs())
    assert _edge(g, "p:farm.sweep", "j:signals.daily", "read")
    assert _edge(g, "p:cov.league", "j:signals.overlay", "read")
    assert _edge(g, "d:NVDA", "j:cov.league", "read")

def test_the_desk_report_node_carries_only_its_run_times():
    assert set(_node(build_graph(_inputs()), "p:desk.report")["m"]) == {"kind", "finished", "stale"}
```

- [ ] **Step 2: Run them to see them fail.** `cd quant && uv run --frozen pytest -q tests/test_machine_graph.py`. Expected: FAIL (import).

- [ ] **Step 3: Implement `graph.py`.** Follow the prototype `graph.py`'s order: the world, then the clock, feeds, products, results, then the desk. Emit `ring` and `at` instead of the prototype's `r` and `a`. `node()` ignores duplicate ids; `edge()` drops an edge whose ends are absent or equal. Do not place anything by angle here.

- [ ] **Step 4: Run them to see them pass.** Expected: PASS; ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine/graph.py quant/tests/test_machine_graph.py
git commit -m "machine: the graph -- spec §5's rings and one rule per edge kind, the book's limits only from a fresh book_summary"
```

---

### Task 5: Product lenses and extras

**Files:**
- Create: `quant/src/ohcamel_quant/machine/lenses.py`
- Modify: `quant/src/ohcamel_quant/machine/graph.py` (put `m.lens` on product nodes)
- Test: `quant/tests/test_machine_graph.py`

**Interfaces:**
- Consumes: `Product` (Task 4).
- Produces:
  - `LENS_TABLES: dict[str, tuple[str, ...]]`: the tables each lens reads.
  - `product_lens(kind: str, p: Product) -> tuple[dict | None, str | None]`: the lens, or None with the reason.
  - `extras(inp: Inputs) -> dict`: `{"fires": [[kind, hour], ...]}`, every fire of every clock entry.
  - Product nodes gain `m.lens` (when present) or `m.lens_why`.

| kind | table → lens |
|---|---|
| `farm.sweep` | `leaderboard` → `{"cells": <leaderboard rows>, "trials": p.rows["trials"], "board": [[cell_id, verdict, oos_sharpe_ann, dsr_farm, psr, pbo, n_combos], …] sorted by oos_sharpe_ann desc}` (the trials table is counted from its metadata, never loaded) |
| `vol.forecast_league` | `summary` → `{"models": [[model, mcs_rate, mean_rank, names], …]}` |
| `cov.league` | `league` → `{"estimators": [[estimator, universe, realized_vol, rank], …]}` |
| `risk.mc_atlas` | `summary` (alpha 0.99) → `{"measures": [[method, horizon, var, es, n_paths], …]}` |
| `models.xs_lgbm` | `holdout_deciles` → `{"deciles": [[decile, mean_next_month_return], …]}` |
| `regime.hmm` | `probs` → `{"date", "p_high", "weeks"}`: the last row with a non-null `p_high`, and the row count |
| `vol.surface_history` | `history` → `{"underlying": "SPY", "asof", "atm30": atm_iv_30d, "slices": n_slices, "names": <distinct underlyings>}`: SPY's newest row. The artifact records expiry slices, not contracts, so the reading says slices. |

Every other kind has no lens. Floats are rounded to 4 dp, and non-finite values become None.

- [ ] **Step 1: Write the failing tests.**
  - `test_each_lens_reads_its_table`: one small frame per kind; assert exact payloads.
  - `test_a_lens_with_a_missing_column_is_omitted_with_the_reason` (Review Focus 5): leaderboard without `oos_sharpe_ann` → `(None, "leaderboard has no column oos_sharpe_ann")`; `json.dumps(build_graph(...))` contains no `NaN`.
  - `test_extras_list_every_fire`: the count equals the sum of `len(j.fires)`.

- [ ] **Step 2: Run them to see them fail.** Expected: FAIL (import).
- [ ] **Step 3: Implement `lenses.py`** and call it from `build_graph`.
- [ ] **Step 4: Run them to see them pass.** Expected: PASS.
- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine quant/tests/test_machine_graph.py
git commit -m "machine: product lenses -- the farm's board, the leagues, the atlas, deciles, regime and surface, each from its own table or omitted with the reason"
```

---

### Task 6: Loading from the stores

**Files:**
- Create: `quant/src/ohcamel_quant/machine/load.py`
- Test: `quant/tests/test_machine_api.py`

**Interfaces:**
- Consumes:
  - `jobs.schedules.read_latest(conn, kind, now=…, root=…)` → `{"manifest", "stale"}`;
  - `jobs.artifacts.inside(root, path)`;
  - `jobs.db.connect`, `utcnow`, `iso`, `parse_iso`;
  - `warehouse.db.open_ro(settings=…)`, `WarehouseUnavailable`;
  - `warehouse.freshness.dataset_freshness(con, now)["datasets"]`;
  - `signals.roster.load_roster()`;
  - test helpers `lane_m_harness.publish_artifact(db, root, kind, tables, …)` and `warehouse.fixture_db.build_fixture_warehouse(path)`.
- Produces:
  - `load_products(db: Path, now: datetime) -> dict[str, Product]`: for each `PROD` kind and `"desk.report"`, the latest artifact. Only the tables in `LENS_TABLES` are read, plus `members` for the atlas, `cells` and `leaderboard` for the farm, `summary` for the vol league, `league` for the cov league, `summary` for the atlas, and `book_summary` for `desk.report`. Each table path is checked with `inside` before reading. `rows` holds `pyarrow.parquet.ParquetFile(f).metadata.num_rows` for every table the manifest lists. A missing table gives `why`, never an error.
  - `load_warehouse(settings: Settings, world: World, now: datetime) -> tuple[dict | None, dict[str, dict], list[str], dict[str, str]]`: (freshness datasets, readouts, equities in cluster order, why). It makes one SQL read: `SELECT ticker, date, adj_close FROM bars_daily WHERE ticker IN (…) AND date >= ?` from the newest date minus 380 days, pivoted wide. On `WarehouseUnavailable`, it returns `(None, {}, sorted equities, {"warehouse": str(e)})`.
  - `load_runs(db: Path, now: datetime, kinds: Collection[str]) -> list[dict]`: jobs that are queued or running, or started within the last 24 h, left-joined to `job_progress`, restricted to `kinds` (the clock's), ordered by `COALESCE(started_at, submitted_at)`. Each is a `Run` in the wire shape. Job `error` text is never included.
  - `load_inputs(db: Path, settings: Settings, now: datetime) -> Inputs`.

- [ ] **Step 1: Write the failing tests** in `test_machine_api.py`, with a `tmp_path` jobs DB and artifacts root, and a fixture warehouse (`build_fixture_warehouse(tmp_path / "wh.duckdb")`, with `Settings(warehouse_path=…, data_dir=tmp_path)`):
  - `test_an_empty_store_gives_missing_products`: no artifacts → every product node is `missing`; the graph still has 23 strategies and the book.
  - `test_published_artifacts_are_read`: publish `cov.league` with a `league` table → `p:cov.league` has its verdict and `m.lens.estimators`.
  - `test_a_locked_warehouse_leaves_the_graph_whole` (Review Focus 1): monkeypatch `load.open_ro` to raise `WarehouseUnavailable("locked")` → feeds `missing` with `why` "locked"; daily series present with no `spark`.
  - `test_runs_are_the_last_day_and_the_live`: enqueue three jobs (one finished 30 h ago, one finished 2 h ago, one running with progress 0.4) → `[r["state"] for r in runs] == ["done", "running"]` and `progress == 0.4`.
  - `test_readouts_come_from_the_fixture_warehouse`: `d:SPY` has `spark`, `ret1y` and `vol`.

- [ ] **Step 2: Run them to see them fail.** `cd quant && uv run --frozen pytest -q tests/test_machine_api.py`. Expected: FAIL (import).
- [ ] **Step 3: Implement `load.py`.**
- [ ] **Step 4: Run them to see them pass.** Expected: PASS.
- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine/load.py quant/tests/test_machine_api.py
git commit -m "machine: load -- latest artifacts inside the root, the warehouse read-only and absent-safe, and the last day's runs"
```

---

### Task 7: `GET /api/machine` and its cache

**Files:**
- Create: `quant/src/ohcamel_quant/machine/service.py`, `quant/src/ohcamel_quant/api/routers/machine.py`
- Test: `quant/tests/test_machine_api.py`

**Interfaces:**
- Produces:
  - `build_machine(db: Path, settings: Settings, now: datetime) -> dict`: `{"asof": iso(now), "nodes", "edges", "x": extras(…), "provenance": [Provenance.now("ohcamel-machine", store="jobs.sqlite+warehouse").to_dict()], "notes": [...]}`. The notes say what is missing, in words.
  - `watermark(db: Path) -> str | None`: `SELECT MAX(finished_at) FROM artifacts`.
  - `MachineCache(build: Callable[[datetime], dict], mark: Callable[[], str | None], ttl_s: float = 300.0)` with `get(now: datetime) -> dict`. It rebuilds when the TTL has passed or the watermark changed, under a `threading.Lock`, so concurrent first requests build once.
  - `router = APIRouter(prefix="/machine", tags=["machine"])`.
  - `get_machine_db_path() -> Path` (`jobs_db_path()`), overridable.
  - `GET /api/machine` → `{**cache.get(now), "runs": load_runs(db, now, kinds)}`. Module-level cache, one per process.

- [ ] **Step 1: Write the failing tests.**
  - `test_the_response_schema`: the top-level keys are exactly `{"asof", "nodes", "edges", "runs", "x", "provenance", "notes"}`; every node has `id, k, l, n, ring, at, m`; every edge is `[int, int, str]`.
  - `test_it_fits_the_budget`: `len(gzip.compress(r.content)) <= 250_000` on the fixture warehouse with every lens artifact published.
  - `test_it_builds_once_per_ttl_and_on_a_new_artifact`: a counting builder; two gets → 1 build; publish an artifact (the watermark moves) → 2; advance `now` past the TTL → 3.
  - `test_runs_are_fresh_inside_the_ttl`: get, enqueue and claim a job, get again → the new run appears with no rebuild.
  - `test_it_is_public_and_not_rate_limited`: `GET` with no auth returns 200 (uses the app's `client`, with `get_machine_db_path` overridden).

- [ ] **Step 2: Run them to see them fail.** Expected: FAIL (404 / import).
- [ ] **Step 3: Implement `service.py` and the router.** The router module is picked up by `api/app.py`'s `pkgutil` loop; no app change.
- [ ] **Step 4: Run the whole quant suite.** `cd quant && uv run --frozen ruff check && uv run --frozen pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit.**
```bash
git add quant/src/ohcamel_quant/machine quant/src/ohcamel_quant/api/routers/machine.py quant/tests/test_machine_api.py
git commit -m "api: GET /api/machine -- the lineage graph, built at most every five minutes or on a new artifact, with the day's runs always fresh"
```

---

### Task 8: Web foundation: three.js, the palette, the guard, `/tape`

**Files:**
- Modify: `quant/web/package.json` and `package-lock.json`, `quant/web/vite.config.ts`, `quant/web/scripts/check-bundle.mjs`, `quant/web/src/styles/tokens.css`, `quant/web/src/styles/tokens.test.ts`, `quant/web/src/lib/routes.ts`, `quant/web/src/lib/routes.test.ts`, `quant/web/src/shell/command.ts`, `quant/web/src/shell/command.test.ts`

**Interfaces:**
- Produces:
  - The `three@^0.169.0` dependency and `@types/three@^0.169.0` (dev). Run `npm install` so the lockfile records them.
  - `manualChunks`: `id.includes("node_modules/three/")` → `"three"`.
  - `--mx-*` tokens in `:root` (all themes; the machine is always dark). Also add `--mx-font-ui: system-ui, -apple-system, "SF Pro Text", "IBM Plex Sans", sans-serif` and `--mx-font-mono: ui-monospace, "SF Mono", "IBM Plex Mono", monospace`.
  - Route `{ path: "/tape", to: "/tape", title: "The Tape", code: "TAPE", nav: false, component: lazy(() => import("../pages/Front")) }`. `/` still renders `Front` until Task 18.
  - `command.ts` `PAGES.TAPE = "/tape"`.

- [ ] **Step 1: Write the failing tests.**
  - `tokens.test.ts`: `block(":root")` has each `--mx-*` with the exact Global Constraints hex; `contrast(--mx-ink, --mx-ground) >= 7` and `contrast(--mx-dim, --mx-ground) >= 4.5`; no `--mx-*` value is green (hue 90–150° with saturation > 0.2).
  - `routes.test.ts`: `resolves("/tape")`; `ROUTES.find(r => r.path === "/tape")?.code === "TAPE"`; TAPE is not in `navRoutes()`; the nav codes are unchanged.
  - `command.test.ts`: `parseCommand("TAPE")` → `{kind: "go", path: "/tape"}`.

- [ ] **Step 2: Run them to see them fail.** `cd quant/web && npm test -- src/styles/tokens.test.ts src/lib/routes.test.ts src/shell/command.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `check-bundle.mjs`, mirror the Plotly block:
  - `isThree(k, c)` is `c.name === "three"` or a `node_modules/three/` key;
  - fail if three is in the entry's static closure;
  - fail if any chunk importing it has a `src` outside `src/pages/machine/` and `src/pages/Machine.tsx`;
  - in the source walk, fail on `from "three"` or `"three/` outside those paths.

  Update the file's header comment to say so.

- [ ] **Step 4: Verify.** `npm test && npm run typecheck && npm run build`. Expected: PASS; `check-bundle: ok`; no three chunk yet (nothing imports it).

- [ ] **Step 5: Commit.**
```bash
git add quant/web/package.json quant/web/package-lock.json quant/web/vite.config.ts quant/web/scripts/check-bundle.mjs quant/web/src/styles quant/web/src/lib/routes.ts quant/web/src/lib/routes.test.ts quant/web/src/shell/command.ts quant/web/src/shell/command.test.ts
git commit -m "web: the machine's foundation -- three.js confined to the machine's chunk by the bundle guard, its palette as tokens, and The Tape at /tape"
```

---

### Task 9: Wire types, the fixture, and live runs

**Files:**
- Create: `quant/web/src/pages/machine/types.ts`, `fixture.ts`, `runs.ts`, `runs.test.ts`, `useMachine.ts`

**Interfaces:**
- Produces:
  - `types.ts`: the wire contract types above, plus `JobEvent = { type: "job"; id: string; state: Run["state"]; progress: number | null; message: string | null }` (the SSE `data` of `/api/jobs/events`, `quant/src/ohcamel_quant/api/routers/jobs.py:276`).
  - `fixture.ts`: `export const FIXTURE: MachineOut`, a small, hand-written graph that obeys every §5 rule. It has:
    - `feed:prices`;
    - `d:SPY`, `d:NVDA` and `d:AAPL` with readouts, plus `s:`, `m:` and `o:` for NVDA, and `f:VIXCLS`;
    - jobs `ingest.bars_daily` (18.5), `vol.forecast_league` (21.5), `farm.sweep` (23), `signals.daily` (18.75), `signals.overlay` (several fires), `desk.report` (16.5), `regime.hmm`;
    - their products, with verdicts, notes (the overlay's real `exposure_scale … = min(1, 0.1 / 0.033877); covariance lw_constant_corr on 504 sessions, …` note and the daily's real `0 written, 0 written late, 1 written again …, 17 held, 0 already present, 3 refused; …` note) and lenses;
    - two farm cells, two strategies, and the book with a summary;
    - limits `aapl-cap` (instrument:AAPL) and `dd-cap`, and `kill` (armed);
    - runs, one of them running.

    Also export `FIXTURE_NO_SUMMARY` (no limits, no kill, `book.m.summary_why` set) and `FIXTURE_BROKEN` (the farm product with `lens` absent and `lens_why` set).
  - `runs.ts`: `applyJobEvent(runs: Run[], e: JobEvent): { runs: Run[]; unknown: boolean }`. It is pure: it patches `state`, `progress` and `msg` of the run with `e.id`; an unseen id returns `unknown: true` and the runs unchanged.
  - `useMachine.ts`: `useMachine(): { data: MachineOut | undefined; error: unknown; runs: Run[] }`.
    - It reads `useApiQuery<MachineOut>("/machine", undefined, { staleTime: 60_000, refetchInterval: 300_000 })`.
    - It opens `new EventSource("/api/jobs/events")` and applies each `job` event with `applyJobEvent`.
    - An unknown id calls `refetch()`.
    - On `error`, it closes the stream, polls every 60 s (`refetchInterval` switches), and retries the stream after 5 minutes.
    - It closes on unmount.

- [ ] **Step 1: Write `runs.test.ts`** (a known id patched; terminal states kept; an unknown id → `unknown: true`, runs `===` the input; a duplicate event is idempotent). Also add a `fixture.test.ts` that recomputes each edge's (from-kind, to-kind) and checks it against a TS copy of `RULES`, so the fixture cannot drift from §5.
- [ ] **Step 2: Run them to see them fail.** `npm test -- src/pages/machine`. Expected: FAIL (import).
- [ ] **Step 3: Implement the four files.**
- [ ] **Step 4: Run them to see them pass,** then `npm run typecheck`. Expected: PASS.
- [ ] **Step 5: Commit.**
```bash
git add quant/web/src/pages/machine
git commit -m "web: the machine's wire types, a rule-checked fixture, and runs patched live from the job stream"
```

---

### Task 10: Layout

**Files:**
- Create: `quant/web/src/pages/machine/layout.ts`, `layout.test.ts`

**Interfaces:**
- Consumes: `MachineOut` (Task 9) and an id → index map.
- Produces:
  - `RADIUS: Record<Ring, number>`; `HEIGHT = 0.34`.
  - `hourAngle(h: number): number`; `toXYZ(r: number, a: number, dy = 0): [number, number, number]`, which returns `[r·sin a, 0.34·r + dy, −r·cos a]` (prototype line 329).
  - `spread(center: number, n: number, width: number): number[]`; `circularMean(angles: number[]): number`.
  - `RESULT_WIDTH = { "p:farm.sweep": 0.95, "p:vol.forecast_league": 0.22, "p:cov.league": 0.36, "p:risk.mc_atlas": 0.22 }`.
  - `layout(out: MachineOut, byId: Map<string, number>): { pos: Float32Array; angle: Float32Array }` (3 floats a node in `pos`). The rules:
    - Daily-ring sections are laid end to end in the order etf, eq, fred, ff, 13f, with a 6-slot gap, starting at `hourAngle(0.6)`.
    - `{of: "d:T"}` series take T's angle.
    - Jobs sit at `hourAngle(h)`.
    - Products take their job's angle; `p:research` is its `beside` angle + 0.12.
    - Results use `spread(parentAngle, n, RESULT_WIDTH[of] ?? 0.3)[i]`.
    - Strategies sit at `i/n·2π + hourAngle(19.5)`; limits and kill at `i/n·2π + hourAngle(1)`.
    - The book is at the origin.
    - A feed sits at the circular mean of the angles of the series its pulled jobs write; `feed:sec` gets + 0.18.

- [ ] **Step 1: Write the tests.**
  - `hourAngle(18) ≈ 3π/2`.
  - `toXYZ(7, 0)` ≈ `[0, 2.38, -7]`.
  - `circularMean([6.2, 0.1])` is within 0.15 of 0, not near π.
  - `spread(1, 3, 0.4)` → `[0.8, 1, 1.2]`, and `spread(1, 1, 0.4)` → `[1]`.
  - On `FIXTURE`: `j:vol.forecast_league`'s angle is `hourAngle(21.5)`; `p:farm.sweep`'s angle equals `j:farm.sweep`'s; `m:NVDA`'s equals `d:NVDA`'s; `book` sits at `[0, 0, 0]`; every coordinate is finite.
- [ ] **Step 2: Run them to see them fail.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them to see them pass.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "web: machine layout -- rings narrowing to the book, the clock in New York hours, feeds at their targets' mean"`

---

### Task 11: Webs

**Files:**
- Create: `quant/web/src/pages/machine/web.ts`, `web.test.ts`

**Interfaces:**
- Produces:
  - `GraphIndex = { out: MachineOut; byId: Map<string, number>; up: number[][]; down: number[][]; edgeAt: Map<string, number> }`, where `up[i]` lists sources feeding `i`, `down[i]` lists targets, and `edgeAt` is keyed `"a>b"`.
  - `indexGraph(out: MachineOut): GraphIndex`.
  - `bfs(adj: number[][], start: number, limit = 7): Map<number, number>` (node → depth, start at 0).
  - `type WebMode = "web" | "reads" | "feeds" | "none"`.
  - `webOf(gi, i, mode): { up: Map<number, number>; down: Map<number, number> }` ("reads" → up only; "feeds" → down only; "none" → empty).
  - `edgeLevel(depth: number): number`, which is `1 / (1 + 0.55 · depth)`.
  - `directReaders(gi, i): number[]` (depth-1 `job` nodes downstream).
  - `countKinds(gi, ids: Iterable<number>): Record<NodeKind, number>`.
  - `summaryLine(c: Record<NodeKind, number>): string`, e.g. `"3 series · 2 jobs · 1 product · 1 book"`: order series, feed, job, product, result, strategy, limit, kill, book; zero counts omitted; singular for 1; numbers via `fmt.num`.
- Also create `fmt.ts`:
  - `num(x, d = 0)` (en-US grouping);
  - `pct(x, d = 1, signed = false)` (with `signed`, a leading `+` or U+2212 minus; returns use it, levels such as volatility do not);
  - `hm(h)` (`18.5` → `"6:30 PM"`);
  - `dur(s)` (`3846` → `"64 min"`, `40` → `"40 s"`, `7300` → `"2 h 2 min"`);
  - `plural(n, word)`.

  Each returns `"—"` only for null or undefined input.

- [ ] **Step 1: Write the tests.** BFS on a chain and a diamond (the shortest depth wins), and a cycle (it terminates); webs on `FIXTURE` from `d:NVDA` (the down web contains `book`; `directReaders` are exactly the jobs with a read edge from `d:NVDA`); the mode filters; the `summaryLine` format; the `fmt` cases above.
- [ ] **Step 2–4: Run, implement, pass.** `npm test -- src/pages/machine/web.test.ts`.
- [ ] **Step 5: Commit.** `git commit -m "web: machine webs -- what a node reads and feeds, by depth, and the plain-word counts"`

---

### Task 12: Readings

**Files:**
- Create: `quant/web/src/pages/machine/notes.ts`, `notes.test.ts`, `readings.ts`, `readings.test.ts`, `testkit.ts` (test-only helpers, imported only by tests)

**Interfaces:**
- Produces:
  - `notes.ts`:
    - `parseDailyNote(notes: string[]): { written: number; late: number; again: number; held: number; present: number; refused: number } | null` matches the first note against `/^(\d+) written, (\d+) written late, (\d+) written again .*?, (\d+) held, (\d+) already present, (\d+) refused/` (`signals/daily.py:482`).
    - `parseOverlayNote(notes: string[]): { scale: number; target: number; forecast: number; estimator: string | null; sessions: number | null } | null` matches the note against `/exposure_scale ([\d.]+) = min\(1, ([\d.]+) \/ ([\d.]+)\)(?:; covariance (\S+) on (\d+) sessions)?/` (`signals/overlay.py:430`).
  - `readings.ts`:
    - `type Seg = { t: string } | { b: string } | { ref: string; t: string }` (plain, a bold figure, a link to a node id).
    - `interface Reading { title: string; paras: Seg[][]; caveats: string[] }`.
    - `reading(gi: GraphIndex, i: number, ctx: { runs: Run[]; now: Date }): Reading`.
  - `testkit.ts`:
    - `allowedNumbers(out: MachineOut): Set<string>`: every numeric value in the fixture rendered through each `fmt` function and precision used, plus `0 … nodes + edges` as plain integers.
    - `numbersIn(text: string): string[]`.

The per-kind content follows spec §6.5, with the prototype's `reading()` (lines 458–511) as the wording reference. Every number comes from `gi`.
- **Daily ticker:**
  - its tracks (the `s:`/`m:`/`o:` nodes that exist), one-year return and volatility;
  - the count and names of `directReaders`;
  - what it reaches (strategies count; the book);
  - if no strategy is reached but the book is, it says it reaches the book through `p:signals.overlay`, and through a limit only when a series → limit edge from it exists;
  - the three highest `peers`.
- **Feed:** keys, as-of, last pull, keys behind; the sentence that slow series lag by nature appears only when `behind > 0`.
- **Job:** `when` and `hm(hour)`; "never in session" if heavy; the runs and cpu-seconds of its kind in `ctx.runs` in the last 24 h; direct reads; what it reaches.
- **Products:**
  - **farm:** cells, `trials`, the best board row's Sharpe and DSR, the count of passing cells (the sentence "no cell passes" when zero), and the farm-strategy count and their capital fraction from the strategy nodes.
  - **daily signals:** `parseDailyNote` counts, plus the two caveats found in its notes, matched case-insensitively by "leverage" and by "open", and quoted verbatim into `caveats`.
  - **overlay:** forecast against target and the scale from `parseOverlayNote`, plus the estimator. If the parse fails, quote the first note verbatim.
  - **vol league, cov league, atlas, xs, regime, surface:** a sentence from the lens.
  - **anything else:** its notes, verbatim.
  - `m.stale` adds "past its schedule's max age". `status: "missing"` gives a reading that says only that it is missing, and `why`.
- **Farm cell:** variants, Sharpe, PSR, DSR, PBO, verdict, detail, and the `st:` it becomes.
- **Strategy:** capital fraction, symbols, producer, its farm cell.
- **Book:** strategies and capital; with a summary, the position count, limit count and kill-switch state "as of" `summary_asof`; without one, `summary_why` stated plainly. Never thresholds, names of positions or sizes; the wire does not carry them.
- **Limit / kill:** kind and scope / state and trips list, as of the summary.

- [ ] **Step 1: Write the tests.**
  - `notes.test.ts`: both real note strings parse to the expected numbers; garbage → null.
  - `readings.test.ts`:

```ts
for (const [name, fx] of [["full", FIXTURE], ["no summary", FIXTURE_NO_SUMMARY], ["broken", FIXTURE_BROKEN]] as const) {
  it(`every reading is honest on the ${name} fixture`, () => {
    const gi = indexGraph(fx), allowed = allowedNumbers(fx);
    fx.nodes.forEach((_, i) => {
      const text = flatten(reading(gi, i, { runs: fx.runs, now: new Date(fx.asof) }));
      expect(text).not.toMatch(/NaN|undefined|null/);
      for (const n of numbersIn(text)) expect(allowed, `${fx.nodes[i].id}: ${n}`).toContain(n);
    });
  });
}
it("the overlay reading states forecast, target and scale", ...)        // "3.4%", "10%", "1.00"
it("NVDA names only its direct readers", ...)                           // depth-1 jobs only
it("the book without a summary says why and names no limit", ...)
it("the daily-signals caveats are quoted", ...)
```

- [ ] **Step 2–4: Run, implement, pass.** `npm test -- src/pages/machine/notes.test.ts src/pages/machine/readings.test.ts`.
- [ ] **Step 5: Commit.** `git commit -m "web: machine readings -- what each thing is and did, in plain words, every figure from the response and the system's own caveats quoted"`

---

### Task 13: Search, the day, deep links

**Files:**
- Create: `quant/web/src/pages/machine/search.ts`, `search.test.ts`, `day.ts`, `day.test.ts`, `url.ts`, `url.test.ts`

**Interfaces:**
- Produces:
  - `search(gi, q: string, limit = 12): number[]`:
    - matches a case-insensitive substring of `id`, `l`, `n` or `k`;
    - ranks an exact label first, then kind in the order book, product, feed, job, strategy, result, series (daily before other tracks), then label length;
    - ignores an empty query.
  - `day.ts`:
    - `dayRuns(runs, now: Date): Run[]` (the last 24 h, by start);
    - `heaviest(runs, now): Run | null`;
    - `runningRuns(runs): Run[]`;
    - `REPLAY_S = 26`;
    - `replayFrame(runs, now, frac: number): { at: Date; active: Run[] }` (the day window is `now − 24 h … now`; active means start ≤ at < end).
  - `url.ts`:
    - `type Tab = "tour" | "read" | "day"`; `interface View { sel: string | null; tab: Tab; step: number }` (step 1–8);
    - `parseView(search: string, has: (id: string) => boolean): View`: an unknown `sel`, a bad `tab` or an out-of-range `step` falls back to `null`, `"tour"` and `1` field by field;
    - `viewSearch(v: View): string` omits defaults.

- [ ] **Step 1: Write the tests.**
  - Search: `"nvda"` ranks `d:NVDA` before `m:NVDA`; `"farm"` ranks `p:farm.sweep` before the `c:` cells; `"book"` → `book` first.
  - Day: the 24 h window bounds; `heaviest` ignores failed runs; `replayFrame` at 0 and 1.
  - URL (Review Focus 4): `parseView("?sel=d:NOPE&tab=read&step=3", has)` → `{sel: null, tab: "read", step: 3}`; `"?sel=<img src=x onerror=alert(1)>"` → `sel: null`; `"?step=99"` → `1`; the round trip `viewSearch(parseView(s))` is stable.
- [ ] **Step 2–4: Run, implement, pass.**
- [ ] **Step 5: Commit.** `git commit -m "web: machine search, the day's runs and replay timeline, and deep links that restore only what exists"`

---

### Task 14: The tour

**Files:**
- Create: `quant/web/src/pages/machine/tour.ts`, `tour.test.ts`

**Interfaces:**
- Consumes: `reading`, `Seg` (Task 12); `webOf`, `directReaders`, `countKinds` (Task 11); `heaviest(runs, now)` (Task 13).
- Produces: `TourStep = { key: string; sel(gi, ctx): number | null; cam: "overview" | "node" | "yours"; title(gi, ctx): string; body(gi, ctx): Seg[][] }` and `STEPS: TourStep[]` (8). `cam` "overview" is the prototype's `[target (0, 1.4, 0), sph(27, 0.92, 0.35)]`; "yours" is `[(0, 1.2, 0), sph(23, 0.62, −0.5)]`; "node" frames the selection (Task 16).

Steps (spec §6.8; the prototype's `STEPS`, lines 556–573, are the wording reference; every figure is interpolated):
1. **overview:** the title is the machine "at {NY time} on a {weekday}" from `asof`; the body uses the strategy count.
2. **`feed:prices`:** `hm` of `j:ingest.bars_daily`; the counts of daily tickers, minute, options and FRED series, filings, and all series.
3. **`d:NVDA`:** its tracks; `directReaders` count; the minute → vol-league and options → surface sentences only when those edges exist; strategies reached, or the overlay-and-limit sentence under the Task 12 rule.
4. **the clock:** selects `heaviest(runs, now)` (the done run with the largest `cpu` in the last 24 h); states its name, its direct read count, `dur(end − start)` and cpu-seconds. With no runs, it selects nothing and says no job has run in the last day.
5. **`p:farm.sweep`:** cells, trials, best Sharpe, passing count.
6. **`p:signals.overlay`:** the target, forecast and scale from `parseOverlayNote`.
7. **`book`:** strategies, capital fraction (one figure if all equal, else the range), incubation cap; limit count and kill-switch state, or `summary_why`.
8. **"Now it's yours":** starting points, listing only ids that exist: `d:SPY`, `p:risk.mc_atlas`, `p:kronos.shadow`, `kill`, `f:VIXCLS`, the farm cell with the best Sharpe, `feed:sec`.

A step whose subject is missing or stale says so instead of asserting.

- [ ] **Step 1: Write the tests.**
  - Every step's `sel` resolves on `FIXTURE` (or is null by design).
  - Step 4 selects the heaviest run's job and, with `runs: []`, returns null and its body says no job ran.
  - Step 7 on `FIXTURE_NO_SUMMARY` contains `summary_why`.
  - No step body on `FIXTURE` contains a number outside `allowedNumbers`.
  - With `p:farm.sweep` missing, step 5 says it is missing.
- [ ] **Step 2–4: Run, implement, pass.**
- [ ] **Step 5: Commit.** `git commit -m "web: the machine's tour -- eight steps inward from the world to the book, written from live data at render time"`

---

### Task 15: Label placement

**Files:**
- Create: `quant/web/src/pages/machine/labels.ts`, `labels.test.ts`

**Interfaces:**
- Produces:
  - `LABEL_PRI: Record<string, number>`: selected 0, in-web 1, book 2, feed 3, product 4, kill 5, job 6, section 7, hour 8.
  - `interface LabelCand { key: string; x: number; y: number; w: number; h: number; pri: number }`.
  - `placeLabels(c: LabelCand[], pad = 4): Set<string>`: greedy by `pri`, then input order; it drops any candidate whose padded rect intersects one already placed.
  - `seriesLabelsAllowed(webSize: number): boolean`, which is `webSize < 140`.

- [ ] **Step 1: Write the tests.** Two overlapping candidates → the lower `pri` wins; touching-but-padded candidates collide; disjoint candidates are all placed; 2,000 candidates are placed in under 20 ms.
- [ ] **Step 2–4: Run, implement, pass.**
- [ ] **Step 5: Commit.** `git commit -m "web: machine labels -- placed by priority, never overlapping"`

---

### Task 16: The scene

**Files:**
- Create: `quant/web/src/pages/machine/palette.ts`, `quant/web/src/pages/machine/scene.ts`

**Interfaces:**
- Consumes: `layout` (Task 10), `GraphIndex`, `webOf`, `edgeLevel` (Task 11), `placeLabels`, `LABEL_PRI`, `seriesLabelsAllowed` (Task 15), `Run`.
- Produces:
  - `palette.ts`: `type Palette = Record<"ground" | "ink" | "bright" | "dim" | "line" | "structure" | "reads" | "feeds" | "fail", THREE.Color>`; `readPalette(el: Element): Palette` reads `--mx-*` through `getComputedStyle`.
  - `scene.ts`:
    ```ts
    export interface SceneHandlers { onHover(i: number | null, x: number, y: number): void; onPick(i: number | null): void;
      onLabels(placed: { key: string; text: string; x: number; y: number; cls: string }[]): void }
    export interface SceneApi {
      setWeb(i: number | null, mode: WebMode): void;            // lights the web, dims the rest
      focus(i: number | null, cam: "overview" | "node" | "yours" | "keep"): void; // flies (or cuts, reduced motion)
      setViewOffset(leftPx: number, rightPx: number, bottomPx: number): void;
      setRuns(runs: Run[]): void;                                  // running jobs pulse with a progress arc
      setReplay(frac: number | null, active: number[]): void;      // the replay hand and active webs
      zoom(deltaY: number): void;
      dispose(): void;
    }
    export function createScene(canvas: HTMLCanvasElement, gi: GraphIndex, opts: { palette: Palette; reducedMotion: boolean; nowNY: () => number /* hours */ }, h: SceneHandlers): SceneApi;
    export function webglAvailable(): boolean;
    ```

The spec §6.6–6.7 values, all of which this task must honour. Port the prototype's geometry, nodes, edges, structure, camera, pick and frame loop (lines 190–345, 537–553, 658–740) to TypeScript, with these changes:
- **Points:** an additive `ShaderMaterial` (a soft disc with a crisp core; no texture, no gradient call). Sizes in px: series 2.4 (other tracks 1.8), job 6, product 9, result and strategy 5.5, feed 11, book 18; limit and kill 5.5. Base colour by kind and verdict: FAIL `fail`, PASS `bright`, `missing` `dim` at half alpha.
- **Edges:** one `LineSegments`. Each edge is a quadratic curve in 16 segments. The control point is at 72% of the angular way to the target, at the mean radius, at height `max(hA, hB) + 0.25 + 0.12·|ΔR|`. A per-vertex `level` attribute holds 0.07 unselected, 0.014 outside the web, and `edgeLevel(depth)` inside it. Up-web edges are `reads`, down-web edges `feeds`, otherwise `line`. A pulse uniform moves along each lit edge in the flow direction; it is off under reduced motion.
- **Structure:** ring circles; 24 hour ticks; a tick for each `x.fires` entry; the session arc 09:30–16:00 in `reads` at low alpha; the hand at `nowNY()`.
- **Labels:** labels are not WebGL. Each frame, project the candidates (selected, in-web, book, feeds, products, kill, jobs, section names, hours; series only when `seriesLabelsAllowed`) and call `onLabels` with `placeLabels`' survivors. React renders them as text (Task 17), never as HTML.
- **Camera:** `OrbitControls` with damping, `minPolarAngle` 0.12, `maxPolarAngle` 1.38, `enablePan` false.
  - `focus` flies for 1.5 s with ease-in-out-cubic.
  - A web over 60 nodes frames the whole machine at distance 19.5.
  - A small web frames its node, with its angle brought to the near side.
  - The book, limits, kill and strategies frame the centre.
  - Under reduced motion, every move is a cut.
- **Picking:** the nearest node in screen space: 6 px for series, 12 px for everything else. A click on empty space picks null.
- **View offset:** `camera.setViewOffset` so the open panels never cover the machine.
- **Running jobs:** a pulse ring plus a progress arc at the job node, and pulses on its read edges.
- **Disposal:** dispose of the renderer, geometries and materials, the RAF and the listeners.

- [ ] **Step 1: Implement `palette.ts` and `scene.ts`.** No unit test (WebGL); Task 17 verifies them in the browser.
- [ ] **Step 2: Verify the types and guards.** `cd quant/web && npm run typecheck && node scripts/check-style.mjs`. Expected: PASS; the style guard reports nothing in `scene.ts`.
- [ ] **Step 3: Commit.** `git commit -m "web: the machine's scene -- additive points and fibres that arch toward what they feed, the clock's structure, focus by web and depth"`

---

### Task 17: The page, the shell variant, and `/`

**Files:**
- Create: `quant/web/src/pages/Machine.tsx`, `quant/web/src/pages/machine/Segs.tsx`, `TopBar.tsx`, `FocusPanel.tsx`, `SidePanel.tsx`, `Legend.tsx`, `machine.css`
- Modify: `quant/web/src/lib/routes.ts` and `routes.test.ts`, `quant/web/src/shell/AppShell.tsx`, `quant/web/src/shell/hotkeys.ts`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - Routes: `/` → `lazy(() => import("../pages/Machine"))`, title "The machine", code FRONT.
  - `HotkeyHandlers.onSlash?: () => void`: when set, `/` calls it instead of `onCommand`; Cmd/Ctrl-K is unchanged.
  - `AppShell`: when `loc.pathname === "/"`, it renders `div.oc-app.oc-app-machine` holding the skip link, `<CommandLine>`, `<main id="main">`, `<KeyMap>`, and **no** `Masthead`, `Tape`, `FunctionNav` or `Footer`. It passes `onSlash: () => document.getElementById("mx-search")?.focus()`.

The page (spec §6.4):
- **`TopBar`** (46 px):
  - the wordmark;
  - plain-word `Link`s, in this order: Tape `/tape`, Markets `/markets`, Risk `/risk`, Portfolios `/portfolio`, Optimizer `/optimize`, Research `/research`, Volatility `/options`, Rates `/macro`, Companies `/company`, Desk `/deck`, System `/system`;
  - the web-mode segmented control;
  - `input#mx-search`, with results from `search()`. Enter with no result hands the text to `parseCommand` → `commandPath` → `navigate`;
  - status `formatDateline(now, sessionOf(useOps().data))`.
- **Canvas:** `position: fixed; inset: 0`, `aria-hidden="true"`, created once in a `useEffect` with `createScene`.
- **`FocusPanel`** (left, 380 px, shown when selected):
  - the id line;
  - the name;
  - the verdict stamp and detail;
  - the provenance row (`code`, `asof`, `cpu`, `engine`, `finished`);
  - the lens (ticker: sparkline as an inline SVG polyline in `--mx-ink`, last / one year / volatility, tracks, "moves most with" chips; farm: board table; leagues and atlas: their tables; xs: decile bars as SVG rects; others none);
  - "reads" and "feeds", each a `summaryLine` plus chips (closest first, at most 48, then "+N more").
- **`SidePanel`** (right, 410 px), with tabs: **Tour** (default; step dots, back/next, `STEPS[step].body`), **Reading** (`reading()` with `caveats` listed), **The day** (`dayRuns` as rows of time, name and `dur`/cpu; running rows first with progress; a replay button sweeping `REPLAY_S`, which highlights each row while `replayFrame` has it active; a click on a row selects its job).
- **Hover tooltip:** from `onHover`: the node's name, kind and verdict, as text beside the pointer.
- **Labels:** from `onLabels`: absolutely positioned `<span>`s with text children.
- **`Legend`:** selected, what it reads, what it feeds, failed a gate, "drag to turn · click to select".
- **`Segs`:** renders `Seg[]`; a `ref` is a `<button>` that selects its node.
- **URL:** `parseView` on load; `viewSearch` with `navigate({ search }, { replace: true })` on every change.
- **Keys:** the tour takes ↑/↓ and PageUp/PageDown while the Tour tab is open; Esc deselects. The wheel steps the tour when the Tour tab is open and the pointer is not over a panel (60 px of accumulated delta, a 900 ms lock); otherwise `scene.zoom`.
- **Panels:** translucent `--mx-ground` at 0.78 alpha with `backdrop-filter: blur(10px)`, 1 px `--mx-line` hairlines, radius 0, `--mx-font-ui`, ids and numbers in `--mx-font-mono` with `font-variant-numeric: tabular-nums`. `aria-live="polite"` on the Reading and Tour bodies.
- **Loading and error:** while `/api/machine` loads, a one-line "Reading the machine…" in `--mx-dim`. On error, the error text and a retry, never a fallback graph.

- [ ] **Step 1: Write the failing test.** `routes.test.ts`: `ROUTES.find(r => r.path === "/")?.title === "The machine"`; the `/` code is FRONT. (`hotkeys.test.ts` covers only the pure `chordStep`; `onSlash` is checked in the browser in Step 4.)
- [ ] **Step 2: Run them to see them fail.** Expected: FAIL.
- [ ] **Step 3: Implement** the components, the page, the shell variant and the route flip.
- [ ] **Step 4: Verify in the browser.**
  1. Start the backend (`cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/python -m ohcamel_quant serve --port 8090`) and `npm run dev`.
     - An empty local store shows the world, the clock, the roster and every product `missing`. The page must read honestly that way too.
     - For a populated check, use a scratch data dir:
       - copy the droplet's jobs DB read-only (`ssh ohcamel`, `sqlite3 … ".backup /tmp/mx.sqlite"`, then fetch and delete the backup; the data dir is in `docs/status.md`);
       - copy the artifact directories its latest rows point to;
       - rewrite the copy's `artifacts.path` prefix to the scratch dir;
       - build a fixture warehouse with `build_fixture_warehouse`.
     - Never write to the droplet's data.
  2. Open `http://localhost:5173/` with the preview tools.
  3. Check:
     - no console errors;
     - `/api/machine` is 200;
     - the tour's eight steps all render;
     - clicking `d:NVDA` lights its web and fills both panels;
     - `/?sel=p:farm.sweep&tab=read` restores;
     - Esc deselects;
     - `/` focuses search;
     - Cmd-K opens the command line;
     - `/tape` shows The Tape with its masthead.
  4. Take screenshots at 1440 × 900.
  5. Measure fps over 5 s with `requestAnimationFrame` counting in the page (`javascript_tool`): the target is ≥ 60 on this M-series machine.
- [ ] **Step 5: Run the full web checks.** `npm run lint && npm run typecheck && npm test && npm run build`. Expected: PASS; check-bundle lists the `three` chunk imported only by the machine chunk; entry ≤ 250 KB.
- [ ] **Step 6: Commit.**
```bash
git add quant/web/src
git commit -m "web: / is The Machine -- the lineage graph as an instrument with focus and side panels, the tour, the day and deep links; the masthead steps aside on / only"
```

---

### Task 18: Phone, reduced motion, and the list view

**Files:**
- Create: `quant/web/src/pages/machine/ListView.tsx`
- Modify: `quant/web/src/pages/Machine.tsx`, `quant/web/src/pages/machine/machine.css`, `TopBar.tsx`

**Interfaces:**
- Consumes: `indexGraph`, `reading`, `search`, `parseView`, `webglAvailable`.
- Produces: `ListView({ gi, view, onSelect })`: "The machine as a list". It is an outline by ring (in `RADIUS` order, outside in), each node a disclosure with its reading and its reads/feeds as links, the same search, and the same `?sel=` deep link. Plain HTML, no canvas.

- **Under 860 px:**
  - the canvas fills the top 60 % of the viewport;
  - the side panel becomes a bottom sheet (40 %, scrollable) that also holds the focus content above the tabs;
  - the top bar collapses its links into a "Pages" `<details>` menu;
  - tap selects;
  - the tour advances by next/back and by scrolling the sheet.
- **`prefers-reduced-motion: reduce`** (read with `matchMedia`, live): `createScene(…, { reducedMotion: true })`; replay steps run by run (one per second) instead of sweeping.
- **No WebGL** (`!webglAvailable()`): render `ListView` in place of the canvas and panels. Also link "The machine as a list" (`?view=list`) from the top bar for everyone; the canvas stays `aria-hidden`.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify in the browser.**
  - `resize_window` to 375 × 812: no horizontal scroll (`document.documentElement.scrollWidth <= innerWidth`); the sheet scrolls; tap selects.
  - Emulate reduced motion (`matchMedia` override via preview, or OS setting): selection cuts, with no pulses.
  - `?view=list`: the outline renders, `?sel=d:NVDA&view=list` opens that node, and search works.
- [ ] **Step 3: Run the checks.** `npm run lint && npm run typecheck && npm test && npm run build`. Expected: PASS.
- [ ] **Step 4: Commit.** `git commit -m "web: the machine on a phone, with reduced motion, and as a list -- the same readings and deep links without WebGL"`

---

### Task 19: Screenshots and accessibility

**Files:**
- Modify: `quant/web/scripts/screenshots.mjs`
- Create: `docs/media/quant/machine/` (generated PNGs)

**Interfaces:**
- Produces:
  - `ROUTES` gains `"/tape"`.
  - A `MACHINE_SHOTS` list `["/?step=1", "/?step=2", "/?step=5", "/?step=7"]`, shot at 1440 × 900 and 375 × 812 into `<out>/../machine/machine-step<n>-<w>.png`, each after waiting for `canvas` and 2.5 s of settling.
  - Chromium is launched with `--enable-unsafe-swiftshader --use-angle=swiftshader` so headless WebGL works.
  - The existing axe and no-horizontal-scroll checks also run on these shots.

- [ ] **Step 1: Implement the script change.**
- [ ] **Step 2: Run it** against the local backend: `cd quant/web && npm run build && npm run screenshots -- http://localhost:8090`. Expected: exit 0; no serious or critical axe violation on `/` or `/tape`; eight machine PNGs written.
- [ ] **Step 3: Look at every PNG** (Read tool) against the spec's anti-goals: no decorative marks, readable labels, panels not covering the machine, nothing reading `undefined` or `—` where data exists.
- [ ] **Step 4: Commit.**
```bash
git add quant/web/scripts/screenshots.mjs docs/media/quant/machine
git commit -m "web: screenshots of the machine's tour at desktop and phone width, with axe on /"
```

---

### Task 20: Ship (owner go-ahead required)

**Files:**
- Modify: `docs/status.md`

- [ ] **Step 1: Run the whole suite on the branch.**
  - `cd quant && uv run --frozen ruff check && uv run --frozen pytest -q`
  - `cd web && npm ci && npm run lint && npm run typecheck && npm test && npm run build && bash ../../deploy/test/csp_hash_test.sh dist/index.html`

  Expected: all PASS.
- [ ] **Step 2: Request a whole-branch review** (superpowers:requesting-code-review) and fix what it finds.
- [ ] **Step 3: Stop and ask the owner** to approve the merge and deploy, showing the Task 19 screenshots. Do not push or deploy without that approval.
- [ ] **Step 4: On approval,** merge to `main`, push, and deploy the public host by the runbook in `docs/status.md`. This is the public profile only: no `--live`, and no edit to the droplet's `book.sexp`.
- [ ] **Step 5: Check acceptance on the deployed host.**
  - `curl -s -o /dev/null -w '%{size_download} %{time_total}\n' --compressed https://ohcamel.ajaiupadhyaya.com/api/machine`: under 250 KB, and the page loads within 2.5 s.
  - In the browser: the tour runs end to end; a running job is visible while one runs; a deep link restores; `?view=list` works; phone width works.
  - Measure fps at 1440 × 900. The spec's ≥ 30 fps on integrated graphics needs such a machine: measure it if one is available, otherwise report it as not measured.
  - After the next weekday 16:30 New York `desk.report`, the book shows the live limits and kill switch "as of" that run.
- [ ] **Step 6: Update `docs/status.md`** (what runs where, the new endpoint, `book_summary`), commit, and push.
- [ ] **Step 7: Show the owner the live page.** The last acceptance line, "the owner does not call it vibecoded", is theirs to judge.
