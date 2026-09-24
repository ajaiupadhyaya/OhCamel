"""Macro dashboard: series transforms, summary statistics and the Taylor rule.

Transforms (applied to a series as published on FRED)
-----------------------------------------------------
* ``level``   -- ``x_t``
* ``yoy_pct`` -- ``100 (x_t / x_{t-1y} - 1)``, where ``x_{t-1y}`` is the
  observation dated one calendar year earlier (the last one on/before that
  date, within a tolerance of a few days, else missing)
* ``diff``    -- ``x_t - x_{t-1}`` (previous observation)
* ``mom_ann`` -- ``100 ((x_t / x_{t-1})^m - 1)`` with ``m`` periods per year
  inferred from the observation spacing (12 monthly, 4 quarterly, 52 weekly)

Taylor rule (Taylor 1993)
-------------------------
``i_t = r* + pi_t + 0.5 (pi_t - pi*) + 0.5 gap_t``

and the "balanced-approach" rule (Yellen 2012; Board of Governors *Monetary
Policy Report* policy-rule box) with a coefficient of 1.0 on the gap.
``pi_t`` = core PCE inflation, year over year (FRED ``PCEPILFE``);
``gap_t = 100 (GDPC1 - GDPPOT)/GDPPOT`` (real GDP vs CBO potential, FRED).
``r*`` and ``pi*`` are the rule's parameters (Taylor's 1993 calibration is
``r* = 2``, ``pi* = 2``), exposed as user inputs, not market data.

References
----------
* Taylor, J. B. (1993), "Discretion versus Policy Rules in Practice",
  Carnegie-Rochester Conference Series on Public Policy 39.
* Yellen, J. L. (2012), "Perspectives on Monetary Policy", speech, Boston
  Economic Club, June 6.
* Board of Governors of the Federal Reserve System, *Monetary Policy Report*
  (policy rules box), various issues.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

SERIES_PATH = Path(__file__).with_name("series.json")
TRANSFORMS = ("level", "yoy_pct", "diff", "mom_ann")
Transform = Literal["level", "yoy_pct", "diff", "mom_ann"]
TAYLOR_1993 = {"r_star": 2.0, "pi_star": 2.0}  # the rule's original calibration (Taylor 1993)

__all__ = ["load_series_config", "infer_periods_per_year", "value_one_period_ago", "apply_transform",
           "summarize", "output_gap", "taylor_rule", "TRANSFORMS", "TAYLOR_1993"]


@lru_cache(maxsize=1)
def _config_cached() -> dict[str, Any]:
    return json.loads(SERIES_PATH.read_text())


def load_series_config() -> dict[str, Any]:
    """Dashboard series definitions from ``series.json`` (a deep copy)."""
    import copy

    cfg = copy.deepcopy(_config_cached())
    for s in cfg["series"]:
        if s["transform"] not in TRANSFORMS:
            raise ValueError(f"unknown transform {s['transform']} for {s['id']}")
    return cfg


def infer_periods_per_year(s: pd.Series) -> int:
    """Observation frequency from the median spacing of the index:
    daily 252, weekly 52, monthly 12, quarterly 4, annual 1."""
    idx = s.dropna().index
    if len(idx) < 3:
        raise ValueError("too few observations to infer frequency")
    med = float(np.median(np.diff(idx.values).astype("timedelta64[D]").astype(float)))
    if med <= 4:
        return 252
    if med <= 10:
        return 52
    if med <= 45:
        return 12
    if med <= 135:
        return 4
    return 1


def value_one_period_ago(s: pd.Series, offset: pd.DateOffset, tol_days: int = 10) -> pd.Series:
    """For each date ``t``, the last observation on/before ``t - offset``,
    or NaN if that observation is more than ``tol_days`` older than the target."""
    s = s.dropna()
    target = s.index - offset
    pos = s.index.searchsorted(target, side="right") - 1
    ok = pos >= 0
    pc = np.clip(pos, 0, None)
    vals = s.to_numpy()[pc]
    gap = (target - s.index[pc]).days.to_numpy()
    ok = ok & (gap <= tol_days)
    return pd.Series(np.where(ok, vals, np.nan), index=s.index)


def apply_transform(s: pd.Series, transform: Transform) -> pd.Series:
    """Apply one of :data:`TRANSFORMS` (module docstring)."""
    x = s.dropna().astype(float)
    if transform == "level":
        out = x
    elif transform == "diff":
        out = x.diff()
    elif transform == "yoy_pct":
        prior = value_one_period_ago(x, pd.DateOffset(years=1))
        out = 100.0 * (x / prior - 1.0)
    elif transform == "mom_ann":
        m = infer_periods_per_year(x)
        out = 100.0 * ((x / x.shift(1)) ** m - 1.0)
    else:
        raise ValueError(f"unknown transform {transform!r}; choose from {TRANSFORMS}")
    return out.dropna().rename(s.name)


def summarize(x: pd.Series, percentile_years: float = 10.0, spark_years: float = 3.0,
              spark_points: int = 80) -> dict[str, Any]:
    """Latest value/date, changes vs 1M/3M/1Y, percentile rank over
    ``percentile_years`` and a down-sampled sparkline.

    A change "vs 1M" is ``x_last - x(asof: last date - 1 month)``: the
    difference from the value that was the latest one a month earlier.
    A horizon shorter than the series' own observation spacing (e.g. "1M"
    for quarterly GDP, which would silently be a quarter-on-quarter change)
    is reported as ``None``.
    """
    x = x.dropna()
    if x.empty:
        raise ValueError("empty series")
    last_d, last_v = x.index[-1], float(x.iloc[-1])
    spacing_days = (float(np.median(np.diff(x.index.values).astype("timedelta64[D]").astype(float)))
                    if len(x) >= 3 else 0.0)
    changes: dict[str, float | None] = {}
    for label, off, days in (("1M", pd.DateOffset(months=1), 30.4), ("3M", pd.DateOffset(months=3), 91.3),
                             ("1Y", pd.DateOffset(years=1), 365.25)):
        if spacing_days > 1.5 * days:
            changes[label] = None
            continue
        ref = last_d - off
        prior = x[x.index <= ref]
        changes[label] = float(last_v - prior.iloc[-1]) if not prior.empty else None
    window_start = last_d - pd.DateOffset(days=int(365.25 * percentile_years))
    hist = x[x.index > window_start]
    pct = float((hist.to_numpy() <= last_v).mean())
    covered = float((last_d - hist.index[0]).days / 365.25)
    # the requested window is fully covered only if the series has data at or before its start
    complete = bool(x.index[0] <= window_start + pd.Timedelta(days=max(spacing_days, 1.0) * 1.5))
    spark = x[x.index > last_d - pd.DateOffset(days=int(365.25 * spark_years))]
    if len(spark) > spark_points:
        step = int(np.ceil(len(spark) / spark_points))
        keep = spark.iloc[::-1].iloc[::step].iloc[::-1]  # always keep the last point
        spark = keep
    return {
        "latest": last_v, "date": last_d, "change": changes, "percentile_10y": pct,
        "percentile_window": {
            "start": hist.index[0], "end": last_d, "n": int(len(hist)),
            "years_requested": float(percentile_years), "years_covered": covered, "complete": complete,
            "description": (f"percentile_10y ranks the latest value among the {len(hist)} observations from "
                            f"{hist.index[0].date()} to {last_d.date()} ({covered:.1f} years"
                            + ("" if complete else f"; shorter than the {percentile_years:g}-year window because "
                               "the series/request starts later") + ")"),
        },
        "history_min": float(hist.min()), "history_max": float(hist.max()),
        "sparkline": {"index": list(spark.index), "values": spark.to_numpy().tolist()},
    }


def output_gap(gdpc1: pd.Series, gdppot: pd.Series) -> pd.Series:
    """``100 (Y - Y*) / Y*`` on quarters where real GDP is published."""
    y = gdpc1.dropna()
    pot = gdppot.reindex(y.index)
    return (100.0 * (y - pot) / pot).dropna().rename("output_gap")


def taylor_rule(core_pce_index: pd.Series, gdpc1: pd.Series, gdppot: pd.Series, r_star: float,
                pi_star: float, fed_funds: pd.Series | None = None,
                infl_coef: float = 0.5) -> tuple[pd.DataFrame, list[str]]:
    """Monthly Taylor (1993) and balanced-approach prescriptions.

    ``i = r* + pi + infl_coef (pi - pi*) + b gap``, ``b = 0.5`` (Taylor) or
    ``1.0`` (balanced approach). The quarterly gap is assigned to the three
    months of its quarter and carried at most two further quarters past the
    latest GDP release (stated in ``notes``). ``fed_funds`` (daily ``DFF``)
    is averaged by month for comparison.
    """
    notes: list[str] = []
    pce = core_pce_index.dropna()
    pce.index = pce.index.to_period("M").to_timestamp()
    pi = apply_transform(pce, "yoy_pct").rename("inflation")
    gap_q = output_gap(gdpc1, gdppot)
    gap_m = gap_q.copy()
    gap_m.index = gap_m.index.to_period("M").to_timestamp()
    months = pd.date_range(min(pi.index.min(), gap_m.index.min()), pi.index.max(), freq="MS")
    gap = gap_m.reindex(months).ffill(limit=8)
    last_q_end = gap_m.index.max() + pd.DateOffset(months=3)
    if pi.index.max() >= last_q_end:
        notes.append(f"output gap after {gap_m.index.max().date()} (latest GDP quarter) is carried forward")
    df = pd.DataFrame({"inflation": pi, "output_gap": gap}).dropna()
    base = r_star + df["inflation"] + infl_coef * (df["inflation"] - pi_star)
    df["taylor_1993"] = base + 0.5 * df["output_gap"]
    df["balanced_approach"] = base + 1.0 * df["output_gap"]
    if fed_funds is not None and fed_funds.notna().any():
        ff = fed_funds.dropna()
        ffm = ff.groupby(ff.index.to_period("M")).mean()
        ffm.index = ffm.index.to_timestamp()
        df["fed_funds"] = ffm.reindex(df.index)
        df["gap_taylor_minus_ff"] = df["taylor_1993"] - df["fed_funds"]
        df["gap_balanced_minus_ff"] = df["balanced_approach"] - df["fed_funds"]
    df.index.name = "date"
    return df, notes
