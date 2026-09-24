"""Cornish-Fisher validity flag and GARCH near-unit-root caveats."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest
from scipy import stats

from ohcamel_quant.risk import garch as gm
from ohcamel_quant.risk import var as vm


def _heavy_tailed_sample() -> np.ndarray:
    # quantiles of a t(3) scaled to 1% daily vol: excess kurtosis far outside the CF domain
    u = (np.arange(1, 2001) - 0.5) / 2000
    return 0.01 * stats.t.ppf(u, 3) / math.sqrt(3.0)


def test_cornish_fisher_outside_domain_reports_null_es():
    x = _heavy_tailed_sample()
    K = stats.kurtosis(x, fisher=True, bias=False)
    assert not vm.cornish_fisher_valid(0.0, K)
    est = vm.cornish_fisher(x, 0.99)
    assert est.valid is False and est.params["valid_domain"] is False
    assert math.isfinite(est.var) and math.isnan(est.es)
    d = est.to_dict(1e6)
    assert d["valid"] is False and d["es_usd"] is None and d["var_usd"] is not None


def test_cornish_fisher_inside_domain_is_valid():
    # t(10) quantiles: mild excess kurtosis (~1), inside the Maillard (2012) domain
    x = stats.t.ppf((np.arange(1, 1001) - 0.5) / 1000, 10) * 0.01
    est = vm.cornish_fisher(x, 0.99)
    assert est.valid is True and est.es >= est.var
    assert est.to_dict()["valid"] is True


def test_summary_rows_carry_valid_flag(client):
    j = client.post("/api/risk/summary", json={"holdings": [{"ticker": "SPY", "weight": 1.0}],
                                               "alphas": [0.99]}).json()
    rows = {e["model"]: e for e in j["estimates"]}
    assert all("valid" in e for e in j["estimates"])
    cf = rows["cornish_fisher"]
    if cf["valid"] is False:
        assert cf["es"] is None and cf["var"] is not None
        assert j["table"]["cornish_fisher"]["0.99"]["valid"] is False
    assert rows["historical"]["valid"] is True


@pytest.fixture(scope="module")
def spy_fit(etf_returns):
    return gm.fit_garch(etf_returns["SPY"], "garch")


def test_garch_near_unit_root_flag_and_note(spy_fit):
    f = spy_fit
    near = dataclasses.replace(f, alpha=0.08, gamma=0.0, beta=0.917)  # P = 0.997
    assert near.params_dict()["near_unit_root"] is True
    est = gm.garch_var_es(near, 0.99, 10)
    assert any("near unit root" in n and "GJR" in n for n in est.notes)
    far = dataclasses.replace(f, alpha=0.08, gamma=0.0, beta=0.85)
    assert far.params_dict()["near_unit_root"] is False
    assert not any("near unit root" in n for n in gm.garch_var_es(far, 0.99, 10).notes)


def test_garch_endpoint_exposes_flag(client):
    j = client.post("/api/risk/garch", json={"holdings": [{"ticker": "SPY", "weight": 1.0}]}).json()
    for kind in ("garch", "gjr"):
        p = j["models"][kind]["params"]
        assert p["near_unit_root"] == (p["persistence"] >= gm.NEAR_UNIT_ROOT)
        if p["near_unit_root"]:
            assert any("near unit root" in n for n in j["notes"])
