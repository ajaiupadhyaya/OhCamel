# The Ship — design

*Approved in conversation 2026-10-05. This document supersedes the scope of
`docs/superpowers/plans/2026-09-19-final-completion.md` (the finish) and narrows
`docs/superpowers/plans/2026-09-24-quant-compute-program.md` (the compute
program). Both plans remain the technical reference for the parts kept here;
where they disagree with this document, this document wins.*

## 1. Intent

The owner, 2026-10-05: *"I want it finished, complete, and published in its
entirety. Emphasizing a huge consumer facing site side better, designed
perfectly, non ai non vibecoded look. brutalist but quant to the max."*

Read as success criteria:

1. **One finished product, live.** `ohcamel.ajaiupadhyaya.com` serves every
   page below, from `main`, deployed by the agent, verified by the smoke suite.
   Nothing that matters lives only on a local branch.
2. **The public site is the centrepiece.** Every route is rebuilt in one design
   system ("Paper Tape", §3). No page keeps the current dark-SaaS look.
3. **Quant to the max, honestly.** The site shows real computation — Monte
   Carlo risk, forecast leagues, a strategy farm, trained models — produced by
   scheduled jobs on the droplet, each with provenance, `data_asof` and a
   verdict. Missing data is shown as missing; nothing is invented.
4. **Finished means a stated boundary.** What was cut is listed on a public
   Ledger page, so "finished" is a claim a reader can check.

Owner decisions taken 2026-10-05: scope = ship plan + compute (all four compute
pieces); look = Paper Tape; the Flight Deck stays the one dark room; the agent
pushes to GitHub and deploys to the droplet.

## 2. Scope

### Kept

| Workstream | Source of truth | Notes |
|---|---|---|
| **S0 Ops close** | finish plan Tasks 10, 11, 14–20 (8, 9, 12, 13, 13a are integrated on `final/s1-ops`) | Only what makes deploys safe and recoverable: image workflow, non-root research image, backup service + restore drill, smoke suite sees the desk, `deploy.sh` deploys a sha, uptime/cert watch, on-host watch, runbooks. Review is one fresh reviewer per task, no mutation batteries. |
| **Phase 0 remainder** | compute plan 0.3 (resource governance), 0.4 (`hostd`), 0.5 (latency baselines / G-RT) | As written. |
| **Lane A — Rust kernels** | compute plan Lane A (A1–A7), contract II.4 | As written. |
| **Lane B — jobs** | compute plan Lane B (B1–B6), contracts II.2–II.3 | As written. |
| **Lane C — warehouse** | compute plan Lane C (C1–C6), contract II.5 | As written. |
| **Lane M — products** | compute plan Lane M (M1–M8) | P1–P7. M6 (EXP-Q01) and M7 (EXP-Q02) need owner-approved pre-registrations (§6). |
| **Lane P — Paper Tape design system** | **this document §3** | Replaces the compute plan's Lane D ("Terminal", dark-first). Keeps D2's primitives, D3's formatting discipline, D4's uPlot charts and bundle rule, D5's SSE live layer. |
| **Lane P2 — pages** | this document §4 | Replaces compute plan Lane D2. |
| **Lane F — Flight Deck** | compute plan Lane F (F1–F3), §3.7 here | F4 kiosk is cut. |
| **Lane H — harden and launch** | compute plan Lane H (H1–H6) | Agent deploys (H5 is no longer an owner step). |

### Cut

Listed on `/ledger` with one line each saying what it would have been:

- Finish plan Stages 2A–6 (Tasks 21–77): OCaml engine depth — the remaining
  risk-depth, desk and research-battery tasks. The engine as deployed stays,
  and its page stays.
- Compute plan Lane E (scenario grid P8, C stubs).
- Kiosk mode (F4), saved per-route layouts, density modes (`data-density`).

## 3. Paper Tape

A printed risk report, not an app. The reference points are a broadsheet
markets page, a chart-recorder strip and a Swiss railway timetable: ink on
paper, rules instead of boxes, numbers set like type.

