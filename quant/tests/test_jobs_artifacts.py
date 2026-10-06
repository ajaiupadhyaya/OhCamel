"""Lane B, B1: artifacts on disk and the `artifacts` table (contract II.3, ship delta)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ohcamel_quant.jobs.artifacts import build_manifest, latest_artifact, publish, write_tables
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import claim_next, enqueue

T0 = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
MANIFEST_KEYS = ["id", "kind", "params", "params_hash", "code_sha", "data_asof", "started_at", "finished_at",
                 "cpu_seconds", "peak_rss_bytes", "engine", "provenance", "notes", "survivorship", "tables"]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _job(conn, params=None):
    enqueue(conn, kind="ingest.fred", params=params or {"series": "DGS10"}, priority=2, mem_class="S",
            heavy=False, submitted_by="schedule:t", now=T0)
    return claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=T0)


def _row(conn, root, art_id, ph, finished_at, kind="ingest.fred", with_manifest=True):
    path = root / kind / art_id
    path.mkdir(parents=True)
    if with_manifest:
        (path / "manifest.json").write_text(json.dumps({"id": art_id}))
    conn.execute("INSERT INTO artifacts (id, kind, params_hash, finished_at, path, data_asof) VALUES (?,?,?,?,?,?)",
                 (art_id, kind, ph, iso(finished_at), str(path), "2026-06-01"))


def test_build_manifest_has_exactly_the_contract_keys(conn):
    job = _job(conn)
    result = {"data_asof": "2026-06-01", "provenance": [{"source": "fixture:fred"}], "notes": ["n"],
              "survivorship": None, "engine": "python", "tables": ["series"]}
    m = build_manifest(job, result, code_sha="abc123", finished_at=iso(T0 + timedelta(seconds=9)),
                       cpu_seconds=2.0, peak_rss_bytes=10)
    assert list(m) == MANIFEST_KEYS
    assert (m["id"], m["kind"], m["params"], m["params_hash"], m["started_at"]) == (
        job.id, "ingest.fred", {"series": "DGS10"}, job.params_hash, job.started_at)
    assert (m["code_sha"], m["cpu_seconds"], m["peak_rss_bytes"], m["tables"]) == ("abc123", 2.0, 10, ["series"])


def test_build_manifest_refuses_an_unknown_engine(conn):
    with pytest.raises(ValueError):
        build_manifest(_job(conn), {"data_asof": None, "provenance": [], "notes": [], "survivorship": None,
                                    "engine": "gpu", "tables": []},
                       code_sha="x", finished_at=iso(T0), cpu_seconds=0.0, peak_rss_bytes=0)


def test_write_tables_writes_parquet_and_validates_names(tmp_path):
    names = write_tables(tmp_path, {"series": pd.DataFrame({"date": ["2026-06-01"], "value": [4.47]})})
    assert names == ["series"]
    assert pd.read_parquet(tmp_path / "series.parquet")["value"].tolist() == [4.47]
    with pytest.raises(ValueError):
        write_tables(tmp_path, {"Bad Name": pd.DataFrame({"a": [1]})})
    with pytest.raises(ValueError):
        write_tables(tmp_path, {"../escape": pd.DataFrame({"a": [1]})})


def test_publish_moves_staging_into_place(tmp_path, conn):
    job = _job(conn)
    root = tmp_path / "artifacts"
    staging = root / ".staging" / job.id
    staging.mkdir(parents=True)
    (staging / "series.parquet").write_bytes(b"x")
    final = publish(staging, root, job, {"id": job.id})
    assert final == root / "ingest.fred" / job.id
    assert not staging.exists() and (final / "series.parquet").exists()
    assert json.loads((final / "manifest.json").read_text()) == {"id": job.id}


def test_latest_is_newest_finished_and_reports_staleness(tmp_path, conn):
    root = tmp_path / "artifacts"
    _row(conn, root, "A1", "h1", T0)
    _row(conn, root, "A2", "h1", T0 + timedelta(hours=1))
    _row(conn, root, "B1", "h2", T0 + timedelta(hours=2))
    max_age = 30 * 3600.0
    t_new = T0 + timedelta(hours=1)
    got = latest_artifact(conn, "ingest.fred", "h1", now=t_new + timedelta(hours=1), max_age_s=max_age)
    assert got == {"manifest": {"id": "A2"}, "stale": False}
    # stale is strict: exactly max_age old is fresh, one second more is stale.
    assert latest_artifact(conn, "ingest.fred", "h1", now=t_new + timedelta(hours=30),
                           max_age_s=max_age)["stale"] is False
    assert latest_artifact(conn, "ingest.fred", "h1", now=t_new + timedelta(hours=30, seconds=1),
                           max_age_s=max_age)["stale"] is True
    # no declared max age: never stale
    assert latest_artifact(conn, "ingest.fred", "h1", now=T0 + timedelta(days=400), max_age_s=None)["stale"] is False
    # without params_hash: newest of the kind
    assert latest_artifact(conn, "ingest.fred", None, now=T0, max_age_s=None)["manifest"]["id"] == "B1"
    assert latest_artifact(conn, "ingest.fred", "nope", now=T0, max_age_s=None) is None
    assert latest_artifact(conn, "risk.mc_atlas", None, now=T0, max_age_s=None) is None


def test_latest_skips_a_row_whose_directory_is_gone(tmp_path, conn):
    root = tmp_path / "artifacts"
    _row(conn, root, "A1", "h1", T0)
    _row(conn, root, "A2", "h1", T0 + timedelta(hours=1), with_manifest=False)
    assert latest_artifact(conn, "ingest.fred", "h1", now=T0, max_age_s=None)["manifest"]["id"] == "A1"
