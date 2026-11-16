"""The Ledger is built from the system it describes (the completion, Task 5.1).

``machine.ledger.build(settings, now=...)``: its ``as_of`` is the day it is built, its prose rows come from
``ledger.yaml`` with the date each was last checked (stale past 45 days), and its computed rows are read from the
system each time: the backup's record, the desk's latest report (strategy counts by tier, the desk's switch, the
kill switch, the book's limit count), the two hosts' builds and the experiments' approval lines. Each computed row
can take every one of its states here, and a source that cannot be read says "Unknown" with the reason, never a
reassuring default. Nothing from the book but counts, state words and a tripped limit's name reaches the payload.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from lane_m_harness import publish_artifact

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers import ledger as ledger_router
from ohcamel_quant.config import REPO_ROOT, Settings
from ohcamel_quant.jobs.db import connect
from ohcamel_quant.machine import ledger
from ohcamel_quant.signals.report import BOOK_COLUMNS, PNL_COLUMNS

# Monday 2026-11-16, 15:00 UTC (10:00 New York): the same date in both.
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
SHA_A = "4b53105c0ffee0000000000000000000000000aa"
SHA_B = "cf76e7b0000000000000000000000000000000bb"
LIMITS = [{"name": "book-cap", "kind": "gross_notional", "scope": "portfolio"},
          {"name": "dd-cap", "kind": "max_drawdown", "scope": "portfolio"},
          {"name": "var-cap", "kind": "var", "scope": "portfolio"}]
# Distinctive book numbers: none may reach the payload (a size, a weight, a threshold, a position count).
CAPITAL = 0.0437
THRESHOLD = 731500
POSITIONS = 4733


def settings(tmp_path: Path, **kw) -> Settings:
    return Settings(data_dir=tmp_path, offline=True, **kw)


def strategies(tiers: dict[str, int], stopped: int = 0) -> pd.DataFrame:
    rows, i = [], 0
    for tier, n in tiers.items():
        for _ in range(n):
            rows.append({"strategy": f"s{i:02d}", "tier": tier, "sizing": tier,
                         "state": "stopped" if i < stopped else "active", "capital_fraction": CAPITAL,
                         "verdict": "accepted_incubation", "pnl": None, "pnl_note": "not on the wire until Stage 3"})
            i += 1
    return pd.DataFrame(rows, columns=PNL_COLUMNS)


def book(**over) -> pd.DataFrame:
    row = {"as_of": "2026-11-13T21:31:07Z", "positions": POSITIONS, "kill_switch": "armed", "tripped_by": None,
           "trips_on": json.dumps(["book-cap", "dd-cap", "var-cap"]), "limits": json.dumps(LIMITS),
           "desk_switch": "clear", **over}
    frame = pd.DataFrame([row], columns=[*BOOK_COLUMNS, *(k for k in row if k not in BOOK_COLUMNS)])
    frame["threshold"] = THRESHOLD   # a column the reader must never copy
    return frame


def publish_desk(tmp_path: Path, *, pnl: pd.DataFrame | None = None, summary: pd.DataFrame | None = None,
                 finished: datetime = datetime(2026, 11, 13, 21, 31, tzinfo=UTC)) -> None:
    tables = {"pnl_by_strategy": strategies({"incubation": 22}) if pnl is None else pnl,
              "book_summary": book() if summary is None else summary}
    publish_artifact(tmp_path / "jobs.sqlite", tmp_path / "artifacts", "desk.report", tables, finished=finished,
                     data_asof="2026-11-13")


def write_backup(tmp_path: Path, finished: datetime | str, *, ok: bool = True, failed: int = 0, steps: int = 6,
                 failed_steps: list[str] | None = None) -> None:
    """The record deploy/backup.sh's ohcamel-backup-status writes (its status_script's five fields)."""
    f = tmp_path / "ops" / "backup.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    stamp = finished if isinstance(finished, str) else finished.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"finished_at": stamp, "ok": ok, "failed": failed, "steps": steps}
    if failed_steps is not None:
        body["failed_steps"] = failed_steps
    f.write_text(json.dumps(body, sort_keys=True))


def prereg(root: Path, name: str, text: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "preregistration.md").write_text(text)


APPROVED_Q = "# EXP-Q01\n\n**Status: APPROVED 2026-10-06.** Drafted.\n\n## Approval\n\n`Approved-by:` owner (someone), 2026-10-06, as written.\n"
APPROVED_K = "# EXP-K01\n\n**Status: APPROVED by the owner on 2026-10-19, as written.** The engineering session drafted it.\n"
PENDING = "# INC-02\n\n**Status: DRAFT, awaiting the owner's approval.**\n\n## Approval\n\n(not yet)\n"


@pytest.fixture
def experiments(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("experiments")
    prereg(root, "EXP-Q01", APPROVED_Q)
    prereg(root, "EXP-K01", APPROVED_K)
    (root / "FACTORS").mkdir()                     # a directory with no pre-registration is not an experiment
    return root


def build(tmp_path: Path, *, engine=(SHA_A, None), own=SHA_A, experiments: Path | None = None,
          static: Path | None = None, now: datetime = NOW, **kw) -> ledger.Ledger:
    return ledger.build(settings(tmp_path, **kw), now=now, engine_build=lambda _s: engine, own_build=lambda: own,
                        experiments=experiments if experiments is not None else REPO_ROOT / "research" / "experiments",
                        static=static or ledger.STATIC)


def rows(led: ledger.Ledger, section: str | None = None) -> list[ledger.Row]:
    return [r for r in led.rows if section is None or r.section == section]


def item(led: ledger.Ledger, *names: str) -> ledger.Row:
    found = [r for r in led.rows if r.item in names]
    assert len(found) == 1, (names, [r.item for r in led.rows])
    return found[0]


def items(led: ledger.Ledger) -> list[str]:
    return [r.item for r in led.rows]


# ------------------------------------------------------------------ the date and the prose rows
def test_as_of_is_the_day_it_is_built(tmp_path):
    assert build(tmp_path).as_of == date(2026, 11, 16)
    late = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)          # 21:00 New York on the 16th
    assert build(tmp_path, now=late).as_of == date(2026, 11, 16)


def test_a_static_row_checked_more_than_45_days_ago_is_stale(tmp_path):
    static = tmp_path / "ledger.yaml"
    static.write_text(
        "rows:\n"
        "  - {section: CUT, item: OLD, detail: Checked long ago., checked: 2026-09-17}\n"     # 60 days before
        "  - {section: CUT, item: EDGE, detail: Checked 45 days ago., checked: 2026-10-02}\n"
        "  - {section: CUT, item: NEW, detail: Checked recently., checked: 2026-11-06}\n")
    led = build(tmp_path, static=static)
    assert item(led, "OLD").stale is True and item(led, "OLD").checked == "2026-09-17"
    assert item(led, "EDGE").stale is False and item(led, "NEW").stale is False
    assert all(r.stale is False for r in led.rows if r.state != "static")


def test_a_static_row_without_a_checked_date_is_refused(tmp_path):
    static = tmp_path / "ledger.yaml"
    static.write_text("rows:\n  - {section: CUT, item: UNDATED, detail: When was this checked.}\n")
    with pytest.raises(ValueError, match="UNDATED"):
        build(tmp_path, static=static)


def test_every_section_has_rows_and_each_item_is_once(tmp_path, experiments):
    publish_desk(tmp_path)
    write_backup(tmp_path, NOW - 5 * HOUR)
    led = build(tmp_path, experiments=experiments)
    for s in ledger.SECTIONS:
        assert rows(led, s), s
    keys = [(r.section, r.item) for r in led.rows]
    assert len(set(keys)) == len(keys)


# ------------------------------------------------------------------ backups
def test_a_fresh_backup_record_gives_no_paused_row(tmp_path):
    write_backup(tmp_path, NOW - 8 * HOUR)
    led = build(tmp_path)
    assert "BACKUPS PAUSED" not in items(led)
    r = item(led, "BACKUPS NIGHTLY")
    assert r.section == "LIMITS" and r.state == "ok" and "8 hours ago" in r.detail


def test_a_three_day_old_backup_record_is_a_paused_row(tmp_path):
    write_backup(tmp_path, NOW - timedelta(days=3, hours=2))
    r = item(build(tmp_path), "BACKUPS PAUSED")
    assert r.section == "LIMITS" and r.state == "attention" and "3 days" in r.detail
    assert "BACKUPS NIGHTLY" not in items(build(tmp_path))


def test_a_failed_backup_is_its_own_row(tmp_path):
    write_backup(tmp_path, NOW - 7 * HOUR, ok=False, failed=1, steps=6)
    r = item(build(tmp_path), "BACKUP FAILED")
    assert r.state == "attention" and "1 of its 6 steps" in r.detail


@pytest.mark.parametrize(("names", "words"), [
    (["recorder"], "failed at the recorder (1 of its 6 steps)"),
    (["desk", "book"], "failed at the desk's journal and book.sexp (2 of its 6 steps)"),
    (["backup-dir"], "failed at the backup directory"),
])
def test_a_failed_run_record_names_the_step(tmp_path, names, words):
    failed = 6 if names == ["backup-dir"] else len(names)
    write_backup(tmp_path, NOW - 7 * HOUR, ok=False, failed=failed, steps=6, failed_steps=names)
    led = build(tmp_path)
    r = item(led, "BACKUP FAILED")
    assert r.state == "attention" and words in r.detail
    assert "BACKUPS NIGHTLY" not in items(led) and "BACKUPS PAUSED" not in items(led)


def test_a_stale_failed_record_is_paused_and_says_what_failed(tmp_path):
    write_backup(tmp_path, NOW - timedelta(days=3), ok=False, failed=1, steps=6, failed_steps=["jobs"])
    r = item(build(tmp_path), "BACKUPS PAUSED")
    assert "3 days" in r.detail and "failed at jobs.sqlite" in r.detail


def test_the_record_deploy_backup_sh_writes_is_read(tmp_path):
    """The record exactly as deploy/backup.sh's status_script writes it, run with this python."""
    import subprocess

    script = (REPO_ROOT / "deploy" / "backup.sh").read_text()
    body = script[script.index("status_script='") + len("status_script='"):]
    body = body[:body.index("\n'\n")]
    subprocess.run(["python3", "-c", body, str(tmp_path / "ops"), "2026-11-16T06:31:00Z", "false", "1", "6", "recorder"],
                   check=True)
    r = item(build(tmp_path), "BACKUP FAILED")
    assert "failed at the recorder (1 of its 6 steps)" in r.detail and "8 hours ago" in r.detail


