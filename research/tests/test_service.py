"""Tests for the research service (service.py):

- the fetch sits behind ``BarSource``; every test here goes through
  ``FixtureBarSource``, backed by the committed ``fixtures/bars/`` -- no
  network, no credential;
- a day's run writes two distinct valid documents, one per strategy, and
  each weight matches the rule computed by hand on the fixture;
- the write is atomic: no ``.tmp`` survives, a crash between write and
  rename leaves no ``.json``, and the temp file's own name never ends in
  ``.json``;
- a same-day (same ``as_of``) re-run is a no-op;
- a stale manifest emits ``unvalidated``, through the same
  ``validation_from_manifest`` path ``signal.emit`` already uses -- this
  file does not re-implement that check, only exercises it through the
  service;
- the family check (config's ``fdq_strategy`` vs. the manifest's
  ``strategy``) and the symbol check (the manifest's own
  ``selected_params["symbol"]`` vs. the config's ``symbol``) each refuse
  to emit, rather than emit something computed under the wrong rule;
- orphaned ``.*.tmp`` files are swept on start, and nothing else is
  touched;
- ``research/service.yaml`` itself loads and validates;
- the scheduler's pure trigger function, ``_due``, is exercised without a
  real clock or a real sleep.

``service.py`` calls ``signal.emit`` for the weight -- the same fdq
strategy classes (``fdq.strategies.trend.MACrossover``/``Donchian``) the
battery ran, via ``signal.REGISTRY`` -- rather than a second
implementation of "is the close above its SMA", so there is nothing here
to pin against fdq's own rule: ``test_the_service_uses_fdqs_own_registry,
not_a_copy`` below is the whole story on that point, and the actual rule
is exercised by the hand-computed weight checks alongside it.

Manifests are built in ``tmp_path`` repos mirroring the paths
``ohcamel_research.manifest`` hashes, following ``test_signal.py``'s
``_build_repo``/``_manifest`` pattern -- kept as a local, trimmed copy
here too, so this file does not depend on another test file's internals.
"""

from __future__ import annotations

import json
import os
import signal as os_signal
import time as time_module
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ohcamel_research import REPO_ROOT
from ohcamel_research.contract import check
from ohcamel_research.manifest import (
    BATTERY_REL,
    UV_LOCK_REL,
    DsrRecord,
    Manifest,
    Window,
    compute_hashes,
    compute_verdict,
)
from ohcamel_research.service import REGISTRY as service_registry
from ohcamel_research.service import (
    RUN_AT,
    ZONE,
    AlpacaBarSource,
    FixtureBarSource,
    ServiceConfig,
    ServiceError,
    StrategyConfig,
    VendorMismatchError,
    _due,
    _lookback_period,
    _lookback_start,
    _previous_weekday,
    _require_alpaca_provenance,
    _to_long_form,
    fetchable_through,
    load_service_config,
    run_forever,
    run_once,
    sweep_orphaned_tmp,
)
from ohcamel_research.signal import REGISTRY as signal_registry
from ohcamel_research.signal import invested_fraction

FIXTURE_BARS = REPO_ROOT / "fixtures" / "bars"

# The test experiment's own friction file, loadable by fdq, with a cash
# buffer that is not friction_v1.yaml's 0.10: a weight of 0.75 can only have
# been read from it. INVESTED is what every "on" document must carry.
CASH_BUFFER_PCT = 0.25
INVESTED = 1.0 - CASH_BUFFER_PCT
FRICTION_TEST = (
    f"min_notional: 1.0\ncash_buffer_pct: {CASH_BUFFER_PCT}\nsettlement_days: 1\n"
    "spread_bps_default: 1.0\n"
).encode()
# Still loadable, but no longer the bytes a manifest recorded: stale.
FRICTION_TEST_EDITED = FRICTION_TEST.replace(
    b"spread_bps_default: 1.0", b"spread_bps_default: 999.0"
)

# The fixture's own last date (both SPY.parquet and TLT.parquet end here --
# see fixtures/bars/*.parquet.meta.json). Every test below runs "today"
# safely after it, so the fetch's own end bound never trims what the
# fixture actually has.
FIXTURE_LAST_DATE = date(2020, 12, 31)
TODAY = date(2021, 1, 4)

# Hand-computed once, in the exploration for this task, directly against
# fixtures/bars/{SPY,TLT}.parquet: SPY's close on 2020-12-31 sits above its
# 150-day SMA (374.25 vs ~338.27); TLT's sits below its 200-day SMA (157.73
# vs ~163.04). MACrossover's fast=1 SMA is just the close price itself, and
# because its terminal state depends only on the final day's comparison
# (see service.py's LOOKBACK_MARGIN_BARS docstring), these are exactly the
# weights signal.emit must produce through fdq's own MACrossover class.
SPY_PARAMS = {"symbol": "SPY", "fast": 1, "slow": 150}
TLT_PARAMS = {"symbol": "TLT", "fast": 1, "slow": 200}


# ---------------------------------------------------------------------------
# A tmp_path repo a manifest can be judged fresh or stale against -- trimmed
# from test_signal.py's _build_repo/_manifest to this file's needs.
# ---------------------------------------------------------------------------


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_repo(root: Path) -> dict[str, Path]:
    battery = root / BATTERY_REL
    _write(battery / "__init__.py", b"")
    _write(battery / "gates.py", b"# gates v1\n")

    exp_dir = root / "research" / "experiments" / "EXP-TEST"
    config_path = exp_dir / "config.yaml"
    _write(
        config_path,
        b"id: EXP-TEST\n"
        b'friction: {version: "1.0.0", file: research/config/friction_test.yaml}\n'
        b"macro: fixtures/macro/macro.parquet\n",
    )

    friction_path = root / "research" / "config" / "friction_test.yaml"
    _write(friction_path, FRICTION_TEST)

    macro_path = root / "fixtures" / "macro" / "macro.parquet"
    _write(macro_path, b"macro bytes v1")
    _write(Path(str(macro_path) + ".meta.json"), b'{"note": "macro sidecar v1"}')

    fixture_path = root / "fixtures" / "history" / "SPY.parquet"
    _write(fixture_path, b"spy bytes v1")
    _write(Path(str(fixture_path) + ".meta.json"), b'{"note": "fixture sidecar v1"}')

    uv_lock_path = root / UV_LOCK_REL
    _write(uv_lock_path, b"lockfile v1")

    return {
        "root": root,
        "exp": exp_dir,
        "battery": battery,
        "config": config_path,
        "friction": friction_path,
        "macro": macro_path,
        "fixture": fixture_path,
    }


