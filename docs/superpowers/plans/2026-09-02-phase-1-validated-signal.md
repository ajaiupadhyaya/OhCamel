# Phase 1 — One Strategy Through the Battery — Implementation Plan

> **Status (2026-09-19):** historical record of a finished, merged phase. Superseded as the active plan by `docs/superpowers/plans/2026-09-19-final-completion.md` ("the finish"); see `docs/superpowers/specs/2026-09-19-the-finish.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one pre-registered strategy from a named paper through the full charter battery on ten years of real bars, write a manifest the signal's `validation` block is copied from, and report the verdict honestly — pass or fail, either is done.

**Architecture:** `fdq` does the walk-forward, DSR and PBO (its `run_experiment` on a committed config). The `alpha` package adds the four gates the charter requires that `fdq`'s runner does not compute — PSR, a stationary-block bootstrap of the out-of-sample Sharpe, regime stress, and a cost sweep — from the walk-forward's out-of-sample returns, and writes one `manifest.json` that says which gates passed. `alpha signal emit --validation-from manifest.json` is the only way a signal's `validation` block is ever written. The OCaml core changes not at all in this phase; its R6 is what all of this is for.

**Tech Stack:** Python 3.12, uv, `fdq` at `652474c` (walk-forward, DSR, PBO, friction model), pandas, numpy, pytest. Real bars from `fdq`'s Alpaca cache with provenance sidecars.

**Spec:** `docs/superpowers/specs/2026-09-02-ohcamel-alpha-design.md` — Phases table (Phase 1 row), *Validation gates*, *Data policy*, and Decision 5 (lineage). Gates: `docs/CHARTER.md`.

## Global Constraints

- **The hypothesis is the owner's.** Task 1 pre-registers it in writing, with rationale, assumptions and failure modes, *before* any run. The code never picks a strategy because it scored well.
- **No synthetic market data anywhere a number is reported.** Every bar file used carries `fdq`'s provenance sidecar; the replay's `read_provenance` is the only reader.
- **Gates are applied literally and never loosened** (charter): DSR ≥ 0.30, PSR ≥ 0.70, bootstrap lower-5% Sharpe > 0, positive in ≥ 3 regimes, holdout (walk-forward out-of-sample) positive, cost sweep reported at every level. A failing gate is reported, not tuned away.
- **Point-in-time.** Parameters are chosen on each fold's train window only (`fdq.validation.walkforward.walk_forward` does this); nothing here looks at the test window to choose anything.
- **A manifest older than its config is stale evidence.** `alpha signal emit --validation-from` refuses a manifest whose `config_sha256` does not match the config on disk.
- **Reproducible.** Seed 42 everywhere; the manifest records the `fdq` commit, the data provenance, the config hash and the gates version `2026-09-02`.
- **Universe:** the nine ETFs (SPY, QQQ, IWM, TLT, IEF, GLD, XLE, XLF, XLK). Strategies are single-symbol long/flat in this phase, because that is what `fdq` ships and what the paper describes.
- Python style: ruff line length 100, `from __future__ import annotations`, tests under `research/tests/`, run with `uv --directory research run pytest -q`.

## File Structure

```
fixtures/history/                      NEW  nine ETFs, 2016-06-01 → 2026-06-01, real, with sidecars (~1 MB)
fixtures/history/README.md             NEW  what it is, where from, how to refresh
research/experiments/EXP-A01/
  hypothesis.md                        NEW  the pre-registration (Task 1)
  config.yaml                          NEW  fdq experiment config, walk-forward mode
  results/                             GENERATED (gitignored except manifest.json and report.md)
  manifest.json                        GENERATED, COMMITTED — the gates
  report.md                            GENERATED, COMMITTED — the verdict
