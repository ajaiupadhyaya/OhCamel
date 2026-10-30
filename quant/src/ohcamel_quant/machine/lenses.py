"""Product lenses: the few table rows a product's reading on the page needs, and the clock's extras.

A lens is a small, plain payload read from the product's own tables: the farm's leaderboard, the vol
league's models, the regime model's latest ``p_high``. ``product_lens`` never raises. A lens it cannot
read is ``None`` with the reason in words ("leaderboard has no column oos_sharpe_ann"). The graph logs that
reason and puts one plain sentence on the node as ``m.lens_why`` instead of a lens, so no table or column
is named on the page. Nothing is invented to fill a gap.

Floats are rounded to four places and anything not finite is ``None``, so a lens is plain JSON.
Counts are ints even when a NaN in their column made them floats.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd

from .graph import ATLAS_ALPHA, Inputs, Product, _plain

# the tables each lens reads: what the loader must hand over. The farm's trials are not among them:
# its lens only counts them, from the row count the manifest's parquet metadata gives.
LENS_TABLES: dict[str, tuple[str, ...]] = {
    "farm.sweep": ("leaderboard",), "vol.forecast_league": ("summary",), "cov.league": ("league",),
    "risk.mc_atlas": ("summary",), "models.xs_lgbm": ("holdout_deciles",), "regime.hmm": ("probs",),
    "vol.surface_history": ("history",),
}

BOARD = ("cell_id", "verdict", "oos_sharpe_ann", "dsr_farm", "psr", "pbo", "n_combos")
SURFACE_NAME = "SPY"   # the surface lens reads this underlying's newest row


class _Omit(Exception):
    """A lens that cannot be read; the message is the reason."""


def _n(v: Any) -> Any:
    """A figure for the wire: Python's own type, floats to four places, ``None`` where not finite."""
    v = _plain(v)
    return round(v, 4) + 0.0 if isinstance(v, float) else v   # + 0.0: a rounded -0.0 is 0.0


def _whole(v: Any) -> Any:
    """A count: an int when the figure is whole (a column that holds a NaN is float), else as ``_n``."""
    v = _n(v)
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _day(v: Any) -> Any:
    """A date as ``YYYY-MM-DD``, whether the artifact wrote it as text or as a timestamp."""
    v = _plain(v)
    return v.split("T")[0] if isinstance(v, str) else v


def _frame(p: Product, name: str, needs: Sequence[str]) -> pd.DataFrame:
    """The product's table ``name`` (a fresh 0..n-1 index) when it is a table with rows and every column
    in ``needs``; otherwise the reason it cannot be read."""
    df = p.tables.get(name)
    if not isinstance(df, pd.DataFrame):
        raise _Omit(f"no {name} table")
    if len(df) == 0:
        raise _Omit(f"{name} has no rows")
    missing = [c for c in needs if c not in df.columns]
    if missing:
        raise _Omit(f"{name} has no column{'s' if len(missing) > 1 else ''} {', '.join(missing)}")
    return df.reset_index(drop=True)


def _rows(df: pd.DataFrame, cols: Sequence[str], counts: Sequence[str] = ()) -> list[list]:
    """Each row's ``cols`` as a list, ``counts`` being the columns that hold counts."""
    return [[(_whole if c in counts else _n)(v) for c, v in zip(cols, row, strict=True)]
            for row in df[list(cols)].itertuples(index=False, name=None)]


def _farm(p: Product) -> dict:
    """The leaderboard, best out-of-sample Sharpe first (a cell with none, or none finite, last), the
    cells on it, and the trials the farm deflated by, which are counted, not read."""
    board = _frame(p, "leaderboard", BOARD)
    trials = p.rows.get("trials")
    if trials is None:
        raise _Omit("no trials table")
    sharpe = pd.to_numeric(board["oos_sharpe_ann"], errors="coerce")
    order = sharpe.where(np.isfinite(sharpe)).sort_values(ascending=False, kind="stable", na_position="last").index
    return {"cells": len(board), "trials": _whole(trials), "board": _rows(board.loc[order], BOARD, ("n_combos",))}


