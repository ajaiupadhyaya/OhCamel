"""Lane M, M1: the charter's gates applied literally (docs/CHARTER.md); completion task 2.5 (owner ruling R3):
PSR and DSR at 0.95, an excess gate (SPA p <= 0.10 against the universe's equal-weight buy-and-hold), one regime
table read by both batteries, and fewer than three regimes with out-of-sample data INSUFFICIENT DATA, not FAIL."""

from __future__ import annotations

import importlib.util

import pandas as pd
import pytest
from lane_m_harness import is_label

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.models import regimes as regime_file
from ohcamel_quant.models.verdict import (
    DSR_MIN,
    PSR_MIN,
    REGIMES,
    REGIMES_MIN,
    SPA_MAX_P,
    Verdict,
    charter_verdict,
    covered,
    insufficient,
    regime_table,
)


def _regimes(pos: list[bool | None]) -> pd.DataFrame:
    return pd.DataFrame({"regime": [r[0] for r in REGIMES], "positive": pos})


#: 2008, 2011 and 2015-16 without out-of-sample data; 2018Q4, 2020, 2022 positive, 2024 negative.
GOOD_REGIMES = _regimes([None, None, None, True, True, True, False])
GOOD = dict(holdout_return=0.08, dsr=0.97, psr=0.99, boot_lo5=0.02, regimes=GOOD_REGIMES, regimes_covered=4,
            spa_p=0.04, pbo=0.2,
            costs=pd.DataFrame({"cost_bps": [0.0, 5.0, 15.0, 30.0], "sharpe": [0.9, 0.8, 0.6, 0.3]}))


def test_the_gates_are_r3s():
    assert (PSR_MIN, DSR_MIN, SPA_MAX_P, REGIMES_MIN) == (0.95, 0.95, 0.10, 3)


def test_every_gate_passing_is_pass_and_says_advisory():
    v = charter_verdict(**GOOD)
    assert v.value == "PASS" and "ADVISORY" in v.detail and is_label(v.detail)
    assert [g.name for g in v.gates] == ["holdout_positive", "dsr", "psr", "excess", "bootstrap_lower_5pct",
                                         "regimes_positive", "pbo", "cost_sweep"]
    excess = {g.name: g for g in v.gates}["excess"]
    assert (excess.value, excess.rule, excess.passed) == (0.04, "SPA p <= 0.10 vs buy-and-hold", True)


def test_one_failed_gate_is_fail_and_is_named():
    v = charter_verdict(**{**GOOD, "dsr": 0.12})
    assert v.value == "FAIL" and "DSR 0.12" in v.detail and "0.95" in v.detail and is_label(v.detail)


def test_a_psr_of_0_90_fails():
    v = charter_verdict(**{**GOOD, "psr": 0.90})
    assert v.value == "FAIL" and "PSR 0.90 (NEEDS >= 0.95)" in v.detail and is_label(v.detail)


def test_a_dsr_of_0_50_fails():
    v = charter_verdict(**{**GOOD, "dsr": 0.50})
    assert v.value == "FAIL" and "DSR 0.50 (NEEDS >= 0.95)" in v.detail


def test_an_spa_p_of_0_20_fails_with_every_other_gate_passing():
    v = charter_verdict(**{**GOOD, "spa_p": 0.20})
    gates = {g.name: g for g in v.gates}
    assert [n for n, g in gates.items() if g.passed is False] == ["excess"]
    assert v.value == "FAIL" and "EXCESS 0.20 (NEEDS SPA P <= 0.10 VS BUY-AND-HOLD)" in v.detail and is_label(v.detail)


@pytest.mark.parametrize("spa_p", [None, float("nan"), "n/a"])
def test_an_excess_gate_with_no_spa_p_fails(spa_p):
    v = charter_verdict(**{**GOOD, "spa_p": spa_p})
    assert v.value == "FAIL" and {g.name: g for g in v.gates}["excess"].passed is False


def test_the_thresholds_are_inclusive():
    assert charter_verdict(**{**GOOD, "dsr": 0.95, "psr": 0.95, "spa_p": 0.10}).value == "PASS"
    assert charter_verdict(**{**GOOD, "spa_p": 0.1000001}).value == "FAIL"


def test_dsr_exactly_at_the_threshold_passes():
    assert charter_verdict(**{**GOOD, "dsr": 0.95}).value == "PASS"  # charter: DSR >= 0.95


