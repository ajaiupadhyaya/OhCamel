"""Tests for the manifest: round trip, staleness by content hash (one test
per kind of change the spec names, plus the required-key checks a fix round
added), freshness, field validation on load, and the runner's refusal to
start on an uncommitted, untracked, or symlinked battery.

Each staleness test builds a small filesystem tree under ``tmp_path`` that
mirrors the real repository's paths (``research/src/ohcamel_research/battery/``,
an experiment's ``config.yaml``, the friction file and macro fixture it
names, a history fixture and its provenance sidecar, ``research/uv.lock``)
closely enough that ``compute_hashes`` and ``is_stale`` walk it exactly as
they would the real one -- a fresh copy of that tree per test, with exactly
one path in it edited, added, or removed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ohcamel_research.manifest import (
    BATTERY_REL,
    UV_LOCK_REL,
    DsrRecord,
    Manifest,
    Window,
    assert_battery_committed,
    compute_hashes,
    compute_verdict,
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
    that they are excluded -- and provenance sidecars for the macro fixture
    and the history fixture, present from the start for the same reason:
    every other test already exercises that they *are* included.
    """
    battery = root / BATTERY_REL
    _write(battery / "__init__.py", b"")
    _write(battery / "gates.py", b"# gates v1\n")
    _write(battery / "__pycache__" / "gates.cpython-312.pyc", b"stale bytecode")
    _write(battery / "gates.pyc", b"also stale bytecode")

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
    macro_sidecar = Path(str(macro_path) + ".meta.json")
    _write(macro_sidecar, b'{"note": "macro sidecar v1"}')

    fixture_path = root / "fixtures" / "history" / "SPY.parquet"
    _write(fixture_path, b"spy bytes v1")
    fixture_sidecar = Path(str(fixture_path) + ".meta.json")
    _write(fixture_sidecar, b'{"note": "fixture sidecar v1"}')

    uv_lock_path = root / UV_LOCK_REL
    _write(uv_lock_path, b"lockfile v1")

    return {
        "root": root,
        "battery": battery,
        "config": config_path,
        "friction": friction_path,
        "macro": macro_path,
        "macro_sidecar": macro_sidecar,
        "fixture": fixture_path,
        "fixture_sidecar": fixture_sidecar,
        "uv_lock": uv_lock_path,
    }


def _make_manifest(paths: dict[str, Path], **overrides) -> Manifest:
    hashes = overrides.pop("hashes", None)
    if hashes is None:
        hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    gates = overrides.pop(
        "gates",
        (
            {"name": "psr", "passed": True, "value": 0.91, "detail": {"threshold": 0.70}},
            {"name": "holdout_positive", "passed": True, "value": 0.031415926535, "detail": {}},
            {"name": "cost_sweep", "passed": None, "value": None, "detail": {"bps": [0, 5, 15]}},
        ),
    )
    pbo = overrides.pop("pbo", 0.2)
    verdict = overrides.pop("verdict", compute_verdict(gates, pbo))
    fields = {
        "experiment": "EXP-TEST",
        "slug": "exp_test_spy",
        "strategy": "ma_crossover",
        "symbol": "SPY",
        "selected_params": {"fast": 1, "slow": 200},
        "selection_window": Window(start="2016-06-01", end="2022-05-31"),
        "holdout_window": Window(start="2022-06-01", end="2026-06-01"),
        "gates_version": "2026-09-02",
        "gates": gates,
        "dsr": DsrRecord(
            value=0.42,
            unit="fold_trials",
            trial_count=56,
            trial_sharpe_sources={"EXP-002-SPY rerun": 44, "EXP-A01 wf.trial_sharpes": 12},
            returns_series="walk_forward_oos+holdout",
        ),
        "psr": 0.91,
        "pbo": pbo,
        "turnover": 1.25,
        "capacity": 4_200_000.0,
        "verdict": verdict,
        "verdict_line": "Every gate passed on the holdout and the concatenated series.",
        "hashes": hashes,
        "ran_at": "2026-09-18T00:00:00Z",
        "python_version": "3.13.1",
        "notes": ("re-run of EXP-002's SPY grids over the selection window",),
    }
    fields.update(overrides)
    return Manifest(**fields)


# --------------------------------------------------------------------------
# Round trip and manifest_path
# --------------------------------------------------------------------------


