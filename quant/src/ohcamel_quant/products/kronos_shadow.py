"""``kronos.shadow``: Kronos and the league's incumbents forecast tomorrow's variance tonight, on the record.

EXP-K01 scored Kronos-small retrospectively and it FAILED. This job keeps measuring it PROSPECTIVELY, which
is the cleanest test a model can have. Each night, after the daily bars are in, it does three things:

1. **Forecast.** For every name it writes one ledger row per model: Kronos (EXP-K01's frozen design, run in the
   ``research/kronos`` venv as a subprocess, so torch never enters this process) and GARCH, GJR, EGARCH and
   EWMA (P2's own functions, refitted tonight on the last 1000 sessions). Every row forecasts the session
   AFTER the last bar, so it is made before that session exists.
2. **Never rewrite.** The ledger is carried forward from the previous artifact. A (symbol, asof) already in it
   is never replaced, so a rerun cannot improve a forecast after the fact.
3. **Score.** A row is realised once the next bar arrives, and scored only if it was made before that session's
   16:00 New York close. A row made later is ``late``: kept, never scored. The first droplet run read its bars
   from a Yahoo cache filled before the close and forecast a session that had already closed. Bars now come
   from the warehouse first, and the ``late`` rule makes "made before it existed" a check rather than a hope. Its loss is P2's QLIKE against the squared return,
   aggregated per name and pooled, with Kronos against EWMA by the one-sided DM K01 used.

It sizes nothing and reaches no desk. The verdict is DESCRIPTIVE ONLY and always names EXP-K01's FAIL.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models import kronos_k01 as K
from ..models import vol_league as V
from ..risk.core import ewma_variance_path
from .io import previous_artifact

KIND = "kronos.shadow"
MODELS = ("kronos", "garch", "gjr", "egarch", "ewma")
LEDGER_COLUMNS = ["symbol", "asof", "model", "var", "q05", "made_at"]
CLOSE_NY = (16, 0)
MIN_POOLED_DAYS = 20
CONFIG = K.repo_root() / "research/experiments/EXP-K01/config.yaml"
DB_PATH: Path | None = None   # tests point this at their own jobs.sqlite


def now_utc() -> datetime:
    """The made_at clock (a function, so a test can replay nights)."""
    return datetime.now(UTC)


def kronos_python() -> Path | None:
    """The interpreter of research/kronos's venv: ``OHCAMEL_KRONOS_PYTHON`` in the image, the checkout's .venv
    in development. None when neither exists (the job then says so and forecasts only the incumbents)."""
    env = os.environ.get("OHCAMEL_KRONOS_PYTHON")
    cands = [Path(env)] if env else [K.repo_root() / "research/kronos/.venv/bin/python"]
    return next((p for p in cands if p.exists()), None)


def bars_of(ctx: Any, ticker: str, end: Any = None) -> Any:
    """Daily bars from the warehouse (written by the 18:30 ingest, so tonight's close is in it); the market
    facade only when there is no warehouse (offline tests, a fresh host). Never a vendor's intraday cache
    when the warehouse has the bar."""
    try:
        from ..warehouse import readers

        return readers.ohlcv(ctx.warehouse, ticker, end=end)
    except DataUnavailable:
        return ctx.market.ohlcv(ticker, end=end)


def target_close_utc(target: str) -> pd.Timestamp:
    t = pd.Timestamp(target).tz_localize("America/New_York") + pd.Timedelta(hours=CLOSE_NY[0], minutes=CLOSE_NY[1])
    return t.tz_convert("UTC")


def adjusted_bars(ds_frame: pd.DataFrame) -> pd.DataFrame:
    """O/H/L/C on the adjusted basis (factor adj_close / close per session), as Alpha158 and K01 use them."""
    k = ds_frame["adj_close"] / ds_frame["close"]
    return pd.DataFrame({"open": ds_frame["open"] * k, "high": ds_frame["high"] * k, "low": ds_frame["low"] * k,
                         "close": ds_frame["adj_close"], "volume": ds_frame["volume"]}, index=ds_frame.index)


def run_kronos(bars: dict[str, pd.DataFrame], python: Path, timeout_s: int = 1800, threads: int = 2) -> pd.DataFrame:
    """One subprocess for every name: bars in as parquet, forecasts out as csv."""
    long = pd.concat([b.assign(symbol=s, date=b.index) for s, b in bars.items()], ignore_index=True)
    with tempfile.TemporaryDirectory(prefix="kronos-shadow-") as d:
        src, out = Path(d) / "bars.parquet", Path(d) / "out.csv"
        long.to_parquet(src, index=False)
        subprocess.run([str(python), "-m", "ohcamel_kronos", "shadow", str(src), str(out), "--config", str(CONFIG),
                        "--threads", str(max(1, threads))],
                       check=True, timeout=timeout_s, capture_output=True)
        return pd.read_csv(out, dtype={"asof": str})


def incumbent_rows(sym: str, ret: pd.Series, made_at: str) -> list[dict[str, Any]]:
    """Tonight's next-session forecast from each incumbent, fitted on returns up to and including the last bar.
    A fit that fails is skipped for tonight, never guessed."""
    r = ret.to_numpy(float)
    asof = ret.index[-1].date().isoformat()
    rows = [{"symbol": sym, "asof": asof, "model": "ewma",
             "var": float(ewma_variance_path(r, V.EWMA_LAMBDA)[-1]), "q05": np.nan, "made_at": made_at}]
    if r.size >= V.EST_WINDOW:
        from .. import kernels

        y = 100.0 * r[-V.EST_WINDOW:]
        for k in V.GARCH_KINDS:
            try:
                nxt = float(kernels.garch_fit(y, k, None).next_variance) / 1e4
            except ValueError:
                continue
            if np.isfinite(nxt) and nxt > 0:
                rows.append({"symbol": sym, "asof": asof, "model": k, "var": nxt, "q05": np.nan, "made_at": made_at})
    return rows


def realise(ledger: pd.DataFrame, rets: dict[str, pd.Series]) -> pd.DataFrame:
    """Attach each row's outcome: the return of the first session after ``asof``. Unrealised rows are dropped."""
    out = []
    for sym, g in ledger.groupby("symbol"):
        r = rets.get(sym)
        if r is None:
            continue
        idx = r.index
        for asof, h in g.groupby("asof"):
            pos = idx.searchsorted(pd.Timestamp(asof), side="right")
            if pos >= len(idx):
                continue                                    # tomorrow has not happened yet
            target = idx[pos].date().isoformat()
            late = pd.to_datetime(h["made_at"], utc=True) >= target_close_utc(target)
            out.append(h.assign(target=target, r=float(r.iloc[pos]), late=late.to_numpy()))
    cols = [*LEDGER_COLUMNS, "target", "r", "late"]
    return pd.concat(out, ignore_index=True)[cols] if out else pd.DataFrame(columns=cols)


def score(real: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per-name mean QLIKE per model on days where EVERY model forecast (a fair comparison), and the pooled
    Kronos-vs-EWMA DM over the cross-name daily mean of the loss differential."""
    if not real.empty:
        real = real[~real["late"].astype(bool)]          # made after its session closed: never scored
    if real.empty:
        return pd.DataFrame(columns=["symbol", "model", "n", "qlike", "rank"]), {"days": 0}
    real = real.assign(qlike=V.qlike(real["r"] ** 2, real["var"]))
    wide = real.pivot_table(index=["symbol", "target"], columns="model", values="qlike")
    have = [m for m in MODELS if m in wide.columns]
    wide = wide[have].dropna()
    rows = []
    for sym, g in wide.groupby(level="symbol"):
        means = g.mean()
        ranks = means.rank()
        rows += [{"symbol": sym, "model": m, "n": len(g), "qlike": float(means[m]), "rank": int(ranks[m])}
                 for m in have]
    pooled: dict[str, Any] = {"days": 0}
    if {"kronos", "ewma"} <= set(have):
        d = (wide["kronos"] - wide["ewma"]).groupby(level="target").mean()
        pooled = {"days": int(len(d)), "mean_qlike_diff_vs_ewma": float(d.mean()) if len(d) else np.nan}
        if len(d) >= MIN_POOLED_DAYS:
            pooled.update(K.one_sided_dm(d.to_numpy(), np.zeros(len(d))))
    return pd.DataFrame(rows, columns=["symbol", "model", "n", "qlike", "rank"]), pooled


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    cfg = K.load_config(CONFIG)
    names = [str(t).upper() for t in params.get("tickers", cfg["names"])]
    made_at = now_utc().isoformat(timespec="seconds")
    notes: list[str] = []
    bars, rets, prov, skipped = {}, {}, [], []
    for t in names:
        ctx.check_cancelled()
        try:
            ds = bars_of(ctx, t)
        except DataUnavailable as e:
            skipped.append({"ticker": t, "reason": str(e)})
            continue
        b = adjusted_bars(ds.data.dropna())
        bars[t] = b
        rets[t] = b["close"].pct_change().dropna()
        prov += ds.provenance_dicts()[:1]
    new: list[dict[str, Any]] = []
    for t in bars:
        new += incumbent_rows(t, rets[t], made_at)
    py = kronos_python()
    if py is None:
        notes.append("Kronos runtime unavailable (no research/kronos venv): tonight's ledger has the incumbents only")
    else:
        try:
            kr = run_kronos(bars, py, threads=ctx.threads)
            new += [{"symbol": r.symbol, "asof": r.asof, "model": "kronos", "var": r.var_kronos, "q05": r.q05_kronos,
                     "made_at": made_at} for r in kr.itertuples()]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            err = getattr(e, "stderr", b"") or b""
            notes.append(f"Kronos subprocess failed ({type(e).__name__}): {err.decode(errors='replace')[-300:]}")
    prev = previous_artifact(KIND, db_path=DB_PATH, with_table="ledger")
    old = pd.read_parquet(prev[1] / "ledger.parquet") if prev else pd.DataFrame(columns=LEDGER_COLUMNS)
    ledger = (pd.concat([old, pd.DataFrame(new, columns=LEDGER_COLUMNS)], ignore_index=True)
              .drop_duplicates(["symbol", "asof", "model"], keep="first")          # a made forecast is never rewritten
              .sort_values(["asof", "symbol", "model"]).reset_index(drop=True))
    real = realise(ledger, rets)
    scores, pooled = score(real)
    if not bars:
        return ArtifactSpec(tables={"ledger": ledger, "skipped": pd.DataFrame(skipped)}, data_asof=None,
                            provenance=prov, notes=notes, verdict="INSUFFICIENT DATA",
                            verdict_detail="NO BARS FOR ANY NAME")
    kr_rank = scores[scores["model"] == "kronos"]["rank"].mean() if not scores.empty else np.nan
    days = pooled.get("days", 0)
    if days < MIN_POOLED_DAYS:
        detail = f"KRONOS SHADOW · FAILED EXP-K01 · ACCRUING {days} OF {MIN_POOLED_DAYS} PROSPECTIVE DAYS"
    else:
        detail = (f"KRONOS SHADOW · FAILED EXP-K01 · {days} PROSPECTIVE DAYS · MEAN QLIKE RANK {kr_rank:.1f} OF "
                  f"{scores['model'].nunique()} · POOLED DM VS EWMA P = {pooled['p_one_sided']:.3g}")
    tonight = ledger[ledger["made_at"] == made_at]
    return ArtifactSpec(
        tables={"ledger": ledger, "realised": real, "scores": scores, "tonight": tonight,
                "pooled": pd.DataFrame([{k: v for k, v in pooled.items()}]),
                "skipped": pd.DataFrame(skipped, columns=["ticker", "reason"])},
        data_asof=max(b.index[-1] for b in bars.values()).date().isoformat(), provenance=prov,
        notes=[*notes, "prospective: every row forecasts the session after its asof and is never rewritten",
               "Kronos-small, EXP-K01's frozen design (FAILED retrospectively); incumbents refitted nightly on the "
               "last 1000 sessions; QLIKE against the squared adjusted return; research only, sizes nothing",
               f"EXP-K01 config {json.dumps({'model_revision': cfg['kronos']['model_revision'], 'n_samples': cfg['sampling']['n_samples']})}"],
        verdict="DESCRIPTIVE ONLY", verdict_detail=detail)