def test_regimes_need_three_positive_and_missing_ones_do_not_count():
    reg = _regimes([None, None, None, True, True, False, None])   # three covered, two positive
    v = charter_verdict(**{**GOOD, "regimes": reg, "regimes_covered": 3})
    assert v.value == "FAIL" and "REGIMES_POSITIVE 2.00 (NEEDS >= 3 OF 7)" in v.detail


def test_a_series_covering_two_regimes_is_insufficient_data_not_fail():
    reg = _regimes([None, None, None, None, True, True, None])     # 2020 and 2022 only, both positive
    v = charter_verdict(**{**GOOD, "regimes": reg, "regimes_covered": 2})
    assert v.value == "INSUFFICIENT DATA" and v.detail == "2 OF 7 REGIMES HAVE OUT-OF-SAMPLE DATA (NEEDS 3)"
    assert is_label(v.detail)
    assert {g.name: g for g in v.gates}["regimes_positive"].note == "2 of 7 regimes have out-of-sample data"


def test_too_few_regimes_is_insufficient_data_whatever_else_fails():
    reg = _regimes([None, None, None, None, None, False, None])
    v = charter_verdict(**{**GOOD, "regimes": reg, "regimes_covered": 1, "dsr": 0.1, "spa_p": 0.9})
    assert v.value == "INSUFFICIENT DATA"


def test_regimes_covered_must_agree_with_the_table():
    with pytest.raises(ValueError, match="regimes_covered 3"):
        charter_verdict(**{**GOOD, "regimes_covered": 3})          # the table has four regimes with data


def test_covered_counts_the_regimes_with_out_of_sample_data():
    assert covered(GOOD_REGIMES) == 4
    assert covered(regime_table(pd.Series(dtype=float, index=pd.DatetimeIndex([])))) == 0


def test_one_regime_table_read_from_the_one_file():
    """Spec, "Gates (R3)": one table, in one file. The verdict reads research/config/regimes.yaml, whose path the
    loader resolves itself (so the research suite can load it standalone) and which is REPO_ROOT's."""
    assert regime_file.REGIMES_PATH == REPO_ROOT / "research" / "config" / "regimes.yaml"
    assert regime_file.REGIMES_REL == "research/config/regimes.yaml"
    assert REGIMES == regime_file.load() == (
        ("2008", "2008-01-01", "2008-12-31"), ("2011", "2011-01-01", "2011-12-31"),
        ("2015-16", "2015-01-01", "2016-12-31"), ("2018Q4", "2018-10-01", "2018-12-31"),
        ("2020", "2020-01-01", "2020-12-31"), ("2022", "2022-01-01", "2022-12-31"),
        ("2024", "2024-01-01", "2024-12-31"))


def test_both_packages_return_the_same_regime_tuple():
    """The research battery's loader, loaded by its path (it imports nothing of its package), reads the same table."""
    path = REPO_ROOT / "research" / "src" / "ohcamel_research" / "battery" / "regimes.py"
    spec = importlib.util.spec_from_file_location("_research_regimes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.REGIMES_PATH == regime_file.REGIMES_PATH
    assert mod.load() == regime_file.load() == REGIMES


@pytest.mark.parametrize("doc, match", [
    ({"regimes": []}, "empty"),
    ({}, "not a regime table"),
    ({"regimes": [{"name": "a", "start": "2020-01-01", "end": "2020-12-31"},
                  {"name": "a", "start": "2021-01-01", "end": "2021-12-31"}]}, "named twice"),
    ({"regimes": [{"name": "a", "start": "2020-12-31", "end": "2020-01-01"}]}, "ends"),
    ({"regimes": [{"name": "a", "start": "2020-01-01", "end": "2020-12-31"},
                  {"name": "b", "start": "2020-06-01", "end": "2021-12-31"}]}, "on or before"),
    ({"regimes": [{"name": "a", "start": "2020-13-01", "end": "2020-12-31"}]}, "ISO date"),
])
def test_a_malformed_regime_table_is_refused(doc, match):
    with pytest.raises(ValueError, match=match):
        regime_file.parse(doc, "t")


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
    assert list(t["regime"]) == [r[0] for r in REGIMES]
    row = t.set_index("regime").loc["2020"]
    assert row["n"] == 2 and row["total_return"] == pytest.approx(1.01 ** 2 - 1)  # 0.0201 by hand
    assert bool(row["positive"]) is True
    assert t.set_index("regime").loc["2008"]["positive"] is None  # no observations: not positive, not negative
    assert covered(t) == 1                                          # 2023 is in no regime (the ruling's list)


def test_verdict_rejects_unknown_values():
    with pytest.raises(ValueError):
        Verdict("MAYBE", "x")
    assert insufficient("no data").value == "INSUFFICIENT DATA"
