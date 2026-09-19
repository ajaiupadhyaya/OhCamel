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
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

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
    _due,
    _lookback_period,
    _lookback_start,
    _to_long_form,
    load_service_config,
    run_once,
    sweep_orphaned_tmp,
)
from ohcamel_research.signal import REGISTRY as signal_registry

FIXTURE_BARS = REPO_ROOT / "fixtures" / "bars"

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
        b"friction: {file: research/config/friction_test.yaml}\n"
        b"macro: fixtures/macro/macro.parquet\n",
    )

    friction_path = root / "research" / "config" / "friction_test.yaml"
    _write(friction_path, b"spread_bps_default: 1.0\n")

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
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    return manifest_file, paths


def _tlt_manifest_file(tmp_path: Path, **overrides: Any) -> tuple[Path, dict[str, Path]]:
    paths = _build_repo(tmp_path)
    overrides.setdefault("slug", "exp_test_tlt")
    overrides.setdefault("symbol", "TLT")
    overrides.setdefault("selected_params", dict(TLT_PARAMS))
    m = _manifest(paths, **overrides)
    manifest_file = paths["root"] / "manifest.exp_test_tlt.json"
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
    spy_file = paths["root"] / "manifest.exp_test_spy.json"
    tlt_file = paths["root"] / "manifest.exp_test_tlt.json"
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
    # Close above its 150-day SMA on 2020-12-31: long.
    assert spy_doc["targets"] == [{"symbol": "SPY", "weight": 1.0}]
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
    paths["friction"].write_bytes(b"spread_bps_default: 999.0\n")

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
