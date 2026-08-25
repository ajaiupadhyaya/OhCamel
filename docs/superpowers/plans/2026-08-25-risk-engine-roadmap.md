# Risk Engine Roadmap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend OhCamel from an equities-only, equal-weighted risk engine into one that
tracks volatility regimes, prices options risk, validates itself against real crisis data
and property-tests its own risk identities — without diluting the architectural thesis
that makes the project worth reading.

**Architecture:** Every phase is *additive*. New numerics go in new pure modules that do
not know `Incremental` exists; they are wired into `graph.ml` as **sibling nodes**, never
as replacements, so a comparison between the old and new estimator is a first-class,
displayable thing rather than a silent swap. No phase introduces a second implementation
of exposure/equity/limit arithmetic — anything that asks "what would the book look like
under X" goes through `Graph.fork`.

**Tech Stack:** OCaml 5.2.1, Jane Street `Incremental` / `Core` / `Async`, `Owl` (BLAS),
`alcotest`, and — newly added by this plan — `qcheck-core` (Phase E), `core_bench`
(Phase D), `bisect_ppx` (Phase G).

**Spec:** `docs/handoff.md` (the brief this plan implements, archived alongside
`docs/brief.md`).

---

## Global Constraints

These are the §2 invariants from the brief. Every task's requirements implicitly include
this section. A phase that ships faster by breaking one of these is a regression even if
the tests pass.

1. **Every dependency is a graph edge.** No node body may read a global, a mutable ref, or
   the network. New inputs are new `Inc.Var.t` cells with a "why does this depend on this"
   comment above the edge, matching the existing voice in `graph.ml`.
2. **No second implementation of exposure/equity/limits arithmetic.** Counterfactuals go
   through `Graph.fork`.
3. **Units stay abstract.** New money- or risk-shaped quantities get their own types with
   one named bridge function, the way `Price`/`Qty`/`Notional` do. A contract count is not
   a share count.
4. **Pure numerics stay pure.** New risk math goes in a module with no `Incremental`
   dependency, unit-testable in isolation — the `risk_metrics.ml` pattern.
5. **A missing credential is fatal, not degraded.** Never fall back to synthetic data
   silently in a mode that claims to be live.
6. **No order routing, ever.** The kill switch stays a bool wired to nothing.
7. **Every new numeric module gets hand-derived test values.** "Returns a number" is not a
   test. Derivations are written beside the assertion.
8. **Prose comments explain *why*, not *what*.** State the design tension, the choice, and
   what breaks under the alternative.

**Build/test loop, run before any phase is called done:**

```bash
make test && make fmt && git diff --exit-code
```

`make run`, `make stress`, `make backtest` and `make demo` are credential-free and are the
end-to-end sanity checks during development.

**README discipline:** each phase ends with a README update in the existing register —
prose that argues with real numbers from real runs, comfortable stating what a feature does
*not* do. A phase that adds a feature without updating the README to that standard is not
done.

---

## Known conflict with the brief — read before Phase C

The brief's Phase C asks for a Global Financial Crisis window (2008-09 → 2009-03) pulled
through "the Alpaca REST backfill [that] already exists". **That is not reachable.** Two
independent reasons, verified rather than assumed:

- Alpaca's historical stock bars begin in **2016**. There is no 2008 in the API at any
  subscription tier, so no amount of credentials makes the GFC window appear.
- This machine has no `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` in the environment, and the
  session's Alpaca connector returns HTTP 401 for `/v2/stocks/bars` — so even the
  2016-onward windows cannot be fetched here without the user supplying keys.

