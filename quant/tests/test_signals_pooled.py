"""The desk's public record is INC-02's one test: its horizon and its power said before its result.

Pure: ``desk.report``'s ``_pooled_test`` is given a research body, session closes and a risk-free map; an
approved pre-registration is a temp directory whose ``preregistration.md`` has an ``Approved-by:`` line."""

from __future__ import annotations

import json
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


def _bodies(n: int, *, mean: float = 0.0006, sd: float = 0.005, seed: int = 1, start: date = date(2027, 1, 4)):
    """A strategy with a record of ``n`` sessions, an account of 100,000 with 25,000 allocated, and a prior-close
    equity for each (one extra close before the first fill)."""
    days = _days(n + 1, start)
    pnl = np.random.default_rng(seed).normal(mean, sd, n) * 25_000
    rec = [{"date": d, "allocated": 25_000.0, "pnl": float(p), "turnover": 1_000.0}
           for d, p in zip(days[1:], pnl, strict=True)]
    research = {"strategies": [{"name": "strat_a", "record": {"sessions": rec}}, {"name": "strat_b", "record": None}]}
    closes = [{"date": d, "equity_close": 100_000.0} for d in days]
    return research, closes, (lambda: {d: 0.0 for d in days}), days[-1]


ROSTER = """version: 2
incubation_cap: 0.55
canary_cap: 0.10
vol_target: 0.10
strategies:
  - {slug: strat_a, producer: research-service, capital_fraction: 0.025}
  - {slug: strat_b, producer: research-service, capital_fraction: 0.025}
"""


def _experiment(tmp_path, approval: str) -> Path:
    (tmp_path / "preregistration.md").write_text(approval)
    (tmp_path / "roster.yaml").write_text(ROSTER)
    return tmp_path / "roster.yaml"


@pytest.fixture
def approved(tmp_path) -> Path:
    return _experiment(tmp_path, "Approved-by: the owner, 2027-01-02\n")


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
    row = _row(({"strategies": [{"name": "strat_a", "record": None}]}, [], lambda: {}, "2027-01-05"), path=approved)
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
        {"date": "2027-01-05", "allocated": 50_000.0, "pnl": 600.0, "turnover": 50_000.0}]}}]}
    closes = [{"date": "2027-01-04", "equity_close": 100_000.0}, {"date": "2027-01-05", "equity_close": 100_600.0}]
    got, recorded, cause = report._pooled_daily(research, closes, lambda: {"2027-01-05": 0.0002}, [])
    assert (recorded, cause) == (1, None)
    assert got == [pytest.approx((600.0 - 50_000.0 * 0.0002) / 100_000.0)]


def test_a_missing_rate_or_prior_equity_marks_that_session_unscorable_and_is_counted():
    research = {"strategies": [{"name": "a", "record": {"sessions": [
        {"date": "2027-01-05", "allocated": 1.0, "pnl": 1.0, "turnover": 1.0}]}}]}
    notes: list[str] = []
    closes = [{"date": "2027-01-04", "equity_close": 1.0}, {"date": "2027-01-05", "equity_close": 1.0}]
    got, recorded, cause = report._pooled_daily(research, closes, lambda: {}, notes)
    assert got == [None] and recorded == 1 and cause is None
    assert any("1 of 1 sessions unscorable" in n and "risk-free" in n for n in notes)
    got, recorded, cause = report._pooled_daily(research, closes[1:], lambda: {"2027-01-05": 0.0}, notes)
    assert got == [None] and recorded == 1 and any("prior-close equity" in n for n in notes)


def test_the_newest_days_missing_rate_is_carried_from_the_last_published_one(approved):
    """C1: the rate is published a day late; the newest session uses the last rate on or before it."""
    research, closes, _, last = _bodies(120)
    days = [c["date"] for c in closes]
    rf = {d: 0.0001 for d in days[:-1]}               # nothing published for the newest session yet
    row = _row((research, closes, lambda: rf, last), path=approved)
    assert row["sessions_done"] == 120 and row["verdict"] == "IN PROGRESS"
    assert row["psr"] is not None and row["reason"] == "running, not the test's result"
    assert "last published risk-free rate on or before each session" in row["statistic"]


