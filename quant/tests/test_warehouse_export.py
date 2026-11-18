"""Task 3.3: the nightly warehouse export -- EXPORT DATABASE from a read-only connection, verified, renamed into
place -- and the restore that rebuilds a warehouse from it."""

from __future__ import annotations

import json

import duckdb
import pytest

from ohcamel_quant.warehouse import export as E
from ohcamel_quant.warehouse.db import migrate, migration_files
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse


@pytest.fixture(scope="module")
def wh(tmp_path_factory):
    return build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "warehouse.duckdb")


def _counts(path):
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = [t for (t,) in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]
        return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def test_the_export_is_a_dated_directory_with_every_table_and_a_verified_record(wh, tmp_path):
    out = E.export_warehouse(wh, tmp_path / "warehouse", day="2026-11-17")
    assert out == tmp_path / "warehouse" / "2026-11-17" and out.is_dir()
    assert (out / "schema.sql").is_file() and (out / "load.sql").is_file()
    record = json.loads((out / E.RECORD).read_text())
    assert record["tables"] == _counts(wh)                               # every table, every row, counted back
    assert record["schema_version"] == max(n for n, _ in migration_files())
    assert record["duckdb"] == duckdb.__version__ and record["day"] == "2026-11-17"
    assert not list((tmp_path / "warehouse").glob("*.part*"))             # no half-written directory left


def test_the_source_is_opened_read_only_and_not_changed(wh, tmp_path, monkeypatch):
    opened, real = [], E._connect
    monkeypatch.setattr(E, "_connect", lambda path, **kw: (opened.append((path, kw["read_only"])), real(path, **kw))[1])
    before = wh.stat().st_mtime_ns, wh.stat().st_size
    E.export_warehouse(wh, tmp_path / "w", day="2026-11-17")
    assert (wh.stat().st_mtime_ns, wh.stat().st_size) == before
    assert opened == [(wh, True)]


def test_a_failed_verification_leaves_no_dated_directory_and_keeps_yesterdays(wh, tmp_path, monkeypatch):
    root = tmp_path / "warehouse"
    good = E.export_warehouse(wh, root, day="2026-11-16")
    monkeypatch.setattr(E, "_count_export", lambda d, tables: {t: -1 for t in tables})
    with pytest.raises(E.ExportFailed, match="row count"):
        E.export_warehouse(wh, root, day="2026-11-17")
    assert sorted(p.name for p in root.iterdir()) == ["2026-11-16"] and good.is_dir()


def test_a_second_export_on_one_day_replaces_the_first_only_once_verified(wh, tmp_path):
    root = tmp_path / "warehouse"
    E.export_warehouse(wh, root, day="2026-11-17")
    E.export_warehouse(wh, root, day="2026-11-17")
    assert sorted(p.name for p in root.iterdir()) == ["2026-11-17"]


def test_restore_rebuilds_the_warehouse_with_its_migrations_version(wh, tmp_path):
    out = E.export_warehouse(wh, tmp_path / "warehouse", day="2026-11-17")
    target = tmp_path / "restored" / "warehouse.duckdb"
    E.restore_export(out, target)
    assert _counts(target) == _counts(wh)
    con = duckdb.connect(str(target))
    try:
        latest = max(n for n, _ in migration_files())
        assert migrate(con) == latest                                     # the runner sees the right version
        restored = con.execute(CONSTRAINTS).fetchall()
    finally:
        con.close()
    src = duckdb.connect(str(wh), read_only=True)
    try:
        assert restored == src.execute(CONSTRAINTS).fetchall() and restored    # every key and NOT NULL came back
    finally:
        src.close()


CONSTRAINTS = ("SELECT table_name, constraint_type, constraint_column_names FROM duckdb_constraints() "
               "ORDER BY table_name, constraint_type, constraint_column_names::VARCHAR")


def test_restore_never_overwrites_an_existing_file_unless_told_and_then_keeps_it(wh, tmp_path):
    out = E.export_warehouse(wh, tmp_path / "warehouse", day="2026-11-17")
    target = tmp_path / "live.duckdb"
    target.write_bytes(b"the live warehouse")
    with pytest.raises(E.ExportFailed, match="exists"):
        E.restore_export(out, target)
    assert target.read_bytes() == b"the live warehouse"
    E.restore_export(out, target, replace=True, stamp="20261117T070000Z")
    assert (tmp_path / "live.duckdb.pre-restore-20261117T070000Z").read_bytes() == b"the live warehouse"
    assert _counts(target) == _counts(wh)


def test_restore_refuses_an_export_without_its_record(wh, tmp_path):
    out = E.export_warehouse(wh, tmp_path / "warehouse", day="2026-11-17")
    (out / E.RECORD).unlink()
    with pytest.raises(E.ExportFailed, match="not a verified export"):
        E.restore_export(out, tmp_path / "x.duckdb")


def test_the_cli_exports_and_restores(wh, tmp_path, capsys):
    from ohcamel_quant.warehouse.__main__ import main

    assert main(["export", str(wh), str(tmp_path / "w"), "--date", "2026-11-17"]) == 0
    assert "2026-11-17" in capsys.readouterr().out
    assert main(["restore", str(tmp_path / "w" / "2026-11-17"), str(tmp_path / "r.duckdb")]) == 0
    assert _counts(tmp_path / "r.duckdb") == _counts(wh)


# --- the export waits for a warehouse writer that is running (fix round 1) ------------------------------------

def _running(tmp_path, kind):
    from ohcamel_quant.jobs.db import connect, utcnow
    from ohcamel_quant.jobs.queue import claim_next, enqueue

    db = tmp_path / "jobs.sqlite"
    conn = connect(db)
    enqueue(conn, kind=kind, params={}, priority=2, mem_class="S", heavy=False, submitted_by="schedule:t",
            now=utcnow())
    job = claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=utcnow())
    conn.close()
    return db, job


def test_the_export_waits_for_a_running_ingest_then_fails_loudly(wh, tmp_path):
    db, job = _running(tmp_path, "ingest.bars_daily")
    with pytest.raises(E.ExportFailed, match=f"ingest.bars_daily {job.id}"):
        E.export_warehouse(wh, tmp_path / "w", day="2026-11-17", jobs_db=db, wait_s=0.3, poll_s=0.1)
    assert not (tmp_path / "w").exists() or not any((tmp_path / "w").iterdir())


def test_the_export_goes_ahead_once_the_writer_finishes(wh, tmp_path, monkeypatch):
    db, _ = _running(tmp_path, "ingest.sec_facts")
    answers = iter([[("ingest.sec_facts", "X")], []])
    monkeypatch.setattr(E, "_writers_running", lambda path: next(answers))
    out = E.export_warehouse(wh, tmp_path / "w", day="2026-11-17", jobs_db=db, wait_s=5, poll_s=0.01)
    assert (out / E.RECORD).is_file()


def test_a_job_that_does_not_write_the_warehouse_does_not_hold_the_export(wh, tmp_path):
    db, _ = _running(tmp_path, "ingest.fred")                       # writes an artifact, never the warehouse
    out = E.export_warehouse(wh, tmp_path / "w", day="2026-11-17", jobs_db=db, wait_s=0.2, poll_s=0.1)
    assert (out / E.RECORD).is_file()
    out = E.export_warehouse(wh, tmp_path / "w2", day="2026-11-17", jobs_db=tmp_path / "absent.sqlite")
    assert (out / E.RECORD).is_file()                               # no queue yet: nothing can be writing
