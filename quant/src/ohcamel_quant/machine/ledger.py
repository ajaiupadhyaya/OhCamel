"""The Ledger, built from the system it describes (``GET /api/ledger``, the ``/ledger`` page).

``build(settings, now=...)`` answers a ``Ledger``: its ``as_of`` is the New York date it is built on, and its
rows are of two kinds.

* **Prose**, from ``ledger.yaml`` beside this file: what was built, what was cut, and the advisory, limit and
  owner rows that are statements rather than state. Each carries ``checked``, the date it was last checked
  against the code, and is ``stale`` when that is more than ``STALE_AFTER_DAYS`` before ``as_of``.
* **Computed**, read from the system on every build, each with a ``source`` naming where:

  - LIMITS, the nightly backup: the record ``{data_dir}/ops/backup.json`` (``BACKUP_RECORD``) that
    ``deploy/backup.sh`` writes at the end of every run, on success and on failure, through its
    ``ohcamel-backup-status`` container: fresh, paused, failed (naming the steps), or unknown. A host whose
    backup has not yet written one says it cannot see the backup.
  - LIMITS, the hosts' builds: this server's image stamp (``OHCAMEL_GIT_SHA``) against the live engine's
    ``/api/ops`` ``build.git_sha``, read through the engine bridge's URL (``OHCAMEL_QUANT_ENGINE_URL``). Words
    only: no row names a commit (``/system`` and the footer do).
  - ADVISORY, the strategies by tier and the desk's switch, and OWNER, the book's limits: the latest
    ``desk.report`` artifact's ``pnl_by_strategy`` and ``book_summary``. Only counts, state words and a tripped
    limit's name are read out of them: never a strategy's name or capital, a position, or a threshold.
  - OWNER, the pre-registrations: each ``research/experiments/*/preregistration.md`` packaged in the image,
    approved (with the date its approval line gives) or awaiting the owner. The approver's name is not read.

A source that is missing, unreadable, stale or ambiguous makes its row say ``Unknown:`` and why, never a
default. Nothing here writes: a host with no ``jobs.sqlite`` stays without one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pyarrow as pa
import yaml

from ..config import REPO_ROOT, Settings
from ..data.base import Provenance
from ..jobs.artifacts import TABLE_NAME, inside
from ..jobs.db import connect, parse_iso
from ..jobs.paths import jobs_db_path
from ..jobs.schedules import load_schedules, read_latest
from ..signals.roster import recorded_approval
from .clock import NY
from .load import missed_fire

log = logging.getLogger("ohcamel_quant.machine.ledger")

SECTIONS = ("BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER")
STATES = ("static", "ok", "attention", "unknown")
STATIC = Path(__file__).with_name("ledger.yaml")
EXPERIMENTS = REPO_ROOT / "research" / "experiments"
BACKUP_RECORD = Path("ops") / "backup.json"
STALE_AFTER_DAYS = 45
BACKUP_STALE = timedelta(hours=36)    # the timer fires daily at 06:30 UTC: one late night is slack, two is a pause
CLOCK_SKEW = timedelta(minutes=5)     # a record dated further ahead than this is not believed
ENGINE_PATH = "/api/ops"
ENGINE_TIMEOUT = httpx.Timeout(2.0, connect=0.5)
ENGINE_TTL_S = 30.0
DESK = "desk.report"
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_EXPERIMENT = re.compile(r"^[A-Z][A-Z0-9-]{1,31}$")
_TIERS = ("incubation", "live", "advisory")
_DESK_SWITCH = ("clear", "tripped", "halted", "stopped")
_KILL = ("disabled", "armed", "halted")


@dataclass(frozen=True)
class Row:
    section: str
    item: str
    detail: str
    state: str                    # static | ok | attention | unknown
    source: str = ""
    checked: str | None = None    # prose rows: the ISO date last checked
    stale: bool = False
    kind: str | None = None       # products: the job kind whose latest artifact the page reads
    methodology: str | None = None


@dataclass(frozen=True)
class Ledger:
    as_of: date
    rows: list[Row]
    provenance: list[dict]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "stale_after_days": STALE_AFTER_DAYS,
                "rows": [asdict(r) for r in self.rows], "provenance": self.provenance, "notes": self.notes}


def _unknown(section: str, item: str, why: str, source: str) -> Row:
    return Row(section, item, f"Unknown: {why}.", "unknown", source)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _age(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "under an hour"
    return _plural(hours, "hour") if hours < 48 else _plural(hours // 24, "day")


# ------------------------------------------------------------------ prose
def load_static(path: Path, as_of: date) -> list[Row]:
    """The prose rows, in file order. A row with no section, item, detail or ``checked`` date is refused: an
    undated statement is exactly what this page stopped publishing."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for raw in doc.get("rows") or []:
        name = raw.get("item") if isinstance(raw, dict) else None
        checked = raw.get("checked") if isinstance(raw, dict) else None
        if not (isinstance(name, str) and raw.get("section") in SECTIONS and isinstance(raw.get("detail"), str)
                and isinstance(checked, date)):
            raise ValueError(f"{path.name}: row {name or raw!r} needs a section, an item, a detail and a checked date")
        out.append(Row(raw["section"], name, raw["detail"].strip(), "static", "", checked.isoformat(),
                       (as_of - checked).days > STALE_AFTER_DAYS, raw.get("kind"), raw.get("methodology")))
    return out


