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


# Harden H4 (security): no request value and no on-disk link takes a read outside <data>/artifacts.
@pytest.mark.parametrize("url", [
    "/api/artifacts/..%2F..%2Fetc/latest",
    "/api/artifacts/%2e%2e%2f%2e%2e%2fetc/latest",
    "/api/artifacts/%2e%2e/latest",
    "/api/artifacts/%2Fetc%2Fpasswd/latest",
    "/api/artifacts/..%5C..%5Cetc/latest",
    "/api/artifacts/ops.selftest/..%2F..%2F..%2Fetc%2Fpasswd",
    "/api/artifacts/ops.selftest/%2e%2e%2f%2e%2e%2fjobs.sqlite",
    "/api/artifacts/ops.selftest/%2Fetc%2Fpasswd",
    "/api/artifacts/ops.selftest/%2e%2e",
])
def test_encoded_traversal_in_kind_or_id_is_404(api, url):
    client, db, root = api
    publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=utcnow())
    r = client.get(url)
    assert r.status_code == 404 and "root:" not in r.text


@pytest.mark.parametrize("table", ["..%2Fmanifest", "%2e%2e%2f%2e%2e%2f%2e%2e%2fjobs", "%2Fetc%2Fpasswd",
                                   "summary.parquet", "%2e%2e", "summary%00", "..%5Csummary", "SUMMARY"])
def test_encoded_traversal_in_table_is_404(api, table):
    client, db, root = api
    aid, _ = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=utcnow())
    assert client.get(f"/api/artifacts/ops.selftest/{aid}/{table}").status_code == 404


def _outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir(exist_ok=True)
    pd.DataFrame({"secret": ["s3cr3t"]}).to_parquet(out / "summary.parquet", index=False)
    (out / "manifest.json").write_text('{"verdict": "PASS", "tables": ["summary"], "secret": "s3cr3t"}')
    return out


def test_a_symlinked_table_pointing_outside_is_refused(api, tmp_path):
    client, db, root = api
    aid, path = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=utcnow())
    out = _outside(tmp_path)
    (path / "summary.parquet").unlink()
    (path / "summary.parquet").symlink_to(out / "summary.parquet")
    r = client.get(f"/api/artifacts/ops.selftest/{aid}/summary")
    assert r.status_code == 404 and r.json()["error"] == "outside_root" and "s3cr3t" not in r.text


def test_a_symlinked_manifest_or_directory_pointing_outside_is_refused(api, tmp_path):
    client, db, root = api
    aid, path = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=utcnow())
    out = _outside(tmp_path)
    (path / "manifest.json").unlink()
    (path / "manifest.json").symlink_to(out / "manifest.json")
    for url in (f"/api/artifacts/ops.selftest/{aid}", f"/api/artifacts/ops.selftest/{aid}/summary",
                "/api/artifacts/ops.selftest/latest"):
        r = client.get(url)
        assert r.status_code == 404 and "s3cr3t" not in r.text, url
    import shutil
    shutil.rmtree(path)
    path.symlink_to(out, target_is_directory=True)  # the artifact directory itself is a link out
    for url in (f"/api/artifacts/ops.selftest/{aid}", f"/api/artifacts/ops.selftest/{aid}/summary",
                "/api/artifacts/ops.selftest/latest"):
        r = client.get(url)
        assert r.status_code == 404 and "s3cr3t" not in r.text, url


def test_a_row_whose_path_leaves_the_root_is_refused(api, tmp_path):
    """A tampered or stale artifacts row (absolute path elsewhere, or root/../x) is never followed."""
    from ohcamel_quant.jobs.db import connect

    client, db, root = api
    aid, _ = publish_artifact(db, root, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=utcnow())
    out = _outside(tmp_path)
    for bad in (str(out), str(root / ".." / "outside")):
        conn = connect(db)
        conn.execute("UPDATE artifacts SET path = ? WHERE id = ?", (bad, aid))
        conn.commit()
        conn.close()
        for url in (f"/api/artifacts/ops.selftest/{aid}", f"/api/artifacts/ops.selftest/{aid}/summary",
                    "/api/artifacts/ops.selftest/latest"):
            r = client.get(url)
            assert r.status_code == 404 and "s3cr3t" not in r.text, (bad, url)
