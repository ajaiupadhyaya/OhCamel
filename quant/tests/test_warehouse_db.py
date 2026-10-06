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
                         ("source", V)],
    "sec_facts": [("cik", V), ("ticker", V), ("tag", V), ("unit", V), ("period_start", D),
                  ("period_end", D), ("filed", D), ("form", V), ("value", F)],
    "holdings_13f": [("cik", V), ("filer", V), ("accession", V), ("form", V), ("period", D), ("filed", D),
                     ("cusip", V), ("put_call", V), ("issuer", V), ("title", V), ("ticker", V),
                     ("shares", F), ("shares_type", V), ("value_usd", F), ("weight", F)],
    "ingest_log": [("dataset", V), ("key", V), ("ran_at", T), ("rows", INT), ("status", V), ("detail", V),
                   ("data_asof", D)],
}
EXPECTED_PKS = {
    "universe_members": ["universe", "ticker"],
    "bars_daily": ["ticker", "date"],
    "bars_minute": ["ticker", "ts"],
    "fred": ["series", "date"],
    "factors": ["dataset", "factor", "date"],
    "option_snapshots": ["underlying", "asof", "expiry", "strike", "cp"],
    "sec_facts": ["cik", "tag", "unit", "period_start", "period_end", "filed"],
    "holdings_13f": ["accession", "cusip", "put_call"],
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
        con.execute("INSERT INTO option_snapshots (underlying, \"asof\", expiry, strike, cp) "
                    "VALUES ('SPY', DATE '2024-06-07', DATE '2024-06-21', 530, 'P')")
        assert con.execute('SELECT underlying, max("asof") FROM option_snapshots GROUP BY underlying'
                           ).fetchall() == [("SPY", date(2024, 6, 7))]


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        first = migrate(con)
        assert migrate(con) == first == 1  # one migration file, 001_init.sql
        assert con.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 1


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
