"""The warehouse file (compute plan II.5, as amended 2026-10-06): schema,
migrations, and the lock-aware read-only / read-write connections."""

from __future__ import annotations

import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date

import duckdb
import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.warehouse.db import (
    WarehouseUnavailable,
    migrate,
    open_ro,
    open_rw,
    warehouse_path,
)

# Contract II.5 verbatim (TEXT is reported as VARCHAR by DuckDB).
V, D, F, T, INT = "VARCHAR", "DATE", "DOUBLE", "TIMESTAMP", "INTEGER"
EXPECTED_COLUMNS = {
    "universe_members": [("universe", V), ("ticker", V), ("name", V), ("added", D), ("source", V),
                         ("survivorship", V)],
    "bars_daily": [("ticker", V), ("date", D), ("open", F), ("high", F), ("low", F), ("close", F),
                   ("adj_close", F), ("volume", F), ("source", V), ("fetched_at", T)],
    "bars_minute": [("ticker", V), ("ts", T), ("open", F), ("high", F), ("low", F), ("close", F),
                    ("volume", F), ("source", V)],
    "fred": [("series", V), ("date", D), ("value", F), ("fetched_at", T)],
    "factors": [("dataset", V), ("factor", V), ("date", D), ("value", F)],
    "option_snapshots": [("underlying", V), ("asof", D), ("expiry", D), ("strike", F), ("cp", V),
                         ("bid", F), ("ask", F), ("last", F), ("volume", F), ("open_interest", F),
                         ("source", V),
                         # 003_option_quotes.sql (completion task 3.4)
                         ("spot", F), ("quote_ts", T), ("vendor_iv", F), ("pull", V), ("quote_ts_source", V)],
    "sec_facts": [("cik", V), ("ticker", V), ("tag", V), ("unit", V), ("period_start", D),
                  ("period_end", D), ("filed", D), ("form", V), ("value", F)],
    "holdings_13f": [("cik", V), ("filer", V), ("accession", V), ("form", V), ("period", D), ("filed", D),
                     ("cusip", V), ("put_call", V), ("issuer", V), ("title", V), ("ticker", V),
                     ("shares", F), ("shares_type", V), ("value_usd", F), ("weight", F)],
    "ingest_log": [("dataset", V), ("key", V), ("ran_at", T), ("rows", INT), ("status", V), ("detail", V),
                   ("data_asof", D)],
    # 002_raw_bars.sql (completion task 3.2): raw bars, the actions that adjust them, and every revision
    "bars_raw": [("ticker", V), ("date", D), ("open", F), ("high", F), ("low", F), ("close", F),
                 ("volume", F), ("source", V)],
    "corp_actions": [("ticker", V), ("ex_date", D), ("kind", V), ("ratio", F), ("cash", F), ("source", V),
                     ("fetched_at", T)],
    "corp_action_revisions": [("ticker", V), ("ex_date", D), ("kind", V), ("old_ratio", F), ("old_cash", F),
                              ("new_ratio", F), ("new_cash", F), ("source", V), ("seen_at", T)],
}
EXPECTED_PKS = {
    "universe_members": ["universe", "ticker"],
    "bars_daily": ["ticker", "date"],
    "bars_minute": ["ticker", "ts"],
    "fred": ["series", "date"],
    "factors": ["dataset", "factor", "date"],
    "option_snapshots": ["underlying", "asof", "expiry", "strike", "cp", "pull"],
    "sec_facts": ["cik", "tag", "unit", "period_start", "period_end", "filed"],
    "holdings_13f": ["accession", "cusip", "put_call"],
    "bars_raw": ["ticker", "date"],
    "corp_actions": ["ticker", "ex_date", "kind"],
}


def test_open_rw_creates_the_contract_schema(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name <> 'schema_version' "
            "ORDER BY table_name, ordinal_position").fetchall()
        pks = con.execute(
            "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
            "WHERE constraint_type = 'PRIMARY KEY'").fetchall()
    got: dict[str, list[tuple[str, str]]] = {}
    for table, col, typ in rows:
        got.setdefault(table, []).append((col, typ))
    assert got == EXPECTED_COLUMNS
    assert {t: list(c) for t, c in pks} == EXPECTED_PKS  # ingest_log has no key (append-only)


