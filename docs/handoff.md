# OhCamel — Handoff Brief for Claude Code

**Repo:** `ajaiupadhyaya/OhCamel` — a reactive risk-and-limits engine in OCaml, built on
Jane Street's `Incremental` self-adjusting computation library.
**Owner:** AJ Upadhyaya (CS + Econ, UVA, class of 2026) — targeting quant trading /
risk roles at Jane Street, Citadel, JPMorgan and similar firms.
**Purpose of this document:** context + prioritized, spec-level roadmap so Claude Code
can extend this project without eroding what already makes it unusual. Read this whole
file before touching code. Then read `README.md` and `lib/graph.ml` in the repo itself —
this brief summarizes them but the repo is the source of truth.

---

## 0. Why this brief exists, and how to use it

This is already a genuinely strong project — not a "student risk dashboard." It has a real
architectural thesis (incremental recomputation instead of polling), it proves that thesis
with counted recomputation tests, it implements an exact Euler risk decomposition, it
validates its own VaR model with Kupiec/Christoffersen/Basel statistics instead of just
reporting a number, and its prose comments explain *why* a design choice was made, not
just *what* the code does. That combination — correctness-first OCaml, honest treatment of
model limitations, an engine that documents its own failure modes — is exactly the signal
that trading firms filter resumes for.

The risk with a "make it more impressive" pass is diluting that signal: bolting on
features that look good in a README bullet list but don't carry the same rigor, or that
quietly break the one invariant the whole architecture depends on (§2 below). So this
roadmap is deliberately conservative about *what* to add and strict about *how*. Every
phase below is scoped to be defensible in a live interview — AJ should be able to say
"I added X because Y was the weakest assumption the README already admitted to, here's
the math, here's the test that would catch a regression" for anything Claude Code ships.

**Work in phases, one PR-sized piece at a time.** Do not attempt all of §4 in one pass.
Each phase has its own acceptance criteria; treat those as a definition of done, run
`make test` and `make fmt` before considering a phase finished, and update `README.md`
in the same style as the existing prose (see §5) so the repo's own documentation never
falls behind the code.

---

## 1. What OhCamel is today (context reset)

- **Engine (`lib/graph.ml`):** an `Incremental` dependency graph. Input cells are prices,
  quantities, cash, return windows, a single macro factor series, and wall-clock time.
  Every derived quantity — exposure, gross/net, equity, drawdown, covariance, VaR,
  expected shortfall, portfolio beta, limit breaches — is a node with declared edges.
  A tick recomputes exactly its downstream, not the whole book. This is proven, not
  claimed: `make run` prints recomputation counts at three book sizes and
  `test/test_graph.ml` asserts them.
- **Types (`lib/types.ml`):** `Price`, `Qty`, `Notional` are abstract and mutually
  incompatible; `Symbol` and `Sector` are distinct string-backed types. Unit bugs are
  compile errors, not runtime surprises.
- **Risk math (`lib/risk_metrics.ml`):** historical VaR, parametric (variance-covariance)
  VaR, expected shortfall, covariance, portfolio beta — pure functions, no `Incremental`
  dependency, unit-tested against hand-derived values.
- **Decomposition (`lib/attribution.ml`):** exact Euler split of portfolio volatility into
  per-instrument and per-sector component VaR. Signs preserved (a hedge can have negative
  component risk). This is the project's best single artifact — it's the kind of result
  that shows up in a Citadel/Jane Street risk-analyst interview question, implemented
  correctly and tested for the exact bug (a stray `abs`) that would silently break it.
- **Backtesting (`lib/var_backtest.ml`):** rolling-origin, point-in-time VaR forecasts
  scored against realized returns, with Kupiec (unconditional coverage), Christoffersen
  (independence), conditional coverage, and the Basel traffic-light zones reproduced
  from first principles (binomial, not a lookup table) and tested against the published
  250-day zone boundaries.
- **Stress testing (`lib/stress.ml`):** price/sector/idiosyncratic/factor/volatility
  shocks applied by forking the live engine (`Graph.fork`) and re-reading it through the
  same nodes — deliberately zero duplicated arithmetic.
