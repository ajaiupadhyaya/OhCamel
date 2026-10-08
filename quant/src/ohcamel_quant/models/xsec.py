"""EXP-Q01 (research/experiments/EXP-Q01/preregistration.md, APPROVED 2026-10-06), as written.

Pure: frames in, frames out. The interpretation points I-Q01-1..9 (plan
docs/superpowers/plans/2026-10-06-lane-m.md; config.yaml ``interpretations``)
fix every detail the pre-registration left open. The holdout is touched only by
``run_holdout``, which the job calls once.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import metrics as M
from ..backtest.validation import bootstrap_sharpe, cscv_pbo
from ..factors.hac import newey_west_lrv, nw_lag_rule
from .cv import owned_months, purge, walk_forward
from .features import FEATURES, Panel, assert_point_in_time, build_features, month_ends
from .verdict import COST_GRID_BPS, Gate, Verdict, charter_verdict, regime_table

CAPACITY_ADV_SHARE = 0.01


@dataclass(frozen=True)
class Q01Config:
    universe: tuple[str, ...]
    benchmark: str
    features: tuple[str, ...]
    train_months: int
    test_months: int
    step_months: int
    embargo: int
    grid: tuple[dict[str, Any], ...]
    rounds: int
    early_stopping: int
    validation_months: int
    seed: int
    quantile: float
    cost_bps: float
    selection_start: pd.Timestamp
    selection_end: pd.Timestamp
    holdout_start: pd.Timestamp
    signs: dict[str, int]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Q01Config:
        g = d["model"]["grid"]
        keys = ("num_leaves", "min_data_in_leaf", "learning_rate")
        grid = tuple(dict(zip(keys, combo, strict=True)) for combo in itertools.product(*(g[k] for k in keys)))
        if tuple(d["features"]) != FEATURES:
            raise ValueError("EXP-Q01's features are fixed by the pre-registration")
        return cls(universe=tuple(d["universe"] or ()), benchmark=d["benchmark"], features=tuple(d["features"]),
                   train_months=d["cv"]["train_months"], test_months=d["cv"]["test_months"],
                   step_months=d["cv"]["step_months"], embargo=d["cv"]["embargo_sessions"], grid=grid,
                   rounds=d["model"]["rounds"], early_stopping=d["model"]["early_stopping_rounds"],
                   validation_months=d["model"]["validation_months"], seed=d["model"]["seed"],
                   quantile=d["portfolio"]["quantile"], cost_bps=d["portfolio"]["cost_bps_base"],
                   selection_start=pd.Timestamp(d["windows"]["selection"][0]),
                   selection_end=pd.Timestamp(d["windows"]["selection"][1]),
                   holdout_start=pd.Timestamp(d["windows"]["holdout_start"]), signs=dict(d["composite_signs"]))


# ----------------------------------------------------------------- samples
def label_returns(panel: Panel, decisions: pd.DatetimeIndex) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """I-Q01-1. Entry = the session after decision m; exit = the session after decision m+1 (-1: none)."""
    idx = panel.adj_close.index
    pos = idx.get_indexer(decisions)
    entry = pos + 1
    exit_ = np.r_[pos[1:] + 1, -1]
    ao = panel.adj_open
    out = pd.DataFrame(np.nan, index=decisions, columns=ao.columns)
    for m in range(len(decisions)):
        if 0 <= exit_[m] < len(idx) and entry[m] < len(idx):
            out.iloc[m] = (ao.iloc[exit_[m]] / ao.iloc[entry[m]] - 1.0).to_numpy()
    return out, entry, exit_


def build_samples(panel: Panel, cfg: Q01Config) -> pd.DataFrame:
    """(date, ticker) rows from the first decision with 252 sessions of history: FEATURES (+ __asof), the
    label ``ret`` (NaN for the newest month), its within-month percentile ``rank``, and the session
    positions ``t0`` (decision) and ``t1`` (label exit; the last session when unlabelled)."""
    decisions = month_ends(panel.adj_close.index)
    feats = build_features(panel, decisions, cfg.benchmark)
    assert_point_in_time(feats)
    ret, _, exit_ = label_returns(panel, decisions)
    pos = panel.adj_close.index.get_indexer(decisions)
    t0 = pd.Series(pos, index=decisions)
    t1 = pd.Series(np.where(exit_ < 0, len(panel.adj_close.index) - 1, exit_), index=decisions)
    df = feats[feats.index.get_level_values("ticker").isin(cfg.universe)].copy()
    df = df.dropna(subset=list(FEATURES))
    d = df.index.get_level_values("date")
    df["ret"] = ret.stack(future_stack=True).reindex(df.index).to_numpy()
    df["rank"] = df.groupby(level="date")["ret"].rank(pct=True)
    df["t0"] = t0.reindex(d).to_numpy()
    df["t1"] = t1.reindex(d).to_numpy()
    return df


def composite_score(x: pd.DataFrame, signs: dict[str, int]) -> pd.Series:
    """I-Q01-7: mean over features of sign x (within-month percentile rank - 0.5)."""
    parts = [signs[f] * (x[f].groupby(level="date").rank(pct=True) - 0.5) for f in signs]
    return pd.concat(parts, axis=1).mean(axis=1)


def quantile_weights(score: pd.Series, q: float) -> pd.DataFrame:
    rows = {}
    for d, s in score.groupby(level="date"):
        s = s.droplevel("date").dropna().sort_values(ascending=False, kind="stable")
        m = max(1, int(math.floor(q * len(s))))
        w = pd.Series(0.0, index=s.index)
        w.iloc[:m] = 1.0 / m
        w.iloc[-m:] = -1.0 / m
        rows[d] = w
    return pd.DataFrame(rows).T.fillna(0.0)


def daily_portfolio(weights: pd.DataFrame, adj_open: pd.DataFrame, cost_bps: float,
                    end: pd.Timestamp | None = None) -> pd.DataFrame:
    """I-Q01-8: open-to-open days from the session after each decision to the next entry (or ``end``, or the
    last available day); weights drift; cost at entry, (1 + net) = (1 + gross)(1 - c x turnover)."""
    ao = adj_open[weights.columns]
    idx = ao.index
    rr = (ao.shift(-1) / ao - 1.0).to_numpy()
    entries = idx.get_indexer(weights.index) + 1
    stop = len(idx) - 1 if end is None else int(idx.get_indexer([end])[0])
    c = cost_bps / 1e4
    w = np.zeros(len(weights.columns))
    rows = []
    for m, e in enumerate(entries):
        nxt = entries[m + 1] if m + 1 < len(entries) else stop
        target = weights.iloc[m].to_numpy(dtype=float)
        for d in range(e, min(nxt, stop)):
            r = rr[d]
            held = (target != 0) if d == e else (w != 0)
            if np.isnan(r[held]).any():
                raise ValueError(f"missing open for a held ETF on {idx[d].date()}")
            r = np.nan_to_num(r)
            to = float(np.abs(target - w).sum()) if d == e else 0.0
            if d == e:
                w = target.copy()
            g = float(w @ r)
            rows.append((idx[d], g, (1.0 + g) * (1.0 - c * to) - 1.0, to))
            w = w * (1.0 + r) / (1.0 + g)
    return pd.DataFrame(rows, columns=["date", "gross", "net", "turnover"]).set_index("date")


# ----------------------------------------------------------------- model
def fit_predict(fit_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame, params: dict[str, Any],
                cfg: Q01Config, threads: int) -> tuple[pd.Series, Any]:
    """I-Q01-2/3: L2 on the percentile rank; early stopping on ``val_df`` only; best-iteration predictions."""
    import lightgbm as lgb

    p = {"objective": "regression", **params, "seed": cfg.seed, "deterministic": True, "force_row_wise": True,
         "num_threads": threads, "verbosity": -1, "feature_pre_filter": False}
    cols = list(FEATURES)
    dtr = lgb.Dataset(fit_df[cols], fit_df["rank"])
    dva = lgb.Dataset(val_df[cols], val_df["rank"], reference=dtr)
    booster = lgb.train(p, dtr, num_boost_round=cfg.rounds, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(cfg.early_stopping, verbose=False)])
    pred = booster.predict(test_df[cols], num_iteration=booster.best_iteration)
    return pd.Series(pred, index=test_df.index), booster


def _split(train: pd.DataFrame, cfg: Q01Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The last ``validation_months`` of a training window validate; the rest fit, purged against them."""
    dates = train.index.get_level_values("date")
    cut = dates.max() - pd.DateOffset(months=cfg.validation_months)
    val = train[dates > cut]
    fit = train[dates <= cut]
    by_date = train.groupby(level="date")[["t0", "t1"]].first()
    vd = by_date.index > cut
    keep_pos = purge(np.flatnonzero(~vd), by_date["t0"].to_numpy(), by_date["t1"].to_numpy(), np.flatnonzero(vd),
                     cfg.embargo)
    return fit[fit.index.get_level_values("date").isin(by_date.index[keep_pos])], val