research/src/alpha/battery/__init__.py NEW
research/src/alpha/battery/gates.py    NEW  psr_gate, bootstrap_gate, regime_gate, cost_sweep — pure functions on return series
research/src/alpha/battery/run.py      NEW  run(config) -> manifest: fdq run_experiment + the gates + manifest writing
research/src/alpha/battery/manifest.py NEW  Manifest dataclass, load/dump, freshness check
research/src/alpha/signal.py           MODIFY  emit(..., validation=...) unchanged; add validation_from_manifest(path) -> dict
research/src/alpha/cli.py              MODIFY  `alpha battery run EXP_DIR`, `alpha signal emit --validation-from PATH`
research/src/alpha/replay.py           MODIFY  load_bars(fixtures) already parametric; no change unless a helper is needed
research/tests/test_gates.py           NEW  hand-derived tests for each gate
research/tests/test_manifest.py        NEW  freshness + round trip
research/tests/test_battery_smoke.py   NEW  the whole battery on the 2018-2020 fixture (small grid), asserting shape not values
Makefile                               MODIFY  `phase1` target; `check` unchanged
README.md                              MODIFY  Phase 1 section quoting the manifest's verdict
.gitignore                             MODIFY  research/experiments/*/results/ but not manifest.json / report.md
```

---

### Task 1: Pre-register the hypothesis and the experiment config

**Files:**
- Create: `research/experiments/EXP-A01/hypothesis.md`
- Create: `research/experiments/EXP-A01/config.yaml`

**Interfaces:**
- Produces: a config in `fdq`'s experiment format (`id`, `title`, `mode: walkforward`, `n_folds`, `strategies`, `capital_tiers`, `window`, `seed`) that `fdq.experiment.runner.run_experiment(Path)` accepts unchanged.

This task has no code to test. Its acceptance is that both files exist and are committed *before* Task 5 runs anything, and the commit timestamp proves it.

- [ ] **Step 1: Write the pre-registration**

`research/experiments/EXP-A01/hypothesis.md`:

```markdown
# EXP-A01 — Does the 10-month moving-average rule survive friction on liquid ETFs?

**Paper.** Faber, M. (2007), "A Quantitative Approach to Tactical Asset
Allocation", *Journal of Wealth Management* 9(4). The rule: hold the asset
when its month-end price is above its 10-month simple moving average,
otherwise hold cash. Daily equivalent used here: price above its 200-day SMA
(fdq's `ma_crossover` with `fast = 1`), decided at the close, executed at the
next open.

**Owner's hypothesis.** The rule reduces drawdown materially in equity and
rates ETFs at the cost of some return, and its risk-adjusted return survives
retail friction. Registered before any run on this data window.

**Economic rationale.** Time-series momentum: prices under-react to slow
information and trend-following captures the drift, while the other side is
taken by rebalancers and forced sellers in drawdowns who accept the loss for
liquidity or mandate reasons. The rule's main effect is expected to be
drawdown avoidance, not return enhancement.

**Assumptions.** Daily bars, Alpaca IEX, 2016-06-01 to 2026-06-01. Nine-ETF
universe, no delisted names needed. Friction model `fdq` v1.0.0: per-symbol
half-spread in bps plus regulatory fees; cost sweep at ×1, ×2, ×5. Signals
at the close, fills at the next open. Long/flat only, one symbol per
strategy.

**Grid, fixed in advance.** `fast ∈ {1}`, `slow ∈ {150, 200, 250}` — three
trials per symbol, so DSR deflates by three. Two symbols: SPY (equities) and
TLT (rates). Anything outside this grid is a new experiment, not a tweak.

**How it fails.** Whipsaw in sideways markets (2015, 2018 H1, 2022 H2)
generates losing round trips; a decade dominated by one long uptrend can make
any long-biased rule look good in-sample; and the 200-day rule's edge in the
literature is mostly pre-2010. Three regimes negative would refute it.

**Kill criteria.** Any charter gate failing is a fail. A pass with PBO above
0.5 is reported as "pass, fragile" and is not promoted to the core without
a second, independent window.
```

- [ ] **Step 2: Write the config**

`research/experiments/EXP-A01/config.yaml`:

```yaml
id: EXP-A01
title: "Does the 10-month moving-average rule survive friction on liquid ETFs?"
friction_version: "1.0.0"
mode: walkforward
n_folds: 4
strategies:
  - ma_crossover: {symbol: SPY, grid: {fast: [1], slow: [150, 200, 250]}}
  - ma_crossover: {symbol: TLT, grid: {fast: [1], slow: [150, 200, 250]}}
capital_tiers: [50000]
window: {start: "2016-06-01", end: "2026-06-01"}
stress_multipliers: [1, 2, 5]
seed: 42
```

- [ ] **Step 3: Commit, so the timestamp precedes any run**

```bash
git add research/experiments/EXP-A01/hypothesis.md research/experiments/EXP-A01/config.yaml
git commit -m "EXP-A01: pre-register the 10-month MA rule (Faber 2007) on SPY and TLT"
```

---

### Task 2: The ten-year history fixture, with provenance

**Files:**
- Create: `fixtures/history/{SPY,QQQ,IWM,TLT,IEF,GLD,XLE,XLF,XLK}.parquet` and `.parquet.meta.json`
- Create: `fixtures/history/README.md`
- Create: `research/src/alpha/fixtures.py`
- Test: `research/tests/test_history_fixture.py`

**Interfaces:**
- Produces: `alpha.fixtures.HISTORY = REPO_ROOT / "fixtures" / "history"`, and `alpha.replay.load_bars(HISTORY)` returning the long frame for 2016-06-01 → 2026-06-01.
- Produces: `alpha.fixtures.refresh_history(source_cache: Path) -> None`, the same recipe that made `fixtures/bars/`.

- [ ] **Step 1: Write the failing test**

`research/tests/test_history_fixture.py`:

```python
from datetime import date

from alpha.fixtures import HISTORY
from alpha.replay import load_bars


def test_history_covers_ten_years_of_the_nine_etfs_with_provenance():
    bars = load_bars(HISTORY)  # raises ProvenanceError if any sidecar is missing
    assert sorted(bars["symbol"].unique()) == [
        "GLD", "IEF", "IWM", "QQQ", "SPY", "TLT", "XLE", "XLF", "XLK"
    ]
    assert bars["date"].min() == date(2016, 6, 1)
    assert bars["date"].max() >= date(2026, 5, 29)
    per_symbol = bars.groupby("symbol").size()
    assert per_symbol.min() > 2400  # ~252 trading days x 10 years, minus holidays
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `uv --directory research run pytest tests/test_history_fixture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpha.fixtures'`

- [ ] **Step 3: Write the refresh recipe**

`research/src/alpha/fixtures.py`:

```python
"""Committed real-data fixtures, and the recipe that makes them.

Both fixture sets are slices of fdq's Alpaca cache, carried with fdq's own
provenance sidecars. `refresh_*` re-slices from a local cache; they are run by
a person, on purpose, and the result is committed. Nothing here downloads.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from fdq.data.provenance import read_provenance, write_provenance

from alpha import REPO_ROOT

UNIVERSE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]
BARS = REPO_ROOT / "fixtures" / "bars"        # 2018-2020, the dev replay
HISTORY = REPO_ROOT / "fixtures" / "history"  # 2016-2026, the battery's data


def _slice(source_cache: Path, out: Path, start: date, end: date) -> int:
    out.mkdir(parents=True, exist_ok=True)
    rows = 0
    for sym in UNIVERSE:
        src = source_cache / f"{sym}.parquet"
        df = pd.read_parquet(src)
        try:
            source = str(read_provenance(src).get("source", "alpaca"))
        except Exception:
            source = "alpaca"
        sl = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        if sl.empty:
            raise RuntimeError(f"{sym}: no bars in {start}..{end} in {source_cache}")
        dst = out / f"{sym}.parquet"
        sl.to_parquet(dst)
        write_provenance(dst, source=source, data_kind="historical", start=start, end=end, symbol=sym)
        rows += len(sl)
    return rows


def refresh_bars(source_cache: Path) -> int:
    return _slice(source_cache, BARS, date(2018, 1, 1), date(2020, 12, 31))


def refresh_history(source_cache: Path) -> int:
    return _slice(source_cache, HISTORY, date(2016, 6, 1), date(2026, 6, 1))
```

- [ ] **Step 4: Produce the fixture from the local cache and write its README**

Run (once, by hand; the cache is `capitallimits/data/raw`):

```bash
uv --directory research run python -c "from pathlib import Path; from alpha.fixtures import refresh_history; print(refresh_history(Path.home()/'Documents/capitallimits/data/raw'), 'bars')"
```

`fixtures/history/README.md`:

```markdown
# fixtures/history

Nine ETFs, daily bars, 2016-06-01 to 2026-06-01, sliced from Five Dollar
Quant's Alpaca cache with its provenance sidecars unchanged. This is the data
the validation battery runs on; `fixtures/bars/` (2018–2020) is the smaller
dev replay. Refresh with `alpha.fixtures.refresh_history(<cache dir>)`.
Never edit a parquet by hand; the sidecar is the audit trail.
```

- [ ] **Step 5: Run the test, confirm it passes**

Run: `uv --directory research run pytest tests/test_history_fixture.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add fixtures/history research/src/alpha/fixtures.py research/tests/test_history_fixture.py
git commit -m "fixtures: ten years of the nine ETFs, real, with provenance, for the battery"
```

---

### Task 3: The four gates fdq does not compute, as pure functions

**Files:**
- Create: `research/src/alpha/battery/__init__.py` (empty)
- Create: `research/src/alpha/battery/gates.py`
- Test: `research/tests/test_gates.py`

**Interfaces:**
- Produces:
  - `psr_gate(returns: pd.Series, threshold: float = 0.70) -> GateResult`
  - `bootstrap_gate(returns: pd.Series, n_samples: int = 1000, block: int = 21, seed: int = 42) -> GateResult`
  - `regime_gate(returns: pd.Series, regimes: dict[str, tuple[date, date]] = REGIMES, min_positive: int = 3) -> GateResult`
  - `cost_sweep(run_at_multiplier: Callable[[float], pd.Series], multipliers: tuple[float, ...] = (1.0, 2.0, 5.0)) -> GateResult`
  - `GateResult(name: str, passed: bool | None, value: float | None, detail: dict)`; `passed is None` means "reported, not gated" (the cost sweep).
  - `REGIMES`: `{"2018_q4_selloff": (2018-10-01, 2018-12-31), "2020_covid": (2020-02-19, 2020-06-30), "2022_rate_shock": (2022-01-01, 2022-12-31), "2023_recovery": (2023-01-01, 2023-12-31), "2024": (2024-01-01, 2024-12-31)}`
- Consumes: `fdq.validation.dsr.probabilistic_sharpe(returns, sr_benchmark)` and `fdq.validation.metrics.sharpe(returns, annualize=...)`.

- [ ] **Step 1: Write the failing tests**

`research/tests/test_gates.py`:

```python
from datetime import date

import numpy as np
import pandas as pd
import pytest

from alpha.battery.gates import REGIMES, bootstrap_gate, cost_sweep, psr_gate, regime_gate


def daily(values: list[float], start: str = "2018-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx)


def test_psr_gate_passes_a_clearly_positive_series_and_fails_a_zero_one():
    rng = np.random.default_rng(0)
    up = daily(list(rng.normal(0.001, 0.005, 800)))     # ~SR 3 annualised
    flat = daily(list(rng.normal(0.0, 0.005, 800)))
    assert psr_gate(up).passed is True
    assert psr_gate(flat).passed is False
    assert 0.0 <= psr_gate(flat).value <= 1.0


def test_bootstrap_gate_lower_5pct_is_positive_only_for_a_real_edge():
    rng = np.random.default_rng(1)
    up = daily(list(rng.normal(0.001, 0.005, 800)))
    noise = daily(list(rng.normal(0.0, 0.005, 800)))
    g_up, g_noise = bootstrap_gate(up), bootstrap_gate(noise)
    assert g_up.passed is True and g_up.value > 0
    assert g_noise.passed is False
    assert g_up.detail["n_samples"] == 1000 and g_up.detail["block"] == 21


def test_bootstrap_is_seeded_and_reproducible():
    rng = np.random.default_rng(2)
    r = daily(list(rng.normal(0.0005, 0.005, 500)))
    assert bootstrap_gate(r, seed=7).value == bootstrap_gate(r, seed=7).value
    assert bootstrap_gate(r, seed=7).value != bootstrap_gate(r, seed=8).value


def test_regime_gate_counts_regimes_with_positive_cumulative_return():
    # +1 bp every day from 2018 through 2024: positive in every regime present.
    idx = pd.bdate_range("2018-01-01", "2024-12-31")
    r = pd.Series(0.0001, index=idx)
    g = regime_gate(r)
    assert g.passed is True
    assert g.value == len(REGIMES)
    # Now make 2020 and 2022 negative: 3 positive of 5, still passes; make 2024 negative too: fails.
    r2 = r.copy()
    r2.loc["2020-02-19":"2020-06-30"] = -0.001
    r2.loc["2022-01-01":"2022-12-31"] = -0.001
    assert regime_gate(r2).passed is True
    r2.loc["2024-01-01":"2024-12-31"] = -0.001
    assert regime_gate(r2).passed is False


def test_regime_gate_skips_regimes_the_series_does_not_cover():
    r = pd.Series(0.0001, index=pd.bdate_range("2023-01-01", "2023-12-31"))
    g = regime_gate(r)
    assert g.detail["covered"] == ["2023_recovery"]
    assert g.passed is False  # one positive regime is not three


def test_cost_sweep_reports_every_level_and_never_gates():
    calls = []

    def run(mult: float) -> pd.Series:
        calls.append(mult)
        return daily([0.001 - 0.0002 * mult] * 300)

    g = cost_sweep(run)
    assert g.passed is None
    assert calls == [1.0, 2.0, 5.0]
    assert list(g.detail["sharpe_by_multiplier"]) == ["1.0", "2.0", "5.0"]
    assert g.detail["sharpe_by_multiplier"]["5.0"] < g.detail["sharpe_by_multiplier"]["1.0"]
```

- [ ] **Step 2: Run them, confirm they fail**

Run: `uv --directory research run pytest tests/test_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpha.battery'`

- [ ] **Step 3: Implement the gates**

`research/src/alpha/battery/__init__.py`: empty file.

`research/src/alpha/battery/gates.py`:

```python
"""The charter's gates that fdq's runner does not compute, as pure functions
of a return series. Each returns a GateResult that says what it measured and
whether it passed; the thresholds are the charter's and are not parameters
anyone tunes per strategy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from fdq.validation.dsr import probabilistic_sharpe
from fdq.validation.metrics import sharpe

REGIMES: dict[str, tuple[date, date]] = {
    "2018_q4_selloff": (date(2018, 10, 1), date(2018, 12, 31)),
    "2020_covid": (date(2020, 2, 19), date(2020, 6, 30)),
    "2022_rate_shock": (date(2022, 1, 1), date(2022, 12, 31)),
    "2023_recovery": (date(2023, 1, 1), date(2023, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
}


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool | None  # None: reported, not gated
    value: float | None
    detail: dict[str, Any] = field(default_factory=dict)


def psr_gate(returns: pd.Series, threshold: float = 0.70) -> GateResult:
    """Probabilistic Sharpe Ratio against a zero benchmark: Pr(true SR > 0)."""
    psr = float(probabilistic_sharpe(returns, 0.0))
    return GateResult("psr", psr >= threshold, psr, {"threshold": threshold})


def _block_bootstrap_sharpes(
    returns: np.ndarray, n_samples: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Stationary block bootstrap (Politis & Romano 1994): blocks of geometric
    length with mean `block`, wrapped circularly, so autocorrelation in the
    series survives resampling."""
    n = len(returns)
    out = np.empty(n_samples)
    p = 1.0 / block
    for i in range(n_samples):
        idx = np.empty(n, dtype=int)
        pos = rng.integers(n)
        for t in range(n):
            if t > 0 and rng.random() < p:
                pos = rng.integers(n)
            idx[t] = pos
            pos = (pos + 1) % n
        sample = returns[idx]
        sd = sample.std(ddof=1)
        out[i] = 0.0 if sd == 0 else (sample.mean() / sd) * np.sqrt(252.0)
    return out


def bootstrap_gate(
    returns: pd.Series, n_samples: int = 1000, block: int = 21, seed: int = 42
) -> GateResult:
    """Lower 5th percentile of the bootstrapped annualised Sharpe must be > 0."""
    rng = np.random.default_rng(seed)
    sharpes = _block_bootstrap_sharpes(returns.to_numpy(dtype=float), n_samples, block, rng)
    lo, med, hi = (float(np.percentile(sharpes, q)) for q in (5, 50, 95))
    return GateResult(
        "bootstrap_sharpe_lower5",
        lo > 0.0,
        lo,
        {"n_samples": n_samples, "block": block, "seed": seed, "p50": med, "p95": hi},
    )


def regime_gate(
    returns: pd.Series,
    regimes: dict[str, tuple[date, date]] = REGIMES,
    min_positive: int = 3,
) -> GateResult:
    """Positive cumulative return in at least `min_positive` of the named
    regimes the series actually covers. A regime the series does not reach is
    skipped and listed, not counted as a pass."""
    idx = pd.to_datetime(returns.index)
    covered, positive, by_regime = [], 0, {}
    for name, (a, b) in regimes.items():
        m = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
        if not m.any():
            continue
        covered.append(name)
        cum = float((1.0 + returns[m]).prod() - 1.0)
        by_regime[name] = cum
        positive += cum > 0
    return GateResult(
        "regimes_positive",
        positive >= min_positive,
        float(positive),
        {"covered": covered, "by_regime": by_regime, "min_positive": min_positive},
    )


def cost_sweep(
    run_at_multiplier: Callable[[float], pd.Series],
    multipliers: tuple[float, ...] = (1.0, 2.0, 5.0),
) -> GateResult:
    """Sharpe at each friction multiplier. Reported, never gated: the charter
    wants the sensitivity shown, not a single assumption blessed."""
    by = {str(m): float(sharpe(run_at_multiplier(m))) for m in multipliers}
    return GateResult("cost_sweep", None, None, {"sharpe_by_multiplier": by})
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `uv --directory research run pytest tests/test_gates.py -v`
Expected: PASS (6 tests). If `psr_gate` on the flat series is borderline, widen the noise sample to 1500; do not touch the threshold.

- [ ] **Step 5: Commit**

```bash
git add research/src/alpha/battery research/tests/test_gates.py
git commit -m "battery: PSR, block-bootstrap Sharpe, regime and cost-sweep gates as pure functions"
```

---

### Task 4: The manifest, and its freshness rule

**Files:**
- Create: `research/src/alpha/battery/manifest.py`
- Test: `research/tests/test_manifest.py`

**Interfaces:**
- Produces:
  - `Manifest` dataclass: `experiment: str`, `strategy: str`, `symbol: str`, `params: dict`, `status: Literal["pass","fail"]`, `gates: list[GateResult]`, `dsr: float`, `psr: float`, `pbo: float`, `gates_version: str`, `config_sha256: str`, `fdq_commit: str`, `data: dict` (fixture dir, sidecar summary), `ran_at: str`.
  - `Manifest.to_json() -> str`, `Manifest.from_path(path) -> Manifest`.
  - `is_fresh(manifest: Manifest, config_path: Path) -> bool` — true iff `sha256(config bytes)` equals `manifest.config_sha256`.
  - `validation_block(manifest: Manifest, manifest_path: Path) -> dict` — the contract's `validation` object: `{"status", "gates_version", "dsr", "psr", "pbo", "manifest": "sha256:<hash of the manifest file>"}`.
- Consumes: `GateResult` from Task 3; `alpha.contract.sha256`.

- [ ] **Step 1: Write the failing tests**

`research/tests/test_manifest.py`:

```python
import json
from pathlib import Path

from alpha.battery.gates import GateResult
from alpha.battery.manifest import Manifest, is_fresh, validation_block


def make(tmp_path: Path, status: str = "pass") -> tuple[Manifest, Path, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("id: X\n")
    m = Manifest(
        experiment="X", strategy="ma_crossover", symbol="SPY", params={"fast": 1, "slow": 200},
        status=status, gates=[GateResult("psr", True, 0.9, {})], dsr=0.4, psr=0.9, pbo=0.2,
        gates_version="2026-09-02", config_sha256=Manifest.sha256_of(cfg), fdq_commit="652474c",
        data={"fixtures": "fixtures/history"}, ran_at="2026-09-02T00:00:00Z",
    )
    p = tmp_path / "manifest.json"
    p.write_text(m.to_json())
    return m, p, cfg


def test_round_trip(tmp_path: Path):
    m, p, _ = make(tmp_path)
    assert Manifest.from_path(p) == m


def test_fresh_iff_config_unchanged(tmp_path: Path):
    m, p, cfg = make(tmp_path)
    assert is_fresh(m, cfg)
    cfg.write_text("id: X\nn_folds: 5\n")
    assert not is_fresh(m, cfg)


def test_validation_block_is_the_contracts_shape(tmp_path: Path):
    m, p, _ = make(tmp_path)
    v = validation_block(m, p)
    assert set(v) == {"status", "gates_version", "dsr", "psr", "pbo", "manifest"}
    assert v["status"] == "pass" and v["manifest"].startswith("sha256:")
    m2, p2, _ = make(tmp_path / "b", status="fail")
    assert validation_block(m2, p2)["status"] == "fail"
```

- [ ] **Step 2: Run them, confirm they fail**

Run: `uv --directory research run pytest tests/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`research/src/alpha/battery/manifest.py`:

```python
"""The battery's manifest: the one document a signal's validation block may
be copied from. It records what was run, on what, under which gates, and
what each gate said. A manifest whose config hash no longer matches the
config on disk is stale evidence and is refused."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from alpha.battery.gates import GateResult


@dataclass(frozen=True)
class Manifest:
    experiment: str
    strategy: str
    symbol: str
    params: dict[str, Any]
    status: Literal["pass", "fail"]
    gates: list[GateResult]
    dsr: float
    psr: float
    pbo: float
    gates_version: str
    config_sha256: str
    fdq_commit: str
    data: dict[str, Any]
    ran_at: str
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def sha256_of(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, sort_keys=True, default=str) + "\n"

    @classmethod
    def from_path(cls, path: Path) -> Manifest:
        d = json.loads(path.read_text())
        d["gates"] = [GateResult(**g) for g in d["gates"]]
        return cls(**d)


def is_fresh(manifest: Manifest, config_path: Path) -> bool:
    return Manifest.sha256_of(config_path) == manifest.config_sha256


def validation_block(manifest: Manifest, manifest_path: Path) -> dict[str, Any]:
    return {
        "status": manifest.status,
        "gates_version": manifest.gates_version,
        "dsr": manifest.dsr,
        "psr": manifest.psr,
        "pbo": manifest.pbo,
        "manifest": "sha256:" + Manifest.sha256_of(manifest_path),
    }
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `uv --directory research run pytest tests/test_manifest.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add research/src/alpha/battery/manifest.py research/tests/test_manifest.py
git commit -m "battery: the manifest, and the rule that a stale one is refused"
```

---

### Task 5: Run the battery: fdq's walk-forward plus the four gates, to a manifest

**Files:**
- Create: `research/src/alpha/battery/run.py`
- Modify: `research/src/alpha/cli.py` (add `battery run`)
- Modify: `.gitignore` (results dirs)
- Test: `research/tests/test_battery_smoke.py`

**Interfaces:**
- Produces: `run(experiment_dir: Path, fixtures: Path = HISTORY, seed: int = 42) -> list[tuple[Manifest, Path]]` — one manifest per strategy entry in the config, written to `experiment_dir/manifest.<strategy>_<symbol>.json`, plus `experiment_dir/report.md`.
- Consumes: `fdq.experiment.runner.run_experiment(config_path, tearsheet=False, report=True, data_dir=...)`; `fdq.validation.walkforward.walk_forward(strategy_name, base_params, grid, bars, tier, friction, macro, n_folds, scheme, seed) -> WalkForwardResult` with `.oos_returns`, `.oos_equity`, `.n_trials`; `fdq.frictions.config.load_friction_config(stress_multiplier=...)`; `fdq.validation.dsr.deflated_sharpe(returns, trial_sharpes)`; Task 3 gates; Task 4 manifest.

How the data reaches fdq: `run_experiment` takes `data_dir` and reads `<data_dir>/<SYMBOL>.parquet` through its own `ensure` layer, which checks provenance sidecars. Pass `fixtures/history` as `data_dir`. If `fdq.data.ensure` insists on a `macro.parquet`, copy fdq's cached `macro.parquet` into `fixtures/history/` with its sidecar in Task 2 (it is FRED data, real, and small).

- [ ] **Step 1: Write the failing smoke test**

`research/tests/test_battery_smoke.py`:

```python
"""The whole battery on the small 2018-2020 fixture with a one-point grid.
Asserts the SHAPE of what comes out, never a value: the numbers on this
window are not a result and must not be treated as one."""

import json
import shutil
from pathlib import Path

import yaml

from alpha.battery.manifest import Manifest, is_fresh
from alpha.battery.run import run
from alpha.fixtures import BARS


def test_battery_writes_a_fresh_manifest_per_strategy(tmp_path: Path):
    exp = tmp_path / "EXP-SMOKE"
    exp.mkdir()
    (exp / "config.yaml").write_text(yaml.safe_dump({
        "id": "EXP-SMOKE", "title": "smoke", "friction_version": "1.0.0",
        "mode": "walkforward", "n_folds": 2,
        "strategies": [{"ma_crossover": {"symbol": "SPY", "grid": {"fast": [1], "slow": [100]}}}],
        "capital_tiers": [50000], "window": {"start": "2018-01-02", "end": "2020-12-31"},
        "stress_multipliers": [1, 2], "seed": 42,
    }))
    out = run(exp, fixtures=BARS)
    assert len(out) == 1
    m, path = out[0]
    assert path.exists() and is_fresh(m, exp / "config.yaml")
    assert m.status in ("pass", "fail")
    assert {g.name for g in m.gates} >= {"dsr", "psr", "pbo", "bootstrap_sharpe_lower5", "regimes_positive", "holdout_positive", "cost_sweep"}
    assert m.fdq_commit and m.gates_version == "2026-09-02"
    assert (exp / "report.md").exists()
    assert Manifest.from_path(path) == m
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `uv --directory research run pytest tests/test_battery_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpha.battery.run'`

- [ ] **Step 3: Implement the runner**

`research/src/alpha/battery/run.py`:

```python
"""Run fdq's walk-forward on a pre-registered config, add the charter's
remaining gates from the out-of-sample returns, and write a manifest per
strategy. This is the only code that writes a manifest, and the manifest is
the only source of a signal's validation block."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from fdq.experiment.runner import run_experiment
from fdq.frictions.config import load_friction_config
from fdq.validation.dsr import deflated_sharpe
from fdq.validation.metrics import sharpe
from fdq.validation.walkforward import in_sample_return_matrix, walk_forward
from fdq.validation.pbo import probability_backtest_overfitting

from alpha.battery.gates import GateResult, bootstrap_gate, cost_sweep, psr_gate, regime_gate
from alpha.battery.manifest import Manifest
from alpha.contract import GATES_VERSION
from alpha.fixtures import HISTORY
from alpha.replay import load_bars
from alpha.signal import wide

DSR_MIN, PSR_MIN = 0.30, 0.70


def _fdq_commit() -> str:
    """The pinned fdq commit, read from the lockfile so it cannot drift from what ran."""
    lock = Path(__file__).resolve().parents[3] / "uv.lock"
    for line in lock.read_text().splitlines():
        if "capitallimits.git" in line and "#" in line:
            return line.split("#")[-1].strip().strip('"').strip("}").strip()
    return "unknown"


def _bars_for_fdq(fixtures: Path) -> pd.DataFrame:
    """fdq's wide frame from the provenance-checked long one."""
    return wide(load_bars(fixtures))


def run(experiment_dir: Path, fixtures: Path = HISTORY, seed: int = 42) -> list[tuple[Manifest, Path]]:
    config_path = experiment_dir / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    bars = _bars_for_fdq(fixtures)
    start = pd.Timestamp(cfg["window"]["start"])
    end = pd.Timestamp(cfg["window"]["end"])
    bars = bars.loc[(bars.index >= start) & (bars.index <= end)]
    tier = float(cfg.get("capital_tiers", [50000])[0])
    n_folds = int(cfg.get("n_folds", 4))
    multipliers = tuple(float(m) for m in cfg.get("stress_multipliers", [1, 2, 5]))
    ran_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out: list[tuple[Manifest, Path]] = []
    report = [f"# {cfg['id']} — {cfg['title']}", "", "Verdicts first; the return comes after.", ""]

    for entry in cfg["strategies"]:
        (name, params), = entry.items()
        params = dict(params)
        grid = dict(params.pop("grid", {}))
        symbol = str(params["symbol"])
        friction = load_friction_config(stress_multiplier=1.0)

        matrix, trial_sharpes = in_sample_return_matrix(
            name, params, grid, bars, tier, friction, None, start.date(), end.date(), seed
        )
        pbo = float(probability_backtest_overfitting(matrix)) if matrix.shape[1] >= 2 else 0.0
        wf = walk_forward(name, params, grid, bars, tier, friction, None, n_folds=n_folds, seed=seed)
        oos = wf.oos_returns
        dsr = float(deflated_sharpe(oos, trial_sharpes))

        def at_multiplier(m: float) -> pd.Series:
            f = load_friction_config(stress_multiplier=m)
            return walk_forward(name, params, grid, bars, tier, f, None, n_folds=n_folds, seed=seed).oos_returns

        holdout = float((1.0 + oos).prod() - 1.0)
        gates = [
            GateResult("dsr", dsr >= DSR_MIN, dsr, {"threshold": DSR_MIN, "n_trials": int(wf.n_trials)}),
            psr_gate(oos),
            GateResult("pbo", None, pbo, {"note": "reported, named in the verdict if > 0.5"}),
            bootstrap_gate(oos, seed=seed),
            regime_gate(oos),
            GateResult("holdout_positive", holdout > 0.0, holdout, {"oos_sharpe": float(sharpe(oos))}),
            cost_sweep(at_multiplier, multipliers),
        ]
        status = "pass" if all(g.passed for g in gates if g.passed is not None) else "fail"
        notes = ["pass, fragile: PBO > 0.5"] if status == "pass" and pbo > 0.5 else []
        m = Manifest(
            experiment=cfg["id"], strategy=name, symbol=symbol, params=params, status=status,
            gates=gates, dsr=dsr, psr=float(next(g.value for g in gates if g.name == "psr")),
            pbo=pbo, gates_version=GATES_VERSION, config_sha256=Manifest.sha256_of(config_path),
            fdq_commit=_fdq_commit(), data={"fixtures": str(fixtures.relative_to(fixtures.parents[1]))},
            ran_at=ran_at, notes=notes,
        )
        path = experiment_dir / f"manifest.{name}_{symbol}.json"
        path.write_text(m.to_json())
        out.append((m, path))

        report += [f"## {name} {symbol} {params} — **{status.upper()}**" + (f" ({notes[0]})" if notes else ""), "",
                   "| gate | passed | value |", "|---|---|---|"]
        for g in gates:
            shown = "reported" if g.passed is None else ("yes" if g.passed else "NO")
            val = "" if g.value is None else f"{g.value:.3f}"
            report.append(f"| {g.name} | {shown} | {val} |")
        sweep = next(g for g in gates if g.name == "cost_sweep").detail["sharpe_by_multiplier"]
        report += ["", "Cost sweep, OOS Sharpe by friction multiplier: " + ", ".join(f"×{k}: {v:.2f}" for k, v in sweep.items()), ""]

    (experiment_dir / "report.md").write_text("\n".join(report) + "\n")
    return out
```

Then in `research/src/alpha/cli.py`, add:

```python
@cli.group()
def battery() -> None:
    """The validation battery."""


@battery.command("run")
@click.argument("experiment_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--fixtures", type=click.Path(path_type=Path), default=None, help="bar directory (default: fixtures/history)")
def battery_run(experiment_dir: Path, fixtures: Path | None) -> None:
    """Run the pre-registered experiment in EXPERIMENT_DIR and write its manifests."""
    from alpha.battery.run import run
    from alpha.fixtures import HISTORY

    for m, path in run(experiment_dir, fixtures or HISTORY):
        click.echo(f"{m.status.upper():5} {m.strategy} {m.symbol} dsr={m.dsr:.3f} psr={m.psr:.3f} pbo={m.pbo:.3f} -> {path.name}")
```

And append to `.gitignore`:

```
# Battery outputs: equity curves are regenerated; manifests and reports are committed.
research/experiments/*/results/
```

- [ ] **Step 4: Run the smoke test, confirm it passes**

Run: `uv --directory research run pytest tests/test_battery_smoke.py -v`
Expected: PASS. If `in_sample_return_matrix`'s signature differs from the one quoted (read `fdq/validation/walkforward.py` lines 100–140 at `652474c`), adapt the call and nothing else.

- [ ] **Step 5: Commit**

```bash
git add research/src/alpha/battery/run.py research/src/alpha/cli.py research/tests/test_battery_smoke.py .gitignore
git commit -m "battery: fdq's walk-forward plus the charter's gates, to a manifest per strategy"
```

---

### Task 6: A signal's validation block comes only from a manifest

**Files:**
- Modify: `research/src/alpha/signal.py`
- Modify: `research/src/alpha/cli.py` (`signal emit --validation-from`)
- Test: `research/tests/test_signal.py` (add two tests)

**Interfaces:**
- Produces: `validation_from_manifest(manifest_path: Path, config_path: Path) -> dict` in `alpha.signal`, raising `StaleManifestError` when `is_fresh` is false.
- Consumes: Task 4's `Manifest.from_path`, `is_fresh`, `validation_block`.

- [ ] **Step 1: Write the failing tests** (append to `research/tests/test_signal.py`)

```python
from pathlib import Path

