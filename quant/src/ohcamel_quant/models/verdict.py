"""Verdicts and the charter's gates (docs/CHARTER.md, "The gates"), applied literally.

A verdict leads every product. ``charter_verdict`` is the strategy battery:
every gated metric must pass; a metric that cannot be computed fails its gate
(never-pass is the conservative reading); no holdout at all is INSUFFICIENT
DATA; PBO and the cost sweep are reported (PBO named when high), not gated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..jobs.artifacts import VERDICTS

DSR_MIN = 0.30
PSR_MIN = 0.70
REGIMES_MIN = 3
PBO_HIGH = 0.5
COST_GRID_BPS = (0.0, 5.0, 15.0, 30.0)
REGIMES = (("2008", "2008-01-01", "2008-12-31"), ("2015-16", "2015-01-01", "2016-12-31"),
           ("2020", "2020-01-01", "2020-12-31"), ("2022", "2022-01-01", "2022-12-31"),
           ("2024", "2024-01-01", "2024-12-31"))
ADVISORY_NOTE = "advisory; never sized"


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


def charter_verdict(*, holdout_return: Any, dsr: Any, psr: Any, boot_lo5: Any, regimes: pd.DataFrame, pbo: Any,
                    costs_reported: bool, extra: Sequence[Gate] = ()) -> Verdict:
    hr = _num(holdout_return)
    if hr is None:
        return Verdict("INSUFFICIENT DATA", "no out-of-sample holdout returns")
    d, p, b, pb = _num(dsr), _num(psr), _num(boot_lo5), _num(pbo)
    flags = [None if x is None or pd.isna(x) else bool(x) for x in regimes["positive"]]  # parquet may return np.bool_
    pos = sum(1 for x in flags if x is True)
    avail = sum(1 for x in flags if x is not None)
    gates = [
        Gate("holdout_positive", hr, "> 0", hr > 0),
        Gate("dsr", d, f">= {DSR_MIN}", d is not None and d >= DSR_MIN),
        Gate("psr", p, f">= {PSR_MIN}", p is not None and p >= PSR_MIN),
        Gate("bootstrap_lower_5pct", b, "> 0", b is not None and b > 0),
        Gate("regimes_positive", float(pos), f">= {REGIMES_MIN} of {len(REGIMES)}", pos >= REGIMES_MIN,
             note=f"{avail} of {len(REGIMES)} regimes have out-of-sample data"),
        Gate("pbo", pb, "reported", None, note="high" if pb is not None and pb > PBO_HIGH else ""),
        Gate("cost_sweep", None, "0/5/15/30 bps reported", None if costs_reported else False,
             note="" if costs_reported else "missing"),
        *extra,
    ]
    pbo_txt = f"; PBO {pb:.2f} (high)" if pb is not None and pb > PBO_HIGH else ""
    failed = [g for g in gates if g.passed is False]
    if failed:
        return Verdict("FAIL", "failed: " + ", ".join(f"{g.name} {_fmt(g.value)} (needs {g.rule})" for g in failed)
                       + pbo_txt, tuple(gates))
    return Verdict("PASS", f"every charter gate passed; {ADVISORY_NOTE}{pbo_txt}", tuple(gates))


def descriptive(detail: str) -> Verdict:
    return Verdict("DESCRIPTIVE ONLY", detail)


def insufficient(detail: str) -> Verdict:
    return Verdict("INSUFFICIENT DATA", detail)
