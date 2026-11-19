"""Committed, dated index constituent lists (compute plan C2).

``sp500.csv`` and ``ndx100.csv`` are CURRENT constituents (survivor lists):
names that left the index are absent, which every artifact built on them says
(``SURVIVORSHIP``; owner decision O-4 may add a point-in-time source). They are
refreshed by hand -- ``python -m ohcamel_quant.warehouse.constituents`` -- from
Wikipedia's maintained tables, and the file header records the source URL and
the retrieval date. Format::

    # source: <url>
    # retrieved: YYYY-MM-DD
    ticker,name
    MMM,3M

Membership with dates (completion task 3.5): ``sp500_changes.csv`` is Wikipedia's "Historical components of the
S&P 500" changes table (effective date, ticker added, ticker removed), refreshed together with ``sp500.csv`` from
pinned revisions of both articles. :func:`membership_intervals` walks it backwards from the current list and gives
each name its ``[start, end)`` stints. What the table cannot say is written down, not guessed:

* it is complete only from :data:`COVERAGE_START` (rows per year: 8 in 2008, 11 in 2010, 16-19 in 2011-2014,
  17-30 since 2015; S&P replaced about 23 a year from 1963 to 2014), and a stint that began before its first
  recorded change has ``start`` NULL;
* ticker changes are not index changes and are not in the table (its own editing note), so a name added under an
  old ticker (FB in 2013, now META) has no stint under the old ticker and its current ticker reads as a member
  from before coverage; each such row is a note, and ``readers.members_asof`` refuses dates before coverage;
* an effective date is the first session WITHOUT the removed name and WITH the added one (the table's own note:
  some dates are the replaced name's last day, one session off);
* a removal of a ticker that is a member again right after it, with no addition between, is read as the TICKER
  passing from one member company to another, not as a gap: the walk records two stints meeting on that date, so
  the ticker's membership is unbroken, and writes a note. That is what happened in the case it was written for:
  on 2015-03-23 Allergan Inc. (AGN) left when Actavis bought it, and Actavis -- already a member, added as ACT --
  took the ticker AGN; likewise T on 2005-11-18 (SBC bought AT&T Corp. and took its ticker). The walk finds the
  same shape at AN and SUN (1998) and MCK (1994), all before coverage, and reads them the same way unchecked.
  It is a reading of the rows, stated here and in the note, not a date the table gives; a stint read this way
  is a ticker's, and the company behind it may differ before and after the date.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SURVIVORSHIP = "current constituents; delisted names absent"
SOURCES = {
    "sp500": "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw",
    "ndx100": "https://en.wikipedia.org/w/index.php?title=List_of_NASDAQ-100_companies&action=raw",
}
#: Each universe whose membership has dates: its changes table's article (the stints come from it).
CHANGES_SOURCES = {
    "sp500": "https://en.wikipedia.org/w/index.php?title=Historical_components_of_the_S%26P_500&action=raw",
}
#: The first date from which the changes table is taken as complete (module docstring); before it no membership.
COVERAGE_START = {"sp500": date(2011, 1, 1)}
MEMBERSHIP_SURVIVORSHIP = "point in time from the index's changes table; delisted names included by ticker"
TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
# {{NyseSymbol|MMM}}, {{NasdaqSymbol|ADBE}} and {{BZX link|CBOE}} (Cboe-listed)
_SYMBOL = re.compile(r"\{\{\s*(?:\w*Symbol|\w+ link)\s*\|\s*([^}|]+?)\s*\}\}", re.I)
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")


@dataclass(frozen=True)
class Constituents:
    name: str
    as_of: date
    source: str
    members: list[tuple[str, str]]


def _strip(cell: str) -> str:
    s = _COMMENT.sub("", cell)
    s = _REF.sub("", s)
    s = _SYMBOL.sub(r"\1", s)
    s = _LINK.sub(r"\1", s)
    s = _TEMPLATE.sub("", s)
    s = s.replace("'''", "").replace("''", "")
    return " ".join(s.strip(" |").split())  # a stray trailing pipe, e.g. "[[ResMed]]|"


def parse_constituents_wikitext(text: str) -> list[tuple[str, str]]:
    """``(ticker, name)`` rows of the ``id="constituents"`` wikitable, in page order."""
    start = text.find('id="constituents"')
    if start < 0:
        raise ValueError("no constituents table in the wikitext")
    end = text.find("\n|}", start)
    table = text[start:end if end >= 0 else None]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in table.split("\n|-")[1:]:
        lines = [ln for ln in row.strip().splitlines() if ln.startswith("|") and not ln.startswith("|}")]
        if not lines:
            continue  # the header row starts with '!'
        cells = [c.strip() for c in " || ".join(ln.lstrip("|").strip() for ln in lines).split("||")]
        if len(cells) < 2:
            continue
        ticker, name = _strip(cells[0]).upper(), _strip(cells[1])
        if not TICKER_RE.match(ticker):
            raise ValueError(f"bad ticker {ticker!r} in row {row.strip()[:80]!r}")
        if ticker not in seen:
            seen.add(ticker)
            out.append((ticker, name))
    if not out:
        raise ValueError("constituents table has no rows")
    return out


def write_constituents_csv(path: Path, members: list[tuple[str, str]], source: str, as_of: date) -> None:
    buf = io.StringIO()
    buf.write(f"# source: {source}\n# retrieved: {as_of.isoformat()}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["ticker", "name"])
    w.writerows(members)
    Path(path).write_text(buf.getvalue())


def load_constituents(name_or_path: str | Path) -> Constituents:
    path = Path(name_or_path)
    if not path.suffix:
        path = HERE / f"{name_or_path}.csv"
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            k, _, v = line[1:].partition(":")
            meta[k.strip()] = v.strip()
        elif line.strip():
            body.append(line)
    rows = list(csv.DictReader(body))
    return Constituents(name=path.stem, as_of=date.fromisoformat(meta["retrieved"]), source=meta["source"],
                        members=[(r["ticker"], r["name"]) for r in rows])


@dataclass(frozen=True)
class Change:
    """One row of an index's changes table: on ``effective`` ``added`` joined and ``removed`` left (either may be
    empty: a spin-off adds without removing, a shrink removes without adding)."""

    effective: date
    added: str
    removed: str


def _ticker_cell(cell: str, row: str) -> str:
    t = _strip(cell).upper()
    if t and not TICKER_RE.match(t):
        raise ValueError(f"bad ticker {t!r} in row {row.strip()[:80]!r}")
    return t


def parse_changes_wikitext(text: str) -> list[Change]:
    """The ``id="changes"`` wikitable: Effective Date, Added (ticker, security), Removed (ticker, security),
    Reason, Refs -- in page order (newest first). A row whose date does not read as ``Month D, YYYY`` is refused."""
    start = text.find('id="changes"')
    if start < 0:
        raise ValueError("no changes table in the wikitext")
    end = text.find("\n|}", start)
    table = text[start:end if end >= 0 else None]
    out: list[Change] = []
    for row in table.split("\n|-")[1:]:
        lines = [ln for ln in row.strip().splitlines() if ln.startswith("|") and not ln.startswith("|}")]
        if not lines:
            continue  # header rows start with '!'
        cells = [c.strip() for c in " || ".join(ln.lstrip("|").strip() for ln in lines).split("||")]
        if len(cells) < 5:
            raise ValueError(f"short changes row {row.strip()[:80]!r}")
        raw = _strip(cells[0])
        try:
            effective = datetime.strptime(raw, "%B %d, %Y").date()
        except ValueError as e:
            raise ValueError(f"bad effective date {raw!r} in row {row.strip()[:80]!r}") from e
        added, removed = _ticker_cell(cells[1], row), _ticker_cell(cells[3], row)
        if not added and not removed:
            raise ValueError(f"changes row with neither ticker: {row.strip()[:80]!r}")
        out.append(Change(effective, added, removed))
    if not out:
        raise ValueError("changes table has no rows")
    return out


def write_changes_csv(path: Path, changes: list[Change], source: str, as_of: date) -> None:
    buf = io.StringIO()
    buf.write(f"# source: {source}\n# retrieved: {as_of.isoformat()}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["effective", "added", "removed"])
    w.writerows([c.effective.isoformat(), c.added, c.removed] for c in changes)
    Path(path).write_text(buf.getvalue())


@dataclass(frozen=True)
class Changes:
    name: str
    as_of: date
    source: str
    changes: list[Change]


def load_changes(name_or_path: str | Path) -> Changes:
    path = Path(name_or_path)
    if not path.suffix:
        path = HERE / f"{name_or_path}_changes.csv"
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            k, _, v = line[1:].partition(":")
            meta[k.strip()] = v.strip()
        elif line.strip():
            body.append(line)
    rows = list(csv.DictReader(body))
    return Changes(name=path.stem.removesuffix("_changes"), as_of=date.fromisoformat(meta["retrieved"]),
                   source=meta["source"],
                   changes=[Change(date.fromisoformat(r["effective"]), r["added"], r["removed"]) for r in rows])


@dataclass(frozen=True)
class Stint:
    ticker: str
    start: date | None   # None: a member before the table's first recorded change
    end: date | None     # None: a member on the list's as_of date; otherwise the first date NOT a member


def membership_intervals(current: list[str], as_of: date,
                         changes: list[Change]) -> tuple[list[Stint], list[str]]:
    """Walk ``changes`` backwards from the ``current`` members on ``as_of``: an addition on ``d`` opens the
    stint at ``d``; a removal on ``d`` means the name was a member until ``d`` (exclusive). Changes after
    ``as_of`` (announced, not yet effective) are not applied. Returns the stints, newest first per walk, and one
    note per row the walk could not place (see the module docstring)."""
    notes: list[str] = []
    later = [c for c in changes if c.effective > as_of]
    if later:
        notes.append(f"{len(later)} change(s) after {as_of.isoformat()} not applied (announced, not yet effective)")
    open_end: dict[str, date | None] = {t: None for t in current}
    stints: list[Stint] = []
    for c in sorted((c for c in changes if c.effective <= as_of), key=lambda c: c.effective, reverse=True):
        if c.added:
            if c.added in open_end:
                stints.append(Stint(c.added, c.effective, open_end.pop(c.added)))
            else:
                notes.append(f"{c.added} added {c.effective.isoformat()} is not a later member under that ticker "
                             "(renamed, or its removal is not in the table): no stint recorded for it")
        if c.removed:
            if c.removed in open_end:
                notes.append(f"{c.removed} removed {c.effective.isoformat()} while a member again right after it "
                             "with no addition recorded: read as the ticker passing to another member company "
                             "(module docstring), its membership unbroken")
                stints.append(Stint(c.removed, c.effective, open_end.pop(c.removed)))
            open_end[c.removed] = c.effective
    stints += [Stint(t, None, e) for t, e in open_end.items()]
    return stints, notes


def _pinned(title: str) -> tuple[str, int]:  # pragma: no cover - networked
    import httpx

    r = httpx.get("https://en.wikipedia.org/w/api.php", timeout=30, headers={"User-Agent": "OhCamel-Quant/1.0 (research)"},
                  params={"action": "query", "prop": "revisions", "titles": title, "rvprop": "ids", "format": "json"})
    revid = int(next(iter(r.json()["query"]["pages"].values()))["revisions"][0]["revid"])
    return title, revid


def main() -> int:  # pragma: no cover - a manual, networked refresh, run by a person
    import httpx

    today = date.today()
    get = lambda u: httpx.get(u, timeout=30, headers={"User-Agent": "OhCamel-Quant/1.0 (research)"}).text  # noqa: E731
    for name, url in SOURCES.items():
        title = url.split("title=")[1].split("&")[0].replace("%26", "&")
        _, revid = _pinned(title)
        pinned = f"{url}&oldid={revid}"
        members = parse_constituents_wikitext(get(pinned))
        write_constituents_csv(HERE / f"{name}.csv", members, pinned, today)
        print(f"{name}: {len(members)} members -> {HERE / f'{name}.csv'}")
        if name in CHANGES_SOURCES:
            curl = CHANGES_SOURCES[name]
            _, crev = _pinned(curl.split("title=")[1].split("&")[0].replace("%26", "&"))
            cpinned = f"{curl}&oldid={crev}"
            changes = parse_changes_wikitext(get(cpinned))
            write_changes_csv(HERE / f"{name}_changes.csv", changes, cpinned, today)
            print(f"{name}: {len(changes)} changes -> {HERE / f'{name}_changes.csv'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