### 3.1 Tokens (`quant/web/src/styles/tokens.css`, rewritten)

| Token | Paper (default) | Carbon (dark) |
|---|---|---|
| `--paper` ground | `#F4F1EA` | `#121211` |
| `--paper-2` alternate band | `#ECE8DF` | `#1B1B19` |
| `--ink` text, rules, gains | `#111111` | `#ECE8DF` |
| `--ink-2` secondary text | `#5E5B54` | `#9A968C` |
| `--ink-3` tertiary / gridlines | `#B9B4A8` | `#45433E` |
| `--signal` losses, breaches, alerts, the one accent | `#C8201A` | `#FF4A3D` |
| `--stale` | `--ink-3` with a hatched fill | same |

- Gains are `--ink` with an explicit `+`. There is no green anywhere outside
  the Deck.
- Radius 0. No shadows. No gradients. No `body::before` wash.
- Rules: `--rule` 1px `--ink`; `--rule-heavy` 2px; `--rule-double` (3px
  double) under the masthead and above footers; `--rule-faint` 1px `--ink-3`
  inside tables.
- Spacing on a 4px grid. Table row height 22px.
- A Vitest test parses `tokens.css`, asserts every token exists in both
  themes, and checks text contrast ≥ 4.5:1 against both grounds (WCAG
  relative luminance).

### 3.2 Type

| Role | Face | Setting |
|---|---|---|
| Display (masthead, page titles, section heads) | **Archivo** variable, width 62–75, weight 800 | Uppercase, tracking −0.01em, titles 40–64px, section heads 13px with +0.08em tracking |
| Text and UI | **IBM Plex Sans** 400/500/600 | 14px base, 13px in tables |
| Every number | **IBM Plex Mono** 400/500, `font-variant-numeric: tabular-nums` | Right-aligned in tables; signs always shown on changes |

Fraunces and Inter are removed from `package.json` and `main.tsx`. Fonts are
self-hosted via `@fontsource`.

### 3.3 Shell (`quant/web/src/shell/`, rebuilt)

- **Masthead:** `OHCAMEL/QUANT` in Archivo, a dateline (`MON 05 OCT 2026 ·
  16:00 ET · SESSION CLOSED`), and a status cluster (data freshness, engine,
  jobs, CPU) as small mono readouts with square lamps. A double rule below.
- **Tape:** one line under the masthead scrolling the index and rates tape
  (pauses on hover; static under `prefers-reduced-motion`).
- **Nav:** a horizontal row of function codes —
  `FRONT MKTS RISK PORT OPT RSCH VOL RATES CO DECK SYS` — with the full name
  on hover and the active code inverted (paper on ink). No sidebar, no icons.
  On narrow screens it scrolls horizontally inside its own row; the page never
  scrolls sideways.
- **Command line:** `/` or `⌘K` opens a one-line input under the nav. A pure,
  tested `parseCommand(s) → Command` handles function codes (`SPY GP`,
  `AAPL DES`, `PORT RISK 99 10D`, `JOB FARM`, `GO RATES`) plus page and
  ticker search. Global keys: `g` then a letter for nav, `?` for the key map.
- **Footer:** a double rule, then build sha, data sources, and links to
  Methodology and Ledger.

### 3.4 Primitives (`quant/web/src/design/`)

- `Sheet` — the page: title block (code, Archivo title, dateline, status
  chips; no subtitle), then a 12-column grid.
- `Cell` — a grid region with a ruled header strip (13px Archivo caps title,
  `asOf`, actions on the right, stale hatch when past its max age). Cells
  separate by rules, never by gaps with boxes.
- `Figure` — a big-number block: label, value in Plex Mono at 32–72px,
  change, and a one-line footnote. Used for headline readings.
- `Table` — DataTable v2: 22px rows, faint inner rules, heavy header rule,
  sticky first column, keyboard row navigation, `format` required on numeric
  columns, sign colouring = ink/`--signal` only.