# ------------------------------------------------------------------ the backup
# deploy/backup.sh names a step by its copy's stem (desk, recorder, jobs, option-snapshots, warehouse, book, deploy-env), and
# a refused backup directory as backup-dir.
_STEP = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_STEP_WORDS = {"backup-dir": "the backup directory", "desk": "the desk's journal", "recorder": "the recorder",
               "jobs": "jobs.sqlite", "option-snapshots": "the option snapshots", "book": "book.sexp",
               "deploy-env": "the deploy settings", "warehouse": "the warehouse export"}


def _failure(failed: Any, steps: Any, names: list[str]) -> str:
    """What failed, in words: the steps by name when the record gives them, with the count of all its steps."""
    counted = isinstance(failed, int) and isinstance(steps, int) and 0 < failed <= steps
    named = [_STEP_WORDS.get(n, n) for n in names]
    which = (", ".join(named[:-1]) + " and " + named[-1]) if len(named) > 1 else (named[0] if named else "")
    if which and counted:
        return f"failed at {which} ({failed} of its {steps} steps)"
    if which:
        return f"failed at {which}"
    return f"failed {failed} of its {steps} steps" if counted else "failed"


def backup_row(settings: Settings, now: datetime) -> Row:
    source = "the backup's record · ops/backup.json"
    f = Path(settings.data_dir) / BACKUP_RECORD
    if not f.exists():
        return _unknown("LIMITS", "BACKUP STATE UNKNOWN",
                        "no record of the nightly backup is in this server's volume; the backup runs on the host, so "
                        "this page cannot see whether last night's ran", source)
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        finished = parse_iso(rec["finished_at"])
        ok, failed, steps = rec["ok"], rec["failed"], rec.get("steps")
        names = rec.get("failed_steps", [])
        if not isinstance(ok, bool) or finished.tzinfo is None or not isinstance(names, list) \
                or not all(isinstance(x, str) and _STEP.match(x) for x in names) \
                or not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
            raise ValueError("malformed")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("the backup record could not be read", exc_info=True)
        return _unknown("LIMITS", "BACKUP STATE UNKNOWN", "the backup's record on this server could not be read", source)
    if ok != (failed == 0) or (ok and names):
        return _unknown("LIMITS", "BACKUP STATE UNKNOWN", "the backup's record contradicts itself: its ok and its "
                        "failed steps disagree", source)
    age = now - finished
    if age < -CLOCK_SKEW:
        return _unknown("LIMITS", "BACKUP STATE UNKNOWN", "the backup's record is dated ahead of this server's clock",
                        source)
    what = _failure(failed, steps, names)
    if age > BACKUP_STALE:
        tail = f", and it {what}" if not ok else ""
        return Row("LIMITS", "BACKUPS PAUSED", f"No nightly backup has finished for {_age(age)}: the last finished on "
                   f"{finished.astimezone(UTC).date().isoformat()}{tail}.", "attention", source)
    if not ok:
        return Row("LIMITS", "BACKUP FAILED", f"The nightly backup {_age(age)} ago {what}; that night's copies are "
                   "not to be trusted.", "attention", source)
    return Row("LIMITS", "BACKUPS NIGHTLY", f"The nightly backup last finished cleanly {_age(max(age, timedelta(0)))} ago.",
               "ok", source)


