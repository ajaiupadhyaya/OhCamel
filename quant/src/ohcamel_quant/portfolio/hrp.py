"""Hierarchical allocation: HRP (Lopez de Prado 2016) and HERC (Raffinot 2018).

Hierarchical Risk Parity -- Lopez de Prado (2016), "Building diversified
portfolios that outperform out of sample", *Journal of Portfolio Management* 42(4):

1. **Tree clustering.** Correlation distance ``d_ij = sqrt(0.5 (1 - rho_ij))``
   (a proper metric). Following the paper, the linkage is run on the Euclidean
   distance between *columns* of ``D``: ``d~_ij = sqrt(sum_k (d_ki - d_kj)^2)``
   (``distance='paper'``); ``distance='direct'`` links on ``d_ij`` itself.
   Single linkage by default (the paper's choice); ward/average/complete optional.
2. **Quasi-diagonalisation.** Reorder assets by the dendrogram's leaf order so
   similar assets sit next to each other and the covariance is near block-diagonal.
3. **Recursive bisection.** Starting from the full ordered list, split each
   cluster into two halves ``L``, ``R``; with each half's inverse-variance
   portfolio variance ``V_L``, ``V_R``, scale ``w_L *= alpha``,
   ``w_R *= 1 - alpha`` where ``alpha = 1 - V_L / (V_L + V_R)``.

Hierarchical Equal Risk Contribution -- Raffinot (2018), "The hierarchical
equal risk contribution portfolio", SSRN 3237540: bisection follows the
dendrogram's actual splits (not list halves) down to ``k`` clusters; clusters
at each split receive risk in inverse proportion to their variance, and
within a terminal cluster assets get inverse-variance (naive risk parity) weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, leaves_list, linkage, to_tree
from scipy.spatial.distance import pdist, squareform

from .covariance import cov_to_corr

Linkage = Literal["single", "ward", "average", "complete"]


@dataclass
class Hierarchy:
    linkage: np.ndarray  # scipy linkage matrix (N-1) x 4
    order: list[int]  # quasi-diagonal leaf order (indices)
    labels: list[str]
    distance: np.ndarray  # correlation distance matrix d_ij

    def dendrogram(self) -> dict[str, Any]:
        """Plot-ready dendrogram (scipy ``icoord``/``dcoord`` segments) plus raw linkage."""
        d = dendrogram(self.linkage, labels=self.labels, no_plot=True)
        return {
            "linkage": self.linkage.tolist(), "order": [self.labels[i] for i in self.order],
            "icoord": d["icoord"], "dcoord": d["dcoord"], "ivl": d["ivl"],
        }


def correlation_distance(corr: np.ndarray) -> np.ndarray:
    """``d_ij = sqrt(0.5 (1 - rho_ij))`` in ``[0, 1]``."""
    return np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))


def cluster(cov: pd.DataFrame, method: Linkage = "single",
            distance: Literal["paper", "direct"] = "paper") -> Hierarchy:
    """Build the hierarchical tree and quasi-diagonal order from a covariance matrix."""
    labels = [str(c) for c in cov.columns]
    corr = cov_to_corr(cov.to_numpy(float))
    d = correlation_distance(corr)
    np.fill_diagonal(d, 0.0)
    if distance == "paper":
        condensed = pdist(d, metric="euclidean")
    elif distance == "direct":
        condensed = squareform(d, checks=False)
    else:
        raise ValueError("distance must be 'paper' or 'direct'")
    if method not in ("single", "ward", "average", "complete"):
        raise ValueError(f"unknown linkage {method!r}")
    z = linkage(condensed, method=method)
    return Hierarchy(z, [int(i) for i in leaves_list(z)], labels, d)


def _ivp(cov: np.ndarray) -> np.ndarray:
    iv = 1.0 / np.diag(cov)
    return iv / iv.sum()


def cluster_variance(cov: np.ndarray, items: list[int]) -> float:
    """Variance of the inverse-variance portfolio of the sub-covariance ``cov[items, items]``."""
    sub = cov[np.ix_(items, items)]
    w = _ivp(sub)
    return float(w @ sub @ w)


def recursive_bisection(cov: np.ndarray, order: list[int]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Lopez de Prado (2016) snippet 16.3 ``getRecBipart``; also returns each split for display."""
    w = np.ones(cov.shape[0])
    clusters = [list(order)]
    steps: list[dict[str, Any]] = []
    while clusters:
        nxt: list[list[int]] = []
        for c in clusters:
            if len(c) <= 1:
                continue
            half = len(c) // 2
            left, right = c[:half], c[half:]
            vl, vr = cluster_variance(cov, left), cluster_variance(cov, right)
            alpha = 1.0 - vl / (vl + vr)
            w[left] *= alpha
            w[right] *= 1.0 - alpha
            steps.append({"left": left, "right": right, "var_left": vl, "var_right": vr, "alpha": alpha})
            nxt += [left, right]
        clusters = nxt
    return w, steps


