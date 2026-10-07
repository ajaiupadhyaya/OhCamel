"""Lane M, M1: GET /api/artifacts (contract II.3 as amended; Review Focus 4)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from lane_m_harness import publish_artifact

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers.artifacts import get_artifacts_db_path
from ohcamel_quant.jobs.db import utcnow


@pytest.fixture
def api(tmp_path):
    app = create_app()
    db = tmp_path / "jobs.sqlite"
    app.dependency_overrides[get_artifacts_db_path] = lambda: db
    return TestClient(app), db, tmp_path / "artifacts"


def test_latest_leads_with_the_verdict(api):
    client, db, root = api
    aid, _ = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1.5, None]})},
                              verdict="DESCRIPTIVE ONLY", finished=utcnow())
    body = client.get("/api/artifacts/ops.selftest/latest").json()
    assert list(body)[:2] == ["verdict", "verdict_detail"]
    assert body["verdict"] == "DESCRIPTIVE ONLY" and body["stale"] is False
    assert body["manifest"]["id"] == aid and body["provenance"] == [{"source": "test"}]
    m = client.get(f"/api/artifacts/ops.selftest/{aid}").json()
    assert list(m)[0] == "verdict" and m["manifest"]["tables"] == ["summary"]
    t = client.get(f"/api/artifacts/ops.selftest/{aid}/summary").json()
    assert list(t)[0] == "verdict" and t["rows"] == 2 and t["columns"] == ["x"]
    assert t["data"]["x"] == [1.5, None]  # NaN serialises as null (api.serialize.clean); II.3 frame shape


def test_artifacts_api_reports_stale(api):
    client, db, root = api
    publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})},
                     finished=utcnow() - timedelta(hours=30))
    assert client.get("/api/artifacts/ops.selftest/latest").json()["stale"] is True  # max_age 26h


def test_artifacts_api_refuses_traversal_and_unknowns(api):
    client, db, root = api
    aid, path = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})},
                                 finished=utcnow())
    assert client.get("/api/artifacts/..%2F..%2Fetc/latest").status_code == 404
    assert client.get("/api/artifacts/not.a.kind/latest").json()["error"] == "unknown_kind"
    assert client.get("/api/artifacts/ops.selftest/latest?params_hash=zz").status_code == 422
    assert client.get("/api/artifacts/ops.selftest/NOT-A-ULID").status_code == 404
    assert client.get(f"/api/artifacts/ops.selftest/{aid}/..%2Fmanifest").status_code == 404
    assert client.get(f"/api/artifacts/ops.selftest/{aid}/other").json()["error"] == "no_table"
    assert client.get(f"/api/artifacts/risk.mc_atlas/{aid}").status_code == 404  # id of another kind
    (path / "summary.parquet").unlink()
    assert client.get(f"/api/artifacts/ops.selftest/{aid}/summary").status_code == 410
    (path / "manifest.json").unlink()
    assert client.get(f"/api/artifacts/ops.selftest/{aid}").status_code == 410


def test_table_reads_are_paged(api):
    client, db, root = api
    aid, _ = publish_artifact(db, root, "ops.selftest", {"t": pd.DataFrame({"x": list(range(10))})},
                              finished=utcnow())
    t = client.get(f"/api/artifacts/ops.selftest/{aid}/t?limit=3&offset=4").json()
    assert t["rows"] == 10 and t["offset"] == 4 and t["data"]["x"] == [4, 5, 6]
    assert client.get(f"/api/artifacts/ops.selftest/{aid}/t?limit=0").status_code == 422
