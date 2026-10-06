"""The shared ingest loop: per-key logging, last-good data_asof on failure,
partial/failed/cancelled summaries, the ArtifactSpec-shaped result, and the
rule that ingest never touches ctx.warehouse (compute plan II.2/II.3/II.5)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse import __main__ as cli
from ohcamel_quant.warehouse import ingest
from ohcamel_quant.warehouse.db import WarehouseUnavailable, open_ro, open_rw
from ohcamel_quant.warehouse.ingest.base import (
    IngestCancelled,
    IngestFailed,
    KeyState,
    LocalContext,
    Written,
    log_row,
    run_ingest,
)


class FakeContext(LocalContext):
    def __init__(self, market, cancel_after=None):
        super().__init__(market=market)
        self.cancel_after = cancel_after
        self.calls = 0
        self.progress_log = []

    def progress(self, fraction, message):
        self.progress_log.append((fraction, message))

    def cancelled(self):
        self.calls += 1
        return self.cancel_after is not None and self.calls > self.cancel_after


def ctx_for(tmp_path, **kw):
    return FakeContext(MarketData(Settings(offline=True, data_dir=tmp_path,
                                           warehouse_path=tmp_path / "w.duckdb")), **kw)


def read_state(con, keys):
    rows = con.execute("SELECT series, max(date) FROM fred WHERE list_contains(?, series) GROUP BY series",
                       [keys]).fetchall()
    return {k: KeyState(d) for k, d in rows}


def fetch_ok(key, state, settings):
    yield date(2024, 1, 3)


def write(con, key, state, day):
    con.execute("INSERT OR REPLACE INTO fred VALUES (?, ?, ?, NULL)", [key, day, 1.0])
    last = con.execute("SELECT max(date) FROM fred WHERE series = ?", [key]).fetchone()[0]
    return Written(1, last, {"source": "test-vendor"})


def run(ctx, keys, fetch=fetch_ok, w=write):
    return run_ingest(dataset="fred", keys=keys, ctx=ctx, read_state=read_state, fetch=fetch, write=w,
                      notes=["test note"])


def log(ctx):
    with open_ro(ctx.market.settings.warehouse_path) as con:
        return con.execute("SELECT key, status, rows, data_asof, detail FROM ingest_log ORDER BY ran_at, key").fetchall()


def test_all_keys_ok(tmp_path):
    ctx = ctx_for(tmp_path)
    spec = run(ctx, ["A", "B"])
    assert set(spec) == {"data_asof", "provenance", "notes", "survivorship", "tables"}
    assert spec["data_asof"] == "2024-01-03" and spec["survivorship"] is None
    assert spec["provenance"][0]["source"] == "test-vendor" and "test note" in spec["notes"]
    assert list(spec["tables"]["ingest_summary"]["status"]) == ["ok", "ok"]
    rows = log(ctx)
    assert [(k, s, d) for k, s, _, d, _ in rows] == [("A", "ok", date(2024, 1, 3)), ("B", "ok", date(2024, 1, 3)),
                                                    ("*", "ok", date(2024, 1, 3))]
    assert ctx.progress_log[-1][0] == 1.0 and len(ctx.progress_log) == 3  # one per key + done


def test_failed_key_keeps_last_good_data_asof(tmp_path):
    ctx = ctx_for(tmp_path)
    with open_rw(ctx.market.settings.warehouse_path) as con:  # B already holds 2024-01-02
        con.execute("INSERT INTO fred VALUES ('B', DATE '2024-01-02', 5.0, NULL)")

    def fetch(key, state, settings):
        if key == "B":
            raise DataUnavailable("vendor: HTTP 500")
        yield date(2024, 1, 3)

    spec = run(ctx, ["A", "B"], fetch=fetch)
    rows = {k: (s, d, det) for k, s, _, d, det in log(ctx)}
    assert rows["B"][:2] == ("failed", date(2024, 1, 2))  # last good, not today
    assert "vendor: HTTP 500" in rows["B"][2]
    assert rows["*"][0] == "partial"
    assert any("B: vendor: HTTP 500" in n for n in spec["notes"])


def test_all_failed_raises_after_logging(tmp_path):
    ctx = ctx_for(tmp_path)

    def fetch(key, state, settings):
        raise DataUnavailable("vendor down")
        yield  # pragma: no cover

    with pytest.raises(IngestFailed, match="all 2 keys failed"):
        run(ctx, ["A", "B"], fetch=fetch)
    assert [s for _, s, *_ in log(ctx)] == ["failed", "failed", "failed"]


def test_write_error_rolls_back_the_key(tmp_path):
    ctx = ctx_for(tmp_path)

    def bad_write(con, key, state, day):
        con.execute("INSERT INTO fred VALUES (?, ?, 1.0, NULL)", [key, day])
        raise ValueError("bad payload")

    with pytest.raises(IngestFailed):
        run(ctx, ["A"], w=bad_write)
    with open_ro(ctx.market.settings.warehouse_path) as con:
        assert con.execute("SELECT count(*) FROM fred").fetchone()[0] == 0  # rolled back
    assert "ValueError: bad payload" in log(ctx)[0][4]


def test_cancel_between_keys(tmp_path):
    ctx = ctx_for(tmp_path, cancel_after=1)
    with pytest.raises(IngestCancelled, match="after 1 of 2"):
        run(ctx, ["A", "B"])
    assert [(k, s) for k, s, *_ in log(ctx)] == [("A", "ok"), ("*", "cancelled")]


def test_ingest_never_reads_ctx_warehouse(tmp_path):
    ctx = ctx_for(tmp_path)
    with pytest.raises(AttributeError, match="must not read ctx.warehouse"):
        ctx.warehouse  # noqa: B018
    run(ctx, ["A"])  # the loop completes without touching it


def test_unconfigured_warehouse_refuses(tmp_path):
    ctx = FakeContext(MarketData(Settings(offline=True, data_dir=tmp_path)))
    with pytest.raises(WarehouseUnavailable):
        run(ctx, ["A"])


def test_cli_run_and_build_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(ingest.HANDLERS, "ingest.test",
                        lambda params, ctx: {"data_asof": "2024-01-03", "provenance": [], "notes": [params["a"]],
                                             "survivorship": None, "tables": {}})
    assert cli.main(["run", "ingest.test", "--params", '{"a": "x"}']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data_asof"] == "2024-01-03" and out["notes"] == ["x"] and "tables" not in out
    assert cli.main(["run", "ingest.nope"]) == 2
    path = tmp_path / "fx.duckdb"
    assert cli.main(["build-fixture", str(path)]) == 0
    with open_ro(path) as con:
        assert con.execute("SELECT count(DISTINCT ticker) FROM bars_daily").fetchone()[0] == 9  # fixtures/history


def test_log_row_signature(tmp_path):
    with open_rw(tmp_path / "w.duckdb") as con:
        log_row(con, "fred", "X", "ok", 3, "{}", date(2024, 1, 2))
        assert con.execute("SELECT rows, data_asof FROM ingest_log").fetchone() == (3, date(2024, 1, 2))
