# The finish: one plan to completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish OhCamel. Build spec §3.13 (risk depth), §3.14 (self-validation) and §3.15 (operations); close every gap the surveys found in the already-merged phases; make every claim in the repository true; and leave `main` one owner command from deployed, with a named list of owner steps for everything the agent cannot do. One plan replaces the A4 plan and the separate A5 and A6 phases.

**Architecture:** Eight stages, each one short branch cut from `main`, each ending merged, green and publishable. The order is ship-first: consolidate what is finished, then build the deploy pipeline, then the long window and the two new estimators (so their forecast rows start accruing at the earliest possible deploy), then the factor model and liquidity, then the correctness gaps, then options and validation in two lanes, then research, then the finish. Risk depth lives in the kernel (`lib/`), with fits as cells so no fork refits; validation lives in the desk over the journal's own rows; operations live in CI, compose and `deploy/`, because the local Docker daemon is unhealthy and the agent cannot ssh. `lib/server.ml` learns nothing about the desk: frames join through `?frame_extra` and `/api/ops` through a new `?ops_extra`.

**Tech Stack:** OCaml 5.2.1, Core/Async, Incremental, Owl (linear algebra and the normal distribution), alcotest, Cohttp_async, Yojson, SQLite. Python ≥ 3.12 with uv, pandas, pyarrow, jsonschema, pytest, ruff; `fdq` pinned at `git+https://github.com/ajaiupadhyaya/capitallimits.git@652474c`. GitHub Actions, Docker Buildx, GHCR, Caddy, systemd, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-12-the-desk-design.md` in full, and its §5 acceptance rows for A4, A5 and A6 in particular:
- A4 — *accepted when identities hold to 1e-9 and values are hand-derived*;
- A5 — *accepted when the page reads "session n of 250"*;
- A6 — *accepted when a deploy pulls and a backup restores*.

The design doc for this finish is `docs/superpowers/specs/2026-09-19-the-finish.md` (Task 1 writes it).

---

## Global Constraints

- **The 60-observation window is not touched.** `return_window` stays 60. `returns[S]`, `factor_returns`, `covariance`, `covariance_ewma`, `historical_var`, `expected_shortfall`, `parametric_var`, `parametric_var_ewma`, `attribution` and `var_notional` compute exactly what they compute today. The tables printed by `make backtest`, `make backtest-crisis`, `make garch`, `make stress` and `make options` stay byte-identical to the Stage 0 baseline; the one permitted exception is the sentence `make garch` prints about wiring. `test_validation_report.ml` passes unchanged.
- **`Var_backtest.Estimator.t` is not extended.** It is a closed three-variant type whose `estimate` feeds `rolling`, which produces the backtest tables above. Adding a variant would ripple into `Estimator.estimate`, `lib/reports.ml`, `lib/validation_report.ml`, `bin/main.ml`, `test/test_properties.ml` and `test/test_validation_report.ml`, and could move a published table. The live scorer keys on the estimator's **name** as the journal records it and calls the statistic functions directly. It never calls `Var_backtest.run`.
- **Prices never reach a fit.** No node that fits, regresses, estimates a covariance or estimates GARCH parameters may be downstream of `price[S]`, `qty[S]` or `now`. A test pins this for every new fit-reading node, as `test_prices_never_reach_covariance` does today.
- **The kernel may not name the desk, and writes no file.** `grep -rE 'paper-api\.alpaca\.markets|/v2/orders|Sqlite3|ohcamel_desk' lib/` prints nothing, and after Task 34 `grep -rn 'Writer.with_file' lib/` prints nothing. The new feeds talk only to `data.alpaca.markets` and FRED. `lib/` cannot use the desk's `Desk_time`.
- **`lib/` has no interface files, and this plan adds none.** Verified: `ls lib/*.mli lib/feed/*.mli` matches nothing. Every kernel task edits the `.ml` alone. `desk/` has five today (`contract`, `halt`, `intake`, `journal`, `wire`); this plan adds `desk/oms.mli` deliberately (Task 47) and an interface for each **new** desk module it creates (`backup_retention`, `validation`, `shadow`, `replay`, `demo_signals`). No task modifies an `.mli` that does not exist: in particular there is no `desk/tca.mli` and none is created.
- **Journal changes are additive.** `CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`; `schema_version` stays 1. A task that believes it needs a bump stops and reports, because the journal refuses a version it does not know and a rollback across a bump would need a restore. The `alerts` table's `limit_name` and `line` are `NOT NULL` (verified at `desk/journal.ml:183`), so halt and reset rows carry `limit_name = "kill_switch"` and the reason (or `"hand reset"`) in `line` — a convention, not a schema change.
- **Unknown is not zero.** A figure the engine cannot compute is `None` and renders as a reason. A limit on such a figure is reported unevaluable.
- **Every node is labelled, costed, typed, on the wire and pinned.** Each new name gets a `Node_name` entry, a unit in `unit_of` from the existing vocabulary, and a cost class in `cost_of`. Each new **scalar** node gets its `by_node` entry in `lib/server.ml` **and its key in `test/test_server.ml`'s `test_round_trips` required-key list, in the same task** — that list is an inclusion check, so a task that adds a key without pinning it stays green while leaving its own key unpinned. `Graph.fork` copies every new cell and construction setting; `observed_roots` and `destroy` stay in step.
- **Topology and scaling pins move deliberately, in the task that moves them,** and the commit message names each: `test_graph.ml`'s pinned recompute sets, labels (`expected_labels`, "sixty-one"), `expected_observed` ("observed 28"), the fork's "+28" and the count formulas; `test_scaling_probe.ml`'s node count and per-tick band; `test_embedded_assets.ml`'s node count. Cells no node reads never appear in the topology, because `walk` starts from observers.
- **The demo keeps its draws.** `Synthetic_book.seed_returns`'s RNG draw order is load-bearing. Everything seeded for this plan uses its own `Random.State` with its own seed, and a test asserts the 60-window returns are identical with and without it.
- **Every fetch is bounded.** Each new request runs under `Clock_ns.with_timeout` (30 s) with Cohttp's `~interrupt`, so a timed-out request is cancelled and not abandoned. A timeout is an `Error`. No startup path awaits a new fetch.
- **Text only, never HTML,** for server-supplied strings on a page: `F.el` and `textContent` only, never `innerHTML` with data. Every element lookup returns early when absent. Units are converted in one named function per page section, and the page computes nothing else.
- **Hermetic tests, with hand-derived values.** Payloads are inline strings written from Alpaca's, FRED's and SQLite's documentation. No test touches the network, reads a credential, or starts the scheduler outside `test/desk_async/`. The derivation of every numeric assertion goes in a comment above it, as in `test_vol_estimators.ml`. Identities are checked at 1e-9.
- **Alpaca facts come from documentation only.** Where a field name, an activity type, a Greek's unit or a calendar field is not already in this repository, confirm it with `search_alpaca_docs`, `get_alpaca_endpoint_docs`, `list_alpaca_api_endpoints` or a fetch of `docs.alpaca.markets`, and cite it in the module header. Never call an account, market-data or trading tool.
- **Counts.** The base is **`main` at `b6132c6`, where `lib/verified.ml` reads `tests = 600` and `scheduler_tests = 30`** (verified). `final/s0-baseline` is cut from there, and Task 2 cherry-picks all eight kernel commits. The reconciliation is `600 + 16 + 16 + 10 + 11 = 653`: `desk/a4-depth`'s long-panel work takes 600 → 616, `a4/t3`'s factor model 600 → 616, `a4/t5`'s liquidity 600 → 610, and `a4/t7a`'s Cornish–Fisher 600 → 611, each measured against the same `main`. Every task that changes a count updates `lib/verified.ml` and the three sentences in the same commit:
  - `README.md` ("`make test` runs N tests…")
  - `docs/overview.md` ("**N tests**…")
  - `docs/status.md` ("**N hermetic tests**…")

  From Task 5 those three numbers are wrapped in `<!-- count:ocaml-tests -->N<!-- /count -->` markers, checked by `make check-counts`, **and that target runs in CI's `lint` job on every push**; the Python count lives in `research/src/ohcamel_research/verified.py` and is asserted by a pytest collection hook. Coverage is re-measured once per stage, by the controller, after the whole-branch review.
- **Where `make` may run.** A task working in a git **worktree** builds and tests with `dune build`, `dune runtest` and `dune build @fmt` on the repository's main opam switch — **never `make`**, because the Makefile assumes the primary checkout (coverage instrumentation, `_coverage/`, the six-mode gate's paths, `uv` in `research/`) and a `make` run from a worktree can clobber the primary checkout's artefacts. `make` runs in exactly two places, both the controller's:
  1. **The primary checkout**, for ordinary local `make build` / `make test` on the branch it already holds.
  2. **A dedicated local clone** under `$TMPDIR`, created with `git clone --local /Users/ajaiupadhyaya/Documents/OhCamel <dir>/repo`, checked out at a named commit, on the main opam switch. A clone has its own `_coverage/` and its own outputs and shares only the object store, so `make` there is safe. This is where the Stage 0 baseline artefacts are produced and where every stage's `make coverage` runs. The primary checkout is never switched to a stage branch for either, so the owner's untracked files there (`notes.txt`, `claudecodehandoff.md`, `.claude/`, `.playwright-mcp/`, `Competitive Market Behavior LLMs.pdf`) are never at risk.
- **The Stage 0 baseline artefacts.** Task 2 creates `$TMPDIR/final-baseline/repo` as a local clone at `b6132c6` and saves, with `make` run there, `backtest.txt`, `backtest-crisis.txt`, `garch.txt`, `stress.txt`, `options.txt` and `run-scaling.txt` under `$TMPDIR/final-baseline/`. Every later task that must show a table unmoved diffs against those files and attaches the diff. The clone and the files survive the whole plan; if they are lost, they are regenerated the same way from the same sha.
- **Credentials.** Never read or print a credential. No `.env`, `deploy/.env`, `/etc/ohcamel`, `~/.claude.json`, `~/ohcamel-live.env`, `~/.ohcamel-live-password`, `~/.porkbun.env`. No task in this plan needs a key.
- **Commit hygiene.** Stage files by name; never `git add -A` or `git add .`. Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp`, `.playwright-mcp/`, `.claude/`, `_coverage/`, any `.env`, or anything under `research/experiments/*/results/` except the per-strategy `manifest.*.json` files and `report.md`. Every commit message ends with a blank line and exactly `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Commit prefixes are `kernel:`, `graph:`, `desk:`, `web:`, `research:`, `deploy:`, `ci:`, `test:`, `docs:`, `book:`, `plan:`, `verified:`.
- **Scratch files use a task-unique prefix:** `$TMPDIR/final-taskN-*`. Nothing scratch is committed.
- **The environment's hard limits.** The agent cannot ssh to the droplet, cannot build a Docker image locally (the daemon is unhealthy), cannot read a credential, and may not edit the owner's `book.sexp`. Every image, compose and deploy change is therefore verified in CI — by the harness smoke, the `lint` job, and dry-run and stub tests — and every deploy, book edit, root host change and drill is an owner step with exact commands. Verifying a stage branch in CI means pushing it to the public `origin`; the executing session confirms the owner's standing authorization to push and merge **once**, before the first push, and records the confirmation in the ledger.
- **The ledger** is `.superpowers/sdd/2026-09-19-final/progress.md`. Every task records its findings, its fix rounds and its disposition there.

---

## Rulings that bind this plan

A4's rulings 1–12 (as carried into `docs/superpowers/specs/2026-09-19-the-finish.md` §3.5, with amendment 5 — the conditioning floor is **0.01**, not 1e-8 — and amendment 7 — monotonicity in **closed form**, not on a grid) bind Stages 2A, 2B and Stage 4's options lane in full. The following rulings bind everything.

1. **Stages ship, and risk depth is two of them.** A stage starts only when the previous one is merged to `main`, pushed, and green in CI. Every stage ends merged, with both images published for the merge sha, so `main` is always one owner command from deployed and nothing waits unmerged across a boundary. Risk depth splits: **Stage 2A** is the long window, the refresh, GARCH, Cornish–Fisher and the forecast rows A5 reads; **Stage 2B** is the factor model, the factor-exposure limit, liquidity, the Risk page's first two sections, the re-measured figures and the A4 documents. The split exists for one reason: the live host accrues `garch` and `cornish_fisher` forecast rows only after the stage that writes them deploys, and Definition of Done #17 counts 60 scored sessions from that deploy. Shipping 2A alone brings the first verdict forward by the length of 2B.
2. **Additive journal only.** Two new tables (`session_meta`, `cash_flows`) and new indexes are created with `IF NOT EXISTS`; `schema_version` stays 1. `docs/status.md` states what a bump would have cost. The `alerts` table keeps its `NOT NULL` columns: a halt row is `kind='halted'`, `limit_name='kill_switch'`, `line=<reason>`; a reset row is `kind='reset'`, `limit_name='kill_switch'`, `line='hand reset'`. Task 36's restore query is `kind IN ('halted','reset') AND limit_name='kill_switch'`, newest first.
3. **`lib/` writes no file.** `lib/alerts.ml`'s `File` sink is retired. `Config` refuses `(File "…")` with a sentence naming the journal's `alerts` table as its replacement, and `check-book` and `deploy.sh` surface that refusal before the engine restarts. `lib/alerts.ml` gains a generic observer callback that names no desk; the desk subscribes and journals.
4. **The live record's loss, derived.** Equity moves by P&L plus net external cash flow: E_t = E_{t−1} + PnL_t + F_t. Therefore **loss_t = E_{t−1} − E_t + F_t**, a deposit positive. An exceedance is loss_t > VaR_notional_{t−1} for that estimator. The statistics are fed fractions of gross: `var = var_notional(t−1) / G(t−1)` and `realised = −loss_t / G(t−1)`, which preserves the exceedance indicator exactly because G > 0.
5. **Pairing and exclusion.** A pair is scored only when the session recorded as *t−1*'s next session is *t*. Rows written before `session_meta` existed pair only when *t* is the next trading day on the venue calendar. Counted-and-shown, never silently dropped: a hole; a forecast made on windows that did not roll (scored and flagged, because dropping it after its outcome is known is a post-hoc filter); a flat book (G = 0 or VaR `None`); a missing forecast; unknown cash flows. The window is the newest 250 scored pairs; the page reads "Session n of 250"; no statistic prints below 60.
6. **The zone is the binomial traffic light at the book's confidence** (0.95), named that way on the page and recorded in §8 — not Basel's 99% zone.
7. **A cash flow is an external cash activity.** Alpaca's non-trade cash activity types, confirmed from its documentation (CSD, CSW and JNLC are expected), read through the venue interface, journaled idempotently by activity id, with `flows_synced_through` advanced only on success. Dividends and fees are P&L, not flows. `Sim_venue` returns none. A paper-account reset reads as a flow and is a stated limit.
8. **The demo's horizon is scaled where the forecast is written.** A demo session spans 20 synthetic daily bars, so `Session_close` takes `?horizon_bars` (default 1) and the demo passes 20, scaling by √20 = 4.472136. The page says so. The task first verifies that `Synthetic_book`'s per-bar draws are i.i.d. with zero mean, and stops and reports if they are not.
9. **The demo may show a synthetic arc and may never be mistaken for a live one.** The demo's strategy carries a `synthetic:` validation manifest. R6 accepts a `synthetic:` manifest only when the venue is simulated; the live intake refuses it and names R6. Both halves are pinned by tests. Every synthetic date, session, figure and strategy cell is labelled on the page.
10. **`replay`'s session membership.** An order belongs to the session of its first fill. An unfilled order belongs to the New York trading date of its submission, and a submission after that date's close belongs to the next session — which is how a market-on-open order placed the evening before appears in the session it opens.
11. **Backups.** SQLite's online backup API, run by `ohcamel journal-backup` inside a one-shot container on a host systemd timer at 06:30 UTC, writing `/var/backups/ohcamel/desk-YYYY-MM-DD.db` on the host. The copy is reopened and `integrity_check`ed before rotation. Retention keeps the 14 newest dailies, the 8 most recent Sundays, and the 5 newest `pre-deploy-*`. The data volume is mounted read-write, because a WAL reader may need to create `-shm`, but the process only reads. A backup is a copy of the journal, so invariant 13 holds; the engine neither writes nor reads backups, and staleness is `watch.sh`'s business. The off-box copy is the owner's free pull; paid backups are a Q10 spending decision.
12. **Images and deploys.** CI builds both images on every push and runs the compose harness smoke against them; only a green `main` publishes `ghcr.io/ajaiupadhyaya/ohcamel{,-research}:<sha>` and `:main`. Compose references `${OHCAMEL_TAG}` with no `build:` block; `deploy.sh` deploys a sha by pulling. Its `--build` fallback builds **both** `deploy/Dockerfile` and `deploy/research.Dockerfile` and tags both `ghcr.io/ajaiupadhyaya/ohcamel:SHA` and `ghcr.io/ajaiupadhyaya/ohcamel-research:SHA`, because until owner step O10 settles GHCR visibility the fallback is the only deploy path and a one-image fallback would leave the research container on a missing or stale image. Rollback is `deploy.sh --live --sha <previous>`. A live deploy is refused on a weekday in [13:25, 20:10) UTC unless `--during-market` is passed, because a restart drops the one allowed stream and forces reconciliation.
13. **`/api/health` stays liveness-only.** Readiness is read in-container from `/api/desk` and `/api/ops`. Live-host smoke runs inside the container, needs no password, and **never** POSTs `/api/desk/kill` — a regression there would halt the live desk. The hermetic desk-route tests cover that route, `smoke.sh` says so in a comment, and because §3.15's acceptance asks for all four mutating routes to refuse a headerless request on the live host, the gap is recorded as a §8 departure in Task 71.
14. **`/api/ops` gains a generic `?ops_extra` seam,** which refuses a key that collides with a core key. Through it the desk states, in words: the venue; trading on or off with its reason, including a refused trading key; the journal's kind, path, size and schema version; `last_sync` and `last_error`; the intake's state; and the market clock (ruling 20). The kernel's own `long_window` block goes straight into `json_of_ops`, because the long window is a kernel feed.
15. **TCA's primary unit is the order:** the quantity-weighted fill price, costs recomputed per order with `tca.ml`'s existing formulas, and count, mean, median and quantity-weighted mean for every metric, overall and by symbol. Per-fill rows stay on the wire as detail.
16. **R8 is decided by evidence, and enforced only after it has been observed.** Task 64's decidable question is whether the research service's bar source and the engine's long panel agree on **feed, adjustment and bar finality/timing** — that is what a code read with no credential can establish, and the ruling is worded to it. Only if they agree is the recipe built: `micro-v1` = SHA-256 over lines `SYMBOL,YYYY-MM-DD,<close in integer micro-dollars>\n`, sorted by symbol then date, each close round-half-even from `close × 1e6`. Documents that do not declare the recipe keep their current meaning, so EXP-A01's manifests never change. Enforcement is staged by a book switch `(r8 observe)` (the default) or `(r8 enforce)`: in `observe`, a mismatch **defers** the signal with both hashes recorded and re-judges every minute, and never refuses; in `enforce`, a mismatch refuses under R8 naming both hashes. A hash the core cannot yet compute always defers, in either mode. If the sources do not agree, R8 becomes a permanent §8 departure stating the exact difference and Tasks 65, 66 and the switch do not exist.
17. **Buy-and-hold is report-only.** Post-hoc, labelled not-a-gate, in its own file. EXP-A01's manifests and `report.md` verdicts are byte-identical. The owner may veto it.
18. **The nav follows §4's order:** Desk, Risk, Research, Execution, Argument, Ops.
19. **Ruled out, with reasons.** A second market-data vendor, a live trading path, an options trading path, a second droplet or staging host, a droplet resize (a disk cannot shrink, so it is a rebuild), any promotion to `(sizing live)`, and a new pre-registered experiment: the first six are out of scope for this finish, and the last two are the owner's decisions. Paid DigitalOcean backups or Spaces: Q10 forbids a spending change, so the free pull is the default and the paid option is an owner decision. `fail2ban` or an IP allowlist on the live host: the owner decides and `docs/status.md` records the choice. `claudecodehandoff.md`: the owner's untracked file, not a completion item.
20. **The clock is on the wire before anything needs it.** `Venue.Session_clock` already carries `now`, `is_open`, `next_open`, `next_close` and `next_close_date` (`desk/venue.ml:60`), `Read.clock` is in the venue interface (`:177`), and `oms.ml` and `session_close.ml` already call it. Nothing publishes it. Task 13a makes the desk cache its newest reading — refreshed on the existing `Config.Runtime.clock_interval` timer that `bin/main.ml:1485` already runs — and puts it on `/api/desk` and in the `/api/ops` desk block with the read stamp and a `source` field. Task 18's `watch.sh` reads it over plain HTTP in-container, with no credential. No host script may be written against a field the wire does not carry.
21. **The scorer reuses the statistics and never the harness.** `desk/validation.ml` does not call `Var_backtest.run`, does not construct a `Var_backtest.Estimator.t`, and does not extend it. It keys on the estimator's name as the journal records it (`historical`, `parametric`, `ewma`, `garch`, `cornish_fisher`) and calls `kupiec_pof`, `christoffersen_independence`, `conditional_coverage`, `duration_independence` and `traffic_light` directly, plus `Validation_report.worst_burst`. The joint conditional-coverage statistic exists today only inside `run` (`lib/var_backtest.ml:514–522`); Task 57 lifts exactly that arithmetic into `val conditional_coverage : exceedances:bool array -> confidence:float -> float * float` and rewrites `run` to call it, so there is one implementation and `run`'s report fields are unchanged to the bit.
22. **A count is a CI fact.** `make check-counts` runs in the `lint` job, which Task 4 creates in Stage 0 and Task 8 extends. Definition-of-done #3 is met by that job and not by the target's existence.
23. **A worktree never runs `make`; a clone does.** As the Global Constraints state. The Stage 0 baseline artefacts and every `make coverage` run come from a dedicated local clone under `$TMPDIR`, at the named commit or stage-branch tip, and the primary checkout is never switched for either.
24. **No new `.mli` under `lib/`.** Verified: none exists. `desk/oms.mli` is deliberate and is the only new interface for an existing module. New desk modules ship their own.
25. **Every snapshot key is pinned in the task that adds it,** in `test/test_server.ml`'s `test_round_trips` required-key list, because that list is an inclusion check and would otherwise stay green with the key unpinned.
26. **A stage with shared files names its reconciliation.** Stage 3 and Stage 4 each name the files two or more of their tasks touch, and each close task reconciles them. A lane claim that two lanes "share no files" is not made unless a grep of the task file lists supports it.

---

## File Structure

