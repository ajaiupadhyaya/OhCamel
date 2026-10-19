"""The factor workbench: RD-Agent's lint and dedup, qlib's signal report, and the honest loop around them."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest.engine import LookAheadError
from ohcamel_quant.factors import expr as E
from ohcamel_quant.factors import workbench as W

HYP = "names that ran above their recent average give part of it back as liquidity providers are paid"


@pytest.fixture(scope="module")
def fields():
    return W.fixture_fields()


@pytest.fixture(scope="module")
def lib(fields):
    explore = {k: v.loc[:W.EXPLORATION_END] for k, v in fields.items()}
    return {n: E.evaluate(e, explore) for n, e in [("MA20", "Mean($close, 20)/$close"), ("ROC5", "Ref($close, 5)/$close")]}


def test_the_hypothesis_comes_first_and_the_holdout_is_out_of_reach(fields, lib):
    with pytest.raises(W.Refused, match="hypothesis first"):
        W.evaluate("Mean($close, 5)/$close", "it might work", fields, library=lib)
    with pytest.raises(W.Refused, match="exploration window only"):
        W.evaluate("Mean($close, 5)/$close", HYP, fields, library=lib, window_end=pd.Timestamp("2024-06-30"))
    with pytest.raises(LookAheadError):
        W.evaluate("Ref($close, -1)/$close", HYP, fields, library=lib)


def test_nothing_after_the_exploration_window_is_read(fields, lib):
    """Overwrite every price after EXPLORATION_END with garbage: the screen, labels included, is unchanged."""
    keep = pd.Series(fields['close'].index <= W.EXPLORATION_END, index=fields['close'].index)
    poisoned = {k: v.where(keep, v * 1e6, axis=0) for k, v in fields.items()}
    assert not poisoned['close'].equals(fields['close'])
    a = W.evaluate("Mean($close, 5)/$close", HYP, fields, library=lib)
    b = W.evaluate("Mean($close, 5)/$close", HYP, poisoned, library=lib)
    assert pd.Series(a.trial.summary).equals(pd.Series(b.trial.summary))


def test_duplicates_are_caught_whichever_sign_they_carry(fields, lib):
    same = W.evaluate(" ( Mean($close,20) ) / $close", HYP, fields, library=lib)
    neg = W.evaluate("-1 * Mean($close, 20) / $close", HYP, fields, library=lib)
    assert same.trial.duplicate_of == "MA20" and neg.trial.duplicate_of == "MA20"   # RD-Agent's signed test misses neg
    assert neg.similar.iloc[0]["mean_xs_corr"] == pytest.approx(-1.0)
    fresh = W.evaluate("Rsquare($close, 30)", HYP, fields, library=lib)
    assert fresh.trial.duplicate_of is None


def test_ledger_counts_distinct_expressions_not_runs(tmp_path, fields, lib):
    led = W.TrialLedger(tmp_path / "trials.jsonl")
    for e in ("Mean($close, 5)/$close", "Mean( $close,5 ) / $close", "Std($close, 10)/$close"):
        W.evaluate(e, HYP, fields, library=lib, ledger=led)
    recs = led.records()
    assert len(recs) == 3 and led.n_trials() == 2
    assert recs[0]["hypothesis"] == HYP and recs[0]["hash"] == E.expr_hash("Mean($close,5)/$close")


def test_forward_returns_are_open_to_open_from_the_next_session():
    idx = pd.bdate_range("2024-01-01", periods=5)
    o = pd.DataFrame({"A": [10.0, 11, 12, 13, 14]}, index=idx)
    f = W.forward_returns(o, 2)
    assert f.loc[idx[0], "A"] == pytest.approx(13 / 11 - 1)   # decided at close of day 0: open 1 -> open 3
    assert f.iloc[-3:].isna().all().all()


def test_the_signal_report_reads_a_perfect_and_an_inverted_oracle(fields):
    """Alignment check: a 'factor' equal to its own next-day label has rank IC 1; minus it, -1."""
    o = fields["open"].loc[:W.EXPLORATION_END]
    label = W.forward_returns(o, 1)
    good = W.signal_report(label, o)["summary"]
    bad = W.signal_report(-label, o)["summary"]
    assert good["rank_ic_mean"] == pytest.approx(1.0) and bad["rank_ic_mean"] == pytest.approx(-1.0)
    assert good["long_short_mean_daily"] > 0 > bad["long_short_mean_daily"]


def test_lint_catches_what_rd_agents_evaluators_catch():
    idx = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(0)
    ok = pd.DataFrame(rng.normal(size=(30, 3)), index=idx, columns=list("ABC"))
    assert all(c.ok for c in W.lint(ok))
    names = lambda df: {c.name for c in W.lint(df) if not c.ok}   # noqa: E731
    assert names(ok.replace(ok.iloc[3, 0], np.inf)) == {"no_inf"}
    assert "varies_across_names" in names(pd.DataFrame(1.0, index=idx, columns=list("ABC")).add(np.arange(30), axis=0))
    holey = ok.copy()
    holey.iloc[5:20, 1] = np.nan
    assert "missing" in names(holey)
    assert "daily_index" in names(ok.iloc[::-1])
    stuck = ok.copy()
    stuck["C"] = 2.0
    assert "varies_in_time" in names(stuck)
    assert names(pd.DataFrame(np.nan, index=idx, columns=list("AB"))) == {"missing"}


def test_cli_refuses_without_a_hypothesis_and_screens_with_one(capsys):
    assert W.main(["Mean($close, 5)/$close", "--hypothesis", "too short", "--no-log"]) == 2
    assert "hypothesis first" in capsys.readouterr().err
    assert W.main(["Mean($close, 20)/$close", "--hypothesis", HYP, "--no-log"]) == 0
    out = capsys.readouterr().out
    assert "MA20" in out and "DUPLICATE" in out and "exploration only" in out