def test_asof_column_needs_quoting(tmp_path):
    # asof is DuckDB's ASOF join keyword: unquoted it does not parse, so every SQL
    # that names option_snapshots.asof writes "asof" (II.5 as amended).
    with pytest.raises(duckdb.ParserException):
        duckdb.connect().execute("CREATE TABLE t (asof DATE)")
    with open_rw(tmp_path / "w.duckdb") as con:
        con.execute("INSERT INTO option_snapshots (underlying, \"asof\", expiry, strike, cp, pull) "
                    "VALUES ('SPY', DATE '2024-06-07', DATE '2024-06-21', 530, 'P', '1620')")
        assert con.execute('SELECT underlying, max("asof") FROM option_snapshots GROUP BY underlying'
                           ).fetchall() == [("SPY", date(2024, 6, 7))]


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        first = migrate(con)
        assert migrate(con) == first == 3  # 001_init.sql, 002_raw_bars.sql, 003_option_quotes.sql
        assert con.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 3


def test_002_on_a_version_1_warehouse_adds_tables_and_leaves_bars_daily_alone(tmp_path):
    """The live file is at version 1 with years of bars: 002 must only add (no rewrite, no long lock)."""
    from ohcamel_quant.warehouse.db import migration_files

    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TIMESTAMP NOT NULL)")
    for stmt in migration_files()[0][1].read_text().split(";"):
        body = "\n".join(x for x in stmt.splitlines() if not x.strip().startswith("--")).strip()
        if body:
            con.execute(body)
    con.execute("INSERT INTO schema_version VALUES (1, now())")
    con.execute("INSERT INTO bars_daily VALUES ('SPY', DATE '2026-11-16', 1, 2, 0.5, 1.5, 1.4, 10, 'yahoo', now())")
    before = con.execute("SELECT * FROM bars_daily").fetchall()
    con.close()
    with open_rw(path) as con:
        assert con.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 3
        assert con.execute("SELECT * FROM bars_daily").fetchall() == before
        assert con.execute("SELECT count(*) FROM bars_raw").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM corp_actions").fetchone()[0] == 0



def _at_version(path, version):
    """A warehouse file built by the migrations up to ``version`` only (the live file before a deploy)."""
    from ohcamel_quant.warehouse.db import _statements, migration_files

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TIMESTAMP NOT NULL)")
    for number, f in migration_files():
        if number <= version:
            for stmt in _statements(f.read_text()):
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, now())", [number])
    return con


OLD_ROWS = [("SPY", date(2026, 11, 13), date(2026, 11, 20), 750.0, "C", 5.1, 5.2, 5.15, 10.0, 100.0, "cboe"),
            ("SPY", date(2026, 11, 13), date(2026, 11, 20), 750.0, "P", 4.9, 5.0, 4.95, 12.0, 90.0, "cboe"),
            ("QQQ", date(2026, 11, 16), date(2026, 11, 20), 600.0, "C", 7.0, 7.2, 7.1, 3.0, 40.0, "cboe")]


