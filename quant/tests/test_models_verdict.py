"""Lane M, M1: the charter's gates applied literally (docs/CHARTER.md)."""

from __future__ import annotations

import pandas as pd
import pytest
from lane_m_harness import is_label

from ohcamel_quant.models.verdict import (
    REGIMES,
    Verdict,
    charter_verdict,
    insufficient,
    regime_table,
)


def _regimes(pos: list[bool | None]) -> pd.DataFrame:
    return pd.DataFrame({"regime": [r[0] for r in REGIMES], "positive": pos})


GOOD = dict(holdout_return=0.08, dsr=0.41, psr=0.83, boot_lo5=0.02, regimes=_regimes([None, True, True, True, False]),
            pbo=0.2, costs=pd.DataFrame({"cost_bps": [0.0, 5.0, 15.0, 30.0], "sharpe": [0.9, 0.8, 0.6, 0.3]}))


def test_every_gate_passing_is_pass_and_says_advisory():
    v = charter_verdict(**GOOD)
    assert v.value == "PASS" and "ADVISORY" in v.detail and is_label(v.detail)
    assert [g.name for g in v.gates] == ["holdout_positive", "dsr", "psr", "bootstrap_lower_5pct", "regimes_positive",
                                         "pbo", "cost_sweep"]


def test_one_failed_gate_is_fail_and_is_named():
    v = charter_verdict(**{**GOOD, "dsr": 0.12})
    assert v.value == "FAIL" and "DSR 0.12" in v.detail and "0.3" in v.detail and is_label(v.detail)


def test_dsr_exactly_at_the_threshold_passes():
    assert charter_verdict(**{**GOOD, "dsr": 0.30}).value == "PASS"  # charter: DSR >= 0.30


def test_regimes_need_three_positive_and_missing_ones_do_not_count():
    v = charter_verdict(**{**GOOD, "regimes": _regimes([None, True, True, None, None])})
    assert v.value == "FAIL" and "REGIMES_POSITIVE 2" in v.detail  # 2 < 3; 2008 and two others have no OOS data


def test_a_metric_that_cannot_be_computed_fails_its_gate():
    v = charter_verdict(**{**GOOD, "psr": float("nan")})
    assert v.value == "FAIL" and "PSR" in v.detail


def test_no_holdout_is_insufficient_data():
    assert charter_verdict(**{**GOOD, "holdout_return": None}).value == "INSUFFICIENT DATA"


def test_high_pbo_is_named_but_reported_not_gated():
    v = charter_verdict(**{**GOOD, "pbo": 0.62})
    assert v.value == "PASS" and "PBO 0.62 HIGH" in v.detail


def test_a_missing_cost_sweep_fails():
    v = charter_verdict(**{**GOOD, "costs": None})
    assert v.value == "FAIL" and "COST_SWEEP" in v.detail


def test_the_cost_sweep_gate_reads_the_table_not_a_flag():
    partial = pd.DataFrame({"cost_bps": [0.0, 5.0], "sharpe": [0.9, 0.8]})  # 15 and 30 bps never computed
    v = charter_verdict(**{**GOOD, "costs": partial})
    gate = {g.name: g for g in v.gates}["cost_sweep"]
    assert v.value == "FAIL" and gate.passed is False and gate.note == "MISSING 15, 30 BPS"
    assert gate.value == 2.0 and is_label(v.detail)


def test_a_cost_row_with_no_number_does_not_count():
    nan_row = pd.DataFrame({"cost_bps": [0.0, 5.0, 15.0, 30.0], "sharpe": [0.9, 0.8, 0.6, float("nan")]})
    assert {g.name: g for g in charter_verdict(**{**GOOD, "costs": nan_row}).gates}["cost_sweep"].passed is False


def test_a_full_cost_sweep_is_reported_not_gated_and_accepts_records():
    recs = [{"cost_bps": b, "sharpe": 0.5} for b in (30, 15, 5, 0)]  # the farm's cost_curve JSON shape, any order
    gate = {g.name: g for g in charter_verdict(**{**GOOD, "costs": recs}).gates}["cost_sweep"]
    assert gate.passed is None and gate.value == 4.0 and gate.note == ""


def test_regime_table_compounds_inside_each_window():
    r = pd.Series([0.01, 0.01, -0.5], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2023-06-01"]))
    t = regime_table(r)
    row = t.set_index("regime").loc["2020"]
    assert row["n"] == 2 and row["total_return"] == pytest.approx(1.01 ** 2 - 1)  # 0.0201 by hand
    assert bool(row["positive"]) is True
    assert t.set_index("regime").loc["2008"]["positive"] is None  # no observations: not positive, not negative


def test_verdict_rejects_unknown_values():
    with pytest.raises(ValueError):
        Verdict("MAYBE", "x")
    assert insufficient("no data").value == "INSUFFICIENT DATA"