# ------------------------------------------------------------------ the hosts' builds
_engine_cache: dict[str, tuple[float, tuple[str | None, str | None]]] = {}


def own_build() -> str | None:
    """This server's build: its image's ``OHCAMEL_GIT_SHA`` (the rule ``/api/ops`` uses), None when unstamped."""
    sha = (os.environ.get("OHCAMEL_GIT_SHA") or "").strip().lower()
    return sha if _SHA.match(sha) else None


def read_engine_build(settings: Settings) -> tuple[str | None, str | None]:
    """The live engine's build sha from its ``/api/ops``, or None and why, in words. One fixed GET through the
    bridge's URL, short timeouts, cached ``ENGINE_TTL_S`` (an answer and a failure alike)."""
    base = (settings.engine_url or "").strip().rstrip("/")
    if not base:
        return None, "the bridge to the live host is off on this server"
    if settings.offline:
        return None, "this server is offline"
    hit = _engine_cache.get(base)
    if hit is not None and time.monotonic() - hit[0] < ENGINE_TTL_S:
        return hit[1]
    got: tuple[str | None, str | None]
    try:
        with httpx.Client(timeout=ENGINE_TIMEOUT, follow_redirects=False) as client:
            resp = client.get(f"{base}{ENGINE_PATH}", headers={"Accept": "application/json"})
        if resp.status_code != 200:
            got = None, f"the live host answered with HTTP {resp.status_code}"
        else:
            body = resp.json()
            build = body.get("build") if isinstance(body, dict) else None
            sha = build.get("git_sha") if isinstance(build, dict) else None
            sha = sha.strip().lower() if isinstance(sha, str) else ""
            got = (sha, None) if _SHA.match(sha) else (None, "the live host's image carries no build stamp")
    except httpx.TimeoutException:
        got = None, "the live host did not answer within 2 s"
    except httpx.InvalidURL:
        got = None, "the bridge's URL is not valid"
    except httpx.HTTPError:
        got = None, "the live host could not be reached"
    except ValueError:
        got = None, "the live host's answer could not be read"
    _engine_cache[base] = (time.monotonic(), got)
    return got


def hosts_row(own: str | None, engine: tuple[str | None, str | None]) -> Row:
    source = "this image's OHCAMEL_GIT_SHA · the live engine's /api/ops build"
    sha, why = engine
    if own is None:
        return _unknown("LIMITS", "LIVE HOST BUILD UNKNOWN", "this server's image carries no build stamp to compare",
                        source)
    if sha is None:
        return _unknown("LIMITS", "LIVE HOST BUILD UNKNOWN", why or "the live host's build was not read", source)
    if own.startswith(sha) or sha.startswith(own):
        return Row("LIMITS", "ONE BUILD ON BOTH HOSTS", "The public host and the live host run the same build.", "ok",
                   source)
    return Row("LIMITS", "LIVE HOST ON ANOTHER BUILD", "The live host runs a different build from the public host.",
               "attention", source)


# ------------------------------------------------------------------ the desk's latest report
@dataclass(frozen=True)
class Desk:
    why: str | None = None                 # why there is nothing to read, as the tail of a sentence
    asof: str | None = None
    pnl: pd.DataFrame | None = None
    book: pd.DataFrame | None = None


def _table(root: Path, directory: Path, manifest: dict, name: str) -> pd.DataFrame | None:
    listed = manifest.get("tables")
    if not (isinstance(listed, list) and name in listed and TABLE_NAME.match(name)):
        return None
    f = directory / f"{name}.parquet"
    if not inside(root, f):
        log.warning("%s: %s lies outside the artifacts directory", DESK, name)
        return None
    try:
        return pd.read_parquet(f)
    except (OSError, ValueError, pa.ArrowException):
        log.warning("%s: %s could not be read", DESK, name, exc_info=True)
        return None


