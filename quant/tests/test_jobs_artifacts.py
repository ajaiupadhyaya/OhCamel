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
                 "cpu_seconds", "peak_rss_bytes", "engine", "provenance", "notes", "survivorship", "tables",
                 "verdict", "verdict_detail", "data_hash", "data_hash_note", "reads", "seed", "lock_hash", "threads",
                 "kernels", "runtime"]


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


def test_build_manifest_carries_and_checks_the_verdict(conn):
    job = _job(conn)
    base = {"data_asof": None, "provenance": [], "notes": [], "survivorship": None, "engine": "python", "tables": []}
    m = build_manifest(job, {**base, "verdict": "FAIL", "verdict_detail": "dsr 0.12 (needs >= 0.3)"},
                       code_sha="x", finished_at=iso(T0), cpu_seconds=0.0, peak_rss_bytes=0)
    assert (m["verdict"], m["verdict_detail"]) == ("FAIL", "dsr 0.12 (needs >= 0.3)")
    m2 = build_manifest(job, base, code_sha="x", finished_at=iso(T0), cpu_seconds=0.0, peak_rss_bytes=0)
    assert (m2["verdict"], m2["verdict_detail"]) == (None, None)  # ingest/api kinds carry no verdict
    with pytest.raises(ValueError, match="verdict"):
        build_manifest(job, {**base, "verdict": "MAYBE"}, code_sha="x", finished_at=iso(T0), cpu_seconds=0.0,
                       peak_rss_bytes=0)


def test_write_tables_writes_parquet_and_validates_names(tmp_path):
    names = write_tables(tmp_path, {"series": pd.DataFrame({"date": ["2026-06-01"], "value": [4.47]})})
    assert names == ["series"]
    assert pd.read_parquet(tmp_path / "series.parquet")["value"].tolist() == [4.47]
    with pytest.raises(ValueError):
        write_tables(tmp_path, {"Bad Name": pd.DataFrame({"a": [1]})})
    with pytest.raises(ValueError):
        write_tables(tmp_path, {"../escape": pd.DataFrame({"a": [1]})})


def test_publish_writes_the_reads_beside_the_manifest(tmp_path, conn):
    """A nightly job over 600 names makes thousands of reads: the list goes to reads.json, the manifest keeps a
    summary (every manifest is read on each artifact lookup; no read's name, which can carry a desk job's
    symbols), and read_reads gives the list back."""
    from ohcamel_quant.jobs.artifacts import read_reads

    job = _job(conn)
    root = tmp_path / "artifacts"
    staging = root / ".staging" / job.id
    staging.mkdir(parents=True)
    reads = [{"name": "market.fred", "sha256": "a" * 64, "source": "fred", "regenerable": False},
             {"name": "w", "sha256": "b" * 64, "source": "warehouse", "regenerable": True}]
    final = publish(staging, root, job, {"id": job.id, "reads": reads})
    m = json.loads((final / "manifest.json").read_text())
    assert m["reads"] == {"file": "reads.json", "count": 2, "sources": ["fred", "warehouse"],
                          "not_regenerable": 1}                                 # a count: names stay in reads.json
    assert read_reads(final) == reads


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


# --- Task 3.3: a number can be regenerated -------------------------------------------------------------------------
# The fixture warehouse (the nine committed ETFs, logged as full-history fetches so the facade serves them), the
# clock pinned to 2026-06-01 17:00 New York -- the fixtures' last session, so the warehouse is fresh -- and
# risk.mc_atlas, a seeded product (Monte Carlo paths), small, reading the books' returns from the warehouse.

ATLAS = {"n_paths": 2_000, "member_paths": 2_000, "members": False}
FIXTURE_NOW = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def fixture_wh(tmp_path_factory):
    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

    return build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "warehouse.duckdb", requested_start="1990-01-01")


def _settings(tmp_path, wh):
    from ohcamel_quant.config import Settings

    return Settings(warehouse_path=wh, offline=True, data_dir=tmp_path / "data")


def _run(tmp_path, wh, kind="risk.mc_atlas", params=None, name="a"):
    from ohcamel_quant.jobs.local import run_local

    return run_local(kind, ATLAS if params is None else params, artifacts_root=tmp_path / name,
                     settings=_settings(tmp_path, wh), threads=1, now=FIXTURE_NOW, code_sha="test")


def _tables(path):
    m = json.loads((path / "manifest.json").read_text())
    return m, {t: pd.read_parquet(path / f"{t}.parquet") for t in m["tables"]}


def _reads(path):
    from ohcamel_quant.jobs.artifacts import read_reads

    return read_reads(path)