def test_a_gap_inside_the_window_is_counted_and_not_fatal(approved):
    research, closes, _, last = _bodies(120)
    days = [c["date"] for c in closes]
    rf = {d: 0.0 for d in days}
    closes[60] = {**closes[60], "equity_close": None}  # the prior equity of session 61 is missing
    row = _row((research, closes, lambda: rf, last), path=approved)
    assert row["sessions_done"] == 120 and row["psr"] is not None
    assert row["reason"] == "running, not the test's result; 1 of 120 sessions unscorable (no prior-close equity or rate)"


def test_a_rate_older_than_the_carry_bound_is_a_gap():
    research = {"strategies": [{"name": "a", "record": {"sessions": [
        {"date": d, "allocated": 1.0, "pnl": 0.0, "turnover": 1.0} for d in _days(9)[1:]]}}]}
    closes = [{"date": d, "equity_close": 1.0} for d in _days(9)]
    got, _, _ = report._pooled_daily(research, closes, lambda: {_days(9)[1]: 0.0}, [])
    # the rate dated on session 1 carries five sessions (2..6); sessions 7 and 8 are unscorable
    assert [x is None for x in got] == [False] * 6 + [True] * 2


def test_past_500_a_later_gap_never_reverts_the_decided_verdict(approved, monkeypatch):
    monkeypatch.setattr(report, "psr_and_se", lambda x: (0.96, 0.9))
    research, closes, _, last = _bodies(520)
    days = [c["date"] for c in closes]
    rf = {d: 0.0 for d in days[:505]}                  # nothing published after session 504
    closes[515] = {**closes[515], "equity_close": None}
    row = _row((research, closes, lambda: rf, last), path=approved)
    assert (row["sessions_done"], row["verdict"], row["reason"]) == (500, "REJECTED NULL", None)


def test_only_inc02_roster_strategies_feed_the_series(approved):
    research, closes, rf, last = _bodies(120)
    other = {"name": "inc01_only", "record": {"sessions": [
        {**r, "pnl": 1e9} for r in research["strategies"][0]["record"]["sessions"]]}}
    with_other = {"strategies": [*research["strategies"], other]}
    assert _row((with_other, closes, rf, last), path=approved) == _row((research, closes, rf, last), path=approved)


def test_the_series_starts_at_the_first_fill_not_the_first_carried_row(approved):
    """INC-01's carried inventory marks a row every session; INC-02 starts at its first FILL on/after approval."""
    research, closes, rf, last = _bodies(120)
    rec = research["strategies"][0]["record"]["sessions"]
    for r in rec[:30]:
        r["turnover"] = 0.0                            # held, not traded
    row = _row((research, closes, rf, last), path=approved)
    assert row["sessions_done"] == 90


def test_the_start_is_clamped_after_inc01s_close(tmp_path):
    path = _experiment(tmp_path, "Approved-by: the owner, 2026-11-01\n")
    research, closes, rf, last = _bodies(30, start=date(2026, 10, 26))
    row = _row((research, closes, rf, last), path=path)
    after = [r for r in research["strategies"][0]["record"]["sessions"] if r["date"] > "2026-11-05"]
    assert row["sessions_done"] == len(after) < 30


def test_the_public_columns_and_no_return_equity_position_or_size(approved):
    row = _row(_bodies(30), path=approved)
    assert sorted(row) == sorted(ALLOWED)
    assert report.POOLED_COLUMNS == ALLOWED
    assert not any(w in c for c in ALLOWED for w in ("return", "equity", "position", "size", "mean", "sd", "pnl"))


def test_a_missing_rate_keeps_the_count_and_gives_its_own_reason(approved):
    research, closes, _, last = _bodies(120)
    row = _row((research, closes, lambda: {}, last), path=approved)
    assert row["sessions_done"] == 120 and row["verdict"] == "IN PROGRESS"
    assert row["psr"] is None and row["sharpe_se"] is None
    assert row["reason"] == ("statistic not computable this session: 120 of 120 sessions unscorable "
                             "(no prior-close equity or rate)")
    assert "no INC-02 fill" not in row["reason"]


def test_a_running_figure_says_it_is_not_the_result_and_the_result_does_not(approved, monkeypatch):
    assert _row(_bodies(120), path=approved)["reason"] == "running, not the test's result"
    assert _row(_bodies(500), path=approved)["reason"] is None