- **Limits (`lib/limits.ml`):** breach computed as data (bool + magnitude), no side
  effects. Four limit kinds, with `scope_is_valid` correctly rejecting a standalone-VaR
  limit on a single name (non-additive) while allowing `Component_var` everywhere
  (additive by construction).
- **Alerting (`lib/alerts.ml`):** edge-triggered with hysteresis, off by default, and a
  kill switch that only ever sets a bool — there is no order-routing code anywhere in
  the repo, by design.
- **Feed (`lib/feed/`):** Alpaca websocket + REST backfill, FRED for the macro factor.
  Missing credentials are fatal, not silently degraded.
- **Serving (`lib/server.ml`, `lib/dashboard_html.ml`):** `/api/snapshot`, `/api/health`,
  and an SSE stream at `/api/stream` that only emits on an actual graph change (parked on
  an `Ivar`, not a timer). Dashboard is a single embedded HTML string, live-only, no
  history.
- **Tests:** 140 hermetic tests (`make test`) covering numerics against hand-derived
  values, the wire format, the alerting state machine, and — the four tests worth
  knowing by name in an interview — the Euler residual check, the hedge/no-abs test, the
  backtest lookahead-isolation test, and the stress-fork isolation test.
- **CI:** single Ubuntu job (`.github/workflows/ci.yml`) — build, test, `ocamlformat`
  check. No benchmarking, no coverage, no macOS matrix (macOS has documented Owl
  workarounds in the `Makefile` that aren't exercised in CI).

**What the README already, honestly, says is missing:** EWMA/GARCH volatility (equal-
weighted is called out as "the weakest assumption"), any persistence/replay, any
multi-broker or multi-macro-source support, any strategy/signal/execution layer
(explicitly out of scope, not a gap).

---

## 2. Invariants Claude Code must not break

These are the rules that make the architecture the point. Violating any of them to ship
a feature faster is a regression even if the tests still pass.

1. **Every dependency is a graph edge.** No new node may read a global, a mutable ref, or
   call out to the network from inside a node body. If a new metric needs an input, it's a
   new `Var.t` cell with a declared edge, following the existing pattern in `graph.ml`,
   including the "why does this depend on this" comment above the edge.
2. **No second implementation of exposure/equity/limits arithmetic.** Anything that needs
   "what would the book look like under X" must go through `Graph.fork`, the way
   `stress.ml` does it — never reimplement P&L math in a new module.
3. **Units stay abstract.** New quantities (Greeks, FX-adjusted notionals, factor
   exposures) get their own types in `types.ml` or a sibling module, with the same
   "the only bridge is one named function" discipline `Price`/`Qty`/`Notional` use. No
   bare floats crossing module boundaries for anything money- or risk-shaped.
4. **Pure numerics stay pure.** `risk_metrics.ml`-style modules must not know
   `Incremental` exists, so they stay independently unit-testable. New risk math
   (EWMA, GARCH, Greeks, EVT/Cornish-Fisher) belongs in a new pure module of this kind,
   wired into the graph the way `risk_metrics.ml` is wired in today.
5. **A missing credential is still fatal, not degraded.** Any new external data source
   (a second broker, options chain data, an FX rate feed) follows `Config.Credentials`'s
   pattern: fail loud at startup, never fall back to synthetic data silently in "live" mode.
6. **No order routing, ever.** The kill switch stays a bool with nothing wired to it.
   Do not add anything that places, cancels, or simulates submitting an order to a real
   or paper account. (An internal "suggested rebalance size, given the risk budget"
   *read-only* calculation is fine — it's the same category as component VaR — but it may
   never touch a broker API.)
7. **Every new numeric module gets hand-derived test values**, in the style of the
   existing tests (e.g. the three-name book where return series are a series and its
   negation, so risk shares reduce to weight magnitudes exactly). A test that only checks
   "the function returns a number" is not acceptable here; that's the project's whole
   standard and it's what differentiates it in review.
8. **Prose comments explain *why*, not *what*.** Match the existing voice: a short
   statement of the design tension, the choice made, and what would go wrong with the
   alternative. Do not write comments that just restate the code.

---

## 3. Gap analysis, mapped to what each target firm actually screens for

| Gap | Why a reviewer at these firms would notice it |
|---|---|
| Volatility is equal-weighted only | README already flags this as the weakest assumption. Any quant reader (Citadel, Jane Street) will ask "so what happens in a regime shift" — right now the honest answer is "the vol estimate lags." |
| Single macro factor, no options/derivatives | Jane Street and Citadel are both heavily options-driven. A risk engine with no notion of delta/gamma/vega reads as equities-only, which undersells the "risk engine" framing. |
| No real historical crisis data in the backtest | `var_backtest.ml` is validated against deterministic synthetic series. Running the same coverage battery against a real crisis window (2008, Mar 2020, 2022 rate shock) using data the feed can already fetch (Alpaca REST backfill) is a much stronger claim than "hermetic and deterministic." |
| No benchmark artifact beyond the `make run` table | The recomputation-count table is a great *architectural* proof but says nothing about wall-clock latency or allocation behavior — the two things a low-latency-adjacent interviewer actually cares about. |
| No property-based tests | 140 example-based tests is good; a small `qcheck` suite that asserts invariants (Euler additivity for arbitrary weights/covariances, VaR monotonicity in confidence, drawdown ≥ 0) generalizes the existing hand-derived tests and is a one-line addition to a Jane Street-style pitch ("I property-tested the risk identities"). |
| Dashboard has no history | Live-only view is consistent with "no persistence" as a stated design choice, but a reviewer skimming the demo GIF sees one frozen number, not a track record. A bounded in-memory time series (still no disk persistence, so the invariant survives) makes the demo materially more convincing in 30 seconds of looking at it. |
| CI doesn't run on macOS despite the Makefile carrying macOS-specific workarounds | Small, cheap fix; a CI matrix that actually exercises the documented `OWL_CFLAGS`/`OWL_LDLIBS` workaround is more credible than prose describing a workaround nothing runs. |
| No written derivation of the math outside README prose | The README's math is good but embedded in narrative prose. A short, dense `docs/quant_notes.md` (or PDF) with the VaR/ES formulas, the Euler decomposition proof sketch, and the Kupiec/Christoffersen test statistics in standard notation is exactly the artifact to attach to an application or bring to a technical interview. |

---

## 4. Roadmap

Phases are ordered by resume/interview leverage per unit of engineering risk, **not**
strictly by dependency — but 4.1 and 4.2 should come before 4.3 since the crisis
backtest in 4.3 is more convincing once EWMA vol exists to compare against equal-weighted.

### Phase A — EWMA (and optionally GARCH(1,1)) volatility estimator

**Why:** closes the gap the README already names. Directly answers "what's the weakest
assumption in your model, and did you fix it" — one of the most predictable quant
interview questions to ask about a risk-model project.

**Spec:**
- New pure module `lib/vol_estimators.ml` (no `Incremental` dependency), following the
  `risk_metrics.ml` pattern exactly.
- `Ewma.covariance : returns:float array array -> lambda:float -> Owl.Mat.mat` (or
  equivalent), decaying weights `(1-λ)λ^k`, RiskMetrics' standard λ=0.94 (daily) as the
  documented default, with the choice justified in a comment.
- Optionally `Garch11.fit : returns:float array -> ...` — a maximum-likelihood GARCH(1,1)
  fit is more work and more failure-prone to get right; treat this as a stretch goal
  behind the EWMA estimator, not a blocker.
- Wire a **new node**, not a replacement — `graph.ml` should expose both
  `covariance_equal_weighted` (existing) and `covariance_ewma` (new) as sibling nodes off
  `aligned_returns`, and `parametric_var`/`attribution` should be parameterizable over
  which one they read, defaulting to equal-weighted for backward compatibility unless a
  config flag says otherwise. This preserves invariant #1 and makes the *comparison*
  between the two estimators a first-class, displayable thing rather than a silent swap.
- Dashboard/CLI: show both VaR numbers side by side where space allows, the same way the
  README already contrasts historical vs. parametric VaR ("the two disagreeing is the
  tail-fatness diagnostic" — do the same move for equal-weighted vs. EWMA: disagreement
  is a regime-change diagnostic).

**Acceptance criteria:**
- Hand-derived unit test: a short synthetic return series where the EWMA covariance can
  be computed by hand (e.g. 4–5 points, λ chosen so the arithmetic is checkable) and
  compared to the estimator's output.
- A test showing EWMA responds faster than equal-weighted to a volatility regime break
  inserted partway through a synthetic series (this is the actual point of the feature —
  test the property, not just the formula).
- `make backtest` extended (or a new `make backtest-ewma`) to run the existing Kupiec/
  Christoffersen suite against VaR computed from EWMA covariance, and the README updated
  with the resulting table, mirroring the existing backtest section's tone.

---

### Phase B — Options/derivatives risk: Greeks-aware exposure

**Why:** the single highest-leverage addition for Jane Street and Citadel specifically —
both trade options at scale. Turns "an equity risk engine" into "a risk engine," full
stop, and gives AJ a legitimate story about Black-Scholes, implied vol, and delta-hedged
risk in an interview.

**Spec — keep this additive, not a rewrite:**
- New types in a `lib/options.ml`: `Strike.t`, `Expiry.t`, `Implied_vol.t`, and an
  `Option_position.t` (underlying `Symbol.t`, strike, expiry, call/put, quantity in
  contracts). Keep these as distinct types from `Types.Qty`/`Types.Notional` — a contract
  count is not a share count (invariant #3).
- Pure pricing/greeks module, no `Incremental` dependency: Black-Scholes price, delta,
  gamma, vega, theta, as ordinary functions of (spot, strike, time-to-expiry, rate,
  implied vol). This is the same "pure numerics, independently testable" pattern as
  `risk_metrics.ml`.
- Graph integration: an options position's **delta-equivalent exposure**
  (`delta * multiplier * spot`) is a new node that folds into the *existing*
  `exposure[S]` aggregation — do not build a parallel exposure system. Gamma and vega are
  reported as separate per-instrument and portfolio-level nodes (`portfolio_gamma`,
  `portfolio_vega`), since they don't have a natural place inside the linear exposure
  sum.
- A new limit kind, `Portfolio_vega` (or `Greek_limit` generalized over which Greek),
  added to `Limits.t` alongside the existing four, with `scope_is_valid` reasoned about
  the same way the existing limits are (additive Greeks are valid at any scope; is
  vega additive across names the way exposure is? — work this out explicitly in a
  comment the way `Component_var`'s validity argument is worked out today).
- Implied vol source: for the demo/synthetic book, a plausible flat or smiled IV surface
  generated alongside the synthetic price feed is fine and should be labeled as such.
  For live mode, this is genuinely hard (need an options-chain data source Alpaca's
  free tier likely doesn't give you) — it's fine, and honest, to ship live-mode options
  risk as **disabled with a clear message** ("no options chain data source configured")
  rather than force a live integration. Do not fake a "live" options feed.

**Acceptance criteria:**
- Hand-derived tests for Black-Scholes price and each Greek against known textbook
  values (e.g. Hull's textbook examples) at multiple moneyness levels.
- A put-call parity test (`call - put = spot - strike * discount`), which is the
  standard "does your pricer actually make sense" sanity check and is cheap to write.
- A test that a delta-hedged position (long stock + short calls sized to zero out
  delta) reads ~0 delta-equivalent exposure but nonzero gamma/vega — this is the
  options analogue of the existing "hedge doesn't breach a risk limit" test and is
  exactly the kind of test a Jane Street interviewer would want to see you think to write.
- README section in the existing prose style ("Where the risk is" already exists for
  equities — add a parallel subsection for the options case).

---

### Phase C — Real historical crisis backtesting

**Why:** upgrades the backtest validation from "hermetic and deterministic" to "tested
against actual tail events," which is a materially stronger claim and costs relatively
little given the Alpaca REST backfill already exists.

**Spec:**
- New `make backtest-crisis` target (or an argument to the existing backtest binary)
  that pulls real historical daily bars for a fixed small universe (reuse the existing
  synthetic book's symbols, or a documented alternative set) across three windows:
  2008-09 to 2009-03 (GFC), 2020-02 to 2020-04 (COVID crash), 2022 (rate-shock, slower
  drawdown — a useful contrast to the other two sharp shocks).
- Run the *existing* `Var_backtest.rolling` machinery unchanged against this real data —
  do not write new backtest logic; the point is reusing the validated harness on harder
  data, per invariant #2's spirit (no parallel implementations).
- Cache the fetched historical bars to disk (a simple CSV or sexp cache keyed by
  symbol+date range) so `make backtest-crisis` is repeatable without hammering Alpaca's
  API or requiring credentials on every CI run — but do **not** silently fall back to
  synthetic data if the cache is missing and no credentials are set; fail with a clear
  message pointing at how to populate the cache (consistent with invariant #5).
- README table in the same format as the existing backtest table, with commentary in the
  existing analytical voice: which windows the model failed in and *why* (equal-weighted
  vol during a sharp vol spike is the expected failure mode — tie this back to Phase A's
  EWMA comparison if that phase is done first).

**Acceptance criteria:**
- The cached crisis data and the backtest table it produces should be checked into
  `docs/` (small CSVs, not huge raw dumps) so the result is reproducible for a reviewer
  who doesn't have API keys.
- A short written paragraph (this can go straight into the README) stating plainly
  whether the model passed or failed Kupiec/Christoffersen in each window and why —
  mirroring the existing honesty about the two rejected synthetic configurations
  ("The suite rejects two of six configurations. That is the point.").

---

### Phase D — Latency/allocation benchmark suite

**Why:** the architectural claim ("cost of an update scales with the change, not the
book") is currently proven in *node-count* terms only. A wall-clock/allocation benchmark
is the natural next proof point and is the kind of artifact that resonates with
performance-conscious interviewers (this matters more at Jane Street/Citadel than at a
bank).

**Spec:**
- New `bench/` directory (own dune stanza, doesn't ship in the main binary) using
  `core_bench` (Jane Street's own benchmarking library — thematically consistent with
  the rest of the stack) to measure: (a) time per tick at the three book sizes already
  used in the `make run` table, (b) allocation (minor/major words) per tick, (c) the same
  two numbers for a naive "recompute everything" baseline implemented as a throwaway
  ~30-line function, to make the polling-vs-incremental contrast quantitative instead of
  just architectural.
- `make bench` target added to the `Makefile`, following the existing target style.
- Results published as a table in the README, directly under the existing recomputation-
  count table, so the two proofs sit together: "here's what we recompute, here's what
  it costs in wall-clock and allocations."

**Acceptance criteria:**
- Benchmarks are deterministic enough to be re-run and reported (note variance/hardware
  in the README the way the Owl macOS section already documents environment-specific
  caveats).
- CI does **not** need to run `make bench` on every push (benchmark numbers on shared CI
  runners are noisy and not meaningful) — a `workflow_dispatch`-triggered job or a
  documented "run this locally and paste results" step is the right scope; don't over-
  engineer CI here.

---

### Phase E — Property-based tests for the risk identities

**Why:** generalizes the project's existing best tests (Euler residual, hedge/no-abs,
backtest lookahead-isolation, stress-fork isolation) from specific hand-derived examples
to arbitrary inputs, which is a stronger correctness claim and a one-sentence addition to
an interview pitch ("I property-tested the Euler decomposition, not just spot-checked it").

**Spec:**
- Add `qcheck`/`qcheck-core` as a test-only dependency.
- Properties to encode, at minimum:
  - **Euler additivity:** for random positive-semidefinite covariance matrices and random
    weight vectors, `sum(component_var_i) ≈ portfolio_var` within float tolerance.
  - **No-abs / hedge property:** for a randomly generated book, a position whose returns
    are the *negation* of the rest of the book's dominant factor reduces total portfolio
    variance relative to removing it — encode this as a property, not a single fixed
    example (the existing hand test stays; this generalizes it).
  - **VaR monotonicity:** historical and parametric VaR are both non-decreasing in
    confidence level, for arbitrary return series.
  - **Stress-fork isolation:** for randomly generated scenarios, the live snapshot is
    unchanged field-for-field after any stress run (generalizes the existing
    `test_stress.ml` isolation test across random scenario parameters instead of the
    fixed suite).
  - **Backtest lookahead:** for random window sizes and series lengths, the rolling
    estimator at time *t* never reads index *t* or later (generalizes the existing fixed
    lookahead test).
- Keep these in a new `test/test_properties.ml`, separate from the existing example-based
  test files, so the two testing styles stay legible as distinct and both remain in CI.

**Acceptance criteria:**
- All five properties pass with a documented number of random trials (100+ per property
  is reasonable for CI runtime).
- `make test` still runs in CI's existing timeout budget; if `qcheck` trial counts make
  this too slow, reduce trials in CI and note a higher local-only trial count in the
  Makefile comment, rather than dropping properties.

---

### Phase F — Dashboard: bounded in-memory history + charts

**Why:** the live dashboard currently shows one frozen number per metric. A short rolling
window of history (still zero disk persistence — the "no persistence" design choice is
explicitly preserved, this is purely an in-memory ring buffer) makes the 30-second demo
GIF read as "a system with a track record" instead of "a snapshot."

**Spec:**
- A new **observer**, not a graph node (consistent with `lib/alerts.ml`'s pattern of
  hanging off `Graph.on_change` rather than living inside the dependency graph), that
  appends `(time, gross, net, var_notional, es_notional, drawdown)` tuples to a fixed-
  capacity ring buffer (e.g. last 500 points) in `lib/server.ml` or a new
  `lib/history_buffer.ml`.
- New `/api/history` endpoint serving the buffer as JSON.
- Dashboard: a lightweight, dependency-free sparkline/line-chart rendered from
  `/api/history` (plain SVG generated server-side or a small inline JS canvas snippet —
  keep the "self-contained binary, no external JS dependency" property `dashboard_html.ml`
  already has; do not pull in a charting library over CDN).
- Explicitly document in a comment that this buffer is in-memory only and resets on
  restart, same as the rest of engine state — do not let this quietly become a
  persistence layer by accident (invariant discipline extends to new code even where
  the existing invariants list doesn't literally cover it).

**Acceptance criteria:**
- A test that the ring buffer evicts oldest entries correctly at capacity.
- A test that `/api/history` returns valid JSON matching the existing wire-format
  conventions used by `/api/snapshot`.
- Updated demo screenshot/GIF in `docs/media/` showing the chart (regenerate the assets
  the README already links).

---

### Phase G — CI: macOS matrix + coverage

**Why:** cheap, low-risk, and closes the credibility gap between "the Makefile documents
macOS-specific Owl workarounds" and "CI never exercises them."

**Spec:**
- Add a `macos-latest` entry to the CI matrix in `.github/workflows/ci.yml`, exporting
  the `OWL_CFLAGS`/`OWL_LDLIBS` the `Makefile` already sets, so CI is actually proving the
  documented workaround still works rather than trusting prose.
- Add `bisect_ppx` (or dune's built-in coverage support) for a coverage report, uploaded
  as a CI artifact or badge. Coverage-as-a-badge is a small, standard signal reviewers
  glance at; don't over-invest here beyond wiring it up correctly.

**Acceptance criteria:**
- Green CI on both `ubuntu-latest` and `macos-latest`.
- A coverage percentage visible in the README badge row, next to the existing CI badge.

---

### Phase H — `docs/quant_notes.md`: the math, written once, densely

**Why:** a reviewer skimming a resume link wants the derivations in one place, in
standard notation, without narrative — the README's prose is excellent for a human
reading start to finish but isn't the fastest reference for someone checking "does this
person actually understand what Kupiec's test is doing."

**Spec:**
- One dense markdown (or LaTeX → PDF, if AJ wants a linkable artifact) document covering,
  each in under half a page with real notation:
  - Historical VaR / ES definitions and the nearest-rank tail convention actually used
    in `risk_metrics.ml` (cite the epsilon-rounding fix in that file as a footnote — it's
    a nice "I found and fixed a real bug" detail).
  - Parametric (variance-covariance) VaR and the Gaussian assumption's limitations.
  - The Euler decomposition derivation (`sigma_p` homogeneous degree 1 ⇒ Euler's theorem
    ⇒ exact additive split), matching what `attribution.ml`'s comment already argues but
    in equation form.
  - Kupiec POF, Christoffersen independence, conditional coverage — test statistics,
    degrees of freedom, and what each null hypothesis actually is.
  - Basel traffic-light zone boundaries and why the test is one-sided.
  - If Phase A/B ship: EWMA recursion and, if applicable, the Black-Scholes Greeks used.
- This is a **writing task, not a code task** — but it should cross-reference exact file
  and function names (`Risk_metrics.tail_count`, `Attribution.component_var`, etc.) so
  it reads as documentation of *this* codebase, not a generic risk-textbook summary.

**Acceptance criteria:** technically correct, checkable notation; a domain expert (finance
professor, quant recruiter) should be able to verify every formula against a standard
reference (Jorion's *Value at Risk*, McNeil/Frey/Embrechts) without needing to read the
OCaml.

---

## 5. Style and workflow rules for Claude Code while executing this roadmap

- **Build/test loop:** `make deps` once, then `make test` and `make fmt` before calling
  any phase done. `make run`, `make stress`, `make backtest` are the credential-free
  ways to sanity-check behavior end-to-end; use them liberally during development.
- **Comment voice:** every new module opens with the same kind of header comment the
  existing modules have — a short paragraph naming the design tension and the choice
  made, not a one-line docstring. Every non-obvious edge in `graph.ml` gets a "why this
  depends on this" comment, matching the existing ones exactly in tone.
- **README discipline:** each phase above ends with a README update. Match the existing
  README's register precisely — it argues in prose with real numbers from real runs, it
  is comfortable stating what a feature does *not* do, and it never oversells. A phase
  that adds a feature but doesn't update the README to the same standard is not done.
- **Commit/PR granularity:** one phase (or a clearly-scoped sub-piece of a phase) per
  PR, so the git history itself becomes something worth pointing an interviewer at —
  "here's the PR that added EWMA vol, here's the test that proves it responds faster to
  a regime break."
- **Don't guess at Owl/Incremental/Async APIs.** These libraries have real surprises
  (see the `Makefile`'s documented macOS Owl bugs, and `graph.ml`'s note about
  `Graph.set_price` vs. `Graph.apply_tick` for the zero-price bug that live-mode testing
  actually found). When unsure, check the installed library's `.mli` in `_opam` rather
  than assuming an API shape.
- **If a phase turns out to conflict with an invariant in §2, stop and flag it** rather
  than quietly working around the invariant. That conflict is itself useful information
  for AJ (it may mean the invariant needs a documented, deliberate exception — which is
  exactly how the existing `Value_at_risk` vs. `Component_var` scope-validity distinction
  reads today).

---

## 6. Suggested execution order

1. **Phase A** (EWMA vol) — closes a README-admitted gap, moderate effort, sets up Phase C.
2. **Phase E** (property tests) — pure test-writing, no architecture risk, fast to ship,
   immediately strengthens the existing correctness story.
3. **Phase C** (crisis backtest) — builds on Phase A, reuses all existing backtest
   machinery, high narrative payoff for low new-code volume.
4. **Phase G** (CI matrix + coverage) — cheap, do it whenever there's a lull.
5. **Phase B** (options/Greeks) — the biggest single addition; do this once the smaller
   phases have re-familiarized Claude Code (and AJ, reviewing the diffs) with the
   codebase's conventions.
6. **Phase F** (dashboard history) — mostly independent, good to interleave whenever a
   visual/demo refresh is useful (e.g. before sending the repo link to a recruiter).
7. **Phase D** (benchmarks) — do after B, since by then there's more graph to benchmark
   meaningfully.
8. **Phase H** (quant notes) — do last, once the math in A–C (and B, if shipped) is
   final, so the document doesn't need revisiting.

This order front-loads the changes with the best effort-to-signal ratio and defers the
largest single change (options/Greeks) until the smaller phases have re-established
context and test conventions.
