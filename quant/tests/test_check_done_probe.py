"""scripts/check_done_probe.py (completion Task 5.3): the checks of scripts/check-done.sh that need the code.

Run on constructed inputs, each case saying what it is. The committed-SPY-chain check (6) takes about seven seconds
and is exercised by running ``scripts/check-done.sh``; here its cheap halves are pinned: the vol league's target
label is the one the code computes, and the probe's bounds are Task 4.1's and Task 4.2's.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

from ohcamel_quant.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_done_probe as P  # noqa: E402


def _say(fn, *args) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert fn(*args) == 0
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 1, lines                      # one line, always
    return lines[0]


def _journal(path: Path, fills, marks, *, sessions=("2026-11-24",)) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE orders (client_order_id TEXT PRIMARY KEY, source TEXT NOT NULL);"
        "CREATE TABLE fills (execution_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, symbol TEXT NOT NULL,"
        " side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL);"
        "CREATE TABLE sessions (date TEXT PRIMARY KEY);"
        "CREATE TABLE marks (date TEXT NOT NULL, symbol TEXT NOT NULL, close REAL NOT NULL, qty REAL NOT NULL);")
    for i, (source, sym, side, qty, price) in enumerate(fills):
        con.execute("INSERT INTO orders VALUES (?, ?)", (f"o{i}", source))
        con.execute("INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?)", (f"e{i}", f"o{i}", sym, side, qty, price))
    for d in sessions:
        con.execute("INSERT INTO sessions VALUES (?)", (d,))
    for sym, close, qty in marks:
        con.execute("INSERT INTO marks VALUES (?, ?, ?, ?)", ("2026-11-24", sym, close, qty))
    con.commit()
    con.close()
    return path


def test_the_tolerance_is_the_ocaml_subledgers() -> None:
    assert P.subledger_tolerance() == 0.5


def test_a_sub_ledger_that_sums_is_ok_and_opposite_sides_net(tmp_path) -> None:
    j = _journal(tmp_path / "j.db",
                 [("signal:alpha:1", "SPY", "buy", 100, 500.0), ("signal:beta:1", "SPY", "sell", 50, 501.0),
                  ("ticket:7", "TLT", "buy", 10, 90.0)],
                 [("SPY", 505.0, 50), ("TLT", 91.0, 10)])
    assert _say(P.ledger, str(j)) == "ok"


def test_a_break_names_the_name_and_both_positions(tmp_path) -> None:
    j = _journal(tmp_path / "j.db", [("signal:alpha:1", "SPY", "buy", 100, 500.0)], [("SPY", 505.0, 40)])
    line = _say(P.ledger, str(j))
    assert line.startswith("FAIL: sub-ledger break at the close of 2026-11-24") and "SPY strategies 100 vs account 40" in line


def test_a_name_the_account_holds_that_no_fill_explains_is_a_break(tmp_path) -> None:
    j = _journal(tmp_path / "j.db", [("signal:alpha:1", "SPY", "buy", 1, 500.0)], [("SPY", 505.0, 1), ("QQQ", 400.0, 7)])
    assert "QQQ strategies 0 vs account 7" in _say(P.ledger, str(j))


def test_a_journal_that_cannot_be_judged_is_not_checked_never_ok(tmp_path) -> None:
    assert _say(P.ledger, str(tmp_path / "absent.db")).startswith("not checked: no journal at")
    empty = _journal(tmp_path / "e.db", [], [], sessions=())
    assert _say(P.ledger, str(empty)) == "not checked: the journal holds no session close"
    nofills = _journal(tmp_path / "n.db", [], [], sessions=("2026-11-24",))
    assert _say(P.ledger, str(nofills)) == "not checked: the journal holds no fills"


def test_the_journal_is_not_written(tmp_path) -> None:
    j = _journal(tmp_path / "j.db", [("signal:alpha:1", "SPY", "buy", 1, 500.0)], [("SPY", 505.0, 1)])
    before = j.read_bytes()
    _say(P.ledger, str(j))
    assert j.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["j.db"]


def _farm_artifact(root: Path, *, fill="open", gross=1.0, capacity=True, manifest_trials=2, table_trials=2) -> Path:
    from ohcamel_quant.models import trial_ledger

    d = root / "farm.sweep" / "run1"
    d.mkdir(parents=True)
    cells = pd.DataFrame({"status": ["ok", "skipped"], "fill": [fill, "close"], "max_gross": [gross, 2.0]})
    if capacity:
        cells["capacity_usd"] = [1e6, float("nan")]
    cells.to_parquet(d / "cells.parquet")
    pd.DataFrame({"cell_id": ["a", "a"], "n": [1, 2]}).iloc[:table_trials].to_parquet(d / "ledger.parquet")
    (d / "manifest.json").write_text(json.dumps({"kind": "farm.sweep", "provenance": [
        {"source": "trial-ledger", "distinct_trials": manifest_trials}]}))
    trial_ledger.append_many(root / trial_ledger.LEDGER_NAME, [
        {"cell_id": "a", "grid_version": "g", "params_hash": h, "sharpe": 0.1, "n_obs": 100} for h in ("h1", "h2")])
    return d


def test_a_farm_scored_as_traded_with_one_trial_count_is_ok(tmp_path) -> None:
    assert _say(P.farm, str(_farm_artifact(tmp_path)), None) == "ok"      # the skipped cell is not judged


@pytest.mark.parametrize(("kw", "needle"), [
    ({"fill": "close"}, "fill is not 'open'"), ({"gross": 2.0}, "max_gross is not 1"),
    ({"capacity": False}, "no capacity_usd column"), ({"manifest_trials": 3}, "trial count: manifest 3"),
    ({"table_trials": 1}, "ledger table 1")])
def test_each_departure_from_the_traded_configuration_is_a_fail(tmp_path, kw, needle) -> None:
    line = _say(P.farm, str(_farm_artifact(tmp_path, **kw)), None)
    assert line.startswith("FAIL: ") and needle in line


def test_no_artifact_or_no_ledger_is_not_checked(tmp_path) -> None:
    assert _say(P.farm, str(tmp_path / "none"), None).startswith("not checked: no farm artifact at")
    d = _farm_artifact(tmp_path)
    (tmp_path / "trial_ledger.jsonl").unlink()
    assert _say(P.farm, str(d), None).startswith("not checked: no trial ledger at")


def test_inc02_fails_while_its_approval_line_is_empty_and_passes_once_it_names_a_date(monkeypatch) -> None:
    from ohcamel_quant.signals import roster

    text = roster.preregistration_text()
    if roster.recorded_approval(text):
        pytest.skip("INC-02 is approved in the checkout; the empty-line case is not constructible from it")
    assert _say(P.inc02).startswith("FAIL: INC-02 is not approved: its Approved-by line is empty")
    approved = text.replace("Approved-by:", "Approved-by: the owner, 2026-12-01", 1)
    monkeypatch.setattr(roster, "preregistration_text", lambda *a, **k: approved)
    assert _say(P.inc02) == "ok"
    monkeypatch.setattr(roster, "preregistration_text", lambda *a, **k: text.replace("Approved-by:", "Approved-by: soon", 1))
    assert "carries no ISO date" in _say(P.inc02)


def test_the_vol_league_note_and_docstring_name_the_target_the_code_computes() -> None:
    import inspect

    from ohcamel_quant.products import vol_league

    assert vol_league.TARGET_LABEL.startswith("close-to-close")        # the code's label; the plan's `close_to_close` is a token
    assert inspect.getdoc(vol_league._daily_rv).lower().startswith("the league's one scoring target: " + vol_league.TARGET_LABEL)
    assert "{TARGET_LABEL}" in inspect.getsource(vol_league.run)


def test_the_bounds_are_the_amended_conditions() -> None:
    assert P.G_FLOOR == -1e-10 and P.PUT_CALL_POINTS == 0.5 and P.RESOLVED_HALF_WIDTH == 0.25 and P.PNL_TOLERANCE_USD == 1.0
