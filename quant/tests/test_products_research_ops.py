"""2026-10-19's three scheduled kinds on fixture data: kronos.shadow, ops.data_integrity, factors.screen.

Kronos itself never runs here (no torch in quant): ``run_kronos`` is stubbed with a fixed multiple of EWMA, and
the subprocess path is covered in research/kronos's own suite (tests/test_shadow.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest
from lane_m_harness import is_label, publish_artifact

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import data_integrity as DI
from ohcamel_quant.products import factor_screen as FS
from ohcamel_quant.products import kronos_shadow as KS


@dataclass
class _DS:
    data: pd.DataFrame

    def provenance_dicts(self):
        return [{"source": "fixture", "fetched_at": "", "detail": {}}]


class Truncated:
    """A market whose history stops at ``end``: the night before, as the worker would have seen it."""

    def __init__(self, market, end):
        self.m, self.end = market, pd.Timestamp(end)

    def ohlcv(self, t, start=None, end=None):
        d = self.m.ohlcv(t).data
        return _DS(d.loc[: self.end if end is None else min(self.end, pd.Timestamp(end))])


def ctx(market, params=None):
    return JobContext(job_id="t", params=params or {}, threads=1, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=market)


def stub_kronos(scale):
    def run(bars, python, timeout_s=0, threads=1):
        rows = []
        for s, b in bars.items():
            r = b["close"].pct_change().dropna().to_numpy()
            v = KS.ewma_variance_path(r, 0.94)[-1] * scale
            rows.append({"symbol": s, "asof": str(b.index[-1].date()), "target_hint": "", "var_kronos": v,
                         "q05_kronos": -1.645 * np.sqrt(v), "distinct_frac": 0.7, "seed": 1})
        return pd.DataFrame(rows)
    return run


@pytest.fixture
def shadow_db(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(KS, "DB_PATH", db)
    monkeypatch.setattr(KS, "kronos_python", lambda: tmp_path)          # "available"
    monkeypatch.setattr(KS, "run_kronos", stub_kronos(3.0))
    return db, tmp_path / "artifacts"


def test_shadow_forecasts_tonight_and_scores_nothing_until_tomorrow_exists(market, shadow_db):
    spec = KS.run({"tickers": ["SPY", "TLT"]}, ctx(Truncated(market, "2026-05-01")))
    led = spec.tables["ledger"]
    assert {"kronos", "garch", "gjr", "ewma"} <= set(led["model"])     # real GARCH fits, one night
    assert set(led["asof"]) == {"2026-05-01"} and spec.tables["realised"].empty
    assert spec.verdict == "DESCRIPTIVE ONLY" and "ACCRUING 0 OF 20" in spec.verdict_detail
    assert "FAILED EXP-K01" in spec.verdict_detail and is_label(spec.verdict_detail)


def test_a_made_forecast_is_never_rewritten_and_is_scored_once_realised(market, shadow_db, monkeypatch):
    db, root = shadow_db
    real_inc = KS.incumbent_rows    # EWMA alone in the loop: 23 nights of GARCH refits on the NumPy reference is minutes
    monkeypatch.setattr(KS, "incumbent_rows", lambda sym, ret, made_at: real_inc(sym, ret.iloc[-500:], made_at))
    nights = pd.bdate_range("2026-04-01", "2026-05-01")
    spec = None
    for night in nights:                                   # the worker, night after night, carrying the ledger
        spec = KS.run({"tickers": ["SPY", "TLT"]}, ctx(Truncated(market, night)))
        publish_artifact(db, root, KS.KIND, spec.tables, verdict=spec.verdict,
                         finished=pd.Timestamp(night, tz="UTC").to_pydatetime() + pd.Timedelta(hours=22))
    first = spec.tables["ledger"]
    again = KS.run({"tickers": ["SPY", "TLT"]}, ctx(Truncated(market, nights[-1])))
    pd.testing.assert_frame_equal(again.tables["ledger"][first.columns].reset_index(drop=True),
                                  first.reset_index(drop=True))           # a rerun adds nothing, rewrites nothing
    real = spec.tables["realised"]
    assert not real.empty and (pd.to_datetime(real["target"]) > pd.to_datetime(real["asof"])).all()
    sc = spec.tables["scores"].set_index(["symbol", "model"])
    assert sc.loc[("SPY", "kronos"), "qlike"] > sc.loc[("SPY", "ewma"), "qlike"]   # 3x EWMA loses to EWMA
    pooled = spec.tables["pooled"].iloc[0]
    assert pooled["days"] >= 20 and pooled["stat"] > 0
    assert "PROSPECTIVE DAYS" in spec.verdict_detail and is_label(spec.verdict_detail)


def test_shadow_without_the_kronos_runtime_says_so_and_keeps_the_incumbents(market, tmp_path, monkeypatch):
    monkeypatch.setattr(KS, "DB_PATH", tmp_path / "jobs.sqlite")
    monkeypatch.setattr(KS, "kronos_python", lambda: None)
    spec = KS.run({"tickers": ["SPY"]}, ctx(Truncated(market, "2026-05-01")))
    assert "kronos" not in set(spec.tables["ledger"]["model"])
    assert any("Kronos runtime unavailable" in n for n in spec.notes)


def test_realise_matches_each_forecast_to_the_first_bar_after_it():
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    rets = {"A": pd.Series([0.01, -0.02, 0.03], index=idx)}
    led = pd.DataFrame([{"symbol": "A", "asof": "2026-01-02", "model": "ewma", "var": 1e-4, "q05": np.nan,
                         "made_at": "x"},
                        {"symbol": "A", "asof": "2026-01-06", "model": "ewma", "var": 1e-4, "q05": np.nan,
                         "made_at": "x"}])
    real = KS.realise(led, rets)
    assert real[["asof", "target", "r"]].values.tolist() == [["2026-01-02", "2026-01-05", -0.02]]   # holiday-proof


# ------------------------------------------------------------------ ops.data_integrity
def _bars(n=30):
    idx = pd.bdate_range("2026-01-01", periods=n)
    c = np.linspace(100, 110, n)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "adj_close": c,
                         "volume": 1e6}, index=idx)


def test_integrity_finds_each_defect_that_has_happened_here():
    clean = _bars()
    assert DI.check_bars(clean) == []
    split = clean.copy()
    split.iloc[20:, :5] = split.iloc[20:, :5] / 2           # a 2:1 split nobody adjusted (XLE, 2025-12-05)
    got = {f["check"] for f in DI.check_bars(split)}
    assert got == {"split_suspect"}
    adjusted = split.copy()
    adjusted.iloc[:20, :5] = adjusted.iloc[:20, :5] / 2      # the same split, adjusted: clean
    assert DI.check_bars(adjusted) == []
    bad = clean.copy()
    bad.iloc[10, bad.columns.get_loc("low")] = 10.4          # a decimal-shift low (SPY, 2026-02-02)
    assert [f["check"] for f in DI.check_bars(bad)] == ["bad_print"]
    crash = clean.copy()
    crash.iloc[15:, :5] = crash.iloc[15:, :5] * 0.5
    crash.iloc[15, crash.columns.get_loc("open")] = crash.iloc[14]["close"] * 0.6   # no split signature overnight
    assert "jump" in {f["check"] for f in DI.check_bars(crash)}
    neg = clean.copy()
    neg.iloc[3, neg.columns.get_loc("volume")] = -1
    assert [f["check"] for f in DI.check_bars(neg)] == ["nonpositive"]
    dup = pd.concat([clean, clean.iloc[[5]]]).sort_index()
    assert "calendar" in {f["check"] for f in DI.check_bars(dup)}


def test_stale_series_are_named():
    last = {"A": pd.Timestamp("2026-10-16"), "B": pd.Timestamp("2026-10-01")}
    assert [f["ticker"] for f in DI.stale(last)] == ["B"]


def test_integrity_job_passes_on_the_repaired_fixtures(market):
    spec = DI.run({}, ctx(market))
    assert spec.verdict == "PASS", spec.tables["findings"]
    assert spec.tables["summary"].iloc[0]["fixture_series"] == 9 and is_label(spec.verdict_detail)


def test_integrity_job_fails_on_the_archived_unadjusted_files(market, monkeypatch, tmp_path):
    arc = DI.F._root() / "archive/2026-10-19-vendor-defects"
    h = tmp_path / "history"
    h.mkdir()
    for s in ("XLE", "SPY"):
        (h / f"{s}.parquet").write_bytes((arc / f"{s}.unadjusted.parquet").read_bytes())
    monkeypatch.setattr(DI.F, "_root", lambda: tmp_path)
    spec = DI.run({}, ctx(market))
    f = spec.tables["findings"]
    assert spec.verdict == "FAIL" and {"split_suspect", "bad_print"} <= set(f["check"])
    assert set(f.loc[f["check"] == "split_suspect", "date"]) == {"2025-12-05"} and is_label(spec.verdict_detail)


# ------------------------------------------------------------------ factors.screen
def _ledger(tmp_path, exprs):
    p = tmp_path / "trials.jsonl"
    from ohcamel_quant.factors import expr as E
    p.write_text("".join(json.dumps({"hash": E.expr_hash(e), "expression": E.canonical(e), "hypothesis": "h" * 9,
                                     "ts": "2026-10-19"}) + "\n" for e in exprs))
    return p


def test_screen_with_an_empty_ledger_says_so(market, tmp_path):
    spec = FS.run({"ledger": str(_ledger(tmp_path, []))}, ctx(market))
    assert spec.verdict == "INSUFFICIENT DATA" and spec.verdict_detail == "NO TRIALS IN THE LEDGER YET"


def test_screen_rescores_distinct_trials_and_flags_duplicates(market, tmp_path):
    led = _ledger(tmp_path, ["Mean($close, 20)/$close", "Mean( $close,20 )/$close", "Rsquare($close, 30)",
                             "$close / 0"])
    lib = [("MA20", "Mean($close, 20)/$close"), ("ROC5", "Ref($close, 5)/$close")]
    trials = FS.distinct_trials(led)
    assert len(trials) == 3                                   # two spellings of one expression are one trial
    fields, _ = FS.load_fields(ctx(market), ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLE"], FS.W.EXPLORATION_END)
    out = FS.screen(trials, fields, lib)
    s = out["screens"].set_index("expression")
    assert s.loc["Div(Mean($close, 20), $close)", "duplicate_of"] == "MA20"
    assert pd.isna(s.loc["Rsquare($close, 30)", "duplicate_of"])
    assert not s.loc["Div($close, 0)", "lint_ok"]              # all NaN: fails lint, never scored
    assert fields["close"].index[-1] <= FS.W.EXPLORATION_END   # the holdout is never loaded


def test_screen_job_on_the_fixture_etfs(market, tmp_path):
    led = _ledger(tmp_path, ["Rsquare($close, 30)"])
    spec = FS.run({"ledger": str(led), "library": "none"}, ctx(market))
    assert spec.verdict == "DESCRIPTIVE ONLY" and "1 TRIALS" in spec.verdict_detail and is_label(spec.verdict_detail)
    assert spec.tables["screens"].iloc[0]["lint_ok"]