def _vol_league(p: Product) -> dict:
    cols = ("model", "mcs_rate", "mean_rank", "names")
    return {"models": _rows(_frame(p, "summary", cols), cols, ("names",))}


def _cov_league(p: Product) -> dict:
    cols = ("estimator", "universe", "realized_vol", "rank")
    return {"estimators": _rows(_frame(p, "league", cols), cols, ("rank",))}


def _atlas(p: Product) -> dict:
    """The measures at alpha 0.99 (the book is the one the atlas holds; there is no book handling)."""
    cols = ("method", "horizon", "var", "es", "n_paths")
    summary = _frame(p, "summary", ("alpha", *cols))
    kept = summary[(pd.to_numeric(summary["alpha"], errors="coerce") - ATLAS_ALPHA).abs() < 1e-9]
    if kept.empty:
        raise _Omit(f"summary has no alpha {ATLAS_ALPHA} rows")
    return {"measures": _rows(kept, cols, ("horizon", "n_paths"))}


def _xs(p: Product) -> dict:
    cols = ("decile", "mean_next_month_return")
    return {"deciles": _rows(_frame(p, "holdout_deciles", cols), cols, ("decile",))}


def _regime(p: Product) -> dict:
    """The latest week with a probability (the first weeks of a rolling fit have none), and how many weeks."""
    probs = _frame(p, "probs", ("date", "p_high"))
    p_high = pd.to_numeric(probs["p_high"], errors="coerce").astype("float64")
    live = p_high[np.isfinite(p_high)]
    if live.empty:
        raise _Omit("probs has no p_high value")
    i = live.index[-1]
    return {"date": _day(probs.at[i, "date"]), "p_high": _n(p_high[i]), "weeks": len(probs)}


def _surface(p: Product) -> dict:
    """SPY's newest row. The artifact records expiry slices, not contracts, so the lens says slices."""
    history = _frame(p, "history", ("underlying", "asof", "atm_iv_30d", "n_slices"))
    own = history[history["underlying"] == SURFACE_NAME]
    if own.empty:
        raise _Omit(f"history has no {SURFACE_NAME} row")
    when = pd.to_datetime(own["asof"], errors="coerce")
    row = own.loc[when.idxmax()] if when.notna().any() else own.iloc[-1]
    return {"underlying": SURFACE_NAME, "asof": _day(row["asof"]), "atm30": _n(row["atm_iv_30d"]),
            "slices": _whole(row["n_slices"]), "names": int(history["underlying"].nunique())}


_LENSES: dict[str, Callable[[Product], dict]] = {
    "farm.sweep": _farm, "vol.forecast_league": _vol_league, "cov.league": _cov_league, "risk.mc_atlas": _atlas,
    "models.xs_lgbm": _xs, "regime.hmm": _regime, "vol.surface_history": _surface,
}


def product_lens(kind: str, p: Product) -> tuple[dict | None, str | None]:
    """``(lens, None)``, or ``(None, why)`` when the lens cannot be read, or ``(None, None)`` for a kind
    that has no lens (``desk.report`` among them). It never raises."""
    read = _LENSES.get(kind)
    if read is None:
        return None, None
    try:
        return read(p), None
    except _Omit as e:
        return None, str(e)
    except (TypeError, ValueError, KeyError, ArithmeticError) as e:   # a table of a shape no lens expects
        return None, f"{kind} lens could not be read ({type(e).__name__})"


def extras(inp: Inputs) -> dict:
    """What the page needs beyond nodes and edges: ``fires``, every fire of every job as ``[kind, hour]``
    (a New York hour, three places), sorted by kind, then hour."""
    return {"fires": sorted([kind, round(h, 3)] for kind, c in inp.clock.items() for h in c.fires)}
