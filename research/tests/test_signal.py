"""Tests for signal.py:

- ``emit`` takes the document's strategy slug and fdq's strategy key as
  separate arguments, so two symbols on the same fdq rule emit two distinct
  documents.
- ``validation_from_manifest`` is the only source of a validation block
  whose status is anything but "unvalidated": fresh pass, fresh fail,
  fresh "pass, fragile" (unvalidated: the pre-registration's second-window
  rule), a stale manifest, a manifest that cannot even be loaded, and a
  manifest earned by another slug, fdq rule or parameters -- each case as
  its own test, none of the unvalidated ones a pass by any route.
- with a manifest, each weight is the fraction of equity the manifest's
  backtests held (``invested_fraction``), read from its experiment's
  friction file, never 1.0.
- ``emit`` itself only ever reaches ``validation_from_manifest`` through its
  own ``validation_from`` path argument, which it calls internally -- there
  is no parameter, at the Python level, that accepts a ready-made
  validation dict. This is checked at the Python API, not just the CLI,
  because Task 16's research service calls ``emit`` directly.
- the CLI's ``signal emit`` has no flag to set the status by hand either;
  every route but ``--validation-from`` a fresh, passing manifest yields
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
``emit``'s own ``repo_root`` parameter is what lets these tests check
freshness against that tmp repo directly, with no monkeypatching of
anything in ``ohcamel_research.cli``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ohcamel_research import REPO_ROOT
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
from ohcamel_research.signal import (
    FRAGILE_REASON,
    UNVALIDATED,
    emit,
    invested_fraction,
    validation_from_manifest,
    write_signal,
)

# What the test manifests below are the evidence for: a document counts as
# validated by one only when it is emitted under exactly this slug, fdq rule
# and parameters.
SLUG = "exp_test_spy"
FDQ = "ma_crossover"
PARAMS = {"symbol": "SPY", "fast": 1, "slow": 200}
IDENTITY: dict[str, Any] = {"strategy": SLUG, "fdq_strategy": FDQ, "params": PARAMS}

# The test experiment's own friction file, loadable by fdq. Its cash buffer
# is deliberately not friction_v1.yaml's 0.10, so a weight of 0.75 can only
# have been read from this file.
CASH_BUFFER_PCT = 0.25
FRICTION_TEST = (
    f"min_notional: 1.0\ncash_buffer_pct: {CASH_BUFFER_PCT}\nsettlement_days: 1\n"
    "spread_bps_default: 1.0\n"
).encode()
# The same file with one number changed: still loadable, but no longer the
# bytes a manifest recorded, so that manifest reads stale.
FRICTION_TEST_EDITED = FRICTION_TEST.replace(
    b"spread_bps_default: 1.0", b"spread_bps_default: 999.0"
)

EXP_A01 = REPO_ROOT / "research" / "experiments" / "EXP-A01"

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
        "selected_params": dict(PARAMS),
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


def test_emit_has_no_parameter_that_accepts_a_ready_made_validation_dict():
    """A raw ``validation`` dict is refused, not merely discouraged: there is
    no keyword named ``validation`` for it to land on at all."""
    raw = {
        "status": "pass",
        "gates_version": "2026-09-02",
        "dsr": None,
        "psr": None,
        "pbo": None,
        "manifest": None,
    }
    with pytest.raises(TypeError):
        emit(  # type: ignore[call-arg]
            "exp_a01_spy",
            "ma_crossover",
            {"symbol": "SPY", "fast": 1, "slow": 200},
            date(2020, 12, 31),
            load_bars(),
            1,
            validation=raw,
        )


def test_emit_refuses_a_dict_passed_positionally_where_validation_from_goes():
    """The same dict, passed positionally instead of by (nonexistent)
    keyword, lands on ``validation_from`` -- which refuses anything that
    is not a path or ``None``, so this is refused too, just later."""
    raw = {
        "status": "pass",
        "gates_version": "2026-09-02",
        "dsr": None,
        "psr": None,
        "pbo": None,
        "manifest": None,
    }
    with pytest.raises(TypeError):
        emit(
            "exp_a01_spy",
            "ma_crossover",
            {"symbol": "SPY", "fast": 1, "slow": 200},
            date(2020, 12, 31),
            load_bars(),
            1,
            raw,
        )


def test_emit_with_no_validation_from_is_unvalidated():
    doc = emit(
        "exp_a01_spy",
        "ma_crossover",
        {"symbol": "SPY", "fast": 1, "slow": 200},
        date(2020, 12, 31),
        load_bars(),
        1,
    )
    assert doc["validation"] == UNVALIDATED
    assert check(doc) == []


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
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "pass"
    assert v["gates_version"] == m.gates_version
    assert v["dsr"] == m.dsr.value
    assert v["psr"] == m.psr
    assert v["pbo"] == m.pbo
    assert v["manifest"] == str(manifest_file)


def test_validation_from_manifest_is_unvalidated_when_fresh_and_verdict_pass_fragile(
    tmp_path: Path,
):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": True, "value": 0.9, "detail": {}},), pbo=0.6
    )
    assert m.verdict == "pass, fragile"
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    # The pre-registration's own kill criterion: not promoted without a
    # second, independent window. Its numbers are still this strategy's own.
    assert v["status"] == "unvalidated"
    assert v["manifest"] == f"{manifest_file}: {FRAGILE_REASON}"
    assert FRAGILE_REASON == "pass, fragile: not promoted without a second, independent window"
    assert v["pbo"] == 0.6
    assert v["dsr"] == m.dsr.value


def test_validation_from_manifest_is_fail_when_fresh_and_verdict_fail(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": False, "value": 0.1, "detail": {}},), pbo=0.2
    )
    assert m.verdict == "fail"
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "fail"
    assert v["dsr"] == m.dsr.value
    assert v["manifest"] == str(manifest_file)


def test_validation_from_manifest_is_unvalidated_when_stale(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    # Edit a hashed file after the manifest was written.
    paths["friction"].write_bytes(FRICTION_TEST_EDITED)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

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
    missing = paths["exp"] / "manifest.does_not_exist.json"

    v = validation_from_manifest(missing, paths["root"], **IDENTITY)

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
    manifest_file = paths["exp"] / "manifest.broken.json"
    manifest_file.write_text("{not valid json at all")

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "unvalidated"
    assert v["dsr"] is None and v["psr"] is None and v["pbo"] is None
    assert "cannot be loaded" in v["manifest"]


@pytest.mark.parametrize("body", ["42", "null", "[]", "true", '"a string"'])
def test_validation_from_manifest_is_unvalidated_when_the_top_level_is_not_an_object(
    tmp_path: Path, body: str
):
    # A corrupt manifest whose top level is not a JSON object reaches the
    # loader's field checks as a non-mapping; it must read "unvalidated",
    # never raise out of emit.
    paths = _build_repo(tmp_path)
    manifest_file = paths["exp"] / "manifest.scalar.json"
    manifest_file.write_text(body)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "unvalidated"
    assert "cannot be loaded" in v["manifest"]


def test_validation_from_manifest_is_unvalidated_when_it_fails_its_own_validation(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    data = m.to_dict()
    del data["verdict_line"]  # a required field, missing -> Manifest.from_dict raises
    manifest_file = paths["exp"] / "manifest.invalid_shape.json"
    manifest_file.write_text(json.dumps(data))

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "unvalidated"
    assert v["dsr"] is None and v["psr"] is None and v["pbo"] is None
    assert "cannot be loaded" in v["manifest"]


def test_validation_from_manifest_never_raises_and_a_document_built_from_it_still_validates(
    tmp_path: Path,
):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    paths["friction"].write_bytes(FRICTION_TEST_EDITED)  # make it stale

    # emit() calls validation_from_manifest itself; there is no dict to
    # thread through by hand any more (see the "emit" tests below).
    doc = emit(
        SLUG,
        FDQ,
        dict(PARAMS),
        date(2020, 12, 31),
        load_bars(),
        1,
        validation_from=manifest_file,
        repo_root=paths["root"],
    )
    assert check(doc) == []
    assert doc["validation"]["status"] == "unvalidated"


# --------------------------------------------------------------------------
# emit's own validation_from + repo_root: the Python API, which is what
# Task 16's research service actually calls -- not the CLI.
# --------------------------------------------------------------------------


def test_emit_with_a_fresh_passing_manifest_is_pass(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(
        paths, gates=({"name": "psr", "passed": True, "value": 0.9, "detail": {}},), pbo=0.1
    )
    assert m.verdict == "pass"
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    doc = emit(
        SLUG,
        FDQ,
        dict(PARAMS),
        date(2020, 12, 31),
        load_bars(),
        1,
        validation_from=manifest_file,
        repo_root=paths["root"],
    )

    assert doc["validation"]["status"] == "pass"
    assert doc["validation"]["manifest"] == str(manifest_file)
    # SPY's close is above its 200-day SMA on 2020-12-31: on, at the
    # fraction the test experiment's friction file leaves invested.
    assert doc["targets"] == [{"symbol": "SPY", "weight": 1.0 - CASH_BUFFER_PCT}]
    assert check(doc) == []


def test_emit_with_a_stale_manifest_is_unvalidated(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    paths["friction"].write_bytes(FRICTION_TEST_EDITED)  # make it stale

    doc = emit(
        SLUG,
        FDQ,
        dict(PARAMS),
        date(2020, 12, 31),
        load_bars(),
        1,
        validation_from=manifest_file,
        repo_root=paths["root"],
    )

    assert doc["validation"]["status"] == "unvalidated"
    assert "stale" in doc["validation"]["manifest"]
    assert "friction" in doc["validation"]["manifest"]
    assert check(doc) == []


# --------------------------------------------------------------------------
# A validation block counts only for the strategy, rule, parameters and
# verdict it was earned by. Each case below uses a fresh manifest whose
# gates all pass -- the one thing that differs is what makes it not a pass.
# --------------------------------------------------------------------------

_PASSING_GATES = ({"name": "psr", "passed": True, "value": 0.9, "detail": {}},)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"slug": "exp_other_spy"}, "its slug 'exp_other_spy' is not the emitting strategy"),
        ({"strategy": "donchian"}, "its strategy 'donchian' is not the emitting fdq rule"),
        (
            {"selected_params": {"symbol": "SPY", "fast": 1, "slow": 250}},
            "are not the params emitted",
        ),
        ({"pbo": 0.6}, FRAGILE_REASON),
    ],
    ids=["slug", "strategy", "params", "pass-fragile"],
)
def test_a_passing_manifest_is_no_pass_for_anything_it_was_not_earned_by(
    tmp_path: Path, overrides: dict[str, Any], reason: str
):
    paths = _build_repo(tmp_path)
    m = _manifest(paths, gates=_PASSING_GATES, pbo=overrides.pop("pbo", 0.1), **overrides)
    assert m.verdict in ("pass", "pass, fragile")
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    direct = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)
    doc = emit(
        SLUG,
        FDQ,
        dict(PARAMS),
        date(2020, 12, 31),
        load_bars(),
        1,
        validation_from=manifest_file,
        repo_root=paths["root"],
    )

    for v in (direct, doc["validation"]):
        assert v["status"] == "unvalidated"
        assert v["manifest"].startswith(f"{manifest_file}: ")
        assert reason in v["manifest"]
    assert check(doc) == []


def test_another_strategys_evidence_reports_none_of_its_numbers(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths, gates=_PASSING_GATES, pbo=0.1, slug="exp_other_spy")
    manifest_file = paths["exp"] / "manifest.exp_other_spy.json"
    m.dump(manifest_file)

    v = validation_from_manifest(manifest_file, paths["root"], **IDENTITY)

    assert v["status"] == "unvalidated"
    assert v["dsr"] is None and v["psr"] is None and v["pbo"] is None
    assert "not this strategy's evidence" in v["manifest"]


def test_params_are_compared_as_the_document_hashes_them(tmp_path: Path):
    # 200 and 200.0 are equal in Python but not in params_hash's canonical
    # JSON, which is what the document's own params_hash states.
    paths = _build_repo(tmp_path)
    m = _manifest(paths, gates=_PASSING_GATES, pbo=0.1)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    floated = {**PARAMS, "slow": 200.0}
    v = validation_from_manifest(
        manifest_file, paths["root"], strategy=SLUG, fdq_strategy=FDQ, params=floated
    )

    assert v["status"] == "unvalidated"
    assert "are not the params emitted" in v["manifest"]


def test_validation_from_manifest_requires_the_emitters_identity(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths, gates=_PASSING_GATES, pbo=0.1)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)

    with pytest.raises(TypeError):
        validation_from_manifest(manifest_file, paths["root"])  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# The weight is the fraction the evidence held.
# --------------------------------------------------------------------------


def test_the_invested_fraction_is_read_from_the_experiments_friction_file(tmp_path: Path):
    paths = _build_repo(tmp_path)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    _manifest(paths).dump(manifest_file)

    assert invested_fraction(manifest_file, paths["root"]) == 1.0 - CASH_BUFFER_PCT


def test_exp_a01s_invested_fraction_is_fdqs_own_max_deployable_under_friction_v1():
    from fdq.frictions.config import load_friction_config

    engine = load_friction_config(REPO_ROOT / "research" / "config" / "friction_v1.yaml")
    for slug in ("exp_a01_spy", "exp_a01_tlt"):
        fraction = invested_fraction(EXP_A01 / f"manifest.{slug}.json", REPO_ROOT)
        assert fraction == engine.max_deployable(1.0) == 1.0 - engine.cash_buffer_pct
        assert fraction < 1.0


def test_emit_off_is_no_target_and_on_is_the_invested_fraction(tmp_path: Path):
    paths = _build_repo(tmp_path)
    m = _manifest(paths, gates=_PASSING_GATES, pbo=0.1)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    m.dump(manifest_file)
    bars = load_bars()

    on = emit(SLUG, FDQ, dict(PARAMS), date(2020, 12, 31), bars, 1, manifest_file, paths["root"])
    # 2020-03-31: SPY's close is far below its 200-day SMA after the crash.
    off = emit(SLUG, FDQ, dict(PARAMS), date(2020, 3, 31), bars, 2, manifest_file, paths["root"])

    assert on["targets"] == [{"symbol": "SPY", "weight": 1.0 - CASH_BUFFER_PCT}]
    assert off["targets"] == []


def test_emit_refuses_when_the_invested_fraction_cannot_be_read(tmp_path: Path):
    paths = _build_repo(tmp_path)
    manifest_file = paths["exp"] / "manifest.exp_test_spy.json"
    _manifest(paths).dump(manifest_file)
    paths["friction"].write_bytes(b"spread_bps_default: 1.0\n")  # no cash_buffer_pct

    with pytest.raises(ValueError, match="invested fraction"):
        emit(SLUG, FDQ, dict(PARAMS), date(2020, 12, 31), load_bars(), 1, manifest_file, tmp_path)


def test_emit_defaults_repo_root_to_the_real_repository():
    """No ``repo_root`` given: freshness is checked against this actual
    checkout, which is what every real caller (the CLI, eventually the
    research service) wants without having to say so."""
    import inspect

    assert inspect.signature(emit).parameters["repo_root"].default == REPO_ROOT


# --------------------------------------------------------------------------
# The CLI: no flag sets the status by hand; --validation-from is the only
# route to anything but "unvalidated". Freshness itself is exercised at the
# emit() level above, against a tmp repo_root -- the actual CLI command
# always passes the real REPO_ROOT, so a CLI-level freshness test would
# only be able to check staleness/freshness against this real checkout.
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


def _cli_emit_with_exp_a01(out: Path, strategy: str, params: dict[str, Any]) -> dict[str, Any]:
    """The CLI against EXP-A01's real SPY manifest, which is fresh against
    this checkout and whose verdict is "fail"."""
    result = CliRunner().invoke(
        cli,
        [
            "signal",
            "emit",
            "--strategy",
            strategy,
            "--fdq-strategy",
            "ma_crossover",
            "--params",
            json.dumps(params),
            "--as-of",
            "2020-12-31",
            "--sequence",
            "1",
            "--validation-from",
            str(EXP_A01 / "manifest.exp_a01_spy.json"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(out.read_text())


def test_cli_with_the_manifests_own_identity_carries_its_verdict_and_fraction(tmp_path: Path):
    manifest = Manifest.load(EXP_A01 / "manifest.exp_a01_spy.json")
    doc = _cli_emit_with_exp_a01(tmp_path / "a.json", "exp_a01_spy", manifest.selected_params)

    assert doc["validation"]["status"] == manifest.verdict == "fail"
    assert doc["validation"]["manifest"] == str(EXP_A01 / "manifest.exp_a01_spy.json")
    # Above its 150-day SMA on 2020-12-31: on, at the fraction EXP-A01 held.
    fraction = invested_fraction(EXP_A01 / "manifest.exp_a01_spy.json", REPO_ROOT)
    assert doc["targets"] == [{"symbol": "SPY", "weight": fraction}]


@pytest.mark.parametrize(
    ("strategy", "params", "reason"),
    [
        (
            "exp_a01_spy",
            {"symbol": "SPY", "fast": 1, "slow": 200},
            "are not the params emitted",
        ),
        (
            "exp_a01_tlt",
            {"symbol": "SPY", "fast": 1, "slow": 150},
            "is not the emitting strategy 'exp_a01_tlt'",
        ),
    ],
    ids=["params", "slug"],
)
def test_cli_params_obey_the_same_rule(
    tmp_path: Path, strategy: str, params: dict[str, Any], reason: str
):
    doc = _cli_emit_with_exp_a01(tmp_path / "b.json", strategy, params)

    assert doc["validation"]["status"] == "unvalidated"
    assert reason in doc["validation"]["manifest"]
    assert check(doc) == []


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