def read_desk(db: Path, now: datetime) -> Desk:
    """The latest ``desk.report``'s two tables. ``why`` says what stops them being read: no jobs database (never
    created here), an unreadable one, no report yet, or a report a scheduled run has been missed since."""
    if not db.exists():
        return Desk("the job system has not started on this host, so there is no desk report to read")
    root = db.parent / "artifacts"
    schedules = load_schedules()
    try:
        with closing(connect(db)) as conn:
            got = read_latest(conn, DESK, now=now, schedules=schedules, root=root)
            manifest = got["manifest"] if got else None
            row = conn.execute("SELECT path FROM artifacts WHERE id = ? AND kind = ?", (manifest.get("id"), DESK)) \
                .fetchone() if isinstance(manifest, dict) and isinstance(manifest.get("id"), str) else None
    except sqlite3.Error:
        log.warning("jobs database unreadable", exc_info=True)
        return Desk("the job system's records could not be read")
    except (ValueError, OSError, RecursionError):
        log.warning("%s: its latest manifest could not be read", DESK, exc_info=True)
        return Desk("the latest desk report's record could not be read")
    if got is None:
        return Desk("no desk report has been published yet")
    if not isinstance(manifest, dict) or row is None:
        return Desk("the latest desk report's files could not be found")
    finished = manifest.get("finished_at")
    if missed_fire(DESK, finished, now, schedules):
        day = str(finished)[:10] if isinstance(finished, str) else "an unknown date"
        return Desk(f"the last desk report is from {day}, and a scheduled one has been missed since")
    asof = manifest.get("data_asof") if isinstance(manifest.get("data_asof"), str) else None
    directory = Path(row["path"])
    return Desk(None, asof, _table(root, directory, manifest, "pnl_by_strategy"),
                _table(root, directory, manifest, "book_summary"))


def _plain(v: Any) -> Any:
    """A parquet cell as a plain value: NaN, None, pd.NA and NaT all become None (pd.NA cannot be compared)."""
    try:
        return None if pd.isna(v) is True else v
    except (TypeError, ValueError):   # a list or an array: not missing
        return v


def _word(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _json_list(v: Any) -> list | None:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, list) else None


def _summary(desk: Desk) -> tuple[dict | None, str | None]:
    """``book_summary``'s one row as plain values, or None and why."""
    if desk.why:
        return None, desk.why
    if desk.book is None:
        return None, "the latest desk report does not carry the book's summary"
    if desk.book.empty:
        return None, "the latest desk report could not read the live desk"
    return {k: _plain(v) for k, v in desk.book.iloc[0].items()}, None


def _source(desk: Desk, table: str) -> str:
    return f"{DESK} {desk.asof or ''} · {table}".replace("  ", " ")


def strategies_row(desk: Desk) -> Row:
    """Strategies registered on the paper book, counted by tier from ``pnl_by_strategy``."""
    source = _source(desk, "pnl_by_strategy")
    if desk.why:
        return _unknown("ADVISORY", "DESK STATE UNKNOWN", desk.why, source)
    if desk.pnl is None or not {"tier", "state"} <= set(desk.pnl.columns):
        return _unknown("ADVISORY", "DESK STATE UNKNOWN", "the latest desk report does not carry the strategy roster",
                        source)
    if desk.pnl.empty:
        if desk.book is not None and desk.book.empty:   # every table empty: the engine was not read at all
            return _unknown("ADVISORY", "DESK STATE UNKNOWN", "the latest desk report could not read the live desk",
                            source)
        return Row("ADVISORY", "NO STRATEGY REGISTERED", "The latest desk report lists no registered strategy.", "ok",
                   source)
    tiers = [t if isinstance(t, str) and t in _TIERS else "other" for t in map(_plain, desk.pnl["tier"])]
    counts = {t: tiers.count(t) for t in (*_TIERS, "other") if tiers.count(t)}
    n, live = len(tiers), counts.get("live", 0)
    stopped = sum(1 for v in map(_plain, desk.pnl["state"]) if v == "stopped")
    lead = f"{_plural(n, 'strategy', 'strategies')} {'is' if n == 1 else 'are'} registered on the paper book: "
    detail = lead + ", ".join(f"{c} {t}" for t, c in counts.items()) + "."
    if stopped:
        detail += f" {stopped} stopped."
    if not live:
        detail += " None is live."
    detail += " The site shows counts and the switch, never a position."
    return Row("ADVISORY", "LIVE STRATEGIES" if live else "NO LIVE STRATEGY", detail, "ok", source)


