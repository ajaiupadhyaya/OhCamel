"""Lane B, B1: artifacts on disk and the `artifacts` table (contract II.3, ship delta)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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



# --- pinned tables (fix rounds 1 and 2): a seed or stream change fails loudly ------------------------------------
# risk.mc_atlas: the canonical hash of each table with every float rounded to 9 significant digits, pinned at the
# values the code produced on 2026-11-18 (tables the review found equal to b8a9604's); it passed on Linux CI too.
# On the NumPy reference only: the Rust kernels draw from another generator (equal in distribution, not in value).

def _rounded_hash(df):
    import numpy as np

    from ohcamel_quant.jobs.provenance import frame_hash

    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = [float(f"{x:.9g}") if np.isfinite(x) else x for x in out[c]]
    return frame_hash(out)


# summary re-pinned in Task 4.4: the copula nu is now the copula-likelihood maximiser (was the marginals' median t MLE)
ATLAS_PIN = ({"n_paths": 2_000, "member_paths": 2_000, "members": False}, {
    "summary": "6e20afd42649f22916516b9cc79e3afe8e38a2baf0a25fab4ae605784f70d092",
    "euler": "d40e16cf30c306b5dc524264402db9c4cc03128db375579c976eed82a06108d5",
    "members": "5a05a9781d46871fc92b8b780a4358ca99b1dab5720c74f41d0cb9cf915bdf5f"})


def _reference_kernels_only():
    from ohcamel_quant import kernels

    if kernels.engine_of("copula_t_paths") != "python":
        pytest.skip("pinned on the NumPy reference kernels")


def test_the_pinned_tables_of_risk_mc_atlas_have_not_moved(tmp_path, fixture_wh):
    _reference_kernels_only()
    params, want = ATLAS_PIN
    got = {t: _rounded_hash(df) for t, df in _tables(_run(tmp_path, fixture_wh, params=params))[1].items()}
    assert got == want, got


# vol.forecast_league: GARCH fits by Nelder-Mead (xatol 1e-10, fatol 1e-14) are not bit-portable -- Linux CI hashed
# league, dm, summary and forecasts differently from macOS at 9 digits (fix round 2). So its pin is the committed
# VALUES (tests/data/golden/, with their provenance): every non-numeric column, every integer and boolean column,
# the ranking and selection columns and the row set must be EQUAL; every other float within RTOL relative. A
# different seed, stream or method moves these values by percent; the optimizer's own tolerance is ~1e-10. Every
# nonzero difference is reported as a warning ("vol.forecast_league golden: largest differences ..."), pass or
# fail, so a CI log says how large the cross-platform difference is.

LEAGUE_GOLDEN = Path(__file__).parent / "data" / "golden" / "vol_forecast_league_fixture.json"
LEAGUE_EXACT = {"league": {"rank_qlike", "in_mcs", "n"}, "summary": {"names", "mean_rank", "mcs_rate"}}
RTOL = 1e-6
#: Per-column relative tolerances, as strict as the cross-platform evidence allows. Linux CI (fix round 1 of Task
#: 3.4) measured against the macOS golden: dm.stat 6.3e-7, dm.pvalue 2.5e-7, forecasts.var_1d_egarch 3.7e-8 (the
#: other forecast columns and the league's losses ~1e-15). The Diebold-Mariano statistic divides the mean of a
#: DIFFERENCE of two near-equal loss series by its standard error, so the fits' ~1e-10 platform noise is amplified
#: by the cancellation; 1e-6 left those two columns ~1.6x headroom, 1e-5 leaves ~16x. The forecasts are the fits'
#: own output and need no such allowance: 1e-7 (about 3x the measured 3.7e-8).
LEAGUE_RTOL = {("dm", "stat"): 1e-5, ("dm", "pvalue"): 1e-5,
               **{("forecasts", c): 1e-7 for c in ("var_1d_garch", "var_1d_gjr", "var_1d_egarch", "var_1d_ewma")}}


def _golden_frame(t):
    return pd.DataFrame(t["rows"], columns=t["columns"])


def compare_to_golden(got, golden, exact, rtol=RTOL, col_rtol=None):
    """(problems, differences): problems are readable mismatches; differences name every table and float column
    with its largest absolute and relative difference. ``col_rtol`` (default ``LEAGUE_RTOL``) overrides ``rtol`` per
    (table, column). Non-finite values must match exactly (NaN with NaN, +inf with +inf); relative differences are
    taken only where both values are finite."""
    col_rtol = LEAGUE_RTOL if col_rtol is None else col_rtol
    import numpy as np

    problems, diffs = [], []
    if sorted(got) != sorted(golden):
        return [f"tables differ: got {sorted(got)}, golden {sorted(golden)}"], diffs
    for name in sorted(golden):
        want, have = _golden_frame(golden[name]), got[name].reset_index(drop=True)
        if list(have.columns) != list(want.columns) or len(have) != len(want):
            problems.append(f"{name}: shape or columns differ: got {list(have.columns)} x {len(have)}, golden "
                            f"{list(want.columns)} x {len(want)}")
            continue
        for col in want.columns:
            a, b = want[col], have[col]
            floaty = pd.api.types.is_float_dtype(b) and col not in exact.get(name, set())
            if floaty:
                tol = col_rtol.get((name, col), rtol)
                x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
                finite = np.isfinite(x) & np.isfinite(y)
                same_nonfinite = ~finite & ((np.isnan(x) & np.isnan(y)) | (x == y))
                with np.errstate(invalid="ignore"):
                    d = np.where(finite, np.abs(x - y), 0.0)
                r = np.where(finite, d / np.maximum(np.abs(x), 1e-300), 0.0)
                if d.size and float(d.max()) > 0:
                    diffs.append(f"{name}.{col}: max abs {float(d.max()):.3e}, max rel {float(r.max()):.3e}")
                odd = ~finite & ~same_nonfinite
                if odd.any():
                    i = int(np.flatnonzero(odd)[0])
                    problems.append(f"{name}.{col}: non-finite values must match exactly (row {i}: golden {x[i]!r}, "
                                    f"got {y[i]!r})")
                elif (r > tol).any():
                    problems.append(f"{name}.{col}: beyond {tol:g} relative -- max abs {float(d.max()):.3e}, "
                                    f"max rel {float(r.max()):.3e} (row {int(r.argmax())}: golden {x[r.argmax()]!r}, "
                                    f"got {y[r.argmax()]!r})")
            else:
                ga = [None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in a]
                gb = [None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in b]
                if ga != gb:
                    problems.append(f"{name}.{col}: must be equal -- golden {ga}, got {gb}")
    return problems, diffs


def test_the_league_comparison_reports_sizes_and_holds_its_tolerance():
    golden = json.loads(LEAGUE_GOLDEN.read_text())["tables"]
    got = {k: _golden_frame(t) for k, t in golden.items()}
    assert compare_to_golden(got, golden, LEAGUE_EXACT) == ([], [])
    near = {k: v.copy() for k, v in got.items()}
    near["league"].loc[0, "qlike"] *= 1 + 1e-8
    problems, diffs = compare_to_golden(near, golden, LEAGUE_EXACT)
    assert problems == [] and diffs and "league.qlike: max abs" in diffs[0]
    far = {k: v.copy() for k, v in got.items()}
    far["forecasts"].loc[0, "var_1d_garch"] *= 1 + 1e-5
    far["league"].loc[0, "rank_qlike"] += 1
    problems, _ = compare_to_golden(far, golden, LEAGUE_EXACT)
    assert any("forecasts.var_1d_garch: beyond 1e-07 relative" in p and "max rel" in p for p in problems)
    assert any(p.startswith("league.rank_qlike: must be equal") for p in problems)



def test_the_league_tolerance_is_per_column_as_measured_across_platforms():
    """Linux CI against the macOS golden measured dm.stat 6.3e-7 and dm.pvalue 2.5e-7 relative, the forecasts at most
    3.7e-8: the DM columns get 1e-5, the forecast columns 1e-7, the rest keep 1e-6."""
    golden = json.loads(LEAGUE_GOLDEN.read_text())["tables"]
    got = {k: _golden_frame(t) for k, t in golden.items()}

    def moved(table, col, rel):
        g = {k: v.copy() for k, v in got.items()}
        g[table].loc[0, col] *= 1 + rel
        return compare_to_golden(g, golden, LEAGUE_EXACT)[0]

    assert moved("dm", "stat", 5e-6) == [] and moved("dm", "pvalue", 5e-6) == []
    assert moved("dm", "stat", 2e-5) and moved("dm", "pvalue", 2e-5)
    assert moved("forecasts", "var_1d_egarch", 5e-8) == []
    assert any("forecasts.var_1d_egarch: beyond 1e-07 relative" in p for p in moved("forecasts", "var_1d_egarch", 5e-7))
    assert moved("league", "qlike", 5e-7) == [] and moved("league", "qlike", 5e-6)


def test_non_finite_league_values_must_match_exactly():
    """An inf against a finite value, or +inf against -inf, is a mismatch however the relative difference comes
    out (inf - inf is NaN, which compares false against any tolerance); NaN against NaN is equal."""
    golden = json.loads(LEAGUE_GOLDEN.read_text())["tables"]
    got = {k: _golden_frame(t) for k, t in golden.items()}
    gold = {k: dict(v) for k, v in golden.items()}

    def with_values(golden_value, got_value):
        g = {k: v.copy() for k, v in got.items()}
        g["dm"]["stat"] = g["dm"]["stat"].astype(float)
        g["dm"].loc[0, "stat"] = got_value
        want = {k: dict(v) for k, v in gold.items()}
        rows = [list(r) for r in golden["dm"]["rows"]]
        rows[0][golden["dm"]["columns"].index("stat")] = golden_value
        want["dm"] = {**golden["dm"], "rows": rows}
        return compare_to_golden(g, want, LEAGUE_EXACT)[0]

    inf = float("inf")
    assert with_values(inf, 1.0) and with_values(1.0, inf) and with_values(inf, -inf)
    assert with_values(inf, inf) == [] and with_values(float("nan"), float("nan")) == []
    assert with_values(float("nan"), 1.0)


def test_the_pinned_values_of_vol_forecast_league_have_not_moved(tmp_path, fixture_wh):
    import warnings

    _reference_kernels_only()
    doc = json.loads(LEAGUE_GOLDEN.read_text())
    got = _tables(_run(tmp_path, fixture_wh, kind="vol.forecast_league", params=doc["params"]))[1]
    problems, diffs = compare_to_golden(got, doc["tables"], LEAGUE_EXACT)
    if diffs:
        warnings.warn("vol.forecast_league golden: largest differences from the values made on "
                      f"{doc['provenance']['platform']}: " + "; ".join(diffs), stacklevel=1)
    assert not problems, "vol.forecast_league golden:\n  " + "\n  ".join(problems + ["differences: " + "; ".join(diffs)])
