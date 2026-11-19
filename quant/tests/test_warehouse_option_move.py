"""Task 3.4 fix round 1: the option-table move of migration 003 is a step the owner runs off-hours
(``python -m ohcamel_quant.warehouse migrate``), and while it is incomplete every reader of the table refuses
instead of reading a part of it: vol.surface_history, the warehouse freshness the Machine shows, and the nightly
backup's option-snapshot copy (which then keeps the previous good parquet)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime

import duckdb
import pytest

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import surface_history
from ohcamel_quant.warehouse import freshness
from ohcamel_quant.warehouse.db import (
    _statements,
    migration_files,
    move_incomplete,
    open_ro,
    open_rw,
)

ROWS = [("SPY", date(2026, 11, 13), date(2026, 11, 20), 750.0, "C", 5.1, 5.2, 5.15, 10.0, 100.0, "cboe"),
        ("SPY", date(2026, 11, 16), date(2026, 11, 20), 750.0, "P", 4.9, 5.0, 4.95, 12.0, 90.0, "cboe"),
        ("QQQ", date(2026, 11, 17), date(2026, 11, 20), 600.0, "C", 7.0, 7.2, 7.1, 3.0, 40.0, "cboe")]


def _at_version(path, version):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TIMESTAMP NOT NULL)")
    for number, f in migration_files():
        if number <= version:
            for stmt in _statements(f.read_text()):
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, now())", [number])
    return con


def killed_after_one_day(path):
    """A version-2 file of three snapshot days, 003 applied and one day moved: a worker killed mid-move."""
    con = _at_version(path, 2)
    con.executemany("INSERT INTO option_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ROWS)
    for stmt in _statements(dict(migration_files())[3].read_text()):
        con.execute(stmt)
    con.execute("INSERT INTO schema_version VALUES (3, now())")
    con.execute("INSERT INTO option_snapshots SELECT *, NULL, NULL, NULL, '1620', NULL FROM option_snapshots_v2 "
                "WHERE \"asof\" = DATE '2026-11-13'")
    con.close()


def ctx(market, path):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market, _warehouse=duckdb.connect(str(path), read_only=True))


def test_a_reader_refuses_while_the_move_is_incomplete(market, tmp_path, monkeypatch):
    path = tmp_path / "w.duckdb"
    killed_after_one_day(path)
    with open_ro(path) as con:
        assert move_incomplete(con)
        with pytest.raises(surface_history.DataUnavailable, match="migrate"):
            surface_history._pending(con, set())
        f = freshness.dataset_freshness(con, datetime(2026, 11, 18, 23, 0, tzinfo=UTC))["datasets"]["option_snapshots"]
    assert f["migrating"] is True and f["stale"] is True
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    spec = surface_history.run({}, ctx(market, path))                 # a refused run, not a crash
    assert spec.verdict == "INSUFFICIENT DATA" and any("migrate" in n for n in spec.notes)


def test_surface_history_on_a_file_before_migration_3_refuses_cleanly(market, tmp_path, monkeypatch):
    path = tmp_path / "w.duckdb"
    con = _at_version(path, 2)
    con.executemany("INSERT INTO option_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ROWS)
    con.close()
    with open_ro(path) as con:
        with pytest.raises(surface_history.DataUnavailable, match="migration 3"):
            surface_history._pending(con, set())
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    spec = surface_history.run({}, ctx(market, path))
    assert spec.verdict == "INSUFFICIENT DATA" and any("migration 3" in n for n in spec.notes)


def _snapshot_script():
    text = (REPO_ROOT / "deploy" / "backup.sh").read_text()
    return re.search(r"^snapshot_script='(.*?)^'$", text, re.M | re.S).group(1)


def test_the_backup_keeps_the_previous_parquet_while_the_move_is_incomplete(tmp_path):
    """deploy/backup.sh's option-snapshot step, run as the backup runs it (python -c SCRIPT SRC DIR DEST): on a
    half-moved file it exits non-zero, so the step is named failed, and yesterday's good parquet stays."""
    path, out = tmp_path / "w.duckdb", tmp_path / "backups"
    out.mkdir()
    killed_after_one_day(path)
    (out / "option-snapshots-2026-11-19.parquet").write_bytes(b"yesterday's good copy")
    r = subprocess.run([sys.executable, "-c", _snapshot_script(), str(path), str(out),
                        "option-snapshots-2026-11-19.parquet"], capture_output=True, text=True)
    assert r.returncode != 0 and "migrate" in r.stderr
    assert (out / "option-snapshots-2026-11-19.parquet").read_bytes() == b"yesterday's good copy"
    assert not (out / "option-snapshots-2026-11-19.parquet.part").exists()
    # once the move is complete the same step copies every row
    from ohcamel_quant.warehouse.__main__ import main

    assert main(["migrate", str(path)]) == 0
    r = subprocess.run([sys.executable, "-c", _snapshot_script(), str(path), str(out),
                        "option-snapshots-2026-11-19.parquet"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    n = duckdb.connect().execute("SELECT count(*) FROM read_parquet(?)",
                                 [str(out / "option-snapshots-2026-11-19.parquet")]).fetchone()[0]
    assert n == len(ROWS)


def test_the_migrate_command_completes_the_move_and_says_what_it_moved(tmp_path, capsys):
    from ohcamel_quant.warehouse.__main__ import main

    path = tmp_path / "w.duckdb"
    killed_after_one_day(path)
    assert main(["migrate", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == 3 and out["days_moved"] == 2 and out["rows_moved"] == 2 and out["seconds"] >= 0
    with open_ro(path) as con:
        assert not move_incomplete(con)
        assert con.execute("SELECT count(*) FROM option_snapshots").fetchone()[0] == len(ROWS)
    assert main(["migrate", str(path)]) == 0                            # again: nothing left to do
    assert json.loads(capsys.readouterr().out)["days_moved"] == 0


def test_the_migrate_command_reads_the_configured_path(tmp_path, capsys, monkeypatch):
    from ohcamel_quant.config import get_settings
    from ohcamel_quant.warehouse.__main__ import main

    path = tmp_path / "w.duckdb"
    killed_after_one_day(path)
    monkeypatch.setenv("OHCAMEL_QUANT_WAREHOUSE_PATH", str(path))
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    try:
        assert main(["migrate"]) == 0
    finally:
        get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    assert json.loads(capsys.readouterr().out)["days_moved"] == 2
    with open_rw(path) as con:                                            # and a writer finds it done
        assert not move_incomplete(con)
    assert os.path.exists(path)