def test_two_runs_of_a_seeded_product_on_one_fixture_give_one_data_hash_and_equal_tables(tmp_path, fixture_wh):
    from ohcamel_quant.jobs.provenance import lock_hash

    p1 = _run(tmp_path, fixture_wh, name="a")
    m1, t1 = _tables(p1)
    m2, t2 = _tables(_run(tmp_path, fixture_wh, name="b"))
    reads = _reads(p1)
    assert m1["data_hash"] is not None and m1["data_hash"] == m2["data_hash"]
    assert m1["data_hash_note"] is None
    assert m1["seed"] == {"seed": 20_261_006}                         # atlas's fixed seed, recorded, not changed
    assert m1["lock_hash"] == lock_hash() and len(m1["lock_hash"]) == 64
    assert m1["threads"] == 1 and m1["kernels"]["engine"] in ("rust", "python")
    assert reads and all(r["regenerable"] for r in reads)
    assert all(r["source"].startswith("warehouse:") for r in reads)         # the warehouse served the books
    # the reads live beside the manifest (reads.json); the manifest says how many, from where, and none unservable
    assert m1["reads"] == {"file": "reads.json", "count": len(reads), "sources": ["warehouse:fixture:alpaca"],
                           "not_regenerable": 0}
    assert set(t1) == set(t2) == {"summary", "euler", "members"}
    for name in t1:
        pd.testing.assert_frame_equal(t1[name], t2[name])


def test_changing_one_input_row_changes_the_data_hash(tmp_path, fixture_wh):
    import shutil

    import duckdb

    changed = tmp_path / "changed.duckdb"
    shutil.copy(fixture_wh, changed)
    con = duckdb.connect(str(changed))
    con.execute("UPDATE bars_daily SET adj_close = adj_close * 1.0001 WHERE ticker = 'SPY' AND date = DATE '2026-05-29'")
    con.close()
    pb, pm = _run(tmp_path, fixture_wh, name="base"), _run(tmp_path, changed, name="moved")
    base, moved = (json.loads((p / "manifest.json").read_text()) for p in (pb, pm))
    assert base["data_hash"] != moved["data_hash"]
    changed_reads = [r["name"] for r, b in zip(_reads(pm), _reads(pb), strict=True) if r["sha256"] != b["sha256"]]
    assert changed_reads and all("SPY" in n for n in changed_reads), changed_reads    # only the books holding SPY


# Three test kinds, registered for the tests below only: one that draws from an unseeded generator, one that
# declares it draws and takes its seed from params, one that declares it draws and records nothing.

def unseeded_handler(params, ctx):
    import numpy as np

    from ohcamel_quant.jobs.artifacts import ArtifactSpec

    x = np.random.default_rng().normal(size=3)
    return ArtifactSpec(tables={"draws": pd.DataFrame({"x": x})}, data_asof=None, provenance=[])


def params_seed_handler(params, ctx):
    from ohcamel_quant.jobs.artifacts import ArtifactSpec

    rng = ctx.rng(params.get("seed"))
    return ArtifactSpec(tables={"draws": pd.DataFrame({"x": rng.normal(size=3)})}, data_asof=None, provenance=[])


def silent_handler(params, ctx):
    from ohcamel_quant.jobs.artifacts import ArtifactSpec

    return ArtifactSpec(tables={"draws": pd.DataFrame({"x": [1.0]})}, data_asof=None, provenance=[])


@pytest.fixture
def test_kinds():
    from ohcamel_quant.jobs.kinds import REGISTRY, KindSpec, register

    added = [register(KindSpec("test.unseeded", "test_jobs_artifacts:unseeded_handler", "S", heavy=False)),
             register(KindSpec("test.params_seed", "test_jobs_artifacts:params_seed_handler", "S", heavy=False,
                               draws_random=True)),
             register(KindSpec("test.silent", "test_jobs_artifacts:silent_handler", "S", heavy=False,
                               draws_random=True))]
    yield
    for k in added:
        REGISTRY.pop(k.name, None)


def test_a_product_that_draws_random_numbers_with_no_seed_fails_the_manifest_build(tmp_path, fixture_wh, test_kinds):
    from ohcamel_quant.jobs.provenance import UnseededDraw

    with pytest.raises(UnseededDraw, match="default_rng"):
        _run(tmp_path, fixture_wh, kind="test.unseeded", params={}, name="u")
    with pytest.raises(UnseededDraw, match="no seed"):
        _run(tmp_path, fixture_wh, kind="test.params_seed", params={}, name="p")
    with pytest.raises(UnseededDraw, match="declares it draws random numbers"):
        _run(tmp_path, fixture_wh, kind="test.silent", params={}, name="s")
    assert not (tmp_path / "u" / "test.unseeded").exists()               # nothing was published
    m = json.loads((_run(tmp_path, fixture_wh, kind="test.params_seed", params={"seed": 5}, name="ok")
                    / "manifest.json").read_text())
    assert m["seed"] == {"seed": 5}


