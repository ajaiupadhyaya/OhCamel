"""``factors.screen``: every expression in the owner's trial ledger, re-screened weekly on the warehouse universe.

``make factor`` screens on nine committed ETFs, where a cross-sectional correlation is mostly noise. This job
takes every DISTINCT expression in ``research/experiments/FACTORS/trials.jsonl`` and runs the same screen on
the warehouse's universe (hundreds of names). It uses the same exploration window, the same lint, qlib's
signal report, and RD-Agent's redundancy screen against all 157 Alpha158 features, evaluated one at a time so
memory stays flat.

What it will not do: write to the ledger, invent an expression, look past 2023-12-31 (the holdout belongs to
a pre-registered battery), or gate anything. A good screen is a reason for the owner to write a
pre-registration. The universe is today's constituents, so delisted names are absent (survivorship, stated
on the artifact).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..factors import alpha158 as A
from ..factors import expr as E
from ..factors import workbench as W
from ..jobs.artifacts import ArtifactSpec

KIND = "factors.screen"
LEDGER = Path(W.__file__).resolve().parents[4] / "research/experiments/FACTORS/trials.jsonl"
SURVIVORSHIP = "current constituents; delisted names absent"
TOP_SIMILAR = 3


def distinct_trials(path: Path) -> list[dict[str, Any]]:
    """The first record of each hash: the expression and the hypothesis as the owner first wrote them."""
    if not path.exists():
        return []
    seen: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            seen.setdefault(r["hash"], r)
    return list(seen.values())


def universe(ctx: Any) -> tuple[list[str], bool]:
    try:
        rows = ctx.warehouse.execute("SELECT DISTINCT ticker FROM universe_members ORDER BY 1").fetchall()
        return [r[0] for r in rows], True
    except DataUnavailable:
        return list(W.NAMES), False


def load_fields(ctx: Any, names: list[str], end: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], list[str]]:
    bars, missing = {}, []
    for t in names:
        ctx.check_cancelled()
        try:
            df = ctx.market.ohlcv(t, end=end.date()).data.dropna()
        except DataUnavailable:
            missing.append(t)
            continue
        if len(df) >= 260:
            bars[t] = df.rename(columns={"adj_close": "close_adj"})
        else:
            missing.append(t)
    return (A.adjusted_fields(bars) if bars else {}), missing


def screen(trials: list[dict[str, Any]], fields: dict[str, pd.DataFrame],
           library: list[tuple[str, str]] | None = None, progress=lambda f, m: None) -> dict[str, pd.DataFrame]:
    rows, decay, sims = [], [], []
    values: dict[str, pd.DataFrame] = {}
    for t in trials:
        try:
            v = E.evaluate(t["expression"], fields)
        except (E.ExprError, ValueError) as e:
            rows.append({"hash": t["hash"], "expression": t["expression"], "lint_ok": False,
                         "failed": f"does not evaluate: {e}"})
            continue
        checks = W.lint(v)
        ok = all(c.ok for c in checks)
        row = {"hash": t["hash"], "expression": t["expression"], "hypothesis": t.get("hypothesis", ""),
               "first_logged": t.get("ts", ""), "lint_ok": ok,
               "failed": "; ".join(f"{c.name}: {c.detail}" for c in checks if not c.ok)}
        if ok:
            rep = W.signal_report(v, fields["open"])
            row.update(rep["summary"])
            decay += [{"hash": t["hash"], **d} for d in rep["decay"].to_dict("records")]
            values[t["hash"]] = v
        rows.append(row)
    lib = library if library is not None else A.feature_config()
    best: dict[str, list[tuple[float, str, float]]] = {h: [] for h in values}
    for i, (name, e) in enumerate(lib):                    # one library feature in memory at a time
        f = E.evaluate(e, fields).to_numpy(float)
        for h, v in values.items():
            c = W._xs_corr(v.to_numpy(float), f)
            m = float(np.nanmean(c)) if np.isfinite(c).any() else np.nan
            if np.isfinite(m):
                best[h] = sorted([*best[h], (abs(m), name, m)], reverse=True)[:TOP_SIMILAR]
        progress(0.5 + 0.5 * (i + 1) / len(lib), name)
    for h, top in best.items():
        sims += [{"hash": h, "rank": k + 1, "feature": n, "mean_xs_corr": m, "duplicate": a >= W.DUPLICATE}
                 for k, (a, n, m) in enumerate(top)]
    screens = pd.DataFrame(rows)
    if len(sims):
        dup = pd.DataFrame(sims).query("duplicate").groupby("hash")["feature"].first()
        screens["duplicate_of"] = screens["hash"].map(dup)
    return {"screens": screens, "decay": pd.DataFrame(decay), "similar": pd.DataFrame(sims)}


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    ledger = Path(params.get("ledger", LEDGER))
    trials = distinct_trials(ledger)
    if not trials:
        return ArtifactSpec(tables={"screens": pd.DataFrame(columns=["hash", "expression"])}, data_asof=None,
                            provenance=[], notes=[f"{ledger.name} has no trials: the owner adds one with make factor"],
                            verdict="INSUFFICIENT DATA", verdict_detail="NO TRIALS IN THE LEDGER YET")
    names, from_wh = universe(ctx)
    names = names[: int(params["max_names"])] if params.get("max_names") else names
    fields, missing = load_fields(ctx, names, W.EXPLORATION_END)
    if not fields or fields["close"].shape[1] < 5:
        return ArtifactSpec(tables={"screens": pd.DataFrame()}, data_asof=None, provenance=[],
                            notes=[f"{len(missing)} names without bars"], verdict="INSUFFICIENT DATA",
                            verdict_detail="FEWER THAN 5 NAMES WITH EXPLORATION-WINDOW BARS")
    lib = None if params.get("library", "alpha158") == "alpha158" else []
    out = screen(trials, fields, lib, progress=ctx.progress)
    s = out["screens"]
    n_ok = int(s["lint_ok"].sum())
    n_dup = int(s.get("duplicate_of", pd.Series(dtype=object)).notna().sum())
    width = fields["close"].shape[1]
    return ArtifactSpec(
        tables={**out, "universe": pd.DataFrame({"ticker": list(fields["close"].columns)})},
        data_asof=str(fields["close"].index[-1].date()),
        provenance=[{"source": "warehouse:bars_daily" if from_wh else "fixtures", "fetched_at": "",
                     "detail": {"names": width, "missing": len(missing), "window_end": str(W.EXPLORATION_END.date())}}],
        notes=[f"{len(trials)} distinct expressions = n_trials for any DSR on a factor promoted from this ledger",
               f"exploration window only (to {W.EXPLORATION_END.date()}); labels purged at its end; reported, never gated",
               *([] if from_wh else ["no warehouse: the nine committed ETFs only"])],
        survivorship=SURVIVORSHIP if from_wh else None,
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"{len(trials)} TRIALS · {n_ok} PASS LINT · {n_dup} DUPLICATE · {width} NAMES · TO 2023-12-31")