@pytest.mark.parametrize(("setup", "why"), [
    (None, "no record of the nightly backup is in this server's volume"),
    ("{not json", "could not be read"),
    ('{"finished_at": "yesterday", "ok": true}', "could not be read"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": "yes", "failed": 0}', "could not be read"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": true}', "could not be read"),
    ('{"finished_at": "2026-11-16T18:00:00Z", "ok": true, "failed": 0}', "ahead of this server's clock"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": false, "failed": 1, "failed_steps": ["/var/backups/x.db"]}',
     "could not be read"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": true, "failed": 2, "steps": 6}', "contradicts itself"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": false, "failed": 0, "steps": 6}', "contradicts itself"),
    ('{"finished_at": "2026-11-16T14:00:00Z", "ok": true, "failed": 0, "steps": 6, "failed_steps": ["jobs"]}',
     "contradicts itself"),
])
def test_a_backup_record_that_is_missing_or_unreadable_is_unknown(tmp_path, setup, why):
    if setup is not None:
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "backup.json").write_text(setup)
    r = item(build(tmp_path), "BACKUP STATE UNKNOWN")
    assert r.state == "unknown" and r.detail.startswith("Unknown:") and why in r.detail


# ------------------------------------------------------------------ the desk: strategies by tier
def test_twenty_two_incubation_strategies_are_said_and_nothing_claims_no_trading(tmp_path):
    publish_desk(tmp_path, pnl=strategies({"incubation": 22}))
    led = build(tmp_path)
    r = item(led, "NO LIVE STRATEGY")
    assert r.section == "ADVISORY" and r.state == "ok"
    assert "22 strategies are registered on the paper book" in r.detail and "22 incubation" in r.detail
    assert "None is live" in r.detail
    assert "desk.report" in r.source and "2026-11-13" in r.source
    text = json.dumps(led.to_dict())
    assert "No strategy on this site trades" not in text


