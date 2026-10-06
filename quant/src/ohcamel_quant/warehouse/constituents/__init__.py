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
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SURVIVORSHIP = "current constituents; delisted names absent"
SOURCES = {
    "sp500": "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw",
    "ndx100": "https://en.wikipedia.org/w/index.php?title=List_of_NASDAQ-100_companies&action=raw",
}
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


def main() -> int:  # pragma: no cover - a manual, networked refresh, run by a person
    import httpx

    today = date.today()
    for name, url in SOURCES.items():
        text = httpx.get(url, timeout=30, headers={"User-Agent": "OhCamel-Quant/1.0 (research)"}).text
        members = parse_constituents_wikitext(text)
        write_constituents_csv(HERE / f"{name}.csv", members, url, today)
        print(f"{name}: {len(members)} members -> {HERE / f'{name}.csv'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