def hrp(cov: pd.DataFrame, method: Linkage = "single",
        distance: Literal["paper", "direct"] = "paper") -> tuple[np.ndarray, Hierarchy, list[dict[str, Any]]]:
    """Hierarchical Risk Parity weights (long-only, sum to one)."""
    h = cluster(cov, method, distance)
    s = cov.to_numpy(float)
    w, steps = recursive_bisection(s, h.order)
    named = [{**st, "left": [h.labels[i] for i in st["left"]], "right": [h.labels[i] for i in st["right"]]}
             for st in steps]
    return w / w.sum(), h, named


def _auto_k(z: np.ndarray, n: int) -> int:
    """Number of clusters at the largest gap between successive merge heights."""
    if n <= 2:
        return n
    heights = z[:, 2]
    gaps = np.diff(heights)
    i = int(np.argmax(gaps))  # cut between merge i and i+1
    return max(2, n - (i + 1))


def herc(cov: pd.DataFrame, method: Linkage = "ward", n_clusters: int | None = None,
         distance: Literal["paper", "direct"] = "paper") -> tuple[np.ndarray, Hierarchy, dict[str, Any]]:
    """Hierarchical Equal Risk Contribution (Raffinot 2018), variance as the risk measure.

    Top-down along the dendrogram until ``k`` clusters: at each node with children
    ``L``, ``R`` the node's weight is split ``alpha = V_R/(V_L+V_R)`` to ``L`` (risk
    parity between the two clusters, ``V`` = inverse-variance-portfolio variance);
    inside each terminal cluster, inverse-variance weights. ``n_clusters=None`` cuts
    the tree at the largest jump in merge height (a simple elbow rule standing in
    for Raffinot's gap statistic).
    """
    h = cluster(cov, method, distance)
    s = cov.to_numpy(float)
    n = s.shape[0]
    k = _auto_k(h.linkage, n) if n_clusters is None else int(n_clusters)
    k = max(1, min(k, n))
    root = to_tree(h.linkage)
    # nodes with id >= n are merges; the last (n - k) merges above the cut are split
    cut_ids = {n + i for i in range(n - 1) if i >= (n - 1) - (k - 1)}
    w = np.zeros(n)

    def leaves(node: Any) -> list[int]:
        return [int(i) for i in node.pre_order()]

    def walk(node: Any, weight: float) -> None:
        if node.is_leaf():
            w[node.id] = weight
            return
        if node.id not in cut_ids:  # terminal cluster -> inverse variance inside
            items = leaves(node)
            w[items] = weight * _ivp(s[np.ix_(items, items)])
            return
        li, ri = leaves(node.left), leaves(node.right)
        vl, vr = cluster_variance(s, li), cluster_variance(s, ri)
        a = vr / (vl + vr)
        walk(node.left, weight * a)
        walk(node.right, weight * (1 - a))

    walk(root, 1.0)
    return w / w.sum(), h, {"n_clusters": k}
