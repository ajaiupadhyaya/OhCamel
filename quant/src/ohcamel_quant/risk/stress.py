"""Stress testing: historical scenario replay and conditional (factor) stress.

(a) Historical replay. Each scenario in ``scenarios.json`` is a dated window
of real market history. Holding i's scenario return is its ACTUAL cumulative
price return over the window, ``R_i = P_i(end)/P_i(base) - 1``; the book is
held buy-and-hold from the base close, so the portfolio P&L path is
``PnL_t = sum_i w_i R_i(t)``. A holding with no prices covering the window
(e.g. it was not yet listed) is PROXIED by ``beta_i R_bench(t)`` with ``beta_i``
estimated by OLS on the real overlapping daily history closest in time to
the window; such names are flagged. If neither real prices nor a proxy
beta exist, the scenario is marked incomplete and no portfolio number is
produced -- nothing is invented.

(b) Conditional stress (Kupiec 1998, "Stress testing in a value at risk
framework", J. Derivatives 6(1)). Shocking a subset ``s`` of assets by the
vector ``x`` moves the others by their conditional mean under a joint normal
with covariance ``Sigma`` (zero mean over the shock horizon):

``E[r_o | r_s = x] = Sigma_os Sigma_ss^{-1} x``,
``Cov[r_o | r_s = x] = Sigma_oo - Sigma_os Sigma_ss^{-1} Sigma_so``.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .core import covariance

SCENARIO_FILE = Path(__file__).with_name("scenarios.json")
_TOL_DAYS = 7  # a window bound may fall on a non-session (weekend/holiday) up to this many days


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Any], ...]:
    raw = json.loads(SCENARIO_FILE.read_text())
    out = []
    for s in raw["scenarios"]:
        s = dict(s)
        s["start"], s["end"] = pd.Timestamp(s["start"]), pd.Timestamp(s["end"])
        if s["end"] < s["start"]:
            raise ValueError(f"scenario {s['id']}: end before start")
        if s.get("anchor", "start_close") not in ("start_close", "prior_close"):
            raise ValueError(f"scenario {s['id']}: bad anchor")
        out.append(s)
    return tuple(out)


def load_scenarios() -> list[dict[str, Any]]:
    """All scenario definitions (start/end as Timestamps)."""
    return [dict(s) for s in _load()]


def get_scenario(sid: str) -> dict[str, Any]:
    for s in _load():
        if s["id"] == sid:
            return dict(s)
    raise ValueError(f"unknown scenario {sid!r}")


def window_bounds(px: pd.Series | None, scenario: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Base and end sessions of ``scenario`` in the price series ``px``, or None
    if ``px`` does not cover the window.

    ``start_close``: base = last session <= start; ``prior_close``: base = last
    session < start. End = last session <= end (must be after base). Each bound
    must lie within a week of the nominal date, so a series that merely
    starts or stops near the window is not mistaken for coverage.
    """
    if px is None:
        return None
    px = px.dropna()
    if px.empty:
        return None
    idx = px.index
    start, end = scenario["start"], scenario["end"]
    before = idx[idx < start] if scenario.get("anchor") == "prior_close" else idx[idx <= start]
    if before.empty or (start - before[-1]).days > _TOL_DAYS:
        return None
    base = before[-1]
    upto = idx[idx <= end]
    if upto.empty or upto[-1] <= base or (end - upto[-1]).days > _TOL_DAYS:
        return None
    if scenario.get("anchor") == "prior_close" and upto[-1] < start:
        return None
    return base, upto[-1]


def proxy_beta(asset_px: pd.Series, bench_px: pd.Series, start: pd.Timestamp, end: pd.Timestamp,
               max_obs: int = 756, min_obs: int = 60) -> dict[str, Any] | None:
    """OLS beta of daily asset returns on benchmark returns, using the (up to)
    ``max_obs`` overlapping real sessions nearest in time to ``[start, end]``
    (excluding the window itself). ``beta = Cov(r_a, r_b)/Var(r_b)``."""
    if asset_px is None or bench_px is None:
        return None
    ra = asset_px.dropna().pct_change().iloc[1:]
    rb = bench_px.dropna().pct_change().iloc[1:]
    df = pd.concat([ra.rename("a"), rb.rename("b")], axis=1, join="inner").dropna()
    df = df[(df.index < start) | (df.index > end)]
    if len(df) < min_obs:
        return None
    mid = start + (end - start) / 2
    dist = np.abs((df.index - mid).days.to_numpy())
    df = df.iloc[np.sort(np.argsort(dist, kind="stable")[:max_obs])]
    c = np.cov(df["a"], df["b"], ddof=1)
    if c[1, 1] <= 0:
        return None
    beta = c[0, 1] / c[1, 1]
    r2 = c[0, 1] ** 2 / (c[0, 0] * c[1, 1]) if c[0, 0] > 0 else math.nan
    return {"beta": float(beta), "obs": int(len(df)), "r2": float(r2),
            "from": df.index[0], "to": df.index[-1]}


