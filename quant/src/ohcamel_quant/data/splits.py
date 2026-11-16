"""Recognise an unadjusted split and divide the bars before it.

The warehouse and the ingest keep the vendor's bytes. This runs on the way
out to a page, and again inside the fixture loader. A series whose adjusted
close already absorbed the split does not match, so a later vendor fix is
not divided a second time.
"""

from __future__ import annotations

import pandas as pd

# A one-session close-to-close jump is tested against these ratios.
SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1.5, 2 / 3)


def present(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Copy of ``df`` with prior bars divided through each unadjusted split.

    A session is a split when the close-to-close move is beyond 30%, the
    previous close over the open is within 3% of a standard ratio, and the
    adjusted close moved beyond 30% as well. Notes are one sentence per
    ex-date, oldest first. Idempotent on an already adjusted series.
    """
    out = df.copy()
    notes: list[str] = []
    prev_close = out["close"].shift(1)
    jump = out["close"] / prev_close - 1.0
    adj_jump = out["adj_close"] / out["adj_close"].shift(1) - 1.0
    overnight = prev_close / out["open"]
    days = list(out.index[(jump.abs() > 0.30).fillna(False)])
    for day in days:
        if abs(float(adj_jump.loc[day])) <= 0.30:
            continue
        ratio = float(overnight.loc[day])
        match = min(SPLIT_RATIOS, key=lambda r: abs(ratio / r - 1.0))
        if abs(ratio / match - 1.0) > 0.03:
            continue
        before = out.index < day
        for col in ("open", "high", "low", "close", "adj_close"):
            out.loc[before, col] = out.loc[before, col] / match
        out.loc[before, "volume"] = out.loc[before, "volume"] * match
        when = pd.Timestamp(day).date().isoformat()
        notes.append(f"split-adjusted {match:g}:1 before {when} (vendor adj_close did not absorb it)")
    return out, notes
