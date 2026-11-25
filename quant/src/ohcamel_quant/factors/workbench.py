"""The factor workbench: RD-Agent's research loop, with the owner in the loop and the trials counted.

Microsoft's RD-Agent (MIT) runs hypothesis -> implementation -> evaluation -> feedback with an LLM proposing
the hypotheses and choosing winners on the test segment. That inverts this repository's charter twice: the
owner owns hypotheses, and selection on the holdout is not selection. What survives the translation, and is
built here:

- **The checks RD-Agent's evaluators run before anything is scored** (``rdagent/components/coder/factor_coder/
  eva_utils.py``): a daily datetime index, no infinities, missing values, and a factor that does not vary. ``lint``.
- **Its redundancy screen** (``rdagent/scenarios/qlib/developer/factor_runner.py:deduplicate_new_factors``): a
  new factor whose mean daily cross-sectional correlation with an existing one reaches 0.99 is a duplicate.
  RD-Agent compares the SIGNED correlation, so a factor that is exactly minus an existing one (correlation -1,
  the same information) passes. Here the screen uses |correlation|. ``redundancy``.
- **The loop, made honest.** A hypothesis written in words is required before an expression is evaluated, and
  every evaluation is appended to a ledger keyed by the expression's canonical hash. The ledger's count of
  distinct expressions is the ``n_trials`` a Deflated Sharpe on anything promoted from here must use. RD-Agent
  forgets its failed rounds; this ledger cannot (it is append-only and committed).
- **Holdout protection.** The workbench scores the EXPLORATION window only (to ``EXPLORATION_END``). It has no
  option to score later dates. The holdout belongs to the pre-registered battery, which runs once.

The signal report follows qlib's ``contrib/report/analysis_model`` and ``analysis_position``: daily IC and
rank IC (with HAC t-statistics, as EXP-Q01 reports them), IC decay over horizons, the lag-1 autocorrelation of
the factor's cross-sectional ranks (qlib's turnover proxy) and quantile-group returns. It is all reported and
never gated. A good screen is a reason to write a pre-registration, not a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest.engine import LookAheadError
from ..models.xsec import hac_t
from . import alpha158 as A
from . import expr as E
from . import models as FM
from . import performance as PF

EXPLORATION_END = pd.Timestamp("2023-12-31")
HORIZONS = (1, 5, 10, 20)
DUPLICATE = 0.99
MIN_HYPOTHESIS_WORDS = 8
BETA_WINDOW = 60
CS_SOURCE = "fixtures/history ETF opens (open-to-open labels), exploration window"
NAMES = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK")


# ============================================================ lint (RD-Agent's evaluators)
@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def lint(values: pd.DataFrame, *, max_missing: float = 0.05, max_flat_days: float = 0.01) -> list[Check]:
    """Structural checks on a (date x ticker) factor, before any return is looked at."""
    out: list[Check] = []
    idx = values.index
    daily = (isinstance(idx, pd.DatetimeIndex) and idx.is_monotonic_increasing and idx.is_unique
             and (len(idx) < 2 or pd.Series(idx).diff().dropna().dt.days.between(1, 5).all()))
    out.append(Check("daily_index", bool(daily), "sorted, unique, sessions 1-5 days apart" if daily
                     else "the index is not a sorted, unique, daily DatetimeIndex"))
    n_inf = int(np.isinf(values.to_numpy(float)).sum())
    out.append(Check("no_inf", n_inf == 0, f"{n_inf} infinite values"))
    full = values.notna().all(axis=1)
    first = full.idxmax() if full.any() else None
    if first is None:
        out.append(Check("missing", False, "no date has a value for every ticker"))
        return out
    miss = float(values.loc[first:].isna().to_numpy().mean())
    out.append(Check("missing", miss <= max_missing,
                     f"{miss:.1%} missing after the warm-up (first complete date {first.date()}); max {max_missing:.0%}"))
    body = values.loc[first:].dropna(how="any")
    flat = float((body.nunique(axis=1) <= 1).mean()) if len(body) else 1.0
    out.append(Check("varies_across_names", flat <= max_flat_days,
                     f"{flat:.1%} of dates have one value for every ticker (nothing to rank); max {max_flat_days:.0%}"))
    stuck = [c for c in body.columns if body[c].nunique() <= 1]
    out.append(Check("varies_in_time", not stuck, "every ticker's value changes" if not stuck
                     else f"constant for {', '.join(stuck)}"))
    return out


# ============================================================ redundancy (RD-Agent's dedup, |corr|)
def _xs_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-date (row) Pearson correlation across tickers; NaN where either row has a gap or no spread."""
    ok = ~(np.isnan(a) | np.isnan(b)).any(axis=1)
    out = np.full(a.shape[0], np.nan)
    da = a[ok] - a[ok].mean(axis=1, keepdims=True)
    db = b[ok] - b[ok].mean(axis=1, keepdims=True)
    den = np.sqrt((da * da).sum(axis=1) * (db * db).sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        out[ok] = np.where(den > 0, (da * db).sum(axis=1) / den, np.nan)
    return out


def redundancy(values: pd.DataFrame, library: dict[str, pd.DataFrame], threshold: float = DUPLICATE) -> pd.DataFrame:
    """Mean daily cross-sectional correlation with every library factor, most similar first."""
    a = values.to_numpy(float)
    rows = []
    for name, lib in library.items():
        c = _xs_corr(a, lib.reindex_like(values).to_numpy(float))
        m = float(np.nanmean(c)) if np.isfinite(c).any() else np.nan
        rows.append({"factor": name, "mean_xs_corr": m, "abs": abs(m), "duplicate": bool(abs(m) >= threshold)})
    return pd.DataFrame(rows, columns=["factor", "mean_xs_corr", "abs", "duplicate"]).sort_values(
        "abs", ascending=False, na_position="last").reset_index(drop=True)


# ============================================================ signal report (qlib's analysis_model)
def forward_returns(open_adj: pd.DataFrame, h: int) -> pd.DataFrame:
    """Open(t+1) -> open(t+1+h): a factor known at the close of t is traded at the next open (charter
    principle 1). These are LABELS: they look forward by construction and are only ever compared with a
    factor value dated t."""
    return open_adj.shift(-1 - h) / open_adj.shift(-1) - 1.0


def _rank_corr(a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
    return _xs_corr(a.rank(axis=1).to_numpy(float), b.rank(axis=1).to_numpy(float))


def signal_report(values: pd.DataFrame, open_adj: pd.DataFrame, *, horizons: tuple[int, ...] = HORIZONS,
                  groups: int = 3) -> dict[str, Any]:
    fwd1 = forward_returns(open_adj, 1).reindex_like(values)
    ic = _xs_corr(values.to_numpy(float), fwd1.to_numpy(float))
    ric = _rank_corr(values, fwd1)
    ic_m, ic_t = hac_t(ic)
    ric_m, ric_t = hac_t(ric)
    finite = ric[np.isfinite(ric)]
    summary = {"dates": int(np.isfinite(ric).sum()), "names_median": int(values.notna().sum(axis=1).median()),
               "ic_mean": ic_m, "ic_hac_t": ic_t, "rank_ic_mean": ric_m, "rank_ic_hac_t": ric_t,
               "rank_icir": float(finite.mean() / finite.std(ddof=1)) if finite.size > 1 and finite.std() > 0 else np.nan,
               "rank_ic_positive_share": float((finite > 0).mean()) if finite.size else np.nan}
    decay = []
    for h in horizons:
        r = _rank_corr(values, forward_returns(open_adj, h).reindex_like(values))
        m, t = hac_t(r)                                     # overlapping h-day labels: the NW lags absorb it
        decay.append({"horizon": h, "rank_ic_mean": m, "rank_ic_hac_t": t})
    rk = values.rank(axis=1)
    summary["rank_autocorr_lag1"] = float(np.nanmean(_xs_corr(rk.to_numpy(float), rk.shift(1).to_numpy(float))))
    # quantile groups on each date, equal-weighted next-day (open-to-open) returns
    pct = values.rank(axis=1, pct=True)
    bucket = np.ceil(pct * groups).clip(1, groups)
    grp = pd.DataFrame({g: fwd1.where(bucket == g).mean(axis=1) for g in range(1, groups + 1)})
    grp["long_short"] = grp[groups] - grp[1]
    ls = grp["long_short"].dropna()
    summary["long_short_mean_daily"] = float(ls.mean()) if len(ls) else np.nan
    summary["long_short_hac_t"] = hac_t(ls.to_numpy())[1] if len(ls) > 2 else np.nan
    return {"summary": summary, "decay": pd.DataFrame(decay), "ic_daily": pd.DataFrame(
        {"ic": ic, "rank_ic": ric}, index=values.index), "groups": grp}


# ============================================================ cross-sectional tests (Fama-MacBeth, GRS)
def _zscore_rows(df: pd.DataFrame) -> pd.DataFrame:
    sd = df.std(axis=1, ddof=0).replace(0.0, np.nan)
    return df.sub(df.mean(axis=1), axis=0).div(sd, axis=0)


def cross_section_report(values: pd.DataFrame, open_adj: pd.DataFrame, *, beta_window: int = BETA_WINDOW) -> dict[str, Any]:
    """The factor's price of risk, and whether its tercile portfolios earn alpha.

    **Fama-MacBeth** (``models.fama_macbeth``): each date the next-session open-to-open return label is regressed
    on the cross-sectionally standardised signal known at that date's close, so a premium is the return per one
    cross-sectional standard deviation of the signal. A second run residualises the signal on a trailing
    ``beta_window``-day beta to the equal-weight market (estimated on data to that same close, never later)
    before standardising. Standard errors are Newey-West only: a signal is a characteristic, not an estimated
    beta on an observed factor, so Shanken's errors-in-variables correction does not apply and is not reported.

    **GRS** (``models.grs_test``): are the top and bottom tercile portfolios' alphas, against the equal-weight
    market, jointly zero? (The middle tercile is left out: with equal terciles the three sum to the market.)
    Reported, not gated; a screen is not a result.
    """
    fwd1 = forward_returns(open_adj, 1).reindex_like(values)
    ret = open_adj.pct_change(fill_method=None).reindex_like(values)
    mkt = ret.mean(axis=1)
    beta = ret.rolling(beta_window, min_periods=beta_window).cov(mkt).div(mkt.rolling(beta_window, min_periods=beta_window).var(), axis=0)
    z = _zscore_rows(values)
    zn = _zscore_rows(PF.neutralise(z, groups=None, betas=beta))
    rows, slopes = {}, {}
    for name, sig in (("signal", z), ("signal, beta-neutral", zn)):
        r = FM.fama_macbeth(fwd1, {"signal": sig}, shanken=False)
        row = r.table().loc["signal", ["premium", "se_nw", "t_nw"]].to_dict()
        row.update(nw_lags=r.nw_lags, n_periods=r.n_periods, mean_assets=r.mean_assets)
        rows[name] = row
        slopes[name] = r.lambdas["signal"]
    pct = values.rank(axis=1, pct=True)
    bucket = np.ceil(pct * 3).clip(1, 3)
    low, high = (fwd1.where(bucket == g).mean(axis=1) for g in (1, 3))
    market = fwd1.mean(axis=1)
    panel = pd.concat({"bottom": low, "top": high, "mkt": market}, axis=1).dropna()
    g = FM.grs_test(panel[["bottom", "top"]], panel[["mkt"]])
    as_of = fwd1.dropna(how="all").index[-1]
    return {"premia": pd.DataFrame(rows).T, "daily_premia": pd.DataFrame(slopes), "source": CS_SOURCE, "as_of": str(as_of.date()),
            "grs": {"statistic": g.statistic, "p_value": g.p_value, "n_assets": g.n_assets, "n_factors": g.n_factors,
                    "n_obs": g.n_obs, "alpha_bottom_bp_day": 1e4 * float(g.alphas["bottom"]),
                    "alpha_top_bp_day": 1e4 * float(g.alphas["top"])}}


# ============================================================ the ledger
@dataclass
class Trial:
    ts: str
    hash: str
    expression: str
    hypothesis: str
    window_end: str
    lint_ok: bool
    duplicate_of: str | None
    summary: dict = field(default_factory=dict)


class TrialLedger:
    """Append-only JSON lines. One line per evaluation; the trial count is the number of DISTINCT hashes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, t: Trial) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(t), sort_keys=True, default=float) + "\n")

    def n_trials(self) -> int:
        """The n_trials a Deflated Sharpe on any factor promoted from this ledger must use."""
        return len({r["hash"] for r in self.records()})


# ============================================================ one turn of the loop
@dataclass
class Evaluation:
    trial: Trial
    checks: list[Check]
    similar: pd.DataFrame
    report: dict[str, Any] | None
    n_trials: int
    cross_section: dict[str, Any] | None = None


class Refused(ValueError):
    pass


def evaluate(expression: str, hypothesis: str, fields: dict[str, pd.DataFrame], *,
             library: dict[str, pd.DataFrame] | None = None, ledger: TrialLedger | None = None,
             window_end: pd.Timestamp = EXPLORATION_END) -> Evaluation:
    if len(hypothesis.split()) < MIN_HYPOTHESIS_WORDS:
        raise Refused(f"write the hypothesis first (at least {MIN_HYPOTHESIS_WORDS} words: why should this "
                      "predict returns, and who is on the other side?)")
    if window_end > EXPLORATION_END:
        raise Refused(f"the workbench scores the exploration window only (to {EXPLORATION_END.date()}); "
                      "later dates are the holdout, which the pre-registered battery scores once")
    node = E.parse(expression)
    explore = {k: v.loc[:window_end] for k, v in fields.items()}
    values = E.evaluate(node, explore)
    # labels are built from exploration prices too, so the last dates simply have no label: purged, rather than
    # scored on an open from the holdout. Nothing after window_end is read, factor or label.
    open_adj = explore["open"]
    checks = lint(values)
    lib = library if library is not None else {n: E.evaluate(e, explore) for n, e in A.feature_config()}
    similar = redundancy(values, lib)
    dup = similar.loc[similar["duplicate"], "factor"].tolist()
    report = signal_report(values, open_adj) if all(c.ok for c in checks) else None
    cross = cross_section_report(values, open_adj) if report else None
    trial = Trial(ts=datetime.now(UTC).isoformat(timespec="seconds"), hash=E.expr_hash(node), expression=str(node),
                  hypothesis=hypothesis.strip(), window_end=str(window_end.date()),
                  lint_ok=all(c.ok for c in checks), duplicate_of=dup[0] if dup else None,
                  summary=report["summary"] if report else {})
    if ledger is not None:
        ledger.append(trial)
    return Evaluation(trial, checks, similar, report, ledger.n_trials() if ledger else 1, cross)


def fixture_fields(names: tuple[str, ...] = NAMES) -> dict[str, pd.DataFrame]:
    root = Path(__file__).resolve().parents[4] / "fixtures/history"
    return A.adjusted_fields({s: pd.read_parquet(root / f"{s}.parquet").sort_index() for s in names})


def render(ev: Evaluation) -> str:
    t = ev.trial
    lines = [f"factor   {t.expression}", f"hash     {t.hash}", f"window   ... -> {t.window_end} (exploration only)",
             f"trials   {ev.n_trials} distinct expressions in the ledger: the n_trials any Deflated Sharpe on a "
             "factor promoted from here must use", "", "lint"]
    lines += [f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in ev.checks]
    lines += ["", "most similar existing factors (mean daily cross-sectional correlation)"]
    lines += [f"  {r.factor:<10} {r.mean_xs_corr:+.3f}{'  DUPLICATE' if r.duplicate else ''}"
              for r in ev.similar.head(5).itertuples()]
    if ev.report:
        s = ev.report["summary"]
        lines += ["", f"signal report (REPORTED, NOT GATED; {s['names_median']} names a date — a small cross-section)",
                  f"  IC {s['ic_mean']:+.4f} (HAC t {s['ic_hac_t']:+.2f})   rank IC {s['rank_ic_mean']:+.4f} "
                  f"(HAC t {s['rank_ic_hac_t']:+.2f})   rank ICIR {s['rank_icir']:+.3f}   "
                  f"positive {s['rank_ic_positive_share']:.0%} of {s['dates']} dates",
                  f"  rank autocorrelation (lag 1) {s['rank_autocorr_lag1']:.3f}   long-short "
                  f"{1e4 * s['long_short_mean_daily']:+.2f} bp/day (HAC t {s['long_short_hac_t']:+.2f})",
                  "  decay: " + "   ".join(f"{r.horizon}d {r.rank_ic_mean:+.4f} (t {r.rank_ic_hac_t:+.2f})"
                                          for r in ev.report["decay"].itertuples())]
        if ev.cross_section:
            c = ev.cross_section
            lines += ["", f"Fama-MacBeth premia, return per 1 s.d. of signal per day (Newey-West t; no Shanken correction: the signal is a characteristic; "
                          f"source {c['source']}; as of {c['as_of']})"]
            lines += [f"  {n:<22} {1e4 * r.premium:+.2f} bp/day   t {r.t_nw:+.2f}   "
                      f"NW lags {int(r.nw_lags)}, {int(r.n_periods)} days" for n, r in c["premia"].iterrows()]
            g = c["grs"]
            lines += [f"  GRS: top and bottom tercile alphas vs the equal-weight market, F = {g['statistic']:.2f}, "
                      f"p = {g['p_value']:.3f} (alpha bottom {g['alpha_bottom_bp_day']:+.1f}, top "
                      f"{g['alpha_top_bp_day']:+.1f} bp/day, N = {g['n_assets']}, {g['n_obs']} days; "
                      f"source {c['source']}; as of {c['as_of']})"]
    else:
        lines += ["", "signal report not run: the factor failed lint"]
    lines += ["", "A screen is not a result. Promotion goes through a pre-registration and the battery."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ohcamel_quant.factors.workbench",
                                 description="Screen one factor expression on the exploration window.")
    ap.add_argument("expression")
    ap.add_argument("--hypothesis", required=True, help="why it should predict returns, in words, written first")
    ap.add_argument("--ledger", type=Path,
                    default=Path(__file__).resolve().parents[4] / "research/experiments/FACTORS/trials.jsonl")
    ap.add_argument("--no-log", action="store_true", help="do not append to the ledger (tests and dry runs)")
    a = ap.parse_args(argv)
    try:
        ev = evaluate(a.expression, a.hypothesis, fixture_fields(), ledger=None if a.no_log else TrialLedger(a.ledger))
    except (Refused, E.ExprError, LookAheadError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(render(ev))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