Per §5 of the brief ("if a phase turns out to conflict with an invariant, stop and flag it
rather than quietly working around it"), Phase C is **re-specced** in this plan:

- **Windows shipped:** COVID crash (2020-02 → 2020-04) and the 2022 rate shock. Both are a
  sharp shock and a slow grind, which is the contrast the brief wanted.
- **The GFC gap is documented, not faked.** The README says plainly that 2008 is outside
  the data source's coverage and therefore outside the claim.
- **Cache population** gets its own credential-gated path, and the resulting small CSVs are
  checked into `docs/crisis/` so a reviewer with no keys can reproduce the table. Missing
  cache *and* missing credentials is a loud failure with a message naming the fix — never a
  silent fall back to synthetic data.

Phase C is ordered after A and E, so this flag is resolved with the user before any code in
it is written.

---

## File Structure

New files, and the one responsibility each carries:

| File | Responsibility |
|---|---|
| `lib/vol_estimators.ml` | Pure EWMA (and optionally GARCH) volatility/covariance estimation. No `Incremental`. |
| `lib/options.ml` | Option position types (`Strike`, `Expiry`, `Implied_vol`, `Contracts`) and Black-Scholes pricing/Greeks. Pure. |
| `lib/history_buffer.ml` | Fixed-capacity ring buffer of published risk snapshots. An observer, not a node. |
| `lib/crisis_data.ml` | Read/write the on-disk daily-bar cache; fetch to populate it. |
| `test/test_vol_estimators.ml` | Hand-derived EWMA values + the regime-response property. |
| `test/test_options.ml` | Hull textbook values, put-call parity, the delta-hedged book. |
| `test/test_properties.ml` | `qcheck` properties over the risk identities. |
| `test/test_history_buffer.ml` | Ring-buffer eviction and the `/api/history` wire format. |
| `bench/bench_graph.ml` + `bench/dune` | `core_bench` latency/allocation suite. Not in the main binary. |
| `docs/quant_notes.md` | The math in standard notation, cross-referenced to file and function names. |
| `docs/crisis/*.csv` | Cached daily closes for the crisis windows, checked in. |

Modified files: `lib/graph.ml` (sibling nodes), `lib/limits.ml` + `lib/types.ml` (Greek
limits), `lib/var_backtest.ml` (a third estimator), `lib/server.ml` +
`lib/dashboard_html.ml` (new fields, history chart), `bin/main.ml` (display + new modes),
`Makefile`, `.github/workflows/ci.yml`, `dune-project`, `README.md`.

---

## Execution order

Ordered by interview leverage per unit of engineering risk, per §6 of the brief:

**A** (EWMA) → **E** (property tests) → **C** (crisis backtest) → **G** (CI matrix) →
**B** (options/Greeks) → **F** (dashboard history) → **D** (benchmarks) → **H** (quant notes)

One commit per phase, on branch `roadmap-phases`, so the history reads as a sequence of
defensible units.

---

## Phase A — EWMA volatility

**Why:** the README already names equal-weighted volatility as the model's weakest
assumption, and the `vol-regime` row of `make backtest` already shows it failing. This
closes the gap the project itself points at.

**Files:**
- Create: `lib/vol_estimators.ml`
- Create: `test/test_vol_estimators.ml`
- Modify: `lib/graph.ml` (sibling covariance node, sibling parametric VaR, selector)
- Modify: `lib/var_backtest.ml` (`Estimator.Parametric_ewma`)
- Modify: `lib/server.ml`, `lib/dashboard_html.ml` (display both)
- Modify: `bin/main.ml` (synthetic + backtest output)
- Modify: `test/test_ohcamel.ml` (register the suite)
- Modify: `README.md`

**Interfaces produced:**

```ocaml
(* lib/vol_estimators.ml *)
module Ewma : sig
  val default_lambda : float                        (* 0.94, RiskMetrics daily *)
  val validate_lambda : lambda:float -> unit
  val weights : n:int -> lambda:float -> float array (* newest last, sums to 1 *)
  val mean : returns:float array -> lambda:float -> float
  val variance : returns:float array -> lambda:float -> float
  val stddev : returns:float array -> lambda:float -> float
  val covariance : xs:float array -> ys:float array -> lambda:float -> float
  val covariance_matrix : series:float array array -> lambda:float -> Owl.Mat.mat
end
```

### The estimator, and the one real design choice in it

Weights decay geometrically toward the past. With a window of `n` observations, `r_n` the
most recent:

```
w_k ∝ λ^k   for k = 0 (newest) … n-1 (oldest),   normalised so Σ w = 1
```

The normaliser is computed as the **explicit sum** `Σ λ^k`, not the closed form
`(1-λ)/(1-λⁿ)`. The two are identical in real arithmetic; the closed form is 0/0 as λ→1,
and λ→1 is exactly the limit the tests exercise.

**The choice: demean against the EWMA-weighted mean, not against zero.** RiskMetrics'
published convention is zero-mean, on the argument that a daily drift estimate is mostly
noise. This module demeans anyway, and the reason is comparability: with a weighted mean,
the estimator reduces *exactly* to `Risk_metrics.covariance_matrix` as λ→1, so the
difference between the two sibling nodes is a difference in **weighting** and nothing else.
Under the zero-mean convention the two would also differ in mean treatment, and the
headline claim of this phase — "the two disagreeing is a regime-change diagnostic" — would
be measuring two things at once. At daily horizons the drift term is second-order either
way; what is not second-order is being able to say precisely what the disagreement means.

- [ ] **Step A1: Write the failing hand-derived test**

`test/test_vol_estimators.ml`. Derivation, written beside the assertion:

λ = 0.5, n = 3. Unnormalised weights newest-first are 1, 0.5, 0.25, summing to 1.75, so
oldest→newest the normalised weights are **1/7, 2/7, 4/7**.

Choose `xs = [| -0.06; 0.01; 0.01 |]`. Weighted mean is
`(1(-0.06) + 2(0.01) + 4(0.01))/7 = (-0.06 + 0.02 + 0.04)/7 = 0` — exactly zero, so the
variance is a plain weighted sum of squares:

```
var = (1(0.06²) + 2(0.01²) + 4(0.01²))/7
    = (0.0036 + 0.0002 + 0.0004)/7
    = 0.0042/7
    = 6.0e-4       exactly
```

Take `ys = -xs`. Its weighted mean is also 0, so `var(ys) = 6.0e-4` and
`cov(xs, ys) = -6.0e-4` exactly — a 2×2 matrix that can be read off the page.

```ocaml
let test_ewma_covariance_hand_derived () =
  let xs = [| -0.06; 0.01; 0.01 |] in
  let ys = Array.map xs ~f:Float.neg in
  Alcotest.check float_eq "var(xs)" 6.0e-4
    (Vol_estimators.Ewma.variance ~returns:xs ~lambda:0.5);
  Alcotest.check float_eq "cov(xs, ys)" (-6.0e-4)
    (Vol_estimators.Ewma.covariance ~xs ~ys ~lambda:0.5)
```

- [ ] **Step A2: Run it, confirm it fails** — `make test`, expect "Unbound module
  Vol_estimators".

- [ ] **Step A3: Implement `lib/vol_estimators.ml`**

Module header in the existing voice: name the tension (equal weighting treats a
sixty-day-old observation and yesterday's as equally informative about tomorrow, which is
false in exactly the regime where the number is read), the choice, and what breaks under
the alternative. Validate λ ∈ (0,1) strictly and raise on violation, matching
`Risk_metrics`' "structurally invalid input raises" convention.

- [ ] **Step A4: `make test` — hand-derived test passes.**

- [ ] **Step A5: The λ→1 reduction test**

The property that justifies calling these siblings. At λ = 0.999999 over a 60-point
series, `Ewma.covariance_matrix` must match `Risk_metrics.covariance_matrix` entry-wise to
1e-9.

- [ ] **Step A6: The regime-response test — this is the actual point of the feature**

Deterministic series, no RNG: 100 observations alternating ±0.005, then 20 alternating
±0.020. The true post-break σ is 0.020.

```
equal-weighted var = (100(0.005²) + 20(0.020²))/120 = 0.0105/120 = 8.75e-5  → σ ≈ 0.00935
EWMA(0.94):  mass on the last 20 = 1 - 0.94²⁰ ≈ 0.710
             var ≈ 0.710(4.0e-4) + 0.290(2.5e-5) ≈ 2.91e-4                 → σ ≈ 0.0171
```

Assert `|σ_ewma - 0.020| < |σ_equal - 0.020|`, and assert `σ_ewma > σ_equal`. Test the
property, not the formula — the formula is already pinned by A1.

- [ ] **Step A7: Wire sibling nodes into `graph.ml`**

- `Node_name.covariance_ewma`, `Node_name.parametric_var_ewma`.
- `covariance_ewma_node` hangs off `aligned_returns_node` and **nothing else** — the same
  edge as `covariance_node`, with a comment saying so and saying why the two are siblings
  rather than one replacing the other.
- `parametric_var_ewma_node = Inc.map2 weights_node covariance_ewma_node`.
- `?ewma_lambda` optional argument on `Graph.create`, defaulting to
  `Vol_estimators.Ewma.default_lambda`; validated at construction, before any node exists,
  like every other `create` argument.
- `?covariance_for_attribution : [ `Equal_weighted | `Ewma ]`, defaulting to
  `` `Equal_weighted `` for backward compatibility. This selects which covariance the
  attribution node reads. It is a construction-time constant, not a runtime read, so the
  edge stays declared and invariant #1 holds.
- Observers `obs_covariance_ewma`, `obs_parametric_var_ewma`; accessors
  `Graph.covariance_ewma`, `Graph.parametric_var_ewma`; `Snapshot` gains
  `parametric_var_ewma` and `ewma_lambda`.

- [ ] **Step A8: Recomputation test in `test_graph.ml`**

A price tick must not reach `covariance_ewma`, exactly as it must not reach `covariance`.
Assert it as a recomputation count, in the style of the existing three architectural tests.
Also assert the node-count deltas the existing scaling table depends on still hold, and
update the expected counts in `test_graph.ml` that the two new nodes shift.

- [ ] **Step A9: `Var_backtest.Estimator.Parametric_ewma`**

Add the third variant. `Estimator.estimate` gains one branch:

```ocaml
| Parametric_ewma ->
    Risk_metrics.parametric_var
      ~mean:(Vol_estimators.Ewma.mean ~returns:window ~lambda)
      ~stddev:(Vol_estimators.Ewma.stddev ~returns:window ~lambda)
      ~confidence
```

λ travels on the variant (`Parametric_ewma of float`) so the report says which λ produced
it. `rolling` is untouched — the point-in-time discipline lives there and must not be
duplicated.

- [ ] **Step A10: `make backtest` prints the third row per series**

Same table, one more estimator per series: 9 rows instead of 6. The expected finding is
that `vol-regime`/`parametric` is rejected and `vol-regime`/`parametric_ewma` is not — the
whole argument for the phase, printed by the program rather than claimed in a comment.
If EWMA is *also* rejected, report that honestly and say so in the README; a phase that
only ships when the result is flattering is not a validation suite.

- [ ] **Step A11: Display both**

`make run`'s risk block, the dashboard, and `/api/snapshot` all carry both parametric
numbers with the same framing the README already uses for historical-vs-parametric: the
gap is the diagnostic. Add a `test_server.ml` case pinning the new wire fields.

- [ ] **Step A12: README** — extend "Is the number any good" with the real backtest table,
  and replace the "EWMA or GARCH would track a regime change faster and neither is here"
  sentence in "What this is not" with what is now true. GARCH is still not here; say so.

- [ ] **Step A13: `make test && make fmt`, then commit**

```bash
git add -A && git commit -m "Phase A: EWMA volatility as a sibling estimator"
```

**Acceptance criteria:** hand-derived covariance test passes; λ→1 reduces to the
equal-weighted matrix; EWMA demonstrably responds faster to an inserted regime break; the
backtest table carries the third estimator; a price tick still cannot reach either
covariance node; README updated with numbers from a real run.

---

## Phase E — Property-based tests for the risk identities

**Why:** generalises the project's four best tests from fixed examples to arbitrary inputs.
Pure test-writing, no architectural risk, and it converts "I spot-checked the Euler
decomposition" into "I property-tested it".

**Files:**
- Create: `test/test_properties.ml`
- Modify: `dune-project`, `test/dune` (add `qcheck-core`, `qcheck-alcotest`)
- Modify: `test/test_ohcamel.ml`
- Modify: `README.md`

- [ ] **Step E1: Add the dependency**

`(qcheck-core (and :with-test (>= 0.21)))` and `(qcheck-alcotest (and :with-test (>= 0.21)))`
in `dune-project`; `qcheck-core qcheck-alcotest` in `test/dune`. Then
`opam install --deps-only --with-test -y .`. `qcheck-alcotest` is what lets a property
appear as an ordinary alcotest case, so both testing styles stay in one runner and both
stay in CI.

- [ ] **Step E2: A PSD covariance generator**

The properties need random *valid* covariance matrices, and a matrix of random entries is
not one. Generate `n × m` random returns (n ≤ 8 instruments, m ≥ n+2 observations) and run
them through `Risk_metrics.covariance_matrix` — a sample covariance matrix is PSD by
construction. This also keeps the generator honest: it produces the same *kind* of matrix
the engine actually sees.

- [ ] **Step E3: Property — Euler additivity**

For random weights and random PSD covariance, `Σ component_i ≈ portfolio_stddev` to
`1e-9 · max(1, σ_p)` (relative, because σ_p spans orders of magnitude across generated
books). Skip the degenerate case where `Attribution.compute` returns `None` — that is a
documented state, not a failure.

- [ ] **Step E4: Property — component VaR sums to portfolio parametric VaR**

The scaled version of E3, and the one that makes an instrument-scoped `Component_var` limit
well posed. Same tolerance discipline.

- [ ] **Step E5: Property — a hedge reduces portfolio variance**

Generalises the hand-written no-`abs` test. For a random book, append a position whose
return series is the negation of the book's own portfolio return series. Assert its
component contribution is `≤ 0`, and that portfolio σ with it is strictly less than
portfolio σ without it, at equal gross. A stray `abs` anywhere in the chain breaks this for
almost every generated case.

- [ ] **Step E6: Property — VaR monotone in confidence**

For arbitrary return series and `c₁ < c₂`, both `historical_var` and `parametric_var` are
non-decreasing. Historical VaR is a step function of confidence, so the assertion is `≥`,
not `>`.

- [ ] **Step E7: Property — stress-fork isolation**

Generalises `test_stress.ml`'s fixed-suite isolation test. For randomly parameterised
scenarios (random shock kinds, random magnitudes, random target symbols), the live snapshot
is unchanged field-for-field after the run. Compare via the snapshot's own fields, not a
sexp — a sexp comparison would pass on a snapshot that lost a field.

- [ ] **Step E8: Property — backtest lookahead isolation**

For random window sizes and series lengths, rebuild each rolling window independently and
demand `Var_backtest.rolling`'s forecast at `t` matches an estimator fed only
`returns[t-w .. t-1]`. Generalises the existing fixed test across shapes.

- [ ] **Step E9: Trial counts**

100 trials per property locally. If `make test` grows past a few seconds, drop the CI count
via a `QCHECK_TRIALS` environment read *in the test file* (not in library code) with a
comment naming the higher local number — per the brief, reduce trials before dropping a
property.

- [ ] **Step E10: `make test && make fmt`, then commit**

```bash
git add -A && git commit -m "Phase E: property-test the risk identities"
```

**Acceptance criteria:** all six properties pass at 100+ trials; `make test` still finishes
inside CI's budget; the example-based and property-based suites remain separate files and
both run in CI.

---

## Phase C — Crisis backtesting against real data

**Read the "Known conflict" section above before starting this phase.**

**Files:**
- Create: `lib/crisis_data.ml`, `docs/crisis/*.csv`
- Modify: `bin/main.ml` (a `backtest-crisis` mode), `Makefile`, `README.md`
- Create: `test/test_crisis_data.ml`

- [ ] **Step C1: Confirm the re-spec with the user** — which windows, and whether to add a
  keyless historical source so the GFC window becomes reachable at all. Do not write the
  fetcher before this is settled.

- [ ] **Step C2: The cache format**

CSV, one file per window per symbol set: `date,symbol,close`. Small enough to check in,
diffable, and readable by a reviewer without running anything. `Crisis_data.load : path ->
(Symbol.t * float array) list Or_error.t` converts closes to returns through the *existing*
`Alpaca_rest.returns_of_closes`, so the return convention cannot drift between live and
cached paths.

- [ ] **Step C3: Cache population, credential-gated**

A `backtest-crisis --populate` path that fetches and writes the CSVs. Missing credentials
is fatal with a message naming the variables — `Config.Credentials.load`'s existing
behaviour, reused, not reimplemented.

- [ ] **Step C4: Loud failure when the cache is absent**

`make backtest-crisis` with no cache and no credentials prints how to populate the cache
and exits non-zero. It must never fall back to the synthetic series — that is invariant #5
and it is the difference between a validation suite and a decoration.

- [ ] **Step C5: Run the *existing* battery**

`Var_backtest.of_returns` unchanged, over the cached windows, for all three estimators
including Phase A's EWMA. No new backtest logic — the point of the phase is harder data
through the validated harness.

- [ ] **Step C6: README table and the honest paragraph**

Which windows the model failed in and why, in the register of the existing
"The suite rejects two of six configurations. That is the point." The expected story is
that equal-weighted vol fails during a sharp vol spike and EWMA does better; if the data
says otherwise, the README says what the data said.

- [ ] **Step C7: `make test && make fmt`, then commit**

**Acceptance criteria:** cached CSVs checked into `docs/`; `make backtest-crisis`
reproducible with no API keys; a written verdict per window; the GFC coverage gap stated
plainly rather than papered over.

---

## Phase G — CI: macOS matrix + coverage

**Files:** `.github/workflows/ci.yml`, `dune-project`, `Makefile`, `README.md`

- [ ] **Step G1: macOS matrix entry**

`strategy.matrix.os: [ubuntu-latest, macos-latest]`. The macOS leg exports the
`OWL_CFLAGS`/`OWL_LDLIBS` the Makefile documents, and installs `libomp` and `openblas` via
Homebrew, so CI actually *exercises* the documented workaround instead of trusting the
prose describing it. Keep the Linux leg's comment explaining why those variables are
deliberately absent there.

- [ ] **Step G2: Coverage**

`bisect_ppx` as a test-only dependency and an `(instrumentation (backend bisect_ppx))`
stanza in `lib/dune`, driven by a `make coverage` target. Upload the report as a CI
artifact. Do not over-invest: wiring it up correctly and putting the number in the badge row
is the whole deliverable.

- [ ] **Step G3: Green on both legs, badge in the README**

If the macOS leg does not go green — a real possibility given the two documented Owl bugs —
**report that rather than deleting the leg.** A red macOS leg that reproduces a known
compiler bug is more informative than no leg, and the Makefile's write-up becomes evidence
instead of assertion. Decide with the user whether to keep it required or mark it
`continue-on-error` with a comment saying why.

- [ ] **Step G4: Commit**

**Acceptance criteria:** both legs run; coverage percentage visible next to the existing CI
badge; the macOS outcome, whatever it is, is documented truthfully.

---

## Phase B — Options: Greeks-aware exposure

The largest single change. Additive throughout; it must not become a rewrite.

**Files:**
- Create: `lib/options.ml`, `test/test_options.ml`
- Modify: `lib/types.ml` (or a sibling) for the new abstract types
- Modify: `lib/graph.ml` (delta-equivalent exposure folds into existing `exposure[S]`;
  `portfolio_gamma`, `portfolio_vega` as separate nodes)
- Modify: `lib/limits.ml` (a `Greek_limit`), `lib/config.ml` (book-file spec),
  `bin/main.ml`, `README.md`

**Interfaces produced:**

```ocaml
module Strike       : sig type t val of_float : float -> t val to_float : t -> float end
module Implied_vol  : sig type t val of_float : float -> t val to_float : t -> float end
module Contracts    : sig type t val of_float : float -> t val to_float : t -> float end
type right = Call | Put

module Black_scholes : sig
  type greeks = { price : float; delta : float; gamma : float; vega : float; theta : float }
  val compute :
    spot:float -> strike:float -> time_to_expiry:float ->
    rate:float -> implied_vol:float -> right:right -> greeks
end
```

`Contracts.t` is distinct from `Qty.t` — a contract count is not a share count, and the
multiplier between them (100, conventionally) is the one named bridge. That is invariant #3
applied to the new quantity.

- [ ] **Step B1: Hull's textbook values as the failing test**

Price, from Hull's worked example — S=42, K=40, r=0.10, σ=0.20, T=0.5:

```
d₁ = [ln(42/40) + (0.10 + 0.20²/2)(0.5)] / (0.20√0.5)
   = [0.048790 + 0.060000] / 0.141421 = 0.7693
d₂ = d₁ - 0.141421 = 0.6279
c  = 42 N(d₁) - 40 e^{-0.05} N(d₂) = 42(0.7791) - 38.049(0.7349) = 4.76
p  = 40 e^{-0.05} N(-d₂) - 42 N(-d₁) = 38.049(0.2651) - 42(0.2209) = 0.81
```

Greeks, from Hull's Chapter 19 example — S=49, K=50, r=0.05, σ=0.20, T=0.3846:

```
d₁ = 0.0542,  N(d₁) = 0.5216,  N'(d₁) = 0.39835
delta = N(d₁)                        = 0.522
gamma = N'(d₁)/(Sσ√T)                = 0.39835/(49 · 0.124032)   = 0.0655
vega  = S N'(d₁) √T                  = 49(0.39835)(0.62016)      = 12.11   (per 1.00 of vol)
theta = -SN'(d₁)σ/(2√T) - rKe^{-rT}N(d₂) = -3.1474 - 1.1580      = -4.305  (per year)
```

Assert to 1e-3 — the textbook values are quoted to three or four figures, so a tighter
tolerance would be asserting the rounding rather than the formula. Say that in a comment.

- [ ] **Step B2: Implement, confirm the values.**

- [ ] **Step B3: Put-call parity as a property**

`c - p = S - K e^{-rT}` across a grid of moneyness levels. Cheap, and it is the standard
"does this pricer make sense at all" check. Also add it to `test_properties.ml` over random
inputs once Phase E exists.

- [ ] **Step B4: Vega/gamma sign and monotonicity checks**

Gamma and vega are strictly positive for both rights; delta ∈ (0,1) for calls and (-1,0)
for puts; deep ITM call delta → 1, deep OTM → 0. These catch a sign error that parity would
not, because parity is symmetric in the mistake.

- [ ] **Step B5: Graph integration — delta-equivalent exposure folds into `exposure[S]`**

An option position's delta-equivalent exposure is `delta × multiplier × contracts × spot`,
and it is added into the **existing** per-symbol exposure aggregation for its underlying.
There is no parallel exposure system: gross, net, weights, equity, drawdown and every
notional limit inherit options exposure for free, which is the entire argument for doing it
this way. New input cells: contracts, strike, expiry, right, implied vol per option
position — each a declared edge with its "why" comment.

- [ ] **Step B6: `portfolio_gamma` and `portfolio_vega` as separate nodes**

These have no natural place inside a linear exposure sum, so they are their own nodes at
per-instrument and portfolio level. Both are additive across positions — say why in the
comment, because that additivity is what makes the next step's limit well posed.

- [ ] **Step B7: `Greek_limit` in `limits.ml`**

Generalised over which Greek. `scope_is_valid` must be *reasoned about*, not asserted: vega
and gamma are additive across names in the same sense exposure is (they are sums of
per-position sensitivities, not quantiles), so unlike `Value_at_risk` they are valid at
every scope. Work that argument out in a comment the way `Component_var`'s validity argument
is worked out today. Note the one caveat honestly: summing vega across different expiries
adds sensitivities to *different* volatilities, so a portfolio vega number is a bucketed
approximation and the comment should say so.

- [ ] **Step B8: The delta-hedged test**

Long stock plus short calls sized to zero the delta reads ≈0 delta-equivalent exposure and
**nonzero** gamma and vega. This is the options analogue of the existing hedge test and it
is the one an interviewer would want to see you think to write.

- [ ] **Step B9: Implied vol source — disabled in live mode, and say so**

The synthetic/demo book gets a plausible smiled IV surface generated alongside its price
feed and **labelled as synthetic** in the output. Live mode ships options risk **disabled
with a clear message** ("no options chain data source configured") rather than a faked feed.
That is invariant #5, and it is the honest answer given a free Alpaca tier.

- [ ] **Step B10: README** — a "Where the risk is: options" subsection parallel to the
  existing equities one, including the delta-hedged example and the vega-bucketing caveat.

- [ ] **Step B11: `make test && make fmt`, then commit**

**Acceptance criteria:** Hull values reproduced; put-call parity holds; a delta-hedged book
reads flat delta and live gamma/vega; options exposure flows through the *existing*
aggregation; live mode declines rather than fabricates.

---

## Phase F — Dashboard: bounded in-memory history

**Files:** `lib/history_buffer.ml`, `test/test_history_buffer.ml`, `lib/server.ml`,
`lib/dashboard_html.ml`, `docs/media/*`, `README.md`

- [ ] **Step F1: The ring buffer, with the non-persistence stated in the header**

Fixed capacity (500 points) of `(time, gross, net, var_notional, es_notional, drawdown)`.
An **observer** hanging off `Graph.on_change`, following `alerts.ml`'s pattern — not a node,
because a node would put display state inside the dependency graph. The module header says
in as many words that this is in-memory only and resets on restart, so it cannot quietly
become a persistence layer.

- [ ] **Step F2: Eviction test** — at capacity + 1, the oldest entry is gone and the newest
  is present, and the length is exactly the capacity.

- [ ] **Step F3: `/api/history`** returning JSON in the same conventions `/api/snapshot`
  uses, with a `test_server.ml` case pinning the shape.

- [ ] **Step F4: Sparkline, dependency-free**

Server-side-generated inline SVG, or a small inline canvas snippet. No CDN, no charting
library — `dashboard_html.ml`'s self-contained-binary property is not negotiable for a
chart.

- [ ] **Step F5: Regenerate `docs/media/dashboard.png` and `demo.png`** so the README's
  images match the page.

- [ ] **Step F6: Commit**

**Acceptance criteria:** eviction test passes; `/api/history` matches the existing wire
conventions; no external JS dependency; screenshots regenerated.

---

## Phase D — Latency and allocation benchmarks

**Files:** `bench/dune`, `bench/bench_graph.ml`, `Makefile`, `.github/workflows/ci.yml`,
`README.md`

- [ ] **Step D1: `core_bench` in its own dune stanza** — `bench/` does not ship in the main
  binary. `core_bench` is Jane Street's own, which keeps the stack thematically coherent.

- [ ] **Step D2: Measure three things at the three book sizes already in the `make run`
  table (10 / 100 / 400 instruments):** time per tick, minor/major words allocated per tick,
  and the same two for a throwaway ~30-line "recompute everything" baseline. The baseline is
  what turns the polling-vs-incremental contrast from architectural into quantitative, and
  it is deliberately throwaway code inside `bench/` so it never becomes a second
  implementation anyone could call by accident (invariant #2).

- [ ] **Step D3: `make bench`**, in the existing target style with the existing comment
  voice.

- [ ] **Step D4: README table directly under the recomputation-count table**, with the
  hardware and the run-to-run variance stated — the way the Owl macOS section already
  documents environment-specific caveats.

- [ ] **Step D5: CI gets a `workflow_dispatch`-only bench job.** Benchmark numbers on shared
  runners are noise; do not gate pushes on them and do not over-engineer this.

- [ ] **Step D6: Commit**

**Acceptance criteria:** re-runnable with reported variance; the naive baseline is measured,
not asserted; CI is not gated on benchmark numbers.

---

## Phase H — `docs/quant_notes.md`

A writing task, done last so the math in A–C (and B) is final.

- [ ] **Step H1: Write it.** Each item in under half a page, in standard notation, and each
  cross-referencing the exact file and function that implements it so it reads as
  documentation of *this* codebase rather than a risk-textbook summary:

  - Historical VaR and ES, with the nearest-rank tail convention as actually implemented
    (`Risk_metrics.tail_count`), and the ε-rounding artefact as a footnote — it is a real
    bug that was found and fixed, and it is worth showing.
  - Parametric VaR (`Risk_metrics.portfolio_parametric_var`) and what the Gaussian
    assumption costs in the tail.
  - The Euler decomposition: σ_p homogeneous of degree 1 ⇒ Euler's theorem ⇒ exact additive
    split (`Attribution.compute`, `Attribution.component_var`), in equations rather than the
    README's prose.
  - EWMA recursion and the weighted-mean choice (`Vol_estimators.Ewma`).
  - Kupiec POF, Christoffersen independence, conditional coverage: statistics, degrees of
    freedom, and what each null actually is (`Var_backtest.kupiec_pof`,
    `Var_backtest.christoffersen_independence`).
  - Basel traffic-light boundaries and why the test is one-sided
    (`Var_backtest.traffic_light`).
  - If Phase B shipped: the Black-Scholes Greeks used (`Options.Black_scholes`).

- [ ] **Step H2: Link it from the README** near the badge row, as the reference companion to
  the README's narrative.

- [ ] **Step H3: Commit**

**Acceptance criteria:** every formula checkable against Jorion or McNeil/Frey/Embrechts
without reading the OCaml; every claim traceable to a named function in this repo.

---

## Self-review against the brief

- **§4 coverage:** Phases A–H each map to a section of the brief. Phase C is the one
  deviation, flagged and re-specced above with the reason stated rather than silently
  narrowed.
- **§2 invariants:** A adds sibling nodes off an existing edge (#1) and a pure module (#4);
  B introduces distinct abstract types for contracts (#3), folds into the existing exposure
  aggregation rather than a parallel one (#2), and declines to fake a live options feed
  (#5); C fails loud on a missing cache (#5); D's naive baseline lives in `bench/` so it is
  not a second implementation anything can reach (#2); no phase adds order routing (#6);
  every numeric phase carries hand-derived values (#7).
- **Placeholders:** none. Every numeric expectation in this plan is derived above, not left
  to the implementer.
- **Naming consistency:** `Vol_estimators.Ewma.covariance_matrix` is used in A3, A7 and A9;
  `Options.Black_scholes.compute` in B1 and B5; `Crisis_data.load` in C2 and C5.