def test_live_and_stopped_strategies_are_counted(tmp_path):
    publish_desk(tmp_path, pnl=strategies({"incubation": 20, "live": 2, "advisory": 1}, stopped=3))
    r = item(build(tmp_path), "LIVE STRATEGIES")
    assert "23 strategies" in r.detail and "20 incubation, 2 live, 1 advisory" in r.detail
    assert "3 stopped" in r.detail and "None is live" not in r.detail


def test_a_report_with_no_strategy_says_so(tmp_path):
    publish_desk(tmp_path, pnl=strategies({}))
    r = item(build(tmp_path), "NO STRATEGY REGISTERED")
    assert r.state == "ok" and "lists no registered strategy" in r.detail


def test_a_report_that_could_not_read_the_desk_is_unknown_not_empty(tmp_path):
    # the engine was unreachable at 16:30: every table empty, and that is not "no strategy"
    publish_desk(tmp_path, pnl=strategies({}), summary=pd.DataFrame(columns=BOOK_COLUMNS))
    led = build(tmp_path)
    assert item(led, "DESK STATE UNKNOWN").detail == "Unknown: the latest desk report could not read the live desk."
    assert item(led, "DESK SWITCH UNKNOWN").state == "unknown"
    assert "NO STRATEGY REGISTERED" not in items(led)