def test_003_on_a_version_2_warehouse_keeps_every_row_as_the_after_close_pull(tmp_path):
    """Task 3.4: the primary key gains `pull`. DuckDB cannot alter a key in place, so 003 renames the table and
    creates the new one, and open_rw moves the rows one snapshot day per transaction (a single-transaction copy of
    a few million rows does not fit the writer's 256 MB). Old rows keep pull '1620' and NULL in the new columns."""
    path = tmp_path / "w.duckdb"
    con = _at_version(path, 2)
    con.executemany("INSERT INTO option_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", OLD_ROWS)
    con.close()
    with open_rw(path) as con:
        assert con.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 3
        got = con.execute('SELECT underlying, "asof", expiry, strike, cp, bid, ask, last, volume, open_interest, '
                          "source, spot, quote_ts, vendor_iv, pull, quote_ts_source FROM option_snapshots "
                          'ORDER BY "asof", underlying, cp').fetchall()
        assert got == [(*r, None, None, None, "1620", None) for r in OLD_ROWS]
        tables = {t for (t,) in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        assert "option_snapshots_v2" not in tables
        # the same contract in the 15:45 pull is a second row; the same pull twice is refused
        con.execute("INSERT INTO option_snapshots (underlying, \"asof\", expiry, strike, cp, pull) "
                    "VALUES ('SPY', DATE '2026-11-13', DATE '2026-11-20', 750, 'C', '1545')")
        with pytest.raises(duckdb.ConstraintException):
            con.execute("INSERT INTO option_snapshots (underlying, \"asof\", expiry, strike, cp, pull) "
                        "VALUES ('SPY', DATE '2026-11-13', DATE '2026-11-20', 750, 'C', '1620')")


def test_an_interrupted_move_resumes_on_the_next_open(tmp_path):
    """The move commits one snapshot day at a time: killed half way, the next open_rw copies the days still
    missing (never one twice) and only then drops the old table."""
    path = tmp_path / "w.duckdb"
    con = _at_version(path, 2)
    con.executemany("INSERT INTO option_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", OLD_ROWS)
    from ohcamel_quant.warehouse.db import _statements, migration_files

    for stmt in _statements(dict(migration_files())[3].read_text()):     # 003 committed, the move not started
        con.execute(stmt)
    con.execute("INSERT INTO schema_version VALUES (3, now())")
    con.execute("INSERT INTO option_snapshots SELECT *, NULL, NULL, NULL, '1620', NULL FROM option_snapshots_v2 "
                "WHERE \"asof\" = DATE '2026-11-13'")                    # the first day moved, then the kill
    con.close()
    with open_rw(path) as con:
        assert con.execute("SELECT count(*), count(DISTINCT (underlying, \"asof\", expiry, strike, cp)) "
                           "FROM option_snapshots").fetchone() == (3, 3)
        tables = {t for (t,) in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        assert "option_snapshots_v2" not in tables


def test_corp_actions_refuses_the_same_action_twice(tmp_path):
    with open_rw(tmp_path / "w.duckdb") as con:
        row = "('AAPL', DATE '2020-08-31', 'split', 4.0, NULL, 'yahoo', now())"
        con.execute(f"INSERT INTO corp_actions VALUES {row}")
        with pytest.raises(duckdb.ConstraintException):
            con.execute(f"INSERT INTO corp_actions VALUES {row}")
        con.execute("INSERT INTO corp_actions VALUES ('AAPL', DATE '2020-08-31', 'dividend', NULL, 0.82, "
                    "'yahoo', now())")  # a different kind on the same day is a different action
        assert con.execute("SELECT count(*) FROM corp_actions").fetchone()[0] == 2


def test_open_ro_refuses_writes_and_missing_files(tmp_path):
    path = tmp_path / "w.duckdb"
    with pytest.raises(WarehouseUnavailable, match="does not exist"):
        with open_ro(path):
            pass
    with open_rw(path):
        pass
    with open_ro(path) as con:
        with pytest.raises(duckdb.Error):
            con.execute("INSERT INTO fred VALUES ('DGS10', DATE '2024-01-02', 3.95, NULL)")


def test_unconfigured_warehouse(monkeypatch, tmp_path):
    monkeypatch.setenv("OHCAMEL_QUANT_WAREHOUSE_PATH", "  ")
    s = Settings(data_dir=tmp_path)
    assert s.warehouse_path is None and warehouse_path(s) is None
    with pytest.raises(WarehouseUnavailable, match="not configured"):
        with open_ro(settings=s):
            pass


@contextmanager
def hold_writer(path, tmp_path):
    """Another process holding the read-write lock, as the worker does mid-ingest."""
    ready = tmp_path / "writer.ready"
    code = ("import duckdb, pathlib, sys, time; con = duckdb.connect(sys.argv[1]); "
            "pathlib.Path(sys.argv[2]).touch(); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(path), str(ready)])
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert time.monotonic() < deadline, "writer subprocess never opened the file"
            time.sleep(0.05)
        yield
    finally:
        proc.kill()
        proc.wait()


def test_open_ro_gives_up_on_writer_lock(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path):
        pass
    with hold_writer(path, tmp_path):
        t0 = time.monotonic()
        with pytest.raises(WarehouseUnavailable, match="locked"):
            with open_ro(path, timeout_s=0.5):
                pass
        assert time.monotonic() - t0 < 5  # bounded wait, not a hang
