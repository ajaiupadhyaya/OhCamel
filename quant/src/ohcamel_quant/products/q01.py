"""P5 job: ``models.xs_lgbm`` (monthly, heavy, class M) and the universe freeze CLI.

Flow, each run: load the frozen config -> refuse (INSUFFICIENT DATA, no
numbers) while the universe is unfrozen -> load the panel from the warehouse
-> reuse the stored selection when it was made under this config and
methodology version, else select -> the holdout: evaluate once, carry
forward, or mark stale (``holdout_decision``) -> retrain on the latest 60
months and score the new month. Everything is advisory; nothing reaches the desk.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import Provenance
from ..jobs.artifacts import ArtifactSpec
from ..models import xsec as X
from ..models.features import Panel
from ..models.verdict import ADVISORY_NOTE
from .experiment import config_hash, holdout_decision, load_config

KIND = "models.xs_lgbm"
PANEL_START = "2006-12-01"   # 252 sessions before the first 2008 decision
DB_PATH: Path | None = None
HOLDOUT_TABLES = ("holdout", "holdout_returns", "holdout_costs", "holdout_regimes", "holdout_ic", "holdout_deciles",
                  "gates")


def load_panel(con: Any, tickers: list[str], start: str = PANEL_START) -> tuple[Panel, list[dict[str, Any]]]:
    df = con.execute("SELECT ticker, date, open, close, adj_close, volume, source FROM bars_daily "
                     "WHERE list_contains(?, ticker) AND date >= ? ORDER BY date", [tickers, start]).df()
    df["date"] = pd.to_datetime(df["date"])
    piv = {c: df.pivot(index="date", columns="ticker", values=c).reindex(columns=tickers)
           for c in ("open", "close", "adj_close", "volume")}
    sources = df.groupby("ticker")["source"].agg(lambda s: sorted(set(s))).to_dict()
    return Panel(**piv), [{"source": "warehouse:bars_daily", "tickers": tickers, "vendors": sources}]


def _insufficient(detail: str, notes: list[str] | None = None) -> ArtifactSpec:
    gates = pd.DataFrame(columns=["gate", "value", "rule", "passed", "note"])
    return ArtifactSpec(tables={"gates": gates}, data_asof=None, provenance=[], notes=notes or [],
                        verdict="INSUFFICIENT DATA", verdict_detail=detail)


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from .io import previous_artifact, read_table

    raw = load_config("EXP-Q01")
    if not raw.get("universe"):
        return _insufficient("universe not frozen: run `python -m ohcamel_quant.products.q01 freeze` after the "
                             "warehouse backfill and commit config.yaml")
    cfg, h, version = X.Q01Config.from_dict(raw), config_hash(raw), int(raw["methodology_version"])
    tickers = sorted(set(cfg.universe) | {cfg.benchmark})
    panel, prov = load_panel(ctx.warehouse, tickers)
    samples = X.build_samples(panel, cfg)
    # Each stored group comes from the newest artifact that holds it, so an INSUFFICIENT DATA artifact (which
    # publishes only an empty gates table) never reopens the once-only holdout or the frozen selection.
    prev_t: dict[str, pd.DataFrame | None] = {}
    for anchor, group in (("holdout", HOLDOUT_TABLES), ("selection", ("selection", "trials", "stitched")),
                          ("model", ("model", "scores"))):
        got = previous_artifact(KIND, db_path=DB_PATH, with_table=anchor)
        prev_t.update({t: read_table(got[1], t) for t in group} if got else {})

    sel_row = prev_t.get("selection")
    if sel_row is not None and not sel_row.empty and sel_row.iloc[0]["config_hash"] == h \
            and int(sel_row.iloc[0]["methodology_version"]) == version:
        st = prev_t["stitched"].set_index("date")
        sel = X.Selection(prev_t["trials"], st, int(sel_row.iloc[0]["selected"]), float(sel_row.iloc[0]["pbo"]))
    else:
        sel = X.run_selection(samples, panel, cfg, ctx.threads, ctx.progress)
        sel_row = pd.DataFrame([{"config_hash": h, "methodology_version": version, "selected": sel.selected,
                                 "params": str(cfg.grid[sel.selected]), "pbo": sel.pbo,
                                 "selected_at": datetime.now(UTC).isoformat()}])

    tables: dict[str, pd.DataFrame] = {"selection": sel_row, "trials": sel.trials,
                                       "stitched": sel.stitched.rename_axis("date").reset_index()}
    decision = holdout_decision(prev_t.get("holdout"), version=version, cfg_hash=h,
                                approved=raw.get("holdout_reevaluation_approved"))
    notes = ["regime gate measured on the selected configuration's full walk-forward OOS record (I-Q01-6); "
             "the selection months carry a mild selection bias, which DSR's trial count accounts for"]
    if decision == "carry":
        tables.update({t: prev_t[t] for t in HOLDOUT_TABLES})
        hrow = prev_t["holdout"].iloc[0]
        verdict, detail = str(hrow["verdict"]), str(hrow["verdict_detail"])
        notes.append(f"holdout evaluated once on {hrow['evaluated_at']}; carried forward unchanged")
    elif decision == "stale":
        tables.update({t: prev_t[t] for t in HOLDOUT_TABLES})
        verdict = "INSUFFICIENT DATA"
        detail = (f"holdout evidence predates methodology v{version} / config {h}; re-evaluating the holdout is "
                  "the owner's decision (holdout_reevaluation_approved)")
    else:
        ho = X.run_holdout(samples, panel, cfg, sel, ctx.threads)
        tables.update({"holdout": pd.DataFrame([{**ho.row, "methodology_version": version, "config_hash": h,
                                                 "evaluated_at": datetime.now(UTC).isoformat()}]),
                       "holdout_returns": ho.returns, "holdout_costs": ho.costs, "holdout_regimes": ho.regimes,
                       "holdout_ic": ho.ic, "holdout_deciles": ho.deciles, "gates": ho.verdict.table()})
        verdict, detail = ho.verdict.value, ho.verdict.detail

    month = str(samples.index.get_level_values("date").max().date())
    mrow = prev_t.get("model")
    if mrow is not None and not mrow.empty and mrow.iloc[0]["month"] == month and mrow.iloc[0]["config_hash"] == h:
        tables["model"] = mrow
        tables["scores"] = prev_t.get("scores") if prev_t.get("scores") is not None else pd.DataFrame()
    else:
        scores, booster = X.score_month(samples, cfg, cfg.grid[sel.selected], ctx.threads)
        tables["scores"] = scores
        tables["model"] = pd.DataFrame([{"month": month, "config_hash": h, "booster": booster}])
    if verdict == "PASS" and ADVISORY_NOTE not in detail:
        detail = f"{detail}; {ADVISORY_NOTE}"
    return ArtifactSpec(tables=tables, data_asof=month,
                        provenance=[*prov, Provenance.now("ohcamel-exp-q01", config_hash=h).to_dict()],
                        notes=notes, verdict=verdict, verdict_detail=detail)


def coverage_from_warehouse(con: Any, tickers: list[str]) -> tuple[dict[str, dict[str, Any]], pd.DatetimeIndex]:
    """Per ticker, every bar's date and the source that wrote it (I-Q01-9 judges adjustment by source; the
    adj_close values say nothing for a fund that pays no distributions)."""
    df = con.execute("SELECT ticker, date, source FROM bars_daily WHERE list_contains(?, ticker) ORDER BY date",
                     [[*tickers, "SPY"]]).df()
    df["date"] = pd.to_datetime(df["date"])
    spy = pd.DatetimeIndex(df.loc[df["ticker"] == "SPY", "date"])
    cov = {t: {"dates": pd.DatetimeIndex(g["date"]), "source": g["source"].fillna("unknown").to_numpy(dtype=object)}
           for t, g in df.groupby("ticker")}
    empty = {"dates": pd.DatetimeIndex([]), "source": np.array([], dtype=object)}
    return {t: cov.get(t, empty) for t in tickers}, spy


def main(argv: list[str] | None = None) -> int:
    import duckdb

    from ..data.market import load_universes

    ap = argparse.ArgumentParser(prog="python -m ohcamel_quant.products.q01")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="print EXP-Q01's frozen universe (I-Q01-9) as YAML for config.yaml")
    f.add_argument("--warehouse", required=True, type=Path)
    a = ap.parse_args(argv)
    u = load_universes()["universes"]
    etfs = sorted({m["ticker"] for v in u.values() for m in v["members"]})
    with duckdb.connect(str(a.warehouse), read_only=True) as con:
        cov, spy = coverage_from_warehouse(con, etfs)
    today = pd.Timestamp(datetime.now(UTC).date())
    members, excluded = X.freeze_universe(cov, spy, pd.Timestamp("2008-01-02"), today,
                                          adjusted_from=pd.Timestamp(PANEL_START))
    print(f"universe: [{', '.join(members)}]")
    print(f'universe_frozen_on: "{today.date()}"')
    for t, why in excluded.items():
        print(f"# excluded {t}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