def test_record_dates_with_no_close_are_noted_not_silently_dropped():
    research = {"strategies": [{"name": "a", "record": {"sessions": [
        {"date": "2027-01-05", "allocated": 1.0, "pnl": 1.0, "turnover": 1.0},
        {"date": "2027-01-06", "allocated": 1.0, "pnl": 1.0, "turnover": 1.0}]}}]}
    closes = [{"date": "2027-01-04", "equity_close": 1.0}, {"date": "2027-01-05", "equity_close": 1.0}]
    notes: list[str] = []
    report._pooled_daily(research, closes, lambda: {"2027-01-05": 0.0}, notes)
    assert any("1 record dates have no session close" in n and "2027-01-06" in n for n in notes)


def test_the_served_record_shape_is_read_end_to_end(tmp_path, monkeypatch):
    """desk/intake.ml serves ``record: {"sessions": [{date, allocated, pnl, turnover}]}`` on the live host."""
    from test_signals_report import (  # noqa: PLC0415  (tests/ is on sys.path, as lane_m_harness is)
        RESEARCH,
        SESSIONS,
        _ctx,
        _Engine,
        _full,
        _ok,
    )

    closes = [{**SESSIONS[0], "date": "2026-11-09"}, {**SESSIONS[1], "date": "2026-11-10"}]
    served = {**RESEARCH, "strategies": [{**RESEARCH["strategies"][0], "name": "exp_a01_spy", "record": {"sessions": [
        {"date": "2026-11-10", "allocated": 25_000.0, "pnl": 100.0, "turnover": 5_000.0}]}},
        *RESEARCH["strategies"][1:]]}
    routes = {**_full(), "/api/research": _ok(served), "/api/desk/sessions": _ok(closes)}
    monkeypatch.setattr(report, "recorded_approval", lambda text: "2026-11-09")
    monkeypatch.setattr(report, "recorded_flatten", lambda text: (True, "2026-11-09"))   # Roster-6(a) done the day before
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    r = spec.tables["pooled_test"].iloc[0]
    # one session recorded; the offline test environment has no risk-free rate, so the statistic says so
    assert r["sessions_done"] == 1 and r["verdict"] == "IN PROGRESS"
    assert r["reason"].startswith("statistic not computable this session")


def test_only_sessions_on_or_after_the_approval_date_count_not_inc01s(tmp_path):
    """The journal still holds INC-01's fills (2026-10-21 to 2026-11-05): they are not INC-02's sessions."""
    path = _experiment(tmp_path, "Approved-by: the owner, 2027-01-20\n")
    research, closes, rf, last = _bodies(100)         # sessions from 2027-01-05
    early = [r["date"] for r in research["strategies"][0]["record"]["sessions"] if r["date"] < "2027-01-20"]
    assert early                                       # INC-01-era rows sit before the approval
    row = _row((research, closes, rf, last), path=path)
    after = [r for r in research["strategies"][0]["record"]["sessions"] if r["date"] >= "2027-01-20"]
    assert row["sessions_done"] == len(after) < 100 and row["verdict"] == "IN PROGRESS"


def test_only_inc01_sessions_before_approval_is_not_started(tmp_path):
    row = _row(_bodies(40), path=_experiment(tmp_path, "Approved-by: the owner, 2030-01-01\n"))
    assert row["verdict"] == "NOT STARTED" and row["sessions_done"] == 0 and row["reason"] == "no INC-02 fill yet"


def test_an_approval_line_without_a_date_is_not_started_and_says_so(tmp_path):
    (tmp_path / "preregistration.md").write_text("Approved-by: the owner\n")
    row = _row(_bodies(40), path=tmp_path / "roster.yaml")
    assert row["verdict"] == "NOT STARTED" and row["sessions_done"] == 0
    assert row["reason"] == "INC-02's approval line carries no date"


