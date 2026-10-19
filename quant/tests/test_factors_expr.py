"""The factor expression language (qlib's operators) and its Alpha158 port.

The parity reference, ``tests/data/alpha158_qlib_golden.parquet``, is qlib 0.9.7's OWN output for Alpha158 on
the committed SPY and XLE bars (adjusted OHLC, raw volume), dumped into qlib's binary format with qlib's
``scripts/dump_bin.py``. It holds the first 70 sessions (the warm-up) and every session from 2025 on. qlib
stores and computes in float32, so the comparison feeds float32-rounded inputs and allows float32 tolerance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest.engine import LookAheadError
from ohcamel_quant.factors import alpha158 as A
from ohcamel_quant.factors import expr as E

FIX = E.__file__.rsplit("/quant/", 1)[0] + "/fixtures/history"
GOLDEN = __file__.rsplit("/", 1)[0] + "/data/alpha158_qlib_golden.parquet"


@pytest.fixture(scope="module")
def fields():
    bars = {s: pd.read_parquet(f"{FIX}/{s}.parquet").sort_index() for s in ["SPY", "XLE", "TLT"]}
    return A.adjusted_fields(bars)


def test_all_157_features_match_qlibs_own_output():
    bars = {s: pd.read_parquet(f"{FIX}/{s}.parquet").sort_index() for s in ["SPY", "XLE"]}
    f32 = {k: v.astype(np.float32).astype(np.float64) for k, v in A.adjusted_fields(bars).items()}
    ours = A.alpha158(f32, compat=True)
    g = pd.read_parquet(GOLDEN)
    g = g.set_index([pd.to_datetime(g["date"]), g["symbol"]]).drop(columns=["date", "symbol"])
    assert set(g.columns) - set(ours.columns) == {"VWAP0"} and len(ours.columns) == 157
    assert g["VWAP0"].isna().all()                          # qlib had no VWAP either
    o = ours.reindex(g.index)
    for c in ours.columns:
        a, b = o[c].to_numpy(), g[c].to_numpy(float)
        np.testing.assert_array_equal(np.isnan(a), np.isnan(b), err_msg=f"{c}: NaN pattern differs")
        np.testing.assert_allclose(a, b, rtol=1e-4, atol=1e-4, equal_nan=True, err_msg=c)


def test_strict_mode_waits_for_a_full_window_then_agrees_with_qlib(fields):
    s = E.evaluate("Mean($close, 60)", fields)
    c = E.evaluate("Mean($close, 60)", fields, compat=True)
    assert s.iloc[:59].isna().all().all() and c.iloc[:59].notna().all().all()
    pd.testing.assert_frame_equal(s.iloc[59:], c.iloc[59:])


def test_alpha158_strict_has_no_warmup_values(fields):
    a = A.alpha158(fields)
    first = a.xs("SPY", level="ticker")
    assert first["MA60"].iloc[:59].isna().all() and first["MA60"].iloc[59:].notna().all()
    assert first["ROC5"].iloc[5:].notna().all()


# One expression per operator. ``test_every_operator_is_point_in_time`` truncates the data at a date and
# demands the same value on that date, which is the definition of "uses nothing after t".
EVERY_OP = [
    "Abs($close - Ref($close, 1))", "Sign($close - $open)", "Log($volume + 1)", "Not($close > $open)",
    "Power($close, 2)", "$close + $open", "$close - $open", "$close * 2", "$close / $open",
    "Greater($open, $close)", "Less($open, $close)", "$close > $open", "$close >= $open", "$close < $open",
    "$close <= $open", "$close == $open", "$close != $open", "($close > $open) & ($volume > 0)",
    "($close > $open) | ($volume > 0)", "If($close > $open, $high, $low)",
    "Ref($close, 3)", "Mean($close, 5)", "Sum($close, 5)", "Std($close, 5)", "Var($close, 5)",
    "Skew($close, 10)", "Kurt($close, 10)", "Max($high, 5)", "Min($low, 5)", "Med($close, 5)",
    "Count($close, 5)", "IdxMax($high, 5)", "IdxMin($low, 5)", "Quantile($close, 10, 0.8)", "Mad($close, 5)",
    "Rank($close, 10)", "Delta($close, 3)", "Slope($close, 10)", "Rsquare($close, 10)", "Resi($close, 10)",
    "WMA($close, 5)", "EMA($close, 10)", "EMA($close, 0.2)", "Corr($close, $volume, 10)",
    "Cov($close, $volume, 10)", "CSRank($close / Ref($close, 20))",
]


def test_the_operator_list_covers_every_operator():
    used = set()
    def walk(n):
        if isinstance(n, E.Call):
            used.add(n.op)
            for a in n.args:
                walk(a)
    for e in EVERY_OP:
        walk(E.parse(e))
    assert used == set(E.OPERATORS)


@pytest.mark.parametrize("expr", EVERY_OP)
def test_every_operator_is_point_in_time(fields, expr):
    full = E.evaluate(expr, fields)
    for t in (fields["close"].index[100], fields["close"].index[-40]):
        cut = E.evaluate(expr, {k: v.loc[:t] for k, v in fields.items()})
        pd.testing.assert_series_equal(cut.loc[t], full.loc[t], check_names=False)


def test_lookahead_and_start_dependent_windows_are_refused(fields):
    with pytest.raises(LookAheadError):
        E.parse("Ref($close, -1)")
    with pytest.raises(LookAheadError):
        E.parse("Mean(Ref($close, -5), 20)")
    for e in ("Mean($close, 0)", "Ref($close, 0)", "Corr($close, $open, 0)"):
        with pytest.raises(E.ExprError, match="depends on where the data starts"):
            E.evaluate(e, fields)


@pytest.mark.parametrize("bad, msg", [
    ("Foo($close)", "unknown operator"), ("Mean($close)", "takes 2 arguments"), ("$nope + 1", "no field"),
    ("Mean($close, $open)", "window must be a number"), ("Mean($close, 2.5)", "non-negative integer"),
    ("Quantile($close, 5, 1.5)", r"\[0, 1\]"), ("1 + 2", "reference a field"), ("Mean($close, 5", "expected"),
    ("$close $open", "unexpected"), ("$close # 2", "cannot read"),
])
def test_malformed_expressions_say_what_is_wrong(fields, bad, msg):
    with pytest.raises(E.ExprError, match=msg):
        E.evaluate(bad, fields)


def test_canonical_form_ignores_spacing_and_parentheses_and_the_hash_follows_it():
    a, b = "Mean($close,20)/$close", " ( Mean( $close , 20 ) ) / ($close) "
    assert E.canonical(a) == E.canonical(b) == "Div(Mean($close, 20), $close)"
    assert E.expr_hash(a) == E.expr_hash(b) != E.expr_hash("Mean($close, 21)/$close")
    assert E.canonical("-$close") == "Mul(-1, $close)" and E.canonical("2-3*$close") == "Sub(2, Mul(3, $close))"
    assert E.canonical("$a > 1 & $b < 2") == "And(Gt($a, 1), Lt($b, 2))"   # & binds looser than comparisons
    assert E.fields_of(E.parse("Corr($close, Log($volume), 5)")) == {"close", "volume"}


def test_hand_computable_values():
    idx = pd.bdate_range("2024-01-01", periods=5)
    d = {"close": pd.DataFrame({"A": [1.0, 2, 3, 4, 10], "B": [5.0, 4, 3, 2, 1]}, index=idx)}
    assert E.evaluate("Slope($close, 3)", d).loc[idx[3]].tolist() == [1.0, -1.0]
    assert E.evaluate("Resi($close, 3)", d).loc[idx[4], "A"] == pytest.approx(10 - (17 / 3 + 3.5))  # window 3,4,10 on x=1,2,3: slope 3.5
    assert E.evaluate("Rsquare($close, 3)", d).loc[idx[3]].tolist() == pytest.approx([1.0, 1.0])
    assert E.evaluate("IdxMax($close, 3)", d).loc[idx[4], "A"] == 3.0
    assert E.evaluate("Rank($close, 3)", d).loc[idx[4]].tolist() == [1.0, pytest.approx(1 / 3)]
    assert E.evaluate("CSRank($close)", d).loc[idx[0]].tolist() == [0.5, 1.0]
    assert E.evaluate("$close / 0", d).isna().all().all()   # inf is not a value: strict mode makes it NaN


def test_fields_must_share_one_grid():
    idx = pd.bdate_range("2024-01-01", periods=3)
    d = {"close": pd.DataFrame({"A": [1.0, 2, 3]}, index=idx), "open": pd.DataFrame({"A": [1.0, 2]}, index=idx[:2])}
    with pytest.raises(E.ExprError, match="share"):
        E.evaluate("$close", d)


def test_vwap_is_omitted_and_says_why():
    names = [n for n, _ in A.feature_config()]
    assert len(names) == 157 and "VWAP0" not in names and "VWAP0" in A.OMITTED
    assert names[:3] == ["KMID", "KLEN", "KMID2"] and names[-1] == "VSUMD60"