```
docs/superpowers/specs/2026-09-19-the-finish.md      NEW   the design doc for this finish
docs/superpowers/plans/2026-09-19-final-completion.md NEW  this plan
docs/superpowers/plans/README.md                     NEW   the plans index; each of the 15 older plans gains a header line
.superpowers/sdd/2026-09-19-final/progress.md        NEW   the ledger (gitignored)
.superpowers/sdd/conventions.md                      MODIFY commit trailer, worktree/clone rule, scratch prefix
scripts/check-counts.sh, scripts/uptime.sh           NEW

lib/types.ml                     MODIFY Limit.kind gains Factor_exposure
lib/long_panel.ml                PRESENT (dae577e) the aligned panel, pure
lib/factor_model.ml              PRESENT (a4/t3)   OLS by QR, factor covariance, Euler split, pure
lib/liquidity.ml                 PRESENT (a4/t5)   days to liquidate, impact, Bangia LVaR, pure
lib/risk_metrics.ml              MODIFY  Cornish-Fisher (a4/t7a); one shared min-observations constant
lib/var_backtest.ml              MODIFY  conditional_coverage lifted out of run (Task 57)
lib/feed/long_window.ml          NEW     SIP bars + DGS10 -> Long_panel.t; the refresh loop
lib/feed/alpaca_options.ml       NEW     indicative snapshot parser, store, poll loop
lib/feed/feed_source.ml          MODIFY  /api/ops long_window block
lib/graph.ml                     MODIFY  long-window cells; factor, liquidity, GARCH, CF, options nodes
                                         (no lib/graph.mli exists and none is created -- ruling 24)
lib/limits.ml                    MODIFY  Factor_exposure in every exhaustive match
lib/config.ml                    MODIFY  Factor_exposure, Greek_limit, options, liquidity, r8 switch;
                                         Config.check; File sink refused
lib/alerts.ml                    MODIFY  File sink retired; generic observer callback
lib/server.ml                    MODIFY  ?ops_extra seam; snapshot fields and by_node entries
lib/synthetic_book.ml            MODIFY  seed_long, own RNG
lib/scaling_probe.ml             MODIFY  seeds a long panel; Time_ns
lib/validation_report.ml         MODIFY  honest ~burst_span:int option
lib/options_walk.ml              MODIFY  named lets
lib/reports.ml, lib/vol_estimators.ml, lib/garch_study.ml  MODIFY the wiring sentences
lib/raw_closes.ml                NEW     the raw-close store for R8 (only if Task 64 allows)
lib/verified.ml                  MODIFY  counts and coverage, dated

desk/journal.ml, desk/journal.mli   MODIFY session_meta, cash_flows, bounded and by-date queries,
                                           open_read_only, backup, verify, prune_memory, halt-state query
desk/backup_retention.ml, .mli      NEW    pure retention
desk/alert_log.ml                   NEW    the Alerts/Halt observer that journals
desk/halt.ml, desk/halt.mli         MODIFY a halt survives a restart on live
desk/validation.ml, .mli            NEW    the pure live-VaR scorer
desk/shadow.ml, .mli                NEW    the shadow backtest-against-live record
desk/replay.ml, .mli                NEW    the pure replay formatter
desk/demo_signals.ml, .mli          NEW    the demo's labelled synthetic strategy
desk/venue.ml, desk/alpaca_paper.ml, desk/sim_venue.ml  MODIFY calendar, cash_flows, Opg fills
desk/desk.ml, desk/desk_routes.ml   MODIFY ops block, clock block, recent_alerts,
                                           /api/desk/validation, against_live
desk/session_close.ml               MODIFY record_long_forecasts, session_meta, flows, ?horizon_bars
desk/tca.ml                         MODIFY per-order roll-up and aggregates (no desk/tca.mli exists)
desk/trade_updates.ml               MODIFY the liveness watchdog
desk/oms.mli                        NEW    one submit site, by the compiler
desk/contract.ml, desk/contract.mli MODIFY synthetic manifests; R8 (if built); dead code removed
desk/intake.ml                      MODIFY venue kind; R8 observe/enforce; the demo path

bin/main.ml                      MODIFY  refresh, options poll, ops_extra, check-book, journal-backup,
                                         journal-verify, replay, serve's book path, demo arc
web/risk.html, web/risk.js       MODIFY  factor model, liquidity, estimators, options (indicative)
web/execution.html, web/execution.js MODIFY validation, per-order TCA, alerts, synthetic labels
web/research.html, web/research.js   MODIFY against live, R8, the synthetic strategy
web/nav.html                     MODIFY  the spec's order
web/graph.js                     MODIFY  the garch ghost node removed; A4 nodes placed and costed
web/argument.html, web/argument.js   MODIFY the GARCH sentence, the A5 sentence, the per-tick figures
web/ops.html, web/ops.js         MODIFY  the desk, clock and long_window blocks
web/quoted.json                  MODIFY  re-captured
web/stream.js                    MODIFY  the halt sentence

deploy/docker-compose.yml               MODIFY pull ${OHCAMEL_TAG}; backup service; caddy pinned and logged
deploy/docker-compose.local.yml         MODIFY the build blocks move here, tag local
deploy/docker-compose.ci.yml            NEW    the image job's override: loaded sha tags, no build block
deploy/Dockerfile, research.Dockerfile  MODIFY pinned bases; build ARGs on both; research runs as uid 10001
deploy/deploy.sh                 MODIFY  pull by sha, pre-deploy backup, check-book, health wait,
                                         market guard, --build builds both images
deploy/smoke.sh                  MODIFY  the desk on both hosts; --live-container
deploy/restore.sh, pull-backups.sh, watch.sh   NEW
deploy/systemd/ohcamel-{backup,watch}.{service,timer}   NEW
deploy/launchd/com.ohcamel.pull-backups.plist           NEW
deploy/test/{deploy_guard,deploy_dryrun,smoke_live,watch}_test.sh   NEW
deploy/deploy.env.example, live.env.example             MODIFY

.github/workflows/ci.yml         MODIFY  permissions, triggers, lint job, check-counts, plist on macOS,
                                         corrected comments
.github/workflows/image.yml      NEW     build, smoke, publish to GHCR
.github/workflows/uptime.yml     NEW     every 15 min; certificates
.github/dependabot.yml           NEW

research/src/ohcamel_research/verified.py       NEW    TESTS
research/src/ohcamel_research/service.py        MODIFY heartbeat; exchange calendar
research/src/ohcamel_research/contract.py       MODIFY R8 recipe (if built)
research/src/ohcamel_research/benchmark.py      NEW    buy-and-hold, report-only
research/README.md, interface/README.md         MODIFY real documentation
interface/signal.schema.json, interface/fixtures/r8/  MODIFY/NEW the recipe marker and the oracle

test/  MODIFY/NEW as each task states
README.md, docs/overview.md, docs/status.md, docs/quant_notes.md, book.example.sexp, Makefile  MODIFY
docs/media/*.png                 MODIFY  re-captured from the finished demo
docs/superpowers/specs/2026-09-12-the-desk-design.md   MODIFY §8 departures
docs/superpowers/specs/2026-08-31-server-side-deployment-design.md  MODIFY addendum
```

---

## Order and dependencies

**Stage 0 — Baseline** (`final/s0-baseline` from `main` at `b6132c6`). Tasks 1–7.
Parallel group A: Tasks 1, 4, 6 (plan and docs, `deploy/` and the new `lint` job, docs) and Task 2 (cherry-picks and the baseline clone) share no files and may run in four worktrees. Task 3 follows 2. Task 5 follows 2 and 4 (it pins the reconciled counts and adds `make check-counts` to the `lint` job Task 4 created). Task 7 is the close, and Task 7's housekeeping half waits on owner step **O1**.

**Stage 1 — Operations (§3.15)** (`desk/a6-ops`). Tasks 8–20, with Task 13a.
Parallel group B: Tasks 8, 12, 13 (CI, journal CLI, `check-book`) — 13 follows 12, and 13a follows 13. Then Task 9 follows 8; Task 10 follows 9; Task 11 follows 10. Parallel group C after 10: Tasks 15, 17. **Task 18 follows 13a**, because its watchdog reads the clock block. Task 14 follows 9 and 12. Task 16 follows 9, 10, 12, 13, 14 and 15. Task 19 follows 13a, 14, 16, 17, 18. Task 20 is the close.
Shared files this stage reconciles at Task 20: `lib/verified.ml` and the three markers (12, 13, 13a, 18), `bin/main.ml` (12, 13), `desk/desk.ml` (13a), `deploy/docker-compose.yml` (9, 11, 14, 18), `.github/workflows/ci.yml` (8, 11, 17), `docs/status.md` (11, 19).

**Stage 2A — the long window and the estimators (§3.13, first half)** (`desk/a4-long`, cut from `main`). Tasks 21, 22, 23, 27, 28, 29, 33A.
Task 22 is pure and parallel with everything. The graph tasks are **strictly serial** on `lib/graph.ml`, `lib/server.ml` and the pins: 21, then 27. Task 23 follows 21 and 22. Task 28 follows 27. Task 29 follows 27 and needs no factor-model work. Task 33A is the close; owner step **O18a** deploys it, and that deploy is what starts the `garch` and `cornish_fisher` clock.

**Stage 2B — the factor model, liquidity and the A4 pages (§3.13, second half)** (`desk/a4-depth`, recreated from `main` after 2A). Tasks 24, 25, 26, 30, 31, 32, 33B.
Serial on `lib/graph.ml`: 24, 25, 26. Then Task 30 follows 26. Task 31 follows 26 and 30. Task 32 follows 31. Task 33B is the close; owner step **O18b** follows.

**Stage 3 — Correctness** (`final/s3-correctness`). Tasks 34–48.
Re-declared against the real file overlaps. Serial spine on `bin/main.ml`, `web/execution.js`, `desk/sim_venue.ml` and `test/test_embedded_assets.ml`: **34 → 35 → 36 → 37 → 40 → 38 → 39 → 41 → 44 → 45 → 46**. Genuinely parallel, in their own worktrees, because they touch none of those files: **42** (`desk/trade_updates.ml`, `test/desk_async/`) and **43** (`test/desk_async/`, `test/test_oms*.ml`, `README.md`) — 42 and 43 both touch `test/desk_async/test_desk_async.ml` and `lib/verified.ml`, so 43 follows 42. Task 47 follows every other task in the stage. Task 48 is the close and reconciles `lib/verified.ml`, the three markers, `test/test_embedded_assets.ml`, `web/execution.js`, `desk/sim_venue.ml`, `bin/main.ml`, `README.md` and `docs/status.md`.

**Stage 4 — Two lanes** (`desk/a4-options` then `desk/a5-validation`, both from `main` after Stage 3). Tasks 49–63.
The two lanes **do** share files: `bin/main.ml` (49, 51, 52 against 58, 59, 60), `test/test_embedded_assets.ml` (53 against 59, 61), `test/test_server.ml` (49, 52 against 59), `README.md` and `docs/status.md` (54 against 62), `lib/verified.ml`, the count markers, `test/test_ohcamel.ml` and `deploy/smoke.sh`'s `EXPECTED_ROUTES`. So the options lane, which is shorter, **merges first**, and the validation lane rebases onto it before any of its tasks touches `bin/main.ml`, `test_server.ml` or `test_embedded_assets.ml`.
Options lane, serial: 49, 50 (50 is pure and parallel with 49), 51, 52, 53, 54.
Validation lane: 55 first; then 56, 57 and 60 in parallel (57 is pure and touches `lib/var_backtest.ml`, which no other task in either lane touches); 58 follows 55; 59 follows 57 and 58; 61 follows 55 and 59; 62 follows 56, 59, 60, 61.
Task 63 is the close, after both lanes are in, and reconciles the shared list above in full.

**Stage 5 — Research completion** (`final/s5-research`). Tasks 64–70, with Task 68a.
Task 64 first, because it decides whether 65 and 66 exist. Tasks 67, 68, 68a and 69 are parallel with each other and may start any time after Stage 0 in a spare worktree; 69's interface half follows 64, and 68a touches `research/src/ohcamel_research/service.py` which Task 18 also touched — 18 is merged by then, so 68a rebases on `main`. Task 65 follows 64; 66 follows 65. Task 70 is the close and reconciles `research/src/ohcamel_research/verified.py` and the count markers.

**Stage 6 — Finish** (`final/s6-finish`). Tasks 71–77, with Task 76a.
Task 71 first; 72 follows 71; 73 parallel with 71 and 72; 74 follows 72 and 73; 75 follows 74; 76 follows 75; **76a follows 76** and re-walks the audit table so no row cites evidence the fix wave or the coverage re-measure changed; 77 last.

Owner steps interleave: **O1** before Task 7's housekeeping; **O2–O9** any time after Stage 0 merges; **O10–O17** after Stage 1 merges; **O18a** after Stage 2A; **O18b** after Stage 2B; **O19** after Stage 3; **O20–O21** after Stage 4; **O22** after Stage 5; **O23–O25** after Stage 6.

---

## Stage 0 — Baseline: one history, one plan, a safe next deploy

Branch `final/s0-baseline`, cut from `main` at `b6132c6`, where `lib/verified.ml` reads `tests = 600`.

### Task 1: The final plan, the design doc, and every prior plan closed

**Files:** Create `docs/superpowers/specs/2026-09-19-the-finish.md`, `docs/superpowers/plans/2026-09-19-final-completion.md`, `docs/superpowers/plans/README.md` and `.superpowers/sdd/2026-09-19-final/progress.md`. Modify `.superpowers/sdd/conventions.md`, `.superpowers/sdd/2026-09-19-desk-a4-depth/progress.md` (one closing line), each of the sixteen existing plan files (one header line each) and, outside the repository, `~/.claude/projects/-Users-ajaiupadhyaya-Documents-OhCamel/memory/ohcamel-direction.md` and `MEMORY.md`.

**What must become true:**
1. **The design doc and this plan are in the repository,** complete, with A4's rulings 1–12 carried verbatim including amendment 5 (the conditioning floor is 0.01) and amendment 7 (monotonicity in closed form). No sentence in either **new** document states a 1e-8 floor.
2. **A trace table** in the plan doc maps every item the four completion surveys marked *missing*, *partial* or *broken* to a task number or a ruling, and every *owner-only* item to an owner step. No item is unmapped.
3. **The plans index** names each of the fifteen finished-phase plans as the historical record of a finished phase, and names `2026-09-19-desk-a4-depth.md` (never merged; tagged `archive/a4-plan-v2` in Task 7) as superseded by this plan. Each existing plan file gains exactly one header line saying which it is. None is edited otherwise, and none is deleted. The A4 plan's header line states that its ruling 5 was amended to a 0.01 conditioning floor and that its line 363's `1e-8` is a superseded code sketch — because the file is evidence of how the decision was reached and editing its body would destroy that.
4. **`conventions.md`** states the commit trailer, the worktree build rule (dune with the main switch, never `make`), the permitted `make` homes (the primary checkout and a dedicated local clone under `$TMPDIR`) and the `$TMPDIR/final-taskN-*` scratch prefix.
5. **The ledger's first entry** quotes the owner's direction and records the standing push-and-merge authorization once the executing session has confirmed it.
6. **The memory note** says the direction is this plan, and no longer names the `OhCamel-figure` worktree.

**Tests:** `grep -L 'docs/superpowers/plans/2026-09-19-final-completion.md' docs/superpowers/plans/*.md` prints only the index and this plan. `grep -n '1e-8' docs/superpowers/specs/2026-09-19-the-finish.md docs/superpowers/plans/2026-09-19-final-completion.md` is empty — the grep is scoped to the new documents, because the archived plan keeps its line. `git diff --stat` touches no file under `lib/`, `desk/`, `bin/`, `web/`, `test/` or `research/src/`.

**Commit** as `plan:`.

### Task 2: Integrate the four finished pure modules, and capture the baseline

**Files:** Cherry-pick, in this order, `29fa0b7`, `dae577e` (from `desk/a4-depth`), `26e18ed`, `65eb552` (from `a4/t3`), `ef9f027`, `4bb3ee6` (from `a4/t5`), `a829812`, `07b43e2` (from `a4/t7a`). Resulting files: `lib/types.ml`, `lib/long_panel.ml`, `lib/factor_model.ml`, `lib/liquidity.ml`, `lib/risk_metrics.ml`, `test/test_long_panel.ml`, `test/test_factor_model.ml`, `test/test_liquidity.ml`, `test/test_risk_metrics.ml`, `test/test_ohcamel.ml`, `lib/verified.ml`, `README.md`, `docs/overview.md`, `docs/status.md`. Creates `$TMPDIR/final-baseline/` outside the repository.

**What must become true:**
1. **All eight kernel commits are picked,** because the branch is cut from `main` at `b6132c6` and none of them is on it. Never pick `8e2e64a`, `8af3206` or `701f45c`, which are plan-document commits.
2. **The conflicts are resolved once:** `lib/verified.ml` reads `tests = 653` and `scheduler_tests = 30` — the arithmetic is `600 + 16 + 16 + 10 + 11`, each delta measured from `main` at `b6132c6` (long panel 600→616, factor model 600→616, liquidity 600→610, Cornish–Fisher 600→611); `test/test_ohcamel.ml` registers `Test_long_panel`, `Test_factor_model` and `Test_liquidity` exactly once each, with the Cornish–Fisher cases inside the existing `Test_risk_metrics`; the three count sentences agree.
3. **Nothing is wired.** `lib/graph.ml`, `bin/main.ml` and `web/` reference none of the four modules. A grep proves it.
4. **Each module is byte-identical to its source tip.** `git diff a4/t3 -- lib/factor_model.ml test/test_factor_model.ml` is empty, and likewise `a4/t5` for `lib/liquidity.ml` and `test/test_liquidity.ml`, `a4/t7a` for `test/test_risk_metrics.ml` and `lib/risk_metrics.ml`'s Cornish–Fisher section, and `dae577e` for `lib/long_panel.ml`.
5. **The baseline artefacts exist and are saved.** `git clone --local /Users/ajaiupadhyaya/Documents/OhCamel $TMPDIR/final-baseline/repo`, `git -C $TMPDIR/final-baseline/repo checkout b6132c6`, then in that clone on the main opam switch run `make backtest`, `make backtest-crisis`, `make garch`, `make stress`, `make options` and `make run`, saving their stdout to `$TMPDIR/final-baseline/{backtest,backtest-crisis,garch,stress,options,run-scaling}.txt`. The clone is why this is safe: it has its own `_coverage/` and its own outputs and shares only the object store, so nothing in the primary checkout is touched. Record the six files' sizes and sha256 in the ledger, so a lost baseline can be shown to have been regenerated identically.

**Tests:** `dune build` succeeds; `dune runtest` reports 653 main and 30 scheduler tests; `dune build @fmt` is clean; the invariant grep over `lib/` exits 1. The six-mode gate run in a second clone at this branch's tip is byte-identical to `$TMPDIR/final-baseline/*.txt`.

**Commit** as `kernel:`, with a message naming each of the eight picked shas.

### Task 3: Liquidity refuses a non-finite spread; one named minimum-rows constant

**Files:** `lib/config.ml` (`Desk_spec.validate`), `lib/liquidity.ml`, `lib/risk_metrics.ml`, `lib/factor_model.ml`, `test/test_liquidity.ml`, `test/test_example_book.ml`, `lib/verified.ml` and the three count sentences.

**Interfaces — produces:** no new public function. `Desk_spec.validate` gains two refusals, and the minimum-rows literal becomes one named constant.

**What must become true:**
1. **`Desk_spec.validate` refuses a non-finite or negative `spread_bps_default`, and any non-finite or negative `spread_bps` entry,** naming the field and the symbol. Today it checks only `< 0`, so a `nan` passes and turns LVaR into `nan` — Task 5's review of the liquidity work found this and left it open.
2. **`Liquidity.line` returns `None` when `half_spread_bps` is not finite,** as a second guard, because a node body may not raise.
3. **The literal `120`** in `Risk_metrics.cornish_fisher_var` and `Factor_model.min_observations` becomes one named constant in a module both already depend on, with no dependency cycle. If no such module exists, each keeps its literal and gains a comment cross-referencing the other by name.

**Tests (hand-derived):**
- `(spread_bps_default nan)` is an `Error` naming `spread_bps_default`; `(spread_bps ((SPY -1.0)))` is an `Error` naming `SPY`.
- A liquidity line with `half_spread_bps = nan` is `None`.
- A spread cost is exact: 100 shares at 50.00 with 5 bps is 100 × 50 × 5e-4 = **2.50**.

**Commit** as `kernel:`.

### Task 4: `deploy.sh` is safe for the overdue W2 and A3 deploy, and CI lints it

**Files:** `deploy/deploy.sh`, `deploy/test/deploy_guard_test.sh` (new), `.github/workflows/ci.yml` (a new `lint` job), `docs/status.md` (the "Operating it" lines about deploying and about a book edit).

**Interfaces — produces:** a pure shell function `in_market_window "$utc_iso"` returning 0 when a live deploy should be refused; a CI job named `lint`.

**What must become true:**
1. **The build step passes `"${PROFILE[@]}"`.** Today `deploy.sh`'s build omits the live profile, so compose skips `ohcamel-research` (which is `profiles: ["live"]`), builds it only once through `up -d`'s missing-image path, and leaves a stale research image on every later deploy.
2. **The live profile is added automatically** when `/etc/ohcamel/live.env` exists or an `ohcamel-live` container exists, with one log line saying so — so a demo-only deploy can no longer leave the live engine on an old image while the smoke suite passes.
3. **Pruning after `up`:** `docker image prune -f` and `docker builder prune -f --keep-storage 5GB`.
4. **The market-hours guard.** A live deploy is refused on a weekday in [13:25, 20:10) UTC unless `--during-market` is given, with a message saying why. `DEPLOY_NOW` overrides the clock for the test.
5. **A `lint` job exists in Stage 0, not Stage 1,** so this script does not reach the owner unlinted. It runs on ubuntu with `permissions: contents: read`, and does two things now: `shellcheck deploy/*.sh deploy/test/*.sh scripts/*.sh` and every `deploy/test/*.sh`. Task 8 extends it; Task 5 adds `make check-counts` to it. The ordering matters because owner step **O2** — the overdue W2 and A3 deploy — runs on this script.
6. **`docs/status.md` is corrected:** `deploy.sh` fast-forwards the checked-out branch (it does not always build `origin/main`); a book edit on the live host needs `docker compose -f deploy/docker-compose.yml --profile live restart ohcamel-live`, not a demo restart.

