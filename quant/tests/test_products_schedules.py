"""Lane M: every product kind is registered, scheduled, aged and consistent (jobs/kinds.py, schedules.yaml)."""

from __future__ import annotations

from ohcamel_quant.jobs.kinds import REGISTRY
from ohcamel_quant.jobs.schedules import load_schedules

# kind -> (mem_class, heavy). Each Lane M task appends its kinds here.
LANE_M_KINDS = {
    "risk.mc_atlas": ("M", True),
    "risk.mc_intraday": ("S", False),
    "vol.forecast_league": ("M", True),
    "cov.league": ("M", True),
    "farm.sweep": ("L", True),
}


def test_lane_m_kinds_are_registered_private_and_scheduled_consistently():
    s = load_schedules()
    for kind, (mem, heavy) in LANE_M_KINDS.items():
        spec = REGISTRY[kind]
        assert (spec.mem_class, spec.heavy, spec.public) == (mem, heavy, False), kind
        entries = [e for e in s.entries if e.kind == kind]
        assert entries, f"{kind} has no schedule"
        assert all((e.mem_class, e.heavy) == (mem, heavy) for e in entries), kind
        assert kind in s.max_age_s, f"{kind} has no max_age"
        if heavy:
            assert all(e.priority == 2 for e in entries)
