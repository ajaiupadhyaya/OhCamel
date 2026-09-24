# OhCamel Alpha — design

*2026-09-02. This document stands in for the `quant-platform-architecture.md`
the brief refers to, which was never found. It records the decisions the brief
said to lock before scaffolding, and why each went the way it did.*

## Problem

Two of the owner's projects each do half of a trading system honestly and
neither does the whole. **OhCamel** is a reactive risk and limits engine in
OCaml: it knows, at every tick, what a book is exposed to and which limits it
is near, and it is deployed. **Five Dollar Quant** (`fdq`) is a Python research
harness with a real-data-only policy and a validation battery — walk-forward,
Deflated Sharpe, Probability of Backtest Overfitting, a pre-registered power
analysis — that can say whether a signal has an edge that survives honest
testing. Neither can take a validated signal, turn it into orders, pass those
orders through risk, and simulate their execution. That loop is what a desk
runs, and it is what a reviewer at a trading firm will ask to see.

The brief also names two more layers — retrieval over research papers with
trade explainability, and a dashboard — and is explicit about priorities:
correctness and a defensible methodology over features, and OCaml genuinely in
the critical path rather than decorating a Python system.

## Goal

A signal computed in Python, validated by the battery, handed to an OCaml core
over a contract the core enforces, checked against OhCamel's limits before any
order exists, and filled by a paper simulator whose every fill is explainable.
Runnable end to end with no credentials, from committed real data, in under a
minute. Every number it reports defensible to a hostile interviewer, including
the negative ones.

## Non-goals

- **Live trading.** Paper only, forever, in this repository. No code path
  submits an order to a broker.
- **Alpha.** This repository builds infrastructure and red-teams results. It
  does not invent signals; hypotheses come from the owner, stated in writing,
  and a trustworthy negative is a complete success.
- **Rewriting what exists.** The validation battery is `fdq`'s and the risk
  engine is OhCamel's. This repo composes them.
- **Intraday.** Daily bars, decisions at the close, fills at the next open.
- **Machine learning, until it beats a baseline under the same gates.**

## The decisions the brief asked for

### 1. Data vendor and budget: Alpaca free tier and FRED, zero budget

`fdq` already fetches real daily bars from Alpaca (IEX feed, history from
2016) with yfinance as a keyless fallback, and stamps every cached file with a
provenance sidecar. That is reused as-is. The free tier's real limitation is
not price quality at daily resolution; it is that **delisted names are
absent**, so any cross-sectional single-name result carries survivorship bias.
Decision 2 removes that problem rather than paying to solve it. If single-name
equities ever enter scope, a survivorship-free daily source (Tiingo, Norgate;
roughly $30 a month) is the precondition, and this paragraph is where that
condition is written down.

### 2. Asset scope: a fixed universe of nine liquid US ETFs

`fdq`'s Phase-1 universe, unchanged: SPY, QQQ, IWM, TLT, IEF, GLD, XLE, XLF,
XLK. All nine existed throughout the data window, so a fixed list of them
carries no survivorship bias, and they are liquid enough that a paper fill at
the open with a basis-point cost model is not a fiction. For OhCamel's
sector-scoped limits they are grouped: equity index (SPY, QQQ, IWM), rates
(TLT, IEF), commodity (GLD), energy (XLE), financials (XLF), technology (XLK).
Options come later and would light up OhCamel's Greeks; single names need
decision 1 revisited; futures rule out Alpaca and are out.

### 3. Timeline: interview-ready as soon as possible

Not stated by the owner; assumed. The consequence is the phase order and where
the line falls: Phases 0–2 are the system, Phases 3–4 are presentation of it.
A working, validated, risk-gated paper loop with an honest report beats a
dashboard over an unvalidated one.

### 4. Structure: a separate repository, and OhCamel keeps its invariant

The brief puts "paper execution sim" inside the OCaml core. OhCamel's charter
(its `docs/handoff.md` §2, invariant 6) says: *no order routing, ever; nothing
that places, cancels, or simulates submitting an order; the kill switch stays
a bool wired to nothing.* Every commit in that repository has honoured it and
its README argues from it. Putting execution inside OhCamel would break the
thesis of the project this one is meant to showcase.

So: **OhCamel stays the risk kernel, exactly as it is.** It is linked into
this repository as an OCaml library (`ohcamel`, already a public dune library)
and its graph is the source of truth for exposure, VaR and limits. **The
execution simulator lives here, in OCaml**, so OCaml remains in the critical
path in the strongest sense — every order is created by OCaml, checked against
OhCamel's limits on a fork of the graph *before it exists*, and filled by
OCaml. Python computes signals asynchronously and hands over a file. What
OhCamel gains, in its own repo and under its own invariants, is a pure
function from a proposed fill to the limits it would breach — the same
mechanism `stress.ml` already uses to answer "what if". That is a
read-only calculation of the kind OhCamel's invariant explicitly permits.