- `Verdict` — a stamped label (`PASS`, `FAIL`, `ADVISORY`, `INSUFFICIENT
  DATA`) in Archivo caps inside a 2px ink border; `FAIL` is red. Every
  research and model result leads with one.
- `Note` — footnote-style text with superscript markers that link to
  Methodology entries. Replaces info-tips and subtitles.
- `Lamp` — a square status indicator: filled ink (ok), hollow (idle),
  `--signal` (fault), hatched (stale/unknown).

### 3.5 Charts (`quant/web/src/charts/`, uPlot)

Line, step, area-to-baseline (hatched, not gradient), bar, histogram,
heatmap (ink density ramp on paper, diverging ink↔signal for signed data)
and a sparkline. Ink strokes at 1.25px, gridlines `--ink-3` dotted, direct
end-of-line labels in Plex Mono instead of legends, and a crosshair readout
strip above the plot. Plotly stays only in the 3-D surface view and is loaded
only there. `scripts/check-bundle.mjs` fails the build if any other chunk
imports Plotly or the entry exceeds 250 KB gzip.

### 3.6 Copy

Labels, not sentences: `VAR 99 · 1D · FHS`, not "How much could I lose
tomorrow?". No page or cell subtitles. No question-shaped headers. No
onboarding boxes. Explanation lives in Methodology, reached through `Note`
markers. Numbers carry units. Every computed panel says where its data came
from (provenance line) and when (`asOf`).

### 3.7 The Flight Deck (the one dark room)

`/deck` renders full-bleed in its own dark instrument palette, without the
paper shell's grid (the masthead, nav and footer remain, inverted). It is
rebuilt for precision rather than costume: Plex Mono readouts, hairline
instrument graticules, seven-segment counters only where a real counter is
shown, no glow blur, no scan-line overlay. The limit corridor, risk radar
and lamp panel stay (their pure modules and Vitest tests are kept). The lamp
panel gains a compute bank (running job kinds, CPU and memory from
`/api/ops/host`). Motion is data-driven only and stops under reduced motion.
The limits editor and marks move to a right drawer (`d`).

### 3.8 Accessibility and responsiveness

axe (via Playwright) with zero serious violations on every route; 380px
without horizontal page scroll; full keyboard operation; both themes
screenshotted per route into `docs/media/quant/paper/`.

## 4. Pages

| Code | Route | Contents (new items in **bold**) |
|---|---|---|
| FRONT | `/` | **The broadsheet:** index tape figures, the curve and 2s10s, **regime probabilities (P6)**, **risk-atlas headline VaR/ES (P1)**, **farm verdict count — passed vs failed, failures first (P4)**, **model IC and verdict (P5)**, **last night's compute: core-hours, jobs, artifacts**, data freshness. Each block links to its page. |
| MKTS | `/markets` | Today's markets (the current Markets page): indices, sectors heatmap, rates, credit, commodities, vol. |
| RISK | `/risk` | **New route.** Portfolio risk (moved out of Portfolio Lab) + **Atlas (P1)**: MC VaR/ES for reference books and universe members, FHS vs t-copula, 1D/10D, Euler split. |
| PORT | `/portfolio` | Portfolio Lab: holdings, performance, factors, stress; **Covariance league (P3)**. |
| OPT | `/optimize` | Optimizer, unchanged in function. |
| RSCH | `/research` | Strategy Lab · **Farm (P4)** leaderboard, verdict first · **Models (P5)** with model card, IC, decile spreads, gate verdict. |
| VOL | `/options` | Volatility · **Forecasts (P2)** league with QLIKE/MSE, DM tests, MCS · **Surface history (P7)** (shows "n days of history" honestly while it accrues). |
| RATES | `/macro` | Rates & Macro · **Regimes (P6)** filtered and smoothed probabilities. |
| CO | `/company/:ticker` | Company, unchanged in function. |
| — | `/ticker/:ticker` | Ticker, unchanged in function. |
| DECK | `/deck` | Flight Deck (§3.7). |
| SYS | `/system` | Index of the four below. |
| — | `/compute` | **New.** `hostd` CPU per slice, steal, memory, swap; queue, running jobs with progress (SSE), last 24h with cpu_seconds and peak RSS; schedule with next runs; artifacts linked to their pages; kernel benchmark table (Rust vs Python). |
| — | `/engine` | Live Engine, restyled. |
| — | `/methodology` | Methodology, with entries for P1–P7 and the kernels. |
| — | `/ledger` | **New.** What exists, what was cut (§2), what is advisory, known limitations (e.g. survivorship in universes), owner-held items. Dated. |

