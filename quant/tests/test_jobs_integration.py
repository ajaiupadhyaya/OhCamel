"""Integration of lanes A, B and C (ship plan Phase 1; compute plan II.2-II.5).

* Lane C's ingest HANDLERS run as Lane B jobs: registered, private, retried,
  their ``dict`` result adapted to an ``ArtifactSpec``.
* schedules.yaml carries Lane C's entries and max ages, plus the intraday
  minute-bar refresh (compute plan I.4 P9: "nightly and intraday").
* ``ops.selftest`` is on the API allow-list, with ``tag`` its only public param.
* ``JobContext.warehouse`` opens ``Settings.warehouse_path`` read-only.
* ``MarketData.returns`` reads the warehouse first only when its file exists.
* The heavy job paths run Lane A's kernels with the job's threads.
* The compose file points the API and the worker at /data/warehouse.duckdb.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
import yaml

from ohcamel_quant import kernels
from ohcamel_quant.config import Settings
from ohcamel_quant.data import fred, http
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.jobs.artifacts import ArtifactSpec
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.jobs.cron import fire_times, parse_cron
from ohcamel_quant.jobs.kinds import get_kind, load_handler, validate_params
from ohcamel_quant.jobs.schedules import load_schedules, parse_duration
from ohcamel_quant.warehouse import schedules as wh_schedules
from ohcamel_quant.warehouse.db import WarehouseUnavailable, open_rw
from ohcamel_quant.warehouse.ingest import HANDLERS

REPO = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")


def _ctx(market=None, threads=1):
    return JobContext(job_id="J", params={}, threads=threads, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=market)


# ------------------------------------------------------------ registry
@pytest.mark.parametrize("kind", sorted(HANDLERS))
def test_every_warehouse_kind_is_a_private_retried_job(kind):
    spec = get_kind(kind)
    entry = next(e for e in wh_schedules.SCHEDULES if e["kind"] == kind)
    assert (spec.mem_class, spec.heavy) == (entry["mem_class"], entry["heavy"])
    assert spec.public is False and spec.retries == 2
    assert callable(load_handler(spec))


@respx.mock
def test_a_warehouse_job_returns_an_artifact_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(
        200, text="observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,3.91\n"))
    market = MarketData(Settings(offline=False, data_dir=tmp_path, http_retries=0,
                                 warehouse_path=tmp_path / "w.duckdb", FRED_API_KEY=None))
    handler = load_handler(get_kind("ingest.fred_warehouse"))
    spec = handler({"series": "DGS10"}, _ctx(market))
    assert isinstance(spec, ArtifactSpec)
    assert spec.data_asof == "2024-01-03" and list(spec.tables) == ["ingest_summary"]
    assert spec.tables["ingest_summary"]["status"].tolist() == ["ok"]
    assert spec.provenance and spec.notes


# ----------------------------------------------------------- schedules
def test_schedules_yaml_carries_every_warehouse_entry_and_max_age():
    s = load_schedules()
    by_name = {e.name: e for e in s.entries}
    for want in wh_schedules.SCHEDULES:
        got = by_name[want["name"]]
        assert (got.cron, got.kind, got.params, got.priority, got.mem_class, got.heavy) == (
            want["cron"], want["kind"], want["params"], want["priority"], want["mem_class"], want["heavy"])
    for kind, age in wh_schedules.MAX_AGE.items():
        assert s.max_age_s[kind] == parse_duration(age)


def test_minute_bars_refresh_intraday_inside_the_session_only():
    s = load_schedules()
    intraday = [e for e in s.entries if e.kind == "ingest.bars_minute" and e.priority == 1]
    assert len(intraday) == 1
    e = intraday[0]
    assert (e.mem_class, e.heavy, e.params) == ("S", False, {})
    cron = parse_cron(e.cron)
    week = [date(2026, 10, 5) + timedelta(days=i) for i in range(7)]  # Monday .. Sunday
    fires = [t.astimezone(NY) for d in week for t in fire_times(cron, d)]
    assert {f.weekday() for f in fires} == {0, 1, 2, 3, 4}
    assert all((9, 30) < (f.hour, f.minute) <= (16, 0) for f in fires)


# ---------------------------------------------------------- allow-list
def test_selftest_is_public_with_tag_its_only_param():
    spec = get_kind("ops.selftest")
    assert spec.public and (spec.mem_class, spec.heavy) == ("S", False)
    validate_params(spec, {})
    validate_params(spec, {"tag": "smoke"})
    for bad in ({"exit_hard": True}, {"fail_if_exists": "/etc/hostname"}, {"sleep_s": 1}, {"tag": "x" * 65}):
        with pytest.raises(ValueError):
            validate_params(spec, bad)


# ----------------------------------------------------------- warehouse
def test_job_context_warehouse_opens_the_configured_file_read_only(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path):
        pass
    ctx = _ctx(MarketData(Settings(offline=True, data_dir=tmp_path, warehouse_path=path)))
    con = ctx.warehouse
    assert con.execute("SELECT count(*) FROM bars_daily").fetchone() == (0,)
    with pytest.raises(Exception, match="read-only"):
        con.execute("CREATE TABLE x (a INTEGER)")
    ctx.close()


def test_job_context_warehouse_unset_is_unavailable(tmp_path):
    ctx = _ctx(MarketData(Settings(offline=True, data_dir=tmp_path, warehouse_path=None)))
    with pytest.raises(WarehouseUnavailable):
        ctx.warehouse  # noqa: B018


def test_market_data_skips_a_configured_but_absent_warehouse(tmp_path, monkeypatch):
    from ohcamel_quant.warehouse import readers

    def boom(*a, **k):
        raise AssertionError("warehouse read attempted for an absent file")

    monkeypatch.setattr(readers, "warehouse_returns", boom)
    m = MarketData(Settings(offline=True, data_dir=tmp_path, warehouse_path=tmp_path / "absent.duckdb"))
    ds = m.returns(["SPY"])
    assert not any("warehouse" in str(p.detail.get("note", "")) for p in ds.provenance)


# ------------------------------------------------------------- kernels
PF = {"holdings": [{"ticker": "SPY", "weight": 0.5}, {"ticker": "TLT", "weight": 0.3},
                   {"ticker": "GLD", "weight": 0.2}], "notional": 2_000_000,
      "models": ["garch"], "window": 500, "refit_every": 60}


def test_risk_backtest_job_refits_on_the_garch_kernel(monkeypatch):
    from ohcamel_quant.jobs.handlers import api_heavy
    from ohcamel_quant.risk import backtest as bt

    seen = {}
    real = bt.rolling_forecasts

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(bt, "rolling_forecasts", spy)
    spec = api_heavy.risk_backtest(PF, _ctx())
    assert seen.get("use_kernels") is True
    assert spec.engine == kernels.engine_of("garch_fit")


def test_sweep_job_runs_cscv_with_the_jobs_threads(monkeypatch):
    from ohcamel_quant.backtest import validation as V
    from ohcamel_quant.jobs.handlers import api_heavy

    seen = {}
    real = V.cscv_pbo

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(V, "cscv_pbo", spy)
    params = {"strategy": "tsmom", "tickers": ["SPY", "TLT", "GLD"], "grid": {"lookback": [126, 252]},
              "spa_reps": 200, "n_partitions": 8}
    spec = api_heavy.backtest_sweep(params, _ctx(threads=2))
    assert seen.get("threads") == 2
    assert spec.engine == kernels.engine_of("backtest_weights")


CMP = {"tickers": ["SPY", "TLT", "GLD"], "start": "2018-01-01", "methods": ["min_variance", "hrp"],
       "window": 252, "rebalance": "Q", "cost_bps": 10}


def test_compare_job_offline_without_a_bill_rate_fails_naming_the_fallback():
    """No DGS3MO fixture is committed (no real data to commit offline), and Ken French RF is online only:
    the job never substitutes a rate; it fails saying the caller can supply risk_free_rate."""
    from ohcamel_quant.data.base import DataUnavailable
    from ohcamel_quant.jobs.handlers import api_heavy

    with pytest.raises(DataUnavailable) as e:
        api_heavy.portfolio_compare(CMP, _ctx())
    msg = str(e.value)
    assert "DGS3MO" in msg and "supply risk_free_rate explicitly" in msg


def test_compare_job_offline_runs_on_a_caller_supplied_rate():
    import json

    from ohcamel_quant.jobs.handlers import api_heavy

    spec = api_heavy.portfolio_compare({**CMP, "risk_free_rate": 0.03}, _ctx())
    payload = json.loads(spec.tables["result"]["payload"].iloc[0])
    assert payload["risk_free"]["source"] == "user" and payload["risk_free"]["annual"] == 0.03
    assert {s["method"] for s in payload["stats"]} == {"min_variance", "hrp", "equal_weight"}


# ------------------------------------------------------------- compose
@pytest.mark.parametrize("service", ["ohcamel-quant", "ohcamel-worker"])
def test_compose_points_the_api_and_worker_at_the_warehouse(service):
    compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
    env = compose["services"][service]["environment"]
    assert env["OHCAMEL_QUANT_WAREHOUSE_PATH"] == "/data/warehouse.duckdb"
