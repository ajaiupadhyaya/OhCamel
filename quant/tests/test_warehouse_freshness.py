"""Freshness per dataset and the endpoint GET /api/warehouse/freshness (compute
plan C6, Review Focus 4: a missed schedule turns a dataset stale), and the
schedule entries handed to Lane B's schedules.yaml."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.config import get_settings
from ohcamel_quant.warehouse import __main__ as cli
from ohcamel_quant.warehouse import freshness
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.ingest import HANDLERS
from ohcamel_quant.warehouse.ingest.base import log_row
from ohcamel_quant.warehouse.ingest.options import OPTION_UNDERLYINGS
from ohcamel_quant.warehouse.schedules import KIND_DATASET, MAX_AGE, SCHEDULES

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # Tue 08:00 EDT


def utc(*a):
    return datetime(*a)  # naive UTC, the warehouse's TIMESTAMP convention


@pytest.fixture
def wh(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        # bars_daily: Mon's session, last ok run Mon 22:30 UTC (18:30 EDT) -> fresh
        log_row(con, "bars_daily", "SPY", "ok", 1, "{}", date(2026, 10, 5), ran_at=utc(2026, 10, 5, 22, 30))
        log_row(con, "bars_daily", "QQQ", "failed", 0, "{}", date(2026, 10, 2), ran_at=utc(2026, 10, 5, 22, 31))
        log_row(con, "bars_daily", "*", "partial", 1, "{}", date(2026, 10, 5), ran_at=utc(2026, 10, 5, 22, 32))
        # option_snapshots: last good Fri 10-02; Mon's 16:20 run missed -> stale (due Mon 19:20 EDT passed)
        log_row(con, "option_snapshots", "SPY", "ok", 9, "{}", date(2026, 10, 2), ran_at=utc(2026, 10, 2, 20, 21))
        log_row(con, "option_snapshots", "*", "ok", 9, "{}", date(2026, 10, 2), ran_at=utc(2026, 10, 2, 20, 22))
        # fred: last ok Sun 11:00 UTC; Tue 12:00 UTC is 49 h later > 30 h -> stale
        log_row(con, "fred", "*", "ok", 5, "{}", date(2026, 10, 2), ran_at=utc(2026, 10, 4, 11, 0))
        # factors: last ok Sat 10:00 UTC, 74 h < 204 h -> fresh
        log_row(con, "factors", "*", "ok", 5, "{}", date(2026, 8, 31), ran_at=utc(2026, 10, 3, 10, 0))
    return path


def test_dataset_freshness_rules(wh):
    with open_ro(wh) as con:
        f = freshness.dataset_freshness(con, NOW)["datasets"]
    assert list(f) == list(freshness.DATASETS)
    b = f["bars_daily"]
    assert (b["data_asof"], b["stale"], b["keys"], b["keys_behind"], b["keys_failed"], b["last_status"]) == (
        "2026-10-05", False, 2, 1, 1, "partial")
    assert f["option_snapshots"]["stale"] is True and f["option_snapshots"]["data_asof"] == "2026-10-02"
    assert f["fred"]["stale"] is True and f["factors"]["stale"] is False
    for never in ("universe_members", "bars_minute", "sec_facts", "holdings_13f"):
        assert f[never]["data_asof"] is None and f[never]["stale"] is True and f[never]["last_status"] is None


def test_freshness_marks_missed_session_stale(wh):
    # Wed 2026-10-07 02:00 UTC = Tue 22:00 EDT: Tue's bars were due 21:30 and the last good is Mon -> stale
    # unless a run after Tue 21:30 confirmed nothing newer (holiday rule) -- none did.
    with open_ro(wh) as con:
        f = freshness.dataset_freshness(con, datetime(2026, 10, 7, 2, 0, tzinfo=UTC))["datasets"]
    assert f["bars_daily"]["stale"] is True


def client(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OHCAMEL_QUANT_WAREHOUSE_PATH", raising=False)
    else:
        monkeypatch.setenv("OHCAMEL_QUANT_WAREHOUSE_PATH", str(value))
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    get_settings.cache_clear()


def test_endpoint_503_when_unset_or_missing(monkeypatch, tmp_path):
    r = client(monkeypatch, None).get("/api/warehouse/freshness")
    assert r.status_code == 503 and r.json() == {
        "error": "warehouse_unavailable", "configured": False,
        "detail": "warehouse not configured (OHCAMEL_QUANT_WAREHOUSE_PATH unset)"}
    r = client(monkeypatch, tmp_path / "absent.duckdb").get("/api/warehouse/freshness")
    assert r.status_code == 503 and r.json()["configured"] is True and "does not exist" in r.json()["detail"]


def test_endpoint_reports_every_dataset(monkeypatch, wh):
    monkeypatch.setattr(freshness, "_now", lambda: NOW)
    r = client(monkeypatch, wh).get("/api/warehouse/freshness")
    assert r.status_code == 200
    j = r.json()
    assert set(j["datasets"]) == set(freshness.DATASETS) and j["now"] == NOW.isoformat()
    assert j["datasets"]["bars_daily"]["data_asof"] == "2026-10-05"
    assert j["provenance"][0]["source"] == "warehouse" and j["notes"]


def test_cli_freshness(monkeypatch, wh, capsys):
    monkeypatch.setattr(freshness, "_now", lambda: NOW)
    monkeypatch.setenv("OHCAMEL_QUANT_WAREHOUSE_PATH", str(wh))
    get_settings.cache_clear()
    assert cli.main(["freshness"]) == 0
    assert json.loads(capsys.readouterr().out)["datasets"]["fred"]["stale"] is True


# Lane B's B4 loader (jobs/schedules.py): _entry demands exactly these keys and
# parse_duration accepts only this form. Restated here because Lane C must not
# import ohcamel_quant.jobs.
B4_KEYS = {"name", "cron", "kind", "params", "priority", "mem_class", "heavy"}
B4_DURATION = re.compile(r"^(\d+)([smhd])$")


def _seconds(text):
    n, unit = B4_DURATION.match(text).groups()
    return int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def test_schedules_cover_every_handler():
    assert sorted(s["kind"] for s in SCHEDULES) == sorted(HANDLERS) and len(HANDLERS) == 8
    assert len({s["name"] for s in SCHEDULES}) == len(SCHEDULES)
    for s in SCHEDULES:
        assert set(s) == B4_KEYS  # no max_age or other extra key: load_schedules would raise
        assert s["mem_class"] in ("S", "M", "L") and s["priority"] == 2 and len(s["cron"].split()) == 5
        assert isinstance(s["heavy"], bool) and isinstance(s["params"], dict)
    assert set(MAX_AGE) == set(HANDLERS)
    for kind, dur in MAX_AGE.items():
        assert B4_DURATION.match(dur), f"{kind}: {dur!r} is not a parse_duration string"
        rule = freshness.FRESHNESS[KIND_DATASET[kind]]
        if rule["rule"] == "age":
            assert _seconds(dur) == rule["max_age_h"] * 3600  # same number as the endpoint
        else:
            assert _seconds(dur) == 78 * 3600  # covers Fri -> Mon (72 h) with slack
    # Plain scalars, lists and mappings only (pyyaml is Lane B's dependency, so JSON stands in):
    # the two objects paste into schedules.yaml unchanged.
    assert json.loads(json.dumps({"max_age": MAX_AGE, "schedules": SCHEDULES})) == {
        "max_age": MAX_AGE, "schedules": SCHEDULES}
    opt = next(s for s in SCHEDULES if s["kind"] == "ingest.option_snapshots")
    assert opt["cron"] == "20 16 * * 1-5"  # after 16:15 New York, session days
    assert opt["params"]["underlyings"] == list(OPTION_UNDERLYINGS)
    assert next(s for s in SCHEDULES if s["kind"] == "ingest.sec_facts")["heavy"] is True