def window_keys(samples: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """Per decision: ``t0``, ``t1`` and ``key``, the entry session, the first day the label accrues (I-Q01-1).
    Folds, selection and holdout are cut on ``key``, so a label is realised inside the window that tests it.
    The newest decision (no next session yet) gets the day after the decision: it is unlabelled."""
    by_date = samples.groupby(level="date")[["t0", "t1"]].first()
    nxt = by_date["t0"].to_numpy(dtype=int) + 1
    have = nxt < len(sessions)
    keys = np.where(have, sessions[np.minimum(nxt, len(sessions) - 1)].to_numpy(),
                    (by_date.index + pd.Timedelta(days=1)).to_numpy())
    return by_date.assign(key=pd.DatetimeIndex(keys))


def _folds(samples: pd.DataFrame, sessions: pd.DatetimeIndex, cfg: Q01Config, first_test: pd.Timestamp,
           last_test_end: pd.Timestamp, allow_partial: bool) -> tuple[pd.DatetimeIndex, list]:
    """Walk-forward folds keyed by label entry (``window_keys``); returns the decision dates and the folds."""
    by_date = window_keys(samples, sessions)
    folds = walk_forward(pd.DatetimeIndex(by_date["key"]), by_date["t0"].to_numpy(), by_date["t1"].to_numpy(),
                         train_months=cfg.train_months, test_months=cfg.test_months, step_months=cfg.step_months,
                         first_test=first_test, last_test_end=last_test_end, embargo=cfg.embargo,
                         allow_partial=allow_partial)
    return by_date.index, folds


def _rows(samples: pd.DataFrame, dates: pd.DatetimeIndex, pos: np.ndarray) -> pd.DataFrame:
    return samples[samples.index.get_level_values("date").isin(dates[pos])]


def _stitched_preds(samples: pd.DataFrame, cfg: Q01Config, params: dict[str, Any], dates: pd.DatetimeIndex,
                    folds: list, threads: int) -> tuple[pd.Series, list[pd.Series]]:
    preds = []
    for f in folds:
        train = _rows(samples, dates, f.train).dropna(subset=["rank"])
        fit, val = _split(train, cfg)
        p, _ = fit_predict(fit, val, _rows(samples, dates, f.test), params, cfg, threads)
        preds.append(p)
    own = owned_months(folds, cfg.step_months)
    parts = [preds[k][preds[k].index.get_level_values("date") == dates[i]] for i, k in sorted(own.items())]
    return pd.concat(parts), preds


def _port(score: pd.Series, panel: Panel, cfg: Q01Config, cost_bps: float | None = None) -> pd.DataFrame:
    """The quantile portfolio of ``score``; the last position exits at the open after the next month-end."""
    w = quantile_weights(score, cfg.quantile)
    later = month_ends(panel.adj_close.index)
    later = later[later > w.index.max()]
    end = None if len(later) == 0 else panel.adj_close.index[panel.adj_close.index.get_loc(later[0]) + 1]
    return daily_portfolio(w, panel.adj_open, cfg.cost_bps if cost_bps is None else cost_bps, end)


@dataclass
class Selection:
    trials: pd.DataFrame       # config, fold, test_start, sr_pp, n
    stitched: pd.DataFrame     # daily net returns, one column per config ("c0".."c7")
    selected: int
    pbo: float


def run_selection(samples: pd.DataFrame, panel: Panel, cfg: Q01Config, threads: int,
                  progress: Any = None) -> Selection:
    # Keyed by entry: the last selection sample is the decision whose label ends by selection_end (I-Q01-1).
    dates, folds = _folds(samples, panel.adj_close.index, cfg,
                          cfg.selection_start + pd.DateOffset(months=cfg.train_months), cfg.selection_end,
                          allow_partial=False)
    if not folds:
        raise ValueError("no complete walk-forward fold inside the selection window")
    trials, nets = [], {}
    for c, params in enumerate(cfg.grid):
        stitched, per_fold = _stitched_preds(samples, cfg, params, dates, folds, threads)
        for k, (f, p) in enumerate(zip(folds, per_fold, strict=True)):
            net = _port(p, panel, cfg)["net"]
            trials.append({"config": c, "fold": k, "test_start": f.test_start, "sr_pp": M.sharpe_per_period(net),
                           "n": int(len(net))})
        nets[f"c{c}"] = _port(stitched, panel, cfg)["net"]
        if progress:
            progress((c + 1) / len(cfg.grid), f"config {c}")
    stitched_df = pd.DataFrame(nets).dropna()
    if len(stitched_df) and stitched_df.index.max() >= cfg.holdout_start:
        raise AssertionError("a selection return is dated inside the holdout")  # I-Q01-1, Review Focus 2
    srs = [M.sharpe_per_period(stitched_df[col]) for col in stitched_df.columns]
    selected = int(np.nanargmax(srs))  # argmax returns the first (lowest index) maximum: I-Q01-5
    return Selection(pd.DataFrame(trials), stitched_df, selected, float(cscv_pbo(stitched_df, 16)["pbo"]))


def hac_t(x: np.ndarray) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    lrv = float(newey_west_lrv((x - x.mean())[:, None], nw_lag_rule(x.size))[0, 0])
    return float(x.mean()), float(x.mean() / math.sqrt(lrv / x.size)) if lrv > 0 else float("nan")


def ic_table(pred: pd.Series, samples: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"pred": pred, "ret": samples["ret"].reindex(pred.index)}).dropna()
    rows = [{"date": d, "ic": g["pred"].corr(g["ret"]), "rank_ic": g["pred"].corr(g["ret"], method="spearman"),
             "n": int(len(g))} for d, g in df.groupby(level="date")]
    return pd.DataFrame(rows)