def _kill_sentence(row: dict) -> str:
    kill, named = row.get("kill_switch"), _word(row.get("tripped_by"))
    if kill == "armed":
        return "The kill switch is armed."
    if kill == "disabled":
        return "The kill switch is disabled."
    if kill == "halted":
        return f"The kill switch has tripped on {named}." if named else "The kill switch has tripped."
    return "The kill switch's state was not read."


def switch_row(desk: Desk) -> Row:
    """The desk's own switch (``desk_switch``) and the kill switch beside it, from ``book_summary``. Trading is
    claimed only when both are known and neither stops it: the desk's switch clear and the kill switch armed (ok)
    or disabled (attention: nothing would stop it). A tripped kill switch is attention whatever the desk's switch
    reads, and an unread switch is unknown."""
    source = _source(desk, "book_summary")
    row, why = _summary(desk)
    if row is None:
        return _unknown("ADVISORY", "DESK SWITCH UNKNOWN", why or "", source)
    word, kill = row.get("desk_switch"), row.get("kill_switch")
    word = word if word in _DESK_SWITCH else None
    kill = kill if kill in _KILL else None
    kill_line = _kill_sentence(row)
    named = _word(row.get("tripped_by"))
    if word in ("halted", "tripped", "stopped"):
        item, detail = {
            "halted": ("DESK HALTED", "The desk is halted by hand: nothing trades until the owner lifts the halt."),
            "tripped": ("DESK TRIPPED", f"The desk is halted by a limit's trip{f' ({named})' if named else ''}: "
                                        "nothing trades until it is cleared."),
            "stopped": ("DESK STOPPED", "The engine has stopped the desk: nothing trades until it is restarted."),
        }[word]
        return Row("ADVISORY", item, f"{detail} {kill_line}", "attention", source)
    if kill == "halted":
        lead = ("The desk's own switch reads clear, so the two disagree; nothing here says the desk trades."
                if word == "clear" else "The desk's own switch was not read.")
        return Row("ADVISORY", "KILL SWITCH TRIPPED", f"{kill_line} {lead}", "attention", source)
    if word is None:
        return Row("ADVISORY", "DESK SWITCH UNKNOWN", f"Unknown: the latest desk report does not carry the desk's "
                   f"switch. {kill_line}", "unknown", source)
    if kill is None:
        return Row("ADVISORY", "DESK CLEAR", f"The desk's own switch is clear. {kill_line} Whether anything would "
                   "stop the desk is unknown.", "unknown", source)
    detail = f"The desk's switch is clear: registered strategies trade on the paper book. {kill_line}"
    return Row("ADVISORY", "DESK CLEAR", detail, "ok" if kill == "armed" else "attention", source)


def limits_row(desk: Desk) -> Row:
    """How many limits the book has and how many the kill switch trips on: counts, never a threshold."""
    source = _source(desk, "book_summary")
    row, why = _summary(desk)
    if row is None:
        return _unknown("OWNER", "BOOK LIMITS", why or "", source)
    limits = _json_list(row.get("limits"))
    if limits is None:
        return _unknown("OWNER", "BOOK LIMITS", "the latest desk report could not read the book's limits", source)
    if not limits:
        return Row("OWNER", "BOOK LIMITS", "The live book has no limits, so the kill switch has nothing to trip on.",
                   "attention", source)
    trips = _json_list(row.get("trips_on"))
    tail = (f"the kill switch trips on {len(trips)} of them" if trips is not None
            else "which of them trip the kill switch was not read")
    return Row("OWNER", "BOOK LIMITS", f"The owner sets the book's {_plural(len(limits), 'limit')} in book.sexp; "
               f"{tail}. Their thresholds are not published.", "ok", source)


