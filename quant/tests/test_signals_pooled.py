"""The desk's public record is INC-02's one test: its horizon and its power said before its result.

Pure: ``desk.report``'s ``_pooled_test`` is given a research body, session closes and a risk-free map; an
approved pre-registration is a temp directory whose ``preregistration.md`` has an ``Approved-by:`` line."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from ohcamel_quant.signals import pooled, report
from ohcamel_quant.signals.roster import ROSTER_PATH

PREREG = ROSTER_PATH.parent / "preregistration.md"
ALLOWED = ["experiment", "sessions_done", "sessions_required", "psr", "sharpe_se", "power_at_1", "power_at_half",
           "verdict", "reason", "statistic", "as_of", "source"]   # the brief's eight, then the ruled-in four


def _days(n: int, start: date = date(2027, 1, 4)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bodies(n: int, *, mean: float = 0.0006, sd: float = 0.005, seed: int = 1):
    """A strategy with a record of ``n`` sessions, an account of 100,000 with 25,000 allocated, and a prior-close
    equity for each (one extra close before the first fill)."""
    days = _days(n + 1)
    pnl = np.random.default_rng(seed).normal(mean, sd, n) * 25_000
    rec = [{"date": d, "allocated": 25_000.0, "pnl": float(p)} for d, p in zip(days[1:], pnl, strict=True)]
    research = {"strategies": [{"name": "a", "record": {"sessions": rec}}, {"name": "b", "record": None}]}
    closes = [{"date": d, "equity_close": 100_000.0} for d in days]
    return research, closes, (lambda: {d: 0.0 for d in days}), days[-1]


@pytest.fixture
def approved(tmp_path) -> Path:
    (tmp_path / "preregistration.md").write_text("Approved-by: the owner, 2027-01-02\n")
    return tmp_path / "roster.yaml"


def _row(body, *, path):
    research, closes, rf, last = body
    [row] = report._pooled_test(research, closes, rf, date.fromisoformat(last), [], roster_path=path)
    return row


def test_the_power_is_computed_and_the_pre_registration_prints_it():
    assert round(pooled.analytic_power(1.0), 2) == 0.41 and round(pooled.analytic_power(0.5), 2) == 0.17
    text = PREREG.read_text()
    assert "| Probability of rejecting H0 in 500 sessions | 5% (the size) | 17% | 41% |" in text


def test_the_real_preregistration_is_unapproved_so_the_real_report_is_not_started():
    from ohcamel_quant.signals.roster import roster_draft

    if roster_draft() is None:
        pytest.skip("INC-02 is approved: the unapproved state is covered by the temp pre-registration below")
    row = _row(_bodies(120), path=ROSTER_PATH)
    assert row["verdict"] == "NOT STARTED" and row["sessions_done"] == 0
    assert row["reason"] == "INC-02 awaits the owner's approval" and row["psr"] is None and row["sharpe_se"] is None


def test_an_unapproved_preregistration_is_not_started_whatever_the_record(tmp_path):
    (tmp_path / "preregistration.md").write_text("no approval recorded here\n")
    row = _row(_bodies(120), path=tmp_path / "roster.yaml")
    assert row["verdict"] == "NOT STARTED" and row["sessions_done"] == 0 and row["psr"] is None
    assert row["reason"] == "INC-02 awaits the owner's approval" and "proposed" in row["statistic"]
    assert (row["power_at_1"], row["power_at_half"], row["sessions_required"]) == (0.41, 0.17, 500)


def test_approved_but_before_the_first_fill_is_not_started(approved):
    row = _row(({"strategies": [{"name": "a", "record": None}]}, [], lambda: {}, "2027-01-05"), path=approved)
    assert row["verdict"] == "NOT STARTED" and row["reason"] == "no INC-02 fill yet" and row["sessions_done"] == 0
    assert "proposed" not in row["statistic"]


def test_at_120_sessions_it_is_in_progress_with_its_psr_and_standard_error(approved):
    row = _row(_bodies(120), path=approved)
    assert row["verdict"] == "IN PROGRESS" and row["sessions_done"] == 120 and row["sessions_required"] == 500
    assert 0.0 < row["psr"] < 1.0 and row["sharpe_se"] > 0
    assert (row["power_at_1"], row["power_at_half"]) == (0.41, 0.17)
    assert row["experiment"] == "INC-02" and row["as_of"] and row["source"]


def test_the_standard_error_is_the_psr_denominator_over_the_sample(approved):
    research, closes, rf, last = _bodies(200)
    row = _row((research, closes, rf, last), path=approved)
    x = np.array([r["pnl"] / 100_000.0 for r in research["strategies"][0]["record"]["sessions"]])
    sr = x.mean() / x.std(ddof=1)
    # the Sharpe estimate's variance under normality is (1 + SR^2/2)/(n-1); skew and kurtosis move it a little
    approx = np.sqrt(252 * (1 + sr * sr / 2) / (len(x) - 1))
    assert row["sharpe_se"] == pytest.approx(approx, rel=0.15)


def test_at_500_with_a_psr_of_096_the_null_is_rejected(approved, monkeypatch):
    monkeypatch.setattr(report, "psr_and_se", lambda x: (0.96, 0.9))
    row = _row(_bodies(500), path=approved)
    assert (row["sessions_done"], row["verdict"], row["psr"]) == (500, "REJECTED NULL", 0.96)
    monkeypatch.setattr(report, "psr_and_se", lambda x: (0.94, 0.9))
    assert _row(_bodies(500), path=approved)["verdict"] == "NOT REJECTED"


def test_the_horizon_is_500_sessions_and_no_more_are_read(approved):
    row = _row(_bodies(520), path=approved)
    assert row["sessions_done"] == 500 and row["verdict"] in ("REJECTED NULL", "NOT REJECTED")


def test_a_flat_session_counts_with_a_zero_return(approved):
    research, closes, rf, last = _bodies(60)
    del research["strategies"][0]["record"]["sessions"][30]       # the desk was halted that day
    assert _row((research, closes, rf, last), path=approved)["sessions_done"] == 60


def test_the_statistic_is_the_excess_return_on_the_accounts_prior_close_equity():
    research = {"strategies": [{"name": "a", "record": {"sessions": [
        {"date": "2027-01-05", "allocated": 50_000.0, "pnl": 600.0}]}}]}
    closes = [{"date": "2027-01-04", "equity_close": 100_000.0}, {"date": "2027-01-05", "equity_close": 100_600.0}]
    got = report._pooled_daily(research, closes, lambda: {"2027-01-05": 0.0002}, [])
    assert got == [pytest.approx((600.0 - 50_000.0 * 0.0002) / 100_000.0)]


def test_a_missing_rate_or_prior_equity_is_a_note_not_a_number():
    research = {"strategies": [{"name": "a", "record": {"sessions": [
        {"date": "2027-01-05", "allocated": 1.0, "pnl": 1.0}]}}]}
    notes: list[str] = []
    closes = [{"date": "2027-01-04", "equity_close": 1.0}, {"date": "2027-01-05", "equity_close": 1.0}]
    assert report._pooled_daily(research, closes, lambda: {}, notes) is None and "risk-free" in notes[0]
    assert report._pooled_daily(research, closes[1:], lambda: {"2027-01-05": 0.0}, notes) is None


def test_the_public_columns_and_no_return_equity_position_or_size(approved):
    row = _row(_bodies(30), path=approved)
    assert sorted(row) == sorted(ALLOWED)
    assert report.POOLED_COLUMNS == ALLOWED
    assert not any(w in c for c in ALLOWED for w in ("return", "equity", "position", "size", "mean", "sd", "pnl"))
