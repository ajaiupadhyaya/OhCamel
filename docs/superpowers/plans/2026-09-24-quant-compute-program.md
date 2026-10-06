# OhCamel Quant — the Compute Program: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a **program plan**: Part I is the design every lane argues from, Part II fixes the contracts between lanes, Part III gives Phase 0 as bite-sized tasks, and Part IV gives every later lane its task list, acceptance gates and the first task of each lane — *write that lane's detailed plan with superpowers:writing-plans before touching its code*. Part V says how to run it as a dynamic workflow.

**Goal:** Turn OhCamel Quant into a full quantitative-finance workstation that puts the droplet's idle capacity to work on useful, scheduled computation over real data. That computation includes Monte Carlo risk, model training, universe-wide backtest farms and option-surface history, built on native Rust kernels, a job system and an analytical warehouse. It sits behind a professional, dense, dark-first "terminal" UI, and the Star Wars instruments get their own console tab.

**Architecture:** Three tiers on one 2-vCPU / 4 GB droplet, each in its own cgroup with a CPU weight and a memory ceiling:

- **Real-time tier**, protected: the OCaml engine.
- **Interactive tier:** Caddy, the FastAPI API and a Rust host-telemetry daemon.
- **Batch tier**, preemptible: a Python worker that runs Rust kernels over a DuckDB warehouse and writes versioned artifacts that the API serves read-only.

Heavy requests become jobs. Nightly and intraday schedules fill the idle CPU with work whose every result is stored, dated, provenanced and shown on the site.

**Tech Stack:**

| Area | Stack |
|---|---|
| Real-time engine | OCaml 5 (Incremental, Async; multicore Domains for the scenario grid) |
| Kernels and host daemon | Rust 1.80+ (PyO3 + maturin kernels with rayon; `tiny_http` for `hostd`) |
| API, worker, models | Python 3.12 (FastAPI, DuckDB, LightGBM, statsmodels, arch) |
| Queue and ledger | SQLite |
| Frontend | React 18 + TypeScript. uPlot for 2-D charts; Plotly kept only for 3-D surfaces |
| Build and deploy | Docker images built in CI and pulled by the droplet (the finish plan's Stage 1) |

**Spec:** Part I of this document is the design; the brief is the owner's instruction of 2026-09-24, quoted in I.1. It builds on and does not repeat:
- `docs/superpowers/specs/2026-09-19-the-finish.md`: binding for the engine lane.
- `docs/superpowers/specs/2026-09-24-quant-flight-deck-design.md`: the Flight Deck backend, already built.
- `docs/superpowers/plans/2026-09-19-final-completion.md`: its Stage 1 is absorbed into Phase 0 here, and its Stages 2A–6 become Lane E.

---

## Global Constraints

These apply to every task. A task's requirements include this section.

1. **Real data only.** Nothing synthetic may stand in for missing data. A number that cannot be computed is `null` with a reason, and a result computed from a fallback source says so in `provenance`/`notes`. This is the Quant platform's existing rule (`quant/README.md`: "nothing substitutes an invented number for missing data").
2. **Every payload and every artifact carries `provenance` and `notes`.** Every artifact also carries `code_sha`, `data_asof`, `started_at`, `finished_at`, `cpu_seconds` and `peak_rss_bytes` (contract II.3).
3. **No busy-work.** No CPU-second is spent on anything whose output is not stored and shown. No mining, no load generators outside `tools/perf/`, no "keep-alive" loops. Utilization is a consequence of useful schedules, never a target in itself.
4. **The live engine is never starved.** Its stabilize latency p99 may not regress more than 10 % against the Phase 0 baseline while the batch tier runs at full load (gate G-RT, task 0.5). Memory ceilings and `oom_score_adj` make the batch tier the first thing the kernel kills.
5. **Memory is the binding constraint.** On 2026-09-24 the host showed 3.9 GB total, 1.46 GB available and 231 MB already in swap. Every job declares a memory class (II.2), and the worker refuses to start a job whose class exceeds the host's `mem_available` minus a 512 MB reserve.
6. **Nothing is built on the droplet.** Images, including the Rust wheels and binaries, are built and published by CI. The droplet pulls them (finish plan Stage 1, Tasks 9–10). A 20-minute OCaml build plus a Rust build would contend with the live engine.
7. **Analytics stay pure.** Modules under `quant/src/ohcamel_quant/{risk,portfolio,factors,backtest,options,macro,fundamentals,models,kernels}` do no I/O. Only routers and job handlers touch `MarketData` or the warehouse.
8. **The Quant side never trades.** No code under `quant/` or `native/` may call an order route. The existing rule for `lib/` (CI greps it for trading paths) is extended to `quant/` and `native/` in task 0.3.
9. **The human owns hypotheses and kill decisions.** Every trained predictive model is an experiment with a pre-registration (`research/experiments/EXP-Q*/preregistration.md`) that the owner approves before its first run. Its gates follow the `quant-rigor` skill: Deflated Sharpe, PSR, PBO, purged CV, costs. A model that fails its gates is shown as failing.
10. **Point-in-time or say so.** A feature may use only data available at its timestamp. Where a dataset is not point-in-time (index constituents today are a survivor list), every page and artifact built on it carries a `survivorship` note.
11. **Tests with hand-derived values.**
    - Every numeric assertion in a new test cites its hand derivation or its reference implementation in a comment.
    - Every Rust kernel has a parity test against its pure-Python reference at a stated tolerance (II.4).
    - Mutating a kernel's core rule must fail a test. Each lane's review runs at least three mutations.
12. **Published counts stay true.** Test counts appear in `README.md`, `docs/status.md` and `docs/overview.md`, and are checked by `scripts/check-counts.sh`. Each task that adds tests updates the count's source of truth in the same commit: `lib/verified.ml` for OCaml; `quant/README.md`'s count for Quant, which task 0.3 brings under `check-counts.sh`.
13. **Commit style.** The repo's commit style is `<area>: <what changed> -- <why>`. Areas are `kernel`, `desk`, `quant`, `native`, `web`, `deploy`, `docs`, `plan` and `verified`. Every commit ends with the attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the executing model's own line). One task means one or more commits on the lane's branch. Nothing is merged to `main` without the lane's gate passing, and the owner deploys.
14. **Accessibility and mobile.** Every page works at 380 px with no horizontal page scroll, in both themes, keyboard-operable, with `prefers-reduced-motion` honoured.

## Review Focus

These are the five failure modes most likely to hurt a user or the owner that no single task's happy-path tests exercise. Each has a pinning test in the task named.

1. **A batch job's memory growth makes the kernel OOM-kill the live engine or the API.** Expected: the worker is killed first, the engine never. Pinned in **0.3**, which asserts `oom_score_adj` and `mem_limit` ordering in the rendered compose, and in **B3**, where admission control refuses a job over budget, tested with a fake `hostd` reading.
2. **A heavy job starts at 09:31 New York and the engine's ticks slow down.** Expected: the batch tier is preempted by weight and the engine's p99 stays within 10 %. Pinned in **0.5**, which provides the load harness and G-RT gate script, and in **B4**, whose scheduler test proves "heavy" jobs never start inside the session window.
3. **A Rust kernel silently diverges from its reference.** Examples: a different quantile convention, an off-by-one in the tail count, a non-seeded RNG. Expected: identical results to tolerance, and bit-identical results for the same seed and thread count. Pinned in **A1**'s parity harness, which every kernel task (A2–A7) extends.
4. **Stale or partial warehouse data presented as current.** Example: a vendor fails at 02:00, the artifact is built on yesterday's bars, and the page shows it as today's. Expected: `data_asof` is on every artifact and panel, and a freshness lamp goes amber after one missed schedule. Pinned in **C3**, where the ingest test fails the vendor and asserts the stored `data_asof`, and in **D5**, where the panel shows the stale badge.
5. **Look-ahead or survivorship in training.** Expected: purged, embargoed CV; features lagged to their availability; the survivorship note present. Pinned in **M1**'s leakage tests: a feature built from `t+1` data must make the pipeline's `assert_point_in_time` raise, and the purge must remove overlapping labels.

---

# Part I — Program design

## I.1 The brief, and what it means here

The owner, 2026-09-24:

> "I still see the CPU usage only at 2%. If I'm paying for the full 100% why not build out the project so that it uses all that I'm paying for? I want the new star wars UI graphics/visualizations to have their own tab, I want a UI overhaul so that its not so vibecoded, and a more comprehensive system that utilizes more CPU and therefore more impressive, more useful. Utilizing real world data, training, etc. A full and incredible quantitative finance tool. Ocaml, Rust, and C/C++/C# use is encouraged."

Read against the facts:

- **The box.** It is a DigitalOcean `s-2vcpu-4gb`: two *shared* vCPUs (`DO-Regular`), 3.9 GB RAM, a 77 GB disk (12 GB used) and 2 GB of swap. At rest it runs Caddy (37 MB), the Quant API (401 MB, with a 1.5 GB limit) and the live engine (27 MB). The research service is not running. Load average is 0.2.
- **Memory binds before CPU.** Two cores can be kept busy all night. Four gigabytes cannot hold a 600-name × 20-year panel in pandas *and* a LightGBM fit *and* the API. So the batch tier streams from DuckDB, works in column chunks and runs one heavy job at a time.
- **Shared vCPUs.** Sustained 100 % is allowed, but the hypervisor may give the cycles to neighbours. That shows up as `steal` in `/proc/stat`. `hostd` (task 0.4) reports steal so the owner can see it. If steal is high, or the work outgrows the box, a resize is an owner decision (O-1). **Every lane is designed to run on today's box.**
- **What "use the CPU" means.** It means scheduled work that produces results a desk would pay for. Busy-looping would satisfy the literal ask and defeat its purpose (Global Constraint 3). The target is that the nightly batch window (16:30–09:00 New York, weekends all day) keeps the batch tier near its CPU cap until that night's schedule completes. The Compute page shows the utilisation, the jobs and their artifacts, so the use is visible and accountable.

## I.2 Languages, and why each one is where it is