**Tests:** `bash -n deploy/deploy.sh` passes. `deploy/test/deploy_guard_test.sh` prints ok for five hand cases: Monday 13:24 allowed; Monday 13:25 refused; Wednesday 20:09 refused; Wednesday 20:10 allowed; Saturday 15:00 allowed. The `lint` job is green on the branch and a seeded shellcheck error on a throwaway commit fails it (the run URL goes in the report and the commit is reverted).

**Commit** as `deploy:` for the script and `ci:` for the job; two commits is fine.

### Task 5: Counts cannot drift, and CI is what stops them

**Files:** Create `research/src/ohcamel_research/verified.py` and `scripts/check-counts.sh`. Modify `research/tests/conftest.py`, `Makefile` (a `check-counts` target, added to `.PHONY`), `.github/workflows/ci.yml` (the `lint` job Task 4 created gains a `make check-counts` step), `README.md`, `docs/overview.md`, `docs/status.md`.

**Interfaces — produces:**
```
research/src/ohcamel_research/verified.py:  TESTS = 388
scripts/check-counts.sh                      exit 0 when every marker matches the code
make check-counts                            runs it
ci.yml: lint                                 runs make check-counts on every push
```

**What must become true:**
1. **Each quoted count in the three documents is wrapped in an invisible marker:** `<!-- count:ocaml-tests -->653<!-- /count -->`, and the same form for `count:scheduler-tests` and `count:research-tests`.
2. **`check-counts.sh` compares every marker** with `lib/verified.ml`'s `tests` and `scheduler_tests` and with `verified.py`'s `TESTS`, and fails naming the file and line on any mismatch. It parses the OCaml and Python constants by grep, needs no build, and runs in under a second so the `lint` job stays cheap.
3. **CI runs it.** Without this step the target is decoration: the OCaml suite already asserts its own count against `lib/verified.ml` (`test/test_ohcamel.ml`), but the three prose numbers are checked by nothing automatic today, which is exactly how `355` survived in three documents. This step is what makes the finish spec's §5 item 3 true.
4. **`conftest.py`'s `pytest_collection_modifyitems` asserts `len(items) == TESTS`** only when the whole `research/tests` directory is collected with no `-k`, `-m` or node-id filter, so `pytest -k contract` still works.
5. **The three stale `355`s become `388`.**

**Tests:** `make check-counts` passes. Editing one marked number in a scratch copy under `$TMPDIR/final-task5-*` makes it fail, naming the file and line. A throwaway commit that edits one marked number fails the `lint` job in CI; the run URL goes in the report and the commit is reverted. `cd research && uv run pytest --collect-only -q | tail -1` reports 388 and the hook passes. `uv run pytest -k contract` runs with the hook inactive. `grep -rn '355' README.md docs/overview.md docs/status.md` finds no test count.

**Commit** as `verified:`.

### Task 6: Stale published facts

**Files:** `docs/status.md` (the Deployed row, the `backtest-crisis` drive-table row, "Next").

**What must become true:**
1. **The Deployed row** says the droplet runs `e0f5a71` (A2), and that `main` at `b6132c6` — W2's `b9471da` and A3 — is merged and not deployed. Today the row names `4e04b29` as the undeployed commit, which is wrong.
2. **`backtest-crisis` runs three windows: GFC, COVID and rates-2022.** Today one row says "COVID and 2022".
3. **"Next"** lists Stages 0, 1, 2A, 2B, 3, 4, 5 and 6 and the owner steps, and points at this plan instead of the superseded A4 plan.

**Tests:** `grep -n 'COVID and 2022' docs/status.md` is empty. `lib/crisis_data.ml`'s `window_names` has three entries and the row matches them. `make check-counts` still passes.

**Commit** as `docs:`.

### Task 7: Stage 0 close, and housekeeping

**Files:** `lib/verified.ml` (coverage constants, re-dated), `README.md` (badge and per-file table), `docs/status.md`, the ledger; then git refs only.

**What must become true:**
1. **A whole-branch review** of Stage 0, explicitly covering `dae577e`'s fix round: duplicate bars, a duplicate instrument, duplicate DGS10 dates and a negative window are `Error`s rather than exceptions; `last_close` and `adv20` read no bar after `as_of`; a stale DGS10 is an `Error`; the factor order is tied to `Factor.all`; and the four surviving mutations are pinned.
2. **The fix wave runs,** each fix scoped-reviewed.
3. **Coverage is re-measured in a dedicated clone.** `git clone --local . $TMPDIR/final-cov-s0/repo`, checkout `final/s0-baseline`, main opam switch, `make coverage`; the figures are transcribed into `lib/verified.ml`, the badge and `docs/status.md` on the branch, with the date and the machine. The primary checkout is not switched, so the owner's untracked files are untouched.
4. **Merge and push.** Fast-forward `main`, push, wait for CI green including `lint`.
5. **Housekeeping, after owner step O1.** Tag `archive/a4-plan-v2` at `701f45c` and `archive/agent-a3-leftover` at `d0b5f98`. Remove the worktrees `OhCamel-a4t3`, `OhCamel-a4t5`, `OhCamel-a4t7a`, `OhCamel-figure` and `.claude/worktrees/agent-a0cbd73aa352f7ae6`. Delete the branches `a4/t3`, `a4/t5`, `a4/t7a`, `desk/a4-depth` (the old one; Stage 2B recreates the name from `main`), `desk/w1-figure`, `roadmap-phases`, `the-page/phase-4`, `the-page/phase-5` and `worktree-agent-a0cbd73aa352f7ae6`. A branch is deleted only after `git cherry main <branch>` shows no `+` line, or after it has been tagged; the output goes in the ledger.

**Tests:** CI on `main` is green on both operating systems, plus `lint`, `coverage` and `research-test`. `git worktree list` shows only the primary checkout. `git branch` shows `main` alone. Both archive tags resolve.

**Commit** as `verified:` for the coverage figures; the merge is a fast-forward.

---

## Stage 1 — Operations (§3.15): images built in CI, deploys that pull, backups that restore

Branch `desk/a6-ops`. Accepted when a deploy pulls and a backup restores.

### Task 8: CI hygiene, pinned bases, and the `lint` job extended

**Files:** `.github/workflows/ci.yml`, `.github/dependabot.yml` (new), `Makefile` (`.PHONY` gains `run-live`, `serve`, `demo`; the coverage sentence), `deploy/Dockerfile` and `deploy/research.Dockerfile` (`FROM` lines pinned to exact versions).

