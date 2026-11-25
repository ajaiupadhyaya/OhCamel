#!/usr/bin/env python3
"""Write each farm strategy's ``(ex_ante_vol F)`` into a book, and drop its ``(ex_ante_vol_source placeholder)`` mark.

INC-02's stop is a multiple of each strategy's ex-ante volatility, and the monthly freeze
(``quant/src/ohcamel_quant/signals/freeze.py``) measures it: the 252-session annualised volatility of the cell's
backtest at its frozen parameters, gross 1, filled at the next open. ``book.example.sexp`` carries measured values for
EXP-A01's two rows and a placeholder for the farm rows (its header says so). Before the book is deployed, this rewrites
the farm rows from the freeze on the host that holds the warehouse (the owner's step):

    python tools/book_ex_ante_vol.py BOOK --vols vols.json [--write]            # on the laptop; the usual route
    cd quant && uv run --frozen python ../tools/book_ex_ante_vol.py BOOK [--month YYYY-MM-DD] [--write]
                                                                                # from a local freeze artifact

``vols.json`` is what ``python -m ohcamel_quant.signals.book_vols`` prints where the warehouse lives (docs/status.md,
*Deploying the desk*, gives the exact command); it computes each figure directly, so it needs no freeze and works on a
month frozen before task 2.2.

Read-only unless ``--write``. With ``--vols``, the file's ``month`` must be ``--month``'s (default: this month), or
the run is refused before anything is read from it. A farm strategy the month's freeze carries no ``ex_ante_vol`` for is REFUSED (exit 1):
nothing is written, and the book keeps whatever it held. EXP-A01's rows (``exp_a01_*``) are never touched. The book's own
``(trading ...)`` word is not read here: a book still carrying a placeholder mark is refused by the loader once
trading is enabled (``check-book``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_WORD = re.compile(r"\(ex_ante_vol\s+[^)\s]+\)")
_MARK = re.compile(r"\s*\(ex_ante_vol_source\s+placeholder\)")
_NAME = re.compile(r"\(name\s+(\w+)\)")


def rewrite(text: str, vols: dict[str, float]) -> tuple[str, list[str], list[str]]:
    """``(new text, rewritten slugs, refused slugs)``: each farm strategy block's ``(ex_ante_vol F)`` set to its
    freeze value; a farm block with no value is refused and left as it was."""
    parts = re.split(r"(?=\(\(name\s)", text)
    done: list[str] = []
    refused: list[str] = []
    out = [parts[0]]
    for block in parts[1:]:
        m = _NAME.match(block[1:]) or _NAME.search(block)
        slug = m.group(1) if m else ""
        if not slug.startswith("farm_"):
            out.append(block)
            continue
        if slug not in vols or not _WORD.search(block):
            refused.append(slug)
            out.append(block)
            continue
        out.append(_MARK.sub("", _WORD.sub(f"(ex_ante_vol {vols[slug]:.6f})", block, count=1)))
        done.append(slug)
    return "".join(out), done, refused


def month_mismatch(doc: dict, month: date) -> str | None:
    """The refusal when ``vols.json``'s ``month`` (``YYYY-MM``, book_vols' own) is not ``--month``'s; None when they
    agree. A file with no month is refused too: its figures could be any month's."""
    want = f"{month.year:04d}-{month.month:02d}"
    got = doc.get("month") if isinstance(doc, dict) else None
    if got is None:
        return f"REFUSED: {want} asked, but the vols file names no month"
    if got != want:
        return f"REFUSED: the vols file is for {got}, not {want} (pass --month {got}-01 if that is the month meant)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("book", type=Path)
    ap.add_argument("--month", type=date.fromisoformat, default=date.today().replace(day=1))
    ap.add_argument("--vols", type=Path, default=None, help="JSON from ohcamel_quant.signals.book_vols")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if args.vols is not None:
        doc = json.loads(args.vols.read_text())
        problem = month_mismatch(doc, args.month)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        print(f"vols for {doc['month']}")
        vols = {k: float(v["ex_ante_vol"]) for k, v in doc["vols"].items() if v.get("ex_ante_vol") is not None}
        for k, v in doc["vols"].items():
            print(f"{k}  {v.get('params_source')}" + (f"  NOT COMPUTED: {v.get('why')}" if v.get("why") else ""))
    else:
        from ohcamel_quant.signals.freeze import freeze_id, frozen_ex_ante_vol
        from ohcamel_quant.signals.roster import load_roster

        cells = [c for c in load_roster().cells if c.producer == "signals.daily"]
        if freeze_id(args.month) is None:
            print(f"no signals.freeze artifact for {args.month:%Y-%m}", file=sys.stderr)
            return 1
        vols = frozen_ex_ante_vol(month=args.month, cells=cells)
    new, done, refused = rewrite(args.book.read_text(), vols)
    for slug in done:
        print(f"{slug}  ex_ante_vol {vols[slug]:.6f}")
    if refused:
        print("REFUSED (no ex_ante_vol in the freeze): " + ", ".join(refused), file=sys.stderr)
        return 1
    if args.write:
        args.book.write_text(new)
        print(f"wrote {args.book}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
