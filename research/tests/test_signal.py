"""Tests for signal.py:

- ``emit`` takes the document's strategy slug and fdq's strategy key as
  separate arguments, so two symbols on the same fdq rule emit two distinct
  documents.
- ``validation_from_manifest`` is the only source of a validation block
  whose status is anything but "unvalidated": fresh pass, fresh
  "pass, fragile" (also "pass"), fresh fail, a stale manifest, and a
  manifest that cannot even be loaded -- each case as its own test.
- the CLI's ``signal emit`` has no flag to set the status by hand; every
  route but ``--validation-from`` a fresh, passing manifest yields
  "unvalidated".
- signals are written atomically, and a NaN weight refuses to be written.
- every emitted document validates against ``interface/signal.schema.json``
  (via ``ohcamel_research.contract.check``, which runs the schema through
  ``jsonschema.Draft202012Validator`` and adds the R7 sum check the schema
  cannot express -- see that module for why).

Manifests are built in ``tmp_path`` repos mirroring the paths
``ohcamel_research.manifest`` hashes, following the pattern
``test_manifest.py`` uses for the same purpose (``_build_repo``/``_manifest``
below are a trimmed, local copy, kept independent of that module's own
helpers so this file does not depend on another test file's internals).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import ohcamel_research.cli as cli_module
from ohcamel_research.cli import cli
from ohcamel_research.contract import check, data_hash
from ohcamel_research.manifest import (
    BATTERY_REL,
    UV_LOCK_REL,
    DsrRecord,
    Manifest,
    Window,
    compute_hashes,
    compute_verdict,
)
from ohcamel_research.replay import load_bars
from ohcamel_research.signal import UNVALIDATED, emit, validation_from_manifest, write_signal

# --------------------------------------------------------------------------
# A tmp_path repo a manifest can be judged fresh or stale against, trimmed
# from test_manifest.py's _build_repo/_make_manifest to this file's needs.
# --------------------------------------------------------------------------


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
        "selected_params": {"fast": 1, "slow": 200},
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


# --------------------------------------------------------------------------
# emit: strategy slug and fdq strategy key are separate arguments.
# --------------------------------------------------------------------------


def test_emit_is_schema_valid_and_unvalidated():
    doc = emit(
        "exp_a01_spy",
        "ma_crossover",
        {"symbol": "SPY", "fast": 50, "slow": 200},
        date(2020, 12, 31),
        load_bars(),
        1,
    )
    assert check(doc) == []
    assert doc["validation"]["status"] == "unvalidated"
    assert doc["strategy"] == "exp_a01_spy"
    assert doc["as_of"] == "2020-12-31"
    assert all(t["symbol"] == "SPY" for t in doc["targets"])
    assert sum(abs(t["weight"]) for t in doc["targets"]) <= 1.0


def test_emit_is_point_in_time():
    bars = load_bars()
    as_of = date(2020, 6, 30)
    doc = emit(
        "exp_a01_spy", "ma_crossover", {"symbol": "SPY", "fast": 50, "slow": 200}, as_of, bars, 1
    )
    # The data hash covers exactly the bars on or before as_of: nothing later
    # could have reached the strategy.
    assert doc["data_hash"] == data_hash(bars.loc[bars["date"] <= as_of])
    assert doc["data_hash"] != data_hash(bars)


def test_ma_crossover_is_long_after_a_long_rally_and_flat_after_the_covid_low():
    # Hand-checkable on the fixture: after the 2019 rally the 50 > 200 SMA on
    # SPY, so the strategy is long; on 2020-03-31, after the crash, 50 < 200.
    bars = load_bars()
    params = {"symbol": "SPY", "fast": 50, "slow": 200}
    long = emit("exp_a01_spy", "ma_crossover", params, date(2019, 12, 31), bars, 1)
    flat = emit("exp_a01_spy", "ma_crossover", params, date(2020, 3, 31), bars, 2)
    assert long["targets"] == [{"symbol": "SPY", "weight": 1.0}]
    assert flat["targets"] == []


def test_emit_rejects_an_unknown_fdq_strategy():
    with pytest.raises(KeyError):
        emit(
            "exp_a01_spy", "not_a_real_rule", {"symbol": "SPY"}, date(2020, 12, 31), load_bars(), 1
        )


def test_two_slugs_on_the_same_fdq_strategy_emit_two_distinct_documents():
    """The bug this task fixes: one argument doing both the document's
    strategy field and fdq's lookup key would make SPY and TLT both say
    "strategy": "ma_crossover", colliding on the desk's per-strategy
    sequence bookkeeping (R5) even though they are written to different
    files. Two slugs on the same fdq rule must disagree on `strategy`."""
    bars = load_bars()
    as_of = date(2020, 12, 31)
    spy = emit(
        "exp_a01_spy", "ma_crossover", {"symbol": "SPY", "fast": 1, "slow": 200}, as_of, bars, 1
    )
    tlt = emit(
        "exp_a01_tlt", "ma_crossover", {"symbol": "TLT", "fast": 1, "slow": 200}, as_of, bars, 1
    )
    assert spy["strategy"] == "exp_a01_spy"
    assert tlt["strategy"] == "exp_a01_tlt"
    assert spy["strategy"] != tlt["strategy"]
    assert check(spy) == []
    assert check(tlt) == []


# --------------------------------------------------------------------------
# validation_from_manifest: the only source of a status other than
# "unvalidated" -- fresh pass, fresh "pass, fragile", fresh fail, stale, and
# a manifest that cannot be loaded at all.
# --------------------------------------------------------------------------


def test_validation_from_manifest_is_pass_when_fresh_and_verdict_pass(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": True, "value": 0.9, "detail": {}},), pbo=0.1
    )
    assert m.verdict == "pass"
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "pass"
    assert v["gates_version"] == m.gates_version
    assert v["dsr"] == m.dsr.value
    assert v["psr"] == m.psr
    assert v["pbo"] == m.pbo
    assert v["manifest"] == str(manifest_file)


def test_validation_from_manifest_is_pass_when_fresh_and_verdict_pass_fragile(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": True, "value": 0.9, "detail": {}},), pbo=0.6
    )
    assert m.verdict == "pass, fragile"
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "pass"
    assert v["pbo"] == 0.6


def test_validation_from_manifest_is_fail_when_fresh_and_verdict_fail(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": False, "value": 0.1, "detail": {}},), pbo=0.2
    )
    assert m.verdict == "fail"
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "fail"
    assert v["dsr"] == m.dsr.value
    assert v["manifest"] == str(manifest_file)


def test_validation_from_manifest_is_unvalidated_when_stale(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    # Edit a hashed file after the manifest was written.
    paths["friction"].write_bytes(b"spread_bps_default: 999.0\n")

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "unvalidated"
    assert v["gates_version"] == m.gates_version
    # The numbers a stale manifest reported are still disclosed; only the
    # status is refused.
    assert v["dsr"] == m.dsr.value
    assert v["psr"] == m.psr
    assert v["pbo"] == m.pbo
    assert v["manifest"].startswith(str(manifest_file))
    assert "stale" in v["manifest"]
    assert "friction" in v["manifest"] and "no longer matches" in v["manifest"]


def test_validation_from_manifest_is_unvalidated_when_the_file_is_missing(tmp_path: Path):
    paths = _build_repo(tmp_path)
    missing = paths["root"] / "manifest.does_not_exist.json"

    v = validation_from_manifest(missing, paths["root"])

    assert v == {
        "status": "unvalidated",
        "gates_version": UNVALIDATED["gates_version"],
        "dsr": None,
        "psr": None,
        "pbo": None,
        "manifest": v["manifest"],
    }
    assert str(missing) in v["manifest"]
    assert "cannot be loaded" in v["manifest"]


def test_validation_from_manifest_is_unvalidated_when_the_json_is_malformed(tmp_path: Path):
    paths = _build_repo(tmp_path)
    manifest_file = paths["root"] / "manifest.broken.json"
    manifest_file.write_text("{not valid json at all")

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "unvalidated"
    assert v["dsr"] is None and v["psr"] is None and v["pbo"] is None
    assert "cannot be loaded" in v["manifest"]


def test_validation_from_manifest_is_unvalidated_when_it_fails_its_own_validation(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    data = m.to_dict()
    del data["verdict_line"]  # a required field, missing -> Manifest.from_dict raises
    manifest_file = paths["root"] / "manifest.invalid_shape.json"
    manifest_file.write_text(json.dumps(data))

    v = validation_from_manifest(manifest_file, paths["root"])

    assert v["status"] == "unvalidated"
    assert v["dsr"] is None and v["psr"] is None and v["pbo"] is None
    assert "cannot be loaded" in v["manifest"]


def test_validation_from_manifest_never_raises_and_a_document_built_from_it_still_validates(
    tmp_path: Path,
):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    paths["friction"].write_bytes(b"spread_bps_default: 999.0\n")  # make it stale

    v = validation_from_manifest(manifest_file, paths["root"])
    doc = emit(
        "exp_a01_spy",
        "ma_crossover",
        {"symbol": "SPY", "fast": 1, "slow": 200},
        date(2020, 12, 31),
        load_bars(),
        1,
        v,
    )
    assert check(doc) == []
    assert doc["validation"]["status"] == "unvalidated"


# --------------------------------------------------------------------------
# The CLI: no flag sets the status by hand; --validation-from is the only
# route to anything but "unvalidated".
# --------------------------------------------------------------------------


def _emit_args(strategy: str, fdq_strategy: str, out: Path, **extra: str) -> list[str]:
    args = [
        "signal",
        "emit",
        "--strategy",
        strategy,
        "--fdq-strategy",
        fdq_strategy,
        "--params",
        json.dumps({"symbol": "SPY" if "spy" in strategy else "TLT", "fast": 1, "slow": 200}),
        "--as-of",
        "2020-12-31",
        "--sequence",
        "1",
        "--out",
        str(out),
    ]
    for k, v in extra.items():
        args += [f"--{k.replace('_', '-')}", v]
    return args


def test_the_emit_command_has_no_flag_to_set_status_directly():
    command = cli.commands["signal"].commands["emit"]  # type: ignore[attr-defined]
    names = {p.name for p in command.params}
    assert names == {
        "strategy",
        "fdq_strategy",
        "params",
        "as_of",
        "sequence",
        "fixtures",
        "validation_from",
        "out",
    }
    assert "status" not in names
    assert "validation" not in names


def test_cli_emit_without_validation_from_is_unvalidated(tmp_path: Path):
    out = tmp_path / "exp_a01_spy-1.json"
    result = CliRunner().invoke(cli, _emit_args("exp_a01_spy", "ma_crossover", out))
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text())
    assert doc["validation"]["status"] == "unvalidated"
    assert doc["strategy"] == "exp_a01_spy"
    assert check(doc) == []


def test_cli_emit_with_validation_from_a_stale_manifest_is_unvalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    paths["friction"].write_bytes(b"spread_bps_default: 999.0\n")
    # The CLI's validation_from_manifest checks freshness against its own
    # REPO_ROOT; point it at this test's tmp repo instead of the real one,
    # so staleness here is the friction edit above and nothing else.
    monkeypatch.setattr(cli_module, "REPO_ROOT", paths["root"])

    out = tmp_path / "out" / "exp_a01_spy-1.json"
    result = CliRunner().invoke(
        cli,
        _emit_args("exp_a01_spy", "ma_crossover", out, validation_from=str(manifest_file)),
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text())
    assert doc["validation"]["status"] == "unvalidated"
    assert "friction" in doc["validation"]["manifest"]
    assert check(doc) == []


def test_cli_emit_with_validation_from_a_fresh_passing_manifest_is_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": True, "value": 0.9, "detail": {}},), pbo=0.1
    )
    manifest_file = paths["root"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    monkeypatch.setattr(cli_module, "REPO_ROOT", paths["root"])

    out = tmp_path / "out" / "exp_a01_spy-1.json"
    result = CliRunner().invoke(
        cli,
        _emit_args("exp_a01_spy", "ma_crossover", out, validation_from=str(manifest_file)),
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text())
    assert doc["validation"]["status"] == "pass"
    assert doc["validation"]["manifest"] == str(manifest_file)
    assert check(doc) == []


def test_cli_emits_two_strategies_as_two_distinct_documents(tmp_path: Path):
    spy_out = tmp_path / "exp_a01_spy-20201231.json"
    tlt_out = tmp_path / "exp_a01_tlt-20201231.json"
    r1 = CliRunner().invoke(cli, _emit_args("exp_a01_spy", "ma_crossover", spy_out))
    r2 = CliRunner().invoke(cli, _emit_args("exp_a01_tlt", "ma_crossover", tlt_out))
    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    assert spy_out != tlt_out
    spy_doc = json.loads(spy_out.read_text())
    tlt_doc = json.loads(tlt_out.read_text())
    assert spy_doc["strategy"] == "exp_a01_spy"
    assert tlt_doc["strategy"] == "exp_a01_tlt"
    assert spy_doc != tlt_doc
    assert check(spy_doc) == []
    assert check(tlt_doc) == []


# --------------------------------------------------------------------------
# Atomic writes, and a NaN weight refused.
# --------------------------------------------------------------------------


def _minimal_doc(**target_overrides: Any) -> dict[str, Any]:
    doc = {
        "schema_version": 1,
        "strategy": "exp_a01_spy",
        "params_hash": "sha256:" + "0" * 64,
        "as_of": "2020-12-31",
        "computed_at": "2020-12-31T00:00:00Z",
        "data_hash": "sha256:" + "0" * 64,
        "sequence": 1,
        "validation": dict(UNVALIDATED),
        "targets": [{"symbol": "SPY", "weight": 1.0}],
    }
    doc.update(target_overrides)
    return doc


def test_write_signal_is_atomic_and_leaves_no_partial_json_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out = tmp_path / "exp_a01_spy-1.json"
    seen_before_rename: list[list[str]] = []
    real_replace = os.replace

    def spying_replace(src: str, dst: str) -> None:
        seen_before_rename.append(sorted(p.name for p in tmp_path.glob("*.json")))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spying_replace)
    write_signal(_minimal_doc(), out)

    assert seen_before_rename == [[]]  # no *.json existed the instant before the rename
    assert out.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*")) == []  # the dotted temp name did not survive


def test_write_signal_temp_name_never_ends_in_json(tmp_path: Path):
    out = tmp_path / "exp_a01_spy-1.json"
    write_signal(_minimal_doc(), out)
    assert out.name.endswith(".json")
    # The temp file this function would have used is gone, but its name is
    # documented behaviour: a dotfile, ending in .tmp, never .json.
    tmp_name = f".{out.name}.tmp"
    assert not tmp_name.endswith(".json")
    assert not (tmp_path / tmp_name).exists()


def test_write_signal_refuses_a_nan_weight_and_leaves_nothing_behind(tmp_path: Path):
    doc = _minimal_doc(targets=[{"symbol": "SPY", "weight": float("nan")}])
    out = tmp_path / "exp_a01_spy-1.json"

    with pytest.raises(ValueError):
        write_signal(doc, out)

    assert not out.exists()
    assert list(tmp_path.glob("*.json")) == []
    assert list(tmp_path.iterdir()) == []  # the temp file was cleaned up too