Separate repository, `ohcamel-alpha`, beside OhCamel. Monorepo would tangle
two build systems with two CI clocks and would put a Python and a Node tree
into a repository whose README is an argument about OCaml.

### 5. Lineage (not in the brief): `fdq`'s battery, `quant-trading`'s charter

The owner has two Python systems with overlapping validation code.
`quant-trading` is the larger and has the fuller battery (CPCV, bootstrap,
regime stress, cost sweeps) and a written charter; `fdq` is smaller, has the
real-data policy with provenance, walk-forward, DSR, PSR, PBO, bootstrap ruin
analysis, a power analysis, and an experiment runner with pre-registered
configs. This repo depends on **`fdq` as a package** (pinned to a commit) for
data and validation, and adopts **`quant-trading`'s charter** as its
methodology, restated in [`../../CHARTER.md`](../../CHARTER.md) with the gates
made explicit. Where `fdq` lacks a test the charter requires — CPCV, regime
stress — that test is added to `fdq` upstream, not reimplemented here.

## Architecture

```
  papers, notes ─────────►  research/   Python (uv), depends on fdq
  real bars (fdq cache) ─►   strategies · fdq validation battery · report
                              │
                              │  writes  signals/<strategy>/<seq>.json
                              │  (interface/signal.schema.json, versioned)
                              ▼
  bars: replay | live ────►  core/      OCaml (dune), links ohcamel
                              intake ── R1..R8: schema, clock, staleness,
                              │         sequence, gate, universe, data hash
                              │  targets → proposed fills
                              ├── pre-trade: Ohcamel.Graph.fork + limits
                              ├── paper fills: next open, explicit cost model
                              └── book → OhCamel graph (live risk on the paper book)
                              │
                              ▼  /api  JSON + SSE
  dashboard/ (Next.js, Phase 4)      rag/ (Phase 3): papers + trade explanations
```

Two clocks, and the core owns both. The **bar clock** is whatever the feed
last delivered — in dev mode a replay of committed real bars; later, Alpaca.
The **signal clock** is Python's `as_of`, which the core treats as a *claim*
to be checked against its own bar clock, never as fact. That is the concrete
meaning of "OCaml never trusts Python's timing, only its output".

### Components

| Directory | Language | Purpose |
|---|---|---|
| `interface/` | JSON Schema | The signal contract. Versioned. Both sides test against it |
| `research/` | Python 3.12, uv | `alpha` package: replay feed, signal emission, strategy runs through `fdq`'s battery, reports |
| `core/` | OCaml 5.2, dune | `alpha` executable: intake, paper book, pre-trade check via `ohcamel`, fills, API |
| `fixtures/bars/` | parquet + sidecars | 9 symbols × 756 real daily bars, 2018-01-02 → 2020-12-31, sliced from `fdq`'s Alpaca cache with provenance. Spans a calm year, the Q4-2018 selloff and the COVID crash |
| `rag/` | Python | Phase 3 |
| `dashboard/` | Next.js | Phase 4 |

### The signal contract, v1

One JSON document per signal. Fields, and why each exists:

| Field | Why |
|---|---|
| `schema_version` | So the core can refuse what it does not understand |
| `strategy` | Slug; must be registered with the core |
| `params_hash` | SHA-256 of the canonical parameter JSON; ties a signal to exactly one configuration |
| `as_of` | The bar date the signal was computed *at the close of*. Actionable at the next open, never the same day |
| `computed_at` | Wall-clock, RFC 3339 UTC. Informational; the core does not act on it |
| `data_hash` | SHA-256 over the bars the strategy read, so the core can prove Python saw the data the core has |
| `sequence` | Monotonic per strategy; a replayed or duplicated file is rejected |
| `validation` | `status` of `pass`, `fail` or `unvalidated`; the battery's DSR, PSR, PBO; the gates version; a manifest reference. Only `pass` may trade |
| `targets` | `[{symbol, weight}]`, weights in [-1, 1], Σ\|w\| ≤ 1 |

### Intake rules — what the core enforces before a target becomes an order