| Language | Where | Why |
|---|---|---|
| **OCaml** | The live engine (`lib/`, `desk/`) and a new multicore scenario grid (Lane E) | It already is the real-time engine. OCaml 5 Domains give it a second core for full-revaluation scenarios without touching the tick path. |
| **Rust** | `native/kernels` (Python extension via PyO3) and `native/hostd` (daemon) | This is the hot loops' home: Monte Carlo, bootstrap, CSCV, GARCH likelihoods, backtest inner loops and SVI. It is memory-safe, uses rayon for parallelism, publishes wheels from CI and has no runtime. A static `hostd` binary uses about 3 MB of RAM. |
| **C** | Only as OCaml C stubs, and only if Lane E's benchmark shows the engine's covariance update is BLAS-bound beyond what Owl gives. | Adding C where Rust already serves Python would be a second native toolchain for no gain. |
| **C++** | **Not used.** | Every C++ use considered (QuantLib, pybind11 kernels) duplicates Rust's role or adds a large dependency with its own build. Revisit only if an owner-approved feature needs a C++-only library. |
| **C#** | **Not used.** | It would add a .NET runtime (about 100–200 MB resident) to a 4 GB box for no capability the stack lacks. Stated here so the omission is a decision, not an oversight. |
| **Python** | API, worker orchestration, model pipelines | This is where the data layer, the 653 tests and every analytic already live. Rust replaces its inner loops, not its structure. |
| **TypeScript** | The web app | Unchanged stack; its design is rebuilt. |

## I.3 The three tiers on one box

```
                   ┌────────────── droplet: 2 shared vCPU, 3.9 GB ───────────────┐
 ohcamel-rt.slice  │ ohcamel-live   OCaml engine      weight 4096  mem 512m  oom -800 │  protected
 ohcamel-web.slice │ caddy          proxy/TLS         weight 1024  mem 128m  oom -500 │  interactive
                   │ ohcamel-quant  FastAPI (API)     weight 1024  mem 1024m oom    0 │
                   │ ohcamel-hostd  Rust telemetry    weight 1024  mem  32m  oom -500 │
 ohcamel-batch     │ ohcamel-worker Python + Rust     weight  128  mem 1280m oom  800 │  preemptible
   .slice          │                kernels, cpus: 1.75 (never both cores whole)     │
                   │ ohcamel-research (daily signal)  weight  256  mem  384m oom  500 │
                   └──────────────────────────────────────────────────────────────────┘
 shared volume quant_data: /data/cache (Parquet), /data/warehouse.duckdb,
                           /data/jobs.sqlite, /data/artifacts/, /data/deck/recorder.sqlite
```

CPU weights only matter under contention. When the engine is idle, the worker takes up to 1.75 cores. When a tick arrives, the engine's weight of 4096 against 128 gets it the core.

The memory ceilings add up to 4.36 GB, more than the 3.9 GB box. That is deliberate: a ceiling is a cap, not a reservation. The engine also gets `mem_reservation: 256m`. The OOM order is the `oom_score_adj` column.

## I.4 What the compute produces

These are the products; Part IV builds them. Each is a scheduled job that writes an artifact, plus a page that reads it.

| # | Product | Schedule | CPU (est.) | Page |
|---|---|---|---|---|
| P1 | **Monte Carlo risk atlas**: 1-day and 10-day VaR/ES for the reference books and every universe member (filtered historical simulation and a Student-t copula), 1M paths per book, with an Euler split | nightly; 250k paths every 15 min in session | 20–40 min nightly | Risk |
| P2 | **Volatility forecast league**: GARCH, GJR, EGARCH, HAR-RV (on minute-bar realized vol) and EWMA for about 600 names. Scored out of sample (QLIKE, MSE, Diebold–Mariano, Model Confidence Set) | nightly refit | 30–60 min | Volatility → Forecasts |
| P3 | **Covariance forecast league**: sample, EWMA, Ledoit–Wolf, OAS, MP-denoised and a statistical factor model. Scored by realized minimum-variance portfolio risk | weekly | 10 min | Portfolio → Covariance |
| P4 | **Strategy farm**: 13 literature strategies × universes × parameter grids, with CSCV PBO, SPA, DSR and cost sensitivity. The Strategy Lab's leaderboard | nightly, incremental | 60–120 min | Research → Farm |
| P5 | **Cross-sectional return model (EXP-Q01)**: LightGBM on point-in-time features, purged walk-forward CV, monthly retrain, IC/decile spreads net of costs, and its gate verdict | monthly retrain, nightly score | 30–90 min | Research → Models |
| P6 | **Regime model (EXP-Q02)**: Gaussian HMM on macro and market features, nightly refit, and regime probabilities with their uncertainty | nightly | 5 min | Macro → Regimes |
| P7 | **Option-surface history**: SVI fits for 30 underlyings from each night's Cboe snapshot. This builds an IV, skew and term-structure history the site did not have, plus VRP | nightly | 10–20 min | Volatility → History |
| P8 | **Engine scenario grid (Lane E)**: full revaluation of the live book over 10,000 historical and hypothetical scenarios on a second OCaml Domain | every minute in session | about 0.3 core | Engine; Flight Deck |
| P9 | **Warehouse ingest**: daily bars for about 600 names over 20 years, minute bars for 50, FRED, French factors, SEC facts and 13F, all incremental | nightly and intraday | 10–30 min | Data freshness lamps everywhere |

Nightly total: roughly 3–6 core-hours against about 30 available core-hours per night. The batch tier will run near its cap for several hours each night and idle afterwards. That is the honest outcome of doing only useful work (constraint 3). The farm (P4) and the forecast leagues (P2/P3) widen their grids to fill the window as the kernels get faster.

## I.5 The UI overhaul — "Terminal"

The survey's findings, all of which the overhaul removes:
- **Editorial type:** Fraunces display headers of 32–48 px, a paper palette and a pointillist gradient wash.
- **Card soup:** 14 px-radius shadowed cards.
- **Explanatory copy:** subtitles on every page, section and panel; up to 22 info-tips per tab; question-shaped headers.
- **Low density:** 49 px table rows.
- **Stray formatting:** 150 inline styles and 32 `.toFixed` calls outside `lib/format`.
- **Bundle weight:** a 4.6 MB Plotly chunk on nearly every page.
- **Polling:** everywhere.

What replaces it:
- **Dark-first neutral palette.** One accent; semantic colours only (gain, loss, warn, stale, unknown). Light theme kept as a secondary.
- **Type:** Inter for UI, IBM Plex Mono for every number. No display serif.
- **Density:** a 20–24 px row height by default, with a `data-density` attribute for compact and comfortable.
- **Panels:** full-bleed grid of panels with hairline dividers, a 0–2 px radius and no shadows. A 28 px page header: title plus status chips, no subtitle.
- **Status bar** at the bottom: session, data freshness, engine, jobs, CPU. It is the lamp panel's small form, and it replaces the "Unavailable" cards.
- **Command line.** The palette becomes a command line with function codes (`SPY GP`, `PORT RISK 99 10D`, `JOB farm`), recent items and actions. Global keys: `g m`-style navigation, `1–9` for tabs, `[`/`]` for period, `?` for help. DataTable gets keyboard row navigation.
- **Charts:** uPlot for time series, bars and heatmaps, with a crosshair readout, inline series labels and no legend box. Plotly is kept only for 3-D surfaces and loaded only there.
- **Live updates:** one SSE stream per page (quotes, deck readings, job events) replaces polling.
- **Copy discipline:** no page or panel subtitles; info-tips only on metric labels; narrative moves to Methodology.
- **Information architecture:**
  - **Home:** Markets, Flight Deck
  - **Analyze:** Portfolio, Risk, Optimizer, Research (Strategy Lab, Farm, Models)
  - **Explore:** Volatility, Rates & Macro, Company
  - **System:** Compute, Live Engine, Methodology
- **Flight Deck: its own full-bleed console tab.** No `Page`/`Panel` chrome. It shows the three instruments in a bezel grid: the targeting computer (limits), the radar (risk) and the lamp panel (feeds, jobs, CPU), plus the session tape. The limits editor and marks sit in a side drawer. The existing pure logic (`quant/web/src/pages/deck/{scope,radar,model,limits,segments}.ts` and its Vitest tests) is kept; only the presentation is redone.

## I.6 Phases and lanes

```
Phase 0  Foundations (sequential)       0.1 → 0.2 → {0.3, 0.4, 0.5} → 0.6
            │
Phase 1  Parallel lanes ─────────────────────────────────────────────────────
            ├─ Lane A  Rust kernels            A0 (plan) A1 … A7
            ├─ Lane B  Jobs, worker, schedules B0 (plan) B1 … B6
            ├─ Lane C  Warehouse + ingest      C0 (plan) C1 … C6
            └─ Lane D  Terminal design system  D0 (plan) D1 … D5
            │           (A, B, C, D touch disjoint directories; see II.7)
Phase 2  Products on the tier (parallel where independent)
            ├─ Lane M  Models & products P1–P7   M0 (plan) M1 … M8   needs A1, B*, C*
            ├─ Lane D2 Page migration + new pages D6 … D12           needs D1–D5, B5
            └─ Lane F  Flight Deck console      F0 (plan) F1 … F4    needs D1–D3, 0.4
Phase 3  Lane E  Engine: finish-plan Stages 2A–6 + scenario grid (P8)   needs 0.5
Phase 4  Lane H  Hardening, docs, review, launch   H1 … H6
```

---

# Part II — Contracts between lanes

These are fixed now so that the lanes can run in parallel. A lane that needs to change a contract stops and amends this Part in a `plan:` commit before any code depends on the change.

## II.1 Directory layout

```
native/                         Rust workspace (new)
  Cargo.toml                    [workspace] members = ["hostd", "kernels"]
  hostd/                        telemetry daemon (task 0.4)
  kernels/                      PyO3 extension crate `ohcamel_kernels` (Lane A)
quant/src/ohcamel_quant/
  kernels/                      dispatcher + pure-Python reference implementations (Lane A)
    __init__.py                 public API (II.4); picks Rust if importable
    reference.py                the reference for every kernel
  jobs/                         queue, worker, scheduler, artifacts (Lane B)
  warehouse/                    DuckDB schema, ingest, readers (Lane C)
  models/                       training pipelines P2, P3, P5, P6 (Lane M)
  products/                     P1, P4, P7 job handlers (Lane M)
  api/routers/{jobs,ops,artifacts,warehouse}.py   (Lanes B, 0.4, M, C)
quant/web/src/
  styles/tokens.css             rewritten by D1
  design/                       new primitives (Lane D): Grid, Pane, StatusBar, CommandLine, Kbd…
  charts/                       uPlot wrappers (D4)
  pages/…                       migrated by D6–D12
research/experiments/EXP-Q01/, EXP-Q02/   pre-registrations (owner-approved)
deploy/                         new services ohcamel-worker, ohcamel-hostd
```

## II.2 Jobs (`jobs.sqlite`, Lane B owns)