def test_build_manifest_refuses_a_declared_draw_with_no_seed_and_any_unseeded_draw(conn):
    from ohcamel_quant.jobs.provenance import UnseededDraw

    enqueue(conn, kind="risk.mc_atlas", params={}, priority=2, mem_class="M", heavy=False,
            submitted_by="schedule:t", now=T0)
    job = claim_next(conn, allowed_classes=frozenset({"M"}), allow_heavy=True, now=T0)
    base = {"data_asof": None, "provenance": [], "notes": [], "survivorship": None, "engine": "python", "tables": [],
            "reads": [], "unseeded": [], "unrecorded": []}
    kw = {"code_sha": "x", "finished_at": iso(T0), "cpu_seconds": 0.0, "peak_rss_bytes": 0}
    with pytest.raises(UnseededDraw, match="declares it draws random numbers"):
        build_manifest(job, {**base, "seed": None}, **kw)
    with pytest.raises(UnseededDraw, match="global state"):
        build_manifest(job, {**base, "seed": {"seed": 1}, "unseeded": ["numpy.random's global state was drawn from"]},
                       **kw)
    m = build_manifest(job, {**base, "seed": {"seed": 1}}, **kw)
    assert m["seed"] == {"seed": 1} and m["data_hash"] is not None


def test_a_kind_with_unrecorded_reads_publishes_data_hash_null_with_the_reason(conn):
    job = _job(conn)   # ingest.fred records through the facade: complete
    base = {"data_asof": None, "provenance": [], "notes": [], "survivorship": None, "engine": "python", "tables": [],
            "reads": [{"name": "market.fred", "sha256": "a" * 64, "source": "fred", "regenerable": False}],
            "seed": None, "unseeded": [], "unrecorded": []}
    kw = {"code_sha": "x", "finished_at": iso(T0), "cpu_seconds": 0.0, "peak_rss_bytes": 0}
    m = build_manifest(job, base, **kw)
    assert m["data_hash"] is not None and m["data_hash_note"] is None
    m = build_manifest(job, {**base, "unrecorded": ["warehouse.sql() bypassed the recorder"]}, **kw)
    assert m["data_hash"] is None and "warehouse.sql()" in m["data_hash_note"]
    legacy = {k: v for k, v in base.items() if k not in ("reads", "unseeded", "unrecorded")}
    m = build_manifest(job, legacy, **kw)
    assert m["data_hash"] is None and "no reads were recorded" in m["data_hash_note"]



# --- pinned tables (fix round 1): a seed or stream change fails loudly -------------------------------------------
# The canonical hash of each table with every float rounded to 9 significant digits (so a last-bit libm difference
# between macOS and Linux cannot move it, while any change of seed, stream or method does), pinned at the values
# the code produced on 2026-11-18 -- tables the review found equal to b8a9604's. On the NumPy reference only: the
# Rust kernels draw from another generator (equal in distribution, not in value), so there it is skipped.

def _rounded_hash(df):
    import numpy as np

    from ohcamel_quant.jobs.provenance import frame_hash

    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = [float(f"{x:.9g}") if np.isfinite(x) else x for x in out[c]]
    return frame_hash(out)


GOLDEN = {
    "risk.mc_atlas": ({"n_paths": 2_000, "member_paths": 2_000, "members": False}, {
        "summary": "8a0081eb7d13672faf956c13c2dea33407563fbfe310c569b169935838669e66",
        "euler": "d40e16cf30c306b5dc524264402db9c4cc03128db375579c976eed82a06108d5",
        "members": "5a05a9781d46871fc92b8b780a4358ca99b1dab5720c74f41d0cb9cf915bdf5f"}),
    "vol.forecast_league": ({"tickers": ["SPY"], "eval_days": 42, "refit_every": 1000}, {
        "league": "13066a63060380a953cd4fa86fe1cc59ebc9793a718f02420c064336a34aec03",
        "dm": "6362d591ba00163750d67d5df4cf4d2ef5b01470d307c01f78212a8e5b05a557",
        "summary": "b10a27b94146a65598549639f5aaf15d09ab01250d8aafae63c75735d60f525e",
        "forecasts": "09b56dbbad42764e8fcf840f4119c40c017b7fb514c85df9f920291104d4ef87",
        "skipped": "fc42feaab8427b4e7db905c2f52111759b58430f3e8a439dda232194f1c8d5ef",
        "dropped": "66b783bee1be18a2b9df15a26bed354e4c3b652f029eaa06118ef4f7c49158bb"}),
}


@pytest.mark.parametrize("kind", sorted(GOLDEN))
def test_the_pinned_tables_of_two_seeded_products_have_not_moved(tmp_path, fixture_wh, kind):
    from ohcamel_quant import kernels

    if kernels.engine_of("copula_t_paths") != "python":
        pytest.skip("pinned on the NumPy reference kernels")
    params, want = GOLDEN[kind]
    got = {t: _rounded_hash(df) for t, df in _tables(_run(tmp_path, fixture_wh, kind=kind, params=params))[1].items()}
    assert got == want, got
