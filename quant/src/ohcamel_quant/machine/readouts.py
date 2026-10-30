"""Ticker readouts and the cluster order: what the Machine's outer ring says about each daily ticker.

Pure functions of one wide frame of warehouse closes (``adj_close``: dates ascending by ticker,
NaN where a ticker has no bar). Nothing is fetched or invented; a ticker with no usable closes has
no readout. Each readout carries ``n``, the closes behind its figures, so a short history is never
passed off as a year. Output is plain Python (floats rounded for the wire, lists not arrays) and
carries no NaN or inf, so it goes straight into a node's ``m``.

Returns are log returns between consecutive rows, so a missing close drops the returns on either
side of it rather than stretching one over the gap. Correlations are Pearson over the pairs of days
both tickers have, and only count when there are ``min_overlap`` of them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

TRADING_DAYS = 252  # annualises daily volatility


def _window(closes: pd.DataFrame, window: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The last ``window + 1`` rows, with closes that are not finite and positive as NaN (a log of
    them would poison every number downstream), and the daily log returns between those rows."""
    tail = closes.iloc[-(window + 1):].astype(float)
    tail = tail.where(np.isfinite(tail) & (tail > 0))
    return tail, np.log(tail).diff()


def _spark(px: np.ndarray, points: int) -> list[float]:
    """Every ``ceil(len / points)``-th close over the first, then the last: at most ``points`` + 1."""
    n = len(px)
    idx = list(range(0, n, math.ceil(n / points)))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [round(float(px[i] / px[0]), 4) for i in idx]


def _peers(corr: pd.DataFrame, n_peers: int) -> dict[str, list[list]]:
    """Each name's ``n_peers`` best-correlated other names, best first (ties by name); NaN pairs are out. One
    stable sort of the whole matrix with its names in order, which is what puts a tie in name order, and a NaN
    sorts last, so five hundred names cost one sort and no pandas Series each."""
    order = corr.columns.argsort()
    names = [str(t) for t in corr.columns[order]]
    values = corr.to_numpy(dtype=float)[np.ix_(order, order)]
    np.fill_diagonal(values, np.nan)                            # no name is its own peer
    best = np.argsort(-values, axis=1, kind="stable")[:, :n_peers]
    top = np.take_along_axis(values, best, axis=1)
    return {t: [[names[j], round(float(c), 2)] for j, c in zip(row, cs, strict=True) if not math.isnan(c)]
            for t, row, cs in zip(names, best, top, strict=True)}


def ticker_readouts(
    closes: pd.DataFrame, *, window: int = 252, points: int = 60, n_peers: int = 6, min_overlap: int = 120
) -> dict[str, dict]:
    """One readout per ticker with at least two usable closes in the last ``window + 1`` rows:
    ``spark`` (closes over the first), ``ret1y`` (last / first - 1), ``vol`` (sample std of daily log
    returns, annualised), ``last``, ``n`` and ``peers`` (``[ticker, corr]``, correlation of log
    returns over the window, only among names with ``min_overlap`` shared returns). ``n`` is how many
    closes every figure rests on: ``ret1y`` is a one-year return only when ``n == window + 1``, and
    otherwise the return over the last ``n`` sessions. ``vol`` is left out when the ticker has fewer
    than two returns, since a sample std of one number is undefined."""
    tail, ret = _window(closes, window)
    out: dict[str, dict] = {}
    for t in tail.columns:
        px = tail[t].dropna().to_numpy()
        if len(px) < 2:
            continue
        r = ret[t].dropna().to_numpy()
        read: dict = {"spark": _spark(px, points), "ret1y": round(float(px[-1] / px[0] - 1), 4)}
        if len(r) >= 2:
            read["vol"] = round(float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS)), 4)
        read["last"] = round(float(px[-1]), 2)
        read["n"] = len(px)
        out[str(t)] = read
    if out:
        peers = _peers(ret.corr(min_periods=min_overlap), n_peers)
        for t, read in out.items():
            read["peers"] = peers[t]
    return out


def cluster_order(
    closes: pd.DataFrame, tickers: Sequence[str], *, window: int = 252, min_overlap: int = 120
) -> list[str]:
    """``tickers`` ordered so that names which move together sit together: average-linkage clustering
    on ``1 - corr`` of log returns over the window, read off as the dendrogram's leaves. A name needs
    ``min_overlap`` returns of its own to be clustered (and some variation, for a correlation to
    exist); a pair that overlaps too little counts as unrelated. The names left out follow in
    alphabetical order. Every input ticker appears exactly once."""
    names = list(dict.fromkeys(tickers))
    _, ret = _window(closes[[t for t in names if t in closes.columns]], window)
    corr = ret.corr(min_periods=min_overlap)
    ok = [t for t in ret.columns if not np.isnan(corr.at[t, t])]
    if len(ok) > 1:
        dist = 1.0 - corr.loc[ok, ok].fillna(0.0).to_numpy()   # unrelated: distance 1
        dist = np.clip((dist + dist.T) / 2, 0.0, None)          # float noise must not make 1 - corr < 0
        np.fill_diagonal(dist, 0.0)
        ok = [ok[i] for i in leaves_list(linkage(squareform(dist), "average"))]
    placed = set(ok)
    return ok + sorted(t for t in names if t not in placed)