@dataclass
class Holdout:
    row: dict[str, Any]
    returns: pd.DataFrame
    costs: pd.DataFrame
    regimes: pd.DataFrame
    ic: pd.DataFrame
    deciles: pd.DataFrame
    verdict: Verdict


def run_holdout(samples: pd.DataFrame, panel: Panel, cfg: Q01Config, sel: Selection, threads: int) -> Holdout:
    sessions = panel.adj_close.index
    labelled = samples.dropna(subset=["ret"]).index.get_level_values("date").max()
    labelled_key = window_keys(samples, sessions).loc[labelled, "key"]
    dates, folds = _folds(samples, sessions, cfg, cfg.holdout_start, labelled_key, allow_partial=True)
    params = cfg.grid[sel.selected]
    pred, _ = _stitched_preds(samples, cfg, params, dates, folds, threads)
    pred = pred[pred.index.get_level_values("date") <= labelled]
    comp = composite_score(samples.loc[pred.index, list(cfg.signs)], cfg.signs)
    model, base = _port(pred, panel, cfg), _port(comp, panel, cfg)
    net = model["net"]
    sr = M.sharpe_per_period(net)
    sk, ku = M.moments(net)
    trial_srs = sel.trials["sr_pp"].to_numpy(dtype=float)
    dsr = M.deflated_sharpe(sr, len(net), sk, ku, len(trial_srs), float(np.nanvar(trial_srs, ddof=1)))["dsr"]
    boot = bootstrap_sharpe(net, None, reps=1000, alpha=0.10, use_kernels=True, threads=threads)
    if net.index.min() < cfg.holdout_start:
        raise AssertionError("a holdout return is dated before holdout_start")
    record = pd.concat([sel.stitched[f"c{sel.selected}"], net]).sort_index()  # disjoint by construction
    if record.index.duplicated().any():
        raise AssertionError("selection and holdout returns overlap")
    regimes = regime_table(record)
    costs = pd.DataFrame([{"cost_bps": b, "model_sharpe": M.sharpe(_port(pred, panel, cfg, b)["net"]),
                           "composite_sharpe": M.sharpe(_port(comp, panel, cfg, b)["net"]),
                           "model_total": M.total_return(_port(pred, panel, cfg, b)["net"])} for b in COST_GRID_BPS])
    beats = M.sharpe(net) - M.sharpe(base["net"])
    v = charter_verdict(holdout_return=M.total_return(net), dsr=dsr, psr=M.probabilistic_sharpe(sr, len(net), sk, ku),
                        boot_lo5=boot["ci_low"], regimes=regimes, pbo=sel.pbo, costs=costs,
                        extra=[Gate("beats_linear_composite", beats, "> 0", beats > 0,
                                    note="model holdout net Sharpe minus composite's")])
    ic = ic_table(pred, samples)
    m_ic, t_ic = hac_t(ic["ic"].to_numpy())
    m_ric, t_ric = hac_t(ic["rank_ic"].to_numpy())
    dec = pd.DataFrame({"pred": pred, "ret": samples["ret"].reindex(pred.index)}).dropna()
    dec["decile"] = dec.groupby(level="date")["pred"].transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                                                                    labels=False) + 1)
    deciles = dec.groupby("decile")["ret"].mean().rename("mean_next_month_return").reset_index()
    w = quantile_weights(pred, cfg.quantile)
    adv = samples["dollar_vol_20d"].unstack("ticker").reindex(w.index)
    cap = (CAPACITY_ADV_SHARE * adv / w.abs().where(w != 0)).min(axis=1).median()
    years = len(net) / 252.0
    row = {"holdout_start": str(net.index[0].date()), "holdout_end": str(net.index[-1].date()),
           "selected_config": sel.selected, "params": str(params), "verdict": v.value, "verdict_detail": v.detail,
           "net_sharpe": M.sharpe(net), "composite_net_sharpe": M.sharpe(base["net"]), "total_return": M.total_return(net),
           "max_drawdown": M.max_drawdown(net), "dsr": dsr, "n_trials": int(len(trial_srs)),
           "psr": M.probabilistic_sharpe(sr, len(net), sk, ku), "boot_lo5": boot["ci_low"], "pbo": sel.pbo,
           "ic_mean": m_ic, "ic_hac_t": t_ic, "rank_ic_mean": m_ric, "rank_ic_hac_t": t_ric,
           "annual_turnover": float(model["turnover"].sum() / years), "capacity_usd": float(cap)}
    rets = pd.DataFrame({"model_net": net, "composite_net": base["net"].reindex(net.index)})
    return Holdout(row, rets.reset_index(), costs, regimes, ic, deciles, v)


