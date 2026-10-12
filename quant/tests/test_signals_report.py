"""desk.report (Task 16; design §4.2): the session's fills, each strategy's line,
what the desk refused, and the overlay's reading, read from the live engine at
16:30 New York.

Hermetic: the engine is a stdlib ``http.server`` on 127.0.0.1 serving canned
bodies shaped exactly as desk/desk.ml, desk/oms.ml and desk/intake.ml build
them (``/api/desk``'s ``fills``, ``/api/desk/tca``, ``/api/research``,
``/api/desk/sessions``). An unreachable engine is a port nothing listens on."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.jobs.kinds import REGISTRY
from ohcamel_quant.signals import report

FILL_TODAY = {
    "execution_id": "e-1", "client_order_id": "oc-1", "symbol": "SPY", "side": "buy", "qty": 3.0,
    "price": 571.25, "at": "2026-10-09T13:30:02.5Z", "decision_price": 570.9, "arrival_bid": 571.1,
    "arrival_ask": 571.3, "shortfall_bps": 6.13, "delay_bps": 3.5, "slippage_bps": 0.87,
    "half_spread_bps": 1.75, "versus_model_bps": None, "arrival_after_hours": True,
}
# 23:50 UTC on the 8th is 19:50 New York on the 8th: yesterday's, not this session's.
FILL_YESTERDAY = {**FILL_TODAY, "execution_id": "e-0", "client_order_id": "oc-0", "symbol": "TLT",
                  "side": "sell", "at": "2026-10-08T23:50:00Z"}

DESK = {
    "status": "enabled", "reason": None, "venue": "alpaca-paper", "trading": True, "session_pnl": -12.5,
    "sessions": 4, "exposure_scale": 0.85, "governor": 1.0,
    "overlay": {"value": 0.85, "as_of": "2026-10-09T19:45:00Z", "age_s": 300, "state": "fresh",
                "reason": None, "governor_cap": 0.15},
    "fills": [FILL_TODAY, FILL_YESTERDAY],
}
TCA = {"note": "paper fills against one venue's quote",
       "overall": {"count": 2, "mean_shortfall_bps": 6.13, "median_shortfall_bps": 6.13,
                   "weighted_shortfall_bps": 6.13, "mean_versus_model_bps": None},
       "by_symbol": {"SPY": {"count": 1, "mean_shortfall_bps": 6.13}}}


def _latest(verdict: str, rule: str | None, detail: str, rebalance: str | None, seq: int) -> dict[str, Any]:
    return {"verdict": verdict, "rule": rule, "detail": detail, "tier": "incubation", "rebalance": rebalance,
            "sequence": seq, "as_of": "2026-10-09", "received_at": "2026-10-08T22:45:10Z",
            "targets": [{"symbol": "SPY", "weight": 1.0}],
            "validation": {"status": "fail", "gates_version": "2026-09-02", "dsr": None, "psr": None,
                           "pbo": None, "manifest": None}}


def _strategy(name: str, latest: dict[str, Any] | None, state: str = "active") -> dict[str, Any]:
    judgement = None if latest is None else {k: latest[k] for k in ("verdict", "rule", "detail")}
    return {"name": name, "symbols": ["SPY"], "max_age": 3, "sizing": "incubation", "tier": "incubation",
            "state": state, "capital_fraction": 0.025, "cadence": "daily", "adjust": "open",
            "latest": latest, "judgement": judgement,
            "validation": None if latest is None else latest["validation"], "record": None}


RESEARCH = {
    "intake": "running", "reason": None, "deferred": 0, "last_pass": "2026-10-09T20:29:00Z",
    "strategies": [
        _strategy("exp_a01_spy", _latest("accepted_incubation", None, "incubation: accepted", "filled 3 SPY", 41)),
        _strategy("farm_tsmom_bonds", _latest("rejected", "R4", "stale: as_of 2026-10-01", None, 7)),
        _strategy("farm_mix_eq2", _latest("accepted_incubation", None, "incubation: accepted",
                                          "refused, and nothing journaled or sent: over the gross cap", 9)),
        _strategy("farm_never", None, state="stopped"),
    ],
    "overlay": {"value": 0.85, "as_of": "2026-10-09T19:45:00Z", "age_s": 300, "state": "fresh", "reason": None,
                "governor": 1.0, "governor_cap": 0.15,
                "inputs": {"vol_target": 0.1, "vol_forecast": 0.1176, "vol_model": "gjr",
                           "cov_estimator": "lw_constant_corr", "vol_artifact": "01A", "cov_artifact": None},
                "reported": {"regime_p_high": 0.2, "mc_var_99_1d_core": 0.031}},
    "r8": "R8 (data hash) is not enforced; see the design §3.12",
}
SESSIONS = [{"date": "2026-10-08", "equity_close": 100012.5, "cash_close": 40000.0, "gross_close": 0.6,
             "net_close": 0.6},
            {"date": "2026-10-09", "equity_close": 100000.0, "cash_close": 39000.0, "gross_close": 0.61,
             "net_close": 0.61}]


class _Engine:
    """routes: path -> (status, body bytes, delay seconds)."""

    def __init__(self, routes: dict[str, tuple[int, bytes, float]]):
        self.routes = routes
        self.seen: list[str] = []
        engine = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server's name)
                engine.seen.append(self.path)
                status, body, delay = engine.routes.get(self.path, (404, b'{"error":"not found"}', 0.0))
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _ok(body: Any) -> tuple[int, bytes, float]:
    return (200, json.dumps(body).encode(), 0.0)


def _full() -> dict[str, tuple[int, bytes, float]]:
    return {"/api/desk": _ok(DESK), "/api/desk/tca": _ok(TCA), "/api/research": _ok(RESEARCH),
            "/api/desk/sessions": _ok(SESSIONS)}


def _ctx(tmp_path: Path, url: str | None) -> JobContext:
    md = MarketData(Settings(offline=True, data_dir=tmp_path, live_engine_url=url))
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=md)


# Friday 2026-10-09, 16:31 New York (EDT, UTC-4): one minute after the scheduled run.
RUN_AT = datetime(2026, 10, 9, 20, 31, 7, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _session(monkeypatch):
    monkeypatch.setattr(report, "_now", lambda: RUN_AT)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------------ the kind
def test_kind_and_schedule():
    from ohcamel_quant.jobs.schedules import load_schedules

    spec = REGISTRY["desk.report"]
    assert (spec.handler, spec.mem_class, spec.heavy, spec.public) == (
        "ohcamel_quant.jobs.handlers.signals:report", "S", False, False)
    s = load_schedules()
    assert s.max_age_s["desk.report"] == 30 * 3600
    [e] = [e for e in s.entries if e.kind == "desk.report"]
    assert (e.cron, e.mem_class, e.heavy) == ("30 16 * * 1-5", "S", False)


def test_the_setting_defaults_to_the_engine_in_container_and_blank_is_unset():
    assert Settings().live_engine_url == "http://ohcamel-live:8081"
    assert Settings(live_engine_url="  ").live_engine_url is None


# ------------------------------------------------------------- a full report
def test_a_full_report(tmp_path):
    with _Engine(_full()) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert sorted(eng.seen) == sorted(report.ROUTES)
    assert spec.verdict is None
    assert spec.data_asof == "2026-10-09"
    assert set(spec.tables) == {"fills", "pnl_by_strategy", "refusals", "overlay_path"}

    fills = spec.tables["fills"]
    # Only this session's fill (13:30Z is 09:30 New York on the 9th), with every field the route serves.
    assert fills["execution_id"].tolist() == ["e-1"]
    row = fills.iloc[0]
    assert (row["symbol"], row["side"], row["qty"], row["price"], row["shortfall_bps"]) == (
        "SPY", "buy", 3.0, 571.25, 6.13)
    assert bool(row["arrival_after_hours"]) is True
    assert list(fills.columns) == report.FILL_COLUMNS

    pnl = spec.tables["pnl_by_strategy"]
    assert pnl["strategy"].tolist() == ["exp_a01_spy", "farm_tsmom_bonds", "farm_mix_eq2", "farm_never"]
    assert pnl["pnl"].isna().all()  # not on the wire until Stage 3; never invented
    assert pnl.set_index("strategy").loc["farm_never", "state"] == "stopped"
    assert pnl.set_index("strategy").loc["farm_tsmom_bonds", "verdict"] == "rejected"
    assert (pnl["capital_fraction"] == 0.025).all()

    ref = spec.tables["refusals"]
    assert ref[["strategy", "source", "verdict"]].values.tolist() == [
        ["farm_tsmom_bonds", "judgement", "rejected"],
        ["farm_mix_eq2", "rebalance", "accepted_incubation"],
    ]
    assert ref.iloc[0]["rule"] == "R4" and pd.isna(ref.iloc[1]["rule"])  # a rebalance names no rule
    assert ref.iloc[1]["detail"].startswith("refused")

    ov = spec.tables["overlay_path"]
    assert len(ov) == 1
    o = ov.iloc[0]
    assert (o["exposure_scale"], o["value"], o["state"], o["governor"], o["vol_model"]) == (
        0.85, 0.85, "fresh", 1.0, "gjr")
    assert json.loads(o["inputs"])["vol_forecast"] == 0.1176

    notes = " | ".join(spec.notes)
    assert "per-strategy P&L is not on the wire" in notes
    assert "not evidence of edge" in notes
    assert "equity_close 100000" in notes          # the session's close, from /api/desk/sessions
    assert "paper fills against one venue's quote" in notes   # /api/desk/tca's own note
    assert "1 of the 2 fills /api/desk serves" in notes


def test_the_handler_runs_the_report(tmp_path):
    from ohcamel_quant.jobs.handlers.signals import report as handler

    with _Engine(_full()) as eng:
        spec = handler({}, _ctx(tmp_path, eng.url))
    assert len(spec.tables["fills"]) == 1


# --------------------------------------------------------- an unreachable engine
def test_an_unreachable_engine_is_a_done_job_that_says_so(tmp_path):
    url = f"http://127.0.0.1:{_free_port()}"
    spec = report.run({}, _ctx(tmp_path, url))
    assert spec.verdict is None
    assert all(df.empty for df in spec.tables.values())
    assert set(spec.tables) == {"fills", "pnl_by_strategy", "refusals", "overlay_path"}
    assert list(spec.tables["fills"].columns) == report.FILL_COLUMNS
    notes = " | ".join(spec.notes)
    assert "did not answer" in notes and url in notes
    assert "live profile" in notes


def test_an_unset_engine_url_is_a_done_job_that_says_so(tmp_path):
    spec = report.run({}, _ctx(tmp_path, ""))
    assert all(df.empty for df in spec.tables.values())
    assert any("OHCAMEL_QUANT_LIVE_ENGINE_URL is unset" in n for n in spec.notes)


def test_a_slow_engine_times_out_and_the_job_still_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "TIMEOUT_S", 0.2)
    routes = _full()
    routes["/api/desk"] = (200, json.dumps(DESK).encode(), 1.0)
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert all(df.empty for df in spec.tables.values())
    assert any("did not answer" in n and "/api/desk" in n for n in spec.notes)


# ------------------------------------------------------------ a malformed body
def test_a_malformed_route_body_is_named_and_the_rest_is_reported(tmp_path):
    routes = _full()
    routes["/api/research"] = (200, b"<html>not json</html>", 0.0)
    routes["/api/desk/tca"] = _ok(["a list, not the object the route serves"])
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert spec.tables["pnl_by_strategy"].empty
    assert spec.tables["refusals"].empty
    assert len(spec.tables["fills"]) == 1                 # /api/desk still read
    assert spec.tables["overlay_path"].iloc[0]["exposure_scale"] == 0.85   # from /api/desk
    notes = " | ".join(spec.notes)
    assert "GET /api/research: malformed" in notes
    assert "GET /api/desk/tca: malformed" in notes


def test_an_error_status_and_bad_rows_are_named(tmp_path):
    routes = _full()
    routes["/api/research"] = (500, b'{"error":"the desk hit an internal error"}', 0.0)
    desk = {**DESK, "fills": [FILL_TODAY, "not a fill", {"symbol": "QQQ"}]}
    routes["/api/desk"] = _ok(desk)
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    notes = " | ".join(spec.notes)
    assert "GET /api/research: HTTP 500" in notes
    assert "2 fill rows skipped" in notes
    assert spec.tables["fills"]["execution_id"].tolist() == ["e-1"]


# ------------------------------------------------------------- the session
@pytest.mark.parametrize(("at", "want"), [
    (datetime(2026, 10, 9, 20, 30, tzinfo=UTC), date(2026, 10, 9)),    # Fri 16:30 NY: that session
    (datetime(2026, 10, 9, 20, 29, tzinfo=UTC), date(2026, 10, 8)),    # Fri 16:29 NY: not yet; Thursday
    (datetime(2026, 10, 10, 4, 30, tzinfo=UTC), date(2026, 10, 9)),    # Sat 00:30 NY: the previous weekday
    (datetime(2026, 10, 12, 4, 30, tzinfo=UTC), date(2026, 10, 9)),    # Mon 00:30 NY: Friday
    (datetime(2026, 10, 13, 4, 30, tzinfo=UTC), date(2026, 10, 12)),   # Tue 00:30 NY: Monday
])
def test_the_session_is_the_newest_weekday_whose_1630_has_passed(at, want):
    assert report._session_date(at) == want


def test_a_run_caught_up_after_midnight_reports_the_previous_weekday(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_now", lambda: datetime(2026, 10, 10, 4, 30, tzinfo=UTC))  # Sat 00:30 NY
    with _Engine(_full()) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert spec.data_asof == "2026-10-09"
    assert spec.tables["fills"]["execution_id"].tolist() == ["e-1"]
    assert any("session close 2026-10-09" in n for n in spec.notes)
    assert not any("the session is" in n for n in spec.notes)


def test_a_monday_0030_run_reports_friday_even_with_the_engine_down(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_now", lambda: datetime(2026, 10, 12, 4, 30, tzinfo=UTC))  # Mon 00:30 NY
    spec = report.run({}, _ctx(tmp_path, f"http://127.0.0.1:{_free_port()}"))
    assert spec.data_asof == "2026-10-09"


def test_the_engines_newest_close_wins_when_it_differs_and_says_so(tmp_path):
    # A holiday-shaped day: the engine's newest close is Thursday's.
    routes = _full()
    routes["/api/desk/sessions"] = _ok(SESSIONS[:1])
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert spec.data_asof == "2026-10-08"
    assert spec.tables["fills"]["execution_id"].tolist() == ["e-0"]   # 19:50 NY on the 8th
    assert any("the session is 2026-10-08" in n and "not 2026-10-09" in n for n in spec.notes)