def test_an_approval_with_an_empty_flatten_line_waits_for_the_flatten(tmp_path):
    """2026-11-27, Roster-6(a): a pre-registration with the flatten line and no date reads NOT STARTED, however many
    fills there are -- the flatten's own fills are not INC-02's first fill."""
    path = _experiment(tmp_path, "Approved-by: the owner, 2027-01-02\n\nRoster-6(a) flatten completed:\n")
    row = _row(_bodies(120), path=path)
    assert row["verdict"] == "NOT STARTED" and row["sessions_done"] == 0
    assert row["reason"] == "INC-02 waits for Roster-6(a)'s flatten of INC-01's carried positions"


def test_the_series_starts_at_the_first_fill_after_the_recorded_flatten(tmp_path):
    """The flatten's session (a fill, positive turnover) and every session up to it are excluded; the series starts
    at the first fill the day after it, never on the flatten itself."""
    research, closes, rf, last = _bodies(120)
    rec = research["strategies"][0]["record"]["sessions"]
    flatten_day = rec[9]["date"]                       # the tenth session: INC-01's flatten, a fill
    path = _experiment(tmp_path, f"Approved-by: the owner, 2027-01-02\n\nRoster-6(a) flatten completed: {flatten_day}\n")
    row = _row((research, closes, rf, last), path=path)
    assert row["sessions_done"] == 110
    from ohcamel_quant.signals.roster import recorded_flatten

    assert recorded_flatten("Roster-6(a) flatten completed:\n") == (True, None)
    assert recorded_flatten("no such line\n") == (False, None)
    assert recorded_flatten(f"`Roster-6(a) flatten completed: {flatten_day}`") == (True, flatten_day)


# ------------------------------------------- 2026-11-27: every one of the 500 sessions is asked for
def _engine_parts():
    from test_signals_report import (  # noqa: PLC0415  (tests/ is on sys.path)
        _ctx,
        _Engine,
        _full,
        _ok,
    )

    return _ctx, _Engine, _full, _ok


def _route_like_the_desk(closes):
    """/api/desk/sessions as desk/desk_routes.ml answers it: the newest 250 without a parameter, every close on or
    after ``since`` with one."""
    from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

    def answer(target: str):
        q = parse_qs(urlsplit(target).query)
        rows = [c for c in closes if c["date"] >= q["since"][0]] if "since" in q else closes[-250:]
        return (200, json.dumps(rows).encode(), 0.0)

    return answer


def test_the_report_asks_the_sessions_route_since_the_series_floor(tmp_path, monkeypatch):
    _ctx, _Engine, _full, _ok = _engine_parts()
    monkeypatch.setattr(report, "recorded_approval", lambda text: "2026-11-27")
    monkeypatch.setattr(report, "recorded_flatten", lambda text: (True, "2026-12-01"))
    with _Engine(_full()) as eng:
        report.run({}, _ctx(tmp_path, eng.url))
    # the floor is the day after the flatten, 2026-12-02; the request reaches 31 days before it for the prior close
    assert "/api/desk/sessions?since=2026-11-01" in eng.asked
    assert report._sessions_since(report.ROSTER_PATH) == "2026-11-01"
    # approved after the flatten: the approval date is the floor
    monkeypatch.setattr(report, "recorded_approval", lambda text: "2026-12-10")
    assert report._sessions_since(report.ROSTER_PATH) == "2026-11-09"
    # no floor yet (the flatten line names no date; or no approval): the route's default, no parameter
    for approval, flatten in (("2026-11-27", (True, None)), (None, (False, None))):
        monkeypatch.setattr(report, "recorded_approval", lambda text, a=approval: a)
        monkeypatch.setattr(report, "recorded_flatten", lambda text, f=flatten: f)
        with _Engine(_full()) as eng:
            report.run({}, _ctx(tmp_path, eng.url))
        assert "/api/desk/sessions" in eng.asked and not any("since" in a for a in eng.asked)


