"""qlib's Alpha158 feature set, as expressions in ``factors.expr``.

Ported from Microsoft qlib (MIT, (c) Microsoft Corporation), ``qlib/contrib/data/loader.py``,
``Alpha158DL.get_feature_config`` at v0.9.7, with its default config: kbar, ``price`` (windows [0], fields
OPEN/HIGH/LOW/VWAP) and ``rolling`` (windows 5/10/20/30/60, every operator).

**157, not 158.** ``VWAP0`` needs a VWAP field, and the committed bars do not carry one. Rather than inventing a
VWAP from O/H/L/C (the charter forbids substituting a made-up number for missing data), the feature is
omitted, and ``OMITTED`` says why. ``test_alpha158.py`` checks the other 157 against qlib's own output on the
same bars.

Every feature is normalized by the current close or volume (scale-free), as in qlib. These are FEATURES, not
signals: none of them is a hypothesis, and nothing here is evaluated against returns. Testing one is
``factors.workbench``'s job, which logs it as a trial.
"""

from __future__ import annotations

import pandas as pd

from .expr import evaluate, parse

WINDOWS = (5, 10, 20, 30, 60)
OMITTED = {"VWAP0": "needs a VWAP field; the committed daily bars carry O/H/L/C/V only, and none is invented"}


def feature_config(windows: tuple[int, ...] = WINDOWS) -> list[tuple[str, str]]:
    """(name, expression) in qlib's order."""
    out: list[tuple[str, str]] = [
        ("KMID", "($close-$open)/$open"),
        ("KLEN", "($high-$low)/$open"),
        ("KMID2", "($close-$open)/($high-$low+1e-12)"),
        ("KUP", "($high-Greater($open, $close))/$open"),
        ("KUP2", "($high-Greater($open, $close))/($high-$low+1e-12)"),
        ("KLOW", "(Less($open, $close)-$low)/$open"),
        ("KLOW2", "(Less($open, $close)-$low)/($high-$low+1e-12)"),
        ("KSFT", "(2*$close-$high-$low)/$open"),
        ("KSFT2", "(2*$close-$high-$low)/($high-$low+1e-12)"),
    ]
    out += [(f"{f.upper()}0", f"${f}/$close") for f in ("open", "high", "low")]
    w = windows
    sumpos = "Sum(Greater({x}-Ref({x}, 1), 0), {d})"
    sumneg = "Sum(Greater(Ref({x}, 1)-{x}, 0), {d})"
    sumabs = "(Sum(Abs({x}-Ref({x}, 1)), {d})+1e-12)"
    rolling = [
        ("ROC", "Ref($close, {d})/$close"),
        ("MA", "Mean($close, {d})/$close"),
        ("STD", "Std($close, {d})/$close"),
        ("BETA", "Slope($close, {d})/$close"),
        ("RSQR", "Rsquare($close, {d})"),
        ("RESI", "Resi($close, {d})/$close"),
        ("MAX", "Max($high, {d})/$close"),
        ("MIN", "Min($low, {d})/$close"),
        ("QTLU", "Quantile($close, {d}, 0.8)/$close"),
        ("QTLD", "Quantile($close, {d}, 0.2)/$close"),
        ("RANK", "Rank($close, {d})"),
        ("RSV", "($close-Min($low, {d}))/(Max($high, {d})-Min($low, {d})+1e-12)"),
        ("IMAX", "IdxMax($high, {d})/{d}"),
        ("IMIN", "IdxMin($low, {d})/{d}"),
        ("IMXD", "(IdxMax($high, {d})-IdxMin($low, {d}))/{d}"),
        ("CORR", "Corr($close, Log($volume+1), {d})"),
        ("CORD", "Corr($close/Ref($close,1), Log($volume/Ref($volume, 1)+1), {d})"),
        ("CNTP", "Mean($close>Ref($close, 1), {d})"),
        ("CNTN", "Mean($close<Ref($close, 1), {d})"),
        ("CNTD", "Mean($close>Ref($close, 1), {d})-Mean($close<Ref($close, 1), {d})"),
        ("SUMP", sumpos.format(x="$close", d="{d}") + "/" + sumabs.format(x="$close", d="{d}")),
        ("SUMN", sumneg.format(x="$close", d="{d}") + "/" + sumabs.format(x="$close", d="{d}")),
        ("SUMD", "(" + sumpos.format(x="$close", d="{d}") + "-" + sumneg.format(x="$close", d="{d}") + ")/"
         + sumabs.format(x="$close", d="{d}")),
        ("VMA", "Mean($volume, {d})/($volume+1e-12)"),
        ("VSTD", "Std($volume, {d})/($volume+1e-12)"),
        ("WVMA", "Std(Abs($close/Ref($close, 1)-1)*$volume, {d})/"
                 "(Mean(Abs($close/Ref($close, 1)-1)*$volume, {d})+1e-12)"),
        ("VSUMP", sumpos.format(x="$volume", d="{d}") + "/" + sumabs.format(x="$volume", d="{d}")),
        ("VSUMN", sumneg.format(x="$volume", d="{d}") + "/" + sumabs.format(x="$volume", d="{d}")),
        ("VSUMD", "(" + sumpos.format(x="$volume", d="{d}") + "-" + sumneg.format(x="$volume", d="{d}") + ")/"
         + sumabs.format(x="$volume", d="{d}")),
    ]
    for name, tpl in rolling:
        out += [(f"{name}{d}", tpl.format(d=d)) for d in w]
    for _, e in out:
        parse(e)                                            # every expression is valid in the strict language
    return out


def adjusted_fields(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """$open/$high/$low/$close on the adjusted basis (factor = close_adj / close, one per session), as qlib's
    stored prices are. ``bars``: ticker -> frame with open, high, low, close, close_adj, volume."""
    idx = sorted(set().union(*(b.index for b in bars.values())))
    def col(c: str) -> pd.DataFrame:
        return pd.DataFrame({t: b[c] for t, b in bars.items()}).reindex(idx)
    k = col("close_adj") / col("close")
    return {"open": col("open") * k, "high": col("high") * k, "low": col("low") * k, "close": col("close_adj"),
            "volume": col("volume")}


def alpha158(fields: dict[str, pd.DataFrame], *, compat: bool = False,
             windows: tuple[int, ...] = WINDOWS) -> pd.DataFrame:
    """Long frame indexed (date, ticker), one column per feature, in qlib's order (VWAP0 omitted)."""
    cols = {name: evaluate(e, fields, compat=compat).stack(future_stack=True) for name, e in feature_config(windows)}
    out = pd.DataFrame(cols)
    out.index.names = ["date", "ticker"]
    return out