def _max_drawdown(wealth: np.ndarray) -> float:
    peak = np.maximum.accumulate(wealth)
    return float(np.min(wealth / peak - 1.0))


def replay(scenario: dict[str, Any], weights: dict[str, float], prices: dict[str, pd.Series | None],
           benchmark: str, bench_px: pd.Series | None, notional: float = 1.0) -> dict[str, Any]:
    """Replay one historical scenario on the portfolio (see module docstring).

    ``prices`` maps each holding to its full real adjusted-close history (or
    None if unavailable); ``bench_px`` is the benchmark's.
    """
    notes: list[str] = []
    bench_bounds = window_bounds(bench_px, scenario)
    cal_src = benchmark if bench_bounds else None
    bounds = bench_bounds
    if bounds is None:
        for t in weights:
            b = window_bounds(prices.get(t), scenario)
            if b is not None:
                bounds, cal_src = b, t
                break
    base_out = {"id": scenario["id"], "name": scenario["name"], "category": scenario.get("category"),
                "description": scenario.get("description"), "start": scenario["start"],
                "end": scenario["end"], "anchor": scenario.get("anchor", "start_close")}
    if bounds is None:
        return {**base_out, "complete": False, "portfolio_return": None, "pnl_usd": None,
                "benchmark": benchmark, "benchmark_return": None, "max_drawdown": None, "path": None,
                "proxied": [], "missing": list(weights),
                "positions": [{"ticker": t, "weight": w, "source": "missing"} for t, w in weights.items()],
                "notes": [f"no price data covering {scenario['start'].date()}..{scenario['end'].date()} "
                          f"for the benchmark ({benchmark}) or any holding"]}
    base, last = bounds
    src_px = bench_px if cal_src == benchmark else prices[cal_src]
    cal = src_px.dropna().index
    cal = cal[(cal >= base) & (cal <= last)]
    bench_path = None
    if bench_bounds is not None:
        bp = bench_px.dropna().reindex(cal, method="ffill")
        bench_path = (bp / bp.iloc[0] - 1.0).to_numpy()
    else:
        notes.append(f"benchmark {benchmark} has no data in this window; proxies unavailable")

    paths: dict[str, np.ndarray] = {}
    positions: list[dict[str, Any]] = []
    complete = True
    for t, w in weights.items():
        px = prices.get(t)
        rec: dict[str, Any] = {"ticker": t, "weight": w}
        if window_bounds(px, scenario) is not None:
            s = px.dropna()
            s = s[s.index <= last]
            aligned = s.reindex(cal.union(s.index)).ffill().reindex(cal)
            path = (aligned / aligned.iloc[0] - 1.0).to_numpy()
            rec["source"] = "actual"
        else:
            pb = proxy_beta(px, bench_px, scenario["start"], scenario["end"]) if (
                bench_path is not None and px is not None) else None
            if pb is None:
                rec.update({"source": "missing", "return": None, "pnl_usd": None})
                positions.append(rec)
                complete = False
                continue
            path = pb["beta"] * bench_path
            rec.update({"source": "proxy", "beta": pb["beta"], "beta_obs": pb["obs"], "beta_r2": pb["r2"],
                        "beta_from": pb["from"], "beta_to": pb["to"]})
        paths[t] = path
        rec["return"] = float(path[-1])
        rec["pnl_usd"] = float(w * path[-1] * notional)
        rec["contribution"] = float(w * path[-1])
        positions.append(rec)

    proxied = [p["ticker"] for p in positions if p.get("source") == "proxy"]
    missing = [p["ticker"] for p in positions if p.get("source") == "missing"]
    if proxied:
        notes.append(f"PROXIED by beta x {benchmark} (no real prices in window): {', '.join(proxied)}")
    if missing:
        notes.append(f"INCOMPLETE: no prices and no overlapping history for a proxy beta: {', '.join(missing)}")
    out = {**base_out, "base_date": base, "end_date": last, "sessions": int(len(cal)),
           "complete": complete, "proxied": proxied, "missing": missing, "positions": positions,
           "benchmark": benchmark,
           "benchmark_return": float(bench_path[-1]) if bench_path is not None else None, "notes": notes}
    if not complete:
        out.update({"portfolio_return": None, "pnl_usd": None, "max_drawdown": None, "path": None})
        return out
    port = sum(weights[t] * paths[t] for t in paths)
    wealth = 1.0 + np.asarray(port, dtype=float)
    daily = wealth[1:] / wealth[:-1] - 1.0 if wealth.size > 1 else np.array([])
    worst = int(np.argmin(daily)) + 1 if daily.size else None
    out.update({
        "portfolio_return": float(port[-1]),
        "pnl_usd": float(port[-1] * notional),
        "max_drawdown": _max_drawdown(np.concatenate([[1.0], wealth])),
        "worst_day": {"date": cal[worst], "return": float(daily[worst - 1])} if worst else None,
        "path": {"dates": list(cal), "portfolio": port,
                 "benchmark": bench_path if bench_path is not None else None},
    })
    return out