def _manifest(paths: dict[str, Path], **overrides: Any) -> Manifest:
    hashes = overrides.pop("hashes", None)
    if hashes is None:
        hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    gates = overrides.pop(
        "gates",
        ({"name": "psr", "passed": True, "value": 0.91, "detail": {"threshold": 0.70}},),
    )
    pbo = overrides.pop("pbo", 0.2)
    verdict = overrides.pop("verdict", compute_verdict(gates, pbo))
    fields: dict[str, Any] = {
        "experiment": "EXP-TEST",
        "slug": "exp_test_spy",
        "strategy": "ma_crossover",
        "symbol": "SPY",
        "selected_params": dict(SPY_PARAMS),
        "selection_window": Window(start="2016-06-01", end="2022-05-31"),
        "holdout_window": Window(start="2022-06-01", end="2026-06-01"),
        "gates_version": "2026-09-02",
        "gates": gates,
        "dsr": DsrRecord(
            value=0.42,
            unit="fold_trials",
            trial_count=1,
            trial_sharpe_sources={"EXP-TEST": 1},
            returns_series="walk_forward_oos+holdout",
        ),
        "psr": 0.91,
        "pbo": pbo,
        "turnover": 1.25,
        "capacity": 4_200_000.0,
        "verdict": verdict,
        "verdict_line": "test manifest, not a real result",
        "hashes": hashes,
        "ran_at": "2026-09-18T00:00:00Z",
        "notes": (),
    }
    fields.update(overrides)
    return Manifest(**fields)


def _spy_manifest_file(tmp_path: Path, **overrides: Any) -> tuple[Path, dict[str, Path]]:
    paths = _build_repo(tmp_path)
    m = _manifest(paths, **overrides)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    return manifest_file, paths


def _tlt_manifest_file(tmp_path: Path, **overrides: Any) -> tuple[Path, dict[str, Path]]:
    paths = _build_repo(tmp_path)
    overrides.setdefault("slug", "exp_test_tlt")
    overrides.setdefault("symbol", "TLT")
    overrides.setdefault("selected_params", dict(TLT_PARAMS))
    m = _manifest(paths, **overrides)
    manifest_file = paths["exp"] / "manifest.exp_test_tlt.json"
    m.dump(manifest_file)
    return manifest_file, paths


def _two_strategy_config(tmp_path: Path) -> tuple[ServiceConfig, dict[str, Path]]:
    """Both strategies' manifests, fresh, in the *same* tmp repo root (so
    a single ``repo_root`` and a single ``signals_dir`` cover a whole
    day's run, as the real service's single ``research/service.yaml``
    does)."""
    paths = _build_repo(tmp_path)
    spy = _manifest(paths, slug="exp_test_spy", symbol="SPY", selected_params=dict(SPY_PARAMS))
    tlt = _manifest(paths, slug="exp_test_tlt", symbol="TLT", selected_params=dict(TLT_PARAMS))
    spy_file = paths["exp"] / "manifest.exp_test_spy.json"
    tlt_file = paths["exp"] / "manifest.exp_test_tlt.json"
    spy.dump(spy_file)
    tlt.dump(tlt_file)
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="ma_crossover",
                symbol="SPY",
                manifest=str(spy_file.relative_to(paths["root"])),
            ),
            StrategyConfig(
                slug="exp_test_tlt",
                fdq_strategy="ma_crossover",
                symbol="TLT",
                manifest=str(tlt_file.relative_to(paths["root"])),
            ),
        )
    )
    return config, paths


# ---------------------------------------------------------------------------
# A day's run: two distinct valid documents, hand-checked weights.
# ---------------------------------------------------------------------------


