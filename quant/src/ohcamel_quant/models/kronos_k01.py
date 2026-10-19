"""EXP-K01: Kronos's sampled one-day variance, scored against the volatility league's incumbents.

The forecasts are made elsewhere (``research/kronos``, which needs torch) and committed as
``research/experiments/EXP-K01/forecasts.csv`` with a sidecar. This module reads that file and the committed
fixtures, runs the incumbents through P2's own functions on the same days (``vol_league.garch_oos``,
``ewma_oos``) and scores everything with P2's own QLIKE, Diebold-Mariano and MCS. So Kronos is judged by
exactly the rules the incumbents are judged by. It never imports torch, and it never sizes anything.

The pass rule is the pre-registration's, applied literally (``verdict``). Its config is DRAFT (``approved:
null``) until the owner confirms it, and every verdict says so.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy import stats

from ..factors.hac import nw_lag_rule, ols_hac_cov
from ..risk.backtest import christoffersen, kupiec_pof
from . import vol_league as V

KRONOS = "kronos"
INCUMBENTS = ("garch", "gjr", "egarch", "ewma")
PASS = "PASS"
FAIL = "DOES NOT BEAT THE INCUMBENTS"
INSUFFICIENT = "INSUFFICIENT DATA"


class Refused(ValueError):
    """An input the experiment will not score: a partial run, a different config, misaligned dates."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    if cfg.get("experiment") != "EXP-K01":
        raise Refused(f"{path} is not EXP-K01's config")
    return cfg


def load_forecasts(csv: Path, config_path: Path) -> pd.DataFrame:
    """The committed forecasts, refused unless the sidecar says the run was complete and made from THIS config."""
    meta_path = Path(f"{csv}.meta.json")
    if not meta_path.exists():
        raise Refused(f"{csv} has no sidecar")
    meta = json.loads(meta_path.read_text())
    if not meta.get("complete"):
        raise Refused(f"{csv} is a partial (engineering) run")
    if meta.get("config_sha256") != hashlib.sha256(config_path.read_bytes()).hexdigest():
        raise Refused(f"{csv} was made from a different config than {config_path}")
    df = pd.read_csv(csv, parse_dates=["date"])
    if df.duplicated(["symbol", "date"]).any():
        raise Refused("duplicate name-days in the forecasts")
    if not (np.isfinite(df["var_kronos"]) & (df["var_kronos"] > 0)).all():
        raise Refused("a Kronos variance is non-finite or not positive")
    return df


def fixture_returns(fixtures: Path, symbol: str) -> pd.Series:
    """Simple returns of the adjusted close, as ``MarketData.returns`` computes them for the league."""
    meta = json.loads((fixtures / f"{symbol}.parquet.meta.json").read_text())
    if meta.get("synthetic", True):
        raise Refused(f"{symbol}: fixture is synthetic or does not say it is not")
    px = pd.read_parquet(fixtures / f"{symbol}.parquet")["close_adj"].sort_index()
    px.index = pd.DatetimeIndex(px.index)
    return px.pct_change().dropna()


def one_sided_dm(la: np.ndarray, lb: np.ndarray) -> dict[str, float]:
    """``dm_test``'s statistic with a one-sided p-value for H1: a's loss is lower (negative stat)."""
    d = V.dm_test(la, lb)
    return {"stat": d["stat"], "p_one_sided": float(stats.norm.cdf(d["stat"]))}


def mincer_zarnowitz(y: np.ndarray, f: np.ndarray) -> dict[str, float]:
    """OLS y = a + b f with Newey-West errors. An unbiased, efficient forecast has a = 0 and b = 1."""
    X = np.column_stack([np.ones_like(f), f])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    se = np.sqrt(np.diag(ols_hac_cov(X, e, nw_lag_rule(y.size))))
    return {"a": float(beta[0]), "b": float(beta[1]), "t_b_eq_1": float((beta[1] - 1.0) / se[1]),
            "r2": float(1 - e.var() / y.var())}