def test_no_jobs_database_is_unknown_and_none_is_created(tmp_path):
    led = build(tmp_path)
    r = item(led, "DESK STATE UNKNOWN")
    assert r.state == "unknown" and "job system has not started" in r.detail
    assert not (tmp_path / "jobs.sqlite").exists()
    assert item(led, "BOOK LIMITS").state == "unknown"


def test_no_desk_report_yet_is_unknown(tmp_path):
    connect(tmp_path / "jobs.sqlite").close()
    assert "no desk report has been published yet" in item(build(tmp_path), "DESK STATE UNKNOWN").detail


def test_a_stale_desk_report_is_unknown(tmp_path):
    publish_desk(tmp_path, finished=datetime(2026, 11, 5, 21, 31, tzinfo=UTC))   # sessions have been missed since
    led = build(tmp_path)
    r = item(led, "DESK STATE UNKNOWN")
    assert "missed since" in r.detail and "2026-11-05" in r.detail
    assert item(led, "DESK SWITCH UNKNOWN").state == "unknown"


def test_a_report_without_the_roster_table_is_unknown(tmp_path):
    publish_artifact(tmp_path / "jobs.sqlite", tmp_path / "artifacts", "desk.report", {"book_summary": book()},
                     finished=datetime(2026, 11, 13, 21, 31, tzinfo=UTC))
    led = build(tmp_path)
    assert "does not carry the strategy roster" in item(led, "DESK STATE UNKNOWN").detail
    assert item(led, "DESK CLEAR").state == "ok"   # the summary still reads


# ------------------------------------------------------------------ the desk's switch and the kill switch
@pytest.mark.parametrize(("over", "name", "state", "words"), [
    ({"desk_switch": "clear"}, "DESK CLEAR", "ok", ["trade on the paper book", "kill switch is armed"]),
    ({"desk_switch": "halted"}, "DESK HALTED", "attention", ["halted by hand", "until the owner lifts"]),
    ({"desk_switch": "tripped", "kill_switch": "halted", "tripped_by": "dd-cap"}, "DESK TRIPPED", "attention",
     ["dd-cap", "kill switch has tripped"]),
    ({"desk_switch": "stopped"}, "DESK STOPPED", "attention", ["engine has stopped the desk"]),
    ({"desk_switch": None}, "DESK SWITCH UNKNOWN", "unknown", ["does not carry the desk's switch"]),
    ({"desk_switch": "clear", "kill_switch": "disabled"}, "DESK CLEAR", "attention", ["kill switch is disabled"]),
    ({"desk_switch": "clear", "kill_switch": None}, "DESK CLEAR", "unknown", ["kill switch's state was not read"]),
    ({"desk_switch": "clear", "kill_switch": "halted", "tripped_by": "var-cap"}, "KILL SWITCH TRIPPED", "attention",
     ["tripped on var-cap", "disagree"]),
])
def test_the_switch_row_takes_each_state(tmp_path, over, name, state, words):
    publish_desk(tmp_path, summary=book(**over))
    r = item(build(tmp_path), name)
    assert r.section == "ADVISORY" and r.state == state
    for w in words:
        assert w in r.detail
    assert "book_summary" in r.source


CLAIM = "trade on the paper book"


