"""Task 3.3: tools/regenerate.py makes an artifact's numbers again from a warehouse export, or says why not.

One fixture artifact (risk.mc_atlas, small, reading the fixture warehouse) and one export of that warehouse,
made once for the module; every case works on copies, and the last checks nothing it read was written.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime

import duckdb
import pandas as pd
import pytest

from ohcamel_quant.config import REPO_ROOT, Settings
from ohcamel_quant.jobs import regenerate as R
from ohcamel_quant.jobs.local import run_local
from ohcamel_quant.warehouse.export import export_warehouse
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

NOW = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)
ATLAS = {"n_paths": 2_000, "member_paths": 2_000, "members": ["SPY", "QQQ"]}


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """(artifact dir, export dir) -- the job ran on the warehouse the export was taken from."""
    root = tmp_path_factory.mktemp("regen")
    wh = build_fixture_warehouse(root / "warehouse.duckdb", requested_start="1990-01-01")
    art = run_local("risk.mc_atlas", ATLAS, artifacts_root=root / "artifacts",
                    settings=Settings(warehouse_path=wh, offline=True, data_dir=root / "data"), threads=1, now=NOW,
                    code_sha=R.checkout_sha())
    export = export_warehouse(wh, root / "exports", day="2026-06-01")
    wh.unlink()                                                          # the drill's "wipe": only the export is left
    return art, export


def _snapshot(path):
    return sorted((str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns) for p in path.rglob("*"))


def _run(capsys, *args):
    rc = R.main([str(a) for a in args])
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_regenerate_passes_on_a_fixture_artifact(made, capsys):
    art, export = made
    rc, out = _run(capsys, art, "--warehouse", export)
    assert rc == 0, out
    assert "data_hash matches" in out and "every table matches" in out
    for table in ("summary", "euler", "members"):
        assert table in out


def test_regenerate_fails_when_a_row_of_the_export_is_altered(made, capsys, tmp_path):
    art, export = made
    altered = tmp_path / "altered"
    shutil.copytree(export, altered)
    f = altered / "bars_daily.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * REPLACE (CASE WHEN ticker = 'SPY' AND date = DATE '2026-05-29' "
                f"THEN adj_close * 1.0001 ELSE adj_close END AS adj_close) FROM read_parquet('{f}')) "
                f"TO '{tmp_path / 'b.parquet'}' (FORMAT PARQUET)")
    con.close()
    shutil.move(tmp_path / "b.parquet", f)
    rc, out = _run(capsys, art, "--warehouse", altered)
    assert rc != 0
    assert "data_hash differs" in out and "SPY" in out                     # said before any table is compared
    assert "every table matches" not in out


def test_a_changed_table_value_is_named_with_its_largest_difference(made, capsys, tmp_path):
    art, export = made
    copy = tmp_path / "art"
    shutil.copytree(art, copy)
    s = pd.read_parquet(copy / "summary.parquet")
    s.loc[0, "var"] = s.loc[0, "var"] + 1e-6
    s.to_parquet(copy / "summary.parquet", index=False)
    rc, out = _run(capsys, copy, "--warehouse", export)
    assert rc == 1
    assert "summary" in out and "var" in out and "largest difference" in out and "1e-06" in out


def test_a_difference_within_1e_9_is_a_match(made, capsys, tmp_path):
    art, export = made
    copy = tmp_path / "art"
    shutil.copytree(art, copy)
    s = pd.read_parquet(copy / "summary.parquet")
    s.loc[0, "var"] = s.loc[0, "var"] + 1e-12
    s.to_parquet(copy / "summary.parquet", index=False)
    rc, out = _run(capsys, copy, "--warehouse", export)
    assert rc == 0, out


def _with_manifest(art, tmp_path, **changes):
    copy = tmp_path / "art"
    shutil.copytree(art, copy)
    m = json.loads((copy / "manifest.json").read_text())
    m.update(changes)
    (copy / "manifest.json").write_text(json.dumps(m))
    return copy


def test_a_different_lock_or_code_is_refused_unless_allowed(made, capsys, tmp_path):
    art, export = made
    copy = _with_manifest(art, tmp_path, lock_hash="0" * 64, code_sha="deadbeef")
    rc, out = _run(capsys, copy, "--warehouse", export)
    assert rc == 3 and "lock" in out and "deadbeef" in out and "--allow-drift" in out
    rc, out = _run(capsys, copy, "--warehouse", export, "--allow-drift")
    assert rc == 0 and "WARNING" in out


def test_a_kind_that_cannot_be_regenerated_is_refused_and_says_why(made, capsys, tmp_path):
    art, export = made
    rc, out = _run(capsys, _with_manifest(art, tmp_path, kind="signals.daily"), "--warehouse", export)
    assert rc == 2 and "signal documents" in out


def test_a_read_no_export_holds_is_refused_by_name(made, capsys, tmp_path):
    art, export = made
    from ohcamel_quant.jobs.artifacts import read_reads

    reads = [*read_reads(art), {"name": "market.quotes{...}", "sha256": "a" * 64, "source": "alpaca",
                                "regenerable": False}]
    rc, out = _run(capsys, _with_manifest(art, tmp_path, reads=reads), "--warehouse", export)
    assert rc == 2 and "market.quotes" in out and "alpaca" in out


def test_a_null_data_hash_is_refused(made, capsys, tmp_path):
    art, export = made
    rc, out = _run(capsys, _with_manifest(art, tmp_path, data_hash=None, data_hash_note="not all reads recorded"),
                   "--warehouse", export)
    assert rc == 2 and "not all reads recorded" in out


def test_regenerate_writes_nothing_into_the_artifact_or_the_export(made, capsys):
    art, export = made
    before = (_snapshot(art), _snapshot(export), _snapshot(art.parent.parent))
    assert _run(capsys, art, "--warehouse", export)[0] == 0
    assert (_snapshot(art), _snapshot(export), _snapshot(art.parent.parent)) == before


def test_the_tools_script_runs_the_same_check(made):
    art, export = made
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "regenerate.py"), str(art), "--warehouse", str(export)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "every table matches" in r.stdout