def test_round_trip(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    out = tmp_path / "manifest.exp_test_spy.json"
    m.dump(out)
    assert Manifest.load(out) == m


def test_round_trip_with_none_turnover_and_capacity(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths, turnover=None, capacity=None)
    out = tmp_path / "manifest.json"
    m.dump(out)
    assert Manifest.load(out) == m


def test_manifest_path_is_one_per_slug():
    exp_dir = Path("research/experiments/EXP-A01")
    assert manifest_path(exp_dir, "exp_a01_spy") == exp_dir / "manifest.exp_a01_spy.json"
    assert manifest_path(exp_dir, "exp_a01_tlt") == exp_dir / "manifest.exp_a01_tlt.json"


def test_manifest_path_rejects_invalid_slug():
    exp_dir = Path("research/experiments/EXP-A01")
    for bad in ("Exp_A01", "_leading_underscore", "", "has space", "with/slash", "UPPER"):
        with pytest.raises(ValueError):
            manifest_path(exp_dir, bad)


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


def test_stale_on_macro_sidecar_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["macro_sidecar"].write_bytes(b'{"note": "edited"}')
    reasons = is_stale(m, paths["root"])
    assert any("macro.parquet.meta.json" in r and "no longer matches" in r for r in reasons)


def test_stale_on_read_fixture_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["fixture"].write_bytes(b"spy bytes v2")
    reasons = is_stale(m, paths["root"])
    assert any("SPY.parquet" in r and "no longer matches" in r for r in reasons)


def test_stale_on_fixture_sidecar_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["fixture_sidecar"].write_bytes(b'{"note": "edited"}')
    reasons = is_stale(m, paths["root"])
    assert any("SPY.parquet.meta.json" in r and "no longer matches" in r for r in reasons)


def test_stale_on_uv_lock_edited(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["uv_lock"].write_bytes(b"lockfile v2")
    reasons = is_stale(m, paths["root"])
    assert any("uv.lock" in r and "no longer matches" in r for r in reasons)


def test_sidecar_not_hashed_when_absent(tmp_path: Path):
    paths = _build_repo(tmp_path)
    paths["fixture_sidecar"].unlink()
    hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    assert not any(k.endswith("SPY.parquet.meta.json") for k in hashes)


# --------------------------------------------------------------------------
# I1: freshness requires the required keys
# --------------------------------------------------------------------------


def test_compute_hashes_raises_on_empty_read_fixtures(tmp_path: Path):
    paths = _build_repo(tmp_path)
    with pytest.raises(ValueError):
        compute_hashes(paths["root"], paths["config"])


def test_stale_without_uv_lock_key(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = dict(compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]))
    del hashes[UV_LOCK_REL.as_posix()]
    m = _make_manifest(paths, hashes=hashes)
    reasons = is_stale(m, paths["root"])
    assert any("uv.lock" in r and "required key missing" in r for r in reasons)


def test_stale_without_config_key(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = dict(compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]))
    key = next(k for k in hashes if Path(k).name == "config.yaml")
    del hashes[key]
    m = _make_manifest(paths, hashes=hashes)
    reasons = is_stale(m, paths["root"])
    assert any("config.yaml" in r and "required key missing" in r for r in reasons)


def test_stale_without_friction_key(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = dict(compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]))
    key = next(k for k in hashes if "friction" in Path(k).name)
    del hashes[key]
    m = _make_manifest(paths, hashes=hashes)
    reasons = is_stale(m, paths["root"])
    assert any("friction file" in r and "required key missing" in r for r in reasons)


def test_stale_without_macro_key(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = dict(compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]))
    for key in [k for k in hashes if "macro.parquet" in k]:
        del hashes[key]
    m = _make_manifest(paths, hashes=hashes)
    reasons = is_stale(m, paths["root"])
    assert any("macro.parquet" in r and "required key missing" in r for r in reasons)


def test_stale_without_any_history_fixture_key(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = dict(compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]))
    for key in [k for k in hashes if k.startswith("fixtures/history/")]:
        del hashes[key]
    m = _make_manifest(paths, hashes=hashes)
    reasons = is_stale(m, paths["root"])
    assert any("fixtures/history/" in r and "required" in r for r in reasons)


def test_stale_with_only_battery_keys(tmp_path: Path):
    paths = _build_repo(tmp_path)
    hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    battery_only = {k: v for k, v in hashes.items() if k.startswith(BATTERY_REL.as_posix() + "/")}
    assert battery_only  # sanity: the fixture tree does have battery keys
    m = _make_manifest(paths, hashes=battery_only)
    assert is_stale(m, paths["root"]) != []