@pytest.mark.parametrize("kill", ["armed", "disabled", "halted", None, "sideways"])
@pytest.mark.parametrize("desk", ["clear", "tripped", "halted", "stopped", None, "sideways"])
def test_no_switch_combination_claims_trading_unless_both_are_clear(tmp_path, desk, kill):
    publish_desk(tmp_path, summary=book(desk_switch=desk, kill_switch=kill,
                                        tripped_by="dd-cap" if kill == "halted" else None))
    led = build(tmp_path)
    r = next(x for x in led.rows if x.section == "ADVISORY" and x.source.endswith("book_summary"))
    both_clear = desk == "clear" and kill in ("armed", "disabled")
    assert (CLAIM in r.detail) == both_clear, (desk, kill, r.detail)
    assert (r.state == "ok") == (desk == "clear" and kill == "armed"), (desk, kill, r.state)
    if kill == "halted" or desk in ("tripped", "halted", "stopped"):
        assert r.state == "attention"
    if desk not in ("clear", "tripped", "halted", "stopped") and kill != "halted":
        assert r.state == "unknown"   # a known trip outranks an unread desk switch


def test_a_report_from_before_the_desk_switch_column_is_unknown(tmp_path):
    old = book().drop(columns=["desk_switch"])
    publish_desk(tmp_path, summary=old)
    r = item(build(tmp_path), "DESK SWITCH UNKNOWN")
    assert r.detail.startswith("Unknown: the latest desk report does not carry the desk's switch")


def test_an_unknown_switch_word_is_unknown_not_clear(tmp_path):
    publish_desk(tmp_path, summary=book(desk_switch="sideways"))
    assert item(build(tmp_path), "DESK SWITCH UNKNOWN").state == "unknown"


# ------------------------------------------------------------------ the book's limits (owner)
def test_book_limits_are_counted_never_quoted(tmp_path):
    publish_desk(tmp_path)
    r = item(build(tmp_path), "BOOK LIMITS")
    assert r.section == "OWNER" and r.state == "ok"
    assert "3 limits" in r.detail and "trips on 3 of them" in r.detail and "not published" in r.detail


def test_book_limits_not_read_are_unknown(tmp_path):
    publish_desk(tmp_path, summary=book(limits=None, trips_on=None))
    r = item(build(tmp_path), "BOOK LIMITS")
    assert r.state == "unknown" and r.detail.startswith("Unknown:")


def test_a_book_with_no_limits_is_attention(tmp_path):
    publish_desk(tmp_path, summary=book(limits="[]", trips_on="[]"))
    r = item(build(tmp_path), "BOOK LIMITS")
    assert r.state == "attention" and "no limits" in r.detail


# ------------------------------------------------------------------ the hosts' builds
@pytest.mark.parametrize(("engine", "own", "name", "state", "words"), [
    ((SHA_A, None), SHA_A, "ONE BUILD ON BOTH HOSTS", "ok", ["same build"]),
    ((SHA_A[:7], None), SHA_A, "ONE BUILD ON BOTH HOSTS", "ok", ["same build"]),
    ((SHA_B, None), SHA_A, "LIVE HOST ON ANOTHER BUILD", "attention", ["different build"]),
    ((None, "the bridge to the live host is off on this server"), SHA_A, "LIVE HOST BUILD UNKNOWN", "unknown",
     ["bridge to the live host is off"]),
    ((SHA_A, None), None, "LIVE HOST BUILD UNKNOWN", "unknown", ["no build stamp"]),
])
def test_the_hosts_row_takes_each_state(tmp_path, engine, own, name, state, words):
    r = item(build(tmp_path, engine=engine, own=own), name)
    assert r.section == "LIMITS" and r.state == state
    for w in words:
        assert w in r.detail


def boom(*_a, **_k):
    raise RuntimeError("an error no handler expected")


def test_a_source_raising_unexpectedly_makes_only_its_row_unknown(tmp_path):
    publish_desk(tmp_path)
    write_backup(tmp_path, NOW - 5 * HOUR)
    good = build(tmp_path)
    bad = ledger.build(settings(tmp_path), now=NOW, engine_build=boom, own_build=lambda: SHA_A,
                       experiments=REPO_ROOT / "research" / "experiments")
    r = item(bad, "LIVE HOST BUILD UNKNOWN")
    assert r.state == "unknown" and "could not be read just now" in r.detail and "RuntimeError" not in r.detail
    assert [x for x in bad.rows if x.section != "LIMITS" or "HOST" not in x.item] == \
           [x for x in good.rows if x.section != "LIMITS" or "HOST" not in x.item]


