"""scripts/check_done_probe.py (completion Task 5.3): the checks of scripts/check-done.sh that need the code.

Run on constructed inputs, each case saying what it is. The committed-SPY-chain check (6) takes about seven seconds
and is exercised by running ``scripts/check-done.sh``; here its cheap halves are pinned: the vol league's target
label is the one the code computes, and the probe's bounds are Task 4.1's and Task 4.2's.
"""

from __future__ import annotations

import json
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
    assert P.G_FLOOR == -1e-10 and P.PUT_CALL_POINTS == 0.5 and P.RESOLVED_HALF_WIDTH == 0.25