def test_stale_with_empty_hashes_and_no_battery(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths, hashes={})
    # Remove battery/ entirely: an empty recorded-hashes dict must still
    # read stale, not accidentally "fresh because there's nothing to check."
    import shutil

    shutil.rmtree(paths["battery"])
    reasons = is_stale(m, paths["root"])
    assert reasons != []
    assert len(reasons) >= 5  # all five required categories missing


# --------------------------------------------------------------------------
# M8: the macro path comes from config, not a constant
# --------------------------------------------------------------------------


def test_macro_path_read_from_config_not_a_constant(tmp_path: Path):
    paths = _build_repo(tmp_path)
    alt_macro = paths["root"] / "fixtures" / "alt_macro" / "macro.parquet"
    _write(alt_macro, b"alt macro bytes")
    paths["config"].write_text(
        paths["config"]
        .read_text()
        .replace("macro: fixtures/macro/macro.parquet", "macro: fixtures/alt_macro/macro.parquet")
    )
    hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])
    assert "fixtures/alt_macro/macro.parquet" in hashes
    assert "fixtures/macro/macro.parquet" not in hashes


# --------------------------------------------------------------------------
# M9: read_fixtures resolved against repo root, not cwd
# --------------------------------------------------------------------------


def test_read_fixtures_resolved_against_repo_root_not_cwd(tmp_path: Path, monkeypatch):
    paths = _build_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rel_fixture = paths["fixture"].relative_to(paths["root"])
    hashes = compute_hashes(paths["root"], paths["config"], read_fixtures=[rel_fixture])
    assert rel_fixture.as_posix() in hashes


# --------------------------------------------------------------------------
# M10: determinism regardless of input order
# --------------------------------------------------------------------------


def test_dump_is_byte_identical_regardless_of_input_order(tmp_path: Path):
    paths = _build_repo(tmp_path)
    fixture2 = paths["root"] / "fixtures" / "history" / "TLT.parquet"
    _write(fixture2, b"tlt bytes v1")

    hashes_forward = compute_hashes(
        paths["root"], paths["config"], read_fixtures=[paths["fixture"], fixture2]
    )
    hashes_backward = compute_hashes(
        paths["root"], paths["config"], read_fixtures=[fixture2, paths["fixture"]]
    )
    assert hashes_forward == hashes_backward

    # Also shuffle the hashes dict's own insertion order directly.
    shuffled = dict(reversed(list(hashes_backward.items())))

    m_a = _make_manifest(paths, hashes=hashes_forward)
    m_b = _make_manifest(paths, hashes=shuffled)

    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    m_a.dump(out_a)
    m_b.dump(out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


# --------------------------------------------------------------------------
# M12: is_stale never raises
# --------------------------------------------------------------------------


def test_is_stale_reports_reason_when_recorded_key_is_now_a_directory(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    paths["fixture"].unlink()
    paths["fixture"].mkdir()
    reasons = is_stale(m, paths["root"])  # must not raise
    assert any("SPY.parquet" in r and "directory" in r for r in reasons)


def test_is_stale_never_raises_when_battery_has_a_symlink(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _make_manifest(paths)
    target = tmp_path / "outside_battery"
    target.mkdir()
    (paths["battery"] / "linked").symlink_to(target, target_is_directory=True)
    reasons = is_stale(m, paths["root"])  # must not raise
    assert any("battery" in r for r in reasons)


# --------------------------------------------------------------------------
# M6: symlinks under battery/ are refused
# --------------------------------------------------------------------------


def test_compute_hashes_raises_on_symlinked_directory_under_battery(tmp_path: Path):
    paths = _build_repo(tmp_path)
    target = tmp_path / "outside_battery"
    target.mkdir()
    _write(target / "sneaky.py", b"# not really in battery/\n")
    (paths["battery"] / "linked").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]])


# --------------------------------------------------------------------------
# Serialisation edge cases (M7)
# --------------------------------------------------------------------------


def test_nan_field_raises_on_construction(tmp_path: Path):
    paths = _build_repo(tmp_path)
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), psr=float("nan"))


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