| | Rule | Rejects |
|---|---|---|
| R1 | `schema_version = 1` | anything else |
| R2 | `strategy` is registered | unknown slugs |
| R3 | `as_of` ≤ the core's latest bar date | a signal from the future — Python's clock is not trusted |
| R4 | `as_of` ≥ latest bar date − `max_age` (default 3 trading days) | a stale signal |
| R5 | `sequence` > last accepted for that strategy | replays, duplicates, out-of-order files |
| R6 | `validation.status = pass` | unvalidated or failed signals are **advisory**: logged with the reason, never traded |
| R7 | every symbol in the universe, each \|w\| ≤ 1, Σ\|w\| ≤ 1 + 1e-9 | malformed targets |
| R8 | `data_hash` equals the core's hash of its own bars up to `as_of` | a signal computed on different data. Phase 2, when the core keeps a bar store |

Each rule is a pure function with a hand-derived test on both sides of the
boundary. A rejection names the rule.

### Paper execution (Phase 2)

Targets are weights; the core turns them into proposed fills against the
paper book at the next bar's open. Each proposed fill is applied to a *fork*
of the OhCamel graph carrying the paper book, and the limits are read from
the fork; a breach rejects the fill and the reason is recorded. Accepted fills
are charged an explicit cost — a basis-point spread plus commission, read from
the same friction configuration `fdq` uses, so the paper P&L and the backtest
P&L share one cost model. Fills update the paper book, the book updates the
live graph, and OhCamel's risk on the paper book is what the API serves.
Nothing here talks to a broker.

## Data policy

Inherited from `fdq` and restated: **no synthetic market data, anywhere a
number is reported.** Dev mode is a *replay of committed real bars* with their
provenance sidecars; the replay refuses a file without a sidecar or with
`synthetic: true`. OhCamel's own demo generates prices, and that is fine for
OhCamel, whose demo demonstrates a risk engine; it is not fine here, where
the point is the signal.

## Validation gates

From the charter, applied literally, never loosened to admit a strategy:
walk-forward; CPCV; DSR ≥ 0.30; PSR ≥ 0.70; PBO reported; bootstrap lower-5%
> 0; positive in ≥ 3 regimes; holdout positive; cost sensitivity at 0/5/15/30
bps reported, not a single assumption. A signal's `validation` block is
written from the battery's manifest by the same code that ran it.

## Phases

| Phase | Deliverable | Acceptance |
|---|---|---|
| 0 | Repo, contract, replay feed, signal emission, OCaml intake with R1–R7, the `ohcamel` link proven | `make check` runs offline: replay streams 6,804 real bars; an *unvalidated* signal from a real strategy is rejected under R6 by name; a validated one is accepted; both halves' tests and CI green |
| 1 | One strategy from a named paper, through the full battery, honestly reported | An experiment config committed *before* the run; a report leading with the gates; a signal file whose `validation` block came from the manifest — `pass` or `fail`, either is done |
| 2 | Pre-trade check and paper execution | OhCamel gains the fork-based check in its own repo under its invariants; the core fills at next open with the shared cost model; a hand-computable P&L case in the tests; a fill that would breach a limit is rejected with the limit named |
| 3 | RAG: papers and trade explanations | A question about the paper answered with a citation; a trade explained from the signal, the check, and the fill |
| 4 | Dashboard on Vercel | Reads the core's API; nothing computed in the browser |

Phase 0 needs nothing from anyone. Phase 1 needs the owner to name the
hypothesis (the brief's own trend-following experiments are the default).
Phase 2 touches OhCamel and goes through that repo's spec discipline.

## Testing

- **Contract:** the same JSON Schema validated from Python (`jsonschema`) and
  parsed in OCaml; fixture documents for every rule on both sides.
- **Intake:** one hand-derived test per rule, per side of its boundary.
- **Replay:** deterministic order, bar count asserted, provenance refused when
  absent.
- **Execution (Phase 2):** a three-bar hand-computable P&L including cost; a
  fill that breaches a limit; sign convention checked explicitly.
- **Battery:** owned by `fdq`; this repo tests that it *was run* (manifest
  present, newer than the config) rather than re-testing the statistics.
- **CI:** Python job (uv, pytest, ruff, mypy); OCaml job (setup-ocaml, pin
  `ohcamel` from GitHub, build, test). Benchmarks never gate.

## Risks, stated

- **The OCaml build is heavy.** Linking `ohcamel` brings Owl, OpenBLAS,
  Async and TLS into this binary; a fresh CI build is twenty minutes. Accepted:
  it is the price of OCaml being in the path rather than beside it.
- **`fdq` is pinned to a feature branch.** Its validation code lives at commit
  `a765fcf` on `feat/phase1-2-and-dashboard`, ahead of `main`. Pinning a SHA is
  safe; merging that branch upstream is the owner's housekeeping.
- **One universe, nine names.** Enough for the mechanism; too few for a
  cross-sectional claim. The README will say what it is.