def test_a_days_run_writes_two_distinct_valid_documents(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    bar_source = FixtureBarSource(FIXTURE_BARS)

    written = run_once(
        config, bar_source=bar_source, today=TODAY, signals_dir=signals_dir, repo_root=paths["root"]
    )

    assert len(written) == 2
    assert {p.name for p in written} == {
        f"exp_test_spy-{FIXTURE_LAST_DATE.isoformat()}.json",
        f"exp_test_tlt-{FIXTURE_LAST_DATE.isoformat()}.json",
    }
    docs = {p.name: json.loads(p.read_text()) for p in written}
    assert check(list(docs.values())[0]) == []
    assert check(list(docs.values())[1]) == []
    spy_doc = docs[f"exp_test_spy-{FIXTURE_LAST_DATE.isoformat()}.json"]
    tlt_doc = docs[f"exp_test_tlt-{FIXTURE_LAST_DATE.isoformat()}.json"]
    assert spy_doc != tlt_doc
    assert spy_doc["strategy"] == "exp_test_spy"
    assert tlt_doc["strategy"] == "exp_test_tlt"


def test_each_weight_matches_the_rule_computed_by_hand_on_the_fixture(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    bar_source = FixtureBarSource(FIXTURE_BARS)

    run_once(
        config, bar_source=bar_source, today=TODAY, signals_dir=signals_dir, repo_root=paths["root"]
    )

    spy_doc = json.loads(
        (signals_dir / f"exp_test_spy-{FIXTURE_LAST_DATE.isoformat()}.json").read_text()
    )
    tlt_doc = json.loads(
        (signals_dir / f"exp_test_tlt-{FIXTURE_LAST_DATE.isoformat()}.json").read_text()
    )
    # Close above its 150-day SMA on 2020-12-31: long, at the fraction the
    # test experiment's friction file leaves invested -- not 1.0.
    assert spy_doc["targets"] == [{"symbol": "SPY", "weight": INVESTED}]
    # Close below its 200-day SMA on 2020-12-31: flat.
    assert tlt_doc["targets"] == []


def test_the_service_uses_fdqs_own_registry_not_a_copy():
    # service.py imports signal.REGISTRY directly; this is the same dict
    # object, not a second one that could quietly drift from it.
    assert service_registry is signal_registry


def test_alpaca_bar_source_reads_no_credential_at_construction():
    # Settings() -- and so the ALPACA_API_KEY/ALPACA_SECRET_KEY environment
    # read -- happens inside fetch(), not __init__: constructing the
    # production source is safe (and credential-free) anywhere, including
    # a test that never calls fetch.
    AlpacaBarSource()


# ---------------------------------------------------------------------------
# Atomic writes.
# ---------------------------------------------------------------------------


def test_no_tmp_file_survives_a_successful_run(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )
    assert list(signals_dir.glob("*.tmp")) == []
    assert list(signals_dir.glob(".*")) == []


def test_a_crash_between_write_and_rename_leaves_no_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"

    real_replace = os.replace
    seen_tmp_names: list[str] = []

    def crashing_replace(src: str, dst: str) -> None:
        seen_tmp_names.append(Path(src).name)
        raise OSError("simulated crash between write and rename")

    monkeypatch.setattr(os, "replace", crashing_replace)

    written = run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )

    monkeypatch.setattr(os, "replace", real_replace)  # tidy up before any later assertion I/O

    assert written == []  # run_once swallows the per-strategy raise
    assert list(signals_dir.glob("*.json")) == []
    assert list(signals_dir.iterdir()) == []  # write_signal's own except cleaned the tmp up too
    assert len(seen_tmp_names) == 2  # both strategies attempted, both crashed
    assert all(not n.endswith(".json") for n in seen_tmp_names)
    assert all(n.startswith(".") and n.endswith(".tmp") for n in seen_tmp_names)


# ---------------------------------------------------------------------------
# A same-day re-run is a no-op.
# ---------------------------------------------------------------------------


def test_a_same_day_rerun_writes_nothing(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    bar_source = FixtureBarSource(FIXTURE_BARS)

    first = run_once(
        config, bar_source=bar_source, today=TODAY, signals_dir=signals_dir, repo_root=paths["root"]
    )
    assert len(first) == 2
    before = {p: p.read_text() for p in signals_dir.iterdir()}

    second = run_once(
        config,
        bar_source=bar_source,
        today=TODAY + timedelta(days=1),  # even a later "today": as_of is still the fixture's max
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )

    assert second == []
    after = {p: p.read_text() for p in signals_dir.iterdir()}
    assert before == after  # byte-for-byte unchanged


# ---------------------------------------------------------------------------
# A stale manifest emits unvalidated.
# ---------------------------------------------------------------------------


def test_a_stale_manifest_emits_unvalidated(tmp_path: Path):
    manifest_file, paths = _spy_manifest_file(tmp_path)
    # Edit a hashed file after the manifest was written -- exactly
    # test_signal.py's own staleness recipe, exercised here through the
    # service instead of validation_from_manifest directly.
    paths["friction"].write_bytes(FRICTION_TEST_EDITED)

    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="ma_crossover",
                symbol="SPY",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    signals_dir = tmp_path / "signals"

    written = run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )

    assert len(written) == 1
    doc = json.loads(written[0].read_text())
    assert doc["validation"]["status"] == "unvalidated"
    assert "stale" in doc["validation"]["manifest"]
    assert check(doc) == []


# ---------------------------------------------------------------------------
# The family and symbol checks -- refused before anything is computed.
# ---------------------------------------------------------------------------


def test_a_family_mismatch_refuses_to_emit(tmp_path: Path):
    manifest_file, paths = _spy_manifest_file(tmp_path)  # manifest.strategy == "ma_crossover"
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="donchian",  # disagrees with the manifest's own "ma_crossover"
                symbol="SPY",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    events: list[str] = []
    written = run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
        on_event=events.append,
    )
    assert written == []
    assert not (tmp_path / "signals").exists() or list((tmp_path / "signals").iterdir()) == []
    assert any("does not match the manifest's strategy" in e for e in events)


def test_a_symbol_mismatch_refuses_to_emit(tmp_path: Path):
    manifest_file, paths = _spy_manifest_file(tmp_path)  # selected_params symbol == "SPY"
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="ma_crossover",
                symbol="TLT",  # disagrees with the manifest's own selected_params symbol
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    events: list[str] = []
    written = run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
        on_event=events.append,
    )
    assert written == []
    assert any("does not match the config's symbol" in e for e in events)


def test_an_unloadable_manifest_refuses_to_emit_and_does_not_sink_other_strategies(
    tmp_path: Path,
):
    config, paths = _two_strategy_config(tmp_path)
    # Corrupt only the SPY manifest; TLT's must still emit.
    spy_manifest_path = paths["root"] / config.strategies[0].manifest
    spy_manifest_path.write_text("{not valid json at all")

    events: list[str] = []
    written = run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
        on_event=events.append,
    )

    assert len(written) == 1
    assert written[0].name == f"exp_test_tlt-{FIXTURE_LAST_DATE.isoformat()}.json"
    assert any("cannot load manifest" in e for e in events)


# ---------------------------------------------------------------------------
# Vendor provenance: refuse anything fdq did not fetch from Alpaca.
#
# fdq.data.bars.fetch_symbol falls back to yfinance -- silently, so far as
# the returned DataFrame is concerned -- when Alpaca's own call fails or
# comes back empty. _require_alpaca_provenance is what AlpacaBarSource.fetch
# calls after every fetch to catch that; these tests drive it directly
# (against a hand-built "cache" -- a bare directory with just a sidecar,
# never a real parquet or a real fdq call) and then through a fake BarSource
# that raises exactly what AlpacaBarSource would, to prove run_once treats
# it as this one strategy's problem: no file written, and a clear line in
# the log naming the vendor (or the missing sidecar) -- never a credential,
# because nothing here ever touches one.
# ---------------------------------------------------------------------------


