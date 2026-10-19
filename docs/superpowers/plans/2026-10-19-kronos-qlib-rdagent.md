# Kronos, qlib and RD-Agent in OhCamel

*Written 2026-10-19. The owner asked for three outside projects to be brought in "wherever best judgement
says" and delegated the choices. This file records what was taken, what was refused, and why. The
refusals are as deliberate as the adoptions.*

The test applied to every candidate: does it make a number more defensible under
[`CHARTER.md`](../../CHARTER.md), or only make the project look larger? Both Microsoft repositories are MIT
licensed, and so is Kronos. Every ported file names its source and keeps the notice.

## What was built

| # | From | What | Where | Status |
|---|---|---|---|---|
| 1 | [Kronos](https://github.com/shiyu-coder/Kronos) | **EXP-K01.** Kronos-small's *sampled* next bar as a one-day variance forecast, scored against GARCH/GJR/EGARCH/EWMA with P2's own QLIKE, DM and MCS on sessions after the weights were frozen (2025-06-30) | `research/kronos/`, `quant/.../models/kronos_k01.py`, `research/experiments/EXP-K01/` | **Done. Verdict: DOES NOT BEAT THE INCUMBENTS** (pooled DM vs EWMA p = 0.969; in the 10% MCS on 3 of 9, where 5 were needed). The owner approved the pre-registration before any result existed. Kronos over-forecasts variance on every name (6.7× realised on SPY, 32× on GLD). The verdict is unchanged on the 7 names without the fixture defect below. See `research/experiments/EXP-K01/report.md` |
| 2 | [qlib](https://github.com/microsoft/qlib) | **A factor expression language** with qlib's 41 operators, ported, and **Alpha158** on it | `quant/.../factors/expr.py`, `alpha158.py` | Done. 157 of 158 features equal qlib 0.9.7's own output (values and NaN pattern) |
| 3 | qlib | **The signal report**: IC and rank IC with HAC t, IC decay by horizon, lag-1 rank autocorrelation, quantile-group and long-short returns | `quant/.../factors/workbench.py` | Done. Reported, never gated |
| 4 | [RD-Agent](https://github.com/microsoft/RD-Agent) | **The research loop, honest**: hypothesis first, RD-Agent's factor lint and redundancy screen, exploration window only, and an append-only trial ledger that supplies `n_trials` | `quant/.../factors/workbench.py`, `make factor` | Done |

### Decisions inside each, and the reasons

**Kronos.**

- Upstream's `KronosPredictor` *averages* its sample paths, which destroys the spread a variance forecast
  needs, so `ohcamel_kronos.sampler` re-implements the one-step inference and keeps every draw. It is pinned
  to upstream by greedy-decoding equality: with tiny models offline, and with the real pinned weights
  against upstream's own regression data behind `OHCAMEL_KRONOS_NETWORK=1`.
- torch lives only in `research/kronos` (its own uv project), never in `quant/` or an image.
- The scorer reads a committed CSV, the same pattern as `docs/crisis/*.csv`.
- top_p is 1.0 (nucleus sampling cuts the tails, which would decide a variance answer in advance), the
  lookback is 400 (the README's own example, not tuned), and 128 draws run on CPU with per-name-day seeds.
- A finding before any score: the tokenizer reconstructs the last bar with a visible level error, and
  the variance forecast anchors on the reconstruction so that error cancels. A diagnostic anchored on
  the *actual* close measures the error and is not eligible to be the result.

**qlib's operators.**

- They are ported, not imported. qlib as a dependency brings MLflow, redis, pymongo and a binary data
  store, which duplicate DuckDB, provenance and the job ledger, on a 4 GB host.
- The default mode departs from qlib in three places, all toward the charter:
  - `Ref(x, -n)` is a `LookAheadError` at parse time.
  - Windows whose value depends on where the loaded data starts (`N = 0`, `Ref(x, 0)`) are refused.
  - A window that is not full is NaN. qlib's `min_periods=1` calls a 3-day mean a 60-day one.
- `compat=True` restores qlib exactly. That is how parity is proven.
- `VWAP0` is omitted rather than invented, because the bars carry no VWAP.
- The parity reference is qlib's own output on our own bars. `tools/qlib_golden.py` regenerates it,
  byte-identical, from a throwaway environment.

**RD-Agent.**

- Kept:
  - Its evaluators' structural checks.
  - Its redundancy screen, fixed to use |correlation|. RD-Agent's signed test lets the exact negation of
    an existing factor through.
  - The shape of its loop.
- Dropped: the LLM proposing hypotheses, and the selection on the test segment.
- The workbench refuses to run without a written hypothesis and refuses any date after 2023-12-31.
  Its labels are built from exploration prices only, so the last dates are purged rather than scored on
  holdout opens; a test poisons every later price and shows that nothing moves.

## What was refused, and why

| Candidate | Why not |
|---|---|
| RD-Agent's autonomous R&D loop | An LLM writes the hypotheses ("the owner owns hypotheses") and `feedback.py` picks the best round on the **test segment** with no trial deflation. It also needs Linux, Docker, a CUDA image and a paid API key. It breaks the charter three ways |
| qlib's backtest / Exchange / NestedExecutor | China-specific market rules (limit up/down, 100-share lots). A second simulator would break "paper and backtest read the same cost configuration". Its `risk_analysis` sums returns and annualises on 238 days |
| qlib's deep model zoo (ALSTM, TRA, HIST, …) | torch on the droplet, parameter-heavy for a 9 to 45 name panel, and each one is a new hypothesis plus a hyperparameter search. "Simple models preferred" |
| qlib's data store, PIT database, MLflow recorder, Mongo task manager | Duplicates the DuckDB warehouse (whose `sec_facts` already keeps filing dates and restatements), the provenance sidecars and the artifact ledger |
| Kronos in the nightly `vol.forecast_league` | Needs torch in the worker image (over 1 GB resident on a 4 GB host). It is reconsidered only if EXP-K01 PASSES and the owner confirms the pre-registration |

## What is left, and whose it is

1. ~~Owner: confirm the EXP-K01 pre-registration~~ (approved 2026-10-19). ~~Engineering: run and score
   EXP-K01~~ (done; FAIL, as above).
2. **Owner, found by EXP-K01:** `fixtures/history/XLE.parquet` and `XLK.parquet` carry an UNADJUSTED 2-for-1
   split on 2025-12-05 (`close_adj` equals `close`, a -50% one-day "return"). Any experiment that reads XLE or
   XLK after that date sees a fictitious crash. That includes EXP-I01–I03 holdouts that run into 2026 and
   INC-01's roster. Repairing a published fixture changes the evidence behind committed manifests, so it is the
   owner's decision. It is detected and pinned by a test, not repaired.
3. **Owner:** the first real `make factor` entry. The ledger starts empty on purpose. Every trial from now
   on is counted.
4. **Optional, small:** POET as a covariance-league contestant (`qlib/model/riskmodel/poet.py`, numpy only).
   Not started.

## On the droplet (2026-10-19, owner's request: "one safe, expansive, effective, efficient, fast package")

Three scheduled kinds, shown on Strategy Lab → **Watch** (`/research?view=watch`), each with a Methodology entry
and a Ledger row. None of them sizes, promotes, or writes a hypothesis.

| Kind | When | Class | What it does |
|---|---|---|---|
| `ops.data_integrity` | weekdays 19:15 | S | Every warehouse series and committed fixture: split signatures the adjusted close did not absorb, bad prints, non-positive prices, calendar faults, staleness. FAIL names the series. Detects, never repairs |
| `kronos.shadow` | weekdays 22:15 | L, heavy | Kronos (EXP-K01's design) and GARCH/GJR/EGARCH/EWMA forecast the next session before it exists. The ledger is append-only (a rerun cannot revise a forecast), and realised rows are scored by QLIKE with the pooled DM K01 used |
| `factors.screen` | Sun 07:00 | M, heavy | Every distinct expression in `research/experiments/FACTORS/trials.jsonl`, on the warehouse universe, exploration window only, against all 157 Alpha158 features evaluated one at a time |

**Efficiency, measured.** Kronos sampling all 128 draws in one batch peaked at **2.5 GB** resident, twice
the worker's 1.25 GB cap. Drawing 8 at a time peaks at **0.58 GB** (0.29 GB of that is torch and the model
loading), with the same wall time: 9 forecasts in about 75 s on two threads. Chunked draws are reproducible and
come from the same distribution. They are not bit-identical to EXP-K01's single batch.

**Safety.**
- torch is CPU-only and lives in its own venv (`/opt/kronos`), run as a subprocess, so quant's lockfile and
  tests never import it.
- The pinned weights are baked into plain directories and loaded by path with the Hub disabled, which works
  in the read-only container.
- The image build runs one real forecast as the unprivileged user, so a broken Kronos fails the build, not
  the night.
- CI gained a `kronos-test` job.
