"""Tests for the manifest: round trip, staleness by content hash (one test
per kind of change the spec names), freshness, and the runner's refusal to
start on an uncommitted battery.

Each staleness test builds a small filesystem tree under ``tmp_path`` that
mirrors the real repository's paths (``research/src/ohcamel_research/battery/``,
an experiment's ``config.yaml``, the friction file it names, the macro
fixture, a history fixture, ``research/uv.lock``) closely enough that
``compute_hashes`` and ``is_stale`` walk it exactly as they would the real
one -- a fresh copy of that tree per test, with exactly one path in it
edited, added or removed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ohcamel_research.manifest import (
    BATTERY_REL,
    MACRO_REL,
    UV_LOCK_REL,
    DsrRecord,
    Manifest,
    Window,
    assert_battery_committed,
    compute_hashes,
    is_stale,
    manifest_path,
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_repo(root: Path) -> dict[str, Path]:
    """A tmp tree mirroring the real repo's hashed paths, plus two kinds of
    file the battery hash must ignore (``__pycache__/`` and a stray
    ``.pyc``), present from the start so every other test already exercises
    that they are excluded."""
    battery = root / BATTERY_REL
    _write(battery / "__init__.py", b"")
    _write(battery / "gates.py", b"# gates v1\n")
    _write(battery / "__pycache__" / "gates.cpython-312.pyc", b"stale bytecode")
    _write(battery / "gates.pyc", b"also stale bytecode")

    exp_dir = root / "research" / "experiments" / "EXP-TEST"
    config_path = exp_dir / "config.yaml"
    _write(config_path, b"id: EXP-TEST\nfriction: {file: research/config/friction_test.yaml}\n")

    friction_path = root / "research" / "config" / "friction_test.yaml"
    _write(friction_path, b"spread_bps_default: 1.0\n")

    macro_path = root / MACRO_REL
    _write(macro_path, b"macro bytes v1")

    fixture_path = root / "fixtures" / "history" / "SPY.parquet"
    _write(fixture_path, b"spy bytes v1")

    uv_lock_path = root / UV_LOCK_REL
    _write(uv_lock_path, b"lockfile v1")

    return {
        "root": root,
        "battery": battery,
        "config": config_path,
        "friction": friction_path,
        "macro": macro_path,
        "fixture": fixture_path,
        "uv_lock": uv_lock_path,
    }


def _make_manifest(paths: dict[str, Path]) -> Manifest:
    hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    return Manifest(
        experiment="EXP-TEST",
        slug="exp_test_spy",
        strategy="ma_crossover",
        symbol="SPY",
        selected_params={"fast": 1, "slow": 200},
        selection_window=Window(start="2016-06-01", end="2022-05-31"),
        holdout_window=Window(start="2022-06-01", end="2026-06-01"),
        gates_version="2026-09-02",
        gates=(
            {"name": "psr", "passed": True, "value": 0.91, "detail": {"threshold": 0.70}},
            {"name": "holdout_positive", "passed": True, "value": 0.031415926535, "detail": {}},
        ),
        dsr=DsrRecord(
            value=0.42,
            unit="fold_trials",
            trial_count=56,
            trial_sharpe_sources=("EXP-002-SPY rerun", "EXP-A01 wf.trial_sharpes"),
            returns_series="walk_forward_oos+holdout",
        ),
        psr=0.91,
        pbo=0.2,
        verdict="pass",
        verdict_line="Every gate passed on the holdout and the concatenated series.",
        hashes=hashes,
        ran_at="2026-09-18T00:00:00Z",
        notes=("re-run of EXP-002's SPY grids over the selection window",),
    )


# --------------------------------------------------------------------------
# Round trip and manifest_path
# --------------------------------------------------------------------------


def test_round_trip(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    out = tmp_path / "manifest.exp_test_spy.json"
    m.dump(out)
    assert Manifest.load(out) == m


def test_manifest_path_is_one_per_slug():
    exp_dir = Path("research/experiments/EXP-A01")
    assert manifest_path(exp_dir, "exp_a01_spy") == exp_dir / "manifest.exp_a01_spy.json"
    assert manifest_path(exp_dir, "exp_a01_tlt") == exp_dir / "manifest.exp_a01_tlt.json"


# --------------------------------------------------------------------------
# Fresh, and each kind of staleness
# --------------------------------------------------------------------------


def test_fresh_when_nothing_changed(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    assert is_stale(m, paths["root"]) == []


def test_pycache_and_pyc_do_not_stale(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    # Add more of exactly what _build_repo already seeded, to be sure this
    # isn't passing by accident of the fixture's own starting state.
    _write(paths["battery"] / "__pycache__" / "extra.cpython-312.pyc", b"more stale bytecode")
    _write(paths["battery"] / "another.pyc", b"another stale bytecode")
    assert is_stale(m, paths["root"]) == []


def test_stale_on_battery_file_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    (paths["battery"] / "gates.py").write_bytes(b"# gates v2 -- edited\n")
    reasons = is_stale(m, paths["root"])
    assert any("battery" in r and "gates.py" in r and "no longer matches" in r for r in reasons)


def test_stale_on_battery_file_added(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    _write(paths["battery"] / "new_gate.py", b"# a new gate\n")
    reasons = is_stale(m, paths["root"])
    assert any("new_gate.py" in r and "added to battery/" in r for r in reasons)


def test_stale_on_battery_file_removed(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    (paths["battery"] / "gates.py").unlink()
    reasons = is_stale(m, paths["root"])
    assert any("gates.py" in r and "missing now" in r for r in reasons)


def test_stale_on_config_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["config"].write_bytes(paths["config"].read_bytes() + b"\nn_folds: 5\n")
    reasons = is_stale(m, paths["root"])
    assert any("config.yaml" in r and "no longer matches" in r for r in reasons)


def test_stale_on_friction_file_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["friction"].write_bytes(b"spread_bps_default: 2.0\n")
    reasons = is_stale(m, paths["root"])
    assert any("friction_test.yaml" in r and "no longer matches" in r for r in reasons)


def test_stale_on_macro_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["macro"].write_bytes(b"macro bytes v2")
    reasons = is_stale(m, paths["root"])
    assert any("macro.parquet" in r and "no longer matches" in r for r in reasons)


def test_stale_on_read_fixture_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["fixture"].write_bytes(b"spy bytes v2")
    reasons = is_stale(m, paths["root"])
    assert any("SPY.parquet" in r and "no longer matches" in r for r in reasons)


def test_stale_on_uv_lock_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["uv_lock"].write_bytes(b"lockfile v2")
    reasons = is_stale(m, paths["root"])
    assert any("uv.lock" in r and "no longer matches" in r for r in reasons)


# --------------------------------------------------------------------------
# Serialisation edge cases
# --------------------------------------------------------------------------


def test_nan_field_raises_on_dump(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = replace(_make_manifest(paths), psr=float("nan"))
    with pytest.raises(ValueError):
        m.dump(tmp_path / "bad.json")


def test_float_round_trips_exactly(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = replace(_make_manifest(paths), pbo=0.1 + 0.2, psr=1.0 / 3.0)
    out = tmp_path / "manifest.json"
    m.dump(out)
    loaded = Manifest.load(out)
    assert loaded.pbo == m.pbo
    assert loaded.psr == m.psr


def test_missing_field_raises_naming_it(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    del raw["verdict"]
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="verdict"):
        Manifest.load(out)


def test_unknown_field_raises_naming_it(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    raw["mystery_field"] = 1
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="mystery_field"):
        Manifest.load(out)


def test_missing_nested_field_raises_naming_it(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    del raw["dsr"]["unit"]
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unit"):
        Manifest.load(out)


# --------------------------------------------------------------------------
# assert_battery_committed
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_git_repo_with_battery(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _write(repo / BATTERY_REL / "gates.py", b"# gates v1\n")
    _git(repo, "add", str(BATTERY_REL / "gates.py"))
    _git(repo, "commit", "-q", "-m", "battery v1")
    return repo


def test_assert_battery_committed_passes_when_clean(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    assert_battery_committed(repo)  # does not raise


def test_assert_battery_committed_raises_on_untracked_file(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    _write(repo / BATTERY_REL / "new_gate.py", b"new\n")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_raises_on_modified_file(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    (repo / BATTERY_REL / "gates.py").write_bytes(b"# gates v2 -- edited\n")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_fails_closed_without_git(tmp_path: Path, monkeypatch):
    repo = tmp_path / "no_git"
    (repo / BATTERY_REL).mkdir(parents=True)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)