@dataclass
class NameScore:
    symbol: str
    n: int
    rows: list[dict] = field(default_factory=list)        # one per model: qlike, mse, in_mcs, rank
    dm: list[dict] = field(default_factory=list)          # kronos vs each incumbent
    calibration: dict = field(default_factory=dict)
    var95: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)
    losses: pd.DataFrame | None = None                    # dated QLIKE per model (for the pooled test)
    in_mcs: bool | None = None


def incumbent_forecasts(r: np.ndarray, n: int, cfg: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    inc = cfg["incumbents"]
    fc: dict[str, np.ndarray] = {}
    dropped: dict[str, str] = {}
    for k in V.GARCH_KINDS:
        f, _ = V.garch_oos(r, k, est_window=inc["est_window"], eval_days=n, refit_every=inc["refit_every"])
        if np.all(np.isfinite(f) & (f > 0)):
            fc[k] = f
        else:
            dropped[k] = "non-finite variance forecast from a degenerate fit; not scored"
    fc["ewma"], _ = V.ewma_oos(r, n, inc["ewma_lambda"])
    return fc, dropped


def score_name(symbol: str, ret: pd.Series, fk: pd.DataFrame, cfg: dict[str, Any]) -> NameScore:
    """``ret`` the name's full return history; ``fk`` its Kronos rows, which must be its LAST ``n`` sessions."""
    fk = fk.sort_values("date")
    n = len(fk)
    tail = ret.iloc[-n:]
    if not tail.index.equals(pd.DatetimeIndex(fk["date"])):
        raise Refused(f"{symbol}: forecast dates are not the last {n} sessions of the fixture")
    r = ret.to_numpy(float)
    y = tail.to_numpy(float)
    rv = y * y
    fc, dropped = incumbent_forecasts(r, n, cfg)
    fc = {KRONOS: fk["var_kronos"].to_numpy(float), **fc}
    out = NameScore(symbol, n, dropped=dropped)
    losses = pd.DataFrame({m: V.qlike(rv, f) for m, f in fc.items()}, index=tail.index)
    try:
        keep: set[str] | None = V.mcs_members(losses, size=cfg["pass"]["mcs_size"])
    except (IndexError, ValueError) as e:
        keep = None
        out.dropped["mcs"] = f"model confidence set not computable: {e}"
    ranks = losses.mean().rank()
    for m, f in fc.items():
        out.rows.append({"symbol": symbol, "model": m, "qlike": float(losses[m].mean()),
                         "mse": float(V.mse(rv, f).mean()), "rank_qlike": int(ranks[m]),
                         "in_mcs": None if keep is None else m in keep, "n": n})
    for m in fc:
        if m != KRONOS:
            out.dm.append({"symbol": symbol, "vs": m,
                           **one_sided_dm(losses[KRONOS].to_numpy(), losses[m].to_numpy())})
    f = fc[KRONOS]
    out.calibration = {"symbol": symbol, "mean_forecast_over_mean_r2": float(f.mean() / rv.mean()),
                       "ewma_mean_over_mean_r2": float(fc["ewma"].mean() / rv.mean()),
                       **{f"mz_{k}": v for k, v in mincer_zarnowitz(rv, f).items()},
                       "distinct_frac_median": float(fk["distinct_frac"].median()),
                       "rec_close_err_median_bp": float(
                           (1e4 * (fk["prev_close_rec"] / fk["prev_close_actual"] - 1)).abs().median())}
    hits = (y < fk["q05_kronos"].to_numpy(float)).astype(int)
    p = 1.0 - cfg["var"]["level"]
    pof, cc = kupiec_pof(int(hits.sum()), n, p), christoffersen(hits, p)
    out.var95 = {"symbol": symbol, "breaches": int(hits.sum()), "expected": n * p, "kupiec_p": pof["p_value"],
                 "christoffersen_ind_p": cc["p_value_ind"], "christoffersen_cc_p": cc["p_value_cc"]}
    out.losses = losses.assign(kronos_actual_anchor=V.qlike(rv, fk["var_kronos_actual_anchor"].to_numpy(float)))
    out.in_mcs = None if keep is None else KRONOS in keep
    return out


def pooled(scores: list[NameScore], column: str = KRONOS) -> dict[str, float]:
    """The pre-registered pooled test: per day, the cross-name mean of (``column`` QLIKE - EWMA QLIKE), then a
    one-sided DM on that daily series. Days are the intersection of every name's evaluation days."""
    d = pd.concat([s.losses[column] - s.losses["ewma"] for s in scores], axis=1, join="inner").mean(axis=1)
    res = one_sided_dm(d.to_numpy(), np.zeros(len(d)))
    return {"days": int(len(d)), "mean_diff": float(d.mean()), **res}


@dataclass(frozen=True)
class Verdict:
    label: str
    detail: str


def verdict(*, pooled_p: float, mcs_names: int, n_names: int, cfg: dict[str, Any],
            short: list[str] | None = None) -> Verdict:
    """The pre-registration's rule, literally. DRAFT is said on every verdict until the owner approves."""
    draft = "" if cfg.get("approved") else " · DRAFT PRE-REGISTRATION (OWNER UNCONFIRMED)"
    if short:
        return Verdict(INSUFFICIENT, f"SHORT: {', '.join(short)}{draft}")
    p, need = cfg["pass"], cfg["pass"]["mcs_min_names"]
    ok_pool = math.isfinite(pooled_p) and pooled_p < p["pooled_dm_p_max"]
    ok_mcs = mcs_names >= need
    detail = (f"POOLED DM vs EWMA p = {pooled_p:.3g} (need < {p['pooled_dm_p_max']}) · IN 10% MCS ON {mcs_names} "
              f"OF {n_names} (need {need}){draft}")
    return Verdict(PASS if ok_pool and ok_mcs else FAIL, detail)


@dataclass
class Result:
    verdict: Verdict
    pooled: dict
    pooled_actual_anchor: dict
    league: pd.DataFrame
    dm: pd.DataFrame
    calibration: pd.DataFrame
    var95: pd.DataFrame
    dropped: pd.DataFrame
    window: tuple[str, str]


def run(config_path: Path | None = None, forecasts: Path | None = None) -> Result:
    root = repo_root()
    config_path = config_path or root / "research/experiments/EXP-K01/config.yaml"
    cfg = load_config(config_path)
    fk = load_forecasts(forecasts or config_path.parent / "forecasts.csv", config_path)
    fixtures = root / cfg["fixtures"]
    scores, short = [], []
    for sym in cfg["names"]:
        rows = fk[fk["symbol"] == sym]
        if len(rows) < cfg["sufficiency"]["min_eval_sessions"]:
            short.append(f"{sym} ({len(rows)} sessions)")
            continue
        scores.append(score_name(sym, fixture_returns(fixtures, sym), rows, cfg))
    if short or not scores:
        v = verdict(pooled_p=math.nan, mcs_names=0, n_names=len(cfg["names"]), cfg=cfg, short=short or ["all"])
        empty = pd.DataFrame()
        return Result(v, {}, {}, empty, empty, empty, empty, empty, ("", ""))
    pl, pa = pooled(scores), pooled(scores, "kronos_actual_anchor")
    v = verdict(pooled_p=pl["p_one_sided"], mcs_names=sum(bool(s.in_mcs) for s in scores),
                n_names=len(scores), cfg=cfg)
    dates = pd.DatetimeIndex(fk["date"])
    return Result(v, pl, pa,
                  pd.DataFrame([r for s in scores for r in s.rows]),
                  pd.DataFrame([r for s in scores for r in s.dm]),
                  pd.DataFrame([s.calibration for s in scores]),
                  pd.DataFrame([s.var95 for s in scores]),
                  pd.DataFrame([{"symbol": s.symbol, "model": m, "reason": why}
                                for s in scores for m, why in s.dropped.items()],
                               columns=["symbol", "model", "reason"]),
                  (str(dates.min().date()), str(dates.max().date())))


def _md(df: pd.DataFrame, floatfmt: str = ".4g") -> str:
    if df.empty:
        return "_none_"
    cols = list(df.columns)
    def cell(v: Any) -> str:
        if isinstance(v, float):
            return format(v, floatfmt)
        return "" if v is None else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def report(res: Result) -> str:
    """The experiment's report: verdict and gates first, then everything reported-not-gated."""
    v = res.verdict
    out = ["# EXP-K01 report: Kronos in the volatility league", "",
           f"**Verdict: {v.label}** — {v.detail}", "",
           "Research only. Kronos sizes nothing, is not a graph node and reaches no desk. The pre-registration "
           "is a DRAFT the owner has not confirmed, so this is a draft-pre-registration result.", ""]
    if v.label == INSUFFICIENT:
        return "\n".join(out) + "\n"
    lg = res.league
    out += [f"Evaluation window {res.window[0]} → {res.window[1]} (sessions after the weights' 2025-06-30 commit), "
            f"{lg['symbol'].nunique()} names, target the squared simple return of the adjusted close.", "",
            "## Gates", "",
            _md(pd.DataFrame([
                {"gate": "pooled DM vs EWMA (one-sided, Kronos lower)", "value": res.pooled["p_one_sided"],
                 "stat": res.pooled["stat"], "mean QLIKE diff": res.pooled["mean_diff"], "days": res.pooled["days"]},
                {"gate": "names with Kronos in the 10% MCS",
                 "value": float(lg[(lg["model"] == KRONOS)]["in_mcs"].astype(float).sum()),
                 "stat": None, "mean QLIKE diff": None, "days": None}])), "",
            "## League (mean QLIKE per name; lower is better)", "",
            _md(lg.pivot(index="symbol", columns="model", values="qlike").reset_index()), "",
            "Rank of Kronos by QLIKE (1 = best of five) and MCS membership:", "",
            _md(lg[lg["model"] == KRONOS][["symbol", "rank_qlike", "in_mcs", "qlike", "mse"]]), "",
            "Mean rank by model across names:", "",
            _md(lg.groupby("model")["rank_qlike"].mean().sort_values().reset_index()), "",
            "## Reported, not gated", "",
            "### Diebold–Mariano, Kronos vs each incumbent (negative stat: Kronos lower loss)", "",
            _md(res.dm.pivot(index="symbol", columns="vs", values="stat").reset_index()), "",
            "### Calibration", "",
            "`mean_forecast_over_mean_r2` above 1 means Kronos forecasts more variance than was realised. "
            "Mincer–Zarnowitz `r² = a + b F`: an unbiased forecast has b = 1 (`mz_t_b_eq_1` is the HAC t of b − 1). "
            "`distinct_frac_median` is the median share of distinct closes among the 128 draws (quantization). "
            "`rec_close_err_median_bp` is the tokenizer's median error reconstructing the last close.", "",
            _md(res.calibration), "",
            "### 95% VaR from the 5th percentile of the draws", "",
            _md(res.var95), "",
            "### Diagnostic, not eligible: anchored on the actual last close", "",
            f"Pooled DM vs EWMA: stat {res.pooled_actual_anchor['stat']:.3g}, one-sided p "
            f"{res.pooled_actual_anchor['p_one_sided']:.3g}, mean QLIKE diff "
            f"{res.pooled_actual_anchor['mean_diff']:.3g}. The gap between this and the gated row measures how much "
            "of Kronos's variance is the tokenizer's level error rather than a forecast.", ""]
    if not res.dropped.empty:
        out += ["### Dropped", "", _md(res.dropped), ""]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m ohcamel_quant.models.kronos_k01", description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="write report.md and results.json beside the config")
    a = ap.parse_args(argv)
    res = run()
    text = report(res)
    print(text)
    if a.write:
        d = repo_root() / "research/experiments/EXP-K01"
        (d / "report.md").write_text(text)
        (d / "results.json").write_text(json.dumps({
            "verdict": res.verdict.label, "detail": res.verdict.detail, "window": res.window,
            "pooled": res.pooled, "pooled_actual_anchor_diagnostic": res.pooled_actual_anchor,
            "league": res.league.to_dict("records"), "dm": res.dm.to_dict("records"),
            "calibration": res.calibration.to_dict("records"), "var95": res.var95.to_dict("records"),
            "dropped": res.dropped.to_dict("records")}, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