**What must become true:**
1. **`ci.yml` gains top-level `permissions: contents: read`** and push triggers for `main`, `desk/**` and `final/**`, so a stage branch runs CI before its merge.
2. **The `lint` job (Task 4's) gains:** `docker compose -f deploy/docker-compose.yml config -q` for the default and the live profile, using a CI-only override that supplies a dummy `live.env` (the real file is `required: true`); the same for `deploy/docker-compose.ci.yml` once Task 9 creates it; `caddy validate` for `deploy/Caddyfile` and `deploy/Caddyfile.local` in a pinned caddy image with dummy environment values; and `systemd-analyze verify` on every unit under `deploy/systemd/` once Task 14 and Task 18 create them. `systemd-analyze` is present on GitHub's ubuntu runners but warns on a `User=` the runner does not have, so the step asserts the *parse*: it fails when the output contains `Failed to parse`, `Unknown lvalue`, `Invalid section` or `Unknown section`, and tolerates the unknown-user warning, which is named in a comment.
3. **The plist check lives on macOS.** `plutil -lint deploy/launchd/com.ohcamel.pull-backups.plist` runs as a step in `build-and-test`'s macos matrix leg, guarded by `if: runner.os == 'macOS'`, because `plutil` is macOS-only and is not on an ubuntu runner. A comment says so.
4. **Two wrong comments are corrected:** the six-mode step's comment says four things, and the coverage comment says "above 90% / near 40%" where `README.md` and `docs/status.md` say above 85% and 36–64%. One coverage sentence, agreed across `Makefile`, CI, `README.md` and `docs/status.md`.
5. **Dependabot** covers `github-actions` and `docker`, weekly.
6. **Base images are pinned** to exact versions rather than floating tags.

**Tests:** CI on `desk/a6-ops` is green including `lint` and the macOS plist step. A seeded shellcheck error on a throwaway commit fails `lint`; a seeded `Unknown lvalue` in a unit file fails it too; both run URLs go in the report and the commits are reverted. GitHub reports no dependabot configuration error.

**Commit** as `ci:`.

### Task 9: Compose pulls an immutable tag, and CI gets its own override

**Files:** `deploy/docker-compose.yml`, `deploy/docker-compose.local.yml`, `deploy/docker-compose.ci.yml` (new), `deploy/local.env`, `Makefile` (the `deploy-*` targets).

**What must become true:**
1. **`x-engine`** uses `image: ${OHCAMEL_IMAGE:-ghcr.io/ajaiupadhyaya/ohcamel}:${OHCAMEL_TAG:?deploy.sh sets OHCAMEL_TAG}`, and `ohcamel-research` uses `ghcr.io/ajaiupadhyaya/ohcamel-research:${OHCAMEL_TAG:?}`.
2. **Both `build:` blocks move to `deploy/docker-compose.local.yml`,** where the tag stays `local`, so `make deploy-build`, `make deploy-up` and `make deploy-verify` still work; they pass `OHCAMEL_TAG=local`.
3. **`deploy/docker-compose.ci.yml` is a third override for the image job only:** it sets both services' `image:` to the sha tags buildx `--load`ed, carries **no `build:` block**, and points `env_file` at a dummy CI env. It exists because Task 10 brings the harness up on images it has just loaded, and composing base + `local` would rebuild from the `local` build blocks instead of using them. A comment in each file says which override is whose.
4. **`caddy` is pinned to an exact 2.x version** and gains the `x-engine` logging block (json-file, 10m × 3). Today it has no logging block, so its one-line-per-request console log grows without bound under the daemon default.

**Tests:** in `lint`, the rendered config shows ghcr refs with the sha when `OHCAMEL_TAG` is set and fails with the `:?` message when it is unset; the local config still carries its build blocks; the ci config renders and `grep -c 'build:' deploy/docker-compose.ci.yml` is 0. `grep -n 'build:' deploy/docker-compose.yml` is empty. No image tag floats.

**Commit** as `deploy:`.

### Task 10: The image workflow — build, smoke, publish

**Files:** `.github/workflows/image.yml` (new).

**What must become true:**
1. **Triggers:** `workflow_run` of `ci` completed with success on `main`, checking out `head_sha`; pushes to `desk/**` and `final/**` and pull requests, which build and smoke but never push; `workflow_dispatch`. Permissions `contents: read, packages: write`.
2. **Steps:** set up buildx; build `deploy/Dockerfile` and `deploy/research.Dockerfile` for `linux/amd64` with build args `OHCAMEL_GIT_SHA` and `OHCAMEL_BUILT_AT`, OCI labels `org.opencontainers.image.source` and `.revision`, the `type=gha,mode=max` cache and `--load`; bring up the local harness with `-f deploy/docker-compose.yml -f deploy/docker-compose.ci.yml` on those loaded tags; run `deploy/smoke.sh http://localhost:8000 --expect-sha <sha>`; tear down; on `main` only, log in to ghcr with `GITHUB_TOKEN` and push `:<sha>` and `:main` for both images. The job summary lists both digests.
3. **Image names are hard-coded in lowercase.** `github.repository` is `ajaiupadhyaya/OhCamel`, which is not a valid image name.
4. **Both images carry their stamp.** A step asserts `docker run --rm <engine> printenv OHCAMEL_GIT_SHA` and `docker run --rm --entrypoint printenv <research> OHCAMEL_GIT_SHA` each print the sha. Task 11 declares the ARGs in `research.Dockerfile`, which has none today, so without that step the arg is silently dropped and only buildx's warning would say so.
5. **No new secret.** `GITHUB_TOKEN` with the job's `packages: write` suffices; the repository's default workflow permission of `read` does not block it.

**Tests:** a run on `desk/a6-ops` builds both images and pushes nothing; the log shows `research.Dockerfile`'s staleness `RUN` step and `uv sync --locked` passing — the first time either has ever run, because the local daemon is unhealthy and the research image has never been built anywhere. The harness smoke passes. Both stamp assertions pass. After Task 20 merges, the run on `main` publishes both tags, and `gh api /users/ajaiupadhyaya/packages/container/ohcamel/versions` lists the merge sha.

**Commit** as `ci:`.

### Task 11: The research image runs as a non-root user, and declares its build stamp

**Files:** `deploy/research.Dockerfile`, `deploy/Dockerfile` (only if uid alignment needs it), `deploy/docker-compose.yml`, `.github/workflows/image.yml` (a check step), `docs/status.md` (the recorded root departure).

**What must become true:**
1. `/signals` is created and owned by uid 10001 — the engine's uid — before the volume is seeded, and the image sets `USER 10001`. The engine keeps its read-only mount. The departure recorded in `docs/status.md` is removed, because it was a guess about volume seeding that no build had ever tested.
2. **The file declares `ARG OHCAMEL_GIT_SHA` and `ARG OHCAMEL_BUILT_AT` with matching `ENV`s,** as `deploy/Dockerfile:74–77` does. It has no `ARG` line at all today, so Task 10's build args were dropped for this image and an operator inside the droplet could not tell which sha the research container was.

**Tests:** in the image job, on a fresh named volume: `docker run --rm -v sig:/signals <research> id -u` prints 10001; `touch /signals/probe` succeeds; the engine image reads `/data/signals/probe` through a `:ro` mount; `docker run --rm --entrypoint printenv <research> OHCAMEL_GIT_SHA` prints the sha.

**Commit** as `deploy:`.

### Task 12: Journal backup, verify and retention

**Files:** `desk/journal.ml`, `desk/journal.mli`, `desk/backup_retention.ml` and `desk/backup_retention.mli` (new, pure), `bin/main.ml` (two modes and the usage text), `test/test_journal_backup.ml` (new), `test/test_ohcamel.ml`, `lib/verified.ml` and the three markers.

**Interfaces — produces:**
```ocaml
(* desk/journal.mli -- exists today; these are additions *)
val open_read_only : string -> (t, string) Result.t     (* SQLITE_OPEN_READONLY; no set_up *)
val backup : src:string -> dst:string -> (unit, string) Result.t
val verify : string -> (report, string) Result.t        (* schema version, integrity, per-table counts,
                                                           newest session date, newest order *)
(* desk/backup_retention.mli -- new, and created in this task alongside its .ml *)
val keep : now:Date.t -> string list -> string list * string list   (* kept, deleted *)
```

**What must become true:**
1. **`backup`** uses `Sqlite3.Backup` (init, `step (-1)`, finish) into `dst.tmp`, then renames, at mode 0640, with a busy timeout. It only reads the source.
2. **`keep`** keeps the 14 newest dailies, the 8 most recent Sundays among the rest, and the 5 newest `pre-deploy-*`.
3. **`ohcamel journal-backup SRC DIR [--name NAME]`** writes `DIR/desk-YYYY-MM-DD.db`, verifies the copy, prunes by `keep`, and exits non-zero on any failure. **`ohcamel journal-verify FILE`** prints the report and exits non-zero on any problem.
4. **`Sqlite3` stays in `desk/`,** so the invariant-6 grep stays silent.

**Tests (hand-derived):**
- **Round trip.** A temp-file WAL journal with a second connection open and at least one row in each of `sessions`, `marks`, `forecasts`, `orders`, `order_events`, `fills`, `alerts`, `signals`, `signal_files` and `signal_deferrals`. After the backup: the copy opens read-only; every table matches row for row; `schema_version` is equal; `integrity_check` is `ok`.
- **Retention.** 61 dailies from 2026-09-01 to 2026-10-31, `now` = Saturday 2026-10-31. Kept: **20** — the 14 dailies 10-18 through 10-31, plus the six older Sundays 09-06, 09-13, 09-20, 09-27, 10-04 and 10-11 (10-18 is a Sunday but is already among the 14). Deleted: **41**. Of seven `pre-deploy-*` files, the five newest are kept.
- `journal-verify` on a truncated copy exits non-zero.

**Commit** as `desk:`.

### Task 13: `check-book`, and a CLI that never throws on a bad argument

**Files:** `lib/config.ml` (`Config.check`), `bin/main.ml` (`check-book [PATH]`; `serve [port] [book]`; the port parse; the usage text), `test/test_example_book.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** `val Config.check : string -> (summary, string list) Result.t`, where `summary` carries the universe, the limits, the strategies and the desk switches.

**What must become true:**
1. **`check-book`** parses the book and runs every validation — limits, alerts, desk, signals, and R7's requirement that a strategy's symbols are in the universe — prints the summary, and exits 1 listing each error. `deploy.sh` runs it before the engine restarts (Task 16), and the owner runs it after every edit.
2. **`serve` takes an optional book path,** as `live` does. Today it always passes `Config.default_book_path`.
3. **A non-numeric port prints the usage and exits 2,** with no backtrace. Today `Int.of_string` on raw argv raises.

**Tests:** `book.example.sexp` is `Ok` with the counts it declares; an unknown limit kind is an `Error` naming it; a strategy symbol outside the universe is an `Error` naming it. `ohcamel check-book book.example.sexp` exits 0. `ohcamel serve abc` prints the usage and exits 2.

**Commit** as `desk:`.

### Task 13a: The desk states its market clock

**Files:** `desk/desk.ml` (a cached `Session_clock` and `summary_fields`), `bin/main.ml` (the existing `Config.Runtime.clock_interval` timer at `bin/main.ml:1485` also refreshes the cache), `desk/sim_venue.ml` (a synthetic clock), `web/ops.js`, `web/desk.js`, `test/test_desk.ml`, `test/test_embedded_assets.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** a `clock` object on `/api/desk` and inside `/api/ops`' desk block:
```
"clock": { "is_open": true, "next_open": "…", "next_close": "…",
           "next_close_date": "2026-09-21", "read_at": "…",
           "source": "venue-clock" | "stale" | "unknown" }
```

**What must become true:** ruling 20. `Venue.Session_clock` already exists with `now`, `is_open`, `next_open`, `next_close` and `next_close_date` (`desk/venue.ml:60–68`), `Read.clock` is in the venue interface (`:177`), `Alpaca_paper.clock_of_json` parses it (`:197`), and `oms.ml:2010` and `session_close.ml:216` already call it. Nothing publishes it, so `watch.sh`'s two most valuable checks would be unimplementable from a host script that may not read a credential. The desk therefore caches the newest reading with the time it was taken; a reading older than 15 minutes reports `source: "stale"` and keeps its values; no reading yet reports `source: "unknown"` with nulls. `Sim_venue` reports a synthetic weekday clock so the demo's block is populated and labelled. `/ops` and the Desk panel render it as text. **This task precedes Task 18.**

**Tests (hand-derived):** a sim-venue desk reports `is_open` matching the synthetic weekday and a `next_close_date` equal to the synthetic session date; a reading stamped 16 minutes back reports `source: "stale"` with its original values; with no reading the fields are null and `source` is `"unknown"`; the `/api/ops` desk block carries the same object as `/api/desk` (one constructor, asserted by equality in the test); the `/ops` and Desk page markers are pinned; `grep -rn 'Session_clock' lib/` is empty, so the kernel still does not name the venue.

**Commit** as `desk:`.

### Task 14: The backup service, its timer, restore, the drill and the off-box pull

**Files:** `deploy/docker-compose.yml` (an `ohcamel-backup` service), `deploy/systemd/ohcamel-backup.service` and `.timer` (new), `deploy/restore.sh` (new), `deploy/pull-backups.sh` (new), `deploy/launchd/com.ohcamel.pull-backups.plist` (new).

**What must become true:**
1. **`ohcamel-backup`:** profile `backup`, the same image and tag as the engine, `network_mode: none`, a read-only root filesystem, `desk_data` mounted read-write (a WAL reader may need to create `-shm`; the process only reads), `/var/backups/ohcamel` bound at `/backups`, running `journal-backup /data/desk.db /backups`.
2. **The timer** is `OnCalendar=*-*-* 06:30:00 UTC`, `Persistent=true`, `User=ohcamel`. 06:30 UTC is clear of the session close (close + 5 min, 20:05 or 21:05 UTC), the research run (00:16 UTC) and the open. It also copies `book.sexp` and `deploy/.env` beside the backup, and **never** `/etc/ohcamel/live.env`.
3. **`restore.sh --drill FILE`** runs `journal-verify` on a scratch copy in a throwaway container and never touches the live volume. **`restore.sh --restore FILE`** stops `ohcamel-live`, moves `desk.db` and its `-wal` and `-shm` aside inside the volume, copies the backup in, chowns it to 10001, starts the engine, waits for the log line `first sync succeeded … restored N session closes`, then compares `/api/desk`'s session count with the verify count. It refuses an unverified file, and refuses inside the market window.
4. **`pull-backups.sh`** rsyncs `/var/backups/ohcamel/` to `~/Backups/ohcamel/` on the laptop; the launchd plist runs it while the laptop is awake.

**Tests:** `shellcheck` is clean on all three scripts; `systemd-analyze verify` passes on both units in the ubuntu `lint` job under Task 8's parse assertion; `plutil -lint` on the plist passes in `build-and-test`'s **macOS** leg, because `plutil` is macOS-only; `compose --profile backup config` renders; `restore.sh --drill --dry-run` prints only commands that read a copy. The spec's *a backup that restores* is accepted by owner step **O14**, recorded in `docs/status.md`.

**Commit** as `deploy:`.

### Task 15: The smoke suite sees the desk on both hosts

**Files:** `deploy/smoke.sh`, `deploy/test/smoke_live_test.sh` (new).

**What must become true:**
1. **Demo checks.** POSTs to `/api/desk/orders`, `/api/desk/cancel`, `/api/desk/kill` and `/api/desk/kill/reset` each answer 405 with a JSON `error` sentence — today only `orders` is checked. In demo mode `/api/desk` reports venue `simulated` and journal `memory`, and its `clock` block is present with `source: "venue-clock"`.
2. **A `--live-container` mode** runs through `docker compose -f deploy/docker-compose.yml --profile live exec -T ohcamel-live curl http://localhost:8081` — the runtime image ships curl for its healthcheck — so no credential enters the suite. It checks:
   - `/api/desk`: status `enabled`, venue `alpaca-paper`, journal `file`, `kill_switch` present, `clock.source` not `"unknown"`, `last_sync` non-null and under 180 s old (polled for up to 120 s, because the first sync follows startup and backfill), `last_error` null, `account.status` `ACTIVE`, `account.trading_blocked` false;
   - without `X-OhCamel-Desk`: POST `orders` with body `{}`, `cancel` with a nonexistent id, and `kill/reset` with no confirm field each answer 403; with the header but no `Origin` or `Sec-Fetch-Site`, 403;
   - **`/api/desk/kill` is never POSTed,** with a comment saying why (ruling 13) and naming the §8 item Task 71 records, because §3.15's acceptance asks for all four mutating routes and this one is covered only by the hermetic tests;
   - `/api/ops`: mode `live`, `build.git_sha` equal to `--expect-sha`, uptime under 300 s;
   - the `ohcamel-research` container is running, its image revision label equals the sha, and `printenv OHCAMEL_GIT_SHA` inside it equals the sha;
   - `/api/research`: intake `running`, with the strategy count reported rather than asserted (0 before the owner's book merge).
3. **`EXPECTED_ROUTES`** stays an exact, ordered copy of the 404 route list; every later task that adds a route appends it in the same commit.

**Tests:** `shellcheck` is clean. The image job's harness smoke passes with the new demo checks. `smoke_live_test.sh` drives `--live-container` against canned JSON through shim `docker` and `curl` binaries on `PATH`, and **fails** on each of: a set `last_error`; a mutating route answering 200; a sha mismatch; a `clock.source` of `"unknown"`.

**Commit** as `deploy:`.

### Task 16: `deploy.sh` deploys a sha by pulling

**Files:** `deploy/deploy.sh`, `deploy/test/deploy_dryrun_test.sh` (new).

**What must become true:** flags `--live`, `--sha SHA` (default `origin/main`), `--build`, `--during-market`, `--dry-run`. Steps, in this order:
1. `git fetch origin`.
2. Check out the sha, detached, so compose, the Caddyfile and `smoke.sh` match the image. `book.sexp` is untouched.
3. Wait up to 40 minutes for `docker manifest inspect` on both GHCR images; otherwise exit 1 naming the sha and saying CI has not published it. `--build` skips this wait.
4. Refuse a live deploy inside the market window unless `--during-market` (Task 4's guard, kept).
5. Take a pre-deploy backup to `pre-deploy-<old-sha>-<UTC>.db` through the backup service, skipped with a message when no journal exists yet, and abort if it fails.
6. Run `check-book` with the new image; abort on failure.
7. `OHCAMEL_TAG=$SHA docker compose … --profile live pull`, passing `OHCAMEL_TAG` as a per-command prefix and never exporting it next to `deploy/.env` values.
8. `up -d --remove-orphans`.
9. Wait up to 120 s for both engines to report healthy — replacing today's fixed `sleep 15`.
10. Smoke the public hosts, then `--live-container`.
11. Append `UTC sha result` to `~/deploys.log`, which becomes the rollback reference.
12. Keep the last 3 tags and prune the rest.

**`--build` builds both images.** It runs `docker build -f deploy/Dockerfile -t ghcr.io/ajaiupadhyaya/ohcamel:$SHA .` **and** `docker build -f deploy/research.Dockerfile -t ghcr.io/ajaiupadhyaya/ohcamel-research:$SHA .`, both with the `OHCAMEL_GIT_SHA` and `OHCAMEL_BUILT_AT` args, then skips step 3 and proceeds. This matters because after Task 9 compose has no `build:` block at all and owner step **O10** makes `--build` the only deploy path until GHCR visibility is settled: a one-image fallback would bring `ohcamel-research` up on a missing or stale image, which is the exact class of defect Task 4 exists to fix.

Rollback is `--sha <previous>`.

**Tests:** `deploy_dryrun_test.sh` records shim invocations and asserts the order fetch, manifest × 2, backup, check-book, pull, up, health, smoke, log; a failing `check-book` aborts **before** the pull; `--dry-run --sha deadbeef` prints the pull and up lines carrying `OHCAMEL_TAG=deadbeef`; `--dry-run --build --sha deadbeef` prints **two** builds and **two** tags, one per Dockerfile, and no manifest wait; `DEPLOY_NOW=2026-09-21T15:00Z` prints the market-hours refusal. `shellcheck` is clean.

**Commit** as `deploy:`.

### Task 17: Uptime and certificate watch from GitHub

**Files:** `scripts/uptime.sh` (new), `.github/workflows/uptime.yml` (new).

**What must become true:** the workflow runs on `schedule: '*/15 * * * *'` and on dispatch, with `permissions: contents: read` and no secrets, and makes read-only GETs only. It checks: the demo's `/` and `/api/health` answer 200; `nodes_recomputed` advances over 2 s; `/api/desk` has its shape; HTTP redirects to HTTPS on both hosts; the live host's `/` and `/api/desk` answer 401; each certificate has more than 21 days left (warn) and more than 10 (fail), via `openssl s_client … | openssl x509 -noout -checkend`. A failing run alerts by failing, once owner step **O11** sets notifications. `docs/status.md` (Task 19) records that GitHub disables a scheduled workflow in a public repository after 60 days without activity, and that `gh workflow enable uptime.yml` re-enables it.

**Tests:** a `workflow_dispatch` run against production is green. `scripts/uptime.sh` run locally prints every check. `shellcheck` is clean.

**Commit** as `ci:`.

### Task 18: On-host watch, and the research service's heartbeat

**Files:** `deploy/watch.sh` (new), `deploy/systemd/ohcamel-watch.service` and `.timer` (new), `deploy/test/watch_test.sh` (new), `research/src/ohcamel_research/service.py`, `research/tests/test_service.py`, `research/src/ohcamel_research/verified.py`, `deploy/docker-compose.yml` (the research healthcheck). **Follows Task 13a**, whose `clock` block it reads.

**What must become true:**
1. **The research service touches a heartbeat file on every poll loop,** and its compose healthcheck fails when the heartbeat is more than 900 s old. Today the service has no healthcheck at all.
2. **`watch.sh` runs every 5 minutes as the `ohcamel` user** and checks: container states, restarting once a container has been unhealthy for 3 consecutive runs and alerting; `/api/desk`'s `last_error` set; `last_sync` over 180 s old **while `/api/desk`'s `clock.is_open` is true and `clock.source` is `"venue-clock"`** — the field Task 13a put on the wire, read in-container over plain HTTP with no credential; the kill switch tripped; no session row 30 minutes after `clock.next_close_date`'s close; no signal file for today after 01:00 UTC when the book has strategies; the newest backup more than 26 h old; disk over 80%; `/var/run/reboot-required` present. When `clock.source` is `"stale"` or `"unknown"` the sync-staleness and session-row checks are **skipped with one log line**, because a watchdog that guesses the market's state is a false-alarm generator.
3. **Alerts are edge-triggered,** with state under `~/.local/state/ohcamel-watch`. Every alert goes to journald; when `/etc/ohcamel/ops.env` holds `SLACK_WEBHOOK_URL` it also goes to Slack. Optional healthchecks.io ping URLs live in the same file.

**Tests (hand-derived, one arithmetic case per threshold, all through shims):**
- **The 3-run restart:** unhealthy at runs 1 and 2 restarts nothing; run 3 restarts exactly once; run 4 while still unhealthy restarts nothing more (the state file records the restart).
- **The 180 s sync:** `clock.is_open` true with `last_sync` stamped 179 s back is silent; 181 s back alerts once; the same 181 s with `clock.is_open` false is silent; the same 181 s with `clock.source = "stale"` is silent and logs the skip.
- **The 30-minute session rule:** with `clock.next_close_date` = 2026-09-18 and its close at 20:00 UTC, no session row at 20:29 UTC is silent and at 20:31 UTC alerts.
- **The 26 h backup rule:** a newest backup stamped 25 h 59 m back is silent; 26 h 01 m back alerts.
- **The 80% disk rule:** `df` reporting 80% is silent (the rule is *over* 80); 81% alerts.
- Edge triggering: the first failure alerts once, a repeat is silent, recovery alerts once.

The pytest heartbeat test passes on a fake clock and a temp path; the research count is bumped and `make check-counts` passes. `systemd-analyze verify` passes on both units in the ubuntu `lint` job.

**Commit** as `deploy:` for the shell and units, `research:` for the heartbeat.

### Task 19: The operations documents and runbooks

**Files:** `docs/status.md` ("Operating it", the environment table, the limits), `README.md` ("Watching it"), `docs/overview.md` (the smoke suite), `deploy/deploy.env.example`, `deploy/live.env.example`, `docs/superpowers/specs/2026-08-31-server-side-deployment-design.md` (an addendum), `docs/superpowers/specs/2026-09-12-the-desk-design.md` (§8 gains rulings 11, 13 and 20).

**What must become true:**
1. **Runbooks:** deploy by sha; the `--build` fallback and that it builds **both** images; rollback, the additive-journal rule and what a `schema_version` bump would cost (restoring the pre-deploy backup loses the orders since); the pre-deploy and nightly backups; restore, the drill and the off-box pull; the watch and uptime checks, including which checks the watchdog skips when the clock is stale; credential rotation — which file holds each secret, the order (new key, edit `live.env`, `up -d --force-recreate`, confirm the first sync, then revoke the old key), and the cadence (on any exposure, otherwise every 180 days), with the note that `docker compose restart` does **not** re-read `env_file`; the 10:00 UTC reboot window; log retention, stating that the journal and not the logs is the audit record of orders and fills; capacity, filled in from owner step **O17**; disaster recovery with a recovery-time target and the exact droplet-rebuild and DNS-repoint steps; and the live host's basic-auth brute-force stance as an owner decision.
2. **One environment-variable table** listing every variable the code reads: `OHCAMEL_LOG_LEVEL`, `OHCAMEL_JOURNAL`, `OHCAMEL_SIGNALS_DIR`, `PEER_ORIGIN`, `SLACK_WEBHOOK_URL`, `OHCAMEL_TAG`, `OHCAMEL_IMAGE`, and the Alpaca and FRED names by name only.
3. **Stale lines fixed:** `/etc/ohcamel/live.env` is 0640 root:ohcamel, not "root-owned and 0600"; the ACME email is the account contact, because Let's Encrypt stopped sending expiry mail on 2025-06-04. `live.env.example` gains commented `SLACK_WEBHOOK_URL` and `OHCAMEL_LOG_LEVEL`. No document says `deploy.sh` builds, or says "pull, rebuild".
4. **An invariant-13 sentence:** a backup is a copy of the journal, written to the host, and the engine neither writes nor reads it.
5. **§8 gains the live kill-route gap:** the live smoke suite checks three of the four mutating routes for a headerless 403 and deliberately never POSTs `/api/desk/kill`, because a regression there would halt the live desk; the route's refusal is covered by the hermetic desk-route tests instead. Task 71 keeps that entry.

**Tests:** every `ohcamel <mode>` and every script path the runbooks name exists, and the grep list goes in the report. `grep -rn 'root-owned and 0600\|sends expiry warnings' deploy docs` is empty. Every `Sys.getenv` in `bin/`, `desk/` and `lib/` appears in the table; the reviewer checks by grep. `make check-counts` passes.

**Commit** as `docs:`.

### Task 20: Stage 1 close, and the first publish

**Files:** `lib/verified.ml`, `README.md`, `docs/status.md`, the ledger.

**What must become true:** a whole-branch review against §3.15 and rulings 11–14 and 20 and the operations survey's list; the fix wave; the shared-file reconciliation this stage's dependency note names; `make coverage` re-measured in a dedicated clone at the branch tip and re-dated; fast-forward `main` and push; the image workflow then publishes both images for the merge sha.

**Tests:** CI, `lint` and the image job are green on `main`; both GHCR packages list the merge sha; the harness smoke passed inside the image job. Owner steps **O10–O17** are announced with the exact sha.

**Commit** as `verified:`.

---

## Stage 2A — risk depth, first half: the long window and the estimators

Branch `desk/a4-long`, cut from `main`. A4's rulings 1–3, 7, 9, 11 and 12 bind. This stage exists as its own stage for one reason (ruling 1): the live host accrues `garch` and `cornish_fisher` forecast rows only after the stage that writes them deploys, so shipping it alone brings the first verdict forward by the length of Stage 2B.

### Task 21: The long window in the graph, the common rows, and the demo's synthetic panel

**Files:** `lib/graph.ml` (no `lib/graph.mli` exists and none is created — ruling 24), `lib/synthetic_book.ml`, `bin/main.ml` (the demo call only), `test/test_graph.ml`, `test/test_synthetic_book.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:**
```ocaml
val create : ... -> ?long_window:int -> ...      (* default 250; kept in t; fork passes it *)
val set_long_panel : t -> Long_panel.t -> unit
val long_as_of : t -> Date.t option
(* cells: returns_long[S], last_close_long[S], adv20[S], long_observations[S],
   factors_long, long_as_of, long_fit_weights, common_rows *)
val Synthetic_book.long_seed : int
val Synthetic_book.seed_long : graph:Graph.t -> unit
```

**What must become true:** rulings 1–3 and 9, exactly.
1. **The cells** exist per instrument and as singletons, each built with `make_var` and a **nan-aware** value cutoff — `Array.equal Float.equal` would call two identical nan-bearing arrays different. `fork` copies every cell and the `long_window` setting; `classify` lists the new prefixes and singletons; `unit_of` covers them.
2. **`common_rows` is built here, not in the factor-model task.** It is the listwise intersection of the frozen-weight names' non-`nan` rows — a panel-derived cell, not a fit — and putting it here is what lets Task 27 follow Task 21 directly and lets this stage ship without the factor model.
3. **No topology change,** because nothing reads the cells yet and `walk` starts from observers. A test asserts the pins are unmoved.
4. **`long_fit_weights`** is `qty × last_close_long` normalised by the gross of those products; a name without a last close gets 0.
5. **The demo.** `seed_long` runs after `seed_returns` on its own `Random.State.make [| long_seed |]`, builds 251 dates, draws the five factors as independent normals with daily standard deviations 0.010, 0.005, 0.004, 0.006 and 0.05 (the last in pp), and each instrument's return as Σₖ β_{S,k} fₖ + ε with ε ~ N(0, 0.012²). Market betas: AAPL 1.2, MSFT 1.1, NVDA 1.6, JPM 1.0, XOM 0.8, CVX 0.8; size, value and momentum loadings are small fixed numbers in the source; rates loadings equal the synthetic book's `rate_beta` per sector. Last closes are the book's marks; ADV comes from a fixed table (AAPL 60e6, MSFT 25e6, NVDA 45e6, JPM 10e6, XOM 15e6, CVX 8e6).

**Tests (hand-derived):** `set_long_panel` round-trips through the accessors; `fork` copies every cell; an absent instrument reads all-`nan`, `None` and count 0; writing the same nan-bearing panel twice recomputes nothing; `long_fit_weights` for 100 shares at 50.00 and −50 shares at 100.00 is gross 10,000 with weights **+0.5** and **−0.5**; `common_rows` on a three-name panel where one name is `nan` on exactly two dates has length 251 − 2 = **249**; `seed_long` is deterministic; `seed_returns`' 60-window returns are identical with and without `seed_long`; on a noise-free design an OLS in the test recovers `seed_long`'s betas to 1e-9; the pins "sixty-one" and "observed 28" are unchanged.

**Commit** as `graph:`.

### Task 22: `lib/feed/long_window.ml` — parse, build the URI, schedule (pure)

**Files:** Create `lib/feed/long_window.ml` and `test/test_long_window.ml`, registered in `test/test_ohcamel.ml`. Modify `lib/verified.ml` and the markers.

**Interfaces — produces:**
```ocaml
val bars_of_body : string -> (Long_panel.Bar.t list Symbol.Map.t * string option) Or_error.t
val build_uri : instruments:Symbol.t list -> window:int -> now:Time_ns.t ->
                page_token:string option -> Uri.t
val next_wakeup : now:Time_ns.t -> Time_ns.t
```

**What must become true:**
1. **`bars_of_body`** parses Alpaca's multi-symbol bars shape `{"bars":{"SPY":[{"t":"2026-09-17T04:00:00Z","o":…,"c":…,"v":…}]},"next_page_token":null}`, taking the date from `t`'s UTC date prefix (daily bars are stamped 04:00Z or 05:00Z). It refuses a non-finite or non-positive close, or a negative volume, naming the symbol and date, and passes Alpaca's own `{"message":…}` through. Confirm the field names from Alpaca's documentation and cite it in the header.
2. **`build_uri`** targets `https://data.alpaca.markets/v2/stocks/bars` with `timeframe=1Day`, `adjustment=all`, `feed=sip`, `limit=10000`, `symbols` = the instruments ∪ `Factor.etfs`, deduplicated and sorted, `start` = now − (2 × window + 60) days, `end` = now − 16 min, and the page token when present.
3. **`next_wakeup`** is pure over 00:20, 06:20, 12:20 and 18:20 UTC.
4. **The module talks only to `data.alpaca.markets`** and uses no `Desk_time`.
5. **The module header records, for Task 64,** the exact feed, adjustment and finality rule this request encodes, so the R8 comparison has one place to read.

**Tests (hand-derived):** a documented two-symbol, two-bar payload with a page token parses and concatenates in order; each named refusal; the `{"message":…}` passthrough; `build_uri` at `now` = 2026-09-18T20:00:00Z gives `end` = 2026-09-18T19:44:00Z and contains `feed=sip` and `adjustment=all` and no trading host; `next_wakeup` at 00:19:59 is 00:20:00 the same day, and at both 00:20:00 and 00:20:01 it is 06:20:00. The invariant grep is silent.

**Commit** as `kernel:`.

### Task 23: The live refresh, and `/api/ops`' long-window block

**Files:** `lib/feed/long_window.ml` (`fetch`, `run`), `lib/feed/feed_source.ml`, `bin/main.ml`, `test/test_long_window.ml` or `test/desk_async/test_desk_async.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:**
```ocaml
val fetch : credentials:… -> instruments:Symbol.t list -> window:int -> now:Time_ns.t ->
            (Long_panel.t, string) Result.t Deferred.t
val run : credentials:… -> instruments:Symbol.t list -> window:int ->
          on_panel:(Long_panel.t -> unit) -> on_error:(string -> unit) -> unit Deferred.t
```
Check the real name of `Config`'s credentials type in `lib/config.ml`.

**What must become true:**
1. **`fetch`** pages by `next_page_token`, reads DGS10 through `Fred_client` with `sort_order=desc`, and reports errors with `redacted_uri`. Every request runs under `Clock_ns.with_timeout` 30 s with `~interrupt`.
2. **`run`** fetches once at once and then on `next_wakeup`'s schedule; a failure keeps the previous panel; **startup never awaits it**.
3. **The live `on_panel`** calls `Graph.set_long_panel`. After the desk's first applied sync, `bin/main.ml` calls `set_long_panel` again with the last panel (ruling 7), or the frozen weights would come from the book file for up to six hours.
4. **`/api/ops` gains a `long_window` block** — `as_of`, `observations_by_instrument`, the last refresh time, `last_error` — straight in `json_of_ops`, because the long window is a kernel feed.

**Tests:** a scheduler test where a fetch hangs times out and the request is interrupted; startup completes while the fetch is pending; `/api/ops` carries the block with `as_of` null before any panel and the values after one. The invariant grep is silent.

**Commit** as `kernel:`.

### Task 27: GARCH and Cornish–Fisher wired on the long window

**Files:** `lib/graph.ml`, `lib/server.ml`, `test/test_graph.ml`, `test/test_server.ml` (the `test_round_trips` required-key list — ruling 25), `test/test_long_estimators.ml` (new), `lib/verified.ml` and the markers.

**Interfaces — produces:** cell `garch_params_long`; nodes `portfolio_returns_long`, `garch_var`, `garch_var_notional`, `cornish_fisher_var`, `cornish_fisher_var_notional`, `cornish_fisher_moments`; snapshot fields `garch_params` and `long_estimator_errors`; `Graph.For_testing.fits_run`.

**What must become true:** ruling 7, exactly. `set_long_panel` fits `Garch11` at **frozen** weights over Task 21's `common_rows` (at least 240) into a cell, incrementing `fits_run` once per call and **catching** any raise into an `Error` cell, because a node body may not raise. The forecast is recomputed at **current** weights through `forecast_stddev`, and the second-order difference from variance targeting is stated. Cornish–Fisher is computed at current weights with zero mean. Any held name outside the common rows makes every one of these `None`, **naming the instrument**; `nan` never reaches a figure or the journal. `fits_run` is introduced here, because this is the stage's first fit.

**Tests (hand-derived):** the graph's `cornish_fisher_var` equals `Risk_metrics.cornish_fisher_var` on the same rows to 1e-9; the graph's `garch_var` equals `−z × Garch11.forecast_stddev × gross` on the same series to 1e-9; below 240 common rows both are `None` with the count; a name bought after the freeze gives `None` with the name; a tick changes `garch_var` while `fits_run` stays 0, including across a six-leg `Gate.check`, a fill and a fork; `fits_run` rises by exactly 1 per `set_long_panel`; every new scalar key is in `test_round_trips`' required list. Pins listed in the commit.

**Commit** as `graph:`.

### Task 28: Every "GARCH is not wired" becomes true, and the page shows it

**Files:** `lib/vol_estimators.ml` (~225, ~333), `lib/garch_study.ml` (~7), `lib/reports.ml`, `bin/main.ml` (~662, ~671, ~690, ~736, and the usage line ~1896), `Makefile` (~136), `web/graph.js` (the ghost node ~190 and the legend ~780), `web/risk.html`, `web/risk.js` (a first `estimators` section), `web/argument.html` (~110), `web/quoted.json` (the garch block, re-captured), `README.md` (~617, ~1675), `docs/status.md` (~140, ~393), `docs/quant_notes.md` (~168), `docs/overview.md`, `test/test_embedded_assets.ml`.

**What must become true:**
1. Every claim says GARCH is wired, on the 250-observation window, beside `make garch`'s own finding, and says why the 60-observation window is not used for it. The ghost node and its legend entry are removed.
2. **The Risk page gains its `estimators` section now,** not two stages later: GARCH and Cornish–Fisher beside parametric, with n, the rows dropped, and `make garch`'s n = 250 row read from `/api/reports/garch`. Shipping the prose that says "wired" while the page shows nothing would be a new half-truth. Task 30 adds the factor-model and liquidity sections above it and does not rewrite this one.

**Tests:** `grep -rniE 'not wired' lib bin web docs README.md Makefile` finds no GARCH claim. `make garch`'s table rows are byte-identical to `$TMPDIR/final-baseline/garch.txt`, produced in a dedicated clone at this branch's tip; only the wiring sentence differs, and the diff is attached. The embedded-asset pins and the marker-order pin are updated. A Playwright check on a local `ohcamel demo` at 1280 px and 380 px in both colour schemes shows the estimators section filled with no horizontal scroll.

**Commit** as `docs:` for the prose and `web:` for the page; two commits is fine.

### Task 29: The forecast rows A5 reads

**Files:** `desk/session_close.ml`, `bin/main.ml` (the live `on_panel` callback), `test/test_session_close.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** `Session_close.record_long_forecasts ~journal ~date ~snapshot`.

**What must become true:** ruling 11, exactly. A refresh advancing `long_as_of` to a date *d* writes the `garch` and `cornish_fisher` rows for *d*, but only when *d* already has a session row and no `garch` row, and only from a **fork**: fork the graph; set every quantity and price from `Journal.marks` for *d*; `set_long_panel` on the fork; snapshot; destroy the fork. Only figures that are `Some` are written. It is an `Error` when *d* has no session row or no marks, and a no-op when the row exists. The existing roll still writes exactly its three rows, and the journal needs no schema change.

This is the task that starts the clock: once Stage 2A deploys at owner step **O18a**, `garch` and `cornish_fisher` begin accruing scored sessions, and their first verdict prints 60 scored sessions later.

**Tests (hand-derived):** a panel for a recorded session writes 2 rows, so that date holds 5; a **late** panel — one arriving after the live book has moved on — writes rows byte-identical to an on-time one, with the test building both; a date with no session writes nothing; a second call is a no-op; a `None` figure writes no row; the live book's cells are asserted untouched.

**Commit** as `desk:`.

### Task 33A: Stage 2A close

**Files:** `lib/verified.ml`, `README.md`, `docs/status.md`, the ledger.

**What must become true:** a whole-branch review against §3.13's long-window and estimator clauses and A4's rulings 1–3, 7, 9, 11 and 12, with each ruling checked off in the ledger; the fix wave; `make coverage` in a dedicated clone at the branch tip; fast-forward `main`, push, and confirm the image job publishes. Owner step **O18a** follows and is flagged in the report as time-critical, because it is what starts the 60-session clock.

**Tests:** CI, `lint` and the image job are green on `main`; the harness smoke passes for the merge sha; the six-mode gate diffs clean against `$TMPDIR/final-baseline/` bar the `make garch` wiring sentence.

**Commit** as `verified:`.

---

## Stage 2B — risk depth, second half: the factor model, liquidity and the A4 pages

Branch `desk/a4-depth`, recreated from `main` after Stage 2A. A4's rulings 4–6, 8, 10 and 12 bind, with amendment 5.

### Task 24: The factor model in the graph, with fits as cells

**Files:** `lib/graph.ml`, `lib/server.ml`, `test/test_graph.ml`, `test/test_server.ml` (the required-key list — ruling 25), `test/test_factor_model_graph.ml` (new), `test/test_scaling_probe.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** cells `factor_fit_long[S]`, `factor_cov_long`, `asset_cov_long`; nodes `factor_risk` and `factor_exposure:<f>`. `common_rows` and `fits_run` already exist, from Stage 2A.

**What must become true:**
1. **`set_long_panel` runs every fit outside stabilization** — each instrument's OLS, the factor covariance and the asset covariance — into cells, incrementing `fits_run` once per call, and **catching** `factor_covariance`'s `invalid_arg` and any other raise into an `Error` cell, because a node body may not raise.
2. **`factor_risk` (on the tick path) and `factor_exposure:<f>` read only cells,** and come with their snapshot fields, `by_node` entries, `test_round_trips` keys, labels, units and cost classes.
3. **Pins move deliberately** and the commit names each: labels, the observed count, the fork's `+N`, the scaling band.

**Tests (hand-derived):** on a Walsh–Hadamard panel (T = 256) with every α and β known exactly, exposures equal Σβx and contributions **sum to the systematic variance to 1e-9**, and **systematic plus idiosyncratic equals the model total to 1e-9**; market betas 1.2 and 0.8 on exposures 100,000 and 50,000 give b = **160,000**; fewer than 120 observations puts the name in `factor_fit_errors`; **the amended 0.01 conditioning floor is exercised at graph level** — a panel whose standardised design has σ_min ÷ σ_max = 0.009 by construction (two columns made near-collinear by a hand-set 0.9999 correlation, with the ratio asserted in the test before the fit) puts the name in `factor_fit_errors` naming conditioning, while a panel at 0.011 fits; a name bought after the freeze makes `factor_risk` `None`, naming it; a raising covariance yields `None` with a reason, not an exception; `fits_run` rises by exactly 1 per `set_long_panel` and stays **0** across a tick, a fill, a fork and a six-leg `Gate.check`.

**Commit** as `graph:`.

### Task 25: The `Factor_exposure` limit kind

**Files:** `lib/types.ml`, `lib/limits.ml`, `lib/graph.ml` (`breach_node`, `limit_kind_to_string`), `lib/config.ml`, `book.example.sexp`, `test/test_properties.ml`, `test/test_gate.ml`, `test/test_example_book.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** `Types.Limit.kind` gains `Factor_exposure of Factor.t * Notional.t`; book syntax `(kind (Factor_exposure (market 2000000.0)))`.

**What must become true:** the kind is Portfolio scope only with a positive threshold, the factor name validated by `Factor.of_string`, and is handled in every exhaustive match — `Limits.threshold`, `unit_of`, `render_value`, `scope_is_valid`, `breach_node` (which reads `factor_exposure:<f>`, `None` meaning unevaluable) and `limit_kind_to_string`.

**Tests (hand-derived):** a config round trip; a Sector scope is refused; b = 160,000 breaches a threshold of 150,000 and does not breach 200,000; `b = None` is unevaluable; the gate refuses on a fork the fill that pushes the market exposure past its cap, naming the limit; the qcheck generators include the kind and the property "the gate never passes a created breach" still holds; `book.example.sexp` carries a commented example that `check-book` accepts.

**Commit** as `graph:`.

### Task 26: Liquidity in the graph, and the book's liquidity block

**Files:** `lib/graph.ml`, `lib/config.ml`, `lib/server.ml`, `test/test_server.ml` (the required-key list), `bin/main.ml`, `book.example.sexp`, `test/test_graph.ml`, `test/test_liquidity_graph.ml` (new), `test/test_example_book.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** `Graph.create` gains `?liquidity` and `?half_spread_bps`; nodes `daily_stddev_long:S`, `liquidity`, `lvar_notional`; book block `(liquidity ((participation 0.10) (impact_coefficient 1.0)))`.

**What must become true:** `create` validates the pair (participation in (0, 1], impact ≥ 0) because node bodies may not raise; `fork` passes both on; `daily_stddev_long` has **no price upstream**; `bin/main.ml` passes the desk block's spreads; the defaults are 0.10 and 1.0.

**Tests (hand-derived):** the single-position case from Task 3 is reproduced to 1e-9; with `var_notional` 10,000 and 1,000,000 of gross at 5 bps, the spread cost is 1,000,000 × 5e-4 = **500** and `lvar_notional` is **10,500**; at zero spreads **LVaR = VaR** exactly; a position with no ADV makes the totals `None`; a fork's LVaR equals its parent's; an invalid liquidity pair is refused at `create`; the prices-never-reach-a-fit pin is extended to `daily_stddev_long`; a config round trip. Pins listed in the commit.

**Commit** as `graph:`.

### Task 30: The Risk page — factor model and liquidity

**Files:** `web/risk.html` (the deferral comment at ~15–17 removed), `web/risk.js`, `web/page.css` (only if a new class is needed), `test/test_embedded_assets.ml`, `test/test_server.ml`.

**What must become true:** two sections after `greeks` and **above** Task 28's `estimators` section — (1) the factor model: exposures, the Euler split, idiosyncratic variance, the model VaR beside xᵀΣx, and the uncorrelated-residuals sentence; (2) liquidity: ADV, days to liquidate, impact, LVaR beside VaR, and the sentence naming what LVaR leaves out. The `estimators` section is enriched only with the rows-dropped counts the factor work makes available, and is not rewritten. Text enters only through `F.el` and `textContent`; units convert in one named function; the demo is labelled synthetic.

**Tests:** the marker-order pin gains the two ids in the right order relative to `estimators`; a grep finds no `innerHTML` with data in `web/risk.js`; a Playwright check on a local `ohcamel demo` at 1280 px and 380 px in both colour schemes shows all three sections filled with no horizontal scroll, with the notes in the report.

**Commit** as `web:`.

### Task 31: The published figures, re-measured, with a band; Figure 1 draws A4's nodes

**Files:** `lib/scaling_probe.ml`, `bench/bench_graph.ml`, `test/test_scaling_probe.ml`, `test/test_embedded_assets.ml` (the node count at ~313), `README.md` (the scaling and bench tables), `docs/status.md` (~336–349), `web/argument.js` (~81, today hard-coding 25.6 / 25.2 / 26.0), `web/quoted.json`, `web/graph.js` (a cost class and a placement for each A4 node).

**What must become true:**
1. The scaling probe and `make bench` seed a deterministic synthetic long panel for every name, so they measure A4's tick-path cost; `make run` and `make bench` are re-run in a dedicated clone at this branch's tip and the prose, the pins and `quoted.json` updated; each A4 node has a cost class and a place in Figure 1, which is a deliberate topology change on the demo (ruling 9). The demo declares no options, so the options nodes do not exist here.
2. **A per-tick band is set, and a regression is a stop-and-report.** The baseline per-tick figure is `$TMPDIR/final-baseline/run-scaling.txt`'s. A4 adds nodes to the tick path, so some increase is expected and the point of the re-measure is to say how much. The new figure is pinned in `test_scaling_probe.ml` as a band of ±15% around the measured value, and the task **stops and reports** if the measured per-tick cost exceeds **1.30 ×** the baseline's — a doubling must not pass, and the old rule, which covered only the five tables, would have let it. The band, the measured value, the baseline value and the ratio go in the report and in the ledger.

**Tests:** `make backtest`, `make backtest-crisis`, `make garch`, `make stress` and `make options`, run in a dedicated clone at this branch's tip, diff byte-identical against `$TMPDIR/final-baseline/*.txt` — the only permitted difference being `make garch`'s wiring sentence — with the diffs attached; **any other difference means stop and report**. The per-tick ratio is at or below 1.30 and the band matches the new measurement. Figure 1 is checked in a browser at 1280 px and 380 px with no overlap, and the screenshots go in the report.

**Commit** as `docs:` for the prose and `test:` for the pins.

### Task 32: The A4 documents

**Files:** `README.md` (a "Risk depth" section and the limits), `docs/quant_notes.md`, `docs/status.md`, `docs/overview.md`, `book.example.sexp` (comments only), `lib/verified.ml` (counts only).

**What must become true:**
1. **`README.md` gains "Risk depth"** after the attribution material and before stress: the long window and why it is separate; the calendar rule; the factor model with a hand example copied from its tests; liquidity with its example; GARCH on 250 beside its own finding; Cornish–Fisher with its example and the validity check.
2. **`docs/quant_notes.md` gains** OLS by QR with its Euler split, Tóth impact, Bangia LVaR with the spread term at 0, Cornish–Fisher with its closed-form monotonicity condition, and GARCH on 250 — each citing its function.
3. **The limits gain:** SIP volume in the liquidity view against IEX volume in the order rule (ruling 10); the factor ETFs fetched over REST and never streamed; the long window's own refresh schedule with only final bars; uncorrelated residuals; Bangia's spread-volatility term at 0; GARCH treating the common rows as consecutive. `docs/status.md`'s "one macro factor" becomes five factors.
4. **`docs/status.md`** records the A4 acceptance row as met, naming the tests, and adds "How it got here" rows for Stage 2A and Stage 2B with `(merged at <sha>, coverage re-measured)`.
5. **`book.example.sexp`'s comments** list every limit kind, including `Component_var` — parseable today and undocumented — and `Factor_exposure`, and the `liquidity` block.

**Tests:** every formula cites a function that exists; `check-book` accepts `book.example.sexp`; `make check-counts` passes.

**Commit** as `docs:`.

### Task 33B: Stage 2B close

**Files:** `lib/verified.ml`, `README.md`, `docs/status.md`, `deploy/smoke.sh` (`EXPECTED_ROUTES`, if a route changed), the ledger.

**What must become true:** a whole-branch review against §3.13's remaining clauses and A4's rulings 4–6, 8, 10 and 12, with each ruling checked off; the fix wave; `make coverage` in a dedicated clone; fast-forward `main`, push, and confirm the image job publishes. Owner step **O18b** follows.

**Tests:** CI, `lint` and the image job are green on `main`; the harness smoke passes for the merge sha.

**Commit** as `verified:`.

---

## Stage 3 — Correctness: every merged feature fully wired and honest

Branch `final/s3-correctness`. Ordering is the serial spine named in the dependency section, because the four "lanes" an earlier draft declared collide on `bin/main.ml`, `web/execution.js`, `desk/sim_venue.ml`, `test/test_embedded_assets.ml` and `lib/verified.ml`. Only Tasks 42 and 43 run outside the spine, and 43 follows 42.

### Task 34: The kernel writes no file — the `File` alert sink retired

**Files:** `lib/alerts.ml`, `lib/config.ml`, `book.example.sexp`, `README.md` (the alerts section), `test/test_alerts.ml`, `.github/workflows/ci.yml` (the invariant grep), `lib/verified.ml` and the markers.

**What must become true:** ruling 3. The `File` sink and its `Writer.with_file` leave `lib/`. `Config` refuses `(File "…")` with the sentence *the File sink is retired: the desk journals every alert (see /api/desk recent_alerts); remove it from the book*, which `check-book` and `deploy.sh` surface before any restart. `lib/alerts.ml` gains a generic observer callback that names no desk, for Task 35. CI's invariant step also refuses `Writer.with_file` under `lib/`.

**Tests:** a book with `(File "x")` is an `Error` carrying that sentence; the callback fires on raise, clear and trip; `grep -rn 'Writer.with_file' lib/` is empty and CI enforces it; the six-mode gate is unchanged against the baseline.

**Commit** as `kernel:`.

### Task 35: Alerts journaled on both hosts, and shown

**Files:** `desk/alert_log.ml` (new), `bin/main.ml`, `desk/desk.ml`, `desk/desk_routes.ml`, `web/execution.html`, `web/execution.js`, `test/test_alert_log.ml` (new), `test/test_embedded_assets.ml`, `lib/verified.ml` and the markers.

**What must become true:** a desk-side observer of the Alerts callback and of Halt events calls `Journal.record_alert` for every event — `raised`, `cleared`, `tripped`, `halted` and `reset` — with the limit's name and the line. Because the `alerts` table's `limit_name` and `line` are `NOT NULL` (`desk/journal.ml:183`) and a hand halt has no limit, ruling 2's convention applies: a halt row is `kind='halted'`, `limit_name='kill_switch'`, `line=<the reason>`; a reset row is `kind='reset'`, `limit_name='kill_switch'`, `line='hand reset'`. That is a convention, not a schema change, and Task 36's restore query depends on it. It is installed on **both** hosts. `/api/desk` carries `recent_alerts`, newest first, at most 50, and the Execution page renders them as text. Today `Journal.record_alert` and `recent_alerts` have no caller outside a test, so §3.5's `alerts` table is never written.

**Tests (hand-derived):** a trip writes a row of kind `tripped` naming the limit; a clear writes `cleared` and a raise `raised`; a hand halt writes `halted` with `limit_name='kill_switch'` and the reason in `line`, and a reset writes `reset` with `'hand reset'`; a round trip through `Journal` returns every row unchanged, so the journal's identity property still holds with these rows present; `/api/desk` lists them newest first; a page marker is pinned; `Journal.record_alert` has a caller outside `test/` (grep); `lib/` is unchanged by name.

**Commit** as `desk:`.

### Task 36: A halt survives a restart on the live host

**Files:** `desk/halt.ml`, `desk/halt.mli`, `desk/journal.ml`, `desk/journal.mli` (a newest-halt-state query), `bin/main.ml`, `web/stream.js` (~84), `test/test_halt.ml`, `lib/verified.ml` and the markers.

**What must become true:** at live startup, the newest row matching `kind IN ('halted','reset') AND limit_name='kill_switch'` decides: a `halted` row with no later `reset` starts the switch tripped with the original reason and the log says `restored halt from <at>`. A trip on a limit (`kind='tripped'`) also restores, matched on its own limit name. The demo's 90 s auto-reset is unchanged. `halt.mli`'s and `stream.js`'s "until the engine is restarted" sentences are corrected, because compose restarts `unless-stopped` and a silent lift is the opposite of a kill switch.

**Tests:** on a temp-file journal — trip, then a new `Halt` from the same journal: tripped with the same reason; trip then reset, then a new `Halt`: not tripped; a `raised` row alone does not trip; the existing demo auto-reset test passes unchanged.

**Commit** as `desk:`.

### Task 37: `/api/ops` states the desk in words (§8 item 5)

**Files:** `lib/server.ml` (`?ops_extra`), `bin/main.ml`, `desk/desk.ml`, `web/ops.html`, `web/ops.js`, `test/test_server.ml`, `test/test_desk.ml`, `test/test_embedded_assets.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** `Server.create ?ops_extra:(unit -> (string * Yojson.Safe.t) list)`, merged into `json_of_ops`, refusing at `create` a key that duplicates a core key.

**What must become true:** ruling 14. The desk block carries the venue; trading on or off with its reason in words — *the book does not enable trading*, *the paper host refused the trading key: <status>*, *kill switch tripped*; the journal's kind, path, schema version and size; `last_sync` and `last_error`; the intake's state and strategy count; and Task 13a's `clock` object, by the same constructor `/api/desk` uses. `/ops` renders it as text only. `lib/` names nothing of the desk.

**Tests:** the extra keys appear in `/api/ops` and a duplicate key is rejected at `create`; a sim venue that refuses the key shows trading disabled with the exact sentence on both `/api/ops` and `/api/desk`; the `clock` object on `/api/ops` equals the one on `/api/desk`, asserted by equality; the `/ops` marker pin is updated; the invariant grep is silent.

**Commit** as `desk:`.

### Task 40: TCA per order

**Files:** `desk/tca.ml` (there is no `desk/tca.mli` and none is created — ruling 24), `desk/oms.ml` (the TCA JSON), `web/execution.js`, `test/test_tca.ml`, `lib/verified.ml` and the markers.

**What must become true:** ruling 15. An order-level roll-up: the quantity-weighted average fill price per order, with shortfall, delay, arrival slippage, quoted half-spread and versus-model recomputed per order using `tca.ml`'s existing formulas. `Summary.t` gains count, mean, median and quantity-weighted mean for **every** metric, overall and by symbol; today only shortfall has more than a mean, and delay, slippage and half-spread have no aggregate at all. Per-fill rows stay on the wire, and the page shows the per-order table beside the aggregates.

**Tests (hand-derived):** a buy with arrival mid 10.00 filling 100 at 10.00 and 300 at 10.04 has VWAP (100 × 10.00 + 300 × 10.04) / 400 = **10.03** and a per-order shortfall of **30 bps**, while the per-fill figures are 0 and 40 bps whose quantity-weighted mean is also 30; three orders with shortfalls 10, 20 and 60 bps on quantities 100, 100 and 200 give mean **30**, median **20** and quantity-weighted mean (10 × 100 + 20 × 100 + 60 × 200) / 400 = **37.5**; the by-symbol split is checked; the page marker is updated.

**Commit** as `desk:`.

### Task 38: Demo session dates say they are synthetic

**Files:** `web/execution.js`, `web/desk.js`, `test/test_embedded_assets.ml`.

**What must become true:** when `/api/desk` reports the venue as `simulated`, the Execution sessions table and the Desk panel both read *synthetic session N — dated on a synthetic calendar from 2026-01-01*. Live rendering is unchanged. Today the demo's dates start at 2026-01-01 and look real, and neither surface labels them.

**Tests:** a page-marker test covers both labels; Playwright on a local demo shows the label in both places in both colour schemes at 380 px; a live-mode fixture renders without it.

**Commit** as `web:`.

### Task 39: Navigation in the spec's order

**Files:** `web/nav.html`, `test/test_embedded_assets.ml`, `deploy/smoke.sh` (only if the page probes are order-sensitive).

**What must become true:** ruling 18. The order becomes Desk, Risk, Research, Execution, Argument, Ops, and the A3 comment explaining the old order is removed.

**Tests:** the nav pin test asserts the new order; a local demo shows it; the harness smoke passes.

**Commit** as `web:`.

### Task 41: The demo's in-memory journal is bounded; the live journal is never pruned

**Files:** `desk/journal.ml`, `desk/journal.mli`, `bin/main.ml` (the demo only), `test/test_journal.ml`, `lib/verified.ml` and the markers.

**What must become true:** `Journal.prune_memory` keeps the newest 300 sessions with their marks and forecasts and drops orders, events and fills older than the oldest kept session's date. It returns an `Error` unless the journal was opened as `:memory:`, so the live journal can never be pruned. The demo calls it every 20 sessions. Today the demo's `:memory:` journal grows without bound — 288 sessions a day, a proposal every 45 s, forever.

**Tests (hand-derived):** a `:memory:` journal with 305 sessions keeps exactly 300 and drops the orders dated before the cut, with the counts derived in the test; a temp-file journal returns an `Error` and keeps every row; at least 251 sessions always remain, which is what the validation window needs.

**Commit** as `desk:`.

### Task 44: The venue's calendar

**Files:** `desk/venue.ml` (`Read.calendar`), `desk/alpaca_paper.ml` (a pure parser over `GET /v2/calendar`), `desk/sim_venue.ml`, `desk/desk_time.ml`, `test/test_alpaca_paper.ml`, `test/test_sim_venue.ml`, `docs/status.md` (the market-on-open holiday departure removed), `lib/verified.ml` and the markers.

**Interfaces — produces:** `Read.calendar : start:Date.t -> end_:Date.t -> (day list, string) Result.t Deferred.t`, each day carrying its open and close times. Confirm the field names from Alpaca's documentation.

**What must become true:** the market-on-open window targets the **next trading day's** open, so a weekday holiday is no longer treated as an open — today the window fails closed on a holiday before 19:00 ET, which is a recorded departure this task removes. `Sim_venue` reports synthetic weekdays. A5's pairing (Task 55) reads the same calendar.

**Tests (hand-derived):** the parser on a documented payload has no 2026-11-26 (Thanksgiving, a Thursday) and closes 2026-11-27 (a Friday) at 13:00 ET; a market-on-open window evaluated on the evening of Wednesday 2026-11-25 targets Friday 2026-11-27's open; the sim venue reports weekdays; the status departure line is gone.

**Commit** as `desk:`.

### Task 45: The demo's labelled synthetic strategy, through the real intake

**Files:** `desk/demo_signals.ml` and `.mli` (new), `desk/contract.ml`, `desk/intake.ml` (it takes the venue kind), `bin/main.ml` (the demo: a tmpfs signals directory and a signals block in the in-code demo book), `web/research.js`, `test/test_intake.ml`, `test/test_contract.ml`, `test/test_embedded_assets.ml`, `lib/verified.ml` and the markers.

**What must become true:** ruling 9. Each synthetic session a writer emits a contract-v1 signal file for the strategy `demo_synthetic`, with targets drawn from its own RNG and a validation manifest under the `synthetic:` scheme. R6 accepts a `synthetic:` manifest **only** when the venue is simulated; the live intake refuses it with a reason naming R6. `/api/research` and the Research page label every cell of the strategy *synthetic*. Figure 1's signals band — drawn only where an intake runs, so never on the demo today — now draws publicly, which is what §0's "every arc is visible on the public demo" asks for.

**Tests:** the live-mode intake refuses a `synthetic:` manifest under R6, naming it; the demo-mode intake accepts it; `/api/research` on the demo lists `demo_synthetic` with *synthetic* in its label; Playwright on a local demo shows the signals band.

**Commit** as `desk:`.

### Task 46: The demo's labelled synthetic rebalancer (§8 item 6)

**Files:** `desk/sim_venue.ml`, `bin/main.ml` (the demo only), `test/test_sim_venue.ml`, `test/test_rebalance.ml`, `lib/verified.ml` and the markers.

**What must become true:** an accepted `demo_synthetic` signal is sized by `Rebalance` into whole-share legs; every non-empty subset of those legs is gated as a unit; the legs are sent as opening (`Opg`) orders, which the sim venue fills at the first mark of the next synthetic session plus or minus the half-spread. Some targets are allowed to breach a demo limit, so the gate's refusal and the kill switch's 90 s reset still show publicly. The ticket-style demo trader stays, because it drives the kill-switch demonstration. This task follows Task 44, which also edits `desk/sim_venue.ml`.

**Tests (hand-derived):** an `Opg` buy of 10 shares with a next-session first mark of 100.00 at 5 bps fills at **100.05**; a scripted multi-session test sees at least one rebalance filled and at least one refused, naming its limit; the existing demo halt tests are unchanged.

**Commit** as `desk:`.

### Task 42: A liveness watchdog on the trade-updates stream

**Files:** `desk/trade_updates.ml`, `test/desk_async/test_desk_async.ml`, `lib/verified.ml` (`scheduler_tests`) and the markers.

**What must become true:** while the venue clock says the market is open, the watchdog sends a websocket ping every 60 s; if no pong or other frame arrives within 30 s it closes the stream, reconnects and runs the existing reconcile, logging one line. It does nothing while the market is closed. It checks for a missing pong to its own ping, never for silence, because the trading stream is legitimately quiet when there are no orders.

**Tests:** on a virtual clock — market open with the pong withheld: exactly one reconnect and one reconcile; market open with pongs arriving: none; market closed and silent: none.

**Commit** as `desk:`.

### Task 43: The async bodies of the mutating routes are tested

**Files:** `test/desk_async/test_desk_async.ml`, `test/test_oms*.ml`, `README.md` (the "not yet done there" sentences at ~1297–1330), `lib/verified.ml` and the markers. Follows Task 42, which also edits `test/desk_async/test_desk_async.ml`.

**What must become true:** new cases against the sim venue cover a well-formed ticket, cancel and kill body passing `Protection` into the sequencer; malformed bodies answering 400 with `guard_parse`'s message; `guard_async`, `parse_cancel_id` and `parse_kill_reason`; `oms` `resolve`'s lookup-error fallback; a fill after a failed order recorded as an anomaly; `propose`'s re-checks; one iteration of `refresh_forever`; and `fills_json`.

**Tests:** each case asserts the journal rows and the venue calls; the coverage of `desk_routes.ml` and `oms.ml` before and after is reported; the README no longer calls these untested.

**Commit** as `test:`.

### Task 47: Dead code, the carried minors, and `desk/oms.mli`

**Files:** `desk/oms.mli` (new), `desk/contract.ml` and `.mli`, `desk/journal.ml` and `.mli`, `lib/server.ml`, `bin/main.ml` (~780–786, ~902), `lib/validation_report.ml` (~182–190), `lib/options_walk.ml` (~165, ~203), `lib/scaling_probe.ml` (~106, ~120), `test/test_ohcamel.ml` (~92–95), `Makefile`, `.superpowers/sdd/carried.md`, `docs/status.md` (limits), `lib/verified.ml` and the markers.

**What must become true:**
1. **`desk/oms.mli`** exports no submit path except behind the `Wire` permit, so "one submit site" becomes a compile error rather than a test. It is the only new interface for an existing module in this plan (ruling 24).
2. **Dead code goes:** `Contract.verdict_to_string` is deleted; the test-only helpers (`Contract.parse_string`, `Contract.default_universe`, `Journal.signal_judged`, `Journal.signal_file_error`, `Server.pending_frame`, `Server.route_paths`, `Server.subscriber_count`) move under a `For_testing` module or are documented as test hooks. `Graph.option_position` and `Graph.set_rate` are left for Task 49, which uses or deletes them.
3. **The six carried Phase-3 minors** are closed: the `Option.value_map ~default:0` on a `None` burst becomes loud; `validation_report.row`'s `[@warning "-16"]` becomes an honest `~burst_span:int option`; `options_walk`'s inline `~confidence:0.95 ~return_window:10` become named lets; `scaling_probe`'s `Time.now` alias becomes `Time_ns`; `test_ohcamel`'s hard-coded `1 +` is derived or keeps its comment; the zero-vega filter comment is made accurate.
4. **Every other carried item** — the broadcaster pushback stall, and the A3 desk minors including the failed DELETE that is not journaled — is fixed with a test or stated as a limit with its reason. A table in the ledger lists each with its disposition and leaves none open.
5. **`Makefile`'s `.PHONY`** gains `run-live`, `serve` and `demo`; the stale `contract.ml` comment saying a later phase persists the registry is corrected, because the intake already derives it from the journal.

**Tests:** the build shows no warnings; the six-mode gate prints its ok lines and diffs clean against the baseline; `verdict_to_string` is gone; every remaining exported function has a production caller or a `For_testing` home, with the grep list in the report; the compiler proves nothing outside `desk/oms.ml` can call the venue's submit.

**Commit** as `desk:`.

### Task 48: Stage 3 close

**Files:** `lib/verified.ml`, `README.md`, `docs/status.md`, `deploy/smoke.sh` (`EXPECTED_ROUTES`, if routes changed), the ledger.

**What must become true:** a whole-branch review against rulings 2, 3, 9, 14, 15, 18 and 20 and the gap items this stage closes; the fix wave; **the shared-file reconciliation** — `lib/verified.ml` and the three markers, `test/test_embedded_assets.ml` (35, 37, 38, 39, 45), `web/execution.js` (35, 38, 40), `desk/sim_venue.ml` (44, 46), `bin/main.ml` (34, 35, 36, 37, 41, 45, 46, 47), `test/desk_async/test_desk_async.ml` (42, 43), `README.md` and `docs/status.md` — each checked by rebuilding and rerunning the suite after the last merge into the branch; `make coverage` in a dedicated clone; fast-forward `main`, push, publish. Owner step **O19** follows.

**Tests:** CI, `lint` and the image job are green on `main`; the harness smoke passes for the merge sha.

**Commit** as `verified:`.

---

## Stage 4, options lane — A4's options clause

Branch `desk/a4-options`, from `main` after Stage 3. **This lane merges first** (ruling 26 and the dependency note), because it owns `bin/main.ml`, `test/test_server.ml` and `test/test_embedded_assets.ml` until it does.

### Task 49: Options declared in the book; Greek limits that parse; unmarked is unevaluable

**Files:** `lib/config.ml` (an `Option_spec` list; `Greek_limit` in `Limit_spec`), `lib/graph.ml` (`marked[o]`, the Greek breach rule, `set_rate`, `option_position`), `lib/limits.ml`, `lib/server.ml` (`option_greeks`), `test/test_server.ml` (the required-key list — ruling 25), `bin/main.ml` (the startup line, the daily `valuation_days` timer), `book.example.sexp`, `test/test_options_graph.ml`, `test/test_example_book.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:** book blocks `(options (…))` with OCC ids, and `(kind (Greek_limit (gamma 400.0)))`. `Types.Limit` already has `Greek_limit`; today no book can declare one.

**What must become true:** ruling 8. The OCC id is cross-checked against every declared field and any disagreement is named; ids are unique and the underlying is in the universe; an expired contract is excluded with a startup warning and does not block startup. The graph gains `marked[o]`, and a Greek limit is `None` — unevaluable — while any contract in its scope is unmarked. The snapshot carries per-contract engine Greeks; `valuation_days` advances daily. `Graph.set_rate` and `Graph.option_position` gain a use here (the book's rate, the per-contract lookup) or are deleted. The demo declares no options, so the demo's topology and Figure 1 node count are unchanged — assert it.

**Tests (hand-derived):** `SPY261218C00600000` parses to SPY, 2026-12-18, call, strike 600.000; a declared strike of 610 against that id is an `Error` naming the strike; an expired contract is warned about and excluded; a Greek limit with one unmarked contract in scope is unevaluable and becomes evaluable once marked; `(kind (Greek_limit (gamma 400.0)))` breaches on a fork; per-contract Greeks are on the wire and in `test_round_trips`' required list; `test_options_graph` and `Options_walk` stay green; the demo's node-count pin is unmoved.

**Commit** as `graph:`.

### Task 50: The option snapshot parser and store (pure)

**Files:** Create `lib/feed/alpaca_options.ml` (`Quote`, `quotes_of_body`, a pure chunking function, a `Store` with `to_json`) and `test/test_alpaca_options.ml`, registered in `test/test_ohcamel.ml`. Modify `lib/verified.ml` and the markers.

**What must become true:** first read Alpaca's documentation for `GET /v1beta1/options/snapshots` with documentation tools only, and record the field names and the units of the Greeks and theta in the module header. Then: a missing implied vol or missing Greeks is `None`; a non-finite or negative implied vol is refused **for that contract only**; the page token is followed; chunks hold at most 100 symbols; the store's JSON labels every entry *indicative*.

**Tests (hand-derived):** a documented two-contract payload, one full and one with neither implied vol nor Greeks; the pagination token; a negative implied vol refused for one contract while the other is kept; chunking 250 ids gives 100, 100 and 50; the store's JSON shape; a malformed body is an `Error`, never an exception. Only `data.alpaca.markets` is named, and the documentation citation is in the header.

**Commit** as `kernel:`.

### Task 51: The indicative poll, wired

**Files:** `lib/feed/alpaca_options.ml` (`run`), `bin/main.ml`, `test/desk_async/test_desk_async.ml` or `test/test_alpaca_options.ml`, `lib/verified.ml` and the markers.

**What must become true:** `run` polls every 5 minutes with `feed=indicative`, in chunks of at most 100, under the 30 s timeout with `~interrupt`, and starts only when the book declares options; startup never awaits it. The callback in `bin/main.ml` calls `Graph.set_implied_vol`, which marks the contract, and updates the store.

**Tests (hand-derived):** for Hull's case (S = 49, K = 50, r = 0.05, T = 0.3846), writing an implied vol of 0.20 marks the contract and the engine's call delta is **0.522 ± 5e-3**; a quote arriving makes a Greek limit evaluable; a hung fetch is interrupted; no poll runs when the book declares no options; `grep -n alpaca_options lib/server.ml` is empty.

**Commit** as `kernel:`.

### Task 52: The options wire finished, and every "DISABLED" removed

**Files:** `lib/server.ml` (`options_indicative` via `frame_extra` from `bin/main.ml`), `bin/main.ml` (~1180–1196), `web/desk.js` (~206), `web/risk.js` (~121), `Makefile` (~127–133), `README.md` (~439–460, ~1682–1688, and the stale "Alpaca's free tier does not provide an options chain"), `docs/status.md` (~108–110, ~392), `docs/overview.md` (~48, ~133, ~268), `lib/options.ml` (~20), `test/test_server.ml`, `lib/verified.ml` and the markers.

**What must become true:** frames carry `options_indicative` beside `option_greeks`, with per-contract engine Greeks, Alpaca's cross-check values, and null fields with their reasons while warming up. `test_round_trips`' required-key list gains **this task's** options keys; the A4 core keys were pinned in Stages 2A and 2B by the tasks that added them (ruling 25), so this task does not carry them. Every copy of "options DISABLED", "no options-chain source" and "the free tier does not provide an options chain" is rewritten as the truth: marks come from the indicative feed, the engine solves no implied vol, every mark is labelled indicative.

**Tests:** a graph with a known panel and known quotes emits the derived values to 1e-9 after the JSON round trip; a warming-up test shows the nulls and their reasons; `grep -rniE 'DISABLED|no options-chain|does not provide an options chain' web bin lib/options.ml README.md docs` is empty; `make options`, run in a dedicated clone, is byte-identical to `$TMPDIR/final-baseline/options.txt`.

**Commit** as `kernel:` for the wire and `docs:` for the prose.

### Task 53: The Risk page's options (indicative) section

**Files:** `web/risk.html`, `web/risk.js`, `test/test_embedded_assets.ml`.

**What must become true:** a fourth section after `estimators`: one row per contract, the engine's Greeks beside Alpaca's, units converted in one named function and named in the headers, every cell saying *indicative*, and unmarked contracts and unevaluable Greek limits stated in words.

**Tests (hand-derived):** an engine vega of 12.34 per 1.00 of vol is **0.1234** per vol point (÷ 100); a theta of −36.5 per year is **−0.1** per calendar day (÷ 365); the marker-order pin gains the id in fourth place; no `innerHTML` with data; Playwright at 380 px and 1280 px in both schemes against a live-mode fixture.

**Commit** as `web:`.

### Task 54: The options documents and the example book

**Files:** `README.md`, `docs/status.md`, `docs/quant_notes.md`, `docs/overview.md`, `book.example.sexp` (a commented options block and a `Greek_limit`).

**What must become true:** the options path, the declared-contract model and its limits are documented — European only, one flat rate, one vol per contract, no dividends, no strike buckets, no implied-vol solve, indicative marks, quantities from the book — together with the consequence that declaring options brings Greek limits into the gate's verdicts on **stock** orders, as `Options_walk` shows. `docs/quant_notes.md` gains the per-share Black–Scholes Greeks and the unit conversions, each citing its function.

**Tests:** `check-book` accepts the example; `make check-counts` passes; the limits appear in both `README.md` and `docs/status.md`.

**Commit** as `docs:`.

---

## Stage 4, validation lane — A5 self-validation (§3.14)

Branch `desk/a5-validation`, from `main` after Stage 3. It **rebases onto the merged options lane** before Task 58, 59 or 60 touches `bin/main.ml`, `test/test_server.ml` or `test/test_embedded_assets.ml`. Accepted when the page reads "session n of 250".

### Task 55: Journal additions, bounded and by-date queries

**Files:** `desk/journal.ml`, `desk/journal.mli`, `desk/session_close.ml`, `test/test_journal.ml`, `test/test_properties.ml`, `lib/verified.ml` and the markers.

**Interfaces — produces:**
```ocaml
(* additive: CREATE TABLE IF NOT EXISTS, schema_version stays 1 *)
(* session_meta(date PK, windows_rolled, next_session_date, flows_synced_through, recorded_at) *)
(* cash_flows(activity_id PK, date, kind, amount, recorded_at) *)
val validation_record : t -> limit:int -> (record list, string) Result.t   (* newest 251, joined *)
val orders_on : t -> date:Date.t -> …      val events_on : t -> date:Date.t -> …
val fills_on  : t -> date:Date.t -> …      (* bounded and indexed *)
```

**What must become true:** ruling 2. `Session_close.record` writes `session_meta`: whether the windows rolled — today that goes only to the log, so a stale forecast cannot be identified — and `next_session_date` from Task 44's calendar. `validation_record` joins sessions, forecasts, `session_meta` and `cash_flows`, bounded at 251 and indexed. New indexes cover orders, fills and events by time. `schema_version` stays 1.

**Tests (hand-derived):** the journal round-trip property is still the identity with the new tables and with Task 35's halt-convention alert rows present; a journal built in-test with `main`'s `CREATE` statements opens and gains them; 300 sessions give a window of the 251 newest; the row counts are hand-checked on a fixture; an order created at 23:00 ET on 2026-09-17 and filled at 09:30 ET on 2026-09-18 is found by the by-date queries for both dates.

**Commit** as `desk:`.

### Task 56: Cash flows from the venue

**Files:** `desk/venue.ml` (`Read.cash_flows`), `desk/alpaca_paper.ml` (a pure parser over `GET /v2/account/activities`), `desk/sim_venue.ml`, `desk/desk.ml` (sync), `desk/session_close.ml`, `test/test_alpaca_paper.ml`, `test/test_desk.ml`, `lib/verified.ml` and the markers.

**What must become true:** ruling 7. Non-trade external cash activity types, confirmed from Alpaca's documentation (CSD, CSW, JNLC expected), paged, under the 30 s timeout, parsed purely with deposits positive. Dividends and fees are P&L, not flows. `Sim_venue` returns `[]`. The sync journals new flows idempotently by activity id and advances `flows_synced_through` only on success; `session_close` stamps it on each session. A failed fetch leaves the session's flows **unknown**, and Task 57 counts that pair unevaluable.

**Tests (hand-derived):** a documented payload with a CSD of +20,000 and a CSW of −5,000 gives +20,000 and −5,000 on their New York dates, netting +15,000 for that date; an unknown type is ignored and logged by name; re-syncing the same page adds nothing; a failed fetch leaves `flows_synced_through` unchanged; the invariant grep is silent.

**Commit** as `desk:`.

### Task 57: `desk/validation.ml` — the pure scorer, and one lifted statistic

**Files:** Create `desk/validation.ml`, `desk/validation.mli` and `test/test_validation_live.ml`, registered in `test/test_ohcamel.ml`. Modify **`lib/var_backtest.ml`** and `test/test_var_backtest.ml`, `lib/verified.ml` and the markers.

**What must become true:** rulings 4, 5, 6 and 21.
1. **One statistic is lifted, not duplicated.** The joint conditional-coverage statistic exists today only inside `Var_backtest.run` (`lib/var_backtest.ml:514–522`): Kupiec's LR plus Christoffersen's independence LR, with `chi2_p ~df:2`. This task adds `val conditional_coverage : exceedances:bool array -> confidence:float -> float * float` next to the other four statistic functions and rewrites `run` to call it, so there is one implementation. `run`'s report fields are unchanged to the bit.
2. **The scorer never calls `run` and never touches `Estimator.t`.** `Var_backtest.Estimator.t` is a closed three-variant type (`Historical | Parametric | Parametric_ewma of float`) whose `estimate` feeds `rolling`, which produces the `make backtest` and `make backtest-crisis` tables this plan must leave byte-identical; extending it would ripple into `Estimator.estimate`, `lib/reports.ml`, `lib/validation_report.ml`, `bin/main.ml`, `test/test_properties.ml` and `test/test_validation_report.ml` and could move a published table. `desk/validation.ml` therefore keys on the estimator's **name** as the journal records it — `historical`, `parametric`, `ewma`, `garch`, `cornish_fisher` — and calls `kupiec_pof`, `christoffersen_independence`, `conditional_coverage`, `duration_independence`, `traffic_light` and `Validation_report.worst_burst` directly. `run` is never called, so the "never with n = 0" hazard does not arise; the guard instead is that no statistic is computed below 60 pairs.
3. **The loss** is `loss_t = E(t−1) − E(t) + F(t)`, a deposit positive, derived from `E_t = E_{t−1} + PnL_t + F_t`. An exceedance is `loss_t > var_notional(t−1)`.
4. **Pairing and exclusion** as ruling 5 states, with every exclusion counted and reported by reason, and an unrolled-window forecast **scored and flagged**, never dropped.
5. **Fractions** are used throughout: `var = var_notional(t−1) / G(t−1)`, `realised = −loss_t / G(t−1)`.
6. **Per estimator** over the newest 250 pairs: below 60, counts only and no verdict field; at 60 or more, every statistic in 2 above, with `worst_burst` at span 21.

**Tests (hand-derived) — every statistic the page prints has one:**
- Equity 1,000,000 → 990,000 → 1,001,000 → 1,021,000 with a +20,000 deposit on the last step and VaR 8,000 on each prior session gives losses **10,000**, **−11,000** and **0** — so **one exceedance in three**.
- A deposit alone: 1,000,000 → 1,005,000 with a 10,000 deposit is a loss of **5,000**, not a gain.
- Day *t*'s loss is scored against the forecast dated *t−1*, never *t*: a day-*t* VaR of 50,000 does not rescue day *t*.
- A hole, an unrolled window, a flat book (G = 0 or VaR `None`), a missing forecast and unknown flows are each excluded-or-flagged **and counted**, with the counts asserted.
- n = 59 prints no statistic; n = 60 does.
- **Kupiec** at n = 60, x = 3, p = 0.05 gives **LR = 0** exactly, because x/n = p; at n = 60, x = 0 it gives −120 ln 0.95 = **6.15520**.
- **Christoffersen independence** on a hand-built 12-element series with exceedances at positions 2, 3 and 4 (so n00 = 7, n01 = 1, n10 = 1, n11 = 2 over the 11 transitions): the four transition counts are asserted first, then the LR is asserted against the value the closed form gives for them, derived in the comment.
- **Conditional coverage** equals the sum of those two statistics, with `chi2_p ~df:2`, asserted to 1e-9 — and equals `Var_backtest.run`'s `conditional_coverage_statistic` on the same series, which is the identity that proves the lift changed nothing.
- **Duration independence** on a hand-built series with exceedances at positions 1, 5 and 9 — durations 4 and 4 — returns `Some` with its statistic derived in the comment, and on a series with fewer than two exceedances returns `None`.
- **The traffic light at the book's confidence.** At n = 60 and p = 0.05 the zone boundary is computed from the binomial CDF in the comment: the test asserts the exact exceedance count at which the zone changes, one count either side, and that the reported label says *binomial traffic light at 95%* and not Basel's zone.
- The dollar and fraction forms give the same exceedances.

**Commit** as `kernel:` for the lifted statistic and `desk:` for the scorer; two commits is fine.

### Task 58: The demo's forecast horizon matches its session

**Files:** `desk/session_close.ml` (`?horizon_bars`, default 1), `bin/main.ml` (the demo passes 20), `web/execution.js`, `test/test_session_close.ml`, `lib/verified.ml` and the markers. Follows the rebase onto the merged options lane.

**What must become true:** ruling 8. The demo pushes a synthetic daily return every 15 s bar and records a session every 20 bars, so a 1-bar VaR scored against a 20-bar equity change would inflate the exceedance rate by about √20 and show a model REJECTED by construction on the public page. The demo's journaled session forecasts are therefore scaled by √20 and the page says so; the live path is untouched. **Before relying on √20, verify that `Synthetic_book`'s per-bar draws are i.i.d. with zero mean; if they are not, stop and report rather than scaling.**

**Tests (hand-derived):** the demo's parametric row equals √20 = 4.472136 times the 1-bar value to 1e-9, so a VaR of 1,000 becomes **4,472.14 ± 0.01**; the live-path `session_close` tests pass unchanged; the page sentence is pinned; the i.i.d. verification is recorded in the ledger with the source lines it rests on.

**Commit** as `desk:`.

### Task 59: The route and the page — "Session n of 250"

**Files:** `desk/desk_routes.ml` (`GET /api/desk/validation` on both hosts), `desk/desk.ml`, `bin/main.ml`, `web/execution.html`, `web/execution.js`, `web/argument.html` (~104), `test/test_desk_routes.ml` or `test/test_server.ml`, `test/test_embedded_assets.ml` (~574, ~594), `deploy/smoke.sh` (`EXPECTED_ROUTES`), `lib/verified.ml` and the markers.

**What must become true:** the JSON carries `n`, the window of 250, and per estimator: n, exceedances, unevaluable counts by reason, and verdict fields **only** from 60 pairs. The Execution page gains a validation section reading *Session n of 250*, with the per-estimator table and a zone labelled *binomial traffic light at 95%* only from 60, and a sentence below that. The demo is labelled synthetic and states its √20 scaling. `web/argument.html`'s "the live host's own track record is not here yet … that's phase A5" points at the new section.

**Tests:** a route test at n = 12 has no verdict fields and a fixture at n = 60 has them; page markers; `grep -rn 'phase A5' web` is empty; no `innerHTML` with data; `EXPECTED_ROUTES` equals the 404 route list exactly and in order, with the options lane's routes already in it after the rebase; Playwright on a local demo at 380 px and 1280 px in both schemes.

**Commit** as `desk:` for the route and `web:` for the page.

### Task 60: `ohcamel replay DATE`

**Files:** Create `desk/replay.ml`, `desk/replay.mli` and `test/test_replay.ml`, registered in `test/test_ohcamel.ml`. Modify `bin/main.ml` (the mode and the usage text), `README.md`, `docs/status.md` (the drive table and the owner command), `lib/verified.ml` and the markers.

**What must become true:** ruling 10. `replay` opens `OHCAMEL_JOURNAL` (default `/data/desk.db`) **read-only** through `Journal.open_read_only`, and prints the session row and each order with its events, fills and TCA. WAL allows a concurrent reader. A bad date exits 2 with the usage text; a missing journal exits 1. The owner's command is `docker compose -f deploy/docker-compose.yml --profile live exec ohcamel-live ohcamel replay YYYY-MM-DD`; the task confirms the binary's path inside the image.

**Tests:** a golden test on a temp-file journal holding a market-on-open order created the evening before and filled at the open, plus one day order and three fills, prints exactly the expected text; an empty date prints `no orders on D`; a write through the read-only handle fails and the file's mtime is unchanged; the usage text lists `replay`.

**Commit** as `desk:`.

### Task 61: Research's "against live" — the shadow record

**Files:** Create `desk/shadow.ml`, `desk/shadow.mli` and `test/test_shadow.ml`, registered in `test/test_ohcamel.ml`. Modify `desk/desk.ml` (an `against_live` field on `/api/research`), `web/research.html` (~22–24, ~45–48), `web/research.js`, `test/test_embedded_assets.ml`, `lib/verified.ml` and the markers.

**What must become true:** for each strategy, each journaled signal's targets are applied to the next session's marks: shadow return = Σ wᵢ × (close_{t+1} / close_t − 1) from the journal's marks. Report the compounded return, the hit rate and the maximum drawdown beside the EXP-A01 manifest's out-of-sample figures, which `/api/research/evidence` already serves. It is labelled *shadow: advisory signals, not fills*, because both strategies fail R6 and the desk holds no traded record of either. No comparison sentence prints below 60 shadow sessions; until then the page reads *n of 60 needed*. A session with a missing mark is unevaluable and counted. On the demo it uses `demo_synthetic`, labelled.

**Tests (hand-derived):** SPY at weight 1 with marks 100 → 101 → 99.99 gives returns **+1.00%** and **−1.00%**, compounding to **−0.01%**, with a hit rate of **50%** and a maximum drawdown of **1.0%**; SPY at weight 0.5 with marks 100 → 102 gives **+1.00%**; 59 sessions give no verdict; `grep -rn 'nothing on this page makes the comparison yet' web` is empty and the pins are updated.

**Commit** as `desk:` for the module and `web:` for the page.

### Task 62: The A5 documents

**Files:** `docs/quant_notes.md`, `README.md` (a "Self-validation" section, and `replay`), `docs/status.md`, `docs/overview.md`, `docs/superpowers/specs/2026-09-12-the-desk-design.md` (§8 gains rulings 5, 6, 8, 9 and 21), `lib/verified.ml` and the markers.

**What must become true:** the loss definition **with its derivation and sign**, the pairing and exclusion rules, cash flows and what a paper-account reset does, the zone at the book's confidence, the demo's √20 scaling, the shadow record's meaning, the fact that the live scorer reuses the statistic functions rather than `Var_backtest.run` and why, and the owner's `replay` command are all written down, each formula citing its function. `docs/status.md`'s limits gain *the live VaR record has no statistical power for months*, and "Next" moves A5's verdict to owner time — 60 scored sessions from the first live close for `historical`, `parametric` and `ewma`, and 60 from Stage 2A's deploy for `garch` and `cornish_fisher`.

**Tests:** every formula cites a function that exists; the five §8 entries are present; `make check-counts` passes.

**Commit** as `docs:`.

### Task 63: Stage 4 close, both lanes

**Files:** `lib/verified.ml`, `README.md`, `docs/status.md`, `docs/quant_notes.md`, `deploy/smoke.sh`, the ledger.

**What must become true:** the options lane merges to `main` first; the validation lane rebases onto it before Tasks 58, 59 and 60 and again before its own merge. The controller reconciles **every** shared file, which is more than an earlier draft claimed: `bin/main.ml` (49, 51, 52 against 58, 59, 60), `test/test_embedded_assets.ml` (53 against 59, 61), `test/test_server.ml` (49, 52 against 59), `README.md` and `docs/status.md` (54 against 62), `docs/quant_notes.md` (54 against 62), `lib/verified.ml`, the count markers, `test/test_ohcamel.ml`'s registrations and `deploy/smoke.sh`'s `EXPECTED_ROUTES`. A whole-branch review of both lanes covers §3.13's options clause, §3.14, A4's ruling 8, and rulings 4 to 10 and 21 — with an explicit check that **no forecast is scored against its own outcome** and that `Var_backtest.Estimator.t` is unextended and `run` uncalled from `desk/`. Then the fix wave, `make coverage` in a dedicated clone, fast-forward `main`, push, publish. Owner steps **O20** and **O21** follow.

**Tests:** CI, `lint` and the image job are green on `main`; the harness smoke passes, including `/api/desk/validation`; `grep -rn 'Var_backtest.run' desk/` is empty; the five baseline tables diff clean.

**Commit** as `verified:`.

---

## Stage 5 — Research completion

Branch `final/s5-research`.

### Task 64: The R8 ruling, from evidence

**Files:** `.superpowers/sdd/2026-09-19-final/progress.md` (the evidence), `docs/superpowers/specs/2026-09-12-the-desk-design.md` (a §3.12 amendment **or** a new §8 item).

**What must become true:** ruling 16's gate, worded to what the evidence can support. The agent holds no credential and fetches no data, so "the two produce identical closes" is not demonstrable and is not the test. The decidable question is **equivalence of source, adjustment and bar finality/timing**: read `research/src/ohcamel_research/service.py`'s `AlpacaBarSource` — its feed, its adjustment and the end time `fetchable_through` pins — and compare it with `lib/feed/long_window.ml`'s request as Task 22's module header records it (SIP, `adjustment=all`, final bars only, `end = now − 16 min`). If feed, adjustment and finality match, amend §3.12 to allow R8 under a language-neutral recipe **in `observe` mode first** (ruling 16), because equivalence of request is strong evidence and not proof, and an observed record is what turns it into proof. If they do not match, record R8 as a permanent §8 departure **stating the exact difference**, and Tasks 65 and 66 do not run.

**Tests:** the ledger cites both code paths by file and line and quotes the three fields from each; the ruling is one sentence in the spec, either the §3.12 amendment or the new §8 item; the decision names which of Tasks 65 and 66 are in scope and, if they are, that enforcement ships as `observe`.

**Commit** as `docs:`.

### Task 65: The R8 recipe in both languages, with a shared oracle

**Files:** `research/src/ohcamel_research/contract.py`, `desk/contract.ml`, `desk/contract.mli`, `interface/signal.schema.json` (an optional `data_hash_recipe`), `interface/fixtures/r8/vectors.json` (new), `interface/README.md`, `research/tests/test_contract.py`, `test/test_contract.ml`, `research/src/ohcamel_research/verified.py`, `lib/verified.ml` and the markers. **Runs only if Task 64 allows it.**

**What must become true:** `micro-v1` is SHA-256 over lines `SYMBOL,YYYY-MM-DD,<close in integer micro-dollars>\n`, sorted by symbol then date, each close round-half-even from `close × 1e6`. Today `contract.py` hashes floats through Python's `repr`, which is why §3.12 deferred R8. Documents that do not declare the recipe keep their current meaning and their R8 reads *not checked (v1 recipe)*, so EXP-A01's committed manifests never change.

**Tests (hand-derived):** both languages produce the oracle's hashes byte for byte on vectors including 512.34 → **512340000**, 0.0001 → **100**, and 99999.9999 → **99999999900** — which in binary floating point is 99999999899.99999…, so round-half-even is load-bearing; EXP-A01's manifest hash check is unchanged in CI.

**Commit** as `research:` for Python and `desk:` for OCaml.

### Task 66: R8 in the core, observing first

**Files:** `lib/feed/long_window.ml` (a raw-close request for the strategy symbols, `adjustment=raw`, `feed=sip`), `lib/raw_closes.ml` (a new pure store), `lib/config.ml` (the `(r8 observe|enforce)` book switch, default `observe`), `desk/intake.ml`, `bin/main.ml`, `book.example.sexp`, `web/research.html` (~50–53), `web/research.js`, `test/test_intake.ml`, `test/test_long_window.ml`, `test/test_example_book.ml`, `lib/verified.ml` and the markers. **Runs only if Task 64 allows it.**

**What must become true:** ruling 16's staging. Each refresh updates the store; the intake recomputes `micro-v1`. A store that is not yet warm, or whose dates do not cover the document's `as_of`, makes R8 unevaluable and the signal is **deferred** with a reason and re-judged every minute — never refused, in either mode. A **mismatch** behaves by the switch: in `observe` (the default) the signal is deferred with both hashes recorded and the page says *R8 observing*; in `enforce` it is refused under R8 naming both hashes. The switch exists because a code read establishes that two requests agree, not that two datasets do, and refusing live signals on the strength of an inference would be the wrong failure. The owner promotes to `enforce` at step **O22** once the record shows no mismatch. The demo checks against its synthetic closes. The "R8 is not enforced" sentence and its pins go, replaced by the true one.

**Tests (hand-derived):** a matching document passes in both modes; one changed close is deferred with both hashes in `observe` and refused under R8 with both hashes in `enforce`; an `as_of` beyond the store is deferred in both modes and then accepted once the store advances; the default book parses as `observe`; `grep -rn 'not enforced' web desk` is empty.

**Commit** as `desk:`.

### Task 67: A report-only buy-and-hold figure

**Files:** Create `research/src/ohcamel_research/benchmark.py`, `research/experiments/EXP-A01/benchmark.md`, `research/experiments/EXP-A01/benchmark.json` and `research/tests/test_benchmark.py`. Modify `research/src/ohcamel_research/cli.py`, `research/src/ohcamel_research/verified.py`, `Makefile` (`research-reproduce` regenerates it), `deploy/research.Dockerfile` (the staleness step either includes the new file or excludes it explicitly), `desk/desk.ml` (`/api/research/evidence`), `web/research.js`.

**What must become true:** ruling 17. Buy-and-hold for SPY and TLT over EXP-A01's out-of-sample folds, reporting CAGR, volatility, Sharpe and maximum drawdown beside each strategy, in its own file, labelled *post-hoc, not pre-registered, not a gate*. EXP-A01's `hypothesis.md`, `config.yaml`, manifests and `report.md` verdicts are byte-identical, because the battery is pre-registered and frozen and its manifests are committed evidence. The owner may veto this task at step **O22**.

**Tests (hand-derived):** prices 100, 110, 99, 105 give a maximum drawdown of 11/110 = **10.0%** and a total return of **5.0%**; `git diff` shows the manifests and `report.md` unchanged; `make research-reproduce` regenerates `benchmark.md` byte-identically; the page says post-hoc.

**Commit** as `research:`.

### Task 68: An end-to-end signal test across the two languages

**Files:** `research/tests/fixtures/` or `interface/fixtures/` (a signal file emitted by the Python CLI and committed), `Makefile` (a `research-fixture-signal` target and a CI check that regeneration is byte-identical), `test/test_intake.ml`, `lib/verified.ml` and the markers.

**What must become true:** the service-to-desk loop is closed hermetically: a file the Python CLI emitted goes through the OCaml intake, is judged R1–R7, lands as a journal row and appears on `/api/research` with the expected verdict. Where `research/tests/test_cross.py` already covers part of this, extend it rather than duplicate it.

**Tests:** the OCaml test reads the committed file and asserts the verdict, the journal row and the served JSON; the CI regeneration check passes.

**Commit** as `test:`.

### Task 68a: The research service reads the exchange calendar

**Files:** `research/src/ohcamel_research/service.py`, `research/tests/test_service.py`, `research/src/ohcamel_research/verified.py`, `research/README.md`, `docs/status.md` (the limits), `docs/superpowers/specs/2026-09-12-the-desk-design.md` (§8, if the fallback is taken).

**What must become true:** the service's whole test for a trading day today is `now_et.weekday() < 5` (`service.py:707–712`), so on a weekday holiday it polls all evening for a document that will never come and logs a missing-strategy warning each time — a departure the stubs survey recorded and that nothing else in this plan closes. The service gains a calendar: it fetches `GET /v2/calendar` for the current and next month through its existing Alpaca client, caches the result on disk beside its heartbeat with a one-day TTL, and treats a day absent from the calendar as a non-trading day. When the fetch fails **and** no cache is usable, it falls back to the weekday rule, logs one line saying so, and the shortfall is the stated §8 limit. `_previous_weekday` becomes `_previous_trading_day` against the same calendar. The engine's calendar (Task 44) and the service's are separate reads of the same endpoint, which is stated in the docs; sharing one would need a shared process and there is none.

**Tests (hand-derived, hermetic):** a canned calendar payload for November 2026 omitting 2026-11-26 makes the service skip Thursday 2026-11-26 and run on Friday 2026-11-27; `_previous_trading_day(2026-11-27)` is 2026-11-25, not 2026-11-26; a fetch failure with no cache runs on the weekday rule and logs the fallback line exactly once; a stale cache past its TTL is refetched; the research count is bumped and `make check-counts` passes.

**Commit** as `research:`.

### Task 69: Real research and interface READMEs

**Files:** `research/README.md`, `interface/README.md` (the R8 row, following Task 64's outcome).

**What must become true:** `research/README.md` — five lines today, saying "(Phase 1)" and documenting no command — covers the layout, every CLI command checked against `cli.py` (`battery run`, `fixtures doctor`, `signal emit`, `signal check`, `contract check`, and `battery benchmark` if Task 67 ships), the service's schedule **and its calendar with its fallback**, `make research-test` and `make research-reproduce`, the fixtures, EXP-A01, and how a new experiment is pre-registered. `interface/README.md`'s R8 row stops saying "Phase 2, when the core keeps a bar store" — Alpha's phase naming — and states this project's outcome: observing, enforcing, or the §8 departure.

**Tests:** every command the README names runs with `--help`; no "Phase 1" or "Phase 2" wording carried over from Alpha remains.

**Commit** as `docs:`.

### Task 70: Stage 5 close

**Files:** `lib/verified.ml`, `research/src/ohcamel_research/verified.py`, `docs/status.md`, the ledger.

**What must become true:** a whole-branch review, including a quant-rigor check that the benchmark is labelled and gated nowhere and that the frozen battery is untouched; the fix wave; the reconciliation of `research/src/ohcamel_research/verified.py` and the count markers across Tasks 67, 68 and 68a; `make coverage` in a dedicated clone; fast-forward `main`, push, publish.

**Tests:** CI, `lint`, `research-test` and the image job are green on `main`; the research image's staleness step passes.

**Commit** as `verified:`.

---

## Stage 6 — Finish: documents, audit, review, merge

Branch `final/s6-finish`.

### Task 71: Limits and departures, complete

**Files:** `README.md`, `docs/status.md`, `docs/superpowers/specs/2026-09-12-the-desk-design.md` (§8).

**What must become true:**
1. **One limits list, identical in `README.md` and `docs/status.md`,** covering §7's seven — and stating explicitly the three that are missing today: that marks are the IEX last print and quotes the IEX BBO rather than the consolidated tape or the NBBO; that the live VaR record has no statistical power for months; that signals trade ETFs only because the data has no delisted names (said only in `docs/CHARTER.md` today) — plus A4's, A5's and operations' limits, and the research service's calendar fallback if it was taken.
2. **§8 gains every departure the surveys named, and the four this plan added:** TCA's former per-fill aggregation and its replacement; the `Venue` `Read`/`Trade` split and the `Wire` permit, where the spec shows one record; the extra `tick` rule; market-on-open orders for rebalances; the retired `File` sink; the `synthetic:` scheme on the demo; the demo's √20 horizon; the zone at the book's confidence; in-process versus container backups; liveness-only `/api/health`; R8's outcome and, if built, its `observe`-first enforcement; **the live smoke suite's deliberate omission of a POST to `/api/desk/kill`**, with §3.15's acceptance row marked met in part and the hermetic coverage named; **the `alerts` table's `kill_switch` convention for halt and reset rows**, since `limit_name` is `NOT NULL` and a bump is forbidden; **the research service's separate calendar read**; and **that `lib/` has no interface files**, so invariant 10–12's one-submit-site guarantee is a compile fact in `desk/` and a test fact in `lib/`.

**Tests:** the reviewer maps every deviation Task 74 finds to a §8 item; the two limits lists are identical, checked by diff.

**Commit** as `docs:`.

### Task 72: Reference docs — the modules, the formulas, the example book

**Files:** `README.md` (the module list and the "only module that can reach the world" sentence), `docs/quant_notes.md`, `book.example.sexp`, `test/test_example_book.ml`.

**What must become true:**
1. **The module list** gains every `desk/*` module and the missing kernel modules — `gate.ml`, `config.ml`, `reports.ml`, `recompute_log.ml`, `long_panel.ml`, `factor_model.ml`, `liquidity.ml`, `scaling_probe.ml` and the new feeds — and the sentence claiming `alerts.ml` is the only module that can reach the world is corrected, because `lib/feed` and the desk's Alpaca client also do (and `alerts.ml` no longer writes a file at all, after Task 34).
2. **`docs/quant_notes.md`** gains TCA (§3.9) per order, the gate's created/worsened/cleared/unevaluable rule, and whole-share rebalance sizing, each citing its function — so that the README's and status's claim of "every formula" is true.
3. **`book.example.sexp`** carries every limit kind and every block: `Gross_notional`, `Value_at_risk`, `Component_var`, `Max_drawdown`, `Factor_exposure`, `Greek_limit`, and the `desk`, `signals`, `liquidity`, `options` and `r8` blocks.

**Tests:** a new test fails if any `Limit_spec` kind is absent from `book.example.sexp`; every `quant_notes` entry cites a function that exists; `check-book` accepts the example.

**Commit** as `docs:`.

### Task 73: Screenshots from the finished demo

**Files:** `docs/media/*.png`, `README.md` (the image references).

**What must become true:** every screenshot is re-captured with Playwright from a local `ohcamel demo` at the final sha, in both colour schemes, covering Desk with Figure 1, Risk, Research and Execution. Today `dashboard.png` predates W1 and `demo.png` and `stress.png` predate W2, A2 and A3.

**Tests:** every image `README.md` references exists and was captured in this stage; each is under 1 MB; the caption names the sha.

**Commit** as `docs:`.

### Task 74: The spec-conformance audit, row by row

**Files:** `.superpowers/sdd/2026-09-19-final/progress.md` (the audit table), `docs/status.md` (the inventory with re-counted and re-dated line figures, "How it got here" rows for every stage, and "Next" reduced to owner steps).

**What must become true:** walk every item of spec §0–§8 and every §5 acceptance row, plus every item the four completion surveys marked partial, missing or broken, and record the evidence per row: a test name or `file:line`, a demo URL path, or a §8 item. A gap becomes a fix task in Task 75. The line counts are re-measured — today `docs/status.md` says `desk/` is 2,000 lines and `test/` 13,700, against roughly 9,650 and 24,500 measured. Each row carries the sha whose tree the evidence was read from, so Task 76a can tell which rows the fix wave and the coverage re-measure touched.

**Tests:** every row reads *done*, *departure* or *owner-only*, and none reads *partial*; the trace table from Task 1 has no unmapped item; `make check-counts` passes.

**Commit** as `docs:`.

### Task 75: The whole-project review and the final fix wave

**Files:** as the findings require.

**What must become true:** an opus-class review of everything since `b6132c6`, against the spec, A4's rulings 1–12 and this plan's rulings 1–26. It covers invariants 6 and 9–13, hermeticity, hand-derived values, the dead-code sweep, and the point-in-time property that no forecast is scored against its own outcome. It runs `make build`, `make test`, `make research-test`, `dune build @fmt`, every grep and `make coverage`; diffs the five tables and the scaling row against `$TMPDIR/final-baseline/`; and checks every page of a local demo in a browser at 380 px and 1280 px in both schemes. Findings are fixed one task at a time, each with its own scoped review.

**Tests:** the verdict is Ready, with every Critical and Important finding fixed and re-reviewed, and every Minor fixed or recorded with its reason.

**Commit** as the fixes require.

### Task 76: The final coverage re-measure

**Files:** `lib/verified.ml` (the coverage constants, dated), `README.md` (the badge and the per-file table rebuilt from bisect's summary), `docs/status.md`, the `Makefile`/CI coverage sentence.

**What must become true:** `make coverage` runs once at the final test count, in a dedicated local clone at the branch tip (ruling 23), and every published figure is re-dated. The machine it was measured on is named, because bisect counts visited points and the number moves with instrumentation.

**Tests:** `lib/verified.ml`'s fraction equals bisect's summary; the badge and `docs/status.md` agree to their stated roundings; `make check-counts` passes and the `lint` job is green.

**Commit** as `verified:`.

### Task 76a: The audit re-walk

**Files:** `.superpowers/sdd/2026-09-19-final/progress.md` (the audit table), `docs/status.md` (any inventory figure the fix wave moved).

**What must become true:** Task 74 wrote the audit against a tree that Task 75's fix wave and Task 76's re-measure then changed, so any row citing a test name, a `file:line`, a count or a coverage figure may now be stale — and the definition of done requires every row to read *done* with **final** evidence. This task re-walks the table against the final tree: every row whose sha differs from the current one is re-verified and its citation refreshed; any row that has become *partial* is a new fix task with its own scoped review, after which this task runs again. The line counts and the inventory are re-measured if the fix wave touched them.

**Tests:** every row's sha equals the branch tip's; no row reads *partial*; every cited test name exists and passes and every cited `file:line` resolves, checked by a script whose output goes in the ledger; the coverage figures the table cites equal `lib/verified.ml`'s.

**Commit** as `docs:`.

### Task 77: Merge, publish, and the deploy-readiness packet

**Files:** `main`; `docs/status.md` (a "Deploying the final build" section); the ledgers; the memory notes (outside the repository).

**What must become true:** fast-forward `main` and push. Confirm CI green on both operating systems, plus `lint`, `coverage`, `research-test`, `image` and `uptime`; both images published for the final sha; the harness smoke passing for that sha. Then close every ledger, update the memory notes to say the direction is complete and only owner steps remain, and confirm the worktree and branch list is clean. The packet gives the owner, in order, the commands to pull-deploy the final sha, run smoke with `--live-container`, run the restore drill, and verify each live page — with every flag checked against `deploy.sh --dry-run`.

**Tests:** the definition of done in `docs/superpowers/specs/2026-09-19-the-finish.md` §5 passes, apart from the items that are owner-only or wait on time; §5 item 14's re-walk is Task 76a's and is cited; the packet's commands match `deploy.sh`'s real flags.

**Commit** as `docs:`.

---

## Owner steps

Everything below is the owner's, because the agent cannot ssh to the droplet, cannot read a credential, cannot edit `book.sexp` and cannot build an image locally. Report each result so `docs/status.md` can record it; nothing here is claimed done on the owner's behalf.

**O1 — consent to housekeeping (before Task 7's second half).** Confirm the merged worktrees and branches in Task 7 may be removed, and confirm that branch `worktree-agent-a0cbd73aa352f7ae6` at `d0b5f98` (an A3 leftover) is superseded. Each is tagged under `archive/` before deletion. Also confirm, once, the standing authorization to push stage branches to the public `origin` and to fast-forward `main` — CI is the only place an image can be built.

**O2 — deploy W2, A3 and Stage 0 (after Stage 0 merges; outside 13:30–20:00 UTC).** The first `--live` run builds `ohcamel-research` for the first time ever, which may take about 20 minutes if the opam layer changed. `deploy.sh`'s build step and market guard were linted in CI by Stage 0's own `lint` job before this step ran.
```
ssh ohcamel
cd ~/OhCamel
git pull --ff-only
cp -p book.sexp ~/book.sexp.bak-$(date -u +%Y%m%dT%H%M%SZ)
deploy/deploy.sh --live
docker compose -f deploy/docker-compose.yml --profile live ps
docker compose -f deploy/docker-compose.yml --profile live logs --tail 80 ohcamel-live ohcamel-research | grep -E 'desk|intake|first sync|research|trading'
```
Report the sha and the smoke line (`N passed, 0 failed`).

**O3 — merge the book by hand.** Never copy `book.example.sexp` over `book.sexp`; that would discard the live book. In `~/OhCamel/book.sexp`:
- add `((symbol SPY) (sector INDEX) (qty 0.0))` and `((symbol TLT) (sector TREASURIES) (qty 0.0))` to `positions`;
- add the `desk` block from `book.example.sexp`, including `(spread_bps ((SPY 1.0) (TLT 1.5)))`, with `(trading disabled)` for now;
- add the `signals` block with `exp_a01_spy` and `exp_a01_tlt`, both left `(sizing advisory)`.

Apply it:
```
docker compose -f deploy/docker-compose.yml --profile live restart ohcamel-live
```
From Stage 1 on, validate first:
```
docker run --rm -v $PWD/book.sexp:/app/book.sexp:ro ghcr.io/ajaiupadhyaya/ohcamel:$(git rev-parse HEAD) check-book
```

**O4 — arm the live desk.** In the `desk` block set `(trading enabled)` and your `max_order_notional`. Set `(alerts ((enabled true) (sinks (Log)) (clear_below 0.95) (kill_switch_enabled true) (kill_switch_trips_on (<your own limit names>))))`. Do **not** use a `(File …)` sink: Stage 3 retires it and `check-book` will refuse it with a sentence pointing at the journal's alerts table. Then restart `ohcamel-live` and confirm:
```
docker compose -f deploy/docker-compose.yml --profile live logs --tail 40 ohcamel-live | grep -E 'trading|kill|first sync'
```

**O5 — A2's acceptance: one paper order filled on the live host.** During market hours (13:30–20:00 UTC on a weekday), place a one-share SPY ticket from the live Desk page, or:
```
P="$(cat ~/.ohcamel-live-password)"
H='https://live.ohcamel.ajaiupadhyaya.com'
curl -sS -u ohcamel:"$P" -H 'X-OhCamel-Desk: 1' -H "Origin: $H" -H 'Content-Type: application/json' \
  -d '{"symbol":"SPY","side":"buy","qty":1}' "$H/api/desk/preview"
curl -sS -u ohcamel:"$P" -H 'X-OhCamel-Desk: 1' -H "Origin: $H" -H 'Content-Type: application/json' \
  -d '{"symbol":"SPY","side":"buy","qty":1}' "$H/api/desk/orders"
curl -sS -u ohcamel:"$P" "$H/api/desk/tca"
```
Paper only. Report the fill.

**O6 — A3's live half.** After the first 00:16 UTC research run following O2 and O3, open `https://live.ohcamel.ajaiupadhyaya.com/research` and confirm both `exp_a01_spy` and `exp_a01_tlt` show *advisory* with their rule. Then:
```
ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml --profile live logs --since 12h ohcamel-research | tail -40'
```

**O7 — rotate the exposed Alpaca paper key (outside market hours).** The key in `~/.claude.json` was printed into a tool output and is still unrotated.
1. Regenerate it in Alpaca's Paper dashboard, API Keys.
2. `ssh ohcamel-root`, edit `/etc/ohcamel/live.env`, then `stat -c '%U:%G %a' /etc/ohcamel/live.env` must print `root:ohcamel 640`.
3. ```
   ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml --profile live up -d --force-recreate ohcamel-live ohcamel-research && sleep 90 && docker compose -f deploy/docker-compose.yml --profile live logs --tail 40 ohcamel-live | grep -E "Alpaca paper|first sync"'
   ```
   `restart` would keep the old environment.
4. On the laptop: `rm -P ~/ohcamel-live.env` (or refresh it), then `claude mcp remove alpaca-paper`, re-adding it with the new key only if you still want it.

Optionally rotate FRED the same way, and the basic-auth password with `docker run --rm -it caddy:<pinned> caddy hash-password`, putting the hash in `deploy/.env` with **every `$` doubled** and then `up -d --force-recreate caddy`. Never `source deploy/.env`.

**O8 — pin `fdq`.** In `capitallimits`, `652474c` is the tip of a feature branch, not `main`; deleting that branch would make research's locked commit unreachable for CI and the droplet.
```
git tag fdq-ohcamel-652474c 652474c && git push origin fdq-ohcamel-652474c
```
or merge it to `main`.

**O9 — archive Alpha.**
```
gh api repos/ajaiupadhyaya/ohcamel-alpha/commits/main --jq '.sha[0:7] + "  " + (.commit.message | split("\n")[0])'   # expect 21c000e, the pointer
gh repo archive ajaiupadhyaya/ohcamel-alpha --yes
gh repo view ajaiupadhyaya/ohcamel-alpha --json isArchived
```

**O10 — make the images reachable (after Stage 1 merges and the first image run publishes).** A package's first push is private, and free private storage is 500 MB, which per-sha images would fill. On github.com: profile, Packages, `ohcamel`, Package settings, Danger Zone, Change visibility, Public — then the same for `ohcamel-research`. The images hold only public-repository content. If you keep them private instead:
```
ssh ohcamel 'docker login ghcr.io -u ajaiupadhyaya --password-stdin' < ~/ghcr-read.token   # a classic PAT with read:packages only
```
Until one of these is done, deploy with `deploy/deploy.sh --live --build`, which builds **both** images on the droplet and tags both with the sha.

**O11 — failure notifications.** GitHub, Settings, Notifications, System, Actions: *Only notify for failed workflows*, with Email ticked. If the uptime workflow is ever auto-disabled after 60 quiet days: `gh workflow enable uptime.yml`.

**O12 — root host setup (after Stage 1).**
```
ssh ohcamel-root
install -d -m 0750 -o 10001 -g ohcamel /var/backups/ohcamel
cd /home/ohcamel/OhCamel/deploy/systemd && install -m 0644 ohcamel-backup.service ohcamel-backup.timer ohcamel-watch.service ohcamel-watch.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ohcamel-backup.timer ohcamel-watch.timer
systemctl start ohcamel-backup.service && journalctl -u ohcamel-backup -n 30 --no-pager && ls -l /var/backups/ohcamel
printf '%s\n' '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
printf '%s\n' 'Unattended-Upgrade::Automatic-Reboot "true";' 'Unattended-Upgrade::Automatic-Reboot-Time "10:00";' > /etc/apt/apt.conf.d/52ohcamel-reboot
systemctl list-timers 'ohcamel-*'
```
`daemon.json` applies to new containers; `systemctl restart docker` restarts every container, so only outside market hours. Optionally put `SLACK_WEBHOOK_URL=…` in `/etc/ohcamel/ops.env` at 0640 root:ohcamel.

**O13 — the first deploy by pull (after Stage 1).**
```
ssh ohcamel
cd ~/OhCamel
git fetch && deploy/deploy.sh --live --sha $(git rev-parse origin/main)
tail -3 ~/deploys.log
```
Report the log line and the smoke result, including the `--live-container` checks and the `clock` block.

**O14 — the restore drill (A6's acceptance).** After the first nightly backup:
```
ssh ohcamel
cd ~/OhCamel
ls -l /var/backups/ohcamel | tail -3
deploy/restore.sh --drill /var/backups/ohcamel/desk-$(date -u +%F).db
```
Paste the output — schema version, integrity ok, table counts, newest session — so `docs/status.md` records the drill with its date.

**O15 — choose the off-box copy.** Free: `launchctl load ~/Library/LaunchAgents/com.ohcamel.pull-backups.plist` on the laptop, which pulls only while the laptop is awake. Paid, and a Q10 spending decision: `doctl compute droplet-action enable-backups 597046661 --wait`, about $4.80 a month.

**O16 — host alerts.**
```
doctl compute droplet get 597046661 --format ID,Name,Features        # is 'monitoring' listed?
# if not: ssh ohcamel-root 'curl -sSL https://repos.insights.digitalocean.com/install.sh | bash'
doctl monitoring alert create --type v1/insights/droplet/disk_utilization_percent --compare GreaterThan --value 80 --window 10m --entities 597046661 --emails OWNER_EMAIL --description 'ohcamel disk > 80%'
doctl monitoring alert create --type v1/insights/droplet/memory_utilization_percent --compare GreaterThan --value 90 --window 10m --entities 597046661 --emails OWNER_EMAIL --description 'ohcamel memory > 90%'
doctl monitoring alert create --type v1/insights/droplet/cpu --compare GreaterThan --value 90 --window 30m --entities 597046661 --emails OWNER_EMAIL --description 'ohcamel cpu > 90% for 30 min'
doctl monitoring alert list
```

**O17 — measure capacity once,** and report the numbers for `docs/status.md`:
```
ssh ohcamel-root 'df -h / && free -m && docker system df && docker volume ls'
```
Optional cleanup outside market hours: `ssh ohcamel 'docker image prune -f && docker builder prune -f --keep-storage 5GB'`.

**O18a — after Stage 2A (the long window and the estimators). Time-critical.** This deploy is what starts the 60-session clock for `garch` and `cornish_fisher`, so it is worth doing on the first suitable day rather than batching it with 2B.
```
ssh ohcamel && cd ~/OhCamel && git fetch && deploy/deploy.sh --live --sha $(git rev-parse origin/main)
```
After the next 00:20 UTC refresh, confirm `/api/ops` carries a `long_window` block with an `as_of` and per-instrument observation counts, and that the Risk page's estimators section fills. After the next session close, confirm `garch` and `cornish_fisher` forecast rows appear:
```
docker compose -f deploy/docker-compose.yml --profile live exec ohcamel-live ohcamel journal-verify /data/desk.db | tail -20
```
Report the date of the first session with five forecast rows — that is day 1 of 60.

**O18b — after Stage 2B (the factor model and liquidity).** `deploy/deploy.sh --live`. Optionally add to `book.sexp` — validating with `check-book` first — the `(liquidity ((participation 0.10) (impact_coefficient 1.0)))` block and `Factor_exposure` limits. Confirm the live Risk page's factor-model and liquidity sections fill.

**O19 — after Stage 3 (correctness).** `deploy/deploy.sh --live`. Its `check-book` step refuses a book that still declares a `(File …)` alert sink; remove the sink if so. Confirm `/ops` shows the desk block in words with its `clock`, `/execution` lists recent alerts and per-order TCA, the nav is in the spec's order, and the demo shows its synthetic strategy, its rebalances and its labelled synthetic dates.

**O20 — after Stage 4's options lane.** Optionally declare option contracts in an `(options …)` block plus `Greek_limit` limits, run `check-book`, then deploy. Confirm the Risk page shows indicative marks within 5 minutes during market hours. Declaring options brings Greek limits into the verdicts on stock orders.

**O21 — after Stage 4's validation lane.** Deploy. Confirm live `/execution` reads *Session n of 250* with no verdict yet, and run once:
```
docker compose -f deploy/docker-compose.yml --profile live exec ohcamel-live ohcamel replay <a recent session date>
```
If you have moved paper cash, confirm the flows appear.

**O22 — after Stage 5 (research).** Three rulings are yours:
1. Keep or veto the report-only buy-and-hold figure.
2. Acknowledge the R8 outcome. If it shipped, it is in `observe` mode: a mismatch defers a signal and is recorded but never refuses it. Once `/research` has shown no mismatch for a stretch you are satisfied with, promote it by setting `(r8 enforce)` in `book.sexp`, validating with `check-book`, and restarting `ohcamel-live`. Leaving it in `observe` forever is a legitimate choice and the page will say so.
3. Any promotion to `(sizing live)` is yours and is impossible today, because R6 refuses both EXP-A01 strategies. A new experiment is your hypothesis to pre-register.

**O23 — the final deploy (after Stage 6).** `deploy/deploy.sh --live`. View every page on both hosts behind the password, in both colour schemes. Report the smoke result, including `--live-container`, so `docs/status.md`'s Deployed row is final.

**O24 — what waits on time, needing no code.** The first live verdict for `historical`, `parametric` and `ewma` prints itself at 60 scored sessions, about early December 2026 counting from the first live close around 2026-09-14. `garch` and `cornish_fisher` reach 60 about three months after **O18a**, which is why O18a is flagged time-critical and why risk depth ships as two stages. The full 250-session window closes around September 2027.

**O25 — optional hardening.** A passphrase on `~/.ssh/id_rsa` (`ssh-keygen -p -f ~/.ssh/id_rsa`); confirm `ssh ohcamel-root 'sshd -T | grep -Ei "^(passwordauthentication|permitrootlogin)"'` shows password authentication off; a CAA record on `ohcamel` limiting issuance to letsencrypt.org through Porkbun's API; the live host's brute-force stance — accept it with the CPU alert, add a fail2ban jail over Caddy's log, or add an IP allowlist — recorded in `docs/status.md`. A move to the $12 plan is a rebuild, not a resize, because a DigitalOcean disk cannot shrink; not by default.

---

## Acceptance

1. `make build` is clean. `make test` is green at the counts `lib/verified.ml` states. `make research-test` is green at the count `verified.py` states. `dune build @fmt` is clean. `make check-counts` passes **and runs in CI's `lint` job**. `grep -rE 'paper-api\.alpaca\.markets|/v2/orders|Sqlite3|ohcamel_desk' lib/`, `grep -rn 'Writer.with_file' lib/` and `ls lib/*.mli lib/feed/*.mli` are all empty.
2. **The published tables did not move.** `make backtest`, `make backtest-crisis`, `make stress`, `make options` and `make garch`'s table rows are byte-identical to `$TMPDIR/final-baseline/*.txt`, captured from `main` at `b6132c6` in a dedicated local clone. The one permitted difference is `make garch`'s wiring sentence. `test_validation_report.ml` passes unchanged. The diffs are attached to the final report.
3. **Identities to 1e-9,** each by a named test: OLS residuals orthogonal to every regressor; Euler contributions summing to the systematic variance; systematic plus idiosyncratic equal to the model total; LVaR equal to VaR at zero spread; Cornish–Fisher equal to the normal quantile at g₁ = g₂ = 0; liquidity totals equal to the sums of their lines; the graph's GARCH and Cornish–Fisher figures equal to the pure functions on the same rows; and `Var_backtest.conditional_coverage` equal to `run`'s field on the same series.
4. **No fit runs on the tick path.** `Graph.For_testing.fits_run` is 0 across a tick, a fill, a fork and a six-leg `Gate.check`, and prices never reach `daily_stddev_long`.
5. **Hand-derived values** sit beside every numeric assertion in `test_long_panel.ml`, `test_long_window.ml`, `test_factor_model.ml`, `test_factor_model_graph.ml`, `test_liquidity.ml`, `test_liquidity_graph.ml`, `test_long_estimators.ml`, `test_alpaca_options.ml`, `test_risk_metrics.ml`, `test_validation_live.ml`, `test_var_backtest.ml`, `test_shadow.ml`, `test_tca.ml`, `test_journal_backup.ml`, `test_replay.ml` and `deploy/test/watch_test.sh`.
6. **Nothing false is left.** A grep over `web/`, `bin/`, `lib/`, `README.md`, `docs/` and `Makefile` finds no "not wired" GARCH claim, no "DISABLED" options line, no "no options-chain source", no "does not provide an options chain", no "phase A5" and no "nothing on this page makes the comparison yet". R8's sentence says observing, enforcing, or §8's item.
7. **A5 scores correctly, in every statistic it prints:** the loss sign under a deposit, the three-session record with one exceedance, the hole, the unrolled window, the flat book and unknown flows each counted, point-in-time pairing, no verdict at n = 59 and a verdict at n = 60, Kupiec LR = 0 at x = 3 of 60 and 6.15520 at x = 0, Christoffersen independence on its hand-built series, conditional coverage as the sum and equal to `run`'s field, `duration_independence` on its hand-built series and `None` below two exceedances, and the traffic-light zone at its exact boundary count for n = 60 at 0.95 — and `/api/desk/validation` returns no statistic below 60 pairs and a zone labelled at the book's confidence above it. `grep -rn 'Var_backtest.run' desk/` is empty.
8. **Every mode and route exists, is tested, and is in the usage text or `EXPECTED_ROUTES`:** `check-book`, `journal-backup`, `journal-verify`, `replay`, `serve [port] [book]`; `GET /api/desk/validation`; `/api/ops` carrying the desk block with its `clock` and the `long_window` block.
9. **Operations work in CI:** the image job builds both images with their build stamps, runs the harness smoke through `deploy/docker-compose.ci.yml`, and on `main` publishes `ghcr.io/ajaiupadhyaya/ohcamel{,-research}:<sha>`; `deploy/docker-compose.yml` has no `build:` block; `deploy.sh --dry-run --sha X` prints a pull in the asserted order and aborts before the pull when `check-book` fails, and `--dry-run --build` prints two builds and two tags; `restore.sh --drill` reads only a copy; the smoke stub test fails on a set `last_error`, a mutating route answering 200, a sha mismatch and an unknown clock source; `lint` is green over shellcheck, `make check-counts`, compose config for all three files, caddy validate, `systemd-analyze verify` and every `deploy/test/*.sh`, and the plist is linted on the macOS leg.
10. **The pages are right** on a local demo at 380 px and 1280 px in both colour schemes: Risk's four sections in order; Execution's "Session n of 250", per-order TCA and alerts; Research's synthetic strategy, R8 state and against-live; the nav in §4's order; every synthetic date and figure labelled; Figure 1 drawing A4's nodes and the signals band.
11. **Coverage is re-measured at the final count** in a dedicated clone, and `lib/verified.ml`, the README badge and `docs/status.md` agree to their stated roundings and carry the final date and machine.
12. **The documents agree:** identical limits lists in `README.md` and `docs/status.md`; every surveyed departure in §8, plus the live kill-route omission, the alerts convention, the research service's calendar and the absence of `lib/` interfaces; a `quant_notes` section for every formula, each citing its function; the environment table covering every `Sys.getenv`; the runbooks; the re-captured screenshots; a real `research/README.md`; every prior plan carrying its header and appearing in the index.
13. **The audit closes, and closes last.** The ledger's table has one row per spec item, per §5 acceptance row and per surveyed gap, each reading *done* with evidence, *departure* with its §8 item, or *owner-only* with its step. None reads *partial*. Every row's sha equals the final tip, because Task 76a re-walked the table after the final fix wave and the final coverage measure. The trace table from Task 1 has no unmapped item. The whole-project review's verdict is Ready with no open Critical or Important finding.
14. **One history.** `main` holds every stage; `git branch` shows only `main`; `git worktree list` shows only the primary checkout; `archive/a4-plan-v2` and `archive/agent-a3-leftover` resolve; `build-and-test`, `lint`, `coverage`, `research-test`, `image` and `uptime` are green on the final sha.
15. **The owner's half is recorded, not assumed.** `docs/status.md` carries, from the owner's own reports: the final sha deployed with smoke passing on both hosts; one paper order filled on the live host; an advisory signal shown with its rule on live `/research`; the live Risk page's A4 sections filled; live `/execution` reading "Session n of 250"; the date of the first session with five forecast rows, which is day 1 of the `garch` and `cornish_fisher` clock; a nightly backup present and one restore drill passed; the deploy having pulled from GHCR; Alpha archived. "Next" contains owner-time items only, each with its exact command, and the ledger records that nothing remains to build.