Old URLs keep working: `/` content moves to `/markets` with the Front Page
taking `/`; no other path changes.

## 5. Architecture

Unchanged from the compute program Parts I–II: three tiers on the one droplet
(protected OCaml engine; interactive API + Caddy + `hostd`; preemptible
worker), `jobs.sqlite`, immutable artifacts under `/data/artifacts`, DuckDB
warehouse written only by the worker, Rust kernels via PyO3 with a NumPy
reference and parity tests, SSE for live updates. The droplet stays
`s-2vcpu-4gb`; memory binds first, so heavy jobs run one at a time outside
the session window.

## 6. Governance

- **quant-rigor applies** to every research and model product: purged
  walk-forward CV, costs included, DSR/PSR/PBO reported, verdict first.
  Most strategies will fail and the page says so.
- **Pre-registrations.** EXP-Q01 and EXP-Q02 are governed by
  `research/experiments/EXP-Q0{1,2}/preregistration.md`, both approved by the
  owner 2026-10-06. Their gates are `docs/CHARTER.md`'s, applied literally
  (amended 2026-10-06: the earlier "DSR > 0.95, PBO < 0.2" here was not the
  charter's). EXP-Q01 runs on the survivorship-free ETF universe, because the
  charter rules single-name cross-sectional claims out of scope until a
  survivorship-free source exists (owner option A). A product whose
  pre-registration is not approved shows `AWAITING PRE-REGISTRATION`.
- Nothing from the models reaches the desk; all model output is advisory.
- `book.sexp` on the droplet is not edited by the agent (live trading stays
  the owner's switch).

## 7. Process

- Integration branch `ship/main`, cut from `main` at `88ebc96`. Lanes work in
  worktrees on `ship/<lane>` branches and merge into `ship/main`; `ship/main`
  merges to `main` and is pushed at each phase end.
- Review: one fresh reviewer per lane task group and one whole-lane review
  before merge, then a fix wave. No per-task mutation batteries.
- Phase order: **Phase 0** (S0 ops close; 0.3–0.5; merge; push; deploy the
  current site) → **Phase 1** (A, B, C, P in parallel) → **Phase 2** (M, P2,
  F) → **Phase 3** (H: review, security pass, docs, deploy, five-session
  soak).
- Every phase ends with tests green (`dune runtest`, `pytest -q`,
  `npm test`, `npm run build`, lint), a push, and — from Phase 0 on — a deploy
  verified by the smoke suite, with the previous sha recorded as rollback.

## 8. Acceptance

1. Every route in §4 renders in Paper Tape (Deck in its dark palette), passes
   axe with zero serious violations, works at 380px, both themes,
   screenshotted.
2. `rg "Fraunces|Inter Variable|oc-page-title|body::before|\.toFixed" quant/web/src`
   is empty outside `lib/format.ts`; the bundle rule is green.
3. P1–P7 each have run on the droplet for five consecutive sessions with
   artifacts present and `data_asof` current; P5/P6 either show results under
   an approved pre-registration or `AWAITING PRE-REGISTRATION`.
4. Kernel parity suite green with Rust and with `OHCAMEL_QUANT_KERNELS=python`;
   benchmark table published on `/compute`.
5. Backup restore drill passed once on the droplet.
6. `main` pushed; droplet runs `main`'s sha; production smoke suite green;
   `docs/status.md` "Deployed" row updated; `/ledger` dated and accurate.