def test_a_500_session_window_counts_500_through_the_served_route(tmp_path, monkeypatch):
    """The route serves the newest 250 by default; the report asks with since=, so all 520 recorded closes arrive
    and the test counts its 500 (the offline rate is unavailable, so the evaluation is INSUFFICIENT DATA)."""
    _ctx, _Engine, _full, _ok = _engine_parts()
    (tmp_path / "inc02").mkdir()
    path = _experiment(tmp_path / "inc02", "Approved-by: the owner, 2027-01-02\n")
    monkeypatch.setattr(report, "ROSTER_PATH", path)
    research, closes, _, last = _bodies(520)
    routes = {**_full(), "/api/research": _ok(research), "/api/desk/sessions": _route_like_the_desk(closes)}
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert "/api/desk/sessions?since=2026-12-02" in eng.asked
    r = spec.tables["pooled_test"].iloc[0]
    assert r["sessions_done"] == 500 and r["verdict"] == "INSUFFICIENT DATA"
    assert r["reason"] == "500 of 500 sessions unscorable (no prior-close equity or rate)"
    assert (tmp_path / report.VERDICT_FILE).exists()             # run() stores on the job's data volume
    # the defect this replaces: without since= the same engine serves 250, and the count stalls there
    monkeypatch.setattr(report, "_sessions_since", lambda roster_path: None)
    (tmp_path / report.VERDICT_FILE).unlink()
    with _Engine(routes) as eng:
        spec = report.run({}, _ctx(tmp_path, eng.url))
    assert spec.tables["pooled_test"].iloc[0]["sessions_done"] == 250


# ------------------------------------------- 2026-11-27: a decided verdict is stored once and never flips
def _decide(approved, monkeypatch, store, *, psr=0.96, n=500, body=None):
    monkeypatch.setattr(report, "psr_and_se", lambda x: (psr, 0.9))
    research, closes, rf, last = body or _bodies(n)
    notes: list[str] = []
    [row] = report._pooled_test(research, closes, rf, date.fromisoformat(last), notes, roster_path=approved,
                                store=store)
    return row, notes


def test_a_decision_is_stored_once_and_served_unchanged_when_the_inputs_change(approved, monkeypatch, tmp_path):
    store = tmp_path / "data" / report.VERDICT_FILE
    monkeypatch.setenv("OHCAMEL_GIT_SHA", "abc1234")
    first, notes = _decide(approved, monkeypatch, store)
    assert (first["verdict"], first["psr"], first["sessions_done"]) == ("REJECTED NULL", 0.96, 500)
    rec = json.loads(store.read_text())
    days = _days(501)
    assert (rec["verdict"], rec["psr"], rec["sharpe_se"], rec["sessions_done"]) == ("REJECTED NULL", 0.96, 0.9, 500)
    assert (rec["first_fill"], rec["decided_on"], rec["code_sha"]) == (days[1], days[-1], "abc1234")
    assert rec["row"] == first and any("stored" in n for n in notes)

    # the next night: the rate fetch fails and the PSR would now read 0.5 -- nothing is recomputed
    def no_rate():
        raise AssertionError("a stored decision must not look up a rate")

    research, closes, _, last = _bodies(560)
    closes[200] = {**closes[200], "equity_close": None}
    again, notes = _decide(approved, monkeypatch, store, psr=0.5, body=(research, closes, no_rate, last))
    assert {k: again[k] for k in again if k != "reason"} == {k: first[k] for k in first if k != "reason"}
    assert again["reason"] == f"the stored decision of {days[-1]} (first fill {days[1]}), not recomputed"
    assert json.loads(store.read_text()) == rec                  # the file is never rewritten
    # the research body unreadable that night: still the stored row, with a note that it was not cross-checked
    notes = []
    [unread] = report._pooled_test(None, None, no_rate, date(2029, 1, 1), notes, roster_path=approved, store=store)
    assert unread["verdict"] == "REJECTED NULL" and any("served as stored" in n for n in notes)


def test_nothing_is_stored_while_the_test_is_in_progress_or_not_started(approved, monkeypatch, tmp_path):
    store = tmp_path / report.VERDICT_FILE
    row, _ = _decide(approved, monkeypatch, store, n=499)
    assert row["verdict"] == "IN PROGRESS" and not store.exists()
    (tmp_path / "x").mkdir()
    unapproved = _experiment(tmp_path / "x", "no approval\n")
    row, _ = _decide(unapproved, monkeypatch, store, n=520)
    assert row["verdict"] == "NOT STARTED" and not store.exists()
    assert list(tmp_path.glob(f".{report.VERDICT_FILE}.*")) == []