def conditional_stress(returns: pd.DataFrame, weights: dict[str, float], shocks: dict[str, float],
                       method: str = "ewma", lam: float = 0.94, notional: float = 1.0) -> dict[str, Any]:
    """Kupiec (1998) conditional stress test.

    ``returns`` must contain every holding and every shocked ticker. Shocks are
    simple-return moves (decimals). Portfolio P&L is linear:
    ``sum_i w_i move_i``. The zero-mean assumption reflects the stress horizon
    (the conditional mean is scale-free in the horizon under iid returns).
    """
    if not shocks:
        raise ValueError("at least one shock is required")
    missing = [t for t in [*weights, *shocks] if t not in returns.columns]
    if missing:
        raise ValueError(f"no return series for {missing}")
    cols = list(dict.fromkeys([*shocks, *weights]))
    _, cov = covariance(returns[cols], method, lam)
    si = [cols.index(t) for t in shocks]
    oi = [i for i in range(len(cols)) if i not in si]
    x = np.array([shocks[t] for t in shocks], dtype=float)
    Sss = cov[np.ix_(si, si)]
    Sos = cov[np.ix_(oi, si)]
    Soo = cov[np.ix_(oi, oi)]
    cond = np.linalg.cond(Sss)
    if cond > 1e10:
        raise ValueError("shocked assets are (nearly) collinear; shock fewer or less correlated assets")
    B = np.linalg.solve(Sss, Sos.T).T                       # Sigma_os Sigma_ss^{-1}
    move = np.empty(len(cols))
    move[si] = x
    move[oi] = B @ x
    cond_cov = Soo - B @ Sos.T
    w = np.array([weights.get(c, 0.0) for c in cols])
    w_o = w[oi]
    resid_sd = math.sqrt(max(float(w_o @ cond_cov @ w_o), 0.0))
    uncond_sd = np.sqrt(np.diag(cov))
    positions = []
    for i, c in enumerate(cols):
        if c not in weights and c not in shocks:
            continue
        positions.append({
            "ticker": c, "weight": weights.get(c, 0.0), "shocked": c in shocks, "move": float(move[i]),
            "move_in_sd": float(move[i] / uncond_sd[i]) if uncond_sd[i] > 0 else None,
            "pnl": float(weights.get(c, 0.0) * move[i]),
            "pnl_usd": float(weights.get(c, 0.0) * move[i] * notional),
            "sensitivity": ({shocks_t: float(B[oi.index(i), j]) for j, shocks_t in enumerate(shocks)}
                            if i in oi else None),
        })
    pnl = float(w @ move)
    return {
        "portfolio_return": pnl, "pnl_usd": pnl * notional, "positions": positions,
        "conditional_residual_vol_daily": resid_sd,
        "shock_zscores": {t: float(shocks[t] / uncond_sd[cols.index(t)]) for t in shocks},
        "condition_number": float(cond),
    }
