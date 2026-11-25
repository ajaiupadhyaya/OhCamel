"""Completion task 4.5: SSVI with the power-law phi (Gatheral & Jacquier 2014), fitted under the conditions that
make it free of calendar and butterfly arbitrage, and checked on the actual arbitrage measures -- total variance
non-decreasing in T at every k on the grid, and Durrleman's g >= 0 on [-K_MAX, K_MAX] by the refined scan
(svi._g_min) -- not only on the parameter inequalities."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant.options import ssvi, svi

K_GRID = np.linspace(-svi.K_MAX, svi.K_MAX, 1201)


def _synthetic(p: ssvi.SSVIParams, n_k: int = 41):
    """Noiseless quotes of a known SSVI surface (CONSTRUCTED; parameter recovery only, nothing reported)."""
    out = []
    for i, T in enumerate(p.T):
        k = np.linspace(-0.6, 0.4, n_k) * np.sqrt(T / 0.25) ** 0.5
        w = ssvi.total_variance(p, k, T)
        out.append((float(T), k, np.sqrt(w / T), np.ones_like(k) * (1.0 + 0.1 * i)))
    return out


TRUE = ssvi.SSVIParams(rho=-0.62, eta=1.05, gamma=0.38,
                       theta=np.array([0.0021, 0.0042, 0.0098, 0.0185, 0.034, 0.066]),
                       T=np.array([0.05, 0.1, 0.25, 0.5, 1.0, 2.0]))


@pytest.mark.parametrize("kw, match", [
    ({"rho": 0.05, "eta": 2.1 / 1.05}, "eta"),        # eta (1 + |rho|) = 2.1
    ({"rho": -0.4, "eta": 2.1 / 1.4}, "eta"),         # the same, on the negative side
    ({"gamma": 0.6}, "gamma"),                        # outside (0, 1/2]
    ({"gamma": 0.0}, "gamma"),
    ({"rho": 1.0}, "rho"),
    ({"theta": np.array([0.01, 0.009, 0.02])}, "non-decreasing"),
    ({"theta": np.array([0.0, 0.01, 0.02])}, "positive"),
    ({"T": np.array([0.1, 0.1, 0.3])}, "increasing"),
])
def test_the_conditions_are_refused_when_violated(kw, match):
    base = {"rho": -0.5, "eta": 1.0, "gamma": 0.4, "theta": np.array([0.01, 0.015, 0.02]),
            "T": np.array([0.1, 0.2, 0.3])}
    with pytest.raises(ValueError, match=match):
        ssvi.SSVIParams(**{**base, **kw})


def test_the_boundary_itself_is_allowed():
    p = ssvi.SSVIParams(rho=-0.25, eta=2.0 / 1.25, gamma=0.5, theta=np.array([0.01, 0.01]), T=np.array([0.1, 0.2]))
    assert p.eta * (1 + abs(p.rho)) == pytest.approx(2.0)


def test_each_slice_is_the_raw_svi_of_gatheral_jacquier_lemma_3_2():
    """The slice at a fitted T, rewritten as raw SVI, is the same total variance (so the per-slice butterfly
    check, svi._g_min, applies to it unchanged)."""
    k = np.linspace(-2, 2, 101)
    for T in TRUE.T:
        assert ssvi.slice_svi(TRUE, T).w(k) == pytest.approx(ssvi.total_variance(TRUE, k, T), rel=1e-13, abs=1e-15)


def test_total_variance_is_monotone_between_and_beyond_the_fitted_maturities():
    T = np.concatenate([[0.01, 0.03], np.linspace(0.05, 2.0, 60), [2.5, 4.0]])
    W = np.vstack([ssvi.total_variance(TRUE, K_GRID, t) for t in T])
    assert np.all(np.diff(W, axis=0) >= -1e-15)
    assert ssvi.total_variance(TRUE, np.array([0.0]), 0.5)[0] == pytest.approx(0.0185)   # theta is ATM total variance


def test_recovers_known_parameters_from_a_noiseless_synthetic_surface():
    f = ssvi.fit(_synthetic(TRUE))
    p = f.params
    assert abs(p.rho - TRUE.rho) < 1e-4 and abs(p.eta - TRUE.eta) < 1e-4 and abs(p.gamma - TRUE.gamma) < 1e-4
    assert np.max(np.abs(p.theta - TRUE.theta)) < 1e-4
    assert np.array_equal(p.T, TRUE.T)
    assert f.rmse_vol_pts < 1e-4


def test_recovers_a_surface_whose_eta_sits_on_the_bound():
    """eta (1 + |rho|) = 2 exactly and gamma = 1/2: the fit reaches the boundary instead of stopping short."""
    edge = ssvi.SSVIParams(rho=-0.5, eta=2.0 / 1.5, gamma=0.5, theta=TRUE.theta, T=TRUE.T)
    p = ssvi.fit(_synthetic(edge)).params
    assert abs(p.rho - edge.rho) < 1e-4 and abs(p.eta - edge.eta) < 1e-4 and abs(p.gamma - 0.5) < 1e-4
    assert p.eta * (1 + abs(p.rho)) <= 2.0


def test_fit_needs_quotes_and_distinct_maturities():
    with pytest.raises(ValueError):
        ssvi.fit([])
    s = _synthetic(TRUE)
    with pytest.raises(ValueError, match="maturit"):
        ssvi.fit([s[0], s[0]])


def test_spy_fixture_has_no_calendar_or_butterfly_arbitrage_at_every_maturity(spy_2026_11_15):
    """The real SPY chain (tests/data, 2026-11-13 quotes): every slice the per-slice SVI fits, fitted jointly.
    Calendar: w(k, T) non-decreasing in T at every k of [-3, 3] (1201 points), across the fitted maturities and
    a fine grid between them. Butterfly: g >= 0 on [-3, 3] by the refined scan at every one of those T."""
    _, vs, inputs = spy_2026_11_15
    slices = [(T, k, iv, wt) for k, iv, T, wt in inputs.values()]
    f = ssvi.fit(slices)
    p = f.params
    assert len(p.T) == len(vs.fits) and p.eta * (1 + abs(p.rho)) <= 2.0 and 0 < p.gamma <= 0.5
    assert np.all(np.diff(p.theta) >= 0)
    T = np.unique(np.concatenate([p.T, np.linspace(p.T[0], p.T[-1], 400)]))
    W = np.vstack([ssvi.total_variance(p, K_GRID, t) for t in T])
    assert np.all(np.diff(W, axis=0) >= 0), "calendar arbitrage"
    cal = svi.calendar_check([(t, ssvi.slice_svi(p, t)) for t in p.T], K_GRID)
    assert not any(c["violation"] for c in cal)
    for t in T:
        g_min, k_at, w_ok = svi._g_min(ssvi.slice_svi(p, t))
        assert w_ok and g_min >= 0.0, f"g < 0 at T={t:.5f}, k={k_at:.4f}: {g_min:.3g}"
    # a usable fit, not a degenerate one. Measured 3.45 vol points pooled over 31 expiries (per-slice SVI: 0.48), with
    # gamma at its bound 1/2: three parameters for the whole surface cannot follow every monthly's wings
    assert f.rmse_vol_pts < 4.0 and f.n == sum(len(s[1]) for s in slices)
    assert len(f.slice_rmse_vol_pts) == len(p.T)