def test_a_desk_read_raising_unexpectedly_leaves_the_rest_served(tmp_path, monkeypatch):
    publish_desk(tmp_path)
    write_backup(tmp_path, NOW - 5 * HOUR)
    monkeypatch.setattr(ledger, "load_schedules", boom)     # outside read_desk's own handlers
    led = build(tmp_path)
    for name in ("DESK STATE UNKNOWN", "DESK SWITCH UNKNOWN"):
        assert item(led, name).state == "unknown"
    assert item(led, "BOOK LIMITS").state == "unknown"
    assert item(led, "BACKUPS NIGHTLY").state == "ok" and item(led, "ONE BUILD ON BOTH HOSTS").state == "ok"
    assert item(led, "PRE-REGISTRATIONS").state == "ok"


def test_missing_values_in_the_report_do_not_break_the_rows(tmp_path):
    pnl = strategies({"incubation": 2})
    pnl["tier"] = pd.array(["incubation", pd.NA], dtype="string")
    pnl["state"] = pd.array(["active", pd.NA], dtype="string")
    summary = book()
    summary["desk_switch"] = pd.array([pd.NA], dtype="string")
    summary["kill_switch"] = pd.array([pd.NA], dtype="string")
    publish_desk(tmp_path, pnl=pnl, summary=summary)
    led = build(tmp_path)
    assert "2 strategies" in item(led, "NO LIVE STRATEGY").detail
    assert item(led, "DESK SWITCH UNKNOWN").state == "unknown"


def test_reading_the_live_hosts_build(tmp_path, monkeypatch):
    import httpx
    import respx

    ledger._engine_cache.clear()
    s = Settings(data_dir=tmp_path, engine_url="http://live.test", offline=False)
    assert ledger.read_engine_build(Settings(data_dir=tmp_path, offline=False)) == (None, "the bridge to the live host is off on this server")
    assert ledger.read_engine_build(Settings(data_dir=tmp_path, engine_url="http://x", offline=True))[1] == "this server is offline"
    with respx.mock:
        respx.get("http://live.test/api/ops").mock(return_value=httpx.Response(200, json={"build": {"git_sha": SHA_B}}))
        assert ledger.read_engine_build(s) == (SHA_B, None)
    ledger._engine_cache.clear()
    with respx.mock:
        respx.get("http://live.test/api/ops").mock(return_value=httpx.Response(200, json={"build": {"git_sha": "unknown"}}))
        assert ledger.read_engine_build(s) == (None, "the live host's image carries no build stamp")
    ledger._engine_cache.clear()
    with respx.mock:
        respx.get("http://live.test/api/ops").mock(return_value=httpx.Response(502))
        assert ledger.read_engine_build(s) == (None, "the live host answered with HTTP 502")
    ledger._engine_cache.clear()
    with respx.mock:
        respx.get("http://live.test/api/ops").mock(side_effect=httpx.ConnectError("refused"))
        assert ledger.read_engine_build(s) == (None, "the live host could not be reached")
    ledger._engine_cache.clear()
    bad = Settings(data_dir=tmp_path, engine_url="http://live.test:notaport", offline=False)
    assert ledger.read_engine_build(bad) == (None, "the bridge's URL is not valid")
    ledger._engine_cache.clear()


def test_this_servers_build_is_its_image_stamp(monkeypatch):
    monkeypatch.setenv("OHCAMEL_GIT_SHA", SHA_A)
    assert ledger.own_build() == SHA_A
    for absent in ("unknown", "", "not-a-sha"):
        monkeypatch.setenv("OHCAMEL_GIT_SHA", absent)
        assert ledger.own_build() is None


# ------------------------------------------------------------------ pre-registrations (owner)
def test_approved_preregistrations_are_listed_by_date(tmp_path, experiments):
    r = item(build(tmp_path, experiments=experiments), "PRE-REGISTRATIONS")
    assert r.section == "OWNER" and r.state == "ok"
    assert "2 approved by the owner" in r.detail
    assert "EXP-Q01 2026-10-06" in r.detail and "EXP-K01 2026-10-19" in r.detail
    assert "someone" not in r.detail                     # the approver's name is not published


