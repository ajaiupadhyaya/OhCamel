"""The ex-ante volatilities for INC-02's book, computed where the data lives (completion task 2.2, round 1).

The monthly freeze writes ``volatility`` rows, but a month frozen before this code existed has none and is never
re-taken, and the freeze artifacts and the warehouse live in the ``quant_data`` volume, not on the owner's laptop.
This module computes the figures directly -- :func:`freeze.ex_ante_vol` (the 252-session annualised volatility of the
cell's backtest at its parameters, gross 1, filled at the next open) -- for every ``signals.daily`` roster cell, and
prints JSON that ``tools/book_ex_ante_vol.py --vols FILE`` writes into the book.

READ-ONLY, for real. jobs.sqlite is opened ``mode=ro`` (``products.io.read_only_db``: no WAL pragma, no schema DDL);
prices come from the warehouse alone (:class:`WarehouseOnly`): a ticker the warehouse cannot serve refuses that
cell with the reason and nothing is fetched or cached; the risk-free rate is not read (``rf`` omitted -- a daily
rate that moves by basis points over a year, so its effect on a 252-session volatility is far below the figure's
six places; the freeze itself reads it, so the two can differ in the last digits). It takes no freeze and writes no
artifact, so it can run against a volume mounted ``:ro``.

PARAMETERS. The month's ``signals.freeze`` ``params`` table when it holds the cell (``params_source`` says
``freeze YYYY-MM``); otherwise the newest worker ``farm.sweep`` artifact's ``selected_params`` for the cell
(``params_source`` says ``farm.sweep <id>, no freeze row for the month``), the parameters the next freeze would
hold. A cell with neither, or whose backtest cannot be run or has fewer than 252 live sessions, has ``ex_ante_vol:
null`` and a ``why``: no number is invented.

    python -m ohcamel_quant.signals.book_vols [--month YYYY-MM-DD] > vols.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from types import SimpleNamespace
from typing import Any

from ..data.base import DataUnavailable
from . import freeze
from .roster import PRODUCERS, Cell, load_roster

__all__ = ["WarehouseOnly", "compute", "main"]


class WarehouseOnly:
    """A ``MarketData`` stand-in that serves prices from the warehouse and never from a vendor: a miss (a ticker not
    stored, stale, or a window before the stored history) raises ``DataUnavailable`` with the warehouse's reason,
    and the risk-free rate (a FRED fetch) is not read."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def prices(self, tickers: list[str], start: date | None = None, end: date | None = None,
               field: str = "adj_close", *, warehouse_first: bool = False) -> Any:
        from ..warehouse.readers import warehouse_prices

        hit, note = warehouse_prices(self.settings, list(tickers), start, end, field)
        if hit is None:
            raise DataUnavailable(f"warehouse miss, nothing fetched: {note or 'no warehouse'}")
        return hit

    def risk_free_daily(self, start: date | None = None, end: date | None = None) -> Any:
        raise DataUnavailable("the risk-free rate is not read: book_vols reads the warehouse only")


def _freeze_params(month: date, db: Any) -> tuple[dict[str, str], set[str]]:
    """``({slug: selected_params}, every slug the month's freeze has a row for)``."""
    from ..products.io import previous_artifact, read_table

    got = previous_artifact(freeze.FREEZE_KIND, freeze._phash(freeze.month_key(month)), db_path=db, worker_only=True)
    table = read_table(got[1], "params") if got else None
    if table is None or not len(table):
        return {}, set()
    rows = table.to_dict("records")
    return ({r["slug"]: r["selected_params"] for r in rows if freeze._str(r.get("selected_params"))},
            {r["slug"] for r in rows})


def compute(ctx: Any, *, month: date, cells: list[Cell] | None = None) -> dict[str, dict[str, Any]]:
    """``{slug: {ex_ante_vol, vol_sessions, vol_asof, params_source, why}}`` for ``cells`` (default: the roster's
    ``signals.daily`` cells), by calling :func:`freeze.ex_ante_vol` at the month's frozen parameters, else the newest
    farm parameters."""
    from ..products.io import read_only_db

    with read_only_db():
        return _compute(ctx, month, cells)


def _compute(ctx: Any, month: date, cells: list[Cell] | None) -> dict[str, dict[str, Any]]:
    cells = cells if cells is not None else [c for c in load_roster().cells if c.producer == "signals.daily"]
    db = freeze._db()
    frozen, in_freeze = _freeze_params(month, db)
    farm_id, farm = freeze.newest_farm(db)
    out: dict[str, dict[str, Any]] = {}
    for c in cells:
        if c.slug in frozen:
            sel, source = frozen[c.slug], f"freeze {freeze.month_key(month)}"
        else:
            r = freeze._row(farm, "cell_id", freeze._cell_id(c))
            sel = freeze._str(r.get("selected_params")) if r else None
            source = (f"farm.sweep {farm_id}, " + ("not emitted in the freeze" if c.slug in in_freeze
                                                    else "no freeze row for the month"))
        row: dict[str, Any] = {"ex_ante_vol": None, "vol_sessions": None, "vol_asof": None, "params_source": source,
                               "why": None}
        if sel is None:
            row["why"] = "no frozen parameters and none in the newest farm.sweep artifact"
        else:
            vol, n, asof, why = freeze.ex_ante_vol(c, json.loads(sel), ctx)
            row.update(ex_ante_vol=vol, vol_sessions=n, vol_asof=asof, why=why)
        out[c.slug] = row
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--month", type=date.fromisoformat, default=date.today().replace(day=1))
    args = ap.parse_args(argv)
    from ..config import Settings

    assert "signals.daily" in PRODUCERS
    ctx = SimpleNamespace(market=WarehouseOnly(Settings()))
    vols = compute(ctx, month=args.month)
    json.dump({"month": freeze.month_key(args.month), "vols": vols}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
