"""svi_fit and realized_vol_minute through the dispatcher, and fit_svi's kernel path (compute plan A7).

There is no committed real Cboe chain at 0b87dec; the SVI inputs are the suite's numerical
BSM/SSVI test chain (test_options_chain_strategy.make_chain) pushed through the real chain
pipeline (analyze_chain), plus a perturbed copy -- test input to a numerical kernel, never data."""

from __future__ import annotations

import math

import numpy as np
import pytest
from test_options_chain_strategy import make_chain

from ohcamel_quant import kernels
from ohcamel_quant.kernels import reference
from ohcamel_quant.options import svi as S
from ohcamel_quant.options.chain import analyze_chain


@pytest.fixture(scope="module")
def smiles():
    """(k, iv, T) per expiry, read exactly as options/surface.build_surface reads them."""
    ca = analyze_chain(make_chain())
    out = []
    for sid, s in ca.slices.iterrows():
        sm = ca.slice_quotes(str(sid))
        sm = sm[sm["use_smile"]]
        out.append((sm["k"].to_numpy(), sm["iv"].to_numpy(), float(s["T"])))
    return out


def test_reference_is_the_legacy_calibration(smiles):
    k, iv, T = smiles[2]
    w = iv * iv * T
    p, sse, con = S.calibrate(k, w, np.ones_like(k))
    r = reference.svi_fit(k, w, np.ones_like(k))
    assert (r.a, r.b, r.rho, r.m, r.sigma, r.sse, r.constrained_a) == (p.a, p.b, p.rho, p.m, p.sigma, sse, con)


def test_fit_svi_on_the_kernel_matches_and_keeps_the_butterfly_check(smiles):
    k, iv, T = smiles[3]
    legacy = S.fit_svi(k, iv, T)
    job = S.fit_svi(k, iv, T, use_kernels=True)
    for f in ("a", "b", "rho", "m", "sigma"):
        assert getattr(job.params, f) == pytest.approx(getattr(legacy.params, f), abs=1e-5)
    assert job.butterfly["arbitrage_free"] == legacy.butterfly["arbitrage_free"]
    assert job.butterfly["g_min"] == pytest.approx(legacy.butterfly["g_min"], abs=1e-6)
    assert job.to_dict()["engine"] == kernels.engine_of("svi_fit")
    assert "engine" not in legacy.to_dict()          # the synchronous payload is unchanged


def test_svi_rejects_bad_input():
    with pytest.raises(ValueError, match="5 quotes"):
        kernels.svi_fit([0.0, 0.1, 0.2, 0.3], [0.01] * 4, [1.0] * 4)
    with pytest.raises(ValueError, match="positive"):
        kernels.svi_fit([0.0, 0.1, 0.2, 0.3, 0.4], [0.01, 0.0, 0.01, 0.01, 0.01], [1.0] * 5)


def test_rv_hand_series():
    # derivation in native/kernels/src/rv.rs: RV1 = 2 ln(1.01)^2, RV2 = ln(1.1)^2, RV3 = NaN
    ts = np.array([500, 1000, 1060, 1120, 1999, 2000, 5000, 5060, 9000], dtype=np.int64)
    px = np.array([99.0, 100.0, 101.0, 100.0, 100.0, 250.0, 50.0, 55.0, 10.0])
    rv = kernels.realized_vol_minute(ts, px, [[1000, 2000], [5000, 6000], [9000, 10000]])
    assert rv[0] == pytest.approx(2 * math.log(1.01) ** 2, rel=1e-12)
    assert rv[1] == pytest.approx(math.log(1.1) ** 2, rel=1e-12)
    assert math.isnan(rv[2])


@pytest.mark.parametrize("ts,px,bounds,msg", [
    ([2, 1], [1.0, 1.0], [[0, 5]], "non-decreasing"),
    ([1, 2], [1.0, 0.0], [[0, 5]], "positive"),
    ([1, 2], [1.0, 1.0], [[0, 5], [4, 8]], "overlapping"),
])
def test_rv_rejects_bad_input(ts, px, bounds, msg):
    with pytest.raises(ValueError, match=msg):
        kernels.realized_vol_minute(np.array(ts, dtype=np.int64), px, bounds)