```sql
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,          -- ULID
  kind          TEXT NOT NULL,             -- e.g. 'risk.mc_atlas', 'farm.sweep', 'ingest.bars_daily'
  params        TEXT NOT NULL,             -- canonical JSON (sorted keys); hash is the dedupe key
  params_hash   TEXT NOT NULL,
  priority      INTEGER NOT NULL,          -- 0 interactive (user-submitted), 1 intraday, 2 nightly
  mem_class     TEXT NOT NULL CHECK (mem_class IN ('S','M','L')),  -- S ≤256 MB, M ≤640 MB, L ≤1152 MB
  heavy         INTEGER NOT NULL,          -- 1 = never starts inside 09:25–16:05 New York on a session day
  state         TEXT NOT NULL CHECK (state IN ('queued','running','done','failed','cancelled')),
  submitted_at  TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  artifact_id   TEXT,                      -- set when done
  cpu_seconds   REAL, peak_rss_bytes INTEGER,
  submitted_by  TEXT NOT NULL              -- 'schedule:<name>' | 'api'
);
CREATE INDEX jobs_state ON jobs(state, priority, submitted_at);
CREATE UNIQUE INDEX jobs_live_dedupe ON jobs(kind, params_hash) WHERE state IN ('queued','running');
```

- **Job handler protocol:** `def run(params: dict, ctx: JobContext) -> ArtifactSpec`. `JobContext` gives `warehouse` (read-only DuckDB connection), `market` (the `MarketData` facade), `progress(fraction, message)`, `cancelled() -> bool` and `threads` (1 or 2, from the scheduler).
- **API** (Lane B, task B5):
  - `POST /api/jobs {kind, params}` → `202 {id, state}`; kinds are allow-listed.
  - `GET /api/jobs/{id}`, `GET /api/jobs?kind=&state=&limit=`, `DELETE /api/jobs/{id}` (cancel).
  - `GET /api/jobs/events` is an SSE stream of `{"type":"job","id","state","progress","message"}`.

## II.3 Artifacts (`/data/artifacts/<kind>/<id>/`, Lanes B and M)

```
manifest.json   {"id","kind","params","params_hash","code_sha","data_asof","started_at",
                 "finished_at","cpu_seconds","peak_rss_bytes","engine":"rust"|"python",
                 "provenance":[…],"notes":[…],"survivorship":null|"<note>","tables":["name",…]}
<table>.parquet one per table the product defines
```

- Artifacts are **immutable**. "Latest" is `SELECT … ORDER BY finished_at DESC LIMIT 1` over an `artifacts` table in `jobs.sqlite` (`id, kind, params_hash, finished_at, path, data_asof`).
- API (Lane M):
  - `GET /api/artifacts/{kind}/latest?params_hash=`
  - `GET /api/artifacts/{kind}/{id}` (manifest)
  - `GET /api/artifacts/{kind}/{id}/{table}` (JSON frame, `lib/serialize.frame` shape)
- Retention: each kind keeps its newest 30 artifacts plus the newest artifact per month (B6).

## II.4 Kernel API (`ohcamel_quant.kernels`, Lane A owns)

Every function exists in `reference.py` (NumPy) and in Rust. The dispatcher chooses Rust when `ohcamel_kernels` imports, unless `OHCAMEL_QUANT_KERNELS=python`, and records the choice in provenance as `engine`. Signatures (float64 C-contiguous arrays; returns are plain Python or NumPy):

```python
def fhs_paths(std_resid: np.ndarray, sigma_path_params: GarchParams, weights: np.ndarray,
              horizon: int, n_paths: int, seed: int, threads: int) -> np.ndarray   # (n_paths,) portfolio P&L, fraction
def copula_t_paths(returns: np.ndarray, weights: np.ndarray, nu: float, horizon: int,
                   n_paths: int, seed: int, threads: int) -> np.ndarray             # (n_paths,)
def var_es_from_pnl(pnl: np.ndarray, alpha: float) -> tuple[float, float]           # same tail-count rule as risk.core.tail_count
def stationary_bootstrap_means(x: np.ndarray, mean_block: float, reps: int, seed: int, threads: int) -> np.ndarray
def cscv_pbo(perf: np.ndarray, n_partitions: int, threads: int) -> dict             # {'pbo','logits','n_combinations','selected','is_sharpe','oos_sharpe'}
def garch_nll(params: np.ndarray, r: np.ndarray, kind: str) -> float                # kind in {'garch','gjr','egarch'}, Student-t
def garch_fit(r: np.ndarray, kind: str, x0: np.ndarray | None) -> GarchFitResult
def backtest_weights(prices: np.ndarray, target_w: np.ndarray, decision_idx: np.ndarray,
                     cost_bps: float, borrow_bps: float, rf: np.ndarray) -> BacktestPath
def svi_fit(k: np.ndarray, w: np.ndarray, weights: np.ndarray) -> SviFitResult
def realized_vol_minute(ts_ns: np.ndarray, px: np.ndarray, session_bounds: np.ndarray) -> np.ndarray  # daily RV
```

- **Determinism:** the same `(seed, threads)` gives bit-identical output. Across thread counts, results are equal in distribution, and the tests check moments to a stated tolerance.
- **Amended 2026-10-06 (Lane A, A4):** `cscv_pbo` also returns `selected` (the in-sample winner n* per combination), `is_sharpe` and `oos_sharpe` (its per-period Sharpe in and out of sample). `backtest/validation.cscv_pbo` needs them for the performance-degradation regression and the selection counts, which it has always published; without them the kernel would cover only the cheap half of the computation. The three original keys are unchanged.
- **RNG:** Rust uses `rand_chacha::ChaCha20Rng` with `set_stream(thread_index)`. The reference uses `np.random.Generator(np.random.PCG64(seed))`. So parity for random kernels is statistical (II.4 tests), and parity for deterministic kernels is to 1e-10 relative.

## II.5 Warehouse (`/data/warehouse.duckdb`, Lane C owns)

```sql
CREATE TABLE universe_members (universe TEXT, ticker TEXT, name TEXT, added DATE, source TEXT,
                               survivorship TEXT, PRIMARY KEY (universe, ticker));
CREATE TABLE bars_daily  (ticker TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                          adj_close DOUBLE, volume DOUBLE, source TEXT, fetched_at TIMESTAMP,
                          PRIMARY KEY (ticker, date));
CREATE TABLE bars_minute (ticker TEXT, ts TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                          volume DOUBLE, source TEXT, PRIMARY KEY (ticker, ts));
CREATE TABLE fred        (series TEXT, date DATE, value DOUBLE, fetched_at TIMESTAMP, PRIMARY KEY (series, date));
CREATE TABLE factors     (dataset TEXT, factor TEXT, date DATE, value DOUBLE, PRIMARY KEY (dataset, factor, date));
CREATE TABLE option_snapshots (underlying TEXT, asof DATE, expiry DATE, strike DOUBLE, cp TEXT,
                          bid DOUBLE, ask DOUBLE, last DOUBLE, volume DOUBLE, open_interest DOUBLE,
                          source TEXT, PRIMARY KEY (underlying, asof, expiry, strike, cp));
CREATE TABLE sec_facts   (cik TEXT, ticker TEXT, tag TEXT, unit TEXT, period_end DATE, filed DATE,
                          form TEXT, value DOUBLE, PRIMARY KEY (cik, tag, unit, period_end, filed));
CREATE TABLE ingest_log  (dataset TEXT, key TEXT, ran_at TIMESTAMP, rows INTEGER, status TEXT,
                          detail TEXT, data_asof DATE);
```

- **Writers:** only the worker's ingest jobs write. The API opens the file `read_only=True`. DuckDB allows one writer process, which is why ingest runs only in the worker.
- **Point-in-time rule:** `sec_facts.filed` is the availability date; a feature may use a fact only when `filed <= feature_date`.

## II.6 Telemetry (`hostd`, task 0.4)

`GET http://ohcamel-hostd:9100/v1/host` returns:

```json
{"version":1,"interval_s":5,"cpus":2,"now_ms":1790000000000,
 "latest":{"t_ms":…,"cpu":0.43,"steal":0.01,"iowait":0.00,"load1":0.8,
           "mem_total":4105236480,"mem_available":1530000000,"swap_used":242221056,
           "groups":{"ohcamel-rt.slice":{"cpu":0.02,"mem":28000000}, "...": {}}},
 "history":[{"t_ms":…,"cpu":…,"steal":…,"mem_available":…,"groups_cpu":{"ohcamel-rt.slice":0.02}}]}
```

- `cpu`, `steal` and `iowait` are fractions of all CPUs over the last interval; each group's `cpu` is in cores.
- History holds 720 samples (1 h at 5 s).
- The API proxies it at `GET /api/ops/host` (503 when unset or unreachable, like the engine bridge). The worker's admission control reads it.

## II.7 Lane isolation (for parallel agents)

| Lane | Owns (may edit) | Reads only |
|---|---|---|
| A | `native/kernels/**`, `quant/src/ohcamel_quant/kernels/**`, `quant/tests/test_kernels_*.py`, CI job `native` | everything |
| B | `quant/src/ohcamel_quant/jobs/**`, `api/routers/jobs.py`, `quant/tests/test_jobs_*.py`, deploy worker service | kernels API (II.4) |
| C | `quant/src/ohcamel_quant/warehouse/**`, `api/routers/warehouse.py`, `quant/tests/test_warehouse_*.py` | jobs protocol (II.2) |
| D | `quant/web/src/{styles,design,charts,shell,components}/**`, `quant/web/package.json` | pages (migrated in D2) |
| M | `quant/src/ohcamel_quant/{models,products}/**`, `api/routers/artifacts.py`, `research/experiments/EXP-Q*/**` | A, B, C |
| F | `quant/web/src/pages/Deck.tsx`, `quant/web/src/pages/deck/**` | D |
| E | `lib/**`, `bin/**`, `desk/**`, `bench/**`, `test/**`, `web/**` | — |

Shared files touched by more than one lane (`quant/src/ohcamel_quant/config.py`, `api/app.py`, `deploy/docker-compose.yml`, `.github/workflows/ci.yml`, `quant/web/src/lib/routes.ts`) are edited **additively**: new keys, services and jobs are appended, and each lane rebases before merging. Conflicts are resolved by keeping both.

---

# Part III — Phase 0: Foundations (bite-sized)

Phase 0 is sequential except where noted. Its gate (**G0**) is at the end.

### Task 0.1: Land the Flight Deck as it stands

The Flight Deck backend is committed on `quant/deck` (`2189e04`, 653 Quant tests). Its frontend is an uncommitted work in progress in `quant/web`. It builds, but at last check its typecheck failed on:
- a casing clash: `Deck.tsx` imports `./deck/Radar` while the file is `pages/deck/RadarScope.tsx` and the logic module is `pages/deck/radar.ts`;
- a `DeckQuery` type whose `refetch` is not assignable to `UseQueryResult`.

