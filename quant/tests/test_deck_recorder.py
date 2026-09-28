"""The flight recorder (deck/recorder.py): the SQLite store, one tick of the
workflow, pruning, and the column-major tape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ohcamel_quant.deck.clock import Clock
from ohcamel_quant.deck.recorder import Recorder, Store, compact, tape_columns


def reading(pnl: float, util: float) -> dict:
    return {
        "book": {"day_pnl_usd": pnl, "equity_usd": 1e6 + pnl},
        "risk": {"var_usd": 11_000.0, "es_usd": 14_000.0},
        "limits": [{"name": "var-cap", "utilisation": util, "breached": util > 1}],
        "marks": [{"ticker": "SPY", "change_pct": pnl / 4e5, "price": 700.0}],
    }


def clock(is_open: bool, session: str = "2026-09-24") -> Clock:
    return Clock(is_open, "now", "open", "close", session, "rule")


T0 = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)


def test_compact_keeps_what_the_tape_draws():
    c = compact(reading(-500.0, 1.2))
    assert c["day_pnl_usd"] == -500.0 and c["utilisation"] == {"var-cap": 1.2}
    assert c["breached"] == ["var-cap"] and c["price"] == {"SPY": 700.0}


def test_a_tick_records_only_while_open(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    state = {"open": True}
    rec = Recorder(store, lambda: {"core": reading(-100.0, 0.5)}, lambda: clock(state["open"]))
    assert rec.tick(T0) == 1
    assert rec.tick(T0 + timedelta(minutes=1)) == 1
    state["open"] = False
    assert rec.tick(T0 + timedelta(minutes=2)) == 0
    assert store.stats()["rows"] == 2 and store.sessions("core") == ["2026-09-24"]


def test_the_tape_is_column_major_in_time_order(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    store.record("core", "2026-09-24", T0 + timedelta(minutes=1), compact(reading(200.0, 0.4)))
    store.record("core", "2026-09-24", T0, compact(reading(-100.0, 0.5)))
    t = tape_columns(store.tape("core", "2026-09-24"))
    assert t["n"] == 2 and t["day_pnl_usd"] == [-100.0, 200.0]
    assert t["utilisation"] == {"var-cap": [0.5, 0.4]}
    assert t["ts"][0] == T0.isoformat()


def test_a_limit_added_mid_session_is_none_before_it_existed():
    rows = [("t0", {"utilisation": {"a": 0.1}}), ("t1", {"utilisation": {"a": 0.2, "b": 0.9}})]
    assert tape_columns(rows)["utilisation"] == {"a": [0.1, 0.2], "b": [None, 0.9]}


def test_after_the_close_it_prunes_to_the_newest_sessions_once(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    for i, day in enumerate(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]):
        store.record("core", day, T0 + timedelta(days=i), compact(reading(0.0, 0.1)))
    rec = Recorder(store, lambda: {}, lambda: clock(False), keep_sessions=2)
    rec.tick(T0 + timedelta(days=5))
    assert store.sessions("core") == ["2026-09-24", "2026-09-23"]
    store.record("core", "2026-09-20", T0 - timedelta(days=1), compact(reading(0.0, 0.1)))
    rec.tick(T0 + timedelta(days=5, minutes=1))                 # same session: no second prune
    assert "2026-09-20" in store.sessions("core")


def test_the_same_instant_twice_is_one_row(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    store.record("core", "2026-09-24", T0, compact(reading(1.0, 0.1)))
    store.record("core", "2026-09-24", T0, compact(reading(2.0, 0.1)))
    assert store.stats()["rows"] == 1 and store.tape("core", "2026-09-24")[0][1]["day_pnl_usd"] == 2.0


def test_the_loop_runs_and_stops(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    rec = Recorder(store, lambda: {"core": reading(1.0, 0.1)}, lambda: clock(True), interval_s=0.05)
    rec.start(initial_delay_s=0)
    import time
    deadline = time.monotonic() + 5
    while store.stats()["rows"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    rec.stop()
    assert store.stats()["rows"] >= 1 and not rec.running


def test_a_failing_tick_is_recorded_and_the_loop_survives(tmp_path):
    store = Store(tmp_path / "r.sqlite")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        raise RuntimeError("vendor down")

    rec = Recorder(store, compute, lambda: clock(True), interval_s=0.02)
    rec.start(initial_delay_s=0)
    import time
    deadline = time.monotonic() + 5
    while calls["n"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    rec.stop()
    assert calls["n"] >= 2 and rec.last_error == "RuntimeError: vendor down"
