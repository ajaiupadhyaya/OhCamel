"""Completion task 2.4: the trial ledger -- every farm trial ever run, append-only, one row per distinct trial.

A trial is (cell_id, grid_version, params_hash): running the same grid again adds nothing, a changed grid adds
trials. The file is JSON lines under the artifact root and is never rewritten: each write appends whole lines, so
every earlier state of the file is a prefix of the next one.
"""

from __future__ import annotations

import io
import json
import math

import pandas as pd
import pytest

from ohcamel_quant.models import trial_ledger as L

COLS = ["cell_id", "grid_version", "params_hash", "sharpe", "n_obs", "recorded_at", "source"]


def test_an_empty_or_missing_ledger_has_no_trials(tmp_path):
    d = L.distinct(tmp_path / "nope.jsonl")
    assert list(d.columns) == COLS and d.empty


def test_append_records_one_trial_and_distinct_reads_it_back(tmp_path):
    p = tmp_path / "trials.jsonl"
    L.append(p, "sma_trend@fixture", "g1", "sha256:aa", 0.05, 1500)
    d = L.distinct(p)
    assert len(d) == 1
    r = d.iloc[0]
    assert (r.cell_id, r.grid_version, r.params_hash, r.sharpe, r.n_obs) == ("sma_trend@fixture", "g1", "sha256:aa",
                                                                              0.05, 1500)
    assert r.source == "worker" and r.recorded_at
    assert p.read_text().count("\n") == 1 and json.loads(p.read_text())["params_hash"] == "sha256:aa"


def test_the_same_trial_twice_is_one_trial_and_writes_nothing(tmp_path):
    p = tmp_path / "trials.jsonl"
    L.append(p, "c@u", "g1", "sha256:aa", 0.05, 1500)
    before = p.read_bytes()
    L.append(p, "c@u", "g1", "sha256:aa", 0.09, 1600)          # a re-run on more data: the same trial
    assert p.read_bytes() == before
    d = L.distinct(p)
    assert len(d) == 1 and d["sharpe"].iloc[0] == 0.05          # the Sharpe it had when first run


def test_a_changed_grid_or_parameter_or_cell_adds_a_trial_and_never_rewrites(tmp_path):
    p = tmp_path / "trials.jsonl"
    L.append(p, "c@u", "g1", "sha256:aa", 0.05, 1500)
    states = [p.read_bytes()]
    for cell, grid, ph in (("c@u", "g2", "sha256:aa"), ("c@u", "g1", "sha256:bb"), ("d@u", "g1", "sha256:aa")):
        L.append(p, cell, grid, ph, 0.01, 1500)
        now = p.read_bytes()
        assert now.startswith(states[-1]) and len(now) > len(states[-1])   # append-only: the old file is a prefix
        states.append(now)
    assert len(L.distinct(p)) == 4


def test_append_many_writes_only_new_trials_in_one_append(tmp_path):
    p = tmp_path / "trials.jsonl"
    rows = [{"cell_id": "c@u", "grid_version": "g1", "params_hash": f"sha256:{i}", "sharpe": 0.01 * i, "n_obs": 900}
            for i in range(5)]
    assert L.append_many(p, rows) == 5
    assert L.append_many(p, rows + [{**rows[0], "params_hash": "sha256:new"}]) == 1
    assert len(L.distinct(p)) == 6 and p.read_text().count("\n") == 6


def test_a_ledger_seeds_another_from_its_distinct_frame(tmp_path):
    """A burst machine (or a lost file) is seeded from the snapshot an earlier artifact carries; seeding twice adds
    nothing."""
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for i in range(3):
        L.append(a, "c@u", "g1", f"sha256:{i}", 0.02 * i, 1000 + i)
    snap = L.distinct(a)
    assert L.append_many(b, snap) == 3 and L.append_many(b, snap) == 0
    got = L.distinct(b)
    pd.testing.assert_frame_equal(got.drop(columns=["recorded_at", "source"]),
                                  snap.drop(columns=["recorded_at", "source"]))
    assert (got["recorded_at"] == snap["recorded_at"]).all()   # when it was first run, not when it was copied


def test_a_missing_sharpe_is_kept_as_nan(tmp_path):
    p = tmp_path / "trials.jsonl"
    L.append(p, "c@u", "g1", "sha256:aa", float("nan"), 10)
    assert json.loads(p.read_text())["sharpe"] is None and math.isnan(L.distinct(p)["sharpe"].iloc[0])


def test_a_torn_last_line_is_skipped_and_the_next_append_starts_a_new_line(tmp_path):
    p = tmp_path / "trials.jsonl"
    L.append(p, "c@u", "g1", "sha256:aa", 0.05, 1500)
    with p.open("a") as f:
        f.write('{"cell_id": "c@u", "grid_ver')                  # a crash mid-write
    torn = p.read_bytes()
    assert len(L.distinct(p)) == 1
    L.append(p, "c@u", "g1", "sha256:bb", 0.04, 1500)
    assert p.read_bytes().startswith(torn) and len(L.distinct(p)) == 2


def test_a_row_without_its_identity_is_refused(tmp_path):
    with pytest.raises(ValueError, match="params_hash"):
        L.append_many(tmp_path / "t.jsonl", [{"cell_id": "c@u", "grid_version": "g1", "sharpe": 0.1, "n_obs": 1}])


def test_grid_version_is_the_grid_and_only_the_grid():
    g = {"months": [2, 3, 5], "skip": [0, 1]}
    assert L.grid_version(g) == L.grid_version({"skip": [0, 1], "months": [2, 3, 5]})   # key order is not the grid
    assert L.grid_version(g) != L.grid_version({"months": [2, 3, 6], "skip": [0, 1]})
    assert L.grid_version({}) != L.grid_version(g)


def test_a_burst_farm_upload_appends_its_trials_to_the_droplets_ledger(tmp_path):
    """Completion task 3.7's burst door publishes farm artifacts made off the droplet: their trials reach the same
    ledger through the same function; any other kind leaves it alone."""
    from ohcamel_quant.jobs import burst_door as D

    src = tmp_path / "burst.jsonl"
    for i in range(4):
        L.append(src, "c@u", "g9", f"sha256:{i}", 0.01 * i, 1200, source="burst")
    buf = io.BytesIO()
    L.distinct(src).to_parquet(buf)
    root = tmp_path / "artifacts"
    L.append(L.ledger_path(root), "c@u", "g9", "sha256:0", 0.0, 1200)   # the droplet already had one of them
    assert D.record_burst_trials("risk.mc_atlas", {"ledger": buf.getvalue()}, root) == 0
    assert D.record_burst_trials("farm.sweep", {"cells": b""}, root) == 0
    assert D.record_burst_trials("farm.sweep", {"ledger": buf.getvalue()}, root) == 3
    assert len(L.distinct(L.ledger_path(root))) == 4
