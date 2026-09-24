"""Hierarchical risk parity on a textbook matrix (hand-worked) and on real ETF returns."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.portfolio import covariance as cv
from ohcamel_quant.portfolio import hrp


def _textbook() -> pd.DataFrame:
    """Two clusters: {A, B} vol 10% corr 0.8; {C, D} vol 20% corr 0.8; cross corr 0.1.
    Listed in scrambled order A, C, B, D so quasi-diagonalisation has work to do."""
    vol = {"A": 0.1, "B": 0.1, "C": 0.2, "D": 0.2}
    cl = {"A": 0, "B": 0, "C": 1, "D": 1}
    names = ["A", "C", "B", "D"]
    m = np.array([[vol[i] * vol[j] * (1.0 if i == j else 0.8 if cl[i] == cl[j] else 0.1) for j in names]
                  for i in names])
    return pd.DataFrame(m, index=names, columns=names)


def test_hrp_textbook_hand_worked():
    cov = _textbook()
    w, h, steps = hrp.hrp(cov)
    ws = pd.Series(w, index=cov.columns)
    # quasi-diagonal order keeps each cluster contiguous
    order = h.dendrogram()["order"]
    assert {frozenset(order[:2]), frozenset(order[2:])} == {frozenset("AB"), frozenset("CD")}
    # hand recursion: IVP inside each half is 1/2, 1/2
    # V_AB = 0.25 (0.01 + 0.01 + 2 * 0.008) = 0.009 ; V_CD = 0.25 (0.04 + 0.04 + 2 * 0.032) = 0.036
    # alpha_AB = 1 - 0.009 / 0.045 = 0.8 -> A = B = 0.4, C = D = 0.1
    assert steps[0]["var_left"] + steps[0]["var_right"] == pytest.approx(0.045)
    assert ws["A"] == pytest.approx(0.4) and ws["B"] == pytest.approx(0.4)
    assert ws["C"] == pytest.approx(0.1) and ws["D"] == pytest.approx(0.1)
    assert ws[["C", "D"]].sum() < ws[["A", "B"]].sum()  # high-variance cluster gets less


def test_hrp_matches_independent_recursion(etf_returns):
    cov = cv.sample_cov(etf_returns.dropna()).cov
    w, h, _ = hrp.hrp(cov)
    s = cov.to_numpy()
    # independent re-implementation of Lopez de Prado (2016) snippet 16.3
    ref = pd.Series(1.0, index=h.order)
    items = [h.order]
    while items:
        items = [i[j:k] for i in items for j, k in ((0, len(i) // 2), (len(i) // 2, len(i))) if len(i) > 1]
        for a in range(0, len(items), 2):
            c0, c1 = items[a], items[a + 1]

            def cvar(c):
                sub = s[np.ix_(c, c)]
                iv = 1 / np.diag(sub)
                iv /= iv.sum()
                return iv @ sub @ iv

            v0, v1 = cvar(c0), cvar(c1)
            al = 1 - v0 / (v0 + v1)
            ref[c0] *= al
            ref[c1] *= 1 - al
    assert np.allclose(w, ref.sort_index().to_numpy())
    assert w.sum() == pytest.approx(1.0) and np.all(w > 0)


def test_hrp_real_etfs_structure(etf_returns):
    cov = cv.lw_constant_correlation(etf_returns.dropna()).cov
    w, h, _ = hrp.hrp(cov)
    ws = pd.Series(w, index=cov.columns)
    order = h.dendrogram()["order"]
    # treasuries cluster together and equities cluster together
    pos = {t: i for i, t in enumerate(order)}
    assert abs(pos["TLT"] - pos["IEF"]) == 1
    assert abs(pos["SPY"] - pos["QQQ"]) <= 2
    # the low-variance bond ETF gets the largest weight
    assert ws.idxmax() == "IEF"
    d = h.dendrogram()
    assert len(d["linkage"]) == 8 and len(d["icoord"]) == 8
    for method in ("ward", "average", "complete"):
        w2, _, _ = hrp.hrp(cov, method)
        assert w2.sum() == pytest.approx(1.0)


def test_correlation_distance_is_metric():
    corr = np.array([[1.0, 0.5, -1.0], [0.5, 1.0, -0.5], [-1.0, -0.5, 1.0]])
    d = hrp.correlation_distance(corr)
    assert np.allclose(np.diag(d), 0)
    assert d[0, 2] == pytest.approx(1.0)
    for i in range(3):
        for j in range(3):
            for k in range(3):
                assert d[i, j] <= d[i, k] + d[k, j] + 1e-12


def test_herc_textbook_and_real(etf_returns):
    w, _, info = hrp.herc(_textbook(), "single", n_clusters=2)
    ws = pd.Series(w, index=_textbook().columns)
    # two clusters: split V_CD/(V_AB+V_CD) = 0.8 to {A,B}; inverse variance inside -> equal
    assert np.allclose(ws[["A", "B", "C", "D"]].to_numpy(), [0.4, 0.4, 0.1, 0.1])
    cov = cv.lw_constant_correlation(etf_returns.dropna()).cov
    w2, _, info2 = hrp.herc(cov)
    assert w2.sum() == pytest.approx(1.0) and np.all(w2 > 0)
    assert 2 <= info2["n_clusters"] <= 9
