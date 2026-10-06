"""Lane B, B2: the two kinds this lane ships -- ops.selftest and ingest.fred -- called in-process."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.jobs.context import JobCancelled, JobContext
from ohcamel_quant.jobs.handlers import fred, selftest
from ohcamel_quant.jobs.kinds import get_kind


def _ctx(params, threads=2, cancelled=lambda: False):
    seen = []
    return JobContext(job_id="J1", params=params, threads=threads,
                      progress_fn=lambda f, m: seen.append((f, m)), cancelled_fn=cancelled), seen


def test_kinds_are_registered_light_and_private():
    for name, public in (("ops.selftest", True), ("ingest.fred", False)):  # selftest: public since integration
        k = get_kind(name)
        assert (k.mem_class, k.heavy, k.public) == ("S", False, public)
    assert get_kind("ingest.fred").retries == 2 and get_kind("ops.selftest").retries == 0


def test_selftest_writes_its_checks():
    ctx, _ = _ctx({"tag": "t"}, threads=1)
    spec = selftest.run({"tag": "t"}, ctx)
    t = spec.tables["checks"]
    facts = dict(zip(t["check"], t["value"], strict=True))
    assert facts["threads"] == "1" and facts["python"] == sys.version.split()[0]
    assert spec.provenance[0]["source"] == "ohcamel-worker" and spec.data_asof is not None
    assert spec.notes


@pytest.mark.parametrize("params", [{"nope": 1}, {"sleep_s": 61}, {"sleep_s": -1}, {"tag": "x" * 65}])
def test_selftest_rejects_bad_params(params):
    ctx, _ = _ctx(params)
    with pytest.raises(ValueError):
        selftest.run(params, ctx)


def test_selftest_fail_if_exists(tmp_path):
    marker = tmp_path / "m"
    ctx, _ = _ctx({})
    selftest.run({"fail_if_exists": str(marker)}, ctx)  # absent: runs
    marker.touch()
    with pytest.raises(RuntimeError, match="fail_if_exists"):
        selftest.run({"fail_if_exists": str(marker)}, ctx)


def test_selftest_stops_when_cancelled():
    ctx, _ = _ctx({"sleep_s": 30}, cancelled=lambda: True)
    with pytest.raises(JobCancelled):
        selftest.run({"sleep_s": 30}, ctx)


def test_fred_one_series_from_the_offline_fixture():
    ctx, seen = _ctx({"series": "dgs10"})
    spec = fred.run({"series": "dgs10"}, ctx)
    t = spec.tables["series"]
    # fixtures/macro/macro.parquet: DGS10 has 2609 rows, 2016-06-01..2026-06-01, the last 4.47 (percent).
    assert len(t) == 2609 and list(t.columns) == ["date", "value"]
    assert str(t["date"].iloc[-1]) == "2026-06-01" and t["value"].iloc[-1] == 4.47
    assert spec.data_asof == "2026-06-01"
    assert spec.provenance[0]["source"] == "fixture:fred"
    assert any("fixture" in n for n in spec.notes)
    assert seen and seen[0][0] < 1.0


@pytest.mark.parametrize("params", [{}, {"series": "DGS10;rm"}, {"series": "DGS10", "extra": 1},
                                    {"series": "DGS10", "start": "yesterday"}])
def test_fred_rejects_bad_params(params):
    ctx, _ = _ctx(params)
    with pytest.raises(ValueError):
        fred.run(params, ctx)


def test_fred_unknown_series_offline_is_unavailable_not_invented():
    ctx, _ = _ctx({"series": "NOSUCHSERIES"})
    with pytest.raises(DataUnavailable):
        fred.run({"series": "NOSUCHSERIES"}, ctx)


@pytest.mark.skipif(importlib.util.find_spec("duckdb") is not None, reason="duckdb installed (Lane C)")
def test_warehouse_without_duckdb_says_so():
    ctx, _ = _ctx({})
    with pytest.raises(RuntimeError, match="warehouse"):
        _ = ctx.warehouse