# ------------------------------------------------------------------ pre-registrations
def approval(text: str) -> str | None:
    """The approval date a pre-registration gives (``signals.roster.recorded_approval``, which the machine graph's
    draft-roster label reads too); None when it records no approval."""
    return recorded_approval(text)


def preregistrations_row(root: Path) -> Row:
    source = "research/experiments · each preregistration.md"
    try:
        found = sorted(d for d in root.iterdir() if d.is_dir() and _EXPERIMENT.match(d.name)
                       and (d / "preregistration.md").is_file())
    except OSError:
        return _unknown("OWNER", "PRE-REGISTRATIONS", "the experiments directory is not in this build", source)
    if not found:
        return _unknown("OWNER", "PRE-REGISTRATIONS", "no pre-registration is packaged with this build", source)
    approved: dict[str, list[str]] = {}
    pending: list[str] = []
    for d in found:
        try:
            when = approval((d / "preregistration.md").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            when = None
        if when:
            approved.setdefault(when, []).append(d.name)
        else:
            pending.append(d.name)
    n = sum(len(v) for v in approved.values())
    groups = "; ".join(f"{', '.join(names)} {day}" for day, names in sorted(approved.items()))
    detail = f"{n} approved by the owner: {groups}." if n else "None is approved by the owner yet."
    if pending:
        detail += f" Awaiting the owner: {', '.join(pending)}."
    detail += " Any deviation is a new experiment and goes back to the owner."
    return Row("OWNER", "PRE-REGISTRATIONS", detail, "attention" if pending else "ok", source)


# ------------------------------------------------------------------ the whole ledger
NOTES = [
    f"Prose rows carry the date they were last checked against the code; one checked more than {STALE_AFTER_DAYS} "
    "days before as_of is stale.",
    "Computed rows are read from the system each time the ledger is built, and each names its source. A source "
    "that cannot be read is shown as unknown, with the reason.",
    "From the desk only counts, state words and a tripped limit's name are shown: no position, size or threshold.",
]


UNEXPECTED = "this row's source could not be read just now"


def _guarded(section: str, item: str, make: Callable[[], Row]) -> Row:
    """``make()``, or the row unknown when its source fails in a way no reader above expected. The exception goes
    to the log, never to the page."""
    try:
        return make()
    except Exception:   # noqa: BLE001 -- one bad source makes one row unknown; the rest of the ledger is served
        log.exception("the ledger's %s row could not be computed", item)
        return _unknown(section, item, UNEXPECTED, "")


def build(settings: Settings, *, now: datetime,
          engine_build: Callable[[Settings], tuple[str | None, str | None]] = read_engine_build,
          own_build: Callable[[], str | None] = own_build, experiments: Path = EXPERIMENTS,
          static: Path = STATIC) -> Ledger:
    """The ledger as of ``now`` (timezone-aware): the prose rows and the computed rows, by section."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    as_of = now.astimezone(NY).date()
    try:
        desk = read_desk(jobs_db_path(settings), now)
    except Exception:   # noqa: BLE001 -- one bad source makes its rows unknown, never the whole ledger
        log.exception("the desk report could not be read")
        desk = Desk(UNEXPECTED)
    computed = [
        _guarded("ADVISORY", "DESK STATE UNKNOWN", lambda: strategies_row(desk)),
        _guarded("ADVISORY", "DESK SWITCH UNKNOWN", lambda: switch_row(desk)),
        _guarded("LIMITS", "BACKUP STATE UNKNOWN", lambda: backup_row(settings, now)),
        _guarded("LIMITS", "LIVE HOST BUILD UNKNOWN", lambda: hosts_row(own_build(), engine_build(settings))),
        _guarded("OWNER", "BOOK LIMITS", lambda: limits_row(desk)),
        _guarded("OWNER", "PRE-REGISTRATIONS", lambda: preregistrations_row(experiments)),
    ]
    rows = load_static(static, as_of) + computed
    rows.sort(key=lambda r: SECTIONS.index(r.section))   # stable: file order, then the computed rows, per section
    provenance = [Provenance.now("ohcamel-ledger", store="ledger.yaml+jobs.sqlite+engine /api/ops+backup record",
                                 read_only=True).to_dict()]
    return Ledger(as_of, rows, provenance, NOTES)