def test_a_preregistration_awaiting_the_owner_is_attention(tmp_path, experiments):
    prereg(experiments, "INC-02", PENDING)
    r = item(build(tmp_path, experiments=experiments), "PRE-REGISTRATIONS")
    assert r.state == "attention" and "Awaiting the owner: INC-02" in r.detail


@pytest.mark.parametrize("make", ["empty", "missing"])
def test_no_preregistration_in_the_build_is_unknown(tmp_path, make):
    root = tmp_path / "exp"
    if make == "empty":
        root.mkdir()
    r = item(build(tmp_path, experiments=root), "PRE-REGISTRATIONS")
    assert r.state == "unknown" and r.detail.startswith("Unknown:")


def test_the_repositorys_preregistrations_all_read_as_approved(tmp_path):
    r = item(build(tmp_path), "PRE-REGISTRATIONS")
    assert r.state == "ok"
    for name in ("EXP-Q01", "EXP-Q02", "EXP-K01", "INC-01"):
        assert name in r.detail


# ------------------------------------------------------------------ what may not appear
def every_state(tmp_path: Path, experiments: Path) -> list[ledger.Ledger]:
    """One ledger per state of each computed row, as far as one store can hold them."""
    out = []
    for i, (over, engine, backup) in enumerate([
        ({"desk_switch": "clear"}, (SHA_A, None), NOW - 3 * HOUR),
        ({"desk_switch": "halted"}, (SHA_B, None), NOW - timedelta(days=4)),
        ({"desk_switch": "tripped", "kill_switch": "halted", "tripped_by": "dd-cap"}, (None, "the live host could not be reached"), None),
        ({"desk_switch": None, "limits": None}, (SHA_A[:7], None), NOW - 2 * HOUR),
    ]):
        d = tmp_path / str(i)
        d.mkdir()
        publish_desk(d, summary=book(**over), pnl=strategies({"incubation": 21, "live": 1}, stopped=2))
        if backup is not None:
            write_backup(d, backup, ok=i != 3, failed=int(i == 3))
        out.append(build(d, engine=engine, experiments=experiments))
    out.append(build(tmp_path / "empty", engine=(None, "x"), own=None, experiments=tmp_path / "none"))
    return out


COMMIT = re.compile(r"(?i)\b[0-9a-f]{7,40}\b")


def commits(led: ledger.Ledger) -> list[str]:
    """Every hex run of 7 to 40 characters anywhere in the served payload: rows, provenance and notes."""
    return COMMIT.findall(json.dumps(led.to_dict()))


def test_no_part_of_the_payload_names_a_commit(tmp_path, experiments):
    for led in every_state(tmp_path, experiments):
        assert commits(led) == [], commits(led)


@pytest.mark.parametrize("leak", [SHA_A, SHA_A[:7], SHA_B.upper(), SHA_B[:12]])
def test_the_commit_guard_catches_a_row_carrying_a_build(tmp_path, leak):
    led = build(tmp_path)
    leaky = ledger.Ledger(led.as_of, [*led.rows, ledger.Row("LIMITS", "X", f"The live host runs {leak}.", "attention")],
                          led.provenance, led.notes)
    assert commits(leaky) == [leak]
    leaky = ledger.Ledger(led.as_of, led.rows, [{"source": "x", "detail": {"sha": leak}}], led.notes)
    assert commits(leaky) == [leak]                      # provenance is scanned too


def test_no_book_number_reaches_the_payload(tmp_path, experiments):
    for led in every_state(tmp_path, experiments):
        text = json.dumps(led.to_dict())
        assert "$" not in text and '"threshold"' not in text
        for n in (str(CAPITAL), "4.37", str(THRESHOLD), "731,500", str(POSITIONS), "4,733"):
            assert n not in text, n
        for r in led.rows:
            if r.state != "static":   # a computed row's numbers are counts, ages and dates only
                for n in re.findall(r"\d+(?:[.,]\d+)?", r.detail):
                    assert "." not in n and "," not in n, (r.item, n)


