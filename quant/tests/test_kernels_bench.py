"""The kernel benchmark table: docs/perf/kernels.json -> kernels.md, served at
GET /api/ops/kernels (the Ship plan's Lane A delta)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers import ops as ops_mod
from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.kernels import bench

TABLE = REPO_ROOT / "docs" / "perf" / "kernels.json"
MD = REPO_ROOT / "docs" / "perf" / "kernels.md"
ROW = {"name": "var_es_from_pnl", "python_ms": 12.5, "rust_1t_ms": 1.25, "rust_2t_ms": None,
       "host": "Test CPU @ 1.0GHz", "measured_on": "test box", "sha": "0b87dec"}


def test_markdown_is_generated_from_the_json():
    assert MD.read_text(encoding="utf-8") == bench.render_markdown(json.loads(TABLE.read_text(encoding="utf-8")))


def test_committed_table_validates():
    bench.validate_table(json.loads(TABLE.read_text(encoding="utf-8")))


def test_sizes_cover_the_api():
    from ohcamel_quant.kernels import API

    assert set(bench.SIZES) == set(API)


@pytest.mark.parametrize("bad,msg", [
    ({**ROW, "name": "nope"}, "unknown kernel"),
    ({**ROW, "python_ms": -1.0}, "positive"),
    ({**ROW, "rust_1t_ms": None}, "positive"),
    ({**ROW, "sha": "xyz"}, "sha"),
    ({**ROW, "measured_on": "a | b"}, "measured_on"),
    ({k: v for k, v in ROW.items() if k != "measured_on"}, "fields"),
    ({**ROW, "extra": 1}, "fields"),
])
def test_validate_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        bench.validate_table({"kernels": [bad]})


def test_validate_rejects_a_non_table():
    with pytest.raises(ValueError, match="kernels"):
        bench.validate_table({"rows": []})


def test_render_rows():
    md = bench.render_markdown({"kernels": [ROW]})
    assert "| var_es_from_pnl |" in md
    assert "| 10.0x |" in md          # 12.5 / 1.25 = 10.0
    assert "| n/a |" in md            # single-threaded: no 2T column


def test_render_empty_says_so():
    assert "No benchmark run is recorded yet." in bench.render_markdown({"kernels": []})


def _client(path):
    app = create_app()
    app.dependency_overrides[ops_mod.get_kernels_table_path] = lambda: path
    return TestClient(app)


def test_endpoint_serves_rows(tmp_path):
    p = tmp_path / "kernels.json"
    p.write_text(json.dumps({"kernels": [ROW]}))
    j = _client(p).get("/api/ops/kernels").json()
    # the row's commit (``sha``) is not served: the public host shows no commit SHA (the owner's ruling, 2026-11-20)
    assert j["kernels"] == [{k: v for k, v in ROW.items() if k != "sha"}]
    assert j["engine"] in ("rust", "python")
    assert set(j["engines"]) == set(bench.SIZES)
    assert j["provenance"][0]["source"] == "docs/perf/kernels.json"
    assert isinstance(j["notes"], list) and j["notes"]


def test_endpoint_empty_table_says_so(tmp_path):
    p = tmp_path / "kernels.json"
    p.write_text(json.dumps({"kernels": []}))
    j = _client(p).get("/api/ops/kernels").json()
    assert j["kernels"] == [] and any("no benchmark run" in n for n in j["notes"])


def test_endpoint_missing_file_is_503(tmp_path):
    r = _client(tmp_path / "missing.json").get("/api/ops/kernels")
    assert r.status_code == 503 and r.json()["error"] == "kernels_table_unavailable"


def test_endpoint_invalid_file_is_503(tmp_path):
    p = tmp_path / "kernels.json"
    p.write_text("{")
    r = _client(p).get("/api/ops/kernels")
    assert r.status_code == 503 and "invalid" in r.json()["detail"]


def test_endpoint_reads_the_committed_table(client):
    assert client.get("/api/ops/kernels").status_code == 200


def test_bench_runner_times_every_kernel(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("bench_kernels", REPO_ROOT / "tools" / "perf" / "bench_kernels.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from ohcamel_quant.kernels import API

    assert set(mod.cases()) == set(API)


# ---------------------------------------------------------------- Task 4.4: dispatch follows the file
def _doc(*rows):
    return {"kernels": [{**ROW, "name": "garch_fit", **r} for r in rows]}


def test_the_committed_table_records_host_and_measured_on_for_every_row():
    for r in bench.validate_table(json.loads(TABLE.read_text(encoding="utf-8"))):
        assert r["host"].strip() and r["measured_on"].strip()


def test_validate_requires_a_host():
    with pytest.raises(ValueError, match="fields"):
        bench.validate_table({"kernels": [{k: v for k, v in ROW.items() if k != "host"}]})


def test_faster_engine_is_whichever_the_row_measured_faster():
    assert bench.faster_engine(_doc({"python_ms": 3.0, "rust_1t_ms": 20.0}), "garch_fit") == "python"
    assert bench.faster_engine(_doc({"python_ms": 30.0, "rust_1t_ms": 2.0}), "garch_fit") == "rust"
    assert bench.faster_engine(_doc({"python_ms": 3.0, "rust_1t_ms": 3.0}), "garch_fit") == "rust"   # a tie keeps the kernel
    assert bench.faster_engine(_doc(), "garch_fit") is None                                          # nothing measured


def test_a_row_measured_on_this_host_outranks_another_hosts():
    doc = _doc({"python_ms": 3.0, "rust_1t_ms": 20.0, "host": "Laptop CPU"},
               {"python_ms": 30.0, "rust_1t_ms": 2.0, "host": "Droplet CPU"})
    assert bench.faster_engine(doc, "garch_fit", host="Laptop CPU") == "python"
    assert bench.faster_engine(doc, "garch_fit", host="Droplet CPU") == "rust"
    assert bench.faster_engine(doc, "garch_fit", host="Other") == "rust"      # no match: the latest row


def test_cpu_model_is_one_nonempty_line_without_pipes():
    m = bench.cpu_model()
    assert m.strip() and "\n" not in m and "|" not in m
