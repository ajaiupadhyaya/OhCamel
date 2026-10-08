"""P6 job: ``regime.hmm`` (weekly, heavy, class M). Probabilities refresh every run; the holdout score is
computed once and carried forward (``products.experiment.holdout_decision``)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..data.base import DataUnavailable, Provenance
from ..jobs.artifacts import ArtifactSpec
from ..models import q02 as Q
from ..models.features import assert_point_in_time
from .experiment import config_hash, holdout_decision, load_config

KIND = "regime.hmm"
DB_PATH: Path | None = None
HOLDOUT_TABLES = ("holdout", "holdout_forecasts", "regression", "gates")


def load_inputs(ctx: Any, cfg: dict[str, Any]) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, list[dict]]:
    ids = cfg["fred"]
    spy_ds = ctx.market.ohlcv("SPY", start=date(1995, 1, 1))
    prov = list(spy_ds.provenance_dicts())
    try:
        df = ctx.warehouse.execute("SELECT series, date, value FROM fred WHERE list_contains(?, series)",
                                   [list(ids.values())]).df()
        wide = df.pivot(index="date", columns="series", values="value")
        wide.index = pd.to_datetime(wide.index)
        prov.append({"source": "warehouse:fred", "series": list(ids.values())})
    except DataUnavailable:
        ds = ctx.market.fred(list(ids.values()))   # raises DataUnavailable when a series is unavailable
        wide, prov = ds.data, prov + ds.provenance_dicts()
    missing = [s for s in ids.values() if s not in wide.columns or wide[s].dropna().empty]
    if missing:
        raise DataUnavailable(f"FRED series unavailable: {', '.join(missing)}")
    return (spy_ds.data["adj_close"], wide[ids["dgs10"]], wide[ids["dgs2"]], wide[ids["hy_oas"]], prov)


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from .io import previous_artifact, read_table

    cfg = load_config("EXP-Q02")
    version, h = int(cfg["methodology_version"]), config_hash(cfg)
    try:
        spy, d10, d2, oas, prov = load_inputs(ctx, cfg)
    except DataUnavailable as e:
        return ArtifactSpec(tables={"gates": pd.DataFrame(columns=["gate", "value", "rule", "passed", "note"])},
                            data_asof=None, provenance=[], notes=[], verdict="INSUFFICIENT DATA",
                            verdict_detail=f"{e} (BAMLH0A0HYM2 is the HY OAS input)")
    feats = Q.weekly_features(spy, d10, d2, oas)
    assert_point_in_time(feats)
    z = Q.standardize_expanding(feats, cfg["standardize"]["min_weeks"]).dropna()
    refits = Q.refit_dates(feats.index, pd.Timestamp(cfg["refit"]["first"]), cfg["refit"]["every_weeks"])
    m = cfg["model"]
    need = refits[0] if len(refits) else feats.index[-1]
    if len(z) == 0 or int(z.index.searchsorted(need, side="right")) < m["min_train_weeks"]:
        short = {c: str(s.dropna().index.min().date()) for c, s in
                 (("SPY", spy), ("DGS10", d10), ("DGS2", d2), ("BAMLH0A0HYM2", oas))}
        return ArtifactSpec(tables={"gates": pd.DataFrame(columns=["gate", "value", "rule", "passed", "note"])},
                            data_asof=None, provenance=prov, notes=[f"first dates: {short}"],
                            verdict="INSUFFICIENT DATA",
                            verdict_detail=f"the first refit ({need.date()}) needs {m['min_train_weeks']} weeks of "
                                           f"standardized features; series start: {short}")
    p_high, probs, fits = Q.oos_probabilities(z, refits, ks=tuple(m["ks"]), restarts=m["restarts"], seed=m["seed"],
                                              min_train=m["min_train_weeks"])
    frame = pd.DataFrame({"rv": Q.target_rv(spy, z.index, cfg["target"]["sessions"]),
                          "rv_end": Q.target_end(spy, z.index, cfg["target"]["sessions"]),
                          "ewma": Q.ewma_month(spy, z.index, cfg["target"]["ewma_lambda"], cfg["target"]["sessions"]),
                          "p_high": p_high})
    prev = previous_artifact(KIND, db_path=DB_PATH, with_table="holdout")  # skips INSUFFICIENT DATA runs
    prev_t = {t: read_table(prev[1], t) for t in HOLDOUT_TABLES} if prev else {}
    decision = holdout_decision(prev_t.get("holdout"), version=version, cfg_hash=h,
                                approved=cfg.get("holdout_reevaluation_approved"))
    tables = {"probs": probs.rename_axis("date").reset_index(), "fits": fits}
    if decision == "evaluate":
        row, reg, fc, v = Q.score(frame, selection_end=pd.Timestamp(cfg["windows"]["selection_end"]),
                                  holdout_start=pd.Timestamp(cfg["windows"]["holdout_start"]),
                                  min_selection=cfg["sufficiency"]["min_selection_weeks"],
                                  min_holdout=cfg["sufficiency"]["min_holdout_weeks"])
        if v.value != "INSUFFICIENT DATA":
            row = {**row, "verdict": v.value, "verdict_detail": v.detail, "methodology_version": version,
                   "config_hash": h, "evaluated_at": datetime.now(UTC).isoformat()}
        tables.update({"holdout": pd.DataFrame([row]) if row else pd.DataFrame(), "holdout_forecasts": fc,
                       "regression": reg, "gates": v.table()})
        verdict, detail = v.value, v.detail
    else:
        tables.update({t: prev_t[t] for t in HOLDOUT_TABLES})
        hrow = prev_t["holdout"].iloc[0]
        if decision == "carry":
            verdict, detail = str(hrow["verdict"]), str(hrow["verdict_detail"])
        else:
            verdict = "INSUFFICIENT DATA"
            detail = (f"holdout evidence predates methodology v{version} / config {h}; re-evaluation is the owner's "
                      "decision (holdout_reevaluation_approved)")
    return ArtifactSpec(tables=tables, data_asof=str(z.index[-1].date()),
                        provenance=[*prov, Provenance.now("ohcamel-exp-q02", config_hash=h).to_dict()],
                        notes=["filtered probabilities are scored; p_high_smoothed_history uses later data and is "
                               "display-only history"], verdict=verdict, verdict_detail=detail)