import pytest

from alpha.battery.gates import GateResult
from alpha.battery.manifest import Manifest
from alpha.signal import StaleManifestError, validation_from_manifest


def _manifest(tmp_path: Path, status: str) -> tuple[Path, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("id: T\n")
    m = Manifest(experiment="T", strategy="ma_crossover", symbol="SPY", params={}, status=status,
                 gates=[GateResult("psr", True, 0.8, {})], dsr=0.35, psr=0.8, pbo=0.1,
                 gates_version="2026-09-02", config_sha256=Manifest.sha256_of(cfg), fdq_commit="x",
                 data={}, ran_at="2026-09-02T00:00:00Z")
    p = tmp_path / "manifest.json"
    p.write_text(m.to_json())
    return p, cfg


def test_validation_block_copies_status_and_numbers_from_the_manifest(tmp_path: Path):
    p, cfg = _manifest(tmp_path, "pass")
    v = validation_from_manifest(p, cfg)
    assert v["status"] == "pass" and v["dsr"] == 0.35 and v["manifest"].startswith("sha256:")
    doc = emit("ma_crossover", {"symbol": "SPY", "fast": 1, "slow": 200}, date(2020, 12, 31), load_bars(), 3, validation=v)
    assert check(doc) == [] and doc["validation"]["status"] == "pass"


def test_a_stale_manifest_is_refused(tmp_path: Path):
    p, cfg = _manifest(tmp_path, "pass")
    cfg.write_text("id: T\nchanged: true\n")
    with pytest.raises(StaleManifestError):
        validation_from_manifest(p, cfg)
```

- [ ] **Step 2: Run them, confirm they fail**

Run: `uv --directory research run pytest tests/test_signal.py -v`
Expected: FAIL with `ImportError: cannot import name 'StaleManifestError'`

- [ ] **Step 3: Implement** (append to `research/src/alpha/signal.py`)

```python
from pathlib import Path

from alpha.battery.manifest import Manifest, is_fresh, validation_block


class StaleManifestError(RuntimeError):
    """The manifest was produced from a config that has since changed."""


def validation_from_manifest(manifest_path: Path, config_path: Path) -> dict[str, Any]:
    m = Manifest.from_path(manifest_path)
    if not is_fresh(m, config_path):
        raise StaleManifestError(
            f"{manifest_path.name} was produced from a different {config_path.name}; re-run the battery"
        )
    return validation_block(m, manifest_path)
```

And in `cli.py`'s `signal_emit`, add the option and use it:

```python
@click.option("--validation-from", "validation_from", type=click.Path(path_type=Path, exists=True),
              help="a battery manifest; its config.yaml must sit beside it")
...
    validation = None
    if validation_from is not None:
        from alpha.signal import validation_from_manifest
        validation = validation_from_manifest(validation_from, validation_from.parent / "config.yaml")
    doc = emit(strategy, json.loads(params), as_of, load_bars(fixtures), sequence, validation)
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `uv --directory research run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add research/src/alpha/signal.py research/src/alpha/cli.py research/tests/test_signal.py
git commit -m "signal: the validation block is copied from a fresh manifest or not at all"
```

---

### Task 7: Run EXP-A01 for real, commit the evidence, and let the core judge it

**Files:**
- Generated and committed: `research/experiments/EXP-A01/manifest.ma_crossover_SPY.json`, `manifest.ma_crossover_TLT.json`, `report.md`
- Modify: `Makefile` (`phase1` target)
- Modify: `README.md` (Phase 1 section)

**Interfaces:**
- Consumes everything above.
- Produces: the Phase 1 acceptance in the spec — a manifest-backed signal judged by the core, `ACCEPT` if the battery passed and `REJECT R6` if it failed. Both outcomes complete the phase.

- [ ] **Step 1: Add the Makefile target**

```make
# Phase 1: the pre-registered experiment through the battery, then a signal
# whose validation block was copied from the manifest, judged by the core.
# Either verdict completes the phase; only a manifest that lies would not.
EXP := research/experiments/EXP-A01
phase1: core-build
	$(UV) run alpha battery run $(abspath $(EXP))
	$(UV) run alpha signal emit --strategy ma_crossover \
	  --params '{"symbol":"SPY","fast":1,"slow":200}' \
	  --fixtures $(abspath fixtures/history) \
	  --as-of 2026-05-29 --sequence 2 \
	  --validation-from $(abspath $(EXP))/manifest.ma_crossover_SPY.json \
	  --out $(abspath signals/ma_crossover/2.json)
	$(UV) run alpha replay --fixtures $(abspath fixtures/history) --start 2026-05-01 > signals/.clock.jsonl
	-$(CORE) intake signals/ma_crossover/2.json --bars signals/.clock.jsonl
```

- [ ] **Step 2: Run it**

Run: `make phase1`
Expected: two lines `PASS`/`FAIL ma_crossover SPY|TLT dsr=... psr=... pbo=...`, a signal written, and a core verdict that is `ACCEPT` iff the SPY manifest says `pass`. Runtime under two minutes.

- [ ] **Step 3: Read the report before writing a word about it**

Open `research/experiments/EXP-A01/report.md`. For each strategy, note which gates passed and which did not. If the result looks better than the literature suggests it should (OOS Sharpe above 1 for a 200-day rule on SPY would be), say so in the README and look for the reason before believing it — the charter's "flag a too-good result before the operator does".

- [ ] **Step 4: Write the README section**

Append to `README.md` after *Running it*, replacing the Phase 1 sentence in *What comes next*:

```markdown
## Phase 1: one rule, all the gates

EXP-A01 pre-registered Faber's 10-month moving-average rule on SPY and TLT —
paper, rationale, grid and kill criteria written down and committed before
the run — and ran it through the battery on ten years of real bars:
walk-forward with the grid searched on train only, Deflated Sharpe over the
three trials, PBO, Probabilistic Sharpe, a stationary-block bootstrap of the
out-of-sample Sharpe, five named regimes, the holdout, and a cost sweep at
one, two and five times the friction model.

<verdict table copied from report.md, one row per gate per symbol>

<two or three sentences on what passed, what failed, and why that is the
honest reading — written after reading the report, never before>

`make phase1` reproduces it, then emits a signal whose `validation` block was
copied from the manifest and hands it to the core, which says ACCEPT only if
the battery did.
```

- [ ] **Step 5: Commit the evidence**

```bash
git add research/experiments/EXP-A01 Makefile README.md
git commit -m "EXP-A01: the 10-month MA rule through the battery -- <PASS|FAIL> on SPY, <PASS|FAIL> on TLT"
```

- [ ] **Step 6: Confirm with the owner before Phase 2**

Show the report and the core's verdict. Phase 2 touches OhCamel and needs its own spec in that repository.

---

## Self-review

**Spec coverage.** Phase 1 row: "an experiment config committed before the run" — Task 1; "a report leading with the gates" — Task 5's `report.md` and Task 7; "a signal whose validation block came from the manifest, pass or fail" — Tasks 6 and 7. *Validation gates*: DSR, PSR, PBO, bootstrap, regimes, holdout, cost sweep — Tasks 3 and 5; CPCV is not implemented here, and the spec's Decision 5 says a missing test is added to `fdq` upstream, not here: **gap, deferred to a Phase 1.5 upstream task, and the README section must say CPCV was not run.** *Data policy*: Task 2 uses `load_bars`, which refuses missing provenance. *Reproducibility*: seed 42, `fdq_commit` from the lockfile, `config_sha256`.

**Placeholders.** Task 7 Step 4 has two bracketed slots that are filled *from the report after the run* by design; they are not placeholders for code. No TBDs elsewhere.

**Type consistency.** `GateResult(name, passed, value, detail)` is used identically in Tasks 3–7. `Manifest` fields match between Task 4 and Task 5's constructor call. `validation_block` returns exactly the contract's six keys, which `alpha.contract.check` validates in Task 6's test. `walk_forward` is called with the signature read from `fdq/validation/walkforward.py:70-80` at `652474c`; `in_sample_return_matrix` is called as `fdq/experiment/runner.py` calls it at lines 305-307 of that commit.