def test_require_alpaca_provenance_refuses_a_yfinance_sidecar(tmp_path: Path):
    cache = tmp_path / "raw" / "SPY.parquet"
    sidecar = cache.with_name(cache.name + ".meta.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"source": "yfinance", "data_kind": "historical"}))

    with pytest.raises(VendorMismatchError, match="yfinance"):
        _require_alpaca_provenance(cache, "SPY")


def test_require_alpaca_provenance_refuses_a_missing_sidecar(tmp_path: Path):
    cache = tmp_path / "raw" / "SPY.parquet"  # no sidecar written at all

    with pytest.raises(VendorMismatchError, match="no readable provenance sidecar"):
        _require_alpaca_provenance(cache, "SPY")


def test_require_alpaca_provenance_accepts_an_alpaca_sidecar(tmp_path: Path):
    cache = tmp_path / "raw" / "SPY.parquet"
    sidecar = cache.with_name(cache.name + ".meta.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"source": "alpaca", "data_kind": "historical"}))

    _require_alpaca_provenance(cache, "SPY")  # does not raise


class _FakeAlpacaLikeBarSource:
    """What ``AlpacaBarSource.fetch`` does, without fdq or the network: it
    writes (or withholds) a sidecar of the test's choosing at fdq's own
    cache layout (``<cache_dir>/raw/<symbol>.parquet[.meta.json]``), then
    runs the exact same ``_require_alpaca_provenance`` check
    ``AlpacaBarSource`` does. A refusal here is indistinguishable, from
    ``run_once``'s point of view, from a real Alpaca-call-fell-back-to-
    yfinance refusal -- which is the point."""

    def __init__(self, cache_dir: Path, sidecar_doc: dict[str, Any] | None) -> None:
        self._raw_dir = cache_dir / "raw"
        self._sidecar_doc = sidecar_doc

    def fetch(self, symbol: str, start: date, end: date) -> Any:
        cache_parquet = self._raw_dir / f"{symbol}.parquet"
        if self._sidecar_doc is not None:
            sidecar = cache_parquet.with_name(cache_parquet.name + ".meta.json")
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(self._sidecar_doc))
        _require_alpaca_provenance(cache_parquet, symbol)
        raise AssertionError("unreachable: _require_alpaca_provenance should have refused")


def test_a_yfinance_fallback_refuses_to_emit_and_writes_nothing(tmp_path: Path):
    manifest_file, paths = _spy_manifest_file(tmp_path)
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="ma_crossover",
                symbol="SPY",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    bar_source = _FakeAlpacaLikeBarSource(
        tmp_path / "cache", {"source": "yfinance", "data_kind": "historical"}
    )
    events: list[str] = []

    written = run_once(
        config,
        bar_source=bar_source,
        today=TODAY,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
        on_event=events.append,
    )

    assert written == []
    assert not (tmp_path / "signals").exists() or list((tmp_path / "signals").iterdir()) == []
    assert any("yfinance" in e for e in events)
    assert any("not alpaca" in e for e in events)


def test_a_missing_provenance_sidecar_refuses_to_emit_and_writes_nothing(tmp_path: Path):
    manifest_file, paths = _spy_manifest_file(tmp_path)
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_spy",
                fdq_strategy="ma_crossover",
                symbol="SPY",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    bar_source = _FakeAlpacaLikeBarSource(tmp_path / "cache", None)
    events: list[str] = []

    written = run_once(
        config,
        bar_source=bar_source,
        today=TODAY,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
        on_event=events.append,
    )

    assert written == []
    assert not (tmp_path / "signals").exists() or list((tmp_path / "signals").iterdir()) == []
    assert any("no readable provenance sidecar" in e for e in events)


# ---------------------------------------------------------------------------
# Orphaned temp files, swept on start.
# ---------------------------------------------------------------------------


def test_sweep_orphaned_tmp_removes_only_the_dotted_tmp_convention(tmp_path: Path):
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir()
    orphan = signals_dir / ".exp_test_spy-2020-12-31.json.tmp"
    orphan.write_text("half-written")
    real_signal = signals_dir / "exp_test_spy-2020-12-30.json"
    real_signal.write_text("{}")
    look_alike = signals_dir / "not-dotted.tmp"  # not write_signal's own naming convention
    look_alike.write_text("leave me alone")

    removed = sweep_orphaned_tmp(signals_dir)

    assert removed == [orphan]
    assert not orphan.exists()
    assert real_signal.exists()
    assert look_alike.exists()


def test_sweep_orphaned_tmp_on_a_missing_directory_is_a_noop(tmp_path: Path):
    assert sweep_orphaned_tmp(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# research/service.yaml itself.
# ---------------------------------------------------------------------------


def test_the_committed_service_yaml_loads_and_names_both_exp_a01_strategies():
    config = load_service_config(REPO_ROOT / "research" / "service.yaml", REPO_ROOT)
    by_slug = {s.slug: s for s in config.strategies}
    assert set(by_slug) == {"exp_a01_spy", "exp_a01_tlt"}
    assert by_slug["exp_a01_spy"].fdq_strategy == "ma_crossover"
    assert by_slug["exp_a01_spy"].symbol == "SPY"
    assert by_slug["exp_a01_tlt"].symbol == "TLT"


def _write_yaml(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_service_config_refuses_an_unknown_top_level_key(tmp_path: Path):
    p = _write_yaml(tmp_path / "service.yaml", "strategies: []\nextra: 1\n")
    with pytest.raises(ServiceError, match="unknown key 'extra'"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_an_empty_strategies_list(tmp_path: Path):
    p = _write_yaml(tmp_path / "service.yaml", "strategies: []\n")
    with pytest.raises(ServiceError, match="non-empty list"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_an_unknown_fdq_strategy(tmp_path: Path):
    p = _write_yaml(
        tmp_path / "service.yaml",
        "strategies:\n  - {slug: exp_x_spy, fdq_strategy: not_a_rule, symbol: SPY, "
        "manifest: manifest.exp_x_spy.json}\n",
    )
    (tmp_path / "manifest.exp_x_spy.json").write_text("{}")
    with pytest.raises(ServiceError, match="not one of"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_donchian_until_its_window_is_anchored(tmp_path: Path):
    # donchian IS a real signal.REGISTRY key (unlike "not_a_rule" above), so
    # this exercises the second, service-specific refusal: Donchian's state
    # can depend on history a truncated lookback would compute wrongly.
    p = _write_yaml(
        tmp_path / "service.yaml",
        "strategies:\n  - {slug: exp_x_spy, fdq_strategy: donchian, symbol: SPY, "
        "manifest: manifest.exp_x_spy.json}\n",
    )
    (tmp_path / "manifest.exp_x_spy.json").write_text("{}")
    with pytest.raises(ServiceError, match="not yet supported"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_duplicate_slugs(tmp_path: Path):
    (tmp_path / "manifest.a.json").write_text("{}")
    p = _write_yaml(
        tmp_path / "service.yaml",
        "strategies:\n"
        "  - {slug: exp_x_spy, fdq_strategy: ma_crossover, symbol: SPY,"
        " manifest: manifest.a.json}\n"
        "  - {slug: exp_x_spy, fdq_strategy: ma_crossover, symbol: TLT,"
        " manifest: manifest.a.json}\n",
    )
    with pytest.raises(ServiceError, match="unique"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_a_missing_manifest_file(tmp_path: Path):
    p = _write_yaml(
        tmp_path / "service.yaml",
        "strategies:\n  - {slug: exp_x_spy, fdq_strategy: ma_crossover, symbol: SPY, "
        "manifest: does/not/exist.json}\n",
    )
    with pytest.raises(ServiceError, match="does not exist"):
        load_service_config(p, tmp_path)


def test_service_config_refuses_a_manifest_path_outside_the_repository(tmp_path: Path):
    p = _write_yaml(
        tmp_path / "service.yaml",
        "strategies:\n  - {slug: exp_x_spy, fdq_strategy: ma_crossover, symbol: SPY, "
        "manifest: '../outside.json'}\n",
    )
    with pytest.raises(ServiceError, match="inside the repository"):
        load_service_config(p, tmp_path)


# ---------------------------------------------------------------------------
# The lookback window.
# ---------------------------------------------------------------------------


def test_lookback_period_is_the_largest_non_symbol_number():
    assert _lookback_period({"symbol": "SPY", "fast": 1, "slow": 150}) == 150
    assert _lookback_period({"symbol": "TLT", "window": 20}) == 20


def test_lookback_period_refuses_params_with_no_number():
    with pytest.raises(ServiceError, match="no numeric period"):
        _lookback_period({"symbol": "SPY"})


def test_lookback_start_covers_the_period_plus_margin():
    today = date(2026, 1, 1)
    start = _lookback_start(today, period=200, margin_bars=30)
    # At minimum, 230 trading days' worth of calendar days back.
    assert (today - start).days >= 230
    assert start < today


# ---------------------------------------------------------------------------
# _to_long_form: fdq's per-symbol frame reshaped to the battery's long form.
# ---------------------------------------------------------------------------


def test_to_long_form_reshapes_fdqs_fetch_symbol_shape():
    import pandas as pd

    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="timestamp")
    raw = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [100.0, 200.0],
            "close_adj": [1.2, 2.2],
        },
        index=idx,
    )

    out = _to_long_form(raw, "SPY")

    assert list(out.columns) == ["date", "symbol", "open", "high", "low", "close", "volume"]
    assert list(out["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert (out["symbol"] == "SPY").all()
    assert out["close"].tolist() == [1.2, 2.2]


# ---------------------------------------------------------------------------
# The scheduler's pure trigger, _due.
# ---------------------------------------------------------------------------


def test_due_is_false_before_the_scheduled_time():
    now = datetime.combine(date(2026, 1, 5), time(19, 14), tzinfo=ZONE)
    assert _due(now, last_run=None) is False


def test_due_is_true_at_or_after_the_scheduled_time_when_not_yet_run_today():
    at = datetime.combine(date(2026, 1, 5), RUN_AT, tzinfo=ZONE)
    after = datetime.combine(date(2026, 1, 5), time(23, 0), tzinfo=ZONE)
    assert _due(at, last_run=None) is True
    assert _due(after, last_run=date(2026, 1, 4)) is True


def test_due_is_false_once_already_run_today_no_matter_how_late():
    now = datetime.combine(date(2026, 1, 5), time(23, 59), tzinfo=ZONE)
    assert _due(now, last_run=date(2026, 1, 5)) is False


def test_due_is_true_again_the_next_day():
    now = datetime.combine(date(2026, 1, 6), RUN_AT, tzinfo=ZONE)
    assert _due(now, last_run=date(2026, 1, 5)) is True


# ---------------------------------------------------------------------------
# The off-by-one guard: the last bar must actually reach emit().
#
# A hand-built frame, engineered so the LAST bar is exactly what flips the
# weight: with it, the 5-day SMA crossover says long; without it (as an
# off-by-one bug dropping the newest bar before emit, or miscomputing
# as_of, would produce), it says flat. This was verified as a real guard,
# not just a tautology, by mutation: temporarily changing _run_strategy to
# call emit with bars_long.iloc[:-1] instead of bars_long turned this
# test's assertion from a pass into a failure (targets [] instead of
# the long XYZ target) -- the mutation was made, observed to
# fail this test, and reverted; it is not part of the committed code.
# ---------------------------------------------------------------------------

# fast=1, slow=5. Hand-computed:
#   including day 9 (close 110) -> SMA5 over days 5-9 = mean(100,100,100,100,110)
#                                    = 102; fast_ma (day 9's own close) = 110 > 102
#                                    -> long.
#   dropping day 9               -> "last" becomes day 8, SMA5 over days 4-8
#                                    (all 100s) = 100; fast_ma (day 8's close) =
#                                    100, not > 100 -> flat.
_OFF_BY_ONE_CLOSES = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 110.0]
_OFF_BY_ONE_PARAMS = {"symbol": "XYZ", "fast": 1, "slow": 5}


def _off_by_one_bars() -> pd.DataFrame:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(_OFF_BY_ONE_CLOSES))]
    return pd.DataFrame(
        [
            {
                "date": d,
                "symbol": "XYZ",
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000.0,
            }
            for d, c in zip(dates, _OFF_BY_ONE_CLOSES, strict=True)
        ]
    )


class _FixedFrameBarSource:
    """Returns the same hand-built frame regardless of the range asked -- a
    fake, not a slice of fixtures/bars/, because this scenario needs
    numbers engineered to cross exactly on the last bar."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self._bars


def test_the_last_bar_participates_in_the_weight(tmp_path: Path):
    paths = _build_repo(tmp_path)
    manifest = _manifest(
        paths, slug="exp_test_xyz", symbol="XYZ", selected_params=dict(_OFF_BY_ONE_PARAMS)
    )
    manifest_file = paths["exp"] / "manifest.exp_test_xyz.json"
    manifest.dump(manifest_file)
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_xyz",
                fdq_strategy="ma_crossover",
                symbol="XYZ",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    bars = _off_by_one_bars()
    last_date = bars["date"].max()

    written = run_once(
        config,
        bar_source=_FixedFrameBarSource(bars),
        today=last_date,
        signals_dir=tmp_path / "signals",
        repo_root=paths["root"],
    )

    assert len(written) == 1
    doc = json.loads(written[0].read_text())
    assert doc["as_of"] == last_date.isoformat()
    assert doc["targets"] == [{"symbol": "XYZ", "weight": INVESTED}]


# ---------------------------------------------------------------------------
# The weight is the fraction the evidence held: on, 1 - cash_buffer_pct of
# the manifest's own experiment; off, no target at all.
# ---------------------------------------------------------------------------


def test_the_weight_is_the_fraction_the_evidence_held_when_on_and_zero_when_off(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"

    run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=TODAY,
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )

    fraction = invested_fraction(paths["root"] / config.strategies[0].manifest, paths["root"])
    assert fraction == INVESTED  # read from the test experiment's friction file
    spy = json.loads((signals_dir / f"exp_test_spy-{FIXTURE_LAST_DATE}.json").read_text())
    tlt = json.loads((signals_dir / f"exp_test_tlt-{FIXTURE_LAST_DATE}.json").read_text())
    assert spy["targets"] == [{"symbol": "SPY", "weight": fraction}]  # on
    assert tlt["targets"] == []  # off: weight 0, so no target


def test_the_committed_service_config_emits_the_fraction_exp_a01_held():
    from fdq.frictions.config import load_friction_config

    engine = load_friction_config(REPO_ROOT / "research" / "config" / "friction_v1.yaml")
    config = load_service_config(REPO_ROOT / "research" / "service.yaml", REPO_ROOT)
    for s in config.strategies:
        fraction = invested_fraction(REPO_ROOT / s.manifest, REPO_ROOT)
        assert fraction == engine.max_deployable(1.0) == 1.0 - engine.cash_buffer_pct


# ---------------------------------------------------------------------------
# AlpacaBarSource against fdq's real fetch_symbol, with fdq's two vendor
# calls replaced by fakes -- no network and no credential: the keys below
# are placeholders that only make fdq take its Alpaca branch.
# ---------------------------------------------------------------------------


def _fdq_frame(days: list[date], close: float) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="timestamp")
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
            "close_adj": close,
        },
        index=idx,
    )


@pytest.fixture
def fake_vendors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """fdq's ``_fetch_alpaca`` answers from ``alpaca`` (a queue: a frame, or
    an exception to raise) and records the end date it was asked for;
    ``_fetch_yfinance`` always answers ``yfinance``."""
    import fdq.data.bars as fdq_bars

    monkeypatch.setenv("ALPACA_API_KEY", "placeholder-not-a-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "placeholder-not-a-key")
    monkeypatch.setenv("FDQ_DATA_DIR", str(tmp_path / "fdq-data"))
    state: dict[str, Any] = {"alpaca": [], "yfinance": None, "alpaca_ends": []}

    def fake_alpaca(symbols: list[str], start: date, end: date, settings: Any) -> dict:
        state["alpaca_ends"].append(end)
        answer = state["alpaca"].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {symbols[0]: answer}

    def fake_yfinance(symbols: list[str], start: date, end: date) -> dict:
        return {symbols[0]: state["yfinance"]}

    monkeypatch.setattr(fdq_bars, "_fetch_alpaca", fake_alpaca)
    monkeypatch.setattr(fdq_bars, "_fetch_yfinance", fake_yfinance)
    return state


def test_rows_from_a_refused_fetch_never_reach_a_later_one(
    tmp_path: Path, fake_vendors: dict[str, Any]
):
    d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
    # First fetch: Alpaca fails, fdq falls back to yfinance -- three rows,
    # one of them a day Alpaca will not have -- and the service refuses it.
    # Second fetch: Alpaca answers two rows. With one shared fdq cache the
    # yfinance row for d3 would be merged in under an "alpaca" sidecar.
    fake_vendors["alpaca"] = [RuntimeError("alpaca unavailable"), _fdq_frame([d1, d2], 100.0)]
    fake_vendors["yfinance"] = _fdq_frame([d1, d2, d3], 999.0)
    source = AlpacaBarSource(clock=lambda: datetime(2024, 2, 1, 12, 0, tzinfo=UTC))

    with pytest.raises(VendorMismatchError, match="yfinance"):
        source.fetch("SPY", date(2024, 1, 1), date(2024, 1, 31))
    out = source.fetch("SPY", date(2024, 1, 1), date(2024, 1, 31))

    assert list(out["date"]) == [d1, d2]
    assert out["close"].tolist() == [100.0, 100.0]
    # Nothing outlives a fetch: each had its own directory, now gone.
    assert list((tmp_path / "fdq-data").iterdir()) == []


@pytest.mark.parametrize(
    ("now", "through"),
    [
        # Summer, 19:15 New York is 23:15 UTC: today's 23:59:59 UTC is ahead.
        (datetime(2026, 7, 1, 19, 15, tzinfo=ZONE), date(2026, 6, 30)),
        # 00:15 UTC: past it, but inside the last 16 minutes.
        (datetime(2026, 7, 1, 20, 15, tzinfo=ZONE), date(2026, 6, 30)),
        (datetime(2026, 7, 1, 20, 16, tzinfo=ZONE), date(2026, 7, 1)),
        # Winter, 19:15 New York is 00:15 UTC.
        (datetime(2026, 1, 5, 19, 15, tzinfo=ZONE), date(2026, 1, 4)),
        (datetime(2026, 1, 5, 19, 16, tzinfo=ZONE), date(2026, 1, 5)),
    ],
)
def test_fetchable_through_keeps_fdqs_end_at_least_16_minutes_behind_now(
    now: datetime, through: date
):
    assert fetchable_through(now) == through
    cutoff = now - timedelta(minutes=16)
    # fdq sends end=D as D 23:59:59.999999, read as UTC.
    assert datetime.combine(through, time.max, tzinfo=UTC) <= cutoff
    assert datetime.combine(through + timedelta(days=1), time.max, tzinfo=UTC) > cutoff


def test_the_alpaca_fetch_end_is_lowered_to_the_pin_and_never_raised(
    fake_vendors: dict[str, Any],
):
    d = date(2026, 6, 30)
    fake_vendors["alpaca"] = [_fdq_frame([d], 100.0), _fdq_frame([d], 100.0)]
    summer_evening = datetime(2026, 7, 1, 19, 15, tzinfo=ZONE)
    source = AlpacaBarSource(clock=lambda: summer_evening)

    source.fetch("SPY", date(2026, 6, 1), date(2026, 7, 1))
    source.fetch("SPY", date(2026, 6, 1), date(2026, 6, 15))

    assert fake_vendors["alpaca_ends"] == [date(2026, 6, 30), date(2026, 6, 15)]


# ---------------------------------------------------------------------------
# The loop itself: a clock that returns the start instant and then one
# instant per poll, and a sleep that stops the loop after the last poll.
# ---------------------------------------------------------------------------


class _Stop(Exception):
    pass


class _CountingSource:
    """Wraps a source, records every fetch, and can hide one day's bars for
    the first ``hide_for`` fetches -- a bar that arrives late."""

    def __init__(self, inner: Any, *, hide: date | None = None, hide_for: int = 0) -> None:
        self._inner = inner
        self._hide = hide
        self._hide_for = hide_for
        self.calls: list[tuple[str, date]] = []

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((symbol, end))
        bars = self._inner.fetch(symbol, start, end)
        if self._hide is not None and len(self.calls) <= self._hide_for:
            bars = bars.loc[bars["date"] != self._hide].reset_index(drop=True)
        return bars


def _run_polls(
    config: ServiceConfig,
    source: Any,
    paths: dict[str, Path],
    signals_dir: Path,
    start: datetime,
    polls: list[datetime],
    **kwargs: Any,
) -> list[str]:
    """``run_forever`` from ``start``, through exactly ``polls``; returns
    what it logged."""
    instants = iter([start, *polls])
    slept: list[float] = []
    events: list[str] = []

    def clock() -> datetime:
        return next(instants)

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == len(polls):
            raise _Stop

    with pytest.raises(_Stop):
        run_forever(
            config,
            bar_source=source,
            signals_dir=signals_dir,
            repo_root=paths["root"],
            clock=clock,
            sleep=sleep,
            on_event=events.append,
            **kwargs,
        )
    return events


def _et(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=ZONE)


def test_a_late_bar_is_retried_every_poll_until_it_lands(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    day = FIXTURE_LAST_DATE  # a Thursday
    # The day's own bar is missing from the first two polls' fetches.
    source = _CountingSource(FixtureBarSource(FIXTURE_BARS), hide=day, hide_for=4)

    events = _run_polls(
        config,
        source,
        paths,
        signals_dir,
        _et(day, 20, 30),
        [_et(day, 20, 30), _et(day, 20, 35), _et(day, 20, 40), _et(day, 20, 45)],
    )

    for slug in ("exp_test_spy", "exp_test_tlt"):
        assert (signals_dir / f"{slug}-{day}.json").exists()
        late = [e for e in events if e.startswith(f"research  {slug}: the newest bar is")]
        assert late == [
            f"research  {slug}: the newest bar is {day - timedelta(days=1)}, not {day} -- a late "
            f"bar, or no session on {day}; no signal for {day} yet"
        ]  # said once, though it was late on two polls
    # Two strategies on each of three polls; the fourth poll fetches nothing,
    # because the day is complete once every strategy's newest bar is today's.
    assert len(source.calls) == 6


def test_weekends_are_skipped_and_said_once(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    saturday = date(2021, 1, 2)
    source = _CountingSource(FixtureBarSource(FIXTURE_BARS))

    events = _run_polls(
        config,
        source,
        paths,
        tmp_path / "signals",
        _et(saturday, 20, 0),  # after 19:15: no back-fill either
        [_et(saturday, 20, 0), _et(saturday, 20, 5), _et(saturday, 23, 55)],
    )

    assert source.calls == []
    assert [e for e in events if "Saturday" in e] == [
        f"research  {saturday} is a Saturday: no session"
    ]


def test_a_weekday_with_no_new_bar_is_said_once_and_writes_nothing_for_that_day(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    holiday = date(2021, 1, 1)  # a Friday with no session: the fixture has no bar for it
    source = _CountingSource(FixtureBarSource(FIXTURE_BARS))

    events = _run_polls(
        config,
        source,
        paths,
        signals_dir,
        _et(holiday, 20, 30),
        [_et(holiday, 20, 30), _et(holiday, 20, 35), _et(holiday, 20, 40)],
    )

    assert not list(signals_dir.glob(f"*-{holiday}.json"))
    assert len([e for e in events if "the newest bar is" in e]) == 2  # once per strategy
    assert len(source.calls) == 6  # every poll retried, in case the bar was only late


def test_a_start_before_the_run_backfills_the_previous_weekday_once(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    friday = date(2021, 1, 1)
    source = _CountingSource(FixtureBarSource(FIXTURE_BARS))

    events = _run_polls(
        config,
        source,
        paths,
        signals_dir,
        _et(friday, 10, 0),
        [_et(friday, 10, 0), _et(friday, 10, 5)],
    )

    session = _previous_weekday(friday)
    assert session == FIXTURE_LAST_DATE
    assert (signals_dir / f"exp_test_spy-{session}.json").exists()
    assert (signals_dir / f"exp_test_tlt-{session}.json").exists()
    assert len(source.calls) == 2  # once, at start; the morning polls are not due
    assert any(f"no signal for {session}" in e and "running that session once" in e for e in events)


def test_no_backfill_when_the_previous_weekdays_signals_exist(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    signals_dir = tmp_path / "signals"
    run_once(
        config,
        bar_source=FixtureBarSource(FIXTURE_BARS),
        today=FIXTURE_LAST_DATE,
        signals_dir=signals_dir,
        repo_root=paths["root"],
    )
    friday = date(2021, 1, 1)
    source = _CountingSource(FixtureBarSource(FIXTURE_BARS))

    _run_polls(config, source, paths, signals_dir, _et(friday, 9, 0), [_et(friday, 9, 0)])

    assert source.calls == []


def test_previous_weekday_skips_the_weekend():
    assert _previous_weekday(date(2021, 1, 4)) == date(2021, 1, 1)  # Monday -> Friday
    assert _previous_weekday(date(2021, 1, 2)) == date(2021, 1, 1)  # Saturday -> Friday
    assert _previous_weekday(date(2021, 1, 3)) == date(2021, 1, 1)  # Sunday -> Friday
    assert _previous_weekday(date(2021, 1, 5)) == date(2021, 1, 4)  # Tuesday -> Monday


def test_due_is_false_on_a_weekend_evening():
    assert _due(_et(date(2026, 1, 3), 19, 30), last_run=None) is False  # Saturday
    assert _due(_et(date(2026, 1, 4), 19, 30), last_run=None) is False  # Sunday


def test_a_summer_evening_holds_until_the_fetch_end_is_16_minutes_old(tmp_path: Path):
    day = date(2026, 7, 1)  # a Wednesday, New York on EDT
    paths = _build_repo(tmp_path)
    manifest = _manifest(
        paths, slug="exp_test_xyz", symbol="XYZ", selected_params=dict(_OFF_BY_ONE_PARAMS)
    )
    manifest_file = paths["exp"] / "manifest.exp_test_xyz.json"
    manifest.dump(manifest_file)
    config = ServiceConfig(
        strategies=(
            StrategyConfig(
                slug="exp_test_xyz",
                fdq_strategy="ma_crossover",
                symbol="XYZ",
                manifest=str(manifest_file.relative_to(paths["root"])),
            ),
        )
    )
    bars = _off_by_one_bars()
    bars["date"] = [day - timedelta(days=len(bars) - 1 - i) for i in range(len(bars))]
    source = _CountingSource(_FixedFrameBarSource(bars))
    signals_dir = tmp_path / "signals"

    events = _run_polls(
        config,
        source,
        paths,
        signals_dir,
        _et(day, 19, 15),
        [_et(day, 19, 15), _et(day, 19, 20), _et(day, 20, 15), _et(day, 20, 16)],
    )

    assert len(source.calls) == 1  # only the 20:16 poll fetched
    assert (signals_dir / f"exp_test_xyz-{day}.json").exists()
    holds = [e for e in events if "holding until" in e]
    assert holds == [
        f"research  {day}: holding until 20:16 EDT -- fdq asks Alpaca for bars through "
        "23:59:59 UTC of the end date, and a free plan may not query SIP data from the last "
        "15 minutes"
    ]


def test_a_hung_fetch_is_abandoned_by_the_watchdog_and_the_loop_moves_on(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)

    class _Hangs:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            self.calls += 1
            time_module.sleep(30)
            raise AssertionError("unreachable: the watchdog fires first")

    source = _Hangs()
    handler_before = os_signal.getsignal(os_signal.SIGALRM)
    day = FIXTURE_LAST_DATE
    began = time_module.monotonic()

    events = _run_polls(
        config,
        source,
        paths,
        tmp_path / "signals",
        _et(day, 20, 30),
        [_et(day, 20, 30), _et(day, 20, 35)],
        watchdog_seconds=0.2,
    )

    assert time_module.monotonic() - began < 10
    watchdog = [e for e in events if "watchdog" in e]
    assert len(watchdog) == 2  # both polls abandoned, and the loop went on to the next
    assert watchdog[0] == (
        f"research  {day}: the run outlived its 0.2 s watchdog and was abandoned; "
        "the next poll retries"
    )
    assert source.calls == 2  # one hung fetch per poll; the other strategy never started
    assert os_signal.getsignal(os_signal.SIGALRM) is handler_before
    assert os_signal.getitimer(os_signal.ITIMER_REAL) == (0.0, 0.0)
    assert not (tmp_path / "signals").exists() or list((tmp_path / "signals").iterdir()) == []


def test_a_run_that_finishes_in_time_leaves_no_alarm_behind(tmp_path: Path):
    config, paths = _two_strategy_config(tmp_path)
    handler_before = os_signal.getsignal(os_signal.SIGALRM)
    day = FIXTURE_LAST_DATE

    _run_polls(
        config,
        FixtureBarSource(FIXTURE_BARS),
        paths,
        tmp_path / "signals",
        _et(day, 20, 30),
        [_et(day, 20, 30)],
        watchdog_seconds=60,
    )

    assert os_signal.getsignal(os_signal.SIGALRM) is handler_before
    assert os_signal.getitimer(os_signal.ITIMER_REAL) == (0.0, 0.0)
