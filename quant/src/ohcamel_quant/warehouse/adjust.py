"""The adjusted daily series, derived from RAW bars and the corporate actions that adjust them (task 3.2).

``bars_daily`` stores what the vendor served: prices already adjusted for every split and dividend up to the
day of the fetch, so each new dividend rewrites a ticker's whole history and nothing says what the price was.
``bars_raw`` keeps the unadjusted prints and ``corp_actions`` the splits and cash dividends; :func:`series`
derives the adjusted series from the two. Until the owner's cut-over (30 clean nights of
``ops.data_integrity``'s ``adjust_drift`` on the live host) every reader still reads ``bars_daily``; this
module is checked against it nightly and read by nothing else yet.

**The convention** (backward adjustment, the CRSP-style multiplicative one Yahoo's ``adjclose`` uses):

1. *Splits first.* A split with ex-date ``E`` and ratio ``r`` (new shares per old share: 2.0 for 2-for-1,
   0.1 for 1-for-10) divides every price dated BEFORE ``E`` by ``r`` and multiplies that volume by ``r``;
   bars on and after ``E`` are untouched. Several splits multiply: a bar is divided by the product of the
   ratios of every split dated after it. The result is the split-adjusted series, the vendor's ``close``
   (``dividends=False`` returns exactly this, volume included).
2. *Then dividends.* A cash dividend with ex-date ``E`` and cash ``c`` per share as paid on ``E`` has the
   factor ``1 - c' / C``, where ``C`` is the split-adjusted close of the last session BEFORE ``E`` and ``c'``
   is ``c`` divided by the ratios of the splits dated after ``E`` (the same share basis as ``C``; when no
   split falls on ``E`` itself, ``c' / C`` equals the raw cash over the raw close of that session). Every
   price (open, high, low, close) dated before ``E`` is multiplied by the factor; volume is not. Factors
   multiply, so their order among themselves does not matter, and a later dividend's factor is computed on
   the split-adjusted close, never on an already dividend-adjusted one.
3. An ex-date that is not a session (the market was shut) uses the last session before it, and adjusts every
   session before it. A dividend dated on or before the first raw bar adjusts nothing. A factor at or below
   zero (cash at or above the close) is refused.

**Measured against the vendor** (the committed responses in quant/tests/data, and the long histories the
task-3.2 report lists): Yahoo's ``adjclose`` is reproduced to 4.2e-7 relative at worst on AAPL 2020-2026,
NVDA 2024-2026 and XLK 2024-2026 (the brief's gate is 1e-6). Yahoo publishes prices as float32 and states its
dividend amounts rounded, so on 1990-onward histories with 130-290 dividends its own figures wander by up to
1.0-1.7e-6 from the exact convention (SPY 1.1e-6, XOM 1.0e-6, TLT 1.7e-6, KO 1.1e-6): a noise floor, not a
different convention. One known exception: an ex-date the market was shut for (KO, 2001-09-12) -- Yahoo
leaves the last session before it unadjusted, this module adjusts it (rule 3); one bar, 0.7%.

**Point in time.** :func:`series` uses exactly the actions it is given, and a backward adjustment by an
action dated ``E`` rescales every bar before ``E``: levels change, returns before ``E`` do not. A caller that
must see history as it was known on date ``D`` passes ``asof=D``: actions with an ex-date after ``D`` and
bars after ``D`` are then ignored, so nothing learned after ``D`` reaches the result.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

PRICES = ["open", "high", "low", "close"]
RAW_COLUMNS = [*PRICES, "volume"]
BAR_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
KINDS = ("split", "dividend")


def _actions(actions: pd.DataFrame, asof: Any) -> pd.DataFrame:
    a = actions.copy() if actions is not None else pd.DataFrame(columns=["ex_date", "kind", "ratio", "cash"])
    for col in ("ex_date", "kind", "ratio", "cash"):
        if col not in a.columns:
            a[col] = np.nan
    if "ticker" in a.columns and a["ticker"].nunique() > 1:
        raise ValueError("actions for more than one ticker")
    a["ex_date"] = pd.to_datetime(a["ex_date"]).astype("datetime64[ns]")
    bad = sorted(set(a["kind"]) - set(KINDS))
    if bad:
        raise ValueError(f"unknown action kind(s): {', '.join(map(str, bad))}")
    dup = a.duplicated(["ex_date", "kind"], keep=False)
    if dup.any():
        first = a.loc[dup].iloc[0]
        raise ValueError(f"the same action twice: {first['kind']} on {first['ex_date'].date()} "
                         "(an action is keyed on ticker, ex_date, kind)")
    if asof is not None:
        a = a[a["ex_date"] <= pd.Timestamp(asof)]
    return a.sort_values(["ex_date", "kind"], kind="stable")


def split_factor(index: pd.DatetimeIndex, splits: pd.DataFrame) -> np.ndarray:
    """Per bar, the product of the ratios of every split dated after it (what a raw price is divided by)."""
    idx = pd.DatetimeIndex(index)
    s = np.ones(len(idx))
    for ex, ratio in zip(splits["ex_date"], splits["ratio"], strict=True):
        r = float(ratio)
        if not (np.isfinite(r) and r > 0):
            raise ValueError(f"split on {pd.Timestamp(ex).date()} has no positive ratio ({ratio!r})")
        s[idx < pd.Timestamp(ex)] *= r
    return s


def series(raw: pd.DataFrame, actions: pd.DataFrame, *, dividends: bool,
           asof: date | pd.Timestamp | None = None) -> pd.DataFrame:
    """The adjusted ``open, high, low, close, volume`` of one ticker (module docstring: the convention).

    ``raw``: unadjusted bars on an ascending, unique date index (columns open, high, low, close, volume;
    others ignored). ``actions``: that ticker's ``corp_actions`` rows (``ex_date, kind, ratio, cash``).
    ``dividends=False`` gives the split-adjusted series (the vendor's ``close``); ``True`` adds the dividend
    factors (the vendor's ``adj_close`` is its ``close``). ``asof``: ignore actions and bars after it."""
    a = _actions(actions, asof)
    bars = raw[RAW_COLUMNS].astype(float)
    idx = pd.DatetimeIndex(bars.index)
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("raw bars must be on an ascending, unique date index")
    if asof is not None:
        keep = idx <= pd.Timestamp(asof)
        bars, idx = bars[keep], idx[keep]
    splits = a[a["kind"] == "split"]
    s = split_factor(idx, splits)
    out = bars[PRICES].div(s, axis=0)
    out["volume"] = bars["volume"] * s
    if dividends:
        f = np.ones(len(idx))
        close = out["close"].to_numpy()
        for ex, cash in zip(a.loc[a["kind"] == "dividend", "ex_date"], a.loc[a["kind"] == "dividend", "cash"],
                            strict=True):
            c = float(cash)
            if not (np.isfinite(c) and c >= 0):
                raise ValueError(f"dividend on {ex.date()} has no cash amount ({cash!r})")
            pos = int(idx.searchsorted(ex))           # first session on or after the ex-date
            if pos == 0 or c == 0.0:
                continue
            later = float(np.prod([float(r) for e, r in zip(splits["ex_date"], splits["ratio"], strict=True)
                                   if e > ex])) if len(splits) else 1.0
            ref = close[pos - 1]
            factor = 1.0 - (c / later) / ref
            if not (np.isfinite(factor) and factor > 0):
                raise ValueError(f"dividend on {ex.date()}: cash {c:g} against a close of {ref * later:g} "
                                 "gives no positive factor")
            f[:pos] *= factor
        out[PRICES] = out[PRICES].mul(f, axis=0)
    out = out[RAW_COLUMNS]
    out.index = idx.rename("date")
    return out


def bars(raw: pd.DataFrame, actions: pd.DataFrame, *, asof: date | pd.Timestamp | None = None) -> pd.DataFrame:
    """``bars_daily``'s shape derived: split-adjusted open..close and volume, and ``adj_close`` with dividends."""
    split_only = series(raw, actions, dividends=False, asof=asof)
    out = split_only.copy()
    out["adj_close"] = series(raw, actions, dividends=True, asof=asof)["close"]
    return out[BAR_COLUMNS]


def read_raw(con: Any, ticker: str) -> pd.DataFrame:
    """One ticker's ``bars_raw`` on a ``date`` index (empty frame when it has none)."""
    df = con.execute("SELECT date, open, high, low, close, volume, source FROM bars_raw WHERE ticker = ? "
                     "ORDER BY date", [ticker.upper().strip()]).df()
    out = df[[*RAW_COLUMNS, "source"]].copy()
    out[RAW_COLUMNS] = out[RAW_COLUMNS].astype(float)
    out.index = pd.DatetimeIndex(pd.to_datetime(df["date"])).astype("datetime64[ns]").rename("date")
    return out


def read_actions(con: Any, ticker: str) -> pd.DataFrame:
    """One ticker's ``corp_actions`` rows (``ex_date, kind, ratio, cash, source, fetched_at``)."""
    df = con.execute("SELECT ex_date, kind, ratio, cash, source, fetched_at FROM corp_actions WHERE ticker = ? "
                     "ORDER BY ex_date, kind", [ticker.upper().strip()]).df()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).astype("datetime64[ns]")
    return df