def test_the_payload_shape(tmp_path):
    publish_desk(tmp_path)
    d = build(tmp_path).to_dict()
    assert d["as_of"] == "2026-11-16" and d["stale_after_days"] == 45
    assert set(d) == {"as_of", "stale_after_days", "rows", "provenance", "notes"}
    for r in d["rows"]:
        assert set(r) == {"section", "item", "detail", "state", "source", "checked", "stale", "kind", "methodology"}
        assert r["state"] in {"static", "ok", "attention", "unknown"}
        assert (r["checked"] is None) == (r["state"] != "static")
    assert [s for s in ledger.SECTIONS] == ["BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER"]


# ------------------------------------------------------------------ the prose file
WEB = REPO_ROOT / "quant" / "web" / "src"


def test_the_prose_file_ties_each_product_to_its_kind_and_methodology(tmp_path):
    static = [r for r in build(tmp_path).rows if r.state == "static"]
    products = [r for r in static if re.match(r"^(P[1-7]|WATCH) ", r.item)]
    kinds = set(re.findall(r'^\s+\w+: "([a-z_.]+)",$', (WEB / "lib" / "artifacts.ts").read_text().split("} as const")[0], re.M))
    assert len(products) == 10 and {r.kind for r in products} == kinds
    assert all(r.kind is None for r in static if r not in products)
    models = (WEB / "pages" / "methodology" / "models.ts").read_text()
    for r in products:
        assert f'id: "{r.methodology}"' in models, r.item


def test_the_prose_file_counts_the_kernels_and_lists_every_cut(tmp_path):
    static = {r.item: r for r in build(tmp_path).rows if r.state == "static"}
    kernels = (WEB / "pages" / "methodology" / "compute.ts").read_text()
    api = kernels[kernels.index("export const KERNEL_API"):]
    assert len(re.findall(r'^\s+"[a-z_]+",$', api[:api.index("] as const")], re.M)) == 12
    assert static["KERNELS"].detail.startswith("Twelve Rust kernels")
    for cut in ("ENGINE DEPTH", "P8 SCENARIO GRID", "C STUBS", "KIOSK MODE", "SAVED LAYOUTS", "DENSITY MODES"):
        assert static[cut].section == "CUT"


def test_the_prose_file_holds_no_state_the_system_computes(tmp_path):
    static = [r for r in build(tmp_path).rows if r.state == "static"]
    text = "\n".join(f"{r.item} {r.detail}" for r in static)
    for gone in ("NO LIVE STRATEGY", "BACKUPS PAUSED", "LIVE HOST BEHIND", "BOOK LIMITS", "PRE-REGISTRATIONS",
                 "No strategy on this site trades", "Twenty-three", "$", "public host"):
        assert gone not in text, gone
    assert not any(r.detail.strip().endswith("?") for r in static)


# ------------------------------------------------------------------ the route
def test_get_api_ledger(tmp_path, monkeypatch):
    publish_desk(tmp_path)
    write_backup(tmp_path, NOW - 5 * HOUR)
    app = create_app()
    app.dependency_overrides[ledger_router.get_ledger_settings] = lambda: settings(tmp_path)
    monkeypatch.setattr(ledger_router, "utcnow", lambda: NOW)
    with TestClient(app) as client:
        r = client.get("/api/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-11-16"
    assert {x["section"] for x in body["rows"]} == set(ledger.SECTIONS)
    assert any(x["item"] == "NO LIVE STRATEGY" for x in body["rows"])
    assert body["provenance"] and body["notes"]


def test_the_route_never_creates_the_jobs_database(tmp_path, monkeypatch):
    app = create_app()
    app.dependency_overrides[ledger_router.get_ledger_settings] = lambda: settings(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/ledger").status_code == 200
    assert not (tmp_path / "jobs.sqlite").exists()


@pytest.mark.parametrize("prose", ["rows: [ {section: CUT", "rows:\n  - {section: CUT, item: UNDATED, detail: x.}\n"])
def test_a_broken_prose_file_is_a_fixed_503(tmp_path, monkeypatch, prose):
    bad = tmp_path / "ledger.yaml"
    bad.write_text(prose)
    monkeypatch.setattr(ledger, "STATIC", bad)
    app = create_app()
    app.dependency_overrides[ledger_router.get_ledger_settings] = lambda: settings(tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/ledger")
    assert r.status_code == 503
    assert r.json() == {"error": "ledger_unavailable", "detail": "the ledger could not be built just now"}
