"""Point-in-time features by date (compute plan M1; EXP-Q01's five features).

Each feature column ``f`` has a companion ``f + "__asof"``: the timestamp of
the newest datum it used. ``assert_point_in_time`` refuses a frame where any
companion is later than its row's date (Review Focus 2). Price features use
data up to and including the decision close ``t`` (a signal at the close of
``t`` is acted on at the open of ``t+1``: charter principle 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..backtest.engine import LookAheadError

FEATURES = ("mom_12_1", "rev_1m", "vol_60d", "beta_252d", "dollar_vol_20d")
ASOF = "__asof"
MIN_HISTORY = 252
ONE_BDAY = pd.offsets.BDay(1)  # FRED: a value is available the business day after its date


@dataclass(frozen=True)
class Panel:
    """Daily bars on one session index, one column per ticker: raw open/close,
    the vendor's fully adjusted close, volume."""

    open: pd.DataFrame
    close: pd.DataFrame
    adj_close: pd.DataFrame
    volume: pd.DataFrame

    def __post_init__(self) -> None:
        for name in ("open", "close", "volume"):
            f = getattr(self, name)
            if not (f.index.equals(self.adj_close.index) and list(f.columns) == list(self.adj_close.columns)):
                raise ValueError(f"panel.{name} must share adj_close's index and columns")
        if not self.adj_close.index.is_monotonic_increasing:
            raise ValueError("panel index must be sorted")

    @property
    def adj_open(self) -> pd.DataFrame:
        """Open on the adjusted basis: open x adj_close / close (one factor per session)."""
        return self.open * (self.adj_close / self.close)

    def truncate(self, end: pd.Timestamp) -> Panel:
        return Panel(*(getattr(self, n).loc[:end] for n in ("open", "close", "adj_close", "volume")))


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The last session of each month that has a later session (so the month is complete)."""
    s = pd.Series(index, index=index)
    last = s.groupby(index.to_period("M")).max()
    return pd.DatetimeIndex(last.iloc[:-1].to_numpy())


def build_features(panel: Panel, dates: pd.DatetimeIndex, benchmark: str = "SPY") -> pd.DataFrame:
    """Rows ``(date, ticker)`` for each date with >= 252 prior sessions; columns FEATURES and their ``__asof``."""
    px = panel.adj_close
    if benchmark not in px.columns:
        raise ValueError(f"benchmark {benchmark} must be in the panel")
    lr = np.log(px).diff()
    pos = px.index.get_indexer(dates)
    if (pos < 0).any():
        raise ValueError("every feature date must be a session in the panel")
    parts = []
    for d, p in zip(dates, pos, strict=True):
        if p < MIN_HISTORY:
            continue
        win = lr.iloc[p - 251:p + 1]
        b = win[benchmark]
        cov = ((win - win.mean()).mul(b - b.mean(), axis=0)).sum() / (len(win) - 1)
        f = pd.DataFrame({
            "mom_12_1": px.iloc[p - 21] / px.iloc[p - 252] - 1.0,
            "rev_1m": px.iloc[p] / px.iloc[p - 21] - 1.0,
            "vol_60d": lr.iloc[p - 59:p + 1].std(ddof=1) * math.sqrt(252),
            "beta_252d": cov / b.var(ddof=1),
            "dollar_vol_20d": (panel.close.iloc[p - 19:p + 1] * panel.volume.iloc[p - 19:p + 1]).mean(),
        })
        for c in FEATURES:
            f[c + ASOF] = d
        f.index = pd.MultiIndex.from_product([[d], f.index], names=["date", "ticker"])
        parts.append(f)
    if not parts:
        return pd.DataFrame(columns=[*FEATURES, *(c + ASOF for c in FEATURES)],
                            index=pd.MultiIndex.from_arrays([[], []], names=["date", "ticker"]))
    return pd.concat(parts)


def assert_point_in_time(df: pd.DataFrame) -> None:
    """Raise LookAheadError when a feature's source timestamp is after its row's date."""
    feats = [c for c in df.columns if not c.endswith(ASOF) and (c in FEATURES or c + ASOF in df.columns)]
    rows = pd.DatetimeIndex(df.index.get_level_values("date"))
    for c in feats:
        if c + ASOF not in df.columns:
            raise ValueError(f"feature {c!r} has no source timestamp column {c + ASOF!r}")
        src = pd.DatetimeIndex(df[c + ASOF])
        bad = np.flatnonzero(np.asarray(src > rows))
        if bad.size:
            i = int(bad[0])
            raise LookAheadError(f"feature {c!r} at {rows[i].date()} uses data from {src[i].date()} "
                                 f"({bad.size} rows look ahead)")


def asof_join(series: pd.Series, dates: pd.DatetimeIndex, lag: pd.DateOffset = ONE_BDAY) -> pd.DataFrame:
    """For each date, the newest value whose availability (observation date + ``lag``) is on or before it."""
    s = series.dropna().sort_index()
    avail = pd.DatetimeIndex(s.index) + lag
    pos = np.searchsorted(avail.to_numpy(), pd.DatetimeIndex(dates).to_numpy(), side="right") - 1
    ok = pos >= 0
    value = np.where(ok, s.to_numpy()[np.clip(pos, 0, None)], np.nan)
    asof = pd.DatetimeIndex(np.where(ok, avail.to_numpy()[np.clip(pos, 0, None)], np.datetime64("NaT", "ns")))
    return pd.DataFrame({"value": value, ASOF: asof}, index=pd.DatetimeIndex(dates))
