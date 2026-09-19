# The signal contract

One JSON document per signal, written by `research/` and enforced by
OhCamel's desk (`desk/contract.ml`).
[`signal.schema.json`](signal.schema.json) is the shape; this file is the
meaning, and in particular the rules the core applies before a target weight
can become an order: R1 through R7. R8 is listed below for completeness but is
not enforced (ruling 3). The rules are numbered so that a rejection can name
one and a test can be written against one.

The principle behind all of them: **the core never trusts Python's timing, only
its output.** Every temporal field in a signal is a *claim*. The core checks it
against the only clock it believes, which is the sequence of bars it has itself
received.

## Rules

| | Rule | What it catches |
|---|---|---|
| **R1** | `schema_version` is exactly `1` | a producer the core does not understand |
| **R2** | `strategy` is registered with the core | a slug nobody configured limits for |
| **R3** | `as_of` is not after the core's latest bar date | a signal from the future — Python's clock was wrong, or the file was fabricated |
| **R4** | `as_of` is within `max_age` trading days of the latest bar (default 3) | a stale signal. Age is counted in bars the core has seen after `as_of`, so no calendar is needed |
| **R5** | `sequence` is greater than the last accepted sequence for that strategy | a replayed, duplicated, or out-of-order file |
| **R6** | `validation.status` is `pass` | an unvalidated or failed strategy. These are **advisory**: the core logs them with the reason and never sizes them |
| **R7** | every symbol is in the universe; each \|weight\| ≤ 1; Σ\|weight\| ≤ 1 + 1e-9 | malformed or over-levered targets |
| **R8** | `data_hash` equals the core's own hash of its bars up to `as_of` | a signal computed on different data than the core holds. Not yet enforced (ruling 3); the finish plan's Stage 5 decides, from evidence, whether the research service's bars and the core's agree, and only then builds and enforces the check (ruling 16) |

Rules are applied in order and the first failure is reported. A document that
fails R2 and R6 is reported as R2.

## Semantics of the fields

- `as_of` is the date of the bar at whose **close** the signal was computed. It
  is actionable at the **next** bar's open and never on `as_of` itself. This is
  the no-lookahead rule expressed as a data contract.
- `computed_at` is wall-clock and informational. The core does not act on it,
  because a wall clock is exactly the thing this contract refuses to trust.
- `params_hash` is `sha256:` over the canonical JSON of the strategy's
  parameters (sorted keys, no whitespace). Two signals with different hashes
  came from different strategies, whatever their slugs say.
- `data_hash` is `sha256:` over the canonical bar text the strategy read: one
  line per bar, `symbol,date,open,high,low,close,volume`, sorted by
  `(date, symbol)`, floats formatted with `repr`.
  `research/src/ohcamel_research/contract.py` computes it. R8, the rule that
  would check it against the core's own bars, is not enforced in OhCamel
  (§3.12 of the desk's design; ruling 3), so `desk/contract.ml` does not
  compute a second copy to check it against.
- `validation` is written by the code that ran the battery, from its manifest,
  never by hand. `gates_version` is the date of the charter whose gates were
  applied.
- `targets` are portfolio weights, not orders. Turning them into orders is the
  core's job, after R1–R7 (R8 is not enforced, ruling 3) and after OhCamel's
  pre-trade check.

## Examples

[`examples/`](examples/) holds one document per rule, plus one that passes, and
[`examples/expected.json`](examples/expected.json) states the outcome each
should produce under a stated core clock. Both test suites read that manifest,
so the two sides cannot drift apart without a test noticing.

## Versioning

Any change to a required field, a rule, or the hash recipe bumps
`schema_version`. The core refuses versions it does not know (R1) rather than
guessing.