def score_month(samples: pd.DataFrame, cfg: Q01Config, params: dict[str, Any], threads: int) -> tuple[pd.DataFrame, str]:
    """Monthly retrain on the latest ``train_months`` of labelled samples; scores for the newest decision."""
    latest = samples.index.get_level_values("date").max()
    labelled = samples.dropna(subset=["rank"])
    start = latest - pd.DateOffset(months=cfg.train_months)
    train = labelled[labelled.index.get_level_values("date") >= start]
    by = samples.groupby(level="date")[["t0", "t1"]].first()
    test_t0 = int(by.loc[latest, "t0"])
    train = train[train["t1"] < test_t0]                       # purge labels reaching the scored month
    fit, val = _split(train, cfg)
    now = samples[samples.index.get_level_values("date") == latest]
    pred, booster = fit_predict(fit, val, now, params, cfg, threads)
    w = quantile_weights(pred, cfg.quantile).iloc[0]
    out = pd.DataFrame({"ticker": pred.index.get_level_values("ticker"), "score": pred.to_numpy(),
                        "side": ["long" if w[t] > 0 else "short" if w[t] < 0 else "none"
                                 for t in pred.index.get_level_values("ticker")]})
    out["rank"] = out["score"].rank(ascending=False, method="first").astype(int)
    return out.sort_values("rank").reset_index(drop=True), booster.model_to_string()