def test_load_rejects_nan_in_json(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    text = out.read_text().replace('"psr": 0.91', '"psr": NaN')
    out.write_text(text)
    with pytest.raises(ValueError):
        Manifest.load(out)


def test_load_rejects_infinity_in_json(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    text = out.read_text().replace('"pbo": 0.2', '"pbo": Infinity')
    out.write_text(text)
    with pytest.raises(ValueError):
        Manifest.load(out)


def test_verdict_must_equal_mechanical_rule(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)  # verdict "pass": pbo 0.2, all gates pass
    raw = json.loads(out.read_text())
    raw["verdict"] = "fail"
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="verdict"):
        Manifest.load(out)


def test_verdict_line_rejects_multiline(tmp_path: Path):
    paths = _build_repo(tmp_path)
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), verdict_line="line one\nline two")


def test_verdict_line_rejects_over_200_chars(tmp_path: Path):
    paths = _build_repo(tmp_path)
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), verdict_line="x" * 201)


def test_verdict_line_rejects_empty(tmp_path: Path):
    paths = _build_repo(tmp_path)
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), verdict_line="")


def test_hash_value_must_match_sha256_pattern(tmp_path: Path):
    paths = _build_repo(tmp_path)
    bad_hashes = dict(_make_manifest(paths).hashes)
    first_key = next(iter(bad_hashes))
    bad_hashes[first_key] = "md5:deadbeef"
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), hashes=bad_hashes)


def test_hash_key_must_be_relative_not_absolute(tmp_path: Path):
    paths = _build_repo(tmp_path)
    bad_hashes = dict(_make_manifest(paths).hashes)
    bad_hashes["/etc/passwd"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), hashes=bad_hashes)


