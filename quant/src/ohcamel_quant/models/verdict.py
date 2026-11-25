"""Verdicts and the charter's gates (docs/CHARTER.md, "The gates"), applied literally.

A verdict leads every product. ``charter_verdict`` is the strategy battery:
every gated metric must pass; a metric that cannot be computed fails its gate
(never-pass is the conservative reading); no holdout at all is INSUFFICIENT
DATA, and so is an out-of-sample record with data in fewer than ``REGIMES_MIN``
regimes; PBO and the cost sweep are reported (PBO named when high), not gated.
The cost sweep is read from the cost table itself: every 0/5/15/30 bps row must
be there with at least one finite number, or the gate fails and names the gaps.

Completion task 2.5 (owner ruling R3; spec, "Gates (R3)"): PSR and DSR at 0.95;
the excess gate, Hansen's SPA p-value against the universe's equal-weight
buy-and-hold at most 0.10 (Sharpe against zero passed long-or-flat SPY in a
bull market); and one regime table, ``research/config/regimes.yaml``, read here
through ``models/regimes.py`` and by the research battery through its own
loader. ``tests/test_products_methodology.py`` holds docs/CHARTER.md's gates
table to these constants.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..jobs.artifacts import VERDICTS
from . import regimes as _regime_file

DSR_MIN = 0.95
PSR_MIN = 0.95
SPA_MAX_P = 0.10
REGIMES_MIN = 3
PBO_HIGH = 0.5
COST_GRID_BPS = (0.0, 5.0, 15.0, 30.0)
#: ``((name, first day, last day), ...)`` from the one regime file (task 2.5): never a copy held here.
REGIMES = _regime_file.load()
EXCESS_RULE = f"SPA p <= {SPA_MAX_P:.2f} vs buy-and-hold"
ADVISORY_NOTE = "ADVISORY · NEVER SIZED"


@dataclass(frozen=True)
class Gate:
    name: str
    value: float | None
    rule: str
    passed: bool | None          # None = reported, not gated
    note: str = ""


@dataclass(frozen=True)
class Verdict:
    value: str
    detail: str
    gates: tuple[Gate, ...] = ()

    def __post_init__(self) -> None:
        if self.value not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}; got {self.value!r}")

    def table(self) -> pd.DataFrame:
        cols = ["gate", "value", "rule", "passed", "note"]
        return pd.DataFrame([{"gate": g.name, "value": g.value, "rule": g.rule, "passed": g.passed, "note": g.note}
                             for g in self.gates], columns=cols)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"


def regime_table(r: pd.Series) -> pd.DataFrame:
    """Compounded return inside each charter regime window; ``positive`` None when the window is empty."""
    rows = []
    for name, a, b in REGIMES:
        s = r.loc[(r.index >= pd.Timestamp(a)) & (r.index <= pd.Timestamp(b))].dropna()
        tot = float((1.0 + s).prod() - 1.0) if len(s) else None
        rows.append({"regime": name, "start": a, "end": b, "n": int(len(s)), "total_return": tot,
                     "positive": None if tot is None else bool(tot > 0)})
    return pd.DataFrame(rows).astype({"positive": object})


def _failed_label(failed: Sequence[Gate]) -> str:
    return "FAILED " + ", ".join(f"{g.name.upper()} {_fmt(g.value).upper()} (NEEDS {g.rule.upper()})" for g in failed)


def covered(regimes: pd.DataFrame) -> int:
    """How many regimes of a :func:`regime_table` frame hold out-of-sample data (``positive`` not missing)."""
    return sum(1 for x in regimes["positive"] if x is not None and not pd.isna(x))


def cost_sweep_gate(costs: pd.DataFrame | Sequence[Mapping[str, Any]] | None) -> Gate:
    """Reported, not gated, when the table holds a row for each of 0/5/15/30 bps with a finite metric."""
    df = pd.DataFrame(list(costs)) if costs is not None and not isinstance(costs, pd.DataFrame) else costs
    have: set[float] = set()
    if df is not None and not df.empty and "cost_bps" in df.columns:
        metrics = df.drop(columns=["cost_bps"]).apply(pd.to_numeric, errors="coerce")
        live = np.isfinite(metrics.to_numpy(dtype=float)).any(axis=1)
        have = {float(b) for b, ok in zip(pd.to_numeric(df["cost_bps"], errors="coerce"), live, strict=True)
                if ok and _num(b) is not None}
    missing = [b for b in COST_GRID_BPS if not any(math.isclose(b, h) for h in have)]
    note = "" if not missing else "MISSING " + ", ".join(f"{b:g}" for b in missing) + " BPS"
    return Gate("cost_sweep", float(len(COST_GRID_BPS) - len(missing)), "0/5/15/30 bps reported",
                None if not missing else False, note=note)


def charter_verdict(*, holdout_return: Any, dsr: Any, psr: Any, boot_lo5: Any, regimes: pd.DataFrame, pbo: Any,
                    costs: pd.DataFrame | Sequence[Mapping[str, Any]] | None, spa_p: Any, regimes_covered: int,
                    extra: Sequence[Gate] = ()) -> Verdict:
    """``spa_p``: Hansen's SPA consistent p-value of the strategy's trials against the universe's equal-weight
    buy-and-hold, filled and costed as the strategy is. ``regimes_covered``: how many of ``regimes`` hold
    out-of-sample data (:func:`covered`); it must agree with the table, and fewer than ``REGIMES_MIN`` is INSUFFICIENT
    DATA whatever the other gates say -- the record cannot be judged across regimes at all."""
    hr = _num(holdout_return)
    if hr is None:
        return Verdict("INSUFFICIENT DATA", "NO OOS HOLDOUT RETURNS")
    d, p, b, pb, sp = _num(dsr), _num(psr), _num(boot_lo5), _num(pbo), _num(spa_p)
    flags = [None if x is None or pd.isna(x) else bool(x) for x in regimes["positive"]]  # parquet may return np.bool_
    pos = sum(1 for x in flags if x is True)
    avail = sum(1 for x in flags if x is not None)
    if int(regimes_covered) != avail:
        raise ValueError(f"regimes_covered {regimes_covered} disagrees with the regime table ({avail} with data)")
    gates = [
        Gate("holdout_positive", hr, "> 0", hr > 0),
        Gate("dsr", d, f">= {DSR_MIN:.2f}", d is not None and d >= DSR_MIN),
        Gate("psr", p, f">= {PSR_MIN:.2f}", p is not None and p >= PSR_MIN),
        Gate("excess", sp, EXCESS_RULE, sp is not None and sp <= SPA_MAX_P,
             note="Hansen's SPA, consistent p-value, against the universe's equal-weight buy-and-hold"),
        Gate("bootstrap_lower_5pct", b, "> 0", b is not None and b > 0),
        Gate("regimes_positive", float(pos), f">= {REGIMES_MIN} of {len(REGIMES)}", pos >= REGIMES_MIN,
             note=f"{avail} of {len(REGIMES)} regimes have out-of-sample data"),
        Gate("pbo", pb, "reported", None, note="high" if pb is not None and pb > PBO_HIGH else ""),
        cost_sweep_gate(costs),
        *extra,
    ]
    pbo_txt = f" · PBO {pb:.2f} HIGH" if pb is not None and pb > PBO_HIGH else ""
    failed = [g for g in gates if g.passed is False]
    if avail < REGIMES_MIN:
        # the coverage first, then every gate that failed outright, in the FAIL label's form (fix round 1, I-1): the
        # regime gate is the coverage itself, not a measured failure
        others = [g for g in failed if g.name != "regimes_positive"]
        return Verdict("INSUFFICIENT DATA", f"{avail} OF {len(REGIMES)} REGIMES HAVE OUT-OF-SAMPLE DATA "
                                            f"(NEEDS {REGIMES_MIN})" + (" · " + _failed_label(others) if others else "")
                       + pbo_txt, tuple(gates))
    if failed:
        return Verdict("FAIL", _failed_label(failed) + pbo_txt, tuple(gates))
    return Verdict("PASS", f"EVERY CHARTER GATE PASSED · {ADVISORY_NOTE}{pbo_txt}", tuple(gates))


def descriptive(detail: str) -> Verdict:
    return Verdict("DESCRIPTIVE ONLY", detail)


def insufficient(detail: str) -> Verdict:
    return Verdict("INSUFFICIENT DATA", detail)