It lands functional now and is restyled in Lane F.

**Files:**
- Modify: `quant/web/src/pages/Deck.tsx` (imports and the `DeckQuery` type)
- Keep: `quant/web/src/pages/deck/**`, `quant/web/src/lib/routes.ts`, `quant/web/src/styles/tokens.css`, `quant/web/src/components/Icon.tsx`, `quant/web/package.json`, `quant/web/package-lock.json`, `quant/web/src/README.md`

**Interfaces:**
- Consumes: `/api/deck/reading`, `/api/deck/books`, `/api/deck/tape` (the spec's contract, implemented in `quant/src/ohcamel_quant/api/routers/deck.py`).
- Produces: the `/deck` route; `quant/web/src/pages/deck/{scope,radar,model,limits,segments}.ts`, which Lane F reuses.

- [ ] **Step 1:** `git -C /Users/ajaiupadhyaya/Documents/OhCamel switch quant/deck && git status --short quant/web`. Expect `Deck.tsx`, `deck/` and the shared-file edits above.
- [ ] **Step 2:** `cd quant/web && npm ci && npm run typecheck`. Record every error.
- [ ] **Step 3:** Fix the import to `import { RadarScope } from "./deck/RadarScope";` (check the exported name with `grep -n "export" src/pages/deck/RadarScope.tsx`). Replace the hand-written `DeckQuery` type with `UseQueryResult<DeckModel, unknown>` from `@tanstack/react-query`, or make each source's hook return that type. Fix any remaining errors without changing `deck/*.ts` logic.
- [ ] **Step 4:** Run all four:

  ```bash
  npm run typecheck && npm run lint && npm test && npm run build
  ```

  Expected: all pass, with Vitest reporting the deck tests.
- [ ] **Step 5:** Check it by hand. Start the API and the web dev server:

  ```bash
  cd quant && OHCAMEL_QUANT_DATA_DIR=$TMPDIR/qdata .venv/bin/python -m ohcamel_quant serve --port 8090
  ```

  ```bash
  cd quant/web && npm run dev
  ```

  Open `http://localhost:5173/deck` at 1280 px and 380 px, in both themes, and switch through all three sources. The engine source must show the bridge's 503 message. There should be no console errors.
- [ ] **Step 6:** Update the Quant test count in `README.md` line 30 ("600 tests") to the actual `pytest -q` count (653 plus any added).
- [ ] **Step 7:** Commit (`web: the Flight Deck page -- …`). Merge `quant/deck` into `main` with `--no-ff`. Do not deploy; deployment is owner step O-2.

### Task 0.2: Absorb the finish plan's Stage 1 (Operations)

The finish plan's Stage 1 is a prerequisite of everything here: images built in CI and published to GHCR, pull-based deploys, journal backup and restore, digest pins and lint. Its Tasks 8–20 and 13a are already specified bite-size in `docs/superpowers/plans/2026-09-19-final-completion.md` and are **not repeated here**. Execute them from that plan, with these amendments:

- [ ] **Step 1:** Rebase the in-flight branches on `main` and finish their open reviews:
  - `s1/t8`: CI hygiene. Remove the throwaway seed and revert commits.
  - `s1/t12`: journal-backup. The review has 4 blocking fixes open.
  - `s1/t13a`: market clock. The review has 3 blocking fixes open.

  Delete `s1/t13`, which equals t12, and `desk/a6-ops`, which is empty. Delete the stale worktrees `../OhCamel-s1t*` and `.claude/worktrees/agent-a0cbd73…` after checking `git -C <wt> status --short` is clean.
- [ ] **Step 2:** Tasks 9–10 (GHCR publish, pull deploy) must also publish `ghcr.io/ajaiupadhyaya/ohcamel-quant:<sha>`, which that plan predates. Leave room in the publish job's matrix for `ohcamel-hostd` (task 0.4) and `ohcamel-worker` (Lane B), which reuses the quant image with another `command`.
- [ ] **Step 3:** Task 14's backup timer must also back up `/data/deck/recorder.sqlite`, and later `/data/jobs.sqlite`. Use `sqlite3 .backup` for SQLite and a `CHECKPOINT` then file copy for DuckDB once Lane C exists. Put the list of files in `deploy/backup.list`, one per line, so later lanes append rather than edit the script.
- [ ] **Step 4:** Gate: the finish plan's Stage 1 acceptance rows (its Acceptance 9, plus 1 for the counts) pass on `main`.

### Task 0.3: Resource governance: slices, weights, ceilings, OOM order

**Files:**
- Modify: `deploy/docker-compose.yml` (every service)
- Create: `deploy/test/resource_budget_test.sh`
- Modify: `.github/workflows/ci.yml`. The lint job already runs every `deploy/test/*.sh`. Extend the build-and-test job's "nothing in `lib/` trades" grep to `quant/` and `native/`.
- Modify: `scripts/check-counts.sh`, to add a `quant-tests` marker with its source of truth in `quant/README.md`'s `TESTS = N` line (add that line)

**Interfaces:**
- Produces: the slice names `ohcamel-rt.slice`, `ohcamel-web.slice` and `ohcamel-batch.slice`, which `hostd` (0.4) reads and the worker service (B2) is placed in.

- [ ] **Step 1: Write the failing test** `deploy/test/resource_budget_test.sh`:

```bash
#!/usr/bin/env bash
# The tiers of docs/superpowers/plans/2026-09-24-quant-compute-program.md I.3:
# the engine is protected, the batch tier is preemptible, and the kernel's OOM
# killer reaches the batch tier first. Renders the compose file with dummy
# values for the variables it requires and asserts each service's budget.
set -euo pipefail
cd "$(dirname "$0")/../.."
export OHCAMEL_DEMO_HOST=x OHCAMEL_LIVE_HOST=x OHCAMEL_LIVE_USER=x OHCAMEL_LIVE_HASH=x \
       OHCAMEL_PUBLIC_HOST=x OHCAMEL_ACME_EMAIL=x
json=$(docker compose -f deploy/docker-compose.yml --profile live --profile demo config --format json)
python3 - "$json" <<'PY'
import json, sys
svc = json.loads(sys.argv[1])["services"]
want = {  # service: (slice, cpu_shares, mem bytes, oom_score_adj)
    "ohcamel-live":     ("ohcamel-rt.slice",    4096,  512 * 2**20, -800),
    "caddy":            ("ohcamel-web.slice",   1024,  128 * 2**20, -500),
    "ohcamel-quant":    ("ohcamel-web.slice",   1024, 1024 * 2**20,    0),
    "ohcamel-research": ("ohcamel-batch.slice",  256,  384 * 2**20,  500),
}
bad = []
for name, (slice_, shares, mem, oom) in want.items():
    s = svc.get(name)
    if s is None:
        bad.append(f"{name}: missing"); continue
    got = (s.get("cgroup_parent"), s.get("cpu_shares"), s.get("mem_limit"), s.get("oom_score_adj"))
    if got != (slice_, shares, mem, oom):
        bad.append(f"{name}: got {got}, want {(slice_, shares, mem, oom)}")
live = svc.get("ohcamel-live", {})
if live.get("mem_reservation") != 256 * 2**20:
    bad.append(f"ohcamel-live: mem_reservation {live.get('mem_reservation')}, want 256 MiB")
ooms = {n: svc[n].get("oom_score_adj", 0) for n in svc if n in want}
if not ooms["ohcamel-live"] < ooms["ohcamel-quant"] < ooms["ohcamel-research"]:
    bad.append(f"OOM order wrong: {ooms}")
if bad:
    print("resource_budget_test: FAIL"); print("\n".join("  " + b for b in bad)); sys.exit(1)
print("resource_budget_test: ok (%d services)" % len(want))
PY
```

  `docker compose config` normalizes `mem_limit` to bytes. If the installed version prints a string, normalize in the script with a `parse_mem` helper and keep the assertion in bytes.

- [ ] **Step 2: Run it and watch it fail.** `bash deploy/test/resource_budget_test.sh`. Expected: `FAIL` listing each service's `(None, None, …)`.
- [ ] **Step 3: Implement.** In `deploy/docker-compose.yml`, add to each service:

```yaml
  ohcamel-live:            # … existing keys unchanged …
    cgroup_parent: ohcamel-rt.slice
    cpu_shares: 4096
    mem_limit: 512m
    mem_reservation: 256m
    oom_score_adj: -800
  caddy:
    cgroup_parent: ohcamel-web.slice
    cpu_shares: 1024
    mem_limit: 128m
    oom_score_adj: -500
  ohcamel-quant:
    cgroup_parent: ohcamel-web.slice
    cpu_shares: 1024
    mem_limit: 1024m      # was 1536m: heavy work moves to the worker (Lane B)
    oom_score_adj: 0
  ohcamel-research:
    cgroup_parent: ohcamel-batch.slice
    cpu_shares: 256
    mem_limit: 384m
    oom_score_adj: 500
  ohcamel-demo:           # the local harness only; same budget as live
    cgroup_parent: ohcamel-rt.slice
    cpu_shares: 4096
    mem_limit: 512m
    oom_score_adj: -800
```

  Put one comment above the first of these blocks pointing at I.3 of this plan. **Owner check before deploy (O-3):** these values assume Docker's `systemd` cgroup driver on cgroup v2, where `cgroup_parent` names a systemd slice. On the droplet, `docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}'` must print `systemd 2`. If it prints `cgroupfs`, change the three slice names to `/ohcamel-rt`, `/ohcamel-web` and `/ohcamel-batch` and set `HOSTD_GROUPS` (0.4) to match.

  **Lowering the quant `mem_limit` from 1536m to 1024m is safe only once heavy requests go through the worker.** Until B5 lands, keep 1536m and change the test's value. B5's final step lowers it to 1024m and restores the assertion.

- [ ] **Step 4: Run it and see it pass.** `bash deploy/test/resource_budget_test.sh` → `ok (4 services)`. Then `shellcheck -S warning deploy/test/resource_budget_test.sh`.
- [ ] **Step 5: Extend the trading grep.** In `.github/workflows/ci.yml` find the build-and-test step that greps `lib/` for `paper-api\.alpaca\.markets|/v2/orders` (from the finish plan). Add a sibling step:

```yaml
      - name: nothing under quant/ or native/ can reach an order route
        run: |
          if grep -rnE '/v2/orders|/api/desk/(orders|cancel|kill)' quant/src native 2>/dev/null; then
            echo "an order route is named under quant/ or native/"; exit 1
          fi
```

  The engine bridge's allow-list in `quant/src/ohcamel_quant/api/routers/engine.py` names only read paths. The docstring that *mentions* `/api/desk/orders` must be reworded to "no desk route" so the grep stays meaningful. Include that edit.

- [ ] **Step 6: Quant count under check-counts.** Add `TESTS = 653` (the real number) to `quant/README.md` on its own line under a `<!-- quant-tests -->` marker, following how `research/.../verified.py` is read. In `scripts/check-counts.sh`, add:

```bash
quant_tests=$(grep -E '^TESTS = [0-9]+$' quant/README.md | grep -oE '[0-9]+') || {
  echo "check-counts.sh: could not find 'TESTS = N' in quant/README.md"; exit 1; }
check_marker quant-tests "$quant_tests"
```

  Mark the top-level README's Quant count sentence with the same marker convention the script already uses for `ocaml-tests`. Run `make check-counts`.

- [ ] **Step 7: Commit.** `deploy: every service gets a slice, a CPU weight, a memory ceiling and an OOM rank -- the engine is protected and the batch tier dies first`.

### Task 0.4: `hostd`: Rust host telemetry, and `/api/ops/host`

**Files:**
- Create: `native/Cargo.toml`, `native/hostd/Cargo.toml`, `native/hostd/src/{main.rs,proc.rs,cgroup.rs,ring.rs}`, `native/hostd/Dockerfile`, `native/rust-toolchain.toml`
- Create: `quant/src/ohcamel_quant/api/routers/ops.py`, `quant/tests/test_ops_host.py`
- Modify: `quant/src/ohcamel_quant/config.py` (`hostd_url: str | None = None`, added to the blank-is-unset validator), `deploy/docker-compose.yml` (the `ohcamel-hostd` service), `.github/workflows/ci.yml` (a `native` job: `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test`, and the image publish per 0.2)

**Interfaces:**
- Produces: `GET /v1/host` (II.6); `GET /api/ops/host` (the same body plus `provenance` and `notes`, or 503 `{"error":"hostd_unavailable","detail","configured"}`); Python `ohcamel_quant.api.routers.ops.read_host() -> dict`, raising `HostdUnavailable`, which B3 uses for admission control.

- [ ] **Step 1: Workspace.** `native/Cargo.toml`:

```toml
[workspace]
resolver = "2"
members = ["hostd"]

[workspace.package]
edition = "2021"
rust-version = "1.80"
license = "MIT"
```

  `native/rust-toolchain.toml`: `[toolchain]\nchannel = "1.80"\ncomponents = ["rustfmt", "clippy"]`.

  `native/hostd/Cargo.toml`:

```toml
[package]
name = "ohcamel-hostd"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tiny_http = "0.12"
```

- [ ] **Step 2: Write the failing parser tests** in `native/hostd/src/proc.rs`:

```rust
//! Parsers for /proc. Pure functions over the file text, so they are tested
//! against captured text rather than the machine running the tests.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CpuTimes { pub busy: u64, pub idle: u64, pub steal: u64, pub iowait: u64, pub total: u64 }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Mem { pub total: u64, pub available: u64, pub swap_total: u64, pub swap_free: u64 }

pub fn parse_stat(text: &str) -> Option<CpuTimes> { todo!() }
pub fn fraction(a: CpuTimes, b: CpuTimes) -> Option<(f64, f64, f64)> { todo!() } // (busy, steal, iowait)
pub fn parse_meminfo(text: &str) -> Option<Mem> { todo!() }
pub fn parse_load1(text: &str) -> Option<f64> { todo!() }

#[cfg(test)]
mod tests {
    use super::*;
    // Captured from a 2-vCPU droplet. Fields: user nice system idle iowait irq softirq steal guest guest_nice
    const STAT_A: &str = "cpu  100 0 50 800 20 0 10 20 0 0\ncpu0 50 0 25 400 10 0 5 10 0 0\n";
    const STAT_B: &str = "cpu  160 0 70 900 20 0 10 40 0 0\n";

    #[test]
    fn stat_splits_busy_idle_steal() {
        let a = parse_stat(STAT_A).unwrap();
        // idle = idle + iowait = 800 + 20; total = user..steal = 100+0+50+800+20+0+10+20 = 1000
        assert_eq!(a, CpuTimes { busy: 180, idle: 820, steal: 20, iowait: 20, total: 1000 });
    }

    #[test]
    fn fraction_is_delta_over_delta() {
        let (a, b) = (parse_stat(STAT_A).unwrap(), parse_stat(STAT_B).unwrap());
        // total 1000 -> 1200 (Δ200); busy 180 -> 280 (Δ100); steal 20 -> 40 (Δ20); iowait Δ0
        assert_eq!(fraction(a, b), Some((0.5, 0.1, 0.0)));
        assert_eq!(fraction(a, a), None); // no time passed
    }

    #[test]
    fn meminfo_in_bytes() {
        let t = "MemTotal:        4008992 kB\nMemFree:  100 kB\nMemAvailable:    1494016 kB\nSwapTotal:       2097148 kB\nSwapFree:        1860604 kB\n";
        let m = parse_meminfo(t).unwrap();
        assert_eq!(m.total, 4008992 * 1024);
        assert_eq!(m.available, 1494016 * 1024);
        assert_eq!(m.swap_total - m.swap_free, (2097148 - 1860604) * 1024); // 231 MiB used
    }

    #[test]
    fn load1_is_the_first_field() {
        assert_eq!(parse_load1("0.17 0.21 0.23 1/234 5678\n"), Some(0.17));
        assert_eq!(parse_load1(""), None);
    }
}
```

- [ ] **Step 3: Run and see failure.** `cd native && cargo test -p ohcamel-hostd`. Expected: panics at `not yet implemented`.
- [ ] **Step 4: Implement.**

```rust
pub fn parse_stat(text: &str) -> Option<CpuTimes> {
    let line = text.lines().find(|l| l.starts_with("cpu "))?;
    let v: Vec<u64> = line.split_whitespace().skip(1).map(|x| x.parse().ok()).collect::<Option<_>>()?;
    if v.len() < 8 { return None; }
    // guest and guest_nice are already inside user/nice: count only the first eight.
    let total: u64 = v[..8].iter().sum();
    let idle = v[3] + v[4];
    Some(CpuTimes { busy: total - idle, idle, steal: v[7], iowait: v[4], total })
}

pub fn fraction(a: CpuTimes, b: CpuTimes) -> Option<(f64, f64, f64)> {
    let dt = b.total.checked_sub(a.total)?;
    if dt == 0 { return None; }
    let d = |x: u64, y: u64| y.saturating_sub(x) as f64 / dt as f64;
    Some((d(a.busy, b.busy), d(a.steal, b.steal), d(a.iowait, b.iowait)))
}

pub fn parse_meminfo(text: &str) -> Option<Mem> {
    let kb = |key: &str| -> Option<u64> {
        text.lines().find(|l| l.starts_with(key))?.split_whitespace().nth(1)?.parse::<u64>().ok().map(|k| k * 1024)
    };
    Some(Mem { total: kb("MemTotal:")?, available: kb("MemAvailable:")?,
               swap_total: kb("SwapTotal:").unwrap_or(0), swap_free: kb("SwapFree:").unwrap_or(0) })
}

pub fn parse_load1(text: &str) -> Option<f64> { text.split_whitespace().next()?.parse().ok() }
```

- [ ] **Step 5: cgroup reader, test first.** `native/hostd/src/cgroup.rs`: `pub fn usage_usec(cpu_stat: &str) -> Option<u64>` (the `usage_usec N` line) and `pub fn read_group(root: &Path, name: &str) -> Option<(u64 /*usage_usec*/, u64 /*memory.current*/)>`. Test `usage_usec` on `"usage_usec 123456\nuser_usec 1\nsystem_usec 2\n"` → `Some(123456)`, and on `""` → `None`. A group's cores between two samples is `Δusage_usec / (Δwall_µs)`. Test that with `(1_000_000 → 1_500_000 over 1_000_000 µs) == 0.5 cores`.
- [ ] **Step 6: Ring and server, test first.** `ring.rs`: `Ring<T>` with capacity 720, `push` and `to_vec()`, oldest first. Test that pushing 721 items leaves `[1..=720]`. Then `main.rs`:
  - Environment variables: `HOSTD_PROC` (default `/host/proc`), `HOSTD_CGROUP` (default `/host/sys/fs/cgroup`), `HOSTD_GROUPS` (comma list, default `ohcamel-rt.slice,ohcamel-web.slice,ohcamel-batch.slice`), `HOSTD_BIND` (default `0.0.0.0:9100`), `HOSTD_INTERVAL_S` (default 5).
  - A sampler thread reads `stat`, `meminfo` and `loadavg` plus each group every interval, computes fractions against the previous sample, and pushes to the ring behind a `Mutex`.
  - The `tiny_http` loop answers `GET /v1/host` with the II.6 JSON (serde structs), `GET /healthz` with `ok`, and everything else with 404.
  - A missing group directory is reported as `null`, never 0.
  - Add a unit test that serializes a `Snapshot` with one sample and asserts the top-level keys are exactly `version, interval_s, cpus, now_ms, latest, history`.
- [ ] **Step 7: Image.** `native/hostd/Dockerfile`:

```dockerfile
FROM rust:1.80-alpine AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
COPY native/ ./
RUN cargo build --release -p ohcamel-hostd --locked
FROM scratch
COPY --from=build /src/target/release/ohcamel-hostd /ohcamel-hostd
USER 65534:65534
EXPOSE 9100
ENTRYPOINT ["/ohcamel-hostd"]
```

  Commit `native/Cargo.lock`.

- [ ] **Step 8: Compose service.** Add to `deploy/docker-compose.yml`. Use `image:` per Stage 1's pull convention with the CI-published tag. Also add `hostd` to `resource_budget_test.sh`'s `want` as `("ohcamel-web.slice", 1024, 32*2**20, -500)`.

```yaml
  ohcamel-hostd:
    image: "ghcr.io/ajaiupadhyaya/ohcamel-hostd:${OHCAMEL_SHA:?}"
    restart: unless-stopped
    read_only: true
    cgroup_parent: ohcamel-web.slice
    cpu_shares: 1024
    mem_limit: 32m
    oom_score_adj: -500
    volumes:
      - /proc:/host/proc:ro
      - /sys/fs/cgroup:/host/sys/fs/cgroup:ro
    networks: [ohcamel]
```

  Also set `OHCAMEL_QUANT_HOSTD_URL: "${OHCAMEL_QUANT_HOSTD_URL-http://ohcamel-hostd:9100}"` on `ohcamel-quant`.

- [ ] **Step 9: `/api/ops/host`, test first.** `quant/tests/test_ops_host.py` uses respx, as `test_engine_bridge.py` does:
  - unset → 503 with `configured: false`;
  - a refused connection → 503 with `configured: true`;
  - a 200 body → 200 whose `latest.cpu` equals the mocked value, with `provenance[0].source == "ohcamel-hostd"`;
  - a POST → 405;
  - a 2-second cache: two GETs cause one upstream call.

  Implement `api/routers/ops.py` as the engine bridge's shape (fixed path `/v1/host`, `httpx.Timeout(2.0, connect=0.5)`, 2 s cache), plus `read_host()` for in-process callers. Run `.venv/bin/pytest -q tests/test_ops_host.py` and see it fail then pass.

- [ ] **Step 10: Smoke.** In `deploy/smoke.sh` section Q, add a `qjson "GET /api/ops/host"` check. It requires `latest.mem_available > 0` and prints `cpu`, `steal` and `swap_used`. Run `shellcheck -S warning deploy/smoke.sh`.
- [ ] **Step 11: Commit** (`native: hostd, the host's CPU, steal, memory and per-slice use in 3 MB of Rust -- so the owner can see what the box is doing and the worker can decide what it may start`).

### Task 0.5: Latency baselines and the G-RT gate (can run in parallel with 0.4)

**Files:**
- Modify: `lib/server.ml`. Add a stabilize-duration ring and percentiles to `/api/ops`.
- Modify: `test/` (a new case) and `lib/verified.ml` (count +1)
- Create: `tools/perf/load.py` (a batch-load generator used **only** for the gate), `tools/perf/gate_rt.py`, `docs/perf/baseline.md`

**Interfaces:**
- Produces: `/api/ops` field `"stabilize_ms": {"n": int, "p50": float, "p99": float, "max": float, "window": 4096}`, and `tools/perf/gate_rt.py --engine URL --baseline docs/perf/baseline.md`, which exits non-zero on a regression over 10 %.

- [ ] **Step 1: Failing OCaml test.** In the scheduler or server test file, add a test that calls a new pure function `Server.For_testing.percentiles [|5.;1.;3.;2.;4.|]` and expects `(p50 = 3., p99 = 5., max = 5.)` using the nearest-rank convention: rank `ceil(p·n)`, so p99 of 5 values is rank 5. Run `make test`. Expected: a compile error because the function does not exist yet.
- [ ] **Step 2: Implement.** Keep a float ring of the last 4096 stabilize durations. Measure with `Time_ns.now ()` before and after each `Incremental.stabilize` in the frame loop, which is where `stabilizes` is already counted. Expose `percentiles` (nearest-rank, sorts a copy) and add `stabilize_ms` to `/api/ops`. Bump `lib/verified.ml` `tests` by 1. Run `make test` and `make check-counts`.
- [ ] **Step 3: `tools/perf/load.py`.** A stdlib-only script that spawns `N` processes, each running NumPy matrix multiplies for `T` seconds. It exists solely to emulate the batch tier for the gate, and its docstring says so (constraint 3).
- [ ] **Step 4: `tools/perf/gate_rt.py`.**
  1. Reads `stabilize_ms` from the engine at rest for 5 minutes (one sample every 10 s).
  2. Starts `load.py` inside the batch slice, via `docker run --rm --cgroup-parent ohcamel-batch.slice --cpu-shares 128 --cpus 1.75 python:3.12-slim …`.
  3. Samples again for 5 minutes.
  4. Compares p99 and exits 1 if it regressed more than 10 %.
  5. Writes a markdown row to stdout.
- [ ] **Step 5: Baseline (owner step O-3's second half).** Run it on the droplet after 0.3 is deployed, during a session, and commit the output to `docs/perf/baseline.md` with date, sha and both p99s. This is **G-RT's baseline**.
- [ ] **Step 6: Commit** (`kernel: the engine reports its stabilize latency percentiles -- the number every batch job is gated against`).

### Task 0.6: Gate G0

- [ ] All suites green on `main`:

  ```bash
  make test && make research-test && (cd quant && .venv/bin/pytest -q && .venv/bin/ruff check) && (cd quant/web && npm run typecheck && npm test && npm run build) && (cd native && cargo test)
  ```

  Also `make check-counts` and `bash deploy/test/resource_budget_test.sh`.
- [ ] The owner has deployed (O-2, O-3). `smoke.sh` passes on both hosts including `/api/ops/host`. `docs/perf/baseline.md` holds the G-RT baseline.
- [ ] Update `docs/status.md`: the Deployed row, and a "Compute program" row pointing at this plan.

---

# Part IV — The lanes

Each lane's first task (**X0**) is: *read Part I, Part II and this lane's section; write the lane's bite-sized plan with `superpowers:writing-plans` to `docs/superpowers/plans/2026-MM-DD-lane-<x>.md`, with every task below expanded into test-first steps with real code; commit it; get a plan review (a fresh reviewer checks it against this section and Part II) before any code.* The tasks below are the required content of that plan, not suggestions. Each lane ends with a whole-lane review and its gate.

## Lane A — Rust kernels (`native/kernels`, `quant/src/ohcamel_quant/kernels`)

**Why:** Every heavy loop the survey found is Python-bound and capped to keep a request in budget:
- FHS: `FHS_MAX_CELLS=2.5M`
- rolling VaR: `_MAX_REFITS=150`
- sweeps: `MAX_GRID=64`, run sequentially
- CSCV: ≤16 partitions
- backtest inner loop: Python per decision date
- pairs strategy: a per-day OLS loop
- SVI: a 63-start grid × Nelder–Mead

**Tasks:**
- **A1: crate, wheel and parity harness.**
  - Build `native/kernels` as a PyO3 (abi3-py312) crate built with maturin.
  - Build `ohcamel_quant/kernels/__init__.py` as the dispatcher. It exposes `ENGINE: Literal["rust","python"]` and the II.4 functions, and `reference.py` holds each one in NumPy.
  - `quant/tests/test_kernels_parity.py` runs every kernel on fixed inputs through both paths:
    - deterministic kernels to 1e-10 relative;
    - random kernels: mean and std of 200k draws to 3 standard errors, and the quantile at α to 2 standard errors via the binomial rule;
    - bit-identical for the same seed and thread count.
  - CI job `native`: build the wheel on ubuntu, `pip install`, then run the parity tests **with** Rust and again with `OHCAMEL_QUANT_KERNELS=python`.
  - `quant/Dockerfile` installs the CI-built wheel. A missing wheel fails the image build; it never silently falls back in production.
- **A2: `var_es_from_pnl`, `fhs_paths` and `copula_t_paths`.**
  - rayon, with a chunk size of 16k paths.
  - The tail-count rule is identical to `risk.core.tail_count`, pinned by a test at n = 1000, α = 0.99 → 10.
  - Wire into `risk/garch.py:fhs_var_es` behind the dispatcher.
  - Raise `FHS_MAX_CELLS` only for jobs (B), not for synchronous requests.
- **A3: `stationary_bootstrap_means`.** Politis–Romano, with its expected block length pinned. Wire into `backtest/validation.py:bootstrap_sharpe`.
- **A4: `cscv_pbo`.**
  - Enumerate combinations in lexicographic order.
  - Parity with the current vectorised NumPy at S = 8, 12 and 16.
  - Allow S = 20 (184,756 combinations) for jobs.
- **A5: `garch_nll` and `garch_fit`** (GARCH, GJR, EGARCH; Student-t).
  - Parity: the NLL at the `arch` package's fitted parameters matches `arch`'s log-likelihood to 1e-8.
  - Fitted parameters are within 1e-4 of `fit_garch_fast` on SPY fixtures.
  - Wire into `risk/backtest.py:rolling_forecasts`, lifting `_MAX_REFITS` for jobs.
- **A6: `backtest_weights`.**
  - The engine's inner loop: lagged execution, costs, borrow, T-bill cash.
  - Parity with `backtest/engine.run_weights` on all 13 strategies' fixture runs to 1e-10.
  - `audit_causality` passes against the Rust path.
- **A7: `svi_fit` and `realized_vol_minute`.**
  - SVI: parity on the committed Cboe fixture expiries; butterfly-arbitrage check unchanged.
  - RV: exact on a hand-built minute series.

**Gate GA:**
- Parity suite green both ways.
- Three mutations per kernel fail tests.
- A benchmark table in `docs/perf/kernels.md` records Python time, Rust single-thread time and Rust two-thread time per kernel, on the droplet's CPU class, via CI's ubuntu runner and one owner run.
- No synchronous endpoint's result changes, verified by the full offline Quant suite.

## Lane B — Jobs, worker, schedules (`quant/src/ohcamel_quant/jobs`)

**Tasks:**
- **B1: queue and ledger.**
  - `jobs.sqlite` per II.2 (WAL), with `enqueue`, `claim_next` (an atomic `UPDATE … RETURNING` of the highest-priority queued job that fits the admission rule), `heartbeat`, `finish` and `fail` (retries: 2 for ingest, 0 for others) and `cancel`.
  - Tests: two claimers never get the same job (threads); a stale `running` job whose heartbeat is older than 5 minutes is re-queued on startup; dedupe on `(kind, params_hash)`.
- **B2: worker process and service.**
  - Entry point `python -m ohcamel_quant worker`, running the same image as `ohcamel-quant` with another `command`.
  - Compose `ohcamel-worker` settings: `cgroup_parent: ohcamel-batch.slice`, `cpu_shares: 128`, `cpus: 1.75`, `mem_limit: 1280m`, `oom_score_adj: 800`, `quant_data:/data` read-write. Add these to `resource_budget_test.sh`.
  - Each job runs in a **child process** (`multiprocessing` spawn) so memory is returned after every job. `peak_rss_bytes` comes from `resource.getrusage(RUSAGE_CHILDREN)` and `cpu_seconds` from `ru_utime + ru_stime`.
  - SIGTERM finishes or cancels the running job within 20 s.
- **B3: admission control.**
  - A job may start only if `hostd.mem_available − 512 MiB ≥ class_budget(mem_class)`.
  - A `heavy` job may not start inside 09:25–16:05 New York on a session day. The clock is `deck/clock.session_clock`.
  - Tests use a fake `read_host` and a fixed clock.
  - `threads` given to a job: 2 outside the session, 1 inside.
- **B4: scheduler.**
  - `quant/src/ohcamel_quant/jobs/schedules.yaml` lists name, cron (New York), kind, params, priority, `mem_class` and `heavy`.
  - A scheduler thread in the worker enqueues due entries. It is idempotent per `(name, scheduled_for)` via a `schedule_runs` table.
  - Tests: DST transitions (2026-11-01, 2027-03-14); a missed run after downtime is enqueued once, not N times; heavy entries at 10:00 are refused by B3 and then run at 16:05.
- **B5: API and synchronous-to-job migration.**
  - `api/routers/jobs.py` per II.2, with an allow-list of kinds, 20 queued jobs per client IP, and SSE `/api/jobs/events`.
  - Move `POST /risk/backtest`, `/backtest/sweep`, `/backtest/walkforward` and `/portfolio/compare` behind jobs when their parameters exceed today's synchronous caps. Under the caps they stay synchronous and unchanged; over them they return `202 {job}` instead of `422`.
  - Then lower `ohcamel-quant`'s `mem_limit` to 1024m (0.3's deferred step).
- **B6: retention and backups.** Prune artifacts per II.3. Append `/data/jobs.sqlite` to `deploy/backup.list`. `/api/ops` gains `jobs: {queued, running, done_24h, failed_24h, cpu_seconds_24h}`.

**Gate GB:**
- Worker runs a synthetic-free smoke job, `ingest.fred` for one series, end to end on a local compose.
- Killing the worker mid-job re-queues it.
- A 1280 MiB-class job is refused when `hostd` reports 1.2 GiB available.
- G-RT (0.5) passes with the worker running the Lane A benchmark jobs.

## Lane C — Warehouse and ingest (`quant/src/ohcamel_quant/warehouse`)

**Tasks:**
- **C1: schema and readers.**
  - DuckDB per II.5; migrations as numbered SQL files; `open_ro()` / `open_rw()`.
  - Readers return the same shapes `MarketData` does. `MarketData.returns` reads the warehouse first when a ticker is present and fresh, and falls back to today's providers otherwise, recording which in `provenance`.
- **C2: universes.**
  - `universe_members` from S&P 500 and Nasdaq-100 current constituents, plus today's 45 ETFs from `universes.json`, about 600 names.
  - Source: SEC `company_tickers` plus a committed constituent list with its date.
  - The `survivorship` column says "current constituents; delisted names absent". Owner decision O-4 may add a point-in-time source.
- **C3: bars ingest.**
  - Alpaca daily bars, 20 years, split-adjusted and fully adjusted (reusing `data/prices.py` fetchers). Incremental by `max(date)` per ticker.
  - Yahoo/Stooq fallback when Alpaca is missing, recorded in `source`.
  - `ingest_log` rows carry `data_asof`.
  - Test: a vendor failure leaves `data_asof` at the last good date and writes `status='failed'` (Review Focus 4).
- **C4: minute bars.** Alpaca IEX minute bars for 50 liquid names, 2 years back then incremental, feeding P2's HAR-RV. Storage estimate in the task: about 50 × 390 × 252 × 2 ≈ 9.8M rows, a few hundred MB compressed.
- **C5: FRED, factors and SEC facts.**
  - All 26 dashboard FRED series in full.
  - French FF5 + momentum.
  - `sec_facts` for the universe, with `filed` dates (the point-in-time rule, II.5).
  - The 13F notable filers each quarter.
- **C6: option snapshots.**
  - Each night after 16:15 New York, snapshot Cboe delayed chains for 30 underlyings (the 11 sector ETFs, SPY, QQQ, IWM, TLT, GLD and the 14 largest names by option volume, listed in `schedules.yaml`) into `option_snapshots`. This is the history P7 builds on; it starts empty and grows.
  - A freshness endpoint, `GET /api/warehouse/freshness`, returns `data_asof` per dataset.

**Gate GC:**
- After a full backfill on the droplet (owner-run, overnight), `freshness` shows every dataset current.
- The DuckDB file size is recorded in `docs/status.md`.
- Offline tests use a small DuckDB built from the committed fixtures, so CI needs no network.

## Lane D — the Terminal design system (`quant/web/src/{styles,design,charts,shell,components}`)

**Tasks:**
- **D1: tokens v2.**
  - Rewrite `styles/tokens.css`: dark-first neutrals with a near-black ground; one accent; semantic `--gain`, `--loss`, `--warn`, `--stale`, `--unknown`; `--crt-*` promoted to an instrument palette; Inter and IBM Plex Mono.
  - Remove Fraunces from `main.tsx` and `package.json`.
  - Spacing on a 4 px grid; `data-density` scales `--row-h` (20 / 24 / 28 px), `--sp-*` and `--fs-*`.
  - Radius 0–2 px; no shadows (hairlines only); remove the `body::before` wash.
  - A Vitest test parses `tokens.css` and asserts every semantic token is defined in both themes and that contrast ratios reach ≥4.5:1 for text tokens against both grounds (WCAG relative-luminance formula, hand-checked for one pair in a comment).
- **D2: primitives** in `design/`:
  - `Workspace` (a CSS-grid page of `Pane`s, with saved layouts per route in localStorage);
  - `Pane` (title bar 24 px, actions, `asOf`, stale badge, collapse, no subtitle);
  - `StatusBar`;
  - `CommandLine`, which replaces `shell/CommandPalette.tsx`: a function-code grammar parsed by a pure, tested `parseCommand(s) → Command`, recents and actions;
  - `Kbd` / `useHotkeys` (global map; the `?` overlay);
  - `DataTable` v2: 20 px rows, keyboard row navigation, sticky first column, `format` required for numeric columns.
- **D3: formatting discipline.** Replace every `.toFixed` outside `lib/format.ts` (32 today) with `lib/format`. Add an ESLint `no-restricted-syntax` rule forbidding `.toFixed` and inline `style=` in `src/pages/**` except CSS custom properties.
- **D4: charts.**
  - `charts/` wraps uPlot (about 50 KB) for line, area, bar, heatmap (canvas) and histogram, with a crosshair readout, inline labels, theme from tokens and a `sync` group.
  - Plotly stays only in `SurfaceChart`, imported only by the pages that render 3-D.
  - Test: `npm run build` asserts via `scripts/check-bundle.mjs` that no chunk other than the surface page's imports `plotly`, and that the main entry is under 250 KB gzip.
- **D5: live and freshness.**
  - `lib/live.ts`: one `EventSource` per page to `/api/stream?topics=…`. A backend SSE hub (B5's events endpoint generalised) multiplexes quotes, deck readings, job events and host metrics.
  - `Pane` shows `asOf` and turns `--stale` after the topic's declared max age (Review Focus 4).
  - Reconnect with backoff; a status-bar lamp per topic.

**Gate GD:**
- The Markets page, migrated as the reference, passes axe (`@axe-core/playwright` in `npm run screenshots`) with zero serious violations.
- 380 px has no horizontal scroll.
- Bundle rule green.
- Screenshots in both themes and both densities committed to `docs/media/quant/terminal/`.

## Lane M — Models and products P1–P7 (`quant/src/ohcamel_quant/{models,products}`)

Each product is a job kind (B), an artifact (II.3), an API read (`api/routers/artifacts.py`) and a Methodology entry (`quant/web/src/pages/methodology/models.ts`).

**Tasks:**
- **M1: point-in-time feature store and leakage guards.**
  - `models/features.py` builds features by date from the warehouse: momentum 12-1, 1-month reversal, 60-day vol, beta, size, value from `sec_facts` lagged by `filed`, quality and liquidity.
  - `assert_point_in_time(df)` checks each feature's source timestamp ≤ its row date.
  - `purged_kfold(dates, label_horizon, embargo)` per López de Prado (2018, ch. 7).
  - Tests (Review Focus 5): a deliberately leaked `t+1` feature raises; purging removes exactly the overlapping labels in a hand-built 20-day example; the embargo excludes the next `k` days.
- **M2: P1 Monte Carlo risk atlas.**
  - Kinds `risk.mc_atlas` (nightly: books × α × {1, 10} days × {FHS, t-copula}, 1M paths) and `risk.mc_intraday` (every 15 minutes in session, 250k paths, `heavy=0`, `mem_class=S`, `threads=1`).
  - Uses A2.
  - Artifact tables `summary` and `euler`.
  - Page: Risk → Atlas.
  - The deck reading gains an optional `mc_var` from the latest intraday artifact, labelled with its `asOf`.
- **M3: P2 volatility forecast league.**
  - Nightly refit of GARCH, GJR, EGARCH (A5), HAR-RV (C4 minute RV) and EWMA for the universe.
  - Out-of-sample 1-day-ahead forecasts scored with QLIKE and MSE against realized variance; Diebold–Mariano pairwise tests; Model Confidence Set (Hansen, Lunde & Nason 2011).
  - Page: Volatility → Forecasts.
- **M4: P3 covariance league.**
  - Weekly; estimators from `portfolio/covariance.py` plus a PCA statistical factor model.
  - Score: realized volatility of each estimator's minimum-variance portfolio over the next month, plus the Ledoit–Wolf (2008) test.
  - Page: Portfolio → Covariance league.
- **M5: P4 strategy farm.**
  - Nightly incremental: 13 strategies × universes × grids up to 256 combinations. Runs A6 in parallel over combinations, then CSCV PBO (A4, S = 16 or 20), SPA, DSR and cost sensitivity.
  - The leaderboard shows the **verdict first** (the research layer's rule): most rows will fail, and the page says so.
  - Page: Research → Farm.
- **M6: P5 EXP-Q01 cross-sectional model.**
  - **The owner's pre-registration is required (O-5).**
  - LightGBM (`num_threads` from the job context; `max_bin` and row subsampling chosen to fit `mem_class=L`).
  - Monthly retrain on purged walk-forward folds (M1) and a nightly score.
  - Metrics: IC and rank-IC with HAC errors, decile long-short net of costs (`backtest` cost model), DSR, PBO.
  - Model card in `research/experiments/EXP-Q01/`.
  - Page: Research → Models. Everything is advisory; nothing reaches the desk (constraint 8).
- **M7: P6 EXP-Q02 regime HMM.**
  - Gaussian HMM (own EM implementation in `models/hmm.py`, tested on a hand-built two-state series against known parameters), 2–4 states chosen by BIC, nightly.
  - Macro → Regimes shows smoothed and filtered probabilities.
  - O-5 applies.
- **M8: P7 option-surface history.**
  - Nightly SVI (A7) on C6 snapshots.
  - Stores ATM IV, 25-delta risk reversal and butterfly, term slope and model-free variance per underlying per day.
  - VRP against HAR-RV forecasts (M3).
  - Page: Volatility → History. For the first weeks it honestly shows "n days of history".

**Gate GM:**
- Each product has run on the droplet for 5 consecutive sessions with artifacts present and `data_asof` current.
- Each Methodology entry exists.
- G-RT passes during a nightly batch.
- `hostd` history shows the batch slice near its cap through the batch window and idle after. Screenshot committed.

## Lane D2 — page migration and new pages (depends on D1–D5, B5)

**Tasks:**
- **D6: Markets.** The reference migration: Workspace layout; StartHere removed; index strip; sector heatmap in uPlot; status bar.
- **D7:** Portfolio and Risk: split Risk into its own route; Atlas (M2).
- **D8:** Optimizer.
- **D9:** Research: Strategy Lab, Farm (M5), Models (M6).
- **D10:** Volatility, including Forecasts (M3) and History (M8).
- **D11:** Rates & Macro, Company, Ticker, Methodology.
- **D12: Compute page** (`/compute`):
  - live `hostd` charts: CPU per slice, steal, memory, swap;
  - the job queue, running jobs with progress (SSE), the last 24 h of jobs with `cpu_seconds` and `peak_rss`;
  - each artifact linked to its page;
  - the schedule table with next run times.

Each page task covers:
- copy discipline (no subtitles; InfoTips only on metric labels);
- uPlot charts;
- `lib/format` only;
- keyboard;
- 380 px;
- both themes;
- axe clean;
- a screenshot, updated in `docs/media/quant/terminal/`.

**Gate GD2:** every route migrated; `rg "oc-page-title|Fraunces|body::before|\.toFixed" quant/web/src` is empty (except `lib/format.ts`); the Plotly bundle rule is green.

## Lane F — the Flight Deck console tab (depends on D1–D3, 0.4)

**2026-09-28 standalone visual release:** `/deck` now has a page-local SVG console,
limit corridor, risk radar, truthful feed lamps, session scanner and details drawer.
This release uses the existing quote polling and daily EWMA models and does not depend
on Terminal tokens, `hostd`, SSE or model artifacts. The private engine bridge stays
at its existing disabled public default. The infrastructure-dependent tasks below
remain open; this visual release does not claim their completion.


**Tasks:**
- **F0:** plan.
- **F1: console layout.**
  - `/deck` renders without `Page`/`Panel` chrome: a full-bleed dark console, a bezel grid of instruments and a 28 px header strip with the source switch and session clock.
  - The limits editor and marks table move into a right drawer (`d` toggles it).
  - The logic modules and their Vitest tests are unchanged.
- **F2: instruments at console scale.**
  - Targeting computer, radar and lamp panel sized to the grid.
  - Seven-segment counters for day P&L, VaR, marks age and time to close.
  - The lamp panel gains a **compute bank**: one lamp per running job kind and a CPU and memory bar from `/api/ops/host` (0.4).
  - All motion stays data-driven; reduced-motion stops all of it.
- **F3: live.** Replace polling with the SSE topics (D5): deck readings every quote refresh and the engine snapshot when the bridge is on. The session tape reads the recorder (already built) and the intraday MC artifact (M2).
- **F4: kiosk mode.** `?kiosk=1` hides the app shell for a wall display: full screen, auto-hiding cursor, `f` toggles it.

**Gate GF:**
- At 1280, 1920 and 380 px, in both schemes, with reduced motion: screenshots committed.
- The deck's Vitest and pytest suites are green.
- A 30-minute kiosk run shows no memory growth in the browser (Performance panel heap snapshot, noted in the PR).

## Lane E — the engine (OCaml; depends on 0.5)

**Tasks:**
- **E0: plan.** Rebase the finish plan's Stages 2A–6 (Tasks 21–77) onto current `main`, re-numbered as E-tasks, and remove anything the Quant platform already delivers publicly. The engine keeps its own identity; nothing is dropped from the finish spec without an owner ruling.
- **E1: scenario grid (P8).**
  - A second OCaml 5 Domain runs full revaluation of the live book over 10,000 scenarios (historical days from the long panel plus hypothetical shocks) every minute in session.
  - Results go to `/api/scenarios` and a Flight Deck lamp.
  - The tick-path Domain never waits on it: communication is through an `Atomic` snapshot swap.
  - Test: the grid's P&L on a 3-position hand-built book equals hand arithmetic.
  - G-RT must pass with the grid running.
- **E2: C stubs, only if justified.** If `make bench` shows the covariance update above 30 % of a tick at 400 names, write a C stub for the rank-1 EWMA update (BLAS `dsyr`) with an alcotest parity test. Otherwise record "not needed" in `docs/perf/engine.md` and close.
- **E3:** Stages 2A–6 per the finish plan, in its order.

**Gate GE:** the finish plan's Acceptance list, as amended by E0, plus G-RT with the grid.

## Lane H — hardening and launch

- **H1:** Whole-program review (a fresh reviewer against Parts I–II). Fix wave.
- **H2:** Docs:
  - `README.md` rewritten around the three tiers and the products;
  - `docs/status.md`;
  - `quant/README.md` code map;
  - the Methodology entries for P1–P8;
  - a runbook `docs/runbooks/compute.md` (reading `hostd`, draining the worker, re-running a schedule, restoring `jobs.sqlite` and the warehouse).
- **H3:** Coverage and counts re-measured; `make check-counts`.
- **H4:** Security pass:
  - allow-lists on `/api/jobs`;
  - rate limits;
  - the SSE fan-out cap;
  - no path traversal in the artifact routes (a test with `../`);
  - Caddy CSP updated for the uPlot and SSE origins.
- **H5:** Owner deploy (O-6) and a 5-session soak with G-RT, GM and the backup drill.
- **H6:** Final screenshots, and the `docs/status.md` "Deployed" row.

---

# Part V — Running it as a dynamic workflow

**Dependency graph** (a task starts when all its inputs are merged to `main`):

```
0.1 → 0.2 → 0.3 ─┬→ 0.4 ─┐
                 └→ 0.5 ─┴→ 0.6(G0)
G0 → {A0, B0, C0, D0} in parallel
A1 → A2..A7 (A2..A7 parallel with each other; one agent each is fine — disjoint files)
B1 → B2 → B3 → B4 → B5 → B6          (B3 needs 0.4)
C1 → {C2 → C3 → C4, C5, C6}           (C3+ need B2 to run as jobs; write code first, wire after B2)
D1 → D2 → {D3, D4} → D5              (D5 needs B5's SSE)
{GA, GB, GC} → M0 → M1 → {M2 (needs A2), M3 (A5, C4), M4, M5 (A4, A6), M6 (M1, O-5), M7 (O-5), M8 (A7, C6)}
{GD, B5} → D6 → {D7..D12}            (D12 needs B5, 0.4)
{D1..D3, 0.4} → F0 → F1 → F2 → F3 (needs D5) → F4
0.5 → E0 → E1 → E3                   (E is independent of A–D and may run throughout)
all gates → H1 → … → H6
```

**Per-task loop** for the workflow's agents:
1. Read Parts I and II, and the lane plan's task.
2. Write the failing test and see it fail.
3. Implement and see it pass.
4. Run the lane's full suite plus `make check-counts`.
5. Commit.
6. A fresh reviewer agent checks the diff against the task and Global Constraints, and must return **Approve** or a list of blocking fixes.
7. Fix, then merge to the lane branch.

At each lane gate: a whole-lane review, then merge to `main` (`--no-ff`).

**Concurrency:** at most four implementation agents at once: one each in A, B, C and D (or E). Lanes own disjoint paths (II.7). Shared files are append-only and rebased before merge. The model for every agent is the owner's choice (Fable 5.1 per the brief). Reviewers should be a fresh context, not the implementer.

**What agents must not do:**
- deploy;
- change droplet settings;
- read or write secrets;
- approve a pre-registration;
- push to `main` without a passing gate;
- raise a synchronous endpoint's caps.

Those are owner steps.

**Owner steps and decisions:**

| # | When | What |
|---|---|---|
| O-1 | any time | **Droplet size.** The plan fits `s-2vcpu-4gb`. If `hostd` shows steal above 10 % for sustained periods, or GM cannot finish the nightly batch by 09:00, resize. Options: more RAM, or dedicated vCPUs to remove the shared-CPU contention. Cost roughly doubles or more; check DigitalOcean's current pricing. A resize is a rebuild or downtime, and the owner's call. |
| O-2 | after 0.1, 0.4, each gate | Deploy (`deploy/deploy.sh`, `--public-only` during market hours for Quant-only changes). |
| O-3 | before 0.3 deploy | Run `docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}'` on the droplet and report it. After 0.3 is deployed, run `tools/perf/gate_rt.py` to write the baseline. |
| O-4 | before C2 | Accept the survivorship caveat for current-constituent universes, or fund a point-in-time constituents source. |
| O-5 | before M6, M7 | Write or approve the hypotheses and kill criteria in `research/experiments/EXP-Q01|Q02/preregistration.md`. Agents draft; the owner signs. |
| O-6 | H5 | Final deploy and soak sign-off. |
| O-7 | any time | Whether the engine bridge publishes the paper book's positions on the public site (`OHCAMEL_QUANT_ENGINE_URL`). It feeds the Flight Deck's engine source and E1's lamp. Today it is off, deliberately. |

---

# Self-review

1. **Brief coverage.**
   - *Use the CPU* → I.1, I.4, B, M, E1, D12.
   - *Star Wars graphics on their own tab* → Lane F, whose own full-bleed console builds on 0.1.
   - *UI overhaul* → I.5, Lanes D and D2.
   - *More comprehensive, more useful* → P1–P9.
   - *Real-world data* → Lane C, constraints 1 and 10.
   - *Training* → M6, M7, M3, M4.
   - *OCaml, Rust, C/C++/C#* → I.2, which states where each is and is not used and why.
   - *A plan for Fable 5.1 and a dynamic workflow* → Part V.
2. **Placeholders.** Phase 0 is fully specified with code. Lanes are specified at contract and acceptance level, and each lane's X0 task writes its bite-sized plan before any code. That is the scope-check decomposition the planning method calls for with a multi-subsystem brief; it is not deferral.
3. **Consistency.**
   - Slice names match across 0.3, 0.4, II.6 and B2.
   - `mem_class` budgets (S 256, M 640, L 1152 MiB) all fit the worker's 1280m ceiling.
   - `hostd`'s 720-sample ring matches II.6's "1 h at 5 s".
   - The quant `mem_limit` change (1536 → 1024) is deferred to B5 in both 0.3 and B5.
4. **Review Focus.** Each of the five has a named pinning test: 0.3, B3, B4, A1, C3, D5, M1.