# ----------------------------------------------------------------- freeze
# Vendors whose bars_daily.adj_close is dividend-adjusted (data/prices.py: alpaca adjustment=all, yahoo
# adjclose). Stooq publishes none (adj_close = close); fixtures are split-only. Allow-list: an unknown source
# is unadjusted until classified (test_every_price_provider_is_classified).
DIVIDEND_ADJUSTED_SOURCES = frozenset({"alpaca", "yahoo"})
UNADJUSTED_SOURCES = frozenset({"stooq"})


def _not_a_fund(ticker: str) -> str | None:
    """Symbols in universes.json that the quintile portfolio cannot hold: indexes (``^VIX``) and crypto
    pairs (``BTC-USD``). The pre-registration's universe is liquid ETFs."""
    if ticker.startswith("^"):
        return "index"
    if ticker.endswith("-USD"):
        return "crypto pair"
    return None


def freeze_universe(coverage: dict[str, dict[str, Any]], spy_dates: pd.DatetimeIndex, window_start: pd.Timestamp,
                    freeze_date: pd.Timestamp, adjusted_from: pd.Timestamp | None = None
                    ) -> tuple[list[str], dict[str, str]]:
    """I-Q01-9. ``coverage[t] = {"dates": DatetimeIndex, "source": array of bars_daily.source, one per date}``.
    Adjustment is judged by source over ``[adjusted_from, freeze_date]`` (default ``window_start``), never by
    adj_close == close: a fund with no distributions (GLD) has adj_close == close from every vendor."""
    cal = spy_dates[spy_dates <= freeze_date]
    since = window_start if adjusted_from is None else adjusted_from
    members, excluded = [], {}
    for t, c in sorted(coverage.items()):
        kind = _not_a_fund(t)
        if kind:
            excluded[t] = f"not a tradable fund ({kind})"
            continue
        d = pd.DatetimeIndex(c["dates"])
        if d.empty:
            excluded[t] = "no bars"
            continue
        if d.min() > window_start:
            excluded[t] = f"first bar {d.min().date()} after {window_start.date()}"
            continue
        src = pd.Series(np.asarray(c["source"], dtype=object), index=d)
        bad = sorted({str(x) for x in src[(d >= since) & (d <= freeze_date)]} - DIVIDEND_ADJUSTED_SOURCES)
        if bad:
            excluded[t] = (f"no dividend-adjusted close: bars from {', '.join(bad)} on or after {since.date()} "
                           "(only alpaca/yahoo publish one)")
            continue
        span = cal[cal >= d.min()]
        have = span.isin(d)
        run = longest = 0
        for h in have:
            run = 0 if h else run + 1
            longest = max(longest, run)
        if longest > 5:
            excluded[t] = f"gap of {longest} sessions vs SPY"
            continue
        members.append(t)
    return members, excluded
