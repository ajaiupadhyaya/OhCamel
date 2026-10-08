"""P7 job: ``vol.surface_history`` (nightly, heavy, class M): each unprocessed (underlying, asof) snapshot
-> chain -> SVI surface -> one history row. The history is carried forward from the previous artifact and
only grows; the snapshots themselves cannot be re-fetched (Lane C C6)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..data.market import OptionChain
from ..jobs.artifacts import ArtifactSpec
from ..models.surface_metrics import day_metrics, rate_curve, vrp
from ..options.chain import analyze_chain
from ..options.surface import build_surface

KIND = "vol.surface_history"
DB_PATH: Path | None = None
ERR_COLS = ["underlying", "asof", "error"]
COLS = ["underlying", "asof", "atm_iv_30d", "atm_iv_90d", "term_slope", "rr25_30d", "bf25_30d", "mf_var_30d",
        "vrp", "vrp_note", "n_slices", "spot"]


def _pending(con: Any, done: set[tuple[str, str]]) -> list[tuple[str, str]]:
    rows = con.execute('SELECT DISTINCT underlying, CAST("asof" AS VARCHAR) FROM option_snapshots '
                       'ORDER BY 2, 1').fetchall()
    return [(u, a) for u, a in rows if (u, a) not in done]


def _chain(con: Any, und: str, asof: str) -> OptionChain:
    q = con.execute('SELECT expiry, strike, cp, bid, ask, last, volume, open_interest FROM option_snapshots '
                    'WHERE underlying = ? AND "asof" = ?', [und, asof]).df()
    log = con.execute("SELECT detail FROM ingest_log WHERE dataset = 'option_snapshots' AND key = ? "
                      "AND data_asof = ? AND status = 'ok' ORDER BY ran_at DESC LIMIT 1", [und, asof]).fetchone()
    if log is None:
        raise ValueError(f"{und} {asof}: no ingest_log row with the snapshot's spot")
    d = json.loads(log[0])
    quotes = q.rename(columns={"cp": "type"}).assign(expiry=lambda x: pd.to_datetime(x["expiry"]), root=und)
    return OptionChain(underlying=und, spot=float(d["spot"]), as_of=pd.Timestamp(d["as_of"]).tz_localize(None),
                       quotes=quotes)


def _har(prev_league: tuple[dict, Path] | None) -> pd.DataFrame | None:
    from .io import read_table

    return read_table(prev_league[1], "forecasts") if prev_league else None


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from .io import previous_artifact, read_table

    prev = previous_artifact(KIND, db_path=DB_PATH)
    hist = read_table(prev[1], "history") if prev else None
    hist = hist if hist is not None else pd.DataFrame(columns=COLS)
    done = set(zip(hist["underlying"], hist["asof"], strict=True))
    try:
        todo = _pending(ctx.warehouse, done)
    except DataUnavailable as e:
        todo = []
        notes = [f"warehouse unavailable: {e}"]
    else:
        notes = []
    har = _har(previous_artifact("vol.forecast_league", db_path=DB_PATH))
    rows, errors = [], []
    for i, (und, asof) in enumerate(todo):
        ctx.check_cancelled()
        try:
            chain = _chain(ctx.warehouse, und, asof)
            try:
                tc = ctx.market.treasury_curve(pd.Timestamp(asof).date() - timedelta(days=30), pd.Timestamp(asof).date())
                curve = rate_curve(tc.data.dropna(how="all").iloc[-1])
            except (DataUnavailable, ValueError, IndexError):
                curve = None
            surf = build_surface(analyze_chain(chain, rate_curve=curve), use_kernels=True)
            m = day_metrics(surf.term.reset_index())
        except (DataUnavailable, ValueError, KeyError, np.linalg.LinAlgError) as e:  # one bad snapshot never sinks the night
            errors.append({"underlying": und, "asof": asof, "error": str(e)})
            continue
        h = None if har is None else har[(har["ticker"] == und) & (har["asof"] <= asof)]
        if h is None or h.empty or "har_22d" not in h or pd.isna(h["har_22d"].iloc[-1]):
            v, why = float("nan"), "no HAR forecast dated on or before the snapshot (minute bars cover ~50 names)"
        else:
            v, why = vrp(m["mf_var_30d"], float(h["har_22d"].iloc[-1])), None
        rows.append({"underlying": und, "asof": asof, **m, "vrp": v, "vrp_note": why, "spot": chain.spot})
        ctx.progress((i + 1) / max(len(todo), 1), f"{und} {asof}")
    if rows:
        new = pd.DataFrame(rows, columns=COLS)
        hist = new if hist.empty else pd.concat([hist, new], ignore_index=True)
    days = hist["asof"].nunique()
    if hist.empty:
        return ArtifactSpec(tables={"history": hist, "errors": pd.DataFrame(errors, columns=ERR_COLS)}, data_asof=None,
                            provenance=[], notes=notes, verdict="INSUFFICIENT DATA",
                            verdict_detail="NO OPTION SNAPSHOTS")
    summary = (hist.groupby("underlying").agg(days=("asof", "nunique"), first=("asof", "min"), last=("asof", "max"))
               .reset_index())
    return ArtifactSpec(
        tables={"history": hist, "summary": summary, "errors": pd.DataFrame(errors, columns=ERR_COLS)},
        data_asof=str(hist["asof"].max()), provenance=[{"source": "warehouse:option_snapshots (Cboe delayed)"}],
        notes=[*notes, "SVI per expiry; ATM in total variance, RR/BF linear in T, no extrapolation; "
                       "model-free variance by the Cboe method"],
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"{days} DAY{'S' if days != 1 else ''} · {hist['underlying'].nunique()} UNDERLYINGS · MEASUREMENT")