def test_hash_key_must_not_contain_dotdot(tmp_path: Path):
    paths = _build_repo(tmp_path)
    bad_hashes = dict(_make_manifest(paths).hashes)
    bad_hashes["../outside.txt"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        replace(_make_manifest(paths), hashes=bad_hashes)


def test_gate_missing_field_raises(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    del raw["gates"][0]["value"]
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="value"):
        Manifest.load(out)


def test_gate_unknown_field_raises(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    raw["gates"][0]["extra"] = 1
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="extra"):
        Manifest.load(out)


def test_gate_wrong_type_raises(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    raw["gates"][0]["passed"] = "yes"
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        Manifest.load(out)


def test_dsr_trial_sharpe_sources_must_sum_to_trial_count(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    raw["dsr"]["trial_sharpe_sources"] = {"a": 10}  # trial_count is 56
    out.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="trial_sharpe_sources"):
        Manifest.load(out)


def test_python_version_recorded_but_not_checked_by_is_stale(tmp_path: Path):
    paths = _build_repo(tmp_path)
    out = tmp_path / "manifest.json"
    _make_manifest(paths).dump(out)
    raw = json.loads(out.read_text())
    raw["python_version"] = "9.9.9"  # nonsense, unrelated to anything on disk
    out.write_text(json.dumps(raw))
    loaded = Manifest.load(out)
    assert loaded.python_version == "9.9.9"
    assert is_stale(loaded, paths["root"]) == []


def test_python_version_defaults_to_the_running_interpreter(tmp_path: Path):
    import platform

    paths = _build_repo(tmp_path)
    fields = {
        "experiment": "EXP-TEST",
        "slug": "exp_test_spy",
        "strategy": "ma_crossover",
        "symbol": "SPY",
        "selected_params": {},
        "selection_window": Window(start="2016-06-01", end="2022-05-31"),
        "holdout_window": Window(start="2022-06-01", end="2026-06-01"),
        "gates_version": "2026-09-02",
        "gates": (),
        "dsr": DsrRecord(
            value=0.42,
            unit="fold_trials",
            trial_count=0,
            trial_sharpe_sources={},
            returns_series="walk_forward_oos+holdout",
        ),
        "psr": 0.91,
        "pbo": 0.2,
        "turnover": None,
        "capacity": None,
        "verdict": "fail",  # no decided gate: never a pass
        "verdict_line": "ok",
        "hashes": compute_hashes(paths["root"], paths["config"], read_fixtures=[paths["fixture"]]),
        "ran_at": "2026-09-18T00:00:00Z",
    }
    m = Manifest(**fields)
    assert m.python_version == platform.python_version()


# --------------------------------------------------------------------------
# compute_verdict, the mechanical rule
# --------------------------------------------------------------------------


def test_compute_verdict_pass():
    gates = ({"name": "g", "passed": True, "value": 1.0, "detail": {}},)
    assert compute_verdict(gates, pbo=0.5) == "pass"


def test_compute_verdict_pass_fragile_above_threshold():
    gates = ({"name": "g", "passed": True, "value": 1.0, "detail": {}},)
    assert compute_verdict(gates, pbo=0.51) == "pass, fragile"


def test_compute_verdict_fail_on_any_failed_gate():
    gates = (
        {"name": "g1", "passed": True, "value": 1.0, "detail": {}},
        {"name": "g2", "passed": False, "value": 0.0, "detail": {}},
    )
    assert compute_verdict(gates, pbo=0.1) == "fail"


def test_compute_verdict_ignores_undecided_gates():
    gates = (
        {"name": "g1", "passed": True, "value": 1.0, "detail": {}},
        {"name": "g2", "passed": None, "value": None, "detail": {}},
    )
    assert compute_verdict(gates, pbo=0.1) == "pass"


def test_compute_verdict_fails_with_no_decided_gate():
    # An empty gate list, or one where every gate is report-only, has no
    # evidence behind it: never "pass".
    assert compute_verdict((), pbo=0.2) == "fail"
    undecided = ({"name": "cost_sweep", "passed": None, "value": None, "detail": {}},)
    assert compute_verdict(undecided, pbo=0.2) == "fail"


def test_compute_verdict_refuses_a_non_finite_pbo():
    gates = ({"name": "psr", "passed": True, "value": 0.9, "detail": {}},)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            compute_verdict(gates, pbo=bad)


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


def test_assert_battery_committed_raises_on_staged_only_change(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    (repo / BATTERY_REL / "gates.py").write_bytes(b"# gates v2 -- edited\n")
    _git(repo, "add", str(BATTERY_REL / "gates.py"))
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_raises_on_untracked_file(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    _write(repo / BATTERY_REL / "new_gate.py", b"new\n")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_raises_on_ignored_non_pyc_file(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    _write(repo / ".gitignore", b".DS_Store\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore DS_Store")
    _write(repo / BATTERY_REL / ".DS_Store", b"\x00\x01")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_passes_with_ignored_pycache_and_pyc(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    _write(repo / ".gitignore", b"__pycache__/\n*.pyc\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore pycache")
    _write(repo / BATTERY_REL / "__pycache__" / "gates.cpython-313.pyc", b"stale")
    _write(repo / BATTERY_REL / "gates.pyc", b"stale2")
    assert_battery_committed(repo)  # does not raise


def test_assert_battery_committed_raises_when_not_a_git_repository(tmp_path: Path):
    repo = tmp_path / "not_a_repo"
    (repo / BATTERY_REL).mkdir(parents=True)
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_raises_when_battery_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_fails_closed_without_git(tmp_path: Path, monkeypatch):
    repo = tmp_path / "no_git"
    (repo / BATTERY_REL).mkdir(parents=True)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_raises_on_symlinked_directory(tmp_path: Path):
    repo = _init_git_repo_with_battery(tmp_path)
    target = repo / "outside_battery"
    target.mkdir()
    _write(target / "sneaky.py", b"# not really in battery/\n")
    link = repo / BATTERY_REL / "linked"
    link.symlink_to(target, target_is_directory=True)
    _git(repo, "add", str(BATTERY_REL / "linked"))
    _git(repo, "commit", "-q", "-m", "add symlink")
    with pytest.raises(ValueError):
        assert_battery_committed(repo)


def test_assert_battery_committed_sees_untracked_files_under_show_untracked_no(tmp_path: Path):
    # The owner's git config may set status.showUntrackedFiles=no; the check
    # passes --untracked-files=all, which must override it.
    repo = _init_git_repo_with_battery(tmp_path)
    _git(repo, "config", "status.showUntrackedFiles", "no")
    _write(repo / BATTERY_REL / "new_gate.py", b"new\n")
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)


def test_assert_battery_committed_parity_catches_what_git_status_hides(tmp_path: Path):
    # assume-unchanged hides a deleted tracked file from git status; only the
    # tracked-versus-hashed parity check sees that battery/ no longer holds it.
    repo = _init_git_repo_with_battery(tmp_path)
    _write(repo / BATTERY_REL / "extra.py", b"# extra\n")
    _git(repo, "add", str(BATTERY_REL / "extra.py"))
    _git(repo, "commit", "-q", "-m", "extra")
    _git(repo, "update-index", "--assume-unchanged", str(BATTERY_REL / "extra.py"))
    (repo / BATTERY_REL / "extra.py").unlink()
    assert _git(repo, "status", "--porcelain").stdout == ""
    with pytest.raises(RuntimeError):
        assert_battery_committed(repo)