def test_the_decision_is_written_atomically(approved, monkeypatch, tmp_path):
    store = tmp_path / report.VERDICT_FILE
    real_replace = report.os.replace
    renamed: list[tuple[str, str]] = []

    def spy(src, dst):
        renamed.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(report.os, "replace", spy)
    _decide(approved, monkeypatch, store)
    [(src, dst)] = renamed
    assert dst == str(store) and Path(src).parent == tmp_path and src != dst    # a temp beside it, renamed over
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "roster.yaml" and p.suffix != ".md") == [
        report.VERDICT_FILE]
    # a rename that fails leaves no decision file and no temp file, serves the row and says it was not stored
    store.unlink()

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(report.os, "replace", broken)
    row, notes = _decide(approved, monkeypatch, store)
    assert row["verdict"] == "REJECTED NULL" and not store.exists()
    assert list(tmp_path.glob(f".{report.VERDICT_FILE}.*")) == []
    assert any("could not be stored" in n for n in notes)


def test_a_stored_decision_for_another_first_fill_is_refused_and_left_alone(approved, monkeypatch, tmp_path):
    store = tmp_path / report.VERDICT_FILE
    _decide(approved, monkeypatch, store)
    before = store.read_bytes()
    research, closes, rf, last = _bodies(520)
    for r in research["strategies"][0]["record"]["sessions"][:3]:
        r["turnover"] = 0.0                                       # the record's first fill is now the fourth session
    row, notes = _decide(approved, monkeypatch, store, body=(research, closes, rf, last))
    days = _days(521)
    assert row["verdict"] is None and row["psr"] is None and row["sharpe_se"] is None
    assert f"first fill on {days[1]}" in row["reason"] and f"first fill is {days[4]}" in row["reason"]
    assert "no verdict is published" in row["reason"] and any("stored decision" in n for n in notes)
    assert store.read_bytes() == before                           # never overwritten by a fresh evaluation


def test_an_unreadable_stored_decision_is_an_error_not_a_fresh_evaluation(approved, monkeypatch, tmp_path):
    store = tmp_path / report.VERDICT_FILE
    _decide(approved, monkeypatch, store)
    good = json.loads(store.read_text())
    for bad in ("{not json", json.dumps({**good, "verdict": "IN PROGRESS"}),
                json.dumps({**good, "psr": 0.2}), json.dumps({**good, "first_fill": "soon"}),
                json.dumps({**good, "row": {"verdict": good["verdict"]}})):
        store.write_text(bad)
        row, _ = _decide(approved, monkeypatch, store)
        assert row["verdict"] is None and "cannot be read" in row["reason"], bad
        assert store.read_text() == bad


@pytest.mark.parametrize(("scorable", "verdict"), [(474, "INSUFFICIENT DATA"), (475, "REJECTED NULL"),
                                                    (476, "REJECTED NULL")])
def test_at_500_fewer_than_475_scorable_sessions_is_insufficient_data(approved, monkeypatch, tmp_path, scorable,
                                                                      verdict):
    research, closes, rf, last = _bodies(500)
    for c in closes[:500 - scorable]:                             # no prior close: sessions 1..k unscorable
        c["equity_close"] = None
    store = tmp_path / report.VERDICT_FILE
    row, _ = _decide(approved, monkeypatch, store, body=(research, closes, rf, last))
    gap = f"{500 - scorable} of 500 sessions unscorable (no prior-close equity or rate)"
    assert (row["sessions_done"], row["verdict"], row["reason"]) == (500, verdict, gap)
    if verdict == "INSUFFICIENT DATA":
        assert row["psr"] is None and row["sharpe_se"] is None   # no statistic over far fewer than 500 sessions
    else:
        assert row["psr"] == 0.96
    assert json.loads(store.read_text())["verdict"] == verdict    # stored like any other decision


def test_the_verdict_rule_reads_the_scored_count():
    assert pooled.MIN_SCORED == 475 and pooled.DECIDED == ("REJECTED NULL", "NOT REJECTED", "INSUFFICIENT DATA")
    assert pooled.verdict_at(499, 0.99, scored=10) == "IN PROGRESS"
    assert pooled.verdict_at(500, 0.99, scored=474) == "INSUFFICIENT DATA"
    assert pooled.verdict_at(500, 0.99, scored=475) == "REJECTED NULL"
    assert pooled.verdict_at(500, 0.94, scored=500) == "NOT REJECTED"
